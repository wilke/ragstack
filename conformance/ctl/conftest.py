"""Shared fixtures for the control-plane (``ragstack-ctl``) conformance suite.

Black-box HTTP against a running ``ragstack-ctl serve``. Nothing here imports
from ``python/`` or ``go/`` — the suite must be able to test a daemon it
cannot import, and reading ``contracts/ctl/`` (the contract) is not importing
the implementation.

**The skip/fail doctrine** is the tenant suite's (``conformance/conftest.py``,
#432/#405), re-used verbatim rather than restated:

* an **absent** credential → *skip*, loudly, naming the variable, tagged with
  :data:`CREDENTIAL_SKIP` so a run that provisioned its own principals
  (``conformance/run_ctl_local.sh`` / ``make test-conformance-ctl``) can fail on it;
* a credential that is **present but is not what it claims to be** → *fail*,
  naming the violated precondition and both sides of the comparison. The
  operator key is proven to be an operator and the viewer key to be a viewer
  through ``GET /v1/me`` before any test relies on the difference; two names
  for one principal is the #405 vacuity and is refused here the same way.

**Principals.**

==================================  ============================================
``RAGSTACK_CTL_URL``                the daemon under test. Required, no default.
``RAGSTACK_CTL_API_KEY``            an **operator** ctl key. :func:`client`
                                    sends it on every request.
``RAGSTACK_CTL_API_KEY_VIEWER``     a **viewer** ctl key, distinct from the
                                    operator key. Drives the authorization
                                    matrix (``test_authz_matrix.py``).
``RAGSTACK_CTL_UNLISTED_BEARER``    a BV-BRC token that VERIFIES (real issuer,
                                    unexpired) whose subject is NOT on the
                                    ctl's principal list. Proves 403-never-
                                    default-role as its own case, distinct
                                    from malformed (401).
==================================  ============================================
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
import yaml

# One definition of the tag, shared with the tenant suite: the root
# ``conformance/conftest.py`` is an ancestor conftest and is always loaded
# first, so importing it by its module name is deterministic here.
from conftest import CREDENTIAL_SKIP, skip_no_credential  # noqa: F401  (re-exported)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIR = REPO_ROOT / "contracts" / "ctl"


def _env(name: str) -> str | None:
    """One variable, treating empty as unset."""
    return os.environ.get(name) or None


# --------------------------------------------------------------------------- #
# Server under test
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def ctl_url() -> str:
    """Base URL of the control-plane daemon under test.

    **Required, deliberately no default.** The daemon's conventional bind is
    ``127.0.0.1:23990`` and on the deployment host that IS the production
    control plane — the one that holds every tenant's admin key. This suite
    is read-only in PR-A, but the same conftest will carry the mutation tests
    of PR-C, and "a default that resolves to production" is the class of bug
    the tenant suite already paid for (#363/#369/#392/#407/#432; see
    ``docs/plans/README.md``). The port convention lives in the run scripts,
    where it cannot fire by accident.
    """
    url = _env("RAGSTACK_CTL_URL")
    if not url:
        raise pytest.UsageError(
            "RAGSTACK_CTL_URL is required and has no default: on the deployment "
            "host the conventional bind (http://127.0.0.1:23990) is the LIVE "
            "control plane. Point it at a `ragstack-ctl serve --fake-drivers` "
            "you booted yourself (make test-conformance-ctl), or export "
            "it knowingly."
        )
    return url.rstrip("/")


def _sync_get(url: str, headers: dict[str, str]) -> tuple[int, object]:
    """One blocking GET, stdlib only — for session-scoped precondition probes,
    which cannot use an event-loop-bound async client (same reason the tenant
    suite's ``_sync_call`` exists)."""
    req = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw.decode("utf-8", "replace")


def _prove_role(ctl_url: str, key: str, variable: str, expected_role: str) -> None:
    status, body = _sync_get(f"{ctl_url}/v1/me", {"X-API-Key": key})
    assert status == 200, (
        f"PRECONDITION key_is_valid: GET /v1/me must answer 200 for the key in "
        f"{variable}; got {status}: {body!r}"
    )
    assert isinstance(body, dict) and body.get("role") == expected_role, (
        f"PRECONDITION role_is_{expected_role}: the key in {variable} must be a "
        f"{expected_role!r}; GET /v1/me says role={body.get('role') if isinstance(body, dict) else body!r}"
    )


@pytest.fixture(scope="session")
def ctl_key(ctl_url: str) -> str:
    """The operator ctl key — the suite's default principal. Skips (tagged)
    when absent; FAILS when present but not an operator."""
    key = _env("RAGSTACK_CTL_API_KEY")
    if not key:
        skip_no_credential(
            "the control plane has no anonymous surface beyond /health; every "
            "other assertion needs an operator key in RAGSTACK_CTL_API_KEY"
        )
    _prove_role(ctl_url, key, "RAGSTACK_CTL_API_KEY", "operator")
    return key


@pytest.fixture(scope="session")
def ctl_viewer_key(ctl_url: str, ctl_key: str) -> str:
    """A viewer ctl key, DISTINCT from the operator key. Skips (tagged) when
    absent; FAILS when it is the operator key under another name (#405) or
    when ``/v1/me`` says it is not a viewer."""
    key = _env("RAGSTACK_CTL_API_KEY_VIEWER")
    if not key:
        skip_no_credential(
            "the authorization matrix needs a viewer key in "
            "RAGSTACK_CTL_API_KEY_VIEWER, distinct from the operator key"
        )
    assert key != ctl_key, (
        "PRECONDITION distinct_principals: RAGSTACK_CTL_API_KEY_VIEWER is the "
        "same value as RAGSTACK_CTL_API_KEY — two names for one principal make "
        "every viewer-vs-operator assertion vacuous (#405)"
    )
    _prove_role(ctl_url, key, "RAGSTACK_CTL_API_KEY_VIEWER", "viewer")
    return key


@pytest.fixture(scope="session")
def unlisted_bearer() -> str:
    """A BV-BRC token that verifies but whose subject is not enrolled."""
    tok = _env("RAGSTACK_CTL_UNLISTED_BEARER")
    if not tok:
        skip_no_credential(
            "the valid-but-unlisted-subject case needs a verifying BV-BRC token "
            "for a subject the ctl does not list, in RAGSTACK_CTL_UNLISTED_BEARER"
        )
    return tok


# --------------------------------------------------------------------------- #
# Clients (function-scoped: see the tenant conftest for the event-loop reason)
# --------------------------------------------------------------------------- #
@pytest_asyncio.fixture
async def client(ctl_url: str, ctl_key: str) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Authenticated as the operator: ``X-API-Key: $RAGSTACK_CTL_API_KEY``."""
    async with httpx.AsyncClient(
        base_url=ctl_url, timeout=30.0, headers={"X-API-Key": ctl_key}
    ) as c:
        yield c


@pytest_asyncio.fixture
async def viewer_client(
    ctl_url: str, ctl_viewer_key: str
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Authenticated as the viewer."""
    async with httpx.AsyncClient(
        base_url=ctl_url, timeout=30.0, headers={"X-API-Key": ctl_viewer_key}
    ) as c:
        yield c


@pytest_asyncio.fixture
async def anon_client(ctl_url: str) -> AsyncGenerator[httpx.AsyncClient, None]:
    """No credential unless a test adds one. httpx MERGES request headers into
    the client's defaults, so :func:`client` cannot be made anonymous per
    request — every 401/400 assertion starts from this one."""
    async with httpx.AsyncClient(base_url=ctl_url, timeout=30.0) as c:
        yield c


# --------------------------------------------------------------------------- #
# The contract
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def schemas() -> dict[str, dict]:
    """All of ``contracts/ctl/schemas/*.json``, keyed by file stem."""
    result: dict[str, dict] = {}
    for schema_file in sorted((CONTRACT_DIR / "schemas").glob("*.json")):
        with open(schema_file, encoding="utf-8") as fh:
            result[schema_file.stem] = json.load(fh)
    assert result, f"no schemas under {CONTRACT_DIR / 'schemas'}"
    return result


@pytest.fixture(scope="session")
def openapi() -> dict:
    """``contracts/ctl/openapi.yaml``, parsed. The authorization matrix test
    is generated from its ``x-ctl-role`` / ``x-ctl-authorization-matrix``."""
    with open(CONTRACT_DIR / "openapi.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="session")
def some_tenant(ctl_url: str, ctl_key: str) -> str:
    """The name of one registry tenant (the first fleet row), for tests that
    need an EXISTING tenant. Skips — not a credential skip — when the fleet is
    empty, which is legitimate on a fresh ``--fake-drivers`` daemon."""
    status, body = _sync_get(f"{ctl_url}/v1/fleet", {"X-API-Key": ctl_key})
    assert status == 200, f"GET /v1/fleet answered {status}: {body!r}"
    rows = body.get("tenants") if isinstance(body, dict) else None
    if not rows:
        pytest.skip("the fleet has no tenants; nothing to show")
    return rows[0]["name"]
