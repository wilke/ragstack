"""Tests for GoWeBackend — the IngestBackend that runs shards on GoWe.

Uses a fake GoWeClient (no server): the tested core is submit→wait→download-
receipts→map-to-ItemResults, plus the engine-failure paths.
"""
from __future__ import annotations

import pytest

from ragstack.ingestion.gowe_backend import GoWeBackend
from ragstack.ingestion.gowe_client import GoWeError
from ragstack.ingestion.manifest import WorkItem
from ragstack.ingestion.receipts import COMPLETED, FAILED, ShardReceipt


def _wi(item_id: str, source: str) -> WorkItem:
    return WorkItem(item_id=item_id, source=source)


class _FakeClient:
    """register/submit/wait/download stub. ``receipts`` maps a File location to a
    ShardReceipt; ``wait`` returns them as the workflow's ``receipts`` output."""

    def __init__(self, receipts: dict[str, ShardReceipt], *, state: str = "COMPLETED",
                 raise_on_submit: bool = False) -> None:
        self._receipts = receipts
        self._state = state
        self._raise = raise_on_submit
        self.submitted_inputs = None

    async def register_workflow(self, name, cwl, labels=None) -> str:
        return "wf_fake"

    async def submit(self, wf_id, inputs, **kw):
        if self._raise:
            raise GoWeError("submit blew up")
        self.submitted_inputs = inputs
        return {"id": "sub_fake", "state": "PENDING"}

    async def wait(self, sub_id, **kw):
        outputs = {"receipts": [{"class": "File", "location": loc}
                                for loc in self._receipts]}
        return {"id": sub_id, "state": self._state, "outputs": outputs}

    async def download(self, location) -> bytes:
        return self._receipts[location].to_json().encode()


def _backend(client) -> GoWeBackend:
    return GoWeBackend(client, "cwlVersion: v1.2", poll_interval=0, timeout=1)


@pytest.mark.asyncio
async def test_maps_receipts_to_item_results() -> None:
    receipts = {
        "file:///d/s0.jsonl.r": ShardReceipt("s0.jsonl", "public", COMPLETED,
                                             n_docs=3, n_chunks=2, chunk_ids=["a", "b"]),
        "file:///d/s1.jsonl.r": ShardReceipt("s1.jsonl", "public", FAILED,
                                             n_docs=1, n_chunks=0, error="boom"),
    }
    backend = _backend(_FakeClient(receipts))
    items = [_wi("i0", "/data/s0.jsonl"), _wi("i1", "/data/s1.jsonl")]
    results = await backend.run_shards([items], shard_fn=None)

    by_item = {r.item_id: r for r in results}
    assert by_item["i0"].status == "completed" and by_item["i0"].chunk_ids == ["a", "b"]
    assert by_item["i1"].status == "failed" and by_item["i1"].error == "boom"


@pytest.mark.asyncio
async def test_submits_shard_files_as_file_inputs() -> None:
    client = _FakeClient({})
    backend = _backend(client)
    await backend.run_shards([[_wi("i0", "/data/s0.jsonl")]], shard_fn=None)
    shards = client.submitted_inputs["shards"]
    assert shards == [{"class": "File", "location": "file:///data/s0.jsonl"}]


@pytest.mark.asyncio
async def test_failed_submission_marks_all_items_failed() -> None:
    backend = _backend(_FakeClient({}, state="FAILED"))
    items = [_wi("i0", "/d/s0.jsonl"), _wi("i1", "/d/s1.jsonl")]
    results = await backend.run_shards([items], shard_fn=None)
    assert all(r.status == "failed" for r in results)
    assert all("FAILED" in r.error for r in results)


@pytest.mark.asyncio
async def test_submit_error_marks_all_failed() -> None:
    backend = _backend(_FakeClient({}, raise_on_submit=True))
    results = await backend.run_shards([[_wi("i0", "/d/s0.jsonl")]], shard_fn=None)
    assert results[0].status == "failed" and "gowe:" in results[0].error


@pytest.mark.asyncio
async def test_missing_receipt_marks_item_failed() -> None:
    # completed submission but no receipt for the shard -> that item fails
    backend = _backend(_FakeClient({}))  # empty receipts
    results = await backend.run_shards([[_wi("i0", "/d/s0.jsonl")]], shard_fn=None)
    assert results[0].status == "failed" and "no receipt" in results[0].error


@pytest.mark.asyncio
async def test_empty_shards_returns_empty() -> None:
    backend = _backend(_FakeClient({}))
    assert await backend.run_shards([], shard_fn=None) == []
