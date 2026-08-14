"""Atomic single-shard **embed** stage (ADR-0001 offline plane, #141).

``run_embed_shard`` is the GPU-bound half of the decoupled bulk pipeline: it runs
one shard through :meth:`IngestionPipeline.embed_source` (load → chunk → embed →
link) and writes the surviving embedded chunks to a JSONL **embedding file** (the
:mod:`ragstack.ingestion.embedding_file` contract). It never touches Qdrant/ES —
that is the separate load stage's job — so a Qdrant stall can never block the
embedding fleet, which is the whole point of #141.

Like :func:`ragstack.ingestion.shard.run_shard`, it **does not raise** for ordinary
failures: a load/embed/write error becomes a ``status=failed`` receipt so a
scattered task fails just its own shard (the engine retries it) rather than
aborting the run. ``asyncio.CancelledError`` (a ``BaseException``) propagates so
the engine's cancellation/timeout is honoured. Re-running a shard overwrites its
embedding file in place (deterministic uuid5 ids + sorted serialization), so an
engine retry is safe.
"""
from __future__ import annotations

from pathlib import Path

from ragstack.ingestion.embedding_file import EmbeddingFileWriter
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.ingestion.receipts import COMPLETED, FAILED, ShardReceipt


async def run_embed_shard(
    pipeline: IngestionPipeline,
    shard_path: str,
    tenant: str,
    shard_id: str,
    out_path: str | Path,
    group_size: int = 64,
) -> ShardReceipt:
    """Embed one shard → embedding file at ``out_path``; return its receipt.

    The receipt's ``embedding_file`` names the file the load stage consumes;
    ``n_chunks``/``chunk_ids`` describe what it contains. ``n_docs`` is the count
    of distinct documents that contributed a surviving chunk.

    Streams: chunks are embedded in document groups (:meth:`iter_embed_source`)
    and written to the file one at a time, so peak memory is bounded to a group
    rather than the whole shard — a 500k-doc shard would OOM if materialized
    (the #144 benchmark measured 2.35 GB for just 3k docs). A partial file from a
    mid-shard failure is unlinked so a retry starts clean.

    ``group_size`` is the fan-out ceiling, not just a memory bound (#334): one
    group is one ``embed()`` call, and the pool can spread a call across at most
    ``ceil(chunks / request_batch)`` endpoints. At the old fixed 64 docs
    (~3 chunks/doc → ~190 chunks → 1.5 sub-requests against a 128 batch), a
    six-endpoint fleet ran on ~1.3 GPUs — measured over 937 samples of a
    production batch — with four cards never above 5%. Callers with a fleet
    should size this so a group yields at least ``len(endpoints) x
    request_batch`` chunks; ``scripts/embed_shard.py`` derives that
    automatically.
    """
    out = Path(out_path)
    writer = EmbeddingFileWriter(out, tenant=tenant)
    chunk_ids: list[str] = []
    doc_ids: set[str] = set()
    try:
        async for group in pipeline.iter_embed_source(
            shard_path, tenant_id=tenant, group_size=max(1, group_size)
        ):
            for chunk in group:
                writer.write(chunk)
                chunk_ids.append(chunk.id)
                doc_ids.add(chunk.doc_id)
    except Exception as e:  # noqa: BLE001 — isolate the shard; the engine retries
        writer.close()
        out.unlink(missing_ok=True)
        return ShardReceipt(shard_id, tenant, FAILED, error=f"{type(e).__name__}: {e}")
    writer.close()

    if writer.count == 0:
        out.unlink(missing_ok=True)
        return ShardReceipt(shard_id, tenant, FAILED,
                            error="empty: no embeddable chunks for source")
    return ShardReceipt(shard_id, tenant, COMPLETED, n_docs=len(doc_ids),
                        n_chunks=writer.count, chunk_ids=chunk_ids,
                        embedding_file=str(out))
