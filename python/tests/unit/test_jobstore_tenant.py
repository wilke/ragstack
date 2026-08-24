"""Tenant-stamping and -scoping of ingest jobs (#130), covering all three
JobStore backends.

Postgres is opt-in only, via the ``pg_test_dsn`` fixture (tests/conftest.py):
skipped unless ``RAGSTACK_TEST_PG_DSN`` is set, and even then every test runs
inside its own throwaway schema that fixture creates and drops — never
against ``public`` on whatever server the DSN names. See that fixture's
docstring for why: an earlier version of this file (and of
tests/integration/test_postgres_jobstore.py) defaulted to a DSN that, on this
host, is the shared infra Postgres a production API points
``JOB_STORE_BACKEND=postgres`` at.

Postgres coverage lives in a parallel ``..._postgres`` test per case rather
than a single dispatcher parametrized over all three backends: ``pg_test_dsn``
must be requested as an ordinary fixture parameter (resolved during pytest's
setup phase) rather than via ``request.getfixturevalue()`` from inside an
already-running async test body — pytest-asyncio's async-fixture wrapper uses
its own ``asyncio.Runner().run(...)`` to build the fixture, which cannot be
invoked from within a coroutine that is already running on an event loop
(``RuntimeError: Runner.run() cannot be called from a running event loop``).
Each case's assertions are factored into a shared ``_assert_*`` coroutine so
the memory/sqlite and postgres variants exercise identical logic.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import uuid
from pathlib import Path

import pytest

from ragstack.jobstore import (
    _JOB_ITEMS_DDL,
    ACCEPTED,
    InMemoryJobStore,
    JobStore,
    PostgresJobStore,
    SqliteJobStore,
)

LOCAL_BACKENDS = ("memory", "sqlite")


def _make_local_store(backend: str, tmp_path: Path):
    if backend == "memory":
        return InMemoryJobStore()
    assert backend == "sqlite"
    return SqliteJobStore(str(tmp_path / "jobs.db"))


# --- shared assertions, run against every backend ---

async def _assert_create_stamps_tenant(store: JobStore) -> None:
    tenant = f"acme-{uuid.uuid4().hex[:8]}"
    job = await store.create(source="/x", tenant_id=tenant)
    assert job.tenant_id == tenant
    # Unscoped fetch (tenant_id=None) — the internal/health-check caller —
    # sees the stamp regardless of who asks.
    fetched = await store.get(job.job_id)
    assert fetched is not None
    assert fetched.tenant_id == tenant


async def _assert_create_defaults_to_unstamped(store: JobStore) -> None:
    """No tenant_id given -> "" (today's callers, and back-compat)."""
    job = await store.create(source="/x")
    assert job.tenant_id == ""
    assert job.status == ACCEPTED


async def _assert_get_is_tenant_scoped(store: JobStore) -> None:
    tenant = f"acme-{uuid.uuid4().hex[:8]}"
    other = f"bob-{uuid.uuid4().hex[:8]}"
    job = await store.create(source="/x", tenant_id=tenant)

    # Owner sees it.
    mine = await store.get(job.job_id, tenant_id=tenant)
    assert mine is not None and mine.job_id == job.job_id

    # A different tenant gets exactly the "doesn't exist" answer.
    assert await store.get(job.job_id, tenant_id=other) is None

    # An unrecognized job_id is indistinguishable from a foreign one.
    assert await store.get("does-not-exist", tenant_id=other) is None


async def _assert_get_admin_bypass_is_logged(store: JobStore, caplog) -> None:
    tenant = f"acme-{uuid.uuid4().hex[:8]}"
    other = f"bob-{uuid.uuid4().hex[:8]}"
    job = await store.create(source="/x", tenant_id=tenant)

    with caplog.at_level(logging.INFO, logger="ragstack.jobstore"):
        seen = await store.get(job.job_id, tenant_id=other, is_admin=True)
    assert seen is not None and seen.job_id == job.job_id
    bypass = [r for r in caplog.records if "admin-bypass" in r.getMessage()]
    assert bypass, "admin bypass must be logged"
    assert any(job.job_id in r.getMessage() for r in bypass)


async def _assert_legacy_unstamped_job_fails_closed(store: JobStore) -> None:
    """A job written before #130 (tenant_id == "") is readable by admin only —
    fail closed, per the #209 convention: "" never equals a real tenant."""
    tenant = f"acme-{uuid.uuid4().hex[:8]}"
    job = await store.create(source="/legacy")  # tenant_id="" (default)
    assert await store.get(job.job_id, tenant_id=tenant) is None
    assert await store.get(job.job_id, tenant_id=tenant, is_admin=True) is not None


async def _assert_empty_caller_tenant_never_matches_legacy_row(store: JobStore) -> None:
    """The hardening in _apply_tenant_scope: a SCOPED caller whose own
    tenant_id happens to be "" must not match a legacy "" row by ordinary
    equality — that would make fail-closed depend on no caller ever being
    stamped "". Not reachable through the API today (DEFAULT_TENANT is
    "default", blank api_key_tenants values are rejected, bearer subjects are
    always "issuer:sub"), but the store-level boundary must refuse it
    explicitly regardless."""
    job = await store.create(source="/legacy")  # tenant_id="" (default)
    assert await store.get(job.job_id, tenant_id="") is None
    # The unscoped (internal-caller) path is untouched: tenant_id=None still
    # returns the row.
    assert await store.get(job.job_id, tenant_id=None) is not None
    # The admin branch is checked BEFORE this guard, not after: pins that
    # ordering so a future refactor can't silently break admin access to
    # legacy rows by moving the empty-tenant check above it.
    assert await store.get(job.job_id, tenant_id="", is_admin=True) is not None


# --- memory / sqlite ---

@pytest.mark.asyncio
@pytest.mark.parametrize("backend", LOCAL_BACKENDS)
async def test_create_stamps_tenant(backend, tmp_path):
    await _assert_create_stamps_tenant(_make_local_store(backend, tmp_path))


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", LOCAL_BACKENDS)
async def test_create_defaults_to_unstamped(backend, tmp_path):
    await _assert_create_defaults_to_unstamped(_make_local_store(backend, tmp_path))


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", LOCAL_BACKENDS)
async def test_get_is_tenant_scoped(backend, tmp_path):
    await _assert_get_is_tenant_scoped(_make_local_store(backend, tmp_path))


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", LOCAL_BACKENDS)
async def test_get_admin_bypass_is_logged(backend, tmp_path, caplog):
    await _assert_get_admin_bypass_is_logged(_make_local_store(backend, tmp_path), caplog)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", LOCAL_BACKENDS)
async def test_legacy_unstamped_job_fails_closed(backend, tmp_path):
    await _assert_legacy_unstamped_job_fails_closed(_make_local_store(backend, tmp_path))


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", LOCAL_BACKENDS)
async def test_empty_caller_tenant_never_matches_legacy_row(backend, tmp_path):
    await _assert_empty_caller_tenant_never_matches_legacy_row(
        _make_local_store(backend, tmp_path)
    )


# --- postgres (opt-in; see module docstring for why this isn't folded into
# the dispatchers above) ---

@pytest.mark.asyncio
async def test_create_stamps_tenant_postgres(pg_test_dsn):
    store = PostgresJobStore(pg_test_dsn)
    try:
        await _assert_create_stamps_tenant(store)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_create_defaults_to_unstamped_postgres(pg_test_dsn):
    store = PostgresJobStore(pg_test_dsn)
    try:
        await _assert_create_defaults_to_unstamped(store)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_get_is_tenant_scoped_postgres(pg_test_dsn):
    store = PostgresJobStore(pg_test_dsn)
    try:
        await _assert_get_is_tenant_scoped(store)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_get_admin_bypass_is_logged_postgres(pg_test_dsn, caplog):
    store = PostgresJobStore(pg_test_dsn)
    try:
        await _assert_get_admin_bypass_is_logged(store, caplog)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_legacy_unstamped_job_fails_closed_postgres(pg_test_dsn):
    store = PostgresJobStore(pg_test_dsn)
    try:
        await _assert_legacy_unstamped_job_fails_closed(store)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_empty_caller_tenant_never_matches_legacy_row_postgres(pg_test_dsn):
    store = PostgresJobStore(pg_test_dsn)
    try:
        await _assert_empty_caller_tenant_never_matches_legacy_row(store)
    finally:
        await store.close()


def test_pg_test_dsn_fixture_skips_without_env(monkeypatch, request):
    """The Postgres opt-in must skip — never silently fall back to any
    default DSN — when RAGSTACK_TEST_PG_DSN isn't set. A stale TEST_PG_DSN
    (the old, now-removed env var this fixture replaces) must NOT re-enable
    it either: that exact default previously pointed at a shared production
    database.

    Called via ``request.getfixturevalue`` from a plain SYNC test — safe,
    unlike doing the same from inside an async test body (see the module
    docstring): there is no already-running event loop to conflict with.
    """
    monkeypatch.delenv("RAGSTACK_TEST_PG_DSN", raising=False)
    monkeypatch.setenv("TEST_PG_DSN", "postgresql://ragstack:ragstack@localhost/ragstack")
    with pytest.raises(pytest.skip.Exception):
        request.getfixturevalue("pg_test_dsn")


# --- sqlite-specific: the additive migration itself ---

_OLD_JOBS_DDL = (
    "CREATE TABLE IF NOT EXISTS jobs ("
    "  job_id TEXT PRIMARY KEY,"
    "  status TEXT NOT NULL,"
    "  source TEXT NOT NULL DEFAULT '',"
    "  chunk_ids TEXT NOT NULL DEFAULT '[]',"
    "  error TEXT NOT NULL DEFAULT ''"
    ")"
)


def test_sqlite_migration_adds_tenant_id_to_existing_file_with_rows(tmp_path):
    """A `jobs` table created by a pre-#130 build (no tenant_id column, with
    live rows) must gain the column in place — CREATE TABLE IF NOT EXISTS alone
    cannot alter an existing table (libraries-spec §8.1's convention)."""
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    try:
        conn.execute(_OLD_JOBS_DDL)
        conn.execute(_JOB_ITEMS_DDL)
        conn.execute(
            "INSERT INTO jobs (job_id, status, source, chunk_ids, error) "
            "VALUES ('legacy-1', 'completed', '/old', '[\"c1\"]', '')"
        )
        conn.commit()
    finally:
        conn.close()

    # Opening the pre-existing file must migrate it additively, not blow up.
    store = SqliteJobStore(path)

    async def _check():
        # The pre-migration row reads back unstamped and fails closed for a
        # real tenant, but is visible to admin.
        assert await store.get("legacy-1", tenant_id="someone") is None
        got = await store.get("legacy-1", tenant_id="someone", is_admin=True)
        assert got is not None
        assert got.tenant_id == ""
        assert got.status == "completed"
        assert got.chunk_ids == ["c1"]

        # A new row created after migration is stamped as usual.
        job = await store.create(source="/new", tenant_id="acme")
        assert (await store.get(job.job_id, tenant_id="acme")) is not None

    asyncio.run(_check())

    # Column present exactly once, and opening the same file again (simulating
    # a second process/restart) is a no-op, not a duplicate-column error.
    conn = sqlite3.connect(path)
    try:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(jobs)")]
        assert cols.count("tenant_id") == 1
    finally:
        conn.close()

    second = SqliteJobStore(path)  # must not raise on the already-migrated file

    async def _check_second():
        assert (await second.get("legacy-1", tenant_id="x", is_admin=True)) is not None

    asyncio.run(_check_second())
