"""Elasticsearch-backed TextIndex (BM25), tenant-scoped.

Mirrors the Qdrant store's tenancy: a chunk's ES document id is scoped by tenant
(``tenant:chunk_id``) so the same source under two tenants yields distinct docs,
and searches filter to the caller's readable tenants. The import of the
elasticsearch client is lazy so the optional ``text`` extra is only required when
this backend is actually selected.
"""
from __future__ import annotations

import dataclasses
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from time import perf_counter
from typing import Any

from ragstack.documents import (
    DocumentSummary,
    decode_cursor,
    document_from_chunk_metadata,
    encode_cursor,
)
from ragstack.models import Chunk, ScoredChunk
from ragstack.stores.errors import (
    KIND_ERROR,
    KIND_TIMEOUT,
    KIND_UNREACHABLE,
    StoreUnavailable,
)
from ragstack.tenancy import DEFAULT_TENANT

log = logging.getLogger(__name__)

#: The client's own per-request timeout when ``ELASTICSEARCH_TIMEOUT`` is unset,
#: in seconds, as observed on elasticsearch 8.19.3 / elastic_transport 8.19.0.
#:
#: This is the **fallback**, not the source of truth:
#: :func:`_client_default_timeout_s` reads the live value out of
#: ``NodeConfig``'s dataclass field default, so a library bump changes the
#: message instead of quietly making it false. Hardcoding it would break the
#: exact standard this work is built on — "name the applied bound, never a
#: setting the client ignores" — while every test stayed green. A test pins this
#: constant against the library so a bump is also *noticed*, not just absorbed.
_CLIENT_DEFAULT_TIMEOUT_FALLBACK_S = 10.0


@lru_cache(maxsize=1)
def _client_default_timeout_s() -> float:
    """The per-request timeout the elasticsearch client applies when we pass
    none. Read from ``elastic_transport.NodeConfig``'s field default.

    Imported lazily (and cached) to preserve this module's rule that the
    optional ``text`` extra is only required when this backend is selected. The
    fallback covers a future layout change in the library rather than letting an
    ``AttributeError`` escape from an error path — the one place an exception is
    least welcome.
    """
    try:
        from elastic_transport import NodeConfig

        for f in dataclasses.fields(NodeConfig):
            if f.name == "request_timeout" and isinstance(f.default, (int, float)):
                return float(f.default)
    except Exception:  # noqa: BLE001 — never raise from inside a failure message
        pass
    return _CLIENT_DEFAULT_TIMEOUT_FALLBACK_S

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

# Bulk-request sizing. ES rejects a body over `http.max_content_length` (100 MB by
# default) with a bare HTTP 413 — no per-item errors, nothing indexed. Cap well
# under it: the per-chunk estimate below counts raw content length only, so it
# undercounts JSON escaping, the action line before each document, and the
# metadata block. A generous margin is much cheaper than a 413.
_BULK_MAX_BYTES = 20 * 1024 * 1024
_BULK_BATCH_SIZE = 500
# Flat allowance per document for the action line + metadata + field names. Chunk
# content dominates for real text; this keeps small-content chunks from looking
# free and letting the count cap alone drive an oversized body.
_METADATA_BYTES_ESTIMATE = 2048

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


def _failure_kind(e: BaseException) -> str | None:
    """Classify an Elasticsearch failure, or ``None`` for "not a store outage".

    ``None`` is the important return value. Qdrant's adapter converts *every*
    ``ApiException`` to a 503, which is defensible there because the wrapped
    client raises it only for transport failures and non-2xx responses. The ES
    client is different: ``ApiError`` also covers ``index_not_found_exception``
    (404) and a malformed query (400), which are the **caller's** problem.
    Reporting those as "elasticsearch unavailable, retry in 5s" would be a
    downgrade from today's behaviour, not parity — so a 4xx returns ``None`` and
    propagates exactly as it does now.

    The import is inside the function to preserve this module's lazy-import rule
    (the optional ``text`` extra is only required when this backend is selected);
    by the time this is called, the client has already been imported and used.

    .. rubric:: The ES ``timeout`` branch is coarser than the Qdrant one, and
       W6's retry copy must not over-promise because of it

    ``ConnectionTimeout`` is **not** a subclass of ``ConnectionError`` in
    ``elastic_transport`` 8.19, so the first two branches below are disjoint **as
    classes** — there is no ordering trap of the kind httpx has, where
    ``ConnectTimeout`` derives from ``TimeoutException``.

    They are **not** disjoint semantically, and that is the honest limitation of
    this mapping. ``elastic_transport``'s aiohttp node builds
    ``aiohttp.ClientTimeout(total=request_timeout)`` — a *total* bound with no
    separate connect bound — and maps both ``asyncio.TimeoutError`` and
    ``aiohttp``'s ``ServerTimeoutError`` onto ``ConnectionTimeout`` (read out of
    ``elastic_transport._node._http_aiohttp``, not assumed). So a
    **connect-phase** timeout — a blackholed address, a host that never
    completes the handshake — arrives here as ``ConnectionTimeout`` and is
    classified ``timeout``, where the Qdrant equivalent
    (``httpx.ConnectTimeout``) is correctly ``unreachable``.

    This is not fixable at this layer: the distinction is destroyed by the
    client before we ever see the exception, and imposing a separate connect
    bound would change request semantics for every caller. What *does* still
    work is the common case — connection refused, DNS failure and TLS errors are
    ``ConnectionError`` and do yield ``unreachable``.

    **Consequence for #427 W6:** the ``timeout`` copy shown for the ES leg must
    be "retrying often succeeds within seconds" and must **never** promise "the
    second read will be warm". That promise is only sound for Qdrant, whose
    client can tell a connect timeout from a read timeout. Written down here
    rather than left for W6 to rediscover.
    """
    from elasticsearch import ApiError, ConnectionError, ConnectionTimeout, TransportError

    if isinstance(e, ConnectionTimeout):
        # Too slow. Usually "accepted, then the search ran long" — but see the
        # rubric above: this also swallows connect-phase timeouts, which is why
        # the ES retry copy may not promise a warm read.
        return KIND_TIMEOUT
    if isinstance(e, ConnectionError):
        # Refused / DNS / TLS (TlsError subclasses this) — we never got there.
        return KIND_UNREACHABLE
    if isinstance(e, ApiError):
        # 429 is deliberately NOT converted, and that is a decision rather than
        # an oversight. `es_rejected_execution_exception` (bulk queue full,
        # circuit breaker tripped) is the classic transient-under-load signal
        # and it does belong in this family — but none of the three kinds
        # describes it. `error` ("the store answered, unhappily") reads as a
        # server fault and would give *worse* advice than the status quo;
        # `timeout` would simply be false. Converting it properly wants a fourth
        # kind (`overloaded`, with back-off advice), and the kind enum is
        # user-visible once W6's UI branches on it — so that is W6's call, not
        # this slice's. Until then a 429 keeps reaching the 500 path exactly as
        # it does today: status quo preserved, no regression, gap written down.
        status = getattr(e, "status_code", None)
        return KIND_ERROR if isinstance(status, int) and status >= 500 else None
    if isinstance(e, TransportError):
        # SerializationError and friends: the store answered unusably.
        return KIND_ERROR
    return None


class ElasticsearchTextIndex:
    """TextIndex protocol backed by Elasticsearch BM25.

    .. rubric:: Why the reads are guarded (#427 W2b)

    Until #427 this class had **no error handling on any read**. An ES timeout on
    the BM25 leg of a hybrid query therefore escaped as a bare HTTP 500 with a
    raw traceback, while the *identical* failure on the vector leg of the same
    query produced a 503 naming the collection, the URL, the error type and the
    timeout knob. The incident that produced #427 happened to hit the
    instrumented leg; had it hit this one there would have been no
    ``qdrant unavailable:`` line to grep and no issue to file. See
    :meth:`_guard`.
    """

    def __init__(self, url: str, index: str, api_key: str | None = None,
                 refresh_on_write: bool = True,
                 bulk_batch_size: int = _BULK_BATCH_SIZE,
                 timeout: float | None = None) -> None:
        from elasticsearch import AsyncElasticsearch

        # `timeout` is appended at the END of the signature rather than slotted
        # next to the other connection arguments. All 12 call sites in the tree
        # in fact use keywords, so nothing would actually have broken — this is
        # convention, not a rescue.
        #
        # When unset we pass nothing and the client keeps its own default, which
        # is 10s per request (see _client_default_timeout_s, which reads it from
        # the library rather than trusting this sentence). That default is a
        # THIRD of the Qdrant bound this deployment runs, and until #427 there
        # was no knob at all on this leg — the interim mitigation that bought the
        # vector leg headroom (QDRANT_TIMEOUT=60) had no counterpart here.
        self._es = (
            AsyncElasticsearch(hosts=url, api_key=api_key or None)
            if timeout is None
            else AsyncElasticsearch(hosts=url, api_key=api_key or None, request_timeout=timeout)
        )
        self._url = url
        self._timeout = timeout
        self._index = index
        self._bulk_batch_size = max(1, bulk_batch_size)
        # Every write forces a synchronous refresh so a subsequent read sees it —
        # read-your-writes, which the interactive API path depends on.
        #
        # It is ruinous for a bulk load. Measured on an 11.9M-doc single-shard
        # index mid-build: 1,355 refreshes in 90 seconds (~15/s, one per bulk and
        # per delete-by-query), consuming 89.1 s of that 90 s window against 1.5 s
        # deleting and 0.0 s indexing. Refresh was ~99% of the wall clock.
        #
        # Note this is NOT the same thing as `index.refresh_interval`, and parking
        # that setting does NOT fix it: an explicit `refresh=true` on a request
        # forces a refresh of the affected shards regardless of the interval. The
        # interval governs only the periodic background refresh. Two different
        # mechanisms; only this one was actually costing us the time.
        self._refresh_on_write = refresh_on_write

    def _describe_failure(self, op: str, e: BaseException) -> str:
        """One readable line: what failed, where, and the knob that bounds it.

        Deliberately the same *shape* as ``QdrantVectorStore._describe_failure``
        — operation, index, URL, error type, applied timeout and its setting name
        — so an operator greps one pattern and gets both legs.
        """
        # str(ConnectionTimeout(...)) is the class's own terse "Connection timed
        # out"; the causal detail ("caused by: TimeoutError()") lives on
        # `.message`. Prefer it, or the message is strictly less useful than the
        # traceback it replaces.
        detail = getattr(e, "message", None) or str(e)
        reason = f"{type(e).__name__}: {detail}".rstrip(": ")
        # `:g` so a float setting renders like Qdrant's int one — 30, not 30.0.
        # Matching the message SHAPE was the point of this whole method: an
        # operator greps one pattern and gets both legs, and `30.0s` vs `30s`
        # quietly breaks that.
        bound = (
            f"{self._timeout:g}s (ELASTICSEARCH_TIMEOUT)"
            if self._timeout is not None
            else (
                f"client default {_client_default_timeout_s():g}s "
                "(ELASTICSEARCH_TIMEOUT unset)"
            )
        )
        return (
            f"elasticsearch {op} on {self._index!r} at {self._url} failed — {reason}; "
            f"per-request timeout is {bound}"
        )

    @contextmanager
    def _guard(self, op: str) -> Iterator[None]:
        """Convert a store outage inside the block into :class:`StoreUnavailable`.

        A **sync** context manager wrapping an ``await``, which is not a mistake:
        ``__exit__`` runs when the awaited call completes or raises, so
        ``with self._guard("search"): resp = await self._es.search(...)`` catches
        exactly what it looks like it catches — and costs no task-group or
        async-generator machinery.

        Keep the block tight around the client call. Anything else inside it
        (response parsing, ``KeyError`` on a shape change) is a bug in this
        module and must keep surfacing as a 500, which is why
        :func:`_failure_kind` returns ``None`` for everything it does not
        positively recognise as an outage.
        """
        started = perf_counter()
        try:
            yield
        except BaseException as e:
            kind = _failure_kind(e)
            if kind is None:
                raise
            raise StoreUnavailable(
                "elasticsearch",
                self._describe_failure(op, e),
                kind=kind,
                elapsed_s=perf_counter() - started,
            ) from e

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

        # The index already existed, so `create` never applied _MAPPINGS to it.
        # Push them so a NEWLY-ENCOUNTERED metadata field on an existing index
        # picks up the bounded template instead of a bare keyword.
        #
        # SCOPE, precisely: a dynamic template governs fields it maps for the
        # FIRST time. Fields already concretely mapped as bare `keyword` keep that
        # mapping and stay vulnerable to the 32 KB term limit — verified against a
        # live ES 8.13.4: after this update, an existing `metadata.title` still
        # rejects a 40 KB value while a brand-new field accepts one. Retrofitting
        # existing fields needs an explicit per-field `properties` update (it IS a
        # legal, updatable parameter) — tracked in #270, not done here.
        #
        # Catches Exception, not ApiError: elasticsearch.ConnectionError is a
        # TransportError, NOT an ApiError, so a narrow catch would let a transient
        # connection blip escape ensure_index() where it previously returned
        # cleanly — and abort startup under require_durable_backends.
        try:
            await self._es.indices.put_mapping(index=self._index, body=_MAPPINGS)
        except Exception:  # noqa: BLE001 — see above; never block store construction
            log.warning(
                "could not update mappings on existing index %r; it keeps its "
                "current template (see stores/elasticsearch.py)",
                self._index,
                exc_info=True,
            )

    async def index(self, chunks: list[Chunk]) -> None:
        """Bulk-index ``chunks``, split into requests ES will actually accept.

        The whole list used to go in ONE bulk request. ES caps a request body at
        ``http.max_content_length`` (100 MB by default) and answers an oversized
        one with a bare **HTTP 413**, so a large shard failed outright — no
        partial write, no per-item error, just a status code.

        That is not hypothetical: a 1.99 GB shard of 38,322 chunks failed exactly
        this way on every load attempt, while the vector store — which has always
        batched at 256 points — took all 38,322. The two legs then disagreed by
        precisely 38,322 documents, and with ``--fail-on-error`` the whole batch
        failed after the other 63 shards had loaded fine.

        Split on BOTH count and accumulated bytes: chunk sizes vary by orders of
        magnitude across a corpus (a figure caption vs a full methods section),
        so a count-only cap still lets a run of large chunks build an oversized
        body. The byte cap is deliberately well under the server limit — the
        estimate ignores JSON escaping and the action lines between documents.
        """
        if not chunks:
            return
        batch: list[Chunk] = []
        nbytes = 0
        for c in chunks:
            approx = len(c.content) + _METADATA_BYTES_ESTIMATE
            if batch and (
                len(batch) >= self._bulk_batch_size or nbytes + approx > _BULK_MAX_BYTES
            ):
                await self._index_batch(batch)
                batch, nbytes = [], 0
            batch.append(c)
            nbytes += approx
        if batch:
            await self._index_batch(batch)

    async def _index_batch(self, chunks: list[Chunk]) -> None:
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
        # refresh so the just-indexed docs are immediately searchable — unless the
        # caller is bulk-loading, where it dominates the wall clock (see __init__).
        #
        # Guarded for the same reason the reads are: POST /v1/ingest is a
        # user-facing request path, and an ES outage mid-ingest was a bare 500
        # here too. The guard is scoped to the round trip ONLY — the errors=true
        # handling below stays a RuntimeError, because a rejected document is a
        # data problem the caller must see, not a transient outage to retry.
        with self._guard("bulk index"):
            resp = await self._es.bulk(operations=operations, refresh=self._refresh_on_write)
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
        # Built OUTSIDE the guard: `_build_query` raises ValueError on a missing
        # tenant filter, which is a fail-closed tenancy guard and must never be
        # reported as "the store is unavailable, retry in 5s".
        body = _build_query(query, filters)
        with self._guard("search"):
            resp = await self._es.search(index=self._index, query=body, size=top_k)
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
        with self._guard("count"):
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
        with self._guard("list_documents"):
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
            refresh=self._refresh_on_write,
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
            refresh=self._refresh_on_write,
            conflicts="proceed",
        )

    async def bulk_load_refresh(self, disable: bool) -> str | None:
        """Disable (or restore) the index refresh interval for a bulk load (#323).

        A default 1-second refresh during a bulk load is pathological: on the
        open-access build the index had spent ~9.6 hours refreshing against ~20
        minutes actually indexing. Disabling it for the load and restoring after is
        the standard remedy.

        ``disable=True`` sets ``refresh_interval=-1`` and returns the PRIOR value so
        the caller can hand it back — ``None`` means the index carried no explicit
        setting and should be reset to the server default. Call with
        ``disable=False`` and that value to restore.

        Caller beware: with refresh off, newly indexed documents are NOT visible to
        search or to ``_count``. Any post-load verification must force a refresh
        first (:meth:`refresh`) or it reads stale.
        """
        if disable:
            prior = None
            try:
                got = await self._es.indices.get_settings(
                    index=self._index, name="index.refresh_interval"
                )
                for body in got.body.values():
                    prior = body.get("settings", {}).get("index", {}).get("refresh_interval")
            except Exception:  # noqa: BLE001 — never fail a load over a settings read
                log.warning("could not read refresh_interval; will restore to default")
            await self._es.indices.put_settings(
                index=self._index, body={"index": {"refresh_interval": "-1"}}
            )
            return prior
        return None

    async def restore_refresh(self, prior: str | None) -> None:
        """Restore what :meth:`bulk_load_refresh` returned. ``None`` resets to the
        server default. Best-effort and never raises: leaving refresh disabled is a
        visible, fixable state, but failing a completed load over it is not."""
        try:
            await self._es.indices.put_settings(
                index=self._index, body={"index": {"refresh_interval": prior}}
            )
        except Exception:  # noqa: BLE001
            log.error(
                "FAILED to restore refresh_interval on %s — the index will not "
                "refresh until this is set manually", self._index, exc_info=True,
            )

    async def refresh(self) -> None:
        """Force a refresh so just-indexed documents become searchable/countable."""
        await self._es.indices.refresh(index=self._index)

    async def close(self) -> None:
        await self._es.close()
