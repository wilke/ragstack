"""The RAGStack group store (``ragstack.group_store``, ADR-0004 decisions 3/4).

The invariants under test are the ones authorization depends on: the built-in
``public`` group is present, constant-true, never materialized, never deletable
and never directly member-editable; membership is a flat list of user subjects
(no nesting); groups_for_subject returns exactly the active-member set plus
public; a share to a real group applies to its active members (the #245 seam);
and every mutation is soft (never DELETE) and audited.
"""
from __future__ import annotations

import os
import sqlite3
from types import SimpleNamespace

import pytest

from ragstack.acl_store import AclStore
from ragstack.group_store import (
    GroupInvariantError,
    GroupNotFoundError,
    InMemoryGroupStore,
    PostgresGroupStore,
    SqliteGroupStore,
    make_group_store,
)
from ragstack.user_store import UserStore

pytestmark = pytest.mark.asyncio

PUBLIC = "public"
COLL = "col-abc123"
OWNER = "bvbrc:owner@patricbrc.org"
ALICE = "bvbrc:alice@patricbrc.org"
BOB = "google:bob-sub-42"
CAROL = "google:carol-sub-7"

BACKENDS = ["memory", "sqlite"]


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
    """Every locally-runnable backend. Postgres needs a server (schema parity
    below + an opt-in round-trip)."""
    return {
        "memory": InMemoryGroupStore(),
        "sqlite": SqliteGroupStore(str(tmp_path / "g.db")),
    }


# --------------------------------------------------------------------------- #
# create / get / list groups
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("backend", BACKENDS)
async def test_create_group_writes_an_active_audited_row(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    g = await store.create_group("readers", owner_subject=OWNER)
    assert g.id and g.created_at
    assert g.name == "readers" and g.owner_subject == OWNER
    assert g.built_in is False and g.active and g.deleted_at == ""

    fetched = await store.get_group(g.id)
    assert fetched == g
    assert [x.id for x in await store.list_groups_owned_by(OWNER)] == [g.id]


@pytest.mark.parametrize("backend", BACKENDS)
async def test_active_name_collision_per_owner_is_rejected(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    await store.create_group("team", owner_subject=OWNER)
    with pytest.raises(GroupInvariantError):
        await store.create_group("team", owner_subject=OWNER)
    # A different owner may reuse the name; and after delete it frees up.
    await store.create_group("team", owner_subject=ALICE)


@pytest.mark.parametrize("backend", BACKENDS)
async def test_empty_and_reserved_names_are_rejected(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    with pytest.raises(GroupInvariantError):
        await store.create_group("", owner_subject=OWNER)
    with pytest.raises(GroupInvariantError):
        await store.create_group(PUBLIC, owner_subject=OWNER)  # reserved
    with pytest.raises(GroupInvariantError):
        await store.create_group("x", owner_subject="")  # owner required


@pytest.mark.parametrize("backend", BACKENDS)
async def test_delete_group_is_soft_and_frees_the_name(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    g = await store.create_group("team", owner_subject=OWNER)
    deleted = await store.delete_group(g.id, actor=OWNER)
    assert deleted.deleted_by == OWNER and deleted.deleted_at
    assert not deleted.active
    # Gone from active listings...
    assert await store.list_groups_owned_by(OWNER) == []
    # ...but the row survives (soft delete) and the name is reusable.
    again = await store.create_group("team", owner_subject=OWNER)
    assert again.id != g.id
    # Deleting an unknown group raises.
    with pytest.raises(GroupNotFoundError):
        await store.delete_group("no-such-group", actor=OWNER)


# --------------------------------------------------------------------------- #
# built-in public group
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("backend", BACKENDS)
async def test_public_group_is_present_constant_true_and_unmaterialized(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    pub = await store.get_group(PUBLIC)
    assert pub is not None and pub.built_in and pub.owner_subject == ""
    # Membership is constant-true for anyone, with ZERO materialized rows.
    assert await store.is_member("anyone:at:all", PUBLIC) is True
    assert await store.list_members(PUBLIC) == []
    assert PUBLIC in await store.groups_for_subject(BOB)


@pytest.mark.parametrize("backend", BACKENDS)
async def test_public_group_is_not_deletable_or_member_editable(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    with pytest.raises(GroupInvariantError):
        await store.delete_group(PUBLIC, actor=OWNER)
    with pytest.raises(GroupInvariantError):
        await store.add_member(PUBLIC, ALICE, added_by=OWNER)


# --------------------------------------------------------------------------- #
# add / remove / list members
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("backend", BACKENDS)
async def test_add_member_writes_audited_row_and_provisions_the_user(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    g = await store.create_group("team", owner_subject=OWNER)
    # BOB has never logged in — add_member must pre-provision a users row.
    assert await store.get(BOB) is None
    m = await store.add_member(g.id, BOB, added_by=OWNER)
    assert m.id and m.added_at and m.added_by == OWNER
    assert m.group_id == g.id and m.subject == BOB and m.active
    provisioned = await store.get(BOB)
    assert provisioned is not None and provisioned.provisional is True

    assert [x.subject for x in await store.list_members(g.id)] == [BOB]
    assert await store.is_member(BOB, g.id) is True
    assert [x.id for x in await store.list_groups_for_member(BOB)] == [g.id]


@pytest.mark.parametrize("backend", BACKENDS)
async def test_duplicate_active_membership_rejected_but_readd_after_remove_ok(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    g = await store.create_group("team", owner_subject=OWNER)
    first = await store.add_member(g.id, ALICE, added_by=OWNER)
    with pytest.raises(GroupInvariantError):
        await store.add_member(g.id, ALICE, added_by=OWNER)
    removed = await store.remove_member(g.id, ALICE, removed_by=OWNER)
    assert removed is not None and removed.id == first.id
    assert removed.removed_by == OWNER and removed.removed_at
    # Re-add after removal mints a NEW active row (audit keeps the old one).
    second = await store.add_member(g.id, ALICE, added_by=OWNER)
    assert second.active and second.id != first.id
    assert [x.subject for x in await store.list_members(g.id)] == [ALICE]


@pytest.mark.parametrize("backend", BACKENDS)
async def test_remove_nonmember_is_a_noop(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    g = await store.create_group("team", owner_subject=OWNER)
    assert await store.remove_member(g.id, ALICE, removed_by=OWNER) is None


@pytest.mark.parametrize("backend", BACKENDS)
async def test_add_member_to_unknown_group_raises(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    with pytest.raises(GroupNotFoundError):
        await store.add_member("no-such-group", ALICE, added_by=OWNER)


@pytest.mark.parametrize("backend", BACKENDS)
async def test_no_nesting_a_group_id_cannot_be_a_member(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    outer = await store.create_group("outer", owner_subject=OWNER)
    inner = await store.create_group("inner", owner_subject=OWNER)
    with pytest.raises(GroupInvariantError):
        await store.add_member(outer.id, inner.id, added_by=OWNER)  # nesting
    # public (also a group id) may not be nested either.
    with pytest.raises(GroupInvariantError):
        await store.add_member(outer.id, PUBLIC, added_by=OWNER)


# --------------------------------------------------------------------------- #
# groups_for_subject — exactly the owned-or-member set (plus public)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("backend", BACKENDS)
async def test_groups_for_subject_is_active_membership_plus_public(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    g1 = await store.create_group("g1", owner_subject=OWNER)
    g2 = await store.create_group("g2", owner_subject=OWNER)
    await store.create_group("g3", owner_subject=OWNER)  # ALICE not a member
    await store.add_member(g1.id, ALICE, added_by=OWNER)
    await store.add_member(g2.id, ALICE, added_by=OWNER)
    await store.add_member(g1.id, BOB, added_by=OWNER)

    assert await store.groups_for_subject(ALICE) == {PUBLIC, g1.id, g2.id}
    # Removing a membership drops it from the set immediately.
    await store.remove_member(g2.id, ALICE, removed_by=OWNER)
    assert await store.groups_for_subject(ALICE) == {PUBLIC, g1.id}
    # Deleting a group drops it too, even while a member row lingers.
    await store.delete_group(g1.id, actor=OWNER)
    assert await store.groups_for_subject(ALICE) == {PUBLIC}


# --------------------------------------------------------------------------- #
# the #245 seam: a share to a real group applies to its active members
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("backend", BACKENDS)
async def test_group_share_reaches_members_via_grants_for_subject(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    g = await store.create_group("team", owner_subject=OWNER)
    await store.add_member(g.id, ALICE, added_by=OWNER)
    direct = await store.grant(COLL, "user", ALICE, "read", granted_by=OWNER)
    via_group = await store.grant("col-team", "group", g.id, "read", granted_by=OWNER)
    pub = await store.grant("col-open", "group", PUBLIC, "read", granted_by=OWNER)
    other = await store.grant(COLL, "user", BOB, "read", granted_by=OWNER)

    ids = {r.id for r in await store.grants_for_subject(ALICE)}
    assert ids == {direct.id, via_group.id, pub.id}  # direct + real group + public
    assert other.id not in ids  # BOB's direct grant is not ALICE's
    # BOB is not a group member: only his direct grant + public reach him.
    assert {r.id for r in await store.grants_for_subject(BOB)} == {other.id, pub.id}

    # Remove ALICE from the group -> the group share stops applying to her.
    await store.remove_member(g.id, ALICE, removed_by=OWNER)
    assert {r.id for r in await store.grants_for_subject(ALICE)} == {direct.id, pub.id}


# --------------------------------------------------------------------------- #
# sqlite durability + additive migration
# --------------------------------------------------------------------------- #


async def test_sqlite_groups_survive_reopen(tmp_path):
    path = str(tmp_path / "durable.db")
    g = await SqliteGroupStore(path).create_group("team", owner_subject=OWNER)
    store2 = SqliteGroupStore(path)
    await store2.add_member(g.id, ALICE, added_by=OWNER)
    reopened = SqliteGroupStore(path)
    assert [x.id for x in await reopened.list_groups_owned_by(OWNER)] == [g.id]
    assert [x.subject for x in await reopened.list_members(g.id)] == [ALICE]


async def test_sqlite_tolerates_group_tables_from_an_older_build(tmp_path):
    """Additive-only migration: narrow ``groups``/``group_members`` tables are
    widened by ensure_columns rather than crashing on the first SELECT."""
    path = str(tmp_path / "old.db")
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE groups (id TEXT PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO groups (id, name) VALUES ('legacy1', 'old-team')")
        conn.execute(
            "CREATE TABLE group_members (id TEXT PRIMARY KEY, group_id TEXT, subject TEXT)"
        )
        conn.execute(
            "INSERT INTO group_members (id, group_id, subject) "
            "VALUES ('lm1', 'legacy1', ?)",
            (ALICE,),
        )
    store = SqliteGroupStore(path)  # runs ensure_columns + index + seed DDL
    got = await store.get_group("legacy1")
    assert got is not None and got.id == "legacy1"
    assert [m.subject for m in await store.list_members("legacy1")] == [ALICE]
    # A subsequent write still works against the widened tables.
    await store.add_member("legacy1", BOB, added_by=OWNER)


# --------------------------------------------------------------------------- #
# chain identity — one object, one database, now four tables
# --------------------------------------------------------------------------- #


async def test_every_group_store_is_also_an_acl_and_user_store(tmp_path):
    for store in _stores(tmp_path).values():
        assert isinstance(store, AclStore) and isinstance(store, UserStore)
        # Drive a users upsert AND a share grant AND a group through one object.
        await store.upsert_seen(ALICE, "bvbrc")
        assert (await store.get(ALICE)) is not None
        await store.grant(COLL, "user", OWNER, "owner", granted_by="")
        assert await store.owner_of(COLL) == OWNER
        g = await store.create_group("team", owner_subject=OWNER)
        assert (await store.get_group(g.id)) is not None
        # ...AND a service account, three subclass levels down from the users
        # table that defines it (#258).
        svc = await store.create_service_account("svc-groups", OWNER, purpose="test")
        assert svc.is_service and (await store.list_service_accounts()) == [svc]
        # add_member pre-provisions a users row; naming the service account as
        # a member must not disturb its kind.
        await store.add_member(g.id, "svc-groups", added_by=OWNER)
        still = await store.get("svc-groups")
        assert still is not None and still.is_service and still.created_by == OWNER


# --------------------------------------------------------------------------- #
# postgres — schema parity without a server, round-trip with one
# --------------------------------------------------------------------------- #


async def test_postgres_store_shares_the_sqlite_schema():
    """Both SQL backends render the same tables from the same DDL strings.
    TEXT/INTEGER only: no JSONB, no TIMESTAMPTZ (ISO-8601 text)."""
    from ragstack import group_store as gs

    for stmt in (gs._GROUPS_DDL, gs._GROUP_MEMBERS_DDL, *gs._GROUPS_INDEXES):
        assert "JSONB" not in stmt.upper() and "TIMESTAMP" not in stmt.upper()
    assert set(gs._GROUPS_COLUMNS) | {"id"} == set(gs._GROUP_COLUMNS)
    assert set(gs._GROUP_MEMBERS_COLUMNS) | {"id"} == set(gs._MEMBER_COLUMNS)
    for name, frag in {**gs._GROUPS_COLUMNS, **gs._GROUP_MEMBERS_COLUMNS}.items():
        assert "NOT NULL" not in frag or "DEFAULT" in frag, name
    name_idx, member_idx = gs._GROUPS_INDEXES
    assert "UNIQUE INDEX" in name_idx and "WHERE deleted_at = ''" in name_idx
    assert "UNIQUE INDEX" in member_idx and "WHERE removed_at = ''" in member_idx
    # Distinct advisory-lock key from the shares DDL's.
    from ragstack import acl_store as acl
    assert gs._GROUPS_DDL_LOCK_KEY != acl._SHARES_DDL_LOCK_KEY
    assert isinstance(PostgresGroupStore("postgresql://x/y"), object)


@pytest.mark.skipif(
    not os.environ.get("RAGSTACK_TEST_POSTGRES_DSN"),
    reason="set RAGSTACK_TEST_POSTGRES_DSN to exercise the postgres backend",
)
async def test_postgres_round_trip():
    store = PostgresGroupStore(os.environ["RAGSTACK_TEST_POSTGRES_DSN"])
    try:
        assert (await store.get_group(PUBLIC)) is not None
        assert await store.is_member("x:y", PUBLIC) is True
        g = await store.create_group("pg-rt-team", owner_subject=OWNER)
        m = await store.add_member(g.id, ALICE, added_by=OWNER)
        assert await store.is_member(ALICE, g.id) is True
        with pytest.raises(GroupInvariantError):
            await store.add_member(g.id, ALICE, added_by=OWNER)
        assert await store.groups_for_subject(ALICE) >= {PUBLIC, g.id}
        via = await store.grant("col-pg-team", "group", g.id, "read", granted_by=OWNER)
        assert via.id in {r.id for r in await store.grants_for_subject(ALICE)}
        assert (await store.remove_member(g.id, ALICE, removed_by=OWNER)).id == m.id
        deleted = await store.delete_group(g.id, actor=OWNER)
        assert not deleted.active
        with pytest.raises(GroupInvariantError):
            await store.delete_group(PUBLIC, actor=OWNER)
    finally:
        await store.close()


# --------------------------------------------------------------------------- #
# backend selection + singleton
# --------------------------------------------------------------------------- #


async def test_backend_selection(tmp_path):
    assert isinstance(make_group_store(_settings(tmp_path)), InMemoryGroupStore)
    assert isinstance(
        make_group_store(_settings(tmp_path, user_store_backend="sqlite")),
        SqliteGroupStore,
    )
    assert isinstance(
        make_group_store(
            _settings(tmp_path, user_store_backend="postgres",
                      user_store_dsn="postgresql://x/y")
        ),
        PostgresGroupStore,
    )
    assert isinstance(
        make_group_store(
            _settings(tmp_path, user_store_backend="postgres",
                      postgres_dsn="postgresql://shared/db")
        ),
        PostgresGroupStore,
    )
    assert isinstance(
        make_group_store(_settings(tmp_path, user_store_backend="wat")),
        InMemoryGroupStore,
    )


async def test_get_group_store_singleton_rebuilds_on_settings_change(tmp_path, monkeypatch):
    from ragstack import group_store

    monkeypatch.setattr(group_store.settings, "user_store_backend", "memory")
    group_store.reset_group_store()
    try:
        first = group_store.get_group_store()
        assert first is group_store.get_group_store()  # cached
        assert isinstance(first, InMemoryGroupStore)

        monkeypatch.setattr(group_store.settings, "user_store_backend", "sqlite")
        monkeypatch.setattr(
            group_store.settings, "user_store_path", str(tmp_path / "singleton.db")
        )
        rebuilt = group_store.get_group_store()
        assert rebuilt is not first and isinstance(rebuilt, SqliteGroupStore)

        injected = InMemoryGroupStore()
        group_store.set_group_store(injected)
        assert group_store.get_group_store() is injected
    finally:
        group_store.reset_group_store()
