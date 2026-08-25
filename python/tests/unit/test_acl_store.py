"""The collection ACL store (``ragstack.acl_store``, ADR-0004 decisions 4-6).

The invariants under test are the ones authorization depends on: one active
owner per collection, owner grantable to users only, ``public`` read-only,
no duplicate active grants (but re-grant after revoke), soft revocation that
never deletes, and the recursive revoke that follows ``granted_by`` chains —
stopping where a grantee retains access through an independent share.
"""
from __future__ import annotations

import os
import sqlite3
from types import SimpleNamespace

import pytest

from ragstack.acl_store import (
    InMemoryAclStore,
    PostgresAclStore,
    ShareInvariantError,
    ShareNotFoundError,
    SqliteAclStore,
    make_acl_store,
)
from ragstack.user_store import UserStore

pytestmark = pytest.mark.asyncio

COLL = "col-abc123"
OWNER = "bvbrc:owner@patricbrc.org"
ALICE = "bvbrc:alice@patricbrc.org"
BOB = "google:bob-sub-42"
CAROL = "google:carol-sub-7"


def _settings(tmp_path, **over):
    base: dict = {
        "user_store_backend": "memory",
        "user_store_path": str(tmp_path / "acl.db"),
        "user_store_dsn": "",
        "postgres_dsn": "",
    }
    base.update(over)
    return SimpleNamespace(**base)


def _stores(tmp_path):
    """Every locally-runnable backend. Postgres needs a server, so it is covered
    separately (schema parity below + an opt-in round-trip)."""
    return {
        "memory": InMemoryAclStore(),
        "sqlite": SqliteAclStore(str(tmp_path / "acl-rt.db")),
    }


BACKENDS = ["memory", "sqlite"]


async def _seed_owner(store):
    return await store.grant(COLL, "user", OWNER, "owner", granted_by="")


# --------------------------------------------------------------------------- #
# grant — record shape + invariants
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("backend", BACKENDS)
async def test_grant_writes_an_active_audited_row(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    rec = await store.grant(COLL, "user", ALICE, "read", granted_by=OWNER, grant_option=True)
    assert rec.id and rec.granted_at
    assert rec.collection_id == COLL and rec.grantee_id == ALICE
    assert rec.permission == "read" and rec.grant_option is True
    assert rec.granted_by == OWNER
    assert rec.revoked_at == "" and rec.active

    listed = await store.shares_for(COLL)
    assert listed == [rec]


@pytest.mark.parametrize("backend", BACKENDS)
async def test_duplicate_active_grant_is_rejected(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    await store.grant(COLL, "user", ALICE, "read", granted_by=OWNER)
    with pytest.raises(ShareInvariantError, match="already exists"):
        await store.grant(COLL, "user", ALICE, "read", granted_by=OWNER)
    # A different permission for the same grantee is NOT a duplicate.
    await store.grant(COLL, "user", ALICE, "write", granted_by=OWNER)


@pytest.mark.parametrize("backend", BACKENDS)
async def test_regrant_after_revoke_is_allowed(tmp_path, backend):
    """The partial-unique form permits re-grant after revoke (ADR-0004
    decision 6) — a plain unique constraint would block it forever."""
    store = _stores(tmp_path)[backend]
    first = await store.grant(COLL, "user", ALICE, "read", granted_by=OWNER)
    await store.revoke(first.id, revoked_by=OWNER)
    second = await store.grant(COLL, "user", ALICE, "read", granted_by=OWNER)
    assert second.active and second.id != first.id
    # Both rows exist: the audit trail keeps the revoked one.
    assert len(await store.shares_for(COLL, include_revoked=True)) == 2


@pytest.mark.parametrize("backend", BACKENDS)
async def test_second_active_owner_is_rejected(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    await _seed_owner(store)
    with pytest.raises(ShareInvariantError, match="already has an active owner"):
        await store.grant(COLL, "user", ALICE, "owner", granted_by=OWNER)
    # A different collection may of course have its own owner.
    await store.grant("col-other", "user", ALICE, "owner", granted_by="")


@pytest.mark.parametrize("backend", BACKENDS)
async def test_owner_is_grantable_to_users_only(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    with pytest.raises(ShareInvariantError, match="users only"):
        await store.grant(COLL, "group", "team-x", "owner", granted_by=OWNER)


@pytest.mark.parametrize("backend", BACKENDS)
async def test_public_group_may_hold_read_only(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    for perm in ("write", "owner"):
        with pytest.raises(ShareInvariantError):
            await store.grant(COLL, "group", "public", perm, granted_by=OWNER)
    # And 'public' is a group, never a user (a same-named user would shadow it).
    with pytest.raises(ShareInvariantError, match="built-in group"):
        await store.grant(COLL, "user", "public", "read", granted_by=OWNER)
    rec = await store.grant(COLL, "group", "public", "read", granted_by=OWNER)
    assert rec.active


@pytest.mark.parametrize("backend", BACKENDS)
async def test_grant_rejects_unknown_vocabulary_and_empty_keys(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    with pytest.raises(ShareInvariantError):
        await store.grant(COLL, "robot", ALICE, "read", granted_by=OWNER)
    with pytest.raises(ShareInvariantError):
        await store.grant(COLL, "user", ALICE, "execute", granted_by=OWNER)
    with pytest.raises(ShareInvariantError):
        await store.grant("", "user", ALICE, "read", granted_by=OWNER)
    with pytest.raises(ShareInvariantError):
        await store.grant(COLL, "user", "", "read", granted_by=OWNER)


@pytest.mark.parametrize("backend", BACKENDS)
async def test_sqlite_partial_index_backstops_the_race_window(tmp_path, backend):
    """Even if the code check were bypassed, the partial unique indexes reject
    a duplicate active share at the database (the concurrent-writer story)."""
    if backend != "sqlite":
        pytest.skip("index enforcement is a SQL-backend property")
    store = _stores(tmp_path)[backend]
    rec = await store.grant(COLL, "user", ALICE, "read", granted_by=OWNER)
    with sqlite3.connect(store._path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO shares (id, collection_id, grantee_type, grantee_id, permission)"
                " VALUES ('x1', ?, 'user', ?, 'read')",
                (COLL, ALICE),
            )
    assert rec.active


# --------------------------------------------------------------------------- #
# revoke — soft, never DELETE, recursive along granted_by chains
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("backend", BACKENDS)
async def test_revoke_is_soft_and_never_deletes(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    rec = await store.grant(COLL, "user", ALICE, "read", granted_by=OWNER)
    revoked = await store.revoke(rec.id, revoked_by=OWNER)
    assert [r.id for r in revoked] == [rec.id]
    assert revoked[0].revoked_by == OWNER and revoked[0].revoked_at

    assert await store.shares_for(COLL) == []  # not active...
    history = await store.shares_for(COLL, include_revoked=True)
    assert [r.id for r in history] == [rec.id]  # ...but never deleted


@pytest.mark.parametrize("backend", BACKENDS)
async def test_revoke_unknown_id_raises_and_rerevoke_is_a_noop(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    with pytest.raises(ShareNotFoundError):
        await store.revoke("no-such-share", revoked_by=OWNER)
    rec = await store.grant(COLL, "user", ALICE, "read", granted_by=OWNER)
    first = await store.revoke(rec.id, revoked_by=OWNER)
    assert first
    again = await store.revoke(rec.id, revoked_by=BOB)
    assert again == []
    # The original audit fields were not overwritten by the no-op.
    history = await store.shares_for(COLL, include_revoked=True)
    assert history[0].revoked_by == OWNER


@pytest.mark.parametrize("backend", BACKENDS)
async def test_recursive_revoke_follows_granted_by_chains(tmp_path, backend):
    """ADR-0004 decision 5: revoking a grantor also revokes everything they
    granted onward — transitively."""
    store = _stores(tmp_path)[backend]
    await _seed_owner(store)
    s_alice = await store.grant(COLL, "user", ALICE, "read", granted_by=OWNER)
    s_bob = await store.grant(COLL, "user", BOB, "read", granted_by=ALICE)
    s_carol = await store.grant(COLL, "user", CAROL, "read", granted_by=BOB)

    revoked = await store.revoke(s_alice.id, revoked_by=OWNER)
    assert {r.id for r in revoked} == {s_alice.id, s_bob.id, s_carol.id}
    assert revoked[0].id == s_alice.id  # root first
    assert all(r.revoked_by == OWNER and r.revoked_at for r in revoked)
    # Only the owner row survives.
    assert [r.permission for r in await store.shares_for(COLL)] == ["owner"]


@pytest.mark.parametrize("backend", BACKENDS)
async def test_recursive_revoke_stops_at_grantees_with_independent_access(tmp_path, backend):
    """Partial overlap: Bob loses the share Alice granted, but retains access
    through the owner's independent grant — so Bob's own onward grant
    survives, and nothing revoked is resurrected."""
    store = _stores(tmp_path)[backend]
    await _seed_owner(store)
    s_alice = await store.grant(COLL, "user", ALICE, "read", granted_by=OWNER)
    s_bob_via_alice = await store.grant(COLL, "user", BOB, "read", granted_by=ALICE)
    s_bob_via_owner = await store.grant(COLL, "user", BOB, "write", granted_by=OWNER)
    s_carol_via_bob = await store.grant(COLL, "user", CAROL, "read", granted_by=BOB)

    revoked = await store.revoke(s_alice.id, revoked_by=OWNER)
    assert {r.id for r in revoked} == {s_alice.id, s_bob_via_alice.id}

    active_ids = {r.id for r in await store.shares_for(COLL)}
    # Bob's independent grant and Carol's chain through it survive.
    assert s_bob_via_owner.id in active_ids and s_carol_via_bob.id in active_ids
    # The revoked pair stays revoked — no resurrection.
    assert s_alice.id not in active_ids and s_bob_via_alice.id not in active_ids


@pytest.mark.parametrize("backend", BACKENDS)
async def test_revoking_bobs_last_share_takes_his_chain_with_it(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    await _seed_owner(store)
    s_bob = await store.grant(COLL, "user", BOB, "read", granted_by=OWNER)
    s_carol = await store.grant(COLL, "user", CAROL, "read", granted_by=BOB)
    revoked = await store.revoke(s_bob.id, revoked_by=OWNER)
    assert {r.id for r in revoked} == {s_bob.id, s_carol.id}


@pytest.mark.parametrize("backend", BACKENDS)
async def test_mutual_grant_cycle_cannot_outlive_its_only_root(tmp_path, backend):
    """Grounded (least-fixpoint) support: Alice and Bob pre-arrange a mutual
    grant cycle (owner→Alice, Alice→Bob, Bob→Alice-write). Revoking the only
    external root must take the whole cycle down — the two remaining shares
    justify each other and nothing else, so neither may keep access."""
    store = _stores(tmp_path)[backend]
    await _seed_owner(store)
    s1 = await store.grant(COLL, "user", ALICE, "read", granted_by=OWNER)
    s2 = await store.grant(COLL, "user", BOB, "read", granted_by=ALICE)
    s3 = await store.grant(COLL, "user", ALICE, "write", granted_by=BOB)

    revoked = await store.revoke(s1.id, revoked_by=OWNER)
    assert {r.id for r in revoked} == {s1.id, s2.id, s3.id}
    # Only the (self-granted / externally granted) owner row survives.
    assert [r.permission for r in await store.shares_for(COLL)] == ["owner"]


# --------------------------------------------------------------------------- #
# owner_of / grants_for_subject
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("backend", BACKENDS)
async def test_owner_of_reflects_the_single_active_owner_row(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    assert await store.owner_of(COLL) is None
    row = await _seed_owner(store)
    assert await store.owner_of(COLL) == OWNER
    await store.revoke(row.id, revoked_by=OWNER)
    assert await store.owner_of(COLL) is None


@pytest.mark.parametrize("backend", BACKENDS)
async def test_grants_for_subject_sees_direct_and_public_only(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    mine = await store.grant(COLL, "user", ALICE, "read", granted_by=OWNER)
    pub = await store.grant("col-open", "group", "public", "read", granted_by=OWNER)
    other = await store.grant(COLL, "user", BOB, "read", granted_by=OWNER)
    group = await store.grant(COLL, "group", "team-x", "read", granted_by=OWNER)

    ids = {r.id for r in await store.grants_for_subject(ALICE)}
    assert ids == {mine.id, pub.id}  # direct + public...
    assert other.id not in ids and group.id not in ids  # ...not others, not real groups

    await store.revoke(mine.id, revoked_by=OWNER)
    assert {r.id for r in await store.grants_for_subject(ALICE)} == {pub.id}


# --------------------------------------------------------------------------- #
# transfer_owner — the ADR's atomic revoke+grant reassignment pair
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("backend", BACKENDS)
async def test_transfer_owner_swaps_the_owner_row_atomically(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    old = await _seed_owner(store)
    new = await store.transfer_owner(COLL, ALICE, actor="bvbrc:admin@patricbrc.org")
    assert new.permission == "owner" and new.grantee_id == ALICE
    assert new.granted_by == "bvbrc:admin@patricbrc.org"
    assert await store.owner_of(COLL) == ALICE

    history = await store.shares_for(COLL, include_revoked=True)
    owners = [r for r in history if r.permission == "owner"]
    assert len(owners) == 2  # both rows kept — the audited pair
    revoked_old = next(r for r in owners if r.id == old.id)
    assert revoked_old.revoked_by == "bvbrc:admin@patricbrc.org" and revoked_old.revoked_at
    # Exactly one active owner.
    assert [r.grantee_id for r in await store.shares_for(COLL)] == [ALICE]


@pytest.mark.parametrize("backend", BACKENDS)
async def test_transfer_owner_does_not_cascade_the_share_graph(tmp_path, backend):
    """Handing a collection over must not destroy its shares — the transfer
    pair is deliberately non-cascading."""
    store = _stores(tmp_path)[backend]
    await _seed_owner(store)
    s_alice = await store.grant(COLL, "user", ALICE, "read", granted_by=OWNER)
    await store.transfer_owner(COLL, BOB, actor=OWNER)
    assert s_alice.id in {r.id for r in await store.shares_for(COLL)}


@pytest.mark.parametrize("backend", BACKENDS)
async def test_transfer_owner_failure_leaves_the_old_owner_intact(tmp_path, backend):
    """Atomicity: an invalid new grant rolls the revoke back too."""
    store = _stores(tmp_path)[backend]
    await _seed_owner(store)
    with pytest.raises(ShareInvariantError):
        await store.transfer_owner(COLL, "", actor=OWNER)  # empty grantee is invalid
    assert await store.owner_of(COLL) == OWNER
    with pytest.raises(ShareInvariantError, match="no active owner"):
        await store.transfer_owner("col-unowned", ALICE, actor=OWNER)


# --------------------------------------------------------------------------- #
# sqlite durability + migration
# --------------------------------------------------------------------------- #


async def test_sqlite_shares_survive_reopen(tmp_path):
    path = str(tmp_path / "durable.db")
    rec = await SqliteAclStore(path).grant(COLL, "user", ALICE, "read", granted_by=OWNER)
    reopened = SqliteAclStore(path)
    assert [r.id for r in await reopened.shares_for(COLL)] == [rec.id]
    assert await reopened.owner_of(COLL) is None


async def test_sqlite_tolerates_a_shares_table_from_an_older_build(tmp_path):
    """Additive-only migration: a narrower ``shares`` table is widened by
    ensure_columns rather than crashing on the first SELECT."""
    path = str(tmp_path / "old.db")
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE shares (id TEXT PRIMARY KEY, collection_id TEXT)")
        conn.execute("INSERT INTO shares (id, collection_id) VALUES ('legacy1', 'col-old')")
    store = SqliteAclStore(path)  # runs ensure_columns + index DDL
    rows = await store.shares_for("col-old")
    assert [r.id for r in rows] == ["legacy1"]
    await store.grant(COLL, "user", ALICE, "read", granted_by=OWNER)


# --------------------------------------------------------------------------- #
# postgres — schema parity without a server, round-trip with one
# --------------------------------------------------------------------------- #


async def test_postgres_store_shares_the_sqlite_schema():
    """Both SQL backends must render the same table from the same DDL strings.
    TEXT/INTEGER only: no JSONB, no TIMESTAMPTZ (ISO-8601 text)."""
    from ragstack import acl_store as acl

    for stmt in (acl._SHARES_DDL, *acl._SHARES_INDEXES):
        assert "JSONB" not in stmt.upper() and "TIMESTAMP" not in stmt.upper()
    assert set(acl._SHARES_COLUMNS) | {"id"} == set(acl._SHARE_COLUMNS)
    for name, frag in acl._SHARES_COLUMNS.items():
        assert "NOT NULL" not in frag or "DEFAULT" in frag, name
    # The invariants that cannot ride ensure_columns are their own
    # CREATE UNIQUE INDEX IF NOT EXISTS statements (shared-dialect syntax).
    active, owner, owned_by = acl._SHARES_INDEXES
    assert "UNIQUE INDEX" in active and "WHERE revoked_at = ''" in active
    assert "UNIQUE INDEX" in owner and "permission = 'owner'" in owner
    # count_owned's supporting index (#290) is deliberately NOT unique — a
    # grantee legitimately holds many active owner rows.
    assert "UNIQUE" not in owned_by
    assert "(grantee_id, permission)" in owned_by and "WHERE revoked_at = ''" in owned_by
    assert isinstance(PostgresAclStore("postgresql://x/y"), object)


@pytest.mark.skipif(
    not os.environ.get("RAGSTACK_TEST_POSTGRES_DSN"),
    reason="set RAGSTACK_TEST_POSTGRES_DSN to exercise the postgres backend",
)
async def test_postgres_round_trip():
    store = PostgresAclStore(os.environ["RAGSTACK_TEST_POSTGRES_DSN"])
    coll = "col-pg-roundtrip"
    try:
        owner_row = await store.grant(coll, "user", OWNER, "owner", granted_by="")
        assert await store.owner_of(coll) == OWNER
        with pytest.raises(ShareInvariantError):
            await store.grant(coll, "user", ALICE, "owner", granted_by=OWNER)
        s_alice = await store.grant(coll, "user", ALICE, "read", granted_by=OWNER)
        s_bob = await store.grant(coll, "user", BOB, "read", granted_by=ALICE)
        revoked = await store.revoke(s_alice.id, revoked_by=OWNER)
        assert {r.id for r in revoked} == {s_alice.id, s_bob.id}
        await store.transfer_owner(coll, ALICE, actor=OWNER)
        assert await store.owner_of(coll) == ALICE
        await store.revoke(owner_row.id, revoked_by=OWNER)  # already revoked -> noop
    finally:
        await store.close()


# --------------------------------------------------------------------------- #
# backend selection + singleton
# --------------------------------------------------------------------------- #


async def test_every_acl_store_is_also_a_user_store(tmp_path):
    """One store object, one database, both tables — the wiring contract."""
    for store in _stores(tmp_path).values():
        assert isinstance(store, UserStore)
        await store.upsert_seen(ALICE, "bvbrc")
        assert (await store.get(ALICE)) is not None
        # The #258 users columns/methods reach this store through super() —
        # acl_store adds no users DDL of its own, so this is what proves the
        # single shared definition actually propagated.
        svc = await store.create_service_account("svc-acl", ALICE, purpose="test")
        assert svc.is_service and (await store.list_service_accounts()) == [svc]


async def test_backend_selection(tmp_path):
    assert isinstance(make_acl_store(_settings(tmp_path)), InMemoryAclStore)
    assert isinstance(
        make_acl_store(_settings(tmp_path, user_store_backend="sqlite")),
        SqliteAclStore,
    )
    assert isinstance(
        make_acl_store(
            _settings(tmp_path, user_store_backend="postgres",
                      user_store_dsn="postgresql://x/y")
        ),
        PostgresAclStore,
    )
    assert isinstance(
        make_acl_store(
            _settings(tmp_path, user_store_backend="postgres",
                      postgres_dsn="postgresql://shared/db")
        ),
        PostgresAclStore,
    )
    assert isinstance(
        make_acl_store(_settings(tmp_path, user_store_backend="wat")),
        InMemoryAclStore,
    )


async def test_get_acl_store_singleton_rebuilds_on_settings_change(tmp_path, monkeypatch):
    from ragstack import acl_store

    monkeypatch.setattr(acl_store.settings, "user_store_backend", "memory")
    acl_store.reset_acl_store()
    try:
        first = acl_store.get_acl_store()
        assert first is acl_store.get_acl_store()  # cached
        assert isinstance(first, InMemoryAclStore)

        monkeypatch.setattr(acl_store.settings, "user_store_backend", "sqlite")
        monkeypatch.setattr(
            acl_store.settings, "user_store_path", str(tmp_path / "singleton.db")
        )
        rebuilt = acl_store.get_acl_store()
        assert rebuilt is not first and isinstance(rebuilt, SqliteAclStore)

        injected = InMemoryAclStore()
        acl_store.set_acl_store(injected)
        assert acl_store.get_acl_store() is injected
    finally:
        acl_store.reset_acl_store()
