"""The short-TTL identity cache: what it must and must not remember."""
from __future__ import annotations

import time

import pytest

from ragstack.identity import (
    CachingIdentityProvider,
    Identity,
    IdentityInvalid,
    IdentityUnavailable,
)


class _Counting:
    """An IdentityProvider double that counts calls and can be made to fail."""

    def __init__(self, identity: Identity | None = None, error: Exception | None = None):
        self.identity = identity
        self.error = error
        self.calls = 0

    async def authenticate(self, credential: str) -> Identity:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert self.identity is not None
        return self.identity


def make_identity(**kwargs) -> Identity:
    defaults = {
        "subject": "alice",
        "issuer": "bvbrc",
        "token_id": "tok-1",
        "expires_at": int(time.time()) + 3600,
    }
    return Identity(**{**defaults, **kwargs})


async def test_repeated_authentication_hits_the_provider_once():
    inner = _Counting(make_identity())
    cache = CachingIdentityProvider(inner, ttl=300)
    for _ in range(4):
        assert (await cache.authenticate("cred")).subject == "alice"
    assert inner.calls == 1


async def test_different_credentials_do_not_share_an_entry():
    inner = _Counting(make_identity())
    cache = CachingIdentityProvider(inner, ttl=300)
    await cache.authenticate("cred-a")
    await cache.authenticate("cred-b")
    assert inner.calls == 2


async def test_a_forged_credential_cannot_poison_a_victims_entry():
    """The key is a digest of the whole credential, not a token_id parsed out of
    it: a token_id read *before* verification is attacker-chosen, so a forgery
    bearing the victim's tokenid would otherwise land on the victim's entry."""
    inner = _Counting(make_identity(subject="victim", token_id="shared-token-id"))
    cache = CachingIdentityProvider(inner, ttl=300)
    await cache.authenticate("victims-real-credential")

    inner.error = IdentityInvalid("forged")
    with pytest.raises(IdentityInvalid):
        await cache.authenticate("forgery-claiming-tokenid=shared-token-id")
    # The victim's entry is untouched and still served.
    assert (await cache.authenticate("victims-real-credential")).subject == "victim"


async def test_expired_identities_are_never_served_from_the_cache():
    inner = _Counting(make_identity(expires_at=int(time.time()) + 1))
    cache = CachingIdentityProvider(inner, ttl=300)
    await cache.authenticate("cred")

    # The credential expires while cached; the entry must not outlive it, even
    # though the TTL has not elapsed.
    inner.identity = make_identity(expires_at=int(time.time()) - 1)
    cache._entries["dummy"] = (time.time() + 300, inner.identity)
    with pytest.raises(IdentityInvalid, match="expired"):
        await cache.authenticate("newly-expired")


async def test_already_expired_identity_is_refused_and_not_cached():
    inner = _Counting(make_identity(expires_at=int(time.time()) - 5))
    cache = CachingIdentityProvider(inner, ttl=300)
    with pytest.raises(IdentityInvalid):
        await cache.authenticate("cred")
    assert cache._entries == {}


async def test_ttl_of_zero_disables_caching():
    inner = _Counting(make_identity())
    cache = CachingIdentityProvider(inner, ttl=0)
    await cache.authenticate("cred")
    await cache.authenticate("cred")
    assert inner.calls == 2


async def test_entry_never_outlives_the_credential():
    # TTL 300 s but the token expires in 5 s → the entry expires in 5 s.
    expires = int(time.time()) + 5
    cache = CachingIdentityProvider(_Counting(make_identity(expires_at=expires)), ttl=300)
    await cache.authenticate("cred")
    good_until, _ = next(iter(cache._entries.values()))
    assert good_until == pytest.approx(float(expires), abs=1)


async def test_failures_are_not_cached():
    for error in (IdentityInvalid("bad"), IdentityUnavailable("down")):
        inner = _Counting(error=error)
        cache = CachingIdentityProvider(inner, ttl=300)
        for _ in range(3):
            with pytest.raises(type(error)):
                await cache.authenticate("cred")
        assert inner.calls == 3
        assert cache._entries == {}


async def test_cache_is_bounded_and_evicts_least_recently_used():
    inner = _Counting(make_identity())
    cache = CachingIdentityProvider(inner, ttl=300, maxsize=3)
    for i in range(3):
        await cache.authenticate(f"cred-{i}")
    await cache.authenticate("cred-0")  # refresh recency of the oldest
    await cache.authenticate("cred-3")  # evicts cred-1

    assert len(cache._entries) == 3
    calls = inner.calls
    await cache.authenticate("cred-0")
    assert inner.calls == calls  # still cached
    await cache.authenticate("cred-1")
    assert inner.calls == calls + 1  # was evicted
