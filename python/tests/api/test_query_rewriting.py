"""Query rewriting: expand → retrieve each variant → RRF fuse."""
import pytest

from ragstack.api.main import app
from ragstack.api.routers.query import _expand_query
from ragstack.models import Chunk, ScoredChunk


class _FakeRewriter:
    def __init__(self, alternatives: list[str]) -> None:
        self.alternatives = alternatives

    async def rewrite(self, query: str) -> list[str]:
        return [query, *self.alternatives]


class _BoomRewriter:
    async def rewrite(self, query: str) -> list[str]:
        raise RuntimeError("rewriter exploded")


class _RecordingRetriever:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def retrieve(self, query, top_k=5, filters=None, use_graph=True):
        self.queries.append(query)
        return [ScoredChunk(chunk=Chunk(id=f"{query}#c", doc_id="d", content=query), score=1.0)]


# --- _expand_query ----------------------------------------------------------

@pytest.mark.asyncio
async def test_expand_passthrough_is_single_variant():
    from ragstack.rewriting.rewriters import PassthroughRewriter

    out = await _expand_query("q", ["passthrough"], {"passthrough": PassthroughRewriter()})
    assert out == ["q"]


@pytest.mark.asyncio
async def test_expand_multiquery_dedups_original_first():
    out = await _expand_query("q", ["multiquery"], {"multiquery": _FakeRewriter(["a", "q", "b"])})
    assert out == ["q", "a", "b"]  # original first, "q" not duplicated


@pytest.mark.asyncio
async def test_expand_skips_unknown_and_failing_strategies():
    out = await _expand_query(
        "q", ["nonexistent", "boom"], {"boom": _BoomRewriter()}
    )
    assert out == ["q"]  # degrades to the plain query


# --- /v1/query end-to-end ---------------------------------------------------

@pytest.mark.asyncio
async def test_query_rewriting_retrieves_each_variant_and_fuses(client):
    retriever = _RecordingRetriever()
    # app is a shared module-level instance; restore the doubles the client
    # fixture set so this test can't leak state into later (order-dependent) tests.
    prev_retriever, prev_rewriters = app.state.retriever, app.state.rewriters
    app.state.retriever = retriever
    app.state.rewriters = {"multiquery": _FakeRewriter(["paraphrase A", "paraphrase B"])}

    try:
        resp = await client.post(
            "/v1/query", json={"query": "orig", "rewrite_strategies": ["multiquery"]}
        )
        assert resp.status_code == 200
        body = resp.json()
        # The variants are reported and each was retrieved...
        assert body["rewritten_queries"] == ["orig", "paraphrase A", "paraphrase B"]
        # Retrievals run concurrently (asyncio.gather), so compare as a set.
        assert set(retriever.queries) == {"orig", "paraphrase A", "paraphrase B"}
        # ...and their results are fused into the sources.
        assert {s["content"] for s in body["sources"]} == {
            "orig",
            "paraphrase A",
            "paraphrase B",
        }
    finally:
        app.state.retriever, app.state.rewriters = prev_retriever, prev_rewriters
