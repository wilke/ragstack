"""Unit tests for IngestionPipeline — re-ingest replaces, doesn't accumulate."""
import pytest

from ragstack.ingestion.chunkers import RecursiveCharacterChunker
from ragstack.ingestion.pipeline import EmptyIngestError, IngestionPipeline
from ragstack.models import Chunk, Document, Triple
from ragstack.stores import InMemoryGraphStore, InMemoryTextIndex, InMemoryVectorStore


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


class _MultiDocLoader:
    """Load several documents with stable ids — models a multi-doc source (a JSONL
    shard) re-ingested after some documents' content changed."""

    def __init__(self, docs: list[tuple[str, str]]) -> None:
        self._docs = docs

    def load(self, source: str) -> list[Document]:
        return [Document(id=i, content=c, source=source) for i, c in self._docs]


class _PoisonEmbedder:
    """Embeds normally, but quarantines any chunk whose content contains ``poison``
    — models one document becoming fully unembeddable on a re-run."""

    def __init__(self, poison: str | None = None) -> None:
        self.poison = poison

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t)), 1.0] for t in texts]

    async def embed_isolated(self, texts: list[str]):
        out = [None if (self.poison and self.poison in t) else [float(len(t)), 1.0]
               for t in texts]
        return out, sum(v is None for v in out)


@pytest.mark.asyncio
async def test_reingest_partial_quarantine_preserves_untouched_docs():
    """#134: in a MULTI-document source, a document whose chunks all quarantine on
    a re-run must keep its prior data — only documents with surviving chunks are
    replaced (regression: delete-prior looped over every loaded doc, so a
    fully-quarantined doc's prior chunks were dropped with no replacement)."""
    vector_store = InMemoryVectorStore()
    text_index = InMemoryTextIndex()
    docs = [("doc-A", "a" * 32), ("doc-B", "b" * 32)]  # distinct char per doc
    chunker = RecursiveCharacterChunker(chunk_size=8, chunk_overlap=0)

    v1 = IngestionPipeline(loader=_MultiDocLoader(docs), chunker=chunker,
                           embedder=_PoisonEmbedder(), vector_store=vector_store,
                           text_index=text_index)
    await v1.ingest("shard.jsonl")
    b_before = {c.id for c in vector_store._chunks if c.doc_id == "doc-B"}
    assert b_before  # doc-B was seeded

    # v2: doc-B is now fully unembeddable (every 'b' chunk poisoned); doc-A embeds.
    v2 = IngestionPipeline(loader=_MultiDocLoader(docs), chunker=chunker,
                           embedder=_PoisonEmbedder(poison="b"), vector_store=vector_store,
                           text_index=text_index)
    ids2 = await v2.ingest("shard.jsonl")  # must NOT raise — doc-A survives

    stored = {c.doc_id for c in vector_store._chunks}
    assert "doc-B" in stored, "doc-B's prior data must survive its full-quarantine re-run"
    # doc-B's prior chunks are unchanged (not deleted), doc-A's are re-ingested.
    assert {c.id for c in vector_store._chunks if c.doc_id == "doc-B"} == b_before
    assert all(cid in {c.id for c in vector_store._chunks} for cid in ids2)
    assert "doc-B" in {c.doc_id for c in text_index._chunks}  # text index preserved too


class _StubExtractor:
    """KGExtractor double: emits a fixed triple per ingest, doc_id from the chunk,
    tenant_id left empty (the pipeline must stamp it)."""

    async def extract(self, chunks: list[Chunk]) -> list[Triple]:
        doc_id = chunks[0].doc_id if chunks else ""
        return [Triple(subject="Alice", predicate="knows", object="Bob", doc_id=doc_id)]


@pytest.mark.asyncio
async def test_ingest_stamps_tenant_on_extracted_triples_and_stores_them():
    vector_store = InMemoryVectorStore()
    text_index = InMemoryTextIndex()
    graph_store = InMemoryGraphStore()
    pipeline = IngestionPipeline(
        loader=_FixedDocLoader("doc-1", "abcdefghijklmnop"),
        chunker=RecursiveCharacterChunker(chunk_size=10, chunk_overlap=0),
        embedder=_FakeEmbedder(),
        vector_store=vector_store,
        text_index=text_index,
        graph_store=graph_store,
        kg_extractor=_StubExtractor(),
    )
    await pipeline.ingest("file.txt", tenant_id="alice")

    stored = graph_store._triples
    assert len(stored) == 1
    assert (stored[0].subject, stored[0].predicate, stored[0].object) == ("Alice", "knows", "Bob")
    assert stored[0].doc_id == "doc-1"
    # The pipeline stamps the owning tenant; the extractor never set it.
    assert stored[0].tenant_id == "alice"


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


class _ExplodingStore:
    """A vector/text store whose every store-mutating call fails the test — proves
    embed_source never contacts the stores (the ADR-0001 / #141 decoupling)."""

    async def delete(self, *a, **k):  # noqa: D401
        raise AssertionError("embed_source must not touch the store (delete)")

    async def upsert(self, *a, **k):
        raise AssertionError("embed_source must not touch the store (upsert)")

    async def index(self, *a, **k):
        raise AssertionError("embed_source must not touch the store (index)")


@pytest.mark.asyncio
async def test_embed_source_does_not_touch_stores():
    """embed_source is GPU-bound only: it returns embedded chunks without any
    vector/text store call (would raise via _ExplodingStore otherwise)."""
    pipeline = IngestionPipeline(
        loader=_FixedDocLoader("doc-1", "abcdefghijklmnopqrst"),
        chunker=RecursiveCharacterChunker(chunk_size=10, chunk_overlap=0),
        embedder=_FakeEmbedder(),
        vector_store=_ExplodingStore(),
        text_index=_ExplodingStore(),
    )
    chunks = await pipeline.embed_source("file.txt")
    assert chunks, "expected surviving embedded chunks"
    assert all(c.embedding is not None for c in chunks)
    assert all(c.metadata.get("tenant_id") for c in chunks)


@pytest.mark.asyncio
async def test_ingest_equals_embed_then_index():
    """ingest() is a literal composition: embed_source → index_chunks produces the
    same chunk ids and the same store state as the coupled ingest()."""
    coupled_vs, coupled_ti = InMemoryVectorStore(), InMemoryTextIndex()
    coupled = IngestionPipeline(
        loader=_FixedDocLoader("doc-1", "abcdefghijklmnopqrst"),
        chunker=RecursiveCharacterChunker(chunk_size=10, chunk_overlap=0),
        embedder=_FakeEmbedder(),
        vector_store=coupled_vs, text_index=coupled_ti,
    )
    coupled_ids = await coupled.ingest("file.txt")

    split_vs, split_ti = InMemoryVectorStore(), InMemoryTextIndex()
    split = IngestionPipeline(
        loader=_FixedDocLoader("doc-1", "abcdefghijklmnopqrst"),
        chunker=RecursiveCharacterChunker(chunk_size=10, chunk_overlap=0),
        embedder=_FakeEmbedder(),
        vector_store=split_vs, text_index=split_ti,
    )
    chunks = await split.embed_source("file.txt")
    split_ids = await split.index_chunks(chunks)

    assert split_ids == coupled_ids
    assert {c.id for c in split_vs._chunks} == {c.id for c in coupled_vs._chunks}
    assert {c.id for c in split_ti._chunks} == {c.id for c in coupled_ti._chunks}


@pytest.mark.asyncio
async def test_index_chunks_is_self_contained_from_chunks():
    """index_chunks needs only the chunks — no Document list. Chunks embedded by
    one pipeline load cleanly into a second pipeline's fresh stores (models the
    offline embed-to-file → separate load stage of #141)."""
    embed_only = IngestionPipeline(
        loader=_FixedDocLoader("doc-1", "abcdefghijklmnopqrst"),
        chunker=RecursiveCharacterChunker(chunk_size=10, chunk_overlap=0),
        embedder=_FakeEmbedder(),
        vector_store=_ExplodingStore(), text_index=_ExplodingStore(),
    )
    chunks = await embed_only.embed_source("file.txt")

    vs, ti = InMemoryVectorStore(), InMemoryTextIndex()
    loader_pipeline = IngestionPipeline(
        loader=_FixedDocLoader("unused", "unused"),  # never called by index_chunks
        chunker=RecursiveCharacterChunker(chunk_size=10, chunk_overlap=0),
        embedder=_FakeEmbedder(),
        vector_store=vs, text_index=ti,
    )
    ids = await loader_pipeline.index_chunks(chunks)
    assert set(ids) == {c.id for c in chunks}
    assert {c.id for c in vs._chunks} == set(ids)


@pytest.mark.asyncio
async def test_embed_source_raises_on_empty_without_store_contact():
    """An empty source raises EmptyIngestError inside embed_source — before any
    store mutation — so the store-mutating half is never reached."""
    pipeline = IngestionPipeline(
        loader=_FixedDocLoader("doc-1", ""),
        chunker=RecursiveCharacterChunker(chunk_size=10, chunk_overlap=0),
        embedder=_FakeEmbedder(),
        vector_store=_ExplodingStore(), text_index=_ExplodingStore(),
    )
    with pytest.raises(EmptyIngestError):
        await pipeline.embed_source("file.txt")
