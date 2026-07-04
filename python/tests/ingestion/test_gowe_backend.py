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
        self.submitted_labels = None

    async def register_workflow(self, name, cwl, labels=None) -> str:
        return "wf_fake"

    async def submit(self, wf_id, inputs, *, labels=None, **kw):
        if self._raise:
            raise GoWeError("submit blew up")
        self.submitted_inputs = inputs
        self.submitted_labels = labels
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
async def test_worker_group_routed_via_submission_label() -> None:
    client = _FakeClient({})
    backend = GoWeBackend(client, "cwlVersion: v1.2", worker_group="ragstack-cpu",
                          poll_interval=0, timeout=1)
    await backend.run_shards([[_wi("i0", "/d/s0.jsonl")]], shard_fn=None)
    assert client.submitted_labels == {"worker_group": "ragstack-cpu"}


@pytest.mark.asyncio
async def test_no_worker_group_sends_no_label() -> None:
    client = _FakeClient({})
    await _backend(client).run_shards([[_wi("i0", "/d/s0.jsonl")]], shard_fn=None)
    assert client.submitted_labels is None


@pytest.mark.parametrize("group", ["", "   "])
@pytest.mark.asyncio
async def test_blank_worker_group_normalised_to_no_label(group) -> None:
    # "" / whitespace must NOT label a nonexistent group (which would fail every
    # shard at preflight) — normalized to None.
    client = _FakeClient({})
    backend = GoWeBackend(client, "cwlVersion: v1.2", worker_group=group,
                          poll_interval=0, timeout=1)
    assert backend.worker_group is None
    await backend.run_shards([[_wi("i0", "/d/s0.jsonl")]], shard_fn=None)
    assert client.submitted_labels is None


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
    assert results[0].status == "failed" and "no readable receipt" in results[0].error


@pytest.mark.asyncio
async def test_same_basename_shards_dont_collide() -> None:
    # Two shards with the SAME basename in different dirs. The old basename-keyed
    # mapping collapsed them (one result lost); positional mapping keeps them
    # distinct — both receipts have shard_id "s0.jsonl" yet map to the right item.
    receipts = {
        "file:///a/s0.r": ShardReceipt("s0.jsonl", "public", COMPLETED,
                                       n_chunks=1, chunk_ids=["A"]),
        "file:///b/s0.r": ShardReceipt("s0.jsonl", "public", FAILED, error="B-failed"),
    }
    backend = _backend(_FakeClient(receipts))
    items = [_wi("iA", "/a/s0.jsonl"), _wi("iB", "/b/s0.jsonl")]
    results = await backend.run_shards([items], shard_fn=None)
    by_item = {r.item_id: r for r in results}
    assert by_item["iA"].status == "completed" and by_item["iA"].chunk_ids == ["A"]
    assert by_item["iB"].status == "failed" and by_item["iB"].error == "B-failed"


@pytest.mark.asyncio
async def test_empty_shards_returns_empty() -> None:
    backend = _backend(_FakeClient({}))
    assert await backend.run_shards([], shard_fn=None) == []


class _BadReceiptClient(_FakeClient):
    """Submission COMPLETED, but the receipt download errors — run_shards must NOT
    raise (the item degrades to failed)."""

    async def wait(self, sub_id, **kw):
        return {"id": sub_id, "state": "COMPLETED",
                "outputs": {"receipts": [{"class": "File", "location": "file:///d/x.r"}]}}

    async def download(self, location) -> bytes:
        raise GoWeError("download 404")


@pytest.mark.asyncio
async def test_unreadable_receipt_degrades_not_raises() -> None:
    backend = _backend(_BadReceiptClient({}))
    results = await backend.run_shards([[_wi("i0", "/d/s0.jsonl")]], shard_fn=None)
    assert results[0].status == "failed" and "no readable receipt" in results[0].error


class _MalformedReceiptClient(_FakeClient):
    async def wait(self, sub_id, **kw):
        return {"id": sub_id, "state": "COMPLETED",
                "outputs": {"receipts": [{"class": "File", "location": "file:///d/x.r"}]}}

    async def download(self, location) -> bytes:
        return b"{not valid json"


@pytest.mark.asyncio
async def test_malformed_receipt_degrades_not_raises() -> None:
    backend = _backend(_MalformedReceiptClient({}))
    results = await backend.run_shards([[_wi("i0", "/d/s0.jsonl")]], shard_fn=None)
    assert results[0].status == "failed"  # bad JSON skipped → no receipt → failed


class _SingleFileReceiptClient(_FakeClient):
    """A non-scattered workflow can return `receipts` as a single File dict, not a
    list — the backend must normalise it."""

    async def wait(self, sub_id, **kw):
        (loc, _r) = next(iter(self._receipts.items()))
        return {"id": sub_id, "state": "COMPLETED",
                "outputs": {"receipts": {"class": "File", "location": loc}}}


@pytest.mark.asyncio
async def test_single_file_receipts_output_normalised() -> None:
    receipts = {"file:///d/s0.r": ShardReceipt("s0.jsonl", "public", COMPLETED,
                                               n_docs=1, n_chunks=1, chunk_ids=["a"])}
    backend = _backend(_SingleFileReceiptClient(receipts))
    results = await backend.run_shards([[_wi("i0", "/data/s0.jsonl")]], shard_fn=None)
    assert results[0].status == "completed" and results[0].chunk_ids == ["a"]
