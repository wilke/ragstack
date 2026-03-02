"""Conformance tests for the /v1/documents endpoint."""

from __future__ import annotations

import httpx
import pytest


pytestmark = pytest.mark.asyncio


async def test_list_documents_returns_list(client: httpx.AsyncClient) -> None:
    """GET /v1/documents returns 200 and a list."""
    resp = await client.get("/v1/documents")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)


async def test_delete_document_returns_204(client: httpx.AsyncClient) -> None:
    """DELETE /v1/documents/{doc_id} returns 204 for a nonexistent doc."""
    resp = await client.delete("/v1/documents/nonexistent")
    assert resp.status_code == 204
