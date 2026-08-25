"""#203 blockers (b) and (c) on ``GoWeBackend``, plus the post-staging contract.

(b) The scattered-input / receipts-output keys are threaded from settings
    through ``_make_gowe_backend`` (they used to be constructor-only, so the PDF
    workflow — whose input is ``pdfs`` — was unreachable from config).
(c) Missing receipts are a **visible** error (``GoWeContractError`` propagates;
    the job fails with that label) — never "every item failed", which is what a
    fully successful run used to report. Two receipt sources, one rule:
      * bulk plane (no ``output_destination``): the workflow's ``receipts``
        ``File[]`` output, downloaded from the engine;
      * user path (``output_destination`` set): ``receipt.json`` INSIDE the
        delivered archive, read from the Workspace as the caller — because the
        engine post-stages every top-level File output flat into the
        destination and rewrites its location to ``ws://``, the workflow exposes
        only the ``archive`` Directory.
Post-staging is two-phase: COMPLETED is observed before ``output_state`` is
decided, so the run waits for ``delivered`` and maps ``upload_failed`` to
``OutputStagingFailed``.

Plus the token seam: a per-call token reaches only the ``Authorization`` header
of each engine request, is held on neither the client nor the backend, and
never appears in an exception (transport errors are re-raised ``from None``).
Everything runs against fakes / ``httpx.MockTransport``; no engine is contacted.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from ragstack.ingestion.backends import make_ingest_backend
from ragstack.ingestion.gowe_backend import (
    OUTPUT_STAGING_FAILED,
    GoWeBackend,
    GoWeContractError,
    OutputStagingFailed,
)
from ragstack.ingestion.gowe_client import GoWeClient, GoWeError
from ragstack.ingestion.manifest import Manifest, WorkItem
from ragstack.ingestion.receipts import COMPLETED, FAILED, ShardReceipt
from ragstack.ingestion.sharded import ShardedIngestor
from ragstack.jobstore import FAILED as JOB_FAILED
from ragstack.jobstore import PENDING, InMemoryJobStore
from ragstack.workspace import WorkspaceNotFound

CWL_TEXT = "cwlVersion: v1.2\nclass: Workflow\n"
SCATTER_CWL = Path(__file__).resolve().parents[3] / "cwl" / "pdf-ingest-scatter.cwl"
USER_TOKEN = "un=alice@patricbrc.org|tokenid=t-1|expiry=9999999999|sig=USERSECRET"
OPERATOR_TOKEN = "un=ops@patricbrc.org|tokenid=t-0|expiry=9999999999|sig=OPSSECRET"
DEST = "ws:///alice@patricbrc.org/home/.ragstack/collections/lib1/versions/"


def _settings(tmp_path: Path, **over: Any) -> SimpleNamespace:
    cwl = tmp_path / "wf.cwl"
    cwl.write_text(CWL_TEXT, encoding="utf-8")
    base: dict[str, Any] = {
        "ingest_backend": "gowe",
        "ingest_concurrency": 4,
        "gowe_url": "http://gowe.test",
        "gowe_token": OPERATOR_TOKEN,
        "gowe_workflow_cwl": str(cwl),
        "gowe_workflow_name": "wf",
        "gowe_workflow_inputs_json": "{}",
        "gowe_worker_group": "",
        "gowe_poll_interval": 0.0,
        "gowe_timeout": 1.0,
        "gowe_shards_input_key": "pdfs",
        "gowe_receipts_output_key": "receipts",
        "gowe_output_wait_timeout": 0.5,
        "workspace_url": "http://workspace.test",
        "workspace_timeout": 5.0,
    }
    base.update(over)
    return SimpleNamespace(**base)


def _wi(i: int) -> WorkItem:
    return WorkItem(item_id=f"i{i}", source=f"ws:///alice@patricbrc.org/home/p{i}.pdf")


def _receipt(*ids: str, status: str = COMPLETED, error: str = "") -> ShardReceipt:
    return ShardReceipt("s", "public", status, n_docs=1, n_chunks=len(ids),
                        chunk_ids=list(ids), error=error)


class _Client:
    """register/submit/wait/download stub. ``outputs`` is what the final
    submission record carries; ``output_state`` its post-staging state."""

    def __init__(self, outputs: dict[str, Any] | None, receipts: dict[str, ShardReceipt],
                 *, state: str = "COMPLETED", output_state: str = "delivered",
                 error: str = "") -> None:
        self.outputs = outputs
        self.receipts = receipts
        self.state = state
        self.output_state = output_state
        self.error = error
        self.submitted: dict[str, Any] | None = None
        self.submit_kwargs: dict[str, Any] = {}
        self.wait_kwargs: dict[str, Any] = {}
        self.downloads: list[str] = []

    async def register_workflow(self, name, cwl, labels=None, **kw) -> str:
        return "wf_fake"

    async def submit(self, wf_id, inputs, **kw):
        self.submitted = inputs
        self.submit_kwargs = kw
        return {"id": "sub_fake", "state": "PENDING"}

    async def wait(self, sub_id, **kw):
        self.wait_kwargs = kw
        rec: dict[str, Any] = {"id": sub_id, "state": self.state,
                               "output_state": self.output_state}
        if self.error:
            rec["error"] = self.error
        if self.outputs is not None:
            rec["outputs"] = self.outputs
        return rec

    async def download(self, location, **kw) -> bytes:
        self.downloads.append(location)
        return self.receipts[location].to_json().encode()


class _Workspace:
    """Serves ``read_file``; records (token, path)."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.reads: list[tuple[str, str]] = []

    async def read_file(self, token: str, path: str) -> bytes:
        self.reads.append((token, path))
        if path not in self.files:
            raise WorkspaceNotFound(f"{path} does not exist")
        return self.files[path]


def _receipts_json(*receipts: ShardReceipt) -> bytes:
    if len(receipts) == 1:  # archive.py copies a single receipt verbatim (an object)
        return receipts[0].to_json().encode()
    return json.dumps([json.loads(r.to_json()) for r in receipts]).encode()


RECEIPT_PATH = "/alice@patricbrc.org/home/.ragstack/collections/lib1/versions/7/receipt.json"


# --- (b) keys from settings -------------------------------------------------- #

def test_keys_threaded_from_settings(tmp_path: Path) -> None:
    b = make_ingest_backend(
        _settings(tmp_path, gowe_shards_input_key="docs", gowe_receipts_output_key="out")
    )
    assert isinstance(b, GoWeBackend)
    assert b.shards_input_key == "docs"
    assert b.receipts_output_key == "out"
    assert b.output_wait_timeout == 0.5
    assert b.workspace is not None and b.workspace.base_url == "http://workspace.test"


def test_settings_defaults_name_the_pdf_workflow_contract(tmp_path: Path) -> None:
    """The defaults are the scatter-per-PDF workflow's actual input name; its only
    output is the archive (the receipts ride inside it)."""
    from ragstack.config import Settings

    yaml = pytest.importorskip("yaml")
    defaults = Settings.model_fields
    shards_key = defaults["gowe_shards_input_key"].default
    wf = yaml.safe_load(SCATTER_CWL.read_text(encoding="utf-8"))
    assert shards_key == "pdfs" and shards_key in wf["inputs"]
    assert wf["inputs"][shards_key]["type"] == "File[]"
    assert list(wf["outputs"]) == ["archive"] and wf["outputs"]["archive"]["type"] == "Directory"
    assert defaults["gowe_output_wait_timeout"].default == 600.0
    # The per-job inputs the API sends are declared (and version/collection_id required).
    for key in ("version", "collection_id", "spec_hash", "job_id", "tenant", "collection"):
        assert key in wf["inputs"], key
    b = make_ingest_backend(_settings(tmp_path, gowe_shards_input_key=shards_key))
    assert isinstance(b, GoWeBackend) and b.shards_input_key == "pdfs"


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_keys_fall_back_to_bulk_workflow_defaults(tmp_path: Path, blank: str) -> None:
    b = make_ingest_backend(
        _settings(tmp_path, gowe_shards_input_key=blank, gowe_receipts_output_key=blank)
    )
    assert isinstance(b, GoWeBackend)
    assert (b.shards_input_key, b.receipts_output_key) == ("shards", "receipts")


def test_settings_without_new_fields_keep_defaults(tmp_path: Path) -> None:
    """An older settings object (no gowe_*_key / workspace attributes) still builds."""
    s = _settings(tmp_path)
    for name in ("gowe_shards_input_key", "gowe_receipts_output_key",
                 "gowe_output_wait_timeout", "workspace_url", "workspace_timeout"):
        delattr(s, name)
    b = make_ingest_backend(s)
    assert isinstance(b, GoWeBackend)
    assert (b.shards_input_key, b.receipts_output_key) == ("shards", "receipts")
    assert b.output_wait_timeout == 600.0


@pytest.mark.asyncio
async def test_custom_keys_drive_submission_and_output_mapping() -> None:
    loc = "file:///w/r0.json"
    client = _Client({"out": [{"class": "File", "location": loc}]}, {loc: _receipt("a", "b")})
    b = GoWeBackend(client, CWL_TEXT, shards_input_key="docs", receipts_output_key="out",
                    poll_interval=0, timeout=1)
    results = await b.run_shards([[_wi(0)]], shard_fn=None)
    assert client.submitted is not None and "docs" in client.submitted
    assert "shards" not in client.submitted and "pdfs" not in client.submitted
    assert client.submitted["docs"] == [{"class": "File", "location": _wi(0).source}]
    assert results[0].status == "completed" and results[0].chunk_ids == ["a", "b"]
    # Bulk plane: no delivery wait, receipts downloaded from the engine.
    assert client.wait_kwargs["require_delivery"] is False and client.downloads == [loc]


# --- (c) bulk plane: no receipts output → visible error ---------------------- #

@pytest.mark.parametrize("outputs", [None, {}, {"receipts": None},
                                     {"summary": {"class": "File", "location": "file:///w/s.json"}}])
@pytest.mark.asyncio
async def test_completed_without_receipts_output_raises_not_all_failed(outputs) -> None:
    b = GoWeBackend(_Client(outputs, {}), CWL_TEXT, poll_interval=0, timeout=1)
    items = [_wi(0), _wi(1)]
    with pytest.raises(GoWeContractError, match="no 'receipts' output"):
        await b.run_shards([items], shard_fn=None)
    with pytest.raises(GoWeContractError):
        await b.run_submission(items)


@pytest.mark.asyncio
async def test_no_receipts_fails_the_job_visibly_not_per_item() -> None:
    """Through ShardedIngestor: the contract error propagates (the router turns
    it into status=failed, error=GoWeContractError) and NO item is marked failed
    — the per-item state stays pending, i.e. 'not reported', not 'failed'."""
    b = GoWeBackend(_Client({}, {}), CWL_TEXT, poll_interval=0, timeout=1)
    store = InMemoryJobStore()
    job = await store.create(source="x")
    ingestor = ShardedIngestor(pipeline=None, backend=b, job_store=store)  # type: ignore[arg-type]
    with pytest.raises(GoWeContractError):
        await ingestor.ingest_manifest(Manifest(items=[_wi(0), _wi(1)]), job_id=job.job_id)
    counts = await store.item_counts(job.job_id)
    assert counts[PENDING] == 2 and counts[JOB_FAILED] == 0


@pytest.mark.asyncio
async def test_engine_failure_still_degrades_to_failed_items() -> None:
    """The existing contract for ENGINE-side failure is unchanged: a FAILED
    submission yields failed items, no exception (only the contract case raises)."""
    b = GoWeBackend(_Client({}, {}, state="FAILED", output_state=""), CWL_TEXT,
                    poll_interval=0, timeout=1)
    results = await b.run_shards([[_wi(0)]], shard_fn=None)
    assert results[0].status == "failed" and "FAILED" in results[0].error
    run = await b.run_submission([_wi(0)])
    assert run.state == "FAILED" and run.archive_ref == ""


# --- (c) user path: receipts come from the delivered archive ----------------- #

def _user_backend(client: _Client, ws: _Workspace | None) -> GoWeBackend:
    return GoWeBackend(client, CWL_TEXT, poll_interval=0, timeout=1,
                       output_wait_timeout=0.2, workspace=ws)


@pytest.mark.asyncio
async def test_receipts_read_from_the_archive_as_the_caller() -> None:
    client = _Client({"archive": {"class": "Directory", "location": "file:///w/7"}}, {})
    ws = _Workspace({RECEIPT_PATH: _receipts_json(
        _receipt("a", "b"), _receipt(status=FAILED, error="boom"))})
    run = await _user_backend(client, ws).run_submission(
        [_wi(0), _wi(1)], inputs={"version": "7"}, token=USER_TOKEN, output_destination=DEST)
    assert run.archive_ref == DEST + "7"
    assert [(r.status, r.chunk_ids, r.error) for r in run.results] == [
        ("completed", ["a", "b"], ""), ("failed", [], "boom")]
    # Waited for delivery, read as the user, downloaded NOTHING from the engine.
    assert client.wait_kwargs["require_delivery"] is True
    assert client.wait_kwargs["delivery_timeout"] == 0.2
    assert ws.reads == [(USER_TOKEN, RECEIPT_PATH)] and client.downloads == []
    assert client.submit_kwargs["output_destination"] == DEST
    assert client.submitted["version"] == "7"


@pytest.mark.asyncio
async def test_single_receipt_object_maps_to_the_one_item() -> None:
    # archive.py copies ONE receipt verbatim (an object, not a 1-element array).
    client = _Client({}, {})
    ws = _Workspace({RECEIPT_PATH: _receipts_json(_receipt("a"))})
    run = await _user_backend(client, ws).run_submission(
        [_wi(0)], inputs={"version": "7"}, token=USER_TOKEN, output_destination=DEST)
    assert run.results[0].status == "completed" and run.results[0].chunk_ids == ["a"]


@pytest.mark.asyncio
async def test_per_call_workspace_overrides_the_backends() -> None:
    client = _Client({}, {})
    default_ws = _Workspace({})
    call_ws = _Workspace({RECEIPT_PATH: _receipts_json(_receipt("a"))})
    run = await _user_backend(client, default_ws).run_submission(
        [_wi(0)], inputs={"version": "7"}, token=USER_TOKEN, output_destination=DEST,
        workspace=call_ws)
    assert run.results[0].status == "completed" and default_ws.reads == []


@pytest.mark.parametrize("body", [None, b"[]", b'[{"shard_id": "only-one"}]', b"not json", b'"str"'])
@pytest.mark.asyncio
async def test_missing_or_short_receipt_array_is_a_contract_error(body) -> None:
    client = _Client({}, {})
    ws = _Workspace({} if body is None else {RECEIPT_PATH: body})
    with pytest.raises(GoWeContractError):
        await _user_backend(client, ws).run_submission(
            [_wi(0), _wi(1)], inputs={"version": "7"}, token=USER_TOKEN, output_destination=DEST)


@pytest.mark.asyncio
async def test_malformed_single_entry_fails_only_its_item() -> None:
    client = _Client({}, {})
    ws = _Workspace({RECEIPT_PATH: json.dumps(
        [json.loads(_receipt("a").to_json()), {"garbage": True}]).encode()})
    run = await _user_backend(client, ws).run_submission(
        [_wi(0), _wi(1)], inputs={"version": "7"}, token=USER_TOKEN, output_destination=DEST)
    assert run.results[0].status == "completed"
    assert run.results[1].status == "failed" and "no readable receipt" in run.results[1].error


@pytest.mark.asyncio
async def test_no_workspace_and_no_version_are_contract_errors() -> None:
    client = _Client({}, {})
    with pytest.raises(GoWeContractError, match="Workspace client"):
        await _user_backend(client, None).run_submission(
            [_wi(0)], inputs={"version": "7"}, token=USER_TOKEN, output_destination=DEST)
    with pytest.raises(GoWeContractError, match="'version' input"):
        await _user_backend(client, _Workspace({})).run_submission(
            [_wi(0)], token=USER_TOKEN, output_destination=DEST)


@pytest.mark.parametrize("state, output_state, error", [
    ("COMPLETED", "upload_failed", ""),
    ("FAILED", "upload_failed", ""),
    ("FAILED", "", f"post-staging: {OUTPUT_STAGING_FAILED}: 403 from Workspace"),
])
@pytest.mark.asyncio
async def test_undelivered_outputs_raise_output_staging_failed(state, output_state, error) -> None:
    client = _Client({}, {}, state=state, output_state=output_state, error=error)
    ws = _Workspace({RECEIPT_PATH: _receipts_json(_receipt("a"))})
    with pytest.raises(OutputStagingFailed, match=OUTPUT_STAGING_FAILED):
        await _user_backend(client, ws).run_submission(
            [_wi(0)], inputs={"version": "7"}, token=USER_TOKEN, output_destination=DEST)
    assert ws.reads == []  # nothing to read: no archive was delivered


@pytest.mark.asyncio
async def test_plain_failure_without_destination_is_not_staging() -> None:
    # The bulk plane has no destination: a FAILED submission stays "all items failed".
    client = _Client({}, {}, state="FAILED", output_state="upload_failed")
    results = await _user_backend(client, None).run_shards([[_wi(0)]], shard_fn=None)
    assert results[0].status == "failed" and "FAILED" in results[0].error


# --- the token seam (real GoWeClient over MockTransport) ---------------------- #

class _FakeEngine:
    def __init__(self, *, fail: str | None = None) -> None:
        self.fail = fail
        self.auth: list[tuple[str, str | None]] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.auth.append((f"{req.method} {req.url.path}", req.headers.get("Authorization")))
        if self.fail == "transport":
            raise httpx.ConnectError("connection refused", request=req)
        if self.fail == "echo-401":
            return httpx.Response(401, text=f"bad token: {req.headers.get('Authorization')}")
        p = req.url.path
        if req.method == "POST" and p == "/api/v1/workflows":
            return httpx.Response(201, json={"data": {"id": "wf_1"}})
        if req.method == "POST" and p == "/api/v1/submissions":
            return httpx.Response(201, json={"data": {"id": "sub_1", "state": "PENDING"}})
        if req.method == "GET" and p == "/api/v1/submissions/sub_1":
            out = {"receipts": [{"class": "File", "location": "file:///w/r0.json"}]}
            return httpx.Response(200, json={"data": {"id": "sub_1", "state": "COMPLETED",
                                                       "output_state": "delivered",
                                                       "outputs": out}})
        if req.method == "GET" and p == "/api/v1/files/download":
            return httpx.Response(200, content=_receipt("a").to_json().encode())
        return httpx.Response(404, text="unexpected")


def _backend(engine: _FakeEngine, ws: _Workspace | None = None) -> tuple[GoWeBackend, GoWeClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(engine))
    client = GoWeClient("http://gowe.test", token=OPERATOR_TOKEN, http=http)
    return GoWeBackend(client, CWL_TEXT, poll_interval=0, timeout=1, workspace=ws), client


def _holds_token(obj: object, token: str) -> bool:
    return token in json.dumps(vars(obj), default=str)


@pytest.mark.asyncio
async def test_per_call_token_reaches_only_the_engine_headers_and_is_not_kept() -> None:
    engine = _FakeEngine()
    ws = _Workspace({RECEIPT_PATH: _receipts_json(_receipt("a"))})
    backend, client = _backend(engine, ws)
    run = await backend.run_submission([_wi(0)], token=USER_TOKEN, output_destination=DEST,
                                       inputs={"version": "7"})
    assert run.results[0].status == "completed"
    by_call = dict(engine.auth)
    # Every engine request of the run went out as the user, not as the operator …
    for call in ("POST /api/v1/workflows", "POST /api/v1/submissions",
                 "GET /api/v1/submissions/sub_1"):
        assert by_call[call] == USER_TOKEN, call
    assert "GET /api/v1/files/download" not in by_call  # receipts came from the Workspace
    assert ws.reads == [(USER_TOKEN, RECEIPT_PATH)]
    # … and the client's own (operator) token is still what it had, untouched.
    assert client._token == OPERATOR_TOKEN
    assert not _holds_token(backend, USER_TOKEN) and not _holds_token(client, USER_TOKEN)


@pytest.mark.asyncio
async def test_without_per_call_token_the_client_token_is_used() -> None:
    engine = _FakeEngine()
    backend, _ = _backend(engine)
    await backend.run_shards([[_wi(0)]], shard_fn=None)
    assert {tok for _, tok in engine.auth} == {OPERATOR_TOKEN}


@pytest.mark.asyncio
async def test_transport_error_carries_no_token_and_no_cause() -> None:
    backend, _ = _backend(_FakeEngine(fail="transport"))
    with pytest.raises(GoWeError) as info:
        await backend.run_submission([_wi(0)], token=USER_TOKEN)
    assert USER_TOKEN not in str(info.value) and OPERATOR_TOKEN not in str(info.value)
    assert "ConnectError" in str(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__


@pytest.mark.asyncio
async def test_engine_error_body_echoing_the_token_is_scrubbed() -> None:
    backend, _ = _backend(_FakeEngine(fail="echo-401"))
    with pytest.raises(GoWeError) as info:
        await backend.run_submission([_wi(0)], token=USER_TOKEN)
    msg = str(info.value)
    assert USER_TOKEN not in msg and "[token]" in msg and "401" in msg
