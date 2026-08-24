"""``IngestJob.archive_ref`` (#203/#353): where a GoWe run's archive landed.

Additive on all three JobStore backends, migrated in place on an existing
``jobs`` table via ``ensure_columns_*`` (the #130 convention). Postgres is
opt-in through ``pg_test_dsn`` (skips unless ``RAGSTACK_TEST_PG_DSN``), with
the memory/sqlite and postgres variants sharing one assertion body — see
tests/unit/test_jobstore_tenant.py for why they are separate tests.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from ragstack.jobstore import (
    COMPLETED,
    InMemoryJobStore,
    JobStore,
    PostgresJobStore,
    SqliteJobStore,
)

REF = "ws:///alice@patricbrc.org/home/.ragstack/collections/lib1/versions/3"


def _local(backend: str, tmp_path: Path) -> JobStore:
    return InMemoryJobStore() if backend == "memory" else SqliteJobStore(str(tmp_path / "j.db"))


async def _assert_archive_ref_roundtrip(store: JobStore) -> None:
    job = await store.create(source="upload", tenant_id="bvbrc:alice")
    assert job.archive_ref == ""
    assert (await store.get(job.job_id)).archive_ref == ""
    await store.update(job.job_id, status=COMPLETED, archive_ref=REF)
    got = await store.get(job.job_id, tenant_id="bvbrc:alice")
    assert got is not None and got.archive_ref == REF and got.status == COMPLETED
    listed = {j.job_id: j for j in await store.list_jobs(limit=10)}
    assert listed[job.job_id].archive_ref == REF
    # A later update that does not mention it leaves it alone.
    await store.update(job.job_id, error="x")
    assert (await store.get(job.job_id)).archive_ref == REF


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
@pytest.mark.asyncio
async def test_archive_ref_roundtrip(backend: str, tmp_path: Path) -> None:
    await _assert_archive_ref_roundtrip(_local(backend, tmp_path))


@pytest.mark.asyncio
async def test_archive_ref_roundtrip_postgres(pg_test_dsn: str) -> None:
    store = PostgresJobStore(pg_test_dsn)
    try:
        await _assert_archive_ref_roundtrip(store)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_sqlite_pre_existing_table_gets_the_column(tmp_path: Path) -> None:
    """A jobs table from a pre-#203 build (tenant_id but no archive_ref) is
    migrated in place — CREATE TABLE IF NOT EXISTS alone would not do it."""
    path = tmp_path / "old.db"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(
            "CREATE TABLE jobs (job_id TEXT PRIMARY KEY, status TEXT NOT NULL,"
            " source TEXT NOT NULL DEFAULT '', chunk_ids TEXT NOT NULL DEFAULT '[]',"
            " error TEXT NOT NULL DEFAULT '', tenant_id TEXT NOT NULL DEFAULT '')"
        )
        conn.execute("INSERT INTO jobs (job_id, status) VALUES ('old', 'completed')")
    store = SqliteJobStore(str(path))
    with closing(sqlite3.connect(path)) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
    assert "archive_ref" in cols
    old = await store.get("old")
    assert old is not None and old.archive_ref == ""
    await _assert_archive_ref_roundtrip(store)


@pytest.mark.asyncio
async def test_postgres_pre_existing_table_gets_the_column(pg_test_dsn: str) -> None:
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(pg_test_dsn)
    try:
        await conn.execute(
            "CREATE TABLE jobs (job_id TEXT PRIMARY KEY, status TEXT NOT NULL,"
            " source TEXT NOT NULL DEFAULT '', chunk_ids TEXT NOT NULL DEFAULT '[]',"
            " error TEXT NOT NULL DEFAULT '', tenant_id TEXT NOT NULL DEFAULT '')"
        )
        await conn.execute("INSERT INTO jobs (job_id, status) VALUES ('old', 'completed')")
    finally:
        await conn.close()
    store = PostgresJobStore(pg_test_dsn)
    try:
        old = await store.get("old")
        assert old is not None and old.archive_ref == ""
        await _assert_archive_ref_roundtrip(store)
    finally:
        await store.close()
