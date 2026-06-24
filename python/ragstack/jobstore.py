"""Ingestion job tracking.

Backs the async ``/v1/ingest`` flow: the POST creates a job and returns
immediately, the pipeline runs in the background, and ``GET /v1/ingest/{job_id}``
reports real progress. ``InMemoryJobStore`` is process-local (dev/tests);
``SqliteJobStore`` is durable across restarts using only the stdlib. Postgres
lands in M2 as the single checkpoint of record for the 500k path.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from contextlib import closing
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

# Status vocabulary (mirror in contracts/openapi.yaml description).
ACCEPTED = "accepted"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
UNKNOWN = "unknown"


class IngestJob(BaseModel):
    """Tracked state of one ingestion request."""

    job_id: str
    status: str = ACCEPTED
    source: str = ""
    chunk_ids: list[str] = Field(default_factory=list)
    # Caller-safe error label only (e.g. exception class name) — never raw paths
    # or upstream messages, which would leak internals through the poll endpoint.
    error: str = ""


# Terminal states. A job not in one of these has an in-flight worker — which,
# since ingestion runs as in-process background tasks, cannot have survived a
# process restart.
_TERMINAL = (COMPLETED, FAILED)
# Error label for jobs whose worker died with the process (see fail_interrupted).
INTERRUPTED = "interrupted"


@runtime_checkable
class JobStore(Protocol):
    """Persist and update ingestion job state."""

    async def create(self, source: str) -> IngestJob: ...

    async def get(self, job_id: str) -> IngestJob | None: ...

    async def update(self, job_id: str, **fields: object) -> None: ...

    async def fail_interrupted(self) -> int: ...


class InMemoryJobStore:
    """Process-local job store. Loses state on restart — dev/tests only."""

    def __init__(self) -> None:
        self._jobs: dict[str, IngestJob] = {}
        self._lock = asyncio.Lock()

    async def create(self, source: str) -> IngestJob:
        job = IngestJob(job_id=str(uuid.uuid4()), status=ACCEPTED, source=source)
        async with self._lock:
            self._jobs[job.job_id] = job
        return job.model_copy()

    async def get(self, job_id: str) -> IngestJob | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            return job.model_copy() if job is not None else None

    async def update(self, job_id: str, **fields: object) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                self._jobs[job_id] = job.model_copy(update=fields)

    async def fail_interrupted(self) -> int:
        async with self._lock:
            swept = 0
            for job_id, job in self._jobs.items():
                if job.status not in _TERMINAL:
                    self._jobs[job_id] = job.model_copy(
                        update={"status": FAILED, "error": INTERRUPTED}
                    )
                    swept += 1
            return swept


class SqliteJobStore:
    """Durable job store backed by stdlib sqlite3.

    A connection is opened per operation and the call runs in a worker thread
    (``asyncio.to_thread``) so blocking sqlite never stalls the event loop.
    WAL mode allows the single background writer to coexist with status reads.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS jobs ("
                "  job_id TEXT PRIMARY KEY,"
                "  status TEXT NOT NULL,"
                "  source TEXT NOT NULL DEFAULT '',"
                "  chunk_ids TEXT NOT NULL DEFAULT '[]',"
                "  error TEXT NOT NULL DEFAULT ''"
                ")"
            )

    def _connect(self) -> sqlite3.Connection:
        # Callers must wrap this in ``closing(...)``: sqlite3's connection
        # context manager (``with conn:``) commits the transaction but does
        # *not* close the connection, so ``with conn:`` alone leaks a handle
        # per operation. The idiom is ``with closing(self._connect()) as conn, conn:``.
        conn = sqlite3.connect(self._path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @staticmethod
    def _row_to_job(row: tuple) -> IngestJob:
        job_id, status, source, chunk_ids, error = row
        return IngestJob(
            job_id=job_id,
            status=status,
            source=source,
            chunk_ids=json.loads(chunk_ids),
            error=error,
        )

    def _create_sync(self, source: str) -> IngestJob:
        job = IngestJob(job_id=str(uuid.uuid4()), status=ACCEPTED, source=source)
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO jobs (job_id, status, source, chunk_ids, error)"
                " VALUES (?, ?, ?, ?, ?)",
                (job.job_id, job.status, job.source, json.dumps(job.chunk_ids), job.error),
            )
        return job

    def _get_sync(self, job_id: str) -> IngestJob | None:
        with closing(self._connect()) as conn, conn:
            cur = conn.execute(
                "SELECT job_id, status, source, chunk_ids, error FROM jobs WHERE job_id = ?",
                (job_id,),
            )
            row = cur.fetchone()
        return self._row_to_job(row) if row is not None else None

    def _update_sync(self, job_id: str, fields: dict[str, object]) -> None:
        allowed = {"status", "source", "chunk_ids", "error"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return
        if "chunk_ids" in sets:
            sets["chunk_ids"] = json.dumps(sets["chunk_ids"])
        assignments = ", ".join(f"{k} = ?" for k in sets)
        with closing(self._connect()) as conn, conn:
            conn.execute(
                f"UPDATE jobs SET {assignments} WHERE job_id = ?",
                (*sets.values(), job_id),
            )

    async def create(self, source: str) -> IngestJob:
        return await asyncio.to_thread(self._create_sync, source)

    async def get(self, job_id: str) -> IngestJob | None:
        return await asyncio.to_thread(self._get_sync, job_id)

    async def update(self, job_id: str, **fields: object) -> None:
        await asyncio.to_thread(self._update_sync, job_id, fields)

    def _fail_interrupted_sync(self) -> int:
        with closing(self._connect()) as conn, conn:
            cur = conn.execute(
                "UPDATE jobs SET status = ?, error = ? WHERE status NOT IN (?, ?)",
                (FAILED, INTERRUPTED, COMPLETED, FAILED),
            )
            return cur.rowcount

    async def fail_interrupted(self) -> int:
        return await asyncio.to_thread(self._fail_interrupted_sync)


def make_job_store(backend: str, path: str) -> JobStore:
    """Build the configured job store (``memory`` | ``sqlite``)."""
    if backend == "sqlite":
        return SqliteJobStore(path)
    return InMemoryJobStore()
