"""Distribution backends for sharded ingestion.

The ``IngestBackend`` seam decouples *what* runs (a shard of work items) from
*where* it runs. ``LocalAsyncIORunner`` is the single-host implementation —
bounded asyncio concurrency, no broker. A Parsl / GoWe / k8s runner can
implement the same protocol later (one task = one shard) without touching the
pipeline. This is the seam the "single host now, cluster later" decision rests on.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from ragstack.ingestion.manifest import ItemResult, WorkItem
from ragstack.jobstore import FAILED

# A shard processor: given a shard (list of work items), return one result each.
ShardFn = Callable[[list[WorkItem]], Awaitable[list[ItemResult]]]


def partition(items: list[WorkItem], shard_size: int) -> list[list[WorkItem]]:
    """Split items into shards of at most ``shard_size``."""
    if shard_size < 1:
        raise ValueError("shard_size must be >= 1")
    return [items[i : i + shard_size] for i in range(0, len(items), shard_size)]


@runtime_checkable
class IngestBackend(Protocol):
    """Run shards of work, returning a flat list of per-item results."""

    async def run_shards(
        self, shards: list[list[WorkItem]], shard_fn: ShardFn
    ) -> list[ItemResult]: ...


class LocalAsyncIORunner:
    """Single-host backend: run shards concurrently under a semaphore.

    Concurrency is bounded so a large run can't open unbounded in-flight work
    (e.g. thousands of simultaneous embed requests). A shard whose processor
    raises wholesale is not fatal: its items are recorded as failed and the run
    continues.
    """

    def __init__(self, max_concurrency: int = 4) -> None:
        self._max = max(1, max_concurrency)

    async def run_shards(
        self, shards: list[list[WorkItem]], shard_fn: ShardFn
    ) -> list[ItemResult]:
        sem = asyncio.Semaphore(self._max)

        async def _one(shard: list[WorkItem]) -> list[ItemResult]:
            async with sem:
                return await shard_fn(shard)

        gathered = await asyncio.gather(
            *(_one(s) for s in shards), return_exceptions=True
        )
        out: list[ItemResult] = []
        for shard, res in zip(shards, gathered, strict=True):
            if isinstance(res, BaseException):
                out.extend(
                    ItemResult(
                        item_id=i.item_id,
                        source=i.source,
                        status=FAILED,
                        error=type(res).__name__,
                    )
                    for i in shard
                )
            else:
                out.extend(res)
        return out
