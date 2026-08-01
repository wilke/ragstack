"""Build the configured :class:`IdentityProvider` (or none at all).

``IDENTITY_PROVIDER`` defaults to ``none``, and ``none`` means the
``Authorization`` header is not an authentication input at all: the API behaves
exactly as it did before this module existed, byte for byte. Turning the flag on
is the only thing that makes a bearer credential meaningful.
"""
from __future__ import annotations

import logging

from ragstack.config import Settings, settings
from ragstack.identity.base import IdentityProvider
from ragstack.identity.bvbrc import BvbrcSignedToken
from ragstack.identity.cache import CachingIdentityProvider
from ragstack.identity.oidc import GOOGLE_ISSUERS, OidcIdentityProvider

logger = logging.getLogger(__name__)

PROVIDER_NONE = "none"
PROVIDER_BVBRC = "bvbrc"
PROVIDER_OIDC = "oidc"
VALID_PROVIDERS = frozenset({PROVIDER_NONE, PROVIDER_BVBRC, PROVIDER_OIDC})

_provider: IdentityProvider | None = None
_built_for: str | None = None


def build_identity_provider(cfg: Settings = settings) -> IdentityProvider | None:
    """Construct the provider named by ``cfg.identity_provider``, or ``None``."""
    name = cfg.identity_provider.strip().lower()
    if name in ("", PROVIDER_NONE):
        return None
    if name == PROVIDER_BVBRC:
        inner: IdentityProvider = BvbrcSignedToken(
            allowlist=tuple(cfg.identity_issuer_allowlist),
            timeout=cfg.identity_http_timeout_seconds,
            key_ttl=float(cfg.identity_key_cache_ttl_seconds),
        )
    elif name == PROVIDER_OIDC:
        inner = OidcIdentityProvider(
            issuer=cfg.identity_oidc_issuer,
            client_ids=tuple(cfg.identity_oidc_client_ids),
            issuer_label=cfg.identity_oidc_issuer_label,
            allowed_issuers=accepted_issuers(cfg),
            timeout=cfg.identity_http_timeout_seconds,
            leeway=cfg.identity_clock_skew_seconds,
        )
    else:
        raise RuntimeError(
            f"identity_provider={name!r} is not one of {sorted(VALID_PROVIDERS)}"
        )
    return CachingIdentityProvider(inner, ttl=float(cfg.identity_cache_ttl_seconds))


def accepted_issuers(cfg: Settings = settings) -> tuple[str, ...]:
    """The exact ``iss`` strings accepted for the configured OIDC issuer.

    Enumerated, never prefix-matched. Google is the reason this is a set rather
    than a scalar: it mints ID tokens with ``iss`` spelled either
    ``accounts.google.com`` or ``https://accounts.google.com``, and a deployment
    that pins only one of them rejects half its users.
    """
    if cfg.identity_oidc_allowed_issuers:
        return tuple(cfg.identity_oidc_allowed_issuers)
    if cfg.identity_oidc_issuer in GOOGLE_ISSUERS:
        return GOOGLE_ISSUERS
    return (cfg.identity_oidc_issuer,)


def get_identity_provider() -> IdentityProvider | None:
    """The process-wide provider, built on first use.

    Rebuilt automatically when ``identity_provider`` changes (tests monkeypatch
    it); call :func:`reset_identity_provider` after changing any *other* identity
    setting.
    """
    global _provider, _built_for
    name = settings.identity_provider.strip().lower()
    if _built_for != name:
        _provider = build_identity_provider(settings)
        _built_for = name
    return _provider


def set_identity_provider(provider: IdentityProvider | None) -> None:
    """Install ``provider`` explicitly (tests, and callers that build their own)."""
    global _provider, _built_for
    _provider = provider
    _built_for = settings.identity_provider.strip().lower()


def reset_identity_provider() -> None:
    """Drop the cached provider so the next use rebuilds it from settings."""
    global _provider, _built_for
    _provider = None
    _built_for = None


def validate_identity_settings() -> None:
    """Fail fast at startup on a misconfigured identity layer.

    Every failure here is one that would otherwise be silent and permanent: an
    unknown provider name (nobody can log in), an empty issuer allowlist (BV-BRC
    tokens all rejected), or — the dangerous one — an OIDC provider with no client
    id, which would accept ID tokens minted for any other application on the IdP.
    """
    name = settings.identity_provider.strip().lower()
    if name not in VALID_PROVIDERS:
        raise RuntimeError(
            f"identity_provider={settings.identity_provider!r} is not one of "
            f"{sorted(VALID_PROVIDERS)}"
        )
    if name == PROVIDER_NONE:
        return
    if settings.identity_cache_ttl_seconds > 300:
        raise RuntimeError(
            "identity_cache_ttl_seconds must be <= 300: the cache TTL is the "
            "revocation lag, and BV-BRC tokens cannot be revoked at all"
        )
    if name == PROVIDER_BVBRC and not settings.identity_issuer_allowlist:
        raise RuntimeError(
            "identity_provider=bvbrc requires a non-empty IDENTITY_ISSUER_ALLOWLIST; "
            "an unpinned SigningSubject lets anyone forge any username"
        )
    if name == PROVIDER_OIDC:
        if not settings.identity_oidc_issuer:
            raise RuntimeError("identity_provider=oidc requires IDENTITY_OIDC_ISSUER")
        if not settings.identity_oidc_client_ids:
            raise RuntimeError(
                "identity_provider=oidc requires IDENTITY_OIDC_CLIENT_IDS; an "
                "unpinned `aud` accepts ID tokens minted for any other application"
            )
    # Constructing it here surfaces the remaining argument-level guards at boot.
    build_identity_provider(settings)
    logger.info("identity provider %s enabled", name)
