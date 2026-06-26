"""Document management endpoints."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field

from ragstack.api.deps import (
    get_ingestor,
    get_job_store,
    get_text_index,
    get_vector_store,
)
from ragstack.api.security import resolve_tenant
from ragstack.config import settings
from ragstack.ingestion.loaders import DEFAULT_INGEST_SUFFIXES
from ragstack.ingestion.manifest import build_manifest
from ragstack.ingestion.sharded import ShardedIngestor
from ragstack.jobstore import COMPLETED, FAILED, PENDING, RUNNING, UNKNOWN, JobStore

log = logging.getLogger(__name__)

router = APIRouter()


def _final_status(counts: dict[str, int]) -> str:
    """Decide a run's overall status from its per-item counts.

    ``completed`` when at least one item completed (partial failures still
    surface via ``items.failed``) or there were no items at all. ``failed`` when
    there were items but none completed — covering both all-failed and the
    leftover-``pending`` case (a shard that raised wholesale reports its items
    failed but never checkpoints them, so they linger pending; without counting
    those, such a run would falsely read completed).
    """
    total = sum(counts.values())
    return FAILED if total > 0 and counts[COMPLETED] == 0 else COMPLETED


async def _run_ingest(
    job_store: JobStore,
    ingestor: ShardedIngestor,
    ingest_root: str,
    job_id: str,
    source: str,
    tenant_id: str,
) -> None:
    """Background worker: expand the source into a manifest and run it.

    A single file is a 1-item manifest, so files and directories share one path.
    Per-item progress is checkpointed by the ingestor; here we set the overall
    job status. Never raises — a run-level failure is captured as a caller-safe
    label. The job is ``failed`` only when the run itself errors or *every* item
    fails; partial failures leave it ``completed`` with non-zero ``items.failed``.
    """
    await job_store.update(job_id, status=RUNNING)
    try:
        manifest = build_manifest(
            source, suffixes=DEFAULT_INGEST_SUFFIXES, ingest_root=ingest_root or None
        )
        results = await ingestor.ingest_manifest(
            manifest, job_id=job_id, tenant_id=tenant_id
        )
    except Exception as e:
        log.warning("ingest job %s failed: %s", job_id, e)
        await job_store.update(job_id, status=FAILED, error=type(e).__name__)
        return

    counts = await job_store.item_counts(job_id)
    final = _final_status(counts)

    fields: dict[str, object] = {"status": final}
    # Surface chunk ids for the single-document case (back-compat); a batch run
    # reports progress via item counts instead of an unbounded id list. Only set
    # chunk_ids when this run actually produced them — passing [] on a resume
    # that skipped the (already-completed) item would erase the stored ids.
    if len(results) == 1 and results[0].status == COMPLETED:
        fields["chunk_ids"] = results[0].chunk_ids
    await job_store.update(job_id, **fields)


class IngestRequest(BaseModel):
    source: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestItemCounts(BaseModel):
    total: int = 0
    completed: int = 0
    failed: int = 0
    pending: int = 0


class IngestResponse(BaseModel):
    job_id: str
    status: str
    chunk_ids: list[str] = Field(default_factory=list)
    items: IngestItemCounts | None = None


class DocumentInfo(BaseModel):
    doc_id: str
    source: str
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.post("/ingest", response_model=IngestResponse)
async def ingest(
    request: IngestRequest,
    background_tasks: BackgroundTasks,
    tenant: str = Depends(resolve_tenant),
    ingestor: ShardedIngestor = Depends(get_ingestor),
    job_store: JobStore = Depends(get_job_store),
) -> IngestResponse:
    """Accept a file or directory for ingestion and run it in the background.

    `request.source` is a path the loader can read (confined to INGEST_ROOT
    when configured). A directory is ingested recursively (.pdf/.txt/.md). Returns
    immediately with a real job_id and `status="accepted"`; poll
    `GET /v1/ingest/{job_id}` for progress (including per-item counts).

    Re-ingesting the same source replaces each document's existing chunks
    (deterministic doc id) rather than duplicating them. A document that yields
    no embeddable chunks fails that item and leaves its prior version intact.

    Each call mints a new job_id, so re-submitting re-processes every document
    (idempotent, but not cheap — it re-embeds). The per-item checkpoint makes a
    run resumable at the ingestor level, but the public API does not yet accept a
    job_id to resume a specific prior run; that wiring is tracked for M2.
    """
    job = await job_store.create(source=request.source)
    background_tasks.add_task(
        _run_ingest,
        job_store,
        ingestor,
        settings.ingest_root,
        job.job_id,
        request.source,
        tenant,
    )
    return IngestResponse(job_id=job.job_id, status=job.status)


@router.get("/ingest/{job_id}", response_model=IngestResponse)
async def ingest_status(
    job_id: str,
    job_store: JobStore = Depends(get_job_store),
) -> IngestResponse:
    """Poll ingestion job status: accepted → running → completed | failed.

    `items` reports per-document progress (total/completed/failed/pending) for
    batch runs. An unrecognized job_id reports status "unknown" (200) rather than
    404, so polling is idempotent and matches the response contract.
    """
    job = await job_store.get(job_id)
    if job is None:
        return IngestResponse(job_id=job_id, status=UNKNOWN)
    counts = await job_store.item_counts(job_id)
    total = sum(counts.values())
    items = (
        IngestItemCounts(
            total=total,
            completed=counts[COMPLETED],
            failed=counts[FAILED],
            pending=counts[PENDING],
        )
        if total
        else None
    )
    return IngestResponse(
        job_id=job.job_id, status=job.status, chunk_ids=job.chunk_ids, items=items
    )


@router.get("/documents", response_model=list[DocumentInfo])
async def list_documents() -> list[DocumentInfo]:
    """List indexed documents.

    Not yet implemented — needs a metadata store (Postgres) to maintain
    a document registry; the vector store has chunks, not documents.
    """
    return []


@router.delete("/documents/{doc_id}", status_code=204)
async def delete_document(
    doc_id: str,
    tenant: str = Depends(resolve_tenant),
    vector_store=Depends(get_vector_store),
    text_index=Depends(get_text_index),
) -> None:
    """Delete a document and its chunks — scoped to the caller's tenant, so one
    tenant cannot delete another's document even by id. Purge both retrieval
    legs (vector + text) so a deleted doc can't resurface via BM25."""
    await vector_store.delete(doc_id, tenant_id=tenant)
    await text_index.delete(doc_id, tenant_id=tenant)
