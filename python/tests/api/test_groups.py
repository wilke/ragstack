"""Group API + group-grant end-to-end (issue #245 part 2).

Exercises the /v1/groups router and the group-grantee share form through the
running app (ASGITransport, in-process), reusing the share-test fixture style:
per-tenant API keys (owner / bob / outsider / admin), the in-memory ACL/group
singleton driven directly to arrange state, and the registry rebuilder.

The store here is an ``InMemoryGroupStore`` installed as BOTH the acl and the
group singletons — the same object authz reads (``grants_for_subject`` unions
real-group shares) and the router manages, exactly as the lifespan wires it in a
running server. That is what makes a group grant reach a member end-to-end.
"""
from __future__ import annotations

import uuid

import pytest

from ragstack.acl_store import (
    GRANTEE_GROUP,
    GRANTEE_USER,
    PERM_OWNER,
    PERM_READ,
    PUBLIC_GROUP,
    get_acl_store,
)
from ragstack.api import security
from ragstack.api.collections import CollectionEntry, CollectionRegistry
from ragstack.api.main import app
from ragstack.api.security import ROLE_ADMIN, ROLE_USER

pytestmark = pytest.mark.asyncio

KEYS = {
    "owner": "k-owner",
    "bob": "k-bob",
    "outsider": "k-outsider",
    "admin": "k-admin",
}
#: Bob's principal tenant is a bearer-shaped subject so that adding him by the
#: bare username ``bob`` (→ ``bvbrc:bob``) matches his principal at read time —
#: a true end-to-end, not a hand-arranged membership row.
BOB_SUBJECT = "bvbrc:bob"


@pytest.fixture(autouse=True)
def _principals(monkeypatch):
    monkeypatch.setattr(security.settings, "api_keys", list(KEYS.values()))
    monkeypatch.setattr(
        security.settings,
        "api_key_tenants",
        {
            "k-owner": "owner",
            "k-bob": BOB_SUBJECT,
            "k-outsider": "outsider",
            "k-admin": "admin",
        },
    )
    monkeypatch.setattr(security.settings, "api_key_roles", {"k-admin": ROLE_ADMIN})
    monkeypatch.setattr(security.settings, "default_role", ROLE_USER)


@pytest.fixture(autouse=True)
def _group_store():
    """Replace the conftest's plain ACL store with an ``InMemoryGroupStore``
    installed as both the acl and group singletons, seeded like the startup
    backfill for the pre-existing ``default`` collection. Runs after the
    conftest's autouse ``_acl_store`` fixture, so it wins."""
    from ragstack.acl_store import ShareRecord, reset_acl_store, set_acl_store
    from ragstack.group_store import (
        InMemoryGroupStore,
        reset_group_store,
        set_group_store,
    )

    store = InMemoryGroupStore()

    def _seed(grantee_type: str, grantee_id: str, permission: str) -> None:
        rec = ShareRecord(
            id=uuid.uuid4().hex,
            collection_id="default",
            grantee_type=grantee_type,
            grantee_id=grantee_id,
            permission=permission,
            granted_by="system:backfill",
            granted_at="2020-01-01T00:00:00+00:00",
        )
        store._shares[rec.id] = rec

    _seed(GRANTEE_USER, "legacy:admin", PERM_OWNER)
    _seed(GRANTEE_GROUP, PUBLIC_GROUP, PERM_READ)
    set_acl_store(store)
    set_group_store(store)
    yield store
    reset_group_store()
    reset_acl_store()


def _h(who: str) -> dict:
    return {"X-API-Key": KEYS[who]}


def _entry(
    cid: str, default: bool = False, owner: str = "", collection: str | None = None
) -> CollectionEntry:
    from tests.api.conftest import _StateRetriever

    return CollectionEntry(
        id=cid, label=cid, collection=collection or cid, model="test-model", dim=4,
        chunk_method="fixed", chunk_size=None, chunk_overlap=None, chunk_params={},
        is_default=default, retriever=_StateRetriever(),
        vector_store=app.state.vector_store, text_index=app.state.text_index,
        owner=owner,
    )


def _register(*entries: CollectionEntry) -> None:
    app.state.kg_extractor = None
    app.state.doi_enricher = None
    app.state.collections = CollectionRegistry(
        [_entry("default", True), *entries], default_id="default"
    )


async def _own(cid: str, subject: str) -> None:
    await get_acl_store().grant(cid, GRANTEE_USER, subject, PERM_OWNER, granted_by=subject)


async def _make_group(client, name: str = "readers", who: str = "owner") -> str:
    r = await client.post("/v1/groups", json={"name": name}, headers=_h(who))
    assert r.status_code == 201, r.text
    return r.json()["id"]


# --------------------------------------------------------------------------- #
# group lifecycle via the API
# --------------------------------------------------------------------------- #


async def test_create_list_and_get_group_with_members(client):
    r = await client.post("/v1/groups", json={"name": "readers"}, headers=_h("owner"))
    assert r.status_code == 201, r.text
    g = r.json()
    gid = g["id"]
    assert g["name"] == "readers" and g["owner_subject"] == "owner"
    assert g["built_in"] is False and g["active"] is True

    # owner adds Bob by bare username -> resolved + echoed as bvbrc:bob
    add = await client.post(
        f"/v1/groups/{gid}/members", json={"subject": "bob"}, headers=_h("owner")
    )
    assert add.status_code == 201, add.text
    assert add.json()["subject"] == BOB_SUBJECT
    assert add.json()["active"] is True
    # a never-logged-in member was pre-provisioned
    provisioned = await get_acl_store().get(BOB_SUBJECT)
    assert provisioned is not None and provisioned.provisional is True

    # the owner lists it; the detail view shows Bob
    lst = await client.get("/v1/groups", headers=_h("owner"))
    assert lst.status_code == 200
    assert gid in {x["id"] for x in lst.json()["groups"]}

    detail = await client.get(f"/v1/groups/{gid}", headers=_h("owner"))
    assert detail.status_code == 200, detail.text
    assert [m["subject"] for m in detail.json()["members"]] == [BOB_SUBJECT]

    # Bob (a member) may view; sees himself in the list of groups he belongs to
    bob_list = await client.get("/v1/groups", headers=_h("bob"))
    assert gid in {x["id"] for x in bob_list.json()["groups"]}
    bob_detail = await client.get(f"/v1/groups/{gid}", headers=_h("bob"))
    assert bob_detail.status_code == 200


async def test_empty_name_is_422_duplicate_is_409(client):
    assert (await client.post("/v1/groups", json={"name": "   "}, headers=_h("owner"))).status_code == 422
    await _make_group(client, "team")
    dup = await client.post("/v1/groups", json={"name": "team"}, headers=_h("owner"))
    assert dup.status_code == 409, dup.text
    # a different owner may reuse the name
    other = await client.post("/v1/groups", json={"name": "team"}, headers=_h("bob"))
    assert other.status_code == 201


# --------------------------------------------------------------------------- #
# leak-safe view + owner-or-admin manage
# --------------------------------------------------------------------------- #


async def test_non_member_cannot_view_or_manage_group(client):
    gid = await _make_group(client)
    await client.post(f"/v1/groups/{gid}/members", json={"subject": "bob"}, headers=_h("owner"))

    # an outsider (neither owner nor member) gets a leak-safe 404 on view + manage
    assert (await client.get(f"/v1/groups/{gid}", headers=_h("outsider"))).status_code == 404
    assert (await client.delete(f"/v1/groups/{gid}", headers=_h("outsider"))).status_code == 404
    add = await client.post(
        f"/v1/groups/{gid}/members", json={"subject": "eve"}, headers=_h("outsider")
    )
    assert add.status_code == 404

    # a member (Bob) may VIEW but not MANAGE — an honest 403, not a fake 404
    assert (await client.get(f"/v1/groups/{gid}", headers=_h("bob"))).status_code == 200
    assert (await client.delete(f"/v1/groups/{gid}", headers=_h("bob"))).status_code == 403
    bob_add = await client.post(
        f"/v1/groups/{gid}/members", json={"subject": "eve"}, headers=_h("bob")
    )
    assert bob_add.status_code == 403

    # an admin may manage (bypass)
    assert (await client.delete(f"/v1/groups/{gid}", headers=_h("admin"))).status_code == 204


async def test_builtin_public_group_is_viewable_but_not_deletable(client):
    # public is viewable by anyone (constant-true membership), empty member list
    v = await client.get(f"/v1/groups/{PUBLIC_GROUP}", headers=_h("outsider"))
    assert v.status_code == 200 and v.json()["group"]["built_in"] is True
    assert v.json()["members"] == []
    # not deletable, even by an admin (the store refuses; surfaced as 409)
    d = await client.delete(f"/v1/groups/{PUBLIC_GROUP}", headers=_h("admin"))
    assert d.status_code == 409, d.text
    # not member-editable either
    a = await client.post(
        f"/v1/groups/{PUBLIC_GROUP}/members", json={"subject": "bob"}, headers=_h("admin")
    )
    assert a.status_code == 409


async def test_group_member_cannot_be_a_group(client):
    outer = await _make_group(client, "outer")
    inner = await _make_group(client, "inner")
    # naming a group id via the reserved @group: form is rejected (no nesting)
    r = await client.post(
        f"/v1/groups/{outer}/members", json={"subject": f"@group:{inner}"}, headers=_h("owner")
    )
    assert r.status_code == 422, r.text


# --------------------------------------------------------------------------- #
# the payoff: a group grant reaches members end-to-end through resolve_access
# --------------------------------------------------------------------------- #


async def _grant_group_read(client, cid: str, gid: str) -> str:
    r = await client.post(
        f"/v1/collections/{cid}/shares",
        json={"grantee": f"@group:{gid}"},
        headers=_h("owner"),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["grantee_type"] == GRANTEE_GROUP
    assert body["grantee_id"] == gid
    assert body["permission"] == PERM_READ
    return body["id"]


async def test_group_grant_lets_member_query_and_hides_from_non_member(client):
    _register(_entry("priv"))
    await _own("priv", "owner")
    gid = await _make_group(client)
    await client.post(f"/v1/groups/{gid}/members", json={"subject": "bob"}, headers=_h("owner"))

    # before the grant, even the member cannot read the private collection
    assert (
        await client.post("/v1/retrieve", json={"query": "x", "collection": "priv"}, headers=_h("bob"))
    ).status_code == 404

    await _grant_group_read(client, "priv", gid)

    # the member can now query end-to-end...
    ok = await client.post("/v1/retrieve", json={"query": "x", "collection": "priv"}, headers=_h("bob"))
    assert ok.status_code == 200, ok.text
    # ...but a non-member still cannot
    assert (
        await client.post("/v1/retrieve", json={"query": "x", "collection": "priv"}, headers=_h("outsider"))
    ).status_code == 404


async def test_removing_member_revokes_access_immediately(client):
    _register(_entry("priv"))
    await _own("priv", "owner")
    gid = await _make_group(client)
    await client.post(f"/v1/groups/{gid}/members", json={"subject": "bob"}, headers=_h("owner"))
    await _grant_group_read(client, "priv", gid)
    assert (
        await client.post("/v1/retrieve", json={"query": "x", "collection": "priv"}, headers=_h("bob"))
    ).status_code == 200

    # remove Bob via the API — next request is denied, no caching
    rm = await client.delete(f"/v1/groups/{gid}/members/{BOB_SUBJECT}", headers=_h("owner"))
    assert rm.status_code == 204, rm.text
    assert (
        await client.post("/v1/retrieve", json={"query": "x", "collection": "priv"}, headers=_h("bob"))
    ).status_code == 404


async def test_removing_member_with_bare_username_matches_add(client):
    # Regression: DELETE resolves the path subject exactly like POST add, so a
    # member added with a bare 'bob' username can be removed with the same bare
    # form (resolved to 'bvbrc:bob'), not only the full 'issuer:sub' string.
    _register(_entry("priv"))
    await _own("priv", "owner")
    gid = await _make_group(client)
    await client.post(f"/v1/groups/{gid}/members", json={"subject": "bob"}, headers=_h("owner"))
    await _grant_group_read(client, "priv", gid)
    assert (
        await client.post("/v1/retrieve", json={"query": "x", "collection": "priv"}, headers=_h("bob"))
    ).status_code == 200

    # remove with the bare form used to add — must actually revoke, not no-op
    rm = await client.delete(f"/v1/groups/{gid}/members/bob", headers=_h("owner"))
    assert rm.status_code == 204, rm.text
    assert (
        await client.post("/v1/retrieve", json={"query": "x", "collection": "priv"}, headers=_h("bob"))
    ).status_code == 404


async def test_removing_member_rejects_group_target_form(client):
    _register(_entry("priv"))
    await _own("priv", "owner")
    gid = await _make_group(client)
    rm = await client.delete(f"/v1/groups/{gid}/members/@public", headers=_h("owner"))
    assert rm.status_code == 422, rm.text


async def test_deleting_group_revokes_member_access(client):
    _register(_entry("priv"))
    await _own("priv", "owner")
    gid = await _make_group(client)
    await client.post(f"/v1/groups/{gid}/members", json={"subject": "bob"}, headers=_h("owner"))
    await _grant_group_read(client, "priv", gid)
    assert (
        await client.post("/v1/retrieve", json={"query": "x", "collection": "priv"}, headers=_h("bob"))
    ).status_code == 200

    assert (await client.delete(f"/v1/groups/{gid}", headers=_h("owner"))).status_code == 204
    # the share row survives (audit) but a deleted group grants nothing
    assert (
        await client.post("/v1/retrieve", json={"query": "x", "collection": "priv"}, headers=_h("bob"))
    ).status_code == 404


async def test_revoking_group_share_revokes_member_access(client):
    _register(_entry("priv"))
    await _own("priv", "owner")
    gid = await _make_group(client)
    await client.post(f"/v1/groups/{gid}/members", json={"subject": "bob"}, headers=_h("owner"))
    share_id = await _grant_group_read(client, "priv", gid)
    assert (
        await client.post("/v1/retrieve", json={"query": "x", "collection": "priv"}, headers=_h("bob"))
    ).status_code == 200

    rev = await client.delete(f"/v1/collections/priv/shares/{share_id}", headers=_h("owner"))
    assert rev.status_code == 204, rev.text
    assert (
        await client.post("/v1/retrieve", json={"query": "x", "collection": "priv"}, headers=_h("bob"))
    ).status_code == 404


# --------------------------------------------------------------------------- #
# grant-time invariants: read-only, group must exist
# --------------------------------------------------------------------------- #


async def test_group_grant_is_read_only(client):
    _register(_entry("priv"))
    await _own("priv", "owner")
    gid = await _make_group(client)
    # write is deferred (422); owner is transferred, not granted (400)
    w = await client.post(
        "/v1/collections/priv/shares",
        json={"grantee": f"@group:{gid}", "permission": "write"},
        headers=_h("owner"),
    )
    assert w.status_code == 422, w.text
    o = await client.post(
        "/v1/collections/priv/shares",
        json={"grantee": f"@group:{gid}", "permission": "owner"},
        headers=_h("owner"),
    )
    assert o.status_code == 400, o.text


async def test_grant_to_unknown_group_is_422_echoing_the_id(client):
    _register(_entry("priv"))
    await _own("priv", "owner")
    r = await client.post(
        "/v1/collections/priv/shares",
        json={"grantee": "@group:no-such-group"},
        headers=_h("owner"),
    )
    assert r.status_code == 422, r.text
    assert "no-such-group" in r.text


# --------------------------------------------------------------------------- #
# _shared_scope co-resident / default suppression still holds for group readers
# --------------------------------------------------------------------------- #


class _Capture:
    def __init__(self) -> None:
        self.filters: dict | None = None

    async def retrieve(self, *args, **kwargs):
        self.filters = kwargs.get("filters")
        return []


async def test_group_reader_gets_owner_tenant_widening_on_exclusive_store(client, monkeypatch):
    _register(_entry("priv"))
    await _own("priv", "owner")
    gid = await _make_group(client)
    await client.post(f"/v1/groups/{gid}/members", json={"subject": "bob"}, headers=_h("owner"))
    await _grant_group_read(client, "priv", gid)

    cap = _Capture()
    monkeypatch.setattr(app.state, "retriever", cap)
    r = await client.post("/v1/retrieve", json={"query": "x", "collection": "priv"}, headers=_h("bob"))
    assert r.status_code == 200, r.text
    tids = cap.filters["tenant_id"]
    assert "owner" in tids and BOB_SUBJECT in tids and "public" in tids


async def test_group_reader_no_widening_on_coresident_store(client, monkeypatch):
    shared = _entry("shared-x", collection="phys")
    other = _entry("shared-y", collection="phys")  # co-resident, NOT shared
    _register(shared, other)
    await _own("shared-x", "owner")
    await _own("shared-y", "owner")
    gid = await _make_group(client)
    await client.post(f"/v1/groups/{gid}/members", json={"subject": "bob"}, headers=_h("owner"))
    await _grant_group_read(client, "shared-x", gid)

    cap = _Capture()
    monkeypatch.setattr(app.state, "retriever", cap)
    r = await client.post("/v1/retrieve", json={"query": "x", "collection": "shared-x"}, headers=_h("bob"))
    assert r.status_code == 200, r.text
    # co-resident store: no owner-tenant widening, so the group reader sees only
    # its own + public (the existing safe-under-expose behaviour, unchanged).
    assert "owner" not in cap.filters["tenant_id"]
