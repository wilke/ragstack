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
import pytest

from ragstack.models import Chunk
from ragstack.stores.elasticsearch import (
    _BULK_MAX_BYTES,
    ElasticsearchTextIndex,
)


class _FakeES:
    """Records each bulk call's operation count and approximate body size."""

    def __init__(self, limit: int | None = None) -> None:
        self.calls: list[int] = []
        self.bytes: list[int] = []
        self._limit = limit

    async def bulk(self, operations, refresh=None, **kw):
        # Two entries per document: the action line and the source.
        docs = len(operations) // 2
        size = sum(len(str(o)) for o in operations)
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
