"""Collection lifecycle state on the registry (#358, phase 2 of #353).

Every backend that can run locally (memory / json sidecar / sqlite) is exercised
through one parametrised fixture; postgres joins in through ``pg_test_dsn`` and
skips unless ``RAGSTACK_TEST_PG_DSN`` names a scratch server. The properties
pinned here are the ones the API's dormant path and the eviction policy (#359)
rest on:

* transitions are recorded with a reason and a change stamp, and a spec upsert
  never resets them;
* ``dormant -> restoring`` is a compare-and-swap: 20 concurrent tasks yield
  exactly ONE winner, on every backend;
* ``archive_pending`` (and an empty version list) refuse eviction;
* ``last_accessed_at`` is batched — N touches through the tracker are ONE store
  write, and the shutdown flush writes what is still dirty.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio

from ragstack.collection_store import (
    ACTIVE,
    ARCHIVING,
    DORMANT,
    LOST,
    PHYSICAL,
    RESTORING,
    STATES,
    AccessTracker,
    CollectionSpec,
    CreateOutcome,
    InMemoryCollectionStore,
    JsonFileCollectionStore,
    PostgresCollectionStore,
    SqliteCollectionStore,
    evictable,
    lifecycle_path,
)

pytestmark = pytest.mark.asyncio

BACKENDS = ["memory", "json", "sqlite", "postgres"]


def _spec(cid: str = "acme", **over) -> CollectionSpec:
    base: dict = {
        "id": cid, "label": cid, "owner": "bvbrc:alice@patricbrc.org",
        "collection": f"ragstack_lib_{cid}", "embedding_api": "openai",
        "embedding_model": "Salesforce/SFR-Embedding-Mistral", "embedding_model_dim": 4096,
        "chunk_method": "fixed_token", "chunk_size": 256, "chunk_overlap": 32,
    }
    base.update(over)
    return CollectionSpec(**base)


@pytest.fixture(params=BACKENDS)
def _backend(request, tmp_path):
    """Build one registry per backend. Sync on purpose: pulling the (async)
    ``pg_test_dsn`` fixture via ``getfixturevalue`` only works from outside the
    running loop. The postgres row skips unless ``RAGSTACK_TEST_PG_DSN`` is set."""
    if request.param == "memory":
        return InMemoryCollectionStore()
    if request.param == "json":
        return JsonFileCollectionStore(SimpleNamespace(
            collections_file=str(tmp_path / "collections.json"), collections_json=""))
    if request.param == "sqlite":
        return SqliteCollectionStore(str(tmp_path / "registry.db"))
    return PostgresCollectionStore(request.getfixturevalue("pg_test_dsn"))


@pytest_asyncio.fixture
async def store(_backend):
    """The registry, seeded with collection ``acme``; closed afterwards."""
    assert await _backend.create(_spec(), limit=None) is CreateOutcome.CREATED
    try:
        yield _backend
    finally:
        await _backend.close()


# --------------------------------------------------------------------------- #
# defaults + transitions
# --------------------------------------------------------------------------- #


async def test_the_cap_counts_physically_present_rows(store):
    """#359: ``create(limit=n)`` refuses at ``n`` rows that hold their stores
    (``PHYSICAL``: active, archiving, restoring); a dormant or lost row
    (nothing on the stores) holds no slot and frees one — inside the same
    atomic section as the insert, on every backend."""
    assert PHYSICAL == {ACTIVE, ARCHIVING, RESTORING}
    assert await store.create(_spec("two"), limit=2) is CreateOutcome.CREATED
    assert await store.create(_spec("three"), limit=2) is CreateOutcome.AT_CAP
    assert await store.set_state("acme", DORMANT, expect=ACTIVE, reason="evicted") is True
    assert await store.create(_spec("three"), limit=2) is CreateOutcome.CREATED
    assert await store.create(_spec("four"), limit=2) is CreateOutcome.AT_CAP
    # archiving/restoring rows still hold (or are rebuilding) their stores:
    # they keep counting, even though neither is evictable.
    for st in (ARCHIVING, RESTORING):
        assert await store.set_state("two", st) is True
        assert await store.create(_spec("four"), limit=2) is CreateOutcome.AT_CAP
    # lost holds nothing.
    assert await store.set_state("two", LOST) is True
    assert await store.create(_spec("four"), limit=2) is CreateOutcome.CREATED
    assert {r.spec.id: r.state for r in await store.list_records()} == {
        "acme": DORMANT, "two": LOST, "three": ACTIVE, "four": ACTIVE,
    }


async def test_a_new_collection_is_active_with_no_versions(store):
    rec = await store.get("acme")
    assert rec is not None
    assert rec.state == ACTIVE
    assert rec.versions == [] and rec.archive_pending is False
    assert rec.last_accessed_at == "" and rec.state_reason == ""


async def test_set_state_records_reason_and_stamp(store):
    assert await store.set_state("acme", DORMANT, reason="evicted: idle 90d") is True
    rec = await store.get("acme")
    assert rec.state == DORMANT
    assert rec.state_reason == "evicted: idle 90d"
    assert rec.state_changed_at.endswith("+00:00")
    # Every state in the vocabulary round-trips through the backend.
    for st in sorted(STATES):
        assert await store.set_state("acme", st) is True
        assert (await store.get("acme")).state == st
    # Unknown id -> False, unknown state -> refused before any write.
    assert await store.set_state("nope", DORMANT) is False
    with pytest.raises(ValueError):
        await store.set_state("acme", "asleep")


async def test_cas_only_moves_from_the_expected_state(store):
    await store.set_state("acme", DORMANT)
    assert await store.set_state("acme", RESTORING, expect=ACTIVE) is False
    assert (await store.get("acme")).state == DORMANT
    assert await store.set_state("acme", RESTORING, expect=DORMANT) is True
    assert (await store.get("acme")).state == RESTORING
    # The watcher's own CAS: restoring -> active, and a second one is a no-op.
    assert await store.set_state("acme", ACTIVE, expect=RESTORING) is True
    assert await store.set_state("acme", ACTIVE, expect=RESTORING) is False


async def test_twenty_concurrent_cas_yield_exactly_one_winner(store):
    """The dormant-collection path: N requests race ``dormant -> restoring``
    and exactly one may submit the restore."""
    await store.set_state("acme", DORMANT)
    results = await asyncio.gather(*(
        store.set_state("acme", RESTORING, expect=DORMANT, reason=f"req-{i}")
        for i in range(20)
    ))
    assert results.count(True) == 1, results
    assert (await store.get("acme")).state == RESTORING


async def test_a_spec_upsert_preserves_the_lifecycle(store):
    await store.set_state("acme", DORMANT, reason="evicted")
    await store.append_version("acme", 1)
    await store.set_archive_pending("acme", True)
    assert await store.put(_spec(label="renamed")) is True
    rec = await store.get("acme")
    assert rec.spec.label == "renamed"
    assert rec.state == DORMANT and rec.state_reason == "evicted"
    assert rec.versions == [1] and rec.archive_pending is True


async def test_delete_then_recreate_starts_active(store):
    """The id namespace is reusable: a stale ``dormant`` inherited by the next
    collection under the same id would 503 it on first access."""
    await store.set_state("acme", LOST, reason="gone")
    await store.append_version("acme", 3)
    assert await store.delete("acme") is True
    assert await store.create(_spec(), limit=None) is CreateOutcome.CREATED
    rec = await store.get("acme")
    assert rec.state == ACTIVE and rec.versions == [] and rec.state_reason == ""


# --------------------------------------------------------------------------- #
# versions
# --------------------------------------------------------------------------- #


async def test_versions_list_is_ordered_and_idempotent(store):
    """``versions`` is the ordered list restore replays; ``append_version``
    records a number the #375 counter handed out once its archive exists."""
    assert await store.append_version("acme", 1) == [1]
    assert await store.append_version("acme", 3) == [1, 3]
    assert await store.append_version("acme", 3) == [1, 3]  # idempotent
    assert (await store.get("acme")).versions == [1, 3]
    assert await store.append_version("nope", 1) == []
    with pytest.raises(ValueError):
        await store.append_version("acme", -1)


async def test_counter_and_list_agree(store):
    """#375's allocating ``next_version`` (the ``versions/<n>/`` number a GoWe
    job archives into) and the ``versions`` list the restore path replays are
    reconciled by the ingest path: reserve, then record on delivery. The json
    backend cannot persist a counter and says so."""
    if isinstance(store, JsonFileCollectionStore):
        with pytest.raises(NotImplementedError):
            await store.next_version("acme")
        return
    assert await store.next_version("acme") == 1
    assert await store.next_version("acme") == 2  # reserved, never recorded (a failed run)
    n = await store.next_version("acme")
    assert n == 3
    assert await store.append_version("acme", n) == [3]
    with pytest.raises(KeyError):
        await store.next_version("nope")


async def test_concurrent_appends_lose_nothing(store):
    await asyncio.gather(*(store.append_version("acme", n) for n in range(1, 11)))
    assert sorted((await store.get("acme")).versions) == list(range(1, 11))


# --------------------------------------------------------------------------- #
# eviction predicate (#359 consumes it)
# --------------------------------------------------------------------------- #


async def test_evictable_needs_active_current_archive(store):
    rec = await store.get("acme")
    assert evictable(rec) is False  # no archive version yet: nothing to restore from
    await store.append_version("acme", 1)
    assert evictable(await store.get("acme")) is True
    assert await store.set_archive_pending("acme", True) is True
    assert evictable(await store.get("acme")) is False  # last load unarchived
    await store.set_archive_pending("acme", False)
    assert evictable(await store.get("acme")) is True
    for st in (ARCHIVING, DORMANT, RESTORING, LOST):
        await store.set_state("acme", st)
        assert evictable(await store.get("acme")) is False
    assert await store.set_archive_pending("nope", True) is False


# --------------------------------------------------------------------------- #
# last_accessed_at batching
# --------------------------------------------------------------------------- #


class _CountingStore:
    """Wrap a real store, counting ``touch_accessed`` calls (and optionally
    failing them) — the batching test asserts on WRITES, not touches."""

    def __init__(self, inner, fail: int = 0) -> None:
        self.inner = inner
        self.calls: list[list[str]] = []
        self.fail = fail

    async def touch_accessed(self, ids, stamp=None):
        self.calls.append(list(ids))
        if self.fail:
            self.fail -= 1
            raise RuntimeError("store down")
        return await self.inner.touch_accessed(ids, stamp)


async def test_touch_accessed_stamps_every_listed_id_in_one_write(store):
    await store.create(_spec("beta"), limit=None)
    n = await store.touch_accessed(["acme", "beta", "ghost"], "2026-08-24T00:00:00+00:00")
    assert n == 2
    for cid in ("acme", "beta"):
        assert (await store.get(cid)).last_accessed_at == "2026-08-24T00:00:00+00:00"
    assert await store.touch_accessed([]) == 0


async def test_tracker_batches_n_touches_into_one_write(store):
    counting = _CountingStore(store)
    tracker = AccessTracker(counting, flush_seconds=3600)
    for i in range(500):
        tracker.touch("acme")
        tracker.touch("beta" if i % 2 else "acme")
    assert tracker.touched == 1000 and tracker.pending == 2
    assert counting.calls == []  # nothing written per touch
    assert await tracker.flush() == 2
    assert len(counting.calls) == 1 and counting.calls[0] == ["acme", "beta"]
    assert tracker.writes == 1
    assert (await store.get("acme")).last_accessed_at != ""
    assert await tracker.flush() == 0 and len(counting.calls) == 1  # nothing dirty


async def test_tracker_stop_flushes_what_is_dirty(store):
    counting = _CountingStore(store)
    tracker = AccessTracker(counting, flush_seconds=3600)
    tracker.start()
    tracker.touch("acme")
    await tracker.stop()  # shutdown: the periodic loop never fired, stop() writes
    assert len(counting.calls) == 1 and tracker.pending == 0
    assert (await store.get("acme")).last_accessed_at != ""


async def test_tracker_periodic_flush_and_failed_write_is_retried(store):
    counting = _CountingStore(store, fail=1)
    tracker = AccessTracker(counting, flush_seconds=0.05)
    tracker.start()
    tracker.touch("acme")
    await asyncio.sleep(0.3)  # several periods: the first write fails, a later one lands
    await tracker.stop()
    assert len(counting.calls) >= 2
    assert tracker.writes == 1
    assert (await store.get("acme")).last_accessed_at != ""


# --------------------------------------------------------------------------- #
# backend specifics
# --------------------------------------------------------------------------- #


async def test_json_backend_keeps_the_registry_file_format_unchanged(tmp_path):
    """The lifecycle lives in a sidecar; the shipped file is byte-identical to
    what the pre-lifecycle code wrote, so a hand-authored registry stays valid."""
    path = tmp_path / "collections.json"
    s = JsonFileCollectionStore(SimpleNamespace(collections_file=str(path), collections_json=""))
    await s.create(_spec(), limit=None)
    before = path.read_text()
    await s.set_state("acme", DORMANT, reason="evicted")
    await s.append_version("acme", 2)
    assert path.read_text() == before
    side = json.loads((tmp_path / lifecycle_path("collections.json")).read_text())
    assert side["acme"]["state"] == DORMANT and side["acme"]["versions"] == [2]
    # A second store over the same file sees the state (cross-process durability).
    other = JsonFileCollectionStore(SimpleNamespace(collections_file=str(path), collections_json=""))
    assert (await other.get("acme")).state == DORMANT
    # A corrupt sidecar degrades to "active", never to an unreadable registry.
    (tmp_path / lifecycle_path("collections.json")).write_text("{not json")
    assert (await other.get("acme")).state == ACTIVE


async def test_json_inline_registry_tracks_lifecycle_in_process(tmp_path):
    inline = json.dumps([_spec().model_dump()])
    s = JsonFileCollectionStore(SimpleNamespace(collections_file="", collections_json=inline))
    assert await s.set_state("acme", DORMANT) is True
    assert (await s.get("acme")).state == DORMANT
    assert await s.set_state("ghost", DORMANT) is False


async def test_sqlite_migration_adds_lifecycle_columns_to_an_older_table(tmp_path):
    """A table created by a pre-#358 build has no lifecycle columns; opening it
    with this build adds them (additive-only) and every row reads as active."""
    import sqlite3

    db = tmp_path / "old.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE collections (id TEXT PRIMARY KEY, label TEXT NOT NULL DEFAULT '', "
            "collection TEXT NOT NULL, text_index TEXT NOT NULL DEFAULT '', "
            "embedding_api TEXT NOT NULL DEFAULT 'openai', embedding_model TEXT NOT NULL DEFAULT '', "
            "embedding_model_dim INTEGER NOT NULL DEFAULT 0, embedding_endpoints TEXT NOT NULL DEFAULT '[]', "
            "embedding_sidecar_url TEXT NOT NULL DEFAULT '', chunk_method TEXT NOT NULL DEFAULT '', "
            "chunk_size INTEGER, chunk_overlap INTEGER, chunk_params TEXT NOT NULL DEFAULT '{}', "
            "spec_hash TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT '', "
            "updated_at TEXT NOT NULL DEFAULT '', owner TEXT NOT NULL DEFAULT '')"
        )
        conn.execute(
            "INSERT INTO collections (id, collection, embedding_model_dim) VALUES ('old', 'ragstack_old', 8)"
        )
    s = SqliteCollectionStore(str(db))
    rec = await s.get("old")
    assert rec is not None and rec.state == ACTIVE and rec.versions == []
    assert await s.set_state("old", DORMANT, expect=ACTIVE) is True
    assert (await s.get("old")).state == DORMANT
