#!/usr/bin/env python
"""Document -> JSONL text-extraction tool for the CWL/GoWe ingest plane (#202/#203, #828).

The name is historical: this is the ``extract`` step of ``cwl/pdf-extract.cwl``
and ``cwl/pdf-ingest-scatter.cwl`` and it handles **every staged file**, not
only PDFs. Each file is loaded through the SAME suffix -> loader map the
in-process API path uses (:func:`ragstack.ingestion.loaders.default_loader_registry`,
asked via :meth:`~ragstack.ingestion.loaders.LoaderRegistry.loader_for`), so one
file gives one text on both ingest paths:

* ``.pdf`` -> :class:`~ragstack.ingestion.loaders.PdfLoader` (PyMuPDF);
* ``.md`` / ``.txt`` -> :class:`~ragstack.ingestion.loaders.TextFileLoader`
  (the raw file text, byte for byte what the in-process path stores);
* ``.jsonl`` -> *skipped* with ``JSONL_UNSUPPORTED_ERROR``: a batch file is
  many documents behind one receipt item (use the bulk JSONL workflows);
* any other suffix -> *skipped* with the constant ``no loader for <suffix>``
  error, the string the in-process upload path records for it.

Before #828 every file went to ``PdfLoader``. PyMuPDF 2.x *opens* Markdown and
returns a rendering of it — link targets, inline HTML and some code dropped —
so a GoWe-built Markdown collection held different text (and different chunks)
than the same files ingested in-process.

The output is a **JSONL shard** in the exact ``{"text", "path", "metadata"}``
shape that ``scripts/ingest_shard.py`` / ``scripts/embed_shard.py`` consume via
:class:`ragstack.ingestion.loaders.JsonlLoader`.

**No OCR.** A scanned / image-only PDF, an unreadable file, a file with an
unsupported suffix, or a text file with no text yields no record and is recorded
as *skipped* in a sidecar JSON report — it never crashes the job, so one bad
file can't sink a large batch. This mirrors how ``JsonlLoader`` skips (rather
than errors on) bad lines. Each skipped entry carries a human ``reason`` and a
constant, countable ``error`` — the loader's ``job_error`` (``NO_TEXT_ERROR``
for a scanned PDF), ``no loader for <suffix>``, ``EMPTY_TEXT_ERROR``,
``JSONL_UNSUPPORTED_ERROR``, or the exception class name —
and the report lists every attempted ``inputs`` path, so the ingest step
(``ingest_shard.py --extract-report``) can account for the whole batch per
document (#203 2b).

**Deterministic.** Inputs are expanded and sorted, so the same directory always
produces the same shard in the same order. The reusable core
(``extract_documents``) is pure and unit-tested offline; ``main`` only does I/O.

Usage::

    python scripts/pdf_extract.py /rag/data/g1-corpus/pdfs \
        --out shard.s0.jsonl --report shard.s0.report.json

    # explicit file list also works, mixed with directories; files of any
    # suffix are dispatched by suffix (a directory contributes its PDFs only):
    python scripts/pdf_extract.py a.pdf notes.md b.txt some/dir --out shard.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ragstack.ingestion.loaders import (
    EMPTY_TEXT_ERROR,
    JSONL_UNSUPPORTED_ERROR,
    LoaderError,
    LoaderRegistry,
    default_loader_registry,
    no_loader_error,
)


def iter_pdf_paths(inputs: list[str], recursive: bool = False) -> list[Path]:
    """Expand ``inputs`` (files and/or directories) to a sorted list of paths.

    A directory contributes its ``*.pdf`` files (``**/*.pdf`` when ``recursive``)
    — the PDF-corpus operator use this tool started with; it does NOT pick up a
    directory's ``.md``/``.txt`` (unlike the in-process directory ingest). A file
    is taken as-is regardless of extension and dispatched by its suffix in
    :func:`extract_documents` — this is how the GoWe workflows call it: every
    staged file is passed explicitly. The result is de-duplicated by resolved
    path and sorted for determinism.
    """
    seen: dict[Path, None] = {}
    for raw in inputs:
        p = Path(raw)
        if p.is_dir():
            pattern = "**/*.pdf" if recursive else "*.pdf"
            for f in p.glob(pattern):
                if f.is_file():
                    seen[f.resolve()] = None
        else:
            # A non-directory input is used verbatim (existence is checked at
            # load time so a missing path is reported as skipped, not a crash).
            seen[p.resolve()] = None
    return sorted(seen)


def _skip(source: str, reason: str, error: str) -> dict:
    return {"path": source, "reason": reason, "error": error}


def extract_documents(
    paths: list[Path], registry: LoaderRegistry | None = None
) -> tuple[list[dict], list[dict]]:
    """Extract text from ``paths`` -> (records, skipped).

    Each path is dispatched by suffix through ``registry`` — by default
    :func:`~ragstack.ingestion.loaders.default_loader_registry`, the map the
    in-process API path loads through — so a ``.md`` file yields exactly the
    ``Document.content`` the in-process path stores (#828). A suffix with no
    registration is skipped with ``no loader for <suffix>`` rather than falling
    back to the registry's default loader: that is what the in-process upload
    path records for it (``documents._fail_unsupported_items``), and uploads are
    what this tool mostly sees.

    ``records`` are JSONL-ready ``{"text", "path", "metadata"}`` dicts (the shape
    ``JsonlLoader`` consumes). ``skipped`` are ``{"path", "reason", "error"}``
    dicts for files with no extractable text or that could not be opened
    (scanned/image-only PDFs, empty files, unsupported suffixes): ``reason`` is
    the human message, ``error`` a constant per-item job error (a
    ``LoaderError`` subclass's ``job_error`` — ``NO_TEXT_ERROR`` for a scanned
    PDF — ``no loader for <suffix>``, ``EMPTY_TEXT_ERROR``,
    ``JSONL_UNSUPPORTED_ERROR``, else the exception's class name), which the
    receipt carries verbatim so a job can ``GROUP BY`` it.
    Pure/deterministic — no filesystem writes.
    """
    registry = registry if registry is not None else default_loader_registry()
    records: list[dict] = []
    skipped: list[dict] = []
    for path in paths:
        # Resolve first: ``loader_for`` reads the suffix of the path as given,
        # ``registry.load`` dispatches on the resolved one — resolving here makes
        # them the same path (a symlink to another suffix cannot split them).
        source = str(Path(path).resolve())
        if Path(source).suffix.lower() == ".jsonl":
            # A batch file is many documents behind one receipt item: it cannot
            # be accounted for per item here, and path-less records would share
            # the shard file's id. Refused, not half-ingested (#830 review).
            skipped.append(_skip(source, JSONL_UNSUPPORTED_ERROR, JSONL_UNSUPPORTED_ERROR))
            continue
        if registry.loader_for(source) is None:
            err = no_loader_error(Path(source).suffix)
            skipped.append(_skip(source, err, err))
            continue
        try:
            docs = registry.load(source)
        except LoaderError as e:
            # Typed, caller-safe loader failure (no text / unreadable / not found).
            skipped.append(_skip(source, str(e), getattr(e, "job_error", None) or str(e)))
            continue
        except Exception as e:  # noqa: BLE001 - one bad file must not sink the batch
            # error: the class name, the label the in-process path records
            # (ingestion/sharded.py) — constant, so countable; reason: the detail.
            skipped.append(_skip(source, f"{type(e).__name__}: {e}", type(e).__name__))
            continue
        kept = [doc for doc in docs if doc.content.strip()]
        if not kept:
            skipped.append(_skip(source, f"{EMPTY_TEXT_ERROR}: {Path(source).name}",
                                 EMPTY_TEXT_ERROR))
            continue
        for doc in kept:
            records.append(
                {
                    "text": doc.content,
                    "path": doc.source,
                    "metadata": dict(doc.metadata),
                }
            )
    return records, skipped


#: The pre-#828 name, kept for callers that import it. Not PDF-only any more.
extract_pdfs = extract_documents


def write_jsonl(records: list[dict], out: str) -> None:
    with open(out, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False))
            fh.write("\n")


def build_report(
    records: list[dict], skipped: list[dict], out: str, inputs: list[Path] | None = None
) -> dict:
    """The sidecar report. ``inputs`` (every attempted path, in extraction
    order) lets the ingest step enumerate the batch even when the shard is
    empty — a batch of nothing but scanned PDFs is N failed documents, not an
    unreadable shard."""
    report = {
        "out": out,
        "n_input": len(records) + len(skipped),
        "n_extracted": len(records),
        "n_skipped": len(skipped),
        "skipped": skipped,
    }
    if inputs is not None:
        report["inputs"] = [str(p) for p in inputs]
    return report


def main(argv=None) -> int:
    args = parse_args(argv)
    paths = iter_pdf_paths(args.pdfs, recursive=args.recursive)
    records, skipped = extract_documents(paths)
    write_jsonl(records, args.out)
    report = build_report(records, skipped, args.out, inputs=paths)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, sort_keys=True)
    print(
        f"pdf_extract: extracted={report['n_extracted']} "
        f"skipped={report['n_skipped']} -> {args.out}"
        + (f" (report: {args.report})" if args.report else ""),
        flush=True,
    )
    # Emitting an empty shard when every input was skipped is a valid (if empty)
    # result, not a failure — the workflow decides whether zero docs is an error.
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "pdfs", nargs="+",
        help="files of any supported suffix (.pdf/.md/.txt, dispatched by "
             "suffix) and/or directories (which contribute their PDFs)",
    )
    p.add_argument("--out", default="shard.jsonl", help="output JSONL shard path")
    p.add_argument(
        "--report",
        default=None,
        help="output sidecar JSON report path (skipped files + counts)",
    )
    p.add_argument(
        "--recursive",
        action="store_true",
        help="recurse into subdirectories when an input is a directory",
    )
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
