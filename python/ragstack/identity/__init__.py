"""Identity providers — who is this caller?

One interface, several implementations (spec §5.0): ``BvbrcSignedToken`` verifies
a BV-BRC signed token offline against a pinned issuer's public key;
``OidcIdentityProvider`` verifies an OIDC ID token against the issuer's JWKS.
Both answer the same question and return the same :class:`Identity`.

Identity (who are you?) is deliberately separate from authorization (may you read
this?) and from storage (give me the bytes) — that separation is what lets a
non-BV-BRC deployment exist at all.
"""
from ragstack.identity.base import (
    Identity,
    IdentityError,
    IdentityInvalid,
    IdentityProvider,
    IdentityUnavailable,
)
from ragstack.identity.bvbrc import DEFAULT_SIGNING_SUBJECTS, BvbrcSignedToken
from ragstack.identity.cache import CachingIdentityProvider
from ragstack.identity.factory import (
    VALID_PROVIDERS,
    accepted_issuers,
    build_identity_provider,
    get_identity_provider,
    reset_identity_provider,
    set_identity_provider,
    validate_identity_settings,
)
from ragstack.identity.oidc import GOOGLE_ISSUER, GOOGLE_ISSUERS, OidcIdentityProvider

__all__ = [
    "DEFAULT_SIGNING_SUBJECTS",
    "GOOGLE_ISSUER",
    "GOOGLE_ISSUERS",
    "VALID_PROVIDERS",
    "BvbrcSignedToken",
    "CachingIdentityProvider",
    "Identity",
    "IdentityError",
    "IdentityInvalid",
    "IdentityProvider",
    "IdentityUnavailable",
    "OidcIdentityProvider",
    "accepted_issuers",
    "build_identity_provider",
    "get_identity_provider",
    "reset_identity_provider",
    "set_identity_provider",
    "validate_identity_settings",
]
