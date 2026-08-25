"""Aggregation / stats endpoints for the dashboard.

Read-only, tenant-scoped counts. Every count is FILTERED to what the caller may
read — own + the shared ``public`` corpus, plus, per collection, a tenant whose
data a SHARE makes readable (``api/scope``) — never a global store total. The
bound is "what a query with this credential returns", so a caller never learns
the size of a corpus it could not retrieve. Each
store probe degrades to ``available=false`` / ``count=null`` on error rather
than 500-ing (graceful degradation, mirroring the graph endpoints).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ragstack.api.access import filter_readable
from ragstack.api.collections import CollectionRegistry, confined_collection_name
from ragstack.api.deps import (
    get_collections,
    get_graph_store,
    probe_tenant_count,
)
from ragstack.api.scope import count_scope_many, shared_scope_many
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


async def _no_count() -> None:
    """A cell that was deliberately not probed (null, not zero)."""
    return None


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
    backend: str, targets: dict[str, tuple[Any, list[str]]]
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
    counts = await asyncio.gather(
        *(probe_tenant_count(store, scope) for store, scope in targets.values())
    )
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
    * WHOSE chunks — ``readable_tenants`` (own + public), WIDENED per collection
      by any share that grants read (``api/scope.count_scope``), which is the
      same widening retrieval applies. A count therefore reports what a query
      over that collection would return — never more, and no longer the 0 that
      made a shared corpus read as empty.

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
    allowed = registry.permitted(
        allowed_collection_ids(principal.tenant, settings.tenant_collections)
    )
    entries = [e for e in registry.entries() if allowed is None or e.id in allowed]
    entries = await filter_readable(principal, entries)
    # Keyed by physical store, per leg — an entry's ES index need not be named
    # after its Qdrant collection. See _count_across on why the keying exists.
    # Each entry carries its OWN scope: a shared collection counts the owner's
    # chunks (what a query over it returns), an unshared one stays own+public.
    # Resolved in ONE ACL round trip for the whole listing (count_scope_many,
    # issue #314), not one owner lookup per entry.
    scope_map = await count_scope_many(entries, registry, principal)
    scopes = [scope_map[e.id] for e in entries]
    vector_targets: dict[str, tuple[Any, list[str]]] = {}
    text_targets: dict[str, tuple[Any, list[str]]] = {}
    for e, sc in zip(entries, scopes, strict=True):
        vector_targets.setdefault(e.collection, (e.vector_store, sc))
        text_targets.setdefault(e.es_index(), (e.text_index, sc))
    graph_collection = confined_collection_name(
        registry, principal.tenant, settings.tenant_collections
    )
    vector, text, graph = await asyncio.gather(
        _count_across(settings.vector_backend, vector_targets),
        _count_across(settings.text_backend, text_targets),
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
    counts: bool = Query(
        True,
        description=(
            "Count the cells. Pass false for the identity/reach fields alone: every "
            "count comes back null and NO store is probed. Default true (unchanged)."
        ),
    ),
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

    Rows include a writer-tenant reached through a SHARE (``scope.shared_scope``),
    because a query over that collection returns its chunks — omitting the row
    reported 0 for a corpus the caller can search, and made this endpoint's split
    disagree with /v1/stats/stores' sum. A shared row is scoped to the collections
    that actually share with the caller: another collection's cell in that row is
    ``null``, never the owner's size.

    ``counts=false`` answers the identity half alone — tenant, role,
    ``auth_enabled``, ``readable``, ``restricted_to``, and the collection columns
    with null counts. This is the endpoint's OTHER caller: the UI has no /v1/me,
    so it resolves "who am I" here on every credential change, and the grid it
    throws away costs one count per (tenant, collection, store). On a large
    deployment that is 5s — Qdrant's exact count hits its timeout and falls back
    to an estimate — for three fields that need no store at all.
    """
    tenants = readable_tenants(principal.tenant)
    allowed = registry.permitted(
        allowed_collection_ids(principal.tenant, settings.tenant_collections)
    )
    entries = [e for e in registry.entries() if allowed is None or e.id in allowed]
    # Same owner-aware visibility filter as GET /v1/collections: the allowlist
    # gates WHICH collections a tenant may see, ownership gates whether it may READ
    # each — the two intersect. Admin sees all; keyless dev is a no-op.
    entries = await filter_readable(principal, entries)
    # Which extra writer-tenant (if any) each entry is readable through, so a
    # shared corpus gets a row instead of reading as zero everywhere.
    #
    # Skipped with counts=false, and NOT merely as an optimisation: shared_scope
    # is an owner lookup (batched via shared_scope_many, issue #314 — ONE ACL
    # round trip for the whole listing, not one per collection) that exists
    # only to decide count SCOPE, and a share-derived row is admitted only when
    # it carries a non-zero count (see below) — with no counts there is nothing
    # it could admit. So the cheap path is the caller's own scopes, resolved
    # with no ACL work beyond the one batched read filter authorization already
    # required.
    shared_by_tenant: dict[str, set[str]] = {}
    if counts:
        extra_map = await shared_scope_many(entries, registry, principal)
        for e in entries:
            for owner in extra_map[e.id]:
                shared_by_tenant.setdefault(owner, set()).add(e.id)
    rows_tenants = [*tenants, *(t for t in sorted(shared_by_tenant) if t not in tenants)]

    # One count per (tenant, collection, store) — each probe filtered to a SINGLE
    # tenant, which is what makes the split meaningful. A share-derived row is
    # probed ONLY for the collections that share with this caller; the rest stay
    # null so the row can never carry that tenant's size elsewhere.
    def _probe(store: object, tenant: str, entry_id: str):
        allowed_here = tenant in tenants or entry_id in shared_by_tenant.get(tenant, set())
        return probe_tenant_count(store, [tenant]) if allowed_here else _no_count()

    # (vector, text) per (tenant, collection), row-major over rows_tenants. Left
    # empty with counts=false — nothing indexes it on that path.
    cells: list[Any] = []
    if counts:
        cells = await asyncio.gather(
            *(
                asyncio.gather(
                    _probe(e.vector_store, t, e.id),
                    _probe(e.text_index, t, e.id),
                )
                for t in rows_tenants
                for e in entries
            )
        )
    rows: list[TenantRow] = []
    for i, t in enumerate(rows_tenants):
        offset = i * len(entries)
        cols: list[TenantCollectionCount] = []
        for j, e in enumerate(entries):
            # One lookup guarded once, rather than indexing an empty list twice
            # and relying on the ternary to short-circuit it.
            vector_count, text_count = cells[offset + j] if counts else (None, None)
            cols.append(
                TenantCollectionCount(
                    collection=e.id,
                    label=e.label,
                    vector_count=vector_count,
                    text_count=text_count,
                )
            )
        # A share-derived row is emitted only when it CARRIES something. The row
        # is keyed by the owner's subject — for a bearer identity that is an
        # email — and an all-zero row would disclose who owns a collection to a
        # caller who cannot read that from anywhere else: GET .../shares is 403
        # for a non-owner, and with no chunks there is no metadata.tenant_id to
        # see either. Non-zero means a query already returns chunks stamped with
        # that tenant, so the row tells the caller nothing new. Own/public rows
        # are unconditional — they are the caller's own scopes.
        if t not in tenants and not any(
            (c.vector_count or 0) or (c.text_count or 0) for c in cols
        ):
            continue
        rows.append(TenantRow(tenant=t, own=t == principal.tenant, collections=cols))
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
