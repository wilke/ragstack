"""The parallel JSONL ingester replaces a document's chunks on re-ingest.

Drives the real concurrent `run()` against in-memory fakes and verifies that
re-ingesting an *edited* document (shifted chunk offsets → new chunk ids) leaves
no orphan chunks — the concurrency-safe delete-before-upsert. The script lives in
scripts/ (not the package), so load it by path.
"""
import argparse
import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ingest_jsonl.py"
_spec = importlib.util.spec_from_file_location("ingest_jsonl", _SCRIPT)
ingest_jsonl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ingest_jsonl)


class _FakeStore:
    """In-memory vector store: points keyed by chunk id; delete is by doc id."""

    def __init__(self) -> None:
        self.points: dict[str, object] = {}
        self.ops: list[str] = []  # ordered "delete:<doc>" / "upsert:<n>" trace

    async def ensure_collection(self) -> None:
        pass

    async def upsert(self, chunks) -> None:
        self.ops.append(f"upsert:{len(chunks)}")
        for c in chunks:
            self.points[c.id] = c

    async def delete(self, doc_id, tenant_id=None) -> None:
        self.ops.append(f"delete:{doc_id}")
        self.points = {
            cid: c for cid, c in self.points.items() if c.doc_id != doc_id
        }

    async def delete_except(self, doc_id, keep_chunk_ids, tenant_id=None) -> None:
        # Prune only this doc's points whose chunk id is NOT being kept (orphans).
        self.ops.append(f"delete_except:{doc_id}:{len(keep_chunk_ids)}")
        self.points = {
            cid: c
            for cid, c in self.points.items()
            if c.doc_id != doc_id or cid in keep_chunk_ids
        }


class _FakeEmbedder:
    async def embed(self, texts):
        # Fail the batch carrying the poison marker; succeed for everything else.
        if any("POISONPILL" in t for t in texts):
            raise RuntimeError("embed boom")
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


def _args(input_path: Path, **over) -> argparse.Namespace:
    base = {
        "input": input_path, "tenant": "public", "doc_types": None, "limit": 0,
        "publisher_profile": "asm",
        "catalog_out": None, "doc_metrics_out": None, "run_metrics_out": None,
        "no_index": False, "checkpoint": None, "resume": False,
        "chunk_size": 200, "chunk_overlap": 20, "batch_size": 2, "concurrency": 2,
        "chunk_method": "fixed", "chunk_buffer_size": 3,
        "chunk_breakpoint_percentile": 80.0, "chunk_min_length": 500,
        "semantic_max_sentences": 3000,
        # Token sizing: 'estimate' is zero-dep (no tokenizer/endpoint) and an
        # explicit budget skips the max_model_len probe, keeping the test offline.
        "chunk_token_counter": "estimate", "chunk_max_tokens": 4096,
        "embedding_url": ["http://x"], "embedding_api": "openai", "embedding_model": "m",
        "embedding_api_key": None,
        "embedding_max_concurrency": 4, "collection": "test", "qdrant_url": "http://q",
        "text_backend": "memory", "es_url": "http://es", "es_index": "i",
        "qdrant_timeout": 120.0, "replace": False, "delete_concurrency": 4,
        "batch_retries": 0,
        # Semantic breakpoint / segmentation-cache / concurrency knobs (match main()'s
        # argparse defaults so production code uses plain attribute access).
        "breakpoint_embedding_api": None, "breakpoint_embedding_url": None,
        "breakpoint_embedding_model": None, "breakpoint_embedding_max_concurrency": None,
        "breakpoint_embedding_api_key": None, "breakpoint_max_tokens": None,
        "segmentation_cache": None, "chunk_concurrency": 1,
    }
    base.update(over)
    return argparse.Namespace(**base)


def _write_corpus(path: Path, text: str) -> None:
    rec = {"text": text, "path": "/corpus/jvi.00155-22.pdf", "metadata": {"title": "T"}}
    path.write_text(json.dumps(rec) + "\n", encoding="utf-8")


def _article(i: int, *, poison: bool = False) -> dict:
    body = f"Article body number {i}. " * 100  # long enough to classify as ARTICLE
    if poison:
        body += " POISONPILL "
    return {"text": body, "path": f"/corpus/jvi.{i:05d}-22.pdf", "metadata": {"title": f"T{i}"}}


def _front_matter(i: int) -> dict:
    body = f"Masthead front matter {i}. " * 100
    return {"text": body, "path": f"/corpus/masthead{i}.pdf", "metadata": {}}


def _write_records(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_reingest_edited_doc_leaves_no_orphans(tmp_path, monkeypatch):
    store = _FakeStore()
    monkeypatch.setattr(ingest_jsonl, "QdrantVectorStore", lambda **kw: store)
    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: _FakeEmbedder())
    monkeypatch.setattr(ingest_jsonl, "collection_name", lambda *a, **kw: "test")

    corpus = tmp_path / "c.jsonl"

    # First ingest: a LONG body → many chunks (trailing offsets that a shorter
    # re-ingest won't cover — exactly where orphans would survive).
    _write_corpus(corpus, "alpha beta gamma delta epsilon zeta. " * 160)
    await ingest_jsonl.run(_args(corpus, checkpoint=tmp_path / "a.ckpt"))
    assert store.points, "first ingest produced no chunks"

    # Re-ingest the SAME path with a much SHORTER edited body under --replace →
    # fewer chunks. The orphan trailing chunks must be pruned, leaving exactly the
    # second ingest's chunks.
    store.ops.clear()
    _write_corpus(corpus, "completely different shorter wording now. " * 20)
    await ingest_jsonl.run(_args(corpus, checkpoint=tmp_path / "b.ckpt", replace=True))

    second_upserted = sum(
        int(op.split(":")[1]) for op in store.ops if op.startswith("upsert:")
    )
    # Store holds exactly the second ingest's chunks — orphans pruned.
    assert len(store.points) == second_upserted
    # Safety: upsert precedes the prune (upsert-then-prune), so a prune failure
    # can never lose the freshly written data.
    first_upsert = next(i for i, op in enumerate(store.ops) if op.startswith("upsert:"))
    first_prune = next(i for i, op in enumerate(store.ops) if op.startswith("delete_except:"))
    assert first_upsert < first_prune


@pytest.mark.asyncio
async def test_default_reingest_is_upsert_only_and_idempotent(tmp_path, monkeypatch):
    """Without --replace the worker NEVER deletes (so a failure can't lose data),
    and re-ingesting identical content is idempotent (deterministic ids overwrite)."""
    store = _FakeStore()
    monkeypatch.setattr(ingest_jsonl, "QdrantVectorStore", lambda **kw: store)
    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: _FakeEmbedder())
    monkeypatch.setattr(ingest_jsonl, "collection_name", lambda *a, **kw: "test")

    corpus = tmp_path / "c.jsonl"
    _write_corpus(corpus, "alpha beta gamma delta epsilon zeta. " * 160)
    await ingest_jsonl.run(_args(corpus, checkpoint=tmp_path / "a.ckpt"))
    n = len(store.points)
    assert n > 0

    store.ops.clear()
    await ingest_jsonl.run(_args(corpus, checkpoint=tmp_path / "b.ckpt"))
    # No delete of any kind on the default path; identical content → same count.
    assert not any(op.startswith(("delete:", "delete_except:")) for op in store.ops)
    assert len(store.points) == n


@pytest.mark.asyncio
async def test_catalog_resume_advances_over_skipped_tail(tmp_path):
    # Records end with a run of filtered-out (front-matter) docs. The checkpoint
    # must advance to the last line, not stall at the last *kept* doc — else a
    # resume re-scans the skipped tail forever.
    corpus = tmp_path / "c.jsonl"
    _write_records(corpus, [_article(1), _article(2), _front_matter(3), _front_matter(4)])
    ckpt = tmp_path / "c.ckpt"
    await ingest_jsonl.run(_args(
        corpus, no_index=True, doc_types=["article"],
        catalog_out=tmp_path / "cat.jsonl", checkpoint=ckpt,
    ))
    assert ingest_jsonl._read_checkpoint(ckpt)["line"] == 4  # past the skipped tail


@pytest.mark.asyncio
async def test_failed_batch_exits_nonzero_and_stalls_checkpoint(tmp_path, monkeypatch):
    store = _FakeStore()
    monkeypatch.setattr(ingest_jsonl, "QdrantVectorStore", lambda **kw: store)
    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: _FakeEmbedder())
    monkeypatch.setattr(ingest_jsonl, "collection_name", lambda *a, **kw: "test")

    corpus = tmp_path / "c.jsonl"
    # batch_size=1 → one batch per doc. Doc 3 poisons its embed; docs 1-2 succeed
    # (checkpoint reaches line 2), doc 3's gap stalls the checkpoint there.
    _write_records(corpus, [_article(1), _article(2), _article(3, poison=True),
                            _article(4), _article(5)])
    ckpt = tmp_path / "c.ckpt"
    with pytest.raises(SystemExit):
        await ingest_jsonl.run(_args(corpus, batch_size=1, concurrency=2, checkpoint=ckpt))

    # Checkpoint stalled at the contiguous completed prefix (before the failed
    # batch), not at the last line.
    assert ingest_jsonl._read_checkpoint(ckpt)["line"] == 2


@pytest.mark.asyncio
async def test_catalog_lockstep_writes_no_rows_past_gap(tmp_path, monkeypatch):
    store = _FakeStore()
    monkeypatch.setattr(ingest_jsonl, "QdrantVectorStore", lambda **kw: store)
    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: _FakeEmbedder())
    monkeypatch.setattr(ingest_jsonl, "collection_name", lambda *a, **kw: "test")

    corpus = tmp_path / "c.jsonl"
    _write_records(corpus, [_article(1), _article(2), _article(3, poison=True),
                            _article(4), _article(5)])
    cat = tmp_path / "cat.jsonl"
    with pytest.raises(SystemExit):
        await ingest_jsonl.run(_args(
            corpus, batch_size=1, concurrency=2, catalog_out=cat,
            checkpoint=tmp_path / "c.ckpt",
        ))

    # Catalog holds only the completed prefix before the gap (docs 1-2); rows for
    # docs 3-5 stay buffered (unwritten) so the catalog never outruns the
    # checkpoint — they'd be written on a --resume past the gap.
    titles = [json.loads(line)["title"] for line in cat.read_text().splitlines()]
    assert titles == ["T1", "T2"]


@pytest.mark.asyncio
async def test_prune_failure_after_upsert_preserves_data(tmp_path, monkeypatch):
    # The whole point of upsert-then-prune (#31): if the orphan prune fails (e.g.
    # a delete timeout), the just-upserted chunks must survive — only orphans/
    # duplicates linger, never data loss — and the run exits non-zero so it's seen.
    class _PruneFailsStore(_FakeStore):
        async def delete_except(self, doc_id, keep_chunk_ids, tenant_id=None):
            raise RuntimeError("prune timed out")

    store = _PruneFailsStore()
    monkeypatch.setattr(ingest_jsonl, "QdrantVectorStore", lambda **kw: store)
    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: _FakeEmbedder())
    monkeypatch.setattr(ingest_jsonl, "collection_name", lambda *a, **kw: "test")

    corpus = tmp_path / "c.jsonl"
    _write_records(corpus, [_article(1), _article(2)])
    with pytest.raises(SystemExit):  # failed batches → non-zero exit
        await ingest_jsonl.run(_args(
            corpus, batch_size=1, concurrency=1, replace=True,
            checkpoint=tmp_path / "c.ckpt",
        ))

    # Upsert ran before the failing prune, so the chunks are still present.
    assert store.points, "a prune failure must not lose the upserted chunks"
    assert any(op.startswith("upsert:") for op in store.ops)


@pytest.mark.asyncio
async def test_chunk_method_routes_through_build_chunker(tmp_path, monkeypatch):
    """--chunk-method selects the chunker via build_chunker; semantic gets a
    (non-None) embed_fn bridge, fixed does not. build_chunker returns
    (chunker, token_counter, max_tokens)."""
    from ragstack.ingestion.chunkers import RecursiveCharacterChunker

    calls: dict = {}

    def fake_build_chunker(method, **kw):
        calls["method"] = method
        calls["embed_fn"] = kw.get("embed_fn")
        chunker = RecursiveCharacterChunker(
            chunk_size=kw.get("chunk_size", 512), chunk_overlap=kw.get("chunk_overlap", 64)
        )
        return chunker, None, 4096

    monkeypatch.setattr(ingest_jsonl, "build_chunker", fake_build_chunker)
    monkeypatch.setattr(ingest_jsonl, "QdrantVectorStore", lambda **kw: _FakeStore())
    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: _FakeEmbedder())
    monkeypatch.setattr(ingest_jsonl, "collection_name", lambda *a, **kw: "test")

    corpus = tmp_path / "c.jsonl"
    _write_corpus(corpus, "alpha beta gamma. " * 50)

    await ingest_jsonl.run(_args(corpus, chunk_method="semantic", checkpoint=tmp_path / "s.ckpt"))
    assert calls["method"] == "semantic"
    assert calls["embed_fn"] is not None  # SyncEmbedBridge handed to the chunker

    await ingest_jsonl.run(_args(corpus, chunk_method="fixed", checkpoint=tmp_path / "f.ckpt"))
    assert calls["method"] == "fixed"
    assert calls["embed_fn"] is None

    # semantic_pooled also gets a bridge (embed_fn), routed via make_chunker.
    await ingest_jsonl.run(_args(corpus, chunk_method="semantic_pooled",
                                 checkpoint=tmp_path / "sp.ckpt"))
    assert calls["method"] == "semantic_pooled"
    assert calls["embed_fn"] is not None


def test_build_breakpoint_embedder_falls_back_and_overrides(monkeypatch):
    """The breakpoint embedder reuses the main --embedding-* backend by default, and
    switches to --breakpoint-embedding-* when set (so boundary detection can run on
    a separate cheap model while stored chunks stay on the main model)."""
    seen: dict = {}
    monkeypatch.setattr(ingest_jsonl, "make_embedder",
                        lambda **kw: seen.setdefault("single", kw))
    monkeypatch.setattr(ingest_jsonl, "make_pooled_embedder",
                        lambda **kw: seen.setdefault("pooled", kw))

    # No --breakpoint-embedding-url → falls back to the main single endpoint.
    seen.clear()
    ingest_jsonl._build_breakpoint_embedder(
        _args(Path("x"), embedding_url=["http://main:1"], embedding_model="MAIN"), http=None)
    assert seen["single"]["base_url"] == "http://main:1" and seen["single"]["model"] == "MAIN"

    # Override with a separate breakpoint endpoint + model (single).
    seen.clear()
    ingest_jsonl._build_breakpoint_embedder(
        _args(Path("x"), embedding_url=["http://main:1"], embedding_model="MAIN",
              breakpoint_embedding_url=["http://bge:9101"],
              breakpoint_embedding_model="BAAI/bge-base-en-v1.5",
              breakpoint_embedding_api="openai"), http=None)
    assert seen["single"]["base_url"] == "http://bge:9101"
    assert seen["single"]["model"] == "BAAI/bge-base-en-v1.5"

    # Multiple breakpoint URLs → pooled fan-out.
    seen.clear()
    ingest_jsonl._build_breakpoint_embedder(
        _args(Path("x"), embedding_url=["http://main:1"], embedding_model="MAIN",
              breakpoint_embedding_url=["http://bge:9101", "http://bge:9102"]), http=None)
    assert seen["pooled"]["base_urls"] == ["http://bge:9101", "http://bge:9102"]


class _RecordingEmbedder:
    """Records every text it embeds, so a test can assert which docs were (not)
    re-embedded. Fails the batch carrying the poison marker, like _FakeEmbedder."""

    def __init__(self) -> None:
        self.embedded: list[str] = []

    async def embed(self, texts):
        if any("POISONPILL" in t for t in texts):
            raise RuntimeError("embed boom")
        self.embedded.extend(texts)
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


@pytest.mark.asyncio
async def test_failed_early_batch_records_done_ranges_above_gap(tmp_path, monkeypatch):
    # #65: an early failed batch stalls the frontier, but the LATER batches that
    # completed out of order must be durably recorded so a resume doesn't redo them.
    store = _FakeStore()
    monkeypatch.setattr(ingest_jsonl, "QdrantVectorStore", lambda **kw: store)
    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: _FakeEmbedder())
    monkeypatch.setattr(ingest_jsonl, "collection_name", lambda *a, **kw: "test")

    corpus = tmp_path / "c.jsonl"
    _write_records(corpus, [_article(1), _article(2), _article(3, poison=True),
                            _article(4), _article(5)])
    ckpt = tmp_path / "c.ckpt"
    with pytest.raises(SystemExit):
        await ingest_jsonl.run(_args(corpus, batch_size=1, concurrency=2, checkpoint=ckpt))

    ck = ingest_jsonl._read_checkpoint(ckpt)
    # Frontier still pinned before the failed batch (no-data-loss invariant)...
    assert ck["line"] == 2
    # ...and docs 4,5 (completed above the gap) are recorded so a resume skips them.
    assert ck["done_ranges"] == [[4, 5]]


@pytest.mark.asyncio
async def test_resume_skips_done_ranges_embed_but_writes_catalog_once(tmp_path, monkeypatch):
    store = _FakeStore()
    monkeypatch.setattr(ingest_jsonl, "QdrantVectorStore", lambda **kw: store)
    monkeypatch.setattr(ingest_jsonl, "collection_name", lambda *a, **kw: "test")
    cat = tmp_path / "cat.jsonl"
    ckpt = tmp_path / "c.ckpt"

    # Run 1: doc 3 poisons; docs 4,5 complete above the gap → done_ranges=[[4,5]],
    # catalog holds only T1,T2 (lockstep can't write past the gap).
    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: _FakeEmbedder())
    corpus = tmp_path / "c.jsonl"
    records = [_article(1), _article(2), _article(3, poison=True), _article(4), _article(5)]
    _write_records(corpus, records)
    with pytest.raises(SystemExit):
        await ingest_jsonl.run(_args(
            corpus, batch_size=1, concurrency=2, catalog_out=cat, checkpoint=ckpt))
    assert [json.loads(x)["title"] for x in cat.read_text().splitlines()] == ["T1", "T2"]

    # Run 2 (resume): fix doc 3 (others byte-identical). Docs 4,5 must NOT be
    # re-embedded (done_ranges skip), but their catalog rows must still be written
    # exactly once so the catalog is complete.
    rec = _RecordingEmbedder()
    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: rec)
    records[2] = _article(3)  # de-poison line 3, everything else unchanged
    _write_records(corpus, records)
    await ingest_jsonl.run(_args(
        corpus, batch_size=1, concurrency=2, catalog_out=cat, checkpoint=ckpt, resume=True))

    # doc 3 reprocessed; docs 4,5 skipped (not re-embedded).
    joined = " ".join(rec.embedded)
    assert "number 3" in joined
    assert "number 4" not in joined and "number 5" not in joined
    # Catalog now complete, every title exactly once (T1,T2 from run 1; T3,T4,T5 run 2).
    assert [json.loads(x)["title"] for x in cat.read_text().splitlines()] == \
        ["T1", "T2", "T3", "T4", "T5"]
    # Checkpoint fully advanced, done_ranges cleared.
    ck = ingest_jsonl._read_checkpoint(ckpt)
    assert ck["line"] == 5 and ck["done_ranges"] == []


@pytest.mark.asyncio
async def test_resume_under_replace_reprocesses_ignoring_done_ranges(tmp_path, monkeypatch):
    # Under --replace done_ranges is disabled: a resume must REPROCESS above-gap docs
    # (re-embed + prune orphans), not skip them, so edited content can't leave stale
    # chunks. Verified by the above-gap docs being re-embedded on resume.
    store = _FakeStore()
    monkeypatch.setattr(ingest_jsonl, "QdrantVectorStore", lambda **kw: store)
    monkeypatch.setattr(ingest_jsonl, "collection_name", lambda *a, **kw: "test")
    ckpt = tmp_path / "c.ckpt"

    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: _FakeEmbedder())
    corpus = tmp_path / "c.jsonl"
    records = [_article(1), _article(2), _article(3, poison=True), _article(4), _article(5)]
    _write_records(corpus, records)
    with pytest.raises(SystemExit):
        await ingest_jsonl.run(_args(
            corpus, batch_size=1, concurrency=2, replace=True, checkpoint=ckpt))
    # Under --replace done_ranges is never recorded.
    assert ingest_jsonl._read_checkpoint(ckpt)["done_ranges"] == []

    rec = _RecordingEmbedder()
    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: rec)
    records[2] = _article(3)
    _write_records(corpus, records)
    await ingest_jsonl.run(_args(
        corpus, batch_size=1, concurrency=2, replace=True, checkpoint=ckpt, resume=True))
    # Above-gap docs 4,5 are re-embedded (reprocessed), not skipped.
    joined = " ".join(rec.embedded)
    assert "number 4" in joined and "number 5" in joined


@pytest.mark.asyncio
async def test_resume_done_ranges_emits_doc_metrics_rows(tmp_path, monkeypatch):
    # A resume-skipped (done_range) doc must still get a per-doc metrics row so the
    # metrics file stays complete — marked skipped/resumed rather than silently gone.
    store = _FakeStore()
    monkeypatch.setattr(ingest_jsonl, "QdrantVectorStore", lambda **kw: store)
    monkeypatch.setattr(ingest_jsonl, "collection_name", lambda *a, **kw: "test")
    ckpt = tmp_path / "c.ckpt"
    metrics = tmp_path / "docs.jsonl"

    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: _FakeEmbedder())
    corpus = tmp_path / "c.jsonl"
    records = [_article(1), _article(2), _article(3, poison=True), _article(4), _article(5)]
    _write_records(corpus, records)
    with pytest.raises(SystemExit):
        await ingest_jsonl.run(_args(
            corpus, batch_size=1, concurrency=2, doc_metrics_out=metrics, checkpoint=ckpt))

    rec = _RecordingEmbedder()
    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: rec)
    records[2] = _article(3)
    _write_records(corpus, records)
    await ingest_jsonl.run(_args(
        corpus, batch_size=1, concurrency=2, doc_metrics_out=metrics, checkpoint=ckpt,
        resume=True))

    rows = [json.loads(x) for x in metrics.read_text().splitlines()]
    resumed = [r for r in rows if r.get("error") == "resumed (already indexed)"]
    # Both above-gap docs (4,5) get a resumed row on the second pass.
    assert len(resumed) == 2 and all(r["skipped"] and r["n_chunks"] == 0 for r in resumed)


class _IsolatingEmbedder:
    """Embedder exposing embed_isolated: any chunk containing POISONPILL is
    quarantined (None vector) rather than failing the whole batch — mirrors
    BatchingEmbedder's 4xx/over-context isolation."""

    async def embed(self, texts):
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]

    async def embed_isolated(self, texts):
        out, quarantined = [], 0
        for t in texts:
            if "POISONPILL" in t:
                out.append(None)
                quarantined += 1
            else:
                out.append([1.0, 0.0, 0.0, 0.0])
        return out, quarantined


@pytest.mark.asyncio
async def test_oversized_chunk_is_dropped_not_aborting_the_run(tmp_path, monkeypatch):
    """An unembeddable chunk (over the context window) is dropped via the embedder's
    embed_isolated backstop and the run COMPLETES, instead of failing the whole
    batch and exiting non-zero (the pre-backstop behaviour with all-or-nothing
    embed())."""
    store = _FakeStore()
    monkeypatch.setattr(ingest_jsonl, "QdrantVectorStore", lambda **kw: store)
    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: _IsolatingEmbedder())
    monkeypatch.setattr(ingest_jsonl, "collection_name", lambda *a, **kw: "test")

    corpus = tmp_path / "c.jsonl"
    # Long enough to classify as ARTICLE and chunk into several pieces; POISONPILL
    # lands in exactly one chunk (chunk_size=200 chars in _args).
    _write_corpus(corpus, ("alpha beta gamma. " * 60) + " POISONPILL " + ("delta epsilon. " * 60))

    # Must not raise SystemExit — the run completes despite the bad chunk.
    await ingest_jsonl.run(_args(corpus, checkpoint=tmp_path / "a.ckpt"))


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@pytest.mark.asyncio
async def test_doc_and_run_metrics_written(tmp_path, monkeypatch):
    """--doc-metrics-out writes one row per document (indexed + skipped), and
    --run-metrics-out writes one per-file summary row with the right counts."""
    store = _FakeStore()
    monkeypatch.setattr(ingest_jsonl, "QdrantVectorStore", lambda **kw: store)
    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: _FakeEmbedder())
    monkeypatch.setattr(ingest_jsonl, "collection_name", lambda *a, **kw: "test")

    corpus = tmp_path / "c.jsonl"
    # 2 articles (kept, multi-chunk) + 1 short non-article doc that is FILTERED OUT
    # by the doc_types=["article"] filter → a skipped per-doc row.
    skip_rec = {"text": "short blurb here", "path": "/corpus/masthead.pdf", "metadata": {}}
    _write_records(corpus, [_article(1), _article(2), skip_rec])
    doc_out = tmp_path / "docs.jsonl"
    run_out = tmp_path / "run.jsonl"
    await ingest_jsonl.run(_args(
        corpus, doc_types=["article"], batch_size=1, concurrency=2,
        doc_metrics_out=doc_out, run_metrics_out=run_out,
        checkpoint=tmp_path / "c.ckpt",
    ))

    rows = _read_jsonl(doc_out)
    assert len(rows) == 3  # one per document seen (2 indexed + 1 skipped)
    by_skip = {r["skipped"] for r in rows}
    assert by_skip == {True, False}
    indexed = [r for r in rows if not r["skipped"]]
    skipped = [r for r in rows if r["skipped"]]
    assert len(indexed) == 2 and len(skipped) == 1
    for r in indexed:
        assert r["n_chunks"] >= 1
        assert r["tokens_min"] is not None
        assert r["tokens_min"] <= r["tokens_median"] <= r["tokens_max"]
        assert r["chunk_chars_median"] is not None
        assert r["error"] is None
        assert r["source_file"].endswith(".pdf")
    sk = skipped[0]
    assert sk["n_chunks"] == 0 and sk["tokens_min"] is None and sk["error"] is None

    run_rows = _read_jsonl(run_out)
    assert len(run_rows) == 1
    run = run_rows[0]
    assert run["docs_seen"] == 3
    assert run["docs_indexed"] == 2
    assert run["docs_skipped"] == 1
    assert run["chunks"] == sum(r["n_chunks"] for r in indexed)
    assert run["failed_batches"] == 0
    assert run["failed_batch_seqs"] == []
    assert run["wall_s"] >= 0
    assert run["file"].endswith("c.jsonl")


@pytest.mark.asyncio
async def test_doc_metrics_record_failed_batch_error(tmp_path, monkeypatch):
    """A failed batch still emits per-doc rows carrying the error, and the run
    summary reports the failed batch count/seqs (metrics survive a partial run)."""
    store = _FakeStore()
    monkeypatch.setattr(ingest_jsonl, "QdrantVectorStore", lambda **kw: store)
    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: _FakeEmbedder())
    monkeypatch.setattr(ingest_jsonl, "collection_name", lambda *a, **kw: "test")

    corpus = tmp_path / "c.jsonl"
    _write_records(corpus, [_article(1), _article(2, poison=True), _article(3)])
    doc_out = tmp_path / "docs.jsonl"
    run_out = tmp_path / "run.jsonl"
    with pytest.raises(SystemExit):
        await ingest_jsonl.run(_args(
            corpus, batch_size=1, concurrency=1,
            doc_metrics_out=doc_out, run_metrics_out=run_out,
            checkpoint=tmp_path / "c.ckpt",
        ))

    rows = _read_jsonl(doc_out)
    errored = [r for r in rows if r["error"] and "failed" in r["error"]]
    assert errored, "the poisoned batch's doc should have an error row"
    assert all(r["n_chunks"] == 0 for r in errored)

    # Metrics are written even though the run exited non-zero.
    run_rows = _read_jsonl(run_out)
    assert len(run_rows) == 1
    assert run_rows[0]["failed_batches"] >= 1
    assert run_rows[0]["failed_batch_seqs"]

    assert store.points, "good chunks should still be stored"
    # The quarantined chunk's text is never indexed.
    assert not any("POISONPILL" in c.content for c in store.points.values())


@pytest.mark.asyncio
async def test_run_metrics_accumulates_across_files(tmp_path, monkeypatch):
    """Two separate (non-resume) invocations into the same --run-metrics-out
    accumulate one row each — the run-metrics log must not truncate on a fresh run."""
    store = _FakeStore()
    monkeypatch.setattr(ingest_jsonl, "QdrantVectorStore", lambda **kw: store)
    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: _FakeEmbedder())
    monkeypatch.setattr(ingest_jsonl, "collection_name", lambda *a, **kw: "test")

    run_out = tmp_path / "run.jsonl"
    file_a = tmp_path / "a.jsonl"
    file_b = tmp_path / "b.jsonl"
    _write_records(file_a, [_article(1)])
    _write_records(file_b, [_article(2)])

    await ingest_jsonl.run(_args(file_a, run_metrics_out=run_out,
                                 checkpoint=tmp_path / "a.ckpt"))
    await ingest_jsonl.run(_args(file_b, run_metrics_out=run_out,
                                 checkpoint=tmp_path / "b.ckpt"))

    rows = _read_jsonl(run_out)
    assert len(rows) == 2, "each fresh run must APPEND its per-file row"
    assert {Path(r["file"]).name for r in rows} == {"a.jsonl", "b.jsonl"}


# --------------------------------------------------------------------------
# --batch-retries: in-process transient-error retry (issue #71 part 2).
# --------------------------------------------------------------------------

class _FlakyEmbedder:
    """Embed the poison batch raises a TRANSIENT error its first `fail_times`
    calls, then succeeds — models a flapping endpoint that self-heals. Every
    non-poison batch always succeeds."""

    def __init__(self, fail_times: int, exc: Exception | None = None) -> None:
        self.fail_times = fail_times
        self.calls = 0
        self._exc = exc or ConnectionError("Server disconnected without sending a response")

    async def embed(self, texts):
        if any("POISONPILL" in t for t in texts):
            self.calls += 1
            if self.calls <= self.fail_times:
                raise self._exc
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


def test_is_transient_error_classification():
    assert ingest_jsonl.is_transient_error(ConnectionError("x"))
    assert ingest_jsonl.is_transient_error(TimeoutError("x"))
    assert ingest_jsonl.is_transient_error(RuntimeError("Server disconnected mid-stream"))
    assert ingest_jsonl.is_transient_error(RuntimeError("Connection timed out"))

    class _Resp:
        status_code = 503

    class _HTTPish(Exception):
        response = _Resp()

    assert ingest_jsonl.is_transient_error(_HTTPish("bad gateway"))
    # A genuine bad-input / 4xx must NOT be treated as transient.
    assert not ingest_jsonl.is_transient_error(ValueError("bad input"))
    assert not ingest_jsonl.is_transient_error(RuntimeError("dimension mismatch"))

    # Chained cause: PooledEmbedder raises RuntimeError('all embedding endpoints
    # failed') from the real transient fault — the walk must see through it, so a
    # multi-endpoint fan-out flap is retried, not misread as a hard failure.
    wrapped = RuntimeError("all embedding endpoints failed")
    wrapped.__cause__ = ConnectionError("Server disconnected without sending a response")
    assert ingest_jsonl.is_transient_error(wrapped)
    # The aggregate message alone is enough (explicit backstop), even with no cause.
    assert ingest_jsonl.is_transient_error(RuntimeError("all embedding endpoints failed"))
    # But a wrapper over a genuine bad-input cause stays non-transient.
    hard = RuntimeError("batch failed")
    hard.__cause__ = ValueError("dimension mismatch")
    assert not ingest_jsonl.is_transient_error(hard)


def test_retry_delay_is_capped_exponential():
    # The shared ragstack.ingestion.retry version jitters by +/-25% so N workers
    # retrying the same flap do not re-collide in lockstep, so these are ranges
    # rather than exact values. The exponential shape and the cap still hold.
    for attempt, base in ((1, 1.0), (2, 2.0), (3, 4.0)):
        d = ingest_jsonl.retry_delay(attempt)
        assert base * 0.75 <= d <= base * 1.25, (attempt, d)
    assert ingest_jsonl.retry_delay(99) <= 30.0 * 1.25  # capped (then jittered)


@pytest.mark.asyncio
async def test_batch_retries_lets_transient_flap_converge(tmp_path, monkeypatch):
    """A batch that hits a transient error twice then succeeds converges (rc=0,
    checkpoint reaches the last line) when --batch-retries covers the flaps."""
    store = _FakeStore()
    emb = _FlakyEmbedder(fail_times=2)
    monkeypatch.setattr(ingest_jsonl, "QdrantVectorStore", lambda **kw: store)
    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: emb)
    monkeypatch.setattr(ingest_jsonl, "collection_name", lambda *a, **kw: "test")
    monkeypatch.setattr(ingest_jsonl, "retry_delay", lambda *a, **k: 0.0)  # no real sleeps

    corpus = tmp_path / "c.jsonl"
    _write_records(corpus, [_article(1), _article(2), _article(3, poison=True),
                            _article(4), _article(5)])
    ckpt = tmp_path / "c.ckpt"
    # batch_size=1 → doc 3 is its own batch; batch_retries=3 covers its 2 flaps.
    await ingest_jsonl.run(_args(corpus, batch_size=1, concurrency=2,
                                 batch_retries=3, checkpoint=ckpt))

    assert emb.calls == 3, "poison batch should have been retried until success"
    # No stall: checkpoint advanced over the whole file, all 5 docs stored.
    assert ingest_jsonl._read_checkpoint(ckpt)["line"] == 5
    stored_docs = {c.doc_id for c in store.points.values()}
    assert len(stored_docs) == 5


@pytest.mark.asyncio
async def test_batch_retries_exhausted_still_stalls(tmp_path, monkeypatch):
    """If the flap outlasts the retry budget the run still exits non-zero and the
    checkpoint stalls at the gap — retries harden, they never mask a real failure."""
    store = _FakeStore()
    emb = _FlakyEmbedder(fail_times=99)  # never recovers
    monkeypatch.setattr(ingest_jsonl, "QdrantVectorStore", lambda **kw: store)
    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: emb)
    monkeypatch.setattr(ingest_jsonl, "collection_name", lambda *a, **kw: "test")
    monkeypatch.setattr(ingest_jsonl, "retry_delay", lambda *a, **k: 0.0)

    corpus = tmp_path / "c.jsonl"
    _write_records(corpus, [_article(1), _article(2), _article(3, poison=True),
                            _article(4), _article(5)])
    ckpt = tmp_path / "c.ckpt"
    with pytest.raises(SystemExit):
        await ingest_jsonl.run(_args(corpus, batch_size=1, concurrency=2,
                                     batch_retries=2, checkpoint=ckpt))
    assert emb.calls == 3, "1 initial attempt + 2 retries, then give up"
    assert ingest_jsonl._read_checkpoint(ckpt)["line"] == 2  # stalls before the gap


@pytest.mark.asyncio
async def test_non_transient_error_is_not_retried(tmp_path, monkeypatch):
    """A non-transient (bad-input) error must fail immediately, not burn retries."""
    store = _FakeStore()
    emb = _FlakyEmbedder(fail_times=99, exc=ValueError("dimension mismatch"))
    monkeypatch.setattr(ingest_jsonl, "QdrantVectorStore", lambda **kw: store)
    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: emb)
    monkeypatch.setattr(ingest_jsonl, "collection_name", lambda *a, **kw: "test")
    monkeypatch.setattr(ingest_jsonl, "retry_delay", lambda *a, **k: 0.0)

    corpus = tmp_path / "c.jsonl"
    _write_records(corpus, [_article(1), _article(2), _article(3, poison=True)])
    ckpt = tmp_path / "c.ckpt"
    with pytest.raises(SystemExit):
        await ingest_jsonl.run(_args(corpus, batch_size=1, concurrency=2,
                                     batch_retries=5, checkpoint=ckpt))
    assert emb.calls == 1, "non-transient error must not be retried"


@pytest.mark.asyncio
async def test_batch_retries_converge_on_wrapped_pool_failure(tmp_path, monkeypatch):
    """The prod case: a multi-endpoint fan-out flap surfaces as
    RuntimeError('all embedding endpoints failed') from a transient cause (as
    PooledEmbedder raises). --batch-retries must see through the wrapper and
    converge, not misread it as a hard failure."""
    def _wrapped():
        e = RuntimeError("all embedding endpoints failed")
        e.__cause__ = ConnectionError("Server disconnected without sending a response")
        return e

    store = _FakeStore()
    emb = _FlakyEmbedder(fail_times=2, exc=_wrapped())
    monkeypatch.setattr(ingest_jsonl, "QdrantVectorStore", lambda **kw: store)
    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: emb)
    monkeypatch.setattr(ingest_jsonl, "collection_name", lambda *a, **kw: "test")
    monkeypatch.setattr(ingest_jsonl, "retry_delay", lambda *a, **k: 0.0)

    corpus = tmp_path / "c.jsonl"
    _write_records(corpus, [_article(1), _article(2), _article(3, poison=True),
                            _article(4), _article(5)])
    ckpt = tmp_path / "c.ckpt"
    await ingest_jsonl.run(_args(corpus, batch_size=1, concurrency=2,
                                 batch_retries=3, checkpoint=ckpt))
    assert emb.calls == 3, "wrapped-pool-failure batch should retry until success"
    assert ingest_jsonl._read_checkpoint(ckpt)["line"] == 5
    assert len({c.doc_id for c in store.points.values()}) == 5


# --------------------------------------------------------------------------- #
# --chunk-concurrency (issue #66 phase 2): concurrent chunking, file-order folds.
# --------------------------------------------------------------------------- #

async def _run_and_capture(tmp_path, monkeypatch, chunk_concurrency):
    """Ingest a fixed corpus at a given --chunk-concurrency; return (chunk-id set,
    checkpoint line, catalog text). Deterministic chunker (fixed), so any two runs
    must agree — that is the file-order invariant the pipeline must preserve."""
    store = _FakeStore()
    monkeypatch.setattr(ingest_jsonl, "QdrantVectorStore", lambda **kw: store)
    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: _FakeEmbedder())
    monkeypatch.setattr(ingest_jsonl, "collection_name", lambda *a, **kw: "test")
    corpus = tmp_path / f"c{chunk_concurrency}.jsonl"
    # articles (kept) interleaved with front-matter (filtered) to exercise skips.
    _write_records(corpus, [_article(1), _front_matter(2), _article(3), _article(4),
                            _front_matter(5), _article(6), _article(7)])
    cat = tmp_path / f"cat{chunk_concurrency}.jsonl"
    ckpt = tmp_path / f"c{chunk_concurrency}.ckpt"
    await ingest_jsonl.run(_args(
        corpus, chunk_method="fixed", chunk_size=200, chunk_overlap=20,
        batch_size=2, concurrency=2, doc_types=["article"],
        catalog_out=cat, checkpoint=ckpt, chunk_concurrency=chunk_concurrency,
    ))
    ids = frozenset(store.points)
    line = ingest_jsonl._read_checkpoint(ckpt)["line"]
    return ids, line, cat.read_text()


@pytest.mark.asyncio
async def test_chunk_concurrency_matches_serial(tmp_path, monkeypatch):
    """--chunk-concurrency>1 must produce byte-identical results to serial: same
    chunk ids, same checkpoint line, same catalog (rows in the same file order).
    This is the #65 invariant — concurrent chunking, strictly file-ordered folds."""
    ids1, line1, cat1 = await _run_and_capture(tmp_path, monkeypatch, 1)
    ids4, line4, cat4 = await _run_and_capture(tmp_path, monkeypatch, 4)
    assert ids1 == ids4 and len(ids1) > 0
    assert line1 == line4 == 7  # checkpoint advances over the filtered tail equally
    assert cat1 == cat4  # catalog rows identical AND in the same order


@pytest.mark.asyncio
async def test_chunk_concurrency_actually_overlaps(tmp_path, monkeypatch):
    """With --chunk-concurrency>1 several documents are chunked at once."""
    import threading
    import time as _time

    from ragstack.models import Chunk

    class _SlowChunker:
        def __init__(self):
            self.active = 0
            self.max_active = 0
            self._lock = threading.Lock()

        def chunk(self, doc):
            with self._lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            _time.sleep(0.03)  # hold the slot so overlap is observable
            with self._lock:
                self.active -= 1
            return [Chunk(id=f"{doc.id}:0:1", doc_id=doc.id, content=doc.content[:1],
                          metadata=dict(doc.metadata), start_char=0, end_char=1)]

    chunker = _SlowChunker()
    monkeypatch.setattr(ingest_jsonl, "build_chunker", lambda *a, **kw: (chunker, None, 4096))
    monkeypatch.setattr(ingest_jsonl, "QdrantVectorStore", lambda **kw: _FakeStore())
    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: _FakeEmbedder())
    monkeypatch.setattr(ingest_jsonl, "collection_name", lambda *a, **kw: "test")

    corpus = tmp_path / "c.jsonl"
    _write_records(corpus, [_article(i) for i in range(1, 9)])
    await ingest_jsonl.run(_args(corpus, chunk_method="fixed", batch_size=2,
                                 concurrency=2, checkpoint=tmp_path / "c.ckpt",
                                 chunk_concurrency=4))
    assert chunker.max_active >= 2, "expected concurrent chunk() calls with cc=4"


@pytest.mark.asyncio
async def test_chunk_failure_propagates_without_hang(tmp_path, monkeypatch):
    """A chunker.chunk() exception must propagate out of run() (not be swallowed)
    AND run() must still return — the producer's finally drains sentinels + gathers
    the workers, so a chunk failure can't hang or orphan the pipeline."""
    class _BoomChunker:
        def chunk(self, doc):
            raise RuntimeError("chunk boom")

    monkeypatch.setattr(ingest_jsonl, "build_chunker", lambda *a, **kw: (_BoomChunker(), None, 4096))
    monkeypatch.setattr(ingest_jsonl, "QdrantVectorStore", lambda **kw: _FakeStore())
    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: _FakeEmbedder())
    monkeypatch.setattr(ingest_jsonl, "collection_name", lambda *a, **kw: "test")

    corpus = tmp_path / "c.jsonl"
    _write_records(corpus, [_article(1), _article(2), _article(3)])
    with pytest.raises(RuntimeError, match="chunk boom"):
        await ingest_jsonl.run(_args(corpus, chunk_method="fixed", batch_size=2,
                                     concurrency=2, chunk_concurrency=3,
                                     checkpoint=tmp_path / "c.ckpt"))
