"""Phase 3 Step 4: POST /v1/ingest honors an optional `collection` target.

A targeted ingest indexes documents with that collection's bound
embedder/chunker/stores; an unknown id is a 404; omitting `collection` (or naming
the default) keeps the prebuilt app ingestor (backward compatible).
"""
from types import SimpleNamespace

import httpx
import pytest

from ragstack.api.collections import CollectionEntry
from ragstack.api.deps import _chunker_for, build_ingestor_for


def _entry(method: str = "fixed") -> CollectionEntry:
    return CollectionEntry(
        id="acme", label="acme", collection="physical_acme", model="m", dim=8,
        chunk_method=method, chunk_size=200, chunk_overlap=20, chunk_params={},
        is_shared_surface=False, retriever=object(),
        vector_store=object(), text_index=object(), embedder=object(),
    )


# --- builders (unit, offline) ---------------------------------------------- #

def test_chunker_for_rejects_semantic_without_an_embed_fn():
    # semantic methods embed while chunking, so they need a sync embed_fn.
    # build_ingestor_for now supplies a per-collection bridge (see
    # test_chunk_choice.py); called bare this must still raise rather than
    # silently chunk some other way.
    for m in ("semantic", "semantic_pooled"):
        with pytest.raises(ValueError):
            _chunker_for(_entry(m))


def test_chunker_for_fixed_builds():
    assert _chunker_for(_entry("fixed")) is not None


def test_build_ingestor_binds_collection_stores():
    e = _entry("fixed")
    app_state = SimpleNamespace(
        graph_store=None, kg_extractor=None, http_client=httpx.AsyncClient(), job_store=None
    )
    ing = build_ingestor_for(app_state, e)
    p = ing._pipeline
    # the pipeline writes into THIS collection's embedder + stores, not the defaults
    assert p.embedder is e.embedder
    assert p.vector_store is e.vector_store
    assert p.text_index is e.text_index
    # ...and it knows WHICH collection it writes into. The graph store is shared
    # across collections, so without this the triples it extracts would be
    # unstamped and its delete-prior would cross the collection boundary (#209).
    assert p.collection == e.collection


# --- endpoint --------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_ingest_unknown_collection_is_404(client):
    r = await client.post("/v1/ingest", json={"source": "x.txt", "collection": "ghost"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_ingest_default_collection_passes_through(client):
    # naming the default id uses the prebuilt ingestor (no per-collection build)
    r = await client.post("/v1/ingest", json={"source": "x.txt", "collection": "default"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_ingest_without_collection_still_works(client):
    # backward compatibility: omitting `collection` is unchanged behavior
    r = await client.post("/v1/ingest", json={"source": "x.txt"})
    assert r.status_code == 200


# --------------------------------------------------------------------------- #
# #422 B — an omitted `collection` targets the caller's WRITABLE default
# --------------------------------------------------------------------------- #
#
# The read paths (PR-2) resolve the caller's readable default. Ingest cannot use
# that unchanged: for a caller who can READ the pointer target but not own it,
# it would route their upload into somebody else's corpus. So the implicit
# ingest picks over the caller's WRITABLE entries — owned, admin, or the legacy
# shared surface where per-chunk `tenant_id` stamping is the write isolation.
#
# Every test below runs on the persona fixture (`conftest.py`), which asserts its
# own preconditions: auth CONFIGURED, no admin, no allowlist. Without that, every
# ACL filter no-ops and the whole file would be vacuous.
#
# NOTE none of these exercise the UI's path. Post-#420 `collectionTarget.ts`
# resolves the listing's advertised `default` and sends it as an EXPLICIT id, so
# the browser never omits the field once the listing has loaded. The picker
# serves raw API/CLI callers; the explicit branch is what serves the UI, and it
# gets its own regression guard below.

from ragstack.api.main import app as _app  # noqa: E402

NO_WRITABLE = (
    "no collection accepts your uploads: name a collection you own explicitly "
    "in 'collection', or create your own (POST /v1/collections)"
)
NO_ACCESSIBLE = "no collection is accessible to this caller"


def _entries_of(p):
    by_id = {e.id: e for e in _app.state.collections.entries()}
    return by_id[p.readable_id], by_id[p.default_id]


def _doc(tmp_path, name="probe.txt", text="a probe document about marmalade"):
    f = tmp_path / name
    f.write_text(text)
    return str(f)


@pytest.fixture
def _ingest_root(monkeypatch, tmp_path):
    """`POST /v1/ingest` fails closed with 503 when `ingest_root` is unset, and
    that 503 fires BEFORE the target is resolved — it would mask every refusal
    this file asserts."""
    from ragstack.config import settings

    monkeypatch.setattr(settings, "ingest_root", str(tmp_path))
    return tmp_path


@pytest.mark.asyncio
async def test_persona_implicit_ingest_lands_in_their_own_collection(
    caller_without_default_access, _ingest_root
):
    """B1 — the reproduction. The persona uploads without naming a collection.
    Asserted three ways, because acceptance B is about AGREEMENT: the 202 names
    the target, the job row records it, and the document is actually readable
    back from that collection."""
    p = caller_without_default_access
    r = await p.client.post(
        "/v1/ingest", json={"source": _doc(_ingest_root)}, headers=p.headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["collection"] == p.readable_id

    job = await _app.state.job_store.get(r.json()["job_id"])
    assert job.collection_id == p.readable_id
    assert job.status == "completed", job.error

    # ...and the chunks are in the persona's OWN stores: GET /v1/documents
    # resolves the same collection, and the pointer target is untouched.
    mine, curated = _entries_of(p)
    listed = await p.client.get("/v1/documents", headers=p.headers)
    assert "probe.txt" in {d["metadata"].get("filename") for d in listed.json()}
    assert await curated.text_index.count_tenants(["persona"]) == 0
    assert await curated.vector_store.count_tenants(["persona"]) == 0


@pytest.mark.asyncio
async def test_curator_implicit_ingest_prefers_the_pointer(
    caller_without_default_access, _ingest_root
):
    """B2. The pointer still wins when the caller can write it — same tie-break
    as every other default in the codebase. Not redundant with B1: the curator
    OWNS the pointer target, and `curated-corpus` is deliberately not
    `entries[0]`, so a picker that just took the first writable entry would
    give a different answer here."""
    p = caller_without_default_access
    r = await p.client.post(
        "/v1/ingest", json={"source": _doc(_ingest_root)}, headers=p.curator_headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["collection"] == p.default_id
    job = await _app.state.job_store.get(r.json()["job_id"])
    assert job.collection_id == p.default_id


@pytest.mark.asyncio
async def test_a_caller_who_owns_nothing_is_refused_and_told_no_id(
    caller_without_default_access, _ingest_root
):
    """B3. The `sharee` can READ both collections and owns neither, and no
    shared surface is registered here — so nothing accepts their writes.

    403, not 200-into-somebody-else's-corpus, and not the read path's 404: the
    distinction is actionable. The body must name NO collection id, so the
    refusal is the same sentence for every caller in this state and can never be
    used to probe which collections exist."""
    p = caller_without_default_access
    mine, curated = _entries_of(p)
    r = await p.client.post(
        "/v1/ingest", json={"source": _doc(_ingest_root)}, headers=p.sharee_headers
    )
    assert r.status_code == 403, r.text
    detail = r.json()["detail"]
    assert detail == NO_WRITABLE
    assert p.readable_id not in detail and p.default_id not in detail
    # Nothing landed anywhere, and no job row was minted.
    assert await mine.text_index.count_tenants(["sharee"]) == 0
    assert await curated.text_index.count_tenants(["sharee"]) == 0
    assert list(_app.state.job_store._jobs.values()) == []


@pytest.mark.asyncio
async def test_a_caller_who_can_read_nothing_gets_the_read_paths_404(
    caller_without_default_access, _ingest_root
):
    """B4. The outsider's refusal is the OTHER one — byte-identical to what
    every read path gives them, because it is the same state ("your listing is
    empty") and must not be two different sentences."""
    p = caller_without_default_access
    r = await p.client.post(
        "/v1/ingest", json={"source": _doc(_ingest_root)}, headers=p.outsider_headers
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == NO_ACCESSIBLE


@pytest.mark.asyncio
async def test_an_explicitly_named_collection_never_enters_the_picker(
    caller_without_default_access, _ingest_root
):
    """B5 — the guard against silent rerouting. The `sharee` NAMES the pointer
    target, which they can read but not own. That must be a 403 about THAT
    collection, not a quiet divert into some other one they can write.

    Both halves matter: the status (unchanged from before this work) and the
    fact that nothing landed anywhere. A picker that accepted explicit ids
    would show up here as a 200."""
    p = caller_without_default_access
    mine, curated = _entries_of(p)
    r = await p.client.post(
        "/v1/ingest",
        json={"source": _doc(_ingest_root), "collection": p.default_id},
        headers=p.sharee_headers,
    )
    assert r.status_code == 403, r.text
    # ...and it is NOT the picker's refusal — this one is about the named id.
    assert r.json()["detail"] != NO_WRITABLE
    assert await mine.text_index.count_tenants(["sharee"]) == 0
    assert await curated.text_index.count_tenants(["sharee"]) == 0
    assert list(_app.state.job_store._jobs.values()) == []


@pytest.mark.asyncio
async def test_naming_an_unreadable_collection_stays_indistinguishable_from_unknown(
    caller_without_default_access, _ingest_root
):
    """B6 — no existence oracle, on the ingest path. The persona names the
    pointer target (readable to others, not to them) and then a genuinely
    unknown id. Both 404, and the bodies differ only by the id the CALLER
    supplied. This is the ingest twin of the read paths' T4 guard, and the
    branch split in this PR is exactly the kind of change that could break it."""
    p = caller_without_default_access
    src = _doc(_ingest_root)
    unreadable = await p.client.post(
        "/v1/ingest", json={"source": src, "collection": p.default_id}, headers=p.headers
    )
    unknown = await p.client.post(
        "/v1/ingest", json={"source": src, "collection": "no-such-collection"},
        headers=p.headers,
    )
    assert unreadable.status_code == unknown.status_code == 404
    assert unreadable.json()["detail"].replace(repr(p.default_id), "<id>") == unknown.json()[
        "detail"
    ].replace(repr("no-such-collection"), "<id>")


# --- the shared-surface arm (W11) ------------------------------------------ #
#
# The persona's two standard entries are both non-surface, and the keyless
# `client` fixture cannot substitute: with auth unconfigured every ACL filter
# no-ops, so a surface test there would be vacuous. These use the fixture's
# `shared=True` knob under the persona's ENFORCED auth.


async def _register_surface(p, cid="shared-corpus"):
    """Add a shared-surface entry every persona caller can READ (a ``public``
    grant, as the startup backfill gives the real one), and return it.

    Its stores are ``app.state``'s — the fixture's ``shared=True`` knob binds
    them — because the ingest path hands the surface the PREBUILT app ingestor
    rather than building a per-collection one."""
    from ragstack.acl_store import GRANTEE_GROUP, PERM_READ, PUBLIC_GROUP
    from ragstack.api.collections import CollectionRegistry

    surface = await p.make_entry(cid, "the curated shared corpus", "curator", shared=True)
    await p.acl.grant(cid, GRANTEE_GROUP, PUBLIC_GROUP, PERM_READ, granted_by="curator")
    by_id = {e.id: e for e in _app.state.collections.entries()}
    _app.state.collections = CollectionRegistry(
        [by_id[p.readable_id], by_id[p.default_id], surface], default_id=p.default_id
    )
    return surface


@pytest.mark.asyncio
async def test_read_on_the_shared_surface_is_enough_to_implicit_ingest_there(
    caller_without_default_access, _ingest_root
):
    """B7 — the exemption, exercised under enforced auth. The `sharee` owns
    nothing (B3 proved they are otherwise refused), but a shared surface is now
    registered and they can read it. On that surface per-chunk `tenant_id`
    stamping is the write isolation, so READ suffices and the write lands
    tenant-stamped.

    Contrast with B3, which is the same caller and the same request with the
    surface absent: this pair is what shows the exemption is doing the work."""
    p = caller_without_default_access
    surface = await _register_surface(p)
    r = await p.client.post(
        "/v1/ingest", json={"source": _doc(_ingest_root)}, headers=p.sharee_headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["collection"] == surface.id
    job = await _app.state.job_store.get(r.json()["job_id"])
    assert job.collection_id == surface.id
    assert job.status == "completed", job.error
    # Stamped with the WRITER's tenant, which is what isolates them there.
    assert await surface.text_index.count_tenants(["sharee"]) == 1


@pytest.mark.asyncio
async def test_the_exemption_keys_on_the_surface_not_on_the_pointer(
    caller_without_default_access, _ingest_root
):
    """B8 — THE hazard the flag split exists to prevent. The registry pointer
    names `curated-corpus`, an OWNED (non-surface) collection the `sharee` can
    read. If the read-suffices exemption followed the POINTER instead of the
    surface flag, omitting `collection` would let them ingest into the curator's
    corpus.

    With a surface also registered they are routed there instead — never into
    the pointer target. That is the invariant: whatever else happens, the
    curator's corpus receives nothing from a caller who merely reads it."""
    p = caller_without_default_access
    surface = await _register_surface(p)
    _mine, curated = _entries_of(p)
    r = await p.client.post(
        "/v1/ingest", json={"source": _doc(_ingest_root)}, headers=p.sharee_headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["collection"] == surface.id != p.default_id
    assert await curated.text_index.count_tenants(["sharee"]) == 0
    assert await curated.vector_store.count_tenants(["sharee"]) == 0


@pytest.mark.asyncio
async def test_explicitly_naming_the_shared_surface_is_accepted_under_enforced_auth(
    caller_without_default_access, _ingest_root
):
    """B9 — the regression this PR would otherwise have shipped, in the shape it
    would have arrived in.

    Splitting the branch moved an explicitly-named surface id onto the EXPLICIT
    path. That path enforced plain `"write"`; the surface is public-read, so a
    reader of it would have got a **403** — and post-#420 the UI sends the
    listing's advertised default as an explicit id, so that 403 would have gone
    to our own uploader on the flagship corpus. Keying the action on
    `is_shared_surface` on BOTH branches is what prevents it.

    Run under the persona's enforced auth on purpose: keyless, `enforce_access`
    no-ops and this test would pass with the exemption deleted."""
    p = caller_without_default_access
    surface = await _register_surface(p)
    r = await p.client.post(
        "/v1/ingest",
        json={"source": _doc(_ingest_root), "collection": surface.id},
        headers=p.sharee_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["collection"] == surface.id
    assert await surface.text_index.count_tenants(["sharee"]) == 1


@pytest.mark.asyncio
async def test_explicitly_naming_the_pointer_target_still_works_keyless(client):
    """B10 — the same regression guard on the KEYLESS fixture, where `SHARED_ID`
    is the shared surface and is what the pointer names. Weaker than B9 (the ACL
    no-ops here) but it is the arm the rest of the suite and every keyless
    deployment actually run, so it is pinned separately."""
    from tests.api.conftest import SHARED_ID

    r = await client.post("/v1/ingest", json={"source": "x.txt", "collection": SHARED_ID})
    assert r.status_code == 200, r.text
    assert r.json()["collection"] == SHARED_ID


@pytest.mark.asyncio
async def test_implicit_ingest_is_unchanged_keyless(client):
    """B11 — the no-op. With auth unconfigured `filter_writable` returns
    everything, exactly as `filter_readable` does, so the open dev path resolves
    the registry pointer and the whole keyless suite is untouched. A picker that
    started answering 403 here would break every keyless deployment."""
    from tests.api.conftest import SHARED_ID

    r = await client.post("/v1/ingest", json={"source": "x.txt"})
    assert r.status_code == 200, r.text
    assert r.json()["collection"] == SHARED_ID


@pytest.mark.asyncio
async def test_a_dormant_pick_still_reaches_the_lifecycle_gate(
    caller_without_default_access, _ingest_root
):
    """B12. `writable_entries` applies no lifecycle filter, deliberately — the
    listing lists dormant collections, so a lifecycle-aware picker would answer
    "you have no collections" (404/403) where the caller should get the lifecycle
    answer. `enforce_access` runs after the pick and owns that gate, so a dormant
    pick is 503 + `Retry-After`, exactly as if the caller had named it."""
    from ragstack.api.lifecycle import (
        LifecycleGate,
        reset_lifecycle_gate,
        set_lifecycle_gate,
    )
    from ragstack.collection_store import (
        DORMANT,
        CollectionSpec,
        InMemoryCollectionStore,
    )

    p = caller_without_default_access
    store = InMemoryCollectionStore()
    await store.put(
        CollectionSpec(
            id=p.readable_id, label=p.readable_id, owner="persona",
            collection=p.readable_id, embedding_api="openai",
            embedding_model="test-model", embedding_model_dim=4, chunk_method="fixed",
        )
    )
    await store.set_state(p.readable_id, DORMANT, reason="evicted")
    prior = getattr(_app.state, "collection_store", None)
    _app.state.collection_store = store
    set_lifecycle_gate(LifecycleGate(store, cache_seconds=0.0, retry_after=30))
    try:
        r = await p.client.post(
            "/v1/ingest", json={"source": _doc(_ingest_root)}, headers=p.headers
        )
        assert r.status_code == 503, r.text
        assert r.headers.get("Retry-After") == "30"
        assert DORMANT in r.json()["detail"]
    finally:
        reset_lifecycle_gate()
        if prior is None:
            del _app.state.collection_store
        else:
            _app.state.collection_store = prior


# --- the upload leg -------------------------------------------------------- #


async def _upload(p, headers, collection=None):
    files = [("files", ("probe.txt", b"a probe document", "text/plain"))]
    data = {"collection": collection} if collection else {}
    return await p.client.post("/v1/ingest/upload", files=files, data=data, headers=headers)


@pytest.mark.asyncio
async def test_upload_uses_the_same_picker(caller_without_default_access, _ingest_root):
    """B13. `POST /v1/ingest/upload` is the endpoint the affected user actually
    reaches for, and it shares `_authorize_ingest_target` with `POST /v1/ingest`
    — but it is a separate route with its own job-row construction, so the three
    outcomes are pinned here too rather than argued from the shared helper."""
    p = caller_without_default_access
    r = await _upload(p, p.headers)
    assert r.status_code == 202, r.text
    assert r.json()["collection"] == p.readable_id
    job = await _app.state.job_store.get(r.json()["job_id"])
    assert job.collection_id == p.readable_id

    assert (await _upload(p, p.sharee_headers)).status_code == 403
    assert (await _upload(p, p.outsider_headers)).status_code == 404


@pytest.mark.asyncio
async def test_upload_refusals_name_no_collection(
    caller_without_default_access, _ingest_root
):
    """B14. Same two bodies as the path-ingest leg, asserted separately so a
    future divergence in the upload route's error handling is visible."""
    p = caller_without_default_access
    assert (await _upload(p, p.sharee_headers)).json()["detail"] == NO_WRITABLE
    assert (await _upload(p, p.outsider_headers)).json()["detail"] == NO_ACCESSIBLE


@pytest.mark.asyncio
async def test_the_nothing_writable_403_leaks_no_id_the_caller_was_not_shown(
    caller_without_default_access, _ingest_root
):
    """B15 — the oracle guard, in the one caller shape that can actually detect
    it. B3 pins the 403's exact body, but for the `sharee` the registry pointer
    target is IN their listing, so an accidental echo of it would leak nothing
    and no property test over that caller could notice.

    This builds the shape that can: grant the `outsider` read on ONE new
    collection and nothing else. They now read something (so they get the 403,
    not the 404) while `curated-corpus` — what the pointer names, and the id a
    careless refusal would reach for — is outside their listing entirely. The
    assertion is the property, over every id in the registry: the refusal names
    nothing the caller's own `GET /v1/collections` does not."""
    from ragstack.acl_store import GRANTEE_USER, PERM_READ
    from ragstack.api.collections import CollectionRegistry

    p = caller_without_default_access
    third = await p.make_entry("third-corpus", "some third corpus", "curator")
    await p.acl.grant("third-corpus", GRANTEE_USER, "outsider", PERM_READ,
                      granted_by="curator")
    by_id = {e.id: e for e in _app.state.collections.entries()}
    _app.state.collections = CollectionRegistry(
        [by_id[p.readable_id], by_id[p.default_id], third], default_id=p.default_id
    )

    listing = (await p.client.get("/v1/collections", headers=p.outsider_headers)).json()
    listed = {c["id"] for c in listing["collections"]}
    assert listed == {"third-corpus"}, listed  # reads exactly one, owns none

    for r in (
        await p.client.post(
            "/v1/ingest", json={"source": _doc(_ingest_root)}, headers=p.outsider_headers
        ),
        await _upload(p, p.outsider_headers),
    ):
        assert r.status_code == 403, r.text
        every_id = {p.readable_id, p.default_id, "third-corpus"}
        leaked = {cid for cid in every_id - listed if cid in r.text}
        assert not leaked, f"{r.status_code} body names {leaked}: {r.text}"
