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
from ragstack.ingestion.loaders import NO_TEXT_ERROR, NO_TEXT_LABEL
from ragstack.ingestion.manifest import ItemResult, Manifest, WorkItem
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.jobstore import COMPLETED, FAILED, JobStore
from ragstack.quota import TenantQuota
from ragstack.tenancy import DEFAULT_TENANT

log = logging.getLogger(__name__)


class ShardedIngestor:
    def __init__(
        self,
        pipeline: IngestionPipeline,
        backend: IngestBackend,
        shard_size: int = 64,
        job_store: JobStore | None = None,
        quota: TenantQuota | None = None,
    ) -> None:
        self._pipeline = pipeline
        self._backend = backend
        self._shard_size = shard_size
        self._job_store = job_store
        # No quota configured → unlimited (a disabled TenantQuota).
        self._quota = quota or TenantQuota(0)

    async def ingest_manifest(
        self,
        manifest: Manifest,
        job_id: str | None = None,
        tenant_id: str = DEFAULT_TENANT,
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
        results = await self._backend.run_shards(
            shards, lambda shard: self._run_shard(shard, job_id, tenant_id)
        )
        # The scanned-PDF count, per run, at INFO (#202): the per-item error is
        # the constant NO_TEXT_ERROR string (so the SQL job stores can GROUP BY
        # it too); this line is the aggregate the OCR decision is made from.
        no_text = sum(1 for r in results if r.error == NO_TEXT_ERROR)
        if no_text:
            log.info(
                "ingest job %s: %d of %d item(s) failed with %s [%s]",
                job_id, no_text, len(results), NO_TEXT_ERROR, NO_TEXT_LABEL,
            )
        return results

    async def _run_shard(
        self, shard: list[WorkItem], job_id: str | None, tenant_id: str
    ) -> list[ItemResult]:
        results: list[ItemResult] = []
        for item in shard:
            result = await self._ingest_item(item, tenant_id)
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

    async def _ingest_item(self, item: WorkItem, tenant_id: str) -> ItemResult:
        try:
            # Hold one of the tenant's concurrency slots so a single tenant can't
            # monopolize the shared embedding fleet during a large ingest.
            async with self._quota.slot(tenant_id):
                chunk_ids = await self._pipeline.ingest(item.source, tenant_id=tenant_id)
        except Exception as e:
            log.warning("ingest item %s failed: %s", item.item_id, e)
            # Caller-safe label: the exception's class name, unless the error
            # type carries an actionable constant (NoTextExtracted.job_error).
            return ItemResult(
                item_id=item.item_id,
                source=item.source,
                status=FAILED,
                error=getattr(e, "job_error", type(e).__name__),
            )
        return ItemResult(
            item_id=item.item_id,
            source=item.source,
            status=COMPLETED,
            chunk_ids=chunk_ids,
        )
