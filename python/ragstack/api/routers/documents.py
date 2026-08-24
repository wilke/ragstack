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
    bound_json_body,
    build_ingestor_for,
    check_ingest_build_spec,
    get_collection_store,
    get_collections,
    get_ingest_backend,
    get_ingestor,
    get_job_store,
    get_workspace,
    rate_limited,
)
from ragstack.api.security import ROLE_ADMIN, Principal, resolve_principal, resolve_tenant
from ragstack.collection_store import CollectionRecord, CollectionStore
from ragstack.config import settings
from ragstack.ingestion.gowe_backend import GoWeBackend, GoWeError
from ragstack.ingestion.loaders import DEFAULT_INGEST_SUFFIXES, LoaderError, confine_to_root
from ragstack.ingestion.manifest import WorkItem, build_manifest
from ragstack.ingestion.sharded import ShardedIngestor
from ragstack.jobstore import COMPLETED, FAILED, PENDING, RUNNING, UNKNOWN, JobStore
from ragstack.tenancy import allowed_collection_ids, readable_tenants
from ragstack.workspace import (
    WorkspaceAuthError,
    WorkspaceClient,
    WorkspaceError,
    WorkspaceTooLarge,
    collection_folder,
    ws_path,
    ws_uri,
)

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


async def _authorize_ingest_target(
    collection_id: str | None,
    principal: Principal,
    collections: CollectionRegistry,
) -> CollectionEntry | None:
    """Resolve + authorize an optional target collection; ``None`` means the
    legacy shared-surface default (the prebuilt app ingestor's stores).

    The authorization half of :func:`_resolve_ingest_target` — the tenant
    allowlist check, the OWNERSHIP check, and the build-spec guard — split out so
    the GoWe path (which builds no in-process ingestor: the workflow does the
    loading) runs exactly the same gates as the local one. Omitting the
    collection (or naming the default id) resolves the default pointer. An id
    the tenant may not access, or an unknown id, is a 404 (never a silent write
    elsewhere). A non-default collection the caller can read but does not OWN
    is a 403 (:func:`enforce_access`, write action): ingest there is
    owner-or-admin (ADR-0003; write shares deferred); one it cannot even read is
    the same 404 as an unknown id (no existence oracle).

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
        # (The prebuilt-ingestor / build_ingestor_for split happens in
        # _resolve_ingest_target; this function only decides WHICH entry.)
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
            return None
        # The pointer names some OTHER collection. `prebuilt` writes to the
        # settings-derived stores, so handing it out here would authorize against
        # one collection and land the bytes in another — the #275 shape inverted.
        return default
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
    return target


async def _resolve_ingest_target(
    collection_id: str | None,
    principal: Principal,
    collections: CollectionRegistry,
    app_state: Any,
    prebuilt: ShardedIngestor,
) -> tuple[CollectionEntry | None, ShardedIngestor]:
    """Resolve an optional target collection to ``(entry, ingestor)`` for the
    LOCAL path: :func:`_authorize_ingest_target`, then the prebuilt app ingestor
    for the shared-surface default or a per-collection ingestor bound to the
    entry's embedder/chunker/stores (so vectors match its model and land in its
    index). Shared by both local entry points so the routing cannot drift."""
    target = await _authorize_ingest_target(collection_id, principal, collections)
    if target is None:
        return None, prebuilt
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


# --------------------------------------------------------------------------- #
# ingest_backend=gowe: submit as the user, Workspace-only source (#203/#353)
# --------------------------------------------------------------------------- #
#
# The API stays thin and never embeds:
#
#   validate → resolve collection (registry row → spec_hash) → [upload: write the
#   PDFs into the caller's Workspace with the caller's token] → reserve the next
#   archive version on the registry → submit the scatter workflow with ws:// File
#   inputs, the caller's token in Authorization, output_destination = the
#   collection's versions/ folder → return job_id; a background task waits for
#   the receipts and checkpoints them + the archive location on the job.
#
# The engine pre-stages ws:// inputs and post-stages the `archive` Directory to
# output_destination as the submitter — no staging directory on this host, no
# task ever sees the token. The token lives only in this request and the
# background task's closure; it is never on the job, in a log line, or in an
# exception (GoWeClient / WorkspaceClient scrub and `raise … from None`).

_GOWE = "gowe"
_LOCAL = "local"


def _ingest_backend_name() -> str:
    return (settings.ingest_backend or _LOCAL).strip().lower()


def _refuse_unknown_backend() -> None:
    """501 for any ingest backend that is neither ``local`` nor ``gowe`` — the
    guard from before #203, narrowed to backends this router cannot drive."""
    backend = _ingest_backend_name()
    if backend not in (_LOCAL, _GOWE):
        raise HTTPException(
            status_code=501,
            detail=(
                f"ingest is not supported with ingest_backend="
                f"{settings.ingest_backend!r}; use ingest_backend=local or gowe"
            ),
        )


def _gowe_caller(principal: Principal) -> tuple[str, str]:
    """``(token, workspace subject)`` for a submission made AS the caller, or 401.

    The engine authenticates the submission with a BV-BRC user token and
    requires ``output_destination`` under that user's Workspace home; there is
    no fallback identity (and none may be added). So an API-key / keyless
    principal, or a bearer identity from another issuer, cannot use this path.
    """
    if not principal.token or principal.issuer != "bvbrc" or not principal.subject:
        raise HTTPException(
            status_code=401,
            detail=(
                "ingest_backend=gowe submits the job as the caller and needs a "
                "BV-BRC user token in the Authorization header; an API key or a "
                "non-BV-BRC identity cannot submit"
            ),
        )
    return principal.token, principal.subject


def _workspace_reference(source: str) -> str:
    """Normalise a Workspace reference (``ws:///u/home/…`` or ``/u/home/…``) to
    the ``ws://`` URI the engine stages from; anything else is a 400.

    A bare server path must never reach the submission: ``GoWeBackend`` would
    absolutise it to ``file://`` and hand a server-visible path to the engine's
    staging — the Workspace is the only ingest source on this backend (#353).
    """
    s = source.strip()
    if s.startswith("ws://") or s.startswith("/"):
        try:
            path = ws_path(s)
        except WorkspaceError:
            path = ""
        parts = path.strip("/").split("/")
        if len(parts) >= 3 and parts[0] and parts[1] == "home":
            return ws_uri(path)
    raise HTTPException(
        status_code=400,
        detail=(
            "with ingest_backend=gowe, source must be a Workspace reference "
            "(ws:///<user>/home/... or /<user>/home/...); server-side paths are "
            "not accepted"
        ),
    )


async def _registry_row(
    entry: CollectionEntry, collection_store: CollectionStore
) -> CollectionRecord:
    """The durable registry row behind ``entry`` (its ``spec_hash`` goes on the
    Workspace folder and into the archive manifest; its version counter names
    ``versions/<n>/``). The settings-derived ``default`` entry has no row —
    a deliberate divergence from the local path: a GoWe ingest needs a
    registered collection."""
    record = await collection_store.get(entry.id)
    if record is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"collection {entry.id!r} is not a registered collection; "
                f"ingest_backend=gowe archives into a registered collection's "
                f"Workspace folder — pass collection=<id> (see POST /v1/collections)"
            ),
        )
    return record


async def _reserve_version(entry: CollectionEntry, collection_store: CollectionStore) -> int:
    """Next archive version for the collection (registry-tracked; starts at 1)."""
    try:
        return await collection_store.next_version(entry.id)
    except KeyError:
        raise HTTPException(
            status_code=400, detail=f"collection {entry.id!r} is not a registered collection"
        ) from None
    except NotImplementedError as e:
        raise HTTPException(status_code=503, detail=str(e)) from None


def _gowe_inputs(
    entry: CollectionEntry, record: CollectionRecord, version: int, job_id: str, tenant: str
) -> dict[str, Any]:
    """Per-job workflow inputs (cwl/pdf-ingest-scatter.cwl): the archive's
    identity (``version``/``collection_id``/``spec_hash``/``job_id``) and the
    target collection's physical stores + build spec, so the run writes where
    the API serves and chunks the way the collection was built (ADR-0002).
    Everything else (embedding endpoints, store URLs, …) comes from
    ``gowe_workflow_inputs_json``; these override it."""
    inputs: dict[str, Any] = {
        "version": str(version),
        "collection_id": entry.id,
        "spec_hash": record.spec_hash,
        "job_id": job_id,
        "tenant": tenant,
        "collection": entry.collection,
        "es_index": entry.es_index(),
    }
    if entry.model:
        inputs["embedding_model"] = entry.model
    if entry.embedding_endpoints:
        inputs["embedding_url"] = list(entry.embedding_endpoints)
    if entry.chunk_method:
        inputs["chunk_method"] = entry.chunk_method
    if entry.chunk_size is not None:
        inputs["chunk_size"] = entry.chunk_size
    if entry.chunk_overlap is not None:
        inputs["chunk_overlap"] = entry.chunk_overlap
    return inputs


def _gowe_backend(http_request: Request) -> GoWeBackend:
    backend = get_ingest_backend(http_request)
    if not isinstance(backend, GoWeBackend):
        raise HTTPException(
            status_code=503,
            detail="ingest_backend=gowe but no GoWe backend is configured on this server",
        )
    return backend


async def _run_gowe_ingest(
    job_store: JobStore,
    backend: GoWeBackend,
    job_id: str,
    items: list[WorkItem],
    inputs: dict[str, Any],
    token: str,
    output_destination: str,
    target: CollectionEntry,
    source: str,
) -> None:
    """Background worker for the GoWe path: submit as the user, wait, checkpoint
    one result per item, record the archive location. Never raises.

    Bypasses ``ShardedIngestor`` on purpose: ``GoWeBackend`` ignores the
    per-shard callable (the engine runs the tools), so nothing in that path
    would ever checkpoint the receipts — every successful run would read
    ``failed`` from its all-pending item counts. A :class:`GoWeContractError`
    (COMPLETED but no receipts) fails the JOB with its class name as the label —
    visible, never "every document failed" (#203 blocker c). The token reaches
    only the engine requests; it is not on the job and not in these log lines.
    """
    await job_store.update(job_id, status=RUNNING)
    await job_store.add_items(job_id, [(i.item_id, i.source) for i in items])
    try:
        run = await backend.run_submission(
            items, inputs=inputs, token=token, output_destination=output_destination
        )
    except GoWeError as e:
        # Message is engine-facing (scrubbed of tokens by GoWeClient); the job
        # carries only the caller-safe class-name label.
        log.error("ingest job %s: gowe submission failed: %s", job_id, e)
        await job_store.update(job_id, status=FAILED, error=type(e).__name__)
        return
    except Exception as e:
        log.warning("ingest job %s failed: %s", job_id, type(e).__name__)
        await job_store.update(job_id, status=FAILED, error=type(e).__name__)
        return

    for r in run.results:
        await job_store.mark_item(
            job_id, r.item_id, status=r.status, chunk_ids=r.chunk_ids, error=r.error
        )
    counts = await job_store.item_counts(job_id)
    final = _final_status(counts)
    fields: dict[str, object] = {"status": final}
    if run.archive_ref:
        fields["archive_ref"] = run.archive_ref
    if len(run.results) == 1 and run.results[0].status == COMPLETED:
        fields["chunk_ids"] = run.results[0].chunk_ids
    await job_store.update(job_id, **fields)
    if final == COMPLETED:
        from ragstack.api.deps import write_ingest_manifest_for

        chunks = sum(len(r.chunk_ids or []) for r in run.results)
        write_ingest_manifest_for(target, source=source, chunk_count=chunks or None)


async def _gowe_upload_sources(
    workspace: WorkspaceClient,
    token: str,
    subject: str,
    entry: CollectionEntry,
    record: CollectionRecord,
    tenant: str,
    files: list[UploadFile],
) -> list[WorkItem]:
    """Write the uploaded PDFs into ``<collection folder>/sources/`` with the
    caller's token; return one work item per file (``ws://`` source).

    ``UploadFile.size`` (when the multipart parser knows it) goes to
    ``upload_source`` so an oversize file is refused before any RPC and the Shock
    PUT carries a Content-Length; the byte cap is enforced while streaming
    either way. Refusals: over ``max_document_bytes`` → 413, a rejected token →
    401, any other Workspace failure → 502. A same-named file already in
    ``sources/`` is refused by the Workspace (never overwritten) → 409; see the
    #202 hardening spec for the per-request bounds still to come.
    """
    folder = await workspace.ensure_collection_folder(
        token, subject, entry.id, spec_hash=record.spec_hash, tenant=tenant
    )
    sources = f"{ws_path(folder)}/sources"
    items: list[WorkItem] = []
    for idx, upload in enumerate(files):
        safe_name = _safe_pdf_name(upload.filename, fallback=f"upload_{idx}.pdf")
        try:
            uri = await workspace.upload_source(
                token, sources, safe_name, upload,
                max_bytes=settings.max_document_bytes, size=upload.size,
            )
        except WorkspaceTooLarge:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"{upload.filename!r}: exceeds max_document_bytes "
                    f"({settings.max_document_bytes} bytes)"
                ),
            ) from None
        except WorkspaceAuthError:
            raise HTTPException(
                status_code=401, detail="the Workspace rejected the caller's token"
            ) from None
        except WorkspaceError as e:
            status = 409 if "already exists" in str(e).lower() else 502
            raise HTTPException(
                status_code=status, detail=f"Workspace write of {safe_name!r} failed: {e}"
            ) from None
        items.append(WorkItem(item_id=ws_path(uri), source=uri))
    return items


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


@router.post(
    "/ingest",
    response_model=IngestResponse,
    dependencies=[Depends(bound_json_body), Depends(rate_limited("ingest"))],
)
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

    Under ``ingest_backend=gowe`` (#203/#353) ``source`` is a Workspace reference
    (``ws:///<user>/home/…`` or ``/<user>/home/…``; anything else is 400) and the
    job is submitted to the GoWe engine AS THE CALLER — a bearer BV-BRC identity
    is required (401 otherwise) and the target must be a registered collection.
    """
    _refuse_unknown_backend()
    if _ingest_backend_name() == _GOWE:
        # Submit as the caller with a Workspace reference (#203/#353). No
        # INGEST_ROOT gate: nothing on this host is read — the engine pre-stages
        # the ws:// source with the caller's token.
        token, subject = _gowe_caller(principal)
        uri = _workspace_reference(request.source)
        entry = await _authorize_ingest_target(request.collection, principal, collections)
        if entry is None:
            entry = collections.resolve(collections.default_id)
        collection_store = get_collection_store(http_request)
        record = await _registry_row(entry, collection_store)
        backend = _gowe_backend(http_request)
        # Every refusal precedes the job: a version-reservation failure (json
        # registry → 503) must not leave an `accepted` job nobody will run.
        version = await _reserve_version(entry, collection_store)
        job = await job_store.create(source=request.source, tenant_id=principal.tenant)
        background_tasks.add_task(
            _run_gowe_ingest,
            job_store,
            backend,
            job.job_id,
            [WorkItem(item_id=ws_path(uri), source=uri)],
            _gowe_inputs(entry, record, version, job.job_id, tenant),
            token,
            f"{ws_uri(collection_folder(subject, entry.id))}/versions/",
            entry,
            request.source,
        )
        return IngestResponse(job_id=job.job_id, status=job.status)
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

    job = await job_store.create(source=request.source, tenant_id=principal.tenant)
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


@router.post(
    "/ingest/upload",
    response_model=IngestResponse,
    status_code=202,
    dependencies=[Depends(rate_limited("ingest"))],
)
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
    have nowhere confined to land) and 501 for an ingest backend that is
    neither ``local`` nor ``gowe``.

    Under ``ingest_backend=gowe`` (#203/#353) the files are written into the
    caller's BV-BRC Workspace (``.ragstack/collections/<id>/sources/``) with the
    caller's own token and the scatter workflow is submitted as the caller with
    ``ws://`` inputs — the same submission path as a Workspace-reference ingest.
    Needs a bearer BV-BRC identity (401 otherwise) and a registered collection.
    """
    _refuse_unknown_backend()
    if len(files) > settings.max_upload_files:
        raise HTTPException(
            status_code=413,
            detail=(
                f"too many files: {len(files)} > max_upload_files "
                f"({settings.max_upload_files})"
            ),
        )
    if _ingest_backend_name() == _GOWE:
        # Browser upload → the caller's Workspace → submit as the caller (#203/
        # #353). One code path with the Workspace-reference ingest above; no
        # staging directory on this host, so no INGEST_ROOT gate here.
        token, subject = _gowe_caller(principal)
        for upload in files:
            if (upload.content_type or "").split(";", 1)[0].strip() != "application/pdf":
                raise HTTPException(
                    status_code=415,
                    detail=f"{upload.filename!r}: only application/pdf uploads are accepted",
                )
        entry = await _authorize_ingest_target(collection, principal, collections)
        if entry is None:
            entry = collections.resolve(collections.default_id)
        collection_store = get_collection_store(http_request)
        record = await _registry_row(entry, collection_store)
        backend = _gowe_backend(http_request)
        workspace = get_workspace(http_request)
        job = await job_store.create(source="upload", tenant_id=principal.tenant)
        try:
            items = await _gowe_upload_sources(
                workspace, token, subject, entry, record, tenant, files
            )
            version = await _reserve_version(entry, collection_store)
        except HTTPException:
            await job_store.update(job.job_id, status=FAILED, error="rejected")
            raise
        background_tasks.add_task(
            _run_gowe_ingest,
            job_store,
            backend,
            job.job_id,
            items,
            _gowe_inputs(entry, record, version, job.job_id, tenant),
            token,
            f"{ws_uri(collection_folder(subject, entry.id))}/versions/",
            entry,
            "upload",
        )
        return IngestResponse(job_id=job.job_id, status=job.status)
    if not settings.ingest_root.strip():
        raise HTTPException(
            status_code=503,
            detail=(
                "ingest is disabled: INGEST_ROOT is not configured; set it to the "
                "directory uploads should be staged under"
            ),
        )

    target, run_ingestor = await _resolve_ingest_target(
        collection, principal, collections, http_request.app.state, ingestor
    )

    job = await job_store.create(source="upload", tenant_id=principal.tenant)
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
    principal: Principal = Depends(resolve_principal),
    job_store: JobStore = Depends(get_job_store),
) -> IngestResponse:
    """Poll ingestion job status: accepted → running → completed | failed.

    `items` reports per-document progress (total/completed/failed/pending) for
    batch runs. An unrecognized job_id reports status "unknown" (200) rather than
    404, so polling is idempotent and matches the response contract.

    Tenant-scoped (#130): a job stamped for another tenant resolves exactly like
    an unrecognized one — status "unknown", same 200 shape — so this endpoint
    never confirms that a foreign job_id exists (IDOR). An admin principal
    bypasses the scope (ADR-0003 §5, logged in ``ragstack.jobstore``) and always
    sees the real status, including for a legacy job written before jobs carried
    a tenant stamp.
    """
    job = await job_store.get(
        job_id, tenant_id=principal.tenant, is_admin=principal.role == ROLE_ADMIN
    )
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
    limit: int = Query(default=100, ge=1, le=settings.max_list_limit),
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
