"""RBAC (Principal + role) and the admin /v1/config allowlist.

Authorization is enforced server-side: a researcher (or keyless dev) is refused
the admin surface even though tenant auth succeeds, and /v1/config never
serializes a secret regardless of how settings grow.
"""
import re

import pytest

from ragstack.api import security
from ragstack.api.security import (
    ROLE_ADMIN,
    ROLE_RESEARCHER,
    Principal,
    _principal_from_key,
    require_role,
    validate_role_settings,
)

# Fields the config endpoint must NEVER expose (names + a substring guard).
_SECRET_FIELDS = {
    "api_keys", "api_key_tenants", "api_key_roles", "qdrant_api_key",
    "elasticsearch_api_key", "neo4j_password", "postgres_dsn", "openai_api_key",
    "redis_url",
}
# Credential-like field names (precise, so it doesn't false-match the safe
# operational `chunk_token_counter`).
_SECRET_RE = re.compile(r"(password|passwd|secret|_dsn|dsn$|api_?key|_key$|credential|bearer)", re.I)


# --------------------------------------------------------------------------- #
# Principal resolution (unit)
# --------------------------------------------------------------------------- #

def test_keyless_is_default_tenant_and_default_role(monkeypatch):
    monkeypatch.setattr(security.settings, "api_keys", [])
    monkeypatch.setattr(security.settings, "default_role", ROLE_RESEARCHER)
    p = _principal_from_key(None)
    assert p == Principal(tenant="default", role=ROLE_RESEARCHER)


def test_mapped_key_resolves_tenant_and_role(monkeypatch):
    monkeypatch.setattr(security.settings, "api_keys", ["adm", "res"])
    monkeypatch.setattr(security.settings, "api_key_tenants", {"adm": "acme"})
    monkeypatch.setattr(security.settings, "api_key_roles", {"adm": ROLE_ADMIN})
    monkeypatch.setattr(security.settings, "default_role", ROLE_RESEARCHER)
    assert _principal_from_key("adm") == Principal(tenant="acme", role=ROLE_ADMIN)
    # A valid-but-unmapped key gets the default tenant + default role.
    assert _principal_from_key("res") == Principal(tenant="default", role=ROLE_RESEARCHER)


def test_bad_key_raises_401(monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(security.settings, "api_keys", ["adm"])
    with pytest.raises(HTTPException) as exc:
        _principal_from_key("nope")
    assert exc.value.status_code == 401


# --------------------------------------------------------------------------- #
# require_role over the admin surface (end-to-end)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_config_keyless_is_forbidden_not_admin(client, monkeypatch):
    # Keyless dev resolves to the default role (researcher), which is NOT admin —
    # the admin surface stays closed by default (least privilege).
    monkeypatch.setattr(security.settings, "api_keys", [])
    monkeypatch.setattr(security.settings, "default_role", ROLE_RESEARCHER)
    assert (await client.get("/v1/config")).status_code == 403


@pytest.mark.asyncio
async def test_config_requires_admin_role(client, monkeypatch):
    monkeypatch.setattr(security.settings, "api_keys", ["adm", "res"])
    monkeypatch.setattr(security.settings, "api_key_roles", {"adm": ROLE_ADMIN})
    monkeypatch.setattr(security.settings, "default_role", ROLE_RESEARCHER)

    # No key -> 401 (auth), valid non-admin -> 403 (authz), admin -> 200.
    assert (await client.get("/v1/config")).status_code == 401
    assert (await client.get("/v1/config", headers={"X-API-Key": "res"})).status_code == 403
    ok = await client.get("/v1/config", headers={"X-API-Key": "adm"})
    assert ok.status_code == 200


@pytest.mark.asyncio
async def test_config_response_is_allowlisted_and_leaks_no_secret(client, monkeypatch):
    monkeypatch.setattr(security.settings, "api_keys", ["adm"])
    monkeypatch.setattr(security.settings, "api_key_roles", {"adm": ROLE_ADMIN})
    # Set a real secret to prove it never appears in the response.
    monkeypatch.setattr(security.settings, "neo4j_password", "SUPER-SECRET")
    monkeypatch.setattr(security.settings, "api_key_tenants", {"adm": "acme"})

    body = (await client.get("/v1/config", headers={"X-API-Key": "adm"})).json()
    keys = set(body)
    assert keys.isdisjoint(_SECRET_FIELDS), f"secret field leaked: {keys & _SECRET_FIELDS}"
    assert not any(_SECRET_RE.search(k) for k in keys), f"secret-looking key: {keys}"
    assert "SUPER-SECRET" not in str(body)
    # ...but it DOES carry the useful operational config.
    assert body["vector_backend"] and "embedding_model" in body and "chunk_method" in body


@pytest.mark.asyncio
async def test_admin_key_reaches_all_lower_surfaces(client, monkeypatch):
    """admin is a superuser: it also satisfies routes that need lesser roles
    (here, tenant-only endpoints still work)."""
    monkeypatch.setattr(security.settings, "api_keys", ["adm"])
    monkeypatch.setattr(security.settings, "api_key_roles", {"adm": ROLE_ADMIN})
    assert (await client.get("/v1/documents", headers={"X-API-Key": "adm"})).status_code == 200


# --------------------------------------------------------------------------- #
# Fail-fast on misconfiguration (Copilot review of PR #81)
# --------------------------------------------------------------------------- #

def test_require_role_rejects_unknown_role_at_build_time():
    # A typo'd role must blow up when the dependency is built (import time), not
    # silently 403 forever at runtime.
    with pytest.raises(ValueError, match="unknown role"):
        require_role("admn")
    # Valid roles still build fine.
    assert require_role(ROLE_ADMIN) is not None


def test_validate_role_settings_rejects_bad_default_role(monkeypatch):
    monkeypatch.setattr(security.settings, "default_role", "supervisor")
    monkeypatch.setattr(security.settings, "api_key_roles", {})
    with pytest.raises(RuntimeError, match="default_role"):
        validate_role_settings()


def test_validate_role_settings_rejects_bad_mapped_role_without_leaking_key(monkeypatch):
    monkeypatch.setattr(security.settings, "default_role", ROLE_RESEARCHER)
    monkeypatch.setattr(security.settings, "api_key_roles", {"SECRET-KEY": "wizard"})
    with pytest.raises(RuntimeError) as exc:
        validate_role_settings()
    assert "wizard" in str(exc.value)
    assert "SECRET-KEY" not in str(exc.value)  # the key itself is never surfaced


def test_validate_role_settings_passes_on_valid_config(monkeypatch):
    monkeypatch.setattr(security.settings, "default_role", ROLE_RESEARCHER)
    monkeypatch.setattr(security.settings, "api_key_roles", {"k": ROLE_ADMIN})
    validate_role_settings()  # no raise


@pytest.mark.asyncio
async def test_config_redacts_url_credentials(client, monkeypatch):
    monkeypatch.setattr(security.settings, "api_keys", ["adm"])
    monkeypatch.setattr(security.settings, "api_key_roles", {"adm": ROLE_ADMIN})
    # A connection string with inline userinfo must not leak the password.
    monkeypatch.setattr(security.settings, "neo4j_uri", "bolt://neo4j:SECRET-PW@graph:7687")
    monkeypatch.setattr(
        security.settings, "embedding_endpoints", ["http://user:PWD@embed:8000"]
    )

    body = (await client.get("/v1/config", headers={"X-API-Key": "adm"})).json()
    assert "SECRET-PW" not in str(body) and "PWD" not in str(body)
    assert body["neo4j_uri"] == "bolt://graph:7687"  # userinfo stripped, host kept
    assert body["embedding_endpoints"] == ["http://embed:8000"]
