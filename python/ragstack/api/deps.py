"""API dependency wiring.

Builds the embedder, vector store, and ingestion pipeline at FastAPI
startup, hangs them off ``app.state``, and exposes ``Depends()``
providers for the routers. Qdrant is used when available; the in-memory
fallback keeps unit tests and demo runs functional without infra.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, TypedDict

import httpx
from fastapi import FastAPI, Request

from ragstack.api.collections import (
    RESERVED_COLLECTION_ID,
    CollectionEntry,
    CollectionRegistry,
    CollectionSpec,
)
from ragstack.api.model_registry import ModelEntry, ModelRegistry
from ragstack.collection_store import (
    CollectionStore,
    JsonFileCollectionStore,
    make_collection_store,
    seed_from_json,
)
from ragstack.config import settings
from ragstack.embed_pool import make_pooled_embedder
from ragstack.embedders import BatchingEmbedder, make_embedder
from ragstack.graph.extractor import LLMKGExtractor
from ragstack.ingestion.backends import make_ingest_backend
from ragstack.ingestion.boilerplate import BoilerplateFilter, config_from_json
from ragstack.ingestion.chunkers import CHUNK_METHODS, make_chunker
from ragstack.ingestion.doi_metadata import DoiEnricher, build_resolver
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
from ragstack.scoring.scorers import RRFScorer, SidecarReranker
from ragstack.stores import InMemoryGraphStore, InMemoryTextIndex, InMemoryVectorStore
from ragstack.stores.errors import VectorDimMismatch

log = logging.getLogger(__name__)


def _qdrant_url_for(collection: str) -> str:
    """The Qdrant base URL serving ``collection``.

    An alternate instance when the collection is routed via
    ``qdrant_collection_routes`` (its own vm.max_map_count budget — see the config
    field), else the default ``qdrant_url``. Keeps single-instance deployments
    byte-for-byte unchanged (empty routes → always ``qdrant_url``)."""
    return (settings.qdrant_collection_routes or {}).get(collection, settings.qdrant_url)


def _derived_collection_name() -> str:
    """The collection the API serves: the explicit override if set, else derived
    from the build spec. Content-addressed over (model, dim, chunk) when
    collection_name_include_chunk is on, so a re-ingest with a different chunker
    routes to a new collection instead of overwriting the old one."""
    from ragstack.provenance import chunk_descriptor
    from ragstack.stores.qdrant import collection_name

    if settings.qdrant_collection_explicit:
        return settings.qdrant_collection_explicit
    chunk = (
        chunk_descriptor(settings.chunk_method, settings.chunk_size, settings.chunk_overlap)
        if settings.collection_name_include_chunk
        else None
    )
    return collection_name(
        settings.qdrant_collection,
        settings.embedding_model,
        settings.embedding_model_dim,
        chunk=chunk,
    )


def write_ingest_manifest(*, source: str, chunk_count: int | None = None) -> None:
    """Write a verified (source='ingest') provenance manifest for the served
    collection after an ingest, overwriting any earlier config-materialized one.
    No-op when manifests are disabled. Best-effort — never raises into the caller."""
    if not settings.collection_manifest_dir:
        return
    try:
        from ragstack.provenance import make_ingest_manifest, write_manifest

        eps = settings.embedding_endpoints or (
            [settings.embedding_sidecar_url] if settings.embedding_sidecar_url else []
        )
        manifest = make_ingest_manifest(
            collection=_derived_collection_name(),
            model=settings.embedding_model, dim=settings.embedding_model_dim,
            embedding_api=settings.embedding_api, embedding_endpoints=eps,
            chunk_method=settings.chunk_method, chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
            corpus=source, chunk_count=chunk_count,
        )
        write_manifest(settings.collection_manifest_dir, manifest)
    except Exception:  # noqa: BLE001 — provenance must never fail an ingest
        log.warning("provenance: ingest manifest write failed", exc_info=True)


def _build_vector_store():
    """Return the configured VectorStore.

    In dev/tests an unavailable Qdrant degrades to InMemory so the API still
    boots. When ``require_durable_backends`` is set (production), an in-memory
    store is refused: a 500k ingest must not silently land in RAM and vanish on
    restart, so a missing/unusable durable backend is a fatal startup error.
    """
    if settings.vector_backend == "qdrant":
        try:
            from ragstack.stores.qdrant import QdrantVectorStore
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
        # An explicit override serves that literal collection verbatim; otherwise
        # derive the name (content-addressed over the build spec when
        # collection_name_include_chunk is on — see _derived_collection_name).
        collection = _derived_collection_name()
        # Route this collection to its Qdrant instance (a second process for a
        # VMA-heavy collection like semantic), defaulting to qdrant_url.
        url = _qdrant_url_for(collection)
        if url != settings.qdrant_url:
            log.info("qdrant: collection %r routed to instance %s", collection, url)
        return QdrantVectorStore(
            url=url,
            collection=collection,
            vector_size=settings.embedding_model_dim,
            api_key=settings.qdrant_api_key or None,
            upsert_batch_size=settings.qdrant_upsert_batch_size,
            upsert_concurrency=settings.qdrant_upsert_concurrency,
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


def _make_embedder(http: httpx.AsyncClient, *, api: str, model: str, urls: list[str]):
    """Build a BatchingEmbedder over ``urls`` for the given api/model — the shared
    core of both the default embedder and each registry collection's embedder."""
    common: _CommonEmbedderKwargs = {
        "api": api,
        "http": http,
        "model": model or None,
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


def embedding_urls() -> list[str]:
    """The configured embedding endpoint URLs — fan-out ``embedding_endpoints``
    override the single ``embedding_sidecar_url``. Single source of truth for both
    the embedder build and the /v1/stats/models status probe, so they can't drift."""
    return settings.embedding_endpoints or [settings.embedding_sidecar_url]


async def probe_tenant_count(store: Any, tenants: list[str]) -> int | None:
    """Tenant-FILTERED chunk count for a store, degrading to ``None`` (never
    raising). Shared by /v1/collections and /v1/stats/stores so a store that is
    missing the method or errors degrades identically in both."""
    if store is None or not hasattr(store, "count_tenants"):
        return None
    try:
        return int(await store.count_tenants(tenants))
    except Exception:
        log.warning("count_tenants probe failed", exc_info=True)
        return None


def _build_embedder(http: httpx.AsyncClient):
    """The default embedder from top-level settings (single-collection path)."""
    return _make_embedder(http, api=settings.embedding_api,
                          model=settings.embedding_model, urls=embedding_urls())


def _default_emb_signature() -> tuple:
    eps = tuple(sorted(settings.embedding_endpoints)) or (settings.embedding_sidecar_url,)
    return (settings.embedding_api, settings.embedding_model, eps, str(settings.embedding_model_dim))


def _materialize_config_manifest(
    collection: str, *, model: str, dim: int, api: str, endpoints: list[str],
    chunk_method: str, chunk_size: int | None, chunk_overlap: int | None,
    chunk_params: dict[str, Any] | None = None,
) -> None:
    """Write a source='config' manifest for a registry collection that has none,
    so pre-existing corpora (ingested before manifests, or out-of-band) still
    report provenance. Never clobbers a source='ingest' (verified) manifest."""
    if not settings.collection_manifest_dir:
        return
    from ragstack.provenance import (
        CollectionManifest,
        chunk_descriptor,
        ragstack_version,
        read_manifest,
        spec_hash,
        write_manifest,
    )

    if read_manifest(settings.collection_manifest_dir, collection) is not None:
        return
    # params belong in the descriptor: they are part of the chunk strategy's
    # identity, so omitting them would hash a config manifest differently from the
    # ingest manifest of the very same build (a spurious "drift").
    params = dict(chunk_params or {})
    desc = chunk_descriptor(chunk_method, chunk_size, chunk_overlap, params or None)
    write_manifest(settings.collection_manifest_dir, CollectionManifest(
        collection=collection, model=model, dim=dim, embedding_api=api,
        embedding_endpoints=endpoints, chunk_method=chunk_method, chunk_size=chunk_size,
        chunk_overlap=chunk_overlap, chunk_params=params,
        spec_hash=spec_hash(model or "", dim, desc),
        ragstack_version=ragstack_version(),
        source="config",
    ))


def _embedder_for_spec(http: httpx.AsyncClient, spec: CollectionSpec) -> Any:
    """The embedder a spec needs (fan-out endpoints or the single sidecar URL)."""
    urls = spec.embedding_endpoints or [spec.embedding_sidecar_url or settings.embedding_sidecar_url]
    return _make_embedder(http, api=spec.embedding_api, model=spec.embedding_model, urls=urls)


def _hybrid_retriever(
    vs: Any, ti: Any, emb: Any, *, graph_store: Any, collection: str
) -> Any:
    """A HybridRetriever over one collection's stores — shared by the startup
    builder and the runtime create path so their retriever wiring can't drift.

    ``collection`` is the physical collection name this retriever serves. The
    vector store and text index are already bound to it; the graph store is the
    single process-wide instance shared by every collection, so the name has to
    be handed to the retriever for the graph leg to be scoped at all (#209)."""
    return HybridRetriever(
        vs, ti, emb,
        graph_store=graph_store,
        rrf_scorer=RRFScorer(k=settings.rrf_k),
        candidate_multiplier=settings.retrieval_candidate_multiplier,
        graph_context_score=settings.graph_context_score,
        graph_context_depth=settings.graph_context_depth,
        collection=collection,
        max_per_doc=settings.retrieval_max_per_doc,
        demote_boilerplate=settings.retrieval_demote_boilerplate,
        boilerplate_config=config_from_json(settings.boilerplate_config_json),
    )


async def build_collection_entry(
    http: httpx.AsyncClient, *, graph_store: Any, spec: CollectionSpec, embedder: Any = None,
) -> CollectionEntry:
    """Build one ready-to-serve, non-default ``CollectionEntry`` from a spec:
    its embedder (unless a shared one is passed), Qdrant store, ES index (both
    best-effort ensured), and hybrid retriever. Used by the startup loop (with a
    shared embedder from the cache) and by ``POST /v1/collections`` (fresh)."""
    from ragstack.stores.qdrant import QdrantVectorStore

    emb = embedder if embedder is not None else _embedder_for_spec(http, spec)
    vs = QdrantVectorStore(
        url=_qdrant_url_for(spec.collection),
        collection=spec.collection,
        vector_size=spec.embedding_model_dim,
        api_key=settings.qdrant_api_key or None,
    )
    ti = _build_text_index_for(spec.es_index())
    # Best-effort readiness — a collection that isn't reachable yet shouldn't abort
    # startup or the create call (the default collection already gated real outages).
    for store, op in ((vs, "ensure_collection"), (ti, "ensure_index")):
        fn = getattr(store, op, None)
        if fn is not None:
            try:
                await fn()
            except Exception as e:  # noqa: BLE001 — non-fatal for a registry entry
                log.warning("collection %r: %s failed: %s", spec.id, op, e)
    return CollectionEntry(
        id=spec.id,
        label=spec.label or spec.id,
        collection=spec.collection,
        model=spec.embedding_model,
        dim=spec.embedding_model_dim,
        chunk_method=spec.chunk_method,
        chunk_size=spec.chunk_size,
        chunk_overlap=spec.chunk_overlap,
        chunk_params=spec.chunk_params,
        is_shared_surface=False,
        retriever=_hybrid_retriever(
            vs, ti, emb, graph_store=graph_store, collection=spec.collection
        ),
        vector_store=vs,
        text_index=ti,
        text_index_name=spec.es_index(),
        embedder=emb,
        embedding_api=spec.embedding_api,
        embedding_endpoints=list(
            spec.embedding_endpoints
            or ([spec.embedding_sidecar_url] if spec.embedding_sidecar_url else [])
        ),
        owner=spec.owner,
    )


def _semantic_param(params: dict[str, Any], key: str, default: Any, cast: Any) -> Any:
    """Read one semantic tunable out of a collection's ``chunk_params``.

    ``chunk_params`` is free-form in the contract, so a value can be anything JSON
    can hold. A bad one is raised as ``ValueError`` — which the ingest router turns
    into a 400 carrying this message — rather than a ``TypeError`` surfacing as a
    500 mid-ingest.
    """
    if key not in params or params[key] is None:
        return default
    try:
        return cast(params[key])
    except (TypeError, ValueError):
        raise ValueError(f"chunk param {key!r} must be a number; got {params[key]!r}") from None


def _chunker_for(entry: CollectionEntry, *, embed_fn: Any = None) -> Any:
    """Build a chunker from a *target collection's* own chunk config, falling back
    to the server defaults for any unset field. ``fixed_token`` binds the HF
    tokenizer of the collection's embedding model (its sliding window is sized in
    that model's tokens).

    The semantic methods embed sentence buffers synchronously, so they need an
    ``embed_fn``; ``build_ingestor_for`` supplies one bound to *this collection's*
    embedding backend (see :func:`_embed_bridge_for`). Called without one for a
    semantic collection this raises, which the ingest router surfaces as a 400 —
    never a silent fall back to a different chunking.

    A collection's ``chunk_params`` (the free-form ``params`` object on
    ``POST /v1/collections``) are honoured here, so a library created with e.g.
    ``buffer_size=5`` is ingested with 5 rather than the server default.
    """
    method = entry.chunk_method or settings.chunk_method
    size = entry.chunk_size if entry.chunk_size is not None else settings.chunk_size
    overlap = entry.chunk_overlap if entry.chunk_overlap is not None else settings.chunk_overlap
    params = dict(entry.chunk_params or {})
    if method in ("semantic", "semantic_pooled") and embed_fn is None:
        raise ValueError(
            f"chunk_method={method!r} needs an embedding backend for this collection, "
            "but none could be built"
        )
    token_counter = None
    if method == "fixed_token":
        model = entry.model or settings.embedding_model
        if not model:
            raise ValueError(
                "chunk_method='fixed_token' requires the collection's embedding_model"
            )
        token_counter = make_token_counter("hf", model=model, api_key=settings.openai_api_key or None)
    return make_chunker(
        method,
        chunk_size=size,
        chunk_overlap=overlap,
        token_counter=token_counter,
        embed_fn=embed_fn,
        buffer_size=_semantic_param(params, "buffer_size", settings.chunk_buffer_size, int),
        breakpoint_percentile_threshold=_semantic_param(
            params, "breakpoint_percentile_threshold", settings.chunk_breakpoint_percentile, float
        ),
        min_chunk_length=_semantic_param(
            params, "min_chunk_length", settings.chunk_min_length, int
        ),
    )


def _embed_bridge_for(app_state: Any, entry: CollectionEntry) -> SyncEmbedBridge:
    """The sync embed bridge a *semantic* per-collection ingest needs, cached per
    collection id on ``app_state``.

    Each bridge owns a background event loop + httpx client, so one is built per
    collection and reused — a fresh bridge per ``/v1/ingest`` call would leak a
    thread per request. Its factory rebuilds THIS collection's embedding backend on
    the bridge's own loop: an httpx client binds to the loop it is first used on,
    so the entry's main-loop embedder cannot be reused, and using the server
    default instead would detect boundaries with a different model than the one
    storing the vectors. ``lifespan`` closes every cached bridge at shutdown.
    """
    bridges = getattr(app_state, "collection_embed_bridges", None)
    if bridges is None:
        bridges = {}
        # app_state is duck-typed in tests; a namespace that refuses attributes
        # just means the bridge isn't cached, not that the ingest fails.
        try:
            app_state.collection_embed_bridges = bridges
        except (AttributeError, TypeError):  # pragma: no cover - defensive
            pass
    bridge = bridges.get(entry.id)
    if bridge is None:
        api = entry.embedding_api or settings.embedding_api
        model = entry.model or settings.embedding_model
        # Only fall back to the server-wide endpoints when the entry records none
        # (a spec with neither `embedding_endpoints` nor `embedding_sidecar_url`);
        # the collection's own endpoints always win.
        urls = list(entry.embedding_endpoints) or embedding_urls()
        bridge = SyncEmbedBridge(lambda http: _make_embedder(http, api=api, model=model, urls=urls))
        bridges[entry.id] = bridge
    return bridge


def _embed_fn_for(app_state: Any, entry: CollectionEntry) -> Any:
    """The sync ``embed_fn`` a collection's chunker needs, or ``None``. Only the
    semantic methods embed during chunking, so nothing else pays for a bridge."""
    method = entry.chunk_method or settings.chunk_method
    if method not in ("semantic", "semantic_pooled"):
        return None
    return _embed_bridge_for(app_state, entry)


def build_ingestor_for(app_state: Any, entry: CollectionEntry) -> ShardedIngestor:
    """A ShardedIngestor that writes into ``entry``'s collection using that
    collection's bound embedder/chunker/stores (so vectors match its model and
    land in its index), while sharing the app's loader, graph store, KG extractor,
    job store, and ingest backend. Used when ``/v1/ingest`` targets a non-default
    ``collection``; the default collection keeps using the prebuilt app ingestor.

    The graph store is shared, so the pipeline is told which collection it writes
    into: triples get stamped with it, and delete-prior is scoped by it so a
    re-ingest here can't drop another collection's triples for the same doc_id
    (#209)."""
    pipeline = IngestionPipeline(
        loader=default_loader_registry(
            ingest_root=settings.ingest_root or None,
            max_bytes=settings.max_document_bytes,
            profile=resolve_profile(settings.publisher_profile),
        ),
        # The target collection's OWN chunker (method/size/overlap/params from its
        # build spec), not the server default. Semantic methods additionally get a
        # per-collection sync embed bridge so boundary detection uses the same
        # model that stores the vectors.
        chunker=_chunker_for(entry, embed_fn=_embed_fn_for(app_state, entry)),
        embedder=entry.embedder,
        vector_store=entry.vector_store,
        text_index=entry.text_index,
        graph_store=app_state.graph_store,
        kg_extractor=app_state.kg_extractor,
        collection=entry.collection,
        # Share the app's enricher (and therefore its cache and its concurrency
        # budget) so per-collection ingests can't multiply the load on Crossref.
        # getattr: app_state is duck-typed in tests, and a missing enricher must
        # mean "disabled", never an AttributeError mid-ingest.
        doi_enricher=getattr(app_state, "doi_enricher", None),
        boilerplate_filter=_build_boilerplate_filter(),
    )
    return ShardedIngestor(
        pipeline,
        make_ingest_backend(settings, http=app_state.http_client),
        shard_size=settings.ingest_shard_size,
        job_store=app_state.job_store,
        quota=TenantQuota(settings.tenant_max_concurrency),
    )


def write_ingest_manifest_for(
    entry: CollectionEntry, *, source: str, chunk_count: int | None = None
) -> None:
    """Verified (source='ingest') manifest for a *specific* collection after a
    targeted ingest — the per-collection analogue of ``write_ingest_manifest``
    (which targets the default derived collection). Best-effort, never raises.

    Records the entry's OWN ``chunk_params`` and embedding backend, not the server
    defaults. Dropping the params here used to re-hash the very same build as a
    different ``spec_hash`` the moment a semantic collection was ingested into —
    an ingest manifest that disagreed with the config manifest of the identical
    spec, i.e. fake drift, which is exactly what the ingest guard reads."""
    if not settings.collection_manifest_dir:
        return
    try:
        from ragstack.provenance import make_ingest_manifest, write_manifest

        manifest = make_ingest_manifest(
            collection=entry.collection,
            model=entry.model, dim=entry.dim,
            embedding_api=entry.embedding_api or settings.embedding_api,
            embedding_endpoints=list(entry.embedding_endpoints),
            chunk_method=entry.chunk_method, chunk_size=entry.chunk_size,
            chunk_overlap=entry.chunk_overlap, chunk_params=entry.chunk_params,
            corpus=source, chunk_count=chunk_count,
        )
        write_manifest(settings.collection_manifest_dir, manifest)
    except Exception:  # noqa: BLE001 — provenance must never fail an ingest
        log.warning("provenance: targeted ingest manifest write failed", exc_info=True)


class BuildSpecMismatch(Exception):
    """An ingest would write into a collection built with a different spec.

    Carries the per-field differences so the router can name what differs instead
    of quoting two opaque hashes at the operator."""

    def __init__(self, cid: str, collection: str, differences: list[str],
                 recorded_hash: str, requested_hash: str) -> None:
        self.cid = cid
        self.collection = collection
        self.differences = differences
        self.recorded_hash = recorded_hash
        self.requested_hash = requested_hash
        super().__init__(
            f"ingest refused: collection {cid!r} (index {collection!r}) was built with "
            + "; ".join(differences)
            + f". Recorded spec_hash {recorded_hash or '?'} != this ingest's "
            f"{requested_hash}. A different build spec is a different corpus — create a "
            f"new collection instead of mixing specs (set COLLECTION_SPEC_GUARD=false to "
            f"override)."
        )


def _differs(field: str, recorded: Any, requested: Any) -> str | None:
    """One field's difference, or ``None`` when there is no *provable* conflict.

    A missing value on either side (empty string, ``None``, empty params) means
    "not stated", not "differs": manifests predate several of these fields, and a
    corpus whose provenance never recorded a chunker must not become un-ingestable
    the moment this guard ships. Only a concrete-vs-concrete disagreement — the
    kind that actually produces mismatched vectors or chunk boundaries — counts."""
    if recorded in (None, "", {}) or requested in (None, "", {}):
        return None
    if recorded == requested:
        return None
    return f"{field}={recorded!r} but this ingest would use {field}={requested!r}"


def check_ingest_build_spec(entry: CollectionEntry) -> None:
    """Refuse an ingest into a collection whose recorded build spec differs.

    The collection's provenance manifest is the durable record of how the corpus
    was built; ``entry`` is what this ingest is about to build with (its embedder
    and ``_chunker_for``'s chunker come from exactly these fields). If they
    disagree the write would mix embedding spaces and/or chunk boundaries inside
    one index — retrievable, plausible-looking, and wrong — so raise instead.

    Skipped when the guard is off, when manifests are disabled, or when the
    collection has no manifest yet (nothing to contradict)."""
    if not settings.collection_spec_guard or not settings.collection_manifest_dir:
        return
    from ragstack.provenance import chunk_descriptor, read_manifest, spec_hash

    recorded = read_manifest(settings.collection_manifest_dir, entry.collection)
    if recorded is None:
        return
    diffs = [
        d for d in (
            _differs("embedding_model", recorded.model, entry.model),
            _differs("embedding_dim", recorded.dim, entry.dim),
            _differs("chunk_method", recorded.chunk_method, entry.chunk_method),
            _differs("chunk_size", recorded.chunk_size, entry.chunk_size),
            _differs("chunk_overlap", recorded.chunk_overlap, entry.chunk_overlap),
            _differs("chunk_params", recorded.chunk_params, entry.chunk_params),
        ) if d is not None
    ]
    if not diffs:
        return
    requested = spec_hash(
        entry.model or "", entry.dim,
        chunk_descriptor(
            entry.chunk_method, entry.chunk_size, entry.chunk_overlap,
            entry.chunk_params or None,
        ),
    )
    raise BuildSpecMismatch(entry.id, entry.collection, diffs, recorded.spec_hash, requested)


def materialize_config_manifest_for_spec(spec: CollectionSpec) -> None:
    """Write a source='config' manifest for a spec's collection (public wrapper
    over ``_materialize_config_manifest`` for the create path)."""
    _materialize_config_manifest(
        spec.collection, model=spec.embedding_model, dim=spec.embedding_model_dim,
        api=spec.embedding_api,
        endpoints=spec.embedding_endpoints
        or ([spec.embedding_sidecar_url] if spec.embedding_sidecar_url else []),
        chunk_method=spec.chunk_method, chunk_size=spec.chunk_size,
        chunk_overlap=spec.chunk_overlap, chunk_params=spec.chunk_params,
    )


async def _build_collection_registry(
    http: httpx.AsyncClient,
    *,
    graph_store: Any,
    default_embedder: Any,
    default_vector_store: Any,
    default_text_index: Any,
    default_retriever: Any,
    default_collection: str,
    store: CollectionStore | None = None,
) -> CollectionRegistry:
    """Build the collection registry. The top-level pinned/derived collection is
    the ``default`` entry (reusing the already-built objects); each spec from the
    durable collection store adds a self-contained entry with its own Qdrant
    collection + ES index + embedder (shared by signature). No specs → a one-entry
    registry equal to the default, so single-collection mode is unchanged.

    ``store`` is the authoritative registry (``collection_store_backend``); it
    defaults to the JSON-file backend, which reads exactly the
    ``collections_file``/``collections_json`` this used to read directly."""
    derived = CollectionEntry(
        id="default",
        label=f"default · {default_collection}",
        collection=default_collection,
        model=settings.embedding_model,
        dim=settings.embedding_model_dim,
        chunk_method=settings.chunk_method,
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        chunk_params={},
        is_shared_surface=True,
        retriever=default_retriever,
        vector_store=default_vector_store,
        text_index=default_text_index,
        text_index_name=_es_index_name(),
        embedder=default_embedder,
        embedding_api=settings.embedding_api,
        embedding_endpoints=list(embedding_urls()),
    )

    # Specs FIRST, so we can tell whether one already claims the physical store
    # the settings-derived entry would serve.
    specs = await (store or JsonFileCollectionStore(settings)).list_specs()
    entries: list[CollectionEntry] = []

    # Reuse the default embedder for specs that share its backend signature.
    emb_cache: dict[tuple, Any] = {_default_emb_signature(): default_embedder}

    seen_physical: dict[str, str] = {}
    for spec in specs:
        if spec.id == RESERVED_COLLECTION_ID:
            raise RuntimeError(
                f"collection registry: a configured spec uses the reserved id "
                f"{RESERVED_COLLECTION_ID!r}. That id names the POINTER — which "
                "collection a request resolves to when it omits 'collection' — "
                "and a spec claiming it silently shadows the server default."
            )
        # ADR-0002, stated positively: a physical index has exactly one registry
        # entry. Two ids over one store are two independent ACLs over one dataset
        # (#275) — true whether the second id comes from the synthesised default
        # or, as here, from another spec.
        for leg in (spec.collection, spec.es_index()):
            prior = seen_physical.get(leg)
            if prior is not None and prior != spec.id:
                raise RuntimeError(
                    f"collection registry: {spec.id!r} and {prior!r} both serve "
                    f"{leg!r}. Two registry ids over one physical store are two "
                    "independent ACLs over one dataset: revoking a grant on one "
                    "leaves the same bytes readable through the other."
                )
            seen_physical[leg] = spec.id
        sig = spec.emb_signature()
        emb = emb_cache.get(sig)
        if emb is None:
            emb = _embedder_for_spec(http, spec)
            emb_cache[sig] = emb
        entries.append(
            await build_collection_entry(http, graph_store=graph_store, spec=spec, embedder=emb)
        )
        # Materialized BEFORE the derived entry's manifest below, deliberately:
        # manifests are keyed by PHYSICAL collection, and
        # `_materialize_config_manifest` early-returns when one already exists.
        # Writing the settings-derived spec first meant it always won, so a named
        # spec's real build spec was never recorded and the ADR-0002 409 guard was
        # armed with a value describing a different entry (#273).
        materialize_config_manifest_for_spec(spec)

    # THE #275 FIX. Access control is asserted at the collection (ADR-0003), so
    # two registry ids over one physical store are two INDEPENDENT ACLs over one
    # dataset: revoking a grant on one leaves the same bytes readable through the
    # other. An owner who un-publishes a corpus has not un-published it.
    #
    # `POST /v1/collections` already refuses this for created collections (the
    # content-addressed alias guard); the config/implicit path needs the same
    # rule. Compared on BOTH legs — a shared ES index aliases just as effectively
    # as a shared Qdrant collection.
    vec_claim = next((e for e in entries if e.collection == derived.collection), None)
    es_claim = next((e for e in entries if e.es_index() == derived.es_index()), None)
    if (vec_claim is None) != (es_claim is None) or (
        vec_claim is not None and es_claim is not None and vec_claim.id != es_claim.id
    ):
        # A spec claims ONE of the derived entry's two legs. Suppressing the
        # derived entry would close the ACL hole on the claimed leg while
        # stranding the other: app.state's vector_store/ingestor keep serving a
        # physical store that no registry entry covers, so nothing authorizes
        # access to it. Serving it under two ids is the bug we are fixing; serving
        # it under NO id is worse. This is a misconfiguration, not something to
        # resolve silently.
        raise RuntimeError(
            "collection registry: a configured collection claims only one leg of "
            f"the server default (vectors={derived.collection!r} claimed by "
            f"{vec_claim.id if vec_claim else None!r}; text index="
            f"{derived.es_index()!r} claimed by {es_claim.id if es_claim else None!r}). "
            "Give the collection BOTH of the default's stores or neither — a "
            "half-shared default leaves one store served by no registry entry, "
            "and therefore governed by no ACL."
        )
    claimed_by = vec_claim
    if claimed_by is None:
        entries.insert(0, derived)
        _materialize_config_manifest(
            default_collection, model=settings.embedding_model, dim=settings.embedding_model_dim,
            api=settings.embedding_api, endpoints=settings.embedding_endpoints,
            chunk_method=settings.chunk_method, chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
        )
    else:
        log.info(
            "collection registry: not synthesising a 'default' entry for %r — "
            "it is already served as %r. Two ids over one store would be two "
            "ACLs over one dataset (#275); requests that omit 'collection' "
            "resolve to %r.",
            derived.collection,
            claimed_by.id,
            claimed_by.id,
        )

    default_id = _resolve_default_id(entries, fallback=(claimed_by or derived).id)
    log.info("collection registry: %d collections (%s), default=%r", len(entries),
             ", ".join(e.id for e in entries), default_id)
    return CollectionRegistry(entries, default_id=default_id)


def _resolve_default_id(entries: list[CollectionEntry], *, fallback: str) -> str:
    """Which id a request that omits ``collection`` resolves to.

    ``DEFAULT_COLLECTION_ID`` wins when set. An unresolvable value is FATAL
    rather than a silent fallback: it is an operator typo, and quietly serving a
    different corpus than the one configured is precisely the class of bug this
    change exists to remove."""
    configured = (settings.default_collection_id or "").strip()
    if not configured:
        return fallback
    if not any(e.id == configured for e in entries):
        raise RuntimeError(
            f"DEFAULT_COLLECTION_ID={configured!r} names no registered collection "
            f"(have: {sorted(e.id for e in entries)}). It is the collection every "
            "request that omits 'collection' resolves to, so a typo here would "
            "serve the wrong corpus or 404 every such request."
        )
    return configured


def _es_index_name() -> str:
    """The Elasticsearch (BM25) index the API serves.

    When a pre-built collection is pinned via ``qdrant_collection_explicit``, the
    BM25 leg must read the SAME corpus or hybrid retrieval silently fuses two
    different indices (the vector leg on the pinned collection, BM25 on the default
    ``ragstack``). So default the ES index to the explicit collection name, unless
    the operator set ``elasticsearch_index`` to a non-default value of its own.
    """
    es_index = settings.elasticsearch_index
    default_es = type(settings).model_fields["elasticsearch_index"].default
    if settings.qdrant_collection_explicit and es_index == default_es:
        return settings.qdrant_collection_explicit
    return es_index


def _build_text_index_for(index: str):
    """The text index bound to a specific ES index name — the shared core of the
    default builder and each registry collection's BM25 leg."""
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
            index,
            settings.elasticsearch_api_key or None,
        )

    if settings.require_durable_backends:
        log.warning(
            "text index is in-memory (text_backend=memory); set "
            "text_backend=elasticsearch for durable BM25 + hybrid retrieval"
        )
    return InMemoryTextIndex()


def _build_text_index():
    """The default text index (top-level ES index name)."""
    return _build_text_index_for(_es_index_name())


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


def _build_doi_enricher(http: httpx.AsyncClient) -> DoiEnricher | None:
    """The Crossref/DataCite metadata enricher, or ``None`` when disabled.

    Opt-in via ``doi_enrichment_enabled`` (default off) because it is the only
    part of ingest that reaches the public internet; disabled, ingest keeps the
    exact behaviour it had before enrichment existed, which is what
    offline/air-gapped deployments need.

    Shares the app's HTTP client (deps owns its lifecycle) and the app's
    publisher profile, so DOI discovery here uses the same filename->DOI rule as
    the local ``enrich`` leg. Warns — but still builds — without a contact
    address, since Crossref's polite pool is a genuine operational nicety we
    shouldn't silently skip.
    """
    if not settings.doi_enrichment_enabled:
        return None
    if not settings.doi_enrichment_mailto:
        log.warning(
            "doi enrichment enabled without DOI_ENRICHMENT_MAILTO: requests will "
            "use Crossref's anonymous pool. Set a contact address."
        )
    log.info(
        "doi enrichment enabled (concurrency=%d, timeout=%.1fs, cache=%s)",
        settings.doi_enrichment_concurrency,
        settings.doi_enrichment_timeout,
        settings.doi_enrichment_cache_dir or "memory-only",
    )
    resolver = build_resolver(
        http,
        mailto=settings.doi_enrichment_mailto,
        user_agent=settings.doi_enrichment_user_agent,
        cache_dir=settings.doi_enrichment_cache_dir,
        timeout=settings.doi_enrichment_timeout,
        concurrency=settings.doi_enrichment_concurrency,
        datacite_fallback=settings.doi_enrichment_datacite_fallback,
    )
    return DoiEnricher(resolver, profile=resolve_profile(settings.publisher_profile))


def _build_boilerplate_filter() -> BoilerplateFilter | None:
    """The chunk-level boilerplate filter, or ``None`` when detection is off.

    Detection defaults ON because it only *adds* ``metadata["section"]`` /
    ``metadata["is_boilerplate"]`` to non-body chunks — nothing is removed, so it
    cannot lose data, and it makes the licence/reference/acknowledgement mass in
    a scholarly corpus both measurable and filterable. Dropping those chunks
    (``boilerplate_drop``) is the opt-in half, because a false positive there is
    permanent for that ingest.
    """
    if not settings.boilerplate_detection_enabled:
        return None
    config = config_from_json(settings.boilerplate_config_json)
    log.info(
        "boilerplate detection enabled (action=%s, sections=%s)",
        "drop" if settings.boilerplate_drop else "flag",
        ",".join(sorted(config.boilerplate_sections)),
    )
    return BoilerplateFilter(config, drop=settings.boilerplate_drop)


def _build_rewriters(llm: OpenAILLM | None) -> dict[str, QueryRewriter]:
    """Query-rewriter registry keyed by strategy name. Passthrough is always
    available; the LLM-backed strategies only when an LLM is configured."""
    rewriters: dict[str, QueryRewriter] = {"passthrough": PassthroughRewriter()}
    if llm is not None:
        rewriters["multiquery"] = MultiQueryRewriter(llm, n=settings.multiquery_n)
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


def _llm_from_entry(entry: Any, http: httpx.AsyncClient) -> OpenAILLM:
    return OpenAILLM(
        base_url=entry.base_urls[0],
        model=entry.model,
        http=http,
        api_key=settings.openai_api_key or None,
        # A registered model's free-form params become the chat request's extra_body
        # (e.g. a reasoning model's chat_template_kwargs to answer into `content`).
        extra_body=dict(entry.params or {}),
    )


def _reranker_from_entry(entry: Any, http: httpx.AsyncClient) -> SidecarReranker:
    return SidecarReranker(base_url=entry.base_urls[0], http=http)


def apply_assignment(app: Any, task: str, entry: ModelEntry | None) -> None:
    """Rebuild and atomically swap the ``app.state`` singleton for a hot-swappable
    task (llm / reranker). ``entry`` is a ``ModelEntry`` to apply, or ``None`` to
    revert the task to its settings-configured default (which may itself be None →
    the task disabled). Attribute assignment is atomic in CPython; in-flight
    requests already captured the prior object via Depends, so the swap needs no
    lock.

    Phase 1 uses only ``base_urls[0]``: the hot-swappable clients (OpenAILLM,
    SidecarReranker) are single-endpoint. Multi-endpoint fan-out/failover for a
    task belongs in the Go embedding-router sidecar (ADR-0001), not a hand-rolled
    pool here — so extra ``base_urls`` are ignored for now and we warn rather than
    pretend to use them."""
    if entry is not None and len(entry.base_urls) > 1:
        log.warning(
            "model %r registers %d base_urls but the %s hot-swap uses only the first (%s); "
            "multi-endpoint fan-out is deferred to the Go router (ADR-0001)",
            entry.id,
            len(entry.base_urls),
            task,
            entry.base_urls[0],
        )
    http = app.state.http_client
    if task == "llm":
        llm = _llm_from_entry(entry, http) if entry is not None else _build_llm(http)
        # The LLM feeds both answer generation and the LLM-backed rewriters, so a
        # swap rebuilds both from the same client.
        app.state.generator = (
            RagGenerator(llm, max_context_chars=settings.llm_max_context_chars)
            if llm is not None
            else None
        )
        app.state.rewriters = _build_rewriters(llm)
    elif task == "reranker":
        app.state.reranker = (
            _build_reranker(http) if entry is None else _reranker_from_entry(entry, http)
        )
    else:  # pragma: no cover - guarded by the registry (HOT_SWAPPABLE) upstream
        raise ValueError(f"task {task!r} is not hot-swappable")


def build_generator_for(
    registry: ModelRegistry, http: httpx.AsyncClient, model_id: str
) -> RagGenerator:
    """A one-off generator for a per-request ``llm`` override — resolve the model
    ref from the registry and build an ephemeral RagGenerator (construction is
    cheap: it just wraps the shared http client). Does NOT touch app.state or the
    rewriters, so the override affects only this request's answer generation.
    Raises ``RegistryError`` (unknown id → 404, non-llm model → 400) — the registry
    owns that taxonomy, so the router maps it via its ``status_code``."""
    entry = registry.resolve_assignment("llm", model_id)
    assert entry is not None  # non-None model_id → resolve returns the entry or raises
    return RagGenerator(_llm_from_entry(entry, http), max_context_chars=settings.llm_max_context_chars)


def build_reranker_for(
    registry: ModelRegistry, http: httpx.AsyncClient, model_id: str
) -> SidecarReranker:
    """A one-off reranker for a per-request ``reranker`` override. Raises
    ``RegistryError`` (unknown id → 404, non-reranker model → 400)."""
    entry = registry.resolve_assignment("reranker", model_id)
    assert entry is not None  # non-None model_id → resolve returns the entry or raises
    return _reranker_from_entry(entry, http)


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
    # Validate against the canonical set, not a hand-copied literal: an out-of-date
    # literal here silently dropped `fixed_token` (and `semantic_pooled`) to `fixed`,
    # turning a token-window request into char-budget chunking before the
    # method-specific handling below could run.
    if method not in CHUNK_METHODS:
        log.warning("unknown chunk_method %r — falling back to 'fixed'", method)
        method = "fixed"
    bridge: SyncEmbedBridge | None = None
    embed_fn = None
    # BOTH semantic variants embed while chunking — ``semantic_pooled`` embeds each
    # sentence once and mean-pools, but it still needs the bridge. Testing only for
    # 'semantic' here meant CHUNK_METHOD=semantic_pooled died at startup with
    # make_chunker's "requires an embed_fn".
    if method in ("semantic", "semantic_pooled"):
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


def _validate_ingest_root() -> None:
    """Check the *shape* of ingest_root — and announce it when it is unset.

    An empty ingest_root is not fatal here: ``POST /v1/ingest`` fails closed with
    503 at request time (see ``api/routers/documents.py``), so a deployment that
    never ingests keeps serving. It is logged at WARNING so an operator sees the
    disabled capability at boot instead of discovering it from a 503.

    A *set* root, though, must actually confine something. ``INGEST_ROOT=/``
    passes any non-emptiness test while being identical in effect to leaving it
    unset (every path resolves inside "/"), so it is refused outright — nobody
    should be able to "fix" a complaint about ingest_root by pointing it at the
    filesystem root. A root that does not exist, or is not a directory, is also
    refused: every ingest would fail against it, so it is a misconfiguration
    worth catching at boot rather than per request.
    """
    raw = settings.ingest_root.strip()
    if not raw:
        log.warning(
            "ingest_root is unset: POST /v1/ingest is DISABLED (503). Without a "
            "root, request.source would be an arbitrary server-side file read "
            "whose text is readable back through /v1/retrieve. Set INGEST_ROOT "
            "to the directory holding ingestable documents to enable ingest."
        )
        return
    resolved = Path(raw).resolve()
    if resolved == Path(resolved.anchor):
        raise RuntimeError(
            f"ingest_root={settings.ingest_root!r} resolves to the filesystem root "
            f"({resolved}), which confines nothing: POST /v1/ingest would still be "
            "an arbitrary server-side file read. Point INGEST_ROOT at the directory "
            "holding ingestable documents, or leave it unset to disable ingest "
            "entirely (the endpoint then returns 503)."
        )
    if not resolved.is_dir():
        raise RuntimeError(
            f"ingest_root={settings.ingest_root!r} is not an existing directory "
            f"(resolved to {resolved}); every ingest would fail against it. Create "
            "it, or leave INGEST_ROOT unset to disable ingest entirely — do not set "
            "it to '/', which disables the confinement instead of the endpoint."
        )


def _validate_production_settings() -> None:
    """Refuse to start in production without the security-critical settings.

    Without auth, the data API is open; without an ingest_root, request.source
    would be an unconfined arbitrary-file read — which is why ``POST /v1/ingest``
    is gated at request time (503) on every configuration, keyed or keyless. Here
    we only validate the *shape* of a configured root, and additionally require
    one to be present when durability (the production marker) is required.
    """
    _validate_ingest_root()
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
    # The ACL database (users + shares) must survive a restart in production —
    # an in-memory store loses every owner row and share on the next boot, which
    # would silently re-open (via backfill) or lock out (no owners) every
    # collection. The user_store_* triple governs both tables (issue #243).
    if (settings.user_store_backend or "memory").lower() == "memory":
        raise RuntimeError(
            "require_durable_backends is set but user_store_backend='memory'; the "
            "ACL database (users + shares) must be durable — set USER_STORE_BACKEND "
            "to 'sqlite' or 'postgres'."
        )
    # A RELATIVE sqlite path resolves against the process working directory, so two
    # servers launched from the same checkout silently SHARE a registry / ACL / job
    # database no matter what their env says — observed live: a demo server's
    # collection registry landed in the repo tree and seeded another tenant's ACL
    # database with a foreign collection. In production the path must be absolute.
    #
    # Only checked for stores actually selected as sqlite; postgres carries its
    # location in the DSN, and memory is already refused above.
    _relative = [
        (name, path)
        for name, backend, path in (
            ("COLLECTION_STORE_PATH", settings.collection_store_backend,
             settings.collection_store_path),
            ("USER_STORE_PATH", settings.user_store_backend, settings.user_store_path),
            ("JOB_STORE_PATH", settings.job_store_backend, settings.job_store_path),
        )
        if (backend or "").lower() == "sqlite" and not os.path.isabs(path or "")
    ]
    if _relative:
        raise RuntimeError(
            "require_durable_backends is set but these sqlite store paths are "
            "RELATIVE, so they resolve against the working directory and two "
            "servers started from one checkout would share them: "
            + ", ".join(f"{n}={v!r}" for n, v in _relative)
            + ". Set an absolute path per tenant (see ADR-0005)."
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
    # Same hazard OUTSIDE production, where the guard above does not run: the
    # documented local recipe starts uvicorn from the repo checkout, so a relative
    # sqlite path lands in the source tree and a second server started the same way
    # silently shares it. Only ONE side of a collision has to be unguarded, which is
    # how the observed cross-tenant seeding happened. Warn rather than refuse — dev
    # convenience is the point of the relative default.
    if not settings.require_durable_backends:
        for _name, _backend, _path in (
            ("COLLECTION_STORE_PATH", settings.collection_store_backend,
             settings.collection_store_path),
            ("USER_STORE_PATH", settings.user_store_backend, settings.user_store_path),
            ("JOB_STORE_PATH", settings.job_store_backend, settings.job_store_path),
        ):
            if (_backend or "").lower() == "sqlite" and not os.path.isabs(_path or ""):
                log.warning(
                    "%s=%r is RELATIVE — it resolves against the working directory "
                    "(%s), so another server started from here shares this database. "
                    "Set an absolute path.",
                    _name, _path, os.getcwd(),
                )
    # Fail fast on a misconfigured RBAC setup (unknown default_role / api_key_roles)
    # rather than silently 403-ing affected callers at runtime.
    from ragstack.api.security import (
        validate_admin_role_cache_settings,
        validate_admin_subjects_settings,
        validate_role_settings,
        validate_service_account_settings,
    )

    validate_role_settings()
    # The bearer admin allowlist lives in the same vocabulary and the same
    # namespace rules, and it is the break-glass path: an entry that can never
    # match (colon-free, padded, reserved) is an operator who believes they have
    # an admin and does not. Must fail before any request is served. Its cache
    # TTL is the DEMOTION lag, so it is capped just like the disabled check's.
    validate_admin_subjects_settings()
    validate_admin_role_cache_settings()
    # And the one runtime revocation lever there is (#258): its cache TTL is the
    # revocation lag, so it is capped at startup exactly like the identity
    # cache's — an uncapped value silently keeps a leaked key working.
    validate_service_account_settings()
    # Same idea for the identity layer: an unknown provider name, an empty
    # BV-BRC issuer allowlist or an OIDC client with no pinned `aud` are all
    # silent-and-permanent failures (the last one being a silent *acceptance*).
    from ragstack.identity import validate_identity_settings

    validate_identity_settings()
    # And the user-profile store (ADR-0004): a typo'd backend would silently
    # record users in memory and lose them on restart.
    from ragstack.user_store import validate_user_store_settings

    validate_user_store_settings()
    http_client = httpx.AsyncClient(timeout=120.0)
    embedder = _build_embedder(http_client)
    vector_store = _build_vector_store()
    text_index = _build_text_index()
    graph_store = _build_graph_store()
    # Resolved up front (not just where the registry is built) because the default
    # pipeline and retriever below need it to scope the shared graph store: the
    # "default" collection is a collection like any other on the graph axis (#209).
    default_collection = _derived_collection_name()

    # Qdrant: make sure the collection exists at startup so the first request doesn't race.
    if hasattr(vector_store, "ensure_collection"):
        try:
            await vector_store.ensure_collection()
            log.info(
                "qdrant collection ready: %s (vector_size=%d)",
                getattr(
                    vector_store,
                    "_collection",
                    settings.qdrant_collection_explicit or settings.qdrant_collection,
                ),
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
            log.info("elasticsearch index ready: %s", _es_index_name())
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
    doi_enricher = _build_doi_enricher(http_client)
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
        collection=default_collection,
        doi_enricher=doi_enricher,
        boilerplate_filter=_build_boilerplate_filter(),
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
    # Select the distribution backend by config: in-process (local) or GoWe.
    # The GoWe client shares the app's http_client (deps owns its lifecycle).
    ingest_backend = make_ingest_backend(settings, http=http_client)
    log.info("ingest backend: %s", type(ingest_backend).__name__)
    ingestor = ShardedIngestor(
        pipeline,
        ingest_backend,
        shard_size=settings.ingest_shard_size,
        job_store=job_store,
        quota=tenant_quota,
    )

    # Hybrid retrieval: fuse dense (vector) + BM25 (text) + optional graph context
    # via RRF. With the in-memory text index the BM25 leg still works (Jaccard),
    # but it's the Elasticsearch backend that makes it real; the graph leg is
    # active only when a graph store is configured.
    retriever = _hybrid_retriever(
        vector_store, text_index, embedder,
        graph_store=graph_store, collection=default_collection,
    )

    app.state.http_client = http_client
    app.state.embedder = embedder
    app.state.vector_store = vector_store
    app.state.text_index = text_index
    app.state.graph_store = graph_store
    app.state.kg_extractor = kg_extractor
    # Hung off app.state so build_ingestor_for can share the one enricher (and
    # its cache + concurrency budget) across per-collection ingests.
    app.state.doi_enricher = doi_enricher
    app.state.pipeline = pipeline
    app.state.embed_bridge = embed_bridge
    # Per-collection sync embed bridges for semantic chunking on a targeted
    # ingest, built lazily by _embed_bridge_for and closed below at shutdown.
    collection_embed_bridges: dict[str, SyncEmbedBridge] = {}
    app.state.collection_embed_bridges = collection_embed_bridges
    app.state.job_store = job_store
    app.state.ingestor = ingestor
    app.state.generator = (
        RagGenerator(llm, max_context_chars=settings.llm_max_context_chars)
        if llm is not None
        else None
    )
    app.state.tenant_quota = tenant_quota
    app.state.retriever = retriever
    app.state.rewriters = _build_rewriters(llm)
    app.state.reranker = _build_reranker(http_client)

    # The durable collection registry. Defaults to the JSON-file backend, i.e.
    # exactly the shipped `collections_file` behaviour; sqlite/postgres are seeded
    # once from that file so switching backends needs no hand-transcription.
    collection_store = make_collection_store(settings)
    await seed_from_json(collection_store, settings)
    app.state.collection_store = collection_store

    # The user-profile + ACL store (ADR-0004 decisions 1/4-6). ONE object, ONE
    # database: every ACL store also satisfies the UserStore protocol (shares live
    # in the same tenant DB as users), so we build the ACL store here and install
    # it as BOTH the user-store singleton (the auth hook's profile upserts) and the
    # acl-store singleton (the authorization seam). Built here — not lazily on the
    # auth path — so the sqlite backend's synchronous DDL never blocks the request
    # loop.
    from typing import cast

    from ragstack.acl_store import AclStore, reset_acl_store, set_acl_store
    from ragstack.group_store import get_group_store, reset_group_store, set_group_store
    from ragstack.user_store import UserStore, set_user_store

    # ONE store object governs FOUR tables (users + shares + groups +
    # group_members): a GroupStore is-an AclStore is-a UserStore over the same
    # sqlite file / asyncpg pool. Build it once and install it as all three
    # singletons so the group a share names, the membership authz expands, and
    # the profile the auth hook upserts are the same rows in one database — one
    # close(), no drift (issue #245 part 2 / group_store.get_group_store note).
    # The three protocols are declared separately but coincide on the concrete
    # object, so cast to each view for the type checker.
    group_store = get_group_store()
    set_group_store(group_store)
    acl_store = cast(AclStore, group_store)
    set_acl_store(acl_store)
    user_view = cast(UserStore, group_store)
    set_user_store(user_view)
    # Probe the backend NOW and fail startup on error. validate_user_store_settings
    # only catches backend-name typos, and the postgres store creates its pool
    # lazily on first use — so a mistyped/unreachable USER_STORE_DSN would
    # otherwise boot clean while every auth-path profile write fails silently
    # (they are fire-and-forget). This keeps postgres on the same fail-fast
    # footing as sqlite, whose DDL already runs synchronously right here. list_users
    # touches the users table; owner_of exercises the shares table + its indexes.
    await user_view.list_users(limit=1)
    await acl_store.owner_of("__startup_probe__")
    app.state.user_store = acl_store
    app.state.acl_store = acl_store

    # Multi-collection registry: the pinned/derived collection is the "default"
    # entry; the store's specs add cross-model / per-chunker entries. With no
    # config this is a one-entry registry equal to the default (unchanged).
    app.state.collections = await _build_collection_registry(
        http_client,
        graph_store=graph_store,
        default_embedder=embedder,
        default_vector_store=vector_store,
        default_text_index=text_index,
        default_retriever=retriever,
        default_collection=default_collection,
        store=collection_store,
    )

    # ACL backfill (issue #243 / ADR-0004 decision 4): reconcile every registry
    # collection's ACL rows. LEGACY collections (spec records no creator) get
    # owner=acl_backfill_owner + public read, so pre-existing corpora stay
    # world-readable exactly as before ownership existed; a collection whose spec
    # records its creator has a lost owner row repaired to that creator and stays
    # private (absence-of-owner alone must never publish anything — fail closed).
    # Idempotent and concurrency-safe (each row keyed on its own history; the
    # partial unique indexes turn a double-grant into a no-op), so it runs on
    # every boot.
    from ragstack.api.access import backfill_collection_owners

    await backfill_collection_owners(
        app.state.collections, acl_store, settings.acl_backfill_owner
    )

    # Runtime model registry (Phase 1): load persisted models + assignments, then
    # apply the hot-swappable assignments over the settings-built defaults so a
    # prior /v1/admin/config/assignments survives restart.
    app.state.model_registry = ModelRegistry.load(
        settings.models_registry_file, allowlist=settings.model_url_allowlist
    )
    for task, model_id in list(app.state.model_registry.assignments.items()):
        entry = app.state.model_registry.get(model_id)
        if entry is None:
            continue
        try:
            apply_assignment(app, task, entry)
            log.info("applied persisted assignment %s -> %s", task, model_id)
        except Exception:
            log.warning("failed to apply assignment %s -> %s", task, model_id, exc_info=True)

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
        # Same for the collection store (only the postgres backend holds a pool).
        await collection_store.close()
        # And the ACL/user store (one object) — but FIRST drain any in-flight
        # fire-and-forget profile upserts: one that outlived its request would
        # otherwise run against a closed store, or rebuild a fresh one via
        # get_user_store()/get_acl_store() after the reset below (an asyncpg pool
        # nobody ever closes). Then reset BOTH module singletons so nothing
        # resolves a closed store after shutdown (tests re-enter the lifespan).
        from ragstack.api.security import (
            drain_profile_upserts,
            reset_disabled_cache,
            reset_role_cache,
        )
        from ragstack.user_store import reset_user_store

        await drain_profile_upserts()
        await acl_store.close()
        reset_user_store()
        # The auth path memoizes "is this subject a disabled service account?"
        # against the store we just closed (#258). Drop it with the singleton so
        # a re-entered lifespan (tests) starts from the new store's answers.
        reset_disabled_cache()
        # Same for "does this subject's row say admin?" — a verdict memoized
        # against a closed store must not decide a re-entered lifespan's first
        # request.
        reset_role_cache()
        reset_acl_store()
        reset_group_store()
        # Close the ES client if the text index holds one.
        if hasattr(text_index, "close"):
            await text_index.close()
        # Close the Neo4j driver if the graph store holds one.
        if graph_store is not None and hasattr(graph_store, "close"):
            await graph_store.close()
        # Stop the semantic chunker's background embed loop, if any — the default
        # pipeline's, plus one per collection that ran a semantic targeted ingest.
        if embed_bridge is not None:
            embed_bridge.close()
        for cid, b in list(collection_embed_bridges.items()):
            try:
                b.close()
            except Exception:  # noqa: BLE001 — shutdown must not raise
                log.warning("collection %r: embed bridge close failed", cid, exc_info=True)
        collection_embed_bridges.clear()


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


def get_collections(request: Request) -> CollectionRegistry:
    return request.app.state.collections


def get_collection_store(request: Request) -> CollectionStore:
    """The durable registry behind ``app.state.collections``. Falls back to the
    JSON-file backend when the app was assembled without a lifespan (duck-typed
    ``app.state`` in tests), so the create/delete paths always have a store."""
    store = getattr(request.app.state, "collection_store", None)
    if store is None:
        return JsonFileCollectionStore(settings)
    return store


def get_model_registry(request: Request) -> ModelRegistry:
    return request.app.state.model_registry


def get_http_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.http_client


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
