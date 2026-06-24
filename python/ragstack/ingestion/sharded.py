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
from ragstack.jobstore import COMPLETED, FAILED

log = logging.getLogger(__name__)


class ShardedIngestor:
    def __init__(
        self,
        pipeline: IngestionPipeline,
        backend: IngestBackend,
        shard_size: int = 64,
    ) -> None:
        self._pipeline = pipeline
        self._backend = backend
        self._shard_size = shard_size

    async def ingest_manifest(self, manifest: Manifest) -> list[ItemResult]:
        """Process every item in the manifest, returning a result for each."""
        shards = partition(manifest.items, self._shard_size)
        return await self._backend.run_shards(shards, self._run_shard)

    async def _run_shard(self, shard: list[WorkItem]) -> list[ItemResult]:
        results: list[ItemResult] = []
        for item in shard:
            results.append(await self._ingest_item(item))
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
