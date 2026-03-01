"""Query and retrieve endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ragstack.models import Source

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


@router.post("/query", response_model=QueryResponse)
async def query(request: QueryRequest) -> QueryResponse:
    """
    Run a full RAG pipeline: rewrite → retrieve → rerank → generate.

    This stub returns a placeholder response; wire up the pipeline
    components in a production deployment.
    """
    return QueryResponse(
        answer="[pipeline not yet wired — implement in ragstack/pipeline/rag.py]",
        sources=[],
        rewritten_queries=[request.query],
    )


@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve(request: RetrieveRequest) -> RetrieveResponse:
    """Retrieve relevant chunks without generating an answer."""
    return RetrieveResponse(sources=[])
