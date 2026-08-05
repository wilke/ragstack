"""Wiring: `Authorization: Bearer <credential>` through the FastAPI dependency.

The identity layer is behind a flag. With ``IDENTITY_PROVIDER=none`` (the
default) the header is not an authentication input at all and every existing
deployment behaves exactly as before — the last test in this file is what pins
that down.
"""
from __future__ import annotations

import time

import pytest

from ragstack.api import security
from ragstack.identity import (
    Identity,
    IdentityInvalid,
    IdentityUnavailable,
    reset_identity_provider,
    set_identity_provider,
)


class FakeProvider:
    """Authenticates exactly one credential; anything else is a 401 (or, when
    ``error`` is set, whatever failure the test is exercising)."""

    def __init__(self, *, good: str = "good-token", error: Exception | None = None):
        self.good = good
        self.error = error
        self.seen: list[str] = []

    async def authenticate(self, credential: str) -> Identity:
        self.seen.append(credential)
        if self.error is not None:
            raise self.error
        if credential != self.good:
            raise IdentityInvalid("no")
        return Identity(
            subject="alice@patricbrc.org",
            issuer="bvbrc",
            token_id="tok-1",
            expires_at=int(time.time()) + 3600,
        )


@pytest.fixture
def identity(monkeypatch):
    """Turn the identity layer on with a fake provider, and off again after."""
    monkeypatch.setattr(security.settings, "identity_provider", "bvbrc")
    provider = FakeProvider()
    set_identity_provider(provider)
    yield provider
    reset_identity_provider()


async def test_bearer_credential_authenticates_as_an_issuer_scoped_tenant(client, identity):
    resp = await client.get(
        "/v1/stats/tenants", headers={"Authorization": "Bearer good-token"}
    )
    assert resp.status_code == 200
    body = resp.json()
    # Namespaced, so a BV-BRC alice is not a Google alice — and neither collides
    # with the reserved `public` / `default` tenants.
    assert body["tenant"] == "bvbrc:alice@patricbrc.org"
    assert body["role"] == "user"


async def test_bearer_prefix_is_optional(client, identity):
    # The BV-BRC wire format carries no scheme, so a raw token must work too.
    resp = await client.get("/v1/stats/tenants", headers={"Authorization": "good-token"})
    assert resp.status_code == 200
    assert resp.json()["tenant"] == "bvbrc:alice@patricbrc.org"


async def test_role_never_falls_through_to_default_role(client, identity, monkeypatch):
    """The demo box runs DEFAULT_ROLE=admin. If the bearer path inherited it,
    every authenticated end user would be a superuser."""
    monkeypatch.setattr(security.settings, "default_role", "admin")

    tenants = await client.get(
        "/v1/stats/tenants", headers={"Authorization": "Bearer good-token"}
    )
    assert tenants.json()["role"] == "user"

    # And the admin surface is actually closed to them.
    admin = await client.get("/v1/config", headers={"Authorization": "Bearer good-token"})
    assert admin.status_code == 403


async def test_presenting_both_credentials_is_400(client, identity, monkeypatch):
    monkeypatch.setattr(security.settings, "api_keys", ["s3cret"])
    resp = await client.get(
        "/v1/stats/tenants",
        headers={"Authorization": "Bearer good-token", "X-API-Key": "s3cret"},
    )
    assert resp.status_code == 400
    # Not merely "the key won"; the ambiguity itself is refused, so which
    # credential authenticated a request is never a guess.
    assert identity.seen == []


async def test_both_credentials_is_400_even_when_the_key_is_wrong(client, identity):
    resp = await client.get(
        "/v1/stats/tenants",
        headers={"Authorization": "Bearer good-token", "X-API-Key": "wrong"},
    )
    assert resp.status_code == 400


async def test_invalid_credential_is_401(client, identity):
    resp = await client.get(
        "/v1/stats/tenants", headers={"Authorization": "Bearer forged"}
    )
    assert resp.status_code == 401


async def test_unavailable_provider_is_503_and_never_an_allow(client, identity):
    """"We could not decide" must not become "come in" — and must not become 401
    either, which would blame the caller for our outage."""
    identity.error = IdentityUnavailable("key server down")

    for path in ("/v1/stats/tenants", "/v1/documents", "/v1/graph/entities"):
        resp = await client.get(path, headers={"Authorization": "Bearer good-token"})
        assert resp.status_code == 503, path


async def test_unavailable_provider_does_not_fall_back_to_the_keyless_path(
    client, identity
):
    # Keyless dev config (api_keys empty) is the open path; a failed identity
    # check must not silently land there as the `default` tenant.
    identity.error = IdentityUnavailable("key server down")
    resp = await client.get("/v1/documents", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 503


async def test_invalid_credential_does_not_fall_back_to_the_keyless_path(client, identity):
    resp = await client.get("/v1/documents", headers={"Authorization": "Bearer forged"})
    assert resp.status_code == 401


async def test_credential_is_verified_once_per_request(client, identity):
    # /v1/documents depends on resolve_tenant at router level and again in the
    # handler; without the per-request memo that is two signature checks.
    await client.get("/v1/documents", headers={"Authorization": "Bearer good-token"})
    assert identity.seen == ["good-token"]


async def test_api_key_path_is_unaffected_while_identity_is_enabled(
    client, identity, monkeypatch
):
    monkeypatch.setattr(security.settings, "api_keys", ["s3cret"])
    ok = await client.get("/v1/documents", headers={"X-API-Key": "s3cret"})
    assert ok.status_code == 200
    bad = await client.get("/v1/documents", headers={"X-API-Key": "nope"})
    assert bad.status_code == 401
    assert identity.seen == []  # the identity provider is not on the key path


async def test_authorization_header_is_inert_while_the_flag_is_off(client, monkeypatch):
    """The compatibility guarantee: with IDENTITY_PROVIDER=none, a request that
    carries an Authorization header behaves exactly as it did before this layer
    existed — including carrying one *alongside* an API key."""
    monkeypatch.setattr(security.settings, "identity_provider", "none")
    reset_identity_provider()
    monkeypatch.setattr(security.settings, "api_keys", ["s3cret"])

    ignored = await client.get(
        "/v1/documents", headers={"Authorization": "Bearer anything", "X-API-Key": "s3cret"}
    )
    assert ignored.status_code == 200  # not 400, not 401

    unauthenticated = await client.get(
        "/v1/documents", headers={"Authorization": "Bearer anything"}
    )
    assert unauthenticated.status_code == 401  # no API key → the pre-existing 401


def test_principal_repr_redacts_the_token():
    principal = security.Principal(
        tenant="bvbrc:alice", role="user", token="un=alice|sig=deadbeef",
        token_id="tok-1", token_exp=123,
    )
    rendered = repr(principal)
    assert "deadbeef" not in rendered
    assert "'***'" in rendered
    # The non-secret fields stay visible — redaction must not blind the logs.
    assert "bvbrc:alice" in rendered and "tok-1" in rendered
    assert principal.token == "un=alice|sig=deadbeef"  # still usable in-process


def test_principal_repr_of_a_keyed_caller_is_unchanged_in_substance():
    assert "token=None" in repr(security.Principal(tenant="default", role="admin"))


def test_both_security_schemes_survive_openapi_generation():
    """FastAPI names a security scheme after its class, so a second unnamed
    APIKeyHeader silently overwrites the first — leaving the generated document
    claiming the API-key scheme reads the `Authorization` header."""
    from ragstack.api.main import app

    schemes = app.openapi()["components"]["securitySchemes"]
    by_header = {v["name"] for v in schemes.values() if v.get("in") == "header"}
    assert by_header == {"X-API-Key", "Authorization"}
