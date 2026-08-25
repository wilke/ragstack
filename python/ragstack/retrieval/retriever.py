"""Retrieval pipeline — hybrid vector + BM25 + graph retrieval."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ragstack.models import Chunk, ContextChunk, ScoredChunk
from ragstack.protocols import GraphStore, TextIndex, VectorStore
from ragstack.scoring.scorers import RRFScorer
from ragstack.tenancy import DEFAULT_TENANT, readable_tenants

if TYPE_CHECKING:
    from ragstack.ingestion.boilerplate import BoilerplateConfig


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
        collection: str | None = None,
        max_per_doc: int = 0,
        demote_boilerplate: bool = False,
        boilerplate_config: BoilerplateConfig | None = None,
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
        # The collection this retriever serves. The vector/text legs get their
        # collection for free — their stores ARE per-collection — but one graph
        # store holds every collection's triples, so the graph leg has to name it
        # (#209). ``None`` = unscoped: dev/tests and single-collection callers
        # that build a retriever directly.
        self.collection = collection
        # Post-fusion shaping (both default-off, i.e. no behaviour change):
        # max_per_doc bounds how much of the answer any single document may
        # supply; demote_boilerplate pushes licence/reference/acknowledgement
        # chunks to the back. Both *reorder* the candidate pool and only then cut
        # to top_k, so neither can shrink the result set.
        self.max_per_doc = max_per_doc
        self.demote_boilerplate = demote_boilerplate
        self.boilerplate_config = boilerplate_config

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
        return self.shape(fused)[:top_k]

    def shape(self, fused: list[ScoredChunk]) -> list[ScoredChunk]:
        """Reorder the fused candidate pool before it is cut to ``top_k``.

        Two independent, opt-in passes, applied in this order:

        1. **Boilerplate demotion** — licence footers, reference lists and
           acknowledgement blocks go to the back (see
           :mod:`ragstack.ingestion.boilerplate`). Done first so the diversity
           cap below spends its per-document budget on real content.
        2. **Per-document cap** — at most ``max_per_doc`` chunks from any one
           ``doc_id`` before the rest of that document is demoted.

        Both are *stable demotions*, never deletions: the relative order within
        the promoted and the demoted group is the fused order, and every input
        chunk is still in the output. That is what makes them safe to apply
        before the ``[:top_k]`` cut — the caller gets the same number of results
        it would have got, just drawn from further down the pool. Public so the
        rerank stage (which re-fetches a larger pool and re-sorts it) can apply
        the same shaping to its own final list.
        """
        if self.demote_boilerplate:
            fused = self._demote_boilerplate(fused)
        if self.max_per_doc > 0:
            fused = self._cap_per_doc(fused)
        return fused

    def _demote_boilerplate(self, fused: list[ScoredChunk]) -> list[ScoredChunk]:
        """Stable-partition the pool into content first, boilerplate last.

        Trusts ``metadata["is_boilerplate"]`` when the chunk carries it (stamped
        at ingest); otherwise re-derives it from the text, which is what lets
        this help a corpus that was indexed before the flag existed — the case
        that motivated the setting.
        """
        from ragstack.ingestion.boilerplate import BOILERPLATE_KEY, is_boilerplate

        keep: list[ScoredChunk] = []
        demoted: list[ScoredChunk] = []
        for scored in fused:
            stamped = scored.chunk.metadata.get(BOILERPLATE_KEY)
            flagged = (
                bool(stamped)
                if isinstance(stamped, bool)
                else is_boilerplate(scored.chunk.content, self.boilerplate_config)
            )
            (demoted if flagged else keep).append(scored)
        return keep + demoted

    def _cap_per_doc(self, fused: list[ScoredChunk]) -> list[ScoredChunk]:
        """Stable-partition the pool so no ``doc_id`` leads with more than
        ``max_per_doc`` chunks; the overflow keeps its relative order at the back.

        Demoting rather than dropping the overflow matters: a query whose only
        good matches genuinely all live in one paper still returns ``top_k``
        chunks from that paper, so the cap costs nothing in the single-relevant-
        document case while breaking the monopoly whenever an alternative exists.
        """
        seen: dict[str, int] = {}
        keep: list[ScoredChunk] = []
        overflow: list[ScoredChunk] = []
        for scored in fused:
            doc_id = scored.chunk.doc_id
            seen[doc_id] = seen.get(doc_id, 0) + 1
            (keep if seen[doc_id] <= self.max_per_doc else overflow).append(scored)
        return keep + overflow

    async def _graph_context(
        self, query: str, top_k: int, tenant_id: str | None = None
    ) -> list[ScoredChunk]:
        """Retrieve graph-neighbourhood context for entities in the query.

        ``tenant_id`` is the caller's own tenant; it scopes the neighbourhood
        query so the graph leg reads only the caller's triples plus the shared
        ``public`` corpus (the store derives that scope). Scoping happens at the
        source — both graph stores filter on the triple's stored ``tenant_id``
        (Neo4j in Cypher, in-memory in ``_visible``) — and is re-applied here so
        the leg does not depend on a single store implementation getting it
        right. An unstamped triple (empty ``tenant_id``) is *not* readable by a
        scoped caller: the re-check fails closed, matching Neo4j, where a null
        ``r.tenant_id`` never satisfies ``IN $tenants``.

        ``tenant_id=None`` means deliberately unscoped and reads every triple —
        a dev/test/library affordance, matching the other legs (which take
        ``filters=None``). The HTTP API never reaches it: ``resolve_tenant``
        always yields a concrete tenant (``default`` when auth is disabled), so
        request-borne retrieval is always scoped.

        ``self.collection`` is the second scope (#209). The vector and BM25 legs
        can't cross a collection because their stores are per-collection; the
        graph leg can, because one graph store holds every collection's triples.
        So the collection is pushed into the neighbourhood query and, like the
        tenant, re-checked on the way back — the leg must not depend on a single
        store implementation getting it right. A triple with no collection stamp
        (written before #209) fails both the store filter and this re-check.

        Every pseudo-chunk is stamped with its triple's owning tenant *and*
        collection, the same way real chunks carry ``metadata["tenant_id"]``.
        Without the stamps, ``tenancy.tenant_of`` reads the default tenant for
        every graph result, so a post-retrieval re-check can only pass or fail
        the whole leg wholesale instead of evaluating it per chunk."""
        from ragstack.models import Chunk

        triples = await self.graph_store.query_neighborhood(  # type: ignore[union-attr]
            query,
            depth=self.graph_context_depth,
            tenant_id=tenant_id,
            collection=self.collection,
        )
        if tenant_id is not None:
            allowed = set(readable_tenants(tenant_id))
            triples = [t for t in triples if t.tenant_id in allowed]
        if self.collection is not None:
            triples = [t for t in triples if t.collection == self.collection]
        chunks = []
        for triple in triples[:top_k]:
            content = f"{triple.subject} {triple.predicate} {triple.object}"
            metadata: dict[str, Any] = {"tenant_id": triple.tenant_id or DEFAULT_TENANT}
            if triple.collection:
                metadata["collection"] = triple.collection
            chunks.append(
                ScoredChunk(
                    chunk=Chunk(
                        id=f"graph-{triple.subject}-{triple.predicate}-{triple.object}",
                        doc_id=triple.doc_id,
                        content=content,
                        metadata=metadata,
                    ),
                    score=self.graph_context_score,
                    retrieval_method="graph",
                )
            )
        return chunks


# --------------------------------------------------------------------------- #
# Server-side context expansion (issue #322)
# --------------------------------------------------------------------------- #

#: Metadata keys stamped by ``ingestion.chunkers.link_neighbors``, by walk direction.
_LINK_KEYS = {-1: "prev_chunk_id", 1: "next_chunk_id"}


def _neighbour_id(chunk: Chunk, direction: int) -> str | None:
    """The id of ``chunk``'s neighbour in ``direction`` (-1 = prev, +1 = next),
    or ``None`` at a document edge. Corpora bulk-loaded before the linker was
    tightened stamped the literal string ``"None"`` there (docs/USER-GUIDE.md);
    treat it — and an empty string — as "no link" too."""
    raw = chunk.metadata.get(_LINK_KEYS[direction])
    if raw is None:
        return None
    link = str(raw).strip()
    return link if link and link != "None" else None


async def expand_context(
    store: VectorStore,
    scored: list[ScoredChunk],
    window: int,
    filters: dict[str, Any] | None = None,
) -> dict[str, list[ContextChunk]]:
    """Walk each returned source's ``prev_chunk_id`` / ``next_chunk_id`` up to
    ``window`` hops each way and return, per source chunk id, its visible
    neighbours ordered by position (negative = before). A post-rerank,
    post-truncation step: ``scored`` is read, never modified — the ranking is
    unchanged and no neighbour is merged into it.

    Round trips: the neighbour ids of ALL sources at a given hop are fetched in
    ONE batched ``store.get_chunks`` call — one per hop, so ``window`` (≤ 3)
    calls at most and exactly one at ``window=1``, independent of ``top_k``. A
    hop's ids can't be known before the previous hop's chunks are in hand (ids
    are opaque uuid5s, not derivable from ``chunk_index``), which is why it is
    one call per hop rather than one per request; a hop that needs nothing new
    (every id already in hand) makes no call at all.

    Scope: ``filters`` is the request's scoped filter dict (user filters +
    tenant scope — the same predicate ``search()`` used, #197), so a neighbour
    the caller may not read is simply not returned by the store and the walk
    stops there for that direction: it is omitted, and nothing beyond it is
    reached through it. Document boundaries (no link) end the walk the same way.

    De-duplication: a neighbour that is itself one of the returned sources is
    walked THROUGH (its links are already in hand, so no fetch) but NOT attached
    as context — its content is already in the response as a scored source.
    Raises ``UnknownFilterKey`` (stores/filters.py) unchanged if ``filters``
    carries a key ``get_chunks`` refuses; the caller maps it to a 400.
    """
    if window <= 0 or not scored:
        return {}
    known: dict[str, Chunk] = {s.chunk.id: s.chunk for s in scored}
    source_ids = set(known)
    # Per source, per direction: the chunk the walk is currently standing on.
    cursors: dict[str, dict[int, Chunk]] = {
        cid: {-1: chunk, 1: chunk} for cid, chunk in known.items()
    }
    found: dict[str, list[ContextChunk]] = {}
    for hop in range(1, window + 1):
        # (source id, direction, neighbour id) for every live walk at this hop.
        steps: list[tuple[str, int, str]] = []
        needed: dict[str, None] = {}  # ordered set of ids not yet in hand
        for cid, dirs in cursors.items():
            for direction, at in list(dirs.items()):
                nid = _neighbour_id(at, direction)
                if nid is None:
                    del dirs[direction]  # document edge: this walk is over
                    continue
                steps.append((cid, direction, nid))
                if nid not in known:
                    needed[nid] = None
        if not steps:
            break
        if needed:
            for c in await store.get_chunks(list(needed), filters):
                known[c.id] = c
        for cid, direction, nid in steps:
            neighbour = known.get(nid)
            if neighbour is None:
                # Not visible to this caller (out of scope) or dangling: omit,
                # and stop this direction — nothing beyond it is reachable.
                del cursors[cid][direction]
                continue
            cursors[cid][direction] = neighbour
            if nid in source_ids:
                continue  # already a scored source: don't duplicate its content
            found.setdefault(cid, []).append(
                ContextChunk(chunk_id=nid, position=direction * hop, content=neighbour.content)
            )
    for ctx in found.values():
        ctx.sort(key=lambda c: c.position)
    return found
