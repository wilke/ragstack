"""Query and retrieve endpoints."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ragstack.api.collections import CollectionRegistry
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
from ragstack.api.model_registry import ModelRegistry
from ragstack.api.security import resolve_tenant
from ragstack.config import settings
from ragstack.models import ScoredChunk, Source
from ragstack.protocols import QueryRewriter
from ragstack.scoring.scorers import RRFScorer
from ragstack.tenancy import scope_filters

log = logging.getLogger(__name__)
router = APIRouter()

# Stateless RRF fusion for combining per-rewrite ranked lists.
_RRF = RRFScorer(k=settings.rrf_k)


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
    # Retrieval legs: hybrid (dense + BM25, RRF-fused), vector (dense only), or
    # bm25 (sparse only). The graph leg is orthogonal (see use_graph).
    retrieval_mode: Literal["hybrid", "vector", "bm25"] = "hybrid"
    # Per-request model overrides (Phase 2): a registered model id to use for THIS
    # request only, without touching the global assignment. ``llm`` overrides the
    # answer generator (retrieval, incl. rewriting, is unchanged — a clean A/B of
    # generation); ``reranker`` overrides the cross-encoder. Unknown id → 404;
    # wrong-task id → 400. See GET /v1/models/available.
    llm: str | None = None
    reranker: str | None = None


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
    # See QueryRequest — hybrid | vector | bm25.
    retrieval_mode: Literal["hybrid", "vector", "bm25"] = "hybrid"
    # See QueryRequest — per-request cross-encoder override (no generation here).
    reranker: str | None = None


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


# A context expansion needs only prev+next, but allow a small batch.
_MAX_CHUNK_IDS = 20


async def _maybe_rerank(
    reranker, query: str, scored: list[ScoredChunk], top_k: int
) -> list[ScoredChunk]:
    """Rescore the fused candidate pool with the cross-encoder, if one is wired.

    Only the top ``top_k`` are kept downstream, so we ask the sidecar to return
    just those — it scores the whole pool either way, but the response payload
    shrinks from the full pool to ``top_k``.

    Degrades gracefully: a reranker outage / bad response falls back to the
    fused order rather than failing the request (same contract as the LLM and
    rewriter stages). Cancellation is never swallowed."""
    if reranker is None or not scored:
        return scored
    try:
        return await reranker.score(query, [s.chunk for s in scored], top_k=top_k)
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
    scored = await _maybe_rerank(active, query, scored, top_k)
    return scored[:top_k]


def _to_sources(scored: list[ScoredChunk]) -> list[Source]:
    return [
        Source(
            doc_id=r.chunk.doc_id,
            chunk_id=r.chunk.id,
            content=r.chunk.content,
            score=r.score,
            metadata=r.chunk.metadata,
        )
        for r in scored
    ]


async def tenant_slot(
    tenant: str = Depends(resolve_tenant),
    quota=Depends(get_tenant_quota),
) -> AsyncIterator[str]:
    """Resolve the caller's tenant and hold one of its concurrency slots for the
    whole request — admission control so one tenant can't monopolize the shared
    embedding fleet. Yields the tenant for read-scoping."""
    async with quota.slot(tenant):
        yield tenant


def _resolve_entry(registry: CollectionRegistry, collection: str | None):
    """The registry entry for the selected collection (default when None). An
    unknown id is a 404 — explicit selection fails loudly rather than serving the
    wrong corpus."""
    try:
        return registry.resolve(collection)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"unknown collection {collection!r}; see GET /v1/collections",
        ) from None


def _resolve_retriever(registry: CollectionRegistry, collection: str | None):
    """The retriever for the selected registry collection (default when None)."""
    return _resolve_entry(registry, collection).retriever


def _override_reranker(models: ModelRegistry, http, model_id: str | None, default):
    """Per-request reranker: the registered ``model_id`` when given, else the
    server default. Unknown id → 404; a non-reranker model → 400."""
    if not model_id:
        return default
    try:
        return build_reranker_for(models, http, model_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"unknown model {model_id!r}; see GET /v1/models/available"
        ) from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


def _override_generator(models: ModelRegistry, http, model_id: str | None, default):
    """Per-request answer generator: the registered ``model_id`` when given, else
    the server default. Unknown id → 404; a non-llm model → 400."""
    if not model_id:
        return default
    try:
        return build_generator_for(models, http, model_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"unknown model {model_id!r}; see GET /v1/models/available"
        ) from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve(
    request: RetrieveRequest,
    tenant: str = Depends(tenant_slot),
    registry: CollectionRegistry = Depends(get_collections),
    reranker=Depends(get_reranker),
    models: ModelRegistry = Depends(get_model_registry),
    http=Depends(get_http_client),
) -> RetrieveResponse:
    """Retrieve relevant chunks (caller's tenant + public) via hybrid
    vector + BM25 retrieval (+ optional cross-encoder rerank), without
    generating an answer."""
    retriever = _resolve_retriever(registry, request.collection)
    reranker = _override_reranker(models, http, request.reranker, reranker)
    scored = await _retrieve_fused(
        retriever,
        reranker,
        request.query,
        [request.query],
        request.top_k,
        scope_filters(request.filters, tenant),
        request.use_graph,
        rerank=request.rerank,
        rerank_candidates=request.rerank_candidates,
        tenant_id=tenant,
        mode=request.retrieval_mode,
    )
    return RetrieveResponse(sources=_to_sources(scored))


@router.get("/chunks", response_model=ChunksResponse)
async def get_chunks(
    ids: str = "",
    collection: str | None = None,
    tenant: str = Depends(tenant_slot),
    registry: CollectionRegistry = Depends(get_collections),
) -> ChunksResponse:
    """Fetch chunks by id from a collection's vector store (tenant-scoped).

    ``ids`` is a comma-separated list — typically the ``prev_chunk_id`` /
    ``next_chunk_id`` carried in a Source's metadata — so a client can expand a
    retrieved chunk's neighbouring context. Unknown or out-of-scope ids are
    silently omitted; order follows the request. ``collection`` selects the
    registry collection (default when omitted); an unknown id 404s."""
    id_list = [x for x in (i.strip() for i in ids.split(",")) if x][:_MAX_CHUNK_IDS]
    if not id_list:
        return ChunksResponse(chunks=[])
    store = _resolve_entry(registry, collection).vector_store
    if store is None:  # pragma: no cover - all wired entries carry a store
        return ChunksResponse(chunks=[])
    chunks = await store.get_chunks(id_list, scope_filters({}, tenant))
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
    generator = _override_generator(models, http, request.llm, generator)
    reranker = _override_reranker(models, http, request.reranker, reranker)
    retriever = _resolve_retriever(registry, request.collection)
    filters = scope_filters(request.filters, tenant)
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

    sources = _to_sources(scored)
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
