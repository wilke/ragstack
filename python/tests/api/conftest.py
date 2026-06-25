"""Shared fixtures for in-process API tests.

The app's lifespan builds its singletons against real infra (Qdrant, an
embedding endpoint) and is not triggered by httpx's ASGITransport. This fixture
populates ``app.state`` with in-memory, network-free doubles so the API can be
exercised without standing up any services.
"""
from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from ragstack.api.main import app
from ragstack.ingestion.backends import LocalAsyncIORunner
from ragstack.ingestion.chunkers import RecursiveCharacterChunker
from ragstack.ingestion.loaders import default_loader_registry
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.ingestion.sharded import ShardedIngestor
from ragstack.jobstore import InMemoryJobStore
from ragstack.quota import TenantQuota
from ragstack.stores import InMemoryTextIndex, InMemoryVectorStore


class _FakeEmbedder:
    """Deterministic constant-dimension embedder — no network."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


@pytest_asyncio.fixture
async def client():
    embedder = _FakeEmbedder()
    vector_store = InMemoryVectorStore()
    text_index = InMemoryTextIndex()
    job_store = InMemoryJobStore()
    pipeline = IngestionPipeline(
        loader=default_loader_registry(),
        chunker=RecursiveCharacterChunker(),
        embedder=embedder,
        vector_store=vector_store,
        text_index=text_index,
    )

    tenant_quota = TenantQuota(0)
    ingestor = ShardedIngestor(
        pipeline,
        LocalAsyncIORunner(max_concurrency=4),
        shard_size=64,
        job_store=job_store,
        quota=tenant_quota,
    )

    app.state.embedder = embedder
    app.state.vector_store = vector_store
    app.state.text_index = text_index
    app.state.job_store = job_store
    app.state.pipeline = pipeline
    app.state.ingestor = ingestor
    app.state.tenant_quota = tenant_quota

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
