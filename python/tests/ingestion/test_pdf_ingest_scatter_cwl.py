"""Contract tests for ``cwl/pdf-ingest-scatter.cwl`` (#203 Option A).

The workflow exists to be driven by ``GoWeBackend``: the backend scatters a
``File[]`` into one named input, and the per-item receipts come back INSIDE the
``archive`` Directory (its ``receipt.json`` array, in input order), which must be
the workflow's ONLY output — GoWe post-stages every top-level File output flat
into ``output_destination`` (by basename, overwriting) and rewrites its location
to ``ws://``. Those invariants — plus "every tool is inlined" and "both docker
keys are declared" — are the ones that break silently (a COMPLETED run reporting
every item failed, or a user's ``versions/`` folder filling with shards), so they
are asserted here rather than left to a live submission.

Offline: parses the YAML, runs nothing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

CWL_PATH = Path(__file__).resolve().parents[3] / "cwl" / "pdf-ingest-scatter.cwl"
PDF_INGEST_PATH = CWL_PATH.with_name("pdf-ingest.cwl")


@pytest.fixture(scope="module")
def wf() -> dict:
    if not CWL_PATH.is_file():
        pytest.skip(f"{CWL_PATH} not present (checkout without cwl/)")
    return yaml.safe_load(CWL_PATH.read_text(encoding="utf-8"))


def _steps(wf: dict) -> dict:
    return wf["steps"]


def test_scattered_pdfs_input(wf: dict) -> None:
    """The scattered input is ``pdfs`` — GoWeBackend must be given
    ``shards_input_key="pdfs"`` (its default, "shards", is for ingest-bulk)."""
    assert wf["inputs"]["pdfs"]["type"] == "File[]"
    assert _steps(wf)["extract"]["scatter"] == "pdf"
    assert _steps(wf)["extract"]["in"]["pdf"] == "pdfs"


def test_archive_is_the_only_workflow_output(wf: dict) -> None:
    """Post-staging uploads EVERY top-level File output flat into the user's
    ``versions/`` folder (basename, overwrite) — so nothing but the archive
    Directory may be exposed, and the receipts must ride inside it."""
    assert list(wf["outputs"]) == ["archive"]
    archive = wf["outputs"]["archive"]
    assert archive["type"] == "Directory"
    assert archive["outputSource"] == "pack/archive"
    # The per-PDF receipts feed the pack step (→ receipt.json array, input order),
    # sourced from the scattered ingest step (not extract), so each receipt
    # reflects the actual Qdrant/ES upsert.
    pack_in = _steps(wf)["pack"]["in"]
    assert pack_in["receipt"] == "ingest/receipt"
    assert pack_in["chunks"] == "ingest/embeddings"
    assert pack_in["version"] == "version"
    assert _steps(wf)["ingest"]["scatter"] == "shard"
    assert _steps(wf)["ingest"]["in"]["shard"] == "extract/shard"
    assert "merge" not in _steps(wf)  # a summary nobody may expose = a dead task


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


def test_every_tool_is_inlined(wf: dict) -> None:
    """GoWeClient.register_workflow POSTs the CWL text — an external ``run:`` file
    reference cannot be resolved engine-side."""
    for name, step in _steps(wf).items():
        assert isinstance(step["run"], dict), f"step {name} uses an external run: ref"


def test_docker_requirement_declares_both_keys(wf: dict) -> None:
    """GoWe reads only ``dockerPull``; cwltool --singularity needs ``dockerImageId``.
    Neither falls back to the other (see cwl/README.md)."""
    for name, step in _steps(wf).items():
        docker = step["run"]["requirements"]["DockerRequirement"]
        assert docker["dockerPull"] == "ragstack-worker.sif", name
        assert docker["dockerImageId"] == "ragstack-worker.sif", name


def test_network_access_only_where_needed(wf: dict) -> None:
    """extract is local PyMuPDF I/O; ingest talks to the fleet + Qdrant/ES."""
    reqs = {n: s["run"]["requirements"] for n, s in _steps(wf).items()}
    assert "NetworkAccess" not in reqs["extract"]
    assert reqs["ingest"]["NetworkAccess"]["networkAccess"] is True
