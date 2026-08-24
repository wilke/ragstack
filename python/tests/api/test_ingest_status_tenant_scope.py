"""GET /v1/ingest/{job_id} is tenant-scoped (#130) — an IDOR fix.

Before #130, any authenticated caller could poll any job_id and read its
status and chunk_ids, regardless of who submitted it. Now:

- a job stamped for tenant A reads as real status for A (and for admin, via
  the named/logged ADR-0003 §5 bypass), and as "unknown" (the same 200 shape
  used for a job_id that doesn't exist at all) for any other tenant B — so the
  endpoint never confirms a foreign job_id even exists;
- a legacy row written before jobs carried a tenant stamp (``tenant_id ==
  ""``) is fail-closed: "unknown" for an ordinary user, real status for admin
  (the #209 convention).

Multi-principal callers are faked with per-tenant API keys — the
test_tenant_isolation / test_collection_ownership convention.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from ragstack.api import security
from ragstack.api.main import app
from ragstack.api.security import ROLE_ADMIN, ROLE_USER
from ragstack.jobstore import UNKNOWN

pytestmark = pytest.mark.asyncio

KEYS = {"alice": "k-alice", "bob": "k-bob", "admin": "k-admin"}


@pytest.fixture(autouse=True)
def _principals(monkeypatch):
    """Two ordinary tenants plus an admin, auth ON so the scoping seam under
    test is actually enforced."""
    monkeypatch.setattr(security.settings, "api_keys", list(KEYS.values()))
    monkeypatch.setattr(
        security.settings,
        "api_key_tenants",
        {"k-alice": "alice", "k-bob": "bob", "k-admin": "admin"},
    )
    monkeypatch.setattr(security.settings, "api_key_roles", {"k-admin": ROLE_ADMIN})
    monkeypatch.setattr(security.settings, "default_role", ROLE_USER)


def _h(who: str) -> dict:
    return {"X-API-Key": KEYS[who]}


async def _ingest_and_wait(client, tmp_path, who: str, name: str = "doc.txt") -> str:
    f = tmp_path / name
    f.write_text(f"{who} document", encoding="utf-8")
    r = await client.post("/v1/ingest", json={"source": str(f)}, headers=_h(who))
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    for _ in range(100):
        s = (await client.get(f"/v1/ingest/{job_id}", headers=_h(who))).json()
        if s["status"] in ("completed", "failed"):
            assert s["status"] == "completed", s
            return job_id
        await asyncio.sleep(0.01)
    raise AssertionError("ingest did not complete")


async def test_owner_sees_real_status(client, tmp_path):
    job_id = await _ingest_and_wait(client, tmp_path, "alice")
    r = await client.get(f"/v1/ingest/{job_id}", headers=_h("alice"))
    assert r.status_code == 200
    assert r.json()["status"] == "completed"


async def test_other_tenant_sees_unknown(client, tmp_path):
    """The cross-tenant IDOR this issue closes: Bob polling Alice's job_id must
    get the SAME 200/"unknown" shape as a nonexistent job_id — no existence
    leak, no chunk_ids."""
    job_id = await _ingest_and_wait(client, tmp_path, "alice")

    r = await client.get(f"/v1/ingest/{job_id}", headers=_h("bob"))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == UNKNOWN
    assert body["job_id"] == job_id
    assert body.get("chunk_ids", []) == []

    missing = await client.get("/v1/ingest/does-not-exist", headers=_h("bob"))
    assert missing.json()["status"] == UNKNOWN == body["status"]


async def test_admin_sees_real_status_and_bypass_is_logged(client, tmp_path, caplog):
    job_id = await _ingest_and_wait(client, tmp_path, "alice")

    with caplog.at_level(logging.INFO, logger="ragstack.jobstore"):
        r = await client.get(f"/v1/ingest/{job_id}", headers=_h("admin"))
    assert r.status_code == 200
    assert r.json()["status"] == "completed"

    bypass = [rec for rec in caplog.records if "admin-bypass" in rec.getMessage()]
    assert bypass, "admin bypass must be logged"
    assert any(job_id in rec.getMessage() for rec in bypass)


async def test_legacy_unstamped_job_is_unknown_for_user_real_for_admin(client):
    """A job created before #130 (tenant_id == "") — simulated here by calling
    the store directly with no tenant_id, exactly today's pre-migration
    default — fails closed for an ordinary user and opens only for admin."""
    job = await app.state.job_store.create(source="/legacy/doc.pdf")
    assert job.tenant_id == ""

    as_user = await client.get(f"/v1/ingest/{job.job_id}", headers=_h("alice"))
    assert as_user.json()["status"] == UNKNOWN

    as_admin = await client.get(f"/v1/ingest/{job.job_id}", headers=_h("admin"))
    assert as_admin.json()["status"] == job.status
    assert as_admin.json()["job_id"] == job.job_id
