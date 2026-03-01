"""Unit tests for chunkers."""
import pytest

from ragstack.ingestion.chunkers import RecursiveCharacterChunker
from ragstack.models import Document


def _make_doc(content: str) -> Document:
    return Document(id="doc1", content=content, source="test")


def test_chunk_short_text_produces_single_chunk():
    chunker = RecursiveCharacterChunker(chunk_size=512, chunk_overlap=64)
    doc = _make_doc("Hello world")
    chunks = chunker.chunk(doc)
    assert len(chunks) == 1
    assert chunks[0].content == "Hello world"
    assert chunks[0].doc_id == "doc1"


def test_chunk_long_text_produces_multiple_chunks():
    chunker = RecursiveCharacterChunker(chunk_size=10, chunk_overlap=2)
    doc = _make_doc("abcdefghijklmnopqrstuvwxyz")
    chunks = chunker.chunk(doc)
    assert len(chunks) > 1
    # All chunk contents should be substrings of the original
    for chunk in chunks:
        assert chunk.content in doc.content or doc.content.startswith(chunk.content)


def test_chunk_overlap_is_respected():
    chunker = RecursiveCharacterChunker(chunk_size=10, chunk_overlap=3)
    doc = _make_doc("0123456789abcdefghij")
    chunks = chunker.chunk(doc)
    # The second chunk should start 7 characters after the first (10 - 3)
    assert chunks[1].start_char == 7


def test_chunk_ids_are_unique():
    chunker = RecursiveCharacterChunker(chunk_size=10, chunk_overlap=0)
    doc = _make_doc("abcdefghijklmnopqrstuvwxyz")
    chunks = chunker.chunk(doc)
    ids = [c.id for c in chunks]
    assert len(ids) == len(set(ids))


def test_chunk_metadata_inherited_from_doc():
    chunker = RecursiveCharacterChunker(chunk_size=512)
    doc = _make_doc("text")
    doc.metadata = {"author": "alice"}
    chunks = chunker.chunk(doc)
    assert chunks[0].metadata["author"] == "alice"
