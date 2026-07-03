"""Unit tests for the #86 document registry: aggregating a distinct-document
list off the served (text) index, tenant-scoped, with opaque cursor pagination.

Exercises ``InMemoryTextIndex.list_documents`` (the behavioural mirror of the ES
composite aggregation) plus the shared projection/cursor helpers.
"""
from __future__ import annotations

import pytest

from ragstack.documents import (
    decode_cursor,
    document_from_chunk_metadata,
    encode_cursor,
)
from ragstack.models import Chunk
from ragstack.stores import InMemoryTextIndex


def _chunk(doc_id: str, idx: int, tenant: str = "public", **meta) -> Chunk:
    return Chunk(
        id=f"{doc_id}:{idx}",
        doc_id=doc_id,
        content=f"content of {doc_id} chunk {idx}",
        start_char=idx * 100,
        end_char=idx * 100 + 100,
        metadata={
            "tenant_id": tenant,
            "chunk_index": idx,
            "prev_chunk_id": None,
            "next_chunk_id": None,
            "source_path": f"/corpus/{doc_id}.pdf",
            "title": f"Title {doc_id}",
            "doc_type": "ARTICLE",
            **meta,
        },
    )


async def _index(chunks: list[Chunk]) -> InMemoryTextIndex:
    ix = InMemoryTextIndex()
    await ix.index(chunks)
    return ix


# --------------------------------------------------------------------------- #
# Projection + cursor helpers
# --------------------------------------------------------------------------- #
def test_projection_drops_chunk_level_keys_and_derives_source() -> None:
    meta = {
        "tenant_id": "public",
        "chunk_index": 3,
        "prev_chunk_id": "x",
        "next_chunk_id": "y",
        "source_path": "/corpus/d1.pdf",
        "title": "T",
        "doc_type": "ARTICLE",
    }
    d = document_from_chunk_metadata("d1", 5, meta)
    assert d.doc_id == "d1"
    assert d.chunk_count == 5
    assert d.source == "/corpus/d1.pdf"
    # doc-level fields survive; chunk-level ones are dropped
    assert d.metadata["title"] == "T"
    assert d.metadata["doc_type"] == "ARTICLE"
    for dropped in ("chunk_index", "prev_chunk_id", "next_chunk_id", "doc_id"):
        assert dropped not in d.metadata


def test_source_falls_back_to_filename() -> None:
    d = document_from_chunk_metadata("d1", 1, {"filename": "d1.pdf"})
    assert d.source == "d1.pdf"


def test_cursor_roundtrip_and_malformed() -> None:
    assert decode_cursor(encode_cursor("doc-42")) == "doc-42"
    with pytest.raises(ValueError):
        decode_cursor("!!!not-base64!!!")


# --------------------------------------------------------------------------- #
# list_documents: dedup, counts, tenant scoping
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_dedup_by_doc_id_with_chunk_count() -> None:
    ix = await _index([_chunk("d1", 0), _chunk("d1", 1), _chunk("d2", 0)])
    docs, nxt = await ix.list_documents(["public"])
    assert nxt is None
    ids = {d.doc_id: d.chunk_count for d in docs}
    assert ids == {"d1": 2, "d2": 1}


@pytest.mark.asyncio
async def test_tenant_scoping_own_plus_public_excludes_others() -> None:
    ix = await _index(
        [_chunk("pub", 0, tenant="public"),
         _chunk("mine", 0, tenant="alice"),
         _chunk("theirs", 0, tenant="bob")]
    )
    docs, _ = await ix.list_documents(["alice", "public"])
    assert {d.doc_id for d in docs} == {"pub", "mine"}  # not bob's


@pytest.mark.asyncio
async def test_empty_tenants_fails_closed() -> None:
    ix = await _index([_chunk("d1", 0)])
    assert await ix.list_documents([]) == ([], None)


# --------------------------------------------------------------------------- #
# Pagination
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_pagination_walks_all_docs_in_doc_id_order() -> None:
    ix = await _index([_chunk(f"d{i:02d}", 0) for i in range(5)])
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        docs, cursor = await ix.list_documents(["public"], limit=2, cursor=cursor)
        seen.extend(d.doc_id for d in docs)
        pages += 1
        if cursor is None:
            break
        assert pages < 10  # guard against a non-terminating cursor
    assert seen == [f"d{i:02d}" for i in range(5)]  # doc_id order, no repeats
    assert pages == 3  # 2 + 2 + 1


@pytest.mark.asyncio
async def test_pagination_terminates_at_exact_multiple_boundary() -> None:
    # Total == 2 * limit: the boundary case. The in-memory store knows the total,
    # so it terminates without a trailing empty page (ES composite, lacking a cheap
    # count, may emit one extra empty page — both are contract-valid: the client
    # loop stops when next_cursor is None either way).
    ix = await _index([_chunk(f"d{i}", 0) for i in range(4)])
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        docs, cursor = await ix.list_documents(["public"], limit=2, cursor=cursor)
        seen.extend(d.doc_id for d in docs)
        pages += 1
        if cursor is None:
            break
        assert pages < 10
    assert seen == ["d0", "d1", "d2", "d3"]  # all, each once
    assert pages == 2  # no trailing empty page from the in-memory store
