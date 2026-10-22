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

    async def retrieve(self, query, top_k=5, filters=None, use_graph=True,
                       mode="hybrid", tenant_id=None):
        self.depths.append(top_k)
        return [ScoredChunk(chunk=c, score=1.0 / (i + 1)) for i, c in enumerate(self.chunks)]


class _ReverseReranker:
    async def score(self, query, candidates, top_k=None):
        return [
            ScoredChunk(chunk=c, score=float(i), retrieval_method="reranked")
            for i, c in enumerate(reversed(candidates))
        ]


class _BoomReranker:
    async def score(self, query, candidates, top_k=None):
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


# --- Per-request rerank control (issue #27) -------------------------------


@pytest.mark.asyncio
async def test_per_request_rerank_opt_out_skips_reranker(client, _restore_state):
    """rerank=false skips reranking even with a reranker wired: fused order is
    preserved and the retriever is asked only for the shallow top_k pool."""
    retriever = _StubRetriever(5)
    app.state.retriever = retriever
    app.state.reranker = _ReverseReranker()

    resp = await client.post("/v1/retrieve", json={"query": "q", "top_k": 2, "rerank": False})
    assert resp.status_code == 200
    ids = [s["chunk_id"] for s in resp.json()["sources"]]
    assert ids == ["c0", "c1"]  # fused order, NOT reversed by the reranker
    assert retriever.depths == [2]  # shallow pool, no deep fetch


@pytest.mark.asyncio
async def test_per_request_rerank_candidates_overrides_pool_depth(client, _restore_state):
    """rerank_candidates overrides the pool depth fed to the reranker."""
    retriever = _StubRetriever(5)
    app.state.retriever = retriever
    app.state.reranker = _ReverseReranker()

    resp = await client.post(
        "/v1/retrieve",
        json={"query": "q", "top_k": 2, "rerank_candidates": 4},
    )
    assert resp.status_code == 200
    ids = [s["chunk_id"] for s in resp.json()["sources"]]
    # rerank still applies (reversed [c0..c4] -> [c4..c0]), truncated to top_k=2
    assert ids == ["c4", "c3"]
    # pool depth is the override (max(top_k=2, 4) == 4), not settings.rerank_candidates
    assert retriever.depths == [4]


@pytest.mark.asyncio
async def test_per_request_defaults_preserve_global_behaviour(client, _restore_state):
    """Omitting rerank/rerank_candidates (null) keeps the server-wide default:
    rerank applies and the deep pool is settings.rerank_candidates."""
    retriever = _StubRetriever(5)
    app.state.retriever = retriever
    app.state.reranker = _ReverseReranker()

    resp = await client.post("/v1/retrieve", json={"query": "q", "top_k": 2})
    assert resp.status_code == 200
    ids = [s["chunk_id"] for s in resp.json()["sources"]]
    assert ids == ["c4", "c3"]
    assert retriever.depths == [settings.rerank_candidates]


@pytest.mark.asyncio
async def test_per_request_rerank_true_noop_without_reranker(client, _restore_state):
    """rerank=true is graceful when no reranker is wired: fused order, shallow
    pool — can't conjure a reranker, never a 500."""
    retriever = _StubRetriever(5)
    app.state.retriever = retriever
    app.state.reranker = None

    resp = await client.post("/v1/retrieve", json={"query": "q", "top_k": 2, "rerank": True})
    assert resp.status_code == 200
    ids = [s["chunk_id"] for s in resp.json()["sources"]]
    assert ids == ["c0", "c1"]
    assert retriever.depths == [2]


@pytest.mark.asyncio
async def test_query_endpoint_honours_per_request_rerank_opt_out(client, _restore_state):
    """The /v1/query endpoint plumbs the per-request controls through too."""
    retriever = _StubRetriever(4)
    app.state.retriever = retriever
    app.state.reranker = _ReverseReranker()

    resp = await client.post("/v1/query", json={"query": "q", "top_k": 2, "rerank": False})
    assert resp.status_code == 200
    ids = [s["chunk_id"] for s in resp.json()["sources"]]
    assert ids == ["c0", "c1"]  # fused order, rerank skipped
    assert retriever.depths == [2]


# --- #748 part 2: the fallback is visible (issue #751 PR-3) ---------------


@pytest.mark.asyncio
async def test_rerank_failure_is_reported_as_degraded(client, _restore_state, caplog):
    """The fused-order fallback is no longer silent: the response carries
    ``degraded: [{component: "rerank", reason}]``, the process counter moves,
    the summary line says ``degraded=rerank``, and the reranker's verdict in
    the shared dependency cache is ``down`` — one fact, four projections."""
    import logging

    from ragstack.observability.context import RequestContextFilter
    from ragstack.observability.dependencies import dependency_health
    from ragstack.observability.fallbacks import fallback_total

    caplog.set_level(logging.INFO)
    caplog.handler.addFilter(RequestContextFilter())
    app.state.retriever = _StubRetriever(5)
    app.state.reranker = _BoomReranker()

    resp = await client.post("/v1/retrieve", json={"query": "q", "top_k": 3})
    assert resp.status_code == 200
    body = resp.json()
    assert body["degraded"] == [{"component": "rerank", "reason": "RuntimeError: reranker down"}]
    assert [s["chunk_id"] for s in body["sources"]] == ["c0", "c1", "c2"]
    assert fallback_total()["rerank"] == 1

    line = next(
        r for r in caplog.records
        if r.name == "ragstack.observability.middleware" and r.getMessage() == "request complete"
    )
    assert line.degraded == "rerank"

    verdict = dependency_health.get("reranker")
    assert verdict is not None and verdict.state == "down" and verdict.source == "request"
    assert verdict.detail == "RuntimeError: reranker down"

    # /v1/query carries it too.
    body = (await client.post("/v1/query", json={"query": "q", "top_k": 2})).json()
    assert body["degraded"][0]["component"] == "rerank"
    assert fallback_total()["rerank"] == 2


@pytest.mark.asyncio
async def test_healthy_rerank_omits_degraded_and_records_ok(client, _restore_state):
    """Byte-identity for the undegraded response: no ``degraded`` key at all
    (never an empty list). And a real success writes ``ok`` into the shared
    verdict, so deep health reports what traffic sees without a probe."""
    from ragstack.observability.dependencies import dependency_health

    app.state.retriever = _StubRetriever(5)
    app.state.reranker = _ReverseReranker()
    for path in ("/v1/retrieve", "/v1/query"):
        body = (await client.post(path, json={"query": "q", "top_k": 2})).json()
        assert "degraded" not in body, body
    verdict = dependency_health.get("reranker")
    assert verdict is not None and verdict.state == "ok" and verdict.source == "request"

    app.state.reranker = None  # no reranker is not a fallback either
    body = (await client.post("/v1/retrieve", json={"query": "q", "top_k": 2})).json()
    assert "degraded" not in body


class _InvalidReranker:
    async def score(self, query, candidates, top_k=None):
        return [
            ScoredChunk(chunk=c, score=float(i), retrieval_method="reranked")
            for i, c in enumerate(candidates)
        ]


@pytest.mark.asyncio
async def test_invalid_rerank_response_is_degraded_too(client, _restore_state, monkeypatch):
    """A contract-breaking sidecar answer (KeyError/ValueError) is the other
    fallback branch: still ``degraded: rerank`` for the caller, verdict
    ``degraded`` (it answered, badly) rather than ``down``."""
    from ragstack.observability.dependencies import dependency_health

    class _Malformed:
        async def score(self, query, candidates, top_k=None):
            raise ValueError("rerank returned duplicate indices")

    app.state.retriever = _StubRetriever(4)
    app.state.reranker = _Malformed()
    body = (await client.post("/v1/retrieve", json={"query": "q", "top_k": 2})).json()
    assert body["degraded"] == [
        {"component": "rerank", "reason": "ValueError: rerank returned duplicate indices"}
    ]
    assert dependency_health.get("reranker").state == "degraded"


@pytest.mark.asyncio
async def test_rerank_warning_is_rate_limited(client, _restore_state, caplog):
    """#748 wrote the same warning 231 times in 90 minutes. Now: the first
    failure logs (with its traceback), the repeats inside 60 s are counted and
    suppressed, and the counter still moves for every one of them."""
    import logging

    from ragstack.observability.fallbacks import fallback_total, rate_limited

    caplog.set_level(logging.WARNING, logger="ragstack.api.routers.query")
    app.state.retriever = _StubRetriever(5)
    app.state.reranker = _BoomReranker()
    for _ in range(3):
        assert (await client.post("/v1/retrieve", json={"query": "q", "top_k": 2})).status_code == 200

    lines = [
        r for r in caplog.records
        if r.name == "ragstack.api.routers.query" and "rerank unavailable" in r.getMessage()
    ]
    assert len(lines) == 1, [r.getMessage() for r in lines]
    assert lines[0].exc_info is not None
    assert rate_limited.suppressed("rerank.unavailable") == 2
    assert fallback_total()["rerank"] == 3


@pytest.mark.asyncio
async def test_per_request_override_reranker_does_not_write_the_shared_verdict(
    client, _restore_state, monkeypatch
):
    """A per-request ``reranker`` override that fails is still ``degraded``
    for THAT caller, but it is not evidence about the server's own reranker,
    so the shared verdict (what deep health and fleet status read) is untouched."""
    from ragstack.api.routers import query as query_router
    from ragstack.observability.dependencies import dependency_health

    app.state.retriever = _StubRetriever(5)
    app.state.reranker = _ReverseReranker()
    monkeypatch.setattr(
        query_router, "build_reranker_for", lambda models, http, model_id: _BoomReranker()
    )
    body = (
        await client.post("/v1/retrieve", json={"query": "q", "top_k": 2, "reranker": "other"})
    ).json()
    assert body["degraded"][0]["component"] == "rerank"
    assert dependency_health.get("reranker") is None
