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
    WorkspaceExists,
    WorkspaceNotFound,
    WorkspaceTooLarge,
    collection_folder,
    ws_path,
    ws_uri,
)

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

    engine = FakeEngine()
    workspace = FakeWorkspace(engine)
    http = httpx.AsyncClient(transport=httpx.MockTransport(engine))
    backend = GoWeBackend(
        GoWeClient("http://gowe.test", token="", http=http),
        "cwlVersion: v1.2\n", workflow_name="ragstack-pdf-ingest-scatter",
        static_inputs={"embedding_url": ["http://emb.test/v1"], "qdrant_url": "http://q.test"},
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
    assert inputs["qdrant_url"] == "http://q.test"  # static input passes through
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
