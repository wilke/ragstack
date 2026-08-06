"""Backlog-aware backpressure (cherry-pick 4/4 from the closed #144 branch).

Status alone is not a sufficient throttle signal: Qdrant reports ``green`` while
the optimizer carries a large unindexed backlog, and it is the backlog — not the
status — that tracks toward VMA exhaustion (#140). The closed branch had this
insight in a standalone HealthGate; this expresses it through main's
BackpressuredVectorStore instead of alongside it.
"""
from __future__ import annotations

import pytest

from ragstack.stores.backpressure import BackpressuredVectorStore
from ragstack.stores.qdrant import CollectionHealth

pytestmark = pytest.mark.asyncio


def test_unindexed_is_the_optimizer_backlog():
    h = CollectionHealth(
        status="green", optimizer_ok=True, segments_count=4,
        points_count=1000, indexed_vectors_count=600,
    )
    assert h.unindexed == 400


def test_unindexed_never_goes_negative():
    # indexed can briefly exceed points across a delete; a negative backlog would
    # read as "healthy" by accident rather than by measurement.
    h = CollectionHealth(
        status="green", optimizer_ok=True, segments_count=1,
        points_count=10, indexed_vectors_count=99,
    )
    assert h.unindexed == 0


def test_defaults_keep_every_existing_constructor_valid():
    h = CollectionHealth(status="green", optimizer_ok=True, segments_count=2)
    assert h.points_count == 0 and h.indexed_vectors_count == 0
    assert h.unindexed == 0


class _Inner:
    def __init__(self) -> None:
        self.upserts = 0

    async def upsert(self, chunks):  # noqa: ARG002
        self.upserts += 1


async def test_a_green_collection_with_a_big_backlog_is_still_held():
    """The whole point: green + healthy optimizer, but 5000 vectors behind."""
    healths = [
        CollectionHealth("green", True, 8, points_count=6000, indexed_vectors_count=1000),
        CollectionHealth("green", True, 8, points_count=6000, indexed_vectors_count=5900),
    ]
    calls = {"n": 0}

    async def health():
        h = healths[min(calls["n"], len(healths) - 1)]
        calls["n"] += 1
        return h

    inner = _Inner()
    store = BackpressuredVectorStore(
        inner, poll_interval=0.0, max_unindexed=500, health=health
    )
    await store.upsert([])
    assert calls["n"] == 2, "should have held once, then proceeded"
    assert inner.upserts == 1


async def test_without_the_knob_the_backlog_is_ignored():
    """Default None preserves the pre-existing status-only behaviour exactly."""
    async def health():
        return CollectionHealth(
            "green", True, 8, points_count=10**6, indexed_vectors_count=0
        )

    inner = _Inner()
    store = BackpressuredVectorStore(inner, poll_interval=0.0, health=health)
    await store.upsert([])
    assert inner.upserts == 1
