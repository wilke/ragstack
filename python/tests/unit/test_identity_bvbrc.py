"""BV-BRC signed-token verification — the attacks, not just the happy path.

Every token here is signed with an RSA key generated in-process. No real
credential is read and nothing leaves the machine.
"""
from __future__ import annotations

import time

import httpx
import pytest

from ragstack.identity import BvbrcSignedToken, IdentityInvalid, IdentityUnavailable
from tests.identity_support import (
    BVBRC_SUBJECT_URL,
    FakeKeyServer,
    bvbrc_payload,
    bvbrc_token,
    generate_key,
    public_pem,
    sign_bvbrc,
)

# 1024-bit because that is what BV-BRC signs with; generated fresh, never a real key.
KEY = generate_key(1024)
OTHER_KEY = generate_key(1024)
ALLOWLIST = (BVBRC_SUBJECT_URL, "https://user.bv-brc.org/public_key")


def make_server(key=KEY, url: str = BVBRC_SUBJECT_URL) -> FakeKeyServer:
    return FakeKeyServer(routes={url: {"pubkey": public_pem(key)}})


def make_provider(server: FakeKeyServer, **kwargs) -> BvbrcSignedToken:
    kwargs.setdefault("min_refetch_interval", 0.0)
    return BvbrcSignedToken(allowlist=ALLOWLIST, http_client=server.client(), **kwargs)


# -- happy path --------------------------------------------------------------- #


async def test_valid_token_authenticates():
    server = make_server()
    identity = await make_provider(server).authenticate(bvbrc_token(KEY))

    assert identity.subject == "alice@patricbrc.org"
    assert identity.issuer == "bvbrc"
    assert identity.token_id == "11111111-2222-3333-4444-555555555555"
    assert identity.expires_at is not None and identity.expires_at > time.time()


async def test_public_key_is_cached_across_requests():
    server = make_server()
    provider = make_provider(server)
    for _ in range(3):
        await provider.authenticate(bvbrc_token(KEY))
    assert server.total_hits() == 1


async def test_raw_pem_body_and_escaped_newlines_both_parse():
    # Key servers in the wild return the PEM as a bare body, and sometimes with
    # literal backslash-n, which load_pem_public_key rejects outright.
    for body in (public_pem(KEY), public_pem(KEY).replace("\n", "\\n")):
        server = FakeKeyServer(routes={BVBRC_SUBJECT_URL: body})
        identity = await make_provider(server).authenticate(bvbrc_token(KEY))
        assert identity.subject == "alice@patricbrc.org"


# -- forgery ------------------------------------------------------------------ #


async def test_signature_from_a_different_key_is_rejected():
    server = make_server()  # serves KEY; the token is signed with OTHER_KEY
    with pytest.raises(IdentityInvalid):
        await make_provider(server).authenticate(bvbrc_token(OTHER_KEY))


async def test_tampered_username_is_rejected():
    # Sign as alice, then rewrite the username inside the signed region.
    token = bvbrc_token(KEY)
    forged = token.replace("un=alice@patricbrc.org", "un=root@patricbrc.org")
    with pytest.raises(IdentityInvalid):
        await make_provider(make_server()).authenticate(forged)


async def test_garbage_signature_is_rejected():
    payload = bvbrc_payload(expiry=int(time.time()) + 60)
    with pytest.raises(IdentityInvalid):
        await make_provider(make_server()).authenticate(f"{payload}|sig={'ab' * 64}")


@pytest.mark.parametrize(
    "credential",
    [
        "",
        "un=alice|tokenid=x|expiry=99999999999",  # no signature at all
        "un=alice|tokenid=x|expiry=99999999999|sig=nothex!!",
        "un=alice|tokenid=x|expiry=99999999999|sig=",
        "|sig=aabb",  # empty payload
    ],
)
async def test_malformed_tokens_are_rejected(credential):
    with pytest.raises(IdentityInvalid):
        await make_provider(make_server()).authenticate(credential)


# -- issuer pinning ----------------------------------------------------------- #


async def test_signing_subject_outside_allowlist_is_rejected():
    """The forgery BV-BRC's own validateToken.js is open to.

    An attacker signs `un=alice` with their own key and points SigningSubject at
    their own key server. If we fetched the key the token named, it would verify.
    """
    evil = "https://evil.example.com/public_key"
    server = FakeKeyServer(routes={evil: {"pubkey": public_pem(OTHER_KEY)}})
    token = bvbrc_token(OTHER_KEY, signing_subject=evil)

    with pytest.raises(IdentityInvalid, match="not an allowed issuer"):
        await make_provider(server).authenticate(token)
    # And the attacker-named URL was never even contacted — the pin is checked
    # before any network call, so it is not an SSRF lever either.
    assert server.total_hits() == 0


async def test_missing_signing_subject_is_rejected():
    payload = "un=alice|tokenid=x|expiry=99999999999"
    with pytest.raises(IdentityInvalid, match="not an allowed issuer"):
        await make_provider(make_server()).authenticate(sign_bvbrc(payload, KEY))


def test_empty_allowlist_is_a_construction_error():
    # An unpinned provider must not be constructible by omission.
    with pytest.raises(ValueError, match="allowlist"):
        BvbrcSignedToken(allowlist=())


# -- expiry ------------------------------------------------------------------- #


async def test_expired_token_is_rejected_without_touching_the_key_server():
    server = make_server()
    token = bvbrc_token(KEY, ttl=-1)

    with pytest.raises(IdentityInvalid, match="expired"):
        await make_provider(server).authenticate(token)
    assert server.total_hits() == 0  # checked every request, before anything else


async def test_token_without_expiry_is_rejected():
    payload = bvbrc_payload(expiry=None)
    with pytest.raises(IdentityInvalid, match="expiry"):
        await make_provider(make_server()).authenticate(sign_bvbrc(payload, KEY))


async def test_non_numeric_expiry_is_rejected():
    payload = "un=a|tokenid=x|expiry=soon|SigningSubject=" + BVBRC_SUBJECT_URL
    with pytest.raises(IdentityInvalid, match="expiry"):
        await make_provider(make_server()).authenticate(sign_bvbrc(payload, KEY))


# -- `un=` is never read before verification ---------------------------------- #


async def test_fields_after_the_signature_are_not_parsed():
    """Appending `|un=eve` to someone else's valid token must change nothing.

    The signed region is the substring preceding `|sig=`; a parser that scanned
    the whole credential would hand back `eve` with a perfectly valid signature.
    """
    token = bvbrc_token(KEY) + "|un=eve@patricbrc.org|tokenid=evil"
    identity = await make_provider(make_server()).authenticate(token)
    assert identity.subject == "alice@patricbrc.org"
    assert identity.token_id == "11111111-2222-3333-4444-555555555555"


async def test_no_identity_is_derived_when_the_key_server_is_unreachable():
    """The strongest form of "un= is never read before verification": if we cannot
    verify at all, there is no identity — not a degraded one built from the token."""
    server = make_server()
    server.error = httpx.ConnectError("connection refused")

    with pytest.raises(IdentityUnavailable):
        await make_provider(server).authenticate(bvbrc_token(KEY))


async def test_duplicate_field_does_not_shadow_the_first():
    # `un=alice|…|un=eve`, all inside the signed region: first occurrence wins, so
    # a duplicate cannot shadow the value an earlier check read.
    payload = bvbrc_payload(extra="un=eve@patricbrc.org", expiry=int(time.time()) + 60)
    identity = await make_provider(make_server()).authenticate(sign_bvbrc(payload, KEY))
    assert identity.subject == "alice@patricbrc.org"


async def test_verified_token_without_un_is_rejected():
    payload = f"tokenid=x|expiry={int(time.time()) + 60}|SigningSubject={BVBRC_SUBJECT_URL}"
    with pytest.raises(IdentityInvalid, match="un="):
        await make_provider(make_server()).authenticate(sign_bvbrc(payload, KEY))


async def test_verified_token_without_tokenid_is_rejected():
    payload = f"un=alice|expiry={int(time.time()) + 60}|SigningSubject={BVBRC_SUBJECT_URL}"
    with pytest.raises(IdentityInvalid, match="tokenid"):
        await make_provider(make_server()).authenticate(sign_bvbrc(payload, KEY))


# -- availability vs. invalidity ---------------------------------------------- #


@pytest.mark.parametrize("failure", ["transport", "http500", "http404"])
async def test_key_server_failure_is_unavailable_never_invalid(failure):
    """503, never 401, and never an allow. A key server that is down says nothing
    about the caller, and reporting it as 401 hides our own outage."""
    server = make_server()
    if failure == "transport":
        server.error = httpx.ConnectTimeout("timed out")
    else:
        server.status = 500 if failure == "http500" else 404

    with pytest.raises(IdentityUnavailable):
        await make_provider(server).authenticate(bvbrc_token(KEY))


async def test_unparseable_public_key_is_unavailable_not_invalid():
    server = FakeKeyServer(routes={BVBRC_SUBJECT_URL: {"pubkey": "not a pem"}})
    with pytest.raises(IdentityUnavailable):
        await make_provider(server).authenticate(bvbrc_token(KEY))


async def test_key_server_response_without_pubkey_is_unavailable():
    server = FakeKeyServer(routes={BVBRC_SUBJECT_URL: {"unexpected": "shape"}})
    with pytest.raises(IdentityUnavailable):
        await make_provider(server).authenticate(bvbrc_token(KEY))


# -- key rotation -------------------------------------------------------------- #


async def test_rotated_key_is_refetched_and_the_token_verifies():
    served = {"key": OTHER_KEY}  # stale key first, rotated key on the refetch
    server = FakeKeyServer(
        routes={BVBRC_SUBJECT_URL: lambda: {"pubkey": public_pem(served["key"])}}
    )
    provider = make_provider(server)

    with pytest.raises(IdentityInvalid):
        await provider.authenticate(bvbrc_token(KEY))
    assert server.total_hits() == 2  # initial fetch + one rotation refetch

    served["key"] = KEY
    identity = await provider.authenticate(bvbrc_token(KEY))
    assert identity.subject == "alice@patricbrc.org"


async def test_bad_signatures_cannot_hammer_the_key_server():
    # Rate-limited refetch: a flood of forged tokens must not become a flood of
    # requests to BV-BRC's key server.
    server = make_server()
    provider = make_provider(server, min_refetch_interval=3600.0)

    for _ in range(5):
        with pytest.raises(IdentityInvalid):
            await provider.authenticate(bvbrc_token(OTHER_KEY))
    assert server.total_hits() == 1


async def test_un_doubles_as_display_name_but_never_a_verified_email():
    """The token format carries no profile claims; `un` is the only
    human-legible handle. It is email-shaped, but BV-BRC asserts nothing about
    the mailbox — so email_verified must stay False (ADR-0004: an unverified
    email never claims a pending share)."""
    identity = await make_provider(make_server()).authenticate(bvbrc_token(KEY))
    assert identity.display_name == "alice@patricbrc.org"
    assert identity.email_verified is False
