"""Document loaders."""
from __future__ import annotations

import uuid
from pathlib import Path

from ragstack.models import Document


class TextFileLoader:
    """Load plain-text or Markdown files from disk."""

    def load(self, source: str) -> list[Document]:
        path = Path(source)
        content = path.read_text(encoding="utf-8")
        return [
            Document(
                id=str(uuid.uuid4()),
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
                id=str(uuid.uuid4()),
                content=source,
                source="<string>",
            )
        ]
