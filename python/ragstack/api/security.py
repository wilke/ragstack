"""API-key authentication for the v1 routers."""
from __future__ import annotations

import secrets

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from ragstack.config import settings
from ragstack.tenancy import DEFAULT_TENANT

API_KEY_HEADER = "X-API-Key"
_api_key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)


def resolve_tenant(api_key: str | None = Security(_api_key_header)) -> str:
    """Authenticate the request and return its tenant_id.

    Enforces API-key auth when keys are configured (constant-time compare); with
    no keys configured the API is open and every caller is the ``default`` tenant
    (dev/tests — production's startup check forbids the keyless path). The tenant
    is derived here, server-side, so it can never be spoofed via the request body.
    Used both as a router-level dependency (enforcement) and a handler parameter
    (the resolved tenant); FastAPI caches it per request, so it runs once.
    """
    keys = settings.api_keys
    if not keys:
        return DEFAULT_TENANT
    # sum() over the generator evaluates every compare_digest (no short-circuit),
    # so total time doesn't reveal which key matched or how far down the list.
    if api_key is not None and sum(secrets.compare_digest(api_key, k) for k in keys) > 0:
        return settings.api_key_tenants.get(api_key, DEFAULT_TENANT)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="missing or invalid API key",
    )
