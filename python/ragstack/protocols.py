"""Protocol definitions for all pipeline components."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ragstack.documents import DocumentSummary
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

    async def delete(self, doc_id: str, tenant_id: str | None = None) -> None: ...

    async def delete_except(
        self, doc_id: str, keep_chunk_ids: set[str], tenant_id: str | None = None
    ) -> None:
        """Prune a document's orphan chunks (those not in ``keep_chunk_ids``).
        Call after upserting the kept chunks so a failure here can't lose data."""
        ...

    async def count_tenants(self, tenants: list[str]) -> int:
        """Count stored chunks visible to ``tenants`` (own + public), using a
        FILTERED count — never the global collection total. Must fail closed
        (return 0) on an empty ``tenants`` list rather than counting everything."""
        ...


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

    async def delete(self, doc_id: str, tenant_id: str | None = None) -> None: ...

    async def delete_except(
        self, doc_id: str, keep_chunk_ids: set[str], tenant_id: str | None = None
    ) -> None:
        """Prune a document's orphan chunks (those not in ``keep_chunk_ids``).
        Call after indexing the kept chunks so a failure here can't lose data."""
        ...

    async def count_tenants(self, tenants: list[str]) -> int:
        """Count indexed chunks visible to ``tenants`` (own + public), using a
        FILTERED count. Must fail closed (return 0) on an empty ``tenants`` list."""
        ...

    async def list_documents(
        self, tenants: list[str], limit: int = 100, cursor: str | None = None
    ) -> tuple[list[DocumentSummary], str | None]:
        """Distinct documents visible to ``tenants`` (own + public), aggregated up
        from indexed chunks by ``doc_id`` and paginated by an opaque ``cursor``.

        Returns ``(documents, next_cursor)`` ordered by ``doc_id``; ``next_cursor``
        is ``None`` on the last page. Must fail closed (return ``([], None)``) on an
        empty ``tenants`` list — an unscoped listing would leak documents across
        tenants. Backs ``GET /v1/documents`` (#86); the list comes from the served
        index, not the job registry, so CLI-built corpora are visible."""
        ...


@runtime_checkable
class GraphStore(Protocol):
    """Knowledge-graph store.

    Tenant-scoped: every triple carries a ``tenant_id`` (set server-side at
    ingest), reads pass the caller's tenant so they see only their own corpus
    plus the shared ``public`` one, and ``delete_by_doc`` never crosses tenants.
    """

    async def add_triples(self, triples: list[Triple]) -> None: ...

    async def query_neighborhood(
        self, entity: str, depth: int = 1, tenant_id: str | None = None
    ) -> list[Triple]: ...

    async def list_entities(
        self, tenant_id: str | None = None, limit: int = 100
    ) -> list[tuple[str, int]]: ...

    async def stats(self, tenant_id: str | None = None) -> tuple[int, int]:
        """Return ``(entities, relationships)`` visible to the caller — scoped to
        readable tenants (own + public); unscoped (``None``) counts everything
        (dev/tests)."""
        ...

    async def delete_by_doc(self, doc_id: str, tenant_id: str | None = None) -> None: ...


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

    async def score(
        self, query: str, candidates: list[Chunk], top_k: int | None = None
    ) -> list[ScoredChunk]: ...
