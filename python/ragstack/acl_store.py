"""Collection ACL store — ADR-0004 decisions 4-6, issue #243 part 1.

Shares live in the SAME per-tenant ACL database as the ``users`` table: the
store classes here *extend* the :mod:`ragstack.user_store` backends, so one
``user_store_backend`` / ``user_store_path`` / ``user_store_dsn`` triple
governs both tables (the file/DSN *is* the tenant's ACL database — no new
backend settings), one sqlite path / one asyncpg pool serves both, and a
future transaction spanning ``users`` + ``shares`` has a single connection
to run on.

Semantics (ADR-0004 decision 5/6):

- Permissions are ``read < write < owner``; ``grant_option`` is an orthogonal
  boolean (SQL's ``WITH GRANT OPTION``), not a level.
- ``owner`` is grantable to users only, and there is exactly ONE active owner
  row per collection (its own partial unique index).
- The built-in ``public`` group may hold ``read`` only.
- Revocation is SOFT — ``revoked_at``/``revoked_by``, never DELETE — and
  RECURSIVE: revoking a share also revokes every active share whose
  ``granted_by`` chain leads back to a grantee who thereby lost all access to
  the collection. A grantee who retains access through an independent active
  share keeps their onward grants (and nothing revoked is ever resurrected).
- Ownership transfer is the ADR's revoke+grant pair, atomic, and deliberately
  NON-cascading — handing a collection over must not destroy its share graph.

House pattern (:mod:`ragstack.user_store` / :mod:`ragstack.collection_store`):
one shared-dialect DDL string (TEXT/INTEGER only, ISO-8601 UTC text
timestamps, INTEGER 0/1 booleans), additive-only ``ensure_columns``
migration, semantics centralized in pure helpers shared by every backend,
``memory``/``sqlite``/``postgres`` backends. "Active" is encoded as
``revoked_at = ''`` (the house empty-string convention, standing in for the
ADR sketch's ``IS NULL``).

MUST import nothing from ``ragstack.api.*`` — this module sits below the API
(authz.py evaluates against it from the request path).
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import uuid
from contextlib import closing
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from ragstack.collection_store import (
    ensure_columns_postgres,
    ensure_columns_sqlite,
)
from ragstack.config import settings
from ragstack.user_store import (
    MEMORY,
    POSTGRES,
    SQLITE,
    InMemoryUserStore,
    PostgresUserStore,
    SqliteUserStore,
    _now,
)

log = logging.getLogger(__name__)

# Vocabulary (mirrored in the DDL contract comment below).
GRANTEE_USER = "user"
GRANTEE_GROUP = "group"
VALID_GRANTEE_TYPES = frozenset({GRANTEE_USER, GRANTEE_GROUP})

PERM_READ = "read"
PERM_WRITE = "write"
PERM_OWNER = "owner"
VALID_PERMISSIONS = frozenset({PERM_READ, PERM_WRITE, PERM_OWNER})

#: The built-in world-readable group (ADR-0004 decision 4). Its membership
#: test short-circuits to true for every caller; it may hold ``read`` only.
PUBLIC_GROUP = "public"


class ShareInvariantError(ValueError):
    """A grant/revoke would violate an ACL invariant (duplicate active grant,
    second owner, owner-to-group, public-write, unknown vocabulary, ...)."""


class ShareNotFoundError(KeyError):
    """The referenced share id does not exist."""


class ShareRecord(BaseModel):
    """One row of the ``shares`` table. ``revoked_at == ''`` means active."""

    id: str
    collection_id: str
    grantee_type: str  # 'user' | 'group'
    grantee_id: str  # subject (user) or group id; 'public' is the built-in group
    permission: str  # 'read' | 'write' | 'owner'
    grant_option: bool = False  # stored as INTEGER 0/1
    granted_by: str = ""  # subject of the grantor ('system:backfill' for startup backfill)
    granted_at: str = ""  # ISO-8601 UTC
    revoked_by: str = ""
    revoked_at: str = ""  # '' = active (partial indexes filter on this)

    @property
    def active(self) -> bool:
        return self.revoked_at == ""


# --------------------------------------------------------------------------- #
# DDL — the published contract
# --------------------------------------------------------------------------- #

# CONTRACT — this DDL is the published cross-consumer schema for collection
# ACLs (issue #243 design note 3). Any other consumer (e.g. a Go client)
# codes against exactly this shape. Changes are ADDITIVE ONLY, via
# _SHARES_COLUMNS + ensure_columns_* — never a column rename, retype, or
# drop, and never a DELETE on rows (revocation is soft, ADR-0004 decision 6).
# Dialect-shared verbatim by sqlite and postgres: TEXT/INTEGER only,
# ISO-8601 UTC text timestamps, INTEGER 0/1 booleans, '' (empty string) for
# "not revoked". Vocabulary: grantee_type in ('user','group'); permission in
# ('read','write','owner'); grantee_id 'public' is the built-in group.
_SHARES_DDL = (
    "CREATE TABLE IF NOT EXISTS shares ("
    "  id TEXT PRIMARY KEY,"
    "  collection_id TEXT NOT NULL,"
    "  grantee_type TEXT NOT NULL,"
    "  grantee_id TEXT NOT NULL,"
    "  permission TEXT NOT NULL,"
    "  grant_option INTEGER NOT NULL DEFAULT 0,"
    "  granted_by TEXT NOT NULL DEFAULT '',"
    "  granted_at TEXT NOT NULL DEFAULT '',"
    "  revoked_by TEXT NOT NULL DEFAULT '',"
    "  revoked_at TEXT NOT NULL DEFAULT ''"
    ")"
)

# Partial unique indexes (identical syntax in sqlite >= 3.8 and postgres).
# ensure_columns can only ADD COLUMN, so constraints live here as their own
# IF NOT EXISTS statements, run in the same DDL block as the CREATE TABLE.
_SHARES_INDEX_ACTIVE = (
    "CREATE UNIQUE INDEX IF NOT EXISTS shares_active "
    "ON shares (collection_id, grantee_type, grantee_id, permission) "
    "WHERE revoked_at = ''"
)
_SHARES_INDEX_OWNER = (
    "CREATE UNIQUE INDEX IF NOT EXISTS shares_active_owner "
    "ON shares (collection_id) "
    "WHERE permission = 'owner' AND revoked_at = ''"
)
_SHARES_INDEXES = (_SHARES_INDEX_ACTIVE, _SHARES_INDEX_OWNER)

# Advisory-lock key serializing the shares DDL across processes. Postgres's
# CREATE TABLE/INDEX IF NOT EXISTS is racy under concurrent execution (the loser
# can die on pg_class's own unique index), and the startup probe now touches the
# pool on every boot — so two workers booting at once would otherwise be able to
# fail one startup nondeterministically. Any stable 64-bit constant works; this
# one is arbitrary but fixed ("ragstack shares DDL").
_SHARES_DDL_LOCK_KEY = 0x7261675F73686172  # b"rag_shar" as an int64

# Column -> DDL fragment for additive migration of a table created by an older
# build. Every entry MUST be nullable or defaulted.
_SHARES_COLUMNS: dict[str, str] = {
    "collection_id": "TEXT NOT NULL DEFAULT ''",
    "grantee_type": "TEXT NOT NULL DEFAULT ''",
    "grantee_id": "TEXT NOT NULL DEFAULT ''",
    "permission": "TEXT NOT NULL DEFAULT ''",
    "grant_option": "INTEGER NOT NULL DEFAULT 0",
    "granted_by": "TEXT NOT NULL DEFAULT ''",
    "granted_at": "TEXT NOT NULL DEFAULT ''",
    "revoked_by": "TEXT NOT NULL DEFAULT ''",
    "revoked_at": "TEXT NOT NULL DEFAULT ''",
}

_SHARE_COLUMNS = (
    "id", "collection_id", "grantee_type", "grantee_id", "permission",
    "grant_option", "granted_by", "granted_at", "revoked_by", "revoked_at",
)
_SHARE_SELECT = f"SELECT {', '.join(_SHARE_COLUMNS)} FROM shares"
# Stable audit order: grant time ascends with insertion, id breaks ties.
_SHARE_ORDER = " ORDER BY granted_at, id"


def _share_to_row(rec: ShareRecord) -> tuple:
    return (
        rec.id, rec.collection_id, rec.grantee_type, rec.grantee_id,
        rec.permission, 1 if rec.grant_option else 0, rec.granted_by,
        rec.granted_at, rec.revoked_by, rec.revoked_at,
    )


def _row_to_share(row: Any) -> ShareRecord:
    (sid, coll, gtype, gid, perm, opt, by, at, rby, rat) = tuple(row)
    return ShareRecord(
        id=sid, collection_id=coll, grantee_type=gtype, grantee_id=gid,
        permission=perm, grant_option=bool(opt), granted_by=by,
        granted_at=at, revoked_by=rby, revoked_at=rat,
    )


# --------------------------------------------------------------------------- #
# Pure semantics — shared by every backend so behaviour cannot drift
# --------------------------------------------------------------------------- #


def _new_share(
    collection_id: str,
    grantee_type: str,
    grantee_id: str,
    permission: str,
    granted_by: str,
    grant_option: bool,
) -> ShareRecord:
    return ShareRecord(
        id=uuid.uuid4().hex,
        collection_id=collection_id,
        grantee_type=grantee_type,
        grantee_id=grantee_id,
        permission=permission,
        grant_option=grant_option,
        granted_by=granted_by,
        granted_at=_now(),
    )


def _check_grant(active: list[ShareRecord], rec: ShareRecord) -> None:
    """The one place grant invariants live (ADR-0004 decisions 4/5).

    ``active`` is the list of currently-active shares for ``rec.collection_id``.
    Raises :class:`ShareInvariantError` on any violation. The SQL backends
    additionally rely on the partial unique indexes to catch races the
    read-then-check window admits.
    """
    if not rec.collection_id:
        raise ShareInvariantError("collection_id must be non-empty")
    if not rec.grantee_id:
        raise ShareInvariantError("grantee_id must be non-empty")
    if rec.grantee_type not in VALID_GRANTEE_TYPES:
        raise ShareInvariantError(
            f"grantee_type {rec.grantee_type!r} is not one of {sorted(VALID_GRANTEE_TYPES)}"
        )
    if rec.permission not in VALID_PERMISSIONS:
        raise ShareInvariantError(
            f"permission {rec.permission!r} is not one of {sorted(VALID_PERMISSIONS)}"
        )
    if rec.grantee_id == PUBLIC_GROUP:
        # 'public' is the built-in group and may hold read only (decision 4);
        # a same-named user grantee would shadow it, so reject that outright.
        if rec.grantee_type != GRANTEE_GROUP:
            raise ShareInvariantError("'public' is the built-in group, not a user")
        if rec.permission != PERM_READ:
            raise ShareInvariantError("the public group may hold 'read' only")
    if rec.permission == PERM_OWNER:
        if rec.grantee_type != GRANTEE_USER:
            raise ShareInvariantError("owner is grantable to users only, never to a group")
        if any(r.permission == PERM_OWNER for r in active):
            raise ShareInvariantError(
                f"collection {rec.collection_id!r} already has an active owner"
            )
    if any(
        r.grantee_type == rec.grantee_type
        and r.grantee_id == rec.grantee_id
        and r.permission == rec.permission
        for r in active
    ):
        raise ShareInvariantError(
            f"active {rec.permission!r} share for {rec.grantee_type}:{rec.grantee_id} "
            f"on {rec.collection_id!r} already exists"
        )


def _revocation_plan(active: list[ShareRecord], root_id: str) -> list[str]:
    """Ids to revoke when ``root_id`` is revoked (ADR-0004 decision 5).

    ``active`` is the active share set of the root's collection. The root goes
    unconditionally; every other share survives only if it is **grounded**: its
    ``granted_by`` chain reaches, without passing through the root, a grantor
    whose own access does not depend on the shares being revoked. Grounded
    support is computed as a least fixpoint (not "revoke while a grantor lost
    everything", which is a greatest fixpoint): a mutually-referential cycle —
    A granted by B, B granted by A, no surviving external root — supports
    nothing and is revoked with the root, so a grantee cannot pre-arrange a
    mutual grant to outlive revocation. Intrinsically grounded rows are those
    whose grantor is not itself a user grantee on the collection (the system
    backfill, an owner-of-record acting before holding a row) and self-grants
    (the owner row records ``granted_by == grantee``). A grantor who keeps
    access through an independent grounded share keeps their onward grants
    (partial overlap), and a row once planned for revocation is never
    un-planned (no resurrection). Deterministic order: root first, then audit
    order.
    """
    by_id = {r.id: r for r in active}
    if root_id not in by_id:
        return []
    # Subjects whose access is even in question: user grantees of active shares.
    user_grantees = {
        r.grantee_id for r in active if r.grantee_type == GRANTEE_USER
    }
    supported: set[str] = set()  # share ids with grounded support
    supported_subjects: set[str] = set()  # user grantees holding a grounded share
    changed = True
    while changed:
        changed = False
        for r in active:
            if r.id == root_id or r.id in supported:
                continue
            grounded = (
                # THE ACTIVE OWNER ROW IS NEVER COLLATERAL DAMAGE. The
                # one-active-owner invariant outranks the grounding fixpoint:
                # a collection with zero active owners cannot be managed,
                # cannot be transferred (that route 409s on a missing owner),
                # and is NOT repaired by the startup backfill, which skips any
                # collection whose history ever contained an owner row. The
                # only exit left is an admin DELETE.
                #
                # This was latent until ownership TRANSFER existed. Every owner
                # row used to be a self-grant (`write_owner_row`) or backfilled
                # by a non-grantee, so the two clauses below already grounded
                # it. `transfer_owner` mints the new owner row with
                # `granted_by=actor`, which is the first owner row a cascade
                # can reach — after which revoking ANY share granted by that
                # actor could take ownership with it.
                r.permission == PERM_OWNER
                or r.granted_by not in user_grantees  # external grantor (system/…)
                or r.granted_by in supported_subjects  # grantor's access is grounded
                or (r.grantee_type == GRANTEE_USER and r.granted_by == r.grantee_id)
            )
            if grounded:
                supported.add(r.id)
                if r.grantee_type == GRANTEE_USER:
                    supported_subjects.add(r.grantee_id)
                changed = True
    doomed = {r.id for r in active if r.id not in supported}
    ordered = [root_id] + sorted(
        (i for i in doomed if i != root_id),
        key=lambda i: (by_id[i].granted_at, i),
    )
    return ordered


def _mark_revoked(rec: ShareRecord, revoked_by: str, stamp: str) -> ShareRecord:
    return rec.model_copy(update={"revoked_by": revoked_by, "revoked_at": stamp})


# --------------------------------------------------------------------------- #
# Protocol
# --------------------------------------------------------------------------- #


@runtime_checkable
class AclStore(Protocol):
    """The shares table's API (ADR-0004 decisions 4-6). Every implementation
    also satisfies :class:`ragstack.user_store.UserStore` — one store object,
    one database, both tables."""

    async def grant(
        self,
        collection_id: str,
        grantee_type: str,
        grantee_id: str,
        permission: str,
        granted_by: str,
        grant_option: bool = False,
    ) -> ShareRecord:
        """Write one active share row. Raises :class:`ShareInvariantError` on
        any invariant violation (see :func:`_check_grant`)."""
        ...

    async def revoke(self, share_id: str, revoked_by: str) -> list[ShareRecord]:
        """Soft-revoke ``share_id`` and, recursively, every active share whose
        ``granted_by`` chain leads back to a grantee losing all access
        (:func:`_revocation_plan`). Never deletes. Returns the newly revoked
        rows (root first); ``[]`` if the row was already revoked. Raises
        :class:`ShareNotFoundError` for an unknown id."""
        ...

    async def owner_of(self, collection_id: str) -> str | None:
        """Subject of the single active owner row, or ``None``."""
        ...

    async def shares_for(
        self, collection_id: str, include_revoked: bool = False
    ) -> list[ShareRecord]: ...

    async def grants_for_subject(self, subject: str) -> list[ShareRecord]:
        """Active shares granted to ``subject`` directly or to the built-in
        ``public`` group (whose membership is constant-true; real groups
        arrive with #245)."""
        ...

    async def transfer_owner(
        self, collection_id: str, new_owner: str, actor: str
    ) -> ShareRecord:
        """ADR-0004's reassignment pair: atomically revoke the current owner
        row (non-cascading) and grant ``owner`` to ``new_owner``. Raises
        :class:`ShareInvariantError` when there is no active owner or the new
        grant is invalid — in which case nothing changes."""
        ...

    async def close(self) -> None: ...


# --------------------------------------------------------------------------- #
# Backends — each extends its user_store sibling (same database, both tables)
# --------------------------------------------------------------------------- #


class InMemoryAclStore(InMemoryUserStore):
    """Process-local users + shares. Loses every grant on restart — dev/tests
    only; validate-time policy for production lands with the API wiring."""

    def __init__(self) -> None:
        super().__init__()
        self._shares: dict[str, ShareRecord] = {}

    def _active(self, collection_id: str) -> list[ShareRecord]:
        return [
            r for r in self._shares.values()
            if r.collection_id == collection_id and r.active
        ]

    async def grant(
        self,
        collection_id: str,
        grantee_type: str,
        grantee_id: str,
        permission: str,
        granted_by: str,
        grant_option: bool = False,
    ) -> ShareRecord:
        async with self._lock:
            rec = _new_share(
                collection_id, grantee_type, grantee_id, permission, granted_by, grant_option
            )
            _check_grant(self._active(collection_id), rec)
            self._shares[rec.id] = rec
            return rec.model_copy(deep=True)

    async def revoke(self, share_id: str, revoked_by: str) -> list[ShareRecord]:
        async with self._lock:
            root = self._shares.get(share_id)
            if root is None:
                raise ShareNotFoundError(share_id)
            if not root.active:
                return []
            active = self._active(root.collection_id)
            stamp = _now()
            revoked = []
            for sid in _revocation_plan(active, share_id):
                self._shares[sid] = _mark_revoked(self._shares[sid], revoked_by, stamp)
                revoked.append(self._shares[sid].model_copy(deep=True))
            return revoked

    async def owner_of(self, collection_id: str) -> str | None:
        async with self._lock:
            for r in self._active(collection_id):
                if r.permission == PERM_OWNER:
                    return r.grantee_id
            return None

    async def shares_for(
        self, collection_id: str, include_revoked: bool = False
    ) -> list[ShareRecord]:
        async with self._lock:
            rows = [
                r for r in self._shares.values()
                if r.collection_id == collection_id and (include_revoked or r.active)
            ]
            rows.sort(key=lambda r: (r.granted_at, r.id))
            return [r.model_copy(deep=True) for r in rows]

    async def grants_for_subject(self, subject: str) -> list[ShareRecord]:
        async with self._lock:
            rows = [
                r for r in self._shares.values()
                if r.active and (
                    (r.grantee_type == GRANTEE_USER and r.grantee_id == subject)
                    or (r.grantee_type == GRANTEE_GROUP and r.grantee_id == PUBLIC_GROUP)
                )
            ]
            rows.sort(key=lambda r: (r.granted_at, r.id))
            return [r.model_copy(deep=True) for r in rows]

    async def transfer_owner(
        self, collection_id: str, new_owner: str, actor: str
    ) -> ShareRecord:
        async with self._lock:
            active = self._active(collection_id)
            current = next((r for r in active if r.permission == PERM_OWNER), None)
            if current is None:
                raise ShareInvariantError(
                    f"collection {collection_id!r} has no active owner to transfer from"
                )
            if current.grantee_id == new_owner:
                # IN the transaction, not in the router. The endpoint's
                # pre-check gives the nicer message, but two concurrent
                # transfers to the SAME subject both passed it and both
                # committed — revoking and re-inserting an identical owner row,
                # churning the audit trail with a handover that never happened.
                raise ShareInvariantError(
                    f"{new_owner!r} already owns collection {collection_id!r}"
                )
            rec = _new_share(collection_id, GRANTEE_USER, new_owner, PERM_OWNER, actor, False)
            # Validate against the post-revoke state BEFORE mutating — atomic.
            _check_grant([r for r in active if r.id != current.id], rec)
            stamp = _now()
            self._shares[current.id] = _mark_revoked(current, actor, stamp)
            self._shares[rec.id] = rec
            return rec.model_copy(deep=True)


class SqliteAclStore(SqliteUserStore):
    """Durable single-host users + shares in one sqlite file.

    DDL (both tables + the partial unique indexes) runs synchronously in
    ``__init__`` — the same place the startup probe already exercises — so a
    broken shares table fails at boot, not at first grant. Recursive revoke
    and ownership transfer each run inside ONE connection context
    (``with closing(...) as conn, conn:`` commits on success, rolls back on
    exception), which is the sqlite transaction boundary.
    """

    def __init__(self, path: str) -> None:
        super().__init__(path)  # users DDL
        with closing(self._connect()) as conn, conn:
            conn.execute(_SHARES_DDL)
            ensure_columns_sqlite(conn, "shares", _SHARES_COLUMNS)
            for stmt in _SHARES_INDEXES:
                conn.execute(stmt)

    @staticmethod
    def _active_rows(conn: sqlite3.Connection, collection_id: str) -> list[ShareRecord]:
        rows = conn.execute(
            _SHARE_SELECT + " WHERE collection_id = ? AND revoked_at = ''" + _SHARE_ORDER,
            (collection_id,),
        ).fetchall()
        return [_row_to_share(r) for r in rows]

    def _insert_share(self, conn: sqlite3.Connection, rec: ShareRecord) -> None:
        try:
            conn.execute(
                f"INSERT INTO shares ({', '.join(_SHARE_COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(_SHARE_COLUMNS))})",
                _share_to_row(rec),
            )
        except sqlite3.IntegrityError as e:
            # The partial unique indexes are the race-window backstop for
            # duplicate-active-share and second-owner.
            raise ShareInvariantError(str(e)) from e

    def _grant_sync(
        self,
        collection_id: str,
        grantee_type: str,
        grantee_id: str,
        permission: str,
        granted_by: str,
        grant_option: bool,
    ) -> ShareRecord:
        with closing(self._connect()) as conn, conn:
            rec = _new_share(
                collection_id, grantee_type, grantee_id, permission, granted_by, grant_option
            )
            _check_grant(self._active_rows(conn, collection_id), rec)
            self._insert_share(conn, rec)
        return rec

    def _revoke_sync(self, share_id: str, revoked_by: str) -> list[ShareRecord]:
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                _SHARE_SELECT + " WHERE id = ?", (share_id,)
            ).fetchone()
            if row is None:
                raise ShareNotFoundError(share_id)
            root = _row_to_share(row)
            if not root.active:
                return []
            active = self._active_rows(conn, root.collection_id)
            stamp = _now()
            revoked = []
            by_id = {r.id: r for r in active}
            for sid in _revocation_plan(active, share_id):
                conn.execute(
                    "UPDATE shares SET revoked_at = ?, revoked_by = ? WHERE id = ?",
                    (stamp, revoked_by, sid),
                )
                revoked.append(_mark_revoked(by_id[sid], revoked_by, stamp))
        return revoked

    def _owner_of_sync(self, collection_id: str) -> str | None:
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                "SELECT grantee_id FROM shares "
                "WHERE collection_id = ? AND permission = 'owner' AND revoked_at = ''",
                (collection_id,),
            ).fetchone()
        return row[0] if row is not None else None

    def _shares_for_sync(self, collection_id: str, include_revoked: bool) -> list[ShareRecord]:
        where = " WHERE collection_id = ?"
        if not include_revoked:
            where += " AND revoked_at = ''"
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(
                _SHARE_SELECT + where + _SHARE_ORDER, (collection_id,)
            ).fetchall()
        return [_row_to_share(r) for r in rows]

    def _grants_for_subject_sync(self, subject: str) -> list[ShareRecord]:
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(
                _SHARE_SELECT
                + " WHERE revoked_at = '' AND ("
                "(grantee_type = 'user' AND grantee_id = ?) "
                "OR (grantee_type = 'group' AND grantee_id = 'public'))"
                + _SHARE_ORDER,
                (subject,),
            ).fetchall()
        return [_row_to_share(r) for r in rows]

    def _transfer_owner_sync(
        self, collection_id: str, new_owner: str, actor: str
    ) -> ShareRecord:
        with closing(self._connect()) as conn, conn:
            active = self._active_rows(conn, collection_id)
            current = next((r for r in active if r.permission == PERM_OWNER), None)
            if current is None:
                raise ShareInvariantError(
                    f"collection {collection_id!r} has no active owner to transfer from"
                )
            if current.grantee_id == new_owner:
                # IN the transaction, not in the router. The endpoint's
                # pre-check gives the nicer message, but two concurrent
                # transfers to the SAME subject both passed it and both
                # committed — revoking and re-inserting an identical owner row,
                # churning the audit trail with a handover that never happened.
                raise ShareInvariantError(
                    f"{new_owner!r} already owns collection {collection_id!r}"
                )
            rec = _new_share(collection_id, GRANTEE_USER, new_owner, PERM_OWNER, actor, False)
            _check_grant([r for r in active if r.id != current.id], rec)
            # Revoke-then-grant inside one transaction: an exception (including
            # an index-level IntegrityError) rolls the revoke back too.
            conn.execute(
                "UPDATE shares SET revoked_at = ?, revoked_by = ? WHERE id = ?",
                (_now(), actor, current.id),
            )
            self._insert_share(conn, rec)
        return rec

    async def grant(
        self,
        collection_id: str,
        grantee_type: str,
        grantee_id: str,
        permission: str,
        granted_by: str,
        grant_option: bool = False,
    ) -> ShareRecord:
        return await asyncio.to_thread(
            self._grant_sync,
            collection_id, grantee_type, grantee_id, permission, granted_by, grant_option,
        )

    async def revoke(self, share_id: str, revoked_by: str) -> list[ShareRecord]:
        return await asyncio.to_thread(self._revoke_sync, share_id, revoked_by)

    async def owner_of(self, collection_id: str) -> str | None:
        return await asyncio.to_thread(self._owner_of_sync, collection_id)

    async def shares_for(
        self, collection_id: str, include_revoked: bool = False
    ) -> list[ShareRecord]:
        return await asyncio.to_thread(self._shares_for_sync, collection_id, include_revoked)

    async def grants_for_subject(self, subject: str) -> list[ShareRecord]:
        return await asyncio.to_thread(self._grants_for_subject_sync, subject)

    async def transfer_owner(
        self, collection_id: str, new_owner: str, actor: str
    ) -> ShareRecord:
        return await asyncio.to_thread(
            self._transfer_owner_sync, collection_id, new_owner, actor
        )


class PostgresAclStore(PostgresUserStore):
    """Durable, multi-process users + shares on one asyncpg pool — the backend
    a multi-instance deployment requires (sqlite is per-host; three prod
    instances on sqlite means three divergent ACL databases).

    Shares DDL runs in the same lazy pool bootstrap as the users DDL
    (:meth:`_pool_`), so the existing startup probe covers both tables.
    Recursive revoke and transfer run in explicit transactions — asyncpg
    autocommits per statement otherwise.
    """

    def __init__(self, dsn: str, min_size: int = 1, max_size: int = 5) -> None:
        super().__init__(dsn, min_size=min_size, max_size=max_size)
        self._shares_ready = False
        self._shares_ddl_lock = asyncio.Lock()

    async def _pool_(self) -> Any:
        pool = await super()._pool_()  # users DDL on first call
        if not self._shares_ready:
            async with self._shares_ddl_lock:
                if not self._shares_ready:
                    async with pool.acquire() as conn, conn.transaction():
                        # Cross-process serialization: IF NOT EXISTS DDL races
                        # under concurrency (duplicate-key on pg_class); the
                        # transaction-scoped advisory lock makes the second
                        # booting worker wait instead of crash. The asyncio lock
                        # above only serializes coroutines in THIS process.
                        await conn.execute(
                            "SELECT pg_advisory_xact_lock($1)", _SHARES_DDL_LOCK_KEY
                        )
                        await conn.execute(_SHARES_DDL)
                        await ensure_columns_postgres(conn, "shares", _SHARES_COLUMNS)
                        for stmt in _SHARES_INDEXES:
                            await conn.execute(stmt)
                    self._shares_ready = True
        return pool

    @staticmethod
    async def _active_rows(conn: Any, collection_id: str) -> list[ShareRecord]:
        rows = await conn.fetch(
            _SHARE_SELECT + " WHERE collection_id = $1 AND revoked_at = ''" + _SHARE_ORDER,
            collection_id,
        )
        return [_row_to_share(tuple(r)) for r in rows]

    @staticmethod
    async def _insert_share(conn: Any, rec: ShareRecord) -> None:
        placeholders = ", ".join(f"${i + 1}" for i in range(len(_SHARE_COLUMNS)))
        try:
            await conn.execute(
                f"INSERT INTO shares ({', '.join(_SHARE_COLUMNS)}) VALUES ({placeholders})",
                *_share_to_row(rec),
            )
        except Exception as e:  # asyncpg.UniqueViolationError, without the import
            if type(e).__name__ == "UniqueViolationError":
                raise ShareInvariantError(str(e)) from e
            raise

    async def grant(
        self,
        collection_id: str,
        grantee_type: str,
        grantee_id: str,
        permission: str,
        granted_by: str,
        grant_option: bool = False,
    ) -> ShareRecord:
        pool = await self._pool_()
        async with pool.acquire() as conn, conn.transaction():
            rec = _new_share(
                collection_id, grantee_type, grantee_id, permission, granted_by, grant_option
            )
            _check_grant(await self._active_rows(conn, collection_id), rec)
            await self._insert_share(conn, rec)
        return rec

    async def revoke(self, share_id: str, revoked_by: str) -> list[ShareRecord]:
        pool = await self._pool_()
        async with pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(_SHARE_SELECT + " WHERE id = $1", share_id)
            if row is None:
                raise ShareNotFoundError(share_id)
            root = _row_to_share(tuple(row))
            if not root.active:
                return []
            active = await self._active_rows(conn, root.collection_id)
            stamp = _now()
            revoked = []
            by_id = {r.id: r for r in active}
            for sid in _revocation_plan(active, share_id):
                await conn.execute(
                    "UPDATE shares SET revoked_at = $1, revoked_by = $2 WHERE id = $3",
                    stamp, revoked_by, sid,
                )
                revoked.append(_mark_revoked(by_id[sid], revoked_by, stamp))
        return revoked

    async def owner_of(self, collection_id: str) -> str | None:
        pool = await self._pool_()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT grantee_id FROM shares "
                "WHERE collection_id = $1 AND permission = 'owner' AND revoked_at = ''",
                collection_id,
            )
        return row[0] if row is not None else None

    async def shares_for(
        self, collection_id: str, include_revoked: bool = False
    ) -> list[ShareRecord]:
        where = " WHERE collection_id = $1"
        if not include_revoked:
            where += " AND revoked_at = ''"
        pool = await self._pool_()
        async with pool.acquire() as conn:
            rows = await conn.fetch(_SHARE_SELECT + where + _SHARE_ORDER, collection_id)
        return [_row_to_share(tuple(r)) for r in rows]

    async def grants_for_subject(self, subject: str) -> list[ShareRecord]:
        pool = await self._pool_()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                _SHARE_SELECT
                + " WHERE revoked_at = '' AND ("
                "(grantee_type = 'user' AND grantee_id = $1) "
                "OR (grantee_type = 'group' AND grantee_id = 'public'))"
                + _SHARE_ORDER,
                subject,
            )
        return [_row_to_share(tuple(r)) for r in rows]

    async def transfer_owner(
        self, collection_id: str, new_owner: str, actor: str
    ) -> ShareRecord:
        pool = await self._pool_()
        async with pool.acquire() as conn, conn.transaction():
            active = await self._active_rows(conn, collection_id)
            current = next((r for r in active if r.permission == PERM_OWNER), None)
            if current is None:
                raise ShareInvariantError(
                    f"collection {collection_id!r} has no active owner to transfer from"
                )
            if current.grantee_id == new_owner:
                # IN the transaction, not in the router. The endpoint's
                # pre-check gives the nicer message, but two concurrent
                # transfers to the SAME subject both passed it and both
                # committed — revoking and re-inserting an identical owner row,
                # churning the audit trail with a handover that never happened.
                raise ShareInvariantError(
                    f"{new_owner!r} already owns collection {collection_id!r}"
                )
            rec = _new_share(collection_id, GRANTEE_USER, new_owner, PERM_OWNER, actor, False)
            _check_grant([r for r in active if r.id != current.id], rec)
            await conn.execute(
                "UPDATE shares SET revoked_at = $1, revoked_by = $2 WHERE id = $3",
                _now(), actor, current.id,
            )
            await self._insert_share(conn, rec)
        return rec


# --------------------------------------------------------------------------- #
# Construction + module-level singleton (user_store's get/set/reset trio)
# --------------------------------------------------------------------------- #


def make_acl_store(cfg: Any = settings) -> AclStore:
    """Build the configured ACL store. Selection rides the existing
    ``user_store_backend`` / ``user_store_path`` / ``user_store_dsn`` settings
    — the user-store file/DSN IS the tenant's ACL database (no new knobs)."""
    backend = (getattr(cfg, "user_store_backend", MEMORY) or MEMORY).lower()
    if backend == SQLITE:
        return SqliteAclStore(cfg.user_store_path)
    if backend == POSTGRES:
        return PostgresAclStore(getattr(cfg, "user_store_dsn", "") or cfg.postgres_dsn)
    if backend != MEMORY:
        log.warning("unknown user_store_backend %r; falling back to 'memory'", backend)
    return InMemoryAclStore()


_store: AclStore | None = None
_built_for: tuple | None = None


def _settings_key() -> tuple:
    return (
        (settings.user_store_backend or MEMORY).lower(),
        getattr(settings, "user_store_path", ""),
        getattr(settings, "user_store_dsn", ""),
    )


def get_acl_store() -> AclStore:
    """The process-wide ACL store, built on first use and rebuilt when the
    ``user_store_*`` settings change. NOTE for wiring (part 2): every ACL
    store is also a full :class:`~ragstack.user_store.UserStore`, so lifespan
    should build THIS store and install it with ``set_user_store`` too — one
    object, one sqlite path / asyncpg pool, one ``close()``."""
    global _store, _built_for
    key = _settings_key()
    if _store is None or _built_for != key:
        _store = make_acl_store(settings)
        _built_for = key
    return _store


def set_acl_store(store: AclStore | None) -> None:
    """Install ``store`` explicitly (tests, and callers that build their own)."""
    global _store, _built_for
    _store = store
    _built_for = _settings_key()


def reset_acl_store() -> None:
    """Drop the cached store so the next use rebuilds it from settings."""
    global _store, _built_for
    _store = None
    _built_for = None
