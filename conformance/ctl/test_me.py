"""Conformance: ``GET /v1/me`` — the only place a client learns its role."""

from __future__ import annotations

import httpx
import pytest

from ctl.helpers import assert_request_id, find_secret_names, validate

pytestmark = pytest.mark.asyncio


async def test_me_as_operator(client: httpx.AsyncClient, schemas: dict[str, dict]) -> None:
    resp = await client.get("/v1/me")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    validate(body, "me_response", schemas)
    assert_request_id(resp)
    assert body["role"] == "operator"
    assert body["auth_method"] == "api_key"
    assert body["expires_at"] is None, "a ctl key does not expire; it is revoked"
    assert body["sudo_user"] is None, "sudo_user is a --direct CLI fact, never seen over HTTP"
    assert find_secret_names(body) == []


async def test_me_as_viewer(viewer_client: httpx.AsyncClient, schemas: dict[str, dict]) -> None:
    resp = await viewer_client.get("/v1/me")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    validate(body, "me_response", schemas)
    assert body["role"] == "viewer"
    assert body["auth_method"] == "api_key"


async def test_me_never_echoes_the_key(client: httpx.AsyncClient, ctl_key: str) -> None:
    resp = await client.get("/v1/me")
    assert ctl_key not in resp.text, "the credential that authenticated the request is in the body"
