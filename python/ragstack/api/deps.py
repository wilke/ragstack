"""API dependency wiring.

Builds the embedder, vector store, and ingestion pipeline at FastAPI
startup, hangs them off ``app.state``, and exposes ``Depends()``
providers for the routers. Qdrant is used when available; the in-memory
fallback keeps unit tests and demo runs functional without infra.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request

from ragstack.config import settings
from ragstack.embedders import make_embedder
from ragstack.ingestion.chunkers import RecursiveCharacterChunker
from ragstack.ingestion.loaders import TextFileLoader
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.stores import InMemoryTextIndex, InMemoryVectorStore

log = logging.getLogger(__name__)


def _build_vector_store():
    """Return the configured VectorStore. Falls back to in-memory when
    Qdrant is unavailable so the API still boots in dev / tests."""
    if settings.vector_backend == "qdrant":
        try:
            from ragstack.stores.qdrant import QdrantVectorStore

            return QdrantVectorStore(
                url=settings.qdrant_url,
                collection=settings.qdrant_collection,
                vector_size=settings.embedding_model_dim,
                api_key=settings.qdrant_api_key or None,
            )
        except ImportError:
            log.warning(
                "qdrant-client not installed — falling back to InMemoryVectorStore. "
                "Install ragstack[vector] to use Qdrant."
            )
    return InMemoryVectorStore()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Construct singletons at startup; tear them down at shutdown."""
    http_client = httpx.AsyncClient(timeout=120.0)
    embedder = make_embedder(
        api=settings.embedding_api,
        http=http_client,
        base_url=settings.embedding_sidecar_url,
        model=settings.embedding_model or None,
        api_key=settings.openai_api_key or None,
    )
    vector_store = _build_vector_store()
    text_index = InMemoryTextIndex()  # ElasticsearchTextIndex lands in a later cut

    # Qdrant: make sure the collection exists at startup so the first request doesn't race.
    if hasattr(vector_store, "ensure_collection"):
        try:
            await vector_store.ensure_collection()
            log.info(
                "qdrant collection ready: %s (vector_size=%d)",
                settings.qdrant_collection,
                settings.embedding_model_dim,
            )
        except Exception as e:
            log.warning("qdrant ensure_collection failed: %s", e)

    pipeline = IngestionPipeline(
        loader=TextFileLoader(),
        chunker=RecursiveCharacterChunker(
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
        ),
        embedder=embedder,
        vector_store=vector_store,
        text_index=text_index,
    )

    app.state.http_client = http_client
    app.state.embedder = embedder
    app.state.vector_store = vector_store
    app.state.text_index = text_index
    app.state.pipeline = pipeline

    try:
        yield
    finally:
        await http_client.aclose()


def get_pipeline(request: Request) -> IngestionPipeline:
    return request.app.state.pipeline


def get_vector_store(request: Request):
    return request.app.state.vector_store


def get_embedder(request: Request):
    return request.app.state.embedder
