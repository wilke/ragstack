"""Unit tests for Qdrant collection naming and dimension reconciliation.

No real Qdrant: the AsyncQdrantClient is replaced with a fake exposing only the
methods ensure_collection uses.
"""
import re
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("qdrant_client")

from ragstack.stores.errors import VectorDimMismatch  # noqa: E402
from ragstack.stores.qdrant import (  # noqa: E402
    QdrantVectorStore,
    _build_filter,
    collection_name,
)


class _FakeClient:
    """Minimal async stand-in for AsyncQdrantClient."""

    def __init__(
        self,
        existing: dict[str, int] | None = None,
        *,
        exact_raises: bool = False,
        counts: tuple[int, int] = (7, 5),  # (exact, estimate)
    ) -> None:
        self.existing = dict(existing or {})  # name -> vector size
        self.created: list[tuple[str, int]] = []
        self.indexed: list[tuple[str, str]] = []  # (collection, field)
        self.exact_raises = exact_raises
        self._exact_count, self._approx_count = counts
        self.last_query_filter: Any = "unset"

    async def query_points(
        self, collection_name, query, limit, query_filter, with_payload
    ):
        self.last_query_filter = query_filter
        return SimpleNamespace(points=[])

    async def count(self, collection_name, count_filter, exact, timeout=None):
        if exact:
            if self.exact_raises:
                raise TimeoutError("exact count scanned too long")
            return SimpleNamespace(count=self._exact_count)
        return SimpleNamespace(count=self._approx_count)

    async def get_collections(self):
        return SimpleNamespace(
            collections=[SimpleNamespace(name=n) for n in self.existing]
        )

    async def get_collection(self, name):
        size = self.existing[name]
        return SimpleNamespace(
            config=SimpleNamespace(params=SimpleNamespace(
                vectors=SimpleNamespace(size=size)
            ))
        )

    async def create_collection(self, collection_name, vectors_config):
        self.created.append((collection_name, vectors_config.size))
        self.existing[collection_name] = vectors_config.size

    async def create_payload_index(self, collection_name, field_name, field_schema):
        self.indexed.append((collection_name, field_name))


def _store(client: _FakeClient, *, collection: str = "c", size: int = 768):
    store = QdrantVectorStore(collection=collection, vector_size=size)
    store._client = client  # swap in the fake (no network)
    return store


# --- collection_name ---------------------------------------------------------

def test_collection_name_scopes_by_model_and_dim():
    a = collection_name("ragstack", "BAAI/bge-base-en-v1.5", 768)
    b = collection_name("ragstack", "BAAI/bge-base-en-v1.5", 768)
    assert a == b  # deterministic
    assert "768" in a and a.startswith("ragstack_")


def test_collection_name_differs_by_model_and_by_dim():
    base = collection_name("ragstack", "model-a", 768)
    assert base != collection_name("ragstack", "model-b", 768)   # different model
    assert base != collection_name("ragstack", "model-a", 1024)  # different dim


def test_collection_name_disambiguates_slug_collisions():
    # Two distinct model names slugify to the same string but must not collide.
    assert collection_name("r", "a/b", 8) != collection_name("r", "a-b", 8)


# --- collection_name: named libraries (isolation) ---------------------------

CHUNK = "fixed_token/512/64"


def test_named_libraries_with_identical_build_specs_are_isolated():
    """The bug: user-named libraries built from the same (model, dim, chunk) all
    mapped to ONE physical collection, so they were aliases over one store."""
    a = collection_name("ragstack", "test/sfr", 8, chunk=CHUNK, name="andy")
    b = collection_name("ragstack", "test/sfr", 8, chunk=CHUNK, name="open-access")
    c = collection_name("ragstack", "test/sfr", 8, chunk=CHUNK, name="test2")
    assert len({a, b, c}) == 3


def test_named_collection_name_is_deterministic():
    args = ("ragstack", "test/sfr", 8)
    kw = {"chunk": CHUNK, "name": "andy"}
    assert collection_name(*args, **kw) == collection_name(*args, **kw)


def test_named_collection_survives_slug_collisions_and_empty_slugs():
    # Names that slugify identically, and names that slugify to nothing at all,
    # must still get distinct stores — the hash covers the raw name.
    def n(name: str) -> str:
        return collection_name("ragstack", "test/sfr", 8, chunk=CHUNK, name=name)

    assert n("open access") != n("open-access")
    assert n("!!!") != n("???")


def test_named_collection_name_is_store_safe_and_diagnosable():
    name = collection_name(
        "ragstack", "BAAI/bge-base-en-v1.5", 768, chunk=CHUNK, name="Open Access"
    )
    # Qdrant + ES safe: lowercase [a-z0-9_], no leading "_", well under ES's 255-byte
    # index-name limit.
    assert re.fullmatch(r"[a-z0-9_]+", name)
    assert not name.startswith("_") and len(name) < 200
    # ...and still carries enough build spec to diagnose from the store listing.
    assert "lib" in name.split("_") and "open_access" in name
    assert "768" in name and "fixed_token" in name


def test_named_collection_never_equals_the_content_addressed_one():
    spec = ("ragstack", "test/sfr", 8)
    assert collection_name(*spec, chunk=CHUNK) != collection_name(
        *spec, chunk=CHUNK, name="andy"
    )


def test_unnamed_collection_names_are_byte_for_byte_unchanged():
    """Pin the content-addressed (corpus) names exactly. Changing them would
    orphan every already-built collection, so a regression must fail loudly."""
    assert (
        collection_name("ragstack", "BAAI/bge-base-en-v1.5", 768)
        == "ragstack_baai_bge_base_en_v1_5_768_0896d168"
    )
    assert (
        collection_name("ragstack", "BAAI/bge-base-en-v1.5", 768, chunk=CHUNK)
        == "ragstack_baai_bge_base_en_v1_5_768_fixed_token_512_64_a3c446b1"
    )
    # name=None and name="" both mean "no explicit id" → content-addressed.
    assert collection_name("ragstack", "BAAI/bge-base-en-v1.5", 768, chunk=CHUNK, name="") == (
        collection_name("ragstack", "BAAI/bge-base-en-v1.5", 768, chunk=CHUNK)
    )


# --- ensure_collection dimension reconciliation ------------------------------

@pytest.mark.asyncio
async def test_ensure_collection_creates_when_absent():
    client = _FakeClient()
    store = _store(client, collection="new", size=256)
    await store.ensure_collection()
    assert ("new", 256) in client.created


@pytest.mark.asyncio
async def test_ensure_collection_noop_when_dim_matches():
    client = _FakeClient(existing={"c": 768})
    store = _store(client, collection="c", size=768)
    await store.ensure_collection()  # must not raise, must not recreate
    assert client.created == []


@pytest.mark.asyncio
async def test_ensure_collection_raises_on_dim_mismatch():
    client = _FakeClient(existing={"c": 1024})
    store = _store(client, collection="c", size=768)
    with pytest.raises(VectorDimMismatch):
        await store.ensure_collection()


@pytest.mark.asyncio
async def test_ensure_collection_indexes_tenant_field_on_create():
    client = _FakeClient()
    store = _store(client, collection="new", size=256)
    await store.ensure_collection()
    assert ("new", "tenant_id") in client.indexed  # tenant field indexed at create


@pytest.mark.asyncio
async def test_ensure_collection_backfills_tenant_index_on_existing():
    # a collection built before this fix (dim matches, no recreate) still gets the
    # tenant index back-filled so tenant-filtered counts stop full-scanning
    client = _FakeClient(existing={"c": 768})
    store = _store(client, collection="c", size=768)
    await store.ensure_collection()
    assert client.created == [] and ("c", "tenant_id") in client.indexed


@pytest.mark.asyncio
async def test_ensure_collection_indexes_doc_id():
    """delete() filters on doc_id, and every re-ingest/bulk load delete-priors
    per document — without this index each delete is a full collection scan.
    Measured on the OA pilot: ~1 delete/s past 150k points (the 'hung' load),
    ~125/s the moment the index existed."""
    client = _FakeClient()
    store = _store(client, collection="new", size=256)
    await store.ensure_collection()
    assert ("new", "doc_id") in client.indexed
    # and back-filled on a pre-existing collection too
    client2 = _FakeClient(existing={"old": 768})
    store2 = _store(client2, collection="old", size=768)
    await store2.ensure_collection()
    assert ("old", "doc_id") in client2.indexed


# --- count_tenants: exact where affordable, estimate on timeout --------------

@pytest.mark.asyncio
async def test_count_tenants_empty_scope_is_zero():
    store = _store(_FakeClient(), collection="c")
    assert await store.count_tenants([]) == 0  # fail-closed, no global count


@pytest.mark.asyncio
async def test_count_tenants_uses_exact_when_affordable():
    store = _store(_FakeClient(counts=(7, 5)), collection="c")
    assert await store.count_tenants(["public"]) == 7


@pytest.mark.asyncio
async def test_count_tenants_falls_back_to_estimate_on_timeout():
    # exact count times out on a huge match set → fast segment estimate
    store = _store(_FakeClient(exact_raises=True, counts=(7, 5)), collection="c")
    assert await store.count_tenants(["public"]) == 5


# --- _build_filter: empty multi-value list fails closed (#196) ---------------

def _conditions(f):
    return {c.key: c.match for c in f.must}


def test_build_filter_no_keys_is_unfiltered():
    # An absent constraint is still an absent constraint — only None/{} means
    # "no filter". This is the case the fail-closed change must NOT break.
    assert _build_filter(None) is None
    assert _build_filter({}) is None


def test_build_filter_non_empty_list_is_match_any():
    f = _build_filter({"tenant_id": ["alice", "public"]})
    assert f is not None
    assert _conditions(f)["tenant_id"].any == ["alice", "public"]


def test_build_filter_scalar_is_match_value():
    f = _build_filter({"doc_id": "d1"})
    assert f is not None
    assert _conditions(f)["doc_id"].value == "d1"


def test_build_filter_empty_list_matches_nothing_not_everything():
    # `value in []` is false: keep the key as an unsatisfiable MatchAny rather
    # than dropping it (which would return an unfiltered, cross-tenant read).
    f = _build_filter({"tenant_id": []})
    assert f is not None, "empty scope must not degrade to an unfiltered read"
    assert _conditions(f)["tenant_id"].any == []


def test_build_filter_empty_second_scope_key_still_constrains():
    # The dangerous shape once a second scope dimension exists: a DB lookup
    # returns no visible libraries → the whole library constraint must not vanish.
    f = _build_filter({"library_id": [], "tenant_id": ["alice", "public"]})
    assert f is not None
    conds = _conditions(f)
    assert set(conds) == {"library_id", "tenant_id"}
    assert conds["library_id"].any == []


@pytest.mark.asyncio
async def test_search_with_empty_scope_sends_a_filter():
    # End-to-end at the store boundary: the query must still carry a filter.
    client = _FakeClient()
    store = _store(client, collection="c")
    await store.search([1.0, 0.0], top_k=5, filters={"tenant_id": []})
    assert client.last_query_filter is not None
