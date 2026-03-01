"""Unit tests for in-memory stores."""
import pytest

from ragstack.models import Chunk, Triple
from ragstack.stores.memory import InMemoryGraphStore, InMemoryTextIndex, InMemoryVectorStore


def _chunk(cid: str, doc_id: str = "doc1", content: str = "hello world") -> Chunk:
    return Chunk(id=cid, doc_id=doc_id, content=content)


# ── Vector store ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_vector_store_upsert_and_search():
    store = InMemoryVectorStore()
    chunk = _chunk("c1")
    chunk.embedding = [1.0, 0.0]
    await store.upsert([chunk])
    results = await store.search([1.0, 0.0], top_k=5)
    assert len(results) == 1
    assert results[0].chunk.id == "c1"
    assert results[0].score == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_vector_store_returns_empty_for_no_embeddings():
    store = InMemoryVectorStore()
    chunk = _chunk("c1")
    # No embedding set
    await store.upsert([chunk])
    results = await store.search([1.0, 0.0])
    assert results == []


@pytest.mark.asyncio
async def test_vector_store_delete():
    store = InMemoryVectorStore()
    chunk = _chunk("c1")
    chunk.embedding = [1.0, 0.0]
    await store.upsert([chunk])
    await store.delete("doc1")
    results = await store.search([1.0, 0.0])
    assert results == []


@pytest.mark.asyncio
async def test_vector_store_metadata_filter():
    store = InMemoryVectorStore()
    c1 = _chunk("c1", content="alpha")
    c1.embedding = [1.0, 0.0]
    c1.metadata = {"lang": "en"}
    c2 = _chunk("c2", content="beta")
    c2.embedding = [1.0, 0.0]
    c2.metadata = {"lang": "fr"}
    await store.upsert([c1, c2])
    results = await store.search([1.0, 0.0], filters={"lang": "en"})
    assert len(results) == 1
    assert results[0].chunk.id == "c1"


# ── Text index ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_text_index_search_finds_matching_chunks():
    index = InMemoryTextIndex()
    chunk = _chunk("c1", content="the quick brown fox")
    await index.index([chunk])
    results = await index.search("quick fox")
    assert len(results) == 1
    assert results[0].chunk.id == "c1"


@pytest.mark.asyncio
async def test_text_index_search_returns_empty_for_no_match():
    index = InMemoryTextIndex()
    chunk = _chunk("c1", content="the quick brown fox")
    await index.index([chunk])
    results = await index.search("elephant")
    assert results == []


@pytest.mark.asyncio
async def test_text_index_delete():
    index = InMemoryTextIndex()
    chunk = _chunk("c1")
    await index.index([chunk])
    await index.delete("doc1")
    results = await index.search("hello")
    assert results == []


# ── Graph store ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_graph_store_add_and_query():
    store = InMemoryGraphStore()
    triple = Triple(subject="Alice", predicate="knows", object="Bob", doc_id="doc1")
    await store.add_triples([triple])
    results = await store.query_neighborhood("Alice")
    assert len(results) == 1
    assert results[0].object == "Bob"


@pytest.mark.asyncio
async def test_graph_store_deduplicates_triples():
    store = InMemoryGraphStore()
    triple = Triple(subject="A", predicate="likes", object="B")
    await store.add_triples([triple, triple])
    all_triples = await store.query_neighborhood("A")
    assert len(all_triples) == 1


@pytest.mark.asyncio
async def test_graph_store_delete_by_doc():
    store = InMemoryGraphStore()
    t1 = Triple(subject="A", predicate="r", object="B", doc_id="doc1")
    t2 = Triple(subject="C", predicate="r", object="D", doc_id="doc2")
    await store.add_triples([t1, t2])
    await store.delete_by_doc("doc1")
    results = await store.query_neighborhood("A")
    assert results == []
    remaining = await store.query_neighborhood("C")
    assert len(remaining) == 1
