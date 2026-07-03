"""Atomic single-shard ingest (ADR-0001 step 2).

``run_shard`` ingests one shard through an ``IngestionPipeline`` and returns a
``ShardReceipt``. It lives in the package (not the CLI script) so it is reusable
by the ``ingest_shard`` CLI, a future ``GoWeBackend``, and unit tests — and it
adds **no** state: the caller/engine owns retry and resume, and a re-run
overwrites in place (deterministic ids + upsert), so it's safe to retry.
"""
from __future__ import annotations

from ragstack.ingestion.pipeline import EmptyIngestError, IngestionPipeline
from ragstack.ingestion.receipts import COMPLETED, FAILED, DocRow, ShardReceipt


async def run_shard(
    pipeline: IngestionPipeline, shard_path: str, tenant: str, shard_id: str
) -> ShardReceipt:
    """Ingest one shard through ``pipeline`` and return its receipt.

    Never raises: a load/ingest failure is captured as ``status=failed`` with a
    caller-safe error, so a scattered task fails just its own shard (the workflow
    retries it) rather than aborting the run. The per-doc catalog comes from the
    pipeline's own loader; ``chunk_ids`` from the ingest (a flat, shard-level list
    — the pipeline doesn't expose per-doc attribution).
    """
    try:
        docs = pipeline.loader.load(shard_path)
    except Exception as e:  # noqa: BLE001 — a bad/missing shard fails just itself
        return ShardReceipt(shard_id, tenant, FAILED, error=f"load: {type(e).__name__}: {e}")
    catalog = [DocRow(doc_id=d.id, source=d.source, metadata=dict(d.metadata)) for d in docs]
    try:
        chunk_ids = await pipeline.ingest(shard_path, tenant_id=tenant)
    except EmptyIngestError as e:
        return ShardReceipt(shard_id, tenant, FAILED, n_docs=len(docs), docs=catalog,
                            error=f"empty: {e}")
    except Exception as e:  # noqa: BLE001 — isolate the shard; the engine retries
        return ShardReceipt(shard_id, tenant, FAILED, n_docs=len(docs), docs=catalog,
                            error=f"{type(e).__name__}: {e}")
    return ShardReceipt(shard_id, tenant, COMPLETED, n_docs=len(docs),
                        n_chunks=len(chunk_ids), chunk_ids=chunk_ids, docs=catalog)
