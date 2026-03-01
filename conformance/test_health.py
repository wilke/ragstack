"""Conformance tests for the /health endpoint."""

from __future__ import annotations

import httpx
import pytest


pytestmark = pytest.mark.asyncio


async def test_health_returns_ok(client: httpx.AsyncClient) -> None:
    """GET /health must return 200 with ``{"status": "ok"}``."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
