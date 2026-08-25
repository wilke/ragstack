"""Conformance tests for ``collections`` on POST /v1/query and /v1/retrieve
(issue #253 — multi-collection fused retrieval).

Black-box: the contract declares the field (1–5 unique ids, mutually
exclusive with ``collection``) and the ``Source.collection`` stamp; a
single-element list is accepted and the response stays schema-valid with
every source stamped with that id. The 422s are Python-first: the Go handler
is a stub per ADR-0006. What data comes back is deployment-dependent, so only
the shape is asserted.
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


async def test_contract_declares_collections(schemas: dict[str, dict]) -> None:
    for name in ("query_request", "retrieve_request"):
        prop = schemas[name]["properties"]["collections"]
        assert prop["type"] == ["array", "null"]
        assert prop["items"] == {"type": "string"}
        assert (prop["minItems"], prop["maxItems"], prop["uniqueItems"]) == (1, 5, True)
        assert prop["default"] is None
    assert schemas["source"]["properties"]["collection"]["type"] == "string"
    assert "collection" not in schemas["source"]["required"]


async def _default_collection_id(client: httpx.AsyncClient) -> str:
    resp = await client.get("/v1/collections", headers=_headers())
    assert resp.status_code == 200, resp.text
    return resp.json()["default"]


@pytest.mark.parametrize("endpoint,schema", [("/v1/retrieve", "retrieve_response"), ("/v1/query", "query_response")])
async def test_single_element_collections_accepted_and_stamped(
    client: httpx.AsyncClient, schemas: dict[str, dict], impl: str, endpoint: str, schema: str
) -> None:
    cid = await _default_collection_id(client)
    resp = await client.post(
        endpoint, json={"query": "What is RAG?", "top_k": 3, "collections": [cid]}, headers=_headers()
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _validate(body, schema, schemas)
    if impl == "python":
        assert all(s["collection"] == cid for s in body["sources"])


@pytest.mark.parametrize("endpoint", ["/v1/retrieve", "/v1/query"])
async def test_singular_form_carries_no_stamp(client: httpx.AsyncClient, endpoint: str) -> None:
    resp = await client.post(endpoint, json={"query": "What is RAG?"}, headers=_headers())
    assert resp.status_code == 200, resp.text
    assert all("collection" not in s for s in resp.json()["sources"])


@pytest.mark.parametrize("endpoint", ["/v1/retrieve", "/v1/query"])
@pytest.mark.parametrize(
    "extra",
    [
        {"collection": "x", "collections": ["y"]},   # both forms
        {"collections": [f"c{i}" for i in range(6)]},  # N = 6
        {"collections": ["a", "a"]},                  # duplicates
        {"collections": []},                          # empty
    ],
    ids=["both", "six", "duplicates", "empty"],
)
async def test_invalid_collections_is_422(
    client: httpx.AsyncClient, impl: str, endpoint: str, extra: dict
) -> None:
    if impl != "python":
        pytest.skip("request validation is python-first; the Go handler is a stub (ADR-0006)")
    resp = await client.post(endpoint, json={"query": "What is RAG?", **extra}, headers=_headers())
    assert resp.status_code == 422, resp.text
