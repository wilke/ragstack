"""Unit tests for IngestionPipeline — re-ingest replaces, doesn't accumulate."""
import pytest

from ragstack.ingestion.chunkers import RecursiveCharacterChunker
from ragstack.ingestion.pipeline import EmptyIngestError, IngestionPipeline
from ragstack.models import Document
from ragstack.stores import InMemoryTextIndex, InMemoryVectorStore


class _FixedDocLoader:
    """Load a document with a stable id but caller-controlled content — models a
    file at a fixed path being edited between ingests (TextFileLoader derives the
    doc id from the resolved path, so it stays constant across edits)."""

    def __init__(self, doc_id: str, content: str) -> None:
        self.doc_id = doc_id
        self.content = content

    def load(self, source: str) -> list[Document]:
        return [Document(id=self.doc_id, content=self.content, source=source)]


class _FakeEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t)), 1.0] for t in texts]


class _AllQuarantineEmbedder:
    """Quarantines every input — models a re-ingest where no chunk embeds."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 1.0] for _ in texts]

    async def embed_isolated(self, texts: list[str]):
        return [None] * len(texts), len(texts)


async def _seed(vector_store, text_index, content: str = "abcdefghijklmnop") -> set[str]:
    """Ingest one document successfully and return its chunk IDs."""
    pipeline = IngestionPipeline(
        loader=_FixedDocLoader("doc-1", content),
        chunker=RecursiveCharacterChunker(chunk_size=10, chunk_overlap=0),
        embedder=_FakeEmbedder(),
        vector_store=vector_store,
        text_index=text_index,
    )
    return set(await pipeline.ingest("file.txt"))


@pytest.mark.asyncio
async def test_reingest_edited_document_replaces_chunks():
    vector_store = InMemoryVectorStore()
    text_index = InMemoryTextIndex()
    chunker = RecursiveCharacterChunker(chunk_size=10, chunk_overlap=0)

    # v1: a long document chunked into several passages.
    v1 = IngestionPipeline(
        loader=_FixedDocLoader("doc-1", "abcdefghijklmnopqrstuvwxyz0123456789"),
        chunker=chunker,
        embedder=_FakeEmbedder(),
        vector_store=vector_store,
        text_index=text_index,
    )
    ids_v1 = await v1.ingest("file.txt")
    assert len(ids_v1) > 1

    # v2: same doc id, different/shorter content -> shifted boundaries, new ids.
    v2 = IngestionPipeline(
        loader=_FixedDocLoader("doc-1", "hello"),
        chunker=chunker,
        embedder=_FakeEmbedder(),
        vector_store=vector_store,
        text_index=text_index,
    )
    ids_v2 = await v2.ingest("file.txt")

    # No orphans: the stores hold exactly the v2 chunks, none from v1.
    assert {c.id for c in vector_store._chunks} == set(ids_v2)
    assert {c.id for c in text_index._chunks} == set(ids_v2)
    assert set(ids_v1).isdisjoint({c.id for c in vector_store._chunks})


@pytest.mark.asyncio
async def test_reingest_identical_document_is_idempotent():
    vector_store = InMemoryVectorStore()
    text_index = InMemoryTextIndex()
    pipeline = IngestionPipeline(
        loader=_FixedDocLoader("doc-1", "abcdefghijklmnopqrstuvwxyz"),
        chunker=RecursiveCharacterChunker(chunk_size=10, chunk_overlap=0),
        embedder=_FakeEmbedder(),
        vector_store=vector_store,
        text_index=text_index,
    )
    first = await pipeline.ingest("file.txt")
    second = await pipeline.ingest("file.txt")
    assert first == second
    assert len(vector_store._chunks) == len(first)


@pytest.mark.asyncio
async def test_reingest_with_all_chunks_quarantined_preserves_prior_data():
    """A re-ingest where every chunk is unembeddable must fail without deleting
    the document's previously-ingested chunks (regression: the replace step
    deleted prior data then upserted nothing)."""
    vector_store = InMemoryVectorStore()
    text_index = InMemoryTextIndex()
    seeded = await _seed(vector_store, text_index)

    doomed = IngestionPipeline(
        loader=_FixedDocLoader("doc-1", "qrstuvwxyz0123456789"),
        chunker=RecursiveCharacterChunker(chunk_size=10, chunk_overlap=0),
        embedder=_AllQuarantineEmbedder(),
        vector_store=vector_store,
        text_index=text_index,
    )
    with pytest.raises(EmptyIngestError):
        await doomed.ingest("file.txt")

    # Prior data is untouched.
    assert {c.id for c in vector_store._chunks} == seeded
    assert {c.id for c in text_index._chunks} == seeded


@pytest.mark.asyncio
async def test_reingest_empty_document_preserves_prior_data():
    """An empty re-ingest (no chunks produced) fails and leaves prior data."""
    vector_store = InMemoryVectorStore()
    text_index = InMemoryTextIndex()
    seeded = await _seed(vector_store, text_index)

    empty = IngestionPipeline(
        loader=_FixedDocLoader("doc-1", ""),
        chunker=RecursiveCharacterChunker(chunk_size=10, chunk_overlap=0),
        embedder=_FakeEmbedder(),
        vector_store=vector_store,
        text_index=text_index,
    )
    with pytest.raises(EmptyIngestError):
        await empty.ingest("file.txt")

    assert {c.id for c in vector_store._chunks} == seeded
    assert {c.id for c in text_index._chunks} == seeded
