"""``GoWeBackend`` — an ``IngestBackend`` that runs shards on GoWe (ADR-0001 2b).

Drops in where ``LocalAsyncIORunner`` runs shards in-process: instead, it submits
a scatter workflow to the GoWe engine (via ``GoWeClient``), waits for it, downloads
the per-item receipts, and maps them back to ``ItemResult``s. The engine owns
scatter/retry/resume; ``shard_fn`` is ignored — the per-item CLI *tool*, not an
in-process callable, is the unit of work (ADR-0001 Appendix A).

Two callers, one submission path (:meth:`GoWeBackend.run_submission`):

* the offline bulk plane — :meth:`run_shards` (the ``IngestBackend`` protocol),
  where each ``WorkItem.source`` is a pre-built JSONL shard under the engine's
  staging dirs and the client's own operator token authenticates; and
* the user ingest path (#203/#353) — the API calls :meth:`run_submission`
  directly with ``ws://`` Workspace sources, the CALLER's token and an
  ``output_destination`` in the caller's Workspace, so the engine pre-stages the
  inputs and post-stages the archive as that user. The token is a per-call
  argument: it is never held on the backend or the client, and it reaches
  exactly one place — the ``Authorization`` header of each engine request.

The workflow's per-item ingest config (collection, embedding endpoints, chunk
method, …) is fixed for the run: ``static_inputs`` at construction, overridden
per submission by ``inputs``.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from ragstack.ingestion.gowe_client import GoWeClient, GoWeError
from ragstack.ingestion.manifest import ItemResult, WorkItem
from ragstack.ingestion.receipts import COMPLETED, ShardReceipt
from ragstack.jobstore import COMPLETED as JOB_COMPLETED
from ragstack.jobstore import FAILED as JOB_FAILED

log = logging.getLogger(__name__)

#: The workflow-level ``Directory`` output the engine post-stages to
#: ``<output_destination>/<basename>/`` (cwl/pdf-ingest-scatter.cwl, #357).
ARCHIVE_OUTPUT_KEY = "archive"


class GoWeContractError(GoWeError):
    """The engine reported COMPLETED but the workflow's outputs do not satisfy
    the per-item receipts contract — e.g. no ``receipts`` output at all.

    Deliberately NOT folded into "every item failed": a workflow that emits no
    receipts would otherwise make a fully successful run report every document
    failed (#203 blocker c). This propagates, so the job fails with an explicit
    label an operator can act on (fix the workflow / the output key setting).
    """


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
        self.timeout = timeout

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
    ) -> GoWeRun:
        """One submission for ``items``: register the workflow, submit
        ``{**static_inputs, **inputs, <shards_input_key>: [File…]}``, wait, map
        receipts. ``token`` (the caller's BV-BRC token) and ``output_destination``
        are passed through to the engine per call and held nowhere.

        Raises :class:`GoWeError` when the engine cannot be driven (register /
        submit / poll failure, timeout) and :class:`GoWeContractError` when a
        COMPLETED submission has no receipts output. A submission that ends in a
        non-COMPLETED terminal state is NOT an exception: every item is reported
        failed with the state, and ``GoWeRun.state`` names it.
        """
        if not items:
            return GoWeRun()
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
            sub_id, poll_interval=self.poll_interval, timeout=self.timeout, token=token
        )
        state = str(final.get("state", ""))
        if state != "COMPLETED":
            reason = f"gowe submission {state}"
            return GoWeRun(
                results=[self._failed(wi, reason) for wi in items],
                submission_id=sub_id, state=state,
            )
        results = await self._map_outputs(items, final, token=token)
        archive_ref = self._archive_ref(final, output_destination, job.get("version"))
        return GoWeRun(results=results, submission_id=sub_id, state=state,
                       archive_ref=archive_ref)

    @staticmethod
    def _archive_ref(
        submission: dict[str, Any], output_destination: str | None, version: Any
    ) -> str:
        """Where the ``archive`` Directory landed. Prefer a ``ws://`` location the
        engine reports on the output; otherwise derive it from the engine's
        contract — a Directory output is uploaded under its basename, and the
        basename is the version — so ``<output_destination>/<version>``."""
        out = (submission.get("outputs") or {}).get(ARCHIVE_OUTPUT_KEY)
        loc = out.get("location") if isinstance(out, dict) else None
        if isinstance(loc, str) and loc.startswith("ws://"):
            return loc.rstrip("/")
        if output_destination and version not in (None, ""):
            return f"{output_destination.rstrip('/')}/{version}"
        return ""

    async def _map_outputs(
        self, items: list[WorkItem], submission: dict[str, Any], *, token: str | None = None
    ) -> list[ItemResult]:
        """Map the workflow's ``receipts`` output back to per-item results
        **positionally**. CWL scatter preserves order, so the i-th receipt is the
        i-th submitted item — matching by position (not by shard_id/basename)
        avoids corruption when two items share a filename, and works regardless
        of ingest_shard's ``--shard-id``. An unreadable/corrupt receipt (or a
        length mismatch) fails just that item; the run is never aborted for it.

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
        the caller fails just that item. A receipt at a location the engine's
        download endpoint cannot serve (anything but ``file://``) is a contract
        problem for the whole run, not one bad document — raised, not degraded."""
        loc = entry.get("location") if isinstance(entry, dict) else None
        if not loc:
            return None
        if not str(loc).startswith("file://"):
            raise GoWeContractError(
                f"receipt output at unsupported location {str(loc)[:120]!r}: only "
                f"file:// locations are downloadable from the engine"
            )
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
