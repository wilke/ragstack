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
  identity always gets the explicit ``ROLE_USER`` and **never**
  ``default_role``: prod runs ``DEFAULT_ROLE=admin``, so falling through would
  make every authenticated end user a superuser.

A verified bearer authentication also schedules a fire-and-forget profile
upsert (ADR-0004 decision 1) — ``users(subject, …)`` keyed on the tenant
string. It must never fail, slow, or 500 the auth path, so the task is wrapped
and every exception is logged at debug. API-key principals get no user row: a
key is a deployment credential, not a person.

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


def _clean_claim(value: str, limit: int = _PROFILE_CLAIM_MAX) -> str:
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
        email=_clean_claim(identity.email) if identity.email_verified else "",
        display_name=_clean_claim(identity.display_name),
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


def _schedule_profile_upsert(identity: Identity) -> None:
    """Fire-and-forget the profile upsert, debounced per subject.

    Authentication must never fail, slow, or 500 because the profile write did —
    ANY exception (scheduling or execution) is swallowed. The first failure is
    logged at WARNING (an operator must be able to see a dead user store at the
    default log level); repeats drop to debug."""
    if not _should_upsert(f"{identity.issuer}:{identity.subject}"):
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
    # First-auth profile upsert (ADR-0004): scheduled, never awaited — the
    # request continues regardless of what the user store does. Debounced per
    # subject (``_should_upsert``): this hook runs on every bearer request
    # (the provider's cache caches *verification*, not this call), so the
    # debounce — not the cache — is what keeps the users table from becoming a
    # per-request write. The store's upsert semantics stay idempotent anyway.
    _schedule_profile_upsert(identity)
    return Principal(
        tenant=f"{identity.issuer}:{identity.subject}",
        # Explicit, NOT settings.default_role: prod sets DEFAULT_ROLE=admin.
        role=ROLE_USER,
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
        # it was before this layer existed.
        return _principal_from_key(api_key)
    credential = _bearer_credential(authorization)
    if credential and api_key is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "present exactly one credential: X-API-Key or Authorization, not both"
            ),
        )
    if not credential:
        return _principal_from_key(api_key)
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
