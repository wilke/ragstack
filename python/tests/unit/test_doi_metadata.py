"""Unit tests for DOI-based metadata enrichment.

The HTTP layer is always mocked (``httpx.MockTransport``) — enrichment is the
only part of ingest that reaches the public internet, and a test suite that hit
Crossref would be both impolite and non-hermetic. Every network behaviour we
care about (404, timeout, rate-limit, malformed JSON) is reproduced locally.
"""
from __future__ import annotations

import json

import httpx
import pytest

from ragstack.ingestion.doi_metadata import (
    ENRICHED_FROM_KEY,
    DoiCache,
    DoiEnricher,
    DoiMetadataResolver,
    Resolution,
    default_user_agent,
    document_doi,
    map_crossref,
    map_datacite,
    merge_enrichment,
    normalize_doi,
    scan_text_for_doi,
)
from ragstack.models import Document

DOI = "10.3390/antibiotics14050475"

CROSSREF_MESSAGE = {
    "DOI": "10.3390/Antibiotics14050475",
    "title": ["High Prevalence of Cefiderocol Resistance"],
    "container-title": ["Antibiotics"],
    "author": [
        {"given": "Ada", "family": "Lovelace"},
        {"given": "Alan", "family": "Turing"},
        {"name": "The Consortium"},
    ],
    "issued": {"date-parts": [[2025, 5, 8]]},
    "publisher": "MDPI AG",
    "type": "journal-article",
    "URL": "https://doi.org/10.3390/antibiotics14050475",
}

DATACITE_DATA = {
    "attributes": {
        "doi": DOI,
        "titles": [{"title": "A Deposited Dataset"}],
        "creators": [
            {"givenName": "Grace", "familyName": "Hopper"},
            {"name": "Anon Group"},
        ],
        "container": {"title": "Zenodo"},
        "publicationYear": 2024,
        "publisher": "Zenodo",
        "types": {"resourceTypeGeneral": "Dataset"},
        "url": "https://zenodo.org/record/1",
    }
}


def _resolver(handler, **kwargs) -> tuple[DoiMetadataResolver, list[httpx.Request]]:
    """A resolver over a MockTransport, plus the list of requests it made."""
    seen: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(_record))
    kwargs.setdefault("cache", DoiCache())
    kwargs.setdefault("datacite_fallback", False)
    return DoiMetadataResolver(client, **kwargs), seen


def _crossref_ok(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"status": "ok", "message": CROSSREF_MESSAGE})


# --------------------------------------------------------------------------- #
# DOI extraction
# --------------------------------------------------------------------------- #

def test_normalize_doi_strips_wrappers_and_punctuation():
    assert normalize_doi("https://doi.org/10.1128/JVI.02415-06") == "10.1128/jvi.02415-06"
    assert normalize_doi("doi: 10.1128/jvi.02415-06.") == "10.1128/jvi.02415-06"
    assert normalize_doi("<10.1128/jvi.02415-06>") == "10.1128/jvi.02415-06"
    # Balanced parens are part of the DOI; an unbalanced trailing one is prose.
    assert normalize_doi("10.1016/S0140-6736(98)01085-X") == "10.1016/s0140-6736(98)01085-x"
    assert normalize_doi("10.1016/S0140-6736(98)01085-X)") == "10.1016/s0140-6736(98)01085-x"


def test_normalize_doi_rejects_non_dois():
    for bad in ("", "not a doi", "10.5/x", "https://example.org/paper", "10.1234"):
        assert normalize_doi(bad) == ""


def test_document_doi_from_existing_metadata_wins():
    doc = Document(
        id="d1",
        content=f"body mentioning doi {DOI} in the text",
        metadata={"doi": "10.1234/from-metadata"},
        source="/corpus/paper.pdf",
    )
    assert document_doi(doc) == ("10.1234/from-metadata", "metadata")


def test_document_doi_extracted_from_text():
    doc = Document(
        id="d1",
        content=f"Antibiotics 2025, 14, 475. https://doi.org/{DOI}\n\nAbstract...",
        metadata={"filename": "PMC12108422.pdf"},
        source="/corpus/PMC12108422.pdf",
    )
    doi, source = document_doi(doc)
    assert doi == DOI
    assert source == "text"


def test_scan_text_finds_doi_past_the_jsonl_4000_char_window():
    """Regression for a real g1-corpus PDF whose front-page DOI sat at char 4017
    — just past ``enrich.derive_doi``'s text window."""
    text = "x" * 4010 + f" https://doi.org/{DOI} " + "y" * 100
    assert scan_text_for_doi(text) == DOI


def test_scan_text_prefers_the_repeated_running_header_doi():
    """The article's own DOI repeats in the page headers; a reference DOI that
    slips into the window appears once."""
    text = (
        f"Downloaded from https://doi.org/{DOI}\n"
        "... body citing 10.1371/journal.pmed.1001921. and 10.1089/fpd.2015.2110. ...\n"
        f"J. Bacteriol. https://doi.org/{DOI}\n"
    )
    assert scan_text_for_doi(text) == DOI


def test_scan_text_is_bounded_so_reference_lists_are_out_of_reach():
    text = "z" * 30_000 + f" https://doi.org/{DOI}"
    assert scan_text_for_doi(text) == ""


def test_document_doi_absent_returns_empty():
    doc = Document(
        id="d1",
        content="A paper with no identifier anywhere in it.",
        metadata={"filename": "PMC12108422.pdf"},
        source="/corpus/PMC12108422.pdf",
    )
    assert document_doi(doc) == ("", "")


def test_document_doi_rejects_malformed_metadata_doi():
    """A junk ``doi`` value must not become a request we know will 404."""
    doc = Document(id="d1", content="no doi here", metadata={"doi": "n/a"})
    assert document_doi(doc) == ("", "")


# --------------------------------------------------------------------------- #
# Response mapping
# --------------------------------------------------------------------------- #

def test_map_crossref_to_normalized_fields():
    mapped = map_crossref(CROSSREF_MESSAGE)
    assert mapped == {
        "title": "High Prevalence of Cefiderocol Resistance",
        "authors": ["Ada Lovelace", "Alan Turing", "The Consortium"],
        "journal": "Antibiotics",
        "year": 2025,
        "doi": DOI,  # normalized (lowercased) even though Crossref returned mixed case
        "publisher": "MDPI AG",
        "type": "journal-article",
        "url": "https://doi.org/10.3390/antibiotics14050475",
    }


def test_map_crossref_falls_back_for_year_and_omits_empties():
    mapped = map_crossref(
        {"DOI": DOI, "title": [], "created": {"date-parts": [[2019, 1, 1]]}}
    )
    assert mapped == {"year": 2019, "doi": DOI}
    assert "title" not in mapped and "authors" not in mapped


def test_map_crossref_normalizes_titles_for_display():
    """All three fixed here were observed on real Crossref records: JATS inline
    markup, HTML entities, and pretty-printed line wrapping."""
    mapped = map_crossref(
        {
            "container-title": ["European Journal of Clinical Microbiology &amp; ID"],
            "title": [
                "<i>In Vitro</i>\n            Activity against\n"
                "            <i>Enterobacterales</i>\n            Collected in India"
            ],
        }
    )
    assert mapped["journal"] == "European Journal of Clinical Microbiology & ID"
    assert mapped["title"] == "In Vitro Activity against Enterobacterales Collected in India"


def test_map_crossref_keeps_a_literal_less_than_in_a_title():
    """Tag stripping is whitelisted, so real maths/chemistry survives."""
    mapped = map_crossref({"title": ["Growth at pH &lt; 7 and 5 &lt; n &lt; 9"]})
    assert mapped["title"] == "Growth at pH < 7 and 5 < n < 9"


def test_map_crossref_tolerates_garbage():
    assert map_crossref({}) == {}
    assert map_crossref({"title": 7, "author": "nope", "issued": "nope"}) == {}


def test_map_datacite_to_normalized_fields():
    assert map_datacite(DATACITE_DATA) == {
        "title": "A Deposited Dataset",
        "authors": ["Grace Hopper", "Anon Group"],
        "journal": "Zenodo",
        "year": 2024,
        "doi": DOI,
        "publisher": "Zenodo",
        "type": "Dataset",
        "url": "https://zenodo.org/record/1",
    }


# --------------------------------------------------------------------------- #
# Precedence
# --------------------------------------------------------------------------- #

def test_merge_fills_only_missing_fields():
    metadata = {"filename": "p.pdf", "title": "", "authors": []}
    filled = merge_enrichment(metadata, map_crossref(CROSSREF_MESSAGE), "crossref")
    assert set(filled) == {
        "title", "authors", "journal", "year", "doi", "publisher",
        "publication_type", "url",
    }
    assert metadata["title"] == "High Prevalence of Cefiderocol Resistance"
    assert metadata["journal"] == "Antibiotics"
    assert metadata["publication_type"] == "journal-article"  # not `doc_type`
    assert metadata[ENRICHED_FROM_KEY] == "crossref"
    assert metadata["filename"] == "p.pdf"  # untouched


def test_merge_never_clobbers_existing_title():
    """The precedence rule: existing explicit metadata wins, always."""
    metadata = {"title": "Locally extracted title", "authors": ["Local Author"]}
    filled = merge_enrichment(metadata, map_crossref(CROSSREF_MESSAGE), "crossref")
    assert metadata["title"] == "Locally extracted title"
    assert metadata["authors"] == ["Local Author"]
    assert "title" not in filled and "authors" not in filled
    # Gaps are still filled around the retained values.
    assert metadata["journal"] == "Antibiotics"


def test_merge_treats_whitespace_only_as_missing():
    metadata = {"title": "   "}
    merge_enrichment(metadata, {"title": "Real Title"}, "crossref")
    assert metadata["title"] == "Real Title"


def test_merge_with_nothing_resolved_adds_no_provenance_stamp():
    metadata = {"title": "Local"}
    assert merge_enrichment(metadata, {"title": "Remote"}, "crossref") == []
    assert ENRICHED_FROM_KEY not in metadata


# --------------------------------------------------------------------------- #
# Resolver: happy path, failures, politeness
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_resolve_maps_crossref_response():
    resolver, seen = _resolver(_crossref_ok, mailto="ops@example.org")
    resolution = await resolver.resolve(DOI)
    assert isinstance(resolution, Resolution)
    assert resolution.service == "crossref"
    assert resolution.fields["journal"] == "Antibiotics"
    # Politeness: descriptive UA carrying the contact, plus the polite-pool param.
    assert "mailto:ops@example.org" in seen[0].headers["User-Agent"]
    assert "RAGStack/" in seen[0].headers["User-Agent"]
    assert seen[0].url.params["mailto"] == "ops@example.org"
    assert str(seen[0].url).startswith("https://api.crossref.org/works/")


@pytest.mark.asyncio
async def test_resolve_404_returns_none():
    resolver, seen = _resolver(lambda _r: httpx.Response(404, text="not found"))
    assert await resolver.resolve(DOI) is None
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_resolve_timeout_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    resolver, _ = _resolver(handler)
    assert await resolver.resolve(DOI) is None


@pytest.mark.asyncio
async def test_resolve_malformed_json_returns_none():
    resolver, _ = _resolver(
        lambda _r: httpx.Response(200, content=b"<html>nope", headers={
            "Content-Type": "application/json"
        })
    )
    assert await resolver.resolve(DOI) is None


@pytest.mark.asyncio
async def test_resolve_server_error_returns_none():
    resolver, _ = _resolver(lambda _r: httpx.Response(500, text="boom"))
    assert await resolver.resolve(DOI) is None


@pytest.mark.asyncio
async def test_resolve_honours_retry_after_then_succeeds():
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return _crossref_ok(_request)

    resolver, _ = _resolver(handler)
    resolution = await resolver.resolve(DOI)
    assert resolution is not None
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_resolve_gives_up_on_long_retry_after():
    """A Retry-After beyond the cap must not park the ingest — skip instead."""
    resolver, seen = _resolver(
        lambda _r: httpx.Response(503, headers={"Retry-After": "86400"})
    )
    assert await resolver.resolve(DOI) is None
    assert len(seen) == 1  # no sleep, no retry


@pytest.mark.asyncio
async def test_datacite_fallback_only_on_definitive_crossref_miss():
    def handler(request: httpx.Request) -> httpx.Response:
        if "crossref" in request.url.host:
            return httpx.Response(404)
        return httpx.Response(200, json={"data": DATACITE_DATA})

    resolver, seen = _resolver(handler, datacite_fallback=True)
    resolution = await resolver.resolve(DOI)
    assert resolution is not None
    assert resolution.service == "datacite"
    assert resolution.fields["title"] == "A Deposited Dataset"
    assert [r.url.host for r in seen] == ["api.crossref.org", "api.datacite.org"]


@pytest.mark.asyncio
async def test_datacite_not_tried_on_transient_crossref_failure():
    """A 5xx is not evidence the DOI is absent; doubling load during an outage
    is exactly the impolite behaviour to avoid."""
    resolver, seen = _resolver(lambda _r: httpx.Response(500), datacite_fallback=True)
    assert await resolver.resolve(DOI) is None
    assert [r.url.host for r in seen] == ["api.crossref.org"]


@pytest.mark.asyncio
async def test_resolve_many_deduplicates():
    resolver, seen = _resolver(_crossref_ok)
    out = await resolver.resolve_many([DOI, DOI.upper(), f"https://doi.org/{DOI}"])
    assert list(out) == [DOI]
    assert len(seen) == 1


def test_default_user_agent_without_contact_still_identifies():
    ua = default_user_agent()
    assert ua.startswith("RAGStack/")
    assert "mailto:" not in ua


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_cache_hit_avoids_second_request(tmp_path):
    resolver, seen = _resolver(_crossref_ok, cache=DoiCache(tmp_path))
    first = await resolver.resolve(DOI)
    second = await resolver.resolve(DOI)
    assert first is not None and second is not None
    assert first.fields == second.fields
    assert len(seen) == 1

    # A fresh resolver (new process, same cache dir) must also stay offline.
    fresh, fresh_seen = _resolver(_crossref_ok, cache=DoiCache(tmp_path))
    reloaded = await fresh.resolve(DOI)
    assert reloaded is not None
    assert reloaded.fields["title"] == first.fields["title"]
    assert reloaded.service == "crossref"
    assert fresh_seen == []


@pytest.mark.asyncio
async def test_negative_result_is_cached_but_transient_failure_is_not(tmp_path):
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404)

    resolver, _ = _resolver(handler, cache=DoiCache(tmp_path))
    assert await resolver.resolve(DOI) is None
    assert await resolver.resolve(DOI) is None
    assert calls["n"] == 1  # the 404 was cached

    # A 500 must NOT be cached — a network blip can't poison the corpus.
    calls["n"] = 0
    flaky, _ = _resolver(lambda _r: httpx.Response(500), cache=DoiCache(tmp_path))
    assert await flaky.resolve("10.9999/other") is None
    assert await flaky.resolve("10.9999/other") is None
    assert calls["n"] == 0


def test_corrupt_cache_file_degrades_to_miss(tmp_path):
    cache = DoiCache(tmp_path)
    cache.put(DOI, Resolution({"title": "T"}, "crossref"))
    # Corrupt the file on disk and use a fresh cache (no memory layer).
    next(tmp_path.glob("*.json")).write_text("{not json", encoding="utf-8")
    assert DoiCache(tmp_path).get(DOI) is DoiCache.MISS


def test_cache_write_to_unwritable_dir_is_not_fatal(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("i am a file, not a directory", encoding="utf-8")
    cache = DoiCache(blocker)
    cache.put(DOI, Resolution({"title": "T"}, "crossref"))  # must not raise
    # The in-memory layer still works even though the disk write failed.
    hit = cache.get(DOI)
    assert isinstance(hit, Resolution)


def test_stale_mapping_version_reads_as_a_miss(tmp_path):
    """Entries store the *mapped* record, so a mapping change must not serve
    records this build would have mapped differently."""
    cache = DoiCache(tmp_path)
    cache.put(DOI, Resolution({"title": "T"}, "crossref"))
    entry = next(tmp_path.glob("*.json"))
    payload = json.loads(entry.read_text())
    assert payload["version"] == DoiCache.VERSION
    payload["version"] = DoiCache.VERSION - 1
    entry.write_text(json.dumps(payload), encoding="utf-8")
    assert DoiCache(tmp_path).get(DOI) is DoiCache.MISS


def test_cache_key_is_a_safe_single_filename(tmp_path):
    cache = DoiCache(tmp_path)
    cache.put("10.1128/jvi.02415-06", Resolution({"title": "T"}, "crossref"))
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    assert "/" not in files[0].name
    assert json.loads(files[0].read_text())["doi"] == "10.1128/jvi.02415-06"


# --------------------------------------------------------------------------- #
# DoiEnricher over Documents
# --------------------------------------------------------------------------- #

def _pdf_doc(**metadata) -> Document:
    """A PDF-shaped document: the demo's failure case — filename only, DOI in
    the first page's text."""
    return Document(
        id="doc-1",
        content=f"Antibiotics 2025, 14, 475\nhttps://doi.org/{DOI}\n\nAbstract ...",
        metadata={"filename": "PMC12108422.pdf", "pages": 16, **metadata},
        source="/corpus/PMC12108422.pdf",
    )


@pytest.mark.asyncio
async def test_enricher_fills_pdf_metadata_gaps():
    resolver, _ = _resolver(_crossref_ok)
    doc = _pdf_doc()
    changed = await DoiEnricher(resolver).enrich_documents([doc])
    assert changed == 1
    assert doc.metadata["title"] == "High Prevalence of Cefiderocol Resistance"
    assert doc.metadata["authors"] == ["Ada Lovelace", "Alan Turing", "The Consortium"]
    assert doc.metadata["journal"] == "Antibiotics"
    assert doc.metadata["year"] == 2025
    assert doc.metadata["doi"] == DOI
    assert doc.metadata["doi_source"] == "text"
    assert doc.metadata[ENRICHED_FROM_KEY] == "crossref"
    # Loader-supplied keys keep their meaning.
    assert doc.metadata["filename"] == "PMC12108422.pdf"
    assert doc.metadata["pages"] == 16


@pytest.mark.asyncio
async def test_enricher_does_not_clobber_an_existing_title():
    resolver, _ = _resolver(_crossref_ok)
    doc = _pdf_doc(title="Title from the PDF outline")
    await DoiEnricher(resolver).enrich_documents([doc])
    assert doc.metadata["title"] == "Title from the PDF outline"
    assert doc.metadata["journal"] == "Antibiotics"  # gap still filled


@pytest.mark.asyncio
async def test_enricher_records_doi_even_when_lookup_fails():
    """A 404 still leaves the document better off: the DOI itself is recorded,
    which the UI's label fallback can use."""
    resolver, _ = _resolver(lambda _r: httpx.Response(404))
    doc = _pdf_doc()
    assert await DoiEnricher(resolver).enrich_documents([doc]) == 0
    assert doc.metadata["doi"] == DOI
    assert "title" not in doc.metadata
    assert ENRICHED_FROM_KEY not in doc.metadata


@pytest.mark.asyncio
async def test_enricher_leaves_metadata_untouched_without_a_doi():
    resolver, seen = _resolver(_crossref_ok)
    doc = Document(id="d", content="no identifier here", metadata={"filename": "a.pdf"})
    assert await DoiEnricher(resolver).enrich_documents([doc]) == 0
    assert doc.metadata == {"filename": "a.pdf"}
    assert seen == []


@pytest.mark.asyncio
async def test_enricher_swallows_a_broken_resolver():
    """Enrichment is best-effort: nothing it does may escape into the ingest."""

    class _Exploding:
        async def resolve_many(self, dois):
            raise RuntimeError("resolver is on fire")

    doc = _pdf_doc()
    changed = await DoiEnricher(_Exploding()).enrich_documents([doc])  # type: ignore[arg-type]
    assert changed == 0
    assert doc.metadata["filename"] == "PMC12108422.pdf"


@pytest.mark.asyncio
async def test_enricher_shares_one_lookup_across_documents_with_the_same_doi():
    resolver, seen = _resolver(_crossref_ok)
    docs = [_pdf_doc(), _pdf_doc()]
    docs[1].id = "doc-2"
    assert await DoiEnricher(resolver).enrich_documents(docs) == 2
    assert len(seen) == 1
