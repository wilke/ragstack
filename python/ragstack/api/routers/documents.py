"""Document management endpoints."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field

from ragstack.api.deps import get_ingestor, get_job_store, get_vector_store
from ragstack.config import settings
from ragstack.ingestion.manifest import build_manifest
from ragstack.ingestion.sharded import ShardedIngestor
from ragstack.jobstore import COMPLETED, FAILED, PENDING, RUNNING, UNKNOWN, JobStore

log = logging.getLogger(__name__)

# Extensions the loader registry can handle; a directory ingest only enqueues these.
_INGEST_SUFFIXES = [".pdf", ".txt", ".md"]

router = APIRouter()


def _confine(source: str, ingest_root: str) -> None:
    """Fail fast if a source path escapes the configured ingest root."""
    if not ingest_root:
        return
    resolved = Path(source).resolve()
    root = Path(ingest_root).resolve()
    if resolved != root and not resolved.is_relative_to(root):
        raise PermissionError("source is outside the permitted ingest root")


async def _run_ingest(
    job_store: JobStore, ingestor: ShardedIngestor, ingest_root: str, job_id: str, source: str
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
        _confine(source, ingest_root)
        manifest = build_manifest(source, suffixes=_INGEST_SUFFIXES)
        results = await ingestor.ingest_manifest(manifest, job_id=job_id)
    except Exception as e:
        log.warning("ingest job %s failed: %s", job_id, e)
        await job_store.update(job_id, status=FAILED, error=type(e).__name__)
        return

    counts = await job_store.item_counts(job_id)
    final = FAILED if counts[COMPLETED] == 0 and counts[FAILED] > 0 else COMPLETED
    # Surface chunk ids for the single-document case (back-compat); a batch run
    # reports progress via item counts instead of an unbounded id list.
    chunk_ids = (
        results[0].chunk_ids
        if len(results) == 1 and results[0].status == COMPLETED
        else []
    )
    await job_store.update(job_id, status=final, chunk_ids=chunk_ids)


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
    ingestor: ShardedIngestor = Depends(get_ingestor),
    job_store: JobStore = Depends(get_job_store),
) -> IngestResponse:
    """Accept a file or directory for ingestion and run it in the background.

    `request.source` is a path the loader can read (confined to INGEST_ROOT
    when configured). A directory is ingested recursively (.pdf/.txt/.md). Returns
    immediately with a real job_id and `status="accepted"`; poll
    `GET /v1/ingest/{job_id}` for progress (including per-item counts).

    Re-ingesting the same source replaces each document's existing chunks
    (deterministic doc id) rather than duplicating them, and resumes a prior job
    of the same id by skipping already-completed items. A document that yields no
    embeddable chunks fails that item and leaves its prior version intact.
    """
    job = await job_store.create(source=request.source)
    background_tasks.add_task(
        _run_ingest, job_store, ingestor, settings.ingest_root, job.job_id, request.source
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
    total = counts[PENDING] + counts[COMPLETED] + counts[FAILED]
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
    vector_store=Depends(get_vector_store),
) -> None:
    """Delete a document and all its chunks from the vector store."""
    await vector_store.delete(doc_id)
