"""/v1/stats/stores — tenant-scoped per-store counts.

The counts must reflect only the caller's *readable* tenants (own + public) and
never leak another tenant's corpus size. Seeded against the in-memory stores the
``client`` fixture wires onto ``app.state``.
"""
import pytest

from ragstack.api import security
from ragstack.api.main import app
from ragstack.api.security import ROLE_RESEARCHER
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
    monkeypatch.setattr(security.settings, "default_role", ROLE_RESEARCHER)


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
