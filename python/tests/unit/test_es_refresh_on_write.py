"""`refresh_on_write` for the ES text index (#323 follow-up).

Every write forced a synchronous refresh. That is correct for the interactive API
path (read-your-writes) and ruinous for a bulk load: measured mid-build on an
11.9M-doc single-shard index, 1,355 refreshes in 90 seconds consumed 89.1 s of
that window against 1.5 s deleting and 0.0 s indexing.

Crucially this is a DIFFERENT mechanism from `index.refresh_interval`, and
parking the interval does not fix it — an explicit `refresh=true` on a request
refreshes the affected shards regardless of the interval. These tests pin the
per-request parameter specifically, because that is the one that cost the time.
"""
import pytest

from ragstack.models import Chunk
from ragstack.stores.elasticsearch import ElasticsearchTextIndex


class _FakeES:
    """Captures the `refresh` argument of every write call."""

    def __init__(self) -> None:
        self.bulk_refresh: list = []
        self.dbq_refresh: list = []

    async def bulk(self, operations, refresh=None, **kw):
        self.bulk_refresh.append(refresh)
        return {"errors": False, "items": []}

    async def delete_by_query(self, index=None, query=None, refresh=None, **kw):
        self.dbq_refresh.append(refresh)
        return {"deleted": 0}


def _index(refresh_on_write):
    idx = ElasticsearchTextIndex.__new__(ElasticsearchTextIndex)
    idx._es = _FakeES()
    idx._index = "t"
    idx._refresh_on_write = refresh_on_write
    return idx


def _chunks(n=2):
    return [
        Chunk(id=f"c{i}", doc_id="d1", content="x", start_char=0, end_char=1,
              metadata={"tenant_id": "public"})
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_default_still_forces_refresh():
    """The interactive API path depends on read-your-writes — do not regress it."""
    idx = _index(True)
    await idx.index(_chunks())
    await idx.delete("d1", tenant_id="public")
    assert idx._es.bulk_refresh == [True]
    assert idx._es.dbq_refresh == [True]


@pytest.mark.asyncio
async def test_bulk_mode_suppresses_refresh_on_every_write_path():
    idx = _index(False)
    await idx.index(_chunks())
    await idx.delete("d1", tenant_id="public")
    await idx.delete_except("d1", {"c0"}, tenant_id="public")
    assert idx._es.bulk_refresh == [False], "bulk index still forced a refresh"
    assert idx._es.dbq_refresh == [False, False], "a delete path still forced one"


@pytest.mark.asyncio
async def test_constructor_default_is_refresh_on_write():
    """Bulk mode must be opt-in: every existing caller keeps read-your-writes."""
    import inspect
    sig = inspect.signature(ElasticsearchTextIndex.__init__)
    assert sig.parameters["refresh_on_write"].default is True
