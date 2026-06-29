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


class _FakeEmbedder:
    async def embed(self, texts):
        # Fail the batch carrying the poison marker; succeed for everything else.
        if any("POISONPILL" in t for t in texts):
            raise RuntimeError("embed boom")
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


def _args(input_path: Path, **over) -> argparse.Namespace:
    base = {
        "input": input_path, "tenant": "public", "doc_types": None, "limit": 0,
        "catalog_out": None, "no_index": False, "checkpoint": None, "resume": False,
        "chunk_size": 200, "chunk_overlap": 20, "batch_size": 2, "concurrency": 2,
        "embedding_url": ["http://x"], "embedding_api": "openai", "embedding_model": "m",
        "embedding_max_concurrency": 4, "collection": "test", "qdrant_url": "http://q",
        "text_backend": "memory", "es_url": "http://es", "es_index": "i",
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

    # Re-ingest the SAME path with a much SHORTER edited body → fewer chunks.
    # Without delete-before-upsert, the first ingest's trailing chunks linger as
    # orphans; with it, the store holds exactly the second ingest's chunks.
    store.ops.clear()
    _write_corpus(corpus, "completely different shorter wording now. " * 20)
    await ingest_jsonl.run(_args(corpus, checkpoint=tmp_path / "b.ckpt"))

    second_upserted = sum(
        int(op.split(":")[1]) for op in store.ops if op.startswith("upsert:")
    )
    # Store holds exactly the second ingest's chunks — no first-ingest orphans.
    assert len(store.points) == second_upserted
    # And the delete preceded the upsert for the document (replace, not append).
    first_delete = next(i for i, op in enumerate(store.ops) if op.startswith("delete:"))
    first_upsert = next(i for i, op in enumerate(store.ops) if op.startswith("upsert:"))
    assert first_delete < first_upsert


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
