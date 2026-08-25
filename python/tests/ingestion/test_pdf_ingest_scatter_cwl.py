"""Contract tests for ``cwl/pdf-ingest-scatter.cwl`` (#203 — Option B, batch per task).

The workflow exists to be driven by ``GoWeBackend``: the backend hands a flat
``File[]`` to one named input, the workflow groups it into batches ITSELF (an
ExpressionTool step — the driver never pre-groups), one task chain ingests a
batch, and the per-document results come back INSIDE the ``archive``
Directory (its ``receipt.json`` array: one receipt per batch, a ``docs`` row
per document), which must be the workflow's ONLY output — GoWe post-stages
every top-level File output flat into ``output_destination`` (by basename,
overwriting) and rewrites its location to ``ws://``. Those invariants — plus
"every tool is inlined", "both docker keys are declared" and "the extract
report reaches ingest_shard" (the scanned-PDF signal) — are the ones that break
silently, so they are asserted here rather than left to a live submission.

Offline: parses the YAML, runs nothing. The end-to-end run under cwltool is
``tests/integration/test_pdf_ingest_batch_cwl.py``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

CWL_PATH = Path(__file__).resolve().parents[3] / "cwl" / "pdf-ingest-scatter.cwl"
PDF_INGEST_PATH = CWL_PATH.with_name("pdf-ingest.cwl")
PDF_EXTRACT_PATH = CWL_PATH.with_name("pdf-extract.cwl")


@pytest.fixture(scope="module")
def wf() -> dict:
    if not CWL_PATH.is_file():
        pytest.skip(f"{CWL_PATH} not present (checkout without cwl/)")
    return yaml.safe_load(CWL_PATH.read_text(encoding="utf-8"))


def _steps(wf: dict) -> dict:
    return wf["steps"]


def test_flat_pdfs_input_batched_inside_the_workflow(wf: dict) -> None:
    """The driver's input stays a flat ``pdfs: File[]`` (GoWeBackend's
    ``shards_input_key`` default); the ``batch`` ExpressionTool turns it into
    ``File[][]`` by ``batch_size`` (default 20) and the task steps scatter over
    the batches."""
    assert wf["inputs"]["pdfs"]["type"] == "File[]"
    assert wf["inputs"]["batch_size"]["type"] == "int"
    assert wf["inputs"]["batch_size"]["default"] == 20
    batch = _steps(wf)["batch"]
    assert batch["run"]["class"] == "ExpressionTool"
    assert batch["in"] == {"pdfs": "pdfs", "batch_size": "batch_size"}
    assert batch["run"]["outputs"]["batches"]["type"] == {
        "type": "array", "items": {"type": "array", "items": "File"}}
    assert "InlineJavascriptRequirement" in batch["run"]["requirements"]
    assert "InlineJavascriptRequirement" in wf["requirements"]
    extract = _steps(wf)["extract"]
    assert extract["scatter"] == ["pdfs", "batch_id"] and extract["scatterMethod"] == "dotproduct"
    assert extract["in"]["pdfs"] == "batch/batches" and extract["in"]["batch_id"] == "batch/batch_ids"
    assert extract["run"]["inputs"]["pdfs"]["type"] == "File[]"


def test_archive_is_the_only_workflow_output(wf: dict) -> None:
    """Post-staging uploads EVERY top-level File output flat into the user's
    ``versions/`` folder (basename, overwrite) — so nothing but the archive
    Directory may be exposed, and the receipts must ride inside it."""
    assert list(wf["outputs"]) == ["archive"]
    archive = wf["outputs"]["archive"]
    assert archive["type"] == "Directory"
    assert archive["outputSource"] == "pack/archive"
    # The per-batch receipts feed the pack step (→ receipt.json array, batch
    # order), sourced from the scattered ingest step (not extract), so each
    # receipt reflects the actual Qdrant/ES upsert.
    pack_in = _steps(wf)["pack"]["in"]
    assert pack_in["receipt"] == "ingest/receipt"
    assert pack_in["chunks"] == "ingest/embeddings"
    assert pack_in["version"] == "version"
    ingest = _steps(wf)["ingest"]
    assert ingest["scatter"] == ["shard", "report"] and ingest["scatterMethod"] == "dotproduct"
    assert ingest["in"]["shard"] == "extract/shard"
    assert "merge" not in _steps(wf)  # a summary nobody may expose = a dead task


def test_extract_report_reaches_ingest_shard(wf: dict) -> None:
    """The scanned-PDF signal (#377 → 2b): the extract step's report — which
    names the skipped files with the constant NO_TEXT_ERROR — is an input of
    the ingest task, bound to ``--extract-report``, so the receipt's per-document
    rows carry it and a no-text PDF fails alone rather than sinking its batch."""
    ingest = _steps(wf)["ingest"]
    assert ingest["in"]["report"] == "extract/report"
    binding = ingest["run"]["inputs"]["report"]["inputBinding"]
    assert binding["prefix"] == "--extract-report"
    assert ingest["run"]["inputs"]["report"]["type"] == "File"
    # Extract names its outputs by the batch id, and ingest names the receipt's
    # shard after the shard (== the batch id) — so a receipt names its batch.
    extract = _steps(wf)["extract"]["run"]
    assert extract["outputs"]["shard"]["outputBinding"]["glob"] == "$(inputs.batch_id).jsonl"
    assert extract["outputs"]["report"]["outputBinding"]["glob"] == "$(inputs.batch_id).report.json"
    shard_id = [a for a in ingest["run"]["arguments"] if a.get("prefix") == "--shard-id"]
    assert shard_id and shard_id[0]["valueFrom"] == "$(inputs.shard.nameroot)"


def test_pdf_ingest_workflow_exposes_only_the_archive_too() -> None:
    """Same rule for the one-shard-per-run PDF workflow."""
    if not PDF_INGEST_PATH.is_file():
        pytest.skip(f"{PDF_INGEST_PATH} not present")
    wf = yaml.safe_load(PDF_INGEST_PATH.read_text(encoding="utf-8"))
    assert list(wf["outputs"]) == ["archive"]
    assert wf["outputs"]["archive"]["type"] == "Directory"
    assert wf["outputs"]["archive"]["outputSource"] == "pack/archive"


def test_receipts_come_from_ingest_shard(wf: dict) -> None:
    """The receipt is produced by ingest_shard.py (ShardReceipt), not re-implemented."""
    cmd = _steps(wf)["ingest"]["run"]["baseCommand"]
    assert cmd[-1].endswith("ingest_shard.py")


def test_inlined_extract_agrees_with_the_standalone_tool(wf: dict) -> None:
    """The inlined extract step must not drift from cwl/pdf-extract.cwl in what
    matters: command, the pdfs binding, the docker keys. Only the output naming
    differs (the batch id instead of a fixed out_name)."""
    if not PDF_EXTRACT_PATH.is_file():
        pytest.skip(f"{PDF_EXTRACT_PATH} not present")
    ref = yaml.safe_load(PDF_EXTRACT_PATH.read_text(encoding="utf-8"))
    tool = _steps(wf)["extract"]["run"]
    assert tool["baseCommand"] == ref["baseCommand"]
    assert tool["requirements"]["DockerRequirement"] == ref["requirements"]["DockerRequirement"]
    assert tool["inputs"]["pdfs"]["inputBinding"] == ref["inputs"]["pdfs"]["inputBinding"]
    assert [a["prefix"] for a in tool["arguments"]] == ["--out", "--report"]


def test_every_tool_is_inlined(wf: dict) -> None:
    """GoWeClient.register_workflow POSTs the CWL text — an external ``run:`` file
    reference cannot be resolved engine-side."""
    for name, step in _steps(wf).items():
        assert isinstance(step["run"], dict), f"step {name} uses an external run: ref"


def test_docker_requirement_declares_both_keys(wf: dict) -> None:
    """GoWe reads only ``dockerPull``; cwltool --singularity needs ``dockerImageId``.
    Neither falls back to the other (see cwl/README.md). The ExpressionTool step
    runs in the engine and declares no image."""
    for name, step in _steps(wf).items():
        if step["run"]["class"] == "ExpressionTool":
            assert "DockerRequirement" not in step["run"].get("requirements", {}), name
            continue
        docker = step["run"]["requirements"]["DockerRequirement"]
        assert docker["dockerPull"] == "ragstack-worker.sif", name
        assert docker["dockerImageId"] == "ragstack-worker.sif", name


def test_network_access_only_where_needed(wf: dict) -> None:
    """extract is local PyMuPDF I/O; ingest talks to the fleet + Qdrant/ES."""
    reqs = {n: s["run"]["requirements"] for n, s in _steps(wf).items()}
    assert "NetworkAccess" not in reqs["extract"]
    assert reqs["ingest"]["NetworkAccess"]["networkAccess"] is True


def test_max_chunks_is_an_optional_input_threaded_to_ingest_shard(wf: dict) -> None:
    """#291: the per-collection chunk cap reaches the worker as ONE optional
    workflow input (default 0 = unlimited) bound to ``ingest_shard --max-chunks``
    — the API passes it per job; a hand-driven run may omit it."""
    inp = wf["inputs"]["max_chunks"]
    assert inp["type"] == "int" and inp["default"] == 0
    ingest = _steps(wf)["ingest"]
    assert ingest["in"]["max_chunks"] == "max_chunks"
    tool_in = ingest["run"]["inputs"]["max_chunks"]
    assert tool_in["type"] == "int" and tool_in["default"] == 0
    assert tool_in["inputBinding"]["prefix"] == "--max-chunks"
