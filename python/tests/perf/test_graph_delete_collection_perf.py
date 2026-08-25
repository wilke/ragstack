"""Perf budget for ``InMemoryGraphStore.delete_collection`` (#380): dropping
one collection's 10,000 triples out of a 20,000-triple store (a sibling
collection of the same size must survive the scan), p95 < 20 ms.

The delete is destructive, so the timed callable RESETS the store from a
prebuilt template before each run — otherwise 19 of 20 samples would time the
idempotent no-op and prove nothing. The reset is a dict copy (~1 ms at this
size) and is inside the budget on purpose: it keeps the measurement honest
rather than optimistic.
"""
from __future__ import annotations

import pytest

from ragstack.models import Triple
from ragstack.stores import InMemoryGraphStore
from tests.perf._budget import assert_budget_async

N = 10_000


def _triples(collection: str, n: int) -> list[Triple]:
    return [
        Triple(subject=f"e{i}", predicate="rel", object=f"e{i + 1}", doc_id=f"d{i // 8}",
               tenant_id="alice" if i % 3 else "public", collection=collection)
        for i in range(n)
    ]


@pytest.mark.perf
@pytest.mark.asyncio
async def test_delete_collection_10k_triples_under_20ms():
    template = InMemoryGraphStore()
    await template.add_triples(_triples("victim", N) + _triples("sibling", N))
    assert len(template._by_key) == 2 * N
    store = InMemoryGraphStore()
    removed: list[int] = []

    async def run() -> None:
        store._by_key = dict(template._by_key)  # reset: the delete is destructive
        store._entity_refs = {k: dict(v) for k, v in template._entity_refs.items()}
        removed.append(await store.delete_collection(None, "victim"))

    await assert_budget_async("graph.delete_collection(10k of 20k)", run, budget_s=0.020, n=30)
    assert set(removed) == {N}
    assert len(store._by_key) == N  # the sibling survived every run
    assert {k[1] for k in store._entity_refs} == {"sibling"}  # the index followed (#349)
