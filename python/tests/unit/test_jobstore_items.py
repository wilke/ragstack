"""Per-item job state tests (resumability spine) across in-memory + sqlite."""
from pathlib import Path

import pytest

from ragstack.jobstore import (
    COMPLETED,
    FAILED,
    PENDING,
    InMemoryJobStore,
    SqliteJobStore,
)


def _stores(tmp_path: Path):
    return [InMemoryJobStore(), SqliteJobStore(str(tmp_path / "j.db"))]


@pytest.mark.asyncio
async def test_add_items_idempotent(tmp_path):
    for store in _stores(tmp_path):
        await store.add_items("J", [("a", "/a"), ("b", "/b")])
        await store.add_items("J", [("a", "/a")])  # re-add, no dup
        counts = await store.item_counts("J")
        assert counts[PENDING] == 2


@pytest.mark.asyncio
async def test_mark_and_completed_ids(tmp_path):
    for store in _stores(tmp_path):
        await store.add_items("J", [("a", "/a"), ("b", "/b")])
        await store.mark_item("J", "a", status=COMPLETED, chunk_ids=["x", "y"])
        await store.mark_item("J", "b", status=FAILED, error="ValueError")
        assert await store.completed_item_ids("J") == {"a"}
        assert await store.item_counts("J") == {PENDING: 0, COMPLETED: 1, FAILED: 1}


@pytest.mark.asyncio
async def test_add_items_preserves_prior_progress(tmp_path):
    # Re-registering a manifest must not reset already-completed items — that's
    # what makes a resumed run skip them.
    for store in _stores(tmp_path):
        await store.add_items("J", [("a", "/a")])
        await store.mark_item("J", "a", status=COMPLETED, chunk_ids=["x"])
        await store.add_items("J", [("a", "/a"), ("b", "/b")])  # adds b, keeps a
        assert await store.completed_item_ids("J") == {"a"}
        assert (await store.item_counts("J"))[PENDING] == 1  # only b


@pytest.mark.asyncio
async def test_sqlite_items_persist_across_instances(tmp_path):
    path = str(tmp_path / "j.db")
    first = SqliteJobStore(path)
    await first.add_items("J", [("a", "/a"), ("b", "/b")])
    await first.mark_item("J", "a", status=COMPLETED, chunk_ids=["x"])

    second = SqliteJobStore(path)
    assert await second.completed_item_ids("J") == {"a"}
