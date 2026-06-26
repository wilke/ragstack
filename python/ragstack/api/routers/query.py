"""Query and retrieve endpoints."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ragstack.api.deps import get_embedder, get_tenant_quota, get_vector_store
from ragstack.api.security import resolve_tenant
from ragstack.models import Source
from ragstack.tenancy import scope_filters

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


async def _retrieve(
    query: str,
    top_k: int,
    filters: dict[str, Any],
    embedder,
    vector_store,
) -> list[Source]:
    [qvec] = await embedder.embed([query])
    results = await vector_store.search(qvec, top_k=top_k, filters=filters or None)
    return [
        Source(
            doc_id=r.chunk.doc_id,
            chunk_id=r.chunk.id,
            content=r.chunk.content,
            score=r.score,
            metadata=r.chunk.metadata,
        )
        for r in results
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
    embedder=Depends(get_embedder),
    vector_store=Depends(get_vector_store),
) -> RetrieveResponse:
    """Retrieve relevant chunks (from the caller's tenant + public) without
    generating an answer."""
    sources = await _retrieve(
        request.query,
        request.top_k,
        scope_filters(request.filters, tenant),
        embedder,
        vector_store,
    )
    return RetrieveResponse(sources=sources)


@router.post("/query", response_model=QueryResponse)
async def query(
    request: QueryRequest,
    tenant: str = Depends(tenant_slot),
    embedder=Depends(get_embedder),
    vector_store=Depends(get_vector_store),
) -> QueryResponse:
    """Full RAG flow: retrieve relevant chunks (caller's tenant + public) and
    return them with the rewritten queries. The LLM-backed `answer` generation
    isn't wired yet, so we return a placeholder that surfaces what *would* be
    passed to it.
    """
    sources = await _retrieve(
        request.query,
        request.top_k,
        scope_filters(request.filters, tenant),
        embedder,
        vector_store,
    )
    answer = (
        f"[LLM not yet wired] retrieved {len(sources)} chunks for query "
        f"{request.query!r}; top score "
        f"{sources[0].score:.4f}" if sources else
        f"[LLM not yet wired] no relevant chunks found for query {request.query!r}"
    )
    return QueryResponse(
        answer=answer,
        sources=sources,
        rewritten_queries=[request.query],
    )
