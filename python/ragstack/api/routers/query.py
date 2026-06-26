"""Query and retrieve endpoints."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ragstack.api.deps import get_generator, get_retriever, get_tenant_quota
from ragstack.api.security import resolve_tenant
from ragstack.models import ScoredChunk, Source
from ragstack.tenancy import scope_filters

log = logging.getLogger(__name__)
router = APIRouter()


class QueryRequest(BaseModel):
    query: str
    top_k: int = 5
    rewrite_strategies: list[str] = Field(default_factory=lambda: ["passthrough"])
    filters: dict[str, Any] = Field(default_factory=dict)
    use_graph: bool = True
    stream: bool = False


class QueryResponse(BaseModel):
    answer: str
    sources: list[Source]
    rewritten_queries: list[str]


class RetrieveRequest(BaseModel):
    query: str
    top_k: int = 5
    filters: dict[str, Any] = Field(default_factory=dict)
    use_graph: bool = True


class RetrieveResponse(BaseModel):
    sources: list[Source]


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
) -> RetrieveResponse:
    """Retrieve relevant chunks (caller's tenant + public) via hybrid
    vector + BM25 retrieval, without generating an answer."""
    scored = await retriever.retrieve(
        request.query,
        top_k=request.top_k,
        filters=scope_filters(request.filters, tenant),
        use_graph=request.use_graph,
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
) -> QueryResponse:
    """Full RAG flow: hybrid-retrieve relevant chunks (caller's tenant + public)
    and generate a grounded answer. When no LLM endpoint is configured the answer
    is a retrieval-only placeholder.
    """
    scored = await retriever.retrieve(
        request.query,
        top_k=request.top_k,
        filters=scope_filters(request.filters, tenant),
        use_graph=request.use_graph,
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
        rewritten_queries=[request.query],
    )
