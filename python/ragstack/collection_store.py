"""Durable, concurrency-safe collection registry.

A collection's **identity is its build spec** — embedding model + dim + chunk
method/size/overlap/params. Ingesting into an existing collection with a
different embedder or chunker produces an incoherent index (mismatched vectors,
inconsistent chunk boundaries) with no error, so the mapping

    collection id -> {physical index, es index, model, dim, chunker, params}

has to be durable and authoritative, not a best-effort side file. This module is
that store. It follows ``docs/libraries-spec.md`` §8.1 and the
:mod:`ragstack.jobstore` precedent: one shared-dialect DDL string for sqlite and
postgres, ``TEXT``/``INTEGER`` only (no ``JSONB``, no ``TIMESTAMPTZ`` — ISO-8601
UTC strings), structured fields via ``json.dumps``, and additive-only migration
through :func:`ensure_columns`.

Four backends, selected by ``collection_store_backend``:

``json`` (default)
    The shipped behaviour: ``collections_file`` holds a JSON list of specs, or
    ``collections_json`` holds the same content inline. **The on-disk format is
    unchanged** — a file written by this store is byte-compatible with one
    written by the pre-store code, so an existing deployment keeps working by
    merely upgrading. What *is* new is that the read-modify-write now runs under
    an ``flock`` on a sidecar ``.lock`` file and writes through a per-writer
    unique temp path, so two processes sharing one ``collections_file`` (which is
    exactly what the three prod API instances do) can no longer lose an entry.
``memory``
    Process-local; nothing persists. Dev/tests.
``sqlite`` / ``postgres``
    A real table. ``postgres`` is the multi-process answer: the row, not a file,
    is the source of truth, and concurrent writers are serialized by the database
    rather than by a filesystem lock.

Migration: pointing an existing deployment at ``sqlite``/``postgres`` seeds the
empty table once from the configured ``collections_file`` (see
:func:`seed_from_json`), so nobody has to hand-transcribe a registry.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

try:  # POSIX only; Windows has no flock and the JSON path degrades to unlocked.
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

# Backend vocabulary (mirror in config.collection_store_backend's comment).
JSON = "json"
MEMORY = "memory"
SQLITE = "sqlite"
POSTGRES = "postgres"


class CollectionSpec(BaseModel):
    """One registry entry — the authoritative build spec for a collection.

    This is also the wire/disk shape of a ``collections_file`` element. **Fields
    here are the JSON file's format**, so nothing may be added to it without
    changing that file: derived values (``spec_hash``) are methods, and store
    bookkeeping (``created_at``/``updated_at``) lives on
    :class:`CollectionRecord`, not here.
    """

    id: str
    label: str = ""
    # Subject that created (and therefore owns) the collection, recorded in the
    # SAME durable write as the spec itself so ownership survives anything the
    # ACL database does not (a memory ACL backend restarting, a crash between
    # the registry write and the owner-row grant). '' = the spec predates
    # ownership (a legacy/hand-authored entry) — exactly the positive marker the
    # startup backfill needs: only an ownerless collection whose spec ALSO
    # records no creator is legacy (world-readable per ADR-0004 decision 4); an
    # ownerless one WITH a recorded creator gets its owner row repaired to that
    # creator and stays private.
    owner: str = ""
    collection: str  # Qdrant collection name (BM25 index defaults to the same)
    text_index: str = ""  # ES index; "" → same as `collection`
    embedding_api: str = "openai"  # sidecar | openai
    embedding_model: str = ""
    embedding_model_dim: int
    embedding_endpoints: list[str] = Field(default_factory=list)
    embedding_sidecar_url: str = ""  # single-endpoint fallback when no `endpoints`
    chunk_method: str = ""  # how the corpus was chunked (see chunk_overlap/params below)
    chunk_size: int | None = None
    chunk_overlap: int | None = None
    chunk_params: dict[str, Any] = Field(default_factory=dict)

    def es_index(self) -> str:
        """The ES index this entry's BM25 leg reads/writes. It rides on
        ``collection`` by default, so whatever isolates the vector store isolates
        the text index too — there is no second name-derivation to keep in sync.

        **Two registry ids may NOT share one physical store.** This docstring
        used to call hand-authoring both specs with the same ``collection`` value
        "the deliberate way to share" one. That was wrong, and it is now a
        startup error (``_build_collection_registry``).

        Access control is asserted at the collection (ADR-0003), so two ids over
        one store are two INDEPENDENT ACLs over one dataset: revoking a grant on
        one id leaves the same bytes readable through the other, and an owner who
        un-publishes a corpus has not un-published it (#275, reproduced live).
        The invariant is ADR-0002's, stated positively: **a physical index has
        exactly one registry entry.**

        ``POST /v1/collections`` never produced this (an explicit id is folded
        into the physical name, so named libraries stay isolated); the config
        path could, and no longer can."""
        return self.text_index or self.collection

    def emb_signature(self) -> tuple[str, str, tuple[str, ...], str]:
        """Identity of the embedding backend — entries that share it reuse one
        embedder instance (one pool), so N same-model collections don't spin up N
        redundant connection pools."""
        eps = tuple(sorted(self.embedding_endpoints)) or (self.embedding_sidecar_url,)
        return (self.embedding_api, self.embedding_model, eps, str(self.embedding_model_dim))

    def chunk_descriptor(self) -> str:
        """This spec's canonical chunk descriptor (see
        :func:`ragstack.provenance.chunk_descriptor`)."""
        from ragstack.provenance import chunk_descriptor

        return chunk_descriptor(
            self.chunk_method, self.chunk_size, self.chunk_overlap, self.chunk_params or None
        )

    def spec_hash(self) -> str:
        """Content-address of the full build spec — the comparison key that
        decides whether an ingest may write into this collection."""
        from ragstack.provenance import spec_hash

        return spec_hash(self.embedding_model or "", self.embedding_model_dim,
                         self.chunk_descriptor())


class CollectionRecord(BaseModel):
    """A stored spec plus the store's own bookkeeping.

    ``spec_hash`` is denormalized onto the row deliberately: it is what an ingest
    guard compares against, and recomputing it from the row would silently follow
    any future change to the hash function instead of reporting drift."""

    spec: CollectionSpec
    spec_hash: str = ""
    created_at: str = ""
    updated_at: str = ""


class CreateOutcome(StrEnum):
    """What :meth:`CollectionStore.create` did — the four answers a capacity-
    checked insert-if-absent can give, kept distinct because the create path maps
    each to a different HTTP status (201 / 409 / 403 / warn-and-fall-back).

    Deliberately NOT a bool: an insert that did not happen is either "the id is
    taken" or "the registry is full", and collapsing them (a rowcount-0
    ``INSERT ... SELECT ... WHERE count < ?``) makes them indistinguishable.
    """

    CREATED = "created"
    DUPLICATE = "duplicate"  #: the id was already stored — the FIRST spec survives
    AT_CAP = "at_cap"  #: the store already holds ``limit`` specs
    UNSUPPORTED = "unsupported"  #: this store cannot persist; the caller must fall back


def _now() -> str:
    return datetime.now(UTC).isoformat()


def make_record(spec: CollectionSpec, *, created_at: str = "", updated_at: str = "") -> CollectionRecord:
    stamp = updated_at or _now()
    return CollectionRecord(
        spec=spec, spec_hash=spec.spec_hash(),
        created_at=created_at or stamp, updated_at=stamp,
    )


@runtime_checkable
class CollectionStore(Protocol):
    """Durable mapping ``collection id -> build spec``."""

    async def list_specs(self) -> list[CollectionSpec]:
        """Every registered spec, in stable registration order."""
        ...

    async def list_records(self) -> list[CollectionRecord]: ...

    async def get(self, cid: str) -> CollectionRecord | None: ...

    async def put(self, spec: CollectionSpec) -> bool:
        """Upsert a spec. Returns ``False`` when the store cannot persist (no
        ``collections_file`` configured, or an inline-JSON registry), which the
        create path reports as "in-memory only, lost on restart"."""
        ...

    async def create(self, spec: CollectionSpec, *, limit: int | None) -> CreateOutcome:
        """Insert ``spec`` **if absent**, refusing once the store already holds
        ``limit`` specs. The capacity reservation for ``POST /v1/collections``.

        The count and the insert are ONE atomic operation in every backend — that
        is the entire point of this method existing next to :meth:`put`. Counting
        in the caller and inserting afterwards is what #286 is: two round-trips
        apart, N concurrent creators at ``limit - 1`` all see room and all pass,
        and across processes ``put``'s upsert silently overwrites a sibling's spec
        while leaving that sibling's physical store unclaimed (the state ADR-0002
        decision 5 outlaws).

        ``limit=None`` disables the cap; ``limit=0`` refuses every create. The
        distinction matters: ``MAX_COLLECTIONS=0`` means *disabled*, while a cap
        fully consumed by reserved slots must mean *refuse everything*, so the
        caller cannot encode both in one int.

        On :attr:`CreateOutcome.DUPLICATE` the stored spec is left EXACTLY as it
        was — never an upsert. A second creator racing the same id must not be
        able to re-point an existing registry entry at its own physical store.
        """
        ...

    async def delete(self, cid: str) -> bool:
        """Remove a spec. ``False`` when the id wasn't stored."""
        ...

    async def close(self) -> None: ...


# --------------------------------------------------------------------------- #
# JSON file (the shipped format) — locked read-modify-write
# --------------------------------------------------------------------------- #


@contextmanager
def json_file_lock(path: str) -> Iterator[None]:
    """Hold an exclusive ``flock`` for the duration of a read-modify-write of
    ``path``.

    The lock lives on a sidecar ``{path}.lock`` file rather than on the registry
    file itself, because the write ends in ``os.replace`` — a lock held on the
    *old* inode would say nothing about the new one, so every writer must agree
    on a file that never gets swapped.

    Degrades to a no-op (with a warning) when the lock cannot be taken: a
    read-only config directory must not make the API unable to read its own
    registry, and unlocked is exactly the pre-existing behaviour.
    """
    if fcntl is None or not path:  # pragma: no cover - non-POSIX / unset
        yield
        return
    fd: int | None = None
    try:
        fd = os.open(f"{path}.lock", os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX)
    except OSError as e:
        log.warning("collections_file: could not lock %s.lock (%s); proceeding unlocked", path, e)
        if fd is not None:
            os.close(fd)
        fd = None
    try:
        yield
    finally:
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


def specs_from_rows(rows: list[Any]) -> list[CollectionSpec]:
    """Validate a list of registry entry dicts, rejecting duplicate ids."""
    specs = [CollectionSpec.model_validate(d) for d in rows]
    ids = [s.id for s in specs]
    if len(set(ids)) != len(ids):
        raise RuntimeError(f"duplicate collection ids in registry: {ids}")
    return specs


def parse_specs(raw: str) -> list[CollectionSpec]:
    """Parse a registry JSON document (a list of specs), rejecting duplicate ids."""
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"collections config is not valid JSON: {e}") from e
    if not isinstance(data, list):
        raise RuntimeError("collections config must be a JSON list of specs")
    return specs_from_rows(data)


def read_json_file(path: str) -> list[dict[str, Any]]:
    """The raw entry dicts in ``path`` ([] when absent). Preserves unknown keys —
    a hand-authored file may carry comment fields (prod's ``_alias_note``), and a
    rewrite must not silently drop them."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            existing = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise RuntimeError(f"collections_file unreadable: {e}") from e
    if not isinstance(existing, list):
        raise RuntimeError("collections_file must be a JSON list")
    return existing


def write_json_file(path: str, data: list[Any]) -> None:
    """Atomically replace ``path`` with ``data``.

    The temp path is per-writer unique: a fixed ``path + '.tmp'`` lets two
    concurrent writers (or a crash mid-write plus a retry) stomp each other's
    partial file. ``indent=2`` keeps the format byte-identical to what shipped."""
    tmp = f"{path}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:  # pragma: no cover - best effort
            pass
        raise


def append_spec_to_file(path: str, spec: CollectionSpec) -> bool:
    """Upsert ``spec`` into the JSON registry at ``path``, under the file lock.

    Upsert rather than blind append: the create path 409s on a duplicate id
    *within one process*, which says nothing about a sibling process, and a file
    with two rows for one id has no defined winner."""
    if not path:
        return False
    with json_file_lock(path):
        existing = read_json_file(path)
        row = spec.model_dump()
        for i, d in enumerate(existing):
            if isinstance(d, dict) and d.get("id") == spec.id:
                existing[i] = row
                break
        else:
            existing.append(row)
        write_json_file(path, existing)
    return True


def create_spec_in_file(path: str, spec: CollectionSpec, *, limit: int | None) -> CreateOutcome:
    """Insert-if-absent with a capacity check, both inside ONE flock section.

    The flock is what makes this cross-process: the count, the duplicate check
    and the ``os.replace`` are a single critical section, so two uvicorns sharing
    a ``collections_file`` cannot both observe ``limit - 1`` entries. (Without the
    lock the read-modify-write loses entries outright — see
    :func:`json_file_lock`.)
    """
    if not path:
        return CreateOutcome.UNSUPPORTED  # inline/unset registry: nothing to reserve in
    with json_file_lock(path):
        existing = read_json_file(path)
        if any(isinstance(d, dict) and d.get("id") == spec.id for d in existing):
            return CreateOutcome.DUPLICATE
        if limit is not None and len(existing) >= limit:
            return CreateOutcome.AT_CAP
        existing.append(spec.model_dump())
        write_json_file(path, existing)
    return CreateOutcome.CREATED


def remove_spec_from_file(path: str, cid: str) -> bool:
    """Drop the entry with id ``cid`` from the JSON registry, under the lock."""
    if not path or not os.path.exists(path):
        return False
    with json_file_lock(path):
        existing = read_json_file(path)
        kept = [d for d in existing if not (isinstance(d, dict) and d.get("id") == cid)]
        if len(kept) == len(existing):
            return False
        write_json_file(path, kept)
    return True


class JsonFileCollectionStore:
    """The shipped ``collections_file`` / ``collections_json`` registry.

    Reads ``settings`` *lazily* on every call rather than snapshotting the path at
    construction: the file location is a live setting (tests monkeypatch it, and
    an operator may point a reload at a new file), and a store that captured an
    empty path at startup would silently stop persisting.
    """

    def __init__(self, settings: Any) -> None:
        self._settings = settings

    @property
    def path(self) -> str:
        return getattr(self._settings, "collections_file", "") or ""

    @property
    def _inline(self) -> str:
        return getattr(self._settings, "collections_json", "") or ""

    def load_specs_sync(self) -> list[CollectionSpec]:
        """Blocking read of the registry — used directly by the synchronous
        ``load_collection_specs`` façade, and off-thread by :meth:`list_specs`."""
        path = self.path
        if path:
            with json_file_lock(path):
                rows = read_json_file(path)
            return specs_from_rows(rows)
        return parse_specs(self._inline)

    async def list_specs(self) -> list[CollectionSpec]:
        return await asyncio.to_thread(self.load_specs_sync)

    async def list_records(self) -> list[CollectionRecord]:
        # The file format carries no timestamps (adding them would change it), so
        # created_at/updated_at are empty here. That is a real limitation of this
        # backend, not a bug: use sqlite/postgres if you need registration times.
        return [
            CollectionRecord(spec=s, spec_hash=s.spec_hash())
            for s in await self.list_specs()
        ]

    async def get(self, cid: str) -> CollectionRecord | None:
        for r in await self.list_records():
            if r.spec.id == cid:
                return r
        return None

    async def put(self, spec: CollectionSpec) -> bool:
        path = self.path
        if not path:
            return False  # inline/unset registry: in-memory only, lost on restart
        return await asyncio.to_thread(append_spec_to_file, path, spec)

    async def create(self, spec: CollectionSpec, *, limit: int | None) -> CreateOutcome:
        path = self.path
        if not path:
            # Mirrors put()'s False: an inline/unset registry cannot hold a
            # reservation, so the caller falls back to its in-process count.
            return CreateOutcome.UNSUPPORTED
        return await asyncio.to_thread(create_spec_in_file, path, spec, limit=limit)

    async def delete(self, cid: str) -> bool:
        path = self.path
        if not path:
            return False
        return await asyncio.to_thread(remove_spec_from_file, path, cid)

    async def close(self) -> None:
        """No resources to release."""


class InMemoryCollectionStore:
    """Process-local registry. Loses everything on restart — dev/tests."""

    def __init__(self, specs: list[CollectionSpec] | None = None) -> None:
        self._records: dict[str, CollectionRecord] = {
            s.id: make_record(s) for s in (specs or [])
        }
        self._lock = asyncio.Lock()

    async def list_specs(self) -> list[CollectionSpec]:
        return [r.spec for r in await self.list_records()]

    async def list_records(self) -> list[CollectionRecord]:
        async with self._lock:
            return [r.model_copy(deep=True) for r in self._records.values()]

    async def get(self, cid: str) -> CollectionRecord | None:
        async with self._lock:
            r = self._records.get(cid)
            return r.model_copy(deep=True) if r is not None else None

    async def put(self, spec: CollectionSpec) -> bool:
        async with self._lock:
            prior = self._records.get(spec.id)
            self._records[spec.id] = make_record(
                spec, created_at=prior.created_at if prior else ""
            )
        return True

    async def create(self, spec: CollectionSpec, *, limit: int | None) -> CreateOutcome:
        # The asyncio lock is the whole mechanism: this store is process-local, so
        # "atomic" here means no other coroutine may run between the count and the
        # insert — which is exactly what an uncancelled `async with` guarantees.
        async with self._lock:
            if spec.id in self._records:
                return CreateOutcome.DUPLICATE
            if limit is not None and len(self._records) >= limit:
                return CreateOutcome.AT_CAP
            self._records[spec.id] = make_record(spec)
        return CreateOutcome.CREATED

    async def delete(self, cid: str) -> bool:
        async with self._lock:
            return self._records.pop(cid, None) is not None

    async def close(self) -> None:
        """No resources to release."""


# --------------------------------------------------------------------------- #
# SQL backends
# --------------------------------------------------------------------------- #

# Shared verbatim by the sqlite and postgres stores (both dialects accept this
# CREATE TABLE form), so the schema lives in one place — jobstore.py's
# discipline. TEXT for strings/JSON/timestamps (no JSONB: sqlite gives it NUMERIC
# affinity; no TIMESTAMPTZ: ISO-8601 UTC text), INTEGER only where the value is
# genuinely a number both dialects agree on.
_COLLECTIONS_DDL = (
    "CREATE TABLE IF NOT EXISTS collections ("
    "  id TEXT PRIMARY KEY,"
    "  label TEXT NOT NULL DEFAULT '',"
    "  collection TEXT NOT NULL,"
    "  text_index TEXT NOT NULL DEFAULT '',"
    "  embedding_api TEXT NOT NULL DEFAULT 'openai',"
    "  embedding_model TEXT NOT NULL DEFAULT '',"
    "  embedding_model_dim INTEGER NOT NULL DEFAULT 0,"
    "  embedding_endpoints TEXT NOT NULL DEFAULT '[]',"
    "  embedding_sidecar_url TEXT NOT NULL DEFAULT '',"
    "  chunk_method TEXT NOT NULL DEFAULT '',"
    "  chunk_size INTEGER,"
    "  chunk_overlap INTEGER,"
    "  chunk_params TEXT NOT NULL DEFAULT '{}',"
    "  spec_hash TEXT NOT NULL DEFAULT '',"
    "  created_at TEXT NOT NULL DEFAULT '',"
    "  updated_at TEXT NOT NULL DEFAULT '',"
    "  owner TEXT NOT NULL DEFAULT ''"
    ")"
)

# Column -> DDL fragment, for additive migration of a table created by an older
# build. Every entry MUST be nullable or defaulted: sqlite's ALTER TABLE ADD
# COLUMN forbids UNIQUE and forbids NOT NULL without a default (libraries-spec
# §8.1's migration convention).
_COLLECTIONS_COLUMNS: dict[str, str] = {
    "label": "TEXT NOT NULL DEFAULT ''",
    "collection": "TEXT NOT NULL DEFAULT ''",
    "text_index": "TEXT NOT NULL DEFAULT ''",
    "embedding_api": "TEXT NOT NULL DEFAULT 'openai'",
    "embedding_model": "TEXT NOT NULL DEFAULT ''",
    "embedding_model_dim": "INTEGER NOT NULL DEFAULT 0",
    "embedding_endpoints": "TEXT NOT NULL DEFAULT '[]'",
    "embedding_sidecar_url": "TEXT NOT NULL DEFAULT ''",
    "chunk_method": "TEXT NOT NULL DEFAULT ''",
    "chunk_size": "INTEGER",
    "chunk_overlap": "INTEGER",
    "chunk_params": "TEXT NOT NULL DEFAULT '{}'",
    "spec_hash": "TEXT NOT NULL DEFAULT ''",
    "created_at": "TEXT NOT NULL DEFAULT ''",
    "updated_at": "TEXT NOT NULL DEFAULT ''",
    "owner": "TEXT NOT NULL DEFAULT ''",
}

_COLUMNS = (
    "id", "label", "collection", "text_index", "embedding_api", "embedding_model",
    "embedding_model_dim", "embedding_endpoints", "embedding_sidecar_url",
    "chunk_method", "chunk_size", "chunk_overlap", "chunk_params",
    "spec_hash", "created_at", "updated_at", "owner",
)
_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM collections"
# Plain inserts (no ON CONFLICT clause) for the create path: a create that finds
# the id present must NOT upsert, so the "do nothing on conflict" behaviour is a
# preceding SELECT inside the same serialized unit rather than a clause here.
_INSERT_SQLITE = (
    f"INSERT INTO collections ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join('?' * len(_COLUMNS))})"
)
_INSERT_POSTGRES = (
    f"INSERT INTO collections ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join(f'${i + 1}' for i in range(len(_COLUMNS)))})"
)
# Stable registration order: created_at ascends with insertion, id breaks ties
# for rows seeded in one batch (identical timestamps are possible).
_ORDER = " ORDER BY created_at, id"


def ensure_columns_sqlite(conn: sqlite3.Connection, table: str, cols: dict[str, str]) -> None:
    """Additive-only migration for sqlite: add any missing column.

    There is no migration tooling in this repo (``CREATE TABLE IF NOT EXISTS``
    cannot alter an existing table), so this is the convention
    ``docs/libraries-spec.md`` §8.1 settles on. The Alembic gap is repo-wide debt.
    """
    have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, ddl in cols.items():
        if name not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


async def ensure_columns_postgres(conn: Any, table: str, cols: dict[str, str]) -> None:
    """Additive-only migration for postgres (``ADD COLUMN IF NOT EXISTS``)."""
    for name, ddl in cols.items():
        await conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {ddl}")


def _record_to_row(rec: CollectionRecord) -> tuple:
    s = rec.spec
    return (
        s.id, s.label, s.collection, s.text_index, s.embedding_api, s.embedding_model,
        s.embedding_model_dim, json.dumps(s.embedding_endpoints), s.embedding_sidecar_url,
        s.chunk_method, s.chunk_size, s.chunk_overlap, json.dumps(s.chunk_params),
        rec.spec_hash, rec.created_at, rec.updated_at, s.owner,
    )


def _row_to_record(row: Any) -> CollectionRecord:
    (
        rid, label, collection, text_index, api, model, dim, endpoints, sidecar,
        method, size, overlap, params, shash, created, updated, owner,
    ) = tuple(row)
    return CollectionRecord(
        spec=CollectionSpec(
            id=rid, label=label, owner=owner or "", collection=collection,
            text_index=text_index,
            embedding_api=api, embedding_model=model, embedding_model_dim=dim,
            embedding_endpoints=json.loads(endpoints or "[]"),
            embedding_sidecar_url=sidecar, chunk_method=method,
            chunk_size=size, chunk_overlap=overlap,
            chunk_params=json.loads(params or "{}"),
        ),
        spec_hash=shash, created_at=created, updated_at=updated,
    )


class SqliteCollectionStore:
    """Durable single-host registry on stdlib sqlite3.

    A connection per operation, run in a worker thread so blocking sqlite never
    stalls the event loop; WAL so readers coexist with the writer. Concurrency is
    the database's problem, not a file lock's — an upsert is one statement, so
    two writers cannot lose each other's entry the way the JSON path could.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        with closing(self._connect()) as conn, conn:
            conn.execute(_COLLECTIONS_DDL)
            ensure_columns_sqlite(conn, "collections", _COLLECTIONS_COLUMNS)

    def _connect(self) -> sqlite3.Connection:
        # ``with closing(...) as conn, conn:`` — sqlite3's connection context
        # manager commits but does NOT close (jobstore.py's note).
        conn = sqlite3.connect(self._path)
        # busy_timeout FIRST: setting journal_mode=WAL is itself a write that can
        # lose a race against another process opening the same fresh database, so
        # it needs the timeout too. Wait for a contended write lock rather than
        # raising SQLITE_BUSY immediately — required now that create() holds a
        # write transaction across a count and an insert.
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _list_sync(self) -> list[CollectionRecord]:
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(_SELECT + _ORDER).fetchall()
        return [_row_to_record(r) for r in rows]

    def _get_sync(self, cid: str) -> CollectionRecord | None:
        with closing(self._connect()) as conn, conn:
            row = conn.execute(_SELECT + " WHERE id = ?", (cid,)).fetchone()
        return _row_to_record(row) if row is not None else None

    def _put_sync(self, spec: CollectionSpec) -> bool:
        with closing(self._connect()) as conn, conn:
            prior = conn.execute(
                "SELECT created_at FROM collections WHERE id = ?", (spec.id,)
            ).fetchone()
            rec = make_record(spec, created_at=prior[0] if prior else "")
            assignments = ", ".join(f"{c} = excluded.{c}" for c in _COLUMNS if c != "id")
            conn.execute(
                f"INSERT INTO collections ({', '.join(_COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(_COLUMNS))}) "
                f"ON CONFLICT(id) DO UPDATE SET {assignments}",
                _record_to_row(rec),
            )
        return True

    def _create_sync(self, spec: CollectionSpec, *, limit: int | None) -> CreateOutcome:
        """Count and insert as ONE serialized unit, via ``BEGIN IMMEDIATE``.

        ``BEGIN IMMEDIATE`` takes sqlite's RESERVED write lock *up front*, before
        the count — a deferred transaction would only acquire it at the INSERT,
        by which point another writer's row may have landed and the count that
        authorized this insert is stale. Manual transaction control
        (``isolation_level = None``) is what makes the explicit BEGIN possible:
        the default implicit-BEGIN mode would start the transaction at the INSERT
        instead, i.e. exactly the deferred behaviour this must avoid.
        """
        with closing(self._connect()) as conn:
            conn.isolation_level = None  # manual BEGIN/COMMIT
            conn.execute("BEGIN IMMEDIATE")
            try:
                if conn.execute(
                    "SELECT 1 FROM collections WHERE id = ?", (spec.id,)
                ).fetchone() is not None:
                    conn.execute("ROLLBACK")
                    return CreateOutcome.DUPLICATE
                if limit is not None:
                    n = conn.execute("SELECT COUNT(*) FROM collections").fetchone()[0]
                    if n >= limit:
                        conn.execute("ROLLBACK")
                        return CreateOutcome.AT_CAP
                conn.execute(_INSERT_SQLITE, _record_to_row(make_record(spec)))
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        return CreateOutcome.CREATED

    def _delete_sync(self, cid: str) -> bool:
        with closing(self._connect()) as conn, conn:
            cur = conn.execute("DELETE FROM collections WHERE id = ?", (cid,))
            return cur.rowcount > 0

    async def list_specs(self) -> list[CollectionSpec]:
        return [r.spec for r in await self.list_records()]

    async def list_records(self) -> list[CollectionRecord]:
        return await asyncio.to_thread(self._list_sync)

    async def get(self, cid: str) -> CollectionRecord | None:
        return await asyncio.to_thread(self._get_sync, cid)

    async def put(self, spec: CollectionSpec) -> bool:
        return await asyncio.to_thread(self._put_sync, spec)

    async def create(self, spec: CollectionSpec, *, limit: int | None) -> CreateOutcome:
        return await asyncio.to_thread(self._create_sync, spec, limit=limit)

    async def delete(self, cid: str) -> bool:
        return await asyncio.to_thread(self._delete_sync, cid)

    async def close(self) -> None:
        """No persistent connection to release."""


#: Postgres advisory-lock key serializing capacity-checked creates
#: (:meth:`PostgresCollectionStore.create`). ``b"rag_coll"`` as an int64 —
#: distinct from _SHARES_DDL_LOCK_KEY / _USERS_DDL_LOCK_KEY /
#: _GROUPS_DDL_LOCK_KEY / _ROLE_WRITE_LOCK, which share this namespace.
_COLLECTIONS_CREATE_LOCK_KEY = 0x7261675F636F6C6C


def _normalize_dsn(dsn: str) -> str:
    """Strip SQLAlchemy-style ``+driver`` suffixes asyncpg doesn't understand."""
    for marker in ("+asyncpg", "+psycopg2", "+psycopg"):
        dsn = dsn.replace(marker, "")
    return dsn


class PostgresCollectionStore:
    """Durable, multi-process registry on Postgres via asyncpg.

    The backend that actually answers §8.2 item 3/4: the three prod API instances
    share one registry, and here the row — not a JSON file they race on — is the
    source of truth. Pool and schema are created lazily on first use (asyncpg
    needs a loop), so construction stays synchronous like the other stores.
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
                            "postgres collection store requires asyncpg "
                            "(pip install ragstack[postgres])"
                        ) from e
                    pool = await asyncpg.create_pool(
                        self._dsn, min_size=self._min, max_size=self._max
                    )
                    async with pool.acquire() as conn:
                        await conn.execute(_COLLECTIONS_DDL)
                        await ensure_columns_postgres(conn, "collections", _COLLECTIONS_COLUMNS)
                    self._pool = pool
        return self._pool

    async def list_specs(self) -> list[CollectionSpec]:
        return [r.spec for r in await self.list_records()]

    async def list_records(self) -> list[CollectionRecord]:
        pool = await self._pool_()
        async with pool.acquire() as conn:
            rows = await conn.fetch(_SELECT + _ORDER)
        return [_row_to_record(tuple(r)) for r in rows]

    async def get(self, cid: str) -> CollectionRecord | None:
        pool = await self._pool_()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(_SELECT + " WHERE id = $1", cid)
        return _row_to_record(tuple(row)) if row is not None else None

    async def put(self, spec: CollectionSpec) -> bool:
        placeholders = ", ".join(f"${i + 1}" for i in range(len(_COLUMNS)))
        assignments = ", ".join(f"{c} = excluded.{c}" for c in _COLUMNS if c != "id")
        pool = await self._pool_()
        async with pool.acquire() as conn:
            prior = await conn.fetchval(
                "SELECT created_at FROM collections WHERE id = $1", spec.id
            )
            rec = make_record(spec, created_at=prior or "")
            await conn.execute(
                f"INSERT INTO collections ({', '.join(_COLUMNS)}) VALUES ({placeholders}) "
                f"ON CONFLICT (id) DO UPDATE SET {assignments}",
                *_record_to_row(rec),
            )
        return True

    async def create(self, spec: CollectionSpec, *, limit: int | None) -> CreateOutcome:
        """Count and insert under a transaction-scoped advisory lock.

        The lock is not paranoia, it is the only thing that works. At READ
        COMMITTED (Postgres' default) a concurrent transaction's uncommitted
        INSERT is invisible, so K creators at ``limit - 1`` each count ``limit -
        1``, each insert, and all K commit — a plain ``INSERT ... WHERE (SELECT
        count(*) ...) < N`` has precisely this hole. ``pg_advisory_xact_lock``
        serializes the whole count-then-insert against every other creator and is
        released by COMMIT/ROLLBACK, so a crashed backend cannot wedge the cap.
        """
        pool = await self._pool_()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock($1)", _COLLECTIONS_CREATE_LOCK_KEY
            )
            if await conn.fetchval("SELECT 1 FROM collections WHERE id = $1", spec.id):
                return CreateOutcome.DUPLICATE
            if limit is not None:
                n = await conn.fetchval("SELECT COUNT(*) FROM collections")
                if n >= limit:
                    return CreateOutcome.AT_CAP
            await conn.execute(_INSERT_POSTGRES, *_record_to_row(make_record(spec)))
        return CreateOutcome.CREATED

    async def delete(self, cid: str) -> bool:
        pool = await self._pool_()
        async with pool.acquire() as conn:
            status = await conn.execute("DELETE FROM collections WHERE id = $1", cid)
        return not status.endswith(" 0")

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()


# --------------------------------------------------------------------------- #
# Construction + migration
# --------------------------------------------------------------------------- #


def make_collection_store(settings: Any) -> CollectionStore:
    """Build the configured collection store.

    Defaults to ``json``, which is the shipped behaviour byte-for-byte — an
    existing deployment that merely upgrades gets the file lock and nothing else.
    """
    backend = (getattr(settings, "collection_store_backend", JSON) or JSON).lower()
    if backend == SQLITE:
        return SqliteCollectionStore(settings.collection_store_path)
    if backend == POSTGRES:
        return PostgresCollectionStore(
            getattr(settings, "collection_store_dsn", "") or settings.postgres_dsn
        )
    if backend == MEMORY:
        return InMemoryCollectionStore()
    if backend != JSON:
        log.warning(
            "unknown collection_store_backend %r; falling back to 'json'", backend
        )
    return JsonFileCollectionStore(settings)


async def seed_from_json(store: CollectionStore, settings: Any) -> int:
    """One-time migration: copy ``collections_file`` into an empty DB-backed store.

    This is the whole upgrade path for a deployment moving off the JSON file —
    set ``COLLECTION_STORE_BACKEND=sqlite`` (or ``postgres``), leave
    ``COLLECTIONS_FILE`` where it is, restart, and the registry is imported once.
    Never runs against a non-empty store (so it cannot resurrect a deleted
    collection), and never for the ``json``/``memory`` backends. The JSON file is
    left untouched, so a rollback is just unsetting the backend.
    """
    if isinstance(store, (JsonFileCollectionStore, InMemoryCollectionStore)):
        return 0
    if await store.list_specs():
        return 0
    source = JsonFileCollectionStore(settings)
    try:
        specs = await source.list_specs()
    except RuntimeError as e:
        log.warning("collection store: cannot seed from collections_file: %s", e)
        return 0
    for spec in specs:
        await store.put(spec)
    if specs:
        log.info(
            "collection store: seeded %d collections from %s",
            len(specs), source.path or "collections_json",
        )
    return len(specs)
