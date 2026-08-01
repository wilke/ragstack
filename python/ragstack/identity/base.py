"""Identity: who is this caller?

The one invariant every implementation shares: **the subject is authoritative and
comes from the provider** — either by introspecting the credential at the auth
service, or by verifying a signed claim offline against the issuer's published
keys. Both are authoritative. What is forbidden, in all cases, is reading an
identity out of an *unverified* credential: a token is an attacker-controlled
string until its signature checks out.

Failure modes are normative, because the difference is a security property:

- :class:`IdentityInvalid` — the credential is malformed, expired, or does not
  verify. Maps to **401**. The caller is not who they claim to be.
- :class:`IdentityUnavailable` — we could not *decide* (key server unreachable,
  timeout, 5xx). Maps to **503**, never 401 and **never an allow**. Degrading an
  undecidable auth check into a pass is the fail-open class this module exists to
  prevent.

See ``docs/libraries-spec.md`` §1 (interfaces) and §5.0 (identity).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


class IdentityError(Exception):
    """Base for identity failures. Never raised directly — the subclass carries
    the HTTP mapping, and code that catches this base loses that distinction."""


class IdentityInvalid(IdentityError):
    """The credential is malformed, expired, or fails verification → 401."""


class IdentityUnavailable(IdentityError):
    """The identity service could not be reached / did not answer → 503.

    Distinct from :class:`IdentityInvalid` on purpose: it is not a statement about
    the caller, it is a statement about us. It must never be converted into an
    allow, and never into a 401 (which would tell a legitimate caller their
    perfectly good token is bad, and would hide our own outage)."""


@dataclass(frozen=True)
class Identity:
    """An authenticated caller, as asserted by an :class:`IdentityProvider`."""

    #: AUTHORITATIVE. Comes from the provider (a verified claim or an
    #: introspection response). NEVER parsed out of an unverified credential.
    subject: str
    #: Short, stable label for the issuing authority: ``"bvbrc"`` | ``"google"`` |
    #: … . Combined with ``subject`` it forms the tenant, so a BV-BRC ``alice``
    #: and a Google ``alice`` are different principals.
    issuer: str
    #: Stable per credential; the authorization cache key (spec §5.1). Derived
    #: only from verified material.
    token_id: str
    #: Unix seconds, or ``None`` when the credential carries no expiry.
    expires_at: int | None
    #: Future-proofing for scoped credentials; unused in v1.
    scopes: frozenset[str] = field(default_factory=frozenset)


@runtime_checkable
class IdentityProvider(Protocol):
    """Authenticate a bearer credential and return the caller's :class:`Identity`.

    Raises :class:`IdentityInvalid` (→ 401) or :class:`IdentityUnavailable`
    (→ 503). Returning ``None`` or a bare ``bool`` for an authentication-relevant
    outcome is forbidden — the two failure modes are not interchangeable.
    """

    async def authenticate(self, credential: str) -> Identity: ...
