"""Protocol definitions for all pipeline components."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ragstack.models import Chunk, Document, ScoredChunk, Triple


@runtime_checkable
class DocumentLoader(Protocol):
    """Load documents from a source."""

    def load(self, source: str) -> list[Document]: ...


@runtime_checkable
class Chunker(Protocol):
    """Split a document into overlapping passages."""

    def chunk(self, doc: Document) -> list[Chunk]: ...


@runtime_checkable
class Embedder(Protocol):
    """Generate dense vector embeddings for a list of texts."""

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


@runtime_checkable
class VectorStore(Protocol):
    """Store and search dense embeddings."""

    async def upsert(self, chunks: list[Chunk]) -> None: ...

    async def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[ScoredChunk]: ...

    async def delete(self, doc_id: str) -> None: ...


@runtime_checkable
class TextIndex(Protocol):
    """Full-text / BM25 search index."""

    async def index(self, chunks: list[Chunk]) -> None: ...

    async def search(
        self,
        query: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[ScoredChunk]: ...

    async def delete(self, doc_id: str) -> None: ...


@runtime_checkable
class GraphStore(Protocol):
    """Knowledge-graph store."""

    async def add_triples(self, triples: list[Triple]) -> None: ...

    async def query_neighborhood(
        self, entity: str, depth: int = 1
    ) -> list[Triple]: ...

    async def delete_by_doc(self, doc_id: str) -> None: ...


@runtime_checkable
class KGExtractor(Protocol):
    """Extract knowledge-graph triples from chunks."""

    async def extract(self, chunks: list[Chunk]) -> list[Triple]: ...


@runtime_checkable
class QueryRewriter(Protocol):
    """Rewrite a query into one or more alternative queries."""

    async def rewrite(self, query: str) -> list[str]: ...


@runtime_checkable
class Scorer(Protocol):
    """Score / rerank a list of candidate chunks against a query."""

    async def score(self, query: str, candidates: list[Chunk]) -> list[ScoredChunk]: ...
