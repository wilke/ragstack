"""Query-side entity extraction for the graph leg (#349).

Before this change ``HybridRetriever._graph_context`` handed the *raw query
string* to ``GraphStore.query_neighborhood``, whose match is "entity name
CONTAINS the term" — so a one-word query could fire the leg and a realistic
multi-word query never did (no entity name contains a whole sentence). Now the
query is tokenised, its 1–``graph_query_ngram_max``-grams are matched *exactly*
(case-folded) against the entity names in the caller's readable scope via
``GraphStore.match_entities``, and the leg is the union of ONE neighbourhood per
matched entity (longest match first, then query order, capped at
``graph_query_entity_max``). No entity → empty leg, no neighbourhood call.

The two goldens below were captured on ``main`` before the change (#347's
pattern): the single-word query's bytes must be unchanged, and the multi-word
query's ``[]`` is the defect this file was written to fail on first.
"""
from __future__ import annotations

import inspect
import json
import sys
import types

import pytest

from ragstack.config import settings
from ragstack.models import Triple
from ragstack.protocols import GraphStore
from ragstack.retrieval.retriever import GRAPH_QUERY_STOPWORDS, HybridRetriever, query_candidates
from ragstack.stores import InMemoryGraphStore, InMemoryTextIndex, InMemoryVectorStore

TENANT = "alice"
X = "corpus_x"
Y = "corpus_y"

# Captured on main (c4c3801) from ``_retriever(_store())`` with the vector and
# BM25 legs empty, i.e. exactly the graph leg's contribution — see ``_run``.
# Regenerate only if the retriever's fusion/shaping changes upstream, never to
# absorb a graph-leg change from this feature.
GOLDEN_SINGLE_WORD = (  # noqa: E501
    '[{"chunk": {"content": "Aspirin inhibits platelet aggregation", "doc_id": "d1", "embedding": null, "end_char": 0, "id": "graph-Aspirin-inhibits-platelet aggregation", "metadata": {"collection": "corpus_x", "tenant_id": "alice"}, "start_char": 0}, "retrieval_method": "hybrid", "score": 0.01639344262295082}, {"chunk": {"content": "Aspirin interacts_with Warfarin", "doc_id": "d1", "embedding": null, "end_char": 0, "id": "graph-Aspirin-interacts_with-Warfarin", "metadata": {"collection": "corpus_x", "tenant_id": "alice"}, "start_char": 0}, "retrieval_method": "hybrid", "score": 0.016129032258064516}, {"chunk": {"content": "Aspirin reduces myocardial infarction risk", "doc_id": "d2", "embedding": null, "end_char": 0, "id": "graph-Aspirin-reduces-myocardial infarction risk", "metadata": {"collection": "corpus_x", "tenant_id": "alice"}, "start_char": 0}, "retrieval_method": "hybrid", "score": 0.015873015873015872}]'
)
# The defect: on main the multi-word query produced an empty graph leg.
GOLDEN_MULTI_WORD_ON_MAIN = "[]"

MULTI_WORD_QUERY = "what does aspirin do to platelet aggregation"


class _FakeEmbedder:
    async def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


class _SpyStore(InMemoryGraphStore):
    """The real in-memory store, recording every read the retriever makes."""

    def __init__(self) -> None:
        super().__init__()
        self.match_calls: list[tuple[list[str], str | None, str | None]] = []
        self.neighborhood_calls: list[str] = []

    async def match_entities(self, candidates, *, tenant_id, collection):
        self.match_calls.append((list(candidates), tenant_id, collection))
        return await super().match_entities(candidates, tenant_id=tenant_id, collection=collection)

    async def query_neighborhood(self, entity, depth=1, tenant_id=None, collection=None):
        self.neighborhood_calls.append(entity)
        return await super().query_neighborhood(
            entity, depth=depth, tenant_id=tenant_id, collection=collection
        )


def _t(subject: str, predicate: str, obj: str, *, doc_id="d1", tenant_id=TENANT,
       collection=X) -> Triple:
    return Triple(subject=subject, predicate=predicate, object=obj, doc_id=doc_id,
                  tenant_id=tenant_id, collection=collection)


async def _store() -> _SpyStore:
    store = _SpyStore()
    await store.add_triples([
        _t("Aspirin", "inhibits", "platelet aggregation"),
        _t("Aspirin", "interacts_with", "Warfarin"),
        _t("Aspirin", "reduces", "myocardial infarction risk", doc_id="d2"),
        _t("Warfarin", "treats", "thrombosis"),
        _t("myocardial infarction risk", "raised_by", "smoking", doc_id="d2"),
        # Out of scope for a retriever bound to (alice, corpus_x):
        _t("Ibuprofen", "is_a", "NSAID", collection=Y),
        _t("Clopidogrel", "inhibits", "P2Y12", tenant_id="bob"),
    ])
    return store


def _retriever(store, *, collection=X, **kw) -> HybridRetriever:
    return HybridRetriever(
        InMemoryVectorStore(), InMemoryTextIndex(), _FakeEmbedder(),
        graph_store=store, collection=collection, **kw,
    )


async def _run(retriever: HybridRetriever, query: str) -> str:
    out = await retriever.retrieve(query, top_k=10, use_graph=True, tenant_id=TENANT)
    return json.dumps([json.loads(s.model_dump_json()) for s in out], sort_keys=True)


def _contents(results) -> set[str]:
    return {r.chunk.content for r in results}


# --------------------------------------------------------------------------- #
# The defect, and the single-word behaviour that must not change
# --------------------------------------------------------------------------- #

async def test_multi_word_query_containing_one_entity_returns_its_neighbourhood():
    """The failing-first case: on main this leg was ``[]``."""
    store = await _store()
    got = await _run(_retriever(store), MULTI_WORD_QUERY)
    assert got != GOLDEN_MULTI_WORD_ON_MAIN
    contents = {json.loads(got)[i]["chunk"]["content"] for i in range(len(json.loads(got)))}
    assert "Aspirin inhibits platelet aggregation" in contents
    assert "Aspirin interacts_with Warfarin" in contents
    # "platelet aggregation" is an entity too (2-gram) — its neighbourhood is
    # the same triple, which appears once (union, not concatenation).
    assert [c["chunk"]["content"] for c in json.loads(got)].count(
        "Aspirin inhibits platelet aggregation") == 1


async def test_single_word_query_is_byte_identical_to_main():
    store = await _store()
    assert await _run(_retriever(store), "aspirin") == GOLDEN_SINGLE_WORD


# --------------------------------------------------------------------------- #
# Matching rule
# --------------------------------------------------------------------------- #

async def test_two_entities_union_their_neighbourhoods():
    store = await _store()
    got = await _retriever(store).retrieve(
        "does warfarin interact with aspirin", top_k=10, use_graph=True, tenant_id=TENANT)
    assert _contents(got) == {
        "Aspirin inhibits platelet aggregation",
        "Aspirin interacts_with Warfarin",          # shared by both — once
        "Aspirin reduces myocardial infarction risk",
        "Warfarin treats thrombosis",
    }
    assert store.neighborhood_calls == ["warfarin", "aspirin"]  # query order, both 1-grams
    ids = [r.chunk.id for r in got]
    assert len(ids) == len(set(ids))


async def test_no_matching_entity_gives_empty_leg_and_no_neighbourhood_call():
    store = await _store()
    got = await _retriever(store).retrieve(
        "how tall is the eiffel tower", top_k=10, use_graph=True, tenant_id=TENANT)
    assert got == []
    assert len(store.match_calls) == 1          # one indexed lookup, in scope
    assert store.match_calls[0][1:] == (TENANT, X)
    assert store.neighborhood_calls == []       # never a CONTAINS over the sentence


@pytest.mark.parametrize("query", ["", "   ", "?!.,;", "—"])
async def test_query_with_no_tokens_makes_no_store_call_at_all(query):
    store = await _store()
    got = await _retriever(store).retrieve(query, top_k=10, use_graph=True, tenant_id=TENANT)
    assert got == []
    assert store.match_calls == []
    assert store.neighborhood_calls == []


async def test_matching_is_case_insensitive_and_ignores_punctuation():
    store = await _store()
    got = await _retriever(store).retrieve(
        "ASPIRIN, and WARFARIN?", top_k=10, use_graph=True, tenant_id=TENANT)
    assert "Warfarin treats thrombosis" in _contents(got)
    assert "Aspirin interacts_with Warfarin" in _contents(got)
    assert store.neighborhood_calls == ["aspirin", "warfarin"]


async def test_three_gram_entity_matches_as_one_and_ranks_first():
    store = await _store()
    got = await _retriever(store).retrieve(
        "does aspirin lower myocardial infarction risk", top_k=10, use_graph=True,
        tenant_id=TENANT)
    # Longest match first, then query order.
    assert store.neighborhood_calls == ["myocardial infarction risk", "aspirin"]
    assert "myocardial infarction risk raised_by smoking" in _contents(got)
    # The 3-gram's own neighbourhood leads the leg.
    assert got[0].chunk.content in {
        "Aspirin reduces myocardial infarction risk",
        "myocardial infarction risk raised_by smoking",
    }


async def test_cap_at_five_entities_longest_first_then_query_order():
    assert settings.graph_query_entity_max == 5
    store = _SpyStore()
    await store.add_triples([
        _t(name, "is_a", f"kind-{i}") for i, name in enumerate([
            "alpha", "beta", "gamma", "delta", "epsilon",          # five 1-grams
            "beta gamma", "gamma delta",                           # two 2-grams
            "alpha beta gamma",                                    # one 3-gram
        ])
    ])
    query = "alpha beta gamma delta epsilon"
    got = await _retriever(store).retrieve(query, top_k=50, use_graph=True, tenant_id=TENANT)
    # 8 entities match; only 5 neighbourhoods are fetched: the 3-gram, the two
    # 2-grams (query order), then the 1-grams in query order until the cap.
    assert store.neighborhood_calls == [
        "alpha beta gamma", "beta gamma", "gamma delta", "alpha", "beta"]
    assert len(store.neighborhood_calls) == settings.graph_query_entity_max
    assert "epsilon is_a kind-4" not in _contents(got)


async def test_entity_cap_and_ngram_max_are_configurable():
    store = _SpyStore()
    await store.add_triples([_t(n, "is_a", "x") for n in ["ax", "bx", "cx", "bx cx"]])
    r = _retriever(store, graph_query_entity_max=2, graph_query_ngram_max=1)
    await r.retrieve("ax bx cx", top_k=10, use_graph=True, tenant_id=TENANT)
    # ngram_max=1: "bx cx" is never a candidate; entity_max=2: only the first two.
    assert store.neighborhood_calls == ["ax", "bx"]


async def test_defaults_come_from_settings_at_query_time(monkeypatch):
    store = _SpyStore()
    await store.add_triples([_t(n, "is_a", "x") for n in ["ax", "bx", "cx"]])
    r = _retriever(store)
    assert r.graph_query_entity_max is None and r.graph_query_ngram_max is None
    monkeypatch.setattr(settings, "graph_query_entity_max", 1)
    await r.retrieve("ax bx cx", top_k=10, use_graph=True, tenant_id=TENANT)
    assert store.neighborhood_calls == ["ax"]


# --------------------------------------------------------------------------- #
# Scope
# --------------------------------------------------------------------------- #

async def test_entity_in_another_collection_or_tenant_does_not_match():
    store = await _store()
    got = await _retriever(store).retrieve(
        "ibuprofen or clopidogrel with aspirin", top_k=10, use_graph=True, tenant_id=TENANT)
    # Ibuprofen lives in corpus_y, Clopidogrel belongs to bob: neither is an
    # entity in (alice, corpus_x), so neither costs a neighbourhood call.
    assert store.neighborhood_calls == ["aspirin"]
    assert "Ibuprofen is_a NSAID" not in _contents(got)
    assert "Clopidogrel inhibits P2Y12" not in _contents(got)


async def test_unscoped_retriever_matches_across_collections():
    store = await _store()
    got = await _retriever(store, collection=None).retrieve(
        "ibuprofen with aspirin", top_k=10, use_graph=True, tenant_id=TENANT)
    assert store.neighborhood_calls == ["ibuprofen", "aspirin"]
    assert "Ibuprofen is_a NSAID" in _contents(got)


async def test_memory_store_match_entities_scope_and_folding():
    store = await _store()
    # Exact, case-folded, distinct, in candidate order; scoped on both axes.
    assert await store.match_entities(
        ["Warfarin", "warfarin", "aspirin", "ASPIRIN tablets", "ibuprofen", "clopidogrel"],
        tenant_id=TENANT, collection=X,
    ) == ["warfarin", "aspirin"]
    assert await store.match_entities(["ibuprofen"], tenant_id=TENANT, collection=Y) == ["ibuprofen"]
    assert await store.match_entities(["clopidogrel"], tenant_id="bob", collection=X) == ["clopidogrel"]
    assert await store.match_entities(["clopidogrel", "ibuprofen"], tenant_id=None, collection=None) == [
        "clopidogrel", "ibuprofen"]
    assert await store.match_entities([], tenant_id=TENANT, collection=X) == []


async def test_memory_store_match_entities_accepts_the_multi_collection_list():
    """The #253/#395 fan-out passes several physical collections as a list —
    the same ``collection IN [...]`` scope ``query_neighborhood`` takes."""
    store = await _store()
    assert await store.match_entities(
        ["ibuprofen", "aspirin", "clopidogrel"], tenant_id=TENANT, collection=[X, Y],
    ) == ["ibuprofen", "aspirin"]
    assert await store.match_entities(["aspirin"], tenant_id=TENANT, collection=[Y]) == []


async def test_memory_store_index_follows_deletes():
    store = await _store()
    await store.delete_by_doc("d2", tenant_id=TENANT, collection=X)
    # "smoking" only ever appeared in d2; "aspirin" is still in d1.
    assert await store.match_entities(
        ["smoking", "aspirin"], tenant_id=TENANT, collection=X) == ["aspirin"]


async def test_leg_still_rechecks_tenant_collection_and_confidence():
    """A store whose match/neighbourhood ignore the scope: the retriever's own
    re-checks (#207/#209) and the #347 floor still apply to the union."""

    class _Leaky:
        async def match_entities(self, candidates, *, tenant_id, collection):
            return ["aspirin"]

        async def query_neighborhood(self, entity, depth=1, tenant_id=None, collection=None):
            return [
                _t("Aspirin", "inhibits", "platelet aggregation"),
                _t("Aspirin", "is_in", "y", collection=Y),
                _t("Aspirin", "is_bobs", "secret", tenant_id="bob"),
                Triple(subject="Aspirin", predicate="verified", object="fact", doc_id="d1",
                       tenant_id=TENANT, collection=X, derived_by="tool:x", confidence=2),
            ]

    got = await _retriever(_Leaky(), graph_min_confidence=2).retrieve(
        "aspirin", top_k=10, use_graph=True, tenant_id=TENANT)
    assert _contents(got) == {"Aspirin verified fact"}


# --------------------------------------------------------------------------- #
# Candidate generation
# --------------------------------------------------------------------------- #

def test_query_candidates_ngrams_lowercased_deduped_in_order():
    cands = query_candidates("Does Aspirin, aspirin help?", 3)
    assert [c.text for c in cands] == [
        "aspirin", "help",                        # "does" is a stopword 1-gram
        "does aspirin", "aspirin aspirin", "aspirin help",
        "does aspirin aspirin", "aspirin aspirin help",
    ]
    by_text = {c.text: c for c in cands}
    assert (by_text["aspirin"].n_tokens, by_text["aspirin"].position) == (1, 1)  # first occurrence
    assert (by_text["aspirin help"].n_tokens, by_text["aspirin help"].position) == (2, 2)
    assert query_candidates("", 3) == []
    assert query_candidates("...", 3) == []
    assert [c.text for c in query_candidates("covid-19 e. coli", 2)] == [
        "covid-19", "e", "coli", "covid-19 e", "e coli"]


def test_stopwords_are_dropped_as_1_grams_but_kept_inside_longer_ngrams():
    texts = [c.text for c in query_candidates("the bank of england", 3)]
    assert "the" not in texts and "of" not in texts
    assert "bank of england" in texts and "bank of" in texts and "of england" in texts
    # No minimum length: 2-letter biomedical abbreviations are real entities,
    # and "no" (nitric oxide) is deliberately not a stopword.
    assert [c.text for c in query_candidates("MI TB IL no", 1)] == ["mi", "tb", "il", "no"]
    assert {"the", "of", "and", "is", "it"} <= GRAPH_QUERY_STOPWORDS
    assert not {"mi", "tb", "il", "no"} & GRAPH_QUERY_STOPWORDS


async def test_stopword_entities_in_scope_do_not_eat_cap_slots():
    """The coordinator's case: entities literally named "the" and "of" exist in
    scope. Without the filter they tie with "aspirin" on length, win on query
    order, and cost neighbourhood calls; with it only the real entity fires."""
    store = _SpyStore()
    await store.add_triples([
        _t("the", "is_a", "junk"), _t("of", "is_a", "junk"), _t("aspirin", "is_a", "NSAID"),
    ])
    got = await _retriever(store).retrieve(
        "the role of aspirin in the heart", top_k=10, use_graph=True, tenant_id=TENANT)
    assert store.neighborhood_calls == ["aspirin"]
    assert _contents(got) == {"aspirin is_a NSAID"}


# --------------------------------------------------------------------------- #
# Neo4j: the generated Cypher
# --------------------------------------------------------------------------- #

class _FakeResult:
    def __init__(self, records):
        self._records = records

    def __aiter__(self):
        async def gen():
            for r in self._records:
                yield r
        return gen()


class _FakeSession:
    def __init__(self, driver):
        self._driver = driver

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def run(self, query, **params):
        self._driver.calls.append((query, params))
        return _FakeResult(self._driver.results.pop(0) if self._driver.results else [])


class _FakeDriver:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.results: list[list[dict]] = []

    def session(self, **kwargs):
        return _FakeSession(self)

    async def close(self):
        pass


@pytest.fixture
def neo4j_store(monkeypatch):
    mod = types.ModuleType("neo4j")

    class _FakeGraphDatabase:
        @staticmethod
        def driver(uri, auth=None):
            return _FakeDriver()

    mod.AsyncGraphDatabase = _FakeGraphDatabase
    monkeypatch.setitem(sys.modules, "neo4j", mod)
    from ragstack.stores.neo4j import Neo4jGraphStore

    return Neo4jGraphStore(uri="bolt://x", user="neo4j", password="ragstack")


async def test_neo4j_match_entities_is_an_indexed_in_lookup_with_scope(neo4j_store):
    neo4j_store._driver.results.append([{"name": "aspirin"}, {"name": "platelet aggregation"}])
    got = await neo4j_store.match_entities(
        ["What", "Aspirin", "platelet aggregation", "aspirin"], tenant_id="alice", collection=X)
    assert got == ["aspirin", "platelet aggregation"]
    [(query, params)] = neo4j_store._driver.calls          # exactly one round trip
    assert "toLower(e.name) IN $candidates" in query
    assert "e.tenant_id IN $tenants" in query
    assert "e.collection = $collection" in query
    assert "CONTAINS" not in query
    assert "DISTINCT" in query
    assert params["candidates"] == ["what", "aspirin", "platelet aggregation"]  # folded, deduped
    assert params["tenants"] == ["alice", "public"]
    assert params["collection"] == X


async def test_neo4j_match_entities_unscoped_has_no_scope_predicates(neo4j_store):
    await neo4j_store.match_entities(["x"], tenant_id=None, collection=None)
    [(query, params)] = neo4j_store._driver.calls
    assert "$tenants" not in query and "$collection" not in query
    assert set(params) == {"candidates"}


async def test_neo4j_match_entities_with_a_collection_list_uses_in(neo4j_store):
    await neo4j_store.match_entities(["aspirin"], tenant_id="alice", collection=[X, Y])
    [(query, params)] = neo4j_store._driver.calls
    assert "e.collection IN $collections" in query
    assert params["collections"] == [X, Y]
    assert "CONTAINS" not in query


async def test_neo4j_match_entities_empty_candidates_is_no_round_trip(neo4j_store):
    assert await neo4j_store.match_entities([], tenant_id="alice", collection=X) == []
    assert neo4j_store._driver.calls == []


async def test_neo4j_neighbourhood_query_is_unchanged_by_this_feature(neo4j_store):
    """The neighbourhood walk still anchors on the (now exact-matched) entity
    name with the same Cypher — this feature only changes what is passed in."""
    await neo4j_store.query_neighborhood("aspirin", tenant_id="alice", collection=X)
    [(query, params)] = neo4j_store._driver.calls
    assert "toLower(start.name) CONTAINS $entity" in query
    assert params["entity"] == "aspirin"


async def test_both_stores_expose_the_same_match_entities_signature(neo4j_store):
    memory = InMemoryGraphStore()
    assert isinstance(memory, GraphStore) and isinstance(neo4j_store, GraphStore)
    mem_sig = inspect.signature(memory.match_entities)
    neo_sig = inspect.signature(neo4j_store.match_entities)
    assert list(mem_sig.parameters) == list(neo_sig.parameters) == [
        "candidates", "tenant_id", "collection"]
    for sig in (mem_sig, neo_sig):
        # Keyword-only and REQUIRED: a caller can't forget the scope.
        for name in ("tenant_id", "collection"):
            assert sig.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
            assert sig.parameters[name].default is inspect.Parameter.empty
