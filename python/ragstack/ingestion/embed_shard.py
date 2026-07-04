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

from ragstack.ingestion.embedding_file import write_embedding_file
from ragstack.ingestion.pipeline import EmptyIngestError, IngestionPipeline
from ragstack.ingestion.receipts import COMPLETED, FAILED, ShardReceipt


async def run_embed_shard(
    pipeline: IngestionPipeline,
    shard_path: str,
    tenant: str,
    shard_id: str,
    out_path: str | Path,
) -> ShardReceipt:
    """Embed one shard → embedding file at ``out_path``; return its receipt.

    The receipt's ``embedding_file`` names the file the load stage consumes;
    ``n_chunks``/``chunk_ids`` describe what it contains. ``n_docs`` is the count
    of distinct documents that contributed a surviving chunk.
    """
    try:
        chunks = await pipeline.embed_source(shard_path, tenant_id=tenant)
    except EmptyIngestError as e:
        return ShardReceipt(shard_id, tenant, FAILED, error=f"empty: {e}")
    except Exception as e:  # noqa: BLE001 — isolate the shard; the engine retries
        return ShardReceipt(shard_id, tenant, FAILED, error=f"{type(e).__name__}: {e}")

    n_docs = len({c.doc_id for c in chunks})
    try:
        write_embedding_file(out_path, chunks, tenant=tenant)
    except Exception as e:  # noqa: BLE001 — a write/serialization fault fails the shard
        return ShardReceipt(shard_id, tenant, FAILED, n_docs=n_docs,
                            n_chunks=len(chunks), error=f"write: {type(e).__name__}: {e}")

    return ShardReceipt(shard_id, tenant, COMPLETED, n_docs=n_docs,
                        n_chunks=len(chunks), chunk_ids=[c.id for c in chunks],
                        embedding_file=str(out_path))
