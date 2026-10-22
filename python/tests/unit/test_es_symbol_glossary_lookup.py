"""``ElasticsearchTextIndex.get_symbol_glossaries`` — the one batched,
tenant-scoped read that gives rerank candidates their glossary (#754).

A recording fake stands in for the client: no live Elasticsearch."""
from __future__ import annotations

import pytest

pytest.importorskip("elasticsearch")

from ragstack.stores.elasticsearch import ElasticsearchTextIndex  # noqa: E402
from ragstack.stores.errors import StoreUnavailable  # noqa: E402


class _RecordingES:
    def __init__(self, hits: list[dict] | None = None, exc: BaseException | None = None) -> None:
        self.calls: list[dict] = []
        self.hits = hits or []
        self.exc = exc

    async def search(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return {"hits": {"hits": self.hits}}

    async def close(self) -> None:  # pragma: no cover
        pass


def _index(es: _RecordingES) -> ElasticsearchTextIndex:
    ix = ElasticsearchTextIndex(url="http://127.0.0.1:1", index="ragstack_lib_x")
    ix._es = es  # type: ignore[assignment]
    return ix


def _hit(tenant: str, cid: str, glossary: str | None) -> dict:
    src: dict = {"chunk_id": cid, "metadata": {"tenant_id": tenant}}
    if glossary is not None:
        src["symbol_glossary"] = glossary
    return {"_id": f"{tenant}:{cid}", "_source": src}


async def test_one_request_by_tenant_scoped_document_id() -> None:
    es = _RecordingES([_hit("alice", "c1", "Qb — flow"), _hit("public", "c2", ""),
                       _hit("public", "c3", None)])
    found = await _index(es).get_symbol_glossaries(["c1", "c2", "c3", "c1"], ["alice", "public"])
    assert len(es.calls) == 1
    call = es.calls[0]
    filters = call["query"]["bool"]["filter"]
    ids = filters[0]["ids"]["values"]
    # Every <tenant>:<chunk_id> the caller may read, de-duplicated — and nothing else.
    assert sorted(ids) == sorted(
        f"{t}:{c}" for c in ("c1", "c2", "c3") for t in ("alice", "public")
    )
    assert filters[1] == {"terms": {"metadata.tenant_id": ["alice", "public"]}}
    assert call["size"] == 6
    assert set(call["source_includes"]) == {"chunk_id", "symbol_glossary", "metadata.tenant_id"}
    # Only non-empty glossaries come back, keyed (tenant, chunk id).
    assert found == {("alice", "c1"): "Qb — flow"}


async def test_an_empty_tenant_scope_is_refused_before_any_request() -> None:
    es = _RecordingES()
    with pytest.raises(ValueError, match="tenant scope"):
        await _index(es).get_symbol_glossaries(["c1"], [])
    assert es.calls == []


async def test_no_ids_no_request() -> None:
    es = _RecordingES()
    assert await _index(es).get_symbol_glossaries([], ["alice"]) == {}
    assert es.calls == []


async def test_an_outage_is_store_unavailable() -> None:
    from elasticsearch import ConnectionTimeout

    es = _RecordingES(exc=ConnectionTimeout("timed out"))
    with pytest.raises(StoreUnavailable):
        await _index(es).get_symbol_glossaries(["c1"], ["alice"])
