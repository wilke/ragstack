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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class TenantQuota:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._sems: dict[str, asyncio.Semaphore] = {}

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
        async with sem:
            yield
