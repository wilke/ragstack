#!/usr/bin/env python
"""PDF -> JSONL text-extraction tool for the CWL/GoWe PDF ingest plane (#202/#203).

Runs the real :class:`ragstack.ingestion.loaders.PdfLoader` (PyMuPDF) over a set
of PDFs and emits a **JSONL shard** in the exact ``{"text", "path", "metadata"}``
shape that ``scripts/ingest_shard.py`` / ``scripts/embed_shard.py`` consume via
:class:`ragstack.ingestion.loaders.JsonlLoader`. This is the missing first step
that lets a directory of PDFs flow through the existing bulk embed/ingest/load
workflows.

**No OCR.** A scanned / image-only PDF (or an unreadable / non-PDF file) yields no
extractable text and is recorded as *skipped* in a sidecar JSON report — it never
crashes the job, so one bad file can't sink a large batch. This mirrors how
``JsonlLoader`` skips (rather than errors on) bad lines. Each skipped entry
carries a human ``reason`` and a constant, countable ``error`` — the loader's
``job_error`` (``NO_TEXT_ERROR`` for a scanned PDF) — and the report lists every
attempted ``inputs`` path, so the ingest step (``ingest_shard.py
--extract-report``) can account for the whole batch per document (#203 2b).

**Deterministic.** Inputs are expanded and sorted, so the same directory always
produces the same shard in the same order. The reusable core (``extract_pdfs``)
is pure and unit-tested offline; ``main`` only does I/O.

Usage::

    python scripts/pdf_extract.py /rag/data/g1-corpus/pdfs \
        --out shard.s0.jsonl --report shard.s0.report.json

    # explicit file list also works, mixed with directories:
    python scripts/pdf_extract.py a.pdf b.pdf some/dir --out shard.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ragstack.ingestion.loaders import LoaderError, PdfLoader


def iter_pdf_paths(inputs: list[str], recursive: bool = False) -> list[Path]:
    """Expand ``inputs`` (files and/or directories) to a sorted list of PDF paths.

    A directory contributes its ``*.pdf`` files (``**/*.pdf`` when ``recursive``);
    a file is taken as-is regardless of extension (so a caller can force a single
    odd-named file through). The result is de-duplicated by resolved path and
    sorted for determinism.
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


def extract_pdfs(
    paths: list[Path], loader: PdfLoader | None = None
) -> tuple[list[dict], list[dict]]:
    """Extract text from ``paths`` -> (records, skipped).

    ``records`` are JSONL-ready ``{"text", "path", "metadata"}`` dicts (the shape
    ``JsonlLoader`` consumes). ``skipped`` are ``{"path", "reason", "error"}``
    dicts for files with no extractable text or that could not be opened
    (scanned/image-only PDFs, empty files, non-PDFs): ``reason`` is the human
    message, ``error`` the loader's constant per-item job error (a
    ``LoaderError`` subclass's ``job_error`` — ``NO_TEXT_ERROR`` for a scanned
    PDF — else the reason itself), which the receipt carries verbatim so a job
    can ``GROUP BY`` it. Pure/deterministic — no filesystem writes.
    """
    loader = loader or PdfLoader()
    records: list[dict] = []
    skipped: list[dict] = []
    for path in paths:
        source = str(path)
        try:
            docs = loader.load(source)
        except LoaderError as e:
            # Typed, caller-safe loader failure (no text / unreadable / non-PDF).
            skipped.append({"path": source, "reason": str(e),
                            "error": getattr(e, "job_error", None) or str(e)})
            continue
        except Exception as e:  # noqa: BLE001 - one bad file must not sink the batch
            skipped.append({"path": source, "reason": f"{type(e).__name__}: {e}",
                            "error": f"{type(e).__name__}: {e}"})
            continue
        for doc in docs:
            records.append(
                {
                    "text": doc.content,
                    "path": doc.source or source,
                    "metadata": dict(doc.metadata),
                }
            )
    return records, skipped


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
    records, skipped = extract_pdfs(paths)
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
    p.add_argument("pdfs", nargs="+", help="PDF files and/or directories of PDFs")
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
