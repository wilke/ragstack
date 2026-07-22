"""Unit tests for Qdrant collection naming and dimension reconciliation.

No real Qdrant: the AsyncQdrantClient is replaced with a fake exposing only the
methods ensure_collection uses.
"""
from types import SimpleNamespace

import pytest

pytest.importorskip("qdrant_client")

from ragstack.stores.errors import VectorDimMismatch  # noqa: E402
from ragstack.stores.qdrant import (  # noqa: E402
    QdrantVectorStore,
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
