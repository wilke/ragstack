"""``POST /v1/collections/{id}/graph`` (#350): the graph leg's trigger.

Fake Workspace (versions listing + manifest reads) and fake GoWe client
(register / submit / wait with a hold-and-release, two-phase completion with
``output_state``), the in-process app with in-memory doubles, a bearer BV-BRC
identity that owns ``lib1`` and a second one that only reads it.

Pins: non-owner 403 / unknown 404; keyless and API-key 401 (the submission is
made as the user); the submission carries the LATEST chunk version's ``ws://``
Directory (tombstones skipped) or ``?version=n``, the registry identity, the
budgets, the owner's ``versions/`` folder as the destination, and the token
ONLY as the per-call header argument — never in the inputs, the job store, a
log line; a second job per owner while one is in flight is 429 + Retry-After;
completion is two-phase — the job completes and ``graph_archived_versions`` is
set only when the engine reports DELIVERED; ``upload_failed`` fails the job
``OUTPUT_STAGING_FAILED`` and records nothing; a FAILED submission with the load
tool's exit 4 is ``graph_cap_exceeded``; a version whose leg exists is a 202
no-op; a dormant collection is 409; an active graph job does not block uploads.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException

from ragstack.acl_store import GRANTEE_USER, PERM_OWNER, PERM_READ
from ragstack.api import security
from ragstack.api.collections import CollectionEntry
from ragstack.api.deps import single_inflight_ingest
from ragstack.api.main import app
from ragstack.collection_store import DORMANT, CollectionSpec, InMemoryCollectionStore
from ragstack.graph.budget import GRAPH_CAP_EXCEEDED, format_graph_refusal
from ragstack.graph_extract import GraphExtractRunner
from ragstack.identity import (
    Identity,
    IdentityInvalid,
    reset_identity_provider,
    set_identity_provider,
)
from ragstack.ingestion.gowe_backend import OUTPUT_STAGING_FAILED
from ragstack.ingestion.gowe_client import GoWeError
from ragstack.jobstore import KIND_GRAPH, KIND_INGEST
from ragstack.workspace import WorkspaceNotFound, collection_folder, ws_path, ws_uri

pytestmark = pytest.mark.asyncio

REPO = Path(__file__).resolve().parents[3]
TOKENS = {
    "alice": "un=alice@patricbrc.org|tokenid=t-1|expiry=9999999999|sig=ALICESECRETSIG",
    "bob": "un=bob@patricbrc.org|tokenid=t-2|expiry=9999999999|sig=BOBSECRETSIG",
}
SUBJECTS = {"alice": "alice@patricbrc.org", "bob": "bob@patricbrc.org"}
ALICE = f"bvbrc:{SUBJECTS['alice']}"
BOB = f"bvbrc:{SUBJECTS['bob']}"
FOLDER = collection_folder(SUBJECTS["alice"], "lib1")
VERSIONS = ws_uri(f"{FOLDER}/versions") + "/"


def _auth(who: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[who]}"}


class _Provider:
    async def authenticate(self, credential: str) -> Identity:
        for who, tok in TOKENS.items():
            if credential == tok:
                return Identity(subject=SUBJECTS[who], issuer="bvbrc", token_id=f"t-{who}",
                                expires_at=int(time.time()) + 3600)
        raise IdentityInvalid("no")


def _manifest(version: int, *, tombstone: bool = False, graph: bool = False) -> dict[str, Any]:
    m: dict[str, Any] = {"format": "ragstack-archive/1", "collection_id": "lib1",
                         "tenant": ALICE, "spec_hash": "x", "version": version,
                         "has_tombstone": tombstone, "graph": graph,
                         "files": {"manifest": "manifest.json"}, "sha256": {}, "counts": {}}
    return m


class FakeWorkspace:
    """The owner's ``versions/`` listing and each version's manifest."""

    def __init__(self) -> None:
        self.versions: dict[int, dict[str, Any]] = {}
        self.calls: list[tuple[str, str, str]] = []
        self.missing = False

    async def list_versions(self, token: str, folder: str) -> list[tuple[int, str]]:
        self.calls.append((token, "ls", folder))
        if self.missing:
            raise WorkspaceNotFound(f"{folder}/versions does not exist")
        assert folder == FOLDER
        return [(n, ws_uri(f"{folder}/versions/{n}")) for n in sorted(self.versions)]

    async def read_file(self, token: str, path: str) -> bytes:
        self.calls.append((token, "read", path))
        prefix = f"{FOLDER}/versions/"
        assert path.startswith(prefix) and path.endswith("/manifest.json"), path
        n = int(path[len(prefix):].split("/")[0])
        return json.dumps(self.versions[n]).encode()


class FakeGoWe:
    """The tokenless shared client shape: every call takes ``token=``. A
    submission stays non-terminal while ``hold`` is set; ``outcome`` decides
    the terminal record: ``delivered`` (COMPLETED, two-phase), ``upload_failed``,
    ``cap`` (FAILED, the load tool's exit 4), ``failed``."""

    def __init__(self) -> None:
        self.submissions: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str | None]] = []
        self.hold = False
        self._released = asyncio.Event()
        self.outcome = "delivered"
        self.refuse_submit = False
        self.waits: list[bool] = []
        self.polls: list[str] = []

    def release(self) -> None:
        self.hold = False
        self._released.set()

    async def register_workflow(self, name, cwl, labels=None, *, token=None) -> str:
        self.calls.append(("register", token))
        assert "graph-extract" in cwl and token
        return "wf_graph"

    async def submit(self, wf_id, inputs, *, labels=None, output_destination=None,
                     token=None, **_kw) -> dict[str, Any]:
        self.calls.append(("submit", token))
        if self.refuse_submit:
            raise GoWeError("POST /api/v1/submissions -> 503: engine down")
        sub_id = f"sub_{len(self.submissions) + 1}"
        self.submissions.append({"id": sub_id, "workflow_id": wf_id, "inputs": inputs,
                                 "output_destination": output_destination, "labels": labels})
        return {"id": sub_id, "state": "PENDING"}

    async def get_submission(self, sub_id, *, token=None):
        self.calls.append(("get", token))
        return {"id": sub_id, "state": "RUNNING" if self.hold else "COMPLETED"}

    async def wait(self, sub_id, *, poll_interval=0, timeout=0, token=None,
                   require_delivery=False, delivery_timeout=0) -> dict[str, Any]:
        self.calls.append(("wait", token))
        self.waits.append(require_delivery)
        if self.hold:
            await self._released.wait()
        version = self.submissions[int(sub_id.split("_")[1]) - 1]["inputs"]["version"]
        outputs = {"archive": {"class": "Directory", "location": f"file:///w/{sub_id}/{version}"}}
        if self.outcome == "delivered":
            # The engine finalizes and post-stages in one tick: a poll can see
            # COMPLETED with output_state "" / uploading first. The client
            # (require_delivery) keeps polling; the record it returns is the
            # delivered one.
            self.polls.extend(["", "uploading", "delivered"])
            return {"id": sub_id, "state": "COMPLETED", "output_state": "delivered",
                    "outputs": outputs}
        if self.outcome == "upload_failed":
            return {"id": sub_id, "state": "COMPLETED", "output_state": "upload_failed",
                    "outputs": outputs}
        if self.outcome == "cap":
            line = format_graph_refusal(199_998, 3, 200_000)
            return {"id": sub_id, "state": "FAILED", "error": {
                "code": "TASK_FAILED", "message": "step load failed",
                "context": {"exit_code": 4, "stderr": f"loading...\n{line}\n"}}}
        return {"id": sub_id, "state": "FAILED", "error": {
            "code": "TASK_FAILED", "message": "step extract failed",
            "context": {"exit_code": 1, "stderr": "boom"}}}


@pytest_asyncio.fixture
async def world(client, monkeypatch, _acl_store):
    """A bearer world: ``lib1`` owned by alice (bob may read it), versions
    1 (chunks), 2 (chunks, the latest chunk version), 3 (a tombstone)."""
    monkeypatch.setattr(security.settings, "identity_provider", "bvbrc")
    set_identity_provider(_Provider())
    workspace = FakeWorkspace()
    workspace.versions = {1: _manifest(1), 2: _manifest(2), 3: _manifest(3, tombstone=True)}
    gowe = FakeGoWe()
    spec = CollectionSpec(id="lib1", label="lib1", owner=ALICE, collection="lib1_phys",
                          embedding_model="test-model", embedding_model_dim=4,
                          chunk_method="fixed_token", chunk_size=256, chunk_overlap=32)
    store = InMemoryCollectionStore([spec])
    prior_store = getattr(app.state, "collection_store", None)
    app.state.collection_store = store
    runner = GraphExtractRunner(
        app.state.job_store, store, workspace=workspace, gowe=gowe,
        cwl_path=REPO / "cwl" / "graph-extract.cwl", static_inputs={
            "llm_endpoint": "http://llm.test", "llm_model": "m", "neo4j_uri": "bolt://g.test",
            "graph_backend": "neo4j"},
        poll_interval=0, timeout=5, output_wait_timeout=5, concurrency=4, max_triples=200_000,
    )
    app.state.graph_extract = runner
    entry = CollectionEntry(
        id="lib1", label="lib1", collection="lib1_phys", model="test-model", dim=4,
        chunk_method="fixed_token", chunk_size=256, chunk_overlap=32, chunk_params={},
        is_shared_surface=False, retriever=None, vector_store=app.state.vector_store,
        text_index=app.state.text_index, owner=ALICE,
    )
    app.state.collections.add(entry)
    await _acl_store.grant("lib1", GRANTEE_USER, ALICE, PERM_OWNER, granted_by="system:test")
    await _acl_store.grant("lib1", GRANTEE_USER, BOB, PERM_READ, granted_by=ALICE)
    try:
        yield SimpleNamespace(workspace=workspace, gowe=gowe, store=store, runner=runner,
                              spec=spec)
    finally:
        gowe.release()
        await runner.drain()
        reset_identity_provider()
        app.state.collections.remove("lib1")
        for attr in ("graph_extract", "collection_store"):
            if hasattr(app.state, attr):
                delattr(app.state, attr)
        if prior_store is not None:
            app.state.collection_store = prior_store


async def _post(client, who: str | None = "alice", cid: str = "lib1", **params):
    headers = _auth(who) if who else {}
    return await client.post(f"/v1/collections/{cid}/graph", params=params, headers=headers)


def _job_store_dump() -> str:
    js = app.state.job_store
    return json.dumps([j.model_dump() for j in js._jobs.values()])


# --------------------------------------------------------------------------- #
# authorization
# --------------------------------------------------------------------------- #


async def test_non_owner_is_403_and_unknown_is_404(client, world):
    r = await _post(client, "bob")
    assert r.status_code == 403, r.text
    r = await _post(client, "alice", cid="nope")
    assert r.status_code == 404
    assert world.gowe.submissions == [] and world.workspace.calls == []


async def test_keyless_and_api_key_callers_are_401(client, world, monkeypatch):
    r = await _post(client, None)
    assert r.status_code == 401, r.text
    monkeypatch.setattr(security.settings, "api_keys", ["k-1"])
    monkeypatch.setattr(security.settings, "api_key_tenants", {"k-1": "kt"})
    r = await client.post("/v1/collections/lib1/graph", headers={"X-API-Key": "k-1"})
    assert r.status_code == 401, r.text
    assert "BV-BRC user token" in r.json()["detail"]
    # And it says nothing about the id: an unknown one is 401 too, not 404.
    r = await client.post("/v1/collections/nope/graph", headers={"X-API-Key": "k-1"})
    assert r.status_code == 401
    assert world.gowe.submissions == [] and world.workspace.calls == []


async def test_no_runner_is_503_and_dormant_is_409(client, world):
    await world.store.set_state("lib1", DORMANT, reason="evicted")
    r = await _post(client)
    assert r.status_code == 409 and "restore it first" in r.json()["detail"]
    await world.store.set_state("lib1", "active")
    del app.state.graph_extract
    r = await _post(client)
    assert r.status_code == 503 and "not configured" in r.json()["detail"]
    assert world.gowe.submissions == []


# --------------------------------------------------------------------------- #
# the submission
# --------------------------------------------------------------------------- #


async def test_submits_the_latest_chunk_version_as_the_user_and_records_on_delivery(
    client, world, caplog,
):
    caplog.set_level(logging.DEBUG)
    r = await _post(client)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["collection_id"] == "lib1" and body["version"] == 2  # 3 is a tombstone
    assert body["job_id"] and body["submission_id"] == "sub_1"
    assert set(body) == {"collection_id", "version", "job_id", "submission_id", "message"}

    # ONE submission, as the user: the token is the per-call argument of every
    # engine call and nowhere in the inputs.
    sub = world.gowe.submissions[0]
    assert {tok for _, tok in world.gowe.calls} == {TOKENS["alice"]}
    inputs = sub["inputs"]
    assert TOKENS["alice"] not in json.dumps(inputs) and "SECRETSIG" not in json.dumps(sub)
    assert inputs["version_dir"] == {"class": "Directory",
                                     "location": ws_uri(f"{FOLDER}/versions/2")}
    assert inputs["version"] == "2" and inputs["collection_id"] == "lib1"
    assert inputs["spec_hash"] == world.spec.spec_hash() and inputs["tenant"] == ALICE
    assert inputs["max_triples"] == 200_000 and inputs["concurrency"] == 4
    assert inputs["job_id"] == body["job_id"]
    assert inputs["llm_endpoint"] == "http://llm.test" and inputs["neo4j_uri"] == "bolt://g.test"
    assert sub["output_destination"] == VERSIONS  # the OWNER's versions/ folder
    # The Workspace was read as the user: the listing, then the newest
    # manifests until a chunk version (3 is a tombstone, 2 is it).
    assert [c[1:] for c in world.workspace.calls] == [
        ("ls", FOLDER), ("read", f"{FOLDER}/versions/3/manifest.json"),
        ("read", f"{FOLDER}/versions/2/manifest.json")]
    assert all(tok == TOKENS["alice"] for tok, *_ in world.workspace.calls)

    # The job: kind graph, this collection, the caller's tenant.
    job = await app.state.job_store.get(body["job_id"])
    assert job.kind == KIND_GRAPH and job.collection_id == "lib1" and job.tenant_id == ALICE
    assert job.source == "graph-extract:lib1@2"
    poll = await client.get(f"/v1/ingest/{body['job_id']}", headers=_auth("alice"))
    assert poll.status_code == 200 and poll.json()["status"] in ("running", "completed")
    assert "kind" not in poll.json()  # the job-status shape is unchanged

    await world.runner.drain()
    assert world.gowe.waits == [True]  # delivery required, not just COMPLETED
    job = await app.state.job_store.get(body["job_id"])
    assert job.status == "completed" and job.archive_ref == VERSIONS + "2"
    assert (await world.store.get("lib1")).graph_archived_versions == [2]
    assert TOKENS["alice"] not in caplog.text and "SECRETSIG" not in caplog.text
    assert TOKENS["alice"] not in _job_store_dump()
    for rec in caplog.records:
        assert TOKENS["alice"] not in rec.getMessage()


async def test_version_query_selects_an_older_version_and_refuses_bad_ones(client, world):
    r = await _post(client, version=1)
    assert r.status_code == 202 and r.json()["version"] == 1
    assert world.gowe.submissions[0]["inputs"]["version"] == "1"
    await world.runner.drain()
    assert (await world.store.get("lib1")).graph_archived_versions == [1]
    r = await _post(client, version=3)
    assert r.status_code == 400 and "tombstone" in r.json()["detail"]
    r = await _post(client, version=9)
    assert r.status_code == 400 and "not in the archive" in r.json()["detail"]
    assert len(world.gowe.submissions) == 1


async def test_idempotent_per_version(client, world):
    r1 = await _post(client)
    assert r1.status_code == 202 and r1.json()["job_id"]
    await world.runner.drain()
    r2 = await _post(client)
    assert r2.status_code == 202, r2.text
    assert r2.json()["job_id"] is None and r2.json()["submission_id"] is None
    assert r2.json()["version"] == 2 and "already" in r2.json()["message"]
    assert len(world.gowe.submissions) == 1
    # The archive says the leg exists but the row does not know (extracted
    # elsewhere, or a watcher that died): a no-op that repairs the row.
    world.workspace.versions[1]["graph"] = True
    r3 = await _post(client, version=1)
    assert r3.status_code == 202 and r3.json()["job_id"] is None
    assert (await world.store.get("lib1")).graph_archived_versions == [2, 1]
    assert len(world.gowe.submissions) == 1


async def test_second_in_flight_extraction_per_owner_is_429_with_retry_after(client, world):
    world.gowe.hold = True
    r1 = await _post(client)
    assert r1.status_code == 202
    r2 = await _post(client, version=1)
    assert r2.status_code == 429, r2.text
    assert r2.headers["Retry-After"] == "30"
    assert "still in flight" in r2.json()["detail"]
    assert len(world.gowe.submissions) == 1
    # Bob (a reader) is 403, not 429 — the guard runs after authorization.
    assert (await _post(client, "bob")).status_code == 403
    world.gowe.release()
    await world.runner.drain()
    # The slot frees itself on completion.
    r3 = await _post(client, version=1)
    assert r3.status_code == 202 and r3.json()["job_id"]
    assert len(world.gowe.submissions) == 2


async def test_an_active_graph_job_does_not_block_uploads(client, world):
    """The upload guard counts ingest jobs only (#377's one-in-flight rule);
    a multi-hour extraction must not freeze the owner's uploads."""
    world.gowe.hold = True
    r = await _post(client)
    assert r.status_code == 202
    js = app.state.job_store
    assert await js.count_active(ALICE, kind=KIND_GRAPH) == 1
    assert await js.count_active(ALICE, kind=KIND_INGEST) == 0
    assert await js.count_active(ALICE) == 1
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(job_store=js)))
    principal = security.Principal(tenant=ALICE, role="user", token=TOKENS["alice"],
                                   issuer="bvbrc", subject=SUBJECTS["alice"])
    await single_inflight_ingest(request, principal)  # no 429
    ingest = await js.create("upload", tenant_id=ALICE, collection_id="lib1")
    with pytest.raises(HTTPException) as ei:
        await single_inflight_ingest(request, principal)
    assert ei.value.status_code == 429
    await js.update(ingest.job_id, status="completed")
    world.gowe.release()


# --------------------------------------------------------------------------- #
# terminal outcomes
# --------------------------------------------------------------------------- #


async def test_upload_failed_is_output_staging_failed_and_records_nothing(client, world):
    world.gowe.outcome = "upload_failed"
    r = await _post(client)
    assert r.status_code == 202
    await world.runner.drain()
    job = await app.state.job_store.get(r.json()["job_id"])
    assert job.status == "failed" and job.error == OUTPUT_STAGING_FAILED
    assert job.archive_ref == ""
    assert (await world.store.get("lib1")).graph_archived_versions == []


async def test_load_refused_at_the_cap_is_graph_cap_exceeded(client, world):
    world.gowe.outcome = "cap"
    r = await _post(client)
    assert r.status_code == 202
    await world.runner.drain()
    job = await app.state.job_store.get(r.json()["job_id"])
    assert job.status == "failed" and job.error == GRAPH_CAP_EXCEEDED
    assert (await world.store.get("lib1")).graph_archived_versions == []
    world.gowe.outcome = "failed"
    r = await _post(client)
    await world.runner.drain()
    job = await app.state.job_store.get(r.json()["job_id"])
    assert job.status == "failed" and job.error == "gowe submission FAILED"


async def test_engine_refusing_the_submission_is_502_with_the_job_failed(client, world):
    world.gowe.refuse_submit = True
    r = await _post(client)
    assert r.status_code == 502, r.text
    assert TOKENS["alice"] not in r.text
    jobs = list(app.state.job_store._jobs.values())
    assert len(jobs) == 1 and jobs[0].status == "failed" and jobs[0].error == "GoWeError"


async def test_missing_archive_and_no_owner_subject_are_400(client, world):
    world.workspace.missing = True
    r = await _post(client)
    assert r.status_code == 400 and "archive folder missing" in r.json()["detail"]
    world.workspace.missing = False
    await world.store.put(world.spec.model_copy(update={"owner": ""}))
    r = await _post(client)
    assert r.status_code == 400 and "no owner subject" in r.json()["detail"]
    assert world.gowe.submissions == [] and app.state.job_store._jobs == {}


async def test_manifest_read_path_is_under_the_owners_versions_folder():
    assert ws_path(VERSIONS + "2") == f"{FOLDER}/versions/2"
