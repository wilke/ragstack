"""Unit tests for QdrantSink — the coupled path preserved behind the #141 seam.

The Sink refactor must leave the default (upsert-to-Qdrant) behaviour byte-for-byte:
upsert-first, ES index, and prune-by-doc only under --replace. These use fakes for
the store + text index (no network).
"""
from __future__ import annotations

from ragstack.ingestion.sinks import QdrantSink
from ragstack.models import Chunk


class _FakeStore:
    def __init__(self) -> None:
        self.upserts: list[list[Chunk]] = []
        self.deletes: list[tuple[str, set[str], str | None]] = []

    async def upsert(self, chunks):
        self.upserts.append(list(chunks))

    async def delete_except(self, doc_id, keep_chunk_ids, tenant_id=None):
        self.deletes.append((doc_id, set(keep_chunk_ids), tenant_id))


class _FakeText:
    def __init__(self) -> None:
        self.indexed: list[list[Chunk]] = []
        self.deletes: list[tuple[str, set[str], str | None]] = []
        self.closed = False

    async def index(self, chunks):
        self.indexed.append(list(chunks))

    async def delete_except(self, doc_id, keep_chunk_ids, tenant_id=None):
        self.deletes.append((doc_id, set(keep_chunk_ids), tenant_id))

    async def close(self):
        self.closed = True


def _chunks() -> list[Chunk]:
    return [
        Chunk(id="a", doc_id="d1", content="x", embedding=[0.1],
              metadata={"tenant_id": "public"}),
        Chunk(id="b", doc_id="d1", content="y", embedding=[0.2],
              metadata={"tenant_id": "public"}),
    ]


async def test_write_upserts_and_indexes_no_prune_by_default():
    store, text = _FakeStore(), _FakeText()
    sink = QdrantSink(store, text, "public", replace=False)
    await sink.write(_chunks())
    assert len(store.upserts) == 1 and len(store.upserts[0]) == 2
    assert len(text.indexed) == 1
    assert store.deletes == [] and text.deletes == []


async def test_write_prunes_per_doc_under_replace():
    store, text = _FakeStore(), _FakeText()
    sink = QdrantSink(store, text, "public", replace=True)
    await sink.write(_chunks())
    # upsert first, then one prune for doc d1 keeping both chunk ids, tenant-scoped.
    assert len(store.upserts) == 1
    assert store.deletes == [("d1", {"a", "b"}, "public")]
    assert text.deletes == [("d1", {"a", "b"}, "public")]


async def test_empty_batch_is_a_noop():
    store, text = _FakeStore(), _FakeText()
    sink = QdrantSink(store, text, "public", replace=True)
    await sink.write([])
    assert store.upserts == [] and text.indexed == [] and store.deletes == []


async def test_aclose_closes_text_index():
    store, text = _FakeStore(), _FakeText()
    sink = QdrantSink(store, text, "public")
    await sink.aclose()
    assert text.closed is True


async def test_write_without_text_index_only_upserts():
    store = _FakeStore()
    sink = QdrantSink(store, None, "public", replace=False)
    await sink.write(_chunks())
    assert len(store.upserts) == 1
    await sink.aclose()  # must not raise with no text index
