"""List the collections the query API can serve (tenant-scoped read).

Principal-gated (any authenticated caller), like ``/stats/stores`` — the Explore
UI needs it to populate the collection picker, so it must NOT be admin-only. Each
entry's ``count`` is filtered to the caller's readable tenants (own + public),
never a global store total.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from ragstack.api.collections import (
    CollectionEntry,
    CollectionRegistry,
    CollectionSpec,
)
from ragstack.api.deps import (
    build_collection_entry,
    get_collection_store,
    get_collections,
    get_model_registry,
    materialize_config_manifest_for_spec,
    probe_tenant_count,
)
from ragstack.api.model_registry import HOT_SWAPPABLE, ModelRegistry
from ragstack.api.security import ROLE_ADMIN, Principal, require_role, resolve_principal
from ragstack.collection_store import CollectionStore
from ragstack.config import settings
from ragstack.ingestion.chunkers import CHUNK_METHODS
from ragstack.provenance import chunk_descriptor, delete_manifest, read_manifest
from ragstack.stores.qdrant import collection_name
from ragstack.tenancy import allowed_collection_ids, readable_tenants

log = logging.getLogger(__name__)

router = APIRouter()


class Provenance(BaseModel):
    """Verified build lineage from the collection's manifest (null when no
    manifest exists — e.g. ``COLLECTION_MANIFEST_DIR`` unset, or an out-of-band
    collection that predates manifests).

    Deliberately excludes ``embedding_endpoints``: the manifest records them, but
    they are internal infra URLs and this endpoint is readable by any principal
    (same reasoning as /v1/models/available hiding base_urls)."""

    collection: str = ""  # physical store name the manifest describes
    model: str = ""  # embedding model as *built* — compare against the registry label
    dim: int | None = None
    embedding_api: str = ""
    chunk_method: str | None = None
    chunk_size: int | None = None
    chunk_overlap: int | None = None
    chunk_params: dict[str, Any] = {}
    spec_hash: str = ""
    corpus: str = ""
    chunk_count: int | None = None
    ingested_at: str = ""
    ragstack_version: str = ""
    source: str = ""  # "ingest" (verified) | "config" (materialized from registry)


class CollectionInfo(BaseModel):
    id: str
    label: str
    model: str
    dim: int
    chunk_method: str | None = None  # from the registry label (may be operator-asserted)
    chunk_size: int | None = None
    default: bool
    count: int | None = None  # vector-store tenant-filtered count; null when unavailable
    text_count: int | None = None  # text-index (BM25) tenant-filtered count; for a vector↔text parity check
    provenance: Provenance | None = None  # verified lineage from the manifest


class CollectionsResponse(BaseModel):
    collections: list[CollectionInfo]
    default: str


def _collection_info(
    entry: CollectionEntry, count: int | None, text_count: int | None = None
) -> CollectionInfo:
    """Assemble a CollectionInfo from a built entry + its (tenant-scoped) vector
    and text counts, folding in verified provenance from the manifest when
    present. Shared by the list and create paths so their shapes can't drift."""
    m = read_manifest(settings.collection_manifest_dir, entry.collection)
    prov = (
        Provenance(
            collection=m.collection,
            model=m.model,
            dim=m.dim,
            embedding_api=m.embedding_api,
            chunk_method=m.chunk_method or None,
            chunk_size=m.chunk_size,
            chunk_overlap=m.chunk_overlap,
            chunk_params=m.chunk_params,
            spec_hash=m.spec_hash,
            corpus=m.corpus,
            chunk_count=m.chunk_count,
            ingested_at=m.ingested_at,
            ragstack_version=m.ragstack_version,
            source=m.source,
        )
        if m is not None
        else None
    )
    return CollectionInfo(
        id=entry.id,
        label=entry.label,
        model=entry.model,
        dim=entry.dim,
        chunk_method=entry.chunk_method or None,
        chunk_size=entry.chunk_size,
        default=entry.is_default,
        count=count,
        text_count=text_count,
        provenance=prov,
    )


@router.get("/collections", response_model=CollectionsResponse)
async def list_collections(
    principal: Principal = Depends(resolve_principal),
    registry: CollectionRegistry = Depends(get_collections),
) -> CollectionsResponse:
    """Registry collections with tenant-scoped counts and chunk-strategy labels.

    Restricted to the collections the caller's tenant may access (per the
    per-tenant allowlist); unrestricted tenants see every registered collection.
    The reported ``default`` is the caller's effective default (the registry
    default when permitted, else the caller's first accessible collection) so it
    is always one of the listed ids."""
    tenants = readable_tenants(principal.tenant)
    allowed = allowed_collection_ids(principal.tenant, settings.tenant_collections)
    entries = [
        e for e in registry.entries() if allowed is None or e.id in allowed
    ]
    # Per-collection vector + text counts are independent store round-trips —
    # gather them all concurrently so latency is one round-trip, not 2N (the ops
    # dashboard polls this, and Explore/Compare call it on load). Both probes share
    # deps.probe_tenant_count, which degrades to None rather than raising.
    vec_counts, txt_counts = await asyncio.gather(
        asyncio.gather(*(probe_tenant_count(e.vector_store, tenants) for e in entries)),
        asyncio.gather(*(probe_tenant_count(e.text_index, tenants) for e in entries)),
    )
    infos = [
        _collection_info(e, vc, tc)
        for e, vc, tc in zip(entries, vec_counts, txt_counts, strict=True)
    ]
    if allowed is None or registry.default_id in allowed:
        default = registry.default_id
    else:
        default = infos[0].id if infos else registry.default_id
    return CollectionsResponse(collections=infos, default=default)


class ChunkConfig(BaseModel):
    """Chunk strategy for a new collection (build-time; part of its identity)."""

    method: str
    size: int | None = None
    overlap: int | None = None
    params: dict[str, Any] = {}
    model_config = ConfigDict(extra="forbid")


# Chunk methods whose boundaries come from embedding similarity rather than a
# size budget, and the tunables they read out of the free-form ``chunk.params``.
SEMANTIC_METHODS = ("semantic", "semantic_pooled")
# name -> (numeric kind, inclusive min, inclusive max)
SEMANTIC_PARAM_BOUNDS: dict[str, tuple[type, float, float]] = {
    "buffer_size": (int, 1, 50),
    "breakpoint_percentile_threshold": (float, 1, 100),
    "min_chunk_length": (int, 0, 100_000),
}
# sentence/words read size == -1 as "one chunk per document"; the char/token
# window chunkers have no such mode (and would never terminate on a negative size).
WHOLE_DOC_METHODS = ("sentence", "words")


def _validate_chunk(chunk: ChunkConfig) -> None:
    """Reject a chunk config that cannot chunk, with a message a UI can show.

    An unknown method is a 400: chunking is collection *identity*, so a typo must
    not mint a collection nobody can ingest into. Beyond that, the sizes are
    checked for the two configurations that don't merely produce odd chunks but
    *hang* — a size of zero, and an overlap >= the size — since both leave the
    sliding-window loop advancing by <= 0 units per step. Semantic params are
    range-checked because ``params`` is free-form JSON and lands in the chunker
    unvalidated otherwise.
    """
    method = chunk.method
    if method not in CHUNK_METHODS:
        raise HTTPException(
            400,
            f"unknown chunk method {method!r}; valid: {', '.join(sorted(CHUNK_METHODS))}",
        )
    if method in SEMANTIC_METHODS:
        for key, value in (chunk.params or {}).items():
            bounds = SEMANTIC_PARAM_BOUNDS.get(key)
            if bounds is None:
                continue  # params is free-form; unrecognised keys ride along untouched
            kind, low, high = bounds
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise HTTPException(400, f"chunk param {key!r} must be a number; got {value!r}")
            if kind is int and float(value) != int(value):
                raise HTTPException(400, f"chunk param {key!r} must be a whole number")
            if not (low <= float(value) <= high):
                raise HTTPException(400, f"chunk param {key!r} must be between {low} and {high}")
        return
    size = chunk.size
    if size is not None:
        if size == -1 and method not in WHOLE_DOC_METHODS:
            raise HTTPException(
                400,
                f"chunk size -1 (whole document) is only supported by "
                f"{', '.join(WHOLE_DOC_METHODS)}, not {method!r}",
            )
        if size < 1 and size != -1:
            raise HTTPException(400, f"chunk size must be at least 1; got {size}")
    overlap = chunk.overlap
    if overlap is not None:
        if overlap < 0:
            raise HTTPException(400, f"chunk overlap cannot be negative; got {overlap}")
        # An omitted size falls back to the server default at ingest, so that is
        # what the overlap has to be smaller than.
        effective = size if size is not None else settings.chunk_size
        if effective > 0 and overlap >= effective:
            raise HTTPException(
                400,
                f"chunk overlap ({overlap}) must be smaller than the chunk size "
                f"({effective}); otherwise chunking never advances",
            )


class CollectionCreateRequest(BaseModel):
    embedding: str  # id of a registered embedding model
    chunk: ChunkConfig
    # Explicit id → a *named library*: the id is folded into the physical store name
    # so same-spec libraries stay isolated. Omitted → a *corpus*: content-addressed
    # over (model, dim, chunk), so re-creating the same spec is idempotent.
    id: str | None = None
    label: str = ""
    model_config = ConfigDict(extra="forbid")


@router.post(
    "/collections",
    response_model=CollectionInfo,
    status_code=201,
    dependencies=[Depends(require_role(ROLE_ADMIN))],
)
async def create_collection(
    body: CollectionCreateRequest,
    request: Request,
    principal: Principal = Depends(resolve_principal),
    models: ModelRegistry = Depends(get_model_registry),
    registry: CollectionRegistry = Depends(get_collections),
    store: CollectionStore = Depends(get_collection_store),
) -> CollectionInfo:
    """Create a collection bound to a registered embedding model and a chunk
    strategy (build-time model selection). Admin only. The collection is created
    empty; populate it via POST /v1/ingest with the returned id.

    Build-time config *is* collection identity, so this mints a new collection
    rather than editing one — you cannot re-point an index at a new embedder.

    Omit ``id`` for a *corpus*: the physical store is content-addressed over
    (model, dim, chunk), so the same spec is idempotent (409 on a repeat). Supply
    ``id`` for a *named library*: the id is part of the physical name, so two
    libraries with identical build specs stay isolated from each other.
    """
    # 1. Resolve the embedding model-ref against the Phase-1 registry.
    entry = models.get(body.embedding)
    if entry is None:
        raise HTTPException(
            404, f"unknown model {body.embedding!r}; see GET /v1/admin/models/registry"
        )
    if entry.task != "embedding":
        raise HTTPException(
            400, f"model {body.embedding!r} is a {entry.task!r} model, not an embedding model"
        )
    if not (entry.dim and entry.dim > 0):
        raise HTTPException(400, f"embedding model {body.embedding!r} has no positive dim")

    # 2. Validate the chunk strategy (identity input; unknown method or a
    # size/overlap that can't chunk → 400 with a message the UI can show).
    _validate_chunk(body.chunk)

    # 3. Derive the physical name. With no explicit id this is content-addressed
    # over (model, dim, chunk): the same build spec re-maps to the same store, which
    # is what makes corpus re-ingest idempotent. With an explicit id the caller is
    # naming a *library*, and two libraries that happen to share a build spec are
    # still different data — so the id is folded into the physical name and they get
    # separate Qdrant collections / ES indices instead of aliasing one store.
    desc = chunk_descriptor(
        body.chunk.method, body.chunk.size, body.chunk.overlap, body.chunk.params or None
    )
    physical = collection_name(
        settings.qdrant_collection, entry.model, entry.dim, chunk=desc, name=body.id or None
    )
    cid = body.id or physical
    if registry.has(cid):
        raise HTTPException(409, f"collection {cid!r} already exists")

    # 4. Build the spec (the embedder API/model/urls come from the registered model,
    # SSRF-checked at registration; vLLM speaks the OpenAI embeddings API).
    api = "sidecar" if entry.provider == "sidecar" else "openai"
    spec = CollectionSpec(
        id=cid,
        label=body.label,
        collection=physical,
        text_index=physical,
        embedding_api=api,
        embedding_model=entry.model,
        embedding_model_dim=entry.dim,
        embedding_endpoints=list(entry.base_urls),
        chunk_method=body.chunk.method,
        chunk_size=body.chunk.size,
        chunk_overlap=body.chunk.overlap,
        chunk_params=body.chunk.params,
    )

    # 5. Build the live entry (stores + retriever), register it, write-through to
    # the durable collection store so it survives restart, and materialize its
    # config manifest. The store — not this process's registry dict — is the
    # authoritative record: it is what the next startup rebuilds from and what the
    # ingest guard's spec comparison ultimately defends.
    built = await build_collection_entry(
        request.app.state.http_client,
        graph_store=request.app.state.graph_store,
        spec=spec,
    )
    try:
        registry.add(built)
    except KeyError:
        raise HTTPException(409, f"collection {cid!r} already exists") from None
    persisted = await store.put(spec)
    if not persisted:
        log.warning(
            "collection %r created in-memory only (no durable collection store); "
            "lost on restart", cid
        )
    materialize_config_manifest_for_spec(spec)

    tenants = readable_tenants(principal.tenant)
    count = await probe_tenant_count(built.vector_store, tenants)
    return _collection_info(built, count)


class PurgeFailure(BaseModel):
    """One target the purge could not remove, with the backend's own message.
    Its presence means the physical resource may still exist — the purge does not
    roll back, so this is the operator's to-do list, not a warning to ignore."""

    target: str  # vectors | text_index | manifest
    error: str


class PurgeReport(BaseModel):
    """What a ``purge=true`` delete actually destroyed.

    Deliberately three lists rather than a boolean: a purge touches four
    independent systems (registry, Qdrant, Elasticsearch, manifest file) that can
    each succeed, be already-gone, or fail on their own. Reporting them
    separately is what lets a partial failure be *honest* instead of a 500 that
    hides the three deletions that did land."""

    collection_id: str
    purged: bool  # false for the default unregister-only delete
    store: str  # the physical Qdrant collection name
    text_index: str  # the physical Elasticsearch index name
    deleted: list[str]  # targets actually removed
    absent: list[str]  # targets that were already gone (idempotent no-op)
    failed: list[PurgeFailure]  # targets that errored — NOT rolled back
    ok: bool  # no failures; the collection and its data are fully gone


# Purge targets, in the order they're attempted. "registry" first because a
# collection whose binding is gone can no longer be queried or ingested into, so
# even a purge that then fails on Qdrant leaves nothing writing new data into the
# store the operator is about to clean up by hand.
_TARGET_REGISTRY = "registry"
_TARGET_VECTORS = "vectors"
_TARGET_TEXT = "text_index"
_TARGET_MANIFEST = "manifest"


def _shared_store_users(registry: CollectionRegistry, entry: CollectionEntry) -> list[str]:
    """Other registry ids pointing at the same physical Qdrant collection or ES
    index as ``entry``.

    This is the guard that matters (#228): a collection created with an explicit
    id gets its own store, but a *blank*-id (content-addressed) collection shares
    its store with every identically-built collection — and a hand-authored
    ``collections_file`` can alias two ids onto one store deliberately. Purging
    either would silently destroy the other's embeddings, which cost GPU time to
    produce and cannot be recovered from the registry."""
    return sorted(
        e.id
        for e in registry.entries()
        if e.id != entry.id
        and (e.collection == entry.collection or e.es_index() == entry.es_index())
    )


async def _purge_physical(entry: CollectionEntry, report: PurgeReport) -> None:
    """Drop this collection's physical stores + manifest, recording each outcome
    on ``report``. Never raises and never rolls back: a Qdrant drop that succeeded
    cannot be undone by an ES failure, so pretending otherwise would be a lie.
    Each target is independent, so one failure must not skip the rest."""
    drops: list[tuple[str, Any]] = [
        (_TARGET_VECTORS, getattr(entry.vector_store, "drop_collection", None)),
        (_TARGET_TEXT, getattr(entry.text_index, "drop_index", None)),
    ]
    for target, fn in drops:
        if fn is None:
            report.failed.append(
                PurgeFailure(target=target, error="backend does not support dropping")
            )
            continue
        try:
            existed = await fn()
        except Exception as e:  # noqa: BLE001 — reported, not raised: fail soft + honest
            log.warning("purge %r: %s drop failed: %s", entry.id, target, e)
            report.failed.append(PurgeFailure(target=target, error=f"{type(e).__name__}: {e}"))
        else:
            (report.deleted if existed else report.absent).append(target)
    try:
        removed = delete_manifest(settings.collection_manifest_dir, entry.collection)
    except Exception as e:  # noqa: BLE001 — e.g. a read-only manifest dir
        log.warning("purge %r: manifest delete failed: %s", entry.id, e)
        report.failed.append(PurgeFailure(target=_TARGET_MANIFEST, error=f"{type(e).__name__}: {e}"))
    else:
        (report.deleted if removed else report.absent).append(_TARGET_MANIFEST)


@router.delete(
    "/collections/{collection_id}",
    response_model=None,
    responses={
        200: {"model": PurgeReport, "description": "Purged — see the report for what was removed"},
        204: {"description": "Registry binding removed; physical stores untouched"},
    },
    dependencies=[Depends(require_role(ROLE_ADMIN))],
)
async def delete_collection(
    collection_id: str,
    purge: bool = Query(
        False,
        description=(
            "Also delete the physical Qdrant collection, the Elasticsearch index and the "
            "provenance manifest. Default false: unregister only (the binding, not the data)."
        ),
    ),
    registry: CollectionRegistry = Depends(get_collections),
    store: CollectionStore = Depends(get_collection_store),
) -> Response:
    """Remove a collection registry entry (admin only).

    ``purge=false`` (the default) drops the *binding* only — the underlying Qdrant
    collection and ES index survive with all their chunks. 204, no body.

    ``purge=true`` additionally destroys the data: the physical Qdrant collection,
    the Elasticsearch index and the provenance manifest. **Irreversible** — the
    embeddings are gone and re-creating them costs another ingest. 200 with a
    :class:`PurgeReport` of what was removed, what was already absent, and what
    failed; a partial failure is reported, never rolled back and never hidden
    behind a 500.

    Purge is refused (409) for the default collection, and for a collection whose
    physical store is still referenced by another registry entry — that entry's
    data is not this caller's to destroy.
    """
    if collection_id == registry.default_id:
        raise HTTPException(409, "cannot delete the default collection")
    try:
        entry = registry.resolve(collection_id)
    except KeyError:
        raise HTTPException(404, f"unknown collection {collection_id!r}") from None

    if purge:
        sharers = _shared_store_users(registry, entry)
        if sharers:
            raise HTTPException(
                409,
                f"cannot purge collection {collection_id!r}: its physical store "
                f"({entry.collection}) is also used by {', '.join(repr(s) for s in sharers)}, "
                f"and purging would destroy their data too. Unregister it instead "
                f"(purge=false), or purge the other collections first.",
            )

    if not registry.remove(collection_id):  # pragma: no cover — resolve() just succeeded
        raise HTTPException(404, f"unknown collection {collection_id!r}")
    await store.delete(collection_id)

    if not purge:
        return Response(status_code=204)

    report = PurgeReport(
        collection_id=collection_id,
        purged=True,
        store=entry.collection,
        text_index=entry.es_index(),
        deleted=[_TARGET_REGISTRY],
        absent=[],
        failed=[],
        ok=True,
    )
    await _purge_physical(entry, report)
    report.ok = not report.failed
    log.info(
        "purged collection %r (store=%s): deleted=%s absent=%s failed=%s",
        collection_id, entry.collection, report.deleted, report.absent,
        [f.target for f in report.failed],
    )
    return JSONResponse(status_code=200, content=report.model_dump())


class AvailableModel(BaseModel):
    id: str
    task: str  # llm | reranker
    label: str
    model: str
    provider: str


class AvailableModelsResponse(BaseModel):
    models: list[AvailableModel]


@router.get("/models/available", response_model=AvailableModelsResponse)
async def list_available_models(
    models: ModelRegistry = Depends(get_model_registry),
) -> AvailableModelsResponse:
    """Registered models assignable to a hot-swappable task (llm / reranker), for
    the Compare per-lane model pickers. Authenticated callers only (the router is
    mounted with ``resolve_principal``) — but base_urls are NOT exposed
    (registration is admin-only + SSRF-checked; callers only need to name a
    curated model)."""
    out = [
        AvailableModel(
            id=e.id, task=e.task, label=e.model or e.id, model=e.model, provider=e.provider
        )
        for e in models.entries()
        if e.task in HOT_SWAPPABLE
    ]
    return AvailableModelsResponse(models=out)
