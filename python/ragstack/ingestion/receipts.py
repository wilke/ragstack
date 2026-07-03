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


@dataclass
class DocRow:
    """One catalog row: a document ingested (or attempted) in the shard.

    ``metadata`` is the document-level catalog subset already curated by the
    loader's enrichment (title/doc_type/doi/authors/year/…); chunk-level fields
    never reach here.
    """

    doc_id: str
    source: str
    metadata: dict = field(default_factory=dict)


@dataclass
class ShardReceipt:
    """What one ``ingest_shard`` invocation produced.

    ``chunk_ids`` is shard-level (the pipeline returns a flat list; per-doc
    attribution isn't exposed). ``status`` is ``completed`` unless the whole shard
    failed to load/ingest — a partial per-chunk quarantine still completes (the
    pipeline drops poison chunks and upserts the rest), matching the ingest
    pipeline's own degrade behaviour.
    """

    shard_id: str
    tenant: str
    status: str
    n_docs: int = 0
    n_chunks: int = 0
    chunk_ids: list[str] = field(default_factory=list)
    docs: list[DocRow] = field(default_factory=list)
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
                   metadata=r.get("metadata", {}) or {})
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
        "n_chunks": sum(r.n_chunks for r in receipts),
        "failed_shards": sorted(failed),
        "errors": {r.shard_id: r.error for r in receipts if r.error},
    }
