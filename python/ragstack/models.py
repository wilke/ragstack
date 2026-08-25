"""Shared data models for RAGStack."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, SerializerFunctionWrapHandler, model_serializer
from pydantic.json_schema import SkipJsonSchema


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


class ContextChunk(BaseModel):
    """One neighbouring chunk attached to a :class:`Source` by server-side
    context expansion (``context_window``, issue #322). ``position`` is the
    offset from the source in chunks: ``-1`` is the immediately preceding chunk,
    ``1`` the following one. Carries no score — the score belongs to the
    matched chunk only."""

    chunk_id: str
    position: int
    content: str


class Source(BaseModel):
    """A source reference returned in query responses.

    ``context`` is the source's document neighbours (``context_window > 0``),
    ordered by position, or ``None`` — and a ``None`` is OMITTED from the
    serialized form rather than emitted as ``"context": null``, so a request
    that did not ask for expansion gets a response byte-identical to the one it
    got before the field existed (the contract lists ``context`` as optional,
    not nullable). Only the ``context`` key is touched: ``metadata`` values that
    are ``None`` (``prev_chunk_id`` on a document's first chunk) still serialize
    as ``null``, exactly as before — which is why this is a targeted serializer
    and not ``exclude_none``.
    """

    doc_id: str
    chunk_id: str
    content: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)
    # ``SkipJsonSchema[None]``: the served OpenAPI shows ``context`` as an
    # optional array — not nullable — matching contracts/schemas/source.json.
    context: list[ContextChunk] | SkipJsonSchema[None] = None

    # Deliberately no return annotation: with one, pydantic replaces the model's
    # serialization JSON schema (what /openapi.json shows for Source) by the
    # annotation's — an opaque ``{"type": "object"}``. Unannotated, it keeps the
    # field-level schema.
    @model_serializer(mode="wrap")
    def _omit_absent_context(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if data.get("context") is None:
            data.pop("context", None)
        return data
