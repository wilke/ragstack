"""Run the archive CWL tools through ``cwltool`` (#357).

What this proves: the tools' argument bindings and ``outputBinding.glob:
$(inputs.version)`` produce a ``Directory`` output whose **basename is the
version number** and whose listing is exactly the expected file set — the
contract GoWe's post-staging turns into ``versions/<N>/``.

What it does not prove: the container. The tools' ``baseCommand`` points at
``/opt/ragstack/scripts`` inside ``ragstack-worker.sif``; that image is a build
artefact that does not exist in a checkout, so the test rewrites the
``baseCommand`` to this interpreter + the checkout's script, drops the
``DockerRequirement`` and runs ``cwltool --no-container``. Skips cleanly when
``cwltool`` is not on ``PATH``. Marked ``integration`` (it spawns a runner) —
it needs no service.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[3]
PY_DIR = REPO / "python"
CWL_DIR = REPO / "cwl"
SCRIPT = PY_DIR / "scripts" / "archive_version.py"
SCHEMA = "ragstack.embedding_file/v1"


def _cwltool() -> str:
    exe = shutil.which("cwltool")
    if not exe:
        pytest.skip("cwltool not on PATH")
    return exe


def _localised_tool(src: Path, dest: Path) -> Path:
    """Copy a tool CWL with baseCommand -> this interpreter + the checkout's
    script and no DockerRequirement, so cwltool can run it without the image."""
    tool = yaml.safe_load(src.read_text(encoding="utf-8"))
    assert tool["class"] == "CommandLineTool"
    assert tool["baseCommand"] == ["python", "/opt/ragstack/scripts/archive_version.py"]
    tool["baseCommand"] = [sys.executable, str(SCRIPT)]
    tool["requirements"].pop("DockerRequirement", None)
    if not tool["requirements"]:
        del tool["requirements"]
    dest.write_text(yaml.safe_dump(tool, sort_keys=False), encoding="utf-8")
    return dest


def _run(cwltool: str, tool: Path, job: Path, outdir: Path) -> dict:
    env = {**os.environ, "PYTHONPATH": str(PY_DIR)}
    proc = subprocess.run(
        [cwltool, "--no-container", "--preserve-environment", "PYTHONPATH",
         "--outdir", str(outdir), str(tool), str(job)],
        capture_output=True, text=True, env=env, timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    return json.loads(proc.stdout)


def _embed_file(path: Path, n: int, dim: int = 8) -> None:
    lines = [json.dumps({"schema": SCHEMA, "tenant": "public", "dim": dim})]
    lines += [json.dumps({"id": f"c{i}", "doc_id": f"d{i // 2}", "content": f"text {i}",
                          "embedding": [float(i) / (j + 1) for j in range(dim)],
                          "metadata": {}, "start_char": 0, "end_char": 6})
              for i in range(n)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_archive_collection_tool_emits_version_directory(tmp_path: Path) -> None:
    cwltool = _cwltool()
    tool = _localised_tool(CWL_DIR / "archive-collection.cwl", tmp_path / "tool.cwl")
    _embed_file(tmp_path / "a.emb.jsonl", 5)
    _embed_file(tmp_path / "b.emb.jsonl", 2)
    (tmp_path / "r1.json").write_text(json.dumps({"shard_id": "a", "status": "completed"}))
    (tmp_path / "r2.json").write_text(json.dumps({"shard_id": "b", "status": "completed"}))
    job = tmp_path / "job.yml"
    job.write_text(yaml.safe_dump({
        "version": "7",
        "chunks": [{"class": "File", "path": str(tmp_path / "a.emb.jsonl")},
                   {"class": "File", "path": str(tmp_path / "b.emb.jsonl")}],
        "receipt": [{"class": "File", "path": str(tmp_path / "r1.json")},
                    {"class": "File", "path": str(tmp_path / "r2.json")}],
        "collection_id": "col-x", "tenant": "acme", "spec_hash": "beef", "job_id": "j9",
    }))
    outdir = tmp_path / "out"
    out = _run(cwltool, tool, job, outdir)

    archive = out["archive"]
    assert archive["class"] == "Directory"
    assert archive["basename"] == "7"
    names = sorted(e["basename"] for e in archive["listing"])
    assert names == ["chunks.jsonl.gz", "manifest.json", "receipt.json", "vectors.f32"]
    assert all(e["class"] == "File" for e in archive["listing"])

    vdir = outdir / "7"
    assert sorted(p.name for p in vdir.iterdir()) == names
    from ragstack.ingestion.archive import read_version, verify_version
    m = verify_version(vdir)
    assert (m["collection_id"], m["tenant"], m["spec_hash"], m["job_id"], m["version"]) == \
        ("col-x", "acme", "beef", "j9", 7)
    assert m["counts"] == {"chunks": 7, "docs": 3}  # d0,d1,d2 in a; b repeats d0
    assert m["receipts"] == 2
    assert [c["id"] for c, _ in read_version(vdir)] == [f"c{i}" for i in range(5)] + ["c0", "c1"]


def test_archive_tombstone_tool_emits_version_directory(tmp_path: Path) -> None:
    cwltool = _cwltool()
    tool = _localised_tool(CWL_DIR / "archive-tombstone.cwl", tmp_path / "tool.cwl")
    (tmp_path / "ids.json").write_text(json.dumps(["d3", "d1"]))
    job = tmp_path / "job.yml"
    job.write_text(yaml.safe_dump({
        "version": "8",
        "tombstone": {"class": "File", "path": str(tmp_path / "ids.json")},
        "collection_id": "col-x",
    }))
    outdir = tmp_path / "out"
    out = _run(cwltool, tool, job, outdir)
    archive = out["archive"]
    assert archive["class"] == "Directory" and archive["basename"] == "8"
    assert sorted(e["basename"] for e in archive["listing"]) == ["manifest.json", "tombstone.json"]
    from ragstack.ingestion.archive import read_tombstone
    assert read_tombstone(outdir / "8") == ["d1", "d3"]


def test_standalone_and_inlined_archive_tools_agree() -> None:
    """The workflows inline the tool (GoWe registers CWL text — no external
    `run:`); the copies must not drift from cwl/archive-collection.cwl in what
    matters: command, bindings, output glob. Only the `receipt` type may differ
    (File in pdf-ingest.cwl, whose load emits one summary)."""
    ref = yaml.safe_load((CWL_DIR / "archive-collection.cwl").read_text(encoding="utf-8"))
    for wf_name in ("pdf-ingest.cwl", "pdf-ingest-scatter.cwl"):
        wf = yaml.safe_load((CWL_DIR / wf_name).read_text(encoding="utf-8"))
        step = wf["steps"]["pack"]
        tool = step["run"]
        assert tool["baseCommand"] == ref["baseCommand"], wf_name
        assert tool["requirements"]["DockerRequirement"] == ref["requirements"]["DockerRequirement"]
        assert tool["arguments"] == ref["arguments"], wf_name
        assert tool["outputs"]["archive"]["outputBinding"] == ref["outputs"]["archive"]["outputBinding"]
        assert tool["outputs"]["archive"]["type"] == "Directory"
        for name, ref_in in ref["inputs"].items():
            got = tool["inputs"][name]
            assert got["inputBinding"] == ref_in["inputBinding"], (wf_name, name)
            assert got.get("default") == ref_in.get("default"), (wf_name, name)
            if name != "receipt":
                assert got["type"] == ref_in["type"], (wf_name, name)
        assert wf["outputs"]["archive"]["type"] == "Directory"
        assert wf["outputs"]["archive"]["outputSource"] == "pack/archive"
