"""The in-memory vector-store bank (#392) does not leak on purge.

Under ``vector_backend=memory`` every collection's store lives in
``app.state.memory_vector_stores`` for the process, so an UNREGISTERED
collection keeps its chunks the way a Qdrant collection outlives its registry
binding. A PURGED collection must not: measured in the #644 review, 200
create + ``purge=true`` cycles left 201 live stores in the bank, because the
purge emptied the object and never forgot it. These tests drive the real
create and delete routes over a memory-backed app; only the embedder is stubbed.
"""
from __future__ import annotations

import pytest

from ragstack.api import deps, security
from ragstack.api.collections import CollectionEntry
from ragstack.api.main import app
from ragstack.api.security import ROLE_ADMIN
from ragstack.stores import InMemoryTextIndex, InMemoryVectorStore
from tests.api.conftest import SHARED_ID

pytestmark = pytest.mark.asyncio


class _FakeEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


@pytest.fixture
def memory_app(client, monkeypatch, tmp_path):
    """The conftest app, switched to the memory vector backend with a bank
    seeded exactly as the lifespan seeds it (the shared surface's store under
    its physical name)."""
    monkeypatch.setattr(deps.settings, "vector_backend", "memory")
    monkeypatch.setattr(deps.settings, "text_backend", "memory")
    monkeypatch.setattr(deps.settings, "require_durable_backends", False)
    monkeypatch.setattr(deps.settings, "collection_manifest_dir", str(tmp_path / "m"))
    monkeypatch.setattr(deps, "_embedder_for_spec", lambda http, spec: _FakeEmbedder())
    monkeypatch.setattr(security.settings, "api_keys", [])
    monkeypatch.setattr(security.settings, "default_role", ROLE_ADMIN)
    bank: dict[str, InMemoryVectorStore] = {SHARED_ID: app.state.vector_store}
    monkeypatch.setattr(app.state, "memory_vector_stores", bank, raising=False)
    return client, bank


async def _create(client, cid: str) -> CollectionEntry:
    r = await client.post("/v1/collections", json={"id": cid})
    assert r.status_code == 201, r.text
    return app.state.collections.resolve(cid)


async def test_create_purge_cycles_return_the_bank_to_its_seeded_size(memory_app):
    client, bank = memory_app
    seeded = dict(bank)
    for i in range(20):
        entry = await _create(client, f"cycle-{i}")
        assert isinstance(entry.vector_store, InMemoryVectorStore)
        assert bank[entry.collection] is entry.vector_store
        r = await client.delete(f"/v1/collections/cycle-{i}?purge=true")
        assert r.status_code == 200, r.text
        assert entry.collection not in bank, f"purge left cycle-{i}'s store in the bank"
    assert bank == seeded, f"bank grew: {sorted(bank)}"
    assert bank[SHARED_ID] is app.state.vector_store, "the seeded default was pruned"


async def test_unregister_keeps_the_store_and_purging_the_last_alias_forgets_it(memory_app):
    """``purge=false`` (allowed only while another entry claims the store)
    keeps the bank entry; purging while an alias still claims it is refused and
    keeps the shared object for the alias; purging the LAST claimant forgets it."""
    client, bank = memory_app
    a = await _create(client, "shared-a")
    phys = a.collection
    await a.vector_store.upsert([])  # the object the alias will share
    alias = CollectionEntry(
        id="shared-b", label="shared-b", collection=phys, model=a.model, dim=a.dim,
        chunk_method=a.chunk_method, chunk_size=a.chunk_size, chunk_overlap=a.chunk_overlap,
        chunk_params={}, is_shared_surface=False, retriever=None,
        vector_store=bank[phys], text_index=InMemoryTextIndex(), text_index_name=phys,
    )
    app.state.collections.add(alias)

    # Purging one of two aliases is refused, and the shared store stays.
    r = await client.delete("/v1/collections/shared-a?purge=true")
    assert r.status_code == 409, r.text
    assert bank[phys] is a.vector_store

    # Unregistering (no purge) drops the binding, not the store.
    r = await client.delete("/v1/collections/shared-a")
    assert r.status_code == 204, r.text
    assert bank[phys] is alias.vector_store, "unregister forgot a store the alias serves"

    # Purging the last claimant forgets it.
    r = await client.delete("/v1/collections/shared-b?purge=true")
    assert r.status_code == 200, r.text
    assert phys not in bank
    assert bank[SHARED_ID] is app.state.vector_store
