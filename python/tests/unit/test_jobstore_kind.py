"""``IngestJob.kind`` (#350): the additive job column that lets the two
per-principal admission guards count independently — one in-flight INGEST
(``kind == ""``, every legacy row) and ``graph_extraction_jobs_per_owner``
extractions (``kind == "graph"``). All three backends; Postgres via
``pg_test_dsn`` (skipped without ``RAGSTACK_TEST_PG_DSN``); a pre-#350 sqlite
table gets the column in place.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from ragstack.jobstore import (
    KIND_GRAPH,
    KIND_INGEST,
    InMemoryJobStore,
    PostgresJobStore,
    SqliteJobStore,
)


async def _assert_kinds(store) -> None:
    ingest = await store.create("upload", tenant_id="t", collection_id="c")
    graph = await store.create("graph-extract:c@1", tenant_id="t", collection_id="c",
                               kind=KIND_GRAPH)
    other = await store.create("graph-extract:d@1", tenant_id="u", kind=KIND_GRAPH)
    assert (await store.get(ingest.job_id)).kind == KIND_INGEST == ""
    assert (await store.get(graph.job_id)).kind == KIND_GRAPH
    assert (await store.get(other.job_id)).kind == KIND_GRAPH
    assert await store.count_active("t") == 2  # every kind (the old meaning)
    assert await store.count_active("t", kind=KIND_INGEST) == 1
    assert await store.count_active("t", kind=KIND_GRAPH) == 1
    assert await store.count_active("u", kind=KIND_GRAPH) == 1
    assert await store.count_active("u", kind=KIND_INGEST) == 0
    await store.update(graph.job_id, status="completed")
    assert await store.count_active("t", kind=KIND_GRAPH) == 0
    assert await store.count_active("t", kind=KIND_INGEST) == 1
    listed = {j.job_id: j.kind for j in await store.list_jobs(limit=10)}
    assert listed[graph.job_id] == KIND_GRAPH and listed[ingest.job_id] == ""
    # Eviction's in-flight view counts every kind: a running extraction pins
    # its collection like an ingest does; the per-collection extraction guard
    # counts graph jobs only.
    assert await store.active_for_collection("c") == 1
    assert await store.active_for_collection("c", kind=KIND_GRAPH) == 0
    assert await store.active_for_collection("c", kind=KIND_INGEST) == 1
    second = await store.create("graph-extract:c@2", tenant_id="v", collection_id="c",
                                kind=KIND_GRAPH)
    assert await store.active_for_collection("c", kind=KIND_GRAPH) == 1
    assert await store.active_for_collection("c") == 2
    await store.update(second.job_id, status="failed")
    assert await store.active_for_collection("c", kind=KIND_GRAPH) == 0
    assert "c" in await store.active_collection_ids()


@pytest.mark.asyncio
async def test_memory_kind() -> None:
    await _assert_kinds(InMemoryJobStore())


@pytest.mark.asyncio
async def test_sqlite_kind(tmp_path: Path) -> None:
    await _assert_kinds(SqliteJobStore(str(tmp_path / "jobs.db")))


@pytest.mark.asyncio
async def test_postgres_kind(pg_test_dsn: str) -> None:
    store = PostgresJobStore(pg_test_dsn)
    try:
        await _assert_kinds(store)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_sqlite_pre_existing_table_gets_the_kind_column(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(
            "CREATE TABLE jobs (job_id TEXT PRIMARY KEY, status TEXT NOT NULL,"
            " source TEXT NOT NULL DEFAULT '', chunk_ids TEXT NOT NULL DEFAULT '[]',"
            " error TEXT NOT NULL DEFAULT '', tenant_id TEXT NOT NULL DEFAULT '',"
            " archive_ref TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '',"
            " collection_id TEXT NOT NULL DEFAULT '')"
        )
        conn.execute(
            "INSERT INTO jobs (job_id, status, tenant_id, updated_at) VALUES"
            " ('old', 'running', 't', '9999-01-01T00:00:00+00:00')"
        )
    store = SqliteJobStore(str(path))
    with closing(sqlite3.connect(path)) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
    assert "kind" in cols
    old = await store.get("old")
    assert old is not None and old.kind == KIND_INGEST  # a legacy row IS an ingest
    assert await store.count_active("t", kind=KIND_INGEST) == 1
    assert await store.count_active("t", kind=KIND_GRAPH) == 0
