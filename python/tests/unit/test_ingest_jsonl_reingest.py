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
