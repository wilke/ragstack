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

# Filters target chunk *metadata* keys (matching the vector store, which filters
# on chunk.metadata), so metadata is stored as a nested object and string values
# are mapped to ``keyword`` for exact term/terms matching. ``content`` is the only
# analyzed (BM25) field; ``doc_id``/``chunk_id`` stay top-level for delete-by-doc
# and id round-tripping. ``tenant_id`` lives in metadata only (no duplication).
_MAPPINGS: dict[str, Any] = {
    "dynamic_templates": [
        {
            "metadata_strings_as_keyword": {
                "path_match": "metadata.*",
                "match_mapping_type": "string",
                "mapping": {"type": "keyword"},
            }
        }
    ],
    "properties": {
        "content": {"type": "text"},  # analyzed → BM25
        "doc_id": {"type": "keyword"},
        "chunk_id": {"type": "keyword"},
        "start_char": {"type": "integer"},
        "end_char": {"type": "integer"},
        "metadata": {"type": "object"},
    },
}


def _es_id(tenant: str, chunk_id: str) -> str:
    return f"{tenant}:{chunk_id}"


def _build_query(query: str, filters: dict[str, Any] | None) -> dict[str, Any]:
    """BM25 match on content, plus exact filters. Filter keys are chunk metadata
    keys (same as the vector store), so they target ``metadata.<key>``. A list
    value matches any of its entries (used for tenant reads: own + public).

    This index is a tenancy boundary, so a non-empty ``tenant_id`` filter is
    required: an unscoped (or empty-scoped) search would silently return chunks
    across every tenant. Fail closed rather than leak."""
    filters = filters or {}
    if not filters.get("tenant_id"):
        raise ValueError(
            "ElasticsearchTextIndex.search requires a non-empty tenant_id filter; "
            "an unscoped search would return chunks across all tenants"
        )
    filter_clauses: list[dict[str, Any]] = []
    for key, value in filters.items():
        field = f"metadata.{key}"
        if isinstance(value, (list, tuple, set)):
            values = list(value)
            if not values:
                continue  # empty list = no constraint, not "match nothing"
            filter_clauses.append({"terms": {field: values}})
        else:
            filter_clauses.append({"term": {field: value}})
    return {"bool": {"must": [{"match": {"content": query}}], "filter": filter_clauses}}


class ElasticsearchTextIndex:
    """TextIndex protocol backed by Elasticsearch BM25."""

    def __init__(self, url: str, index: str, api_key: str | None = None) -> None:
        from elasticsearch import AsyncElasticsearch

        self._es = AsyncElasticsearch(hosts=url, api_key=api_key or None)
        self._index = index

    async def ensure_index(self) -> None:
        # Create idempotently rather than gating on exists(): two workers can both
        # pass an exists-check and then race on create, and the loser gets
        # resource_already_exists_exception. Treat an already-existing index as
        # success; re-raise any other API error.
        from elasticsearch import ApiError

        try:
            await self._es.indices.create(index=self._index, mappings=_MAPPINGS)
        except ApiError as e:
            if "resource_already_exists_exception" not in str(e):
                raise

    async def index(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        operations: list[dict[str, Any]] = []
        for c in chunks:
            # Persist full metadata (not just tenant_id) so BM25 hits round-trip
            # the same metadata as the vector store — otherwise RRF fusion would
            # clobber metadata-rich vector chunks with metadata-poor BM25 chunks.
            metadata = dict(c.metadata)
            tenant = str(metadata.get("tenant_id", DEFAULT_TENANT))
            metadata["tenant_id"] = tenant
            operations.append({"index": {"_index": self._index, "_id": _es_id(tenant, c.id)}})
            operations.append(
                {
                    "content": c.content,
                    "doc_id": c.doc_id,
                    "chunk_id": c.id,
                    "start_char": c.start_char,
                    "end_char": c.end_char,
                    "metadata": metadata,
                }
            )
        # refresh so the just-indexed docs are immediately searchable.
        resp = await self._es.bulk(operations=operations, refresh=True)
        # ES returns HTTP 200 with errors=true on partial failure rather than
        # raising, so a malformed/conflicting doc would silently never be indexed
        # (and a later BM25 search would miss it). Surface the first failure.
        if resp.get("errors"):
            for item in resp.get("items", []):
                result = next(iter(item.values()))
                if result.get("error"):
                    raise RuntimeError(
                        f"elasticsearch bulk index failed for _id={result.get('_id')}: "
                        f"{result['error']}"
                    )

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
            metadata = dict(src.get("metadata") or {})
            metadata.setdefault("tenant_id", DEFAULT_TENANT)
            chunk = Chunk(
                id=str(src.get("chunk_id", hit["_id"])),
                doc_id=str(src.get("doc_id", "")),
                content=str(src.get("content", "")),
                start_char=int(src.get("start_char", 0) or 0),
                end_char=int(src.get("end_char", 0) or 0),
                metadata=metadata,
            )
            results.append(
                ScoredChunk(chunk=chunk, score=float(hit["_score"]), retrieval_method="bm25")
            )
        return results

    async def count_tenants(self, tenants: list[str]) -> int:
        """Count indexed chunks visible to ``tenants`` (own + public) via a
        terms-filtered ``_count``. Fails closed (returns 0) on an empty list —
        an unscoped count would total every tenant's chunks, mirroring the
        non-empty-tenant guard in ``_build_query``."""
        if not tenants:
            return 0
        resp = await self._es.count(
            index=self._index,
            query={"bool": {"filter": [{"terms": {"metadata.tenant_id": list(tenants)}}]}},
        )
        return int(resp["count"])

    async def delete(self, doc_id: str, tenant_id: str | None = None) -> None:
        filter_clauses: list[dict[str, Any]] = [{"term": {"doc_id": doc_id}}]
        if tenant_id is not None:
            filter_clauses.append({"term": {"metadata.tenant_id": tenant_id}})
        await self._es.delete_by_query(
            index=self._index,
            query={"bool": {"filter": filter_clauses}},
            refresh=True,
            conflicts="proceed",
        )

    async def delete_except(
        self, doc_id: str, keep_chunk_ids: set[str], tenant_id: str | None = None
    ) -> None:
        """Prune a document's orphan chunks (chunk_id not in ``keep_chunk_ids``).
        The BM25 counterpart to ``QdrantVectorStore.delete_except`` — same
        upsert-then-prune safety (call after indexing the kept chunks so a failure
        here can't lose data) — but via a ``delete_by_query`` scoped to this one
        ``doc_id`` (O(chunks-per-doc), not a whole-index filtered delete), so it
        doesn't hit the at-scale timeout the Qdrant side scrolls-by-id to avoid."""
        filter_clauses: list[dict[str, Any]] = [{"term": {"doc_id": doc_id}}]
        if tenant_id is not None:
            filter_clauses.append({"term": {"metadata.tenant_id": tenant_id}})
        await self._es.delete_by_query(
            index=self._index,
            query={
                "bool": {
                    "filter": filter_clauses,
                    "must_not": [{"terms": {"chunk_id": list(keep_chunk_ids)}}],
                }
            },
            refresh=True,
            conflicts="proceed",
        )

    async def close(self) -> None:
        await self._es.close()
