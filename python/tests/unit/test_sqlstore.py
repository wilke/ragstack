"""ragstack.sqlstore — the plumbing the six SQL-backed stores share (#1033).

The busy-timeout test is the B6 / #1030 guard. Python's ``sqlite3.connect``
already defaults to ``timeout=5.0`` (busy_timeout 5000 ms), so a store that
never sets the pragma still waits. The test therefore pins
``sqlite3.connect``'s timeout to 0: only a store that sets busy_timeout
itself still reports the shared value.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import types
from contextlib import closing

import pytest

from ragstack.acl_store import SqliteAclStore
from ragstack.collection_store import SqliteCollectionStore
from ragstack.grading.store import SqliteGradingStore
from ragstack.group_store import SqliteGroupStore
from ragstack.jobstore import SqliteJobStore
from ragstack.sqlstore import (
    SQLITE_BUSY_TIMEOUT_MS,
    LazyPool,
    is_unique_violation,
    normalize_dsn,
    sqlite_connect,
)
from ragstack.user_store import SqliteUserStore

SQLITE_STORES = [
    SqliteCollectionStore,
    SqliteUserStore,
    SqliteAclStore,
    SqliteGroupStore,
    SqliteJobStore,
    SqliteGradingStore,
]


@pytest.fixture
def no_default_timeout(monkeypatch):
    """Make ``sqlite3.connect`` open with busy_timeout 0, so the Python
    default cannot stand in for a store that never sets the pragma."""
    real = sqlite3.connect

    def connect(database, *args, **kwargs):
        kwargs["timeout"] = 0
        return real(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)


@pytest.mark.parametrize("cls", SQLITE_STORES, ids=lambda c: c.__name__)
def test_every_sqlite_store_sets_the_shared_busy_timeout(cls, tmp_path, no_default_timeout):
    store = cls(str(tmp_path / "store.db"))
    with closing(store._connect()) as conn:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == SQLITE_BUSY_TIMEOUT_MS
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_sqlite_connect_honours_an_explicit_timeout(tmp_path):
    with closing(sqlite_connect(str(tmp_path / "x.db"), busy_timeout_ms=1234)) as conn:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 1234


def test_normalize_dsn_strips_driver_suffixes():
    assert normalize_dsn("postgresql+asyncpg://u@h/db") == "postgresql://u@h/db"
    assert normalize_dsn("postgresql+psycopg2://u@h/db") == "postgresql://u@h/db"
    assert normalize_dsn("postgresql+psycopg://u@h/db") == "postgresql://u@h/db"
    assert normalize_dsn("postgresql://u@h/db") == "postgresql://u@h/db"


def test_is_unique_violation_matches_by_type_name():
    UniqueViolationError = type("UniqueViolationError", (Exception,), {})
    assert is_unique_violation(UniqueViolationError("dup"))
    assert not is_unique_violation(RuntimeError("other"))


# --------------------------------------------------------------------------- #
# LazyPool against a fake asyncpg — the real-Postgres store tests skip unless
# RAGSTACK_TEST_PG_DSN is set.
# --------------------------------------------------------------------------- #


class _Conn:
    def __init__(self, log):
        self.log = log

    async def execute(self, sql, *args):
        self.log.append(("execute", sql, args))

    def transaction(self):
        log = self.log

        class _Tx:
            async def __aenter__(self):
                log.append(("begin",))

            async def __aexit__(self, *exc):
                log.append(("commit",) if exc[0] is None else ("rollback",))

        return _Tx()


class _Pool:
    def __init__(self, log):
        self.log = log
        self.closed = False

    def acquire(self):
        conn = _Conn(self.log)

        class _Acq:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return None

        return _Acq()

    async def close(self):
        self.closed = True
        self.log.append(("pool-close",))


@pytest.fixture
def fake_asyncpg(monkeypatch):
    created: list[tuple[str, int, int]] = []
    log: list[tuple] = []

    async def create_pool(dsn, min_size, max_size):
        created.append((dsn, min_size, max_size))
        return _Pool(log)

    mod = types.ModuleType("asyncpg")
    mod.create_pool = create_pool  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "asyncpg", mod)
    return created, log


def test_lazy_pool_runs_each_step_once_in_order(fake_asyncpg):
    created, log = fake_asyncpg
    lazy = LazyPool("postgresql+asyncpg://u@h/db", store="test", min_size=2, max_size=3)

    async def plain(conn):
        await conn.execute("PLAIN")

    async def locked(conn):
        await conn.execute("LOCKED")

    lazy.add_step(plain)
    lazy.add_step(locked, lock_key=42)

    async def go():
        pools = await asyncio.gather(*(lazy.get() for _ in range(5)))
        assert all(p is pools[0] for p in pools)
        return pools[0]

    asyncio.run(go())
    assert created == [("postgresql://u@h/db", 2, 3)]
    assert log == [
        ("execute", "PLAIN", ()),
        ("begin",),
        ("execute", "SELECT pg_advisory_xact_lock($1)", (42,)),
        ("execute", "LOCKED", ()),
        ("commit",),
    ]


def test_lazy_pool_retries_a_failed_step_without_rerunning_earlier_ones(fake_asyncpg):
    created, log = fake_asyncpg
    lazy = LazyPool("postgresql://u@h/db", store="test")
    calls = {"first": 0, "second": 0}

    async def first(conn):
        calls["first"] += 1

    async def second(conn):
        calls["second"] += 1
        if calls["second"] == 1:
            raise RuntimeError("ddl failed")

    lazy.add_step(first, lock_key=1)
    lazy.add_step(second, lock_key=2)

    async def go():
        with pytest.raises(RuntimeError, match="ddl failed"):
            await lazy.get()
        return await lazy.get()

    asyncio.run(go())
    assert calls == {"first": 1, "second": 2}
    assert len(created) == 1
    assert ("rollback",) in log


def test_lazy_pool_closed_stays_closed(fake_asyncpg):
    """After close(), get() must raise, not quietly open a pool nothing closes."""
    created, _ = fake_asyncpg
    lazy = LazyPool("postgresql://u@h/db", store="test")

    async def go():
        pool = await lazy.get()
        await lazy.close()
        assert pool.closed
        with pytest.raises(RuntimeError, match="postgres test store is closed"):
            await lazy.get()
        await lazy.close()  # idempotent

    asyncio.run(go())
    assert len(created) == 1


def test_lazy_pool_close_before_first_use_creates_nothing(fake_asyncpg):
    created, _ = fake_asyncpg
    lazy = LazyPool("postgresql://u@h/db", store="test")

    async def go():
        await lazy.close()
        with pytest.raises(RuntimeError, match="is closed"):
            await lazy.get()

    asyncio.run(go())
    assert created == []


def test_lazy_pool_close_during_bootstrap_waits_for_it(fake_asyncpg):
    """close() racing a get() that is still running DDL steps: the bootstrap
    finishes on its pool, then close() closes it, and later get()s raise."""
    created, log = fake_asyncpg
    lazy = LazyPool("postgresql://u@h/db", store="test")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow(conn):
        entered.set()
        await release.wait()
        await conn.execute("SLOW")

    async def after(conn):
        await conn.execute("AFTER")

    lazy.add_step(slow)
    lazy.add_step(after, lock_key=7)

    async def go():
        getter = asyncio.create_task(lazy.get())
        await entered.wait()
        closer = asyncio.create_task(lazy.close())
        await asyncio.sleep(0)
        release.set()
        pool = await getter  # before the fix: AttributeError on a None pool
        await closer
        assert pool.closed
        with pytest.raises(RuntimeError, match="is closed"):
            await lazy.get()

    asyncio.run(go())
    assert len(created) == 1
    # Both steps ran, and the pool closed only after the bootstrap finished.
    assert log[-3:] == [("execute", "AFTER", ()), ("commit",), ("pool-close",)]


# Pinned: the advisory keys serialise DDL across processes, so they and the
# step order must not drift. (name, lock_key) per step, in run order.
_USERS_KEY = 0x7261675F75736572  # b"rag_user"
_SHARES_KEY = 0x7261675F73686172  # b"rag_shar"
_GROUPS_KEY = 0x7261675F67727073  # b"rag_grps"


def _pg_store_cases():
    from ragstack.acl_store import PostgresAclStore
    from ragstack.collection_store import PostgresCollectionStore
    from ragstack.grading.store import PostgresGradingStore
    from ragstack.group_store import PostgresGroupStore
    from ragstack.jobstore import PostgresJobStore
    from ragstack.user_store import PostgresUserStore

    return [
        (PostgresCollectionStore, [("_ddl", None)]),
        (PostgresJobStore, [("_ddl", None)]),
        (PostgresGradingStore, [("_ddl", None)]),
        (PostgresUserStore, [("_users_ddl", _USERS_KEY)]),
        (PostgresAclStore, [("_users_ddl", _USERS_KEY), ("_shares_ddl", _SHARES_KEY)]),
        (
            PostgresGroupStore,
            [
                ("_users_ddl", _USERS_KEY),
                ("_shares_ddl", _SHARES_KEY),
                ("_groups_ddl", _GROUPS_KEY),
            ],
        ),
    ]


@pytest.mark.parametrize(
    ("cls", "expected"), _pg_store_cases(), ids=lambda v: getattr(v, "__name__", "")
)
def test_postgres_store_ddl_steps_and_lock_keys_are_pinned(cls, expected):
    store = cls("postgresql://u@h/db")  # lazy: nothing connects
    steps = [(step.__name__, key) for step, key in store._lazy._steps]
    assert steps == expected
