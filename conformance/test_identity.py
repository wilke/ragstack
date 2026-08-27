"""Conformance: bearer-credential identity (`Authorization`) — spec §5.0.

Black-box over HTTP; no imports from the implementations. Google is the
integration target because it is the second implementation of the same
``IdentityProvider`` interface — the proof that the border between "who are you?"
and "may you read this?" is real and not BV-BRC-shaped.

Env keys, exported by ``conformance/run_identity_google.sh``:

* ``RAGSTACK_IDENTITY_ENABLED`` — set when the server under test has an identity
  provider configured. Everything here skips without it, so ordinary
  ``make test-conformance`` runs against a default (``IDENTITY_PROVIDER=none``)
  server are unaffected.
* ``RAGSTACK_IDENTITY_ISSUER_LABEL`` — the ``Identity.issuer`` label the server is
  configured with (``google``), i.e. the tenant prefix to expect.
* ``RAGSTACK_GOOGLE_ID_TOKEN`` — OPTIONAL. A real Google ID token minted for the
  server's configured client id. Present ⇒ the positive path is asserted too.

Note what is *not* here: no test reads ``~/.patric_token`` and no test sends a
credential anywhere but the server under test. The negative assertions need no
real credential at all, which is why they run on every configured server.
"""
from __future__ import annotations

import os

import httpx
import pytest

from conftest import skip_no_credential

pytestmark = pytest.mark.asyncio

IDENTITY_ENABLED = bool(os.environ.get("RAGSTACK_IDENTITY_ENABLED"))
ISSUER_LABEL = os.environ.get("RAGSTACK_IDENTITY_ISSUER_LABEL", "google")
ID_TOKEN = os.environ.get("RAGSTACK_GOOGLE_ID_TOKEN") or None
API_KEY = os.environ.get("RAGSTACK_API_KEY") or None

requires_identity = pytest.mark.skipif(
    not IDENTITY_ENABLED,
    reason="server has no identity provider configured (RAGSTACK_IDENTITY_ENABLED unset)",
)

# A syntactically valid but unsigned-by-anyone JWT: three base64url segments with
# an RS256 header. It is not a credential — it authenticates nobody, anywhere.
JUNK_JWT = (
    "eyJhbGciOiJSUzI1NiIsImtpZCI6Im5vLXN1Y2gta2lkIiwidHlwIjoiSldUIn0"
    ".eyJpc3MiOiJodHRwczovL2FjY291bnRzLmdvb2dsZS5jb20iLCJzdWIiOiIwIn0"
    ".bm90LWEtc2lnbmF0dXJl"
)


@requires_identity
@pytest.mark.parametrize(
    "credential",
    [JUNK_JWT, "Bearer " + JUNK_JWT, "not-a-token-at-all", "un=root|sig=deadbeef"],
    ids=["junk-jwt", "bearer-junk-jwt", "garbage", "fake-bvbrc-token"],
)
async def test_unverifiable_credential_is_rejected(anon_client: httpx.AsyncClient, credential):
    """401 or 503 — never 200. The credential is not signed by the pinned issuer,
    so the only two honest answers are "no" and "I could not check"."""
    resp = await anon_client.get("/v1/stats/tenants", headers={"Authorization": credential})
    assert resp.status_code in (401, 503), resp.text


@requires_identity
async def test_both_credentials_is_400(anon_client: httpx.AsyncClient):
    """Which credential authenticated a request must never be a guess."""
    if not API_KEY:
        skip_no_credential("no RAGSTACK_API_KEY configured")
    resp = await anon_client.get(
        "/v1/stats/tenants",
        headers={"Authorization": f"Bearer {JUNK_JWT}", "X-API-Key": API_KEY},
    )
    assert resp.status_code == 400, resp.text


@requires_identity
async def test_no_credential_at_all_is_not_authenticated_as_a_bearer_user(
    anon_client: httpx.AsyncClient,
):
    """An empty Authorization header must not become an identity."""
    resp = await anon_client.get("/v1/stats/tenants", headers={"Authorization": ""})
    if resp.status_code == 200:
        # Keyless dev server: the caller is the default tenant, NOT an issuer one.
        assert not resp.json()["tenant"].startswith(f"{ISSUER_LABEL}:")
    else:
        assert resp.status_code in (401, 503)


@pytest.mark.skipif(not ID_TOKEN, reason="no RAGSTACK_GOOGLE_ID_TOKEN configured")
@requires_identity
async def test_real_google_id_token_authenticates(anon_client: httpx.AsyncClient):
    """The positive path: a genuine Google ID token minted for the server's
    configured client id resolves to an issuer-scoped tenant with the explicit
    user role — never the deployment's default role."""
    resp = await anon_client.get(
        "/v1/stats/tenants", headers={"Authorization": f"Bearer {ID_TOKEN}"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tenant"].startswith(f"{ISSUER_LABEL}:")
    # The identity is `sub`, never `email`: emails are reassignable.
    assert "@" not in body["tenant"].split(":", 1)[1]
    assert body["role"] == "user"


@pytest.mark.skipif(not ID_TOKEN, reason="no RAGSTACK_GOOGLE_ID_TOKEN configured")
@requires_identity
async def test_admin_surface_stays_closed_to_a_bearer_identity(anon_client: httpx.AsyncClient):
    resp = await anon_client.get("/v1/config", headers={"Authorization": f"Bearer {ID_TOKEN}"})
    assert resp.status_code == 403, resp.text
