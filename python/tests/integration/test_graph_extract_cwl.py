"""Run the graph-extract workflow through ``cwltool --no-container`` (#350).

What this proves: the inlined tools' bindings, the ``$(inputs.version)``
output glob and the step wiring produce a workflow-level ``Directory`` output
whose basename is the version number and whose listing is exactly
``manifest.json`` + ``triples.jsonl.gz`` — the delta GoWe's post-staging merges
onto ``versions/<n>/`` — after the load step consumed it; that merging it onto
the archived version yields a version that verifies whole (every sha256, the
manifest's ``graph: true``); and that a budget refusal fails the workflow with
the refusal line and leaves the archived version untouched.

What it does not prove: the container (the tools' ``baseCommand`` points at
``/opt/ragstack/scripts`` inside ``ragstack-worker.sif``; both steps are
rewritten to this interpreter + the checkout's scripts and run without
``DockerRequirement``), the LLM (``RAGSTACK_FAKE_LLM=1`` — the tool's built-in
fake, one triple per chunk from its first sentence) or a durable graph store
(``graph_backend: memory``; the load step resolves the collection from an
inline JSON registry passed through the environment). Skips cleanly when
``cwltool`` is not on ``PATH``. Marked ``integration`` (it spawns a runner).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ragstack.ingestion.archive import (
    TRIPLES_NAME,
    read_triples,
    read_version,
    verify_version,
)
from tests.archive_support import chunk_version

yaml = pytest.importorskip("yaml")

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[3]
PY_DIR = REPO / "python"
CWL_DIR = REPO / "cwl"
SCRIPTS = {
    "extract_graph.py": PY_DIR / "scripts" / "extract_graph.py",
    "load_graph.py": PY_DIR / "scripts" / "load_graph.py",
}
REGISTRY = [{"id": "lib", "collection": "lib_phys", "embedding_model": "m",
             "embedding_model_dim": 16}]


def _cwltool() -> str:
    exe = shutil.which("cwltool")
    if not exe:
        pytest.skip("cwltool not on PATH")
    return exe


def _localised_workflow(dest: Path) -> Path:
    """The workflow with each inlined tool's baseCommand -> this interpreter +
    the checkout's script and no DockerRequirement."""
    wf = yaml.safe_load((CWL_DIR / "graph-extract.cwl").read_text(encoding="utf-8"))
    assert wf["class"] == "Workflow"
    for step in wf["steps"].values():
        tool = step["run"]
        assert tool["baseCommand"][0] == "python"
        script = Path(tool["baseCommand"][1]).name
        tool["baseCommand"] = [sys.executable, str(SCRIPTS[script])]
        tool["requirements"].pop("DockerRequirement", None)
    dest.write_text(yaml.safe_dump(wf, sort_keys=False), encoding="utf-8")
    return dest


def _run(cwltool: str, wf: Path, job: Path, outdir: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(PY_DIR), "RAGSTACK_FAKE_LLM": "1",
           "COLLECTIONS_JSON": json.dumps(REGISTRY), "COLLECTION_STORE_BACKEND": "json",
           "COLLECTIONS_FILE": ""}
    return subprocess.run(
        [cwltool, "--no-container", "--preserve-environment", "PYTHONPATH",
         "--preserve-environment", "RAGSTACK_FAKE_LLM",
         "--preserve-environment", "COLLECTIONS_JSON",
         "--preserve-environment", "COLLECTION_STORE_BACKEND",
         "--preserve-environment", "COLLECTIONS_FILE",
         "--outdir", str(outdir), str(wf), str(job)],
        capture_output=True, text=True, env=env, timeout=300,
    )


def _job(tmp_path: Path, vdir: Path, **extra) -> Path:
    job = tmp_path / "job.yml"
    job.write_text(yaml.safe_dump({
        "version_dir": {"class": "Directory", "path": str(vdir)},
        "version": vdir.name, "collection_id": "lib", "tenant": "bvbrc:alice@patricbrc.org",
        "spec_hash": "cafe0001", "llm_endpoint": "http://unused.test", "llm_model": "fake",
        "concurrency": 2, "graph_backend": "memory",
        # neo4j_uri is a REQUIRED input with no default (#407) — omitting it used
        # to be legal only because the workflow defaulted it to bolt://localhost:7687,
        # which is production on the deployment host. Dead address: graph_backend
        # is `memory` here, so nothing dials it; a regression that started using it
        # would fail to connect rather than write into the production graph.
        "neo4j_uri": "bolt://127.0.0.1:1",
        **extra,
    }))
    return job


def test_graph_extract_workflow_emits_the_delta_and_it_merges(tmp_path: Path) -> None:
    cwltool = _cwltool()
    wf = _localised_workflow(tmp_path / "wf.cwl")
    (tmp_path / "archive").mkdir()
    vdir, _recs = chunk_version(tmp_path / "archive",3, 3, collection_id="lib")
    before = verify_version(vdir)
    assert before["graph"] is False
    outdir = tmp_path / "out"
    proc = _run(cwltool, wf, _job(tmp_path, vdir), outdir)
    assert proc.returncode == 0, proc.stderr[-4000:]
    out = json.loads(proc.stdout)

    assert list(out) == ["archive"]  # the ONLY workflow output
    archive = out["archive"]
    assert archive["class"] == "Directory" and archive["basename"] == "3"
    names = sorted(e["basename"] for e in archive["listing"])
    assert names == ["manifest.json", TRIPLES_NAME]
    delta = outdir / "3"
    assert sorted(p.name for p in delta.iterdir()) == names

    # The archived version is untouched by the run itself...
    assert verify_version(vdir) == before
    # ...and merging the delta onto it (what post-staging does: same
    # basename, overwrite) yields a version that verifies whole.
    for p in delta.iterdir():
        shutil.copy(p, vdir / p.name)
    m = verify_version(vdir)
    assert m["graph"] is True and m["files"]["triples"] == TRIPLES_NAME
    assert m["counts"] == {"chunks": 3, "docs": 1, "triples": 3}
    assert set(m["sha256"]) == {"chunks.jsonl.gz", "vectors.f32", "receipt.json", TRIPLES_NAME}
    assert m["graph_extraction"]["extractor"] == "fake"
    assert (m["collection_id"], m["spec_hash"], m["version"]) == ("lib", "cafe0001", 3)
    triples = list(read_triples(vdir, manifest=m))
    assert [t.chunk_id for t in triples] == ["chunk-0", "chunk-1", "chunk-2"]
    assert all(t.derived_by == "llm" and t.confidence == 1 and t.evidence for t in triples)
    assert all(t.tenant_id == "bvbrc:alice@patricbrc.org" and t.collection == "" for t in triples)
    assert len(list(read_version(vdir, manifest=m))) == 3


def test_graph_extract_workflow_refuses_at_the_budget_with_nothing_delivered(
    tmp_path: Path,
) -> None:
    cwltool = _cwltool()
    wf = _localised_workflow(tmp_path / "wf.cwl")
    (tmp_path / "archive").mkdir()
    vdir, _recs = chunk_version(tmp_path / "archive",4, 3, collection_id="lib")
    before = (vdir / "manifest.json").read_bytes()
    outdir = tmp_path / "out"
    proc = _run(cwltool, wf, _job(tmp_path, vdir, max_triples=1), outdir)
    assert proc.returncode != 0
    assert "graph_cap_exceeded: live=? incoming=3 cap=1 would_fit=1" in proc.stderr
    assert "permanentFail" in proc.stderr or "permanent" in proc.stderr.lower()
    assert not (outdir / "4").exists()
    assert (vdir / "manifest.json").read_bytes() == before
    assert not (vdir / TRIPLES_NAME).exists()
