"""The per-owner collection quota over HTTP (issue #290).

``MAX_COLLECTIONS_PER_OWNER`` is enforced on ACQUISITION at both points an
owner row is minted: ``POST /v1/collections`` (create) and
``POST /v1/collections/{id}/owner`` (transfer). Both answer 409 with a
structured ``{owned, limit}`` detail. Admin is exempt from this quota (a
logged branch) — distinct from ``MAX_COLLECTIONS``, which stays admin-inclusive
physical protection (ADR-0005 decision 5) and is untouched by this issue.

Two properties specific to transfer, both HIGH findings from review:

- The exemption is the RECIPIENT's admin-ness, not the acting principal's — an
  admin-initiated transfer to a full NON-admin colleague still 409s (the
  poisoning case, reachable by admins if this were keyed on the actor); it
  only bypasses when the recipient itself is admin.
- A transfer to a subject who has never signed in (and is not a registered
  service account) is refused with 422 for a non-admin actor, before any row
  is minted for the ghost — a never-seen recipient's owned count is always 0,
  which otherwise makes the quota fully evadable (create at the limit,
  transfer to a fresh ghost, create again, ...). Admin actor is exempt
  (logged): offboarding to a successor who hasn't signed in yet is legitimate.

The quota is monkeypatched down to 2 throughout so each test stays fast (no
need to actually create 5+ collections against the dead-port Qdrant double).
"""
from __future__ import annotations

import logging

import pytest

from ragstack.acl_store import get_acl_store
from ragstack.api import security
from ragstack.api.collections import CollectionEntry, CollectionRegistry
from ragstack.api.main import app
from ragstack.api.security import ROLE_ADMIN, ROLE_USER
from ragstack.config import settings
from tests.api.conftest import SHARED_ID

pytestmark = pytest.mark.asyncio

KEYS = {"alice": "k-alice", "bob": "k-bob", "admin": "k-admin"}


@pytest.fixture(autouse=True)
def _principals(monkeypatch):
    monkeypatch.setattr(security.settings, "api_keys", list(KEYS.values()))
    monkeypatch.setattr(
        security.settings,
        "api_key_tenants",
        {"k-alice": "alice", "k-bob": "bob", "k-admin": "admin"},
    )
    monkeypatch.setattr(security.settings, "api_key_roles", {"k-admin": ROLE_ADMIN})
    monkeypatch.setattr(security.settings, "default_role", ROLE_USER)


@pytest.fixture(autouse=True)
def _quota_of_2(monkeypatch):
    monkeypatch.setattr(settings, "max_collections_per_owner", 2)


def _h(who: str) -> dict:
    return {"X-API-Key": KEYS[who]}


async def _create(client, who: str, cid: str):
    return await client.post("/v1/collections", json={"id": cid}, headers=_h(who))


# --------------------------------------------------------------------------- #
# create
# --------------------------------------------------------------------------- #


async def test_create_at_the_quota_is_409_with_the_numbers(client):
    ok1 = await _create(client, "alice", "alice-1")
    ok2 = await _create(client, "alice", "alice-2")
    assert ok1.status_code == 201, ok1.text
    assert ok2.status_code == 201, ok2.text

    over = await _create(client, "alice", "alice-3")
    assert over.status_code == 409, over.text
    detail = over.json()["detail"]
    assert detail["owned"] == 2 and detail["limit"] == 2
    assert detail["error"] == "owner_quota_exceeded"
    # Refused, not partially created.
    assert await get_acl_store().owner_of("alice-3") is None


async def test_create_under_the_quota_is_unaffected(client):
    ok = await _create(client, "alice", "alice-1")
    assert ok.status_code == 201, ok.text
    assert await get_acl_store().owner_of("alice-1") == "alice"


async def test_admin_create_bypasses_the_quota_and_logs(client, caplog):
    for i in range(2):
        r = await _create(client, "admin", f"admin-{i}")
        assert r.status_code == 201, r.text
    with caplog.at_level(logging.INFO, logger="ragstack.api.access"):
        over = await _create(client, "admin", "admin-over")
    assert over.status_code == 201, over.text
    assert await get_acl_store().owner_of("admin-over") == "admin"
    assert any(
        "owner-quota admin-bypass" in r.message for r in caplog.records
    ), [r.message for r in caplog.records]


async def test_different_owners_have_independent_quotas(client):
    for i in range(2):
        assert (await _create(client, "alice", f"alice-{i}")).status_code == 201
    # Bob is untouched by Alice's quota.
    r = await _create(client, "bob", "bob-1")
    assert r.status_code == 201, r.text


# --------------------------------------------------------------------------- #
# transfer
# --------------------------------------------------------------------------- #


def _entry(cid: str) -> CollectionEntry:
    from tests.api.conftest import _StateRetriever

    return CollectionEntry(
        id=cid, label=cid, collection=cid, model="test-model", dim=4,
        chunk_method="fixed", chunk_size=None, chunk_overlap=None, chunk_params={},
        is_shared_surface=False, retriever=_StateRetriever(),
        vector_store=app.state.vector_store, text_index=app.state.text_index,
    )


def _register(*cids: str) -> None:
    """Add registry entries for direct-owned (non-HTTP-created) collections —
    the transfer endpoint resolves against the registry, not the ACL store
    alone. Rebuilds over whatever the ``client`` fixture (or an earlier call
    here) already registered, so entries accumulate rather than replace."""
    app.state.collections = CollectionRegistry(
        [*app.state.collections.entries(), *(_entry(c) for c in cids)],
        default_id=SHARED_ID,
    )


async def _own_directly(cid: str, subject: str) -> None:
    """Register the collection and seed an owner row without going through the
    create endpoint's own quota check — lets a test put a subject exactly AT
    the limit cheaply. Also marks ``subject`` as having signed in
    (``upsert_seen``): the registration step the never-seen-recipient gate
    (issue #290 HIGH finding 1) requires before it will let a non-admin
    transfer name that subject. These tests are about the QUOTA, not the gate
    — a ghost 422 would otherwise mask what they mean to exercise."""
    from ragstack.acl_store import GRANTEE_USER, PERM_OWNER

    _register(cid)
    store = get_acl_store()
    await store.upsert_seen(subject, "bvbrc")
    await store.grant(cid, GRANTEE_USER, subject, PERM_OWNER, granted_by=subject)


async def test_transfer_to_a_full_grantee_is_409_and_source_keeps_ownership(client):
    await _own_directly("mine", "alice")
    await _own_directly("bob-1", "bob")
    await _own_directly("bob-2", "bob")

    r = await client.post(
        "/v1/collections/mine/owner", json={"subject": "@service:bob"}, headers=_h("alice")
    )
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["owned"] == 2 and detail["limit"] == 2
    assert detail["error"] == "owner_quota_exceeded"
    assert await get_acl_store().owner_of("mine") == "alice"  # unchanged


async def test_transfer_to_a_grantee_under_the_quota_succeeds(client):
    await _own_directly("mine", "alice")
    await _own_directly("bob-1", "bob")

    r = await client.post(
        "/v1/collections/mine/owner", json={"subject": "@service:bob"}, headers=_h("alice")
    )
    assert r.status_code == 200, r.text
    assert await get_acl_store().owner_of("mine") == "bob"


async def test_admin_transfer_to_a_full_non_admin_recipient_is_409(client, caplog):
    """Issue #290 HIGH finding 2: the exemption is the RECIPIENT's admin-ness,
    not the acting principal's. An admin actor transferring to a full
    NON-admin colleague must not be a backdoor around that colleague's own
    quota (the poisoning case, reachable by admins if this were keyed on the
    actor instead) — the transfer still 409s, and the actor's admin-ness is
    only noted in the log, not honoured as a bypass."""
    await _own_directly("mine", "alice")
    await _own_directly("bob-1", "bob")
    await _own_directly("bob-2", "bob")  # bob is already at the limit

    with caplog.at_level(logging.INFO):
        r = await client.post(
            "/v1/collections/mine/owner", json={"subject": "@service:bob"}, headers=_h("admin")
        )
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["owned"] == 2 and detail["limit"] == 2
    assert detail["error"] == "owner_quota_exceeded"
    assert await get_acl_store().owner_of("mine") == "alice"  # unchanged
    assert any(
        "admin-actor" in r.message and "quota still applies" in r.message
        for r in caplog.records
    ), [r.message for r in caplog.records]


async def test_admin_transfer_to_a_full_admin_recipient_is_allowed_and_logs(client, caplog):
    """The mirror image: the exemption DOES fire when the RECIPIENT is admin —
    here, the built-in admin API-key tenant — even though it is already at
    (in fact over) the limit."""
    await _own_directly("mine", "alice")
    await _own_directly("admin-1", "admin")
    await _own_directly("admin-2", "admin")  # already at the limit

    with caplog.at_level(logging.INFO):
        r = await client.post(
            "/v1/collections/mine/owner", json={"subject": "@service:admin"}, headers=_h("admin")
        )
    assert r.status_code == 200, r.text
    assert await get_acl_store().owner_of("mine") == "admin"
    assert any(
        "owner-quota admin-bypass" in r.message and "recipient is admin" in r.message
        for r in caplog.records
    ), [r.message for r in caplog.records]


# --------------------------------------------------------------------------- #
# the evasion sequence, end to end over HTTP
# --------------------------------------------------------------------------- #


async def test_the_evasion_sequence_is_blocked_over_http(client):
    """create at the limit -> transfer one away to a colluder who is ALSO
    already full -> create again. The transfer 409s (the colluder's own
    quota), alice's count never drops, and the follow-up create stays 409."""
    for i in range(2):
        assert (await _create(client, "alice", f"alice-{i}")).status_code == 201
    await _own_directly("bob-1", "bob")
    await _own_directly("bob-2", "bob")

    transfer = await client.post(
        "/v1/collections/alice-0/owner", json={"subject": "@service:bob"}, headers=_h("alice")
    )
    assert transfer.status_code == 409, transfer.text
    assert await get_acl_store().owner_of("alice-0") == "alice"

    again = await _create(client, "alice", "alice-extra")
    assert again.status_code == 409, again.text


# --------------------------------------------------------------------------- #
# the never-seen-recipient gate (issue #290 HIGH finding 1) — the issue body's
# own evasion case: a never-seen subject always owns 0, so unless transferring
# to one is refused outright, the quota is fully evadable regardless of what
# the quota check itself does.
# --------------------------------------------------------------------------- #


async def test_non_admin_transfer_to_a_never_seen_recipient_is_422(client):
    await _own_directly("mine", "alice")
    r = await client.post(
        "/v1/collections/mine/owner",
        json={"subject": "ghost@patricbrc.org"},
        headers=_h("alice"),
    )
    assert r.status_code == 422, r.text
    assert await get_acl_store().owner_of("mine") == "alice"  # unchanged
    # Refused BEFORE ensure_provisional — the ghost never gets a row at all.
    assert await get_acl_store().get("bvbrc:ghost@patricbrc.org") is None


async def test_the_never_seen_evasion_is_blocked_before_the_quota_even_applies(client):
    """The issue's own case: create at the limit, transfer to a FRESH ghost
    (never anyone's quota problem), create again. Blocked at the very first
    transfer attempt — a never-seen recipient can never receive a collection
    from a non-admin, so the sequence never gets to move alice's count at
    all, and the follow-up create stays refused."""
    for i in range(2):
        assert (await _create(client, "alice", f"alice-{i}")).status_code == 201

    transfer = await client.post(
        "/v1/collections/alice-0/owner",
        json={"subject": "ghost-0@patricbrc.org"},
        headers=_h("alice"),
    )
    assert transfer.status_code == 422, transfer.text
    assert await get_acl_store().owner_of("alice-0") == "alice"

    again = await _create(client, "alice", "alice-extra")
    assert again.status_code == 409, again.text


async def test_admin_transfer_to_a_never_seen_recipient_is_allowed_and_logs(client, caplog):
    """Admin actor is exempt from the gate (logged): offboarding a collection
    to a successor who hasn't signed in yet is legitimate. The row is still
    pre-provisioned so the new owner is reachable once they do sign in."""
    await _own_directly("mine", "alice")
    with caplog.at_level(logging.INFO):
        r = await client.post(
            "/v1/collections/mine/owner",
            json={"subject": "successor@patricbrc.org"},
            headers=_h("admin"),
        )
    assert r.status_code == 200, r.text
    assert await get_acl_store().owner_of("mine") == "bvbrc:successor@patricbrc.org"
    assert await get_acl_store().get("bvbrc:successor@patricbrc.org") is not None
    assert any(
        "never-seen recipient" in r.message for r in caplog.records
    ), [r.message for r in caplog.records]
