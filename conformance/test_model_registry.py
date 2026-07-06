"""Conformance for the model registry (admin, black-box over HTTP).

GET /v1/admin/models/registry is served by both impls (the Go scaffold returns an
empty, schema-valid snapshot). It's admin-gated, so a keyless / non-admin caller
gets 401/403 → skip; when reachable, the body must match
contracts/schemas/models_registry_response.json. The write + PATCH routes are
Python-only in phase 1 and covered by the Python API tests.
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


async def test_registry_schema(client: httpx.AsyncClient, schemas: dict[str, dict]) -> None:
    resp = await client.get("/v1/admin/models/registry", headers=_headers())
    if resp.status_code in (401, 403):
        pytest.skip("caller lacks admin access to the model registry")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    jsonschema.validate(instance=body, schema=schemas["models_registry_response"])
    assert set(body) >= {"models", "assignments"}
