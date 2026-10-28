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
from collections import Counter
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
    document they are on ``docs[i].chunk_ids``. ``status`` is the TASK outcome
    (#203 2b): ``completed`` whenever the batch was processed to the end —
    including a batch in which EVERY document failed (``n_docs_failed ==
    n_docs``, each row errored) — and ``failed`` only when the batch itself
    could not be loaded/embedded/indexed (``error`` set). Per-document failure
    is data in the rows, never a task failure, because a failed task would
    fail the whole run after its sibling batches had already upserted. A
    partial per-chunk quarantine still completes (the pipeline drops poison
    chunks and upserts the rest), matching the ingest pipeline's own degrade
    behaviour. ``n_docs`` counts every document attempted (loaded +
    reported-skipped); ``n_docs_failed`` those with a per-document ``error``.
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
    # ADR-0010 decision 8 (#655 step 2): which GoWe workflow and which tools
    # image produced this receipt — the object ``ragstack.provenance.
    # tool_provenance`` builds (workflow_id / tool_image / tool_image_digest
    # from the submission inputs, image_version / image_commit / image_build
    # from the worker's own RELEASE file, and — since #318 P2 —
    # segmenter_version, chunkers.segmenter_version() of the worker — the
    # backend actually in use, punkt-abbrev-v1 or regex-abbrev-v1 (#815) —
    # whose sentence boundaries this shard's sentence/semantic chunk ids
    # derive from; a receipt without it predates the key). ``None`` on a receipt written
    # before step 2 or by a hand-run tool given no inputs; readers normalise
    # it with ``ragstack.provenance.read_provenance``.
    provenance: dict | None = None
    # ADR-0011: post-chunking enrichment steps this shard ran, with version and
    # config — today ``{"symbol_glossary": {"extractor": "rules/1", ...}}``
    # (#754). ``None`` = none ran; then the key is omitted from the JSON, so a
    # receipt from a run without enrichment is byte-identical to one written
    # before the field existed. INTERIM: folds into ADR-0011's
    # ``core.activity.params`` when ADR-0011 PR-3 lands.
    enrichment: dict | None = None
    # What a SEMANTIC chunker applied on this shard
    # (``chunkers.semantic_chunking_provenance``): ``{"v": 2, "method",
    # "size_cap", "chunk_size", "cap_split", "breakpoint_cap", "window_budget",
    # "breakpoint_input_budget", "size_tolerance", "min_piece", "ceiling"}`` —
    # ``size_cap: null`` records the legacy, window-only chunking; the three
    # #831 tolerance keys are null on a strict (or uncapped) collection, and a
    # v1 object (an image before #831) has none of them and was strict. ``None`` = not recorded: another method, or a receipt
    # written before the key existed (a worker image without the size cap); then
    # the key is omitted from the JSON, like ``enrichment``. ``v`` versions the
    # object's own keys.
    chunking: dict | None = None

    def to_json(self) -> str:
        # Deterministic (sorted, no timestamp) so a re-run against an unchanged
        # shard produces a byte-identical receipt — idempotent + diff-able.
        data = asdict(self)
        for optional in ("enrichment", "chunking"):
            if data.get(optional) is None:
                data.pop(optional, None)
        return json.dumps(data, indent=2, sort_keys=True)

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
            provenance=(dict(d["provenance"]) if isinstance(d.get("provenance"), dict)
                        else None),
            enrichment=(dict(d["enrichment"]) if isinstance(d.get("enrichment"), dict)
                        else None),
            chunking=(dict(d["chunking"]) if isinstance(d.get("chunking"), dict)
                      else None),
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


# --------------------------------------------------------------------------- #
# Nothing to archive (#768): a run in which NO document was ingested.
# --------------------------------------------------------------------------- #

#: Exit code of the archive step (``scripts/archive_version.py``) when every
#: document of the run failed on its own — all refused as duplicates, all
#: scanned PDFs — so there is no chunk to pack and ``ragstack-archive/1``
#: refuses a zero-row version (the #357 residual). Distinct from the generic
#: exit 1 so the API can classify the engine's failure record DETERMINISTICALLY
#: (``gowe_backend.nothing_to_archive_of``) and lift each document's own reason
#: onto its job item instead of ``gowe submission FAILED``. Mirrors the chunk
#: cap's ``CAP_REFUSED_EXIT_CODE = 4``.
NOTHING_TO_ARCHIVE_EXIT_CODE = 5
#: The label line the archive step prints first on stderr in that case.
NOTHING_TO_ARCHIVE = "nothing_to_archive"
#: GoWe copies the failed task's stderr into the submission's error record
#: truncated to its first 1000 characters, and the worker PREPENDS its own
#: ``execute: apptainer command failed (exit 5): `` (~44 characters) inside
#: that window. Stay under what is left so no listed line is cut, and stop
#: listing documents (whole lines only) when the budget is spent.
NOTHING_TO_ARCHIVE_STDERR_BUDGET = 880
_MAX_LISTED_ERROR = 400


def format_nothing_to_archive(receipts: list[ShardReceipt]) -> str:
    """The archive step's stderr for a run with nothing to archive: a label
    line ``nothing_to_archive: docs=N failed=F listed=L``, then one compact
    JSON object per failed document, ``{"doc": <source basename>, "error":
    <its row error>}``, in receipt order, within
    :data:`NOTHING_TO_ARCHIVE_STDERR_BUDGET`. The basename is what the API
    matches a work item on (the engine pre-stages each input under it)."""
    rows = [row for r in receipts for row in r.docs]
    failed = [row for row in rows if row.error]
    lines: list[str] = []
    used = 0
    for row in failed:
        name = row.source.rstrip("/").rsplit("/", 1)[-1] if row.source else ""
        if not name:
            continue
        error = " ".join(row.error.split())[:_MAX_LISTED_ERROR]
        line = json.dumps({"doc": name, "error": error}, ensure_ascii=False,
                          separators=(",", ":"))
        # +1 newline; the label line (< 80 chars) is reserved below.
        if used + len(line) + 1 > NOTHING_TO_ARCHIVE_STDERR_BUDGET - 80:
            break
        lines.append(line)
        used += len(line) + 1
    label = (f"{NOTHING_TO_ARCHIVE}: docs={len(rows)} failed={len(failed)} "
             f"listed={len(lines)}")
    return "\n".join([label, *lines])


def parse_nothing_to_archive(text: str) -> dict[str, str]:
    """``{source basename: error}`` from :func:`format_nothing_to_archive`'s
    output (or a truncated copy of it): every line that parses as such an
    object; anything else — the label, a cut-off last line — is ignored."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        doc, error = obj.get("doc"), obj.get("error")
        if isinstance(doc, str) and doc and isinstance(error, str) and error:
            out.setdefault(doc, error)
    return out


#: The remedy every #852 refusal names. The cause it guards against: a
#: scatter whose tasks all write ``--out receipt.json`` and glob
#: ``receipt.json``, under an engine that stages a gathered ``File[]`` flat by
#: basename (GoWe), hands the gather step N paths onto ONE surviving file.
DUPLICATE_RECEIPT_REMEDY = (
    "name each scatter task's receipt uniquely, e.g. "
    "`--out $(inputs.shard.nameroot).receipt.json` with the matching glob (#852)")


class DuplicateReceiptError(ValueError):
    """Several receipts that are one receipt: paths resolving to one file, or
    two receipts naming the same ``shard_id`` (#852). Counting them would
    report one task's documents N times and every other task's as missing."""


def check_distinct_receipt_paths(paths: list[str] | list[Path], *, what: str = "receipts") -> None:
    """Refuse receipt ``paths`` of which two resolve to the same file.

    cwltool stages colliding basenames into separate directories, so distinct
    receipts arrive as distinct paths and pass; GoWe stages them onto one path,
    which this refuses instead of arraying one receipt N times."""
    seen: dict[Path, str] = {}
    for p in paths:
        key = Path(p).resolve()
        if key in seen:
            raise DuplicateReceiptError(
                f"{what} staged onto one file: {seen[key]!r} and {str(p)!r} both "
                f"resolve to {str(key)!r} ({len(paths)} path(s) given); "
                f"{DUPLICATE_RECEIPT_REMEDY}")
        seen[key] = str(p)


def check_distinct_shard_ids(shard_ids: list[str]) -> None:
    """Refuse two receipts with the same ``shard_id`` (#852)."""
    dups = sorted(s for s, n in Counter(shard_ids).items() if n > 1)
    if dups:
        raise DuplicateReceiptError(
            f"duplicate receipt shard_id(s) {dups} among {len(shard_ids)} receipt(s): "
            f"the same receipt counted more than once; {DUPLICATE_RECEIPT_REMEDY}")


def merge_summary(receipts: list[ShardReceipt]) -> dict:
    """Fold shard receipts into a run summary (the gather step's core).

    Reports totals and — crucially — the ids of any failed shards, so a bulk run
    surfaces partial failure instead of silently under-ingesting. Raises
    :class:`DuplicateReceiptError` when two receipts share a ``shard_id``
    (#852): one receipt staged N times would otherwise be counted N times.
    """
    check_distinct_shard_ids([r.shard_id for r in receipts])
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
