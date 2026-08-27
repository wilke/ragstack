"""Run ``cwl/pdf-ingest-scatter.cwl`` (batch per task, #203 2b) through ``cwltool``.

What this proves, end to end through a real CWL runner: the ``batch``
ExpressionTool groups 5 PDFs into 3 batches of ``batch_size=2``; the two
dotproduct scatters produce ONE shard/report/receipt per batch; a scanned
(no-text) PDF is skipped by extract, folded into its batch's receipt by
``ingest_shard --extract-report`` with the constant ``NO_TEXT_ERROR`` and does
NOT fail its batch; the pack step emits one ``Directory`` named by the version
whose ``receipt.json`` is the 3 per-batch receipts with 5 per-document rows.

What it does not prove: the container or the stores. The tools' ``baseCommand``
points at ``/opt/ragstack/scripts`` inside ``ragstack-worker.sif`` (a build
artefact absent from a checkout), so the test rewrites each inlined tool to
this interpreter + the checkout's script, drops the ``DockerRequirement`` and
runs ``cwltool --no-container``; the ingest step is pointed at in-memory stores,
char-based chunking (no tokenizer) and a fake ``/v1/embeddings`` server on
localhost. Skips cleanly without ``cwltool`` on ``PATH``. Marked
``integration`` (it spawns a runner) — it needs no service.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")
pymupdf = pytest.importorskip("pymupdf")

from ragstack.ingestion.loaders import NO_TEXT_ERROR  # noqa: E402

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[3]
PY_DIR = REPO / "python"
CWL = REPO / "cwl" / "pdf-ingest-scatter.cwl"
IMAGE_CMD = "/opt/ragstack/scripts/"
DIM = 4


def _cwltool() -> str:
    exe = shutil.which("cwltool")
    if not exe:
        pytest.skip("cwltool not on PATH")
    return exe


class _Embeddings(BaseHTTPRequestHandler):
    """OpenAI-shaped ``/v1/embeddings``: a fixed 4-d vector per input."""

    def do_POST(self) -> None:  # noqa: N802 — http.server API
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        inputs = body.get("input") or []
        if isinstance(inputs, str):
            inputs = [inputs]
        data = [{"object": "embedding", "index": i,
                 "embedding": [float(len(t)) / 100, 1.0, 0.5, 0.25]}
                for i, t in enumerate(inputs)]
        out = json.dumps({"object": "list", "data": data, "model": body.get("model", "fake"),
                          "usage": {"prompt_tokens": 0, "total_tokens": 0}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_GET(self) -> None:  # noqa: N802
        out = b'{"data": []}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a) -> None:  # quiet
        pass


@pytest.fixture
def embeddings_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Embeddings)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _pdf(path: Path, text: str | None) -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    if text:
        page.insert_text((72, 72), text, fontsize=11)
    doc.save(str(path))
    doc.close()
    return path


def _localised_workflow(dest: Path) -> Path:
    """The workflow with every inlined CommandLineTool rewritten to run from
    the checkout without the image; the ingest tool gets the offline flags."""
    wf = yaml.safe_load(CWL.read_text(encoding="utf-8"))
    for name, step in wf["steps"].items():
        tool = step["run"]
        if tool["class"] != "CommandLineTool":
            continue
        cmd = tool["baseCommand"]
        assert cmd[0] == "python" and cmd[1].startswith(IMAGE_CMD), (name, cmd)
        tool["baseCommand"] = [sys.executable, str(PY_DIR / "scripts" / cmd[1][len(IMAGE_CMD):])]
        tool["requirements"].pop("DockerRequirement", None)
        if not tool["requirements"]:
            del tool["requirements"]
        if name == "ingest":
            tool["arguments"] += [
                {"position": 90, "prefix": "--vector-backend", "valueFrom": "memory"},
                {"position": 91, "prefix": "--text-backend", "valueFrom": "memory"},
                {"position": 92, "prefix": "--embedding-api", "valueFrom": "openai"},
                {"position": 93, "prefix": "--chunk-token-counter", "valueFrom": "estimate"},
            ]
    dest.write_text(yaml.safe_dump(wf, sort_keys=False), encoding="utf-8")
    return dest


def test_five_pdfs_in_batches_of_two_yield_three_tasks_and_five_rows(
    tmp_path: Path, embeddings_url: str
) -> None:
    cwltool = _cwltool()
    wf = _localised_workflow(tmp_path / "wf.cwl")
    src = tmp_path / "src"
    src.mkdir()
    texts = {
        "a.pdf": "Reciprocal rank fusion combines lexical and dense retrieval rankings. " * 6,
        "b.pdf": "Hybrid retrieval over scientific corpora needs chunking that respects sections. " * 6,
        "scan.pdf": None,  # image-only / scanned: no extractable text
        "d.pdf": "Neighbor links between adjacent chunks let the reader expand context. " * 6,
        "e.pdf": "A tombstone version records deleted document ids for replay. " * 6,
    }
    pdfs = [_pdf(src / name, text) for name, text in texts.items()]
    job = tmp_path / "job.yml"
    job.write_text(yaml.safe_dump({
        "pdfs": [{"class": "File", "path": str(p)} for p in pdfs],
        "batch_size": 2,
        "collection": "demo_batch", "tenant": "acme",
        "chunk_method": "fixed", "chunk_size": 200, "chunk_overlap": 20,
        "embedding_url": [embeddings_url], "embedding_model": "fake-model",
        "version": "3", "collection_id": "col-x", "spec_hash": "beef", "job_id": "j1",
        # qdrant_url/es_url are REQUIRED inputs with no default (#407) — omitting
        # them used to be legal only because the workflow defaulted them to
        # production. Dead addresses: the ingest tool is forced to the in-memory
        # backends below, so nothing dials them; a regression that started
        # honouring them would fail to connect rather than write somewhere real.
        "qdrant_url": "http://127.0.0.1:1", "es_url": "http://127.0.0.1:1",
    }), encoding="utf-8")
    outdir = tmp_path / "out"
    env = {**os.environ, "PYTHONPATH": str(PY_DIR)}
    proc = subprocess.run(
        [cwltool, "--no-container", "--preserve-environment", "PYTHONPATH",
         "--outdir", str(outdir), str(wf), str(job)],
        capture_output=True, text=True, env=env, timeout=600,
    )
    assert proc.returncode == 0, proc.stderr[-6000:]
    out = json.loads(proc.stdout)

    # ONE output: the archive Directory named by the version.
    assert list(out) == ["archive"]
    archive = out["archive"]
    assert archive["class"] == "Directory" and archive["basename"] == "3"
    names = sorted(e["basename"] for e in archive["listing"])
    assert names == ["chunks.jsonl.gz", "manifest.json", "receipt.json", "vectors.f32"]

    # 5 PDFs / batch_size 2 → 3 batches → 3 receipts, in batch order, 5 rows.
    receipts = json.loads((outdir / "3" / "receipt.json").read_text(encoding="utf-8"))
    assert isinstance(receipts, list) and len(receipts) == 3
    assert [r["shard_id"] for r in receipts] == ["batch-00000", "batch-00001", "batch-00002"]
    assert [r["n_docs"] for r in receipts] == [2, 2, 1]
    rows = [row for r in receipts for row in r["docs"]]
    assert len(rows) == 5
    by_name = {Path(row["source"]).name: row for row in rows}
    assert set(by_name) == set(texts)
    # The scanned PDF: its own row, the constant error verbatim, no chunks — and
    # its batch still COMPLETED (the neighbour was upserted).
    assert by_name["scan.pdf"]["error"] == NO_TEXT_ERROR
    assert by_name["scan.pdf"]["chunk_ids"] == []
    scan_batch = next(r for r in receipts if any(Path(d["source"]).name == "scan.pdf" for d in r["docs"]))
    assert scan_batch["status"] == "completed" and scan_batch["n_docs_failed"] == 1
    assert all(r["status"] == "completed" and r["error"] == "" for r in receipts)
    for name in ("a.pdf", "b.pdf", "d.pdf", "e.pdf"):
        assert by_name[name]["error"] == "" and by_name[name]["chunk_ids"]
    # Per-document ids partition the shard ids.
    for r in receipts:
        per_doc = [c for d in r["docs"] for c in d["chunk_ids"]]
        assert sorted(per_doc) == sorted(r["chunk_ids"])

    # The archive packs exactly the successful documents' chunks.
    from ragstack.ingestion.archive import read_version, verify_version
    m = verify_version(outdir / "3")
    assert (m["collection_id"], m["tenant"], m["spec_hash"], m["job_id"], m["version"]) == \
        ("col-x", "acme", "beef", "j1", 3)
    assert m["receipts"] == 3
    total = sum(r["n_chunks"] for r in receipts)
    assert m["counts"] == {"chunks": total, "docs": 4}
    packed = [c["id"] for c, vec in read_version(outdir / "3")]
    assert sorted(packed) == sorted(c for r in receipts for c in r["chunk_ids"])
    assert all(len(vec) == DIM for _, vec in read_version(outdir / "3"))
