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
