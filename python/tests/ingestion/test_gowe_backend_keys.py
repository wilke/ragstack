"""#203 blockers (b) and (c) on ``GoWeBackend``.

(b) The scattered-input / receipts-output keys are threaded from settings
    through ``_make_gowe_backend`` (they used to be constructor-only, so the PDF
    workflow — whose input is ``pdfs`` — was unreachable from config).
(c) A COMPLETED submission with NO receipts output is a **visible** error
    (``GoWeContractError`` propagates; the job fails with that label) — never
    "every item failed", which is what a fully successful run used to report.

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
from ragstack.ingestion.gowe_backend import GoWeBackend, GoWeContractError
from ragstack.ingestion.gowe_client import GoWeClient, GoWeError
from ragstack.ingestion.manifest import Manifest, WorkItem
from ragstack.ingestion.receipts import COMPLETED, ShardReceipt
from ragstack.ingestion.sharded import ShardedIngestor
from ragstack.jobstore import FAILED as JOB_FAILED
from ragstack.jobstore import PENDING, InMemoryJobStore

CWL_TEXT = "cwlVersion: v1.2\nclass: Workflow\n"
SCATTER_CWL = Path(__file__).resolve().parents[3] / "cwl" / "pdf-ingest-scatter.cwl"
USER_TOKEN = "un=alice@patricbrc.org|tokenid=t-1|expiry=9999999999|sig=USERSECRET"
OPERATOR_TOKEN = "un=ops@patricbrc.org|tokenid=t-0|expiry=9999999999|sig=OPSSECRET"


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
    }
    base.update(over)
    return SimpleNamespace(**base)


def _wi(i: int) -> WorkItem:
    return WorkItem(item_id=f"i{i}", source=f"ws:///alice@patricbrc.org/home/p{i}.pdf")


class _Client:
    """register/submit/wait/download stub with configurable workflow outputs."""

    def __init__(self, outputs: dict[str, Any] | None, receipts: dict[str, ShardReceipt]) -> None:
        self.outputs = outputs
        self.receipts = receipts
        self.submitted: dict[str, Any] | None = None
        self.submit_kwargs: dict[str, Any] = {}

    async def register_workflow(self, name, cwl, labels=None, **kw) -> str:
        return "wf_fake"

    async def submit(self, wf_id, inputs, **kw):
        self.submitted = inputs
        self.submit_kwargs = kw
        return {"id": "sub_fake", "state": "PENDING"}

    async def wait(self, sub_id, **kw):
        rec: dict[str, Any] = {"id": sub_id, "state": "COMPLETED"}
        if self.outputs is not None:
            rec["outputs"] = self.outputs
        return rec

    async def download(self, location, **kw) -> bytes:
        return self.receipts[location].to_json().encode()


def _receipt(loc: str, *ids: str) -> tuple[str, ShardReceipt]:
    return loc, ShardReceipt("s", "public", COMPLETED, n_docs=1, n_chunks=len(ids),
                             chunk_ids=list(ids))


# --- (b) keys from settings -------------------------------------------------- #

def test_keys_threaded_from_settings(tmp_path: Path) -> None:
    b = make_ingest_backend(
        _settings(tmp_path, gowe_shards_input_key="docs", gowe_receipts_output_key="out")
    )
    assert isinstance(b, GoWeBackend)
    assert b.shards_input_key == "docs"
    assert b.receipts_output_key == "out"


def test_settings_defaults_name_the_pdf_workflow_contract(tmp_path: Path) -> None:
    """The defaults are the scatter-per-PDF workflow's actual input/output names."""
    from ragstack.config import Settings

    yaml = pytest.importorskip("yaml")
    defaults = Settings.model_fields
    shards_key = defaults["gowe_shards_input_key"].default
    receipts_key = defaults["gowe_receipts_output_key"].default
    wf = yaml.safe_load(SCATTER_CWL.read_text(encoding="utf-8"))
    assert shards_key == "pdfs" and shards_key in wf["inputs"]
    assert wf["inputs"][shards_key]["type"] == "File[]"
    assert receipts_key == "receipts" and receipts_key in wf["outputs"]
    assert wf["outputs"][receipts_key]["type"] == "File[]"
    assert wf["outputs"]["archive"]["type"] == "Directory"
    # The per-job inputs the API sends are declared (and version/collection_id required).
    for key in ("version", "collection_id", "spec_hash", "job_id", "tenant", "collection"):
        assert key in wf["inputs"], key
    b = make_ingest_backend(_settings(tmp_path, gowe_shards_input_key=shards_key,
                                      gowe_receipts_output_key=receipts_key))
    assert isinstance(b, GoWeBackend) and b.shards_input_key == "pdfs"


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_keys_fall_back_to_bulk_workflow_defaults(tmp_path: Path, blank: str) -> None:
    b = make_ingest_backend(
        _settings(tmp_path, gowe_shards_input_key=blank, gowe_receipts_output_key=blank)
    )
    assert isinstance(b, GoWeBackend)
    assert (b.shards_input_key, b.receipts_output_key) == ("shards", "receipts")


def test_settings_without_key_fields_keep_bulk_defaults(tmp_path: Path) -> None:
    """An older settings object (no gowe_*_key attributes) still builds."""
    s = _settings(tmp_path)
    del s.gowe_shards_input_key, s.gowe_receipts_output_key
    b = make_ingest_backend(s)
    assert isinstance(b, GoWeBackend)
    assert (b.shards_input_key, b.receipts_output_key) == ("shards", "receipts")


@pytest.mark.asyncio
async def test_custom_keys_drive_submission_and_output_mapping() -> None:
    loc, rec = _receipt("file:///w/r0.json", "a", "b")
    client = _Client({"out": [{"class": "File", "location": loc}]}, {loc: rec})
    b = GoWeBackend(client, CWL_TEXT, shards_input_key="docs", receipts_output_key="out",
                    poll_interval=0, timeout=1)
    results = await b.run_shards([[_wi(0)]], shard_fn=None)
    assert client.submitted is not None and "docs" in client.submitted
    assert "shards" not in client.submitted and "pdfs" not in client.submitted
    assert client.submitted["docs"] == [{"class": "File", "location": _wi(0).source}]
    assert results[0].status == "completed" and results[0].chunk_ids == ["a", "b"]


# --- (c) no receipts output → visible error ---------------------------------- #

@pytest.mark.parametrize("outputs", [None, {}, {"receipts": None}, {"summary": {"class": "File", "location": "file:///w/s.json"}}])
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
async def test_receipt_at_non_file_location_is_contract_error() -> None:
    # The engine's download endpoint serves file:// only; a ws:// (post-staged)
    # receipt location is a contract mismatch for the whole run, not one bad doc.
    b = GoWeBackend(
        _Client({"receipts": [{"class": "File", "location": "ws:///a/home/r.json"}]}, {}),
        CWL_TEXT, poll_interval=0, timeout=1,
    )
    with pytest.raises(GoWeContractError, match="unsupported location"):
        await b.run_shards([[_wi(0)]], shard_fn=None)


@pytest.mark.asyncio
async def test_engine_failure_still_degrades_to_failed_items() -> None:
    """The existing contract for ENGINE-side failure is unchanged: a FAILED
    submission yields failed items, no exception (only the contract case raises)."""
    class _Failed(_Client):
        async def wait(self, sub_id, **kw):
            return {"id": sub_id, "state": "FAILED"}

    b = GoWeBackend(_Failed({}, {}), CWL_TEXT, poll_interval=0, timeout=1)
    results = await b.run_shards([[_wi(0)]], shard_fn=None)
    assert results[0].status == "failed" and "FAILED" in results[0].error
    run = await b.run_submission([_wi(0)])
    assert run.state == "FAILED" and run.archive_ref == ""


# --- archive_ref --------------------------------------------------------------- #

DEST = "ws:///alice@patricbrc.org/home/.ragstack/collections/lib1/versions/"


@pytest.mark.asyncio
async def test_archive_ref_derived_from_destination_and_version() -> None:
    loc, rec = _receipt("file:///w/r0.json", "a")
    outputs = {"receipts": [{"class": "File", "location": loc}],
               "archive": {"class": "Directory", "location": "file:///w/out/7"}}
    client = _Client(outputs, {loc: rec})
    b = GoWeBackend(client, CWL_TEXT, poll_interval=0, timeout=1)
    run = await b.run_submission([_wi(0)], inputs={"version": "7"}, output_destination=DEST)
    assert run.archive_ref == DEST + "7"
    assert client.submit_kwargs["output_destination"] == DEST
    assert client.submitted["version"] == "7"


@pytest.mark.asyncio
async def test_archive_ref_prefers_reported_ws_location() -> None:
    loc, rec = _receipt("file:///w/r0.json", "a")
    outputs = {"receipts": [{"class": "File", "location": loc}],
               "archive": {"class": "Directory", "location": DEST + "7/"}}
    b = GoWeBackend(_Client(outputs, {loc: rec}), CWL_TEXT, poll_interval=0, timeout=1)
    run = await b.run_submission([_wi(0)], inputs={"version": "7"}, output_destination=DEST)
    assert run.archive_ref == DEST + "7"


@pytest.mark.asyncio
async def test_no_destination_means_no_archive_ref() -> None:
    loc, rec = _receipt("file:///w/r0.json", "a")
    b = GoWeBackend(_Client({"receipts": [{"class": "File", "location": loc}]}, {loc: rec}),
                    CWL_TEXT, poll_interval=0, timeout=1)
    run = await b.run_submission([_wi(0)], inputs={"version": "1"})
    assert run.archive_ref == "" and run.results[0].status == "completed"


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
                                                       "outputs": out}})
        if req.method == "GET" and p == "/api/v1/files/download":
            r = ShardReceipt("s", "public", COMPLETED, n_docs=1, n_chunks=1, chunk_ids=["a"])
            return httpx.Response(200, content=r.to_json().encode())
        return httpx.Response(404, text="unexpected")


def _backend(engine: _FakeEngine) -> tuple[GoWeBackend, GoWeClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(engine))
    client = GoWeClient("http://gowe.test", token=OPERATOR_TOKEN, http=http)
    return GoWeBackend(client, CWL_TEXT, poll_interval=0, timeout=1), client


def _holds_token(obj: object, token: str) -> bool:
    return token in json.dumps(vars(obj), default=str)


@pytest.mark.asyncio
async def test_per_call_token_reaches_only_the_engine_headers_and_is_not_kept() -> None:
    engine = _FakeEngine()
    backend, client = _backend(engine)
    run = await backend.run_submission([_wi(0)], token=USER_TOKEN, output_destination=DEST,
                                       inputs={"version": "1"})
    assert run.results[0].status == "completed"
    by_call = dict(engine.auth)
    # Every request of the run went out as the user, not as the operator …
    for call in ("POST /api/v1/submissions", "GET /api/v1/submissions/sub_1",
                 "GET /api/v1/files/download"):
        assert by_call[call] == USER_TOKEN, call
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
