"""First-auth profile upsert (ADR-0004 decision 1) through the bearer path.

The hook lives in ``_principal_from_bearer``: after a credential verifies, a
fire-and-forget task records the user in the user store. The properties under
test are the ones auth depends on: a verified bearer login creates the row, a
broken store never breaks auth, and API-key principals get no row (a key is a
deployment credential, not a person).
"""
from __future__ import annotations

import time

import pytest

from ragstack.api import security
from ragstack.identity import Identity, reset_identity_provider, set_identity_provider
from ragstack.user_store import InMemoryUserStore, reset_user_store, set_user_store


class FakeProvider:
    """Authenticates exactly one credential, with profile claims attached."""

    def __init__(
        self,
        *,
        good: str = "good-token",
        email: str = "alice@example.org",
        email_verified: bool = True,
        display_name: str = "Alice",
    ):
        self.good = good
        self.email = email
        self.email_verified = email_verified
        self.display_name = display_name

    async def authenticate(self, credential: str) -> Identity:
        from ragstack.identity import IdentityInvalid

        if credential != self.good:
            raise IdentityInvalid("no")
        return Identity(
            subject="alice@patricbrc.org",
            issuer="bvbrc",
            token_id="tok-1",
            expires_at=int(time.time()) + 3600,
            email=self.email,
            email_verified=self.email_verified,
            display_name=self.display_name,
        )


@pytest.fixture
def identity(monkeypatch):
    monkeypatch.setattr(security.settings, "identity_provider", "bvbrc")
    provider = FakeProvider()
    set_identity_provider(provider)
    yield provider
    reset_identity_provider()


@pytest.fixture
def user_store():
    """Inject an in-memory user store into the module singleton — the auth hook
    resolves through it, and ASGITransport never runs the lifespan. The
    per-subject upsert debounce is process-wide state, so clear it here: these
    tests share one subject and each expects its own first-auth write."""
    store = InMemoryUserStore()
    set_user_store(store)
    security._upsert_last.clear()
    security._upsert_failure_warned = False
    yield store
    reset_user_store()
    security._upsert_last.clear()


async def _drain_upserts() -> None:
    """The upsert is fire-and-forget; the test shares the request's event loop,
    so draining the pending task set is a deterministic seam. Uses the same
    drain the lifespan shutdown runs before closing the user store."""
    await security.drain_profile_upserts()


async def test_bearer_auth_creates_a_user_row(client, identity, user_store):
    resp = await client.get(
        "/v1/stats/tenants", headers={"Authorization": "Bearer good-token"}
    )
    assert resp.status_code == 200
    await _drain_upserts()

    rec = await user_store.get("bvbrc:alice@patricbrc.org")
    assert rec is not None
    # Keyed on the tenant string, never the email.
    assert rec.subject == "bvbrc:alice@patricbrc.org"
    assert rec.issuer == "bvbrc"
    assert rec.email == "alice@example.org"
    assert rec.display_name == "Alice"
    assert rec.provisional is False
    assert rec.first_seen_at and rec.last_seen_at


async def test_unverified_email_is_not_written(client, identity, user_store):
    """ADR-0004: an unverified email claim must never become claimable profile
    data — registering a colleague's address at any accepted IdP would
    otherwise steal their pending grants."""
    identity.email_verified = False
    resp = await client.get(
        "/v1/stats/tenants", headers={"Authorization": "Bearer good-token"}
    )
    assert resp.status_code == 200
    await _drain_upserts()

    rec = await user_store.get("bvbrc:alice@patricbrc.org")
    assert rec is not None
    assert rec.email == ""  # unverified → not stored
    assert rec.display_name == "Alice"  # display name is not an email claim


async def test_store_failure_never_breaks_auth(client, identity, user_store, monkeypatch):
    """Authentication must never fail, slow, or 500 because the profile write
    did — the ADR calls the write fire-and-forget for exactly this reason."""

    async def _boom(*args, **kwargs):
        raise RuntimeError("user store down")

    monkeypatch.setattr(user_store, "upsert_seen", _boom)
    resp = await client.get(
        "/v1/stats/tenants", headers={"Authorization": "Bearer good-token"}
    )
    assert resp.status_code == 200
    assert resp.json()["tenant"] == "bvbrc:alice@patricbrc.org"
    await _drain_upserts()  # the failed task must not leak into other tests


async def test_first_upsert_failure_is_logged_at_warning_not_raised(
    client, identity, user_store, monkeypatch, caplog
):
    """The first failure per process is a WARNING — an operator running at the
    default INFO level must be able to see a dead user store that is silently
    recording nobody. Repeats drop to debug so a dead store can't spam."""

    async def _boom(*args, **kwargs):
        raise RuntimeError("user store down")

    monkeypatch.setattr(user_store, "upsert_seen", _boom)
    import logging

    with caplog.at_level(logging.DEBUG, logger="ragstack.api.security"):
        resp = await client.get(
            "/v1/stats/tenants", headers={"Authorization": "Bearer good-token"}
        )
        assert resp.status_code == 200
        await _drain_upserts()
    failures = [r for r in caplog.records if "user profile upsert failed" in r.message]
    assert failures and failures[0].levelno == logging.WARNING


async def test_upserts_are_debounced_per_subject(client, identity, user_store, monkeypatch):
    """The hook runs on every bearer request (the provider cache caches
    verification, not this hook), so the per-subject debounce is what keeps the
    users table from becoming a per-request write hotspot."""
    calls = 0
    real = user_store.upsert_seen

    async def _counting(*args, **kwargs):
        nonlocal calls
        calls += 1
        return await real(*args, **kwargs)

    monkeypatch.setattr(user_store, "upsert_seen", _counting)
    for _ in range(5):
        resp = await client.get(
            "/v1/stats/tenants", headers={"Authorization": "Bearer good-token"}
        )
        assert resp.status_code == 200
    await _drain_upserts()
    assert calls == 1
    assert await user_store.get("bvbrc:alice@patricbrc.org") is not None


async def test_oversized_or_control_character_claims_are_clamped(
    client, identity, user_store
):
    """Profile claims come from the token (OIDC `name`/`email` are only
    isinstance-checked upstream): a validly-signed token with a multi-megabyte
    or control-character-laden claim must not be persisted verbatim."""
    identity.display_name = "\x00\x1b[31mA" + "b" * 10_000
    identity.email = "a" * 500 + "@example.org"
    resp = await client.get(
        "/v1/stats/tenants", headers={"Authorization": "Bearer good-token"}
    )
    assert resp.status_code == 200
    await _drain_upserts()

    rec = await user_store.get("bvbrc:alice@patricbrc.org")
    assert rec is not None
    assert len(rec.display_name) <= security._PROFILE_CLAIM_MAX
    assert len(rec.email) <= security._PROFILE_CLAIM_MAX
    assert "\x00" not in rec.display_name and "\x1b" not in rec.display_name
    assert rec.display_name.startswith("[31mAbb")  # only the controls are gone


async def test_api_key_principals_get_no_user_row(client, identity, user_store, monkeypatch):
    """An API key is a deployment credential, not a person — no profile row."""
    monkeypatch.setattr(security.settings, "api_keys", ["s3cret"])
    resp = await client.get("/v1/documents", headers={"X-API-Key": "s3cret"})
    assert resp.status_code == 200
    await _drain_upserts()
    assert await user_store.list_users() == []


async def test_repeated_logins_touch_one_row(client, identity, user_store):
    for _ in range(3):
        resp = await client.get(
            "/v1/stats/tenants", headers={"Authorization": "Bearer good-token"}
        )
        assert resp.status_code == 200
    await _drain_upserts()
    users = await user_store.list_users()
    assert [u.subject for u in users] == ["bvbrc:alice@patricbrc.org"]
