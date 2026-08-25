"""``IngestJob.collection_id`` (#359): stamped at create, additive-migrated
onto an existing ``jobs`` table, and the in-flight queries eviction consults.
Memory + sqlite always; postgres through ``pg_test_dsn`` (skips unless
``RAGSTACK_TEST_PG_DSN`` names a scratch server)."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import timedelta

import pytest

from ragstack.jobstore import (
    COMPLETED,
    FAILED,
    RUNNING,
    InMemoryJobStore,
    PostgresJobStore,
    SqliteJobStore,
)

pytestmark = pytest.mark.asyncio

BACKENDS = ["memory", "sqlite", "postgres"]


@pytest.fixture(params=BACKENDS)
def store(request, tmp_path):
    if request.param == "memory":
        return InMemoryJobStore()
    if request.param == "sqlite":
        return SqliteJobStore(str(tmp_path / "jobs.db"))
    return PostgresJobStore(request.getfixturevalue("pg_test_dsn"))


async def test_collection_id_is_stamped_and_round_trips(store):
    try:
        job = await store.create("ws:///u/home/a.pdf", tenant_id="acme", collection_id="lib")
        assert job.collection_id == "lib"
        assert (await store.get(job.job_id)).collection_id == "lib"
        unstamped = await store.create("s")
        assert (await store.get(unstamped.job_id)).collection_id == ""
        listed = {j.job_id: j.collection_id for j in await store.list_jobs()}
        assert listed[job.job_id] == "lib" and listed[unstamped.job_id] == ""
    finally:
        await store.close()


async def test_in_flight_queries_see_accepted_and_running_only(store):
    try:
        a = await store.create("a", collection_id="lib-a")        # accepted
        b = await store.create("b", collection_id="lib-b")
        await store.update(b.job_id, status=RUNNING)               # running
        c = await store.create("c", collection_id="lib-c")
        await store.update(c.job_id, status=COMPLETED)             # terminal
        d = await store.create("d", collection_id="lib-a")
        await store.update(d.job_id, status=FAILED)                # terminal
        await store.create("e")                                    # unstamped: never counted
        assert await store.active_collection_ids() == {"lib-a", "lib-b"}
        assert await store.active_for_collection("lib-a") == 1
        assert await store.active_for_collection("lib-b") == 1
        assert await store.active_for_collection("lib-c") == 0
        assert await store.active_for_collection("") == 0
        await store.update(a.job_id, status=COMPLETED)
        assert await store.active_collection_ids() == {"lib-b"}
    finally:
        await store.close()


async def test_a_stale_in_flight_job_stops_shielding_its_collection(store):
    """The #7 interaction: a job orphaned by a dead process (postgres has no
    sweep) stays `running` forever. The same `stale_after` cutoff
    `count_active` applies (#202) makes it stop counting here too, so it
    cannot pin its collection as un-evictable indefinitely."""
    try:
        job = await store.create("s", collection_id="lib")
        await store.update(job.job_id, status=RUNNING)
        assert await store.active_collection_ids() == {"lib"}
        # A cutoff in the future: nothing written "within" it.
        assert await store.active_collection_ids(stale_after=timedelta(seconds=-1)) == set()
        assert await store.active_for_collection("lib", stale_after=timedelta(seconds=-1)) == 0
        # A fresh write revives it.
        await store.update(job.job_id, status=RUNNING)
        assert await store.active_for_collection("lib") == 1
    finally:
        await store.close()


async def test_sqlite_migrates_a_pre_359_jobs_table(tmp_path):
    """A jobs table created before the column existed gains it in place."""
    path = str(tmp_path / "old.db")
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(
            "CREATE TABLE jobs (job_id TEXT PRIMARY KEY, status TEXT NOT NULL, "
            "source TEXT NOT NULL DEFAULT '', chunk_ids TEXT NOT NULL DEFAULT '[]', "
            "error TEXT NOT NULL DEFAULT '', tenant_id TEXT NOT NULL DEFAULT '', "
            "archive_ref TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '')"
        )
        conn.execute(
            "INSERT INTO jobs (job_id, status, updated_at) VALUES ('legacy', 'running', '')"
        )
    store = SqliteJobStore(path)
    legacy = await store.get("legacy")
    assert legacy is not None and legacy.collection_id == ""
    assert await store.active_collection_ids() == set()  # a legacy row protects nothing
    job = await store.create("s", collection_id="lib")
    assert (await store.get(job.job_id)).collection_id == "lib"
    assert await store.active_collection_ids() == {"lib"}
