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
  ``engineer`` | ``manager`` | ``researcher``. ``require_role`` gates an endpoint;
  a UI role-gate is UX only — authorization is always re-checked here. A bearer
  identity always gets the explicit ``ROLE_RESEARCHER`` and **never**
  ``default_role``: prod runs ``DEFAULT_ROLE=admin``, so falling through would
  make every authenticated end user a superuser.

Presenting both credentials is a **400**, not a silent precedence rule: which one
authenticated you would otherwise be invisible in the logs and in the tenant.
"""
from __future__ import annotations

import secrets
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from ragstack.config import settings
from ragstack.identity import (
    IdentityInvalid,
    IdentityProvider,
    IdentityUnavailable,
    get_identity_provider,
)
from ragstack.tenancy import DEFAULT_TENANT

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
ROLE_ENGINEER = "engineer"
ROLE_MANAGER = "manager"
ROLE_RESEARCHER = "researcher"
VALID_ROLES = frozenset({ROLE_ADMIN, ROLE_ENGINEER, ROLE_MANAGER, ROLE_RESEARCHER})


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
        return Principal(tenant=DEFAULT_TENANT, role=settings.default_role)
    # sum() over the generator evaluates every compare_digest (no short-circuit),
    # so total time doesn't reveal which key matched or how far down the list.
    if api_key is not None and sum(secrets.compare_digest(api_key, k) for k in keys) > 0:
        return Principal(
            tenant=settings.api_key_tenants.get(api_key, DEFAULT_TENANT),
            role=settings.api_key_roles.get(api_key, settings.default_role),
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
    return Principal(
        tenant=f"{identity.issuer}:{identity.subject}",
        # Explicit, NOT settings.default_role: prod sets DEFAULT_ROLE=admin.
        role=ROLE_RESEARCHER,
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
    """
    if settings.default_role not in VALID_ROLES:
        raise RuntimeError(
            f"default_role={settings.default_role!r} is not a valid role; "
            f"valid roles are {sorted(VALID_ROLES)}"
        )
    bad_roles = {r for r in settings.api_key_roles.values() if r not in VALID_ROLES}
    if bad_roles:
        raise RuntimeError(
            f"api_key_roles maps key(s) to invalid role(s) {sorted(bad_roles)}; "
            f"valid roles are {sorted(VALID_ROLES)}"
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

    Validates ``roles`` against :data:`VALID_ROLES` at build time, so a typo like
    ``require_role("admn")`` fails loudly at import — not as a silent, permanent
    403 at runtime.
    """
    unknown = set(roles) - VALID_ROLES
    if unknown:
        raise ValueError(
            f"require_role got unknown role(s) {sorted(unknown)}; "
            f"valid roles are {sorted(VALID_ROLES)}"
        )
    allowed = set(roles)

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
