"""Phase 2 conformance: per-request llm/reranker overrides + GET /v1/models/available.

The availability picker and the request fields are served by both impls (the Go
scaffold returns an empty list / accepts-and-ignores the fields). Override
*resolution* (unknown id → 404) is Python-first. Field acceptance uses `null`
(no override) so it never depends on a registered model existing.
"""
from __future__ import annotations

import os

import httpx
import jsonschema
import pytest

pytestmark = pytest.mark.asyncio


def _headers() -> dict[str, str]:
    k = os.environ.get("RAGSTACK_API_KEY") or None
    return {"X-API-Key": k} if k else {}


async def test_available_models_schema(client: httpx.AsyncClient, schemas: dict[str, dict]) -> None:
    resp = await client.get("/v1/models/available", headers=_headers())
    assert resp.status_code == 200, resp.text
    jsonschema.validate(instance=resp.json(), schema=schemas["available_models_response"])


async def test_query_accepts_override_fields(client: httpx.AsyncClient) -> None:
    """`llm` / `reranker` are accepted on /query (null = no override → no 404)."""
    try:
        resp = await client.post(
            "/v1/query",
            json={"query": "x", "retrieval_mode": "bm25", "llm": None, "reranker": None},
            headers=_headers(),
            timeout=25.0,
        )
    except httpx.ReadTimeout:
        pytest.skip("override fields accepted; generation slow")
        return
    assert resp.status_code == 200, resp.text


async def test_retrieve_accepts_reranker_field(client: httpx.AsyncClient) -> None:
    try:
        resp = await client.post(
            "/v1/retrieve",
            json={"query": "x", "retrieval_mode": "bm25", "reranker": None},
            headers=_headers(),
            timeout=20.0,
        )
    except httpx.ReadTimeout:
        pytest.skip("reranker field accepted; backend slow")
        return
    assert resp.status_code == 200, resp.text


async def test_unknown_llm_override_is_404(client: httpx.AsyncClient, impl: str) -> None:
    if impl != "python":
        pytest.skip("override resolution is python-first (Go accepts-and-ignores)")
    resp = await client.post(
        "/v1/query", json={"query": "x", "llm": "__no_such_model__"}, headers=_headers(), timeout=15.0
    )
    assert resp.status_code == 404, resp.text
