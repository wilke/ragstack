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

    def __init__(self, existing: dict[str, int] | None = None) -> None:
        self.existing = dict(existing or {})  # name -> vector size
        self.created: list[tuple[str, int]] = []

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
