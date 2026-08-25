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


def _widening_eligible(entry: CollectionEntry, registry: CollectionRegistry) -> bool:
    """Is ``entry`` a candidate for owner-tenant widening at all, independent of
    who owns it? False for the shared multi-tenant surface (``is_shared_surface``
    — ``tenant_id`` IS its isolation, and its ownership is only a backfill
    artifact) and for a collection that is CO-RESIDENT with another registry
    entry (same physical Qdrant collection or ES index): the store filters by
    ``tenant_id`` alone, no ``collection_id`` predicate, so widening one of a
    pair would leak the other's chunks too. Shared by :func:`shared_scope` and
    :func:`shared_scope_many` so the two guards can't drift apart."""
    if entry.is_shared_surface:
        return False
    return not any(
        e.collection == entry.collection or e.es_index() == entry.es_index()
        for e in registry.entries()
        if e.id != entry.id
    )


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

    See :func:`_widening_eligible` for the co-residency / shared-surface guards.

    A no-op when auth is unconfigured (the single open dev tenant) or when the
    caller already is the owner. Fail-soft: a store hiccup returns no extra tenant
    (the caller still sees own + public) — it never widens scope on error.

    Single-entry — used by the query path (one collection per request). A
    listing resolving this for every entry it shows should call
    :func:`shared_scope_many` instead: this issues one ``owner_of`` ACL round
    trip per call, which is fine for one collection and an N+1 for N of them
    (issue #314)."""
    if not auth_configured():
        return []
    if not _widening_eligible(entry, registry):
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


async def shared_scope_many(
    entries: list[CollectionEntry], registry: CollectionRegistry, principal: Principal
) -> dict[str, list[str]]:
    """Batch counterpart of :func:`shared_scope` — same semantics and guards,
    per entry, but ONE ``AclStore.owners_of`` round trip for the whole list
    instead of one ``owner_of`` call per entry (issue #314: the listing
    endpoints — ``GET /v1/collections``, ``/v1/stats/stores``,
    ``/v1/stats/tenants`` — each paid N ACL calls resolving count scope for N
    entries). The result always has one key per entry in ``entries``.

    Falls back to the per-entry loop (still correct, just not batched) when the
    configured store hasn't got ``owners_of`` — a test double or a future
    backend that only implements the ``AclStore`` protocol's older surface."""
    if not auth_configured():
        return {e.id: [] for e in entries}
    eligible = [e for e in entries if _widening_eligible(e, registry)]
    store = get_acl_store()
    owners_of = getattr(store, "owners_of", None)
    owners: dict[str, str | None] = {}
    if eligible and owners_of is not None:
        try:
            owners = await owners_of([e.id for e in eligible])
        except Exception:  # noqa: BLE001 — never widen scope on a store hiccup
            log.warning(
                "scope: owners_of(...) failed for %d entries; not widening read scope",
                len(eligible), exc_info=True,
            )
            owners = {}
    elif eligible:
        for e in eligible:
            try:
                owners[e.id] = await store.owner_of(e.id)
            except Exception:  # noqa: BLE001 — never widen scope on a store hiccup
                log.warning(
                    "scope: owner_of(%r) failed; not widening read scope", e.id, exc_info=True
                )
                owners[e.id] = None
    out: dict[str, list[str]] = {}
    for e in entries:
        owner = owners.get(e.id)
        out[e.id] = [owner] if owner and owner != principal.tenant else []
    return out


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

    Single-entry, like :func:`shared_scope` — a listing should use
    :func:`count_scope_many`.
    """
    return [*readable_tenants(principal.tenant), *await shared_scope(entry, registry, principal)]


async def count_scope_many(
    entries: list[CollectionEntry], registry: CollectionRegistry, principal: Principal
) -> dict[str, list[str]]:
    """Batch counterpart of :func:`count_scope`, built on
    :func:`shared_scope_many` — same per-entry semantics, one ACL round trip
    for the whole listing (issue #314)."""
    extra = await shared_scope_many(entries, registry, principal)
    base = readable_tenants(principal.tenant)
    return {e.id: [*base, *extra[e.id]] for e in entries}
