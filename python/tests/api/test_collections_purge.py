"""DELETE /v1/collections/{id}?purge=true — the data-destroying delete.

The default delete drops the registry *binding* only, which is why every
create/delete cycle in the UI leaves an orphaned Qdrant collection + ES index +
manifest behind. ``purge=true`` opts in to destroying those too.

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
from ragstack.models import Chunk
from ragstack.provenance import CollectionManifest, read_manifest, write_manifest
from ragstack.stores import InMemoryTextIndex, InMemoryVectorStore

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
        is_default=False, retriever=None, vector_store=vs, text_index=ti,
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


async def test_purge_removes_vectors_text_and_manifest(client, manifests):
    _, vs, ti = _add("phys_purge_me", cid="purge-me")
    await _populate(vs, ti)
    _manifest(manifests, "phys_purge_me")

    r = await client.delete("/v1/collections/purge-me?purge=true")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["purged"] is True and body["ok"] is True
    assert body["store"] == "phys_purge_me" and body["text_index"] == "phys_purge_me"
    assert set(body["deleted"]) == {"registry", "vectors", "text_index", "manifest"}
    assert body["absent"] == [] and body["failed"] == []

    # the data itself, not just the report
    assert await vs.count_tenants(["default"]) == 0
    assert await ti.count_tenants(["default"]) == 0
    assert read_manifest(str(manifests), "phys_purge_me") is None
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


async def test_default_delete_still_only_unregisters(client, manifests):
    """The pre-existing contract: no ``purge`` → 204, and every byte survives."""
    _, vs, ti = _add("phys_keep", cid="keep-data")
    await _populate(vs, ti)
    _manifest(manifests, "phys_keep")

    r = await client.delete("/v1/collections/keep-data")
    assert r.status_code == 204 and r.content == b""
    assert await vs.count_tenants(["default"]) == 1
    assert await ti.count_tenants(["default"]) == 1
    assert read_manifest(str(manifests), "phys_keep") is not None


async def test_explicit_purge_false_is_also_unregister_only(client, manifests):
    _, vs, _ = _add("phys_keep2", cid="keep-data-2")
    await _populate(vs, InMemoryTextIndex())
    r = await client.delete("/v1/collections/keep-data-2?purge=false")
    assert r.status_code == 204
    assert await vs.count_tenants(["default"]) == 1


# --- guards ---------------------------------------------------------------- #


async def test_purging_the_default_is_409(client, manifests):
    r = await client.delete("/v1/collections/default?purge=true")
    assert r.status_code == 409
    assert "default" in r.json()["detail"]


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
    monkeypatch.setattr(security.settings, "default_role", "researcher")
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
    assert set(body["absent"]) == {"vectors", "text_index", "manifest"}


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
    assert set(r.json()["absent"]) == {"vectors", "text_index", "manifest"}


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
