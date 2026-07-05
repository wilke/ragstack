"""Conformance tests for the model-status endpoints (admin, black-box over HTTP).

GET /v1/stats/models and POST /v1/stats/models/benchmark are admin-gated ops
reads: Python-first in phase 1 (Go scaffold has no route → 404, skip). Validates
the response schemas when the caller has admin access; skips on 403 / keyless
non-admin. The benchmark POST hits the live fleet, so it's skipped unless it
returns 200 (a down embedder/LLM shouldn't fail the conformance run).
"""
from __future__ import annotations

import httpx
import jsonschema
import pytest

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _python_only(impl: str) -> None:
    if impl != "python":
        pytest.skip("/v1/stats/models is python-only in phase 1")


def _validate(data, schema_name: str, schemas: dict[str, dict]) -> None:
    store = {s.get("$id", n): s for n, s in schemas.items()}
    resolver = jsonschema.RefResolver.from_schema({}, store=store)
    jsonschema.validate(instance=data, schema=schemas[schema_name], resolver=resolver)


async def test_models_status_schema(client: httpx.AsyncClient, schemas: dict[str, dict]) -> None:
    resp = await client.get("/v1/stats/models")
    if resp.status_code in (401, 403):
        pytest.skip("caller lacks admin access to /v1/stats/models")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _validate(body, "models_status_response", schemas)
    roles = {m["role"] for m in body["models"]}
    assert roles, "at least one model role must be reported"


async def test_models_benchmark_schema(client: httpx.AsyncClient, schemas: dict[str, dict]) -> None:
    # Bounded, but it runs a real embed + completion — allow a longer timeout and
    # skip (don't fail) if the live fleet is unavailable.
    resp = await client.post(
        "/v1/stats/models/benchmark", json={"embed_batch": 4, "llm_tokens": 16}, timeout=120.0
    )
    if resp.status_code in (401, 403):
        pytest.skip("caller lacks admin access to the benchmark endpoint")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _validate(body, "benchmark_response", schemas)
    assert set(body.keys()) >= {"embedding", "llm"}
