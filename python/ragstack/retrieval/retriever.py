"""Retrieval pipeline — hybrid vector + BM25 + graph retrieval."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ragstack.models import Chunk, ContextChunk, ScoredChunk

# The submodule, not the package: ``observability/__init__`` imports the
# middleware and so pulls in starlette, which this module has no business
# depending on. ``stage`` is a no-op outside a request context, so the ingest
# pipeline, the CLI scripts and every library caller are unaffected.
from ragstack.observability.stages import stage
from ragstack.protocols import GraphStore, TextIndex, VectorStore
from ragstack.scoring.scorers import RRFScorer
from ragstack.tenancy import DEFAULT_TENANT, readable_tenants, scope_filters

if TYPE_CHECKING:
    from ragstack.ingestion.boilerplate import BoilerplateConfig
    from ragstack.models import Triple


#: A query token: a run of word characters, optionally joined by a hyphen or
#: apostrophe ("covid-19", "parkinson's"). Everything else is punctuation and is
#: dropped, so "aspirin," and "aspirin" are the same token.
_TOKEN_RE = re.compile(r"\w+(?:[-']\w+)*")

#: English function words that are never a 1-gram entity candidate (#349). With
#: an entity literally named "the" or "of" in scope, "the role of aspirin in the
#: heart" would match ['the', 'of', 'aspirin'] — the stopwords tie with the real
#: entity on length, win on query order, and eat cap slots and neighbourhood
#: calls. Applied to 1-grams ONLY: multi-grams keep them ("bank of england").
#: Deliberately NOT a minimum length — 2-letter biomedical abbreviations (MI,
#: TB, IL) are real entities — and "no" is left out for the same reason (NO,
#: nitric oxide). Articles, prepositions, conjunctions, pronouns, auxiliaries.
GRAPH_QUERY_STOPWORDS: frozenset[str] = frozenset({
    # articles / determiners
    "a", "an", "the", "this", "that", "these", "those", "some", "any", "each",
    # prepositions
    "of", "in", "on", "at", "to", "for", "from", "by", "with", "about", "into",
    "onto", "over", "under", "after", "before", "between", "through", "during",
    "without", "within", "among", "against", "than", "as", "via", "per",
    # conjunctions
    "and", "or", "but", "nor", "so", "if", "because", "while", "whether", "then",
    # pronouns / question words
    "i", "me", "my", "we", "us", "our", "you", "your", "he", "him", "his", "she",
    "her", "it", "its", "they", "them", "their", "what", "which", "who", "whom",
    "whose", "how", "when", "where", "why",
    # auxiliaries / common verbs
    "am", "is", "are", "was", "were", "be", "been", "being", "do", "does", "did",
    "have", "has", "had", "can", "could", "will", "would", "shall", "should",
    "may", "might", "must", "not",
})


@dataclass(frozen=True)
class Candidate:
    """One n-gram of the query, already case-folded. ``position`` is the index
    of its first token in the query — the tie-breaker after length when the
    graph leg picks which matched entities to expand (#349)."""

    text: str
    n_tokens: int
    position: int


def query_candidates(query: str, ngram_max: int) -> list[Candidate]:
    """The distinct 1..``ngram_max``-grams of ``query`` as entity-name
    candidates: a simple regex word split, lower-cased, punctuation stripped,
    tokens re-joined with single spaces. A 1-gram in :data:`GRAPH_QUERY_STOPWORDS`
    is skipped (longer n-grams containing it are not). Ordered by n then
    position, and a text that occurs twice keeps its first position. Pure
    string work — no model."""
    tokens = _TOKEN_RE.findall(query.lower())
    seen: set[str] = set()
    out: list[Candidate] = []
    for n in range(1, max(1, ngram_max) + 1):
        for i in range(len(tokens) - n + 1):
            text = " ".join(tokens[i:i + n])
            if n == 1 and text in GRAPH_QUERY_STOPWORDS:
                continue
            if text not in seen:
                seen.add(text)
                out.append(Candidate(text, n, i))
    return out


def filter_by_confidence(triples: list[Triple], floor: int) -> list[Triple]:
    """Drop triples whose ``confidence`` is below ``floor``. A floor of 0 (the
    default) is a no-op that returns the input list itself — the common path
    costs nothing and cannot change results."""
    if floor <= 0:
        return triples
    return [t for t in triples if t.confidence >= floor]


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
        graph_min_confidence: int | None = None,
        graph_query_entity_max: int | None = None,
        graph_query_ngram_max: int | None = None,
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
        # Confidence floor for the graph leg (#347). ``None`` = read
        # ``settings.graph_min_confidence`` at query time, so the setting is live
        # for API-built retrievers without a deps.py change (that file is being
        # reworked under #276; wiring the explicit kwarg there is a one-liner
        # follow-up). Tests and direct callers pass an int.
        self.graph_min_confidence = graph_min_confidence
        # Query-side entity extraction for the graph leg (#349): how many
        # matched entities get a neighbourhood query, and the longest n-gram
        # tried against the entity index. Same ``None`` = live-settings
        # convention as the confidence floor.
        self.graph_query_entity_max = graph_query_entity_max
        self.graph_query_ngram_max = graph_query_ngram_max
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

        # Every leg is timed (#427 W3). `self.collection` is the PHYSICAL
        # collection name and is None for unscoped dev/test retrievers, which
        # renders as `-`; the registry id lives on the request context.
        #
        # Dense retrieval — unless BM25-only. (Skips the query embed for bm25 mode.)
        if mode != "bm25":
            with stage("embed"):
                query_vectors: list[list[float]] = await self.embedder.embed([query])  # type: ignore[attr-defined]
            # The leg that failed in the incident #427 was opened for. The timer
            # records whether or not the search raises, which is the whole point
            # — a search that spent 30 s and then timed out is the observation.
            with stage("vector", self.collection):
                ranked_lists.append(
                    await self.vector_store.search(query_vectors[0], top_k=depth, filters=filters)
                )

        # Sparse / BM25 retrieval — unless vector-only.
        if mode != "vector":
            with stage("text", self.collection):
                ranked_lists.append(
                    await self.text_index.search(query, top_k=depth, filters=filters)
                )

        # Optional graph-augmented context (independent of mode).
        if use_graph and self.graph_store:
            with stage("graph", self.collection):
                graph_chunks = await self._graph_context(query, top_k, tenant_id)
            if graph_chunks:
                ranked_lists.append(graph_chunks)

        with stage("fuse"):
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

    def _min_confidence(self) -> int:
        if self.graph_min_confidence is None:
            from ragstack.config import settings

            return settings.graph_min_confidence
        return self.graph_min_confidence

    def _entity_max(self) -> int:
        if self.graph_query_entity_max is None:
            from ragstack.config import settings

            return settings.graph_query_entity_max
        return self.graph_query_entity_max

    def _ngram_max(self) -> int:
        if self.graph_query_ngram_max is None:
            from ragstack.config import settings

            return settings.graph_query_ngram_max
        return self.graph_query_ngram_max

    async def query_entities(self, query: str, tenant_id: str | None = None) -> list[str]:
        """The entities of ``query`` this retriever's graph leg will expand, in
        expansion order — :func:`query_entities` over this retriever's scope
        (#349). Public so the matching step can be measured on its own."""
        assert self.graph_store is not None
        return await query_entities(
            self.graph_store, query, tenant_id,
            [self.collection] if self.collection is not None else None,
            entity_max=self._entity_max(), ngram_max=self._ngram_max(),
        )

    async def _graph_context(
        self, query: str, top_k: int, tenant_id: str | None = None
    ) -> list[ScoredChunk]:
        """Retrieve graph-neighbourhood context for the entities in the query —
        :func:`graph_context` over this retriever's scope. The entities are
        extracted first (#349, see :func:`query_entities`): one
        ``query_neighborhood`` per matched entity, at most
        ``graph_query_entity_max`` of them, never the raw query string.

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
        assert self.graph_store is not None
        return await graph_context(
            self.graph_store,
            query,
            top_k,
            tenant_id,
            [self.collection] if self.collection is not None else None,
            depth=self.graph_context_depth,
            score=self.graph_context_score,
            min_confidence=self._min_confidence(),
            entity_max=self._entity_max(),
            ngram_max=self._ngram_max(),
        )


def _collection_scope(collections: list[str] | None) -> str | list[str] | None:
    """The ``collection`` argument the graph store gets: ``None`` unscoped, the
    bare name for one collection (so the store's single-value predicate is
    byte-identical to before #253), the list for several (``IN [...]``)."""
    if collections is None:
        return None
    return collections[0] if len(collections) == 1 else list(collections)


async def query_entities(
    graph_store: GraphStore,
    query: str,
    tenant_id: str | None,
    collections: list[str] | None,
    *,
    entity_max: int,
    ngram_max: int,
) -> list[str]:
    """The entities of ``query`` the graph leg will expand, in expansion order
    (#349). A free function, like :func:`graph_context`, so the single- and
    multi-collection retrievers share it; public so the perf test and the
    ablation harness (#122) can measure the matching step on its own.

    The rule: the query's 1..``ngram_max``-gram candidates
    (:func:`query_candidates`) are handed to ``GraphStore.match_entities`` in
    ONE call — an indexed, exact, case-folded lookup against the entity names
    in the caller's readable ``(tenant, collection)`` scope, never a
    ``CONTAINS`` over the sentence. Of the candidates that name an entity, the
    longest (most tokens) wins, ties broken by position in the query, and the
    first ``entity_max`` are kept. A query with no tokens costs no store call
    at all; a query whose candidates match nothing costs the one lookup and no
    neighbourhood query. Nothing here calls a model: the LLM variant of the
    issue is deliberately not built (#350)."""
    candidates = query_candidates(query, ngram_max)
    if not candidates:
        return []
    by_text = {c.text: c for c in candidates}
    matched = await graph_store.match_entities(
        [c.text for c in candidates],
        tenant_id=tenant_id,
        collection=_collection_scope(collections),
    )
    # Fold and re-validate what came back: a store may return the stored
    # surface form rather than the candidate, and must not be able to add an
    # entity the query never named.
    names = {m.lower() for m in matched if m.lower() in by_text}
    ranked = sorted(names, key=lambda t: (-by_text[t].n_tokens, by_text[t].position))
    return ranked[: max(0, entity_max)]


async def graph_context(
    graph_store: GraphStore,
    query: str,
    top_k: int,
    tenant_id: str | None,
    collections: list[str] | None,
    *,
    depth: int = 1,
    score: float = 0.5,
    min_confidence: int = 0,
    entity_max: int,
    ngram_max: int,
) -> list[ScoredChunk]:
    """The graph leg as a free function, scoped to ``tenant_id`` and to
    ``collections`` — one physical collection name (the single-collection
    retriever, passed to the store as the bare name so its single-value
    predicate is byte-identical to before) or several (the multi-collection
    fan-out of issue #253, passed as a list: ``collection IN [...]``, exact on
    Neo4j and on the in-memory store — a graph property predicate, not an HNSW
    payload filter, so #199 does not apply). ``None`` is the deliberately
    unscoped dev/library read.

    The entities are *extracted first* (#349): :func:`query_entities` matches
    the query's n-gram candidates exactly against the scoped entity index (one
    ``match_entities`` call), then this runs ONE ``query_neighborhood`` per
    matched entity — at most ``entity_max`` of them — and unions the triples
    (deduplicated on the triple's identity, first-seen order, so an entity pair
    that shares an edge contributes it once). No matched entity means an empty
    leg and no neighbourhood call; the raw query string is never handed to the
    store. Before #349 it was, and the store's ``CONTAINS`` match meant a
    multi-word query almost never fired the leg.

    Both scopes are re-checked on the way back, per
    :meth:`HybridRetriever._graph_context`; the ``min_confidence`` evidence
    floor (#347) is applied after them and fails OPEN (see the comment below).
    ``top_k`` is the pseudo-chunk budget of the whole call — with several
    collections it is shared across them, not ``top_k`` each. Every
    pseudo-chunk carries ``metadata["collection"]`` = its triple's physical
    collection, which is how the fan-out maps it back to a registry id."""
    entities = await query_entities(
        graph_store, query, tenant_id, collections,
        entity_max=entity_max, ngram_max=ngram_max,
    )
    if not entities:
        return []
    scope = _collection_scope(collections)
    triples: list[Triple] = []
    seen: set[tuple[str, str, str, str, str, str]] = set()
    for entity in entities:
        neighbourhood = await graph_store.query_neighborhood(
            entity, depth=depth, tenant_id=tenant_id, collection=scope,
        )
        for t in neighbourhood:
            key = (t.subject, t.predicate, t.object, t.doc_id, t.tenant_id, t.collection)
            if key not in seen:
                seen.add(key)
                triples.append(t)
    if tenant_id is not None:
        allowed = set(readable_tenants(tenant_id))
        triples = [t for t in triples if t.tenant_id in allowed]
    if collections is not None:
        wanted = set(collections)
        triples = [t for t in triples if t.collection in wanted]
    # Evidence floor (#347). NOTE this deliberately fails OPEN, the opposite
    # of the two scope re-checks above and of the #209 convention (an
    # unstamped ``collection`` is invisible). Tenant/collection are safety
    # boundaries, where "unknown" must mean "not yours". Confidence is a
    # quality axis on a field that did not exist before #347: failing closed
    # would make every pre-existing triple vanish the moment the field was
    # added — a silent corpus-wide regression, not a safety property. So an
    # unstamped triple has confidence 0, the default floor is 0, and it
    # passes; an operator opts into filtering by raising the floor.
    triples = filter_by_confidence(triples, min_confidence)
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
                score=score,
                retrieval_method="graph",
            )
        )
    return chunks


# --------------------------------------------------------------------------- #
# Multi-collection fan-out (issue #253)
# --------------------------------------------------------------------------- #

#: Metadata key carrying the registry-collection stamp on the per-leg COPY of
#: a retrieved chunk. The stamp has to survive a reranker, and the ``Scorer``
#: protocol only promises ``ScoredChunk``s over the candidate chunks — not the
#: same objects (a scorer may ``model_copy()`` them), so object identity is not
#: enough. The key rides on the copy only (the store's own chunk is never
#: mutated) and is stripped before the chunk's metadata reaches a ``Source``
#: (``routers.query._source_metadata``).
STAMP_KEY = "_rs_collection"


@dataclass
class CollectionLeg:
    """One member of a multi-collection request: its registry id, its
    already-collection-scoped retriever (the stores behind a
    :class:`HybridRetriever` ARE per-collection), the physical collection name
    its graph triples are stamped with, and the extra writer-tenants the
    caller may read in THIS collection (share-based widening, computed once
    per entry by the router and reused for context expansion)."""

    id: str
    retriever: Any
    physical: str = ""
    vector_store: Any = None
    extra_tenants: list[str] = field(default_factory=list)

    def filters(self, filters: dict[str, Any] | None, tenant_id: str | None) -> dict[str, Any] | None:
        """The scoped filter dict this leg's stores see: the caller's filters
        plus ``tenant_id``'s readable tenants widened by this leg's
        ``extra_tenants`` — always single-collection by construction (the
        scope is the store, never a many-valued ``collection`` predicate).
        ``tenant_id=None`` is the unscoped library path: filters pass through."""
        if tenant_id is None:
            return filters
        return scope_filters(filters or {}, tenant_id, self.extra_tenants)


class MultiCollectionRetriever:
    """N single-collection legs, run concurrently, fused with RRF — never one
    many-valued store filter (#199, #354).

    Implements the same ``retrieve`` surface as :class:`HybridRetriever` so the
    router's fusion/rerank/shaping code (``_retrieve_fused``) drives it
    unchanged. Semantics:

    * ``retrieve(query, top_k=depth, ...)`` runs every leg with that SAME
      ``top_k`` — the per-leg candidate depth the single-collection path uses —
      and returns the fused UNION un-truncated (at most ``N × depth``
      candidates, ``N ≤ 5``): the caller reranks ONCE over the union and cuts
      to its ``top_k`` afterwards. This is deliberately more than ``depth``
      results.
    * Every result is stamped with its leg's registry id — on
      ``ScoredChunk.collection`` AND, on a shallow copy of the chunk, under
      ``metadata[STAMP_KEY]`` — so identity is ``(collection, chunk id)``
      through fusion, rerank and shaping, and a document present in two
      collections appears once per collection. The metadata stamp is what a
      reranker cannot lose, whatever it does with the chunk objects.
    * RRF ties resolve in request order: the fusion is a stable sort over the
      legs as listed, so at equal fused score the earlier collection's chunk
      ranks first.
    * One leg is not fused at all — its ranked list is returned as-is (with
      the stamp), so ``collections: [x]`` is byte-for-byte ``collection: x``
      plus the stamp, graph leg included.
    * With two or more legs the legs run WITHOUT their own graph leg and the
      graph is ONE neighbourhood query with ``collection IN [physical names]``
      (:func:`graph_context`), fused as one more ranked list; each graph
      pseudo-chunk is stamped with the registry id its triple's collection maps
      to (co-resident stores map to the first such leg).
    * Shaping (``shape`` / ``max_per_doc`` / ``demote_boilerplate``) mirrors
      the first leg's so the router's ``_shaping_active`` sees the same
      configuration it would for one collection (settings are process-wide).
    """

    def __init__(
        self,
        legs: list[CollectionLeg],
        *,
        graph_store: GraphStore | None = None,
        rrf_scorer: RRFScorer | None = None,
        graph_context_score: float = 0.5,
        graph_context_depth: int = 1,
        graph_min_confidence: int | None = None,
        graph_query_entity_max: int | None = None,
        graph_query_ngram_max: int | None = None,
    ) -> None:
        if not legs:
            raise ValueError("MultiCollectionRetriever needs at least one leg")
        self.legs = legs
        self.graph_store = graph_store
        self.rrf = rrf_scorer or RRFScorer()
        self.graph_context_score = graph_context_score
        self.graph_context_depth = graph_context_depth
        # Evidence floor for the one graph query (#347); ``None`` = the setting
        # at query time, exactly as HybridRetriever reads it.
        self.graph_min_confidence = graph_min_confidence
        # Query-side entity extraction knobs (#349), same rule.
        self.graph_query_entity_max = graph_query_entity_max
        self.graph_query_ngram_max = graph_query_ngram_max
        first = legs[0].retriever
        self.max_per_doc = int(getattr(first, "max_per_doc", 0) or 0)
        self.demote_boilerplate = bool(getattr(first, "demote_boilerplate", False))

    @property
    def collections(self) -> list[str]:
        return [leg.id for leg in self.legs]

    def shape(self, fused: list[ScoredChunk]) -> list[ScoredChunk]:
        """The first leg's shaping (stable demotions, identical settings for
        every leg); a leg without one is a no-op."""
        shaper = getattr(self.legs[0].retriever, "shape", None)
        return shaper(fused) if callable(shaper) else fused

    def _min_confidence(self) -> int:
        if self.graph_min_confidence is None:
            from ragstack.config import settings

            return settings.graph_min_confidence
        return self.graph_min_confidence

    def _entity_max(self) -> int:
        if self.graph_query_entity_max is None:
            from ragstack.config import settings

            return settings.graph_query_entity_max
        return self.graph_query_entity_max

    def _ngram_max(self) -> int:
        if self.graph_query_ngram_max is None:
            from ragstack.config import settings

            return settings.graph_query_ngram_max
        return self.graph_query_ngram_max

    @staticmethod
    def _stamp(scored: list[ScoredChunk], cid: str) -> list[ScoredChunk]:
        """Stamp ``cid`` on a per-leg copy of each chunk (see ``STAMP_KEY``):
        the store's own object and its metadata dict are left untouched."""
        return [
            ScoredChunk(
                chunk=s.chunk.model_copy(
                    update={"metadata": {**s.chunk.metadata, STAMP_KEY: cid}}
                ),
                score=s.score,
                retrieval_method=s.retrieval_method,
                collection=cid,
            )
            for s in scored
        ]

    async def _leg(
        self,
        leg: CollectionLeg,
        query: str,
        top_k: int,
        filters: dict[str, Any] | None,
        use_graph: bool,
        tenant_id: str | None,
        mode: str,
    ) -> list[ScoredChunk]:
        ranked = await leg.retriever.retrieve(
            query,
            top_k=top_k,
            filters=leg.filters(filters, tenant_id),
            use_graph=use_graph,
            tenant_id=tenant_id,
            mode=mode,
        )
        return self._stamp(ranked, leg.id)

    async def _graph(
        self, query: str, top_k: int, tenant_id: str | None
    ) -> list[ScoredChunk]:
        """The graph leg over every leg's physical collection at once (one
        entity match with ``collection IN [...]``, one neighbourhood per matched
        entity), its pseudo-chunks stamped with the owning leg's registry id."""
        if self.graph_store is None:
            return []
        by_physical: dict[str, str] = {}
        for leg in self.legs:
            if leg.physical:
                by_physical.setdefault(leg.physical, leg.id)
        if not by_physical:
            return []
        # Timed as `graph` like the single-collection leg, and untagged: this is
        # ONE Neo4j query spanning every member's physical collection, so no
        # single collection owns it. On the multi path the legs run with
        # use_graph=False, so without this the request's only graph round trip
        # would be invisible and would inflate `self_ms` (#427 W3, ADR-0006).
        with stage("graph"):
            chunks = await graph_context(
                self.graph_store,
                query,
                top_k,
                tenant_id,
                list(by_physical),
                depth=self.graph_context_depth,
                score=self.graph_context_score,
                min_confidence=self._min_confidence(),
                entity_max=self._entity_max(),
                ngram_max=self._ngram_max(),
            )
        out: list[ScoredChunk] = []
        for c in chunks:
            cid = by_physical.get(str(c.chunk.metadata.get("collection", "")))
            if cid is None:
                continue  # re-check already excluded it; belt and braces
            out.extend(self._stamp([c], cid))
        return out

    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
        use_graph: bool = True,
        tenant_id: str | None = None,
        mode: str = "hybrid",
    ) -> list[ScoredChunk]:
        """Run every leg concurrently at depth ``top_k`` and return the
        RRF-fused union (see the class docstring — NOT cut to ``top_k``; the
        router cuts it to the rerank pool, or to its own ``top_k``). The graph
        pseudo-chunk budget ``top_k`` is one budget for the whole call, shared
        across the members."""
        if len(self.legs) == 1:
            return await self._leg(
                self.legs[0], query, top_k, filters, use_graph, tenant_id, mode
            )
        tasks = [
            self._leg(leg, query, top_k, filters, False, tenant_id, mode)
            for leg in self.legs
        ]
        if use_graph and self.graph_store is not None:
            tasks.append(self._graph(query, top_k, tenant_id))
        ranked_lists = await asyncio.gather(*tasks)
        return self.rrf.fuse([r for r in ranked_lists if r])


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
