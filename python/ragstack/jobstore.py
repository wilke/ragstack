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
# Per-item state (resumable manifest runs): an item not yet attempted.
PENDING = "pending"


class IngestJob(BaseModel):
    """Tracked state of one ingestion request."""

    job_id: str
    status: str = ACCEPTED
    source: str = ""
    chunk_ids: list[str] = Field(default_factory=list)
    # Caller-safe error label only (e.g. exception class name) — never raw paths
    # or upstream messages, which would leak internals through the poll endpoint.
    error: str = ""


class JobItem(BaseModel):
    """Per-item state within a job — the unit a resumable run checkpoints and
    skips. ``item_id`` equals the manifest/document id."""

    job_id: str
    item_id: str
    source: str = ""
    status: str = PENDING  # pending | completed | failed
    chunk_ids: list[str] = Field(default_factory=list)
    error: str = ""


# Terminal states. A job not in one of these has an in-flight worker — which,
# since ingestion runs as in-process background tasks, cannot have survived a
# process restart.
_TERMINAL = (COMPLETED, FAILED)
# Error label for jobs whose worker died with the process (see fail_interrupted).
INTERRUPTED = "interrupted"

# Table DDL, shared verbatim by the sqlite and postgres stores (both dialects
# accept this CREATE TABLE form), so the schema lives in one place.
_JOBS_DDL = (
    "CREATE TABLE IF NOT EXISTS jobs ("
    "  job_id TEXT PRIMARY KEY,"
    "  status TEXT NOT NULL,"
    "  source TEXT NOT NULL DEFAULT '',"
    "  chunk_ids TEXT NOT NULL DEFAULT '[]',"
    "  error TEXT NOT NULL DEFAULT ''"
    ")"
)
_JOB_ITEMS_DDL = (
    "CREATE TABLE IF NOT EXISTS job_items ("
    "  job_id TEXT NOT NULL,"
    "  item_id TEXT NOT NULL,"
    "  source TEXT NOT NULL DEFAULT '',"
    "  status TEXT NOT NULL DEFAULT 'pending',"
    "  chunk_ids TEXT NOT NULL DEFAULT '[]',"
    "  error TEXT NOT NULL DEFAULT '',"
    "  PRIMARY KEY (job_id, item_id)"
    ")"
)


# The job columns an update() may set. Shared by both SQL stores so the
# updatable set and the chunk_ids serialization convention live in one place.
_JOB_UPDATE_COLUMNS = ("status", "source", "chunk_ids", "error")


def _zero_item_counts() -> dict[str, int]:
    """The per-status counts dict seeded to zero (the shape all stores return)."""
    return dict.fromkeys((PENDING, COMPLETED, FAILED), 0)


def _prepare_job_update(fields: dict[str, object]) -> dict[str, object]:
    """Filter an update to the allowed job columns and JSON-encode chunk_ids.
    The dialect-independent half of update(); each SQL store renders its own
    placeholders from the returned dict's keys."""
    sets = {k: v for k, v in fields.items() if k in _JOB_UPDATE_COLUMNS}
    if "chunk_ids" in sets:
        sets["chunk_ids"] = json.dumps(sets["chunk_ids"])
    return sets


def _fold_status_counts(rows: list[tuple[str, int]]) -> dict[str, int]:
    """Fold ``(status, count)`` rows onto the zero-seeded counts dict — the
    shared tail of every SQL ``item_counts`` (GROUP BY status)."""
    counts = _zero_item_counts()
    for status, n in rows:
        counts[status] = n
    return counts


@runtime_checkable
class JobStore(Protocol):
    """Persist and update ingestion job state."""

    async def create(self, source: str) -> IngestJob: ...

    async def get(self, job_id: str) -> IngestJob | None: ...

    async def list_jobs(self, limit: int = 25) -> list[IngestJob]:
        """Most-recent-first job list, capped at ``limit``. Powers the admin Ops
        jobs panel. Not tenant-scoped — jobs aren't tenant-stamped yet, so the
        endpoint is admin-only (an admin may see all runs)."""
        ...

    async def update(self, job_id: str, **fields: object) -> None: ...

    async def fail_interrupted(self) -> int: ...

    # --- per-item state (resumable manifest runs) ---

    async def add_items(self, job_id: str, items: list[tuple[str, str]]) -> None:
        """Register (item_id, source) pairs as pending. Idempotent: existing
        items keep their state, so re-running a job preserves prior progress."""
        ...

    async def mark_item(
        self,
        job_id: str,
        item_id: str,
        status: str,
        chunk_ids: list[str] | None = None,
        error: str = "",
    ) -> None: ...

    async def completed_item_ids(self, job_id: str) -> set[str]: ...

    async def item_counts(self, job_id: str) -> dict[str, int]: ...

    async def close(self) -> None:
        """Release any held resources (e.g. a connection pool). No-op for the
        in-memory / connection-per-op stores."""
        ...


class InMemoryJobStore:
    """Process-local job store. Loses state on restart — dev/tests only."""

    def __init__(self) -> None:
        self._jobs: dict[str, IngestJob] = {}
        self._items: dict[str, dict[str, JobItem]] = {}
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

    async def list_jobs(self, limit: int = 25) -> list[IngestJob]:
        async with self._lock:
            # Dict preserves insertion order; newest-first, capped.
            jobs = list(self._jobs.values())[-limit:]
            return [j.model_copy() for j in reversed(jobs)]

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

    async def add_items(self, job_id: str, items: list[tuple[str, str]]) -> None:
        async with self._lock:
            bucket = self._items.setdefault(job_id, {})
            for item_id, source in items:
                if item_id not in bucket:
                    bucket[item_id] = JobItem(
                        job_id=job_id, item_id=item_id, source=source
                    )

    async def mark_item(
        self,
        job_id: str,
        item_id: str,
        status: str,
        chunk_ids: list[str] | None = None,
        error: str = "",
    ) -> None:
        async with self._lock:
            bucket = self._items.setdefault(job_id, {})
            item = bucket.get(item_id) or JobItem(job_id=job_id, item_id=item_id)
            bucket[item_id] = item.model_copy(
                update={"status": status, "chunk_ids": chunk_ids or [], "error": error}
            )

    async def completed_item_ids(self, job_id: str) -> set[str]:
        async with self._lock:
            return {
                iid
                for iid, it in self._items.get(job_id, {}).items()
                if it.status == COMPLETED
            }

    async def item_counts(self, job_id: str) -> dict[str, int]:
        async with self._lock:
            counts = _zero_item_counts()
            for it in self._items.get(job_id, {}).values():
                counts[it.status] = counts.get(it.status, 0) + 1
            return counts

    async def close(self) -> None:
        """No resources to release."""


class SqliteJobStore:
    """Durable job store backed by stdlib sqlite3.

    A connection is opened per operation and the call runs in a worker thread
    (``asyncio.to_thread``) so blocking sqlite never stalls the event loop.
    WAL mode allows the single background writer to coexist with status reads.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        with closing(self._connect()) as conn, conn:
            conn.execute(_JOBS_DDL)
            conn.execute(_JOB_ITEMS_DDL)

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
        sets = _prepare_job_update(fields)
        if not sets:
            return
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

    def _list_jobs_sync(self, limit: int) -> list[IngestJob]:
        # Implicit rowid ascends with insertion; DESC gives newest-first.
        with closing(self._connect()) as conn, conn:
            cur = conn.execute(
                "SELECT job_id, status, source, chunk_ids, error FROM jobs"
                " ORDER BY rowid DESC LIMIT ?",
                (limit,),
            )
            rows = cur.fetchall()
        return [self._row_to_job(r) for r in rows]

    async def list_jobs(self, limit: int = 25) -> list[IngestJob]:
        return await asyncio.to_thread(self._list_jobs_sync, limit)

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

    def _add_items_sync(self, job_id: str, items: list[tuple[str, str]]) -> None:
        with closing(self._connect()) as conn, conn:
            conn.executemany(
                "INSERT OR IGNORE INTO job_items (job_id, item_id, source) "
                "VALUES (?, ?, ?)",
                [(job_id, item_id, source) for item_id, source in items],
            )

    def _mark_item_sync(
        self, job_id: str, item_id: str, status: str, chunk_ids: list[str], error: str
    ) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO job_items (job_id, item_id, status, chunk_ids, error) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(job_id, item_id) DO UPDATE SET "
                "  status=excluded.status, chunk_ids=excluded.chunk_ids, error=excluded.error",
                (job_id, item_id, status, json.dumps(chunk_ids), error),
            )

    def _completed_item_ids_sync(self, job_id: str) -> set[str]:
        with closing(self._connect()) as conn, conn:
            cur = conn.execute(
                "SELECT item_id FROM job_items WHERE job_id = ? AND status = ?",
                (job_id, COMPLETED),
            )
            return {row[0] for row in cur.fetchall()}

    def _item_counts_sync(self, job_id: str) -> dict[str, int]:
        with closing(self._connect()) as conn, conn:
            cur = conn.execute(
                "SELECT status, COUNT(*) FROM job_items WHERE job_id = ? GROUP BY status",
                (job_id,),
            )
            return _fold_status_counts(cur.fetchall())

    async def add_items(self, job_id: str, items: list[tuple[str, str]]) -> None:
        await asyncio.to_thread(self._add_items_sync, job_id, items)

    async def mark_item(
        self,
        job_id: str,
        item_id: str,
        status: str,
        chunk_ids: list[str] | None = None,
        error: str = "",
    ) -> None:
        await asyncio.to_thread(
            self._mark_item_sync, job_id, item_id, status, chunk_ids or [], error
        )

    async def completed_item_ids(self, job_id: str) -> set[str]:
        return await asyncio.to_thread(self._completed_item_ids_sync, job_id)

    async def item_counts(self, job_id: str) -> dict[str, int]:
        return await asyncio.to_thread(self._item_counts_sync, job_id)

    async def close(self) -> None:
        """No persistent connection to release — each op opens and closes its own."""


def _normalize_dsn(dsn: str) -> str:
    """Strip SQLAlchemy-style ``+driver`` suffixes asyncpg doesn't understand."""
    for marker in ("+asyncpg", "+psycopg2", "+psycopg"):
        dsn = dsn.replace(marker, "")
    return dsn


class PostgresJobStore:
    """Durable, multi-process job store backed by Postgres via asyncpg.

    The single checkpoint of record for the 500k path: unlike sqlite's single
    writer, Postgres lets multiple workers update item state concurrently. The
    connection pool and schema are created lazily on first use (asyncpg needs an
    event loop), so construction stays synchronous like the other stores.
    """

    def __init__(self, dsn: str, min_size: int = 1, max_size: int = 5) -> None:
        self._dsn = _normalize_dsn(dsn)
        self._min = min_size
        self._max = max_size
        self._pool = None
        self._lock = asyncio.Lock()

    async def _pool_(self):
        if self._pool is None:
            async with self._lock:
                if self._pool is None:
                    try:
                        import asyncpg
                    except ImportError as e:  # pragma: no cover
                        raise RuntimeError(
                            "postgres job store requires asyncpg "
                            "(pip install ragstack[postgres])"
                        ) from e
                    pool = await asyncpg.create_pool(
                        self._dsn, min_size=self._min, max_size=self._max
                    )
                    async with pool.acquire() as conn:
                        await conn.execute(_JOBS_DDL)
                        await conn.execute(_JOB_ITEMS_DDL)
                    self._pool = pool
        return self._pool

    async def create(self, source: str) -> IngestJob:
        job = IngestJob(job_id=str(uuid.uuid4()), status=ACCEPTED, source=source)
        pool = await self._pool_()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO jobs (job_id, status, source, chunk_ids, error) "
                "VALUES ($1, $2, $3, $4, $5)",
                job.job_id, job.status, job.source, json.dumps(job.chunk_ids), job.error,
            )
        return job

    async def get(self, job_id: str) -> IngestJob | None:
        pool = await self._pool_()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT job_id, status, source, chunk_ids, error FROM jobs WHERE job_id = $1",
                job_id,
            )
        if row is None:
            return None
        return IngestJob(
            job_id=row["job_id"],
            status=row["status"],
            source=row["source"],
            chunk_ids=json.loads(row["chunk_ids"]),
            error=row["error"],
        )

    async def list_jobs(self, limit: int = 25) -> list[IngestJob]:
        # The shared jobs schema has no monotonic created_at column, so order by
        # ``ctid DESC`` — most-recently-written tuple first (an UPDATE rewrites the
        # row under MVCC), which surfaces recently-active jobs for the ops view.
        # Best-effort recency, not strict insertion order like sqlite's rowid; add
        # a created_at column if strict creation order is ever needed.
        pool = await self._pool_()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT job_id, status, source, chunk_ids, error FROM jobs"
                " ORDER BY ctid DESC LIMIT $1",
                limit,
            )
        return [
            IngestJob(
                job_id=r["job_id"],
                status=r["status"],
                source=r["source"],
                chunk_ids=json.loads(r["chunk_ids"]),
                error=r["error"],
            )
            for r in rows
        ]

    async def update(self, job_id: str, **fields: object) -> None:
        sets = _prepare_job_update(fields)
        if not sets:
            return
        cols = list(sets)
        assignments = ", ".join(f"{c} = ${i + 1}" for i, c in enumerate(cols))
        pool = await self._pool_()
        async with pool.acquire() as conn:
            await conn.execute(
                f"UPDATE jobs SET {assignments} WHERE job_id = ${len(cols) + 1}",
                *sets.values(), job_id,
            )

    async def fail_interrupted(self) -> int:
        # No-op: this is the multi-process backend, and the sweep is unscoped —
        # it would mark every non-terminal job failed, including ones legitimately
        # running in sibling workers. Reaping here needs a per-owner lease /
        # heartbeat (tracked in issue #7); until then, never touch shared state.
        return 0

    async def add_items(self, job_id: str, items: list[tuple[str, str]]) -> None:
        if not items:
            return
        pool = await self._pool_()
        async with pool.acquire() as conn:
            await conn.executemany(
                "INSERT INTO job_items (job_id, item_id, source) VALUES ($1, $2, $3) "
                "ON CONFLICT (job_id, item_id) DO NOTHING",
                [(job_id, item_id, source) for item_id, source in items],
            )

    async def mark_item(
        self,
        job_id: str,
        item_id: str,
        status: str,
        chunk_ids: list[str] | None = None,
        error: str = "",
    ) -> None:
        pool = await self._pool_()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO job_items (job_id, item_id, status, chunk_ids, error) "
                "VALUES ($1, $2, $3, $4, $5) "
                "ON CONFLICT (job_id, item_id) DO UPDATE SET "
                "  status = excluded.status, chunk_ids = excluded.chunk_ids, error = excluded.error",
                job_id, item_id, status, json.dumps(chunk_ids or []), error,
            )

    async def completed_item_ids(self, job_id: str) -> set[str]:
        pool = await self._pool_()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT item_id FROM job_items WHERE job_id = $1 AND status = $2",
                job_id, COMPLETED,
            )
        return {r["item_id"] for r in rows}

    async def item_counts(self, job_id: str) -> dict[str, int]:
        pool = await self._pool_()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT status, COUNT(*) AS n FROM job_items WHERE job_id = $1 GROUP BY status",
                job_id,
            )
        return _fold_status_counts([(r["status"], r["n"]) for r in rows])

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()


def make_job_store(backend: str, path: str, dsn: str = "") -> JobStore:
    """Build the configured job store (``memory`` | ``sqlite`` | ``postgres``)."""
    if backend == "sqlite":
        return SqliteJobStore(path)
    if backend == "postgres":
        return PostgresJobStore(dsn)
    return InMemoryJobStore()
