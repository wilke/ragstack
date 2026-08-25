"""Knowledge-graph endpoints (M4).

Reads are tenant-scoped: the tenant is derived server-side from the API key
(``resolve_tenant``) and passed to the store, so a caller sees only its own
triples plus the shared ``public`` corpus. When no graph store is configured the
endpoints degrade to empty results rather than erroring (graceful degradation —
no 500s).

They are additionally **collection-scoped for confined tenants** (#209): a tenant
listed in ``TENANT_COLLECTIONS`` sees only triples derived from the collection an
unqualified ``/v1/query`` would serve it, because the single shared graph store
holds every collection's triples. Unrestricted callers (operators/admins, and
every single-collection deployment) keep the cross-collection view — which is
also the only view that shows pre-#209 triples, written before triples carried a
collection stamp. There is deliberately no ``collection`` request parameter: that
would change the API surface (``contracts/openapi.yaml``); per-collection graph
inspection is a follow-up.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from ragstack.api.collections import confined_collection_name
from ragstack.api.deps import get_graph_store
from ragstack.api.security import resolve_tenant
from ragstack.config import settings
from ragstack.protocols import GraphStore

log = logging.getLogger(__name__)

router = APIRouter()


def _scope_collection(request: Request, tenant: str) -> str | None:
    """The collection to scope this caller's graph read to (None = unscoped)."""
    return confined_collection_name(
        getattr(request.app.state, "collections", None), tenant, settings.tenant_collections
    )

# Cap neighbourhood hops: depth feeds a Cypher variable-length traversal, so an
# unbounded value is a DoS. Mirrors stores.neo4j._MAX_DEPTH.
MAX_GRAPH_DEPTH = 5


class EntityInfo(BaseModel):
    name: str
    triple_count: int = 0


class TripleResponse(BaseModel):
    subject: str
    predicate: str
    object: str
    # Epistemic provenance (#347) — optional in the contract, always emitted.
    evidence: str = ""
    chunk_id: str = ""
    derived_by: str = ""
    confidence: int = 0
    subject_id: str = ""
    object_id: str = ""


class GraphStatsResponse(BaseModel):
    backend: str
    available: bool
    entities: int | None
    relationships: int | None


@router.get("/stats", response_model=GraphStatsResponse)
async def graph_stats(
    request: Request,
    tenant: str = Depends(resolve_tenant),
    graph_store: GraphStore | None = Depends(get_graph_store),
) -> GraphStatsResponse:
    """Entity/relationship counts scoped to the caller's readable tenants (own +
    public), and to its collection when it is confined. Degrades to
    ``available=false`` with null counts when no graph store is configured or a
    probe fails (no 500)."""
    backend = settings.graph_backend
    if graph_store is None:
        return GraphStatsResponse(
            backend=backend, available=False, entities=None, relationships=None
        )
    try:
        entities, relationships = await graph_store.stats(
            tenant_id=tenant, collection=_scope_collection(request, tenant)
        )
    except Exception:
        # Degrade to available=false, but log so operators can tell a missing
        # graph store from a misconfigured/down one when counts come back null.
        log.warning("graph/stats: %s stats probe failed", backend, exc_info=True)
        return GraphStatsResponse(
            backend=backend, available=False, entities=None, relationships=None
        )
    return GraphStatsResponse(
        backend=backend, available=True, entities=entities, relationships=relationships
    )


@router.get("/entities", response_model=list[EntityInfo])
async def list_entities(
    request: Request,
    limit: int = Query(default=100, ge=1, le=settings.max_list_limit),
    tenant: str = Depends(resolve_tenant),
    graph_store: GraphStore | None = Depends(get_graph_store),
) -> list[EntityInfo]:
    """List entities in the knowledge graph, most-connected first — scoped to the
    caller's tenant (own + public) and, for a confined tenant, its collection.
    Empty when no graph store is configured."""
    if graph_store is None:
        return []
    entities = await graph_store.list_entities(
        tenant_id=tenant, limit=limit, collection=_scope_collection(request, tenant)
    )
    return [EntityInfo(name=name, triple_count=count) for name, count in entities]


@router.get("/neighbors/{entity}", response_model=list[TripleResponse])
async def get_neighbors(
    request: Request,
    entity: str,
    depth: int = Query(default=1, ge=1, le=MAX_GRAPH_DEPTH),
    tenant: str = Depends(resolve_tenant),
    graph_store: GraphStore | None = Depends(get_graph_store),
) -> list[TripleResponse]:
    """Return triples in the neighbourhood of an entity — scoped to the caller's
    tenant (own + public) and, for a confined tenant, its collection. Empty when
    no graph store is configured."""
    if graph_store is None:
        return []
    triples = await graph_store.query_neighborhood(
        entity, depth=depth, tenant_id=tenant,
        collection=_scope_collection(request, tenant),
    )
    return [
        TripleResponse(
            subject=t.subject, predicate=t.predicate, object=t.object,
            evidence=t.evidence, chunk_id=t.chunk_id, derived_by=t.derived_by,
            confidence=t.confidence, subject_id=t.subject_id, object_id=t.object_id,
        )
        for t in triples
    ]
