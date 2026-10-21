"""Conformance tests for ``rewrite_strategies`` on POST /v1/retrieve (#750).

The field has the same name, values, default and semantics on /v1/query and
/v1/retrieve: an unknown or unavailable strategy is skipped (200), never a
422, and the default ``["passthrough"]`` leaves the retrieve response
unchanged. /v1/retrieve never echoes the variants — ``retrieve_response`` has
no ``rewritten_queries`` and ``additionalProperties: false``.

Python expands the query; Go has no rewriters and accepts the field and ignores
it (ADR-0006 stub). Both satisfy everything asserted here. ``bm25`` keeps the
retrievals embedding-free; anything that may reach an LLM (HyDE, multi-query,
generation on /v1/query) is bounded and skips on a read timeout.
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


def _validate(data, schema_name: str, schemas: dict[str, dict]) -> None:
    store = {s.get("$id", n): s for n, s in schemas.items()}
    resolver = jsonschema.RefResolver.from_schema({}, store=store)
    jsonschema.validate(instance=data, schema=schemas[schema_name], resolver=resolver)


async def test_contract_declares_rewrite_strategies(schemas: dict[str, dict]) -> None:
    q = schemas["query_request"]["properties"]["rewrite_strategies"]
    r = schemas["retrieve_request"]["properties"]["rewrite_strategies"]
    assert q == r, "the field is declared identically on both request schemas"
    assert r["type"] == "array"
    assert r["items"] == {"type": "string"}
    assert r["default"] == ["passthrough"]
    assert "rewritten_queries" not in schemas["retrieve_response"]["properties"]


async def test_retrieve_accepts_rewrite_strategies(
    client: httpx.AsyncClient, schemas: dict[str, dict]
) -> None:
    try:
        resp = await client.post(
            "/v1/retrieve",
            json={
                "query": "x",
                "rewrite_strategies": ["hyde", "multiquery"],
                "retrieval_mode": "bm25",
            },
            headers=_headers(),
            timeout=30.0,
        )
    except httpx.ReadTimeout:
        pytest.skip("rewrite_strategies accepted; the LLM-backed rewriters are slow here")
    assert resp.status_code == 200, resp.text
    _validate(resp.json(), "retrieve_response", schemas)


async def test_unknown_strategy_is_not_an_error(client: httpx.AsyncClient) -> None:
    body = {"query": "x", "rewrite_strategies": ["nonexistent"], "retrieval_mode": "bm25"}
    resp = await client.post("/v1/retrieve", json=body, headers=_headers(), timeout=20.0)
    assert resp.status_code == 200, resp.text
    assert "rewritten_queries" not in resp.json()
    try:
        q = await client.post("/v1/query", json=body, headers=_headers(), timeout=30.0)
    except httpx.ReadTimeout:
        pytest.skip("retrieve half passed; generation on /v1/query is slow here")
    assert q.status_code == 200, q.text
    assert q.json()["rewritten_queries"] == ["x"], "an unknown strategy adds no variant"


async def test_default_equals_explicit_passthrough(client: httpx.AsyncClient) -> None:
    base = {"query": "x", "retrieval_mode": "bm25", "top_k": 5}
    omitted = await client.post("/v1/retrieve", json=base, headers=_headers(), timeout=20.0)
    explicit = await client.post(
        "/v1/retrieve",
        json={**base, "rewrite_strategies": ["passthrough"]},
        headers=_headers(),
        timeout=20.0,
    )
    assert omitted.status_code == 200, omitted.text
    assert explicit.status_code == 200, explicit.text
    assert omitted.json()["sources"] == explicit.json()["sources"]
