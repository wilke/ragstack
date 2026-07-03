#!/usr/bin/env python
"""Gather step for the CWL/GoWe bulk-ingest workflow (ADR-0001 step 2).

Reads the per-shard ``receipt.json`` files emitted by ``ingest_shard.py`` and
writes a run summary — totals plus, crucially, the ids of any **failed shards**
so a bulk run surfaces partial failure instead of silently under-ingesting. Pure
computation over the receipt files (no store/network), so it's CI-friendly.

Usage::

    python scripts/merge_receipts.py r0.json r1.json ... --out summary.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ragstack.ingestion.receipts import ShardReceipt, merge_summary


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("receipts", nargs="+", help="per-shard receipt.json files")
    p.add_argument("--out", default="summary.json", help="output summary path")
    p.add_argument("--fail-on-shard-error", action="store_true",
                   help="exit non-zero if any shard failed (for a gating workflow)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    receipts = [ShardReceipt.load(p) for p in args.receipts]
    summary = merge_summary(receipts)
    Path(args.out).write_text(json.dumps(summary, indent=2, sort_keys=True),
                              encoding="utf-8")
    print(f"{summary['n_shards']} shards: {summary['n_docs']} docs, "
          f"{summary['n_chunks']} chunks, {summary['n_shards_failed']} failed "
          f"→ {args.out}", flush=True)
    if summary["failed_shards"]:
        print(f"FAILED shards: {summary['failed_shards']}", file=sys.stderr)
    if args.fail_on_shard_error and summary["n_shards_failed"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
