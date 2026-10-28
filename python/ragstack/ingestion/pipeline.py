"""Ingestion pipeline — orchestrates loading, chunking, embedding, and indexing."""
from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from ragstack.ingestion.boilerplate import BoilerplateFilter
from ragstack.ingestion.chunkers import link_neighbors_by_document
from ragstack.ingestion.doi_metadata import DoiEnricher
from ragstack.ingestion.structure import (
    attach_structure,
    stamp_documents,
    structure_provenance,
)
from ragstack.ingestion.symbol_glossary import SymbolGlossaryAttacher
from ragstack.ingestion.work_identity import (
    POLICY_OFF,
    DuplicateCheck,
    DuplicateWork,
    WorkIdentityStage,
    refusal_of,
)
from ragstack.metadata_schema import validate_chunks
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
from ragstack.tenancy import DEFAULT_TENANT, readable_tenants

log = logging.getLogger(__name__)


class EmptyIngestError(RuntimeError):
    """A source produced no embeddable chunks — either it had no chunkable
    content or every chunk was quarantined as unembeddable. Raised before the
    replace step so a failed/empty re-ingest never deletes the document's
    previously-ingested data."""


@dataclass
class PreparedSource:
    """A source after load -> (DOI enrich) -> chunk -> boilerplate filter, BEFORE
    the embed: the text-only half of :meth:`IngestionPipeline.embed_source`.

    Exists so a job can be sized — ``len(chunks)`` per source, summed — before
    the first GPU call and before the first store write (the per-collection
    chunk cap, #291), without loading or chunking anything twice: the same
    chunks then go straight into :meth:`IngestionPipeline.embed_prepared`.
    Holds chunk TEXT only (no vectors), so a whole job's worth is small; a
    caller that has already decided to refuse may drop ``chunks`` and keep
    counting. ``doc_ids`` names every loaded document (the skipped-document
    diagnostic needs them after the survivors are known); ``produced`` is the
    chunk count before the boilerplate filter (for the empty-ingest message).
    """

    source: str
    doc_ids: list[str] = field(default_factory=list)
    chunks: list[Chunk] = field(default_factory=list)
    produced: int = 0


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
        glossary_attacher: SymbolGlossaryAttacher | None = None,
        work_identity: WorkIdentityStage | None = None,
        structure_metadata: bool = False,
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
        # Optional per-document symbol glossary (#754), applied AFTER the
        # boilerplate filter (a dropped chunk needs no glossary) and before the
        # embed. It writes only ``Chunk.symbol_glossary`` and one metadata
        # marker — never ``content`` — so what is embedded is unchanged. ``None``
        # = disabled, and the path is byte-for-byte what it was.
        self.glossary_attacher = glossary_attacher
        # What the glossary step actually did, cumulatively: "applied" /
        # "degraded" batches and "chunks_with_glossary". Read by
        # :meth:`glossary_provenance`, so a manifest or receipt never claims a
        # step that degraded on every batch (#754 review).
        self.glossary_counts: Counter[str] = Counter()
        # Optional document-structure step (#828; STRUCTURE_METADATA /
        # --structure-metadata). On: the ingest adapters run after load and DOI
        # enrichment (``attach_structure``; none exist yet in PR 1, so only a
        # structure that arrived with the document — the JSONL ``structure``
        # key — is used), and every chunk of a structured document is stamped
        # with section/role/block metadata between chunking and the boilerplate
        # filter (``_stamp_structure``). Payload fields only: content, offsets,
        # ids and the embedded text are unchanged, so it is not collection
        # identity (Q3). Off (the default): the path is byte-for-byte what it
        # was. ``structure_counts`` feeds the receipt's ``enrichment.structure``.
        self.structure_metadata = structure_metadata
        self.structure_counts: Counter[str] = Counter()
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
        # The duplicate-paper guard (#747). Runs only when a caller passes a
        # ``duplicate_policy`` other than "off" (the default on every method), so
        # an unguarded caller is byte-for-byte what it was. The lookup goes to
        # THIS pipeline's text index — the collection the write lands in.
        self.work_identity = work_identity or WorkIdentityStage(
            text_index, profile=getattr(doi_enricher, "profile", None)
        )

    async def ingest(
        self,
        source: str,
        tenant_id: str = DEFAULT_TENANT,
        *,
        duplicate_policy: str | DuplicateCheck = POLICY_OFF,
    ) -> list[str]:
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
            await self.embed_source(
                source, tenant_id=tenant_id, duplicate_policy=duplicate_policy
            ),
            tenant_id=tenant_id,
        )

    async def embed_source(
        self,
        source: str,
        tenant_id: str = DEFAULT_TENANT,
        *,
        duplicate_policy: str | DuplicateCheck = POLICY_OFF,
    ) -> list[Chunk]:
        """Load, chunk, and embed ``source``; return the surviving embedded chunks.

        The first half of :meth:`ingest`. Touches **only** the loader, chunker,
        and embedder — never the vector/text/graph stores — so it can run on a
        GPU/embedding worker decoupled from Qdrant (ADR-0001 offline plane, #141).
        Each returned chunk carries its ``embedding``, tenant stamp, and a
        neighbor chain over the survivors. Raises :class:`EmptyIngestError` when
        nothing embeddable was produced, so a caller never advances to the
        store-mutating half with an empty replacement.

        A literal composition — :meth:`prepare_source` (load + text-only
        chunking) then :meth:`embed_prepared` (the GPU half plus the guards) —
        so a caller that must size a job before embedding it (the chunk cap,
        #291) runs the same two steps with a count in between, and nothing is
        loaded or chunked twice. :meth:`embed_documents` is the same pair of
        primitives for a caller that owns the document list.
        """
        prepared = await self.prepare_source(
            source, tenant_id=tenant_id, duplicate_policy=duplicate_policy
        )
        return await self.embed_prepared(prepared, tenant_id)

    async def prepare_source(
        self,
        source: str,
        *,
        tenant_id: str = DEFAULT_TENANT,
        duplicate_policy: str | DuplicateCheck = POLICY_OFF,
    ) -> PreparedSource:
        """Load ``source`` and run :meth:`prepare_documents` — the text-only,
        store-free, GPU-free first step of :meth:`embed_source`. Loader errors
        propagate exactly as they did from ``embed_source``.

        Raises :class:`~ragstack.ingestion.work_identity.DuplicateWork` when the
        duplicate guard refused EVERY loaded document (#747): one source is one
        job item, and that item fails with the refusal as its error — before
        any embed or store write. A partially refused multi-document source
        goes on with the survivors; the refused ones are logged."""
        documents: list[Document] = self.loader.load(source)
        chunks, produced = await self.prepare_documents(
            documents, source=source, tenant_id=tenant_id,
            duplicate_policy=duplicate_policy,
        )
        refused = [d for d in documents if refusal_of(d)]
        if refused and len(refused) == len(documents):
            raise DuplicateWork(refusal_of(refused[0]))
        return PreparedSource(
            source=source, doc_ids=[d.id for d in documents if not refusal_of(d)],
            chunks=chunks, produced=produced,
        )

    async def prepare_documents(
        self,
        documents: list[Document],
        source: str = "",
        *,
        tenant_id: str = DEFAULT_TENANT,
        duplicate_policy: str | DuplicateCheck = POLICY_OFF,
    ) -> tuple[list[Chunk], int]:
        """Enrich, chunk and boilerplate-filter already-loaded ``documents``;
        return ``(chunks, produced)`` — the text-only chunks and how many were
        produced before the boilerplate filter. Touches only the chunker (and
        the optional DOI enricher): no embedder, no store. ``source`` labels
        log lines.

        ``duplicate_policy`` (#747): a policy name (``work_identity.POLICIES``)
        or a ``work_identity.DuplicateCheck`` carrying the lookup scope and the
        job's shared ``seen`` keys. Anything but ``"off"`` runs the
        duplicate-paper guard: ids filled from the header and a ``PMC…`` file
        name before enrichment, then ONE text-index lookup scoped to the
        check's ``tenants`` (default ``readable_tenants(tenant_id)``) after it,
        BEFORE the chunk loop. A refused document produces no chunks and is
        marked (``work_identity.refusal_of``); an admitted duplicate's chunks
        carry ``duplicate_of``. ``documents`` itself is never shortened."""
        check = DuplicateCheck.coerce(duplicate_policy)
        guarded = check.policy != POLICY_OFF
        if guarded:
            self.work_identity.fill_ids(documents)
        await self._apply_doi_enrichment(documents)
        to_chunk = documents
        if guarded:
            to_chunk = await self.work_identity.admit(
                documents,
                tenants=check.tenants if check.tenants is not None
                else readable_tenants(tenant_id),
                policy=check.policy, source=source, seen=check.seen,
            )
        self._attach_structure(to_chunk)
        all_chunks: list[Chunk] = []
        for doc in to_chunk:
            # Run chunking in a worker thread: chunkers are synchronous, and the
            # SemanticChunker blocks on a (bridged) embed round-trip, which would
            # otherwise stall the event loop. to_thread keeps the loop responsive.
            all_chunks.extend(await asyncio.to_thread(self.chunker.chunk, doc))
        produced = len(all_chunks)
        await self._stamp_structure(to_chunk, all_chunks)
        kept = self._filter_boilerplate(all_chunks, source)
        self._attach_glossary(documents, kept, source)
        return kept, produced

    async def _embed_chunks(
        self, chunks: list[Chunk], tenant_id: str, source: str
    ) -> tuple[list[Chunk], int]:
        """Embed (quarantining) + neighbour-link ``chunks``; log the quarantine
        count. Returns ``(kept, quarantined)``. The one GPU step both
        :meth:`embed_prepared` and :meth:`embed_documents` are built on."""
        kept, quarantined = await self._embed_and_link(chunks, tenant_id)
        if quarantined:
            log.warning(
                "ingest %r: quarantined %d unembeddable chunk(s)", source, quarantined
            )
        return kept, quarantined

    async def embed_prepared(
        self, prepared: PreparedSource, tenant_id: str = DEFAULT_TENANT
    ) -> list[Chunk]:
        """Embed a :class:`PreparedSource` — the second step of
        :meth:`embed_source`, with its empty-ingest guard and skipped-document
        diagnostic. Returns the surviving embedded chunks."""
        source, produced = prepared.source, prepared.produced
        kept, quarantined = await self._embed_chunks(prepared.chunks, tenant_id, source)

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
        # here, where we still have the loaded document ids to name them.
        docs_with_chunks = {c.doc_id for c in kept}
        skipped = [d for d in prepared.doc_ids if d not in docs_with_chunks]
        if skipped:
            log.warning(
                "ingest %r: kept prior data for %d document(s) with no surviving "
                "chunks this run (empty or all-quarantined): %s",
                source, len(skipped), skipped,
            )
        return kept

    async def ingest_prepared(
        self, prepared: PreparedSource, tenant_id: str = DEFAULT_TENANT
    ) -> list[str]:
        """:meth:`ingest` from a :class:`PreparedSource`: embed, then index. The
        path a cap-checked job takes for each of its sources once the whole job
        has been sized and admitted (#291) — identical to ``ingest`` from the
        embed step on."""
        return await self.index_chunks(
            await self.embed_prepared(prepared, tenant_id=tenant_id), tenant_id=tenant_id
        )

    async def embed_documents(
        self,
        documents: list[Document],
        tenant_id: str = DEFAULT_TENANT,
        source: str = "",
        *,
        duplicate_policy: str | DuplicateCheck = POLICY_OFF,
    ) -> tuple[list[Chunk], int, int]:
        """Enrich, chunk, and embed already-loaded ``documents``; return
        ``(kept, produced, quarantined)`` — the surviving embedded chunks, how
        many chunks were produced before boilerplate filtering, and how many
        were quarantined as unembeddable.

        The loader-free core of :meth:`embed_source`, so a caller that owns the
        document list — the per-batch ``ingest_shard`` tool (#203 2b), which has
        to attribute the outcome to EACH document of a batch — can run the same
        chunk → filter → embed → link path and then group ``kept`` by
        ``doc_id``. Unlike :meth:`embed_source` it does NOT raise when nothing
        survives: an empty result is data for the caller's own per-document
        bookkeeping (:meth:`embed_source` turns it into ``EmptyIngestError``
        for the single-source path, where it protects prior data). Touches
        only the chunker and embedder — never a store. ``source`` only labels
        log lines. The same two primitives as ``embed_source``:
        :meth:`prepare_documents` then the embed step. A document the duplicate
        guard refused (``duplicate_policy``, #747) simply has no chunks in
        ``kept``; ``work_identity.refusal_of(doc)`` names why.
        """
        chunks, produced = await self.prepare_documents(
            documents, source=source, tenant_id=tenant_id,
            duplicate_policy=duplicate_policy,
        )
        if not chunks:
            # Nothing to embed — every document refused by the duplicate guard,
            # empty, or all boilerplate. Skip the embed step entirely: an empty
            # request is not something every backend accepts (vLLM 400s, which
            # once recursed into RecursionError and failed the task, #768), and
            # the caller's per-document bookkeeping needs no vectors.
            return [], produced, 0
        kept, quarantined = await self._embed_chunks(chunks, tenant_id, source)
        return kept, produced, quarantined

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

    def _attach_glossary(
        self, documents: list[Document], chunks: list[Chunk], source: str
    ) -> None:
        """Give each chunk the definitions of the symbols it uses, from its own
        document's glossary (#754). Runs after the boilerplate filter.

        Never fatal, the boilerplate filter's contract: a bug in the extractor
        must not fail an ingest, so any exception degrades to "no glossary" —
        every chunk's field reset to ``""`` and its marker removed, so a
        half-applied glossary never reaches a store."""
        if self.glossary_attacher is None or not chunks:
            return
        try:
            result = self.glossary_attacher.apply(documents, chunks)
        except Exception as e:  # noqa: BLE001 - degrade, never fail the ingest
            self.glossary_counts["degraded"] += 1
            log.warning("symbol glossary skipped for %r: %s", source, e)
            from ragstack.ingestion.symbol_glossary import VERSION_KEY

            for c in chunks:
                c.symbol_glossary = ""
                c.metadata.pop(VERSION_KEY, None)
            return
        self.glossary_counts["applied"] += 1
        self.glossary_counts["chunks_with_glossary"] += result.chunks_with_glossary
        if result.chunks_with_glossary:
            log.info("ingest %r: symbol glossary %s", source, result.summary())

    def _attach_structure(self, documents: list[Document]) -> None:
        """Run the ingest adapters (#828 §2) when the structure step is on."""
        if self.structure_metadata:
            attach_structure(documents, counts=self.structure_counts)

    async def _stamp_structure(self, documents: list[Document], chunks: list[Chunk]) -> None:
        """Stamp the chunks of structured documents (#828 §6). The one helper
        both chunk loops call — :meth:`prepare_documents` (paths A, B) and
        :meth:`iter_embed_source` (path C) — between the chunk loop and
        :meth:`_filter_boilerplate`; ``scripts/ingest_jsonl.py`` calls the same
        :func:`~ragstack.ingestion.structure.stamp_documents`. Off → no-op, and
        no chunk gets any structure key."""
        if self.structure_metadata and chunks:
            # Off the event loop, beside the chunker: the structure key is
            # caller-supplied on the by-reference .jsonl path, so its cost
            # must never stall the loop. The worker thread counts into a LOCAL
            # Counter; the shared one is mutated only here, on the loop thread
            # — a concurrent job's structure_provenance() iterates it
            # (Counter.__sub__), and a thread inserting a key mid-iteration
            # raises "dictionary changed size during iteration".
            local: Counter[str] = Counter()
            await asyncio.to_thread(stamp_documents, documents, chunks, local)
            self.structure_counts.update(local)

    def structure_provenance(self, since: Counter[str] | None = None) -> dict[str, Any] | None:
        """The ``enrichment.structure`` step record (#828 §6) for what the step
        did since ``since`` (a copy of :attr:`structure_counts`); ``None`` when
        the step is off or saw no document."""
        if not self.structure_metadata:
            return None
        return structure_provenance(self.structure_counts, since)

    def glossary_provenance(self, since: Counter[str] | None = None) -> dict[str, Any] | None:
        """The step record for a manifest/receipt ``enrichment`` (ADR-0011), from
        THIS pipeline's attacher, with what it did since ``since`` (a copy of
        :attr:`glossary_counts` taken earlier; ``None`` = since construction).
        ``None`` when no glossary step is configured or it never ran; a step that
        only degraded is recorded WITH ``applied_batches: 0``, never as applied."""
        if self.glossary_attacher is None:
            return None
        done = self.glossary_counts - (since or Counter())
        if not done["applied"] and not done["degraded"]:
            return None
        rec = self.glossary_attacher.provenance()
        rec["symbol_glossary"].update(
            applied_batches=done["applied"],
            degraded_batches=done["degraded"],
            chunks_with_glossary=done["chunks_with_glossary"],
        )
        return rec

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
        if not all_chunks:
            return [], 0  # no embed call for nothing (#768)
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
        self._attach_structure(documents)
        step = max(1, group_size)
        for start in range(0, len(documents), step):
            group = documents[start : start + step]
            all_chunks: list[Chunk] = []
            for doc in group:
                all_chunks.extend(await asyncio.to_thread(self.chunker.chunk, doc))
            if not all_chunks:
                continue
            await self._stamp_structure(group, all_chunks)
            all_chunks = self._filter_boilerplate(all_chunks, source)
            if not all_chunks:
                continue
            # Whole documents stay within a group, so the per-document glossary
            # sees each document's full text here, as it does in
            # prepare_documents.
            self._attach_glossary(group, all_chunks, source)
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
        # THE INGEST BOUNDARY (#603). Refuse a chunk whose declared metadata
        # carries the wrong type, BEFORE the delete-prior below and before either
        # store is written. Here because an Elasticsearch mapping is inferred from
        # the first document to arrive and then cannot be changed in place: a
        # `pmid` that arrives as an int does not produce a bad row, it produces a
        # collection permanently incompatible with its peers for cross-collection
        # filtering, fixable only by reindexing (#594/#600). This is the cheap
        # place to say no.
        #
        # Before the delete, deliberately: a refused batch must not have destroyed
        # the prior version of the documents it was meant to replace — the same
        # rule EmptyIngestError exists to enforce one layer up.
        #
        # Undeclared keys pass through untouched; the namespace is open by design.
        validate_chunks(chunks, where="index_chunks")

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
