"""Unit tests for get_chunks' shared filter predicate (#197).

Both InMemoryVectorStore and QdrantVectorStore resolve get_chunks by point id
(O(ids), never a filtered scan) but must apply every OTHER filter key to the
returned payload identically — via the shared ``payload_matches`` predicate
(stores/filters.py) — so the two backends cannot silently diverge on which
scope constraints actually take effect.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from ragstack.models import Chunk
from ragstack.stores.filters import UnknownFilterKey
from ragstack.stores.memory import InMemoryVectorStore

pytestmark = pytest.mark.asyncio


def _chunk(cid: str, tenant: str, **meta: Any) -> Chunk:
    return Chunk(
        id=cid, doc_id=f"d-{cid}", content=f"chunk {cid}",
        metadata={"tenant_id": tenant, **meta},
    )


# --------------------------------------------------------------------------- #
# InMemoryVectorStore
# --------------------------------------------------------------------------- #

async def test_memory_omits_id_under_readable_tenant_but_different_collection():
    store = InMemoryVectorStore()
    await store.upsert(
        [
            _chunk("a", "alice", collection="mine"),
            _chunk("b", "alice", collection="other"),
        ]
    )
    got = await store.get_chunks(
        ["a", "b"], filters={"tenant_id": ["alice", "public"], "collection": "mine"}
    )
    assert [c.id for c in got] == ["a"]


async def test_memory_honours_metadata_filter_key():
    store = InMemoryVectorStore()
    await store.upsert(
        [
            _chunk("a", "alice", source="paper.pdf"),
            _chunk("b", "alice", source="other.pdf"),
        ]
    )
    got = await store.get_chunks(
        ["a", "b"],
        filters={"tenant_id": ["alice", "public"], "metadata.source": "paper.pdf"},
    )
    assert [c.id for c in got] == ["a"]


async def test_memory_unknown_filter_key_raises():
    store = InMemoryVectorStore()
    await store.upsert([_chunk("a", "alice")])
    with pytest.raises(UnknownFilterKey):
        await store.get_chunks(["a"], filters={"tenant_id": ["alice"], "bogus": "x"})


async def test_memory_unknown_filter_key_raises_even_with_zero_matching_records():
    """The refusal must not be data-dependent: an id that resolves to nothing
    still has to raise on the bad key, not silently return []."""
    store = InMemoryVectorStore()
    with pytest.raises(UnknownFilterKey):
        await store.get_chunks(["missing"], filters={"tenant_id": ["alice"], "bogus": "x"})


# --------------------------------------------------------------------------- #
# QdrantVectorStore — fake AsyncQdrantClient.retrieve, no network
# --------------------------------------------------------------------------- #

pytest.importorskip("qdrant_client")

from ragstack.stores.qdrant import QdrantVectorStore, _point_id  # noqa: E402


class _FakeRetrieveClient:
    """Minimal async stand-in for AsyncQdrantClient.retrieve, keyed by point id.
    Counts calls so the perf test can assert exactly one round trip."""

    def __init__(self, records: dict[str, SimpleNamespace]) -> None:
        self._records = records
        self.retrieve_calls = 0

    async def retrieve(self, collection_name, ids, with_payload, with_vectors):
        self.retrieve_calls += 1
        return [self._records[i] for i in ids if i in self._records]


def _record(chunk_id: str, tenant: str, **meta: Any) -> tuple[str, SimpleNamespace]:
    pid = _point_id(chunk_id, tenant)
    payload = {
        "chunk_id": chunk_id, "doc_id": f"d-{chunk_id}", "content": "x",
        "tenant_id": tenant, **meta,
    }
    return pid, SimpleNamespace(id=pid, payload=payload)


def _qdrant_store(
    records: dict[str, SimpleNamespace],
) -> tuple[QdrantVectorStore, _FakeRetrieveClient]:
    store = QdrantVectorStore(collection="c", vector_size=4)
    client = _FakeRetrieveClient(records)
    store._client = client  # swap in the fake (no network)
    return store, client


async def test_qdrant_omits_id_under_readable_tenant_but_different_collection():
    recs = dict(
        [
            _record("a", "alice", collection="mine"),
            _record("b", "alice", collection="other"),
        ]
    )
    store, _client = _qdrant_store(recs)
    got = await store.get_chunks(
        ["a", "b"], filters={"tenant_id": ["alice", "public"], "collection": "mine"}
    )
    assert [c.id for c in got] == ["a"]


async def test_qdrant_honours_metadata_filter_key():
    recs = dict(
        [
            _record("a", "alice", source="paper.pdf"),
            _record("b", "alice", source="other.pdf"),
        ]
    )
    store, _client = _qdrant_store(recs)
    got = await store.get_chunks(
        ["a", "b"],
        filters={"tenant_id": ["alice", "public"], "metadata.source": "paper.pdf"},
    )
    assert [c.id for c in got] == ["a"]


async def test_qdrant_unknown_filter_key_raises():
    recs = dict([_record("a", "alice")])
    store, _client = _qdrant_store(recs)
    with pytest.raises(UnknownFilterKey):
        await store.get_chunks(["a"], filters={"tenant_id": ["alice"], "bogus": "x"})


async def test_qdrant_unknown_filter_key_raises_even_with_zero_matching_records():
    """The refusal must not be data-dependent: an id that resolves to nothing
    (empty ``retrieve`` result) still has to raise on the bad key, not
    silently return []."""
    store, _client = _qdrant_store({})
    with pytest.raises(UnknownFilterKey):
        await store.get_chunks(["missing"], filters={"tenant_id": ["alice"], "bogus": "x"})


async def test_qdrant_tenant_mismatch_in_payload_is_excluded():
    """Defense in depth (mirrors InMemoryVectorStore, which has always
    re-checked tenant_id against the chunk rather than trusting the point-id
    derivation alone): a record whose payload disagrees with every requested
    tenant is dropped even though its point id was computed from one of them."""
    pid = _point_id("a", "alice")
    payload = {"chunk_id": "a", "doc_id": "d-a", "content": "x", "tenant_id": "someone-else"}
    store, _client = _qdrant_store({pid: SimpleNamespace(id=pid, payload=payload)})
    got = await store.get_chunks(["a"], filters={"tenant_id": ["alice", "public"]})
    assert got == []
