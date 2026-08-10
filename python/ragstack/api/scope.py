"""Read-scope widening for collections reached through a share.

Extracted from routers/query.py so RETRIEVAL and COUNTING resolve scope through
one implementation. They disagreed: a grantee could query a shared collection
and get results, while every count it was shown read 0 — the UI said "0 chunks"
about a corpus the same key could search.
"""
from __future__ import annotations

import logging

from ragstack.acl_store import get_acl_store
from ragstack.api.access import auth_configured
from ragstack.api.collections import CollectionEntry, CollectionRegistry
from ragstack.api.security import Principal
from ragstack.tenancy import readable_tenants

log = logging.getLogger(__name__)


async def shared_scope(
    entry: CollectionEntry, registry: CollectionRegistry, principal: Principal
) -> list[str]:
    """Extra readable writer-tenants for a collection the caller reaches through a
    share (or the ``public`` grant) rather than owning.

    Read authorization (the ACL share) and data visibility (the per-chunk
    ``tenant_id`` vector scope) are two independent gates. A private collection's
    chunks are stamped with the OWNER's tenant at ingest, so a grantee whose scope
    is only ``{own, public}`` passes the read gate but sees zero of the shared
    chunks. This closes that gap: read access to ``entry.id`` was already enforced
    (:func:`_resolve_entry`), so exposing the owner's tenant — which stamps exactly
    this collection's chunks — for this query is precisely the grant, no wider.

    Two collections that share one physical store break that "no wider": the store
    filters by ``tenant_id`` alone (no ``collection_id`` predicate), so widening to
    the owner's tenant would also surface the owner's chunks in a *co-resident*
    collection that was never shared. So widening is confined to a collection whose
    store is exclusively its own. The ``default`` collection is likewise excluded:
    it is the shared multi-tenant surface where ``tenant_id`` IS the isolation and
    its ownership is only a backfill artifact — widening there would inject the
    backfill owner's tenant into every caller's scope.

    A no-op when auth is unconfigured (the single open dev tenant) or when the
    caller already is the owner. Fail-soft: a store hiccup returns no extra tenant
    (the caller still sees own + public) — it never widens scope on error."""
    if not auth_configured():
        return []
    # The default collection is the multi-tenant shared surface — never widen it.
    if entry.is_shared_surface:
        return []
    # Co-resident store (another registry entry points at the same physical
    # collection): widening by tenant_id would cross the collection boundary the
    # filter can't express. Under-expose (safe) rather than leak the neighbour.
    if any(e.collection == entry.collection for e in registry.entries() if e.id != entry.id):
        return []
    try:
        owner = await get_acl_store().owner_of(entry.id)
    except Exception:  # noqa: BLE001 — never widen scope on a store hiccup
        log.warning(
            "scope: owner_of(%r) failed; not widening read scope", entry.id, exc_info=True
        )
        return []
    if owner and owner != principal.tenant:
        return [owner]
    return []



async def count_scope(
    entry: CollectionEntry, registry: CollectionRegistry, principal: Principal
) -> list[str]:
    """The writer-tenants a COUNT of ``entry`` must include for this caller.

    ``readable_tenants`` (own + public) widened by :func:`shared_scope`, so a
    count reports exactly what a query over the same collection would retrieve.
    Counting anything a query would not return would overstate; counting less —
    which is what the endpoints did before — reports 0 for a corpus the caller
    can search, and reads as "empty" rather than "not yours".

    Widening is per COLLECTION, so it is resolved per entry rather than once per
    request: entry A may be shared with the caller while entry B is not.
    """
    return [*readable_tenants(principal.tenant), *await shared_scope(entry, registry, principal)]
