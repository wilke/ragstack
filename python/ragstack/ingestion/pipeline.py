"""Ingestion pipeline — orchestrates loading, chunking, embedding, and indexing."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from ragstack.ingestion.boilerplate import BoilerplateFilter
from ragstack.ingestion.chunkers import link_neighbors_by_document
from ragstack.ingestion.doi_metadata import DoiEnricher
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
        delete_concurrency: int = 8,
        collection: str | None = None,
        doi_enricher: DoiEnricher | None = None,
        boilerplate_filter: BoilerplateFilter | None = None,
        delete_prior: bool = True,
    ) -> None:
        self.loader = loader
        self.chunker = chunker
        self.embedder = embedder
        self.vector_store = vector_store
        self.text_index = text_index
        self.graph_store = graph_store
        self.kg_extractor = kg_extractor
        # Optional, opt-in (DOI_ENRICHMENT_ENABLED) scholarly-metadata lookup,
        # applied between load and chunk so resolved fields ride on every chunk's
        # metadata. ``None`` = disabled, and the load→chunk path is byte-for-byte
        # what it was before — no import-time cost, no network, no behaviour
        # change for offline/air-gapped deployments.
        self.doi_enricher = doi_enricher
        # Optional chunk-level boilerplate flag/drop, applied between chunk and
        # embed — the one point where the chunk text exists but has not yet cost
        # a GPU round-trip or reached a store. ``None`` = disabled, and the
        # chunk→embed path is byte-for-byte what it was.
        self.boilerplate_filter = boilerplate_filter
        # The collection this pipeline writes into. The vector store and text
        # index carry it implicitly (they ARE per-collection); the graph store is
        # shared across collections, so triples must be stamped with it on write
        # and deletes scoped by it (#209). ``None`` = unstamped/unscoped, for
        # library and test pipelines that have no collection identity.
        self.collection = collection
        # Delete-prior is one delete per replaced doc_id across vector+text(+graph);
        # run serially it is ~6000 round-trips for a 3000-doc shard and dominates
        # the load (the #144 A/B benchmark). Bound-concurrent it instead.
        self._delete_concurrency = max(1, delete_concurrency)
        # Delete-prior guarantees "replace, don't accumulate": deterministic ids
        # make a byte-identical re-ingest upsert in place, but an *edited* document
        # yields shifted boundaries → new ids → the old chunks linger as orphans.
        # It is nonetheless a provable no-op when the chunk ids cannot have moved —
        # a replay from an unchanged embedding file, where ids are READ from the
        # file rather than recomputed (#323). At 8.6k doc_ids per shard that is
        # ~550k round-trips per 64-shard batch of pure waste.
        #
        # Opt-in only, and deliberately NOT inferred: "the file looks unchanged" is
        # not the same as "this doc was never ingested with different boundaries",
        # and #303 produced exactly that divergence in this repository. The caller
        # asserts id-stability; the pipeline does not guess it.
        self._delete_prior = delete_prior

    async def ingest(self, source: str, tenant_id: str = DEFAULT_TENANT) -> list[str]:
        """Ingest a source and return the list of chunk IDs created.

        Every chunk is stamped with ``tenant_id`` (the owning tenant, derived
        server-side from the API key), which scopes both its stored identity and
        which queries can read it.

        This is the **coupled** path: it composes the two halves of ingestion —
        :meth:`embed_source` (load→chunk→embed, GPU-bound, no store contact) and
        :meth:`index_chunks` (delete-prior→upsert→index, store-bound). Keeping it
        a literal composition means the online single-document path is unchanged
        while the offline plane (ADR-0001 / #141) can run the halves as separate
        stages — embed to a file on the GPU fleet, then load with backpressure —
        without forking this logic.
        """
        return await self.index_chunks(
            await self.embed_source(source, tenant_id=tenant_id), tenant_id=tenant_id
        )

    async def embed_source(
        self, source: str, tenant_id: str = DEFAULT_TENANT
    ) -> list[Chunk]:
        """Load, chunk, and embed ``source``; return the surviving embedded chunks.

        The first half of :meth:`ingest`. Touches **only** the loader, chunker,
        and embedder — never the vector/text/graph stores — so it can run on a
        GPU/embedding worker decoupled from Qdrant (ADR-0001 offline plane, #141).
        Each returned chunk carries its ``embedding``, tenant stamp, and a
        neighbor chain over the survivors. Raises :class:`EmptyIngestError` when
        nothing embeddable was produced, so a caller never advances to the
        store-mutating half with an empty replacement.
        """
        documents: list[Document] = self.loader.load(source)
        await self._apply_doi_enrichment(documents)
        all_chunks: list[Chunk] = []
        for doc in documents:
            # Run chunking in a worker thread: chunkers are synchronous, and the
            # SemanticChunker blocks on a (bridged) embed round-trip, which would
            # otherwise stall the event loop. to_thread keeps the loop responsive.
            all_chunks.extend(await asyncio.to_thread(self.chunker.chunk, doc))
        produced = len(all_chunks)
        all_chunks = self._filter_boilerplate(all_chunks, source)

        kept, quarantined = await self._embed_and_link(all_chunks, tenant_id)
        if quarantined:
            log.warning(
                "ingest %r: quarantined %d unembeddable chunk(s)", source, quarantined
            )

        # Never delete prior data without a replacement. If the source produced
        # no chunks (empty content) or every chunk was quarantined, the replace
        # block in index_chunks would delete the previously-ingested version and
        # upsert nothing — silent data loss on a failed/empty re-ingest. Fail
        # instead: the prior corpus stays intact and _run_ingest records a failed
        # job. Raising here (before any store mutation) is what makes the two-stage
        # split safe — a failed embed can never reach the store-mutating half.
        if not kept:
            raise EmptyIngestError(
                f"no embeddable chunks for source "
                f"(produced {produced}, quarantined {quarantined})"
            )

        # Diagnostic: documents that were loaded but produced no surviving chunk
        # (empty or all-quarantined). index_chunks will keep their prior data
        # intact (it only delete-priors docs that have a survivor); surface that
        # here, where we still have the loaded `documents` to name them.
        docs_with_chunks = {c.doc_id for c in kept}
        skipped = [d.id for d in documents if d.id not in docs_with_chunks]
        if skipped:
            log.warning(
                "ingest %r: kept prior data for %d document(s) with no surviving "
                "chunks this run (empty or all-quarantined): %s",
                source, len(skipped), skipped,
            )
        return kept

    def _filter_boilerplate(self, chunks: list[Chunk], source: str) -> list[Chunk]:
        """Stamp (and, if configured, drop) boilerplate chunks — visibly.

        Runs between chunk and embed. The counts are logged at INFO on every
        source that had any, and at WARNING when the filter removed more than
        half a source's chunks — the signal that the thresholds are wrong for
        this corpus. Deliberately NOT silent: ``scripts/ingest_jsonl.py``'s
        ``_kept()`` drops records with no record of having done so, which is how
        an over-aggressive filter goes unnoticed until answers start missing.

        Never fatal: a bug in the classifier must not be able to fail an ingest,
        so any exception degrades to "keep every chunk".
        """
        if self.boilerplate_filter is None or not chunks:
            return chunks
        try:
            result = self.boilerplate_filter.apply(chunks)
        except Exception as e:  # pragma: no cover - defensive
            log.warning("boilerplate filter skipped for %r: %s", source, e)
            return chunks
        if sum(result.flagged.values()):
            log.info("ingest %r: boilerplate %s", source, result.summary())
        if result.dropped > len(chunks) // 2:
            log.warning(
                "ingest %r: boilerplate filter dropped %d of %d chunks (>50%%) — "
                "check BOILERPLATE_CONFIG_JSON thresholds for this corpus",
                source, result.dropped, len(chunks),
            )
        return result.chunks

    async def _apply_doi_enrichment(self, documents: list[Document]) -> None:
        """Fill metadata gaps from each document's DOI, between load and chunk.

        Placed here — after ``loader.load``, before ``chunker.chunk`` — because
        chunking copies ``Document.metadata`` onto every chunk, so this is the
        one point where a single write per document reaches every downstream
        payload (Qdrant, Elasticsearch, ``/v1/query`` sources) at no per-chunk
        cost.

        **Enrichment must never fail an ingest.** ``DoiEnricher`` already catches
        internally; this second guard covers the case where the enricher itself
        is misconfigured or a non-conforming object was injected. Whatever
        happens, the documents keep the metadata their loader produced and the
        ingest proceeds.
        """
        if self.doi_enricher is None:
            return
        try:
            await self.doi_enricher.enrich_documents(documents)
        except Exception as e:
            log.warning("doi enrichment skipped for this source: %s", e)

    async def _embed_and_link(
        self, all_chunks: list[Chunk], tenant_id: str
    ) -> tuple[list[Chunk], int]:
        """Stamp tenant, embed (poison-isolating when the embedder supports it),
        drop unembeddable chunks, and neighbor-link the survivors per document.

        Shared by the materialized :meth:`embed_source` and the streaming
        :meth:`iter_embed_source`, so the embed/quarantine/link logic lives in one
        place. Neighbor linking groups by ``doc_id``, so a per-group call is
        correct as long as a document's chunks aren't split across groups (the
        streaming path groups whole documents). Returns (survivors, quarantined)."""
        for chunk in all_chunks:
            chunk.metadata["tenant_id"] = tenant_id
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
        link_neighbors_by_document(kept)
        return kept, quarantined

    async def iter_embed_source(
        self, source: str, tenant_id: str = DEFAULT_TENANT, group_size: int = 64
    ) -> AsyncIterator[list[Chunk]]:
        """Streaming counterpart to :meth:`embed_source`: load → chunk → embed in
        groups of ``group_size`` documents, yielding each group's surviving
        embedded chunks.

        Bounds peak memory to one group's chunks+vectors. The materialized path
        holds the WHOLE shard's chunks and 4096-d vectors at once (2.35 GB for a
        3k-doc shard in the #144 benchmark), which does not scale to the offline
        plane's large shards (e.g. 500k docs); this is the path ``run_embed_shard``
        uses. Whole documents stay within a group, so per-group neighbor linking
        equals per-document linking. Yields nothing for an empty source — the file
        sink treats zero chunks as a failed shard, which is the streaming analogue
        of :meth:`embed_source`'s ``EmptyIngestError`` (no store is touched here, so
        there is no prior data to protect)."""
        documents: list[Document] = self.loader.load(source)
        await self._apply_doi_enrichment(documents)
        step = max(1, group_size)
        for start in range(0, len(documents), step):
            group = documents[start : start + step]
            all_chunks: list[Chunk] = []
            for doc in group:
                all_chunks.extend(await asyncio.to_thread(self.chunker.chunk, doc))
            if not all_chunks:
                continue
            all_chunks = self._filter_boilerplate(all_chunks, source)
            if not all_chunks:
                continue
            kept, quarantined = await self._embed_and_link(all_chunks, tenant_id)
            if quarantined:
                log.warning(
                    "embed %r: quarantined %d unembeddable chunk(s) in group",
                    source, quarantined,
                )
            if kept:
                yield kept

    async def index_chunks(
        self, chunks: list[Chunk], tenant_id: str = DEFAULT_TENANT
    ) -> list[str]:
        """Delete prior data and index ``chunks`` (vector + text + graph).

        The second half of :meth:`ingest`. Store-bound: it is the only half that
        contacts Qdrant/ES/Neo4j, so the offline plane can drive it separately —
        reading embedded chunks from a file and upserting with backpressure (#141)
        — while reusing this exact logic. ``chunks`` must already carry embeddings,
        tenant stamps, and neighbor links (i.e. come from :meth:`embed_source`);
        this method is self-contained from them and needs no ``Document`` list.

        Replace, don't accumulate. Deterministic IDs make a byte-identical
        re-ingest overwrite its points in place, but an *edited* document produces
        shifted chunk boundaries — and therefore new chunk IDs — so the previous
        chunks would linger as orphans and pollute retrieval. Delete each
        document's prior chunks first. Done here (a transient embed failure raised
        in embed_source before this point), so old data is never destroyed before
        its replacement exists.

        Delete-prior ONLY for documents that produced a surviving chunk this run.
        In a multi-document source, a document whose chunks were *all* quarantined
        contributes nothing to ``chunks``; deleting its prior chunks here would
        drop good data with no replacement — the per-document form of the
        empty-ingest data loss the ``EmptyIngestError`` guard prevents at the
        whole-source level. Such a document keeps its prior data intact (surfaced
        by embed_source's warning); removing a document is the explicit ``DELETE``
        endpoint's job, not a side effect of a re-ingest whose new content failed
        to embed. Trade-off: if that document's *content changed*, the retained
        prior chunks are now STALE until a later successful re-ingest or explicit
        delete — the accepted lesser evil vs. silently losing them.

        Predicate correctness rests on ``chunk.doc_id == doc.id``: every chunker
        builds chunks through ``chunkers._make_chunk``, which sets ``doc_id=doc.id``
        (there is no other ``Chunk(...)`` construction), so the set of doc_ids to
        replace is exactly the doc_ids present on the surviving chunks.
        """
        # Delete-prior, bound-concurrent across the replaced doc_ids (deletes are
        # independent + idempotent per doc_id, so order doesn't matter). Serial,
        # this was the load's dominant cost for a many-doc shard.
        docs_with_chunks = {c.doc_id for c in chunks}
        sem = asyncio.Semaphore(self._delete_concurrency)

        async def _delete_prior(doc_id: str) -> None:
            async with sem:
                await self.vector_store.delete(doc_id, tenant_id=tenant_id)
                await self.text_index.delete(doc_id, tenant_id=tenant_id)
                if self.graph_store is not None:
                    await self.graph_store.delete_by_doc(
                        doc_id, tenant_id=tenant_id, collection=self.collection
                    )

        if self._delete_prior:
            await asyncio.gather(*(_delete_prior(d) for d in docs_with_chunks))

        # Index. The two legs are independent and individually batched, and each is
        # idempotent under deterministic ids — so gather them rather than idling one
        # store while the other works (#323). This also removes the mid-load leg
        # skew that made a vector-count lead look like a real mismatch: within a
        # batch the legs now advance together instead of vectors-then-text.
        #
        # return_exceptions=True, then re-raise: a bare gather propagates the first
        # failure while the sibling leg keeps running unsupervised, so a failed load
        # could still be writing to one store after index_chunks returned. Awaiting
        # both to completion bounds the write when we raise. Which store ends up
        # ahead on a partial failure is unchanged from the serial version — both
        # orders were already reachable — and a re-load repairs it either way.
        results = await asyncio.gather(
            self.vector_store.upsert(chunks),
            self.text_index.index(chunks),
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, BaseException):
                raise r

        # Knowledge-graph extraction (optional)
        if self.kg_extractor and self.graph_store:
            triples = await self.kg_extractor.extract(chunks)
            # Stamp the owning tenant AND the target collection on every triple so
            # graph deletes/reads can be scoped on both axes regardless of what the
            # extractor populated. The collection stamp is what keeps one
            # collection's triples out of another collection's graph leg (#209).
            for triple in triples:
                triple.tenant_id = tenant_id
                triple.collection = self.collection or ""
            await self.graph_store.add_triples(triples)

        return [c.id for c in chunks]
