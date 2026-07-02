"""Aggregation / stats endpoints for the dashboard.

Read-only, tenant-scoped counts. Every count is FILTERED to the caller's
*readable* tenants (own + the shared ``public`` corpus) — never a global store
total — so a caller can never learn the size of another tenant's corpus. Each
store probe degrades to ``available=false`` / ``count=null`` on error rather
than 500-ing (graceful degradation, mirroring the graph endpoints).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ragstack.api.deps import get_graph_store, get_text_index, get_vector_store
from ragstack.api.security import Principal, resolve_principal
from ragstack.config import settings
from ragstack.tenancy import readable_tenants

log = logging.getLogger(__name__)

router = APIRouter()


class StoreCount(BaseModel):
    """One store's tenant-filtered count (``None`` when unavailable)."""

    backend: str
    available: bool
    count: int | None


class StoreStatsResponse(BaseModel):
    tenants: list[str]
    vector: StoreCount
    text: StoreCount
    graph: StoreCount


async def _count_store(backend: str, store: Any, tenants: list[str]) -> StoreCount:
    """Probe a vector/text store's tenant-filtered count, degrading gracefully."""
    if store is None or not hasattr(store, "count_tenants"):
        return StoreCount(backend=backend, available=False, count=None)
    try:
        n = await store.count_tenants(tenants)
    except Exception:
        # Degrade to available=false, but leave a server-side trail — an operator
        # seeing a null count needs to distinguish "down" from "misconfigured".
        log.warning("stats: %s count_tenants probe failed", backend, exc_info=True)
        return StoreCount(backend=backend, available=False, count=None)
    return StoreCount(backend=backend, available=True, count=int(n))


async def _count_graph(backend: str, store: Any, tenant_id: str) -> StoreCount:
    """Graph store size (relationship count), tenant-scoped, degrading gracefully.

    The graph store takes a single ``tenant_id`` and applies ``readable_tenants``
    internally (its existing read convention), so it isn't handed the widened
    list the vector/text stores get."""
    if store is None or not hasattr(store, "stats"):
        return StoreCount(backend=backend, available=False, count=None)
    try:
        _entities, relationships = await store.stats(tenant_id)
    except Exception:
        log.warning("stats: %s graph stats probe failed", backend, exc_info=True)
        return StoreCount(backend=backend, available=False, count=None)
    return StoreCount(backend=backend, available=True, count=int(relationships))


@router.get("/stats/stores", response_model=StoreStatsResponse)
async def stats_stores(
    principal: Principal = Depends(resolve_principal),
    vector_store: Any = Depends(get_vector_store),
    text_index: Any = Depends(get_text_index),
    graph_store: Any = Depends(get_graph_store),
) -> StoreStatsResponse:
    """Per-store counts scoped to the caller's readable tenants (own + public).

    Never a global store total: the vector/text counts are tenant-filtered and
    the graph count is scoped by the store. Any store that errors or lacks a
    count method degrades to ``available=false`` / ``count=null``.
    """
    tenants = readable_tenants(principal.tenant)
    return StoreStatsResponse(
        tenants=tenants,
        vector=await _count_store(settings.vector_backend, vector_store, tenants),
        text=await _count_store(settings.text_backend, text_index, tenants),
        graph=await _count_graph(settings.graph_backend, graph_store, principal.tenant),
    )
