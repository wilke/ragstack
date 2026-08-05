"""RAGStack-native group store — ADR-0004 decisions 3 & 4, issue #245 part 1.

Groups + group_members live in the SAME per-tenant ACL database as ``users``
and ``shares``: this module's store classes *extend* the
:mod:`ragstack.acl_store` backends (which in turn extend
:mod:`ragstack.user_store`), so one ``user_store_backend`` /
``user_store_path`` / ``user_store_dsn`` triple governs all four tables — one
sqlite path / one asyncpg pool, one ``close()`` — and a transaction spanning
``users`` + ``shares`` + ``groups`` + ``group_members`` has a single connection
to run on.

MUST import nothing from ``ragstack.api.*`` — authz evaluates against it from
the request path, and a reverse import is a cycle. Schema (the DDL strings)
and service (the store classes) stand alone below the API; the API and
authz.py call DOWN into them.

Semantics (ADR-0004 decisions 3/4):

- ``groups(id, name, owner_subject, built_in, created_at, deleted_by,
  deleted_at)`` — a group is a named, owned bag of user subjects. Membership
  is a *flat* list of USER tenant strings; there is NO nesting (a member is
  never a group id — :func:`_check_membership` rejects it), so no
  transitive-closure resolver is ever needed.
- ``group_members(id, group_id, subject, added_by, added_at, removed_by,
  removed_at)`` — one soft-deletable membership row per (group, subject).
- The built-in ``public`` group (id == :data:`~ragstack.acl_store.PUBLIC_GROUP`)
  is a SINGLE ``groups`` row with ``built_in = 1`` so it can be listed and
  referenced as a share target. Its membership is CONSTANT-TRUE and cannot be
  enumerated ("everyone"), so it is resolved in code — :meth:`is_member` and
  the grants expansion short-circuit to true for it — and its
  ``group_members`` table stays EMPTY. Public is never editable and never
  deletable.
- Soft-delete everywhere (the ACL DB's contract, ADR-0004 decision 6): groups
  carry ``deleted_at`` and members carry ``removed_at``; ``''`` means active.
  Nothing here ever issues a DELETE — the audit trail is the point.

House pattern (:mod:`ragstack.acl_store` / :mod:`ragstack.user_store`): one
shared-dialect DDL string per table (TEXT/INTEGER only, ISO-8601 UTC text
timestamps, INTEGER 0/1 booleans, ``''`` sentinel), additive-only
``ensure_columns`` migration, partial unique indexes as their own statements,
semantics centralized in pure helpers shared by every backend, and
``memory``/``sqlite``/``postgres`` backends selected by the same settings.

The ``#245`` grants seam: :func:`ragstack.acl_store.AclStore.grants_for_subject`
unions only direct-user shares + the public group. This module is the ONLY
layer that can read ``group_members``, so it OVERRIDES ``grants_for_subject``
to also apply a share granted to a real group to that group's active members.
Leaving the acl_store version untouched would make real-group shares silently
do nothing.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import uuid
from contextlib import closing
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from ragstack.acl_store import (
    _SHARE_ORDER,
    _SHARE_SELECT,
    GRANTEE_GROUP,
    GRANTEE_USER,
    PUBLIC_GROUP,
    InMemoryAclStore,
    PostgresAclStore,
    SqliteAclStore,
    _row_to_share,
)
from ragstack.collection_store import (
    ensure_columns_postgres,
    ensure_columns_sqlite,
)
from ragstack.config import settings
from ragstack.user_store import MEMORY, POSTGRES, SQLITE, _now

log = logging.getLogger(__name__)


class GroupInvariantError(ValueError):
    """A group/membership mutation would violate an invariant (empty name,
    reserved/built-in target, nesting, duplicate active membership, ...)."""


class GroupNotFoundError(KeyError):
    """The referenced group id does not exist (or is soft-deleted)."""


class GroupRecord(BaseModel):
    """One row of the ``groups`` table. ``deleted_at == ''`` means active.

    ``owner_subject`` is the creating user's tenant string (ADR-0004's
    ``owner_subject``); it doubles as the creator for audit. The built-in
    ``public`` group uses the fixed id :data:`~ragstack.acl_store.PUBLIC_GROUP`
    and ``owner_subject == ''``.
    """

    id: str
    name: str = ""
    owner_subject: str = ""
    built_in: bool = False  # stored as INTEGER 0/1
    created_at: str = ""  # ISO-8601 UTC
    deleted_by: str = ""
    deleted_at: str = ""  # '' = active (partial indexes filter on this)

    @property
    def active(self) -> bool:
        return self.deleted_at == ""


class GroupMemberRecord(BaseModel):
    """One row of the ``group_members`` table. ``removed_at == ''`` means
    active. ``subject`` is always a USER tenant string ``f"{issuer}:{sub}"`` —
    never a group id (no nesting). Audit columns mirror ``shares`` one-for-one
    (``added_by``/``added_at`` == ``granted_by``/``granted_at``)."""

    id: str
    group_id: str
    subject: str
    added_by: str = ""
    added_at: str = ""  # ISO-8601 UTC
    removed_by: str = ""
    removed_at: str = ""  # '' = active

    @property
    def active(self) -> bool:
        return self.removed_at == ""


# --------------------------------------------------------------------------- #
# DDL — the published contract
# --------------------------------------------------------------------------- #

# CONTRACT — this DDL is the published cross-consumer schema for RAGStack
# groups (ADR-0004 decision 3; issue #245). Any other consumer (a Go client,
# the intended second service consumer) codes against exactly this shape.
# Changes are ADDITIVE ONLY, via _GROUPS_COLUMNS + ensure_columns_* — never a
# rename, retype, or drop, and never a DELETE on rows (soft-delete only:
# deleted_at/removed_at, ADR-0004 decision 6). Dialect-shared verbatim by
# sqlite and postgres: TEXT/INTEGER only, ISO-8601 UTC text timestamps,
# INTEGER 0/1 booleans, '' (empty string) for "not deleted"/"not removed".
# No JSONB, no TIMESTAMPTZ, no real FOREIGN KEY (integrity is app-level via
# ensure_provisional + the _check_* helpers, "FK-checkable later" — shares
# declares none either).
_GROUPS_DDL = (
    "CREATE TABLE IF NOT EXISTS groups ("
    "  id TEXT PRIMARY KEY,"
    "  name TEXT NOT NULL DEFAULT '',"
    "  owner_subject TEXT NOT NULL DEFAULT '',"
    "  built_in INTEGER NOT NULL DEFAULT 0,"
    "  created_at TEXT NOT NULL DEFAULT '',"
    "  deleted_by TEXT NOT NULL DEFAULT '',"
    "  deleted_at TEXT NOT NULL DEFAULT ''"
    ")"
)

_GROUP_MEMBERS_DDL = (
    "CREATE TABLE IF NOT EXISTS group_members ("
    "  id TEXT PRIMARY KEY,"
    "  group_id TEXT NOT NULL,"
    "  subject TEXT NOT NULL,"
    "  added_by TEXT NOT NULL DEFAULT '',"
    "  added_at TEXT NOT NULL DEFAULT '',"
    "  removed_by TEXT NOT NULL DEFAULT '',"
    "  removed_at TEXT NOT NULL DEFAULT ''"
    ")"
)

# Partial unique indexes (identical syntax in sqlite >= 3.8 and postgres).
# ensure_columns can only ADD COLUMN, so constraints live here as their own
# IF NOT EXISTS statements, run in the same DDL block as the CREATE TABLEs.
# One active group name per owner; one active membership per (group, subject).
_GROUPS_INDEX_NAME = (
    "CREATE UNIQUE INDEX IF NOT EXISTS groups_active_name "
    "ON groups (owner_subject, name) "
    "WHERE deleted_at = ''"
)
_GROUP_MEMBERS_INDEX_ACTIVE = (
    "CREATE UNIQUE INDEX IF NOT EXISTS group_members_active "
    "ON group_members (group_id, subject) "
    "WHERE removed_at = ''"
)
_GROUPS_INDEXES = (_GROUPS_INDEX_NAME, _GROUP_MEMBERS_INDEX_ACTIVE)

# Advisory-lock key serializing the groups DDL across processes. Distinct from
# acl_store._SHARES_DDL_LOCK_KEY: the groups DDL runs in a SEPARATE transaction
# after super()._pool_() already ran (and released, being xact-scoped) the
# shares DDL under the shares key, so a distinct key keeps the two independent.
# Any stable 64-bit constant works; this one is b"rag_grps" as an int64.
_GROUPS_DDL_LOCK_KEY = 0x7261675F67727073

# Column -> DDL fragment for additive migration of tables created by an older
# build. Every entry MUST be nullable or defaulted (sqlite's ALTER TABLE ADD
# COLUMN forbids NOT NULL without a default).
_GROUPS_COLUMNS: dict[str, str] = {
    "name": "TEXT NOT NULL DEFAULT ''",
    "owner_subject": "TEXT NOT NULL DEFAULT ''",
    "built_in": "INTEGER NOT NULL DEFAULT 0",
    "created_at": "TEXT NOT NULL DEFAULT ''",
    "deleted_by": "TEXT NOT NULL DEFAULT ''",
    "deleted_at": "TEXT NOT NULL DEFAULT ''",
}
_GROUP_MEMBERS_COLUMNS: dict[str, str] = {
    "group_id": "TEXT NOT NULL DEFAULT ''",
    "subject": "TEXT NOT NULL DEFAULT ''",
    "added_by": "TEXT NOT NULL DEFAULT ''",
    "added_at": "TEXT NOT NULL DEFAULT ''",
    "removed_by": "TEXT NOT NULL DEFAULT ''",
    "removed_at": "TEXT NOT NULL DEFAULT ''",
}

_GROUP_COLUMNS = (
    "id", "name", "owner_subject", "built_in",
    "created_at", "deleted_by", "deleted_at",
)
_GROUP_SELECT = f"SELECT {', '.join(_GROUP_COLUMNS)} FROM groups"
_GROUP_ORDER = " ORDER BY created_at, id"

_MEMBER_COLUMNS = (
    "id", "group_id", "subject",
    "added_by", "added_at", "removed_by", "removed_at",
)
_MEMBER_SELECT = f"SELECT {', '.join(_MEMBER_COLUMNS)} FROM group_members"
_MEMBER_ORDER = " ORDER BY added_at, id"


def _group_to_row(rec: GroupRecord) -> tuple:
    return (
        rec.id, rec.name, rec.owner_subject, 1 if rec.built_in else 0,
        rec.created_at, rec.deleted_by, rec.deleted_at,
    )


def _row_to_group(row: Any) -> GroupRecord:
    gid, name, owner, built_in, created, dby, dat = tuple(row)
    return GroupRecord(
        id=gid, name=name, owner_subject=owner, built_in=bool(built_in),
        created_at=created, deleted_by=dby, deleted_at=dat,
    )


def _member_to_row(rec: GroupMemberRecord) -> tuple:
    return (
        rec.id, rec.group_id, rec.subject,
        rec.added_by, rec.added_at, rec.removed_by, rec.removed_at,
    )


def _row_to_member(row: Any) -> GroupMemberRecord:
    mid, gid, subject, aby, aat, rby, rat = tuple(row)
    return GroupMemberRecord(
        id=mid, group_id=gid, subject=subject,
        added_by=aby, added_at=aat, removed_by=rby, removed_at=rat,
    )


# --------------------------------------------------------------------------- #
# Pure semantics — shared by every backend so behaviour cannot drift
# --------------------------------------------------------------------------- #


def _public_group() -> GroupRecord:
    """The single built-in ``public`` row (seeded idempotently at bootstrap)."""
    return GroupRecord(
        id=PUBLIC_GROUP, name=PUBLIC_GROUP, owner_subject="",
        built_in=True, created_at=_now(),
    )


def _new_group(name: str, owner_subject: str) -> GroupRecord:
    return GroupRecord(
        id=uuid.uuid4().hex, name=name, owner_subject=owner_subject,
        built_in=False, created_at=_now(),
    )


def _new_member(group_id: str, subject: str, added_by: str) -> GroupMemberRecord:
    return GroupMemberRecord(
        id=uuid.uuid4().hex, group_id=group_id, subject=subject,
        added_by=added_by, added_at=_now(),
    )


def _check_group(rec: GroupRecord) -> None:
    """Invariants for creating a group. ``public`` is reserved for the built-in
    row, so a user-created group may not claim that name."""
    if not rec.name:
        raise GroupInvariantError("group name must be non-empty")
    if not rec.owner_subject:
        raise GroupInvariantError("owner_subject must be non-empty")
    if rec.name == PUBLIC_GROUP and not rec.built_in:
        raise GroupInvariantError(
            f"{PUBLIC_GROUP!r} is the reserved built-in group name"
        )


def _check_membership(
    active_members: list[GroupMemberRecord],
    group_ids: set[str],
    rec: GroupMemberRecord,
) -> None:
    """The one place membership invariants live. ``active_members`` is the
    current active membership of ``rec.group_id``; ``group_ids`` is the set of
    all known group ids (to reject nesting). The SQL backends additionally
    rely on the partial unique index for the read-then-check race window."""
    if not rec.group_id:
        raise GroupInvariantError("group_id must be non-empty")
    if not rec.subject:
        raise GroupInvariantError("member subject must be non-empty")
    if rec.subject in group_ids:
        raise GroupInvariantError(
            "no nesting: a group cannot be a member of a group"
        )
    if any(m.subject == rec.subject for m in active_members):
        raise GroupInvariantError(
            f"{rec.subject!r} is already an active member of {rec.group_id!r}"
        )


def _mark_removed(
    rec: GroupMemberRecord, removed_by: str, stamp: str
) -> GroupMemberRecord:
    return rec.model_copy(update={"removed_by": removed_by, "removed_at": stamp})


def _mark_group_deleted(rec: GroupRecord, deleted_by: str, stamp: str) -> GroupRecord:
    return rec.model_copy(update={"deleted_by": deleted_by, "deleted_at": stamp})


def _issuer_of(subject: str) -> str:
    """The issuer half of a ``f"{issuer}:{sub}"`` tenant string (for the
    ensure_provisional pre-provision), or ``''`` when unstructured."""
    return subject.split(":", 1)[0] if ":" in subject else ""


# --------------------------------------------------------------------------- #
# Protocol
# --------------------------------------------------------------------------- #


@runtime_checkable
class GroupStore(Protocol):
    """The groups/group_members API (ADR-0004 decision 3). Every implementation
    also satisfies :class:`ragstack.acl_store.AclStore` and
    :class:`ragstack.user_store.UserStore` — one store object, one database,
    four tables."""

    async def create_group(self, name: str, owner_subject: str) -> GroupRecord:
        """Mint an active group owned by ``owner_subject``. Raises
        :class:`GroupInvariantError` on an empty/reserved name or an active
        name collision for the same owner."""
        ...

    async def delete_group(self, group_id: str, actor: str) -> GroupRecord:
        """Soft-delete a group (``deleted_at``/``deleted_by``). Refuses a
        built-in group. Its member rows and any shares granted to it are left
        intact for audit but become inert (:meth:`groups_for_subject` only
        expands active groups). Raises :class:`GroupNotFoundError` if unknown."""
        ...

    async def get_group(self, group_id: str) -> GroupRecord | None: ...

    async def list_groups_owned_by(self, subject: str) -> list[GroupRecord]:
        """Active groups whose ``owner_subject == subject`` (oldest-first)."""
        ...

    async def list_groups_for_member(self, subject: str) -> list[GroupRecord]:
        """Active groups ``subject`` is an active member of (oldest-first).
        The built-in ``public`` group is implicit and NOT listed here."""
        ...

    async def add_member(
        self, group_id: str, subject: str, added_by: str
    ) -> GroupMemberRecord:
        """Add ``subject`` (a user tenant string) to ``group_id``. Pre-provisions
        the user so a never-logged-in subject can be named. Rejects a built-in
        group, a nested subject (a group id), and a duplicate active
        membership."""
        ...

    async def remove_member(
        self, group_id: str, subject: str, removed_by: str = ""
    ) -> GroupMemberRecord | None:
        """Soft-remove ``subject``'s active membership. Returns the removed row,
        or ``None`` if ``subject`` was not an active member (no-op)."""
        ...

    async def list_members(self, group_id: str) -> list[GroupMemberRecord]:
        """Active membership rows of ``group_id`` (oldest-first). The built-in
        ``public`` group is never materialized, so this is always empty for it."""
        ...

    async def is_member(self, subject: str, group_id: str) -> bool:
        """Whether ``subject`` belongs to ``group_id``. Constant-true for the
        built-in ``public`` group."""
        ...

    async def groups_for_subject(self, subject: str) -> set[str]:
        """The group ids ``subject`` belongs to — active memberships PLUS the
        implicit built-in ``public`` group (always included, constant-true).
        This is what authz calls to expand real-group shares."""
        ...


# --------------------------------------------------------------------------- #
# Backends — each extends its acl_store sibling (same database, four tables)
# --------------------------------------------------------------------------- #


class InMemoryGroupStore(InMemoryAclStore):
    """Process-local users + shares + groups + members. Dev/tests only."""

    def __init__(self) -> None:
        super().__init__()  # _records (users), _shares, _lock
        self._groups: dict[str, GroupRecord] = {}
        self._members: dict[str, GroupMemberRecord] = {}
        pub = _public_group()
        self._groups[pub.id] = pub

    def _active_members(self, group_id: str) -> list[GroupMemberRecord]:
        return [
            m for m in self._members.values()
            if m.group_id == group_id and m.active
        ]

    def _active_group_ids_for(self, subject: str) -> set[str]:
        """Real (non-built-in) active group ids ``subject`` is an active member
        of. Excludes ``public`` — that is handled as a constant-true special
        case by callers, never materialized here."""
        out: set[str] = set()
        for m in self._members.values():
            if not (m.active and m.subject == subject):
                continue
            g = self._groups.get(m.group_id)
            if g is not None and g.active and not g.built_in:
                out.add(m.group_id)
        return out

    async def create_group(self, name: str, owner_subject: str) -> GroupRecord:
        async with self._lock:
            rec = _new_group(name, owner_subject)
            _check_group(rec)
            if any(
                g.active and g.owner_subject == owner_subject and g.name == name
                for g in self._groups.values()
            ):
                raise GroupInvariantError(
                    f"an active group named {name!r} already exists for this owner"
                )
            self._groups[rec.id] = rec
            return rec.model_copy(deep=True)

    async def delete_group(self, group_id: str, actor: str) -> GroupRecord:
        async with self._lock:
            g = self._groups.get(group_id)
            if g is None:
                raise GroupNotFoundError(group_id)
            if g.built_in:
                raise GroupInvariantError(
                    f"the built-in {group_id!r} group cannot be deleted"
                )
            if not g.active:
                return g.model_copy(deep=True)
            deleted = _mark_group_deleted(g, actor, _now())
            self._groups[group_id] = deleted
            return deleted.model_copy(deep=True)

    async def get_group(self, group_id: str) -> GroupRecord | None:
        async with self._lock:
            g = self._groups.get(group_id)
            return g.model_copy(deep=True) if g is not None else None

    async def list_groups_owned_by(self, subject: str) -> list[GroupRecord]:
        async with self._lock:
            rows = [
                g for g in self._groups.values()
                if g.active and g.owner_subject == subject
            ]
            rows.sort(key=lambda g: (g.created_at, g.id))
            return [g.model_copy(deep=True) for g in rows]

    async def list_groups_for_member(self, subject: str) -> list[GroupRecord]:
        async with self._lock:
            ids = self._active_group_ids_for(subject)
            rows = [self._groups[i] for i in ids if i in self._groups]
            rows.sort(key=lambda g: (g.created_at, g.id))
            return [g.model_copy(deep=True) for g in rows]

    async def add_member(
        self, group_id: str, subject: str, added_by: str
    ) -> GroupMemberRecord:
        # Validate the target group before we pre-provision a user for it.
        async with self._lock:
            g = self._groups.get(group_id)
            if g is None or not g.active:
                raise GroupNotFoundError(group_id)
            if g.built_in:
                raise GroupInvariantError(
                    f"members cannot be added to the built-in {group_id!r} group"
                )
        # ensure_provisional acquires self._lock, so it must run OUTSIDE the
        # block above — a group_members row must never dangle without a users row.
        await self.ensure_provisional(subject, _issuer_of(subject))
        async with self._lock:
            # Re-check under the re-acquired lock: a concurrent delete_group in
            # the ensure_provisional window must not leave a member row on a
            # soft-deleted group. The SQL backends check+insert in one
            # transaction; mirror that atomicity here.
            g = self._groups.get(group_id)
            if g is None or not g.active:
                raise GroupNotFoundError(group_id)
            rec = _new_member(group_id, subject, added_by)
            _check_membership(self._active_members(group_id), set(self._groups), rec)
            self._members[rec.id] = rec
            return rec.model_copy(deep=True)

    async def remove_member(
        self, group_id: str, subject: str, removed_by: str = ""
    ) -> GroupMemberRecord | None:
        async with self._lock:
            for m in self._members.values():
                if m.group_id == group_id and m.subject == subject and m.active:
                    removed = _mark_removed(m, removed_by, _now())
                    self._members[m.id] = removed
                    return removed.model_copy(deep=True)
            return None

    async def list_members(self, group_id: str) -> list[GroupMemberRecord]:
        async with self._lock:
            rows = self._active_members(group_id)
            rows.sort(key=lambda m: (m.added_at, m.id))
            return [m.model_copy(deep=True) for m in rows]

    async def is_member(self, subject: str, group_id: str) -> bool:
        if group_id == PUBLIC_GROUP:
            return True  # constant-true, never materialized
        async with self._lock:
            g = self._groups.get(group_id)
            if g is None or not g.active:
                return False
            return any(
                m.group_id == group_id and m.subject == subject and m.active
                for m in self._members.values()
            )

    async def groups_for_subject(self, subject: str) -> set[str]:
        async with self._lock:
            return {PUBLIC_GROUP} | self._active_group_ids_for(subject)

    async def grants_for_subject(self, subject: str) -> list[Any]:
        # Override the acl_store seam (#245): direct-user + public + every share
        # granted to a real group the subject actively belongs to.
        async with self._lock:
            gids = self._active_group_ids_for(subject)
            rows = [
                r for r in self._shares.values()
                if r.active and (
                    (r.grantee_type == GRANTEE_USER and r.grantee_id == subject)
                    or (r.grantee_type == GRANTEE_GROUP and r.grantee_id == PUBLIC_GROUP)
                    or (r.grantee_type == GRANTEE_GROUP and r.grantee_id in gids)
                )
            ]
            rows.sort(key=lambda r: (r.granted_at, r.id))
            return [r.model_copy(deep=True) for r in rows]


class SqliteGroupStore(SqliteAclStore):
    """Durable single-host users + shares + groups + members in one sqlite file.

    The groups DDL (both tables + the partial unique indexes + the idempotent
    public-group seed) runs synchronously in ``__init__`` — the same place the
    startup probe already exercises — so a broken table fails at boot, not at
    first call. Each mutation runs inside ONE ``with closing(...) as conn,
    conn:`` transaction boundary.
    """

    def __init__(self, path: str) -> None:
        super().__init__(path)  # users + shares DDL
        with closing(self._connect()) as conn, conn:
            conn.execute(_GROUPS_DDL)
            ensure_columns_sqlite(conn, "groups", _GROUPS_COLUMNS)
            conn.execute(_GROUP_MEMBERS_DDL)
            ensure_columns_sqlite(conn, "group_members", _GROUP_MEMBERS_COLUMNS)
            for stmt in _GROUPS_INDEXES:
                conn.execute(stmt)
            pub = _public_group()
            conn.execute(
                f"INSERT OR IGNORE INTO groups ({', '.join(_GROUP_COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(_GROUP_COLUMNS))})",
                _group_to_row(pub),
            )

    # --- sync helpers (run in worker threads) --- #

    def _insert_group(self, conn: sqlite3.Connection, rec: GroupRecord) -> None:
        try:
            conn.execute(
                f"INSERT INTO groups ({', '.join(_GROUP_COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(_GROUP_COLUMNS))})",
                _group_to_row(rec),
            )
        except sqlite3.IntegrityError as e:
            raise GroupInvariantError(str(e)) from e

    def _insert_member(self, conn: sqlite3.Connection, rec: GroupMemberRecord) -> None:
        try:
            conn.execute(
                f"INSERT INTO group_members ({', '.join(_MEMBER_COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(_MEMBER_COLUMNS))})",
                _member_to_row(rec),
            )
        except sqlite3.IntegrityError as e:
            raise GroupInvariantError(str(e)) from e

    @staticmethod
    def _group_row(conn: sqlite3.Connection, group_id: str) -> GroupRecord | None:
        row = conn.execute(
            _GROUP_SELECT + " WHERE id = ?", (group_id,)
        ).fetchone()
        return _row_to_group(row) if row is not None else None

    def _create_group_sync(self, name: str, owner_subject: str) -> GroupRecord:
        with closing(self._connect()) as conn, conn:
            rec = _new_group(name, owner_subject)
            _check_group(rec)
            dup = conn.execute(
                "SELECT 1 FROM groups "
                "WHERE owner_subject = ? AND name = ? AND deleted_at = ''",
                (owner_subject, name),
            ).fetchone()
            if dup is not None:
                raise GroupInvariantError(
                    f"an active group named {name!r} already exists for this owner"
                )
            self._insert_group(conn, rec)
        return rec

    def _delete_group_sync(self, group_id: str, actor: str) -> GroupRecord:
        with closing(self._connect()) as conn, conn:
            g = self._group_row(conn, group_id)
            if g is None:
                raise GroupNotFoundError(group_id)
            if g.built_in:
                raise GroupInvariantError(
                    f"the built-in {group_id!r} group cannot be deleted"
                )
            if not g.active:
                return g
            deleted = _mark_group_deleted(g, actor, _now())
            conn.execute(
                "UPDATE groups SET deleted_at = ?, deleted_by = ? WHERE id = ?",
                (deleted.deleted_at, deleted.deleted_by, group_id),
            )
        return deleted

    def _get_group_sync(self, group_id: str) -> GroupRecord | None:
        with closing(self._connect()) as conn, conn:
            return self._group_row(conn, group_id)

    def _list_owned_sync(self, subject: str) -> list[GroupRecord]:
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(
                _GROUP_SELECT
                + " WHERE deleted_at = '' AND owner_subject = ?"
                + _GROUP_ORDER,
                (subject,),
            ).fetchall()
        return [_row_to_group(r) for r in rows]

    def _list_for_member_sync(self, subject: str) -> list[GroupRecord]:
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(
                _GROUP_SELECT + " g WHERE g.deleted_at = '' AND g.built_in = 0 "
                "AND EXISTS (SELECT 1 FROM group_members gm "
                "WHERE gm.group_id = g.id AND gm.subject = ? AND gm.removed_at = '')"
                + _GROUP_ORDER.replace(" ORDER BY ", " ORDER BY g."),
                (subject,),
            ).fetchall()
        return [_row_to_group(r) for r in rows]

    def _add_member_sync(
        self, group_id: str, subject: str, added_by: str
    ) -> GroupMemberRecord:
        with closing(self._connect()) as conn, conn:
            g = self._group_row(conn, group_id)
            if g is None or not g.active:
                raise GroupNotFoundError(group_id)
            if g.built_in:
                raise GroupInvariantError(
                    f"members cannot be added to the built-in {group_id!r} group"
                )
            if conn.execute(
                "SELECT 1 FROM groups WHERE id = ?", (subject,)
            ).fetchone() is not None:
                raise GroupInvariantError(
                    "no nesting: a group cannot be a member of a group"
                )
            if conn.execute(
                "SELECT 1 FROM group_members "
                "WHERE group_id = ? AND subject = ? AND removed_at = ''",
                (group_id, subject),
            ).fetchone() is not None:
                raise GroupInvariantError(
                    f"{subject!r} is already an active member of {group_id!r}"
                )
            rec = _new_member(group_id, subject, added_by)
            self._insert_member(conn, rec)
        return rec

    def _remove_member_sync(
        self, group_id: str, subject: str, removed_by: str
    ) -> GroupMemberRecord | None:
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                _MEMBER_SELECT
                + " WHERE group_id = ? AND subject = ? AND removed_at = ''",
                (group_id, subject),
            ).fetchone()
            if row is None:
                return None
            removed = _mark_removed(_row_to_member(row), removed_by, _now())
            conn.execute(
                "UPDATE group_members SET removed_at = ?, removed_by = ? WHERE id = ?",
                (removed.removed_at, removed.removed_by, removed.id),
            )
        return removed

    def _list_members_sync(self, group_id: str) -> list[GroupMemberRecord]:
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(
                _MEMBER_SELECT
                + " WHERE group_id = ? AND removed_at = ''"
                + _MEMBER_ORDER,
                (group_id,),
            ).fetchall()
        return [_row_to_member(r) for r in rows]

    def _is_member_sync(self, subject: str, group_id: str) -> bool:
        with closing(self._connect()) as conn, conn:
            g = self._group_row(conn, group_id)
            if g is None or not g.active:
                return False
            row = conn.execute(
                "SELECT 1 FROM group_members "
                "WHERE group_id = ? AND subject = ? AND removed_at = ''",
                (group_id, subject),
            ).fetchone()
        return row is not None

    def _groups_for_subject_sync(self, subject: str) -> set[str]:
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(
                "SELECT gm.group_id FROM group_members gm "
                "JOIN groups g ON g.id = gm.group_id "
                "WHERE gm.subject = ? AND gm.removed_at = '' "
                "AND g.deleted_at = '' AND g.built_in = 0",
                (subject,),
            ).fetchall()
        return {PUBLIC_GROUP} | {r[0] for r in rows}

    def _group_grants_for_subject_sync(self, subject: str) -> list[Any]:
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(
                _SHARE_SELECT
                + " WHERE revoked_at = '' AND ("
                "(grantee_type = 'user' AND grantee_id = ?) "
                "OR (grantee_type = 'group' AND grantee_id = 'public') "
                "OR (grantee_type = 'group' AND grantee_id IN ("
                "  SELECT gm.group_id FROM group_members gm "
                "  JOIN groups g ON g.id = gm.group_id "
                "  WHERE gm.subject = ? AND gm.removed_at = '' "
                "  AND g.deleted_at = '' AND g.built_in = 0)))"
                + _SHARE_ORDER,
                (subject, subject),
            ).fetchall()
        return [_row_to_share(r) for r in rows]

    # --- async surface --- #

    async def create_group(self, name: str, owner_subject: str) -> GroupRecord:
        return await asyncio.to_thread(self._create_group_sync, name, owner_subject)

    async def delete_group(self, group_id: str, actor: str) -> GroupRecord:
        return await asyncio.to_thread(self._delete_group_sync, group_id, actor)

    async def get_group(self, group_id: str) -> GroupRecord | None:
        return await asyncio.to_thread(self._get_group_sync, group_id)

    async def list_groups_owned_by(self, subject: str) -> list[GroupRecord]:
        return await asyncio.to_thread(self._list_owned_sync, subject)

    async def list_groups_for_member(self, subject: str) -> list[GroupRecord]:
        return await asyncio.to_thread(self._list_for_member_sync, subject)

    async def add_member(
        self, group_id: str, subject: str, added_by: str
    ) -> GroupMemberRecord:
        # Pre-provision the user first so the members row never dangles.
        await self.ensure_provisional(subject, _issuer_of(subject))
        return await asyncio.to_thread(
            self._add_member_sync, group_id, subject, added_by
        )

    async def remove_member(
        self, group_id: str, subject: str, removed_by: str = ""
    ) -> GroupMemberRecord | None:
        return await asyncio.to_thread(
            self._remove_member_sync, group_id, subject, removed_by
        )

    async def list_members(self, group_id: str) -> list[GroupMemberRecord]:
        return await asyncio.to_thread(self._list_members_sync, group_id)

    async def is_member(self, subject: str, group_id: str) -> bool:
        if group_id == PUBLIC_GROUP:
            return True
        return await asyncio.to_thread(self._is_member_sync, subject, group_id)

    async def groups_for_subject(self, subject: str) -> set[str]:
        return await asyncio.to_thread(self._groups_for_subject_sync, subject)

    async def grants_for_subject(self, subject: str) -> list[Any]:
        return await asyncio.to_thread(self._group_grants_for_subject_sync, subject)


class PostgresGroupStore(PostgresAclStore):
    """Durable, multi-process users + shares + groups + members on one asyncpg
    pool — the backend a multi-instance deployment requires.

    The groups DDL runs in a SEPARATE, advisory-locked bootstrap after the
    users+shares DDL (:meth:`_pool_`). asyncpg autocommits per statement, so
    every multi-statement mutation runs inside an explicit transaction.
    """

    def __init__(self, dsn: str, min_size: int = 1, max_size: int = 5) -> None:
        super().__init__(dsn, min_size=min_size, max_size=max_size)
        self._groups_ready = False
        self._groups_ddl_lock = asyncio.Lock()

    async def _pool_(self) -> Any:
        pool = await super()._pool_()  # users + shares DDL
        if not self._groups_ready:
            async with self._groups_ddl_lock:
                if not self._groups_ready:
                    async with pool.acquire() as conn, conn.transaction():
                        await conn.execute(
                            "SELECT pg_advisory_xact_lock($1)", _GROUPS_DDL_LOCK_KEY
                        )
                        await conn.execute(_GROUPS_DDL)
                        await ensure_columns_postgres(conn, "groups", _GROUPS_COLUMNS)
                        await conn.execute(_GROUP_MEMBERS_DDL)
                        await ensure_columns_postgres(
                            conn, "group_members", _GROUP_MEMBERS_COLUMNS
                        )
                        for stmt in _GROUPS_INDEXES:
                            await conn.execute(stmt)
                        pub = _public_group()
                        placeholders = ", ".join(
                            f"${i + 1}" for i in range(len(_GROUP_COLUMNS))
                        )
                        await conn.execute(
                            f"INSERT INTO groups ({', '.join(_GROUP_COLUMNS)}) "
                            f"VALUES ({placeholders}) ON CONFLICT (id) DO NOTHING",
                            *_group_to_row(pub),
                        )
                    self._groups_ready = True
        return pool

    @staticmethod
    async def _group_row(conn: Any, group_id: str) -> GroupRecord | None:
        row = await conn.fetchrow(_GROUP_SELECT + " WHERE id = $1", group_id)
        return _row_to_group(tuple(row)) if row is not None else None

    @staticmethod
    async def _insert_group(conn: Any, rec: GroupRecord) -> None:
        placeholders = ", ".join(f"${i + 1}" for i in range(len(_GROUP_COLUMNS)))
        try:
            await conn.execute(
                f"INSERT INTO groups ({', '.join(_GROUP_COLUMNS)}) "
                f"VALUES ({placeholders})",
                *_group_to_row(rec),
            )
        except Exception as e:  # asyncpg.UniqueViolationError, without the import
            if type(e).__name__ == "UniqueViolationError":
                raise GroupInvariantError(str(e)) from e
            raise

    @staticmethod
    async def _insert_member(conn: Any, rec: GroupMemberRecord) -> None:
        placeholders = ", ".join(f"${i + 1}" for i in range(len(_MEMBER_COLUMNS)))
        try:
            await conn.execute(
                f"INSERT INTO group_members ({', '.join(_MEMBER_COLUMNS)}) "
                f"VALUES ({placeholders})",
                *_member_to_row(rec),
            )
        except Exception as e:
            if type(e).__name__ == "UniqueViolationError":
                raise GroupInvariantError(str(e)) from e
            raise

    async def create_group(self, name: str, owner_subject: str) -> GroupRecord:
        pool = await self._pool_()
        async with pool.acquire() as conn, conn.transaction():
            rec = _new_group(name, owner_subject)
            _check_group(rec)
            dup = await conn.fetchrow(
                "SELECT 1 FROM groups "
                "WHERE owner_subject = $1 AND name = $2 AND deleted_at = ''",
                owner_subject, name,
            )
            if dup is not None:
                raise GroupInvariantError(
                    f"an active group named {name!r} already exists for this owner"
                )
            await self._insert_group(conn, rec)
        return rec

    async def delete_group(self, group_id: str, actor: str) -> GroupRecord:
        pool = await self._pool_()
        async with pool.acquire() as conn, conn.transaction():
            g = await self._group_row(conn, group_id)
            if g is None:
                raise GroupNotFoundError(group_id)
            if g.built_in:
                raise GroupInvariantError(
                    f"the built-in {group_id!r} group cannot be deleted"
                )
            if not g.active:
                return g
            deleted = _mark_group_deleted(g, actor, _now())
            await conn.execute(
                "UPDATE groups SET deleted_at = $1, deleted_by = $2 WHERE id = $3",
                deleted.deleted_at, deleted.deleted_by, group_id,
            )
        return deleted

    async def get_group(self, group_id: str) -> GroupRecord | None:
        pool = await self._pool_()
        async with pool.acquire() as conn:
            return await self._group_row(conn, group_id)

    async def list_groups_owned_by(self, subject: str) -> list[GroupRecord]:
        pool = await self._pool_()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                _GROUP_SELECT
                + " WHERE deleted_at = '' AND owner_subject = $1"
                + _GROUP_ORDER,
                subject,
            )
        return [_row_to_group(tuple(r)) for r in rows]

    async def list_groups_for_member(self, subject: str) -> list[GroupRecord]:
        pool = await self._pool_()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                _GROUP_SELECT + " g WHERE g.deleted_at = '' AND g.built_in = 0 "
                "AND EXISTS (SELECT 1 FROM group_members gm "
                "WHERE gm.group_id = g.id AND gm.subject = $1 AND gm.removed_at = '')"
                + _GROUP_ORDER.replace(" ORDER BY ", " ORDER BY g."),
                subject,
            )
        return [_row_to_group(tuple(r)) for r in rows]

    async def add_member(
        self, group_id: str, subject: str, added_by: str
    ) -> GroupMemberRecord:
        await self.ensure_provisional(subject, _issuer_of(subject))
        pool = await self._pool_()
        async with pool.acquire() as conn, conn.transaction():
            g = await self._group_row(conn, group_id)
            if g is None or not g.active:
                raise GroupNotFoundError(group_id)
            if g.built_in:
                raise GroupInvariantError(
                    f"members cannot be added to the built-in {group_id!r} group"
                )
            if await conn.fetchrow(
                "SELECT 1 FROM groups WHERE id = $1", subject
            ) is not None:
                raise GroupInvariantError(
                    "no nesting: a group cannot be a member of a group"
                )
            if await conn.fetchrow(
                "SELECT 1 FROM group_members "
                "WHERE group_id = $1 AND subject = $2 AND removed_at = ''",
                group_id, subject,
            ) is not None:
                raise GroupInvariantError(
                    f"{subject!r} is already an active member of {group_id!r}"
                )
            rec = _new_member(group_id, subject, added_by)
            await self._insert_member(conn, rec)
        return rec

    async def remove_member(
        self, group_id: str, subject: str, removed_by: str = ""
    ) -> GroupMemberRecord | None:
        pool = await self._pool_()
        async with pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                _MEMBER_SELECT
                + " WHERE group_id = $1 AND subject = $2 AND removed_at = ''",
                group_id, subject,
            )
            if row is None:
                return None
            removed = _mark_removed(_row_to_member(tuple(row)), removed_by, _now())
            await conn.execute(
                "UPDATE group_members SET removed_at = $1, removed_by = $2 WHERE id = $3",
                removed.removed_at, removed.removed_by, removed.id,
            )
        return removed

    async def list_members(self, group_id: str) -> list[GroupMemberRecord]:
        pool = await self._pool_()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                _MEMBER_SELECT
                + " WHERE group_id = $1 AND removed_at = ''"
                + _MEMBER_ORDER,
                group_id,
            )
        return [_row_to_member(tuple(r)) for r in rows]

    async def is_member(self, subject: str, group_id: str) -> bool:
        if group_id == PUBLIC_GROUP:
            return True
        pool = await self._pool_()
        async with pool.acquire() as conn:
            g = await self._group_row(conn, group_id)
            if g is None or not g.active:
                return False
            row = await conn.fetchrow(
                "SELECT 1 FROM group_members "
                "WHERE group_id = $1 AND subject = $2 AND removed_at = ''",
                group_id, subject,
            )
        return row is not None

    async def groups_for_subject(self, subject: str) -> set[str]:
        pool = await self._pool_()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT gm.group_id FROM group_members gm "
                "JOIN groups g ON g.id = gm.group_id "
                "WHERE gm.subject = $1 AND gm.removed_at = '' "
                "AND g.deleted_at = '' AND g.built_in = 0",
                subject,
            )
        return {PUBLIC_GROUP} | {r[0] for r in rows}

    async def grants_for_subject(self, subject: str) -> list[Any]:
        pool = await self._pool_()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                _SHARE_SELECT
                + " WHERE revoked_at = '' AND ("
                "(grantee_type = 'user' AND grantee_id = $1) "
                "OR (grantee_type = 'group' AND grantee_id = 'public') "
                "OR (grantee_type = 'group' AND grantee_id IN ("
                "  SELECT gm.group_id FROM group_members gm "
                "  JOIN groups g ON g.id = gm.group_id "
                "  WHERE gm.subject = $1 AND gm.removed_at = '' "
                "  AND g.deleted_at = '' AND g.built_in = 0)))"
                + _SHARE_ORDER,
                subject,
            )
        return [_row_to_share(tuple(r)) for r in rows]


# --------------------------------------------------------------------------- #
# Construction + module-level singleton (user_store's get/set/reset trio)
# --------------------------------------------------------------------------- #


def make_group_store(cfg: Any = settings) -> GroupStore:
    """Build the configured group store. Selection rides the SAME
    ``user_store_backend`` / ``user_store_path`` / ``user_store_dsn`` settings
    as users/shares — the file/DSN IS the tenant's ACL database (no new
    knobs), so all four tables share one store object."""
    backend = (getattr(cfg, "user_store_backend", MEMORY) or MEMORY).lower()
    if backend == SQLITE:
        return SqliteGroupStore(cfg.user_store_path)
    if backend == POSTGRES:
        return PostgresGroupStore(getattr(cfg, "user_store_dsn", "") or cfg.postgres_dsn)
    if backend != MEMORY:
        log.warning("unknown user_store_backend %r; falling back to 'memory'", backend)
    return InMemoryGroupStore()


_store: GroupStore | None = None
_built_for: tuple | None = None


def _settings_key() -> tuple:
    return (
        (settings.user_store_backend or MEMORY).lower(),
        getattr(settings, "user_store_path", ""),
        getattr(settings, "user_store_dsn", ""),
    )


def get_group_store() -> GroupStore:
    """The process-wide group store, built on first use and rebuilt when the
    ``user_store_*`` settings change. NOTE for wiring (part 2): because a
    GroupStore is-an AclStore is-a UserStore, lifespan should build THIS store
    once and install it as all three singletons (``set_user_store`` +
    ``set_acl_store`` + ``set_group_store``) — one object, one sqlite file /
    asyncpg pool, one ``close()``, four tables."""
    global _store, _built_for
    key = _settings_key()
    if _store is None or _built_for != key:
        _store = make_group_store(settings)
        _built_for = key
    return _store


def set_group_store(store: GroupStore | None) -> None:
    """Install ``store`` explicitly (tests, and callers that build their own)."""
    global _store, _built_for
    _store = store
    _built_for = _settings_key()


def reset_group_store() -> None:
    """Drop the cached store so the next use rebuilds it from settings."""
    global _store, _built_for
    _store = None
    _built_for = None
