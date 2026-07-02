"""API-key authentication + RBAC for the v1 routers.

Two layers, both resolved server-side from the ``X-API-Key`` header (never from
the request body, so neither can be spoofed):

- **Tenant** (data isolation) — which corpus the caller reads/writes. Existing.
- **Role** (authz) — what surface the caller may use: ``admin`` (superuser) |
  ``engineer`` | ``manager`` | ``researcher``. New, for the dashboard/admin
  surface. ``require_role`` gates an endpoint; a UI role-gate is UX only —
  authorization is always re-checked here.
"""
from __future__ import annotations

import secrets
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from ragstack.config import settings
from ragstack.tenancy import DEFAULT_TENANT

API_KEY_HEADER = "X-API-Key"
_api_key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)

# RBAC roles. ``admin`` is a superuser: it satisfies every ``require_role`` check.
ROLE_ADMIN = "admin"
ROLE_ENGINEER = "engineer"
ROLE_MANAGER = "manager"
ROLE_RESEARCHER = "researcher"
VALID_ROLES = frozenset({ROLE_ADMIN, ROLE_ENGINEER, ROLE_MANAGER, ROLE_RESEARCHER})


@dataclass(frozen=True)
class Principal:
    """The authenticated caller: its data tenant and its RBAC role."""

    tenant: str
    role: str


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


def resolve_principal(api_key: str | None = Security(_api_key_header)) -> Principal:
    """FastAPI dependency: the authenticated :class:`Principal` (tenant + role)."""
    return _principal_from_key(api_key)


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


def resolve_tenant(api_key: str | None = Security(_api_key_header)) -> str:
    """Authenticate the request and return its tenant_id.

    Enforces API-key auth when keys are configured (constant-time compare); with
    no keys configured the API is open and every caller is the ``default`` tenant
    (dev/tests — production's startup check forbids the keyless path). The tenant
    is derived here, server-side, so it can never be spoofed via the request body.
    Used both as a router-level dependency (enforcement) and a handler parameter
    (the resolved tenant); FastAPI caches it per request, so it runs once.
    """
    return _principal_from_key(api_key).tenant


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
