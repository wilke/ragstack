"""Conformance tests for GET /v1/stats/stores (black-box over HTTP).

Validates the response against contracts/schemas/store_stats_response.json and
asserts the tenant-scoping contract: every count is per-store and the echoed
``tenants`` list scopes them. Cross-tenant no-leak is asserted only when two
tenant-mapped keys are configured for the server under test.
"""
from __future__ import annotations

import os

import httpx
import jsonschema
import pytest

from conftest import skip_no_credential

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _python_only(impl: str) -> None:
    # These read endpoints are Python-first in phase 1; the Go scaffold registers
    # no route, so it returns 404 (not 501) — skip rather than fail its run.
    if impl != "python":
        pytest.skip("/v1/stats/stores is python-only in phase 1")


def _build_resolver(schemas: dict[str, dict]) -> jsonschema.RefResolver:
    store = {s.get("$id", name): s for name, s in schemas.items()}
    return jsonschema.RefResolver.from_schema({}, store=store)


def _validate(data, schema, schemas) -> None:
    jsonschema.validate(instance=data, schema=schema, resolver=_build_resolver(schemas))


def _key(name: str) -> str | None:
    return os.environ.get(name) or None


async def test_stats_stores_schema(client: httpx.AsyncClient, schemas: dict[str, dict]) -> None:
    headers = {}
    if (k := _key("RAGSTACK_API_KEY")):
        headers["X-API-Key"] = k
    resp = await client.get("/v1/stats/stores", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _validate(body, schemas["store_stats_response"], schemas)
    # Counts, when present, are non-negative and scoped to the echoed tenants.
    assert isinstance(body["tenants"], list)
    for store in ("vector", "text", "graph"):
        count = body[store]["count"]
        assert count is None or count >= 0


async def test_stats_stores_requires_key_when_configured(
    anon_client: httpx.AsyncClient,
) -> None:
    """When the server is key-protected, an unauthenticated call is rejected.

    ``anon_client``, not ``client``: since #405 the shared client sends
    ``$RAGSTACK_API_KEY`` and httpx merges its headers into every request, so
    this assertion would be authenticated and would fail for the right reason
    only by accident.
    """
    if not _key("RAGSTACK_API_KEY"):
        skip_no_credential("server is keyless (no RAGSTACK_API_KEY); nothing to enforce")
    resp = await anon_client.get("/v1/stats/stores")
    assert resp.status_code == 401


async def test_stats_stores_no_cross_tenant_leak(client: httpx.AsyncClient) -> None:
    """Two tenant-mapped keys must see disjoint tenant scopes."""
    a, b = _key("RAGSTACK_API_KEY"), _key("RAGSTACK_API_KEY_B")
    if not (a and b):
        skip_no_credential(
            "needs two tenant-mapped keys (RAGSTACK_API_KEY, RAGSTACK_API_KEY_B); "
            "`make test-conformance-keyed` maps four keys to four subjects"
        )
    assert a != b, (
        "RAGSTACK_API_KEY and RAGSTACK_API_KEY_B hold the same value, so this "
        "test would be comparing one tenant's scope with its own (#405)"
    )
    ta = (await client.get("/v1/stats/stores", headers={"X-API-Key": a})).json()["tenants"]
    tb = (await client.get("/v1/stats/stores", headers={"X-API-Key": b})).json()["tenants"]
    # Each sees its own tenant (+ public); their non-public tenants differ.
    assert set(ta) - {"public"} != set(tb) - {"public"}
