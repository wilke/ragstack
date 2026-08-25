"""Protocol definitions for all pipeline components.

The auth-side protocol lives next to its implementations rather than here:
``ragstack.identity.base.IdentityProvider`` (who is this caller?), because its
failure modes are normative — ``IdentityInvalid`` → 401 vs ``IdentityUnavailable``
→ 503, never an allow — and belong beside the code that raises them.
"""
from __future__ import annotations

from collections.abc import Sequence
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

    async def get_chunks(
        self, chunk_ids: list[str], filters: dict[str, Any] | None = None
    ) -> list[Chunk]:
        """Fetch chunks by id, tenant-scoped via ``filters`` (same ``tenant_id``
        read scope as ``search``). Preserves the requested id order; ids not found
        or not visible are omitted. Used to resolve a chunk's neighbours
        (``prev_chunk_id`` / ``next_chunk_id``) for context expansion."""
        ...

    async def count(self) -> int:
        """The number of chunks the WHOLE store (this collection) holds — the
        live figure the per-collection chunk cap (#291) is checked against,
        once per ingest job. Deliberately unfiltered: the cap bounds the
        collection, not a tenant's stripe of it. Never expose this to a
        non-admin reader (``count_tenants`` is the scoped count)."""
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

    Scoped on **two** independent axes, both stamped onto every triple
    server-side at ingest (see :class:`ragstack.models.Triple`):

    * ``tenant_id`` — reads pass the caller's tenant and see their own corpus
      plus the shared ``public`` one; ``delete_by_doc`` never crosses tenants.
    * ``collection`` — which corpus the triple was derived from. Unlike the
      vector/text stores, whose per-collection isolation is physical (one Qdrant
      collection / ES index each), one graph backend holds every collection's
      triples (Neo4j Community serves a single database), so the collection
      boundary has to be carried in the data. Reads and deletes take the
      collection they are scoped to; ``None`` means deliberately unscoped
      (dev/tests/admin inspection) and spans every collection.

    A ``collection``-scoped call never matches an unstamped triple (empty
    ``collection``, i.e. data written before #209) — it fails closed rather than
    guessing which corpus legacy data belongs to. Such triples remain reachable
    through unscoped calls; re-ingest re-derives them with a stamp.
    """

    async def add_triples(self, triples: list[Triple]) -> None:
        """Upsert triples, persisting each one's ``tenant_id`` **and**
        ``collection`` stamps so both scopes are properties of the stored data."""
        ...

    async def query_neighborhood(
        self,
        entity: str,
        depth: int = 1,
        tenant_id: str | None = None,
        collection: str | Sequence[str] | None = None,
    ) -> list[Triple]: ...

    async def match_entities(
        self,
        candidates: list[str],
        *,
        tenant_id: str | None,
        collection: str | Sequence[str] | None,
    ) -> list[str]:
        """The subset of ``candidates`` that name an entity in the readable
        scope — an indexed, **exact**, case-folded lookup (never a substring
        match), returned case-folded, distinct, in candidate order. ONE round
        trip for the whole list; an empty list costs none. This is the query-side
        entity extraction of the graph leg (#349): the retriever hands it the
        query's n-grams and only expands the ones that name a known entity.

        The scope is keyword-only and required so a caller can't forget it;
        ``None`` on an axis is the same deliberate unscoped read as everywhere
        else on this protocol."""
        ...

    async def list_entities(
        self,
        tenant_id: str | None = None,
        limit: int = 100,
        collection: str | None = None,
    ) -> list[tuple[str, int]]: ...

    async def stats(
        self, tenant_id: str | None = None, collection: str | None = None
    ) -> tuple[int, int]:
        """Return ``(entities, relationships)`` visible to the caller — scoped to
        readable tenants (own + public) and to ``collection``; unscoped (``None``)
        counts everything on that axis (dev/tests)."""
        ...

    async def delete_by_doc(
        self,
        doc_id: str,
        tenant_id: str | None = None,
        collection: str | None = None,
    ) -> None:
        """Delete the triples ``doc_id`` contributed, never crossing the tenant or
        the collection boundary. A collection-blind delete would drop another
        collection's triples for a doc_id that exists in both."""
        ...

    async def delete_collection(self, tenant_id: str | None, collection: str) -> int:
        """Delete every triple stamped ``collection`` (#380) and sweep the
        entities that lost their last edge; return the number of triples
        (edges) removed, so a second call returns ``0`` — the idempotency signal
        the eviction/purge report turns into ``absent``.

        ``collection`` is required and never crossed: another collection's
        triples — even for the same ``doc_id`` or entity name — are untouched
        (#209). ``tenant_id`` narrows the delete to ONE tenant's triples by
        exact equality (never the readable set — ``public`` is not the
        caller's to delete); ``None`` is the deliberate collection-wide form
        the physical-store drops use: eviction and purge destroy the whole
        Qdrant collection regardless of who wrote which chunk, so the graph leg
        must do the same or a reused collection inherits the previous owner's
        edges (#295, the direction that issue asked to be chosen explicitly).
        An empty ``collection`` is refused (``ValueError``) rather than matching
        unstamped legacy triples."""
        ...


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
