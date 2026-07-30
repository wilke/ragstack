"""/v1/stats/tenants — the tenancy breakdown behind the Ops "Tenants" section.

/v1/stats/stores collapses the caller's readable tenants (own + public) into one
number per store; this endpoint splits that union into a tenant × collection grid
so an operator can see *which* tenant actually owns a corpus. The isolation
invariants must hold either way: rows only for tenants the caller may read,
columns only for collections its allowlist permits, and the cross-tenant policy
map admin-only.
"""
import pytest

from ragstack.api import security
from ragstack.api.main import app
from ragstack.api.routers import stats
from ragstack.api.security import ROLE_ADMIN, ROLE_RESEARCHER
from ragstack.models import Chunk

pytestmark = pytest.mark.asyncio


def _chunk(cid: str, tenant: str) -> Chunk:
    return Chunk(
        id=cid,
        doc_id=f"doc-{cid}",
        content="hello world",
        embedding=[0.1, 0.2, 0.3, 0.4],
        metadata={"tenant_id": tenant},
    )


async def _seed() -> None:
    # acme: 2, other: 1, public: 3 — the public skew is the thing the breakdown
    # exists to reveal (stores would just report acme+public = 5).
    chunks = [
        _chunk("a1", "acme"),
        _chunk("a2", "acme"),
        _chunk("b1", "other"),
        _chunk("p1", "public"),
        _chunk("p2", "public"),
        _chunk("p3", "public"),
    ]
    await app.state.vector_store.upsert(chunks)
    await app.state.text_index.index(chunks)


def _configure_keys(monkeypatch, role: str = ROLE_RESEARCHER) -> None:
    monkeypatch.setattr(security.settings, "api_keys", ["k-acme", "k-admin"])
    monkeypatch.setattr(
        security.settings, "api_key_tenants", {"k-acme": "acme", "k-admin": "acme"}
    )
    monkeypatch.setattr(security.settings, "api_key_roles", {"k-admin": ROLE_ADMIN})
    monkeypatch.setattr(security.settings, "default_role", role)


async def test_splits_own_and_public_per_collection(client, monkeypatch):
    _configure_keys(monkeypatch)
    await _seed()
    r = await client.get("/v1/stats/tenants", headers={"X-API-Key": "k-acme"})
    assert r.status_code == 200
    body = r.json()
    assert body["tenant"] == "acme"
    assert body["readable"] == ["acme", "public"]
    rows = {t["tenant"]: t for t in body["tenants"]}
    assert set(rows) == {"acme", "public"}  # never 'other'
    assert rows["acme"]["own"] is True and rows["public"]["own"] is False
    acme = rows["acme"]["collections"][0]
    public = rows["public"]["collections"][0]
    assert (acme["vector_count"], acme["text_count"]) == (2, 2)
    assert (public["vector_count"], public["text_count"]) == (3, 3)
    # The split sums back to what /v1/stats/stores reports for the same caller.
    assert acme["vector_count"] + public["vector_count"] == 5


async def test_other_tenants_corpus_never_listed(client, monkeypatch):
    _configure_keys(monkeypatch)
    await _seed()
    r = await client.get("/v1/stats/tenants", headers={"X-API-Key": "k-acme"})
    assert "other" not in r.text  # neither as a row nor via a count


async def test_policy_map_is_admin_only(client, monkeypatch):
    _configure_keys(monkeypatch)
    monkeypatch.setattr(stats.settings, "tenant_collections", {"acme": ["default"]})
    researcher = await client.get("/v1/stats/tenants", headers={"X-API-Key": "k-acme"})
    admin = await client.get("/v1/stats/tenants", headers={"X-API-Key": "k-admin"})
    # The map names OTHER tenants, so a researcher gets null while still learning
    # its own confinement via restricted_to.
    assert researcher.json()["policy"] is None
    assert researcher.json()["restricted_to"] == ["default"]
    assert admin.json()["policy"] == {"acme": ["default"]}


async def test_columns_limited_to_allowed_collections(client, monkeypatch):
    _configure_keys(monkeypatch)
    # Confined to a collection that isn't registered → no columns at all, rather
    # than falling open to the default collection.
    monkeypatch.setattr(stats.settings, "tenant_collections", {"acme": ["nope"]})
    r = await client.get("/v1/stats/tenants", headers={"X-API-Key": "k-acme"})
    body = r.json()
    assert body["restricted_to"] == ["nope"]
    assert all(t["collections"] == [] for t in body["tenants"])


async def test_unrestricted_tenant_reports_null_allowlist(client, monkeypatch):
    _configure_keys(monkeypatch)
    monkeypatch.setattr(stats.settings, "tenant_collections", {})
    r = await client.get("/v1/stats/tenants", headers={"X-API-Key": "k-acme"})
    body = r.json()
    assert body["restricted_to"] is None  # null = unrestricted, not "no access"
    assert body["auth_enabled"] is True
    assert [c["collection"] for c in body["tenants"][0]["collections"]] == ["default"]


async def test_keyless_path_reports_auth_disabled(client, monkeypatch):
    monkeypatch.setattr(security.settings, "api_keys", [])
    monkeypatch.setattr(security.settings, "default_role", ROLE_ADMIN)
    r = await client.get("/v1/stats/tenants")
    body = r.json()
    assert body["auth_enabled"] is False  # the open dev path — flag it in the UI
    assert body["tenant"] == "default" and body["role"] == ROLE_ADMIN


async def test_requires_authentication(client, monkeypatch):
    _configure_keys(monkeypatch)
    r = await client.get("/v1/stats/tenants")
    assert r.status_code == 401


async def test_count_failure_degrades_to_null(client, monkeypatch):
    _configure_keys(monkeypatch)

    async def boom(_tenants):
        raise RuntimeError("store down")

    monkeypatch.setattr(app.state.vector_store, "count_tenants", boom)
    r = await client.get("/v1/stats/tenants", headers={"X-API-Key": "k-acme"})
    assert r.status_code == 200  # a dead store must not 500 the ops panel
    cell = r.json()["tenants"][0]["collections"][0]
    assert cell["vector_count"] is None and cell["text_count"] is not None
