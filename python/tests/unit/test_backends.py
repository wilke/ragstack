"""Unit tests for the IngestBackend seam (LocalAsyncIORunner)."""
import asyncio

import pytest

from ragstack.ingestion.backends import LocalAsyncIORunner, partition
from ragstack.ingestion.manifest import ItemResult, WorkItem


def _items(n: int) -> list[WorkItem]:
    return [WorkItem(item_id=str(i), source=f"/s/{i}") for i in range(n)]


def _ok(shard: list[WorkItem]) -> list[ItemResult]:
    return [ItemResult(item_id=i.item_id, source=i.source, status="completed") for i in shard]


def test_partition_sizes():
    items = _items(5)
    assert [len(s) for s in partition(items, 2)] == [2, 2, 1]
    assert len(partition(items, 10)) == 1


def test_partition_rejects_bad_size():
    with pytest.raises(ValueError):
        partition(_items(1), 0)


@pytest.mark.asyncio
async def test_runs_all_shards_and_aggregates():
    runner = LocalAsyncIORunner(max_concurrency=4)

    async def fn(shard):
        return _ok(shard)

    shards = partition(_items(5), 2)
    results = await runner.run_shards(shards, fn)
    assert len(results) == 5
    assert {r.item_id for r in results} == {str(i) for i in range(5)}


@pytest.mark.asyncio
async def test_respects_concurrency_bound():
    runner = LocalAsyncIORunner(max_concurrency=2)
    active = 0
    peak = 0
    lock = asyncio.Lock()

    async def fn(shard):
        nonlocal active, peak
        async with lock:
            active += 1
            peak = max(peak, active)
        await asyncio.sleep(0.02)
        async with lock:
            active -= 1
        return _ok(shard)

    shards = [[w] for w in _items(6)]
    results = await runner.run_shards(shards, fn)
    assert len(results) == 6
    assert peak <= 2


@pytest.mark.asyncio
async def test_failing_shard_marks_its_items_failed_and_continues():
    runner = LocalAsyncIORunner(max_concurrency=4)

    async def fn(shard):
        if any(i.item_id == "boom" for i in shard):
            raise RuntimeError("shard blew up")
        return _ok(shard)

    good = WorkItem(item_id="ok", source="/s/ok")
    bad = WorkItem(item_id="boom", source="/s/boom")
    results = await runner.run_shards([[good], [bad]], fn)
    by_id = {r.item_id: r for r in results}
    assert by_id["ok"].status == "completed"
    assert by_id["boom"].status == "failed"
    assert by_id["boom"].error == "RuntimeError"
