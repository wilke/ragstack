"""Unit tests for JsonlLoader's opt-in raw-metadata passthrough (#301).

``enrich()`` reads only doi/title/authors/keywords/abstract out of a record's
``metadata``, so every *other* key a corpus carries (JATS/PMC: content_type,
pmcid, pmid, journal, licence, section_title, …) was dropped before chunking.
``passthrough_keys`` opts specific keys back in. These tests pin the four rules
that make that safe: default-off, enriched-wins-on-collision, no empty values,
and no non-scalar values.
"""
from __future__ import annotations

import json
from pathlib import Path

from ragstack.ingestion.loaders import JsonlLoader

# Long enough to classify as ARTICLE rather than SHORT (threshold 1500 chars).
_BODY = "article body " * 200


def _write(path: Path, records: list[dict]) -> str:
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    return str(path)


def _record(**metadata) -> dict:
    return {"path": "/scratch/jvi.02415-06.pdf", "text": _BODY, "metadata": metadata}


# --- default: off -----------------------------------------------------------


def test_default_construction_drops_extra_keys(tmp_path: Path):
    """No passthrough_keys → exactly the enriched subset, as before #301."""
    f = _write(tmp_path / "c.jsonl", [_record(
        title="A Title", content_type="table", pmcid="PMC123", licence="CC-BY",
    )])
    meta = JsonlLoader().load(f)[0].metadata
    assert "content_type" not in meta
    assert "pmcid" not in meta
    assert "licence" not in meta
    # ...and the enriched subset is untouched.
    assert meta["doi"] == "10.1128/jvi.02415-06"
    assert meta["title"] == "A Title"
    # ``year`` is absent because this filename encodes none — index_metadata
    # drops empty values, and the passthrough must not resurrect them either.
    assert set(meta) == {
        "source_path", "filename", "doc_type", "doi", "doi_source", "title",
        "n_citations",
    }


def test_empty_passthrough_list_is_the_same_as_none(tmp_path: Path):
    f = _write(tmp_path / "c.jsonl", [_record(content_type="table")])
    assert (JsonlLoader(passthrough_keys=[]).load(f)[0].metadata
            == JsonlLoader().load(f)[0].metadata)


# --- opted-in keys survive --------------------------------------------------


def test_opted_in_keys_reach_document_metadata(tmp_path: Path):
    f = _write(tmp_path / "c.jsonl", [_record(
        content_type="table", pmcid="PMC123", journal="J Virol", pmid=12345,
        licence="CC-BY", sha256="deadbeef",
    )])
    meta = JsonlLoader(
        passthrough_keys={"content_type", "pmcid", "journal", "pmid"}
    ).load(f)[0].metadata
    assert meta["content_type"] == "table"
    assert meta["pmcid"] == "PMC123"
    assert meta["journal"] == "J Virol"
    assert meta["pmid"] == 12345  # non-string scalars ride through unchanged
    # Keys not opted in stay dropped, even though they are present in the record.
    assert "licence" not in meta
    assert "sha256" not in meta
    # The enriched subset is still intact alongside them.
    assert meta["doi"] == "10.1128/jvi.02415-06"


def test_flat_list_of_scalars_passes_through(tmp_path: Path):
    """Lists are what the enriched schema itself emits (authors/keywords) and ES
    maps a string array through the same keyword template, so they are allowed."""
    f = _write(tmp_path / "c.jsonl", [_record(mesh_terms=["Virology", "RNA"])])
    meta = JsonlLoader(passthrough_keys={"mesh_terms"}).load(f)[0].metadata
    assert meta["mesh_terms"] == ["Virology", "RNA"]


# --- enriched wins on collision ---------------------------------------------


def test_enriched_doi_wins_over_raw_passthrough_doi(tmp_path: Path):
    """The derived/normalised DOI survives; the raw record value never overwrites
    it. Here enrich() strips the raw value, so the two differ observably."""
    f = _write(tmp_path / "c.jsonl", [_record(doi="  10.1128/jvi.02415-06  ")])
    meta = JsonlLoader(passthrough_keys={"doi", "doi_source"}).load(f)[0].metadata
    assert meta["doi"] == "10.1128/jvi.02415-06"  # normalised, not the raw string
    assert meta["doi_source"] == "metadata"


def test_bogus_doi_source_cannot_shadow_the_derived_one(tmp_path: Path):
    """doi_source is derived by enrich() from *where* the DOI came from; a raw key
    of the same name must not be able to lie about it."""
    f = _write(tmp_path / "c.jsonl", [_record(doi_source="bogus")])
    meta = JsonlLoader(passthrough_keys={"doi_source"}).load(f)[0].metadata
    # No metadata DOI → derived from the filename rule, and that is what is stamped.
    assert meta["doi"] == "10.1128/jvi.02415-06"
    assert meta["doi_source"] == "filename"


def test_enriched_title_wins_over_raw_passthrough_title(tmp_path: Path):
    f = _write(tmp_path / "c.jsonl", [_record(title="  Real Title  ")])
    meta = JsonlLoader(passthrough_keys={"title"}).load(f)[0].metadata
    assert meta["title"] == "Real Title"


def test_blank_raw_value_cannot_take_a_dropped_enriched_slot(tmp_path: Path):
    """index_metadata drops an empty enriched title, freeing the key name. A
    whitespace-only raw value must not slide into that slot."""
    f = _write(tmp_path / "c.jsonl", [_record(title="   ")])
    meta = JsonlLoader(passthrough_keys={"title"}).load(f)[0].metadata
    assert "title" not in meta


# --- empty / absent / non-scalar values -------------------------------------


def test_absent_key_does_not_appear_as_an_empty_value(tmp_path: Path):
    f = _write(tmp_path / "c.jsonl", [_record(content_type="table")])
    meta = JsonlLoader(
        passthrough_keys={"content_type", "pmcid", "section_title"}
    ).load(f)[0].metadata
    assert meta["content_type"] == "table"
    assert "pmcid" not in meta
    assert "section_title" not in meta


def test_empty_values_are_dropped_like_index_metadata_does(tmp_path: Path):
    f = _write(tmp_path / "c.jsonl", [_record(
        pmcid="", licence=None, mesh_terms=[], section_title="   ", content_type="table",
    )])
    meta = JsonlLoader(passthrough_keys={
        "pmcid", "licence", "mesh_terms", "section_title", "content_type",
    }).load(f)[0].metadata
    assert "pmcid" not in meta
    assert "licence" not in meta
    assert "mesh_terms" not in meta
    assert "section_title" not in meta
    assert meta["content_type"] == "table"


def test_non_scalar_values_are_dropped(tmp_path: Path):
    """A nested object would miss the ES ``metadata.*`` dynamic template (it
    matches one level only) and land as text+keyword, breaking exact-term
    filtering; so objects, and lists containing them, never pass through."""
    f = _write(tmp_path / "c.jsonl", [_record(
        affiliations={"1": "Some University"},
        figures=[{"id": "f1"}],
        nested=[["a", "b"]],
        content_type="table",
    )])
    meta = JsonlLoader(passthrough_keys={
        "affiliations", "figures", "nested", "content_type",
    }).load(f)[0].metadata
    assert "affiliations" not in meta
    assert "figures" not in meta
    assert "nested" not in meta
    assert meta["content_type"] == "table"


def test_record_without_a_metadata_object_is_tolerated(tmp_path: Path):
    """A record with no ``metadata`` at all must not raise when passthrough is on.

    (A record whose ``metadata`` is present but not an object already raises
    inside ``enrich()`` — pre-existing, upstream of the passthrough, which is why
    ``_metadata`` carries no isinstance guard of its own.)
    """
    f = _write(tmp_path / "c.jsonl", [{"path": "/scratch/jvi.02415-06.pdf", "text": _BODY}])
    docs = JsonlLoader(passthrough_keys={"content_type"}).load(f)
    assert len(docs) == 1
    assert "content_type" not in docs[0].metadata
    assert docs[0].metadata["doi"] == "10.1128/jvi.02415-06"


# --- realistic JATS shape ---------------------------------------------------


def test_jats_record_keeps_content_type_and_pmcid(tmp_path: Path):
    f = _write(tmp_path / "pmc.jsonl", [{
        "text": _BODY,
        "path": "PMC123#table-2",
        "metadata": {
            "content_type": "table",
            "pmcid": "PMC123",
            "licence": "CC-BY",
            "section_title": "Results",
        },
    }])
    keys = ("content_type", "pmcid", "licence", "section_title")
    doc = JsonlLoader(passthrough_keys=keys).load(f)[0]
    assert doc.metadata["content_type"] == "table"
    assert doc.metadata["pmcid"] == "PMC123"
    assert doc.metadata["licence"] == "CC-BY"
    assert doc.metadata["section_title"] == "Results"
    # The enriched fields still ride along; the non-PDF path just has no DOI.
    assert doc.metadata["source_path"] == "PMC123#table-2"
    assert doc.metadata["filename"] == "PMC123#table-2"
    assert doc.source == "PMC123#table-2"


def test_jats_default_construction_loses_content_type(tmp_path: Path):
    """The bug #301 fixes, pinned: without opting in, content_type is gone and
    'filter to tables at query time' is unanswerable."""
    f = _write(tmp_path / "pmc.jsonl", [{
        "text": _BODY, "path": "PMC123#table-2",
        "metadata": {"content_type": "table", "pmcid": "PMC123"},
    }])
    assert "content_type" not in JsonlLoader().load(f)[0].metadata


def test_document_id_is_unaffected_by_passthrough(tmp_path: Path):
    """The id is derived from the record path only — opting keys in must not move
    it, or an opt-in would silently duplicate an already-ingested corpus."""
    f = _write(tmp_path / "c.jsonl", [_record(content_type="table")])
    assert (JsonlLoader().load(f)[0].id
            == JsonlLoader(passthrough_keys={"content_type"}).load(f)[0].id)


# --------------------------------------------------------------------------
# doc-id stability across working directories (#301 GoWe duplication bug)
# --------------------------------------------------------------------------


def test_relative_path_doc_id_is_independent_of_cwd(tmp_path, monkeypatch):
    """A record whose ``path`` is an opaque relative identifier (JATS:
    ``PMC123#table-2``) must get the same doc id wherever the loader runs.
    Path.resolve() prepended the process CWD, so a GoWe worker (task workdir)
    and a shell run (checkout dir) minted two id families for the same shard —
    delete-prior matched nothing and the corpus duplicated instead of
    upserting. Found live: +12,233 points on what should have been a no-op."""
    from ragstack.ingestion.loaders import JsonlLoader

    line = json.dumps({"text": "body text long enough to classify",
                       "path": "PMC6305292#table-1", "metadata": {}})
    a, b = tmp_path / "a", tmp_path / "b"
    ids = []
    for d in (a, b):
        d.mkdir()
        src = d / "shard.jsonl"
        src.write_text(line + "\n")
        monkeypatch.chdir(d)
        ids.append(JsonlLoader().load(str(src))[0].id)
    assert ids[0] == ids[1], "doc id must not depend on the working directory"


def test_absolute_path_doc_id_unchanged(tmp_path):
    """Absolute paths keep the resolve() behaviour — existing corpora (ASM PDFs
    were ingested with absolute paths) must keep their doc ids."""
    from ragstack.ingestion.loaders import JsonlLoader, deterministic_doc_id

    p = tmp_path / "shard.jsonl"
    abs_path = str(tmp_path / "corpus" / "x.pdf")
    p.write_text(json.dumps({"text": "body text long enough",
                             "path": abs_path, "metadata": {}}) + "\n")
    doc = JsonlLoader().load(str(p))[0]
    assert doc.id == deterministic_doc_id(str(Path(abs_path).resolve()))
