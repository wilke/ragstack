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
from pydantic import BaseModel, ConfigDict, Field

from ragstack.acl_store import (
    GRANTEE_GROUP,
    GRANTEE_USER,
    PERM_OWNER,
    PERM_READ,
    PUBLIC_GROUP,
    ShareInvariantError,
    ShareNotFoundError,
    ShareRecord,
    get_acl_store,
)
from ragstack.api.access import (
    enforce_access,
    filter_readable,
    revoke_collection_acl,
    write_owner_row,
)
from ragstack.api.collections import (
    RESERVED_COLLECTION_ID,
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
from ragstack.api.security import ROLE_ADMIN, ROLE_USER, Principal, resolve_principal
from ragstack.authz import AuthzUnavailable, resolve_access
from ragstack.collection_store import CollectionStore
from ragstack.config import settings
from ragstack.group_store import get_group_store
from ragstack.ingestion.chunkers import CHUNK_METHODS
from ragstack.provenance import chunk_descriptor, delete_manifest, read_manifest
from ragstack.stores.qdrant import collection_name
from ragstack.tenancy import allowed_collection_ids, readable_tenants
from ragstack.user_store import RESERVED_SERVICE_SUBJECTS

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
    entry: CollectionEntry,
    count: int | None,
    text_count: int | None = None,
    *,
    is_default: bool = False,
) -> CollectionInfo:
    """Assemble a CollectionInfo from a built entry + its (tenant-scoped) vector
    and text counts, folding in verified provenance from the manifest when
    present. Shared by the list and create paths so their shapes can't drift.

    ``is_default`` is the POINTER question — ``entry.id == registry.default_id``
    — supplied by the caller, deliberately NOT read off the entry. The entry's
    own ``is_shared_surface`` flag answers a different question (legacy
    tenant-stamped surface) and carries authz exemptions with it; see
    ``CollectionEntry.is_shared_surface``."""
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
        default=is_default,
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
    # ...then drop the ones the caller may not READ (owner / grant / public), on
    # top of the allowlist — ownership INTERSECTS confinement, never replaces it
    # (ADR-0003 decision 3). Admin sees all (admin bypass inside resolve_access);
    # keyless dev is a no-op. A store outage here 503s rather than silently hiding
    # a readable collection.
    entries = await filter_readable(principal, entries)
    # Per-collection vector + text counts are independent store round-trips —
    # gather them all concurrently so latency is one round-trip, not 2N (the ops
    # dashboard polls this, and Explore/Compare call it on load). Both probes share
    # deps.probe_tenant_count, which degrades to None rather than raising.
    vec_counts, txt_counts = await asyncio.gather(
        asyncio.gather(*(probe_tenant_count(e.vector_store, tenants) for e in entries)),
        asyncio.gather(*(probe_tenant_count(e.text_index, tenants) for e in entries)),
    )
    infos = [
        _collection_info(e, vc, tc, is_default=e.id == registry.default_id)
        for e, vc, tc in zip(entries, vec_counts, txt_counts, strict=True)
    ]
    # The reported default must be one of the listed ids: the registry default
    # when the caller can actually see it (allowlist AND readable), else its first
    # visible collection.
    visible_ids = {i.id for i in infos}
    if registry.default_id in visible_ids:
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
    # Both build-spec fields are OPTIONAL (ADR-0003 decision 3): omitted → the
    # server's default build spec is resolved into concrete values at create
    # time. Supplying either is an admin-only override (403 for other roles) —
    # they change what every future ingest into the collection produces.
    embedding: str | None = None  # id of a registered embedding model
    chunk: ChunkConfig | None = None
    # Explicit id → a *named library*: the id is folded into the physical store name
    # so same-spec libraries stay isolated. Omitted → a *corpus*: content-addressed
    # over (model, dim, chunk), so re-creating the same spec is idempotent.
    # max_length: the id is persisted verbatim as the registry key and folded
    # (slugged + hashed) into physical store names — creation is open to any
    # authenticated principal, so an unbounded string here would be an
    # unbounded caller-controlled write into the shared registry.
    id: str | None = Field(default=None, max_length=128)
    label: str = Field(default="", max_length=256)
    model_config = ConfigDict(extra="forbid")


@router.post(
    "/collections",
    response_model=CollectionInfo,
    status_code=201,
)
async def create_collection(
    body: CollectionCreateRequest,
    request: Request,
    principal: Principal = Depends(resolve_principal),
    models: ModelRegistry = Depends(get_model_registry),
    registry: CollectionRegistry = Depends(get_collections),
    store: CollectionStore = Depends(get_collection_store),
) -> CollectionInfo:
    """Create a collection bound to an embedding model and a chunk strategy
    (build-time model selection). Open to any authenticated principal
    (ADR-0003: a library *is* a collection, created directly by users); the
    ``embedding``/``chunk`` build-spec overrides are admin-only. The collection
    is created empty; populate it via POST /v1/ingest with the returned id.

    Build-time config *is* collection identity, so this mints a new collection
    rather than editing one — you cannot re-point an index at a new embedder.
    An omitted ``embedding``/``chunk`` is resolved from the server-default build
    spec *before* the identity is derived, so the collection carries concrete
    values: a later change of server defaults never changes an existing
    collection (it mints a new one on the next omitted-spec create).

    Omit ``id`` for a *corpus*: the physical store is content-addressed over
    (model, dim, chunk), so the same spec is idempotent (409 on a repeat). Supply
    ``id`` for a *named library*: the id is part of the physical name, so two
    libraries with identical build specs stay isolated from each other.
    """
    # 0. Authorization: creation itself is open, but the build-spec fields stay
    # admin-only — they change what every future ingest into the collection
    # produces (and `embedding` names admin-registered infra).
    if principal.role != ROLE_ADMIN and (body.embedding is not None or body.chunk is not None):
        raise HTTPException(
            403,
            "build-spec overrides ('embedding', 'chunk') are admin-only; omit both "
            "fields to create a collection from the server-default build spec",
        )

    # 0b. Capacity. ADR-0003: the collection count is the binding constraint
    # (Qdrant budget ~100-150 per instance; thread exhaustion near ~1000, crash
    # on create ~2000). Creation is open and each create mints a physical Qdrant
    # collection + ES index, so without this cap any caller could loop the
    # endpoint into an instance-wide denial of service. Applies to admins too —
    # the limit is physical, not an authorization tier; raise MAX_COLLECTIONS
    # deliberately if the budget genuinely grows.
    limit = settings.max_collections
    if limit > 0 and len(registry.entries()) >= limit:
        raise HTTPException(
            403,
            f"collection limit reached ({limit}): the server caps registered "
            "collections because each one costs physical Qdrant/Elasticsearch "
            "resources (ADR-0003). Delete unused collections or have the "
            "operator raise MAX_COLLECTIONS",
        )

    # 1. Resolve the embedding backend: an explicit model-ref against the Phase-1
    # registry (admin path), or the server-default embedder when omitted. The
    # default path resolves CONCRETE values from settings here, not at ingest,
    # so the content-address, the persisted spec and the manifest all record what
    # was actually built (and the ingest-time spec guard keeps enforcing).
    if body.embedding is not None:
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
        # The embedder API/model/urls come from the registered model, SSRF-checked
        # at registration; vLLM speaks the OpenAI embeddings API.
        emb_api = "sidecar" if entry.provider == "sidecar" else "openai"
        emb_model = entry.model
        emb_dim = entry.dim
        emb_endpoints = list(entry.base_urls)
    else:
        emb_api = settings.embedding_api  # already "sidecar" | "openai"
        emb_model = settings.embedding_model
        emb_dim = settings.embedding_model_dim
        # Same fallback as deps.embedding_urls(): fan-out endpoints override the
        # single sidecar URL.
        emb_endpoints = settings.embedding_endpoints or [settings.embedding_sidecar_url]

    # 2. Resolve the chunk strategy. Supplied → validate it (identity input;
    # unknown method or a size/overlap that can't chunk → 400 with a message the
    # UI can show). Omitted → the server-default chunker, resolved to concrete
    # values NOW so `chunk omitted` and `chunk == explicit server defaults`
    # content-address to the same physical store (chunk_descriptor renders None
    # slots as empty strings, which would otherwise split them).
    if body.chunk is not None:
        _validate_chunk(body.chunk)
        chunk = body.chunk
    else:
        chunk = ChunkConfig(
            method=settings.chunk_method,
            size=settings.chunk_size,
            overlap=settings.chunk_overlap,
        )

    # 3. Derive the physical name. With no explicit id this is content-addressed
    # over (model, dim, chunk): the same build spec re-maps to the same store, which
    # is what makes corpus re-ingest idempotent. With an explicit id the caller is
    # naming a *library*, and two libraries that happen to share a build spec are
    # still different data — so the id is folded into the physical name and they get
    # separate Qdrant collections / ES indices instead of aliasing one store.
    desc = chunk_descriptor(chunk.method, chunk.size, chunk.overlap, chunk.params or None)
    physical = collection_name(
        settings.qdrant_collection, emb_model, emb_dim, chunk=desc, name=body.id or None
    )
    cid = body.id or physical
    if cid == RESERVED_COLLECTION_ID:
        # `default` names the POINTER, not a collection. It used to be
        # unmintable only by accident — the synthetic entry always occupied the
        # id, so `registry.has(cid)` below happened to 409. Now that the entry is
        # not synthesised when a spec already serves the store (#275), that
        # accident is gone on exactly the deployments where a user-minted
        # `default` would be most confusing.
        raise HTTPException(
            400,
            f"{RESERVED_COLLECTION_ID!r} is reserved: it names the collection a "
            "request resolves to when it omits 'collection', not a collection "
            "you can create. Pick another id.",
        )
    if registry.has(cid):
        # "already exists" confirms the id to the caller — an enumeration oracle
        # for a stranger's private, unreadable named library. Only say so to a
        # caller who can already read it (owner / grant / public / admin); to
        # everyone else the id is merely "unavailable", matching the read path's
        # leak-safe posture and the residual-ACL message in write_owner_row.
        try:
            decision = await resolve_access(
                principal.tenant, principal.role, cid, "read", get_acl_store()
            )
        except AuthzUnavailable:
            raise HTTPException(503, "authorization store unavailable") from None
        if decision.allowed:
            raise HTTPException(409, f"collection {cid!r} already exists")
        raise HTTPException(409, f"collection id {cid!r} is unavailable; choose a different id")
    if not body.id:
        # `not body.id`, NOT `body.id is None`: an empty-string id is treated as
        # omitted everywhere else (`cid = body.id or physical` above,
        # `name=body.id or None` in the physical name), so it must also take the
        # content-addressed guard below — otherwise {"id": ""} would skip the
        # alias check this branch exists for.
        # A content-addressed create whose physical store is already served under
        # another registry id (e.g. the default collection, whose derived name
        # folds in the same server defaults) would silently alias that entry's
        # data — refuse instead, and name the way out.
        sharers = sorted(e.id for e in registry.entries() if e.collection == physical)
        if sharers:
            raise HTTPException(
                409,
                f"a collection with this exact build spec already exists as "
                f"{', '.join(repr(s) for s in sharers)} (content-addressed creates share "
                f"one physical store per spec); supply an explicit 'id' to mint a "
                f"separate named library",
            )

    # 4. Build the spec from the RESOLVED values (never None for an omitted
    # field), so spec_hash/409 ingest guarding and the manifest stay concrete.
    spec = CollectionSpec(
        id=cid,
        label=body.label,
        # The creator, recorded in the SAME durable write as the spec: the
        # startup backfill reads it to repair a lost owner row to THIS subject
        # (private) instead of publishing the collection as legacy.
        owner=principal.tenant,
        collection=physical,
        text_index=physical,
        embedding_api=emb_api,
        embedding_model=emb_model,
        embedding_model_dim=emb_dim,
        embedding_endpoints=emb_endpoints,
        chunk_method=chunk.method,
        chunk_size=chunk.size,
        chunk_overlap=chunk.overlap,
        chunk_params=chunk.params,
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

    # 6. Ownership (ADR-0004 decision 4): the creator OWNS the new collection,
    # which is private by default (no public grant — unlike a backfilled legacy
    # one). Written AFTER the durable registry write so the FK-by-convention holds;
    # keyed on principal.tenant (the subject for bearer callers, the deployment
    # tenant for API-key callers). A failure ROLLS THE CREATE BACK (409 residual
    # ACL state / 503 store outage): a 201 whose ownership silently never landed
    # would leave a durable-but-ownerless collection — exactly the shape a later
    # startup could mis-handle. (A crash inside this window self-heals instead:
    # the backfill repairs the owner row from the spec-recorded creator above.)
    try:
        await write_owner_row(get_acl_store(), cid, principal.tenant)
    except HTTPException:
        registry.remove(cid)
        try:
            await store.delete(cid)
        except Exception:  # noqa: BLE001 — rollback is best-effort; backfill repairs
            log.warning(
                "create %r: rollback of the durable spec failed; the startup "
                "backfill will repair ownership from the recorded creator", cid,
                exc_info=True,
            )
        try:
            delete_manifest(settings.collection_manifest_dir, spec.collection)
        except Exception:  # noqa: BLE001 — a stale config manifest is harmless
            pass
        raise

    tenants = readable_tenants(principal.tenant)
    count = await probe_tenant_count(built.vector_store, tenants)
    # A just-created collection is never the pointer target: making it one is a
    # separate, deliberate act (DEFAULT_COLLECTION_ID / a stored preference).
    return _collection_info(built, count, is_default=False)


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
)
async def delete_collection(
    collection_id: str,
    principal: Principal = Depends(resolve_principal),
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
    """Remove a collection registry entry (owner or admin).

    ADR-0003: a user manages their own private collections, so this is no longer
    admin-only — the caller must OWN the collection, or be an admin (whose bypass
    is the one logged branch in :func:`resolve_access`). The gate runs through the
    ownership seam like every other, with the ``owner`` action.

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
    data is not this caller's to destroy (owning a registry id is not owning the
    physical store it may share content-addressed with others).

    Deleting also revokes every ACL row of the collection (the owner row and all
    shares, softly — audit history survives), so a later collection reusing the
    same id starts with a clean slate instead of inheriting the deleted one's
    owner or ``public`` grant.
    """
    # TWO guards, because this used to be one by accident. The pointer target and
    # the legacy shared surface were always the same entry, so `== default_id`
    # incidentally protected the flagship corpus. Repoint the pointer and that
    # protection moves with it — leaving the shared surface deletable (and
    # purgeable: drop_collection on a multi-million-point Qdrant collection and
    # its ES index).
    if registry.has(collection_id) and registry.resolve(collection_id).is_shared_surface:
        raise HTTPException(
            409,
            "cannot delete the shared collection: it is the settings-derived "
            "corpus this server was configured to serve, not a collection this "
            "API created",
        )
    if collection_id == registry.default_id:
        # Name the way out. Before the pointer was configurable this could only
        # ever be the synthetic entry; now it can be a real, user-owned
        # collection, and its owner would otherwise have no recourse at all.
        raise HTTPException(
            409,
            f"{collection_id!r} is the collection requests resolve to when they "
            "omit 'collection'; repoint DEFAULT_COLLECTION_ID before deleting it",
        )
    try:
        entry = registry.resolve(collection_id)
    except KeyError:
        raise HTTPException(404, f"unknown collection {collection_id!r}") from None

    # Owner-or-admin: 403 for a non-owner, 503 if the ACL store can't answer.
    await enforce_access(principal, entry.id, "owner")

    # BOTH guards sit AFTER the owner gate, deliberately: a 409 that fires for a
    # caller who cannot read the collection would be an existence oracle for a
    # stranger's private id (see api/access.py's leak-safe posture).
    #
    # Between them they make ADR-0002 decision 5 — "a physical index has exactly
    # one registry entry" — TOTAL. #279 enforced the "not two" half at registry
    # build. This is the "not zero" half: unregistering the last entry for a
    # store leaves data that no entry claims, and therefore that no ACL governs.
    # deps.py already refuses that state at startup ("serving it under NO id is
    # worse"); the delete path used to manufacture it at runtime, on request.
    #
    # Exactly one of the two branches is legal for any given collection, so
    # nothing becomes undeletable:
    #   * store shared with another entry  -> purge refused, unregister allowed
    #   * store claimed by this entry only -> unregister refused, purge allowed
    vec_shared = any(
        e.id != entry.id and e.collection == entry.collection for e in registry.entries()
    )
    es_shared = any(
        e.id != entry.id and e.es_index() == entry.es_index() for e in registry.entries()
    )
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
    # `and`, not `or`: a half-shared entry (one leg claimed, one not) would be
    # undeletable by either branch — but registry build refuses that shape at
    # startup, so it cannot occur on a running server.
    elif not (vec_shared and es_shared):
        raise HTTPException(
            409,
            f"cannot unregister collection {collection_id!r} without purging: no "
            f"other registry entry claims its physical store ({entry.collection}) "
            f"or text index ({entry.es_index()}), so dropping the binding would "
            "leave that data with no registry entry — and therefore no ACL "
            "governing who may read it (ADR-0002 decision 5). Delete the data too "
            "with ?purge=true, or leave the collection registered.",
        )

    # Revoke the collection's ACL rows (owner + every share) BEFORE the registry
    # entry goes away: the id namespace is reusable, so a stale active owner row
    # would hand ownership of the NEXT collection minted under this id to today's
    # owner (hijack), and a stale `public read` row would silently publish it.
    # A store outage 503s here and aborts with the registry intact (fail closed);
    # retrying the delete is safe.
    await revoke_collection_acl(get_acl_store(), entry.id, principal.tenant)

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


# --------------------------------------------------------------------------- #
# Collection shares (issue #244) — grant / list / revoke, owner-or-admin
# --------------------------------------------------------------------------- #

#: Reserved literals that expand to the built-in world-readable ``public`` group
#: server-side. Sharing with everyone is ``GRANT read TO public`` (ADR-0004
#: decision 4); it is never a user grantee and is never prefixed with an issuer.
_PUBLIC_LITERALS = frozenset({"@public", "public"})

#: Default issuer for a bare (unprefixed) grantee username. A share dialog sends
#: an email-shaped BV-BRC username (e.g. ``alice@patricbrc.org``); the stored
#: subject MUST be ``bvbrc:<username>`` or it will never match the caller's
#: principal at read time. A grantee that already contains ``':'`` is taken to be
#: a full ``issuer:subject`` string as-is — the server does NOT guess an OIDC
#: subject from a bare username (bare names are BV-BRC only).
_DEFAULT_ISSUER = "bvbrc"

#: Reserved prefixes naming a RAGStack group by id as a grantee. Intercepted
#: BEFORE the generic ``':' → user`` branch so a bare ``group:<id>`` cannot be
#: mis-parsed as an ``issuer='group'`` user subject. Mirrors ``@public``.
_GROUP_PREFIXES = ("@group:", "group:")

#: Reserved prefix naming a SERVICE ACCOUNT (issue #258) as a user grantee: the
#: subject after it is kept verbatim and, being colon-free, stays in the machine
#: namespace instead of being qualified to ``bvbrc:<value>``.
#:
#: It has to be explicit. A service subject is colon-free by construction, and a
#: bare colon-free grantee is exactly what the share dialog sends for a BV-BRC
#: username — so without a prefix the two are indistinguishable strings, and the
#: default-issuer rule (which the UI depends on) would qualify every service
#: grant into a federated subject that can never authenticate: an inert grant.
#:
#: Only the ``@``-sigil form is accepted, unlike ``@group:``/``group:``. A bare
#: ``service:x`` would shadow a federated subject whose issuer is literally
#: ``service`` and silently retarget the grant into the machine namespace, and
#: nothing needs the bare spelling.
_SERVICE_PREFIX = "@service:"


class ShareGrantRequest(BaseModel):
    """POST body for granting a share. v1 is deliberately minimal and read-only:
    ``grant_option`` (WITH GRANT OPTION) and ``owner`` grants are NOT exposed —
    ownership is transferred, never granted, through its own flow."""

    model_config = ConfigDict(extra="forbid")

    grantee: str = Field(
        ...,
        min_length=1,
        description=(
            "Who to share with. Either the literal '@public'/'public' (→ the "
            "built-in public group, read-only), '@group:<id>' (a RAGStack group), "
            "'@service:<subject>' (a service account, kept colon-free), a full "
            "'issuer:subject' string (kept verbatim), or a bare BV-BRC username "
            "which is prefixed to 'bvbrc:<username>'. The resolved subject is "
            "echoed back so a typo is visible — a typo'd grantee is otherwise an "
            "unclaimable grant."
        ),
    )
    permission: str = Field(
        PERM_READ,
        description="v1 accepts 'read' only; 'write'/'owner' are rejected.",
    )
    issuer: str = Field(
        _DEFAULT_ISSUER,
        description="Issuer used to qualify a bare username (default 'bvbrc').",
    )


class ShareInfo(BaseModel):
    """One share row, as surfaced by the API. ``grant_option`` is omitted (not
    settable in v1); ``active`` is derived from ``revoked_at``."""

    id: str
    collection_id: str
    grantee_type: str  # 'user' | 'group'
    grantee_id: str  # subject (user) or group id ('public' for the public group)
    permission: str
    granted_by: str
    granted_at: str
    revoked_by: str
    revoked_at: str
    active: bool


class SharesResponse(BaseModel):
    shares: list[ShareInfo]
    owner: str | None = None


def _share_info(rec: ShareRecord) -> ShareInfo:
    return ShareInfo(
        id=rec.id,
        collection_id=rec.collection_id,
        grantee_type=rec.grantee_type,
        grantee_id=rec.grantee_id,
        permission=rec.permission,
        granted_by=rec.granted_by,
        granted_at=rec.granted_at,
        revoked_by=rec.revoked_by,
        revoked_at=rec.revoked_at,
        active=rec.active,
    )


def _resolve_grantee(grantee: str, issuer: str) -> tuple[str, str]:
    """Map a grantee input to ``(grantee_type, grantee_id)``.

    - '@public'/'public' → (group, 'public'); never prefixed.
    - '@group:<id>'/'group:<id>' → (group, '<id>'); a named group by id.
    - '@service:<subject>' → (user, '<subject>'); a service account, kept
      COLON-FREE. This is the only input that yields a colon-free user subject,
      and it is what makes a service account (#258) reachable as a grantee at
      all: its subject IS its API-key tenant, so qualifying it with an issuer
      would produce a federated subject nothing ever authenticates as — a grant
      that silently never applies.
    - contains ':' → (user, <verbatim full subject>).
    - otherwise → (user, '<issuer>:<username>').

    Raises :class:`HTTPException` 422 on an empty/whitespace grantee, an empty
    group id, an empty or colon-bearing service subject, a blank issuer for a
    bare username, or a full 'issuer:subject' whose issuer or subject half is
    empty (':', 'bvbrc:', ':alice' — a degenerate, unclaimable grant). Group
    *existence* is validated by the caller (:func:`create_share`), not here —
    this stays a pure string mapping. Service-account *registration* is NOT
    validated either: registration is opt-in, so an operator may share with a
    configured API-key tenant that was never registered, exactly as a share may
    name a user who has never logged in."""
    g = grantee.strip()
    if not g:
        raise HTTPException(422, "grantee must not be empty or whitespace")
    if g in _PUBLIC_LITERALS:
        return GRANTEE_GROUP, PUBLIC_GROUP
    # A named group by id — intercepted before the generic ':' user branch so a
    # bare 'group:<id>' is never parsed as an issuer='group' user subject.
    for pref in _GROUP_PREFIXES:
        if g.startswith(pref):
            gid = g[len(pref):].strip()
            if not gid:
                raise HTTPException(
                    422, "a '@group:<id>' grantee must name a non-empty group id"
                )
            return GRANTEE_GROUP, gid
    # A service account by subject — also before the ':' branch, and the one
    # place a user subject stays unqualified.
    if g.startswith(_SERVICE_PREFIX):
        svc = g[len(_SERVICE_PREFIX):].strip()
        if not svc:
            raise HTTPException(
                422, f"a '{_SERVICE_PREFIX}<subject>' grantee must name a "
                "non-empty service subject"
            )
        if ":" in svc:
            # Would forge a federated grantee through the machine-namespace
            # door ('@service:bvbrc:alice' → 'bvbrc:alice'). Service subjects
            # are colon-free; say so rather than quietly crossing namespaces.
            raise HTTPException(
                422,
                f"service subject {svc!r} must be colon-free: ':' is reserved for "
                "federated 'issuer:sub' identities; grant to one of those by "
                "passing the full subject instead",
            )
        if svc in RESERVED_SERVICE_SUBJECTS:
            # This branch is the ONLY input that yields a colon-free user
            # grantee, so without this check '@service:default' would grant to
            # the fallback tenant EVERY unmapped API key resolves to — an
            # unrestricted '@public' wearing a single-account name, and one the
            # share dialog would echo back as an innocuous 'default'. Registration
            # refuses these subjects in two places; granting must too.
            raise HTTPException(
                422,
                f"{svc!r} is a reserved tenant, not a service account: "
                f"{sorted(RESERVED_SERVICE_SUBJECTS)} are the shared fallback "
                "tenants unmapped keys resolve to, so granting to one would share "
                "with every such caller. Use '@public' if that is the intent",
            )
        return GRANTEE_USER, svc
    if ":" in g:
        # Already a full 'issuer:subject' string (a UI share dialog may still send
        # a bare username, handled below) — keep it verbatim, but reject a
        # degenerate half: an empty issuer or subject can never match a principal
        # at read time, so it would silently create an unclaimable grant (the exact
        # failure the resolved-subject echo exists to prevent).
        issuer_part, _, subject_part = g.partition(":")
        if not issuer_part.strip() or not subject_part.strip():
            raise HTTPException(
                422,
                "a full 'issuer:subject' grantee must have a non-empty issuer and "
                "subject",
            )
        return GRANTEE_USER, g
    iss = issuer.strip()
    if not iss:
        raise HTTPException(422, "issuer must not be empty for a bare username")
    return GRANTEE_USER, f"{iss}:{g}"


@router.get(
    "/collections/{collection_id}/shares",
    response_model=SharesResponse,
)
async def list_shares(
    collection_id: str,
    principal: Principal = Depends(resolve_principal),
    include_revoked: bool = Query(
        False,
        description="Include soft-revoked rows (audit history). Default: active only.",
    ),
    registry: CollectionRegistry = Depends(get_collections),
) -> SharesResponse:
    """List a collection's shares (owner-or-admin).

    Gated on the ``owner`` action through the ONE seam — only the owner (or an
    admin, whose bypass is logged) may enumerate a collection's grantees, so the
    share list never leaks who a private collection is shared with. A non-owner
    who can read it gets 403; one who cannot gets 404 (existence not leaked); a
    store outage is 503 (fail closed)."""
    try:
        entry = registry.resolve(collection_id)
    except KeyError:
        raise HTTPException(404, f"unknown collection {collection_id!r}") from None

    await enforce_access(principal, entry.id, "owner")

    store = get_acl_store()
    try:
        rows = await store.shares_for(entry.id, include_revoked=include_revoked)
        owner = await store.owner_of(entry.id)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — fail closed on any store outage
        raise HTTPException(
            503, "authorization store unavailable; refusing to serve (fail closed)"
        ) from e
    return SharesResponse(shares=[_share_info(r) for r in rows], owner=owner)


@router.post(
    "/collections/{collection_id}/shares",
    response_model=ShareInfo,
    status_code=201,
)
async def create_share(
    collection_id: str,
    req: ShareGrantRequest,
    principal: Principal = Depends(resolve_principal),
    registry: CollectionRegistry = Depends(get_collections),
) -> ShareInfo:
    """Grant a share on a collection (owner-or-admin).

    v1 rules, enforced server-side:

    - ``permission`` is ``read`` only. ``owner`` is rejected (ownership is
      transferred, not granted); ``write`` and anything else is rejected too.
    - a user grantee may be a full ``issuer:subject`` string OR a bare BV-BRC
      username (prefixed to ``bvbrc:<username>``); the literal ``@public`` /
      ``public`` maps to the built-in public group (read-only).
    - a **service account** (#258) is named ``@service:<subject>``, which keeps
      the subject colon-free. Without that prefix a bare subject would be
      qualified to ``bvbrc:<subject>`` — a federated identity the machine
      account can never authenticate as, i.e. a grant that silently never
      applies. The echoed ``grantee_id`` shows which namespace it landed in.
    - sharing with a user who has never logged in pre-provisions a users row
      (``ensure_provisional``) so the FK-by-convention holds; there is NO way to
      verify a BV-BRC username exists first, so the grant is best-effort on an
      unverifiable identifier — the resolved subject is echoed back so a typo is
      visible.

    ``grant_option`` is not exposed (defaults false). A duplicate active grant is
    a 409. A 404 hides an unknown/unreadable collection; 403 a readable non-owned
    one; 503 a store outage."""
    try:
        entry = registry.resolve(collection_id)
    except KeyError:
        raise HTTPException(404, f"unknown collection {collection_id!r}") from None

    await enforce_access(principal, entry.id, "owner")

    perm = (req.permission or PERM_READ).strip()
    if perm == PERM_OWNER:
        raise HTTPException(
            400,
            "ownership is transferred, not granted; use "
            f"POST /v1/collections/{entry.id}/owner",
        )
    if perm != PERM_READ:
        raise HTTPException(
            422, f"v1 shares are read-only; permission {perm!r} is not allowed"
        )

    grantee_type, grantee_id = _resolve_grantee(req.grantee, req.issuer)

    store = get_acl_store()

    if grantee_type == GRANTEE_GROUP and grantee_id != PUBLIC_GROUP:
        # A group grantee is RAGStack-native and therefore verifiable — unlike a
        # BV-BRC username. Validate it exists (and is active) so a typo'd id is a
        # 422 echoing the resolved id, not an unclaimable grant nobody belongs to.
        # (The store keeps group grants read-only via the API perm check above;
        # write/owner resolution is owner-only regardless — authz.py.)
        try:
            group = await get_group_store().get_group(grantee_id)
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001 — fail closed on any store outage
            raise HTTPException(
                503, "authorization store unavailable; refusing to serve (fail closed)"
            ) from e
        if group is None or not group.active:
            raise HTTPException(
                422, f"unknown group {grantee_id!r}; create it via POST /v1/groups first"
            )

    if grantee_type == GRANTEE_USER:
        # A read grant to the collection's own owner is a trivial no-op (the owner
        # already holds every permission) — reject it so it isn't a redundant row.
        try:
            owner = await store.owner_of(entry.id)
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001 — fail closed
            raise HTTPException(
                503, "authorization store unavailable; refusing to serve (fail closed)"
            ) from e
        if owner is not None and owner == grantee_id:
            raise HTTPException(
                409, f"{grantee_id!r} already owns collection {entry.id!r}"
            )
        # Pre-provision the grantee's users row so shares.grantee/granted_by
        # resolve to a real row (mirror write_owner_row). The issuer is taken from
        # the subject itself (e.g. 'bvbrc' for 'bvbrc:alice@…'); an absent user row
        # is not fatal to the grant. There is NO BV-BRC existence check to reuse —
        # a typo'd username is stored as a provisional user that never matches.
        ep = getattr(store, "ensure_provisional", None)
        if ep is not None:
            # A colon-free subject is a SERVICE account (@service:…): we are its
            # issuer, so the row's issuer is '' — exactly what
            # ``_new_service_account`` writes. Splitting on ':' would otherwise
            # record the subject itself as its own issuer.
            grantee_issuer = (
                (grantee_id.split(":", 1)[0] or _DEFAULT_ISSUER)
                if ":" in grantee_id
                else ""
            )
            try:
                await ep(grantee_id, grantee_issuer)
            except Exception:  # noqa: BLE001 — provisioning is best-effort
                log.warning(
                    "share: ensure_provisional(%s) failed", grantee_id, exc_info=True
                )

    try:
        rec = await store.grant(
            entry.id, grantee_type, grantee_id, perm, granted_by=principal.tenant
        )
    except ShareInvariantError as e:
        # Duplicate active grant / public-write / owner-to-group / unknown
        # vocabulary all surface here as an invariant violation.
        raise HTTPException(409, str(e)) from e
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — fail closed on any store outage
        raise HTTPException(
            503, "authorization store unavailable; refusing to serve (fail closed)"
        ) from e
    return _share_info(rec)


@router.delete(
    "/collections/{collection_id}/shares/{share_id}",
    status_code=204,
    response_model=None,
)
async def revoke_share(
    collection_id: str,
    share_id: str,
    principal: Principal = Depends(resolve_principal),
    registry: CollectionRegistry = Depends(get_collections),
) -> Response:
    """Revoke a share (owner-or-admin). Soft + recursive via the store.

    The un-publish of a public collection is just DELETE of its ``public`` share.
    Revocation cascades along ``granted_by`` chains (ADR-0004 decision 5); the row
    count revoked is logged, and 204 is returned.

    The store's ``revoke`` looks a share up by id ALONE — it is not scoped to the
    collection — so the share is first verified to belong to ``{collection_id}``;
    a mismatch (or unknown id) is a 404, never a cross-collection revoke. The
    active owner row is not revocable through this endpoint (that would strip
    ownership; use delete/transfer)."""
    try:
        entry = registry.resolve(collection_id)
    except KeyError:
        raise HTTPException(404, f"unknown collection {collection_id!r}") from None

    await enforce_access(principal, entry.id, "owner")

    store = get_acl_store()
    try:
        history = await store.shares_for(entry.id, include_revoked=True)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — fail closed
        raise HTTPException(
            503, "authorization store unavailable; refusing to serve (fail closed)"
        ) from e
    match = next((s for s in history if s.id == share_id), None)
    if match is None:
        # Unknown id, OR a share of a DIFFERENT collection — both 404 so this
        # endpoint can never revoke another collection's share via a mismatched
        # path (the store itself does not scope by collection).
        raise HTTPException(
            404, f"unknown share {share_id!r} on collection {collection_id!r}"
        )
    if match.permission == PERM_OWNER and match.active:
        raise HTTPException(
            409,
            "the owner row is not revocable via the share API; delete the "
            f"collection or transfer it (POST /v1/collections/{entry.id}/owner)",
        )

    try:
        revoked = await store.revoke(share_id, revoked_by=principal.tenant)
    except ShareNotFoundError:
        raise HTTPException(
            404, f"unknown share {share_id!r} on collection {collection_id!r}"
        ) from None
    log.info(
        "revoked share %s on %r (cascade revoked %d row(s)) by %s",
        share_id, entry.id, len(revoked), principal.tenant,
    )
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Ownership transfer (issue #280) — the flow the shares endpoint points at
# --------------------------------------------------------------------------- #


class OwnerTransferRequest(BaseModel):
    """POST body for handing a collection to a new owner. ``subject`` takes the
    SAME grantee vocabulary as :class:`ShareGrantRequest.grantee` — one
    resolution rule for both, so ``@service:<subject>`` / a full
    ``issuer:subject`` / a bare BV-BRC username mean here exactly what they mean
    there. Group forms (``@public``, ``@group:<id>``) parse fine and are then
    refused: ownership is a user's, never a group's."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(
        ...,
        min_length=1,
        description=(
            "The new owner. A full 'issuer:subject' string (kept verbatim), "
            "'@service:<subject>' (a service account, kept colon-free), or a bare "
            "BV-BRC username prefixed to 'bvbrc:<username>'. Group forms "
            "('@public', '@group:<id>') are rejected — ownership is users only. "
            "The resolved subject is echoed back as 'owner' so a typo is visible "
            "BEFORE it becomes an unreachable collection."
        ),
    )
    issuer: str = Field(
        _DEFAULT_ISSUER,
        description="Issuer used to qualify a bare username (default 'bvbrc').",
    )


class OwnerTransferResponse(BaseModel):
    """What the transfer actually did — deliberately explicit about the OUTGOING
    owner, because both possible policies are surprising when they are silent."""

    collection_id: str
    owner: str  # resolved subject of the new owner (echo — a typo is visible here)
    previous_owner: str  # the outgoing subject, whose owner row was soft-revoked
    revoked_share_id: str  # id of that row; still readable via ?include_revoked=true
    previous_owner_retains_read: bool | None
    share: ShareInfo  # the new, active owner row


@router.post(
    "/collections/{collection_id}/owner",
    response_model=OwnerTransferResponse,
)
async def transfer_collection_owner(
    collection_id: str,
    req: OwnerTransferRequest,
    principal: Principal = Depends(resolve_principal),
    registry: CollectionRegistry = Depends(get_collections),
) -> OwnerTransferResponse:
    """Transfer ownership of a collection to another user (owner-or-admin).

    This is the flow ``POST /v1/collections/{id}/shares`` refuses ``permission:
    owner`` in favour of. Ownership is not a grant you add — there is exactly ONE
    active owner row per collection (the ``shares_active_owner`` partial unique
    index), so handing a collection over is a revoke+grant *pair* that must be
    atomic. It runs through :meth:`AclStore.transfer_owner`, which does both
    inside one transaction on every backend; this endpoint never touches SQL.

    **POST, not PATCH.** PATCH means "merge this partial representation into the
    resource", and there is no ``GET /v1/collections/{id}/owner`` document to
    merge into. What happens here is a state *transition* with audit side effects
    — one row soft-revoked, one row appended — and it is not idempotent: replaying
    it is a 409, not a no-op. That is a POST-shaped action, and it matches the
    verb the sibling share-grant endpoint already uses.

    **The outgoing owner loses access** (unless something else grants it back).
    Their owner row is soft-revoked — never deleted, ADR-0004 decision 6, so the
    handover stays in the audit trail — and no consolation ``read`` grant is
    minted for them. Three reasons: (1) a consolation grant would be a SECOND
    write outside ``transfer_owner``'s transaction, and a crash between the two
    would leave a state no invariant describes, with no rollback for an already
    committed transfer; (2) every permission in this system comes from an
    explicit row with a real ``granted_by`` — a row the system invented on the
    actor's behalf would be a grant nobody asked for, still active, that the new
    owner must discover and revoke; and (3) the common reason to transfer
    (offboarding, a mis-assigned collection) is exactly the case where a silent
    residual read is the leak. Re-granting is one explicit, audited call:
    ``POST /v1/collections/{id}/shares {"grantee": "<previous owner>"}``.

    So that this is not *silent* either way, the response states it: the outgoing
    ``previous_owner``, the ``revoked_share_id`` of their now-revoked row, and
    ``previous_owner_retains_read`` — re-evaluated through the ONE authorization
    seam after the transfer, so it accounts for an independent share, group
    membership or a ``public`` grant. It is evaluated as an ordinary user (an
    admin keeps access through the admin bypass regardless), and is ``null`` if
    the store could not answer — the transfer itself had already committed.

    Transfer is deliberately NON-cascading (ADR-0004): every other share on the
    collection survives the handover untouched.

    Statuses: 400 for a group subject; 404 for an unknown collection or one the
    caller cannot read (unreadable == unknown — the same leak-safe posture as the
    share endpoints); 403 for a readable collection the caller does not own; 409
    for a subject that already owns it, or a collection with no active owner row
    to transfer from; 422 for a malformed subject; 503 for a store outage (fail
    closed).
    """
    try:
        entry = registry.resolve(collection_id)
    except KeyError:
        raise HTTPException(404, f"unknown collection {collection_id!r}") from None

    # Current-owner OR admin. enforce_access already admits admin (the logged
    # bypass in resolve_access) and already maps a denial to 403-if-readable /
    # 404-otherwise, so an unknown id and an unreadable one are indistinguishable.
    await enforce_access(principal, entry.id, "owner")

    # Same resolution as a share grantee — '@service:', 'issuer:subject', bare
    # username, and every reserved-subject/degenerate-form 422 — so the two
    # endpoints can never disagree about what a subject string means.
    grantee_type, new_owner = _resolve_grantee(req.subject, req.issuer)
    if grantee_type == GRANTEE_GROUP:
        # ``_check_grant`` enforces this too, but as a ShareInvariantError deep
        # inside the store — surface it here as the clean 400 it is, naming the
        # two inputs that get here ('@public' and '@group:<id>').
        raise HTTPException(
            400,
            f"ownership is grantable to users only, never to a group: {req.subject!r} "
            f"resolves to the group {new_owner!r}. Share a group in instead "
            f"(POST /v1/collections/{entry.id}/shares)",
        )

    store = get_acl_store()

    # Read the CURRENT owner row (not just owner_of): its id is what the response
    # points at for the audit trail, and one round-trip answers both.
    try:
        active = await store.shares_for(entry.id)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — fail closed on any store outage
        raise HTTPException(
            503, "authorization store unavailable; refusing to serve (fail closed)"
        ) from e
    current = next((s for s in active if s.permission == PERM_OWNER), None)
    if current is None:
        # Nothing to transfer FROM. Only reachable by an admin (a non-admin
        # cannot pass the owner gate on an ownerless collection), and it means
        # the owner row was lost — the startup backfill repairs that from the
        # spec-recorded creator; say so rather than minting an owner here, which
        # would let this endpoint *claim* collections instead of hand them over.
        raise HTTPException(
            409,
            f"collection {entry.id!r} has no active owner row to transfer from; "
            "restart repairs a lost owner row from the recorded creator (ACL "
            "backfill), or re-create the collection",
        )
    if current.grantee_id == new_owner:
        # 409, not a 200 no-op: `transfer_owner` would happily revoke and re-insert
        # an identical owner row, churning the audit trail with a handover that
        # never happened. Mirrors the share endpoint's own "already owns" 409.
        raise HTTPException(
            409, f"{new_owner!r} already owns collection {entry.id!r}"
        )

    # Pre-provision the incoming owner's users row (mirrors create_share and
    # write_owner_row): a subject that has never authenticated still needs a row
    # for the FK-by-convention to hold. A colon-free subject is a SERVICE account
    # — we are its issuer, so the row's issuer is ''.
    ep = getattr(store, "ensure_provisional", None)
    if ep is not None:
        new_owner_issuer = (
            (new_owner.split(":", 1)[0] or _DEFAULT_ISSUER) if ":" in new_owner else ""
        )
        try:
            await ep(new_owner, new_owner_issuer)
        except Exception:  # noqa: BLE001 — provisioning is best-effort
            log.warning(
                "transfer: ensure_provisional(%s) failed", new_owner, exc_info=True
            )

    try:
        rec = await store.transfer_owner(entry.id, new_owner, actor=principal.tenant)
    except ShareInvariantError as e:
        # The owner row vanished between the read above and here, or the incoming
        # subject collides with an invariant. Nothing changed (the store validates
        # against the post-revoke state before mutating, inside the transaction).
        raise HTTPException(409, str(e)) from e
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — fail closed on any store outage
        raise HTTPException(
            503, "authorization store unavailable; refusing to serve (fail closed)"
        ) from e

    # What the outgoing owner is left with, answered by the seam rather than by
    # this endpoint's own reasoning — it is the same evaluation a later request
    # would make (direct share, group membership, or a `public` grant). Deliberately
    # not fatal: the transfer is already committed, so a store hiccup here reports
    # `null` instead of a 503 that would falsely read as "the transfer failed".
    retains: bool | None
    try:
        retains = (
            await resolve_access(
                current.grantee_id, ROLE_USER, entry.id, "read", store
            )
        ).allowed
    except Exception:  # noqa: BLE001 — unknown, not failed
        log.warning(
            "transfer %r: could not evaluate residual read for %s",
            entry.id, current.grantee_id, exc_info=True,
        )
        retains = None

    log.info(
        "transferred ownership of %r: %s -> %s (by %s; outgoing row %s revoked, "
        "retains_read=%s)",
        entry.id, current.grantee_id, new_owner, principal.tenant, current.id, retains,
    )
    return OwnerTransferResponse(
        collection_id=entry.id,
        owner=new_owner,
        previous_owner=current.grantee_id,
        revoked_share_id=current.id,
        previous_owner_retains_read=retains,
        share=_share_info(rec),
    )


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
