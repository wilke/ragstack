"""Robustness tests for ElasticsearchTextIndex.list_documents response parsing.

Feeds crafted aggregation responses through a fake ES client (no server) to prove
the parser tolerates the transient/edge shapes a live cluster can return —
notably a zero-hit top_hits bucket (a bucket's only chunk deleted mid-aggregation)
and a missing aggregations block — without raising an uncaught 500.
"""
from __future__ import annotations

import pytest

pytest.importorskip("elasticsearch")

from ragstack.documents import encode_cursor  # noqa: E402
from ragstack.stores.elasticsearch import ElasticsearchTextIndex  # noqa: E402


class _FakeES:
    def __init__(self, response: dict) -> None:
        self._response = response

    async def search(self, **kwargs) -> dict:
        return self._response

    async def close(self) -> None:  # pragma: no cover - not exercised
        pass


def _index(response: dict) -> ElasticsearchTextIndex:
    ix = ElasticsearchTextIndex(url="http://unused.invalid:9200", index="x")
    ix._es = _FakeES(response)  # type: ignore[assignment]
    return ix


def _bucket(doc_id: str, count: int, hits: list[dict]) -> dict:
    return {
        "key": {"doc_id": doc_id},
        "doc_count": count,
        "exemplar": {"hits": {"hits": hits}},
    }


def _hit(**metadata) -> dict:
    return {"_source": {"metadata": metadata}}


@pytest.mark.asyncio
async def test_skips_zero_hit_bucket_but_keeps_cursor() -> None:
    ix = _index(
        {
            "aggregations": {
                "docs": {
                    "buckets": [
                        _bucket("d1", 2, [_hit(title="T", tenant_id="public")]),
                        _bucket("d2", 1, []),  # transient zero-hit → skipped, no IndexError
                    ],
                    "after_key": {"doc_id": "d2"},
                }
            }
        }
    )
    docs, nxt = await ix.list_documents(["public"], limit=2)
    assert [d.doc_id for d in docs] == ["d1"]  # d2 dropped, d1 preserved
    assert nxt == encode_cursor("d2")  # cursor still advances (page was full)


@pytest.mark.asyncio
async def test_missing_aggregations_returns_empty() -> None:
    ix = _index({})  # e.g. a degenerate/error-adjacent response
    assert await ix.list_documents(["public"]) == ([], None)


@pytest.mark.asyncio
async def test_non_string_after_key_does_not_raise() -> None:
    ix = _index(
        {
            "aggregations": {
                "docs": {
                    "buckets": [_bucket("d1", 1, [_hit(title="T")])],
                    "after_key": {"doc_id": 12345},  # defensively coerced via str()
                }
            }
        }
    )
    docs, nxt = await ix.list_documents(["public"], limit=1)
    assert [d.doc_id for d in docs] == ["d1"]
    assert nxt == encode_cursor("12345")


@pytest.mark.asyncio
async def test_hit_without_metadata_yields_empty_source() -> None:
    ix = _index(
        {
            "aggregations": {
                "docs": {
                    "buckets": [{"key": {"doc_id": "d1"}, "doc_count": 1,
                                 "exemplar": {"hits": {"hits": [{"_source": {}}]}}}],
                }
            }
        }
    )
    docs, nxt = await ix.list_documents(["public"], limit=5)
    assert docs[0].doc_id == "d1" and docs[0].source == ""
    assert nxt is None  # short page terminates
