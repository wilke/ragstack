"""Unit tests for the qdrant_ingest_agent backpressure gate + drain (#141).

No real Qdrant: a stub store scripts its collection_health readings and records the
interleaving of health polls and upserts, so we can assert the agent blocks until
green and never exceeds --max-inflight concurrent upserts.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from ragstack.ingestion.sinks import FileSink
from ragstack.models import Chunk
from ragstack.stores.qdrant import CollectionHealth

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import qdrant_ingest_agent as agent  # noqa: E402


def _health(status: str, *, optimizer_ok: bool = True, unindexed: int = 0,
            segments: int = 1) -> CollectionHealth:
    return CollectionHealth(
        status=status, optimizer_ok=optimizer_ok, points_count=unindexed,
        indexed_vectors_count=0, segments_count=segments,
    )


class _ScriptedStore:
    """collection_health pops the scripted sequence, holding the last value; every
    poll and upsert appends to a shared event log for ordering assertions."""

    def __init__(self, health_seq: list[CollectionHealth]) -> None:
        self._seq = list(health_seq)
        self.events: list[tuple] = []
        self.upserted: list[Chunk] = []
        self._inflight = 0
        self.max_inflight = 0

    async def ensure_collection(self):
        pass

    async def collection_health(self):
        h = self._seq.pop(0) if len(self._seq) > 1 else self._seq[0]
        self.events.append(("poll", h.status))
        return h

    async def upsert(self, chunks):
        self._inflight += 1
        self.max_inflight = max(self.max_inflight, self._inflight)
        self.events.append(("upsert", len(chunks)))
        await asyncio.sleep(0)  # yield so overlapping tasks could interleave
        self.upserted.extend(chunks)
        self._inflight -= 1


async def _write_shard(tmp_path: Path, n: int) -> list[Path]:
    from ragstack.ingestion.sinks import list_shards
    sink = FileSink(tmp_path, "run", shard_size=1000, compress=True, meta={"dim": 2})
    await sink.write([
        Chunk(id=f"c{i}", doc_id="d", content=f"x{i}", embedding=[float(i), 0.0],
              metadata={"tenant_id": "public"})
        for i in range(n)
    ])
    await sink.aclose()
    return list_shards(tmp_path)


async def test_gate_blocks_until_green(tmp_path: Path):
    shards = await _write_shard(tmp_path, 3)
    store = _ScriptedStore([_health("yellow"), _health("yellow"), _health("green")])
    gate = agent.HealthGate(store, poll_interval=0.0, max_unindexed=10_000)

    stats = await agent.drain(
        shards, store, None, gate,
        batch_size=10, max_inflight=1,
        checkpoint_path=tmp_path / "ck", resume=False,
        delete_shards=False, batch_retries=0,
    )

    # Three polls (yellow, yellow, green) precede the single upsert.
    assert store.events == [
        ("poll", "yellow"), ("poll", "yellow"), ("poll", "green"), ("upsert", 3)
    ]
    assert gate.polls == 3
    assert stats["chunks"] == 3 and len(store.upserted) == 3


async def test_optimizer_busy_throttles_even_when_green(tmp_path: Path):
    shards = await _write_shard(tmp_path, 2)
    store = _ScriptedStore([
        _health("green", optimizer_ok=False),  # optimizing -> blocked
        _health("green", optimizer_ok=True),
    ])
    gate = agent.HealthGate(store, poll_interval=0.0)
    await agent.drain(shards, store, None, gate, batch_size=10, max_inflight=1,
                      checkpoint_path=tmp_path / "ck", resume=False,
                      delete_shards=False, batch_retries=0)
    assert gate.polls == 2  # blocked once on the optimizer, then proceeded
    assert len(store.upserted) == 2


async def test_unindexed_backlog_throttles(tmp_path: Path):
    shards = await _write_shard(tmp_path, 2)
    store = _ScriptedStore([
        _health("green", unindexed=500),  # backlog over the ceiling
        _health("green", unindexed=10),
    ])
    gate = agent.HealthGate(store, poll_interval=0.0, max_unindexed=100)
    await agent.drain(shards, store, None, gate, batch_size=10, max_inflight=1,
                      checkpoint_path=tmp_path / "ck", resume=False,
                      delete_shards=False, batch_retries=0)
    assert gate.polls == 2
    assert len(store.upserted) == 2


async def test_max_inflight_is_respected(tmp_path: Path):
    shards = await _write_shard(tmp_path, 6)
    store = _ScriptedStore([_health("green")])  # always healthy
    gate = agent.HealthGate(store, poll_interval=0.0)
    await agent.drain(shards, store, None, gate, batch_size=1, max_inflight=1,
                      checkpoint_path=tmp_path / "ck", resume=False,
                      delete_shards=False, batch_retries=0)
    # 6 single-chunk batches, but never more than one upsert in flight at once.
    assert store.max_inflight == 1
    assert len(store.upserted) == 6
