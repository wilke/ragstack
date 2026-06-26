"""Elasticsearch-backed TextIndex (BM25), tenant-scoped.

Mirrors the Qdrant store's tenancy: a chunk's ES document id is scoped by tenant
(``tenant:chunk_id``) so the same source under two tenants yields distinct docs,
and searches filter to the caller's readable tenants. The import of the
elasticsearch client is lazy so the optional ``text`` extra is only required when
this backend is actually selected.
"""
from __future__ import annotations

from typing import Any

from ragstack.models import Chunk, ScoredChunk
from ragstack.tenancy import DEFAULT_TENANT

_MAPPING: dict[str, Any] = {
    "content": {"type": "text"},  # analyzed → BM25
    "tenant_id": {"type": "keyword"},
    "doc_id": {"type": "keyword"},
    "chunk_id": {"type": "keyword"},
    "start_char": {"type": "integer"},
    "end_char": {"type": "integer"},
}


def _es_id(tenant: str, chunk_id: str) -> str:
    return f"{tenant}:{chunk_id}"


def _build_query(query: str, filters: dict[str, Any] | None) -> dict[str, Any]:
    """BM25 match on content, plus exact filters. A list filter value matches any
    of its entries (used for tenant reads: own + public)."""
    filter_clauses: list[dict[str, Any]] = []
    for key, value in (filters or {}).items():
        if isinstance(value, (list, tuple, set)):
            values = list(value)
            if not values:
                continue  # empty list = no constraint, not "match nothing"
            filter_clauses.append({"terms": {key: values}})
        else:
            filter_clauses.append({"term": {key: value}})
    return {"bool": {"must": [{"match": {"content": query}}], "filter": filter_clauses}}


class ElasticsearchTextIndex:
    """TextIndex protocol backed by Elasticsearch BM25."""

    def __init__(self, url: str, index: str, api_key: str | None = None) -> None:
        from elasticsearch import AsyncElasticsearch

        self._es = AsyncElasticsearch(hosts=url, api_key=api_key or None)
        self._index = index

    async def ensure_index(self) -> None:
        if not await self._es.indices.exists(index=self._index):
            await self._es.indices.create(index=self._index, mappings={"properties": _MAPPING})

    async def index(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        operations: list[dict[str, Any]] = []
        for c in chunks:
            tenant = str(c.metadata.get("tenant_id", DEFAULT_TENANT))
            operations.append({"index": {"_index": self._index, "_id": _es_id(tenant, c.id)}})
            operations.append(
                {
                    "content": c.content,
                    "tenant_id": tenant,
                    "doc_id": c.doc_id,
                    "chunk_id": c.id,
                    "start_char": c.start_char,
                    "end_char": c.end_char,
                }
            )
        # refresh so the just-indexed docs are immediately searchable.
        await self._es.bulk(operations=operations, refresh=True)

    async def search(
        self,
        query: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[ScoredChunk]:
        resp = await self._es.search(
            index=self._index, query=_build_query(query, filters), size=top_k
        )
        results: list[ScoredChunk] = []
        for hit in resp["hits"]["hits"]:
            src = hit["_source"]
            chunk = Chunk(
                id=str(src.get("chunk_id", hit["_id"])),
                doc_id=str(src.get("doc_id", "")),
                content=str(src.get("content", "")),
                start_char=int(src.get("start_char", 0) or 0),
                end_char=int(src.get("end_char", 0) or 0),
                metadata={"tenant_id": src.get("tenant_id", DEFAULT_TENANT)},
            )
            results.append(
                ScoredChunk(chunk=chunk, score=float(hit["_score"]), retrieval_method="bm25")
            )
        return results

    async def delete(self, doc_id: str, tenant_id: str | None = None) -> None:
        filter_clauses: list[dict[str, Any]] = [{"term": {"doc_id": doc_id}}]
        if tenant_id is not None:
            filter_clauses.append({"term": {"tenant_id": tenant_id}})
        await self._es.delete_by_query(
            index=self._index,
            query={"bool": {"filter": filter_clauses}},
            refresh=True,
            conflicts="proceed",
        )

    async def close(self) -> None:
        await self._es.close()
