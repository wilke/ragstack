"""Single embedding-file **load** stage (ADR-0001 offline plane, #141).

The store-bound half of the decoupled bulk pipeline: read one JSONL embedding
file (produced by :func:`ragstack.ingestion.embed_shard.run_embed_shard`) and
push it into Qdrant/ES by reusing :meth:`IngestionPipeline.index_chunks` — the
*exact* delete-prior → upsert → index → KG logic of the coupled pipeline, no
fork. The embed work is already done; this stage only writes to the stores.

**Backpressure is not here yet.** #141's must-have is throttling these upserts on
Qdrant's live health (status/optimizer/segments). That lands as a
``BackpressuredVectorStore`` decorator wrapping the pipeline's ``vector_store`` —
``index_chunks`` calls ``vector_store.upsert`` and the decorator throttles
transparently, so *this* module needs no change when it arrives. Until then the
load runs at full rate (fine on an uncapped Qdrant; the reason #141 is tracked).

Never-raises, mirroring :func:`ragstack.ingestion.shard.run_shard`: a read or
index error becomes a ``status=failed`` receipt for just this file. Idempotent —
deterministic ids + upsert-only + per-doc delete-prior — so re-loading a file (an
engine retry or resume) overwrites in place.
"""
from __future__ import annotations

from pathlib import Path

from ragstack.ingestion.embedding_file import EmbeddingFileError, read_embedding_file
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.ingestion.receipts import COMPLETED, FAILED, ShardReceipt


async def run_load_file(
    pipeline: IngestionPipeline,
    embedding_path: str | Path,
    file_id: str,
    tenant: str | None = None,
) -> ShardReceipt:
    """Load one embedding file into the stores via ``pipeline.index_chunks``.

    ``tenant`` defaults to the file header's tenant (embed_source stamped it on
    the chunks); pass it only to override. Returns a receipt whose
    ``embedding_file`` echoes the input and ``n_chunks`` is what was indexed.
    """
    try:
        chunks, header = read_embedding_file(embedding_path)
    except EmbeddingFileError as e:
        return ShardReceipt(file_id, tenant or "", FAILED, error=f"read: {e}")
    except Exception as e:  # noqa: BLE001 — a missing/corrupt file fails just itself
        return ShardReceipt(file_id, tenant or "", FAILED,
                            error=f"read: {type(e).__name__}: {e}")

    tenant = tenant or header.get("tenant", "") or ""
    n_docs = len({c.doc_id for c in chunks})
    # Re-stamp the resolved tenant onto every chunk before indexing. index_chunks
    # scopes its delete-prior by ``tenant_id=tenant``, but the upsert/index scope
    # each point/doc by ``chunk.metadata["tenant_id"]`` (via ``tenant_of``). For an
    # explicit ``--tenant`` override that differs from the embed-time tenant baked
    # into the file, those would disagree — the delete would clear the override
    # tenant (a no-op) while the write landed under the old tenant, orphaning prior
    # data and mis-scoping the load. Stamping here (as embed_source does) keeps
    # delete, upsert, and point-id tenant consistent. A no-op when tenant already
    # matches the header (the default, non-override path).
    for c in chunks:
        c.metadata["tenant_id"] = tenant
    try:
        chunk_ids = await pipeline.index_chunks(chunks, tenant_id=tenant)
    except Exception as e:  # noqa: BLE001 — isolate the file; the engine retries
        return ShardReceipt(file_id, tenant, FAILED, n_docs=n_docs, n_chunks=len(chunks),
                            embedding_file=str(embedding_path),
                            error=f"{type(e).__name__}: {e}")

    return ShardReceipt(file_id, tenant, COMPLETED, n_docs=n_docs,
                        n_chunks=len(chunk_ids), chunk_ids=chunk_ids,
                        embedding_file=str(embedding_path))
