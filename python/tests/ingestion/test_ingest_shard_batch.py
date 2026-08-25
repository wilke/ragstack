"""Batch-per-task ingest (#203 2b, Option B): one shard = N documents.

The rules under test, at the ``run_shard`` core AND through the ``ingest_shard``
CLI (its exit code is what fails a GoWe task):

* a per-document failure — a scanned PDF the extract stage skipped
  (``NO_TEXT_ERROR`` from its report), a document with no embeddable chunk —
  is recorded on ITS row and the batch continues (exit 0);
* the embedding file holds only the successful documents' chunks;
* the task fails (non-zero) only when EVERY document of the batch failed, and
  then every row names its own error — the constant one survives verbatim;
* a batch-level failure (infra) is attributed to every document that has no
  more specific error.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from ragstack.ingestion.chunkers import RecursiveCharacterChunker
from ragstack.ingestion.embedding_file import read_embedding_file
from ragstack.ingestion.loaders import NO_TEXT_ERROR, JsonlLoader, deterministic_doc_id
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.ingestion.receipts import COMPLETED, FAILED, NO_CHUNKS_ERROR, ShardReceipt
from ragstack.ingestion.shard import NOT_EXTRACTED_ERROR, ExtractReport, run_shard
from ragstack.stores.memory import InMemoryTextIndex, InMemoryVectorStore

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import ingest_shard  # noqa: E402

TEXT = ("Document {i} about reciprocal rank fusion and hybrid retrieval in "
        "scientific corpora. Passage body {i}, long enough to chunk.")


class _FakeEmbedder:
    """Embeds everything; ``poison`` texts are quarantined (vector None)."""

    def __init__(self, poison: str = "") -> None:
        self.poison = poison

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]

    async def embed_isolated(self, texts):
        vectors = [None if self.poison and self.poison in t else [0.1, 0.2, 0.3, 0.4]
                   for t in texts]
        return vectors, sum(v is None for v in vectors)


def _pipeline(embedder=None, vstore=None) -> tuple[IngestionPipeline, InMemoryVectorStore]:
    vstore = vstore or InMemoryVectorStore()
    pipe = IngestionPipeline(loader=JsonlLoader(), chunker=RecursiveCharacterChunker(),
                             embedder=embedder or _FakeEmbedder(), vector_store=vstore,
                             text_index=InMemoryTextIndex())
    return pipe, vstore


def _batch(tmp_path: Path, n_text: int, n_scanned: int, *, name: str = "batch") -> tuple[str, str]:
    """A batch of ``n_text + n_scanned`` PDFs as the extract stage leaves it: a
    shard with one record per text PDF and a report whose ``skipped`` lists the
    scanned ones with the constant error, plus every input path."""
    pdfs = [str(tmp_path / f"p{i:02d}.pdf") for i in range(n_text + n_scanned)]
    text, scanned = pdfs[:n_text], pdfs[n_text:]
    shard = tmp_path / f"{name}.jsonl"
    shard.write_text("".join(json.dumps({"text": TEXT.format(i=i), "path": p}) + "\n"
                             for i, p in enumerate(text)), encoding="utf-8")
    report = tmp_path / f"{name}.report.json"
    report.write_text(json.dumps({
        "out": str(shard), "n_input": len(pdfs), "n_extracted": n_text, "n_skipped": n_scanned,
        "skipped": [{"path": p, "reason": f"no extractable text in PDF '{Path(p).name}' (scanned PDF?)",
                     "error": NO_TEXT_ERROR} for p in scanned],
        "inputs": pdfs,
    }), encoding="utf-8")
    return str(shard), str(report)


# --------------------------------------------------------------------------- #
# run_shard core
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_mixed_batch_two_scanned_of_twenty_is_exact_per_document(tmp_path: Path) -> None:
    pipe, vstore = _pipeline()
    shard, report = _batch(tmp_path, 18, 2)
    emb = tmp_path / "batch.emb.jsonl"
    r = await run_shard(pipe, shard, "public", "batch-0", embedding_file=emb,
                        report=ExtractReport.load(report))

    assert r.status == COMPLETED and r.error == ""
    assert r.n_docs == 20 and r.n_docs_failed == 2
    assert len(r.docs) == 20
    ok = [d for d in r.docs if d.ok]
    bad = [d for d in r.docs if not d.ok]
    assert len(ok) == 18 and len(bad) == 2
    # The scanned PDFs carry the constant error VERBATIM — countable per job.
    assert {d.error for d in bad} == {NO_TEXT_ERROR}
    assert {Path(d.source).name for d in bad} == {"p18.pdf", "p19.pdf"}
    assert all(d.chunk_ids == [] for d in bad)
    # Every successful document has its own chunk ids; together they are the
    # shard's chunk ids (no chunk attributed to two documents).
    per_doc = [cid for d in ok for cid in d.chunk_ids]
    assert all(d.chunk_ids for d in ok)
    assert sorted(per_doc) == sorted(r.chunk_ids) and len(set(per_doc)) == len(per_doc)
    assert r.n_chunks == len(r.chunk_ids) == await vstore.count_tenants(["public"])
    # doc ids match what the PDF loader would have minted for the source path.
    for d in ok:
        assert d.doc_id == deterministic_doc_id(d.source)
    # The embedding file holds exactly the successful documents' chunks.
    chunks, header = read_embedding_file(emb)
    assert header["count"] == len(r.chunk_ids)
    assert sorted(c.id for c in chunks) == sorted(r.chunk_ids)
    assert {c.doc_id for c in chunks} == {d.doc_id for d in ok}
    assert r.embedding_file == str(emb)


@pytest.mark.asyncio
async def test_all_failed_batch_reports_every_document(tmp_path: Path) -> None:
    pipe, vstore = _pipeline()
    shard, report = _batch(tmp_path, 0, 3)  # nothing but scanned PDFs → empty shard
    emb = tmp_path / "batch.emb.jsonl"
    r = await run_shard(pipe, shard, "public", "batch-1", embedding_file=emb,
                        report=ExtractReport.load(report))
    assert r.status == FAILED
    assert r.n_docs == 3 and r.n_docs_failed == 3 and r.n_chunks == 0
    assert [d.error for d in r.docs] == [NO_TEXT_ERROR] * 3
    assert r.error == NO_TEXT_ERROR  # the one common per-document error
    assert not emb.exists() and r.embedding_file == ""
    assert await vstore.count_tenants(["public"]) == 0


@pytest.mark.asyncio
async def test_document_with_no_embeddable_chunk_fails_alone(tmp_path: Path) -> None:
    """A loaded document whose every chunk is quarantined gets NO_CHUNKS_ERROR;
    the rest of the batch is upserted."""
    pipe, vstore = _pipeline(embedder=_FakeEmbedder(poison="Document 1 "))
    shard, report = _batch(tmp_path, 3, 0)
    r = await run_shard(pipe, shard, "public", "b", report=ExtractReport.load(report))
    assert r.status == COMPLETED and r.n_docs_failed == 1
    errors = {Path(d.source).name: d.error for d in r.docs}
    assert errors == {"p00.pdf": "", "p01.pdf": NO_CHUNKS_ERROR, "p02.pdf": ""}
    assert await vstore.count_tenants(["public"]) == r.n_chunks > 0


@pytest.mark.asyncio
async def test_every_document_unembeddable_is_a_failed_batch(tmp_path: Path) -> None:
    pipe, _ = _pipeline(embedder=_FakeEmbedder(poison="Document"))
    shard, report = _batch(tmp_path, 2, 1)
    emb = tmp_path / "e.jsonl"
    r = await run_shard(pipe, shard, "public", "b", embedding_file=emb,
                        report=ExtractReport.load(report))
    assert r.status == FAILED and r.error.startswith("empty:")
    assert {Path(d.source).name: d.error for d in r.docs} == {
        "p00.pdf": NO_CHUNKS_ERROR, "p01.pdf": NO_CHUNKS_ERROR, "p02.pdf": NO_TEXT_ERROR}
    assert not emb.exists()


@pytest.mark.asyncio
async def test_batch_level_failure_is_attributed_to_every_document(tmp_path: Path) -> None:
    """An upsert failure (infra) fails the batch: every document without a more
    specific error carries the batch error; the scanned one keeps its own."""

    class _BoomStore(InMemoryVectorStore):
        async def upsert(self, chunks):
            raise RuntimeError("qdrant down")

    pipe, _ = _pipeline(vstore=_BoomStore())
    shard, report = _batch(tmp_path, 2, 1)
    emb = tmp_path / "e.jsonl"
    r = await run_shard(pipe, shard, "public", "b", embedding_file=emb,
                        report=ExtractReport.load(report))
    assert r.status == FAILED and "qdrant down" in r.error
    by_name = {Path(d.source).name: d for d in r.docs}
    assert by_name["p00.pdf"].error == r.error and by_name["p01.pdf"].error == r.error
    assert by_name["p02.pdf"].error == NO_TEXT_ERROR
    assert all(d.chunk_ids == [] for d in r.docs) and r.chunk_ids == []
    assert not emb.exists()  # never a stale file next to a failed receipt


@pytest.mark.asyncio
async def test_embed_failure_is_a_batch_failure(tmp_path: Path) -> None:
    class _Down:
        async def embed(self, texts):
            raise ConnectionError("fleet unreachable")

    pipe, _ = _pipeline(embedder=_Down())
    shard, report = _batch(tmp_path, 2, 0)
    r = await run_shard(pipe, shard, "public", "b", report=ExtractReport.load(report))
    assert r.status == FAILED and "fleet unreachable" in r.error
    assert [d.error for d in r.docs] == [r.error] * 2


@pytest.mark.asyncio
async def test_reported_input_missing_from_shard_is_a_failed_row(tmp_path: Path) -> None:
    """A path the extract report lists as an input but neither delivered nor
    skipped is accounted for (never silently dropped from the batch)."""
    pipe, _ = _pipeline()
    shard, report = _batch(tmp_path, 2, 0)
    rep = json.loads(Path(report).read_text())
    rep["inputs"].append(str(tmp_path / "ghost.pdf"))
    r = await run_shard(pipe, shard, "public", "b", report=ExtractReport.from_dict(rep))
    assert r.status == COMPLETED and r.n_docs == 3 and r.n_docs_failed == 1
    assert {Path(d.source).name: d.error for d in r.docs}["ghost.pdf"] == NOT_EXTRACTED_ERROR


@pytest.mark.asyncio
async def test_without_a_report_an_empty_shard_is_still_a_load_failure(tmp_path: Path) -> None:
    pipe, _ = _pipeline()
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    r = await run_shard(pipe, str(empty), "public", "e")
    assert r.status == FAILED and r.error.startswith("load:") and r.docs == []


@pytest.mark.asyncio
async def test_no_text_row_survives_the_receipt_round_trip(tmp_path: Path) -> None:
    pipe, _ = _pipeline()
    shard, report = _batch(tmp_path, 1, 1)
    r = await run_shard(pipe, shard, "public", "b", report=ExtractReport.load(report))
    back = ShardReceipt.from_dict(json.loads(r.to_json()))
    assert back == r
    assert [d.error for d in back.docs] == ["", NO_TEXT_ERROR]
    assert back.docs[0].chunk_ids == r.docs[0].chunk_ids != []


def test_extract_report_tolerates_old_shape_and_garbage() -> None:
    rep = ExtractReport.from_dict({"skipped": [{"path": "/a.pdf", "reason": "why"}, "junk",
                                               {"reason": "no path"}]})
    assert rep.skipped == [("/a.pdf", "why")] and rep.inputs == []
    rep = ExtractReport.from_dict({"skipped": [{"path": "/a.pdf", "reason": "why", "error": "E"}],
                                   "inputs": ["/a.pdf", "/b.pdf"]})
    assert rep.skipped == [("/a.pdf", "E")] and rep.inputs == ["/a.pdf", "/b.pdf"]


# --------------------------------------------------------------------------- #
# the CLI: exit code follows the batch rule
# --------------------------------------------------------------------------- #
def _cli(shard: str, report: str, out: Path, emb: Path) -> list[str]:
    return [shard, "--extract-report", report, "--out", str(out), "--embedding-file", str(emb),
            "--shard-id", "batch-0", "--vector-backend", "memory", "--text-backend", "memory",
            "--chunk-method", "fixed", "--chunk-token-counter", "estimate"]


@pytest.fixture
def fake_embedder(monkeypatch):
    monkeypatch.setattr(ingest_shard, "_build_embedder", lambda args, http: _FakeEmbedder())


def test_cli_mixed_batch_exits_zero_with_per_document_receipt(tmp_path: Path, fake_embedder) -> None:
    shard, report = _batch(tmp_path, 18, 2)
    out, emb = tmp_path / "receipt.json", tmp_path / "batch.emb.jsonl"
    assert ingest_shard.main(_cli(shard, report, out, emb)) == 0
    r = ShardReceipt.load(out)
    assert r.status == COMPLETED and r.n_docs == 20 and r.n_docs_failed == 2
    assert sorted(d.error for d in r.docs if d.error) == [NO_TEXT_ERROR, NO_TEXT_ERROR]
    chunks, _ = read_embedding_file(emb)
    assert {c.doc_id for c in chunks} == {d.doc_id for d in r.docs if d.ok}


def test_cli_all_failed_batch_exits_nonzero(tmp_path: Path, fake_embedder, capsys) -> None:
    shard, report = _batch(tmp_path, 0, 2)
    out, emb = tmp_path / "receipt.json", tmp_path / "batch.emb.jsonl"
    assert ingest_shard.main(_cli(shard, report, out, emb)) == 1
    r = ShardReceipt.load(out)
    assert r.status == FAILED and [d.error for d in r.docs] == [NO_TEXT_ERROR] * 2
    assert not emb.exists()
    stdout = capsys.readouterr().out
    assert "failed=2" in stdout and NO_TEXT_ERROR in stdout


def test_cli_without_report_ingests_the_plain_shard(tmp_path: Path, fake_embedder) -> None:
    """The JSONL bulk plane is untouched: no report, no per-document skips."""
    shard, _ = _batch(tmp_path, 3, 0)
    out, emb = tmp_path / "receipt.json", tmp_path / "e.jsonl"
    argv = _cli(shard, "", out, emb)
    argv = [a for i, a in enumerate(argv) if a != "--extract-report" and argv[i - 1] != "--extract-report"]
    assert ingest_shard.main(argv) == 0
    r = ShardReceipt.load(out)
    assert r.status == COMPLETED and r.n_docs == 3 and r.n_docs_failed == 0
