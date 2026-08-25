"""Sharded ingestion: run a manifest of sources through the pipeline.

Ties a ``Manifest`` to the existing single-document ``IngestionPipeline`` via an
``IngestBackend``. Each item runs the full load→chunk→embed→replace pipeline;
per-item failures are isolated (one bad document never fails its shard), and the
backend bounds how many run at once. The 1-document case is just a 1-item
manifest, so the single-source path and the 500k path share one code path.

The per-collection chunk cap (#291) is enforced here, ONCE per manifest run —
the manifest IS the job on the API/local path: every remaining item is
prepared (loaded + chunked, text only, no GPU, no store) so the job's
``incoming`` chunk count is exact, ONE live ``vector_store.count()`` is taken,
and a job that would cross the cap is refused whole before its first embed or
write. An admitted job then embeds and indexes the very chunks it was sized
from (``IngestionPipeline.ingest_prepared``) — nothing is loaded or chunked
twice. An uncapped run (``chunk_cap=None``) takes the original per-item path
untouched. One ordering difference on the capped path: load + chunk of every
item runs serially in the gate, before the first embed (the uncapped path
loads inside the shard-parallel run); the gate could later be spread over
``self._backend.run_shards`` if sizing ever dominates.
"""
from __future__ import annotations

import logging

from ragstack.ingestion.backends import IngestBackend, partition
from ragstack.ingestion.chunk_cap import ChunkCapExceeded
from ragstack.ingestion.loaders import NO_TEXT_ERROR, NO_TEXT_LABEL
from ragstack.ingestion.manifest import ItemResult, Manifest, WorkItem
from ragstack.ingestion.pipeline import IngestionPipeline, PreparedSource
from ragstack.jobstore import COMPLETED, FAILED, JobStore
from ragstack.quota import TenantQuota
from ragstack.tenancy import DEFAULT_TENANT

log = logging.getLogger(__name__)

#: Per item, after the cap gate: the prepared source to embed+index, or the
#: failure its load/chunk step already produced (returned as-is, never retried).
Prepared = dict[str, PreparedSource | ItemResult]


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
        chunk_cap: int | None = None,
    ) -> list[ItemResult]:
        """Process the manifest, returning a result per *processed* item.

        When a ``job_store`` and ``job_id`` are set, the run is **resumable**:
        items are registered, already-completed items are skipped, and each
        item's outcome is checkpointed as it finishes. Re-invoking with the same
        job_id after a crash processes only what's left.

        ``chunk_cap`` (#291): the collection's chunk cap, or ``None`` for
        unlimited. With a cap the whole run is sized and admitted first (see
        the module docstring) and raises :class:`ChunkCapExceeded` — with every
        remaining item checkpointed ``failed`` under the refusal — when
        ``live + incoming`` would exceed it. Nothing is written in that case.
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

        # The cap gate: one count, one prepare pass, before the first write.
        # Only the REMAINING items count as incoming — a resumed job's completed
        # items are already in the live figure.
        prepared = (
            await self._admit(items, job_id, chunk_cap) if chunk_cap is not None else None
        )

        shards = partition(items, self._shard_size)
        results = await self._backend.run_shards(
            shards, lambda shard: self._run_shard(shard, job_id, tenant_id, prepared)
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

    async def _admit(
        self, items: list[WorkItem], job_id: str | None, chunk_cap: int
    ) -> Prepared:
        """Size the job and check it against ``chunk_cap`` — ONE live count and
        one text-only prepare pass over ``items``; no embed, no store write.

        The count is taken FIRST so the pass can stop *retaining* chunks the
        moment the job is known to be refused (it keeps counting, so the
        reported ``incoming`` stays exact): memory is bounded by roughly the
        cap, not by the job. A source whose load/chunk step fails is recorded
        as that item's failure (excluded from ``incoming``, never retried),
        exactly as the per-item path would have failed it.

        On refusal every remaining item is checkpointed ``failed`` with the
        formatted refusal (the four numbers travel on the items; the job
        carries the bare label) — except an item whose load already failed,
        which keeps its own, more specific label — and
        :class:`ChunkCapExceeded` is raised.
        ``incoming`` is the post-chunk, pre-quarantine count — a conservative
        overcount by any chunks the embedder would have quarantined.
        """
        live = int(await self._pipeline.vector_store.count())
        prepared: Prepared = {}
        incoming = 0
        for item in items:
            try:
                p = await self._pipeline.prepare_source(item.source)
            except Exception as e:  # noqa: BLE001 — the item fails; the job goes on
                log.warning("ingest item %s failed: %s", item.item_id, e)
                prepared[item.item_id] = ItemResult(
                    item_id=item.item_id, source=item.source, status=FAILED,
                    error=getattr(e, "job_error", type(e).__name__),
                )
                continue
            incoming += len(p.chunks)
            if live + incoming > chunk_cap:
                p.chunks = []  # refused: stop holding text, keep counting
            prepared[item.item_id] = p
        if live + incoming > chunk_cap:
            exc = ChunkCapExceeded(live, incoming, chunk_cap)
            log.warning("ingest job %s refused: %s", job_id, exc)
            if self._job_store is not None and job_id is not None:
                for item in items:
                    own = prepared.get(item.item_id)
                    error = own.error if isinstance(own, ItemResult) else str(exc)
                    await self._job_store.mark_item(
                        job_id, item.item_id, status=FAILED, error=error
                    )
            raise exc
        return prepared

    async def _run_shard(
        self,
        shard: list[WorkItem],
        job_id: str | None,
        tenant_id: str,
        prepared: Prepared | None = None,
    ) -> list[ItemResult]:
        results: list[ItemResult] = []
        for item in shard:
            if prepared is None:
                result = await self._ingest_item(item, tenant_id)
            else:
                # Consume the item ONCE: ``_embed_and_link`` sets ``embedding``
                # in place on these very chunk objects, so a reference left in
                # the dict would pin every embedded vector of the job until the
                # run returns (measured: 1,000 x 52 chunks at 1024-d, 19 MiB
                # uncapped vs 1.7 GiB retained). Popped, each item's vectors
                # are released as soon as its upsert is done.
                result = await self._ingest_prepared_item(
                    item, prepared.pop(item.item_id), tenant_id
                )
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

    async def _ingest_prepared_item(
        self, item: WorkItem, prepared: PreparedSource | ItemResult, tenant_id: str
    ) -> ItemResult:
        """The admitted-job twin of :meth:`_ingest_item`: embed + index the
        chunks the gate already sized, under the same quota slot. A failure the
        gate recorded is returned as-is."""
        if isinstance(prepared, ItemResult):
            return prepared
        try:
            async with self._quota.slot(tenant_id):
                chunk_ids = await self._pipeline.ingest_prepared(prepared, tenant_id=tenant_id)
        except Exception as e:
            log.warning("ingest item %s failed: %s", item.item_id, e)
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
