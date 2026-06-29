"""Unit tests for the SidecarReranker HTTP client — it maps the sidecar's
(scores, indices) response back onto the candidate chunks in ranked order."""
from __future__ import annotations

import json

import httpx
import pytest

from ragstack.models import Chunk
from ragstack.scoring.scorers import SidecarReranker


def _chunks(n: int) -> list[Chunk]:
    return [Chunk(id=f"c{i}", doc_id="d", content=f"text {i}") for i in range(n)]


@pytest.mark.asyncio
async def test_reranker_maps_sidecar_ranking_onto_chunks():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured.update(body)
        n = len(body["documents"])
        # Sidecar contract: ranked best-first. Here: reverse order, descending scores.
        indices = list(reversed(range(n)))
        scores = [float(n - i) for i in range(n)]
        return httpx.Response(200, json={"scores": scores, "indices": indices})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        rr = SidecarReranker("http://crossencoder:50052/", http)
        out = await rr.score("the query", _chunks(3))

    # full pool requested (so the cross-encoder rescores everything, not top-5)
    assert captured["top_k"] == 3
    assert captured["query"] == "the query"
    assert captured["documents"] == ["text 0", "text 1", "text 2"]
    # chunks come back in the sidecar's ranked order, tagged "reranked"
    assert [s.chunk.id for s in out] == ["c2", "c1", "c0"]
    assert all(s.retrieval_method == "reranked" for s in out)
    assert out[0].score >= out[1].score >= out[2].score


@pytest.mark.asyncio
async def test_reranker_empty_candidates_short_circuits():
    called = False

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        nonlocal called
        called = True
        return httpx.Response(200, json={"scores": [], "indices": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        rr = SidecarReranker("http://crossencoder:50052", http)
        assert await rr.score("q", []) == []
    assert called is False  # no HTTP call for an empty pool


@pytest.mark.asyncio
async def test_reranker_propagates_http_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="model loading")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        rr = SidecarReranker("http://crossencoder:50052", http)
        with pytest.raises(httpx.HTTPStatusError):
            await rr.score("q", _chunks(2))
