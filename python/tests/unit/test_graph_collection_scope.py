"""The knowledge-graph leg is scoped by collection as well as by tenant (#209).

Vector and text stores get collection isolation for free — each collection has
its own Qdrant collection and ES index — but a single graph store serves every
collection (Neo4j Community has one database), so the boundary has to live in the
triple data. These tests pin that down on all three paths:

* **read** — a caller on collection ``x`` never sees triples derived from ``y``,
  neither through the store nor through ``HybridRetriever``'s graph leg;
* **write** — the ingest pipeline stamps its target collection onto every triple;
* **delete** — delete-prior for a doc in ``x`` leaves ``y``'s triples for the same
  ``doc_id`` alone.

and on both implementations: the in-memory store behaviourally, the Neo4j store
through its generated Cypher (the driver is the optional ``graph`` extra, so
these use the same fake-driver harness as ``test_neo4j_store.py``). A fix that
only covered the in-memory store would ship green with no real coverage.
"""
from __future__ import annotations

import inspect
import sys
import types

import pytest

from ragstack.ingestion.chunkers import RecursiveCharacterChunker
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.models import Chunk, Document, Triple
from ragstack.protocols import GraphStore
from ragstack.retrieval.retriever import HybridRetriever
from ragstack.stores import InMemoryGraphStore, InMemoryTextIndex, InMemoryVectorStore

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- #
# Fixtures / doubles
# --------------------------------------------------------------------------- #

X = "corpus_x"
Y = "corpus_y"


def _triple(subject: str, obj: str, *, collection: str, tenant: str = "public",
            doc_id: str = "d1") -> Triple:
    return Triple(subject=subject, predicate="knows", object=obj,
                  doc_id=doc_id, tenant_id=tenant, collection=collection)


async def _two_collection_store() -> InMemoryGraphStore:
    """One graph store holding two collections' triples — the real deployment
    shape, and the reason the boundary can't be an object boundary."""
    store = InMemoryGraphStore()
    await store.add_triples([
        _triple("Alice", "Bob", collection=X),
        _triple("Alice", "Eve", collection=Y),
    ])
    return store


class _FakeEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t)), 1.0] for t in texts]


class _FixedDocLoader:
    def __init__(self, doc_id: str, content: str) -> None:
        self.doc_id, self.content = doc_id, content

    def load(self, source: str) -> list[Document]:
        return [Document(id=self.doc_id, content=self.content, source=source)]


class _StubExtractor:
    """Emits one triple per ingest with neither stamp set — the pipeline must
    supply both the tenant and the collection."""

    async def extract(self, chunks: list[Chunk]) -> list[Triple]:
        doc_id = chunks[0].doc_id if chunks else ""
        return [Triple(subject="Alice", predicate="knows", object="Bob", doc_id=doc_id)]


class _LeakyGraphStore:
    """A store that ignores the collection scope entirely — stands in for a
    backend whose filter is missing or wrong, so the assertions exercise the
    retriever's own re-check rather than the store's."""

    def __init__(self, triples: list[Triple]) -> None:
        self._triples = triples
        self.collection = "unset"

    async def query_neighborhood(self, entity, depth=1, tenant_id=None, collection=None):
        self.collection = collection
        return list(self._triples)


# --------------------------------------------------------------------------- #
# In-memory store: read / delete
# --------------------------------------------------------------------------- #

async def test_in_memory_neighborhood_is_collection_scoped():
    store = await _two_collection_store()

    in_x = await store.query_neighborhood("Alice", tenant_id="public", collection=X)
    assert {t.object for t in in_x} == {"Bob"}

    in_y = await store.query_neighborhood("Alice", tenant_id="public", collection=Y)
    assert {t.object for t in in_y} == {"Eve"}

    # Unscoped (dev/tests/admin) still spans both.
    everything = await store.query_neighborhood("Alice", tenant_id="public")
    assert {t.object for t in everything} == {"Bob", "Eve"}


async def test_in_memory_multi_hop_cannot_tunnel_through_another_collection():
    """Depth > 1 must not use an out-of-collection edge as a bridge: every hop is
    re-filtered, mirroring the Cypher path clause."""
    store = InMemoryGraphStore()
    await store.add_triples([
        _triple("Alice", "Bob", collection=X),
        _triple("Bob", "Carol", collection=Y),   # the bridge — wrong collection
        _triple("Carol", "Dave", collection=X),
    ])
    reachable = await store.query_neighborhood(
        "Alice", depth=3, tenant_id="public", collection=X
    )
    assert {t.object for t in reachable} == {"Bob"}
    assert "Dave" not in {t.object for t in reachable}


async def test_in_memory_list_entities_and_stats_are_collection_scoped():
    store = await _two_collection_store()

    assert {n for n, _ in await store.list_entities(tenant_id="public", collection=X)} == {
        "Alice", "Bob"
    }
    assert await store.stats(tenant_id="public", collection=X) == (2, 1)
    assert await store.stats(tenant_id="public", collection=Y) == (2, 1)
    assert await store.stats(tenant_id="public") == (3, 2)  # unscoped spans both


async def test_in_memory_delete_by_doc_is_collection_scoped():
    """The same doc_id ingested into two collections keeps a triple set per
    collection; deleting in one must not take the other's."""
    store = InMemoryGraphStore()
    await store.add_triples([
        _triple("Alice", "Bob", collection=X, doc_id="shared"),
        _triple("Alice", "Eve", collection=Y, doc_id="shared"),
    ])
    await store.delete_by_doc("shared", tenant_id="public", collection=X)

    survivors = await store.query_neighborhood("Alice", tenant_id="public")
    assert [(t.object, t.collection) for t in survivors] == [("Eve", Y)]


async def test_in_memory_same_triple_in_two_collections_both_survive():
    """Dedup keys on (s, p, o, tenant, collection) — matching Neo4j's MERGE key —
    so an identical triple derived in two collections is two rows, not one."""
    store = InMemoryGraphStore()
    await store.add_triples([
        _triple("Alice", "Bob", collection=X),
        _triple("Alice", "Bob", collection=Y),
    ])
    assert len(store._triples) == 2
    assert len(await store.query_neighborhood("Alice", tenant_id="public", collection=X)) == 1


async def test_in_memory_unstamped_legacy_triple_fails_closed():
    """Triples written before #209 carry no collection. A collection-scoped read
    must not guess which corpus they belong to — they are invisible until a
    re-ingest re-derives them (they stay reachable to unscoped/admin reads)."""
    store = InMemoryGraphStore()
    await store.add_triples([
        Triple(subject="Alice", predicate="knows", object="Bob", tenant_id="public"),
    ])
    assert await store.query_neighborhood("Alice", tenant_id="public", collection=X) == []
    assert await store.query_neighborhood("Alice", tenant_id="public", collection=Y) == []
    # Still there — an unscoped/admin read is how an operator finds the orphans.
    assert len(await store.query_neighborhood("Alice", tenant_id="public")) == 1


# --------------------------------------------------------------------------- #
# Retriever: the reported failure
# --------------------------------------------------------------------------- #

async def test_query_on_collection_x_gets_no_graph_context_from_collection_y():
    """The issue's scenario: a doc ingested into ``y`` produced triples; a caller
    confined to ``x`` must not have them fused into its ranked list."""
    graph = await _two_collection_store()
    retriever = HybridRetriever(
        InMemoryVectorStore(), InMemoryTextIndex(), _FakeEmbedder(),
        graph_store=graph, collection=X,
    )

    fused = await retriever.retrieve("Alice", top_k=10, use_graph=True, tenant_id="public")

    contents = {r.chunk.content for r in fused}
    assert "Alice knows Bob" in contents      # x's own triple still fuses
    assert "Alice knows Eve" not in contents  # y's does not


async def test_graph_leg_rechecks_collection_against_a_leaky_store():
    """Defence in depth, exactly as the tenant re-check works: the leg must not
    depend on one store implementation getting the filter right."""
    leaky = _LeakyGraphStore([
        _triple("Alice", "Bob", collection=X),
        _triple("Alice", "Eve", collection=Y),
        Triple(subject="Alice", predicate="knows", object="Legacy", tenant_id="public"),
    ])
    retriever = HybridRetriever(
        InMemoryVectorStore(), InMemoryTextIndex(), _FakeEmbedder(),
        graph_store=leaky, collection=X,
    )

    fused = await retriever.retrieve("Alice", top_k=10, use_graph=True, tenant_id="public")

    assert leaky.collection == X  # the scope was pushed down to the store too
    assert {r.chunk.content for r in fused} == {"Alice knows Bob"}


async def test_graph_pseudo_chunks_carry_the_collection_stamp():
    """#207 gave graph pseudo-chunks a tenant stamp so a post-retrieval re-check
    could evaluate them per chunk; the collection stamp sits alongside it so that
    re-check has collection provenance too. The tenant stamp must survive."""
    graph = await _two_collection_store()
    retriever = HybridRetriever(
        InMemoryVectorStore(), InMemoryTextIndex(), _FakeEmbedder(),
        graph_store=graph, collection=X,
    )

    fused = await retriever.retrieve("Alice", top_k=10, use_graph=True, tenant_id="public")

    assert fused
    for result in fused:
        assert result.chunk.metadata["collection"] == X
        assert result.chunk.metadata["tenant_id"] == "public"


async def test_retriever_without_a_collection_stays_unscoped():
    """Library/dev callers that build a retriever directly keep today's behaviour
    (``collection=None`` = no collection constraint), so this fix doesn't force a
    stamp on code paths that have no collection identity."""
    graph = await _two_collection_store()
    retriever = HybridRetriever(
        InMemoryVectorStore(), InMemoryTextIndex(), _FakeEmbedder(), graph_store=graph
    )

    fused = await retriever.retrieve("Alice", top_k=10, use_graph=True, tenant_id="public")

    assert {r.chunk.content for r in fused} == {"Alice knows Bob", "Alice knows Eve"}


# --------------------------------------------------------------------------- #
# Ingest pipeline: write + delete-prior
# --------------------------------------------------------------------------- #

def _pipeline(graph_store, *, collection, doc_id="doc-1", content="abcdefghijklmnop"):
    return IngestionPipeline(
        loader=_FixedDocLoader(doc_id, content),
        chunker=RecursiveCharacterChunker(chunk_size=10, chunk_overlap=0),
        embedder=_FakeEmbedder(),
        vector_store=InMemoryVectorStore(),
        text_index=InMemoryTextIndex(),
        graph_store=graph_store,
        kg_extractor=_StubExtractor(),
        collection=collection,
    )


async def test_ingest_stamps_the_target_collection_on_triples():
    graph = InMemoryGraphStore()
    await _pipeline(graph, collection=X).ingest("file.txt", tenant_id="alice")

    assert [(t.tenant_id, t.collection) for t in graph._triples] == [("alice", X)]


async def test_reingest_into_one_collection_keeps_the_others_triples():
    """Delete-prior runs per replaced doc_id across vector+text+graph. Collection-
    blind, it would wipe the other collection's triples for the same doc_id — the
    'read-side filter with a collection-blind delete just moves the bug' case."""
    graph = InMemoryGraphStore()
    await _pipeline(graph, collection=X).ingest("file.txt", tenant_id="alice")
    await _pipeline(graph, collection=Y).ingest("file.txt", tenant_id="alice")
    assert len(graph._triples) == 2

    # Re-ingest the same document into x only.
    await _pipeline(graph, collection=X, content="zyxwvutsrq").ingest(
        "file.txt", tenant_id="alice"
    )

    assert sorted(t.collection for t in graph._triples) == [X, Y]


# --------------------------------------------------------------------------- #
# Neo4j store: the same contract, in Cypher
# --------------------------------------------------------------------------- #

class _FakeResult:
    def __init__(self, records: list[dict]):
        self._records = records

    def __aiter__(self):
        async def gen():
            for r in self._records:
                yield r
        return gen()

    async def single(self):
        return self._records[0] if self._records else None


class _FakeSession:
    def __init__(self, driver: _FakeDriver):
        self._driver = driver

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def run(self, query, **params):
        self._driver.calls.append((query, params))
        if self._driver.results:
            return _FakeResult(self._driver.results.pop(0))
        return _FakeResult([])


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
    """A Neo4jGraphStore over a fake driver that records every Cypher statement."""
    mod = types.ModuleType("neo4j")

    class _FakeGraphDatabase:
        @staticmethod
        def driver(uri, auth=None):
            return _FakeDriver()

    mod.AsyncGraphDatabase = _FakeGraphDatabase
    monkeypatch.setitem(sys.modules, "neo4j", mod)
    from ragstack.stores.neo4j import Neo4jGraphStore

    return Neo4jGraphStore(uri="bolt://x", user="neo4j", password="ragstack")


def _last(store) -> tuple[str, dict]:
    return store._driver.calls[-1]


async def test_neo4j_neighborhood_filters_and_anchors_on_collection(neo4j_store):
    await neo4j_store.query_neighborhood("Alice", depth=2, tenant_id="alice", collection=X)
    query, params = _last(neo4j_store)

    assert params["collection"] == X
    # Anchor, traversal and returned-edge filter all carry the collection: without
    # the path clause a depth>1 walk could bridge through another collection's edge.
    assert "start.collection = $collection" in query
    assert "rel.collection = $collection" in query
    assert "r.collection = $collection" in query
    assert "rel.tenant_id IN $tenants" in query  # #207's tenant scoping preserved
    assert "r.collection AS collection" in query  # stamp round-trips to the Triple


async def test_neo4j_reads_stay_unscoped_without_a_collection(neo4j_store):
    await neo4j_store.query_neighborhood("Alice", tenant_id="alice")
    query, params = _last(neo4j_store)
    assert "collection" not in params
    assert "$collection" not in query


async def test_neo4j_list_entities_and_stats_filter_on_collection(neo4j_store):
    await neo4j_store.list_entities(tenant_id="alice", collection=X)
    query, params = _last(neo4j_store)
    assert "r.collection = $collection" in query
    assert params["collection"] == X

    neo4j_store._driver.results.append([{"entities": 2, "relationships": 1}])
    assert await neo4j_store.stats(tenant_id="alice", collection=X) == (2, 1)
    query, params = _last(neo4j_store)
    assert "e.collection = $collection" in query
    assert "r.collection = $collection" in query
    assert "e.tenant_id IN $tenants" in query
    assert params["collection"] == X


async def test_neo4j_delete_by_doc_is_collection_scoped(neo4j_store):
    await neo4j_store.delete_by_doc("d1", tenant_id="alice", collection=X)
    query, params = _last(neo4j_store)
    assert "r.doc_id = $doc_id" in query
    assert "r.tenant_id = $tenant_id" in query
    assert "r.collection = $collection" in query
    assert params == {"doc_id": "d1", "tenant_id": "alice", "collection": X}


async def test_neo4j_triples_round_trip_the_collection_stamp(neo4j_store):
    neo4j_store._driver.results.append([
        {"subject": "Alice", "predicate": "knows", "object": "Bob",
         "doc_id": "d1", "tenant_id": "alice", "collection": X},
    ])
    triples = await neo4j_store.query_neighborhood("Alice", tenant_id="alice", collection=X)
    assert [t.collection for t in triples] == [X]


# --------------------------------------------------------------------------- #
# The two stores agree
# --------------------------------------------------------------------------- #

async def test_both_stores_satisfy_the_same_scoped_protocol(neo4j_store):
    """Signature parity on every method that takes a scope. The unit suite runs on
    the in-memory store, so a Neo4j store that silently lacked the ``collection``
    parameter would ship untested — and would be called with it at runtime."""
    memory = InMemoryGraphStore()
    assert isinstance(memory, GraphStore)
    assert isinstance(neo4j_store, GraphStore)

    for name in ("query_neighborhood", "list_entities", "stats", "delete_by_doc"):
        mem_sig = inspect.signature(getattr(memory, name))
        neo_sig = inspect.signature(getattr(neo4j_store, name))
        assert "collection" in mem_sig.parameters, name
        assert list(mem_sig.parameters) == list(neo_sig.parameters), name
        # Both default to unscoped, so an un-migrated call site is not silently
        # narrowed to some arbitrary collection.
        assert mem_sig.parameters["collection"].default is None, name
        assert neo_sig.parameters["collection"].default is None, name
