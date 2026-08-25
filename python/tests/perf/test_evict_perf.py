"""Perf budget for eviction victim selection (#359): ``choose_victims`` over
1,000 registry rows, with the protected predicate built over a 1,000-entry
registry and an in-flight set, p95 < 5 ms.

Selection is pure and synchronous (the I/O — listing rows, one in-flight
query, flushing the access tracker — happens once in the caller), so this
times exactly the policy: one sort by stamp plus O(1) checks per row. The
budget covers ``make_protected`` too, since the create path builds it per
call.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from ragstack.collection_store import ACTIVE, DORMANT, CollectionRecord, CollectionSpec
from ragstack.ops.evict import choose_victims, make_protected
from tests.perf._budget import assert_budget


@dataclass
class _Entry:
    id: str
    collection: str
    text_index_name: str = ""
    is_shared_surface: bool = False

    def es_index(self) -> str:
        return self.text_index_name or self.collection


class _Registry:
    def __init__(self, entries):
        self._entries = {e.id: e for e in entries}

    def entries(self):
        return list(self._entries.values())


def _rows(n: int) -> list[CollectionRecord]:
    base = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    rows = []
    for i in range(n):
        spec = CollectionSpec(
            id=f"lib-{i}", collection=f"ragstack_lib_{i}", embedding_model="m",
            embedding_model_dim=4, chunk_method="fixed",
        )
        stamp = datetime.fromtimestamp(base + (i * 7919) % 86_400 * 30, tz=UTC).isoformat()
        rows.append(CollectionRecord(
            spec=spec, spec_hash="h", created_at=stamp, last_accessed_at=stamp,
            versions=[1] if i % 5 else [], archive_pending=(i % 11 == 0),
            state=DORMANT if i % 13 == 0 else ACTIVE,
        ))
    return rows


@pytest.mark.perf
def test_choose_victims_over_1000_rows_p95_budget():
    rows = _rows(1000)
    registry = _Registry(
        [_Entry("default", "ragstack", is_shared_surface=True)]
        + [_Entry(r.spec.id, r.spec.collection) for r in rows]
    )
    in_flight = frozenset(f"lib-{i}" for i in range(0, 1000, 17))
    registered = {e.id for e in registry.entries()}
    now = time.time()

    def _select_once() -> None:
        victims, shortfall = choose_victims(
            rows, 1, now=now, in_flight=in_flight,
            protected=make_protected(registry, derived=("ragstack", "ragstack")),
            registered=registered,
        )
        assert len(victims) == 1 and shortfall.found == 1

    assert_budget("evict_choose_victims_1000", _select_once, budget_s=0.005, n=50)
