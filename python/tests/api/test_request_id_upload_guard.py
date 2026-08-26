"""The outermost-ordering assertion.

``UploadContentLengthMiddleware`` refuses an oversized or chunked upload from the
request headers alone (#202): it hand-builds the 411/413 response and returns
**without ever calling the application**. So no exception handler, no router and
no inner middleware can stamp those responses — only a ``send``-wrapper installed
*outside* the guard can.

That makes this file a test of the `add_middleware` **order** in
``api/main.py``, not of the request id itself. If someone moves
``RequestContextMiddleware`` above ``UploadContentLengthMiddleware`` in that
file, everything else still passes and only this goes red. It is deliberately
kept separate from ``test_request_id.py`` so the failure names the cause.
"""
import re

import pytest

from ragstack.api.main import app
from ragstack.api.upload_guard import UploadContentLengthMiddleware
from ragstack.observability.middleware import RequestContextMiddleware

RID_RE = re.compile(r"^[0-9a-f]{16}$")
MULTIPART = "multipart/form-data; boundary=----x"


@pytest.fixture
def _bound(monkeypatch):
    """A tiny upload bound so a few bytes trip the 413.

    Both settings: ``content_length_limit()`` adds ``max_upload_files`` worth of
    multipart framing on top of the byte cap, so pinning only the cap leaves a
    several-kilobyte allowance.
    """
    from ragstack.config import settings

    monkeypatch.setattr(settings, "max_upload_bytes_per_request", 16)
    monkeypatch.setattr(settings, "max_upload_files", 0)


@pytest.mark.asyncio
async def test_guard_411_carries_the_request_id(client):
    """No Content-Length (chunked) — refused before the body is read."""
    r = await client.post(
        "/v1/ingest/upload",
        headers={"content-type": MULTIPART, "transfer-encoding": "chunked"},
        content=b"",
    )
    assert r.status_code == 411, r.text
    rid = r.headers.get("x-request-id")
    assert rid and RID_RE.match(rid), (
        "the upload guard's hand-built 411 lost X-Request-Id — "
        "RequestContextMiddleware is no longer outside UploadContentLengthMiddleware"
    )


@pytest.mark.asyncio
async def test_guard_413_carries_the_request_id(client, _bound):
    """Content-Length over the bound — refused before the body is read."""
    r = await client.post(
        "/v1/ingest/upload",
        headers={"content-type": MULTIPART},
        content=b"x" * 512,
    )
    assert r.status_code == 413, r.text
    rid = r.headers.get("x-request-id")
    assert rid and RID_RE.match(rid), (
        "the upload guard's hand-built 413 lost X-Request-Id — "
        "RequestContextMiddleware is no longer outside UploadContentLengthMiddleware"
    )


def test_middleware_order_is_pinned_explicitly():
    """State the invariant directly as well, so a reviewer reading the failure
    does not have to infer it from an HTTP status.

    ``app.user_middleware`` is ordered outermost-first (``add_middleware``
    inserts at the front), so the request-context middleware must come before
    the upload guard in that list.
    """
    classes = [m.cls for m in app.user_middleware]
    assert RequestContextMiddleware in classes
    assert UploadContentLengthMiddleware in classes
    assert classes.index(RequestContextMiddleware) < classes.index(
        UploadContentLengthMiddleware
    ), (
        "RequestContextMiddleware must be added LAST in api/main.py so it is the "
        "outermost middleware and can stamp the upload guard's hand-built refusals"
    )
