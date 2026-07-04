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


async def test_resume_continues_after_existing_shards_no_overwrite(tmp_path: Path):
    # First pass: 3 chunks, shard_size 2 -> run-000 (2), run-001 (1).
    s1 = FileSink(tmp_path, "run", shard_size=2, compress=True, meta={"dim": 4})
    await s1.write(_chunks(3))
    await s1.aclose()
    first_shards = [p.name for p in list_shards(tmp_path)]
    first_ids = {c.id for p in list_shards(tmp_path) for c in iter_embedded_records(p)}
    assert first_shards == ["run-000.jsonl.gz", "run-001.jsonl.gz"]

    # Resume: a NEW FileSink, same run_id + dir (mimics --resume after a crash).
    more = [
        Chunk(id=f"r-{i}", doc_id="d", content=f"m{i}", embedding=[float(i)] * 4,
              metadata={"tenant_id": "public"})
        for i in range(2)
    ]
    s2 = FileSink(tmp_path, "run", shard_size=2, compress=True, meta={"dim": 4})
    await s2.write(more)
    await s2.aclose()

    all_shards = [p.name for p in list_shards(tmp_path)]
    # Old shards preserved; the new one is appended past the highest index.
    assert all_shards == ["run-000.jsonl.gz", "run-001.jsonl.gz", "run-002.jsonl.gz"]
    all_ids = {c.id for p in list_shards(tmp_path) for c in iter_embedded_records(p)}
    # Nothing overwritten/lost: original 3 + new 2 all present.
    assert all_ids == first_ids | {"r-0", "r-1"}
    assert len(all_ids) == 5


async def test_no_compress_writes_plain_jsonl(tmp_path: Path):
    sink = FileSink(tmp_path, "run", shard_size=10, compress=False, meta={"dim": 4})
    await sink.write(_chunks(3))
    await sink.aclose()
    shards = list_shards(tmp_path)
    assert [p.name for p in shards] == ["run-000.jsonl"]
    assert sum(1 for _ in iter_embedded_records(shards[0])) == 3
