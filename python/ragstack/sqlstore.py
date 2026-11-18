"""Plumbing shared by the six SQL-backed stores.

``collection_store``, ``user_store``, ``acl_store``, ``group_store``,
``jobstore`` and ``grading.store`` each have a sqlite and a Postgres backend.
What they share — opening a sqlite connection, normalising a DSN, the lazy
asyncpg pool with its run-once DDL, recognising a unique violation — lives
here, so each store keeps only its own SQL.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from typing import Any

#: One busy timeout for every sqlite store: a connection that finds the
#: database locked retries for up to this long before raising ``database is
#: locked``. It does not cover every case: under WAL, a deferred transaction
#: that has already read and then tries to write gets SQLITE_BUSY at once,
#: without waiting, if another connection holds the write lock or has
#: committed since that read — use ``BEGIN IMMEDIATE`` where that matters.
SQLITE_BUSY_TIMEOUT_MS = 5000


def sqlite_connect(path: str, busy_timeout_ms: int = SQLITE_BUSY_TIMEOUT_MS) -> sqlite3.Connection:
    """Open ``path`` with the shared busy timeout, in WAL mode.

    Callers must wrap the result in ``closing(...)``: sqlite3's connection
    context manager (``with conn:``) commits but does *not* close, so the
    idiom is ``with closing(sqlite_connect(path)) as conn, conn:``.
    """
    conn = sqlite3.connect(path)
    # busy_timeout FIRST: setting journal_mode=WAL is itself a write that can
    # lose a race against another process opening the same fresh database, so
    # it needs the timeout too.
    conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def normalize_dsn(dsn: str) -> str:
    """Strip SQLAlchemy-style ``+driver`` suffixes asyncpg doesn't understand."""
    for marker in ("+asyncpg", "+psycopg2", "+psycopg"):
        dsn = dsn.replace(marker, "")
    return dsn


def is_unique_violation(exc: BaseException) -> bool:
    """True for asyncpg's ``UniqueViolationError``, matched by name so the
    stores need not import asyncpg."""
    return type(exc).__name__ == "UniqueViolationError"


#: A DDL step: given an acquired asyncpg connection, create what it needs.
DdlStep = Callable[[Any], Awaitable[None]]


class LazyPool:
    """An asyncpg pool created on first use, with its DDL run once.

    asyncpg needs a running loop, so the stores build this synchronously and
    the pool appears on the first :meth:`get`. Each DDL step runs once, in
    order, on its own connection. A step registered with a ``lock_key`` runs
    inside a transaction holding ``pg_advisory_xact_lock(lock_key)``, so two
    processes booting at once serialise instead of racing ``IF NOT EXISTS``
    DDL; a step without one runs on a plain connection. A step that raises is
    retried on the next :meth:`get`; steps before it stay done. Once
    :meth:`close` has been called the pool stays closed: :meth:`get` raises.
    """

    def __init__(
        self, dsn: str, *, store: str, min_size: int = 1, max_size: int = 5
    ) -> None:
        self.dsn = normalize_dsn(dsn)
        self._store = store
        self._min = min_size
        self._max = max_size
        self._pool: Any = None
        self._steps: list[tuple[DdlStep, int | None]] = []
        self._done = 0  # steps[:_done] have run
        self._closed = False
        self._lock = asyncio.Lock()

    def add_step(self, step: DdlStep, *, lock_key: int | None = None) -> None:
        """Append a DDL step; it runs after every step added before it.

        A step may use only the connection it is given. It must never call
        back into the store, :meth:`get` or anything that does: one
        non-reentrant lock covers every step, so a re-entrant call waits on
        itself and boot hangs.
        """
        self._steps.append((step, lock_key))

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError(f"postgres {self._store} store is closed")

    async def get(self) -> Any:
        """The pool, created and bootstrapped on the first call.

        Raises ``RuntimeError`` once :meth:`close` has been called.
        """
        self._check_open()
        pool = self._pool
        if pool is None or self._done < len(self._steps):
            async with self._lock:
                self._check_open()
                pool = self._pool
                if pool is None:
                    try:
                        import asyncpg
                    except ImportError as e:  # pragma: no cover
                        raise RuntimeError(
                            f"postgres {self._store} store requires asyncpg "
                            "(pip install ragstack[postgres])"
                        ) from e
                    pool = await asyncpg.create_pool(
                        self.dsn, min_size=self._min, max_size=self._max
                    )
                    self._pool = pool
                while self._done < len(self._steps):
                    step, lock_key = self._steps[self._done]
                    if lock_key is None:
                        async with pool.acquire() as conn:
                            await step(conn)
                    else:
                        async with pool.acquire() as conn, conn.transaction():
                            await conn.execute("SELECT pg_advisory_xact_lock($1)", lock_key)
                            await step(conn)
                    self._done += 1
        return pool

    async def close(self) -> None:
        """Close the pool if one was created. Closed stays closed: a later
        :meth:`get` raises instead of quietly opening a pool nothing would
        close. A bootstrap already running in :meth:`get` finishes first
        (close waits on the same lock); later calls fail at once."""
        self._closed = True
        async with self._lock:
            pool, self._pool = self._pool, None
            if pool is not None:
                await pool.close()
