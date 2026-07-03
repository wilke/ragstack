"""Atomic single-shard ingest (ADR-0001 step 2).

``run_shard`` ingests one shard through an ``IngestionPipeline`` and returns a
``ShardReceipt``. It lives in the package (not the CLI script) so it is reusable
by the ``ingest_shard`` CLI, a future ``GoWeBackend``, and unit tests — and it
adds **no** state: the caller/engine owns retry and resume.

Retry safety: a re-run against a working endpoint overwrites in place
(deterministic ids + upsert of the same content), so an engine retry is safe. The
one caveat is a document that flips from embeddable to *fully* unembeddable
between runs (every chunk 4xx-quarantined): ``IngestionPipeline.ingest`` deletes
each loaded doc's prior chunks before upserting the survivors, so that doc's prior
data is dropped and the shard still reports ``completed`` (per-doc atomicity is a
pipeline-level gap tracked separately). An infra (5xx) failure raises before the
delete, preserving prior data and reporting ``failed``.
"""
from __future__ import annotations

from ragstack.ingestion.pipeline import EmptyIngestError, IngestionPipeline
from ragstack.ingestion.receipts import COMPLETED, FAILED, DocRow, ShardReceipt


async def run_shard(
    pipeline: IngestionPipeline, shard_path: str, tenant: str, shard_id: str
) -> ShardReceipt:
    """Ingest one shard through ``pipeline`` and return its receipt.

    Does not raise for ordinary failures: a load or ingest error is captured as
    ``status=failed`` with a caller-safe message, so a scattered task fails just
    its own shard (the engine retries it) rather than aborting the run. (An
    ``asyncio.CancelledError`` — a ``BaseException`` — deliberately propagates, so
    the engine's own cancellation/timeout is honoured.) The per-doc catalog comes
    from the pipeline's loader; ``chunk_ids`` from the ingest (a flat, shard-level
    list — the pipeline doesn't expose per-doc attribution).
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
