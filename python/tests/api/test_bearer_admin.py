"""A bearer identity CAN be an admin — but only by deliberate assignment.

The properties under test, and the concrete failure each one prevents:

* **A token cannot self-elevate.** Nothing that travels with the credential is
  an input to the role decision; the only two sources are an env allowlist the
  operator set and a users row an existing admin wrote.
* **``DEFAULT_ROLE=admin`` does not leak into the bearer path.** Production runs
  exactly that, so a fallthrough would make every authenticated end user a
  superuser over every collection in the deployment. This is the invariant the
  old hardcoded ``role=ROLE_USER`` protected, and it must survive the change.
* **``upsert_seen`` on every login does not reset an admin's role.** The
  first-auth hook runs on every bearer request (debounced, fire-and-forget); if
  it could write ``role`` an admin would silently be demoted minutes after
  signing in.
* **The env allowlist works on an empty users table** and is checked FIRST, so
  it needs no store read: that is the bootstrap path (the grant route is itself
  admin-gated) and the break-glass path a database write cannot revoke.
* **A store outage demotes to ``user``, never elevates.** The mirror image of
  the disabled-account check, which deliberately fails OPEN.
* **The grant route is admin-gated, and refuses a last-admin revoke that would
  be UNRECOVERABLE** — measured against every admin source, not just this table:
  a usable ``ADMIN_SUBJECTS`` entry or an API key mapped to ``admin`` is a way
  back, an allowlist entry no provider can produce is not, and the refusal is
  decided inside the write so two concurrent revokes cannot both pass it.
"""
from __future__ import annotations

import time

import pytest

from ragstack.api import security
from ragstack.api.security import ROLE_ADMIN, ROLE_USER
from ragstack.identity import Identity, reset_identity_provider, set_identity_provider
from ragstack.user_store import (
    USER_ROLE_ADMIN,
    USER_ROLE_USER,
    InMemoryUserStore,
    reset_user_store,
    set_user_store,
)

ALICE = "bvbrc:alice@patricbrc.org"
BOB = "bvbrc:bob@patricbrc.org"


class FakeProvider:
    """Authenticates exactly one credential per subject — the identity layer is
    not what is under test here, only what the auth path does with the result."""

    def __init__(self, subject: str = "alice@patricbrc.org"):
        self.subject = subject

    async def authenticate(self, credential: str) -> Identity:
        return Identity(
            subject=self.subject,
            issuer="bvbrc",
            token_id="tok-1",
            expires_at=int(time.time()) + 3600,
        )


class BrokenStore(InMemoryUserStore):
    """A user store whose every call fails — a partitioned/unreachable ACL
    database. ``upsert_seen`` is left working so the fire-and-forget login
    write is not what the test ends up observing."""

    async def get(self, subject: str):
        raise RuntimeError("user store is down")

    async def count_admins(self) -> int:
        raise RuntimeError("user store is down")

    async def set_role(self, subject: str, role: str, actor: str, **kwargs):
        raise RuntimeError("user store is down")


@pytest.fixture
def user_store():
    store = InMemoryUserStore()
    set_user_store(store)
    yield store
    reset_user_store()


@pytest.fixture(autouse=True)
def _clean_module_state(monkeypatch):
    """Every process-wide cache/flag this path touches, cleared on setup AND
    teardown: the role cache and its warn-once flag decide verdicts, and the
    profile-upsert debounce would otherwise suppress the login write a later
    test depends on. Also start from an EMPTY allowlist so a test that forgets
    to set one cannot inherit another's admin."""
    monkeypatch.setattr(security.settings, "admin_subjects", [])
    monkeypatch.setattr(security.settings, "default_role", ROLE_USER)
    security.reset_role_cache()
    security._role_lookup_failure_warned = False
    security._upsert_last.clear()
    yield
    security.reset_role_cache()
    security._role_lookup_failure_warned = False
    security._upsert_last.clear()


@pytest.fixture
def identity(monkeypatch):
    """Turn the identity layer on with a fake provider, and off again after."""
    monkeypatch.setattr(security.settings, "identity_provider", "bvbrc")
    provider = FakeProvider()
    set_identity_provider(provider)
    yield provider
    reset_identity_provider()


def _bearer() -> dict:
    return {"Authorization": "Bearer good-token"}


async def _role(client) -> str:
    resp = await client.get("/v1/stats/tenants", headers=_bearer())
    assert resp.status_code == 200
    return resp.json()["role"]


# --------------------------------------------------------------------------- #
# Source 1: the ADMIN_SUBJECTS env allowlist
# --------------------------------------------------------------------------- #


async def test_an_admin_subjects_entry_gets_the_admin_role(client, identity, monkeypatch):
    monkeypatch.setattr(security.settings, "admin_subjects", [ALICE])
    assert await _role(client) == ROLE_ADMIN
    # And the admin surface actually opens — the role is not merely cosmetic.
    assert (await client.get("/v1/config", headers=_bearer())).status_code == 200


async def test_the_allowlist_needs_no_users_row_and_no_store_read(
    client, identity, monkeypatch
):
    """The bootstrap case. The grant route is itself admin-gated, so a
    store-only design would 403 the very operator trying to create the first
    admin; and a store outage must not take the break-glass path away."""
    set_user_store(BrokenStore())
    monkeypatch.setattr(security.settings, "admin_subjects", [ALICE])
    try:
        assert await _role(client) == ROLE_ADMIN
    finally:
        reset_user_store()


async def test_the_allowlist_beats_a_stored_non_admin_role(
    client, identity, monkeypatch, user_store
):
    """Precedence is one-directional: a database write must not be able to
    revoke an operator's break-glass entry, or it stops being break-glass."""
    await user_store.upsert_seen(ALICE, "bvbrc")
    await user_store.set_role(ALICE, USER_ROLE_USER, actor="bvbrc:root")
    monkeypatch.setattr(security.settings, "admin_subjects", [ALICE])
    assert await _role(client) == ROLE_ADMIN


async def test_the_allowlist_is_read_at_call_time(client, identity, monkeypatch):
    """Not frozen at import: an allowlist pinned at import time would be
    unmonkeypatchable and would silently decide the whole process."""
    assert await _role(client) == ROLE_USER
    monkeypatch.setattr(security.settings, "admin_subjects", [ALICE])
    assert await _role(client) == ROLE_ADMIN


async def test_the_allowlist_names_one_subject_not_everyone(
    client, identity, monkeypatch
):
    monkeypatch.setattr(security.settings, "admin_subjects", [BOB])
    assert await _role(client) == ROLE_USER


def test_the_allowlist_is_not_consulted_on_the_api_key_path(monkeypatch):
    """Namespace crossing: an API-key tenant is colon-free and carries its own
    ``api_key_roles``. If the bearer allowlist could elevate a key principal,
    the disjoint-namespace invariant (#243) would dissolve — so the key path
    must not look at it even when the strings happen to match."""
    monkeypatch.setattr(security.settings, "api_keys", ["k"])
    monkeypatch.setattr(security.settings, "api_key_tenants", {"k": "loader"})
    monkeypatch.setattr(security.settings, "api_key_roles", {})
    monkeypatch.setattr(security.settings, "default_role", ROLE_USER)
    monkeypatch.setattr(security.settings, "admin_subjects", ["loader"])
    assert security._principal_from_key("k").role == ROLE_USER


# --------------------------------------------------------------------------- #
# Source 2: the stored users.role
# --------------------------------------------------------------------------- #


async def test_a_stored_admin_role_gets_the_admin_role(
    client, identity, user_store
):
    await user_store.upsert_seen(ALICE, "bvbrc")
    await user_store.set_role(ALICE, USER_ROLE_ADMIN, actor="bvbrc:root")
    assert await _role(client) == ROLE_ADMIN
    assert (await client.get("/v1/config", headers=_bearer())).status_code == 200


async def test_a_stored_plain_role_is_not_admin(client, identity, user_store):
    await user_store.upsert_seen(ALICE, "bvbrc")
    await user_store.set_role(ALICE, USER_ROLE_USER, actor="bvbrc:root")
    assert await _role(client) == ROLE_USER


async def test_logging_in_does_not_reset_a_stored_admin_role(
    client, identity, user_store
):
    """``upsert_seen`` runs on the auth path itself (fire-and-forget). If it
    could assign ``role``, an admin would be demoted by their own next login,
    with no error raised anywhere."""
    await user_store.upsert_seen(ALICE, "bvbrc")
    await user_store.set_role(ALICE, USER_ROLE_ADMIN, actor="bvbrc:root")

    assert await _role(client) == ROLE_ADMIN
    await security.drain_profile_upserts()  # let the login write actually land

    rec = await user_store.get(ALICE)
    assert rec is not None and rec.role == USER_ROLE_ADMIN
    assert rec.last_seen_at  # the login DID write — this is not a vacuous pass
    security.reset_role_cache()  # stands in for the TTL elapsing
    assert await _role(client) == ROLE_ADMIN


async def test_a_store_outage_yields_user_never_admin(client, identity, caplog):
    """The sharpest asymmetry: the disabled-account check fails OPEN because the
    store can only REFUSE an already-verified key. This read can only GRANT, so
    it fails CLOSED — and the request still authenticates."""
    set_user_store(BrokenStore())
    try:
        with caplog.at_level("WARNING"):
            assert await _role(client) == ROLE_USER
        assert (await client.get("/v1/config", headers=_bearer())).status_code == 403
    finally:
        reset_user_store()
    assert any("failing closed" in r.getMessage() for r in caplog.records)


async def test_the_store_outage_warning_is_once_per_outage(client, identity, caplog):
    """An operator must see "admin grants are not being honoured" at the default
    level, without a dead store emitting a line per request."""
    set_user_store(BrokenStore())
    try:
        with caplog.at_level("WARNING"):
            for _ in range(3):
                security.reset_role_cache()
                await _role(client)
        warnings = [r for r in caplog.records if "failing closed" in r.getMessage()]
        assert len(warnings) == 1
    finally:
        reset_user_store()


# --------------------------------------------------------------------------- #
# The invariant the hardcoded ROLE_USER protected
# --------------------------------------------------------------------------- #


async def test_default_role_admin_does_not_leak_into_the_bearer_path(
    client, identity, monkeypatch, user_store
):
    """The regression test. Prod sets DEFAULT_ROLE=admin; with no allowlist
    entry and no stored grant, a bearer caller is a plain user and the admin
    surface is closed to them."""
    monkeypatch.setattr(security.settings, "default_role", ROLE_ADMIN)
    assert await _role(client) == ROLE_USER
    assert (await client.get("/v1/config", headers=_bearer())).status_code == 403


def test_default_role_appears_nowhere_in_the_bearer_role_resolution():
    """Structural, not behavioural: the ONLY assignment of ROLE_ADMIN is inside
    an explicit "a server-side source says admin" branch, so every other path —
    including every exception and timeout — lands on the ROLE_USER literal.
    ``settings.default_role`` must not be reachable as CODE from either
    function (the comments naming it are the point, and stay)."""
    import ast
    import inspect
    import textwrap

    for fn in (security._principal_from_bearer, security._bearer_role):
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        assert "default_role" not in attrs | names, fn.__name__
        assert "normalize_role" not in attrs | names, fn.__name__


# --------------------------------------------------------------------------- #
# Startup validation of ADMIN_SUBJECTS
# --------------------------------------------------------------------------- #


def test_a_colon_free_admin_subject_is_refused_at_startup(monkeypatch):
    """The inverse of the service-account rule. A colon-free entry names an
    API-key TENANT, so honouring it would make a machine credential a superuser
    through the bearer door — the message points at API_KEY_ROLES instead."""
    monkeypatch.setattr(security.settings, "admin_subjects", ["loader"])
    with pytest.raises(RuntimeError) as exc:
        security.validate_admin_subjects_settings()
    assert "loader" in str(exc.value)
    assert "API_KEY_ROLES" in str(exc.value)


@pytest.mark.parametrize("bad", [":", "bvbrc:", ":alice", " bvbrc:alice", "bvbrc:alice ", ""])
def test_malformed_admin_subjects_are_refused_at_startup(monkeypatch, bad):
    monkeypatch.setattr(security.settings, "admin_subjects", [bad])
    with pytest.raises(RuntimeError):
        security.validate_admin_subjects_settings()


def test_a_control_bearing_or_oversized_admin_subject_is_refused(monkeypatch):
    monkeypatch.setattr(security.settings, "admin_subjects", ["bvbrc:al\x1bice"])
    with pytest.raises(RuntimeError, match="control characters"):
        security.validate_admin_subjects_settings()
    monkeypatch.setattr(security.settings, "admin_subjects", ["bvbrc:" + "a" * 200])
    with pytest.raises(RuntimeError, match="cap is 128"):
        security.validate_admin_subjects_settings()


def test_a_reserved_tenant_cannot_be_allowlisted(monkeypatch):
    """Only the WHOLE entry names a reserved tenant. The colon rule already
    keeps the two namespaces disjoint, so a federated subject whose `sub` half
    happens to be the word 'default' or 'public' is a legitimate identity in a
    namespace that cannot collide with the colon-free reserved tenants —
    refusing it blocked a real user and blamed a collision that cannot occur."""
    monkeypatch.setattr(security.settings, "admin_subjects", ["default"])
    with pytest.raises(RuntimeError, match="reserved|not a bearer subject"):
        security.validate_admin_subjects_settings()

    # A federated subject whose sub half is a reserved WORD is fine.
    monkeypatch.setattr(security.settings, "identity_provider", "oidc")
    monkeypatch.setattr(security.settings, "identity_oidc_issuer_label", "oidc")
    monkeypatch.setattr(security.settings, "admin_subjects", ["oidc:public"])
    security.validate_admin_subjects_settings()
    assert security.usable_admin_subjects() == frozenset({"oidc:public"})


def test_a_valid_allowlist_logs_only_the_count(monkeypatch, caplog):
    """The list names real people and is exactly what an attacker reading logs
    would want; only the count may be logged."""
    monkeypatch.setattr(security.settings, "admin_subjects", [ALICE, BOB])
    monkeypatch.setattr(security.settings, "identity_provider", "bvbrc")
    with caplog.at_level("INFO"):
        security.validate_admin_subjects_settings()
    rendered = " ".join(r.getMessage() for r in caplog.records)
    assert "2 bearer subject(s)" in rendered
    assert ALICE not in rendered and BOB not in rendered


def test_an_inert_allowlist_warns_at_startup(monkeypatch, caplog):
    """A non-empty allowlist with no identity provider means the operator
    believes they have an admin and does not — announce it, like
    ``_validate_ingest_root`` announces a silently-disabled capability."""
    monkeypatch.setattr(security.settings, "admin_subjects", [ALICE])
    monkeypatch.setattr(security.settings, "identity_provider", "none")
    with caplog.at_level("WARNING"):
        security.validate_admin_subjects_settings()
    assert any("inert" in r.getMessage() for r in caplog.records)


def test_an_unknown_issuer_prefix_warns_at_startup(monkeypatch, caplog):
    monkeypatch.setattr(security.settings, "admin_subjects", ["okta:alice"])
    monkeypatch.setattr(security.settings, "identity_provider", "bvbrc")
    monkeypatch.setattr(security.settings, "identity_oidc_issuer_label", "google")
    with caplog.at_level("WARNING"):
        security.validate_admin_subjects_settings()
    assert any("never match" in r.getMessage() for r in caplog.records)


def test_the_role_cache_ttl_is_capped_at_startup(monkeypatch):
    """The TTL is the DEMOTION lag: a revoked admin keeps admin for that long,
    so an operator cutting database load must not silently buy hours of it."""
    monkeypatch.setattr(security.settings, "admin_role_cache_ttl_seconds", 3600)
    with pytest.raises(RuntimeError, match="REVOKED admin"):
        security.validate_admin_role_cache_settings()
    monkeypatch.setattr(security.settings, "admin_role_cache_ttl_seconds", 300)
    security.validate_admin_role_cache_settings()


# --------------------------------------------------------------------------- #
# PATCH /v1/admin/users/{subject}/role
# --------------------------------------------------------------------------- #

KEYS = {"admin": "k-admin", "user": "k-user"}


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setattr(security.settings, "api_keys", list(KEYS.values()))
    monkeypatch.setattr(
        security.settings, "api_key_tenants", {"k-admin": "admin", "k-user": "user"}
    )
    monkeypatch.setattr(security.settings, "api_key_roles", {"k-admin": ROLE_ADMIN})


def _h(who: str) -> dict:
    return {"X-API-Key": KEYS[who]}


async def test_the_role_route_is_admin_only(client, keys, user_store):
    body = {"role": "admin"}
    path = f"/v1/admin/users/{ALICE}/role"
    assert (await client.patch(path, json=body)).status_code == 401
    assert (await client.patch(path, headers=_h("user"), json=body)).status_code == 403


async def test_granting_admin_records_the_audit_trail(client, keys, user_store):
    await user_store.upsert_seen(ALICE, "bvbrc")
    resp = await client.patch(
        f"/v1/admin/users/{ALICE}/role", headers=_h("admin"), json={"role": "admin"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["subject"] == ALICE and body["role"] == "admin"
    assert body["role_set_by"] == "admin" and body["role_set_at"]
    assert body["env_admin"] is False
    assert (await user_store.get(ALICE)).is_admin is True


async def test_an_unknown_role_is_400_and_an_unknown_subject_is_404(
    client, keys, user_store
):
    await user_store.upsert_seen(ALICE, "bvbrc")
    bad_role = await client.patch(
        f"/v1/admin/users/{ALICE}/role", headers=_h("admin"), json={"role": "wizard"}
    )
    assert bad_role.status_code == 400
    unknown = await client.patch(
        f"/v1/admin/users/{BOB}/role", headers=_h("admin"), json={"role": "admin"}
    )
    assert unknown.status_code == 404
    # ...and a role the caller cannot even name is a 400, not a stored value.
    assert (await user_store.get(ALICE)).role == ""


async def test_a_non_federated_subject_is_400(client, keys, user_store):
    """A colon-free subject is an API-key tenant, whose role lives in
    API_KEY_ROLES — refusing it here is the same namespace partition the
    service-account surface enforces from the other side."""
    for bad in ("loader", ":", "bvbrc:", ":alice"):
        resp = await client.patch(
            f"/v1/admin/users/{bad}/role", headers=_h("admin"), json={"role": "admin"}
        )
        assert resp.status_code == 400, bad


# --- the last-admin guard --------------------------------------------------
#
# The guard protects ONE state: a deployment whose users table is the only
# source of the admin role. That is not the `keys` fixture's deployment — an
# API-key admin lives in the environment, survives any write here, and is
# usually the very caller making the request — so these tests set the stage the
# guard is actually for: an identity provider on, the caller a STORED bearer
# admin, no ADMIN_SUBJECTS, and API keys that are all plain users.


@pytest.fixture
def stored_admins_only(monkeypatch, identity):
    """The only admin source is the users table."""
    monkeypatch.setattr(security.settings, "api_keys", ["k-user"])
    monkeypatch.setattr(security.settings, "api_key_tenants", {"k-user": "user"})
    monkeypatch.setattr(security.settings, "api_key_roles", {"k-user": ROLE_USER})
    return identity


async def _make_admin(user_store, subject: str) -> None:
    await user_store.upsert_seen(subject, "bvbrc")
    await user_store.set_role(subject, USER_ROLE_ADMIN, actor="bvbrc:root")


async def test_revoking_the_last_admin_is_refused(
    client, stored_admins_only, user_store
):
    """An unrecoverable lockout otherwise: the caller is the last admin, the
    route is admin-gated, and no environment source can mint another one."""
    await _make_admin(user_store, ALICE)  # ALICE is also the bearer caller
    resp = await client.patch(
        f"/v1/admin/users/{ALICE}/role", headers=_bearer(), json={"role": "user"}
    )
    assert resp.status_code == 409
    assert "ADMIN_SUBJECTS" in resp.json()["detail"]
    assert (await user_store.get(ALICE)).is_admin is True  # nothing was written


async def test_the_last_admin_guard_relaxes_when_admin_subjects_is_set(
    client, stored_admins_only, user_store, monkeypatch
):
    """The break-glass path IS the way back, so once it exists the guard has
    nothing left to protect."""
    await _make_admin(user_store, ALICE)
    monkeypatch.setattr(security.settings, "admin_subjects", [BOB])
    resp = await client.patch(
        f"/v1/admin/users/{ALICE}/role", headers=_bearer(), json={"role": "user"}
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "user"


async def test_an_allowlist_entry_that_can_never_match_does_not_relax_the_guard(
    client, stored_admins_only, user_store, monkeypatch, caplog
):
    """ADMIN_SUBJECTS entries whose issuer half no accepted token produces are
    inert — startup only WARNS about them. Counting one as a way back would let
    an operator typo silently turn the 409 into a 200 and remove the last
    in-API route to a new admin."""
    await _make_admin(user_store, ALICE)
    monkeypatch.setattr(security.settings, "identity_oidc_issuer_label", "google")
    monkeypatch.setattr(security.settings, "admin_subjects", ["okta:typo"])
    with caplog.at_level("WARNING", logger="ragstack.api.security"):
        security.validate_admin_subjects_settings()
    assert "can never match" in caplog.text  # the startup warning, verbatim
    resp = await client.patch(
        f"/v1/admin/users/{ALICE}/role", headers=_bearer(), json={"role": "user"}
    )
    assert resp.status_code == 409
    assert (await user_store.get(ALICE)).is_admin is True


async def test_which_admin_sources_count_as_a_way_back(monkeypatch):
    """`admin_recovery_sources` is the whole of the guard's stand-down rule, so
    each way in and each inert lookalike is pinned here rather than inferred
    from a status code."""
    monkeypatch.setattr(security.settings, "identity_provider", "bvbrc")
    monkeypatch.setattr(security.settings, "identity_oidc_issuer_label", "")
    monkeypatch.setattr(security.settings, "api_keys", ["k-user"])
    monkeypatch.setattr(security.settings, "api_key_tenants", {})
    monkeypatch.setattr(security.settings, "api_key_roles", {"k-user": ROLE_USER})
    monkeypatch.setattr(security.settings, "default_role", ROLE_USER)
    monkeypatch.setattr(security.settings, "admin_subjects", [])
    assert await security.admin_recovery_sources() == ()

    # A real allowlist entry: a way back.
    monkeypatch.setattr(security.settings, "admin_subjects", [ALICE])
    assert security.usable_admin_subjects() == frozenset({ALICE})
    assert await security.admin_recovery_sources() == ("ADMIN_SUBJECTS",)

    # ...but not one whose issuer half this deployment can never produce, and
    # not any entry at all when no provider is enabled: both are conditions
    # startup only WARNS about, and both name nobody who can authenticate.
    monkeypatch.setattr(security.settings, "admin_subjects", ["okta:alice"])
    assert security.usable_admin_subjects() == frozenset()
    monkeypatch.setattr(security.settings, "admin_subjects", [ALICE])
    monkeypatch.setattr(security.settings, "identity_provider", "none")
    assert security.usable_admin_subjects() == frozenset()
    assert await security.admin_recovery_sources() == ()

    # An API key mapped to admin is a way back...
    monkeypatch.setattr(security.settings, "api_key_roles", {"k-user": ROLE_ADMIN})
    assert await security.admin_recovery_sources() == ("API_KEY_ROLES",)
    # ...and so is DEFAULT_ROLE=admin, which every unmapped key inherits — the
    # production setting, and the reason the guard must not count only the
    # users table.
    monkeypatch.setattr(security.settings, "api_key_roles", {})
    monkeypatch.setattr(security.settings, "default_role", ROLE_ADMIN)
    assert await security.admin_recovery_sources() == ("DEFAULT_ROLE=admin",)
    monkeypatch.setattr(security.settings, "api_keys", [])
    assert await security.admin_recovery_sources() == (
        "DEFAULT_ROLE=admin (this deployment is keyless)",
    )


async def test_an_allowlist_entry_from_the_wrong_provider_is_not_a_way_back(
    monkeypatch,
):
    """Only ONE identity provider is ever active, so an entry naming the other
    one's issuer is exactly as inert as a third-party prefix — no token this
    server accepts can carry it. Counting it stood the last-admin guard down on
    a path nobody could ever walk, and (because the same rule drives the startup
    warning) told the operator nothing."""
    monkeypatch.setattr(security.settings, "api_keys", ["k-user"])
    monkeypatch.setattr(security.settings, "api_key_roles", {"k-user": ROLE_USER})
    monkeypatch.setattr(security.settings, "default_role", ROLE_USER)

    # A bvbrc deployment: an oidc: entry can never match.
    monkeypatch.setattr(security.settings, "identity_provider", "bvbrc")
    monkeypatch.setattr(security.settings, "identity_oidc_issuer_label", "oidc")
    monkeypatch.setattr(security.settings, "admin_subjects", ["oidc:alice"])
    assert security.usable_admin_subjects() == frozenset()
    assert await security.admin_recovery_sources() == ()

    # ...and the mirror image: an OIDC deployment cannot produce a bvbrc: one.
    monkeypatch.setattr(security.settings, "identity_provider", "oidc")
    monkeypatch.setattr(security.settings, "admin_subjects", [ALICE])
    assert security.usable_admin_subjects() == frozenset()
    assert await security.admin_recovery_sources() == ()

    # The matching entry for each provider still counts.
    monkeypatch.setattr(security.settings, "admin_subjects", ["oidc:alice"])
    assert security.usable_admin_subjects() == frozenset({"oidc:alice"})
    monkeypatch.setattr(security.settings, "identity_provider", "bvbrc")
    monkeypatch.setattr(security.settings, "admin_subjects", [ALICE])
    assert security.usable_admin_subjects() == frozenset({ALICE})


async def test_a_provider_name_is_normalized_before_the_guard_reads_it(monkeypatch):
    """`identity/factory.py` strips and lowercases, so IDENTITY_PROVIDER=BVBRC
    builds a WORKING provider. Comparing the raw string here made that
    deployment look provider-less: the live allowlist entry went uncounted and
    the guard refused a legitimate revoke — the guard's own failure mode
    inverted, with a startup warning calling the working path dead."""
    monkeypatch.setattr(security.settings, "api_keys", ["k-user"])
    monkeypatch.setattr(security.settings, "api_key_roles", {"k-user": ROLE_USER})
    monkeypatch.setattr(security.settings, "default_role", ROLE_USER)
    monkeypatch.setattr(security.settings, "admin_subjects", [ALICE])
    for raw in ("bvbrc", "BVBRC", " bvbrc ", "BvBrc"):
        monkeypatch.setattr(security.settings, "identity_provider", raw)
        assert security.usable_admin_subjects() == frozenset({ALICE}), raw
        assert await security.admin_recovery_sources() == ("ADMIN_SUBJECTS",), raw
    # ...and a padded "none" still reads as no provider at all.
    monkeypatch.setattr(security.settings, "identity_provider", " NONE ")
    assert security.usable_admin_subjects() == frozenset()


async def test_an_issuer_label_containing_a_colon_still_matches(monkeypatch):
    """The label is free-form config. Splitting a subject on its FIRST colon
    yielded a fragment that matched nothing, so every entry from such a
    deployment looked inert and the guard refused a legitimate revoke."""
    monkeypatch.setattr(security.settings, "identity_provider", "oidc")
    monkeypatch.setattr(security.settings, "identity_oidc_issuer_label", "my:idp")
    monkeypatch.setattr(security.settings, "api_keys", ["k-user"])
    monkeypatch.setattr(security.settings, "api_key_roles", {"k-user": ROLE_USER})
    monkeypatch.setattr(security.settings, "default_role", ROLE_USER)
    monkeypatch.setattr(security.settings, "admin_subjects", ["my:idp:alice"])
    assert security.usable_admin_subjects() == frozenset({"my:idp:alice"})
    assert await security.admin_recovery_sources() == ("ADMIN_SUBJECTS",)

    # A different label is still correctly inert.
    monkeypatch.setattr(security.settings, "admin_subjects", ["my:other:alice"])
    assert security.usable_admin_subjects() == frozenset()


async def test_a_disabled_service_account_key_is_not_a_way_back(
    monkeypatch, user_store
):
    """An env mapping is not liveness: a key whose tenant is a disabled service
    account 401s, so counting it lets an operator disable the last admin key,
    revoke the last stored admin, and find neither route open."""
    monkeypatch.setattr(security.settings, "identity_provider", "none")
    monkeypatch.setattr(security.settings, "admin_subjects", [])
    monkeypatch.setattr(security.settings, "api_keys", ["k-loader"])
    monkeypatch.setattr(security.settings, "api_key_tenants", {"k-loader": "loader"})
    monkeypatch.setattr(security.settings, "api_key_roles", {"k-loader": ROLE_ADMIN})
    monkeypatch.setattr(security.settings, "default_role", ROLE_USER)

    # Registered but still enabled: a genuine way back.
    await user_store.create_service_account("loader", created_by="test")
    assert await security.admin_recovery_sources() == ("API_KEY_ROLES",)

    # Disabled: the key now 401s, so it is no way back at all.
    await user_store.disable_service_account("loader", actor="test")
    assert await security.admin_recovery_sources() == ()


async def test_an_unanswerable_store_is_not_counted_as_a_way_back(monkeypatch):
    """The lockout guard inverts _service_account_disabled's fail-open policy:
    an unanswerable store must not license an irreversible revoke. A 409 the
    caller can retry is the safe direction."""
    monkeypatch.setattr(security.settings, "identity_provider", "none")
    monkeypatch.setattr(security.settings, "admin_subjects", [])
    monkeypatch.setattr(security.settings, "api_keys", ["k-loader"])
    monkeypatch.setattr(security.settings, "api_key_tenants", {"k-loader": "loader"})
    monkeypatch.setattr(security.settings, "api_key_roles", {"k-loader": ROLE_ADMIN})

    async def _boom(_subject):
        raise RuntimeError("acl database is unreachable")

    monkeypatch.setattr(security, "_service_account_disabled_strict", _boom)
    assert await security.admin_recovery_sources() == ()


async def test_an_api_key_admin_is_a_way_back_so_the_guard_stands_down(
    client, keys, user_store
):
    """The standard deployment: API_KEY_ROLES names an admin key. Refusing the
    revoke there would refuse it to a caller holding — and just having used —
    the credential that can grant it straight back."""
    await _make_admin(user_store, ALICE)
    resp = await client.patch(
        f"/v1/admin/users/{ALICE}/role", headers=_h("admin"), json={"role": "user"}
    )
    assert resp.status_code == 200
    assert await user_store.count_admins() == 0
    assert await security.admin_recovery_sources() == ("API_KEY_ROLES",)


async def test_an_unknown_role_for_the_last_admin_is_still_400(
    client, stored_admins_only, user_store
):
    """A typo'd role is a malformed REQUEST. Answering 'refusing to revoke
    admin' to a caller who never asked to revoke anything would collapse the
    400/409 split the contract documents."""
    await _make_admin(user_store, ALICE)
    resp = await client.patch(
        f"/v1/admin/users/{ALICE}/role", headers=_bearer(), json={"role": "wizard"}
    )
    assert resp.status_code == 400
    assert (await user_store.get(ALICE)).is_admin is True


async def test_the_router_delegates_the_guard_to_the_write(
    client, stored_admins_only, monkeypatch
):
    """The router asks the STORE to refuse, and never counts admins itself.

    That is the whole correctness argument: "is this the last admin" is a claim
    about the whole table, so a count taken out here — between the read and the
    write — is a TOCTOU. Two concurrent revokes of different rows would each
    observe the other's admin, both pass their check, and the deployment would
    land on zero admins with no way back. ``test_concurrent_revokes_cannot_
    strand_the_deployment`` in tests/unit/test_user_store.py races the store
    itself; this pins the router to it.
    """
    seen: dict = {}

    class RecordingStore(InMemoryUserStore):
        async def set_role(self, subject, role, actor, *, require_remaining_admin=False):
            seen["guarded"] = require_remaining_admin
            return await super().set_role(
                subject, role, actor, require_remaining_admin=require_remaining_admin
            )

        async def count_admins(self) -> int:
            seen["counted_outside_the_write"] = True
            return await super().count_admins()

    store = RecordingStore()
    set_user_store(store)
    try:
        await _make_admin(store, ALICE)
        resp = await client.patch(
            f"/v1/admin/users/{ALICE}/role", headers=_bearer(), json={"role": "user"}
        )
        assert resp.status_code == 409
        assert seen == {"guarded": True}

        # ...and with a way back in the environment the flag flips off, which is
        # the ONLY thing this layer gets to decide.
        seen.clear()
        monkeypatch.setattr(security.settings, "admin_subjects", [BOB])
        resp = await client.patch(
            f"/v1/admin/users/{ALICE}/role", headers=_bearer(), json={"role": "user"}
        )
        assert resp.status_code == 200
        assert seen == {"guarded": False}
    finally:
        reset_user_store()


async def test_a_penultimate_admin_can_be_revoked(
    client, stored_admins_only, user_store
):
    for subject in (ALICE, BOB):
        await _make_admin(user_store, subject)
    resp = await client.patch(
        f"/v1/admin/users/{BOB}/role", headers=_bearer(), json={"role": "user"}
    )
    assert resp.status_code == 200
    assert await user_store.count_admins() == 1


async def test_env_admin_is_surfaced_so_a_revoke_is_not_misread(
    client, keys, user_store, monkeypatch
):
    """Revoking the stored role of an allowlisted subject does NOT take their
    admin away — the response has to say so, or an operator will believe they
    revoked something they did not."""
    await user_store.upsert_seen(ALICE, "bvbrc")
    monkeypatch.setattr(security.settings, "admin_subjects", [ALICE])
    resp = await client.patch(
        f"/v1/admin/users/{ALICE}/role", headers=_h("admin"), json={"role": "user"}
    )
    assert resp.status_code == 200
    assert resp.json()["env_admin"] is True


async def test_a_store_outage_on_the_role_route_is_503(client, keys):
    set_user_store(BrokenStore())
    try:
        resp = await client.patch(
            f"/v1/admin/users/{ALICE}/role", headers=_h("admin"), json={"role": "admin"}
        )
        assert resp.status_code == 503
    finally:
        reset_user_store()


async def test_a_grant_flushes_this_process_role_cache(
    client, keys, identity, user_store, monkeypatch
):
    """Without the flush the grant would wait out the TTL even in the process
    that performed it."""
    await user_store.upsert_seen(ALICE, "bvbrc")
    assert await _role(client) == ROLE_USER  # caches "not admin"
    resp = await client.patch(
        f"/v1/admin/users/{ALICE}/role", headers=_h("admin"), json={"role": "admin"}
    )
    assert resp.status_code == 200
    assert await _role(client) == ROLE_ADMIN
