"""Conformance tests for the /v1/ingest endpoint."""

from __future__ import annotations

import httpx
import pytest


pytestmark = pytest.mark.asyncio


async def test_ingest_returns_accepted(client: httpx.AsyncClient) -> None:
    """POST /v1/ingest returns 200 with status 'accepted' and a job_id."""
    resp = await client.post("/v1/ingest", json={"source": "/tmp/test.txt"})
    if resp.status_code == 503:
        # Conformant: a server with no INGEST_ROOT disables ingest, because an
        # unconfined `source` would be an arbitrary server-side file read. Not a
        # failure — there is simply no accepted-job contract to assert.
        pytest.skip("target server has no INGEST_ROOT; /v1/ingest disabled (503)")
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


async def test_ingest_rejects_an_unknown_duplicate_policy(client: httpx.AsyncClient) -> None:
    """#747: `duplicate_policy` is an enum (refuse | allow | null); any other
    value is a 422 on both implementations — before INGEST_ROOT is consulted,
    since it is a body validation error."""
    resp = await client.post(
        "/v1/ingest", json={"source": "/tmp/test.txt", "duplicate_policy": "bogus"}
    )
    if resp.status_code == 503:
        pytest.skip("target server has no INGEST_ROOT; /v1/ingest disabled (503)")
    assert resp.status_code == 422, resp.text


async def test_ingest_accepts_duplicate_policy_allow(client: httpx.AsyncClient) -> None:
    """#747: `duplicate_policy: "allow"` is part of IngestRequest — the same
    accepted-job contract as a request without it."""
    resp = await client.post(
        "/v1/ingest", json={"source": "/tmp/test.txt", "duplicate_policy": "allow"}
    )
    if resp.status_code == 503:
        pytest.skip("target server has no INGEST_ROOT; /v1/ingest disabled (503)")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "accepted"
    assert "job_id" in body


async def test_ingest_accepts_a_source_whose_suffix_has_no_loader(
    client: httpx.AsyncClient,
) -> None:
    """#828 PR 5a (#833 item 1): the loader is chosen per file, in the
    background — so a source whose suffix has no loader (`.rst` here) is still
    accepted at the request level. It is never a 415/422: it becomes a failed
    *item* with `no loader for .rst`, which `test_ingest_no_loader.py` pins
    against a server that actually runs the job."""
    resp = await client.post("/v1/ingest", json={"source": "/tmp/test.rst"})
    if resp.status_code == 503:
        pytest.skip("target server has no INGEST_ROOT; /v1/ingest disabled (503)")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "accepted"
    assert "job_id" in body
