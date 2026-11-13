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


# --- #177: batches are bounded by request BYTES as well as by count ----------
#
# Qdrant rejects a request body over service.max_request_size_mb (32 MiB by
# default) and rejects the WHOLE request. A few chunks carrying ~1 MB of
# mis-extracted metadata (a reference list copied into title/citation) used to
# push a 256-point batch over the limit, and every point in it was lost.

from qdrant_client.models import PointsList  # noqa: E402

from ragstack.config import Settings  # noqa: E402
from ragstack.stores.qdrant import (  # noqa: E402
    UPSERT_MAX_BYTES_DEFAULT,
    OversizedPointError,
)

_QDRANT_DEFAULT_LIMIT = 32 * 1024 * 1024


class _SizeLimitedClient(_FakeClient):
    """Rejects a request whose REST body exceeds ``limit``, the way Qdrant does:
    the whole request fails and nothing in it is written. The body is measured
    with the same pydantic serialization qdrant-client's REST path sends."""

    def __init__(self, limit=_QDRANT_DEFAULT_LIMIT):
        super().__init__()
        self.limit = limit
        self.body_bytes = []
        self.rejected = 0

    async def upsert(self, collection_name, points):
        body = PointsList(points=points).model_dump_json(exclude_unset=True)
        size = len(body.encode())
        if size > self.limit:
            self.rejected += 1
            raise RuntimeError(f"400 Payload error: request {size} > {self.limit}")
        self.body_bytes.append(size)
        await super().upsert(collection_name, points)


def _poisoned_chunks(n, poison_every, poison_bytes, dim=3):
    out = []
    for i in range(n):
        meta = {"title": "A normal title", "authors": "Doe J"}
        if i % poison_every == 0:
            meta["citation"] = "r" * poison_bytes   # the mis-extracted ref list
        out.append(Chunk(id=f"c{i}", doc_id="d", content="x" * 1000,
                         embedding=[0.123456789] * dim, metadata=meta))
    return out


@pytest.mark.asyncio
async def test_metadata_heavy_points_no_longer_sink_their_batch():
    """The #177 failure at its real scale: 256 chunks, 40 of them carrying ~1 MB
    of metadata (40 MB total) against Qdrant's default 32 MiB limit. Count-only
    batching sends one 256-point request, which the server rejects whole."""
    s = _store()          # all defaults: batch 256, default byte cap
    s._client = _SizeLimitedClient()
    chunks = _poisoned_chunks(256, poison_every=6, poison_bytes=1_000_000)
    await s.upsert(chunks)
    assert s._client.rejected == 0
    assert len(s._client.all_ids) == 256                 # every point sent
    assert len(set(s._client.all_ids)) == 256            # none twice
    assert max(s._client.body_bytes) <= _QDRANT_DEFAULT_LIMIT
    assert len(s._client.batch_sizes) > 1


@pytest.mark.asyncio
async def test_byte_cap_bounds_every_request_and_drops_nothing():
    """A small configured cap: no request's real body exceeds it."""
    cap = 1024 * 1024
    s = _store(upsert_max_bytes=cap)
    s._client = _SizeLimitedClient(limit=cap)
    await s.upsert(_poisoned_chunks(256, poison_every=50, poison_bytes=300_000))
    assert s._client.rejected == 0
    assert len(s._client.all_ids) == 256
    assert len(set(s._client.all_ids)) == 256
    assert max(s._client.body_bytes) <= cap


@pytest.mark.asyncio
async def test_byte_cap_applies_on_the_concurrent_path_too():
    cap = 1024 * 1024
    s = _store(upsert_max_bytes=cap, upsert_concurrency=4)
    s._client = _SizeLimitedClient(limit=cap)
    await s.upsert(_poisoned_chunks(600, poison_every=40, poison_bytes=300_000))
    assert s._client.rejected == 0
    assert len(s._client.all_ids) == 600
    assert max(s._client.body_bytes) <= cap


@pytest.mark.asyncio
async def test_normal_4096d_data_keeps_the_same_request_count():
    """Ordinary chunks (2 KB content, small metadata, 4096-d vectors) must batch
    exactly as before: the byte cap only bites on outliers. The real body of a
    full 256-point batch also stays under Qdrant's default limit."""
    import random
    rng = random.Random(0)
    chunks = [
        Chunk(id=f"c{i}", doc_id=f"d{i // 40}", content="w" * 2000,
              embedding=[rng.uniform(-0.05, 0.05) for _ in range(4096)],
              metadata={"title": "A normal title", "authors": "Doe J, Roe R",
                        "year": 2021, "source": "pmc"})
        for i in range(600)
    ]
    s = QdrantVectorStore(url="http://x", collection="c", vector_size=4096)
    s._client = _SizeLimitedClient()
    await s.upsert(chunks)
    assert s._client.batch_sizes == [256, 256, 88]
    assert s._client.rejected == 0


@pytest.mark.asyncio
async def test_a_point_over_the_cap_is_sent_alone():
    cap = 64 * 1024
    s = _store(upsert_max_bytes=cap)
    chunks = _chunks(10)
    chunks[4].metadata["citation"] = "r" * (cap * 2)
    await s.upsert(chunks)
    # The giant point travels by itself; its neighbours are not held hostage.
    assert s._client.batch_sizes == [4, 1, 5]
    assert len(s._client.all_ids) == 10


@pytest.mark.asyncio
async def test_rejected_oversized_point_error_names_the_chunk():
    cap = 64 * 1024
    s = _store(upsert_max_bytes=cap)
    s._client = _SizeLimitedClient(limit=cap)   # the server rejects it too
    chunks = [Chunk(id=f"c{i}", doc_id="paper-7", content="x",
                    embedding=[1.0, 2.0, 3.0]) for i in range(3)]
    chunks[1].metadata["citation"] = "r" * (cap * 2)
    with pytest.raises(OversizedPointError) as ei:
        await s.upsert(chunks)
    msg = str(ei.value)
    assert "chunk_id='c1'" in msg and "doc_id='paper-7'" in msg
    assert "'citation'" in msg and "estimated_bytes=" in msg
    assert isinstance(ei.value.__cause__, RuntimeError)   # store error chained
    assert len(msg) < 2000                                 # payload not echoed
    assert s._client.all_ids and len(s._client.all_ids) == 1   # c0 was sent


@pytest.mark.asyncio
async def test_ordinary_batch_failure_is_not_rewrapped():
    """Only a lone oversized point gets the attributing error; any other
    failure propagates as the store raised it (retry classification unchanged)."""

    class _Boom(_FakeClient):
        async def upsert(self, collection_name, points):
            raise ConnectionError("down")

    s = _store()
    s._client = _Boom()
    with pytest.raises(ConnectionError):
        await s.upsert(_chunks(3))


def test_setting_default_matches_store_default():
    # The field default, not Settings(): an operator env var must not decide this.
    assert Settings.model_fields["qdrant_upsert_max_bytes"].default == UPSERT_MAX_BYTES_DEFAULT
