"""Per-tenant collection access control.

One multi-collection API can serve several orgs: a tenant listed in
``settings.tenant_collections`` is confined to its collection ids for reads
(query / retrieve / chunks / GET /v1/collections) and ingest targets; a tenant
NOT listed (or an empty map) is unrestricted. Out-of-scope ids 404 like unknown
ones, so collection existence isn't leaked.
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from ragstack.api import security
from ragstack.api.collections import CollectionEntry, CollectionRegistry
from ragstack.api.main import app
from ragstack.api.routers.query import _effective_collection
from ragstack.api.security import ROLE_ADMIN
from ragstack.config import settings

# --- pure resolution logic (no HTTP / no backend) --------------------------- #


def _reg(ids, default):
    return SimpleNamespace(
        default_id=default, entries=lambda: [SimpleNamespace(id=i) for i in ids]
    )


def test_effective_collection_unrestricted_passthrough(monkeypatch):
    monkeypatch.setattr(settings, "tenant_collections", {})
    reg = _reg(["default", "a"], "default")
    assert _effective_collection(reg, "a", "t") == "a"
    assert _effective_collection(reg, None, "t") is None  # → registry default later


def test_effective_collection_allowed(monkeypatch):
    monkeypatch.setattr(settings, "tenant_collections", {"t": ["a", "b"]})
    assert _effective_collection(_reg(["default", "a", "b"], "default"), "a", "t") == "a"


def test_effective_collection_disallowed_is_404(monkeypatch):
    monkeypatch.setattr(settings, "tenant_collections", {"t": ["a"]})
    with pytest.raises(HTTPException) as ei:
        _effective_collection(_reg(["default", "a", "b"], "default"), "b", "t")
    assert ei.value.status_code == 404


def test_effective_collection_default_when_permitted(monkeypatch):
    monkeypatch.setattr(settings, "tenant_collections", {"t": ["default", "a"]})
    assert _effective_collection(_reg(["default", "a"], "default"), None, "t") == "default"


def test_effective_collection_falls_back_to_first_allowed(monkeypatch):
    # registry default not permitted → the tenant's own default is its first allowed
    monkeypatch.setattr(settings, "tenant_collections", {"t": ["b", "a"]})
    assert _effective_collection(_reg(["default", "a", "b"], "default"), None, "t") == "a"


def test_effective_collection_none_accessible_is_404(monkeypatch):
    monkeypatch.setattr(settings, "tenant_collections", {"t": ["z"]})
    with pytest.raises(HTTPException) as ei:
        _effective_collection(_reg(["default", "a"], "default"), None, "t")
    assert ei.value.status_code == 404


def test_unlisted_tenant_is_unrestricted(monkeypatch):
    # feature on for other orgs, but this tenant isn't listed → passthrough
    monkeypatch.setattr(settings, "tenant_collections", {"other": ["x"]})
    assert _effective_collection(_reg(["default", "a"], "default"), "a", "t") == "a"


# --- endpoints (in-memory registry; no real stores) ------------------------- #


def _entry(cid: str, default: bool = False) -> CollectionEntry:
    return CollectionEntry(
        id=cid, label=cid, collection=cid, model="m", dim=8,
        chunk_method="fixed", chunk_size=None, chunk_overlap=None, chunk_params={},
        is_shared_surface=default, retriever=None, vector_store=None, text_index=None, embedder=None,
    )


def _install_registry():
    app.state.collections = CollectionRegistry(
        [_entry("default", True), _entry("col_a"), _entry("col_b")], default_id="default"
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
    assert ids == {"col_a"}  # not "default", not "col_b"
    assert body["default"] == "col_a"  # effective default is the accessible one


@pytest.mark.asyncio
async def test_list_unrestricted_sees_all(client):
    _install_registry()  # feature off (empty map)
    body = (await client.get("/v1/collections")).json()
    assert {c["id"] for c in body["collections"]} == {"default", "col_a", "col_b"}
    assert body["default"] == "default"


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
