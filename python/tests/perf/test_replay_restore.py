"""Perf budgets for #358 (the #355 convention: p50/p95 over N repetitions, a
grep-able ``PERF`` line, an explicit budget).

1. **Replay of 35k chunks into the in-memory stores < 20 s.** One archive
   version of 35 000 synthetic chunks is packed with the real writer, then
   replayed through ``run_replay`` (verify → per-version delete-prior → batched
   upserts). The dim is 128, not 4096: the budget targets the LOADER's
   throughput (verification pass, gzip/JSON decode, Chunk construction, two
   store legs per batch), not sha256 over 560 MB — the packer's own 4096-d
   budget lives in ``test_archive_pack.py``. Timed once (a single replay is the
   workload), reported the same way.

2. **The lifecycle check on the resolution path < 0.2 ms p95.** It is one
   registry read, memoized: the store's ``get`` is counted and must be hit
   exactly once across the whole run.

    pytest tests/perf/test_replay_restore.py -m perf -q -s
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from ragstack.api.lifecycle import LifecycleGate
from ragstack.api.security import Principal
from ragstack.collection_store import AccessTracker, CollectionSpec, InMemoryCollectionStore
from ragstack.ingestion.chunkers import RecursiveCharacterChunker
from ragstack.ingestion.load_embeddings import run_replay
from ragstack.ingestion.loaders import JsonlLoader
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.stores.memory import InMemoryTextIndex, InMemoryVectorStore
from tests.archive_support import chunk_version
from tests.perf._budget import assert_budget_async

N_CHUNKS = 35_000
DIM = 128
REPLAY_BUDGET_S = 20.0
GATE_BUDGET_S = 0.0002
SPEC = "cafe0001"


class _NoEmbed:
    async def embed(self, texts):  # pragma: no cover
        raise RuntimeError("replay does not embed")


@pytest.mark.perf
async def test_replay_35k_chunks_into_memory_stores_within_budget(tmp_path: Path) -> None:
    t0 = time.perf_counter()
    vdir, _ = chunk_version(tmp_path, 1, N_CHUNKS, dim=DIM, spec_hash=SPEC, chunks_per_doc=4)
    pack_s = time.perf_counter() - t0
    vstore, tindex = InMemoryVectorStore(), InMemoryTextIndex()
    pipeline = IngestionPipeline(
        loader=JsonlLoader(), chunker=RecursiveCharacterChunker(), embedder=_NoEmbed(),
        vector_store=vstore, text_index=tindex, delete_prior=False,
    )
    t0 = time.perf_counter()
    summary = await run_replay(pipeline, [vdir], spec_hash=SPEC, collection_id="lib")
    replay_s = time.perf_counter() - t0
    print(f"PERF replay_35k_chunks: {replay_s:.2f}s (pack={pack_s:.1f}s) "
          f"budget={REPLAY_BUDGET_S:.1f}s n_chunks={summary.n_chunks} "
          f"rate={summary.n_chunks / replay_s:,.0f} chunks/s")
    assert summary.status == "completed" and summary.n_chunks == N_CHUNKS
    assert len(vstore._chunks) == N_CHUNKS and len(tindex._chunks) == N_CHUNKS
    assert replay_s <= REPLAY_BUDGET_S, f"replay took {replay_s:.1f}s > {REPLAY_BUDGET_S}s"


class _CountingStore(InMemoryCollectionStore):
    def __init__(self) -> None:
        super().__init__()
        self.gets = 0

    async def get(self, cid):
        self.gets += 1
        return await super().get(cid)


@pytest.mark.perf
async def test_lifecycle_check_on_resolution_is_one_cached_read() -> None:
    store = _CountingStore()
    await store.put(CollectionSpec(id="lib", collection="ragstack_lib_lib",
                                   embedding_model="m", embedding_model_dim=4))
    gate = LifecycleGate(store, tracker=AccessTracker(store, flush_seconds=3600),
                         cache_seconds=60.0)
    principal = Principal(tenant="bvbrc:alice@patricbrc.org", role="user", token="t")
    await gate.enforce(principal, "lib")  # warm: the one registry read

    await assert_budget_async(
        "lifecycle_gate_check", lambda: gate.enforce(principal, "lib"),
        budget_s=GATE_BUDGET_S, n=500,
    )
    assert store.gets == 1, f"expected one registry read, saw {store.gets}"
    assert gate.reads == 1
