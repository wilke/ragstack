"""Live integration test for PostgresJobStore.

Opt-in only (#130 follow-up) via the ``pg_test_dsn`` fixture (tests/conftest.py):
skipped unless ``RAGSTACK_TEST_PG_DSN`` is set, and even then it runs entirely
inside a throwaway schema that fixture creates and drops — never against
``public`` on whatever server the DSN names. See that fixture's docstring for
why the old always-on default DSN was removed.
"""
import pytest

from ragstack.jobstore import (
    COMPLETED,
    FAILED,
    PENDING,
    PostgresJobStore,
)

pytest.importorskip("asyncpg")


@pytest.mark.asyncio
async def test_postgres_jobstore_roundtrip_and_resume(pg_test_dsn):
    store = PostgresJobStore(pg_test_dsn)
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
        # No manual row cleanup needed: the whole schema is dropped when
        # pg_test_dsn tears down. Just release the pool.
        await store.close()
