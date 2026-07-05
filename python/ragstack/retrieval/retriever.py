"""Retrieval pipeline — hybrid vector + BM25 + graph retrieval."""
from __future__ import annotations

from typing import Any

from ragstack.models import ScoredChunk
from ragstack.protocols import GraphStore, TextIndex, VectorStore
from ragstack.scoring.scorers import RRFScorer


class HybridRetriever:
    """
    Combine dense-vector retrieval, BM25 text search, and optional
    knowledge-graph context into a single fused ranked list.
    """

    def __init__(
        self,
        vector_store: VectorStore,
        text_index: TextIndex,
        embedder: object,
        graph_store: GraphStore | None = None,
        rrf_scorer: RRFScorer | None = None,
        candidate_multiplier: int = 2,
        graph_context_score: float = 0.5,
        graph_context_depth: int = 1,
    ) -> None:
        self.vector_store = vector_store
        self.text_index = text_index
        self.embedder = embedder
        self.graph_store = graph_store
        self.rrf = rrf_scorer or RRFScorer()
        # Per-leg candidate depth (top_k * multiplier) and graph-leg tuning; defaults
        # match the prior hardcoded values, overridden from Settings in deps.py.
        self.candidate_multiplier = candidate_multiplier
        self.graph_context_score = graph_context_score
        self.graph_context_depth = graph_context_depth

    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
        use_graph: bool = True,
        tenant_id: str | None = None,
        mode: str = "hybrid",
    ) -> list[ScoredChunk]:
        """Retrieve ``top_k`` chunks. ``mode`` selects the retrieval legs:
        ``hybrid`` (dense + BM25, RRF-fused — default), ``vector`` (dense only),
        or ``bm25`` (sparse only). The graph leg is orthogonal — ``use_graph``
        adds it under any mode. An unknown mode falls back to hybrid (both legs)."""
        depth = top_k * self.candidate_multiplier
        ranked_lists = []

        # Dense retrieval — unless BM25-only. (Skips the query embed for bm25 mode.)
        if mode != "bm25":
            query_vectors: list[list[float]] = await self.embedder.embed([query])  # type: ignore[attr-defined]
            ranked_lists.append(
                await self.vector_store.search(query_vectors[0], top_k=depth, filters=filters)
            )

        # Sparse / BM25 retrieval — unless vector-only.
        if mode != "vector":
            ranked_lists.append(await self.text_index.search(query, top_k=depth, filters=filters))

        # Optional graph-augmented context (independent of mode).
        if use_graph and self.graph_store:
            graph_chunks = await self._graph_context(query, top_k, tenant_id)
            if graph_chunks:
                ranked_lists.append(graph_chunks)

        fused = self.rrf.fuse(ranked_lists)
        return fused[:top_k]

    async def _graph_context(
        self, query: str, top_k: int, tenant_id: str | None = None
    ) -> list[ScoredChunk]:
        """Retrieve graph-neighbourhood context for entities in the query.

        ``tenant_id`` is the caller's own tenant; it scopes the neighbourhood
        query so the graph leg reads only the caller's triples plus the shared
        ``public`` corpus (the store derives that scope). ``None`` (dev/tests /
        unauthenticated) reads unscoped, matching the other legs' behaviour."""
        from ragstack.models import Chunk

        triples = await self.graph_store.query_neighborhood(  # type: ignore[union-attr]
            query, depth=self.graph_context_depth, tenant_id=tenant_id
        )
        chunks = []
        for triple in triples[:top_k]:
            content = f"{triple.subject} {triple.predicate} {triple.object}"
            chunks.append(
                ScoredChunk(
                    chunk=Chunk(
                        id=f"graph-{triple.subject}-{triple.predicate}-{triple.object}",
                        doc_id=triple.doc_id,
                        content=content,
                    ),
                    score=self.graph_context_score,
                    retrieval_method="graph",
                )
            )
        return chunks
