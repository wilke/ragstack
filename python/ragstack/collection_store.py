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

Lifecycle (#353 / #358). Every record also carries the collection's lifecycle
bookkeeping — ``state`` (``active | archiving | dormant | restoring | lost``),
``versions`` (the ordered archive version numbers restore replays),
``archive_pending`` (the last load's archive step failed: not evictable) and
``last_accessed_at`` (eviction's LRU key). These live on
:class:`CollectionRecord`, never on :class:`CollectionSpec` (the frozen JSON file
format): the SQL backends add columns (additive migration), the JSON backend
keeps them in a sidecar ``{collections_file}.lifecycle.json`` under the same
``flock``, and the memory backend holds them in the record. State changes are
compare-and-swap (:meth:`CollectionStore.set_state` with ``expect``), which is
what lets N concurrent requests on a dormant collection produce exactly one
restore submission. ``last_accessed_at`` is NEVER written per request:
:class:`AccessTracker` batches touches in-process and flushes every
``collection_access_flush_seconds`` (and at shutdown).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import uuid
from collections.abc import Iterable, Iterator
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


# Lifecycle states (#353). Mirror in contracts/schemas/collection_info.json.
ACTIVE = "active"        #: physical stores exist; reads/writes proceed
ARCHIVING = "archiving"  #: an archive version is being written; reads/writes proceed
DORMANT = "dormant"      #: only the Workspace archive exists; first access restores
RESTORING = "restoring"  #: a restore submission is in flight; 503 + Retry-After
LOST = "lost"            #: archive missing or failed verification; 409 until repaired
STATES = frozenset({ACTIVE, ARCHIVING, DORMANT, RESTORING, LOST})
#: The states in which a collection HOLDS its physical stores (a Qdrant
#: collection + an ES index — a slot against ``max_collections``, #359):
#: ``active``, ``archiving`` (stores exist, the archive step is running) and
#: ``restoring`` (the loader is rebuilding them). ``dormant``/``lost`` hold
#: nothing. Only ``active`` is evictable; the other two are mid-transition.
PHYSICAL = frozenset({ACTIVE, ARCHIVING, RESTORING})


class CollectionRecord(BaseModel):
    """A stored spec plus the store's own bookkeeping.

    ``spec_hash`` is denormalized onto the row deliberately: it is what an ingest
    guard compares against, and recomputing it from the row would silently follow
    any future change to the hash function instead of reporting drift.

    The lifecycle fields (#353/#358) are store bookkeeping too — they are NOT
    part of the spec, so the JSON file format is untouched — and are preserved
    across :meth:`CollectionStore.put` (a spec upsert never resets a state)."""

    spec: CollectionSpec
    spec_hash: str = ""
    created_at: str = ""
    updated_at: str = ""
    #: ``active | archiving | dormant | restoring | lost`` (:data:`STATES`).
    state: str = ACTIVE
    #: Why the row is in its state — the recorded error for ``dormant`` after a
    #: failed restore, the verification failure for ``lost``; '' otherwise.
    state_reason: str = ""
    #: ISO-8601 UTC of the last state change; '' for a row that never changed.
    #: The restore watchdog uses it to un-stick a ``restoring`` row whose API
    #: process died mid-restore.
    state_changed_at: str = ""
    #: Ordered archive version numbers (``versions/<n>/`` in the owner's
    #: Workspace). Restore replays them in this order.
    versions: list[int] = Field(default_factory=list)
    #: The last load happened but its archive step/upload failed: the collection
    #: stays active, cannot be evicted, and the owner's next call retries.
    archive_pending: bool = False
    #: ISO-8601 UTC of the last read/ingest that touched it (batched writes).
    last_accessed_at: str = ""


#: The record fields that are lifecycle bookkeeping (as opposed to the spec and
#: the create/update stamps). One list, used by every backend to preserve them
#: across a spec upsert and to serialise them.
LIFECYCLE_FIELDS = (
    "state", "state_reason", "state_changed_at", "versions", "archive_pending",
    "last_accessed_at",
)


def lifecycle_of(rec: CollectionRecord) -> dict[str, Any]:
    """The lifecycle fields of ``rec`` as a plain dict (JSON-serialisable)."""
    return {f: getattr(rec, f) for f in LIFECYCLE_FIELDS}


def with_lifecycle(rec: CollectionRecord, data: dict[str, Any] | None) -> CollectionRecord:
    """``rec`` with its lifecycle fields replaced by ``data`` (missing keys keep
    the defaults). Tolerates a stale/partial dict, e.g. an older sidecar file."""
    if not data:
        return rec
    update: dict[str, Any] = {}
    for f in LIFECYCLE_FIELDS:
        if f in data:
            update[f] = data[f]
    if "versions" in update:
        update["versions"] = [int(v) for v in (update["versions"] or [])]
    if "archive_pending" in update:
        update["archive_pending"] = bool(update["archive_pending"])
    if "state" in update and update["state"] not in STATES:
        log.warning("collection %r: unknown lifecycle state %r; treating as %s",
                    rec.spec.id, update["state"], ACTIVE)
        update["state"] = ACTIVE
    return rec.model_copy(update=update)


def evictable(rec: CollectionRecord) -> bool:
    """May the eviction policy (#359) make this collection dormant?

    Only an ``active`` collection whose archive is CURRENT: ``archive_pending``
    means the last load's archive step failed, so evicting would lose data that
    exists nowhere else; an empty ``versions`` list means no archive was ever
    written, for the same reason. Every other state is either mid-transition or
    already off the physical stores."""
    return rec.state == ACTIVE and not rec.archive_pending and bool(rec.versions)


def _check_state(state: str) -> str:
    if state not in STATES:
        raise ValueError(f"unknown collection lifecycle state {state!r}; valid: {sorted(STATES)}")
    return state


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
        ``limit`` **physically present** specs. The capacity reservation for
        ``POST /v1/collections``.

        Since #359 the cap bounds the collections whose stores exist —
        :data:`PHYSICAL`: ``active``, plus ``archiving`` and ``restoring``,
        which hold (or are rebuilding) a Qdrant/ES slot but are not evictable.
        A ``dormant`` (or ``lost``) row costs nothing physical and is not
        counted. The count runs inside the same atomic section as the insert,
        so eviction freeing a slot and a create taking it cannot interleave
        with a second creator.

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

    # -- lifecycle (#353 / #358) ------------------------------------------ #

    async def set_state(
        self, cid: str, state: str, *, expect: str | None = None, reason: str = ""
    ) -> bool:
        """Move ``cid`` to ``state`` (recording ``reason`` and the change time).

        With ``expect`` this is a **compare-and-swap**: the write happens only
        if the stored state is ``expect`` at that moment, atomically in every
        backend, and the return value says whether THIS call made the change.
        That is the whole mechanism behind "N concurrent requests on a dormant
        collection submit exactly one restore": each CASes ``dormant →
        restoring`` and only the winner submits. ``False`` also for an unknown
        id."""
        ...

    async def append_version(self, cid: str, version: int) -> list[int]:
        """Record archive version ``version`` on the row (idempotent — a version
        already listed is not duplicated). Returns the full ordered list, or
        ``[]`` for an unknown id."""
        ...

    async def set_archive_pending(self, cid: str, pending: bool) -> bool:
        """Flag/clear "the last load's archive is missing" (blocks eviction)."""
        ...

    async def touch_accessed(self, ids: Iterable[str], stamp: str | None = None) -> int:
        """Stamp ``last_accessed_at`` on every listed id in ONE write. Callers
        must batch through :class:`AccessTracker` — this is never called per
        request. Returns the number of rows updated."""

    async def next_version(self, cid: str) -> int:
        """Reserve and return the next archive version number for ``cid``
        (#203/#353): ``1`` on the first call, then ``2``, … — the ``versions/<n>/``
        subfolder a GoWe ingest job's archive lands in. The increment is ONE
        atomic statement in every backend, so two concurrent jobs on the same
        collection can never be handed the same number. A number is consumed
        when reserved, not when the job completes: a failed run leaves a gap,
        and gaps are fine (``WorkspaceClient.list_versions`` lists what exists).

        Raises ``KeyError`` for an id the store does not hold (the settings-
        derived ``default`` entry has no row) and ``NotImplementedError`` from a
        backend that cannot persist a counter (the JSON-file registry — dev only;
        ``require_durable_backends`` already forbids it in production).
        """
        ...

    async def close(self) -> None: ...


class AccessTracker:
    """Batches ``last_accessed_at`` touches: an in-process dirty set flushed
    every ``flush_seconds`` and at shutdown — never a registry write per request.

    ``touch`` is synchronous and I/O-free (a set insert) so the request path
    pays nothing. ``flush`` writes the whole batch through ONE
    :meth:`CollectionStore.touch_accessed` call; a failing store keeps the ids
    dirty for the next flush instead of dropping them. ``writes`` counts store
    calls, which is what the batching test asserts on."""

    def __init__(self, store: CollectionStore, flush_seconds: float = 60.0) -> None:
        self._store = store
        self._flush_seconds = max(0.05, float(flush_seconds))
        self._dirty: set[str] = set()
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self.writes = 0
        self.touched = 0

    def touch(self, cid: str) -> None:
        if cid:
            self._dirty.add(cid)
            self.touched += 1

    @property
    def pending(self) -> int:
        return len(self._dirty)

    async def flush(self) -> int:
        """Write every dirty id (one store call). Returns how many were flushed."""
        async with self._lock:
            ids, self._dirty = self._dirty, set()
            if not ids:
                return 0
            try:
                await self._store.touch_accessed(sorted(ids), _now())
                self.writes += 1
            except Exception:  # noqa: BLE001 — retry on the next flush, never lose the batch
                log.warning("collection access tracker: flush of %d id(s) failed; "
                            "retrying next round", len(ids), exc_info=True)
                self._dirty |= ids
                return 0
            return len(ids)

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._flush_seconds)
            await self.flush()

    def start(self) -> None:
        """Start the periodic flush (needs a running loop). Idempotent."""
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        """Cancel the periodic flush and write whatever is still dirty."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 — shutting down
                pass
        await self.flush()


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


def lifecycle_path(path: str) -> str:
    """The JSON backend's lifecycle sidecar: ``{collections_file}.lifecycle.json``.

    A separate file because the registry file's format is frozen (every field
    of :class:`CollectionSpec` is that format), and lifecycle is store
    bookkeeping, not spec. It is read and written under the SAME flock as the
    registry file, so a state CAS is cross-process on this backend too."""
    return f"{path}.lifecycle.json"


def read_lifecycle_file(path: str) -> dict[str, dict[str, Any]]:
    """``{collection id: lifecycle dict}`` from the sidecar ({} when absent or
    unreadable — a corrupt sidecar must not make the registry unreadable; it
    degrades to "everything active", which is the pre-lifecycle behaviour)."""
    lp = lifecycle_path(path)
    if not os.path.exists(lp):
        return {}
    try:
        with open(lp, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("collections lifecycle sidecar %s unreadable (%s); ignoring", lp, e)
        return {}
    return data if isinstance(data, dict) else {}


def write_lifecycle_file(path: str, data: dict[str, dict[str, Any]]) -> None:
    write_json_file(lifecycle_path(path), data)  # type: ignore[arg-type]


def _drop_lifecycle_entry(path: str, cid: str) -> None:
    """Forget ``cid``'s lifecycle (called under the lock, on delete and on a
    fresh create): the id namespace is reusable, and a stale ``dormant`` row
    inherited by the next collection minted under the same id would 503 it."""
    data = read_lifecycle_file(path)
    if cid in data:
        del data[cid]
        write_lifecycle_file(path, data)


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
        if limit is not None and _count_physical_rows(existing, read_lifecycle_file(path)) >= limit:
            return CreateOutcome.AT_CAP
        existing.append(spec.model_dump())
        write_json_file(path, existing)
        _drop_lifecycle_entry(path, spec.id)  # a new collection starts `active`
    return CreateOutcome.CREATED


def _count_physical_rows(rows: list[Any], lifecycle: dict[str, dict[str, Any]]) -> int:
    """How many registry rows hold their stores (:data:`PHYSICAL`) — a row
    with no sidecar entry is active (the pre-lifecycle default). The JSON
    backend's half of the #359 cap; read under the same flock as the insert
    it authorizes."""
    n = 0
    for d in rows:
        if not isinstance(d, dict):
            continue
        entry = lifecycle.get(str(d.get("id", "")))
        if entry is None or entry.get("state", ACTIVE) in PHYSICAL:
            n += 1
    return n


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
        _drop_lifecycle_entry(path, cid)
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
        # Lifecycle for an inline/unset registry (no file to put a sidecar next
        # to): process-local, like the registry itself is then.
        self._inline_lifecycle: dict[str, dict[str, Any]] = {}

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

    def _records_sync(self) -> list[CollectionRecord]:
        # The file format carries no timestamps (adding them would change it), so
        # created_at/updated_at are empty here. That is a real limitation of this
        # backend, not a bug: use sqlite/postgres if you need registration times.
        # Lifecycle comes from the sidecar (or the inline dict), read under the
        # same lock as the registry so a record is never half of each.
        path = self.path
        if path:
            with json_file_lock(path):
                rows = read_json_file(path)
                lifecycle = read_lifecycle_file(path)
            specs = specs_from_rows(rows)
        else:
            specs = parse_specs(self._inline)
            lifecycle = self._inline_lifecycle
        return [
            with_lifecycle(CollectionRecord(spec=s, spec_hash=s.spec_hash()), lifecycle.get(s.id))
            for s in specs
        ]

    async def list_records(self) -> list[CollectionRecord]:
        return await asyncio.to_thread(self._records_sync)

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

    # -- lifecycle: one locked read-modify-write of the sidecar per call ---- #

    def _mutate_lifecycle_sync(self, fn: Any) -> Any:
        """Run ``fn(known_ids, lifecycle) -> (result, changed)`` under the file
        lock and persist the sidecar when ``changed``. ``known_ids`` lets a
        mutation refuse an id the registry does not hold."""
        path = self.path
        if not path:
            specs = parse_specs(self._inline)
            result, _changed = fn({s.id for s in specs}, self._inline_lifecycle)
            return result
        with json_file_lock(path):
            ids = {d.get("id") for d in read_json_file(path) if isinstance(d, dict)}
            data = read_lifecycle_file(path)
            result, changed = fn(ids, data)
            if changed:
                write_lifecycle_file(path, data)
        return result

    async def set_state(
        self, cid: str, state: str, *, expect: str | None = None, reason: str = ""
    ) -> bool:
        _check_state(state)

        def fn(ids: set[str], data: dict[str, dict[str, Any]]) -> tuple[bool, bool]:
            if cid not in ids:
                return False, False
            entry = data.setdefault(cid, _lifecycle_default())
            if expect is not None and entry.get("state", ACTIVE) != expect:
                return False, False
            _apply_state(entry, state, reason)
            return True, True

        return await asyncio.to_thread(self._mutate_lifecycle_sync, fn)

    async def append_version(self, cid: str, version: int) -> list[int]:
        def fn(ids: set[str], data: dict[str, dict[str, Any]]) -> tuple[list[int], bool]:
            if cid not in ids:
                return [], False
            entry = data.setdefault(cid, _lifecycle_default())
            return _apply_version(entry, version)

        return await asyncio.to_thread(self._mutate_lifecycle_sync, fn)

    async def set_archive_pending(self, cid: str, pending: bool) -> bool:
        def fn(ids: set[str], data: dict[str, dict[str, Any]]) -> tuple[bool, bool]:
            if cid not in ids:
                return False, False
            entry = data.setdefault(cid, _lifecycle_default())
            entry["archive_pending"] = bool(pending)
            return True, True

        return await asyncio.to_thread(self._mutate_lifecycle_sync, fn)

    async def touch_accessed(self, ids: Iterable[str], stamp: str | None = None) -> int:
        wanted = set(ids)
        at = stamp or _now()

        def fn(known: set[str], data: dict[str, dict[str, Any]]) -> tuple[int, bool]:
            n = 0
            for cid in wanted & known:
                data.setdefault(cid, _lifecycle_default())["last_accessed_at"] = at
                n += 1
            return n, n > 0

        return await asyncio.to_thread(self._mutate_lifecycle_sync, fn)

    async def next_version(self, cid: str) -> int:
        # The file format is the wire format (see CollectionSpec) and carries no
        # bookkeeping; a counter kept only in this process would hand out
        # ``versions/1/`` again after every restart. Refuse rather than collide.
        raise NotImplementedError(
            "the json collection registry cannot track archive versions; use "
            "COLLECTION_STORE_BACKEND=sqlite or postgres"
        )

    async def close(self) -> None:
        """No resources to release."""


def _lifecycle_default() -> dict[str, Any]:
    return {"state": ACTIVE, "state_reason": "", "state_changed_at": "",
            "versions": [], "archive_pending": False, "last_accessed_at": ""}


def _apply_state(entry: dict[str, Any], state: str, reason: str) -> None:
    entry["state"] = state
    entry["state_reason"] = reason or ""
    entry["state_changed_at"] = _now()


def _apply_version(entry: dict[str, Any], version: int) -> tuple[list[int], bool]:
    """Idempotent append into ``entry['versions']`` -> (list, changed)."""
    if not isinstance(version, int) or isinstance(version, bool) or version < 0:
        raise ValueError(f"version must be a non-negative integer, got {version!r}")
    versions = [int(v) for v in (entry.get("versions") or [])]
    if version in versions:
        return versions, False
    versions.append(version)
    entry["versions"] = versions
    return versions, True



class InMemoryCollectionStore:
    """Process-local registry. Loses everything on restart — dev/tests."""

    def __init__(self, specs: list[CollectionSpec] | None = None) -> None:
        self._records: dict[str, CollectionRecord] = {
            s.id: make_record(s) for s in (specs or [])
        }
        self._versions: dict[str, int] = {}
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
            rec = make_record(spec, created_at=prior.created_at if prior else "")
            # A spec upsert never resets the lifecycle (a re-put of a dormant
            # collection's spec must not make it "active" with no stores).
            self._records[spec.id] = (
                with_lifecycle(rec, lifecycle_of(prior)) if prior else rec
            )
        return True

    # -- lifecycle: the asyncio lock makes each mutation atomic ------------- #

    async def set_state(
        self, cid: str, state: str, *, expect: str | None = None, reason: str = ""
    ) -> bool:
        _check_state(state)
        async with self._lock:
            rec = self._records.get(cid)
            if rec is None or (expect is not None and rec.state != expect):
                return False
            self._records[cid] = rec.model_copy(update={
                "state": state, "state_reason": reason or "", "state_changed_at": _now(),
            })
            return True

    async def append_version(self, cid: str, version: int) -> list[int]:
        async with self._lock:
            rec = self._records.get(cid)
            if rec is None:
                return []
            entry = {"versions": list(rec.versions)}
            versions, changed = _apply_version(entry, version)
            if changed:
                self._records[cid] = rec.model_copy(update={"versions": versions})
            return list(versions)

    async def set_archive_pending(self, cid: str, pending: bool) -> bool:
        async with self._lock:
            rec = self._records.get(cid)
            if rec is None:
                return False
            self._records[cid] = rec.model_copy(update={"archive_pending": bool(pending)})
            return True

    async def touch_accessed(self, ids: Iterable[str], stamp: str | None = None) -> int:
        at = stamp or _now()
        async with self._lock:
            n = 0
            for cid in set(ids):
                rec = self._records.get(cid)
                if rec is not None:
                    self._records[cid] = rec.model_copy(update={"last_accessed_at": at})
                    n += 1
            return n

    async def create(self, spec: CollectionSpec, *, limit: int | None) -> CreateOutcome:
        # The asyncio lock is the whole mechanism: this store is process-local, so
        # "atomic" here means no other coroutine may run between the count and the
        # insert — which is exactly what an uncancelled `async with` guarantees.
        async with self._lock:
            if spec.id in self._records:
                return CreateOutcome.DUPLICATE
            if limit is not None:
                present = sum(1 for r in self._records.values() if r.state in PHYSICAL)
                if present >= limit:
                    return CreateOutcome.AT_CAP
            self._records[spec.id] = make_record(spec)
        return CreateOutcome.CREATED

    async def delete(self, cid: str) -> bool:
        async with self._lock:
            self._versions.pop(cid, None)
            return self._records.pop(cid, None) is not None

    async def next_version(self, cid: str) -> int:
        async with self._lock:
            if cid not in self._records:
                raise KeyError(cid)
            self._versions[cid] = n = self._versions.get(cid, 0) + 1
            return n

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
    "  owner TEXT NOT NULL DEFAULT '',"
    "  archive_version INTEGER NOT NULL DEFAULT 0,"
    "  state TEXT NOT NULL DEFAULT 'active',"
    "  state_reason TEXT NOT NULL DEFAULT '',"
    "  state_changed_at TEXT NOT NULL DEFAULT '',"
    "  versions TEXT NOT NULL DEFAULT '[]',"
    "  archive_pending INTEGER NOT NULL DEFAULT 0,"
    "  last_accessed_at TEXT NOT NULL DEFAULT ''"
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
    # #203/#353: the last archive version number handed out by next_version().
    # Store bookkeeping, deliberately NOT in _COLUMNS — put()/create() must never
    # rewrite (reset) it when a spec is upserted.
    "archive_version": "INTEGER NOT NULL DEFAULT 0",
    # Lifecycle (#353/#358) — additive; a table from an older build gets them
    # with their defaults, i.e. every existing collection is `active`.
    "state": "TEXT NOT NULL DEFAULT 'active'",
    "state_reason": "TEXT NOT NULL DEFAULT ''",
    "state_changed_at": "TEXT NOT NULL DEFAULT ''",
    "versions": "TEXT NOT NULL DEFAULT '[]'",
    "archive_pending": "INTEGER NOT NULL DEFAULT 0",
    "last_accessed_at": "TEXT NOT NULL DEFAULT ''",
}
#: Columns that exist for the store's own bookkeeping and are NOT part of the
#: record row (_COLUMNS): migrated in, never read or written by put()/create().
_BOOKKEEPING_COLUMNS = frozenset({"archive_version"})

_COLUMNS = (
    "id", "label", "collection", "text_index", "embedding_api", "embedding_model",
    "embedding_model_dim", "embedding_endpoints", "embedding_sidecar_url",
    "chunk_method", "chunk_size", "chunk_overlap", "chunk_params",
    "spec_hash", "created_at", "updated_at", "owner",
    *LIFECYCLE_FIELDS,
)
#: Columns a spec upsert (``put``) must NOT overwrite: the lifecycle is store
#: state that a re-put of the spec has no business resetting.
_PUT_COLUMNS = tuple(c for c in _COLUMNS if c != "id" and c not in LIFECYCLE_FIELDS)
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


#: The physically-present states as SQL parameters (stable order for tests).
_PHYSICAL_PARAMS: list[str] = sorted(PHYSICAL)
_PHYSICAL_MARKS = ", ".join("?" * len(_PHYSICAL_PARAMS))


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
        rec.state, rec.state_reason, rec.state_changed_at, json.dumps(rec.versions),
        1 if rec.archive_pending else 0, rec.last_accessed_at,
    )


def _row_to_record(row: Any) -> CollectionRecord:
    (
        rid, label, collection, text_index, api, model, dim, endpoints, sidecar,
        method, size, overlap, params, shash, created, updated, owner,
        state, state_reason, state_changed, versions, pending, accessed,
    ) = tuple(row)
    rec = CollectionRecord(
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
    return with_lifecycle(rec, {
        "state": state or ACTIVE, "state_reason": state_reason or "",
        "state_changed_at": state_changed or "", "versions": json.loads(versions or "[]"),
        "archive_pending": bool(pending), "last_accessed_at": accessed or "",
    })


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
            # Lifecycle columns are deliberately absent from the UPDATE list:
            # an existing row keeps its state/versions; a new row gets defaults.
            assignments = ", ".join(f"{c} = excluded.{c}" for c in _PUT_COLUMNS)
            conn.execute(
                f"INSERT INTO collections ({', '.join(_COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(_COLUMNS))}) "
                f"ON CONFLICT(id) DO UPDATE SET {assignments}",
                _record_to_row(rec),
            )
        return True

    # -- lifecycle ---------------------------------------------------------- #

    def _set_state_sync(self, cid: str, state: str, expect: str | None, reason: str) -> bool:
        # One UPDATE with the expectation in its WHERE clause: sqlite serialises
        # writers, so the compare and the swap are one atomic statement and the
        # rowcount says whether this caller won.
        with closing(self._connect()) as conn, conn:
            sql = ("UPDATE collections SET state = ?, state_reason = ?, "
                   "state_changed_at = ? WHERE id = ?")
            params: list[Any] = [state, reason or "", _now(), cid]
            if expect is not None:
                sql += " AND state = ?"
                params.append(expect)
            return conn.execute(sql, params).rowcount > 0

    def _append_version_sync(self, cid: str, version: int) -> list[int]:
        with closing(self._connect()) as conn:
            conn.isolation_level = None
            conn.execute("BEGIN IMMEDIATE")  # read-modify-write under the write lock
            try:
                row = conn.execute("SELECT versions FROM collections WHERE id = ?", (cid,)).fetchone()
                if row is None:
                    conn.execute("ROLLBACK")
                    return []
                entry = {"versions": json.loads(row[0] or "[]")}
                versions, changed = _apply_version(entry, version)
                if changed:
                    conn.execute("UPDATE collections SET versions = ? WHERE id = ?",
                                 (json.dumps(versions), cid))
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        return versions

    def _set_archive_pending_sync(self, cid: str, pending: bool) -> bool:
        with closing(self._connect()) as conn, conn:
            cur = conn.execute("UPDATE collections SET archive_pending = ? WHERE id = ?",
                               (1 if pending else 0, cid))
            return cur.rowcount > 0

    def _touch_sync(self, ids: list[str], stamp: str) -> int:
        if not ids:
            return 0
        with closing(self._connect()) as conn, conn:
            marks = ", ".join("?" * len(ids))
            cur = conn.execute(
                f"UPDATE collections SET last_accessed_at = ? WHERE id IN ({marks})",
                (stamp, *ids),
            )
            return cur.rowcount

    async def set_state(
        self, cid: str, state: str, *, expect: str | None = None, reason: str = ""
    ) -> bool:
        _check_state(state)
        return await asyncio.to_thread(self._set_state_sync, cid, state, expect, reason)

    async def append_version(self, cid: str, version: int) -> list[int]:
        return await asyncio.to_thread(self._append_version_sync, cid, version)

    async def set_archive_pending(self, cid: str, pending: bool) -> bool:
        return await asyncio.to_thread(self._set_archive_pending_sync, cid, pending)

    async def touch_accessed(self, ids: Iterable[str], stamp: str | None = None) -> int:
        return await asyncio.to_thread(self._touch_sync, sorted(set(ids)), stamp or _now())

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
                    n = conn.execute(
                        f"SELECT COUNT(*) FROM collections WHERE state IN ({_PHYSICAL_MARKS})",
                        _PHYSICAL_PARAMS,
                    ).fetchone()[0]
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

    def _next_version_sync(self, cid: str) -> int:
        # One UPDATE … RETURNING: the increment and the read are a single
        # statement under sqlite's write lock, so concurrent callers serialize.
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                "UPDATE collections SET archive_version = archive_version + 1 "
                "WHERE id = ? RETURNING archive_version",
                (cid,),
            ).fetchone()
        if row is None:
            raise KeyError(cid)
        return int(row[0])

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

    async def next_version(self, cid: str) -> int:
        return await asyncio.to_thread(self._next_version_sync, cid)

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
        # Lifecycle columns stay out of the UPDATE list (see the sqlite twin).
        assignments = ", ".join(f"{c} = excluded.{c}" for c in _PUT_COLUMNS)
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
                n = await conn.fetchval(
                    "SELECT COUNT(*) FROM collections WHERE state = ANY($1::text[])",
                    _PHYSICAL_PARAMS,
                )
                if n >= limit:
                    return CreateOutcome.AT_CAP
            await conn.execute(_INSERT_POSTGRES, *_record_to_row(make_record(spec)))
        return CreateOutcome.CREATED

    async def delete(self, cid: str) -> bool:
        pool = await self._pool_()
        async with pool.acquire() as conn:
            status = await conn.execute("DELETE FROM collections WHERE id = $1", cid)
        return not status.endswith(" 0")

    # -- lifecycle ---------------------------------------------------------- #

    @staticmethod
    def _rows(status: str) -> int:
        """``"UPDATE 3"`` -> 3 (asyncpg's command-tag status string)."""
        try:
            return int(status.rsplit(" ", 1)[-1])
        except ValueError:  # pragma: no cover - defensive
            return 0

    async def set_state(
        self, cid: str, state: str, *, expect: str | None = None, reason: str = ""
    ) -> bool:
        _check_state(state)
        pool = await self._pool_()
        async with pool.acquire() as conn:
            if expect is None:
                status = await conn.execute(
                    "UPDATE collections SET state = $1, state_reason = $2, "
                    "state_changed_at = $3 WHERE id = $4",
                    state, reason or "", _now(), cid,
                )
            else:
                # The compare is in the WHERE clause: a single row-locked UPDATE,
                # so two concurrent CASes from different processes cannot both
                # see `expect` and both succeed.
                status = await conn.execute(
                    "UPDATE collections SET state = $1, state_reason = $2, "
                    "state_changed_at = $3 WHERE id = $4 AND state = $5",
                    state, reason or "", _now(), cid, expect,
                )
        return self._rows(status) > 0

    async def append_version(self, cid: str, version: int) -> list[int]:
        pool = await self._pool_()
        async with pool.acquire() as conn, conn.transaction():
            raw = await conn.fetchval(
                "SELECT versions FROM collections WHERE id = $1 FOR UPDATE", cid
            )
            if raw is None:
                return []
            entry = {"versions": json.loads(raw or "[]")}
            versions, changed = _apply_version(entry, version)
            if changed:
                await conn.execute("UPDATE collections SET versions = $1 WHERE id = $2",
                                   json.dumps(versions), cid)
        return versions

    async def set_archive_pending(self, cid: str, pending: bool) -> bool:
        pool = await self._pool_()
        async with pool.acquire() as conn:
            status = await conn.execute(
                "UPDATE collections SET archive_pending = $1 WHERE id = $2",
                1 if pending else 0, cid,
            )
        return self._rows(status) > 0

    async def touch_accessed(self, ids: Iterable[str], stamp: str | None = None) -> int:
        wanted = sorted(set(ids))
        if not wanted:
            return 0
        pool = await self._pool_()
        async with pool.acquire() as conn:
            status = await conn.execute(
                "UPDATE collections SET last_accessed_at = $1 WHERE id = ANY($2::text[])",
                stamp or _now(), wanted,
            )
        return self._rows(status)

    async def next_version(self, cid: str) -> int:
        # A single UPDATE … RETURNING is atomic per row under MVCC: concurrent
        # callers queue on the row lock and each sees the other's increment.
        pool = await self._pool_()
        async with pool.acquire() as conn:
            n = await conn.fetchval(
                "UPDATE collections SET archive_version = archive_version + 1 "
                "WHERE id = $1 RETURNING archive_version",
                cid,
            )
        if n is None:
            raise KeyError(cid)
        return int(n)

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
