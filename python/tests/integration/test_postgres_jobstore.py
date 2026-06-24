"""Live integration test for PostgresJobStore.

Skipped unless asyncpg is installed and a Postgres reachable at TEST_PG_DSN
(default the local ragstack DB). Uses a unique job_id and deletes only its own
rows; it never calls fail_interrupted (which would touch other jobs in a shared
database).
"""
import asyncio
import os

import pytest

asyncpg = pytest.importorskip("asyncpg")

from ragstack.jobstore import (  # noqa: E402
    COMPLETED,
    FAILED,
    PENDING,
    PostgresJobStore,
)

DSN = os.environ.get("TEST_PG_DSN", "postgresql://ragstack:ragstack@localhost/ragstack")


async def _reachable() -> bool:
    try:
        conn = await asyncio.wait_for(asyncpg.connect(DSN), timeout=3)
        await conn.close()
        return True
    except Exception:
        return False


@pytest.mark.asyncio
async def test_postgres_jobstore_roundtrip_and_resume():
    if not await _reachable():
        pytest.skip("postgres not reachable at TEST_PG_DSN")

    store = PostgresJobStore(DSN)
    job = await store.create(source="/integration-test")
    try:
        # Job-level round-trip.
        await store.update(job.job_id, status=COMPLETED, chunk_ids=["a", "b"])
        got = await store.get(job.job_id)
        assert got is not None
        assert got.status == COMPLETED
        assert got.chunk_ids == ["a", "b"]

        # Per-item: add (idempotent), mark, query.
        await store.add_items(job.job_id, [("i1", "/1"), ("i2", "/2")])
        await store.add_items(job.job_id, [("i1", "/1")])  # no duplicate
        await store.mark_item(job.job_id, "i1", status=COMPLETED, chunk_ids=["x"])
        await store.mark_item(job.job_id, "i2", status=FAILED, error="ValueError")

        assert await store.completed_item_ids(job.job_id) == {"i1"}
        assert await store.item_counts(job.job_id) == {
            PENDING: 0,
            COMPLETED: 1,
            FAILED: 1,
        }
    finally:
        pool = await store._pool_()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM job_items WHERE job_id = $1", job.job_id)
            await conn.execute("DELETE FROM jobs WHERE job_id = $1", job.job_id)
        await store.close()
