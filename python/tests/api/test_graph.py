"""API tests for the knowledge-graph endpoints (M4 Phase 1).

Exercise the router over the in-memory graph double wired by the ``client``
fixture. Auth is disabled in tests, so every caller is the ``default`` tenant.
"""
from __future__ import annotations

import pytest

from ragstack.api.main import app
from ragstack.models import Triple
from ragstack.stores import InMemoryGraphStore


@pytest.mark.asyncio
async def test_entities_empty_by_default(client):
    resp = await client.get("/v1/graph/entities")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_entities_and_neighbors_return_real_data(client):
    graph = InMemoryGraphStore()
    await graph.add_triples([
        Triple(subject="Alice", predicate="knows", object="Bob",
               doc_id="d1", tenant_id="default"),
        Triple(subject="Alice", predicate="likes", object="Coffee",
               doc_id="d1", tenant_id="default"),
    ])
    app.state.graph_store = graph

    entities = (await client.get("/v1/graph/entities")).json()
    names = {e["name"] for e in entities}
    assert names == {"Alice", "Bob", "Coffee"}
    alice = next(e for e in entities if e["name"] == "Alice")
    assert alice["triple_count"] == 2  # most-connected first

    neighbors = (await client.get("/v1/graph/neighbors/Alice")).json()
    objects = {t["object"] for t in neighbors}
    assert objects == {"Bob", "Coffee"}


@pytest.mark.asyncio
async def test_endpoints_degrade_to_empty_without_store(client):
    app.state.graph_store = None
    assert (await client.get("/v1/graph/entities")).json() == []
    assert (await client.get("/v1/graph/neighbors/Alice")).json() == []


@pytest.mark.asyncio
async def test_neighbors_scoped_to_tenant(client):
    # Default-tenant caller must not see another tenant's triples.
    graph = InMemoryGraphStore()
    await graph.add_triples([
        Triple(subject="Alice", predicate="knows", object="Bob", tenant_id="default"),
        Triple(subject="Alice", predicate="knows", object="Eve", tenant_id="other"),
    ])
    app.state.graph_store = graph
    neighbors = (await client.get("/v1/graph/neighbors/Alice")).json()
    assert {t["object"] for t in neighbors} == {"Bob"}


@pytest.mark.asyncio
async def test_neighbors_rejects_out_of_range_depth(client):
    # depth feeds a Cypher variable-length traversal, so it's capped to avoid a
    # DoS: out-of-range values are a 422, not an unbounded query.
    assert (await client.get("/v1/graph/neighbors/Alice?depth=0")).status_code == 422
    assert (await client.get("/v1/graph/neighbors/Alice?depth=999")).status_code == 422
    assert (await client.get("/v1/graph/neighbors/Alice?depth=3")).status_code == 200


async def _seed_two_collections() -> InMemoryGraphStore:
    """One graph store holding triples from the ``client`` fixture's collection
    (physical name ``ragstack``) and from another one."""
    graph = InMemoryGraphStore()
    await graph.add_triples([
        Triple(subject="Alice", predicate="knows", object="Bob",
               tenant_id="default", collection="ragstack"),
        Triple(subject="Alice", predicate="knows", object="Eve",
               tenant_id="default", collection="other_corpus"),
    ])
    app.state.graph_store = graph
    return graph


@pytest.mark.asyncio
async def test_confined_tenant_sees_only_its_collections_triples(client, monkeypatch):
    """#209: the graph endpoints take no ``collection`` argument, so a tenant
    confined by TENANT_COLLECTIONS would otherwise inspect triples derived from a
    collection it may not even query."""
    from ragstack.config import settings

    await _seed_two_collections()
    monkeypatch.setattr(settings, "tenant_collections", {"default": ["default"]})

    neighbors = (await client.get("/v1/graph/neighbors/Alice")).json()
    assert {t["object"] for t in neighbors} == {"Bob"}

    entities = {e["name"] for e in (await client.get("/v1/graph/entities")).json()}
    assert entities == {"Alice", "Bob"}

    stats = (await client.get("/v1/graph/stats")).json()
    assert stats["relationships"] == 1


@pytest.mark.asyncio
async def test_unrestricted_caller_keeps_the_cross_collection_view(client, monkeypatch):
    """Operators/admins (and every deployment with the feature off) still get the
    whole graph — which is also the only view that shows pre-#209 triples, written
    before triples carried a collection stamp."""
    from ragstack.config import settings

    graph = await _seed_two_collections()
    await graph.add_triples([
        Triple(subject="Alice", predicate="knows", object="Legacy", tenant_id="default"),
    ])
    monkeypatch.setattr(settings, "tenant_collections", {})

    neighbors = (await client.get("/v1/graph/neighbors/Alice")).json()
    assert {t["object"] for t in neighbors} == {"Bob", "Eve", "Legacy"}
