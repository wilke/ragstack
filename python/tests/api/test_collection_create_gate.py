"""ALLOW_USER_COLLECTION_CREATE: a capability gate on POST /v1/collections (#287).

ADR-0003 opened collection creation to any authenticated principal. That is a
gap for a deployment that must issue a credential which can read but never
create — e.g. the read-only service account minted for an integration
partner, where every OTHER write already 403s a non-owner but creation is
object-less (there is nothing yet to check an ACL against), so it was the one
write that always returned 201 regardless of intent.

The switch is a blunt, env-wide capability gate (not a role, not an ACL): off
means every non-admin gets 403 on create, full stop; admins are never subject
to it. See issue #287 for why a `reader` role was rejected instead.
"""
from __future__ import annotations

import pytest

from ragstack.acl_store import GRANTEE_GROUP, PERM_READ, PUBLIC_GROUP, get_acl_store
from ragstack.api import security
from ragstack.api.collections import CollectionEntry, CollectionRegistry
from ragstack.api.main import app
from ragstack.api.security import ROLE_ADMIN, ROLE_USER
from ragstack.config import settings
from tests.api.conftest import SHARED_ID

pytestmark = pytest.mark.asyncio

KEYS = {"owner": "k-owner", "user": "k-user", "admin": "k-admin", "svc": "k-svc"}


@pytest.fixture(autouse=True)
def _principals(monkeypatch):
    """Four keyed callers: owner (a plain user who owns `shared`), user (a
    plain non-owning caller), admin, and svc (stands in for the read-only
    service account — a `user`-role principal by construction, since the
    posture comes from ACLs + this switch, not from a role)."""
    monkeypatch.setattr(security.settings, "api_keys", list(KEYS.values()))
    monkeypatch.setattr(
        security.settings,
        "api_key_tenants",
        {"k-owner": "owner", "k-user": "user", "k-admin": "admin", "k-svc": "svc"},
    )
    monkeypatch.setattr(security.settings, "api_key_roles", {"k-admin": ROLE_ADMIN})
    monkeypatch.setattr(security.settings, "default_role", ROLE_USER)


@pytest.fixture(autouse=True)
def _restore_switch():
    """The switch is read straight off the global `settings` singleton by the
    router, so pin it back to the documented default after every test even
    though each test that flips it also uses monkeypatch (belt and suspenders
    against ordering across the module)."""
    original = settings.allow_user_collection_create
    yield
    settings.allow_user_collection_create = original


def _h(who: str) -> dict:
    return {"X-API-Key": KEYS[who]}


def _entry(cid: str, default: bool = False, owner: str = "") -> CollectionEntry:
    from tests.api.conftest import _StateRetriever

    return CollectionEntry(
        id=cid, label=cid, collection=cid, model="test-model", dim=4,
        chunk_method="fixed", chunk_size=None, chunk_overlap=None, chunk_params={},
        is_shared_surface=default, retriever=_StateRetriever(),
        vector_store=app.state.vector_store, text_index=app.state.text_index,
        owner=owner,
    )


def _register(*entries: CollectionEntry) -> None:
    app.state.kg_extractor = None
    app.state.doi_enricher = None
    app.state.collections = CollectionRegistry(
        [_entry(SHARED_ID, True), *entries], default_id=SHARED_ID
    )


async def _own(cid: str, subject: str) -> None:
    await get_acl_store().grant(cid, "user", subject, "owner", granted_by=subject)


# --------------------------------------------------------------------------- #
# switch off: non-admin 403, admin unaffected
# --------------------------------------------------------------------------- #


async def test_switch_off_user_create_is_403(client, monkeypatch):
    monkeypatch.setattr(settings, "allow_user_collection_create", False)
    r = await client.post("/v1/collections", json={"id": "mine"}, headers=_h("user"))
    assert r.status_code == 403, r.text
    detail = r.json()["detail"]
    assert "ALLOW_USER_COLLECTION_CREATE" in detail


async def test_switch_off_admin_create_is_201(client, monkeypatch):
    monkeypatch.setattr(settings, "allow_user_collection_create", False)
    r = await client.post("/v1/collections", json={"id": "mine"}, headers=_h("admin"))
    assert r.status_code == 201, r.text


# --------------------------------------------------------------------------- #
# switch on (default): unchanged, ADR-0003 behaviour
# --------------------------------------------------------------------------- #


async def test_switch_on_user_create_is_201(client):
    assert settings.allow_user_collection_create is True  # the documented default
    r = await client.post("/v1/collections", json={"id": "mine"}, headers=_h("user"))
    assert r.status_code == 201, r.text


async def test_switch_off_does_not_touch_the_build_spec_gate_for_admins(client, monkeypatch):
    """Admins bypass the create switch AND may still supply build-spec fields —
    the two gates are independent."""
    monkeypatch.setattr(settings, "allow_user_collection_create", False)
    r = await client.post(
        "/v1/collections",
        json={"id": "mine", "chunk": {"method": "fixed_token", "size": 256, "overlap": 32}},
        headers=_h("admin"),
    )
    assert r.status_code == 201, r.text


# --------------------------------------------------------------------------- #
# GET /v1/config reports the switch
# --------------------------------------------------------------------------- #


async def test_config_reports_the_switch(client, monkeypatch):
    monkeypatch.setattr(settings, "allow_user_collection_create", False)
    body = (await client.get("/v1/config", headers=_h("admin"))).json()
    assert body["allow_user_collection_create"] is False


async def test_config_reports_the_switch_when_true(client):
    body = (await client.get("/v1/config", headers=_h("admin"))).json()
    assert body["allow_user_collection_create"] is True


# --------------------------------------------------------------------------- #
# the read-only service-account scenario from the issue: every write 403,
# including create, once the switch is off
# --------------------------------------------------------------------------- #


async def test_read_only_service_account_every_write_is_403(client, monkeypatch):
    """Reproduces the gap the issue opens with: a service account handed out
    for read-only integration use got 403 on every write EXCEPT create, which
    returned 201. With the switch off, create joins the rest."""
    monkeypatch.setattr(settings, "allow_user_collection_create", False)

    _register(_entry("shared"))
    await _own("shared", "owner")
    await get_acl_store().grant(
        "shared", GRANTEE_GROUP, PUBLIC_GROUP, PERM_READ, granted_by="owner"
    )

    # Reads still work: the svc account can see and query the shared collection.
    r = await client.post(
        "/v1/retrieve", json={"query": "x", "collection": "shared"}, headers=_h("svc")
    )
    assert r.status_code == 200, r.text

    # Every write on a collection it can read but does not own is 403...
    ingest = await client.post(
        "/v1/ingest", json={"source": "x.txt", "collection": "shared"}, headers=_h("svc")
    )
    assert ingest.status_code == 403, ingest.text

    delete = await client.delete("/v1/collections/shared", headers=_h("svc"))
    assert delete.status_code == 403, delete.text

    share = await client.post(
        "/v1/collections/shared/shares",
        json={"grantee": "@public", "permission": "read"},
        headers=_h("svc"),
    )
    assert share.status_code == 403, share.text

    # ...and now creation is too — the gap the issue names is closed.
    create = await client.post("/v1/collections", json={"id": "new-lib"}, headers=_h("svc"))
    assert create.status_code == 403, create.text
    assert "ALLOW_USER_COLLECTION_CREATE" in create.json()["detail"]
