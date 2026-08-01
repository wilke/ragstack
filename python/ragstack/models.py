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
    """A knowledge-graph (subject, predicate, object) triple.

    A triple carries **two** provenance stamps, both set server-side at ingest:
    ``tenant_id`` (who owns it) and ``collection`` (which corpus it was derived
    from). They are independent isolation axes — ``tenant_id`` scopes rows within
    a corpus, ``collection`` is the boundary a multi-collection deployment uses to
    serve several orgs (see ``tenancy.allowed_collection_ids``). Both are honoured
    on read and delete; a triple with an empty ``collection`` (legacy data written
    before #209) is invisible to any collection-scoped caller — fail closed.
    """

    subject: str
    predicate: str
    object: str
    doc_id: str = ""
    tenant_id: str = ""
    collection: str = ""


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
