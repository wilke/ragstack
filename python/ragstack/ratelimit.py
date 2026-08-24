"""Per-principal request-RATE limiting (token bucket).

Companion to :mod:`ragstack.quota`: that module bounds *concurrency* per tenant
(how many requests a tenant may have in flight at once, for fairness on the
shared embedding fleet); this one bounds *rate* (requests per hour) per
principal, for write endpoints where an unbounded caller — buggy or malicious
— could grief a shared resource by sheer request volume rather than by holding
slots open (ingest, collection creation, share grants).

Same LRU-bounded-map discipline as :class:`ragstack.quota.TenantQuota`, and the
same trap that module documents, translated from "concurrency in progress" to
"budget not yet recovered": a bucket that has been drawn down and has not
fully refilled is LIVE and must never be evicted, or the principal it belongs
to gets a fresh, full bucket for free just by waiting for other principals to
churn the map. A bucket sitting at full capacity is indistinguishable from one
that was never created, so eviction may only drop those — exactly the
"in-flight entries are never evicted" rule, with "in-flight" redefined as
"tokens < capacity". As with ``TenantQuota``, this means the map can exceed its
ceiling when every tracked principal is genuinely mid-budget; eviction is a
leak guard, not a hard cap the hot path depends on.

**Per-process, in-memory — same caveat as** ``TenantQuota``. Each API
worker/replica enforces its own bucket independently: N processes behind a
load balancer give an effective ceiling of N times the configured rate, not
the configured rate itself. There is no cross-process or cross-replica
coordination here (no shared store, no Redis) — that is out of scope for this
module (distributed rate limiting is a separate, larger piece of work) and
must be accounted for in capacity planning, not assumed away.

``rate_per_hour <= 0`` disables the limiter (unlimited) — opt-in, same as
``TenantQuota``'s ``limit <= 0``.
"""
from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable

#: Ceiling on the number of per-principal buckets kept alive. An eviction
#: guard against an unbounded memory leak (principal strings can derive from
#: arbitrary authenticated identities — see ``quota.DEFAULT_MAX_TENANTS`` for
#: the same reasoning), not something the hot path is expected to notice.
DEFAULT_MAX_PRINCIPALS = 10_000

_SECONDS_PER_HOUR = 3600.0


class _Bucket:
    """Mutable per-principal bucket state. A plain class (not a dataclass) with
    ``__slots__`` — this is allocated once per tracked principal and read/written
    on every rate-limited request, so the smaller footprint and faster attribute
    access are worth the boilerplate."""

    __slots__ = ("tokens", "updated_at")

    def __init__(self, tokens: float, updated_at: float) -> None:
        self.tokens = tokens
        self.updated_at = updated_at


class TokenBucketLimiter:
    """A token bucket per principal, keyed by an opaque string (the caller
    passes ``principal.tenant`` — see ``api/deps.py::rate_limited``).

    Capacity equals ``rate_per_hour`` (one hour's worth of requests can be
    banked as burst); tokens refill continuously at ``rate_per_hour / 3600``
    per second, so a fully-drained bucket always takes exactly one hour to
    reach full capacity again, whatever the configured rate.
    """

    def __init__(
        self,
        rate_per_hour: int,
        max_principals: int = DEFAULT_MAX_PRINCIPALS,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._rate_per_hour = rate_per_hour
        self._capacity = float(rate_per_hour)
        self._refill_per_second = rate_per_hour / _SECONDS_PER_HOUR
        self._max_principals = max(int(max_principals), 1)
        self._clock = clock
        # One bucket per principal, created lazily, kept in LRU order — same
        # shape as TenantQuota._sems.
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()

    def allow(self, principal: str) -> tuple[bool, float]:
        """Try to spend one token for ``principal``.

        Returns ``(True, 0.0)`` when the request is admitted, or
        ``(False, retry_after_seconds)`` when the bucket is empty — the caller
        (``api/deps.py::rate_limited``) turns the latter into a 429 with a
        ``Retry-After`` header.

        get-or-create-and-refill has no ``await`` between its read and its
        write, so it is atomic under asyncio (single-threaded event loop) with
        no lock needed — the same reasoning ``TenantQuota.slot`` documents.
        """
        if self._rate_per_hour <= 0:
            return True, 0.0
        now = self._clock()
        bucket = self._buckets.get(principal)
        if bucket is None:
            bucket = _Bucket(tokens=self._capacity, updated_at=now)
            self._buckets[principal] = bucket
        else:
            self._refill(bucket, now)
        self._buckets.move_to_end(principal)
        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            self._evict(now)
            return True, 0.0
        missing = 1.0 - bucket.tokens
        retry_after = missing / self._refill_per_second
        self._evict(now)
        return False, retry_after

    def _refill(self, bucket: _Bucket, now: float) -> None:
        elapsed = now - bucket.updated_at
        if elapsed <= 0:
            return
        bucket.tokens = min(self._capacity, bucket.tokens + elapsed * self._refill_per_second)
        bucket.updated_at = now

    def _evict(self, now: float) -> None:
        """Drop least-recently-used FULL buckets down to the ceiling.

        Refills each candidate before checking it — a bucket idle since well
        before ``now`` still carries a stale (drained) ``tokens`` value until
        something looks at it, and skipping the refill here would make an
        actually-recovered bucket look permanently live, defeating eviction
        for anyone who last called an hour ago instead of a moment ago.

        A bucket at full capacity is indistinguishable from a freshly-created
        one — dropping it just means the next call re-creates an identical
        bucket, so it's the only kind eviction may remove. A bucket still
        below capacity encodes real, unexpired rate-limit history; dropping
        that one would hand its principal a fresh, full bucket — the exact
        bypass ``TenantQuota``'s in-flight rule guards against, here
        translated to "budget not yet recovered".
        """
        if len(self._buckets) <= self._max_principals:
            return
        for principal in list(self._buckets):
            if len(self._buckets) <= self._max_principals:
                return
            bucket = self._buckets[principal]
            self._refill(bucket, now)
            if bucket.tokens >= self._capacity:
                del self._buckets[principal]
