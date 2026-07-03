"""Conformance: authz (401/403) for the core /v1 data ops and the admin surface.

Closes the #88 gap — the core data ops (``/v1/query``, ``/v1/retrieve``,
``/v1/ingest``, ``/v1/documents``) had **zero** authz conformance assertions,
while only three read endpoints (stats/stores, graph/stats, health/deep) were
covered. Black-box over HTTP; no imports from the implementations.

The contract these assert (see ``api/main.py`` router wiring):

* **Core ops** are secured by ``resolve_tenant`` — a valid API key is required
  (**401** when the server is key-protected) but **no role** is: any
  authenticated caller may use them, so a valid non-admin key must **not** get
  **403** (guards against accidental over-restriction / admin-gating a data op).
* **Admin ops** (``GET /v1/config``) are gated by ``require_role("admin")`` —
  **401** without a key, **403** for a valid non-admin key, **200** for admin.

Every assertion **skips** when the credential it needs is not configured, so a
keyless dev server (or the Go phase-1 scaffold, for the admin checks) does not
fail. Env keys, matching the existing authz tests:

* ``RAGSTACK_API_KEY``          — any valid key (its presence == server is key-protected)
* ``RAGSTACK_API_KEY_NONADMIN`` — a valid key whose role is not ``admin``
* ``RAGSTACK_API_KEY_ADMIN``    — a valid key whose role is ``admin``
"""
from __future__ import annotations

import os

import httpx
import pytest

pytestmark = pytest.mark.asyncio


def _key(name: str) -> str | None:
    return os.environ.get(name) or None


# (label, method, path, valid_body). A *valid* body is used for the 401 checks so
# the ONLY possible rejection reason is auth (the handler never runs — the security
# dependency rejects first — so even ``/v1/ingest`` has no side effect). ``None`` =
# no body (GET). The DELETE /v1/documents/{id} route shares the documents router's
# ``resolve_tenant`` gate, so GET is representative of the documents surface.
CORE_OPS = [
    ("query", "POST", "/v1/query", {"query": "conformance authz probe"}),
    ("retrieve", "POST", "/v1/retrieve", {"query": "conformance authz probe"}),
    ("ingest", "POST", "/v1/ingest", {"source": "___conformance_authz_probe___"}),
    ("documents", "GET", "/v1/documents", None),
]


async def _call(
    client: httpx.AsyncClient, method: str, path: str,
    *, key: str | None = None, body: dict | None = None,
) -> httpx.Response:
    headers = {"X-API-Key": key} if key else {}
    if method == "GET":
        return await client.get(path, headers=headers)
    return await client.request(method, path, headers=headers, json=(body or {}))


# --------------------------------------------------------------------------- #
# Core ops: 401 (auth required) — the missing #88 coverage
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("label,method,path,body", CORE_OPS, ids=[op[0] for op in CORE_OPS])
async def test_core_op_requires_key_when_configured(
    client: httpx.AsyncClient, label: str, method: str, path: str, body: dict | None,
) -> None:
    """A key-protected server rejects an unauthenticated core-op call with 401."""
    if not _key("RAGSTACK_API_KEY"):
        pytest.skip("server is keyless; nothing to enforce")
    resp = await _call(client, method, path, body=body)
    assert resp.status_code == 401, f"{label}: expected 401 unauthenticated, got {resp.status_code}: {resp.text}"


@pytest.mark.parametrize("label,method,path,body", CORE_OPS, ids=[op[0] for op in CORE_OPS])
async def test_core_op_rejects_invalid_key(
    client: httpx.AsyncClient, label: str, method: str, path: str, body: dict | None,
) -> None:
    """A syntactically-present but unknown key is rejected with 401 (not 200/500)."""
    if not _key("RAGSTACK_API_KEY"):
        pytest.skip("server is keyless; an unknown key maps to the default identity")
    resp = await _call(client, method, path, key="conformance-invalid-key-nomatch", body=body)
    assert resp.status_code == 401, f"{label}: expected 401 for invalid key, got {resp.status_code}: {resp.text}"


@pytest.mark.parametrize("label,method,path,body", CORE_OPS, ids=[op[0] for op in CORE_OPS])
async def test_core_op_not_admin_gated(
    client: httpx.AsyncClient, label: str, method: str, path: str, body: dict | None,
) -> None:
    """A valid non-admin key must reach the core ops — never 401, never 403.

    Sends an intentionally-empty body so POST ops fail request validation (422)
    *after* auth succeeds, proving the op is neither unauthenticated-rejected nor
    admin-gated, without triggering a real ingest/query side effect.
    """
    key = _key("RAGSTACK_API_KEY_NONADMIN")
    if not key:
        pytest.skip("needs a valid non-admin key (RAGSTACK_API_KEY_NONADMIN)")
    resp = await _call(client, method, path, key=key, body={})
    assert resp.status_code not in (401, 403), (
        f"{label}: a valid non-admin key was rejected with {resp.status_code} "
        f"(core ops require auth but not a role): {resp.text}"
    )


# --------------------------------------------------------------------------- #
# Admin surface: 401 (no key) / 403 (non-admin) / 200 (admin) on GET /v1/config
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _admin_python_only(impl: str, request: pytest.FixtureRequest) -> None:
    # The RBAC admin router is python-first (the Go scaffold registers no admin
    # route → 404). Gate only the admin tests, not the shared core-op tests.
    if request.function.__name__.startswith("test_admin_") and impl != "python":
        pytest.skip("admin surface is python-only in phase 1")


async def test_admin_config_requires_key_when_configured(client: httpx.AsyncClient) -> None:
    if not _key("RAGSTACK_API_KEY"):
        pytest.skip("server is keyless; nothing to enforce")
    resp = await client.get("/v1/config")
    assert resp.status_code == 401, resp.text


async def test_admin_config_forbidden_for_non_admin(client: httpx.AsyncClient) -> None:
    key = _key("RAGSTACK_API_KEY_NONADMIN")
    if not key:
        pytest.skip("needs a valid non-admin key (RAGSTACK_API_KEY_NONADMIN)")
    resp = await client.get("/v1/config", headers={"X-API-Key": key})
    assert resp.status_code == 403, resp.text


async def test_admin_config_allows_admin(client: httpx.AsyncClient) -> None:
    key = _key("RAGSTACK_API_KEY_ADMIN")
    if not key:
        pytest.skip("needs an admin key (RAGSTACK_API_KEY_ADMIN)")
    resp = await client.get("/v1/config", headers={"X-API-Key": key})
    assert resp.status_code == 200, resp.text
