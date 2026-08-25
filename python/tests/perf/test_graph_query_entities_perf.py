"""Perf budget for query-side entity extraction in the graph leg (#349, #355):
matching a 30-token query against 10k entity names on the in-memory store in
< 2 ms p95, and at most ``graph_query_entity_max`` (5) neighbourhood calls per
query however many entities the query names.

The in-memory entity index is maintained at write time (refcounted per
``(tenant, collection)`` bucket), so there is nothing to warm: the first
``match_entities`` costs the same dict probes as the thousandth. What is timed
is the whole matching step — ``query_candidates`` (tokenise + 1–3-grams) plus
``match_entities`` plus the longest-first selection — i.e. everything the leg
does before its first neighbourhood query.

    pytest tests/perf/test_graph_query_entities_perf.py -m perf -q -s
"""
from __future__ import annotations

import pytest

from ragstack.models import Triple
from ragstack.retrieval.retriever import HybridRetriever
from ragstack.stores import InMemoryGraphStore, InMemoryTextIndex, InMemoryVectorStore
from tests.perf._budget import assert_budget_async

N_ENTITIES = 10_000
BUDGET_S = 0.002
TENANT = "alice"
COL = "corpus_x"

# 30 tokens; eight of them name entities in the 10k index (see _big_store).
QUERY = (
    "what does entity-17 do to entity-4242 when entity-99 and entity-1234 "
    "are given with entity-5 entity-77 entity-8080 entity-9999 in patients "
    "who also take a daily dose of something else entirely unrelated"
)
assert len(QUERY.split()) == 30


class _FakeEmbedder:
    async def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


class _CountingStore(InMemoryGraphStore):
    def __init__(self) -> None:
        super().__init__()
        self.neighborhood_calls = 0

    async def query_neighborhood(self, entity, depth=1, tenant_id=None, collection=None):
        self.neighborhood_calls += 1
        return await super().query_neighborhood(
            entity, depth=depth, tenant_id=tenant_id, collection=collection
        )


async def _big_store() -> _CountingStore:
    store = _CountingStore()
    # A ring: entity-i -> entity-(i+1). 10k distinct names, 10k triples.
    await store.add_triples([
        Triple(subject=f"entity-{i}", predicate="links", object=f"entity-{(i + 1) % N_ENTITIES}",
               doc_id=f"d{i // 100}", tenant_id=TENANT, collection=COL)
        for i in range(N_ENTITIES)
    ])
    return store


@pytest.mark.perf
@pytest.mark.asyncio
async def test_entity_matching_30_tokens_vs_10k_names_under_2ms() -> None:
    store = await _big_store()
    entities, _ = await store.stats(tenant_id=TENANT, collection=COL)
    assert entities == N_ENTITIES
    retriever = HybridRetriever(
        InMemoryVectorStore(), InMemoryTextIndex(), _FakeEmbedder(),
        graph_store=store, collection=COL,
    )

    matched = await retriever.query_entities(QUERY, TENANT)
    assert len(matched) == 5 and set(matched) <= {
        "entity-17", "entity-4242", "entity-99", "entity-1234",
        "entity-5", "entity-77", "entity-8080", "entity-9999"}

    await assert_budget_async(
        "graph_query_entity_match_30tok_10k",
        lambda: retriever.query_entities(QUERY, TENANT),
        budget_s=BUDGET_S,
        n=200,
    )


@pytest.mark.perf
@pytest.mark.asyncio
async def test_at_most_five_neighbourhood_calls_per_query() -> None:
    store = await _big_store()
    retriever = HybridRetriever(
        InMemoryVectorStore(), InMemoryTextIndex(), _FakeEmbedder(),
        graph_store=store, collection=COL,
    )
    n_requests = 30
    for _ in range(n_requests):
        out = await retriever.retrieve(QUERY, top_k=10, use_graph=True, tenant_id=TENANT)
        assert out
    # Eight entities in the query, five expanded — never more, per request.
    print(f"PERF graph_query_neighbourhood_calls: {store.neighborhood_calls} calls "
          f"for {n_requests} requests, entities_in_query=8, cap=5")
    assert store.neighborhood_calls == 5 * n_requests
