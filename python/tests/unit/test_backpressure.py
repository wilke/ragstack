"""Unit tests for Qdrant upsert backpressure (#141): QdrantVectorStore.
collection_health parsing and the BackpressuredVectorStore decorator (gate on
green, pass-through delegation, timeout)."""
import types

import pytest
from qdrant_client.models import CollectionStatus

from ragstack.stores.backpressure import BackpressuredVectorStore, BackpressureTimeout
from ragstack.stores.qdrant import CollectionHealth, QdrantVectorStore

# --- collection_health parsing ---------------------------------------------- #

def _store_with_info(info):
    store = QdrantVectorStore(url="http://x", collection="c", vector_size=3)

    class _FakeClient:
        async def get_collection(self, name):
            assert name == "c"
            return info

    store._client = _FakeClient()
    return store


@pytest.mark.asyncio
async def test_collection_health_enum_status_and_ok_optimizer():
    info = types.SimpleNamespace(
        status=CollectionStatus.YELLOW, optimizer_status="ok", segments_count=5,
    )
    h = await _store_with_info(info).collection_health()
    assert isinstance(h, CollectionHealth)
    assert h.status == "yellow" and h.optimizer_ok is True and h.segments_count == 5


@pytest.mark.asyncio
async def test_collection_health_bare_string_status():
    info = types.SimpleNamespace(status="green", optimizer_status="ok", segments_count=1)
    h = await _store_with_info(info).collection_health()
    assert h.status == "green" and h.optimizer_ok is True


@pytest.mark.asyncio
async def test_collection_health_optimizer_error():
    opt = types.SimpleNamespace(error="disk full")
    info = types.SimpleNamespace(status=CollectionStatus.RED, optimizer_status=opt,
                                 segments_count=9)
    h = await _store_with_info(info).collection_health()
    assert h.status == "red" and h.optimizer_ok is False and h.segments_count == 9


# --- BackpressuredVectorStore ----------------------------------------------- #

class _RecordingInner:
    def __init__(self):
        self.upserts = []
        self.deletes = []
        self.custom = "inner-attr"

    async def upsert(self, chunks):
        self.upserts.append(chunks)

    async def delete(self, doc_id, tenant_id=None):
        self.deletes.append((doc_id, tenant_id))

    async def search(self, *a, **k):
        return ["hit"]

    async def delete_except(self, *a, **k):
        return None

    async def count_tenants(self, tenants):
        return 7


class _Health:
    """Returns the given statuses in order, holding the last one after exhaustion."""

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = 0

    async def __call__(self):
        s = self.statuses[min(self.calls, len(self.statuses) - 1)]
        self.calls += 1
        return types.SimpleNamespace(status=s, segments_count=1, optimizer_ok=s == "green")


@pytest.mark.asyncio
async def test_no_health_source_passes_through():
    inner = _RecordingInner()  # no collection_health attr
    store = BackpressuredVectorStore(inner)
    await store.upsert(["c1"])
    assert inner.upserts == [["c1"]]  # not held


@pytest.mark.asyncio
async def test_upsert_held_until_green():
    inner = _RecordingInner()
    health = _Health(["yellow", "yellow", "green"])
    store = BackpressuredVectorStore(inner, poll_interval=0, health=health)
    await store.upsert(["c1"])
    assert health.calls == 3  # polled twice while yellow, proceeded on green
    assert inner.upserts == [["c1"]]


@pytest.mark.asyncio
async def test_upsert_proceeds_immediately_when_green():
    inner = _RecordingInner()
    health = _Health(["green"])
    store = BackpressuredVectorStore(inner, poll_interval=0, health=health)
    await store.upsert(["c1"])
    assert health.calls == 1 and inner.upserts == [["c1"]]


@pytest.mark.asyncio
async def test_max_wait_times_out():
    inner = _RecordingInner()
    health = _Health(["yellow"])  # never green
    store = BackpressuredVectorStore(inner, poll_interval=0.01, max_wait=0.02, health=health)
    with pytest.raises(BackpressureTimeout):
        await store.upsert(["c1"])
    assert inner.upserts == []  # never upserted
    assert health.calls >= 1  # polled before giving up


@pytest.mark.asyncio
async def test_reads_deletes_pass_through_ungated():
    inner = _RecordingInner()
    health = _Health(["yellow"])  # would block an upsert forever
    store = BackpressuredVectorStore(inner, poll_interval=0, max_wait=0, health=health)
    # delete/search/count are not gated — they must not consult health at all.
    await store.delete("doc-1", tenant_id="t")
    assert inner.deletes == [("doc-1", "t")]
    assert await store.search("q") == ["hit"]
    assert await store.count_tenants(["t"]) == 7
    assert health.calls == 0


@pytest.mark.asyncio
async def test_getattr_delegates_and_uses_inner_health_by_default():
    inner = _RecordingInner()

    async def _ch():
        return types.SimpleNamespace(status="green", segments_count=1)

    inner.collection_health = _ch
    store = BackpressuredVectorStore(inner, poll_interval=0)
    assert store._health is _ch  # picked up inner.collection_health
    assert store.custom == "inner-attr"  # __getattr__ delegates unknown attrs
    await store.upsert(["c1"])
    assert inner.upserts == [["c1"]]


class _HealthPairs:
    """Yields (status, optimizer_ok) pairs in order, holding the last."""

    def __init__(self, pairs):
        self.pairs = list(pairs)
        self.calls = 0

    async def __call__(self):
        status, ok = self.pairs[min(self.calls, len(self.pairs) - 1)]
        self.calls += 1
        return types.SimpleNamespace(status=status, optimizer_ok=ok, segments_count=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", ["grey", "red", "yellow"])
async def test_gate_holds_on_non_green_status(blocked):
    inner = _RecordingInner()
    health = _Health([blocked, "green"])
    store = BackpressuredVectorStore(inner, poll_interval=0, health=health)
    await store.upsert(["c1"])
    assert health.calls == 2  # held on the non-green status, proceeded on green
    assert inner.upserts == [["c1"]]


@pytest.mark.asyncio
async def test_gate_holds_on_optimizer_error_even_when_green():
    """status can read green while the optimizer has failed — hold on that too."""
    inner = _RecordingInner()
    health = _HealthPairs([("green", False), ("green", True)])
    store = BackpressuredVectorStore(inner, poll_interval=0, health=health)
    await store.upsert(["c1"])
    assert health.calls == 2  # held while optimizer_ok=False despite green status
    assert inner.upserts == [["c1"]]


@pytest.mark.asyncio
async def test_upsert_happens_after_the_green_poll():
    """Ordering: the upsert fires strictly after the poll that returned ready —
    not before or independently of it."""
    events = []

    class _LoggingHealth:
        def __init__(self, statuses):
            self.statuses = list(statuses)
            self.calls = 0

        async def __call__(self):
            s = self.statuses[min(self.calls, len(self.statuses) - 1)]
            self.calls += 1
            events.append(("poll", s))
            return types.SimpleNamespace(status=s, optimizer_ok=s == "green", segments_count=1)

    class _LoggingInner:
        async def upsert(self, chunks):
            events.append(("upsert", chunks))

    store = BackpressuredVectorStore(_LoggingInner(), poll_interval=0,
                                     health=_LoggingHealth(["yellow", "green"]))
    await store.upsert(["c1"])
    assert events == [("poll", "yellow"), ("poll", "green"), ("upsert", ["c1"])]


@pytest.mark.asyncio
async def test_max_in_flight_bounds_concurrent_upserts():
    """max_in_flight caps concurrent inner.upsert calls (the #141 in-flight window)."""
    import asyncio

    class _ConcurrencyInner:
        def __init__(self):
            self.current = 0
            self.peak = 0

        async def upsert(self, chunks):
            self.current += 1
            self.peak = max(self.peak, self.current)
            await asyncio.sleep(0)  # yield so overlap can happen
            await asyncio.sleep(0)
            self.current -= 1

    async def _green():
        return types.SimpleNamespace(status="green", optimizer_ok=True, segments_count=1)

    inner = _ConcurrencyInner()
    store = BackpressuredVectorStore(inner, poll_interval=0, max_in_flight=2, health=_green)
    await asyncio.gather(*(store.upsert([f"c{i}"]) for i in range(6)))
    assert inner.peak <= 2  # never more than 2 upserts in flight at once


@pytest.mark.asyncio
async def test_no_max_in_flight_allows_full_overlap():
    """Without max_in_flight the decorator does not bound concurrency (baseline
    that proves the previous test's cap is real, not an artifact of scheduling)."""
    import asyncio

    class _ConcurrencyInner:
        def __init__(self):
            self.current = 0
            self.peak = 0

        async def upsert(self, chunks):
            self.current += 1
            self.peak = max(self.peak, self.current)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.current -= 1

    async def _green():
        return types.SimpleNamespace(status="green", optimizer_ok=True, segments_count=1)

    inner = _ConcurrencyInner()
    store = BackpressuredVectorStore(inner, poll_interval=0, health=_green)  # no cap
    await asyncio.gather(*(store.upsert([f"c{i}"]) for i in range(6)))
    assert inner.peak >= 3  # unbounded overlap
