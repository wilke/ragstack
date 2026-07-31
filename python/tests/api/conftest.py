"""Shared fixtures for in-process API tests.

The app's lifespan builds its singletons against real infra (Qdrant, an
embedding endpoint) and is not triggered by httpx's ASGITransport. This fixture
populates ``app.state`` with in-memory, network-free doubles so the API can be
exercised without standing up any services.
"""
from __future__ import annotations

import tempfile

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from ragstack.api.collections import CollectionEntry, CollectionRegistry
from ragstack.api.main import app
from ragstack.api.model_registry import ModelRegistry
from ragstack.ingestion.backends import LocalAsyncIORunner
from ragstack.ingestion.chunkers import RecursiveCharacterChunker
from ragstack.ingestion.loaders import default_loader_registry
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.ingestion.sharded import ShardedIngestor
from ragstack.jobstore import InMemoryJobStore
from ragstack.quota import TenantQuota
from ragstack.retrieval.retriever import HybridRetriever
from ragstack.rewriting.rewriters import PassthroughRewriter
from ragstack.stores import InMemoryGraphStore, InMemoryTextIndex, InMemoryVectorStore


class _FakeEmbedder:
    """Deterministic constant-dimension embedder — no network."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


class _StateRetriever:
    """The default collection entry's retriever, proxied to the CURRENT
    ``app.state.retriever``. The query path resolves through the registry
    (``registry.resolve(...).retriever``), so tests that swap ``app.state.retriever``
    (e.g. the reranking tests) keep working without also rebuilding the registry."""

    async def retrieve(self, *args: object, **kwargs: object) -> object:
        return await app.state.retriever.retrieve(*args, **kwargs)


@pytest.fixture(autouse=True)
def _isolate_qdrant(monkeypatch):
    """Never touch a real Qdrant from a test. ``POST /v1/collections`` builds a
    live ``QdrantVectorStore`` and calls ``ensure_collection`` — with the default
    ``QDRANT_URL`` (:6333) that would create stray collections on whatever Qdrant
    is reachable (e.g. a prod instance on the dev host, or CI). Pin it to a dead
    port so the ensure step fails fast and is swallowed (best-effort), leaving the
    registry/response assertions intact and nothing created."""
    from ragstack.config import settings

    monkeypatch.setattr(settings, "qdrant_url", "http://localhost:6399")


@pytest.fixture(autouse=True)
def _enable_ingest(monkeypatch):
    """``POST /v1/ingest`` fails closed with 503 when ``ingest_root`` is unset — an
    unconfined ``source`` is an arbitrary server-side file read. Unset is the
    default, so point the root at the temp dir that ``tmp_path`` lives under;
    otherwise every ingest test would be asserting against the gate rather than
    the behaviour it is about. Tests that exercise the gate set it back to ``""``
    themselves (their monkeypatch is applied after this one, so it wins)."""
    from ragstack.config import settings

    monkeypatch.setattr(settings, "ingest_root", tempfile.gettempdir())


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
    app.state.graph_store = InMemoryGraphStore()
    app.state.job_store = job_store
    app.state.pipeline = pipeline
    app.state.ingestor = ingestor
    app.state.generator = None  # no LLM by default → placeholder answer
    app.state.tenant_quota = tenant_quota
    app.state.retriever = HybridRetriever(vector_store, text_index, embedder)
    app.state.rewriters = {"passthrough": PassthroughRewriter()}  # no LLM in tests
    app.state.reranker = None  # rerank off by default → fused order
    # The lifespan builds app.state.collections (the multi-collection registry the
    # query/retrieve/collections routers resolve through); ASGITransport skips the
    # lifespan, so build a one-entry registry over the in-memory doubles here — the
    # single-collection default path.
    app.state.collections = CollectionRegistry(
        [
            CollectionEntry(
                id="default", label="default", collection="ragstack",
                model="test-model", dim=4, chunk_method="fixed", chunk_size=None,
                chunk_overlap=None, chunk_params={},
                is_default=True, retriever=_StateRetriever(),
                vector_store=vector_store, text_index=text_index,
            )
        ],
        default_id="default",
    )
    # Model registry (Phase 1) + a real http client for apply_assignment to hand
    # to any swapped OpenAILLM/SidecarReranker (construction only — no network).
    state_http = httpx.AsyncClient()
    app.state.http_client = state_http
    app.state.model_registry = ModelRegistry(
        [], {}, allowlist=["http://localhost", "http://127.0.0.1"]
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await state_http.aclose()
