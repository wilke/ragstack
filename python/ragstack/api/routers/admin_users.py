"""Admin: grant/revoke the admin role for a federated (bearer) user.

``PATCH /v1/admin/users/{subject}/role``. This is the ONLY in-API way a bearer
identity becomes an admin. The bearer auth path never inherits ``DEFAULT_ROLE``
(which is ``admin`` in production), and nothing that travels with a credential
— an OIDC claim, a BV-BRC token field, the header itself — is an input to the
role decision. A token can never elevate the caller presenting it; a role is
something an existing admin, or the operator's environment, assigned.

**What this surface manages, and what it does not.** It writes the ``role``
column of a ``human`` users row. It does NOT manage:

* ``ADMIN_SUBJECTS`` — the environment allowlist, and the OTHER admin source.
  It is read-only from here by construction: it is evaluated FIRST on the auth
  path and short-circuits, so a stored ``user`` role cannot demote an
  allowlisted subject. That one-directional precedence is what makes the
  allowlist break-glass — it works on an empty users table (which is how the
  first admin exists at all, since this route is itself admin-gated), survives
  a user-store outage, and no database write can revoke it. Removing an entry
  is an operator edit plus a restart, and the response says so via
  ``env_admin``.
* an API-key principal's role — that is ``API_KEY_ROLES`` in the environment.
  A service account is refused with 409: the ``users.role`` column is read only
  on the bearer path, so writing it for a machine identity would be an inert
  grant that blurs the two disjoint subject namespaces (#243).
* the credential itself. Like ``service_accounts.py``, nothing here mints,
  stores or rotates anything a caller authenticates with.

**Admin is a hard superuser, not a UI tier.** ``require_role`` short-circuits on
``admin`` for every gate, and ``authz.resolve_access`` bypasses ownership
entirely with a logged ``admin-bypass`` decision. Granting it hands read/write
over EVERY collection in the deployment, all of ``/v1/admin/*``, ``/v1/config``,
``/v1/health/deep``, the model registry and ``stats.policy``. The route is
deliberately narrow, deliberately audited (``role_set_by``/``role_set_at``, never
blanked), and refuses a last-admin revoke that would be UNRECOVERABLE.

**"Unrecoverable" is measured against every admin source, not just this table.**
The users table is one of three (``security.admin_recovery_sources``): an
``ADMIN_SUBJECTS`` entry the CONFIGURED identity provider can actually produce,
and an API key that maps to ``admin`` via ``API_KEY_ROLES``/``DEFAULT_ROLE``,
both outlive any write here — and an API-key admin is very often the caller
making this exact request. Both are judged on LIVENESS, not merely on being
present in the env: an entry whose issuer half the active provider never emits
can match no token, and an admin key whose tenant is a disabled service account
now 401s. Counting either would stand the guard down on a door that is already
locked. So the refusal fires only when the users table is genuinely the last
source; anywhere else the revoke is legitimate and is allowed. The check itself
runs inside ``set_role``'s transaction, because "is this the last admin" is a
statement about the whole table and a router-side count would be a TOCTOU.

**How a bearer admin is stopped, stated explicitly.** The disabled-account gate
runs only on the API-key path — a federated identity is revoked at its issuer —
and ``_toggle_service_account`` refuses to disable a ``human`` row at all. So
there is no "disable this person" lever here, deliberately: the ways to stop a
bearer admin are (a) revoke the stored role through this route, which lands
within ``ADMIN_ROLE_CACHE_TTL_SECONDS`` per worker, (b) remove the
``ADMIN_SUBJECTS`` entry, which is an env edit plus a restart, or (c) revoke the
identity at the issuer, which removes their access entirely. Supporting a
disable for humans would need the bearer auth path to also read ``rec.enabled``
— a second store dependency on the hot path — and is not part of this surface.

Gating is at include time in ``main.py`` (``dependencies=_admin``), matching the
``/v1/admin`` precedent — no route re-gates itself.

Python-only in v1: Go serves no bearer identity surface.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ragstack.api.security import (
    ROLE_ADMIN,
    Principal,
    admin_recovery_sources,
    admin_subject_allowlist,
    reset_role_cache,
    resolve_principal,
)
from ragstack.user_store import (
    LastAdminError,
    UserInvariantError,
    UserNotFoundError,
    UserRecord,
    UserRoleError,
    get_user_store,
)

log = logging.getLogger(__name__)

router = APIRouter()

#: Same cap the service-account surface applies (``_SUBJECT_MAX``): the value is
#: a users primary key and lands in every log line about this grant.
_SUBJECT_MAX = 128


class UserRoleRequest(BaseModel):
    """PATCH body. ``extra="forbid"`` so a typo'd field is a 422 rather than a
    silent no-op on a privilege change."""

    model_config = ConfigDict(extra="forbid")

    role: str = Field(
        ...,
        description=(
            "'admin' (superuser across the whole deployment) or 'user'. Any "
            "other value is a 400."
        ),
    )


class UserRoleRecord(BaseModel):
    """A user's role state after the change.

    ``role`` is what is STORED; ``env_admin`` is whether ``ADMIN_SUBJECTS`` also
    names this subject. Both are surfaced because they answer different
    questions: ``role`` is what this API can change, ``env_admin`` is what it
    cannot — an allowlisted subject stays admin no matter what ``role`` says.
    """

    subject: str
    role: str
    role_set_by: str
    role_set_at: str
    env_admin: bool


def _record(rec: UserRecord) -> UserRoleRecord:
    return UserRoleRecord(
        subject=rec.subject,
        role=rec.role,
        role_set_by=rec.role_set_by,
        role_set_at=rec.role_set_at,
        env_admin=rec.subject in admin_subject_allowlist(),
    )


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="user store unavailable; refusing to serve (fail closed)",
    )


def _clean_subject(raw: str) -> str:
    """Normalize and validate a BEARER subject, or raise a 400.

    The colon rule here is the INVERSE of the service-account one, for the same
    reason: the two authentication namespaces must stay disjoint. A bearer
    subject is always ``issuer:sub`` with both halves non-empty, so ``':'``,
    ``'bvbrc:'`` and ``':alice'`` are all refused — they name nobody, and a
    colon-free value names an API-key tenant whose role comes from
    ``API_KEY_ROLES``, not from this table.

    Rejecting the shape here keeps a malformed *request* a 400 and leaves 409 to
    mean exactly one thing: a refused state change (a service account, or the
    last-admin lockout).
    """
    subject = raw.strip()
    if not subject:
        raise HTTPException(400, "subject must not be empty or whitespace")
    if len(subject) > _SUBJECT_MAX:
        raise HTTPException(400, f"subject must be at most {_SUBJECT_MAX} characters")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in subject):
        raise HTTPException(400, "subject must not contain control characters")
    issuer, _, sub = subject.partition(":")
    if not issuer or not sub:
        raise HTTPException(
            400,
            f"{subject!r} is not a federated subject: roles are set on bearer "
            "identities, whose subject is 'issuer:sub' with both halves "
            "non-empty (e.g. 'bvbrc:alice'). A colon-free subject is an API-key "
            "tenant, whose role comes from API_KEY_ROLES in the server "
            "environment",
        )
    return subject


@router.patch("/users/{subject}/role", response_model=UserRoleRecord)
async def set_user_role(
    subject: str,
    req: UserRoleRequest,
    principal: Principal = Depends(resolve_principal),
) -> UserRoleRecord:
    """Grant or revoke the admin role for a federated user (admin only).

    400 on an unknown role or a non-federated subject, 404 when the subject has
    no users row (a user who has never authenticated and was never named as a
    grantee — this route deliberately does not mint one: a typo'd issuer prefix
    would otherwise create a permanent admin nobody can authenticate as),
    409 for revoking the last admin of a deployment with no other admin source,
    503 on a store outage. Idempotent: setting the role a user already has
    returns the row unchanged, without re-stamping who last changed it.

    A service account cannot be reached here at all: its subject is colon-free
    by invariant and ``_clean_subject`` requires a colon, so it is a **400**,
    not the 409 the store would raise. ``set_role``'s own service-account
    refusal stays as a data-layer invariant for non-HTTP callers.

    The change lands on the bearer auth path within
    ``ADMIN_ROLE_CACHE_TTL_SECONDS``; this process's cache is flushed here, and
    every sibling worker waits out its own TTL.
    """
    resolved = _clean_subject(subject)
    try:
        store = get_user_store()
    except Exception as e:  # noqa: BLE001 — a store we cannot even build is a 503
        # Inside the try for the same reason the calls below are: constructing
        # the store can fail (a bad DSN, an unreachable Postgres), and the
        # contract documents a store problem as 503, not 500.
        raise _unavailable() from e

    # Last-admin lockout guard, in the spirit of the service-account
    # self-disable 409. The refusal itself lives in the store, where it is
    # decided inside the write's own transaction — the count is a statement
    # about the whole table, so asking it from here would be a TOCTOU: two
    # concurrent revokes would each see the other's admin, both pass, and the
    # deployment would land on zero admins. What this layer decides is only
    # WHETHER TO ASK FOR IT: the guard exists to prevent an unrecoverable
    # lockout, so it stands down whenever an admin source that a users-table
    # write cannot touch would survive the revoke (`admin_recovery_sources` —
    # a usable ADMIN_SUBJECTS entry, or an API key that maps to admin, which is
    # very often the credential making this exact call). A grant is never
    # refused by it, and neither is a re-revoke of somebody who was not admin.
    recovery = await admin_recovery_sources()

    try:
        rec = await store.set_role(
            resolved,
            req.role,
            actor=principal.tenant,
            require_remaining_admin=not recovery,
        )
    except UserRoleError as e:
        # A typo'd role is a malformed REQUEST (400), distinct from a refused
        # state change (409) — the store raises two error types precisely so
        # these do not collapse into one answer. It is validated BEFORE the
        # last-admin check, inside set_role, so an unknown role targeting the
        # last admin is the 400 the contract documents and not a revoke refusal
        # for a revoke nobody asked for.
        raise HTTPException(400, str(e)) from e
    except UserNotFoundError:
        raise HTTPException(404, f"unknown user {resolved!r}") from None
    except LastAdminError as e:
        # Caught before UserInvariantError (its base) to add the remediation the
        # store cannot know: which env sources are missing and what setting each
        # one is.
        raise HTTPException(
            409,
            f"refusing to revoke admin from {resolved!r}: it is the last stored "
            "admin, and this deployment has no admin source outside the users "
            "table — ADMIN_SUBJECTS names no subject this identity provider can "
            "produce, and no API key maps to the admin role — so nobody would be "
            "able to grant the role back through this route. Grant admin to "
            "somebody else first, map an API key to admin with API_KEY_ROLES, or "
            "set ADMIN_SUBJECTS (the break-glass path, an env edit plus a "
            "restart).",
        ) from e
    except UserInvariantError as e:
        raise HTTPException(409, str(e)) from e
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — fail closed
        raise _unavailable() from e

    # Drop this process's memoized role verdicts so the change is not waiting
    # out a TTL here. Siblings still are: the TTL is the demotion lag, and this
    # narrows it rather than removing it.
    reset_role_cache()
    log.info(
        "user %r role set to %r by %r%s",
        rec.subject,
        rec.role,
        principal.tenant,
        (
            " (ADMIN_SUBJECTS also names this subject: still admin)"
            if rec.subject in admin_subject_allowlist() and rec.role != ROLE_ADMIN
            else ""
        ),
    )
    return _record(rec)
