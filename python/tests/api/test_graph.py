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
