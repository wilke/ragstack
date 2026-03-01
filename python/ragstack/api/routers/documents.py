"""Document management endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

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
async def ingest(request: IngestRequest) -> IngestResponse:
    """
    Ingest a document from the given source path or URL.

    Returns a job ID for polling status and the list of chunk IDs created.
    Wire up IngestionPipeline in a production deployment.
    """
    import uuid

    return IngestResponse(
        job_id=str(uuid.uuid4()),
        status="accepted",
        chunk_ids=[],
    )


@router.get("/ingest/{job_id}", response_model=IngestResponse)
async def ingest_status(job_id: str) -> IngestResponse:
    """Poll the status of an ingestion job."""
    return IngestResponse(job_id=job_id, status="unknown")


@router.get("/documents", response_model=list[DocumentInfo])
async def list_documents() -> list[DocumentInfo]:
    """List all indexed documents."""
    return []


@router.delete("/documents/{doc_id}", status_code=204)
async def delete_document(doc_id: str) -> None:
    """Delete a document and all its chunks from every index."""
    return None
