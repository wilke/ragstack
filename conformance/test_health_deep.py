"""Conformance tests for GET /v1/health/deep (admin only, black-box over HTTP).

The endpoint returns backend detail, so it must be admin-gated: a non-admin key
gets 403 and no backend detail; an admin key gets a schema-valid body. Assertions
that need specific credentials skip when those keys aren't configured.
"""
from __future__ import annotations

import os
import re

import httpx
import jsonschema
import pytest

pytestmark = pytest.mark.asyncio

_BACKEND_LEAK_RE = re.compile(
    r"(qdrant|elasticsearch|neo4j|postgres|sqlite|localhost|:\d{4}|latency_ms|checks)", re.I
)


@pytest.fixture(autouse=True)
def _python_only(impl: str) -> None:
    # Python-first in phase 1; the Go scaffold has no route (404, not 501) — skip.
    if impl != "python":
        pytest.skip("/v1/health/deep is python-only in phase 1")


def _validate(data, schemas: dict[str, dict]) -> None:
    store = {s.get("$id", n): s for n, s in schemas.items()}
    resolver = jsonschema.RefResolver.from_schema({}, store=store)
    jsonschema.validate(instance=data, schema=schemas["deep_health_response"], resolver=resolver)


async def test_health_deep_non_admin_is_forbidden(client: httpx.AsyncClient) -> None:
    key = os.environ.get("RAGSTACK_API_KEY_NONADMIN") or None
    if not key:
        pytest.skip("needs a non-admin key (RAGSTACK_API_KEY_NONADMIN)")
    resp = await client.get("/v1/health/deep", headers={"X-API-Key": key})
    assert resp.status_code == 403
    assert not _BACKEND_LEAK_RE.search(resp.text), resp.text


async def test_health_deep_admin_schema(client: httpx.AsyncClient, schemas: dict[str, dict]) -> None:
    key = os.environ.get("RAGSTACK_API_KEY_ADMIN") or None
    if not key:
        pytest.skip("needs an admin key (RAGSTACK_API_KEY_ADMIN)")
    resp = await client.get("/v1/health/deep", headers={"X-API-Key": key})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _validate(body, schemas)
    assert body["status"] in {"ok", "degraded"}
    assert {c["name"] for c in body["checks"]}  # non-empty
