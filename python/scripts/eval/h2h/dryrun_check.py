"""``h2h_report.py dryrun``: the sentinel checks of PREREG §2 (dry run) and §7 step 2 (the
real run's first shard), per arm, from files — no live call.

    h2h_report.py dryrun --arm S --receipt <receipt.json> [--summary <summary.json>]
        [--stderr-file <staged file>] [--logs-json <GoWe logs endpoint capture>]
        [--qdrant-count N --es-count N] [--collection-entry <GET /v1/collections item>]
        [--expect-build 1] [--expect-sentinel-chunks 168] [--compare-receipt <other arm's receipt>]

Exit 0 when every check passes, 1 otherwise; prints one line per check. The per-arm
expectations are those of ``gates`` (segmenter, build, the full ``chunking`` object,
documents 40 / 0 failed, store counts = receipt). ``--compare-receipt`` adds the descriptive
chunk-id overlap between the two arms (§4.3 in miniature).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_EVAL = Path(__file__).resolve().parents[1]
if str(_EVAL) not in sys.path:
    sys.path.insert(0, str(_EVAL))

from h2h import gates as G  # noqa: E402


def _load(p: str | None) -> Any:
    return None if not p else json.loads(Path(p).read_text(encoding="utf-8"))


def run_checks(arm: str, receipt: dict[str, Any], *, summary: dict[str, Any] | None = None,
               stderr_text: str | None = None, logs: dict[str, Any] | None = None,
               qdrant: int | None = None, es: int | None = None, entry: dict[str, Any] | None = None,
               expect_build: str | None = None, expect_sentinel_chunks: int | None = None,
               other: dict[str, Any] | None = None) -> tuple[list[str], dict[str, Any]]:
    bad: list[str] = []
    bad += G.check_receipt(receipt, arm, "sentinel", expect_build=expect_build)
    if receipt.get("n_docs") != G.N_SENTINEL_DOCS:
        bad.append(f"{arm}: n_docs {receipt.get('n_docs')} != {G.N_SENTINEL_DOCS}")
    if expect_sentinel_chunks is not None and receipt.get("n_chunks") != expect_sentinel_chunks:
        bad.append(f"{arm}: n_chunks {receipt.get('n_chunks')} != expected {expect_sentinel_chunks}")
    if summary is not None:
        if summary.get("failed_shards"):
            bad.append(f"{arm}: summary failed_shards {summary['failed_shards']}")
        if summary.get("n_shards_without_provenance"):
            bad.append(f"{arm}: summary n_shards_without_provenance {summary['n_shards_without_provenance']}")
        if summary.get("n_chunks") != receipt.get("n_chunks"):
            bad.append(f"{arm}: summary n_chunks {summary.get('n_chunks')} != receipt {receipt.get('n_chunks')}")
    info: dict[str, Any] = {"n_chunks": receipt.get("n_chunks"), "provenance": receipt.get("provenance"),
                            "chunking": receipt.get("chunking")}
    if stderr_text is not None:
        sb, si = G.check_stderr({"sentinel": stderr_text}, arm)
        bad += sb
        info["stderr_file"] = si
    if logs is not None:
        data = logs.get("data") or logs
        lb, li = G.check_stderr({"sentinel": data.get("stderr")}, arm, tail_limited=True)
        bad += lb
        info["logs_endpoint"] = {**li, "exit_code": data.get("exit_code")}
        if data.get("exit_code") not in (0, None):
            bad.append(f"{arm}: ingest task exit_code {data.get('exit_code')}")
        if stderr_text is not None and li["warning_lines"]["sentinel"] != info["stderr_file"]["warning_lines"]["sentinel"]:
            bad.append(f"{arm}: warning-line count differs between the staged file and the logs endpoint")
    if stderr_text is None and logs is None:
        bad.append(f"{arm}: no worker stderr given (neither --stderr-file nor --logs-json)")
    ids = receipt.get("chunk_ids") or []
    expected = len(set(ids)) if ids else receipt.get("n_chunks")
    if qdrant is not None or es is not None:
        bad += G.check_store_counts(arm, expected, qdrant, es)
    if entry is not None:
        prov = entry.get("provenance") or {}
        params = prov.get("chunk_params") or entry.get("chunk_params")
        if params != G.EXPECTED_CHUNK_PARAMS:
            bad.append(f"{arm}: registry chunk_params {params} != {G.EXPECTED_CHUNK_PARAMS}")
        if (entry.get("chunk_method"), entry.get("chunk_size"), prov.get("chunk_overlap")) != ("sentence", 512, 0):
            bad.append(f"{arm}: registry entry method/size/overlap {entry.get('chunk_method')}/{entry.get('chunk_size')}/{prov.get('chunk_overlap')}")
        info["registry"] = {"model": entry.get("model"), "dim": entry.get("dim"), "embedding_api": prov.get("embedding_api"),
                            "spec_hash": prov.get("spec_hash"), "chunk_params": params}
    if other is not None:
        info["chunk_id_overlap"] = G.chunk_id_overlap(ids, other.get("chunk_ids") or [])
        if (other.get("provenance") or {}).get("image_commit") != (receipt.get("provenance") or {}).get("image_commit"):
            bad.append("image_commit differs between the two arms' receipts")
    return bad, info


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arm", required=True, choices=["S", "S-regex"])
    ap.add_argument("--receipt", required=True)
    ap.add_argument("--summary")
    ap.add_argument("--stderr-file")
    ap.add_argument("--logs-json")
    ap.add_argument("--qdrant-count", type=int)
    ap.add_argument("--es-count", type=int)
    ap.add_argument("--collection-entry")
    ap.add_argument("--expect-build")
    ap.add_argument("--expect-sentinel-chunks", type=int)
    ap.add_argument("--compare-receipt")
    args = ap.parse_args(argv)
    text = Path(args.stderr_file).read_text(encoding="utf-8", errors="replace") if args.stderr_file else None
    bad, info = run_checks(args.arm, _load(args.receipt), summary=_load(args.summary), stderr_text=text,
                           logs=_load(args.logs_json), qdrant=args.qdrant_count, es=args.es_count,
                           entry=_load(args.collection_entry), expect_build=args.expect_build,
                           expect_sentinel_chunks=args.expect_sentinel_chunks, other=_load(args.compare_receipt))
    print(json.dumps({"arm": args.arm, "ok": not bad, "failures": bad, "info": info}, indent=1, default=str))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
