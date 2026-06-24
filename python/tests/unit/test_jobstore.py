"""Unit tests for the ingestion JobStore (in-memory + durable sqlite)."""
from pathlib import Path

import pytest

from ragstack.jobstore import (
    ACCEPTED,
    COMPLETED,
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


def test_make_job_store_selects_backend(tmp_path):
    assert isinstance(make_job_store("memory", ""), InMemoryJobStore)
    assert isinstance(make_job_store("sqlite", str(tmp_path / "j.db")), SqliteJobStore)
