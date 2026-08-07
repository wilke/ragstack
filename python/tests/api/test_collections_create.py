"""Phase 3 Step 3: POST /v1/collections (build-time model selection) + DELETE.

Create binds an embedding model + chunk strategy into a new content-addressed
collection; the physical name is derived from (model, dim, chunk) so the same
spec is idempotent (409) and a different chunker mints a distinct collection.
Creation is open to any authenticated principal (ADR-0003 decision 3) — the
``embedding``/``chunk`` build-spec overrides are admin-only, and an omitted
field is resolved from the server-default build spec at create time. Delete
drops the registry binding (still admin-only).
"""
import json

import pytest

from ragstack.api import security
from ragstack.api.security import ROLE_ADMIN
from ragstack.config import settings

pytestmark = pytest.mark.asyncio

EMB = {
    "id": "emb-sfr", "task": "embedding", "provider": "vllm",
    "base_urls": ["http://localhost:9100"], "model": "test/sfr", "dim": 8,
}
LLM = {
    "id": "an-llm", "task": "llm", "provider": "vllm",
    "base_urls": ["http://localhost:9101"], "model": "test/llm",
}
CHUNK = {"method": "fixed_token", "size": 256, "overlap": 32}


@pytest.fixture(autouse=True)
def _admin(monkeypatch):
    # keyless dev → admin, so both the model-registry and create surfaces are reachable
    monkeypatch.setattr(security.settings, "api_keys", [])
    monkeypatch.setattr(security.settings, "default_role", ROLE_ADMIN)


async def _register(client, entry):
    r = await client.post("/v1/admin/models/registry", json=entry)
    assert r.status_code == 201, r.text


async def test_create_then_listed(client):
    await _register(client, EMB)
    r = await client.post(
        "/v1/collections", json={"embedding": "emb-sfr", "chunk": CHUNK, "label": "SFR 256"}
    )
    assert r.status_code == 201, r.text
    info = r.json()
    assert info["model"] == "test/sfr" and info["dim"] == 8
    assert info["chunk_method"] == "fixed_token" and info["chunk_size"] == 256
    assert info["default"] is False and info["label"] == "SFR 256"
    cid = info["id"]
    listed = (await client.get("/v1/collections")).json()["collections"]
    assert cid in {c["id"] for c in listed}


async def test_created_entry_retriever_is_collection_scoped(client):
    """#209: a runtime-created collection's retriever must be bound to its own
    physical collection, or its graph leg would fuse every other collection's
    triples (the graph store is shared; only the vector/text stores are not)."""
    await _register(client, EMB)
    r = await client.post("/v1/collections", json={"embedding": "emb-sfr", "chunk": CHUNK})
    assert r.status_code == 201, r.text

    from ragstack.api.main import app

    entry = app.state.collections.resolve(r.json()["id"])
    assert entry.retriever.collection == entry.collection


async def test_create_unknown_model_404(client):
    r = await client.post("/v1/collections", json={"embedding": "ghost", "chunk": CHUNK})
    assert r.status_code == 404


async def test_create_wrong_task_400(client):
    await _register(client, LLM)  # an llm, not an embedding model
    r = await client.post("/v1/collections", json={"embedding": "an-llm", "chunk": CHUNK})
    assert r.status_code == 400


async def test_create_bad_chunk_method_400(client):
    await _register(client, EMB)
    r = await client.post(
        "/v1/collections", json={"embedding": "emb-sfr", "chunk": {"method": "nope"}}
    )
    assert r.status_code == 400


async def test_create_is_content_addressed_and_idempotent(client):
    await _register(client, EMB)
    a = await client.post("/v1/collections", json={"embedding": "emb-sfr", "chunk": CHUNK})
    assert a.status_code == 201
    # same (model, dim, chunk) → same derived id → 409
    dup = await client.post("/v1/collections", json={"embedding": "emb-sfr", "chunk": CHUNK})
    assert dup.status_code == 409
    # a different chunker → a distinct collection → 201
    other = await client.post(
        "/v1/collections",
        json={"embedding": "emb-sfr", "chunk": {"method": "fixed_token", "size": 512, "overlap": 64}},
    )
    assert other.status_code == 201
    assert other.json()["id"] != a.json()["id"]


# --- named libraries must not share a physical store ------------------------ #


async def test_named_collections_with_same_spec_get_distinct_stores(
    client, monkeypatch, tmp_path
):
    """The isolation bug: `andy`, `open-access` and `test2` were all created with
    the same embedding model + chunker, so all three derived the SAME physical
    Qdrant collection / ES index and reported identical counts — aliases over one
    store. An explicit id must mint its own store."""
    from ragstack.api.collections import CollectionSpec
    from ragstack.api.main import app

    f = tmp_path / "libs.collections.json"
    monkeypatch.setattr(settings, "collections_file", str(f))
    await _register(client, EMB)
    ids = ["andy", "open-access", "test2"]
    for cid in ids:
        r = await client.post(
            "/v1/collections", json={"embedding": "emb-sfr", "chunk": CHUNK, "id": cid}
        )
        assert r.status_code == 201, r.text
        assert r.json()["id"] == cid

    physicals = [app.state.collections.resolve(cid).collection for cid in ids]
    assert len(set(physicals)) == 3, physicals
    # ...and each name is still diagnosable back to its library + build spec.
    for p in physicals:
        assert p.startswith("ragstack_lib_") and "fixed_token" in p and "_8_" in p

    # The ES index rides on the same name (CollectionSpec.es_index() is
    # `text_index or collection`, and create pins text_index to the physical name),
    # so the text side is isolated by the same fix. Read it back off the persisted
    # specs rather than the built entry, which doesn't carry the index name.
    specs = [CollectionSpec.model_validate(d) for d in json.loads(f.read_text())]
    indices = [s.es_index() for s in specs if s.id in ids]
    assert len(indices) == 3 and len(set(indices)) == 3, indices
    assert set(indices) == set(physicals)


async def test_named_collection_differs_from_content_addressed_one(client):
    """An id'd library and the anonymous content-addressed corpus built from the
    same spec are different data and must not land in the same store."""
    from ragstack.api.main import app

    await _register(client, EMB)
    anon = await client.post("/v1/collections", json={"embedding": "emb-sfr", "chunk": CHUNK})
    named = await client.post(
        "/v1/collections", json={"embedding": "emb-sfr", "chunk": CHUNK, "id": "andy"}
    )
    assert anon.status_code == 201 and named.status_code == 201
    a = app.state.collections.resolve(anon.json()["id"])
    b = app.state.collections.resolve("andy")
    assert a.collection != b.collection


async def test_named_collection_duplicate_id_is_still_409(client):
    await _register(client, EMB)
    first = await client.post(
        "/v1/collections", json={"embedding": "emb-sfr", "chunk": CHUNK, "id": "andy"}
    )
    assert first.status_code == 201
    dup = await client.post(
        "/v1/collections", json={"embedding": "emb-sfr", "chunk": CHUNK, "id": "andy"}
    )
    assert dup.status_code == 409
    # ...even with a *different* build spec: the registry id is the unique key.
    dup2 = await client.post(
        "/v1/collections",
        json={
            "embedding": "emb-sfr",
            "chunk": {"method": "fixed_token", "size": 512, "overlap": 64},
            "id": "andy",
        },
    )
    assert dup2.status_code == 409


# --- ADR-0003: creation open to `user`; build-spec overrides admin-only ----- #


async def test_user_creates_with_server_default_spec(client, monkeypatch):
    """A non-admin creating with only {id, label} gets a 201 whose spec IS the
    server-default build spec — resolved to concrete values at create time, not
    left as Nones for ingest-time fallback."""
    monkeypatch.setattr(security.settings, "default_role", "user")
    r = await client.post("/v1/collections", json={"id": "mylib", "label": "My lib"})
    assert r.status_code == 201, r.text
    info = r.json()
    assert info["id"] == "mylib" and info["label"] == "My lib"
    assert info["model"] == settings.embedding_model
    assert info["dim"] == settings.embedding_model_dim
    assert info["chunk_method"] == settings.chunk_method
    assert info["chunk_size"] == settings.chunk_size

    from ragstack.api.main import app

    entry = app.state.collections.resolve("mylib")
    assert entry.chunk_overlap == settings.chunk_overlap


async def test_user_supplying_chunk_is_403(client, monkeypatch):
    monkeypatch.setattr(security.settings, "default_role", "user")
    r = await client.post("/v1/collections", json={"chunk": CHUNK, "id": "nope"})
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert "admin-only" in detail and "server-default" in detail


async def test_user_supplying_embedding_is_403(client, monkeypatch):
    monkeypatch.setattr(security.settings, "default_role", "user")
    r = await client.post("/v1/collections", json={"embedding": "emb-sfr"})
    assert r.status_code == 403


async def test_admin_supplying_chunk_still_works(client):
    await _register(client, EMB)
    r = await client.post("/v1/collections", json={"embedding": "emb-sfr", "chunk": CHUNK})
    assert r.status_code == 201, r.text
    assert r.json()["chunk_method"] == "fixed_token" and r.json()["chunk_size"] == 256


async def test_omitted_chunk_content_addresses_like_explicit_defaults(client):
    """Defaults are resolved BEFORE the content-address is computed, so `chunk
    omitted` and `chunk == the explicit server defaults` are the SAME collection
    (409), not two physical stores for one effective build."""
    a = await client.post("/v1/collections", json={})
    assert a.status_code == 201, a.text
    explicit = {
        "method": settings.chunk_method,
        "size": settings.chunk_size,
        "overlap": settings.chunk_overlap,
    }
    dup = await client.post("/v1/collections", json={"chunk": explicit})
    assert dup.status_code == 409, dup.text


async def test_recreate_named_default_spec_with_different_explicit_spec_is_409(client):
    """The dup guard keeps firing across the default/explicit boundary: a named
    collection minted from the server-default spec cannot be re-created under the
    same id with a different explicit spec."""
    await _register(client, EMB)
    first = await client.post("/v1/collections", json={"id": "andy3"})
    assert first.status_code == 201, first.text
    dup = await client.post(
        "/v1/collections", json={"id": "andy3", "embedding": "emb-sfr", "chunk": CHUNK}
    )
    assert dup.status_code == 409


async def test_empty_string_id_takes_the_content_addressed_alias_guard(client):
    """``{"id": ""}`` is treated as *omitted* everywhere else (``cid``, the
    physical name), so it must also take the content-addressed sharers guard —
    it previously checked ``body.id is None`` and let an empty-string id
    register a second entry over another collection's physical store (silent
    aliasing: ingest writes into the other collection, purge destroys it)."""
    import dataclasses

    from ragstack.api.main import app

    first = await client.post("/v1/collections", json={})
    assert first.status_code == 201, first.text
    physical = first.json()["id"]
    registry = app.state.collections
    # Re-register the same physical store under a DIFFERENT registry id — the
    # shape the guard defends against (e.g. a seeded default entry whose id
    # differs from the derived store name).
    entry = registry.resolve(physical)
    assert registry.remove(physical)
    registry.add(dataclasses.replace(entry, id="seeded-alias"))

    for body in ({}, {"id": ""}):
        dup = await client.post("/v1/collections", json=body)
        assert dup.status_code == 409, (body, dup.text)
        assert "seeded-alias" in dup.json()["detail"]


async def test_collection_cap_is_enforced(client, monkeypatch):
    """ADR-0003 calls the collection count the binding physical constraint, so
    POST /v1/collections *enforces* ``max_collections`` (creation is open to any
    authenticated principal — without a cap, looping the endpoint mints physical
    Qdrant/ES stores until the instance fails). Applies to admins too."""
    from ragstack.api.main import app

    n = len(app.state.collections.entries())
    monkeypatch.setattr(settings, "max_collections", n + 1)
    ok = await client.post("/v1/collections", json={"id": "under-cap"})
    assert ok.status_code == 201, ok.text
    blocked = await client.post("/v1/collections", json={"id": "over-cap"})
    assert blocked.status_code == 403
    assert "collection limit reached" in blocked.json()["detail"]
    # 0 disables the cap.
    monkeypatch.setattr(settings, "max_collections", 0)
    open_again = await client.post("/v1/collections", json={"id": "over-cap"})
    assert open_again.status_code == 201, open_again.text


async def test_create_persists_to_collections_file(client, monkeypatch, tmp_path):
    f = tmp_path / "acme.collections.json"
    monkeypatch.setattr(settings, "collections_file", str(f))
    await _register(client, EMB)
    cid = (
        await client.post("/v1/collections", json={"embedding": "emb-sfr", "chunk": CHUNK})
    ).json()["id"]
    data = json.loads(f.read_text())
    match = [d for d in data if d["id"] == cid]
    assert match and match[0]["embedding_model"] == "test/sfr"
    assert match[0]["chunk_overlap"] == 32 and match[0]["embedding_model_dim"] == 8


async def test_delete_drops_binding(client):
    await _register(client, EMB)
    cid = (
        await client.post("/v1/collections", json={"embedding": "emb-sfr", "chunk": CHUNK})
    ).json()["id"]
    d = await client.delete(f"/v1/collections/{cid}?purge=true")
    assert d.status_code == 200
    listed = {c["id"] for c in (await client.get("/v1/collections")).json()["collections"]}
    assert cid not in listed


async def test_delete_default_is_409(client):
    r = await client.delete("/v1/collections/default")
    assert r.status_code == 409


async def test_delete_unknown_is_404(client):
    r = await client.delete("/v1/collections/nope")
    assert r.status_code == 404
