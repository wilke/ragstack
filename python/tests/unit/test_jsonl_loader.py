"""Unit tests for JsonlLoader — per-line documents, metadata propagation,
empty-skip / doc_type filtering, malformed-line tolerance, deterministic IDs,
and registry dispatch on the ``.jsonl`` suffix."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ragstack.ingestion.enrich import ARTICLE, FRONT_MATTER
from ragstack.ingestion.loaders import (
    DEFAULT_INGEST_SUFFIXES,
    JsonlLoader,
    LoaderError,
    default_loader_registry,
)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")


def _article(name: str, text: str | None = None) -> dict:
    return {
        "path": f"/scratch/{name}",
        "text": text or ("article body " * 200),
        "metadata": {"title": f"Title {name}", "authors": "Alice;Bob", "doi": ""},
    }


def test_loads_one_document_per_line(tmp_path: Path):
    f = tmp_path / "corpus.jsonl"
    _write_jsonl(f, [_article("jvi.00001-20.pdf"), _article("jvi.00002-20.pdf")])
    docs = JsonlLoader().load(str(f))
    assert len(docs) == 2
    assert docs[0].content.startswith("article body")
    assert docs[0].source == "/scratch/jvi.00001-20.pdf"


def test_metadata_is_enriched_onto_document(tmp_path: Path):
    f = tmp_path / "corpus.jsonl"
    _write_jsonl(f, [_article("jvi.02415-06.pdf")])
    doc = JsonlLoader().load(str(f))[0]
    assert doc.metadata["doi"] == "10.1128/jvi.02415-06"
    assert doc.metadata["doi_source"] == "filename"
    assert doc.metadata["doc_type"] == ARTICLE
    assert doc.metadata["authors"] == ["Alice", "Bob"]
    # Heavy fields never ride on the document/chunk payload.
    assert "citations" not in doc.metadata


def test_empty_records_skipped_by_default(tmp_path: Path):
    f = tmp_path / "corpus.jsonl"
    _write_jsonl(f, [
        _article("jvi.00001-20.pdf"),
        {"path": "/scratch/cover.pdf", "text": "", "metadata": {}},
    ])
    docs = JsonlLoader().load(str(f))
    assert len(docs) == 1


def test_doc_type_filter(tmp_path: Path):
    f = tmp_path / "corpus.jsonl"
    _write_jsonl(f, [
        _article("jvi.00001-20.pdf"),
        {"path": "/scratch/masthead.pdf", "text": "x" * 5000, "metadata": {}},
    ])
    # Skip both empty AND front-matter -> only the article survives.
    from ragstack.ingestion.enrich import EMPTY
    docs = JsonlLoader(skip_types={EMPTY, FRONT_MATTER}).load(str(f))
    assert len(docs) == 1
    assert docs[0].metadata["doc_type"] == ARTICLE


def test_malformed_lines_are_skipped(tmp_path: Path):
    f = tmp_path / "corpus.jsonl"
    f.write_text(
        json.dumps(_article("jvi.00001-20.pdf")) + "\n{ this is not json }\n\n"
        + json.dumps(_article("jvi.00002-20.pdf")),
        encoding="utf-8",
    )
    docs = JsonlLoader().load(str(f))
    assert len(docs) == 2


def test_empty_source_raises(tmp_path: Path):
    f = tmp_path / "corpus.jsonl"
    _write_jsonl(f, [{"path": "/scratch/cover.pdf", "text": "", "metadata": {}}])
    with pytest.raises(LoaderError):
        JsonlLoader().load(str(f))


def test_document_ids_deterministic_across_loads(tmp_path: Path):
    f = tmp_path / "corpus.jsonl"
    _write_jsonl(f, [_article("jvi.00001-20.pdf")])
    a = JsonlLoader().load(str(f))[0]
    b = JsonlLoader().load(str(f))[0]
    assert a.id == b.id


def test_registry_dispatches_jsonl(tmp_path: Path):
    assert ".jsonl" in DEFAULT_INGEST_SUFFIXES
    f = tmp_path / "corpus.jsonl"
    _write_jsonl(f, [_article("jvi.00001-20.pdf")])
    registry = default_loader_registry(ingest_root=str(tmp_path))
    docs = registry.load(str(f))
    assert len(docs) == 1
    assert docs[0].metadata["doi"] == "10.1128/jvi.00001-20"
