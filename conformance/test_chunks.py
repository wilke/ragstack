"""Conformance tests for GET /v1/chunks (fetch chunks by id — context expansion).

The endpoint is served by BOTH implementations (the Go scaffold returns an empty,
schema-valid chunk list), so the shape tests run on every impl. Fetching real
neighbour content is data-dependent and covered by the Python implementation
live; here we assert the contract: empty/unknown ids → an empty, schema-valid
`chunks` array, and unknown-collection → 404 (Python-first).
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


def _validate(data, schemas: dict[str, dict]) -> None:
    store = {s.get("$id", n): s for n, s in schemas.items()}
    resolver = jsonschema.RefResolver.from_schema({}, store=store)
    jsonschema.validate(instance=data, schema=schemas["chunks_response"], resolver=resolver)


async def test_chunks_no_ids_is_empty(client: httpx.AsyncClient, schemas: dict[str, dict]) -> None:
    """No ids → an empty, schema-valid chunk list (not an error)."""
    resp = await client.get("/v1/chunks", headers=_headers())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _validate(body, schemas)
    assert body["chunks"] == []


async def test_chunks_unknown_id_omitted(
    client: httpx.AsyncClient, schemas: dict[str, dict]
) -> None:
    """An id that doesn't exist is silently omitted — still a 200 + valid shape."""
    resp = await client.get(
        "/v1/chunks", params={"ids": "__no_such_chunk_id__"}, headers=_headers(), timeout=20.0
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _validate(body, schemas)
    assert body["chunks"] == []


async def test_chunks_unknown_collection_is_404(client: httpx.AsyncClient, impl: str) -> None:
    if impl != "python":
        pytest.skip("unknown-collection 404 is python-first behavior in phase 1")
    resp = await client.get(
        "/v1/chunks",
        params={"ids": "x", "collection": "__no_such_collection__"},
        headers=_headers(),
    )
    assert resp.status_code == 404, resp.text
