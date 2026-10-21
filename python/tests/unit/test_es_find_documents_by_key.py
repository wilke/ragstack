"""``ElasticsearchTextIndex.find_documents_by_key`` (#747) against a fake client.

No cluster: the request the method builds is captured and asserted (tenant
filter, the case-insensitive DOI term + the ``/-`` prefix for the malformed
form production indexes carry, pmcid/pmid terms, one request), and a trimmed
real-shaped aggregation response is parsed and matched on NORMALISED keys.
Self-exclusion is the guard's job, not the store's.
"""
from __future__ import annotations

import pytest

pytest.importorskip("elasticsearch")

from ragstack.stores.elasticsearch import ElasticsearchTextIndex  # noqa: E402

DOI = "10.1073/pnas.2018995118"


class _FakeES:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls: list[dict] = []

    async def search(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return self.response


def _index(response: dict) -> tuple[ElasticsearchTextIndex, _FakeES]:
    ix = ElasticsearchTextIndex(url="http://unused.invalid:9200", index="x")
    fake = _FakeES(response)
    ix._es = fake  # type: ignore[assignment]
    return ix, fake


def _bucket(doc_id: str, meta: dict, count: int = 5) -> dict:
    return {"key": doc_id, "doc_count": count, "exemplar": {"hits": {"hits": [
        {"_source": {"doc_id": doc_id, "metadata": meta}}]}}}


RESPONSE = {"aggregations": {"docs": {"buckets": [
    _bucket("a", {"doi": DOI, "pmcid": "PMC8092463", "pmid": "33858987",
                  "tenant_id": "alice", "filename": "sha-1.md"}),
    _bucket("b", {"doi": f"{DOI}/-/", "tenant_id": "public"}),
    _bucket("c", {"pmid": 33858987, "tenant_id": "alice"}),  # the #594 long
    _bucket("d", {"doi": "10.1000/unrelated", "tenant_id": "alice"}),
    {"key": "e", "doc_count": 1, "exemplar": {"hits": {"hits": []}}},  # deleted mid-agg
]}}}


@pytest.mark.asyncio
async def test_query_shape() -> None:
    ix, fake = _index(RESPONSE)
    keys = [("doi", DOI), ("pmcid", "PMC8092463"), ("pmid", "33858987")]
    await ix.find_documents_by_key(keys, ["alice", "public"], limit=7)
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["size"] == 0 and call["track_total_hits"] is False
    q = call["query"]["bool"]
    assert q["filter"] == [{"terms": {"metadata.tenant_id": ["alice", "public"]}}]
    assert q["minimum_should_match"] == 1
    assert {"term": {"metadata.doi": {"value": DOI, "case_insensitive": True}}} in q["should"]
    assert {"prefix": {"metadata.doi": {"value": f"{DOI}/-", "case_insensitive": True}}} \
        in q["should"]
    assert {"terms": {"metadata.pmcid": ["PMC8092463"]}} in q["should"]
    assert {"terms": {"metadata.pmid": ["33858987"]}} in q["should"]
    agg = call["aggs"]["docs"]
    assert agg["terms"] == {"field": "doc_id", "size": 7}
    assert agg["aggs"]["exemplar"]["top_hits"]["size"] == 1


@pytest.mark.asyncio
async def test_parse_groups_on_normalised_keys() -> None:
    ix, _ = _index(RESPONSE)
    keys = [("doi", DOI), ("pmid", "33858987")]
    found = await ix.find_documents_by_key(keys, ["alice", "public"])
    assert [s.doc_id for s in found[("doi", DOI)]] == ["a", "b"]   # `/-/` joins
    assert [s.doc_id for s in found[("pmid", "33858987")]] == ["a", "c"]  # int coerces
    assert found[("doi", DOI)][0].chunk_count == 5
    assert found[("doi", DOI)][0].source == "sha-1.md"


@pytest.mark.asyncio
async def test_fails_closed_on_no_tenants_and_sends_nothing_without_keys() -> None:
    ix, fake = _index(RESPONSE)
    assert await ix.find_documents_by_key([("doi", DOI)], []) == {}
    assert await ix.find_documents_by_key([], ["alice"]) == {}
    assert fake.calls == []


@pytest.mark.asyncio
async def test_missing_aggregations_block_is_no_match() -> None:
    ix, _ = _index({})
    assert await ix.find_documents_by_key([("doi", DOI)], ["alice"]) == {}
