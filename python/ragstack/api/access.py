"""HTTP enforcement of the ONE authorization seam (issue #243 part 2).

Routers call :func:`resolve_access` (:mod:`ragstack.authz`) *through* the helpers
here — never inline SQL or ad-hoc ownership checks. authz.py is sterile (it
imports nothing from ``ragstack.api``); this module owns the HTTP mapping it
deliberately leaves out:

- :class:`~ragstack.authz.AuthzUnavailable` (the store could not answer) → **503**.
  Fail closed — never 200 (the #196 lesson).
- ``read`` denied → **404**, indistinguishable from an unknown id, so a caller
  with no access can't probe collection existence. This matches the tenant
  allowlist's own 404 convention (``query.py`` / ``documents.py``).
- ``write`` / ``owner`` denied → **403** only when the caller can READ the
  collection (it is public or shared to them): "you can't do that" is then
  honest and leaks nothing new. When the caller cannot read it either, the
  denial is the same **404** as the read path — otherwise the write endpoints
  would be an existence oracle for private collections (probe POST /v1/ingest
  with a guessed id: 403 = exists, 404 = doesn't), defeating the read path's
  leak-avoidance.

Read/write enforcement is active only when auth is *configured* (API keys or an
identity provider). Keyless-with-no-identity is the open dev/test path the rest
of the stack already treats as unauthenticated (``resolve_tenant``: "the API is
open"), and production forbids it (``require_durable_backends`` demands
``api_keys``). The owner-gated ``DELETE /v1/collections`` is **always** enforced:
it replaced a route-level ``require_role(admin)`` that already gated keyless
callers, so dropping the gate there would be a regression. The per-chunk
``tenant_id`` filter and the ``TENANT_COLLECTIONS`` allowlist stay in force
underneath, unchanged — defence in depth (ADR-0003 decision 3).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

from ragstack.acl_store import (
    GRANTEE_GROUP,
    GRANTEE_USER,
    PERM_OWNER,
    PERM_READ,
    PUBLIC_GROUP,
    AclStore,
    ShareInvariantError,
    get_acl_store,
)
from ragstack.api.lifecycle import enforce_lifecycle
from ragstack.api.security import Principal
from ragstack.authz import Action, AuthzUnavailable, resolve_access, resolve_read_many
from ragstack.config import settings
from ragstack.identity import get_identity_provider

log = logging.getLogger(__name__)

_DENY_STATUS: dict[str, int] = {"read": 404, "write": 403, "owner": 403}


def auth_configured() -> bool:
    """True when the deployment authenticates callers — API keys are set, or an
    identity provider is on. Keyless-and-no-identity is the open dev/test path;
    read/write ownership enforcement is a no-op there, exactly as tenant auth is
    (``resolve_tenant``)."""
    return bool(settings.api_keys) or get_identity_provider() is not None


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="authorization store unavailable; refusing to serve (fail closed)",
    )


async def _decide(
    principal: Principal, collection_id: str, action: Action, store: AclStore
):
    """resolve_access, mapping the fail-closed store error to a 503."""
    try:
        return await resolve_access(
            principal.tenant, principal.role, collection_id, action, store
        )
    except AuthzUnavailable as e:
        raise _unavailable() from e


async def enforce_access(
    principal: Principal,
    collection_id: str,
    action: Action,
    store: AclStore | None = None,
) -> None:
    """Authorize ``action`` on ``collection_id`` for ``principal`` or raise.

    Raises :class:`HTTPException` — 404 (read denied, or a write/owner denial on
    a collection the caller can't read either), 403 (write/owner denied on a
    readable collection), or 503 (store unavailable). Returns ``None`` on allow.
    Read/write are skipped when auth is unconfigured; ``owner`` is always
    enforced (see the module docstring).

    An ALLOWED ``read``/``write`` then passes the collection LIFECYCLE gate
    (:mod:`ragstack.api.lifecycle`, #358): a ``dormant`` collection triggers
    one restore and answers 503 + ``Retry-After``, ``restoring`` is 503,
    ``lost`` is 409. It runs AFTER authorization on purpose — a lifecycle
    answer for a collection the caller may not read would be an existence
    oracle — and on both allow paths, including the keyless/open one, since
    dormancy is a property of the stores, not of who is asking. ``owner``
    actions (delete, share, transfer, the explicit restore) are never gated:
    managing a dormant collection must not require restoring it."""
    if action != "owner" and not auth_configured():
        await enforce_lifecycle(principal, collection_id, action)
        return
    store = store if store is not None else get_acl_store()
    decision = await _decide(principal, collection_id, action, store)
    if decision.allowed:
        if action != "owner":
            await enforce_lifecycle(principal, collection_id, action)
        return
    if action != "read":
        # A write/owner denial is only reported as 403 when the caller could
        # read the collection anyway — otherwise 403-vs-404 is an existence
        # oracle for private collections (module docstring). With auth
        # unconfigured (the always-enforced `owner` action reaches here in
        # keyless dev) read enforcement is a no-op, i.e. everything is
        # readable — so the denial is the honest 403, never a fake 404.
        readable = (
            not auth_configured()
            or (await _decide(principal, collection_id, "read", store)).allowed
        )
        if readable:
            raise HTTPException(
                status_code=_DENY_STATUS[action],
                detail=f"you do not have {action} access to collection {collection_id!r}",
            )
    raise HTTPException(
        status_code=404,
        detail=f"unknown collection {collection_id!r}; see GET /v1/collections",
    )


async def filter_readable(
    principal: Principal, entries: list[Any], store: AclStore | None = None
) -> list[Any]:
    """Keep only the ``entries`` (anything with an ``.id``) the principal may
    READ. A no-op when auth is unconfigured (the allowlist the caller already
    applied is the only filter in dev). Raises 503 if the store can't answer —
    a listing that silently dropped a readable collection would be a lie in the
    same family as failing open.

    Uses the seam's batch resolver (:func:`~ragstack.authz.resolve_read_many`):
    ONE ``grants_for_subject`` round-trip decides the whole listing, instead of
    ``owner_of`` + an identical grants query per entry (2·N store calls for the
    polled ``GET /v1/collections`` / ``GET /v1/stats/tenants``)."""
    if not auth_configured():
        return list(entries)
    store = store if store is not None else get_acl_store()
    try:
        decisions = await resolve_read_many(
            principal.tenant, principal.role, [e.id for e in entries], store
        )
    except AuthzUnavailable as e:
        raise _unavailable() from e
    return [e for e in entries if decisions[e.id].allowed]


async def write_owner_row(store: AclStore, collection_id: str, owner_subject: str) -> None:
    """Grant ``owner`` of ``collection_id`` to ``owner_subject`` (also its own
    grantor). Called AFTER the registry entry is durably persisted, so the FK-by-
    convention holds.

    Raises rather than swallowing failure — a 201 whose owner row silently never
    landed is what used to leave a durable-but-ownerless collection for the next
    startup backfill to mis-classify. The caller (``POST /v1/collections``) rolls
    the create back and surfaces the error:

    - the id already has an active owner row for a DIFFERENT subject → **409**
      (residual ACL state / a concurrent claim — the id is not this caller's);
      the same subject already owning it is the idempotent success (a concurrent
      startup backfill repairing from the spec-recorded creator got there first);
    - any other store failure → **503** (fail closed, the #196 lesson)."""
    ep = getattr(store, "ensure_provisional", None)
    if ep is not None:
        try:
            # 'local' issuer for API-key / keyless subjects: a bearer subject
            # already got its users row on the auth path; this covers the rest so
            # the shares.granted_by / owner subject resolves to a real users row.
            await ep(owner_subject, "local")
        except Exception:  # noqa: BLE001 — an absent user row is not fatal to the grant
            log.warning(
                "owner-row: ensure_provisional(%s) failed", owner_subject, exc_info=True
            )
    try:
        await store.grant(
            collection_id, GRANTEE_USER, owner_subject, PERM_OWNER,
            granted_by=owner_subject,
        )
        return
    except ShareInvariantError:
        try:
            existing = await store.owner_of(collection_id)
        except Exception as e:  # noqa: BLE001 — can't tell whose row it is → 503
            raise _unavailable() from e
        if existing == owner_subject:
            return  # already owned by the creator (idempotent) — success
        log.warning(
            "owner-row: %r already has an active owner (%s) != creator %s; "
            "refusing the create",
            collection_id, existing, owner_subject,
        )
        # Do not reveal WHO owns the residual state, nor that it belonged to a
        # since-deleted collection — that would turn create into an ownership
        # oracle. The caller only learns the id is unavailable to them.
        raise HTTPException(
            status_code=409,
            detail=(
                f"collection id {collection_id!r} is unavailable; choose a "
                "different id"
            ),
        ) from None
    except Exception as e:  # noqa: BLE001 — store outage: fail the create, closed
        log.warning(
            "owner-row: could not grant owner of %r to %s",
            collection_id, owner_subject, exc_info=True,
        )
        raise _unavailable() from e


async def revoke_collection_acl(
    store: AclStore, collection_id: str, actor: str
) -> int:
    """Soft-revoke EVERY active share of ``collection_id`` (owner row included).

    Run by ``DELETE /v1/collections`` *before* the registry entry goes away: the
    collection-id namespace is reusable, so ACL rows left behind would be
    inherited by whoever mints the same id next — a stale owner row hijacks the
    new collection, a stale ``public read`` row silently publishes it. Raises
    503 (fail closed) if the store can't answer; the delete then aborts with the
    registry entry intact, and a retry is safe (revocation is idempotent).
    Returns the number of rows revoked."""
    try:
        revoked = 0
        # revoke() cascades along granted_by chains, so later roots may already
        # be gone by the time we reach them — re-check via the returned rows
        # rather than assuming one revoke per share.
        for share in await store.shares_for(collection_id):
            revoked += len(await store.revoke(share.id, revoked_by=actor))
        return revoked
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — any store failure: abort the delete
        raise _unavailable() from e


async def _try_grant(
    store: AclStore, collection_id: str, grantee_type: str, grantee_id: str, permission: str
) -> None:
    """Idempotent backfill grant: a duplicate active grant / second owner is the
    concurrency-safe no-op the partial unique indexes turn a race into, not a
    crash. Any other error is logged and skipped so one bad row can't abort boot."""
    try:
        await store.grant(
            collection_id, grantee_type, grantee_id, permission,
            granted_by="system:backfill",
        )
    except ShareInvariantError:
        pass  # already granted (or a concurrent backfill won) — nothing to do
    except Exception:  # noqa: BLE001 — backfill is best-effort per collection
        log.warning(
            "backfill: grant %s:%s %s on %r failed",
            grantee_type, grantee_id, permission, collection_id, exc_info=True,
        )


async def backfill_collection_owners(
    registry: Any, store: AclStore, owner_subject: str
) -> int:
    """Reconcile the ACL rows of every registry collection at startup
    (ADR-0004 decision 4). Idempotent and concurrency-safe — run on every boot.

    The registry entry's spec-recorded creator (``CollectionSpec.owner``,
    surfaced as ``entry.owner``) is the positive marker that decides what an
    *ownerless* collection is — absence of an owner row alone cannot, because a
    collection whose owner-row write was lost (crash window, memory ACL backend
    restart) is also ownerless, and publishing it would be a fail-open:

    - ``entry.owner`` **set** (created via ``POST /v1/collections`` after
      ownership existed): if no owner row was ever written, REPAIR it — grant
      ``owner`` back to the recorded creator. Never granted ``public`` read:
      the collection was private by default and stays private.
    - ``entry.owner`` **empty** (legacy: predates ownership, hand-authored, or
      the settings-derived default entry): it was world-readable before
      ownership existed, so it gets ``owner`` = ``owner_subject`` (the
      configured ``acl_backfill_owner``, reassignable later) and ``read`` to
      ``public``.

    Each grant is keyed on its own row's FULL history (``include_revoked``),
    not on the owner row's presence: a crash between the owner grant and the
    public-read grant is retried on the next boot, while a *deliberately
    revoked* row (un-publishing is revoking the public row; ownership moves by
    transfer) is never resurrected. Returns the number of collections whose
    rows were touched."""
    ep = getattr(store, "ensure_provisional", None)
    if ep is not None:
        try:
            await ep(owner_subject, "system")
        except Exception:  # noqa: BLE001
            log.warning(
                "backfill: ensure_provisional(%s) failed", owner_subject, exc_info=True
            )
    backfilled = 0
    for entry in registry.entries():
        try:
            history = await store.shares_for(entry.id, include_revoked=True)
        except Exception:  # noqa: BLE001 — a store hiccup must not abort startup
            log.warning(
                "backfill: shares_for(%r) failed; skipping", entry.id, exc_info=True
            )
            continue
        ever_owner = any(r.permission == PERM_OWNER for r in history)
        ever_public_read = any(
            r.grantee_type == GRANTEE_GROUP
            and r.grantee_id == PUBLIC_GROUP
            and r.permission == PERM_READ
            for r in history
        )
        touched = False
        creator = getattr(entry, "owner", "") or ""
        if creator:
            # Post-ownership collection: repair a lost owner row to the recorded
            # creator; NEVER publish it.
            if not ever_owner:
                await _try_grant(store, entry.id, GRANTEE_USER, creator, PERM_OWNER)
                log.info(
                    "acl backfill: repaired lost owner row of %r → %s (private)",
                    entry.id, creator,
                )
                touched = True
        else:
            # No recorded creator. Distinguish a genuinely pre-ownership legacy
            # collection from an owned one whose spec.owner was lost: a REAL
            # subject actively owning it (anyone other than the backfill owner)
            # means the collection is owned, not legacy — publishing it would be a
            # fail-open, so leave it entirely alone.
            active_owner = next(
                (r.grantee_id for r in history
                 if r.permission == PERM_OWNER and not r.revoked_at),
                None,
            )
            is_legacy = active_owner in (None, owner_subject)
            if is_legacy:
                # World-readable before ownership existed. Grant the backfill owner
                # if it has none yet, and (re)grant public read unless it was ever
                # granted — the `history`-over-all-rows check never resurrects a
                # deliberately revoked (un-published) grant.
                if not ever_owner:
                    await _try_grant(
                        store, entry.id, GRANTEE_USER, owner_subject, PERM_OWNER
                    )
                    touched = True
                if not ever_public_read:
                    await _try_grant(
                        store, entry.id, GRANTEE_GROUP, PUBLIC_GROUP, PERM_READ
                    )
                    touched = True
        if touched:
            backfilled += 1
    if backfilled:
        log.info(
            "acl backfill: reconciled ACL rows of %d collection(s) (legacy → "
            "owner=%s + public read; spec-owned → repaired private owner)",
            backfilled, owner_subject,
        )
    return backfilled
