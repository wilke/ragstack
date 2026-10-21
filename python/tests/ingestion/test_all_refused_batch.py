"""A batch in which EVERY document is refused by the duplicate guard (#768).

Before the fix, ``IngestionPipeline.embed_documents`` still embedded the empty
chunk list; on the GoWe worker that reached ``PooledEmbedder.embed_isolated([])``,
vLLM answered the empty input with a 400, and the bisect recursed on ``[]``
until RecursionError — ``ingest_shard`` exited 1, the engine retried three
times and the job item read ``gowe submission FAILED`` instead of
``duplicate_work: key=… existing_doc_id=…``.

The embedder here is the REAL ``PooledEmbedder`` over an endpoint that 400s on
an empty batch, like vLLM, so the original failure is reproduced rather than
papered over by a fake that accepts ``[]``. No live store: in-memory backends,
and every store URL is ``http://127.0.0.1:1``.

The chain is followed past the ingest task, because a single refused upload
is the common case and then EVERY batch of the run is all-refused: the
``pack`` step (``archive_version.py``) has zero rows, ``ragstack-archive/1``
has no zero-row version (the #357 residual), so it exits
``NOTHING_TO_ARCHIVE_EXIT_CODE`` with each document's reason on stderr — and
``GoWeBackend`` lifts those onto the items instead of failing them opaquely.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

from ragstack.documents import DocumentSummary
from ragstack.embed_pool import Endpoint, PooledEmbedder
from ragstack.ingestion.chunkers import RecursiveCharacterChunker
from ragstack.ingestion.embedding_file import SCHEMA as EMBEDDING_FILE_SCHEMA
from ragstack.ingestion.gowe_backend import (
    NOTHING_TO_ARCHIVE_UNLISTED,
    GoWeBackend,
    nothing_to_archive_of,
    tool_exit_code,
)
from ragstack.ingestion.loaders import JsonlLoader
from ragstack.ingestion.manifest import WorkItem
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.ingestion.receipts import (
    COMPLETED,
    NOTHING_TO_ARCHIVE,
    NOTHING_TO_ARCHIVE_EXIT_CODE,
    NOTHING_TO_ARCHIVE_STDERR_BUDGET,
    DocRow,
    ShardReceipt,
    format_nothing_to_archive,
    parse_nothing_to_archive,
)
from ragstack.ingestion.shard import run_shard
from ragstack.ingestion.work_identity import is_duplicate_refusal
from ragstack.jobstore import FAILED as JOB_FAILED
from ragstack.stores.memory import InMemoryTextIndex, InMemoryVectorStore

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import archive_version  # noqa: E402
import ingest_shard  # noqa: E402

DEAD = "http://127.0.0.1:1"
DOI = "10.1073/pnas.2018995118"
OTHER_DOI = "10.1128/jvi.02415-06"
EXISTING = "existing-doc-a"


def _status_error(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "http://vllm/v1/embeddings")
    return httpx.HTTPStatusError("err", request=req, response=httpx.Response(code, request=req))


class _VllmLike:
    """An endpoint embedder that 400s on an empty batch, like vLLM. Bounded so
    an unfixed run fails fast with RecursionError instead of grinding."""

    def __init__(self) -> None:
        self.calls = 0
        self.empty_calls = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if not texts:
            self.empty_calls += 1
            if self.empty_calls > 50:
                raise RecursionError("bisected an empty input 50 times")
            raise _status_error(400)
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


def _pool(http: httpx.AsyncClient, ep: _VllmLike) -> PooledEmbedder:
    return PooledEmbedder([Endpoint(ep, "http://vllm/health")], http=http)


def _paper(path: str, header: str) -> str:
    body = " ".join(f"Indoor airborne transmission passage {i}." for i in range(30))
    return json.dumps({"text": f"{header}\n\n{body}", "path": path})


def _shard(tmp_path: Path, name: str, *lines: str) -> str:
    p = tmp_path / name
    p.write_text("\n".join(lines), encoding="utf-8")
    return str(p)


class _IndexWithA(InMemoryTextIndex):
    """The fake duplicate lookup: ``DOI`` is already indexed as ``EXISTING``."""

    async def find_documents_by_key(self, keys, tenants, *, limit=20):
        hit = DocumentSummary(doc_id=EXISTING, source="/stage/a.pdf", chunk_count=3,
                              metadata={"doi": DOI})
        return {k: [hit] for k in keys if k == ("doi", DOI)}


def _pipeline(embedder) -> IngestionPipeline:
    return IngestionPipeline(loader=JsonlLoader(), chunker=RecursiveCharacterChunker(),
                             embedder=embedder, vector_store=InMemoryVectorStore(),
                             text_index=_IndexWithA())


# --------------------------------------------------------------------------- #
# the pipeline
# --------------------------------------------------------------------------- #

async def test_embed_documents_with_every_document_refused_makes_no_embed_call(tmp_path):
    ep = _VllmLike()
    async with httpx.AsyncClient() as http:
        pipe = _pipeline(_pool(http, ep))
        docs = pipe.loader.load(_shard(
            tmp_path, "b.jsonl",
            _paper("/stage/sub_2/b.pdf", f"DOI: {DOI}"),
            _paper("/stage/sub_2/c.pdf", f"DOI: {DOI}")))
        kept, produced, quarantined = await pipe.embed_documents(
            docs, tenant_id="dev", source="b.jsonl", duplicate_policy="refuse")
    assert (kept, produced, quarantined) == ([], 0, 0)
    assert ep.calls == 0
    from ragstack.ingestion.work_identity import refusal_of
    assert all(is_duplicate_refusal(refusal_of(d)) for d in docs)


async def test_embed_documents_on_a_plain_embedder_that_400s_on_empty(tmp_path):
    """The same with an embedder that has no ``embed_isolated`` (the pipeline's
    other branch): an all-refused batch never sends the empty request."""
    ep = _VllmLike()
    pipe = _pipeline(ep)
    docs = pipe.loader.load(_shard(tmp_path, "b.jsonl",
                                   _paper("/stage/sub_2/b.pdf", f"DOI: {DOI}")))
    kept, produced, quarantined = await pipe.embed_documents(
        docs, tenant_id="dev", duplicate_policy="refuse")
    assert (kept, produced, quarantined) == ([], 0, 0) and ep.calls == 0


# --------------------------------------------------------------------------- #
# run_shard
# --------------------------------------------------------------------------- #

async def test_run_shard_all_refused_completes_with_duplicate_work_rows(tmp_path):
    ep = _VllmLike()
    emb = tmp_path / "b.emb.jsonl"
    async with httpx.AsyncClient() as http:
        pipe = _pipeline(_pool(http, ep))
        receipt = await run_shard(pipe, _shard(
            tmp_path, "b.jsonl",
            _paper("/stage/sub_2/b.pdf", f"DOI: {DOI}"),
            _paper("/stage/sub_2/c.pdf", f"PMCID: PMC8092463 · DOI: {DOI}")),
            "dev", "b", embedding_file=emb, duplicate_policy="refuse")
    assert receipt.status == COMPLETED and receipt.error == ""
    assert receipt.n_docs == 2 and receipt.n_docs_failed == 2 and receipt.n_chunks == 0
    for row in receipt.docs:
        assert row.error.startswith(f"duplicate_work: key=doi:{DOI} existing_doc_id={EXISTING}")
    # the archive step still gets a (header-only) embedding file for this batch
    lines = emb.read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["schema"] == EMBEDDING_FILE_SCHEMA
    assert ep.calls == 0


# --------------------------------------------------------------------------- #
# ingest_shard end to end (amain, memory backends, real PooledEmbedder)
# --------------------------------------------------------------------------- #

def _argv(shard: str, out: Path, emb: Path) -> list[str]:
    return [shard, "--out", str(out), "--embedding-file", str(emb),
            "--chunk-method", "fixed", "--chunk-token-counter", "estimate",
            "--vector-backend", "memory", "--text-backend", "memory",
            "--qdrant-url", DEAD, "--es-url", DEAD, "--embedding-url", DEAD,
            "--embedding-model", "test/model", "--duplicate-policy", "refuse",
            "--tenant", "dev"]


def _run_amain(monkeypatch, argv: list[str]) -> tuple[int, _VllmLike]:
    ep = _VllmLike()
    monkeypatch.setattr(ingest_shard, "_build_embedder", lambda args, http: _pool(http, ep))
    monkeypatch.setattr(ingest_shard, "InMemoryTextIndex", _IndexWithA)
    rc = asyncio.run(ingest_shard.amain(ingest_shard.parse_args(argv)))
    return rc, ep


def test_ingest_shard_all_refused_batch_exits_0_with_per_document_outcomes(
        tmp_path, monkeypatch, capsys):
    shard = _shard(tmp_path, "batch0.jsonl",
                   _paper("/stage/sub_2/b.pdf", f"DOI: {DOI}"),
                   _paper("/stage/sub_2/c.pdf", f"DOI: {DOI}"))
    out, emb = tmp_path / "receipt.json", tmp_path / "batch0.emb.jsonl"
    rc, ep = _run_amain(monkeypatch, _argv(shard, out, emb))
    assert rc == 0
    receipt = ShardReceipt.load(out)
    assert receipt.status == COMPLETED and receipt.n_docs_failed == 2
    assert [Path(r.source).name for r in receipt.docs] == ["b.pdf", "c.pdf"]
    for row in receipt.docs:
        assert row.error.startswith(f"duplicate_work: key=doi:{DOI} existing_doc_id={EXISTING}")
    assert len(emb.read_text().splitlines()) == 1  # header-only
    assert "duplicate_work" in capsys.readouterr().out
    assert ep.calls == 0


def test_ingest_shard_mixed_batch_reports_the_refusal_like_before(tmp_path, monkeypatch):
    shard = _shard(tmp_path, "batch0.jsonl",
                   _paper("/stage/sub_2/b.pdf", f"DOI: {DOI}"),
                   _paper("/stage/sub_2/new.pdf", f"DOI: {OTHER_DOI}"))
    out, emb = tmp_path / "receipt.json", tmp_path / "batch0.emb.jsonl"
    rc, ep = _run_amain(monkeypatch, _argv(shard, out, emb))
    assert rc == 0
    receipt = ShardReceipt.load(out)
    refused, admitted = receipt.docs
    assert receipt.status == COMPLETED and receipt.n_docs_failed == 1
    assert refused.error.startswith(f"duplicate_work: key=doi:{DOI} existing_doc_id={EXISTING}")
    assert refused.chunk_ids == []
    assert admitted.error == "" and admitted.chunk_ids
    assert len(emb.read_text().splitlines()) == 1 + len(admitted.chunk_ids)
    assert ep.calls >= 1 and ep.empty_calls == 0


# --------------------------------------------------------------------------- #
# the pack step over the resulting outputs, and the API's reading of it
# --------------------------------------------------------------------------- #

def _pack(tmp_path: Path, embs: list[Path], receipts: list[Path]) -> int:
    return archive_version.main(
        ["--version", "7", "--chunks", *map(str, embs), "--receipt", *map(str, receipts),
         "--out", str(tmp_path / "out"), "--collection-id", "lib1", "--tenant", "dev",
         "--workers", "1"])


def test_pack_of_an_all_refused_run_names_each_document(tmp_path, monkeypatch, capsys):
    """The single-upload case: every batch all-refused, so the pack step has
    nothing to archive. It exits with its own code and puts every document's
    reason FIRST on stderr, inside the engine's 1000-character window."""
    embs, receipts = [], []
    for i, names in enumerate([["b.pdf", "c.pdf"], ["d.pdf"]]):
        shard = _shard(tmp_path, f"batch{i}.jsonl",
                       *[_paper(f"/stage/sub_2/{n}", f"DOI: {DOI}") for n in names])
        out, emb = tmp_path / f"r{i}.json", tmp_path / f"batch{i}.emb.jsonl"
        assert _run_amain(monkeypatch, _argv(shard, out, emb))[0] == 0
        embs.append(emb)
        receipts.append(out)
    capsys.readouterr()

    rc = _pack(tmp_path, embs, receipts)

    assert rc == NOTHING_TO_ARCHIVE_EXIT_CODE
    err = capsys.readouterr().err
    window = err[:1000]
    assert window.startswith(f"{NOTHING_TO_ARCHIVE}: docs=3 failed=3 listed=3")
    by_name = parse_nothing_to_archive(window)
    assert set(by_name) == {"b.pdf", "c.pdf", "d.pdf"}
    assert all(e.startswith(f"duplicate_work: key=doi:{DOI} existing_doc_id={EXISTING}")
               for e in by_name.values())
    assert not (tmp_path / "out" / "7").exists()

    # …and the backend lifts it onto the items from the engine's failure record.
    record = {"state": "FAILED", "error": {
        "code": "TASK_FAILED", "message": "task pack failed",
        "context": {"stderr": window, "exit_code": rc}}}
    assert nothing_to_archive_of(record) == by_name


def test_pack_of_a_mixed_run_still_writes_the_version(tmp_path, monkeypatch):
    shard = _shard(tmp_path, "batch0.jsonl",
                   _paper("/stage/sub_2/b.pdf", f"DOI: {DOI}"),
                   _paper("/stage/sub_2/new.pdf", f"DOI: {OTHER_DOI}"))
    out, emb = tmp_path / "r0.json", tmp_path / "batch0.emb.jsonl"
    assert _run_amain(monkeypatch, _argv(shard, out, emb))[0] == 0
    assert _pack(tmp_path, [emb], [out]) == 0
    packed = json.loads((tmp_path / "out" / "7" / "receipt.json").read_text())
    assert packed["docs"][0]["error"].startswith("duplicate_work: ")


def test_stderr_listing_stays_inside_the_engine_window() -> None:
    rows = [DocRow(doc_id=f"d{i}", source=f"/stage/sub/{i:03d}-paper.pdf",
                   error=f"duplicate_work: key=doi:10.1000/x{i} existing_doc_id={'e' * 64}")
            for i in range(40)]
    text = format_nothing_to_archive([ShardReceipt("s", "t", COMPLETED, docs=rows)])
    assert len(text) <= NOTHING_TO_ARCHIVE_STDERR_BUDGET
    label, *listed = text.splitlines()
    assert label.startswith(f"{NOTHING_TO_ARCHIVE}: docs=40 failed=40 listed={len(listed)}")
    assert 0 < len(listed) < 40
    assert len(parse_nothing_to_archive(text)) == len(listed)
    # a window that cuts the last line mid-object loses only that line
    assert len(parse_nothing_to_archive(text[:-5])) == len(listed) - 1


class _FailedEngine:
    def __init__(self, exit_code: int, stderr: str) -> None:
        self.record = {"id": "sub_x", "state": "FAILED", "error": {
            "code": "TASK_FAILED", "message": "task pack failed",
            "context": {"stderr": stderr, "exit_code": exit_code}}}

    async def register_workflow(self, *a, **k):
        return "wf"

    async def submit(self, *a, **k):
        return {"id": "sub_x", "state": "PENDING"}

    async def wait(self, *a, **k):
        return dict(self.record)


async def _run(engine) -> list:
    backend = GoWeBackend(engine, "cwlVersion: v1.2", poll_interval=0, timeout=1)
    run = await backend.run_submission(
        [WorkItem(item_id="b", source="ws:///u/home/lib/sources/b.pdf"),
         WorkItem(item_id="z", source="ws:///u/home/lib/sources/z.pdf")],
        inputs={"version": "7"}, token="t", output_destination="ws:///u/home/lib/versions/")
    assert run.state == "FAILED"
    return run.results


async def test_gowe_backend_maps_nothing_to_archive_per_document() -> None:
    refusal = f"duplicate_work: key=doi:{DOI} existing_doc_id={EXISTING}"
    stderr = format_nothing_to_archive([ShardReceipt("s", "t", COMPLETED, docs=[
        DocRow(doc_id="b", source="/tmp/gowe-ws-stage/sub_1/b.pdf", error=refusal)])])
    results = await _run(_FailedEngine(NOTHING_TO_ARCHIVE_EXIT_CODE, stderr + "\narchive: no"))
    by_id = {r.item_id: r for r in results}
    assert by_id["b"].status == JOB_FAILED and by_id["b"].error == refusal
    # an item whose reason did not fit the window is labelled, not left opaque
    assert by_id["z"].status == JOB_FAILED and by_id["z"].error == NOTHING_TO_ARCHIVE_UNLISTED
    # the exit code is authoritative: the same stderr under exit 1 is a plain failure
    results = await _run(_FailedEngine(1, stderr))
    assert {r.error for r in results} == {"gowe submission FAILED"}


@pytest.mark.parametrize("record", [{}, {"error": "boom"},
                                    {"error": {"context": {"exit_code": "x"}}}])
def test_nothing_to_archive_of_ignores_other_failures(record) -> None:
    assert nothing_to_archive_of(record) is None


# --------------------------------------------------------------------------- #
# the REAL engine record shape (GoWe 0.26.1, the #771 review)
# --------------------------------------------------------------------------- #

#: What GoWe's worker prepends to a failed apptainer tool's stderr.
EXEC_PREFIX = "execute: apptainer command failed (exit {code}): "


def real_record(code: int, tool_stderr: str, step: str = "pack") -> dict:
    """A FAILED submission exactly as the deployed engine records it: the
    context's ``exit_code`` is ALWAYS -1 for a failed apptainer tool, the real
    code survives only in the worker's prefix, and the stderr is cut to 1000
    characters with a ``...(truncated)`` marker (scheduler buildSubmissionError)."""
    stderr = EXEC_PREFIX.format(code=code) + tool_stderr
    if len(stderr) > 1000:
        stderr = stderr[:1000] + "...(truncated)"
    return {"id": "sub_x", "state": "FAILED", "error": {
        "code": "TASK_FAILED", "message": f"step '{step}' task failed with exit code -1",
        "context": {"step_id": step, "exit_code": -1, "stderr": stderr}}}


def _refusals(n: int) -> list[ShardReceipt]:
    return [ShardReceipt("batch0", "t", COMPLETED, docs=[
        DocRow(doc_id=f"d{i}", source=f"/tmp/gowe-ws-stage/sub_1/{i:03d}-paper.pdf",
               error=f"duplicate_work: key=doi:10.1000/x{i} existing_doc_id={'e' * 64}")
        for i in range(n)])]


def test_tool_exit_code_reads_the_worker_prefix_when_the_engine_says_minus_1() -> None:
    assert tool_exit_code(real_record(5, "nothing_to_archive: docs=1")) == 5
    assert tool_exit_code(real_record(4, "chunk_cap_exceeded: live=1")) == 4
    # a future engine that records the real code: the context wins
    rec = real_record(1, "boom")
    rec["error"]["context"]["exit_code"] = 5
    assert tool_exit_code(rec) == 5
    # the prefix only in the message
    assert tool_exit_code({"error": {"message": "x: command failed (exit 3): y",
                                     "context": {"exit_code": -1}}}) == 3
    assert tool_exit_code({"error": {"context": {"exit_code": -1, "stderr": "oops"}}}) is None
    assert tool_exit_code({}) is None


async def test_real_engine_record_exit_5_maps_each_document() -> None:
    refusal = f"duplicate_work: key=doi:{DOI} existing_doc_id={EXISTING}"
    tool = format_nothing_to_archive([ShardReceipt("s", "t", COMPLETED, docs=[
        DocRow(doc_id="b", source="/tmp/gowe-ws-stage/sub_1/b.pdf", error=refusal)])])
    engine = _FailedEngine(0, "")
    engine.record = real_record(NOTHING_TO_ARCHIVE_EXIT_CODE, tool + "\narchive: no chunk records")
    by_id = {r.item_id: r for r in await _run(engine)}
    assert by_id["b"].error == refusal
    assert by_id["z"].error == NOTHING_TO_ARCHIVE_UNLISTED


def test_real_engine_exit_5_needs_the_label_line() -> None:
    """Exit 5 is not reserved by the engine: without the label it is not ours."""
    assert nothing_to_archive_of(real_record(5, "Traceback: something else")) is None
    assert nothing_to_archive_of(real_record(1, "nothing_to_archive: docs=1")) is None
    assert nothing_to_archive_of(real_record(5, "nothing_to_archive: docs=0 failed=0 "
                                                "listed=0")) == {}


def test_prefix_plus_the_largest_listing_fits_the_engine_window() -> None:
    """The worker's ~44-character prefix sits INSIDE the 1000 kept characters:
    the label and every listed line must survive it uncut."""
    tool = format_nothing_to_archive(_refusals(200))
    payload = EXEC_PREFIX.format(code=NOTHING_TO_ARCHIVE_EXIT_CODE) + tool
    assert len(payload) <= 1000
    listed = len(tool.splitlines()) - 1
    assert listed > 0
    got = nothing_to_archive_of(
        real_record(NOTHING_TO_ARCHIVE_EXIT_CODE, tool + "\narchive: no chunk records"))
    assert got is not None and len(got) == listed
