"""Tests for make_ingest_backend — the composition-root seam that selects the
in-process (local) vs GoWe distribution backend from settings — plus an
end-to-end ShardedIngestor→GoWeBackend run over a fake GoWe client."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from ragstack.ingestion.backends import (
    IngestBackend,
    LocalAsyncIORunner,
    make_ingest_backend,
)
from ragstack.ingestion.gowe_backend import GoWeBackend
from ragstack.ingestion.manifest import Manifest, WorkItem
from ragstack.ingestion.receipts import COMPLETED, FAILED, ShardReceipt
from ragstack.ingestion.sharded import ShardedIngestor
from ragstack.jobstore import COMPLETED as JOB_COMPLETED
from ragstack.jobstore import FAILED as JOB_FAILED


def _settings(**over):
    base = {
        "ingest_backend": "local",
        "ingest_concurrency": 4,
        "gowe_url": "http://localhost:8091",
        "gowe_token": "",
        "gowe_workflow_cwl": "",
        "gowe_workflow_name": "ragstack-bulk-ingest",
        "gowe_workflow_inputs_json": "{}",
        "gowe_worker_group": "",
        "gowe_poll_interval": 5.0,
        "gowe_timeout": 7200.0,
    }
    base.update(over)
    return SimpleNamespace(**base)


# --- backend selection ------------------------------------------------------- #

def test_default_is_local():
    b = make_ingest_backend(_settings())
    assert isinstance(b, LocalAsyncIORunner)
    assert isinstance(b, IngestBackend)


def test_unknown_backend_raises():
    with pytest.raises(ValueError, match="unknown ingest_backend"):
        make_ingest_backend(_settings(ingest_backend="parsl"))


def test_gowe_requires_workflow_cwl():
    with pytest.raises(ValueError, match="requires gowe_workflow_cwl"):
        make_ingest_backend(_settings(ingest_backend="gowe"))


def test_gowe_unreadable_cwl_raises(tmp_path):
    missing = tmp_path / "nope.cwl"
    with pytest.raises(ValueError, match="unreadable"):
        make_ingest_backend(_settings(ingest_backend="gowe", gowe_workflow_cwl=str(missing)))


def test_gowe_bad_inputs_json_raises(tmp_path):
    cwl = tmp_path / "wf.cwl"
    cwl.write_text("cwlVersion: v1.2\nclass: Workflow\n")
    with pytest.raises(ValueError, match="not valid JSON"):
        make_ingest_backend(_settings(
            ingest_backend="gowe", gowe_workflow_cwl=str(cwl),
            gowe_workflow_inputs_json="{not json",
        ))


def test_gowe_non_object_inputs_json_raises(tmp_path):
    cwl = tmp_path / "wf.cwl"
    cwl.write_text("cwlVersion: v1.2\n")
    with pytest.raises(ValueError, match="must be a JSON object"):
        make_ingest_backend(_settings(
            ingest_backend="gowe", gowe_workflow_cwl=str(cwl),
            gowe_workflow_inputs_json="[1, 2]",
        ))


def test_gowe_backend_built_with_static_inputs(tmp_path):
    cwl = tmp_path / "ingest-bulk.cwl"
    cwl.write_text("cwlVersion: v1.2\nclass: Workflow\n")
    b = make_ingest_backend(_settings(
        ingest_backend="gowe",
        gowe_workflow_cwl=str(cwl),
        gowe_workflow_name="my-wf",
        gowe_workflow_inputs_json='{"collection": "ragstack_sfr_tok256", "tenant": "public"}',
        gowe_worker_group="  ragstack-cpu  ",
    ))
    assert isinstance(b, GoWeBackend) and isinstance(b, IngestBackend)
    assert b.workflow_cwl.startswith("cwlVersion")  # file content, not the path
    assert b.workflow_name == "my-wf"
    assert b.static_inputs == {"collection": "ragstack_sfr_tok256", "tenant": "public"}
    assert b.worker_group == "ragstack-cpu"  # normalized (stripped)


# --- ShardedIngestor drives a GoWeBackend end-to-end ------------------------- #

class _FakeClient:
    def __init__(self, receipts):
        self._receipts = receipts  # dict: File location -> ShardReceipt (in item order)

    async def register_workflow(self, name, cwl, labels=None):
        return "wf_fake"

    async def submit(self, wf_id, inputs, *, labels=None, **kw):
        self.inputs = inputs
        return {"id": "sub_fake", "state": "PENDING"}

    async def wait(self, sub_id, **kw):
        return {"id": sub_id, "state": "COMPLETED",
                "outputs": {"receipts": [{"class": "File", "location": loc}
                                         for loc in self._receipts]}}

    async def download(self, location):
        return self._receipts[location].to_json().encode()


@pytest.mark.asyncio
async def test_sharded_ingestor_over_gowe_backend(tmp_path):
    receipts = {
        "file:///d/s0.r": ShardReceipt("s0", "public", COMPLETED, n_chunks=2,
                                       chunk_ids=["a", "b"]),
        "file:///d/s1.r": ShardReceipt("s1", "public", FAILED, error="boom"),
    }
    backend = GoWeBackend(_FakeClient(receipts), "cwlVersion: v1.2", poll_interval=0,
                          timeout=1)
    ingestor = ShardedIngestor(pipeline=object(), backend=backend, shard_size=64)

    manifest = Manifest(items=[
        WorkItem(item_id="s0", source="/scout/wf/data/s0.jsonl"),
        WorkItem(item_id="s1", source="/scout/wf/data/s1.jsonl"),
    ])
    results = await ingestor.ingest_manifest(manifest, tenant_id="public")

    assert [r.item_id for r in results] == ["s0", "s1"]
    assert results[0].status == JOB_COMPLETED and results[0].chunk_ids == ["a", "b"]
    assert results[1].status == JOB_FAILED and results[1].error == "boom"
