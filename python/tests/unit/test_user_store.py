"""The user-profile store (``ragstack.user_store``, ADR-0004 decision 1).

The row is keyed on the tenant string ``f"{issuer}:{sub}"`` — never an email —
and the semantics under test are the ones auth depends on: ``upsert_seen`` is
idempotent (it fires once per identity-cache expiry, not once per session),
never blanks out profile data a previous token provided, and flips a
provisional grantee row into a real profile without losing ``first_seen_at``.

Service accounts (issue #258) share the table and add their own invariants: a
service subject is colon-free (the bearer namespace is ``issuer:sub``, so the
two can never collide), ``upsert_seen``/``ensure_provisional`` can neither mint
a service row nor downgrade one to ``human``, a real human row can never be
converted into a service account silently, and disabling is soft state
(``disabled_at``), never a DELETE.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
from types import SimpleNamespace

import pytest

from ragstack.user_store import (
    KIND_HUMAN,
    KIND_SERVICE,
    USER_ROLE_ADMIN,
    USER_ROLE_USER,
    InMemoryUserStore,
    LastAdminError,
    PostgresUserStore,
    SqliteUserStore,
    UserInvariantError,
    UserNotFoundError,
    UserRecord,
    UserRoleError,
    make_user_store,
)

pytestmark = pytest.mark.asyncio

SUBJECT = "bvbrc:alice@patricbrc.org"
ISSUER = "bvbrc"
SVC = "svc-askclark"
ADMIN = "bvbrc:admin@patricbrc.org"
BACKENDS = ["memory", "sqlite"]


def _settings(tmp_path, **over):
    base: dict = {
        "user_store_backend": "memory",
        "user_store_path": str(tmp_path / "users.db"),
        "user_store_dsn": "",
        "postgres_dsn": "",
    }
    base.update(over)
    return SimpleNamespace(**base)


def _stores(tmp_path):
    """Every locally-runnable backend. Postgres needs a server, so it is covered
    separately (schema parity below + an opt-in round-trip)."""
    return {
        "memory": InMemoryUserStore(),
        "sqlite": SqliteUserStore(str(tmp_path / "rt.db")),
    }


# --------------------------------------------------------------------------- #
# upsert_seen semantics
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_upsert_seen_creates_a_full_profile_row(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    rec = await store.upsert_seen(SUBJECT, ISSUER, email="a@x.org", display_name="Alice")
    assert rec.subject == SUBJECT and rec.issuer == ISSUER
    assert rec.email == "a@x.org" and rec.display_name == "Alice"
    assert rec.provisional is False
    assert rec.first_seen_at and rec.last_seen_at == rec.first_seen_at

    stored = await store.get(SUBJECT)
    assert stored == rec


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_upsert_seen_restamps_last_seen_but_never_first_seen(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    first = await store.upsert_seen(SUBJECT, ISSUER)
    second = await store.upsert_seen(SUBJECT, ISSUER)
    assert second.first_seen_at == first.first_seen_at
    assert second.last_seen_at >= first.last_seen_at


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_upsert_seen_never_blanks_out_existing_profile_data(tmp_path, backend):
    """A later token that carries no profile claims (e.g. a BV-BRC token after
    an OIDC login, or an ID token without the email scope) must not erase what
    an earlier one provided."""
    store = _stores(tmp_path)[backend]
    await store.upsert_seen(SUBJECT, ISSUER, email="a@x.org", display_name="Alice")
    rec = await store.upsert_seen(SUBJECT, ISSUER)  # claimless authentication
    assert rec.email == "a@x.org"
    assert rec.display_name == "Alice"


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_upsert_seen_fills_in_newly_available_claims(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    await store.upsert_seen(SUBJECT, ISSUER)
    rec = await store.upsert_seen(SUBJECT, ISSUER, email="a@x.org", display_name="Alice")
    assert rec.email == "a@x.org" and rec.display_name == "Alice"


# --------------------------------------------------------------------------- #
# ensure_provisional — the "name a grantee before their first login" path
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_ensure_provisional_creates_only_when_absent(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    rec = await store.ensure_provisional(SUBJECT, ISSUER)
    assert rec.provisional is True
    assert rec.first_seen_at  # creation time is recorded...
    assert rec.last_seen_at == ""  # ...but the user has never actually been seen

    again = await store.ensure_provisional(SUBJECT, ISSUER)
    assert again == rec  # a second call returns the row unchanged


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_ensure_provisional_never_demotes_a_real_profile(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    real = await store.upsert_seen(SUBJECT, ISSUER, email="a@x.org")
    rec = await store.ensure_provisional(SUBJECT, ISSUER)
    assert rec == real  # untouched: still non-provisional, timestamps intact


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_first_login_flips_provisional_and_preserves_first_seen(tmp_path, backend):
    """The ADR-0004 hand-off: a grantee named before their first login becomes
    a real profile on that login — same row, provisional off, and the
    row-creation time survives."""
    store = _stores(tmp_path)[backend]
    provisional = await store.ensure_provisional(SUBJECT, ISSUER)
    rec = await store.upsert_seen(SUBJECT, ISSUER, display_name="Alice")
    assert rec.provisional is False
    assert rec.first_seen_at == provisional.first_seen_at
    assert rec.last_seen_at
    assert rec.display_name == "Alice"


# --------------------------------------------------------------------------- #
# get / list_users
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_get_missing_subject_is_none(tmp_path, backend):
    assert await _stores(tmp_path)[backend].get("google:nobody") is None


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_list_users_is_oldest_first_and_bounded(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    for i in range(5):
        await store.upsert_seen(f"bvbrc:u{i}@patricbrc.org", ISSUER)
    listed = await store.list_users()
    assert [r.subject for r in listed] == [f"bvbrc:u{i}@patricbrc.org" for i in range(5)]
    assert len(await store.list_users(limit=2)) == 2


async def test_sqlite_store_survives_reopen(tmp_path):
    path = str(tmp_path / "durable.db")
    await SqliteUserStore(path).upsert_seen(SUBJECT, ISSUER, email="a@x.org")
    rec = await SqliteUserStore(path).get(SUBJECT)
    assert rec is not None and rec.email == "a@x.org"


async def test_sqlite_tolerates_a_table_created_by_an_older_build(tmp_path):
    """Additive-only migration: a ``users`` table missing later columns must be
    widened by ``ensure_columns`` rather than crash on the first SELECT."""
    path = str(tmp_path / "old.db")
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE users (subject TEXT PRIMARY KEY, issuer TEXT)")
        conn.execute("INSERT INTO users (subject, issuer) VALUES ('bvbrc:legacy', 'bvbrc')")
    store = SqliteUserStore(path)  # runs ensure_columns
    rec = await store.get("bvbrc:legacy")
    # Full-model equality pins "SQL column default == pydantic field default"...
    assert rec == UserRecord(subject="bvbrc:legacy", issuer="bvbrc")
    # ...and, explicitly, a pre-#258 row backfills to an enabled human account
    # rather than to an empty/NULL kind.
    assert rec is not None
    assert rec.kind == KIND_HUMAN and rec.disabled_at == ""
    assert rec.enabled is True and rec.is_service is False
    await store.upsert_seen(SUBJECT, ISSUER)
    assert len(await store.list_users()) == 2


# --------------------------------------------------------------------------- #
# service accounts (#258) — the machine-identity half of the table
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("backend", BACKENDS)
async def test_create_service_account_records_provenance(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    rec = await store.create_service_account(SVC, ADMIN, purpose="ASM read-only")
    assert rec.subject == SVC and rec.kind == KIND_SERVICE
    assert rec.is_service is True and rec.enabled is True
    assert rec.created_by == ADMIN and rec.purpose == "ASM read-only"
    # Deliberately created, never "seen": provisional off, never authenticated.
    assert rec.provisional is False
    assert rec.first_seen_at and rec.last_seen_at == ""
    assert (await store.get(SVC)) == rec


@pytest.mark.parametrize("backend", BACKENDS)
async def test_create_service_account_rejects_a_colon_in_the_subject(tmp_path, backend):
    """The colon rule IS the namespace partition: a bearer subject is always
    ``issuer:sub``, so a colon-free service subject can never collide with one
    (the data-layer form of the #243 startup guard)."""
    store = _stores(tmp_path)[backend]
    for bad in ("bvbrc:svc", "svc:", ":svc", "a:b:c"):
        with pytest.raises(UserInvariantError, match="colon-free"):
            await store.create_service_account(bad, ADMIN)
        assert (await store.get(bad)) is None  # and nothing was written


@pytest.mark.parametrize("backend", BACKENDS)
async def test_create_service_account_rejects_the_reserved_tenants(tmp_path, backend):
    """``default`` and ``public`` are shared fallback tenants, not one caller's
    identity: every valid-but-unmapped API key (and the whole keyless dev path)
    resolves to ``default``. A service row on one would make a single disable a
    DEPLOYMENT-WIDE 401 — including for the admin key that would call /enable,
    which is a lockout no API route can undo."""
    store = _stores(tmp_path)[backend]
    for bad in ("default", "public"):
        with pytest.raises(UserInvariantError, match="reserved"):
            await store.create_service_account(bad, ADMIN)
        assert (await store.get(bad)) is None  # and nothing was written


@pytest.mark.parametrize("backend", BACKENDS)
async def test_create_service_account_requires_subject_and_creator(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    with pytest.raises(UserInvariantError, match="subject"):
        await store.create_service_account("", ADMIN)
    with pytest.raises(UserInvariantError, match="created_by"):
        await store.create_service_account(SVC, "")


@pytest.mark.parametrize("backend", BACKENDS)
async def test_create_service_account_is_idempotent_ish(tmp_path, backend):
    """A re-run of a provisioning script is a no-op — it must not rewrite the
    purpose, the creation time, or (crucially) a disabled_at somebody set."""
    store = _stores(tmp_path)[backend]
    first = await store.create_service_account(SVC, ADMIN, purpose="ASM read-only")
    await store.disable_service_account(SVC, ADMIN)
    again = await store.create_service_account(SVC, "someone:else", purpose="hijack")
    assert again.created_by == ADMIN and again.purpose == "ASM read-only"
    assert again.first_seen_at == first.first_seen_at
    assert again.enabled is False  # re-creation did not silently re-enable it


@pytest.mark.parametrize("backend", BACKENDS)
async def test_create_service_account_refuses_an_existing_human(tmp_path, backend):
    """A human row cannot be converted into a machine credential silently —
    that is a privilege event, so it must be a typed error."""
    store = _stores(tmp_path)[backend]
    await store.upsert_seen("alice", ISSUER, email="a@x.org")  # colon-free human
    with pytest.raises(UserInvariantError, match="already exists"):
        await store.create_service_account("alice", ADMIN)
    rec = await store.get("alice")
    assert rec is not None and rec.kind == KIND_HUMAN and rec.email == "a@x.org"


@pytest.mark.parametrize("backend", BACKENDS)
async def test_create_service_account_claims_a_never_seen_provisional_row(tmp_path, backend):
    """``ensure_provisional`` is called opportunistically with arbitrary
    subjects (share grantee, group member), so naming a service account before
    creating it must not permanently block creation. Nobody has ever
    authenticated as that placeholder, so claiming it is a creation."""
    store = _stores(tmp_path)[backend]
    placeholder = await store.ensure_provisional(SVC, "")
    assert placeholder.kind == KIND_HUMAN and placeholder.provisional is True

    rec = await store.create_service_account(SVC, ADMIN, purpose="ASM")
    assert rec.kind == KIND_SERVICE and rec.provisional is False
    assert rec.created_by == ADMIN
    assert rec.first_seen_at == placeholder.first_seen_at  # row-creation time survives
    assert (await store.get(SVC)) == rec


@pytest.mark.parametrize("backend", BACKENDS)
async def test_disable_and_enable_are_soft_and_idempotent(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    created = await store.create_service_account(SVC, ADMIN)

    off = await store.disable_service_account(SVC, ADMIN)
    assert off.enabled is False and off.disabled_at and off.disabled_by == ADMIN
    assert off.kind == KIND_SERVICE and off.created_by == ADMIN
    assert (await store.get(SVC)) == off  # the row still exists — soft state
    assert SVC in [r.subject for r in await store.list_users()]

    again = await store.disable_service_account(SVC, "bvbrc:other")
    assert again.disabled_at == off.disabled_at  # idempotent: not re-stamped

    on = await store.enable_service_account(SVC, "bvbrc:second-admin")
    assert on.enabled is True
    assert on.first_seen_at == created.first_seen_at
    assert (await store.enable_service_account(SVC, ADMIN)) == on  # idempotent


@pytest.mark.parametrize("backend", BACKENDS)
async def test_enable_keeps_the_disable_audit_trail(tmp_path, backend):
    """ADR-0004 decision 6: the audit trail is the point. A re-enable must not
    leave a row byte-identical to one nobody ever disabled — who revoked the
    credential, when, and who put it back all survive."""
    store = _stores(tmp_path)[backend]
    await store.create_service_account(SVC, ADMIN)
    off = await store.disable_service_account(SVC, ADMIN)

    on = await store.enable_service_account(SVC, "bvbrc:second-admin")
    assert on.enabled is True
    # The disable stamp is history, NOT state: it stays put.
    assert on.disabled_by == ADMIN and on.disabled_at == off.disabled_at
    # ...and the enable is recorded against the admin who performed it.
    assert on.enabled_by == "bvbrc:second-admin" and on.enabled_at
    assert (await store.get(SVC)) == on  # and it is what was persisted

    # A second disable overwrites only its own pair, and flips the state back.
    again = await store.disable_service_account(SVC, "bvbrc:third-admin")
    assert again.enabled is False
    assert again.disabled_by == "bvbrc:third-admin"
    assert again.enabled_by == "bvbrc:second-admin"  # the enable is still on record


@pytest.mark.parametrize("backend", BACKENDS)
async def test_disable_rejects_unknown_and_human_subjects(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    with pytest.raises(UserNotFoundError):
        await store.disable_service_account("nope", ADMIN)
    await store.upsert_seen(SUBJECT, ISSUER)
    with pytest.raises(UserInvariantError, match="only service accounts"):
        await store.disable_service_account(SUBJECT, ADMIN)
    with pytest.raises(UserInvariantError, match="actor"):
        await store.disable_service_account(SVC, "")


@pytest.mark.parametrize("backend", BACKENDS)
async def test_ensure_provisional_never_downgrades_a_service_row(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    svc = await store.create_service_account(SVC, ADMIN, purpose="ASM")
    assert (await store.ensure_provisional(SVC, "")) == svc  # returned untouched
    assert (await store.get(SVC)) == svc


@pytest.mark.parametrize("backend", BACKENDS)
async def test_upsert_seen_never_reclassifies_or_re_enables_a_service_row(tmp_path, backend):
    """The colon rule means a bearer identity and a service account cannot be
    the same subject — but the first-auth hook must be structurally unable to
    flip ``kind`` or clear ``disabled_at`` even if one somehow is."""
    store = _stores(tmp_path)[backend]
    await store.create_service_account(SVC, ADMIN, purpose="ASM")
    await store.disable_service_account(SVC, ADMIN)

    rec = await store.upsert_seen(SVC, "bvbrc", email="attacker@x.org")
    assert rec.kind == KIND_SERVICE
    assert rec.created_by == ADMIN and rec.purpose == "ASM"
    assert rec.disabled_by == ADMIN and rec.disabled_at and rec.enabled is False
    assert (await store.get(SVC)) == rec  # and that is what actually landed


@pytest.mark.parametrize("backend", BACKENDS)
async def test_list_service_accounts_filters_by_kind_and_creator(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    await store.upsert_seen(SUBJECT, ISSUER)  # a human, must not appear
    await store.create_service_account("svc-a", ADMIN)
    await store.create_service_account("svc-b", ADMIN)
    await store.create_service_account("svc-c", "bvbrc:other")

    listed = await store.list_service_accounts()
    assert [r.subject for r in listed] == ["svc-a", "svc-b", "svc-c"]
    assert all(r.kind == KIND_SERVICE for r in listed)
    assert [r.subject for r in await store.list_service_accounts(created_by=ADMIN)] == [
        "svc-a", "svc-b",
    ]
    assert len(await store.list_service_accounts(limit=2)) == 2

    # Disabled accounts stay listed — soft state, not a deletion.
    await store.disable_service_account("svc-a", ADMIN)
    assert "svc-a" in [r.subject for r in await store.list_service_accounts()]


async def test_sqlite_service_account_survives_reopen(tmp_path):
    """``kind`` and the disabled stamp are durable, not process-local state."""
    path = str(tmp_path / "svc.db")
    await SqliteUserStore(path).create_service_account(SVC, ADMIN, purpose="ASM")
    await SqliteUserStore(path).disable_service_account(SVC, ADMIN)
    rec = await SqliteUserStore(path).get(SVC)
    assert rec is not None
    assert rec.kind == KIND_SERVICE and rec.created_by == ADMIN and rec.purpose == "ASM"
    assert rec.enabled is False
    assert [r.subject for r in await SqliteUserStore(path).list_service_accounts()] == [SVC]


async def test_sqlite_service_account_on_a_table_created_by_an_older_build(tmp_path):
    """The #258 columns must arrive additively: a pre-existing users table gets
    them from ``ensure_columns``, and a service account is creatable there."""
    path = str(tmp_path / "old-svc.db")
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE users (subject TEXT PRIMARY KEY, issuer TEXT)")
        conn.execute("INSERT INTO users (subject, issuer) VALUES ('bvbrc:legacy', 'bvbrc')")
    store = SqliteUserStore(path)  # runs ensure_columns for all five new columns
    rec = await store.create_service_account(SVC, ADMIN, purpose="ASM")
    assert rec.kind == KIND_SERVICE
    assert [r.subject for r in await store.list_service_accounts()] == [SVC]
    legacy = await store.get("bvbrc:legacy")
    assert legacy is not None and legacy.kind == KIND_HUMAN
    assert legacy.enabled is True  # a widened human row is not accidentally off


async def test_a_row_disabled_before_the_state_column_existed_stays_disabled(tmp_path):
    """Upgrade safety: when state lived in ``disabled_at``, a disabled row had
    no ``disabled`` column. ``ensure_columns`` backfills that flag with 0, so a
    naive read would silently RESURRECT a revoked credential. It must not."""
    path = str(tmp_path / "pre-flag.db")
    store = SqliteUserStore(path)
    await store.create_service_account(SVC, ADMIN)
    with sqlite3.connect(path) as conn:  # emulate the old writer exactly
        conn.execute(
            "UPDATE users SET disabled = 0, disabled_by = ?, disabled_at = ?, "
            "enabled_by = '', enabled_at = '' WHERE subject = ?",
            (ADMIN, "2026-01-01T00:00:00+00:00", SVC),
        )
    rec = await store.get(SVC)
    assert rec is not None and rec.enabled is False
    # ...while a row that was disabled and then legitimately re-enabled reads
    # back ENABLED, disable stamp and all.
    on = await store.enable_service_account(SVC, ADMIN)
    assert on.enabled is True and on.disabled_at and on.enabled_at
    assert (await store.get(SVC)) == on


# --------------------------------------------------------------------------- #
# the role column — the bearer path's stored admin source
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("backend", BACKENDS)
async def test_set_role_grants_and_records_who_did_it(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    await store.upsert_seen(SUBJECT, ISSUER)
    rec = await store.set_role(SUBJECT, USER_ROLE_ADMIN, actor=ADMIN)
    assert rec.role == USER_ROLE_ADMIN and rec.is_admin is True
    assert rec.role_set_by == ADMIN and rec.role_set_at
    assert (await store.get(SUBJECT)) == rec
    assert await store.count_admins() == 1


@pytest.mark.parametrize("backend", BACKENDS)
async def test_a_fresh_row_is_not_admin(tmp_path, backend):
    """'' is the "never set" sentinel, and it must read as NOT admin — the only
    safe direction for a privilege column."""
    store = _stores(tmp_path)[backend]
    rec = await store.upsert_seen(SUBJECT, ISSUER)
    assert rec.role == "" and rec.is_admin is False
    assert await store.count_admins() == 0


@pytest.mark.parametrize("backend", BACKENDS)
async def test_set_role_revokes_without_erasing_the_audit(tmp_path, backend):
    """ADR-0004 decision 6: a revoke records who revoked; it never blanks the
    fact that a change happened."""
    store = _stores(tmp_path)[backend]
    await store.upsert_seen(SUBJECT, ISSUER)
    await store.set_role(SUBJECT, USER_ROLE_ADMIN, actor=ADMIN)
    off = await store.set_role(SUBJECT, USER_ROLE_USER, actor="bvbrc:second-admin")
    assert off.role == USER_ROLE_USER and off.is_admin is False
    assert off.role_set_by == "bvbrc:second-admin" and off.role_set_at
    assert await store.count_admins() == 0


@pytest.mark.parametrize("backend", BACKENDS)
async def test_set_role_is_idempotent_and_does_not_re_stamp(tmp_path, backend):
    """The audit answers "who last CHANGED this"; a no-op re-grant overwriting
    it would erase the real granter."""
    store = _stores(tmp_path)[backend]
    await store.upsert_seen(SUBJECT, ISSUER)
    first = await store.set_role(SUBJECT, USER_ROLE_ADMIN, actor=ADMIN)
    again = await store.set_role(SUBJECT, USER_ROLE_ADMIN, actor="bvbrc:someone-else")
    assert again.role_set_by == ADMIN and again.role_set_at == first.role_set_at
    # And revoking a row that never had a role is a no-op too: '' and 'user' are
    # the same state, so it must not manufacture an audit event.
    await store.upsert_seen(ADMIN, ISSUER)
    plain = await store.set_role(ADMIN, USER_ROLE_USER, actor=SUBJECT)
    assert plain.role == "" and plain.role_set_by == ""


@pytest.mark.parametrize("backend", BACKENDS)
async def test_set_role_rejects_an_unknown_role_with_its_own_error(tmp_path, backend):
    """A typo is a malformed REQUEST (400 at the router), which must stay
    distinguishable from a refused state change (409)."""
    store = _stores(tmp_path)[backend]
    await store.upsert_seen(SUBJECT, ISSUER)
    for bad in ("wizard", "ADMIN", "researcher", ""):
        with pytest.raises(UserRoleError, match="not one of"):
            await store.set_role(SUBJECT, bad, actor=ADMIN)
    assert (await store.get(SUBJECT)).role == ""


@pytest.mark.parametrize("backend", BACKENDS)
async def test_set_role_requires_an_existing_row_and_an_actor(tmp_path, backend):
    """Never a create: minting a row from a subject string would let a typo'd
    issuer prefix create a permanent admin nobody can authenticate as, and hide
    the real mistake."""
    store = _stores(tmp_path)[backend]
    with pytest.raises(UserNotFoundError):
        await store.set_role(SUBJECT, USER_ROLE_ADMIN, actor=ADMIN)
    assert (await store.get(SUBJECT)) is None
    await store.upsert_seen(SUBJECT, ISSUER)
    with pytest.raises(UserInvariantError, match="actor"):
        await store.set_role(SUBJECT, USER_ROLE_ADMIN, actor="")


@pytest.mark.parametrize("backend", BACKENDS)
async def test_set_role_refuses_a_service_account(tmp_path, backend):
    """An API-key principal's role comes from API_KEY_ROLES; this column is read
    only on the bearer path, so writing it here would be an inert grant that
    blurs the two disjoint namespaces."""
    store = _stores(tmp_path)[backend]
    await store.create_service_account(SVC, ADMIN)
    with pytest.raises(UserInvariantError, match="API_KEY_ROLES"):
        await store.set_role(SVC, USER_ROLE_ADMIN, actor=ADMIN)
    assert (await store.get(SVC)).role == ""


# --------------------------------------------------------------------------- #
# the last-admin guard: a refusal about the whole TABLE, so it lives in the write
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("backend", BACKENDS)
async def test_the_last_admin_guard_refuses_and_writes_nothing(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    await store.upsert_seen(SUBJECT, ISSUER)
    await store.set_role(SUBJECT, USER_ROLE_ADMIN, actor=ADMIN)
    with pytest.raises(LastAdminError, match="last stored admin"):
        await store.set_role(
            SUBJECT, USER_ROLE_USER, actor=ADMIN, require_remaining_admin=True
        )
    # The whole transaction rolled back, not just the refusal.
    rec = await store.get(SUBJECT)
    assert rec is not None and rec.is_admin is True
    assert await store.count_admins() == 1


@pytest.mark.parametrize("backend", BACKENDS)
async def test_the_guard_is_opt_in_and_only_covers_a_real_demotion(tmp_path, backend):
    """Everything the guard must NOT refuse: it is off by default, a grant can
    never strand a deployment, a penultimate admin is revocable, and revoking
    somebody who was never admin is not a demotion at all."""
    store = _stores(tmp_path)[backend]
    never_admin = "bvbrc:carol@patricbrc.org"
    for subject in (SUBJECT, ADMIN, never_admin):
        await store.upsert_seen(subject, ISSUER)
    # A grant, under the guard: allowed.
    await store.set_role(SUBJECT, USER_ROLE_ADMIN, actor=ADMIN, require_remaining_admin=True)
    # Revoking a never-admin row, under the guard: not a demotion.
    await store.set_role(
        never_admin, USER_ROLE_USER, actor=ADMIN, require_remaining_admin=True
    )
    # A penultimate admin: one is left, so the revoke stands.
    await store.set_role(ADMIN, USER_ROLE_ADMIN, actor=SUBJECT, require_remaining_admin=True)
    await store.set_role(ADMIN, USER_ROLE_USER, actor=SUBJECT, require_remaining_admin=True)
    assert await store.count_admins() == 1
    # ...and without the flag the last admin goes, which is what makes the
    # refusal a caller's decision rather than a store policy.
    await store.set_role(SUBJECT, USER_ROLE_USER, actor=ADMIN)
    assert await store.count_admins() == 0


@pytest.mark.parametrize("backend", BACKENDS)
async def test_the_role_vocabulary_is_checked_before_the_guard(tmp_path, backend):
    """A typo'd role targeting the last admin is a malformed REQUEST (400), not
    a refusal of a revoke the caller never asked for."""
    store = _stores(tmp_path)[backend]
    await store.upsert_seen(SUBJECT, ISSUER)
    await store.set_role(SUBJECT, USER_ROLE_ADMIN, actor=ADMIN)
    with pytest.raises(UserRoleError, match="not one of"):
        await store.set_role(
            SUBJECT, "wizard", actor=ADMIN, require_remaining_admin=True
        )


@pytest.mark.parametrize("backend", BACKENDS)
async def test_concurrent_revokes_cannot_strand_the_deployment(tmp_path, backend):
    """The reason the guard is inside the write. Two revokes of DIFFERENT rows
    race; a check-then-write would have both observe the other's admin, both
    pass, and land on zero. Exactly one must be refused."""
    store = _stores(tmp_path)[backend]
    for subject in (SUBJECT, ADMIN):
        await store.upsert_seen(subject, ISSUER)
        await store.set_role(subject, USER_ROLE_ADMIN, actor="bvbrc:root")

    async def revoke(subject: str):
        # Yield first, so both coroutines are in flight before either writes —
        # which is what a real backend does anyway (sqlite hops to a thread,
        # postgres to a socket).
        await asyncio.sleep(0)
        return await store.set_role(
            subject, USER_ROLE_USER, actor="bvbrc:root", require_remaining_admin=True
        )

    results = await asyncio.gather(
        revoke(SUBJECT), revoke(ADMIN), return_exceptions=True
    )
    refused = [r for r in results if isinstance(r, LastAdminError)]
    assert len(refused) == 1, results
    assert await store.count_admins() == 1


@pytest.mark.parametrize("backend", BACKENDS)
async def test_upsert_seen_never_resets_a_role(tmp_path, backend):
    """THE invariant: ``upsert_seen`` is the first-auth hook and runs on every
    login. If it could assign ``role``, an admin would be demoted by their own
    next sign-in, in both dialects, with no error anywhere."""
    store = _stores(tmp_path)[backend]
    await store.upsert_seen(SUBJECT, ISSUER)
    granted = await store.set_role(SUBJECT, USER_ROLE_ADMIN, actor=ADMIN)

    seen = await store.upsert_seen(SUBJECT, ISSUER, email="a@x.org", display_name="A")
    assert seen.role == USER_ROLE_ADMIN
    assert seen.role_set_by == ADMIN and seen.role_set_at == granted.role_set_at
    assert seen.email == "a@x.org"  # the profile half DID update
    stored = await store.get(SUBJECT)
    assert stored is not None and stored.is_admin is True


@pytest.mark.parametrize("backend", BACKENDS)
async def test_a_role_bearing_placeholder_cannot_become_a_service_account(
    tmp_path, backend
):
    """An admin can be granted before first login. Converting that pending
    grant into a machine credential would carry a bearer role onto an API-key
    identity through a door ``set_role`` deliberately refuses."""
    store = _stores(tmp_path)[backend]
    await store.ensure_provisional(SVC, "")
    await store.set_role(SVC, USER_ROLE_ADMIN, actor=ADMIN)
    with pytest.raises(UserInvariantError, match="revoke it"):
        await store.create_service_account(SVC, ADMIN)
    rec = await store.get(SVC)
    assert rec is not None and rec.kind == KIND_HUMAN


@pytest.mark.parametrize("backend", BACKENDS)
async def test_upgrading_a_placeholder_writes_an_empty_role(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    await store.ensure_provisional(SVC, "")
    rec = await store.create_service_account(SVC, ADMIN)
    assert rec.kind == KIND_SERVICE and rec.role == "" and rec.role_set_by == ""


async def test_sqlite_role_survives_a_reopen(tmp_path):
    path = str(tmp_path / "role.db")
    await SqliteUserStore(path).upsert_seen(SUBJECT, ISSUER)
    await SqliteUserStore(path).set_role(SUBJECT, USER_ROLE_ADMIN, actor=ADMIN)
    rec = await SqliteUserStore(path).get(SUBJECT)
    assert rec is not None and rec.is_admin is True and rec.role_set_by == ADMIN
    assert await SqliteUserStore(path).count_admins() == 1


async def test_a_legacy_row_widened_with_the_role_column_is_not_admin(tmp_path):
    """Additive migration must backfill role='' — any other default would mean
    an upgrade silently made every existing user a superuser."""
    path = str(tmp_path / "old-role.db")
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE users (subject TEXT PRIMARY KEY, issuer TEXT)")
        conn.execute("INSERT INTO users (subject, issuer) VALUES ('bvbrc:legacy', 'bvbrc')")
    store = SqliteUserStore(path)  # runs ensure_columns
    rec = await store.get("bvbrc:legacy")
    assert rec == UserRecord(subject="bvbrc:legacy", issuer="bvbrc")
    assert rec is not None and rec.role == "" and rec.is_admin is False
    assert await store.count_admins() == 0
    # ...and the widened row is still grantable.
    granted = await store.set_role("bvbrc:legacy", USER_ROLE_ADMIN, actor=ADMIN)
    assert granted.is_admin is True


async def test_the_store_role_vocabulary_matches_the_api_one():
    """user_store cannot import ragstack.api.security (that would be a cycle),
    so the two spellings are pinned equal here instead."""
    from ragstack.api.security import ROLE_ADMIN, ROLE_USER

    assert USER_ROLE_ADMIN == ROLE_ADMIN
    assert USER_ROLE_USER == ROLE_USER


# --------------------------------------------------------------------------- #
# postgres — schema parity without a server, round-trip with one
# --------------------------------------------------------------------------- #


async def test_postgres_store_shares_the_sqlite_schema():
    """Both SQL backends must render the same table from the same DDL string.
    TEXT/INTEGER only: no JSONB (sqlite gives it NUMERIC affinity), no
    TIMESTAMPTZ (ISO-8601 text)."""
    from ragstack import user_store as us

    ddl = us._USERS_DDL
    assert "JSONB" not in ddl.upper() and "TIMESTAMP" not in ddl.upper()
    assert set(us._USERS_COLUMNS) | {"subject"} == set(us._COLUMNS)
    # Every additively-added column is nullable or defaulted (sqlite's ALTER
    # TABLE ADD COLUMN forbids NOT NULL without a default).
    for name, frag in us._USERS_COLUMNS.items():
        assert "NOT NULL" not in frag or "DEFAULT" in frag, name
    assert isinstance(PostgresUserStore("postgresql://x/y"), object)
    # The first-auth hook's ON CONFLICT clause is narrowed to profile columns
    # in BOTH dialects, so it is structurally unable to write the
    # identity-class ones (#258). This is the DB-level form of the invariant.
    assert set(us._SEEN_ASSIGN_COLUMNS).isdisjoint(
        {
            "subject", "kind", "created_by", "purpose",
            "disabled", "disabled_by", "disabled_at", "enabled_by", "enabled_at",
            # ``role`` most of all: the first-auth hook runs on EVERY login, so
            # a role in this list would blank an admin's grant minutes after
            # they signed in, in both dialects, with no error anywhere.
            "role", "role_set_by", "role_set_at",
        }
    )
    assert set(us._SEEN_ASSIGN_COLUMNS) < set(us._COLUMNS)
    # The three parallel positional lists stay in lockstep.
    assert len(us._record_to_row(UserRecord(subject="x"))) == len(us._COLUMNS)
    assert len(us._service_update_args(UserRecord(subject="x"))) == len(
        us._SERVICE_SET_COLUMNS
    ) + 1
    assert len(us._role_update_args(UserRecord(subject="x"))) == len(
        us._ROLE_SET_COLUMNS
    ) + 1
    assert set(us._ROLE_SET_COLUMNS) < set(us._COLUMNS)


@pytest.mark.skipif(
    not os.environ.get("RAGSTACK_TEST_POSTGRES_DSN"),
    reason="set RAGSTACK_TEST_POSTGRES_DSN to exercise the postgres backend",
)
async def test_postgres_round_trip():
    store = PostgresUserStore(os.environ["RAGSTACK_TEST_POSTGRES_DSN"])
    subject = "bvbrc:pg-roundtrip@patricbrc.org"
    try:
        provisional = await store.ensure_provisional(subject, ISSUER)
        assert provisional.provisional is True
        rec = await store.upsert_seen(subject, ISSUER, email="pg@x.org")
        assert rec.provisional is False
        assert rec.first_seen_at == provisional.first_seen_at
        assert (await store.get(subject)) == rec
        assert subject in [r.subject for r in await store.list_users(limit=1000)]

        svc = "svc-pg-roundtrip"
        created = await store.create_service_account(svc, ADMIN, purpose="pg")
        assert created.kind == KIND_SERVICE and created.created_by == ADMIN
        off = await store.disable_service_account(svc, ADMIN)
        assert off.enabled is False
        # The narrowed ON CONFLICT list must hold on postgres too.
        seen = await store.upsert_seen(svc, "bvbrc")
        assert seen.kind == KIND_SERVICE and seen.enabled is False
        assert (await store.get(svc)) == seen
        assert svc in [r.subject for r in await store.list_service_accounts(limit=1000)]
        assert (await store.enable_service_account(svc, ADMIN)).enabled is True
        with pytest.raises(UserInvariantError, match="colon-free"):
            await store.create_service_account("bvbrc:nope", ADMIN)
    finally:
        await store.close()


# --------------------------------------------------------------------------- #
# backend selection
# --------------------------------------------------------------------------- #


async def test_backend_selection(tmp_path):
    assert isinstance(make_user_store(_settings(tmp_path)), InMemoryUserStore)
    assert isinstance(
        make_user_store(_settings(tmp_path, user_store_backend="sqlite")),
        SqliteUserStore,
    )
    assert isinstance(
        make_user_store(
            _settings(tmp_path, user_store_backend="postgres",
                      user_store_dsn="postgresql://x/y")
        ),
        PostgresUserStore,
    )
    # The DSN falls back to the shared postgres_dsn when unset.
    assert isinstance(
        make_user_store(
            _settings(tmp_path, user_store_backend="postgres",
                      postgres_dsn="postgresql://shared/db")
        ),
        PostgresUserStore,
    )
    # An unknown backend falls back to memory rather than failing construction
    # (startup's validate_user_store_settings is what fails fast).
    assert isinstance(
        make_user_store(_settings(tmp_path, user_store_backend="wat")),
        InMemoryUserStore,
    )


async def test_validate_user_store_settings_rejects_unknown_backend(monkeypatch):
    from ragstack import user_store

    monkeypatch.setattr(user_store.settings, "user_store_backend", "wat")
    with pytest.raises(RuntimeError, match="user_store_backend"):
        user_store.validate_user_store_settings()
    monkeypatch.setattr(user_store.settings, "user_store_backend", "memory")
    user_store.validate_user_store_settings()  # no raise


async def test_get_user_store_singleton_rebuilds_on_settings_change(tmp_path, monkeypatch):
    from ragstack import user_store

    monkeypatch.setattr(user_store.settings, "user_store_backend", "memory")
    user_store.reset_user_store()
    try:
        first = user_store.get_user_store()
        assert first is user_store.get_user_store()  # cached
        assert isinstance(first, InMemoryUserStore)

        monkeypatch.setattr(user_store.settings, "user_store_backend", "sqlite")
        monkeypatch.setattr(
            user_store.settings, "user_store_path", str(tmp_path / "singleton.db")
        )
        rebuilt = user_store.get_user_store()
        assert rebuilt is not first and isinstance(rebuilt, SqliteUserStore)

        injected = InMemoryUserStore()
        user_store.set_user_store(injected)
        assert user_store.get_user_store() is injected
    finally:
        user_store.reset_user_store()
