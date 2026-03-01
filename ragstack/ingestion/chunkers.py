"""Text chunkers — split documents into overlapping passages."""
from __future__ import annotations

import uuid

from ragstack.models import Chunk, Document


class RecursiveCharacterChunker:
    """Split text by characters with configurable size and overlap."""

    def __init__(self, chunk_size: int = 512, chunk_overlap: int = 64) -> None:
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def chunk(self, doc: Document) -> list[Chunk]:
        text = doc.content
        chunks: list[Chunk] = []
        start = 0
        while start < len(text):
            end = min(start + self.chunk_size, len(text))
            chunk_text = text[start:end]
            chunks.append(
                Chunk(
                    id=str(uuid.uuid4()),
                    doc_id=doc.id,
                    content=chunk_text,
                    metadata=dict(doc.metadata),
                    start_char=start,
                    end_char=end,
                )
            )
            if end == len(text):
                break
            start = end - self.chunk_overlap
        return chunks
