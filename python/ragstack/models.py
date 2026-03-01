"""Shared data models for RAGStack."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Document(BaseModel):
    """A source document before chunking."""

    id: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    source: str = ""


class Chunk(BaseModel):
    """A passage-level fragment of a Document."""

    id: str
    doc_id: str
    content: str
    embedding: list[float] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    start_char: int = 0
    end_char: int = 0


class Triple(BaseModel):
    """A knowledge-graph (subject, predicate, object) triple."""

    subject: str
    predicate: str
    object: str
    doc_id: str = ""


class ScoredChunk(BaseModel):
    """A Chunk annotated with a relevance score."""

    chunk: Chunk
    score: float
    retrieval_method: str = "hybrid"  # vector | bm25 | graph | hybrid


class Source(BaseModel):
    """A source reference returned in query responses."""

    doc_id: str
    chunk_id: str
    content: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)
