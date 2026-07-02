"""Conformance tests for GET /v1/graph/stats (black-box over HTTP)."""
from __future__ import annotations

import os

import httpx
import jsonschema
import pytest

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _python_only(impl: str) -> None:
    # Python-first in phase 1; the Go scaffold has no route (404, not 501) — skip.
    if impl != "python":
        pytest.skip("/v1/graph/stats is python-only in phase 1")


def _validate(data, schemas: dict[str, dict], name: str) -> None:
    store = {s.get("$id", n): s for n, s in schemas.items()}
    resolver = jsonschema.RefResolver.from_schema({}, store=store)
    jsonschema.validate(instance=data, schema=schemas[name], resolver=resolver)


async def test_graph_stats_schema(client: httpx.AsyncClient, schemas: dict[str, dict]) -> None:
    headers = {}
    if k := (os.environ.get("RAGSTACK_API_KEY") or None):
        headers["X-API-Key"] = k
    resp = await client.get("/v1/graph/stats", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _validate(body, schemas, "graph_stats_response")
    # available=false → null counts; available=true → non-negative integers.
    if body["available"]:
        assert body["entities"] >= 0 and body["relationships"] >= 0
    else:
        assert body["entities"] is None and body["relationships"] is None


async def test_graph_stats_requires_key_when_configured(client: httpx.AsyncClient) -> None:
    if not (os.environ.get("RAGSTACK_API_KEY") or None):
        pytest.skip("server is keyless; nothing to enforce")
    assert (await client.get("/v1/graph/stats")).status_code == 401
