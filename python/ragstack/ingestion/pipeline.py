"""Ingestion pipeline — orchestrates loading, chunking, embedding, and indexing."""
from __future__ import annotations

from ragstack.models import Chunk, Document
from ragstack.protocols import (
    Chunker,
    DocumentLoader,
    Embedder,
    GraphStore,
    KGExtractor,
    TextIndex,
    VectorStore,
)


class IngestionPipeline:
    """
    End-to-end document ingestion:

    1. Load  → 2. Chunk  → 3. Embed  → 4. Index (vector + text + graph)
    """

    def __init__(
        self,
        loader: DocumentLoader,
        chunker: Chunker,
        embedder: Embedder,
        vector_store: VectorStore,
        text_index: TextIndex,
        graph_store: GraphStore | None = None,
        kg_extractor: KGExtractor | None = None,
    ) -> None:
        self.loader = loader
        self.chunker = chunker
        self.embedder = embedder
        self.vector_store = vector_store
        self.text_index = text_index
        self.graph_store = graph_store
        self.kg_extractor = kg_extractor

    async def ingest(self, source: str) -> list[str]:
        """Ingest a source and return the list of chunk IDs created."""
        documents: list[Document] = self.loader.load(source)
        all_chunks: list[Chunk] = []
        for doc in documents:
            all_chunks.extend(self.chunker.chunk(doc))

        # Embed
        texts = [c.content for c in all_chunks]
        embeddings = await self.embedder.embed(texts)
        for chunk, embedding in zip(all_chunks, embeddings):
            chunk.embedding = embedding

        # Index
        await self.vector_store.upsert(all_chunks)
        await self.text_index.index(all_chunks)

        # Knowledge-graph extraction (optional)
        if self.kg_extractor and self.graph_store:
            triples = await self.kg_extractor.extract(all_chunks)
            await self.graph_store.add_triples(triples)

        return [c.id for c in all_chunks]
