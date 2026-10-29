"""Run ``cwl/jats-ingest.cwl`` through ``cwltool`` (#847 G2b, the parallel branch).

The bulk JATS plane runs ``glossary`` in parallel with ``embed`` (both read
the extract output), gathers the per-shard glossary files in
``merge_glossary`` and joins them at ``load`` (``load_embeddings
--glossary-file``), which reassembles every document from its chunks and
checks its ``text_sha256`` against the file before any write. That join is
wiring no other test executes: a glossary keyed to different text than the
embedded chunks would refuse the load (exit 2).

Offline like ``test_pdf_ingest_batch_cwl.py``: every inlined tool rewritten to
this interpreter + the checkout's script, no container, a fake
``/v1/embeddings`` on 127.0.0.1, char-based chunking, in-memory stores, and a
scratch sqlite registry row (the load resolves ``collection_id`` through it).
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

from ragstack.collection_store import CollectionSpec, SqliteCollectionStore  # noqa: E402
from tests.ingestion.test_jats_extract import _corpus  # noqa: E402
from tests.integration.test_pdf_ingest_batch_cwl import (  # noqa: E402,F401 — fixture
    IMAGE_CMD,
    _cwltool,
    embeddings_url,
)

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[3]
PY_DIR = REPO / "python"
CWL = REPO / "cwl" / "jats-ingest.cwl"
CID = "scratch-847-jats"


def _localised(dest: Path) -> Path:
    wf = yaml.safe_load(CWL.read_text(encoding="utf-8"))
    for name, step in wf["steps"].items():
        tool = step["run"]
        cmd = tool["baseCommand"]
        assert cmd[0] == "python" and cmd[1].startswith(IMAGE_CMD), (name, cmd)
        tool["baseCommand"] = [sys.executable, str(PY_DIR / "scripts" / cmd[1][len(IMAGE_CMD):]),
                               *cmd[2:]]
        tool["requirements"].pop("DockerRequirement", None)
        extra: list[tuple[str, str]] = []
        if name == "embed":
            extra = [("--embedding-api", "openai"), ("--chunk-token-counter", "estimate")]
        elif name == "load":
            extra = [("--vector-backend", "memory"), ("--text-backend", "memory")]
        tool.setdefault("arguments", [])
        tool["arguments"] += [{"position": 90 + i, "prefix": k, "valueFrom": v}
                              for i, (k, v) in enumerate(extra)]
        if not tool["requirements"]:
            del tool["requirements"]
    dest.write_text(yaml.safe_dump(wf, sort_keys=False), encoding="utf-8")
    return dest


def test_glossary_branch_joins_the_embeddings_at_load(
        tmp_path: Path, embeddings_url: str) -> None:  # noqa: F811
    cwltool = _cwltool()
    corpus = _corpus(tmp_path, ids=("1", "2", "3"))
    # Two shards, distinct basenames (#852): PMC1+PMC2, PMC3.
    shards = []
    for name, ids in (("shard-0000", ("1", "2")), ("shard-0001", ("3",))):
        p = tmp_path / f"{name}.jsonl"
        p.write_text("".join(json.dumps({"pmcid": f"PMC{n}", "xml_path": f"clean/ab/cd/PMC{n}.xml"})
                             + "\n" for n in ids), encoding="utf-8")
        shards.append(p)
    db = tmp_path / "registry.db"
    asyncio.run(SqliteCollectionStore(str(db)).put(CollectionSpec(
        id=CID, collection="store_scratch_847_jats", embedding_model="fake-model",
        embedding_model_dim=4, chunk_method="fixed", chunk_size=400, chunk_overlap=0)))
    job = tmp_path / "job.yml"
    job.write_text(yaml.safe_dump({
        "corpus": {"class": "Directory", "path": str(corpus)},
        "shards": [{"class": "File", "path": str(p)} for p in shards],
        "collection_id": CID, "registry_db": {"class": "File", "path": str(db)},
        "chunk_method": "fixed", "chunk_size": 400, "chunk_overlap": 0,
        "embedding_url": [embeddings_url], "embedding_model": "fake-model",
        "qdrant_url": "http://127.0.0.1:1", "es_url": "http://127.0.0.1:1",
        "max_chunks": 0,
    }), encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(PY_DIR)}
    for k in list(env):
        if k.startswith("COLLECTION_STORE_") or k == "RAGSTACK_COLLECTION_ID":
            env.pop(k)
    proc = subprocess.run(
        [cwltool, "--no-container", "--preserve-environment", "PYTHONPATH",
         "--outdir", str(tmp_path / "out"), str(_localised(tmp_path / "wf.cwl")), str(job)],
        capture_output=True, text=True, env=env, timeout=600,
    )
    assert proc.returncode == 0, proc.stderr[-6000:]
    out = json.loads(proc.stdout)

    from ragstack.ingestion.glossary_file import read_glossary_file

    gfile = read_glossary_file(out["glossary_file"]["path"])
    n_docs = gfile.header["counts"]["documents"]
    assert n_docs >= 3                                 # prose + table/figure units
    assert [Path(s["path"]).name for s in out["glossary_summaries"]] == [
        "shard-0000.glossary-summary.json", "shard-0001.glossary-summary.json"]
    summary = json.loads(Path(out["load_summary"]["path"]).read_text())
    assert summary["n_shards_failed"] == 0 and summary["n_chunks"] > 0
    # The load rendered from the merged file, and checked every document.
    gf = summary["glossary_file"]
    assert gf["counts"] == gfile.header["counts"]
    docs = gf["documents"]
    assert docs["unverified"] == 0 and docs["verified"] + docs["verified_ws"] == n_docs, docs
    # #849: max_chunks 0 from the job: no cap, and the summary says so.
    assert summary["chunk_cap"] == {"effective": 0, "source": "none"}
    # #821: the bulk receipts say what they are even unseeded.
    receipts = [json.loads(Path(r["path"]).read_text()) for r in out["receipts"]]
    assert [Path(r["path"]).name for r in out["receipts"]] == [
        "shard-0000.receipt.json", "shard-0001.receipt.json"]
    assert all(r["provenance"]["segmenter_version"] for r in receipts)
