"""Durable user-profile store — ADR-0004 decision 1.

The first verified token for a subject upserts a profile row
``users(subject, issuer, email, display_name, first_seen_at, last_seen_at)``.
``subject`` is the tenant string ``f"{issuer}:{sub}"`` — the same key the rest
of the system uses — which makes users enumerable and (later) shares
FK-checkable. A row grants nothing by itself.

The email/identity invariant (``ragstack.identity.oidc`` docstring): email is
profile metadata, reassignable, and must NEVER key the tenant or this table's
primary key. ``email_verified`` is a claim about the mailbox, not the account.

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

log = logging.getLogger(__name__)

# Backend vocabulary (mirror in config.user_store_backend's comment).
MEMORY = "memory"
SQLITE = "sqlite"
POSTGRES = "postgres"
VALID_USER_STORE_BACKENDS = frozenset({MEMORY, SQLITE, POSTGRES})


class UserRecord(BaseModel):
    """One profile row.

    ``subject`` is the primary key: the tenant string ``f"{issuer}:{sub}"``,
    never an email (emails are reassignable). ``provisional`` marks a row
    created *about* a user (e.g. as a share grantee) before that user's first
    verified login; the first :meth:`UserStore.upsert_seen` flips it off.
    """

    subject: str
    issuer: str = ""
    email: str = ""
    display_name: str = ""
    provisional: bool = False  # stored as INTEGER 0/1
    first_seen_at: str = ""  # ISO-8601 UTC; set once, on row creation
    last_seen_at: str = ""  # ISO-8601 UTC; re-stamped on every upsert_seen


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

    async def get(self, subject: str) -> UserRecord | None: ...

    async def list_users(self, limit: int = 100) -> list[UserRecord]:
        """Known users, oldest-first (stable ``first_seen_at`` order)."""
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
    it, so "never blank out existing data" cannot drift between dialects."""
    stamp = _now()
    if prior is None:
        return UserRecord(
            subject=subject, issuer=issuer, email=email,
            display_name=display_name, provisional=False,
            first_seen_at=stamp, last_seen_at=stamp,
        )
    return UserRecord(
        subject=subject,
        issuer=issuer or prior.issuer,
        email=email or prior.email,
        display_name=display_name or prior.display_name,
        provisional=False,
        first_seen_at=prior.first_seen_at or stamp,
        last_seen_at=stamp,
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

    async def close(self) -> None:
        """No resources to release."""


# --------------------------------------------------------------------------- #
# SQL backends
# --------------------------------------------------------------------------- #

# Shared verbatim by the sqlite and postgres stores — collection_store.py's
# discipline. TEXT for strings/timestamps (ISO-8601 UTC text, no TIMESTAMPTZ),
# INTEGER 0/1 for the provisional flag.
_USERS_DDL = (
    "CREATE TABLE IF NOT EXISTS users ("
    "  subject TEXT PRIMARY KEY,"
    "  issuer TEXT NOT NULL DEFAULT '',"
    "  email TEXT NOT NULL DEFAULT '',"
    "  display_name TEXT NOT NULL DEFAULT '',"
    "  provisional INTEGER NOT NULL DEFAULT 0,"
    "  first_seen_at TEXT NOT NULL DEFAULT '',"
    "  last_seen_at TEXT NOT NULL DEFAULT ''"
    ")"
)

# Column -> DDL fragment for additive migration of a table created by an older
# build. Every entry MUST be nullable or defaulted (sqlite's ALTER TABLE ADD
# COLUMN forbids NOT NULL without a default).
_USERS_COLUMNS: dict[str, str] = {
    "issuer": "TEXT NOT NULL DEFAULT ''",
    "email": "TEXT NOT NULL DEFAULT ''",
    "display_name": "TEXT NOT NULL DEFAULT ''",
    "provisional": "INTEGER NOT NULL DEFAULT 0",
    "first_seen_at": "TEXT NOT NULL DEFAULT ''",
    "last_seen_at": "TEXT NOT NULL DEFAULT ''",
}

_COLUMNS = (
    "subject", "issuer", "email", "display_name",
    "provisional", "first_seen_at", "last_seen_at",
)
_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM users"
# Oldest-first: first_seen_at ascends with registration, subject breaks ties.
_ORDER = " ORDER BY first_seen_at, subject"


def _record_to_row(rec: UserRecord) -> tuple:
    return (
        rec.subject, rec.issuer, rec.email, rec.display_name,
        1 if rec.provisional else 0, rec.first_seen_at, rec.last_seen_at,
    )


def _row_to_record(row: Any) -> UserRecord:
    subject, issuer, email, display_name, provisional, first_seen, last_seen = tuple(row)
    return UserRecord(
        subject=subject, issuer=issuer, email=email, display_name=display_name,
        provisional=bool(provisional), first_seen_at=first_seen, last_seen_at=last_seen,
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
        assignments = ", ".join(f"{c} = excluded.{c}" for c in _COLUMNS if c != "subject")
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

    def _get_sync(self, subject: str) -> UserRecord | None:
        with closing(self._connect()) as conn, conn:
            row = conn.execute(_SELECT + " WHERE subject = ?", (subject,)).fetchone()
        return _row_to_record(row) if row is not None else None

    def _list_sync(self, limit: int) -> list[UserRecord]:
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(_SELECT + _ORDER + " LIMIT ?", (limit,)).fetchall()
        return [_row_to_record(r) for r in rows]

    async def upsert_seen(
        self, subject: str, issuer: str, email: str = "", display_name: str = ""
    ) -> UserRecord:
        return await asyncio.to_thread(
            self._upsert_seen_sync, subject, issuer, email, display_name
        )

    async def ensure_provisional(self, subject: str, issuer: str) -> UserRecord:
        return await asyncio.to_thread(self._ensure_provisional_sync, subject, issuer)

    async def get(self, subject: str) -> UserRecord | None:
        return await asyncio.to_thread(self._get_sync, subject)

    async def list_users(self, limit: int = 100) -> list[UserRecord]:
        return await asyncio.to_thread(self._list_sync, limit)

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
                    async with pool.acquire() as conn:
                        await conn.execute(_USERS_DDL)
                        await ensure_columns_postgres(conn, "users", _USERS_COLUMNS)
                    self._pool = pool
        return self._pool

    async def upsert_seen(
        self, subject: str, issuer: str, email: str = "", display_name: str = ""
    ) -> UserRecord:
        placeholders = ", ".join(f"${i + 1}" for i in range(len(_COLUMNS)))
        assignments = ", ".join(f"{c} = excluded.{c}" for c in _COLUMNS if c != "subject")
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
