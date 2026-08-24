"""``ingest_backend=gowe``: user ingest submits to GoWe AS THE USER (#203 2a).

Fake GoWe engine (``httpx.MockTransport``) + fake Workspace; nothing live.
Pins the seam: upload → one ``upload_source`` per file with the caller's token
→ ONE submission whose inputs are the ``ws://`` sources, whose ``Authorization``
header is the caller's token and whose ``output_destination`` is the
collection's ``versions/`` folder under the caller's home; ``version``
increments across jobs (one registry read + one registry write per job — the
whole per-job cost, so no perf budget beyond the call-count assertion); the
archive location lands on the job. The token appears in NO log line (DEBUG
caplog), NO job-store field and NO exception text. A keyless / API-key
principal is refused with 401; a non-Workspace source with 400; the default
(unregistered) collection with 400; any backend that is neither local nor gowe
still 501s. ``ingest_backend=local`` is covered unchanged by the existing tests.
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
from ragstack.ingestion.gowe_backend import GoWeBackend
from ragstack.ingestion.gowe_client import GoWeClient
from ragstack.ingestion.receipts import COMPLETED, ShardReceipt
from ragstack.workspace import WorkspaceTooLarge, collection_folder, ws_path, ws_uri

TOKEN = "un=alice@patricbrc.org|tokenid=t-1|expiry=9999999999|sig=SECRETSIGNATURE"
SUBJECT = "alice@patricbrc.org"
TENANT = f"bvbrc:{SUBJECT}"
HOME = f"ws:///{SUBJECT}/home/"
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
    """GoWe REST fake: records every submission (body + Authorization header)."""

    def __init__(self) -> None:
        self.submissions: list[dict[str, Any]] = []
        self.auth: list[str | None] = []
        self.fail: str | None = None  # "transport" | "no_receipts"

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.auth.append(req.headers.get("Authorization"))
        if self.fail == "transport":
            raise httpx.ConnectError("refused", request=req)
        p = req.url.path
        if req.method == "POST" and p == "/api/v1/workflows":
            return httpx.Response(201, json={"data": {"id": "wf_1"}})
        if req.method == "POST" and p == "/api/v1/submissions":
            body = json.loads(req.content)
            body["_auth"] = req.headers.get("Authorization")
            self.submissions.append(body)
            sid = f"sub_{len(self.submissions)}"
            return httpx.Response(201, json={"data": {"id": sid, "state": "PENDING"}})
        if req.method == "GET" and p.startswith("/api/v1/submissions/"):
            n = int(p.rsplit("_", 1)[1])
            body = self.submissions[n - 1]
            key = "pdfs"
            files = body["inputs"][key]
            outputs: dict[str, Any] = {}
            if self.fail != "no_receipts":
                outputs["receipts"] = [
                    {"class": "File", "location": f"file:///w/{n}/r{i}.json"}
                    for i in range(len(files))
                ]
                outputs["archive"] = {"class": "Directory",
                                      "location": f"file:///w/{n}/{body['inputs']['version']}"}
            return httpx.Response(200, json={"data": {"id": p.rsplit('/', 1)[1],
                                                       "state": "COMPLETED", "outputs": outputs}})
        if req.method == "GET" and p == "/api/v1/files/download":
            loc = req.url.params["location"]
            r = ShardReceipt(loc, "public", COMPLETED, n_docs=1, n_chunks=2,
                             chunk_ids=[f"{loc}#0", f"{loc}#1"])
            return httpx.Response(200, content=r.to_json().encode())
        return httpx.Response(404, text="unexpected")


class FakeWorkspace:
    """Records ensure_collection_folder / upload_source calls; consumes streams."""

    def __init__(self) -> None:
        self.folders: list[tuple[str, str, str, str, str]] = []
        self.uploads: list[dict[str, Any]] = []
        self.max_bytes_seen: list[int] = []

    async def ensure_collection_folder(self, token, subject, collection_id, *, spec_hash, tenant):
        self.folders.append((token, subject, collection_id, spec_hash, tenant))
        return ws_uri(collection_folder(subject, collection_id))

    async def upload_source(self, token, folder, filename, stream, *, max_bytes, size=None):
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
    http = httpx.AsyncClient(transport=httpx.MockTransport(engine))
    backend = GoWeBackend(
        GoWeClient("http://gowe.test", token="", http=http),
        "cwlVersion: v1.2\n", workflow_name="ragstack-pdf-ingest-scatter",
        static_inputs={"embedding_url": ["http://emb.test/v1"], "qdrant_url": "http://q.test"},
        shards_input_key="pdfs", receipts_output_key="receipts", poll_interval=0, timeout=5,
    )
    app.state.ingest_backend = backend
    workspace = FakeWorkspace()
    app.state.workspace = workspace

    spec = CollectionSpec(id="lib1", label="lib1", owner=TENANT, collection="lib1_phys",
                          embedding_model="test-model", embedding_model_dim=4,
                          embedding_endpoints=["http://emb.lib1/v1"],
                          chunk_method="fixed_token", chunk_size=256, chunk_overlap=32)
    store = CountingStore(InMemoryCollectionStore([spec]))
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
        await http.aclose()


AUTH = {"Authorization": f"Bearer {TOKEN}"}


async def _upload(client, *names: str, collection: str | None = "lib1", headers=AUTH):
    files = [("files", (n, _pdf(), "application/pdf")) for n in names]
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
    assert sub["output_destination"] == HOME + ".ragstack/collections/lib1/versions/"
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

    # Receipts mapped per item; archive location recorded on the job (#358's hook).
    poll = await client.get(f"/v1/ingest/{job_id}", headers=AUTH)
    assert poll.status_code == 200 and poll.json()["status"] == "completed"
    assert poll.json()["items"] == {"total": 2, "completed": 2, "failed": 0, "pending": 0}
    assert "archive_ref" not in poll.json()  # contract unchanged
    job = await app.state.job_store.get(job_id)
    assert job.archive_ref == HOME + ".ragstack/collections/lib1/versions/1"
    assert job.tenant_id == TENANT and job.source == "upload"

    # The per-job registry cost: one read (spec_hash) + one write (next_version).
    assert store.calls == {"get": 1, "next_version": 1}


@pytest.mark.asyncio
async def test_version_increments_across_jobs(client, gowe):
    r1 = await _upload(client, "a.pdf")
    r2 = await _upload(client, "b.pdf")
    assert (r1.status_code, r2.status_code) == (202, 202)
    versions = [s["inputs"]["version"] for s in gowe["engine"].submissions]
    assert versions == ["1", "2"]
    js = app.state.job_store
    refs = [(await js.get(r.json()["job_id"])).archive_ref for r in (r1, r2)]
    assert refs == [HOME + ".ragstack/collections/lib1/versions/1",
                    HOME + ".ragstack/collections/lib1/versions/2"]
    assert gowe["store"].calls == {"get": 2, "next_version": 2}


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
    assert sub["output_destination"] == HOME + ".ragstack/collections/lib1/versions/"
    poll = await client.get(f"/v1/ingest/{r.json()['job_id']}", headers=AUTH)
    assert poll.json()["status"] == "completed"
    assert poll.json()["items"]["completed"] == 1


@pytest.mark.parametrize("source", ["/data/corpus/x.pdf", "relative/x.pdf", "ws:///alice", "/alice/other/x.pdf", "file:///etc/passwd"])
@pytest.mark.asyncio
async def test_non_workspace_source_is_400_and_never_submitted(client, gowe, source):
    r = await client.post("/v1/ingest", json={"source": source, "collection": "lib1"},
                          headers=AUTH)
    assert r.status_code == 400, r.text
    assert "Workspace reference" in r.json()["detail"]
    assert gowe["engine"].submissions == []


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

    assert TOKEN not in caplog.text
    assert "SECRETSIGNATURE" not in caplog.text
    assert TOKEN not in _job_store_dump()
    assert TOKEN not in json.dumps(vars(gowe["backend"]), default=str)
    assert TOKEN not in json.dumps(vars(gowe["backend"].client), default=str)
    assert any("gowe submission failed" in rec.getMessage() for rec in caplog.records)
    for rec in caplog.records:
        assert TOKEN not in rec.getMessage()
        if rec.exc_text:
            assert TOKEN not in rec.exc_text


@pytest.mark.asyncio
async def test_completed_without_receipts_is_a_visible_job_failure(client, gowe):
    gowe["engine"].fail = "no_receipts"
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
async def test_keyless_principal_is_401(client, gowe, monkeypatch):
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
    assert gowe["store"].calls.get("next_version", 0) == 0  # no version consumed
    # The job minted for the request is marked failed/rejected, like the local path.
    job = await app.state.job_store.get(list(app.state.job_store._jobs)[-1])
    assert job.status == "failed" and job.error == "rejected"


@pytest.mark.asyncio
async def test_non_pdf_is_415_before_any_workspace_write(client, gowe):
    r = await client.post("/v1/ingest/upload", data={"collection": "lib1"},
                          files=[("files", ("n.txt", b"text", "text/plain"))], headers=AUTH)
    assert r.status_code == 415
    assert gowe["workspace"].uploads == [] and gowe["workspace"].folders == []


@pytest.mark.asyncio
async def test_ingest_root_not_required_on_gowe(client, gowe, monkeypatch):
    from ragstack.config import settings

    monkeypatch.setattr(settings, "ingest_root", "")
    assert (await _upload(client, "a.pdf")).status_code == 202
    r = await client.post("/v1/ingest", json={"source": f"ws:///{SUBJECT}/home/x.pdf",
                                              "collection": "lib1"}, headers=AUTH)
    assert r.status_code == 200
