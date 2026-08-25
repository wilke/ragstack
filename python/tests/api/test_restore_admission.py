"""Restore admission at the active bound (#381).

A restore rebuilds a collection's physical stores, so it takes a slot against
``max_collections`` exactly as a create does. At the bound, the first access
to a ``dormant`` collection evicts ONE least-recently-accessed archived
collection — its row is swapped ``active → dormant`` BEFORE its stores are
dropped (asserted on the fakes' shared log) — and submits exactly one restore;
with nothing evictable it answers **503 + Retry-After** naming the capacity
reason, submits nothing and leaves the row ``dormant``. The explicit
``POST /v1/collections/{id}/restore`` takes the same path. Twenty concurrent
accesses cost at most one eviction and one submission. And over a random
sequence of creates, accesses, restore completions and archivings, the number
of physically-present collections never exceeds the bound.

The app's registry, store and stores are the in-process doubles from
``conftest.py`` plus what each test adds; the restorer is a recorder (the
engine side is covered by ``test_dormant_collection.py``).
"""
from __future__ import annotations

import asyncio
import random
import time
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from ragstack.acl_store import GRANTEE_USER, PERM_OWNER
from ragstack.api import security
from ragstack.api.collections import CollectionEntry
from ragstack.api.eviction import RestoreCapacity
from ragstack.api.lifecycle import LifecycleGate, reset_lifecycle_gate, set_lifecycle_gate
from ragstack.api.main import app
from ragstack.collection_store import (
    ACTIVE,
    DORMANT,
    PHYSICAL,
    RESTORING,
    AccessTracker,
    CollectionSpec,
    InMemoryCollectionStore,
    RestoreAdmission,
)
from ragstack.config import settings
from ragstack.identity import (
    Identity,
    IdentityInvalid,
    reset_identity_provider,
    set_identity_provider,
)
from ragstack.retrieval.retriever import HybridRetriever
from ragstack.stores import InMemoryTextIndex, InMemoryVectorStore

pytestmark = pytest.mark.asyncio

OWNER = "bvbrc:alice@patricbrc.org"
ALICE_TOKEN = "alice-token"
DORM = "dorm"  # the dormant collection under test


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #


class FakeProvider:
    async def authenticate(self, credential: str) -> Identity:
        if credential != ALICE_TOKEN:
            raise IdentityInvalid("no")
        return Identity(subject="alice@patricbrc.org", issuer="bvbrc", token_id=credential,
                        expires_at=int(time.time()) + 3600)


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
    """Logs every state swap and every admission attempt, in order."""

    def __init__(self, log: list) -> None:
        super().__init__()
        self._log = log

    async def set_state(self, cid, state, *, expect=None, reason=""):
        won = await super().set_state(cid, state, expect=expect, reason=reason)
        self._log.append(("set_state", cid, state, won))
        return won

    async def begin_restore(self, cid, *, expect, limit, reason=""):
        outcome = await super().begin_restore(cid, expect=expect, limit=limit, reason=reason)
        self._log.append(("begin_restore", cid, outcome))
        return outcome


class _RecordingRestorer:
    """The restorer's surface as the gate and the endpoint use it: records
    submissions and never completes them (the row stays ``restoring`` until a
    test flips it)."""

    def __init__(self) -> None:
        self.submissions: list[tuple[str, str]] = []

    async def submit(self, rec, token: str) -> str:
        self.submissions.append((rec.spec.id, token))
        return f"sub_{len(self.submissions)}"

    def watching(self, cid: str) -> bool:
        return False

    async def drain(self) -> None:
        return None


def _ago(**kw) -> str:
    return (datetime.now(UTC) - timedelta(**kw)).isoformat()


class World:
    """Archived collections over logging doubles, LRU order ``lib-0``
    (oldest) … ``lib-{n-1}`` (newest), plus the dormant ``dorm``."""

    def __init__(self, log: list, store: _RecordingStore, acl) -> None:
        self.log, self.store, self.acl, self.ids = log, store, acl, []
        self.gate: LifecycleGate | None = None
        self.restorer = _RecordingRestorer()

    async def add(self, cid: str, *, accessed: str, versions=(1,), archive_pending=False,
                  state: str = ACTIVE) -> None:
        spec = CollectionSpec(
            id=cid, label=cid, owner=OWNER, collection=f"ragstack_lib_{cid}",
            embedding_api="openai", embedding_model="test-model", embedding_model_dim=4,
            chunk_method="fixed",
        )
        await self.store.put(spec)
        for v in versions:
            await self.store.append_version(cid, v)
        await self.store.set_archive_pending(cid, archive_pending)
        await self.store.touch_accessed([cid], accessed)
        if state != ACTIVE:
            await self.store.set_state(cid, state, reason="evicted")
        vs, ti = _LoggingVectorStore(self.log, cid), _LoggingTextIndex(self.log, cid)
        app.state.collections.add(CollectionEntry(
            id=cid, label=cid, collection=f"ragstack_lib_{cid}", model="test-model", dim=4,
            chunk_method="fixed", chunk_size=None, chunk_overlap=None, chunk_params={},
            is_shared_surface=False, retriever=HybridRetriever(vs, ti, _FakeEmbedder()),
            vector_store=vs, text_index=ti, embedder=_FakeEmbedder(), owner=OWNER,
        ))
        await self.acl.grant(cid, GRANTEE_USER, OWNER, PERM_OWNER, granted_by=OWNER)
        self.ids.append(cid)

    async def states(self) -> dict[str, str]:
        return {r.spec.id: r.state for r in await self.store.list_records()}

    async def physical(self) -> int:
        return sum(1 for r in await self.store.list_records() if r.state in PHYSICAL)


@pytest.fixture
def identity(monkeypatch):
    monkeypatch.setattr(security.settings, "identity_provider", "bvbrc")
    set_identity_provider(FakeProvider())
    yield
    reset_identity_provider()


@pytest.fixture(autouse=True)
def _unlimited_creates(monkeypatch):
    """Autouse so it runs BEFORE ``client`` builds the limiters from settings:
    the property test creates far more collections than the per-hour create
    budget allows."""
    monkeypatch.setattr(settings, "rate_limit_collections_create_per_hour", 0)


@pytest_asyncio.fixture
async def world(client, identity, _acl_store, monkeypatch):
    monkeypatch.setattr(security.settings, "tenant_collections", {})
    log: list = []
    store = _RecordingStore(log)
    prior = getattr(app.state, "collection_store", None)
    app.state.collection_store = store
    w = World(log, store, _acl_store)
    for i in range(3):
        await w.add(f"lib-{i}", accessed=_ago(days=30 - 10 * i))
    await w.add(DORM, accessed=_ago(days=1), state=DORMANT)
    tracker = AccessTracker(store, flush_seconds=3600)
    gate = LifecycleGate(store, tracker=tracker, capacity=RestoreCapacity(app.state),
                         cache_seconds=5.0, retry_after=30)
    gate.restorer = w.restorer  # type: ignore[assignment]
    set_lifecycle_gate(gate)
    w.gate = gate
    log.clear()
    try:
        yield w
    finally:
        await gate.drain()
        reset_lifecycle_gate()
        for cid in list(w.ids):
            app.state.collections.remove(cid)
        if prior is None:
            del app.state.collection_store
        else:
            app.state.collection_store = prior


def _auth(token: str | None = ALICE_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


async def _retrieve(client, cid: str = DORM):
    return await client.post("/v1/retrieve", json={"query": "x", "collection": cid},
                             headers=_auth())


def _at_bound(monkeypatch, active: int = 3) -> None:
    """max_collections = the physically-present rows + the shared-surface
    pointer's slot: every slot is taken, a restore must evict."""
    monkeypatch.setattr(settings, "max_collections", active + 1)


async def _settle(world: World) -> None:
    """Let the winner's background submission run."""
    for _ in range(100):
        if not world.gate._pending:  # type: ignore[union-attr]
            break
        await asyncio.sleep(0.01)
    await world.gate.drain()  # type: ignore[union-attr]


# --------------------------------------------------------------------------- #
# on-access restore at the bound
# --------------------------------------------------------------------------- #


async def test_dormant_access_at_the_bound_evicts_one_lru_collection_and_submits_once(
    client, monkeypatch, world,
):
    _at_bound(monkeypatch)
    resp = await _retrieve(client)
    assert resp.status_code == 503, resp.text
    assert resp.headers["Retry-After"] == "30"
    assert f"collection {DORM!r} is restoring" in resp.json()["detail"]
    await _settle(world)

    assert await world.states() == {
        "lib-0": DORMANT, "lib-1": ACTIVE, "lib-2": ACTIVE, DORM: RESTORING,
    }
    assert world.restorer.submissions == [(DORM, ALICE_TOKEN)]
    # THE ORDER: the store refuses at the bound, the victim's row is dormant
    # BEFORE either of its stores is dropped, and only then is the restore
    # admitted. (Two refusals: the first try, then the re-try under the lock.)
    assert world.log == [
        ("begin_restore", DORM, RestoreAdmission.AT_CAP),
        ("begin_restore", DORM, RestoreAdmission.AT_CAP),
        ("set_state", "lib-0", DORMANT, True),
        ("drop_vectors", "lib-0"),
        ("drop_text", "lib-0"),
        ("begin_restore", DORM, RestoreAdmission.ADMITTED),
    ]
    assert world.gate.evictions == 1
    # The bound holds: three physically present (lib-1, lib-2, dorm restoring).
    assert await world.physical() == 3
    # Still restoring: the ordinary 503, nothing more evicted or submitted.
    resp = await _retrieve(client)
    assert resp.status_code == 503 and resp.headers["Retry-After"] == "30"
    assert world.gate.evictions == 1 and len(world.restorer.submissions) == 1


async def test_below_the_bound_no_eviction_and_one_admission(client, monkeypatch, world):
    monkeypatch.setattr(settings, "max_collections", 3 + 1 + 1)  # one free slot
    resp = await _retrieve(client)
    assert resp.status_code == 503 and resp.headers["Retry-After"] == "30"
    await _settle(world)
    assert world.log == [("begin_restore", DORM, RestoreAdmission.ADMITTED)]
    assert world.gate.evictions == 0 and world.gate.admissions == 1
    assert world.restorer.submissions == [(DORM, ALICE_TOKEN)]
    assert (await world.states())["lib-0"] == ACTIVE


async def test_nothing_evictable_is_503_retry_after_no_submission_row_stays_dormant(
    client, monkeypatch, world,
):
    for cid in ("lib-0", "lib-1", "lib-2"):
        await world.store.set_archive_pending(cid, True)  # nothing may be evicted
    world.log.clear()
    _at_bound(monkeypatch)

    resp = await _retrieve(client)
    assert resp.status_code == 503, resp.text
    assert resp.headers["Retry-After"] == "30"
    detail = resp.json()["detail"]
    assert f"collection {DORM!r} is dormant" in detail
    assert "tenant at capacity" in detail
    assert "active collection bound (4) is met" in detail
    assert "needed 1, found 0" in detail and "archive_pending=3" in detail
    await _settle(world)
    assert world.restorer.submissions == []
    assert (await world.states())[DORM] == DORMANT
    assert not [e for e in world.log if e[0] in ("set_state", "drop_vectors", "drop_text")]
    assert world.gate.evictions == 0
    # Still dormant, still refused: the next access tries again (and still
    # finds nothing to evict).
    resp = await _retrieve(client)
    assert resp.status_code == 503 and "tenant at capacity" in resp.json()["detail"]
    assert world.restorer.submissions == []


async def test_20_concurrent_accesses_at_the_bound_cost_at_most_one_eviction_and_one_submission(
    client, monkeypatch, world,
):
    _at_bound(monkeypatch)
    responses = await asyncio.gather(*(_retrieve(client) for _ in range(20)))
    assert {r.status_code for r in responses} == {503}
    assert {r.headers.get("Retry-After") for r in responses} == {"30"}
    details = [r.json()["detail"] for r in responses]
    # Every answer is the ordinary "restoring" 503 — nobody was refused for
    # capacity, because the winner's eviction made room for the one restore.
    assert all(f"collection {DORM!r} is restoring" in d for d in details), details
    await _settle(world)

    assert len(world.restorer.submissions) == 1
    assert world.gate.evictions == 1
    assert [e for e in world.log if e[0] == "drop_vectors"] == [("drop_vectors", "lib-0")]
    assert [e for e in world.log if e[0] == "set_state"] == [("set_state", "lib-0", DORMANT, True)]
    admitted = [e for e in world.log if e == ("begin_restore", DORM, RestoreAdmission.ADMITTED)]
    assert len(admitted) == 1
    assert await world.states() == {
        "lib-0": DORMANT, "lib-1": ACTIVE, "lib-2": ACTIVE, DORM: RESTORING,
    }
    assert await world.physical() == 3


async def test_a_concurrent_reservation_taking_the_freed_slot_is_503_not_a_second_eviction(
    client, monkeypatch, world,
):
    """One eviction, one retry, never a loop (the create path's rule): when
    a sibling's reservation lands in the freed slot, the restore is refused
    with the row left dormant rather than evicting again."""
    _at_bound(monkeypatch)
    original = world.store.begin_restore
    calls = 0
    thief = CollectionSpec(
        id="thief", collection="ragstack_lib_thief", embedding_api="openai",
        embedding_model="test-model", embedding_model_dim=4, chunk_method="fixed",
    )

    async def begin_with_a_thief(cid, *, expect, limit, reason=""):
        nonlocal calls
        calls += 1
        if calls == 3:  # the re-try after OUR eviction: a sibling's create lands first
            assert (await world.store.get("lib-0")).state == DORMANT
            await InMemoryCollectionStore.create(world.store, thief, limit=None)
        return await original(cid, expect=expect, limit=limit, reason=reason)

    world.store.begin_restore = begin_with_a_thief  # type: ignore[method-assign]
    resp = await _retrieve(client)
    assert resp.status_code == 503, resp.text
    assert resp.headers["Retry-After"] == "30"
    assert "tenant at capacity" in resp.json()["detail"]
    assert "took the slot the eviction freed" in resp.json()["detail"]
    await _settle(world)
    states = await world.states()
    assert states["lib-0"] == DORMANT and states["lib-1"] == ACTIVE  # exactly one eviction
    assert states[DORM] == DORMANT and "thief" in states
    assert world.restorer.submissions == [] and world.gate.evictions == 1
    assert await world.physical() == 3
    await world.store.delete("thief")


async def test_a_just_restored_collection_is_not_the_next_victim(client, monkeypatch, world):
    """LRU thrash: admission stamps `last_accessed_at`, so a collection
    restored a moment ago — whose pre-eviction stamp may be the oldest of
    all — is not the LRU victim of the next restore at the bound."""
    await world.add("dorm-b", accessed=_ago(days=2), state=DORMANT)
    await world.store.touch_accessed([DORM], _ago(days=100))  # the oldest stamp by far
    _at_bound(monkeypatch)
    assert (await _retrieve(client)).status_code == 503
    await _settle(world)
    assert (await world.states())["lib-0"] == DORMANT and (await world.states())[DORM] == RESTORING
    # The restore completes; A is active with the stamp its admission gave it.
    assert await world.store.set_state(DORM, ACTIVE, expect=RESTORING)
    world.gate.invalidate(DORM)
    assert (await world.store.get(DORM)).last_accessed_at > _ago(minutes=1)
    # B's restore at the bound evicts the LRU ACTIVE collection: lib-1, not A.
    assert (await _retrieve(client, "dorm-b")).status_code == 503
    await _settle(world)
    assert await world.states() == {
        "lib-0": DORMANT, "lib-1": DORMANT, "lib-2": ACTIVE, DORM: ACTIVE, "dorm-b": RESTORING,
    }
    assert [(c, t) for c, t in world.restorer.submissions] == [(DORM, ALICE_TOKEN), ("dorm-b", ALICE_TOKEN)]


# --------------------------------------------------------------------------- #
# POST /v1/collections/{id}/restore
# --------------------------------------------------------------------------- #


async def test_explicit_restore_at_the_bound_evicts_one_and_submits(client, monkeypatch, world):
    _at_bound(monkeypatch)
    resp = await client.post(f"/v1/collections/{DORM}/restore", headers=_auth())
    assert resp.status_code == 202, resp.text
    assert resp.json()["state"] == RESTORING and resp.json()["submission_id"] == "sub_1"
    assert world.restorer.submissions == [(DORM, ALICE_TOKEN)]
    assert await world.states() == {
        "lib-0": DORMANT, "lib-1": ACTIVE, "lib-2": ACTIVE, DORM: RESTORING,
    }
    assert world.log == [
        ("begin_restore", DORM, RestoreAdmission.AT_CAP),
        ("begin_restore", DORM, RestoreAdmission.AT_CAP),
        ("set_state", "lib-0", DORMANT, True),
        ("drop_vectors", "lib-0"),
        ("drop_text", "lib-0"),
        ("begin_restore", DORM, RestoreAdmission.ADMITTED),
    ]
    # Idempotent while in flight, exactly as before.
    resp = await client.post(f"/v1/collections/{DORM}/restore", headers=_auth())
    assert resp.status_code == 202 and resp.json()["submission_id"] is None
    assert len(world.restorer.submissions) == 1 and world.gate.evictions == 1


async def test_explicit_restore_with_nothing_evictable_is_503_retry_after(
    client, monkeypatch, world,
):
    for cid in ("lib-0", "lib-1", "lib-2"):
        await world.store.set_archive_pending(cid, True)
    world.log.clear()
    _at_bound(monkeypatch)
    resp = await client.post(f"/v1/collections/{DORM}/restore", headers=_auth())
    assert resp.status_code == 503, resp.text
    assert resp.headers["Retry-After"] == "30"
    detail = resp.json()["detail"]
    assert "tenant at capacity" in detail and "needed 1, found 0" in detail
    assert world.restorer.submissions == []
    assert (await world.states())[DORM] == DORMANT
    assert not [e for e in world.log if e[0] in ("set_state", "drop_vectors", "drop_text")]


# --------------------------------------------------------------------------- #
# the invariant: create + restore never exceed the bound
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("seed", [1, 7, 42, 2026])
async def test_physically_present_never_exceeds_the_bound_over_random_creates_and_accesses(
    client, monkeypatch, world, seed,
):
    """Property (#381 acceptance): over a random sequence of creates
    (``POST /v1/collections``), accesses to dormant collections (the gate),
    restore completions and archivings, the number of physically-present
    rows (``PHYSICAL``) never exceeds the bound — ``max_collections`` minus
    the shared-surface pointer's slot. Each step's status is one of the
    expected answers; what matters is the count after every step."""
    rng = random.Random(seed)
    bound = 4
    monkeypatch.setattr(settings, "max_collections", bound + 1)
    monkeypatch.setattr(settings, "max_collections_per_owner", 0)
    created: list[str] = []
    seen: dict[int, int] = {}
    try:
        for step in range(60):
            records = {r.spec.id: r for r in await world.store.list_records()}
            dormant = sorted(cid for cid, r in records.items() if r.state == DORMANT)
            restoring = sorted(cid for cid, r in records.items() if r.state == RESTORING)
            unarchived = sorted(cid for cid, r in records.items()
                                if r.state == ACTIVE and not r.versions)
            op = rng.choice(["create", "create", "access", "access", "complete", "archive"])
            if op == "create":
                cid = f"c{seed}-{step}"
                r = await client.post("/v1/collections", json={"id": cid}, headers=_auth())
                assert r.status_code in (201, 507), r.text
                if r.status_code == 201:
                    created.append(cid)
            elif op == "access" and dormant:
                r = await _retrieve(client, rng.choice(dormant))
                assert r.status_code == 503, r.text
                await _settle(world)
            elif op == "complete" and restoring:
                cid = rng.choice(restoring)
                assert await world.store.set_state(cid, ACTIVE, expect=RESTORING)
                world.gate.invalidate(cid)
            elif op == "archive" and unarchived:
                await world.store.append_version(rng.choice(unarchived), 1)
            present = await world.physical()
            seen[present] = seen.get(present, 0) + 1
            assert present <= bound, (seed, step, op, await world.states())
        # The sequence actually exercised the bound (creates + restores
        # filled it), not just an under-full registry.
        assert bound in seen, seen
    finally:
        for cid in created:
            app.state.collections.remove(cid)
