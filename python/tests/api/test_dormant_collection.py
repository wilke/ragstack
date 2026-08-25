"""The dormant → restoring → active path over HTTP (#358, phase 2 of #353).

A registry collection whose state is ``dormant`` answers every read/ingest
with **503 + Retry-After** and submits ONE restore — as the caller, with the
caller's bearer token — however many requests race for it; ``restoring`` is
503 too; ``lost`` is 409. The fakes stand in for the two external systems and
nothing else: the Workspace (lists the owner's version folders) and the GoWe
engine, which here actually RUNS the loader's verification over the local
version directories on submission, so a tampered manifest fails the way a real
worker would fail it and the API's classification of that failure is what is
under test.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
import pytest_asyncio

from ragstack.acl_store import GRANTEE_USER, PERM_OWNER
from ragstack.api import security
from ragstack.api.collections import CollectionEntry
from ragstack.api.lifecycle import LifecycleGate, reset_lifecycle_gate, set_lifecycle_gate
from ragstack.api.main import app
from ragstack.collection_store import (
    ACTIVE,
    DORMANT,
    LOST,
    RESTORING,
    AccessTracker,
    CollectionSpec,
    InMemoryCollectionStore,
)
from ragstack.identity import (
    Identity,
    IdentityInvalid,
    reset_identity_provider,
    set_identity_provider,
)
from ragstack.ingestion.gowe_client import GoWeError
from ragstack.ingestion.load_embeddings import ReplayRefused, verify_replay
from ragstack.restore import DEFAULT_CWL, CollectionRestorer, classify_failure
from ragstack.retrieval.retriever import HybridRetriever
from ragstack.stores import InMemoryTextIndex, InMemoryVectorStore
from ragstack.workspace import WorkspaceNotFound, collection_folder, ws_uri
from tests.archive_support import chunk_version, tamper_manifest, tombstone_version

pytestmark = pytest.mark.asyncio

OWNER = "bvbrc:alice@patricbrc.org"
ALICE_TOKEN = "alice-token"
BOB_TOKEN = "bob-token"
CID = "lib"
SPEC_HASH = "cafe0001"


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #


class FakeProvider:
    """Two bearer identities: alice (the owner) and bob (a stranger)."""

    async def authenticate(self, credential: str) -> Identity:
        who = {ALICE_TOKEN: "alice@patricbrc.org", BOB_TOKEN: "bob@patricbrc.org"}.get(credential)
        if who is None:
            raise IdentityInvalid("no")
        return Identity(subject=who, issuer="bvbrc", token_id=credential,
                        expires_at=int(time.time()) + 3600)


class FakeWorkspace:
    """``list_versions`` over a dict of folder -> [(n, local_dir)]."""

    def __init__(self) -> None:
        self.folders: dict[str, list[tuple[int, Path]]] = {}
        self.calls: list[tuple[str, str]] = []

    async def list_versions(self, token: str, folder: str) -> list[tuple[int, str]]:
        self.calls.append((token, folder))
        if folder not in self.folders:
            raise WorkspaceNotFound(f"{folder}/versions does not exist")
        return [(n, ws_uri(f"{folder}/versions/{n}")) for n, _ in sorted(self.folders[folder])]


class FakeEngine:
    """The GoWe side. ``submit`` resolves the ``ws://`` Directory inputs back
    to local dirs and runs the loader's verification — the part of the
    restore workflow that decides lost-vs-restored — recording COMPLETED or a
    FAILED record carrying the loader's marker line. ``hold`` keeps every
    submission non-terminal until :meth:`release`."""

    #: GoWe keeps only the first 1000 characters of a failed task's stderr.
    STDERR_KEEP = 1000

    def __init__(self, workspace: FakeWorkspace) -> None:
        self.workspace = workspace
        self.submissions: list[dict] = []
        self.tokens: list[str] = []
        self.records: dict[str, dict] = {}
        self.hold = False
        self._released = asyncio.Event()
        # What the loader writes to stderr BEFORE the marker line (store
        # client warnings from building the pipeline); real runs have plenty.
        self.stderr_prefix = ""
        # How many times `wait` gives up (its own timeout) before returning.
        self.wait_timeouts = 0
        # ...and whether the engine forgets the submission at that moment.
        self.forget_on_timeout = False

    def _local(self, location: str) -> Path:
        for folder, versions in self.workspace.folders.items():
            for n, path in versions:
                if location == ws_uri(f"{folder}/versions/{n}"):
                    return path
        raise AssertionError(f"unstageable input {location}")

    def submit(self, token: str, inputs: dict, labels: dict | None) -> dict:
        sub_id = f"sub_{len(self.submissions) + 1}"
        self.tokens.append(token)
        self.submissions.append({"id": sub_id, "inputs": inputs, "labels": labels})
        dirs = [self._local(d["location"]) for d in inputs["versions"]]
        try:
            verify_replay(dirs, spec_hash=inputs["spec_hash"], collection_id=inputs["collection_id"])
            record = {"id": sub_id, "state": "COMPLETED", "outputs": {}}
        except ReplayRefused as e:
            # The real terminal record: error.{code, message, context.{stderr,
            # exit_code}}, stderr truncated to its first 1000 characters —
            # the loader's marker line comes AFTER whatever the pipeline build
            # printed, so it may or may not fall inside the window.
            stderr = (self.stderr_prefix + str(e) + "\n")[: self.STDERR_KEEP]
            record = {"id": sub_id, "state": "FAILED", "error": {
                "code": "TASK_FAILED", "message": "step replay failed",
                "context": {"stderr": stderr, "exit_code": 3},
            }}
        self.records[sub_id] = record
        return {"id": sub_id, "state": "PENDING"}

    def release(self) -> None:
        self.hold = False
        self._released.set()

    async def terminal(self, sub_id: str) -> dict:
        if self.hold:
            await self._released.wait()
        return self.records[sub_id]


class FakeGoWeClient:
    """What ``CollectionRestorer`` builds per submission (``gowe_factory``)."""

    def __init__(self, engine: FakeEngine, token: str) -> None:
        self.engine = engine
        self.token = token
        self.closed = False

    async def register_workflow(self, name: str, cwl: str, labels=None) -> str:
        assert "restore-collection" in cwl or "cwlVersion" in cwl
        return "wf_restore"

    async def submit(self, workflow_id: str, inputs: dict, *, labels=None, **_kw) -> dict:
        assert workflow_id == "wf_restore"
        if not self.token:
            raise GoWeError("POST /api/v1/submissions -> 401: token required")
        return self.engine.submit(self.token, inputs, labels)

    async def get_submission(self, sub_id: str) -> dict:
        if sub_id not in self.engine.records:
            raise GoWeError(f"GET /api/v1/submissions/{sub_id} -> 404")
        if self.engine.hold:
            return {"id": sub_id, "state": "RUNNING"}
        return self.engine.records[sub_id]

    async def wait(self, sub_id: str, *, poll_interval: float = 0, timeout: float = 0) -> dict:
        if self.engine.wait_timeouts > 0:
            self.engine.wait_timeouts -= 1
            if self.engine.forget_on_timeout:
                self.engine.records.pop(sub_id, None)
            raise GoWeError(f"submission {sub_id} not terminal after {timeout}s (state=RUNNING)")
        return await self.engine.terminal(sub_id)

    async def close(self) -> None:
        self.closed = True


class _FakeEmbedder:
    async def embed(self, texts):
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def identity(monkeypatch):
    monkeypatch.setattr(security.settings, "identity_provider", "bvbrc")
    set_identity_provider(FakeProvider())
    yield
    reset_identity_provider()


@pytest_asyncio.fixture
async def world(client, _acl_store, tmp_path, monkeypatch):
    """A registry with collection ``lib`` owned by alice, its Workspace archive
    (two chunk versions + a tombstone) and the lifecycle gate over fakes."""
    monkeypatch.setattr(security.settings, "tenant_collections", {})
    store = InMemoryCollectionStore()
    spec = CollectionSpec(
        id=CID, label="lib", owner=OWNER, collection="ragstack_lib_lib",
        embedding_api="openai", embedding_model="test-model", embedding_model_dim=4,
        chunk_method="fixed",
    )
    await store.put(spec)
    # The API test fixture's registry knows only `default`; add `lib` over
    # fresh in-memory stores so an ACTIVE read actually serves.
    vstore, tindex = InMemoryVectorStore(), InMemoryTextIndex()
    app.state.collections.add(CollectionEntry(
        id=CID, label="lib", collection="ragstack_lib_lib", model="test-model", dim=4,
        chunk_method="fixed", chunk_size=None, chunk_overlap=None, chunk_params={},
        is_shared_surface=False, retriever=HybridRetriever(vstore, tindex, _FakeEmbedder()),
        vector_store=vstore, text_index=tindex, embedder=_FakeEmbedder(), owner=OWNER,
    ))
    app.state.collection_store = store
    await _acl_store.grant(CID, GRANTEE_USER, OWNER, PERM_OWNER, granted_by=OWNER)

    spec_hash = (await store.get(CID)).spec_hash
    root = tmp_path / "archive"
    root.mkdir()
    v1, _ = chunk_version(root, 1, 6, spec_hash=spec_hash, collection_id=CID)
    v2, _ = chunk_version(root, 2, 6, start=6, spec_hash=spec_hash, collection_id=CID)
    v3 = tombstone_version(root, 3, ["doc-0"], spec_hash=spec_hash, collection_id=CID)
    workspace = FakeWorkspace()
    folder = collection_folder("alice@patricbrc.org", CID)
    workspace.folders[folder] = [(1, v1), (2, v2), (3, v3)]
    engine = FakeEngine(workspace)

    tracker = AccessTracker(store, flush_seconds=3600)
    gate = LifecycleGate(store, tracker=tracker, cache_seconds=5.0, retry_after=30,
                         restore_timeout=3600)
    gate.restorer = CollectionRestorer(
        store, workspace=workspace, gowe_factory=lambda tok: FakeGoWeClient(engine, tok),
        cwl_path=DEFAULT_CWL, static_inputs={"qdrant_url": "http://q", "es_url": "http://e"},
        worker_group="ragstack", poll_interval=0, timeout=60, on_change=gate.invalidate,
    )
    set_lifecycle_gate(gate)
    try:
        yield {"store": store, "gate": gate, "engine": engine, "workspace": workspace,
               "versions": [v1, v2, v3], "spec_hash": spec_hash, "tracker": tracker}
    finally:
        engine.release()
        await gate.drain()
        reset_lifecycle_gate()
        app.state.collections.remove(CID)


def _auth(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


async def _retrieve(client, token=ALICE_TOKEN):
    return await client.post("/v1/retrieve", json={"query": "x", "collection": CID},
                             headers=_auth(token))


# --------------------------------------------------------------------------- #
# on-access restore
# --------------------------------------------------------------------------- #


async def test_active_collection_serves_and_records_the_access(client, identity, world):
    resp = await _retrieve(client)
    assert resp.status_code == 200, resp.text
    assert world["tracker"].pending == 1  # touched, not written
    assert (await world["store"].get(CID)).last_accessed_at == ""
    assert await world["tracker"].flush() == 1
    assert (await world["store"].get(CID)).last_accessed_at != ""


async def test_dormant_query_is_503_with_retry_after_and_submits_once_across_20_requests(
    client, identity, world,
):
    store, gate, engine = world["store"], world["gate"], world["engine"]
    await store.set_state(CID, DORMANT, reason="evicted")
    gate.invalidate(CID)
    engine.hold = True  # keep the submission running so the state stays `restoring`

    responses = await asyncio.gather(*(_retrieve(client) for _ in range(20)))
    assert {r.status_code for r in responses} == {503}
    assert {r.headers.get("Retry-After") for r in responses} == {"30"}
    details = {r.json()["detail"] for r in responses}
    assert all(f"collection {CID!r} is restoring" in d for d in details), details

    # Exactly one submission, made AS THE CALLER, over every version in order,
    # with the registry identity the loader verifies against.
    await asyncio.sleep(0)  # let the winner's background submission run
    while gate._pending and len(engine.submissions) == 0:
        await asyncio.sleep(0.01)
    assert len(engine.submissions) == 1
    sub = engine.submissions[0]
    assert engine.tokens == [ALICE_TOKEN]
    assert [d["location"] for d in sub["inputs"]["versions"]] == [
        ws_uri(f"{collection_folder('alice@patricbrc.org', CID)}/versions/{n}") for n in (1, 2, 3)
    ]
    assert sub["inputs"]["collection_id"] == CID
    assert sub["inputs"]["spec_hash"] == world["spec_hash"]
    assert sub["inputs"]["qdrant_url"] == "http://q"
    assert sub["labels"] == {"worker_group": "ragstack"}
    # The token never travels in the inputs (only in the Authorization header).
    assert ALICE_TOKEN not in json.dumps(sub["inputs"])
    assert (await store.get(CID)).state == RESTORING

    # Still restoring: 503 again, and no second submission.
    resp = await _retrieve(client)
    assert resp.status_code == 503 and resp.headers["Retry-After"] == "30"
    assert len(engine.submissions) == 1

    # The engine completes → the watcher flips the row → the next read serves.
    engine.release()
    await gate.drain()
    assert (await store.get(CID)).state == ACTIVE
    assert (await _retrieve(client)).status_code == 200


async def test_ingest_into_a_dormant_collection_is_503_too(client, identity, world, tmp_path):
    await world["store"].set_state(CID, DORMANT)
    world["gate"].invalidate(CID)
    world["engine"].hold = True
    doc = tmp_path / "doc.txt"
    doc.write_text("hello")
    resp = await client.post("/v1/ingest", json={"source": str(doc), "collection": CID},
                             headers=_auth(ALICE_TOKEN))
    assert resp.status_code == 503, resp.text
    assert resp.headers["Retry-After"] == "30"


async def test_keyless_caller_cannot_trigger_a_restore(client, world):
    """No identity provider: the caller has no user token. 503 says so, the
    row stays dormant and nothing is submitted."""
    await world["store"].set_state(CID, DORMANT)
    world["gate"].invalidate(CID)
    resp = await client.post("/v1/retrieve", json={"query": "x", "collection": CID})
    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == "30"
    assert "user (bearer) token is required" in resp.json()["detail"]
    assert world["engine"].submissions == []
    assert (await world["store"].get(CID)).state == DORMANT


async def test_lost_collection_is_409_with_the_reason(client, identity, world):
    await world["store"].set_state(CID, LOST, reason="sha256 mismatch in versions/2")
    world["gate"].invalidate(CID)
    resp = await _retrieve(client)
    assert resp.status_code == 409
    assert "sha256 mismatch in versions/2" in resp.json()["detail"]
    assert "/restore" in resp.json()["detail"]
    assert world["engine"].submissions == []


async def test_tampered_manifest_makes_the_collection_lost(client, identity, world):
    """The fake engine runs the loader's verification: a manifest whose
    spec_hash was edited fails as SpecMismatch, the watcher classifies the
    FAILED record and the row becomes `lost` with the reason."""
    store, gate = world["store"], world["gate"]
    tamper_manifest(world["versions"][1], spec_hash="deadbeef")
    await store.set_state(CID, DORMANT)
    gate.invalidate(CID)
    assert (await _retrieve(client)).status_code == 503
    await gate.drain()
    rec = await store.get(CID)
    assert rec.state == LOST
    assert "SpecMismatch" in rec.state_reason and "deadbeef" in rec.state_reason
    resp = await _retrieve(client)
    assert resp.status_code == 409
    assert "SpecMismatch" in resp.json()["detail"]


async def test_marker_beyond_the_engines_stderr_window_still_classifies_as_lost(
    client, identity, world,
):
    """The engine keeps the first 1000 chars of stderr; the loader's marker
    line comes after the pipeline build's client warnings, so it is often
    outside the window. The exit code (3) is what decides `lost`."""
    store, gate, engine = world["store"], world["gate"], world["engine"]
    engine.stderr_prefix = ("UserWarning: Failed to obtain server version. Unable to check "
                            "client-server compatibility.\n") * 15  # > 1000 chars
    tamper_manifest(world["versions"][0], spec_hash="deadbeef")
    await store.set_state(CID, DORMANT)
    gate.invalidate(CID)
    assert (await _retrieve(client)).status_code == 503
    await gate.drain()
    record = engine.records["sub_1"]
    assert "SpecMismatch" not in record["error"]["context"]["stderr"]  # the case that bit
    rec = await store.get(CID)
    assert rec.state == LOST, rec.state_reason
    assert "exit 3" in rec.state_reason and "submission=sub_1" in rec.state_reason
    assert (await _retrieve(client)).status_code == 409


async def test_classify_failure_is_exit_code_first():
    ctx = {"stderr": "warnings only, no marker in the window", "exit_code": 3}
    state, reason = classify_failure({"id": "s", "state": "FAILED",
                                      "error": {"code": "TASK_FAILED", "message": "m", "context": ctx}})
    assert state == LOST and "exit 3" in reason
    # Marker in the window is the secondary signal (exit code unknown).
    state, reason = classify_failure({"id": "s", "state": "FAILED", "error": {
        "code": "TASK_FAILED", "message": "m",
        "context": {"stderr": "junk\nArchiveCorrupt: vectors.f32: sha256 x != y\nmore"}}})
    assert state == LOST and reason.endswith("ArchiveCorrupt: vectors.f32: sha256 x != y")
    # Any other exit code without a marker: dormant, retried (2 = registry
    # disagreement, 1 = mid-stream) — the exit code is recorded.
    for code in (1, 2, 137):
        state, reason = classify_failure({"id": "s", "state": "FAILED", "error": {
            "code": "TASK_FAILED", "message": "worker OOM",
            "context": {"stderr": "", "exit_code": code}}})
        assert state == DORMANT and f"(exit {code})" in reason and "worker OOM" in reason
    # A record with no error at all (CANCELLED) is dormant too.
    assert classify_failure({"id": "s", "state": "CANCELLED"})[0] == DORMANT


async def test_wait_timeout_does_not_demote_while_the_engine_still_runs(
    client, identity, world,
):
    """`client.wait` giving up (its own timeout) is not the engine stopping:
    the watcher confirms with get_submission, keeps `restoring`, waits again —
    and the gate's watchdog leaves a row this process is still watching
    alone, however old it looks. Otherwise the next access would submit a
    SECOND restore over the same versions while the first still runs."""
    store, gate, engine = world["store"], world["gate"], world["engine"]
    engine.hold = True           # the engine keeps running…
    engine.wait_timeouts = 1     # …while the first wait() gives up
    await store.set_state(CID, DORMANT)
    gate.invalidate(CID)
    assert (await _retrieve(client)).status_code == 503
    while len(engine.submissions) == 0:
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)  # the watcher has hit the timeout and re-checked by now
    assert (await store.get(CID)).state == RESTORING
    assert "submission=sub_1" in (await store.get(CID)).state_reason
    assert gate.restorer.watching(CID)
    # Make the row LOOK orphaned: the watchdog must still not reset it.
    gate.restore_timeout = 1.0
    rec = await store.get(CID)
    store._records[CID] = rec.model_copy(update={"state_changed_at": "2020-01-01T00:00:00+00:00"})
    gate.invalidate(CID)
    resp = await _retrieve(client)
    assert resp.status_code == 503 and resp.headers["Retry-After"] == "30"
    assert len(engine.submissions) == 1
    assert (await store.get(CID)).state == RESTORING
    engine.release()
    await gate.drain()
    assert (await store.get(CID)).state == ACTIVE
    assert len(engine.submissions) == 1


async def test_unreadable_submission_after_a_wait_failure_demotes(client, identity, world):
    store, gate, engine = world["store"], world["gate"], world["engine"]
    engine.wait_timeouts = 1
    engine.forget_on_timeout = True  # the engine forgot it: get_submission 404s
    await store.set_state(CID, DORMANT)
    gate.invalidate(CID)
    assert (await _retrieve(client)).status_code == 503
    await gate.drain()
    rec = await store.get(CID)
    assert rec.state == DORMANT and "no longer reports" in rec.state_reason


async def test_missing_archive_folder_makes_the_collection_lost(client, identity, world):
    world["workspace"].folders.clear()
    await world["store"].set_state(CID, DORMANT)
    world["gate"].invalidate(CID)
    assert (await _retrieve(client)).status_code == 503
    await world["gate"].drain()
    rec = await world["store"].get(CID)
    assert rec.state == LOST and "archive folder missing" in rec.state_reason
    assert world["engine"].submissions == []


async def test_engine_failure_returns_the_row_to_dormant_with_the_error(client, identity, world):
    engine = world["engine"]
    engine.records["sub_1"] = {"id": "sub_1", "state": "FAILED", "error": "worker OOM"}
    original = engine.submit

    def failing_submit(token, inputs, labels):
        original(token, inputs, labels)
        engine.records["sub_1"] = {"id": "sub_1", "state": "FAILED", "error": "worker OOM"}
        return {"id": "sub_1", "state": "PENDING"}

    engine.submit = failing_submit  # type: ignore[method-assign]
    await world["store"].set_state(CID, DORMANT)
    world["gate"].invalidate(CID)
    assert (await _retrieve(client)).status_code == 503
    await world["gate"].drain()
    rec = await world["store"].get(CID)
    assert rec.state == DORMANT and "worker OOM" in rec.state_reason
    # ...and the NEXT access tries again.
    assert (await _retrieve(client)).status_code == 503
    await world["gate"].drain()
    assert len(engine.submissions) == 2


async def test_stale_restoring_row_is_unstuck_and_restored(client, identity, world):
    """An API process that died mid-restore leaves `restoring` behind; a row
    older than the restore timeout is reset and restored on the next access."""
    store, gate = world["store"], world["gate"]
    gate.restore_timeout = 1.0
    await store.set_state(CID, RESTORING, reason="orphaned by a dead process")
    rec = await store.get(CID)
    store._records[CID] = rec.model_copy(update={"state_changed_at": "2020-01-01T00:00:00+00:00"})
    gate.invalidate(CID)
    assert (await _retrieve(client)).status_code == 503
    await gate.drain()
    assert len(world["engine"].submissions) == 1
    assert (await store.get(CID)).state == ACTIVE


async def test_owner_actions_are_not_lifecycle_gated(client, identity, world):
    """Managing a dormant collection (shares, here) must not require restoring it."""
    await world["store"].set_state(CID, DORMANT)
    world["gate"].invalidate(CID)
    resp = await client.get(f"/v1/collections/{CID}/shares", headers=_auth(ALICE_TOKEN))
    assert resp.status_code == 200, resp.text
    assert world["engine"].submissions == []


async def test_listing_reports_the_lifecycle_fields(client, identity, world):
    await world["store"].set_state(CID, DORMANT, reason="evicted")
    await world["store"].append_version(CID, 1)
    await world["store"].append_version(CID, 2)
    resp = await client.get("/v1/collections", headers=_auth(ALICE_TOKEN))
    assert resp.status_code == 200
    by_id = {c["id"]: c for c in resp.json()["collections"]}
    assert by_id[CID]["state"] == DORMANT
    assert by_id[CID]["archive_pending"] is False
    assert by_id[CID]["versions"] == [1, 2]
    # The settings-derived default is not registry-tracked: fields are null.
    assert by_id["default"]["state"] is None and by_id["default"]["versions"] is None


# --------------------------------------------------------------------------- #
# POST /v1/collections/{id}/restore
# --------------------------------------------------------------------------- #


async def test_explicit_restore_is_owner_only_and_idempotent(client, identity, world):
    store, engine = world["store"], world["engine"]
    await store.set_state(CID, DORMANT)
    world["gate"].invalidate(CID)
    engine.hold = True

    # A stranger: cannot read it -> 404 (no existence oracle).
    resp = await client.post(f"/v1/collections/{CID}/restore", headers=_auth(BOB_TOKEN))
    assert resp.status_code == 404
    assert engine.submissions == []

    resp = await client.post(f"/v1/collections/{CID}/restore", headers=_auth(ALICE_TOKEN))
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body == {"collection_id": CID, "state": RESTORING, "submission_id": "sub_1",
                    "message": body["message"]}
    assert len(engine.submissions) == 1 and engine.tokens == [ALICE_TOKEN]

    # Again while in flight: 202, no second submission.
    resp = await client.post(f"/v1/collections/{CID}/restore", headers=_auth(ALICE_TOKEN))
    assert resp.status_code == 202
    assert resp.json()["state"] == RESTORING and resp.json()["submission_id"] is None
    assert len(engine.submissions) == 1

    engine.release()
    await world["gate"].drain()
    # Active: 202, nothing to do.
    resp = await client.post(f"/v1/collections/{CID}/restore", headers=_auth(ALICE_TOKEN))
    assert resp.status_code == 202 and resp.json()["state"] == ACTIVE
    assert len(engine.submissions) == 1


async def test_explicit_restore_may_retry_from_lost(client, identity, world):
    await world["store"].set_state(CID, LOST, reason="user deleted a version")
    world["gate"].invalidate(CID)
    resp = await client.post(f"/v1/collections/{CID}/restore", headers=_auth(ALICE_TOKEN))
    assert resp.status_code == 202 and resp.json()["state"] == RESTORING
    await world["gate"].drain()
    assert (await world["store"].get(CID)).state == ACTIVE


async def test_explicit_restore_needs_a_user_token(client, world, monkeypatch):
    # Keyless admin (owner check passes) but no bearer credential: refused
    # before anything is listed or submitted.
    monkeypatch.setattr(security.settings, "default_role", "admin")
    await world["store"].set_state(CID, DORMANT)
    resp = await client.post(f"/v1/collections/{CID}/restore")
    assert resp.status_code == 400
    assert "bearer" in resp.json()["detail"]
    assert world["engine"].submissions == []


async def test_explicit_restore_reports_a_submission_failure_as_502(client, identity, world):
    world["workspace"].folders.clear()
    await world["store"].set_state(CID, DORMANT)
    world["gate"].invalidate(CID)
    resp = await client.post(f"/v1/collections/{CID}/restore", headers=_auth(ALICE_TOKEN))
    assert resp.status_code == 502
    assert "archive folder missing" in resp.json()["detail"]
    assert (await world["store"].get(CID)).state == LOST
