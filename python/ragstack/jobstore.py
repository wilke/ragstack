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
import logging
import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from ragstack.collection_store import ensure_columns_postgres, ensure_columns_sqlite

log = logging.getLogger(__name__)

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
    # Stamped at create() from the caller's Principal (#130). Never exposed on
    # IngestResponse — contracts/schemas/ingest_response.json forbids it via
    # additionalProperties: false. "" means unstamped: a row written before this
    # migration, or (in principle) an internal caller that opted out of scoping.
    # An empty string never equals a real tenant, so it fails closed rather than
    # being readable by whichever tenant happens to be named "" — see
    # _apply_tenant_scope.
    tenant_id: str = ""
    # Where the run's archive landed (#203/#353): the ``ws://`` URI of the
    # ``versions/<n>/`` folder GoWe post-staged the workflow's ``archive``
    # Directory output to, in the owner's Workspace. "" for local runs and for
    # rows written before this column existed. Not on IngestResponse (the
    # contract is unchanged); #358 reads it off the job to find the archive.
    archive_ref: str = ""
    # Last write to the job or one of its items (#202): stamped by create(),
    # update() and mark_item() as a sortable ISO-8601 UTC string
    # (``YYYY-MM-DDTHH:MM:SS+00:00``, so string comparison IS time order).
    # ``count_active`` ignores an in-flight job that has not moved for
    # ``stale_after`` — the multi-process store has no sweep (#7), so without
    # this a worker that died mid-run would pin its principal at 429 forever.
    # "" on rows written before the column existed (never counted as active).
    updated_at: str = ""
    # The registry id of the collection the run writes into (#359), stamped at
    # every ingest entry point. Eviction (`ops/evict.py`) refuses a collection
    # with an in-flight (`ACTIVE`, non-stale) job: dropping its stores mid-load
    # would lose the chunks the job has already written and leave the archive
    # step with nothing to pack. "" for rows written before this column existed
    # (never matches a real id, so a legacy row protects nothing). Not on
    # IngestResponse.
    collection_id: str = ""


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
# In-flight states — what ``count_active`` counts (#202: one running ingest job
# per principal). ``unknown`` is never stored, so this is the complement of
# _TERMINAL over the stored statuses.
ACTIVE = (ACCEPTED, RUNNING)
# An in-flight job that has not been written to for this long is treated as
# abandoned by ``count_active`` (see IngestJob.updated_at).
STALE_AFTER = timedelta(hours=6)


def _now() -> str:
    """The ``updated_at`` stamp: sortable ISO-8601 UTC, second precision."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def _cutoff(stale_after: timedelta) -> str:
    return (datetime.now(UTC) - stale_after).isoformat(timespec="seconds")
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
    "  error TEXT NOT NULL DEFAULT '',"
    "  tenant_id TEXT NOT NULL DEFAULT '',"
    "  archive_ref TEXT NOT NULL DEFAULT '',"
    "  updated_at TEXT NOT NULL DEFAULT '',"
    "  collection_id TEXT NOT NULL DEFAULT ''"
    ")"
)
# Column -> DDL fragment, applied via ensure_columns_* (collection_store.py) so
# a `jobs` table created by a pre-#130 build gets the column added in place —
# CREATE TABLE IF NOT EXISTS only helps a brand-new file. Additive-only: never
# a rename, retype, or drop (same convention as _COLLECTIONS_COLUMNS et al).
_JOBS_COLUMNS: dict[str, str] = {
    "tenant_id": "TEXT NOT NULL DEFAULT ''",
    "archive_ref": "TEXT NOT NULL DEFAULT ''",  # #203: the gowe run's archive location
    "updated_at": "TEXT NOT NULL DEFAULT ''",  # #202: last write (staleness for count_active)
    "collection_id": "TEXT NOT NULL DEFAULT ''",  # #359: eviction's in-flight check
}
# count_active's lookup (#202) is (tenant_id, status) — indexed so the per-upload
# admission check stays a point lookup as the jobs table grows. Both dialects
# accept this form; applied after the column migration so it can name tenant_id
# on a pre-#130 table too.
_JOBS_ACTIVE_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS jobs_tenant_status ON jobs (tenant_id, status)"
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
_JOB_UPDATE_COLUMNS = (
    "status", "source", "chunk_ids", "error", "archive_ref", "updated_at", "collection_id",
)


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
    if sets:
        # Every write bumps the stamp unless the caller sets it explicitly
        # (tests back-date jobs that way).
        sets.setdefault("updated_at", _now())
    return sets


def _fold_status_counts(rows: list[tuple[str, int]]) -> dict[str, int]:
    """Fold ``(status, count)`` rows onto the zero-seeded counts dict — the
    shared tail of every SQL ``item_counts`` (GROUP BY status)."""
    counts = _zero_item_counts()
    for status, n in rows:
        counts[status] = n
    return counts


def _apply_tenant_scope(
    job: IngestJob | None, tenant_id: str | None, is_admin: bool
) -> IngestJob | None:
    """The tenant-scoping decision for ``JobStore.get()``, applied by every
    backend after its own raw single-row fetch (#130).

    ``tenant_id=None`` means an unscoped internal caller (e.g. the deep health
    check's existence probe) — no filtering, exactly today's pre-#130 behaviour.
    A scoped caller only sees a job whose stamped ``tenant_id`` matches theirs;
    a legacy row (``tenant_id == ""``, written before this migration) matches no
    real tenant string, so it fails closed by ordinary equality — the #209
    convention. A scoped caller whose OWN ``tenant_id`` is ``""`` is refused
    explicitly (never falls through to the equality check) rather than
    relying on that equality accidentally doing the right thing — an empty
    caller tenant is not reachable through the API today, but this function
    is the boundary, so it states the invariant rather than assumes it.
    ``is_admin`` is the one escape hatch, and it is a named, logged branch
    (ADR-0003 §5), mirroring ``authz.resolve_access``'s admin-bypass: logged
    on every use, not just when it changes the outcome, so the audit trail
    counts and time-orders admin access to jobs regardless of owner.
    """
    if job is None or tenant_id is None:
        return job
    if is_admin:
        log.info("jobstore admin-bypass: tenant=%s job_id=%s", tenant_id, job.job_id)
        return job
    if not tenant_id:
        # A scoped caller whose OWN tenant is "" must not match a legacy ""
        # row either — that would make the fail-closed convention above
        # depend on no real caller ever being stamped "". Not reachable
        # today (DEFAULT_TENANT is "default", blank api_key_tenants values
        # are rejected at config load, and bearer subjects are always
        # "issuer:sub"), but this helper IS the boundary, so state it rather
        # than rely on every future caller upholding the invariant.
        return None
    return job if job.tenant_id == tenant_id else None


@runtime_checkable
class JobStore(Protocol):
    """Persist and update ingestion job state."""

    async def create(
        self, source: str, tenant_id: str = "", collection_id: str = ""
    ) -> IngestJob:
        """Mint an ``accepted`` job. ``collection_id`` is the registry id the
        run targets (#359) — stamp it at every entry point, or the eviction
        policy cannot see the job."""
        ...

    async def get(
        self, job_id: str, tenant_id: str | None = None, *, is_admin: bool = False
    ) -> IngestJob | None:
        """Fetch a job, tenant-scoped (#130). ``tenant_id=None`` is unscoped —
        for internal callers only (e.g. the deep health check); every caller
        reachable from the API must pass its principal's tenant. A mismatched
        or unstamped (``tenant_id == ""``, pre-#130) job is reported exactly
        like a missing one — ``None`` — unless ``is_admin``, a named, logged
        bypass (ADR-0003 §5)."""
        ...

    async def list_jobs(self, limit: int = 25) -> list[IngestJob]:
        """Most-recent-first job list, capped at ``limit``. Powers the admin Ops
        jobs panel. Jobs are tenant-stamped as of #130, but this listing is not
        yet scoped by it — still admin-only (an admin may see all runs); #100
        tracks adding a tenant-scoped listing."""
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

    async def count_active(
        self, tenant_id: str, *, stale_after: timedelta = STALE_AFTER
    ) -> int:
        """Number of this tenant's jobs that are still in flight (status in
        :data:`ACTIVE`: ``accepted`` or ``running``) AND were written to within
        ``stale_after`` (``updated_at``). The admission check behind
        ``api/deps.py::single_inflight_ingest`` (#202). Exact equality on
        ``tenant_id`` — an unstamped legacy row (``""``) never counts for a
        real tenant; a row with no ``updated_at`` (pre-column) never counts as
        active either."""
        ...

    async def active_collection_ids(self, *, stale_after: timedelta = STALE_AFTER) -> set[str]:
        """Registry ids with an in-flight job (status in :data:`ACTIVE`,
        written to within ``stale_after`` — the same staleness rule as
        :meth:`count_active`, so a job orphaned by a dead process (#7) stops
        shielding its collection from eviction once it goes stale). ONE
        query, however many collections; what eviction (#359) consults
        before choosing victims. Unstamped legacy rows (``""``) are never
        included."""
        ...

    async def active_for_collection(
        self, collection_id: str, *, stale_after: timedelta = STALE_AFTER
    ) -> int:
        """How many in-flight, non-stale jobs target ``collection_id`` (0 =
        safe to evict on this axis)."""
        ...

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

    async def create(
        self, source: str, tenant_id: str = "", collection_id: str = ""
    ) -> IngestJob:
        job = IngestJob(
            job_id=str(uuid.uuid4()), status=ACCEPTED, source=source, tenant_id=tenant_id,
            updated_at=_now(), collection_id=collection_id,
        )
        async with self._lock:
            self._jobs[job.job_id] = job
        return job.model_copy()

    async def get(
        self, job_id: str, tenant_id: str | None = None, *, is_admin: bool = False
    ) -> IngestJob | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            job = job.model_copy() if job is not None else None
        return _apply_tenant_scope(job, tenant_id, is_admin)

    async def list_jobs(self, limit: int = 25) -> list[IngestJob]:
        async with self._lock:
            # Dict preserves insertion order; newest-first, capped.
            jobs = list(self._jobs.values())[-limit:]
            return [j.model_copy() for j in reversed(jobs)]

    async def update(self, job_id: str, **fields: object) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                self._jobs[job_id] = job.model_copy(update={"updated_at": _now(), **fields})

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
            job = self._jobs.get(job_id)
            if job is not None:  # item progress keeps the job fresh
                self._jobs[job_id] = job.model_copy(update={"updated_at": _now()})

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

    async def count_active(
        self, tenant_id: str, *, stale_after: timedelta = STALE_AFTER
    ) -> int:
        # A plain scan: the dev/test store holds at most a few thousand jobs,
        # and a scan needs no index kept in step with create/update/fail_interrupted.
        cutoff = _cutoff(stale_after)
        async with self._lock:
            return sum(
                1
                for j in self._jobs.values()
                if j.tenant_id == tenant_id and j.status in ACTIVE and j.updated_at > cutoff
            )

    async def active_collection_ids(self, *, stale_after: timedelta = STALE_AFTER) -> set[str]:
        cutoff = _cutoff(stale_after)
        async with self._lock:
            return {
                j.collection_id for j in self._jobs.values()
                if j.collection_id and j.status in ACTIVE and j.updated_at > cutoff
            }

    async def active_for_collection(
        self, collection_id: str, *, stale_after: timedelta = STALE_AFTER
    ) -> int:
        if not collection_id:
            return 0
        cutoff = _cutoff(stale_after)
        async with self._lock:
            return sum(
                1 for j in self._jobs.values()
                if j.collection_id == collection_id and j.status in ACTIVE
                and j.updated_at > cutoff
            )

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
            # Additive migration for a `jobs` table created by a pre-#130 build —
            # CREATE TABLE IF NOT EXISTS above is a no-op against an existing file.
            ensure_columns_sqlite(conn, "jobs", _JOBS_COLUMNS)
            conn.execute(_JOBS_ACTIVE_INDEX_DDL)

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
        (job_id, status, source, chunk_ids, error, tenant_id, archive_ref, updated_at,
         collection_id) = row
        return IngestJob(
            job_id=job_id,
            status=status,
            source=source,
            chunk_ids=json.loads(chunk_ids),
            error=error,
            tenant_id=tenant_id,
            archive_ref=archive_ref,
            updated_at=updated_at,
            collection_id=collection_id,
        )

    def _create_sync(self, source: str, tenant_id: str, collection_id: str) -> IngestJob:
        job = IngestJob(
            job_id=str(uuid.uuid4()), status=ACCEPTED, source=source, tenant_id=tenant_id,
            updated_at=_now(), collection_id=collection_id,
        )
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO jobs (job_id, status, source, chunk_ids, error, tenant_id,"
                " updated_at, collection_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job.job_id, job.status, job.source, json.dumps(job.chunk_ids),
                    job.error, job.tenant_id, job.updated_at, job.collection_id,
                ),
            )
        return job

    def _get_sync(self, job_id: str) -> IngestJob | None:
        with closing(self._connect()) as conn, conn:
            cur = conn.execute(
                "SELECT job_id, status, source, chunk_ids, error, tenant_id, archive_ref,"
                " updated_at, collection_id"
                " FROM jobs WHERE job_id = ?",
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

    async def create(
        self, source: str, tenant_id: str = "", collection_id: str = ""
    ) -> IngestJob:
        return await asyncio.to_thread(self._create_sync, source, tenant_id, collection_id)

    async def get(
        self, job_id: str, tenant_id: str | None = None, *, is_admin: bool = False
    ) -> IngestJob | None:
        job = await asyncio.to_thread(self._get_sync, job_id)
        return _apply_tenant_scope(job, tenant_id, is_admin)

    def _list_jobs_sync(self, limit: int) -> list[IngestJob]:
        # Implicit rowid ascends with insertion; DESC gives newest-first.
        with closing(self._connect()) as conn, conn:
            cur = conn.execute(
                "SELECT job_id, status, source, chunk_ids, error, tenant_id, archive_ref,"
                " updated_at, collection_id"
                " FROM jobs ORDER BY rowid DESC LIMIT ?",
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
            # Item progress keeps the job fresh for count_active's staleness.
            conn.execute("UPDATE jobs SET updated_at = ? WHERE job_id = ?", (_now(), job_id))

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

    def _count_active_sync(self, tenant_id: str, cutoff: str) -> int:
        with closing(self._connect()) as conn, conn:
            cur = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE tenant_id = ? AND status IN (?, ?)"
                " AND updated_at > ?",
                (tenant_id, *ACTIVE, cutoff),
            )
            return int(cur.fetchone()[0])

    def _active_collection_ids_sync(self, cutoff: str) -> set[str]:
        with closing(self._connect()) as conn, conn:
            cur = conn.execute(
                "SELECT DISTINCT collection_id FROM jobs"
                " WHERE collection_id != '' AND status IN (?, ?) AND updated_at > ?",
                (*ACTIVE, cutoff),
            )
            return {row[0] for row in cur.fetchall()}

    def _active_for_collection_sync(self, collection_id: str, cutoff: str) -> int:
        with closing(self._connect()) as conn, conn:
            cur = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE collection_id = ? AND status IN (?, ?)"
                " AND updated_at > ?",
                (collection_id, *ACTIVE, cutoff),
            )
            return int(cur.fetchone()[0])

    async def active_collection_ids(self, *, stale_after: timedelta = STALE_AFTER) -> set[str]:
        return await asyncio.to_thread(self._active_collection_ids_sync, _cutoff(stale_after))

    async def active_for_collection(
        self, collection_id: str, *, stale_after: timedelta = STALE_AFTER
    ) -> int:
        if not collection_id:
            return 0
        return await asyncio.to_thread(
            self._active_for_collection_sync, collection_id, _cutoff(stale_after)
        )

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

    async def count_active(
        self, tenant_id: str, *, stale_after: timedelta = STALE_AFTER
    ) -> int:
        return await asyncio.to_thread(self._count_active_sync, tenant_id, _cutoff(stale_after))

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
                        # Additive migration for a `jobs` table from a pre-#130
                        # build — CREATE TABLE IF NOT EXISTS above is a no-op
                        # against an existing table.
                        await ensure_columns_postgres(conn, "jobs", _JOBS_COLUMNS)
                        await conn.execute(_JOBS_ACTIVE_INDEX_DDL)
                    self._pool = pool
        return self._pool

    async def create(
        self, source: str, tenant_id: str = "", collection_id: str = ""
    ) -> IngestJob:
        job = IngestJob(
            job_id=str(uuid.uuid4()), status=ACCEPTED, source=source, tenant_id=tenant_id,
            updated_at=_now(), collection_id=collection_id,
        )
        pool = await self._pool_()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO jobs (job_id, status, source, chunk_ids, error, tenant_id,"
                " updated_at, collection_id) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                job.job_id, job.status, job.source, json.dumps(job.chunk_ids), job.error,
                job.tenant_id, job.updated_at, job.collection_id,
            )
        return job

    async def get(
        self, job_id: str, tenant_id: str | None = None, *, is_admin: bool = False
    ) -> IngestJob | None:
        pool = await self._pool_()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT job_id, status, source, chunk_ids, error, tenant_id, archive_ref,"
                " updated_at, collection_id"
                " FROM jobs WHERE job_id = $1",
                job_id,
            )
        job = (
            None
            if row is None
            else IngestJob(
                job_id=row["job_id"],
                status=row["status"],
                source=row["source"],
                chunk_ids=json.loads(row["chunk_ids"]),
                error=row["error"],
                tenant_id=row["tenant_id"],
                archive_ref=row["archive_ref"],
                updated_at=row["updated_at"],
                collection_id=row["collection_id"],
            )
        )
        return _apply_tenant_scope(job, tenant_id, is_admin)

    async def list_jobs(self, limit: int = 25) -> list[IngestJob]:
        # The shared jobs schema has no monotonic created_at column, so order by
        # ``ctid DESC`` — most-recently-written tuple first (an UPDATE rewrites the
        # row under MVCC), which surfaces recently-active jobs for the ops view.
        # Best-effort recency, not strict insertion order like sqlite's rowid; add
        # a created_at column if strict creation order is ever needed.
        pool = await self._pool_()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT job_id, status, source, chunk_ids, error, tenant_id, archive_ref,"
                " updated_at, collection_id"
                " FROM jobs ORDER BY ctid DESC LIMIT $1",
                limit,
            )
        return [
            IngestJob(
                job_id=r["job_id"],
                status=r["status"],
                source=r["source"],
                chunk_ids=json.loads(r["chunk_ids"]),
                error=r["error"],
                tenant_id=r["tenant_id"],
                archive_ref=r["archive_ref"],
                updated_at=r["updated_at"],
                collection_id=r["collection_id"],
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
            # Item progress keeps the job fresh for count_active's staleness.
            await conn.execute(
                "UPDATE jobs SET updated_at = $1 WHERE job_id = $2", _now(), job_id
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

    async def count_active(
        self, tenant_id: str, *, stale_after: timedelta = STALE_AFTER
    ) -> int:
        pool = await self._pool_()
        async with pool.acquire() as conn:
            n = await conn.fetchval(
                "SELECT COUNT(*) FROM jobs WHERE tenant_id = $1 AND status IN ($2, $3)"
                " AND updated_at > $4",
                tenant_id, *ACTIVE, _cutoff(stale_after),
            )
        return int(n or 0)

    async def active_collection_ids(self, *, stale_after: timedelta = STALE_AFTER) -> set[str]:
        pool = await self._pool_()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT collection_id FROM jobs"
                " WHERE collection_id <> '' AND status IN ($1, $2) AND updated_at > $3",
                *ACTIVE, _cutoff(stale_after),
            )
        return {r["collection_id"] for r in rows}

    async def active_for_collection(
        self, collection_id: str, *, stale_after: timedelta = STALE_AFTER
    ) -> int:
        if not collection_id:
            return 0
        pool = await self._pool_()
        async with pool.acquire() as conn:
            n = await conn.fetchval(
                "SELECT COUNT(*) FROM jobs WHERE collection_id = $1 AND status IN ($2, $3)"
                " AND updated_at > $4",
                collection_id, *ACTIVE, _cutoff(stale_after),
            )
        return int(n or 0)

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
