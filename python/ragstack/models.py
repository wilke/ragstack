"""Shared data models for RAGStack."""
from __future__ import annotations

from typing import Any

from pydantic import (
    BaseModel,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)
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


# Trust ladder bounds for ``Triple.confidence`` (#347). ``LLM_MAX_CONFIDENCE`` is
# the no-launder cap: an LLM can propose (1) but never corroborate (2) or verify (3).
CONFIDENCE_MAX = 3
LLM_MAX_CONFIDENCE = 1
DERIVED_BY_LLM = "llm"


class Triple(BaseModel):
    """A knowledge-graph (subject, predicate, object) triple.

    A triple carries **two** provenance stamps, both set server-side at ingest:
    ``tenant_id`` (who owns it) and ``collection`` (which corpus it was derived
    from). They are independent isolation axes — ``tenant_id`` scopes rows within
    a corpus, ``collection`` is the boundary a multi-collection deployment uses to
    serve several orgs (see ``tenancy.allowed_collection_ids``). Both are honoured
    on read and delete; a triple with an empty ``collection`` (legacy data written
    before #209) is invisible to any collection-scoped caller — fail closed.

    A third, independent axis is **epistemic provenance** (#347): *why should you
    believe this*. ``evidence`` is the verbatim span the triple was read from,
    ``chunk_id`` points back at the chunk that produced it, ``derived_by`` names
    the producer (``"llm"``, ``"tool:<source>"``, or ``""`` when unknown) and
    ``confidence`` is a 0–3 trust ladder: 0 unknown/proposed, 1 LLM-plausible,
    2 corroborated by a real tool call, 3 verified against a structured source.
    ``subject_id`` / ``object_id`` are optional typed identifiers (e.g.
    ``bvbrc:genome:<id>``) that make a tool-verified triple checkable; the
    free-text ``subject`` / ``object`` stay the display form.

    All six default empty/zero and, unlike the two scope stamps, **fail open**: an
    unstamped triple is *unfiltered*, not invisible (see
    ``HybridRetriever._graph_context`` for why). The one hard rule is
    no-laundering — belief must never self-assert as evidence — so an
    ``"llm"``-derived triple can never carry ``confidence > 1``; that is enforced
    here at the model so no write path can bypass it.

    The field set is also the record shape of the archive's reserved ``triples``
    role (#353): ``model_dump()`` must stay JSON-serialisable.
    """

    subject: str
    predicate: str
    object: str
    doc_id: str = ""
    tenant_id: str = ""
    collection: str = ""
    # --- epistemic provenance (#347); all optional, all default-empty ---
    evidence: str = ""
    chunk_id: str = ""
    derived_by: str = ""
    confidence: int = Field(default=0, ge=0, le=CONFIDENCE_MAX)
    subject_id: str = ""
    object_id: str = ""

    @model_validator(mode="after")
    def _no_laundering(self) -> Triple:
        """An LLM-derived triple is at most ``LLM_MAX_CONFIDENCE``: levels 2 and 3
        have to be *earned* by a tool/structured source, never self-asserted."""
        if self.derived_by == DERIVED_BY_LLM and self.confidence > LLM_MAX_CONFIDENCE:
            raise ValueError(
                f"derived_by={DERIVED_BY_LLM!r} caps confidence at {LLM_MAX_CONFIDENCE}; "
                f"got {self.confidence}"
            )
        return self


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
