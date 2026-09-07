"""Persistence for the grading resources — ``memory`` | ``sqlite`` | ``postgres``.

The same backend switch the job store uses (:mod:`ragstack.jobstore`), for the
same reason: ``memory`` is the keyed conformance boot and local dev, ``sqlite``
is durable single-process, ``postgres`` (via ``postgres_dsn``) is what a tenant
runs. Selected by ``grading_store_backend``; see :func:`make_grading_store`.

**Four collections**, mirroring grading-ui.md §3.3:

===================  ==========================================================
``grading_batches``  one row per read
``grading_tasks``    one row per task, ``position`` = batch order
``grading_verdicts`` APPEND-ONLY by ``(task_id, reader, version)``
``grading_adjs``     APPEND-ONLY by ``(task_id, version)``
===================  ==========================================================

The current row of a reader (or of an adjudication) is the one with the highest
``version``; earlier versions are kept and are not surfaced in v1. That is what
makes an overwrite non-destructive: the pre-adjudication read the study reports
κ from survives a reader changing their mind.

The SQL backends store each record as a JSON document in a ``data`` column with
the identifying fields promoted to real columns. There is no migration tooling
in this repo (``docs/libraries-spec.md`` §8.1), and a task carries a whole
segmented document — a column per field would be a schema change per contract
change, for a resource whose shape the contract already pins.

Nothing here decides who may see what: every method answers about rows, and
:mod:`ragstack.api.routers.grading` is the one place that knows who is asking.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from contextlib import closing
from typing import Any, Protocol, runtime_checkable

from ragstack.grading.models import (
    GradingAdjudicationRecord,
    GradingBatchRecord,
    GradingTaskRecord,
    GradingVerdictRecord,
)

log = logging.getLogger(__name__)

# Backend vocabulary (mirror in config.grading_store_backend's comment).
MEMORY = "memory"
SQLITE = "sqlite"
POSTGRES = "postgres"


class GradingStoreUnavailable(RuntimeError):
    """The grading store could not answer. Mapped to a 503 by the router —
    fail closed, never a 200 with a partial read (the #196 lesson)."""


@runtime_checkable
class GradingStore(Protocol):
    """Persist batches, tasks, verdicts and adjudications.

    Every method may raise :class:`GradingStoreUnavailable`; the router maps it
    to 503. ``None`` / an empty list means *no such row*, never *I could not
    look* — the two are distinguishable precisely because the failure raises.
    """

    async def create_batch(
        self, batch: GradingBatchRecord, tasks: list[GradingTaskRecord]
    ) -> None:
        """Store a batch and all of its tasks. Whole or not at all: the batch
        row is written last (sqlite/postgres do it in one transaction), so a
        crash mid-write never leaves a listable batch with missing tasks."""
        ...

    async def get_batch(self, batch_id: str) -> GradingBatchRecord | None: ...

    async def list_batches(self) -> list[GradingBatchRecord]:
        """Every batch, newest first. Unpaginated — a deployment runs a handful
        of reads; the router filters to what the caller may see."""
        ...

    async def delete_batch(self, batch_id: str) -> bool:
        """Hard-delete a batch, its tasks, every verdict version and every
        adjudication. True when a batch was removed."""
        ...

    async def begin_adjudication(self, batch_id: str, at: str) -> bool:
        """Move ``open`` → ``adjudicating``, stamping ``adjudicating_at``.

        CONDITIONAL on the batch still being ``open`` and False otherwise, so
        two concurrent clicks cannot both win: the loser gets the contract's
        409 instead of silently re-stamping a frozen read."""
        ...

    async def get_task(self, task_id: str) -> GradingTaskRecord | None: ...

    async def list_tasks(self, batch_id: str) -> list[GradingTaskRecord]:
        """The batch's tasks in BATCH order (``position``)."""
        ...

    async def put_verdict(self, verdict: GradingVerdictRecord) -> GradingVerdictRecord:
        """Append ``verdict`` as the next version for its (task, reader) and
        return it with ``version`` filled in. The caller's ``version`` is
        ignored — the store owns the sequence, so two saves racing produce two
        versions rather than one lost write."""
        ...

    async def get_verdict(self, task_id: str, reader: str) -> GradingVerdictRecord | None:
        """The CURRENT row for (task, reader) — the highest version — or None."""
        ...

    async def list_verdicts(self, batch_id: str) -> list[GradingVerdictRecord]:
        """Every current verdict row in the batch, one per (task, reader)."""
        ...

    async def put_adjudication(
        self, adjudication: GradingAdjudicationRecord
    ) -> GradingAdjudicationRecord:
        """Append the next version of a task's joint-read row; ``version`` is
        assigned by the store, as for a verdict."""
        ...

    async def get_adjudication(self, task_id: str) -> GradingAdjudicationRecord | None: ...

    async def list_adjudications(self, batch_id: str) -> list[GradingAdjudicationRecord]:
        """Every task's current joint-read row in the batch."""
        ...

    async def close(self) -> None:
        """Release any held resources. No-op for the in-memory and
        connection-per-op stores."""
        ...


def _current_verdicts(rows: list[GradingVerdictRecord]) -> list[GradingVerdictRecord]:
    """Collapse an append-only history to one current row per (task, reader)."""
    latest: dict[tuple[str, str], GradingVerdictRecord] = {}
    for r in rows:
        key = (r.task_id, r.reader)
        cur = latest.get(key)
        if cur is None or r.version > cur.version:
            latest[key] = r
    return list(latest.values())


def _current_adjudications(
    rows: list[GradingAdjudicationRecord],
) -> list[GradingAdjudicationRecord]:
    latest: dict[str, GradingAdjudicationRecord] = {}
    for r in rows:
        cur = latest.get(r.task_id)
        if cur is None or r.version > cur.version:
            latest[r.task_id] = r
    return list(latest.values())


# --------------------------------------------------------------------------- #
# memory
# --------------------------------------------------------------------------- #
class InMemoryGradingStore:
    """Process-local store. Loses everything on restart — dev, tests, and the
    keyed conformance boot, which creates a batch and deletes it again."""

    def __init__(self) -> None:
        self._batches: dict[str, GradingBatchRecord] = {}
        self._tasks: dict[str, GradingTaskRecord] = {}
        self._verdicts: list[GradingVerdictRecord] = []
        self._adjudications: list[GradingAdjudicationRecord] = []
        self._lock = asyncio.Lock()

    async def create_batch(
        self, batch: GradingBatchRecord, tasks: list[GradingTaskRecord]
    ) -> None:
        async with self._lock:
            for t in tasks:
                self._tasks[t.id] = t
            self._batches[batch.id] = batch

    async def get_batch(self, batch_id: str) -> GradingBatchRecord | None:
        return self._batches.get(batch_id)

    async def list_batches(self) -> list[GradingBatchRecord]:
        return sorted(self._batches.values(), key=lambda b: (b.created_at, b.id), reverse=True)

    async def delete_batch(self, batch_id: str) -> bool:
        async with self._lock:
            if batch_id not in self._batches:
                return False
            del self._batches[batch_id]
            self._tasks = {k: v for k, v in self._tasks.items() if v.batch_id != batch_id}
            self._verdicts = [v for v in self._verdicts if v.batch_id != batch_id]
            self._adjudications = [a for a in self._adjudications if a.batch_id != batch_id]
            return True

    async def begin_adjudication(self, batch_id: str, at: str) -> bool:
        async with self._lock:
            batch = self._batches.get(batch_id)
            if batch is None or batch.status != "open":
                return False
            self._batches[batch_id] = batch.model_copy(
                update={"status": "adjudicating", "adjudicating_at": at}
            )
            return True

    async def get_task(self, task_id: str) -> GradingTaskRecord | None:
        return self._tasks.get(task_id)

    async def list_tasks(self, batch_id: str) -> list[GradingTaskRecord]:
        return sorted(
            (t for t in self._tasks.values() if t.batch_id == batch_id),
            key=lambda t: t.position,
        )

    async def put_verdict(self, verdict: GradingVerdictRecord) -> GradingVerdictRecord:
        async with self._lock:
            prior = [
                v.version
                for v in self._verdicts
                if v.task_id == verdict.task_id and v.reader == verdict.reader
            ]
            row = verdict.model_copy(update={"version": (max(prior) if prior else 0) + 1})
            self._verdicts.append(row)
            return row

    async def get_verdict(self, task_id: str, reader: str) -> GradingVerdictRecord | None:
        rows = [v for v in self._verdicts if v.task_id == task_id and v.reader == reader]
        return max(rows, key=lambda v: v.version) if rows else None

    async def list_verdicts(self, batch_id: str) -> list[GradingVerdictRecord]:
        return _current_verdicts([v for v in self._verdicts if v.batch_id == batch_id])

    async def put_adjudication(
        self, adjudication: GradingAdjudicationRecord
    ) -> GradingAdjudicationRecord:
        async with self._lock:
            prior = [a.version for a in self._adjudications if a.task_id == adjudication.task_id]
            row = adjudication.model_copy(update={"version": (max(prior) if prior else 0) + 1})
            self._adjudications.append(row)
            return row

    async def get_adjudication(self, task_id: str) -> GradingAdjudicationRecord | None:
        rows = [a for a in self._adjudications if a.task_id == task_id]
        return max(rows, key=lambda a: a.version) if rows else None

    async def list_adjudications(self, batch_id: str) -> list[GradingAdjudicationRecord]:
        return _current_adjudications(
            [a for a in self._adjudications if a.batch_id == batch_id]
        )

    async def close(self) -> None:
        return None


# --------------------------------------------------------------------------- #
# sqlite / postgres — shared DDL
# --------------------------------------------------------------------------- #
_BATCHES_DDL = """
CREATE TABLE IF NOT EXISTS grading_batches (
    batch_id   TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    data       TEXT NOT NULL
)
"""
_TASKS_DDL = """
CREATE TABLE IF NOT EXISTS grading_tasks (
    task_id  TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    data     TEXT NOT NULL
)
"""
_TASKS_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS grading_tasks_batch "
    "ON grading_tasks (batch_id, position)"
)
_VERDICTS_DDL = """
CREATE TABLE IF NOT EXISTS grading_verdicts (
    task_id  TEXT NOT NULL,
    reader   TEXT NOT NULL,
    version  INTEGER NOT NULL,
    batch_id TEXT NOT NULL,
    data     TEXT NOT NULL,
    PRIMARY KEY (task_id, reader, version)
)
"""
_VERDICTS_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS grading_verdicts_batch ON grading_verdicts (batch_id)"
)
_ADJS_DDL = """
CREATE TABLE IF NOT EXISTS grading_adjs (
    task_id  TEXT NOT NULL,
    version  INTEGER NOT NULL,
    batch_id TEXT NOT NULL,
    data     TEXT NOT NULL,
    PRIMARY KEY (task_id, version)
)
"""
_ADJS_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS grading_adjs_batch ON grading_adjs (batch_id)"
)

_SCHEMA = (
    _BATCHES_DDL,
    _TASKS_DDL,
    _TASKS_INDEX_DDL,
    _VERDICTS_DDL,
    _VERDICTS_INDEX_DDL,
    _ADJS_DDL,
    _ADJS_INDEX_DDL,
)


class SqliteGradingStore:
    """Durable store backed by stdlib sqlite3 — no new dependency.

    A connection is opened per operation and the call runs in a worker thread
    (``asyncio.to_thread``) so blocking sqlite never stalls the event loop; WAL
    mode lets reads coexist with the single writer. The idiom throughout is
    ``with closing(self._connect()) as conn, conn:`` — sqlite3's connection
    context manager commits but does NOT close (see :mod:`ragstack.jobstore`).
    """

    def __init__(self, path: str) -> None:
        self._path = path
        with closing(self._connect()) as conn, conn:
            for stmt in _SCHEMA:
                conn.execute(stmt)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # --- create / read batches ------------------------------------------- #
    def _create_batch_sync(
        self, batch: GradingBatchRecord, tasks: list[GradingTaskRecord]
    ) -> None:
        with closing(self._connect()) as conn, conn:
            conn.executemany(
                "INSERT INTO grading_tasks (task_id, batch_id, position, data)"
                " VALUES (?, ?, ?, ?)",
                [(t.id, t.batch_id, t.position, t.model_dump_json()) for t in tasks],
            )
            conn.execute(
                "INSERT INTO grading_batches (batch_id, created_at, data) VALUES (?, ?, ?)",
                (batch.id, batch.created_at, batch.model_dump_json()),
            )

    async def create_batch(
        self, batch: GradingBatchRecord, tasks: list[GradingTaskRecord]
    ) -> None:
        await asyncio.to_thread(self._create_batch_sync, batch, tasks)

    def _get_batch_sync(self, batch_id: str) -> GradingBatchRecord | None:
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                "SELECT data FROM grading_batches WHERE batch_id = ?", (batch_id,)
            ).fetchone()
        return GradingBatchRecord.model_validate_json(row[0]) if row else None

    async def get_batch(self, batch_id: str) -> GradingBatchRecord | None:
        return await asyncio.to_thread(self._get_batch_sync, batch_id)

    def _list_batches_sync(self) -> list[GradingBatchRecord]:
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(
                "SELECT data FROM grading_batches ORDER BY created_at DESC, batch_id DESC"
            ).fetchall()
        return [GradingBatchRecord.model_validate_json(r[0]) for r in rows]

    async def list_batches(self) -> list[GradingBatchRecord]:
        return await asyncio.to_thread(self._list_batches_sync)

    def _delete_batch_sync(self, batch_id: str) -> bool:
        with closing(self._connect()) as conn, conn:
            cur = conn.execute("DELETE FROM grading_batches WHERE batch_id = ?", (batch_id,))
            removed = cur.rowcount > 0
            conn.execute("DELETE FROM grading_tasks WHERE batch_id = ?", (batch_id,))
            conn.execute("DELETE FROM grading_verdicts WHERE batch_id = ?", (batch_id,))
            conn.execute("DELETE FROM grading_adjs WHERE batch_id = ?", (batch_id,))
        return removed

    async def delete_batch(self, batch_id: str) -> bool:
        return await asyncio.to_thread(self._delete_batch_sync, batch_id)

    def _begin_adjudication_sync(self, batch_id: str, at: str) -> bool:
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                "SELECT data FROM grading_batches WHERE batch_id = ?", (batch_id,)
            ).fetchone()
            if row is None:
                return False
            batch = GradingBatchRecord.model_validate_json(row[0])
            if batch.status != "open":
                return False
            updated = batch.model_copy(
                update={"status": "adjudicating", "adjudicating_at": at}
            )
            # The WHERE re-checks the status inside the same write transaction,
            # so a second click that read the same `open` row still loses.
            cur = conn.execute(
                "UPDATE grading_batches SET data = ? WHERE batch_id = ?"
                " AND json_extract(data, '$.status') = 'open'",
                (updated.model_dump_json(), batch_id),
            )
            return cur.rowcount > 0

    async def begin_adjudication(self, batch_id: str, at: str) -> bool:
        return await asyncio.to_thread(self._begin_adjudication_sync, batch_id, at)

    # --- tasks ------------------------------------------------------------ #
    def _get_task_sync(self, task_id: str) -> GradingTaskRecord | None:
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                "SELECT data FROM grading_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        return GradingTaskRecord.model_validate_json(row[0]) if row else None

    async def get_task(self, task_id: str) -> GradingTaskRecord | None:
        return await asyncio.to_thread(self._get_task_sync, task_id)

    def _list_tasks_sync(self, batch_id: str) -> list[GradingTaskRecord]:
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(
                "SELECT data FROM grading_tasks WHERE batch_id = ? ORDER BY position",
                (batch_id,),
            ).fetchall()
        return [GradingTaskRecord.model_validate_json(r[0]) for r in rows]

    async def list_tasks(self, batch_id: str) -> list[GradingTaskRecord]:
        return await asyncio.to_thread(self._list_tasks_sync, batch_id)

    # --- verdicts --------------------------------------------------------- #
    def _put_verdict_sync(self, verdict: GradingVerdictRecord) -> GradingVerdictRecord:
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                "SELECT MAX(version) FROM grading_verdicts WHERE task_id = ? AND reader = ?",
                (verdict.task_id, verdict.reader),
            ).fetchone()
            out = verdict.model_copy(update={"version": (row[0] or 0) + 1})
            conn.execute(
                "INSERT INTO grading_verdicts (task_id, reader, version, batch_id, data)"
                " VALUES (?, ?, ?, ?, ?)",
                (out.task_id, out.reader, out.version, out.batch_id, out.model_dump_json()),
            )
        return out

    async def put_verdict(self, verdict: GradingVerdictRecord) -> GradingVerdictRecord:
        return await asyncio.to_thread(self._put_verdict_sync, verdict)

    def _get_verdict_sync(self, task_id: str, reader: str) -> GradingVerdictRecord | None:
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                "SELECT data FROM grading_verdicts WHERE task_id = ? AND reader = ?"
                " ORDER BY version DESC LIMIT 1",
                (task_id, reader),
            ).fetchone()
        return GradingVerdictRecord.model_validate_json(row[0]) if row else None

    async def get_verdict(self, task_id: str, reader: str) -> GradingVerdictRecord | None:
        return await asyncio.to_thread(self._get_verdict_sync, task_id, reader)

    def _list_verdicts_sync(self, batch_id: str) -> list[GradingVerdictRecord]:
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(
                "SELECT data FROM grading_verdicts WHERE batch_id = ?", (batch_id,)
            ).fetchall()
        return _current_verdicts([GradingVerdictRecord.model_validate_json(r[0]) for r in rows])

    async def list_verdicts(self, batch_id: str) -> list[GradingVerdictRecord]:
        return await asyncio.to_thread(self._list_verdicts_sync, batch_id)

    # --- adjudications ---------------------------------------------------- #
    def _put_adjudication_sync(
        self, adjudication: GradingAdjudicationRecord
    ) -> GradingAdjudicationRecord:
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                "SELECT MAX(version) FROM grading_adjs WHERE task_id = ?",
                (adjudication.task_id,),
            ).fetchone()
            out = adjudication.model_copy(update={"version": (row[0] or 0) + 1})
            conn.execute(
                "INSERT INTO grading_adjs (task_id, version, batch_id, data)"
                " VALUES (?, ?, ?, ?)",
                (out.task_id, out.version, out.batch_id, out.model_dump_json()),
            )
        return out

    async def put_adjudication(
        self, adjudication: GradingAdjudicationRecord
    ) -> GradingAdjudicationRecord:
        return await asyncio.to_thread(self._put_adjudication_sync, adjudication)

    def _get_adjudication_sync(self, task_id: str) -> GradingAdjudicationRecord | None:
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                "SELECT data FROM grading_adjs WHERE task_id = ? ORDER BY version DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        return GradingAdjudicationRecord.model_validate_json(row[0]) if row else None

    async def get_adjudication(self, task_id: str) -> GradingAdjudicationRecord | None:
        return await asyncio.to_thread(self._get_adjudication_sync, task_id)

    def _list_adjudications_sync(self, batch_id: str) -> list[GradingAdjudicationRecord]:
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(
                "SELECT data FROM grading_adjs WHERE batch_id = ?", (batch_id,)
            ).fetchall()
        return _current_adjudications(
            [GradingAdjudicationRecord.model_validate_json(r[0]) for r in rows]
        )

    async def list_adjudications(self, batch_id: str) -> list[GradingAdjudicationRecord]:
        return await asyncio.to_thread(self._list_adjudications_sync, batch_id)

    async def close(self) -> None:
        return None


def _normalize_dsn(dsn: str) -> str:
    """Strip SQLAlchemy-style ``+driver`` suffixes asyncpg doesn't understand
    (the same helper :mod:`ragstack.jobstore` needs, for the same DSN)."""
    for marker in ("+asyncpg", "+psycopg2", "+psycopg"):
        dsn = dsn.replace(marker, "")
    return dsn


class PostgresGradingStore:
    """Durable, multi-process store backed by Postgres via asyncpg.

    The pool and schema are created lazily on first use (asyncpg needs an event
    loop), so construction stays synchronous like the other stores.
    """

    def __init__(self, dsn: str, min_size: int = 1, max_size: int = 5) -> None:
        self._dsn = _normalize_dsn(dsn)
        self._min = min_size
        self._max = max_size
        self._pool: Any = None
        self._lock = asyncio.Lock()

    async def _pool_(self) -> Any:
        if self._pool is None:
            async with self._lock:
                if self._pool is None:
                    try:
                        import asyncpg
                    except ImportError as e:  # pragma: no cover
                        raise RuntimeError(
                            "postgres grading store requires asyncpg "
                            "(pip install ragstack[postgres])"
                        ) from e
                    pool = await asyncpg.create_pool(
                        self._dsn, min_size=self._min, max_size=self._max
                    )
                    async with pool.acquire() as conn:
                        for stmt in _SCHEMA:
                            await conn.execute(stmt)
                    self._pool = pool
        return self._pool

    async def create_batch(
        self, batch: GradingBatchRecord, tasks: list[GradingTaskRecord]
    ) -> None:
        pool = await self._pool_()
        async with pool.acquire() as conn, conn.transaction():
            await conn.executemany(
                "INSERT INTO grading_tasks (task_id, batch_id, position, data)"
                " VALUES ($1, $2, $3, $4)",
                [(t.id, t.batch_id, t.position, t.model_dump_json()) for t in tasks],
            )
            await conn.execute(
                "INSERT INTO grading_batches (batch_id, created_at, data)"
                " VALUES ($1, $2, $3)",
                batch.id, batch.created_at, batch.model_dump_json(),
            )

    async def get_batch(self, batch_id: str) -> GradingBatchRecord | None:
        pool = await self._pool_()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT data FROM grading_batches WHERE batch_id = $1", batch_id
            )
        return GradingBatchRecord.model_validate_json(row["data"]) if row else None

    async def list_batches(self) -> list[GradingBatchRecord]:
        pool = await self._pool_()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT data FROM grading_batches ORDER BY created_at DESC, batch_id DESC"
            )
        return [GradingBatchRecord.model_validate_json(r["data"]) for r in rows]

    async def delete_batch(self, batch_id: str) -> bool:
        pool = await self._pool_()
        async with pool.acquire() as conn, conn.transaction():
            status = await conn.execute(
                "DELETE FROM grading_batches WHERE batch_id = $1", batch_id
            )
            await conn.execute("DELETE FROM grading_tasks WHERE batch_id = $1", batch_id)
            await conn.execute("DELETE FROM grading_verdicts WHERE batch_id = $1", batch_id)
            await conn.execute("DELETE FROM grading_adjs WHERE batch_id = $1", batch_id)
        return status.rsplit(" ", 1)[-1] != "0"

    async def begin_adjudication(self, batch_id: str, at: str) -> bool:
        pool = await self._pool_()
        async with pool.acquire() as conn, conn.transaction():
            # FOR UPDATE, so two concurrent clicks serialise here and the second
            # one sees `adjudicating` rather than re-stamping a frozen read.
            row = await conn.fetchrow(
                "SELECT data FROM grading_batches WHERE batch_id = $1 FOR UPDATE", batch_id
            )
            if row is None:
                return False
            batch = GradingBatchRecord.model_validate_json(row["data"])
            if batch.status != "open":
                return False
            updated = batch.model_copy(
                update={"status": "adjudicating", "adjudicating_at": at}
            )
            await conn.execute(
                "UPDATE grading_batches SET data = $1 WHERE batch_id = $2",
                updated.model_dump_json(), batch_id,
            )
            return True

    async def get_task(self, task_id: str) -> GradingTaskRecord | None:
        pool = await self._pool_()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT data FROM grading_tasks WHERE task_id = $1", task_id
            )
        return GradingTaskRecord.model_validate_json(row["data"]) if row else None

    async def list_tasks(self, batch_id: str) -> list[GradingTaskRecord]:
        pool = await self._pool_()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT data FROM grading_tasks WHERE batch_id = $1 ORDER BY position",
                batch_id,
            )
        return [GradingTaskRecord.model_validate_json(r["data"]) for r in rows]

    async def put_verdict(self, verdict: GradingVerdictRecord) -> GradingVerdictRecord:
        pool = await self._pool_()
        async with pool.acquire() as conn, conn.transaction():
            prior = await conn.fetchval(
                "SELECT MAX(version) FROM grading_verdicts"
                " WHERE task_id = $1 AND reader = $2",
                verdict.task_id, verdict.reader,
            )
            out = verdict.model_copy(update={"version": (prior or 0) + 1})
            await conn.execute(
                "INSERT INTO grading_verdicts (task_id, reader, version, batch_id, data)"
                " VALUES ($1, $2, $3, $4, $5)",
                out.task_id, out.reader, out.version, out.batch_id, out.model_dump_json(),
            )
        return out

    async def get_verdict(self, task_id: str, reader: str) -> GradingVerdictRecord | None:
        pool = await self._pool_()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT data FROM grading_verdicts WHERE task_id = $1 AND reader = $2"
                " ORDER BY version DESC LIMIT 1",
                task_id, reader,
            )
        return GradingVerdictRecord.model_validate_json(row["data"]) if row else None

    async def list_verdicts(self, batch_id: str) -> list[GradingVerdictRecord]:
        pool = await self._pool_()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT data FROM grading_verdicts WHERE batch_id = $1", batch_id
            )
        return _current_verdicts(
            [GradingVerdictRecord.model_validate_json(r["data"]) for r in rows]
        )

    async def put_adjudication(
        self, adjudication: GradingAdjudicationRecord
    ) -> GradingAdjudicationRecord:
        pool = await self._pool_()
        async with pool.acquire() as conn, conn.transaction():
            prior = await conn.fetchval(
                "SELECT MAX(version) FROM grading_adjs WHERE task_id = $1",
                adjudication.task_id,
            )
            out = adjudication.model_copy(update={"version": (prior or 0) + 1})
            await conn.execute(
                "INSERT INTO grading_adjs (task_id, version, batch_id, data)"
                " VALUES ($1, $2, $3, $4)",
                out.task_id, out.version, out.batch_id, out.model_dump_json(),
            )
        return out

    async def get_adjudication(self, task_id: str) -> GradingAdjudicationRecord | None:
        pool = await self._pool_()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT data FROM grading_adjs WHERE task_id = $1"
                " ORDER BY version DESC LIMIT 1",
                task_id,
            )
        return GradingAdjudicationRecord.model_validate_json(row["data"]) if row else None

    async def list_adjudications(self, batch_id: str) -> list[GradingAdjudicationRecord]:
        pool = await self._pool_()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT data FROM grading_adjs WHERE batch_id = $1", batch_id
            )
        return _current_adjudications(
            [GradingAdjudicationRecord.model_validate_json(r["data"]) for r in rows]
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


def make_grading_store(backend: str, path: str, dsn: str = "") -> GradingStore:
    """Build the configured grading store (``memory`` | ``sqlite`` | ``postgres``).

    An unknown backend falls back to ``memory`` with a warning rather than
    refusing to boot: grading is an additive surface, and a typo in
    ``GRADING_STORE_BACKEND`` should not take an API down that also serves
    query and ingest. The fallback is loud and the batches are process-local,
    so it is visible on the first restart.
    """
    name = (backend or MEMORY).lower()
    if name == SQLITE:
        return SqliteGradingStore(path)
    if name == POSTGRES:
        return PostgresGradingStore(dsn)
    if name != MEMORY:
        log.warning("unknown grading_store_backend %r; falling back to 'memory'", backend)
    return InMemoryGradingStore()


__all__ = [
    "GradingStore",
    "GradingStoreUnavailable",
    "InMemoryGradingStore",
    "PostgresGradingStore",
    "SqliteGradingStore",
    "make_grading_store",
]
