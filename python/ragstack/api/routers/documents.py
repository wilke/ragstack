"""Document management endpoints."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field

from ragstack.api.deps import get_job_store, get_pipeline, get_vector_store
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.jobstore import COMPLETED, FAILED, RUNNING, UNKNOWN, JobStore

log = logging.getLogger(__name__)

router = APIRouter()


async def _run_ingest(
    job_store: JobStore, pipeline: IngestionPipeline, job_id: str, source: str
) -> None:
    """Background worker: run the pipeline and record the outcome on the job.

    Never raises — any failure is captured on the job as a caller-safe label so
    the poll endpoint can report it without leaking paths or internals.
    """
    await job_store.update(job_id, status=RUNNING)
    try:
        chunk_ids = await pipeline.ingest(source)
    except Exception as e:
        log.warning("ingest job %s failed: %s", job_id, e)
        await job_store.update(job_id, status=FAILED, error=type(e).__name__)
        return
    await job_store.update(job_id, status=COMPLETED, chunk_ids=chunk_ids)


class IngestRequest(BaseModel):
    source: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestResponse(BaseModel):
    job_id: str
    status: str
    chunk_ids: list[str] = Field(default_factory=list)


class DocumentInfo(BaseModel):
    doc_id: str
    source: str
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.post("/ingest", response_model=IngestResponse)
async def ingest(
    request: IngestRequest,
    background_tasks: BackgroundTasks,
    pipeline: IngestionPipeline = Depends(get_pipeline),
    job_store: JobStore = Depends(get_job_store),
) -> IngestResponse:
    """Accept a document for ingestion and run the pipeline in the background.

    `request.source` is a path the loader can read (confined to INGEST_ROOT
    when configured). Returns immediately with a real job_id and
    `status="accepted"`; poll `GET /v1/ingest/{job_id}` for progress.
    """
    job = await job_store.create(source=request.source)
    background_tasks.add_task(_run_ingest, job_store, pipeline, job.job_id, request.source)
    return IngestResponse(job_id=job.job_id, status=job.status)


@router.get("/ingest/{job_id}", response_model=IngestResponse)
async def ingest_status(
    job_id: str,
    job_store: JobStore = Depends(get_job_store),
) -> IngestResponse:
    """Poll ingestion job status: accepted → running → completed | failed.

    An unrecognized job_id reports status "unknown" (200) rather than 404, so
    polling is idempotent and matches the response contract.
    """
    job = await job_store.get(job_id)
    if job is None:
        return IngestResponse(job_id=job_id, status=UNKNOWN)
    return IngestResponse(job_id=job.job_id, status=job.status, chunk_ids=job.chunk_ids)


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
