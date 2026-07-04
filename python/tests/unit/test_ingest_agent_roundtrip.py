"""Unit tests for qdrant_ingest_agent: point-id parity, resume, dim guard (#141).

Confirms that draining embed-file records produces the SAME Qdrant point ids the
coupled path would; that --resume skips drained shards; that --delete-shards unlinks
finished files; and that a dim mismatch hard-fails before any upsert.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ragstack.ingestion.sinks import FileSink, iter_embedded_records, list_shards
from ragstack.models import Chunk
from ragstack.stores.errors import VectorDimMismatch
from ragstack.stores.qdrant import QdrantVectorStore, _point_id
from ragstack.tenancy import tenant_of

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import qdrant_ingest_agent as agent  # noqa: E402


class _RecordingStore:
    def __init__(self) -> None:
        self.upserted: list[Chunk] = []

    async def ensure_collection(self):
        pass

    async def collection_health(self):
        return SimpleNamespace(status="green", optimizer_ok=True, unindexed=0,
                               segments_count=1)

    async def upsert(self, chunks):
        self.upserted.extend(chunks)


async def _write(tmp_path: Path, chunks: list[Chunk]) -> list[Path]:
    sink = FileSink(tmp_path, "run", shard_size=2, compress=True,
                    meta={"model": "SFR", "dim": 4, "collection": "ragstack_sfr_x",
                          "tenant": "public"})
    await sink.write(chunks)
    await sink.aclose()
    return list_shards(tmp_path)


def _chunks(n: int) -> list[Chunk]:
    return [
        Chunk(id=f"chunk-{i}", doc_id=f"doc-{i}", content=f"b{i}",
              embedding=[float(i)] * 4, metadata={"tenant_id": "public"})
        for i in range(n)
    ]


async def test_reconstructed_records_yield_identical_point_ids(tmp_path: Path):
    src = _chunks(3)
    shards = await _write(tmp_path, src)
    read: list[Chunk] = []
    for s in shards:
        read.extend(iter_embedded_records(s))
    got = {c.id: _point_id(c.id, tenant_of(c)) for c in read}
    want = {c.id: _point_id(c.id, tenant_of(c)) for c in src}
    assert got == want  # file round-trip preserves id + tenant -> same point id


async def test_resume_skips_completed_shards(tmp_path: Path):
    shards = await _write(tmp_path, _chunks(5))
    ck = tmp_path / "agent.ck"
    store = _RecordingStore()
    gate = agent.HealthGate(store, poll_interval=0.0)
    first = await agent.drain(shards, store, None, gate, batch_size=2, max_inflight=1,
                              checkpoint_path=ck, resume=False, delete_shards=False,
                              batch_retries=0)
    assert first["chunks"] == 5 and len(store.upserted) == 5

    store2 = _RecordingStore()
    gate2 = agent.HealthGate(store2, poll_interval=0.0)
    second = await agent.drain(shards, store2, None, gate2, batch_size=2, max_inflight=1,
                               checkpoint_path=ck, resume=True, delete_shards=False,
                               batch_retries=0)
    assert second["chunks"] == 0 and store2.upserted == []  # nothing re-drained


async def test_delete_shards_unlinks_drained_files(tmp_path: Path):
    shards = await _write(tmp_path, _chunks(3))
    assert all(p.exists() for p in shards)
    store = _RecordingStore()
    gate = agent.HealthGate(store, poll_interval=0.0)
    await agent.drain(shards, store, None, gate, batch_size=2, max_inflight=1,
                      checkpoint_path=tmp_path / "ck", resume=False,
                      delete_shards=True, batch_retries=0)
    assert all(not p.exists() for p in shards)  # every shard removed after draining


async def test_resolve_dim_and_collection_from_manifest(tmp_path: Path):
    shards = await _write(tmp_path, _chunks(2))
    from ragstack.ingestion.sinks import read_manifests
    manifests = read_manifests(tmp_path)
    ns = SimpleNamespace(collection=None)
    assert agent._resolve_dim(ns, manifests, shards) == 4
    assert agent._resolve_collection(ns, manifests) == "ragstack_sfr_x"


class _FakeClient:
    """Minimal AsyncQdrantClient stand-in with one existing collection."""

    def __init__(self, name: str, size: int) -> None:
        self._name, self._size = name, size

    async def get_collections(self):
        return SimpleNamespace(collections=[SimpleNamespace(name=self._name)])

    async def get_collection(self, name):
        return SimpleNamespace(config=SimpleNamespace(params=SimpleNamespace(
            vectors=SimpleNamespace(size=self._size))))


async def test_dim_mismatch_hard_fails_before_upsert():
    # Agent would build the store with vector_size = manifest dim (4); the existing
    # collection is 8-d -> ensure_collection must raise, never upsert mixed sizes.
    store = QdrantVectorStore(collection="ragstack_sfr_x", vector_size=4)
    store._client = _FakeClient("ragstack_sfr_x", 8)
    with pytest.raises(VectorDimMismatch):
        await store.ensure_collection()
