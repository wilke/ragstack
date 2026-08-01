"""Aggregation / stats endpoints for the dashboard.

Read-only, tenant-scoped counts. Every count is FILTERED to the caller's
*readable* tenants (own + the shared ``public`` corpus) — never a global store
total — so a caller can never learn the size of another tenant's corpus. Each
store probe degrades to ``available=false`` / ``count=null`` on error rather
than 500-ing (graceful degradation, mirroring the graph endpoints).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from ragstack.api.collections import CollectionRegistry, confined_collection_name
from ragstack.api.deps import (
    get_collections,
    get_graph_store,
    get_text_index,
    get_vector_store,
    probe_tenant_count,
)
from ragstack.api.security import ROLE_ADMIN, Principal, resolve_principal
from ragstack.config import settings
from ragstack.tenancy import allowed_collection_ids, readable_tenants

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
    """Probe a vector/text store's tenant-filtered count, degrading gracefully.

    Shares the probe with /v1/collections via ``deps.probe_tenant_count`` (which
    logs a server-side trail on failure); ``available`` is False whenever the count
    couldn't be obtained (store missing, no method, or the probe errored)."""
    n = await probe_tenant_count(store, tenants)
    return StoreCount(backend=backend, available=n is not None, count=n)


async def _count_graph(
    backend: str, store: Any, tenant_id: str, collection: str | None = None
) -> StoreCount:
    """Graph store size (relationship count), tenant-scoped, degrading gracefully.

    The graph store takes a single ``tenant_id`` and applies ``readable_tenants``
    internally (its existing read convention), so it isn't handed the widened
    list the vector/text stores get. ``collection`` narrows the count for a tenant
    confined by ``TENANT_COLLECTIONS`` — one graph store spans every collection,
    so without it a confined caller would be told the whole graph's size (#209)."""
    if store is None or not hasattr(store, "stats"):
        return StoreCount(backend=backend, available=False, count=None)
    try:
        _entities, relationships = await store.stats(tenant_id, collection=collection)
    except Exception:
        log.warning("stats: %s graph stats probe failed", backend, exc_info=True)
        return StoreCount(backend=backend, available=False, count=None)
    return StoreCount(backend=backend, available=True, count=int(relationships))


@router.get("/stats/stores", response_model=StoreStatsResponse)
async def stats_stores(
    request: Request,
    principal: Principal = Depends(resolve_principal),
    vector_store: Any = Depends(get_vector_store),
    text_index: Any = Depends(get_text_index),
    graph_store: Any = Depends(get_graph_store),
) -> StoreStatsResponse:
    """Per-store counts scoped to the caller's readable tenants (own + public).

    Never a global store total: the vector/text counts are tenant-filtered and
    the graph count is scoped by the store — and, for a confined tenant, by its
    collection. Any store that errors or lacks a count method degrades to
    ``available=false`` / ``count=null``.
    """
    tenants = readable_tenants(principal.tenant)
    graph_collection = confined_collection_name(
        getattr(request.app.state, "collections", None),
        principal.tenant,
        settings.tenant_collections,
    )
    return StoreStatsResponse(
        tenants=tenants,
        vector=await _count_store(settings.vector_backend, vector_store, tenants),
        text=await _count_store(settings.text_backend, text_index, tenants),
        graph=await _count_graph(
            settings.graph_backend, graph_store, principal.tenant, graph_collection
        ),
    )


class TenantCollectionCount(BaseModel):
    """One (tenant, collection) cell: how much of that collection that single
    tenant owns. ``null`` when the store couldn't be counted."""

    collection: str
    label: str
    vector_count: int | None = None
    text_count: int | None = None


class TenantRow(BaseModel):
    tenant: str
    own: bool  # the caller's own tenant, vs the shared public corpus it may also read
    collections: list[TenantCollectionCount]


class TenantsResponse(BaseModel):
    """Who the caller is, what it may reach, and where its readable data lives."""

    tenant: str
    role: str
    readable: list[str]  # own + public — the tenants every read is filtered to
    restricted_to: list[str] | None = None  # collection allowlist; null = unrestricted
    auth_enabled: bool  # false = keyless dev/test path (everyone is the default tenant)
    policy: dict[str, list[str]] | None = None  # admin-only: the full TENANT_COLLECTIONS map
    tenants: list[TenantRow]


@router.get("/stats/tenants", response_model=TenantsResponse)
async def stats_tenants(
    principal: Principal = Depends(resolve_principal),
    registry: CollectionRegistry = Depends(get_collections),
) -> TenantsResponse:
    """The caller's tenancy: identity, readable tenants, collection allowlist, and
    a per-tenant × per-collection count breakdown.

    Unlike /v1/stats/stores — which reports one number per store for the *union*
    of readable tenants — this splits that union apart, so an operator can see
    which tenant actually owns a corpus (e.g. everything sitting in ``public``)
    and per collection rather than in aggregate.

    Still never leaks another tenant's size: the rows are exactly the tenants this
    caller may already read, and the columns exactly the collections its allowlist
    permits. The ``policy`` map names other tenants, so it is admin-only.
    """
    tenants = readable_tenants(principal.tenant)
    allowed = allowed_collection_ids(principal.tenant, settings.tenant_collections)
    entries = [e for e in registry.entries() if allowed is None or e.id in allowed]
    # One count per (tenant, collection, store) — each probe is filtered to a
    # SINGLE tenant, which is what makes the split meaningful. Gathered so the
    # latency is one round-trip rather than 2·|tenants|·|collections|; each probe
    # degrades to None on its own (probe_tenant_count never raises).
    cells = await asyncio.gather(
        *(
            asyncio.gather(
                probe_tenant_count(e.vector_store, [t]),
                probe_tenant_count(e.text_index, [t]),
            )
            for t in tenants
            for e in entries
        )
    )
    rows: list[TenantRow] = []
    for i, t in enumerate(tenants):
        offset = i * len(entries)
        rows.append(
            TenantRow(
                tenant=t,
                own=t == principal.tenant,
                collections=[
                    TenantCollectionCount(
                        collection=e.id,
                        label=e.label,
                        vector_count=cells[offset + j][0],
                        text_count=cells[offset + j][1],
                    )
                    for j, e in enumerate(entries)
                ],
            )
        )
    return TenantsResponse(
        tenant=principal.tenant,
        role=principal.role,
        readable=tenants,
        restricted_to=sorted(allowed) if allowed is not None else None,
        auth_enabled=bool(settings.api_keys),
        policy=(
            {k: list(v) for k, v in settings.tenant_collections.items()}
            if principal.role == ROLE_ADMIN
            else None
        ),
        tenants=rows,
    )
