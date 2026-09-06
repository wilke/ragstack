"""Stage 0 item 8 -- the SCORER for the two-independent-reader R-dev read (SPEC SS6.6).

``s0_rdev.py`` produced the draw and the two blank verdict sheets and stopped there, on
purpose: **no agent may read the R-dev pairs or invent a verdict.** This module is the
other half -- it consumes verdicts that *humans wrote* and turns them into the statistics
SS6.6.3 names and the SS6.6.4 acceptance table.

It is deliberately incapable of manufacturing a result. It never opens a document, never
looks at a span, and refuses (exit 2) when fewer than 10 pairs carry a verdict from both
readers -- which is exactly what happens if it is pointed at the blank sheets that ship in
``artifacts/``. Running it today is supposed to fail, and it does.

What it computes, all of it defined beside the number in the report:

* **kappa(A-B)** -- Cohen's kappa on the 6-category pair-level verdict, 95% percentile
  bootstrap over pairs (10,000 resamples, seed 20260917), plus raw percent agreement.
* the **binary collapse** {correct, correctly-none} vs {wrong-location, non-minimal,
  missed-evidence}, with ``ambiguous`` excluded and the exclusion counted.
* **per-stratum kappa** where n >= 10, else "n too small" -- the strata are the ones
  ``rdev_sample.json`` recorded before any pair was read.
* **rates**: label-error (wrong-location + non-minimal), missed-evidence, correctly-none,
  ambiguous -- over the ADJUDICATED verdicts when ``--adjudicated`` is supplied (SS6.6.3:
  the adjudicated verdict is the one used), else over each reader separately.
* **kappa(labeler-human)** -- SS6.6.4's kappa(Scout-human), on the binary "does this
  document contain localizable evidence", with positive-class agreement reported in both
  directions because kappa is deflated by this endpoint's skewed prevalence (SS6.6.3).
* the **SS6.6.4 acceptance table**, thresholds and consequence text reproduced from the
  SPEC, one row per criterion, PASS / FAIL / NOT-EVALUABLE.

Usage::

    python3 s0_rdev_score.py --a artifacts/rdev_verdicts_A.csv \\
                             --b artifacts/rdev_verdicts_B.csv \\
                             --sample artifacts/rdev_sample.json \\
                             --labels artifacts/labels-dev.jsonl \\
                             [--adjudicated artifacts/rdev_verdicts_ADJ.csv] \\
                             [--extra scout_grader=path/to/grader.csv] \\
                             [--out report.json] [--md report.md]

No network, no store client, no GPU. ``scipy`` is absent from the pinned environment, so
every statistic comes from ``s0_math.py``.
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys

import s0_math as M

HERE = pathlib.Path(__file__).resolve().parent
ARTIFACTS = HERE / "artifacts"

# ``s0_common`` pulls in ``stage1_common`` (not present in every checkout) and creates the
# big scratch tree; the scorer needs neither, so its paths are picked up when they are
# importable and defaulted when they are not.
try:                                                   # pragma: no cover - env dependent
    import s0_common as C
    WORK = C.WORK
    ARTIFACTS = getattr(C, "ARTIFACTS", ARTIFACTS)
except Exception:                                      # pragma: no cover - env dependent
    C = None
    WORK = ARTIFACTS

# ---------------------------------------------------------------- vocabulary (SS6.6.2)
VERDICTS = ("correct", "wrong-location", "non-minimal", "missed-evidence",
            "correctly-none", "ambiguous")
VIDX = {v: i for i, v in enumerate(VERDICTS)}

# binary collapse -- the task's mapping, and the one SS6.6.4's label-error row implies
BIN_ACCEPTABLE = ("correct", "correctly-none")
BIN_ERROR = ("wrong-location", "non-minimal", "missed-evidence")
BIN_EXCLUDED = ("ambiguous",)

# "is there localizable evidence in this document at all" -- the human side of
# kappa(Scout-human). ``missed-evidence`` is evidence-exists by construction: the reader
# found evidence the labeler did not supply.
EVIDENCE_EXISTS = ("correct", "wrong-location", "non-minimal", "missed-evidence")
EVIDENCE_NONE = ("correctly-none",)
EVIDENCE_EXCLUDED = ("ambiguous",)

SEED_RDEV_SCORE = 20260917
N_BOOT = 10000
MIN_SCORED = 10
STRATUM_MIN = 10

DEFS = {
    "kappa": "Cohen's kappa = (po - pe)/(1 - pe); po = fraction of pairs on which the two "
             "raters recorded the same verdict, pe = agreement expected from the raters' "
             "own marginals. Chance-corrected: on skewed marginals a po of 0.90 can be a "
             "kappa near 0, and §6.6.4 gates on kappa, never on po.",
    "kappa_ci": f"95% percentile bootstrap over PAIRS ({N_BOOT} resamples, seed "
                f"{SEED_RDEV_SCORE}); both readers' verdicts travel together in a resample "
                f"because the pair is the independent unit.",
    "percent_agreement": "po -- the raw fraction of scored pairs on which the two raters "
                         "wrote the same verdict. Reported for transparency; it is NOT the "
                         "gate statistic.",
    "binary": "6 categories collapsed to {label-acceptable: correct, correctly-none} vs "
              "{label-error-or-omission: wrong-location, non-minimal, missed-evidence}. "
              "'ambiguous' is EXCLUDED from the binary (the count of exclusions is "
              "reported); a pair is excluded if either rater called it ambiguous.",
    "per_stratum": f"kappa within each stratum recorded by rdev_sample.json before any "
                   f"pair was read; computed only where n >= {STRATUM_MIN}, else "
                   f"'n too small'.",
    "label_error_rate": "(wrong-location + non-minimal) / scored -- §6.6.4's "
                        "label-error rate. Gate is on its Wilson 95% upper bound.",
    "missed_evidence_rate": "missed-evidence / scored -- §6.6.2's omission rate, reported "
                            "separately from the label-error rate because it biases a "
                            "CONTRAST rather than a level. Gate is on its Wilson upper.",
    "correctly_none_rate": "correctly-none / scored -- pairs where the labeler returned "
                           "'no localizable evidence' and the reader agreed.",
    "ambiguous_rate": "ambiguous / scored -- pairs the reader could not resolve; §6.6.1 "
                      "requires a sensitivity with these dropped.",
    "wilson": "Wilson score interval. Two bounds are printed: the upper end of the "
              "two-sided 95% interval (the LARGER, and the one the acceptance table uses, "
              "as the conservative reading of 'Wilson 95% upper') and the one-sided 95% "
              "upper bound. Neither is a rule-of-three approximation.",
    "kappa_labeler_human": "kappa on the binary 'does this document contain localizable "
                           "evidence'. LABELER positive = the labels file lists >= 1 "
                           "evidence set for the pair, negative = an empty set list (the "
                           "'no localizable evidence' verdict). HUMAN positive = verdict in "
                           f"{EVIDENCE_EXISTS}; HUMAN negative = {EVIDENCE_NONE}; "
                           f"{EVIDENCE_EXCLUDED} EXCLUDED.",
    "positive_class_agreement": "§6.6.3: reported beside kappa because kappa is deflated "
                                "by prevalence and this endpoint's prevalence is skewed. "
                                "human->labeler = of the pairs the human called positive, "
                                "the fraction the labeler also called positive. "
                                "labeler->human = of the pairs the labeler called positive, "
                                "the fraction the human also called positive.",
    "unread": "A blank verdict cell is NOT YET READ, never a verdict. A pair is scored only "
              "when BOTH readers recorded a verdict from the vocabulary; 'unread' counts "
              "blank cells and 'missing' counts sample pairs absent from the reader's file.",
}


# ------------------------------------------------------------------------ inputs
def read_verdicts(path) -> dict[str, dict]:
    """Read a ``pair_id,verdict,notes`` sheet. Blank verdict => not yet read (None)."""
    path = pathlib.Path(path)
    rows: dict[str, dict] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        rd = csv.DictReader(fh)
        if rd.fieldnames is None or "pair_id" not in rd.fieldnames \
                or "verdict" not in rd.fieldnames:
            raise SystemExit(f"{path}: header must be pair_id,verdict,notes "
                             f"(got {rd.fieldnames})")
        for i, r in enumerate(rd, 2):
            pid = (r.get("pair_id") or "").strip()
            if not pid:
                continue
            v = (r.get("verdict") or "").strip().lower()
            if v and v not in VIDX:
                raise SystemExit(
                    f"{path}:{i}: verdict {v!r} is not in the SS6.6.2 vocabulary "
                    f"{list(VERDICTS)}")
            if pid in rows:
                raise SystemExit(f"{path}:{i}: duplicate pair_id {pid!r}")
            rows[pid] = {"verdict": v or None, "notes": (r.get("notes") or "").strip()}
    return rows


def read_sample(path) -> tuple[list[str], dict[str, str], dict]:
    """(ordered pair ids, pair_id -> stratum, the draw's own metadata)."""
    meta = json.loads(pathlib.Path(path).read_text())
    order, strat = [], {}
    for p in meta["pairs"]:
        pid = f"{p['topic']}__{p['docno']}"
        order.append(pid)
        strat[pid] = p["stratum"]
    return order, strat, meta


def read_labeler(path) -> dict[str, bool]:
    """pair_id -> labeler positive? (>= 1 evidence set). Empty ``sets`` is the labeler's
    'no localizable evidence' verdict -- SS6.6.1 makes that a legal verdict, not a gap."""
    out: dict[str, bool] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            out[f"{r['topic']}__{r['docno']}"] = bool(r.get("sets"))
    return out


# ------------------------------------------------------------------- statistics
def _kappa_block(a: list[str], b: list[str], cats: tuple[str, ...],
                 seed: int = SEED_RDEV_SCORE) -> dict:
    idx = {c: i for i, c in enumerate(cats)}
    ai = [idx[x] for x in a]
    bi = [idx[x] for x in b]
    k = len(cats)
    kap, po, pe = M.cohen_kappa(ai, bi, k)
    out = {"n": len(ai), "categories": list(cats), "kappa": kap,
           "percent_agreement": po, "expected_agreement": pe,
           "confusion": M.confusion(ai, bi, k).tolist()}
    if len(ai) >= 2:
        lo, hi, _ks = M.boot_kappa_ci(ai, bi, k, n_boot=N_BOOT, seed=seed)
        out["kappa_ci95"] = [lo, hi]
        out["kappa_ci_method"] = DEFS["kappa_ci"]
    else:
        out["kappa_ci95"] = None
        out["kappa_ci_method"] = "n < 2 -- no bootstrap"
    return out


def _binary_collapse(va: list[str], vb: list[str]) -> dict:
    """Collapse to acceptable/error, dropping any pair either rater called ambiguous."""
    ba, bb, excluded = [], [], 0
    for x, y in zip(va, vb):
        if x in BIN_EXCLUDED or y in BIN_EXCLUDED:
            excluded += 1
            continue
        ba.append("label-acceptable" if x in BIN_ACCEPTABLE else "label-error")
        bb.append("label-acceptable" if y in BIN_ACCEPTABLE else "label-error")
    cats = ("label-acceptable", "label-error")
    blk = _kappa_block(ba, bb, cats) if ba else {
        "n": 0, "kappa": float("nan"), "percent_agreement": float("nan"),
        "kappa_ci95": None, "categories": list(cats), "confusion": [[0, 0], [0, 0]]}
    blk["ambiguous_excluded"] = excluded
    blk["definition"] = DEFS["binary"]
    return blk


def _rates(verdicts: list[str], who: str) -> dict:
    n = len(verdicts)
    cnt = {v: sum(1 for x in verdicts if x == v) for v in VERDICTS}

    def rate(k: int, key: str) -> dict:
        two = M.wilson(k, n, 0.95)
        one = M.wilson(k, n, 0.95, one_sided=True)
        return {"k": k, "n": n, "rate": (k / n) if n else float("nan"),
                "wilson95_two_sided": list(two), "wilson95_upper": two[1],
                "wilson95_upper_one_sided": one[1], "definition": DEFS[key]}

    return {
        "source": who,
        "n": n,
        "counts": cnt,
        "label_error_rate": rate(cnt["wrong-location"] + cnt["non-minimal"],
                                 "label_error_rate"),
        "missed_evidence_rate": rate(cnt["missed-evidence"], "missed_evidence_rate"),
        "correctly_none_rate": rate(cnt["correctly-none"], "correctly_none_rate"),
        "ambiguous_rate": rate(cnt["ambiguous"], "ambiguous_rate"),
        "wilson_note": DEFS["wilson"],
    }


def _labeler_human(pids: list[str], human: dict[str, str],
                   labeler: dict[str, bool]) -> dict:
    lab, hum, excluded, unknown = [], [], 0, []
    for pid in pids:
        v = human[pid]
        if v in EVIDENCE_EXCLUDED:
            excluded += 1
            continue
        if pid not in labeler:
            unknown.append(pid)
            continue
        hum.append("evidence-exists" if v in EVIDENCE_EXISTS else "no-evidence")
        lab.append("evidence-exists" if labeler[pid] else "no-evidence")
    cats = ("evidence-exists", "no-evidence")
    blk = _kappa_block(lab, hum, cats) if lab else {
        "n": 0, "kappa": float("nan"), "percent_agreement": float("nan"),
        "kappa_ci95": None, "categories": list(cats), "confusion": [[0, 0], [0, 0]]}
    hp = sum(1 for x in hum if x == "evidence-exists")
    lp = sum(1 for x in lab if x == "evidence-exists")
    both = sum(1 for x, y in zip(lab, hum)
               if x == "evidence-exists" and y == "evidence-exists")
    blk.update({
        "ambiguous_excluded": excluded,
        "pairs_absent_from_labels_file": unknown,
        "confusion_axes": "rows = labeler, cols = human",
        "positive_class_agreement": {
            "human_to_labeler": (both / hp) if hp else None,
            "labeler_to_human": (both / lp) if lp else None,
            "n_human_positive": hp, "n_labeler_positive": lp, "n_both_positive": both,
            "definition": DEFS["positive_class_agreement"]},
        "mapping": DEFS["kappa_labeler_human"],
    })
    return blk


# ---------------------------------------------------------------- acceptance table
def _fmt(x, n=4):
    if x is None:
        return "—"
    if isinstance(x, float):
        return "nan" if x != x else f"{x:.{n}f}"
    return str(x)


def acceptance_table(kappa_hh: float | None, kappa_hh_ci, kappa_sh: float | None,
                     kappa_sh_ci, pos_agree: dict | None, rates: dict | None) -> list[dict]:
    """SS6.6.4, thresholds and consequence text reproduced from the SPEC verbatim.

    Polarity is explicit per row: most rows state a TRIGGER (the consequence fires when the
    condition is met), so PASS means the condition is NOT met. Row 5 is a PERMISSION, so
    PASS means the condition IS met. NOT-EVALUABLE means this read does not produce the
    input -- it is never a silent pass.
    """
    rows: list[dict] = []

    def add(stat, value_text, cond, consequence, verdict, note="", polarity="trigger"):
        rows.append({"statistic": stat, "condition": cond, "observed": value_text,
                     "consequence": consequence, "verdict": verdict,
                     "polarity": polarity, "note": note})

    hh = kappa_hh
    hh_txt = f"{_fmt(hh)}" + (f" (95% CI [{_fmt(kappa_hh_ci[0])}, {_fmt(kappa_hh_ci[1])}])"
                              if kappa_hh_ci else "")
    if hh is None or hh != hh:
        add("κ(human–human)", "—", "< 0.40",
            "`RUBRIC_FAILURE` — the rubric is not applicable by humans; the study stops; "
            "no amount of model agreement rescues it", "NOT-EVALUABLE",
            "no κ(A–B) was computable")
        add("κ(human–human)", "—", "0.40 – 0.60",
            "rubric revised once (revision dated, diffed, sha256 recorded), R-dev re-read "
            "on a fresh ≥ 100-pair draw; a second shortfall is `RUBRIC_FAILURE`",
            "NOT-EVALUABLE", "no κ(A–B) was computable")
    else:
        add("κ(human–human)", hh_txt, "< 0.40",
            "`RUBRIC_FAILURE` — the rubric is not applicable by humans; the study stops; "
            "no amount of model agreement rescues it",
            "FAIL" if hh < 0.40 else "PASS",
            _ci_note(kappa_hh_ci, 0.40))
        add("κ(human–human)", hh_txt, "0.40 – 0.60",
            "rubric revised once (revision dated, diffed, sha256 recorded), R-dev re-read "
            "on a fresh ≥ 100-pair draw; a second shortfall is `RUBRIC_FAILURE`",
            "FAIL" if 0.40 <= hh < 0.60 else "PASS",
            _ci_note(kappa_hh_ci, 0.60))

    sh = kappa_sh
    sh_txt = f"{_fmt(sh)}" + (f" (95% CI [{_fmt(kappa_sh_ci[0])}, {_fmt(kappa_sh_ci[1])}])"
                              if kappa_sh_ci else "")
    pa = None
    if pos_agree:
        cand = [v for v in (pos_agree.get("human_to_labeler"),
                            pos_agree.get("labeler_to_human")) if v is not None]
        pa = min(cand) if cand else None
    if sh is None or sh != sh:
        for cond, cons in (
            ("< 0.40", "stop; Scout is not a usable labeler for this rubric"),
            ("0.40 – 0.60", "proceed **only** at capped claim strength: every confirmatory "
                            "verdict is reported `MODERATE`, and the abstract may not state "
                            "a decision as established"),
        ):
            add("κ(Scout–human)", "—", cond, cons, "NOT-EVALUABLE",
                "no κ(labeler–human) was computable")
        add("κ(Scout–human) ≥ 0.60 *or* positive-class agreement ≥ 0.85", "—",
            "≥ 0.60 / ≥ 0.85", "full-strength claims permitted", "NOT-EVALUABLE",
            "no κ(labeler–human) was computable", polarity="permission")
    else:
        add("κ(Scout–human)", sh_txt, "< 0.40",
            "stop; Scout is not a usable labeler for this rubric",
            "FAIL" if sh < 0.40 else "PASS", _ci_note(kappa_sh_ci, 0.40))
        add("κ(Scout–human)", sh_txt, "0.40 – 0.60",
            "proceed **only** at capped claim strength: every confirmatory verdict is "
            "reported `MODERATE`, and the abstract may not state a decision as established",
            "FAIL" if 0.40 <= sh < 0.60 else "PASS", _ci_note(kappa_sh_ci, 0.60))
        add("κ(Scout–human) ≥ 0.60 *or* positive-class agreement ≥ 0.85",
            f"κ = {_fmt(sh)}; min positive-class agreement = {_fmt(pa)}",
            "≥ 0.60 / ≥ 0.85", "full-strength claims permitted",
            "PASS" if (sh >= 0.60 or (pa is not None and pa >= 0.85)) else "FAIL",
            "polarity is a PERMISSION: PASS = full-strength claims are permitted; FAIL = "
            "they are not (the row above says what is permitted instead)",
            polarity="permission")

    if rates is None:
        add("label-error rate (`wrong-location` + `non-minimal`, Wilson upper)", "—",
            "> 0.10",
            "relabel the affected stratum with the revised prompt; if still > 0.10, the "
            "study reports label-limited and no NI conclusion is drawn", "NOT-EVALUABLE",
            "no adjudicated or reader verdict set was scored")
        add("`missed-evidence` rate (Wilson upper)", "—", "> 0.15",
            "pool bias is unbounded → the evidence-recall secondary (§7.5) is promoted to a "
            "reported limitation and NI claims are downgraded to "
            "UNRESOLVED-BY-LABEL-OMISSION", "NOT-EVALUABLE",
            "no adjudicated or reader verdict set was scored")
    else:
        le = rates["label_error_rate"]
        me = rates["missed_evidence_rate"]
        add("label-error rate (`wrong-location` + `non-minimal`, Wilson upper)",
            f"p̂ = {_fmt(le['rate'])}, Wilson 95% upper = {_fmt(le['wilson95_upper'])} "
            f"(one-sided {_fmt(le['wilson95_upper_one_sided'])}) [{rates['source']}]",
            "> 0.10",
            "relabel the affected stratum with the revised prompt; if still > 0.10, the "
            "study reports label-limited and no NI conclusion is drawn",
            "FAIL" if le["wilson95_upper"] > 0.10 else "PASS",
            "the two-sided Wilson upper governs (the conservative reading); the one-sided "
            "bound is printed beside it")
        add("`missed-evidence` rate (Wilson upper)",
            f"p̂ = {_fmt(me['rate'])}, Wilson 95% upper = {_fmt(me['wilson95_upper'])} "
            f"(one-sided {_fmt(me['wilson95_upper_one_sided'])}) [{rates['source']}]",
            "> 0.15",
            "pool bias is unbounded → the evidence-recall secondary (§7.5) is promoted to a "
            "reported limitation and NI claims are downgraded to "
            "UNRESOLVED-BY-LABEL-OMISSION",
            "FAIL" if me["wilson95_upper"] > 0.15 else "PASS",
            "the two-sided Wilson upper governs")

    add("hallucinated-span rate (§6.4 rule 2)", "—", "> 0.05", "stop (unchanged)",
        "NOT-EVALUABLE",
        "not produced by the human read — it comes from the §6.4 deterministic checker "
        "(artifacts/label_gates.json), not from this scorer")
    add("self-consistency (§6.4 rule 4)", "—", "< 0.90", "stop (unchanged)",
        "NOT-EVALUABLE",
        "not produced by the human read — it comes from the §6.4 duplicate-relabel run "
        "(artifacts/label_gates.json), not from this scorer")
    return rows


def _ci_note(ci, thr: float) -> str:
    if not ci or ci[0] != ci[0]:
        return ""
    if ci[0] < thr <= ci[1]:
        return f"the 95% CI straddles {thr:.2f} — the point estimate decides the row, but " \
               f"the read does not separate the two sides of this threshold"
    return ""


# ------------------------------------------------------------------------- report
def build_report(a_path, b_path, sample_path, labels_path,
                 adjudicated_path=None, extras: dict | None = None) -> dict:
    order, strat, meta = read_sample(sample_path)
    A = read_verdicts(a_path)
    B = read_verdicts(b_path)
    labeler = read_labeler(labels_path)

    def coverage(rows: dict, name: str) -> dict:
        present = [p for p in order if p in rows]
        return {"reader": name,
                "in_sheet": len(rows),
                "sample_pairs": len(order),
                "read": sum(1 for p in present if rows[p]["verdict"]),
                "unread_blank": sum(1 for p in present if not rows[p]["verdict"]),
                "missing_from_sheet": [p for p in order if p not in rows],
                "not_in_sample": sorted(set(rows) - set(order)),
                "definition": DEFS["unread"]}

    cov = {"A": coverage(A, "A"), "B": coverage(B, "B")}

    scored = [p for p in order
              if A.get(p, {}).get("verdict") and B.get(p, {}).get("verdict")]
    va = [A[p]["verdict"] for p in scored]
    vb = [B[p]["verdict"] for p in scored]

    rep: dict = {
        "what": "R-dev two-reader read — SPEC-confirmation-run.md §6.6 scoring",
        "inputs": {"a": str(a_path), "b": str(b_path), "sample": str(sample_path),
                   "labels": str(labels_path),
                   "adjudicated": str(adjudicated_path) if adjudicated_path else None,
                   "extras": {k: str(v) for k, v in (extras or {}).items()}},
        "draw": {k: meta[k] for k in ("seed", "target", "drawn", "strata_definition",
                                      "drawn_by_stratum", "shortfalls") if k in meta},
        "seed_scoring": SEED_RDEV_SCORE,
        "n_boot": N_BOOT,
        "coverage": cov,
        "n_scored": len(scored),
        "min_scored_required": MIN_SCORED,
        "scored_by_stratum": {s: sum(1 for p in scored if strat[p] == s)
                              for s in sorted({strat[p] for p in order})},
        "sample_by_stratum": {s: sum(1 for p in order if strat[p] == s)
                              for s in sorted({strat[p] for p in order})},
    }
    if len(scored) < MIN_SCORED:
        rep["status"] = (f"REFUSED — fewer than {MIN_SCORED} scored pairs "
                         f"({len(scored)}). No κ is reported. The R-dev read is still "
                         f"PENDING-HUMAN.")
        rep["kappa_reported"] = False
        return rep

    rep["status"] = "SCORED"
    rep["kappa_reported"] = True

    # ---- κ(A–B), 6-category and binary
    hh = _kappa_block(va, vb, VERDICTS)
    hh["definition"] = DEFS["kappa"]
    hh["percent_agreement_definition"] = DEFS["percent_agreement"]
    hh["confusion_axes"] = "rows = reader A, cols = reader B"
    rep["kappa_human_human_6cat"] = hh
    rep["kappa_human_human_binary"] = _binary_collapse(va, vb)

    # ---- per-stratum
    per: dict = {"definition": DEFS["per_stratum"], "strata": {}}
    for s in rep["scored_by_stratum"]:
        ix = [i for i, p in enumerate(scored) if strat[p] == s]
        if len(ix) < STRATUM_MIN:
            per["strata"][s] = {"n": len(ix), "kappa": None,
                                "note": f"n too small (n = {len(ix)} < {STRATUM_MIN})"}
            continue
        blk = _kappa_block([va[i] for i in ix], [vb[i] for i in ix], VERDICTS,
                           seed=SEED_RDEV_SCORE + 1)
        per["strata"][s] = blk
    rep["kappa_per_stratum_6cat"] = per

    # ---- rates: adjudicated if given, else each reader
    rate_sets: dict = {}
    governing = None
    if adjudicated_path:
        ADJ = read_verdicts(adjudicated_path)
        adj_scored = [p for p in scored if ADJ.get(p, {}).get("verdict")]
        missing = [p for p in scored if not ADJ.get(p, {}).get("verdict")]
        r = _rates([ADJ[p]["verdict"] for p in adj_scored], "adjudicated")
        r["pairs_scored_but_not_adjudicated"] = missing
        r["note"] = ("§6.6.3: the adjudicated verdict is the one used; the reported κ stays "
                     "the PRE-adjudication κ(A–B) above.")
        rate_sets["adjudicated"] = r
        governing = r
        human_for_labeler = {p: ADJ[p]["verdict"] for p in adj_scored}
        human_src = "adjudicated"
    else:
        rate_sets["A"] = _rates(va, "reader A")
        rate_sets["B"] = _rates(vb, "reader B")
        rate_sets["_note"] = ("no --adjudicated supplied: rates are reported per reader. "
                              "§6.6.3 wants the adjudicated verdicts here; the acceptance "
                              "table below is evaluated on the WORSE of the two readers, "
                              "which is the conservative stand-in and is labelled as such.")
        worse = max((rate_sets["A"], rate_sets["B"]),
                    key=lambda r: (r["label_error_rate"]["wilson95_upper"],
                                   r["missed_evidence_rate"]["wilson95_upper"]))
        governing = dict(worse)
        governing["source"] = worse["source"] + " (worse of the two readers)"
        human_for_labeler = {p: A[p]["verdict"] for p in scored}
        human_src = "reader A (no adjudicated set supplied)"
    rep["rates"] = rate_sets
    rep["rates_governing"] = governing

    # ---- κ(labeler–human)
    lh = _labeler_human(scored, human_for_labeler, labeler)
    lh["human_side"] = human_src
    lh["definition"] = DEFS["kappa"]
    rep["kappa_labeler_human_binary"] = lh
    if not adjudicated_path:
        lh_b = _labeler_human(scored, {p: B[p]["verdict"] for p in scored}, labeler)
        lh_b["human_side"] = "reader B"
        rep["kappa_labeler_human_binary_readerB"] = lh_b

    # ---- extra graders
    if extras:
        gr: dict = {
            "header": "agent/model graders — never a substitute for κ(human–human)",
            "warning": "These graders did not read the documents as humans. Their agreement "
                       "with a human is a diagnostic, not evidence about the rubric: "
                       "§6.6.4's κ(human–human) rows can only be satisfied by two people. "
                       "No number in this section enters κ(human–human).",
            "graders": {}}
        for name, path in extras.items():
            E = read_verdicts(path)
            entry: dict = {"path": str(path),
                           "read": sum(1 for p in scored if E.get(p, {}).get("verdict")),
                           "unread_blank": sum(1 for p in scored
                                               if p in E and not E[p]["verdict"]),
                           "missing_from_sheet": [p for p in scored if p not in E],
                           "vs": {}}
            targets = []
            if adjudicated_path:
                ADJ = read_verdicts(adjudicated_path)
                targets.append(("adjudicated", {p: ADJ[p]["verdict"] for p in scored
                                                if ADJ.get(p, {}).get("verdict")}))
            else:
                targets.append(("reader A", {p: A[p]["verdict"] for p in scored}))
                targets.append(("reader B", {p: B[p]["verdict"] for p in scored}))
            for tname, tv in targets:
                common = [p for p in scored
                          if p in tv and E.get(p, {}).get("verdict")]
                if len(common) < 2:
                    entry["vs"][tname] = {"n": len(common),
                                          "note": "too few common pairs"}
                    continue
                ea = [E[p]["verdict"] for p in common]
                ta = [tv[p] for p in common]
                entry["vs"][tname] = {
                    "six_category": _kappa_block(ea, ta, VERDICTS,
                                                 seed=SEED_RDEV_SCORE + 2),
                    "binary": _binary_collapse(ea, ta)}
            gr["graders"][name] = entry
        rep["extra_graders"] = gr

    # ---- acceptance table
    rep["acceptance_table_6_6_4"] = acceptance_table(
        hh["kappa"], hh.get("kappa_ci95"),
        lh.get("kappa"), lh.get("kappa_ci95"),
        lh.get("positive_class_agreement"), governing)
    rep["acceptance_table_note"] = (
        "Thresholds and consequence text reproduced verbatim from "
        "design/SPEC-confirmation-run.md §6.6.4. Most rows state a TRIGGER, so PASS means "
        "the trigger condition was NOT met; the ≥ 0.60 / ≥ 0.85 row is a PERMISSION, where "
        "PASS means full-strength claims are permitted. κ(human–human) is read off the "
        "6-category PAIR-LEVEL verdict; κ(Scout–human) is the binary evidence-exists κ. "
        "§6.6.3 also names a UNIT-LEVEL κ — this scorer does not compute it, because the "
        "verdict sheets record one pair-level verdict per pair and no per-span judgements.")
    return rep


# ------------------------------------------------------------------------ markdown
def render_md(rep: dict) -> str:
    L: list[str] = []
    P = L.append
    P("# R-dev read — two-reader scoring (SPEC §6.6)\n")
    P(f"**Status: {rep['status']}**\n")
    P(f"* draw seed `{rep['draw'].get('seed')}`, {rep['draw'].get('drawn')} pairs; "
      f"scoring seed `{rep['seed_scoring']}`, {rep['n_boot']} bootstrap resamples")
    P(f"* inputs: A = `{rep['inputs']['a']}`, B = `{rep['inputs']['b']}`, "
      f"sample = `{rep['inputs']['sample']}`, labels = `{rep['inputs']['labels']}`, "
      f"adjudicated = `{rep['inputs']['adjudicated']}`\n")

    P("## 1. Coverage\n")
    P("| reader | pairs in sheet | verdicts recorded | blank (unread) | missing from sheet |")
    P("|---|---|---|---|---|")
    for k in ("A", "B"):
        c = rep["coverage"][k]
        P(f"| {k} | {c['in_sheet']} | {c['read']} | {c['unread_blank']} | "
          f"{len(c['missing_from_sheet'])} |")
    P(f"\n*{DEFS['unread']}*\n")
    P(f"**n scored (both readers): {rep['n_scored']}** "
      f"(minimum required: {rep['min_scored_required']})\n")
    P("| stratum | in draw | scored |")
    P("|---|---|---|")
    for s in rep["sample_by_stratum"]:
        P(f"| `{s}` | {rep['sample_by_stratum'][s]} | "
          f"{rep['scored_by_stratum'].get(s, 0)} |")
    P("")

    if not rep.get("kappa_reported"):
        P("## No statistics\n")
        P(f"{rep['status']}\n")
        P("The R-dev read is a **human** gate (§6.6.2). Until two people have recorded "
          "verdicts, there is nothing to score, and this scorer will not invent one.\n")
        return "\n".join(L)

    hh = rep["kappa_human_human_6cat"]
    hb = rep["kappa_human_human_binary"]
    P("## 2. κ(A–B) — the statistic §6.6.4 gates on\n")
    P("| statistic | n | κ | 95% CI | percent agreement (po) | expected (pe) |")
    P("|---|---|---|---|---|---|")
    ci = hh.get("kappa_ci95")
    P(f"| 6-category pair-level verdict | {hh['n']} | **{_fmt(hh['kappa'])}** | "
      f"[{_fmt(ci[0]) if ci else '—'}, {_fmt(ci[1]) if ci else '—'}] | "
      f"{_fmt(hh['percent_agreement'])} | {_fmt(hh['expected_agreement'])} |")
    cib = hb.get("kappa_ci95")
    P(f"| binary collapse (ambiguous excluded: {hb['ambiguous_excluded']}) | {hb['n']} | "
      f"**{_fmt(hb['kappa'])}** | [{_fmt(cib[0]) if cib else '—'}, "
      f"{_fmt(cib[1]) if cib else '—'}] | {_fmt(hb['percent_agreement'])} | "
      f"{_fmt(hb.get('expected_agreement'))} |")
    P(f"\n* **κ**: {DEFS['kappa']}")
    P(f"* **CI**: {DEFS['kappa_ci']}")
    P(f"* **po**: {DEFS['percent_agreement']}")
    P(f"* **binary collapse**: {DEFS['binary']}\n")

    P("### Per-stratum κ\n")
    P(f"*{DEFS['per_stratum']}*\n")
    P("| stratum | n | κ | 95% CI | po |")
    P("|---|---|---|---|---|")
    for s, blk in rep["kappa_per_stratum_6cat"]["strata"].items():
        if blk.get("kappa") is None:
            P(f"| `{s}` | {blk['n']} | n too small | — | — |")
            continue
        c = blk.get("kappa_ci95")
        P(f"| `{s}` | {blk['n']} | {_fmt(blk['kappa'])} | "
          f"[{_fmt(c[0]) if c else '—'}, {_fmt(c[1]) if c else '—'}] | "
          f"{_fmt(blk['percent_agreement'])} |")
    P("")

    P("## 3. Rates\n")
    for key, r in rep["rates"].items():
        if key.startswith("_"):
            P(f"*{r}*\n")
            continue
        P(f"### over {r['source']} (n = {r['n']})\n")
        P("| rate | k | p̂ | Wilson 95% upper (two-sided) | Wilson 95% upper (one-sided) |")
        P("|---|---|---|---|---|")
        for name in ("label_error_rate", "missed_evidence_rate", "correctly_none_rate",
                     "ambiguous_rate"):
            x = r[name]
            P(f"| {name.replace('_', ' ')} | {x['k']} | {_fmt(x['rate'])} | "
              f"{_fmt(x['wilson95_upper'])} | {_fmt(x['wilson95_upper_one_sided'])} |")
        P("")
        for name in ("label_error_rate", "missed_evidence_rate", "correctly_none_rate",
                     "ambiguous_rate"):
            P(f"* **{name.replace('_', ' ')}**: {r[name]['definition']}")
        P(f"* **Wilson**: {r['wilson_note']}\n")

    lh = rep["kappa_labeler_human_binary"]
    P("## 4. κ(labeler–human) — §6.6.4's κ(Scout–human)\n")
    P(f"Human side: **{lh['human_side']}**. {lh['mapping']}\n")
    cil = lh.get("kappa_ci95")
    P("| statistic | n | κ | 95% CI | po |")
    P("|---|---|---|---|---|")
    P(f"| binary evidence-exists (ambiguous excluded: {lh['ambiguous_excluded']}) | "
      f"{lh['n']} | **{_fmt(lh['kappa'])}** | [{_fmt(cil[0]) if cil else '—'}, "
      f"{_fmt(cil[1]) if cil else '—'}] | {_fmt(lh['percent_agreement'])} |")
    pa = lh["positive_class_agreement"]
    P("\n| positive-class agreement | value |")
    P("|---|---|")
    P(f"| human-positive pairs the labeler also called positive | "
      f"{_fmt(pa['human_to_labeler'])} ({pa['n_both_positive']}/{pa['n_human_positive']}) |")
    P(f"| labeler-positive pairs the human also called positive | "
      f"{_fmt(pa['labeler_to_human'])} ({pa['n_both_positive']}/{pa['n_labeler_positive']}) |")
    P(f"\n*{pa['definition']}*\n")

    g = rep.get("extra_graders")
    if g is None:
        P("## 5. agent/model graders — never a substitute for κ(human–human)\n")
        P("None supplied (`--extra NAME=PATH`).\n")
    else:
        P(f"## 5. {g['header']}\n")
        P(f"> {g['warning']}\n")
        for name, e in g["graders"].items():
            P(f"### `{name}` — {e['read']} verdicts recorded, {e['unread_blank']} blank\n")
            P("| vs | κ (6-category) | 95% CI | κ (binary) | 95% CI | n |")
            P("|---|---|---|---|---|---|")
            for tname, blk in e["vs"].items():
                if "six_category" not in blk:
                    P(f"| {tname} | — | — | — | — | {blk['n']} ({blk['note']}) |")
                    continue
                s6, sb = blk["six_category"], blk["binary"]
                c6, cb = s6.get("kappa_ci95"), sb.get("kappa_ci95")
                P(f"| {tname} | {_fmt(s6['kappa'])} | "
                  f"[{_fmt(c6[0]) if c6 else '—'}, {_fmt(c6[1]) if c6 else '—'}] | "
                  f"{_fmt(sb['kappa'])} | "
                  f"[{_fmt(cb[0]) if cb else '—'}, {_fmt(cb[1]) if cb else '—'}] | "
                  f"{s6['n']} |")
            P("")

    P("## 6. §6.6.4 acceptance table\n")
    P(f"*{rep['acceptance_table_note']}*\n")
    P("| statistic | observed | condition | verdict | consequence if triggered |")
    P("|---|---|---|---|---|")
    for r in rep["acceptance_table_6_6_4"]:
        P(f"| {r['statistic']} | {r['observed']} | {r['condition']} | **{r['verdict']}** | "
          f"{r['consequence']} |")
    P("")
    notes = [r for r in rep["acceptance_table_6_6_4"] if r["note"]]
    if notes:
        P("Notes:\n")
        for r in notes:
            P(f"* *{r['statistic']} / {r['condition']}*: {r['note']}")
        P("")
    return "\n".join(L)


# ----------------------------------------------------------------------------- CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Score the two-independent-reader R-dev read (SPEC §6.6).")
    ap.add_argument("--a", required=True, help="reader A verdicts CSV (pair_id,verdict,notes)")
    ap.add_argument("--b", required=True, help="reader B verdicts CSV")
    ap.add_argument("--sample", default=str(ARTIFACTS / "rdev_sample.json"),
                    help="the recorded draw (rdev_sample.json)")
    ap.add_argument("--labels", default=str(ARTIFACTS / "labels-dev.jsonl"),
                    help="the labeler's output, one JSON object per pair")
    ap.add_argument("--adjudicated", default=None,
                    help="post-joint-read verdicts, same CSV shape (§6.6.3)")
    ap.add_argument("--extra", action="append", default=[], metavar="NAME=PATH",
                    help="an additional grader's CSV, scored against the humans but never "
                         "entering κ(human–human). Repeatable.")
    ap.add_argument("--out", default=None, help="write the JSON report here")
    ap.add_argument("--md", default=None, help="write the markdown tables here")
    args = ap.parse_args(argv)

    extras = {}
    for spec in args.extra:
        if "=" not in spec:
            ap.error(f"--extra expects NAME=PATH, got {spec!r}")
        name, path = spec.split("=", 1)
        if not name or not path:
            ap.error(f"--extra expects NAME=PATH, got {spec!r}")
        extras[name] = path

    rep = build_report(args.a, args.b, args.sample, args.labels,
                       args.adjudicated, extras)
    md = render_md(rep)
    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(rep, indent=1) + "\n")
    if args.md:
        pathlib.Path(args.md).write_text(md + "\n")
    if not rep.get("kappa_reported"):
        sys.stderr.write(rep["status"] + "\n")
        sys.stderr.write(
            f"Reader A: {rep['coverage']['A']['read']} read, "
            f"{rep['coverage']['A']['unread_blank']} blank. "
            f"Reader B: {rep['coverage']['B']['read']} read, "
            f"{rep['coverage']['B']['unread_blank']} blank.\n")
        if not args.out and not args.md:
            print(md)
        return 2
    if not args.out and not args.md:
        print(md)
    else:
        print(f"n scored = {rep['n_scored']}; "
              f"κ(A–B) 6-cat = {_fmt(rep['kappa_human_human_6cat']['kappa'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
