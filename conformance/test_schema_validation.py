"""Conformance tests that validate API responses against JSON schemas.

Each test hits a real endpoint and validates the response body against
the corresponding schema loaded from ``contracts/schemas/``.
"""

from __future__ import annotations

import httpx
import jsonschema
import pytest


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_resolver(schemas: dict[str, dict]) -> jsonschema.RefResolver:
    """Build a JSON-Schema ``RefResolver`` so that ``$ref`` between schemas
    (e.g. ``source.json``) are resolved from the loaded schemas dict.
    """
    schema_store = {s.get("$id", name): s for name, s in schemas.items()}
    # Use an empty schema as the base; individual tests pass the real schema.
    return jsonschema.RefResolver.from_schema({}, store=schema_store)


def _validate(data: dict | list, schema: dict, schemas: dict[str, dict]) -> None:
    """Validate *data* against *schema*, resolving ``$ref`` via *schemas*."""
    resolver = _build_resolver(schemas)
    jsonschema.validate(instance=data, schema=schema, resolver=resolver)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_health_schema(
    client: httpx.AsyncClient,
    schemas: dict[str, dict],
) -> None:
    """GET /health response conforms to health_response schema."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    _validate(resp.json(), schemas["health_response"], schemas)


async def test_query_schema(
    client: httpx.AsyncClient,
    schemas: dict[str, dict],
) -> None:
    """POST /v1/query response conforms to query_response schema."""
    resp = await client.post("/v1/query", json={"query": "What is RAG?"})
    assert resp.status_code == 200
    _validate(resp.json(), schemas["query_response"], schemas)


async def test_retrieve_schema(
    client: httpx.AsyncClient,
    schemas: dict[str, dict],
) -> None:
    """POST /v1/retrieve response conforms to retrieve_response schema."""
    resp = await client.post("/v1/retrieve", json={"query": "vector databases"})
    assert resp.status_code == 200
    _validate(resp.json(), schemas["retrieve_response"], schemas)


async def test_ingest_schema(
    client: httpx.AsyncClient,
    schemas: dict[str, dict],
) -> None:
    """POST /v1/ingest response conforms to ingest_response schema."""
    resp = await client.post("/v1/ingest", json={"source": "/tmp/test.txt"})
    assert resp.status_code == 200
    _validate(resp.json(), schemas["ingest_response"], schemas)


async def test_documents_schema(
    client: httpx.AsyncClient,
    schemas: dict[str, dict],
) -> None:
    """GET /v1/documents response is a list (no dedicated schema; assert type)."""
    resp = await client.get("/v1/documents")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)


async def test_entities_schema(
    client: httpx.AsyncClient,
    schemas: dict[str, dict],
) -> None:
    """GET /v1/graph/entities response is a list."""
    resp = await client.get("/v1/graph/entities")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)


async def test_neighbors_schema(
    client: httpx.AsyncClient,
    schemas: dict[str, dict],
) -> None:
    """GET /v1/graph/neighbors/Alice response is a list."""
    resp = await client.get("/v1/graph/neighbors/Alice")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
