"""OIDC ID-token verification — issuer/audience pinning and key rotation.

Tokens are signed with an RSA key generated in-process; discovery and JWKS are
served by a counting mock transport. Nothing leaves the machine.
"""
from __future__ import annotations

import time

import httpx
import pytest

from ragstack.identity import (
    GOOGLE_ISSUERS,
    IdentityInvalid,
    IdentityUnavailable,
    OidcIdentityProvider,
)
from tests.identity_support import (
    FakeKeyServer,
    generate_key,
    google_claims,
    id_token,
    jwk_for,
)

KEY = generate_key(2048)
OTHER_KEY = generate_key(2048)

ISSUER = "https://accounts.google.com"
DISCOVERY = f"{ISSUER}/.well-known/openid-configuration"
JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"
CLIENT_ID = "ragstack-test.apps.googleusercontent.com"


def make_server(*keys: tuple[str, object]) -> FakeKeyServer:
    entries = keys or (("key-1", KEY),)
    return FakeKeyServer(
        routes={
            DISCOVERY: {"issuer": ISSUER, "jwks_uri": JWKS_URI},
            JWKS_URI: {"keys": [jwk_for(k, kid) for kid, k in entries]},
        }
    )


def make_provider(server: FakeKeyServer, **kwargs) -> OidcIdentityProvider:
    kwargs.setdefault("issuer", ISSUER)
    kwargs.setdefault("client_ids", (CLIENT_ID,))
    kwargs.setdefault("issuer_label", "google")
    kwargs.setdefault("allowed_issuers", GOOGLE_ISSUERS)
    kwargs.setdefault("min_refetch_interval", 0.0)
    return OidcIdentityProvider(http_client=server.client(), **kwargs)


# -- happy path --------------------------------------------------------------- #


async def test_valid_id_token_authenticates():
    identity = await make_provider(make_server()).authenticate(
        id_token(KEY, **google_claims())
    )
    assert identity.subject == "104729384756102938475"
    assert identity.issuer == "google"
    assert identity.expires_at is not None and identity.expires_at > time.time()


async def test_discovery_and_jwks_are_cached():
    server = make_server()
    provider = make_provider(server)
    for _ in range(3):
        await provider.authenticate(id_token(KEY, **google_claims()))
    assert server.hits[DISCOVERY] == 1
    assert server.hits[JWKS_URI] == 1


async def test_identity_is_sub_never_email():
    """Emails are reassignable; an email-keyed tenant can be handed to a stranger
    when a mailbox is recycled. The subject is always `sub`."""
    identity = await make_provider(make_server()).authenticate(
        id_token(KEY, **google_claims(sub="12345", email="alice@example.org"))
    )
    assert identity.subject == "12345"
    assert "alice@example.org" not in (identity.subject, identity.token_id)


async def test_both_google_issuer_spellings_are_accepted():
    # Google mints `iss` both ways. Enumerated, not prefix-matched.
    for iss in GOOGLE_ISSUERS:
        identity = await make_provider(make_server()).authenticate(
            id_token(KEY, **google_claims(iss=iss))
        )
        assert identity.subject == "104729384756102938475"


# -- audience pinning --------------------------------------------------------- #


async def test_audience_mismatch_is_rejected():
    """The most-botched OIDC check: an ID token minted for a *different* app on
    the same IdP must not authenticate anyone here."""
    token = id_token(KEY, **google_claims(aud="some-other-app.apps.googleusercontent.com"))
    with pytest.raises(IdentityInvalid, match="audience"):
        await make_provider(make_server()).authenticate(token)


async def test_audience_list_containing_our_client_id_is_accepted():
    token = id_token(KEY, **google_claims(aud=["another-app", CLIENT_ID]))
    identity = await make_provider(make_server()).authenticate(token)
    assert identity.subject == "104729384756102938475"


async def test_missing_audience_is_rejected():
    claims = google_claims()
    claims.pop("aud")
    with pytest.raises(IdentityInvalid, match="audience"):
        await make_provider(make_server()).authenticate(id_token(KEY, **claims))


def test_provider_without_client_ids_refuses_to_exist():
    with pytest.raises(ValueError, match="client id"):
        OidcIdentityProvider(issuer=ISSUER, client_ids=())


# -- issuer pinning ------------------------------------------------------------ #


async def test_issuer_mismatch_is_rejected():
    token = id_token(KEY, **google_claims(iss="https://accounts.evil.example"))
    with pytest.raises(IdentityInvalid, match="issuer"):
        await make_provider(make_server()).authenticate(token)


async def test_issuer_is_not_prefix_matched():
    # `https://accounts.google.com.evil.example` starts with the real issuer.
    token = id_token(KEY, **google_claims(iss=f"{ISSUER}.evil.example"))
    with pytest.raises(IdentityInvalid, match="issuer"):
        await make_provider(make_server()).authenticate(token)


# -- signature and algorithm --------------------------------------------------- #


async def test_signature_from_a_different_key_is_rejected():
    token = id_token(OTHER_KEY, **google_claims())
    with pytest.raises(IdentityInvalid, match="signature"):
        await make_provider(make_server()).authenticate(token)


async def test_forged_signature_is_rejected():
    token = id_token(KEY, sign=False, **google_claims())
    with pytest.raises(IdentityInvalid, match="signature"):
        await make_provider(make_server()).authenticate(token)


@pytest.mark.parametrize("alg", ["none", "HS256", "RS512"])
async def test_unsupported_algorithms_are_rejected(alg):
    """`none` is an unsigned token; HS256 would let an attacker sign with the
    public key, which is public."""
    token = id_token(KEY, alg=alg, **google_claims())
    with pytest.raises(IdentityInvalid, match="alg"):
        await make_provider(make_server()).authenticate(token)


@pytest.mark.parametrize(
    "credential", ["", "not.a.jwt.at.all", "onlyonesegment", "a.b", "a..c", "!!!.@@@.###"]
)
async def test_malformed_tokens_are_rejected(credential):
    with pytest.raises(IdentityInvalid):
        await make_provider(make_server()).authenticate(credential)


async def test_token_without_sub_is_rejected():
    claims = google_claims()
    claims.pop("sub")
    with pytest.raises(IdentityInvalid, match="sub"):
        await make_provider(make_server()).authenticate(id_token(KEY, **claims))


# -- time validation ------------------------------------------------------------ #


async def test_expired_token_is_rejected():
    token = id_token(KEY, **google_claims(exp=int(time.time()) - 3600))
    with pytest.raises(IdentityInvalid, match="expired"):
        await make_provider(make_server()).authenticate(token)


async def test_token_without_exp_is_rejected():
    claims = google_claims()
    claims.pop("exp")
    with pytest.raises(IdentityInvalid, match="exp"):
        await make_provider(make_server()).authenticate(id_token(KEY, **claims))


async def test_nbf_far_in_the_future_is_rejected():
    token = id_token(KEY, **google_claims(nbf=int(time.time()) + 3600))
    with pytest.raises(IdentityInvalid, match="nbf"):
        await make_provider(make_server()).authenticate(token)


async def test_small_clock_skew_is_tolerated():
    # Within 300 s: a slightly-fast IdP clock must not lock users out.
    token = id_token(KEY, **google_claims(nbf=int(time.time()) + 60, iat=int(time.time()) + 60))
    identity = await make_provider(make_server()).authenticate(token)
    assert identity.subject == "104729384756102938475"


def test_clock_skew_is_capped_at_300s():
    provider = make_provider(make_server(), leeway=86400)
    assert provider._leeway == 300


# -- key rotation --------------------------------------------------------------- #


async def test_unknown_kid_refetches_exactly_once_then_rejects():
    server = make_server(("key-1", KEY))
    provider = make_provider(server)
    await provider.authenticate(id_token(KEY, kid="key-1", **google_claims()))
    assert server.hits[JWKS_URI] == 1

    with pytest.raises(IdentityInvalid, match="key id"):
        await provider.authenticate(id_token(KEY, kid="key-99", **google_claims()))
    assert server.hits[JWKS_URI] == 2  # exactly one refetch, then a rejection


async def test_rotated_kid_is_picked_up_without_a_restart():
    server = make_server(("key-1", KEY))
    provider = make_provider(server)
    await provider.authenticate(id_token(KEY, kid="key-1", **google_claims()))

    # The IdP rotates: a new kid appears in the JWKS.
    server.routes[JWKS_URI] = {"keys": [jwk_for(OTHER_KEY, "key-2")]}
    identity = await provider.authenticate(
        id_token(OTHER_KEY, kid="key-2", **google_claims())
    )
    assert identity.subject == "104729384756102938475"
    assert server.hits[JWKS_URI] == 2


async def test_unknown_kids_cannot_hammer_the_idp():
    server = make_server()
    provider = make_provider(server, min_refetch_interval=3600.0)
    await provider.authenticate(id_token(KEY, **google_claims()))

    for n in range(5):
        with pytest.raises(IdentityInvalid, match="key id"):
            await provider.authenticate(id_token(KEY, kid=f"bogus-{n}", **google_claims()))
    assert server.hits[JWKS_URI] == 1


async def test_missing_kid_uses_the_only_key():
    server = make_server()
    identity = await make_provider(server).authenticate(
        id_token(KEY, kid="", **google_claims())
    )
    assert identity.subject == "104729384756102938475"


async def test_missing_kid_with_several_published_keys_is_ambiguous():
    server = make_server(("key-1", KEY), ("key-2", OTHER_KEY))
    with pytest.raises(IdentityInvalid, match="kid"):
        await make_provider(server).authenticate(id_token(KEY, kid="", **google_claims()))


# -- availability vs. invalidity -------------------------------------------------- #


@pytest.mark.parametrize("route", [DISCOVERY, JWKS_URI])
async def test_unreachable_idp_is_unavailable_never_invalid(route):
    server = make_server()
    del server.routes[route]  # 404 from the mock transport
    with pytest.raises(IdentityUnavailable):
        await make_provider(server).authenticate(id_token(KEY, **google_claims()))


async def test_transport_failure_is_unavailable():
    server = make_server()
    server.error = httpx.ConnectError("connection refused")
    with pytest.raises(IdentityUnavailable):
        await make_provider(server).authenticate(id_token(KEY, **google_claims()))


async def test_discovery_without_jwks_uri_is_unavailable():
    server = make_server()
    server.routes[DISCOVERY] = {"issuer": ISSUER}
    with pytest.raises(IdentityUnavailable, match="jwks_uri"):
        await make_provider(server).authenticate(id_token(KEY, **google_claims()))


async def test_jwks_without_usable_keys_is_unavailable():
    server = make_server()
    server.routes[JWKS_URI] = {"keys": [{"kty": "EC", "kid": "ec-1"}]}
    with pytest.raises(IdentityUnavailable, match="no usable RSA keys"):
        await make_provider(server).authenticate(id_token(KEY, **google_claims()))


async def test_explicit_jwks_uri_skips_discovery():
    server = make_server()
    provider = make_provider(server, jwks_uri=JWKS_URI)
    await provider.authenticate(id_token(KEY, **google_claims()))
    assert DISCOVERY not in server.hits
