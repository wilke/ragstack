"""Unit tests for FileSink — the embed-to-file stage of the decoupled ingest (#141).

FileSink writes each embedded Chunk as one JSON line into gzip JSONL shards that
roll every ``shard_size`` records, plus a manifest. iter_embedded_records streams
them back. These verify the round-trip is lossless and sharding/manifest are correct.
"""
from __future__ import annotations

from pathlib import Path

from ragstack.ingestion.sinks import (
    FileSink,
    iter_embedded_records,
    list_shards,
    read_manifests,
)
from ragstack.models import Chunk


def _chunks(n: int, dim: int = 4) -> list[Chunk]:
    return [
        Chunk(
            id=f"chunk-{i}", doc_id=f"doc-{i // 2}", content=f"body {i}",
            start_char=i * 10, end_char=i * 10 + 9,
            embedding=[float(i)] * dim,
            metadata={"tenant_id": "public", "title": f"t{i}", "prev_chunk_id": None},
        )
        for i in range(n)
    ]


async def test_roundtrip_preserves_every_field(tmp_path: Path):
    src = _chunks(5)
    sink = FileSink(tmp_path, "run", shard_size=2, compress=True,
                    meta={"model": "m", "dim": 4, "collection": "c", "tenant": "public"})
    await sink.write(src)
    await sink.aclose()

    read: list[Chunk] = []
    for shard in list_shards(tmp_path):
        read.extend(iter_embedded_records(shard))

    assert len(read) == 5
    by_id = {c.id: c for c in read}
    for orig in src:
        got = by_id[orig.id]
        assert got.doc_id == orig.doc_id
        assert got.content == orig.content
        assert got.start_char == orig.start_char and got.end_char == orig.end_char
        assert got.embedding == orig.embedding
        assert got.metadata == orig.metadata


async def test_shards_roll_at_shard_size(tmp_path: Path):
    sink = FileSink(tmp_path, "run", shard_size=2, compress=True, meta={"dim": 4})
    await sink.write(_chunks(5))
    await sink.aclose()
    shards = list_shards(tmp_path)
    # 5 records / 2 per shard -> 3 shards (2, 2, 1).
    assert [p.name for p in shards] == [
        "run-000.jsonl.gz", "run-001.jsonl.gz", "run-002.jsonl.gz"
    ]
    counts = [sum(1 for _ in iter_embedded_records(p)) for p in shards]
    assert counts == [2, 2, 1]


async def test_manifest_records_meta_and_shards(tmp_path: Path):
    sink = FileSink(tmp_path, "run", shard_size=2, compress=True,
                    meta={"model": "SFR", "dim": 4, "collection": "ragstack_x"})
    await sink.write(_chunks(3))
    await sink.aclose()
    manifests = read_manifests(tmp_path)
    assert len(manifests) == 1
    m = manifests[0]
    assert m["dim"] == 4 and m["model"] == "SFR" and m["collection"] == "ragstack_x"
    assert m["record_count"] == 3
    assert len(m["shards"]) == 2  # 3 records / 2 = shards 000, 001


async def test_no_compress_writes_plain_jsonl(tmp_path: Path):
    sink = FileSink(tmp_path, "run", shard_size=10, compress=False, meta={"dim": 4})
    await sink.write(_chunks(3))
    await sink.aclose()
    shards = list_shards(tmp_path)
    assert [p.name for p in shards] == ["run-000.jsonl"]
    assert sum(1 for _ in iter_embedded_records(shards[0])) == 3
