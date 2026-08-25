"""Extraction throughput is MEASURED, not budgeted (#350 / #355).

The number that sizes ``graph_extraction_jobs_per_owner`` is chunks/s against
the real LLM endpoint over a ~35k-chunk collection — a run this repo cannot
make hermetically. What this test measures is the driver's overhead and its
concurrency: 100 archived chunks through ``extract_version`` at concurrency 8
against a fake LLM with a fixed 10 ms latency (a zero-latency fake would
measure Python overhead only and print a meaningless rate). Serial would be
~1 s; concurrency 8 should land near 0.13 s. Printed, never asserted against a
budget — the real measurement is recorded on the PR / #350.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from ragstack.graph.extract_version import extract_version
from ragstack.graph.extractor import LLMKGExtractor
from ragstack.ingestion.archive import read_triples, write_version
from ragstack.ingestion.embedding_file import SCHEMA

pytestmark = pytest.mark.perf

N_CHUNKS = 100
CONCURRENCY = 8
LATENCY_S = 0.010


class LatencyLLM:
    """One triple per chunk after a fixed simulated round trip."""

    def __init__(self) -> None:
        self.in_flight = 0
        self.peak = 0

    async def complete_text(self, prompt: str, **_kw: object) -> str:
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(LATENCY_S)
        finally:
            self.in_flight -= 1
        text = prompt.rsplit("Text:\n", 1)[-1].strip()
        subject = text.split(" is ", 1)[0]
        return json.dumps({"triples": [{"subject": subject, "predicate": "is",
                                        "object": "a drug", "evidence": text}]})


def _version(root: Path, n: int) -> Path:
    emb = root / "v1.emb.jsonl"
    header = {"schema": SCHEMA, "tenant": "public", "dim": 4}
    recs = [{"id": f"c{i}", "doc_id": f"d{i // 4}", "content": f"Drug{i} is a drug.",
             "embedding": [0.1, 0.2, 0.3, 0.4], "metadata": {"tenant_id": "public"},
             "start_char": 0, "end_char": 20} for i in range(n)]
    emb.write_text("\n".join(json.dumps(r) for r in [header, *recs]) + "\n")
    receipt = root / "r.json"
    receipt.write_text(json.dumps({"status": "completed"}))
    write_version(root, 1, [emb], [receipt], collection_id="lib", tenant="public",
                  spec_hash="s", workers=1)
    return root / "1"


@pytest.mark.asyncio
async def test_extract_version_throughput_at_concurrency_8(tmp_path: Path) -> None:
    vdir = _version(tmp_path, N_CHUNKS)
    llm = LatencyLLM()
    t0 = time.perf_counter()
    summary = await extract_version(vdir, LLMKGExtractor(llm), concurrency=CONCURRENCY)
    wall = time.perf_counter() - t0
    rate = N_CHUNKS / wall
    print(f"PERF extract_graph_fake_llm: {N_CHUNKS} chunks in {wall:.3f}s = {rate:.0f} chunks/s "
          f"(concurrency={CONCURRENCY}, fake LLM latency {LATENCY_S * 1000:.0f}ms, "
          f"peak in flight={llm.peak}; measured, not budgeted — the real number needs "
          f"the dev tenant's LLM endpoint)")
    assert summary.n_triples == N_CHUNKS and summary.n_chunks == N_CHUNKS
    assert llm.peak == CONCURRENCY  # the semaphore is the throughput lever
    assert len(list(read_triples(vdir))) == N_CHUNKS
