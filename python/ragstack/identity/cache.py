"""A short-TTL, bounded cache in front of an :class:`IdentityProvider`.

Spec §5.0: cache the *result* of authentication for at most 300 s, bounded LRU,
and check local expiry **every request, uncached**, so an expired credential can
never be served out of the cache.

Two properties are deliberate:

- **Only successes are cached.** Caching a failure would let one transient outage
  turn into minutes of 401s, and caching an :class:`IdentityUnavailable` would be
  caching "we don't know".
- **The key is derived from the credential itself**, not from a ``token_id``
  parsed out of it. A ``token_id`` read before verification is attacker-chosen: a
  forged credential carrying a victim's ``tokenid`` would land on the victim's
  entry and poison it. Hashing the whole credential makes a forgery land on its
  own key, where it fails on its own merits.

The cache is per process, so N workers means N caches and N× the upstream call
volume; revocation lag equals the TTL. BV-BRC tokens cannot be revoked before
their expiry at all without an introspection endpoint, which is the other reason
the TTL is small rather than merely bounded.
"""
from __future__ import annotations

import hashlib
import time
from collections import OrderedDict

from ragstack.identity.base import Identity, IdentityInvalid, IdentityProvider


class CachingIdentityProvider:
    """Wrap ``inner``, memoizing successful authentications for ``ttl`` seconds."""

    def __init__(
        self,
        inner: IdentityProvider,
        *,
        ttl: float = 300.0,
        maxsize: int = 10_000,
    ) -> None:
        self._inner = inner
        self._ttl = max(float(ttl), 0.0)
        self._maxsize = max(int(maxsize), 1)
        self._entries: OrderedDict[str, tuple[float, Identity]] = OrderedDict()

    async def authenticate(self, credential: str) -> Identity:
        if self._ttl <= 0:
            return await self._inner.authenticate(credential)

        key = hashlib.sha256(credential.encode("utf-8")).hexdigest()
        now = time.time()
        entry = self._entries.get(key)
        if entry is not None:
            good_until, identity = entry
            if good_until > now and not _expired(identity, now):
                self._entries.move_to_end(key)
                return identity
            del self._entries[key]

        identity = await self._inner.authenticate(credential)
        if _expired(identity, time.time()):
            # Belt and braces: never cache — or return — something already dead.
            raise IdentityInvalid("credential expired")
        self._store(key, identity)
        return identity

    def clear(self) -> None:
        self._entries.clear()

    def _store(self, key: str, identity: Identity) -> None:
        good_until = time.time() + self._ttl
        if identity.expires_at is not None:
            good_until = min(good_until, float(identity.expires_at))
        self._entries[key] = (good_until, identity)
        self._entries.move_to_end(key)
        while len(self._entries) > self._maxsize:
            self._entries.popitem(last=False)

    async def aclose(self) -> None:
        self.clear()
        aclose = getattr(self._inner, "aclose", None)
        if aclose is not None:
            await aclose()


def _expired(identity: Identity, now: float) -> bool:
    return identity.expires_at is not None and identity.expires_at <= now
