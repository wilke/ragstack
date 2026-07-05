"""QdrantVectorStore batches upserts so one request never carries an oversized
payload (the #144 A/B benchmark showed a single large-shard upsert raises
ResponseHandlingException), and pipelines the batches when upsert_concurrency > 1."""
import asyncio

import pytest

from ragstack.models import Chunk
from ragstack.stores.qdrant import QdrantVectorStore


class _FakeClient:
    """Records each upsert's point count and tracks peak concurrency."""

    def __init__(self):
        self.batch_sizes = []
        self.all_ids = []
        self._inflight = 0
        self.peak_inflight = 0

    async def upsert(self, collection_name, points):
        self._inflight += 1
        self.peak_inflight = max(self.peak_inflight, self._inflight)
        await asyncio.sleep(0)  # yield so concurrent batches can overlap
        self.batch_sizes.append(len(points))
        self.all_ids.extend(p.id for p in points)
        await asyncio.sleep(0)
        self._inflight -= 1


def _store(**kw):
    s = QdrantVectorStore(url="http://x", collection="c", vector_size=3, **kw)
    s._client = _FakeClient()
    return s


def _chunks(n):
    return [Chunk(id=f"c{i}", doc_id="d", content="x", embedding=[1.0, 2.0, 3.0])
            for i in range(n)]


@pytest.mark.asyncio
async def test_upsert_splits_into_batches():
    s = _store(upsert_batch_size=256, upsert_concurrency=1)
    await s.upsert(_chunks(600))
    assert s._client.batch_sizes == [256, 256, 88]     # no single 600-point call
    assert len(s._client.all_ids) == 600               # nothing dropped


@pytest.mark.asyncio
async def test_small_upsert_is_one_batch():
    s = _store(upsert_batch_size=256)
    await s.upsert(_chunks(10))
    assert s._client.batch_sizes == [10]


@pytest.mark.asyncio
async def test_empty_upsert_makes_no_call():
    s = _store()
    await s.upsert([])
    assert s._client.batch_sizes == []


@pytest.mark.asyncio
async def test_concurrency_bounds_inflight_batches():
    s = _store(upsert_batch_size=100, upsert_concurrency=3)
    await s.upsert(_chunks(1000))               # 10 batches
    assert sum(s._client.batch_sizes) == 1000
    assert len(s._client.batch_sizes) == 10
    assert s._client.peak_inflight <= 3          # semaphore-bounded


@pytest.mark.asyncio
async def test_serial_default_never_overlaps():
    s = _store(upsert_batch_size=100, upsert_concurrency=1)
    await s.upsert(_chunks(500))
    assert s._client.peak_inflight == 1          # serial by default
