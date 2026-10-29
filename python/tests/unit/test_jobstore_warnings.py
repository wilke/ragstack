"""``IngestJob.warnings`` (#847 G2b): caller-safe notes on a job that
completed with a caveat (the glossary step degraded some documents). An
additive JSON-list column, ``[]`` by default and on every legacy row; all three
backends round-trip it through ``update``, ``get`` and ``list_jobs``. Postgres
via ``pg_test_dsn`` (skipped without ``RAGSTACK_TEST_PG_DSN``).
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from ragstack.jobstore import InMemoryJobStore, PostgresJobStore, SqliteJobStore

NOTE = "symbol_glossary_degraded: 2 of 3 document(s) (timeout=2) endpoint model:8004"


async def _assert_warnings(store) -> None:
    job = await store.create("upload", tenant_id="t")
    assert (await store.get(job.job_id)).warnings == []
    await store.update(job.job_id, status="completed", warnings=[NOTE])
    got = await store.get(job.job_id)
    assert got.warnings == [NOTE] and got.status == "completed"
    listed = {j.job_id: j.warnings for j in await store.list_jobs(limit=5)}
    assert listed[job.job_id] == [NOTE]
    # an unrelated update leaves them alone
    await store.update(job.job_id, error="")
    assert (await store.get(job.job_id)).warnings == [NOTE]


@pytest.mark.asyncio
async def test_memory_warnings() -> None:
    await _assert_warnings(InMemoryJobStore())


@pytest.mark.asyncio
async def test_sqlite_warnings(tmp_path: Path) -> None:
    await _assert_warnings(SqliteJobStore(str(tmp_path / "jobs.db")))


@pytest.mark.asyncio
async def test_postgres_warnings(pg_test_dsn: str) -> None:
    store = PostgresJobStore(pg_test_dsn)
    try:
        await _assert_warnings(store)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_sqlite_pre_existing_table_gets_the_warnings_column(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(
            "CREATE TABLE jobs (job_id TEXT PRIMARY KEY, status TEXT NOT NULL,"
            " source TEXT NOT NULL DEFAULT '', chunk_ids TEXT NOT NULL DEFAULT '[]',"
            " error TEXT NOT NULL DEFAULT '', tenant_id TEXT NOT NULL DEFAULT '',"
            " archive_ref TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '',"
            " collection_id TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT '')"
        )
        conn.execute("INSERT INTO jobs (job_id, status) VALUES ('old', 'completed')")
    store = SqliteJobStore(str(path))
    with closing(sqlite3.connect(path)) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
    assert "warnings" in cols
    old = await store.get("old")
    assert old is not None and old.warnings == []
