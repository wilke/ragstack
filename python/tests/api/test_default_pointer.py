"""`default` is a POINTER, not a registry entry (#276, ADR-0002 decision 5).

``default`` itself is a reserved, uncreatable id; saying
``collection="default"`` means the same as omitting it. The pointer names
``DEFAULT_COLLECTION_ID`` (else the settings-derived collection, registered
under its own physical name).

**Amended by #419.** This used to say an omitted ``collection`` resolves "at
request time, one dict lookup — to ``DEFAULT_COLLECTION_ID``". The REGISTRY
still resolves an id in one dict lookup, and that is all this file's
``_CountingStore`` assertion ever measured. But ``DEFAULT_COLLECTION_ID`` is the
GLOBAL pointer, and it is no longer by itself what a request TARGETS: a caller
who cannot read the pointer target now gets their own first readable collection
(:mod:`ragstack.api.default_collection`), which costs one batched ACL round trip
on the implicit path.

Every test in this file runs KEYLESS, where ``filter_readable`` is a no-op, so
the pointer is still the answer here and every assertion below still holds
unchanged. That is also exactly why
``test_resolving_an_omitted_collection_makes_no_registry_store_call`` could not
have caught the change: it counts the COLLECTION store, not the ACL store. The
caller-aware behaviour is pinned in
``tests/api/test_default_collection_resolution.py``.

The two flags this keeps apart:

* **is the default** — ``entry.id == registry.default_id``, reported as
  ``CollectionInfo.is_default`` (and ``default``). Moves when the pointer moves.
* **is the legacy shared surface** — ``CollectionEntry.is_shared_surface``. Carries
  the authz exemptions (read-not-write on the omitted-collection ingest and
  document-delete paths; no share-based scope widening) because there per-chunk
  ``tenant_id`` is the isolation. Never moves: pointing ``default`` at an OWNED
  collection must not let its readers write to it by omitting ``collection`` —
  that is the privilege escalation the split exists to prevent.
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
from ragstack.api import deps, security
from ragstack.api.collections import CollectionEntry, CollectionRegistry
from ragstack.api.main import app
from ragstack.api.security import ROLE_ADMIN, ROLE_USER
from ragstack.models import Chunk
from ragstack.retrieval.retriever import HybridRetriever
from ragstack.stores import InMemoryTextIndex, InMemoryVectorStore
from tests.api.conftest import SHARED_ID

pytestmark = pytest.mark.asyncio

KEYS = {"owner": "k-owner", "reader": "k-reader", "admin": "k-admin"}


def _h(who: str) -> dict:
    return {"X-API-Key": KEYS[who]}


@pytest.fixture
def _auth_on(monkeypatch):
    """Three keyed callers (owner / reader / admin) so the ownership seam is
    enforced. Not autouse: the create and listing tests run keyless."""
    monkeypatch.setattr(security.settings, "api_keys", list(KEYS.values()))
    monkeypatch.setattr(
        security.settings, "api_key_tenants",
        {"k-owner": "owner", "k-reader": "reader", "k-admin": "admin"},
    )
    monkeypatch.setattr(security.settings, "api_key_roles", {"k-admin": ROLE_ADMIN})
    monkeypatch.setattr(security.settings, "default_role", ROLE_USER)


def _entry(cid: str, *, shared: bool = False, owner: str = "") -> CollectionEntry:
    """A self-contained entry: its OWN vector store + text index and a retriever
    bound to them, so which entry a request landed on is observable."""
    vs, ti = InMemoryVectorStore(), InMemoryTextIndex()
    return CollectionEntry(
        id=cid, label=cid, collection=cid, model="test-model", dim=4,
        chunk_method="fixed", chunk_size=None, chunk_overlap=None, chunk_params={},
        is_shared_surface=shared, owner=owner,
        retriever=HybridRetriever(vs, ti, app.state.embedder),
        vector_store=vs, text_index=ti, embedder=app.state.embedder,
    )


async def _seed(entry: CollectionEntry, chunk_id: str, tenant: str = "default") -> None:
    chunk = Chunk(
        id=chunk_id, doc_id=f"doc-{chunk_id}", content=f"text of {chunk_id}",
        embedding=[0.1, 0.2, 0.3, 0.4], metadata={"tenant_id": tenant},
    )
    await entry.vector_store.upsert([chunk])
    await entry.text_index.index([chunk])


def _install(entries: list[CollectionEntry], *, pointer: str = "") -> CollectionRegistry:
    """Build the registry the way the lifespan does: the pointer comes from
    ``DEFAULT_COLLECTION_ID`` (``deps._resolve_default_id``), falling back to the
    settings-derived entry — the first one — when unset."""
    app.state.kg_extractor = None
    app.state.doi_enricher = None
    deps.settings.default_collection_id = pointer
    default_id = deps._resolve_default_id(entries, fallback=entries[0].id)
    reg = CollectionRegistry(entries, default_id=default_id)
    app.state.collections = reg
    return reg


@pytest.fixture(autouse=True)
def _restore_pointer_setting(monkeypatch):
    monkeypatch.setattr(deps.settings, "default_collection_id", "")


async def _own(cid: str, subject: str) -> None:
    await get_acl_store().grant(cid, GRANTEE_USER, subject, PERM_OWNER, granted_by=subject)


async def _publish(cid: str) -> None:
    await get_acl_store().grant(
        cid, GRANTEE_GROUP, PUBLIC_GROUP, PERM_READ, granted_by="owner"
    )


# --------------------------------------------------------------------------- #
# reserved, uncreatable
# --------------------------------------------------------------------------- #


async def test_creating_the_reserved_id_is_409(client, monkeypatch):
    monkeypatch.setattr(security.settings, "api_keys", [])
    monkeypatch.setattr(security.settings, "default_role", ROLE_ADMIN)
    r = await client.post("/v1/collections", json={"id": "default", "label": "nope"})
    assert r.status_code == 409, r.text
    assert "pointer" in r.json()["detail"] or "reserved" in r.json()["detail"]
    # ...and nothing was minted under it.
    assert not app.state.collections.has("default")
    assert "default" not in {c["id"] for c in (await client.get("/v1/collections")).json()["collections"]}


async def test_deleting_the_pointer_name_is_409(client, monkeypatch):
    monkeypatch.setattr(security.settings, "api_keys", [])
    monkeypatch.setattr(security.settings, "default_role", ROLE_ADMIN)
    r = await client.delete("/v1/collections/default")
    assert r.status_code == 409, r.text
    assert app.state.collections.has(SHARED_ID)  # the target is untouched


# --------------------------------------------------------------------------- #
# omitted `collection` resolves to the configured id
# --------------------------------------------------------------------------- #


async def test_omitting_collection_resolves_to_the_configured_id(client):
    shared, corpus = _entry(SHARED_ID, shared=True), _entry("corpus-a")
    await _seed(shared, "in-shared")
    await _seed(corpus, "in-corpus")
    _install([shared, corpus], pointer="corpus-a")

    # chunks: read off the pointer target's OWN store.
    r = await client.get("/v1/chunks", params={"ids": "in-corpus,in-shared"})
    assert r.status_code == 200, r.text
    assert [c["chunk_id"] for c in r.json()["chunks"]] == ["in-corpus"]

    # query + retrieve: the pointer target's retriever, so only its corpus.
    for path in ("/v1/query", "/v1/retrieve"):
        r = await client.post(path, json={"query": "text"})
        assert r.status_code == 200, (path, r.text)
        docs = {s["doc_id"] for s in r.json()["sources"]}
        assert docs == {"doc-in-corpus"}, (path, docs)

    # ingest: the job is stamped with the REAL id the pointer resolved to.
    r = await client.post("/v1/ingest", json={"source": "x.txt"})
    assert r.status_code == 200, r.text
    job = await app.state.job_store.get(r.json()["job_id"])
    assert job is not None and job.collection_id == "corpus-a"


async def test_naming_the_pointer_is_the_same_as_omitting_it(client):
    """Backward compatibility: the shipped UI and older clients send
    ``collection="default"``. It is not an entry — it resolves through."""
    shared, corpus = _entry(SHARED_ID, shared=True), _entry("corpus-a")
    await _seed(corpus, "in-corpus")
    _install([shared, corpus], pointer="corpus-a")

    r = await client.get("/v1/chunks", params={"ids": "in-corpus", "collection": "default"})
    assert r.status_code == 200, r.text
    assert [c["chunk_id"] for c in r.json()["chunks"]] == ["in-corpus"]
    r = await client.post("/v1/query", json={"query": "text", "collection": "default"})
    assert r.status_code == 200, r.text
    assert {s["doc_id"] for s in r.json()["sources"]} == {"doc-in-corpus"}
    r = await client.post("/v1/ingest", json={"source": "x.txt", "collection": "default"})
    assert r.status_code == 200, r.text
    job = await app.state.job_store.get(r.json()["job_id"])
    assert job is not None and job.collection_id == "corpus-a"


async def test_changing_default_collection_id_moves_the_pointer_not_the_data(client):
    """Two collections, flip the setting: the no-collection request lands on the
    other one, both corpora are exactly as they were, and either is still
    reachable by its real id."""
    a, b = _entry("corpus-a"), _entry("corpus-b")
    await _seed(a, "chunk-a")
    await _seed(b, "chunk-b")

    async def where_does_omitted_land() -> list[str]:
        r = await client.get("/v1/chunks", params={"ids": "chunk-a,chunk-b"})
        assert r.status_code == 200, r.text
        return [c["chunk_id"] for c in r.json()["chunks"]]

    reg = _install([a, b], pointer="corpus-a")
    assert reg.default_id == "corpus-a"
    assert await where_does_omitted_land() == ["chunk-a"]

    reg = _install([a, b], pointer="corpus-b")  # the operator flips the setting
    assert reg.default_id == "corpus-b"
    assert await where_does_omitted_land() == ["chunk-b"]

    # Nothing moved: both stores intact, both ids still explicit-addressable.
    assert await a.vector_store.count() == 1 and await b.vector_store.count() == 1
    for cid, chunk in (("corpus-a", "chunk-a"), ("corpus-b", "chunk-b")):
        r = await client.get("/v1/chunks", params={"ids": "chunk-a,chunk-b", "collection": cid})
        assert [c["chunk_id"] for c in r.json()["chunks"]] == [chunk]
    # ...and the flag followed the pointer, the surface flag did not.
    assert [e.id for e in reg.entries() if e.id == reg.default_id] == ["corpus-b"]
    assert not any(e.is_shared_surface for e in reg.entries())


async def test_pointing_at_the_pointer_name_is_fatal():
    """``DEFAULT_COLLECTION_ID=default`` is the pointer naming itself."""
    deps.settings.default_collection_id = "default"
    with pytest.raises(RuntimeError, match="pointer name"):
        deps._resolve_default_id([_entry("corpus-a")], fallback="corpus-a")


# --------------------------------------------------------------------------- #
# the exemptions follow the SURFACE, never the pointer
# --------------------------------------------------------------------------- #


async def test_reader_of_an_owned_pointer_target_cannot_write_by_omitting_collection(
    client, _auth_on
):
    """THE HAZARD. A reader of an owned collection that the pointer happens to
    name must not be able to ingest into it, or delete from it, by omitting
    ``collection`` — the read-not-write exemption belongs to the legacy shared
    surface, not to whatever ``default`` points at.

    **The reader's ingest answer changed in #422, and this is the arm that
    documents it.** The invariant — *nothing of the reader's ever lands in
    ``owned``* — is unchanged and is what is asserted below. What changed is
    the shape of the refusal: the implicit ingest picker now chooses among the
    caller's WRITABLE collections, and this reader has one, the legacy shared
    surface (registered here, and tenant-stamped, so writing there is safe).
    So their omitted-``collection`` ingest is accepted INTO THE SURFACE rather
    than refused — and the 202 says so, in ``IngestResponse.collection``,
    precisely so the divert is never silent.

    The companion test below removes the surface from the registry and pins the
    other arm: with nothing writable at all, the same request is a 403.

    ``DELETE /v1/documents`` is unchanged (403): the delete path resolves the
    caller's READ default, which for this reader is the pointer target, and
    that is not the shared surface, so it demands write."""
    shared, owned = _entry(SHARED_ID, shared=True), _entry("owned", owner="owner")
    await _seed(owned, "owners-chunk", tenant="owner")
    _install([shared, owned], pointer="owned")
    await _own("owned", "owner")
    await _publish("owned")  # the reader may READ it...

    r = await client.get("/v1/chunks", params={"ids": "owners-chunk"}, headers=_h("reader"))
    assert r.status_code == 200, r.text  # (resolution works; scope filters the rows)

    # ...but omitting `collection` is not a way to write to it. The picker skips
    # `owned` (readable, not owned) and lands on the shared surface, which says
    # so out loud.
    r = await client.post("/v1/ingest", json={"source": "x.txt"}, headers=_h("reader"))
    assert r.status_code == 200, r.text
    assert r.json()["collection"] == SHARED_ID
    job = await app.state.job_store.get(r.json()["job_id"])
    assert job.collection_id == SHARED_ID  # ...and the job row agrees

    r = await client.delete("/v1/documents/doc-owners-chunk", headers=_h("reader"))
    assert r.status_code == 403, r.text
    assert await owned.vector_store.count() == 1  # nothing was deleted

    # The owner, of course, can — through the very same omitted-collection path,
    # and for them the pointer target IS writable, so that is where it goes.
    r = await client.post("/v1/ingest", json={"source": "x.txt"}, headers=_h("owner"))
    assert r.status_code == 200, r.text
    assert r.json()["collection"] == "owned"
    r = await client.delete("/v1/documents/doc-owners-chunk", headers=_h("owner"))
    assert r.status_code == 204, r.text


async def test_a_reader_with_nothing_writable_is_refused_without_being_told_an_id(
    client, _auth_on
):
    """The other arm of the hazard, and the one the surface was masking above:
    strip the shared surface out of the registry and the reader of the pointer
    target has NOTHING that accepts their writes.

    They get a 403 — not a 200 into somebody's corpus, and not the read path's
    404, because "you can read collections but own none" is a different and
    actionable state. The body names no collection id at all, so the refusal is
    the same sentence for every caller in that state and can never be used to
    probe which collections exist."""
    owned = _entry("owned", owner="owner")
    await _seed(owned, "owners-chunk", tenant="owner")
    _install([owned], pointer="owned")
    await _own("owned", "owner")
    await _publish("owned")

    r = await client.post("/v1/ingest", json={"source": "x.txt"}, headers=_h("reader"))
    assert r.status_code == 403, r.text
    detail = r.json()["detail"]
    assert "owned" not in detail, detail
    assert detail == (
        "no collection accepts your uploads: name a collection you own "
        "explicitly in 'collection', or create your own (POST /v1/collections)"
    )
    # Nothing was written, and no job row was minted.
    assert await owned.vector_store.count() == 1


async def test_the_legacy_shared_surface_keeps_read_not_write(client, _auth_on):
    """Same reader, pointer at the legacy shared surface: read suffices, because
    there every writer lands in its own tenant stripe."""
    shared, owned = _entry(SHARED_ID, shared=True), _entry("owned", owner="owner")
    _install([shared, owned])  # pointer unset → the settings-derived surface
    assert app.state.collections.default_id == SHARED_ID
    await _own("owned", "owner")

    r = await client.post("/v1/ingest", json={"source": "x.txt"}, headers=_h("reader"))
    assert r.status_code == 200, r.text
    await _seed(shared, "readers-chunk", tenant="reader")
    r = await client.delete("/v1/documents/doc-readers-chunk", headers=_h("reader"))
    assert r.status_code == 204, r.text


# --------------------------------------------------------------------------- #
# the listing
# --------------------------------------------------------------------------- #


async def test_is_default_is_reported_on_exactly_one_entry(client):
    shared, a, b = _entry(SHARED_ID, shared=True), _entry("corpus-a"), _entry("corpus-b")
    _install([shared, a, b], pointer="corpus-b")

    body = (await client.get("/v1/collections")).json()
    flagged = [c["id"] for c in body["collections"] if c["is_default"]]
    assert flagged == ["corpus-b"] and body["default"] == "corpus-b"
    for c in body["collections"]:
        assert c["is_default"] == c["default"]  # the same answer, twice named
    # `default` is the pointer, so it is not a listed collection.
    assert "default" not in {c["id"] for c in body["collections"]}

    _install([shared, a, b])  # unset → the settings-derived entry
    body = (await client.get("/v1/collections")).json()
    assert [c["id"] for c in body["collections"] if c["is_default"]] == [SHARED_ID]
    assert body["default"] == SHARED_ID


# --------------------------------------------------------------------------- #
# resolution is one dict lookup — the durable registry is never consulted
# --------------------------------------------------------------------------- #


class _CountingStore:
    """A CollectionStore whose every method counts itself. None is expected on
    the read/resolve paths: the pointer is resolved on the in-process registry,
    which was built from the store once at startup."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str):
        async def _record(*a, **k):
            self.calls.append(name)
            return [] if name.startswith("list") else None
        return _record


async def test_resolving_an_omitted_collection_makes_no_registry_store_call(client):
    shared, corpus = _entry(SHARED_ID, shared=True), _entry("corpus-a")
    await _seed(corpus, "in-corpus")
    _install([shared, corpus], pointer="corpus-a")
    store = _CountingStore()
    app.state.collection_store = store
    try:
        r = await client.get("/v1/chunks", params={"ids": "in-corpus"})
        assert r.status_code == 200 and len(r.json()["chunks"]) == 1, r.text
        r = await client.post("/v1/retrieve", json={"query": "text"})
        assert r.status_code == 200, r.text
        r = await client.post("/v1/query", json={"query": "text", "collection": "default"})
        assert r.status_code == 200, r.text
    finally:
        del app.state.collection_store
    assert store.calls == [], store.calls


# --------------------------------------------------------------------------- #
# migration: the shared surface's ACL history under the old `default` id
# --------------------------------------------------------------------------- #


async def test_backfill_does_not_republish_a_surface_unpublished_under_the_old_id():
    """#276 moves the settings-derived surface from the synthetic id `default`
    to its real id, so its ACL history starts over — and the startup backfill,
    seeing "never granted public read", would re-publish a corpus its owner
    deliberately UN-published under the old id (issue #276's hazard 4). The
    publish decision looks back at the old id's revoked history; the owner row
    is still written under the new id as usual."""
    from ragstack.acl_store import InMemoryAclStore, set_acl_store
    from ragstack.api.access import backfill_collection_owners

    store = InMemoryAclStore()
    set_acl_store(store)
    # The pre-#276 rows: `default` was owned by the backfill owner and public —
    # then the owner un-published it.
    await store.grant("default", GRANTEE_USER, "legacy:admin", PERM_OWNER, granted_by="system:backfill")
    pub = await store.grant("default", GRANTEE_GROUP, PUBLIC_GROUP, PERM_READ, granted_by="system:backfill")
    await store.revoke(pub.id, revoked_by="legacy:admin")

    reg = CollectionRegistry([_entry(SHARED_ID, shared=True), _entry("lib")], default_id=SHARED_ID)
    await backfill_collection_owners(reg, store, "legacy:admin")

    def _public(rows):
        return [r for r in rows if r.grantee_type == GRANTEE_GROUP and r.grantee_id == PUBLIC_GROUP]

    # The surface: owner row written under the NEW id; NOT re-published.
    assert await store.owner_of(SHARED_ID) == "legacy:admin"
    assert _public(await store.shares_for(SHARED_ID)) == []
    # An ordinary legacy collection is unaffected by the lookback: published.
    assert len(_public(await store.shares_for("lib"))) == 1
    # And a second boot changes nothing.
    assert await backfill_collection_owners(reg, store, "legacy:admin") == 0
    assert _public(await store.shares_for(SHARED_ID)) == []


async def test_backfill_publishes_the_renamed_surface_when_it_was_never_unpublished():
    """The lookback only ever SUPPRESSES: a surface that was public under the
    old id (or had no rows at all) is published under its real id as before."""
    from ragstack.acl_store import InMemoryAclStore, set_acl_store
    from ragstack.api.access import backfill_collection_owners

    store = InMemoryAclStore()
    set_acl_store(store)
    await store.grant("default", GRANTEE_USER, "legacy:admin", PERM_OWNER, granted_by="system:backfill")
    await store.grant("default", GRANTEE_GROUP, PUBLIC_GROUP, PERM_READ, granted_by="system:backfill")
    reg = CollectionRegistry([_entry(SHARED_ID, shared=True)], default_id=SHARED_ID)
    await backfill_collection_owners(reg, store, "legacy:admin")
    rows = await store.shares_for(SHARED_ID)
    assert any(r.grantee_id == PUBLIC_GROUP and r.permission == PERM_READ for r in rows)
    assert await store.owner_of(SHARED_ID) == "legacy:admin"


async def test_backfill_publishes_a_surface_republished_under_the_old_id():
    """Soft revocation keeps every row, so an un-publish followed by a
    RE-publish under `default` leaves one revoked and one active public-read
    row. The owner's LAST word was to publish — so the renamed surface is
    published. ("Any revoked row exists" would read this as un-published.)"""
    from ragstack.acl_store import InMemoryAclStore, set_acl_store
    from ragstack.api.access import backfill_collection_owners

    store = InMemoryAclStore()
    set_acl_store(store)
    await store.grant("default", GRANTEE_USER, "legacy:admin", PERM_OWNER, granted_by="system:backfill")
    first = await store.grant("default", GRANTEE_GROUP, PUBLIC_GROUP, PERM_READ, granted_by="system:backfill")
    await store.revoke(first.id, revoked_by="legacy:admin")
    # The un-publish happened well before the re-publish (not within the same
    # microsecond, where the order would be undefined).
    store._shares[first.id].granted_at = "2020-01-01T00:00:00+00:00"
    store._shares[first.id].revoked_at = "2020-06-01T00:00:00+00:00"
    await store.grant("default", GRANTEE_GROUP, PUBLIC_GROUP, PERM_READ, granted_by="legacy:admin")
    reg = CollectionRegistry([_entry(SHARED_ID, shared=True)], default_id=SHARED_ID)
    await backfill_collection_owners(reg, store, "legacy:admin")
    rows = await store.shares_for(SHARED_ID)
    assert any(r.grantee_id == PUBLIC_GROUP and r.permission == PERM_READ for r in rows)


async def test_backfill_skips_the_publish_when_the_lookback_fails(caplog):
    """A store hiccup on the first post-upgrade boot must not default to
    publishing (#275 by way of the error path): the grant is skipped that boot,
    logged, and retried when the lookback works again."""
    from ragstack.acl_store import InMemoryAclStore, set_acl_store
    from ragstack.api.access import backfill_collection_owners

    store = InMemoryAclStore()
    set_acl_store(store)
    real = store.shares_for

    async def flaky(collection_id, include_revoked=False):
        if collection_id == "default":
            raise RuntimeError("acl store hiccup")
        return await real(collection_id, include_revoked)

    store.shares_for = flaky  # type: ignore[method-assign]
    reg = CollectionRegistry([_entry(SHARED_ID, shared=True)], default_id=SHARED_ID)
    import logging

    with caplog.at_level(logging.WARNING, logger="ragstack.api.access"):
        await backfill_collection_owners(reg, store, "legacy:admin")
    rows = await store.shares_for(SHARED_ID)
    assert not any(r.grantee_id == PUBLIC_GROUP for r in rows), "published blind"
    assert await store.owner_of(SHARED_ID) == "legacy:admin"  # the owner row still lands
    assert any("NOT publishing" in r.message and "could not be read" in r.message
               for r in caplog.records)

    # The lookback works again on the next boot: published as before.
    store.shares_for = real  # type: ignore[method-assign]
    await backfill_collection_owners(reg, store, "legacy:admin")
    rows = await store.shares_for(SHARED_ID)
    assert any(r.grantee_id == PUBLIC_GROUP and r.permission == PERM_READ for r in rows)


# --------------------------------------------------------------------------- #
# management routes addressed to the pointer name
# --------------------------------------------------------------------------- #


async def test_management_routes_refuse_the_literal_pointer_name(client, _auth_on):
    """share / revoke / transfer / restore on `/collections/default` are 409:
    resolving through would act on a collection the caller never named (the
    same rule as DELETE), and their ACL rows are keyed by the REAL id.

    #422 FLIPPED the assertion on the body. It used to require the message to
    echo the pointer target (``'owned'``); it now requires the message NOT to.
    These guards fire before any ACL check, so the old wording told every caller
    who could reach the route which collection the GLOBAL pointer names — a
    collection their own ``GET /v1/collections`` may never list, and since #419
    not even the id their omitted-``collection`` requests target. The message
    points at that listing instead. The status and the no-side-effects half are
    unchanged; only the prose moved."""
    shared, owned = _entry(SHARED_ID, shared=True), _entry("owned", owner="owner")
    _install([shared, owned], pointer="owned")
    await _own("owned", "owner")

    calls = [
        ("post", "/v1/collections/default/shares", {"grantee": "reader", "permission": "read"}),
        ("delete", "/v1/collections/default/shares/some-share-id", None),
        ("post", "/v1/collections/default/owner", {"subject": "reader"}),
        ("post", "/v1/collections/default/restore", None),
        # The DELETE guard is an INLINE second copy of the same refusal
        # (collections.py), so it needs its own arm or the copy can drift back.
        ("delete", "/v1/collections/default", None),
    ]
    for method, path, body in calls:
        r = await client.request(method, path, json=body, headers=_h("owner"))
        assert r.status_code == 409, (method, path, r.status_code, r.text)
        detail = r.json()["detail"]
        assert "pointer name" in detail, (method, path, detail)
        assert "owned" not in detail, (method, path, detail)
        assert "GET /v1/collections" in detail, (method, path, detail)
    # Nothing happened to the real target.
    assert await get_acl_store().owner_of("owned") == "owner"
    assert [r for r in await get_acl_store().shares_for("owned") if r.grantee_id == "reader"] == []
