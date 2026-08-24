"""Offline tests for the ADR-0001 step-2 per-shard ingest tool.

`run_shard` is exercised end-to-end against in-memory stores + a fake embedder
(no GPU/Qdrant/ES), proving the receipt contract, idempotency, and failure
isolation. The receipt dataclass round-trip + the merge/gather summary are pure
and fully covered.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from ragstack.ingestion.chunkers import RecursiveCharacterChunker
from ragstack.ingestion.loaders import JsonlLoader
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.ingestion.receipts import COMPLETED, FAILED, DocRow, ShardReceipt, merge_summary
from ragstack.ingestion.shard import run_shard
from ragstack.stores.memory import InMemoryTextIndex, InMemoryVectorStore

# merge_receipts + ingest_shard CLIs live under python/scripts.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import ingest_shard  # noqa: E402
import merge_receipts  # noqa: E402


class _FakeEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


def _pipeline() -> tuple[IngestionPipeline, InMemoryVectorStore]:
    vstore = InMemoryVectorStore()
    pipe = IngestionPipeline(
        loader=JsonlLoader(),
        chunker=RecursiveCharacterChunker(),
        embedder=_FakeEmbedder(),
        vector_store=vstore,
        text_index=InMemoryTextIndex(),
    )
    return pipe, vstore


def _shard(tmp_path: Path, name: str, n: int = 3) -> str:
    p = tmp_path / name
    lines = [
        json.dumps({"text": f"Document {i} about reciprocal rank fusion and hybrid "
                            f"retrieval in scientific corpora. Passage body {i}.",
                    "path": f"/corpus/doc{i}.txt"})
        for i in range(n)
    ]
    p.write_text("\n".join(lines), encoding="utf-8")
    return str(p)


# --------------------------------------------------------------------------- #
# run_shard
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_run_shard_completed_receipt(tmp_path: Path) -> None:
    pipe, vstore = _pipeline()
    receipt = await run_shard(pipe, _shard(tmp_path, "s0.jsonl", 3), "public", "s0")
    assert receipt.status == COMPLETED
    assert receipt.shard_id == "s0" and receipt.tenant == "public"
    assert receipt.n_docs == 3
    assert receipt.n_chunks == len(receipt.chunk_ids) >= 3
    assert len(receipt.docs) == 3 and all(isinstance(d, DocRow) for d in receipt.docs)
    assert all(d.doc_id for d in receipt.docs)
    # the ingest actually wrote to the store
    assert await vstore.count_tenants(["public"]) == receipt.n_chunks


@pytest.mark.asyncio
async def test_run_shard_idempotent(tmp_path: Path) -> None:
    pipe, vstore = _pipeline()
    shard = _shard(tmp_path, "s0.jsonl", 3)
    r1 = await run_shard(pipe, shard, "public", "s0")
    r2 = await run_shard(pipe, shard, "public", "s0")  # re-run (a GoWe retry)
    assert r1.chunk_ids == r2.chunk_ids                # deterministic ids
    assert await vstore.count_tenants(["public"]) == r1.n_chunks  # no duplication


@pytest.mark.asyncio
async def test_run_shard_embedding_file_matches_upsert(tmp_path: Path) -> None:
    """``embedding_file`` (#357): the chunks written to the file are exactly the
    chunks upserted (same ids, same vectors), the receipt names the file, and a
    failed shard leaves no file behind."""
    from ragstack.ingestion.embedding_file import read_embedding_file

    pipe, vstore = _pipeline()
    emb = tmp_path / "s0.emb.jsonl"
    r = await run_shard(pipe, _shard(tmp_path, "s0.jsonl"), "public", "s0", embedding_file=emb)
    assert r.status == COMPLETED
    assert r.embedding_file == str(emb)
    chunks, header = read_embedding_file(emb)
    assert header["tenant"] == "public" and header["dim"] == 4
    assert [c.id for c in chunks] == r.chunk_ids
    stored = {c.id: c.embedding for c in vstore._chunks}
    assert {c.id: c.embedding for c in chunks} == stored

    missing = tmp_path / "missing.emb.jsonl"
    r2 = await run_shard(pipe, str(tmp_path / "nope.jsonl"), "public", "nope",
                         embedding_file=missing)
    assert r2.status == FAILED and not missing.exists()

    # The file is written BEFORE the upsert; a failed upsert must take it with it,
    # or a retry would find a stale file next to a "failed" receipt.
    class _BoomStore(InMemoryVectorStore):
        async def upsert(self, chunks):
            raise RuntimeError("qdrant down")

    boom = IngestionPipeline(loader=JsonlLoader(), chunker=RecursiveCharacterChunker(),
                             embedder=_FakeEmbedder(), vector_store=_BoomStore(),
                             text_index=InMemoryTextIndex())
    r3 = await run_shard(boom, _shard(tmp_path, "s1.jsonl"), "public", "s1",
                         embedding_file=missing)
    assert r3.status == FAILED and "qdrant down" in r3.error
    assert not missing.exists()


async def test_run_shard_missing_file_is_failed_not_raised(tmp_path: Path) -> None:
    pipe, _ = _pipeline()
    receipt = await run_shard(pipe, str(tmp_path / "nope.jsonl"), "public", "sX")
    assert receipt.status == FAILED and receipt.error.startswith("load:")
    assert receipt.n_chunks == 0


@pytest.mark.asyncio
async def test_run_shard_empty_shard_is_failed(tmp_path: Path) -> None:
    pipe, _ = _pipeline()
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    receipt = await run_shard(pipe, str(empty), "public", "sE")
    assert receipt.status == FAILED  # no usable docs → LoaderError → captured


# --------------------------------------------------------------------------- #
# receipt round-trip + merge/gather
# --------------------------------------------------------------------------- #
def test_receipt_json_round_trip() -> None:
    r = ShardReceipt("s0", "public", COMPLETED, n_docs=2, n_chunks=5,
                     chunk_ids=["a", "b"], docs=[DocRow("d1", "/x", {"title": "T"})])
    back = ShardReceipt.from_dict(json.loads(r.to_json()))
    assert back == r
    assert isinstance(back.docs[0], DocRow)


def test_merge_summary_surfaces_failed_shards() -> None:
    receipts = [
        ShardReceipt("s0", "public", COMPLETED, n_docs=3, n_chunks=9),
        ShardReceipt("s1", "public", COMPLETED, n_docs=2, n_chunks=6),
        ShardReceipt("s2", "public", FAILED, n_docs=2, n_chunks=0, error="boom"),
    ]
    s = merge_summary(receipts)
    assert s["n_shards"] == 3 and s["n_shards_failed"] == 1
    assert s["n_docs"] == 7 and s["n_chunks"] == 15
    assert s["failed_shards"] == ["s2"] and s["errors"] == {"s2": "boom"}


def test_receipt_load_malformed_is_clean_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"tenant": "public"}', encoding="utf-8")  # no shard_id/status
    with pytest.raises(ValueError, match="invalid receipt"):
        ShardReceipt.load(bad)


# --------------------------------------------------------------------------- #
# CLI wiring (offline): the _build_pipeline path the unit test above bypassed —
# where the #133 fixed_token blocker lived.
# --------------------------------------------------------------------------- #
def test_build_chunker_fixed_offline() -> None:
    args = ingest_shard.parse_args(["x.jsonl", "--chunk-method", "fixed",
                                    "--embedding-model", ""])
    assert ingest_shard._build_chunker(args) is not None  # no crash, no network


def test_build_chunker_rejects_semantic() -> None:
    args = ingest_shard.parse_args(["x.jsonl", "--chunk-method", "semantic_pooled"])
    with pytest.raises(SystemExit, match="not yet wired"):
        ingest_shard._build_chunker(args)


@pytest.mark.asyncio
async def test_build_pipeline_rejects_mixed_backends() -> None:
    # split-brain guard fires before any I/O, so http can be None
    args = ingest_shard.parse_args(["x.jsonl", "--vector-backend", "memory",
                                    "--text-backend", "elasticsearch"])
    with pytest.raises(SystemExit, match="consistent"):
        await ingest_shard._build_pipeline(args, None)


def test_merge_receipts_cli(tmp_path: Path) -> None:
    files = []
    for i, status in enumerate([COMPLETED, FAILED]):
        p = tmp_path / f"r{i}.json"
        ShardReceipt(f"s{i}", "public", status, n_docs=2,
                     n_chunks=4 if status == COMPLETED else 0).write(p)
        files.append(str(p))
    out = tmp_path / "summary.json"
    # non-gating: returns 0 even with a failed shard
    assert merge_receipts.main([*files, "--out", str(out)]) == 0
    summary = json.loads(out.read_text())
    assert summary["n_shards"] == 2 and summary["failed_shards"] == ["s1"]
    # gating: returns 1 when a shard failed
    assert merge_receipts.main([*files, "--out", str(out), "--fail-on-shard-error"]) == 1
