"""Run ``cwl/pdf-ingest-scatter.cwl`` (batch per task, #203 2b) through ``cwltool``.

What this proves, end to end through a real CWL runner: the ``batch``
ExpressionTool groups 5 PDFs into 3 batches of ``batch_size=2``; the two
dotproduct scatters produce ONE shard/report/receipt per batch; a scanned
(no-text) PDF is skipped by extract, folded into its batch's receipt by
``ingest_shard --extract-report`` with the constant ``NO_TEXT_ERROR`` and does
NOT fail its batch; the pack step emits one ``Directory`` named by the version
whose ``receipt.json`` is the 3 per-batch receipts with 5 per-document rows.

#847 G2b: the ``glossary`` step runs per batch, ``ingest`` renders every
chunk's field from its file and ``pack`` archives the merged
``glossary.jsonl`` (manifest role ``glossary``). Two more runs drive the step
with ``glossary_llm: "on"`` against a fake OpenAI-compatible server on a real
127.0.0.1 socket (``tests.glossary_llm_support.serve``) — one that answers, and
a closed port, where every document degrades to the rules and the run still
completes.

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
from tests.glossary_llm_support import MODEL, Answer, FakeOpenAI, serve  # noqa: E402

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


def _pdf_lines(path: Path, lines: list[str]) -> Path:
    """A PDF whose text is ``lines``, one per line (no clipping at the margin)."""
    doc = pymupdf.open()
    page = doc.new_page()
    for i, line in enumerate(lines):
        page.insert_text((36, 48 + 14 * i), line, fontsize=9)
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


def _run_workflow(tmp_path: Path, pdfs: list[Path], job_extra: dict,
                  env_extra: dict[str, str] | None = None) -> dict:
    """cwltool over the localised workflow; the parsed output object."""
    cwltool = _cwltool()
    wf = _localised_workflow(tmp_path / "wf.cwl")
    job = tmp_path / "job.yml"
    job.write_text(yaml.safe_dump({
        "pdfs": [{"class": "File", "path": str(p)} for p in pdfs],
        "collection": "demo_batch", "tenant": "acme",
        "chunk_method": "fixed", "chunk_size": 200, "chunk_overlap": 20,
        "embedding_model": "fake-model",
        "version": "3", "collection_id": "col-x", "spec_hash": "beef", "job_id": "j1",
        "qdrant_url": "http://127.0.0.1:1", "es_url": "http://127.0.0.1:1",
        **job_extra,
    }), encoding="utf-8")
    preserve = ["--preserve-environment", "PYTHONPATH"]
    for name in env_extra or {}:
        preserve += ["--preserve-environment", name]
    env = {**os.environ, "PYTHONPATH": str(PY_DIR), **(env_extra or {})}
    proc = subprocess.run(
        [cwltool, "--no-container", *preserve, "--outdir", str(tmp_path / "out"),
         str(wf), str(job)],
        capture_output=True, text=True, env=env, timeout=600,
    )
    assert proc.returncode == 0, proc.stderr[-6000:]
    return json.loads(proc.stdout)


def _chunks(vdir: Path) -> list[dict]:
    import gzip

    with gzip.open(vdir / "chunks.jsonl.gz", "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _glossary_receipts(vdir: Path) -> list[dict]:
    receipts = json.loads((vdir / "receipt.json").read_text(encoding="utf-8"))
    receipts = receipts if isinstance(receipts, list) else [receipts]
    return [r["enrichment"]["symbol_glossary"] for r in receipts]


#: A paper that DEFINES a symbol and later states its value, split over lines.
_SYMBOL_LINES = [
    "We model airborne transmission in a ventilated room.",
    "The inhaled dose depends on the occupants and on the air.",
    "where Qb is the breathing flow rate of an occupant at rest.",
    *[f"Filler sentence number {i} keeps the definition and the value apart." for i in range(8)],
    "In the reference case we measured Qb = 0.5 m3/h for every occupant.",
]


def _glossary_pdfs(src: Path) -> list[Path]:
    src.mkdir()
    return [_pdf_lines(src / f"{n}.pdf", [f"Paper {n}.", *_SYMBOL_LINES]) for n in ("p", "q", "r")]


def _llm_job(endpoint: str) -> dict:
    return {"batch_size": 2, "glossary_llm": "on", "glossary_llm_endpoint": endpoint,
            "glossary_llm_model": MODEL, "glossary_llm_timeout_s": 5.0,
            "glossary_llm_max_tokens": 256, "glossary_llm_window_chars": 4000,
            "glossary_llm_max_windows": 2, "glossary_llm_concurrency": 2,
            "glossary_llm_api_key_env": "GLOSSARY_E2E_KEY"}


def test_glossary_llm_on_renders_the_model_half_into_archive_and_chunks(
    tmp_path: Path, embeddings_url: str
) -> None:
    from ragstack.ingestion.archive import verify_version
    from ragstack.ingestion.glossary_file import read_glossary_file

    fake = FakeOpenAI(default=Answer([{"symbol": "Qb", "definition": "breathing flow rate"}]))
    pdfs = _glossary_pdfs(tmp_path / "src")
    with serve(fake) as endpoint:
        out = _run_workflow(tmp_path, pdfs, {"embedding_url": [embeddings_url], **_llm_job(endpoint)},
                            env_extra={"GLOSSARY_E2E_KEY": "sk-e2e-not-a-secret"})
    vdir = tmp_path / "out" / "3"
    names = sorted(e["basename"] for e in out["archive"]["listing"])
    assert "glossary.jsonl" in names
    m = verify_version(vdir)                       # the manifest's glossary role verifies
    assert m["files"]["glossary"] == "glossary.jsonl"
    gfile = read_glossary_file(vdir / "glossary.jsonl")
    assert gfile.header["counts"]["documents"] == 3 == gfile.header["counts"]["with_llm"]
    assert gfile.header["counts"]["degraded"] == 0
    for g in _glossary_receipts(vdir):             # 3 PDFs, batch 2 -> 2 receipts
        assert g["source"] == "glossary_file"
        assert g["counts"]["with_llm"] == g["counts"]["documents"] and g["counts"]["degraded"] == 0
    chunks = _chunks(vdir)
    versions = {c["metadata"].get("symbol_glossary_version") for c in chunks}
    assert versions and all(v.startswith("llm/2:") and v.endswith("@file/1") for v in versions), versions
    assert any("breathing flow rate" in (c.get("symbol_glossary") or "") for c in chunks)
    # The model was really called, with the limits the job gave and the key
    # read from the NAMED variable (never an input).
    assert fake.chats and all(s.body["max_tokens"] == 256 for s in fake.chats)
    assert all(s.authorized for s in fake.seen)
    assert "sk-e2e-not-a-secret" not in (vdir / "glossary.jsonl").read_text()


def test_glossary_endpoint_down_degrades_every_document_and_the_run_completes(
    tmp_path: Path, embeddings_url: str
) -> None:
    from ragstack.ingestion.glossary_file import read_glossary_file

    pdfs = _glossary_pdfs(tmp_path / "src")
    _run_workflow(tmp_path, pdfs, {"embedding_url": [embeddings_url],
                                   **_llm_job("http://127.0.0.1:1")})
    vdir = tmp_path / "out" / "3"
    gfile = read_glossary_file(vdir / "glossary.jsonl")
    assert gfile.header["counts"]["degraded"] == 3 and gfile.header["counts"]["with_llm"] == 0
    assert gfile.degraded_reasons == {"endpoint_unavailable": 3}
    receipts = _glossary_receipts(vdir)
    assert sum(g["counts"]["degraded"] for g in receipts) == 3
    assert all(set(g["degraded_reasons"]) == {"endpoint_unavailable"} for g in receipts)
    versions = {v for g in receipts for v in g["glossary_versions"]}
    assert versions and all(v.startswith("rules/") for v in versions), versions
    stamps = {c["metadata"].get("symbol_glossary_version") for c in _chunks(vdir)}
    assert stamps and all(s.startswith("rules/") and s.endswith("@file/1") for s in stamps), stamps


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
    # #847 G2b: the merged glossary file rides in the version.
    assert names == ["chunks.jsonl.gz", "glossary.jsonl", "manifest.json", "receipt.json",
                     "vectors.f32"]

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
    # #783: the job names no symbol_glossary, so the workflow default (rules)
    # reaches ingest_shard --symbol-glossary and every batch records the step.
    # #847 G2b: rendered from the glossary step's file (glossary_llm "off" by
    # default: rules only), so the record is the file's, not an extractor's.
    for r in receipts:
        g = r["enrichment"]["symbol_glossary"]
        assert g["source"] == "glossary_file", g
        assert g["counts"]["with_llm"] == 0 and g["counts"]["degraded"] == 0
    assert all(v.startswith("rules/") for r in receipts
               for v in r["enrichment"]["symbol_glossary"]["glossary_versions"])
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
