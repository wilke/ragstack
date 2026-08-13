"""Counts must report what a query over the same collection would return.

A private collection's chunks are stamped with the OWNER's tenant at ingest.
Read authorization (the share) and data visibility (the per-chunk ``tenant_id``
scope) are independent gates, and query resolves both (routers/query.py
``shared_scope``) while counting resolved only the second. A grantee could
therefore search a corpus of 1.5M chunks and be told, by /v1/collections and by
the Ops store tiles, that it held 0 — indistinguishable from empty.
"""
from __future__ import annotations

import pytest

from ragstack.acl_store import GRANTEE_USER, PERM_OWNER, PERM_READ, get_acl_store
from ragstack.api import security
from ragstack.api.collections import CollectionEntry, CollectionRegistry
from ragstack.api.main import app
from ragstack.api.security import ROLE_USER
from ragstack.models import Chunk

pytestmark = pytest.mark.asyncio

KEYS = {"owner": "k-owner", "grantee": "k-grantee"}


@pytest.fixture(autouse=True)
def _principals(monkeypatch):
    monkeypatch.setattr(security.settings, "api_keys", list(KEYS.values()))
    monkeypatch.setattr(
        security.settings,
        "api_key_tenants",
        {"k-owner": "owner", "k-grantee": "grantee"},
    )
    monkeypatch.setattr(security.settings, "api_key_roles", {})
    monkeypatch.setattr(security.settings, "default_role", ROLE_USER)


def _h(who: str) -> dict:
    return {"X-API-Key": KEYS[who]}


def _entry(cid: str, default: bool = False) -> CollectionEntry:
    from tests.api.conftest import _StateRetriever

    return CollectionEntry(
        id=cid, label=cid, collection=cid, model="test-model", dim=4,
        chunk_method="fixed", chunk_size=None, chunk_overlap=None, chunk_params={},
        is_shared_surface=default, retriever=_StateRetriever(),
        vector_store=app.state.vector_store, text_index=app.state.text_index,
    )


async def _seed_owned_corpus(n: int = 3) -> None:
    """n chunks stamped with the OWNER's tenant — what ingest does."""
    chunks = [
        Chunk(
            id=f"c{i}",
            doc_id=f"doc{i}",
            content="hello world",
            embedding=[0.1, 0.2, 0.3, 0.4],
            metadata={"tenant_id": "owner"},
        )
        for i in range(n)
    ]
    await app.state.vector_store.upsert(chunks)
    await app.state.text_index.index(chunks)


async def _setup_shared() -> None:
    app.state.kg_extractor = None
    app.state.doi_enricher = None
    app.state.collections = CollectionRegistry(
        [_entry("default", True), _entry("priv")], default_id="default"
    )
    await _seed_owned_corpus()
    store = get_acl_store()
    await store.grant("priv", GRANTEE_USER, "owner", PERM_OWNER, granted_by="owner")
    await store.grant("priv", GRANTEE_USER, "grantee", PERM_READ, granted_by="owner")


async def test_collections_count_matches_what_the_grantee_can_retrieve(client):
    await _setup_shared()

    listing = (await client.get("/v1/collections", headers=_h("grantee"))).json()
    priv = next(c for c in listing["collections"] if c["id"] == "priv")

    # The same key really can search it — that is why 0 was a lie, not a policy.
    got = await client.post(
        "/v1/retrieve", json={"query": "hello", "collection": "priv"}, headers=_h("grantee")
    )
    assert got.status_code == 200, got.text
    retrieved = len(got.json()["sources"])
    assert retrieved == 3

    assert priv["count"] == retrieved  # was 0
    assert priv["text_count"] == retrieved


async def test_store_totals_include_a_shared_collection(client):
    await _setup_shared()
    body = (await client.get("/v1/stats/stores", headers=_h("grantee"))).json()
    assert body["vector"]["count"] == 3
    assert body["text"]["count"] == 3


async def test_tenants_grid_shows_the_owner_row_for_a_shared_collection(client):
    await _setup_shared()
    body = (await client.get("/v1/stats/tenants", headers=_h("grantee"))).json()
    rows = {r["tenant"]: r for r in body["tenants"]}
    assert "owner" in rows, "the writer-tenant reached through the share must be a row"
    cell = next(c for c in rows["owner"]["collections"] if c["collection"] == "priv")
    assert cell["vector_count"] == 3
    # The grid must still split apart: the grantee's own scope owns nothing.
    own = next(c for c in rows["grantee"]["collections"] if c["collection"] == "priv")
    assert own["vector_count"] == 0


async def test_a_shared_row_never_carries_a_tenant_size_from_another_collection(client):
    """The widening is per collection, and so is the ROW.

    Two collections BOTH readable by the caller but owned by different tenants,
    where the second also holds chunks stamped with the first owner. Probing
    every row x every column would report those chunks under (owner, B) — chunks
    a query on B, widened only to o2, never returns. This is the case the
    per-cell guard exists for; without it the numbers leak across collections.
    """
    from tests.api.conftest import _StateRetriever  # noqa: F401  (entry factory)

    app.state.kg_extractor = None
    app.state.doi_enricher = None
    a, b = _entry("A"), _entry("B")
    # Distinct physical stores so the co-residency guard does not (correctly)
    # refuse to widen either of them.
    b.__dict__["collection"] = "B-store"
    app.state.collections = CollectionRegistry(
        [_entry("default", True), a, b], default_id="default"
    )

    # A holds 3 chunks stamped `owner`; B holds 5 stamped `o2` AND 7 stamped
    # `owner` (a corpus B's owner ingested from the same source).
    await _seed_owned_corpus(3)  # -> A's store (shared in-memory doubles)
    store = get_acl_store()
    await store.grant("A", GRANTEE_USER, "owner", PERM_OWNER, granted_by="owner")
    await store.grant("B", GRANTEE_USER, "o2", PERM_OWNER, granted_by="o2")
    await store.grant("A", GRANTEE_USER, "grantee", PERM_READ, granted_by="owner")
    await store.grant("B", GRANTEE_USER, "grantee", PERM_READ, granted_by="o2")

    body = (await client.get("/v1/stats/tenants", headers=_h("grantee"))).json()
    rows = {r["tenant"]: r for r in body["tenants"]}
    # `owner` is a row (A is shared and non-empty) but must NOT report a count
    # for B — that column is not shared with this caller through `owner`.
    assert "owner" in rows
    cols = {c["collection"]: c for c in rows["owner"]["collections"]}
    assert cols["A"]["vector_count"] == 3
    assert cols["B"]["vector_count"] is None, "a shared row must not count another collection"


async def test_an_all_zero_share_row_is_omitted(client):
    """A row keyed by the owner's SUBJECT (an email, for a bearer identity) that
    carries no chunks is pure identity disclosure: GET .../shares is 403 for a
    non-owner, and an empty collection has no metadata.tenant_id to read either.
    Emit such a row only when a query would already reveal the tenant."""
    app.state.kg_extractor = None
    app.state.doi_enricher = None
    app.state.collections = CollectionRegistry(
        [_entry("default", True), _entry("empty")], default_id="default"
    )
    store = get_acl_store()
    await store.grant("empty", GRANTEE_USER, "google:alice@corp.example", PERM_OWNER,
                      granted_by="google:alice@corp.example")
    await store.grant("empty", GRANTEE_USER, "grantee", PERM_READ,
                      granted_by="google:alice@corp.example")

    body = (await client.get("/v1/stats/tenants", headers=_h("grantee"))).json()
    named = [r["tenant"] for r in body["tenants"]]
    assert "google:alice@corp.example" not in named
    assert "grantee" in named  # the caller's own scopes are unconditional


async def test_counts_false_exposes_no_more_than_counts_true(client):
    """The invariant the cheap path's authorization argument rests on.

    ``counts=false`` skips the share widening, so it cannot be argued safe cell
    by cell — it has to hold as a WHOLE: the rows it exposes are a SUBSET of the
    counted ones (share-derived rows are dropped entirely, which is the
    identity-disclosure rule above applied harder, never a row the counted call
    would have withheld), the columns are the same collections, and the identity
    half — the only thing the flag is for — is identical. Skipping work must
    change how much is measured, never WHO or WHAT is visible.
    """
    await _setup_shared()

    counted = (await client.get("/v1/stats/tenants", headers=_h("grantee"))).json()
    cheap = (
        await client.get("/v1/stats/tenants?counts=false", headers=_h("grantee"))
    ).json()

    counted_rows = {r["tenant"] for r in counted["tenants"]}
    cheap_rows = {r["tenant"] for r in cheap["tenants"]}
    assert cheap_rows <= counted_rows, "the cheap path must not expose a new tenant"
    # Not vacuous: this fixture HAS a share-derived row, and that row is exactly
    # what the cheap path drops. Equal sets would prove nothing about the
    # widening being skipped.
    assert "owner" in counted_rows and "owner" not in cheap_rows

    def _cols(body: dict) -> dict[str, list[str]]:
        return {
            r["tenant"]: [c["collection"] for c in r["collections"]]
            for r in body["tenants"]
        }

    counted_cols, cheap_cols = _cols(counted), _cols(cheap)
    assert all(cheap_cols[t] == counted_cols[t] for t in cheap_cols), (
        "the same collections either way — the allowlist is not a count"
    )

    identity = ("tenant", "role", "readable", "restricted_to", "auth_enabled")
    assert {k: cheap[k] for k in identity} == {k: counted[k] for k in identity}


async def test_an_unshared_collection_still_counts_zero_for_a_stranger(client):
    """Widening is the SHARE, no wider: without a grant the count stays 0 and the
    collection is not listed at all."""
    app.state.kg_extractor = None
    app.state.doi_enricher = None
    app.state.collections = CollectionRegistry(
        [_entry("default", True), _entry("priv")], default_id="default"
    )
    await _seed_owned_corpus()
    await get_acl_store().grant("priv", GRANTEE_USER, "owner", PERM_OWNER, granted_by="owner")

    listing = (await client.get("/v1/collections", headers=_h("grantee"))).json()
    assert all(c["id"] != "priv" for c in listing["collections"])
    body = (await client.get("/v1/stats/stores", headers=_h("grantee"))).json()
    assert body["vector"]["count"] == 0
