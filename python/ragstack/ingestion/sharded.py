"""Sharded ingestion: run a manifest of sources through the pipeline.

Ties a ``Manifest`` to the existing single-document ``IngestionPipeline`` via an
``IngestBackend``. Each item runs the full load→chunk→embed→replace pipeline;
per-item failures are isolated (one bad document never fails its shard), and the
backend bounds how many run at once. The 1-document case is just a 1-item
manifest, so the single-source path and the 500k path share one code path.
"""
from __future__ import annotations

import logging

from ragstack.ingestion.backends import IngestBackend, partition
from ragstack.ingestion.manifest import ItemResult, Manifest, WorkItem
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.jobstore import COMPLETED, FAILED, JobStore

log = logging.getLogger(__name__)


class ShardedIngestor:
    def __init__(
        self,
        pipeline: IngestionPipeline,
        backend: IngestBackend,
        shard_size: int = 64,
        job_store: JobStore | None = None,
    ) -> None:
        self._pipeline = pipeline
        self._backend = backend
        self._shard_size = shard_size
        self._job_store = job_store

    async def ingest_manifest(
        self, manifest: Manifest, job_id: str | None = None
    ) -> list[ItemResult]:
        """Process the manifest, returning a result per *processed* item.

        When a ``job_store`` and ``job_id`` are set, the run is **resumable**:
        items are registered, already-completed items are skipped, and each
        item's outcome is checkpointed as it finishes. Re-invoking with the same
        job_id after a crash processes only what's left.
        """
        items = manifest.items
        if self._job_store is not None and job_id is not None:
            await self._job_store.add_items(
                job_id, [(i.item_id, i.source) for i in items]
            )
            completed = await self._job_store.completed_item_ids(job_id)
            remaining = [i for i in items if i.item_id not in completed]
            skipped = len(items) - len(remaining)
            if skipped:
                log.info("resuming job %s: skipping %d completed item(s)", job_id, skipped)
            items = remaining

        shards = partition(items, self._shard_size)
        return await self._backend.run_shards(
            shards, lambda shard: self._run_shard(shard, job_id)
        )

    async def _run_shard(
        self, shard: list[WorkItem], job_id: str | None
    ) -> list[ItemResult]:
        results: list[ItemResult] = []
        for item in shard:
            result = await self._ingest_item(item)
            if self._job_store is not None and job_id is not None:
                await self._job_store.mark_item(
                    job_id,
                    item.item_id,
                    status=result.status,
                    chunk_ids=result.chunk_ids,
                    error=result.error,
                )
            results.append(result)
        return results

    async def _ingest_item(self, item: WorkItem) -> ItemResult:
        try:
            chunk_ids = await self._pipeline.ingest(item.source)
        except Exception as e:
            log.warning("ingest item %s failed: %s", item.item_id, e)
            return ItemResult(
                item_id=item.item_id,
                source=item.source,
                status=FAILED,
                error=type(e).__name__,
            )
        return ItemResult(
            item_id=item.item_id,
            source=item.source,
            status=COMPLETED,
            chunk_ids=chunk_ids,
        )
