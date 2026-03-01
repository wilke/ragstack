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
    ) -> None:
        self.vector_store = vector_store
        self.text_index = text_index
        self.embedder = embedder
        self.graph_store = graph_store
        self.rrf = rrf_scorer or RRFScorer()

    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
        use_graph: bool = True,
    ) -> list[ScoredChunk]:
        # Dense retrieval
        query_vectors: list[list[float]] = await self.embedder.embed([query])  # type: ignore[attr-defined]
        vector_results = await self.vector_store.search(
            query_vectors[0], top_k=top_k * 2, filters=filters
        )

        # Sparse / BM25 retrieval
        bm25_results = await self.text_index.search(query, top_k=top_k * 2, filters=filters)

        ranked_lists = [vector_results, bm25_results]

        # Optional graph-augmented context
        if use_graph and self.graph_store:
            graph_chunks = await self._graph_context(query, top_k)
            if graph_chunks:
                ranked_lists.append(graph_chunks)

        fused = self.rrf.fuse(ranked_lists)
        return fused[:top_k]

    async def _graph_context(self, query: str, top_k: int) -> list[ScoredChunk]:
        """Retrieve graph-neighbourhood context for entities in the query."""
        from ragstack.models import Chunk

        triples = await self.graph_store.query_neighborhood(query, depth=1)  # type: ignore[union-attr]
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
                    score=0.5,
                    retrieval_method="graph",
                )
            )
        return chunks
