"""ES bulk requests must be split so the server will accept them.

`index()` used to put every chunk in ONE bulk request. ES caps the body at
`http.max_content_length` (100 MB default) and answers an oversized one with a
bare HTTP 413 — no per-item errors, nothing written.

This was the root cause of a multi-day production failure: a 1.99 GB shard of
38,322 chunks failed this way on every load attempt, while the vector store
(which has always batched at 256) accepted all 38,322. The legs then disagreed
by exactly 38,322 documents and the batch failed after 63 other shards had
loaded fine.
"""
import json

import pytest

from ragstack.models import Chunk
from ragstack.stores.elasticsearch import (
    _BULK_MAX_BYTES,
    ElasticsearchTextIndex,
    _doc_bytes_estimate,
)


class _FakeES:
    """Records each bulk call's operation count and its encoded body size."""

    def __init__(self, limit: int | None = None) -> None:
        self.calls: list[int] = []
        self.bytes: list[int] = []
        self._limit = limit

    async def bulk(self, operations, refresh=None, **kw):
        # Two entries per document: the action line and the source.
        docs = len(operations) // 2
        # The real wire bytes: UTF-8 JSON, non-ASCII unescaped, as the client
        # sends it. One NDJSON line per operation.
        size = sum(len(json.dumps(o, ensure_ascii=False).encode()) + 1
                   for o in operations)
        if self._limit is not None and size > self._limit:
            raise RuntimeError(f"413 payload too large: {size} > {self._limit}")
        self.calls.append(docs)
        self.bytes.append(size)
        return {"errors": False, "items": []}


def _index(es, batch_size=500):
    idx = ElasticsearchTextIndex.__new__(ElasticsearchTextIndex)
    idx._es = es
    idx._index = "t"
    idx._refresh_on_write = False
    idx._bulk_batch_size = batch_size
    return idx


def _chunks(n, content_len=100):
    return [
        Chunk(id=f"c{i}", doc_id=f"d{i//30}", content="x" * content_len,
              start_char=0, end_char=content_len, metadata={"tenant_id": "public"})
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_large_shard_is_split_instead_of_one_giant_request():
    """The regression: 38,322 chunks used to go in a single request."""
    es = _FakeES()
    await _index(es).index(_chunks(38322))
    assert len(es.calls) > 1, "38,322 chunks still went in one bulk request"
    assert sum(es.calls) == 38322, "batching lost or duplicated documents"
    assert max(es.calls) <= 500


@pytest.mark.asyncio
async def test_every_batch_stays_under_the_byte_cap():
    es = _FakeES()
    # 40 KB of content each: the count cap alone (500) would build a ~20 MB body.
    await _index(es).index(_chunks(400, content_len=40_000))
    assert sum(es.calls) == 400
    assert max(es.bytes) <= _BULK_MAX_BYTES, "a batch exceeded the byte cap"


@pytest.mark.asyncio
async def test_byte_cap_splits_even_when_count_cap_would_not():
    """Size varies by orders of magnitude across a real corpus, so a count-only
    cap still lets a run of large chunks build an oversized body."""
    es = _FakeES()
    await _index(es, batch_size=10_000).index(_chunks(300, content_len=200_000))
    assert len(es.calls) > 1, "byte cap did not split; count cap alone is not enough"
    assert sum(es.calls) == 300


@pytest.mark.asyncio
async def test_survives_a_server_that_rejects_oversized_bodies():
    """End-to-end shape of the production failure: a server enforcing a limit."""
    es = _FakeES(limit=_BULK_MAX_BYTES * 2)
    await _index(es).index(_chunks(38322, content_len=1000))
    assert sum(es.calls) == 38322


@pytest.mark.asyncio
async def test_small_input_still_uses_a_single_request():
    es = _FakeES()
    await _index(es).index(_chunks(10))
    assert es.calls == [10]


@pytest.mark.asyncio
async def test_empty_input_makes_no_request():
    es = _FakeES()
    await _index(es).index([])
    assert es.calls == []


# --- #177: the estimate must count the metadata actually being sent ----------


def _meta_heavy_chunks(n, meta_bytes):
    """Tiny content, ~1 MB of metadata each: the mis-extracted reference list
    copied into `citation` that #177's corpus scan found on ~100 chunks of one doc."""
    return [
        Chunk(id=f"c{i}", doc_id="d0", content="short", start_char=0, end_char=5,
              metadata={"tenant_id": "public", "citation": "r" * meta_bytes})
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_metadata_heavy_chunks_close_the_batch_by_bytes():
    """60 chunks x 1 MB metadata = 60 MB. The old estimate (content + flat 2 KB)
    called that ~123 KB and sent it as one request — over the 20 MiB cap, and
    a server with a smaller http.max_content_length answers it with a 413."""
    es = _FakeES(limit=_BULK_MAX_BYTES)
    await _index(es).index(_meta_heavy_chunks(60, 1_000_000))
    assert sum(es.calls) == 60
    assert len(es.calls) > 1
    assert max(es.bytes) <= _BULK_MAX_BYTES


def test_estimate_counts_serialized_metadata():
    small = Chunk(id="a", doc_id="d", content="x" * 100, metadata={"t": "y"})
    big = Chunk(id="b", doc_id="d", content="x" * 100, metadata={"t": "y" * 500_000})
    assert _doc_bytes_estimate(big) - _doc_bytes_estimate(small) >= 499_999


@pytest.mark.asyncio
async def test_normal_chunks_keep_the_same_request_count():
    """Ordinary chunks with ordinary metadata still batch on the count cap."""
    es = _FakeES()
    chunks = [
        Chunk(id=f"c{i}", doc_id=f"d{i // 30}", content="w" * 2000, start_char=0,
              end_char=2000, metadata={"tenant_id": "public", "title": "A title",
                                       "authors": "Doe J, Roe R", "year": 2021})
        for i in range(1200)
    ]
    await _index(es).index(chunks)
    assert es.calls == [500, 500, 200]


@pytest.mark.asyncio
async def test_non_ascii_metadata_batches_stay_under_the_cap_in_real_bytes():
    """CJK/emoji metadata: the fake measures UTF-8 bytes, the estimate counts
    ensure_ascii escapes (never fewer bytes), so no batch can exceed the cap."""
    es = _FakeES(limit=_BULK_MAX_BYTES)
    chunks = [
        Chunk(id=f"c{i}", doc_id="d0", content="内容" * 100, start_char=0,
              end_char=200, metadata={"tenant_id": "public",
                                      "title": ("漢字" if i % 2 else "😀") * 400_000})
        for i in range(40)
    ]
    await _index(es).index(chunks)
    assert sum(es.calls) == 40
    assert max(es.bytes) <= _BULK_MAX_BYTES
