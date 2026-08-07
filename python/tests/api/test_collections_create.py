"""Phase 3 Step 3: POST /v1/collections (build-time model selection) + DELETE.

Create binds an embedding model + chunk strategy into a new content-addressed
collection; the physical name is derived from (model, dim, chunk) so the same
spec is idempotent (409) and a different chunker mints a distinct collection.
Creation is open to any authenticated principal (ADR-0003 decision 3) — the
``embedding``/``chunk`` build-spec overrides are admin-only, and an omitted
field is resolved from the server-default build spec at create time. Delete
drops the registry binding (still admin-only).
"""
import asyncio
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


# --- the cap counts the DURABLE registry (#286) ----------------------------- #


def _seed_durable(monkeypatch, tmp_path, n: int, name: str = "seeded.collections.json"):
    """Point ``collections_file`` at a registry that already holds ``n`` specs —
    the state a sibling API process, the bulk CLI, or a hand edit would leave.
    This process's ``app.state.collections`` knows about none of them."""
    from ragstack.collection_store import CollectionSpec as StoreSpec

    f = tmp_path / name
    rows = [
        StoreSpec(
            id=f"sibling-{i}", collection=f"phys_{i}", embedding_model_dim=8,
            chunk_method="fixed_token", chunk_size=256,
        ).model_dump()
        for i in range(n)
    ]
    f.write_text(json.dumps(rows, indent=2))
    monkeypatch.setattr(settings, "collections_file", str(f))
    return f


async def test_cap_counts_the_durable_store_not_the_in_process_registry(
    client, monkeypatch, tmp_path
):
    """The cross-process bug in one assertion: three specs are already registered
    durably while this process's registry holds exactly one entry. Counting
    ``len(registry.entries())`` sees 1 and creates happily — which is how two APIs
    over one Qdrant permitted ~2x the advertised cap (#286 item 2)."""
    from ragstack.api.main import app

    assert len(app.state.collections.entries()) == 1
    _seed_durable(monkeypatch, tmp_path, 3)
    monkeypatch.setattr(settings, "max_collections", 3)

    r = await client.post("/v1/collections", json={"id": "one-too-many"})
    assert r.status_code == 403, r.text
    assert "collection limit reached" in r.json()["detail"]


async def test_concurrent_creates_cannot_exceed_the_cap(client, monkeypatch, tmp_path):
    """The TOCTOU: ten concurrent creates with ONE slot left → exactly one 201.

    Before the fix the capacity check sat two network round-trips
    (``ensure_collection`` + ``ensure_index``) before the insert it authorized, so
    every in-flight request saw the same pre-create count and all ten passed. The
    check now happens *inside* the durable insert, so nine must be refused."""
    _seed_durable(monkeypatch, tmp_path, 2)
    # One shared-surface entry (`default`) reserves a slot, so 4 - 1 = 3 durable
    # slots against 2 seeded specs: exactly one free.
    monkeypatch.setattr(settings, "max_collections", 4)

    results = await asyncio.gather(
        *(client.post("/v1/collections", json={"id": f"racer-{i}"}) for i in range(10))
    )
    codes = sorted(r.status_code for r in results)
    assert codes == [201] + [403] * 9, [(r.status_code, r.text) for r in results]


async def test_a_lost_race_does_not_overwrite_the_winners_spec(
    client, monkeypatch, tmp_path
):
    """``put`` upserted, so a second creator racing one id silently re-pointed the
    first one's registry entry at its own physical store — leaving the first
    store claimed by nobody (ADR-0002 decision 5). ``create`` refuses instead."""
    from ragstack.api.main import app
    from ragstack.collection_store import CollectionSpec as StoreSpec

    f = _seed_durable(monkeypatch, tmp_path, 0)
    monkeypatch.setattr(settings, "max_collections", 0)  # cap disabled; this is about the id
    ok = await client.post("/v1/collections", json={"id": "contested"})
    assert ok.status_code == 201, ok.text
    winner = [d for d in json.loads(f.read_text()) if d["id"] == "contested"][0]

    # A sibling process's attempt at the same id: the registry entry this process
    # holds is invisible to it, so it reaches the durable insert.
    app.state.collections.remove("contested")
    dup = await client.post("/v1/collections", json={"id": "contested"})
    assert dup.status_code == 409, dup.text
    rows = [d for d in json.loads(f.read_text()) if d["id"] == "contested"]
    assert len(rows) == 1
    assert StoreSpec.model_validate(rows[0]) == StoreSpec.model_validate(winner)


async def test_shared_surface_reserves_a_slot(client, monkeypatch, tmp_path):
    """The synthetic ``default`` pointer is not a durable row, so the durable
    count cannot see it — charge it a slot explicitly, or the advertised cap
    would silently be one higher than it says (#286 item 4, inverted)."""
    from ragstack.api.main import app

    assert all(e.is_shared_surface for e in app.state.collections.entries())
    _seed_durable(monkeypatch, tmp_path, 2)
    monkeypatch.setattr(settings, "max_collections", 3)  # 3 - 1 reserved = 2 = full
    blocked = await client.post("/v1/collections", json={"id": "nope"})
    assert blocked.status_code == 403, blocked.text

    monkeypatch.setattr(settings, "max_collections", 4)  # 4 - 1 = 3 > 2 = room
    ok = await client.post("/v1/collections", json={"id": "yes"})
    assert ok.status_code == 201, ok.text


async def test_max_collections_1_with_a_shared_surface_refuses_everything(
    client, monkeypatch, tmp_path
):
    """The sentinel edge case. ``MAX_COLLECTIONS=0`` means DISABLED, but
    ``limit - reserved == 0`` must mean REFUSE EVERYTHING — collapsing the two
    into one int turns the tightest possible cap into an unlimited one."""
    _seed_durable(monkeypatch, tmp_path, 0)  # an EMPTY durable registry
    monkeypatch.setattr(settings, "max_collections", 1)
    r = await client.post("/v1/collections", json={"id": "only-one"})
    assert r.status_code == 403, r.text
    assert "collection limit reached (1)" in r.json()["detail"]

    # ...while 0 really is "no cap", against the same empty store.
    monkeypatch.setattr(settings, "max_collections", 0)
    assert (await client.post("/v1/collections", json={"id": "only-one"})).status_code == 201


async def test_cap_falls_back_to_the_registry_without_a_durable_store(
    client, monkeypatch
):
    """No ``collections_file`` → the store cannot hold a reservation, so the cap
    degrades to today's in-process count rather than failing open."""
    from ragstack.api.main import app

    monkeypatch.setattr(settings, "collections_file", "")
    monkeypatch.setattr(settings, "collections_json", "")
    n = len(app.state.collections.entries())
    monkeypatch.setattr(settings, "max_collections", n)
    r = await client.post("/v1/collections", json={"id": "over-cap"})
    assert r.status_code == 403, r.text


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


async def test_a_build_failure_withdraws_the_durable_reservation(
    client, monkeypatch, tmp_path
):
    """Reserving before the build inverts the crash window deliberately — but a
    build that RAISES must not leave the spec behind. It would consume a cap slot
    and, having no owner row yet, `enforce_access(..., "owner")` would refuse
    everyone but an admin: the creator could not delete what they just failed to
    create. Repeated failures would let any caller eat the whole budget with
    collections nobody can remove."""
    import json as _json

    from ragstack.api.routers import collections as router_mod

    f = _seed_durable(monkeypatch, tmp_path, 0, name="buildfail.collections.json")

    async def _boom(*a, **k):
        raise RuntimeError("qdrant unreachable")

    monkeypatch.setattr(router_mod, "build_collection_entry", _boom)
    with pytest.raises(RuntimeError):
        await client.post("/v1/collections", json={"id": "build-fails"})

    rows = _json.loads(f.read_text())
    assert not any(r["id"] == "build-fails" for r in rows), (
        "the durable reservation survived a failed build — it consumes a cap slot "
        "and has no owner row, so nobody but an admin can remove it"
    )


async def test_a_cancelled_build_withdraws_the_reservation(client, monkeypatch, tmp_path):
    """`asyncio.CancelledError` is a BaseException, so `except Exception` around
    the build did not catch it — and a cancellation (client disconnect, server
    timeout) is the MOST likely build failure under load, because the build is
    the slow part: two network round-trips ensuring the Qdrant collection and the
    ES index. Catching only Exception left exactly the leak the handler exists to
    prevent: a spec with no owner row, holding a cap slot nobody but an admin can
    reclaim."""
    import json as _json

    from ragstack.api.routers import collections as router_mod

    f = _seed_durable(monkeypatch, tmp_path, 0, name="cancelled.collections.json")

    async def _cancel(*a, **k):
        raise asyncio.CancelledError()

    monkeypatch.setattr(router_mod, "build_collection_entry", _cancel)
    with pytest.raises(asyncio.CancelledError):
        await client.post("/v1/collections", json={"id": "cancelled-build"})

    rows = _json.loads(f.read_text())
    assert not any(r["id"] == "cancelled-build" for r in rows), (
        "a cancelled build leaked the durable reservation"
    )
