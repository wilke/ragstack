"""Per-principal request-RATE limiting (ragstack.ratelimit.TokenBucketLimiter).

Companion to tests/unit/test_quota.py — same LRU-bounded-map discipline as
TenantQuota, translated from "concurrency in progress" to "budget not yet
recovered" (see the module docstring). A ``FakeClock`` gives these tests
control over refill math without any real sleeping.
"""
from __future__ import annotations

from ragstack.ratelimit import TokenBucketLimiter


class FakeClock:
    """A controllable ``time.monotonic``-shaped clock for deterministic
    refill-math tests: starts at 0.0, only moves when ``advance`` is called."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def advance(self, seconds: float) -> None:
        self._now += seconds

    def __call__(self) -> float:
        return self._now


# --- disabled / basic admission -------------------------------------------- #


def test_disabled_limiter_never_bounds():
    limiter = TokenBucketLimiter(0)
    for _ in range(1000):
        allowed, retry_after = limiter.allow("t")
        assert allowed is True
        assert retry_after == 0.0
    # A disabled limiter never even tracks a bucket — nothing to evict, nothing
    # to leak.
    assert limiter._buckets == {}


def test_bucket_starts_full_and_admits_up_to_capacity():
    limiter = TokenBucketLimiter(3, clock=FakeClock())
    for _ in range(3):
        allowed, retry_after = limiter.allow("t")
        assert allowed is True
        assert retry_after == 0.0
    allowed, retry_after = limiter.allow("t")
    assert allowed is False
    assert retry_after > 0.0


def test_denied_call_does_not_consume_a_token():
    """A denied request must not further drain the bucket — otherwise a flood
    of rejected calls would keep pushing the recovery time further out."""
    clock = FakeClock()
    limiter = TokenBucketLimiter(1, clock=clock)
    assert limiter.allow("t") == (True, 0.0)
    allowed1, retry1 = limiter.allow("t")
    allowed2, retry2 = limiter.allow("t")
    assert allowed1 is False and allowed2 is False
    assert retry1 == retry2  # no time passed between the two denials


# --- refill math ------------------------------------------------------------ #


def test_bucket_refills_continuously_and_admits_again():
    clock = FakeClock()
    rate = 10  # 10/hour → one token every 360s
    limiter = TokenBucketLimiter(rate, clock=clock)
    for _ in range(rate):
        assert limiter.allow("t") == (True, 0.0)
    allowed, _ = limiter.allow("t")
    assert allowed is False

    clock.advance(360.0)  # exactly one token's worth of time
    allowed, retry_after = limiter.allow("t")
    assert allowed is True
    assert retry_after == 0.0


def test_refill_never_exceeds_capacity():
    clock = FakeClock()
    limiter = TokenBucketLimiter(5, clock=clock)
    limiter.allow("t")  # tokens: 5 -> 4
    clock.advance(10 * 3600.0)  # far more than enough to refill fully
    for _ in range(5):
        assert limiter.allow("t") == (True, 0.0)
    # Capacity is 5: a 6th call right after must still be denied, not admitted
    # on some overflowed token count.
    allowed, _ = limiter.allow("t")
    assert allowed is False


def test_retry_after_matches_the_missing_token():
    clock = FakeClock()
    rate = 4  # refill_per_second = 4/3600
    limiter = TokenBucketLimiter(rate, clock=clock)
    for _ in range(rate):
        limiter.allow("t")
    allowed, retry_after = limiter.allow("t")
    assert allowed is False
    expected = 1.0 / (rate / 3600.0)
    assert retry_after == expected


# --- independence per principal --------------------------------------------- #


def test_bucket_is_independent_per_principal():
    clock = FakeClock()
    limiter = TokenBucketLimiter(1, clock=clock)
    assert limiter.allow("a") == (True, 0.0)
    allowed_a, _ = limiter.allow("a")
    assert allowed_a is False
    # "b" has its own full bucket — unaffected by "a" being drained.
    assert limiter.allow("b") == (True, 0.0)


# --- LRU eviction (mirrors quota.py's §5.0 discipline) ---------------------- #


def test_bucket_map_is_bounded_when_principals_fully_recover_between_calls():
    """Each principal calls once, then enough time passes to fully refill their
    bucket before the next principal is admitted — every bucket is "idle" by
    the time it's scanned for eviction, so the map stays at the ceiling."""
    clock = FakeClock()
    rate = 100  # a single call debits 1 token; refill_per_second = 100/3600
    recover_seconds = 3600.0 / rate + 1.0  # time to fully refill 1 token, +margin
    limiter = TokenBucketLimiter(rate, max_principals=8, clock=clock)
    for i in range(50):
        clock.advance(recover_seconds)
        limiter.allow(f"p-{i}")
    assert len(limiter._buckets) <= 8


def test_eviction_is_least_recently_used():
    clock = FakeClock()
    rate = 100
    recover_seconds = 3600.0 / rate + 1.0
    limiter = TokenBucketLimiter(rate, max_principals=2, clock=clock)
    limiter.allow("a")
    clock.advance(recover_seconds)
    limiter.allow("b")
    clock.advance(recover_seconds)
    limiter.allow("a")  # refresh a's recency
    clock.advance(recover_seconds)
    limiter.allow("c")  # pushes past the ceiling → evicts b (LRU, both idle)
    assert set(limiter._buckets) == {"a", "c"}


def test_a_drawn_down_bucket_is_never_evicted():
    """The trap TenantQuota documents, translated: evicting a bucket that has
    NOT fully refilled would hand its principal a fresh, full bucket for free —
    exactly the bypass "live entries are never evicted" guards against.

    "busy" drains its bucket completely (needs a full simulated hour to
    recover); a flood of other principals only ever needs ~1 second each to
    recover their own single-token debit. Advancing the clock by just over 1s
    per flood call keeps the flood buckets evictable while never coming close
    to the hour "busy" needs — so "busy" must survive the whole flood.
    """
    clock = FakeClock()
    rate = 100  # 1 token debit needs 3600/100 = 36s to recover
    limiter = TokenBucketLimiter(rate, max_principals=3, clock=clock)

    for _ in range(rate):  # fully drain "busy": tokens -> 0, needs 3600s to refill
        limiter.allow("busy")
    assert limiter._buckets["busy"].tokens == 0.0

    for i in range(30):
        clock.advance(40.0)  # > 36s: previous flood buckets fully recover
        limiter.allow(f"other-{i}")
        # Total elapsed after 30 iters is 1200s — nowhere near the 3600s "busy" needs.

    assert "busy" in limiter._buckets  # never evicted despite sustained pressure
    assert limiter._buckets["busy"].tokens < limiter._capacity  # still genuinely live
    assert len(limiter._buckets) <= 3  # ceiling holds: "busy" occupies one slot
