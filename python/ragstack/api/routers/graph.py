"""Knowledge-graph endpoints (M4).

Reads are tenant-scoped: the tenant is derived server-side from the API key
(``resolve_tenant``) and passed to the store, so a caller sees only its own
triples plus the shared ``public`` corpus. When no graph store is configured the
endpoints degrade to empty results rather than erroring (graceful degradation —
no 500s).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ragstack.api.deps import get_graph_store
from ragstack.api.security import resolve_tenant
from ragstack.protocols import GraphStore

router = APIRouter()

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


@router.get("/entities", response_model=list[EntityInfo])
async def list_entities(
    limit: int = 100,
    tenant: str = Depends(resolve_tenant),
    graph_store: GraphStore | None = Depends(get_graph_store),
) -> list[EntityInfo]:
    """List entities in the knowledge graph, most-connected first — scoped to the
    caller's tenant (own + public). Empty when no graph store is configured."""
    if graph_store is None:
        return []
    entities = await graph_store.list_entities(tenant_id=tenant, limit=limit)
    return [EntityInfo(name=name, triple_count=count) for name, count in entities]


@router.get("/neighbors/{entity}", response_model=list[TripleResponse])
async def get_neighbors(
    entity: str,
    depth: int = Query(default=1, ge=1, le=MAX_GRAPH_DEPTH),
    tenant: str = Depends(resolve_tenant),
    graph_store: GraphStore | None = Depends(get_graph_store),
) -> list[TripleResponse]:
    """Return triples in the neighbourhood of an entity — scoped to the caller's
    tenant (own + public). Empty when no graph store is configured."""
    if graph_store is None:
        return []
    triples = await graph_store.query_neighborhood(entity, depth=depth, tenant_id=tenant)
    return [
        TripleResponse(subject=t.subject, predicate=t.predicate, object=t.object)
        for t in triples
    ]
