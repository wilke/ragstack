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
import os
from typing import Any

from ragstack.ingestion.gowe_client import GoWeClient, GoWeError
from ragstack.ingestion.manifest import ItemResult, WorkItem
from ragstack.ingestion.receipts import COMPLETED, ShardReceipt
from ragstack.jobstore import COMPLETED as JOB_COMPLETED
from ragstack.jobstore import FAILED as JOB_FAILED


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

        receipts = await self._download_receipts(final)
        return self._map_results(items, receipts)

    async def _download_receipts(self, submission: dict[str, Any]) -> list[ShardReceipt]:
        out = (submission.get("outputs") or {}).get(self.receipts_output_key)
        if out is None:
            return []
        # A scatter File[] output is a list of {class:File, location}; a single File
        # is one dict. Normalise to a list.
        entries = out if isinstance(out, list) else [out]
        receipts: list[ShardReceipt] = []
        for entry in entries:
            loc = entry.get("location") if isinstance(entry, dict) else None
            if not loc:
                continue
            raw = await self.client.download(loc)
            receipts.append(ShardReceipt.from_dict(json.loads(raw)))
        return receipts

    def _map_results(
        self, items: list[WorkItem], receipts: list[ShardReceipt]
    ) -> list[ItemResult]:
        # Match a shard receipt to its work item by shard_id == the source's
        # basename (what ingest_shard defaults the shard_id to). An item with no
        # receipt (the engine dropped/never ran it) is reported failed.
        by_id = {r.shard_id: r for r in receipts}
        results: list[ItemResult] = []
        for wi in items:
            r = by_id.get(os.path.basename(wi.source)) or by_id.get(wi.item_id)
            if r is None:
                results.append(self._failed(wi, "no receipt returned for shard"))
            else:
                results.append(ItemResult(
                    item_id=wi.item_id, source=wi.source,
                    status=_receipt_status_to_job(r.status),
                    chunk_ids=list(r.chunk_ids), error=r.error,
                ))
        return results

    @staticmethod
    def _failed(wi: WorkItem, error: str) -> ItemResult:
        return ItemResult(item_id=wi.item_id, source=wi.source, status=JOB_FAILED,
                          error=error)
