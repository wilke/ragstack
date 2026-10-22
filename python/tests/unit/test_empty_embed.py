"""Every embedder is safe on an empty input (#768).

vLLM answers an empty ``input`` with HTTP 400. ``PooledEmbedder.
_embed_isolated_range`` bisected a 4xx'd request and stopped only at one
index, so an empty one split ``[]`` into ``[] + []`` until RecursionError — and
the GoWe ingest task of a batch whose every document the duplicate guard had
refused exited 1. The rule these tests hold: nothing to embed is a valid
request with an empty answer and NO HTTP call, on every embedder.

Each backend double here 400s on an empty batch, like vLLM, so a missing guard
fails loudly instead of passing on a fake that happens to accept ``[]``.
"""
from __future__ import annotations

import json

import httpx
import pytest

from ragstack.embed_pool import Endpoint, PooledEmbedder
from ragstack.embedders import BatchingEmbedder, OpenAIEmbedder, SidecarEmbedder


def _status_error(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "http://e/v1/embeddings")
    return httpx.HTTPStatusError("err", request=req, response=httpx.Response(code, request=req))


class _VllmLike:
    """An endpoint embedder that rejects an empty batch with a 400, like vLLM."""

    def __init__(self) -> None:
        self.calls = 0
        self.empty_calls = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if not texts:
            self.empty_calls += 1
            if self.empty_calls > 50:  # keep an unfixed run fast and finite
                raise RecursionError("bisected an empty input 50 times")
            raise _status_error(400)
        return [[float(len(t))] for t in texts]


def _transport(requests: list[httpx.Request]) -> httpx.MockTransport:
    """An HTTP backend for both wire formats; 400 on an empty batch."""

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content or b"{}")
        texts = body.get("input", body.get("texts", []))
        if not texts:
            return httpx.Response(400, json={"error": "empty input"})
        if request.url.path.endswith("/v1/embeddings"):
            return httpx.Response(200, json={"data": [
                {"index": i, "embedding": [1.0]} for i in range(len(texts))]})
        return httpx.Response(200, json={"embeddings": [[1.0] for _ in texts]})

    return httpx.MockTransport(handler)


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


async def test_openai_embedder_empty_input_makes_no_request() -> None:
    requests: list[httpx.Request] = []
    async with httpx.AsyncClient(transport=_transport(requests)) as client:
        emb = OpenAIEmbedder("http://vllm", "m", http=client)
        assert await emb.embed([]) == []
        assert requests == []
        assert await emb.embed(["a"]) == [[1.0]]  # the double itself works
        assert len(requests) == 1


async def test_sidecar_embedder_empty_input_makes_no_request() -> None:
    requests: list[httpx.Request] = []
    async with httpx.AsyncClient(transport=_transport(requests)) as client:
        emb = SidecarEmbedder("http://sidecar", http=client)
        assert await emb.embed([]) == []
        assert requests == []
        assert await emb.embed(["a"]) == [[1.0]]
        assert len(requests) == 1


async def test_pooled_embed_empty_input_makes_no_call(http) -> None:
    ep = _VllmLike()
    pool = PooledEmbedder([Endpoint(ep, "http://e0/health")], http=http)
    assert await pool.embed([]) == []
    assert ep.calls == 0


async def test_pooled_embed_isolated_empty_returns_nothing_quarantined(http) -> None:
    """The issue's unit test: ``embed_isolated([]) == ([], 0)`` and no call."""
    ep = _VllmLike()
    pool = PooledEmbedder([Endpoint(ep, "http://e0/health")], http=http)
    assert await pool.embed_isolated([]) == ([], 0)
    assert ep.calls == 0


async def test_pooled_isolated_range_on_no_indices_cannot_recurse(http) -> None:
    """The bisect itself has a base case for nothing, independent of ``embed``'s
    own guard: hand it an empty index list over an endpoint that 400s on an
    empty batch, and it must return 0 without calling — not bisect ``[]``."""
    ep = _VllmLike()
    pool = PooledEmbedder([Endpoint(ep, "http://e0/health")], http=http)

    async def raw_embed(texts):  # bypass embed()'s own empty guard
        return await pool._embed_one(texts)

    pool.embed = raw_embed  # type: ignore[method-assign]
    out: list = []
    assert await pool._embed_isolated_range([], [], out) == 0
    assert ep.calls == 0 and out == []


async def test_pooled_isolated_still_bisects_a_real_poison_input(http) -> None:
    """The guard is for nothing-to-embed only: a genuinely bad input is still
    quarantined, and its neighbours still embedded."""

    class _Poison(_VllmLike):
        async def embed(self, texts):
            if "BAD" in texts:
                raise _status_error(400)
            return await super().embed(texts)

    pool = PooledEmbedder([Endpoint(_Poison(), "http://e0/health")], http=http)
    vecs, quarantined = await pool.embed_isolated(["a", "BAD", "ccc"])
    assert quarantined == 1 and vecs == [[1.0], None, [3.0]]


async def test_batching_embedder_empty_input_makes_no_call() -> None:
    base = _VllmLike()
    emb = BatchingEmbedder(base)
    assert await emb.embed([]) == []
    assert await emb.embed_isolated([]) == ([], 0)
    out: list = []
    assert await emb._embed_group([], [], out) == 0
    assert base.calls == 0

