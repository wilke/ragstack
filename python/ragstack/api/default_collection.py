"""The ONE answer to "which collection does an omitted ``collection`` mean?" (#419).

``GET /v1/collections`` documents its ``default`` field as *"the id served when a
request omits ``collection``"* and computes it **caller-aware**. ``/v1/query``,
``/v1/retrieve`` and ``/v1/chunks`` used to resolve the **global** registry
pointer instead and then 404 on the ownership seam — so a caller whose readable
set excluded the tenant default was told, by id, that a collection they had
never been shown did not exist. That was a **conformance violation**, not an
open semantic question: the contract already said the right thing and the
diverging side was the bug.

It shipped because there were **four** independent computations of "the default
for this caller", with three different rule sets, disagreeing on two axes:

===============================================  ================  ==================
site                                             visibility        fallback order
===============================================  ================  ==================
``routers/collections.py`` (the listing)         allowlist ∩ read  insertion
``routers/query.py::_effective_collection``      allowlist only    lexicographic
``api/collections.py::confined_collection_name`` allowlist only    lexicographic
``routers/documents.py``                         none              n/a
===============================================  ================  ==================

This module is the shared symbol the first two now import, so "who else resolves
a default?" is a one-grep question. The last two are a tracked follow-up — and
the ingest half of ``documents.py`` needs a **writable**-set picker, not this
read-based one, or an upload would be routed into a collection the caller can
read but not own.

**The rules, in one place:**

* visibility is **allowlist ∩ readable** — ownership INTERSECTS confinement,
  never replaces it (ADR-0003 decision 3);
* the pick is the registry pointer **when it is visible**, else the first
  visible entry in **insertion order** — the order the listing shows and the
  order the user sees in the picker, so "the first one in your list" is
  literally true on screen (deliberately not ``sorted()``, which is what
  ``_effective_collection`` used; see the PR for #419);
* no visible entry at all is a 404 that names **no id**.

**Lifecycle is deliberately NOT a filter here.** The listing lists dormant
collections, so the pick must be able to choose one or the listing's ``default``
and the query target would disagree again. The caller gets the same 503 +
``Retry-After`` + triggered restore they would get by naming it.

**Cost.** The implicit path goes from 2 store calls to 3: one batched
``filter_readable`` (``resolve_read_many``), then ``enforce_access``'s
``owner_of`` + ``grants_for_subject``. ``enforce_access`` stays — the collection
LIFECYCLE gate lives inside it and ``filter_readable`` does not run it. It can
never *contradict* the pick: ``resolve_read_many`` and ``resolve_access`` are
semantically identical for ``read`` (same admin bypass, same
owner-row-is-a-grant derivation, same public fallback), so ``filter_readable``
cannot hand :func:`pick_default` an entry that ``enforce_access`` then 404s. The
EXPLICIT path pays nothing extra. If the hop ever shows up in the perf budget,
thread the batch decision through — do not drop the enforcement.
"""
from __future__ import annotations

from fastapi import HTTPException

from ragstack.api.access import filter_readable
from ragstack.api.collections import CollectionEntry, CollectionRegistry
from ragstack.api.security import Principal
from ragstack.config import settings
from ragstack.tenancy import allowed_collection_ids

#: The 404 a caller with an empty readable set gets. It names no collection —
#: the id was chosen by the SERVER, not by the caller, so echoing it would tell
#: them about a collection they were never shown (api/access.py's
#: no-existence-oracle stance). Byte-identical to the message the allowlist path
#: has always used, and pinned by the tests for #419.
NO_ACCESSIBLE_COLLECTION = "no collection is accessible to this caller"


async def visible_entries(
    registry: CollectionRegistry, principal: Principal
) -> list[CollectionEntry]:
    """The entries ``GET /v1/collections`` lists for this caller, in listing
    (insertion) order: the per-tenant allowlist INTERSECTED with what the
    caller may actually READ.

    ``filter_readable`` is a no-op when auth is unconfigured, so keyless dev is
    unaffected; admin sees everything (the bypass inside ``resolve_access``); an
    ACL-store outage raises 503 rather than silently hiding a readable
    collection."""
    allowed = registry.permitted(
        allowed_collection_ids(principal.tenant, settings.tenant_collections)
    )
    entries = [e for e in registry.entries() if allowed is None or e.id in allowed]
    return await filter_readable(principal, entries)


def pick_default(
    entries: list[CollectionEntry], registry: CollectionRegistry
) -> str | None:
    """The id an omitted ``collection`` targets, given the caller's visible
    ``entries``: the registry pointer when it is among them, else the first of
    them (**insertion** order), else ``None``.

    PURE — no I/O, no principal, no settings. The visibility decision belongs to
    :func:`visible_entries`; this is only the choice among what survived it, so
    the listing and the query path can share it without sharing a round trip."""
    if registry.default_id in {e.id for e in entries}:
        return registry.default_id
    return entries[0].id if entries else None


async def resolve_default_entry(
    registry: CollectionRegistry, principal: Principal
) -> CollectionEntry:
    """The registry entry an omitted ``collection`` resolves to for this caller.

    :func:`visible_entries` + :func:`pick_default`. Raises 404
    :data:`NO_ACCESSIBLE_COLLECTION` when the caller can read nothing at all —
    the same state in which the listing reports ``default: ""`` and an empty
    ``collections`` array.

    Authorization is NOT done here: the caller must still run ``enforce_access``
    on the returned entry, which is what applies the lifecycle gate (see the
    module docstring)."""
    entries = await visible_entries(registry, principal)
    chosen = pick_default(entries, registry)
    if chosen is None:
        raise HTTPException(status_code=404, detail=NO_ACCESSIBLE_COLLECTION)
    return registry.resolve(chosen)
