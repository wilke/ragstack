"""Unit tests for the ingestion JobStore (in-memory + durable sqlite)."""
from pathlib import Path

import pytest

from ragstack.jobstore import (
    ACCEPTED,
    COMPLETED,
    FAILED,
    INTERRUPTED,
    RUNNING,
    InMemoryJobStore,
    SqliteJobStore,
    make_job_store,
)


def _stores(tmp_path: Path):
    return [
        InMemoryJobStore(),
        SqliteJobStore(str(tmp_path / "jobs.db")),
    ]


@pytest.mark.asyncio
async def test_create_get_roundtrip(tmp_path):
    for store in _stores(tmp_path):
        job = await store.create(source="/data/doc.pdf")
        assert job.status == ACCEPTED
        assert job.source == "/data/doc.pdf"
        fetched = await store.get(job.job_id)
        assert fetched is not None
        assert fetched.job_id == job.job_id


@pytest.mark.asyncio
async def test_update_status_and_chunk_ids(tmp_path):
    for store in _stores(tmp_path):
        job = await store.create(source="s")
        await store.update(job.job_id, status=COMPLETED, chunk_ids=["a", "b"])
        fetched = await store.get(job.job_id)
        assert fetched.status == COMPLETED
        assert fetched.chunk_ids == ["a", "b"]


@pytest.mark.asyncio
async def test_get_unknown_returns_none(tmp_path):
    for store in _stores(tmp_path):
        assert await store.get("missing") is None


@pytest.mark.asyncio
async def test_update_unknown_is_noop(tmp_path):
    for store in _stores(tmp_path):
        await store.update("missing", status=COMPLETED)  # must not raise


@pytest.mark.asyncio
async def test_sqlite_store_persists_across_instances(tmp_path):
    path = str(tmp_path / "jobs.db")
    first = SqliteJobStore(path)
    job = await first.create(source="durable")
    await first.update(job.job_id, status=COMPLETED, chunk_ids=["x"])

    # A fresh instance over the same file sees the persisted job.
    second = SqliteJobStore(path)
    fetched = await second.get(job.job_id)
    assert fetched is not None
    assert fetched.status == COMPLETED
    assert fetched.chunk_ids == ["x"]


@pytest.mark.asyncio
async def test_sqlite_store_closes_every_connection(tmp_path, monkeypatch):
    """Regression: ``with conn:`` commits but never closes — every op must close
    its connection or the durable backend leaks file handles under load."""
    import sqlite3 as _sqlite3

    open_count = {"n": 0}

    class _Tracking(_sqlite3.Connection):
        # Connection.close is read-only on instances, so track via a subclass
        # passed as the connect ``factory``.
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            open_count["n"] += 1

        def close(self):
            open_count["n"] -= 1
            super().close()

    real_connect = _sqlite3.connect
    monkeypatch.setattr(
        _sqlite3, "connect", lambda *a, **k: real_connect(*a, factory=_Tracking, **k)
    )

    store = SqliteJobStore(str(tmp_path / "jobs.db"))  # __init__ opens one
    job = await store.create(source="s")
    await store.update(job.job_id, status=COMPLETED, chunk_ids=["a"])
    await store.get(job.job_id)
    await store.get("missing")

    assert open_count["n"] == 0, f"{open_count['n']} sqlite connection(s) left open"


@pytest.mark.asyncio
async def test_fail_interrupted_sweeps_non_terminal_jobs(tmp_path):
    """Orphaned accepted/running jobs become failed/interrupted; terminal untouched."""
    for store in _stores(tmp_path):
        running = await store.create(source="r")
        await store.update(running.job_id, status=RUNNING)
        accepted = await store.create(source="a")  # left in ACCEPTED
        done = await store.create(source="d")
        await store.update(done.job_id, status=COMPLETED, chunk_ids=["x"])

        swept = await store.fail_interrupted()
        assert swept == 2

        for job_id in (running.job_id, accepted.job_id):
            job = await store.get(job_id)
            assert job.status == FAILED
            assert job.error == INTERRUPTED
        # A terminal job is left exactly as it was.
        finished = await store.get(done.job_id)
        assert finished.status == COMPLETED
        assert finished.chunk_ids == ["x"]


@pytest.mark.asyncio
async def test_sqlite_fail_interrupted_across_restart(tmp_path):
    """A job left running when the process dies is reaped by the next startup."""
    path = str(tmp_path / "jobs.db")
    first = SqliteJobStore(path)
    job = await first.create(source="s")
    await first.update(job.job_id, status=RUNNING)

    # New instance over the same file == a restart; the sweep reaps the orphan.
    second = SqliteJobStore(path)
    assert await second.fail_interrupted() == 1
    fetched = await second.get(job.job_id)
    assert fetched.status == FAILED
    assert fetched.error == INTERRUPTED


def test_make_job_store_selects_backend(tmp_path):
    assert isinstance(make_job_store("memory", ""), InMemoryJobStore)
    assert isinstance(make_job_store("sqlite", str(tmp_path / "j.db")), SqliteJobStore)
