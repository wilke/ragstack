"""Query and retrieve endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ragstack.api.deps import get_embedder, get_generator, get_vector_store
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


@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve(
    request: RetrieveRequest,
    tenant: str = Depends(resolve_tenant),
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


def _placeholder_answer(query_text: str, sources: list[Source]) -> str:
    if sources:
        return (
            f"[LLM not configured] retrieved {len(sources)} chunks for query "
            f"{query_text!r}; top score {sources[0].score:.4f}"
        )
    return f"[LLM not configured] no relevant chunks found for query {query_text!r}"


@router.post("/query", response_model=QueryResponse)
async def query(
    request: QueryRequest,
    tenant: str = Depends(resolve_tenant),
    embedder=Depends(get_embedder),
    vector_store=Depends(get_vector_store),
    generator=Depends(get_generator),
) -> QueryResponse:
    """Full RAG flow: retrieve relevant chunks (caller's tenant + public) and
    generate a grounded answer. When no LLM endpoint is configured the answer is
    a retrieval-only placeholder.
    """
    sources = await _retrieve(
        request.query,
        request.top_k,
        scope_filters(request.filters, tenant),
        embedder,
        vector_store,
    )
    if generator is not None:
        answer = await generator.generate(request.query, sources)
    else:
        answer = _placeholder_answer(request.query, sources)
    return QueryResponse(
        answer=answer,
        sources=sources,
        rewritten_queries=[request.query],
    )
