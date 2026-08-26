"""Per-tenant collection access control.

One multi-collection API can serve several orgs: a tenant listed in
``settings.tenant_collections`` is confined to its collection ids for reads
(query / retrieve / chunks / GET /v1/collections) and ingest targets; a tenant
NOT listed (or an empty map) is unrestricted. Out-of-scope ids 404 like unknown
ones, so collection existence isn't leaked.
"""
import pytest

from ragstack.api import security
from ragstack.api.collections import CollectionEntry, CollectionRegistry
from ragstack.api.main import app
from ragstack.api.security import ROLE_ADMIN
from ragstack.config import settings
from tests.api.conftest import SHARED_ID

# --- pure resolution logic ---------------------------------------------------#
#
# MOVED to tests/api/test_default_collection.py (#419). `_effective_collection`
# is gone: it held a SECOND copy of "the default for this caller" that drifted
# from the listing's on two axes — it never consulted the readable set, and it
# broke ties with `sorted()` where the listing used insertion order. The
# explicit-allowlist half it also owned is now `query.py::_check_allowlist` and
# is tested there; the implicit half is `default_collection.pick_default`.
#
# One case changed MEANING in the move and did not just relocate:
# `test_effective_collection_falls_back_to_first_allowed`. It LOOKED like it
# pinned `sorted()` and did not — its registry was [ragstack, "a", "b"] with
# allowlist {a, b}, so insertion-first and lexicographic-first were BOTH "a"
# and the assertion held under either rule. Nothing in this tree pinned either
# ordering, which is why the two implementations could disagree for years
# unnoticed: that IS #419's thesis, in miniature. The replacement reorders the
# registry so the two rules give different answers, and asserts insertion order
# (decision D2 on #419). Deliberate. An implementer who "fixes" it back to
# `sorted()` has reintroduced the drift.

# --- endpoints (in-memory registry; no real stores) ------------------------- #


def _entry(cid: str, default: bool = False) -> CollectionEntry:
    return CollectionEntry(
        id=cid, label=cid, collection=cid, model="m", dim=8,
        chunk_method="fixed", chunk_size=None, chunk_overlap=None, chunk_params={},
        is_shared_surface=default, retriever=None, vector_store=None, text_index=None, embedder=None,
    )


def _install_registry():
    app.state.collections = CollectionRegistry(
        [_entry(SHARED_ID, True), _entry("col_a"), _entry("col_b")], default_id=SHARED_ID
    )


@pytest.fixture(autouse=True)
def _keyless(monkeypatch):
    # keyless dev → tenant "default", admin role; feature off unless a test sets it
    monkeypatch.setattr(security.settings, "api_keys", [])
    monkeypatch.setattr(security.settings, "default_role", ROLE_ADMIN)
    monkeypatch.setattr(settings, "tenant_collections", {})


@pytest.mark.asyncio
async def test_list_filtered_to_allowed(client, monkeypatch):
    _install_registry()
    monkeypatch.setattr(settings, "tenant_collections", {"default": ["col_a"]})
    body = (await client.get("/v1/collections")).json()
    ids = {c["id"] for c in body["collections"]}
    assert ids == {"col_a"}  # not the shared collection, not "col_b"
    assert body["default"] == "col_a"  # effective default is the accessible one


@pytest.mark.asyncio
async def test_list_unrestricted_sees_all(client):
    _install_registry()  # feature off (empty map)
    body = (await client.get("/v1/collections")).json()
    assert {c["id"] for c in body["collections"]} == {SHARED_ID, "col_a", "col_b"}
    assert body["default"] == SHARED_ID


@pytest.mark.asyncio
async def test_query_disallowed_collection_is_404(client, monkeypatch):
    _install_registry()
    monkeypatch.setattr(settings, "tenant_collections", {"default": ["col_a"]})
    resp = await client.post("/v1/query", json={"query": "x", "collection": "col_b"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_retrieve_disallowed_collection_is_404(client, monkeypatch):
    _install_registry()
    monkeypatch.setattr(settings, "tenant_collections", {"default": ["col_a"]})
    resp = await client.post("/v1/retrieve", json={"query": "x", "collection": "col_b"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_ingest_disallowed_collection_is_404(client, monkeypatch):
    _install_registry()
    monkeypatch.setattr(settings, "tenant_collections", {"default": ["col_a"]})
    resp = await client.post(
        "/v1/ingest", json={"source": "x.txt", "collection": "col_b"}
    )
    assert resp.status_code == 404
