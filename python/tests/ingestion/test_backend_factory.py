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


@pytest.mark.parametrize("blob, named", [
    ('{"qdrant_url": "http://evil:6333"}', "qdrant_url"),
    ('{"es_url": "http://evil:9200"}', "es_url"),
    ('{"es_url": "http://e:9200", "qdrant_url": "http://e:6333"}', "es_url, qdrant_url"),
])
def test_gowe_inputs_json_may_not_set_store_targets(tmp_path, blob, named):
    """#407: the ingest router seeds ``qdrant_url``/``es_url`` per run from
    QDRANT_URL / ELASTICSEARCH_URL, and a per-run input wins GoWeBackend's merge
    — so these keys in the blob are INERT. Refuse at boot rather than let an
    operator run for months believing config that does nothing is steering their
    writes: inert config believed live is this very defect family (#407 is its
    fourth instance). The message must name the remedy, because the operator who
    hits this is mid-deploy."""
    cwl = tmp_path / "wf.cwl"
    cwl.write_text("cwlVersion: v1.2\nclass: Workflow\n")
    with pytest.raises(ValueError) as exc:
        make_ingest_backend(_settings(
            ingest_backend="gowe", gowe_workflow_cwl=str(cwl),
            gowe_workflow_inputs_json=blob,
        ))
    msg = str(exc.value)
    assert f"may not set {named}" in msg
    assert "QDRANT_URL" in msg and "ELASTICSEARCH_URL" in msg


def test_gowe_inputs_json_still_accepts_genuine_extras(tmp_path):
    """Control for the refusal above — it must reject the reserved keys, not the
    blob. A refusal that rejected any non-empty blob would pass the test above."""
    cwl = tmp_path / "wf.cwl"
    cwl.write_text("cwlVersion: v1.2\nclass: Workflow\n")
    b = make_ingest_backend(_settings(
        ingest_backend="gowe", gowe_workflow_cwl=str(cwl),
        gowe_workflow_inputs_json='{"batch_size": 20, "qdrant_urls": "not-reserved"}',
    ))
    assert b.static_inputs == {"batch_size": 20, "qdrant_urls": "not-reserved"}


def test_local_backend_ignores_a_store_target_in_the_blob(tmp_path):
    """The refusal is scoped to the gowe backend, where the blob is read at all.

    Blast radius check (#407): of the four deployed tenants only ``dev`` sets
    ``INGEST_BACKEND=gowe``, so an upgrade cannot brick a tenant whose blob is a
    leftover but whose backend is local."""
    assert isinstance(
        make_ingest_backend(_settings(
            gowe_workflow_inputs_json='{"qdrant_url": "http://evil:6333"}')),
        LocalAsyncIORunner,
    )


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

    async def register_workflow(self, name, cwl, labels=None, **kw):
        return "wf_fake"

    async def submit(self, wf_id, inputs, *, labels=None, **kw):
        self.inputs = inputs
        return {"id": "sub_fake", "state": "PENDING"}

    async def wait(self, sub_id, **kw):
        return {"id": sub_id, "state": "COMPLETED",
                "outputs": {"receipts": [{"class": "File", "location": loc}
                                         for loc in self._receipts]}}

    async def download(self, location, **kw):
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


# --- field-contract against the REAL Settings (guards future drift) ---------- #

def test_real_settings_builds_local():
    from ragstack.config import Settings

    b = make_ingest_backend(Settings(ingest_backend="local", ingest_concurrency=4))
    assert isinstance(b, LocalAsyncIORunner) and b._max == 4


def test_real_settings_builds_gowe(tmp_path):
    from ragstack.config import Settings

    cwl = tmp_path / "wf.cwl"
    cwl.write_text("cwlVersion: v1.2\n")
    b = make_ingest_backend(Settings(
        ingest_backend="gowe", gowe_workflow_cwl=str(cwl),
        gowe_workflow_inputs_json='{"collection": "c"}',
    ))
    assert isinstance(b, GoWeBackend) and b.static_inputs == {"collection": "c"}


def test_local_wires_concurrency_from_settings():
    b = make_ingest_backend(_settings(ingest_concurrency=7))
    assert b._max == 7  # not a hardcoded default


def test_gowe_carries_poll_and_timeout(tmp_path):
    cwl = tmp_path / "wf.cwl"
    cwl.write_text("cwlVersion: v1.2\n")
    b = make_ingest_backend(_settings(
        ingest_backend="gowe", gowe_workflow_cwl=str(cwl),
        gowe_poll_interval=1.5, gowe_timeout=99.0,
    ))
    assert b.poll_interval == 1.5 and b.timeout == 99.0


def test_gowe_unset_worker_group_is_none(tmp_path):
    cwl = tmp_path / "wf.cwl"
    cwl.write_text("cwlVersion: v1.2\n")
    b = make_ingest_backend(_settings(ingest_backend="gowe", gowe_workflow_cwl=str(cwl)))
    assert b.worker_group is None  # "" normalized to None (no phantom label)


# --- /v1/ingest guard in a non-local backend --------------------------------- #

def _call_ingest(docs, principal):
    return docs.ingest(
        request=SimpleNamespace(source="/data/doc.pdf", collection=None),
        http_request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace())),
        background_tasks=SimpleNamespace(add_task=lambda *a, **k: None),
        tenant="public", principal=principal, ingestor=object(), job_store=object(),
        collections=object(),
    )


@pytest.mark.asyncio
async def test_ingest_endpoint_rejects_unknown_backend(monkeypatch):
    """The 501 guard survives for any backend this router cannot drive."""
    from fastapi import HTTPException

    from ragstack.api.routers import documents as docs
    from ragstack.api.security import Principal

    monkeypatch.setattr(docs.settings, "ingest_backend", "parsl")
    with pytest.raises(HTTPException) as ei:
        await _call_ingest(docs, Principal(tenant="default", role="admin"))
    assert ei.value.status_code == 501
    assert "not supported" in ei.value.detail


@pytest.mark.asyncio
async def test_ingest_endpoint_gowe_needs_a_user_token(monkeypatch):
    """#203: gowe is no longer 501 — but it submits AS the caller, so a keyless
    / API-key principal (no BV-BRC token) is refused with 401."""
    from fastapi import HTTPException

    from ragstack.api.routers import documents as docs
    from ragstack.api.security import Principal

    monkeypatch.setattr(docs.settings, "ingest_backend", "gowe")
    with pytest.raises(HTTPException) as ei:
        await _call_ingest(docs, Principal(tenant="default", role="admin"))
    assert ei.value.status_code == 401
    assert "BV-BRC user token" in ei.value.detail
