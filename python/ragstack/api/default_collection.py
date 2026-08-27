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

This module is the shared symbol the other sites import, so "who else resolves a
default?" is a one-grep question.

Converged so far: the listing and ``query.py`` (#419); ``documents.py``'s two
**read** paths — ``GET /v1/documents`` and ``DELETE /v1/documents/{doc_id}``,
both implicit-only, so their whole surface moved per caller (#422 PR-2).

Converged too, on the same tie-break but a narrower candidate set: the INGEST
half of ``documents.py`` (#422 PR-3). An implicit ingest cannot use the read
default unchanged — it would route an upload into a collection the caller can
read but not own — so it picks over the caller's **writable** entries
(:func:`writable_entries`) and refuses when none accepts their writes. Same
visibility rule, same order, same ``pick_default``; one extra filter.

Still open: ``confined_collection_name`` (allowlist-only, lexicographic). It
answers a genuinely different question — "which PHYSICAL store do I scope these
triples to" — and has no principal to intersect with, so it is a documented
divergence rather than a missing import.

**The rules, in one place:**

* visibility is **allowlist ∩ readable** — ownership INTERSECTS confinement,
  never replaces it (ADR-0003 decision 3);
* the pick is the registry pointer **when it is visible**, else the first
  visible entry in **insertion order** — the order the listing shows and the
  order the user sees in the picker, so "the first one in your list" is
  literally true on screen (deliberately not ``sorted()``, which is what
  ``_effective_collection`` used; see the PR for #419);
* no visible entry at all is a 404 that names **no id**;
* for INGEST only, the candidates are narrowed to what the caller may WRITE
  (owned, admin, or the legacy shared surface), and "visible but nothing
  writable" is a 403 that also names no id.

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

The implicit INGEST path adds one more batched call (``resolve_write_many``) on
top of that. Ingest spawns a background job and is not latency-critical, so a
combined read+write batch resolver saving the round trip is a deliberate
later option, not a debt.
"""
from __future__ import annotations

from fastapi import HTTPException

from ragstack.api.access import filter_readable, filter_writable
from ragstack.api.collections import CollectionEntry, CollectionRegistry
from ragstack.api.security import Principal
from ragstack.config import settings
from ragstack.tenancy import allowed_collection_ids

#: The 403 an implicit INGEST gets when the caller CAN read collections but
#: none of them accepts their writes (#422). It names no collection id — not
#: even one from the caller's own listing — so it is the same sentence for every
#: caller in that state and can never become an oracle. 403 rather than the
#: read path's 404 because the distinction is actionable: the caller is being
#: told the write RULE, which their own listing plus this message fully explain,
#: not being told a collection does not exist.
NO_WRITABLE_COLLECTION = (
    "no collection accepts your uploads: name a collection you own explicitly "
    "in 'collection', or create your own (POST /v1/collections)"
)

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


async def writable_entries(
    registry: CollectionRegistry, principal: Principal
) -> list[CollectionEntry]:
    """The subset of :func:`visible_entries` an implicit INGEST may land in, in
    the same listing (insertion) order.

    ``visible_entries`` narrowed by :func:`~ragstack.api.access.filter_writable`
    — the caller owns it (or is admin), or it is the legacy shared surface where
    per-chunk ``tenant_id`` stamping is the write isolation. Writability NARROWS
    readability and never widens it: an entry the caller cannot read is not a
    candidate however writable the ACL would say it is, because the caller was
    never shown it.

    ``filter_writable`` no-ops when auth is unconfigured, exactly as
    ``filter_readable`` does, so keyless dev keeps resolving the registry
    pointer."""
    return await filter_writable(principal, await visible_entries(registry, principal))


async def _pick_writable(
    registry: CollectionRegistry, principal: Principal
) -> tuple[CollectionEntry | None, bool]:
    """``(the picked entry or None, whether the caller can see anything)`` —
    one pass, so the refusal branch does not re-run ``visible_entries``."""
    visible = await visible_entries(registry, principal)
    writable = await filter_writable(principal, visible)
    chosen = pick_default(writable, registry)
    return (registry.resolve(chosen) if chosen is not None else None, bool(visible))


async def resolve_ingest_default_entry(
    registry: CollectionRegistry, principal: Principal
) -> CollectionEntry:
    """The registry entry an OMITTED ``collection`` targets on ingest/upload.

    :func:`writable_entries` + :func:`pick_default` — the same tie-break as
    every other default in the codebase, so "the pointer when you can use it,
    else the first one in your listing" stays one rule with one implementation.

    Two refusals, and the difference between them is the point:

    * the caller can read NOTHING → 404 :data:`NO_ACCESSIBLE_COLLECTION`,
      byte-identical to the read paths' refusal (same state, same sentence, and
      the state in which the listing reports ``default: ""``);
    * the caller can read something but nothing accepts their writes → 403
      :data:`NO_WRITABLE_COLLECTION`, which names no id.

    Explicitly named collections must NEVER reach this function. A named id the
    caller cannot write stays 403-if-readable / 404-if-not via ``enforce_access``
    — indistinguishable from today, and no request is ever silently rerouted
    from an id the caller chose to one the server did.

    Authorization is NOT done here. The caller must still run ``enforce_access``
    on the returned entry: that is what applies the collection LIFECYCLE gate,
    which no filter in this module runs. It can never contradict the pick —
    ``resolve_write_many`` and ``resolve_access``'s write branch are the same
    policy in the same file, by construction (see that function's docstring)."""
    entry, sees_anything = await _pick_writable(registry, principal)
    if entry is not None:
        return entry
    # Nothing writable. WHICH refusal depends on whether the caller can see
    # anything at all — that distinction is what makes both answers honest,
    # instead of one blanket 404 telling a caller who simply owns nothing that
    # the collections they can plainly list do not exist.
    if sees_anything:
        raise HTTPException(status_code=403, detail=NO_WRITABLE_COLLECTION)
    raise HTTPException(status_code=404, detail=NO_ACCESSIBLE_COLLECTION)
