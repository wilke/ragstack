"""``GraphStore.delete_collection`` (#380) on both stores — the per-collection
delete that eviction, purge (#295) and tombstone replay use.

What must hold, on the in-memory store behaviourally and on the Neo4j store
through its generated Cypher (the same fake-driver harness as
``test_neo4j_store.py``; no live Neo4j):

* **scope** — only the named collection's triples go; another collection's
  triples for the same doc ids and the same entity names stay (#209). With a
  ``tenant_id`` only that tenant's triples in the collection go — exact
  equality, so the shared ``public`` corpus and a co-writer's triples stay;
  with ``tenant_id=None`` (the collection-wide form the store drops use) every
  tenant's triples in the collection go, and nothing outside it.
* **idempotent** — the count is the edges removed; a second call removes 0.
* **orphan sweep** — an entity whose only edges were in the deleted collection
  is gone; an entity of the same name in another collection is kept.
* **fail closed** — an empty collection is refused, never a wildcard.
"""
from __future__ import annotations

import inspect
import sys
import types

import pytest

from ragstack.models import Triple
from ragstack.protocols import GraphStore
from ragstack.stores import InMemoryGraphStore
from ragstack.stores.neo4j import Neo4jGraphStore

pytestmark = pytest.mark.asyncio

X = "corpus_x"
Y = "corpus_y"


def _t(subject: str, obj: str, *, collection: str, tenant: str = "alice",
       doc_id: str = "d1", predicate: str = "knows") -> Triple:
    return Triple(subject=subject, predicate=predicate, object=obj,
                  doc_id=doc_id, tenant_id=tenant, collection=collection)


def _seed() -> list[Triple]:
    """Two collections, two tenants + public, overlapping doc ids AND entity
    names, so every axis the delete must respect is represented."""
    return [
        # collection x: alice's, bob's and public triples
        _t("Alice", "Bob", collection=X, tenant="alice", doc_id="d1"),
        _t("Bob", "Carol", collection=X, tenant="alice", doc_id="d2"),
        _t("Dave", "Erin", collection=X, tenant="bob", doc_id="d1"),
        _t("Alice", "Zed", collection=X, tenant="public", doc_id="d9"),
        # collection y: same doc id, same entity names — a different corpus
        _t("Alice", "Bob", collection=Y, tenant="alice", doc_id="d1"),
        _t("Frank", "Alice", collection=Y, tenant="bob", doc_id="d3"),
    ]


@pytest.fixture
async def memory() -> InMemoryGraphStore:
    store = InMemoryGraphStore()
    await store.add_triples(_seed())
    return store


# --------------------------------------------------------------------------- #
# in-memory store: behaviour
# --------------------------------------------------------------------------- #


async def test_collection_wide_delete_takes_every_tenant_in_x_and_nothing_in_y(memory):
    n = await memory.delete_collection(None, X)
    assert n == 4  # alice ×2, bob ×1, public ×1
    assert await memory.stats(tenant_id=None, collection=X) == (0, 0)
    # y intact — including the same doc id and the same entity names.
    left = await memory.query_neighborhood("Alice", tenant_id=None, collection=Y)
    assert {(t.subject, t.object, t.doc_id) for t in left} == {("Alice", "Bob", "d1"),
                                                                ("Frank", "Alice", "d3")}
    assert await memory.stats(tenant_id=None, collection=Y) == (3, 2)


async def test_tenant_scoped_delete_spares_the_other_tenant_and_public_in_the_same_collection(memory):
    n = await memory.delete_collection("alice", X)
    assert n == 2
    remaining = await memory.query_neighborhood("", tenant_id=None, collection=X)
    assert {(t.tenant_id, t.subject, t.object) for t in remaining} == {
        ("bob", "Dave", "Erin"),        # a co-writer's triple, untouched
        ("public", "Alice", "Zed"),     # the shared corpus is never the caller's to delete
    }
    # y untouched on every tenant
    assert await memory.stats(tenant_id=None, collection=Y) == (3, 2)


async def test_delete_is_idempotent_and_reports_the_edge_count(memory):
    assert await memory.delete_collection(None, X) == 4
    assert await memory.delete_collection(None, X) == 0
    assert await memory.delete_collection("alice", X) == 0
    # and a never-populated collection is a clean 0, not an error
    assert await memory.delete_collection(None, "corpus_never") == 0


async def test_orphan_entities_go_and_shared_names_survive_through_the_other_collection(memory):
    await memory.delete_collection(None, X)
    names = {name for name, _ in await memory.list_entities(tenant_id=None, limit=100)}
    # Carol, Dave, Erin, Zed only ever had edges in x → gone.
    assert names.isdisjoint({"Carol", "Dave", "Erin", "Zed"})
    # Alice and Bob also live in y → still listed, with y's degrees only.
    assert {"Alice", "Bob", "Frank"} <= names
    degrees = dict(await memory.list_entities(tenant_id=None, limit=100))
    assert degrees["Alice"] == 2 and degrees["Bob"] == 1


async def test_entity_index_forgets_the_deleted_collection_but_not_the_siblings_name(memory):
    """``match_entities`` (#349) reads a per-``(tenant, collection)`` name index
    that only ever grows through ``add_triples``; a delete that left it alone
    would keep the dead names as stale positives and — selection being capped
    — let one displace a live entity. Same name in another collection must
    still match."""
    assert await memory.match_entities(["Carol", "Alice"], tenant_id=None, collection=X) == ["carol", "alice"]
    await memory.delete_collection(None, X)
    assert await memory.match_entities(["Carol", "Alice", "Dave"], tenant_id=None, collection=X) == []
    assert await memory.match_entities(["Alice", "Carol"], tenant_id=None, collection=Y) == ["alice"]
    assert await memory.match_entities(["Alice"], tenant_id="alice", collection=None) == ["alice"]
    # tenant-scoped: alice's bucket in x goes, bob's and public's stay
    await memory.add_triples(_seed())
    await memory.delete_collection("alice", X)
    assert await memory.match_entities(["Carol", "Dave", "Zed"], tenant_id=None, collection=X) == ["dave", "zed"]


async def test_empty_collection_is_refused_not_a_wildcard(memory):
    with pytest.raises(ValueError):
        await memory.delete_collection(None, "")
    assert await memory.stats(tenant_id=None) == (7, 6)  # nothing happened


async def test_legacy_unstamped_triples_are_never_matched_by_a_real_collection():
    """A triple written before #209 carries ``collection=""``; a scoped delete
    of any real collection must leave it (fail closed, like the reads)."""
    store = InMemoryGraphStore()
    await store.add_triples([_t("Old", "Data", collection=""),
                             _t("Alice", "Bob", collection=X)])
    assert await store.delete_collection(None, X) == 1
    assert [t.subject for t in store._triples] == ["Old"]


# --------------------------------------------------------------------------- #
# Neo4j store: the Cypher
# --------------------------------------------------------------------------- #


class _FakeResult:
    def __init__(self, records):
        self._records = records

    def __aiter__(self):
        async def gen():
            for r in self._records:
                yield r
        return gen()

    async def single(self):
        return self._records[0] if self._records else None


class _FakeSession:
    def __init__(self, driver):
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
        self.calls = []
        self.results = []

    def session(self, **kwargs):
        return _FakeSession(self)

    async def close(self):
        pass


@pytest.fixture
def neo4j_store(monkeypatch) -> Neo4jGraphStore:
    mod = types.ModuleType("neo4j")
    mod.AsyncGraphDatabase = types.SimpleNamespace(driver=lambda uri, auth=None: _FakeDriver())
    monkeypatch.setitem(sys.modules, "neo4j", mod)
    return Neo4jGraphStore(uri="bolt://x:7687", user="neo4j", password="ragstack")


async def test_neo4j_cypher_carries_both_scope_keys_on_the_edges_and_the_sweep(neo4j_store):
    drv = neo4j_store._driver
    drv.results.append([{"deleted": 4}])
    drv.results.append([{"swept": 6}])

    n = await neo4j_store.delete_collection("alice", X)

    assert n == 4
    (edges, edge_params), (sweep, sweep_params) = drv.calls
    # Edge delete: BOTH keys as MATCH properties on a DIRECTED pattern (an
    # undirected one would yield each edge twice and double the count),
    # batched so a big collection never has to fit one transaction.
    assert "MATCH ()-[r:REL {tenant_id: $tenant_id, collection: $collection}]->()" in edges
    assert "CALL { WITH r DELETE r } IN TRANSACTIONS OF 1000 ROWS" in edges
    assert "RETURN count(r) AS deleted" in edges
    assert edge_params == {"tenant_id": "alice", "collection": X}
    # Orphan sweep: the same two keys on the node, and ONLY edgeless nodes —
    # never a global `MATCH (e:Entity) WHERE NOT (e)--()` over every collection.
    assert "MATCH (e:Entity {tenant_id: $tenant_id, collection: $collection}) WHERE NOT (e)--()" in sweep
    assert "CALL { WITH e DELETE e } IN TRANSACTIONS OF 1000 ROWS" in sweep
    assert sweep_params == {"tenant_id": "alice", "collection": X}


async def test_neo4j_collection_wide_form_drops_only_the_tenant_key(neo4j_store):
    drv = neo4j_store._driver
    drv.results.append([{"deleted": 2}])
    assert await neo4j_store.delete_collection(None, X) == 2
    (edges, edge_params), (sweep, sweep_params) = drv.calls
    assert "{collection: $collection}" in edges and "tenant_id" not in edges
    assert "{collection: $collection}" in sweep and "tenant_id" not in sweep
    assert edge_params == sweep_params == {"collection": X}


async def test_neo4j_never_matches_the_readable_set(neo4j_store):
    """A delete scoped to alice must not reuse the READ filter (own + public):
    `$tenants IN [...]` would delete the shared corpus."""
    await neo4j_store.delete_collection("alice", X)
    for query, params in neo4j_store._driver.calls:
        assert "$tenants" not in query and "tenants" not in params
        assert "IN $" not in query


async def test_neo4j_empty_result_counts_zero_and_empty_collection_is_refused(neo4j_store):
    assert await neo4j_store.delete_collection(None, X) == 0  # no queued record → 0, not a crash
    with pytest.raises(ValueError):
        await neo4j_store.delete_collection(None, "")
    assert len(neo4j_store._driver.calls) == 2  # the refused call ran nothing


# --------------------------------------------------------------------------- #
# the two stores agree
# --------------------------------------------------------------------------- #


async def test_both_stores_expose_the_same_required_collection_signature(neo4j_store):
    memory = InMemoryGraphStore()
    assert isinstance(memory, GraphStore) and isinstance(neo4j_store, GraphStore)
    mem_sig = inspect.signature(memory.delete_collection)
    neo_sig = inspect.signature(neo4j_store.delete_collection)
    assert list(mem_sig.parameters) == list(neo_sig.parameters) == ["tenant_id", "collection"]
    # `collection` is REQUIRED (no default): a per-collection delete with no
    # collection is exactly the wildcard #209 forbids.
    for sig in (mem_sig, neo_sig):
        assert sig.parameters["collection"].default is inspect.Parameter.empty
        assert sig.parameters["tenant_id"].default is inspect.Parameter.empty
