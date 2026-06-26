"""HybridRetriever fuses vector + BM25 and scopes both legs by tenant."""
import pytest

from ragstack.models import Chunk, ScoredChunk
from ragstack.retrieval.retriever import HybridRetriever


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


@pytest.mark.asyncio
async def test_hybrid_respects_top_k():
    chunks = [Chunk(id=str(i), doc_id="d", content=f"c{i}") for i in range(10)]
    retriever = HybridRetriever(_FakeVectorStore(chunks), _FakeTextIndex(chunks), _FakeEmbedder())
    fused = await retriever.retrieve("q", top_k=3, use_graph=False)
    assert len(fused) == 3
