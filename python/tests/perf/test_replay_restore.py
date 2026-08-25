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

3. **Restore admission (#381) is one count read per dormant access and none
   per normal access.** The store's ``begin_restore`` (count + swap) is
   counted: zero over 200 active accesses, exactly one per dormant access;
   the dormant path (reset + access) stays under 2 ms p95.

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


class _AdmissionCountingStore(_CountingStore):
    def __init__(self) -> None:
        super().__init__()
        self.begins = 0

    async def begin_restore(self, cid, *, expect, limit, reason=""):
        self.begins += 1
        return await super().begin_restore(cid, expect=expect, limit=limit, reason=reason)


class _Capacity:
    """A bound with room in it: the count runs, nothing is ever evicted."""

    def limit(self) -> int | None:
        return 10

    async def make_room(self) -> str | None:  # pragma: no cover - must not be called
        raise AssertionError("no eviction below the bound")


class _NoRestorer:
    async def submit(self, rec, token):
        return "sub"

    def watching(self, cid):
        return False

    async def drain(self):
        return None


@pytest.mark.perf
async def test_restore_admission_is_one_count_read_per_dormant_access_and_none_otherwise() -> None:
    """#381: the admission check is ONE ``begin_restore`` (count + swap in the
    store) per dormant access and NOTHING on a normal access — the cached
    registry read of the active path stays the only call it makes."""
    from fastapi import HTTPException

    from ragstack.collection_store import DORMANT

    store = _AdmissionCountingStore()
    await store.put(CollectionSpec(id="lib", collection="ragstack_lib_lib",
                                   embedding_model="m", embedding_model_dim=4))
    gate = LifecycleGate(store, tracker=AccessTracker(store, flush_seconds=3600),
                         capacity=_Capacity(), cache_seconds=60.0)
    gate.restorer = _NoRestorer()  # type: ignore[assignment]
    # A BV-BRC bearer: the one caller that can trigger a restore (gowe_caller).
    principal = Principal(tenant="bvbrc:alice@patricbrc.org", role="user", token="t",
                          issuer="bvbrc", subject="alice@patricbrc.org")

    # Normal accesses: no admission call at all.
    for _ in range(200):
        await gate.enforce(principal, "lib")
    assert store.begins == 0 and gate.admissions == 0 and store.gets == 1

    # Dormant accesses: each one is refused 503 after exactly one admission
    # call. The row is reset to dormant between accesses (a store write and a
    # cache invalidation, both inside the timed callable — the budget covers
    # them); each reset costs the one registry re-read the cache always paid.
    n = 200

    async def dormant_access() -> None:
        await store.set_state("lib", DORMANT, reason="evicted")
        gate.invalidate("lib")
        try:
            await gate.enforce(principal, "lib")
        except HTTPException as e:
            assert e.status_code == 503 and "is restoring" in str(e.detail)
        else:  # pragma: no cover
            raise AssertionError("a dormant access must be refused")

    await assert_budget_async("restore_admission_check", dormant_access, budget_s=0.002, n=n)
    await gate.drain()
    assert store.begins == n, f"expected one admission per dormant access, saw {store.begins}"
    assert gate.admissions == n and gate.evictions == 0
    assert store.gets == 1 + n, f"expected one re-read per invalidation, saw {store.gets}"
