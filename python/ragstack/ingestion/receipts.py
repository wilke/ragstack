"""Per-shard ingest receipts (ADR-0001 step 2, offline plane).

A bulk-ingest workflow step's *real* output is a database side effect — the
Qdrant/ES upsert. CWL is file-in/file-out, so each per-shard step emits a
**receipt** file recording what it produced (chunk ids + a per-doc catalog) so
the DAG has a gather-able, auditable artifact and downstream steps stay
file-based. The gather step (``merge_receipts``) folds the shard receipts into a
run summary. (This is the concrete form of the collection-agreement/receipts idea
in #62.)

Kept deliberately small and dependency-free (dataclasses + json) so it is a
stable contract shared by the CLI tool, the gather step, and — later — a
``GoWeBackend`` mapping receipts back to ``ItemResult``.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

COMPLETED = "completed"
FAILED = "failed"


#: The per-document error for a loaded document that produced no embeddable
#: chunk (empty after chunking/boilerplate, or every chunk quarantined). A
#: constant, caller-safe string — countable with ``GROUP BY error`` like
#: :data:`ragstack.ingestion.loaders.NO_TEXT_ERROR`.
NO_CHUNKS_ERROR = "no embeddable chunks (empty or all quarantined)"


@dataclass
class DocRow:
    """One catalog row: a document ingested (or attempted) in the shard.

    ``metadata`` is the document-level catalog subset already curated by the
    loader's enrichment (title/doc_type/doi/authors/year/…); chunk-level fields
    never reach here.

    Per-document status (#203 2b — a shard is a *batch* of documents, so the
    shard-level ``status`` no longer describes each one): ``error`` is empty for
    a document whose chunks were upserted and otherwise names why it was not —
    the extract stage's constant (``NO_TEXT_ERROR`` for a scanned PDF, carried
    verbatim from its report), :data:`NO_CHUNKS_ERROR`, or the batch-level
    failure every document of a failed shard inherits. ``chunk_ids`` are the
    ids upserted for THIS document (a subset of the shard's ``chunk_ids``), so
    a driver can attribute chunks per document rather than per batch.
    """

    doc_id: str
    source: str
    metadata: dict = field(default_factory=dict)
    chunk_ids: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass
class ShardReceipt:
    """What one ``ingest_shard`` invocation produced.

    ``chunk_ids`` is shard-level (every id upserted by this invocation); per
    document they are on ``docs[i].chunk_ids``. ``status`` is ``completed``
    when AT LEAST ONE document was upserted and ``failed`` only when every
    document failed (or the shard could not be loaded/indexed at all) — the
    batch rule of #203 2b, so one scanned PDF cannot sink a batch. A partial
    per-chunk quarantine still completes (the pipeline drops poison chunks and
    upserts the rest), matching the ingest pipeline's own degrade behaviour.
    ``n_docs`` counts every document attempted (loaded + reported-skipped);
    ``n_docs_failed`` those with a per-document ``error``.
    """

    shard_id: str
    tenant: str
    status: str
    n_docs: int = 0
    n_chunks: int = 0
    chunk_ids: list[str] = field(default_factory=list)
    docs: list[DocRow] = field(default_factory=list)
    n_docs_failed: int = 0
    # Set by the embed stage (ADR-0001 offline plane, #141): the JSONL embedding
    # file this shard produced, for the downstream load stage. Empty for the
    # coupled ingest_shard path (which upserts directly).
    embedding_file: str = ""
    error: str = ""

    def to_json(self) -> str:
        # Deterministic (sorted, no timestamp) so a re-run against an unchanged
        # shard produces a byte-identical receipt — idempotent + diff-able.
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    def write(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def from_dict(cls, d: dict) -> ShardReceipt:
        missing = [k for k in ("shard_id", "status") if k not in d]
        if missing:
            raise ValueError(f"receipt missing required field(s): {missing}")
        # Tolerate unexpected/missing DocRow keys (forward/back-compat) rather than
        # TypeError/KeyError on a hand-edited catalog row.
        docs = [
            DocRow(doc_id=r.get("doc_id", ""), source=r.get("source", ""),
                   metadata=r.get("metadata", {}) or {},
                   chunk_ids=list(r.get("chunk_ids", []) or []),
                   error=str(r.get("error", "") or ""))
            for r in d.get("docs", [])
        ]
        return cls(
            shard_id=d["shard_id"],
            tenant=d.get("tenant", ""),
            status=d["status"],
            n_docs=int(d.get("n_docs", 0)),
            n_chunks=int(d.get("n_chunks", 0)),
            chunk_ids=list(d.get("chunk_ids", [])),
            docs=docs,
            n_docs_failed=int(d.get("n_docs_failed", 0)),
            embedding_file=d.get("embedding_file", ""),
            error=d.get("error", ""),
        )

    @classmethod
    def load(cls, path: str | Path) -> ShardReceipt:
        """Load + validate a receipt, attributing any error to the file (so the
        gather step reports ``<path>: invalid receipt`` cleanly, not a raw
        traceback on one corrupt file)."""
        try:
            return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
        except (ValueError, TypeError, json.JSONDecodeError) as e:
            raise ValueError(f"{path}: invalid receipt: {e}") from e


def merge_summary(receipts: list[ShardReceipt]) -> dict:
    """Fold shard receipts into a run summary (the gather step's core).

    Reports totals and — crucially — the ids of any failed shards, so a bulk run
    surfaces partial failure instead of silently under-ingesting.
    """
    failed = [r.shard_id for r in receipts if r.status != COMPLETED]
    return {
        "n_shards": len(receipts),
        "n_shards_failed": len(failed),
        "n_docs": sum(r.n_docs for r in receipts),
        "n_docs_failed": sum(r.n_docs_failed for r in receipts),
        "n_chunks": sum(r.n_chunks for r in receipts),
        "failed_shards": sorted(failed),
        "errors": {r.shard_id: r.error for r in receipts if r.error},
    }
