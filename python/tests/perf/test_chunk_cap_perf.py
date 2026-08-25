"""Perf rule of the chunk cap (#291 / #355): ONE ``count()`` per ingest job.

A 1,000-document job through ``ShardedIngestor.ingest_manifest`` with a cap
must make exactly one ``count`` call on the vector store and otherwise the
same per-item calls the uncapped path makes (one upsert + one delete-prior per
document) — no per-chunk store call of any kind was added. Every method the
store receives is recorded, so a new call site shows up as a new key, not as a
slower run. The gate itself (count + size the job, no embed, no write) over the
same 1,000 documents is budgeted at p95 < 500 ms (measured ~200 ms).

Run with ``-m perf`` (``addopts`` excludes perf tests by default).
"""
from __future__ import annotations

import pytest

from ragstack.ingestion.backends import LocalAsyncIORunner
from ragstack.ingestion.chunk_cap import ChunkCapExceeded
from ragstack.ingestion.manifest import Manifest, WorkItem
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.ingestion.sharded import ShardedIngestor
from ragstack.models import Chunk, Document
from ragstack.stores.memory import InMemoryTextIndex, InMemoryVectorStore
from tests.perf._budget import assert_budget_async

N_DOCS = 1_000
CHUNKS_PER_DOC = 34  # the measured per-article figure the cap is derived from


class RecordingVectorStore:
    """Every attribute access is recorded before delegating, so the assertion
    is over the full set of store calls the job made — not a hand-picked few."""

    def __init__(self) -> None:
        self._inner = InMemoryVectorStore()
        self.calls: dict[str, int] = {}

    def __getattr__(self, name: str):
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        async def _counted(*a, **kw):
            self.calls[name] = self.calls.get(name, 0) + 1
            return await attr(*a, **kw)
        return _counted


class _FakeEmbedder:
    async def embed(self, texts):
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


class _Loader:
    def load(self, source: str) -> list[Document]:
        return [Document(id=source, content="x", source=source)]


class _Chunker:
    def chunk(self, doc: Document) -> list[Chunk]:
        return [Chunk(id=f"{doc.id}#{i}", doc_id=doc.id, content=f"c{i}")
                for i in range(CHUNKS_PER_DOC)]


def _manifest() -> Manifest:
    return Manifest(items=[WorkItem(item_id=f"d{i}", source=f"d{i}") for i in range(N_DOCS)])


def _ingestor(store: RecordingVectorStore) -> ShardedIngestor:
    pipeline = IngestionPipeline(loader=_Loader(), chunker=_Chunker(), embedder=_FakeEmbedder(),
                                 vector_store=store, text_index=InMemoryTextIndex())
    return ShardedIngestor(pipeline, LocalAsyncIORunner(max_concurrency=8), shard_size=64)


@pytest.mark.perf
async def test_one_count_per_1000_doc_job_and_no_per_chunk_store_calls():
    cap = N_DOCS * CHUNKS_PER_DOC  # exactly at the cap: 34,000 chunks admitted
    capped = RecordingVectorStore()
    results = await _ingestor(capped).ingest_manifest(_manifest(), chunk_cap=cap)
    assert len(results) == N_DOCS and all(r.status == "completed" for r in results)

    uncapped = RecordingVectorStore()
    await _ingestor(uncapped).ingest_manifest(_manifest())

    # The capped job's store calls are the uncapped job's plus EXACTLY one count.
    assert capped.calls.pop("count") == 1
    assert capped.calls == uncapped.calls == {"delete": N_DOCS, "upsert": N_DOCS}
    assert await capped._inner.count() == cap

    # A refused job: still one count, and nothing else reached the store.
    refused = RecordingVectorStore()
    with pytest.raises(ChunkCapExceeded) as ei:
        await _ingestor(refused).ingest_manifest(_manifest(), chunk_cap=cap - 1)
    assert refused.calls == {"count": 1}
    assert (ei.value.incoming, ei.value.would_fit) == (cap, cap - 1)


@pytest.mark.perf
async def test_admission_gate_over_1000_docs_p95_budget():
    """The gate alone — one count plus sizing 1,000 documents (34k chunks of
    text, no embed, no write) — p95 under 500 ms."""
    ingestor = _ingestor(RecordingVectorStore())
    items = _manifest().items

    async def _gate_once() -> None:
        prepared = await ingestor._admit(items, None, N_DOCS * CHUNKS_PER_DOC)
        assert len(prepared) == N_DOCS

    await assert_budget_async("chunk_cap_admit_1000_docs", _gate_once, budget_s=0.5, n=20)
