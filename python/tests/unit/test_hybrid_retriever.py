"""HybridRetriever fuses vector + BM25 and scopes both legs by tenant."""
import pytest

from ragstack.models import Chunk, ScoredChunk, Triple
from ragstack.retrieval.retriever import HybridRetriever
from ragstack.stores import InMemoryGraphStore
from ragstack.tenancy import tenant_of


class _FakeVectorStore:
    def __init__(self, chunks: list[Chunk]) -> None:
        self._chunks = chunks
        self.filters = "unset"

    async def search(self, query_vector, top_k=5, filters=None):
        self.filters = filters
        return [ScoredChunk(chunk=c, score=1.0, retrieval_method="vector") for c in self._chunks]


class _FakeTextIndex:
    def __init__(self, chunks: list[Chunk]) -> None:
        self._chunks = chunks
        self.filters = "unset"

    async def search(self, query, top_k=5, filters=None):
        self.filters = filters
        return [ScoredChunk(chunk=c, score=2.0, retrieval_method="bm25") for c in self._chunks]


class _FakeEmbedder:
    async def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


@pytest.mark.asyncio
async def test_hybrid_fuses_both_legs_and_passes_tenant_filter():
    vec_only = Chunk(id="v", doc_id="d", content="from vector")
    text_only = Chunk(id="t", doc_id="d", content="from bm25")
    vec = _FakeVectorStore([vec_only])
    txt = _FakeTextIndex([text_only])
    retriever = HybridRetriever(vec, txt, _FakeEmbedder())

    filters = {"tenant_id": ["alice", "public"]}
    fused = await retriever.retrieve("q", top_k=5, filters=filters, use_graph=False)

    # Results come from both retrieval legs, fused (RRF labels them "hybrid").
    assert {r.chunk.id for r in fused} == {"v", "t"}
    assert all(r.retrieval_method == "hybrid" for r in fused)
    # The tenant scope reached BOTH stores — isolation holds in hybrid retrieval.
    assert vec.filters == filters
    assert txt.filters == filters


class _SpyGraphStore:
    """Records the tenant_id passed to query_neighborhood and returns the given
    triples (defaulting to one alice-owned triple), ignoring the scope — so the
    retriever's own re-check is what the assertions exercise."""

    def __init__(self, triples: list[Triple] | None = None) -> None:
        self.tenant_id = "unset"
        self._triples = triples if triples is not None else [
            Triple(subject="Alice", predicate="knows", object="Bob",
                   doc_id="d", tenant_id="alice"),
        ]

    async def query_neighborhood(self, entity, depth=1, tenant_id=None):
        self.tenant_id = tenant_id
        return list(self._triples)


@pytest.mark.asyncio
async def test_graph_leg_is_tenant_scoped_and_fuses():
    vec = _FakeVectorStore([Chunk(id="v", doc_id="d", content="from vector")])
    txt = _FakeTextIndex([Chunk(id="t", doc_id="d", content="from bm25")])
    graph = _SpyGraphStore()
    retriever = HybridRetriever(vec, txt, _FakeEmbedder(), graph_store=graph)

    fused = await retriever.retrieve("Alice", top_k=5, use_graph=True, tenant_id="alice")

    # The caller's tenant reached query_neighborhood — no cross-tenant graph read.
    assert graph.tenant_id == "alice"
    # The graph triple fused in alongside the vector + BM25 legs.
    methods = {r.chunk.id for r in fused}
    assert "graph-Alice-knows-Bob" in methods
    assert {"v", "t"} <= methods


@pytest.mark.asyncio
async def test_graph_pseudo_chunks_carry_the_tenant_stamp():
    # Graph pseudo-chunks must carry tenant_id in metadata like real chunks, so a
    # post-retrieval re-check can evaluate them per-chunk instead of wholesale.
    graph = _SpyGraphStore([
        Triple(subject="Alice", predicate="knows", object="Bob",
               doc_id="d", tenant_id="alice"),
        Triple(subject="Alice", predicate="reads", object="Docs",
               doc_id="p", tenant_id="public"),
    ])
    retriever = HybridRetriever(
        _FakeVectorStore([]), _FakeTextIndex([]), _FakeEmbedder(), graph_store=graph
    )

    fused = await retriever.retrieve("Alice", top_k=5, use_graph=True, tenant_id="alice")

    stamps = {r.chunk.id: tenant_of(r.chunk) for r in fused}
    assert stamps == {"graph-Alice-knows-Bob": "alice", "graph-Alice-reads-Docs": "public"}


@pytest.mark.asyncio
async def test_graph_leg_drops_unreadable_triples_from_a_leaky_store():
    # Defence in depth: even if a store hands back another tenant's triple (or an
    # unstamped one), the retriever must not fuse it into the caller's context.
    graph = _SpyGraphStore([
        Triple(subject="Alice", predicate="knows", object="Bob",
               doc_id="d", tenant_id="alice"),
        Triple(subject="Alice", predicate="knows", object="Eve",
               doc_id="x", tenant_id="bob"),
        Triple(subject="Alice", predicate="knows", object="Mallory", doc_id="u"),
    ])
    retriever = HybridRetriever(
        _FakeVectorStore([]), _FakeTextIndex([]), _FakeEmbedder(), graph_store=graph
    )

    fused = await retriever.retrieve("Alice", top_k=5, use_graph=True, tenant_id="alice")

    assert {r.chunk.id for r in fused} == {"graph-Alice-knows-Bob"}


@pytest.mark.asyncio
async def test_graph_leg_unscoped_reads_everything_but_stamps_truthfully():
    # tenant_id=None is the deliberate unscoped (dev/tests/library) path — it reads
    # every triple, but each pseudo-chunk still carries its real owning tenant.
    graph = _SpyGraphStore([
        Triple(subject="Alice", predicate="knows", object="Bob",
               doc_id="d", tenant_id="alice"),
        Triple(subject="Alice", predicate="knows", object="Eve",
               doc_id="x", tenant_id="bob"),
    ])
    retriever = HybridRetriever(
        _FakeVectorStore([]), _FakeTextIndex([]), _FakeEmbedder(), graph_store=graph
    )

    fused = await retriever.retrieve("Alice", top_k=5, use_graph=True)

    assert {tenant_of(r.chunk) for r in fused} == {"alice", "bob"}


@pytest.mark.asyncio
async def test_graph_leg_isolates_tenants_over_the_real_in_memory_store():
    graph = InMemoryGraphStore()
    await graph.add_triples([
        Triple(subject="Project", predicate="owned_by", object="ALICE",
               doc_id="da", tenant_id="alice"),
        Triple(subject="Project", predicate="owned_by", object="BOB",
               doc_id="db", tenant_id="bob"),
        Triple(subject="Project", predicate="listed_in", object="PUBLIC",
               doc_id="dp", tenant_id="public"),
    ])
    retriever = HybridRetriever(
        _FakeVectorStore([]), _FakeTextIndex([]), _FakeEmbedder(), graph_store=graph
    )

    alice = await retriever.retrieve("Project", top_k=10, use_graph=True, tenant_id="alice")
    bob = await retriever.retrieve("Project", top_k=10, use_graph=True, tenant_id="bob")

    # Own tenant + public, never the other tenant's triples.
    assert {r.chunk.content for r in alice} == {
        "Project owned_by ALICE", "Project listed_in PUBLIC",
    }
    assert {r.chunk.content for r in bob} == {
        "Project owned_by BOB", "Project listed_in PUBLIC",
    }
    assert all(tenant_of(r.chunk) in ("alice", "public") for r in alice)


@pytest.mark.asyncio
async def test_graph_leg_disabled_when_no_store():
    # No graph store wired → graph leg is skipped, only vector + BM25 fuse.
    vec = _FakeVectorStore([Chunk(id="v", doc_id="d", content="from vector")])
    txt = _FakeTextIndex([Chunk(id="t", doc_id="d", content="from bm25")])
    retriever = HybridRetriever(vec, txt, _FakeEmbedder())
    fused = await retriever.retrieve("q", top_k=5, use_graph=True, tenant_id="alice")
    assert {r.chunk.id for r in fused} == {"v", "t"}


@pytest.mark.asyncio
async def test_hybrid_respects_top_k():
    chunks = [Chunk(id=str(i), doc_id="d", content=f"c{i}") for i in range(10)]
    retriever = HybridRetriever(_FakeVectorStore(chunks), _FakeTextIndex(chunks), _FakeEmbedder())
    fused = await retriever.retrieve("q", top_k=3, use_graph=False)
    assert len(fused) == 3
