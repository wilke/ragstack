"""Tenant-stamping and -scoping of ingest jobs (#130), parametrised over all
three JobStore backends.

Postgres is included via the same convention as
``tests/integration/test_postgres_jobstore.py``: skipped unless asyncpg is
installed and a server answers at ``TEST_PG_DSN`` (default the local ragstack
DB); each test uses its own uuid-suffixed job_id/tenant strings and deletes
only the rows it created, in a ``finally``.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import uuid
from pathlib import Path

import pytest

from ragstack.jobstore import (
    _JOB_ITEMS_DDL,
    ACCEPTED,
    InMemoryJobStore,
    PostgresJobStore,
    SqliteJobStore,
)

BACKENDS = ("memory", "sqlite", "postgres")
DSN = os.environ.get("TEST_PG_DSN", "postgresql://ragstack:ragstack@localhost/ragstack")


async def _postgres_reachable() -> bool:
    asyncpg = pytest.importorskip("asyncpg")
    try:
        conn = await asyncio.wait_for(asyncpg.connect(DSN), timeout=3)
        await conn.close()
        return True
    except Exception:
        return False


async def _make_store(backend: str, tmp_path: Path):
    if backend == "memory":
        return InMemoryJobStore()
    if backend == "sqlite":
        return SqliteJobStore(str(tmp_path / "jobs.db"))
    assert backend == "postgres"
    if not await _postgres_reachable():
        pytest.skip("postgres not reachable at TEST_PG_DSN")
    return PostgresJobStore(DSN)


async def _cleanup(backend: str, store, job_ids: list[str]) -> None:
    if backend != "postgres":
        return
    pool = await store._pool_()
    async with pool.acquire() as conn:
        for job_id in job_ids:
            await conn.execute("DELETE FROM job_items WHERE job_id = $1", job_id)
            await conn.execute("DELETE FROM jobs WHERE job_id = $1", job_id)
    await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", BACKENDS)
async def test_create_stamps_tenant(backend, tmp_path):
    store = await _make_store(backend, tmp_path)
    tenant = f"acme-{uuid.uuid4().hex[:8]}"
    job = await store.create(source="/x", tenant_id=tenant)
    try:
        assert job.tenant_id == tenant
        # Unscoped fetch (tenant_id=None) — the internal/health-check caller —
        # sees the stamp regardless of who asks.
        fetched = await store.get(job.job_id)
        assert fetched is not None
        assert fetched.tenant_id == tenant
    finally:
        await _cleanup(backend, store, [job.job_id])


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", BACKENDS)
async def test_create_defaults_to_unstamped(backend, tmp_path):
    """No tenant_id given -> "" (today's callers, and back-compat)."""
    store = await _make_store(backend, tmp_path)
    job = await store.create(source="/x")
    try:
        assert job.tenant_id == ""
        assert job.status == ACCEPTED
    finally:
        await _cleanup(backend, store, [job.job_id])


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", BACKENDS)
async def test_get_is_tenant_scoped(backend, tmp_path):
    store = await _make_store(backend, tmp_path)
    tenant = f"acme-{uuid.uuid4().hex[:8]}"
    other = f"bob-{uuid.uuid4().hex[:8]}"
    job = await store.create(source="/x", tenant_id=tenant)
    try:
        # Owner sees it.
        mine = await store.get(job.job_id, tenant_id=tenant)
        assert mine is not None and mine.job_id == job.job_id

        # A different tenant gets exactly the "doesn't exist" answer.
        assert await store.get(job.job_id, tenant_id=other) is None

        # An unrecognized job_id is indistinguishable from a foreign one.
        assert await store.get("does-not-exist", tenant_id=other) is None
    finally:
        await _cleanup(backend, store, [job.job_id])


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", BACKENDS)
async def test_get_admin_bypass_is_logged(backend, tmp_path, caplog):
    store = await _make_store(backend, tmp_path)
    tenant = f"acme-{uuid.uuid4().hex[:8]}"
    other = f"bob-{uuid.uuid4().hex[:8]}"
    job = await store.create(source="/x", tenant_id=tenant)
    try:
        with caplog.at_level(logging.INFO, logger="ragstack.jobstore"):
            seen = await store.get(job.job_id, tenant_id=other, is_admin=True)
        assert seen is not None and seen.job_id == job.job_id
        bypass = [r for r in caplog.records if "admin-bypass" in r.getMessage()]
        assert bypass, "admin bypass must be logged"
        assert any(job.job_id in r.getMessage() for r in bypass)
    finally:
        await _cleanup(backend, store, [job.job_id])


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", BACKENDS)
async def test_legacy_unstamped_job_fails_closed(backend, tmp_path):
    """A job written before #130 (tenant_id == "") is readable by admin only —
    fail closed, per the #209 convention: "" never equals a real tenant."""
    store = await _make_store(backend, tmp_path)
    tenant = f"acme-{uuid.uuid4().hex[:8]}"
    job = await store.create(source="/legacy")  # tenant_id="" (default)
    try:
        assert await store.get(job.job_id, tenant_id=tenant) is None
        assert await store.get(job.job_id, tenant_id=tenant, is_admin=True) is not None
    finally:
        await _cleanup(backend, store, [job.job_id])


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
