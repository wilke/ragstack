"""Conformance tests for GET /v1/jobs (admin, black-box over HTTP).

Admin-gated ops read: Python-first in phase 1 (the Go scaffold has no route → 404,
skip). Validates the response against contracts/schemas/jobs_response.json when the
caller has admin access; skips when the server denies (403) or is keyless-non-admin.
"""
from __future__ import annotations

import httpx
import jsonschema
import pytest

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _python_only(impl: str) -> None:
    if impl != "python":
        pytest.skip("/v1/jobs is python-only in phase 1")


def _validate(data, schemas: dict[str, dict]) -> None:
    store = {s.get("$id", n): s for n, s in schemas.items()}
    resolver = jsonschema.RefResolver.from_schema({}, store=store)
    jsonschema.validate(instance=data, schema=schemas["jobs_response"], resolver=resolver)


async def test_jobs_schema(client: httpx.AsyncClient, schemas: dict[str, dict]) -> None:
    resp = await client.get("/v1/jobs")
    if resp.status_code in (401, 403):
        pytest.skip("caller lacks admin access to /v1/jobs")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _validate(body, schemas)
    assert isinstance(body["jobs"], list)
    for j in body["jobs"]:
        for k in ("pending", "completed", "failed"):
            assert j["items"][k] >= 0


async def test_jobs_limit_bounds(client: httpx.AsyncClient) -> None:
    """`limit` is validated (1..100) — an out-of-range value is a 422."""
    resp = await client.get("/v1/jobs", params={"limit": 0})
    if resp.status_code in (401, 403):
        pytest.skip("caller lacks admin access to /v1/jobs")
    assert resp.status_code == 422, resp.text
