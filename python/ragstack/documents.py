"""Document-registry domain types for ``GET /v1/documents`` (#86).

The document list is derived by aggregating the *served* text index up to the
document level (distinct ``doc_id``), tenant-scoped — **not** from the API job
registry, which CLI-built corpora bypass entirely (a bulk ingest writes straight
to Qdrant/ES). Every chunk of a document carries identical document-level
metadata (title/doc_type/doi/… stamped at enrichment), so a single exemplar
chunk per ``doc_id`` reconstructs the document record.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

# Chunk-level payload keys are dropped when projecting a chunk's metadata up to
# the document level — they vary per chunk (or are redundant with doc_id), so
# they don't belong on a document record.
_CHUNK_LEVEL_KEYS = frozenset(
    {
        "content",
        "chunk_id",
        "doc_id",
        "start_char",
        "end_char",
        "chunk_index",
        "prev_chunk_id",
        "next_chunk_id",
    }
)


@dataclass
class DocumentSummary:
    """A distinct indexed document, aggregated from its chunks."""

    doc_id: str
    source: str
    chunk_count: int
    metadata: dict[str, Any] = field(default_factory=dict)


def document_from_chunk_metadata(
    doc_id: str, chunk_count: int, chunk_metadata: dict[str, Any]
) -> DocumentSummary:
    """Project one chunk's metadata up to a ``DocumentSummary``.

    Drops chunk-level keys; ``source`` prefers the enrichment ``source_path``,
    falling back to ``filename`` (the plain ``Chunk.source`` is empty by default).
    """
    meta = {k: v for k, v in chunk_metadata.items() if k not in _CHUNK_LEVEL_KEYS}
    source = str(meta.get("source_path") or meta.get("filename") or "")
    return DocumentSummary(
        doc_id=doc_id, source=source, chunk_count=chunk_count, metadata=meta
    )


# --------------------------------------------------------------------------- #
# Opaque pagination cursor. A single ``doc_id`` is the composite-aggregation
# ``after`` key (ES) / the slice anchor (in-memory); base64 keeps it opaque so
# callers treat it as a token, not a doc_id to guess with.
# --------------------------------------------------------------------------- #
def encode_cursor(doc_id: str) -> str:
    return base64.urlsafe_b64encode(doc_id.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str) -> str:
    """Decode a cursor to its ``doc_id`` anchor. Raises ``ValueError`` on a
    malformed token so the caller can reject it as a 400 rather than 500."""
    try:
        return base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
    except Exception as e:  # noqa: BLE001 — normalize any decode error to one type
        raise ValueError(f"malformed cursor: {cursor!r}") from e
