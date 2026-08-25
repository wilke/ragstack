"""GET /v1/chunks — collection scope (#197 acceptance criterion; prep for #322's
server-side context expansion, which calls ``get_chunks`` on whatever
``_resolve_entry`` hands it and depends on both guarantees below holding).

1. A caller who cannot resolve a collection gets the same 404 as an unknown id
   (the ownership seam, ``enforce_access`` — see test_collection_ownership.py's
   ``test_chunks_endpoint_is_gated_too`` for the original). Pinned here again
   because it's the FIRST line of defence #322 relies on.

2. A chunk id that only exists in a *different* collection's store never
   resolves through a collection the caller CAN read — even when the id string
   collides. Each ``CollectionEntry`` is bound to its own physical store
   (ADR-0002: one physical index has exactly one registry entry), and
   ``get_chunks`` resolves by point id against THAT entry's store only, so a
   colliding id in another collection's store is simply never seen, let alone
   leaked.
"""
from __future__ import annotations

import pytest

from ragstack.acl_store import GRANTEE_USER, PERM_OWNER, get_acl_store
from ragstack.api import security
from ragstack.api.collections import CollectionEntry, CollectionRegistry
from ragstack.api.main import app
from ragstack.api.security import ROLE_USER
from ragstack.models import Chunk
from ragstack.stores import InMemoryVectorStore
from tests.api.conftest import SHARED_ID

pytestmark = pytest.mark.asyncio

KEYS = {"owner": "k-owner", "stranger": "k-stranger"}


@pytest.fixture(autouse=True)
def _principals(monkeypatch):
    """Auth ON (api_keys set) so the ownership seam is enforced — the point of
    this suite (mirrors test_collection_ownership.py's fixture)."""
    monkeypatch.setattr(security.settings, "api_keys", list(KEYS.values()))
    monkeypatch.setattr(
        security.settings,
        "api_key_tenants",
        {"k-owner": "owner", "k-stranger": "stranger"},
    )
    monkeypatch.setattr(security.settings, "default_role", ROLE_USER)


def _h(who: str) -> dict:
    return {"X-API-Key": KEYS[who]}


def _entry(cid: str, store: InMemoryVectorStore, default: bool = False) -> CollectionEntry:
    from tests.api.conftest import _StateRetriever

    return CollectionEntry(
        id=cid, label=cid, collection=cid, model="test-model", dim=4,
        chunk_method="fixed", chunk_size=None, chunk_overlap=None, chunk_params={},
        is_shared_surface=default, retriever=_StateRetriever(),
        vector_store=store, text_index=app.state.text_index,
    )


async def _own(cid: str, subject: str) -> None:
    await get_acl_store().grant(cid, GRANTEE_USER, subject, PERM_OWNER, granted_by=subject)


async def test_unresolvable_collection_is_404_not_leaked(client):
    store_a = InMemoryVectorStore()
    await store_a.upsert(
        [Chunk(id="c1", doc_id="D", content="x", metadata={"tenant_id": "owner"})]
    )
    app.state.collections = CollectionRegistry(
        [_entry(SHARED_ID, app.state.vector_store, default=True), _entry("priv", store_a)],
        default_id=SHARED_ID,
    )
    await _own("priv", "owner")
    resp = await client.get(
        "/v1/chunks", params={"ids": "c1", "collection": "priv"}, headers=_h("stranger")
    )
    assert resp.status_code == 404, resp.text


async def test_id_from_another_collection_does_not_resolve(client):
    """Same chunk id string exists in TWO collections' separate stores. A
    caller resolving collection B — which it fully owns — must not see
    collection A's copy: get_chunks runs against B's own store, which never
    received that point."""
    store_a = InMemoryVectorStore()
    store_b = InMemoryVectorStore()
    await store_a.upsert(
        [Chunk(id="shared-id", doc_id="DA", content="secret in A",
               metadata={"tenant_id": "owner"})]
    )
    await store_b.upsert(
        [Chunk(id="other", doc_id="DB", content="in B", metadata={"tenant_id": "owner"})]
    )
    app.state.collections = CollectionRegistry(
        [
            _entry(SHARED_ID, app.state.vector_store, default=True),
            _entry("libA", store_a),
            _entry("libB", store_b),
        ],
        default_id=SHARED_ID,
    )
    await _own("libA", "owner")
    await _own("libB", "owner")

    resp = await client.get(
        "/v1/chunks",
        params={"ids": "shared-id,other", "collection": "libB"},
        headers=_h("owner"),
    )
    assert resp.status_code == 200, resp.text
    ids = [c["chunk_id"] for c in resp.json()["chunks"]]
    # "shared-id" lives only in libA's store — omitted, not leaked, even
    # though the owner can read both collections and the id string collides.
    assert ids == ["other"]


async def test_unknown_filter_key_from_the_store_is_400_not_500(client, monkeypatch):
    """The router's own filters are always well-formed (scope_filters only ever
    emits ``tenant_id``), but a store is free to refuse a filters dict it
    doesn't understand (#197) — the router must turn that refusal into a 400,
    not let it surface as an unhandled 500. Exercised by forcing the resolved
    entry's store to raise, since nothing in this endpoint can produce an
    unknown key today (that's the point: this is a belt-and-braces mapping for
    when #322's expansion widens what get_chunks is called with)."""
    from ragstack.stores.filters import UnknownFilterKey

    async def _boom(ids, filters=None):
        raise UnknownFilterKey("bogus")

    monkeypatch.setattr(app.state.vector_store, "get_chunks", _boom)
    resp = await client.get("/v1/chunks", params={"ids": "c1"}, headers=_h("owner"))
    assert resp.status_code == 400, resp.text
