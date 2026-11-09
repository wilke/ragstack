#!/usr/bin/env python
"""Rechunk stage 1 (exploratory): the report. Reads the rechunk run's rows (``alias_study_run.py``
in ``rechunk`` mode: per corpus arm, ``<runs>/<arm>/none.jsonl``, the served list of every record
under each retriever) and the private label table (``rechunk_prep.py gold-map``), and writes a
public JSON of counts, rates and Wilson 95 % intervals only.

    rechunk_stage1.py --runs RUNS_DIR --label-table TABLE.jsonl --out PUBLIC.json \\
                      [--run-label-table RUN_TABLE.jsonl]

**Stage 1b.** The run summaries record the sha of the label table the run was made against, and a
table with another sha is refused. A merged table (``rechunk_prep.py gold-map --extra-final``)
has another sha, so it is read with ``--run-label-table`` naming the run's table: the summaries
are checked against that one, and the merged table must carry the same pair rows and every label
of it unchanged (registries may only be added, and the combined label of every existing row must
stay the same). The scoring is unchanged: a stage-1b label counts
in ``A`` and ``U`` like any other (``A_own`` still reads only the pair's own mapped chunks).

**Per retriever and pair** (each pair read in its corpus arm's run, its question's top 10):

- ``A``: a chunk labelled *answers* for the pair's record (any registry of the label table:
  control, cover-any, and on a merged table stage1b) is in the top 10. The
  labels are per record: a chunk mapped and judged for *another* pair of the same record counts;
- ``A_own``: one of *this* pair's own mapped chunks (its max-overlap chunk or a cover-any chunk)
  is labelled *answers* and in the top 10; ``A`` without ``A_own`` (``A_from_other_pairs_only``)
  is counted beside it;
- ``M``: the pair's max-overlap chunk is in the top 10, and its rank;
- ``U``: how many top-10 chunks carry no label for the record (unjudged, or never judged);
- ``A_or_unlabelled``: ``A`` or ``U`` > 0, the upper bound on what the labels could show.

A failed call leaves its pair out of that retriever's figures (counted).

**Q1 (the lost pairs):** the confirmatory pairs whose control chunk was judged *not*, read as
``all_lost`` and split by their cover-any class: *recovered* (a non-primary overlapping chunk
answers: ``other_only`` and ``both``), the *others* (``none``: no overlapping chunk answers) and
``primary_only`` on its own (only the re-read of the max-overlap chunk in the cover registry
answers: its ``A`` rests on a label that conflicts with the control registry's *not* for the same
chunk).

**Q2 (all pairs):** by coverage band (:data:`alias_control_chunks.COVERAGE_BANDS`) and by corpus
arm. The headline is ``M`` over all pairs (and ``M_window_retained``, below), and ``A`` with
``A_own`` over the pairs whose control chunk was labelled *answers*.

Each group also gives its distinct records and distinct top-10 lists ((record, corpus arm): the
pairs of one record in one arm share one top 10), and per retriever ``M`` among the pairs whose
stage-A window was in that retriever's same-arm stage-A top 10 (the counterpart of a retained
window).

**Reference.** Beside each group: the draw stratum of its pairs (the confirmatory sample's; every
pair was drawn from the stage-A *answers* stratum, so this is not a stage-A route), the stage-A
legs their windows came from, and per retriever the share of the group whose stage-A window was in
the stage-A top 10 of that retriever on the same corpus arm (none glossary, first run). A
base-paper window is always run in the base collection, even when stage A retrieved it only in a
larger arm's leg; its same-arm share is 0 (counted, ``base_windows_only_in_larger_arm_legs``).

The run summaries must be the rechunk mode's (``study.mode`` ``rechunk``, complete, no hard stop,
no gated condition, the label table's sha); each arm's ``gates`` map is carried into ``runs``.
Nothing in the output names a record, chunk or pair (``alias_study_common.public_problems``
through ``alias_control_chunks.write_public``).
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
OTHERS = ("none",)
PRIMARY_ONLY = ("primary_only",)
RANK_BANDS = (("1", 1, 1), ("2-3", 2, 3), ("4-10", 4, 10))


# ------------------------------------------------------------------ inputs

def run_files(runs: Path, corpus_arm: str) -> tuple[Path, Path]:
    import alias_study_run as ar

    return ar.leg_files(runs, corpus_arm, asc.REPEAT_GLOSSARY, "default", 1)


def check_summary(summary: Mapping[str, Any], corpus_arm: str, table_sha: str) -> dict[str, Any]:
    """The run summary of one corpus arm: complete (no hard stop), the rechunk mode, every
    retriever, no condition gated (``conditions[c]["gate"]``), the label table's sha. SystemExit
    otherwise, naming the gate; returns what the report keeps (``gates``: condition -> gate, all
    ``None`` in a report that is written)."""
    study = summary.get("study") or {}
    problems = []
    if summary.get("status") == "hard_stop" or summary.get("hard_stop"):
        problems.append(f"hard stop ({summary.get('reason') or summary.get('hard_stop')})")
    elif summary.get("status") != "complete":
        problems.append(f"status {summary.get('status')!r}")
    gates = {str(c): (v or {}).get("gate") for c, v in sorted((summary.get("conditions") or {}).items())}
    gated = {c: g for c, g in gates.items() if g}
    if gated:
        problems.append("gated condition(s): " + "; ".join(f"{c}: {g}" for c, g in gated.items()))
    if study.get("mode") != "rechunk" or not study.get("exploratory"):
        problems.append("not a rechunk-mode run")
    if set(asc.RETRIEVERS.values()) - set(summary.get("conditions") or {}):
        problems.append("a retriever is missing")
    if (study.get("rechunk_presence") or {}).get("label_table_sha256") != table_sha:
        problems.append("run against another label table")
    if problems:
        raise SystemExit(f"REFUSED: the {corpus_arm} run: " + "; ".join(problems))
    return {"status": summary["status"], "citable": summary.get("citable"), "gates": gates,
            "collection_build": {k: (study.get("collection_build") or {}).get(k)
                                 for k in ("n_chunks", "load_receipt_sha256", "glossary_file_sha256")},
            "export_sha256": (study.get("rechunk_presence") or {}).get("export_sha256"),
            "mapped_chunks": (study.get("rechunk_presence") or {}).get("mapped_chunks"),
            "provenance": {k: (summary.get("provenance") or {}).get(k) for k in ("commit", "dirty")}}


def read_runs(runs: Path, table: rp.LabelTable, run_table_sha: str | None = None
              ) -> tuple[dict[str, dict[tuple[str, str], dict[str, Any]]], dict[str, Any]]:
    """``corpus arm -> (record, condition) -> row`` for every arm with a pair, and the per-arm
    run records (summary checks, file shas). The summaries must name ``run_table_sha`` (default:
    ``table``'s own sha; see :func:`check_merged`)."""
    rows: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    info: dict[str, Any] = {}
    for c in asc.CORPUS_ARMS:
        if not table.pairs_in(c):
            continue
        rpath, spath = run_files(runs, c)
        if not rpath.is_file() or not spath.is_file():
            raise SystemExit(f"REFUSED: no rechunk run for {c} (its pairs need one)")
        summ = json.loads(spath.read_text(encoding="utf-8"))
        info[c] = {**check_summary(summ, c, run_table_sha or table.sha256), "rows_sha256": asc.sha256_file(rpath),
                   "summary_sha256": asc.sha256_file(spath)}
        by: dict[tuple[str, str], dict[str, Any]] = {}
        for r in rp.read_rows(rpath):
            by[(r["record_id"], r["condition"])] = r
        rows[c] = by
    return rows, info


# ------------------------------------------------------------------ per pair

def score_pair(pair: Mapping[str, Any], row: Mapping[str, Any] | None, table: rp.LabelTable) -> dict[str, Any]:
    """``A``, ``A_own``, ``M`` (and its rank), ``U`` and the upper bound of one pair under one
    retriever; ``failed`` when the call failed (the pair is left out of the figures)."""
    if row is None:
        raise SystemExit("REFUSED: a pair's record has no row in its corpus arm's run")
    if row.get("failed") or row.get("ranked_chunk_ids") is None:
        return {"failed": True}
    top = list(row["ranked_chunk_ids"])[:asc.FOUND_K]
    rid = pair["record_id"]
    labs = [table.label(rid, c) for c in top]
    a = any(x == "answers" for x in labs)
    m = pair["max_overlap_chunk_id"]
    own = {m, *pair["cover_any_chunk_ids"]}
    a_own = any(x == "answers" and c in own for c, x in zip(top, labs, strict=True))
    u = sum(x is None or x == "unjudged" for x in labs)
    rank = top.index(m) + 1 if m in top else None
    return {"failed": False, "A": a, "A_own": a_own, "M": rank is not None, "M_rank": rank, "U": u,
            "A_or_unlabelled": a or u > 0}


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


def same_arm_leg(pair: Mapping[str, Any], retriever: str) -> str:
    """The stage-A leg of ``retriever`` on the pair's corpus arm (none glossary, first run)."""
    return asc.leg_id(pair["corpus_arm"], asc.REPEAT_GLOSSARY, retriever)


def aggregate(scored: Sequence[Mapping[str, Any]], retriever: str) -> dict[str, Any]:
    """The figures of one group under one retriever (Wilson 95 %, ``alias_control_chunks.wilson``).
    ``records`` and ``top10_lists`` count the distinct records and (record, corpus arm) lists
    behind ``n``; ``M_window_retained`` and ``A_own_window_retained`` are ``M`` and ``A_own`` among
    the pairs whose stage-A window was in this retriever's same-arm stage-A top 10."""
    items = [x for x in scored if not x["by"][retriever]["failed"]]
    ok = [x["by"][retriever] for x in items]
    n = len(ok)
    ranks = [x["M_rank"] for x in ok if x["M_rank"] is not None]
    us = [x["U"] for x in ok]
    kept = [x["by"][retriever] for x in items if same_arm_leg(x["pair"], retriever) in x["pair"]["stage_a_legs"]]
    return {"pairs": len(scored), "failed": len(scored) - n, "n": n,
            "records": len({x["pair"]["record_id"] for x in items}),
            "top10_lists": len({(x["pair"]["record_id"], x["pair"]["corpus_arm"]) for x in items}),
            "A": acc.wilson(sum(x["A"] for x in ok), n), "A_own": acc.wilson(sum(x["A_own"] for x in ok), n),
            "A_from_other_pairs_only": acc.wilson(sum(x["A"] and not x["A_own"] for x in ok), n),
            "M": acc.wilson(len(ranks), n), "M_rank": rank_bands(ranks),
            "M_window_retained": acc.wilson(sum(x["M"] for x in kept), len(kept)),
            "A_own_window_retained": acc.wilson(sum(x["A_own"] for x in kept), len(kept)),
            "U_mean": round(sum(us) / n, 4) if n else None,
            "U_any": acc.wilson(sum(u > 0 for u in us), n),
            "A_or_unlabelled": acc.wilson(sum(x["A_or_unlabelled"] for x in ok), n)}


def reference(scored: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The stage-A reference of a group: its pairs' draw stratum (the confirmatory sample's, not a
    stage-A route) and stage-A legs, per retriever the share whose window was in that retriever's
    stage-A top 10 on the same corpus arm, and the base pairs stage A found only in a larger arm."""
    import alias_study_run as ar

    pairs = [x["pair"] for x in scored]
    legs: Counter[str] = Counter()
    for p in pairs:
        legs.update(lg for lg in p["stage_a_legs"] if lg in ar.JUDGING_A_LEGS)
    same = {r: acc.wilson(sum(same_arm_leg(p, r) in p["stage_a_legs"] for p in pairs), len(pairs))
            for r in RETRIEVERS}
    return {"draw_stratum": dict(Counter(str(p.get("draw_stratum")) for p in pairs)),
            "stage_a_judged_legs": dict(sorted(legs.items())), "window_in_stage_a_top10_same_arm": same,
            "base_windows_only_in_larger_arm_legs": sum(rp.base_only_in_larger_arm(p) for p in pairs)}


def group(scored: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {"pairs": len(scored), "records": len({x["pair"]["record_id"] for x in scored}),
            "top10_lists": len({(x["pair"]["record_id"], x["pair"]["corpus_arm"]) for x in scored}),
            "by_retriever": {r: aggregate(scored, r) for r in RETRIEVERS}, "reference": reference(scored)}


def split(scored: Sequence[Mapping[str, Any]], key: Callable[[Mapping[str, Any]], str],
          order: Sequence[str]) -> dict[str, Any]:
    return {k: group([x for x in scored if key(x["pair"]) == k]) for k in order}


def q1(scored: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    lost = [x for x in scored if x["pair"]["lost"]]
    cls = Counter(str(x["pair"]["cover_class"]) for x in lost)
    pick = {"recovered": RECOVERED, "others": OTHERS, "primary_only": PRIMARY_ONLY}
    return {"lost_pairs": len(lost), "cover_class": {k: cls[k] for k in acc.COVER_SPLIT},
            "lost_without_cover": cls["None"], "definitions": Q1_DEFINITIONS,
            "all_lost": group(lost),
            **{name: group([x for x in lost if x["pair"]["cover_class"] in want]) for name, want in pick.items()}}


Q1_DEFINITIONS: dict[str, Any] = {
    "all_lost": "every lost pair (control chunk judged not), whatever its cover-any class",
    "recovered": list(RECOVERED), "others": list(OTHERS), "primary_only": list(PRIMARY_ONLY),
    "primary_only_note": "the control registry judged the max-overlap chunk not, and the cover-any re-read "
                         "answers on the same chunk; the combined label is answers, so A (and A_own) for these "
                         "pairs rests on that conflicting re-read",
    "A_vs_A_own": "A counts any chunk labelled answers for the record, including chunks mapped for another "
                  "pair of the same record; A_own only this pair's max-overlap and cover-any chunks",
    "base_windows": "a base-paper window is always run in the base collection, even when its stage-A window "
                    "was retrieved only in a larger arm's leg (less competition than in stage A; its same-arm "
                    "stage-A reference is 0)",
}


def q2(scored: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    bands = [name for _, _, name in acc.COVERAGE_BANDS]
    answers = [x for x in scored if x["pair"]["control_label"] == "answers"]

    def band(p: Mapping[str, Any]) -> str:
        return str(p["coverage_band"])

    def arm(p: Mapping[str, Any]) -> str:
        return str(p["corpus_arm"])

    return {"headline": {"M_all_pairs": {r: aggregate(scored, r)["M"] for r in RETRIEVERS},
                         "M_window_retained": {r: aggregate(scored, r)["M_window_retained"] for r in RETRIEVERS},
                         "A_own_window_retained": {r: aggregate(scored, r)["A_own_window_retained"]
                                                   for r in RETRIEVERS},
                         "A_control_answers_pairs": {r: aggregate(answers, r)["A"] for r in RETRIEVERS},
                         "A_own_control_answers_pairs": {r: aggregate(answers, r)["A_own"] for r in RETRIEVERS}},
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
            "base_windows_only_in_larger_arm_legs": sum(rp.base_only_in_larger_arm(p) for p in table.pairs),
            "q1_lost_pairs": q1(scored), "q2_all_pairs": q2(scored)}


def check_merged(run_table: rp.LabelTable, table: rp.LabelTable) -> dict[str, Any]:
    """``table`` extends ``run_table`` (the table the run was made against) by labels only, as
    ``gold-map --extra-final`` does: the same pair rows, and every label row of ``run_table``
    present with the same per-registry labels (registries may be added) **and the same combined
    ``label``**: an added registry may never flip a label the run's figures were read against.
    SystemExit otherwise."""
    if table.pairs != run_table.pairs:
        raise SystemExit("REFUSED: --label-table has other pair rows than --run-label-table (not a merged table)")
    for key, r in run_table.labels.items():
        m = table.labels.get(key)
        regs = (m or {}).get("registries") or {}
        if m is None or any(regs.get(k) != v for k, v in (r.get("registries") or {}).items()):
            raise SystemExit("REFUSED: a label row of --run-label-table is missing or changed in --label-table")
        if m.get("label") != r.get("label"):
            raise SystemExit("REFUSED: the combined label of a --run-label-table row differs in --label-table "
                             "(an added registry relabelled an already-labelled chunk)")
    return {"run_label_table_sha256": run_table.sha256, "labels_added": len(table.labels) - len(run_table.labels),
            "extra_registries": table.meta.get("extra_registries") or []}


def run(runs: Path, table_path: Path, out: Path, run_table_path: Path | None = None) -> dict[str, Any]:
    """The report. ``run_table_path``: the label table the run was made against, when
    ``table_path`` is a merged one (``gold-map --extra-final``, stage 1b): the run summaries are
    checked against it and the merged table must extend it (:func:`check_merged`)."""
    table = rp.read_label_table(table_path)
    merged = None
    forbidden_extra: set[str] = set()
    if run_table_path is not None:
        run_table = rp.read_label_table(run_table_path)
        merged = check_merged(run_table, table)
        forbidden_extra = run_table.forbidden()
    rows, info = read_runs(runs, table, None if merged is None else merged["run_label_table_sha256"])
    rec = {"schema": SCHEMA, "exploratory": True,
           "measures": {"A": "a chunk labelled answers for the record (any registry in the label table: control, "
                             "cover-any, stage1b) is in the top 10",
                        "A_own": "one of this pair's own mapped chunks (max-overlap or cover-any) is labelled "
                                 "answers and in the top 10",
                        "A_from_other_pairs_only": "A without A_own: the answers chunk was mapped for another pair "
                                                   "of the same record",
                        "M_window_retained": "M among the pairs whose stage-A window was in the same retriever's "
                                             "same-arm stage-A top 10",
                        "A_own_window_retained": "A_own among the same pairs as M_window_retained",
                        "M": "the pair's max-overlap chunk is in the top 10 (rank bands beside)",
                        "U": "top-10 chunks with no label for the record (unjudged or never judged)",
                        "A_or_unlabelled": "A, or any unlabelled chunk in the top 10 (upper bound)"},
           "label_rule": table.meta.get("label_rule"), "corpus_arm_rule": table.meta.get("corpus_arm_rule"),
           "top_k": asc.FOUND_K, "interval": "Wilson 95 %, rounded outward",
           **report(table, rows), "runs": info, "label_table_sha256": table.sha256,
           "provenance": acc.public_provenance(rp.experiment_provenance())}
    if merged is not None:
        rec["merged_label_table"] = merged
    forbidden = table.forbidden() | forbidden_extra
    for arm_rows in rows.values():
        for r in arm_rows.values():
            forbidden |= set(r.get("ranked_chunk_ids") or [])
    return acc.write_public(out, rec, forbidden)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs", required=True, type=Path, help="the rechunk run's out dir")
    ap.add_argument("--label-table", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path, help="the public report (counts only)")
    ap.add_argument("--run-label-table", type=Path, default=None,
                    help="the table the run was made against, when --label-table is a merged (stage-1b) one")
    args = ap.parse_args(argv)
    rec = run(args.runs, args.label_table, args.out, args.run_label_table)
    q = rec["q1_lost_pairs"]
    print(json.dumps({"pairs": rec["pairs"], "lost_pairs": q["lost_pairs"], "cover_class": q["cover_class"],
                      "out": str(args.out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
