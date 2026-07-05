"""Unit tests for the decoupled embed/load stages (ADR-0001 offline plane, #141):
the embedding-file contract, run_embed_shard (no store contact), and
run_load_file reusing index_chunks — an end-to-end embed→file→load round-trip
that reconstructs the same store state as the coupled ingest()."""
import json

import pytest

from ragstack.ingestion.chunkers import RecursiveCharacterChunker
from ragstack.ingestion.embed_shard import run_embed_shard
from ragstack.ingestion.embedding_file import (
    SCHEMA,
    EmbeddingFileError,
    read_embedding_file,
    read_header,
    write_embedding_file,
)
from ragstack.ingestion.load_embeddings import run_load_file
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.ingestion.receipts import COMPLETED, FAILED
from ragstack.models import Chunk, Document
from ragstack.stores import InMemoryTextIndex, InMemoryVectorStore


class _FixedDocLoader:
    def __init__(self, doc_id, content):
        self.doc_id, self.content = doc_id, content

    def load(self, source):
        return [Document(id=self.doc_id, content=self.content, source=source)]


class _FakeEmbedder:
    async def embed(self, texts):
        return [[float(len(t)), 1.0, 2.0] for t in texts]


class _ExplodingStore:
    async def delete(self, *a, **k):
        raise AssertionError("embed stage must not touch the store")

    async def upsert(self, *a, **k):
        raise AssertionError("embed stage must not touch the store")

    async def index(self, *a, **k):
        raise AssertionError("embed stage must not touch the store")


def _embed_pipeline(content="abcdefghijklmnopqrst", stores_explode=True):
    vs = _ExplodingStore() if stores_explode else InMemoryVectorStore()
    ti = _ExplodingStore() if stores_explode else InMemoryTextIndex()
    return IngestionPipeline(
        loader=_FixedDocLoader("doc-1", content),
        chunker=RecursiveCharacterChunker(chunk_size=10, chunk_overlap=0),
        embedder=_FakeEmbedder(), vector_store=vs, text_index=ti,
    )


# --- embedding-file contract ------------------------------------------------- #

def test_embedding_file_roundtrip(tmp_path):
    chunks = [
        Chunk(id="a", doc_id="d", content="x", embedding=[1.0, 2.0, 3.0], metadata={"k": 1}),
        Chunk(id="b", doc_id="d", content="y", embedding=[4.0, 5.0, 6.0]),
    ]
    path = tmp_path / "e.jsonl"
    write_embedding_file(path, chunks, tenant="public")
    header = read_header(path)
    assert header["schema"] == SCHEMA and header["dim"] == 3
    assert header["count"] == 2 and header["tenant"] == "public"
    loaded, _ = read_embedding_file(path)
    assert [c.id for c in loaded] == ["a", "b"]
    assert loaded[0].embedding == [1.0, 2.0, 3.0] and loaded[0].metadata == {"k": 1}


def test_embedding_file_is_deterministic(tmp_path):
    chunks = [Chunk(id="a", doc_id="d", content="x", embedding=[1.0, 2.0, 3.0])]
    p1, p2 = tmp_path / "1.jsonl", tmp_path / "2.jsonl"
    write_embedding_file(p1, chunks, tenant="t")
    write_embedding_file(p2, chunks, tenant="t")
    assert p1.read_bytes() == p2.read_bytes()


def test_write_refuses_unembedded_chunk(tmp_path):
    chunks = [Chunk(id="a", doc_id="d", content="x", embedding=None)]
    with pytest.raises(EmbeddingFileError, match="no embedding"):
        write_embedding_file(tmp_path / "e.jsonl", chunks)


def test_write_refuses_nonuniform_dims(tmp_path):
    chunks = [
        Chunk(id="a", doc_id="d", content="x", embedding=[1.0, 2.0]),
        Chunk(id="b", doc_id="d", content="y", embedding=[1.0, 2.0, 3.0]),
    ]
    with pytest.raises(EmbeddingFileError, match="non-uniform"):
        write_embedding_file(tmp_path / "e.jsonl", chunks)


def test_read_rejects_wrong_schema(tmp_path):
    p = tmp_path / "e.jsonl"
    p.write_text(json.dumps({"schema": "bogus/v9", "dim": 3, "count": 0}) + "\n")
    with pytest.raises(EmbeddingFileError, match="unknown schema"):
        read_embedding_file(p)


def test_read_rejects_dim_mismatch(tmp_path):
    p = tmp_path / "e.jsonl"
    lines = [
        json.dumps({"schema": SCHEMA, "tenant": "", "dim": 3, "count": 1}),
        json.dumps({"id": "a", "doc_id": "d", "content": "x", "embedding": [1.0, 2.0]}),
    ]
    p.write_text("\n".join(lines) + "\n")
    with pytest.raises(EmbeddingFileError, match="dim 2 != header 3"):
        read_embedding_file(p)


# --- run_embed_shard --------------------------------------------------------- #

@pytest.mark.asyncio
async def test_run_embed_shard_writes_file_without_store_contact(tmp_path):
    out = tmp_path / "shard.emb.jsonl"
    receipt = await run_embed_shard(_embed_pipeline(), "file.txt", "public", "s0", out)
    assert receipt.status == COMPLETED
    assert receipt.embedding_file == str(out) and receipt.n_chunks > 0
    chunks, header = read_embedding_file(out)
    assert header["tenant"] == "public"
    assert len(chunks) == receipt.n_chunks
    assert all(c.metadata.get("tenant_id") == "public" for c in chunks)


@pytest.mark.asyncio
async def test_run_embed_shard_empty_source_fails_soft(tmp_path):
    receipt = await run_embed_shard(_embed_pipeline(content=""), "file.txt", "public",
                                    "s0", tmp_path / "e.jsonl")
    assert receipt.status == FAILED and receipt.error.startswith("empty:")
    assert not (tmp_path / "e.jsonl").exists()  # no file written on failure


# --- run_load_file + round-trip ---------------------------------------------- #

@pytest.mark.asyncio
async def test_load_file_reuses_index_chunks(tmp_path):
    # Embed to a file with an exploding-store pipeline...
    emb = tmp_path / "shard.emb.jsonl"
    await run_embed_shard(_embed_pipeline(), "file.txt", "public", "s0", emb)
    # ...then load it into fresh in-memory stores via a load pipeline.
    vs, ti = InMemoryVectorStore(), InMemoryTextIndex()
    load_pipeline = IngestionPipeline(
        loader=_FixedDocLoader("unused", "unused"),  # never called by the load path
        chunker=RecursiveCharacterChunker(), embedder=_FakeEmbedder(),
        vector_store=vs, text_index=ti,
    )
    receipt = await run_load_file(load_pipeline, emb, file_id="s0")
    assert receipt.status == COMPLETED
    assert {c.id for c in vs._chunks} == set(receipt.chunk_ids)
    assert {c.id for c in ti._chunks} == set(receipt.chunk_ids)


@pytest.mark.asyncio
async def test_embed_then_load_equals_coupled_ingest(tmp_path):
    """embed→file→load reconstructs the same store state as coupled ingest()."""
    coupled_vs, coupled_ti = InMemoryVectorStore(), InMemoryTextIndex()
    coupled = IngestionPipeline(
        loader=_FixedDocLoader("doc-1", "abcdefghijklmnopqrst"),
        chunker=RecursiveCharacterChunker(chunk_size=10, chunk_overlap=0),
        embedder=_FakeEmbedder(), vector_store=coupled_vs, text_index=coupled_ti,
    )
    coupled_ids = set(await coupled.ingest("file.txt"))

    emb = tmp_path / "shard.emb.jsonl"
    await run_embed_shard(_embed_pipeline(), "file.txt", "public", "s0", emb)
    split_vs, split_ti = InMemoryVectorStore(), InMemoryTextIndex()
    split = IngestionPipeline(
        loader=_FixedDocLoader("unused", "unused"), chunker=RecursiveCharacterChunker(),
        embedder=_FakeEmbedder(), vector_store=split_vs, text_index=split_ti,
    )
    await run_load_file(split, emb, file_id="s0")
    assert {c.id for c in split_vs._chunks} == coupled_ids
    assert {c.id for c in split_ti._chunks} == coupled_ids


@pytest.mark.asyncio
async def test_load_file_missing_file_fails_soft(tmp_path):
    load_pipeline = IngestionPipeline(
        loader=_FixedDocLoader("u", "u"), chunker=RecursiveCharacterChunker(),
        embedder=_FakeEmbedder(),
        vector_store=InMemoryVectorStore(), text_index=InMemoryTextIndex(),
    )
    receipt = await run_load_file(load_pipeline, tmp_path / "nope.jsonl", file_id="s0")
    assert receipt.status == FAILED and "read:" in receipt.error


# --- tenant override (regression: #143 review) ------------------------------- #

class _MultiDocLoader:
    def load(self, source):
        return [
            Document(id="d1", content="abcdefghijklmnopqrst", source=source),
            Document(id="d2", content="0123456789abcdefghij", source=source),
        ]


class _QuarantineFirstEmbedder:
    """Embeds every chunk, but the isolating path drops the first one (poison)."""

    async def embed(self, texts):
        return [[1.0, 1.0, 1.0] for _ in texts]

    async def embed_isolated(self, texts):
        vecs = [None if i == 0 else [float(i), 1.0, 2.0] for i in range(len(texts))]
        return vecs, sum(1 for v in vecs if v is None)


async def _load_pipeline(vs, ti):
    return IngestionPipeline(
        loader=_FixedDocLoader("unused", "unused"), chunker=RecursiveCharacterChunker(),
        embedder=_FakeEmbedder(), vector_store=vs, text_index=ti,
    )


@pytest.mark.asyncio
async def test_load_defaults_to_header_tenant(tmp_path):
    from ragstack.tenancy import tenant_of
    emb = tmp_path / "s.emb.jsonl"
    await run_embed_shard(_embed_pipeline(), "file.txt", "public", "s0", emb)
    vs, ti = InMemoryVectorStore(), InMemoryTextIndex()
    await run_load_file(await _load_pipeline(vs, ti), emb, file_id="s0")  # no override
    assert vs._chunks and all(tenant_of(c) == "public" for c in vs._chunks)
    assert all(tenant_of(c) == "public" for c in ti._chunks)


@pytest.mark.asyncio
async def test_load_tenant_override_restamps_delete_and_upsert(tmp_path):
    """--tenant override must scope BOTH the delete-prior and the upsert to the
    override tenant — not delete under the override while writing under the
    embed-time tenant (the #143-review data-orphaning bug)."""
    from ragstack.tenancy import tenant_of
    emb = tmp_path / "s.emb.jsonl"
    await run_embed_shard(_embed_pipeline(), "file.txt", "public", "s0", emb)

    vs, ti = InMemoryVectorStore(), InMemoryTextIndex()
    receipt = await run_load_file(await _load_pipeline(vs, ti), emb, file_id="s0",
                                  tenant="acme")
    assert receipt.status == COMPLETED and receipt.tenant == "acme"
    # Everything landed under 'acme' — nothing stranded under 'public'.
    assert vs._chunks and all(tenant_of(c) == "acme" for c in vs._chunks)
    assert all(tenant_of(c) == "acme" for c in ti._chunks)

    # Re-loading under 'acme' replaces in place (idempotent), doesn't accumulate.
    n_before = len(vs._chunks)
    await run_load_file(await _load_pipeline(vs, ti), emb, file_id="s0", tenant="acme")
    assert len(vs._chunks) == n_before


# --- multi-doc + quarantine embed gaps (#143 review) ------------------------- #

@pytest.mark.asyncio
async def test_embed_shard_multi_doc(tmp_path):
    pipeline = IngestionPipeline(
        loader=_MultiDocLoader(),
        chunker=RecursiveCharacterChunker(chunk_size=10, chunk_overlap=0),
        embedder=_FakeEmbedder(),
        vector_store=_ExplodingStore(), text_index=_ExplodingStore(),
    )
    out = tmp_path / "s.emb.jsonl"
    receipt = await run_embed_shard(pipeline, "file.txt", "public", "s0", out)
    assert receipt.status == COMPLETED and receipt.n_docs == 2
    chunks, _ = read_embedding_file(out)
    assert {c.doc_id for c in chunks} == {"d1", "d2"}
    # Neighbor chain never crosses documents: each doc's first chunk has no prev.
    for doc_id in ("d1", "d2"):
        first = [c for c in chunks if c.doc_id == doc_id][0]
        assert first.metadata.get("prev_chunk_id") is None


@pytest.mark.asyncio
async def test_embed_shard_quarantines_poison_and_writes_clean_file(tmp_path):
    pipeline = IngestionPipeline(
        loader=_FixedDocLoader("doc-1", "abcdefghijklmnopqrst"),
        chunker=RecursiveCharacterChunker(chunk_size=10, chunk_overlap=0),
        embedder=_QuarantineFirstEmbedder(),
        vector_store=_ExplodingStore(), text_index=_ExplodingStore(),
    )
    out = tmp_path / "s.emb.jsonl"
    receipt = await run_embed_shard(pipeline, "file.txt", "public", "s0", out)
    assert receipt.status == COMPLETED
    chunks, header = read_embedding_file(out)
    # The poison (first) chunk is dropped; the streamed file is complete + uniform-dim.
    # Streamed files omit the header count — receipt.n_chunks is authoritative.
    assert len(chunks) == receipt.n_chunks and "count" not in header
    assert all(c.embedding is not None for c in chunks)


# --- header robustness + make_embedder_auto (#143 review) -------------------- #

def test_read_embedding_file_tolerates_missing_count(tmp_path):
    p = tmp_path / "e.jsonl"
    lines = [
        json.dumps({"schema": SCHEMA, "tenant": "", "dim": 3}),  # no 'count'
        json.dumps({"id": "a", "doc_id": "d", "content": "x", "embedding": [1.0, 2.0, 3.0]}),
    ]
    p.write_text("\n".join(lines) + "\n")
    chunks, _ = read_embedding_file(p)
    assert len(chunks) == 1  # guard skipped, not a spurious mismatch


@pytest.mark.asyncio
async def test_make_embedder_auto_picks_single_vs_pooled():
    import httpx

    from ragstack.embed_pool import PooledEmbedder, make_embedder_auto
    from ragstack.embedders import OpenAIEmbedder
    async with httpx.AsyncClient() as http:
        one = make_embedder_auto(api="openai", http=http, base_urls=["http://a"],
                                 model="m")
        many = make_embedder_auto(api="openai", http=http,
                                  base_urls=["http://a", "http://b"], model="m")
    assert isinstance(one, OpenAIEmbedder)
    assert isinstance(many, PooledEmbedder)


# --- streaming embed (memory-bounded large shards) --------------------------- #

@pytest.mark.asyncio
async def test_iter_embed_source_matches_embed_source():
    """The streaming iterator yields the same surviving chunks (ids + order) as the
    materialized embed_source — grouping is transparent to the result."""
    def _mk():
        return IngestionPipeline(
            loader=_MultiDocLoader(),
            chunker=RecursiveCharacterChunker(chunk_size=10, chunk_overlap=0),
            embedder=_FakeEmbedder(),
            vector_store=_ExplodingStore(), text_index=_ExplodingStore(),
        )
    materialized = await _mk().embed_source("file.txt", tenant_id="public")
    streamed = []
    async for group in _mk().iter_embed_source("file.txt", tenant_id="public", group_size=1):
        streamed.extend(group)
    assert [c.id for c in streamed] == [c.id for c in materialized]
    assert all(c.embedding is not None for c in streamed)
    # group_size=1 → one group per document (whole docs never split)
    assert {c.doc_id for c in streamed} == {"d1", "d2"}


@pytest.mark.asyncio
async def test_iter_embed_source_neighbor_links_within_group():
    """Each document's chunks are neighbor-linked even when streamed one doc/group:
    the first chunk of each doc has no prev link."""
    pipeline = IngestionPipeline(
        loader=_MultiDocLoader(),
        chunker=RecursiveCharacterChunker(chunk_size=10, chunk_overlap=0),
        embedder=_FakeEmbedder(),
        vector_store=_ExplodingStore(), text_index=_ExplodingStore(),
    )
    chunks = []
    async for group in pipeline.iter_embed_source("file.txt", tenant_id="public", group_size=1):
        chunks.extend(group)
    for doc_id in ("d1", "d2"):
        first = [c for c in chunks if c.doc_id == doc_id][0]
        assert first.metadata.get("prev_chunk_id") is None


def test_embedding_file_writer_roundtrip(tmp_path):
    from ragstack.ingestion.embedding_file import EmbeddingFileWriter
    path = tmp_path / "w.jsonl"
    chunks = [
        Chunk(id="a", doc_id="d", content="x", embedding=[1.0, 2.0, 3.0], metadata={"k": 1}),
        Chunk(id="b", doc_id="d", content="y", embedding=[4.0, 5.0, 6.0]),
    ]
    with EmbeddingFileWriter(path, tenant="public") as w:
        for c in chunks:
            w.write(c)
    assert w.count == 2
    loaded, header = read_embedding_file(path)
    assert [c.id for c in loaded] == ["a", "b"] and header["dim"] == 3
    assert header["tenant"] == "public" and "count" not in header  # streamed: no count


def test_embedding_file_writer_no_write_no_file(tmp_path):
    from ragstack.ingestion.embedding_file import EmbeddingFileWriter
    path = tmp_path / "empty.jsonl"
    with EmbeddingFileWriter(path) as w:
        pass
    assert w.count == 0 and not path.exists()  # lazily created only on first write
