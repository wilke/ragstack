"""Perf budget for the archive packer (#357): 35k chunks x 4096-d (~560 MB of
float32) packs in < 30 s with < 1.5 GB peak-RSS growth — streaming, never a
materialised list of Python floats (the #342 lesson: ~4.6x expansion).

The input embed file is generated on the fly (nothing committed): 64 distinct
full-precision random vectors, serialised once and cycled, so the parse cost
per line is the real one (~85 KB of 17-digit decimals) while generation stays
cheap. The file is ~2.9 GB; it lives in ``tmp_path`` and is removed with it.

Peak RSS is ``resource.getrusage`` before/after — for this process (the
streaming main loop) and, separately, for the packer worker processes, both
printed. Run alone for a meaningful "before" (ru_maxrss is a high-water mark):

    pytest tests/perf/test_archive_pack.py -m perf -q -s
"""
from __future__ import annotations

import json
import random
import resource
import time
from pathlib import Path

import pytest

from ragstack.ingestion.archive import default_workers, verify_version, write_version
from ragstack.ingestion.embedding_file import SCHEMA

N_CHUNKS = 35_000
DIM = 4096
DISTINCT = 64
BUDGET_S = 30.0
RSS_BUDGET_BYTES = 1.5 * 1024 ** 3


def _kb_to_bytes(kb: int) -> int:
    return kb * 1024  # Linux ru_maxrss is in kilobytes


def _generate(path: Path) -> None:
    rng = random.Random(357)
    vec_texts = [json.dumps([rng.uniform(-1.0, 1.0) for _ in range(DIM)])
                 for _ in range(DISTINCT)]
    with path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"schema": SCHEMA, "tenant": "public", "dim": DIM}) + "\n")
        for i in range(N_CHUNKS):
            # Same key layout as Chunk.model_dump() with sort_keys=True.
            fh.write(f'{{"content": "passage {i} of a scientific article about hybrid '
                     f'retrieval, reciprocal rank fusion and evaluation on SciFact.", '
                     f'"doc_id": "doc-{i // 4}", "embedding": {vec_texts[i % DISTINCT]}, '
                     f'"end_char": 120, "id": "chunk-{i}", '
                     f'"metadata": {{"title": "T{i // 4}", "year": 2024}}, "start_char": 0}}\n')


@pytest.mark.perf
def test_pack_35k_x_4096_streams_within_budget(tmp_path: Path) -> None:
    emb = tmp_path / "big.emb.jsonl"
    t0 = time.perf_counter()
    _generate(emb)
    gen_s = time.perf_counter() - t0
    (tmp_path / "receipt.json").write_text(json.dumps({"n_chunks": N_CHUNKS}))
    print(f"\nPERF archive_pack: generated {emb.stat().st_size / 1e9:.2f} GB input in {gen_s:.1f}s")

    workers = default_workers()
    self_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    kids_before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    t0 = time.perf_counter()
    manifest = write_version(tmp_path / "out", 1, [emb], [tmp_path / "receipt.json"],
                             collection_id="perf", tenant="public", workers=workers)
    pack_s = time.perf_counter() - t0
    self_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    kids_after = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss

    self_growth = _kb_to_bytes(self_after - self_before)
    kids_peak = _kb_to_bytes(kids_after)
    vec_bytes = (tmp_path / "out" / "1" / "vectors.f32").stat().st_size
    print(f"PERF archive_pack: {N_CHUNKS} x {DIM} -> vectors.f32 {vec_bytes / 1e6:.0f} MB "
          f"in {pack_s:.1f}s (budget {BUDGET_S:.0f}s) workers={workers} "
          f"rate={N_CHUNKS / pack_s:.0f} chunks/s")
    print(f"PERF archive_pack: RSS self {_kb_to_bytes(self_before) / 1e6:.0f} MB -> "
          f"{_kb_to_bytes(self_after) / 1e6:.0f} MB (growth {self_growth / 1e6:.0f} MB, "
          f"budget {RSS_BUDGET_BYTES / 1e6:.0f} MB); worker peak "
          f"{(kids_peak - _kb_to_bytes(kids_before)) / 1e6:.0f} MB growth, {kids_peak / 1e6:.0f} MB high-water")

    assert manifest["counts"] == {"chunks": N_CHUNKS, "docs": N_CHUNKS // 4}
    assert vec_bytes == 64 + N_CHUNKS * DIM * 4
    assert pack_s < BUDGET_S, f"pack took {pack_s:.1f}s > {BUDGET_S}s"
    assert self_growth < RSS_BUDGET_BYTES, f"RSS grew {self_growth / 1e6:.0f} MB"
    assert kids_peak < RSS_BUDGET_BYTES, f"a packer worker peaked at {kids_peak / 1e6:.0f} MB"

    t0 = time.perf_counter()
    verify_version(tmp_path / "out" / "1")
    print(f"PERF archive_pack: verify (sha256 of every file + geometry) {time.perf_counter() - t0:.1f}s")
