"""Collection ownership enforcement at resolution (issue #243 part 2).

The ONE authorization seam (``ragstack.authz.resolve_access``, reached only via
``ragstack.api.access``) gates every collection resolution:

- the creator can query its own PRIVATE collection; a second principal cannot;
- a ``public`` read grant re-opens it to everyone;
- a legacy/backfilled collection stays world-readable (anonymous-readable);
- ingest into someone else's collection is denied (owner-or-admin);
- ``DELETE /v1/collections`` is owner-or-admin — owner ok, non-owner denied,
  admin bypasses AND logs;
- the collection listing filters to what each caller may read;
- a store outage is a 503 (fail closed), never a silent allow;
- backfill is idempotent across two startups.

Multi-principal callers are faked with per-tenant API keys (the
test_tenant_isolation convention). The ACL store is the process-wide in-memory
singleton the conftest installs; here we drive it directly to set up owners and
grants, since ASGITransport skips the lifespan that would otherwise backfill.
"""
from __future__ import annotations

import logging

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
    """Three keyed callers: owner (user), stranger (user), admin. Auth is ON
    (api_keys set), so the ownership seam is enforced — the point of the suite."""
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
        is_shared_surface=default, retriever=_StateRetriever(),
        vector_store=app.state.vector_store, text_index=app.state.text_index,
        owner=owner,
    )


def _register(*entries: CollectionEntry) -> None:
    """Rebuild the registry with a default plus the given entries."""
    # build_ingestor_for (the named-collection ingest path) reads these off
    # app.state; the client fixture doesn't set them, so default them here.
    app.state.kg_extractor = None
    app.state.doi_enricher = None
    app.state.collections = CollectionRegistry(
        [_entry("default", True), *entries], default_id="default"
    )


async def _own(cid: str, subject: str) -> None:
    await get_acl_store().grant(cid, GRANTEE_USER, subject, PERM_OWNER, granted_by=subject)


# --------------------------------------------------------------------------- #
# read: private is owner-only; public re-opens it
# --------------------------------------------------------------------------- #


async def test_creator_can_query_own_private_collection(client):
    _register(_entry("priv"))
    await _own("priv", "owner")
    r = await client.post("/v1/query", json={"query": "x", "collection": "priv"}, headers=_h("owner"))
    assert r.status_code == 200, r.text


async def test_second_principal_is_denied_a_private_collection(client):
    _register(_entry("priv"))
    await _own("priv", "owner")
    # 404, not 403: a read denial must be indistinguishable from an unknown id so
    # the private collection's very existence isn't leaked.
    for path in ("/v1/query", "/v1/retrieve"):
        r = await client.post(path, json={"query": "x", "collection": "priv"}, headers=_h("stranger"))
        assert r.status_code == 404, (path, r.text)


async def test_public_grant_opens_read_to_everyone(client):
    _register(_entry("shared"))
    await _own("shared", "owner")
    await get_acl_store().grant(
        "shared", GRANTEE_GROUP, PUBLIC_GROUP, PERM_READ, granted_by="owner"
    )
    r = await client.post("/v1/retrieve", json={"query": "x", "collection": "shared"}, headers=_h("stranger"))
    assert r.status_code == 200, r.text


async def test_chunks_endpoint_is_gated_too(client):
    _register(_entry("priv"))
    await _own("priv", "owner")
    r = await client.get("/v1/chunks", params={"ids": "c1", "collection": "priv"}, headers=_h("stranger"))
    assert r.status_code == 404


async def test_legacy_backfilled_collection_stays_anonymous_readable(client):
    # The conftest seeds `default` exactly as the startup backfill would: owned by
    # legacy:admin + public read. A stranger who owns nothing still reads it.
    r = await client.post("/v1/retrieve", json={"query": "x"}, headers=_h("stranger"))
    assert r.status_code == 200, r.text
    lst = await client.get("/v1/documents", headers=_h("stranger"))
    assert lst.status_code == 200


# --------------------------------------------------------------------------- #
# write: ingest is owner-or-admin
# --------------------------------------------------------------------------- #


async def test_ingest_into_someone_elses_collection_is_denied(client):
    _register(_entry("priv"))
    await _own("priv", "owner")
    r = await client.post(
        "/v1/ingest", json={"source": "x.txt", "collection": "priv"}, headers=_h("stranger")
    )
    # 404, not 403: the stranger can't READ `priv` either, so the write denial
    # must be indistinguishable from an unknown id — a 403 here would make the
    # ingest endpoint an existence oracle for private collections.
    assert r.status_code == 404, r.text


async def test_ingest_denial_is_403_only_when_the_collection_is_readable(client):
    # A collection the caller CAN read (public) but does not own: the honest
    # answer is 403 — no existence is leaked, it is already listed to everyone.
    _register(_entry("shared"))
    await _own("shared", "owner")
    await get_acl_store().grant(
        "shared", GRANTEE_GROUP, PUBLIC_GROUP, PERM_READ, granted_by="owner"
    )
    r = await client.post(
        "/v1/ingest", json={"source": "x.txt", "collection": "shared"}, headers=_h("stranger")
    )
    assert r.status_code == 403, r.text


async def test_default_collection_stays_open_to_tenant_scoped_writes(client):
    # The DEFAULT collection is the shared pre-ownership surface (public read via
    # backfill); ingest into it and document deletes stay open to any principal
    # that can READ it — the per-chunk tenant stamp is the write isolation there
    # (and the authz conformance contract: core data ops need auth, not a role).
    r = await client.post("/v1/ingest", json={"source": "x.txt"}, headers=_h("stranger"))
    assert r.status_code == 200, r.text
    d = await client.delete("/v1/documents/some-doc", headers=_h("stranger"))
    assert d.status_code == 204, d.text


async def test_owner_can_ingest_into_own_collection(client):
    _register(_entry("priv"))
    await _own("priv", "owner")
    r = await client.post(
        "/v1/ingest", json={"source": "x.txt", "collection": "priv"}, headers=_h("owner")
    )
    assert r.status_code == 200, r.text


async def test_admin_can_ingest_anywhere(client):
    _register(_entry("priv"))
    await _own("priv", "owner")
    r = await client.post(
        "/v1/ingest", json={"source": "x.txt", "collection": "priv"}, headers=_h("admin")
    )
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------- #
# owner: DELETE /v1/collections is owner-or-admin
# --------------------------------------------------------------------------- #


async def test_owner_can_delete_own_collection(client):
    _register(_entry("mine"))
    await _own("mine", "owner")
    r = await client.delete("/v1/collections/mine?purge=true", headers=_h("owner"))
    assert r.status_code == 200, r.text


async def test_non_owner_cannot_delete(client):
    _register(_entry("mine"))
    await _own("mine", "owner")
    # The stranger can't read `mine` either → 404 (no existence oracle); a
    # non-owner who CAN read it would get the honest 403 instead.
    r = await client.delete("/v1/collections/mine", headers=_h("stranger"))
    assert r.status_code == 404, r.text
    assert app.state.collections.has("mine")  # nothing removed


async def test_non_owner_delete_of_a_readable_collection_is_403(client):
    _register(_entry("open"))
    await _own("open", "owner")
    await get_acl_store().grant(
        "open", GRANTEE_GROUP, PUBLIC_GROUP, PERM_READ, granted_by="owner"
    )
    r = await client.delete("/v1/collections/open", headers=_h("stranger"))
    assert r.status_code == 403, r.text
    assert app.state.collections.has("open")


async def test_delete_revokes_the_collections_acl_rows(client):
    # The id namespace is reusable: a delete must not leave the owner row (or a
    # public grant) behind for the next collection minted under the same id.
    _register(_entry("mine"))
    await _own("mine", "owner")
    await get_acl_store().grant(
        "mine", GRANTEE_GROUP, PUBLIC_GROUP, PERM_READ, granted_by="owner"
    )
    r = await client.delete("/v1/collections/mine?purge=true", headers=_h("owner"))
    assert r.status_code == 200, r.text
    store = get_acl_store()
    assert await store.owner_of("mine") is None
    assert await store.shares_for("mine") == []  # no ACTIVE rows survive...
    history = await store.shares_for("mine", include_revoked=True)
    assert len(history) == 2  # ...but the audit history does (soft revoke)


async def test_reused_id_does_not_inherit_the_deleted_owners_row(client):
    # Full hijack repro from the review: owner creates + deletes `fresh`; a
    # stranger re-creating the same id must OWN the new collection — not find
    # the previous owner still owner-of-record.
    r = await client.post("/v1/collections", json={"id": "fresh"}, headers=_h("owner"))
    assert r.status_code == 201, r.text
    assert await get_acl_store().owner_of("fresh") == "owner"
    d = await client.delete("/v1/collections/fresh?purge=true", headers=_h("owner"))
    assert d.status_code == 200, d.text
    r2 = await client.post("/v1/collections", json={"id": "fresh"}, headers=_h("stranger"))
    assert r2.status_code == 201, r2.text
    assert await get_acl_store().owner_of("fresh") == "stranger"


async def test_create_over_residual_foreign_acl_state_is_refused(client):
    # If a stale ACTIVE owner row for someone else somehow survives under an id
    # (the pre-fix hijack shape), the create must refuse and roll back — never
    # 201 with the wrong owner-of-record.
    await _own("ghost", "owner")  # residual row; no registry entry
    r = await client.post("/v1/collections", json={"id": "ghost"}, headers=_h("stranger"))
    assert r.status_code == 409, r.text
    assert not app.state.collections.has("ghost")  # rolled back
    assert await get_acl_store().owner_of("ghost") == "owner"  # untouched


async def test_create_over_existing_private_id_does_not_leak_existence(client):
    # Independent review finding #1: query returns 404 for an unreadable
    # collection, but POST /v1/collections must not confirm the id via a
    # "already exists" 409 — that would be an enumeration oracle. A stranger
    # gets a generic "unavailable"; the owner, who may read it, gets the
    # informative "already exists".
    _register(_entry("secret"))
    await _own("secret", "owner")
    stranger = await client.post(
        "/v1/collections", json={"id": "secret"}, headers=_h("stranger")
    )
    assert stranger.status_code == 409, stranger.text
    assert "already exists" not in stranger.text
    assert "unavailable" in stranger.text.lower()
    owner = await client.post(
        "/v1/collections", json={"id": "secret"}, headers=_h("owner")
    )
    assert owner.status_code == 409
    assert "already exists" in owner.text


async def test_admin_delete_bypasses_and_logs(client, caplog):
    _register(_entry("mine"))
    await _own("mine", "owner")
    with caplog.at_level(logging.INFO, logger="ragstack.authz"):
        r = await client.delete("/v1/collections/mine?purge=true", headers=_h("admin"))
    assert r.status_code == 200, r.text
    bypass = [rec for rec in caplog.records if "admin-bypass" in rec.getMessage()]
    assert bypass, "admin bypass must be logged"
    assert any("mine" in rec.getMessage() for rec in bypass)


# --------------------------------------------------------------------------- #
# listing filters per caller
# --------------------------------------------------------------------------- #


async def test_list_filters_to_readable_collections(client):
    _register(_entry("owned"), _entry("private_to_owner"), _entry("open"))
    await _own("owned", "stranger")
    await _own("private_to_owner", "owner")
    await _own("open", "owner")
    await get_acl_store().grant(
        "open", GRANTEE_GROUP, PUBLIC_GROUP, PERM_READ, granted_by="owner"
    )
    body = (await client.get("/v1/collections", headers=_h("stranger"))).json()
    ids = {c["id"] for c in body["collections"]}
    # stranger sees: default (public), its own `owned`, and `open` (public) —
    # never `private_to_owner`.
    assert ids == {"default", "owned", "open"}


async def test_admin_list_sees_everything(client):
    _register(_entry("a"), _entry("b"))
    await _own("a", "owner")
    await _own("b", "stranger")
    body = (await client.get("/v1/collections", headers=_h("admin"))).json()
    assert {"default", "a", "b"} <= {c["id"] for c in body["collections"]}


# --------------------------------------------------------------------------- #
# fail closed: a store outage is a 503, never an allow
# --------------------------------------------------------------------------- #


async def test_store_failure_is_503_not_an_allow(client, monkeypatch):
    _register(_entry("priv"))
    await _own("priv", "owner")

    async def boom(_cid):
        raise ConnectionError("acl db down")

    monkeypatch.setattr(get_acl_store(), "owner_of", boom)
    r = await client.post(
        "/v1/retrieve", json={"query": "x", "collection": "priv"}, headers=_h("owner")
    )
    assert r.status_code == 503, r.text


# --------------------------------------------------------------------------- #
# create writes an owner row; backfill is idempotent
# --------------------------------------------------------------------------- #


async def test_create_writes_owner_row_and_is_private(client, monkeypatch):
    # Create is open to any principal; the creator becomes owner and the
    # collection is PRIVATE (no public grant), so a stranger can't read it. The
    # created entry carries a real (dead-port) Qdrant store, so we assert the
    # authorization outcomes — the owner row and the stranger's 404, both decided
    # BEFORE any store call — not a live read.
    r = await client.post("/v1/collections", json={"id": "fresh"}, headers=_h("owner"))
    assert r.status_code == 201, r.text
    assert await get_acl_store().owner_of("fresh") == "owner"
    # No public grant → private: a stranger is denied read (404, leak-safe).
    denied = await client.post(
        "/v1/retrieve", json={"query": "x", "collection": "fresh"}, headers=_h("stranger")
    )
    assert denied.status_code == 404


async def test_backfill_is_idempotent_across_two_startups():
    from ragstack.acl_store import InMemoryAclStore, set_acl_store
    from ragstack.api.access import backfill_collection_owners

    store = InMemoryAclStore()
    reg = CollectionRegistry([_entry("default", True), _entry("legacy")], default_id="default")
    set_acl_store(store)

    n1 = await backfill_collection_owners(reg, store, "legacy:admin")
    assert n1 == 2  # both collections had no owner
    owner1 = await store.owner_of("legacy")
    shares1 = await store.shares_for("legacy")

    # Second "startup": nothing new to do, and no duplicate rows / crash.
    n2 = await backfill_collection_owners(reg, store, "legacy:admin")
    assert n2 == 0
    assert await store.owner_of("legacy") == owner1 == "legacy:admin"
    assert len(await store.shares_for("legacy")) == len(shares1)
    # ...and it stayed world-readable via the public group.
    assert any(
        s.grantee_type == GRANTEE_GROUP and s.grantee_id == PUBLIC_GROUP and s.permission == PERM_READ
        for s in shares1
    )


async def test_backfill_repairs_a_lost_owner_row_privately():
    """A collection whose spec RECORDS a creator (post-ownership create) but has
    no owner row — the owner-row write was lost to a crash, or the memory ACL
    backend restarted — is repaired to its creator, NOT published. This is the
    fail-open the review found: absence-of-owner alone must never mean legacy."""
    from ragstack.acl_store import InMemoryAclStore, set_acl_store
    from ragstack.api.access import backfill_collection_owners

    store = InMemoryAclStore()
    set_acl_store(store)
    reg = CollectionRegistry(
        [_entry("default", True), _entry("was-private", owner="alice")],
        default_id="default",
    )
    await backfill_collection_owners(reg, store, "legacy:admin")
    assert await store.owner_of("was-private") == "alice"  # repaired, not claimed
    rows = await store.shares_for("was-private")
    assert [r.permission for r in rows] == [PERM_OWNER]  # and NO public grant


async def test_backfill_retries_a_missed_public_grant_but_never_resurrects():
    """The owner/public pair is not atomic: a crash after the owner grant used to
    leave the public row missing forever (idempotency keyed on owner presence).
    Now each row is keyed on its own history — the missing grant is retried on
    the next boot, while a DELIBERATELY revoked public row (un-publishing) stays
    revoked."""
    from ragstack.acl_store import GRANTEE_USER as GU
    from ragstack.acl_store import InMemoryAclStore, set_acl_store
    from ragstack.api.access import backfill_collection_owners

    store = InMemoryAclStore()
    set_acl_store(store)
    reg = CollectionRegistry([_entry("default", True), _entry("legacy")], default_id="default")
    # Simulate the crash: owner row landed, public row didn't.
    await store.grant("legacy", GU, "legacy:admin", PERM_OWNER, granted_by="system:backfill")
    n = await backfill_collection_owners(reg, store, "legacy:admin")
    assert n >= 1
    pub = [
        r for r in await store.shares_for("legacy")
        if r.grantee_type == GRANTEE_GROUP and r.grantee_id == PUBLIC_GROUP
    ]
    assert len(pub) == 1  # retried

    # Un-publish: the owner revokes the public row; the next boot must NOT
    # resurrect it.
    await store.revoke(pub[0].id, revoked_by="legacy:admin")
    n2 = await backfill_collection_owners(reg, store, "legacy:admin")
    assert n2 == 0
    assert [
        r for r in await store.shares_for("legacy")
        if r.grantee_type == GRANTEE_GROUP and r.grantee_id == PUBLIC_GROUP
    ] == []


async def test_backfill_never_publishes_a_real_owner_whose_spec_owner_was_lost():
    """Defence in depth (independent review finding #2): a collection with a REAL
    active owner but a blank spec.owner — a future backend that dropped the field,
    or a hand-edit — must be treated as owned, never republished world-readable.
    The legacy branch is gated on the active owner being the backfill owner (or
    none), not merely on owner-row absence."""
    from ragstack.acl_store import GRANTEE_USER as GU
    from ragstack.acl_store import InMemoryAclStore, set_acl_store
    from ragstack.api.access import backfill_collection_owners

    store = InMemoryAclStore()
    set_acl_store(store)
    # Blank spec.owner (legacy-looking) but bob genuinely owns it.
    reg = CollectionRegistry([_entry("default", True), _entry("bobs")], default_id="default")
    await store.grant("bobs", GU, "bvbrc:bob", PERM_OWNER, granted_by="bvbrc:bob")
    await backfill_collection_owners(reg, store, "legacy:admin")
    assert await store.owner_of("bobs") == "bvbrc:bob"  # untouched
    assert [
        r for r in await store.shares_for("bobs")
        if r.grantee_type == GRANTEE_GROUP and r.grantee_id == PUBLIC_GROUP
    ] == []  # NOT published
