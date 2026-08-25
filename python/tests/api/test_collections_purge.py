"""DELETE /v1/collections/{id}?purge=true — the data-destroying delete.

The default delete drops the registry *binding* only, which is why every
create/delete cycle in the UI leaves an orphaned Qdrant collection + ES index +
manifest behind. ``purge=true`` opts in to destroying those too — and, since
#380 (closing #295), the collection's knowledge-graph triples: the ``graph``
target, reported like the others and present whenever the app has a graph
store (the test app always does).

These tests drive the registry directly (rather than through POST /v1/collections)
because the created entry would otherwise carry a real ``QdrantVectorStore``
pointing at localhost — the purge would then depend on whether a Qdrant happens
to be running. In-memory doubles make "the data is actually gone" assertable.
"""
import pytest

from ragstack.api import security
from ragstack.api.collections import CollectionEntry
from ragstack.api.main import app
from ragstack.api.security import ROLE_ADMIN
from ragstack.config import settings
from ragstack.models import Chunk, Triple
from ragstack.provenance import CollectionManifest, read_manifest, write_manifest
from ragstack.stores import InMemoryTextIndex, InMemoryVectorStore
from tests.api.conftest import SHARED_ID

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _admin(monkeypatch):
    monkeypatch.setattr(security.settings, "api_keys", [])
    monkeypatch.setattr(security.settings, "default_role", ROLE_ADMIN)


@pytest.fixture
def manifests(monkeypatch, tmp_path):
    """An isolated manifest dir — the purge deletes files in it."""
    d = tmp_path / "manifests"
    d.mkdir()
    monkeypatch.setattr(settings, "collection_manifest_dir", str(d))
    return d


def _chunk(cid: str) -> Chunk:
    return Chunk(id=cid, doc_id="d1", content="hello world", embedding=[0.1, 0.2, 0.3, 0.4])


def _add(collection: str, *, cid: str, text_index: str = ""):
    """Register an entry over in-memory doubles and return (entry, vs, ti)."""
    vs, ti = InMemoryVectorStore(), InMemoryTextIndex()
    entry = CollectionEntry(
        id=cid, label=cid, collection=collection, model="test-model", dim=4,
        chunk_method="fixed", chunk_size=256, chunk_overlap=32, chunk_params={},
        is_shared_surface=False, retriever=None, vector_store=vs, text_index=ti,
        text_index_name=text_index,
    )
    app.state.collections.add(entry)
    return entry, vs, ti


async def _populate(vs, ti) -> None:
    await vs.upsert([_chunk("c1")])
    await ti.index([_chunk("c1")])


def _manifest(manifest_dir, collection: str) -> None:
    write_manifest(
        str(manifest_dir),
        CollectionManifest(collection=collection, model="test-model", dim=4, chunk_count=1),
    )


# --- the happy path -------------------------------------------------------- #


def _triple(collection: str, *, tenant: str = "default", doc_id: str = "d1") -> Triple:
    return Triple(subject="Alice", predicate="knows", object="Bob",
                  doc_id=doc_id, tenant_id=tenant, collection=collection)


async def test_purge_removes_vectors_text_graph_and_manifest(client, manifests):
    _, vs, ti = _add("phys_purge_me", cid="purge-me")
    await _populate(vs, ti)
    _manifest(manifests, "phys_purge_me")
    graph = app.state.graph_store
    # Triples in the purged collection from TWO tenants (the purge is
    # collection-wide, like the store drops), plus a sibling collection's
    # triple for the same doc id that must survive (#209).
    await graph.add_triples([
        _triple("phys_purge_me"), _triple("phys_purge_me", tenant="other", doc_id="d2"),
        _triple("phys_sibling"),
    ])

    r = await client.delete("/v1/collections/purge-me?purge=true")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["purged"] is True and body["ok"] is True
    assert body["store"] == "phys_purge_me" and body["text_index"] == "phys_purge_me"
    assert set(body["deleted"]) == {"registry", "vectors", "text_index", "graph", "manifest"}
    assert body["absent"] == [] and body["failed"] == []

    # the data itself, not just the report
    assert await vs.count_tenants(["default"]) == 0
    assert await ti.count_tenants(["default"]) == 0
    assert read_manifest(str(manifests), "phys_purge_me") is None
    assert await graph.stats(tenant_id=None, collection="phys_purge_me") == (0, 0)
    assert await graph.stats(tenant_id=None, collection="phys_sibling") == (2, 1)


async def test_purge_reports_a_failed_graph_delete_and_still_drops_the_rest(client, manifests):
    """The graph leg is best-effort like the others: a backend outage is named
    under ``failed`` as ``graph``, the store drops that landed are reported as
    landed, and the binding is gone either way."""
    _, vs, ti = _add("phys_graph_down", cid="graph-down")
    await _populate(vs, ti)
    _manifest(manifests, "phys_graph_down")

    class _Down:
        async def delete_collection(self, tenant_id, collection):
            raise RuntimeError("ServiceUnavailable: graph backend unreachable")

    app.state.graph_store = _Down()
    r = await client.delete("/v1/collections/graph-down?purge=true")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert set(body["deleted"]) == {"registry", "vectors", "text_index", "manifest"}
    assert [f["target"] for f in body["failed"]] == ["graph"]
    assert "graph backend unreachable" in body["failed"][0]["error"]
    assert await vs.count_tenants(["default"]) == 0


async def test_purge_without_a_graph_store_reports_no_graph_target(client, manifests):
    """``graph_backend=disabled``: nothing to drop, so the report does not
    claim a target it never attempted (neither deleted nor absent)."""
    _, vs, ti = _add("phys_no_graph", cid="no-graph")
    await _populate(vs, ti)
    app.state.graph_store = None
    r = await client.delete("/v1/collections/no-graph?purge=true")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "graph" not in body["deleted"] and "graph" not in body["absent"]
    assert body["failed"] == [] and body["ok"] is True
    listed = {c["id"] for c in (await client.get("/v1/collections")).json()["collections"]}
    assert "purge-me" not in listed


async def test_purge_reports_the_separate_es_index_name(client, manifests):
    """``text_index`` may name a different index than ``collection``; the report
    has to say which index was actually dropped, not assume they match."""
    _add("phys_split", cid="split", text_index="phys_split_bm25")
    r = await client.delete("/v1/collections/split?purge=true")
    assert r.status_code == 200, r.text
    assert r.json()["store"] == "phys_split"
    assert r.json()["text_index"] == "phys_split_bm25"


# --- purge=false is unchanged ---------------------------------------------- #


async def test_unregistering_a_solo_store_is_refused(client, manifests):
    """THE FIX (#285). `purge=false` used to drop the binding and leave the
    Qdrant collection and ES index behind — data no registry entry claimed, and
    therefore no ACL governed. `create -> delete -> repeat` leaked a physical
    store pair per iteration while returning the registry count to baseline, so
    MAX_COLLECTIONS never fired.

    ADR-0002 decision 5 says a physical index has EXACTLY one registry entry.
    #279 enforced "not two"; this is "not zero"."""
    _, vs, ti = _add("phys_keep", cid="keep-data")
    await _populate(vs, ti)
    _manifest(manifests, "phys_keep")

    r = await client.delete("/v1/collections/keep-data")
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "phys_keep" in detail  # names the store that would be stranded
    assert "purge=true" in detail  # and the way out

    # ...and NOTHING was mutated on the way to the refusal. A guard that fires
    # after a partial delete would be worse than no guard.
    listed = {c["id"] for c in (await client.get("/v1/collections")).json()["collections"]}
    assert "keep-data" in listed
    assert await vs.count_tenants(["default"]) == 1
    assert await ti.count_tenants(["default"]) == 1
    assert read_manifest(str(manifests), "phys_keep") is not None


async def test_explicit_purge_false_is_refused_the_same_way(client, manifests):
    _, vs, _ = _add("phys_keep2", cid="keep-data-2")
    await _populate(vs, InMemoryTextIndex())
    r = await client.delete("/v1/collections/keep-data-2?purge=false")
    assert r.status_code == 409
    assert await vs.count_tenants(["default"]) == 1


async def test_the_api_cannot_produce_an_orphan(client, manifests):
    """The property the guard exists for: whichever delete you choose, you never
    end up with a physical store that no registry entry claims."""
    _, vs, ti = _add("phys_prop", cid="prop")
    await _populate(vs, ti)

    # Unregister-only is refused...
    assert (await client.delete("/v1/collections/prop")).status_code == 409
    assert await vs.count_tenants(["default"]) == 1  # still claimed, still there

    # ...and the delete that IS allowed takes the data with it.
    r = await client.delete("/v1/collections/prop?purge=true")
    assert r.status_code == 200, r.text
    assert await vs.count_tenants(["default"]) == 0
    assert await ti.count_tenants(["default"]) == 0


async def test_a_non_owner_gets_the_ownership_error_not_the_orphan_error(client, manifests):
    """Ordering matters: the new 409 must never fire for a caller who cannot
    read the collection, or it becomes an existence oracle for a private id."""
    from ragstack.api import security
    from ragstack.api.security import ROLE_USER

    _add("phys_private", cid="private-thing")
    security.settings.default_role = ROLE_USER  # drop out of the admin bypass
    try:
        r = await client.delete("/v1/collections/private-thing")
    finally:
        security.settings.default_role = ROLE_ADMIN
    assert r.status_code in (403, 404), r.text
    assert "purge=true" not in r.text


# --- guards ---------------------------------------------------------------- #


async def test_purging_the_default_is_409(client, manifests):
    r = await client.delete(f"/v1/collections/{SHARED_ID}?purge=true")
    assert r.status_code == 409
    # The SHARED SURFACE guard, not the pointer guard. These used to be the same
    # check: the pointer always named the settings-derived entry, so `==
    # default_id` incidentally protected the flagship corpus. Once the pointer is
    # configurable that protection would move with it and leave a multi-million
    # point Qdrant collection purgeable, so the surface is guarded on its own.
    assert "shared collection" in r.json()["detail"]


async def test_the_pointer_target_is_also_undeletable_and_says_how_to_free_it(
    client, manifests, monkeypatch
):
    """A repointed default names a REAL, user-owned collection. It must still be
    protected — a request that omits `collection` would otherwise 404 — but its
    owner needs to be told how to get out, which the old message never had to
    say because the target could only ever be the synthetic entry."""
    target, _, _ = _add("phys_pointer", cid="pointed-at")
    assert target.is_shared_surface is False
    monkeypatch.setattr(app.state.collections, "_default_id", target.id)

    r = await client.delete(f"/v1/collections/{target.id}?purge=true")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "omit" in detail
    assert "DEFAULT_COLLECTION_ID" in detail


async def test_purging_a_shared_store_409s_and_names_the_sharer(client, manifests):
    """#228: two content-addressed collections built from an identical spec share
    one physical store. Purging either would silently destroy the other's
    embeddings, so it is refused — and the refusal has to say who else is on it."""
    _, vs, ti = _add("phys_shared", cid="sharer-a")
    await _populate(vs, ti)
    _add("phys_shared", cid="sharer-b")

    r = await client.delete("/v1/collections/sharer-a?purge=true")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "sharer-b" in detail and "phys_shared" in detail
    # nothing destroyed, nothing unregistered
    assert await vs.count_tenants(["default"]) == 1
    listed = {c["id"] for c in (await client.get("/v1/collections")).json()["collections"]}
    assert {"sharer-a", "sharer-b"} <= listed


async def test_shared_es_index_alone_also_blocks_the_purge(client, manifests):
    """Sharing only the *text* index is enough: dropping it would blind the other
    collection's BM25 leg."""
    _add("phys_own_vectors", cid="es-sharer-a", text_index="phys_shared_bm25")
    _add("phys_other_vectors", cid="es-sharer-b", text_index="phys_shared_bm25")
    r = await client.delete("/v1/collections/es-sharer-a?purge=true")
    assert r.status_code == 409 and "es-sharer-b" in r.json()["detail"]


async def test_shared_store_still_unregisters_with_purge_false(client, manifests):
    """The guard is on destroying data, not on dropping a binding — unregistering
    one of two collections sharing a store is exactly how you'd fix the situation."""
    _add("phys_shared_ok", cid="unreg-a")
    _add("phys_shared_ok", cid="unreg-b")
    assert (await client.delete("/v1/collections/unreg-a")).status_code == 204


async def test_purge_requires_admin(client, monkeypatch, manifests):
    _, vs, _ = _add("phys_guarded", cid="guarded")
    await _populate(vs, InMemoryTextIndex())
    monkeypatch.setattr(security.settings, "default_role", "user")
    r = await client.delete("/v1/collections/guarded?purge=true")
    assert r.status_code == 403
    assert await vs.count_tenants(["default"]) == 1


async def test_purging_an_unknown_collection_is_404(client, manifests):
    assert (await client.delete("/v1/collections/nope?purge=true")).status_code == 404


# --- idempotence + honest partial failure ---------------------------------- #


async def test_purging_already_gone_targets_is_not_an_error(client, manifests):
    """An empty collection with no manifest (e.g. purged by hand already, or never
    ingested into): every target is reported ``absent`` and the purge still succeeds."""
    _add("phys_empty", cid="already-empty")
    r = await client.delete("/v1/collections/already-empty?purge=true")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["deleted"] == ["registry"]
    assert set(body["absent"]) == {"vectors", "text_index", "graph", "manifest"}


async def test_repeat_purge_of_the_same_store_is_safe(client, manifests):
    """Re-register the same physical store and purge again — the second pass finds
    nothing and says so, rather than erroring."""
    _, vs, ti = _add("phys_twice", cid="twice-a")
    await _populate(vs, ti)
    _manifest(manifests, "phys_twice")
    assert (await client.delete("/v1/collections/twice-a?purge=true")).status_code == 200

    _add("phys_twice", cid="twice-b")
    r = await client.delete("/v1/collections/twice-b?purge=true")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert set(r.json()["absent"]) == {"vectors", "text_index", "graph", "manifest"}


class _BrokenTextIndex(InMemoryTextIndex):
    async def drop_index(self) -> bool:
        raise RuntimeError("index_not_found_exception: no such index")


async def test_partial_failure_reports_both_sides_honestly(client, manifests):
    """Qdrant drops, ES blows up. The three deletions that landed must be reported
    as landed (they cannot be rolled back), and the failure named — not swallowed
    into a 500 that leaves the operator guessing what state they're in."""
    entry, vs, _ = _add("phys_partial", cid="partial")
    entry.text_index = _BrokenTextIndex()
    await vs.upsert([_chunk("c1")])
    _manifest(manifests, "phys_partial")

    r = await client.delete("/v1/collections/partial?purge=true")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert set(body["deleted"]) == {"registry", "vectors", "manifest"}
    assert [f["target"] for f in body["failed"]] == ["text_index"]
    assert "index_not_found_exception" in body["failed"][0]["error"]
    # the successful drops really happened; the purge did not roll back
    assert await vs.count_tenants(["default"]) == 0
    assert read_manifest(str(manifests), "phys_partial") is None
    # and the binding is gone either way, so nothing writes into the orphan
    listed = {c["id"] for c in (await client.get("/v1/collections")).json()["collections"]}
    assert "partial" not in listed


class _NoDropVectorStore(InMemoryVectorStore):
    drop_collection = None  # type: ignore[assignment]


async def test_backend_without_drop_support_is_reported_not_crashed(client, manifests):
    entry, _, _ = _add("phys_nodrop", cid="nodrop")
    entry.vector_store = _NoDropVectorStore()
    r = await client.delete("/v1/collections/nodrop?purge=true")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["failed"][0]["target"] == "vectors"
    assert "does not support" in body["failed"][0]["error"]


async def test_the_two_forms_are_exact_complements(client, manifests):
    """The property the guards rest on: for ANY collection, exactly one of
    purge/unregister is permitted — never both, never neither.

    An earlier version tested the two legs with `and` here while
    `_shared_store_users` uses `or`, so a HALF-shared entry (one leg claimed,
    one not) was refused both ways and became permanently undeletable. Both
    branches now ask the same question."""
    # Half-shared: same ES index, different Qdrant collections. Reachable at
    # runtime, because the create path's alias guard only checks the vector leg.
    _add("phys_a", cid="half-a", text_index="shared_es")
    _add("phys_b", cid="half-b", text_index="shared_es")

    for cid in ("half-a", "half-b"):
        unreg = await client.delete(f"/v1/collections/{cid}")
        purge = await client.delete(f"/v1/collections/{cid}?purge=true")
        allowed = [r.status_code for r in (unreg, purge) if r.status_code < 300]
        assert len(allowed) == 1, (
            f"{cid}: expected exactly one permitted form, got "
            f"unregister={unreg.status_code} purge={purge.status_code}"
        )
        break  # the first delete mutates the registry; one pass is the assertion
