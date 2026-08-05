"""The one authorization decision seam (``ragstack.authz``, issue #243).

Matrix over role x action x grant shape: admin bypasses everything (logged on
EVERY bypass — the audit trail must count and time-order repeated access),
owners get every action, read follows any active grant (direct or via the
built-in ``public`` group), write/owner stay owner-only for the MVP, and a
failing store DENIES by raising ``AuthzUnavailable`` — fail closed, never
open. The batch read resolver (``resolve_read_many``) must agree with the
per-collection decision while touching the store exactly once.
"""
from __future__ import annotations

import logging

import pytest

from ragstack.acl_store import InMemoryAclStore
from ragstack.authz import (
    AccessDecision,
    AuthzUnavailable,
    resolve_access,
    resolve_read_many,
)

pytestmark = pytest.mark.asyncio

COLL = "col-abc123"
OWNER = "bvbrc:owner@patricbrc.org"
ALICE = "bvbrc:alice@patricbrc.org"
STRANGER = "google:stranger-sub"

ACTIONS = ["read", "write", "owner"]


@pytest.fixture
async def store():
    s = InMemoryAclStore()
    await s.grant(COLL, "user", OWNER, "owner", granted_by="")
    return s


# --------------------------------------------------------------------------- #
# admin bypass — explicit, allowed for everything, logged EVERY time
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("action", ACTIONS)
async def test_admin_bypasses_everything_even_with_no_rows(action):
    decision = await resolve_access(STRANGER, "admin", COLL, action, InMemoryAclStore())
    assert decision == AccessDecision(
        allowed=True, reason="admin role bypasses ownership", via="admin-bypass"
    )


async def test_admin_bypass_is_logged_every_time(caplog, store):
    """The superuser override must leave a countable, time-ordered audit trail:
    repeating the SAME (subject, collection, action) shape logs again — no
    per-shape dedup that would collapse N accesses into one line."""
    with caplog.at_level(logging.INFO, logger="ragstack.authz"):
        await resolve_access(STRANGER, "admin", COLL, "read", store)
        await resolve_access(STRANGER, "admin", COLL, "read", store)  # same shape
        await resolve_access(STRANGER, "admin", COLL, "write", store)
    bypass = [r for r in caplog.records if "admin-bypass" in r.getMessage()]
    assert len(bypass) == 3
    assert STRANGER in bypass[0].getMessage() and COLL in bypass[0].getMessage()


async def test_admin_bypass_does_not_touch_the_store():
    class Exploding:
        def __getattr__(self, name):
            raise AssertionError("admin bypass must not consult the store")

    decision = await resolve_access(STRANGER, "admin", COLL, "owner", Exploding())
    assert decision.allowed and decision.via == "admin-bypass"


# --------------------------------------------------------------------------- #
# owner — every action
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("action", ACTIONS)
async def test_owner_is_allowed_every_action(store, action):
    decision = await resolve_access(OWNER, "user", COLL, action, store)
    assert decision.allowed and decision.via == "owner"


# --------------------------------------------------------------------------- #
# read — any active grant, direct or public
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("permission", ["read", "write"])
async def test_any_direct_active_grant_allows_read(store, permission):
    await store.grant(COLL, "user", ALICE, permission, granted_by=OWNER)
    decision = await resolve_access(ALICE, "user", COLL, "read", store)
    assert decision.allowed and decision.via == "grant"


async def test_public_grant_allows_read_for_any_subject(store):
    await store.grant(COLL, "group", "public", "read", granted_by=OWNER)
    decision = await resolve_access(STRANGER, "user", COLL, "read", store)
    assert decision.allowed and decision.via == "public"


async def test_direct_grant_is_preferred_over_public_in_the_decision(store):
    await store.grant(COLL, "group", "public", "read", granted_by=OWNER)
    await store.grant(COLL, "user", ALICE, "read", granted_by=OWNER)
    decision = await resolve_access(ALICE, "user", COLL, "read", store)
    assert decision.allowed and decision.via == "grant"


async def test_no_grant_means_no_read(store):
    decision = await resolve_access(STRANGER, "user", COLL, "read", store)
    assert not decision.allowed and decision.via is None


async def test_a_grant_on_another_collection_does_not_leak(store):
    await store.grant("col-other", "user", ALICE, "read", granted_by=OWNER)
    decision = await resolve_access(ALICE, "user", COLL, "read", store)
    assert not decision.allowed


async def test_revoked_grant_no_longer_reads(store):
    rec = await store.grant(COLL, "user", ALICE, "read", granted_by=OWNER)
    await store.revoke(rec.id, revoked_by=OWNER)
    decision = await resolve_access(ALICE, "user", COLL, "read", store)
    assert not decision.allowed


# --------------------------------------------------------------------------- #
# write / owner — owner only for the MVP (write shares deferred)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("action", ["write", "owner"])
async def test_even_a_write_grant_does_not_allow_write_yet(store, action):
    await store.grant(COLL, "user", ALICE, "write", granted_by=OWNER)
    decision = await resolve_access(ALICE, "user", COLL, action, store)
    assert not decision.allowed
    assert "deferred" in decision.reason


@pytest.mark.parametrize("action", ["write", "owner"])
async def test_public_never_allows_write_or_owner(store, action):
    await store.grant(COLL, "group", "public", "read", granted_by=OWNER)
    decision = await resolve_access(STRANGER, "user", COLL, action, store)
    assert not decision.allowed


# --------------------------------------------------------------------------- #
# failure modes — fail closed
# --------------------------------------------------------------------------- #


class _BrokenStore:
    async def owner_of(self, collection_id):
        raise ConnectionError("db down")

    async def grants_for_subject(self, subject):
        raise ConnectionError("db down")


class _BrokenGrants(InMemoryAclStore):
    async def grants_for_subject(self, subject):
        raise ConnectionError("db down")


@pytest.mark.parametrize("action", ACTIONS)
async def test_store_failure_denies_by_raising_authz_unavailable(action):
    with pytest.raises(AuthzUnavailable):
        await resolve_access(ALICE, "user", COLL, action, _BrokenStore())


async def test_grants_lookup_failure_also_fails_closed():
    store = _BrokenGrants()
    await store.grant(COLL, "user", OWNER, "owner", granted_by="")
    # Owner still resolves (owner_of works)...
    assert (await resolve_access(OWNER, "user", COLL, "read", store)).allowed
    # ...but a non-owner read that needs the grants lookup raises.
    with pytest.raises(AuthzUnavailable):
        await resolve_access(ALICE, "user", COLL, "read", store)


async def test_unknown_action_is_rejected_outright(store):
    with pytest.raises(ValueError, match="action"):
        await resolve_access(ALICE, "user", COLL, "delete", store)  # type: ignore[arg-type]


async def test_non_admin_roles_get_no_bypass(store):
    for role in ("user", "researcher", "", "ADMIN "):  # exact match only
        decision = await resolve_access(STRANGER, role, COLL, "read", store)
        assert not decision.allowed


# --------------------------------------------------------------------------- #
# resolve_read_many — the listing batch: same decisions, one store round-trip
# --------------------------------------------------------------------------- #


async def test_read_many_agrees_with_per_collection_decisions(store):
    await store.grant(COLL, "user", ALICE, "read", granted_by=OWNER)
    await store.grant("col-open", "group", "public", "read", granted_by=OWNER)
    cids = [COLL, "col-open", "col-hidden"]
    for subject in (OWNER, ALICE, STRANGER):
        batch = await resolve_read_many(subject, "user", cids, store)
        for cid in cids:
            single = await resolve_access(subject, "user", cid, "read", store)
            assert batch[cid].allowed == single.allowed, (subject, cid)
            assert batch[cid].via == single.via, (subject, cid)


async def test_read_many_touches_the_store_exactly_once(store):
    calls = {"n": 0}
    inner = store.grants_for_subject

    async def counting(subject):
        calls["n"] += 1
        return await inner(subject)

    store.grants_for_subject = counting  # type: ignore[method-assign]
    batch = await resolve_read_many(OWNER, "user", [COLL, "a", "b", "c"], store)
    assert calls["n"] == 1
    assert batch[COLL].allowed and batch[COLL].via == "owner"


async def test_read_many_admin_bypasses_with_one_summary_log(caplog):
    class Exploding:
        def __getattr__(self, name):
            raise AssertionError("admin bypass must not consult the store")

    with caplog.at_level(logging.INFO, logger="ragstack.authz"):
        batch = await resolve_read_many(STRANGER, "admin", ["a", "b", "c"], Exploding())
    assert all(d.allowed and d.via == "admin-bypass" for d in batch.values())
    bypass = [r for r in caplog.records if "admin-bypass" in r.getMessage()]
    assert len(bypass) == 1  # one summary line per batch, not one per entry


async def test_read_many_fails_closed_on_store_error():
    with pytest.raises(AuthzUnavailable):
        await resolve_read_many(ALICE, "user", [COLL], _BrokenStore())
