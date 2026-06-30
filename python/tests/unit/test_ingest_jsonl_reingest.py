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
        "catalog_out": None, "no_index": False, "checkpoint": None, "resume": False,
        "chunk_size": 200, "chunk_overlap": 20, "batch_size": 2, "concurrency": 2,
        "embedding_url": ["http://x"], "embedding_api": "openai", "embedding_model": "m",
        "embedding_max_concurrency": 4, "collection": "test", "qdrant_url": "http://q",
        "text_backend": "memory", "es_url": "http://es", "es_index": "i",
        "qdrant_timeout": 120.0, "replace": False, "delete_concurrency": 4,
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
