#!/usr/bin/env python
"""Rechunk stage 1 (exploratory): the report. Reads the rechunk run's rows (``alias_study_run.py``
in ``rechunk`` mode: per corpus arm, ``<runs>/<arm>/none.jsonl``, the served list of every record
under each retriever) and the private label table (``rechunk_prep.py gold-map``), and writes a
public JSON of counts, rates and Wilson 95 % intervals only.

    rechunk_stage1.py --runs RUNS_DIR --label-table TABLE.jsonl --out PUBLIC.json

**Per retriever and pair** (each pair read in its corpus arm's run, its question's top 10):

- ``A``: a chunk labelled *answers* for the pair's record (either registry) is in the top 10;
- ``M``: the pair's max-overlap chunk is in the top 10, and its rank;
- ``U``: how many top-10 chunks carry no label for the record (unjudged, or never judged);
- ``A_or_unlabelled``: ``A`` or ``U`` > 0, the upper bound on what the labels could show.

A failed call leaves its pair out of that retriever's figures (counted).

**Q1 (the lost pairs):** the confirmatory pairs whose control chunk was judged *not*, split by
their cover-any class: *recovered* (a non-primary overlapping chunk answers: ``other_only`` and
``both``) against the *others* (``none`` and ``primary_only``, each also reported alone).

**Q2 (all pairs):** by coverage band (:data:`alias_control_chunks.COVERAGE_BANDS`) and by corpus
arm. The headline is ``M`` over all pairs, and ``A`` over the pairs whose control chunk was labelled
*answers*.

**Reference.** Beside each group: the stage-A status of its pairs (their draw stratum) and the
stage-A legs their windows came from, and per retriever the share of the group whose stage-A window
was in the stage-A top 10 of that retriever on the same corpus arm (none glossary, first run).

The run summaries must be the rechunk mode's (``study.mode`` ``rechunk``, complete, the label
table's sha). Nothing in the output names a record, chunk or pair (``alias_study_common.
public_problems`` through ``alias_control_chunks.write_public``).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE / "glossbench", _HERE.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import alias_control_chunks as acc  # noqa: E402
import alias_study_common as asc  # noqa: E402
import rechunk_prep as rp  # noqa: E402

SCHEMA = "rechunk-stage1/1"
RETRIEVERS = tuple(asc.RETRIEVERS)  # default, bm25, norerank
RECOVERED = ("other_only", "both")
OTHERS = ("none", "primary_only")
RANK_BANDS = (("1", 1, 1), ("2-3", 2, 3), ("4-10", 4, 10))


# ------------------------------------------------------------------ inputs

def run_files(runs: Path, corpus_arm: str) -> tuple[Path, Path]:
    import alias_study_run as ar

    return ar.leg_files(runs, corpus_arm, asc.REPEAT_GLOSSARY, "default", 1)


def check_summary(summary: Mapping[str, Any], corpus_arm: str, table_sha: str) -> dict[str, Any]:
    """The run summary of one corpus arm: complete, the rechunk mode, every retriever, the label
    table's sha. SystemExit otherwise; returns what the report keeps."""
    study = summary.get("study") or {}
    problems = []
    if summary.get("status") != "complete":
        problems.append(f"status {summary.get('status')!r}")
    if study.get("mode") != "rechunk" or not study.get("exploratory"):
        problems.append("not a rechunk-mode run")
    if set(asc.RETRIEVERS.values()) - set(summary.get("conditions") or {}):
        problems.append("a retriever is missing")
    if (study.get("rechunk_presence") or {}).get("label_table_sha256") != table_sha:
        problems.append("run against another label table")
    if problems:
        raise SystemExit(f"REFUSED: the {corpus_arm} run: " + "; ".join(problems))
    return {"status": summary["status"], "citable": summary.get("citable"),
            "collection_build": {k: (study.get("collection_build") or {}).get(k)
                                 for k in ("n_chunks", "load_receipt_sha256", "glossary_file_sha256")},
            "export_sha256": (study.get("rechunk_presence") or {}).get("export_sha256"),
            "mapped_chunks": (study.get("rechunk_presence") or {}).get("mapped_chunks"),
            "provenance": {k: (summary.get("provenance") or {}).get(k) for k in ("commit", "dirty")}}


def read_runs(runs: Path, table: rp.LabelTable) -> tuple[dict[str, dict[tuple[str, str], dict[str, Any]]],
                                                       dict[str, Any]]:
    """``corpus arm -> (record, condition) -> row`` for every arm with a pair, and the per-arm
    run records (summary checks, file shas)."""
    rows: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    info: dict[str, Any] = {}
    for c in asc.CORPUS_ARMS:
        if not table.pairs_in(c):
            continue
        rpath, spath = run_files(runs, c)
        if not rpath.is_file() or not spath.is_file():
            raise SystemExit(f"REFUSED: no rechunk run for {c} (its pairs need one)")
        summ = json.loads(spath.read_text(encoding="utf-8"))
        info[c] = {**check_summary(summ, c, table.sha256), "rows_sha256": asc.sha256_file(rpath),
                   "summary_sha256": asc.sha256_file(spath)}
        by: dict[tuple[str, str], dict[str, Any]] = {}
        for r in rp.read_rows(rpath):
            by[(r["record_id"], r["condition"])] = r
        rows[c] = by
    return rows, info


# ------------------------------------------------------------------ per pair

def score_pair(pair: Mapping[str, Any], row: Mapping[str, Any] | None, table: rp.LabelTable) -> dict[str, Any]:
    """``A``, ``M`` (and its rank), ``U`` and the upper bound of one pair under one retriever;
    ``failed`` when the call failed (the pair is left out of the figures)."""
    if row is None:
        raise SystemExit("REFUSED: a pair's record has no row in its corpus arm's run")
    if row.get("failed") or row.get("ranked_chunk_ids") is None:
        return {"failed": True}
    top = list(row["ranked_chunk_ids"])[:asc.FOUND_K]
    rid = pair["record_id"]
    labs = [table.label(rid, c) for c in top]
    a = any(x == "answers" for x in labs)
    u = sum(x is None or x == "unjudged" for x in labs)
    m = pair["max_overlap_chunk_id"]
    rank = top.index(m) + 1 if m in top else None
    return {"failed": False, "A": a, "M": rank is not None, "M_rank": rank, "U": u, "A_or_unlabelled": a or u > 0}


def score_all(table: rp.LabelTable, rows: Mapping[str, Mapping[tuple[str, str], Mapping[str, Any]]]
              ) -> list[dict[str, Any]]:
    """One entry per pair: the pair row and its score per retriever."""
    out = []
    for p in table.pairs:
        arm_rows = rows[p["corpus_arm"]]
        out.append({"pair": p, "by": {r: score_pair(p, arm_rows.get((p["record_id"], asc.RETRIEVERS[r])), table)
                                      for r in RETRIEVERS}})
    return out


# ------------------------------------------------------------------ aggregation

def rank_bands(ranks: Sequence[int]) -> dict[str, int]:
    return {name: sum(lo <= x <= hi for x in ranks) for name, lo, hi in RANK_BANDS}


def aggregate(scored: Sequence[Mapping[str, Any]], retriever: str) -> dict[str, Any]:
    """The figures of one group under one retriever (Wilson 95 %, ``alias_control_chunks.wilson``)."""
    s = [x["by"][retriever] for x in scored]
    ok = [x for x in s if not x["failed"]]
    n = len(ok)
    ranks = [x["M_rank"] for x in ok if x["M_rank"] is not None]
    us = [x["U"] for x in ok]
    return {"pairs": len(s), "failed": len(s) - n, "n": n,
            "A": acc.wilson(sum(x["A"] for x in ok), n), "M": acc.wilson(len(ranks), n),
            "M_rank": rank_bands(ranks), "U_mean": round(sum(us) / n, 4) if n else None,
            "U_any": acc.wilson(sum(u > 0 for u in us), n),
            "A_or_unlabelled": acc.wilson(sum(x["A_or_unlabelled"] for x in ok), n)}


def reference(scored: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The stage-A reference of a group: its pairs' stage-A status and legs, and per retriever the
    share whose window was in that retriever's stage-A top 10 on the same corpus arm."""
    import alias_study_run as ar

    pairs = [x["pair"] for x in scored]
    legs: Counter[str] = Counter()
    for p in pairs:
        legs.update(lg for lg in p["stage_a_legs"] if lg in ar.JUDGING_A_LEGS)
    same = {r: acc.wilson(sum(asc.leg_id(p["corpus_arm"], asc.REPEAT_GLOSSARY, r) in p["stage_a_legs"]
                              for p in pairs), len(pairs)) for r in RETRIEVERS}
    return {"stage_a_status": dict(Counter(str(p.get("stratum")) for p in pairs)),
            "stage_a_judged_legs": dict(sorted(legs.items())), "window_in_stage_a_top10_same_arm": same}


def group(scored: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {"by_retriever": {r: aggregate(scored, r) for r in RETRIEVERS}, "reference": reference(scored)}


def split(scored: Sequence[Mapping[str, Any]], key: Callable[[Mapping[str, Any]], str],
          order: Sequence[str]) -> dict[str, Any]:
    return {k: group([x for x in scored if key(x["pair"]) == k]) for k in order}


def q1(scored: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    lost = [x for x in scored if x["pair"]["lost"]]
    cls = Counter(str(x["pair"]["cover_class"]) for x in lost)
    pick = {"recovered": RECOVERED, "others": OTHERS, "none": ("none",), "primary_only": ("primary_only",)}
    return {"lost_pairs": len(lost), "cover_class": {k: cls[k] for k in acc.COVER_SPLIT},
            "lost_without_cover": cls["None"],
            "definitions": {"recovered": list(RECOVERED), "others": list(OTHERS)},
            **{name: group([x for x in lost if x["pair"]["cover_class"] in want]) for name, want in pick.items()}}


def q2(scored: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    bands = [name for _, _, name in acc.COVERAGE_BANDS]
    answers = [x for x in scored if x["pair"]["control_label"] == "answers"]

    def band(p: Mapping[str, Any]) -> str:
        return str(p["coverage_band"])

    def arm(p: Mapping[str, Any]) -> str:
        return str(p["corpus_arm"])

    return {"headline": {"M_all_pairs": {r: aggregate(scored, r)["M"] for r in RETRIEVERS},
                         "A_control_answers_pairs": {r: aggregate(answers, r)["A"] for r in RETRIEVERS}},
            "all_pairs": {"all": group(scored), "by_coverage_band": split(scored, band, bands),
                          "by_corpus_arm": split(scored, arm, asc.CORPUS_ARMS)},
            "control_answers_pairs": {"all": group(answers), "by_coverage_band": split(answers, band, bands),
                                      "by_corpus_arm": split(answers, arm, asc.CORPUS_ARMS)},
            "control_label": dict(Counter(str(x["pair"]["control_label"]) for x in scored))}


def report(table: rp.LabelTable, rows: Mapping[str, Mapping[tuple[str, str], Mapping[str, Any]]]) -> dict[str, Any]:
    scored = score_all(table, rows)
    return {"pairs": len(scored), "records": len(table.records()),
            "pairs_by_corpus_arm": dict(Counter(p["corpus_arm"] for p in table.pairs)),
            "random_both_draws": sum(bool(p.get("random_both_draws")) for p in table.pairs),
            "q1_lost_pairs": q1(scored), "q2_all_pairs": q2(scored)}


def run(runs: Path, table_path: Path, out: Path) -> dict[str, Any]:
    table = rp.read_label_table(table_path)
    rows, info = read_runs(runs, table)
    rec = {"schema": SCHEMA, "exploratory": True,
           "measures": {"A": "a chunk labelled answers for the record (control or cover-any registry) is in the top 10",
                        "M": "the pair's max-overlap chunk is in the top 10 (rank bands beside)",
                        "U": "top-10 chunks with no label for the record (unjudged or never judged)",
                        "A_or_unlabelled": "A, or any unlabelled chunk in the top 10 (upper bound)"},
           "label_rule": table.meta.get("label_rule"), "corpus_arm_rule": table.meta.get("corpus_arm_rule"),
           "top_k": asc.FOUND_K, "interval": "Wilson 95 %, rounded outward",
           **report(table, rows), "runs": info, "label_table_sha256": table.sha256,
           "provenance": acc.public_provenance(rp.experiment_provenance())}
    forbidden = table.forbidden()
    for arm_rows in rows.values():
        for r in arm_rows.values():
            forbidden |= set(r.get("ranked_chunk_ids") or [])
    return acc.write_public(out, rec, forbidden)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs", required=True, type=Path, help="the rechunk run's out dir")
    ap.add_argument("--label-table", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path, help="the public report (counts only)")
    args = ap.parse_args(argv)
    rec = run(args.runs, args.label_table, args.out)
    q = rec["q1_lost_pairs"]
    print(json.dumps({"pairs": rec["pairs"], "lost_pairs": q["lost_pairs"], "cover_class": q["cover_class"],
                      "out": str(args.out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
