"""Document loaders."""
from __future__ import annotations

import uuid
from pathlib import Path

from ragstack.models import Document

# Namespace for deriving *deterministic* document IDs. Chunk IDs — and therefore
# the Qdrant point IDs (uuid5 of the chunk ID, see stores/qdrant.py) — are derived
# from the document ID, so a random per-load doc ID makes every re-ingest write
# fresh points and silently duplicate the corpus. Deriving the doc ID from a
# stable key (resolved path / content) makes re-ingest overwrite in place.
_DOC_NAMESPACE = uuid.NAMESPACE_URL


def deterministic_doc_id(key: str) -> str:
    """Stable document ID for a normalized source key (path or content)."""
    return str(uuid.uuid5(_DOC_NAMESPACE, key))


class TextFileLoader:
    """Load plain-text or Markdown files from disk."""

    def load(self, source: str) -> list[Document]:
        path = Path(source)
        content = path.read_text(encoding="utf-8")
        return [
            Document(
                id=deterministic_doc_id(str(path.resolve())),
                content=content,
                metadata={"filename": path.name},
                source=source,
            )
        ]


class StringLoader:
    """Load a document directly from a string — useful for testing."""

    def load(self, source: str) -> list[Document]:
        return [
            Document(
                # The string itself is the only stable key we have here.
                id=deterministic_doc_id(source),
                content=source,
                source="<string>",
            )
        ]
