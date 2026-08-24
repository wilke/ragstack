"""Perf budget for the rate limiter's hot path (issue #87 / #355): the spec
requires < 0.05 ms p95 per call over 10k calls, since TokenBucketLimiter.allow
is pure dict + monotonic clock — no I/O, no lock, nothing that should ever cost
more than a few dict operations and a float comparison.
"""
import pytest

from ragstack.ratelimit import TokenBucketLimiter
from tests.perf._budget import assert_budget


@pytest.mark.perf
def test_token_bucket_allow_p95_budget():
    limiter = TokenBucketLimiter(1_000_000_000)  # never actually denies; steady state
    tenant = "steady-tenant"

    def _call_once() -> None:
        limiter.allow(tenant)

    assert_budget("token_bucket_allow_steady_state", _call_once, budget_s=0.00005, n=10_000)


@pytest.mark.perf
def test_token_bucket_allow_p95_budget_many_principals():
    """Same budget, but rotating through many distinct principals — exercises
    the OrderedDict move_to_end / bucket-creation path, not just a warm single
    bucket, since a production server sees many tenants."""
    limiter = TokenBucketLimiter(1_000_000_000, max_principals=20_000)
    principals = [f"tenant-{i}" for i in range(10_000)]
    it = iter(principals)

    def _call_once() -> None:
        limiter.allow(next(it))

    assert_budget("token_bucket_allow_many_principals", _call_once, budget_s=0.00005, n=10_000)
