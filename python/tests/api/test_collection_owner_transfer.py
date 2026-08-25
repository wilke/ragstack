"""Ownership transfer (issue #280).

``POST /v1/collections/{id}/owner`` is the flow the shares endpoint refuses
``permission: owner`` in favour of. Current-owner-or-admin through the ONE
authorization seam, resolving the new owner with the SAME ``_resolve_grantee``
rules as a share grantee, and going through ``AclStore.transfer_owner`` — the
one place that does the revoke+grant pair inside a single transaction, which is
what the ``shares_active_owner`` partial unique index requires.

What is actually asserted here, beyond the happy path: the outgoing owner's row
is soft-revoked and still present (ADR-0004 decision 6 — the audit trail is the
point), exactly ONE active owner row survives, a group can never own a
collection, a self-transfer is a clean 409 rather than audit-trail churn, and a
store outage fails closed with a 503.

Fixture shape is borrowed from ``test_collection_shares``: per-tenant API keys
(owner / stranger / admin), the in-memory ACL singleton the conftest installs,
and a registry rebuilt per test. Note that the test tenants are COLON-FREE
subjects, so a transfer *to* one of them uses the ``@service:`` form — a bare
name would be qualified to ``bvbrc:<name>`` (the same trap the share tests
document).
Issue #290 added a gate this file's fixtures must satisfy: a NON-ADMIN
transfer to a subject who has never signed in (and is not a registered
service account) is now refused with 422, so a never-seen recipient's owned
count can't be used to evade the per-owner quota. ``owner``/``stranger``/
``admin`` are the three subjects real transfers move things between in this
file, so an autouse fixture marks them as having signed in — the tests about
the gate itself (below, "never-seen subject") use a distinct, deliberately
unregistered subject instead.
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
from tests.api.conftest import SHARED_ID

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


@pytest.fixture(autouse=True)
async def _signed_in():
    """Issue #290's never-seen-recipient gate: mark the three test tenants as
    having signed in so it doesn't interfere with tests about OTHER transfer
    behavior. Safe to run after conftest.py's autouse ``_acl_store`` (a parent
    conftest's autouse fixtures instantiate before the test module's own),
    which is what installs the store ``get_acl_store()`` returns here."""
    store = get_acl_store()
    for who in KEYS:
        await store.upsert_seen(who, "bvbrc")


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
    await get_acl_store().grant(cid, GRANTEE_USER, subject, PERM_OWNER, granted_by=subject)


async def _active_owner_rows(cid: str) -> list:
    return [
        s for s in await get_acl_store().shares_for(cid) if s.permission == PERM_OWNER
    ]


# --------------------------------------------------------------------------- #
# happy path — who may transfer
# --------------------------------------------------------------------------- #


async def test_owner_transfers_and_the_new_owner_takes_over(client):
    """The end-to-end handover: the current owner transfers, the store's owner
    changes, the NEW owner can exercise owner-gated endpoints, and the outgoing
    owner is left with nothing (its 404 is the leak-safe unreadable answer)."""
    _register(_entry("priv"))
    await _own("priv", "owner")

    r = await client.post(
        "/v1/collections/priv/owner",
        json={"subject": "@service:stranger"},  # colon-free: the tenant itself
        headers=_h("owner"),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["collection_id"] == "priv"
    assert body["owner"] == "stranger"  # echoed resolution, NOT 'bvbrc:stranger'
    assert body["previous_owner"] == "owner"
    assert body["share"]["permission"] == PERM_OWNER
    assert body["share"]["grantee_id"] == "stranger"
    assert body["share"]["granted_by"] == "owner"  # the actor, audited
    assert body["share"]["active"] is True

    assert await get_acl_store().owner_of("priv") == "stranger"

    # The new owner may now do owner-gated things (listing shares is one).
    ok = await client.get("/v1/collections/priv/shares", headers=_h("stranger"))
    assert ok.status_code == 200, ok.text
    assert ok.json()["owner"] == "stranger"

    # …and the outgoing owner can no longer even read it → 404, not 403.
    gone = await client.get("/v1/collections/priv/shares", headers=_h("owner"))
    assert gone.status_code == 404, gone.text
    denied = await client.post(
        "/v1/retrieve", json={"query": "x", "collection": "priv"}, headers=_h("owner")
    )
    assert denied.status_code == 404, denied.text


async def test_unrelated_reader_cannot_transfer_403(client):
    """A caller who can READ (public grant) but does not own it gets 403 — the
    honest answer, since existence already leaked via the public grant."""
    _register(_entry("open"))
    await _own("open", "owner")
    await get_acl_store().grant(
        "open", GRANTEE_GROUP, PUBLIC_GROUP, PERM_READ, granted_by="owner"
    )
    r = await client.post(
        "/v1/collections/open/owner",
        json={"subject": "@service:stranger"},
        headers=_h("stranger"),
    )
    assert r.status_code == 403, r.text
    assert await get_acl_store().owner_of("open") == "owner"


async def test_non_owner_of_a_private_collection_is_404(client):
    """Unreadable == unknown: a stranger must not learn 'priv' exists."""
    _register(_entry("priv"))
    await _own("priv", "owner")
    r = await client.post(
        "/v1/collections/priv/owner",
        json={"subject": "@service:stranger"},
        headers=_h("stranger"),
    )
    assert r.status_code == 404, r.text
    assert await get_acl_store().owner_of("priv") == "owner"


async def test_admin_can_transfer_via_the_bypass(client):
    """Admin owns nothing here; the logged admin bypass in resolve_access is what
    admits it (support/migration path, ADR-0003 decision 5)."""
    _register(_entry("priv"))
    await _own("priv", "owner")
    r = await client.post(
        "/v1/collections/priv/owner",
        json={"subject": "@service:stranger"},
        headers=_h("admin"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["share"]["granted_by"] == "admin"  # the admin is the grantor
    assert await get_acl_store().owner_of("priv") == "stranger"


async def test_transfer_of_an_unknown_collection_is_404(client):
    _register(_entry("priv"))
    r = await client.post(
        "/v1/collections/nope/owner", json={"subject": "bob"}, headers=_h("owner")
    )
    assert r.status_code == 404, r.text


# --------------------------------------------------------------------------- #
# the audit trail: soft revoke, and exactly one active owner
# --------------------------------------------------------------------------- #


async def test_outgoing_owner_row_is_soft_revoked_never_deleted(client):
    """ADR-0004 decision 6: the row that recorded the old ownership must survive
    with revoked_at/revoked_by set — a handover that left no trace would be
    indistinguishable from one that never happened."""
    _register(_entry("priv"))
    await _own("priv", "owner")
    before = await _active_owner_rows("priv")
    assert len(before) == 1
    old_row_id = before[0].id

    r = await client.post(
        "/v1/collections/priv/owner",
        json={"subject": "@service:stranger"},
        headers=_h("owner"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["revoked_share_id"] == old_row_id  # the response points at it

    store = get_acl_store()
    # Not in the ACTIVE set…
    assert all(s.id != old_row_id for s in await store.shares_for("priv"))
    # …but still there, stamped, in the history.
    history = await store.shares_for("priv", include_revoked=True)
    old = next((s for s in history if s.id == old_row_id), None)
    assert old is not None, "the outgoing owner row was DELETED, not soft-revoked"
    assert old.grantee_id == "owner"
    assert old.permission == PERM_OWNER
    assert old.revoked_at != ""
    assert old.revoked_by == "owner"  # the actor who performed the transfer
    assert old.active is False

    # And it is visible through the API's own audit view.
    hist = await client.get(
        "/v1/collections/priv/shares",
        params={"include_revoked": "true"},
        headers=_h("stranger"),  # the new owner
    )
    assert hist.status_code == 200, hist.text
    assert any(
        s["id"] == old_row_id and s["active"] is False for s in hist.json()["shares"]
    )


async def test_exactly_one_active_owner_after_a_chain_of_transfers(client):
    """The ``shares_active_owner`` partial unique index permits ONE active owner
    row per collection. Assert the invariant directly, and after a second hop —
    a revoke+grant pair that left both rows active would violate it (and a
    backend without the index would only show up here)."""
    _register(_entry("priv"))
    await _own("priv", "owner")

    first = await client.post(
        "/v1/collections/priv/owner",
        json={"subject": "@service:stranger"},
        headers=_h("owner"),
    )
    assert first.status_code == 200, first.text
    rows = await _active_owner_rows("priv")
    assert len(rows) == 1 and rows[0].grantee_id == "stranger"

    second = await client.post(
        "/v1/collections/priv/owner",
        json={"subject": "@service:admin"},
        headers=_h("stranger"),
    )
    assert second.status_code == 200, second.text
    rows = await _active_owner_rows("priv")
    assert len(rows) == 1 and rows[0].grantee_id == "admin"

    # Both handovers are in the history; nothing was deleted.
    history = await get_acl_store().shares_for("priv", include_revoked=True)
    owner_rows = [s for s in history if s.permission == PERM_OWNER]
    assert len(owner_rows) == 3
    assert sum(1 for s in owner_rows if s.active) == 1


async def test_other_shares_survive_the_handover(client):
    """Transfer is deliberately NON-cascading (ADR-0004): handing a collection
    over must not destroy its share graph, even though the grantor's own owner
    row is being revoked."""
    _register(_entry("priv"))
    await _own("priv", "owner")
    g = await client.post(
        "/v1/collections/priv/shares", json={"grantee": "carol"}, headers=_h("owner")
    )
    assert g.status_code == 201, g.text
    share_id = g.json()["id"]

    r = await client.post(
        "/v1/collections/priv/owner",
        json={"subject": "@service:stranger"},
        headers=_h("owner"),
    )
    assert r.status_code == 200, r.text
    active = await get_acl_store().shares_for("priv")
    assert any(s.id == share_id and s.active for s in active)


# --------------------------------------------------------------------------- #
# the outgoing owner's fate is reported, not implied
# --------------------------------------------------------------------------- #


async def test_outgoing_owner_retains_read_is_false_when_access_is_gone(client):
    _register(_entry("priv"))
    await _own("priv", "owner")
    r = await client.post(
        "/v1/collections/priv/owner",
        json={"subject": "@service:stranger"},
        headers=_h("owner"),
    )
    assert r.status_code == 200, r.text
    # No consolation read grant is minted — the outgoing owner is out.
    assert r.json()["previous_owner_retains_read"] is False
    assert all(
        s.grantee_id != "owner"
        for s in await get_acl_store().shares_for("priv")
    )


async def test_outgoing_owner_retains_read_reports_a_surviving_public_grant(client):
    """The residual-access answer comes from the SEAM, not from this endpoint's
    own reasoning — so a `public` grant (or a group membership, or an independent
    share) that keeps the old owner reading is reported honestly rather than
    silently contradicting a bare 'they lost access'."""
    _register(_entry("open"))
    await _own("open", "owner")
    await get_acl_store().grant(
        "open", GRANTEE_GROUP, PUBLIC_GROUP, PERM_READ, granted_by="owner"
    )
    r = await client.post(
        "/v1/collections/open/owner",
        json={"subject": "@service:stranger"},
        headers=_h("owner"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["previous_owner_retains_read"] is True


# --------------------------------------------------------------------------- #
# refusals
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("subject", ["@public", "public", "@group:team", "group:team"])
async def test_transfer_to_a_group_is_400(client, subject):
    """``_check_grant`` enforces 'owner is grantable to users only, never to a
    group' inside the store; the endpoint must surface that as a clean 400 rather
    than letting a ShareInvariantError become a 500 — and must not have moved
    ownership."""
    _register(_entry("priv"))
    await _own("priv", "owner")
    r = await client.post(
        "/v1/collections/priv/owner", json={"subject": subject}, headers=_h("owner")
    )
    assert r.status_code == 400, r.text
    assert "group" in r.text.lower()
    assert await get_acl_store().owner_of("priv") == "owner"
    rows = await _active_owner_rows("priv")
    assert len(rows) == 1 and rows[0].grantee_id == "owner"


async def test_transfer_to_the_current_owner_is_409(client):
    """A self-transfer is refused, not silently accepted: ``transfer_owner``
    would happily revoke and re-insert an identical owner row, churning the audit
    trail with a handover that never happened. Mirrors the share endpoint's own
    'already owns' 409. 'priv2' is owned by 'bvbrc:bob'; the bare-username form
    resolves to that same subject."""
    _register(_entry("priv2"))
    await _own("priv2", "bvbrc:bob")
    before = (await _active_owner_rows("priv2"))[0]

    r = await client.post(
        "/v1/collections/priv2/owner",
        json={"subject": "bob"},  # → 'bvbrc:bob' == the current owner
        headers=_h("admin"),  # admin: the owner subject is not one of our keys
    )
    assert r.status_code == 409, r.text
    assert "owns" in r.text.lower()
    # The row is untouched — same id, still active (no churn).
    after = await _active_owner_rows("priv2")
    assert len(after) == 1
    assert after[0].id == before.id
    assert after[0].granted_at == before.granted_at


async def test_transfer_of_an_ownerless_collection_is_409(client):
    """Only an admin can reach this (a non-admin cannot pass the owner gate on a
    collection with no owner row). Claiming it here would turn the transfer
    endpoint into a *claim* endpoint; the startup backfill is what repairs a lost
    owner row, from the spec-recorded creator."""
    _register(_entry("orphan"))
    assert await get_acl_store().owner_of("orphan") is None
    r = await client.post(
        "/v1/collections/orphan/owner",
        json={"subject": "@service:stranger"},
        headers=_h("admin"),
    )
    assert r.status_code == 409, r.text
    assert "no active owner" in r.text.lower()
    assert await get_acl_store().owner_of("orphan") is None


@pytest.mark.parametrize(
    "subject", ["   ", ":", "bvbrc:", ":alice", "@service:", "@service:bvbrc:alice"]
)
async def test_malformed_subject_is_422_and_ownership_is_untouched(client, subject):
    """The same ``_resolve_grantee`` refusals as a share grantee — an unclaimable
    owner is far worse than an unclaimable read grant (it strands the
    collection), so these must never reach the store."""
    _register(_entry("priv"))
    await _own("priv", "owner")
    r = await client.post(
        "/v1/collections/priv/owner", json={"subject": subject}, headers=_h("owner")
    )
    assert r.status_code == 422, r.text
    assert await get_acl_store().owner_of("priv") == "owner"


async def test_reserved_service_subject_cannot_become_owner(client):
    """'@service:default' is the fallback tenant every unmapped API key resolves
    to — handing a collection to it would hand it to every such caller."""
    _register(_entry("priv"))
    await _own("priv", "owner")
    r = await client.post(
        "/v1/collections/priv/owner",
        json={"subject": "@service:default"},
        headers=_h("owner"),
    )
    assert r.status_code == 422, r.text
    assert await get_acl_store().owner_of("priv") == "owner"


async def test_unknown_body_field_is_rejected(client):
    _register(_entry("priv"))
    await _own("priv", "owner")
    r = await client.post(
        "/v1/collections/priv/owner",
        json={"subject": "bob", "permission": "owner"},
        headers=_h("owner"),
    )
    assert r.status_code == 422, r.text


# --------------------------------------------------------------------------- #
# never-seen subject, and fail-closed
# --------------------------------------------------------------------------- #


async def test_non_admin_transfer_to_a_never_seen_subject_is_422(client):
    """Issue #290: unlike a share grantee, an OWNER transfer now requires the
    recipient to have signed in at least once (or be a registered service
    account) when the actor is non-admin — a never-seen recipient always owns
    0 collections, which would otherwise make the per-owner quota fully
    evadable (create at the limit, transfer to a fresh ghost, create again,
    ...). Refused BEFORE any row is minted: the ghost must not appear in the
    store afterward."""
    _register(_entry("lib"))
    await _own("lib", "owner")
    r = await client.post(
        "/v1/collections/lib/owner",
        json={"subject": "ghost@patricbrc.org"},
        headers=_h("owner"),  # non-admin
    )
    assert r.status_code == 422, r.text
    assert "signed in" in r.text.lower()
    assert await get_acl_store().owner_of("lib") == "owner"  # untouched
    assert await get_acl_store().get("bvbrc:ghost@patricbrc.org") is None  # never minted


async def test_admin_transfer_to_a_never_seen_subject_is_allowed_and_logs(client, caplog):
    """Admin actor is exempt from the gate (logged): offboarding a collection
    to a successor who has not signed in yet is a legitimate admin action. The
    row is still pre-provisioned and the resolved subject echoed back, exactly
    as the old (now non-admin-only) behavior did."""
    _register(_entry("lib"))
    await _own("lib", "owner")
    with caplog.at_level(logging.INFO):
        r = await client.post(
            "/v1/collections/lib/owner",
            json={"subject": "successor@patricbrc.org"},
            headers=_h("admin"),
        )
    assert r.status_code == 200, r.text
    assert r.json()["owner"] == "bvbrc:successor@patricbrc.org"
    assert any(
        "never-seen recipient" in rec.message for rec in caplog.records
    ), [rec.message for rec in caplog.records]

    store = get_acl_store()
    assert await store.get("bvbrc:successor@patricbrc.org") is not None
    from ragstack.authz import resolve_access

    decision = await resolve_access(
        "bvbrc:successor@patricbrc.org", ROLE_USER, "lib", "owner", store
    )
    assert decision.allowed is True


async def test_transfer_store_outage_is_503(client, monkeypatch):
    """Fail closed like the rest of the file. ``owner_of`` answers the
    enforce_access gate, so breaking ``shares_for`` lets the gate pass and fails
    the transfer's own read."""
    _register(_entry("priv"))
    await _own("priv", "owner")

    async def boom(*_a, **_k):
        raise ConnectionError("acl db down")

    monkeypatch.setattr(get_acl_store(), "shares_for", boom)
    r = await client.post(
        "/v1/collections/priv/owner",
        json={"subject": "@service:stranger"},
        headers=_h("owner"),
    )
    assert r.status_code == 503, r.text


async def test_transfer_gate_outage_is_503(client, monkeypatch):
    """And a store that cannot answer the OWNER gate itself never reaches the
    transfer at all."""
    _register(_entry("priv"))
    await _own("priv", "owner")

    async def boom(*_a, **_k):
        raise ConnectionError("acl db down")

    monkeypatch.setattr(get_acl_store(), "owner_of", boom)
    r = await client.post(
        "/v1/collections/priv/owner",
        json={"subject": "@service:stranger"},
        headers=_h("owner"),
    )
    assert r.status_code == 503, r.text


async def test_transfer_itself_failing_is_503_and_changes_nothing(client, monkeypatch):
    _register(_entry("priv"))
    await _own("priv", "owner")

    async def boom(*_a, **_k):
        raise ConnectionError("acl db down")

    monkeypatch.setattr(get_acl_store(), "transfer_owner", boom)
    r = await client.post(
        "/v1/collections/priv/owner",
        json={"subject": "@service:stranger"},
        headers=_h("owner"),
    )
    assert r.status_code == 503, r.text
    assert await get_acl_store().owner_of("priv") == "owner"


# --------------------------------------------------------------------------- #
# the pointer the shares endpoint hands out actually resolves
# --------------------------------------------------------------------------- #


async def test_share_endpoint_points_at_this_endpoint_for_owner(client):
    """The 400 that motivated #280 now names a route that exists."""
    _register(_entry("priv"))
    await _own("priv", "owner")
    r = await client.post(
        "/v1/collections/priv/shares",
        json={"grantee": "bob", "permission": "owner"},
        headers=_h("owner"),
    )
    assert r.status_code == 400, r.text
    assert "/v1/collections/priv/owner" in r.text
    routes = {getattr(rt, "path", "") for rt in app.routes}
    assert "/v1/collections/{collection_id}/owner" in routes


# --------------------------------------------------------------------------- #
# the revocation cascade (regression for the CRITICAL found in review)
# --------------------------------------------------------------------------- #
#
# Transfer created the first owner rows a cascade could REACH. Every owner row
# used to be a self-grant (`write_owner_row`) or backfilled by a non-grantee, and
# `_revocation_plan` treats both as intrinsically grounded. `transfer_owner`
# mints the new owner row with `granted_by=<actor>` — so once the new owner had
# granted anything, revoking THAT share cascaded into the owner row and left the
# collection with ZERO active owners.
#
# That state is terminal: it cannot be managed, cannot be transferred (this route
# 409s on a missing owner), and is NOT repaired by the startup backfill, which
# skips any collection whose history ever held an owner row.


async def test_revoking_an_unrelated_share_does_not_orphan_the_collection(client):
    """The new owner grants a read to a third party, then revokes it. Ownership
    must be untouched — it is not part of that grant's support chain."""
    _register(_entry("priv"))
    await _own("priv", "owner")
    r = await client.post(
        "/v1/collections/priv/owner",
        json={"subject": "@service:stranger"},
        headers=_h("owner"),
    )
    assert r.status_code == 200, r.text

    g = await client.post(
        "/v1/collections/priv/shares", json={"grantee": "carol"}, headers=_h("stranger")
    )
    assert g.status_code == 201, g.text
    d = await client.delete(
        f"/v1/collections/priv/shares/{g.json()['id']}", headers=_h("stranger")
    )
    assert d.status_code == 204, d.text

    owners = await _active_owner_rows("priv")
    assert len(owners) == 1, f"COLLECTION ORPHANED: active owner rows={owners}"
    assert owners[0].grantee_id == "stranger"
    assert await get_acl_store().owner_of("priv") == "stranger"


async def test_revoking_the_re_granted_previous_owner_keeps_ownership(client):
    """The documented recovery path: after a handover the previous owner is
    re-granted read, and later that read is revoked. Ownership must survive —
    this is the exact sequence the endpoint's own docstring recommends."""
    _register(_entry("priv"))
    await _own("priv", "owner")
    assert (
        await client.post(
            "/v1/collections/priv/owner",
            json={"subject": "@service:stranger"},
            headers=_h("owner"),
        )
    ).status_code == 200

    g = await client.post(
        "/v1/collections/priv/shares", json={"grantee": "owner"}, headers=_h("stranger")
    )
    assert g.status_code == 201, g.text
    assert (
        await client.delete(
            f"/v1/collections/priv/shares/{g.json()['id']}", headers=_h("stranger")
        )
    ).status_code == 204

    owners = await _active_owner_rows("priv")
    assert len(owners) == 1, f"COLLECTION ORPHANED: active owner rows={owners}"
    assert await get_acl_store().owner_of("priv") == "stranger"


async def test_a_mutual_grant_cycle_is_still_revoked(client):
    """The cascade must still do its job: the owner-row carve-out must not
    become a way to pre-arrange grants that outlive revocation. Only the OWNER
    row is intrinsically grounded; an ordinary mutual-grant cycle is not."""
    _register(_entry("priv"))
    await _own("priv", "owner")
    a = await client.post(
        "/v1/collections/priv/shares", json={"grantee": "carol"}, headers=_h("owner")
    )
    assert a.status_code == 201, a.text

    d = await client.delete(
        f"/v1/collections/priv/shares/{a.json()['id']}", headers=_h("owner")
    )
    assert d.status_code == 204
    active = await get_acl_store().shares_for("priv")
    assert not any(s.grantee_id == "carol" and s.active for s in active)
    # ...and the owner is of course still there.
    assert len(await _active_owner_rows("priv")) == 1
