"""Collection share management endpoints (issue #244).

GET/POST/DELETE /v1/collections/{id}/shares, owner-or-admin through the ONE
authorization seam (``ragstack.api.access.enforce_access`` with the ``owner``
action). Grants are ``read`` only in v1; sharing with ``@public`` opens the
collection to everyone via the built-in public group; sharing with a bare
BV-BRC username is prefixed to ``bvbrc:<username>`` and pre-provisions a users
row so a never-logged-in grantee can read after their first login.

Reuses the multi-principal fixtures of ``test_collection_ownership``: per-tenant
API keys (owner / stranger / admin), the in-memory ACL singleton the conftest
installs, and the registry rebuilder. ASGITransport skips the lifespan, so the
ACL store is driven directly to arrange owners/grants.
"""
from __future__ import annotations

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

KEYS = {"owner": "k-owner", "stranger": "k-stranger", "admin": "k-admin"}


@pytest.fixture(autouse=True)
def _principals(monkeypatch):
    monkeypatch.setattr(security.settings, "api_keys", list(KEYS.values()))
    monkeypatch.setattr(
        security.settings,
        "api_key_tenants",
        {"k-owner": "owner", "k-stranger": "stranger", "k-admin": "admin"},
    )
    monkeypatch.setattr(security.settings, "api_key_roles", {"k-admin": ROLE_ADMIN})
    monkeypatch.setattr(security.settings, "default_role", ROLE_USER)


def _h(who: str) -> dict:
    return {"X-API-Key": KEYS[who]}


def _entry(cid: str, default: bool = False, owner: str = "") -> CollectionEntry:
    from tests.api.conftest import _StateRetriever

    return CollectionEntry(
        id=cid, label=cid, collection=cid, model="test-model", dim=4,
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


# --------------------------------------------------------------------------- #
# POST — grant read to a user (end-to-end through resolve_access)
# --------------------------------------------------------------------------- #


async def test_owner_grants_read_to_a_user_who_can_then_query(client):
    _register(_entry("priv"))
    await _own("priv", "owner")
    # Before the grant, the stranger cannot read the private collection.
    denied = await client.post(
        "/v1/retrieve", json={"query": "x", "collection": "priv"}, headers=_h("stranger")
    )
    assert denied.status_code == 404, denied.text

    r = await client.post(
        "/v1/collections/priv/shares",
        json={"grantee": "stranger"},
        headers=_h("owner"),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["grantee_type"] == GRANTEE_USER
    # A bare username is BV-BRC-prefixed; a typo would be visible in this echo.
    assert body["grantee_id"] == "bvbrc:stranger"
    assert body["permission"] == PERM_READ
    assert body["granted_by"] == "owner"
    assert body["active"] is True

    rows = await get_acl_store().shares_for("priv")
    assert any(
        s.grantee_type == GRANTEE_USER and s.grantee_id == "bvbrc:stranger" for s in rows
    )


async def test_shared_read_widens_retrieval_scope_to_owner_tenant(client, monkeypatch):
    # ADR-0004 read authorization and ADR-0005 per-writer tenant_id scoping are two
    # independent gates. A private collection's chunks are stamped with the OWNER's
    # tenant at ingest, so a grantee whose scope is only {own, public} passes the
    # read gate but would see zero shared chunks. The share must therefore widen the
    # grantee's retrieval scope to include the owner's writer-tenant.
    _register(_entry("priv"))
    await _own("priv", "owner")
    # Grant read directly to the stranger's subject (== its principal tenant).
    await get_acl_store().grant(
        "priv", GRANTEE_USER, "stranger", PERM_READ, granted_by="owner"
    )

    captured: dict = {}

    class _Capture:
        async def retrieve(self, *args, **kwargs):
            captured["filters"] = kwargs.get("filters")
            return []

    monkeypatch.setattr(app.state, "retriever", _Capture())
    r = await client.post(
        "/v1/retrieve", json={"query": "x", "collection": "priv"}, headers=_h("stranger")
    )
    assert r.status_code == 200, r.text
    tids = captured["filters"]["tenant_id"]
    assert "owner" in tids  # the owner's writer-tenant, added by the share
    assert "stranger" in tids  # the caller's own
    assert "public" in tids


async def test_owner_query_scope_is_not_widened(client, monkeypatch):
    # The owner querying its own collection gets no extra tenant (owner == caller),
    # so scope stays {own, public} — no duplicate/self-widening.
    _register(_entry("priv"))
    await _own("priv", "owner")

    captured: dict = {}

    class _Capture:
        async def retrieve(self, *args, **kwargs):
            captured["filters"] = kwargs.get("filters")
            return []

    monkeypatch.setattr(app.state, "retriever", _Capture())
    r = await client.post(
        "/v1/retrieve", json={"query": "x", "collection": "priv"}, headers=_h("owner")
    )
    assert r.status_code == 200, r.text
    assert captured["filters"]["tenant_id"] == ["owner", "public"]


async def test_grant_to_full_subject_is_kept_verbatim(client):
    # A grantee that already carries a ':' is a full 'issuer:subject' string and is
    # stored verbatim — the issuer field is NOT applied on top.
    _register(_entry("priv"))
    await _own("priv", "owner")
    r = await client.post(
        "/v1/collections/priv/shares",
        json={"grantee": "oidc:bob", "issuer": "bvbrc"},
        headers=_h("owner"),
    )
    assert r.status_code == 201, r.text
    assert r.json()["grantee_id"] == "oidc:bob"


async def test_bare_username_with_blank_issuer_is_422(client):
    _register(_entry("priv"))
    await _own("priv", "owner")
    r = await client.post(
        "/v1/collections/priv/shares",
        json={"grantee": "stranger", "issuer": ""},
        headers=_h("owner"),
    )
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("grantee", [":", "bvbrc:", ":alice", " : ", "bvbrc: "])
async def test_degenerate_issuer_subject_grantee_is_422(client, grantee):
    # A ':'-bearing grantee is taken as a full 'issuer:subject', but an empty
    # issuer or subject half can never match a principal at read time — it would
    # be a silently-accepted unclaimable grant. Reject it (422), like the
    # empty/whitespace path, instead of creating the dead row.
    _register(_entry("priv"))
    await _own("priv", "owner")
    r = await client.post(
        "/v1/collections/priv/shares",
        json={"grantee": grantee},
        headers=_h("owner"),
    )
    assert r.status_code == 422, r.text
    # No share row was created.
    rows = await get_acl_store().shares_for("priv")
    assert all(s.permission == PERM_OWNER for s in rows)


async def test_never_seen_username_is_provisioned_and_can_read(client):
    # Grant to a BV-BRC username that has never logged in: a provisional users row
    # is created and the grantee reads after "logging in" (here: a principal whose
    # tenant equals the resolved subject).
    _register(_entry("lib"))
    await _own("lib", "owner")
    r = await client.post(
        "/v1/collections/lib/shares",
        json={"grantee": "alice@patricbrc.org"},
        headers=_h("owner"),
    )
    assert r.status_code == 201, r.text
    assert r.json()["grantee_id"] == "bvbrc:alice@patricbrc.org"
    # The provisional users row exists.
    store = get_acl_store()
    user = await store.get("bvbrc:alice@patricbrc.org")
    assert user is not None
    # And resolve_access grants read to that subject directly.
    from ragstack.authz import resolve_access

    decision = await resolve_access(
        "bvbrc:alice@patricbrc.org", ROLE_USER, "lib", "read", store
    )
    assert decision.allowed is True


# --------------------------------------------------------------------------- #
# POST — public grant opens read to everyone
# --------------------------------------------------------------------------- #


async def test_public_grant_opens_read_to_everyone(client):
    _register(_entry("shared"))
    await _own("shared", "owner")
    r = await client.post(
        "/v1/collections/shared/shares",
        json={"grantee": "@public"},
        headers=_h("owner"),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["grantee_type"] == GRANTEE_GROUP
    assert body["grantee_id"] == PUBLIC_GROUP
    # A stranger who owns nothing can now read it.
    ok = await client.post(
        "/v1/retrieve", json={"query": "x", "collection": "shared"}, headers=_h("stranger")
    )
    assert ok.status_code == 200, ok.text


async def test_public_literal_without_at_sign_also_maps_to_group(client):
    _register(_entry("shared"))
    await _own("shared", "owner")
    r = await client.post(
        "/v1/collections/shared/shares",
        json={"grantee": "public"},
        headers=_h("owner"),
    )
    assert r.status_code == 201, r.text
    assert r.json()["grantee_id"] == PUBLIC_GROUP


# --------------------------------------------------------------------------- #
# authorization: only owner-or-admin may grant / list / revoke
# --------------------------------------------------------------------------- #


async def test_non_owner_cannot_grant_unreadable_is_404(client):
    _register(_entry("priv"))
    await _own("priv", "owner")
    r = await client.post(
        "/v1/collections/priv/shares",
        json={"grantee": "bob"},
        headers=_h("stranger"),
    )
    # Stranger can't read `priv` → 404 (no existence oracle); nothing granted.
    assert r.status_code == 404, r.text
    assert all(
        s.grantee_id != "bvbrc:bob"
        for s in await get_acl_store().shares_for("priv")
    )


async def test_non_owner_grant_of_a_readable_collection_is_403(client):
    _register(_entry("open"))
    await _own("open", "owner")
    await get_acl_store().grant(
        "open", GRANTEE_GROUP, PUBLIC_GROUP, PERM_READ, granted_by="owner"
    )
    r = await client.post(
        "/v1/collections/open/shares",
        json={"grantee": "bob"},
        headers=_h("stranger"),
    )
    assert r.status_code == 403, r.text


async def test_admin_can_grant_anywhere(client):
    _register(_entry("priv"))
    await _own("priv", "owner")
    r = await client.post(
        "/v1/collections/priv/shares",
        json={"grantee": "carol"},
        headers=_h("admin"),
    )
    assert r.status_code == 201, r.text


async def test_grant_to_unknown_collection_is_404(client):
    _register(_entry("priv"))
    r = await client.post(
        "/v1/collections/nope/shares",
        json={"grantee": "bob"},
        headers=_h("owner"),
    )
    assert r.status_code == 404, r.text


# --------------------------------------------------------------------------- #
# validation: read-only, no owner/write, no duplicate
# --------------------------------------------------------------------------- #


async def test_granting_owner_is_rejected(client):
    _register(_entry("priv"))
    await _own("priv", "owner")
    r = await client.post(
        "/v1/collections/priv/shares",
        json={"grantee": "bob", "permission": "owner"},
        headers=_h("owner"),
    )
    assert r.status_code == 400, r.text
    assert "transfer" in r.text.lower()


async def test_granting_write_is_rejected(client):
    _register(_entry("priv"))
    await _own("priv", "owner")
    r = await client.post(
        "/v1/collections/priv/shares",
        json={"grantee": "bob", "permission": "write"},
        headers=_h("owner"),
    )
    assert r.status_code == 422, r.text


async def test_empty_grantee_is_rejected(client):
    _register(_entry("priv"))
    await _own("priv", "owner")
    r = await client.post(
        "/v1/collections/priv/shares",
        json={"grantee": "   "},
        headers=_h("owner"),
    )
    assert r.status_code == 422, r.text


async def test_grant_to_the_owner_subject_is_a_rejected_no_op_409(client):
    # A read grant to the collection's own owner is a trivial no-op (the owner
    # already holds every permission) and is rejected. `priv2` is owned by
    # 'bvbrc:bob'; a bare-username grant to 'bob' resolves to that same subject.
    _register(_entry("priv2"))
    await _own("priv2", "bvbrc:bob")
    r = await client.post(
        "/v1/collections/priv2/shares",
        json={"grantee": "bob"},  # → 'bvbrc:bob' == owner subject
        headers=_h("admin"),  # admin, since the owner subject isn't one of our keys
    )
    assert r.status_code == 409, r.text
    assert "owns" in r.text.lower()


async def test_duplicate_active_grant_is_409(client):
    _register(_entry("priv"))
    await _own("priv", "owner")
    first = await client.post(
        "/v1/collections/priv/shares", json={"grantee": "dave"}, headers=_h("owner")
    )
    assert first.status_code == 201, first.text
    second = await client.post(
        "/v1/collections/priv/shares", json={"grantee": "dave"}, headers=_h("owner")
    )
    assert second.status_code == 409, second.text


# --------------------------------------------------------------------------- #
# GET — list shows active shares, hides revoked, reports owner
# --------------------------------------------------------------------------- #


async def test_list_shows_owner_and_active_shares(client):
    _register(_entry("priv"))
    await _own("priv", "owner")
    await client.post(
        "/v1/collections/priv/shares", json={"grantee": "erin"}, headers=_h("owner")
    )
    r = await client.get("/v1/collections/priv/shares", headers=_h("owner"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["owner"] == "owner"
    grantees = {s["grantee_id"] for s in body["shares"]}
    assert "bvbrc:erin" in grantees


async def test_list_hides_revoked_by_default_but_include_revoked_shows_them(client):
    _register(_entry("priv"))
    await _own("priv", "owner")
    g = await client.post(
        "/v1/collections/priv/shares", json={"grantee": "frank"}, headers=_h("owner")
    )
    share_id = g.json()["id"]
    d = await client.delete(f"/v1/collections/priv/shares/{share_id}", headers=_h("owner"))
    assert d.status_code == 204, d.text

    active = await client.get("/v1/collections/priv/shares", headers=_h("owner"))
    assert all(s["grantee_id"] != "bvbrc:frank" for s in active.json()["shares"])

    hist = await client.get(
        "/v1/collections/priv/shares",
        params={"include_revoked": "true"},
        headers=_h("owner"),
    )
    revoked = [s for s in hist.json()["shares"] if s["grantee_id"] == "bvbrc:frank"]
    assert len(revoked) == 1
    assert revoked[0]["active"] is False
    assert revoked[0]["revoked_by"] == "owner"


async def test_non_owner_cannot_list_unreadable(client):
    _register(_entry("priv"))
    await _own("priv", "owner")
    r = await client.get("/v1/collections/priv/shares", headers=_h("stranger"))
    assert r.status_code == 404, r.text


# --------------------------------------------------------------------------- #
# DELETE — revoke removes access; un-publish; guards
# --------------------------------------------------------------------------- #


async def test_revoke_removes_read_access(client):
    _register(_entry("priv"))
    await _own("priv", "owner")
    g = await client.post(
        "/v1/collections/priv/shares", json={"grantee": "stranger"}, headers=_h("owner")
    )
    share_id = g.json()["id"]
    # granted → the store now shows the active row
    assert any(
        s.grantee_id == "bvbrc:stranger"
        for s in await get_acl_store().shares_for("priv")
    )
    d = await client.delete(f"/v1/collections/priv/shares/{share_id}", headers=_h("owner"))
    assert d.status_code == 204, d.text
    # active row gone; history survives
    assert all(
        s.grantee_id != "bvbrc:stranger"
        for s in await get_acl_store().shares_for("priv")
    )
    hist = await get_acl_store().shares_for("priv", include_revoked=True)
    assert any(s.grantee_id == "bvbrc:stranger" for s in hist)


async def test_unpublish_is_delete_of_the_public_share(client):
    _register(_entry("shared"))
    await _own("shared", "owner")
    g = await client.post(
        "/v1/collections/shared/shares", json={"grantee": "@public"}, headers=_h("owner")
    )
    share_id = g.json()["id"]
    # public read works…
    assert (
        await client.post(
            "/v1/retrieve", json={"query": "x", "collection": "shared"}, headers=_h("stranger")
        )
    ).status_code == 200
    # …until un-published.
    d = await client.delete(
        f"/v1/collections/shared/shares/{share_id}", headers=_h("owner")
    )
    assert d.status_code == 204, d.text
    assert (
        await client.post(
            "/v1/retrieve", json={"query": "x", "collection": "shared"}, headers=_h("stranger")
        )
    ).status_code == 404


async def test_revoke_of_unknown_share_is_404(client):
    _register(_entry("priv"))
    await _own("priv", "owner")
    r = await client.delete(
        "/v1/collections/priv/shares/does-not-exist", headers=_h("owner")
    )
    assert r.status_code == 404, r.text


async def test_revoke_cross_collection_share_via_mismatched_path_is_404(client):
    # A share belonging to collection B must not be revocable via collection A's
    # path even though the caller owns A (the store's revoke is not scoped).
    _register(_entry("a"), _entry("b"))
    await _own("a", "owner")
    await _own("b", "owner")
    g = await client.post(
        "/v1/collections/b/shares", json={"grantee": "gwen"}, headers=_h("owner")
    )
    share_id = g.json()["id"]
    r = await client.delete(f"/v1/collections/a/shares/{share_id}", headers=_h("owner"))
    assert r.status_code == 404, r.text
    # the share on B is untouched
    assert any(
        s.id == share_id for s in await get_acl_store().shares_for("b")
    )


async def test_owner_row_is_not_revocable_via_the_api(client):
    _register(_entry("priv"))
    await _own("priv", "owner")
    rows = await get_acl_store().shares_for("priv")
    owner_row = next(s for s in rows if s.permission == PERM_OWNER)
    r = await client.delete(
        f"/v1/collections/priv/shares/{owner_row.id}", headers=_h("owner")
    )
    assert r.status_code == 409, r.text
    # ownership intact
    assert await get_acl_store().owner_of("priv") == "owner"


async def test_non_owner_cannot_revoke(client):
    _register(_entry("priv"))
    await _own("priv", "owner")
    g = await client.post(
        "/v1/collections/priv/shares", json={"grantee": "heidi"}, headers=_h("owner")
    )
    share_id = g.json()["id"]
    r = await client.delete(
        f"/v1/collections/priv/shares/{share_id}", headers=_h("stranger")
    )
    assert r.status_code == 404, r.text  # unreadable → 404, not 403


# --------------------------------------------------------------------------- #
# fail closed: a store outage is 503
# --------------------------------------------------------------------------- #


async def test_list_store_outage_is_503(client, monkeypatch):
    _register(_entry("priv"))
    await _own("priv", "owner")

    async def boom(*_a, **_k):
        raise ConnectionError("acl db down")

    # owner_of answers the enforce_access gate; break shares_for so the gate passes
    # and the listing itself fails closed.
    monkeypatch.setattr(get_acl_store(), "shares_for", boom)
    r = await client.get("/v1/collections/priv/shares", headers=_h("owner"))
    assert r.status_code == 503, r.text


async def test_grant_store_outage_is_503(client, monkeypatch):
    _register(_entry("priv"))
    await _own("priv", "owner")

    async def boom(*_a, **_k):
        raise ConnectionError("acl db down")

    # Break owner_of so the enforce_access(owner) gate itself fails closed.
    monkeypatch.setattr(get_acl_store(), "owner_of", boom)
    r = await client.post(
        "/v1/collections/priv/shares", json={"grantee": "ivan"}, headers=_h("owner")
    )
    assert r.status_code == 503, r.text
