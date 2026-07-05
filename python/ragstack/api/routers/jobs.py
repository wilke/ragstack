"""Ingest job listing for the Ops dashboard (role: ``admin``).

Included under the ``require_role(ROLE_ADMIN)`` group in ``api/main.py``. Jobs
aren't tenant-stamped yet (their ``source`` is a raw path — a cross-tenant leak
if exposed to non-admins; see #85), so this list is **admin-only**: an admin may
see every run. The single-job poll ``GET /v1/ingest/{job_id}`` stays the
tenant-agnostic per-run endpoint.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ragstack.api.deps import get_job_store
from ragstack.jobstore import COMPLETED, FAILED, PENDING, JobStore

router = APIRouter()


class JobItemCounts(BaseModel):
    pending: int = 0
    completed: int = 0
    failed: int = 0


class JobSummary(BaseModel):
    job_id: str
    status: str
    source: str
    error: str
    chunks: int  # chunk_ids stamped on the job record (single-doc runs)
    items: JobItemCounts  # per-item counts (resumable manifest runs; zeros otherwise)


class JobsResponse(BaseModel):
    jobs: list[JobSummary]


@router.get("/jobs", response_model=JobsResponse)
async def list_jobs(
    limit: int = Query(default=25, ge=1, le=100),
    job_store: JobStore = Depends(get_job_store),
) -> JobsResponse:
    """Most-recent ingest runs with status + progress counts (admin only)."""
    jobs = await job_store.list_jobs(limit=limit)
    out: list[JobSummary] = []
    for j in jobs:
        counts = await job_store.item_counts(j.job_id)
        out.append(JobSummary(
            job_id=j.job_id,
            status=j.status,
            source=j.source,
            error=j.error,
            chunks=len(j.chunk_ids),
            items=JobItemCounts(
                pending=counts.get(PENDING, 0),
                completed=counts.get(COMPLETED, 0),
                failed=counts.get(FAILED, 0),
            ),
        ))
    return JobsResponse(jobs=out)
