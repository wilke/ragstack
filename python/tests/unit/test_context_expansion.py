"""Server-side context expansion (issue #322): ``expand_context`` walks each
returned source's ``prev_chunk_id`` / ``next_chunk_id`` up to ``window`` hops
each way through the vector store's ``get_chunks`` and returns the neighbours
per source — never touching the ranking.

Exercised against the real ``InMemoryVectorStore`` (wrapped to count
``get_chunks`` calls), so the scope guarantee under test is the one #197 made:
``get_chunks`` honours the same filter dict ``search()`` does, so a neighbour
under another tenant — or one a user filter excludes — is simply not returned.
"""
from __future__ import annotations

from typing import Any

import pytest

from ragstack.ingestion.chunkers import link_neighbors_by_document
from ragstack.models import Chunk, ScoredChunk
from ragstack.retrieval.retriever import expand_context
from ragstack.stores.filters import UnknownFilterKey
from ragstack.stores.memory import InMemoryVectorStore
from ragstack.tenancy import scope_filters

pytestmark = pytest.mark.asyncio


class CountingStore(InMemoryVectorStore):
    """The in-memory store, recording every ``get_chunks`` batch it is asked for."""

    def __init__(self) -> None:
        super().__init__()
        self.batches: list[list[str]] = []

    @property
    def calls(self) -> int:
        return len(self.batches)

    async def get_chunks(
        self, chunk_ids: list[str], filters: dict[str, Any] | None = None
    ) -> list[Chunk]:
        self.batches.append(list(chunk_ids))
        return await super().get_chunks(chunk_ids, filters)


def make_doc(doc: str, n: int, tenant: str = "alice", **extra: Any) -> list[Chunk]:
    """``n`` linked chunks ``<doc>-c0 .. <doc>-c<n-1>`` stamped for ``tenant``."""
    chunks = [
        Chunk(
            id=f"{doc}-c{i}",
            doc_id=doc,
            content=f"{doc} passage {i}",
            metadata={"tenant_id": tenant, "source": f"{doc}.txt", **extra},
        )
        for i in range(n)
    ]
    link_neighbors_by_document(chunks)
    return chunks


def scored(*chunks: Chunk) -> list[ScoredChunk]:
    """A descending-score ranked list over ``chunks`` in the given order."""
    return [
        ScoredChunk(chunk=c, score=1.0 - i * 0.1, retrieval_method="hybrid")
        for i, c in enumerate(chunks)
    ]


async def seeded(*docs: list[Chunk]) -> CountingStore:
    store = CountingStore()
    for d in docs:
        await store.upsert(d)
    return store


FILTERS = scope_filters({}, "alice")  # alice + public, as the router builds it


def positions(ctx) -> list[tuple[int, str]]:
    return [(c.position, c.chunk_id) for c in ctx]


async def test_window_1_attaches_prev_and_next():
    doc = make_doc("A", 5)
    store = await seeded(doc)
    ranked = scored(doc[2])

    out = await expand_context(store, ranked, 1, FILTERS)

    assert positions(out["A-c2"]) == [(-1, "A-c1"), (1, "A-c3")]
    assert [c.content for c in out["A-c2"]] == ["A passage 1", "A passage 3"]
    assert store.calls == 1 and sorted(store.batches[0]) == ["A-c1", "A-c3"]


async def test_document_boundaries_respected():
    doc = make_doc("A", 3)
    store = await seeded(doc)
    ranked = scored(doc[0], doc[2])  # first and last chunk of the document

    out = await expand_context(store, ranked, 1, FILTERS)

    assert positions(out["A-c0"]) == [(1, "A-c1")]  # no prev: nothing before
    assert positions(out["A-c2"]) == [(-1, "A-c1")]  # no next: nothing after


async def test_single_chunk_document_gets_no_context_key():
    doc = make_doc("solo", 1)
    store = await seeded(doc)

    out = await expand_context(store, scored(doc[0]), 3, FILTERS)

    assert out == {}  # no neighbour at all -> nothing attached, no empty list
    assert store.calls == 0  # nothing to fetch, no round trip


@pytest.mark.parametrize("legacy", ["None", "", None])
async def test_legacy_none_links_are_document_edges(legacy):
    # Older bulk loads stamped the literal string "None" (docs/USER-GUIDE.md).
    doc = make_doc("A", 2)
    doc[1].metadata["next_chunk_id"] = legacy
    store = await seeded(doc)

    out = await expand_context(store, scored(doc[1]), 2, FILTERS)

    assert positions(out["A-c1"]) == [(-1, "A-c0")]
    assert all("None" not in b and "" not in b for b in store.batches)


async def test_window_3_walks_the_chain_one_batch_per_hop():
    doc = make_doc("A", 9)
    store = await seeded(doc)
    ranked = scored(doc[4])

    out = await expand_context(store, ranked, 3, FILTERS)

    assert positions(out["A-c4"]) == [
        (-3, "A-c1"), (-2, "A-c2"), (-1, "A-c3"), (1, "A-c5"), (2, "A-c6"), (3, "A-c7"),
    ]
    # Hop h's ids are only known once hop h-1 is in hand: one batch per hop,
    # each batch carrying BOTH directions for every source.
    assert store.batches == [["A-c3", "A-c5"], ["A-c2", "A-c6"], ["A-c1", "A-c7"]]


async def test_window_3_stops_at_the_edge_without_extra_calls():
    doc = make_doc("A", 3)
    store = await seeded(doc)

    out = await expand_context(store, scored(doc[1]), 3, FILTERS)

    assert positions(out["A-c1"]) == [(-1, "A-c0"), (1, "A-c2")]
    assert store.calls == 1  # hop 2 has nowhere to go: no second round trip


async def test_batches_are_one_per_hop_regardless_of_source_count():
    docs = [make_doc(f"D{i}", 5) for i in range(10)]
    store = await seeded(*docs)
    ranked = scored(*(d[2] for d in docs))  # ten sources from ten documents

    out = await expand_context(store, ranked, 1, FILTERS)

    assert len(out) == 10 and all(len(ctx) == 2 for ctx in out.values())
    assert store.calls == 1 and len(store.batches[0]) == 20


async def test_out_of_scope_neighbour_omitted_and_walk_stops():
    # The #197 guarantee: get_chunks applies the same scope predicate search()
    # did. A neighbour written by another tenant is invisible to alice, so it
    # is omitted — and nothing beyond it is reachable through it.
    doc = make_doc("A", 5)
    doc[3].metadata["tenant_id"] = "bob"  # next of the source belongs to bob
    store = await seeded(doc)

    out = await expand_context(store, scored(doc[2]), 3, FILTERS)

    assert positions(out["A-c2"]) == [(-2, "A-c0"), (-1, "A-c1")]
    assert "A-c3" not in {c.chunk_id for c in out["A-c2"]}
    assert "A-c4" not in {c.chunk_id for c in out["A-c2"]}  # unreachable past bob's


async def test_public_neighbour_is_visible_to_every_tenant():
    doc = make_doc("A", 3, tenant="public")
    store = await seeded(doc)

    out = await expand_context(store, scored(doc[1]), 1, FILTERS)

    assert positions(out["A-c1"]) == [(-1, "A-c0"), (1, "A-c2")]


async def test_user_filters_pass_through_to_the_neighbour_fetch():
    # A user filter that the retrieval honoured (say, source=A.txt) scopes the
    # neighbours too: a neighbour whose payload doesn't satisfy it is omitted.
    doc = make_doc("A", 3)
    doc[2].metadata["source"] = "other.txt"
    store = await seeded(doc)

    out = await expand_context(
        store, scored(doc[1]), 1, scope_filters({"source": "A.txt"}, "alice")
    )

    assert positions(out["A-c1"]) == [(-1, "A-c0")]


async def test_refused_filter_key_propagates():
    doc = make_doc("A", 3)
    store = await seeded(doc)
    with pytest.raises(UnknownFilterKey):
        await expand_context(store, scored(doc[1]), 1, {"doc_id": "A", "tenant_id": ["alice"]})


async def test_neighbour_that_is_itself_a_source_is_not_duplicated():
    doc = make_doc("A", 5)
    store = await seeded(doc)
    ranked = scored(doc[2], doc[3])  # adjacent chunks both retrieved

    out = await expand_context(store, ranked, 1, FILTERS)

    # Each source's neighbour on the shared side is the other source: its content
    # is already in the response as a scored source, so it is not attached again.
    assert positions(out["A-c2"]) == [(-1, "A-c1")]
    assert positions(out["A-c3"]) == [(1, "A-c4")]
    # ...and it wasn't fetched either: its links were already in hand.
    assert store.calls == 1 and sorted(store.batches[0]) == ["A-c1", "A-c4"]


async def test_walk_continues_through_a_source_neighbour():
    doc = make_doc("A", 6)
    store = await seeded(doc)
    ranked = scored(doc[2], doc[3])

    out = await expand_context(store, ranked, 2, FILTERS)

    # From c2: c3 is a source (skipped), c4 beyond it is attached at +2.
    assert positions(out["A-c2"]) == [(-2, "A-c0"), (-1, "A-c1"), (2, "A-c4")]
    assert positions(out["A-c3"]) == [(-2, "A-c1"), (1, "A-c4"), (2, "A-c5")]


async def test_ranking_is_untouched():
    docs = [make_doc(f"D{i}", 4) for i in range(4)]
    store = await seeded(*docs)
    ranked = scored(docs[0][1], docs[2][2], docs[1][0], docs[3][3])
    before = [(s.chunk.id, s.score, s.retrieval_method) for s in ranked]

    out = await expand_context(store, ranked, 2, FILTERS)

    assert [(s.chunk.id, s.score, s.retrieval_method) for s in ranked] == before
    assert set(out) <= {s.chunk.id for s in ranked}  # keyed by the sources only
    # No neighbour leaks into the scored list — it is decoration, keyed aside.
    assert not any(c.chunk_id in {s.chunk.id for s in ranked} for v in out.values() for c in v)


async def test_window_0_is_a_no_op_with_no_store_call():
    doc = make_doc("A", 3)
    store = await seeded(doc)
    assert await expand_context(store, scored(doc[1]), 0, FILTERS) == {}
    assert store.calls == 0


async def test_empty_ranking_is_a_no_op():
    store = await seeded(make_doc("A", 3))
    assert await expand_context(store, [], 3, FILTERS) == {}
    assert store.calls == 0
