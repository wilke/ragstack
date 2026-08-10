"""Counts must report what a query over the same collection would return.

A private collection's chunks are stamped with the OWNER's tenant at ingest.
Read authorization (the share) and data visibility (the per-chunk ``tenant_id``
scope) are independent gates, and query resolves both (routers/query.py
``shared_scope``) while counting resolved only the second. A grantee could
therefore search a corpus of 1.5M chunks and be told, by /v1/collections and by
the Ops store tiles, that it held 0 — indistinguishable from empty.
"""
from __future__ import annotations

import pytest

from ragstack.acl_store import GRANTEE_USER, PERM_OWNER, PERM_READ, get_acl_store
from ragstack.api import security
from ragstack.api.collections import CollectionEntry, CollectionRegistry
from ragstack.api.main import app
from ragstack.api.security import ROLE_USER
from ragstack.models import Chunk

pytestmark = pytest.mark.asyncio

KEYS = {"owner": "k-owner", "grantee": "k-grantee"}


@pytest.fixture(autouse=True)
def _principals(monkeypatch):
    monkeypatch.setattr(security.settings, "api_keys", list(KEYS.values()))
    monkeypatch.setattr(
        security.settings,
        "api_key_tenants",
        {"k-owner": "owner", "k-grantee": "grantee"},
    )
    monkeypatch.setattr(security.settings, "api_key_roles", {})
    monkeypatch.setattr(security.settings, "default_role", ROLE_USER)


def _h(who: str) -> dict:
    return {"X-API-Key": KEYS[who]}


def _entry(cid: str, default: bool = False) -> CollectionEntry:
    from tests.api.conftest import _StateRetriever

    return CollectionEntry(
        id=cid, label=cid, collection=cid, model="test-model", dim=4,
        chunk_method="fixed", chunk_size=None, chunk_overlap=None, chunk_params={},
        is_shared_surface=default, retriever=_StateRetriever(),
        vector_store=app.state.vector_store, text_index=app.state.text_index,
    )


async def _seed_owned_corpus(n: int = 3) -> None:
    """n chunks stamped with the OWNER's tenant — what ingest does."""
    chunks = [
        Chunk(
            id=f"c{i}",
            doc_id=f"doc{i}",
            content="hello world",
            embedding=[0.1, 0.2, 0.3, 0.4],
            metadata={"tenant_id": "owner"},
        )
        for i in range(n)
    ]
    await app.state.vector_store.upsert(chunks)
    await app.state.text_index.index(chunks)


async def _setup_shared() -> None:
    app.state.kg_extractor = None
    app.state.doi_enricher = None
    app.state.collections = CollectionRegistry(
        [_entry("default", True), _entry("priv")], default_id="default"
    )
    await _seed_owned_corpus()
    store = get_acl_store()
    await store.grant("priv", GRANTEE_USER, "owner", PERM_OWNER, granted_by="owner")
    await store.grant("priv", GRANTEE_USER, "grantee", PERM_READ, granted_by="owner")


async def test_collections_count_matches_what_the_grantee_can_retrieve(client):
    await _setup_shared()

    listing = (await client.get("/v1/collections", headers=_h("grantee"))).json()
    priv = next(c for c in listing["collections"] if c["id"] == "priv")

    # The same key really can search it — that is why 0 was a lie, not a policy.
    got = await client.post(
        "/v1/retrieve", json={"query": "hello", "collection": "priv"}, headers=_h("grantee")
    )
    assert got.status_code == 200, got.text
    retrieved = len(got.json()["sources"])
    assert retrieved == 3

    assert priv["count"] == retrieved  # was 0
    assert priv["text_count"] == retrieved


async def test_store_totals_include_a_shared_collection(client):
    await _setup_shared()
    body = (await client.get("/v1/stats/stores", headers=_h("grantee"))).json()
    assert body["vector"]["count"] == 3
    assert body["text"]["count"] == 3


async def test_tenants_grid_shows_the_owner_row_for_a_shared_collection(client):
    await _setup_shared()
    body = (await client.get("/v1/stats/tenants", headers=_h("grantee"))).json()
    rows = {r["tenant"]: r for r in body["tenants"]}
    assert "owner" in rows, "the writer-tenant reached through the share must be a row"
    cell = next(c for c in rows["owner"]["collections"] if c["collection"] == "priv")
    assert cell["vector_count"] == 3
    # The grid must still split apart: the grantee's own scope owns nothing.
    own = next(c for c in rows["grantee"]["collections"] if c["collection"] == "priv")
    assert own["vector_count"] == 0


async def test_a_shared_row_is_scoped_to_the_shared_collection_only(client):
    """The owner row must not carry that tenant's size in a collection that was
    never shared — the share is per collection, and so is the widening."""
    app.state.kg_extractor = None
    app.state.doi_enricher = None
    app.state.collections = CollectionRegistry(
        [_entry("default", True), _entry("priv"), _entry("secret")], default_id="default"
    )
    await _seed_owned_corpus()
    store = get_acl_store()
    await store.grant("priv", GRANTEE_USER, "owner", PERM_OWNER, granted_by="owner")
    await store.grant("secret", GRANTEE_USER, "owner", PERM_OWNER, granted_by="owner")
    await store.grant("priv", GRANTEE_USER, "grantee", PERM_READ, granted_by="owner")

    body = (await client.get("/v1/stats/tenants", headers=_h("grantee"))).json()
    rows = {r["tenant"]: r for r in body["tenants"]}
    cols = {c["collection"]: c for c in rows["owner"]["collections"]}
    assert cols["priv"]["vector_count"] == 3
    # 'secret' is not shared with this caller, so it is not even a column for it;
    # if it is listed, it must never carry the owner's size.
    assert cols.get("secret", {}).get("vector_count") in (None, 0)


async def test_an_unshared_collection_still_counts_zero_for_a_stranger(client):
    """Widening is the SHARE, no wider: without a grant the count stays 0 and the
    collection is not listed at all."""
    app.state.kg_extractor = None
    app.state.doi_enricher = None
    app.state.collections = CollectionRegistry(
        [_entry("default", True), _entry("priv")], default_id="default"
    )
    await _seed_owned_corpus()
    await get_acl_store().grant("priv", GRANTEE_USER, "owner", PERM_OWNER, granted_by="owner")

    listing = (await client.get("/v1/collections", headers=_h("grantee"))).json()
    assert all(c["id"] != "priv" for c in listing["collections"])
    body = (await client.get("/v1/stats/stores", headers=_h("grantee"))).json()
    assert body["vector"]["count"] == 0
