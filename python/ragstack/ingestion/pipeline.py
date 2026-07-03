"""Ingestion pipeline — orchestrates loading, chunking, embedding, and indexing."""
from __future__ import annotations

import asyncio
import logging

from ragstack.ingestion.chunkers import link_neighbors_by_document
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
from ragstack.tenancy import DEFAULT_TENANT

log = logging.getLogger(__name__)


class EmptyIngestError(RuntimeError):
    """A source produced no embeddable chunks — either it had no chunkable
    content or every chunk was quarantined as unembeddable. Raised before the
    replace step so a failed/empty re-ingest never deletes the document's
    previously-ingested data."""


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

    async def ingest(self, source: str, tenant_id: str = DEFAULT_TENANT) -> list[str]:
        """Ingest a source and return the list of chunk IDs created.

        Every chunk is stamped with ``tenant_id`` (the owning tenant, derived
        server-side from the API key), which scopes both its stored identity and
        which queries can read it.
        """
        documents: list[Document] = self.loader.load(source)
        all_chunks: list[Chunk] = []
        for doc in documents:
            # Run chunking in a worker thread: chunkers are synchronous, and the
            # SemanticChunker blocks on a (bridged) embed round-trip, which would
            # otherwise stall the event loop. to_thread keeps the loop responsive.
            all_chunks.extend(await asyncio.to_thread(self.chunker.chunk, doc))
        for chunk in all_chunks:
            chunk.metadata["tenant_id"] = tenant_id
        produced = len(all_chunks)

        # Embed. Prefer the poison-isolating path when the embedder supports it
        # (bounded batching wrapper): a single unembeddable chunk is quarantined
        # rather than failing the whole document.
        texts = [c.content for c in all_chunks]
        embed_isolated = getattr(self.embedder, "embed_isolated", None)
        if embed_isolated is not None:
            vectors, quarantined = await embed_isolated(texts)
        else:
            vectors, quarantined = await self.embedder.embed(texts), 0

        kept: list[Chunk] = []
        for chunk, vector in zip(all_chunks, vectors, strict=True):
            if vector is None:
                continue
            chunk.embedding = vector
            kept.append(chunk)
        if quarantined:
            log.warning(
                "ingest %r: quarantined %d unembeddable chunk(s)", source, quarantined
            )
        all_chunks = kept

        # Stamp prev/next/chunk_index on the SURVIVING chunks (per document, after
        # the embed drop above) so both the Qdrant payload and the ES document carry
        # a neighbor chain that never dangles to a quarantined chunk.
        link_neighbors_by_document(all_chunks)

        # Never delete prior data without a replacement. If the source produced
        # no chunks (empty content) or every chunk was quarantined, the replace
        # block below would delete the previously-ingested version and upsert
        # nothing — silent data loss on a failed/empty re-ingest. Fail instead:
        # the prior corpus stays intact and _run_ingest records a failed job.
        if not all_chunks:
            raise EmptyIngestError(
                f"no embeddable chunks for source "
                f"(produced {produced}, quarantined {quarantined})"
            )

        # Replace, don't accumulate. Deterministic IDs make a byte-identical
        # re-ingest overwrite its points in place, but an *edited* document
        # produces shifted chunk boundaries — and therefore new chunk IDs — so
        # the previous chunks would linger as orphans and pollute retrieval.
        # Delete each document's prior chunks first. Done here, after a
        # successful embed (a transient embed failure raises before this point),
        # so old data is never destroyed before its replacement exists.
        #
        # Delete-prior ONLY for documents that produced a surviving chunk this run.
        # In a multi-document source, a document whose chunks were *all* quarantined
        # (unembeddable) contributes nothing to ``all_chunks``; deleting its prior
        # chunks here would drop good data with no replacement — the per-document
        # form of the empty-ingest data loss the ``EmptyIngestError`` guard prevents
        # at the whole-source level. Such a document keeps its prior data intact
        # (the quarantine is surfaced by the warning above); removing a document is
        # the explicit ``DELETE`` endpoint's job, not a side effect of a re-ingest
        # whose new content failed to embed.
        docs_with_chunks = {c.doc_id for c in all_chunks}
        skipped = [d.id for d in documents if d.id not in docs_with_chunks]
        if skipped:
            log.warning(
                "ingest %r: kept prior data for %d document(s) with no surviving "
                "chunks this run (empty or all-quarantined): %s",
                source, len(skipped), skipped,
            )
        for doc in documents:
            if doc.id not in docs_with_chunks:
                continue
            await self.vector_store.delete(doc.id, tenant_id=tenant_id)
            await self.text_index.delete(doc.id, tenant_id=tenant_id)
            if self.graph_store is not None:
                await self.graph_store.delete_by_doc(doc.id, tenant_id=tenant_id)

        # Index
        await self.vector_store.upsert(all_chunks)
        await self.text_index.index(all_chunks)

        # Knowledge-graph extraction (optional)
        if self.kg_extractor and self.graph_store:
            triples = await self.kg_extractor.extract(all_chunks)
            # Stamp the owning tenant on every triple so graph deletes/reads can be
            # tenant-scoped regardless of what the extractor populated.
            for triple in triples:
                triple.tenant_id = tenant_id
            await self.graph_store.add_triples(triples)

        return [c.id for c in all_chunks]
