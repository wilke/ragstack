"""Perf budget for server-side context expansion (issue #322; #355 convention).

Two promises: the expansion costs ONE extra ``get_chunks`` round trip per
request at ``window=1`` regardless of ``top_k`` (asserted on the counting
store), and against the in-memory store it adds < 15 ms p95 for ``top_k=10``,
``window=1``. Timing ``expand_context`` alone is the "added latency": it is
the only thing the window switches on — retrieval, rerank and truncation run
before it, unchanged.
"""
import pytest

from ragstack.retrieval.retriever import expand_context
from tests.perf._budget import assert_budget_async
from tests.unit.test_context_expansion import FILTERS, CountingStore, make_doc, scored

_N_DOCS = 20
_CHUNKS_PER_DOC = 10
_TOP_K = 10
_N = 50


async def _seed() -> tuple[CountingStore, list]:
    store = CountingStore()
    docs = [make_doc(f"D{i}", _CHUNKS_PER_DOC) for i in range(_N_DOCS)]
    for d in docs:
        await store.upsert(d)
    # top_k=10 sources, one mid-document chunk from each of ten documents, so
    # every source has two non-source neighbours to fetch.
    ranked = scored(*(docs[i][5] for i in range(_TOP_K)))
    return store, ranked


@pytest.mark.perf
@pytest.mark.asyncio
async def test_context_window_1_one_round_trip_and_latency_budget():
    store, ranked = await _seed()

    async def _once() -> None:
        out = await expand_context(store, ranked, 1, FILTERS)
        assert len(out) == _TOP_K

    await assert_budget_async(
        "context_window_1_top_k_10", _once, budget_s=0.015, n=_N,
    )
    # Exactly one get_chunks per request: _N reps => _N calls, each carrying
    # every source's two neighbour ids in a single batch.
    assert store.calls == _N
    assert all(len(b) == 2 * _TOP_K for b in store.batches)


@pytest.mark.perf
@pytest.mark.asyncio
async def test_round_trips_independent_of_top_k():
    store, ranked = await _seed()
    for top_k in (1, 3, 10):
        store.batches.clear()
        await expand_context(store, ranked[:top_k], 1, FILTERS)
        assert store.calls == 1, f"top_k={top_k}: {store.calls} calls"
        assert len(store.batches[0]) == 2 * top_k
    # window=3: one batch per hop, never per source — three at most.
    store.batches.clear()
    await expand_context(store, ranked, 3, FILTERS)
    assert store.calls == 3
