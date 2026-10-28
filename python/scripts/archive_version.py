#!/usr/bin/env python
"""Archive step of the ingest workflows (#357, phase 2 of #353): pack the embed
stage's output(s) + the load receipt into ONE versioned directory,
``<out>/<N>/``, in the ``ragstack-archive/1`` format
(:mod:`ragstack.ingestion.archive`). The workflow emits that directory as a
CWL ``Directory`` output whose basename is the version number, and the engine
uploads it to the submission's output destination — so the archive lands at
``versions/<N>/`` with no Workspace call and no token inside this task.

Two modes, mutually exclusive:

* **chunk version** — ``--chunks <emb.jsonl...> --receipt <receipt.json...>``
  writes ``chunks.jsonl.gz`` + ``vectors.f32`` + ``receipt.json`` +
  ``manifest.json`` (+ ``glossary.jsonl`` with ``--glossary-file``, #847 G2:
  the glossary step's per-batch files merged; role ``glossary``);
  When the embedding files hold NO chunk at all (every document of the run
  failed on its own — all duplicates, all scanned) there is nothing to
  archive: the tool exits ``NOTHING_TO_ARCHIVE_EXIT_CODE`` (5) and prints
  each document's receipt error to stderr first (#768), so the API can
  report it per document;
* **tombstone version** — ``--tombstone doc_ids.json`` (a JSON list of doc ids,
  or an object with a ``doc_ids`` list) writes ``tombstone.json`` +
  ``manifest.json`` only.

Pure local I/O: no store, no network. Streaming — never holds the vectors in
memory. Idempotent: a re-run replaces ``<out>/<N>/`` with byte-identical files.

Both modes record ``provenance`` in the manifest (ADR-0010 decision 8, #655
step 2): ``--workflow-id`` / ``--tool-image`` / ``--tool-image-digest`` as the
API seeded them on the submission, plus ``image_version`` / ``image_commit`` /
``image_build`` read from this image's ``/opt/ragstack/RELEASE`` (null outside
an image). A hand run with none of the flags records an all-null object.

Usage::

    python scripts/archive_version.py --version 3 --collection-id oa-dev \
        --chunks shard.emb.jsonl --receipt load-summary.json --out .
    python scripts/archive_version.py --version 4 --collection-id oa-dev \
        --tombstone removed.json --out .
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ragstack.ingestion.archive import (
    ArchiveError,
    EmptyVersionError,
    write_tombstone,
    write_version,
)
from ragstack.ingestion.receipts import (
    NOTHING_TO_ARCHIVE_EXIT_CODE,
    ShardReceipt,
    format_nothing_to_archive,
)
from ragstack.provenance import add_provenance_arguments, provenance_from_args


def _version(text: str) -> int:
    if not text.isdigit():
        raise argparse.ArgumentTypeError(f"--version must be a non-negative integer, got {text!r}")
    return int(text)


def _load_doc_ids(path: str) -> list[str]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"{path}: cannot read tombstone ids: {e}") from e
    ids = data.get("doc_ids") if isinstance(data, dict) else data
    if not isinstance(ids, list) or not all(isinstance(d, str) for d in ids):
        raise SystemExit(f"{path}: expected a JSON list of doc-id strings "
                         "(or an object with a 'doc_ids' list)")
    return ids


def _readable_receipts(paths: list[str]) -> list[ShardReceipt]:
    """The receipts that parse (one object or a JSON array of them per file);
    an unreadable one only shortens the per-document listing."""
    out: list[ShardReceipt] = []
    for path in paths:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for entry in data if isinstance(data, list) else [data]:
            try:
                out.append(ShardReceipt.from_dict(entry))
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
    return out


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", type=_version, required=True,
                   help="version number N; the output directory is <out>/N")
    p.add_argument("--chunks", nargs="+", default=[], metavar="EMB_JSONL",
                   help="embedding file(s) from embed_shard / ingest_shard, in row order")
    p.add_argument("--receipt", nargs="+", default=[], metavar="RECEIPT_JSON",
                   help="the load stage's receipt(s): one is copied verbatim, several are "
                        "written as a JSON array")
    p.add_argument("--glossary-file", nargs="+", default=[], metavar="GLOSSARY_JSONL",
                   help="#847: the glossary step's file(s) for this version's documents; "
                        "merged into glossary.jsonl (role 'glossary'). A doc_id in two "
                        "files, or files built under different configs, refuses (exit 1)")
    p.add_argument("--tombstone", default=None, metavar="DOC_IDS_JSON",
                   help="tombstone mode: JSON list of removed doc ids (no chunks/receipt)")
    p.add_argument("--out", default=".", help="parent directory for <N>/ (default: cwd)")
    p.add_argument("--collection-id", required=True, help="registry collection id")
    p.add_argument("--tenant", default="public")
    p.add_argument("--spec-hash", default="", help="the collection's build-spec hash (ADR-0002)")
    p.add_argument("--job-id", default="", help="the RAGStack ingest job id this version came from")
    p.add_argument("--workers", type=int, default=None,
                   help="packer processes (default: min(4, cpus)); 1 = in-process")
    add_provenance_arguments(p)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.tombstone and (args.chunks or args.receipt or args.glossary_file):
        raise SystemExit("--tombstone is exclusive with --chunks/--receipt/--glossary-file")
    if not args.tombstone and not (args.chunks and args.receipt):
        raise SystemExit("need --chunks and --receipt (chunk version) or --tombstone")
    # ADR-0010 decision 8: what built this version — the submission inputs the
    # API seeded (workflow id, image name, digest) plus this image's own RELEASE.
    provenance = provenance_from_args(args)
    try:
        if args.tombstone:
            manifest = write_tombstone(
                args.out, args.version, _load_doc_ids(args.tombstone),
                collection_id=args.collection_id, tenant=args.tenant,
                spec_hash=args.spec_hash, job_id=args.job_id, provenance=provenance,
            )
        else:
            manifest = write_version(
                args.out, args.version, args.chunks, args.receipt,
                collection_id=args.collection_id, tenant=args.tenant,
                spec_hash=args.spec_hash, job_id=args.job_id, workers=args.workers,
                provenance=provenance, glossary_paths=args.glossary_file,
            )
    except EmptyVersionError as e:
        # Every document of the run failed on its own (#768): all refused as
        # duplicates, all scanned PDFs. There is no zero-row version in
        # ragstack-archive/1 (#357 residual), so this task still fails — but
        # with its own exit code and each document's reason FIRST on stderr,
        # the part of it the engine keeps on the submission's error record, so
        # the API can report per document instead of `gowe submission FAILED`.
        print(format_nothing_to_archive(_readable_receipts(args.receipt)),
              file=sys.stderr, flush=True)
        print(f"archive: {e}", file=sys.stderr)
        return NOTHING_TO_ARCHIVE_EXIT_CODE
    except ArchiveError as e:
        print(f"archive: {e}", file=sys.stderr)
        return 1
    counts = manifest["counts"]
    kind = "tombstone" if manifest["has_tombstone"] else "chunks"
    print(f"[{args.collection_id}] version={args.version} {kind}: "
          f"chunks={counts['chunks']} docs={counts['docs']} "
          f"files={manifest['files']} → {Path(args.out) / str(args.version)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
