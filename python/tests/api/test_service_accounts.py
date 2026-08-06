"""Service accounts: the admin surface + auth-time recognition (issue #258 part 2).

Two things are under test and they are deliberately separate concerns:

1. ``/v1/admin/service-accounts`` — admin-only CRUD over the account RECORD.
   It never touches a credential: ``settings.api_keys`` has no writer, so key
   rotation stays an operator env edit plus a restart.
2. The API-key auth path — a key whose tenant is a REGISTERED and DISABLED
   service account is refused with 401. That is the whole point of the record:
   stopping a leaked key without a restart. Its failure policy is FAIL OPEN and
   its cache TTL is the revocation lag, both pinned here.

The bearer path is untouched by all of this and is not exercised.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from ragstack.api import security
from ragstack.api.security import ROLE_ADMIN, ROLE_USER
from ragstack.user_store import InMemoryUserStore, reset_user_store, set_user_store

pytestmark = pytest.mark.asyncio

# admin manages; user is a plain caller (403 on this surface); svc is the
# machine identity whose key we revoke; plain is an UNREGISTERED api-key tenant
# that must keep working exactly as before (registration is opt-in).
KEYS = {"admin": "k-admin", "user": "k-user", "svc": "k-svc", "plain": "k-plain"}
TENANTS = {
    "k-admin": "admin",
    "k-user": "user",
    "k-svc": "loader",
    "k-plain": "unregistered",
}


@pytest.fixture(autouse=True)
def _principals(monkeypatch):
    monkeypatch.setattr(security.settings, "api_keys", list(KEYS.values()))
    monkeypatch.setattr(security.settings, "api_key_tenants", dict(TENANTS))
    monkeypatch.setattr(security.settings, "api_key_roles", {"k-admin": ROLE_ADMIN})
    monkeypatch.setattr(security.settings, "default_role", ROLE_USER)


@pytest.fixture
def user_store():
    """In-memory user store as the module singleton — the admin router and the
    auth-path disabled check both resolve through it, and ASGITransport never
    runs the lifespan. The auth path's disabled-lookup cache and its warn-once
    flag are process-wide, so clear them here too (the conftest clears the cache
    for every test; the warn flag is this module's business)."""
    store = InMemoryUserStore()
    set_user_store(store)
    security.reset_disabled_cache()
    security._disabled_lookup_failure_warned = False
    yield store
    reset_user_store()
    security.reset_disabled_cache()
    security._disabled_lookup_failure_warned = False


def _h(who: str) -> dict:
    return {"X-API-Key": KEYS[who]}


# --------------------------------------------------------------------------- #
# Admin gate
# --------------------------------------------------------------------------- #


async def test_surface_is_admin_only(client, user_store):
    """Gated at include time by ``require_role(ROLE_ADMIN)``, which also performs
    authentication: no key is 401, a valid non-admin key is 403, on every route."""
    routes = [
        ("get", "/v1/admin/service-accounts", None),
        ("post", "/v1/admin/service-accounts", {"subject": "loader"}),
        ("post", "/v1/admin/service-accounts/loader/disable", None),
        ("post", "/v1/admin/service-accounts/loader/enable", None),
    ]
    for method, path, body in routes:
        call = getattr(client, method)
        kwargs = {"json": body} if body is not None else {}
        assert (await call(path, **kwargs)).status_code == 401, path
        assert (await call(path, headers=_h("user"), **kwargs)).status_code == 403, path


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #


async def test_create_list_disable_enable(client, user_store):
    resp = await client.post(
        "/v1/admin/service-accounts",
        headers=_h("admin"),
        json={"subject": "loader", "purpose": "nightly ingest"},
    )
    assert resp.status_code == 201
    rec = resp.json()
    assert rec["subject"] == "loader"
    assert rec["purpose"] == "nightly ingest"
    assert rec["created_by"] == "admin"  # the admin's tenant, from the Principal
    assert rec["created_at"]
    assert rec["active"] is True
    assert rec["disabled_by"] == "" and rec["disabled_at"] == ""
    assert rec["enabled_by"] == "" and rec["enabled_at"] == ""

    listed = await client.get("/v1/admin/service-accounts", headers=_h("admin"))
    assert listed.status_code == 200
    assert [a["subject"] for a in listed.json()["service_accounts"]] == ["loader"]

    assert (
        await client.post(
            "/v1/admin/service-accounts/loader/disable", headers=_h("admin")
        )
    ).status_code == 204

    listed = await client.get("/v1/admin/service-accounts", headers=_h("admin"))
    (row,) = listed.json()["service_accounts"]
    # Soft state: still listed, with who/when — never a row deletion.
    assert row["active"] is False
    assert row["disabled_by"] == "admin" and row["disabled_at"]

    assert (
        await client.post(
            "/v1/admin/service-accounts/loader/enable", headers=_h("admin")
        )
    ).status_code == 204
    listed = await client.get("/v1/admin/service-accounts", headers=_h("admin"))
    (row,) = listed.json()["service_accounts"]
    assert row["active"] is True
    # The disable stamp SURVIVES the re-enable (ADR-0004 decision 6): a row that
    # was revoked once must never read back identical to one that never was.
    assert row["disabled_by"] == "admin" and row["disabled_at"]
    assert row["enabled_by"] == "admin" and row["enabled_at"]


async def test_toggles_are_idempotent(client, user_store):
    await client.post(
        "/v1/admin/service-accounts", headers=_h("admin"), json={"subject": "loader"}
    )
    for _ in range(2):
        r = await client.post(
            "/v1/admin/service-accounts/loader/disable", headers=_h("admin")
        )
        assert r.status_code == 204
    for _ in range(2):
        r = await client.post(
            "/v1/admin/service-accounts/loader/enable", headers=_h("admin")
        )
        assert r.status_code == 204


async def test_recreate_returns_the_stored_row_unchanged(client, user_store):
    """A provisioning script must be re-runnable: the second create is a no-op
    that neither rewrites ``purpose`` nor resurrects a disabled account."""
    first = await client.post(
        "/v1/admin/service-accounts",
        headers=_h("admin"),
        json={"subject": "loader", "purpose": "original"},
    )
    assert first.status_code == 201
    await client.post("/v1/admin/service-accounts/loader/disable", headers=_h("admin"))

    second = await client.post(
        "/v1/admin/service-accounts",
        headers=_h("admin"),
        json={"subject": "loader", "purpose": "rewritten"},
    )
    assert second.status_code == 201
    assert second.json()["purpose"] == "original"
    assert second.json()["active"] is False  # a re-create is NOT a re-enable


async def test_colon_subject_is_refused(client, user_store):
    """The colon rule is the partition between the two authentication
    namespaces, and it is also what lets a service subject be an
    ``api_key_tenants`` value at all in production (the #243 startup guard
    rejects a coloned API-key tenant when an IdP is on)."""
    resp = await client.post(
        "/v1/admin/service-accounts", headers=_h("admin"), json={"subject": "svc:loader"}
    )
    assert resp.status_code in (400, 422)
    assert await user_store.list_service_accounts() == []

    for path in (
        "/v1/admin/service-accounts/svc:loader/disable",
        "/v1/admin/service-accounts/svc:loader/enable",
    ):
        assert (await client.post(path, headers=_h("admin"))).status_code in (400, 422)


async def test_reserved_tenants_cannot_be_registered(client, user_store):
    """The lockout that has no way back. ``default`` is what EVERY valid-but-
    unmapped API key resolves to (and the whole keyless dev path); ``public`` is
    the shared corpus. Registering one and disabling it 401s every such caller
    at once — including the admin key that would have to call /enable, so the
    only recovery is an env edit plus a restart. Refuse at registration."""
    for reserved in ("default", "public"):
        resp = await client.post(
            "/v1/admin/service-accounts",
            headers=_h("admin"),
            json={"subject": reserved},
        )
        assert resp.status_code == 400, reserved
        assert "reserved" in resp.json()["detail"]
        assert await user_store.get(reserved) is None  # nothing was written


async def test_an_admin_cannot_disable_the_account_it_is_using(client, user_store):
    """Self-lockout guard. The disabled check runs on the API-key path this
    caller just authenticated on, so disabling its own subject would 401 the very
    next request — including the /enable that undoes it. 409, and the account
    stays usable."""
    await client.post(
        "/v1/admin/service-accounts", headers=_h("admin"), json={"subject": "admin"}
    )
    resp = await client.post(
        "/v1/admin/service-accounts/admin/disable", headers=_h("admin")
    )
    assert resp.status_code == 409
    assert "authenticating as" in resp.json()["detail"]
    # Not disabled, and the admin key still works — the surface is not bricked.
    assert (await client.get("/v1/documents", headers=_h("admin"))).status_code == 200
    listed = await client.get("/v1/admin/service-accounts", headers=_h("admin"))
    assert listed.json()["service_accounts"][0]["active"] is True
    # A DIFFERENT subject is unaffected: this is a self-guard, not a ban on
    # disabling, and enabling your own account is always fine.
    await client.post(
        "/v1/admin/service-accounts", headers=_h("admin"), json={"subject": "loader"}
    )
    assert (
        await client.post(
            "/v1/admin/service-accounts/loader/disable", headers=_h("admin")
        )
    ).status_code == 204
    assert (
        await client.post(
            "/v1/admin/service-accounts/admin/enable", headers=_h("admin")
        )
    ).status_code == 204


async def test_purpose_and_subject_are_bounded_and_stripped_of_control_chars(
    client, user_store
):
    """``purpose`` is admin-supplied but still round-trips through GET into
    terminals, logs and the UI, so it gets the SAME sanitizer the auth path
    applies to profile claims — control characters stripped, then truncated —
    not just the length half. An unbounded subject would become a users-table
    primary key."""
    resp = await client.post(
        "/v1/admin/service-accounts",
        headers=_h("admin"),
        json={"subject": "loader", "purpose": "a\x00b\x1bc\ndrop" + "x" * 500},
    )
    assert resp.status_code == 201
    purpose = resp.json()["purpose"]
    assert purpose.startswith("abcdrop")
    assert not any(c in purpose for c in "\x00\x1b\n")
    assert len(purpose) <= 256

    long_subject = await client.post(
        "/v1/admin/service-accounts",
        headers=_h("admin"),
        json={"subject": "s" * 200_000},
    )
    assert long_subject.status_code in (400, 422)
    assert [a["subject"] for a in
            (await client.get("/v1/admin/service-accounts", headers=_h("admin"))
             ).json()["service_accounts"]] == ["loader"]


async def test_blank_and_malformed_bodies_are_refused(client, user_store):
    blank = await client.post(
        "/v1/admin/service-accounts", headers=_h("admin"), json={"subject": "   "}
    )
    assert blank.status_code in (400, 422)
    missing = await client.post(
        "/v1/admin/service-accounts", headers=_h("admin"), json={}
    )
    assert missing.status_code == 422
    # extra="forbid": an attempt to pass key material is a 422, not a silent drop.
    extra = await client.post(
        "/v1/admin/service-accounts",
        headers=_h("admin"),
        json={"subject": "loader", "api_key": "k-svc"},
    )
    assert extra.status_code == 422


async def test_human_collision_is_409(client, user_store):
    """Converting a real person's row into a machine credential is a privilege
    event and is refused — 409, not a silent reclassification."""
    await user_store.upsert_seen(subject="loader", issuer="bvbrc")
    resp = await client.post(
        "/v1/admin/service-accounts", headers=_h("admin"), json={"subject": "loader"}
    )
    assert resp.status_code == 409
    rec = await user_store.get("loader")
    assert rec is not None and rec.kind == "human"


async def test_toggling_an_unknown_or_human_subject(client, user_store):
    assert (
        await client.post(
            "/v1/admin/service-accounts/nobody/disable", headers=_h("admin")
        )
    ).status_code == 404
    await user_store.upsert_seen(subject="person", issuer="bvbrc")
    assert (
        await client.post(
            "/v1/admin/service-accounts/person/disable", headers=_h("admin")
        )
    ).status_code == 409


async def test_response_never_carries_key_material(client, user_store):
    """``admin.py`` states as a contract that api_keys/api_key_tenants/
    api_key_roles are never read into any response. A service-account surface
    that echoed a key, a prefix, or even a count would partially undo it."""
    await client.post(
        "/v1/admin/service-accounts", headers=_h("admin"), json={"subject": "loader"}
    )
    body = (await client.get("/v1/admin/service-accounts", headers=_h("admin"))).text
    for key in KEYS.values():
        assert key not in body
    assert "api_key" not in body


async def test_store_outage_on_the_admin_surface_is_503(client, user_store, monkeypatch):
    """Fail CLOSED here — unlike the auth path. An admin asking "who is
    registered?" must never get a confidently empty list from a broken store."""

    async def _boom(*args, **kwargs):
        raise RuntimeError("user store down")

    monkeypatch.setattr(user_store, "list_service_accounts", _boom)
    resp = await client.get("/v1/admin/service-accounts", headers=_h("admin"))
    assert resp.status_code == 503


# --------------------------------------------------------------------------- #
# Auth-time recognition (the API-key path only)
# --------------------------------------------------------------------------- #


async def test_disabled_service_account_key_is_401(client, user_store):
    """The operational point of the whole feature: a still-valid, still-
    configured key stops working because the account was disabled."""
    assert (await client.get("/v1/documents", headers=_h("svc"))).status_code == 200

    await client.post(
        "/v1/admin/service-accounts", headers=_h("admin"), json={"subject": "loader"}
    )
    # Registered but enabled → unchanged.
    assert (await client.get("/v1/documents", headers=_h("svc"))).status_code == 200

    await client.post("/v1/admin/service-accounts/loader/disable", headers=_h("admin"))
    assert (await client.get("/v1/documents", headers=_h("svc"))).status_code == 401

    await client.post("/v1/admin/service-accounts/loader/enable", headers=_h("admin"))
    assert (await client.get("/v1/documents", headers=_h("svc"))).status_code == 200


async def test_unregistered_key_tenant_is_untouched(client, user_store):
    """Registration is opt-in and never becomes a requirement: a key whose tenant
    has no users row behaves exactly as it did before #258."""
    resp = await client.get("/v1/stats/tenants", headers=_h("plain"))
    assert resp.status_code == 200
    assert resp.json()["tenant"] == "unregistered"
    assert await user_store.get("unregistered") is None  # and nothing was written


async def test_disabling_does_not_touch_other_tenants(client, user_store):
    await client.post(
        "/v1/admin/service-accounts", headers=_h("admin"), json={"subject": "loader"}
    )
    await client.post("/v1/admin/service-accounts/loader/disable", headers=_h("admin"))
    assert (await client.get("/v1/documents", headers=_h("svc"))).status_code == 401
    assert (await client.get("/v1/documents", headers=_h("plain"))).status_code == 200
    assert (await client.get("/v1/documents", headers=_h("user"))).status_code == 200


async def test_a_disabled_human_row_never_blocks_a_key(client, user_store):
    """Only ``kind='service'`` rows carry this flag. A human row that somehow
    shares the tenant string must not be able to lock out a deployment key."""
    await user_store.upsert_seen(subject="loader", issuer="bvbrc")
    security.reset_disabled_cache()
    assert (await client.get("/v1/documents", headers=_h("svc"))).status_code == 200


async def test_store_outage_does_not_break_api_key_auth(
    client, user_store, monkeypatch
):
    """FAIL OPEN, and deliberately so: the key was already verified by a
    constant-time compare, so a user-store outage must not lock out every
    API-key caller. The honest cost is that a disabled account is not revoked
    while the store is unreachable."""

    async def _boom(*args, **kwargs):
        raise RuntimeError("user store down")

    monkeypatch.setattr(user_store, "get", _boom)
    resp = await client.get("/v1/stats/tenants", headers=_h("svc"))
    assert resp.status_code == 200
    # Tenant and role stay env-derived — the store is never authoritative here.
    assert resp.json()["tenant"] == "loader"


async def test_first_lookup_failure_is_logged_at_warning(
    client, user_store, monkeypatch, caplog
):
    """An operator at the default INFO level must be able to see that revocation
    has silently stopped working. Repeats drop to debug so a dead store can't
    spam a line per request."""

    async def _boom(*args, **kwargs):
        raise RuntimeError("user store down")

    monkeypatch.setattr(user_store, "get", _boom)
    monkeypatch.setattr(security.settings, "service_account_disabled_cache_ttl_seconds", 0)
    with caplog.at_level(logging.DEBUG, logger="ragstack.api.security"):
        for _ in range(3):
            assert (
                await client.get("/v1/documents", headers=_h("svc"))
            ).status_code == 200
    failures = [
        r for r in caplog.records if "disabled check failed" in r.message
    ]
    assert failures
    assert failures[0].levelno == logging.WARNING
    assert all(r.levelno == logging.DEBUG for r in failures[1:])


async def test_the_lookup_warns_again_after_a_recovery(
    client, user_store, monkeypatch, caplog
):
    """The warn-once flag is per OUTAGE, not per process. Without the re-arm a
    single blip at boot would demote every later real outage to DEBUG, hiding
    "revocation is currently off" from an operator at the default INFO level —
    the exact condition the warning exists to surface."""

    async def _boom(*args, **kwargs):
        raise RuntimeError("user store down")

    real = user_store.get
    monkeypatch.setattr(security.settings, "service_account_disabled_cache_ttl_seconds", 0)
    with caplog.at_level(logging.DEBUG, logger="ragstack.api.security"):
        monkeypatch.setattr(user_store, "get", _boom)
        await client.get("/v1/documents", headers=_h("svc"))  # outage 1: WARNING
        monkeypatch.setattr(user_store, "get", real)
        await client.get("/v1/documents", headers=_h("svc"))  # recovery
        monkeypatch.setattr(user_store, "get", _boom)
        await client.get("/v1/documents", headers=_h("svc"))  # outage 2: WARNING

    failures = [r for r in caplog.records if "disabled check failed" in r.message]
    assert [r.levelno for r in failures] == [logging.WARNING, logging.WARNING]
    assert any("recovered" in r.message and r.levelno == logging.WARNING
               for r in caplog.records)


async def test_the_cache_ttl_is_capped_at_startup(monkeypatch):
    """The TTL is the revocation lag on the ONLY revoke that needs no restart,
    so it is hard-capped exactly like ``identity_cache_ttl_seconds`` — an
    operator cutting ACL-DB load must not silently buy a 30-day revocation
    window, and a clamp would hide which TTL is really in force."""
    from ragstack.api.security import validate_service_account_settings

    monkeypatch.setattr(
        security.settings, "service_account_disabled_cache_ttl_seconds", 300
    )
    validate_service_account_settings()  # at the cap: fine

    monkeypatch.setattr(
        security.settings, "service_account_disabled_cache_ttl_seconds", 2_592_000
    )
    with pytest.raises(RuntimeError, match="revocation lag"):
        validate_service_account_settings()


async def test_a_whitespace_padded_api_key_tenant_is_refused_at_startup(monkeypatch):
    """A tenant value is used VERBATIM on the auth path but every admin surface
    names subjects stripped, so ``"loader "`` is a subject nothing can revoke:
    disabling ``loader`` would 204 while the padded key kept authenticating, and
    the account would list as inactive with a live credential."""
    from ragstack.api.security import validate_role_settings

    monkeypatch.setattr(security.settings, "identity_provider", "none")
    monkeypatch.setattr(security.settings, "default_role", ROLE_USER)
    monkeypatch.setattr(security.settings, "api_key_roles", {})
    monkeypatch.setattr(security.settings, "api_key_tenants", {"k-svc": "loader"})
    validate_role_settings()  # clean value: fine

    for bad in ("loader ", " loader", "  "):
        monkeypatch.setattr(security.settings, "api_key_tenants", {"k-svc": bad})
        with pytest.raises(RuntimeError, match="whitespace"):
            validate_role_settings()


async def test_service_subjects_boot_under_an_identity_provider(monkeypatch):
    """The colon-free subject shape is what lets a service account be an
    ``api_key_tenants`` value in PRODUCTION at all.

    ``validate_role_settings`` refuses a coloned API-key tenant whenever an
    identity provider is enabled (issue #243), because such a tenant would
    collide with a bearer user's ``issuer:sub`` ownership identity. Prod runs an
    identity provider, so a namespaced ``svc:loader`` would not boot — and #258
    needed no exemption to that guard precisely because it chose colon-free
    subjects. Both halves are asserted: service subjects pass, a bearer-shaped
    tenant is still rejected."""
    from ragstack.api.security import validate_role_settings

    monkeypatch.setattr(security.settings, "identity_provider", "bvbrc")
    monkeypatch.setattr(security.settings, "default_role", ROLE_USER)
    monkeypatch.setattr(security.settings, "api_key_roles", {})
    monkeypatch.setattr(security.settings, "api_key_tenants", {"k-svc": "loader"})
    validate_role_settings()  # no raise — a colon-free service subject is fine

    monkeypatch.setattr(security.settings, "api_key_tenants", {"k-svc": "google:alice"})
    with pytest.raises(RuntimeError, match="collides with a bearer subject"):
        validate_role_settings()


# --------------------------------------------------------------------------- #
# The cache: hot-path cost vs revocation lag
# --------------------------------------------------------------------------- #


async def test_lookup_is_cached_per_subject(client, user_store, monkeypatch):
    """The key path was pure CPU before this check. With the TTL cache it is one
    store read per subject per window, not one per request."""
    calls = 0
    real = user_store.get

    async def _counting(subject):
        nonlocal calls
        calls += 1
        return await real(subject)

    monkeypatch.setattr(security.settings, "service_account_disabled_cache_ttl_seconds", 300)
    monkeypatch.setattr(user_store, "get", _counting)
    security.reset_disabled_cache()
    for _ in range(5):
        assert (await client.get("/v1/documents", headers=_h("svc"))).status_code == 200
    assert calls == 1


async def test_ttl_zero_disables_the_cache(client, user_store, monkeypatch):
    """TTL 0 is the "instant revocation, worst hot-path cost" end of the trade."""
    calls = 0
    real = user_store.get

    async def _counting(subject):
        nonlocal calls
        calls += 1
        return await real(subject)

    monkeypatch.setattr(security.settings, "service_account_disabled_cache_ttl_seconds", 0)
    monkeypatch.setattr(user_store, "get", _counting)
    security.reset_disabled_cache()
    for _ in range(3):
        assert (await client.get("/v1/documents", headers=_h("svc"))).status_code == 200
    assert calls == 3


async def test_the_ttl_is_the_revocation_lag(client, user_store, monkeypatch):
    """Written down as a test because it is the feature's real contract: a
    disable performed OUT OF BAND (another worker, or straight into the store)
    is not honoured until this process's cached answer expires."""
    monkeypatch.setattr(security.settings, "service_account_disabled_cache_ttl_seconds", 300)
    await user_store.create_service_account("loader", created_by="admin")
    security.reset_disabled_cache()
    assert (await client.get("/v1/documents", headers=_h("svc"))).status_code == 200

    # Out-of-band disable: no request touched this process's router, so nothing
    # flushed its cache. The stale "enabled" answer stands until the TTL lapses.
    await user_store.disable_service_account("loader", actor="admin")
    assert (await client.get("/v1/documents", headers=_h("svc"))).status_code == 200

    security.reset_disabled_cache()  # stands in for the TTL elapsing
    assert (await client.get("/v1/documents", headers=_h("svc"))).status_code == 401


async def test_disable_through_the_api_flushes_this_process_cache(client, user_store):
    """The in-process flush narrows the lag to zero for the worker that served
    the disable — it does not remove it for the others."""
    from ragstack.config import settings

    original = settings.service_account_disabled_cache_ttl_seconds
    settings.service_account_disabled_cache_ttl_seconds = 300
    try:
        await client.post(
            "/v1/admin/service-accounts",
            headers=_h("admin"),
            json={"subject": "loader"},
        )
        assert (await client.get("/v1/documents", headers=_h("svc"))).status_code == 200
        await client.post(
            "/v1/admin/service-accounts/loader/disable", headers=_h("admin")
        )
        assert (await client.get("/v1/documents", headers=_h("svc"))).status_code == 401
    finally:
        settings.service_account_disabled_cache_ttl_seconds = original


# --------------------------------------------------------------------------- #
# Independent-review regressions (PR #259).
# --------------------------------------------------------------------------- #


async def test_a_flush_beats_an_in_flight_lookup(monkeypatch):
    """A lookup that started BEFORE an operator's /disable must not resume and
    re-install its stale 'enabled' verdict — that silently voided the flush for
    a full TTL and defeated exactly the check an operator runs after revoking."""
    from ragstack.api import security

    security.reset_disabled_cache()
    started = asyncio.Event()
    release = asyncio.Event()

    class _SlowStore:
        async def get(self, subject):
            started.set()
            await release.wait()
            return None  # the pre-disable truth: not disabled

    monkeypatch.setattr("ragstack.user_store.get_user_store", lambda: _SlowStore())
    task = asyncio.create_task(security._service_account_disabled("svc-x"))
    await started.wait()
    security.reset_disabled_cache()  # the operator disables mid-flight
    release.set()
    assert await task is False  # this request still proceeds (freshest read)
    # ...but the stale verdict must NOT have been memoized for the next one.
    assert "svc-x" not in security._disabled_cache


async def test_a_subject_that_cannot_be_revoked_is_refused(client):
    """A '/' in the subject makes the disable route 404 (Starlette decodes %2F
    before routing), so the account would register and then be permanently
    unrevocable — the one operation it exists for."""
    for bad in ["ops/prod", "a/b/c", "..", "svc%2Fx", "svc?x", "svc#x"]:
        r = await client.post(
            "/v1/admin/service-accounts",
            json={"subject": bad, "purpose": "x"},
            headers=_h("admin"),
        )
        assert r.status_code in (400, 422), f"{bad!r} accepted: {r.text}"


async def test_control_characters_in_a_subject_are_refused(client):
    r = await client.post(
        "/v1/admin/service-accounts",
        json={"subject": "load\x00er\x1b[31m", "purpose": "x"},
        headers=_h("admin"),
    )
    assert r.status_code in (400, 422), r.text


def test_a_non_ascii_api_key_is_401_not_500(monkeypatch):
    """Starlette decodes header bytes as latin-1 and compare_digest raises on a
    non-ASCII str — one high byte from an unauthenticated caller escaped as a
    500. Tested at the function (httpx refuses to encode such a header at all,
    so it cannot reach the app through the test client)."""
    from fastapi import HTTPException

    from ragstack.api import security

    monkeypatch.setattr(security.settings, "api_keys", ["k-real"])
    with pytest.raises(HTTPException) as exc:
        security._principal_from_key("k\xe9")
    assert exc.value.status_code == 401
