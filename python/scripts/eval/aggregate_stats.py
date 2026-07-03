#!/usr/bin/env python
"""Gather step for the CWL chunking-eval workflow (ADR-0001, offline plane).

Reads the per-config ``metrics.json`` files emitted by ``chunk_one.py`` (one per
chunking config, produced by independent scatter tasks) and assembles the
statistics layer — the metrics table plus the paired-bootstrap difference CIs and
Holm-corrected Wilcoxon signed-rank section — into a single ``report.md``.

This is the deterministic *gather* half of the scatter/gather eval DAG: pure
computation over the metric files, **no GPU / no store / no network**, so it runs
anywhere. It reuses the canonical assemblers from ``scifact_chunk_eval`` and the
stats primitives in ``_stats`` rather than reimplementing them (one owner per
responsibility — the #25 no-fork rule the ADR rests on).

Input contract (one file per config, from ``chunk_one.py``)::

    {"config": "fixed_tok512", "source": "hf:BeIR/scifact",
     "n_queries": 300,
     "means": {"ndcg@10": .., "map": .., "recall@10": .., "recall@20": .., "recall@100": ..},
     "per_query": {"ndcg@10": [..], "map": [..], "recall@10": [..], ...}}

Usage::

    python scripts/eval/aggregate_stats.py a.json b.json ... --out report.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

# Reuse the canonical table/significance assemblers (which wrap the _stats layer)
# and the reference-config constant — do not re-derive the stats here.
import chunking_compare_7way as c7  # noqa: E402
import scifact_chunk_eval as sc  # noqa: E402

# Per-query metric arrays the significance section consumes; every input file must
# carry these keys (chunk_one writes exactly evaluate_config's per_query shape).
_REQUIRED_PQ = ("ndcg@10", "map", "recall@10", "recall@20", "recall@100")


def load_metrics(paths: list[str]) -> tuple[dict, list[str], str, int]:
    """Load per-config metric files into the ``eval_stats`` shape the assemblers
    expect. Returns (eval_stats, ordered_keys, source, n_queries).

    Ordering: the stats reference config (``fixed_tok512``) is placed first when
    present so it reads as the baseline column (``build_significance_section``
    independently picks it as the reference); the rest keep input order.

    Fails loudly (``SystemExit``) on a duplicate config, a missing ``per_query``
    or ``means`` metric, or **misaligned queries**. The paired diff-CI / Wilcoxon
    tests are only valid over the SAME queries in the SAME order for every config;
    when the files carry ``query_ids`` (chunk_one always writes them) that is
    checked exactly, and a length-only fallback covers legacy files without ids.
    """
    eval_stats: dict = {}
    order: list[str] = []
    sources: set[str] = set()
    lengths: set[int] = set()
    qid_lists: dict[str, tuple] = {}
    for p in paths:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        key = data.get("config")
        if not key:
            raise SystemExit(f"{p}: missing 'config'")
        if key in eval_stats:
            raise SystemExit(f"duplicate config '{key}' (from {p})")
        pq = data.get("per_query") or {}
        missing_pq = [m for m in _REQUIRED_PQ if m not in pq]
        if missing_pq:
            raise SystemExit(f"{p}: per_query missing {missing_pq}")
        means = data.get("means") or {}
        missing_means = [m for m in _REQUIRED_PQ if m not in means]
        if missing_means:
            raise SystemExit(f"{p}: means missing {missing_means}")
        lengths.add(len(pq["ndcg@10"]))
        sources.add(str(data.get("source", "?")))
        if data.get("query_ids") is not None:
            qid_lists[key] = tuple(data["query_ids"])
        eval_stats[key] = {"key": key, "means": means, "per_query": pq}
        order.append(key)
    # Alignment: exact query-id match when every file recorded ids (the real
    # paired guarantee); else fall back to a length check and flag that order is
    # unverified — length equality is necessary but NOT sufficient for pairing.
    if len(qid_lists) == len(order):
        if len(set(qid_lists.values())) != 1:
            raise SystemExit(
                "configs were scored over DIFFERENT queries (query_ids mismatch); "
                "the paired diff-CI / Wilcoxon tests require the SAME queries in "
                "the same order for every config"
            )
    elif len(lengths) != 1:
        raise SystemExit(
            f"per-query arrays differ in length across configs ({sorted(lengths)}); "
            "the paired tests require the same queries for every config"
        )
    elif qid_lists:
        print("WARNING: some metrics files lack query_ids; per-query alignment is "
              "assumed from equal length but not verified.", file=sys.stderr)
    if len(sources) > 1:
        print(f"WARNING: configs report different data sources {sorted(sources)}; "
              "a cross-source comparison is not apples-to-apples.", file=sys.stderr)
    ref = c7.STATS_REFERENCE
    keys = ([ref] if ref in eval_stats else []) + [k for k in order if k != ref]
    return eval_stats, keys, next(iter(sources), "?"), next(iter(lengths), 0)


def build_report(eval_stats: dict, keys: list[str], source: str, n_q: int) -> str:
    table = sc.build_metrics_table(eval_stats, keys)
    sig = sc.build_significance_section(eval_stats, keys, n_q)
    return (
        f"# Chunking-eval aggregate (SciFact, {len(keys)} configs)\n\n"
        f"Gathered by `scripts/eval/aggregate_stats.py` from per-config `metrics.json` "
        f"files (CWL scatter/gather, ADR-0001). Data source: **{source}**; "
        f"{n_q} test queries.\n\n"
        f"## Document-level metrics\n\n{table}\n"
        f"## Significance vs the reference config\n\n{sig}\n"
    )


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("metrics", nargs="+",
                   help="per-config metrics.json files from chunk_one.py")
    p.add_argument("--out", default="report.md",
                   help="output report path (default: report.md)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    eval_stats, keys, source, n_q = load_metrics(args.metrics)
    if not keys:
        raise SystemExit("no configs loaded")
    report = build_report(eval_stats, keys, source, n_q)
    Path(args.out).write_text(report, encoding="utf-8")
    print(f"Wrote {args.out} ({len(keys)} configs, {n_q} queries).", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
