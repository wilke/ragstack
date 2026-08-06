"""Durable user-profile store — ADR-0004 decision 1.

The first verified token for a subject upserts a profile row
``users(subject, issuer, email, display_name, first_seen_at, last_seen_at)``.
``subject`` is the tenant string ``f"{issuer}:{sub}"`` — the same key the rest
of the system uses — which makes users enumerable and (later) shares
FK-checkable. A row grants nothing by itself.

The email/identity invariant (``ragstack.identity.oidc`` docstring): email is
profile metadata, reassignable, and must NEVER key the tenant or this table's
primary key. ``email_verified`` is a claim about the mailbox, not the account.

The table also holds **service accounts** (issue #258): machine identities
authenticated by an ``X-API-Key`` secret we mint rather than by a token an
external issuer signed. The two authentication paths produce subjects in
disjoint namespaces — a bearer subject is always ``f"{issuer}:{sub}"``, a
service subject is **colon-free** — so ``kind`` records which one a row belongs
to and :func:`_check_service_account` enforces the colon rule at the data
layer, the same partition the #243 startup guard enforces at the edge. Service
rows are created deliberately by :meth:`UserStore.create_service_account`;
``upsert_seen`` (the first-auth hook) can never mint or reclassify one.

A ``human`` row also carries a ``role``: the RBAC role the BEARER auth path
resolves for that subject, and one of exactly two server-side admin sources
(the other being the ``ADMIN_SUBJECTS`` env allowlist, which is checked first
and cannot be revoked from here). It is written ONLY by
:meth:`UserStore.set_role`, an admin-only call, and is excluded from
``_SEEN_ASSIGN_COLUMNS`` so the login hook that runs on every request is
structurally unable to touch it. Nothing in a credential is an input to it —
a token can never elevate the caller presenting it.

This module follows the :mod:`ragstack.collection_store` /
:mod:`ragstack.jobstore` discipline: one shared-dialect DDL string for sqlite
and postgres, ``TEXT``/``INTEGER`` columns only (no ``JSONB``, no
``TIMESTAMPTZ`` — ISO-8601 UTC strings), and additive-only migration through
the ``ensure_columns_*`` helpers. Three backends, selected by
``user_store_backend``:

``memory`` (default)
    Process-local; nothing persists. Dev/tests, and any deployment that has
    not opted into user enumeration.
``sqlite``
    A durable ``users`` table in ``user_store_path``.
``postgres``
    The same table in ``user_store_dsn`` (falls back to ``postgres_dsn``) —
    the multi-process answer, and the backend ADR-0004's shares will require.

MUST import nothing from ``ragstack.api.*`` — security.py imports this module
(lazily) from the auth path, and a reverse import is a cycle.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from ragstack.collection_store import (
    ensure_columns_postgres,
    ensure_columns_sqlite,
)
from ragstack.config import settings
from ragstack.tenancy import DEFAULT_TENANT, PUBLIC_TENANT

log = logging.getLogger(__name__)

# Backend vocabulary (mirror in config.user_store_backend's comment).
MEMORY = "memory"
SQLITE = "sqlite"
POSTGRES = "postgres"
VALID_USER_STORE_BACKENDS = frozenset({MEMORY, SQLITE, POSTGRES})

# Account-kind vocabulary (mirrored in the DDL contract comment below, #258).
#: A federated identity: subject is ``f"{issuer}:{sub}"``, created by the
#: bearer first-auth hook (``upsert_seen``) or named ahead of it as a grantee.
KIND_HUMAN = "human"
#: A machine identity authenticated by an ``X-API-Key`` secret we mint. Its
#: subject is COLON-FREE by construction, which is what keeps it in a namespace
#: disjoint from every bearer identity (the #243 startup guard rejects an
#: ``api_key_tenants`` value containing ``':'`` when an IdP is enabled). Service
#: rows are created deliberately, never by first-auth.
KIND_SERVICE = "service"
VALID_USER_KINDS = frozenset({KIND_HUMAN, KIND_SERVICE})

#: Subjects that may NEVER be registered as a service account, because they are
#: not one caller's identity: ``default`` is the fallback tenant every valid but
#: unmapped API key resolves to (``security._principal_from_key``) and the whole
#: keyless dev path, and ``public`` is the shared world-readable corpus. A
#: service row on either name turns the auth-path disabled check into a
#: DEPLOYMENT-WIDE kill switch — disabling ``default`` 401s every unmapped key
#: at once, including the admin key needed to call ``/enable``, which makes the
#: lockout unrecoverable through the API (env edit + restart only). Registering
#: one is refused here, at the data layer, so no caller of this store can
#: create that state.
RESERVED_SERVICE_SUBJECTS = frozenset({DEFAULT_TENANT, PUBLIC_TENANT})

# Role vocabulary for the ``role`` column. Spelled here rather than imported
# from ``ragstack.api.security`` because this module MUST NOT import from
# ``ragstack.api.*`` (security.py imports it from the auth path, and the reverse
# import is a cycle) — the two spellings are pinned equal by a test.
#: A superuser across the whole deployment; the only value that elevates.
USER_ROLE_ADMIN = "admin"
#: The plain role. Stored explicitly by a revoke so the audit stamps have
#: something to describe; ``''`` (the column default) means the same thing at
#: the auth path — "never set" is not a third role.
USER_ROLE_USER = "user"
VALID_USER_ROLES = frozenset({USER_ROLE_ADMIN, USER_ROLE_USER})


class UserInvariantError(ValueError):
    """A user-row mutation would violate an account invariant (a colon in a
    service subject, converting a real human row into a service account,
    disabling a non-service row, granting a bearer role to a machine
    credential, ...)."""


class UserRoleError(ValueError):
    """The requested role is not part of the vocabulary.

    Separate from :class:`UserInvariantError` because the two map to different
    HTTP answers: an unknown role is a malformed REQUEST (400 — the caller
    typed ``admn``), while an invariant breach is a refused state change (409).
    Folding them together would make a typo indistinguishable from a
    last-admin lockout at the router.
    """


class LastAdminError(UserInvariantError):
    """This role write would demote the only remaining STORED admin.

    Raised by :meth:`UserStore.set_role` — and only when the caller asked for
    the guard by passing ``require_remaining_admin`` — because it is the one
    refusal that cannot be decided from the target row alone: it depends on the
    rest of the table, so it has to be evaluated in the same transaction as the
    write it vetoes. A router that checked it separately would be a TOCTOU
    (two concurrent revokes each see the other's admin, both pass, the
    deployment lands on zero admins), which is the exact unrecoverable state
    the refusal exists to prevent.

    A subclass of :class:`UserInvariantError` so a caller that only knows the
    coarse "refused state change" (409) vocabulary still maps it correctly;
    catch it FIRST to say something more specific.
    """


class UserNotFoundError(KeyError):
    """The referenced subject has no row."""


class UserRecord(BaseModel):
    """One profile row.

    ``subject`` is the primary key: the tenant string ``f"{issuer}:{sub}"``,
    never an email (emails are reassignable). ``provisional`` marks a row
    created *about* a user (e.g. as a share grantee) before that user's first
    verified login; the first :meth:`UserStore.upsert_seen` flips it off.

    ``kind`` splits the table into the two authentication namespaces (#258):
    ``human`` rows come from a bearer token an external issuer signed, and
    ``service`` rows are locally-minted machine identities authenticated by an
    API key. Every field default here MUST equal the SQL column default
    verbatim — a legacy row widened by ``ensure_columns`` has to read back
    equal to a bare ``UserRecord(subject=..., issuer=...)``.
    """

    subject: str
    issuer: str = ""
    email: str = ""
    display_name: str = ""
    provisional: bool = False  # stored as INTEGER 0/1
    first_seen_at: str = ""  # ISO-8601 UTC; set once, on row creation
    last_seen_at: str = ""  # ISO-8601 UTC; re-stamped on every upsert_seen
    kind: str = KIND_HUMAN  # 'human' | 'service'
    created_by: str = ""  # subject of the admin who minted a service account
    purpose: str = ""  # free text: what this credential is for
    # State and audit are SEPARATE fields (ADR-0004 decision 6 — "the audit trail
    # is the point"). ``disabled`` is the only state; the four stamps below are
    # APPEND-ONLY history of the last event of each kind and are NEVER cleared,
    # so a re-enable cannot erase the fact that a revocation happened, who did
    # it, or who undid it. Folding state into ``disabled_at`` (empty == enabled)
    # is what forced enable to blank the disable stamp.
    disabled: bool = False  # stored as INTEGER 0/1 — THE enabled/disabled state
    disabled_by: str = ""  # subject of the admin who LAST disabled it (kept)
    disabled_at: str = ""  # ISO-8601 UTC of that disable (kept across a re-enable)
    enabled_by: str = ""  # subject of the admin who LAST re-enabled it
    enabled_at: str = ""  # ISO-8601 UTC of that re-enable
    # RBAC role for the BEARER auth path, and one of exactly two server-side
    # admin sources (the other is the ADMIN_SUBJECTS env allowlist). '' is the
    # house "not set" sentinel every other TEXT column uses and reads as
    # ``user``; only the literal 'admin' elevates. This is an IDENTITY-CLASS
    # column: it is carried through unchanged by ``_merge_seen``, excluded from
    # :data:`_SEEN_ASSIGN_COLUMNS`, and therefore structurally unwritable by the
    # first-auth hook that runs on EVERY login. Nothing in a token can set it.
    role: str = ""
    role_set_by: str = ""  # subject of the admin who LAST changed the role
    role_set_at: str = ""  # ISO-8601 UTC of that change (append-only audit)

    @property
    def is_admin(self) -> bool:
        """Does the STORED role elevate? ``''`` and ``'user'`` do not.

        The auth path ORs this with the ADMIN_SUBJECTS allowlist and never
        consults ``settings.default_role``; see
        ``security._principal_from_bearer``.
        """
        return self.role == USER_ROLE_ADMIN

    @property
    def enabled(self) -> bool:
        """Soft-delete flag, mirroring ``ShareRecord.active``.

        Read from the ``disabled`` state field, never from ``disabled_at`` —
        that stamp survives a re-enable as audit history.

        ADVISORY as of #258 part 1: nothing on the auth path reads it yet
        (``_schedule_profile_upsert`` is fire-and-forget and the ACL joins
        never touch ``users``), so a disabled row still authenticates. Wiring
        it is a deliberate change to the auth hot path's cost profile.
        """
        return not self.disabled

    @property
    def is_service(self) -> bool:
        return self.kind == KIND_SERVICE


def _now() -> str:
    return datetime.now(UTC).isoformat()


@runtime_checkable
class UserStore(Protocol):
    """Durable mapping ``subject -> profile row`` (ADR-0004 decision 1)."""

    async def upsert_seen(
        self, subject: str, issuer: str, email: str = "", display_name: str = ""
    ) -> UserRecord:
        """Record a verified authentication for ``subject``.

        Creates the row when absent; always re-stamps ``last_seen_at``; sets
        ``first_seen_at`` only on create; flips ``provisional`` to ``False``;
        and fills ``email``/``display_name`` only with non-empty values —
        a token that carries no profile claims never blanks out data a
        previous one provided. Idempotent by design: with the identity cache
        in front, this fires once per cache expiry per process, not once per
        session.
        """
        ...

    async def ensure_provisional(self, subject: str, issuer: str) -> UserRecord:
        """Create a ``provisional=True`` row iff ``subject`` is absent.

        Returns the existing row *unchanged* otherwise — this is the "name a
        grantee before their first login" path and must never demote a real
        profile back to provisional or touch its timestamps.
        """
        ...

    async def create_service_account(
        self, subject: str, created_by: str, purpose: str = ""
    ) -> UserRecord:
        """Register a machine identity (#258 scope item 1).

        ``subject`` MUST be colon-free: a bearer subject is always
        ``f"{issuer}:{sub}"``, so the colon rule is what makes a service
        account structurally unable to collide with — or be impersonated by —
        a federated identity. This is the data-layer expression of the #243
        startup guard.

        ``subject`` must also not be one of :data:`RESERVED_SERVICE_SUBJECTS`
        (``default``/``public``) — those name shared fallback tenants rather
        than one caller, and a disable on either is a deployment-wide lockout.

        Returns ``kind='service', provisional=False``. Idempotent-ish:

        * subject absent -> created;
        * subject already a service row -> returned **unchanged** (so a
          re-run of a provisioning script is a no-op, and neither
          ``purpose`` nor a ``disabled`` state somebody set is silently
          rewritten);
        * subject is a ``provisional=True`` row nobody has ever authenticated
          as (``last_seen_at == ''``, i.e. an ``ensure_provisional``
          placeholder left by naming it as a share grantee or group member)
          -> **upgraded** in place, keeping ``first_seen_at``;
        * subject is any other human row -> :class:`UserInvariantError`.
          Converting a real person's row into a machine credential is a
          privilege event and must never happen implicitly.
        """
        ...

    async def disable_service_account(self, subject: str, actor: str) -> UserRecord:
        """Soft-disable a service account: set ``disabled`` and stamp
        ``disabled_by``/``disabled_at``.

        Never deletes a row (ADR-0004 decision 6 — the audit trail is the
        point). Raises :class:`UserNotFoundError` when absent and
        :class:`UserInvariantError` for a ``human`` row. Idempotent: an
        already-disabled account is returned unchanged (the stamp is not
        re-written).

        ADVISORY as of #258 part 1 — see :attr:`UserRecord.enabled`.
        """
        ...

    async def enable_service_account(self, subject: str, actor: str) -> UserRecord:
        """Clear ``disabled`` and stamp ``enabled_by``/``enabled_at``. Inverse of
        :meth:`disable_service_account`, same errors, also idempotent.

        ``disabled_by``/``disabled_at`` are **kept**: they are the record of the
        last revocation, and a re-enable that blanked them would leave a row
        byte-identical to one nobody ever disabled. The two pairs together are
        the audit trail ADR-0004 decision 6 asks for — who stopped this
        credential, when, and who put it back."""
        ...

    async def set_role(
        self,
        subject: str,
        role: str,
        actor: str,
        *,
        require_remaining_admin: bool = False,
    ) -> UserRecord:
        """Grant or revoke the bearer RBAC role for ``subject``.

        The ONLY writer of the ``role`` column. ``role`` must be ``admin`` or
        ``user`` (:class:`UserRoleError` otherwise, so a typo stays
        distinguishable from a refused state change), the row must exist
        (:class:`UserNotFoundError`), and it must be a ``human`` row
        (:class:`UserInvariantError` for a service account: an API-key
        principal's role comes from ``API_KEY_ROLES``, and writing it here
        would dissolve the disjoint-namespace invariant).

        ``require_remaining_admin`` adds the last-admin refusal
        (:class:`LastAdminError`): a demotion that would leave the table with
        zero stored admins is rolled back. It lives HERE, not in the caller,
        because it is a statement about the whole table and must be evaluated
        under the same lock/transaction as the write — every implementation
        below therefore serializes role writes against each other. The caller
        passes it only when no environment admin source (``ADMIN_SUBJECTS``, an
        admin ``API_KEY_ROLES`` mapping) would survive the write; the role
        vocabulary is validated BEFORE it, so a typo'd role stays a
        :class:`UserRoleError`.

        Stamps ``role_set_by``/``role_set_at`` — append-only audit, in the
        ADR-0004 decision 6 vocabulary: a revoke records who revoked, it never
        blanks who granted. Idempotent: setting the role a row already has
        returns it unchanged rather than re-stamping, so "who last CHANGED
        this" stays true.

        Note what this method cannot do: it cannot take away an ADMIN_SUBJECTS
        entry. The env allowlist is checked first on the auth path and
        short-circuits, so a stored ``user`` role does not demote an
        allowlisted subject — that is the point of a break-glass path.
        """
        ...

    async def count_admins(self) -> int:
        """How many rows STORE ``role='admin'`` (the env allowlist is not
        counted — it is not in this table).

        A plain observation, safe to serve from outside a transaction: it
        reports how many stored admins a deployment has. It is NOT what decides
        the last-admin refusal — that count has to be taken inside the write's
        own transaction, so ``set_role(..., require_remaining_admin=True)``
        takes its own.
        """
        ...

    async def get(self, subject: str) -> UserRecord | None: ...

    async def list_users(self, limit: int = 100) -> list[UserRecord]:
        """Known users, oldest-first (stable ``first_seen_at`` order)."""
        ...

    async def list_service_accounts(
        self, created_by: str = "", limit: int = 100
    ) -> list[UserRecord]:
        """``kind='service'`` rows only, oldest-first; optionally narrowed to
        the ones ``created_by`` minted. Disabled accounts are included — they
        are soft state, not deletions."""
        ...

    async def close(self) -> None: ...


def _merge_seen(
    prior: UserRecord | None,
    subject: str,
    issuer: str,
    email: str,
    display_name: str,
) -> UserRecord:
    """The one place upsert_seen's semantics live — every backend goes through
    it, so "never blank out existing data" cannot drift between dialects.

    It is also where the human/service distinction could be lost, so the
    identity-class columns (``kind``/``created_by``/``purpose``/``disabled_*``/
    ``role*``) are carried through UNCHANGED on the update branch and defaulted
    only on create. ``role`` matters most of all here: this helper runs on
    EVERY login, so blanking it would silently demote an admin the next time
    they signed in. The SQL backends narrow their ON CONFLICT assignment list to
    :data:`_SEEN_ASSIGN_COLUMNS` so the invariant holds even if this helper is
    wrong — belt and braces, because the read-then-merge is not atomic.
    """
    stamp = _now()
    if prior is None:
        return UserRecord(
            subject=subject, issuer=issuer, email=email,
            display_name=display_name, provisional=False,
            first_seen_at=stamp, last_seen_at=stamp,
            kind=KIND_HUMAN, created_by="", purpose="",
            disabled=False, disabled_by="", disabled_at="",
            enabled_by="", enabled_at="",
            role="", role_set_by="", role_set_at="",
        )
    return UserRecord(
        subject=subject,
        issuer=issuer or prior.issuer,
        email=email or prior.email,
        display_name=display_name or prior.display_name,
        provisional=False,
        first_seen_at=prior.first_seen_at or stamp,
        last_seen_at=stamp,
        # Never re-classify an existing row. ``or KIND_HUMAN`` covers a row
        # widened from a legacy table where the column could read ''.
        kind=prior.kind or KIND_HUMAN,
        created_by=prior.created_by,
        purpose=prior.purpose,
        disabled=prior.disabled,
        disabled_by=prior.disabled_by,
        disabled_at=prior.disabled_at,
        enabled_by=prior.enabled_by,
        enabled_at=prior.enabled_at,
        # A login is not a role event. Carrying these through is what makes
        # "signing in does not demote an admin" true even before the narrowed
        # ON CONFLICT list makes it structurally true.
        role=prior.role,
        role_set_by=prior.role_set_by,
        role_set_at=prior.role_set_at,
    )


def _new_service_account(subject: str, created_by: str, purpose: str) -> UserRecord:
    """A fresh service row. ``issuer`` stays '' — we are the issuer, and the
    subject carries no issuer prefix by the colon rule."""
    return UserRecord(
        subject=subject,
        issuer="",
        provisional=False,
        first_seen_at=_now(),
        last_seen_at="",  # never authenticated yet
        kind=KIND_SERVICE,
        created_by=created_by,
        purpose=purpose,
    )


def _check_service_account(prior: UserRecord | None, rec: UserRecord) -> None:
    """The one place create_service_account's invariants live (#258).

    Raises :class:`UserInvariantError` on a malformed request or on a prior row
    that must not be reclassified. Returning normally means "write ``rec``",
    EXCEPT when ``prior`` is already a service row — callers return that row
    unchanged rather than rewriting it (see the Protocol docstring).
    """
    if not rec.subject:
        raise UserInvariantError("subject must be non-empty")
    if ":" in rec.subject:
        raise UserInvariantError(
            f"service subject {rec.subject!r} must be colon-free: ':' is reserved "
            "for federated 'issuer:sub' identities, and the two namespaces must "
            "stay disjoint (issue #243 startup guard)"
        )
    if any(c in rec.subject for c in "/\\?#%") or rec.subject in (".", ".."):
        # The subject is a PATH SEGMENT on /v1/admin/service-accounts/{subject}/
        # disable. A '/' (even percent-encoded — Starlette decodes before routing)
        # makes that route 404, so the account registers fine and can then NEVER
        # be revoked through the API: the one operation it exists for. Refuse the
        # characters rather than ship an account with a dead off-switch.
        raise UserInvariantError(
            f"service subject {rec.subject!r} must not contain any of / \\ ? # % "
            "or be '.'/'..': the subject is a URL path segment on the disable "
            "route, and these make it unaddressable — the account would be "
            "unrevocable through the API"
        )
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in rec.subject):
        # Lands in the users primary key and is echoed by GET; NUL additionally
        # diverges by backend (asyncpg rejects it, sqlite/memory accept).
        raise UserInvariantError(
            f"service subject {rec.subject!r} must not contain control characters"
        )
    if rec.subject in RESERVED_SERVICE_SUBJECTS:
        raise UserInvariantError(
            f"{rec.subject!r} is a reserved tenant, not one caller's identity: "
            f"{sorted(RESERVED_SERVICE_SUBJECTS)} are the shared fallback/public "
            "tenants that unmapped API keys and the keyless path resolve to, so a "
            "service row on one would let a single disable lock out every such "
            "caller at once (including the admin key that would undo it)"
        )
    if not rec.created_by:
        raise UserInvariantError("created_by must be non-empty")
    if rec.kind not in VALID_USER_KINDS:
        raise UserInvariantError(
            f"kind {rec.kind!r} is not one of {sorted(VALID_USER_KINDS)}"
        )
    if prior is None or prior.kind == KIND_SERVICE:
        return
    if prior.role:
        # A never-authenticated placeholder that an admin has already given a
        # ROLE to is not an empty name any more — it is a pending privilege
        # grant. Silently converting it into a machine credential would carry
        # that role onto an API-key identity through a door the role surface
        # deliberately refuses (set_role rejects service rows). Revoke the role
        # first, deliberately, and the upgrade below becomes available again.
        raise UserInvariantError(
            f"{rec.subject!r} carries the stored role {prior.role!r}; revoke it "
            "before converting the row into a service account — a machine "
            "credential must never inherit a bearer role grant"
        )
    if prior.provisional and not prior.last_seen_at:
        # An ensure_provisional placeholder (named as a grantee/member before
        # the account existed). Nobody has ever authenticated as it, so
        # claiming it is a creation, not a conversion.
        return
    raise UserInvariantError(
        f"{rec.subject!r} already exists as a {prior.kind!r} account; converting a "
        "real user row into a service account is a privilege event and is refused"
    )


def _toggle_service_account(
    prior: UserRecord | None, subject: str, actor: str, disable: bool
) -> UserRecord:
    """Shared disable/enable semantics. Returns the row to persist — which is
    ``prior`` itself when the requested state already holds (idempotent).

    Both directions are ADDITIVE: the ``disabled`` state flips and the acting
    subject/time is stamped into that direction's pair. Neither direction clears
    the other's stamp, so a disable→enable cycle leaves a row that still says a
    revocation happened, who performed it, and who reversed it (ADR-0004
    decision 6). Only the *last* event of each kind is kept — this table is a
    row per subject, not an event log — but "the last enable erased the last
    disable" is exactly the loss the separation prevents.
    """
    if not actor:
        raise UserInvariantError("actor must be non-empty")
    if prior is None:
        raise UserNotFoundError(subject)
    if prior.kind != KIND_SERVICE:
        raise UserInvariantError(
            f"{subject!r} is a {prior.kind!r} account; only service accounts "
            "can be disabled/enabled here"
        )
    if disable == (not prior.enabled):
        return prior
    if disable:
        return prior.model_copy(
            update={"disabled": True, "disabled_by": actor, "disabled_at": _now()}
        )
    return prior.model_copy(
        update={"disabled": False, "enabled_by": actor, "enabled_at": _now()}
    )


def _apply_role(prior: UserRecord | None, subject: str, role: str, actor: str) -> UserRecord:
    """The one place ``set_role``'s semantics live — every backend calls it, so
    the rules that decide who is a superuser cannot drift between the memory,
    sqlite and postgres implementations.

    Returns the row to persist, which is ``prior`` itself when the requested
    role already holds (idempotent, and deliberately NOT re-stamped: the audit
    pair answers "who last changed this", and a no-op re-grant overwriting it
    would erase the real granter).

    Refusals, and the concrete failure each prevents:

    * unknown ``role`` -> :class:`UserRoleError`, so a typo cannot land a value
      that is neither ``admin`` nor ``user`` in the column and then read as
      "not admin" forever while the operator believes they granted something.
    * blank ``actor`` -> :class:`UserInvariantError`: an unattributed privilege
      grant is exactly the record ADR-0004 decision 6 exists to prevent.
    * missing row -> :class:`UserNotFoundError`, never a create. Minting a row
      from a subject string would let a typo'd issuer prefix create a
      permanent admin nobody can authenticate as — and hide the real mistake.
    * ``service`` row -> :class:`UserInvariantError`. An API-key principal's
      role comes from ``API_KEY_ROLES``; this column is read only on the bearer
      path. Writing it for a machine identity would be an inert grant at best,
      and at worst the seam through which the two disjoint namespaces (#243)
      start sharing an authorization concept.
    """
    if role not in VALID_USER_ROLES:
        raise UserRoleError(
            f"role {role!r} is not one of {sorted(VALID_USER_ROLES)}"
        )
    if not actor:
        raise UserInvariantError("actor must be non-empty")
    if prior is None:
        raise UserNotFoundError(subject)
    if prior.kind == KIND_SERVICE:
        raise UserInvariantError(
            f"{subject!r} is a service account; its role comes from API_KEY_ROLES "
            "in the server environment, and the users.role column is read only on "
            "the bearer path — granting it here would be inert and would blur the "
            "two authentication namespaces"
        )
    # '' and 'user' are the same state; a row that has never been touched must
    # not be re-stamped by a revoke that changes nothing.
    if (prior.role or USER_ROLE_USER) == role:
        return prior
    return prior.model_copy(
        update={"role": role, "role_set_by": actor, "role_set_at": _now()}
    )


def _is_demotion(prior: UserRecord | None, rec: UserRecord) -> bool:
    """Does this write actually take admin AWAY from a stored admin?

    The last-admin guard is scoped to exactly this: a grant cannot strand the
    deployment, an idempotent re-grant returns ``prior`` unchanged (so both
    sides are admin), and a re-revoke of somebody who was never admin is not a
    demotion — none of those may be refused.
    """
    return prior is not None and prior.is_admin and not rec.is_admin


def _last_admin_error(subject: str) -> LastAdminError:
    """The store-level half of the refusal: WHAT was refused, not what to do
    about it. The remediation depends on settings this module deliberately
    cannot see (``ADMIN_SUBJECTS``, ``API_KEY_ROLES``), so the router adds it."""
    return LastAdminError(
        f"{subject!r} is the last stored admin; demoting it would leave the "
        "users table with none"
    )


class InMemoryUserStore:
    """Process-local profile table. Loses everything on restart — dev/tests."""

    def __init__(self) -> None:
        self._records: dict[str, UserRecord] = {}
        self._lock = asyncio.Lock()

    async def upsert_seen(
        self, subject: str, issuer: str, email: str = "", display_name: str = ""
    ) -> UserRecord:
        async with self._lock:
            rec = _merge_seen(self._records.get(subject), subject, issuer, email, display_name)
            self._records[subject] = rec
            return rec.model_copy(deep=True)

    async def ensure_provisional(self, subject: str, issuer: str) -> UserRecord:
        async with self._lock:
            existing = self._records.get(subject)
            if existing is not None:
                return existing.model_copy(deep=True)
            rec = UserRecord(
                subject=subject, issuer=issuer, provisional=True, first_seen_at=_now()
            )
            self._records[subject] = rec
            return rec.model_copy(deep=True)

    async def create_service_account(
        self, subject: str, created_by: str, purpose: str = ""
    ) -> UserRecord:
        # Exactly one lock acquisition, and no other locking store method is
        # called while holding it — the subclasses share this lock and it is
        # not re-entrant (group_store.py's add_member note).
        async with self._lock:
            prior = self._records.get(subject)
            rec = _new_service_account(subject, created_by, purpose)
            _check_service_account(prior, rec)
            if prior is not None:
                if prior.kind == KIND_SERVICE:
                    return prior.model_copy(deep=True)
                # Upgrade the never-authenticated placeholder in place.
                rec = rec.model_copy(
                    update={
                        "issuer": prior.issuer,
                        "first_seen_at": prior.first_seen_at or rec.first_seen_at,
                    }
                )
            self._records[subject] = rec
            return rec.model_copy(deep=True)

    async def _set_disabled(self, subject: str, actor: str, disable: bool) -> UserRecord:
        async with self._lock:
            rec = _toggle_service_account(self._records.get(subject), subject, actor, disable)
            self._records[subject] = rec
            return rec.model_copy(deep=True)

    async def disable_service_account(self, subject: str, actor: str) -> UserRecord:
        return await self._set_disabled(subject, actor, True)

    async def enable_service_account(self, subject: str, actor: str) -> UserRecord:
        return await self._set_disabled(subject, actor, False)

    async def set_role(
        self,
        subject: str,
        role: str,
        actor: str,
        *,
        require_remaining_admin: bool = False,
    ) -> UserRecord:
        # The lock is this backend's transaction: validate, count the OTHER
        # admins and write without yielding, so two concurrent revokes cannot
        # both observe the other's admin.
        async with self._lock:
            prior = self._records.get(subject)
            rec = _apply_role(prior, subject, role, actor)
            if require_remaining_admin and _is_demotion(prior, rec):
                others = sum(
                    1 for s, r in self._records.items() if s != subject and r.is_admin
                )
                if others == 0:
                    raise _last_admin_error(subject)
            self._records[subject] = rec
            return rec.model_copy(deep=True)

    async def count_admins(self) -> int:
        async with self._lock:
            return sum(1 for r in self._records.values() if r.is_admin)

    async def get(self, subject: str) -> UserRecord | None:
        async with self._lock:
            rec = self._records.get(subject)
            return rec.model_copy(deep=True) if rec is not None else None

    async def list_users(self, limit: int = 100) -> list[UserRecord]:
        async with self._lock:
            rows = sorted(
                self._records.values(), key=lambda r: (r.first_seen_at, r.subject)
            )
            return [r.model_copy(deep=True) for r in rows[:limit]]

    async def list_service_accounts(
        self, created_by: str = "", limit: int = 100
    ) -> list[UserRecord]:
        async with self._lock:
            rows = sorted(
                (
                    r for r in self._records.values()
                    if r.kind == KIND_SERVICE
                    and (not created_by or r.created_by == created_by)
                ),
                key=lambda r: (r.first_seen_at, r.subject),
            )
            return [r.model_copy(deep=True) for r in rows[:limit]]

    async def close(self) -> None:
        """No resources to release."""


# --------------------------------------------------------------------------- #
# SQL backends
# --------------------------------------------------------------------------- #

# CONTRACT — this DDL is the published cross-consumer schema for user profiles
# and service accounts. Any other consumer (e.g. the Go implementation) codes
# against exactly this shape. Changes are ADDITIVE ONLY, via _USERS_COLUMNS +
# ensure_columns_* — never a column rename, retype, or drop, and never a DELETE
# on rows (disabling is soft: the `disabled` flag carries the state and
# disabled_by/at + enabled_by/at are append-only audit stamps that a re-enable
# must NOT clear, ADR-0004 decision 6).
# Shared verbatim by the sqlite and postgres stores — collection_store.py's
# discipline. TEXT for strings/timestamps (ISO-8601 UTC text, no TIMESTAMPTZ),
# INTEGER 0/1 for the provisional flag, '' for "not set / not disabled".
# Vocabulary: kind in ('human','service') — 'human' subjects are 'issuer:sub'
# strings from a verified bearer token; 'service' subjects are colon-free
# locally-minted machine identities (issue #258). The two namespaces are
# disjoint by that rule, and the #243 startup guard enforces it at the edge.
# role in ('', 'admin', 'user') — the RBAC role of a 'human' row on the BEARER
# auth path, written only by an admin through set_role and never by a login.
# '' is "never set" and reads as 'user'; only the literal 'admin' elevates, and
# it grants superuser across the whole deployment. role_set_by/role_set_at are
# the append-only audit of the last change (ADR-0004 decision 6), never cleared.
# A 'service' row's role is always '': an API-key principal's role comes from
# API_KEY_ROLES in the environment, not from this table.
_USERS_DDL = (
    "CREATE TABLE IF NOT EXISTS users ("
    "  subject TEXT PRIMARY KEY,"
    "  issuer TEXT NOT NULL DEFAULT '',"
    "  email TEXT NOT NULL DEFAULT '',"
    "  display_name TEXT NOT NULL DEFAULT '',"
    "  provisional INTEGER NOT NULL DEFAULT 0,"
    "  first_seen_at TEXT NOT NULL DEFAULT '',"
    "  last_seen_at TEXT NOT NULL DEFAULT '',"
    "  kind TEXT NOT NULL DEFAULT 'human',"
    "  created_by TEXT NOT NULL DEFAULT '',"
    "  purpose TEXT NOT NULL DEFAULT '',"
    "  disabled INTEGER NOT NULL DEFAULT 0,"
    "  disabled_by TEXT NOT NULL DEFAULT '',"
    "  disabled_at TEXT NOT NULL DEFAULT '',"
    "  enabled_by TEXT NOT NULL DEFAULT '',"
    "  enabled_at TEXT NOT NULL DEFAULT '',"
    "  role TEXT NOT NULL DEFAULT '',"
    "  role_set_by TEXT NOT NULL DEFAULT '',"
    "  role_set_at TEXT NOT NULL DEFAULT ''"
    ")"
)

# Advisory-lock key serializing the users DDL across processes. Postgres's
# CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS are racy under
# concurrency (the loser can die on pg_class's own unique index, and ADD COLUMN
# takes ACCESS EXCLUSIVE), and every worker runs this on boot — so without it a
# rolling restart can fail one startup nondeterministically. Mirrors
# _SHARES_DDL_LOCK_KEY / _GROUPS_DDL_LOCK_KEY; any stable 64-bit constant works.
_USERS_DDL_LOCK_KEY = 0x7261675F75736572  # b"rag_user" as an int64

# Column -> DDL fragment for additive migration of a table created by an older
# build. Every entry MUST be nullable or defaulted (sqlite's ALTER TABLE ADD
# COLUMN forbids NOT NULL without a default) — which is also what backfills
# every pre-existing row to kind='human', the safe default.
_USERS_COLUMNS: dict[str, str] = {
    "issuer": "TEXT NOT NULL DEFAULT ''",
    "email": "TEXT NOT NULL DEFAULT ''",
    "display_name": "TEXT NOT NULL DEFAULT ''",
    "provisional": "INTEGER NOT NULL DEFAULT 0",
    "first_seen_at": "TEXT NOT NULL DEFAULT ''",
    "last_seen_at": "TEXT NOT NULL DEFAULT ''",
    "kind": "TEXT NOT NULL DEFAULT 'human'",
    "created_by": "TEXT NOT NULL DEFAULT ''",
    "purpose": "TEXT NOT NULL DEFAULT ''",
    "disabled": "INTEGER NOT NULL DEFAULT 0",
    "disabled_by": "TEXT NOT NULL DEFAULT ''",
    "disabled_at": "TEXT NOT NULL DEFAULT ''",
    "enabled_by": "TEXT NOT NULL DEFAULT ''",
    "enabled_at": "TEXT NOT NULL DEFAULT ''",
    # A row widened from a legacy table backfills to role='' — i.e. NOT admin.
    # Any other default would mean an upgrade silently elevated everyone.
    "role": "TEXT NOT NULL DEFAULT ''",
    "role_set_by": "TEXT NOT NULL DEFAULT ''",
    "role_set_at": "TEXT NOT NULL DEFAULT ''",
}

# Positional and arity-strict: _COLUMNS, _record_to_row and _row_to_record are
# three parallel lists. Append only, and always to all three at once.
_COLUMNS = (
    "subject", "issuer", "email", "display_name",
    "provisional", "first_seen_at", "last_seen_at",
    "kind", "created_by", "purpose",
    "disabled", "disabled_by", "disabled_at", "enabled_by", "enabled_at",
    "role", "role_set_by", "role_set_at",
)

# The ONLY columns upsert_seen's ON CONFLICT clause may assign. Narrowing it
# (rather than "everything but subject") makes the first-auth hook
# STRUCTURALLY incapable of writing kind/created_by/purpose/disabled_*/role* in
# either dialect — a DB-level invariant that does not depend on _merge_seen
# being right, and that survives the non-atomic read-then-merge window.
#
# ``role`` is the entry that must never be added here. _upsert_profile is
# fire-and-forget and debounced 300 s per subject, so a role in this list would
# blank an admin's grant within minutes of any login, in both dialects, with no
# error raised anywhere — the failure would look like "admin randomly stopped
# working".
_SEEN_ASSIGN_COLUMNS = (
    "issuer", "email", "display_name", "provisional", "first_seen_at", "last_seen_at",
)

# The identity-class columns, written only by the explicit admin calls
# (create/disable/enable). Never touched by upsert_seen or ensure_provisional.
# ``role*`` is here so that upgrading a never-authenticated placeholder into a
# service account WRITES the fresh row's empty role rather than leaving whatever
# the placeholder had; _check_service_account already refuses a role-bearing
# placeholder, so this is the belt to that braces.
_SERVICE_SET_COLUMNS = (
    "issuer", "provisional", "first_seen_at",
    "kind", "created_by", "purpose",
    "disabled", "disabled_by", "disabled_at", "enabled_by", "enabled_at",
    "role", "role_set_by", "role_set_at",
)

# The narrow UPDATE for set_role: the role writer touches these three columns
# and nothing else, so a role grant can never disturb profile or account state.
_ROLE_SET_COLUMNS = ("role", "role_set_by", "role_set_at")
_ROLE_UPDATE_SQLITE = (
    "UPDATE users SET "
    + ", ".join(f"{c} = ?" for c in _ROLE_SET_COLUMNS)
    + " WHERE subject = ?"
)
_ROLE_UPDATE_POSTGRES = (
    "UPDATE users SET "
    + ", ".join(f"{c} = ${i + 1}" for i, c in enumerate(_ROLE_SET_COLUMNS))
    + f" WHERE subject = ${len(_ROLE_SET_COLUMNS) + 1}"
)
_COUNT_ADMINS_SQLITE = "SELECT COUNT(*) FROM users WHERE role = ?"
_COUNT_ADMINS_POSTGRES = "SELECT COUNT(*) FROM users WHERE role = $1"
# The last-admin guard's count: admins OTHER than the row being written. Asked
# before the UPDATE (and inside its transaction), so it needs no assumption
# about whether the write has landed yet.
_COUNT_OTHER_ADMINS_SQLITE = "SELECT COUNT(*) FROM users WHERE role = ? AND subject <> ?"
_COUNT_OTHER_ADMINS_POSTGRES = (
    "SELECT COUNT(*) FROM users WHERE role = $1 AND subject <> $2"
)
#: Postgres advisory-lock key serializing role writes (see
#: ``PostgresUserStore.set_role``). An arbitrary constant — 'role' as bytes —
#: shared by every worker against one database, which is the point.
_ROLE_WRITE_LOCK = 0x726F6C65
_SERVICE_UPDATE_SQLITE = (
    "UPDATE users SET "
    + ", ".join(f"{c} = ?" for c in _SERVICE_SET_COLUMNS)
    + " WHERE subject = ?"
)
_SERVICE_UPDATE_POSTGRES = (
    "UPDATE users SET "
    + ", ".join(f"{c} = ${i + 1}" for i, c in enumerate(_SERVICE_SET_COLUMNS))
    + f" WHERE subject = ${len(_SERVICE_SET_COLUMNS) + 1}"
)

_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM users"
# Oldest-first: first_seen_at ascends with registration, subject breaks ties.
_ORDER = " ORDER BY first_seen_at, subject"


def _record_to_row(rec: UserRecord) -> tuple:
    return (
        rec.subject, rec.issuer, rec.email, rec.display_name,
        1 if rec.provisional else 0, rec.first_seen_at, rec.last_seen_at,
        rec.kind, rec.created_by, rec.purpose,
        1 if rec.disabled else 0, rec.disabled_by, rec.disabled_at,
        rec.enabled_by, rec.enabled_at,
        rec.role, rec.role_set_by, rec.role_set_at,
    )


def _service_update_args(rec: UserRecord) -> tuple:
    """Positional args for _SERVICE_UPDATE_* — same order as
    _SERVICE_SET_COLUMNS, with ``subject`` last for the WHERE clause."""
    return (
        rec.issuer, 1 if rec.provisional else 0, rec.first_seen_at,
        rec.kind, rec.created_by, rec.purpose,
        1 if rec.disabled else 0, rec.disabled_by, rec.disabled_at,
        rec.enabled_by, rec.enabled_at,
        rec.role, rec.role_set_by, rec.role_set_at,
        rec.subject,
    )


def _role_update_args(rec: UserRecord) -> tuple:
    """Positional args for _ROLE_UPDATE_* — same order as _ROLE_SET_COLUMNS,
    with ``subject`` last for the WHERE clause."""
    return (rec.role, rec.role_set_by, rec.role_set_at, rec.subject)


def _row_to_record(row: Any) -> UserRecord:
    (
        subject, issuer, email, display_name, provisional, first_seen, last_seen,
        kind, created_by, purpose, disabled, disabled_by, disabled_at,
        enabled_by, enabled_at, role, role_set_by, role_set_at,
    ) = tuple(row)
    return UserRecord(
        subject=subject, issuer=issuer, email=email, display_name=display_name,
        provisional=bool(provisional), first_seen_at=first_seen, last_seen_at=last_seen,
        kind=kind or KIND_HUMAN, created_by=created_by, purpose=purpose,
        # A row written by a build that had no `disabled` column but did stamp
        # `disabled_at` reads back DISABLED, not enabled: ensure_columns
        # backfills the new flag with 0, and silently resurrecting a revoked
        # credential on upgrade is the one migration outcome worth code.
        disabled=bool(disabled) or (bool(disabled_at) and not enabled_at),
        disabled_by=disabled_by, disabled_at=disabled_at,
        enabled_by=enabled_by, enabled_at=enabled_at,
        # No fallback and no coercion: an unrecognised value read back is NOT
        # admin, which is the only safe direction for a privilege column.
        role=role, role_set_by=role_set_by, role_set_at=role_set_at,
    )


class SqliteUserStore:
    """Durable single-host profile table on stdlib sqlite3.

    A connection per operation, run in a worker thread so blocking sqlite never
    stalls the event loop; WAL so readers coexist with the writer. DDL +
    ensure_columns run synchronously in ``__init__`` — build this in lifespan
    (or off-loop), not inside a request handler.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        with closing(self._connect()) as conn, conn:
            conn.execute(_USERS_DDL)
            ensure_columns_sqlite(conn, "users", _USERS_COLUMNS)

    def _connect(self) -> sqlite3.Connection:
        # ``with closing(...) as conn, conn:`` — sqlite3's connection context
        # manager commits but does NOT close (jobstore.py's note).
        conn = sqlite3.connect(self._path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _upsert(self, conn: sqlite3.Connection, rec: UserRecord) -> None:
        # Profile columns ONLY (see _SEEN_ASSIGN_COLUMNS): the first-auth hook
        # must not be able to write kind/created_by/purpose/disabled_*.
        assignments = ", ".join(f"{c} = excluded.{c}" for c in _SEEN_ASSIGN_COLUMNS)
        conn.execute(
            f"INSERT INTO users ({', '.join(_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(_COLUMNS))}) "
            f"ON CONFLICT(subject) DO UPDATE SET {assignments}",
            _record_to_row(rec),
        )

    def _upsert_seen_sync(
        self, subject: str, issuer: str, email: str, display_name: str
    ) -> UserRecord:
        with closing(self._connect()) as conn, conn:
            prior = conn.execute(_SELECT + " WHERE subject = ?", (subject,)).fetchone()
            rec = _merge_seen(
                _row_to_record(prior) if prior is not None else None,
                subject, issuer, email, display_name,
            )
            self._upsert(conn, rec)
        return rec

    def _ensure_provisional_sync(self, subject: str, issuer: str) -> UserRecord:
        with closing(self._connect()) as conn, conn:
            prior = conn.execute(_SELECT + " WHERE subject = ?", (subject,)).fetchone()
            if prior is not None:
                return _row_to_record(prior)
            rec = UserRecord(
                subject=subject, issuer=issuer, provisional=True, first_seen_at=_now()
            )
            # INSERT OR IGNORE: a racing upsert_seen must win, not be demoted.
            conn.execute(
                f"INSERT OR IGNORE INTO users ({', '.join(_COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(_COLUMNS))})",
                _record_to_row(rec),
            )
            row = conn.execute(_SELECT + " WHERE subject = ?", (subject,)).fetchone()
        return _row_to_record(row)

    def _create_service_account_sync(
        self, subject: str, created_by: str, purpose: str
    ) -> UserRecord:
        # Read, validate, write — all inside ONE connection context, which is
        # the sqlite transaction boundary (commits on success, rolls back on
        # the UserInvariantError).
        with closing(self._connect()) as conn, conn:
            prior_row = conn.execute(_SELECT + " WHERE subject = ?", (subject,)).fetchone()
            prior = _row_to_record(prior_row) if prior_row is not None else None
            rec = _new_service_account(subject, created_by, purpose)
            _check_service_account(prior, rec)
            if prior is not None and prior.kind == KIND_SERVICE:
                return prior  # already registered: unchanged, not rewritten
            if prior is None:
                try:
                    conn.execute(
                        f"INSERT INTO users ({', '.join(_COLUMNS)}) "
                        f"VALUES ({', '.join('?' * len(_COLUMNS))})",
                        _record_to_row(rec),
                    )
                except sqlite3.IntegrityError as e:
                    # The primary key is the race-window backstop: someone
                    # created the subject between our SELECT and this INSERT.
                    raise UserInvariantError(
                        f"{subject!r} was created concurrently; retry"
                    ) from e
            else:
                # Upgrade the never-authenticated provisional placeholder in
                # place, keeping its first_seen_at (row-creation time).
                rec = rec.model_copy(
                    update={
                        "issuer": prior.issuer,
                        "first_seen_at": prior.first_seen_at or rec.first_seen_at,
                    }
                )
                conn.execute(_SERVICE_UPDATE_SQLITE, _service_update_args(rec))
            row = conn.execute(_SELECT + " WHERE subject = ?", (subject,)).fetchone()
        return _row_to_record(row)

    def _set_disabled_sync(self, subject: str, actor: str, disable: bool) -> UserRecord:
        with closing(self._connect()) as conn, conn:
            prior_row = conn.execute(_SELECT + " WHERE subject = ?", (subject,)).fetchone()
            prior = _row_to_record(prior_row) if prior_row is not None else None
            rec = _toggle_service_account(prior, subject, actor, disable)
            conn.execute(_SERVICE_UPDATE_SQLITE, _service_update_args(rec))
        return rec

    def _set_role_sync(
        self,
        subject: str,
        role: str,
        actor: str,
        require_remaining_admin: bool = False,
    ) -> UserRecord:
        # BEGIN IMMEDIATE, not the implicit deferred transaction: a privilege
        # change reads the table (the row, and — under the guard — how many
        # OTHER admins exist) and then writes based on what it read. A deferred
        # transaction takes no lock until its first DML, so those SELECTs would
        # sit outside any lock and a concurrent revoke could commit between
        # them and the UPDATE. IMMEDIATE takes the write lock up front, which
        # makes read-validate-write atomic against every other role writer
        # (they wait, then see this one's result) and lets a raised
        # LastAdminError roll the whole thing back via ``with conn``.
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            prior_row = conn.execute(_SELECT + " WHERE subject = ?", (subject,)).fetchone()
            prior = _row_to_record(prior_row) if prior_row is not None else None
            rec = _apply_role(prior, subject, role, actor)
            if require_remaining_admin and _is_demotion(prior, rec):
                row = conn.execute(
                    _COUNT_OTHER_ADMINS_SQLITE, (USER_ROLE_ADMIN, subject)
                ).fetchone()
                if int(row[0] if row is not None else 0) == 0:
                    raise _last_admin_error(subject)
            conn.execute(_ROLE_UPDATE_SQLITE, _role_update_args(rec))
        return rec

    def _count_admins_sync(self) -> int:
        with closing(self._connect()) as conn, conn:
            row = conn.execute(_COUNT_ADMINS_SQLITE, (USER_ROLE_ADMIN,)).fetchone()
        return int(row[0]) if row is not None else 0

    def _get_sync(self, subject: str) -> UserRecord | None:
        with closing(self._connect()) as conn, conn:
            row = conn.execute(_SELECT + " WHERE subject = ?", (subject,)).fetchone()
        return _row_to_record(row) if row is not None else None

    def _list_sync(self, limit: int) -> list[UserRecord]:
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(_SELECT + _ORDER + " LIMIT ?", (limit,)).fetchall()
        return [_row_to_record(r) for r in rows]

    def _list_service_sync(self, created_by: str, limit: int) -> list[UserRecord]:
        where = " WHERE kind = ?"
        args: tuple = (KIND_SERVICE,)
        if created_by:
            where += " AND created_by = ?"
            args += (created_by,)
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(
                _SELECT + where + _ORDER + " LIMIT ?", (*args, limit)
            ).fetchall()
        return [_row_to_record(r) for r in rows]

    async def upsert_seen(
        self, subject: str, issuer: str, email: str = "", display_name: str = ""
    ) -> UserRecord:
        return await asyncio.to_thread(
            self._upsert_seen_sync, subject, issuer, email, display_name
        )

    async def ensure_provisional(self, subject: str, issuer: str) -> UserRecord:
        return await asyncio.to_thread(self._ensure_provisional_sync, subject, issuer)

    async def create_service_account(
        self, subject: str, created_by: str, purpose: str = ""
    ) -> UserRecord:
        return await asyncio.to_thread(
            self._create_service_account_sync, subject, created_by, purpose
        )

    async def disable_service_account(self, subject: str, actor: str) -> UserRecord:
        return await asyncio.to_thread(self._set_disabled_sync, subject, actor, True)

    async def enable_service_account(self, subject: str, actor: str) -> UserRecord:
        return await asyncio.to_thread(self._set_disabled_sync, subject, actor, False)

    async def set_role(
        self,
        subject: str,
        role: str,
        actor: str,
        *,
        require_remaining_admin: bool = False,
    ) -> UserRecord:
        return await asyncio.to_thread(
            self._set_role_sync, subject, role, actor, require_remaining_admin
        )

    async def count_admins(self) -> int:
        return await asyncio.to_thread(self._count_admins_sync)

    async def get(self, subject: str) -> UserRecord | None:
        return await asyncio.to_thread(self._get_sync, subject)

    async def list_users(self, limit: int = 100) -> list[UserRecord]:
        return await asyncio.to_thread(self._list_sync, limit)

    async def list_service_accounts(
        self, created_by: str = "", limit: int = 100
    ) -> list[UserRecord]:
        return await asyncio.to_thread(self._list_service_sync, created_by, limit)

    async def close(self) -> None:
        """No persistent connection to release."""


def _normalize_dsn(dsn: str) -> str:
    """Strip SQLAlchemy-style ``+driver`` suffixes asyncpg doesn't understand."""
    for marker in ("+asyncpg", "+psycopg2", "+psycopg"):
        dsn = dsn.replace(marker, "")
    return dsn


class PostgresUserStore:
    """Durable, multi-process profile table on Postgres via asyncpg.

    The backend ADR-0004's shares/groups will require (they FK onto this
    table). Pool and schema are created lazily on first use (asyncpg needs a
    loop), so construction stays synchronous like the other stores.
    """

    def __init__(self, dsn: str, min_size: int = 1, max_size: int = 5) -> None:
        self._dsn = _normalize_dsn(dsn)
        self._min = min_size
        self._max = max_size
        self._pool: Any = None
        self._lock = asyncio.Lock()

    async def _pool_(self) -> Any:
        if self._pool is None:
            async with self._lock:
                if self._pool is None:
                    try:
                        import asyncpg
                    except ImportError as e:  # pragma: no cover
                        raise RuntimeError(
                            "postgres user store requires asyncpg "
                            "(pip install ragstack[postgres])"
                        ) from e
                    pool = await asyncpg.create_pool(
                        self._dsn, min_size=self._min, max_size=self._max
                    )
                    async with pool.acquire() as conn, conn.transaction():
                        # Cross-process serialization: IF NOT EXISTS DDL races
                        # under concurrency (duplicate-key on pg_class), and
                        # ADD COLUMN IF NOT EXISTS takes ACCESS EXCLUSIVE — so
                        # two workers booting at once would otherwise be able
                        # to fail one startup nondeterministically. Mirrors the
                        # shares/groups DDL locks. The asyncio lock above only
                        # serializes coroutines in THIS process.
                        await conn.execute(
                            "SELECT pg_advisory_xact_lock($1)", _USERS_DDL_LOCK_KEY
                        )
                        await conn.execute(_USERS_DDL)
                        await ensure_columns_postgres(conn, "users", _USERS_COLUMNS)
                    self._pool = pool
        return self._pool

    async def upsert_seen(
        self, subject: str, issuer: str, email: str = "", display_name: str = ""
    ) -> UserRecord:
        placeholders = ", ".join(f"${i + 1}" for i in range(len(_COLUMNS)))
        # Profile columns ONLY (see _SEEN_ASSIGN_COLUMNS) — with no enclosing
        # transaction around the read-then-write, this narrowing is what stops
        # a create_service_account landing in the window from being clobbered.
        assignments = ", ".join(f"{c} = excluded.{c}" for c in _SEEN_ASSIGN_COLUMNS)
        pool = await self._pool_()
        async with pool.acquire() as conn:
            prior = await conn.fetchrow(_SELECT + " WHERE subject = $1", subject)
            rec = _merge_seen(
                _row_to_record(tuple(prior)) if prior is not None else None,
                subject, issuer, email, display_name,
            )
            await conn.execute(
                f"INSERT INTO users ({', '.join(_COLUMNS)}) VALUES ({placeholders}) "
                f"ON CONFLICT (subject) DO UPDATE SET {assignments}",
                *_record_to_row(rec),
            )
        return rec

    async def ensure_provisional(self, subject: str, issuer: str) -> UserRecord:
        placeholders = ", ".join(f"${i + 1}" for i in range(len(_COLUMNS)))
        pool = await self._pool_()
        async with pool.acquire() as conn:
            rec = UserRecord(
                subject=subject, issuer=issuer, provisional=True, first_seen_at=_now()
            )
            # DO NOTHING: a racing upsert_seen must win, not be demoted.
            await conn.execute(
                f"INSERT INTO users ({', '.join(_COLUMNS)}) VALUES ({placeholders}) "
                "ON CONFLICT (subject) DO NOTHING",
                *_record_to_row(rec),
            )
            row = await conn.fetchrow(_SELECT + " WHERE subject = $1", subject)
        return _row_to_record(tuple(row))

    async def create_service_account(
        self, subject: str, created_by: str, purpose: str = ""
    ) -> UserRecord:
        placeholders = ", ".join(f"${i + 1}" for i in range(len(_COLUMNS)))
        pool = await self._pool_()
        # asyncpg autocommits per statement, so the read-validate-write needs an
        # explicit transaction (acl_store.py's multi-statement precedent).
        async with pool.acquire() as conn, conn.transaction():
            prior_row = await conn.fetchrow(_SELECT + " WHERE subject = $1", subject)
            prior = _row_to_record(tuple(prior_row)) if prior_row is not None else None
            rec = _new_service_account(subject, created_by, purpose)
            _check_service_account(prior, rec)
            if prior is not None and prior.kind == KIND_SERVICE:
                return prior  # already registered: unchanged, not rewritten
            if prior is None:
                try:
                    await conn.execute(
                        f"INSERT INTO users ({', '.join(_COLUMNS)}) "
                        f"VALUES ({placeholders})",
                        *_record_to_row(rec),
                    )
                except Exception as e:  # pragma: no cover - needs a real race
                    # asyncpg types matched by name to avoid importing asyncpg.
                    if type(e).__name__ != "UniqueViolationError":
                        raise
                    raise UserInvariantError(
                        f"{subject!r} was created concurrently; retry"
                    ) from e
            else:
                rec = rec.model_copy(
                    update={
                        "issuer": prior.issuer,
                        "first_seen_at": prior.first_seen_at or rec.first_seen_at,
                    }
                )
                await conn.execute(_SERVICE_UPDATE_POSTGRES, *_service_update_args(rec))
            row = await conn.fetchrow(_SELECT + " WHERE subject = $1", subject)
        return _row_to_record(tuple(row))

    async def _set_disabled(self, subject: str, actor: str, disable: bool) -> UserRecord:
        pool = await self._pool_()
        async with pool.acquire() as conn, conn.transaction():
            prior_row = await conn.fetchrow(_SELECT + " WHERE subject = $1", subject)
            prior = _row_to_record(tuple(prior_row)) if prior_row is not None else None
            rec = _toggle_service_account(prior, subject, actor, disable)
            await conn.execute(_SERVICE_UPDATE_POSTGRES, *_service_update_args(rec))
        return rec

    async def disable_service_account(self, subject: str, actor: str) -> UserRecord:
        return await self._set_disabled(subject, actor, True)

    async def enable_service_account(self, subject: str, actor: str) -> UserRecord:
        return await self._set_disabled(subject, actor, False)

    async def set_role(
        self,
        subject: str,
        role: str,
        actor: str,
        *,
        require_remaining_admin: bool = False,
    ) -> UserRecord:
        pool = await self._pool_()
        # asyncpg autocommits per statement, so the read-validate-write needs an
        # explicit transaction — a privilege change must not be able to interleave
        # with a concurrent one and lose the loser's validation.
        #
        # The transaction alone is not enough for the last-admin guard: under
        # READ COMMITTED two concurrent revokes touch DIFFERENT rows, so neither
        # blocks and each still counts the other as an admin (write skew) — both
        # commit and the deployment lands on zero admins. The advisory lock is
        # transaction-scoped and taken by every role write, so role writes are
        # serialized deployment-wide and the count is taken against a table no
        # other role writer can be mutating. Role changes are rare; the
        # contention this costs is nil.
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", _ROLE_WRITE_LOCK)
            prior_row = await conn.fetchrow(_SELECT + " WHERE subject = $1", subject)
            prior = _row_to_record(tuple(prior_row)) if prior_row is not None else None
            rec = _apply_role(prior, subject, role, actor)
            if require_remaining_admin and _is_demotion(prior, rec):
                others = await conn.fetchval(
                    _COUNT_OTHER_ADMINS_POSTGRES, USER_ROLE_ADMIN, subject
                )
                if int(others or 0) == 0:
                    raise _last_admin_error(subject)
            await conn.execute(_ROLE_UPDATE_POSTGRES, *_role_update_args(rec))
        return rec

    async def count_admins(self) -> int:
        pool = await self._pool_()
        async with pool.acquire() as conn:
            value = await conn.fetchval(_COUNT_ADMINS_POSTGRES, USER_ROLE_ADMIN)
        return int(value or 0)

    async def get(self, subject: str) -> UserRecord | None:
        pool = await self._pool_()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(_SELECT + " WHERE subject = $1", subject)
        return _row_to_record(tuple(row)) if row is not None else None

    async def list_users(self, limit: int = 100) -> list[UserRecord]:
        pool = await self._pool_()
        async with pool.acquire() as conn:
            rows = await conn.fetch(_SELECT + _ORDER + " LIMIT $1", limit)
        return [_row_to_record(tuple(r)) for r in rows]

    async def list_service_accounts(
        self, created_by: str = "", limit: int = 100
    ) -> list[UserRecord]:
        where = " WHERE kind = $1"
        args: tuple = (KIND_SERVICE,)
        if created_by:
            where += " AND created_by = $2"
            args += (created_by,)
        pool = await self._pool_()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                _SELECT + where + _ORDER + f" LIMIT ${len(args) + 1}", *args, limit
            )
        return [_row_to_record(tuple(r)) for r in rows]

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()


# --------------------------------------------------------------------------- #
# Construction + module-level singleton
# --------------------------------------------------------------------------- #


def make_user_store(cfg: Any = settings) -> UserStore:
    """Build the configured user store. Defaults to ``memory`` — no deployment
    grows a users table without opting in."""
    backend = (getattr(cfg, "user_store_backend", MEMORY) or MEMORY).lower()
    if backend == SQLITE:
        return SqliteUserStore(cfg.user_store_path)
    if backend == POSTGRES:
        return PostgresUserStore(
            getattr(cfg, "user_store_dsn", "") or cfg.postgres_dsn
        )
    if backend != MEMORY:
        log.warning("unknown user_store_backend %r; falling back to 'memory'", backend)
    return InMemoryUserStore()


def validate_user_store_settings() -> None:
    """Fail fast at startup on a misconfigured user store — a typo'd backend
    would otherwise silently record users in memory and lose them on restart."""
    backend = (settings.user_store_backend or MEMORY).lower()
    if backend not in VALID_USER_STORE_BACKENDS:
        raise RuntimeError(
            f"user_store_backend={settings.user_store_backend!r} is not one of "
            f"{sorted(VALID_USER_STORE_BACKENDS)}"
        )


# The process-wide store, built on first use and rebuilt when the settings it
# was built from change (tests monkeypatch them) — the identity/factory.py
# get/set/reset trio.
_store: UserStore | None = None
_built_for: tuple | None = None


def _settings_key() -> tuple:
    return (
        (settings.user_store_backend or MEMORY).lower(),
        getattr(settings, "user_store_path", ""),
        getattr(settings, "user_store_dsn", ""),
    )


def get_user_store() -> UserStore:
    """The process-wide user store, built on first use.

    Rebuilt automatically when the ``user_store_*`` settings change; call
    :func:`reset_user_store` to drop it explicitly (tests, shutdown).
    """
    global _store, _built_for
    key = _settings_key()
    if _store is None or _built_for != key:
        _store = make_user_store(settings)
        _built_for = key
    return _store


def set_user_store(store: UserStore | None) -> None:
    """Install ``store`` explicitly (tests, and callers that build their own)."""
    global _store, _built_for
    _store = store
    _built_for = _settings_key()


def reset_user_store() -> None:
    """Drop the cached store so the next use rebuilds it from settings."""
    global _store, _built_for
    _store = None
    _built_for = None
