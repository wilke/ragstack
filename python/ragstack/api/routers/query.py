"""Query and retrieve endpoints."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from ragstack.api.access import enforce_access
from ragstack.api.collections import (
    CollectionEntry,
    CollectionRegistry,
    is_reserved_collection_id,
)
from ragstack.api.deps import (
    build_generator_for,
    build_reranker_for,
    get_collections,
    get_generator,
    get_http_client,
    get_model_registry,
    get_reranker,
    get_rewriters,
    get_tenant_quota,
)
from ragstack.api.model_registry import ModelRegistry, RegistryError
from ragstack.api.scope import shared_scope
from ragstack.api.security import Principal, resolve_principal, resolve_tenant
from ragstack.config import settings
from ragstack.models import ContextChunk, ScoredChunk, Source
from ragstack.protocols import QueryRewriter
from ragstack.retrieval.retriever import (
    STAMP_KEY,
    CollectionLeg,
    MultiCollectionRetriever,
    expand_context,
)
from ragstack.scoring.scorers import RRFScorer
from ragstack.stores.filters import UnknownFilterKey
from ragstack.tenancy import allowed_collection_ids, scope_filters

log = logging.getLogger(__name__)
router = APIRouter()

# Stateless RRF fusion for combining per-rewrite ranked lists.
_RRF = RRFScorer(k=settings.rrf_k)

# Hard cap on ``context_window`` (issue #322): each hop is one batched store
# round trip, and three chunks either side is already more than an answer
# prompt can use. Part of the contract (contracts/schemas/*_request.json).
MAX_CONTEXT_WINDOW = 3

# Hard cap on ``collections`` (issue #253): one retrieval leg per member, so N
# bounds the fan-out (N × per-leg depth candidates into ONE rerank). Five is
# the per-owner collection quota (#290) — what one user can own. Part of the
# contract (``maxItems`` in contracts/schemas/*_request.json).
MAX_QUERY_COLLECTIONS = 5


async def _expand_query(
    query: str, strategies: list[str], rewriters: dict[str, QueryRewriter]
) -> list[str]:
    """Expand the query into retrieval variants per the requested strategies.

    Always includes the original query. Unknown or unavailable strategies (e.g.
    an LLM strategy with no LLM configured) are skipped, and a rewriter that
    raises is skipped too — so retrieval degrades to the plain query rather than
    failing the request. Variants are de-duplicated, original first.
    """
    variants: list[str] = [query]
    seen = {query}
    for name in strategies:
        rewriter = rewriters.get(name)
        if rewriter is None:
            continue
        try:
            produced = await rewriter.rewrite(query)
        except asyncio.CancelledError:
            raise  # never swallow cancellation (client disconnect / timeout)
        except Exception:
            log.warning("query rewriter %r failed; skipping", name, exc_info=True)
            continue
        for v in produced:
            v = (v or "").strip()
            if v and v not in seen:
                seen.add(v)
                variants.append(v)
    return variants


def _bound_top_k(v: int) -> int:
    """Shared ``top_k`` ceiling for :class:`QueryRequest` and
    :class:`RetrieveRequest` (issue #87): an unbounded ``top_k`` lets one
    request force an arbitrarily large fusion/rerank pool. Reads
    ``settings.max_top_k`` at VALIDATION time, not import time, so it tracks a
    settings override made after these classes were defined (module import is
    long since done by the time a request arrives) — unlike the ``limit``
    bounds declared as ``Query(..., le=settings.max_list_limit)`` elsewhere
    (documents.py, service_accounts.py, graph.py), which ARE baked in at
    import/route-registration time. ``max_top_k <= 0`` disables the bound."""
    limit = settings.max_top_k
    if limit > 0 and v > limit:
        raise ValueError(f"top_k must be <= {limit} (got {v})")
    return v


def _check_collections(collection: str | None, collections: list[str] | None) -> None:
    """Shared ``collections`` rules for both request models (issue #253),
    beyond what the field bounds (1–5 items) already enforce: it is mutually
    exclusive with the singular ``collection``, and its ids are unique
    (``uniqueItems`` in the contract) — a duplicate would be the same leg
    twice, double-counting every hit in the fusion. Each violation is a 422."""
    if collections is None:
        return
    if collection is not None:
        raise ValueError(
            "collection and collections are mutually exclusive; pass one id as "
            "collection or several as collections"
        )
    if len(set(collections)) != len(collections):
        raise ValueError("collections must not contain duplicates")


class QueryRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1)
    rewrite_strategies: list[str] = Field(default_factory=lambda: ["passthrough"])
    filters: dict[str, Any] = Field(default_factory=dict)
    use_graph: bool = True
    stream: bool = False
    # Per-request rerank control. ``None`` preserves the server-wide default
    # (rerank iff a reranker is wired). ``False`` forces a rerank skip even when
    # one is available; ``True`` is a no-op when none is wired (graceful).
    rerank: bool | None = None
    # Override for the candidate-pool depth fed to the reranker. ``None`` uses
    # the server default (``max(top_k, settings.rerank_candidates)``).
    rerank_candidates: int | None = Field(default=None, ge=1)
    # Which registry collection to query. ``None`` uses the default collection.
    # An unknown id is a 404 (explicit selection fails loudly). See GET /v1/collections.
    collection: str | None = None
    # Multi-collection fused retrieval (issue #253): 1–5 unique registry ids,
    # one single-collection leg each, RRF-fused, reranked once. Mutually
    # exclusive with ``collection`` (both → 422); see _validate_collections.
    collections: list[str] | None = Field(
        default=None, min_length=1, max_length=MAX_QUERY_COLLECTIONS
    )
    # Retrieval legs: hybrid (dense + BM25, RRF-fused), vector (dense only), or
    # bm25 (sparse only). The graph leg is orthogonal (see use_graph).
    retrieval_mode: Literal["hybrid", "vector", "bm25"] = "hybrid"
    # Server-side context expansion (issue #322): after fusion, rerank and the
    # top_k cut, walk each source's prev/next links up to this many hops each
    # way and attach the neighbours as ``Source.context`` (ranking unchanged).
    # 0 (default) leaves the response exactly as before; the cap is a 422.
    context_window: int = Field(default=0, ge=0, le=MAX_CONTEXT_WINDOW)
    # Per-request model overrides (Phase 2): a registered model id to use for THIS
    # request only, without touching the global assignment. ``llm`` overrides the
    # answer generator (retrieval, incl. rewriting, is unchanged — a clean A/B of
    # generation); ``reranker`` overrides the cross-encoder. Unknown id → 404;
    # wrong-task id → 400. See GET /v1/models/available.
    llm: str | None = None
    reranker: str | None = None

    @field_validator("top_k")
    @classmethod
    def _validate_top_k(cls, v: int) -> int:
        return _bound_top_k(v)

    @model_validator(mode="after")
    def _validate_collections(self) -> QueryRequest:
        _check_collections(self.collection, self.collections)
        return self


class QueryResponse(BaseModel):
    answer: str
    sources: list[Source]
    rewritten_queries: list[str]


class RetrieveRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1)
    filters: dict[str, Any] = Field(default_factory=dict)
    use_graph: bool = True
    # See QueryRequest for semantics — same per-request rerank control.
    rerank: bool | None = None
    rerank_candidates: int | None = Field(default=None, ge=1)
    # See QueryRequest — which registry collection to retrieve from.
    collection: str | None = None
    # See QueryRequest — multi-collection fused retrieval (#253).
    collections: list[str] | None = Field(
        default=None, min_length=1, max_length=MAX_QUERY_COLLECTIONS
    )
    # See QueryRequest — hybrid | vector | bm25.
    retrieval_mode: Literal["hybrid", "vector", "bm25"] = "hybrid"
    # See QueryRequest — same server-side context expansion.
    context_window: int = Field(default=0, ge=0, le=MAX_CONTEXT_WINDOW)
    # See QueryRequest — per-request cross-encoder override (no generation here).
    reranker: str | None = None

    @field_validator("top_k")
    @classmethod
    def _validate_top_k(cls, v: int) -> int:
        return _bound_top_k(v)

    @model_validator(mode="after")
    def _validate_collections(self) -> RetrieveRequest:
        _check_collections(self.collection, self.collections)
        return self


class RetrieveResponse(BaseModel):
    sources: list[Source]


class ChunkOut(BaseModel):
    """A chunk fetched directly by id (no retrieval score) — used for context
    expansion around a retrieved source."""

    doc_id: str
    chunk_id: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChunksResponse(BaseModel):
    chunks: list[ChunkOut]


def _shaping_active(retriever: Any) -> bool:
    """Does ``retriever`` apply post-fusion shaping (per-doc cap / boilerplate
    demotion)? Duck-typed: tests and library callers inject retrievers that
    predate ``shape``, and a missing attribute must mean "no shaping", never an
    AttributeError mid-request."""
    if not callable(getattr(retriever, "shape", None)):
        return False
    return bool(getattr(retriever, "max_per_doc", 0) > 0) or bool(
        getattr(retriever, "demote_boilerplate", False)
    )


def _fans_out(retriever: Any) -> bool:
    """Is ``retriever`` a multi-collection fan-out over more than one leg (#253)
    — i.e. does its ``retrieve`` return a fused UNION wider than the depth it
    was asked for? One leg returns the leg's own list, exactly ``depth``."""
    return isinstance(retriever, MultiCollectionRetriever) and len(retriever.legs) > 1


async def _maybe_rerank(
    reranker, query: str, scored: list[ScoredChunk], top_k: int | None
) -> list[ScoredChunk]:
    """Rescore the fused candidate pool with the cross-encoder, if one is wired.

    Only the top ``top_k`` are kept downstream, so we ask the sidecar to return
    just those — it scores the whole pool either way, but the response payload
    shrinks from the full pool to ``top_k``. ``top_k=None`` asks for the whole
    ranked pool instead, which is what the caller needs when a post-rerank
    shaping pass still has to choose from it.

    Degrades gracefully: a reranker outage / bad response falls back to the
    fused order rather than failing the request (same contract as the LLM and
    rewriter stages). Cancellation is never swallowed."""
    if reranker is None or not scored:
        return scored
    try:
        reranked = await reranker.score(query, [s.chunk for s in scored], top_k=top_k)
        return _restamp(scored, reranked)
    except asyncio.CancelledError:
        raise
    except (KeyError, ValueError) as e:
        # A malformed/invalid sidecar response is a contract bug, not a transient
        # outage — log it distinctly (ERROR) so it isn't lost among flaky-network
        # warnings. Still degrade to the fused order.
        log.error("rerank response invalid (%s); using fused order", e, exc_info=True)
        return scored
    except Exception:
        log.warning("rerank unavailable; using fused order", exc_info=True)
        return scored


def _restamp(pool: list[ScoredChunk], reranked: list[ScoredChunk]) -> list[ScoredChunk]:
    """Carry each candidate's ``collection`` stamp (issue #253) across a
    rerank, which rebuilds ``ScoredChunk``s from the bare chunks it was handed.
    The stamp is read from the chunk's own metadata (``STAMP_KEY``, written on
    the per-leg copy by the multi-collection wrapper), so it survives a scorer
    that copies or rebuilds its chunks — the ``Scorer`` protocol does not
    promise the same objects back — and is unambiguous for a document present
    in two collections (each copy carries its own key). Object identity is
    only the fallback for a chunk that somehow lost its metadata. A pool with
    no stamps at all (the single-collection path) is returned untouched."""
    if not any(s.collection is not None for s in pool):
        return reranked
    by_obj = {id(s.chunk): s.collection for s in pool}
    out = []
    for r in reranked:
        cid = r.chunk.metadata.get(STAMP_KEY) or by_obj.get(id(r.chunk))
        out.append(r if r.collection == cid else r.model_copy(update={"collection": cid}))
    return out


async def _retrieve_fused(
    retriever,
    reranker,
    query: str,
    variants: list[str],
    top_k: int,
    filters: dict[str, Any],
    use_graph: bool,
    rerank: bool | None = None,
    rerank_candidates: int | None = None,
    tenant_id: str | None = None,
    mode: str = "hybrid",
) -> list[ScoredChunk]:
    """Hybrid-retrieve each query variant, RRF-fuse, optionally rerank, truncate.

    When a reranker is active the per-variant retrievals fetch a deeper pool
    (``rerank_candidates``) so the cross-encoder has real recall to work with;
    the final cut to ``top_k`` happens after reranking. With no reranker this is
    exactly the previous behaviour (retrieve top_k, fuse, slice).

    Per-request overrides (issue #27):
      * ``rerank=False`` skips reranking even when a reranker is wired — the
        pool is then a shallow ``top_k`` (no deep fetch). ``rerank=True`` is a
        no-op when none is wired (graceful: can't conjure a reranker).
      * ``rerank_candidates`` overrides the pool depth used when reranking.
      * Both ``None`` (the default) preserve the prior server-wide behaviour.
    """
    active = reranker if rerank is not False else None
    if active is not None:
        pool = rerank_candidates if rerank_candidates is not None else settings.rerank_candidates
        depth = max(top_k, pool)
    else:
        depth = top_k
    if len(variants) == 1:
        scored = await retriever.retrieve(
            variants[0], top_k=depth, filters=filters, use_graph=use_graph,
            tenant_id=tenant_id, mode=mode,
        )
    else:
        # Independent retrievals run concurrently — latency is one retrieve, not N.
        ranked = await asyncio.gather(
            *(
                retriever.retrieve(
                    v, top_k=depth, filters=filters, use_graph=use_graph,
                    tenant_id=tenant_id, mode=mode,
                )
                for v in variants
            )
        )
        scored = _RRF.fuse(list(ranked))
    # Multi-collection fan-out (#253): the wrapper returns the fused UNION of
    # its legs (up to N × depth candidates). The rerank pool is a cost the
    # caller budgets with ``rerank_candidates`` (the pool "fed to the
    # reranker", per the contract), so the union is cut to ``depth`` here —
    # after fusion, before the one rerank. A no-op at N=1 (the leg returned
    # exactly ``depth``) and on the singular path, and skipped without a
    # reranker, where shaping still gets the whole union to promote from and
    # the final cut is ``top_k`` below. Per-collection recall into the pool is
    # roughly depth/N under RRF interleaving; raise ``rerank_candidates`` for
    # more. Only the fan-out is cut: a single retriever's pool is whatever it
    # returned for ``depth`` (test stubs may hand back more), exactly as before.
    if active is not None and _fans_out(retriever):
        scored = scored[:depth]
    # Post-fusion shaping (per-document cap / boilerplate demotion) has to be the
    # LAST step before the top_k cut: reranking re-sorts the whole pool and would
    # otherwise undo it. When shaping is active we therefore keep the reranker's
    # full ranked pool instead of its top_k, so the shaping has candidates to
    # promote from; with shaping off this is byte-for-byte the previous path.
    shaping = _shaping_active(retriever)
    scored = await _maybe_rerank(active, query, scored, None if shaping else top_k)
    if shaping:
        scored = retriever.shape(scored)
    return scored[:top_k]


#: Context-expansion key: (collection stamp, chunk id) — the same identity RRF
#: fusion uses, so a document present in two collections keeps its neighbours
#: apart per collection. ``None`` stamp on the single-collection path.
_SourceKey = tuple[str | None, str]


def _to_sources(
    scored: list[ScoredChunk],
    context: dict[_SourceKey, list[ContextChunk]] | None = None,
) -> list[Source]:
    """Sources in ``scored`` order. ``context`` (from :func:`_expand_sources`)
    is keyed by (collection, chunk id); a source with no entry gets no
    ``context`` key at all (omitted from the response — see ``Source``), never
    an empty list. ``collection`` is the multi-collection stamp (#253), omitted
    the same way when absent."""
    context = context or {}
    return [
        Source(
            doc_id=r.chunk.doc_id,
            chunk_id=r.chunk.id,
            content=r.chunk.content,
            score=r.score,
            metadata=_source_metadata(r.chunk),
            context=context.get((r.collection, r.chunk.id)),
            collection=r.collection,
        )
        for r in scored
    ]


async def _expand_sources(
    targets: dict[str | None, tuple[Any, dict[str, Any]]],
    scored: list[ScoredChunk],
    window: int,
) -> dict[_SourceKey, list[ContextChunk]]:
    """Server-side context expansion (issue #322) for the final, already
    reranked and truncated ``scored`` list: the neighbours to attach, per
    (collection, chunk id). Runs AFTER ``_retrieve_fused`` so it can't touch
    the ranking. ``targets`` maps each collection stamp — ``None`` on the
    single-collection path, the registry id per member on a multi-collection
    one (#253) — to ``(vector_store, scoped filters)``: the SAME store and the
    SAME scoped filter dict that collection's retrieval leg used, so a
    neighbour outside the caller's scope in that collection is never returned
    (#197), and a source's neighbours are only ever looked up in the
    collection it came from. One ``expand_context`` per collection, run
    concurrently: at most ``N × window`` batched ``get_chunks`` calls
    (``≤ 5 × 3``). ``window=0`` (the default) is a no-op with no store call.
    A refused filter key is a 400, exactly as ``GET /v1/chunks`` answers it."""
    if window <= 0 or not scored:
        return {}
    jobs: list[tuple[str | None, Any]] = []
    for cid, (store, filters) in targets.items():
        subset = [s for s in scored if s.collection == cid]
        if store is None or not subset:
            continue
        jobs.append((cid, expand_context(store, subset, window, filters)))
    try:
        results = await asyncio.gather(*(job for _, job in jobs))
    except UnknownFilterKey as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    out: dict[_SourceKey, list[ContextChunk]] = {}
    for (cid, _), found in zip(jobs, results, strict=True):
        for chunk_id, ctx in found.items():
            out[(cid, chunk_id)] = ctx
    return out


def _source_metadata(chunk: Any) -> dict[str, Any]:
    """The chunk's metadata plus its char offsets into the ORIGINAL document.

    ``start_char``/``end_char`` live as fields on the Chunk (popped out of the
    stored payload), not in ``metadata`` — but the UI needs them to measure
    cross-chunker *passage-span overlap* (whether two lanes with different
    chunkers surfaced the same region of a document, which doc_id matching can't
    tell). Attach them only when meaningful (``end > start``); chunks/stores
    without offsets leave them absent."""
    md = dict(chunk.metadata)
    # The multi-collection stamp (#253) rides on the chunk copy's metadata only
    # to survive the reranker; it is reported as ``Source.collection``, never
    # as a metadata key (the single-collection golden guards this).
    md.pop(STAMP_KEY, None)
    if chunk.end_char > chunk.start_char:
        md.setdefault("start_char", chunk.start_char)
        md.setdefault("end_char", chunk.end_char)
    return md


async def tenant_slot(
    tenant: str = Depends(resolve_tenant),
    quota=Depends(get_tenant_quota),
) -> AsyncIterator[str]:
    """Resolve the caller's tenant and hold one of its concurrency slots for the
    whole request — admission control so one tenant can't monopolize the shared
    embedding fleet. Yields the tenant for read-scoping."""
    async with quota.slot(tenant):
        yield tenant


def _effective_collection(
    registry: CollectionRegistry, collection: str | None, tenant: str
) -> str | None:
    """Apply the per-tenant collection allowlist, returning the id to resolve.

    Unrestricted tenants pass through unchanged. A restricted tenant may only name
    a collection in its set (else 404 — same as an unknown id, so membership isn't
    leaked); when it names none, it gets its own default (the registry default if
    permitted, else its first allowed collection present in the registry).

    Naming the pointer (``collection="default"``) is the same as omitting it:
    ``default`` is not a collection, it is the name of the resolution (#276)."""
    if is_reserved_collection_id(collection):
        collection = None
    allowed = registry.permitted(allowed_collection_ids(tenant, settings.tenant_collections))
    if allowed is None:
        return collection
    if collection is not None:
        if collection not in allowed:
            raise HTTPException(
                status_code=404,
                detail=f"unknown collection {collection!r}; see GET /v1/collections",
            )
        return collection
    if registry.default_id in allowed:
        return registry.default_id
    present = [e.id for e in registry.entries() if e.id in allowed]
    if not present:
        raise HTTPException(
            status_code=404, detail="no collection is accessible to this caller"
        )
    return sorted(present)[0]


async def _resolve_entry(
    registry: CollectionRegistry, collection: str | None, principal: Principal
):
    """The registry entry for the selected collection (the caller's default when
    None), after applying the per-tenant allowlist AND the ownership check.

    An unknown or out-of-scope id is a 404 — explicit selection fails loudly
    rather than serving the wrong corpus. A collection the caller may not READ is
    a 404 too (the ownership seam, :func:`enforce_access`): membership is never
    leaked, so "you can't read it" is indistinguishable from "it doesn't exist"."""
    effective = _effective_collection(registry, collection, principal.tenant)
    try:
        entry = registry.resolve(effective)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"unknown collection {collection!r}; see GET /v1/collections",
        ) from None
    await enforce_access(principal, entry.id, "read")
    return entry


async def _resolve_retrieval(
    registry: CollectionRegistry,
    collection: str | None,
    collections: list[str] | None,
    principal: Principal,
    tenant: str,
    filters: dict[str, Any],
) -> tuple[Any, dict[str, Any], dict[str | None, tuple[Any, dict[str, Any]]]]:
    """What a retrieve/query request runs over: ``(retriever, filters,
    expansion targets)``.

    Single collection (``collections`` absent): exactly the pre-#253 path —
    the entry's own retriever and one scoped filter dict, keyed ``None``.

    Multi-collection (``collections`` given): EVERY id is resolved through the
    registry, the tenant allowlist and ``enforce_access(read)`` — in request
    order, BEFORE any retrieval leg runs. The first refusal is the answer for
    the whole request: unknown / unreadable → 404 (the read seam never
    distinguishes the two, so membership is not leaked — same as the singular
    path), a dormant or restoring member → 503 + ``Retry-After`` (the
    lifecycle gate inside ``enforce_access`` submits that member's restore
    exactly as a single-collection read would; nothing else runs, no partial
    answer), a lost member → 409. Two ids that resolve to the same entry (the
    ``default`` pointer next to its target) are the same leg twice → 422.
    Share-based scope widening (:func:`shared_scope`) is computed ONCE per
    member and reused for both its retrieval leg and its context expansion.
    The result is a :class:`MultiCollectionRetriever` over the members' own
    retrievers — one single-collection leg each — and the caller's UNSCOPED
    filters (the wrapper scopes them per leg); the graph leg, when a graph
    store is wired, is one neighbourhood query across the members' physical
    collections."""
    if collections is None:
        entry = await _resolve_entry(registry, collection, principal)
        scoped = scope_filters(filters, tenant, await shared_scope(entry, registry, principal))
        return entry.retriever, scoped, {None: (entry.vector_store, scoped)}
    entries: list[CollectionEntry] = []
    for cid in collections:
        entries.append(await _resolve_entry(registry, cid, principal))
    if len({e.id for e in entries}) != len(entries):
        raise HTTPException(
            status_code=422,
            detail="collections: two ids resolve to the same collection",
        )
    legs: list[CollectionLeg] = []
    targets: dict[str | None, tuple[Any, dict[str, Any]]] = {}
    for entry in entries:
        extra = await shared_scope(entry, registry, principal)
        legs.append(
            CollectionLeg(
                id=entry.id,
                retriever=entry.retriever,
                physical=entry.collection,
                vector_store=entry.vector_store,
                extra_tenants=extra,
            )
        )
        targets[entry.id] = (entry.vector_store, scope_filters(filters, tenant, extra))
    first = entries[0].retriever
    retriever = MultiCollectionRetriever(
        legs,
        graph_store=getattr(first, "graph_store", None),
        rrf_scorer=RRFScorer(k=settings.rrf_k),
        graph_context_score=getattr(first, "graph_context_score", settings.graph_context_score),
        graph_context_depth=getattr(first, "graph_context_depth", settings.graph_context_depth),
    )
    return retriever, filters, targets


def _override_model(builder, models: ModelRegistry, http, model_id: str | None, default):
    """Per-request model override: build from the registered ``model_id`` when
    given (via ``builder`` — build_generator_for / build_reranker_for), else return
    the server default. Resolution errors carry their HTTP status on the
    ``RegistryError`` (unknown id → 404, wrong-task model → 400), so the taxonomy
    stays single-sourced in the registry rather than re-derived here."""
    if not model_id:
        return default
    try:
        return builder(models, http, model_id)
    except RegistryError as e:
        detail = str(e)
        if e.status_code == 404:
            detail += "; see GET /v1/models/available"
        raise HTTPException(status_code=e.status_code, detail=detail) from None


@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve(
    request: RetrieveRequest,
    tenant: str = Depends(tenant_slot),
    principal: Principal = Depends(resolve_principal),
    registry: CollectionRegistry = Depends(get_collections),
    reranker=Depends(get_reranker),
    models: ModelRegistry = Depends(get_model_registry),
    http=Depends(get_http_client),
) -> RetrieveResponse:
    """Retrieve relevant chunks (caller's tenant + public) via hybrid
    vector + BM25 retrieval (+ optional cross-encoder rerank), without
    generating an answer."""
    retriever, filters, targets = await _resolve_retrieval(
        registry, request.collection, request.collections, principal, tenant, request.filters
    )
    reranker = _override_model(build_reranker_for, models, http, request.reranker, reranker)
    scored = await _retrieve_fused(
        retriever,
        reranker,
        request.query,
        [request.query],
        request.top_k,
        filters,
        request.use_graph,
        rerank=request.rerank,
        rerank_candidates=request.rerank_candidates,
        tenant_id=tenant,
        mode=request.retrieval_mode,
    )
    context = await _expand_sources(targets, scored, request.context_window)
    return RetrieveResponse(sources=_to_sources(scored, context))


@router.get("/chunks", response_model=ChunksResponse)
async def get_chunks(
    ids: str = "",
    collection: str | None = None,
    tenant: str = Depends(tenant_slot),
    principal: Principal = Depends(resolve_principal),
    registry: CollectionRegistry = Depends(get_collections),
) -> ChunksResponse:
    """Fetch chunks by id from a collection's vector store (tenant-scoped).

    ``ids`` is a comma-separated list — typically the ``prev_chunk_id`` /
    ``next_chunk_id`` carried in a Source's metadata — so a client can expand a
    retrieved chunk's neighbouring context. Unknown or out-of-scope ids are
    silently omitted; order follows the request. More than ``max_chunk_ids``
    entries is a 422 (issue #87) — rejected outright rather than silently
    truncated, so a caller relying on the tail of a long list finds out instead
    of getting a quietly incomplete response. ``collection`` selects the
    registry collection (default when omitted); an unknown id 404s."""
    id_list = [x for x in (i.strip() for i in ids.split(",")) if x]
    max_ids = settings.max_chunk_ids
    if max_ids > 0 and len(id_list) > max_ids:
        raise HTTPException(
            status_code=422,
            detail=f"ids: at most {max_ids} allowed (got {len(id_list)})",
        )
    if not id_list:
        return ChunksResponse(chunks=[])
    entry = await _resolve_entry(registry, collection, principal)
    store = entry.vector_store
    if store is None:  # pragma: no cover - all wired entries carry a store
        return ChunksResponse(chunks=[])
    try:
        chunks = await store.get_chunks(
            id_list, scope_filters({}, tenant, await shared_scope(entry, registry, principal))
        )
    except UnknownFilterKey as e:
        # Refuse rather than silently ignore an unsupported scope key (#197) —
        # a store's shared predicate rejected it before any filtering happened.
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ChunksResponse(
        chunks=[
            ChunkOut(doc_id=c.doc_id, chunk_id=c.id, content=c.content, metadata=c.metadata)
            for c in chunks
        ]
    )


def _fallback_answer(prefix: str, query_text: str, sources: list[Source]) -> str:
    """A non-generated answer (no LLM configured, or generation failed) that still
    surfaces what was retrieved so the caller gets the sources, not just an error."""
    if sources:
        return (
            f"{prefix} retrieved {len(sources)} chunks for query "
            f"{query_text!r}; top score {sources[0].score:.4f}"
        )
    return f"{prefix} no relevant chunks found for query {query_text!r}"


@router.post("/query", response_model=QueryResponse)
async def query(
    request: QueryRequest,
    tenant: str = Depends(tenant_slot),
    principal: Principal = Depends(resolve_principal),
    registry: CollectionRegistry = Depends(get_collections),
    generator=Depends(get_generator),
    rewriters=Depends(get_rewriters),
    reranker=Depends(get_reranker),
    models: ModelRegistry = Depends(get_model_registry),
    http=Depends(get_http_client),
) -> QueryResponse:
    """Full RAG flow: optionally rewrite the query (HyDE / multi-query), hybrid-
    retrieve for each variant (caller's tenant + public), fuse with RRF,
    optionally cross-encoder rerank, and generate a grounded answer. When no LLM
    endpoint is configured the answer is a retrieval-only placeholder.

    Per-request ``llm`` / ``reranker`` overrides swap those clients for this
    request only (the global assignment is untouched) — the corpus and, for the
    llm override, the retrieval path stay fixed, so it's a clean A/B.
    """
    generator = _override_model(build_generator_for, models, http, request.llm, generator)
    reranker = _override_model(build_reranker_for, models, http, request.reranker, reranker)
    retriever, filters, targets = await _resolve_retrieval(
        registry, request.collection, request.collections, principal, tenant, request.filters
    )
    variants = await _expand_query(request.query, request.rewrite_strategies, rewriters)
    scored = await _retrieve_fused(
        retriever,
        reranker,
        request.query,
        variants,
        request.top_k,
        filters,
        request.use_graph,
        rerank=request.rerank,
        rerank_candidates=request.rerank_candidates,
        tenant_id=tenant,
        mode=request.retrieval_mode,
    )

    # Context expansion is strictly post-rank: it reads the final ``scored``
    # list and only decorates the sources. The generator sees the decorated
    # sources, so with ``context_window > 0`` the answer is grounded in each
    # passage plus its neighbours (see RagGenerator._format_context).
    context = await _expand_sources(targets, scored, request.context_window)
    sources = _to_sources(scored, context)
    if generator is None:
        answer = _fallback_answer("[LLM not configured]", request.query, sources)
    else:
        try:
            answer = await generator.generate(request.query, sources)
        except Exception:
            # Retrieval already succeeded — don't fail the whole query on an LLM
            # outage or a malformed/empty response. Return the sources with a note.
            log.warning("answer generation failed; returning sources only", exc_info=True)
            answer = _fallback_answer("[answer generation failed]", request.query, sources)
    return QueryResponse(
        answer=answer,
        sources=sources,
        rewritten_queries=variants,
    )
