"""Perf budget for multi-collection fan-out (issue #253; #355 convention).

Two promises. The N legs run CONCURRENTLY: the wall time of a five-leg
retrieval is at most 1.5× the slowest single leg (each fake leg sleeps, so a
serial fan-out would take 5× and fail loudly). And the reranker is called
EXACTLY ONCE per request, over the fused union — never once per leg.
"""
import asyncio
import time

import pytest

from ragstack.api.routers.query import _retrieve_fused
from ragstack.models import Chunk, ScoredChunk
from ragstack.retrieval.retriever import CollectionLeg, MultiCollectionRetriever
from tests.perf._budget import assert_budget_async

_N_LEGS = 5
_LEG_SLEEP = 0.02
_N = 30


class SleepingLeg:
    """A single-collection retriever whose stores take ``delay`` to answer."""

    def __init__(self, cid: str, delay: float, n: int = 20) -> None:
        self.cid, self.delay = cid, delay
        self.chunks = [
            Chunk(id=f"{cid}-{i}", doc_id=cid, content=f"{cid} {i}", metadata={"tenant_id": "public"})
            for i in range(n)
        ]
        self.calls = 0

    async def retrieve(self, query, top_k=5, filters=None, use_graph=True, tenant_id=None, mode="hybrid"):
        self.calls += 1
        await asyncio.sleep(self.delay)
        return [ScoredChunk(chunk=c, score=1.0 - i * 0.01) for i, c in enumerate(self.chunks[:top_k])]


def _wrapper(delay: float = _LEG_SLEEP) -> tuple[MultiCollectionRetriever, list[SleepingLeg]]:
    legs = [SleepingLeg(f"c{i}", delay) for i in range(_N_LEGS)]
    return MultiCollectionRetriever([CollectionLeg(id=leg.cid, retriever=leg) for leg in legs]), legs


@pytest.mark.perf
@pytest.mark.asyncio
async def test_five_legs_run_concurrently():
    wrapper, legs = _wrapper()
    # The slowest single leg, measured — the budget is 1.5× that, not a constant.
    slowest = 0.0
    for leg in legs:
        start = time.perf_counter()
        await leg.retrieve("q", top_k=10, tenant_id="alice")
        slowest = max(slowest, time.perf_counter() - start)

    async def _once() -> None:
        out = await wrapper.retrieve("q", top_k=10, tenant_id="alice", use_graph=False)
        assert len(out) == _N_LEGS * 10  # the whole union, every leg's depth

    await assert_budget_async(
        f"multi_collection_{_N_LEGS}_legs_wall", _once, budget_s=1.5 * slowest, n=_N,
    )
    print(f"PERF multi_collection slowest_single_leg={slowest:.4f}s")
    assert all(leg.calls == 1 + _N for leg in legs)


class CountingReranker:
    def __init__(self) -> None:
        self.calls: list[int] = []

    async def score(self, query, candidates, top_k=None):
        self.calls.append(len(candidates))
        out = [ScoredChunk(chunk=c, score=float(len(candidates) - i)) for i, c in enumerate(candidates)]
        return out if top_k is None else out[:top_k]


@pytest.mark.perf
@pytest.mark.asyncio
async def test_exactly_one_rerank_call_over_the_union():
    wrapper, legs = _wrapper(delay=0.0)
    reranker = CountingReranker()
    for _ in range(_N):
        scored = await _retrieve_fused(
            wrapper, reranker, "q", ["q"], 5, {}, False,
            rerank=True, rerank_candidates=20, tenant_id="alice",
        )
        assert len(scored) == 5 and all(s.collection for s in scored)
    # One rerank per request, each over the whole union (5 legs × depth 20).
    assert reranker.calls == [_N_LEGS * 20] * _N
    print(f"PERF multi_collection_rerank_calls: {len(reranker.calls)} calls for {_N} requests, "
          f"pool={_N_LEGS * 20}")
