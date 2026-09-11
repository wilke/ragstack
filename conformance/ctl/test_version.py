"""Conformance: ``GET /v1/version`` (viewer)."""

from __future__ import annotations

import httpx
import pytest

from ctl.helpers import assert_request_id, validate

pytestmark = pytest.mark.asyncio


async def test_version_conforms(client: httpx.AsyncClient, schemas: dict[str, dict]) -> None:
    resp = await client.get("/v1/version")
    assert resp.status_code == 200, resp.text
    validate(resp.json(), "version_response", schemas)
    assert_request_id(resp)


async def test_version_agrees_with_health(
    client: httpx.AsyncClient, anon_client: httpx.AsyncClient
) -> None:
    """One binary, one version string — the anonymous liveness body and the
    authenticated build identity must name the same build."""
    health = (await anon_client.get("/health")).json()
    version = (await client.get("/v1/version")).json()
    assert health["version"] == version["version"]


async def test_version_requires_a_credential(anon_client: httpx.AsyncClient) -> None:
    resp = await anon_client.get("/v1/version")
    assert resp.status_code == 401, resp.text
