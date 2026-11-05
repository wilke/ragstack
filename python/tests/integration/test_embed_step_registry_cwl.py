"""The repin step R (#828 §12): each shipped embed workflow's ``embed_shard.py``
step resolves the registry entry its load step resolves, run through ``cwltool``.

``pdf-ingest.cwl`` and ``embed-bulk.cwl`` pass ``collection_id`` and the
registry NAME (``--registry``, #563) to the embed tool; ``jats-ingest.cwl``
(``test_jats_ingest_cwl.py``) hands it the load step's sqlite file through the
same ``EnvVarRequirement``. Proven here on the artefact the load step checks:
every embedding file's header carries ``chunking.collection_id`` equal to the
id the workflow was given — which it does only when the embed tool resolved
the entry (an unresolved run writes no ``collection_id``). And a chunk input
that contradicts the entry fails the embed step (exit 2, before any embed),
which only a resolving tool can do.

Offline like ``test_pdf_ingest_batch_cwl.py``: every inlined tool rewritten to
this interpreter + the checkout's script, no container, a fake
``/v1/embeddings`` on 127.0.0.1, char-based chunking, in-memory stores, and a
scratch sqlite registry selected by NAME through ``COLLECTION_STORE_*_<NAME>``
(the ambient unsuffixed variables are removed from the runner's environment).
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")
pytest.importorskip("pymupdf")

from ragstack.collection_store import CollectionSpec, SqliteCollectionStore  # noqa: E402
from ragstack.ingestion.embedding_file import read_header  # noqa: E402
from ragstack.ops import ingest_target as it  # noqa: E402
from tests.integration.test_pdf_ingest_batch_cwl import (  # noqa: E402,F401 — fixture
    IMAGE_CMD,
    _cwltool,
    _pdf,
    embeddings_url,
)

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[3]
PY_DIR = REPO / "python"
CID = "scratch-828-step-r"
REG = "scratch828r"


def _localised(cwl: Path, dest: Path, *, expose_embeddings: str | None = None) -> Path:
    wf = yaml.safe_load(cwl.read_text(encoding="utf-8"))
    for name, step in wf["steps"].items():
        tool = step["run"]
        if tool["class"] != "CommandLineTool":
            continue
        cmd = tool["baseCommand"]
        assert cmd[0] == "python" and cmd[1].startswith(IMAGE_CMD), (name, cmd)
        script = cmd[1][len(IMAGE_CMD):]
        tool["baseCommand"] = [sys.executable, str(PY_DIR / "scripts" / script), *cmd[2:]]
        tool["requirements"].pop("DockerRequirement", None)
        extra: list[tuple[str, str]] = []
        if script == "embed_shard.py":
            extra = [("--embedding-api", "openai"), ("--chunk-token-counter", "estimate")]
        elif script == "load_embeddings.py":
            extra = [("--vector-backend", "memory"), ("--text-backend", "memory")]
        tool.setdefault("arguments", [])
        tool["arguments"] += [{"position": 90 + i, "prefix": k, "valueFrom": v}
                              for i, (k, v) in enumerate(extra)]
        if not tool["requirements"]:
            del tool["requirements"]
    if expose_embeddings:
        wf["outputs"]["embeddings"] = {"type": "File[]", "outputSource": expose_embeddings}
    dest.write_text(yaml.safe_dump(wf, sort_keys=False), encoding="utf-8")
    return dest


def _registry(tmp_path: Path) -> dict[str, str]:
    """A scratch sqlite registry with the one entry; the env selecting it by NAME."""
    db = tmp_path / "registry.db"
    asyncio.run(SqliteCollectionStore(str(db)).put(CollectionSpec(
        id=CID, collection="store_scratch_828_step_r", embedding_model="fake-model",
        embedding_model_dim=4, chunk_method="fixed", chunk_size=200, chunk_overlap=20)))
    suffix = it.registry_env_suffix(REG)
    return {f"COLLECTION_STORE_BACKEND_{suffix}": "sqlite",
            f"COLLECTION_STORE_PATH_{suffix}": str(db)}


def _run(tmp_path: Path, wf: Path, job: dict, reg_env: dict[str, str]):
    job_path = tmp_path / "job.yml"
    job_path.write_text(yaml.safe_dump(job), encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("COLLECTION_STORE_") and k != "RAGSTACK_COLLECTION_ID"}
    env.update({"PYTHONPATH": str(PY_DIR), **reg_env})
    preserve = ["--preserve-environment", "PYTHONPATH"]
    for name in reg_env:
        preserve += ["--preserve-environment", name]
    return subprocess.run(
        [_cwltool(), "--no-container", *preserve, "--outdir", str(tmp_path / "out"),
         str(wf), str(job_path)],
        capture_output=True, text=True, env=env, timeout=600,
    )


def _header_ids(out: dict) -> list:
    return [(read_header(f["path"]).get("chunking") or {}).get("collection_id")
            for f in out["embeddings"]]


def test_pdf_ingest_embed_step_resolves_the_load_steps_entry(
        tmp_path: Path, embeddings_url: str) -> None:  # noqa: F811
    src = tmp_path / "src"
    src.mkdir()
    pdfs = [_pdf(src / f"{n}.pdf", f"Paper {n} on hybrid retrieval and chunking. " * 8)
            for n in ("a", "b")]
    wf = _localised(REPO / "cwl" / "pdf-ingest.cwl", tmp_path / "wf.cwl",
                    expose_embeddings="embed/embeddings")
    proc = _run(tmp_path, wf, {
        "pdfs": [{"class": "File", "path": str(p)} for p in pdfs],
        "collection": "store_scratch_828_step_r", "tenant": "acme",
        "chunk_method": "fixed", "chunk_size": 200, "chunk_overlap": 20,
        "embedding_url": [embeddings_url], "embedding_model": "fake-model",
        "qdrant_url": "http://127.0.0.1:1", "es_url": "http://127.0.0.1:1",
        "version": "1", "collection_id": CID, "registry": REG,
    }, _registry(tmp_path))
    assert proc.returncode == 0, proc.stderr[-6000:]
    out = json.loads(proc.stdout)
    assert _header_ids(out) == [CID]
    manifest = json.loads((tmp_path / "out" / "1" / "manifest.json").read_text())
    assert manifest["collection_id"] == CID          # what pack recorded, from the same input


def _shard(tmp_path: Path) -> Path:
    p = tmp_path / "shard-0000.jsonl"
    p.write_text("".join(json.dumps({
        "text": f"Document {n}: reciprocal rank fusion over lexical and dense rankings. " * 6,
        "path": f"/docs/{n}.txt", "metadata": {"title": f"Doc {n}"}}) + "\n"
        for n in ("x", "y")), encoding="utf-8")
    return p


def _bulk_job(tmp_path: Path, embeddings_url: str, **over) -> dict:
    return {"shards": [{"class": "File", "path": str(_shard(tmp_path))}],
            "chunk_method": "fixed", "chunk_size": 200, "chunk_overlap": 20,
            "embedding_url": [embeddings_url], "embedding_model": "fake-model", **over}


def test_embed_bulk_embed_step_resolves_the_entry_given_its_optional_inputs(
        tmp_path: Path, embeddings_url: str) -> None:  # noqa: F811
    wf = _localised(REPO / "cwl" / "embed-bulk.cwl", tmp_path / "wf.cwl")
    proc = _run(tmp_path, wf, _bulk_job(tmp_path, embeddings_url,
                                        collection_id=CID, registry=REG), _registry(tmp_path))
    assert proc.returncode == 0, proc.stderr[-6000:]
    assert _header_ids(json.loads(proc.stdout)) == [CID]


def test_embed_bulk_without_the_optional_inputs_runs_unresolved_as_before(
        tmp_path: Path, embeddings_url: str) -> None:  # noqa: F811
    """A workflow contract change that keeps every existing input file valid."""
    wf = _localised(REPO / "cwl" / "embed-bulk.cwl", tmp_path / "wf.cwl")
    proc = _run(tmp_path, wf, _bulk_job(tmp_path, embeddings_url), {})
    assert proc.returncode == 0, proc.stderr[-6000:]
    assert _header_ids(json.loads(proc.stdout)) == [None]


def test_embed_bulk_refuses_a_chunk_input_that_contradicts_the_entry(
        tmp_path: Path, embeddings_url: str) -> None:  # noqa: F811
    """Only a resolving embed step can see the contradiction (exit 2, before
    any embed)."""
    wf = _localised(REPO / "cwl" / "embed-bulk.cwl", tmp_path / "wf.cwl")
    proc = _run(tmp_path, wf, _bulk_job(tmp_path, embeddings_url, chunk_size=300,
                                        collection_id=CID, registry=REG), _registry(tmp_path))
    assert proc.returncode != 0
    assert "chunk_size" in proc.stderr or "chunk-size" in proc.stderr, proc.stderr[-3000:]
