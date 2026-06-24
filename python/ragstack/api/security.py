"""API-key authentication for the v1 routers."""
from __future__ import annotations

import secrets

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from ragstack.config import settings

API_KEY_HEADER = "X-API-Key"
_api_key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)


def require_api_key(api_key: str | None = Security(_api_key_header)) -> None:
    """Enforce API-key auth when keys are configured.

    With no keys configured the API is open (dev/tests). In production the
    startup check (see deps._validate_production_settings) requires keys to be
    set, so the open path is never reachable there. Key comparison is
    constant-time to avoid leaking validity through timing.
    """
    keys = settings.api_keys
    if not keys:
        return
    if api_key is not None and any(secrets.compare_digest(api_key, k) for k in keys):
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="missing or invalid API key",
    )
