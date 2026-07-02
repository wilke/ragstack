"""/v1/health/deep — admin-only deep dependency probe.

The endpoint's ``detail`` strings can carry backend hostnames/versions/errors, so
authorization is the guardrail: no key → 401, non-admin → 403 (with a body that
carries no backend detail), admin → 200 with per-dependency checks.
"""
import re

import pytest

from ragstack.api import security
from ragstack.api.security import ROLE_ADMIN, ROLE_ENGINEER, ROLE_MANAGER, ROLE_RESEARCHER

pytestmark = pytest.mark.asyncio

# Backend identifiers that must never appear in a non-admin (403) response body.
_BACKEND_LEAK_RE = re.compile(
    r"(qdrant|elasticsearch|neo4j|postgres|sqlite|localhost|:\d{4}|latency_ms|checks)", re.I
)


def _configure(monkeypatch, roles: dict[str, str]) -> None:
    monkeypatch.setattr(security.settings, "api_keys", list(roles))
    monkeypatch.setattr(security.settings, "api_key_roles", dict(roles))
    monkeypatch.setattr(security.settings, "default_role", ROLE_RESEARCHER)


async def test_no_key_is_401(client, monkeypatch):
    _configure(monkeypatch, {"adm": ROLE_ADMIN})
    assert (await client.get("/v1/health/deep")).status_code == 401


@pytest.mark.parametrize("role", [ROLE_RESEARCHER, ROLE_MANAGER, ROLE_ENGINEER])
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
