"""Unit tests for DOI-based metadata enrichment.

The HTTP layer is always mocked (``httpx.MockTransport``) — enrichment is the
only part of ingest that reaches the public internet, and a test suite that hit
Crossref would be both impolite and non-hermetic. Every network behaviour we
care about (404, timeout, rate-limit, malformed JSON) is reproduced locally.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from ragstack.ingestion.doi_metadata import (
    BREAKER_THRESHOLD,
    DOI_ARG_DEST,
    ENRICHED_FROM_KEY,
    METADATA_SOURCE_KEY,
    DoiCache,
    DoiEnricher,
    DoiMetadataResolver,
    Resolution,
    add_doi_enrichment_args,
    default_user_agent,
    document_doi,
    enricher_from_args,
    map_crossref,
    map_datacite,
    map_idconv,
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
    # Off unless a test is about the ID Converter, for the same reason
    # datacite_fallback is: a test asserting "exactly one request" should not
    # have to know which optional legs happen to be on by default.
    kwargs.setdefault("pubmed_ids", False)
    return DoiMetadataResolver(client, **kwargs), seen


def _crossref_ok(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"status": "ok", "message": CROSSREF_MESSAGE})


# The ID Converter's real response shape, copied from a live call made while
# building this. Two details are load-bearing and both are reproduced exactly:
# ``pmid`` is a JSON *integer*, and ``doi`` echoes the publisher's original CASE
# while ``requested-id`` echoes the (lowercased) id we actually asked for.
IDCONV_RECORD = {
    "doi": "10.3390/Antibiotics14050475",
    "pmcid": "PMC12108422",
    "pmid": 40426541,
    "requested-id": DOI,
}


def _idconv_ok(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"status": "ok", "records": [IDCONV_RECORD]})


def _routed(**by_host):
    """Dispatch a mock request to a handler chosen by a fragment of its host."""

    def handler(request: httpx.Request) -> httpx.Response:
        for fragment, responder in by_host.items():
            if fragment in request.url.host:
                return responder(request)
        raise AssertionError(f"unexpected host {request.url.host}")

    return handler


def _hosts(seen: list[httpx.Request]) -> list[str]:
    return [r.url.host for r in seen]


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


# --------------------------------------------------------------------------- #
# NCBI ID Converter (pmid / pmcid) — #596
# --------------------------------------------------------------------------- #

def test_map_idconv_keys_on_requested_id_not_the_returned_doi():
    """The record's ``doi`` is the DOI as NCBI stores it, in the publisher's
    original case; ``requested-id`` is what we sent. Keying on ``doi`` drops
    every record from a publisher that uppercases its suffix — which is most of
    ASM — and the drop is silent."""
    mapped = map_idconv({"records": [IDCONV_RECORD]})
    assert set(mapped) == {DOI}
    assert mapped[DOI] == {"pmid": "40426541", "pmcid": "PMC12108422"}


def test_map_idconv_stringifies_the_integer_pmid():
    """``ingestion.jats`` keeps pmid/pmcid as strings on purpose. A corpus that
    mixes 40426541 with "40426541" gives Elasticsearch two field types for one
    name across collections."""
    assert map_idconv({"records": [IDCONV_RECORD]})[DOI]["pmid"] == "40426541"


def test_map_idconv_omits_records_that_failed_to_resolve():
    payload = {
        "records": [
            {"doi": "10.9999/nope", "requested-id": "10.9999/nope",
             "status": "error", "errmsg": "Identifier not found in PMC"},
            IDCONV_RECORD,
        ]
    }
    assert set(map_idconv(payload)) == {DOI}


def test_map_idconv_tolerates_garbage():
    assert map_idconv({}) == {}
    assert map_idconv({"records": "nope"}) == {}
    assert map_idconv({"records": [None, {"pmid": 1}]}) == {}


@pytest.mark.asyncio
async def test_resolve_merges_crossref_and_pubmed_ids():
    resolver, seen = _resolver(
        _routed(crossref=_crossref_ok, ncbi=_idconv_ok), pubmed_ids=True
    )
    resolution = await resolver.resolve(DOI)
    assert resolution is not None
    assert resolution.fields["title"] == "High Prevalence of Cefiderocol Resistance"
    assert resolution.fields["pmid"] == "40426541"
    assert resolution.fields["pmcid"] == "PMC12108422"
    assert resolution.service == "crossref+idconv"
    assert sorted(_hosts(seen)) == ["api.crossref.org", "pmc.ncbi.nlm.nih.gov"]


@pytest.mark.asyncio
async def test_idconv_uses_the_current_endpoint_not_the_redirecting_one():
    """The widely-copied ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/ URL 301s here,
    and httpx does not follow redirects by default — asking the old one returns
    an empty body and no ids at all."""
    resolver, seen = _resolver(
        _routed(crossref=_crossref_ok, ncbi=_idconv_ok), pubmed_ids=True,
        mailto="ops@example.org",
    )
    await resolver.resolve(DOI)
    idconv = next(r for r in seen if "ncbi" in r.url.host)
    assert str(idconv.url).startswith(
        "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/"
    )
    assert idconv.url.params["ids"] == DOI
    assert idconv.url.params["tool"] == "ragstack"
    assert idconv.url.params["email"] == "ops@example.org"


@pytest.mark.asyncio
async def test_pubmed_ids_resolve_even_when_crossref_has_no_record():
    """A DOI Crossref 404s can still be in PMC, and a pmid/pmcid alone is worth
    having — so this is a Resolution, not a miss."""
    resolver, _ = _resolver(
        _routed(crossref=lambda _r: httpx.Response(404), ncbi=_idconv_ok),
        pubmed_ids=True,
    )
    resolution = await resolver.resolve(DOI)
    assert resolution is not None
    assert resolution.service == "idconv"
    assert resolution.fields == {"pmid": "40426541", "pmcid": "PMC12108422"}
    assert "title" not in resolution.fields


@pytest.mark.asyncio
async def test_idconv_is_one_batched_request_for_the_whole_shard():
    """Per DISTINCT DOI, and for the ID Converter not even that: one request for
    all of them. The regression this guards is 382 chunks -> 382 lookups."""
    dois = [DOI, "10.1128/jvi.02415-06", "10.1371/journal.pntd.0010774"]

    def idconv(request: httpx.Request) -> httpx.Response:
        asked = request.url.params["ids"].split(",")
        return httpx.Response(200, json={"records": [
            {"requested-id": d, "pmid": 1000 + i, "pmcid": f"PMC{i}"}
            for i, d in enumerate(asked)
        ]})

    resolver, seen = _resolver(
        _routed(crossref=_crossref_ok, ncbi=idconv), pubmed_ids=True
    )
    out = await resolver.resolve_many(dois * 50)
    assert set(out) == set(dois)
    assert _hosts(seen).count("pmc.ncbi.nlm.nih.gov") == 1
    assert _hosts(seen).count("api.crossref.org") == 3
    assert out[dois[1]].fields["pmid"] == "1001"


@pytest.mark.asyncio
async def test_a_failed_idconv_call_still_leaves_the_crossref_record():
    resolver, _ = _resolver(
        _routed(crossref=_crossref_ok, ncbi=lambda _r: httpx.Response(500)),
        pubmed_ids=True,
    )
    resolution = await resolver.resolve(DOI)
    assert resolution is not None
    assert resolution.service == "crossref"
    assert resolution.fields["title"]
    assert "pmid" not in resolution.fields


@pytest.mark.asyncio
async def test_a_failed_idconv_call_blocks_the_negative_cache(tmp_path):
    """Crossref 404 + an ID Converter that merely fell over is not evidence the
    DOI has no metadata. Caching that negative would outlive the outage."""
    resolver, seen = _resolver(
        _routed(crossref=lambda _r: httpx.Response(404),
                ncbi=lambda _r: httpx.Response(503)),
        pubmed_ids=True, cache=DoiCache(tmp_path),
    )
    assert await resolver.resolve(DOI) is None
    assert list(tmp_path.glob("*.json")) == []


@pytest.mark.asyncio
async def test_authoritative_misses_everywhere_are_negative_cached(tmp_path):
    """Both services answered and neither has it — that IS cacheable."""
    resolver, _ = _resolver(
        _routed(crossref=lambda _r: httpx.Response(404),
                ncbi=lambda _r: httpx.Response(200, json={"records": [
                    {"requested-id": DOI, "status": "error"}]})),
        pubmed_ids=True, cache=DoiCache(tmp_path),
    )
    assert await resolver.resolve(DOI) is None
    assert len(list(tmp_path.glob("*.json"))) == 1


@pytest.mark.asyncio
async def test_resolve_many_makes_no_requests_for_already_cached_dois(tmp_path):
    """The whole point of the cache: the same paper ingested into a second
    collection resolves zero times, ID Converter batch included."""
    handler = _routed(crossref=_crossref_ok, ncbi=_idconv_ok)
    first, first_seen = _resolver(handler, pubmed_ids=True, cache=DoiCache(tmp_path))
    assert await first.resolve_many([DOI])
    assert len(first_seen) == 2

    second, second_seen = _resolver(handler, pubmed_ids=True, cache=DoiCache(tmp_path))
    out = await second.resolve_many([DOI, DOI.upper()])
    assert out[DOI].fields["pmid"] == "40426541"
    assert second_seen == []


@pytest.mark.asyncio
async def test_pubmed_ids_can_be_turned_off_independently():
    resolver, seen = _resolver(_routed(crossref=_crossref_ok), pubmed_ids=False)
    resolution = await resolver.resolve(DOI)
    assert resolution is not None and resolution.service == "crossref"
    assert _hosts(seen) == ["api.crossref.org"]


# --------------------------------------------------------------------------- #
# Circuit breaker — what makes "on by default" affordable
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_breaker_stops_asking_once_the_network_is_unreachable():
    """An air-gapped host must not pay a timeout per document. After
    BREAKER_THRESHOLD consecutive transport failures the resolver stops making
    requests for the rest of its life — and still never fails anything."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    resolver, seen = _resolver(handler)
    dois = [f"10.1234/paper-{i}" for i in range(40)]
    assert await resolver.resolve_many(dois) == {}
    assert len(seen) == BREAKER_THRESHOLD


@pytest.mark.asyncio
async def test_breaker_resets_when_a_service_answers_at_all():
    """A 404 is a working network. Only a failure to reach the service counts."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] % 2:
            raise httpx.ConnectError("flaky", request=request)
        return httpx.Response(404)

    resolver, seen = _resolver(handler)
    dois = [f"10.1234/paper-{i}" for i in range(20)]
    assert await resolver.resolve_many(dois) == {}
    assert len(seen) == 20  # never tripped


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #

def test_merge_stamps_both_provenance_keys():
    """``doi_enriched_from`` is this module's own stamp; ``metadata_source`` is
    the key scripts/backfill_collection_metadata.py writes and the tenant-admin
    runbook tells operators to grep for. A collection born enriched and one
    repaired after the fact must answer the same question the same way."""
    metadata: dict = {}
    merge_enrichment(metadata, {"title": "T", "pmid": "1"}, "crossref+idconv")
    assert metadata[ENRICHED_FROM_KEY] == "crossref+idconv"
    assert metadata[METADATA_SOURCE_KEY] == "crossref+idconv"


def test_merge_does_not_overwrite_extracted_pubmed_ids():
    metadata = {"pmid": "from-the-jats", "pmcid": ""}
    filled = merge_enrichment(metadata, {"pmid": "40426541", "pmcid": "PMC1"}, "idconv")
    assert metadata["pmid"] == "from-the-jats"
    assert metadata["pmcid"] == "PMC1"
    assert filled == ["pmcid"]


# --------------------------------------------------------------------------- #
# End to end over Documents — the #596 acceptance case
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_enricher_fills_the_whole_scholarly_set_on_an_uploaded_pdf():
    """The measured failure: doi 382/382, title/authors/journal/pmid/pmcid
    0/382. One document, one Crossref call, one ID Converter call, all five
    fields present."""
    resolver, seen = _resolver(
        _routed(crossref=_crossref_ok, ncbi=_idconv_ok), pubmed_ids=True
    )
    doc = _pdf_doc()
    assert await DoiEnricher(resolver).enrich_documents([doc]) == 1
    assert doc.metadata["title"] == "High Prevalence of Cefiderocol Resistance"
    assert doc.metadata["authors"]
    assert doc.metadata["journal"] == "Antibiotics"
    assert doc.metadata["pmid"] == "40426541"
    assert doc.metadata["pmcid"] == "PMC12108422"
    assert doc.metadata[METADATA_SOURCE_KEY] == "crossref+idconv"
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_enricher_resolves_per_distinct_doi_not_per_document():
    """Two papers spread over many documents cost two Crossref lookups and one
    batched ID Converter call — never one per chunk."""
    def idconv(request: httpx.Request) -> httpx.Response:
        asked = request.url.params["ids"].split(",")
        return httpx.Response(200, json={"records": [
            {"requested-id": d, "pmid": 7} for d in asked]})

    resolver, seen = _resolver(
        _routed(crossref=_crossref_ok, ncbi=idconv), pubmed_ids=True
    )
    docs = []
    for i in range(20):
        doc = _pdf_doc(doi=DOI if i % 2 else "10.1128/jvi.02415-06")
        doc.id = f"doc-{i}"
        docs.append(doc)
    assert await DoiEnricher(resolver).enrich_documents(docs) == 20
    assert _hosts(seen).count("api.crossref.org") == 2
    assert _hosts(seen).count("pmc.ncbi.nlm.nih.gov") == 1


@pytest.mark.asyncio
async def test_every_service_down_leaves_the_documents_ingestible():
    """The non-negotiable one: a total outage costs metadata, never a job."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    resolver, _ = _resolver(handler, pubmed_ids=True)
    doc = _pdf_doc()
    assert await DoiEnricher(resolver).enrich_documents([doc]) == 0
    assert doc.metadata["doi"] == DOI          # the local leg still improved it
    assert "title" not in doc.metadata
    assert METADATA_SOURCE_KEY not in doc.metadata


# --------------------------------------------------------------------------- #
# Worker-tool wiring (scripts/ingest_shard.py, scripts/embed_shard.py)
# --------------------------------------------------------------------------- #

class _Args(SimpleNamespace):
    pass


@pytest.mark.asyncio
async def test_enricher_from_args_is_none_unless_asked():
    async with httpx.AsyncClient() as http:
        assert enricher_from_args(_Args(), http) is None
        assert enricher_from_args(_Args(doi_enrichment=False), http) is None


@pytest.mark.asyncio
async def test_enricher_from_args_builds_a_configured_enricher(tmp_path):
    async with httpx.AsyncClient() as http:
        enricher = enricher_from_args(
            _Args(doi_enrichment=True, doi_mailto="ops@example.org",
                  doi_cache_dir=str(tmp_path), doi_timeout=3.0, doi_concurrency=2),
            http,
        )
    assert isinstance(enricher, DoiEnricher)


def test_worker_tools_expose_the_same_doi_flags():
    """ingest_shard (the gowe upload path) and embed_shard (the decoupled bulk
    plane) must not drift: both are wired through add_doi_enrichment_args."""
    import argparse

    parser = argparse.ArgumentParser()
    add_doi_enrichment_args(parser)
    args = parser.parse_args(["--doi-enrichment", "--doi-mailto", "a@b.c"])
    assert args.doi_enrichment is True
    assert args.doi_mailto == "a@b.c"
    assert parser.parse_args([]).doi_enrichment is False
    assert set(DOI_ARG_DEST) <= set(vars(parser.parse_args([])))
