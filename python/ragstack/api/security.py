"""Authentication + RBAC for the v1 routers.

Two credential types, both resolved server-side (never from the request body, so
neither can be spoofed):

- ``X-API-Key`` — a per-deployment key mapped to a tenant and a role. Existing.
- ``Authorization: Bearer <credential>`` — a user identity verified by the
  configured :class:`~ragstack.identity.base.IdentityProvider` (spec §5.0). Off
  unless ``IDENTITY_PROVIDER`` names one; while it is ``none`` the header is not
  an authentication input at all and behaviour is unchanged.

Two layers come out of that:

- **Tenant** (data isolation) — which corpus the caller reads/writes. For a
  bearer identity it is ``f"{issuer}:{subject}"``, which keeps a BV-BRC ``alice``
  distinct from a Google ``alice`` and both clear of the reserved ``public`` /
  ``default``.
- **Role** (authz) — what surface the caller may use: ``admin`` (superuser) |
  ``user``. ``researcher`` is a deprecated alias for ``user`` (normalized with a
  warning wherever roles are read); ``engineer``/``manager`` were removed per
  ADR-0003 and are rejected at startup. ``require_role`` gates an endpoint;
  a UI role-gate is UX only — authorization is always re-checked here. A bearer
  identity gets the explicit ``ROLE_USER`` unless an explicit server-side admin
  source names this subject, and **never** ``default_role``: prod runs
  ``DEFAULT_ROLE=admin``, so falling through would make every authenticated end
  user a superuser.

There are exactly two admin sources for a bearer identity, and both are
server-side, so a token can never elevate the caller presenting it:

1. ``ADMIN_SUBJECTS`` — an operator-set env allowlist of ``issuer:subject``
   strings. Evaluated FIRST, as a pure frozenset membership test with no I/O:
   that is what makes admin reachable on an empty users table (bootstrap), what
   keeps it working through a user-store outage, and what makes it break-glass
   that no database write can revoke.
2. ``users.role == 'admin'`` — written only by an existing admin through
   ``PATCH /v1/admin/users/{subject}/role``. A store read, memoized per subject,
   and it FAILS CLOSED to ``ROLE_USER``: a store outage must never hand out
   admin, and briefly losing it is the safe direction.

A verified bearer authentication also schedules a fire-and-forget profile
upsert (ADR-0004 decision 1) — ``users(subject, …)`` keyed on the tenant
string. It must never fail, slow, or 500 the auth path, so the task is wrapped
and every exception is logged at debug. API-key principals still get no user row
WRITTEN here: a key is a deployment credential, not a person, and nothing on the
key path upserts. The key path does perform one READ (issue #258): a key whose
tenant is a *registered and disabled* service account is rejected with 401 — the
only way to revoke a leaked key without an env edit and a restart. That read is
cached per subject and FAILS OPEN; see :func:`_service_account_disabled`.

Presenting both credentials is a **400**, not a silent precedence rule: which one
authenticated you would otherwise be invisible in the logs and in the tenant.
"""
from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from ragstack.config import settings
from ragstack.identity import (
    Identity,
    IdentityInvalid,
    IdentityProvider,
    IdentityUnavailable,
    get_identity_provider,
)
from ragstack.tenancy import DEFAULT_TENANT

logger = logging.getLogger(__name__)

API_KEY_HEADER = "X-API-Key"
AUTHORIZATION_HEADER = "Authorization"
_api_key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)
# APIKeyHeader (not HTTPBearer): the BV-BRC wire format has no ``Bearer `` prefix,
# so the prefix is optional here and HTTPBearer's mandatory-scheme parsing would
# reject a perfectly good token.
# scheme_name is explicit: FastAPI names a security scheme after its class, so a
# second unnamed APIKeyHeader would overwrite the X-API-Key entry in the generated
# OpenAPI and quietly mislabel the existing scheme as `Authorization`.
_authorization_header = APIKeyHeader(
    name=AUTHORIZATION_HEADER, auto_error=False, scheme_name="BearerIdentity"
)

# RBAC roles. ``admin`` is a superuser: it satisfies every ``require_role`` check.
ROLE_ADMIN = "admin"
ROLE_USER = "user"
#: Deprecated alias for :data:`ROLE_USER` (pre-ADR-0003 vocabulary). Accepted
#: wherever roles are read and normalized to ``user`` with a warning; the
#: ``engineer``/``manager`` roles were removed outright (startup rejects them).
ROLE_RESEARCHER = "researcher"
VALID_ROLES = frozenset({ROLE_ADMIN, ROLE_USER})
#: Roles that existed pre-ADR-0003 but no longer do. Named in the startup
#: rejection so an operator knows this is a deliberate removal, not a typo.
_REMOVED_ROLES = frozenset({"engineer", "manager"})

_warned_researcher_alias = False


def normalize_role(role: str) -> str:
    """Map the deprecated ``researcher`` alias to ``user`` (warning once per
    process). Every place a role is *read* — ``api_key_roles`` values,
    ``default_role``, the bearer path — goes through this, so a config written
    against the old vocabulary keeps working while announcing its own age."""
    global _warned_researcher_alias
    if role == ROLE_RESEARCHER:
        if not _warned_researcher_alias:
            logger.warning(
                "role 'researcher' is a deprecated alias for 'user' (ADR-0003); "
                "update DEFAULT_ROLE / API_KEY_ROLES"
            )
            _warned_researcher_alias = True
        return ROLE_USER
    return role


@dataclass(frozen=True)
class Principal:
    """The authenticated caller: its data tenant, its RBAC role, and — when it
    authenticated with a bearer credential — the verified credential itself.

    ``token`` is carried so downstream calls can act *as the user* (the Workspace
    probe in spec §5.1), and ``token_id`` / ``token_exp`` are what the
    authorization cache is keyed and expired on. Both are set **only after
    signature verification**; a credential that failed to verify never reaches a
    ``Principal``.

    ``__repr__`` redacts ``token``: a Principal ends up in exception context,
    debug logs and tracebacks, and a bearer credential printed there is a
    reusable credential in a log aggregator.
    """

    tenant: str
    role: str
    token: str | None = None
    token_id: str | None = None
    token_exp: int | None = None

    def __repr__(self) -> str:
        token = "'***'" if self.token else repr(self.token)
        return (
            f"Principal(tenant={self.tenant!r}, role={self.role!r}, token={token}, "
            f"token_id={self.token_id!r}, token_exp={self.token_exp!r})"
        )


def _principal_from_key(api_key: str | None) -> Principal:
    """Authenticate ``api_key`` and resolve (tenant, role), or raise 401.

    Keyless (no ``api_keys`` configured) is the open dev/test path: the caller is
    the ``default`` tenant with ``default_role`` (production's startup check
    forbids keyless). Otherwise the key is verified in constant time and mapped to
    its tenant/role; an unmapped-but-valid key gets the default tenant/role.
    """
    keys = settings.api_keys
    if not keys:
        return Principal(tenant=DEFAULT_TENANT, role=normalize_role(settings.default_role))
    # sum() over the generator evaluates every compare_digest (no short-circuit),
    # so total time doesn't reveal which key matched or how far down the list.
    # compare_digest raises TypeError on a non-ASCII str, and Starlette decodes
    # header bytes as latin-1 — so one high byte in X-API-Key used to escape as a
    # 500 from an unauthenticated caller. A key that cannot match any ASCII key
    # is simply invalid.
    if api_key is not None and not api_key.isascii():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid API key",
        )
    if api_key is not None and sum(secrets.compare_digest(api_key, k) for k in keys) > 0:
        return Principal(
            tenant=settings.api_key_tenants.get(api_key, DEFAULT_TENANT),
            role=normalize_role(
                settings.api_key_roles.get(api_key, settings.default_role)
            ),
        )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="missing or invalid API key",
    )


# --------------------------------------------------------------------------- #
# Service-account revocation on the API-key path (issue #258)
# --------------------------------------------------------------------------- #
#
# ``settings.api_keys`` / ``api_key_tenants`` / ``api_key_roles`` are read from
# the environment at import and have no writer anywhere in the tree, so
# rotating or withdrawing a credential is an env edit plus a restart. That is
# the operational gap issue #258 names: a leaked key cannot be stopped at
# runtime. The registered-service-account record closes it — an admin disables
# the account, and this check turns the still-valid key into a 401.
#
# Per-subject memoization, mirroring the identity cache's discipline (and its
# honest framing: ``identity_cache_ttl_seconds`` is capped precisely because the
# TTL *is* the revocation lag). Without it this read would run on every single
# API-key request, coupling a path that was pure CPU — a dict lookup and a
# constant-time compare — to the ACL database's p99.
_disabled_cache: dict[str, tuple[float, bool]] = {}
#: Bumped by :func:`reset_disabled_cache`. A lookup samples this BEFORE its
#: store read and refuses to write its verdict back if the value changed while
#: it was in flight: otherwise a request that started before an operator's
#: ``/disable`` resumes afterwards and re-installs its stale "enabled" verdict,
#: silently voiding the flush for a full TTL (found in review of #258).
_disabled_cache_gen = 0
# Prune trigger only; the dict stays bounded by subjects active in one window.
_DISABLED_CACHE_MAX = 10_000
# Warn once per OUTAGE when the lookup starts failing, then drop to debug — an
# operator must be able to see at INFO that revocation has stopped working,
# without a dead store spamming a line per request. Re-armed on the next
# successful lookup (and a recovery is logged), so this is once per outage
# rather than once per process: without that, a single blip during boot would
# demote every later real outage to DEBUG, hiding "revocation is off" for the
# rest of the process's life. That is the opposite of the intent, and unlike
# ``_upsert_failure_warned`` — which guards a best-effort profile write — the
# thing failing here is a revocation control that fails OPEN.
_disabled_lookup_failure_warned = False


def _disabled_cache_ttl() -> float:
    """Cache lifetime in seconds; 0 (or negative, clamped) disables caching."""
    return max(0.0, float(settings.service_account_disabled_cache_ttl_seconds))


def reset_disabled_cache() -> None:
    """Drop the memoized disabled-lookups (tests, and any deliberate flush).

    Bumping the generation is what makes the flush hold: a lookup already
    awaiting its store read would otherwise finish and re-install the verdict
    it read *before* the flush, keeping a just-revoked key alive for a full TTL.
    """
    global _disabled_cache_gen
    _disabled_cache.clear()
    _disabled_cache_gen += 1


async def _service_account_disabled(subject: str) -> bool:
    """Is ``subject`` a REGISTERED service account that an admin has DISABLED?

    **Failure policy: FAIL OPEN.** Any store error is swallowed and answered
    ``False`` — the request proceeds. This is deliberate and is the opposite of
    how *authorization* behaves in this codebase (``authz.AuthzUnavailable`` →
    503, ``groups._unavailable``): the API key is the primary authentication
    factor and it has *already* been verified by a constant-time compare, so the
    caller is authenticated regardless of what the user store can say. The
    disable flag is a revocation convenience layered on top of that. Failing
    closed would trade a working authentication path for it — in this deployment
    the API-key path is the ingest path and the whole production surface, so a
    partitioned or slow ACL database would lock out every API-key caller,
    including all the accounts nobody ever disabled. That is a bad bargain, and
    the honest consequence is written down rather than hidden:

        **DISABLING IS A SOFT, BEST-EFFORT REVOKE. THE AUTHORITATIVE REVOKE IS
        REMOVING THE KEY FROM ``API_KEYS`` AND RESTARTING.**

    An UNREGISTERED subject (no users row) is ``False``: registration is opt-in
    and never becomes a requirement for an existing key. A ``human`` row is
    ``False`` too — only ``kind='service'`` rows carry this flag, and a bearer
    identity never reaches this path anyway.

    The answer is cached per subject for
    ``settings.service_account_disabled_cache_ttl_seconds``, so **revocation
    takes effect within that TTL** (per process). Fail-open answers are cached
    on the same terms: during an outage that bounds the store hammering to one
    attempt per subject per window instead of one per request, and it cannot
    make the revoke any weaker than fail-open already made it.
    """
    ttl = _disabled_cache_ttl()
    now = time.monotonic()
    # Sampled BEFORE the await; compared after. A flush that lands mid-flight
    # must invalidate this lookup's verdict, not be overwritten by it.
    gen = _disabled_cache_gen
    if ttl > 0:
        hit = _disabled_cache.get(subject)
        if hit is not None and now < hit[0]:
            return hit[1]

    disabled = False
    global _disabled_lookup_failure_warned
    try:
        from ragstack.user_store import get_user_store  # lazy: avoid import cycles

        rec = await get_user_store().get(subject)
        disabled = rec is not None and rec.is_service and not rec.enabled
        if _disabled_lookup_failure_warned:
            # Re-arm: the NEXT outage warns at WARNING again, and the operator
            # who saw the fail-open warning gets the matching all-clear.
            _disabled_lookup_failure_warned = False
            logger.warning(
                "service-account disabled check recovered; revocation is being "
                "enforced again"
            )
    except Exception:  # noqa: BLE001 — fail OPEN; see the docstring
        level = logging.DEBUG if _disabled_lookup_failure_warned else logging.WARNING
        _disabled_lookup_failure_warned = True
        logger.log(
            level,
            "service-account disabled check failed; failing open — API-key auth "
            "proceeds on the verified key alone, so a disabled account is NOT "
            "revoked while this persists",
            exc_info=True,
        )
        disabled = False

    # Only memoize if no flush landed while the store read was in flight. On a
    # stale generation the verdict still applies to THIS request (it is the
    # freshest read we have) but must not be cached for the next one.
    if ttl > 0 and gen == _disabled_cache_gen:
        if len(_disabled_cache) >= _DISABLED_CACHE_MAX:
            for key, (expiry, _) in list(_disabled_cache.items()):
                if expiry <= now:
                    del _disabled_cache[key]
            if len(_disabled_cache) >= _DISABLED_CACHE_MAX:
                _disabled_cache.clear()
        _disabled_cache[subject] = (now + ttl, disabled)
    return disabled


async def _principal_from_key_checked(api_key: str | None) -> Principal:
    """:func:`_principal_from_key` plus the disabled-service-account gate.

    Kept as a wrapper rather than folded into ``_principal_from_key`` so that
    function stays SYNC and I/O-free: it is the pure "does this key verify, and
    what does the env map it to" step, unit-tested directly as such, and the
    tenant/role it returns remain env-derived and authoritative. Nothing the
    store says can *change* a principal here — the lookup may only refuse one.
    """
    principal = _principal_from_key(api_key)
    if await _service_account_disabled(principal.tenant):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="service account is disabled",
        )
    return principal


# --------------------------------------------------------------------------- #
# Bearer admin resolution: ADMIN_SUBJECTS, then users.role
# --------------------------------------------------------------------------- #
#
# A bearer identity may be an admin, but ONLY by deliberate assignment. Nothing
# that travels with the credential — an OIDC claim, a BV-BRC token field, the
# Authorization header itself — is an input here; ``Identity`` deliberately
# carries no role. The two admissible sources are an env allowlist the operator
# set and a users row an existing admin wrote, neither of which is reachable by
# the caller presenting the token.
#
# PRECEDENCE IS ONE-DIRECTIONAL AND DELIBERATE: the env allowlist is evaluated
# first and short-circuits, so a stored ``user`` role can never demote an
# allowlisted subject. It is a pure frozenset membership test with no I/O and no
# failure mode, which is exactly what makes it usable as break-glass — it works
# on an empty users table (the bootstrap case: the grant route is itself
# admin-gated, so a store-only design would 403 the very operator trying to
# create the first admin), it survives a store outage, and no database write can
# take it away. The auth path never writes it back into the users table either:
# that would be the login path writing an identity-class column, which
# ``_SEEN_ASSIGN_COLUMNS`` exists to make impossible.

_role_cache: dict[str, tuple[float, bool]] = {}
#: Bumped by :func:`reset_role_cache`, and sampled BEFORE the store read for the
#: same reason as ``_disabled_cache_gen``: a lookup that started before an
#: operator's revoke must not resume afterwards and re-install its stale
#: "admin" verdict for a full TTL.
_role_cache_gen = 0
# Prune trigger only; the dict stays bounded by subjects active in one window.
_ROLE_CACHE_MAX = 10_000
#: Warn once per OUTAGE, then drop to debug, re-arming (and logging recovery) on
#: the next successful lookup — the ``_disabled_lookup_failure_warned`` shape.
#: "every admin surface is suddenly 403ing" is an operator-visible symptom and
#: must be explicable from the logs at the default level.
_role_lookup_failure_warned = False


def admin_subject_allowlist() -> frozenset[str]:
    """``ADMIN_SUBJECTS`` as a set, read at CALL time.

    Deliberately not frozen at import: a module-level constant would make the
    setting unmonkeypatchable and silently pin one allowlist for a whole test
    session (and for any process that reloads settings). The set is tiny — an
    operator-typed list — so rebuilding it per call is cheaper than the cache
    invalidation it would need.
    """
    return frozenset(settings.admin_subjects or ())


def _configured_provider() -> str:
    """``identity_provider``, normalized the way the identity layer reads it.

    ``identity/factory.py`` does ``.strip().lower()`` at every branch, so
    ``IDENTITY_PROVIDER=BVBRC`` (or a padded value out of a ``.env``) builds a
    working provider. Comparing the raw string here made such a deployment look
    like it had NO provider: the live ``ADMIN_SUBJECTS`` break-glass entry became
    invisible to the last-admin guard, which then refused a legitimate revoke —
    and the startup warning told the operator their working allowlist was dead.
    """
    return (settings.identity_provider or "").strip().lower()


def _known_issuer_labels() -> frozenset[str]:
    """Issuer halves the CONFIGURED provider can actually produce.

    Keyed off ``identity_provider``, not off the union of every label the code
    knows: exactly one provider is ever active, so on a ``bvbrc`` deployment an
    ``oidc:*`` entry is as inert as an ``okta:*`` one — no token this server
    accepts will ever carry that issuer. Taking the union instead let a
    cross-provider entry pose as a live break-glass path and stand the
    last-admin guard down (and, because it also suppressed the startup warning,
    with no signal to the operator at all).

    ``bvbrc`` is the provider's fixed label; the OIDC one is configurable, and an
    empty one is dropped so an unset ``IDENTITY_OIDC_ISSUER_LABEL`` cannot make
    ``'':sub`` look known.
    """
    provider = _configured_provider()
    if provider == "bvbrc":
        return frozenset({"bvbrc"})
    if provider == "oidc":
        return frozenset(
            label for label in (settings.identity_oidc_issuer_label,) if label
        )
    # none / unset: nothing can present a bearer credential, so no issuer half
    # is producible. usable_admin_subjects() short-circuits on this too.
    return frozenset()


def usable_admin_subjects() -> frozenset[str]:
    """The ``ADMIN_SUBJECTS`` entries that could actually elevate SOMEBODY here.

    :func:`admin_subject_allowlist` is what the auth path matches against, and it
    stays deliberately literal. This is the narrower question the last-admin
    guard has to ask: "is the break-glass path real?" The two conditions that
    :func:`validate_admin_subjects_settings` only WARNS about at startup —
    no identity provider (nothing can present a bearer credential at all), and
    an issuer prefix no accepted token produces — are exactly the ones that make
    an entry inert, and an inert entry must not be mistaken for a way back in.

    Without this an operator typo (``okta:alice`` on a bvbrc deployment) would
    silently convert the last-admin 409 into a 200 and remove the only in-API
    route to a new admin.
    """
    entries = admin_subject_allowlist()
    if not entries or _configured_provider() in ("", "none"):
        return frozenset()
    known = _known_issuer_labels()
    # Prefix match, not a split on the FIRST colon: an issuer label is free-form
    # config and may itself contain a colon, in which case splitting yields a
    # fragment that matches nothing and a live allowlist entry looks inert.
    return frozenset(e for e in entries if any(e.startswith(f"{k}:") for k in known))


async def _api_key_admin_source() -> str:
    """How an API-key caller becomes admin here, or ``""`` if none can.

    Mirrors :func:`_principal_from_key`'s own mapping rather than restating it:
    keyless means every caller gets ``default_role``, and a configured key gets
    ``api_key_roles[key]`` falling back to ``default_role``. So an unlisted key
    IS an admin when ``DEFAULT_ROLE=admin`` (which production runs), and a
    deployment whose every key is explicitly non-admin has no API-key admin even
    though ``API_KEY_ROLES`` is non-empty.

    **Liveness matters here, not just configuration.** Since #258 a key whose
    tenant is a registered-and-disabled service account is 401'd by
    :func:`_principal_from_key_checked`, so an env mapping alone does not prove
    anybody can still authenticate as an admin. Counting a dead credential as
    the way back is how an operator disables the last admin service account,
    revokes the last stored admin, and finds neither route open.

    Note the deliberate inversion of :func:`_service_account_disabled`'s
    fail-open policy: there, a store error must not break an already-verified
    credential. Here the question is "is there a way back in?", so an
    unanswerable store makes us answer NO — the caller gets a 409 they can
    retry, instead of an irreversible revoke.
    """
    default = normalize_role(settings.default_role)
    if not settings.api_keys:
        return "DEFAULT_ROLE=admin (this deployment is keyless)" if default == ROLE_ADMIN else ""
    for key in settings.api_keys:
        if normalize_role(settings.api_key_roles.get(key, default)) != ROLE_ADMIN:
            continue
        tenant = settings.api_key_tenants.get(key, DEFAULT_TENANT)
        try:
            if await _service_account_disabled_strict(tenant):
                continue  # a key that 401s is not a way back
        except Exception:  # noqa: BLE001 — unanswerable store => not a way back
            logger.warning(
                "could not confirm whether the admin API key's tenant is a "
                "disabled service account; treating it as NO recovery source so "
                "a last-admin revoke is refused rather than made irreversible",
                exc_info=True,
            )
            continue
        return "API_KEY_ROLES" if settings.api_key_roles else "DEFAULT_ROLE=admin"
    return ""


async def _service_account_disabled_strict(subject: str) -> bool:
    """:func:`_service_account_disabled` without the fail-open swallow.

    Deliberately uncached: this asks a one-off question on a route that is not
    hot, and reusing the disabled cache would let a verdict memoized for the
    auth path decide a lockout refusal. A store error propagates instead of
    answering ``False`` — see :func:`_api_key_admin_source` for why the polarity
    flips.
    """
    from ragstack.user_store import get_user_store

    rec = await get_user_store().get(subject)
    return rec is not None and rec.is_service and not rec.enabled


async def admin_recovery_sources() -> tuple[str, ...]:
    """Admin sources that a users-table write cannot take away.

    The last-admin guard's real question. Revoking the final stored admin is
    only a lockout when NOTHING else can produce one, and the users table is not
    the only source: ``ADMIN_SUBJECTS`` is the break-glass allowlist, and an
    API-key principal's role comes from ``API_KEY_ROLES``/``DEFAULT_ROLE`` — an
    API-key admin can call the grant route (and in the standard deployment is
    the caller doing so). Counting only stored admins refuses a legitimate
    revoke and prints remediation the operator is already holding.

    Returns the names of the surviving sources, so a refusal can say precisely
    which ones are missing.
    """
    sources = []
    if usable_admin_subjects():
        sources.append("ADMIN_SUBJECTS")
    key_source = await _api_key_admin_source()
    if key_source:
        sources.append(key_source)
    return tuple(sources)


def _role_cache_ttl() -> float:
    """Cache lifetime in seconds; 0 (or negative, clamped) disables caching."""
    return max(0.0, float(settings.admin_role_cache_ttl_seconds))


def reset_role_cache() -> None:
    """Drop the memoized stored-role lookups (tests, and any deliberate flush).

    Called by the grant/revoke route so a demotion is not waiting out a TTL in
    THIS process. Every sibling uvicorn worker still does — the TTL is the
    demotion lag, and this narrows it rather than removing it.
    """
    global _role_cache_gen
    _role_cache.clear()
    _role_cache_gen += 1


async def _stored_role_is_admin(subject: str) -> bool:
    """Does ``subject``'s users row store ``role='admin'``?

    **Failure policy: FAIL CLOSED.** Any store error answers ``False`` and the
    caller stays :data:`ROLE_USER`. This is the exact mirror of
    :func:`_service_account_disabled`, which fails OPEN — and the asymmetry is
    the whole point. There, the store can only REFUSE an already-verified
    credential, so failing closed would lock out every API-key caller during an
    outage for the sake of a revocation convenience. Here the store can only
    GRANT privilege, so failing open would hand out superuser over every
    collection in the deployment whenever the ACL database hiccups. Losing admin
    briefly is recoverable; granting it wrongly is not.

    The request is NOT failed: the caller authenticated on their verified token
    and keeps their tenant and their own data. Only the elevation is withheld,
    and ``ADMIN_SUBJECTS`` — which needs no store at all — still elevates,
    which is why it is evaluated first.

    Memoized per subject for ``settings.admin_role_cache_ttl_seconds``. THAT TTL
    IS THE DEMOTION LAG (a revoked admin keeps admin for up to that long, per
    process), which is why it is capped at startup and flushed by the grant
    route — the opposite direction from the disabled cache, where the TTL only
    delays a denial.
    """
    ttl = _role_cache_ttl()
    now = time.monotonic()
    # Sampled BEFORE the await; compared after.
    gen = _role_cache_gen
    if ttl > 0:
        hit = _role_cache.get(subject)
        if hit is not None and now < hit[0]:
            return hit[1]

    is_admin = False
    global _role_lookup_failure_warned
    try:
        from ragstack.user_store import get_user_store  # lazy: avoid import cycles

        rec = await get_user_store().get(subject)
        is_admin = rec is not None and rec.is_admin
        if _role_lookup_failure_warned:
            _role_lookup_failure_warned = False
            logger.warning(
                "stored admin-role lookup recovered; users.role grants are being "
                "honoured again"
            )
    except Exception:  # noqa: BLE001 — fail CLOSED; see the docstring
        level = logging.DEBUG if _role_lookup_failure_warned else logging.WARNING
        _role_lookup_failure_warned = True
        logger.log(
            level,
            "stored admin-role lookup failed; failing closed — bearer callers "
            "resolve to the 'user' role and every users.role admin grant is "
            "withheld while this persists (ADMIN_SUBJECTS still applies)",
            exc_info=True,
        )
        is_admin = False

    # Only memoize if no flush landed while the store read was in flight.
    if ttl > 0 and gen == _role_cache_gen:
        if len(_role_cache) >= _ROLE_CACHE_MAX:
            for key, (expiry, _) in list(_role_cache.items()):
                if expiry <= now:
                    del _role_cache[key]
            if len(_role_cache) >= _ROLE_CACHE_MAX:
                _role_cache.clear()
        _role_cache[subject] = (now + ttl, is_admin)
    return is_admin


async def _bearer_role(subject: str) -> str:
    """The RBAC role for a verified bearer ``subject``.

    Written as a POSITIVE branch: :data:`ROLE_ADMIN` is assigned in exactly one
    place — "an explicit server-side admin source names this subject" — so every
    other path, including every exception, timeout and store outage inside
    :func:`_stored_role_is_admin`, structurally lands on the :data:`ROLE_USER`
    literal. ``settings.default_role`` is not consulted and must never appear in
    this function or its callers: it is ``admin`` in production, and inheriting
    it would make every authenticated end user a superuser.
    """
    if subject in admin_subject_allowlist():
        # No store read, no row-existence precondition: the break-glass path.
        return ROLE_ADMIN
    if await _stored_role_is_admin(subject):
        return ROLE_ADMIN
    return ROLE_USER


def _bearer_credential(authorization: str | None) -> str:
    """The credential inside an ``Authorization`` header value.

    ``Bearer `` is stripped when present and simply absent for a raw BV-BRC token
    (whose wire format carries no scheme). Returns ``""`` when there is nothing to
    authenticate with.
    """
    if not authorization:
        return ""
    value = authorization.strip()
    if value[:7].lower() == "bearer ":
        value = value[7:].strip()
    return value


# Strong references to in-flight profile-upsert tasks: a bare fire-and-forget
# task is GC-able mid-flight, and a dropped one dies silently.
_pending: set[asyncio.Task] = set()

# Per-subject debounce for the profile upsert. resolve_principal memoizes only
# per *request*, and the identity provider's cache caches verification — not
# this hook — so without a debounce every bearer-authenticated request would
# emit a users-table write transaction (a per-request write hotspot under a
# polling UI, and an unbounded _pending set under a burst with a slow store).
# One write per subject per window is plenty: the row is idempotent and
# last_seen_at at minute granularity is all ADR-0004 needs.
_UPSERT_DEBOUNCE_SECONDS = 300.0
_upsert_last: dict[str, float] = {}
# Prune trigger only — the dict stays bounded by subjects active in one window.
_UPSERT_LAST_PRUNE_AT = 10_000

# Profile claims are caller-influenced (OIDC `name`/`email` pass only
# isinstance(str) checks upstream), so bound what gets persisted: a
# validly-signed token carrying a multi-megabyte `name` must not become an
# unbounded users-table row, and control characters must not reach whatever
# later renders display_name. 254 is the RFC 5321 address ceiling; 256 is a
# generous human-name budget.
_PROFILE_CLAIM_MAX = 256
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def clean_stored_text(value: str, limit: int = _PROFILE_CLAIM_MAX) -> str:
    """Strip control characters, then truncate — the one sanitizer for any
    caller-supplied string this process persists into the users table.

    Public because the service-account router stores an admin-supplied
    ``purpose`` in the same table and must apply the same rule, not just the
    length half of it (both values are echoed back by a GET and reach logs and
    terminals, where a raw ESC is an output-context injection).
    """
    return _CONTROL_CHARS.sub("", value)[:limit]


async def _upsert_profile(identity: Identity) -> None:
    """Record the verified authentication in the user store (ADR-0004).

    The row is keyed on the tenant string ``f"{issuer}:{sub}"`` — never an
    email. An *unverified* email claim is not written: it would later be
    indistinguishable from a verified one in the row, and ADR-0004's pending
    shares must never be claimable by an unverified address.
    """
    from ragstack.user_store import get_user_store  # lazy: avoid import cycles

    await get_user_store().upsert_seen(
        subject=f"{identity.issuer}:{identity.subject}",
        issuer=identity.issuer,
        email=clean_stored_text(identity.email) if identity.email_verified else "",
        display_name=clean_stored_text(identity.display_name),
    )


def _should_upsert(subject: str) -> bool:
    """True when ``subject`` has not had an upsert scheduled this window.

    Records the attempt immediately (not on completion): a failing store must
    not turn the debounce off and hammer itself once per request.
    """
    now = time.monotonic()
    last = _upsert_last.get(subject)
    if last is not None and now - last < _UPSERT_DEBOUNCE_SECONDS:
        return False
    if len(_upsert_last) >= _UPSERT_LAST_PRUNE_AT:
        cutoff = now - _UPSERT_DEBOUNCE_SECONDS
        for key, stamp in list(_upsert_last.items()):
            if stamp < cutoff:
                del _upsert_last[key]
    _upsert_last[subject] = now
    return True


# Warn once per process when profile writes start failing, then drop to debug:
# a misconfigured/unreachable user store would otherwise be invisible at the
# default INFO level while silently recording nobody.
_upsert_failure_warned = False


def _schedule_profile_upsert(identity: Identity, subject: str) -> None:
    """Fire-and-forget the profile upsert, debounced per ``subject`` (the
    caller's already-built ``f"{issuer}:{sub}"`` string, so the debounce key and
    the row key cannot drift from the Principal's tenant).

    Authentication must never fail, slow, or 500 because the profile write did —
    ANY exception (scheduling or execution) is swallowed. The first failure is
    logged at WARNING (an operator must be able to see a dead user store at the
    default log level); repeats drop to debug."""
    if not _should_upsert(subject):
        return

    async def _run() -> None:
        try:
            await _upsert_profile(identity)
        except Exception:  # noqa: BLE001 — never let a profile write hurt auth
            global _upsert_failure_warned
            level = logging.DEBUG if _upsert_failure_warned else logging.WARNING
            _upsert_failure_warned = True
            logger.log(level, "user profile upsert failed", exc_info=True)

    try:
        task = asyncio.get_running_loop().create_task(_run())
        _pending.add(task)
        task.add_done_callback(_pending.discard)
    except Exception:  # noqa: BLE001 — e.g. no running loop in a sync test
        logger.debug("user profile upsert could not be scheduled", exc_info=True)


async def drain_profile_upserts() -> None:
    """Await every in-flight profile upsert (idempotent; exceptions already
    handled inside the tasks).

    Called at shutdown BEFORE the user store closes: a fire-and-forget write
    that outlives the lifespan would otherwise run against a closed store — or
    worse, rebuild a fresh one through ``get_user_store()`` after
    ``reset_user_store()`` (an asyncpg pool nobody closes; sqlite DDL on the
    event loop)."""
    while _pending:
        await asyncio.gather(*list(_pending), return_exceptions=True)


async def _principal_from_bearer(provider: IdentityProvider, credential: str) -> Principal:
    """Verify ``credential`` with ``provider`` and build a Principal.

    The two failure modes are kept apart deliberately: an invalid credential is a
    401 (we decided, and the answer is no); an unreachable key server is a 503 (we
    could not decide). The unavailable case must never fall through to the API-key
    path or to any other allow — "we don't know" is not "come in".
    """
    try:
        identity = await provider.authenticate(credential)
    except IdentityInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired bearer credential",
        ) from exc
    except IdentityUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="identity provider unavailable",
        ) from exc
    if not identity.issuer or not identity.subject:
        # A provider that returns a blank subject would collapse every caller onto
        # the tenant ":" — refuse rather than merge users.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired bearer credential",
        )
    # The one place the tenant/authorization subject is spelled: built once here
    # and reused for the profile upsert, the role lookup and the Principal, so
    # the three can never disagree about who this caller is.
    subject = f"{identity.issuer}:{identity.subject}"
    # First-auth profile upsert (ADR-0004): scheduled, never awaited — the
    # request continues regardless of what the user store does. Debounced per
    # subject (``_should_upsert``): this hook runs on every bearer request
    # (the provider's cache caches *verification*, not this call), so the
    # debounce — not the cache — is what keeps the users table from becoming a
    # per-request write. The store's upsert semantics stay idempotent anyway.
    _schedule_profile_upsert(identity, subject)
    # Resolved from an explicit server-side source only (env allowlist first,
    # then the users row) and never from settings.default_role — prod sets
    # DEFAULT_ROLE=admin. ROLE_USER is the literal fallback on every other path,
    # including every failure inside the lookup.
    role = await _bearer_role(subject)
    return Principal(
        tenant=subject,
        role=role,
        token=credential,
        token_id=identity.token_id,
        token_exp=identity.expires_at,
    )


async def _authenticate(api_key: str | None, authorization: str | None) -> Principal:
    """Resolve the request's Principal from whichever credential it presented."""
    provider = get_identity_provider()
    if provider is None:
        # Identity layer off: `Authorization` is not an authentication input, so
        # it is neither honoured nor a conflict. Behaviour is byte-for-byte what
        # it was before this layer existed — except that a key whose tenant is a
        # registered-and-disabled service account is refused (#258).
        return await _principal_from_key_checked(api_key)
    credential = _bearer_credential(authorization)
    if credential and api_key is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "present exactly one credential: X-API-Key or Authorization, not both"
            ),
        )
    if not credential:
        return await _principal_from_key_checked(api_key)
    # Bearer is untouched by #258: a federated identity is revoked at its issuer
    # (or by its expiry), never by a service-account row.
    return await _principal_from_bearer(provider, credential)


async def resolve_principal(
    request: Request,
    api_key: str | None = Security(_api_key_header),
    authorization: str | None = Security(_authorization_header),
) -> Principal:
    """FastAPI dependency: the authenticated :class:`Principal` (tenant + role).

    Memoized on ``request.state`` so a route that depends on both this and
    :func:`resolve_tenant` verifies the credential once — with a bearer credential
    that would otherwise be a second signature check (or a second network call).
    """
    cached = getattr(request.state, "principal", None)
    if cached is not None:
        return cached
    principal = await _authenticate(api_key, authorization)
    request.state.principal = principal
    return principal


def validate_role_settings() -> None:
    """Fail fast on a misconfigured RBAC setup (call at startup).

    An unknown ``default_role``, or a key mapped to an unknown role, would
    otherwise silently 403 every affected caller — a hard-to-debug runtime
    failure. Raise a clear error instead. Never logs the keys themselves, only
    the offending role values.

    The deprecated ``researcher`` alias passes (normalized to ``user`` with a
    warning wherever roles are read); the removed ``engineer``/``manager``
    roles are rejected with a pointer at ADR-0003 — they no longer map to any
    surface, so a config granting them would silently 403 those callers.

    It also checks the ``api_key_tenants`` VALUES, because a tenant string is an
    authorization subject: blank/whitespace-padded values are rejected outright,
    and a colon-bearing one is rejected whenever an identity provider is on.
    """

    def _check(role: str, where: str) -> None:
        if role in _REMOVED_ROLES:
            raise RuntimeError(
                f"{where} uses role {role!r}, which was removed: the role "
                "vocabulary is now admin | user (see docs/adr/0003-access-control.md)"
            )
        if normalize_role(role) not in VALID_ROLES:
            raise RuntimeError(
                f"{where}={role!r} is not a valid role; "
                f"valid roles are {sorted(VALID_ROLES)}"
            )

    _check(settings.default_role, "default_role")
    for role in set(settings.api_key_roles.values()):
        _check(role, "api_key_roles")

    # A tenant value is used VERBATIM on the auth path (``api_key_tenants.get``)
    # but every admin surface that names a subject normalizes it
    # (``service_accounts._clean_subject`` strips, share/group grantees strip).
    # So a value with leading/trailing whitespace is a subject nothing can name:
    # disabling "loader " through the API 204s against the stored "loader" while
    # the padded key keeps authenticating, and the account lists as inactive with
    # a live credential. Reject the shape at startup instead of silently
    # normalizing here, which would change which tenant an existing deployment's
    # data belongs to.
    for key_tenant in set(settings.api_key_tenants.values()):
        if key_tenant != key_tenant.strip() or not key_tenant:
            raise RuntimeError(
                f"api_key_tenants maps a key to tenant {key_tenant!r}, which is "
                "blank or has leading/trailing whitespace; the tenant string is "
                "the authorization subject and every admin surface names it "
                "stripped, so this one could never be shared with, added to a "
                "group, or revoked"
            )

    # An API-key tenant string IS the authz subject (authz.resolve_access). When a
    # bearer identity provider is also on, a bearer subject is "{issuer}:{sub}";
    # an API-key tenant literally shaped like "google:alice" would collide with
    # that bearer user's ownership identity. Reject the shape at startup rather
    # than let a misconfiguration hand one caller another's collections.
    if settings.identity_provider not in ("", "none"):
        for tenant in set(settings.api_key_tenants.values()):
            if ":" in tenant:
                raise RuntimeError(
                    f"api_key_tenants maps a key to tenant {tenant!r}, whose "
                    "':' collides with a bearer subject 'issuer:sub' while an "
                    "identity provider is enabled; use a colon-free tenant name"
                )


def validate_rate_limit_settings() -> None:
    """Warn (never fail) at startup when a configured rate limit (issue #87)
    would be a no-op for keyless/unmapped callers.

    ``api/deps.py::rate_limited`` exempts an ``admin`` principal from the
    bucket (logged, per request). ``default_role=admin`` means EVERY keyless
    caller — and every API key not explicitly mapped in ``api_key_roles`` — IS
    that admin principal, so a deployment shaped this way gets the exemption
    for free on every request the limiter would otherwise gate. That may be
    intentional (a single-operator/dev deployment), so this only warns; it is
    not the startup-time RBAC misconfiguration ``validate_role_settings``
    guards against, which is why it is a separate, non-raising check.
    """
    if settings.default_role != ROLE_ADMIN:
        return
    configured = {
        "rate_limit_ingest_per_hour": settings.rate_limit_ingest_per_hour,
        "rate_limit_collections_create_per_hour": (
            settings.rate_limit_collections_create_per_hour
        ),
        "rate_limit_shares_per_hour": settings.rate_limit_shares_per_hour,
    }
    active = {name: value for name, value in configured.items() if value > 0}
    if not active:
        return
    logger.warning(
        "default_role=admin with %s configured: every keyless or "
        "unmapped-key caller on this deployment IS an admin principal, and "
        "an admin is exempt from the rate-limit bucket (issue #87) — so the "
        "limiter is a no-op for them. If the intent is for the rate limit to "
        "actually apply, set DEFAULT_ROLE=user and/or map callers to a "
        "non-admin role via API_KEY_ROLES.",
        ", ".join(f"{name}={value}" for name, value in active.items()),
    )


#: Upper bound on an ``ADMIN_SUBJECTS`` entry, matching the service-account
#: router's ``_SUBJECT_MAX``: the value is compared against a users primary key
#: and lands in log lines, so a paste accident must fail startup, not be stored.
_ADMIN_SUBJECT_MAX = 128


def validate_admin_subjects_settings() -> None:
    """Fail fast on an ``ADMIN_SUBJECTS`` list that cannot mean what it says.

    Call at startup, next to :func:`validate_role_settings` — same vocabulary,
    same namespace rules, and it must fail before any request is served: this
    list is the break-glass admin path, so an entry that silently never matches
    is an operator who believes they have a way in and does not.

    The rules are :func:`validate_role_settings`'s ``api_key_tenants`` VALUE
    checks, INVERTED on the colon:

    * Non-empty and equal to its own ``strip()``. The auth path matches the
      entry verbatim while every admin surface normalizes stripped, so a padded
      entry would name a subject nothing can revoke or even display.
    * Contains ``':'`` with BOTH halves non-empty. Entries here are BEARER
      subjects (``issuer:subject``) — the exact inverse of the colon-free
      service-account rule. A colon-free entry names an API-key tenant, and
      honouring it would make a machine credential admin through the bearer
      door, dissolving the disjoint-namespace invariant the #243 startup guard,
      ``_check_service_account`` and ``_clean_subject`` all defend. Use
      ``API_KEY_ROLES`` for those, and the message says so.
    * Not one of the reserved shared tenants, and no control characters (the
      value is echoed into logs, where a raw ESC is an output-context
      injection), and length-capped.

    Two conditions are only WARNED about, following ``_validate_ingest_root``'s
    precedent of announcing a silently-disabled capability at boot rather than
    refusing to start: a non-empty allowlist while no identity provider is
    enabled (nothing can ever present a bearer credential, so the list is
    inert), and an issuer half matching neither ``bvbrc`` nor
    ``identity_oidc_issuer_label`` (no token this deployment accepts will ever
    produce that subject).
    """
    from ragstack.user_store import RESERVED_SERVICE_SUBJECTS

    entries = list(settings.admin_subjects or ())
    for entry in entries:
        if not entry or entry != entry.strip():
            raise RuntimeError(
                f"admin_subjects contains {entry!r}, which is blank or has "
                "leading/trailing whitespace; the bearer auth path matches this "
                "value verbatim while every admin surface names subjects "
                "stripped, so it could never be revoked, listed or displayed"
            )
        if len(entry) > _ADMIN_SUBJECT_MAX:
            raise RuntimeError(
                "admin_subjects contains an entry of "
                f"{len(entry)} characters; the cap is {_ADMIN_SUBJECT_MAX} (a "
                "subject is a users primary key, not a document)"
            )
        if any(ord(c) < 0x20 or ord(c) == 0x7F for c in entry):
            raise RuntimeError(
                f"admin_subjects contains {entry!r}, which has control "
                "characters; the value is echoed into logs and terminals"
            )
        issuer, _, sub = entry.partition(":")
        if not issuer or not sub:
            raise RuntimeError(
                f"admin_subjects contains {entry!r}, which is not a bearer "
                "subject: entries must be 'issuer:subject' with BOTH halves "
                "non-empty (e.g. 'bvbrc:alice'). A colon-free value names an "
                "API-key TENANT — granting it admin through the bearer "
                "allowlist would make a machine credential a superuser and "
                "collapse the two disjoint subject namespaces; use "
                "API_KEY_ROLES for an API-key principal's role"
            )
        if entry in RESERVED_SERVICE_SUBJECTS:
            # Only the WHOLE entry is checked. The colon rule above already
            # guarantees a federated subject cannot collide with a (colon-free)
            # reserved tenant, so also refusing a *subject half* of 'default' or
            # 'public' — as this once did — blocked a legitimate identity whose
            # OIDC `sub` happens to be that word, and explained it with a
            # collision that cannot occur. Kept for the entry itself because the
            # colon check makes that branch unreachable, and a guard that
            # documents the invariant costs nothing.
            raise RuntimeError(
                f"admin_subjects contains {entry!r}, which names a reserved "
                f"tenant ({sorted(RESERVED_SERVICE_SUBJECTS)}); those are shared "
                "fallback/public corpora, not one caller's identity"
            )

    if not entries:
        return
    if _configured_provider() in ("", "none"):
        logger.warning(
            "ADMIN_SUBJECTS names %d subject(s) but IDENTITY_PROVIDER is %r: no "
            "caller can present a bearer credential, so the allowlist is inert "
            "and this deployment has NO bearer admin",
            len(entries),
            settings.identity_provider,
        )
    else:
        # Same rule as usable_admin_subjects(), which is what the last-admin
        # guard asks: an entry warned about here is one that guard must NOT
        # count. Only reached when a provider IS configured — otherwise every
        # entry is unknown and this would just restate the warning above.
        known = _known_issuer_labels()
        # Same prefix rule as usable_admin_subjects: an entry is "unknown" only
        # when no configured label is a proper `label:` prefix of it.
        unknown = sorted(
            {
                e.partition(":")[0]
                for e in entries
                if not any(e.startswith(f"{k}:") for k in known)
            }
        )
        if unknown:
            logger.warning(
                "ADMIN_SUBJECTS uses issuer prefix(es) %s, but IDENTITY_PROVIDER "
                "is %r, whose tokens carry issuer %s: no token this deployment "
                "accepts produces such a subject, so those entries can never "
                "match and are not counted as a way back by the last-admin guard",
                unknown,
                settings.identity_provider,
                sorted(known) or "(none)",
            )
    # The COUNT only — never the values. The list names real people, and it is
    # exactly the list an attacker reading logs would want.
    logger.info("ADMIN_SUBJECTS: %d bearer subject(s) allowlisted as admin", len(entries))


#: Hard ceiling on ``admin_role_cache_ttl_seconds``. The same cap and the same
#: reasoning as ``_DISABLED_CACHE_TTL_MAX``, pointing the other way: this TTL is
#: the DEMOTION lag, so an operator raising it to cut database load would
#: silently keep a REVOKED admin a superuser for that long.
_ROLE_CACHE_TTL_MAX = 300


def validate_admin_role_cache_settings() -> None:
    """Fail fast on a role cache whose TTL outlives a demotion (call at
    startup). A hard failure rather than a clamp, for the same reason as
    :func:`validate_service_account_settings`: honouring a different TTL than
    the configured one is how an operator ends up believing a revoke landed."""
    ttl = settings.admin_role_cache_ttl_seconds
    if ttl > _ROLE_CACHE_TTL_MAX:
        raise RuntimeError(
            f"admin_role_cache_ttl_seconds={ttl} exceeds the "
            f"{_ROLE_CACHE_TTL_MAX}s cap: this TTL is how long a REVOKED admin "
            "keeps superuser rights on the bearer path (per process), so a "
            "larger value silently defers every demotion — the same cap "
            "identity_cache_ttl_seconds and the disabled-check TTL get"
        )


#: Hard ceiling on ``service_account_disabled_cache_ttl_seconds``, mirroring the
#: identity layer's cap on ``identity_cache_ttl_seconds``
#: (``identity/factory.py``) for exactly the same reason: THE CACHE TTL IS THE
#: REVOCATION LAG. Disabling an account is the only runtime revocation lever
#: there is, and an operator raising this to cut ACL-database load would
#: otherwise silently buy a revocation window measured in hours or days, with no
#: warning at any log level. Five minutes is the same bound the identity cache
#: gets.
_DISABLED_CACHE_TTL_MAX = 300


def validate_service_account_settings() -> None:
    """Fail fast on a service-account revocation setup that cannot revoke.

    Call at startup, next to :func:`validate_role_settings`. The TTL is clamped
    at read time for the negative end (``_disabled_cache_ttl``); this is the
    other end, and it is a hard failure rather than a clamp because silently
    honouring a *different* TTL than the one configured is how an operator ends
    up believing revocation is faster than it is.
    """
    ttl = settings.service_account_disabled_cache_ttl_seconds
    if ttl > _DISABLED_CACHE_TTL_MAX:
        raise RuntimeError(
            f"service_account_disabled_cache_ttl_seconds={ttl} exceeds the "
            f"{_DISABLED_CACHE_TTL_MAX}s cap: this TTL is the revocation lag for "
            "disabled service accounts (the only revoke that does not need a "
            "restart), so a larger value silently keeps a leaked key working for "
            "that long — the same cap identity_cache_ttl_seconds gets"
        )


async def resolve_tenant(
    request: Request,
    api_key: str | None = Security(_api_key_header),
    authorization: str | None = Security(_authorization_header),
) -> str:
    """Authenticate the request and return its tenant_id.

    Enforces API-key auth when keys are configured (constant-time compare); with
    no keys configured the API is open and every caller is the ``default`` tenant
    (dev/tests — production's startup check forbids the keyless path). When the
    identity layer is on, a bearer credential resolves instead to
    ``f"{issuer}:{subject}"``. The tenant is derived here, server-side, so it can
    never be spoofed via the request body. Used both as a router-level dependency
    (enforcement) and a handler parameter (the resolved tenant); FastAPI caches it
    per request, so it runs once.
    """
    return (await resolve_principal(request, api_key, authorization)).tenant


def require_role(
    *roles: str,
) -> Callable[[Principal], Coroutine[Any, Any, Principal]]:
    """Build a dependency that authorizes only the given ``roles`` (``admin`` is a
    superuser and always passes). Returns the :class:`Principal` on success, 403s
    otherwise. Use at router-include level to gate a whole surface, or per route.

    The role vocabulary is ``admin`` | ``user`` (ADR-0003); the deprecated
    ``researcher`` alias is normalized to ``user`` here just as it is where
    roles are read, so an old call site keeps gating the same callers.

    Validates ``roles`` against :data:`VALID_ROLES` at build time, so a typo like
    ``require_role("admn")`` — or one of the removed ``engineer``/``manager``
    roles — fails loudly at import, not as a silent, permanent 403 at runtime.
    """
    allowed = {normalize_role(r) for r in roles}
    unknown = allowed - VALID_ROLES
    if unknown:
        raise ValueError(
            f"require_role got unknown role(s) {sorted(unknown)}; "
            f"valid roles are {sorted(VALID_ROLES)}"
        )

    async def _dependency(
        principal: Principal = Depends(resolve_principal),
    ) -> Principal:
        if principal.role == ROLE_ADMIN or principal.role in allowed:
            return principal
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="insufficient role for this resource",
        )

    return _dependency
