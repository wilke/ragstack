"""/v1/stats/stores — tenant-scoped per-store counts.

The counts must reflect only the caller's *readable* tenants (own + public) and
never leak another tenant's corpus size. Seeded against the in-memory stores the
``client`` fixture wires onto ``app.state``.
"""
import pytest

from ragstack.api import security
from ragstack.api.main import app
from ragstack.api.security import ROLE_USER
from ragstack.models import Chunk, Triple

pytestmark = pytest.mark.asyncio


def _chunk(cid: str, tenant: str) -> Chunk:
    return Chunk(
        id=cid,
        doc_id=f"doc-{cid}",
        content="hello world",
        embedding=[0.1, 0.2, 0.3, 0.4],
        metadata={"tenant_id": tenant},
    )


async def _seed() -> None:
    # acme: 2, other: 1, public: 1 — across both the vector store and text index.
    chunks = [
        _chunk("a1", "acme"),
        _chunk("a2", "acme"),
        _chunk("b1", "other"),
        _chunk("p1", "public"),
    ]
    await app.state.vector_store.upsert(chunks)
    await app.state.text_index.index(chunks)


def _configure_keys(monkeypatch) -> None:
    monkeypatch.setattr(security.settings, "api_keys", ["k-acme", "k-other"])
    monkeypatch.setattr(
        security.settings, "api_key_tenants", {"k-acme": "acme", "k-other": "other"}
    )
    monkeypatch.setattr(security.settings, "api_key_roles", {})
    monkeypatch.setattr(security.settings, "default_role", ROLE_USER)


async def test_counts_scoped_to_readable_tenants(client, monkeypatch):
    _configure_keys(monkeypatch)
    await _seed()

    body = (await client.get("/v1/stats/stores", headers={"X-API-Key": "k-acme"})).json()

    assert set(body["tenants"]) == {"acme", "public"}
    # acme's 2 + public's 1 = 3; other's chunk is excluded.
    assert body["vector"]["count"] == 3
    assert body["text"]["count"] == 3
    assert body["vector"]["available"] is True
    assert body["text"]["available"] is True


async def test_no_cross_tenant_leak(client, monkeypatch):
    _configure_keys(monkeypatch)
    await _seed()

    other = (await client.get("/v1/stats/stores", headers={"X-API-Key": "k-other"})).json()
    # other's 1 + public's 1 = 2; acme's two chunks never counted.
    assert other["vector"]["count"] == 2
    assert other["text"]["count"] == 2
    assert set(other["tenants"]) == {"other", "public"}


async def test_missing_key_is_401_when_keys_configured(client, monkeypatch):
    _configure_keys(monkeypatch)
    assert (await client.get("/v1/stats/stores")).status_code == 401


async def test_graph_count_is_relationship_count(client, monkeypatch):
    _configure_keys(monkeypatch)
    # Seed so entities != relationships: acme has 3 entities (A,B,C) across 2
    # relationships; the count must be the RELATIONSHIP count (2), not entities
    # (3) — and never the other tenant's edge. This fails if _count_graph ever
    # returns entities instead of relationships.
    await app.state.graph_store.add_triples([
        Triple(subject="A", predicate="rel", object="B", doc_id="d1", tenant_id="acme"),
        Triple(subject="B", predicate="rel", object="C", doc_id="d1", tenant_id="acme"),
        Triple(subject="X", predicate="rel", object="Y", doc_id="d2", tenant_id="other"),
    ])
    body = (await client.get("/v1/stats/stores", headers={"X-API-Key": "k-acme"})).json()
    assert body["graph"]["available"] is True
    assert body["graph"]["count"] == 2  # relationships, distinct from 3 entities


async def test_graph_count_is_collection_scoped_for_a_confined_tenant(client, monkeypatch):
    """One graph store spans every collection (#209), so a tenant confined by
    TENANT_COLLECTIONS must not be told the size of the whole graph. The fixture's
    only collection is physically named ``ragstack``."""
    from ragstack.config import settings

    _configure_keys(monkeypatch)
    await app.state.graph_store.add_triples([
        Triple(subject="A", predicate="rel", object="B", doc_id="d1",
               tenant_id="acme", collection="ragstack"),
        Triple(subject="X", predicate="rel", object="Y", doc_id="d2",
               tenant_id="acme", collection="other_corpus"),
    ])
    monkeypatch.setattr(settings, "tenant_collections", {"acme": ["default"]})

    body = (await client.get("/v1/stats/stores", headers={"X-API-Key": "k-acme"})).json()
    assert body["graph"]["count"] == 1  # not 2 — the other collection's edge is out


def _entry(cid: str, physical: str, vector_store, text_index, es_index: str = ""):
    """A registry entry bound to the given physical stores (test helper)."""
    from ragstack.api.collections import CollectionEntry

    return CollectionEntry(
        id=cid, label=cid, collection=physical,
        model="test-model", dim=4, chunk_method="fixed", chunk_size=None,
        chunk_overlap=None, chunk_params={},
        is_shared_surface=False, retriever=None,
        vector_store=vector_store, text_index=text_index,
        text_index_name=es_index,
    )


def _publish(store, collection_id: str) -> None:
    """World-read the collection, the way the conftest seeds ``default`` — these
    tests are about COUNTING, and an unpublished entry is filtered out by the
    ownership gate before any probe runs."""
    import uuid

    from ragstack.acl_store import GRANTEE_GROUP, PERM_READ, PUBLIC_GROUP, ShareRecord

    rec = ShareRecord(
        id=uuid.uuid4().hex,
        collection_id=collection_id,
        grantee_type=GRANTEE_GROUP,
        grantee_id=PUBLIC_GROUP,
        permission=PERM_READ,
        granted_by="system:test",
        granted_at="2020-01-01T00:00:00+00:00",
    )
    store._shares[rec.id] = rec


async def test_counts_span_every_readable_collection(client, monkeypatch, _acl_store):
    """The corpus usually lives in a NAMED collection, not the default store.

    Counting only the default one reported 0 for a deployment whose data sits in
    e.g. `oa-dev` — each collection is its own physical Qdrant collection / ES
    index, so the honest total is their sum.
    """
    from ragstack.api.collections import CollectionRegistry
    from ragstack.stores.memory import InMemoryTextIndex, InMemoryVectorStore

    _configure_keys(monkeypatch)
    await _seed()  # 3 readable chunks in the default stores

    # A second collection with its own physical stores, holding one more chunk.
    other_vec, other_txt = InMemoryVectorStore(), InMemoryTextIndex()
    extra = [_chunk("c1", "acme")]
    await other_vec.upsert(extra)
    await other_txt.index(extra)
    _publish(_acl_store, "named")
    app.state.collections = CollectionRegistry(
        [
            _entry("default", "ragstack", app.state.vector_store, app.state.text_index),
            _entry("named", "ragstack_named", other_vec, other_txt),
        ],
        default_id="default",
    )

    body = (await client.get("/v1/stats/stores", headers={"X-API-Key": "k-acme"})).json()
    assert body["vector"]["count"] == 4  # 3 in the default store + 1 in the named one
    assert body["text"]["count"] == 4


async def test_shared_physical_store_is_counted_once(client, monkeypatch, _acl_store):
    """Two registry entries may deliberately share one physical store; summing
    per entry would report that data twice."""
    from ragstack.api.collections import CollectionRegistry

    _configure_keys(monkeypatch)
    await _seed()  # 3 readable chunks
    _publish(_acl_store, "b")

    app.state.collections = CollectionRegistry(
        [
            _entry("default", "ragstack", app.state.vector_store, app.state.text_index),
            _entry("b", "ragstack", app.state.vector_store, app.state.text_index),
        ],
        default_id="default",
    )

    body = (await client.get("/v1/stats/stores", headers={"X-API-Key": "k-acme"})).json()
    assert body["vector"]["count"] == 3  # not 6
    assert body["text"]["count"] == 3
