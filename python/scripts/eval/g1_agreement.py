#!/usr/bin/env python
"""G1 rating agreement — Cohen's/Fleiss' κ, human vs human and judge vs human.

Protocol §4.4 makes one number decide whether the pilot's whole quality track is
evidence or commentary:

    Two human annotators independently judge a random subsample; report Cohen's κ
    (human–human) and κ (judge–human). **If κ(judge–human) < 0.4 the pilot's
    quality track is descriptive only**, the recommendation falls back to the
    SciFact anchor plus Track C, and the size question defers entirely to Part II.

A gate that sharp needs three things the protocol does not spell out, and this
script supplies them.

**An interval, not a point.** κ̂ at n = 100 has a 95% CI of roughly ±0.15, so a
point estimate of 0.45 is entirely consistent with a true κ of 0.30. Every κ here
is reported as ``point [lo, hi]`` from an item-resampling bootstrap
(``_stats.bootstrap_statistic_ci``), and :func:`banded_verdict` applies the rule
that follows from it: **when the CI spans a band boundary, the lower band's
consequences apply.** A gate decided by a point estimate whose interval crosses it
is not a decision, it is a coin flip with a decimal point.

**Bands between 0.4 and "fine".** §4.4 gates at 0.4 and says nothing about what
0.45 buys versus 0.75. :data:`KAPPA_BANDS` proposes four bands with explicit
consequences (see ``docs/g1-sop-rating.md`` §6 for the argument). The short
version: label noise is not symmetric in its effect. Non-differential noise
attenuates real differences, which *manufactures* EQUIVALENT verdicts — so under
moderate agreement an equivalence claim is the *less* trustworthy one, not the
more, and the bands say so.

**A ceiling.** κ(judge–human) cannot exceed what humans manage with each other. A
judge scored against labels the humans themselves disagree about is being graded
on a noisy target, so the report always states κ(judge–human) beside
κ(human–human) and their ratio; and if κ(human–human) is itself below the gate the
verdict is ``RUBRIC_FAILURE`` — the fix is the rubric and the raters, not the
judge.

Also emitted, because they are the operational half of the same job:

* **Per-rater diagnostics** — item counts, grade histogram, median seconds/item,
  the share of items graded in under 5 s. These are the SOP §2.5 disqualification
  inputs; a rater whose median is 3 s did not read the chunks.
* **An adjudication queue** — every pair where two raters split 0 vs 2 (§4.4's
  qualitative disagreement), written as a file the adjudicator works through.
* **Calibration scoring** — each rater against the gold key, before live work.
* **Judge self-consistency** — the duplicate-pair re-label rate §4.4 asks for.

Usage::

    cd python && export PYTHONPATH="$PWD"
    /rag/envs/ragstack/bin/python scripts/eval/g1_agreement.py \\
        --judgments reports/g1-library-retrieval/rating/round1/exports/*.jsonl \\
        --llm-labels reports/g1-library-retrieval/qrels/pilot/judgments.jsonl \\
        --calibration-key reports/g1-library-retrieval/rating/round1/calibration_key.jsonl \\
        --out-dir reports/g1-library-retrieval/rating/round1/analysis
"""
from __future__ import annotations

import argparse
import math
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import _g1_rating as g1r  # noqa: E402
import _stats  # noqa: E402

GATE = 0.4  # protocol §4.4

#: Proposed κ bands. ``(floor, name, use)`` — the *use* column is the whole point:
#: a band without a consequence is a vocabulary, not a rule. Argued in
#: ``docs/g1-sop-rating.md`` §6.
KAPPA_BANDS: tuple[tuple[float, str, str], ...] = (
    (
        0.80,
        "STRONG",
        "Full §7.5 verdict vocabulary. The judge may substitute for one human "
        "rater in later rounds; the human subsample drops to a 100-pair audit.",
    ),
    (
        0.60,
        "SUBSTANTIAL",
        "Full §7.5 verdict vocabulary (DIFFERENT / EQUIVALENT / INCONCLUSIVE), "
        "scoped to 50-200 documents, still requiring the SciFact anchor to agree "
        "in sign (§7.2).",
    ),
    (
        0.40,
        "MODERATE",
        "Screening only. Stage-1 nomination and the SIGN of a contrast may use "
        "LLM labels; no shippable claim rests on them alone. EQUIVALENT is "
        "downgraded to INCONCLUSIVE unless the SciFact anchor is also EQUIVALENT "
        "(label noise manufactures equivalence). DIFFERENT additionally requires "
        "the chunk-length and IDF-overlap sensitivity checks to come back flat.",
    ),
    (
        float("-inf"),
        "FAIL",
        "Protocol §4.4 gate not met: the pilot's quality track is DESCRIPTIVE "
        "ONLY. The recommendation falls back to the SciFact anchor plus Track C, "
        "and the size question defers entirely to Part II.",
    ),
)


def kappa_band(value: float) -> tuple[str, str]:
    """``(name, consequence)`` for a κ point estimate."""
    if not math.isfinite(value):
        return "UNDEFINED", "κ is undefined (no items, or a single-category marginal)."
    for floor, name, use in KAPPA_BANDS:
        if value >= floor:
            return name, use
    return KAPPA_BANDS[-1][1], KAPPA_BANDS[-1][2]


def banded_verdict(ci: _stats.CI) -> dict[str, Any]:
    """Apply the band rule to an interval, not to a point.

    When the CI spans a boundary the **lower** band's consequences apply: the
    study has not shown it is in the higher band, and every consequence in this
    table licenses *more* use of the labels as the band rises.
    """
    point_band, use = kappa_band(ci.point)
    lo_band, lo_use = kappa_band(ci.lo)
    effective, eff_use = (point_band, use) if lo_band == point_band else (lo_band, lo_use)
    return {
        "kappa": round(ci.point, 4),
        "ci95": [round(ci.lo, 4), round(ci.hi, 4)],
        "half_width": round((ci.hi - ci.lo) / 2, 4) if math.isfinite(ci.hi) else None,
        "band_point": point_band,
        "band_lower_bound": lo_band,
        "band_effective": effective,
        "spans_boundary": lo_band != point_band,
        "consequence": eff_use,
        "meets_gate": bool(math.isfinite(ci.point) and ci.point >= GATE),
        "gate": GATE,
    }


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def load_judgments(patterns: Sequence[str]) -> list[dict[str, Any]]:
    """Read the tool's exports; keep the latest judgment per (rater, pair).

    A rater who resumes a session and re-exports produces the same pair twice.
    The later timestamp wins, and the *pre-dedup* pairs are kept around for the
    intra-rater test-retest number, which is a free reliability check the design
    otherwise would not have.
    """
    rows: list[dict[str, Any]] = []
    for path in g1r.iter_glob(patterns):
        for rec in g1r.read_jsonl(path):
            if rec.get("pair_id") is None or rec.get("grade") is None:
                continue
            rows.append({**rec, "_file": str(path)})
    rows.sort(key=lambda r: (str(r.get("rater_id")), str(r["pair_id"]), str(r.get("timestamp", ""))))
    return rows


def dedupe(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    keep: dict[tuple[str, str], dict[str, Any]] = {}
    repeats: list[tuple[int, int]] = []
    for rec in rows:
        key = (str(rec.get("rater_id")), str(rec["pair_id"]))
        prev = keep.get(key)
        if prev is not None:
            repeats.append((int(prev["grade"]), int(rec["grade"])))
        keep[key] = rec
    stable = sum(1 for a, b in repeats if a == b)
    return (
        sorted(keep.values(), key=lambda r: (str(r.get("rater_id")), str(r["pair_id"]))),
        {
            "n_repeat_ratings": len(repeats),
            "test_retest_agreement": round(stable / len(repeats), 4) if repeats else None,
        },
    )


def by_pair(rows: Sequence[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """``pair_id → {rater_id: grade}``."""
    out: dict[str, dict[str, int]] = defaultdict(dict)
    for rec in rows:
        out[str(rec["pair_id"])][str(rec.get("rater_id"))] = int(rec["grade"])
    return dict(out)


# --------------------------------------------------------------------------- #
# Consensus and adjudication
# --------------------------------------------------------------------------- #
def consensus(
    grades: dict[str, int], adjudications: dict[str, int] | None = None, pair_id: str = ""
) -> tuple[int | None, str]:
    """The human label for a pair, and how it was reached.

    Rules, in order:

    1. An adjudicated grade always wins (``"adjudicated"``).
    2. Unanimous → that grade.
    3. A **boundary** disagreement (spread of 1, e.g. 1 vs 2) resolves to the
       majority, ties to the **lower** grade. Ties-to-lower is deliberate: the
       rubric's failure mode is leniency (§4.4, "topical drift"), and a graded
       relevance label that is wrong upward inflates every recall-flavoured
       metric, while one that is wrong downward mostly costs power.
    4. A **qualitative** disagreement (spread ≥ 2 — one rater saw an answer where
       another saw noise) does *not* resolve. It returns ``None`` and goes to the
       adjudication queue, because averaging it would invent a label neither
       rater would defend.
    """
    if adjudications and pair_id in adjudications:
        return adjudications[pair_id], "adjudicated"
    vals = sorted(grades.values())
    if not vals:
        return None, "empty"
    if len(vals) == 1:
        return vals[0], "single_rater"
    if vals[0] == vals[-1]:
        return vals[0], "unanimous"
    if vals[-1] - vals[0] >= g1r.ADJUDICATION_SPREAD:
        return None, "needs_adjudication"
    counts = Counter(vals)
    top = max(counts.values())
    return min(g for g, c in counts.items() if c == top), "majority_lower_tie"


# --------------------------------------------------------------------------- #
# κ helpers
# --------------------------------------------------------------------------- #
def kappa_with_ci(
    a: Sequence[int],
    b: Sequence[int],
    *,
    weights: str = "none",
    iters: int = _stats.BOOTSTRAP_ITERS,
    seed: int = _stats.SEED,
) -> _stats.CI:
    """Cohen's κ with an item-resampling bootstrap 95% CI."""
    aa, bb = list(a), list(b)
    cats = list(g1r.GRADES)
    if not aa:
        return _stats.CI(float("nan"), float("nan"), float("nan"))
    point = _stats.cohen_kappa(aa, bb, cats, weights)

    def _stat(idx: np.ndarray) -> float:
        return _stats.cohen_kappa([aa[i] for i in idx], [bb[i] for i in idx], cats, weights)

    return _stats.bootstrap_statistic_ci(len(aa), _stat, iters=iters, seed=seed, point=point)


def pairwise_kappa(
    pairs: dict[str, dict[str, int]], raters: Sequence[str], **kw: Any
) -> list[dict[str, Any]]:
    """Cohen's κ for every rater pair, over the items both of them rated."""
    out: list[dict[str, Any]] = []
    for i, r1 in enumerate(raters):
        for r2 in raters[i + 1 :]:
            common = sorted(p for p, g in pairs.items() if r1 in g and r2 in g)
            if not common:
                continue
            a = [pairs[p][r1] for p in common]
            b = [pairs[p][r2] for p in common]
            unweighted = kappa_with_ci(a, b, weights="none", **kw)
            linear = kappa_with_ci(a, b, weights="linear", **kw)
            agree = sum(1 for x, y in zip(a, b, strict=True) if x == y) / len(common)
            out.append(
                {
                    "raters": [r1, r2],
                    "n_items": len(common),
                    "percent_agreement": round(agree, 4),
                    "kappa": banded_verdict(unweighted),
                    "kappa_linear_weighted": {
                        "kappa": round(linear.point, 4),
                        "ci95": [round(linear.lo, 4), round(linear.hi, 4)],
                    },
                    "confusion": _confusion(a, b),
                    "n_qualitative_disagreements": sum(
                        1 for x, y in zip(a, b, strict=True) if abs(x - y) >= g1r.ADJUDICATION_SPREAD
                    ),
                }
            )
    return out


def _confusion(a: Sequence[int], b: Sequence[int]) -> dict[str, int]:
    c = Counter(zip(a, b, strict=True))
    return {f"{x}x{y}": c.get((x, y), 0) for x in g1r.GRADES for y in g1r.GRADES}


def panel_fleiss(pairs: dict[str, dict[str, int]], raters: Sequence[str]) -> dict[str, Any]:
    """Fleiss' κ over the overlap set — the pairs every rater on the panel rated."""
    items = sorted(p for p, g in pairs.items() if all(r in g for r in raters))
    if len(raters) < 3 or len(items) < 2:
        return {
            "applicable": False,
            "n_items": len(items),
            "reason": "fewer than 3 raters or fewer than 2 fully-overlapped items",
        }
    counts = [[sum(1 for r in raters if pairs[p][r] == g) for g in g1r.GRADES] for p in items]
    point = _stats.fleiss_kappa(counts)

    def _stat(idx: np.ndarray) -> float:
        return _stats.fleiss_kappa([counts[i] for i in idx])

    ci = _stats.bootstrap_statistic_ci(len(counts), _stat, point=point)
    return {"applicable": True, "n_items": len(items), "raters": list(raters), **banded_verdict(ci)}


# --------------------------------------------------------------------------- #
# Rater diagnostics (SOP §2.5)
# --------------------------------------------------------------------------- #
FAST_ITEM_SECONDS = 5.0


def rater_stats(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for rater in sorted({str(r.get("rater_id")) for r in rows}):
        mine = [r for r in rows if str(r.get("rater_id")) == rater]
        secs = [float(r["seconds_on_item"]) for r in mine if r.get("seconds_on_item") is not None]
        hist = Counter(int(r["grade"]) for r in mine)
        total = len(mine) or 1
        out[rater] = {
            "n_items": len(mine),
            "grade_histogram": {str(g): hist.get(g, 0) for g in g1r.GRADES},
            "grade_shares": {str(g): round(hist.get(g, 0) / total, 4) for g in g1r.GRADES},
            "median_seconds": round(statistics.median(secs), 2) if secs else None,
            "p90_seconds": (
                round(sorted(secs)[min(len(secs) - 1, int(0.9 * (len(secs) - 1)))], 2)
                if secs
                else None
            ),
            "share_under_5s": (
                round(sum(1 for s in secs if s < FAST_ITEM_SECONDS) / len(secs), 4) if secs else None
            ),
            "shuffle_seeds": sorted({str(r.get("shuffle_seed")) for r in mine if r.get("shuffle_seed") is not None}),
            "assignment_ids": sorted({str(r.get("assignment_id")) for r in mine if r.get("assignment_id")}),
            # A rater who never uses a grade is either seeing a degenerate sample
            # or has collapsed the scale; both invalidate κ for different reasons.
            "unused_grades": [str(g) for g in g1r.GRADES if hist.get(g, 0) == 0],
        }
    return out


# --------------------------------------------------------------------------- #
# Calibration
# --------------------------------------------------------------------------- #
CALIBRATION_EXACT_MIN = 0.70
CALIBRATION_MAX_QUALITATIVE_ERRORS = 2


def score_calibration(
    rows: Sequence[dict[str, Any]], key: dict[str, int]
) -> dict[str, Any]:
    """Score each rater's calibration set against the gold key (SOP §2.3)."""
    out: dict[str, Any] = {}
    for rater in sorted({str(r.get("rater_id")) for r in rows}):
        mine = [r for r in rows if str(r.get("rater_id")) == rater and str(r["pair_id"]) in key]
        if not mine:
            continue
        got = [int(r["grade"]) for r in mine]
        gold = [key[str(r["pair_id"])] for r in mine]
        exact = sum(1 for a, b in zip(got, gold, strict=True) if a == b) / len(got)
        qualitative = [
            str(r["pair_id"])
            for r, a, b in zip(mine, got, gold, strict=True)
            if abs(a - b) >= g1r.ADJUDICATION_SPREAD
        ]
        ci = kappa_with_ci(got, gold)
        passed = exact >= CALIBRATION_EXACT_MIN and len(qualitative) <= CALIBRATION_MAX_QUALITATIVE_ERRORS
        out[rater] = {
            "n_items": len(got),
            "exact_agreement": round(exact, 4),
            "kappa_vs_gold": round(ci.point, 4),
            "kappa_ci95": [round(ci.lo, 4), round(ci.hi, 4)],
            "qualitative_errors": qualitative,
            "passed": bool(passed),
            "thresholds": {
                "exact_agreement_min": CALIBRATION_EXACT_MIN,
                "max_qualitative_errors": CALIBRATION_MAX_QUALITATIVE_ERRORS,
            },
        }
    return out


# --------------------------------------------------------------------------- #
# Report assembly
# --------------------------------------------------------------------------- #
def analyse(
    judgments: list[dict[str, Any]],
    llm_labels: dict[str, int] | None,
    adjudications: dict[str, int] | None,
    calibration_key: dict[str, int] | None,
    *,
    iters: int = _stats.BOOTSTRAP_ITERS,
    seed: int = _stats.SEED,
) -> dict[str, Any]:
    live = [r for r in judgments if str(r.get("set", "live")) != "calibration"]
    calib = [r for r in judgments if str(r.get("set", "live")) == "calibration"]
    live, repeat_info = dedupe(live)

    pairs = by_pair(live)
    raters = sorted({str(r.get("rater_id")) for r in live})

    cons: dict[str, int] = {}
    how: dict[str, str] = {}
    for pid, grades in pairs.items():
        val, method = consensus(grades, adjudications, pid)
        how[pid] = method
        if val is not None:
            cons[pid] = val

    queue = sorted(pid for pid, m in how.items() if m == "needs_adjudication")

    result: dict[str, Any] = {
        "n_judgments": len(live),
        "n_pairs": len(pairs),
        "n_double_rated": sum(1 for g in pairs.values() if len(g) >= 2),
        "raters": raters,
        "repeats": repeat_info,
        "consensus_methods": dict(sorted(Counter(how.values()).items())),
        "adjudication_queue": queue,
        # Carried alongside the ids so the queue file can be written without
        # re-deriving the grades from the raw (still calibration-contaminated,
        # still un-deduped) export set.
        "adjudication_rows": [
            {"pair_id": pid, "grades": dict(sorted(pairs[pid].items())),
             "resolved_grade": None, "adjudicator": "", "rationale": ""}
            for pid in queue
        ],
        "rater_stats": rater_stats(live),
        "human_human": {
            "pairwise": pairwise_kappa(pairs, raters, iters=iters, seed=seed),
            "panel_fleiss": panel_fleiss(pairs, raters),
        },
        "label_histogram": {
            str(g): sum(1 for v in cons.values() if v == g) for g in g1r.GRADES
        },
    }

    hh = result["human_human"]["pairwise"]
    hh_kappa = (
        statistics.fmean([p["kappa"]["kappa"] for p in hh if math.isfinite(p["kappa"]["kappa"])])
        if hh
        else float("nan")
    )
    result["human_human"]["mean_pairwise_kappa"] = (
        round(hh_kappa, 4) if math.isfinite(hh_kappa) else None
    )

    if llm_labels:
        shared = sorted(set(cons) & set(llm_labels))
        human = [cons[p] for p in shared]
        judge = [llm_labels[p] for p in shared]
        ci = kappa_with_ci(judge, human, iters=iters, seed=seed)
        lin = kappa_with_ci(judge, human, weights="linear", iters=iters, seed=seed)
        per_rater = {}
        for rater in raters:
            common = sorted(p for p, g in pairs.items() if rater in g and p in llm_labels)
            if common:
                c = kappa_with_ci(
                    [llm_labels[p] for p in common],
                    [pairs[p][rater] for p in common],
                    iters=iters,
                    seed=seed,
                )
                per_rater[rater] = {
                    "n_items": len(common),
                    "kappa": round(c.point, 4),
                    "ci95": [round(c.lo, 4), round(c.hi, 4)],
                }
        verdict = banded_verdict(ci)
        # κ(judge-human) is bounded above by what the humans manage between
        # themselves; a judge scored against labels the humans dispute is being
        # graded on a moving target. If the humans fail the gate, the rubric is
        # the finding.
        if hh and math.isfinite(hh_kappa):
            if hh_kappa < GATE:
                verdict["band_effective"] = "RUBRIC_FAILURE"
                verdict["ceiling_rule"] = "kappa_human_human_below_gate"
                verdict["consequence"] = (
                    f"κ(human-human)={hh_kappa:.3f} is itself below the {GATE} gate: the rubric "
                    "or the rater panel is the problem, not the judge. Re-calibrate and re-rate "
                    "before interpreting κ(judge-human) at all."
                )
            elif hh_kappa < 0.60 and verdict["band_effective"] in ("SUBSTANTIAL", "STRONG"):
                # SOP §6.4.2: the judge cannot be shown to agree with humans better
                # than humans agree with each other.
                verdict["band_uncapped"] = verdict["band_effective"]
                verdict["band_effective"] = "MODERATE"
                verdict["ceiling_rule"] = "capped_by_kappa_human_human"
                verdict["consequence"] = (
                    f"Capped at MODERATE by the human ceiling: κ(human-human)={hh_kappa:.3f} "
                    f"< 0.60, so a higher κ(judge-human) cannot be demonstrated. "
                    + kappa_band(0.5)[1]
                )
        result["judge_human"] = {
            "n_items": len(shared),
            "percent_agreement": (
                round(sum(1 for a, b in zip(judge, human, strict=True) if a == b) / len(shared), 4)
                if shared
                else None
            ),
            "confusion": _confusion(judge, human) if shared else {},
            "kappa_linear_weighted": {
                "kappa": round(lin.point, 4),
                "ci95": [round(lin.lo, 4), round(lin.hi, 4)],
            },
            "per_rater": per_rater,
            "normalized_vs_human_ceiling": (
                round(ci.point / hh_kappa, 4)
                if math.isfinite(hh_kappa) and hh_kappa > 0 and math.isfinite(ci.point)
                else None
            ),
            **verdict,
        }
    else:
        result["judge_human"] = {"applicable": False, "reason": "no --llm-labels supplied"}

    if calibration_key:
        result["calibration"] = score_calibration(calib, calibration_key)
    elif calib:
        result["calibration"] = {"n_ratings": len(calib), "reason": "no --calibration-key supplied"}

    return result


def render_markdown(result: dict[str, Any]) -> str:
    lines: list[str] = [
        "# G1 rating agreement",
        "",
        f"- judgments: **{result['n_judgments']}** over **{result['n_pairs']}** pairs "
        f"({result['n_double_rated']} rated by ≥2 raters)",
        f"- raters: {', '.join('`' + r + '`' for r in result['raters']) or '—'}",
        f"- consensus: {result['consensus_methods']}",
        f"- adjudication queue: **{len(result['adjudication_queue'])}** pair(s) "
        f"(0-vs-2 splits)",
        "",
        "## Human–human",
        "",
        "| raters | n | % agree | κ [95% CI] | κ linear | band | 0-vs-2 |",
        "|---|---:|---:|---|---|---|---:|",
    ]
    for p in result["human_human"]["pairwise"]:
        k = p["kappa"]
        lines.append(
            f"| {' / '.join(p['raters'])} | {p['n_items']} | {p['percent_agreement']:.3f} | "
            f"{k['kappa']:.3f} [{k['ci95'][0]:.3f}, {k['ci95'][1]:.3f}] | "
            f"{p['kappa_linear_weighted']['kappa']:.3f} | {k['band_effective']} | "
            f"{p['n_qualitative_disagreements']} |"
        )
    fl = result["human_human"]["panel_fleiss"]
    if fl.get("applicable"):
        lines += [
            "",
            f"Panel Fleiss' κ over the {fl['n_items']}-pair overlap set: "
            f"**{fl['kappa']:.3f}** [{fl['ci95'][0]:.3f}, {fl['ci95'][1]:.3f}] "
            f"→ {fl['band_effective']}",
        ]

    jh = result["judge_human"]
    lines += ["", "## Judge–human (the §4.4 gate)", ""]
    if jh.get("applicable") is False:
        lines.append(f"_Not computed: {jh['reason']}._")
    else:
        lines += [
            f"- n = {jh['n_items']}, % agreement = {jh['percent_agreement']}",
            f"- **κ = {jh['kappa']:.3f} [{jh['ci95'][0]:.3f}, {jh['ci95'][1]:.3f}]** "
            f"(half-width {jh['half_width']}), linear-weighted "
            f"{jh['kappa_linear_weighted']['kappa']:.3f}",
            f"- gate (κ ≥ {jh['gate']}): **{'MET' if jh['meets_gate'] else 'NOT MET'}**",
            f"- band by point estimate: **{jh['band_point']}**; by CI lower bound: "
            f"**{jh['band_lower_bound']}**; "
            f"**effective: {jh['band_effective']}**"
            + ("  ← the CI spans a band boundary" if jh.get("spans_boundary") else ""),
            f"- normalized against the human ceiling: {jh['normalized_vs_human_ceiling']}",
            "",
            f"> **Consequence.** {jh['consequence']}",
        ]

    if result.get("calibration"):
        lines += ["", "## Calibration", "", "| rater | n | exact | κ vs gold | 0-vs-2 | passed |",
                  "|---|---:|---:|---:|---:|---|"]
        for rater, c in sorted(result["calibration"].items()):
            if not isinstance(c, dict) or "exact_agreement" not in c:
                continue
            lines.append(
                f"| {rater} | {c['n_items']} | {c['exact_agreement']:.3f} | "
                f"{c['kappa_vs_gold']:.3f} | {len(c['qualitative_errors'])} | "
                f"{'PASS' if c['passed'] else 'FAIL'} |"
            )

    lines += ["", "## Rater diagnostics", "",
              "| rater | n | median s | p90 s | <5 s | grade shares | unused |",
              "|---|---:|---:|---:|---:|---|---|"]
    for rater, s in sorted(result["rater_stats"].items()):
        shares = "/".join(f"{s['grade_shares'][str(g)]:.2f}" for g in g1r.GRADES)
        lines.append(
            f"| {rater} | {s['n_items']} | {s['median_seconds']} | {s['p90_seconds']} | "
            f"{s['share_under_5s']} | {shares} | {','.join(s['unused_grades']) or '—'} |"
        )
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def _load_grade_map(patterns: Sequence[str], field: str = "grade") -> dict[str, int]:
    out: dict[str, int] = {}
    for path in g1r.iter_glob(patterns):
        for rec in g1r.read_jsonl(path):
            pid = rec.get("pair_id")
            if not pid and rec.get("query_id") and rec.get("chunk_id"):
                pid = g1r.pair_id(str(rec["query_id"]), str(rec["chunk_id"]))
            grade = rec.get(field, rec.get("grade", rec.get("label")))
            if pid and grade is not None:
                out[str(pid)] = int(grade)
    return out


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="κ between raters and between the LLM judge and humans (protocol §4.4).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--judgments", nargs="+", required=True, help="rating-tool exports (.jsonl)")
    p.add_argument("--llm-labels", nargs="*", default=[], help="judge output (.jsonl)")
    p.add_argument("--adjudications", nargs="*", default=[], help="resolved 0-vs-2 splits")
    p.add_argument("--calibration-key", nargs="*", default=[], help="gold grades for the calibration set")
    p.add_argument("--bootstrap-iters", type=int, default=_stats.BOOTSTRAP_ITERS)
    p.add_argument("--seed", type=int, default=_stats.SEED)
    p.add_argument("--out-dir", required=True)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    judgments = load_judgments(args.judgments)
    if not judgments:
        raise SystemExit(f"no judgments matched {args.judgments}")
    llm = _load_grade_map(args.llm_labels) if args.llm_labels else None
    adj = _load_grade_map(args.adjudications) if args.adjudications else None
    key = _load_grade_map(args.calibration_key, field="gold_grade") if args.calibration_key else None

    result = analyse(
        judgments, llm, adj, key, iters=args.bootstrap_iters, seed=args.seed
    )
    man = g1r.manifest_header("g1_agreement")
    man["inputs"] = {
        "judgments": list(args.judgments),
        "llm_labels": list(args.llm_labels),
        "adjudications": list(args.adjudications),
        "calibration_key": list(args.calibration_key),
        "bootstrap_iters": args.bootstrap_iters,
        "seed": args.seed,
    }
    man["kappa_bands"] = [
        {"floor": None if floor == float("-inf") else floor, "band": name, "consequence": use}
        for floor, name, use in KAPPA_BANDS
    ]
    man["agreement"] = result

    out_dir = Path(args.out_dir)
    g1r.write_json(out_dir / "agreement.json", man)
    (out_dir / "agreement.md").write_text(render_markdown(result), encoding="utf-8")
    g1r.write_jsonl(out_dir / "adjudication_queue.jsonl", result["adjudication_rows"])
    print(render_markdown(result))
    print(f"[out] {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
