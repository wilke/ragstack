"""Tests for GoWeClient — the REST wrapper over the GoWe engine (ADR-0001 2b).

The API-shape tests use httpx.MockTransport (no server). A guarded live round-trip
(GOWE_LIVE=1) exercises the real server end-to-end with a BV-BRC token.
"""
from __future__ import annotations

import json
import os

import httpx
import pytest

from ragstack.ingestion.gowe_client import GoWeClient, GoWeError, load_bvbrc_token

TOKEN = "un=test@bvbrc|tokenid=abc|expiry=9999999999|sig=x"


def _client(handler) -> GoWeClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GoWeClient("http://gowe.test", token=TOKEN, http=http)


@pytest.mark.asyncio
async def test_register_submit_wait_download_roundtrip() -> None:
    seen: list[tuple[str, str]] = []
    poll = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path))
        assert req.headers.get("Authorization") == TOKEN  # raw token, no Bearer
        p = req.url.path
        if req.method == "POST" and p == "/api/v1/workflows":
            assert json.loads(req.content)["name"] == "wf-name"
            return httpx.Response(201, json={"data": {"id": "wf_1"}})
        if req.method == "POST" and p == "/api/v1/submissions":
            assert req.url.query == b""  # not a dry-run
            return httpx.Response(201, json={"data": {"id": "sub_1", "state": "PENDING"}})
        if req.method == "GET" and p == "/api/v1/submissions/sub_1":
            poll["n"] += 1
            state = "RUNNING" if poll["n"] == 1 else "COMPLETED"
            out = {"greeting": {"class": "File", "location": "file:///d/g.txt"}}
            return httpx.Response(200, json={"data": {"id": "sub_1", "state": state,
                                                       "outputs": out}})
        if req.method == "GET" and p == "/api/v1/files/download":
            assert req.url.params["location"] == "file:///d/g.txt"
            return httpx.Response(200, content=b"hello")
        return httpx.Response(404, text="unexpected")

    c = _client(handler)
    wf = await c.register_workflow("wf-name", "cwlVersion: v1.2")
    assert wf == "wf_1"
    sub = await c.submit(wf, {"x": 1})
    assert sub["id"] == "sub_1"
    final = await c.wait("sub_1", poll_interval=0)  # RUNNING then COMPLETED
    assert final["state"] == "COMPLETED"
    content = await c.download(final["outputs"]["greeting"]["location"])
    assert content == b"hello"
    assert poll["n"] == 2  # polled until terminal
    await c.close()


@pytest.mark.asyncio
async def test_download_empty_body_returns_empty_not_local_read() -> None:
    # A 200 with an empty body must yield b"" — never a local-filesystem read of
    # the server-side path (the removed footgun).
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")
    c = _client(handler)
    assert await c.download("file:///etc/passwd") == b""
    await c.close()


@pytest.mark.asyncio
async def test_dry_run_sets_query() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.query == b"dry_run=true"
        return httpx.Response(200, json={"data": {"valid": True}})
    c = _client(handler)
    await c.submit("wf_1", {}, dry_run=True)
    await c.close()


@pytest.mark.asyncio
async def test_error_status_raises_goweerror() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(422, text="bad cwl")
    c = _client(handler)
    with pytest.raises(GoWeError, match="422"):
        await c.register_workflow("n", "bad")
    await c.close()


@pytest.mark.asyncio
async def test_wait_times_out() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"id": "s", "state": "RUNNING"}})
    c = _client(handler)
    with pytest.raises(GoWeError, match="not terminal"):
        await c.wait("s", poll_interval=0, timeout=0)
    await c.close()


def test_load_token_env_precedence(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GOWE_TOKEN", "un=envuser|x")
    assert load_bvbrc_token() == "un=envuser|x"


def test_load_token_from_json_and_bare_files(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("GOWE_TOKEN", raising=False)
    monkeypatch.delenv("BVBRC_TOKEN", raising=False)
    cred = tmp_path / "credentials.json"
    cred.write_text(json.dumps({"token": "un=jsonuser|x"}), encoding="utf-8")
    bare = tmp_path / "patric_token"
    bare.write_text("un=bareuser|x", encoding="utf-8")
    # patch the module's file list to point at our temp files
    import ragstack.ingestion.gowe_client as gc
    monkeypatch.setattr(gc, "_TOKEN_FILES", (str(cred), str(bare)))
    assert load_bvbrc_token() == "un=jsonuser|x"  # json file wins (first in list)
    monkeypatch.setattr(gc, "_TOKEN_FILES", (str(bare),))
    assert load_bvbrc_token() == "un=bareuser|x"


# --------------------------------------------------------------------------- #
# Live round-trip against the real GoWe server (opt-in). Registers a trivial
# python workflow, submits with a BV-BRC token, waits, downloads the output.
# --------------------------------------------------------------------------- #
_LIVE_CWL = """cwlVersion: v1.2
class: CommandLineTool
baseCommand: [python, -c]
arguments:
  - position: 1
    valueFrom: |
      import sys; open('out.txt','w').write("live:"+sys.argv[1])
inputs:
  msg: {type: string, inputBinding: {position: 2}}
outputs:
  out: {type: File, outputBinding: {glob: out.txt}}
"""


@pytest.mark.skipif(not os.environ.get("GOWE_LIVE"), reason="set GOWE_LIVE=1 for the live GoWe round-trip")
@pytest.mark.asyncio
async def test_live_roundtrip() -> None:
    if not load_bvbrc_token():
        pytest.skip("no BV-BRC token available")
    c = GoWeClient()  # real server + token from files
    try:
        wf = await c.register_workflow("ragstack-goweclient-test", _LIVE_CWL)
        sub = await c.submit(wf, {"msg": "pytest"})
        final = await c.wait(sub["id"], poll_interval=2.0, timeout=180)
        assert final["state"] == "COMPLETED", final
        content = await c.download(final["outputs"]["out"]["location"])
        assert content.decode().strip() == "live:pytest"
    finally:
        await c.close()
