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
    _bucket("c2", {"pmcid": "pmc8092463", "tenant_id": "alice"}),  # stored lower-case
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
    assert {"term": {"metadata.pmcid": {"value": "PMC8092463", "case_insensitive": True}}} \
        in q["should"]
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
    by_pmcid = await ix.find_documents_by_key([("pmcid", "PMC8092463")], ["alice"])
    assert [s.doc_id for s in by_pmcid[("pmcid", "PMC8092463")]] == ["a", "c2"]
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


class _Indices:
    def __init__(self, ftype: str) -> None:
        self.ftype = ftype
        self.calls = 0

    async def get_field_mapping(self, index, fields):
        self.calls += 1
        return {"x": {"mappings": {
            "metadata.doi": {"full_name": "metadata.doi", "mapping": {"doi": {"type": self.ftype}}},
        }}}


@pytest.mark.asyncio
@pytest.mark.parametrize("ftype,warned", [("text", True), ("keyword", False)])
async def test_one_time_warning_when_the_id_field_is_not_keyword(caplog, ftype, warned) -> None:
    """Review fix 7: a text-mapped metadata.doi makes the term query match
    nothing — a silent miss, so the first lookup per index says so, once."""
    import logging

    ix, fake = _index(RESPONSE)
    fake.indices = _Indices(ftype)  # type: ignore[attr-defined]
    with caplog.at_level(logging.WARNING, logger="ragstack.stores.elasticsearch"):
        await ix.find_documents_by_key([("doi", DOI)], ["alice"])
        await ix.find_documents_by_key([("doi", DOI)], ["alice"])
    assert fake.indices.calls == 1  # type: ignore[attr-defined]
    assert ("mapped 'text', not keyword" in caplog.text) is warned
