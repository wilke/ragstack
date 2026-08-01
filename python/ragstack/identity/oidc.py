"""Generic OIDC identity — ID-token verification against the issuer's JWKS.

What is verified, and why each check is load-bearing:

``alg``
    Must be ``RS256``. ``none`` is an unsigned token; ``HS*`` would let an
    attacker sign with the *public* key, which is public.
``kid`` → JWKS
    Keys are cached and selected by ``kid``. An unknown ``kid`` triggers exactly
    one re-fetch (the key-rotation path) and then a rejection — rotation must not
    require a restart, and a stream of bogus ``kid``s must not become a hammering
    loop against the IdP.
``iss``
    Compared against an **enumerated** set of accepted issuer strings, never a
    prefix match. (Google emits both ``accounts.google.com`` and
    ``https://accounts.google.com``, so the set genuinely has two members — that
    is a reason to enumerate, not a reason to loosen the comparison.)
``aud``
    Must contain one of our configured client ids. This is the single most-botched
    OIDC check: an unpinned ``aud`` accepts an ID token minted for *any other
    application* on the same IdP, so anyone with a Google app could log in as your
    users. A provider constructed without client ids raises at construction.
``exp`` / ``nbf`` / ``iat``
    With a clock skew of at most 300 s.

The identity is ``sub`` — never ``email``. Emails are reassignable (and
``email_verified`` is a claim about the mailbox, not about the account), so an
email-keyed tenant can be silently transferred to a different human.

We validate the **ID token** (a signed JWT), not the access token, which for most
IdPs — Google included — is an opaque string with no verifiable structure.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import time
from typing import Any

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey, RSAPublicNumbers

from ragstack.identity._http import fetch_json
from ragstack.identity.base import Identity, IdentityInvalid, IdentityUnavailable

logger = logging.getLogger(__name__)

#: Google is the integration-test issuer. It emits `iss` in two spellings, so both
#: are accepted — enumerated, not prefix-matched.
GOOGLE_ISSUER = "https://accounts.google.com"
GOOGLE_ISSUERS: tuple[str, ...] = ("https://accounts.google.com", "accounts.google.com")

#: Only RSA-SHA256. Widening this set is a security decision, not a config knob.
SUPPORTED_ALGS = frozenset({"RS256"})

MAX_CLOCK_SKEW = 300


class OidcIdentityProvider:
    """:class:`~ragstack.identity.base.IdentityProvider` for OIDC ID tokens."""

    def __init__(
        self,
        *,
        issuer: str,
        client_ids: tuple[str, ...] | list[str],
        issuer_label: str = "oidc",
        allowed_issuers: tuple[str, ...] | list[str] | None = None,
        jwks_uri: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        cache_ttl: float = 3600.0,
        min_refetch_interval: float = 60.0,
        leeway: int = MAX_CLOCK_SKEW,
        timeout: float = 5.0,
    ) -> None:
        if not issuer:
            raise ValueError("OidcIdentityProvider requires an issuer")
        self._client_ids = frozenset(c for c in client_ids if c)
        if not self._client_ids:
            # Refuse to exist rather than accept every ID token on the IdP.
            raise ValueError(
                "OidcIdentityProvider requires at least one client id: an unpinned "
                "`aud` accepts ID tokens minted for any other application"
            )
        self._issuer = issuer
        self._issuer_label = issuer_label
        self._allowed_issuers = frozenset(allowed_issuers or (issuer,))
        self._jwks_uri = jwks_uri
        self._client = http_client
        self._owns_client = http_client is None
        self._timeout = timeout
        self._cache_ttl = cache_ttl
        self._min_refetch_interval = min_refetch_interval
        self._leeway = min(max(int(leeway), 0), MAX_CLOCK_SKEW)
        self._keys: dict[str, RSAPublicKey] = {}
        self._keys_fetched_at: float | None = None

    # -- public API ---------------------------------------------------------- #

    async def authenticate(self, credential: str) -> Identity:
        header, claims, signing_input, signature = _split(credential)

        alg = header.get("alg")
        if alg not in SUPPORTED_ALGS:
            raise IdentityInvalid(f"unsupported ID-token alg {alg!r}")

        key = await self._select_key(header.get("kid"))
        try:
            key.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
        except (InvalidSignature, ValueError) as exc:
            raise IdentityInvalid("ID token signature does not verify") from exc

        # Only now are the claims anything but attacker-supplied JSON.
        self._check_claims(claims)

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise IdentityInvalid("ID token carries no sub")

        exp = claims.get("exp")
        jti = claims.get("jti")
        token_id = (
            jti
            if isinstance(jti, str) and jti
            # No jti (Google does not emit one): a digest of the credential is
            # stable per credential and reveals nothing, which is all the
            # authorization cache needs of it.
            else hashlib.sha256(credential.encode("utf-8")).hexdigest()
        )
        scopes = claims.get("scope")
        return Identity(
            subject=subject,
            issuer=self._issuer_label,
            token_id=token_id,
            expires_at=int(exp) if isinstance(exp, int | float) else None,
            scopes=frozenset(scopes.split()) if isinstance(scopes, str) else frozenset(),
        )

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- claim validation ----------------------------------------------------- #

    def _check_claims(self, claims: dict[str, Any]) -> None:
        if claims.get("iss") not in self._allowed_issuers:
            raise IdentityInvalid("ID token issuer mismatch")

        aud = claims.get("aud")
        auds = {aud} if isinstance(aud, str) else set(aud) if isinstance(aud, list) else set()
        if not auds & self._client_ids:
            raise IdentityInvalid("ID token audience does not match a configured client id")

        now = time.time()
        exp = claims.get("exp")
        if not isinstance(exp, int | float):
            raise IdentityInvalid("ID token carries no exp")
        if exp + self._leeway <= now:
            raise IdentityInvalid("ID token expired")
        for name in ("nbf", "iat"):
            value = claims.get(name)
            if isinstance(value, int | float) and value - self._leeway > now:
                raise IdentityInvalid(f"ID token {name} is in the future")

    # -- key handling --------------------------------------------------------- #

    async def _select_key(self, kid: Any) -> RSAPublicKey:
        keys = await self._jwks()
        if isinstance(kid, str) and kid:
            key = keys.get(kid)
            if key is not None:
                return key
            refreshed = await self._refresh_jwks()
            if refreshed is not None and (key := refreshed.get(kid)) is not None:
                return key
            raise IdentityInvalid("ID token key id is not in the issuer's JWKS")
        # No kid: unambiguous only when the issuer publishes a single key.
        if len(keys) == 1:
            return next(iter(keys.values()))
        raise IdentityInvalid("ID token has no kid and the issuer publishes several keys")

    async def _jwks(self) -> dict[str, RSAPublicKey]:
        fetched = self._keys_fetched_at
        if fetched is not None and time.monotonic() - fetched < self._cache_ttl:
            return self._keys
        return await self._fetch_jwks()

    async def _refresh_jwks(self) -> dict[str, RSAPublicKey] | None:
        fetched = self._keys_fetched_at
        if fetched is not None and time.monotonic() - fetched < self._min_refetch_interval:
            return None  # rate-limited: bogus kids must not become a DoS lever
        return await self._fetch_jwks()

    async def _fetch_jwks(self) -> dict[str, RSAPublicKey]:
        uri = await self._resolve_jwks_uri()
        document = await fetch_json(self._http(), uri, what="oidc jwks")
        keys = _parse_jwks(document)
        if not keys:
            raise IdentityUnavailable(f"oidc jwks at {uri} contains no usable RSA keys")
        self._keys = keys
        self._keys_fetched_at = time.monotonic()
        return keys

    async def _resolve_jwks_uri(self) -> str:
        if self._jwks_uri:
            return self._jwks_uri
        url = _discovery_url(self._issuer)
        document = await fetch_json(self._http(), url, what="oidc discovery")
        uri = document.get("jwks_uri") if isinstance(document, dict) else None
        if not isinstance(uri, str) or not uri:
            raise IdentityUnavailable(f"oidc discovery at {url} has no jwks_uri")
        self._jwks_uri = uri
        return uri

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client


# -- module helpers ----------------------------------------------------------- #


def _discovery_url(issuer: str) -> str:
    base = issuer if "://" in issuer else f"https://{issuer}"
    return f"{base.rstrip('/')}/.well-known/openid-configuration"


def _b64url(segment: str) -> bytes:
    padded = segment + "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
        raise IdentityInvalid("malformed ID token: bad base64url") from exc


def _split(credential: str) -> tuple[dict[str, Any], dict[str, Any], bytes, bytes]:
    """Return ``(header, claims, signing_input, signature)`` for a compact JWS."""
    parts = credential.split(".")
    if len(parts) != 3 or not all(parts):
        raise IdentityInvalid("malformed ID token: expected three JWT segments")
    header_b64, claims_b64, sig_b64 = parts
    header = _json_object(_b64url(header_b64), "header")
    claims = _json_object(_b64url(claims_b64), "claims")
    signing_input = f"{header_b64}.{claims_b64}".encode("ascii")
    return header, claims, signing_input, _b64url(sig_b64)


def _json_object(raw: bytes, what: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise IdentityInvalid(f"malformed ID token: {what} is not JSON") from exc
    if not isinstance(value, dict):
        raise IdentityInvalid(f"malformed ID token: {what} is not an object")
    return value


def _parse_jwks(document: Any) -> dict[str, RSAPublicKey]:
    """Build ``kid -> RSAPublicKey`` from a JWKS document, skipping anything that
    is not an RSA signing key rather than failing the whole set."""
    keys: dict[str, RSAPublicKey] = {}
    entries = document.get("keys") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        return keys
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("kty") != "RSA":
            continue
        if entry.get("use") not in (None, "sig"):
            continue
        if entry.get("alg") not in (None, *SUPPORTED_ALGS):
            continue
        kid = entry.get("kid")
        n, e = entry.get("n"), entry.get("e")
        if not isinstance(kid, str) or not isinstance(n, str) or not isinstance(e, str):
            continue
        try:
            numbers = RSAPublicNumbers(
                e=int.from_bytes(_b64url(e), "big"), n=int.from_bytes(_b64url(n), "big")
            )
            keys[kid] = numbers.public_key()
        except (IdentityInvalid, ValueError):
            continue
    return keys
