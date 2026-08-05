"""The user-profile store (``ragstack.user_store``, ADR-0004 decision 1).

The row is keyed on the tenant string ``f"{issuer}:{sub}"`` — never an email —
and the semantics under test are the ones auth depends on: ``upsert_seen`` is
idempotent (it fires once per identity-cache expiry, not once per session),
never blanks out profile data a previous token provided, and flips a
provisional grantee row into a real profile without losing ``first_seen_at``.
"""
from __future__ import annotations

import os
import sqlite3
from types import SimpleNamespace

import pytest

from ragstack.user_store import (
    InMemoryUserStore,
    PostgresUserStore,
    SqliteUserStore,
    UserRecord,
    make_user_store,
)

pytestmark = pytest.mark.asyncio

SUBJECT = "bvbrc:alice@patricbrc.org"
ISSUER = "bvbrc"


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
    assert rec == UserRecord(subject="bvbrc:legacy", issuer="bvbrc")
    await store.upsert_seen(SUBJECT, ISSUER)
    assert len(await store.list_users()) == 2


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
