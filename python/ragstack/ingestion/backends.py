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
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ragstack.ingestion.manifest import ItemResult, WorkItem
from ragstack.jobstore import FAILED

if TYPE_CHECKING:
    import httpx

    from ragstack.config import Settings

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


def make_ingest_backend(
    settings: Settings, *, http: httpx.AsyncClient | None = None
) -> IngestBackend:
    """Build the configured ``IngestBackend`` (the composition-root seam).

    ``ingest_backend="local"`` → :class:`LocalAsyncIORunner` (in-process, the
    default). ``"gowe"`` → a :class:`~ragstack.ingestion.gowe_backend.GoWeBackend`
    that submits each run's shards to the GoWe CWL engine. The GoWe stack is
    imported lazily so the default local path never pulls in the httpx workflow
    client. Raises ``ValueError`` with an actionable message on an unknown backend
    or missing/invalid GoWe config, so a misconfiguration fails fast at startup
    rather than on the first ingest.
    """
    backend = (settings.ingest_backend or "local").strip().lower()
    if backend == "local":
        return LocalAsyncIORunner(max_concurrency=settings.ingest_concurrency)
    if backend == "gowe":
        return _make_gowe_backend(settings, http)
    raise ValueError(
        f"unknown ingest_backend {settings.ingest_backend!r} (use 'local' or 'gowe')"
    )


def _make_gowe_backend(
    settings: Settings, http: httpx.AsyncClient | None
) -> IngestBackend:
    import json
    from pathlib import Path

    # Lazy: keep the httpx/workflow client out of the default local path.
    from ragstack.ingestion.gowe_backend import GoWeBackend
    from ragstack.ingestion.gowe_client import GoWeClient

    if not settings.gowe_workflow_cwl:
        raise ValueError(
            "ingest_backend=gowe requires gowe_workflow_cwl (path to the scatter CWL)"
        )
    # Resolve to an absolute path so a relative value doesn't silently depend on
    # the process CWD (which differs between `make run-python` and the deployed
    # unit); the error names the resolved path so a miss is diagnosable.
    cwl_path = Path(settings.gowe_workflow_cwl).expanduser().resolve()
    try:
        cwl = cwl_path.read_text(encoding="utf-8")
    except OSError as e:
        raise ValueError(f"gowe_workflow_cwl {str(cwl_path)!r} is unreadable: {e}") from e
    try:
        static_inputs = json.loads(settings.gowe_workflow_inputs_json or "{}")
    except json.JSONDecodeError as e:
        raise ValueError(f"gowe_workflow_inputs_json is not valid JSON: {e}") from e
    if not isinstance(static_inputs, dict):
        raise ValueError("gowe_workflow_inputs_json must be a JSON object")

    client = GoWeClient(
        base_url=settings.gowe_url, token=settings.gowe_token or None, http=http
    )
    return GoWeBackend(
        client,
        cwl,
        workflow_name=settings.gowe_workflow_name,
        static_inputs=static_inputs,
        worker_group=settings.gowe_worker_group or None,
        poll_interval=settings.gowe_poll_interval,
        timeout=settings.gowe_timeout,
    )
