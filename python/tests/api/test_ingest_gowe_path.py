"""``ingest_backend=gowe``: user ingest submits to GoWe AS THE USER (#203 2a).

Fake GoWe engine (``httpx.MockTransport``) + fake Workspace; nothing live. The
fakes model what the engine's post-staging really does: a submission is marked
COMPLETED and post-staged in the same scheduler tick, so polls observe
``COMPLETED`` with ``output_state`` ``""`` → ``uploading`` → ``delivered`` (or
``upload_failed``); the workflow's only output is the ``archive`` Directory,
delivered to ``<output_destination>/<version>/``; the per-item receipts are
read from ``receipt.json`` inside it through the Workspace with the caller's
token — nothing is downloaded from the engine.

Pins the seam: upload → one ``upload_source`` per file with the caller's token
→ ONE submission whose inputs are the ``ws://`` sources, whose ``Authorization``
header is the caller's token and whose ``output_destination`` is the
collection's ``versions/`` folder under the caller's home; ``version``
increments across jobs (one registry read + one registry write per job — the
whole per-job cost, so no perf budget beyond the call-count assertion); the
archive location lands on the job, only after delivery. The token appears in NO
log line (DEBUG caplog), NO job-store field and NO exception text — for a
transport failure and for an engine that echoes the header back in a 401 body.
A keyless / API-key principal is refused with 401; a non-Workspace source with
400; the default (unregistered) collection with 400; any backend that is
neither local nor gowe still 501s. ``ingest_backend=local`` is covered
unchanged by the existing tests.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio

from ragstack.acl_store import GRANTEE_USER, PERM_OWNER
from ragstack.api import security
from ragstack.api.collections import CollectionEntry
from ragstack.api.main import app
from ragstack.collection_store import CollectionSpec, InMemoryCollectionStore
from ragstack.identity import (
    Identity,
    IdentityInvalid,
    reset_identity_provider,
    set_identity_provider,
)
from ragstack.ingestion.gowe_backend import OUTPUT_STAGING_FAILED, GoWeBackend
from ragstack.ingestion.gowe_client import GoWeClient
from ragstack.ingestion.receipts import COMPLETED, ShardReceipt
from ragstack.workspace import (
    ARCHIVE_FORMAT,
    WorkspaceAuthError,
    WorkspaceClient,
    WorkspaceError,
    WorkspaceExists,
    WorkspaceNotFound,
    WorkspaceTooLarge,
    collection_folder,
    ws_path,
    ws_uri,
)
from tests.workspace_support import WS_URL as WS_SERVICE_URL
from tests.workspace_support import FakeWorkspace as WorkspaceService

# Dead-but-distinctive store addresses the `gowe` fixture pins settings to (#407).
QDRANT_UNDER_TEST = "http://127.0.0.1:1/qdrant-under-test"
ES_UNDER_TEST = "http://127.0.0.1:1/es-under-test"
ROUTED_UNDER_TEST = "http://127.0.0.1:1/routed-qdrant-under-test"
ROUTED_ES_UNDER_TEST = "http://127.0.0.1:1/routed-es-under-test"

TOKEN = "un=alice@patricbrc.org|tokenid=t-1|expiry=9999999999|sig=SECRETSIGNATURE"
SUBJECT = "alice@patricbrc.org"
TENANT = f"bvbrc:{SUBJECT}"
HOME = f"ws:///{SUBJECT}/home/"
VERSIONS = HOME + ".ragstack/collections/lib1/versions/"
_FIXTURE = Path(__file__).resolve().parents[3] / "contracts" / "fixtures" / "documents" / "sample_small.pdf"


def _pdf() -> bytes:
    return _FIXTURE.read_bytes()


class _Provider:
    async def authenticate(self, credential: str) -> Identity:
        if credential != TOKEN:
            raise IdentityInvalid("no")
        return Identity(subject=SUBJECT, issuer="bvbrc", token_id="t-1",
                        expires_at=int(time.time()) + 3600)


class FakeEngine:
    """GoWe REST fake with two-phase completion: every poll of a submission
    reports COMPLETED, and ``output_state`` walks "" → uploading → delivered
    (or → upload_failed). The only output is the archive Directory."""

    def __init__(self) -> None:
        self.submissions: list[dict[str, Any]] = []
        self.auth: list[str | None] = []
        self.polls: list[tuple[str, str, str]] = []  # (sub id, state, output_state)
        self.events: list[str] = []  # shared timeline with the fake Workspace
        self.fail: str | None = None  # "transport" | "echo-401" | "upload_failed"

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.auth.append(req.headers.get("Authorization"))
        if self.fail == "transport":
            raise httpx.ConnectError("refused", request=req)
        if self.fail == "echo-401":
            return httpx.Response(401, text=f"bad token: {req.headers.get('Authorization')}")
        p = req.url.path
        if req.method == "POST" and p == "/api/v1/workflows":
            return httpx.Response(201, json={"data": {"id": "wf_1"}})
        if req.method == "POST" and p == "/api/v1/submissions":
            body = json.loads(req.content)
            body["_auth"] = req.headers.get("Authorization")
            body["_polls"] = 0
            self.submissions.append(body)
            sid = f"sub_{len(self.submissions)}"
            return httpx.Response(201, json={"data": {"id": sid, "state": "PENDING"}})
        if req.method == "GET" and p.startswith("/api/v1/submissions/"):
            sid = p.rsplit("/", 1)[1]
            body = self.submissions[int(sid.rsplit("_", 1)[1]) - 1]
            body["_polls"] += 1
            n = body["_polls"]
            final = "upload_failed" if self.fail == "upload_failed" else "delivered"
            output_state = {1: "", 2: "uploading"}.get(n, final)
            self.polls.append((sid, "COMPLETED", output_state))
            self.events.append(f"poll:{output_state}")
            version = body["inputs"]["version"]
            outputs = {"archive": {"class": "Directory", "location": f"file:///w/{sid}/{version}"}}
            return httpx.Response(200, json={"data": {"id": sid, "state": "COMPLETED",
                                                       "output_state": output_state,
                                                       "outputs": outputs}})
        if req.method == "GET" and p == "/api/v1/files/download":
            raise AssertionError("the user path must not download from the engine")
        return httpx.Response(404, text="unexpected")

    def receipts_for(self, version: str) -> list[dict[str, Any]]:
        for body in self.submissions:
            if body["inputs"]["version"] == version:
                return [
                    json.loads(ShardReceipt(f["location"], "public", COMPLETED, n_docs=1,
                                            n_chunks=2, chunk_ids=[f"{f['location']}#0",
                                                                    f"{f['location']}#1"]).to_json())
                    for f in body["inputs"]["pdfs"]
                ]
        raise WorkspaceNotFound(f"no submission wrote version {version}")


class FakeWorkspace:
    """Records ensure_collection_folder / upload_source calls; consumes streams;
    serves ``versions/<n>/receipt.json`` from what the fake engine 'delivered'."""

    def __init__(self, engine: FakeEngine) -> None:
        self.engine = engine
        self.folders: list[tuple[str, str, str, str, str]] = []
        self.uploads: list[dict[str, Any]] = []
        self.reads: list[tuple[str, str]] = []
        self.existing: set[str] = set()  # filenames already in sources/
        self.empty_receipts = False

    async def ensure_collection_folder(self, token, subject, collection_id, *, spec_hash, tenant):
        self.folders.append((token, subject, collection_id, spec_hash, tenant))
        return ws_uri(collection_folder(subject, collection_id))

    async def upload_source(self, token, folder, filename, stream, *, max_bytes, size=None):
        if filename in self.existing:
            raise WorkspaceExists(f"{ws_path(folder)}/{filename}")
        if size is not None and size > max_bytes:
            raise WorkspaceTooLarge(filename, max_bytes)
        n = 0
        while chunk := await stream.read(1 << 16):
            n += len(chunk)
            if n > max_bytes:
                raise WorkspaceTooLarge(filename, max_bytes)
        self.uploads.append({"token": token, "folder": folder, "filename": filename,
                             "size": size, "bytes": n})
        return ws_uri(f"{ws_path(folder)}/{filename}")

    async def read_file(self, token, path):
        self.reads.append((token, path))
        self.engine.events.append("read")
        assert path.startswith(ws_path(VERSIONS)) and path.endswith("/receipt.json"), path
        version = path[len(ws_path(VERSIONS)) + 1:].split("/")[0]
        if self.empty_receipts:
            return b"[]"
        receipts = self.engine.receipts_for(version)
        # archive.py copies a single receipt verbatim (an object), else an array.
        return json.dumps(receipts[0] if len(receipts) == 1 else receipts).encode()


class CountingStore:
    """InMemoryCollectionStore with per-method call counters (the per-job cost)."""

    def __init__(self, inner: InMemoryCollectionStore) -> None:
        self._inner = inner
        self.calls: dict[str, int] = {}

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        async def _counted(*a, **kw):
            self.calls[name] = self.calls.get(name, 0) + 1
            return await attr(*a, **kw)
        return _counted


@pytest_asyncio.fixture
async def gowe(client, monkeypatch, _acl_store):
    """ingest_backend=gowe over a fake engine + fake Workspace, a bearer BV-BRC
    identity, and one registered collection ``lib1`` owned by that identity."""
    from ragstack.config import settings

    monkeypatch.setattr(settings, "ingest_backend", "gowe")
    monkeypatch.setattr(security.settings, "identity_provider", "bvbrc")
    set_identity_provider(_Provider())
    # #407: the API seeds the submission's store targets from ITS OWN settings.
    # Distinctive and DEAD (port 1, a path no real store carries) so an assertion
    # on them cannot be satisfied by an ambient default, and so a regression that
    # actually dialled them would fail to connect rather than reach a live store.
    monkeypatch.setattr(settings, "qdrant_url", QDRANT_UNDER_TEST)
    monkeypatch.setattr(settings, "elasticsearch_url", ES_UNDER_TEST)
    monkeypatch.setattr(settings, "qdrant_collection_routes", {})
    monkeypatch.setattr(settings, "es_collection_routes", {})

    engine = FakeEngine()
    workspace = FakeWorkspace(engine)
    http = httpx.AsyncClient(transport=httpx.MockTransport(engine))
    backend = GoWeBackend(
        GoWeClient("http://gowe.test", token="", http=http),
        "cwlVersion: v1.2\n", workflow_name="ragstack-pdf-ingest-scatter",
        # `qdrant_url` here is what an operator's GOWE_WORKFLOW_INPUTS_JSON used to
        # be able to set. Constructed directly (not through make_ingest_backend,
        # which now refuses these keys outright) so this fixture can assert the
        # SECOND, independent guarantee: even if the blob carries a store target,
        # the per-run seed wins the merge. `worker_scratch` is the control — a
        # genuine extra, which must still pass through.
        static_inputs={"embedding_url": ["http://emb.test/v1"],
                       "qdrant_url": "http://evil:6333",
                       "worker_scratch": "/scratch/under-test"},
        shards_input_key="pdfs", receipts_output_key="receipts", poll_interval=0, timeout=5,
        output_wait_timeout=5,
    )
    app.state.ingest_backend = backend
    app.state.workspace = workspace

    spec = CollectionSpec(id="lib1", label="lib1", owner=TENANT, collection="lib1_phys",
                          embedding_model="test-model", embedding_model_dim=4,
                          embedding_endpoints=["http://emb.lib1/v1"],
                          chunk_method="fixed_token", chunk_size=256, chunk_overlap=32)
    store = CountingStore(InMemoryCollectionStore([spec]))
    prior_store = getattr(app.state, "collection_store", None)
    app.state.collection_store = store
    entry = CollectionEntry(
        id="lib1", label="lib1", collection="lib1_phys", model="test-model", dim=4,
        chunk_method="fixed_token", chunk_size=256, chunk_overlap=32, chunk_params={},
        is_shared_surface=False, retriever=None, vector_store=app.state.vector_store,
        text_index=app.state.text_index, embedding_endpoints=["http://emb.lib1/v1"],
        owner=TENANT,
    )
    app.state.collections.add(entry)
    await _acl_store.grant("lib1", GRANTEE_USER, TENANT, PERM_OWNER, granted_by="system:test")
    try:
        yield {"engine": engine, "workspace": workspace, "store": store, "spec": spec,
               "backend": backend}
    finally:
        reset_identity_provider()
        app.state.collections.remove("lib1")
        for attr in ("ingest_backend", "workspace", "collection_store"):
            if hasattr(app.state, attr):
                delattr(app.state, attr)
        if prior_store is not None:
            app.state.collection_store = prior_store
        await http.aclose()


AUTH = {"Authorization": f"Bearer {TOKEN}"}


async def _upload(client, *names: str, collection: str | None = "lib1", headers=AUTH,
                  content: bytes | None = None):
    files = [("files", (n, content if content is not None else _pdf(), "application/pdf"))
             for n in names]
    data = {"collection": collection} if collection else {}
    return await client.post("/v1/ingest/upload", files=files, data=data, headers=headers)


def _job_store_dump() -> str:
    js = app.state.job_store
    return json.dumps(
        [j.model_dump() for j in js._jobs.values()]
        + [i.model_dump() for b in js._items.values() for i in b.values()]
    )


@pytest.mark.asyncio
async def test_upload_writes_sources_then_submits_once_as_the_user(client, gowe):
    r = await _upload(client, "a.pdf", "b.pdf")
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    ws, engine, store, spec = gowe["workspace"], gowe["engine"], gowe["store"], gowe["spec"]

    # Folder stamped for THIS collection build, with the caller's token.
    assert ws.folders == [(TOKEN, SUBJECT, "lib1", spec.spec_hash(), TENANT)]
    # One Workspace write per file, into <collection>/sources/, with size → Content-Length.
    src_folder = f"/{SUBJECT}/home/.ragstack/collections/lib1/sources"
    assert [(u["token"], u["folder"], u["filename"]) for u in ws.uploads] == [
        (TOKEN, src_folder, "a.pdf"), (TOKEN, src_folder, "b.pdf")]
    assert all(u["size"] == len(_pdf()) == u["bytes"] for u in ws.uploads)

    # ONE submission, as the user, with ws:// inputs and the versions/ destination.
    assert len(engine.submissions) == 1
    sub = engine.submissions[0]
    assert sub["_auth"] == TOKEN
    assert set(engine.auth) == {TOKEN}  # every engine request of the run
    assert sub["inputs"]["pdfs"] == [
        {"class": "File", "location": f"ws://{src_folder}/a.pdf"},
        {"class": "File", "location": f"ws://{src_folder}/b.pdf"},
    ]
    assert sub["output_destination"] == VERSIONS
    inputs = sub["inputs"]
    assert inputs["version"] == "1" and inputs["collection_id"] == "lib1"
    assert inputs["spec_hash"] == spec.spec_hash() and inputs["job_id"] == job_id
    assert inputs["tenant"] == TENANT and inputs["collection"] == "lib1_phys"
    assert inputs["es_index"] == "lib1_phys" and inputs["embedding_model"] == "test-model"
    assert inputs["embedding_url"] == ["http://emb.lib1/v1"]  # the entry's, over static
    # #407: store targets are seeded PER RUN from settings, and the per-run input
    # wins GoWeBackend's `{**static_inputs, **inputs}` merge — so the fixture's
    # static `http://evil:6333` cannot redirect this ingest. Before the fix the
    # submission carried no store URLs at all and the CWL's own production
    # defaults decided; a dev-tenant ingest wrote to the production instances.
    assert inputs["qdrant_url"] == QDRANT_UNDER_TEST
    assert inputs["es_url"] == ES_UNDER_TEST
    # Control: the blob is still honoured for keys the API does not own.
    assert inputs["worker_scratch"] == "/scratch/under-test"
    assert (inputs["chunk_method"], inputs["chunk_size"], inputs["chunk_overlap"]) == (
        "fixed_token", 256, 32)
    assert "shards" not in inputs

    # Receipts read from the DELIVERED archive as the user, mapped per item;
    # archive location recorded on the job (#358's hook).
    assert ws.reads == [(TOKEN, ws_path(VERSIONS + "1") + "/receipt.json")]
    poll = await client.get(f"/v1/ingest/{job_id}", headers=AUTH)
    assert poll.status_code == 200 and poll.json()["status"] == "completed"
    assert poll.json()["items"] == {"total": 2, "completed": 2, "failed": 0, "pending": 0}
    assert "archive_ref" not in poll.json()  # contract unchanged
    job = await app.state.job_store.get(job_id)
    assert job.archive_ref == VERSIONS + "1"
    assert job.tenant_id == TENANT and job.source == "upload"

    # The per-job registry cost: one read (spec_hash) + one write (next_version)
    # + one write on delivery (append_version: the list restore replays, #358).
    assert store.calls == {"get": 1, "next_version": 1, "append_version": 1}
    row = await store._inner.get("lib1")
    assert row.versions == [1] and row.archive_pending is False


@pytest.mark.asyncio
async def test_completed_is_not_done_until_delivered(client, gowe):
    """The engine reports COMPLETED with output_state "" (then uploading) before
    the archive exists; the run keeps polling and only reads the receipts —
    and only finishes — once it is delivered."""
    r = await _upload(client, "a.pdf")
    assert r.status_code == 202
    engine = gowe["engine"]
    assert [s for _, _, s in engine.polls] == ["", "uploading", "delivered"]
    assert engine.events == ["poll:", "poll:uploading", "poll:delivered", "read"]
    job = await app.state.job_store.get(r.json()["job_id"])
    assert job.status == "completed" and job.archive_ref == VERSIONS + "1"
    assert job.chunk_ids == [f"ws:///{SUBJECT}/home/.ragstack/collections/lib1/sources/a.pdf#0",
                             f"ws:///{SUBJECT}/home/.ragstack/collections/lib1/sources/a.pdf#1"]


@pytest.mark.asyncio
async def test_upload_failed_is_output_staging_failed(client, gowe):
    gowe["engine"].fail = "upload_failed"
    r = await _upload(client, "a.pdf", "b.pdf")
    assert r.status_code == 202
    job = await app.state.job_store.get(r.json()["job_id"])
    assert job.status == "failed" and job.error == OUTPUT_STAGING_FAILED
    assert job.archive_ref == ""  # nothing was delivered
    assert gowe["workspace"].reads == []
    poll = await client.get(f"/v1/ingest/{job.job_id}", headers=AUTH)
    # The load happened but was never reported: items stay pending, not failed.
    assert poll.json()["items"] == {"total": 2, "completed": 0, "failed": 0, "pending": 2}
    # #358: the registry row says the archive is missing — the reserved
    # version is NOT recorded (restore must not replay a folder that does not
    # exist) and archive_pending blocks eviction until #353's retry re-archives.
    row = await gowe["store"]._inner.get("lib1")
    assert row.versions == [] and row.archive_pending is True
    assert gowe["store"].calls.get("append_version", 0) == 0
    assert gowe["store"].calls.get("set_archive_pending") == 1


@pytest.mark.asyncio
async def test_version_increments_across_jobs(client, gowe):
    r1 = await _upload(client, "a.pdf")
    r2 = await _upload(client, "b.pdf")
    assert (r1.status_code, r2.status_code) == (202, 202)
    versions = [s["inputs"]["version"] for s in gowe["engine"].submissions]
    assert versions == ["1", "2"]
    js = app.state.job_store
    refs = [(await js.get(r.json()["job_id"])).archive_ref for r in (r1, r2)]
    assert refs == [VERSIONS + "1", VERSIONS + "2"]
    assert gowe["store"].calls == {"get": 2, "next_version": 2, "append_version": 2}
    assert (await gowe["store"]._inner.get("lib1")).versions == [1, 2]


# --- #407: the API seeds the store targets, per run, from its own settings --- #

@pytest.mark.asyncio
async def test_ingest_submission_carries_the_api_settings_store_urls(client, gowe):
    """``POST /v1/ingest`` (the ws:// reference leg), not just upload.

    Both legs build their inputs through ``_gowe_inputs``, and this asserts the
    seeding on the leg the upload test does not cover — the two call sites are
    why the seed lives in that one helper."""
    r = await client.post("/v1/ingest",
                          json={"source": f"ws:///{SUBJECT}/home/papers/x.pdf",
                                "collection": "lib1"}, headers=AUTH)
    assert r.status_code == 200, r.text
    inputs = gowe["engine"].submissions[0]["inputs"]
    assert inputs["qdrant_url"] == QDRANT_UNDER_TEST
    assert inputs["es_url"] == ES_UNDER_TEST


# --- #563: …and WHICH REGISTRY, by name, alongside them --------------------- #

@pytest.mark.asyncio
async def test_no_registry_is_sent_when_the_setting_is_unset(client, gowe):
    """The default is silence, and silence is the pre-#563 contract: the worker
    uses its own unsuffixed COLLECTION_STORE_* variables. A deployment that has
    not adopted the convention — the `dev` tenant, right through the transition
    — must submit exactly what it submitted before."""
    r = await _upload(client, "a.pdf")
    assert r.status_code == 202, r.text
    assert "registry" not in gowe["engine"].submissions[0]["inputs"]


@pytest.mark.asyncio
async def test_the_registry_name_is_seeded_per_job(client, gowe, monkeypatch):
    """The last piece of a tenant's physical state that did not travel on the
    submission. The worker resolved WHICH registry from its own process
    environment, set once per worker GROUP — so the shared group served exactly
    one tenant's registry, and every `hackathon` ingest resolved against the dev
    tenant's database and died after extract had already succeeded."""
    from ragstack.config import settings

    monkeypatch.setattr(settings, "collection_registry_name", "hackathon")
    r = await _upload(client, "a.pdf")
    assert r.status_code == 202, r.text
    assert gowe["engine"].submissions[0]["inputs"]["registry"] == "hackathon"


@pytest.mark.asyncio
async def test_the_submission_never_carries_a_registry_credential(
    client, gowe, monkeypatch
):
    """A NAME, and only a name. ``GET /api/v1/submissions/{id}`` returns both
    ``inputs`` and ``submitted_inputs``, and ``submitted_inputs`` is an
    IMMUTABLE snapshot — rendered by the UI, returned again on every task
    record, stored in the engine's SQLite in plaintext (only provider tokens are
    encrypted). A DSN placed there could never be withdrawn. So even with the
    API's own registry sitting on a credentialled postgres, nothing about that
    credential may appear anywhere in what is submitted."""
    import json

    from ragstack.config import settings

    dsn = "postgresql+asyncpg://ragstack:hunter2@db.internal/ragstack"
    monkeypatch.setattr(settings, "collection_registry_name", "hackathon")
    monkeypatch.setattr(settings, "collection_store_backend", "postgres")
    monkeypatch.setattr(settings, "collection_store_dsn", dsn)
    monkeypatch.setattr(settings, "postgres_dsn", dsn)

    r = await _upload(client, "a.pdf")
    assert r.status_code == 202, r.text
    submitted = json.dumps(gowe["engine"].submissions[0])
    assert "hackathon" in submitted          # the name did travel …
    assert "hunter2" not in submitted        # … and nothing else did
    assert "postgres" not in submitted
    assert dsn not in submitted


@pytest.mark.asyncio
async def test_a_routed_collection_is_ingested_to_its_routed_instance(
    client, gowe, monkeypatch
):
    """A collection listed in ``qdrant_collection_routes`` lives on its OWN
    Qdrant instance, and the ingest must write THERE.

    The route keys on the PHYSICAL collection name (``lib1_phys``), the same key
    the API's own store construction uses — seeding the bare ``qdrant_url``
    instead would build a second, invisible copy of a store that already exists
    on the routed instance. This is the deliberate divergence from the restore
    path, which still seeds the unrouted URL (tracked as a follow-up).

    The vector leg alone is routed here: ``es_collection_routes`` is empty, so
    ``es_url`` stays the bare setting. One leg routed and the other not is a
    legitimate configuration — the two tables are independent — and pinning it
    keeps the legs from being wired to one another."""
    from ragstack.config import settings

    monkeypatch.setattr(settings, "qdrant_collection_routes",
                        {"lib1_phys": ROUTED_UNDER_TEST})
    r = await _upload(client, "a.pdf")
    assert r.status_code == 202, r.text
    inputs = gowe["engine"].submissions[0]["inputs"]
    assert inputs["qdrant_url"] == ROUTED_UNDER_TEST
    assert inputs["es_url"] == ES_UNDER_TEST


@pytest.mark.asyncio
async def test_a_routed_index_is_ingested_to_its_routed_cluster(
    client, gowe, monkeypatch
):
    """The text leg's twin of the test above (``ES_COLLECTION_ROUTES``).

    The worker must write the BM25 half where the API reads it. A bare
    ``elasticsearch_url`` here is #407 one leg over: the chunks land in the
    default cluster's copy of the index while every query hits the routed
    cluster, which stays empty — a silently half-ingested corpus rather than a
    connection error.

    The key is the PHYSICAL INDEX (``entry.es_index()``, sent on the same
    submission as ``es_index``), not the collection id ``lib1``."""
    from ragstack.config import settings

    monkeypatch.setattr(settings, "es_collection_routes",
                        {"lib1_phys": ROUTED_ES_UNDER_TEST})
    r = await _upload(client, "a.pdf")
    assert r.status_code == 202, r.text
    inputs = gowe["engine"].submissions[0]["inputs"]
    assert inputs["es_index"] == "lib1_phys"
    assert inputs["es_url"] == ROUTED_ES_UNDER_TEST
    # The vector leg is untouched by an ES route.
    assert inputs["qdrant_url"] == QDRANT_UNDER_TEST


@pytest.mark.asyncio
async def test_both_legs_route_independently(client, gowe, monkeypatch):
    """Vector and text routed to different instances on one submission — the
    configuration the tables exist for: a collection whose two halves live on
    shared instances that neither this tenant nor each other owns."""
    from ragstack.config import settings

    monkeypatch.setattr(settings, "qdrant_collection_routes",
                        {"lib1_phys": ROUTED_UNDER_TEST})
    monkeypatch.setattr(settings, "es_collection_routes",
                        {"lib1_phys": ROUTED_ES_UNDER_TEST})
    r = await _upload(client, "a.pdf")
    assert r.status_code == 202, r.text
    inputs = gowe["engine"].submissions[0]["inputs"]
    assert inputs["qdrant_url"] == ROUTED_UNDER_TEST
    assert inputs["es_url"] == ROUTED_ES_UNDER_TEST


@pytest.mark.asyncio
async def test_an_es_route_for_another_index_does_not_touch_this_one(
    client, gowe, monkeypatch
):
    """Discriminator for the ES test above, matching the Qdrant one: routes
    configured but naming a DIFFERENT index fall back to ``elasticsearch_url``.
    Without it, a seed that returned "the first route" would pass."""
    from ragstack.config import settings

    monkeypatch.setattr(settings, "es_collection_routes",
                        {"some_other_index": ROUTED_ES_UNDER_TEST})
    r = await _upload(client, "a.pdf")
    assert r.status_code == 202, r.text
    assert gowe["engine"].submissions[0]["inputs"]["es_url"] == ES_UNDER_TEST


@pytest.mark.asyncio
async def test_a_route_for_another_collection_does_not_touch_this_one(
    client, gowe, monkeypatch
):
    """Discriminator for the test above: with routes configured but naming a
    DIFFERENT collection, the seed falls back to ``qdrant_url``. Without this, a
    seed that blindly returned "the first route" would pass the routed test."""
    from ragstack.config import settings

    monkeypatch.setattr(settings, "qdrant_collection_routes",
                        {"some_other_phys": ROUTED_UNDER_TEST})
    r = await _upload(client, "a.pdf")
    assert r.status_code == 202, r.text
    assert gowe["engine"].submissions[0]["inputs"]["qdrant_url"] == QDRANT_UNDER_TEST


@pytest.mark.parametrize("source", [
    f"ws:///{SUBJECT}/home/papers/x.pdf", f"/{SUBJECT}/home/papers/x.pdf",
])
@pytest.mark.asyncio
async def test_workspace_reference_submits_directly_without_upload(client, gowe, source):
    r = await client.post("/v1/ingest", json={"source": source, "collection": "lib1"},
                          headers=AUTH)
    assert r.status_code == 200, r.text
    assert gowe["workspace"].uploads == [] and gowe["workspace"].folders == []
    sub = gowe["engine"].submissions[0]
    assert sub["_auth"] == TOKEN
    assert sub["inputs"]["pdfs"] == [{"class": "File",
                                      "location": f"ws:///{SUBJECT}/home/papers/x.pdf"}]
    assert sub["output_destination"] == VERSIONS
    poll = await client.get(f"/v1/ingest/{r.json()['job_id']}", headers=AUTH)
    assert poll.json()["status"] == "completed"
    assert poll.json()["items"]["completed"] == 1
    assert (await app.state.job_store.get(r.json()["job_id"])).archive_ref == VERSIONS + "1"


@pytest.mark.parametrize("source", ["/data/corpus/x.pdf", "relative/x.pdf", "ws:///alice",
                                    "/alice/other/x.pdf", "file:///etc/passwd"])
@pytest.mark.asyncio
async def test_non_workspace_source_is_400_and_never_submitted(client, gowe, source):
    r = await client.post("/v1/ingest", json={"source": source, "collection": "lib1"},
                          headers=AUTH)
    assert r.status_code == 400, r.text
    assert "Workspace reference" in r.json()["detail"]
    assert gowe["engine"].submissions == []
    assert gowe["store"].calls.get("next_version", 0) == 0  # refused before reserving


def _assert_token_nowhere(caplog, backend) -> None:
    assert TOKEN not in caplog.text
    assert "SECRETSIGNATURE" not in caplog.text
    assert TOKEN not in _job_store_dump()
    assert TOKEN not in json.dumps(vars(backend), default=str)
    assert TOKEN not in json.dumps(vars(backend.client), default=str)
    for rec in caplog.records:
        assert TOKEN not in rec.getMessage()
        if rec.exc_text:
            assert TOKEN not in rec.exc_text


@pytest.mark.asyncio
async def test_token_in_no_log_line_no_job_field_no_exception(client, gowe, caplog):
    caplog.set_level(logging.DEBUG)
    r = await _upload(client, "a.pdf", "b.pdf")
    assert r.status_code == 202
    # A failing run too (transport error → the exception text is what gets logged).
    gowe["engine"].fail = "transport"
    r2 = await _upload(client, "c.pdf")
    assert r2.status_code == 202
    job2 = await app.state.job_store.get(r2.json()["job_id"])
    assert job2.status == "failed" and job2.error == "GoWeError"
    assert any("gowe submission failed" in rec.getMessage() for rec in caplog.records)
    _assert_token_nowhere(caplog, gowe["backend"])


@pytest.mark.asyncio
async def test_engine_echoing_the_token_in_an_error_body_is_scrubbed(client, gowe, caplog):
    caplog.set_level(logging.DEBUG)
    gowe["engine"].fail = "echo-401"
    r = await _upload(client, "a.pdf")
    assert r.status_code == 202
    job = await app.state.job_store.get(r.json()["job_id"])
    assert job.status == "failed" and job.error == "GoWeError"
    logged = [rec.getMessage() for rec in caplog.records if "gowe submission failed" in rec.getMessage()]
    assert logged and "[token]" in logged[0] and "401" in logged[0]
    _assert_token_nowhere(caplog, gowe["backend"])


@pytest.mark.asyncio
async def test_delivered_archive_without_receipts_is_a_visible_job_failure(client, gowe):
    gowe["workspace"].empty_receipts = True
    r = await _upload(client, "a.pdf", "b.pdf")
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    poll = await client.get(f"/v1/ingest/{job_id}", headers=AUTH)
    assert poll.json()["status"] == "failed"
    job = await app.state.job_store.get(job_id)
    assert job.error == "GoWeContractError"
    # Not "every document failed": nothing was reported, so the items stay pending.
    assert poll.json()["items"] == {"total": 2, "completed": 0, "failed": 0, "pending": 2}
    assert job.archive_ref == ""


@pytest.mark.asyncio
async def test_api_key_principal_is_401_on_both_endpoints(client, gowe, monkeypatch):
    monkeypatch.setattr(security.settings, "api_keys", ["k-1"])
    key = {"X-API-Key": "k-1"}
    r = await _upload(client, "a.pdf", headers=key)
    assert r.status_code == 401, r.text
    assert "BV-BRC user token" in r.json()["detail"]
    r = await client.post("/v1/ingest", json={"source": f"ws:///{SUBJECT}/home/x.pdf",
                                              "collection": "lib1"}, headers=key)
    assert r.status_code == 401, r.text
    assert gowe["engine"].submissions == [] and gowe["workspace"].uploads == []


@pytest.mark.asyncio
async def test_keyless_principal_is_401(client, gowe):
    # No credential at all: identity is on but nothing was presented → keyless
    # default principal (no token) → refused before any Workspace/engine call.
    r = await _upload(client, "a.pdf", headers={})
    assert r.status_code == 401, r.text
    assert gowe["engine"].submissions == [] and gowe["workspace"].uploads == []


@pytest.mark.asyncio
async def test_default_collection_has_no_registry_row_400(client, gowe):
    r = await _upload(client, "a.pdf", collection=None)
    assert r.status_code == 400, r.text
    assert "registered collection" in r.json()["detail"]
    assert gowe["workspace"].uploads == [] and gowe["engine"].submissions == []


@pytest.mark.asyncio
async def test_other_backend_still_501(client, gowe, monkeypatch):
    from ragstack.config import settings

    monkeypatch.setattr(settings, "ingest_backend", "parsl")
    assert (await _upload(client, "a.pdf")).status_code == 501
    r = await client.post("/v1/ingest", json={"source": f"ws:///{SUBJECT}/home/x.pdf"},
                          headers=AUTH)
    assert r.status_code == 501


@pytest.mark.asyncio
async def test_oversize_upload_is_413_before_any_submission(client, gowe, monkeypatch):
    from ragstack.config import settings

    monkeypatch.setattr(settings, "max_document_bytes", 10)
    r = await _upload(client, "big.pdf")
    assert r.status_code == 413, r.text
    assert gowe["engine"].submissions == []
    # Since #202 2c the declared size is checked up front — before the version
    # is reserved, before a job exists and before any Workspace call — so an
    # oversize refusal leaves no gap in the numbering and nothing to poll.
    assert gowe["store"].calls.get("next_version", 0) == 0
    assert gowe["workspace"].uploads == [] and gowe["workspace"].folders == []
    assert app.state.job_store._jobs == {}
    assert (await _upload(client, "big2.pdf")).status_code == 413
    monkeypatch.setattr(settings, "max_document_bytes", 50_000_000)
    r2 = await _upload(client, "ok.pdf")
    assert r2.status_code == 202
    assert gowe["engine"].submissions[-1]["inputs"]["version"] == "1"  # no gaps


@pytest.mark.parametrize("name, body, ctype, why", [
    ("n.zip", b"PK\x03\x04", "application/zip", "not an accepted upload content type"),
    ("fake.pdf", b"NOT-A-PDF-AT-ALL", "application/pdf", "%PDF"),
    ("empty.pdf", b"", "application/pdf", "%PDF"),
])
@pytest.mark.asyncio
async def test_non_pdf_is_415_before_any_workspace_write(client, gowe, name, body, ctype, why):
    r = await client.post("/v1/ingest/upload", data={"collection": "lib1"},
                          files=[("files", (name, body, ctype))], headers=AUTH)
    assert r.status_code == 415, r.text
    assert why in r.json()["detail"]
    assert gowe["workspace"].uploads == [] and gowe["workspace"].folders == []
    assert gowe["store"].calls.get("next_version", 0) == 0


@pytest.mark.asyncio
async def test_existing_source_is_409_naming_the_object_and_the_reference_path(client, gowe):
    gowe["workspace"].existing.add("dup.pdf")
    r = await _upload(client, "dup.pdf")
    assert r.status_code == 409, r.text
    existing = f"ws:///{SUBJECT}/home/.ragstack/collections/lib1/sources/dup.pdf"
    detail = r.json()["detail"]
    assert existing in detail and "POST /v1/ingest" in detail and "untouched" in detail
    assert gowe["engine"].submissions == []
    # …and that reference path works.
    r2 = await client.post("/v1/ingest", json={"source": existing, "collection": "lib1"},
                           headers=AUTH)
    assert r2.status_code == 200
    assert (await app.state.job_store.get(r2.json()["job_id"])).status == "completed"


@pytest.mark.asyncio
async def test_ingest_root_not_required_on_gowe(client, gowe, monkeypatch):
    from ragstack.config import settings

    monkeypatch.setattr(settings, "ingest_root", "")
    assert (await _upload(client, "a.pdf")).status_code == 202
    r = await client.post("/v1/ingest", json={"source": f"ws:///{SUBJECT}/home/x.pdf",
                                              "collection": "lib1"}, headers=AUTH)
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# #414 — the second upload into a collection (regression) and Workspace failures
# ---------------------------------------------------------------------------


class _LiveShapeWorkspace(WorkspaceClient):
    """The REAL :class:`WorkspaceClient` over the strict Workspace service fake
    (:mod:`tests.workspace_support`), so ``ensure_collection_folder`` and
    ``upload_source`` are the code under test — usermeta constraints included.
    Only ``read_file`` is stubbed, to serve the receipt the engine 'delivered'.
    """

    def __init__(self, http: httpx.AsyncClient, receipts: FakeWorkspace) -> None:
        super().__init__(WS_SERVICE_URL, http, timeout=5.0)
        self._receipts = receipts

    async def read_file(self, token: str, path: str) -> bytes:
        return await self._receipts.read_file(token, path)


@pytest.fixture
async def live_shape_workspace(gowe):
    """Swap the recording double for the real client + the strict service fake."""
    service = WorkspaceService(token=TOKEN)
    http = httpx.AsyncClient(transport=httpx.MockTransport(service))
    app.state.workspace = _LiveShapeWorkspace(http, gowe["workspace"])
    try:
        yield service
    finally:
        await http.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("create_stores_metadata", [True, False])
async def test_two_consecutive_uploads_into_one_collection_both_succeed(
    client, gowe, live_shape_workspace, create_stores_metadata,
):
    """#414: the collection folder exists after upload 1, so upload 2 takes the
    metadata-backfill branch of ``ensure_collection_folder``. With dotted key
    names that branch raised ``WorkspaceError`` out of the route — an unhandled
    exception, i.e. a bare HTTP 500 — and a collection accepted exactly one
    ingest job for its lifetime. Both readings of #408 are exercised: a
    ``create`` that keeps the (non-dotted) usermeta, and one that keeps none.
    """
    live_shape_workspace.create_stores_metadata = create_stores_metadata
    folder = f"/{SUBJECT}/home/.ragstack/collections/lib1"

    r1 = await _upload(client, "a.pdf")
    assert r1.status_code == 202, r1.text
    assert (await app.state.job_store.get(r1.json()["job_id"])).status == "completed"

    r2 = await _upload(client, "b.pdf")  # distinct name: a repeat would be a 409
    assert r2.status_code == 202, r2.text
    assert (await app.state.job_store.get(r2.json()["job_id"])).status == "completed"

    # Both files landed, one submission each, versions 1 and 2.
    assert {f"{folder}/sources/a.pdf", f"{folder}/sources/b.pdf"} <= set(
        live_shape_workspace.objects)
    assert [s["inputs"]["version"] for s in gowe["engine"].submissions] == ["1", "2"]
    # …and the folder really is stamped — read back out of the service fake.
    assert live_shape_workspace.objects[folder]["metadata"] == {
        "ragstack_format": ARCHIVE_FORMAT, "ragstack_collection_id": "lib1",
        "ragstack_tenant": TENANT, "ragstack_spec_hash": gowe["spec"].spec_hash(),
    }


@pytest.mark.asyncio
async def test_workspace_failure_on_the_folder_is_mapped_not_a_500(client, gowe):
    """A store failure preparing the collection folder must reach the caller as
    a mapped, actionable response — not an unhandled exception (#414)."""
    async def boom(*a, **kw):
        raise WorkspaceError("Workspace.update_metadata: everything is on fire")

    gowe["workspace"].ensure_collection_folder = boom
    r = await _upload(client, "a.pdf")
    assert r.status_code == 502, r.text
    detail = r.json()["detail"]
    assert "lib1" in detail and "on fire" in detail and "No files were uploaded" in detail
    assert gowe["workspace"].uploads == [] and gowe["engine"].submissions == []
    # The job row does not linger in flight — which also unblocks the retry.
    assert [j.status for j in app.state.job_store._jobs.values()] == ["failed"]


@pytest.mark.asyncio
async def test_workspace_auth_failure_on_the_folder_is_401(client, gowe):
    async def boom(*a, **kw):
        raise WorkspaceAuthError("Workspace.get: Token validation failed")

    gowe["workspace"].ensure_collection_folder = boom
    r = await _upload(client, "a.pdf")
    assert r.status_code == 401, r.text
    assert "token" in r.json()["detail"].lower()
    assert TOKEN not in r.text


# --------------------------------------------------------------------------- #
# #422 — the GoWe leg of "the job row records the same id the picker chose"
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_picked_collection_agrees_across_response_job_row_and_submission(
    client, gowe
):
    """Acceptance B's agreement property, on the GoWe path.

    The GoWe branch used to back-fill a ``None`` target with
    ``collections.resolve(collections.default_id)`` — the GLOBAL pointer — for
    the job row, while ``_gowe_inputs`` was built from the authorized entry.
    While the pointer target and the shared surface were necessarily the same
    entry those two agreed by accident; the picker can choose the surface while
    the pointer names something else, and then the row would have named a
    different collection than the run actually wrote to. #422 deletes the
    back-fill, so all three come from one value.

    Asserted as an EQUALITY CHAIN over three independently-produced strings —
    the 202's ``collection``, the job row's ``collection_id``, and the submitted
    workflow inputs' ``collection_id`` — rather than three assertions against a
    literal, so nothing here can pass by all three being wrong the same way."""
    r = await _upload(client, "a.pdf", collection="lib1")
    assert r.status_code == 202, r.text
    body = r.json()
    job = await app.state.job_store.get(body["job_id"])
    submitted = gowe["engine"].submissions[0]["inputs"]

    assert body["collection"] == job.collection_id == submitted["collection_id"]
    assert body["collection"] == "lib1"


@pytest.mark.asyncio
async def test_omitting_the_collection_agrees_too_and_picks_the_writable_default(
    client, gowe, _acl_store
):
    """The same chain with ``collection`` OMITTED — the case the picker actually
    decides, and the one the deleted back-fill was covering for.

    The registry pointer names the legacy shared surface, which is writable by
    exemption, so ``pick_default`` would prefer it — and the GoWe path then
    refuses it with a pre-existing 400 (that branch archives into a REGISTERED
    collection's Workspace folder and the settings-derived corpus has no
    registry row; the arm below pins that, unchanged). Revoking this caller's
    read on the surface leaves ``lib1`` as their only visible collection, so the
    picker must choose it — and the three records must agree on that."""
    from tests.api.conftest import SHARED_ID

    for share in await _acl_store.shares_for(SHARED_ID):
        await _acl_store.revoke(share.id, revoked_by="system:test")

    r = await _upload(client, "a.pdf", collection=None)
    assert r.status_code == 202, r.text
    body = r.json()
    job = await app.state.job_store.get(body["job_id"])
    submitted = gowe["engine"].submissions[0]["inputs"]

    assert body["collection"] == job.collection_id == submitted["collection_id"]
    assert body["collection"] == "lib1"


@pytest.mark.asyncio
async def test_omitting_the_collection_onto_the_shared_surface_is_the_same_400(
    client, gowe
):
    """The arm the test above steps around, pinned so #422 is not read as having
    changed it. When the picker lands on the legacy shared surface, the GoWe
    branch still refuses with the pre-existing 400: that path archives into a
    REGISTERED collection's Workspace folder and the settings-derived corpus has
    no registry row. Before #422 an omitted ``collection`` resolved the same
    entry through the pointer and hit the same refusal."""
    r = await _upload(client, "a.pdf", collection=None)
    assert r.status_code == 400, r.text
    assert "not a registered collection" in r.json()["detail"]
    assert gowe["engine"].submissions == []


# --- #596: …and whether the worker resolves scholarly metadata ------------- #

@pytest.mark.asyncio
async def test_doi_enrichment_travels_on_the_submission(client, gowe, monkeypatch):
    """The gap #596 exists to close.

    On this backend the ingest runs as ``ingest_shard.py`` inside a container the
    engine launches, so it cannot read this tenant's ``DOI_ENRICHMENT_*``
    settings — the enricher the API builds for ITSELF
    (``deps._build_doi_enricher``) reaches only the ``local`` backend's
    in-process pipeline. Measured consequence before this: an upload arrived with
    ``doi`` on every chunk and title/authors/journal/pmid/pmcid on none, whatever
    the tenant had configured. The setting has to travel with the job, exactly as
    the store URLs (#407) and the registry name (#563) do."""
    from ragstack.config import settings

    monkeypatch.setattr(settings, "doi_enrichment_enabled", True)
    monkeypatch.setattr(settings, "doi_enrichment_mailto", "ops@example.org")
    monkeypatch.setattr(settings, "doi_enrichment_cache_dir", "/rag/cache/doi")
    r = await _upload(client, "a.pdf")
    assert r.status_code == 202, r.text
    inputs = gowe["engine"].submissions[0]["inputs"]
    assert inputs["doi_enrichment"] is True
    assert inputs["doi_mailto"] == "ops@example.org"
    assert inputs["doi_cache_dir"] == "/rag/cache/doi"


@pytest.mark.asyncio
async def test_no_doi_keys_are_sent_when_enrichment_is_off(client, gowe, monkeypatch):
    """Off is the air-gapped contract, and it has to reach the worker as
    SILENCE: the submission must be byte-for-byte the pre-#596 one, so the task
    makes no outbound request and an older worker image is not handed a flag it
    does not know."""
    from ragstack.config import settings

    monkeypatch.setattr(settings, "doi_enrichment_enabled", False)
    r = await _upload(client, "a.pdf")
    assert r.status_code == 202, r.text
    inputs = gowe["engine"].submissions[0]["inputs"]
    assert "doi_enrichment" not in inputs
    assert "doi_mailto" not in inputs
    assert "doi_cache_dir" not in inputs


@pytest.mark.asyncio
async def test_a_contact_address_is_optional_and_never_invented(
    client, gowe, monkeypatch
):
    """Enrichment still runs without one (Crossref's anonymous pool), and the
    submission must not carry an empty string the worker would pass to
    ``--doi-mailto``."""
    from ragstack.config import settings

    monkeypatch.setattr(settings, "doi_enrichment_enabled", True)
    monkeypatch.setattr(settings, "doi_enrichment_mailto", "")
    monkeypatch.setattr(settings, "doi_enrichment_cache_dir", "")
    r = await _upload(client, "a.pdf")
    assert r.status_code == 202, r.text
    inputs = gowe["engine"].submissions[0]["inputs"]
    assert inputs["doi_enrichment"] is True
    assert "doi_mailto" not in inputs
    assert "doi_cache_dir" not in inputs


# --- #609: a collection this backend cannot chunk is refused AT SUBMIT --------
#
# The create-time guard (test_collections_create.py) stops NEW semantic
# collections. These cover the ones that already exist — the reported case,
# `Salmonella_AMR2`, was created before any guard shipped. Without this, its
# owner's next upload fails inside the scatter: 20 items, three attempts, an
# error naming the shard tool rather than anything they can act on.


def _set_chunk_method(method: str | None) -> None:
    """Flip the registered `lib1` entry's chunk method in place.

    `CollectionEntry` is a plain dataclass and `app.state.collections` hands back
    the live object, so this is the same mutation a differently-configured
    fixture would produce — and it lets one test show the SAME request passing
    and failing, which is what makes the guard the cause rather than a
    coincidence of the fixture.
    """
    from ragstack.api.main import app

    app.state.collections.resolve("lib1").chunk_method = method


@pytest.mark.asyncio
async def test_semantic_upload_is_422_and_nothing_else_happens(client, gowe):
    _set_chunk_method("semantic")
    r = await _upload(client, "a.pdf")
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "semantic" in detail and "fixed_token" in detail and "609" in detail

    # Refused BEFORE every side effect: no file in the Workspace, no submission,
    # and — the one that is not merely wasteful — no version number burned. A
    # version reserved for a job that never runs leaves a permanent gap.
    assert gowe["workspace"].uploads == []
    assert gowe["engine"].submissions == []
    assert gowe["store"].calls.get("next_version", 0) == 0

    # The control, in the same test and against the same fixture: put the method
    # back and the identical request is accepted. Without this the assertion
    # above would still pass if the upload were broken for any other reason.
    _set_chunk_method("fixed_token")
    ok = await _upload(client, "a.pdf")
    assert ok.status_code == 202, ok.text
    assert gowe["store"].calls.get("next_version", 0) == 1


@pytest.mark.asyncio
async def test_semantic_ws_ingest_is_422(client, gowe):
    _set_chunk_method("semantic_pooled")
    r = await client.post(
        "/v1/ingest", json={"source": f"ws:///{SUBJECT}/home/x.pdf", "collection": "lib1"},
        headers=AUTH,
    )
    assert r.status_code == 422, r.text
    assert "semantic_pooled" in r.json()["detail"]
    assert gowe["engine"].submissions == []
    assert gowe["store"].calls.get("next_version", 0) == 0


@pytest.mark.asyncio
async def test_entry_without_a_chunk_method_falls_back_to_the_server_default(
    client, gowe, monkeypatch
):
    """An entry that records no method is read as `CHUNK_METHOD` by the guard.

    Reading only `entry.chunk_method` would wave such a collection through. The
    fallback is conservative rather than exact — see the helper's docstring: the
    shard tool would actually use its OWN argparse default here, because
    `_gowe_inputs` sends no `chunk_method` when the entry has none. Refusing is
    still the right answer, since the alternative is a run chunked by a method
    nobody chose and no store records. API-created collections never hit this:
    `create_collection` persists the resolved method.
    """
    from ragstack.config import settings

    _set_chunk_method(None)
    monkeypatch.setattr(settings, "chunk_method", "semantic")
    r = await _upload(client, "a.pdf")
    assert r.status_code == 422, r.text
    assert "semantic" in r.json()["detail"]

    monkeypatch.setattr(settings, "chunk_method", "fixed")
    assert (await _upload(client, "a.pdf")).status_code == 202


@pytest.mark.asyncio
async def test_local_backend_does_not_refuse_semantic(client, gowe, monkeypatch):
    """The guard is about THIS deployment's ingest path, not about semantic.

    `ingest_backend=local` chunks in-process, where the embed bridge exists and
    semantic works — so the same collection must not be refused there. A guard
    that fired on the method alone would break every local semantic ingest,
    which is a far larger regression than the bug it fixes.
    """
    from ragstack.config import settings

    _set_chunk_method("semantic")
    monkeypatch.setattr(settings, "ingest_backend", "local")
    monkeypatch.setattr(settings, "ingest_root", "")  # local path stops at its own gate
    r = await _upload(client, "a.pdf")
    assert r.status_code != 422, r.text
    assert r.status_code == 503 and "INGEST_ROOT" in r.json()["detail"]
