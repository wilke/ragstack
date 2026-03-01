"""Conformance tests for the /v1/retrieve endpoint."""

from __future__ import annotations

import httpx
import pytest


pytestmark = pytest.mark.asyncio


async def test_retrieve_returns_200(client: httpx.AsyncClient) -> None:
    """POST /v1/retrieve with a valid body returns 200 and has sources."""
    resp = await client.post("/v1/retrieve", json={"query": "vector databases"})
    assert resp.status_code == 200
    body = resp.json()
    assert "sources" in body
