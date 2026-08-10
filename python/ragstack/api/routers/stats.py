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

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ragstack.api.access import filter_readable
from ragstack.api.collections import CollectionRegistry, confined_collection_name
from ragstack.api.deps import (
    get_collections,
    get_graph_store,
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


async def _count_across(
    backend: str, targets: dict[str, Any], tenants: list[str]
) -> StoreCount:
    """Sum a store's tenant-filtered count across every readable collection.

    ``targets`` is keyed by PHYSICAL store name, which deduplicates as defence in
    depth only: ADR-0002 makes two registry ids over one store a *startup* error
    (``deps._build_collection_registry`` raises), so a served registry cannot
    contain the collision. The key is what makes that guarantee visible here
    rather than assumed — dropping it would silently double-count if the
    invariant ever regressed.

    All-or-nothing on failure: a partial sum presented as a total is a wrong
    number, which is worse than no number, so if any probe fails the whole store
    degrades to ``available=false`` / ``count=null`` — the same shape a single
    failed probe produced before. No readable collections is a true zero.

    Failure is NAMED: with N collections the aggregate says only that something
    is unavailable, so the store that poisoned it is logged (``probe_tenant_count``
    logs the traceback but not which store it was probing).
    """
    names = list(targets)
    counts = await asyncio.gather(*(probe_tenant_count(t, tenants) for t in targets.values()))
    failed = [n for n, c in zip(names, counts, strict=True) if c is None]
    if failed:
        log.warning(
            "stats: %s count unavailable — probe failed for %s", backend, ", ".join(failed)
        )
        return StoreCount(backend=backend, available=False, count=None)
    return StoreCount(backend=backend, available=True, count=sum(counts))  # type: ignore[arg-type]


@router.get("/stats/stores", response_model=StoreStatsResponse)
async def stats_stores(
    principal: Principal = Depends(resolve_principal),
    registry: CollectionRegistry = Depends(get_collections),
    graph_store: Any = Depends(get_graph_store),
) -> StoreStatsResponse:
    """Per-store counts scoped to the caller's readable tenants (own + public).

    The vector/text counts span EVERY collection the caller may read, not just
    the default one. They used to probe only the settings-derived default store,
    which reported 0 for a deployment whose corpus lives in a named collection —
    each collection is its own physical Qdrant collection / ES index, so the
    default store genuinely holds nothing and the honest total is their sum.
    /v1/stats/tenants splits the same figure per (tenant, collection).

    Never a global store total, and narrower than "everything I can see" in two
    ways worth stating:

    * WHICH collections — the caller's allowlist AND ownership, the same
      intersection /v1/collections applies. An unreadable collection is not
      probed at all.
    * WHOSE chunks — each probe stays filtered to ``readable_tenants`` (own +
      public), so a collection reached through a SHARE contributes the caller's
      own + public chunks in it, typically 0, not the owner's. Query-time scope
      widening (routers/query.py) deliberately does not apply to counting.

    Cost scales with the number of readable collections: one probe per physical
    store per leg. Callers that poll should do so slowly — the Ops dashboard uses
    15s, matching /v1/collections, which carries the same per-collection cost.

    Degradation: any store that errors or lacks a count method degrades that leg
    to ``available=false`` / ``count=null`` (never a 500, never a partial sum
    presented as a total). The ownership filter is the one hard failure — an
    unreachable ACL store fails the request closed with 503.

    Asymmetry to be aware of: vector/text SUM every readable collection, while
    the graph count is scoped to at most one (``confined_collection_name``),
    because one graph store spans them all and #209 narrows it for a confined
    tenant. A confined caller can therefore see a summed vector/text beside a
    single-collection graph figure.
    """
    tenants = readable_tenants(principal.tenant)
    allowed = allowed_collection_ids(principal.tenant, settings.tenant_collections)
    entries = [e for e in registry.entries() if allowed is None or e.id in allowed]
    entries = await filter_readable(principal, entries)
    # Keyed by physical store, per leg — an entry's ES index need not be named
    # after its Qdrant collection. See _count_across on why the keying exists.
    vector_targets: dict[str, Any] = {}
    text_targets: dict[str, Any] = {}
    for e in entries:
        vector_targets.setdefault(e.collection, e.vector_store)
        text_targets.setdefault(e.es_index(), e.text_index)
    graph_collection = confined_collection_name(
        registry, principal.tenant, settings.tenant_collections
    )
    vector, text, graph = await asyncio.gather(
        _count_across(settings.vector_backend, vector_targets, tenants),
        _count_across(settings.text_backend, text_targets, tenants),
        _count_graph(settings.graph_backend, graph_store, principal.tenant, graph_collection),
    )
    return StoreStatsResponse(tenants=tenants, vector=vector, text=text, graph=graph)


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

    Unlike /v1/stats/stores — which reports one number per store, summed over
    every readable collection for the *union* of readable tenants — this splits
    that total apart on both axes, so an operator can see
    which tenant actually owns a corpus (e.g. everything sitting in ``public``)
    and per collection rather than in aggregate.

    Still never leaks another tenant's size: the rows are exactly the tenants this
    caller may already read, and the columns exactly the collections its allowlist
    permits. The ``policy`` map names other tenants, so it is admin-only.
    """
    tenants = readable_tenants(principal.tenant)
    allowed = allowed_collection_ids(principal.tenant, settings.tenant_collections)
    entries = [e for e in registry.entries() if allowed is None or e.id in allowed]
    # Same owner-aware visibility filter as GET /v1/collections: the allowlist
    # gates WHICH collections a tenant may see, ownership gates whether it may READ
    # each — the two intersect. Admin sees all; keyless dev is a no-op.
    entries = await filter_readable(principal, entries)
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
