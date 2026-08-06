"""Elasticsearch-backed TextIndex (BM25), tenant-scoped.

Mirrors the Qdrant store's tenancy: a chunk's ES document id is scoped by tenant
(``tenant:chunk_id``) so the same source under two tenants yields distinct docs,
and searches filter to the caller's readable tenants. The import of the
elasticsearch client is lazy so the optional ``text`` extra is only required when
this backend is actually selected.
"""
from __future__ import annotations

import logging
from typing import Any

from ragstack.documents import (
    DocumentSummary,
    decode_cursor,
    document_from_chunk_metadata,
    encode_cursor,
)
from ragstack.models import Chunk, ScoredChunk
from ragstack.tenancy import DEFAULT_TENANT

log = logging.getLogger(__name__)

# Filters target chunk *metadata* keys (matching the vector store, which filters
# on chunk.metadata), so metadata is stored as a nested object and string values
# are mapped to ``keyword`` for exact term/terms matching. ``content`` is the only
# analyzed (BM25) field; ``doc_id``/``chunk_id`` stay top-level for delete-by-doc
# and id round-tripping. ``tenant_id`` lives in metadata only (no duplication);
# the key name is historical — see tenancy.OWNER_FIELD (owner provenance,
# ADR-0003).
#
# ``ignore_above`` is REQUIRED on the keyword template: a keyword indexes the whole
# value as one Lucene term, and a term over ~32 KB raises a document_parsing_exception
# that aborts the whole bulk ingest (``index()`` raises on the first item error, which
# is not in the transient set — so the batch fails, the checkpoint stalls, the run
# exits nonzero). Real corpora contain poison rows: a paper's entire reference list
# mis-extracted into ``metadata.title``, observed at ~38 KB in production. With
# ignore_above set, an over-long value is simply not indexed for exact match — it is
# still stored in _source and still returned. 8191 chars is the largest bound that
# stays under Lucene's 32766-BYTE limit even for 4-byte UTF-8.
_METADATA_KEYWORD_IGNORE_ABOVE = 8191

_MAPPINGS: dict[str, Any] = {
    "dynamic_templates": [
        {
            "metadata_strings_as_keyword": {
                "path_match": "metadata.*",
                "match_mapping_type": "string",
                "mapping": {
                    "type": "keyword",
                    "ignore_above": _METADATA_KEYWORD_IGNORE_ABOVE,
                },
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
    across every tenant. Fail closed rather than leak.

    Every *other* key fails closed too, without raising: an empty list matches
    nothing rather than lifting the constraint (#196), which is what ES's own
    ``terms`` with an empty array does. A key present with an empty list is a
    real (unsatisfiable) constraint; only an absent key means unconstrained.
    Keep in sync with ``_build_filter`` in stores/qdrant.py and ``_matches`` in
    stores/memory.py."""
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
            # An empty ``terms`` array matches no documents — the fail-closed
            # reading of "value in []" — so it needs no special-casing.
            filter_clauses.append({"terms": {field: list(value)}})
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
            return
        except ApiError as e:
            if "resource_already_exists_exception" not in str(e):
                raise

        # The index already existed, so `create` never applied _MAPPINGS to it —
        # which would leave every index built before a mapping change permanently
        # on the old template. That is not hypothetical: the ignore_above guard
        # above is worthless on exactly the large, long-lived corpora that hit the
        # 32 KB term limit. Push the template with a mapping update.
        #
        # Only ADDITIVE mapping changes are legal in Elasticsearch; adding
        # `ignore_above` to a keyword is one. A rejected update is logged and
        # swallowed: an unwritable mapping must not stop a read-only caller from
        # constructing the store.
        try:
            await self._es.indices.put_mapping(index=self._index, body=_MAPPINGS)
        except ApiError:
            log.warning(
                "could not update mappings on existing index %r; it keeps its "
                "current template (see stores/elasticsearch.py)",
                self._index,
                exc_info=True,
            )

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

    async def list_documents(
        self, tenants: list[str], limit: int = 100, cursor: str | None = None
    ) -> tuple[list[DocumentSummary], str | None]:
        """Distinct documents visible to ``tenants``, via a composite terms
        aggregation on the ``doc_id`` keyword (O(#docs), not O(#chunks) — the
        reason listing goes through the text index rather than scrolling Qdrant).
        A ``top_hits`` sub-agg pulls one chunk per bucket for the document-level
        metadata. Fails closed on an empty ``tenants`` list."""
        if not tenants:
            return [], None
        composite: dict[str, Any] = {
            "size": limit,
            "sources": [{"doc_id": {"terms": {"field": "doc_id"}}}],
        }
        if cursor:
            composite["after"] = {"doc_id": decode_cursor(cursor)}
        resp = await self._es.search(
            index=self._index,
            size=0,
            track_total_hits=False,
            query={"bool": {"filter": [{"terms": {"metadata.tenant_id": list(tenants)}}]}},
            aggs={
                "docs": {
                    "composite": composite,
                    "aggs": {
                        "exemplar": {
                            "top_hits": {
                                "size": 1,
                                "_source": {"includes": ["doc_id", "metadata"]},
                            }
                        }
                    },
                }
            },
        )
        agg = resp.get("aggregations", {}).get("docs", {})
        buckets = agg.get("buckets", [])
        docs = []
        for b in buckets:
            # A top_hits sub-agg can momentarily return zero hits when a bucket's
            # only chunk is deleted mid-aggregation (a concurrent delete_by_query);
            # skip that bucket rather than IndexError on hits[0]. Pagination is
            # unaffected — the cursor is driven by the raw bucket count / after_key.
            hits = b["exemplar"]["hits"]["hits"]
            if not hits:
                continue
            docs.append(
                document_from_chunk_metadata(
                    b["key"]["doc_id"],
                    int(b["doc_count"]),
                    dict(hits[0]["_source"].get("metadata") or {}),
                )
            )
        # Composite returns an after_key whenever it emitted buckets, including on
        # the final full page; only advance the cursor when the page was full, so
        # a short page terminates. (A total that's an exact multiple of ``limit``
        # yields one final empty page — standard composite-pagination behaviour.)
        after_key = agg.get("after_key")
        next_cursor = (
            # doc_id is keyword-typed so ES returns a string; str() guards a
            # non-string after_key rather than letting encode_cursor AttributeError.
            encode_cursor(str(after_key["doc_id"]))
            if after_key and len(buckets) == limit
            else None
        )
        return docs, next_cursor

    async def healthcheck(self) -> None:
        """Read-only liveness probe: cluster info, no mutation. Unlike
        ``ensure_index`` (which *creates* the index), this only confirms the
        server is reachable, so a health probe can't provision infrastructure.
        Raises on an unreachable server."""
        await self._es.info()

    async def drop_index(self) -> bool:
        """Delete the entire index — every document, every tenant.

        The nuclear counterpart to :meth:`ensure_index`, used only by the
        collection purge (``DELETE /v1/collections/{id}?purge=true``). Not
        tenant-scoped, by design: it removes the index itself, not rows in it.

        Idempotent — a missing index returns ``False`` rather than raising, so a
        purge can report "already gone" instead of failing. Any other API error
        propagates so the purge reports it as a real failure.
        """
        from elasticsearch import ApiError

        try:
            await self._es.indices.delete(index=self._index)
        except ApiError as e:
            if getattr(e, "status_code", None) == 404 or "index_not_found_exception" in str(e):
                return False
            raise
        return True

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
