#!/usr/bin/env python
"""G1 pooling, stratified subsampling and blinded rater assignment (protocol §4.3/§4.4).

Three jobs, in one script because they share one set of seeds and one manifest:

**1. TREC-style pooling.** For every query, the union of the top-``--pool-depth``
chunks from *every* grid cell, deduped to ``(query_id, chunk_id)`` pairs. §4.3
makes the fixed depth mandatory: if pool depth varied with a cell's parameters,
configurations contributing more to the pool would be systematically advantaged —
and since sweeping ``rerank_candidates`` *is* a pool-depth manipulation, a
depth-varying pool would bias exactly the comparison the pilot exists to make.
The pool is therefore built at one depth for all cells and re-used across every
cell and rung, and the script refuses to pool cells whose rankings are shorter
than the requested depth without saying so.

**2. Stratified subsample for human rating.** The full pool is what the LLM judge
labels (§4.2 budgets 25k–50k pairs); humans see a subsample, and *which*
subsample decides whether κ means anything. A uniform draw from the pool would be
overwhelmingly deep-rank, overwhelmingly grade-0 pairs, and κ estimated on a
near-degenerate marginal is both imprecise and — because κ is marginal-dependent
— not comparable to anything. So the subsample is stratified on the two things
that predict the label: the pair's **best rank across cells** and, when LLM
labels are available, the **judge's grade**. Allocation is proportional with a
per-stratum floor: proportional keeps κ interpretable as an estimate for the pool
as it will actually be judged, and the floor is what guarantees the rare
strata (grade 2, rank 1) are observed at all. ``--allocation balanced`` is
available and deliberately *not* the default, because it changes the estimand.

**Size.** Default 400, range-checked to 300–500. The reason is precision, and
:func:`_stats.kappa_se_forecast` puts a number on it: at n = 100 pairs the SD of
κ̂ around 0.5 is ≈ 0.08, i.e. a 95% CI of roughly ±0.15 — which straddles the
protocol's 0.4 gate from 0.35 to 0.65 and therefore cannot decide the thing it
gates. At n = 400 the SD is ≈ 0.04. The forecast for the realized design is
written into the manifest, so the precision claim ships with the artefact.

**3. Assignment with a deliberate overlap set.** Each pair goes to
``--replication`` raters (default 2 — "double-rated"), and a designated overlap
set goes to *every* rater on the panel. With two raters those coincide; with three
or more they do not, and the overlap set is what makes a single panel-wide Fleiss'
κ computable instead of a bag of incomparable pairwise numbers. Loads are balanced
to within one item.

**Blinding is enforced on write.** §4.4's "config leakage" row requires the judge
to see only ``(query, chunk text)``; a human rater who can see ``cell_id`` or the
LLM's label is not an independent label source. Every assignment record is checked
against :func:`_g1_rating.blinding_violations` before the file is written, and the
browser tool re-checks on load. The contributing cells and the LLM labels live in
``pool.jsonl``, which is never handed to a rater.

Outputs::

    <out-dir>/pool.jsonl                    every pooled pair + provenance (NOT for raters)
    <out-dir>/subsample.jsonl               the human subsample with its strata
    <out-dir>/assignments/<rater>.jsonl     blinded, one per rater
    <out-dir>/calibration.jsonl             blinded, identical for every rater
    <out-dir>/calibration_key.template.jsonl  gold grades for the study lead to fill in
    <out-dir>/manifest.json                 seeds, strata, allocations, κ forecast

Usage::

    cd python && export PYTHONPATH="$PWD"
    /rag/envs/ragstack/bin/python scripts/eval/g1_make_pool.py \\
        --rankings reports/g1-library-retrieval/<run>/raw \\
        --queries  reports/g1-library-retrieval/fixtures/g1_pilot_p50_queries.jsonl \\
        --chunks   /rag/data/g1-corpus/chunks.p200.jsonl \\
        --corpus-manifest /rag/data/g1-corpus/manifest.json \\
        --llm-labels reports/g1-library-retrieval/qrels/pilot/judgments.jsonl \\
        --raters alice,bob --subsample 400 \\
        --out-dir reports/g1-library-retrieval/rating/round1
"""
from __future__ import annotations

import argparse
import random
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import _g1_rating as g1r  # noqa: E402
import _stats  # noqa: E402
from g1_make_queries import load_chunks, load_titles  # noqa: E402

POOL_DEPTH = 20  # protocol §4.3 — fixed for every cell, non-negotiable
SUBSAMPLE_MIN, SUBSAMPLE_MAX = 300, 500

#: Rank bands for stratification. The boundaries are the k values the protocol
#: reports at (§6.1/§6.2: k ∈ {1, 3, 5, 10, 20}) so a stratum maps onto a metric
#: cutoff rather than onto an arbitrary split.
RANK_BANDS: tuple[tuple[str, int, int], ...] = (
    ("r01", 1, 1),
    ("r02_05", 2, 5),
    ("r06_10", 6, 10),
    ("r11_20", 11, 20),
    ("r21_plus", 21, 10**9),
)


# --------------------------------------------------------------------------- #
# Pooling
# --------------------------------------------------------------------------- #
def cell_id_from_path(path: Path) -> str:
    """``P200_hybrid_rrf60_d100_rr0.rankings.jsonl`` → ``P200_hybrid_rrf60_d100_rr0``."""
    name = path.name
    for suffix in (".rankings.jsonl", ".jsonl"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return path.stem


def pool_pairs(
    rankings: dict[str, dict[str, list[str]]], pool_depth: int = POOL_DEPTH
) -> list[dict[str, Any]]:
    """Union the top-``pool_depth`` of every cell into deduped judgment pairs.

    ``rankings`` maps ``cell_id → {query_id: [chunk_id, …]}``. Each output row
    carries ``best_rank`` (the shallowest rank the pair reached in any cell) and
    ``n_cells`` (how many cells surfaced it) — the two pooling facts the
    stratification needs and the two that must never reach a rater.

    Output order is deterministic: ``(query_id, best_rank, chunk_id)``.
    """
    if pool_depth <= 0:
        raise ValueError("pool_depth must be positive")
    acc: dict[tuple[str, str], dict[str, Any]] = {}
    for cell_id in sorted(rankings):
        for qid, chunk_ids in sorted(rankings[cell_id].items()):
            for rank, chunk_id in enumerate(chunk_ids[:pool_depth], start=1):
                key = (qid, chunk_id)
                row = acc.get(key)
                if row is None:
                    acc[key] = {
                        "pair_id": g1r.pair_id(qid, chunk_id),
                        "query_id": qid,
                        "chunk_id": chunk_id,
                        "best_rank": rank,
                        "n_cells": 1,
                        "cells": [cell_id],
                    }
                else:
                    row["best_rank"] = min(row["best_rank"], rank)
                    row["n_cells"] += 1
                    row["cells"].append(cell_id)
    return sorted(acc.values(), key=lambda r: (r["query_id"], r["best_rank"], r["chunk_id"]))


def rank_band(rank: int) -> str:
    for name, lo, hi in RANK_BANDS:
        if lo <= rank <= hi:
            return name
    return RANK_BANDS[-1][0]


def stratum_of(row: dict[str, Any], llm_labels: dict[str, int] | None) -> str:
    """``<rank band>/<judge grade>`` — the stratification key.

    The judge grade is included when available *because* κ(judge–human) is the
    gated statistic: a subsample that happens to contain three grade-2 pairs
    estimates the judge's agreement on the labels that matter with essentially no
    precision, however many grade-0 pairs sit next to them.
    """
    band = rank_band(int(row["best_rank"]))
    if llm_labels is None:
        return band
    grade = llm_labels.get(row["pair_id"])
    return f"{band}/g{grade}" if grade is not None else f"{band}/gna"


# --------------------------------------------------------------------------- #
# Allocation
# --------------------------------------------------------------------------- #
def allocate(sizes: dict[str, int], n: int, min_per_stratum: int = 0) -> dict[str, int]:
    """Split ``n`` draws across strata proportionally, with a floor and a cap.

    Largest-remainder over the *residual* capacity, so a stratum smaller than its
    proportional share hands the surplus back rather than silently under-filling
    the sample. When the floors alone exceed ``n`` they are trimmed from the
    largest allocation down — deterministically — rather than the request being
    rejected, because a floor is a preference and ``n`` is a budget.
    """
    keys = sorted(sizes)
    total = sum(sizes.values())
    if n <= 0:
        return dict.fromkeys(keys, 0)
    if n >= total:
        return {k: sizes[k] for k in keys}
    alloc = {k: min(min_per_stratum, sizes[k]) for k in keys}
    while sum(alloc.values()) > n:
        k = max((k for k in keys if alloc[k] > 0), key=lambda k: (alloc[k], sizes[k], k))
        alloc[k] -= 1
    cap = {k: sizes[k] - alloc[k] for k in keys}
    remaining = n - sum(alloc.values())
    while remaining > 0:
        avail = [k for k in keys if cap[k] > 0]
        if not avail:
            break
        total_cap = sum(cap[k] for k in avail)
        shares = {k: remaining * cap[k] / total_cap for k in avail}
        floors = {k: min(cap[k], int(shares[k])) for k in avail}
        given = sum(floors.values())
        if given == 0:
            order = sorted(avail, key=lambda k: (-shares[k], -cap[k], k))
            for k in order[:remaining]:
                alloc[k] += 1
                cap[k] -= 1
            remaining -= min(remaining, len(order))
            continue
        for k in avail:
            alloc[k] += floors[k]
            cap[k] -= floors[k]
        remaining -= given
    return alloc


def allocate_balanced(sizes: dict[str, int], n: int) -> dict[str, int]:
    """Equal per stratum, capped by stratum size, surplus spread over the rest.

    Changes the estimand — κ over a re-weighted population is not κ over the pool
    — which is why the caller has to ask for it explicitly.
    """
    keys = sorted(sizes)
    alloc = dict.fromkeys(keys, 0)
    remaining = min(n, sum(sizes.values()))
    while remaining > 0:
        avail = [k for k in keys if alloc[k] < sizes[k]]
        if not avail:
            break
        k = min(avail, key=lambda k: (alloc[k], -sizes[k], k))
        alloc[k] += 1
        remaining -= 1
    return alloc


def stratified_pick(
    rows: Sequence[dict[str, Any]],
    n: int,
    *,
    key: Callable[[dict[str, Any]], str],
    seed: int,
    min_per_stratum: int = 0,
    allocation: str = "proportional",
) -> list[dict[str, Any]]:
    """Draw ``n`` rows, stratified by ``key``, deterministically given ``seed``.

    Within a stratum the draw is a seeded shuffle of the rows in ``pair_id``
    order, so the result depends on the *content* of the pool and the seed, never
    on file order or on dict iteration order.
    """
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in sorted(rows, key=lambda r: r["pair_id"]):
        buckets.setdefault(key(row), []).append(row)
    sizes = {k: len(v) for k, v in buckets.items()}
    alloc = (
        allocate_balanced(sizes, n)
        if allocation == "balanced"
        else allocate(sizes, n, min_per_stratum)
    )
    out: list[dict[str, Any]] = []
    for k in sorted(buckets):
        pool = list(buckets[k])
        random.Random(f"{seed}:{k}").shuffle(pool)
        for row in pool[: alloc[k]]:
            out.append({**row, "stratum": k})
    return sorted(out, key=lambda r: r["pair_id"])


# --------------------------------------------------------------------------- #
# Assignment
# --------------------------------------------------------------------------- #
def assign(
    pair_ids: Sequence[str],
    raters: Sequence[str],
    *,
    replication: int,
    overlap_ids: Iterable[str] = (),
    seed: int = 0,
    forbidden: dict[str, set[str]] | None = None,
) -> dict[str, list[str]]:
    """Map rater → pair ids, ``replication`` raters per pair, loads balanced ±1.

    Pairs in ``overlap_ids`` go to **every** eligible rater regardless of
    ``replication``: that set is the panel-wide anchor Fleiss' κ is computed on,
    and with more than two raters it is the only way a single agreement number
    exists for the panel. Non-overlap pairs go to the ``replication`` least-loaded
    eligible raters, which keeps the session lengths comparable — an unbalanced
    panel is a fatigue confound (SOP §4.3), not merely an aesthetic problem.

    ``forbidden`` maps a pair to raters who must not see it. The case that
    matters: a domain expert who *wrote* a query must not judge relevance for it
    (SOP §3.4). They know what they had in mind, which is precisely the
    information a relevance judgment is supposed to be independent of, and their
    agreement with the judge would be an artefact of authorship.
    """
    if not raters:
        raise ValueError("no raters")
    if len(set(raters)) != len(raters):
        raise ValueError(f"duplicate rater ids: {raters}")
    if not 1 <= replication <= len(raters):
        raise ValueError(f"replication must be in 1..{len(raters)}, got {replication}")
    forbidden = forbidden or {}
    overlap = set(overlap_ids)
    order = sorted(set(pair_ids))
    random.Random(seed).shuffle(order)
    load = dict.fromkeys(raters, 0)
    out: dict[str, list[str]] = {r: [] for r in raters}
    rank = {r: i for i, r in enumerate(raters)}
    for pid in order:
        eligible = [r for r in raters if r not in forbidden.get(pid, ())]
        if len(eligible) < replication:
            raise ValueError(
                f"pair {pid}: only {len(eligible)} eligible rater(s) for replication="
                f"{replication} (excluded: {sorted(forbidden.get(pid, ()))})"
            )
        targets = (
            eligible
            if pid in overlap
            else sorted(eligible, key=lambda r: (load[r], rank[r]))[:replication]
        )
        for r in targets:
            out[r].append(pid)
            load[r] += 1
    return {r: sorted(v) for r, v in out.items()}


def assignment_records(
    pair_ids: Sequence[str],
    *,
    rater_id: str,
    assignment_id: str,
    set_label: str,
    pool_by_id: dict[str, dict[str, Any]],
    queries: dict[str, str],
    chunks: dict[str, dict[str, Any]],
    titles: dict[str, str],
) -> list[dict[str, Any]]:
    """Build the blinded records the rating tool consumes.

    Exactly the fields §4.4 permits — query, chunk text, source document title —
    plus the ids needed to join the export back to the pool. ``doc_id`` itself is
    *not* included: the rater is shown the title, and the pool holds the mapping,
    so an id that could be looked up against a configuration's output never
    reaches the browser.
    """
    out: list[dict[str, Any]] = []
    for pid in pair_ids:
        row = pool_by_id[pid]
        chunk = chunks.get(row["chunk_id"])
        if chunk is None:
            raise KeyError(f"chunk {row['chunk_id']} (pair {pid}) missing from --chunks")
        query = queries.get(row["query_id"])
        if query is None:
            raise KeyError(f"query {row['query_id']} (pair {pid}) missing from --queries")
        out.append(
            {
                "pair_id": pid,
                "assignment_id": assignment_id,
                "rater_id": rater_id,
                "set": set_label,
                "query_id": row["query_id"],
                "query": query,
                "chunk_text": chunk["text"],
                "doc_title": titles.get(chunk["doc_id"]) or chunk.get("title") or "",
            }
        )
    g1r.assert_blind(out)
    return out


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #
def load_rankings(patterns: Sequence[str]) -> dict[str, dict[str, list[str]]]:
    """Read the sweep's ``raw/<cell_id>.rankings.jsonl`` files.

    Format (``g1_library_sweep.write_outputs``): one JSON object per line,
    ``{"query_id": …, "chunk_ids": [...]}``.
    """
    out: dict[str, dict[str, list[str]]] = {}
    for path in g1r.iter_glob(patterns):
        if path.name.endswith(".counters.jsonl"):
            continue
        rows = g1r.read_jsonl(path)
        if not rows or "chunk_ids" not in rows[0]:
            continue
        out[cell_id_from_path(path)] = {
            str(r["query_id"]): [str(c) for c in r.get("chunk_ids") or []] for r in rows
        }
    return out


def load_llm_labels(path: str | Path | None) -> dict[str, int] | None:
    """``pair_id → grade`` from the judge's output.

    Accepts either a ``pair_id`` column or the ``(query_id, chunk_id)`` the
    protocol's ``qrels/pilot/judgments.jsonl`` shape uses, since the pair id is
    derivable from the latter.
    """
    if not path:
        return None
    out: dict[str, int] = {}
    for rec in g1r.read_jsonl(path):
        pid = rec.get("pair_id")
        if not pid and rec.get("query_id") and rec.get("chunk_id"):
            pid = g1r.pair_id(str(rec["query_id"]), str(rec["chunk_id"]))
        grade = rec.get("grade", rec.get("label"))
        if pid and grade is not None:
            out[str(pid)] = int(grade)
    return out


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Pool, stratify and assign G1 (query, chunk) pairs for human rating.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--rankings", nargs="+", required=True, help="sweep raw/ dir(s) or file glob(s)")
    p.add_argument("--queries", required=True, help="g1_make_queries output (.jsonl)")
    p.add_argument("--chunks", required=True, help="chunk file covering every pooled chunk_id")
    p.add_argument("--corpus-manifest", help="/rag/data/g1-corpus/manifest.json, for doc titles")
    p.add_argument("--llm-labels", help="judge output, for stratification and later κ")
    p.add_argument("--pool-depth", type=int, default=POOL_DEPTH, help="§4.3: fixed for every cell")
    p.add_argument("--subsample", type=int, default=400, help=f"{SUBSAMPLE_MIN}-{SUBSAMPLE_MAX}")
    p.add_argument("--allocation", choices=("proportional", "balanced"), default="proportional")
    p.add_argument("--min-per-stratum", type=int, default=10)
    p.add_argument("--raters", required=True, help="comma-separated rater ids")
    p.add_argument("--replication", type=int, default=2, help="raters per pair")
    p.add_argument("--overlap-frac", type=float, default=0.2, help="share seen by EVERY rater")
    p.add_argument("--overlap-n", type=int, default=None, help="overrides --overlap-frac")
    p.add_argument("--calibration-n", type=int, default=30, help="SOP §2.3 calibration set")
    p.add_argument("--exclude", nargs="*", default=[], help="judgment/pair files to exclude")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument(
        "--allow-any-size",
        action="store_true",
        help="permit a subsample outside 300-500 (records the deviation in the manifest)",
    )
    p.add_argument("--out-dir", required=True)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.allow_any_size and not SUBSAMPLE_MIN <= args.subsample <= SUBSAMPLE_MAX:
        raise SystemExit(
            f"--subsample {args.subsample} outside {SUBSAMPLE_MIN}-{SUBSAMPLE_MAX}: at 100 pairs "
            f"the 95% CI of κ is ≈ ±0.15 against a 0.4 gate. Pass --allow-any-size to override."
        )
    raters = [r.strip() for r in args.raters.split(",") if r.strip()]
    out_dir = Path(args.out_dir)

    rankings = load_rankings(args.rankings)
    if not rankings:
        raise SystemExit(f"no rankings files matched {args.rankings}")
    pool = pool_pairs(rankings, args.pool_depth)
    short_cells = sorted(
        cid
        for cid, per_q in rankings.items()
        if per_q and min(len(v) for v in per_q.values()) < args.pool_depth
    )
    print(
        f"[pool] {len(rankings)} cells x depth {args.pool_depth} -> {len(pool)} pairs "
        f"over {len({r['query_id'] for r in pool})} queries",
        flush=True,
    )
    if short_cells:
        print(
            f"[pool] WARNING {len(short_cells)} cell(s) returned fewer than {args.pool_depth} "
            f"chunks for some query — the pool is uneven across cells (§4.3): "
            f"{', '.join(short_cells[:5])}{' …' if len(short_cells) > 5 else ''}",
            flush=True,
        )

    query_recs = g1r.read_jsonl(args.queries)
    queries = {q["query_id"]: q["text"] for q in query_recs}
    # SOP §3.4: whoever wrote a query may not judge it.
    authors = {q["query_id"]: str(q.get("author_id") or "") for q in query_recs}
    chunks = {c["chunk_id"]: c for c in load_chunks(args.chunks)}
    titles = load_titles(args.corpus_manifest)
    llm_labels = load_llm_labels(args.llm_labels)

    missing_q = sorted({r["query_id"] for r in pool} - set(queries))
    missing_c = sorted({r["chunk_id"] for r in pool} - set(chunks))
    if missing_q or missing_c:
        raise SystemExit(
            f"pool references {len(missing_q)} unknown query id(s) and {len(missing_c)} unknown "
            f"chunk id(s); e.g. {missing_q[:3]} {missing_c[:3]}"
        )

    excluded: set[str] = set()
    for path in g1r.iter_glob(args.exclude):
        for rec in g1r.read_jsonl(path):
            pid = rec.get("pair_id")
            if not pid and rec.get("query_id") and rec.get("chunk_id"):
                pid = g1r.pair_id(str(rec["query_id"]), str(rec["chunk_id"]))
            if pid:
                excluded.add(str(pid))
    candidates = [r for r in pool if r["pair_id"] not in excluded]

    def _key(row: dict[str, Any]) -> str:
        return stratum_of(row, llm_labels)

    # Calibration comes out first and is then withheld from the live subsample:
    # a rater who has already seen a pair *with its gold label* is not blind to it.
    calibration = stratified_pick(
        candidates,
        args.calibration_n,
        key=_key,
        seed=args.seed + 101,
        allocation="balanced",
    )
    cal_ids = {r["pair_id"] for r in calibration}
    live_pool = [r for r in candidates if r["pair_id"] not in cal_ids]

    subsample = stratified_pick(
        live_pool,
        args.subsample,
        key=_key,
        seed=args.seed,
        min_per_stratum=args.min_per_stratum,
        allocation=args.allocation,
    )
    overlap_n = (
        args.overlap_n
        if args.overlap_n is not None
        else int(round(args.overlap_frac * len(subsample)))
    )
    overlap = stratified_pick(subsample, overlap_n, key=_key, seed=args.seed + 7)
    overlap_ids = [r["pair_id"] for r in overlap]

    forbidden = {
        r["pair_id"]: {authors[r["query_id"]]}
        for r in subsample
        if authors.get(r["query_id"]) in raters
    }
    if forbidden:
        print(f"[assign] {len(forbidden)} pair(s) withheld from their query's author")
    assignments = assign(
        [r["pair_id"] for r in subsample],
        raters,
        replication=args.replication,
        overlap_ids=overlap_ids,
        seed=args.seed,
        forbidden=forbidden,
    )
    pool_by_id = {r["pair_id"]: r for r in pool}

    g1r.write_jsonl(out_dir / "pool.jsonl", pool)
    g1r.write_jsonl(out_dir / "subsample.jsonl", subsample)
    written: dict[str, Any] = {}
    for rater, pids in assignments.items():
        aid = "a-" + g1r.digest(out_dir.name, rater, *pids)
        recs = assignment_records(
            pids,
            rater_id=rater,
            assignment_id=aid,
            set_label="live",
            pool_by_id=pool_by_id,
            queries=queries,
            chunks=chunks,
            titles=titles,
        )
        path = g1r.write_jsonl(out_dir / "assignments" / f"{rater}.jsonl", recs)
        written[rater] = {
            "path": str(path),
            "assignment_id": aid,
            "n_pairs": len(recs),
            "n_overlap": len(set(pids) & set(overlap_ids)),
            "sha256": g1r.sha256_file(path),
        }
        print(f"[assign] {rater}: {len(recs)} pairs ({written[rater]['n_overlap']} overlap)")

    cal_ids_sorted = [r["pair_id"] for r in calibration]
    cal_aid = "a-" + g1r.digest(out_dir.name, "calibration", *cal_ids_sorted)
    cal_recs = assignment_records(
        cal_ids_sorted,
        rater_id="",
        assignment_id=cal_aid,
        set_label="calibration",
        pool_by_id=pool_by_id,
        queries=queries,
        chunks=chunks,
        titles=titles,
    )
    g1r.write_jsonl(out_dir / "calibration.jsonl", cal_recs)
    g1r.write_jsonl(
        out_dir / "calibration_key.template.jsonl",
        [
            {
                "pair_id": r["pair_id"],
                "gold_grade": None,
                "rationale": "",
                "set_by": "",
            }
            for r in cal_recs
        ],
    )

    strata_sizes = Counter(_key(r) for r in candidates)
    grade_probs = [0.7, 0.2, 0.1]
    if llm_labels:
        hist = Counter(llm_labels.get(r["pair_id"], 0) for r in subsample)
        total = sum(hist.values()) or 1
        grade_probs = [hist.get(g, 0) / total for g in g1r.GRADES]
        if min(grade_probs) <= 0:  # a degenerate marginal makes the forecast useless
            grade_probs = [max(p, 0.02) for p in grade_probs]

    man = g1r.manifest_header("g1_make_pool")
    man["pooling"] = {
        "pool_depth": args.pool_depth,
        "n_cells": len(rankings),
        "cells": sorted(rankings),
        "cells_short_of_depth": short_cells,
        "n_pairs": len(pool),
        "n_queries": len({r["query_id"] for r in pool}),
        "n_excluded_prior": len(excluded & {r["pair_id"] for r in pool}),
        "mean_pairs_per_query": (
            round(len(pool) / len({r["query_id"] for r in pool}), 3) if pool else 0.0
        ),
    }
    man["subsample"] = {
        "n": len(subsample),
        "requested": args.subsample,
        "allocation": args.allocation,
        "min_per_stratum": args.min_per_stratum,
        "seed": args.seed,
        "strata_pool_sizes": dict(sorted(strata_sizes.items())),
        "strata_drawn": dict(sorted(Counter(r["stratum"] for r in subsample).items())),
        "stratified_on": ["best_rank_band"] + (["llm_grade"] if llm_labels else []),
        "size_gate": {
            "min": SUBSAMPLE_MIN,
            "max": SUBSAMPLE_MAX,
            "overridden": bool(args.allow_any_size),
        },
    }
    man["assignment"] = {
        "raters": raters,
        "replication": args.replication,
        "overlap_n": len(overlap_ids),
        "overlap_frac": round(len(overlap_ids) / len(subsample), 4) if subsample else 0.0,
        "seed": args.seed,
        "files": written,
        "total_judgments": sum(len(v) for v in assignments.values()),
        "author_conflicts_withheld": len(forbidden),
        "calibration": {
            "n": len(cal_recs),
            "assignment_id": cal_aid,
            "path": str(out_dir / "calibration.jsonl"),
        },
    }
    # Precision the design actually buys, at the realized n and marginal (§4.4).
    man["kappa_precision_forecast"] = {
        "note": (
            "Monte-Carlo SD of Cohen's κ̂ at this design point; 95% CI half-width ≈ 1.96×SD. "
            "Marginal taken from the LLM label histogram over the subsample when available."
        ),
        "grade_probs": [round(p, 4) for p in grade_probs],
        "double_rated_n": len(subsample),
        "sd_at_kappa": {
            f"{k:.1f}": round(_stats.kappa_se_forecast(len(subsample), grade_probs, k), 4)
            for k in (0.3, 0.4, 0.5, 0.6, 0.8)
        },
        "sd_at_n100_kappa_0.5": round(_stats.kappa_se_forecast(100, grade_probs, 0.5), 4),
    }
    man["inputs"] = {
        "queries": args.queries,
        "queries_sha256": g1r.sha256_file(args.queries),
        "chunks": args.chunks,
        "chunks_sha256": g1r.sha256_file(args.chunks),
        "llm_labels": args.llm_labels,
        "llm_labels_sha256": g1r.sha256_file(args.llm_labels) if args.llm_labels else None,
        "rankings": list(args.rankings),
    }
    man["blinding"] = {
        "enforced": True,
        "allowed_keys": sorted(g1r.ASSIGNMENT_ALLOWED_KEYS),
        "checked_records": sum(len(v) for v in assignments.values()) + len(cal_recs),
    }
    g1r.write_json(out_dir / "manifest.json", man)

    fc = man["kappa_precision_forecast"]
    print(
        f"\n[kappa] at n={len(subsample)} double-rated, SD(κ̂) ≈ {fc['sd_at_kappa']['0.5']} "
        f"(95% half-width ≈ {1.96 * fc['sd_at_kappa']['0.5']:.3f}); "
        f"at n=100 it would be {fc['sd_at_n100_kappa_0.5']}"
    )
    print(f"[out]   {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
