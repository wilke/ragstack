"""Qdrant health counters as observability (cherry-pick 4/4 from the closed #144
branch).

The branch gated backpressure on ``points_count - indexed_vectors_count`` as an
optimizer backlog. Review measured that against live collections and it is
unsound in BOTH regimes — see the CollectionHealth comment and issue #271 —
so only the raw counters are taken here, as a diagnostic. No throttle behaviour
changes.
"""
from __future__ import annotations

import pytest

from ragstack.stores.backpressure import BackpressuredVectorStore
from ragstack.stores.qdrant import CollectionHealth


def test_counters_default_so_existing_constructors_stay_valid():
    h = CollectionHealth(status="green", optimizer_ok=True, segments_count=2)
    assert h.points_count == 0
    assert h.indexed_vectors_count == 0


def test_counters_round_trip():
    h = CollectionHealth(
        status="green", optimizer_ok=True, segments_count=4,
        points_count=1000, indexed_vectors_count=600,
    )
    assert (h.points_count, h.indexed_vectors_count) == (1000, 600)


def test_indexed_may_legitimately_exceed_points():
    """Not an anomaly to correct — measured as the steady state on a mature
    production collection (+125,051 on 24.8M points). Recording it as a fact is
    the point; deriving a 'backlog' from it is what review disproved."""
    h = CollectionHealth(
        status="green", optimizer_ok=True, segments_count=8,
        points_count=24_830_600, indexed_vectors_count=24_955_651,
    )
    assert h.indexed_vectors_count > h.points_count


class _Inner:
    def __init__(self) -> None:
        self.upserts = 0

    async def upsert(self, chunks):  # noqa: ARG002
        self.upserts += 1


@pytest.mark.asyncio
async def test_throttle_behaviour_is_unchanged_by_the_counters():
    """A green collection proceeds immediately even with a huge points/indexed
    gap — the counters are diagnostic, never a gate."""
    async def health():
        return CollectionHealth(
            "green", True, 8, points_count=10**6, indexed_vectors_count=0
        )

    inner = _Inner()
    store = BackpressuredVectorStore(inner, poll_interval=0.0, health=health)
    await store.upsert([])
    assert inner.upserts == 1
