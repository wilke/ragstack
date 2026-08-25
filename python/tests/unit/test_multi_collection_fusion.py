"""Multi-collection fused retrieval (issue #253): N single-collection legs,
one RRF, one rerank — and never a many-valued store filter (#199, #354).

Exercised at two seams. :class:`MultiCollectionRetriever` (retrieval/retriever.py)
over real ``HybridRetriever`` legs whose stores are recording fakes, so every
filter dict a store ever sees is on record; and the router's
``_resolve_retrieval`` (api/routers/query.py), which must resolve and
read-authorize EVERY member before any leg runs — asserted on the same fakes:
a refused request leaves every store untouched.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from ragstack.acl_store import (
    GRANTEE_GROUP,
    GRANTEE_USER,
    PERM_OWNER,
    PERM_READ,
    PUBLIC_GROUP,
    InMemoryAclStore,
    reset_acl_store,
    set_acl_store,
)
from ragstack.api.collections import CollectionEntry, CollectionRegistry
from ragstack.api.lifecycle import LifecycleGate, reset_lifecycle_gate, set_lifecycle_gate
from ragstack.api.routers.query import _resolve_retrieval, _retrieve_fused
from ragstack.api.security import Principal
from ragstack.collection_store import DORMANT, CollectionSpec, InMemoryCollectionStore
from ragstack.config import settings
from ragstack.models import Chunk, ScoredChunk, Triple
from ragstack.retrieval.retriever import (
    STAMP_KEY,
    CollectionLeg,
    HybridRetriever,
    MultiCollectionRetriever,
)
from ragstack.scoring.scorers import RRFScorer

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- #
# recording fakes
# --------------------------------------------------------------------------- #


class RecordingVectorStore:
    """Returns its chunks in order; records every filter dict it is handed."""

    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks
        self.search_filters: list[Any] = []
        self.get_filters: list[Any] = []

    async def search(self, query_vector, top_k=5, filters=None):
        self.search_filters.append(filters)
        return [
            ScoredChunk(chunk=c, score=1.0 - i * 0.01, retrieval_method="vector")
            for i, c in enumerate(self.chunks[:top_k])
        ]

    async def get_chunks(self, chunk_ids, filters=None):
        self.get_filters.append(filters)
        by_id = {c.id: c for c in self.chunks}
        return [by_id[i] for i in chunk_ids if i in by_id]


class RecordingTextIndex:
    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks
        self.search_filters: list[Any] = []

    async def search(self, query, top_k=5, filters=None):
        self.search_filters.append(filters)
        return [
            ScoredChunk(chunk=c, score=2.0 - i * 0.01, retrieval_method="bm25")
            for i, c in enumerate(self.chunks[:top_k])
        ]


class SpyGraphStore:
    """Records every ``query_neighborhood`` call's collection scope."""

    def __init__(self, triples: list[Triple]) -> None:
        self.triples = triples
        self.calls: list[Any] = []

    async def query_neighborhood(self, entity, depth=1, tenant_id=None, collection=None):
        self.calls.append(collection)
        wanted = {collection} if isinstance(collection, str) else set(collection or [])
        return [t for t in self.triples if not wanted or t.collection in wanted]


class _Embedder:
    async def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


def chunks(prefix: str, n: int, tenant: str = "public") -> list[Chunk]:
    return [
        Chunk(id=f"{prefix}-{i}", doc_id=prefix, content=f"{prefix} passage {i}",
              metadata={"tenant_id": tenant})
        for i in range(n)
    ]


class Fixture:
    """One collection: its stores, its retriever, its registry entry."""

    def __init__(self, cid: str, vec: list[Chunk], txt: list[Chunk] | None = None,
                 graph_store: Any = None) -> None:
        self.id = cid
        self.physical = f"phys-{cid}"
        self.vector_store = RecordingVectorStore(vec)
        self.text_index = RecordingTextIndex(txt if txt is not None else vec)
        self.retriever = HybridRetriever(
            self.vector_store, self.text_index, _Embedder(),
            graph_store=graph_store, collection=self.physical,
        )

    @property
    def calls(self) -> int:
        return (
            len(self.vector_store.search_filters)
            + len(self.text_index.search_filters)
            + len(self.vector_store.get_filters)
        )

    def leg(self, extra: list[str] | None = None) -> CollectionLeg:
        return CollectionLeg(
            id=self.id, retriever=self.retriever, physical=self.physical,
            vector_store=self.vector_store, extra_tenants=list(extra or []),
        )

    def entry(self, shared_surface: bool = False) -> CollectionEntry:
        return CollectionEntry(
            id=self.id, label=self.id, collection=self.physical, model="m", dim=2,
            chunk_method="fixed", chunk_size=None, chunk_overlap=None, chunk_params={},
            is_shared_surface=shared_surface, retriever=self.retriever,
            vector_store=self.vector_store, text_index=self.text_index,
        )


def ranked(scored: list[ScoredChunk]) -> list[tuple[str | None, str, float]]:
    return [(s.collection, s.chunk.id, round(s.score, 12)) for s in scored]


def stamped(scored: list[ScoredChunk], cid: str) -> list[ScoredChunk]:
    return [s.model_copy(update={"collection": cid}) for s in scored]


# --------------------------------------------------------------------------- #
# fusion
# --------------------------------------------------------------------------- #


async def test_two_legs_fuse_to_rrf_over_the_two_ranked_lists():
    a, b = Fixture("A", chunks("a", 4)), Fixture("B", chunks("b", 3))
    wrapper = MultiCollectionRetriever([a.leg(), b.leg()], rrf_scorer=RRFScorer())

    fused = await wrapper.retrieve("q", top_k=3, tenant_id="alice", use_graph=False)

    # Each leg alone, at the same depth, then plain RRF over the two lists —
    # with the collection stamps that make the two lists' identities disjoint.
    leg_a = await a.retriever.retrieve("q", top_k=3, tenant_id="alice", use_graph=False)
    leg_b = await b.retriever.retrieve("q", top_k=3, tenant_id="alice", use_graph=False)
    expected = RRFScorer().fuse([stamped(leg_a, "A"), stamped(leg_b, "B")])
    assert ranked(fused) == ranked(expected)
    # The union, not a cut to top_k: the caller reranks over it and cuts after.
    assert len(fused) == 6
    assert {s.collection for s in fused} == {"A", "B"}


async def test_document_in_both_collections_appears_once_per_collection():
    shared = Chunk(id="shared-0", doc_id="shared", content="the same passage",
                   metadata={"tenant_id": "public"})
    a = Fixture("A", [shared] + chunks("a", 2))
    b = Fixture("B", [shared.model_copy()] + chunks("b", 2))
    wrapper = MultiCollectionRetriever([a.leg(), b.leg()])

    fused = await wrapper.retrieve("q", top_k=3, tenant_id="alice", use_graph=False)

    copies = [s for s in fused if s.chunk.id == "shared-0"]
    assert sorted(c.collection for c in copies) == ["A", "B"]
    # Neither copy's score absorbed the other's rank: each is rank 1 in its own
    # leg, exactly the single-list RRF contribution — not double.
    k = wrapper.rrf.k
    assert all(abs(c.score - 1.0 / (k + 1)) < 1e-12 for c in copies)
    # The chunk object itself is untouched — no stamp leaked into metadata.
    assert "collection" not in shared.metadata


async def test_single_element_is_the_leg_itself_plus_the_stamp():
    spy = SpyGraphStore([
        Triple(subject="Alice", predicate="knows", object="Bob", doc_id="d",
               tenant_id="public", collection="phys-A"),
    ])
    a = Fixture("A", chunks("a", 3), graph_store=spy)
    wrapper = MultiCollectionRetriever([a.leg()], graph_store=spy)

    fused = await wrapper.retrieve("Alice", top_k=5, tenant_id="alice", use_graph=True)
    alone = await a.retriever.retrieve("Alice", top_k=5, tenant_id="alice", use_graph=True)

    assert ranked(fused) == ranked(stamped(alone, "A"))
    # Graph leg included and, with one member, scoped by the single name —
    # the store's single-value predicate, byte-identical to the singular path.
    assert spy.calls == ["phys-A", "phys-A"]
    assert any(s.chunk.id.startswith("graph-") for s in alone)  # graph leg fused in


async def test_graph_leg_is_one_neighbourhood_query_with_collection_in_list():
    spy = SpyGraphStore([
        Triple(subject="Alice", predicate="knows", object="Bob", doc_id="d",
               tenant_id="public", collection="phys-A"),
        Triple(subject="Alice", predicate="likes", object="Eve", doc_id="d",
               tenant_id="public", collection="phys-B"),
        Triple(subject="Alice", predicate="hates", object="Mallory", doc_id="d",
               tenant_id="public", collection="phys-C"),  # not a member
    ])
    a = Fixture("A", chunks("a", 2), graph_store=spy)
    b = Fixture("B", chunks("b", 2), graph_store=spy)
    wrapper = MultiCollectionRetriever([a.leg(), b.leg()], graph_store=spy)

    fused = await wrapper.retrieve("Alice", top_k=5, tenant_id="alice", use_graph=True)

    # ONE query, with the members' physical names as a list — the legs
    # themselves ran without their own graph leg.
    assert spy.calls == [["phys-A", "phys-B"]]
    graph = {s.chunk.id: s.collection for s in fused if s.chunk.id.startswith("graph-")}
    assert graph == {"graph-Alice-knows-Bob": "A", "graph-Alice-likes-Eve": "B"}


async def test_use_graph_false_makes_no_graph_query():
    spy = SpyGraphStore([])
    a, b = Fixture("A", chunks("a", 2), graph_store=spy), Fixture("B", chunks("b", 2), graph_store=spy)
    wrapper = MultiCollectionRetriever([a.leg(), b.leg()], graph_store=spy)
    await wrapper.retrieve("q", top_k=2, tenant_id="alice", use_graph=False)
    assert spy.calls == []


# --------------------------------------------------------------------------- #
# never a many-valued store filter
# --------------------------------------------------------------------------- #


def _assert_single_collection_scoped(filters: Any, expected_tenants: list[str]) -> None:
    assert isinstance(filters, dict)
    # The only list-valued key is the tenant scope (own + public [+ this
    # collection's share widening]); no key names a collection at all — the
    # collection IS the store the call went to.
    assert filters["tenant_id"] == expected_tenants
    assert not any("collection" in k for k in filters)
    assert [k for k, v in filters.items() if isinstance(v, list | tuple | set)] == ["tenant_id"]


async def test_every_store_call_carries_exactly_one_collection_scope():
    a, b = Fixture("A", chunks("a", 3)), Fixture("B", chunks("b", 3))
    # B is reached through a share: its leg widens to the owner's tenant.
    wrapper = MultiCollectionRetriever([a.leg(), b.leg(extra=["bob"])])

    await wrapper.retrieve(
        "q", top_k=3, filters={"journal": "mBio"}, tenant_id="alice", use_graph=False
    )

    for f in a.vector_store.search_filters + a.text_index.search_filters:
        _assert_single_collection_scoped(f, ["alice", "public"])
        assert f["journal"] == "mBio"
    for f in b.vector_store.search_filters + b.text_index.search_filters:
        _assert_single_collection_scoped(f, ["alice", "public", "bob"])
    # One search per store per leg — no cross-leg call, nothing fanned out
    # through a single store.
    assert a.calls == 2 and b.calls == 2


async def test_rerank_runs_once_over_the_union_and_keeps_the_stamps():
    a, b = Fixture("A", chunks("a", 3)), Fixture("B", chunks("b", 3))
    wrapper = MultiCollectionRetriever([a.leg(), b.leg()])

    class CountingReranker:
        calls: list[int] = []

        async def score(self, query, candidates, top_k=None):
            self.calls.append(len(candidates))
            # Reverse the pool: the stamps must follow the chunks, not positions.
            out = [ScoredChunk(chunk=c, score=float(i), retrieval_method="reranked")
                   for i, c in enumerate(candidates)]
            out.reverse()
            return out if top_k is None else out[:top_k]

    reranker = CountingReranker()
    scored = await _retrieve_fused(
        wrapper, reranker, "q", ["q"], 2, {}, False, rerank=True, rerank_candidates=4,
        tenant_id="alice",
    )
    # One call, over the fused union CUT to the rerank pool (rerank_candidates
    # = 4, not 2 legs × depth 4 = 8): the pool is the cost the caller budgets.
    assert reranker.calls == [4]
    assert len(scored) == 2
    assert all(s.retrieval_method == "reranked" for s in scored)
    for s in scored:
        assert s.collection == ("A" if s.chunk.id.startswith("a-") else "B")
        assert STAMP_KEY not in s.chunk.metadata or s.chunk.metadata[STAMP_KEY] == s.collection


async def test_stamp_survives_a_reranker_that_copies_its_chunks():
    """The ``Scorer`` protocol does not promise the same chunk objects back. A
    document present in BOTH collections is the hard case: by chunk id alone
    the two copies are indistinguishable, so the stamp has to ride on each
    copy itself."""
    shared = Chunk(id="shared-0", doc_id="shared", content="the same passage",
                   metadata={"tenant_id": "public"})
    a = Fixture("A", [shared] + chunks("a", 1))
    b = Fixture("B", [shared.model_copy()] + chunks("b", 1))
    wrapper = MultiCollectionRetriever([a.leg(), b.leg()])

    class CopyingReranker:
        async def score(self, query, candidates, top_k=None):
            out = [ScoredChunk(chunk=c.model_copy(), score=float(len(candidates) - i),
                               retrieval_method="reranked") for i, c in enumerate(candidates)]
            return out if top_k is None else out[:top_k]

    scored = await _retrieve_fused(
        wrapper, CopyingReranker(), "q", ["q"], 4, {}, False, rerank=True,
        rerank_candidates=4, tenant_id="alice",
    )
    copies = sorted(s.collection for s in scored if s.chunk.id == "shared-0")
    assert copies == ["A", "B"]
    assert all(s.collection in {"A", "B"} for s in scored)
    # The store's own chunk was never stamped — only the per-leg copies.
    assert STAMP_KEY not in shared.metadata


async def test_union_is_not_cut_without_a_reranker():
    a, b = Fixture("A", chunks("a", 3)), Fixture("B", chunks("b", 3))
    wrapper = MultiCollectionRetriever([a.leg(), b.leg()])
    scored = await _retrieve_fused(wrapper, None, "q", ["q"], 5, {}, False, tenant_id="alice")
    assert len(scored) == 5  # the final top_k cut only; both collections present
    assert {s.collection for s in scored} == {"A", "B"}


# --------------------------------------------------------------------------- #
# resolution before any leg runs (router seam)
# --------------------------------------------------------------------------- #


@pytest.fixture
def acl():
    store = InMemoryAclStore()
    set_acl_store(store)
    yield store
    reset_acl_store()


@pytest.fixture
def unrestricted(monkeypatch):
    monkeypatch.setattr(settings, "tenant_collections", {})


def _registry(*fixtures: Fixture) -> CollectionRegistry:
    entries = [f.entry(shared_surface=(i == 0)) for i, f in enumerate(fixtures)]
    return CollectionRegistry(entries, default_id=fixtures[0].id)


async def test_unreadable_member_refuses_the_whole_request_before_any_leg(
    acl, unrestricted, monkeypatch
):
    monkeypatch.setattr(settings, "api_keys", ["k"])  # auth configured → seam active
    a, b = Fixture("A", chunks("a", 3)), Fixture("B", chunks("b", 3))
    registry = _registry(a, b)
    await acl.grant("A", GRANTEE_GROUP, PUBLIC_GROUP, PERM_READ, granted_by="system")
    await acl.grant("B", GRANTEE_USER, "bob", PERM_OWNER, granted_by="bob")  # private to bob
    alice = Principal(tenant="alice", role="user")

    # The status the SINGLE-collection path gives alice for B (the read seam's
    # leak-safe 404 — "unreadable" is indistinguishable from "unknown").
    with pytest.raises(HTTPException) as single:
        await _resolve_retrieval(registry, "B", None, alice, "alice", {})

    for order in (["A", "B"], ["B", "A"]):
        with pytest.raises(HTTPException) as multi:
            await _resolve_retrieval(registry, None, order, alice, "alice", {})
        assert multi.value.status_code == single.value.status_code == 404
    # Nothing ran — not even the readable member's leg.
    assert a.calls == 0 and b.calls == 0


async def test_unknown_member_is_404_and_nothing_runs(acl, unrestricted):
    a = Fixture("A", chunks("a", 3))
    with pytest.raises(HTTPException) as ei:
        await _resolve_retrieval(_registry(a), None, ["A", "nope"], Principal("alice", "user"), "alice", {})
    assert ei.value.status_code == 404
    assert a.calls == 0


async def test_dormant_member_is_503_with_retry_after_and_nothing_runs(
    acl, unrestricted, monkeypatch
):
    monkeypatch.setattr(settings, "api_keys", [])  # keyless: no user token to restore with
    a, b = Fixture("A", chunks("a", 3)), Fixture("B", chunks("b", 3))
    registry = _registry(a, b)
    store = InMemoryCollectionStore()
    await store.put(CollectionSpec(
        id="B", label="B", collection="phys-B", embedding_api="openai",
        embedding_model="m", embedding_model_dim=2, chunk_method="fixed",
    ))
    await store.set_state("B", DORMANT, reason="evicted")
    gate = LifecycleGate(store, retry_after=42)
    set_lifecycle_gate(gate)
    try:
        with pytest.raises(HTTPException) as ei:
            await _resolve_retrieval(
                registry, None, ["A", "B"], Principal("alice", "user"), "alice", {}
            )
        assert ei.value.status_code == 503
        assert ei.value.headers["Retry-After"] == "42"
        assert "dormant" in ei.value.detail
        # The whole request is refused: no leg ran — the readable, active A included.
        assert a.calls == 0 and b.calls == 0
        # Without a user token nothing was submitted; the row stays dormant.
        assert (await store.get("B")).state == DORMANT
        await gate.drain()
    finally:
        reset_lifecycle_gate()


async def test_all_members_readable_builds_one_leg_per_member(acl, unrestricted):
    a, b = Fixture("A", chunks("a", 3)), Fixture("B", chunks("b", 3))
    retriever, filters, targets = await _resolve_retrieval(
        _registry(a, b), None, ["A", "B"], Principal("alice", "user"), "alice", {"x": 1}
    )
    assert isinstance(retriever, MultiCollectionRetriever)
    assert retriever.collections == ["A", "B"]
    assert filters == {"x": 1}  # unscoped: the wrapper scopes per leg
    assert set(targets) == {"A", "B"}
    assert targets["A"][0] is a.vector_store and targets["B"][0] is b.vector_store
    assert targets["A"][1]["tenant_id"] == ["alice", "public"]
    assert a.calls == 0 and b.calls == 0  # resolution runs nothing


async def test_two_ids_resolving_to_the_same_entry_is_422(acl, unrestricted):
    a = Fixture("A", chunks("a", 3))
    registry = _registry(a)

    class Aliasing(CollectionRegistry):
        def resolve(self, cid):
            return super().resolve("A" if cid == "alias" else cid)

    registry = Aliasing([a.entry(True)], default_id="A")
    with pytest.raises(HTTPException) as ei:
        await _resolve_retrieval(registry, None, ["A", "alias"], Principal("alice", "user"), "alice", {})
    assert ei.value.status_code == 422
    assert a.calls == 0


async def test_singular_path_is_unchanged(acl, unrestricted):
    a = Fixture("A", chunks("a", 3))
    retriever, filters, targets = await _resolve_retrieval(
        _registry(a), None, None, Principal("alice", "user"), "alice", {}
    )
    assert retriever is a.retriever
    assert filters == {"tenant_id": ["alice", "public"]}
    assert targets == {None: (a.vector_store, filters)}
