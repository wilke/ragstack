"""#754: the ``symbol_glossary`` field on the BM25 leg. Pure ``_build_query`` /
bulk-body tests — no Elasticsearch."""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from ragstack.models import Chunk
from ragstack.stores.elasticsearch import _MAPPINGS, ElasticsearchTextIndex, _build_query
from ragstack.stores.filters import Not

FILTERS = {"tenant_id": ["alice", "public"], "source": "g.pdf", "section": Not("references")}


def test_boost_zero_is_byte_identical_to_the_pre_754_query() -> None:
    pre = {
        "bool": {
            "must": [{"match": {"content": "breathing rate"}}],
            "filter": [
                {"terms": {"metadata.tenant_id": ["alice", "public"]}},
                {"term": {"metadata.source": "g.pdf"}},
            ],
            "must_not": [{"term": {"metadata.section": "references"}}],
        }
    }
    for q in (_build_query("breathing rate", FILTERS), _build_query("breathing rate", FILTERS, 0.0),
              _build_query("breathing rate", FILTERS, -1.0)):
        assert json.dumps(q, sort_keys=True) == json.dumps(pre, sort_keys=True)


def test_positive_boost_sums_content_and_glossary() -> None:
    q = _build_query("breathing rate", FILTERS, 0.5)
    assert q["bool"]["must"] == [{
        "multi_match": {
            "query": "breathing rate",
            "type": "most_fields",
            "fields": ["content", "symbol_glossary^0.5"],
        }
    }]
    # Filters and the negation are untouched by the boost.
    assert q["bool"]["filter"] == _build_query("breathing rate", FILTERS)["bool"]["filter"]
    assert q["bool"]["must_not"] == [{"term": {"metadata.section": "references"}}]


@pytest.mark.parametrize("filters", [None, {}, {"tenant_id": []}])
def test_tenant_fail_closed_unchanged_with_boost(filters: Any) -> None:
    with pytest.raises(ValueError, match="tenant_id"):
        _build_query("q", filters, 2.0)


def test_mapping_declares_symbol_glossary_as_text() -> None:
    assert _MAPPINGS["properties"]["symbol_glossary"] == {"type": "text"}
    assert _MAPPINGS["properties"]["content"] == {"type": "text"}


class _FakeES:
    def __init__(self) -> None:
        self.bulk_bodies: list[list[dict[str, Any]]] = []
        self.search_bodies: list[dict[str, Any]] = []

    async def bulk(self, operations: list[dict[str, Any]], refresh: bool) -> dict[str, Any]:
        self.bulk_bodies.append(operations)
        return {"errors": False, "items": []}

    async def search(self, index: str, query: dict[str, Any], size: int) -> dict[str, Any]:
        self.search_bodies.append(query)
        return {"hits": {"hits": [{"_id": "t:c1", "_score": 1.0, "_source": {
            "chunk_id": "c1", "doc_id": "d1", "content": "Qb=0.5", "start_char": 0,
            "end_char": 6, "metadata": {"tenant_id": "t"},
            "symbol_glossary": "Qb — breathing flow rate"}}]}}


def _store(boost: float = 0.0) -> tuple[ElasticsearchTextIndex, _FakeES]:
    pytest.importorskip("elasticsearch")
    store = ElasticsearchTextIndex(url="http://127.0.0.1:1", index="i", symbol_glossary_boost=boost)
    fake = _FakeES()
    store._es = fake  # type: ignore[assignment]
    return store, fake


def test_bulk_body_carries_glossary_only_when_present() -> None:
    store, fake = _store()
    with_g = Chunk(id="c1", doc_id="d1", content="Qb=0.5", symbol_glossary="Qb — breathing flow rate",
                   metadata={"tenant_id": "t"})
    without = Chunk(id="c2", doc_id="d1", content="plain", metadata={"tenant_id": "t"})
    asyncio.run(store.index([with_g, without]))
    docs = fake.bulk_bodies[0][1::2]
    assert docs[0]["symbol_glossary"] == "Qb — breathing flow rate"
    assert "symbol_glossary" not in docs[1]
    assert sorted(docs[1]) == ["chunk_id", "content", "doc_id", "end_char", "metadata", "start_char"]


def test_search_uses_configured_boost_and_round_trips_field() -> None:
    store, fake = _store(1.5)
    hits = asyncio.run(store.search("breathing rate", filters={"tenant_id": ["t"]}))
    assert fake.search_bodies[0]["bool"]["must"][0]["multi_match"]["fields"][1] == "symbol_glossary^1.5"
    assert hits[0].chunk.symbol_glossary == "Qb — breathing flow rate"
    store0, fake0 = _store()
    asyncio.run(store0.search("breathing rate", filters={"tenant_id": ["t"]}))
    assert fake0.search_bodies[0]["bool"]["must"] == [{"match": {"content": "breathing rate"}}]
