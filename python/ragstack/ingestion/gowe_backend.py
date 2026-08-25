"""``GoWeBackend`` — an ``IngestBackend`` that runs shards on GoWe (ADR-0001 2b).

Drops in where ``LocalAsyncIORunner`` runs shards in-process: instead, it submits
a scatter workflow to the GoWe engine (via ``GoWeClient``), waits for it, reads
the per-item receipts, and maps them back to ``ItemResult``s. The engine owns
scatter/retry/resume; ``shard_fn`` is ignored — the per-item CLI *tool*, not an
in-process callable, is the unit of work (ADR-0001 Appendix A).

Two callers, one submission path (:meth:`GoWeBackend.run_submission`):

* the offline bulk plane — :meth:`run_shards` (the ``IngestBackend`` protocol),
  where each ``WorkItem.source`` is a pre-built JSONL shard under the engine's
  staging dirs, the client's own operator token authenticates, there is no
  ``output_destination``, and the receipts are the workflow's ``receipts``
  ``File[]`` output downloaded from the engine (``file://`` locations); and
* the user ingest path (#203/#353) — the API calls :meth:`run_submission`
  with ``ws://`` Workspace sources, the CALLER's token and an
  ``output_destination`` in the caller's Workspace. The engine pre-stages the
  inputs and post-stages the workflow's ONE output — the ``archive``
  Directory — to ``<output_destination>/<version>/`` as that user, and the
  per-item receipts are read back from ``receipt.json`` INSIDE that archive
  through the Workspace, with the caller's token. Nothing is downloaded from
  the engine on this path: post-staging rewrites File output locations to
  ``ws://`` (so the engine's download endpoint cannot serve them afterwards),
  and the workflow exposes no File outputs anyway (every top-level File would
  otherwise be uploaded flat, by basename, into ``versions/``).

The token is a per-call argument: it is never held on the backend or the
client, and it reaches exactly one place — the ``Authorization`` header of each
engine / Workspace request.

Completion is two-phase on the user path: the engine marks a submission
COMPLETED and post-stages it in the same scheduler tick, so ``wait`` treats
COMPLETED as terminal only once ``output_state`` is ``delivered`` (or
``upload_failed`` → :class:`OutputStagingFailed`), bounded by
``output_wait_timeout``.

Per-DOCUMENT status under batching (#203 2b). The scatter workflow ingests a
*batch* of PDFs per task, so ``receipt.json`` holds one ``ShardReceipt`` per
batch, not per item; the per-document truth is each receipt's ``docs`` rows
(``error`` empty = upserted, else why not; ``chunk_ids`` = that document's).
:meth:`GoWeBackend._map_archive_receipts` therefore maps items to rows **by
source basename** (the engine pre-stages a ``ws://`` input under its basename,
which is what the extract tool records as the document's path), a failed batch
attributes its error to every document of that batch, and a row's constant
error (``NO_TEXT_ERROR`` for a scanned PDF) reaches the job's per-item error
verbatim. Option-A archives (one receipt per item) still map positionally.
"""
from __future__ import annotations

import json
import logging
import os
import posixpath
from dataclasses import dataclass, field
from typing import Any

from ragstack.ingestion.gowe_client import (
    OUTPUT_UPLOAD_FAILED,
    GoWeClient,
    GoWeError,
)
from ragstack.ingestion.manifest import ItemResult, WorkItem
from ragstack.ingestion.receipts import COMPLETED, DocRow, ShardReceipt
from ragstack.jobstore import COMPLETED as JOB_COMPLETED
from ragstack.jobstore import FAILED as JOB_FAILED
from ragstack.workspace import WorkspaceClient, WorkspaceError, ws_path

log = logging.getLogger(__name__)

#: The file inside an archive version directory holding the load receipt(s):
#: one receipt object, or a JSON array of them in scatter order (archive.py).
ARCHIVE_RECEIPT_NAME = "receipt.json"
#: The engine's failure label when post-staging to output_destination fails.
OUTPUT_STAGING_FAILED = "OUTPUT_STAGING_FAILED"
#: Per-item error when the delivered receipts account for the run but not for
#: this document (its batch's receipt names no row for it).
NO_RECEIPT_ENTRY = "no receipt entry for document"
#: Per-item error for a receipt entry that is not a readable ShardReceipt.
NO_READABLE_RECEIPT = "no readable receipt for shard"
#: Per-item error for a document in a failed batch whose receipt names no
#: reason (a receipt not written by ``run_shard``).
BATCH_FAILED = "batch failed"


class GoWeContractError(GoWeError):
    """The engine reported success but the outputs do not satisfy the per-item
    receipts contract — no receipts output at all (bulk path), or an archive
    whose ``receipt.json`` is missing or shorter than the item list (user path).

    Deliberately NOT folded into "every item failed": a workflow that emits no
    receipts would otherwise make a fully successful run report every document
    failed (#203 blocker c). This propagates, so the job fails with an explicit
    label an operator can act on (fix the workflow / the output key setting).
    """


class OutputStagingFailed(GoWeError):
    """The run completed but the engine could not deliver its outputs to
    ``output_destination`` (``output_state == upload_failed``, or the
    submission FAILED with ``OUTPUT_STAGING_FAILED``): the data was loaded but
    no archive exists in the Workspace. The job is labelled
    :data:`OUTPUT_STAGING_FAILED` so #353's ``archive_pending`` retry can find it."""


def _receipt_status_to_job(status: str) -> str:
    return JOB_COMPLETED if status == COMPLETED else JOB_FAILED


@dataclass
class GoWeRun:
    """What one submission produced: a result per work item, the submission's
    id + terminal state, and where the engine post-staged the archive."""

    results: list[ItemResult] = field(default_factory=list)
    submission_id: str = ""
    state: str = ""
    #: ``ws://`` URI of the ``versions/<n>/`` folder holding this run's archive
    #: (``""`` when the run had no ``output_destination`` or did not complete).
    archive_ref: str = ""


class GoWeBackend:
    """Run ingest shards on GoWe. Satisfies the ``IngestBackend`` protocol."""

    def __init__(
        self,
        client: GoWeClient,
        workflow_cwl: str,
        *,
        workflow_name: str = "ragstack-bulk-ingest",
        static_inputs: dict[str, Any] | None = None,
        shards_input_key: str = "shards",
        receipts_output_key: str = "receipts",
        worker_group: str | None = None,
        poll_interval: float = 5.0,
        timeout: float = 7200.0,
        output_wait_timeout: float = 600.0,
        workspace: WorkspaceClient | None = None,
        interactive_poll_interval: float = 0.5,
        interactive_max_items: int = 50,
    ) -> None:
        self.client = client
        self.workflow_cwl = workflow_cwl
        self.workflow_name = workflow_name
        self.static_inputs = static_inputs or {}
        self.shards_input_key = shards_input_key
        self.receipts_output_key = receipts_output_key
        # Route tasks to a specific GoWe worker group via a submission label (GoWe
        # reads submission Labels["worker_group"]). Lets ingest land on a dedicated
        # ragstack worker — a `--runtime none` worker in the ragstack env — without
        # a group hint baked into the CWL. See cwl/README.md. Normalize to None so
        # None/""/whitespace all mean "no routing" (a whitespace group would label a
        # group no worker has → every shard fails preflight).
        self.worker_group = (worker_group or "").strip() or None
        self.poll_interval = poll_interval
        # The poll interval is a per-SUBMISSION concern (#203 2b): the setting
        # is the right granularity for a bulk run, but a 3-file browser upload
        # that finishes in 20 s must not wait up to 5 s to learn it. A
        # submission of at most ``interactive_max_items`` items polls every
        # ``interactive_poll_interval`` seconds (never slower than the setting
        # — a 0 configured for tests stays 0).
        self.interactive_poll_interval = interactive_poll_interval
        self.interactive_max_items = interactive_max_items
        self.timeout = timeout
        self.output_wait_timeout = output_wait_timeout
        # Reads the archive's receipt.json on the user path (holds no token).
        self.workspace = workspace

    @staticmethod
    def _file_input(source: str) -> dict[str, str]:
        # A location the engine can stage: ``ws://`` (pre-staged from the
        # Workspace as the submitter) or ``file://`` (under its staging dirs).
        # A bare path is absolutized to file:// for the bulk plane; the API's user
        # path never hands one in (documents.py refuses non-Workspace sources).
        loc = source if "://" in source else f"file://{os.path.abspath(source)}"
        return {"class": "File", "location": loc}

    async def run_shards(
        self, shards: list[list[WorkItem]], shard_fn
    ) -> list[ItemResult]:
        """Submit the shards' source files as a scatter workflow, await receipts,
        and return one ``ItemResult`` per work item. Never raises for an
        engine-side failure: a failed/timed-out submission yields all-failed
        results so the run records the failure rather than aborting. A
        :class:`GoWeContractError` (COMPLETED, but no receipts output) DOES
        propagate — that is a broken workflow contract, not a failed run, and
        reporting it as "every item failed" is the silent corruption #203 names."""
        items = [wi for shard in shards for wi in shard]
        if not items:
            return []
        try:
            run = await self.run_submission(items)
        except GoWeContractError:
            raise
        except GoWeError as e:
            return [self._failed(wi, f"gowe: {e}") for wi in items]
        return run.results

    async def run_submission(
        self,
        items: list[WorkItem],
        *,
        inputs: dict[str, Any] | None = None,
        token: str | None = None,
        output_destination: str | None = None,
        workspace: WorkspaceClient | None = None,
    ) -> GoWeRun:
        """One submission for ``items``: register the workflow, submit
        ``{**static_inputs, **inputs, <shards_input_key>: [File…]}``, wait, map
        receipts. ``token`` (the caller's BV-BRC token) and ``output_destination``
        are passed through to the engine per call and held nowhere.

        With an ``output_destination`` the wait includes delivery (see the
        module docstring), the archive is ``<output_destination>/<version>``
        (``inputs["version"]`` — the Directory output's basename, which the
        engine keeps as the subfolder name) and the receipts are read from
        ``receipt.json`` inside it via ``workspace`` (or the backend's own).
        Without one (the bulk plane) the ``receipts`` File[] output is downloaded
        from the engine.

        Raises :class:`GoWeError` when the engine cannot be driven (register /
        submit / poll failure, timeout, undelivered outputs),
        :class:`OutputStagingFailed` when the engine could not post-stage, and
        :class:`GoWeContractError` when the receipts are missing/short. A
        submission that ends in any other non-COMPLETED terminal state is NOT an
        exception: every item is reported failed with the state, and
        ``GoWeRun.state`` names it.
        """
        if not items:
            return GoWeRun()
        if output_destination:
            # The user path maps results per document by basename; the bulk
            # plane maps its `receipts` output positionally and may repeat one.
            self._refuse_duplicate_names(items)
        job: dict[str, Any] = {
            **self.static_inputs,
            **(inputs or {}),
            self.shards_input_key: [self._file_input(wi.source) for wi in items],
        }
        wf_id = await self.client.register_workflow(
            self.workflow_name, self.workflow_cwl, token=token
        )
        labels = {"worker_group": self.worker_group} if self.worker_group else None
        sub = await self.client.submit(
            wf_id, job, labels=labels, output_destination=output_destination, token=token
        )
        sub_id = str(sub.get("id", ""))
        log.info("gowe: submitted %s (%d item(s)) as workflow %s", sub_id, len(items), wf_id)
        final = await self.client.wait(
            sub_id, poll_interval=self.poll_interval_for(len(items)), timeout=self.timeout,
            token=token,
            require_delivery=bool(output_destination),
            delivery_timeout=self.output_wait_timeout,
        )
        state = str(final.get("state", ""))
        output_state = str(final.get("output_state") or "")
        if output_destination and _staging_failed(final):
            raise OutputStagingFailed(
                f"gowe submission {sub_id} could not deliver its outputs to the "
                f"Workspace (state={state}, output_state={output_state!r}): "
                f"{OUTPUT_STAGING_FAILED}"
            )
        if state != "COMPLETED":
            reason = f"gowe submission {state}"
            return GoWeRun(
                results=[self._failed(wi, reason) for wi in items],
                submission_id=sub_id, state=state,
            )
        if output_destination:
            archive_ref = self._archive_ref(output_destination, job.get("version"))
            results = await self._map_archive_receipts(
                items, archive_ref, token, workspace or self.workspace
            )
            return GoWeRun(results=results, submission_id=sub_id, state=state,
                           archive_ref=archive_ref)
        results = await self._map_outputs(items, final, token=token)
        return GoWeRun(results=results, submission_id=sub_id, state=state)

    @staticmethod
    def _refuse_duplicate_names(items: list[WorkItem]) -> None:
        """Per-document results are matched on source basename (the engine
        pre-stages every input under its basename, so two items sharing one
        would collide at staging anyway); refuse a submission that could not be
        mapped back unambiguously, BEFORE anything is submitted."""
        seen: dict[str, str] = {}
        for wi in items:
            key = _item_key(wi.source)
            if key in seen:
                raise GoWeContractError(
                    f"two work items share the source basename {key!r} "
                    f"({seen[key]} and {wi.item_id}); per-document receipts are "
                    f"matched by basename, so the submission is refused"
                )
            seen[key] = wi.item_id

    def poll_interval_for(self, n_items: int) -> float:
        """The poll interval for a submission of ``n_items`` work items: the
        interactive interval for a small (upload-sized) submission, the
        configured one otherwise — and never slower than the configured one."""
        if n_items <= self.interactive_max_items:
            return min(self.poll_interval, self.interactive_poll_interval)
        return self.poll_interval

    @staticmethod
    def _archive_ref(output_destination: str, version: Any) -> str:
        """Where the ``archive`` Directory landed, from the engine's contract: a
        Directory output is uploaded under its basename, and the basename is the
        version — so ``<output_destination>/<version>``. (The engine rewrites
        only File output locations to ``ws://``; a Directory's is not reported.)"""
        if version in (None, ""):
            raise GoWeContractError(
                "a submission with an output_destination needs a 'version' input — "
                "it names the archive subfolder the engine uploads to"
            )
        return f"{output_destination.rstrip('/')}/{version}"

    async def _map_archive_receipts(
        self,
        items: list[WorkItem],
        archive_ref: str,
        token: str | None,
        workspace: WorkspaceClient | None,
    ) -> list[ItemResult]:
        """Read ``<archive>/receipt.json`` as the caller and map its entries to
        the items **per document**: the pack step writes one ``ShardReceipt``
        per scattered task — under batching (#203 2b) a task is a BATCH of
        PDFs — and each receipt's ``docs`` rows carry the per-document outcome.
        Rows are matched to items by source basename (the engine pre-stages a
        ``ws://`` input under its basename; the extract tool records that path
        on the row). A one-item run's single receipt object (archive.py copies
        it verbatim) is one entry. A missing file, a non-JSON body, a
        non-list/dict shape, or receipts that name NONE of the items is a
        :class:`GoWeContractError`; a malformed entry or an unmatched item
        fails only the items it concerns. See :func:`map_receipt_entries`."""
        if workspace is None:
            raise GoWeContractError(
                "no Workspace client to read the archive's receipts with — GoWeBackend "
                "needs `workspace=` when a submission carries an output_destination"
            )
        path = f"{ws_path(archive_ref)}/{ARCHIVE_RECEIPT_NAME}"
        try:
            raw = await workspace.read_file(token or "", path)
        except WorkspaceError as e:
            raise GoWeContractError(
                f"archive delivered but its receipts could not be read from {path}: {e}"
            ) from None
        try:
            data = json.loads(raw)
        except ValueError as e:
            raise GoWeContractError(f"{path} is not valid JSON: {e}") from None
        entries: list[Any] = [data] if isinstance(data, dict) else data if isinstance(data, list) else []
        if not isinstance(data, dict | list) or not entries:
            raise GoWeContractError(
                f"{path} holds no receipt for {len(items)} work item(s); "
                f"the archive does not satisfy the per-item receipts contract"
            )
        return map_receipt_entries(items, entries, label=path)

    async def _map_outputs(
        self, items: list[WorkItem], submission: dict[str, Any], *, token: str | None = None
    ) -> list[ItemResult]:
        """Bulk plane: map the workflow's ``receipts`` output back to per-item
        results **positionally**. CWL scatter preserves order, so the i-th
        receipt is the i-th submitted item — matching by position (not by
        shard_id/basename) avoids corruption when two items share a filename,
        and works regardless of ingest_shard's ``--shard-id``. An
        unreadable/corrupt receipt (or a length mismatch) fails just that item;
        the run is never aborted for it.

        A COMPLETED submission with NO receipts output is different: that is not
        N failed documents, it is a workflow that cannot report — raised as
        :class:`GoWeContractError` (#203 blocker c)."""
        outputs = submission.get("outputs") or {}
        out = outputs.get(self.receipts_output_key)
        if out is None:
            raise GoWeContractError(
                f"gowe submission {submission.get('id')} COMPLETED but emitted no "
                f"{self.receipts_output_key!r} output (workflow outputs: "
                f"{sorted(outputs)}); the workflow does not satisfy the per-item "
                f"receipts contract, so no per-item result can be reported — check "
                f"gowe_workflow_cwl / gowe_receipts_output_key"
            )
        # A scatter File[] output is a list of {class:File, location}; a single File
        # is one dict.
        entries: list[Any] = out if isinstance(out, list) else [out]
        if len(entries) != len(items):
            log.warning("gowe: %d receipt outputs for %d work items; mapping the "
                        "overlap positionally, the remainder fail",
                        len(entries), len(items))
        results: list[ItemResult] = []
        for i, wi in enumerate(items):
            r = await self._load_receipt(entries[i] if i < len(entries) else None, token)
            if r is None:
                results.append(self._failed(wi, "no readable receipt for shard"))
            else:
                results.append(ItemResult(
                    item_id=wi.item_id, source=wi.source,
                    status=_receipt_status_to_job(r.status),
                    chunk_ids=list(r.chunk_ids), error=r.error,
                ))
        return results

    async def _load_receipt(self, entry: Any, token: str | None) -> ShardReceipt | None:
        """Download + parse one receipt File entry; None on missing/unreadable so
        the caller fails just that item (never raises — the protocol contract)."""
        loc = entry.get("location") if isinstance(entry, dict) else None
        if not loc:
            return None
        try:
            raw = await self.client.download(loc, token=token)
            return ShardReceipt.from_dict(json.loads(raw))
        except (GoWeError, ValueError, json.JSONDecodeError) as e:
            log.warning("gowe: unreadable receipt %s: %s", loc, e)
            return None

    @staticmethod
    def _failed(wi: WorkItem, error: str) -> ItemResult:
        return ItemResult(item_id=wi.item_id, source=wi.source, status=JOB_FAILED,
                          error=error)


def _item_key(source: str) -> str:
    """The name a receipt row is matched on: the basename of the item's source
    (``ws://`` URIs through :func:`ws_path`). The engine pre-stages a Workspace
    file under its basename, so that is the path the extract tool records."""
    path = ws_path(source) if source.startswith("ws://") else source
    return posixpath.basename(path.rstrip("/"))


def _row_result(wi: WorkItem, receipt: ShardReceipt, row: DocRow) -> ItemResult:
    """One document's result from its row (+ its batch's receipt): the row's
    own error verbatim, else the batch error when the batch failed, else
    completed. ``chunk_ids`` are the row's; a receipt written before rows
    carried ids (one document per receipt) falls back to the shard's."""
    error = row.error
    if not error and receipt.status != COMPLETED:
        error = receipt.error or BATCH_FAILED
    chunk_ids = list(row.chunk_ids)
    if not chunk_ids and not error and len(receipt.docs) == 1:
        chunk_ids = list(receipt.chunk_ids)
    return ItemResult(
        item_id=wi.item_id, source=wi.source,
        status=JOB_FAILED if error else JOB_COMPLETED, chunk_ids=chunk_ids, error=error,
    )


def map_receipt_entries(
    items: list[WorkItem], entries: list[Any], *, label: str = "receipt.json"
) -> list[ItemResult]:
    """Map receipt entries (one per scattered task, each a batch) to one
    ``ItemResult`` per work item — by document, not by receipt position.

    1. Every readable receipt's ``docs`` rows are indexed by source basename.
    2. Each item is looked up by its own source basename: a hit yields that
       document's status/chunk ids/error (a failed batch's error reaches every
       document of the batch through the row — ``run_shard`` writes it there —
       or, for a receipt that did not, through the receipt's ``error``).
    3. An item with no row falls back to POSITIONAL mapping only when there is
       exactly one entry per item (an Option-A archive, one receipt per PDF);
       otherwise it fails with :data:`NO_RECEIPT_ENTRY`.

    Raises :class:`GoWeContractError` when the receipts name none of the
    items and cannot be mapped positionally either — that is a workflow that
    cannot report, not N failed documents (#203 blocker c).
    """
    receipts: list[ShardReceipt | None] = []
    by_name: dict[str, tuple[ShardReceipt, DocRow]] = {}
    for entry in entries:
        try:
            r = ShardReceipt.from_dict(entry)
        except (ValueError, TypeError, KeyError, AttributeError) as e:
            log.warning("gowe: unreadable receipt entry in %s: %s", label, type(e).__name__)
            receipts.append(None)
            continue
        receipts.append(r)
        for row in r.docs:
            key = posixpath.basename(row.source.rstrip("/")) if row.source else ""
            if not key:
                continue
            if key in by_name:
                log.warning("gowe: duplicate document name %r in %s; keeping the first",
                            key, label)
                continue
            by_name[key] = (r, row)

    positional = len(entries) == len(items)
    results: list[ItemResult] = []
    matched = 0
    for i, wi in enumerate(items):
        hit = by_name.get(_item_key(wi.source))
        if hit is not None:
            matched += 1
            results.append(_row_result(wi, *hit))
            continue
        if positional:
            rec = receipts[i]
            if rec is None:
                results.append(GoWeBackend._failed(wi, NO_READABLE_RECEIPT))
            else:
                results.append(ItemResult(
                    item_id=wi.item_id, source=wi.source,
                    status=_receipt_status_to_job(rec.status),
                    chunk_ids=list(rec.chunk_ids), error=rec.error,
                ))
            continue
        results.append(GoWeBackend._failed(wi, NO_RECEIPT_ENTRY))
    if matched == 0 and not positional:
        raise GoWeContractError(
            f"{label} holds {len(entries)} receipt(s) naming none of the {len(items)} "
            f"work item(s); the archive does not satisfy the per-item receipts contract"
        )
    if matched < len(items):
        log.warning("gowe: %d of %d work items have no receipt row in %s%s",
                    len(items) - matched, len(items), label,
                    " (mapped positionally)" if positional else "")
    return results


def _staging_failed(submission: dict[str, Any]) -> bool:
    """Did post-staging fail? Either the stager recorded ``output_state ==
    upload_failed`` on a COMPLETED submission, or the submission itself was
    FAILED with the engine's ``OUTPUT_STAGING_FAILED`` label in its error text."""
    if str(submission.get("output_state") or "") == OUTPUT_UPLOAD_FAILED:
        return True
    if str(submission.get("state", "")) != "FAILED":
        return False
    # The engine's shape (pkg/model/submission.go): ``error`` is a
    # ``SubmissionError`` object ``{code, message, context}`` and post-staging
    # failure sets ``code == "OUTPUT_STAGING_FAILED"`` (scheduler/workspace.go).
    err = submission.get("error")
    if isinstance(err, dict) and str(err.get("code") or "") == OUTPUT_STAGING_FAILED:
        return True
    # Defensive fallback for a flat-string error field.
    text = " ".join(
        str(submission.get(k) or "")
        for k in ("error", "error_message", "failure_reason", "message", "reason")
    )
    return OUTPUT_STAGING_FAILED in text
