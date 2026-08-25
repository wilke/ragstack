"""``api/upload_guard.py`` — the Content-Length guard in front of the multipart
parser for ``POST /v1/ingest/upload`` (#202 review).

FastAPI parses a multipart body before any dependency runs, so every gate in
the handler decides after the whole body has been received and spooled. The
middleware is the one check that runs BEFORE ``receive()``: over the cap →
413, no Content-Length → 411, both with ``Connection: close`` and without a
single body read. Pinned on a raw ASGI scope (where "receive was never
awaited" is provable) and through the real app (where the route path, the
prefix handling and CORS ordering are).
"""
from __future__ import annotations

import json

import pytest

from ragstack.api.upload_guard import (
    FRAMING_PER_FILE,
    UploadContentLengthMiddleware,
    content_length_limit,
)
from ragstack.config import settings

ROUTE = "/v1/ingest/upload"
MULTIPART = b"multipart/form-data; boundary=xyz"


class _Downstream:
    """Records whether the wrapped app was reached."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, scope, receive, send) -> None:
        self.calls.append(scope)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


async def _never_receive():
    raise AssertionError("receive() must not be awaited on a refusal")


def _scope(*, method="POST", path=ROUTE, root_path="", content_type=MULTIPART,
           content_length: bytes | None = b"10") -> dict:
    headers = [(b"content-type", content_type)] if content_type else []
    if content_length is not None:
        headers.append((b"content-length", content_length))
    return {"type": "http", "method": method, "path": path, "root_path": root_path,
            "headers": headers}


async def _run(scope, downstream=None, receive=_never_receive):
    downstream = downstream or _Downstream()
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    await UploadContentLengthMiddleware(downstream)(scope, receive, send)
    return downstream, sent


def _status(sent) -> int:
    return sent[0]["status"]


def _headers(sent) -> dict[str, str]:
    return {k.decode(): v.decode() for k, v in sent[0]["headers"]}


def _detail(sent) -> str:
    return json.loads(sent[1]["body"])["detail"]


def test_limit_is_cap_plus_framing(monkeypatch):
    monkeypatch.setattr(settings, "max_upload_bytes_per_request", 1000)
    monkeypatch.setattr(settings, "max_upload_files", 3)
    assert content_length_limit() == 1000 + 3 * FRAMING_PER_FILE
    monkeypatch.setattr(settings, "max_upload_bytes_per_request", 0)
    assert content_length_limit() == 0  # disabled


@pytest.mark.asyncio
async def test_over_limit_is_413_without_touching_the_body(monkeypatch):
    monkeypatch.setattr(settings, "max_upload_bytes_per_request", 1000)
    monkeypatch.setattr(settings, "max_upload_files", 1)
    limit = content_length_limit()
    downstream, sent = await _run(_scope(content_length=str(limit + 1).encode()))
    assert downstream.calls == []  # never reached the app, so never the parser
    assert _status(sent) == 413
    h = _headers(sent)
    assert h["connection"] == "close" and h["content-type"] == "application/json"
    assert int(h["content-length"]) == len(sent[1]["body"])
    assert f"exceeds the upload limit of {limit} bytes" in _detail(sent)
    assert sent[1]["more_body"] is False


@pytest.mark.asyncio
async def test_exactly_at_limit_passes_through(monkeypatch):
    monkeypatch.setattr(settings, "max_upload_bytes_per_request", 1000)
    monkeypatch.setattr(settings, "max_upload_files", 1)
    scope = _scope(content_length=str(content_length_limit()).encode())
    downstream, sent = await _run(scope, receive=_never_receive)
    assert downstream.calls == [scope] and _status(sent) == 200


@pytest.mark.parametrize("raw", [None, b"", b"abc", b"-1"])
@pytest.mark.asyncio
async def test_missing_or_unparseable_content_length_is_411(raw):
    downstream, sent = await _run(_scope(content_length=raw))
    assert downstream.calls == []
    assert _status(sent) == 411
    assert _headers(sent)["connection"] == "close"
    assert "Content-Length is required" in _detail(sent)


@pytest.mark.asyncio
async def test_disabled_cap_lets_chunked_uploads_through(monkeypatch):
    monkeypatch.setattr(settings, "max_upload_bytes_per_request", 0)
    downstream, sent = await _run(_scope(content_length=None))
    assert len(downstream.calls) == 1 and _status(sent) == 200


@pytest.mark.parametrize("scope", [
    _scope(method="GET", content_length=None),                       # not a POST
    _scope(path="/v1/ingest", content_type=b"application/json",
           content_length=None),                                     # another route
    _scope(path="/v1/ingest/uploads", content_length=None),          # not a segment match
    _scope(content_type=b"application/json", content_length=None),   # not multipart
    _scope(content_type=None, content_length=None),                  # no content type
    {"type": "lifespan"},                                            # not http
])
@pytest.mark.asyncio
async def test_everything_else_passes_untouched(scope):
    downstream = _Downstream()
    if scope["type"] == "lifespan":
        sent: list = []

        async def send(m):
            sent.append(m)

        await UploadContentLengthMiddleware(downstream)(scope, _never_receive, send)
        assert downstream.calls == [scope]
        return
    downstream, sent = await _run(scope, downstream)
    assert downstream.calls == [scope] and _status(sent) == 200


@pytest.mark.asyncio
async def test_matches_the_route_under_a_mounted_prefix(monkeypatch):
    """Behind the gateway the scope carries root_path + the full path; the
    guard matches on the ROUTE path, like the router does."""
    monkeypatch.setattr(settings, "max_upload_bytes_per_request", 10)
    monkeypatch.setattr(settings, "max_upload_files", 1)
    scope = _scope(path="/ragstack/t1/api" + ROUTE, root_path="/ragstack/t1/api",
                   content_length=b"99999")
    downstream, sent = await _run(scope)
    assert downstream.calls == [] and _status(sent) == 413
    # …and a prefix that is NOT in root_path is a different route.
    scope = _scope(path="/other" + ROUTE, root_path="", content_length=b"99999")
    downstream, sent = await _run(scope)
    assert len(downstream.calls) == 1


# --------------------------------------------------------------------------- #
# Through the real app
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_endpoint_413_from_content_length_before_the_parser(client, monkeypatch):
    from ragstack.api.main import app

    monkeypatch.setattr(settings, "max_upload_bytes_per_request", 100)
    monkeypatch.setattr(settings, "max_upload_files", 1)
    r = await client.post(
        "/v1/ingest/upload",
        files=[("files", ("big.pdf", b"%PDF" + b"\0" * 5000, "application/pdf"))],
    )
    assert r.status_code == 413, r.text
    # The middleware's wording, not the handler's — and no job was ever created,
    # which the handler would have done before its own size checks.
    assert "exceeds the upload limit" in r.json()["detail"]
    assert r.headers["connection"] == "close"
    assert app.state.job_store._jobs == {}
    # CORS headers still present: the guard sits inside CORSMiddleware.
    r = await client.post(
        "/v1/ingest/upload",
        files=[("files", ("big.pdf", b"%PDF" + b"\0" * 5000, "application/pdf"))],
        headers={"Origin": "http://example.test"},
    )
    assert r.status_code == 413 and "access-control-allow-origin" in r.headers


@pytest.mark.asyncio
async def test_endpoint_411_for_a_chunked_upload(client):
    async def _body():
        yield b"--xyz\r\nContent-Disposition: form-data; name=\"files\"; filename=\"a.pdf\"\r\n"
        yield b"Content-Type: application/pdf\r\n\r\n%PDF-1.4\r\n--xyz--\r\n"

    r = await client.post(
        "/v1/ingest/upload",
        content=_body(),
        headers={"Content-Type": "multipart/form-data; boundary=xyz"},
    )
    assert r.status_code == 411, r.text
    assert "Content-Length is required" in r.json()["detail"]


@pytest.mark.asyncio
async def test_endpoint_under_limit_reaches_the_handler(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ingest_root", str(tmp_path))
    r = await client.post(
        "/v1/ingest/upload",
        files=[("files", ("a.zip", b"PK\x03\x04", "application/zip"))],
    )
    assert r.status_code == 415  # the handler's allowlist, so the guard let it through
