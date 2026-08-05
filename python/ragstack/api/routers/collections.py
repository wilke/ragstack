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
from ragstack.api.security import ROLE_ADMIN, Principal, resolve_principal
from ragstack.authz import AuthzUnavailable, resolve_access
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
        _collection_info(e, vc, tc)
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
    if collection_id == registry.default_id:
        raise HTTPException(409, "cannot delete the default collection")
    try:
        entry = registry.resolve(collection_id)
    except KeyError:
        raise HTTPException(404, f"unknown collection {collection_id!r}") from None

    # Owner-or-admin: 403 for a non-owner, 503 if the ACL store can't answer.
    await enforce_access(principal, entry.id, "owner")

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
            "built-in public group, read-only), a full 'issuer:subject' string "
            "(kept verbatim), or a bare BV-BRC username which is prefixed to "
            "'bvbrc:<username>'. The resolved subject is echoed back so a typo is "
            "visible — a typo'd grantee is otherwise an unclaimable grant."
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
    - contains ':' → (user, <verbatim full subject>).
    - otherwise → (user, '<issuer>:<username>').

    Raises :class:`HTTPException` 422 on an empty/whitespace grantee, a blank
    issuer for a bare username, or a full 'issuer:subject' whose issuer or subject
    half is empty (':', 'bvbrc:', ':alice' — a degenerate, unclaimable grant)."""
    g = grantee.strip()
    if not g:
        raise HTTPException(422, "grantee must not be empty or whitespace")
    if g in _PUBLIC_LITERALS:
        return GRANTEE_GROUP, PUBLIC_GROUP
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
            400, "ownership is transferred, not granted; use the transfer flow"
        )
    if perm != PERM_READ:
        raise HTTPException(
            422, f"v1 shares are read-only; permission {perm!r} is not allowed"
        )

    grantee_type, grantee_id = _resolve_grantee(req.grantee, req.issuer)

    store = get_acl_store()

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
            grantee_issuer = grantee_id.split(":", 1)[0] or _DEFAULT_ISSUER
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
            "the owner row is not revocable via the share API; "
            "delete the collection or transfer ownership instead",
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
