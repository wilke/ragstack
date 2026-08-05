"""/v1/health/deep — admin-only deep dependency probe.

The endpoint's ``detail`` strings can carry backend hostnames/versions/errors, so
authorization is the guardrail: no key → 401, non-admin → 403 (with a body that
carries no backend detail), admin → 200 with per-dependency checks.
"""
import re

import pytest

from ragstack.api import security
from ragstack.api.main import app
from ragstack.api.security import ROLE_ADMIN, ROLE_RESEARCHER, ROLE_USER

pytestmark = pytest.mark.asyncio


class _MutationTrapVectorStore:
    """A vector store whose liveness probe is read-only: healthcheck() is fine,
    but ensure_collection() (which would provision infra) is a trap."""

    def __init__(self) -> None:
        self.healthchecked = False

    async def healthcheck(self) -> None:
        self.healthchecked = True

    async def ensure_collection(self) -> None:  # pragma: no cover - must not run
        raise AssertionError("health probe must not call ensure_collection (mutates infra)")

# Backend identifiers that must never appear in a non-admin (403) response body.
_BACKEND_LEAK_RE = re.compile(
    r"(qdrant|elasticsearch|neo4j|postgres|sqlite|localhost|:\d{4}|latency_ms|checks)", re.I
)


def _configure(monkeypatch, roles: dict[str, str]) -> None:
    monkeypatch.setattr(security.settings, "api_keys", list(roles))
    monkeypatch.setattr(security.settings, "api_key_roles", dict(roles))
    monkeypatch.setattr(security.settings, "default_role", ROLE_USER)


async def test_no_key_is_401(client, monkeypatch):
    _configure(monkeypatch, {"adm": ROLE_ADMIN})
    assert (await client.get("/v1/health/deep")).status_code == 401


# `user` is the only non-admin role (ADR-0003); the deprecated `researcher`
# alias must land on the same 403, not on a different surface.
@pytest.mark.parametrize("role", [ROLE_USER, ROLE_RESEARCHER])
async def test_non_admin_is_403_and_leaks_no_backend_detail(client, monkeypatch, role):
    _configure(monkeypatch, {"k": role})
    resp = await client.get("/v1/health/deep", headers={"X-API-Key": "k"})
    assert resp.status_code == 403
    # The refusal body must not carry any backend detail (hostnames/ports/checks).
    assert not _BACKEND_LEAK_RE.search(resp.text), resp.text


async def test_admin_gets_checks(client, monkeypatch):
    _configure(monkeypatch, {"adm": ROLE_ADMIN})
    resp = await client.get("/v1/health/deep", headers={"X-API-Key": "adm"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in {"ok", "degraded"}
    names = {c["name"] for c in body["checks"]}
    assert {"vector", "text", "graph", "jobstore"} <= names
    # In-memory doubles are all live → ok.
    assert body["status"] == "ok"
    assert all(c["ok"] for c in body["checks"])


async def test_probe_is_read_only_uses_healthcheck_not_ensure(client, monkeypatch):
    """The vector probe must call the read-only healthcheck(), never
    ensure_collection() (which would create the collection — provisioning infra
    from a health check)."""
    _configure(monkeypatch, {"adm": ROLE_ADMIN})
    trap = _MutationTrapVectorStore()
    monkeypatch.setattr(app.state, "vector_store", trap)

    resp = await client.get("/v1/health/deep", headers={"X-API-Key": "adm"})
    assert resp.status_code == 200
    assert trap.healthchecked is True  # read-only probe ran
    vector = next(c for c in resp.json()["checks"] if c["name"] == "vector")
    assert vector["ok"] is True  # ensure_collection was NOT called (would have raised)
