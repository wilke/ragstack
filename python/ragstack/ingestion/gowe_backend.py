"""``GoWeBackend`` — an ``IngestBackend`` that runs shards on GoWe (ADR-0001 2b).

Drops in where ``LocalAsyncIORunner`` runs shards in-process: instead, it submits
a scatter workflow to the GoWe engine (via ``GoWeClient``), waits for it, downloads
the per-shard receipts, and maps them back to ``ItemResult``s. The engine owns
scatter/retry/resume; ``shard_fn`` is ignored — the per-shard CLI *tool*, not an
in-process callable, is the unit of work (ADR-0001 Appendix A).

Each ``WorkItem.source`` is a shard input file (a JSONL shard) that GoWe's workers
can read — i.e. a path under the server's allowed staging dirs. The workflow's
per-shard ingest config (collection, embedding endpoints, chunk method) is fixed
for the run and supplied as ``static_inputs`` at construction.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from ragstack.ingestion.gowe_client import GoWeClient, GoWeError
from ragstack.ingestion.manifest import ItemResult, WorkItem
from ragstack.ingestion.receipts import COMPLETED, ShardReceipt
from ragstack.jobstore import COMPLETED as JOB_COMPLETED
from ragstack.jobstore import FAILED as JOB_FAILED

log = logging.getLogger(__name__)


def _receipt_status_to_job(status: str) -> str:
    return JOB_COMPLETED if status == COMPLETED else JOB_FAILED


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
        poll_interval: float = 5.0,
        timeout: float = 7200.0,
    ) -> None:
        self.client = client
        self.workflow_cwl = workflow_cwl
        self.workflow_name = workflow_name
        self.static_inputs = static_inputs or {}
        self.shards_input_key = shards_input_key
        self.receipts_output_key = receipts_output_key
        self.poll_interval = poll_interval
        self.timeout = timeout

    @staticmethod
    def _file_input(source: str) -> dict[str, str]:
        # GoWe wants a file:// location its workers can read. Absolute-ize a bare path.
        loc = source if "://" in source else f"file://{os.path.abspath(source)}"
        return {"class": "File", "location": loc}

    async def run_shards(
        self, shards: list[list[WorkItem]], shard_fn
    ) -> list[ItemResult]:
        """Submit the shards' source files as a scatter workflow, await receipts,
        and return one ``ItemResult`` per work item. Never raises for an engine-side
        failure: a failed/timed-out submission yields all-failed results so the run
        records the failure rather than aborting."""
        items = [wi for shard in shards for wi in shard]
        if not items:
            return []
        inputs = {
            **self.static_inputs,
            self.shards_input_key: [self._file_input(wi.source) for wi in items],
        }
        try:
            wf_id = await self.client.register_workflow(self.workflow_name, self.workflow_cwl)
            sub = await self.client.submit(wf_id, inputs)
            final = await self.client.wait(
                sub["id"], poll_interval=self.poll_interval, timeout=self.timeout
            )
        except GoWeError as e:
            return [self._failed(wi, f"gowe: {e}") for wi in items]

        if final.get("state") != "COMPLETED":
            reason = f"gowe submission {final.get('state')}"
            return [self._failed(wi, reason) for wi in items]

        return await self._map_outputs(items, final)

    async def _map_outputs(
        self, items: list[WorkItem], submission: dict[str, Any]
    ) -> list[ItemResult]:
        """Map the workflow's ``receipts`` output back to per-item results
        **positionally**. CWL scatter preserves order, so the i-th receipt is the
        i-th submitted shard — matching by position (not by shard_id/basename)
        avoids corruption when two shards share a filename, and works regardless of
        ingest_shard's ``--shard-id``. An unreadable/corrupt receipt (or a length
        mismatch) fails just that item; the run is never aborted."""
        out = (submission.get("outputs") or {}).get(self.receipts_output_key)
        # A scatter File[] output is a list of {class:File, location}; a single File
        # is one dict; absent is None.
        entries: list[Any] = [] if out is None else (out if isinstance(out, list) else [out])
        if len(entries) != len(items):
            log.warning("gowe: %d receipt outputs for %d work items; mapping the "
                        "overlap positionally, the remainder fail",
                        len(entries), len(items))
        results: list[ItemResult] = []
        for i, wi in enumerate(items):
            r = await self._load_receipt(entries[i] if i < len(entries) else None)
            if r is None:
                results.append(self._failed(wi, "no readable receipt for shard"))
            else:
                results.append(ItemResult(
                    item_id=wi.item_id, source=wi.source,
                    status=_receipt_status_to_job(r.status),
                    chunk_ids=list(r.chunk_ids), error=r.error,
                ))
        return results

    async def _load_receipt(self, entry: Any) -> ShardReceipt | None:
        """Download + parse one receipt File entry; None on missing/unreadable so
        the caller fails just that item (never raises — the protocol contract)."""
        loc = entry.get("location") if isinstance(entry, dict) else None
        if not loc:
            return None
        try:
            raw = await self.client.download(loc)
            return ShardReceipt.from_dict(json.loads(raw))
        except (GoWeError, ValueError, json.JSONDecodeError) as e:
            log.warning("gowe: unreadable receipt %s: %s", loc, e)
            return None

    @staticmethod
    def _failed(wi: WorkItem, error: str) -> ItemResult:
        return ItemResult(item_id=wi.item_id, source=wi.source, status=JOB_FAILED,
                          error=error)
