"""Unit tests for Neo4jGraphStore against a mocked neo4j driver.

These never touch a live Neo4j: a fake AsyncGraphDatabase/driver/session records
every Cypher query + params so we can assert the generated Cypher, the tenant
filter, and the (subject, predicate, object) round-trip. The real ``neo4j``
driver is the optional ``graph`` extra and may not be installed in CI.
"""
from __future__ import annotations

import sys
import types

import pytest

from ragstack.models import Triple
from ragstack.stores.neo4j import Neo4jGraphStore
from ragstack.tenancy import PUBLIC_TENANT


class _FakeResult:
    def __init__(self, records: list[dict]):
        self._records = records

    def __aiter__(self):
        async def gen():
            for r in self._records:
                yield r
        return gen()

    async def single(self):
        """Mirror neo4j's ``result.single()`` (used by ``stats``): the first
        record, or None when the result is empty."""
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
        # Return the next queued result, or an empty one.
        if self._driver.results:
            return _FakeResult(self._driver.results.pop(0))
        return _FakeResult([])


class _FakeDriver:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.results: list[list[dict]] = []
        self.closed = False

    def session(self, **kwargs):
        return _FakeSession(self)

    async def close(self):
        self.closed = True


class _FakeGraphDatabase:
    last_auth = None

    @staticmethod
    def driver(uri, auth=None):
        _FakeGraphDatabase.last_auth = (uri, auth)
        return _FakeDriver()


@pytest.fixture
def fake_neo4j(monkeypatch):
    """Inject a fake ``neo4j`` module so ``from neo4j import AsyncGraphDatabase``
    inside the store resolves to our recorder."""
    mod = types.ModuleType("neo4j")
    mod.AsyncGraphDatabase = _FakeGraphDatabase
    monkeypatch.setitem(sys.modules, "neo4j", mod)
    return mod


@pytest.fixture
def store(fake_neo4j):
    s = Neo4jGraphStore(uri="bolt://x:7687", user="neo4j", password="ragstack")
    return s


def _driver(store: Neo4jGraphStore) -> _FakeDriver:
    return store._driver  # type: ignore[return-value]


async def test_constructs_driver_with_auth(store):
    assert _FakeGraphDatabase.last_auth == ("bolt://x:7687", ("neo4j", "ragstack"))


async def test_add_triples_merges_with_tenant_and_collection(store):
    await store.add_triples(
        [Triple(subject="Alice", predicate="knows", object="Bob",
                doc_id="d1", tenant_id="alice", collection="x")]
    )
    query, params = _driver(store).calls[-1]
    # Entities and the relationship are all MERGE'd with BOTH stamps: entity
    # identity is (name, tenant_id, collection), so the same surface form in two
    # collections is two nodes and cannot be traversed between (#209).
    assert (
        "MERGE (s:Entity {name: row.subject, tenant_id: row.tenant_id, "
        "collection: row.collection})"
    ) in query
    assert (
        "MERGE (o:Entity {name: row.object, tenant_id: row.tenant_id, "
        "collection: row.collection})"
    ) in query
    assert (
        ":REL {predicate: row.predicate, doc_id: row.doc_id, "
        "tenant_id: row.tenant_id, collection: row.collection}"
    ) in query
    assert params["rows"] == [
        {"subject": "Alice", "predicate": "knows", "object": "Bob",
         "doc_id": "d1", "tenant_id": "alice", "collection": "x"}
    ]


async def test_add_triples_empty_is_noop(store):
    await store.add_triples([])
    assert _driver(store).calls == []


async def test_add_triples_stamps_default_tenant(store):
    await store.add_triples([Triple(subject="a", predicate="r", object="b")])
    _, params = _driver(store).calls[-1]
    assert params["rows"][0]["tenant_id"] == "default"


async def test_query_neighborhood_filters_to_readable_tenants(store):
    _driver(store).results.append([
        {"subject": "Alice", "predicate": "knows", "object": "Bob",
         "doc_id": "d1", "tenant_id": "alice"},
    ])
    triples = await store.query_neighborhood("alice", depth=1, tenant_id="alice")
    query, params = _driver(store).calls[-1]
    # Tenant filter present and scoped to own + public.
    assert "r.tenant_id IN $tenants" in query
    assert params["tenants"] == ["alice", PUBLIC_TENANT]
    assert params["entity"] == "alice"  # lowercased for case-insensitive match
    # Round-trips a directed triple.
    assert triples == [
        Triple(subject="Alice", predicate="knows", object="Bob",
               doc_id="d1", tenant_id="alice")
    ]


async def test_query_neighborhood_unscoped_has_no_tenant_filter(store):
    await store.query_neighborhood("x", depth=1, tenant_id=None)
    query, params = _driver(store).calls[-1]
    assert "$tenants" not in query
    assert "tenants" not in params


async def test_query_neighborhood_depth_in_path(store):
    await store.query_neighborhood("x", depth=3, tenant_id="t")
    query, _ = _driver(store).calls[-1]
    assert "*1..3" in query


async def test_query_neighborhood_depth_floored_to_one(store):
    await store.query_neighborhood("x", depth=0, tenant_id="t")
    query, _ = _driver(store).calls[-1]
    assert "*1..1" in query


async def test_query_neighborhood_depth_capped(store):
    # An absurd depth is clamped so it can't blow up the variable-length pattern.
    await store.query_neighborhood("x", depth=1000, tenant_id="t")
    query, _ = _driver(store).calls[-1]
    assert "*1..5" in query
    assert "*1..1000" not in query


async def test_query_neighborhood_scopes_the_traversal_not_just_returned_edges(store):
    # The path predicate must require every hop to be readable, so a multi-hop
    # query can't tunnel through another tenant's edge to reach a hidden entity.
    await store.query_neighborhood("x", depth=3, tenant_id="alice")
    query, _ = _driver(store).calls[-1]
    assert "all(rel IN rels WHERE rel.tenant_id IN $tenants)" in query


async def test_list_entities_scoped_and_ranked(store):
    _driver(store).results.append([
        {"name": "Alice", "degree": 3},
        {"name": "Bob", "degree": 1},
    ])
    result = await store.list_entities(tenant_id="alice", limit=10)
    query, params = _driver(store).calls[-1]
    assert "WHERE r.tenant_id IN $tenants" in query
    assert params["tenants"] == ["alice", PUBLIC_TENANT]
    assert params["limit"] == 10
    assert "ORDER BY degree DESC" in query
    assert result == [("Alice", 3), ("Bob", 1)]


async def test_list_entities_unscoped(store):
    _driver(store).results.append([])
    await store.list_entities(tenant_id=None)
    query, params = _driver(store).calls[-1]
    assert "$tenants" not in query
    assert "tenants" not in params


async def test_delete_by_doc_scoped_to_tenant(store):
    await store.delete_by_doc("d1", tenant_id="alice")
    # A single query deletes the doc's relationships scoped by doc + tenant, then
    # sweeps only THIS delete's endpoint entities if now edgeless (not a global
    # scan that could delete other tenants' nodes).
    del_query, del_params = _driver(store).calls[0]
    assert "r.doc_id = $doc_id" in del_query
    assert "r.tenant_id = $tenant_id" in del_query
    assert del_params == {"doc_id": "d1", "tenant_id": "alice"}
    assert "MATCH (e:Entity) WHERE NOT (e)--()" not in del_query  # no global sweep
    assert "UNWIND ends AS e" in del_query  # endpoint-scoped sweep


async def test_delete_by_doc_unscoped_omits_tenant_clause(store):
    await store.delete_by_doc("d1", tenant_id=None)
    del_query, del_params = _driver(store).calls[0]
    assert "r.tenant_id" not in del_query
    assert del_params == {"doc_id": "d1"}


async def test_ensure_schema_creates_constraint(store):
    await store.ensure_schema()
    queries = [q for q, _ in _driver(store).calls]
    # The pre-#209 (name, tenant_id) constraint is dropped first: it would REJECT
    # the same entity name existing in two collections, which is exactly what
    # collection scoping needs. Both statements are idempotent (IF EXISTS /
    # IF NOT EXISTS), so ensure_schema doubles as the migration.
    assert "DROP CONSTRAINT entity_name_tenant IF EXISTS" in queries[0]
    assert "CREATE CONSTRAINT" in queries[-1]
    assert "(e.name, e.tenant_id, e.collection) IS UNIQUE" in queries[-1]


async def test_stats_scopes_both_counts_and_reports_n_zero(store):
    # Entities exist but no relationships: the OPTIONAL MATCH row must survive so
    # stats reports (5, 0), not (0, 0). Both counts are tenant-scoped.
    _driver(store).results.append([{"entities": 5, "relationships": 0}])
    entities, relationships = await store.stats(tenant_id="alice")
    assert (entities, relationships) == (5, 0)
    query, params = _driver(store).calls[-1]
    assert "WHERE e.tenant_id IN $tenants" in query
    assert "WHERE r.tenant_id IN $tenants" in query
    assert "OPTIONAL MATCH" in query  # so entities>0 / rels=0 is not dropped to (0,0)
    assert params["tenants"] == ["alice", PUBLIC_TENANT]


async def test_stats_unscoped_omits_tenant_filter(store):
    _driver(store).results.append([{"entities": 3, "relationships": 2}])
    assert await store.stats(tenant_id=None) == (3, 2)
    query, params = _driver(store).calls[-1]
    assert "$tenants" not in query
    assert "tenants" not in params


async def test_stats_empty_result_is_zero(store):
    # No queued result → fake single() returns None → (0, 0), never a crash.
    assert await store.stats(tenant_id="alice") == (0, 0)


async def test_close_closes_driver(store):
    drv = _driver(store)
    await store.close()
    assert drv.closed is True
