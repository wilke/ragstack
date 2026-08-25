"""Conformance tests for ``context_window`` on POST /v1/query and /v1/retrieve
(issue #322 — server-side neighbour expansion).

Black-box: the request field is accepted on both endpoints, the response stays
schema-valid (``Source.context`` is the only addition, optional), a source's
``context`` — when present — is a position-ordered list of neighbour chunks,
and the default leaves ``context`` out entirely. Whether any neighbour is
actually attached is data-dependent (needs a linked corpus), so that is only
asserted when it shows up. The cap (4 → 422) is Python-first: the Go handler
is a stub per ADR-0006.
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


def _check_context(sources: list[dict]) -> None:
    for s in sources:
        if "context" not in s:
            continue
        ctx = s["context"]
        assert isinstance(ctx, list) and ctx, "context, when present, is a non-empty list"
        positions = [c["position"] for c in ctx]
        assert positions == sorted(positions), "context is ordered by position"
        assert 0 not in positions, "the source itself is never in its own context"
        assert all(abs(p) <= 3 for p in positions)
        assert all(c["chunk_id"] != s["chunk_id"] for c in ctx)


async def test_contract_declares_context_window(schemas: dict[str, dict]) -> None:
    for name in ("query_request", "retrieve_request"):
        prop = schemas[name]["properties"]["context_window"]
        assert prop["type"] == "integer"
        assert (prop["minimum"], prop["maximum"], prop["default"]) == (0, 3, 0)
    ctx = schemas["source"]["properties"]["context"]
    assert ctx["type"] == "array"
    assert set(ctx["items"]["required"]) == {"chunk_id", "position", "content"}
    assert ctx["items"]["additionalProperties"] is False


@pytest.mark.parametrize("endpoint,schema", [("/v1/retrieve", "retrieve_response"), ("/v1/query", "query_response")])
async def test_context_window_accepted_and_schema_valid(
    client: httpx.AsyncClient, schemas: dict[str, dict], endpoint: str, schema: str
) -> None:
    resp = await client.post(
        endpoint,
        json={"query": "What is RAG?", "top_k": 3, "context_window": 1},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _validate(body, schema, schemas)
    _check_context(body["sources"])


@pytest.mark.parametrize("endpoint", ["/v1/retrieve", "/v1/query"])
async def test_default_window_attaches_no_context(
    client: httpx.AsyncClient, endpoint: str
) -> None:
    resp = await client.post(endpoint, json={"query": "What is RAG?"}, headers=_headers())
    assert resp.status_code == 200, resp.text
    assert all("context" not in s for s in resp.json()["sources"])


@pytest.mark.parametrize("endpoint", ["/v1/retrieve", "/v1/query"])
async def test_ranking_unchanged_by_window(client: httpx.AsyncClient, endpoint: str) -> None:
    body = {"query": "What is RAG?", "top_k": 5}
    plain = await client.post(endpoint, json=body, headers=_headers())
    expanded = await client.post(endpoint, json={**body, "context_window": 1}, headers=_headers())
    assert plain.status_code == 200 and expanded.status_code == 200
    rank = lambda r: [(s["chunk_id"], s["score"]) for s in r.json()["sources"]]  # noqa: E731
    assert rank(plain) == rank(expanded)


@pytest.mark.parametrize("endpoint", ["/v1/retrieve", "/v1/query"])
async def test_context_window_above_cap_is_422(
    client: httpx.AsyncClient, impl: str, endpoint: str
) -> None:
    if impl != "python":
        pytest.skip("request validation is python-first; the Go handler is a stub (ADR-0006)")
    resp = await client.post(
        endpoint, json={"query": "What is RAG?", "context_window": 4}, headers=_headers()
    )
    assert resp.status_code == 422, resp.text
