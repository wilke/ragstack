"""Conformance: ``GET /health`` — the one anonymous operation, and the
``X-Request-Id`` drift pin (the same properties the tenant suite pins in
``test_request_id.py``, asserted here against the Go daemon)."""

from __future__ import annotations

import httpx
import pytest

from ctl.helpers import RID_RE, assert_request_id, validate

pytestmark = pytest.mark.asyncio


async def test_health_is_anonymous_and_conforms(
    anon_client: httpx.AsyncClient, schemas: dict[str, dict]
) -> None:
    resp = await anon_client.get("/health")
    assert resp.status_code == 200, resp.text
    validate(resp.json(), "health_response", schemas)
    assert_request_id(resp)


async def test_health_says_whether_the_job_engine_is_available(
    anon_client: httpx.AsyncClient,
) -> None:
    """``engine`` is the half of a daemon's health a liveness check cannot see.

    ``status`` is pinned to ``ok`` by the schema and says only that the process
    is answering. A daemon whose job store cannot be opened answers every read
    perfectly and refuses every mutation with 409 ``refused`` — and on
    2026-09-17 one did exactly that for a day, because the only place it said so
    was a WARN line at start-up. This field is that fact, on the one endpoint
    that needs no credential, so a probe can raise it.

    A daemon this harness booted has a store of its own, so the answer here is
    ``available``; ``unavailable`` from this fixture would mean the harness's own
    scratch state directory is unwritable.
    """
    body = (await anon_client.get("/health")).json()
    assert body["engine"] == "available", (
        f"the fixture daemon reports engine={body['engine']!r}: its job store could not be opened"
    )


async def test_request_id_differs_between_requests(anon_client: httpx.AsyncClient) -> None:
    first = assert_request_id(await anon_client.get("/health"))
    second = assert_request_id(await anon_client.get("/health"))
    assert first != second, "two requests received the same X-Request-Id"


async def test_inbound_request_id_is_never_echoed(anon_client: httpx.AsyncClient) -> None:
    resp = await anon_client.get("/health", headers={"X-Request-ID": "conformance-upstream-id.1"})
    rid = assert_request_id(resp)
    assert rid != "conformance-upstream-id.1"


async def test_hostile_inbound_request_id_is_not_echoed(anon_client: httpx.AsyncClient) -> None:
    resp = await anon_client.get("/health", headers={"X-Request-ID": "z" * 512})
    assert RID_RE.match(assert_request_id(resp))


async def test_unknown_route_is_an_error_body_with_a_request_id(
    client: httpx.AsyncClient, schemas: dict[str, dict]
) -> None:
    """The whole point of the id: it is on the responses a user reports. An
    unknown route under ``/v1`` is a 404 ``not_found`` in the contract's error
    shape — not a router's bare text."""
    from ctl.helpers import assert_error

    resp = await client.get("/v1/this-route-does-not-exist")
    assert_error(resp, 404, "not_found", schemas)
