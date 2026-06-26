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
from ragstack.embed_pool import make_pooled_embedder
from ragstack.embedders import BatchingEmbedder, make_embedder
from ragstack.ingestion.backends import LocalAsyncIORunner
from ragstack.ingestion.chunkers import RecursiveCharacterChunker
from ragstack.ingestion.loaders import default_loader_registry
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.ingestion.sharded import ShardedIngestor
from ragstack.jobstore import make_job_store
from ragstack.quota import TenantQuota
from ragstack.stores import InMemoryTextIndex, InMemoryVectorStore
from ragstack.stores.errors import VectorDimMismatch

log = logging.getLogger(__name__)


def _build_vector_store():
    """Return the configured VectorStore.

    In dev/tests an unavailable Qdrant degrades to InMemory so the API still
    boots. When ``require_durable_backends`` is set (production), an in-memory
    store is refused: a 500k ingest must not silently land in RAM and vanish on
    restart, so a missing/unusable durable backend is a fatal startup error.
    """
    if settings.vector_backend == "qdrant":
        try:
            from ragstack.stores.qdrant import QdrantVectorStore, collection_name
        except ImportError as e:
            if settings.require_durable_backends:
                raise RuntimeError(
                    "vector_backend='qdrant' but qdrant-client is not installed "
                    "and require_durable_backends is set. Install ragstack[vector]."
                ) from e
            log.warning(
                "qdrant-client not installed — falling back to InMemoryVectorStore. "
                "Install ragstack[vector] to use Qdrant."
            )
            return InMemoryVectorStore()
        return QdrantVectorStore(
            url=settings.qdrant_url,
            # Scope the collection to (model, dim) so swapping embedding models
            # keeps experiments isolated and a dimension change can't land in an
            # incompatible collection.
            collection=collection_name(
                settings.qdrant_collection,
                settings.embedding_model,
                settings.embedding_model_dim,
            ),
            vector_size=settings.embedding_model_dim,
            api_key=settings.qdrant_api_key or None,
        )

    if settings.require_durable_backends:
        raise RuntimeError(
            f"vector_backend={settings.vector_backend!r} is not durable but "
            "require_durable_backends is set; use 'qdrant'."
        )
    return InMemoryVectorStore()


def _build_embedder(http: httpx.AsyncClient):
    """Build the embedder (single endpoint or a load-balanced pool), wrapped in
    BatchingEmbedder. Multiple ``embedding_endpoints`` → a PooledEmbedder with
    failover + backpressure; otherwise the single ``embedding_sidecar_url``."""
    urls = settings.embedding_endpoints or [settings.embedding_sidecar_url]
    common = {
        "api": settings.embedding_api,
        "http": http,
        "model": settings.embedding_model or None,
        "api_key": settings.openai_api_key or None,
    }
    if len(urls) > 1:
        base = make_pooled_embedder(
            base_urls=urls,
            max_concurrency=settings.embedding_max_concurrency,
            health_path=settings.embedding_health_path,
            **common,
        )
        log.info("embedding fan-out across %d endpoints", len(urls))
    else:
        base = make_embedder(base_url=urls[0], **common)
    return BatchingEmbedder(
        base,
        max_batch_items=settings.embedding_max_batch_items,
        max_batch_tokens=settings.embedding_max_batch_tokens,
        chars_per_token=settings.embedding_chars_per_token,
    )


def _build_text_index():
    """Return the text index.

    Only the in-memory index exists today (ElasticsearchTextIndex lands in a
    later cut). Under ``require_durable_backends`` this is a known gap: warn
    loudly rather than hard-fail, so the flag stays usable for its primary
    purpose (a durable vector store). Gate this once Elasticsearch lands.
    """
    if settings.require_durable_backends:
        log.warning(
            "text index is in-memory (Elasticsearch backend not yet implemented); "
            "the lexical index will not survive restart"
        )
    return InMemoryTextIndex()


def _validate_production_settings() -> None:
    """Refuse to start in production without the security-critical settings.

    Without auth, the data API is open; without an ingest_root, request.source
    is an unconfined arbitrary-file read. Both must be set when durability (the
    production marker) is required.
    """
    if not settings.require_durable_backends:
        return
    missing = []
    if not settings.api_keys:
        missing.append("api_keys")
    if not settings.ingest_root:
        missing.append("ingest_root")
    if missing:
        raise RuntimeError(
            "require_durable_backends is set but these production settings are "
            f"unset: {', '.join(missing)}"
        )
    # Fail closed on a partial tenant map: if any key→tenant mapping is set, every
    # configured key must be mapped. Otherwise an unmapped key silently collapses
    # into the shared "default" tenant and loses isolation. (Keys are not logged.)
    if settings.api_key_tenants:
        unmapped = sum(1 for k in settings.api_keys if k not in settings.api_key_tenants)
        if unmapped:
            raise RuntimeError(
                f"api_key_tenants is set but {unmapped} configured api_key(s) have no "
                "tenant mapping; map every key (unmapped keys would share the "
                "'default' tenant and break isolation)"
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Construct singletons at startup; tear them down at shutdown."""
    _validate_production_settings()
    http_client = httpx.AsyncClient(timeout=120.0)
    embedder = _build_embedder(http_client)
    vector_store = _build_vector_store()
    text_index = _build_text_index()

    # Qdrant: make sure the collection exists at startup so the first request doesn't race.
    if hasattr(vector_store, "ensure_collection"):
        try:
            await vector_store.ensure_collection()
            log.info(
                "qdrant collection ready: %s (vector_size=%d)",
                getattr(vector_store, "_collection", settings.qdrant_collection),
                settings.embedding_model_dim,
            )
        except VectorDimMismatch:
            # Fatal misconfiguration — refuse to start rather than write mixed
            # vectors into the wrong collection.
            raise
        except Exception as e:
            # Readiness gate: in production, refuse to start (and thus accept
            # ingests we can't persist) if the durable store isn't reachable.
            if settings.require_durable_backends:
                raise
            log.warning("qdrant ensure_collection failed: %s", e)

    pipeline = IngestionPipeline(
        loader=default_loader_registry(
            ingest_root=settings.ingest_root or None,
            max_bytes=settings.max_document_bytes,
        ),
        chunker=RecursiveCharacterChunker(
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
        ),
        embedder=embedder,
        vector_store=vector_store,
        text_index=text_index,
    )

    job_store = make_job_store(
        settings.job_store_backend, settings.job_store_path, settings.postgres_dsn
    )
    # Ingestion runs as in-process background tasks, so any job left non-terminal
    # in a durable store belongs to a worker that died with the previous process.
    # Mark them failed at startup rather than leaving them stuck "running" forever.
    # Each store decides whether the sweep is safe: the multi-process Postgres
    # store no-ops it (an unscoped sweep would reap sibling workers' live jobs).
    interrupted = await job_store.fail_interrupted()
    if interrupted:
        log.warning(
            "marked %d interrupted ingest job(s) as failed at startup", interrupted
        )

    tenant_quota = TenantQuota(settings.tenant_max_concurrency)
    if 0 < settings.embedding_max_concurrency <= settings.tenant_max_concurrency:
        log.warning(
            "tenant_max_concurrency (%d) >= embedding_max_concurrency (%d): the "
            "per-tenant quota won't isolate tenants on the shared embedder pool; "
            "set it lower for real fairness.",
            settings.tenant_max_concurrency,
            settings.embedding_max_concurrency,
        )
    ingestor = ShardedIngestor(
        pipeline,
        LocalAsyncIORunner(max_concurrency=settings.ingest_concurrency),
        shard_size=settings.ingest_shard_size,
        job_store=job_store,
        quota=tenant_quota,
    )

    app.state.http_client = http_client
    app.state.embedder = embedder
    app.state.vector_store = vector_store
    app.state.text_index = text_index
    app.state.pipeline = pipeline
    app.state.job_store = job_store
    app.state.ingestor = ingestor
    app.state.tenant_quota = tenant_quota

    try:
        yield
    finally:
        await http_client.aclose()
        # Release the job store's resources (PostgresJobStore's asyncpg pool;
        # a no-op for the in-memory / sqlite stores).
        await job_store.close()


def get_pipeline(request: Request) -> IngestionPipeline:
    return request.app.state.pipeline


def get_vector_store(request: Request):
    return request.app.state.vector_store


def get_embedder(request: Request):
    return request.app.state.embedder


def get_job_store(request: Request):
    return request.app.state.job_store


def get_ingestor(request: Request):
    return request.app.state.ingestor


def get_tenant_quota(request: Request):
    return request.app.state.tenant_quota
