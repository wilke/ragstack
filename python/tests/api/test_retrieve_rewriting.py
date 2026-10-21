"""``rewrite_strategies`` on ``/v1/retrieve`` (#750).

/v1/retrieve runs what /v1/query runs before generation: expand the query,
retrieve each variant, RRF-fuse, rerank ONCE against the original query, cut to
``top_k``. It never generates and never echoes the variants. Unknown,
unavailable and failing strategies are skipped exactly as on /v1/query — the
request degrades to the plain query, never a 422.
"""
import logging

import pytest

from ragstack.api.main import app
from ragstack.models import Chunk, ScoredChunk
from tests.api.test_query_summary_line import _one_summary, _stage_names

pytestmark = pytest.mark.asyncio


class _FakeRewriter:
    """Returns the query plus fixed alternatives, and records each call."""

    def __init__(self, alternatives: list[str]) -> None:
        self.alternatives = alternatives
        self.calls: list[str] = []

    async def rewrite(self, query: str) -> list[str]:
        self.calls.append(query)
        return [query, *self.alternatives]


class _BoomRewriter:
    async def rewrite(self, query: str) -> list[str]:
        raise RuntimeError("rewriter exploded")


class _RecordingRetriever:
    """One chunk per query, whose content is the query text."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def retrieve(self, query, top_k=5, filters=None, use_graph=True,
                       mode="hybrid", tenant_id=None):
        self.queries.append(query)
        return [ScoredChunk(chunk=Chunk(id=f"{query}#c", doc_id="d", content=query), score=1.0)]


class _RecordingReranker:
    """Keeps the input order and records every (query, candidate contents) call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    async def score(self, query, candidates, top_k=None):
        self.calls.append((query, [c.content for c in candidates]))
        return [
            ScoredChunk(chunk=c, score=float(len(candidates) - i), retrieval_method="reranked")
            for i, c in enumerate(candidates)
        ]


@pytest.fixture
def _restore_state():
    prev = (app.state.retriever, app.state.rewriters, app.state.reranker)
    try:
        yield
    finally:
        app.state.retriever, app.state.rewriters, app.state.reranker = prev


async def test_retrieve_default_equals_explicit_passthrough(client, _restore_state):
    """The default leaves the response byte-identical; ``passthrough`` plus an
    unknown name is the same response too. The response never grows a
    ``rewritten_queries`` key."""
    retriever = _RecordingRetriever()
    app.state.retriever = retriever

    bodies = [
        {"query": "orig"},
        {"query": "orig", "rewrite_strategies": ["passthrough"]},
        {"query": "orig", "rewrite_strategies": ["passthrough", "nonexistent"]},
    ]
    texts = []
    for body in bodies:
        resp = await client.post("/v1/retrieve", json=body)
        assert resp.status_code == 200, resp.text
        assert "rewritten_queries" not in resp.json()
        texts.append(resp.text)
    assert texts[0] == texts[1] == texts[2]
    assert resp.json()["sources"], "the fixture retriever returned nothing"
    assert retriever.queries == ["orig"] * 3  # one retrieval per request


async def test_retrieve_multiquery_retrieves_each_variant_and_fuses(client, _restore_state):
    retriever = _RecordingRetriever()
    app.state.retriever = retriever
    app.state.rewriters = {"multiquery": _FakeRewriter(["paraphrase A", "paraphrase B"])}

    resp = await client.post(
        "/v1/retrieve", json={"query": "orig", "rewrite_strategies": ["multiquery"]}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"sources"}  # no rewritten_queries on RetrieveResponse
    # Retrievals run concurrently (asyncio.gather), so compare as sets.
    assert sorted(retriever.queries) == ["orig", "paraphrase A", "paraphrase B"]
    assert {s["content"] for s in body["sources"]} == {"orig", "paraphrase A", "paraphrase B"}


async def test_retrieve_hyde_reranks_once_against_the_original_query(client, _restore_state):
    retriever = _RecordingRetriever()
    reranker = _RecordingReranker()
    app.state.retriever = retriever
    app.state.reranker = reranker
    app.state.rewriters = {"hyde": _FakeRewriter(["a hypothetical answer passage"])}

    resp = await client.post(
        "/v1/retrieve", json={"query": "orig", "rewrite_strategies": ["hyde"], "top_k": 5}
    )
    assert resp.status_code == 200, resp.text
    # Exactly one rerank, scored against the ORIGINAL query (never the HyDE
    # passage), over the fused union of both variants' candidates.
    assert len(reranker.calls) == 1
    query, candidates = reranker.calls[0]
    assert query == "orig"
    assert sorted(candidates) == ["a hypothetical answer passage", "orig"]
    sources = resp.json()["sources"]
    # The reranker's order (it keeps its input order) is the response order.
    assert [s["content"] for s in sources] == candidates


@pytest.mark.parametrize(
    "strategies",
    [["nonexistent"], ["hyde"], ["multiquery", "hyde"], []],
    ids=["unknown", "unavailable-hyde", "unavailable-both", "empty"],
)
async def test_retrieve_unknown_and_unavailable_strategies_degrade_to_plain_query(
    client, _restore_state, strategies
):
    """The conftest wires only ``passthrough`` (no LLM), so ``hyde`` and
    ``multiquery`` are unavailable here: skipped, 200, same response as the
    plain query — parity with /v1/query, never a 422."""
    retriever = _RecordingRetriever()
    app.state.retriever = retriever

    plain = await client.post("/v1/retrieve", json={"query": "orig"})
    resp = await client.post(
        "/v1/retrieve", json={"query": "orig", "rewrite_strategies": strategies}
    )
    assert resp.status_code == 200, resp.text
    assert resp.text == plain.text
    assert retriever.queries == ["orig", "orig"]


async def test_retrieve_rewriter_failure_is_skipped(client, _restore_state):
    retriever = _RecordingRetriever()
    app.state.retriever = retriever
    app.state.rewriters = {"boom": _BoomRewriter()}

    resp = await client.post("/v1/retrieve", json={"query": "orig", "rewrite_strategies": ["boom"]})
    assert resp.status_code == 200, resp.text
    assert retriever.queries == ["orig"]
    assert [s["content"] for s in resp.json()["sources"]] == ["orig"]


async def test_retrieve_rewrite_runs_after_authz(client, _restore_state):
    """A refused request costs no rewriter (i.e. no LLM) call: an unknown
    collection is a 404 before any expansion runs."""
    rewriter = _FakeRewriter(["paraphrase"])
    app.state.rewriters = {"multiquery": rewriter}

    resp = await client.post(
        "/v1/retrieve",
        json={"query": "orig", "rewrite_strategies": ["multiquery"], "collection": "nope"},
    )
    assert resp.status_code == 404, resp.text
    assert rewriter.calls == []


@pytest.mark.parametrize("endpoint", ["/v1/retrieve", "/v1/query"])
@pytest.mark.parametrize("bad", ["hyde", [1], {"hyde": True}], ids=["string", "int-item", "object"])
async def test_rewrite_strategies_type_violation_is_422_on_both_endpoints(client, endpoint, bad):
    resp = await client.post(endpoint, json={"query": "orig", "rewrite_strategies": bad})
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("endpoint", ["/v1/retrieve", "/v1/query"])
async def test_more_than_eight_strategies_is_422_on_both_endpoints(client, endpoint):
    """The list is capped at 8 (each LLM-backed entry is an LLM call)."""
    ok = await client.post(endpoint, json={"query": "orig", "rewrite_strategies": ["x"] * 8})
    assert ok.status_code == 200, ok.text
    resp = await client.post(endpoint, json={"query": "orig", "rewrite_strategies": ["x"] * 9})
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("endpoint", ["/v1/retrieve", "/v1/query"])
async def test_repeated_strategy_runs_once(client, _restore_state, endpoint):
    """``["hyde"] * 3`` is one rewriter (one LLM) call — de-duplicated silently,
    not rejected."""
    retriever = _RecordingRetriever()
    rewriter = _FakeRewriter(["a hypothetical answer passage"])
    app.state.retriever = retriever
    app.state.rewriters = {"hyde": rewriter}

    resp = await client.post(endpoint, json={"query": "orig", "rewrite_strategies": ["hyde"] * 3})
    assert resp.status_code == 200, resp.text
    assert rewriter.calls == ["orig"]
    assert sorted(retriever.queries) == ["a hypothetical answer passage", "orig"]


async def test_served_openapi_declares_rewrite_strategies_identically(client):
    spec = app.openapi()
    props = [
        spec["components"]["schemas"][name]["properties"]["rewrite_strategies"]
        for name in ("QueryRequest", "RetrieveRequest")
    ]
    assert props[0] == props[1]
    for prop in props:
        assert prop["type"] == "array"
        assert prop["items"] == {"type": "string"}
        assert prop["default"] == ["passthrough"]
        assert prop["maxItems"] == 8
    assert "rewrite_strategies" not in spec["components"]["schemas"]["RetrieveResponse"]["properties"]


async def test_retrieve_emits_rewrite_ms(client, caplog, _restore_state):
    """The query-expansion timing on /v1/retrieve is ``rewrite_ms`` — the same
    stage /v1/query records. (``expand_ms`` is CONTEXT expansion, which
    retrieve already timed before #750.)"""
    app.state.retriever = _RecordingRetriever()
    app.state.rewriters = {"multiquery": _FakeRewriter(["paraphrase"])}
    caplog.set_level(logging.INFO)

    resp = await client.post(
        "/v1/retrieve", json={"query": "orig", "rewrite_strategies": ["multiquery"]}
    )
    assert resp.status_code == 200, resp.text
    emitted = _stage_names(_one_summary(caplog))
    assert {"rewrite", "expand", "authz", "fuse"} <= emitted
