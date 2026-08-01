"""Per-tenant concurrency quota."""
import asyncio

import pytest

from ragstack.ingestion.backends import LocalAsyncIORunner
from ragstack.ingestion.manifest import Manifest, WorkItem
from ragstack.ingestion.sharded import ShardedIngestor
from ragstack.quota import TenantQuota


class _ConcurrencyProbe:
    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self._lock = asyncio.Lock()

    async def run(self, hold: float = 0.02) -> None:
        async with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        await asyncio.sleep(hold)
        async with self._lock:
            self.active -= 1


@pytest.mark.asyncio
async def test_disabled_quota_does_not_bound():
    quota = TenantQuota(0)
    probe = _ConcurrencyProbe()

    async def work():
        async with quota.slot("t"):
            await probe.run()

    await asyncio.gather(*(work() for _ in range(10)))
    assert probe.peak == 10  # unlimited


@pytest.mark.asyncio
async def test_quota_bounds_a_single_tenant():
    quota = TenantQuota(2)
    probe = _ConcurrencyProbe()

    async def work():
        async with quota.slot("t"):
            await probe.run()

    await asyncio.gather(*(work() for _ in range(8)))
    assert probe.peak <= 2


@pytest.mark.asyncio
async def test_quota_is_independent_per_tenant():
    quota = TenantQuota(1)
    probe = _ConcurrencyProbe()

    async def work(tenant: str):
        async with quota.slot(tenant):
            await probe.run()

    # Each tenant is capped at 1, but they don't block each other → 2 in flight.
    await asyncio.gather(work("a"), work("a"), work("b"), work("b"))
    assert probe.peak == 2


@pytest.mark.asyncio
async def test_sharded_ingestor_enforces_quota():
    probe = _ConcurrencyProbe()

    class _SlowPipe:
        async def ingest(self, source: str, tenant_id: str = "default") -> list[str]:
            await probe.run()
            return ["c"]

    # Runner would allow 8 concurrent, but the tenant quota of 2 must dominate.
    ingestor = ShardedIngestor(
        _SlowPipe(),
        LocalAsyncIORunner(max_concurrency=8),
        shard_size=1,
        quota=TenantQuota(2),
    )
    manifest = Manifest(items=[WorkItem(item_id=str(i), source=f"/s/{i}") for i in range(8)])
    results = await ingestor.ingest_manifest(manifest, tenant_id="t")
    assert len(results) == 8
    assert probe.peak <= 2


# --- LRU eviction (spec §5.0: tenants now derive from usernames) ------------- #


@pytest.mark.asyncio
async def test_semaphore_map_is_bounded():
    """A tenant is f"{issuer}:{subject}" once the identity layer is on — one per
    authenticated end user. An unbounded map would be a memory leak keyed by
    anyone who can log in."""
    quota = TenantQuota(2, max_tenants=8)
    for i in range(100):
        async with quota.slot(f"bvbrc:user-{i}"):
            pass
    assert len(quota._sems) <= 8
    assert quota._inflight == {}


@pytest.mark.asyncio
async def test_eviction_is_least_recently_used():
    quota = TenantQuota(2, max_tenants=2)
    for tenant in ("a", "b"):
        async with quota.slot(tenant):
            pass
    async with quota.slot("a"):  # refresh a's recency
        pass
    async with quota.slot("c"):  # pushes past the ceiling → evicts b
        pass
    assert set(quota._sems) == {"a", "c"}


@pytest.mark.asyncio
async def test_an_in_flight_tenant_is_never_evicted():
    """Evicting a live semaphore would let the next caller mint a fresh one and
    quietly double that tenant's concurrency."""
    quota = TenantQuota(1, max_tenants=1)
    started = asyncio.Event()
    release = asyncio.Event()

    async def holder():
        async with quota.slot("busy"):
            started.set()
            await release.wait()

    task = asyncio.create_task(holder())
    await started.wait()
    for i in range(20):
        async with quota.slot(f"other-{i}"):
            pass
    assert "busy" in quota._sems  # still held, so still present

    release.set()
    await task
    assert quota._inflight == {}
