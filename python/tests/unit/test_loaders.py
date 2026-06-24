"""Unit tests for document loaders — deterministic IDs (idempotent re-ingest),
extension dispatch, PDF extraction, and ingest-root confinement."""
from pathlib import Path

import pytest

from ragstack.ingestion.chunkers import RecursiveCharacterChunker
from ragstack.ingestion.loaders import (
    LoaderError,
    PdfLoader,
    StringLoader,
    TextFileLoader,
    default_loader_registry,
)


def _write_pdf(path: Path, text: str = "Hello PDF world") -> None:
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


def test_stringloader_doc_id_is_deterministic():
    loader = StringLoader()
    a = loader.load("hello world")[0]
    b = loader.load("hello world")[0]
    assert a.id == b.id
    # Different content -> different ID.
    assert loader.load("different content")[0].id != a.id


def test_textfileloader_doc_id_stable_across_loads(tmp_path: Path):
    f = tmp_path / "doc.txt"
    f.write_text("some content", encoding="utf-8")
    loader = TextFileLoader()
    first = loader.load(str(f))[0]
    second = loader.load(str(f))[0]
    assert first.id == second.id
    # A different file path -> different ID.
    g = tmp_path / "other.txt"
    g.write_text("some content", encoding="utf-8")
    assert loader.load(str(g))[0].id != first.id


def test_reingest_produces_identical_chunk_ids(tmp_path: Path):
    """Load-bearing idempotency guard: same file -> same chunk IDs -> same Qdrant points."""
    f = tmp_path / "doc.txt"
    f.write_text("abcdefghijklmnopqrstuvwxyz" * 3, encoding="utf-8")
    loader = TextFileLoader()
    chunker = RecursiveCharacterChunker(chunk_size=10, chunk_overlap=2)

    def ingest_ids() -> list[str]:
        doc = loader.load(str(f))[0]
        return [c.id for c in chunker.chunk(doc)]

    assert ingest_ids() == ingest_ids()


def test_pdfloader_extracts_text(tmp_path: Path):
    pdf = tmp_path / "doc.pdf"
    _write_pdf(pdf, "The quick brown fox")
    docs = PdfLoader().load(str(pdf))
    assert len(docs) == 1
    assert "quick brown fox" in docs[0].content
    assert docs[0].metadata["filename"] == "doc.pdf"
    assert docs[0].metadata["pages"] == 1


def test_pdfloader_empty_pdf_raises(tmp_path: Path):
    pymupdf = pytest.importorskip("pymupdf")
    pdf = tmp_path / "blank.pdf"
    doc = pymupdf.open()
    doc.new_page()  # a page with no text
    doc.save(str(pdf))
    doc.close()
    with pytest.raises(LoaderError):
        PdfLoader().load(str(pdf))


def test_registry_dispatches_by_extension(tmp_path: Path):
    txt = tmp_path / "a.txt"
    txt.write_text("plain text", encoding="utf-8")
    pdf = tmp_path / "b.pdf"
    _write_pdf(pdf, "pdf body text")

    registry = default_loader_registry()
    assert registry.load(str(txt))[0].content == "plain text"
    assert "pdf body text" in registry.load(str(pdf))[0].content


def test_registry_confines_to_ingest_root(tmp_path: Path):
    root = tmp_path / "corpus"
    root.mkdir()
    inside = root / "ok.txt"
    inside.write_text("inside", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("outside", encoding="utf-8")

    registry = default_loader_registry(ingest_root=str(root))
    assert registry.load(str(inside))[0].content == "inside"
    with pytest.raises(LoaderError):
        registry.load(str(outside))
    # Path-traversal attempt out of the root is rejected too.
    with pytest.raises(LoaderError):
        registry.load(str(root / ".." / "secret.txt"))


def test_registry_rejects_oversized(tmp_path: Path):
    f = tmp_path / "big.txt"
    f.write_text("x" * 1000, encoding="utf-8")
    registry = default_loader_registry(max_bytes=100)
    with pytest.raises(LoaderError):
        registry.load(str(f))


def test_registry_missing_source_raises(tmp_path: Path):
    registry = default_loader_registry()
    with pytest.raises(LoaderError):
        registry.load(str(tmp_path / "does-not-exist.txt"))
