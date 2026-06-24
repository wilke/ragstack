"""Unit tests for document loaders — deterministic IDs (idempotent re-ingest)."""
from pathlib import Path

from ragstack.ingestion.chunkers import RecursiveCharacterChunker
from ragstack.ingestion.loaders import StringLoader, TextFileLoader


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
