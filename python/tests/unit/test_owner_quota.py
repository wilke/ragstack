"""Per-owner collection quota (issue #290): ``AclStore.count_owned`` and the
atomic ``owner_quota`` check threaded through ``grant``/``transfer_owner``.

Enforcement happens on ACQUISITION — creating an owner row (``grant`` with
``permission='owner'``) and transferring one (``transfer_owner``) — in the
SAME transaction/critical section as the write, on every backend:

- memory: the single event loop (no ``await`` between the count and the
  write, all under ``self._lock``);
- sqlite: ``BEGIN IMMEDIATE`` takes the write lock before the count, exactly
  like ``user_store.SqliteUserStore._set_role_sync``;
- postgres: ``pg_advisory_xact_lock`` serializes the count-then-write against
  every other quota-checked call, released on commit/rollback.

Parametrised over all three backends; postgres skips without
``RAGSTACK_TEST_PG_DSN`` (``pg_test_dsn`` fixture, the one sanctioned way any
test here may open a real Postgres connection).
"""
from __future__ import annotations

import asyncio

import pytest

from ragstack.acl_store import (
    GRANTEE_USER,
    PERM_OWNER,
    InMemoryAclStore,
    OwnerQuotaExceededError,
    PostgresAclStore,
    ShareInvariantError,
    SqliteAclStore,
)

pytestmark = pytest.mark.asyncio

LIMIT = 5
ALICE = "bvbrc:alice@patricbrc.org"
BOB = "bvbrc:bob@patricbrc.org"

BACKENDS = ["memory", "sqlite", "postgres"]


@pytest.fixture(params=BACKENDS)
def _backend(request, tmp_path):
    """One store per backend. Sync on purpose — pulling the async
    ``pg_test_dsn`` fixture via ``getfixturevalue`` only works from outside the
    running loop (mirrors ``test_collection_state.py``'s ``_backend``)."""
    if request.param == "memory":
        return InMemoryAclStore()
    if request.param == "sqlite":
        return SqliteAclStore(str(tmp_path / "acl.db"))
    return PostgresAclStore(request.getfixturevalue("pg_test_dsn"))


@pytest.fixture
async def store(_backend):
    try:
        yield _backend
    finally:
        await _backend.close()


async def _own_n(store, subject: str, n: int, prefix: str = "col") -> None:
    """Seed ``n`` active owner rows for ``subject``, bypassing the quota check
    (``owner_quota`` omitted) — this is how the test's starting state is built,
    not what is under test."""
    for i in range(n):
        await store.grant(f"{prefix}-{subject}-{i}", GRANTEE_USER, subject, PERM_OWNER,
                           granted_by=subject)


# --------------------------------------------------------------------------- #
# count_owned
# --------------------------------------------------------------------------- #


async def test_count_owned_counts_only_active_owner_rows_for_the_subject(store):
    assert await store.count_owned(ALICE) == 0
    await _own_n(store, ALICE, 3)
    assert await store.count_owned(ALICE) == 3
    assert await store.count_owned(BOB) == 0
    # A non-owner share (read/write) does not count.
    await store.grant("col-shared", GRANTEE_USER, ALICE, "read", granted_by=BOB)
    assert await store.count_owned(ALICE) == 3
    # Revoking an owner row drops the count.
    rows = await store.shares_for(f"col-{ALICE}-0")
    await store.revoke(rows[0].id, revoked_by=ALICE)
    assert await store.count_owned(ALICE) == 2


# --------------------------------------------------------------------------- #
# create (grant) at the limit
# --------------------------------------------------------------------------- #


async def test_grant_owner_at_the_limit_is_refused(store):
    await _own_n(store, ALICE, LIMIT)
    with pytest.raises(OwnerQuotaExceededError) as ei:
        await store.grant("col-over", GRANTEE_USER, ALICE, PERM_OWNER, granted_by=ALICE,
                           owner_quota=LIMIT)
    assert ei.value.owned == LIMIT and ei.value.limit == LIMIT
    # Refused — the count did not move and no row was written.
    assert await store.count_owned(ALICE) == LIMIT
    assert await store.owner_of("col-over") is None


async def test_grant_owner_under_the_limit_still_succeeds(store):
    await _own_n(store, ALICE, LIMIT - 1)
    rec = await store.grant("col-last", GRANTEE_USER, ALICE, PERM_OWNER, granted_by=ALICE,
                             owner_quota=LIMIT)
    assert rec.grantee_id == ALICE
    assert await store.count_owned(ALICE) == LIMIT


async def test_owner_quota_none_disables_the_check(store):
    """The default — ``owner_quota=None`` — is what backfill's ``_try_grant``
    relies on to never refuse."""
    await _own_n(store, ALICE, LIMIT)
    rec = await store.grant("col-unchecked", GRANTEE_USER, ALICE, PERM_OWNER, granted_by=ALICE)
    assert rec.active
    assert await store.count_owned(ALICE) == LIMIT + 1


async def test_idempotent_same_subject_regrant_of_the_same_collection_is_not_growth(store):
    """A concurrent backfill / retried create landing an owner row for the SAME
    subject on the SAME (new) collection must not be miscounted as acquiring
    one MORE collection — the quota check excludes the target collection_id."""
    await _own_n(store, ALICE, LIMIT)
    # Alice is already at the limit; a grant of a collection she does NOT yet
    # own is refused...
    with pytest.raises(OwnerQuotaExceededError):
        await store.grant("col-new", GRANTEE_USER, ALICE, PERM_OWNER, granted_by=ALICE,
                           owner_quota=LIMIT)
    # ...but re-granting owner of the FIRST collection she already owns (the
    # exact race write_owner_row's idempotent-success path handles) is not
    # blocked by the quota — it collides on the one-owner-per-collection
    # invariant instead, which is the correct error for that case.
    first_id = f"col-{ALICE}-0"
    with pytest.raises(ShareInvariantError, match="already has an active owner"):
        await store.grant(first_id, GRANTEE_USER, ALICE, PERM_OWNER, granted_by=ALICE,
                           owner_quota=LIMIT)


# --------------------------------------------------------------------------- #
# transfer — the other acquisition site
# --------------------------------------------------------------------------- #


async def test_transfer_to_a_full_grantee_is_refused_and_source_keeps_ownership(store):
    await store.grant("col-x", GRANTEE_USER, ALICE, PERM_OWNER, granted_by=ALICE)
    await _own_n(store, BOB, LIMIT)  # bob is already at the limit
    with pytest.raises(OwnerQuotaExceededError) as ei:
        await store.transfer_owner("col-x", BOB, actor=ALICE, owner_quota=LIMIT)
    assert ei.value.owned == LIMIT and ei.value.limit == LIMIT
    # Nothing changed: alice keeps col-x, bob's count is untouched.
    assert await store.owner_of("col-x") == ALICE
    assert await store.count_owned(BOB) == LIMIT


async def test_transfer_to_a_grantee_under_the_limit_succeeds(store):
    await store.grant("col-x", GRANTEE_USER, ALICE, PERM_OWNER, granted_by=ALICE)
    await _own_n(store, BOB, LIMIT - 1)
    rec = await store.transfer_owner("col-x", BOB, actor=ALICE, owner_quota=LIMIT)
    assert rec.grantee_id == BOB
    assert await store.owner_of("col-x") == BOB
    assert await store.count_owned(BOB) == LIMIT


# --------------------------------------------------------------------------- #
# the evasion sequence from the issue: create at limit -> transfer away -> create
# --------------------------------------------------------------------------- #


async def test_the_evasion_sequence_is_blocked_by_the_transfer_side_check(store):
    """create at the limit -> transfer one to a colluding subject -> create
    again. Unchecked at transfer, this is unbounded: the transfer always frees
    a slot regardless of who receives it. Checked at transfer, the sequence is
    only as good as the colluder's OWN remaining quota — once the colluder is
    also full (the scenario here), the transfer itself 409s and alice never
    drops below the limit, so the follow-up create stays blocked too."""
    await _own_n(store, ALICE, LIMIT, prefix="mine")
    colluder = "bvbrc:colluder@patricbrc.org"
    await _own_n(store, colluder, LIMIT, prefix="junk")

    target = f"mine-{ALICE}-0"
    with pytest.raises(OwnerQuotaExceededError):
        await store.transfer_owner(target, colluder, actor=ALICE, owner_quota=LIMIT)
    assert await store.owner_of(target) == ALICE  # evasion transfer refused
    assert await store.count_owned(ALICE) == LIMIT  # never dropped

    with pytest.raises(OwnerQuotaExceededError):
        await store.grant("mine-extra", GRANTEE_USER, ALICE, PERM_OWNER, granted_by=ALICE,
                           owner_quota=LIMIT)
    assert await store.count_owned(ALICE) == LIMIT  # still blocked


async def test_the_poisoning_attack_is_blocked_at_the_grantees_quota(store):
    """Transferring junk onto a colleague who is NOT yet full still succeeds
    (nothing here refuses a legitimate handover) — but once it fills them up,
    the NEXT one lands on their quota exactly like any other acquisition."""
    victim = "bvbrc:victim@patricbrc.org"
    await _own_n(store, victim, LIMIT - 1, prefix="victim-own")
    await _own_n(store, ALICE, 1, prefix="poison")
    poison_id = f"poison-{ALICE}-0"
    # Fills the victim to exactly the limit — allowed, it's a real handover.
    await store.transfer_owner(poison_id, victim, actor=ALICE, owner_quota=LIMIT)
    assert await store.count_owned(victim) == LIMIT
    # A second one is refused: the victim's own quota is what stops this.
    await _own_n(store, ALICE, 1, prefix="poison2")
    with pytest.raises(OwnerQuotaExceededError):
        await store.transfer_owner(
            f"poison2-{ALICE}-0", victim, actor=ALICE, owner_quota=LIMIT
        )


# --------------------------------------------------------------------------- #
# concurrency: 20 racers at limit-1 yield exactly one winner
# --------------------------------------------------------------------------- #


async def test_twenty_concurrent_creates_at_limit_minus_one_yield_exactly_one_winner(store):
    """Real concurrency: sqlite's ``grant`` runs each call via
    ``asyncio.to_thread`` with its own connection (copying the pattern
    ``test_collection_state.py`` uses for its CAS test), so ``asyncio.gather``
    genuinely races 20 threads against the same ``BEGIN IMMEDIATE`` write lock.
    Memory races 20 coroutines through the single event loop; postgres races
    20 connections against one ``pg_advisory_xact_lock``. All three must yield
    exactly one success."""
    await _own_n(store, ALICE, LIMIT - 1)
    assert await store.count_owned(ALICE) == LIMIT - 1

    async def _racer(i: int) -> bool:
        try:
            await store.grant(f"racer-{i}", GRANTEE_USER, ALICE, PERM_OWNER, granted_by=ALICE,
                               owner_quota=LIMIT)
            return True
        except OwnerQuotaExceededError:
            return False

    results = await asyncio.gather(*(_racer(i) for i in range(20)))
    assert results.count(True) == 1, results
    assert await store.count_owned(ALICE) == LIMIT


async def test_twenty_concurrent_transfers_to_the_same_grantee_yield_exactly_one_winner(store):
    await _own_n(store, ALICE, LIMIT - 1)
    for i in range(20):
        await store.grant(f"src-{i}", GRANTEE_USER, f"src-owner-{i}", PERM_OWNER,
                           granted_by=f"src-owner-{i}")

    async def _racer(i: int) -> bool:
        try:
            await store.transfer_owner(
                f"src-{i}", ALICE, actor=f"src-owner-{i}", owner_quota=LIMIT
            )
            return True
        except OwnerQuotaExceededError:
            return False

    results = await asyncio.gather(*(_racer(i) for i in range(20)))
    assert results.count(True) == 1, results
    assert await store.count_owned(ALICE) == LIMIT
