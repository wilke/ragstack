"""Conformance tests for the /v1/retrieve endpoint."""

from __future__ import annotations

import asyncio
import os
import time

import httpx
import pytest

from conftest import assert_degraded_valid, rerank_stub_url, skip_no_credential

pytestmark = pytest.mark.asyncio


async def test_retrieve_returns_200(client: httpx.AsyncClient, schemas: dict[str, dict]) -> None:
    """POST /v1/retrieve with a valid body returns 200 and has sources."""
    resp = await client.post("/v1/retrieve", json={"query": "vector databases"})
    assert resp.status_code == 200
    body = resp.json()
    assert "sources" in body
    # `degraded` (#748) is optional; when present it is the contract's shape.
    assert_degraded_valid(body, schemas)


async def test_rerank_outage_is_degraded_not_an_error(
    client: httpx.AsyncClient, schemas: dict[str, dict]
) -> None:
    """#748 part 2, end to end over HTTP: with the reranker sidecar failing,
    /v1/retrieve still answers 200 (decision 1 of #751) and SAYS so —
    ``degraded: [{component: "rerank", reason}]`` — and /v1/health/deep
    reports ``reranker`` as down from the same fact, without a probe of its
    own. When the sidecar recovers, the next real rerank clears both.

    Needs the self-booted stub sidecar (it is the only reranker a test may
    break) and an admin key for deep health; skips otherwise.
    """
    stub = rerank_stub_url()
    if not stub:
        pytest.skip("needs the conformance sidecar stub (RAGSTACK_CONFORMANCE_RERANK_STUB_URL)")
    admin = os.environ.get("RAGSTACK_API_KEY_ADMIN") or None
    if not admin:
        skip_no_credential("needs an admin key (RAGSTACK_API_KEY_ADMIN)")

    async def set_mode(mode: str) -> None:
        async with httpx.AsyncClient(base_url=stub, timeout=10.0) as c:
            r = await c.post("/__conformance/rerank", json={"mode": mode})
            assert r.status_code == 200, r.text

    def check(body: dict, name: str) -> dict:
        return next(c for c in body["checks"] if c["name"] == name)

    # The rerank stage only runs over a non-empty pool, and the self-booted
    # collections are empty — so seed one small document first (and remove it
    # after, so the collection is as the next file found it).
    name = "conformance-degraded-rerank.txt"
    up = await client.post(
        "/v1/ingest/upload",
        files={"files": (name, b"vector databases index embeddings for similarity search\n",
                         "text/plain")},
    )
    if up.status_code in (404, 501, 503):
        pytest.skip(f"POST /v1/ingest/upload answered {up.status_code}; cannot seed a document")
    assert up.status_code == 202, up.text
    job_id = up.json()["job_id"]
    deadline = time.monotonic() + 30.0
    while True:
        poll = await client.get(f"/v1/ingest/{job_id}")
        assert poll.status_code == 200, poll.text
        status = poll.json().get("status")
        if status == "completed":
            break
        assert status != "failed", poll.text
        assert time.monotonic() < deadline, f"seed ingest still {status!r} after 30 s"
        await asyncio.sleep(0.2)

    body_query = {"query": "vector databases", "top_k": 3}
    try:
        await set_mode("fail")
        try:
            resp = await client.post("/v1/retrieve", json=body_query)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["sources"], "the seeded document must be retrievable"
            assert_degraded_valid(body, schemas)
            assert [e["component"] for e in body.get("degraded", [])] == ["rerank"], body
            assert body["degraded"][0]["reason"]

            deep = await client.get("/v1/health/deep", headers={"X-API-Key": admin})
            assert deep.status_code == 200, deep.text
            rr = check(deep.json(), "reranker")
            assert rr["ok"] is False and rr["state"] == "down", rr
            assert deep.json()["status"] == "degraded"
        finally:
            await set_mode("ok")

        resp = await client.post("/v1/retrieve", json=body_query)
        assert resp.status_code == 200, resp.text
        assert resp.json()["sources"]
        assert "degraded" not in resp.json(), "a healthy rerank must omit the key entirely"
        deep = await client.get("/v1/health/deep", headers={"X-API-Key": admin})
        rr = check(deep.json(), "reranker")
        assert rr["ok"] is True and rr["state"] == "ok", rr
    finally:
        listing = await client.get("/v1/documents")
        if listing.status_code == 200:
            for doc in listing.json():
                if str(doc.get("source", "")).endswith(name):
                    await client.delete(f"/v1/documents/{doc['doc_id']}")
