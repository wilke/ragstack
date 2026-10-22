"""Conformance tests for GET /v1/health/deep (admin only, black-box over HTTP).

The endpoint returns backend detail, so it must be admin-gated: a non-admin key
gets 403 and no backend detail; an admin key gets a schema-valid body. Assertions
that need specific credentials skip when those keys aren't configured.

Both implementations serve the route since #751 PR-3 (decision 6: the Go
scaffold answers a minimal body with every check ``disabled``), so there is no
implementation skip here any more.
"""
from __future__ import annotations

import os
import re

import httpx
import jsonschema
import pytest

from conftest import rerank_stub_url, skip_no_credential

pytestmark = pytest.mark.asyncio

_BACKEND_LEAK_RE = re.compile(
    r"(qdrant|elasticsearch|neo4j|postgres|sqlite|localhost|:\d{4}|latency_ms|checks)", re.I
)

#: The check names the contract describes. The four stores were always there;
#: the five routed-around dependencies are #751 PR-3.
STORE_CHECKS = {"vector", "text", "graph", "jobstore"}
DEPENDENCY_CHECKS = {"reranker", "embedder", "llm", "ingest_engine", "doi_resolver"}
STATES = {"ok", "degraded", "down", "disabled"}


def _validate(data, schemas: dict[str, dict]) -> None:
    store = {s.get("$id", n): s for n, s in schemas.items()}
    resolver = jsonschema.RefResolver.from_schema({}, store=store)
    jsonschema.validate(instance=data, schema=schemas["deep_health_response"], resolver=resolver)


async def test_health_deep_non_admin_is_forbidden(client: httpx.AsyncClient) -> None:
    key = os.environ.get("RAGSTACK_API_KEY_NONADMIN") or None
    if not key:
        skip_no_credential("needs a non-admin key (RAGSTACK_API_KEY_NONADMIN)")
    resp = await client.get("/v1/health/deep", headers={"X-API-Key": key})
    assert resp.status_code == 403
    assert not _BACKEND_LEAK_RE.search(resp.text), resp.text


async def test_health_deep_admin_schema(client: httpx.AsyncClient, schemas: dict[str, dict]) -> None:
    key = os.environ.get("RAGSTACK_API_KEY_ADMIN") or None
    if not key:
        skip_no_credential("needs an admin key (RAGSTACK_API_KEY_ADMIN)")
    resp = await client.get("/v1/health/deep", headers={"X-API-Key": key})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _validate(body, schemas)
    assert body["status"] in {"ok", "degraded"}
    names = {c["name"] for c in body["checks"]}
    assert names  # non-empty
    assert STORE_CHECKS | DEPENDENCY_CHECKS <= names, names
    for c in body["checks"]:
        state = c.get("state")
        if state is not None:
            assert state in STATES, c
            # `ok` is the compatibility projection of `state`.
            assert c["ok"] == (state in {"ok", "disabled"}), c
    # The whole is degraded exactly when some check is neither ok nor disabled.
    bad = [c for c in body["checks"] if not c["ok"]]
    assert (body["status"] == "degraded") == bool(bad), (body["status"], bad)


async def test_health_deep_reranker_check_is_live_when_a_reranker_is_wired(
    client: httpx.AsyncClient,
) -> None:
    """Against the self-booted keyed server (RERANK_ENABLED=true over the stub
    sidecar) the ``reranker`` check is a real verdict, not ``disabled``."""
    if not rerank_stub_url():
        pytest.skip("needs the conformance sidecar stub (RAGSTACK_CONFORMANCE_RERANK_STUB_URL)")
    key = os.environ.get("RAGSTACK_API_KEY_ADMIN") or None
    if not key:
        skip_no_credential("needs an admin key (RAGSTACK_API_KEY_ADMIN)")
    resp = await client.get("/v1/health/deep", headers={"X-API-Key": key})
    assert resp.status_code == 200, resp.text
    rr = next(c for c in resp.json()["checks"] if c["name"] == "reranker")
    assert rr["state"] in {"ok", "degraded", "down"}, rr
