#!/usr/bin/env python
"""Build and verify ``glossary.jsonl`` files (#847 G1). Offline: no store, no
network, no model.

``build`` converts what exists today into the file format
(:mod:`ragstack.ingestion.glossary_file`): for every document of one or more
pre-extracted JSONL files (``JsonlLoader``, the format the ingest tools read),
the rules glossary, the offline LLM glossary when ``--llm-cache`` holds the
document's exact text (re-validated against it, as ingest does), and the merged
glossary — the one today's in-process attach would render, except that a
cached LLM glossary the gate keeps nothing of falls back to the rules here
(``choose_merged``; the attach keeps the empty one). A file built with no
cache therefore renders exactly the field today's ingest produces.

``verify`` reads a file strictly (schema version, every row, the header counts)
and, with ``--jsonl``, checks every document's ``text_sha256`` against the text
the loader produces. Exit 1 on a mismatch or a document with no row.

``merge`` (#847 G2) concatenates files — e.g. the per-batch files of one
workflow run — into one, refusing a ``doc_id`` in two inputs and inputs built
under different configs. Deterministic: the same inputs give the same bytes.

Usage::

    python scripts/glossary_file.py build --jsonl docs.jsonl [--llm-cache cache.jsonl] \\
        --out glossary.jsonl
    python scripts/glossary_file.py verify glossary.jsonl [--jsonl docs.jsonl]
    python scripts/glossary_file.py merge --out glossary.jsonl batch0.jsonl batch1.jsonl ...
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterator

from ragstack.ingestion.glossary_file import (
    GlossaryFileError,
    GlossaryRow,
    build_row,
    merge_files,
    read_glossary_file,
    write_glossary_file,
)
from ragstack.ingestion.loaders import JsonlLoader
from ragstack.ingestion.symbol_glossary import (
    EXTRACTOR_VERSION,
    SymbolGlossaryAttacher,
    SymbolGlossaryCache,
    config_from_json,
    content_key,
)

PRODUCER = f"scripts/glossary_file.py build (extractor {EXTRACTOR_VERSION})"


def _existing_nonempty(path: str) -> str:
    """A typo must not open as an EMPTY cache (every document would silently
    fall back to the rules) — the backfill's ``--llm-cache`` rule."""
    if not os.path.isfile(path):
        raise argparse.ArgumentTypeError(f"{path!r}: no such file")
    if os.path.getsize(path) == 0:
        raise argparse.ArgumentTypeError(f"{path!r}: the file is empty")
    return path


def _documents(paths: list[str]) -> Iterator[tuple[str, str]]:
    loader = JsonlLoader()
    for path in paths:
        for doc in loader.load(path):
            yield doc.id, doc.content


def build(args: argparse.Namespace) -> dict[str, int]:
    config = config_from_json(args.config)
    cache = SymbolGlossaryCache(args.llm_cache) if args.llm_cache else None
    attacher = SymbolGlossaryAttacher(config, cache, args.llm_cache_version or None)

    def rows() -> Iterator[GlossaryRow]:
        for doc_id, text in _documents(args.jsonl):
            yield build_row(doc_id, text, attacher)

    try:
        return write_glossary_file(args.out, rows(), producer=PRODUCER, config=config)
    finally:
        if cache is not None:
            cache.close()


def verify(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    gfile = read_glossary_file(args.file)
    out: dict[str, object] = {"file": str(args.file), "schema": gfile.header["schema"],
                              "counts": gfile.header["counts"]}
    rc = 0
    if args.jsonl:
        mismatch: list[str] = []
        missing: list[str] = []
        seen: set[str] = set()
        for doc_id, text in _documents(args.jsonl):
            seen.add(doc_id)
            row = gfile.rows.get(doc_id)
            if row is None:
                missing.append(doc_id)
            elif row.text_sha256 != content_key(text):
                mismatch.append(doc_id)
        out.update(documents=len(seen), mismatch=len(mismatch), missing=len(missing),
                   rows_without_document=len(set(gfile.rows) - seen),
                   examples={"mismatch": mismatch[:5], "missing": missing[:5]})
        rc = 1 if (mismatch or missing) else 0
    return out, rc


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="documents (+ optional LLM cache) -> glossary.jsonl")
    b.add_argument("--jsonl", action="append", required=True,
                   help="pre-extracted documents (JsonlLoader format); repeatable")
    b.add_argument("--llm-cache", default=None, type=_existing_nonempty,
                   help="offline LLM glossary cache (scripts/symbol_glossary_llm.py), read-only")
    b.add_argument("--llm-cache-version", default="",
                   help="pin one cache version (default: the last record per document)")
    b.add_argument("--config", default="", help="GlossaryConfig overrides as JSON")
    b.add_argument("--out", required=True, help="the glossary.jsonl to write (atomically)")
    v = sub.add_parser("verify", help="strict read; with --jsonl, check text_sha256")
    v.add_argument("file")
    v.add_argument("--jsonl", action="append", default=[],
                   help="documents to check the rows' text_sha256 against; repeatable")
    m = sub.add_parser("merge", help="concatenate glossary files into one")
    m.add_argument("files", nargs="+")
    m.add_argument("--out", required=True, help="the merged glossary.jsonl (atomically)")
    args = p.parse_args(argv)
    try:
        if args.command == "merge":
            counts = merge_files(args.files, args.out)
            print(json.dumps({"out": args.out, "counts": counts}, sort_keys=True))
            return 0
        if args.command == "build":
            counts = build(args)
            print(json.dumps({"out": args.out, "counts": counts}, sort_keys=True))
            return 0
        result, rc = verify(args)
        print(json.dumps(result, sort_keys=True))
        return rc
    except GlossaryFileError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
