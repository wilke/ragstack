"""#632: which GoWe failures may have written to the stores.

``may_have_written(exc)`` is True for an exception out of
``GoWeBackend.run_submission`` raised once the engine may have accepted the
submission; ``GoWeRun.may_have_written`` is True for a non-COMPLETED run whose
batches may have upserted. The API flags ``archive_pending`` on either, and the
flag never clears — so a submit the engine DEFINITELY did not take (no
connection, a 4xx refusal) must not be marked, while a timeout, a 5xx, a 408
and anything after submit must be.

A real ``GoWeClient`` over ``httpx.MockTransport`` so the client's own error
classification is what is under test; nothing live.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from ragstack.ingestion.gowe_backend import (
    GoWeBackend,
    GoWeContractError,
    GoWeRun,
    may_have_written,
)
from ragstack.ingestion.gowe_client import GoWeClient, GoWeError
from ragstack.ingestion.manifest import WorkItem
from ragstack.ingestion.receipts import (
    COMPLETED,
    NOTHING_TO_ARCHIVE_EXIT_CODE,
    DocRow,
    ShardReceipt,
    format_nothing_to_archive,
)

ITEMS = [WorkItem(item_id="a", source="/data/a.jsonl")]


def _backend(handler) -> tuple[GoWeBackend, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = GoWeClient("http://gowe.test", token="t", http=http)
    return GoWeBackend(client, "cwlVersion: v1.2\n", poll_interval=0, timeout=1), http


def _engine(*, on_submit=None, on_poll=None):
    """Registration always succeeds; ``on_submit`` / ``on_poll`` decide."""
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST" and req.url.path == "/api/v1/workflows":
            return httpx.Response(201, json={"data": {"id": "wf_1"}})
        if req.method == "POST" and req.url.path == "/api/v1/submissions":
            if on_submit is not None:
                return on_submit(req)
            return httpx.Response(201, json={"data": {"id": "sub_1", "state": "PENDING"}})
        if req.method == "GET" and req.url.path.startswith("/api/v1/submissions/"):
            if on_poll is not None:
                return on_poll(req)
            return httpx.Response(200, json={"data": {"id": "sub_1", "state": "COMPLETED",
                                                       "outputs": {}}})
        return httpx.Response(404, text="unexpected")
    return handler


async def _raised(handler) -> BaseException:
    backend, http = _backend(handler)
    try:
        with pytest.raises(BaseException) as ei:
            await backend.run_submission(ITEMS)
        return ei.value
    finally:
        await http.aclose()


def _status(code: int):
    return lambda req: httpx.Response(code, text="no")


def _connect_error(req):
    raise httpx.ConnectError("refused", request=req)


def _read_timeout(req):
    raise httpx.ReadTimeout("slow", request=req)


@pytest.mark.parametrize("code", [400, 401, 403, 404, 422, 429])
async def test_a_4xx_refusal_of_the_submit_is_not_marked(code) -> None:
    exc = await _raised(_engine(on_submit=_status(code)))
    assert isinstance(exc, GoWeError) and exc.status_code == code
    assert exc.definitely_not_accepted and not may_have_written(exc)


async def test_a_connect_error_on_the_submit_is_not_marked() -> None:
    exc = await _raised(_engine(on_submit=_connect_error))
    assert isinstance(exc, GoWeError) and exc.connect_error
    assert not may_have_written(exc)


@pytest.mark.parametrize("on_submit", [_status(503), _status(500), _status(408), _read_timeout],
                         ids=["503", "500", "408", "read-timeout"])
async def test_an_ambiguous_submit_failure_is_marked(on_submit) -> None:
    exc = await _raised(_engine(on_submit=on_submit))
    assert isinstance(exc, GoWeError) and not exc.definitely_not_accepted
    assert may_have_written(exc)


async def test_a_4xx_after_the_submit_was_accepted_is_marked() -> None:
    """A 404 on a POLL says nothing about the submission's tasks: it ran."""
    exc = await _raised(_engine(on_poll=_status(404)))
    assert isinstance(exc, GoWeError) and exc.status_code == 404
    assert may_have_written(exc)


async def test_a_registration_failure_is_not_marked() -> None:
    def handler(req):
        if req.url.path == "/api/v1/workflows":
            return httpx.Response(503, text="down")
        raise AssertionError("nothing may be submitted")
    exc = await _raised(handler)
    assert isinstance(exc, GoWeError) and not may_have_written(exc)


async def test_the_duplicate_basename_refusal_is_not_marked() -> None:
    """Raised before anything is registered or submitted."""
    def handler(req):
        raise AssertionError("nothing may reach the engine")
    backend, http = _backend(handler)
    try:
        with pytest.raises(GoWeContractError) as ei:
            await backend.run_submission(
                [WorkItem(item_id="a", source="ws:///u/x/a.pdf"),
                 WorkItem(item_id="b", source="ws:///u/y/a.pdf")],
                output_destination="ws:///u/home/v/", inputs={"version": "1"})
    finally:
        await http.aclose()
    assert not may_have_written(ei.value)


async def test_a_cancellation_after_submit_is_marked() -> None:
    backend, http = _backend(_engine())

    async def wait(sub_id, **kw):
        raise asyncio.CancelledError

    backend.client.wait = wait  # type: ignore[method-assign]
    try:
        with pytest.raises(asyncio.CancelledError) as ei:
            await backend.run_submission(ITEMS)
    finally:
        await http.aclose()
    assert may_have_written(ei.value)


async def test_an_unrelated_exception_is_not_marked() -> None:
    assert not may_have_written(RuntimeError("x"))
    assert not may_have_written(GoWeError("x"))


def _poll_record(state: str, error: dict | None = None):
    body = {"id": "sub_1", "state": state, "outputs": {}}
    if error is not None:
        body["error"] = error
    return lambda req: httpx.Response(200, json={"data": body})


async def _run(on_poll) -> GoWeRun:
    backend, http = _backend(_engine(on_poll=on_poll))
    try:
        return await backend.run_submission(ITEMS)
    finally:
        await http.aclose()


@pytest.mark.parametrize("state", ["FAILED", "CANCELLED"])
async def test_a_non_completed_run_may_have_written(state) -> None:
    run = await _run(_poll_record(state, {"code": "TASK_FAILED", "message": "boom"}))
    assert run.state == state and run.may_have_written and run.archive_ref == ""


async def test_a_nothing_to_archive_run_wrote_nothing() -> None:
    stderr = format_nothing_to_archive([ShardReceipt("b0", "t", COMPLETED, docs=[
        DocRow(doc_id="a", source="/stage/a.jsonl", error="no text")])])
    run = await _run(_poll_record("FAILED", {
        "code": "TASK_FAILED", "message": "pack failed",
        "context": {"exit_code": -1, "stderr":
                    f"execute: apptainer command failed (exit {NOTHING_TO_ARCHIVE_EXIT_CODE}): "
                    + stderr}}))
    assert run.state == "FAILED" and not run.may_have_written


async def test_a_completed_run_is_not_flagged() -> None:
    def poll(req):
        return httpx.Response(200, json={"data": {"id": "sub_1", "state": "COMPLETED",
                                                   "outputs": {"receipts": []}}})
    run = await _run(poll)
    assert run.state == "COMPLETED" and not run.may_have_written


def test_the_status_survives_on_the_error() -> None:
    e = GoWeError("POST /x -> 400: no", status_code=400)
    assert (e.status_code, e.connect_error, str(e)) == (400, False, "POST /x -> 400: no")
    assert json.dumps(str(e))  # still a plain message
