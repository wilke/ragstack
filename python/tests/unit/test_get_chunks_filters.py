"""Unit tests for get_chunks' shared filter predicate (#197).

Both InMemoryVectorStore and QdrantVectorStore resolve get_chunks by point id
(O(ids), never a filtered scan) but must apply every OTHER filter key to the
returned payload identically — via the shared ``payload_matches`` predicate
(stores/filters.py) — so the two backends cannot silently diverge on which
scope constraints actually take effect.

The predicate's grammar MUST match ``_build_filter``/``_matches`` for the keys
they already support: a bare key is a metadata lookup, full stop (see
stores/filters.py's module docstring) — #322 passes a caller-supplied,
bare-key ``filters`` dict straight through, so a caller writing
``{"journal": "mBio"}`` (docs/API.md's documented grammar) has to get the same
answer from ``search()`` and from ``get_chunks()``.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from ragstack.models import Chunk
from ragstack.stores.filters import UnknownFilterKey, payload_matches
from ragstack.stores.memory import InMemoryVectorStore, _matches

pytestmark = pytest.mark.asyncio


def _chunk(cid: str, tenant: str, **meta: Any) -> Chunk:
    return Chunk(
        id=cid, doc_id=f"d-{cid}", content=f"chunk {cid}",
        metadata={"tenant_id": tenant, **meta},
    )


# --------------------------------------------------------------------------- #
# Bare-key / search() parity (the blocking finding in review)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "filters",
    [
        {"tenant_id": ["alice", "public"]},
        {"tenant_id": ["bob"]},
        {"tenant_id": []},
        {"source": "paper.pdf"},
        {"source": "other.pdf"},
        {"tenant_id": ["alice", "public"], "source": "paper.pdf"},
        {},
    ],
)
def test_bare_key_semantics_agree_between_search_and_get_chunks(filters):
    """``_matches`` (backs ``search()``) and ``payload_matches`` (backs
    ``get_chunks``) must accept/reject an identical bare-key filters dict the
    same way on the same chunk — the same dict a caller builds for one has to
    work for the other."""
    chunk = _chunk("a", "alice", source="paper.pdf")
    assert _matches(chunk, filters) == payload_matches(chunk.metadata, filters)


async def test_memory_search_and_get_chunks_agree_on_a_bare_metadata_key():
    """End-to-end version of the parity check above, through the public API of
    both read paths."""
    store = InMemoryVectorStore()
    a = _chunk("a", "alice", source="paper.pdf")
    a.embedding = [1.0, 0.0]
    b = _chunk("b", "alice", source="other.pdf")
    b.embedding = [1.0, 0.0]
    await store.upsert([a, b])

    filters = {"tenant_id": ["alice", "public"], "source": "paper.pdf"}
    searched = await store.search([1.0, 0.0], top_k=10, filters=filters)
    fetched = await store.get_chunks(["a", "b"], filters=filters)

    assert {r.chunk.id for r in searched} == {"a"}
    assert [c.id for c in fetched] == ["a"]


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


async def test_memory_honours_bare_metadata_filter_key():
    store = InMemoryVectorStore()
    await store.upsert(
        [
            _chunk("a", "alice", source="paper.pdf"),
            _chunk("b", "alice", source="other.pdf"),
        ]
    )
    got = await store.get_chunks(
        ["a", "b"],
        filters={"tenant_id": ["alice", "public"], "source": "paper.pdf"},
    )
    assert [c.id for c in got] == ["a"]


async def test_memory_honours_metadata_prefixed_alias():
    """``metadata.<key>`` is accepted too — the prefix is stripped, so it
    addresses the same field as the bare key above."""
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


@pytest.mark.parametrize("bad_key", ["doc_id", "chunk_id", "content", "library_id"])
async def test_memory_refused_key_raises(bad_key):
    store = InMemoryVectorStore()
    await store.upsert([_chunk("a", "alice")])
    with pytest.raises(UnknownFilterKey):
        await store.get_chunks(["a"], filters={"tenant_id": ["alice"], bad_key: "x"})


async def test_memory_refused_key_raises_even_with_zero_matching_records():
    """The refusal must not be data-dependent: an id that resolves to nothing
    still has to raise on the bad key, not silently return []."""
    store = InMemoryVectorStore()
    with pytest.raises(UnknownFilterKey):
        await store.get_chunks(["missing"], filters={"tenant_id": ["alice"], "doc_id": "x"})


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


async def test_qdrant_honours_bare_metadata_filter_key():
    recs = dict(
        [
            _record("a", "alice", source="paper.pdf"),
            _record("b", "alice", source="other.pdf"),
        ]
    )
    store, _client = _qdrant_store(recs)
    got = await store.get_chunks(
        ["a", "b"],
        filters={"tenant_id": ["alice", "public"], "source": "paper.pdf"},
    )
    assert [c.id for c in got] == ["a"]


async def test_qdrant_honours_metadata_prefixed_alias():
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


@pytest.mark.parametrize("bad_key", ["doc_id", "chunk_id", "content", "library_id"])
async def test_qdrant_refused_key_raises(bad_key):
    recs = dict([_record("a", "alice")])
    store, _client = _qdrant_store(recs)
    with pytest.raises(UnknownFilterKey):
        await store.get_chunks(["a"], filters={"tenant_id": ["alice"], bad_key: "x"})


async def test_qdrant_refused_key_raises_even_with_zero_matching_records():
    """The refusal must not be data-dependent: an id that resolves to nothing
    (empty ``retrieve`` result) still has to raise on the bad key, not
    silently return []."""
    store, _client = _qdrant_store({})
    with pytest.raises(UnknownFilterKey):
        await store.get_chunks(["missing"], filters={"tenant_id": ["alice"], "doc_id": "x"})


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
