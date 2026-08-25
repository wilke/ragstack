"""Atomic single-shard ingest (ADR-0001 step 2) — a shard is a BATCH of documents.

``run_shard`` ingests one shard through an ``IngestionPipeline`` and returns a
``ShardReceipt``. It lives in the package (not the CLI script) so it is reusable
by the ``ingest_shard`` CLI, the ``GoWeBackend`` mapping, and unit tests — and it
adds **no** state: the caller/engine owns retry and resume.

Per-document outcome (#203 2b, Option B). Under batch-per-task one shard holds
N documents, so the receipt reports each one: ``docs[i].error`` is empty for a
document whose chunks were upserted, otherwise names why it was not, and
``docs[i].chunk_ids`` are that document's ids. Three failure classes:

* **extract-stage failures** — a scanned PDF (``NO_TEXT_ERROR``), an unreadable
  or non-PDF file — never reach the shard; the extract tool's *report* lists
  them and ``run_shard`` folds them in as failed rows, carrying the report's
  constant ``error`` verbatim so a job can ``GROUP BY error`` over them;
* **per-document ingest failures** — a loaded document that produced no
  embeddable chunk (:data:`~ragstack.ingestion.receipts.NO_CHUNKS_ERROR`);
* **batch-level failures** — the shard could not be loaded, the embedder
  raised (an infra 5xx), or the upsert failed: the receipt is ``failed`` and
  EVERY document without a more specific error inherits the batch error.

``status`` is the TASK outcome, not a document count: ``completed`` whenever
the batch was processed to the end — even when EVERY document failed (all
scanned PDFs: ``n_docs_failed == n_docs``, every row errored, a header-only
embedding file) — and ``failed`` only for a batch-level error. The CLI's exit
code follows ``status``, and that is deliberate: GoWe treats any non-zero exit
as a task failure, retries it (``MaxRetries``), then fails the step, its
dependants and the submission — while the OTHER batches of the run have already
upserted into the stores (this tool is coupled embed+load). An exit 1 for a
batch of scanned PDFs would therefore leave the stores and the archive
diverged (no ``versions/N/``, and a later restore silently omits those
documents) — the very failure class Option B exists to remove. So per-document
failure is DATA in the receipt, never a task failure.

Known residual (#357 format): when every batch of a run is all-failed there are
zero rows to pack, ``archive.write_version`` refuses a zero-row version and the
run fails with no per-item detail — a format decision, not a batch-rule one.

Retry safety: a re-run against a working endpoint overwrites in place
(deterministic ids + upsert of the same content), so an engine retry is safe. The
one caveat is a document that flips from embeddable to *fully* unembeddable
between runs (every chunk 4xx-quarantined): ``IngestionPipeline.index_chunks``
delete-priors only documents with a survivor, so that document keeps its prior
data (surfaced as ``NO_CHUNKS_ERROR`` on its row). An infra (5xx) failure raises
before the delete, preserving prior data and reporting ``failed``.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from ragstack.ingestion.chunk_cap import ChunkCapExceeded, check_chunk_cap
from ragstack.ingestion.embedding_file import write_embedding_file
from ragstack.ingestion.loaders import deterministic_doc_id
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.ingestion.receipts import (
    COMPLETED,
    FAILED,
    NO_CHUNKS_ERROR,
    DocRow,
    ShardReceipt,
)

log = logging.getLogger(__name__)

#: The per-document error for a file the extract report lists as an input but
#: that is neither in the shard nor in its ``skipped`` list.
NOT_EXTRACTED_ERROR = "missing from the extract stage's output"


@dataclass
class ExtractReport:
    """What the extract stage (``pdf_extract.py --report``) reports for a batch:
    every input it attempted and the ones it skipped, each with the constant
    per-item ``error`` (``NO_TEXT_ERROR`` for a scanned PDF). Lets the ingest
    task account for the whole batch even when the shard is empty."""

    inputs: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (source, error)

    @classmethod
    def from_dict(cls, d: dict) -> ExtractReport:
        skipped: list[tuple[str, str]] = []
        for row in d.get("skipped") or []:
            if not isinstance(row, dict):
                continue
            source = str(row.get("path") or "")
            # ``error`` is the constant job error the loader assigned; older
            # reports carry only the human ``reason``.
            error = str(row.get("error") or row.get("reason") or "skipped by extract")
            if source:
                skipped.append((source, error))
        inputs = [str(p) for p in (d.get("inputs") or []) if p]
        return cls(inputs=inputs, skipped=skipped)

    @classmethod
    def load(cls, path: str | Path) -> ExtractReport:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ValueError(f"{path}: unreadable extract report: {e}") from e
        if not isinstance(data, dict):
            raise ValueError(f"{path}: extract report is not a JSON object")
        return cls.from_dict(data)


async def run_shard(
    pipeline: IngestionPipeline,
    shard_path: str,
    tenant: str,
    shard_id: str,
    embedding_file: str | Path | None = None,
    report: ExtractReport | None = None,
    max_chunks: int = 0,
) -> ShardReceipt:
    """Ingest one shard (a batch of documents) through ``pipeline`` and return
    its receipt — see the module docstring for the per-document rules.

    ``max_chunks`` (#291): the collection's chunk cap, ``0`` = unlimited. The
    worker-side enforcement point of the per-collection cap for the GoWe
    scatter path (``ingest_shard --max-chunks``, the ``max_chunks`` workflow
    input the API derives per job): after the embed and BEFORE the first
    write — the embedding file included — one live ``vector_store.count()``
    decides; a batch that would push the collection over the cap is refused
    WHOLE: a ``failed`` receipt (a batch-level error, every row inheriting it)
    whose ``error`` is ``chunk_cap_exceeded: live=.. incoming=.. cap=..
    would_fit=..`` — the one per-document failure class that IS a task
    failure by design (a cap refusal is a whole-job refusal by spec): the CLI
    exits 4 on it and the API classifies that exit onto the job. Each
    scattered task checks its own batch against the live count, so under a
    scatter the cap is enforced per task (concurrent tasks may collectively
    overshoot by the other tasks' batches); the API/local path sizes the
    whole job.

    ``embedding_file`` (#357): also write the embedded chunks to that path in the
    ``ragstack.embedding_file/v1`` format *between* the two halves of the
    ingest — ``embed_documents`` -> file -> ``index_chunks`` is the literal
    decomposition of ``pipeline.ingest``, so the receipt still reports what was
    actually upserted, and the archive step of the scatter workflow gets the
    same input the decoupled embed stage would have produced. The file holds
    ONLY the successful documents' chunks. ``None`` keeps the coupled
    behaviour. A file from a failed batch is removed (retry starts clean).

    ``report`` folds the extract stage's skipped files into the receipt as
    failed rows (their constant errors verbatim) and lets an empty shard — a
    batch of nothing but scanned PDFs — be reported as N failed documents
    rather than an unreadable shard.

    Does not raise for ordinary failures: a load, embed or index error is
    captured as ``status=failed`` with a caller-safe message on every document,
    so a scattered task fails just its own batch (the engine retries it) rather
    than aborting the run. (An ``asyncio.CancelledError`` — a ``BaseException``
    — deliberately propagates, so the engine's own cancellation/timeout is
    honoured.)
    """
    rows = _rows_from_report(report)

    def failed_batch(error: str) -> ShardReceipt:
        _discard(embedding_file)
        return _receipt(shard_id, tenant, rows, [], batch_error=error)

    def processed_empty() -> ShardReceipt:
        # Processed to the end with nothing to upsert: every row carries its
        # own error, the task succeeds, and the archive step still gets a
        # (header-only) embedding file for this batch.
        if embedding_file is not None:
            write_embedding_file(embedding_file, [], tenant=tenant)
        return _receipt(shard_id, tenant, rows, [],
                        embedding_file=str(embedding_file) if embedding_file else "")

    try:
        docs = pipeline.loader.load(shard_path)
    except Exception as e:  # noqa: BLE001 — a bad/missing shard fails just itself
        # An empty shard is legitimate when the extract stage skipped every
        # file of the batch: those documents failed for their own reasons.
        if rows and Path(shard_path).is_file() and _is_empty_shard(shard_path):
            docs = []
        else:
            return failed_batch(f"load: {type(e).__name__}: {e}")
    loaded = [DocRow(doc_id=d.id, source=d.source, metadata=dict(d.metadata)) for d in docs]
    rows = _merge_rows(loaded, rows)
    if not docs:
        # Every document failed at the extract stage, each for its own reason
        # (the rows carry them): a processed batch, not a failed task.
        return processed_empty()

    try:
        kept, produced, quarantined = await pipeline.embed_documents(
            docs, tenant_id=tenant, source=shard_path
        )
    except Exception as e:  # noqa: BLE001 — isolate the batch; the engine retries
        return failed_batch(f"{type(e).__name__}: {e}")
    ids_by_doc: dict[str, list[str]] = {}
    for c in kept:
        ids_by_doc.setdefault(c.doc_id, []).append(c.id)
    for row in rows:
        if row.error:
            continue
        row.chunk_ids = ids_by_doc.get(row.doc_id, [])
        if not row.chunk_ids:
            row.error = NO_CHUNKS_ERROR
    if not kept:
        log.warning("shard %s: no embeddable chunks in the batch (produced %d, "
                    "quarantined %d); every row reports its own error",
                    shard_id, produced, quarantined)
        return processed_empty()

    try:
        if max_chunks > 0:
            # The cap gate (#291): one count, before the file and before the stores.
            await check_chunk_cap(pipeline.vector_store, len(kept), max_chunks)
        if embedding_file is not None:
            write_embedding_file(embedding_file, kept, tenant=tenant)
        chunk_ids = await pipeline.index_chunks(kept, tenant_id=tenant)
    except ChunkCapExceeded as e:
        # Nothing written (the check precedes the file): the whole batch is
        # refused, the labelled refusal with its four numbers on the receipt.
        for row in rows:
            row.chunk_ids = []
        return failed_batch(str(e))
    except Exception as e:  # noqa: BLE001 — isolate the batch; the engine retries
        for row in rows:
            row.chunk_ids = []
        return failed_batch(f"{type(e).__name__}: {e}")
    return _receipt(shard_id, tenant, rows, chunk_ids,
                    embedding_file=str(embedding_file) if embedding_file else "")


def _rows_from_report(report: ExtractReport | None) -> list[DocRow]:
    """Failed rows for the extract stage's skipped files, then a placeholder row
    for every reported input the shard will have to account for."""
    if report is None:
        return []
    rows: list[DocRow] = []
    seen: set[str] = set()
    for source, error in report.skipped:
        if source in seen:
            continue
        seen.add(source)
        rows.append(DocRow(doc_id=_doc_id(source), source=source, error=error))
    for source in report.inputs:
        if source in seen:
            continue
        seen.add(source)
        rows.append(DocRow(doc_id=_doc_id(source), source=source, error=NOT_EXTRACTED_ERROR))
    return rows


def _merge_rows(loaded: list[DocRow], reported: list[DocRow]) -> list[DocRow]:
    """Loaded documents first (shard order), then the reported rows the shard
    did not deliver. A reported input the shard DID deliver is the loaded row."""
    delivered = {r.source for r in loaded} | {r.doc_id for r in loaded}
    rest = [r for r in reported
            if r.source not in delivered and _doc_id(r.source) not in delivered]
    return [*loaded, *rest]


def _receipt(
    shard_id: str,
    tenant: str,
    rows: list[DocRow],
    chunk_ids: list[str],
    *,
    batch_error: str = "",
    embedding_file: str = "",
) -> ShardReceipt:
    if batch_error:
        # Attribute the batch failure to every document that has no more
        # specific error of its own (a skipped scanned PDF keeps NO_TEXT_ERROR).
        for row in rows:
            if not row.error:
                row.error = batch_error
    failed = sum(1 for r in rows if r.error)
    # The task outcome: failed only for a batch-level error (see the module
    # docstring) — an all-failed batch that was processed to the end completes.
    status = FAILED if batch_error else COMPLETED
    return ShardReceipt(
        shard_id, tenant, status, n_docs=len(rows), n_chunks=len(chunk_ids),
        chunk_ids=list(chunk_ids), docs=rows, n_docs_failed=failed,
        embedding_file=embedding_file if status == COMPLETED else "",
        error=batch_error,
    )


def _doc_id(source: str) -> str:
    # The PdfLoader keys a document by its resolved path; the extract tool
    # already hands out resolved paths, so no further resolution here (a
    # missing file must still get a stable id).
    return deterministic_doc_id(source)


def _is_empty_shard(path: str) -> bool:
    try:
        with open(path, encoding="utf-8") as fh:
            return not any(line.strip() for line in fh)
    except OSError:
        return False


def _discard(embedding_file: str | Path | None) -> None:
    if embedding_file is not None:
        Path(embedding_file).unlink(missing_ok=True)
