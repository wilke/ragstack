"""Conformance tests for the /v1/query endpoint."""

from __future__ import annotations

import httpx
import pytest


pytestmark = pytest.mark.asyncio


async def test_query_returns_200(client: httpx.AsyncClient) -> None:
    """POST /v1/query with a valid body returns 200 and expected fields."""
    resp = await client.post("/v1/query", json={"query": "What is RAG?"})
    assert resp.status_code == 200
    body = resp.json()
    assert "answer" in body
    assert "sources" in body
    assert "rewritten_queries" in body


async def test_query_missing_field_returns_422(client: httpx.AsyncClient) -> None:
    """POST /v1/query with an empty body must return 422."""
    resp = await client.post("/v1/query", json={})
    assert resp.status_code == 422
