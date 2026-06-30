"""Query and retrieve endpoints."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ragstack.api.deps import (
    get_generator,
    get_reranker,
    get_retriever,
    get_rewriters,
    get_tenant_quota,
)
from ragstack.api.security import resolve_tenant
from ragstack.config import settings
from ragstack.models import ScoredChunk, Source
from ragstack.protocols import QueryRewriter
from ragstack.scoring.scorers import RRFScorer
from ragstack.tenancy import scope_filters

log = logging.getLogger(__name__)
router = APIRouter()

# Stateless RRF fusion for combining per-rewrite ranked lists.
_RRF = RRFScorer()


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


class RetrieveResponse(BaseModel):
    sources: list[Source]


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
            tenant_id=tenant_id,
        )
    else:
        # Independent retrievals run concurrently — latency is one retrieve, not N.
        ranked = await asyncio.gather(
            *(
                retriever.retrieve(
                    v, top_k=depth, filters=filters, use_graph=use_graph,
                    tenant_id=tenant_id,
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


@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve(
    request: RetrieveRequest,
    tenant: str = Depends(tenant_slot),
    retriever=Depends(get_retriever),
    reranker=Depends(get_reranker),
) -> RetrieveResponse:
    """Retrieve relevant chunks (caller's tenant + public) via hybrid
    vector + BM25 retrieval (+ optional cross-encoder rerank), without
    generating an answer."""
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
    )
    return RetrieveResponse(sources=_to_sources(scored))


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
    retriever=Depends(get_retriever),
    generator=Depends(get_generator),
    rewriters=Depends(get_rewriters),
    reranker=Depends(get_reranker),
) -> QueryResponse:
    """Full RAG flow: optionally rewrite the query (HyDE / multi-query), hybrid-
    retrieve for each variant (caller's tenant + public), fuse with RRF,
    optionally cross-encoder rerank, and generate a grounded answer. When no LLM
    endpoint is configured the answer is a retrieval-only placeholder.
    """
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
