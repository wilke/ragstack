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
from ragstack.api.security import ROLE_ADMIN, ROLE_USER
from ragstack.models import Chunk
from tests.api.conftest import SHARED_ID

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


def _configure_keys(monkeypatch, role: str = ROLE_USER) -> None:
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
    monkeypatch.setattr(stats.settings, "tenant_collections", {"acme": [SHARED_ID]})
    user = await client.get("/v1/stats/tenants", headers={"X-API-Key": "k-acme"})
    admin = await client.get("/v1/stats/tenants", headers={"X-API-Key": "k-admin"})
    # The map names OTHER tenants, so a plain user gets null while still learning
    # its own confinement via restricted_to.
    assert user.json()["policy"] is None
    assert user.json()["restricted_to"] == [SHARED_ID]
    assert admin.json()["policy"] == {"acme": [SHARED_ID]}


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
    assert [c["collection"] for c in body["tenants"][0]["collections"]] == [SHARED_ID]


async def test_keyless_path_reports_auth_disabled(client, monkeypatch):
    monkeypatch.setattr(security.settings, "api_keys", [])
    monkeypatch.setattr(security.settings, "default_role", ROLE_ADMIN)
    r = await client.get("/v1/stats/tenants")
    body = r.json()
    assert body["auth_enabled"] is False  # the open dev path — flag it in the UI
    assert body["tenant"] == "default" and body["role"] == ROLE_ADMIN


@pytest.mark.parametrize("query", ["", "?counts=false"])
async def test_requires_authentication(client, monkeypatch, query):
    # The cheap path skips the count sweep, never the credential — and it is the
    # call a UI makes on every credential change, so it is the likeliest one to
    # arrive without one.
    _configure_keys(monkeypatch)
    r = await client.get(f"/v1/stats/tenants{query}")
    assert r.status_code == 401


async def test_counts_false_answers_identity_with_null_cells(client, monkeypatch):
    # The whoami shape: who I am and what I may reach, no numbers. The columns
    # stay, so the answer is the same object the counted call returns — uncounted.
    _configure_keys(monkeypatch)
    await _seed()
    r = await client.get("/v1/stats/tenants?counts=false", headers={"X-API-Key": "k-acme"})
    assert r.status_code == 200
    body = r.json()
    assert (body["tenant"], body["role"], body["auth_enabled"]) == ("acme", ROLE_USER, True)
    assert body["readable"] == ["acme", "public"]
    cells = [c for row in body["tenants"] for c in row["collections"]]
    assert [c["collection"] for c in cells] == [SHARED_ID, SHARED_ID]  # acme + public
    assert all(c["vector_count"] is None and c["text_count"] is None for c in cells)


async def test_counts_false_probes_no_store_and_looks_up_no_owner(client, monkeypatch):
    # The flag asserted where it is SPENT, not at the nulls: a null cell could
    # equally be a failed probe. Touching either seam at all is the failure.
    # shared_scope_many is in here because its batched owner lookup (#314)
    # exists only to decide count scope — it is part of what counts=false must
    # not pay for.
    _configure_keys(monkeypatch)
    await _seed()

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("counts=false must not touch a store or the ACL owner")

    monkeypatch.setattr(stats, "probe_tenant_count", forbidden)
    monkeypatch.setattr(stats, "shared_scope_many", forbidden)
    r = await client.get("/v1/stats/tenants?counts=false", headers={"X-API-Key": "k-acme"})
    assert r.status_code == 200
    assert r.json()["tenant"] == "acme"


async def test_counts_default_still_probes_every_cell(client, monkeypatch):
    # The default stays the counted endpoint the Ops dashboard reads: the flag is
    # opt-in, so omitting it probes (tenant x collection x store) exactly as before.
    _configure_keys(monkeypatch)
    await _seed()
    probed: list[list[str]] = []
    real = stats.probe_tenant_count

    async def counting(store, tenants):
        probed.append(tenants)
        return await real(store, tenants)

    monkeypatch.setattr(stats, "probe_tenant_count", counting)
    r = await client.get("/v1/stats/tenants", headers={"X-API-Key": "k-acme"})
    assert r.status_code == 200
    assert probed == [["acme"], ["acme"], ["public"], ["public"]]  # 2 tenants x 2 stores
    assert r.json()["tenants"][0]["collections"][0]["vector_count"] == 2


async def test_count_failure_degrades_to_null(client, monkeypatch):
    _configure_keys(monkeypatch)

    async def boom(_tenants):
        raise RuntimeError("store down")

    monkeypatch.setattr(app.state.vector_store, "count_tenants", boom)
    r = await client.get("/v1/stats/tenants", headers={"X-API-Key": "k-acme"})
    assert r.status_code == 200  # a dead store must not 500 the ops panel
    cell = r.json()["tenants"][0]["collections"][0]
    assert cell["vector_count"] is None and cell["text_count"] is not None
