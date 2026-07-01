"""API dependency wiring.

Builds the embedder, vector store, and ingestion pipeline at FastAPI
startup, hangs them off ``app.state``, and exposes ``Depends()``
providers for the routers. Qdrant is used when available; the in-memory
fallback keeps unit tests and demo runs functional without infra.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TypedDict

import httpx
from fastapi import FastAPI, Request

from ragstack.config import settings
from ragstack.embed_pool import make_pooled_embedder
from ragstack.embedders import BatchingEmbedder, make_embedder
from ragstack.graph.extractor import LLMKGExtractor
from ragstack.ingestion.backends import LocalAsyncIORunner
from ragstack.ingestion.chunkers import make_chunker
from ragstack.ingestion.embed_bridge import SyncEmbedBridge
from ragstack.ingestion.enrich import resolve_profile
from ragstack.ingestion.loaders import default_loader_registry
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.ingestion.sharded import ShardedIngestor
from ragstack.ingestion.tokenization import make_token_counter, resolve_max_tokens
from ragstack.jobstore import make_job_store
from ragstack.llm import OpenAILLM, RagGenerator
from ragstack.protocols import QueryRewriter
from ragstack.quota import TenantQuota
from ragstack.retrieval.retriever import HybridRetriever
from ragstack.rewriting.rewriters import (
    HyDERewriter,
    MultiQueryRewriter,
    PassthroughRewriter,
)
from ragstack.scoring.scorers import SidecarReranker
from ragstack.stores import InMemoryGraphStore, InMemoryTextIndex, InMemoryVectorStore
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


class _CommonEmbedderKwargs(TypedDict):
    api: str
    http: httpx.AsyncClient
    model: str | None
    api_key: str | None


def _build_embedder(http: httpx.AsyncClient):
    """Build the embedder (single endpoint or a load-balanced pool), wrapped in
    BatchingEmbedder. Multiple ``embedding_endpoints`` → a PooledEmbedder with
    failover + backpressure; otherwise the single ``embedding_sidecar_url``."""
    urls = settings.embedding_endpoints or [settings.embedding_sidecar_url]
    common: _CommonEmbedderKwargs = {
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
    """Return the text index. ``text_backend=elasticsearch`` is the durable BM25
    backend used for hybrid retrieval; otherwise the in-memory Jaccard placeholder
    (non-durable — warned under require_durable_backends)."""
    if settings.text_backend == "elasticsearch":
        try:
            from ragstack.stores.elasticsearch import ElasticsearchTextIndex
        except ImportError as e:
            if settings.require_durable_backends:
                raise RuntimeError(
                    "text_backend='elasticsearch' but the elasticsearch client is "
                    "not installed. Install ragstack[text]."
                ) from e
            log.warning("elasticsearch client not installed — using in-memory text index")
            return InMemoryTextIndex()
        return ElasticsearchTextIndex(
            settings.elasticsearch_url,
            settings.elasticsearch_index,
            settings.elasticsearch_api_key or None,
        )

    if settings.require_durable_backends:
        log.warning(
            "text index is in-memory (text_backend=memory); set "
            "text_backend=elasticsearch for durable BM25 + hybrid retrieval"
        )
    return InMemoryTextIndex()


def _build_graph_store():
    """Return the configured GraphStore (knowledge graph), or ``None`` when graph
    support is disabled.

    ``graph_backend=neo4j`` selects the durable Neo4j property-graph backend; the
    ``neo4j`` driver is the optional ``graph`` extra and is imported lazily, so an
    unconfigured/uninstalled graph degrades to the in-memory store in dev (and to
    ``None`` only when explicitly disabled). Under ``require_durable_backends`` a
    selected-but-unavailable Neo4j is fatal — the same readiness contract as the
    vector/text backends — rather than silently dropping the graph.
    """
    if settings.graph_backend == "neo4j":
        try:
            from ragstack.stores.neo4j import Neo4jGraphStore
        except ImportError as e:  # pragma: no cover - module has no hard import
            if settings.require_durable_backends:
                raise RuntimeError(
                    "graph_backend='neo4j' but the neo4j driver is not installed "
                    "and require_durable_backends is set. Install ragstack[graph]."
                ) from e
            log.warning(
                "neo4j driver not installed — falling back to InMemoryGraphStore. "
                "Install ragstack[graph] to use Neo4j."
            )
            return InMemoryGraphStore()
        return Neo4jGraphStore(
            uri=settings.neo4j_uri,
            user=settings.neo4j_user,
            password=settings.neo4j_password,
            database=settings.neo4j_database or None,
        )

    if settings.graph_backend == "disabled":
        return None

    if settings.require_durable_backends:
        log.warning(
            "knowledge graph is in-memory (graph_backend=memory); set "
            "graph_backend=neo4j for a durable graph"
        )
    return InMemoryGraphStore()


def _build_llm(http: httpx.AsyncClient) -> OpenAILLM | None:
    """The shared OpenAI-compatible LLM client (answer generation + rewriters), or
    None when no endpoint is configured."""
    if not settings.llm_endpoint:
        return None
    log.info("LLM enabled: %s @ %s", settings.llm_model, settings.llm_endpoint)
    return OpenAILLM(
        base_url=settings.llm_endpoint,
        model=settings.llm_model,
        http=http,
        api_key=settings.openai_api_key or None,
    )


def _build_kg_extractor(llm: OpenAILLM | None) -> LLMKGExtractor | None:
    """The LLM knowledge-graph triple extractor, or ``None`` when disabled.

    Built only when ``kg_extraction_enabled`` is set AND an LLM is configured —
    extraction is an LLM cost, so it's opt-in and a no-op without an endpoint.
    The pipeline only runs it when a graph store is also present, so an enabled
    extractor with ``graph_backend=disabled`` is harmless."""
    if not settings.kg_extraction_enabled or llm is None:
        return None
    log.info(
        "kg extraction enabled (max_chunks=%d, max_triples_per_chunk=%d)",
        settings.kg_extraction_max_chunks,
        settings.kg_extraction_max_triples_per_chunk,
    )
    return LLMKGExtractor(
        llm,
        max_chunks=settings.kg_extraction_max_chunks,
        max_triples_per_chunk=settings.kg_extraction_max_triples_per_chunk,
    )


def _build_rewriters(llm: OpenAILLM | None) -> dict[str, QueryRewriter]:
    """Query-rewriter registry keyed by strategy name. Passthrough is always
    available; the LLM-backed strategies only when an LLM is configured."""
    rewriters: dict[str, QueryRewriter] = {"passthrough": PassthroughRewriter()}
    if llm is not None:
        rewriters["multiquery"] = MultiQueryRewriter(llm)
        rewriters["hyde"] = HyDERewriter(llm)
    return rewriters


def _build_reranker(http: httpx.AsyncClient) -> SidecarReranker | None:
    """The cross-encoder reranker (sidecar HTTP client), or None when disabled.

    Opt-in via ``rerank_enabled``; an unreachable sidecar is *not* fatal here —
    the query path catches rerank failures and falls back to the fused order, so
    a reranker outage degrades quality rather than availability."""
    if not settings.rerank_enabled:
        return None
    log.info("reranker enabled: %s @ %s", settings.reranker_model, settings.crossencoder_sidecar_url)
    return SidecarReranker(base_url=settings.crossencoder_sidecar_url, http=http)


def _build_chunker():
    """Build the configured chunker.

    Returns ``(chunker, embed_bridge)``. ``embed_bridge`` is non-None only for
    ``chunk_method=semantic``: that chunker embeds sentence buffers synchronously
    from inside the (async) ingestion pipeline, so we hand it a sync bridge. The
    bridge builds its *own* embedder + httpx client on its background loop (via
    ``_build_embedder``) — not the app's main-loop client, which would otherwise
    raise a cross-loop error — and is closed at shutdown.
    """
    method = settings.chunk_method
    if method not in ("fixed", "sentence", "words", "semantic"):
        log.warning("unknown chunk_method %r — falling back to 'fixed'", method)
        method = "fixed"
    bridge: SyncEmbedBridge | None = None
    embed_fn = None
    if method == "semantic":
        bridge = SyncEmbedBridge(_build_embedder)
        embed_fn = bridge

    # Token-based sizing is opt-in (chunk_max_tokens set). When off, pass nothing
    # so the char-budget path is unchanged and no tokenizer/endpoint is touched at
    # startup. When on, build a TokenCounter and resolve the per-chunk budget from
    # the embedding endpoint so /v1/ingest never emits an over-window chunk.
    #
    # The ``fixed_token`` method ALSO needs a TokenCounter (its window is the sizing
    # unit) even when chunk_max_tokens is unset — and specifically the HF fast
    # tokenizer's offset mapping, so force the 'hf' backend and require a model.
    # Without this, make_chunker('fixed_token') would raise the opaque
    # "requires a token_counter" at startup.
    max_tokens: int | None = None
    token_counter = None
    if settings.chunk_max_tokens is not None or method == "fixed_token":
        base_url = (settings.embedding_endpoints or [settings.embedding_sidecar_url])[0]
        api_key = settings.openai_api_key or None
        model = settings.embedding_model or None
        # fixed_token forces the HF backend (it needs the fast tokenizer's offset
        # map) and requires a model to load that tokenizer.
        if method == "fixed_token" and not model:
            raise ValueError(
                "chunk_method='fixed_token' requires embedding_model (its sliding "
                "token window is built from that model's HF tokenizer)"
            )
        counter_backend = "hf" if method == "fixed_token" else settings.chunk_token_counter
        token_counter = make_token_counter(
            counter_backend,
            model=model,
            base_url=base_url,
            api_key=api_key,
        )
        if settings.chunk_max_tokens is not None:
            max_tokens = resolve_max_tokens(
                settings.chunk_max_tokens,
                base_url=base_url,
                api_key=api_key,
            )
            log.info(
                "token-based chunk sizing on: max_tokens=%d counter=%s",
                max_tokens,
                counter_backend,
            )

    chunker = make_chunker(
        method,
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        embed_fn=embed_fn,
        buffer_size=settings.chunk_buffer_size,
        breakpoint_percentile_threshold=settings.chunk_breakpoint_percentile,
        min_chunk_length=settings.chunk_min_length,
        max_tokens=max_tokens,
        token_counter=token_counter,
    )
    log.info("chunker: chunk_method=%s", method)
    return chunker, bridge


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
    graph_store = _build_graph_store()

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

    # Neo4j: create the entity uniqueness constraint at startup; same readiness
    # gate as Qdrant/Elasticsearch (fatal under require_durable_backends).
    if graph_store is not None and hasattr(graph_store, "ensure_schema"):
        try:
            await graph_store.ensure_schema()
            log.info("neo4j graph schema ready")
        except Exception as e:
            if settings.require_durable_backends:
                raise
            log.warning("neo4j ensure_schema failed: %s", e)

    # Elasticsearch: create the index at startup; same readiness gate as Qdrant.
    if hasattr(text_index, "ensure_index"):
        try:
            await text_index.ensure_index()
            log.info("elasticsearch index ready: %s", settings.elasticsearch_index)
        except Exception as e:
            if settings.require_durable_backends:
                raise
            log.warning("elasticsearch ensure_index failed: %s", e)

    # One LLM client, shared by answer generation, the query rewriters, and the
    # knowledge-graph extractor. Built before the pipeline so the (optional)
    # extractor can be wired into ingestion.
    llm = _build_llm(http_client)
    kg_extractor = _build_kg_extractor(llm)

    chunker, embed_bridge = _build_chunker()
    pipeline = IngestionPipeline(
        loader=default_loader_registry(
            ingest_root=settings.ingest_root or None,
            max_bytes=settings.max_document_bytes,
            profile=resolve_profile(settings.publisher_profile),
        ),
        chunker=chunker,
        embedder=embedder,
        vector_store=vector_store,
        text_index=text_index,
        graph_store=graph_store,
        kg_extractor=kg_extractor,
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

    # Hybrid retrieval: fuse dense (vector) + BM25 (text) + optional graph context
    # via RRF. With the in-memory text index the BM25 leg still works (Jaccard),
    # but it's the Elasticsearch backend that makes it real; the graph leg is
    # active only when a graph store is configured.
    retriever = HybridRetriever(vector_store, text_index, embedder, graph_store=graph_store)

    app.state.http_client = http_client
    app.state.embedder = embedder
    app.state.vector_store = vector_store
    app.state.text_index = text_index
    app.state.graph_store = graph_store
    app.state.kg_extractor = kg_extractor
    app.state.pipeline = pipeline
    app.state.embed_bridge = embed_bridge
    app.state.job_store = job_store
    app.state.ingestor = ingestor
    app.state.generator = RagGenerator(llm) if llm is not None else None
    app.state.tenant_quota = tenant_quota
    app.state.retriever = retriever
    app.state.rewriters = _build_rewriters(llm)
    app.state.reranker = _build_reranker(http_client)

    # Best-effort: the sidecar picks its own model from MODEL_NAME, so a mismatched
    # deploy would silently rerank with a different model than config advertises.
    # Probe /health at startup and warn loudly on a mismatch (don't fail — the
    # sidecar may still be warming up).
    if app.state.reranker is not None:
        try:
            resp = await http_client.get(
                f"{settings.crossencoder_sidecar_url.rstrip('/')}/health", timeout=5.0
            )
            actual = resp.json().get("model")
            if actual and actual != settings.reranker_model:
                log.warning(
                    "reranker model mismatch: sidecar loaded %r but config.reranker_model "
                    "is %r — rerank scores will not reflect the advertised model",
                    actual,
                    settings.reranker_model,
                )
            else:
                log.info("reranker model confirmed: %s", actual)
        except Exception as e:
            log.warning("could not verify reranker model at startup: %s", e)

    try:
        yield
    finally:
        await http_client.aclose()
        # Release the job store's resources (PostgresJobStore's asyncpg pool;
        # a no-op for the in-memory / sqlite stores).
        await job_store.close()
        # Close the ES client if the text index holds one.
        if hasattr(text_index, "close"):
            await text_index.close()
        # Close the Neo4j driver if the graph store holds one.
        if graph_store is not None and hasattr(graph_store, "close"):
            await graph_store.close()
        # Stop the semantic chunker's background embed loop, if any.
        if embed_bridge is not None:
            embed_bridge.close()


def get_pipeline(request: Request) -> IngestionPipeline:
    return request.app.state.pipeline


def get_vector_store(request: Request):
    return request.app.state.vector_store


def get_text_index(request: Request):
    return request.app.state.text_index


def get_graph_store(request: Request):
    """The configured GraphStore, or ``None`` when graph support is disabled."""
    return request.app.state.graph_store


def get_kg_extractor(request: Request):
    """The configured KGExtractor, or ``None`` when KG extraction is disabled."""
    return request.app.state.kg_extractor


def get_embedder(request: Request):
    return request.app.state.embedder


def get_generator(request: Request):
    return request.app.state.generator


def get_retriever(request: Request):
    return request.app.state.retriever


def get_rewriters(request: Request):
    return request.app.state.rewriters


def get_reranker(request: Request):
    return request.app.state.reranker


def get_job_store(request: Request):
    return request.app.state.job_store


def get_ingestor(request: Request):
    return request.app.state.ingestor


def get_tenant_quota(request: Request):
    return request.app.state.tenant_quota
