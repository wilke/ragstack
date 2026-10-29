"""Document management endpoints."""
from __future__ import annotations

import hashlib
import logging
import shutil
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Literal

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

from ragstack.api.access import enforce_access, is_user_created
from ragstack.api.collections import (
    CollectionEntry,
    CollectionRegistry,
    is_reserved_collection_id,
)
from ragstack.api.default_collection import (
    resolve_default_entry,
    resolve_ingest_default_entry,
)
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
    single_inflight_ingest,
)
from ragstack.api.job_lifecycle import job_lifecycle
from ragstack.api.scope import count_scope
from ragstack.api.security import (
    ROLE_ADMIN,
    Principal,
    gowe_caller,
    resolve_principal,
    resolve_tenant,
)
from ragstack.collection_store import CollectionRecord, CollectionStore
from ragstack.config import settings
from ragstack.ingestion.backends import ingest_backend_name
from ragstack.ingestion.chunk_cap import (
    CHUNK_CAP_EXCEEDED,
    effective_chunk_cap,
    is_cap_refusal,
)
from ragstack.ingestion.chunker_config import (
    ModelWindowUnavailable,
    parse_unsupported_methods,
    shard_refusal,
)
from ragstack.ingestion.enrich import resolve_profile
from ragstack.ingestion.glossary_file import GLOSSARY_FILE_MISMATCH, is_glossary_refusal
from ragstack.ingestion.glossary_inputs import glossary_inputs, in_process_glossary_mode
from ragstack.ingestion.gowe_backend import (
    OUTPUT_STAGING_FAILED,
    GoWeBackend,
    GoWeError,
    OutputStagingFailed,
)
from ragstack.ingestion.loaders import (
    DEFAULT_INGEST_SUFFIXES,
    NO_LOADER_LABEL,
    LoaderError,
    confine_to_root,
    deterministic_doc_id,
    no_loader_error,
)
from ragstack.ingestion.manifest import Manifest, WorkItem, build_manifest
from ragstack.ingestion.sharded import ShardedIngestor
from ragstack.ingestion.structure import (
    StructureBackendUnavailable,
    merge_enrichment,
    require_structure_backends,
    structure_metadata_enabled,
)
from ragstack.ingestion.tokenization import TokenCounterUnavailable
from ragstack.ingestion.work_identity import (
    DUPLICATE_WORK,
    POLICY_OFF,
    POLICY_REFUSE,
    DuplicateCheck,
    ids_from_filename,
    work_keys,
)
from ragstack.jobstore import COMPLETED, FAILED, PENDING, RUNNING, UNKNOWN, JobStore
from ragstack.store_routing import es_url_for, qdrant_url_for
from ragstack.tenancy import allowed_collection_ids, readable_tenants
from ragstack.tool_image import declared_workflow_inputs, names_stamped_image
from ragstack.workspace import (
    WorkspaceAuthError,
    WorkspaceClient,
    WorkspaceError,
    WorkspaceExists,
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
    target: CollectionEntry,
    every_file: bool = False,
    collection_store: CollectionStore | None = None,
    duplicate_policy: str = POLICY_OFF,
    duplicate_scope: list[str] | None = None,
) -> None:
    """Background worker: expand the source into a manifest and run it.

    A single file is a 1-item manifest, so files and directories share one path.
    Per-item progress is checkpointed by the ingestor; here we set the overall
    job status. Never raises — a run-level failure is captured as a caller-safe
    label. The job is ``failed`` only when the run itself errors or *every* item
    fails; partial failures leave it ``completed`` with non-zero ``items.failed``.

    ``every_file`` (the upload staging dir, #202): enumerate EVERY staged file,
    not just the suffixes the loaders handle, and fail the rest visibly — one
    item each, error ``no loader for <suffix>`` — instead of letting the
    manifest filter drop them into a ``completed`` job with fewer items than
    files. A directory ingest (``POST /v1/ingest``) keeps the filter: a corpus
    tree legitimately holds files that are not documents.

    The per-collection chunk cap (#291) is resolved here, once per job
    (:func:`_chunk_cap_for`: the registry override, else the deployment default
    for a user-created collection) and enforced inside ``ingest_manifest`` —
    one live count, before the first write. A refused job is ``failed`` with
    the ``chunk_cap_exceeded`` label; its items carry the four numbers.

    ``duplicate_policy`` (#747): the duplicate-paper guard each item runs (the
    authoritative, post-enrichment check). A refused item is ``failed`` with
    ``duplicate_work: key=… existing_doc_id=…`` as its error; the job status
    follows :func:`_final_status` like any other item failure.
    ``duplicate_scope`` is the request's lookup scope (:func:`_duplicate_scope`).
    """
    await job_store.update(job_id, status=RUNNING)
    try:
        manifest = build_manifest(
            source,
            suffixes=None if every_file else DEFAULT_INGEST_SUFFIXES,
            ingest_root=ingest_root or None,
        )
        if every_file:
            manifest = await _fail_unsupported_items(job_store, job_id, manifest)
        cap = (
            await _chunk_cap_for(target, collection_store)
            if collection_store is not None else None
        )
        # The keywords travel only when they apply, so an uncapped, unguarded
        # run is the call it always was (duck-typed ingestors included).
        extra: dict[str, Any] = {}
        if cap is not None:
            extra["chunk_cap"] = cap
        if duplicate_policy != POLICY_OFF:
            # One check per job: its `seen` keys are shared by every item.
            extra["duplicate_policy"] = DuplicateCheck(
                policy=duplicate_policy, tenants=duplicate_scope
            )
        # #754: snapshot the glossary step's counters so the manifest records
        # what THIS job's pipeline did (duck-typed ingestors have none).
        pipeline = getattr(ingestor, "_pipeline", None)
        glossary_before = Counter(getattr(pipeline, "glossary_counts", None) or {})
        structure_before = Counter(getattr(pipeline, "structure_counts", None) or {})
        results = await ingestor.ingest_manifest(
            manifest, job_id=job_id, tenant_id=tenant_id, **extra
        )
    except Exception as e:
        log.warning("ingest job %s failed: %s", job_id, e)
        # Caller-safe label: the class name, unless the error type carries an
        # actionable constant (ChunkCapExceeded.job_error, the item convention).
        await job_store.update(
            job_id, status=FAILED, error=getattr(e, "job_error", type(e).__name__)
        )
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
        # The step record comes from the pipeline that ran, never from settings:
        # a degraded or unconfigured step is not claimed (#754). Shared by
        # concurrent jobs on one pipeline, so the delta is approximate there.
        provenance_of = getattr(pipeline, "glossary_provenance", None)
        enrichment = provenance_of(glossary_before) if callable(provenance_of) else None
        # #828: the structure step's record beside the glossary's, same rule.
        structure_of = getattr(pipeline, "structure_provenance", None)
        enrichment = merge_enrichment(
            enrichment, structure_of(structure_before) if callable(structure_of) else None
        )
        # Keyed on the SURFACE FLAG, not on `target is None` (#422 passes the
        # surface entry through now). The surface keeps the app-level manifest
        # it has always written — per-collection manifests/versions are for
        # collections this API created, and starting to write them for the
        # settings-derived corpus would be a new behaviour, not a cleanup.
        # NOTE the GoWe path (_run_gowe_ingest) deliberately differs: it writes
        # a per-collection manifest and appends a version for EVERY entry,
        # surface included, and has always done so. Do not "align" it here.
        if not target.is_shared_surface:
            from ragstack.api.deps import write_ingest_manifest_for

            write_ingest_manifest_for(
                target, source=source, chunk_count=chunks or None, enrichment=enrichment
            )
        else:
            from ragstack.api.deps import write_ingest_manifest

            write_ingest_manifest(
                source=source, chunk_count=chunks or None, enrichment=enrichment
            )


async def _fail_unsupported_items(
    job_store: JobStore, job_id: str, manifest: Manifest
) -> Manifest:
    """Split off the items whose suffix has no loader (not in
    ``DEFAULT_INGEST_SUFFIXES``), record each as a failed item with the
    constant ``no_loader_error`` string, and return the manifest of the rest.
    Logged at INFO with the ``no_loader`` label, like the scanned-PDF count."""
    supported = {s.lower() for s in DEFAULT_INGEST_SUFFIXES}
    keep: list[WorkItem] = []
    dropped: list[WorkItem] = []
    for item in manifest.items:
        (keep if Path(item.source).suffix.lower() in supported else dropped).append(item)
    if dropped:
        await job_store.add_items(job_id, [(i.item_id, i.source) for i in dropped])
        for item in dropped:
            await job_store.mark_item(
                job_id, item.item_id, status=FAILED,
                error=no_loader_error(Path(item.source).suffix),
            )
        log.info(
            "ingest job %s: %d of %d file(s) have no loader for their suffix [%s]",
            job_id, len(dropped), len(manifest.items), NO_LOADER_LABEL,
        )
    return Manifest(items=keep)


async def _chunk_cap_for(
    target: CollectionEntry,
    collection_store: CollectionStore,
    record: CollectionRecord | None = None,
) -> int | None:
    """The chunk cap (#291) for one ingest job into ``target``, or ``None``.

    Resolved ONCE per job, before it runs: the registry entry's ``max_chunks``
    override wins (``0`` exempts); otherwise ``max_chunks_per_collection``
    applies iff the collection is user-created (:func:`is_user_created`: an
    owner row whose owner is not an admin). The legacy shared surface is a
    curated corpus and is never capped — keyed on the flag, since #422 passes
    that entry through rather than ``None``. ``record``
    (the row a gowe caller already fetched) saves the registry read. A registry
    that cannot answer is treated as "no override" — the derivation still
    applies, so an outage can never lift a cap."""
    if target.is_shared_surface:
        return None
    if record is None:
        try:
            record = await collection_store.get(target.id)
        except Exception:  # noqa: BLE001 — no override readable; derive instead
            log.warning("chunk cap: registry row for %r unreadable; deriving the cap",
                        target.id, exc_info=True)
            record = None
    override = record.spec.max_chunks if record is not None else None
    # The owner/role lookups run only when the override does not decide.
    user_created = override is None and await is_user_created(target.id)
    return effective_chunk_cap(
        override=override, user_created=user_created,
        default_cap=settings.max_chunks_per_collection,
    )


async def _authorize_ingest_target(
    collection_id: str | None,
    principal: Principal,
    collections: CollectionRegistry,
) -> CollectionEntry:
    """Resolve + authorize the target collection for one ingest. Always returns
    an entry — never ``None``.

    The authorization half of :func:`_resolve_ingest_target` — the tenant
    allowlist check, the OWNERSHIP check, and the build-spec guard — split out so
    the GoWe path (which builds no in-process ingestor: the workflow does the
    loading) runs exactly the same gates as the local one.

    **IMPLICIT** (``collection`` omitted, or the reserved pointer NAME, which
    #276 makes equivalent to omitting): the target is
    :func:`resolve_ingest_default_entry` — the caller's writable default. That
    is the read-side picker narrowed to what the caller may actually write to,
    so an upload is never routed into a collection they can only read. Nothing
    writable in their listing is a 403 naming no id; nothing readable at all is
    the same 404 the read paths give. See #422.

    **EXPLICIT** (any other id, *including* one that happens to equal
    ``collections.default_id``): allowlist 404, resolve-or-404, then
    ``enforce_access``. An explicitly named id NEVER enters the picker — a
    request is never silently rerouted from the collection the caller chose, and
    an id they cannot write stays 403-if-readable / 404-if-not, exactly as
    before (no existence oracle).

    **The action on both branches is ``"read" if is_shared_surface else
    "write"``**, and that is load-bearing rather than cosmetic. The exemption
    exists for the LEGACY SHARED SURFACE: there per-chunk ``tenant_id`` stamping
    — not collection ownership — is what isolates writers, so demanding
    ownership would lock every non-admin out of the flagship shared corpus (and
    break the conformance contract: core data ops need auth, not a role). It
    keys on the SURFACE FLAG and never on "is this what ``default`` points at":
    point the pointer at a genuinely owned collection (#276) and a
    pointer-keyed exemption would let any reader of it ingest into somebody
    else's corpus just by omitting ``collection``.

    Two consequences of applying it on the EXPLICIT branch too, both deliberate:

    * it is what stops this split from breaking our own UI. Post-#420
      ``frontend/src/lib/collectionTarget.ts`` resolves the listing's advertised
      ``default`` and sends it as an **explicit** id, so the shared surface now
      arrives here by name. The surface is public-read backfilled, so plain
      ``"write"`` would deny a caller who *can* read it — a **403** on the
      flagship corpus, to our own uploader. (The same fact means the UI does not
      exercise the implicit picker at all: that path serves raw API/CLI callers
      who omit the field.)
    * it *loosens* one pre-existing case: explicitly naming the surface while
      the pointer aims elsewhere used to demand ``write`` and now does not. That
      matches the documented intent, and the write still lands tenant-stamped.

    One further behaviour change the split carries: the implicit branch used to
    skip the tenant allowlist entirely, so a tenant confined *away* from the
    default could still reach the shared surface by omitting ``collection`` or
    by naming the pointer target's id. Both now go through the allowlist —
    implicitly via ``visible_entries``, explicitly via the 404 below.

    Both branches run :func:`check_ingest_build_spec` against the collection the
    write will land in, including the shared surface: a pinned
    ``qdrant_collection_explicit`` keeps its name across a settings change, so
    that collection is precisely where a swapped embedder or chunker would
    quietly append incoherent data to a 25M-point index.
    """
    if not collection_id or is_reserved_collection_id(collection_id):
        # Naming the pointer == omitting it (#276).
        target = await resolve_ingest_default_entry(collections, principal)
    else:
        allowed = collections.permitted(
            allowed_collection_ids(principal.tenant, settings.tenant_collections)
        )
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
    # Enforcement runs after BOTH branches. On the implicit one it cannot
    # contradict the pick (resolve_write_many and resolve_access's write branch
    # are the same policy, in the same file) — what it adds is the collection
    # LIFECYCLE gate, which no filter in default_collection.py runs.
    await enforce_access(
        principal, target.id, "read" if target.is_shared_surface else "write"
    )
    return target


async def _resolve_ingest_target(
    collection_id: str | None,
    principal: Principal,
    collections: CollectionRegistry,
    app_state: Any,
    prebuilt: ShardedIngestor,
) -> tuple[CollectionEntry, ShardedIngestor]:
    """Resolve the target collection to ``(entry, ingestor)`` for the LOCAL
    path: :func:`_authorize_ingest_target`, then the prebuilt app ingestor for
    the legacy shared surface or a per-collection ingestor bound to the entry's
    embedder/chunker/stores (so vectors match its model and land in its index).
    Shared by both local entry points so the routing cannot drift.

    The entry is ALWAYS returned, the shared surface included (#422). It used to
    come back as ``None`` there, which was tolerable only while the surface and
    the pointer target were necessarily the same entry: the picker can choose the
    surface while the pointer names something else, and the callers'
    ``resolve(default_id)`` back-fill would then have named the wrong collection
    on the job row. The prebuilt ingestor still keys on the SURFACE FLAG — its
    stores are the settings-derived ones, so handing it out for any other entry
    would authorize against one collection and land the bytes in another."""
    target = await _authorize_ingest_target(collection_id, principal, collections)
    if target.is_shared_surface:
        # The derived entry IS app.state's ingestor — same stores, no build.
        _refuse_missing_structure_backend(prebuilt)
        return target, prebuilt
    try:
        run_ingestor = build_ingestor_for(app_state, target)
    except TokenCounterUnavailable as e:
        # 503, not 400: the request is well-formed and no change to the payload
        # would fix it — the tokenizer this collection's chunking depends on
        # cannot load on this server. Answering 400 would tell the caller to
        # edit something on their side, which is a lie.
        #
        # 503 rather than a bare 500 follows the two nearest precedents in this
        # file: an unset INGEST_ROOT and a missing GoWe backend are both
        # persistent, operator-only misconfigurations that leave the rest of the
        # server serving, and both answer 503 *with* an actionable detail. And
        # `main.py`'s catch-all 500 deliberately carries no detail at all ("not
        # to expose internals"), so a 500 here would drop the remediation into
        # the log and hand the caller an empty body.
        #
        # No Retry-After: this file's transient 503s carry one, these
        # misconfiguration 503s do not — the header is what separates "come back
        # in five seconds" from "an operator has to act".
        #
        # `client_detail`, not `str(e)`: the full message embeds the tokenizer
        # loader's own error text, which quotes the HF cache path on this host.
        raise HTTPException(status_code=503, detail=e.client_detail) from None
    except ModelWindowUnavailable as e:
        # 503, not 400 (#647): the per-chunk token budget comes from the
        # collection's embedding endpoint (or CHUNK_MAX_TOKENS), and neither gave
        # one — a dependency failure the payload cannot fix. Refusing beats the
        # old silent 4096, which sized the whole collection on a guess. An
        # endpoint that exists but did not answer is worth a retry, so that case
        # carries Retry-After like this file's other transient 503s; "no endpoint
        # configured" is operator-only and does not. Logged here because an
        # HTTPException writes no line of its own (docs/runbooks/tracing-a-503.md).
        # The message names the collection and the fix, never the endpoint URL.
        log.warning("ingest refused, no token window: %s", e)
        raise HTTPException(
            status_code=503, detail=str(e),
            headers={"Retry-After": "5"} if e.transient else None,
        ) from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    _refuse_missing_structure_backend(run_ingestor)
    return target, run_ingestor


def _refuse_missing_structure_backend(ingestor: Any) -> None:
    """503 when the pipeline this LOCAL ingest will run has the structure step
    on and an ingest adapter's parser is missing (#828 §6: "a missing parser is
    a refusal, never a fallback"). Read from the pipeline that runs, not from
    settings, so what is refused is exactly what would have run. Before the
    job exists, so nothing is accepted that cannot run; the same 503 shape as
    the token-counter refusal (operator-only, so no Retry-After). The GoWe
    path does not parse here: its tools image refuses at configuration."""
    pipeline = getattr(ingestor, "_pipeline", None)
    # No `_pipeline` (a duck-typed test ingestor) means no structure step to run.
    if not getattr(pipeline, "structure_metadata", False):
        return
    try:
        require_structure_backends()
    except StructureBackendUnavailable as e:
        log.warning("ingest refused: %s", e)
        raise HTTPException(status_code=503, detail=e.client_detail) from None


def _guard(entry: CollectionEntry) -> None:
    """409 when this ingest's build spec contradicts the collection's recorded one.

    409 rather than 400: the request is well-formed and would be valid against a
    collection built the same way — it is the *state* of the target that makes it
    a conflict, and the fix is a new collection, not a new payload."""
    try:
        check_ingest_build_spec(entry)
    except BuildSpecMismatch as e:
        raise HTTPException(status_code=409, detail=str(e)) from None


def _resolve_duplicate_policy(requested: str | None) -> str:
    """The duplicate guard's policy for one request (#747): what the request
    named, else the deployment default (``settings.duplicate_policy``)."""
    return requested or settings.duplicate_policy


async def _duplicate_scope(
    entry: CollectionEntry,
    collections: CollectionRegistry,
    principal: Principal,
    policy: str,
) -> list[str] | None:
    """The duplicate guard's lookup scope for one request (#747 D6), resolved
    ONCE: :func:`ragstack.api.scope.count_scope` — own + public, widened by the
    owner tenant of a collection SHARED with the caller, i.e. exactly the
    writer-tenants whose documents a query of ``entry`` would return to this
    caller. So a collaborator's copy in a shared collection is found, and only
    documents the caller can already read are ever named. ``None`` when the
    guard is off (no ACL round trip)."""
    if policy == POLICY_OFF:
        return None
    return await count_scope(entry, collections, principal)


async def _precheck_duplicate(
    entry: CollectionEntry,
    names: list[str],
    scope: list[str] | None,
    policy: str,
    *,
    same_document: set[str] | None = None,
) -> None:
    """The admission-time half of the duplicate guard (#747 D2): a 409 BEFORE a
    job exists, a version is reserved or a byte is written, when a file NAME
    carries a work id (``PMC1234567.pdf``, a publisher-profile DOI on a ``.pdf``
    name) that a document already indexed in ``entry`` carries too — or that
    another file of the SAME request carries.

    Best-effort by design — the bytes are not parsed here (an upload is bytes,
    a GoWe source is not even on this host), so a name without an id passes and
    the authoritative check runs in the job after enrichment. Only ``refuse``
    pre-checks. ``scope`` is :func:`_duplicate_scope` — never a tenant whose
    documents the caller cannot read (naming a foreign doc_id would be an
    existence oracle). ``same_document`` holds doc ids / source paths that are
    THIS source (a re-ingest of the same path is a replace, not a duplicate).
    A lookup failure admits (fail-open): the job-time check still runs.

    The 409 ``detail`` is an object, like ``owner_quota_exceeded``:
    ``{error: "duplicate_work", key, existing_doc_id, collection, message}``;
    ``existing_doc_id`` is null when the clash is between two files of the
    request.
    """
    if policy != POLICY_REFUSE or not scope:
        return
    profile = resolve_profile(settings.publisher_profile)
    per_name = [(n, work_keys(ids_from_filename(n, profile))) for n in names]

    def _conflict(key: tuple[str, str], existing: str | None, message: str) -> HTTPException:
        kind, value = key
        return HTTPException(status_code=409, detail={
            "error": DUPLICATE_WORK, "key": f"{kind}:{value}",
            "existing_doc_id": existing, "collection": entry.id, "message": message,
        })

    first_name: dict[tuple[str, str], str] = {}
    for name, name_keys in per_name:
        for key in name_keys:
            other = first_name.setdefault(key, name)
            if other != name:
                raise _conflict(key, None, (
                    f"{other!r} and {name!r} in this request are the same work "
                    f"({key[0]} {key[1]}); nothing was ingested. Send one of them, or "
                    f"resubmit with duplicate_policy=allow to keep both."
                ))
    keys = list(first_name)
    lookup = getattr(getattr(entry, "text_index", None), "find_documents_by_key", None)
    if not keys or lookup is None:
        return
    try:
        found = await lookup(keys, scope)
    except Exception as e:  # noqa: BLE001 — fail-open; the job-time check still runs
        log.warning("duplicate pre-check on %r skipped (%s): %s",
                    entry.id, type(e).__name__, e)
        return
    skip = same_document or set()
    for name, name_keys in per_name:
        for key in name_keys:
            for existing in found.get(key, []):
                if existing.doc_id in skip or (existing.source and existing.source in skip):
                    continue
                raise _conflict(key, existing.doc_id, (
                    f"{name!r} is {key[0]} {key[1]}, which is already indexed in "
                    f"collection {entry.id!r} as document {existing.doc_id}; "
                    f"nothing was ingested. Resubmit with "
                    f"duplicate_policy=allow to keep a second copy."
                ))


def _source_identity(source: str) -> set[str]:
    """Doc ids and paths that name a local ``source`` itself — what a
    re-ingest of the same path would replace (the loaders key a file's doc id
    on its resolved path)."""
    out: set[str] = set()
    for p in (Path(source), Path(settings.ingest_root or ".") / source):
        try:
            resolved = str(p.resolve())
        except (OSError, RuntimeError):
            continue
        out.update({resolved, deterministic_doc_id(resolved)})
    return out


def _is_file_name(source: str) -> bool:
    """A source whose basename has an ingestable suffix — a file the name
    pre-check applies to (a directory ingest has no single name)."""
    return Path(source).suffix.lower() in {s.lower() for s in DEFAULT_INGEST_SUFFIXES}


# --------------------------------------------------------------------------- #
# ingest_backend=gowe: submit as the user, Workspace-only source (#203/#353)
# --------------------------------------------------------------------------- #
#
# The API stays thin and never embeds:
#
#   validate → resolve collection (registry row → spec_hash) → reserve the next
#   archive version on the registry → mint the job → [upload: write the PDFs
#   into the caller's Workspace with the caller's token] → submit the scatter
#   workflow with ws:// File inputs, the caller's token in Authorization,
#   output_destination = the collection's versions/ folder → return job_id; a
#   background task waits for COMPLETED *and delivered*, reads the per-item
#   receipts from receipt.json inside the delivered archive (versions/<n>/) with
#   the caller's token, and checkpoints them + the archive location on the job.
#
# The engine pre-stages ws:// inputs and post-stages the workflow's one output —
# the `archive` Directory — to output_destination as the submitter: no staging
# directory on this host, no task ever sees the token, nothing is downloaded
# from the engine. The token lives only in this request and the background
# task's closure; it is never on the job, in a log line, or in an exception
# (GoWeClient / WorkspaceClient scrub and `raise … from None`).

_GOWE = "gowe"
_LOCAL = "local"


def _ingest_backend_name() -> str:
    return ingest_backend_name(settings)


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
    caller = gowe_caller(principal)  # the one implementation (api/security.py)
    if caller is None:
        raise HTTPException(
            status_code=401,
            detail=(
                "ingest_backend=gowe submits the job as the caller and needs a "
                "BV-BRC user token in the Authorization header; an API key or a "
                "non-BV-BRC identity cannot submit"
            ),
        )
    return caller


def _refuse_unrunnable_chunk_method(entry: CollectionEntry) -> None:
    """422 when the target collection's chunk method cannot run on this backend.

    The create-time guard in the collections router stops NEW semantic
    collections on a gowe deployment; this one is for the collections that
    already exist — the reported case was created before any guard shipped and is
    still there, so without this its owner's next upload would fail inside the
    scatter again (20 items, three attempts, a GoWe-level error) instead of being
    told at submit time why, and before anything is written.

    Placed before the version reservation and before any Workspace write, per the
    surrounding rule that an up-front refusal must not burn a version number or
    leave sources uploaded with no version to run under.

    .. rubric:: The fallback is conservative, and deliberately so

    An entry that records no ``chunk_method`` is read as
    ``settings.unrecorded_chunk_method()`` here (``CHUNK_METHOD`` when set, else
    the pre-2026-10-06 default ``fixed`` — not the ``fixed_token`` default new
    collections get), and that is a **guess about a different process**. ``_gowe_inputs``
    sends ``chunk_method`` only when the entry has one, so for a method-less
    entry the scatter supplies nothing and ``pdf-ingest-scatter.cwl``'s own
    default decides — ``fixed_token/256/32``, where this process would have said
    ``fixed/512/64``. Two builders, two defaults, no agreement; the same defect
    #609 names, visible with nothing semantic involved. (``chunk_params`` is not
    sent either, so semantic tunables would not survive the trip regardless.)

    Be clear about what this guard therefore does and does not do for such an
    entry. It refuses only when ``settings.unrecorded_chunk_method()`` is itself
    a refused method — which on a deployment whose default is supported (hackathon's, where
    ``CHUNK_METHOD`` is unset and the fallback is ``fixed``) means it does NOT
    fire, and the run proceeds chunked by the CWL default with nothing in either
    store recording which method won. That silent divergence is real and this
    guard does not close it; #609 step 2 does, by having the tool read the
    registry entry. The one case where the fallback does fire, it refuses a run
    the tool would in fact have completed — using a method the caller never
    chose. Refusing is still the better error there, but it is a judgement call
    on an already-ambiguous state, not a precise test.

    API-created collections are unaffected either way: ``create_collection``
    persists the RESOLVED method, so their ``chunk_method`` is never ``None``.
    The method-less entries are the ones registered by the bulk CLI or by hand.
    """
    method = entry.chunk_method or settings.unrecorded_chunk_method()
    refusal = shard_refusal(
        method,
        unsupported=parse_unsupported_methods(settings.ingest_worker_unsupported_methods),
    )
    if refusal is not None:
        raise HTTPException(status_code=422, detail=refusal)


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


def gowe_symbol_glossary(mode: str) -> str:
    """The ``symbol_glossary`` workflow input for ``SYMBOL_GLOSSARY_EXTRACTION``
    (#783): ``rules`` for ``rules`` and for ``llm``, else ``off`` — the
    in-process reading (``in_process_glossary_mode`` maps ``llm`` to
    ``rules``; ``attacher_from_mode`` accepts exactly ``off``/``rules`` and
    ``deps`` degrades anything else to off).

    ``llm`` MUST be ``rules`` here (#847 G2b): ``ingest_shard`` ignores
    ``--glossary-file`` under ``--symbol-glossary off``, so sending ``off``
    would compute the model's glossary and discard it. The model half itself
    is switched by the separate ``glossary_llm`` input."""
    return "rules" if in_process_glossary_mode(mode) == "rules" else "off"


def gowe_structure_metadata(mode: str) -> str:
    """The ``structure_metadata`` workflow input for ``STRUCTURE_METADATA``
    (#828): ``on`` only for exactly ``on``, else ``off`` — the in-process
    reading (``structure.structure_metadata_enabled``)."""
    return "on" if structure_metadata_enabled(mode) else "off"


def _gowe_inputs(
    entry: CollectionEntry,
    record: CollectionRecord,
    version: int,
    job_id: str,
    tenant: str,
    *,
    max_chunks: int | None = None,
    duplicate_policy: str = POLICY_OFF,
    declared_inputs: set[str] | None = None,
    stamped_image: bool = False,
    n_items: int = 1,
    batch_size: int = 20,
) -> dict[str, Any]:
    """Per-job workflow inputs (cwl/pdf-ingest-scatter.cwl): the archive's
    identity (``version``/``collection_id``/``spec_hash``/``job_id``) and the
    target collection's physical stores + build spec, so the run writes where
    the API serves and chunks the way the collection was built (ADR-0002).

    **The store URLs are seeded here, per run, from this API's own settings**
    (#407). They are not operator config and never come from
    ``gowe_workflow_inputs_json``: because ``GoWeBackend.run`` merges
    ``{**static_inputs, **inputs}`` (``gowe_backend.py``), a per-run key wins,
    so that blob is *structurally* unable to redirect an ingest at another
    instance — and ``backends.py`` refuses it at boot if it tries. Before this,
    the submission carried no store URLs at all and the CWL's own defaults
    (``http://localhost:6333``/``:9200``) decided, which on this host is
    production: a dev-tenant ingest created a collection and an index on the
    production instances. Each URL is the one **serving this entry's own store
    on that leg** — the vector leg through ``qdrant_collection_routes``, keyed
    by the physical collection, and the text leg through
    ``es_collection_routes``, keyed by the physical index (``es_index``, sent
    just above). The worker must write where the API reads, so both legs
    resolve through ``ragstack.store_routing``, the same functions the API's own
    store construction uses; a bare ``elasticsearch_url`` here would send a
    routed collection's chunks to the default cluster while the API queried the
    routed one, which is #407 on the other leg.

    ``max_chunks`` (#291): the job's chunk cap, threaded to the worker's
    ``ingest_shard --max-chunks`` — the worker has the store URLs, so it
    performs the same one-count check before its first write. Sent on every
    submission when the registered workflow declares the input (#849: 0 =
    unlimited, sent explicitly — the submitter's decision is on the record);
    to an older text that does not declare it, only when a cap applies.

    ``doi_enrichment`` (#596): whether the worker resolves each DISTINCT DOI to
    a title/authors/journal/pmid/pmcid. It has to travel on the submission for
    the same reason the store URLs do — the ingest task is a container with its
    own argv and cannot read this tenant's environment — and that gap is exactly
    why uploads through this backend arrived with a DOI and nothing else while
    the *local* backend (which runs the pipeline in this process, with
    ``app.state.doi_enricher``) enriched them. ``doi_mailto`` is a contact
    address, not a credential: it is published in the User-Agent of every
    request enrichment makes, so the immutable, UI-rendered ``submitted_inputs``
    snapshot is a fine place for it. Sent only when enrichment is on, so a
    deployment that turns it off submits what it submitted before.

    ``registry`` (#563): WHICH collection registry the worker resolves
    ``collection_id`` against, by NAME. It is the last piece of this tenant's
    physical state that did not travel on the submission — the worker read it
    from its own process environment, set once per worker GROUP, so a shared
    group could serve exactly one tenant's registry and every other tenant's
    ingest resolved against the wrong database. A NAME and never coordinates:
    ``inputs``/``submitted_inputs`` are an immutable, UI-rendered, plaintext
    snapshot, so a DSN put there would be permanent; the DSN reaches the
    container through the worker's ``--secret-file``. Sent only when
    ``COLLECTION_REGISTRY_NAME`` is set, so a deployment that has not adopted
    the convention submits exactly what it submitted before.

    ``duplicate_policy`` (#747): the worker-side duplicate guard
    (``ingest_shard --duplicate-policy``). Sent only when the policy is not
    ``off`` AND the registered workflow DECLARES the input
    (``declared_inputs``, from ``tool_image.declared_workflow_inputs`` — the
    ``provenance_inputs`` precedent). ADR-0010: the CWL names the image, so a
    CWL that declares the input is the declaration that its image's
    ``ingest_shard`` takes the flag; the pinned image does not, and its CWL
    does not declare it. The input arrives with the tools-image repin.

    ``symbol_glossary`` (#783): ``SYMBOL_GLOSSARY_EXTRACTION``, threaded to the
    worker's ``--symbol-glossary`` so a gowe ingest makes the same glossary
    choice as an in-process one (an ingest-time behaviour must travel on the
    submission, the #596 lesson). Sent for BOTH values — the workflow input
    defaults to ``rules``, so an omitted ``off`` would be ignored — but only
    when the registered workflow DECLARES the input, the ``duplicate_policy``
    precedent: an older registered text, whose image may predate the flag,
    is submitted exactly as before. (The image pinned when this landed,
    ragstack-tools-v1.6.10-b1, already takes ``--symbol-glossary``.) An unknown mode is sent as ``off``, which
    is what ``deps._build_glossary_attacher`` turns it into in-process, and
    never verbatim: the tool's argparse would exit 2 and fail every task.

    ``structure_metadata`` (#828): ``STRUCTURE_METADATA``, threaded to the
    worker's ``--structure-metadata`` — the ``symbol_glossary`` pattern, sent
    for both values and never verbatim (``gowe_structure_metadata``), with ONE
    more gate: the registered text must also name a STAMPED tools image
    (``stamped_image``, ``tool_image.names_stamped_image``; the
    ``provenance_inputs`` gate, ADR-0010 decision 4). No deployed image takes
    the flag yet, and an unstamped tree names ``ragstack-worker.sif``, which
    may be any build; there the input stays null (the CWL declares it
    optional, so the tool's default ``off`` applies) and the receipt's missing
    ``enrichment.structure`` is the tell.

    ``glossary_llm`` (#847 G2b): the glossary step's model switch, sent for
    both values when the registered text declares it AND names a stamped
    image (the ``structure_metadata`` gate). ``on`` exactly when
    ``SYMBOL_GLOSSARY_EXTRACTION=llm``, with the endpoint, the model, the key's
    NAME and the limits (:func:`~ragstack.ingestion.glossary_inputs.glossary_inputs`;
    the boot check refused anything they could not drive). The concurrency is
    split over the run's ``ceil(n_items / batch_size)`` tasks. An undeclaring
    text gets exactly the inputs it got before."""
    inputs: dict[str, Any] = {
        "version": str(version),
        "collection_id": entry.id,
        "spec_hash": record.spec_hash,
        "job_id": job_id,
        "tenant": tenant,
        "collection": entry.collection,
        "es_index": entry.es_index(),
        "qdrant_url": qdrant_url_for(entry.collection, settings),
        "es_url": es_url_for(entry.es_index(), settings),
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
    if "max_chunks" in (declared_inputs or set()):
        # #849: the submitter decides the cap, and says so every time — 0 is
        # "no cap", sent explicitly, so the run's inputs snapshot records the
        # decision rather than the workflow default.
        inputs["max_chunks"] = int(max_chunks or 0)
    elif max_chunks is not None and max_chunks > 0:
        inputs["max_chunks"] = int(max_chunks)
    if settings.collection_registry_name:
        inputs["registry"] = settings.collection_registry_name
    if duplicate_policy != POLICY_OFF and "duplicate_policy" in (declared_inputs or set()):
        inputs["duplicate_policy"] = duplicate_policy
    if "symbol_glossary" in (declared_inputs or set()):
        inputs["symbol_glossary"] = gowe_symbol_glossary(settings.symbol_glossary_extraction)
    if "structure_metadata" in (declared_inputs or set()):
        if stamped_image:
            inputs["structure_metadata"] = gowe_structure_metadata(settings.structure_metadata)
        elif structure_metadata_enabled(settings.structure_metadata):
            log.warning(
                "STRUCTURE_METADATA=on is not sent to the gowe ingest: the registered "
                "workflow names no stamped tools image, so its image may predate "
                "--structure-metadata (ADR-0010); chunks are ingested unstamped"
            )
    declared = declared_inputs or set()
    if "glossary_llm" in declared and stamped_image:
        mode = settings.symbol_glossary_extraction
        inputs["glossary_llm"] = "on" if mode == "llm" else "off"
        if mode == "llm":
            n_batches = max(1, -(-max(1, n_items) // max(1, batch_size)))
            inputs.update(glossary_inputs(settings, declared, n_batches=n_batches))
    if settings.doi_enrichment_enabled:
        inputs["doi_enrichment"] = True
        if settings.doi_enrichment_mailto:
            inputs["doi_mailto"] = settings.doi_enrichment_mailto
        if settings.doi_enrichment_cache_dir:
            inputs["doi_cache_dir"] = settings.doi_enrichment_cache_dir
    return inputs


#: The job warning label for a run whose glossary model half degraded (#847).
SYMBOL_GLOSSARY_DEGRADED = "symbol_glossary_degraded"


def glossary_degraded_warning(glossary: dict[str, Any], endpoint: str) -> str:
    """``symbol_glossary_degraded: <n> of <m> document(s) (<reason>=<k>, ...)
    endpoint <host:port>`` — counts, reason labels and host:port only: the
    reasons are the step's constant labels, never a message."""
    return (f"{SYMBOL_GLOSSARY_DEGRADED}: {glossary.get('degraded', 0)} of "
            f"{(glossary.get('counts') or {}).get('documents', 0)} document(s) "
            f"({_degraded_text(glossary)}) endpoint {endpoint}")


def _degraded_text(glossary: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v}" for k, v in (glossary.get("degraded_reasons") or {}).items()) \
        or "no reason recorded"


def _glossary_endpoint_name() -> str:
    from ragstack.ingestion.glossary_step import endpoint_name

    url = settings.symbol_glossary_llm_endpoint
    return endpoint_name(url) if url else "unset"


def _batch_size(backend: GoWeBackend) -> int:
    """The scatter workflow's ``batch_size`` as this deployment submits it
    (GOWE_WORKFLOW_INPUTS_JSON, else the workflow default 20) — what the
    glossary concurrency split divides by."""
    try:
        size = int((backend.static_inputs or {}).get("batch_size") or 20)
    except (TypeError, ValueError):
        size = 20
    return size if size > 0 else 20


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
    workspace: WorkspaceClient,
    *,
    collection_store: CollectionStore | None = None,
    version: int | None = None,
) -> None:
    """Background worker for the GoWe path: submit as the user, wait for
    completion AND delivery, checkpoint one result per item, record the archive
    location. Never raises — every failure lands on the job as a caller-safe
    label (an exception's class name; :data:`OUTPUT_STAGING_FAILED` when the
    engine could not deliver the archive).

    Reconciles the registry's two version records (#358): the reserved
    ``version`` (the counter, ``next_version``) is appended to the row's
    ordered ``versions`` list — what a restore replays — only once the
    archive was DELIVERED; a staging failure sets ``archive_pending`` on the
    row instead (the load happened, the archive does not exist: not evictable,
    and #353's retry hook re-archives it on the owner's next call).

    Bypasses ``ShardedIngestor`` on purpose: ``GoWeBackend`` ignores the
    per-shard callable (the engine runs the tools), so nothing in that path
    would ever checkpoint the receipts — every successful run would read
    ``failed`` from its all-pending item counts. A :class:`GoWeContractError`
    (delivered, but no usable receipts) fails the JOB with its class name as
    the label — visible, never "every document failed" (#203 blocker c). The
    token reaches only the engine / Workspace requests; it is not on the job
    and not in these log lines.
    """
    try:
        await job_store.update(job_id, status=RUNNING)
        await job_store.add_items(job_id, [(i.item_id, i.source) for i in items])
        try:
            run = await backend.run_submission(
                items, inputs=inputs, token=token, output_destination=output_destination,
                workspace=workspace,
            )
        except OutputStagingFailed as e:
            # Loaded into the stores, but no archive exists in the Workspace: the
            # engine's own label on the job, and archive_pending on the registry
            # row — the collection must not be evicted until it is re-archived.
            log.error("ingest job %s: %s", job_id, e)
            await job_store.update(job_id, status=FAILED, error=OUTPUT_STAGING_FAILED)
            if collection_store is not None:
                try:
                    await collection_store.set_archive_pending(target.id, True)
                except Exception:  # noqa: BLE001 — the job already records the failure
                    log.warning("ingest job %s: could not flag archive_pending on %r",
                                job_id, target.id, exc_info=True)
            return
        except GoWeError as e:
            # Message is engine-facing (scrubbed of tokens by GoWeClient); the
            # job carries only the caller-safe class-name label.
            log.error("ingest job %s: gowe submission failed: %s", job_id, e)
            await job_store.update(job_id, status=FAILED, error=type(e).__name__)
            return

        if run.archive_ref and collection_store is not None and version is not None:
            # Delivered: the reserved number now names a real versions/<n>/ —
            # record it in the order restore will replay.
            try:
                await collection_store.append_version(target.id, version)
            except Exception:  # noqa: BLE001 — the archive exists; the list is repairable
                log.warning("ingest job %s: could not record version %s on %r",
                            job_id, version, target.id, exc_info=True)
        for r in run.results:
            await job_store.mark_item(
                job_id, r.item_id, status=r.status, chunk_ids=r.chunk_ids, error=r.error
            )
        counts = await job_store.item_counts(job_id)
        final = _final_status(counts)
        fields: dict[str, object] = {"status": final}
        if final == FAILED and any(is_cap_refusal(r.error) for r in run.results):
            # The worker refused the shard(s) at the chunk cap (#291): the
            # receipts carry the numbers per item; the job carries the label.
            fields["error"] = CHUNK_CAP_EXCEEDED
        elif final == FAILED and any(is_glossary_refusal(r.error) for r in run.results):
            # #847 G2b: ingest_shard refused a batch whose glossary file does
            # not match it (exit 6) — a wiring bug, never data; the items carry
            # the detail, the job the label.
            fields["error"] = GLOSSARY_FILE_MISMATCH
        if run.glossary and run.glossary.get("degraded"):
            # The model half degraded for some documents: they carry the rules
            # glossary and the run completed. Said once per job, naming the
            # endpoint (host:port, never a credential) and the reasons.
            log.warning(
                "ingest job %s: symbol glossary DEGRADED to the rules glossary for %d of "
                "%d document(s) (%s) — glossary endpoint %s",
                job_id, run.glossary["degraded"],
                run.glossary.get("counts", {}).get("documents", 0),
                _degraded_text(run.glossary), _glossary_endpoint_name())
            fields["warnings"] = [glossary_degraded_warning(run.glossary,
                                                            _glossary_endpoint_name())]
        if run.archive_ref:
            fields["archive_ref"] = run.archive_ref
        if len(run.results) == 1 and run.results[0].status == COMPLETED:
            fields["chunk_ids"] = run.results[0].chunk_ids
        await job_store.update(job_id, **fields)
        if final == COMPLETED:
            from ragstack.api.deps import write_ingest_manifest_for

            chunks = sum(len(r.chunk_ids or []) for r in run.results)
            write_ingest_manifest_for(target, source=source, chunk_count=chunks or None)
    except Exception as e:  # noqa: BLE001 — a background task must never raise
        log.warning("ingest job %s failed: %s", job_id, type(e).__name__)
        try:
            await job_store.update(job_id, status=FAILED, error=type(e).__name__)
        except Exception:  # noqa: BLE001
            log.warning("ingest job %s: could not record the failure", job_id, exc_info=True)


async def _gowe_upload_sources(
    workspace: WorkspaceClient,
    token: str,
    subject: str,
    entry: CollectionEntry,
    record: CollectionRecord,
    tenant: str,
    files: list[UploadFile],
    kinds: list[str],
    budget: _UploadBudget,
) -> list[WorkItem]:
    """Write the admitted uploads into ``<collection folder>/sources/`` with the
    caller's token; return one work item per file (``ws://`` source).

    ``UploadFile.size`` (when the multipart parser knows it) goes to
    ``upload_source`` so an oversize file is refused before any RPC and the Shock
    PUT carries a Content-Length; the per-file byte cap is enforced while
    streaming either way (``workspace._bounded``), and the per-request
    ``budget`` is charged per chunk through ``_MeteredUpload`` (#202). Refusals:
    over ``max_document_bytes`` → 413, over the request budget → 413, a
    rejected token → 401, any other Workspace failure → 502. A same-named file
    already in ``sources/`` is refused (never overwritten, never deleted) → 409
    naming the existing object, which can be ingested by reference through
    ``POST /v1/ingest``. ``kinds`` are the files' ``_sniff_upload`` results (in
    order) — the content type and magic were checked before this is called.

    Files are written one after another, so a mid-stream refusal on file N
    leaves files 1..N-1 in ``sources/`` (never deleted): a plain retry of the
    same request then 409s on file 1. Not reachable through this endpoint
    today — the declared sizes are exact (the parser has the whole body), so
    every size refusal happens in ``_admit_uploads`` before the first write —
    but it is the consequence if a stream ever trips ``_bounded`` or the
    request budget here.

    Preparing the collection folder is the first Workspace call and gets the
    same mapping as the writes that follow — 401 for a rejected token, 502 for
    anything else — because an uncaught store failure here surfaced as a bare
    500 on the route (#414: with the folder already present from an earlier
    job, every later upload into the same collection hit it).
    """
    try:
        folder = await workspace.ensure_collection_folder(
            token, subject, entry.id, spec_hash=record.spec_hash, tenant=tenant
        )
    except WorkspaceAuthError:
        raise HTTPException(
            status_code=401, detail="the Workspace rejected the caller's token"
        ) from None
    except WorkspaceError as e:
        raise HTTPException(
            status_code=502,
            detail=(
                f"the collection folder for {entry.id!r} could not be prepared in the "
                f"Workspace: {e}. No files were uploaded — retry, and if it persists "
                f"check that {ws_uri(collection_folder(subject, entry.id))} is a folder "
                f"you own and is not in use by another collection"
            ),
        ) from None
    sources = f"{ws_path(folder)}/sources"
    items: list[WorkItem] = []
    for idx, (upload, kind) in enumerate(zip(files, kinds, strict=True)):
        safe_name = _safe_upload_name(
            upload.filename, fallback=f"upload_{idx}{_kind_suffixes(kind)[0]}", kind=kind
        )
        try:
            uri = await workspace.upload_source(
                token, sources, safe_name, _MeteredUpload(upload, budget),
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
        except WorkspaceExists as e:
            existing = ws_uri(e.path)
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{safe_name!r} already exists in the collection's sources at "
                    f"{existing}; it was left untouched — to ingest it, POST /v1/ingest "
                    f"with source={existing}"
                ),
            ) from None
        except WorkspaceError as e:
            # The live service's own wording for an existing object.
            if "overwrit" in str(e).lower():
                existing = ws_uri(f"{sources}/{safe_name}")
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"{safe_name!r} already exists in the collection's sources at "
                        f"{existing}; it was left untouched — to ingest it, POST "
                        f"/v1/ingest with source={existing}"
                    ),
                ) from None
            raise HTTPException(
                status_code=502, detail=f"Workspace write of {safe_name!r} failed: {e}"
            ) from None
        items.append(WorkItem(item_id=ws_path(uri), source=uri))
    return items


class IngestRequest(BaseModel):
    source: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Target collection id. None (or the reserved pointer name) → the caller's
    # WRITABLE default: their advertised listing `default` when they can write
    # it or it is the shared surface, else their first writable collection (#422).
    collection: str | None = None
    # The duplicate-paper guard for this request (#747). None → the deployment
    # default (settings.duplicate_policy, `refuse`); "allow" admits a second
    # copy of an already-indexed work and stamps `duplicate_of` on it.
    duplicate_policy: Literal["refuse", "allow"] | None = None


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
    #: The collection this job actually targets (#422). It matters when the
    #: request OMITTED `collection`: that is the case where the SERVER picked,
    #: and a caller whose read default differs from their write pick would
    #: otherwise have to infer where their upload landed. Echoing the id is safe
    #: — the picker only ever chooses from the caller's own visible set, and on
    #: the explicit path it is the id the caller sent. Optional in the schema, so
    #: the Go stub need not emit it.
    collection: str | None = None
    #: Caller-safe notes on a run that completed with a caveat (#847 G2b), e.g.
    #: ``symbol_glossary_degraded: timeout=2 (endpoint host:port)``. Always
    #: present here (``[]`` when none); optional in the schema, so the Go stub
    #: need not emit it.
    warnings: list[str] = Field(default_factory=list)


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
        _refuse_unrunnable_chunk_method(entry)
        policy = _resolve_duplicate_policy(request.duplicate_policy)
        await _precheck_duplicate(
            entry, [Path(ws_path(uri)).name],
            await _duplicate_scope(entry, collections, principal, policy), policy,
        )
        collection_store = get_collection_store(http_request)
        record = await _registry_row(entry, collection_store)
        backend = _gowe_backend(http_request)
        workspace = get_workspace(http_request)
        # Every refusal precedes the job: a version-reservation failure (json
        # registry → 503) must not leave an `accepted` job nobody will run.
        cap = await _chunk_cap_for(entry, collection_store, record)
        version = await _reserve_version(entry, collection_store)
        # The scope owns the row from here (#415), and it deliberately extends
        # PAST add_task's argument expressions: `_gowe_inputs(...)` and
        # `ws_uri(collection_folder(...))` are evaluated inside the stranding
        # window, so anything they raise strands the row without it.
        async with job_lifecycle(
            job_store, source=request.source, tenant_id=principal.tenant,
            collection_id=entry.id,
        ) as job:
            background_tasks.add_task(
                _run_gowe_ingest,
                job_store,
                backend,
                job.job_id,
                [WorkItem(item_id=ws_path(uri), source=uri)],
                _gowe_inputs(entry, record, version, job.job_id, tenant, max_chunks=cap,
                             duplicate_policy=policy,
                             declared_inputs=declared_workflow_inputs(backend.workflow_cwl),
                             stamped_image=names_stamped_image(backend.workflow_cwl),
                             n_items=1, batch_size=_batch_size(backend)),
                token,
                f"{ws_uri(collection_folder(subject, entry.id))}/versions/",
                entry,
                request.source,
                workspace,
                collection_store=collection_store,
                version=version,
            )
            job.dispatched()  # _run_gowe_ingest terminalizes the row from here
            return IngestResponse(
                job_id=job.job_id, status=job.status, collection=entry.id
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
    policy = _resolve_duplicate_policy(request.duplicate_policy)
    scope = await _duplicate_scope(target, collections, principal, policy)
    if _is_file_name(request.source):
        await _precheck_duplicate(
            target, [Path(request.source).name], scope, policy,
            same_document=_source_identity(request.source),
        )

    # As above (#415): `get_collection_store(http_request)` is an add_task
    # argument, evaluated inside the window the scope has to cover.
    async with job_lifecycle(
        job_store, source=request.source, tenant_id=principal.tenant,
        collection_id=target.id,
    ) as job:
        background_tasks.add_task(
            _run_ingest,
            job_store,
            run_ingestor,
            settings.ingest_root,
            job.job_id,
            request.source,
            tenant,
            target,
            collection_store=get_collection_store(http_request),
            duplicate_policy=policy,
            duplicate_scope=scope,
        )
        job.dispatched()  # _run_ingest terminalizes the row from here
        return IngestResponse(
            job_id=job.job_id, status=job.status, collection=target.id
        )


_PDF_MAGIC = b"%PDF"
_UPLOAD_CHUNK = 1 << 20  # 1 MiB read granularity while streaming to disk

# The upload kinds this router accepts, keyed by the normalised content type:
# kind → the file suffixes staged for it (the first is forced onto a name that
# carries none of them). ``settings.upload_content_types`` (the operator
# allowlist) selects from this table; a type absent from BOTH is 415. The
# pdf/text/markdown suffixes are ones ``DEFAULT_INGEST_SUFFIXES`` enqueues, so
# the local branch ingests them. XML (JATS) is accepted at the gate per #202's
# allowlist, but ``.xml`` has no loader in the local registry yet: the staged
# file becomes a FAILED item with the error ``no loader for .xml``
# (_fail_unsupported_items — visible and countable, never a silent skip), and
# the gowe scatter workflow extracts PDFs; wiring the JATS path
# (cwl/jats-ingest.cwl) into the upload is follow-up work.
_UPLOAD_KINDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "application/pdf": ("pdf", (".pdf",)),
    "text/plain": ("text", (".txt", ".md")),
    "text/markdown": ("markdown", (".md", ".txt")),
    "application/xml": ("xml", (".xml",)),
    "text/xml": ("xml", (".xml",)),
}


def _content_type(upload: UploadFile) -> str:
    return (upload.content_type or "").split(";", 1)[0].strip().lower()


def _allowed_content_types() -> set[str]:
    return {t.strip().lower() for t in settings.upload_content_types if t.strip()}


async def _sniff_upload(upload: UploadFile) -> str:
    """Content-type allowlist + magic check for ONE upload; returns its kind.

    The single implementation both upload branches gate on (#202): the content
    type must be in ``settings.upload_content_types`` AND a kind this router
    knows (415 otherwise); a PDF must additionally start with ``%PDF`` (415 —
    an empty upload has no header, so it is refused the same way). Only the
    magic bytes are read, and the stream is rewound, so nothing is staged or
    written before every file has passed. Text/XML get no byte sniff: a BOM or
    a leading comment would false-refuse a valid file.
    """
    ctype = _content_type(upload)
    allowed = _allowed_content_types()
    if ctype not in allowed or ctype not in _UPLOAD_KINDS:
        raise HTTPException(
            status_code=415,
            detail=(
                f"{upload.filename!r}: {ctype or '(none)'!r} is not an accepted upload "
                f"content type; accepted: {', '.join(sorted(allowed & _UPLOAD_KINDS.keys()))}"
            ),
        )
    kind = _UPLOAD_KINDS[ctype][0]
    if kind == "pdf":
        head = await upload.read(len(_PDF_MAGIC))
        await upload.seek(0)
        if not head.startswith(_PDF_MAGIC):
            what = "empty upload, no %PDF header" if not head else "missing %PDF header"
            raise HTTPException(
                status_code=415, detail=f"{upload.filename!r}: not a PDF ({what})"
            )
    return kind


#: Typographic characters that publishers routinely put in PDF filenames, mapped
#: to the ASCII they stand in for. Folding these keeps the name readable —
#: ``N\u2010glycosylation`` becomes ``N-glycosylation``, not ``Nglycosylation``.
_ASCII_LOOKALIKES = {
    0x2010: "-", 0x2011: "-", 0x2012: "-", 0x2013: "-", 0x2014: "-", 0x2015: "-",
    0x2212: "-",
    0x2018: "'", 0x2019: "'", 0x201A: "'", 0x201B: "'",
    0x201C: "'", 0x201D: "'", 0x201E: "'", 0x201F: "'",
    0x00A0: " ", 0x2007: " ", 0x2008: " ", 0x2009: " ", 0x200A: " ", 0x202F: " ",
    0x200B: "", 0x200C: "", 0x200D: "", 0x00AD: "", 0xFEFF: "",
    0x2026: "...",
}

#: Characters that are ASCII but change the meaning of the ``ws://`` URI the
#: name ends up inside. ``#`` matters most: item ids are ``<source>#<index>``
#: (``…/sources/a.pdf#0``), so a ``#`` in the filename splits the id in the
#: wrong place.
_URI_HOSTILE = str.maketrans({"#": "_", "%": "_", "?": "_"})


def _ascii_fold(name: str) -> str:
    r"""Reduce *name* to ASCII, keeping it readable.

    BV-BRC's Workspace cannot address a path with a non-ASCII component at all:
    ``get_download_url`` raises ``[-32603] Can't escape \x{2010}, try
    uri_escape_utf8() instead``. So a publisher PDF named with a typographic
    hyphen — ``The FEBS Journal - 2021 - Pralow - Comprehensive
    N\u2010glycosylation analysis.pdf`` — cannot be pre-staged, and the ingest
    fails at ``extract`` with a message that names neither the file nor the
    character (GoWe#267). Folding at upload time is the durable fix: nothing
    downstream ever sees the non-ASCII name.

    Typographic lookalikes are mapped to their ASCII counterparts first, then
    NFKD decomposition drops accents (``é`` → ``e``), then anything still
    non-ASCII is replaced with ``_`` rather than deleted, so two names that
    differ only outside ASCII stay different and the 409-on-collision check
    still means what it says.
    """
    folded = name.translate(_ASCII_LOOKALIKES)
    decomposed = unicodedata.normalize("NFKD", folded)
    out = []
    lossy = False
    for ch in decomposed:
        if unicodedata.combining(ch):
            continue
        if ch.isascii():
            out.append(ch)
        else:
            # Surrogates land here too: unreachable through Starlette's
            # multipart decode, but they would crash the tag's utf-8 encode.
            out.append("_")
            lossy = True
    ascii_name = "".join(out).translate(_URI_HOSTILE)
    if not lossy:
        return ascii_name
    # Characters were replaced rather than mapped, so distinct names can collide
    # ("\u4e2d\u6587A.pdf" and "\u4e2d\u6587B.pdf" both become "___.pdf"). Without a
    # disambiguator the second upload 409s against a filename the user never
    # chose. Key the tag off the ORIGINAL name so it is stable across retries.
    tag = hashlib.sha1(name.encode("utf-8", "surrogatepass")).hexdigest()[:6]
    stem, dot, ext = ascii_name.rpartition(".")
    return f"{stem}-{tag}{dot}{ext}" if dot else f"{ascii_name}-{tag}"


def _safe_upload_name(raw: str | None, fallback: str, kind: str) -> str:
    """Reduce a client-supplied filename to a safe, traversal-free ASCII basename.

    Keeps only the final path component (drops any directory parts, absolute
    prefixes, and ``..`` segments), strips other separators, folds the result to
    ASCII (see ``_ascii_fold`` — the Workspace cannot address a non-ASCII path),
    and forces a suffix the ingest manifest picks up for ``kind`` (see
    ``_UPLOAD_KINDS``) unless the name already carries one. Falls back to
    ``fallback`` when nothing usable remains. The result is still re-confined
    under the staging dir by the caller — this is the first line of defence, not
    the only one.
    """
    # PurePosixPath/ntpath both leave ".." as a name component, so take the last
    # component and reject the dot-names explicitly.
    base = raw.replace("\\", "/").rsplit("/", 1)[-1].strip() if raw else ""
    base = base.replace("\x00", "")
    # Fold before the dot-name check: a name that is only non-ASCII folds to
    # separators or nothing, and must land on the fallback rather than be written.
    base = _ascii_fold(base)
    # ...and split AGAIN. The fold can MANUFACTURE separators the first split
    # never saw: NFKD maps U+FF0F FULLWIDTH SOLIDUS to "/", U+FF3C to a
    # backslash, and U+FF0E/U+2024/U+2025/U+2026 to "." or "..". Without this,
    # a fullwidth-slash name leaves here as "../../etc/passwd" -- confinement
    # downstream refuses it, but this function is documented as THE
    # traversal-free basename and must not hand one on. It also stopped
    # benign "dir/sub/name.pdf" spellings from 500ing on the staging branch.
    base = base.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if base in ("", ".", "..") or set(base) <= {".", "_", " ", "-"}:
        return fallback
    suffixes = _kind_suffixes(kind)
    if not base.lower().endswith(suffixes):
        base = f"{base}{suffixes[0]}"
    return base


def _kind_suffixes(kind: str) -> tuple[str, ...]:
    for k, suffixes in _UPLOAD_KINDS.values():
        if k == kind:
            return suffixes
    raise ValueError(f"unknown upload kind {kind!r}")


def _upload_names(files: list[UploadFile], kinds: list[str]) -> list[str]:
    """The sanitized names the uploads will be staged / written under — the
    names the duplicate pre-check reads ids from (#747)."""
    return [
        _safe_upload_name(
            u.filename, fallback=f"upload_{i}{_kind_suffixes(k)[0]}", kind=k
        )
        for i, (u, k) in enumerate(zip(files, kinds, strict=True))
    ]


class _UploadBudget:
    """The per-request byte total (``max_upload_bytes_per_request``), charged
    chunk by chunk as the files flow on either branch — the streaming half of
    the bound ``_admit_uploads`` checks up front against the declared sizes.
    ``limit <= 0`` disables it."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    def charge(self, n: int, filename: str | None) -> None:
        self.used += n
        if self.limit > 0 and self.used > self.limit:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"{filename!r}: the request's files exceed "
                    f"max_upload_bytes_per_request ({self.limit} bytes)"
                ),
            )


class _MeteredUpload:
    """``read(n)`` over an ``UploadFile`` that charges ``budget`` per chunk —
    the stream handed to ``WorkspaceClient.upload_source`` (which accepts any
    async ``read``-able), so the running per-request total aborts the Shock
    PUT mid-body exactly like the per-file cap does."""

    def __init__(self, upload: UploadFile, budget: _UploadBudget) -> None:
        self._upload = upload
        self._budget = budget
        self.filename = upload.filename

    async def read(self, n: int = -1) -> bytes:
        chunk = await self._upload.read(n)
        self._budget.charge(len(chunk), self._upload.filename)
        return chunk


async def _admit_uploads(files: list[UploadFile]) -> tuple[list[str], _UploadBudget]:
    """The up-front gate for one upload request, before anything is staged,
    written, or reserved: file count (413), content type + PDF magic per file
    (415), each declared size against ``max_document_bytes`` (413) and their
    sum against ``max_upload_bytes_per_request`` (413). Returns the files'
    kinds (in order) and the request's streaming byte budget.

    "Before any read" means before any staging or Workspace write: the
    multipart body has already been parsed (spooled by Starlette) by the time
    a handler runs — that is also why ``UploadFile.size`` is always known here,
    which makes the streaming aborts downstream belt-and-braces for a parser
    that did not report sizes.
    """
    if len(files) > settings.max_upload_files:
        raise HTTPException(
            status_code=413,
            detail=(
                f"too many files: {len(files)} > max_upload_files "
                f"({settings.max_upload_files})"
            ),
        )
    kinds = [await _sniff_upload(upload) for upload in files]
    max_bytes = settings.max_document_bytes
    for upload in files:
        if max_bytes and upload.size is not None and upload.size > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"{upload.filename!r}: exceeds max_document_bytes "
                    f"({max_bytes} bytes)"
                ),
            )
    limit = settings.max_upload_bytes_per_request
    declared = sum(upload.size or 0 for upload in files)
    if limit > 0 and declared > limit:
        raise HTTPException(
            status_code=413,
            detail=(
                f"the request's files total {declared} bytes > "
                f"max_upload_bytes_per_request ({limit} bytes)"
            ),
        )
    return kinds, _UploadBudget(limit)


async def _stage_upload(
    upload: UploadFile, dest: Path, max_bytes: int, budget: _UploadBudget
) -> None:
    """Stream one admitted upload from the server's spool to ``dest``, enforcing
    the per-file byte cap (413) and the per-request budget (413) WHILE writing
    — never holding the file in memory. The reads are capped at
    ``max_bytes + 1 - written``, so a refusal stops at byte ``max_bytes + 1``
    of the spool, never a whole trailing chunk, and nothing oversized reaches
    INGEST_ROOT. (The body itself has already been received and spooled by the
    multipart parser — see ``api/upload_guard.py`` for the check that runs
    before that.) ``dest`` is assumed already confined to the staging dir; the
    content type and magic were checked by ``_sniff_upload`` before anything
    was staged.
    """
    written = 0
    with dest.open("wb") as fh:
        while True:
            want = _UPLOAD_CHUNK
            if max_bytes:
                want = min(want, max_bytes + 1 - written)
            chunk = await upload.read(want)
            if not chunk:
                break
            written += len(chunk)
            if max_bytes and written > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"{upload.filename!r}: exceeds max_document_bytes "
                        f"({max_bytes} bytes)"
                    ),
                )
            budget.charge(len(chunk), upload.filename)
            fh.write(chunk)


@router.post(
    "/ingest/upload",
    response_model=IngestResponse,
    status_code=202,
    dependencies=[Depends(single_inflight_ingest), Depends(rate_limited("ingest"))],
)
async def ingest_upload(
    http_request: Request,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(
        ..., description="One or more files (PDF, plain text, Markdown, or JATS XML)."
    ),
    collection: str | None = Form(default=None),
    duplicate_policy: Literal["refuse", "allow"] | None = Form(default=None),
    tenant: str = Depends(resolve_tenant),
    principal: Principal = Depends(resolve_principal),
    ingestor: ShardedIngestor = Depends(get_ingestor),
    job_store: JobStore = Depends(get_job_store),
    collections: CollectionRegistry = Depends(get_collections),
) -> IngestResponse:
    """Upload one or more files and ingest them in the background.

    Multipart counterpart to ``POST /v1/ingest`` (which takes a server-side path):
    each file is staged under ``{INGEST_ROOT}/uploads/{tenant}/{job_id}/`` with a
    sanitized, traversal-confined filename, then the SAME sharded ingest path runs
    over that per-job directory. Returns 202 with a real job_id; poll
    ``GET /v1/ingest/{job_id}`` for progress exactly as for a path ingest.

    Bounds (#202 phase 2c), all checked before anything is staged or written
    (``_admit_uploads``; the multipart body has by then been received and
    spooled by the server — ``api/upload_guard.py`` is the check that runs
    before the body, from ``Content-Length``) and — for the byte caps — again
    while the files stream out of the spool:
    a content type outside ``upload_content_types`` or a PDF without the
    ``%PDF`` header → 415; a file over ``max_document_bytes``, more than
    ``max_upload_files`` files, or files totalling more than
    ``max_upload_bytes_per_request`` → 413; a job of the caller's still in
    flight → 429 + ``Retry-After`` (``single_inflight_ingest``). Like
    ``POST /v1/ingest``: 503 when no INGEST_ROOT is configured (uploads have
    nowhere confined to land) and 501 for an ingest backend that is neither
    ``local`` nor ``gowe``.

    Under ``ingest_backend=gowe`` (#203/#353) the files are written into the
    caller's BV-BRC Workspace (``.ragstack/collections/<id>/sources/``) with the
    caller's own token and the scatter workflow is submitted as the caller with
    ``ws://`` inputs — the same submission path as a Workspace-reference ingest.
    Needs a bearer BV-BRC identity (401 otherwise) and a registered collection.
    A Workspace that refuses the token is 401 and any other Workspace failure —
    preparing the collection folder included — is 502 with the job marked
    ``failed``; neither is ever a bare 500 (#414).
    """
    _refuse_unknown_backend()
    if _ingest_backend_name() == _GOWE:
        # Browser upload → the caller's Workspace → submit as the caller (#203/
        # #353). One code path with the Workspace-reference ingest above; no
        # staging directory on this host, so no INGEST_ROOT gate here.
        token, subject = _gowe_caller(principal)
        # Every gate BEFORE any Workspace write and before the version is
        # reserved: an up-front refusal must not burn a version number.
        kinds, budget = await _admit_uploads(files)
        entry = await _authorize_ingest_target(collection, principal, collections)
        _refuse_unrunnable_chunk_method(entry)
        policy = _resolve_duplicate_policy(duplicate_policy)
        await _precheck_duplicate(
            entry, _upload_names(files, kinds),
            await _duplicate_scope(entry, collections, principal, policy), policy,
        )
        collection_store = get_collection_store(http_request)
        record = await _registry_row(entry, collection_store)
        backend = _gowe_backend(http_request)
        workspace = get_workspace(http_request)
        # Reserve the version BEFORE writing sources: a registry that cannot
        # (json → 503) must not leave files uploaded with no version to run
        # under. A rejected upload then leaves a gap in the numbering — fine.
        cap = await _chunk_cap_for(entry, collection_store, record)
        version = await _reserve_version(entry, collection_store)
        # The scope covers the whole window (#415), Workspace streaming and
        # add_task's argument expressions included. The `except HTTPException`
        # below STAYS: it sets the more specific "rejected" label, and the
        # scope re-reads the row before terminalizing, so it never clobbers it.
        async with job_lifecycle(
            job_store, source="upload", tenant_id=principal.tenant, collection_id=entry.id,
        ) as job:
            try:
                items = await _gowe_upload_sources(
                    workspace, token, subject, entry, record, tenant, files, kinds, budget
                )
            except HTTPException:
                await job_store.update(job.job_id, status=FAILED, error="rejected")
                raise
            background_tasks.add_task(
                _run_gowe_ingest,
                job_store,
                backend,
                job.job_id,
                items,
                _gowe_inputs(entry, record, version, job.job_id, tenant, max_chunks=cap,
                             duplicate_policy=policy,
                             declared_inputs=declared_workflow_inputs(backend.workflow_cwl),
                             stamped_image=names_stamped_image(backend.workflow_cwl),
                             n_items=len(items), batch_size=_batch_size(backend)),
                token,
                f"{ws_uri(collection_folder(subject, entry.id))}/versions/",
                entry,
                "upload",
                workspace,
                collection_store=collection_store,
                version=version,
            )
            job.dispatched()  # _run_gowe_ingest terminalizes the row from here
            return IngestResponse(
                job_id=job.job_id, status=job.status, collection=entry.id
            )
    kinds, budget = await _admit_uploads(files)
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
    policy = _resolve_duplicate_policy(duplicate_policy)
    scope = await _duplicate_scope(target, collections, principal, policy)
    await _precheck_duplicate(target, _upload_names(files, kinds), scope, policy)

    # #415, and this is the site that proved a hand-rolled handler is not
    # enough: the `confine_to_root` 400 and the `mkdir` OSError below both sit
    # ABOVE the staging `try` that was believed to cover them, so both stranded
    # the row. The scope starts at the create and ends past add_task.
    async with job_lifecycle(
        job_store, source="upload", tenant_id=principal.tenant,
        collection_id=target.id,
    ) as job:
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
            for idx, (upload, kind) in enumerate(zip(files, kinds, strict=True)):
                safe_name = _safe_upload_name(
                    upload.filename, fallback=f"upload_{idx}{_kind_suffixes(kind)[0]}", kind=kind
                )
                dest = staging_dir / safe_name
                try:
                    dest = confine_to_root(str(dest), staging_dir)
                except LoaderError:
                    raise HTTPException(
                        status_code=400,
                        detail=f"{upload.filename!r}: resolves outside the staging directory",
                    ) from None
                await _stage_upload(upload, dest, settings.max_document_bytes, budget)
        except HTTPException:
            # Reject cleanly: drop the partial staging dir and mark the job failed so a
            # poll reflects reality, then re-raise the original 4xx to the client.
            # Kept under the scope: "rejected" is the more specific label and the
            # scope's re-read leaves an already-terminal row alone.
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
            True,  # every staged file becomes an item; unsupported suffixes fail visibly
            collection_store=get_collection_store(http_request),
            duplicate_policy=policy,
            duplicate_scope=scope,
        )
        job.dispatched()  # _run_ingest terminalizes the row from here
        return IngestResponse(
            job_id=job.job_id, status=job.status, collection=target.id
        )


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
        job_id=job.job_id, status=job.status, chunk_ids=job.chunk_ids, items=items,
        # The row's own stamp, so a poll answers "where did this land?" with the
        # same id the 202 did. Legacy rows carry "" — report null, not "".
        collection=job.collection_id or None,
        warnings=job.warnings,
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

    There is no way to name a collection here (no per-collection document
    endpoint exists yet), so the route is IMPLICIT-only and targets whatever an
    omitted ``collection`` resolves to **for this caller** —
    :func:`resolve_default_entry`, the same symbol ``/v1/query`` and the
    ``GET /v1/collections`` listing use. The surface therefore MOVES per caller:
    a caller who cannot read the registry pointer target lists their own first
    visible collection instead of being told, by id, that a collection they were
    never shown does not exist (#422 — the ``/v1/query`` half of this was #419).
    A caller who can read nothing at all gets the 404 that names no id.
    """
    # Authorize AND read the same entry. These used to be the same store by
    # construction (the pointer was always the settings-derived entry, and
    # `get_text_index` returns that entry's index). Now that the target is both
    # configurable AND caller-aware they can differ — and reading `app.state`'s
    # index after authorizing against the resolved entry would serve one
    # collection's documents under another collection's ACL.
    #
    # The resolver deliberately does not authorize: `enforce_access` stays,
    # because the collection LIFECYCLE gate lives inside it and `filter_readable`
    # does not run it (see api/default_collection.py's module docstring).
    default = await resolve_default_entry(registry, principal)
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

    Like ``GET /v1/documents`` this route is IMPLICIT-only — there is no way to
    name a collection — so it targets whatever an omitted ``collection``
    resolves to **for this caller** (:func:`resolve_default_entry`, #422), not
    the global registry pointer. READ suffices only on the LEGACY SHARED SURFACE
    — there per-chunk ``tenant_id`` stamping is the isolation and the delete can
    only ever remove the caller's own chunks, so a write gate would lock every
    non-admin out of the shared corpus. Anywhere else this is a mutation of
    somebody's owned collection and needs write; the exemption keys on the
    SURFACE FLAG, never on "is this what default points at" (see
    :attr:`CollectionEntry.is_shared_surface`). For a caller whose resolved
    entry is their own collection, ``write`` passes via ownership. Stores are
    taken from the same entry that was authorized, never from ``app.state``."""
    default = await resolve_default_entry(registry, principal)
    await enforce_access(
        principal, default.id, "read" if default.is_shared_surface else "write"
    )
    await default.vector_store.delete(doc_id, tenant_id=tenant)
    await default.text_index.delete(doc_id, tenant_id=tenant)
