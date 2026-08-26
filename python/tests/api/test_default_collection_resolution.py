"""An omitted ``collection`` targets the CALLER'S default, not the registry's (#419).

``GET /v1/collections`` has always reported a caller-aware ``default`` — the
registry pointer when the caller can actually see it (allowlist AND readable),
else their first visible collection — and the contract documents that field as
*"the id served when a request omits ``collection``"*. ``/v1/query``,
``/v1/retrieve`` and ``/v1/chunks`` resolved the GLOBAL registry pointer
instead, then 404'd on the ownership seam. **That is a conformance violation,
not an open semantic question**: the schema is authoritative and the diverging
side is the bug.

The regression that matters is the EQUIVALENCE (T6): the listing's ``default``
and the collection an omitted-``collection`` request actually serves are the
same id, for a caller who can read the pointer target and for one who cannot.
The two implementations drifted precisely because nothing pinned them together.

**Observability constraint.** The singular path stamps no ``collection`` on its
sources — that stamp is a multi-collection feature (#253) and
``conformance/test_multi_collection.py::test_singular_form_carries_no_stamp``
pins its absence. So "which collection served this?" is read off the seeded
CONTENT, never off a response field. Do not add a stamp to make this easier:
that is a contract change and out of scope.
"""
from __future__ import annotations

import pytest

from ragstack.api import security
from ragstack.api.main import app
from ragstack.config import settings

pytestmark = pytest.mark.asyncio


def _texts(body: dict) -> list[str]:
    return [s["content"] for s in body.get("sources", [])]


# --------------------------------------------------------------------------- #
# T2 / T3 — the affected user's request succeeds
# --------------------------------------------------------------------------- #


async def test_query_with_collection_omitted_serves_the_callers_own_default(
    caller_without_default_access,
):
    """T2. The reproduction from #419: a caller whose only readable collection
    is not the registry pointer target asks a question without naming one."""
    p = caller_without_default_access
    r = await p.client.post("/v1/query", json={"query": "marmalade"}, headers=p.headers)
    assert r.status_code == 200, r.text
    assert _texts(r.json()) == [p.readable_text]


async def test_retrieve_with_collection_omitted_serves_the_callers_own_default(
    caller_without_default_access,
):
    """T3a. ``/v1/retrieve`` shares ``_resolve_entry``."""
    p = caller_without_default_access
    r = await p.client.post("/v1/retrieve", json={"query": "marmalade"}, headers=p.headers)
    assert r.status_code == 200, r.text
    assert _texts(r.json()) == [p.readable_text]


async def test_chunks_with_collection_omitted_serves_the_callers_own_default(
    caller_without_default_access,
):
    """T3b. ``/v1/chunks`` too — and a chunk id that exists only in the pointer
    target must NOT come back, so this cannot pass by resolving the wrong
    collection and finding nothing."""
    p = caller_without_default_access
    r = await p.client.get(
        "/v1/chunks",
        params={"ids": f"{p.readable_chunk_id},{p.default_chunk_id}"},
        headers=p.headers,
    )
    assert r.status_code == 200, r.text
    assert [c["content"] for c in r.json()["chunks"]] == [p.readable_text]


# --------------------------------------------------------------------------- #
# T4 — the EXPLICIT path is unchanged: no existence oracle
# --------------------------------------------------------------------------- #


async def test_naming_the_unreadable_pointer_target_is_indistinguishable_from_unknown(
    caller_without_default_access,
):
    """T4. Only the IMPLICIT path changes. An explicitly named collection the
    caller may not read stays a 404 whose body is the same sentence a genuinely
    unknown id gets — the read seam never distinguishes the two."""
    p = caller_without_default_access
    unreadable = await p.client.post(
        "/v1/query", json={"query": "x", "collection": p.default_id}, headers=p.headers
    )
    unknown = await p.client.post(
        "/v1/query", json={"query": "x", "collection": "no-such-collection"}, headers=p.headers
    )
    assert unreadable.status_code == unknown.status_code == 404
    # The bodies differ only by the id the CALLER supplied; substitute it out
    # and they must be byte-identical.
    assert unreadable.json()["detail"].replace(repr(p.default_id), "<id>") == unknown.json()[
        "detail"
    ].replace(repr("no-such-collection"), "<id>")


# --------------------------------------------------------------------------- #
# T5 — a caller who can read nothing at all
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("body", [{"query": "x"}, {"query": "x", "collection": "default"}])
async def test_caller_with_no_readable_collection_gets_the_honest_404(
    caller_without_default_access, body
):
    """T5. Not "unknown collection '<the tenant default>'" — an id this caller
    was never shown and cannot see. Naming the POINTER explicitly is the same
    as omitting it (#276), so it gets the same honest answer and still does not
    confirm what ``default`` currently points at."""
    p = caller_without_default_access
    r = await p.client.post("/v1/query", json=body, headers=p.outsider_headers)
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "no collection is accessible to this caller"


async def test_caller_with_no_readable_collection_lists_nothing_and_defaults_to_empty(
    caller_without_default_access,
):
    """D1: ``default: ""`` when the caller can read nothing — not the id the
    query path now refuses to name, and not a value absent from ``collections``.
    The listing and the query path agree even in this corner."""
    p = caller_without_default_access
    body = (await p.client.get("/v1/collections", headers=p.outsider_headers)).json()
    assert body["collections"] == []
    assert body["default"] == ""


# --------------------------------------------------------------------------- #
# T6 — THE assertion: the listing's `default` IS what /v1/query targets
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("who", ["persona", "sharee"])
async def test_listing_default_is_what_an_omitted_collection_actually_serves(
    caller_without_default_access, who
):
    """T6 (and T8 for the ``sharee`` arm — a caller who CAN read the pointer
    keeps their target unchanged).

    Parameterised over both callers on purpose: this is the EQUIVALENCE, not
    two examples. The two arms disagree about the answer, so neither can pass
    by accident —

    * ``persona`` cannot read the pointer target → the first VISIBLE entry;
    * ``sharee`` can read both, so the answer is the pointer even though the
      pointer is *not* ``entries[0]`` (the registry lists ``C_readable``
      first). A picker that just took ``entries[0]`` would fail this arm.
    """
    p = caller_without_default_access
    headers = p.headers if who == "persona" else p.sharee_headers
    expected = {"persona": p.readable_id, "sharee": p.default_id}[who]
    expected_text = {"persona": p.readable_text, "sharee": p.default_text}[who]

    listing = (await p.client.get("/v1/collections", headers=headers)).json()
    assert listing["default"] == expected
    assert expected in {c["id"] for c in listing["collections"]}

    served = await p.client.post("/v1/query", json={"query": "anything"}, headers=headers)
    assert served.status_code == 200, served.text
    # The singular path carries no `collection` stamp (#253) — discriminate on
    # the distinct content seeded into each collection.
    assert _texts(served.json()) == [expected_text]


async def test_the_pointer_flag_stays_global_even_when_the_caller_cannot_see_it(
    caller_without_default_access,
):
    """The per-item ``is_default``/``default`` flags answer a DIFFERENT question
    — which listed entry the registry pointer names — and stay global. For the
    persona that means ZERO entries carry them, which is why the contract's
    "exactly one" had to become "at most one" (W2)."""
    p = caller_without_default_access
    listing = (await p.client.get("/v1/collections", headers=p.headers)).json()
    assert [c["id"] for c in listing["collections"]] == [p.readable_id]
    assert [c["id"] for c in listing["collections"] if c["is_default"]] == []
    assert [c["id"] for c in listing["collections"] if c["default"]] == []
    assert listing["default"] == p.readable_id


# --------------------------------------------------------------------------- #
# T7 — a server-chosen 404 never names an id the caller was not shown
# --------------------------------------------------------------------------- #


async def test_no_implicit_path_4xx_names_a_collection_outside_the_callers_listing(
    caller_without_default_access,
):
    """T7 (§2.5). Asserted as a PROPERTY over every implicit-path request, not
    as one example: after the restructure the ``unknown collection {id!r}`` body
    is reachable only on the EXPLICIT branch, where the id is by construction
    the string the caller sent. The implicit branch's only 404 names no id at
    all. This is the guard that stops a future refactor re-introducing an echo.
    """
    p = caller_without_default_access
    every_id = {p.readable_id, p.default_id}
    for headers, who in ((p.headers, "persona"), (p.outsider_headers, "outsider")):
        listing = (await p.client.get("/v1/collections", headers=headers)).json()
        listed = {c["id"] for c in listing["collections"]}
        responses = [
            await p.client.post("/v1/query", json={"query": "x"}, headers=headers),
            await p.client.post("/v1/retrieve", json={"query": "x"}, headers=headers),
            await p.client.get("/v1/chunks", params={"ids": "c1"}, headers=headers),
            # `collection: "default"` is the pointer NAME, normalised to the
            # implicit path (#276) — this is the leak the old code had.
            await p.client.post(
                "/v1/query", json={"query": "x", "collection": "default"}, headers=headers
            ),
        ]
        for r in responses:
            if r.status_code < 400:
                continue
            leaked = {cid for cid in every_id - listed if cid in r.text}
            assert not leaked, f"{who}: {r.status_code} body names {leaked}: {r.text}"


# --------------------------------------------------------------------------- #
# T10 — allowlist INTERSECTS readable set, never replaces it
# --------------------------------------------------------------------------- #


async def test_tenant_allowlist_intersects_the_readable_set(
    caller_without_default_access, monkeypatch
):
    """T10. ``TENANT_COLLECTIONS`` and the readable set are two independent
    filters and the implicit default must respect BOTH (ADR-0003 decision 3:
    ownership INTERSECTS confinement, never replaces it).

    Constructed so all three candidate rules give DIFFERENT answers, i.e. no
    arm of this can pass by accident. Registry insertion order is
    ``[third-corpus, blocked-corpus, personal-mine, curated-corpus]`` with the
    pointer on ``curated-corpus``; the persona can read ``third-corpus`` and
    ``personal-mine``; the allowlist permits ``blocked-corpus`` and
    ``personal-mine``. The pointer is in neither set, so the fallback decides:

    * allowlist only  → ``blocked-corpus`` (first permitted)
    * readable only   → ``third-corpus``   (first readable)
    * **intersection** → ``personal-mine`` ← the only correct answer
    """
    from ragstack.acl_store import GRANTEE_USER, PERM_OWNER
    from ragstack.api.collections import CollectionRegistry

    p = caller_without_default_access
    third = await p.make_entry("third-corpus", "third corpus text", "persona")
    blocked = await p.make_entry("blocked-corpus", "blocked corpus text", "curator")
    await p.acl.grant("third-corpus", GRANTEE_USER, "persona", PERM_OWNER, granted_by="persona")
    await p.acl.grant("blocked-corpus", GRANTEE_USER, "curator", PERM_OWNER, granted_by="curator")
    by_id = {e.id: e for e in app.state.collections.entries()}
    app.state.collections = CollectionRegistry(
        [third, blocked, by_id[p.readable_id], by_id[p.default_id]], default_id=p.default_id
    )
    monkeypatch.setattr(
        settings, "tenant_collections", {"persona": ["blocked-corpus", p.readable_id]}
    )
    listing = (await p.client.get("/v1/collections", headers=p.headers)).json()
    assert [c["id"] for c in listing["collections"]] == [p.readable_id]
    assert listing["default"] == p.readable_id
    served = await p.client.post("/v1/query", json={"query": "x"}, headers=p.headers)
    assert served.status_code == 200, served.text
    assert _texts(served.json()) == [p.readable_text]


# --------------------------------------------------------------------------- #
# A3 — the behaviour change inside `collections[]`
# --------------------------------------------------------------------------- #


async def test_pointer_name_beside_its_caller_aware_target_is_422_not_404(
    caller_without_default_access,
):
    """A3 — a DELIBERATE behaviour change, named here so nobody reads it as a
    regression. Multi-collection members go through the same ``_resolve_entry``,
    so a member literally named ``"default"`` now resolves CALLER-AWARE. For the
    persona, ``collections: ["default", "personal-mine"]`` used to be a 404
    (``default`` → the unreadable pointer target) and is now the 422 "two ids
    resolve to the same collection" — which is the honest answer, since for
    this caller ``default`` IS ``personal-mine``.

    ``test_query_collections.py::test_default_pointer_next_to_its_target_is_422``
    pins the same 422 but runs KEYLESS, so ``filter_readable`` no-ops there and
    that test stays green either way — it cannot catch a mistake here. This one
    runs under the persona's configured auth, so it can.
    """
    p = caller_without_default_access
    r = await p.client.post(
        "/v1/query",
        json={"query": "x", "collections": ["default", p.readable_id]},
        headers=p.headers,
    )
    assert r.status_code == 422, r.text
    assert "same collection" in r.json()["detail"]


async def test_multi_collection_members_are_still_enforced_individually(
    caller_without_default_access,
):
    """The A3 change must not weaken the members' own read check: naming the
    unreadable pointer target EXPLICITLY beside a readable one is still a 404."""
    p = caller_without_default_access
    r = await p.client.post(
        "/v1/query",
        json={"query": "x", "collections": [p.readable_id, p.default_id]},
        headers=p.headers,
    )
    assert r.status_code == 404, r.text


# --------------------------------------------------------------------------- #
# invariant 2.3(d) — a DORMANT collection can be the implicit default
# --------------------------------------------------------------------------- #


async def test_a_dormant_collection_can_be_the_implicit_default(
    caller_without_default_access,
):
    """Invariant (d), asserted rather than only argued.

    ``pick_default`` deliberately applies NO lifecycle filter. The listing lists
    dormant collections, so if the picker skipped them the listing's ``default``
    and the query target would disagree again — the exact drift #419 removes,
    reintroduced through a different door.

    So when the persona's ONLY readable collection is dormant, an omitted
    ``collection`` must reach the lifecycle gate and get **503 + Retry-After**,
    exactly as if they had named it. The failure this guards against is a
    **404** ``no collection is accessible to this caller`` — what a
    lifecycle-aware picker would produce, by filtering the collection out and
    leaving the caller with an empty visible set.
    """
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
    # `get_collection_store` falls back to the JSON store only when the
    # attribute is ABSENT, so put back exactly what was there.
    prior = getattr(app.state, "collection_store", None)
    app.state.collection_store = store
    # restorer=None: this asserts the GATE is REACHED, not that a restore runs.
    set_lifecycle_gate(LifecycleGate(store, cache_seconds=0.0, retry_after=30))
    try:
        # The listing still names it — dormant collections are listed...
        listing = (await p.client.get("/v1/collections", headers=p.headers)).json()
        assert [c["id"] for c in listing["collections"]] == [p.readable_id]
        assert listing["default"] == p.readable_id
        # ...so the picker must be able to choose it, and the caller must get
        # the lifecycle answer rather than "you have no collections".
        r = await p.client.post("/v1/query", json={"query": "x"}, headers=p.headers)
        assert r.status_code == 503, r.text
        assert r.headers.get("Retry-After") == "30"
        assert p.readable_id in r.json()["detail"]
        assert DORMANT in r.json()["detail"]
    finally:
        reset_lifecycle_gate()
        if prior is None:
            del app.state.collection_store
        else:
            app.state.collection_store = prior


# --------------------------------------------------------------------------- #
# invariant 2.3(b) — the callers who work today are untouched
# --------------------------------------------------------------------------- #


async def test_keyless_dev_still_resolves_the_registry_pointer(client, monkeypatch):
    """``filter_readable`` is a no-op when auth is unconfigured, so the open
    dev path — and the whole keyless suite — resolves the registry pointer
    exactly as before."""
    monkeypatch.setattr(security.settings, "api_keys", [])
    from tests.api.conftest import SHARED_ID

    body = (await client.get("/v1/collections")).json()
    assert body["default"] == SHARED_ID
    r = await client.post("/v1/query", json={"query": "x"})
    assert r.status_code == 200, r.text
