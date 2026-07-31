"""Tenant isolation at the vector-store layer."""
import pytest

from ragstack.models import Chunk, Triple
from ragstack.stores.memory import (
    InMemoryGraphStore,
    InMemoryTextIndex,
    InMemoryVectorStore,
)


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


@pytest.mark.asyncio
async def test_empty_list_filter_matches_nothing():
    # An empty multi-value filter means "match nothing" (`x in []` is false), not
    # "no constraint on this key" (#196) — the latter would turn a degenerate
    # scope into an unfiltered, cross-tenant read.
    store = InMemoryVectorStore()
    await store.upsert([_chunk("1", "dA", "alice"), _chunk("2", "dB", "bob")])
    res = await store.search([1.0, 0.0], top_k=10, filters={"tenant_id": []})
    assert res == []


@pytest.mark.asyncio
async def test_empty_second_scope_key_does_not_widen_the_read():
    # The shape that makes this dangerous: a second scope dimension sourced from
    # a lookup (visible libraries) comes back empty → must narrow to nothing,
    # not fall back to the tenant scope alone.
    store = InMemoryVectorStore()
    await store.upsert([_chunk("1", "dA", "alice"), _chunk("2", "dP", "public")])
    res = await store.search(
        [1.0, 0.0], top_k=10, filters={"library_id": [], "tenant_id": ["alice", "public"]}
    )
    assert res == []


@pytest.mark.asyncio
async def test_absent_filter_key_is_still_unconstrained():
    # Only an absent key means "no constraint" — a filter dict with no keys, or
    # none at all, must stay unfiltered.
    store = InMemoryVectorStore()
    await store.upsert([_chunk("1", "dA", "alice"), _chunk("2", "dB", "bob")])
    assert len(await store.search([1.0, 0.0], top_k=10)) == 2
    assert len(await store.search([1.0, 0.0], top_k=10, filters={})) == 2


@pytest.mark.asyncio
async def test_text_index_empty_list_filter_matches_nothing():
    # _matches backs the text index too, so the fail-closed reading must hold
    # on the BM25 side (where a widened read is just as much of a leak).
    idx = InMemoryTextIndex()
    await idx.index([_chunk("1", "dA", "alice"), _chunk("2", "dB", "bob")])
    assert await idx.search("chunk", top_k=10, filters={"tenant_id": []}) == []
    assert len(await idx.search("chunk", top_k=10)) == 2


@pytest.mark.asyncio
async def test_get_chunks_empty_scope_returns_nothing():
    store = InMemoryVectorStore()
    await store.upsert([_chunk("1", "dA", "alice"), _chunk("2", "dB", "bob")])
    assert await store.get_chunks(["1", "2"], filters={"tenant_id": []}) == []
    assert len(await store.get_chunks(["1", "2"], filters={"tenant_id": ["alice"]})) == 1


@pytest.mark.asyncio
async def test_text_index_same_chunk_id_two_tenants_coexist():
    # InMemoryTextIndex.index must key on (tenant, id), else the second tenant's
    # chunk is dropped as a duplicate when two tenants share a source.
    idx = InMemoryTextIndex()
    await idx.index([_chunk("same", "d", "alice")])
    await idx.index([_chunk("same", "d", "bob")])
    alice = await idx.search("chunk", top_k=10, filters={"tenant_id": ["alice", "public"]})
    bob = await idx.search("chunk", top_k=10, filters={"tenant_id": ["bob", "public"]})
    assert {r.chunk.metadata["tenant_id"] for r in alice} == {"alice"}
    assert {r.chunk.metadata["tenant_id"] for r in bob} == {"bob"}


@pytest.mark.asyncio
async def test_text_index_delete_scoped_to_tenant():
    idx = InMemoryTextIndex()
    await idx.index([_chunk("a", "d1", "alice"), _chunk("b", "d1", "bob")])
    await idx.delete("d1", tenant_id="alice")  # must spare bob's copy
    remaining = await idx.search("chunk", top_k=10)
    assert {r.chunk.metadata["tenant_id"] for r in remaining} == {"bob"}


@pytest.mark.asyncio
async def test_graph_delete_scoped_to_tenant():
    graph = InMemoryGraphStore()
    await graph.add_triples([Triple(subject="s", predicate="p", object="o",
                                    doc_id="d1", tenant_id="alice")])
    await graph.add_triples([Triple(subject="s2", predicate="p", object="o2",
                                    doc_id="d1", tenant_id="bob")])
    await graph.delete_by_doc("d1", tenant_id="alice")  # must spare bob's triple
    remaining = await graph.query_neighborhood("s2")
    assert [t.tenant_id for t in remaining] == ["bob"]


@pytest.mark.asyncio
async def test_graph_add_triples_dedup_keyed_by_tenant():
    # Two tenants ingesting the same surface-form triple must BOTH survive — the
    # dedup is per-tenant (matches Neo4j's tenant-keyed MERGE), not (s,p,o)-only
    # which would silently drop the second tenant's copy.
    graph = InMemoryGraphStore()
    await graph.add_triples([Triple(subject="A", predicate="isA", object="Co",
                                    doc_id="da", tenant_id="alice")])
    await graph.add_triples([Triple(subject="A", predicate="isA", object="Co",
                                    doc_id="db", tenant_id="bob")])
    assert {t.tenant_id for t in await graph.query_neighborhood("A")} == {"alice", "bob"}
    # Same tenant re-adding the identical triple is still deduped.
    await graph.add_triples([Triple(subject="A", predicate="isA", object="Co",
                                    doc_id="da", tenant_id="alice")])
    assert len(await graph.query_neighborhood("A", tenant_id=None)) == 2


@pytest.mark.asyncio
async def test_graph_query_scoped_to_readable_tenants():
    graph = InMemoryGraphStore()
    await graph.add_triples([
        Triple(subject="A", predicate="p", object="B", doc_id="d", tenant_id="alice"),
        Triple(subject="A", predicate="p", object="C", doc_id="d", tenant_id="bob"),
        Triple(subject="A", predicate="p", object="D", doc_id="d", tenant_id="public"),
    ])
    # Alice sees her own + public, never bob's.
    triples = await graph.query_neighborhood("A", tenant_id="alice")
    assert {t.object for t in triples} == {"B", "D"}
    # Unscoped (dev/tests) sees everything.
    everyone = await graph.query_neighborhood("A", tenant_id=None)
    assert {t.object for t in everyone} == {"B", "C", "D"}


@pytest.mark.asyncio
async def test_graph_list_entities_scoped_and_ranked():
    graph = InMemoryGraphStore()
    await graph.add_triples([
        Triple(subject="A", predicate="p", object="B", tenant_id="alice"),
        Triple(subject="A", predicate="p", object="C", tenant_id="alice"),
        Triple(subject="X", predicate="p", object="Y", tenant_id="bob"),
    ])
    entities = await graph.list_entities(tenant_id="alice")
    names = {name for name, _ in entities}
    assert names == {"A", "B", "C"}  # bob's X/Y excluded
    # A participates in 2 triples → ranked first.
    assert entities[0] == ("A", 2)
