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
