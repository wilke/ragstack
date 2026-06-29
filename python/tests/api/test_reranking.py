"""Cross-encoder rerank stage in /v1/retrieve and /v1/query: it reorders the
fused pool, fetches a deeper pool to rerank, and degrades to the fused order on
failure (never a 500)."""
import pytest

from ragstack.api.main import app
from ragstack.config import settings
from ragstack.models import Chunk, ScoredChunk


class _StubRetriever:
    """Returns a fixed candidate list and records the depth it was asked for."""

    def __init__(self, n: int) -> None:
        self.chunks = [Chunk(id=f"c{i}", doc_id="d", content=f"text {i}") for i in range(n)]
        self.depths: list[int] = []

    async def retrieve(self, query, top_k=5, filters=None, use_graph=True):
        self.depths.append(top_k)
        return [ScoredChunk(chunk=c, score=1.0 / (i + 1)) for i, c in enumerate(self.chunks)]


class _ReverseReranker:
    async def score(self, query, candidates):
        return [
            ScoredChunk(chunk=c, score=float(i), retrieval_method="reranked")
            for i, c in enumerate(reversed(candidates))
        ]


class _BoomReranker:
    async def score(self, query, candidates):
        raise RuntimeError("reranker down")


@pytest.fixture
def _restore_state():
    prev = (app.state.retriever, app.state.reranker)
    try:
        yield
    finally:
        app.state.retriever, app.state.reranker = prev


@pytest.mark.asyncio
async def test_retrieve_applies_rerank_and_fetches_deeper_pool(client, _restore_state):
    retriever = _StubRetriever(5)
    app.state.retriever = retriever
    app.state.reranker = _ReverseReranker()

    resp = await client.post("/v1/retrieve", json={"query": "q", "top_k": 2})
    assert resp.status_code == 200
    ids = [s["chunk_id"] for s in resp.json()["sources"]]
    # reranker reversed [c0..c4] -> [c4..c0], then truncated to top_k=2
    assert ids == ["c4", "c3"]
    # with a reranker active, the retriever was asked for the deeper pool
    assert retriever.depths == [settings.rerank_candidates]


@pytest.mark.asyncio
async def test_query_applies_rerank(client, _restore_state):
    app.state.retriever = _StubRetriever(4)
    app.state.reranker = _ReverseReranker()

    resp = await client.post("/v1/query", json={"query": "q", "top_k": 2})
    assert resp.status_code == 200
    ids = [s["chunk_id"] for s in resp.json()["sources"]]
    assert ids == ["c3", "c2"]


@pytest.mark.asyncio
async def test_rerank_failure_falls_back_to_fused_order(client, _restore_state):
    app.state.retriever = _StubRetriever(5)
    app.state.reranker = _BoomReranker()

    resp = await client.post("/v1/retrieve", json={"query": "q", "top_k": 3})
    assert resp.status_code == 200  # graceful degradation, not a 500
    ids = [s["chunk_id"] for s in resp.json()["sources"]]
    assert ids == ["c0", "c1", "c2"]  # original fused order preserved


@pytest.mark.asyncio
async def test_no_reranker_keeps_fused_order_and_shallow_pool(client, _restore_state):
    retriever = _StubRetriever(5)
    app.state.retriever = retriever
    app.state.reranker = None

    resp = await client.post("/v1/retrieve", json={"query": "q", "top_k": 2})
    assert resp.status_code == 200
    ids = [s["chunk_id"] for s in resp.json()["sources"]]
    assert ids == ["c0", "c1"]
    assert retriever.depths == [2]  # no deeper pool when rerank is off
