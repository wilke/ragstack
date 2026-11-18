"""One 404 body for an unknown collection and an unreadable one (#1029).

``api/access.enforce_access`` answers a collection the caller may not read with
the same 404 an unknown id gets, so collection existence cannot be probed. That
promise holds only if the two 404s have the same BODY too: the registry-miss
branches in ``routers/collections.py`` used to answer ``unknown collection 'x'``
while the deny path answered ``unknown collection 'x'; see GET /v1/collections``
— same status, different text, and the difference told a stranger which private
ids exist.

Each case below asks the same route twice as a non-owner who cannot read the
private collection: once naming it, once naming an id that is not registered.
The bodies must match byte for byte once the echoed id is masked (the body
names the id the caller supplied, and nothing else). Both ids are the same
length and need no quoting, so the mask is the only difference allowed.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from ragstack.acl_store import GRANTEE_USER, PERM_OWNER, get_acl_store
from ragstack.api import security
from ragstack.api.access import unknown_collection
from ragstack.api.collections import CollectionEntry, CollectionRegistry
from ragstack.api.main import app
from ragstack.api.security import ROLE_ADMIN, ROLE_USER
from tests.api.conftest import SHARED_ID

pytestmark = pytest.mark.asyncio

KEYS = {"owner": "k-owner", "stranger": "k-stranger", "admin": "k-admin"}

#: Registered, owned by ``owner``, shared with nobody: unreadable to ``stranger``.
PRIVATE = "priv-coll"
#: Not registered at all. Same length as PRIVATE, so masking cannot hide a
#: length difference.
UNKNOWN = "nope-coll"
MASK = b"<ID>"


@pytest.fixture(autouse=True)
def _principals(monkeypatch):
    monkeypatch.setattr(security.settings, "api_keys", list(KEYS.values()))
    monkeypatch.setattr(
        security.settings,
        "api_key_tenants",
        {"k-owner": "owner", "k-stranger": "stranger", "k-admin": "admin"},
    )
    monkeypatch.setattr(security.settings, "api_key_roles", {"k-admin": ROLE_ADMIN})
    monkeypatch.setattr(security.settings, "default_role", ROLE_USER)


def _entry(cid: str, default: bool = False) -> CollectionEntry:
    from tests.api.conftest import _StateRetriever

    return CollectionEntry(
        id=cid, label=cid, collection=cid, model="test-model", dim=4,
        chunk_method="fixed", chunk_size=None, chunk_overlap=None, chunk_params={},
        is_shared_surface=default, retriever=_StateRetriever(),
        vector_store=app.state.vector_store, text_index=app.state.text_index,
        owner="",
    )


@pytest.fixture
async def _private_collection():
    app.state.kg_extractor = None
    app.state.doi_enricher = None
    app.state.collections = CollectionRegistry(
        [_entry(SHARED_ID, True), _entry(PRIVATE)], default_id=SHARED_ID
    )
    await get_acl_store().grant(
        PRIVATE, GRANTEE_USER, "owner", PERM_OWNER, granted_by="owner"
    )


# (label, method, path template, json body). ``{cid}`` is the collection id.
# Every route here has a registry-miss branch AND a deny branch for a caller who
# cannot read the collection.
ROUTES = [
    ("restore", "POST", "/v1/collections/{cid}/restore", None),
    ("delete", "DELETE", "/v1/collections/{cid}", None),
    ("delete-purge", "DELETE", "/v1/collections/{cid}?purge=true", None),
    ("list-shares", "GET", "/v1/collections/{cid}/shares", None),
    ("create-share", "POST", "/v1/collections/{cid}/shares", {"grantee": "bob"}),
    ("revoke-share", "DELETE", "/v1/collections/{cid}/shares/some-share-id", None),
    ("transfer-owner", "POST", "/v1/collections/{cid}/owner", {"subject": "bob"}),
    ("query", "POST", "/v1/query", {"query": "x", "collection": "{cid}"}),
    ("retrieve", "POST", "/v1/retrieve", {"query": "x", "collection": "{cid}"}),
    ("chunks", "GET", "/v1/chunks?ids=c1&collection={cid}", None),
    ("ingest", "POST", "/v1/ingest", {"source": "nothing.txt", "collection": "{cid}"}),
]


async def _ask(client, method: str, path: str, body: dict | None, cid: str):
    url = path.format(cid=cid)
    payload = (
        {k: (v.format(cid=cid) if isinstance(v, str) else v) for k, v in body.items()}
        if body is not None
        else None
    )
    return await client.request(
        method, url, json=payload, headers={"X-API-Key": KEYS["stranger"]}
    )


@pytest.mark.parametrize(
    "label,method,path,body", ROUTES, ids=[r[0] for r in ROUTES]
)
async def test_unknown_and_unreadable_give_the_same_404_body(
    client, _private_collection, label, method, path, body
):
    unreadable = await _ask(client, method, path, body, PRIVATE)
    unknown = await _ask(client, method, path, body, UNKNOWN)

    assert unreadable.status_code == 404, f"{label}: {unreadable.text}"
    assert unknown.status_code == 404, f"{label}: {unknown.text}"
    masked_unreadable = unreadable.content.replace(PRIVATE.encode(), MASK)
    masked_unknown = unknown.content.replace(UNKNOWN.encode(), MASK)
    assert masked_unreadable == masked_unknown, (
        f"{label}: an existing collection the caller cannot read answered "
        f"{unreadable.content!r}, an unknown id answered {unknown.content!r} — "
        "the difference tells the caller the private id exists"
    )
    assert unknown.json() == {
        "detail": f"unknown collection {UNKNOWN!r}; see GET /v1/collections"
    }


async def test_graph_extract_unknown_and_unreadable_give_the_same_404_body(
    client, _private_collection, monkeypatch
):
    """``POST /v1/collections/{id}/graph`` refuses a non-bearer caller (401)
    before it looks the id up, so the registry-miss branch is only reachable
    with a Workspace identity; stand one in for the stranger."""
    from ragstack.api.routers import collections as collections_router

    monkeypatch.setattr(
        collections_router, "gowe_caller", lambda principal: ("tok", principal.tenant)
    )
    path = "/v1/collections/{cid}/graph"
    unreadable = await _ask(client, "POST", path, None, PRIVATE)
    unknown = await _ask(client, "POST", path, None, UNKNOWN)
    assert unreadable.status_code == 404, unreadable.text
    assert unknown.status_code == 404, unknown.text
    assert unreadable.content.replace(PRIVATE.encode(), MASK) == (
        unknown.content.replace(UNKNOWN.encode(), MASK)
    )


async def test_unknown_collection_helper_is_the_one_body():
    e = unknown_collection("x")
    assert isinstance(e, HTTPException)
    assert e.status_code == 404
    assert e.detail == "unknown collection 'x'; see GET /v1/collections"


async def test_enforce_access_read_denial_uses_the_helper(_private_collection):
    """The deny path and the registry-miss path build the body in one place."""
    from ragstack.api.access import enforce_access
    from ragstack.api.security import Principal

    stranger = Principal(tenant="stranger", role=ROLE_USER)
    for action in ("read", "write", "owner"):
        with pytest.raises(HTTPException) as ei:
            await enforce_access(stranger, PRIVATE, action)
        assert ei.value.status_code == 404
        assert ei.value.detail == unknown_collection(PRIVATE).detail
