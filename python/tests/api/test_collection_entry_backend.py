"""``build_collection_entry`` honours ``VECTOR_BACKEND`` for non-default
collections exactly as the default collection does (#392).

The defect: the spec-entry path constructed a real ``QdrantVectorStore`` with no
``vector_backend`` branch, so an "all in-memory" test server still opened a
Qdrant client at ``qdrant_url`` for every collection it created — the
production instance on the dev host, or, once that URL was dead-pinned, a store
that refused every write while the default collection's accepted them.

These tests build REAL entries through the real builder and assert on the real
store objects' types and behaviour. Nothing here stubs the entry: the store
factory (``_vector_store_for``) is the seam under test, so replacing it would
make every assertion below vacuous.

No live store is reachable from this file. ``qdrant_url`` is pinned to a dead
port by the conftest, and the qdrant-backend tests only need construction (the
client is lazy) plus a swallowed, fast-failing ``ensure_collection``.
"""
from __future__ import annotations

import httpx
import pytest

from ragstack.api import deps
from ragstack.collection_store import CollectionSpec
from ragstack.models import Chunk
from ragstack.stores import InMemoryTextIndex, InMemoryVectorStore

pytestmark = pytest.mark.asyncio

DEAD = "http://127.0.0.1:1"


class _FakeEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


def _spec(cid: str) -> CollectionSpec:
    return CollectionSpec(
        id=cid, collection=f"phys_{cid}", embedding_api="sidecar",
        embedding_model="test/model", embedding_model_dim=4,
        chunk_method="fixed", chunk_size=200, chunk_overlap=20,
    )


def _chunk(cid: str, doc: str = "d1") -> Chunk:
    return Chunk(id=cid, doc_id=doc, content="hello world", embedding=[0.1, 0.2, 0.3, 0.4])


@pytest.fixture
def dead_stores(monkeypatch):
    """Every store URL dead, both backends configurable per test."""
    monkeypatch.setattr(deps.settings, "qdrant_url", DEAD)
    monkeypatch.setattr(deps.settings, "qdrant_collection_routes", {})
    monkeypatch.setattr(deps.settings, "elasticsearch_url", DEAD)
    monkeypatch.setattr(deps.settings, "require_durable_backends", False)
    monkeypatch.setattr(deps.settings, "text_backend", "memory")
    monkeypatch.setattr(deps.settings, "collection_manifest_dir", "")


async def _build(spec: CollectionSpec, bank: dict | None):
    async with httpx.AsyncClient() as http:
        return await deps.build_collection_entry(
            http, graph_store=None, spec=spec, embedder=_FakeEmbedder(),
            memory_vector_stores=bank,
        )


# --------------------------------------------------------------------------- #
# vector_backend=memory: one isolated in-memory store per collection
# --------------------------------------------------------------------------- #
async def test_memory_backend_gives_each_collection_its_own_in_memory_store(
    dead_stores, monkeypatch,
):
    monkeypatch.setattr(deps.settings, "vector_backend", "memory")
    bank: dict[str, InMemoryVectorStore] = {}

    a = await _build(_spec("alpha"), bank)
    b = await _build(_spec("beta"), bank)

    assert isinstance(a.vector_store, InMemoryVectorStore)
    assert isinstance(b.vector_store, InMemoryVectorStore)
    assert a.vector_store is not b.vector_store, "two collections share one store"
    # The text leg already branched on its backend; pin that it still does.
    assert isinstance(a.text_index, InMemoryTextIndex)
    assert a.text_index is not b.text_index

    # A write to one is invisible in the other...
    await a.vector_store.upsert([_chunk("c1")])
    assert await a.vector_store.count() == 1
    assert await b.vector_store.count() == 0, "alpha's chunk leaked into beta"
    assert await b.vector_store.get_chunks(["c1"]) == []

    # ...and purging one does not empty the other.
    await b.vector_store.upsert([_chunk("c2")])
    assert await a.vector_store.drop_collection() is True
    assert await a.vector_store.count() == 0
    assert await b.vector_store.count() == 1, "dropping alpha emptied beta"


async def test_memory_store_is_keyed_by_physical_name_for_the_process(
    dead_stores, monkeypatch,
):
    """Re-building an entry over the same physical collection (unregister, then
    register again under the same name) serves the SAME store, so the chunks a
    job wrote are still there — the in-memory analogue of a Qdrant collection
    outliving its registry binding."""
    monkeypatch.setattr(deps.settings, "vector_backend", "memory")
    bank: dict[str, InMemoryVectorStore] = {}

    first = await _build(_spec("alpha"), bank)
    await first.vector_store.upsert([_chunk("c1")])
    again = await _build(_spec("alpha"), bank)

    assert again.vector_store is first.vector_store
    assert await again.vector_store.count() == 1
    assert set(bank) == {"phys_alpha"}, "the bank is keyed by physical name"


async def test_memory_backend_without_a_bank_is_still_isolated(dead_stores, monkeypatch):
    """No ``app.state`` bank (an in-process caller): each build gets a fresh
    store rather than a shared module-level one."""
    monkeypatch.setattr(deps.settings, "vector_backend", "memory")
    a = await _build(_spec("alpha"), None)
    b = await _build(_spec("alpha"), None)
    assert isinstance(a.vector_store, InMemoryVectorStore)
    assert a.vector_store is not b.vector_store
    await a.vector_store.upsert([_chunk("c1")])
    assert await b.vector_store.count() == 0


# --------------------------------------------------------------------------- #
# vector_backend=qdrant: a real Qdrant store, never a silent fallback
# --------------------------------------------------------------------------- #
async def test_qdrant_backend_with_a_dead_url_still_gets_a_qdrant_store(
    dead_stores, monkeypatch,
):
    """A misconfigured production must fail loudly at the store, not quietly
    serve RAM: with the backend set to qdrant and the URL dead, the entry still
    carries a ``QdrantVectorStore`` bound to the spec's physical collection. The
    best-effort ``ensure_collection`` fails fast against port 1 and is swallowed,
    exactly as before."""
    from ragstack.stores.qdrant import QdrantVectorStore

    monkeypatch.setattr(deps.settings, "vector_backend", "qdrant")
    bank: dict[str, InMemoryVectorStore] = {}

    entry = await _build(_spec("alpha"), bank)

    assert isinstance(entry.vector_store, QdrantVectorStore)
    assert not isinstance(entry.vector_store, InMemoryVectorStore)
    assert entry.vector_store._collection == "phys_alpha"
    assert bank == {}, "the qdrant path must not touch the in-memory bank"
    # And the dead URL is what it was built with — no fallback to a live default.
    assert deps._qdrant_url_for("phys_alpha") == DEAD


async def test_memory_backend_is_refused_under_require_durable(dead_stores, monkeypatch):
    """The same gate ``_build_vector_store`` applies to the default collection:
    an in-memory store is never handed to a deployment that demanded durability."""
    monkeypatch.setattr(deps.settings, "vector_backend", "memory")
    monkeypatch.setattr(deps.settings, "require_durable_backends", True)
    with pytest.raises(RuntimeError, match="not durable"):
        await _build(_spec("alpha"), {})


# --------------------------------------------------------------------------- #
# The startup registry hands every spec the same bank
# --------------------------------------------------------------------------- #
async def test_registry_builds_spec_entries_from_the_bank(dead_stores, monkeypatch):
    """``_build_collection_registry`` passes the lifespan's bank through, and a
    spec that CLAIMS the settings-derived physical name serves the very store
    the lifespan seeded under that name — one physical name, one store."""
    monkeypatch.setattr(deps.settings, "vector_backend", "memory")
    monkeypatch.setattr(deps.settings, "default_collection_id", "")
    monkeypatch.setattr(deps.settings, "qdrant_collection_explicit", "")
    monkeypatch.setattr(deps.settings, "elasticsearch_index", "ragstack")
    monkeypatch.setattr(deps.settings, "collections_file", "")
    monkeypatch.setattr(
        deps.settings, "collections_json",
        '[{"id": "named", "collection": "phys_named", "text_index": "phys_named", '
        '"embedding_api": "sidecar", "embedding_model": "test/model", '
        '"embedding_model_dim": 4, "chunk_method": "fixed"}]',
    )
    default_vs, default_ti = InMemoryVectorStore(), InMemoryTextIndex()
    bank: dict[str, InMemoryVectorStore] = {"ragstack": default_vs}

    async with httpx.AsyncClient() as http:
        registry = await deps._build_collection_registry(
            http, graph_store=None, default_embedder=_FakeEmbedder(),
            default_vector_store=default_vs, default_text_index=default_ti,
            default_retriever=None, default_collection="ragstack",
            memory_vector_stores=bank,
        )

    named = registry.resolve("named")
    assert isinstance(named.vector_store, InMemoryVectorStore)
    assert named.vector_store is bank["phys_named"]
    assert named.vector_store is not default_vs
    assert registry.resolve("ragstack").vector_store is default_vs
