"""Test-only key material and token factories for the identity layer.

Every fixture here is signed with an RSA key generated **in this process**. No
real credential is ever read (``~/.patric_token`` is deliberately untouched) and
no request ever leaves the machine: the key servers and JWKS endpoints are
``httpx.MockTransport`` handlers that also count how many times they were hit, so
the caching and refetch-on-rotation paths are observable.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

BVBRC_SUBJECT_URL = "https://user.patricbrc.org/public_key"


def generate_key(bits: int) -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=bits)


def public_pem(key: rsa.RSAPrivateKey) -> str:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


# -- BV-BRC ------------------------------------------------------------------- #


def bvbrc_payload(
    *,
    un: str = "alice@patricbrc.org",
    tokenid: str = "11111111-2222-3333-4444-555555555555",
    expiry: int | None = None,
    signing_subject: str = BVBRC_SUBJECT_URL,
    extra: str = "",
) -> str:
    parts = [f"un={un}", f"tokenid={tokenid}"]
    if expiry is not None:
        parts.append(f"expiry={expiry}")
    parts += ["client_id=" + un, "token_type=Bearer", "realm=patricbrc.org"]
    if extra:
        parts.append(extra)
    parts.append(f"SigningSubject={signing_subject}")
    return "|".join(parts)


def sign_bvbrc(payload: str, key: rsa.RSAPrivateKey) -> str:
    """Append a valid ``|sig=<hex>`` for ``payload`` (RSA PKCS#1 v1.5 / SHA-1)."""
    sig = key.sign(payload.encode("utf-8"), padding.PKCS1v15(), hashes.SHA1())
    return f"{payload}|sig={sig.hex()}"


def bvbrc_token(key: rsa.RSAPrivateKey, *, ttl: int = 3600, **kwargs: Any) -> str:
    kwargs.setdefault("expiry", int(time.time()) + ttl)
    return sign_bvbrc(bvbrc_payload(**kwargs), key)


# -- OIDC --------------------------------------------------------------------- #


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def jwk_for(key: rsa.RSAPrivateKey, kid: str) -> dict[str, str]:
    numbers = key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": kid,
        "n": b64url(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
        "e": b64url(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
    }


def id_token(
    key: rsa.RSAPrivateKey,
    *,
    kid: str = "key-1",
    alg: str = "RS256",
    sign: bool = True,
    **claims: Any,
) -> str:
    """Build a compact JWS ID token. ``sign=False`` produces a garbage signature
    of the right shape, for the forged-signature case."""
    header: dict[str, Any] = {"alg": alg, "typ": "JWT"}
    if kid:
        header["kid"] = kid
    header_b64 = b64url(json.dumps(header).encode())
    claims_b64 = b64url(json.dumps(claims).encode())
    signing_input = f"{header_b64}.{claims_b64}".encode("ascii")
    if sign:
        sig = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    else:
        sig = hashlib.sha256(signing_input).digest() * (key.key_size // 8 // 32)
    return f"{header_b64}.{claims_b64}.{b64url(sig)}"


def google_claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": "https://accounts.google.com",
        "sub": "104729384756102938475",
        "aud": "ragstack-test.apps.googleusercontent.com",
        "email": "alice@example.org",
        "email_verified": True,
        "iat": now,
        "exp": now + 3600,
    }
    claims.update(overrides)
    return claims


# -- fake key servers --------------------------------------------------------- #


@dataclass
class FakeKeyServer:
    """An httpx transport serving key material, counting hits per URL path."""

    routes: dict[str, Any] = field(default_factory=dict)
    hits: dict[str, int] = field(default_factory=dict)
    #: When set, every request raises this instead of answering.
    error: Exception | None = None
    #: When set, every request answers with this status instead of a body.
    status: int | None = None

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def total_hits(self) -> int:
        return sum(self.hits.values())

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.hits[url] = self.hits.get(url, 0) + 1
        if self.error is not None:
            raise self.error
        if self.status is not None:
            return httpx.Response(self.status, text="nope")
        body = self.routes.get(url)
        if body is None:
            return httpx.Response(404, text="no such route")
        if callable(body):
            body = body()
        if isinstance(body, str):
            return httpx.Response(200, text=body)
        return httpx.Response(200, json=body)
