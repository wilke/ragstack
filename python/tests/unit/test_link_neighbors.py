"""Unit tests for link_neighbors — prev/next/chunk_index neighbor metadata.

``link_neighbors`` stamps ``chunk_index`` / ``prev_chunk_id`` / ``next_chunk_id``
on a document's ORDERED final chunk list, using the doc-level ``chunk.id``.
"""
from __future__ import annotations

from ragstack.ingestion.chunkers import (
    RecursiveCharacterChunker,
    link_neighbors,
)
from ragstack.models import Chunk, Document


def _chunks(n: int) -> list[Chunk]:
    return [
        Chunk(id=f"id{i}", doc_id="doc1", content=f"c{i}", start_char=i, end_char=i + 1)
        for i in range(n)
    ]


def test_chunk_index_is_0_to_n_minus_1_in_order():
    chunks = _chunks(5)
    link_neighbors(chunks)
    assert [c.metadata["chunk_index"] for c in chunks] == [0, 1, 2, 3, 4]


def test_prev_and_next_equal_neighbor_ids():
    chunks = _chunks(4)
    link_neighbors(chunks)
    for i, c in enumerate(chunks):
        expected_prev = chunks[i - 1].id if i > 0 else None
        expected_next = chunks[i + 1].id if i < len(chunks) - 1 else None
        assert c.metadata["prev_chunk_id"] == expected_prev
        assert c.metadata["next_chunk_id"] == expected_next


def test_first_prev_and_last_next_are_none():
    chunks = _chunks(3)
    link_neighbors(chunks)
    assert chunks[0].metadata["prev_chunk_id"] is None
    assert chunks[-1].metadata["next_chunk_id"] is None
    # Interior chunk has both.
    assert chunks[1].metadata["prev_chunk_id"] == "id0"
    assert chunks[1].metadata["next_chunk_id"] == "id2"


def test_single_chunk_doc_index0_prev_next_none():
    chunks = _chunks(1)
    link_neighbors(chunks)
    assert chunks[0].metadata["chunk_index"] == 0
    assert chunks[0].metadata["prev_chunk_id"] is None
    assert chunks[0].metadata["next_chunk_id"] is None


def test_empty_list_is_noop():
    chunks: list[Chunk] = []
    link_neighbors(chunks)  # must not raise
    assert chunks == []


def test_uses_doc_level_chunk_id_not_point_id():
    # The links must reference chunk.id (uuid5(doc_id:start:end)), which is exactly
    # what the store point-id derives FROM (tenant:chunk_id) — but link_neighbors
    # must use the bare chunk.id so links are tenant-independent and idempotent.
    chunks = _chunks(2)
    link_neighbors(chunks)
    assert chunks[0].metadata["next_chunk_id"] == chunks[1].id == "id1"
    assert chunks[1].metadata["prev_chunk_id"] == chunks[0].id == "id0"


def test_preserves_existing_metadata():
    chunks = [
        Chunk(id="a", doc_id="d", content="x", start_char=0, end_char=1,
              metadata={"title": "T", "tenant_id": "public"}),
        Chunk(id="b", doc_id="d", content="y", start_char=1, end_char=2,
              metadata={"title": "T", "tenant_id": "public"}),
    ]
    link_neighbors(chunks)
    for c in chunks:
        assert c.metadata["title"] == "T"
        assert c.metadata["tenant_id"] == "public"
    assert chunks[0].metadata["next_chunk_id"] == "b"


def test_integration_with_a_real_chunker():
    # link_neighbors on a real chunker's ORDERED output: indices 0..n-1, prev/next
    # chain matches, endpoints None.
    doc = Document(id="doc1", content="abcdefghijklmnopqrstuvwxyz", source="t")
    chunks = RecursiveCharacterChunker(chunk_size=10, chunk_overlap=2).chunk(doc)
    assert len(chunks) > 1
    link_neighbors(chunks)
    assert [c.metadata["chunk_index"] for c in chunks] == list(range(len(chunks)))
    for i, c in enumerate(chunks):
        assert c.metadata["prev_chunk_id"] == (chunks[i - 1].id if i > 0 else None)
        assert c.metadata["next_chunk_id"] == (
            chunks[i + 1].id if i < len(chunks) - 1 else None
        )
