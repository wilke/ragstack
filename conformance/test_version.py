"""Conformance tests for GET /v1/version (any credential, black-box over HTTP).

The contract (``contracts/openapi.yaml`` → ``VersionResponse``): a credential is
required when the server is key-protected (**401** without one), but **no role**
is — a valid non-admin key gets the same schema-valid body an admin does. The
body says which implementation answered (``impl``); the fields are what the
control plane (ADR-0007) shows on its fleet view as "running".

Python-only in v1 (ADR-0006 decision 4): the Go scaffold has no route, so the
whole file skips on ``RAGSTACK_IMPL=go``.

The 401 assertion takes :func:`conftest.anon_client`, not the shared ``client``:
since #405 ``client`` carries ``X-API-Key`` by default and httpx MERGES request
headers with the client's, so an unauthenticated check written against it would
be quietly authenticated. Keyed-vs-keyless is decided the way ``test_authz.py``
decides it — the presence of ``RAGSTACK_API_KEY`` means the server enforces keys.
"""
from __future__ import annotations

import os
from datetime import datetime

import httpx
import jsonschema
import pytest

from conftest import skip_no_credential

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _python_only(impl: str) -> None:
    if impl != "python":
        pytest.skip("Python-only surface (ADR-0006 d4)")


def _key(name: str) -> str | None:
    return os.environ.get(name) or None


def _validate(data, schemas: dict[str, dict]) -> None:
    store = {s.get("$id", n): s for n, s in schemas.items()}
    resolver = jsonschema.RefResolver.from_schema({}, store=store)
    jsonschema.validate(instance=data, schema=schemas["version_response"], resolver=resolver)


async def test_version_schema(client: httpx.AsyncClient, schemas: dict[str, dict]) -> None:
    """The suite's default principal gets a schema-valid body naming this impl."""
    resp = await client.get("/v1/version")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _validate(body, schemas)
    assert body["impl"] == "python"
    # started_at is ISO-8601 and timezone-aware (the schema can only say "string").
    started = datetime.fromisoformat(body["started_at"])
    assert started.tzinfo is not None, body["started_at"]


async def test_version_requires_credential_when_configured(anon_client: httpx.AsyncClient) -> None:
    """A key-protected server rejects an unauthenticated call with 401 — the
    endpoint is "any credential", not "no credential"."""
    if not _key("RAGSTACK_API_KEY"):
        skip_no_credential("server is keyless (no RAGSTACK_API_KEY); nothing to enforce")
    resp = await anon_client.get("/v1/version")
    assert resp.status_code == 401, f"expected 401 unauthenticated, got {resp.status_code}: {resp.text}"


async def test_version_rejects_invalid_key(anon_client: httpx.AsyncClient) -> None:
    if not _key("RAGSTACK_API_KEY"):
        skip_no_credential("server is keyless (no RAGSTACK_API_KEY); an unknown key maps to the default identity")
    resp = await anon_client.get("/v1/version", headers={"X-API-Key": "conformance-invalid-key-nomatch"})
    assert resp.status_code == 401, f"expected 401 for invalid key, got {resp.status_code}: {resp.text}"


async def test_version_not_admin_gated(
    anon_client: httpx.AsyncClient, schemas: dict[str, dict]
) -> None:
    """A valid non-admin key gets the full body — never 403. Guards against the
    route drifting into the admin group in ``api/main.py``."""
    key = _key("RAGSTACK_API_KEY_NONADMIN")
    if not key:
        skip_no_credential("needs a valid non-admin key (RAGSTACK_API_KEY_NONADMIN)")
    resp = await anon_client.get("/v1/version", headers={"X-API-Key": key})
    assert resp.status_code == 200, (
        f"a valid non-admin key was rejected with {resp.status_code} "
        f"(/v1/version requires auth but not a role): {resp.text}"
    )
    _validate(resp.json(), schemas)
