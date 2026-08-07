"""Document management endpoints."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from pydantic import BaseModel, Field

from ragstack.api.access import enforce_access
from ragstack.api.collections import CollectionEntry, CollectionRegistry
from ragstack.api.deps import (
    BuildSpecMismatch,
    build_ingestor_for,
    check_ingest_build_spec,
    get_collections,
    get_ingestor,
    get_job_store,
)
from ragstack.api.security import Principal, resolve_principal, resolve_tenant
from ragstack.config import settings
from ragstack.ingestion.loaders import DEFAULT_INGEST_SUFFIXES, LoaderError, confine_to_root
from ragstack.ingestion.manifest import build_manifest
from ragstack.ingestion.sharded import ShardedIngestor
from ragstack.jobstore import COMPLETED, FAILED, PENDING, RUNNING, UNKNOWN, JobStore
from ragstack.tenancy import allowed_collection_ids, readable_tenants

log = logging.getLogger(__name__)

router = APIRouter()


def _final_status(counts: dict[str, int]) -> str:
    """Decide a run's overall status from its per-item counts.

    ``completed`` when at least one item completed (partial failures still
    surface via ``items.failed``) or there were no items at all. ``failed`` when
    there were items but none completed — covering both all-failed and the
    leftover-``pending`` case (a shard that raised wholesale reports its items
    failed but never checkpoints them, so they linger pending; without counting
    those, such a run would falsely read completed).
    """
    total = sum(counts.values())
    return FAILED if total > 0 and counts[COMPLETED] == 0 else COMPLETED


async def _run_ingest(
    job_store: JobStore,
    ingestor: ShardedIngestor,
    ingest_root: str,
    job_id: str,
    source: str,
    tenant_id: str,
    target: CollectionEntry | None = None,
) -> None:
    """Background worker: expand the source into a manifest and run it.

    A single file is a 1-item manifest, so files and directories share one path.
    Per-item progress is checkpointed by the ingestor; here we set the overall
    job status. Never raises — a run-level failure is captured as a caller-safe
    label. The job is ``failed`` only when the run itself errors or *every* item
    fails; partial failures leave it ``completed`` with non-zero ``items.failed``.
    """
    await job_store.update(job_id, status=RUNNING)
    try:
        manifest = build_manifest(
            source, suffixes=DEFAULT_INGEST_SUFFIXES, ingest_root=ingest_root or None
        )
        results = await ingestor.ingest_manifest(
            manifest, job_id=job_id, tenant_id=tenant_id
        )
    except Exception as e:
        log.warning("ingest job %s failed: %s", job_id, e)
        await job_store.update(job_id, status=FAILED, error=type(e).__name__)
        return

    counts = await job_store.item_counts(job_id)
    final = _final_status(counts)

    fields: dict[str, object] = {"status": final}
    # Surface chunk ids for the single-document case (back-compat); a batch run
    # reports progress via item counts instead of an unbounded id list. Only set
    # chunk_ids when this run actually produced them — passing [] on a resume
    # that skipped the (already-completed) item would erase the stored ids.
    if len(results) == 1 and results[0].status == COMPLETED:
        fields["chunk_ids"] = results[0].chunk_ids
    await job_store.update(job_id, **fields)

    # Record verified provenance ONLY when the run actually landed data. On an
    # all-items-failed run (final == FAILED) skip it: a source=ingest manifest with
    # a null count would falsely mark the collection "verified" and clobber a prior
    # good/config manifest for the derived collection.
    if final == COMPLETED:
        chunks = sum(len(r.chunk_ids or []) for r in results)
        if target is not None:
            from ragstack.api.deps import write_ingest_manifest_for

            write_ingest_manifest_for(target, source=source, chunk_count=chunks or None)
        else:
            from ragstack.api.deps import write_ingest_manifest

            write_ingest_manifest(source=source, chunk_count=chunks or None)


async def _resolve_ingest_target(
    collection_id: str | None,
    principal: Principal,
    collections: CollectionRegistry,
    app_state: Any,
    prebuilt: ShardedIngestor,
) -> tuple[CollectionEntry | None, ShardedIngestor]:
    """Resolve an optional target collection to ``(entry, ingestor)``.

    Shared by ``POST /v1/ingest`` and ``POST /v1/ingest/upload`` so the routing
    into a collection's bound embedder/chunker/stores — its tenant allowlist
    check, the OWNERSHIP check, and the build-spec guard — cannot drift between
    the two entry points. Omitting the collection (or naming the default id)
    keeps the prebuilt app ingestor. An id the tenant may not access, or an
    unknown id, is a 404 (never a silent write elsewhere). A non-default
    collection the caller can read but does not OWN is a 403
    (:func:`enforce_access`, write action): ingest there is owner-or-admin
    (ADR-0003; write shares deferred); one it cannot even read is the same 404
    as an unknown id (no existence oracle).

    The DEFAULT collection is the exception, deliberately: it is the shared
    pre-ownership multi-tenant surface (backfilled ``public read``, synthetic
    ``acl_backfill_owner``), where per-chunk ``tenant_id`` stamping — not
    collection ownership — is what isolates writers, exactly as before ownership
    existed. Requiring ownership there would lock every non-admin out of the
    flagship shared corpus (and break the conformance contract: core data ops
    need auth, not a role). So the default branch enforces READ on the default
    collection — still the one seam, still 404 for a tenant that may not see it
    (e.g. an operator who revoked its ``public`` row) — and the write lands
    tenant-stamped. The gate has to run on this branch too: a tenant confined
    *away* from the default can still be handed the prebuilt default ingestor by
    omitting ``collection``.

    Both branches run :func:`check_ingest_build_spec` against the collection the
    write will land in, including the default one: a pinned
    ``qdrant_collection_explicit`` keeps its name across a settings change, so the
    default collection is precisely where a swapped embedder or chunker would
    quietly append incoherent data to a 25M-point index.
    """
    if not collection_id or collection_id == collections.default_id:
        default = collections.resolve(collections.default_id)
        _guard(default)
        # READ-not-write is an exemption for the LEGACY SHARED SURFACE, not for
        # "whatever default points at". On that surface per-chunk ``tenant_id``
        # is the isolation and every caller writes into their own stripe, so a
        # write gate would block the thing it is built for. Once ``default`` is
        # a configurable pointer (#276) it can name a genuinely OWNED collection
        # — and there the same exemption would let any reader ingest into
        # somebody else's corpus just by omitting ``collection``. So the
        # exemption keys on the surface, and everything else needs write.
        await enforce_access(
            principal, default.id, "read" if default.is_shared_surface else "write"
        )
        if default.is_shared_surface:
            # The derived entry IS app.state's ingestor — same stores, no build.
            return None, prebuilt
        # The pointer names some OTHER collection. `prebuilt` writes to the
        # settings-derived stores, so returning it here would authorize against
        # one collection and land the bytes in another — the #275 shape inverted.
        return default, build_ingestor_for(app_state, default)
    allowed = allowed_collection_ids(principal.tenant, settings.tenant_collections)
    if allowed is not None and collection_id not in allowed:
        raise HTTPException(
            status_code=404,
            detail=f"unknown collection {collection_id!r}; see GET /v1/collections",
        )
    try:
        target = collections.resolve(collection_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"unknown collection {collection_id!r}; see GET /v1/collections",
        ) from None
    _guard(target)
    await enforce_access(principal, target.id, "write")
    try:
        run_ingestor = build_ingestor_for(app_state, target)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return target, run_ingestor


def _guard(entry: CollectionEntry) -> None:
    """409 when this ingest's build spec contradicts the collection's recorded one.

    409 rather than 400: the request is well-formed and would be valid against a
    collection built the same way — it is the *state* of the target that makes it
    a conflict, and the fix is a new collection, not a new payload."""
    try:
        check_ingest_build_spec(entry)
    except BuildSpecMismatch as e:
        raise HTTPException(status_code=409, detail=str(e)) from None


class IngestRequest(BaseModel):
    source: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    collection: str | None = None  # target collection id; None → server default


class IngestItemCounts(BaseModel):
    total: int = 0
    completed: int = 0
    failed: int = 0
    pending: int = 0


class IngestResponse(BaseModel):
    job_id: str
    status: str
    chunk_ids: list[str] = Field(default_factory=list)
    items: IngestItemCounts | None = None


class DocumentInfo(BaseModel):
    doc_id: str
    source: str
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.post("/ingest", response_model=IngestResponse)
async def ingest(
    request: IngestRequest,
    http_request: Request,
    background_tasks: BackgroundTasks,
    tenant: str = Depends(resolve_tenant),
    principal: Principal = Depends(resolve_principal),
    ingestor: ShardedIngestor = Depends(get_ingestor),
    job_store: JobStore = Depends(get_job_store),
    collections: CollectionRegistry = Depends(get_collections),
) -> IngestResponse:
    """Accept a file or directory for ingestion and run it in the background.

    `request.source` is a path the loader can read (confined to INGEST_ROOT
    when configured). A directory is ingested recursively (.pdf/.txt/.md). Returns
    immediately with a real job_id and `status="accepted"`; poll
    `GET /v1/ingest/{job_id}` for progress (including per-item counts).

    Re-ingesting the same source replaces each document's existing chunks
    (deterministic doc id) rather than duplicating them. A document that yields
    no embeddable chunks fails that item and leaves its prior version intact.

    Each call mints a new job_id, so re-submitting re-processes every document
    (idempotent, but not cheap — it re-embeds). The per-item checkpoint makes a
    run resumable at the ingestor level, but the public API does not yet accept a
    job_id to resume a specific prior run; that wiring is tracked for M2.
    """
    # /v1/ingest ingests DOCUMENTS: each manifest item's source is a document the
    # in-process pipeline loads. The GoWe backend instead treats each source as a
    # pre-built shard FILE its workers load — so a document manifest would be
    # submitted as shard files and fail wholesale. Reject clearly rather than
    # returning an all-failed job. (A pre-sharded ingest entry point for the gowe
    # backend is a separate follow-up; the offline plane uses the embed/load CWL
    # workflows directly.)
    if settings.ingest_backend != "local":
        raise HTTPException(
            status_code=501,
            detail=(
                f"document ingestion via /v1/ingest is not supported with "
                f"ingest_backend={settings.ingest_backend!r} (it expects pre-sharded "
                f"inputs); use ingest_backend=local, or the offline embed/load "
                f"workflows for bulk ingest"
            ),
        )
    # Fail closed when ingest is unconfined. `request.source` is a server-side
    # path; with ingest_root empty, build_manifest skips confine_to_root entirely,
    # so any readable file or tree is ingested and then readable back through
    # /v1/retrieve. Gate here, at request time, rather than at boot: this closes
    # keyless deployments (where DEFAULT_ROLE=admin makes that an unauthenticated
    # arbitrary file read) exactly as it closes keyed ones, and it cannot brick a
    # running deployment that never calls /v1/ingest.
    if not settings.ingest_root.strip():
        raise HTTPException(
            status_code=503,
            detail=(
                "ingest is disabled: INGEST_ROOT is not configured (an unset root "
                "would make POST /v1/ingest an arbitrary server-side file read); "
                "set INGEST_ROOT to the directory holding ingestable documents"
            ),
        )
    # Route into a specific collection when asked: documents are indexed with that
    # collection's bound embedder/chunker/stores (so vectors match its model and
    # land in its index). Omitted — or the default id — keeps the prebuilt app
    # ingestor (backward compatible). An unknown id is a 404, not a silent default.
    target, run_ingestor = await _resolve_ingest_target(
        request.collection, principal, collections, http_request.app.state, ingestor
    )

    job = await job_store.create(source=request.source)
    background_tasks.add_task(
        _run_ingest,
        job_store,
        run_ingestor,
        settings.ingest_root,
        job.job_id,
        request.source,
        tenant,
        target,
    )
    return IngestResponse(job_id=job.job_id, status=job.status)


_PDF_MAGIC = b"%PDF"
_UPLOAD_CHUNK = 1 << 20  # 1 MiB read granularity while streaming to disk


def _safe_pdf_name(raw: str | None, fallback: str) -> str:
    """Reduce a client-supplied filename to a safe, traversal-free basename.

    Keeps only the final path component (drops any directory parts, absolute
    prefixes, and ``..`` segments), strips other separators, and forces a
    ``.pdf`` suffix. Falls back to ``fallback`` when nothing usable remains. The
    result is still re-confined under the staging dir by the caller — this is the
    first line of defence, not the only one.
    """
    # PurePosixPath/ntpath both leave ".." as a name component, so take the last
    # component and reject the dot-names explicitly.
    base = raw.replace("\\", "/").rsplit("/", 1)[-1].strip() if raw else ""
    base = base.replace("\x00", "")
    if base in ("", ".", ".."):
        return fallback
    if not base.lower().endswith(".pdf"):
        base = f"{base}.pdf"
    return base


async def _stage_upload(
    upload: UploadFile, dest: Path, max_bytes: int
) -> None:
    """Sniff, size-check, and stream one uploaded PDF to ``dest``.

    Enforces the PDF content-type + ``%PDF`` magic (415) and the per-file byte
    cap (413) while writing, so an oversize file is rejected without buffering it
    whole in memory. ``dest`` is assumed already confined to the staging dir.
    """
    if (upload.content_type or "").split(";", 1)[0].strip() != "application/pdf":
        raise HTTPException(
            status_code=415,
            detail=f"{upload.filename!r}: only application/pdf uploads are accepted",
        )
    written = 0
    first = True
    with dest.open("wb") as fh:
        while True:
            chunk = await upload.read(_UPLOAD_CHUNK)
            if not chunk:
                break
            if first:
                if not chunk.startswith(_PDF_MAGIC):
                    raise HTTPException(
                        status_code=415,
                        detail=f"{upload.filename!r}: not a PDF (missing %PDF header)",
                    )
                first = False
            written += len(chunk)
            if max_bytes and written > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"{upload.filename!r}: exceeds max_document_bytes "
                        f"({max_bytes} bytes)"
                    ),
                )
            fh.write(chunk)
    if first:  # never entered the loop → empty upload, so magic was never checked
        raise HTTPException(
            status_code=415,
            detail=f"{upload.filename!r}: not a PDF (empty upload)",
        )


@router.post("/ingest/upload", response_model=IngestResponse, status_code=202)
async def ingest_upload(
    http_request: Request,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(..., description="One or more PDF files."),
    collection: str | None = Form(default=None),
    tenant: str = Depends(resolve_tenant),
    principal: Principal = Depends(resolve_principal),
    ingestor: ShardedIngestor = Depends(get_ingestor),
    job_store: JobStore = Depends(get_job_store),
    collections: CollectionRegistry = Depends(get_collections),
) -> IngestResponse:
    """Upload one or more PDF files and ingest them in the background.

    Multipart counterpart to ``POST /v1/ingest`` (which takes a server-side path):
    each file is staged under ``{INGEST_ROOT}/uploads/{tenant}/{job_id}/`` with a
    sanitized, traversal-confined filename, then the SAME sharded ingest path runs
    over that per-job directory. Returns 202 with a real job_id; poll
    ``GET /v1/ingest/{job_id}`` for progress exactly as for a path ingest.

    Rejections: non-PDF (content-type or magic) → 415, a file over
    ``max_document_bytes`` → 413, more than ``max_upload_files`` files → 413, and
    — like ``POST /v1/ingest`` — 503 when no INGEST_ROOT is configured (uploads
    have nowhere confined to land) and 501 for a non-local ingest backend.
    """
    if settings.ingest_backend != "local":
        raise HTTPException(
            status_code=501,
            detail=(
                f"file upload is not supported with ingest_backend="
                f"{settings.ingest_backend!r}; use ingest_backend=local"
            ),
        )
    if not settings.ingest_root.strip():
        raise HTTPException(
            status_code=503,
            detail=(
                "ingest is disabled: INGEST_ROOT is not configured; set it to the "
                "directory uploads should be staged under"
            ),
        )
    if len(files) > settings.max_upload_files:
        raise HTTPException(
            status_code=413,
            detail=(
                f"too many files: {len(files)} > max_upload_files "
                f"({settings.max_upload_files})"
            ),
        )

    target, run_ingestor = await _resolve_ingest_target(
        collection, principal, collections, http_request.app.state, ingestor
    )

    job = await job_store.create(source="upload")
    # Staging dir is server-side root + tenant + the freshly minted job_id — none
    # client-controlled today — and each file dest is re-confined under it. But
    # confine the dir itself too: tenant is not validated (config.py accepts any
    # string), and under token auth it will derive from the credential, so a value
    # with path separators must not relocate the tree outside {ingest_root}/uploads.
    uploads_root = Path(settings.ingest_root) / "uploads"
    staging_dir = uploads_root / tenant / job.job_id
    try:
        confine_to_root(str(staging_dir), uploads_root)
    except LoaderError:
        raise HTTPException(status_code=400, detail="invalid tenant for staging") from None
    staging_dir.mkdir(parents=True, exist_ok=True)
    try:
        for idx, upload in enumerate(files):
            safe_name = _safe_pdf_name(upload.filename, fallback=f"upload_{idx}.pdf")
            dest = staging_dir / safe_name
            try:
                dest = confine_to_root(str(dest), staging_dir)
            except LoaderError:
                raise HTTPException(
                    status_code=400,
                    detail=f"{upload.filename!r}: resolves outside the staging directory",
                ) from None
            await _stage_upload(upload, dest, settings.max_document_bytes)
    except HTTPException:
        # Reject cleanly: drop the partial staging dir and mark the job failed so a
        # poll reflects reality, then re-raise the original 4xx to the client.
        shutil.rmtree(staging_dir, ignore_errors=True)
        await job_store.update(job.job_id, status=FAILED, error="rejected")
        raise

    background_tasks.add_task(
        _run_ingest,
        job_store,
        run_ingestor,
        settings.ingest_root,
        job.job_id,
        str(staging_dir),
        tenant,
        target,
    )
    return IngestResponse(job_id=job.job_id, status=job.status)


@router.get("/ingest/{job_id}", response_model=IngestResponse)
async def ingest_status(
    job_id: str,
    job_store: JobStore = Depends(get_job_store),
) -> IngestResponse:
    """Poll ingestion job status: accepted → running → completed | failed.

    `items` reports per-document progress (total/completed/failed/pending) for
    batch runs. An unrecognized job_id reports status "unknown" (200) rather than
    404, so polling is idempotent and matches the response contract.
    """
    job = await job_store.get(job_id)
    if job is None:
        return IngestResponse(job_id=job_id, status=UNKNOWN)
    counts = await job_store.item_counts(job_id)
    total = sum(counts.values())
    items = (
        IngestItemCounts(
            total=total,
            completed=counts[COMPLETED],
            failed=counts[FAILED],
            pending=counts[PENDING],
        )
        if total
        else None
    )
    return IngestResponse(
        job_id=job.job_id, status=job.status, chunk_ids=job.chunk_ids, items=items
    )


@router.get("/documents", response_model=list[DocumentInfo])
async def list_documents(
    response: Response,
    limit: int = Query(default=100, ge=1, le=1000),
    cursor: str | None = Query(
        default=None,
        description="Opaque pagination token from a prior response's "
        "X-Next-Cursor header; omit for the first page.",
    ),
    tenant: str = Depends(resolve_tenant),
    principal: Principal = Depends(resolve_principal),
    registry: CollectionRegistry = Depends(get_collections),
) -> list[DocumentInfo]:
    """List indexed documents visible to the caller (own tenant + ``public``).

    Aggregated from the served text index by ``doc_id`` — **not** the API job
    registry, which CLI-built (bulk-ingested) corpora bypass. Paginated: pass
    ``?limit=`` and the opaque ``?cursor=`` echoed from the prior response's
    ``X-Next-Cursor`` header; the header is absent on the last page. Empty when
    nothing visible is indexed. ``metadata`` carries the document-level fields
    (title, doc_type, doi, …) plus ``chunk_count``.

    Listing targets the default collection's text index today (there is no
    per-collection document endpoint yet), so the ownership seam runs against the
    default collection: a caller who may not READ it gets the same 404 as an
    unknown collection.
    """
    # Authorize AND read the same entry. These used to be the same store by
    # construction (the pointer was always the settings-derived entry, and
    # `get_text_index` returns that entry's index). Now that the pointer is
    # configurable they can differ — and reading `app.state`'s index after
    # authorizing against the pointer target served one collection's documents
    # under another collection's ACL.
    default = registry.resolve(registry.default_id)
    await enforce_access(principal, default.id, "read")
    try:
        docs, next_cursor = await default.text_index.list_documents(
            readable_tenants(tenant), limit=limit, cursor=cursor
        )
    except ValueError as e:
        # Malformed cursor. Keep the client-facing detail generic — don't reflect
        # the attacker-supplied cursor into the response body; the specifics
        # (repr-escaped, so log-injection-safe) go to the log only.
        log.info("list_documents rejected a malformed cursor: %s", e)
        raise HTTPException(status_code=400, detail="malformed pagination cursor") from e
    except Exception:
        # Degrade to empty on a backend fault (ES unreachable / index missing),
        # matching the graceful degradation of the other tenant-scoped read probes
        # (graph/stats, stats/stores) rather than surfacing a 500.
        log.warning(
            "list_documents: backend listing failed; degrading to empty", exc_info=True
        )
        return []
    if next_cursor:
        response.headers["X-Next-Cursor"] = next_cursor
    return [
        DocumentInfo(
            doc_id=d.doc_id,
            source=d.source,
            metadata={**d.metadata, "chunk_count": d.chunk_count},
        )
        for d in docs
    ]


@router.delete("/documents/{doc_id}", status_code=204)
async def delete_document(
    doc_id: str,
    tenant: str = Depends(resolve_tenant),
    principal: Principal = Depends(resolve_principal),
    registry: CollectionRegistry = Depends(get_collections),
) -> None:
    """Delete a document and its chunks — scoped to the caller's tenant, so one
    tenant cannot delete another's document even by id. Purge both retrieval
    legs (vector + text) so a deleted doc can't resurface via BM25.

    The route targets the collection ``default`` points at. READ suffices only on
    the LEGACY SHARED SURFACE — there per-chunk ``tenant_id`` stamping is the
    isolation and the delete can only ever remove the caller's own chunks, so a
    write gate would lock every non-admin out of the shared corpus. Anywhere else
    this is a mutation of somebody's owned collection and needs write; the
    exemption must not follow the pointer (see
    :attr:`CollectionEntry.is_shared_surface`). Stores are taken from the same
    entry that was authorized, never from ``app.state``."""
    default = registry.resolve(registry.default_id)
    await enforce_access(
        principal, default.id, "read" if default.is_shared_surface else "write"
    )
    await default.vector_store.delete(doc_id, tenant_id=tenant)
    await default.text_index.delete(doc_id, tenant_id=tenant)
