"""Conformance tests for the /v1/graph endpoints."""

from __future__ import annotations

import httpx
import pytest


pytestmark = pytest.mark.asyncio


async def test_graph_entities_returns_list(client: httpx.AsyncClient) -> None:
    """GET /v1/graph/entities returns 200 and a list."""
    resp = await client.get("/v1/graph/entities")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)


async def test_graph_neighbors_returns_list(client: httpx.AsyncClient) -> None:
    """GET /v1/graph/neighbors/Alice returns 200 and a list."""
    resp = await client.get("/v1/graph/neighbors/Alice")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
