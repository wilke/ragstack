"""BV-BRC signed-token identity — offline signature verification.

Wire format (pipe-separated ``key=value`` fields, signature last)::

    un=alice@patricbrc.org|tokenid=<uuid>|expiry=<epoch>|client_id=…|
    token_type=Bearer|realm=patricbrc.org|SigningSubject=<url>|sig=<hex>

The signature is RSA (PKCS#1 v1.5) over **SHA-1**, hex-encoded, computed over the
substring *preceding* ``|sig=`` — so every field except the signature itself is
integrity-protected, including ``un`` and ``expiry``.

Why we do not reuse BV-BRC's own check
--------------------------------------
BV-BRC's ``validateToken.js`` fetches the verifying key from whatever
``SigningSubject`` URL *the token itself embeds*, and its allowlist guard builds
``new Error(...)`` without ever ``throw``-ing it — so the guard is dead code.
Unpinned, that means anyone who can serve a URL can mint a token for any
username: point ``SigningSubject`` at your own key server, sign
``un=someone-else`` with your own key, and it verifies.

So the allowlist here is **hard-pinned in config** (``IDENTITY_ISSUER_ALLOWLIST``,
defaulting to the canonical set from BV-BRC's ``P3AuthConstants.pm``) and a token
whose ``SigningSubject`` is not in it is rejected before any network call is made.
Only after the signature verifies against a pinned key is ``un=`` read — at which
point it is not an echo of client input but a server-set, signature-protected
claim (``generateToken.js`` sets it).

Known gap, by design of the token format: BV-BRC tokens carry **no audience**. One
token is equally valid at GoWe, the Workspace and here. We mitigate with a short
identity-cache TTL and by never accepting a credential relayed by another service
on a user's behalf; we cannot fix it from this side. Likewise there is no
revocation before ``expiry`` without an introspection endpoint.
"""
from __future__ import annotations

import binascii
import logging
import time

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

from ragstack.identity._http import fetch
from ragstack.identity.base import Identity, IdentityInvalid, IdentityUnavailable

logger = logging.getLogger(__name__)

#: The canonical signing subjects, from BV-BRC's ``P3AuthConstants.pm``. This is
#: the default value of ``IDENTITY_ISSUER_ALLOWLIST``; deployments may narrow it,
#: and an empty allowlist is a configuration error (it would authenticate nobody
#: — or, if the check were skipped, everybody).
DEFAULT_SIGNING_SUBJECTS: tuple[str, ...] = (
    "https://user.patricbrc.org/public_key",
    "https://user.bv-brc.org/public_key",
    "https://user.alpha.patricbrc.org/public_key",
    "https://user.beta.patricbrc.org/public_key",
)

ISSUER = "bvbrc"

_SIG_SEPARATOR = "|sig="


class _CachedKey:
    __slots__ = ("key", "fetched_at")

    def __init__(self, key: RSAPublicKey, fetched_at: float) -> None:
        self.key = key
        self.fetched_at = fetched_at


class BvbrcSignedToken:
    """:class:`~ragstack.identity.base.IdentityProvider` for BV-BRC tokens.

    Verification is fully offline apart from fetching the public key, which is
    cached for ``key_ttl`` (24 h by default) and re-fetched when a signature fails
    to verify (the key-rotation path). That refetch is rate-limited by
    ``min_refetch_interval`` so a stream of garbage signatures cannot be turned
    into a hammering loop against the BV-BRC key server.
    """

    def __init__(
        self,
        *,
        allowlist: tuple[str, ...] | list[str] = DEFAULT_SIGNING_SUBJECTS,
        http_client: httpx.AsyncClient | None = None,
        key_ttl: float = 86400.0,
        min_refetch_interval: float = 60.0,
        timeout: float = 5.0,
    ) -> None:
        self._allowlist = frozenset(allowlist)
        if not self._allowlist:
            raise ValueError(
                "BvbrcSignedToken requires a non-empty SigningSubject allowlist; "
                "an unpinned issuer lets anyone forge any username"
            )
        self._client = http_client
        self._owns_client = http_client is None
        self._timeout = timeout
        self._key_ttl = key_ttl
        self._min_refetch_interval = min_refetch_interval
        self._keys: dict[str, _CachedKey] = {}

    # -- public API ---------------------------------------------------------- #

    async def authenticate(self, credential: str) -> Identity:
        """Verify ``credential`` and return the caller's identity.

        Order matters and is part of the contract: parse → pin the issuer →
        check expiry → verify signature → *only then* read ``un``.
        """
        payload, sig = _split(credential)
        fields = _parse_fields(payload)

        signing_subject = fields.get("SigningSubject", "")
        if signing_subject not in self._allowlist:
            # Before any network call: an unpinned SigningSubject is the forgery
            # vector, so we never even fetch the key it names.
            raise IdentityInvalid("token SigningSubject is not an allowed issuer")

        expires_at = _expiry(fields)
        if expires_at is not None and expires_at <= time.time():
            # Checked on EVERY request, uncached — an expired credential never
            # reaches the verification path or the identity cache.
            raise IdentityInvalid("token expired")

        await self._verify(signing_subject, payload, sig)

        # Past this line, and not one line earlier, `fields` is trustworthy: it
        # was parsed from the exact byte range the signature covers.
        subject = fields.get("un", "")
        if not subject:
            raise IdentityInvalid("verified token carries no un= subject")
        token_id = fields.get("tokenid", "")
        if not token_id:
            raise IdentityInvalid("verified token carries no tokenid")

        return Identity(
            subject=subject,
            issuer=ISSUER,
            token_id=token_id,
            expires_at=expires_at,
            # The token format carries no profile claims. `un` is the only
            # human-legible handle, so it doubles as the display name; it is
            # email-shaped but carries no verified-email flag, so
            # email/email_verified stay at their empty/False defaults.
            display_name=subject,
        )

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- internals ----------------------------------------------------------- #

    async def _verify(self, url: str, payload: str, sig: bytes) -> None:
        key = await self._public_key(url)
        if _signature_ok(key, payload, sig):
            return
        # Rotation path: the pinned issuer may have rolled its key since we
        # cached it. Refetch once (rate-limited) and retry; still bad → 401.
        refreshed = await self._refresh_key(url)
        if refreshed is not None and _signature_ok(refreshed, payload, sig):
            return
        raise IdentityInvalid("token signature does not verify")

    async def _public_key(self, url: str) -> RSAPublicKey:
        cached = self._keys.get(url)
        now = time.monotonic()
        if cached is not None and now - cached.fetched_at < self._key_ttl:
            return cached.key
        return await self._fetch_key(url)

    async def _refresh_key(self, url: str) -> RSAPublicKey | None:
        cached = self._keys.get(url)
        if (
            cached is not None
            and time.monotonic() - cached.fetched_at < self._min_refetch_interval
        ):
            return None  # rate-limited: bad signatures must not become a DoS lever
        return await self._fetch_key(url)

    async def _fetch_key(self, url: str) -> RSAPublicKey:
        # `url` is always a member of the pinned allowlist here — never a value
        # taken from the token — so this cannot be steered into an SSRF.
        client = self._http()
        resp = await fetch(client, url, what="bvbrc public key")
        pem = _extract_pem(resp)
        try:
            key = serialization.load_pem_public_key(pem.encode("utf-8"))
        except Exception as exc:
            raise IdentityUnavailable(f"bvbrc public key at {url} is unparseable") from exc
        if not isinstance(key, RSAPublicKey):
            raise IdentityUnavailable(f"bvbrc public key at {url} is not an RSA key")
        self._keys[url] = _CachedKey(key, time.monotonic())
        return key

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client


def _http_body_pem(body: str) -> str:
    # Some deployments return the PEM with literal backslash-n rather than real
    # newlines; load_pem_public_key rejects that outright.
    return body.replace("\\n", "\n").strip()


def _extract_pem(resp: httpx.Response) -> str:
    """Pull the PEM out of a key-server response (JSON ``{"pubkey": …}`` or raw)."""
    try:
        body = resp.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        pem = body.get("pubkey") or body.get("public_key") or ""
        if not isinstance(pem, str) or not pem:
            raise IdentityUnavailable("bvbrc key server response has no pubkey field")
        return _http_body_pem(pem)
    return _http_body_pem(resp.text)


def _signature_ok(key: RSAPublicKey, payload: str, sig: bytes) -> bool:
    try:
        # SHA-1 is what BV-BRC signs with; the choice is theirs, not ours. It is
        # sound for signature *verification* of an existing scheme (a collision
        # attack would require the signer's cooperation), but it is the reason
        # this provider stays pinned to a small, explicit issuer set.
        key.verify(sig, payload.encode("utf-8"), padding.PKCS1v15(), hashes.SHA1())
    except InvalidSignature:
        return False
    except ValueError:
        # e.g. signature length != modulus length
        return False
    return True


def _split(credential: str) -> tuple[str, bytes]:
    """Return ``(signed_payload, signature_bytes)``.

    The signed payload is everything *before the first* ``|sig=``; the signature
    is the hex run that follows, up to the next ``|``. Anything after that is
    outside the signed region and is discarded rather than parsed — appending
    ``|un=eve`` to a valid token must not change who the caller is.
    """
    payload, sep, tail = credential.partition(_SIG_SEPARATOR)
    if not sep or not payload:
        raise IdentityInvalid("malformed BV-BRC token: no signature")
    sig_hex = tail.split("|", 1)[0].strip()
    try:
        sig = binascii.unhexlify(sig_hex)
    except (binascii.Error, ValueError) as exc:
        raise IdentityInvalid("malformed BV-BRC token: signature is not hex") from exc
    if not sig:
        raise IdentityInvalid("malformed BV-BRC token: empty signature")
    return payload, sig


def _parse_fields(payload: str) -> dict[str, str]:
    """Parse ``k=v|k=v`` into a dict. First occurrence of a key wins, so a
    duplicated field cannot be used to shadow the one a validator read."""
    fields: dict[str, str] = {}
    for part in payload.split("|"):
        key, sep, value = part.partition("=")
        if sep and key not in fields:
            fields[key] = value
    return fields


def _expiry(fields: dict[str, str]) -> int | None:
    raw = fields.get("expiry")
    if raw is None:
        # A token with no expiry never expires. Refuse it rather than mint an
        # immortal session.
        raise IdentityInvalid("token carries no expiry")
    try:
        return int(float(raw))
    except ValueError as exc:
        raise IdentityInvalid("token expiry is not a number") from exc
