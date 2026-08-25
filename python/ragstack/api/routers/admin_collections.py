"""Admin handle on the active-collection bound (#359, phase 4 of #353).

``POST /v1/admin/collections/evict?need=k[&dry_run=true]`` runs the same LRU
policy ``POST /v1/collections`` runs at the bound — see
:mod:`ragstack.api.eviction` for what is gathered and :mod:`ragstack.ops.evict`
for the policy and the order of operations. Always 200: fewer than ``need``
victims is a ``shortfall`` with per-reason counts, not an error. Admin-gated
at include time (``api/main.py``), like every ``/v1/admin`` route.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from ragstack.api.collections import CollectionRegistry
from ragstack.api.deps import get_collection_store, get_collections
from ragstack.api.eviction import EvictionResponse, evict_collections
from ragstack.collection_store import CollectionStore

router = APIRouter()


@router.post("/collections/evict", response_model=EvictionResponse)
async def evict_collections_endpoint(
    request: Request,
    need: int = Query(1, ge=1, le=1000, description="How many collections to evict."),
    dry_run: bool = Query(False, description="Report the plan without acting."),
    registry: CollectionRegistry = Depends(get_collections),
    store: CollectionStore = Depends(get_collection_store),
) -> EvictionResponse:
    """Evict the ``need`` least-recently-accessed active collections whose
    Workspace archive is current (never one with an in-flight ingest job,
    never the legacy shared surface's stores or a store two registry ids
    share), or with ``dry_run`` report which ones would be. Each victim's
    registry row is swapped ``active → dormant`` BEFORE its Qdrant collection
    and ES index are dropped; a failed drop is named in the row's reason."""
    return await evict_collections(
        request.app.state, registry, store, need=need, dry_run=dry_run
    )
