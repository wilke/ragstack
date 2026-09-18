"""Offline tests for the ADR-0001 step-2 per-shard ingest tool.

`run_shard` is exercised end-to-end against in-memory stores + a fake embedder
(no GPU/Qdrant/ES), proving the receipt contract, idempotency, and failure
isolation. The receipt dataclass round-trip + the merge/gather summary are pure
and fully covered.
"""
from __future__ import annotations

import json
import sys
import threading
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
    # `fixed` is char-budgeted, so the token counter is irrelevant here — but a
    # counter is still built, and with no --embedding-model the default 'hf'
    # backend cannot be. This test used to get the estimator by accident, from
    # the silent no-model degrade; that degrade is now a refusal, so the choice
    # is stated. Naming it is the point: nothing else about the test changed.
    args = ingest_shard.parse_args(["x.jsonl", "--qdrant-url", "http://127.0.0.1:1", "--es-url", "http://127.0.0.1:1", "--chunk-method", "fixed",
                                    "--chunk-token-counter", "estimate",
                                    "--embedding-model", ""])
    assert ingest_shard._build_chunker(args) is not None  # no crash, no network


# --------------------------------------------------------------------------- #
# semantic chunking (#609) — the tool can do it now
# --------------------------------------------------------------------------- #


def _topic_embed_fn(texts):
    """Vectors keyed on cat-vs-dog content, so a real topic shift produces a large
    cosine distance and a breakpoint actually fires.

    A constant-vector fake — which is what `_FakeEmbedder` above is — makes every
    distance 0, so the 80th percentile is 0, nothing exceeds it, and the document
    comes back as ONE chunk. Every assertion below would still pass while
    breakpoint detection never ran. Same shape as
    `tests/unit/test_chunkers_semantic.py::_topic_embed_fn`.
    """
    out = []
    for t in texts:
        tl = t.lower()
        out.append([float(tl.count("cat")) + 0.01, float(tl.count("dog")) + 0.01])
    return out


def _args_for(method: str, **over):
    argv = ["x.jsonl", "--qdrant-url", "http://127.0.0.1:1",
            "--es-url", "http://127.0.0.1:1", "--chunk-method", method]
    for k, v in over.items():
        argv += [f"--{k.replace('_', '-')}", str(v)]
    return ingest_shard.parse_args(argv)


class _Spec:
    """Just the attribute `_semantic_params` reads off a CollectionSpec."""

    def __init__(self, chunk_params):
        self.chunk_params = chunk_params


@pytest.mark.parametrize("method", ["semantic", "semantic_pooled"])
def test_build_chunker_accepts_semantic_with_a_bridge(method: str) -> None:
    """The refusal is gone: given an embed_fn, both semantic methods build."""
    chunker = ingest_shard._build_chunker(_args_for(method), None, embed_fn=_topic_embed_fn)
    assert type(chunker).__name__ == "SemanticChunker"
    assert chunker.pool_sentences is (method == "semantic_pooled")


@pytest.mark.parametrize("method", ["semantic", "semantic_pooled"])
def test_semantic_without_a_bridge_refuses_rather_than_demoting(method: str) -> None:
    """Never a silent fall back to a different chunker — that would re-chunk the
    corpus under a method nobody selected and no store records."""
    with pytest.raises(ValueError, match="requires an embed_fn"):
        ingest_shard._build_chunker(_args_for(method), None, embed_fn=None)


def test_semantic_chunker_actually_splits_on_a_topic_shift() -> None:
    """The fake must be topic-sensitive or this test is vacuous — see above."""
    from ragstack.ingestion.loaders import Document

    text = ("Cats are wonderful. Cats purr softly. Cats love to nap. "
            "Dogs are loyal companions. Dogs bark loudly. Dogs fetch balls.")
    # buffer_size=1 matters: at the default 3, every buffer over a 6-sentence
    # document spans the WHOLE document, so all buffer vectors are identical, all
    # distances are 0 and no breakpoint can fire. That is not a bug in the fake —
    # it is the same "the test passes without exercising anything" failure the
    # docstring above warns about, reached from the other direction.
    chunker = ingest_shard._build_chunker(
        _args_for("semantic_pooled"),
        _Spec({"buffer_size": 1, "min_chunk_length": 10}),
        embed_fn=_topic_embed_fn,
    )
    chunks = chunker.chunk(Document(id="d1", content=text, metadata={}, source="t"))
    assert len(chunks) > 1, "no breakpoint fired — the embed fake is not topic-sensitive"
    # Lossless: the spans still tile the source exactly.
    assert "".join(text[c.start_char:c.end_char] for c in chunks) == text


def test_chunk_params_come_from_the_registry_entry() -> None:
    """`_gowe_inputs` never sends chunk_params, so the entry is the only source.

    A collection created with buffer_size=5 must be ingested with 5; falling back
    to the tool's default silently chunks it differently than it was specified.
    """
    chunker = ingest_shard._build_chunker(
        _args_for("semantic_pooled"),
        _Spec({"buffer_size": 5, "breakpoint_percentile_threshold": 66.0,
               "min_chunk_length": 42}),
        embed_fn=_topic_embed_fn,
    )
    assert chunker.buffer_size == 5
    assert chunker.breakpoint_percentile_threshold == 66.0
    assert chunker.min_chunk_length == 42

    # Control: with no params the tool's defaults stand, so the assertion above
    # is about the entry and not about whatever the defaults happen to be.
    default = ingest_shard._build_chunker(
        _args_for("semantic_pooled"), _Spec({}), embed_fn=_topic_embed_fn)
    assert (default.buffer_size, default.breakpoint_percentile_threshold,
            default.min_chunk_length) == (3, 80.0, 500)


def test_bad_chunk_param_fails_at_config_not_mid_shard() -> None:
    with pytest.raises(SystemExit, match="buffer_size"):
        ingest_shard._build_chunker(
            _args_for("semantic_pooled"), _Spec({"buffer_size": "lots"}),
            embed_fn=_topic_embed_fn)


class _TopicEmbedder:
    """Async embedder with topic-sensitive vectors, for the bridge to drive."""

    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return _topic_embed_fn(texts)


def _semantic_argv(shard: str, out: str, method: str = "semantic_pooled") -> list[str]:
    return [shard, "--out", out, "--chunk-method", method,
            "--vector-backend", "memory", "--text-backend", "memory",
            "--qdrant-url", "http://127.0.0.1:1", "--es-url", "http://127.0.0.1:1",
            "--embedding-model", "test/model", "--chunk-token-counter", "estimate"]


def test_amain_runs_a_semantic_shard_end_to_end(tmp_path, monkeypatch) -> None:
    """Drives `amain` with a REAL SyncEmbedBridge — only the embedder is faked.

    This is the test whose absence let a crash ship: `_build_bridge` passed
    `args.batch_size`, an attribute this tool's parser does not define (it was
    copied from ingest_jsonl.py, which does). Every semantic shard would have died
    with AttributeError before any I/O — no receipt written, a traceback instead
    of a refusal, and the engine retrying it three times.

    The suite was green because the only test touching the bridge builder
    monkeypatched it away, and nothing ran `amain` with a semantic method. So the
    rule here is: stub the EMBEDDER, never the bridge. The bridge's own
    construction, background loop, fan-out and close are what this exercises.
    """
    import asyncio

    text = ("Cats are wonderful. Cats purr softly. Cats love to nap. "
            "Dogs are loyal companions. Dogs bark loudly. Dogs fetch balls.")
    shard = tmp_path / "s.jsonl"
    shard.write_text(json.dumps({"text": text, "path": "/corpus/a.txt"}), encoding="utf-8")
    out = str(tmp_path / "r.json")

    embedder = _TopicEmbedder()
    monkeypatch.setattr(ingest_shard, "_build_embedder", lambda args, http: embedder)
    args = ingest_shard.parse_args(_semantic_argv(str(shard), out))
    # buffer_size=1: at the default 3 every buffer spans this whole document, all
    # distances are 0 and no breakpoint can fire — see the note on the split test.
    monkeypatch.setattr(ingest_shard, "_semantic_params",
                        lambda spec: {"buffer_size": 1, "min_chunk_length": 10})

    rc = asyncio.run(ingest_shard.amain(args))

    assert rc == 0
    receipt = json.loads(Path(out).read_text())
    assert receipt["status"] == COMPLETED
    assert receipt["n_chunks"] > 1, "the semantic chunker did not split"
    assert embedder.calls >= 2, "breakpoint embed never happened"
    # The bridge's background thread is gone: the `finally` ran and close() worked.
    assert [t for t in threading.enumerate() if "embed" in t.name.lower()] == []


def test_amain_closes_the_bridge_when_the_shard_fails(tmp_path, monkeypatch) -> None:
    """A failing shard must still leave no thread behind — and still write its
    receipt, because that is what the engine classifies on."""
    import asyncio

    class _Boom:
        async def embed(self, texts):
            raise RuntimeError("fleet down")

    shard = tmp_path / "s.jsonl"
    shard.write_text(json.dumps({"text": "Cats nap. Dogs bark.", "path": "/c/a.txt"}),
                     encoding="utf-8")
    out = str(tmp_path / "r.json")
    monkeypatch.setattr(ingest_shard, "_build_embedder", lambda args, http: _Boom())
    args = ingest_shard.parse_args(_semantic_argv(str(shard), out))

    rc = asyncio.run(ingest_shard.amain(args))

    assert rc == 1
    assert json.loads(Path(out).read_text())["status"] == FAILED
    assert [t for t in threading.enumerate() if "embed" in t.name.lower()] == []


def test_amain_survives_a_close_that_raises(tmp_path, monkeypatch) -> None:
    """close() raises after thread.join times out against a wedged endpoint.

    Letting that escape would turn a correctly written receipt into a traceback
    and lose the exit code the engine classifies on.
    """
    import asyncio

    from ragstack.ingestion.embed_bridge import SyncEmbedBridge

    shard = tmp_path / "s.jsonl"
    shard.write_text(json.dumps({"text": "Cats nap. Dogs bark.", "path": "/c/a.txt"}),
                     encoding="utf-8")
    out = str(tmp_path / "r.json")
    monkeypatch.setattr(ingest_shard, "_build_embedder", lambda args, http: _TopicEmbedder())
    real_close = SyncEmbedBridge.close

    def _angry(self):
        real_close(self)
        raise RuntimeError("event loop is running")

    monkeypatch.setattr(SyncEmbedBridge, "close", _angry)
    args = ingest_shard.parse_args(_semantic_argv(str(shard), out))

    rc = asyncio.run(ingest_shard.amain(args))

    assert rc == 0
    assert json.loads(Path(out).read_text())["status"] == COMPLETED


def test_non_semantic_shard_builds_no_bridge(tmp_path, monkeypatch) -> None:
    """A fixed_token shard must not spin a background loop + thread it never uses.

    Asserted through `amain`, not by stubbing `_build_bridge` and checking a list
    that could not have been appended to — which is what the first version of this
    test did, and why it proved nothing.
    """
    import asyncio

    def _explode(args):
        raise AssertionError("built a breakpoint bridge for a non-semantic method")

    monkeypatch.setattr(ingest_shard, "_build_bridge", _explode)
    monkeypatch.setattr(ingest_shard, "_build_embedder", lambda args, http: _FakeEmbedder())
    shard = _shard(tmp_path, "s.jsonl", n=2)
    out = str(tmp_path / "r.json")
    args = ingest_shard.parse_args(
        _semantic_argv(shard, out, method="fixed") )

    rc = asyncio.run(ingest_shard.amain(args))

    assert rc == 0
    assert json.loads(Path(out).read_text())["status"] == COMPLETED


def test_non_semantic_method_ignores_junk_chunk_params() -> None:
    """`_validate_chunk` range-checks params only for the semantic methods, but
    create_collection persists them for any method — so a `fixed` collection can
    carry junk. Reading it unconditionally refused a shard on the path every
    current collection actually uses."""
    chunker = ingest_shard._build_chunker(
        _args_for("fixed"), _Spec({"buffer_size": "lots"}))
    assert type(chunker).__name__ == "RecursiveCharacterChunker"


@pytest.mark.asyncio
async def test_build_pipeline_rejects_mixed_backends() -> None:
    # split-brain guard fires before any I/O, so http can be None
    args = ingest_shard.parse_args(["x.jsonl", "--qdrant-url", "http://127.0.0.1:1", "--es-url", "http://127.0.0.1:1", "--vector-backend", "memory",
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
