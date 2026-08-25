"""Eviction over HTTP (#359, phase 4 of #353).

At the active bound, ``POST /v1/collections`` evicts EXACTLY ONE least-
recently-accessed archived collection — its registry row is swapped
``active → dormant`` BEFORE its stores are dropped (asserted on the fakes'
shared log) — and succeeds; with no evictable candidate it answers 507 naming
the per-reason counts. ``POST /v1/admin/collections/evict`` plans (dry run)
and acts. A reader of the victim gets 503 + Retry-After from the lifecycle
gate the instant the swap lands.

The app's registry, store and stores are the in-process doubles from
``conftest.py`` plus what each test adds; nothing physical is touched.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jsonschema
import pytest
import pytest_asyncio

from ragstack.api import security
from ragstack.api.collections import CollectionEntry
from ragstack.api.lifecycle import LifecycleGate, reset_lifecycle_gate, set_lifecycle_gate
from ragstack.api.main import app
from ragstack.collection_store import (
    ACTIVE,
    DORMANT,
    AccessTracker,
    CollectionSpec,
    InMemoryCollectionStore,
)
from ragstack.config import settings
from ragstack.jobstore import RUNNING
from ragstack.retrieval.retriever import HybridRetriever
from ragstack.stores import InMemoryTextIndex, InMemoryVectorStore

pytestmark = pytest.mark.asyncio

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[3] / "contracts" / "schemas" / "eviction_response.json")
    .read_text()
)


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #


class _FakeEmbedder:
    async def embed(self, texts):
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


class _LoggingVectorStore(InMemoryVectorStore):
    def __init__(self, log: list, name: str) -> None:
        super().__init__()
        self._log, self._name = log, name

    async def drop_collection(self) -> bool:
        self._log.append(("drop_vectors", self._name))
        return await super().drop_collection()


class _LoggingTextIndex(InMemoryTextIndex):
    def __init__(self, log: list, name: str) -> None:
        super().__init__()
        self._log, self._name = log, name

    async def drop_index(self) -> bool:
        self._log.append(("drop_text", self._name))
        return await super().drop_index()


class _RecordingStore(InMemoryCollectionStore):
    def __init__(self, log: list) -> None:
        super().__init__()
        self._log = log

    async def set_state(self, cid, state, *, expect=None, reason=""):
        won = await super().set_state(cid, state, expect=expect, reason=reason)
        self._log.append(("set_state", cid, state, won))
        return won


def _ago(**kw) -> str:
    return (datetime.now(UTC) - timedelta(**kw)).isoformat()


class World:
    """N archived, active collections over logging doubles, LRU order
    ``lib-0`` (oldest) … ``lib-{n-1}`` (newest)."""

    def __init__(self, log: list, store: _RecordingStore) -> None:
        self.log, self.store, self.ids = log, store, []

    async def add(self, cid: str, *, accessed: str, versions=(1,), archive_pending=False) -> None:
        spec = CollectionSpec(
            id=cid, label=cid, collection=f"ragstack_lib_{cid}", embedding_api="openai",
            embedding_model="test-model", embedding_model_dim=4, chunk_method="fixed",
        )
        await self.store.put(spec)
        for v in versions:
            await self.store.append_version(cid, v)
        await self.store.set_archive_pending(cid, archive_pending)
        await self.store.touch_accessed([cid], accessed)
        vs, ti = _LoggingVectorStore(self.log, cid), _LoggingTextIndex(self.log, cid)
        app.state.collections.add(CollectionEntry(
            id=cid, label=cid, collection=f"ragstack_lib_{cid}", model="test-model", dim=4,
            chunk_method="fixed", chunk_size=None, chunk_overlap=None, chunk_params={},
            is_shared_surface=False, retriever=HybridRetriever(vs, ti, _FakeEmbedder()),
            vector_store=vs, text_index=ti, embedder=_FakeEmbedder(),
        ))
        self.ids.append(cid)

    async def states(self) -> dict[str, str]:
        return {r.spec.id: r.state for r in await self.store.list_records()}


@pytest_asyncio.fixture
async def world(client, monkeypatch):
    monkeypatch.setattr(security.settings, "tenant_collections", {})
    log: list = []
    store = _RecordingStore(log)
    prior = getattr(app.state, "collection_store", None)
    app.state.collection_store = store
    w = World(log, store)
    for i in range(3):
        await w.add(f"lib-{i}", accessed=_ago(days=30 - 10 * i))
    tracker = AccessTracker(store, flush_seconds=3600)
    gate = LifecycleGate(store, tracker=tracker, cache_seconds=5.0, retry_after=30)
    set_lifecycle_gate(gate)
    w.gate = gate  # type: ignore[attr-defined]
    try:
        yield w
    finally:
        await gate.drain()
        reset_lifecycle_gate()
        for cid in list(w.ids) + ["new-one", "new-two", "legacy"]:
            app.state.collections.remove(cid)
        if prior is None:
            del app.state.collection_store  # other modules rely on the JSON fallback
        else:
            app.state.collection_store = prior


def _at_bound(monkeypatch, world: World) -> None:
    """max_collections = the durable rows + the shared-surface pointer's slot:
    every slot is taken, the next create must evict."""
    monkeypatch.setattr(settings, "max_collections", len(world.ids) + 1)


async def _as_admin(monkeypatch) -> None:
    monkeypatch.setattr(security.settings, "default_role", "admin")


# --------------------------------------------------------------------------- #
# POST /v1/collections at the bound
# --------------------------------------------------------------------------- #


async def test_create_at_the_bound_evicts_exactly_one_lru_collection_and_succeeds(
    client, monkeypatch, world,
):
    _at_bound(monkeypatch, world)
    world.log.clear()

    r = await client.post("/v1/collections", json={"id": "new-one"})
    assert r.status_code == 201, r.text
    assert r.json()["state"] == ACTIVE

    # Exactly one victim, the least recently accessed; the rest untouched.
    assert await world.states() == {
        "lib-0": DORMANT, "lib-1": ACTIVE, "lib-2": ACTIVE, "new-one": ACTIVE,
    }
    row = await world.store.get("lib-0")
    assert row.state_reason.startswith("evicted (LRU; last accessed ")
    # THE ORDER: the row is dormant BEFORE either store is dropped.
    assert world.log == [
        ("set_state", "lib-0", DORMANT, True),
        ("drop_vectors", "lib-0"),
        ("drop_text", "lib-0"),
    ]
    # The victim stays listed — dormant, with its versions — and restorable.
    listed = {c["id"]: c for c in (await client.get("/v1/collections")).json()["collections"]}
    assert listed["lib-0"]["state"] == DORMANT and listed["lib-0"]["versions"] == [1]
    assert "new-one" in listed


async def test_a_reader_of_the_victim_gets_503_retry_after_after_eviction(
    client, monkeypatch, world,
):
    """The lifecycle gate had `lib-0` cached as active; eviction invalidates
    it, so the very next read sees dormant."""
    ok = await client.post("/v1/retrieve", json={"query": "x", "collection": "lib-0"})
    assert ok.status_code == 200, ok.text
    assert world.gate._cache["lib-0"][1].state == ACTIVE  # cached as active
    # That read made lib-0 the most recently used; re-age it so it is the
    # victim again while the gate's cache still says active.
    await world.gate.tracker.flush()
    await world.store.touch_accessed(["lib-0"], _ago(days=30))
    _at_bound(monkeypatch, world)
    assert (await client.post("/v1/collections", json={"id": "new-one"})).status_code == 201

    gone = await client.post("/v1/retrieve", json={"query": "x", "collection": "lib-0"})
    assert gone.status_code == 503, gone.text
    assert gone.headers["Retry-After"] == "30"
    assert "is dormant" in gone.json()["detail"]
    # The survivors serve.
    assert (await client.post("/v1/retrieve", json={"query": "x", "collection": "lib-1"})).status_code == 200


async def test_lru_uses_the_flushed_access_stamps(client, monkeypatch, world):
    """A read of the oldest collection just before the create makes it the
    most recently used — the tracker's in-process touches are flushed before
    selection, so the victim is the next one, not the one just read."""
    assert (await client.post("/v1/retrieve", json={"query": "x", "collection": "lib-0"})).status_code == 200
    assert world.gate.tracker.pending == 1
    _at_bound(monkeypatch, world)
    assert (await client.post("/v1/collections", json={"id": "new-one"})).status_code == 201
    states = await world.states()
    assert states["lib-0"] == ACTIVE and states["lib-1"] == DORMANT


async def test_create_is_507_when_nothing_is_evictable(client, monkeypatch, world):
    await world.add("pending", accessed=_ago(days=100), archive_pending=True)
    await world.add("unarchived", accessed=_ago(days=90), versions=())
    await world.add("loading", accessed=_ago(days=80))
    job = await app.state.job_store.create("ws:///x", tenant_id="default", collection_id="loading")
    await app.state.job_store.update(job.job_id, status=RUNNING)
    # Every archived one is dormant already.
    for cid in ("lib-0", "lib-1", "lib-2"):
        await world.store.set_state(cid, DORMANT)
    # bound = the three ACTIVE rows + the pointer's slot: full, nothing to evict.
    monkeypatch.setattr(settings, "max_collections", 3 + 1)
    world.log.clear()

    r = await client.post("/v1/collections", json={"id": "new-one"})
    assert r.status_code == 507, r.text
    detail = r.json()["detail"]
    assert detail.startswith("active collection bound reached (4)")
    assert "needed 1, found 0" in detail
    for fragment in ("not_active=3", "archive_pending=1", "no_archive=1", "in_flight=1",
                     "protected=0", "unregistered=0"):
        assert fragment in detail, detail
    assert world.log == []  # nothing was swapped or dropped
    assert (await world.states())["loading"] == ACTIVE


async def test_the_legacy_shared_surface_is_never_evicted_for_a_create(
    client, monkeypatch, world,
):
    """The registry's `default` entry is the settings-derived shared surface.
    A durable row over its stores — the `claimed_by` shape — is the oldest
    by far and its archive is current; it must still be skipped, and with
    nothing else evictable the create is 507 with `protected=1`."""
    derived = app.state.collections.resolve("default")
    legacy = CollectionSpec(
        id="legacy", collection=derived.collection, text_index=derived.es_index(),
        embedding_api="openai", embedding_model="test-model", embedding_model_dim=4,
        chunk_method="fixed",
    )
    await world.store.put(legacy)
    await world.store.append_version("legacy", 1)
    await world.store.touch_accessed(["legacy"], _ago(days=900))
    # Served by this process over the SAME store objects as `default`.
    app.state.collections.add(CollectionEntry(
        id="legacy", label="legacy", collection=derived.collection, model="test-model", dim=4,
        chunk_method="fixed", chunk_size=None, chunk_overlap=None, chunk_params={},
        is_shared_surface=False, retriever=derived.retriever,
        vector_store=derived.vector_store, text_index=derived.text_index,
        text_index_name=derived.es_index(),
    ))
    for cid in ("lib-0", "lib-1", "lib-2"):
        await world.store.set_state(cid, DORMANT)
    monkeypatch.setattr(settings, "max_collections", 1 + 1)
    r = await client.post("/v1/collections", json={"id": "new-one"})
    assert r.status_code == 507, r.text
    assert "protected=1" in r.json()["detail"] and "not_active=3" in r.json()["detail"]
    assert (await world.store.get("legacy")).state == ACTIVE
    await world.store.delete("legacy")


async def test_a_dormant_collection_does_not_count_toward_the_bound(client, monkeypatch, world):
    await world.store.set_state("lib-2", DORMANT)
    monkeypatch.setattr(settings, "max_collections", 3 + 1)  # 3 rows, 2 active: room for one
    assert (await client.post("/v1/collections", json={"id": "new-one"})).status_code == 201
    states = await world.states()
    assert states["lib-0"] == ACTIVE and states["lib-1"] == ACTIVE  # no eviction was needed
    # ...and now the bound is met again: the next create evicts lib-0.
    assert (await client.post("/v1/collections", json={"id": "new-two"})).status_code == 201
    assert (await world.states())["lib-0"] == DORMANT


async def test_a_concurrent_create_taking_the_freed_slot_is_507_not_a_second_eviction(
    client, monkeypatch, world,
):
    """One eviction, one retry, never a loop: when a sibling creator lands in
    the freed slot, the loser is told so rather than evicting again."""
    _at_bound(monkeypatch, world)
    original = world.store.create
    calls = 0
    thief = CollectionSpec(
        id="thief", collection="ragstack_lib_thief", embedding_api="openai",
        embedding_model="test-model", embedding_model_dim=4, chunk_method="fixed",
    )

    async def create_with_a_thief(spec, *, limit):
        nonlocal calls
        calls += 1
        if calls == 2:
            # The retry after OUR eviction: a sibling's create lands first.
            assert (await world.store.get("lib-0")).state == DORMANT
            await original(thief, limit=None)
        return await original(spec, limit=limit)

    world.store.create = create_with_a_thief  # type: ignore[method-assign]
    r = await client.post("/v1/collections", json={"id": "new-one"})
    assert r.status_code == 507, r.text
    assert "concurrent create took the slot" in r.json()["detail"]
    states = await world.states()
    assert states["lib-0"] == DORMANT and states["lib-1"] == ACTIVE  # exactly one eviction
    assert "thief" in states
    await world.store.delete("thief")


# --------------------------------------------------------------------------- #
# POST /v1/admin/collections/evict
# --------------------------------------------------------------------------- #


async def test_admin_evict_dry_run_plans_without_acting(client, monkeypatch, world):
    await _as_admin(monkeypatch)
    world.log.clear()
    r = await client.post("/v1/admin/collections/evict?need=2&dry_run=true")
    assert r.status_code == 200, r.text
    body = r.json()
    jsonschema.validate(instance=body, schema=SCHEMA)
    assert body["need"] == 2 and body["dry_run"] is True and body["evicted"] == 0
    assert [v["collection_id"] for v in body["victims"]] == ["lib-0", "lib-1"]
    assert body["victims"][0]["idle_seconds"] > body["victims"][1]["idle_seconds"] > 0
    assert all(v["state"] == ACTIVE and v["ok"] and v["deleted"] == [] for v in body["victims"])
    assert body["shortfall"] == {
        "needed": 2, "found": 2,
        "reasons": {"not_active": 0, "archive_pending": 0, "no_archive": 0,
                    "in_flight": 0, "protected": 0, "unregistered": 0},
    }
    assert world.log == []
    assert set((await world.states()).values()) == {ACTIVE}


async def test_admin_evict_acts_and_reports_the_shortfall(client, monkeypatch, world):
    await _as_admin(monkeypatch)
    await world.add("pending", accessed=_ago(days=100), archive_pending=True)
    world.log.clear()
    r = await client.post("/v1/admin/collections/evict?need=5")
    assert r.status_code == 200, r.text
    body = r.json()
    jsonschema.validate(instance=body, schema=SCHEMA)
    assert body["evicted"] == 3 and body["dry_run"] is False
    assert [v["collection_id"] for v in body["victims"]] == ["lib-0", "lib-1", "lib-2"]
    for v in body["victims"]:
        assert v["state"] == DORMANT and v["ok"] and v["failed"] == []
        assert v["deleted"] == [] and set(v["absent"]) == {"vectors", "text_index"}  # empty doubles
        assert v["reason"].startswith("evicted (LRU")
    assert body["shortfall"]["needed"] == 5 and body["shortfall"]["found"] == 3
    assert body["shortfall"]["reasons"]["archive_pending"] == 1
    # Each victim: swap first, then the two drops.
    assert world.log == [
        ("set_state", "lib-0", DORMANT, True), ("drop_vectors", "lib-0"), ("drop_text", "lib-0"),
        ("set_state", "lib-1", DORMANT, True), ("drop_vectors", "lib-1"), ("drop_text", "lib-1"),
        ("set_state", "lib-2", DORMANT, True), ("drop_vectors", "lib-2"), ("drop_text", "lib-2"),
    ]
    assert (await world.states())["pending"] == ACTIVE


async def test_admin_evict_reports_a_failed_drop_and_keeps_the_row_dormant(
    client, monkeypatch, world,
):
    await _as_admin(monkeypatch)

    class _Broken(InMemoryTextIndex):
        async def drop_index(self) -> bool:
            raise RuntimeError("index_not_found_exception")

    app.state.collections.resolve("lib-0").text_index = _Broken()
    r = await client.post("/v1/admin/collections/evict?need=1")
    assert r.status_code == 200, r.text
    body = r.json()
    jsonschema.validate(instance=body, schema=SCHEMA)
    [v] = body["victims"]
    assert v["state"] == DORMANT and v["ok"] is False and body["evicted"] == 1
    assert v["failed"] == [{"target": "text_index", "error": "RuntimeError: index_not_found_exception"}]
    assert "text_index 'ragstack_lib_lib-0' still present" in v["reason"]
    assert (await world.store.get("lib-0")).state_reason == v["reason"]


async def test_admin_evict_is_admin_only_and_validates_need(client, monkeypatch, world):
    r = await client.post("/v1/admin/collections/evict?need=1")
    assert r.status_code == 403
    await _as_admin(monkeypatch)
    assert (await client.post("/v1/admin/collections/evict?need=0")).status_code == 422
    assert (await client.post("/v1/admin/collections/evict?need=1001")).status_code == 422
    assert set((await world.states()).values()) == {ACTIVE}
