"""Document management endpoints."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ragstack.api.deps import get_pipeline, get_vector_store
from ragstack.ingestion.pipeline import IngestionPipeline

router = APIRouter()


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
    pipeline: IngestionPipeline = Depends(get_pipeline),
) -> IngestResponse:
    """Ingest a document synchronously: load → chunk → embed → upsert.

    `request.source` is a filesystem path the loader can read. Returns a
    job_id for forward-compat with an eventual async queue, plus the IDs
    of the chunks that landed in the vector store.
    """
    try:
        chunk_ids = await pipeline.ingest(request.source)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=f"source not found: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ingest failed: {e}")
    return IngestResponse(
        job_id=str(uuid.uuid4()),
        status="completed",
        chunk_ids=chunk_ids,
    )


@router.get("/ingest/{job_id}", response_model=IngestResponse)
async def ingest_status(job_id: str) -> IngestResponse:
    """Poll ingestion job status.

    Ingestion is currently synchronous, so once /ingest returns the job
    is already complete. Persisting per-job state would require a metadata
    store (planned alongside the Celery queue).
    """
    return IngestResponse(job_id=job_id, status="unknown")


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
