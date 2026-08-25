"""Reproduces #274: a shared collection's listing count must use the
collection's OWNER scope — the same rule ``routers/query.py`` already applies
to retrieval — not the caller's own tenant filter. Before the fix landed
(#313, ``api/scope.py::count_scope``), a grantee's own tenant never matched
the owner-stamped ``tenant_id`` payload, so the count read 0 for a corpus the
same key could search.

Also pins #314's follow-up at the sites it targets (``GET /v1/collections``):
count scope for N listed collections must cost ONE batched ACL round trip
(``AclStore.owners_of``), not N ``owner_of`` calls.
"""
from __future__ import annotations

import pytest

from ragstack.acl_store import (
    GRANTEE_GROUP,
    GRANTEE_USER,
    PERM_OWNER,
    PERM_READ,
    PUBLIC_GROUP,
    get_acl_store,
)
from ragstack.api import security
from ragstack.api.collections import CollectionRegistry
from ragstack.api.main import app
from ragstack.api.security import ROLE_USER
from ragstack.models import Chunk
from tests.api.conftest import SHARED_ID, _StateRetriever

pytestmark = pytest.mark.asyncio

KEYS = {"owner": "k-owner", "bob": "k-bob"}


@pytest.fixture(autouse=True)
def _principals(monkeypatch):
    monkeypatch.setattr(security.settings, "api_keys", list(KEYS.values()))
    monkeypatch.setattr(
        security.settings, "api_key_tenants", {"k-owner": "owner", "k-bob": "bob"},
    )
    monkeypatch.setattr(security.settings, "api_key_roles", {})
    monkeypatch.setattr(security.settings, "default_role", ROLE_USER)


def _h(who: str) -> dict:
    return {"X-API-Key": KEYS[who]}


def _entry(cid: str, default: bool = False):
    from ragstack.api.collections import CollectionEntry

    return CollectionEntry(
        id=cid, label=cid, collection=cid, model="test-model", dim=4,
        chunk_method="fixed", chunk_size=None, chunk_overlap=None, chunk_params={},
        is_shared_surface=default, retriever=_StateRetriever(),
        vector_store=app.state.vector_store, text_index=app.state.text_index,
    )


async def _ingest(n: int, *, tenant: str = "owner") -> None:
    """``n`` chunks stamped ``tenant`` — what ingest does for that tenant's key."""
    chunks = [
        Chunk(
            id=f"{tenant}-c{i}", doc_id=f"{tenant}-doc{i}", content="hello world",
            embedding=[0.1, 0.2, 0.3, 0.4], metadata={"tenant_id": tenant},
        )
        for i in range(n)
    ]
    await app.state.vector_store.upsert(chunks)
    await app.state.text_index.index(chunks)


async def test_shared_collection_count_uses_owner_scope(client):
    """owner ingests N chunks into an owned collection, shares it read-only to
    bob; bob lists collections. Expected count == N; before #313, 0."""
    app.state.kg_extractor = None
    app.state.doi_enricher = None
    app.state.collections = CollectionRegistry(
        [_entry(SHARED_ID, True), _entry("priv")], default_id=SHARED_ID,
    )
    n = 5
    await _ingest(n)
    store = get_acl_store()
    await store.grant("priv", GRANTEE_USER, "owner", PERM_OWNER, granted_by="owner")
    await store.grant("priv", GRANTEE_USER, "bob", PERM_READ, granted_by="owner")

    # The count claim is only meaningful if bob can actually retrieve that much.
    retrieved = await client.post(
        "/v1/retrieve", json={"query": "hello", "collection": "priv"}, headers=_h("bob")
    )
    assert retrieved.status_code == 200, retrieved.text
    assert len(retrieved.json()["sources"]) == n

    listing = (await client.get("/v1/collections", headers=_h("bob"))).json()
    priv = next(c for c in listing["collections"] if c["id"] == "priv")
    assert priv["count"] == n
    assert priv["text_count"] == n


async def test_shared_collection_count_uses_owner_scope_via_public_grant(client):
    """The scope rule applies identically to a ``public`` grant, not only a
    direct share — #274's fix shape says "via a share (or public)"."""
    app.state.kg_extractor = None
    app.state.doi_enricher = None
    app.state.collections = CollectionRegistry(
        [_entry(SHARED_ID, True), _entry("open")], default_id=SHARED_ID,
    )
    n = 4
    await _ingest(n)
    store = get_acl_store()
    await store.grant("open", GRANTEE_USER, "owner", PERM_OWNER, granted_by="owner")
    await store.grant("open", GRANTEE_GROUP, PUBLIC_GROUP, PERM_READ, granted_by="owner")

    listing = (await client.get("/v1/collections", headers=_h("bob"))).json()
    op = next(c for c in listing["collections"] if c["id"] == "open")
    assert op["count"] == n
    assert op["text_count"] == n


async def test_legacy_shared_surface_count_is_unchanged(client):
    """The legacy shared surface (``SHARED_ID`` / the ``default`` pointer)
    counts over ``[caller, public]`` BY DESIGN, never widened to an owner's
    tenant — its owner row is only a backfill artifact, not a real share, and
    widening it would leak every tenant's chunks into every other caller's
    count on the one collection everyone shares."""
    app.state.kg_extractor = None
    app.state.doi_enricher = None
    app.state.collections = CollectionRegistry([_entry(SHARED_ID, True)], default_id=SHARED_ID)
    await _ingest(3, tenant="bob")            # bob's own chunks: readable
    # The autouse ACL fixture already seeds SHARED_ID with an owner row
    # (legacy:admin) + a public grant — exactly the backfill artifact this
    # guards against widening on. Stamp the FILLER chunks under that same
    # backfill owner's tenant, not an arbitrary third tenant: widening (if the
    # is_shared_surface guard were ever removed) would then count them, making
    # this test non-vacuous — a stranger tenant these chunks would never
    # widen the count anyway, so the assertion would pass even with the guard
    # gone.
    backfill_owner = await get_acl_store().owner_of(SHARED_ID)
    assert backfill_owner
    await _ingest(2, tenant=backfill_owner)   # widening would count these too

    listing = (await client.get("/v1/collections", headers=_h("bob"))).json()
    shared = next(c for c in listing["collections"] if c["id"] == SHARED_ID)
    assert shared["count"] == 3  # [bob, public] only — not the 5 total in the store


async def test_listing_batches_owner_lookups_not_one_per_collection(client, monkeypatch):
    """#314: resolving count scope for a 20-collection listing must cost ONE
    ``AclStore.owners_of`` round trip, not one ``owner_of`` call per entry."""
    app.state.kg_extractor = None
    app.state.doi_enricher = None
    n_collections = 20
    entries = [_entry(f"c{i}") for i in range(n_collections)]
    app.state.collections = CollectionRegistry(
        [_entry(SHARED_ID, True), *entries], default_id=SHARED_ID,
    )
    acl = get_acl_store()
    for i in range(n_collections):
        await acl.grant(f"c{i}", GRANTEE_USER, f"owner{i}", PERM_OWNER, granted_by=f"owner{i}")
        await acl.grant(f"c{i}", GRANTEE_USER, "bob", PERM_READ, granted_by=f"owner{i}")

    acl_calls = {"owner_of": 0, "owners_of": 0}
    orig_owner_of, orig_owners_of = acl.owner_of, acl.owners_of

    async def counted_owner_of(cid):
        acl_calls["owner_of"] += 1
        return await orig_owner_of(cid)

    async def counted_owners_of(cids):
        acl_calls["owners_of"] += 1
        return await orig_owners_of(cids)

    monkeypatch.setattr(acl, "owner_of", counted_owner_of)
    monkeypatch.setattr(acl, "owners_of", counted_owners_of)

    # The vector/text stores are shared fakes across every entry (conftest's
    # `client` fixture) — count_tenants calls on the vector leg are still one
    # per listed collection (20), never doubled to 40 by the scope-resolution
    # change.
    vector_calls = {"n": 0}
    orig_count_tenants = app.state.vector_store.count_tenants

    async def counted_count_tenants(tenants):
        vector_calls["n"] += 1
        return await orig_count_tenants(tenants)

    monkeypatch.setattr(app.state.vector_store, "count_tenants", counted_count_tenants)

    listing = (await client.get("/v1/collections", headers=_h("bob"))).json()
    assert len(listing["collections"]) == n_collections + 1  # + the shared surface

    assert acl_calls["owner_of"] == 0, "count-scope owner lookups must be batched (#314)"
    assert acl_calls["owners_of"] == 1, "exactly one batched owner lookup for the whole listing"
    assert vector_calls["n"] == n_collections + 1, "one vector count per listed collection, not 2x"
