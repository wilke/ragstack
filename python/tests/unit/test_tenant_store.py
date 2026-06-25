"""Tenant isolation at the vector-store layer."""
import pytest

from ragstack.models import Chunk
from ragstack.stores.memory import InMemoryVectorStore


def _chunk(cid: str, doc_id: str, tenant: str) -> Chunk:
    return Chunk(
        id=cid,
        doc_id=doc_id,
        content=f"chunk {cid}",
        embedding=[1.0, 0.0],
        metadata={"tenant_id": tenant},
    )


@pytest.mark.asyncio
async def test_search_scoped_to_tenant_list():
    store = InMemoryVectorStore()
    await store.upsert(
        [_chunk("1", "dA", "alice"), _chunk("2", "dB", "bob"), _chunk("3", "dP", "public")]
    )
    res = await store.search([1.0, 0.0], top_k=10, filters={"tenant_id": ["alice", "public"]})
    assert {r.chunk.metadata["tenant_id"] for r in res} == {"alice", "public"}  # no bob


@pytest.mark.asyncio
async def test_same_chunk_id_two_tenants_coexist():
    store = InMemoryVectorStore()
    await store.upsert([_chunk("same", "d", "alice")])
    await store.upsert([_chunk("same", "d", "bob")])  # must not clobber alice's
    alice = await store.search([1.0, 0.0], top_k=10, filters={"tenant_id": ["alice", "public"]})
    bob = await store.search([1.0, 0.0], top_k=10, filters={"tenant_id": ["bob", "public"]})
    assert {r.chunk.metadata["tenant_id"] for r in alice} == {"alice"}
    assert {r.chunk.metadata["tenant_id"] for r in bob} == {"bob"}


@pytest.mark.asyncio
async def test_delete_scoped_to_tenant():
    store = InMemoryVectorStore()
    # Two tenants share a doc_id; deleting one tenant's doc must spare the other.
    await store.upsert([_chunk("a", "d1", "alice"), _chunk("b", "d1", "bob")])
    await store.delete("d1", tenant_id="alice")
    res = await store.search([1.0, 0.0], top_k=10)
    assert {r.chunk.metadata["tenant_id"] for r in res} == {"bob"}
