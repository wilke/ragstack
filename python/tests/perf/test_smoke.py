"""Smoke test for the perf convention: an in-memory hybrid retrieve, timed over
>= 20 reps with a generous budget so it never flakes on a loaded CI-less box.
"""
import asyncio

import pytest

from ragstack.models import Chunk
from ragstack.retrieval.retriever import HybridRetriever
from ragstack.stores import InMemoryTextIndex, InMemoryVectorStore
from tests.perf._budget import assert_budget


class _FakeEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t)), 1.0] for t in texts]


async def _seed() -> HybridRetriever:
    vector_store = InMemoryVectorStore()
    text_index = InMemoryTextIndex()
    chunks = [
        Chunk(id=f"c{i}", doc_id="doc-1", content=f"chunk number {i} about ragstack perf")
        for i in range(50)
    ]
    for c in chunks:
        c.embedding = [float(len(c.content)), 1.0]
    await vector_store.upsert(chunks)
    await text_index.index(chunks)
    return HybridRetriever(vector_store, text_index, _FakeEmbedder())


@pytest.mark.perf
def test_inmemory_hybrid_retrieve_p95_budget():
    retriever = asyncio.run(_seed())

    async def _retrieve_once() -> None:
        await retriever.retrieve("ragstack perf", top_k=5, use_graph=False)

    assert_budget(
        "inmemory_hybrid_retrieve",
        _retrieve_once,
        budget_s=0.05,
        n=20,
    )
