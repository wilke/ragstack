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
