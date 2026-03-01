"""Conformance tests for the /v1/ingest endpoint."""

from __future__ import annotations

import httpx
import pytest


pytestmark = pytest.mark.asyncio


async def test_ingest_returns_accepted(client: httpx.AsyncClient) -> None:
    """POST /v1/ingest returns 200 with status 'accepted' and a job_id."""
    resp = await client.post("/v1/ingest", json={"source": "/tmp/test.txt"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "accepted"
    assert "job_id" in body


async def test_ingest_status_returns_response(client: httpx.AsyncClient) -> None:
    """GET /v1/ingest/{job_id} returns 200 with job_id and status."""
    resp = await client.get("/v1/ingest/fake-job-id")
    assert resp.status_code == 200
    body = resp.json()
    assert "job_id" in body
    assert "status" in body
