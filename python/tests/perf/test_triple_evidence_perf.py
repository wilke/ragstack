"""Perf budgets for the #347 evidence fields (#355 convention).

* The six extra properties add no upsert cost beyond noise: 1k triples into a
  fresh ``InMemoryGraphStore`` with every field stamped stays within 10 % of the
  same upsert with the fields left at their defaults, p95 over interleaved runs.
* The graph leg's confidence filter over 10k triples is < 1 ms p95 on the
  non-short-circuit path (a floor of 2; floor 0 returns the input list itself).
"""
from __future__ import annotations

import asyncio
import time

import pytest

from ragstack.models import Triple
from ragstack.retrieval.retriever import filter_by_confidence
from ragstack.stores import InMemoryGraphStore
from tests.perf._budget import _percentile, assert_budget

_N_UPSERT = 1_000
_N_FILTER = 10_000
_RUNS = 60


def _bare(i: int) -> Triple:
    return Triple(subject=f"s{i}", predicate="p", object=f"o{i}", doc_id=f"d{i % 50}",
                  tenant_id="public", collection="x")


def _stamped(i: int) -> Triple:
    return Triple(subject=f"s{i}", predicate="p", object=f"o{i}", doc_id=f"d{i % 50}",
                  tenant_id="public", collection="x",
                  evidence=f"s{i} p o{i}, as stated in the source sentence number {i}.",
                  chunk_id=f"d{i % 50}:c{i}", derived_by="tool:bvbrc", confidence=i % 4,
                  subject_id=f"bvbrc:genome:{i}", object_id=f"bvbrc:feature:{i}")


@pytest.mark.perf
def test_upsert_1k_stamped_within_10pct_of_bare_baseline():
    bare = [_bare(i) for i in range(_N_UPSERT)]          # built outside the timing
    stamped = [_stamped(i) for i in range(_N_UPSERT)]

    def timed(triples: list[Triple]) -> float:
        store = InMemoryGraphStore()                     # fresh: measures upsert, not ON MATCH
        start = time.perf_counter()
        asyncio.run(store.add_triples(triples))
        elapsed = time.perf_counter() - start
        assert len(store._triples) == _N_UPSERT
        return elapsed

    timed(bare), timed(stamped)                          # warm-up
    base_s: list[float] = []
    stamp_s: list[float] = []
    for _ in range(_RUNS):                               # interleaved, same conditions
        base_s.append(timed(bare))
        stamp_s.append(timed(stamped))
    base_s.sort()
    stamp_s.sort()
    p95_base, p95_stamp = _percentile(base_s, 0.95), _percentile(stamp_s, 0.95)
    print(f"PERF upsert_1k_bare: p50={_percentile(base_s, 0.5):.4f}s p95={p95_base:.4f}s n={_RUNS}")
    print(f"PERF upsert_1k_stamped: p50={_percentile(stamp_s, 0.5):.4f}s p95={p95_stamp:.4f}s "
          f"n={_RUNS} ratio={p95_stamp / p95_base:.3f} budget=1.100")
    assert p95_stamp <= 1.10 * p95_base, (
        f"stamped upsert p95={p95_stamp:.4f}s is more than 10% over bare p95={p95_base:.4f}s"
    )


@pytest.mark.perf
def test_confidence_filter_over_10k_triples_under_1ms():
    triples = [_stamped(i) for i in range(_N_FILTER)]
    kept = filter_by_confidence(triples, 2)
    assert len(kept) == _N_FILTER // 2                   # confidence = i % 4 → {2, 3}
    assert_budget(
        "confidence_filter_10k", lambda: filter_by_confidence(triples, 2),
        budget_s=0.001, n=50,
    )
