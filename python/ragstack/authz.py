"""The ONE authorization decision seam — issue #243 design note 1.

Every authorization decision goes through :func:`resolve_access`; routers
never run inline SQL or ad-hoc ownership checks. The signature is the future
ACL sidecar's API, discovered rather than designed. The admin bypass lives
here, as the one explicit *logged* branch (ADR-0003 decision 5) — never an
absent check.

Subject identity convention: the Principal's tenant string IS the subject
(``f"{issuer}:{sub}"`` for bearer identities; the mapped tenant for API
keys). This module takes plain strings and MUST import nothing from
``ragstack.api.*``.

MVP semantics (ADR-0004 / #243):

- ``role == 'admin'`` — allowed for every action, via ``admin-bypass``,
  logged on **every** bypassed request (a superuser override must leave a
  countable, time-ordered audit trail; the listing filter's batch resolver
  logs one summary line per call instead, so a dashboard poll can't flood).
- owner of the collection — allowed for every action.
- ``read`` — any active read/write/owner grant to the subject directly OR to
  the built-in ``public`` group ('public' membership is constant-true; real
  group resolution arrives with #245).
- ``write`` / ``owner`` — owner only (write shares are deferred per the MVP
  cut).
- Store failure — DENY and raise :class:`AuthzUnavailable`; callers map it to
  503. Fail closed, never open (the #196 lesson).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from ragstack.acl_store import (
    GRANTEE_GROUP,
    GRANTEE_USER,
    PERM_OWNER,
    PUBLIC_GROUP,
    AclStore,
)

log = logging.getLogger(__name__)

Action = Literal["read", "write", "owner"]
Via = Literal["owner", "grant", "public", "admin-bypass"]

VALID_ACTIONS = ("read", "write", "owner")


class AuthzUnavailable(RuntimeError):
    """The ACL store could not answer — the decision is DENY (fail closed);
    API callers map this to 503, never to 200."""


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str
    via: Via | None = None


async def resolve_access(
    subject: str,
    role: str,
    collection_id: str,
    action: Action,
    store: AclStore,
) -> AccessDecision:
    """Decide whether ``subject`` (with ``role``) may perform ``action`` on
    ``collection_id``, evaluated against ``store``. See the module docstring
    for the semantics; raises :class:`AuthzUnavailable` when the store cannot
    answer."""
    if action not in VALID_ACTIONS:
        raise ValueError(f"action {action!r} is not one of {VALID_ACTIONS}")

    if role == "admin":
        # ADR-0003 decision 5: the bypass is a decision the code states —
        # explicit and logged — never an absent check. Logged EVERY time: the
        # audit trail must count and time-order repeated admin access, so no
        # per-shape dedup (the batch resolver below covers the listing path
        # with one summary line per call instead).
        log.info(
            "authz admin-bypass: subject=%s action=%s collection=%s",
            subject, action, collection_id,
        )
        return AccessDecision(allowed=True, reason="admin role bypasses ownership",
                              via="admin-bypass")

    try:
        owner = await store.owner_of(collection_id)
    except Exception as e:
        raise AuthzUnavailable(
            f"ACL store unavailable resolving owner of {collection_id!r}"
        ) from e
    if owner is not None and owner == subject:
        return AccessDecision(allowed=True, reason="collection owner", via="owner")

    if action == "read":
        try:
            grants = await store.grants_for_subject(subject)
        except Exception as e:
            raise AuthzUnavailable(
                f"ACL store unavailable resolving grants for {subject!r}"
            ) from e
        mine = [g for g in grants if g.collection_id == collection_id]
        # Any active grant (read/write/owner) implies read. Prefer reporting a
        # direct grant over the public one when both exist.
        direct = next(
            (g for g in mine
             if not (g.grantee_type == GRANTEE_GROUP and g.grantee_id == PUBLIC_GROUP)),
            None,
        )
        if direct is not None:
            return AccessDecision(
                allowed=True, reason=f"active {direct.permission!r} grant", via="grant"
            )
        if mine:
            return AccessDecision(
                allowed=True, reason="collection is shared to 'public'", via="public"
            )
        return AccessDecision(
            allowed=False, reason="no owner row and no active read grant", via=None
        )

    # write/owner: owner only for now — write shares are deferred (MVP cut).
    return AccessDecision(
        allowed=False,
        reason=f"{action!r} requires ownership (write shares are deferred)",
        via=None,
    )


async def resolve_read_many(
    subject: str,
    role: str,
    collection_ids: list[str],
    store: AclStore,
) -> dict[str, AccessDecision]:
    """Batch counterpart of :func:`resolve_access` for ``read`` — same seam,
    same semantics, ONE store round-trip for the whole batch.

    Listing endpoints (``GET /v1/collections``, ``GET /v1/stats/tenants``) must
    decide read for every registry entry; calling :func:`resolve_access` per
    entry costs ``owner_of`` + ``grants_for_subject`` each — 2·N store queries
    for information one ``grants_for_subject`` call already contains (the
    subject's owner rows ARE active grants, and the public rows come back for
    every collection). The admin bypass emits one summary log line per call —
    still every-time, still countable, without N lines per dashboard poll.

    Raises :class:`AuthzUnavailable` when the store cannot answer (the whole
    batch fails closed — a listing that silently dropped a readable collection
    would be a lie in the same family as failing open)."""
    if role == "admin":
        log.info(
            "authz admin-bypass: subject=%s action=read collections=%d (listing)",
            subject, len(collection_ids),
        )
        return {
            cid: AccessDecision(
                allowed=True, reason="admin role bypasses ownership", via="admin-bypass"
            )
            for cid in collection_ids
        }
    try:
        grants = await store.grants_for_subject(subject)
    except Exception as e:
        raise AuthzUnavailable(
            f"ACL store unavailable resolving grants for {subject!r}"
        ) from e
    out: dict[str, AccessDecision] = {}
    for cid in collection_ids:
        mine = [g for g in grants if g.collection_id == cid]
        owner = next(
            (g for g in mine
             if g.grantee_type == GRANTEE_USER and g.grantee_id == subject
             and g.permission == PERM_OWNER),
            None,
        )
        if owner is not None:
            out[cid] = AccessDecision(allowed=True, reason="collection owner", via="owner")
            continue
        direct = next(
            (g for g in mine
             if not (g.grantee_type == GRANTEE_GROUP and g.grantee_id == PUBLIC_GROUP)),
            None,
        )
        if direct is not None:
            out[cid] = AccessDecision(
                allowed=True, reason=f"active {direct.permission!r} grant", via="grant"
            )
        elif mine:
            out[cid] = AccessDecision(
                allowed=True, reason="collection is shared to 'public'", via="public"
            )
        else:
            out[cid] = AccessDecision(
                allowed=False, reason="no owner row and no active read grant", via=None
            )
    return out
