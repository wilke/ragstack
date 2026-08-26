"""Conformance: every response carries an ``X-Request-Id`` (#427).

Black-box over HTTP against a running server, so it is the drift pin between the
two implementations that #419's post-mortem asked for.

.. rubric:: Expected-red window — read this before deleting anything

This test lands with #427 **W1**, which is the Python side. Go does not satisfy
it until **W7** (wire ``observability.LoggingMiddleware``, stop discarding the
logger in ``router.go``, echo the header from chi's ``middleware.GetReqID``). So
it is skipped for ``RAGSTACK_IMPL=go`` in the meantime, deliberately, rather than
left red: a permanently-red target trains people to ignore the target, which is
exactly how the #419 drift happened.

**W7 deletes ``requires_request_id`` and this paragraph.** The deletion is part
of W7's own diff and therefore cannot be forgotten.

``/health`` is the subject because it is unauthenticated and exists on both
implementations, so this needs no credentials and no seeded data.
"""

from __future__ import annotations

import os
import re

import httpx
import pytest

pytestmark = pytest.mark.asyncio

#: 16 lowercase hex characters — the format contracts/openapi.yaml documents at
#: components/headers/XRequestId.
RID_RE = re.compile(r"^[0-9a-f]{16}$")

#: Deleted by #427 W7, together with the module docstring's note about it.
requires_request_id = pytest.mark.skipif(
    os.environ.get("RAGSTACK_IMPL") == "go",
    reason="Go echoes X-Request-Id from #427 W7; this skip is deleted in that PR",
)


@requires_request_id
async def test_request_id_header_is_present_and_well_formed(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200

    rid = resp.headers.get("X-Request-Id")
    assert rid, "no X-Request-Id header on the response"
    assert RID_RE.match(rid), f"X-Request-Id {rid!r} does not match {RID_RE.pattern}"


@requires_request_id
async def test_request_id_differs_between_requests(client: httpx.AsyncClient) -> None:
    """The property that makes the id useful at all: two concurrent users'
    failures must be distinguishable in the log."""
    first = (await client.get("/health")).headers.get("X-Request-Id")
    second = (await client.get("/health")).headers.get("X-Request-Id")
    assert first and second
    assert first != second, "two requests received the same X-Request-Id"


@requires_request_id
async def test_inbound_request_id_is_never_echoed(client: httpx.AsyncClient) -> None:
    """The server always generates its own id. A caller-supplied one is recorded
    for gateway correlation but never becomes the response's — otherwise a
    client could forge an id, or make two requests indistinguishable."""
    inbound = "conformance-upstream-id.1"
    resp = await client.get("/health", headers={"X-Request-ID": inbound})

    rid = resp.headers.get("X-Request-Id")
    assert rid and rid != inbound, "the server echoed a caller-supplied request id"
    assert RID_RE.match(rid)


@requires_request_id
async def test_hostile_inbound_request_id_is_not_echoed(
    client: httpx.AsyncClient,
) -> None:
    """A value that would forge a log line or flood the log must be dropped, not
    reflected. httpx rejects a literal newline in a header, so the length cap is
    what is exercised over the wire here."""
    resp = await client.get("/health", headers={"X-Request-ID": "z" * 512})

    rid = resp.headers.get("X-Request-Id")
    assert rid and RID_RE.match(rid), f"X-Request-Id {rid!r} is not a server-generated id"


@requires_request_id
async def test_request_id_is_present_on_an_error_response(
    client: httpx.AsyncClient,
) -> None:
    """The whole point: the id has to be on the responses a user reports, not
    just on the ones that worked."""
    resp = await client.get("/v1/this-route-does-not-exist")
    assert resp.status_code in (401, 403, 404), resp.status_code

    rid = resp.headers.get("X-Request-Id")
    assert rid, f"no X-Request-Id on a {resp.status_code}"
    assert RID_RE.match(rid)
