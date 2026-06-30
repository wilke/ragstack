"""`delete_except` prunes only a document's orphan points/docs (by id), leaving
the kept chunks — the targeted, scale-safe replacement for a filtered delete."""
from __future__ import annotations

import types

import pytest

from ragstack.stores.elasticsearch import ElasticsearchTextIndex
from ragstack.stores.qdrant import QdrantVectorStore, _point_id


class _FakeQdrant:
    def __init__(self, existing_point_ids: list[str]) -> None:
        self._existing = existing_point_ids
        self.deleted: list[str] | None = None

    async def scroll(self, *, collection_name, scroll_filter, with_payload,
                     with_vectors, limit, offset):
        # single page, then stop
        if offset is not None:
            return [], None
        return [types.SimpleNamespace(id=i) for i in self._existing], None

    async def delete(self, *, collection_name, points_selector):
        self.deleted = list(points_selector.points)


@pytest.mark.asyncio
async def test_qdrant_delete_except_removes_only_orphans():
    store = QdrantVectorStore(url="http://q", collection="c", vector_size=4)
    keep_pid = _point_id("keep", "public")
    orphan_pid = _point_id("orphan", "public")
    fake = _FakeQdrant([keep_pid, orphan_pid])
    store._client = fake  # type: ignore[assignment]

    await store.delete_except("doc1", {"keep"}, tenant_id="public")

    assert fake.deleted == [orphan_pid]  # kept point id is NOT deleted


@pytest.mark.asyncio
async def test_qdrant_delete_except_noop_when_nothing_stale():
    store = QdrantVectorStore(url="http://q", collection="c", vector_size=4)
    fake = _FakeQdrant([_point_id("keep", "public")])
    store._client = fake  # type: ignore[assignment]

    await store.delete_except("doc1", {"keep"}, tenant_id="public")

    assert fake.deleted is None  # no delete call when there are no orphans


class _FakeES:
    def __init__(self) -> None:
        self.query = None

    async def delete_by_query(self, *, index, query, refresh, conflicts):
        self.query = query


@pytest.mark.asyncio
async def test_es_delete_except_builds_must_not_chunk_ids():
    idx = ElasticsearchTextIndex(url="http://es:9200", index="i")
    fake = _FakeES()
    idx._es = fake  # type: ignore[assignment]

    await idx.delete_except("doc1", {"keep1", "keep2"}, tenant_id="public")

    bq = fake.query["bool"]
    assert {"term": {"doc_id": "doc1"}} in bq["filter"]
    assert {"term": {"metadata.tenant_id": "public"}} in bq["filter"]
    must_not = bq["must_not"][0]["terms"]["chunk_id"]
    assert set(must_not) == {"keep1", "keep2"}
