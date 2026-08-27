"""Conformance: authz (401/403) for the core /v1 data ops and the admin surface.

Closes the #88 gap — the core data ops (``/v1/query``, ``/v1/retrieve``,
``/v1/ingest``, ``/v1/documents``) had **zero** authz conformance assertions,
while only three read endpoints (stats/stores, graph/stats, health/deep) were
covered. The #88 continuation extends this to the remaining tenant-scoped ops:
the graph reads (``/v1/graph/entities``, ``/v1/graph/neighbors/{entity}``) and
the explicit ``DELETE /v1/documents/{id}``. Black-box over HTTP; no imports from
the implementations.

The contract these assert (see ``api/main.py`` router wiring):

* **Core + tenant-scoped ops** (the four core ops, the graph reads, and
  ``DELETE /v1/documents/{id}``) are secured by ``resolve_tenant`` — a valid API
  key is required (**401** when the server is key-protected) but **no role** is:
  any authenticated caller may use them, so a valid non-admin key must **not**
  get **403** (guards against accidental over-restriction / admin-gating a data op).
* **Admin ops** (``GET /v1/config``) are gated by ``require_role("admin")`` —
  **401** without a key, **403** for a valid non-admin key, **200** for admin.

Every assertion **skips** when the credential it needs is not configured, so a
keyless dev server (or the Go phase-1 scaffold, for the admin checks) does not
fail. Those skips are tagged (:func:`conftest.skip_no_credential`) so a run that
*provisioned* the credentials — ``run_authz_keyed.sh`` — fails instead of
skipping past its own misconfiguration. Env keys:

* ``RAGSTACK_API_KEY``          — any valid key (its presence == server is key-protected)
* ``RAGSTACK_API_KEY_NONADMIN`` — a valid key whose role is not ``admin``
* ``RAGSTACK_API_KEY_ADMIN``    — a valid key whose role is ``admin``

Every test here takes :func:`conftest.anon_client`, **not** the shared
``client``. Since #405 ``client`` carries ``X-API-Key: $RAGSTACK_API_KEY`` by
default, and httpx MERGES per-request headers with the client's — so a 401
assertion written against it would be quietly authenticated and would pass for
the wrong reason. An unauthenticated baseline is this file's entire premise.
"""
from __future__ import annotations

import os

import httpx
import pytest

from conftest import skip_no_credential

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
    anon_client: httpx.AsyncClient, method: str, path: str,
    *, key: str | None = None, body: dict | None = None,
) -> httpx.Response:
    headers = {"X-API-Key": key} if key else {}
    if method == "GET":
        return await anon_client.get(path, headers=headers)
    return await anon_client.request(method, path, headers=headers, json=(body or {}))


# --------------------------------------------------------------------------- #
# Core ops: 401 (auth required) — the missing #88 coverage
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("label,method,path,body", CORE_OPS, ids=[op[0] for op in CORE_OPS])
async def test_core_op_requires_key_when_configured(
    anon_client: httpx.AsyncClient, label: str, method: str, path: str, body: dict | None,
) -> None:
    """A key-protected server rejects an unauthenticated core-op call with 401."""
    if not _key("RAGSTACK_API_KEY"):
        skip_no_credential("server is keyless (no RAGSTACK_API_KEY); nothing to enforce")
    resp = await _call(anon_client, method, path, body=body)
    assert resp.status_code == 401, f"{label}: expected 401 unauthenticated, got {resp.status_code}: {resp.text}"


@pytest.mark.parametrize("label,method,path,body", CORE_OPS, ids=[op[0] for op in CORE_OPS])
async def test_core_op_rejects_invalid_key(
    anon_client: httpx.AsyncClient, label: str, method: str, path: str, body: dict | None,
) -> None:
    """A syntactically-present but unknown key is rejected with 401 (not 200/500)."""
    if not _key("RAGSTACK_API_KEY"):
        skip_no_credential(
            "server is keyless (no RAGSTACK_API_KEY); an unknown key maps to the "
            "default identity"
        )
    resp = await _call(anon_client, method, path, key="conformance-invalid-key-nomatch", body=body)
    assert resp.status_code == 401, f"{label}: expected 401 for invalid key, got {resp.status_code}: {resp.text}"


@pytest.mark.parametrize("label,method,path,body", CORE_OPS, ids=[op[0] for op in CORE_OPS])
async def test_core_op_not_admin_gated(
    anon_client: httpx.AsyncClient, label: str, method: str, path: str, body: dict | None,
) -> None:
    """A valid non-admin key must reach the core ops — never 401, never 403.

    Sends an intentionally-empty body so POST ops fail request validation (422)
    *after* auth succeeds, proving the op is neither unauthenticated-rejected nor
    admin-gated, without triggering a real ingest/query side effect.
    """
    key = _key("RAGSTACK_API_KEY_NONADMIN")
    if not key:
        skip_no_credential("needs a valid non-admin key (RAGSTACK_API_KEY_NONADMIN)")
    resp = await _call(anon_client, method, path, key=key, body={})
    assert resp.status_code not in (401, 403), (
        f"{label}: a valid non-admin key was rejected with {resp.status_code} "
        f"(core ops require auth but not a role): {resp.text}"
    )


# --------------------------------------------------------------------------- #
# Additional tenant-scoped ops (#88 continuation): the graph reads and the
# explicit DELETE /v1/documents/{id}. Same gate as the core ops — resolve_tenant:
# a valid key is required (401) but no role is (never 403). A synthetic probe
# id/entity is used so the not-admin-gated DELETE reaches the real handler yet is
# a guaranteed no-op (no document under any tenant has that id). The graph authz
# probes are python-only: the Go phase-1 scaffold has no auth middleware — its
# /v1/graph handlers are unauthenticated 200 stubs — so a 401 assertion can't
# hold there (the routes exist; they just don't authenticate). The documents
# surface exists on both impls.
#
# Not covered here: GET /v1/ingest/{job_id}. It is NOT unauthenticated — the
# documents router include carries the resolve_tenant gate, so a keyless-off
# server returns 401 without a key. #130 tenant-stamped jobs and scoped
# job_store.get(job_id) by tenant_id (admin bypasses, logged, per ADR-0003
# §5), closing the cross-tenant IDOR this comment used to describe. A real
# assertion here still needs a two-tenant fixture this single-tenant probe
# harness doesn't have; that conformance coverage is tracked under #100.
# --------------------------------------------------------------------------- #
_PROBE = "___conformance_authz_probe___"
EXTRA_TENANT_OPS = [
    ("graph_entities", "GET", "/v1/graph/entities", None),
    ("graph_neighbors", "GET", f"/v1/graph/neighbors/{_PROBE}", None),
    ("documents_delete", "DELETE", f"/v1/documents/{_PROBE}", None),
]


def _skip_go_graph(label: str, impl: str) -> None:
    if label.startswith("graph_") and impl != "python":
        pytest.skip("graph surface is python-only in phase 1")


@pytest.mark.parametrize("label,method,path,body", EXTRA_TENANT_OPS, ids=[op[0] for op in EXTRA_TENANT_OPS])
async def test_extra_tenant_op_requires_key_when_configured(
    anon_client: httpx.AsyncClient, impl: str, label: str, method: str, path: str, body: dict | None,
) -> None:
    """A key-protected server rejects an unauthenticated graph-read / delete with 401."""
    _skip_go_graph(label, impl)
    if not _key("RAGSTACK_API_KEY"):
        skip_no_credential("server is keyless (no RAGSTACK_API_KEY); nothing to enforce")
    resp = await _call(anon_client, method, path, body=body)
    assert resp.status_code == 401, f"{label}: expected 401 unauthenticated, got {resp.status_code}: {resp.text}"


@pytest.mark.parametrize("label,method,path,body", EXTRA_TENANT_OPS, ids=[op[0] for op in EXTRA_TENANT_OPS])
async def test_extra_tenant_op_rejects_invalid_key(
    anon_client: httpx.AsyncClient, impl: str, label: str, method: str, path: str, body: dict | None,
) -> None:
    """A syntactically-present but unknown key is rejected with 401 (not 200/500)."""
    _skip_go_graph(label, impl)
    if not _key("RAGSTACK_API_KEY"):
        skip_no_credential(
            "server is keyless (no RAGSTACK_API_KEY); an unknown key maps to the "
            "default identity"
        )
    resp = await _call(anon_client, method, path, key="conformance-invalid-key-nomatch", body=body)
    assert resp.status_code == 401, f"{label}: expected 401 for invalid key, got {resp.status_code}: {resp.text}"


@pytest.mark.parametrize("label,method,path,body", EXTRA_TENANT_OPS, ids=[op[0] for op in EXTRA_TENANT_OPS])
async def test_extra_tenant_op_not_admin_gated(
    anon_client: httpx.AsyncClient, impl: str, label: str, method: str, path: str, body: dict | None,
) -> None:
    """A valid non-admin key must reach these ops with a real success, never
    401/403 (nor a 500 masking a broken gate).

    Unlike the core-op counterpart — which sends an empty body and tolerates a
    422 validation failure, so it can only assert ``not in (401, 403)`` — these
    ops have unambiguous success codes: the graph reads return 200 and the DELETE
    (a scoped no-op on the synthetic id) returns 204. Asserting the exact set
    catches a gate that 500s or otherwise misbehaves, not just one that rejects.
    """
    _skip_go_graph(label, impl)
    key = _key("RAGSTACK_API_KEY_NONADMIN")
    if not key:
        skip_no_credential("needs a valid non-admin key (RAGSTACK_API_KEY_NONADMIN)")
    resp = await _call(anon_client, method, path, key=key, body=body)
    assert resp.status_code in (200, 204), (
        f"{label}: a valid non-admin key expected a 200/204 success, got "
        f"{resp.status_code} (these ops require auth but not a role): {resp.text}"
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


async def test_admin_config_requires_key_when_configured(anon_client: httpx.AsyncClient) -> None:
    if not _key("RAGSTACK_API_KEY"):
        skip_no_credential("server is keyless (no RAGSTACK_API_KEY); nothing to enforce")
    resp = await anon_client.get("/v1/config")
    assert resp.status_code == 401, resp.text


async def test_admin_config_forbidden_for_non_admin(anon_client: httpx.AsyncClient) -> None:
    key = _key("RAGSTACK_API_KEY_NONADMIN")
    if not key:
        skip_no_credential("needs a valid non-admin key (RAGSTACK_API_KEY_NONADMIN)")
    resp = await anon_client.get("/v1/config", headers={"X-API-Key": key})
    assert resp.status_code == 403, resp.text


async def test_admin_config_allows_admin(anon_client: httpx.AsyncClient) -> None:
    key = _key("RAGSTACK_API_KEY_ADMIN")
    if not key:
        skip_no_credential("needs an admin key (RAGSTACK_API_KEY_ADMIN)")
    resp = await anon_client.get("/v1/config", headers={"X-API-Key": key})
    assert resp.status_code == 200, resp.text


# --------------------------------------------------------------------------- #
# The #405 regression: the suite must have as many principals as it has names
# --------------------------------------------------------------------------- #
async def test_the_configured_principals_are_distinct() -> None:
    """No two ``RAGSTACK_API_KEY*`` variables may hold the same value.

    ``run_authz_keyed.sh`` used to set ``RAGSTACK_API_KEY`` and
    ``RAGSTACK_API_KEY_NONADMIN`` to the *same key*. Every 403-for-a-non-admin
    assertion above therefore ran against whatever role that single key had, and
    the suite's only real axis was one principal wearing two names. It stayed
    green throughout, because nothing ever compared the two values.

    Env-only, no HTTP: this is a property of the invocation, and it is exactly
    the property that was false.
    """
    names = [
        "RAGSTACK_API_KEY",
        "RAGSTACK_API_KEY_ADMIN",
        "RAGSTACK_API_KEY_NONADMIN",
        "RAGSTACK_API_KEY_P2",
        "RAGSTACK_API_KEY_B",
    ]
    configured = {n: _key(n) for n in names if _key(n)}
    if len(configured) < 2:
        skip_no_credential(
            "fewer than two principals are configured; there is nothing to "
            "distinguish. `make test-conformance-keyed` provisions four."
        )
    # RAGSTACK_API_KEY is deliberately an ALIAS of one of the roles — the
    # scripted run points it at the admin key, because several files probe
    # admin-gated surfaces through it. Every other pair must differ.
    roles = {n: v for n, v in configured.items() if n != "RAGSTACK_API_KEY"}
    collisions = [
        (a, b)
        for i, (a, va) in enumerate(sorted(roles.items()))
        for b, vb in sorted(roles.items())[i + 1 :]
        if va == vb
    ]
    assert not collisions, (
        f"these principals share a key value: {collisions}. Two names for one "
        "principal is the #405 defect: every assertion that believes it is "
        "contrasting them is contrasting a key with itself."
    )
