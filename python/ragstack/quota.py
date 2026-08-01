"""Per-tenant concurrency quota.

The embedder pool bounds *total* in-flight requests across the fleet; this bounds
how many a *single tenant* can have at once, so one tenant's 500k-doc ingest
can't starve another tenant's queries on the shared embedding GPUs. Enforced at
the admission layer (ingest items and queries, where the tenant is known) rather
than inside the embedder, which stays tenant-agnostic.

``limit <= 0`` disables it (unlimited) — the default, so it's opt-in.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

#: Ceiling on the number of per-tenant semaphores kept alive. Comfortably above
#: any realistic count of *concurrently active* tenants, so eviction is a leak
#: guard rather than something the hot path ever notices.
DEFAULT_MAX_TENANTS = 10_000


class TenantQuota:
    def __init__(self, limit: int, max_tenants: int = DEFAULT_MAX_TENANTS) -> None:
        self._limit = limit
        self._max_tenants = max(int(max_tenants), 1)
        # One Semaphore per tenant, created lazily, in LRU order.
        #
        # This used to be an unbounded dict, safe because tenant strings came from
        # the bounded api_key_tenants map — with a comment saying to add eviction
        # "if tenant ever derives from untrusted/arbitrary input". The identity
        # layer is exactly that: a tenant is now f"{issuer}:{subject}", one per
        # authenticated end user, so an unbounded map would be a slow memory leak
        # keyed by anyone who can log in.
        self._sems: OrderedDict[str, asyncio.Semaphore] = OrderedDict()
        # In-flight holders per tenant. An entry with holders is never evicted:
        # dropping a live semaphore would let the next caller create a fresh one
        # and quietly double that tenant's concurrency.
        self._inflight: dict[str, int] = {}

    @asynccontextmanager
    async def slot(self, tenant: str) -> AsyncIterator[None]:
        """Hold one of the tenant's concurrency slots for the duration."""
        if self._limit <= 0:
            yield
            return
        # get-or-create has no await between lookup and insert, so it's atomic
        # under asyncio — no lock needed.
        sem = self._sems.get(tenant)
        if sem is None:
            sem = asyncio.Semaphore(self._limit)
            self._sems[tenant] = sem
        self._sems.move_to_end(tenant)
        self._inflight[tenant] = self._inflight.get(tenant, 0) + 1
        try:
            async with sem:
                yield
        finally:
            remaining = self._inflight[tenant] - 1
            if remaining:
                self._inflight[tenant] = remaining
            else:
                del self._inflight[tenant]
            self._evict()

    def _evict(self) -> None:
        """Drop least-recently-used idle tenants down to the ceiling."""
        if len(self._sems) <= self._max_tenants:
            return
        for tenant in list(self._sems):
            if len(self._sems) <= self._max_tenants:
                return
            if tenant not in self._inflight:
                del self._sems[tenant]
