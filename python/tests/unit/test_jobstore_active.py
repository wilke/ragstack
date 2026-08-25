"""``JobStore.count_active(tenant_id)`` (#202): the admission query behind
``api/deps.py::single_inflight_ingest`` — how many of a tenant's jobs are still
``accepted``/``running``. Pinned on all three backends: memory and sqlite
in-process, Postgres opt-in through the ``pg_test_dsn`` fixture (skips unless
``RAGSTACK_TEST_PG_DSN`` names a scratch database; requested as an ordinary
fixture parameter for the reason ``test_jobstore_tenant.py`` documents).

Each case's assertions are one shared coroutine so every backend runs the
identical logic.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ragstack.jobstore import (
    ACTIVE,
    COMPLETED,
    FAILED,
    RUNNING,
    STALE_AFTER,
    InMemoryJobStore,
    JobStore,
    PostgresJobStore,
    SqliteJobStore,
)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")

LOCAL_BACKENDS = ("memory", "sqlite")


def _make_local_store(backend: str, tmp_path: Path) -> JobStore:
    if backend == "memory":
        return InMemoryJobStore()
    assert backend == "sqlite"
    return SqliteJobStore(str(tmp_path / "jobs.db"))


async def _assert_count_active(store: JobStore) -> None:
    tenant = f"acme-{uuid.uuid4().hex[:8]}"
    other = f"other-{uuid.uuid4().hex[:8]}"
    assert ACTIVE == ("accepted", "running")
    assert await store.count_active(tenant) == 0

    a = await store.create(source="/a", tenant_id=tenant)  # accepted
    assert await store.count_active(tenant) == 1
    await store.update(a.job_id, status=RUNNING)
    assert await store.count_active(tenant) == 1

    # Another tenant's in-flight job is not this tenant's.
    b = await store.create(source="/b", tenant_id=other)
    assert await store.count_active(tenant) == 1
    assert await store.count_active(other) == 1

    # Two in flight → 2; terminal states free the slot, whatever the label.
    c = await store.create(source="/c", tenant_id=tenant)
    assert await store.count_active(tenant) == 2
    await store.update(a.job_id, status=COMPLETED)
    assert await store.count_active(tenant) == 1
    await store.update(c.job_id, status=FAILED, error="rejected")
    assert await store.count_active(tenant) == 0
    await store.update(b.job_id, status=COMPLETED)
    assert await store.count_active(other) == 0

    # A legacy unstamped row ("") never counts for a real tenant — and a
    # caller stamped "" only sees its own kind.
    legacy = await store.create(source="/legacy")
    assert await store.count_active(tenant) == 0
    assert await store.count_active("") == 1
    await store.update(legacy.job_id, status=COMPLETED)
    assert await store.count_active("") == 0


async def _assert_stale_running_job_does_not_block(store: JobStore) -> None:
    """A ``running`` job that has not been written to for ``stale_after``
    stops counting — the Postgres store never sweeps (#7), so this is what
    keeps a crashed worker's job from pinning its principal at 429 forever.
    Any job/item write refreshes ``updated_at`` and makes it count again."""
    assert STALE_AFTER == timedelta(hours=6)
    tenant = f"acme-{uuid.uuid4().hex[:8]}"
    job = await store.create(source="/a", tenant_id=tenant)
    assert job.updated_at  # stamped at create
    fetched = await store.get(job.job_id, tenant_id=None)
    assert fetched is not None and fetched.updated_at == job.updated_at
    await store.update(job.job_id, status=RUNNING)
    assert (await store.get(job.job_id, tenant_id=None)).updated_at >= job.updated_at
    assert await store.count_active(tenant) == 1

    # Back-date the last write past the window (an explicit updated_at wins
    # over the automatic stamp) → no longer counted, by default or explicitly.
    old = _iso(datetime.now(UTC) - STALE_AFTER - timedelta(minutes=1))
    await store.update(job.job_id, updated_at=old)
    assert (await store.get(job.job_id, tenant_id=None)).updated_at == old
    assert (await store.get(job.job_id, tenant_id=None)).status == RUNNING
    assert await store.count_active(tenant) == 0
    assert await store.count_active(tenant, stale_after=timedelta(hours=6)) == 0
    # …but a wider window still sees it.
    assert await store.count_active(tenant, stale_after=timedelta(days=365)) == 1

    # Item progress is a write on the job: it becomes fresh (and blocking) again.
    await store.add_items(job.job_id, [("i1", "/a/1.pdf")])
    await store.mark_item(job.job_id, "i1", status=COMPLETED, chunk_ids=["c1"])
    assert (await store.get(job.job_id, tenant_id=None)).updated_at > old
    assert await store.count_active(tenant) == 1

    # A pre-column row (updated_at == "") never counts as active.
    await store.update(job.job_id, updated_at="")
    assert await store.count_active(tenant) == 0


async def _assert_fail_interrupted_frees_the_slot(store: JobStore) -> None:
    tenant = f"acme-{uuid.uuid4().hex[:8]}"
    await store.create(source="/a", tenant_id=tenant)
    assert await store.count_active(tenant) == 1
    assert await store.fail_interrupted() >= 1
    assert await store.count_active(tenant) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", LOCAL_BACKENDS)
async def test_count_active(backend, tmp_path):
    await _assert_count_active(_make_local_store(backend, tmp_path))


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", LOCAL_BACKENDS)
async def test_stale_running_job_does_not_block(backend, tmp_path):
    await _assert_stale_running_job_does_not_block(_make_local_store(backend, tmp_path))


def test_sqlite_pre_column_file_gets_updated_at_added(tmp_path):
    """Additive migration: a jobs table from before this column opens fine and
    its rows read back with updated_at == ''."""
    path = tmp_path / "jobs.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE jobs (job_id TEXT PRIMARY KEY, status TEXT NOT NULL,"
            " source TEXT NOT NULL DEFAULT '', chunk_ids TEXT NOT NULL DEFAULT '[]',"
            " error TEXT NOT NULL DEFAULT '', tenant_id TEXT NOT NULL DEFAULT '',"
            " archive_ref TEXT NOT NULL DEFAULT '')"
        )
        conn.execute("INSERT INTO jobs (job_id, status, tenant_id) VALUES ('old', 'running', 't')")
    store = SqliteJobStore(str(path))
    with sqlite3.connect(path) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    assert "updated_at" in cols

    async def _check() -> None:
        job = await store.get("old", tenant_id=None)
        assert job is not None and job.updated_at == "" and job.status == RUNNING
        assert await store.count_active("t") == 0  # never counted: no stamp

    import asyncio

    asyncio.run(_check())


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", LOCAL_BACKENDS)
async def test_fail_interrupted_frees_the_slot(backend, tmp_path):
    await _assert_fail_interrupted_frees_the_slot(_make_local_store(backend, tmp_path))


def test_sqlite_has_the_tenant_status_index(tmp_path):
    """The lookup is indexed on (tenant_id, status) so admission stays a point
    lookup as the jobs table grows — and the index is added to a pre-existing
    file too (CREATE INDEX IF NOT EXISTS runs on every open)."""
    path = tmp_path / "jobs.db"
    SqliteJobStore(str(path))
    with sqlite3.connect(path) as conn:
        names = {row[1] for row in conn.execute("PRAGMA index_list(jobs)")}
        assert "jobs_tenant_status" in names
        cols = [row[2] for row in conn.execute("PRAGMA index_info(jobs_tenant_status)")]
        assert cols == ["tenant_id", "status"]
        conn.execute("DROP INDEX jobs_tenant_status")
    SqliteJobStore(str(path))  # re-open re-creates it
    with sqlite3.connect(path) as conn:
        assert "jobs_tenant_status" in {r[1] for r in conn.execute("PRAGMA index_list(jobs)")}


# --- postgres (opt-in) ---

@pytest.mark.asyncio
async def test_count_active_postgres(pg_test_dsn):
    store = PostgresJobStore(pg_test_dsn)
    try:
        await _assert_count_active(store)
        await _assert_stale_running_job_does_not_block(store)
        # The index exists on the fresh schema.
        pool = await store._pool_()
        async with pool.acquire() as conn:
            names = {r["indexname"] for r in await conn.fetch(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'jobs'"
            )}
        assert "jobs_tenant_status" in names
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_postgres_fail_interrupted_is_a_noop_so_a_stuck_job_keeps_blocking(pg_test_dsn):
    """Documented caveat (#7): the multi-process store never sweeps, so a job
    left in flight by a crashed worker blocks that principal — until its
    ``updated_at`` ages past ``STALE_AFTER``, which is what bounds the damage.
    Pinned so the caveat in single_inflight_ingest stays true to the code."""
    store = PostgresJobStore(pg_test_dsn)
    try:
        tenant = f"acme-{uuid.uuid4().hex[:8]}"
        job = await store.create(source="/a", tenant_id=tenant)
        assert await store.fail_interrupted() == 0
        assert await store.count_active(tenant) == 1
        await store.update(job.job_id, updated_at=_iso(datetime.now(UTC) - timedelta(hours=7)))
        assert await store.count_active(tenant) == 0
    finally:
        await store.close()
