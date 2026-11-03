#!/usr/bin/env python
"""Alias study: judge follow-ups requested by the methodology review (2026-11-02). Offline
analyses over the judge-validation labels (JUDGE-VALIDATION-PLAN.md, TIEBREAKER-PLAN.md) plus
the prompt-paraphrase check. No model, gateway, store or index call is made here; the
paraphrase labels come from ``glossbench_alias_judge.py label --backend qwen`` with
``qwen.subset_file`` set.

    alias_judge_followups.py --config CFG precision           -> analysis-precision.json
    alias_judge_followups.py --config CFG paraphrase-subset   -> paraphrase-subset.txt (frozen)
    alias_judge_followups.py --config CFG analyse-paraphrase  -> analysis-paraphrase.json

**Labels and reference.** The reference is the Opus label on the Opus-unanimous validation
pairs (:func:`glossbench_alias_judge.opus_reference`, 3 reps, all valid and equal). The positive
class is *answers* (yes). A judge's missing label (no C majority, an invalid E sample, an
unlabelled tiebreaker reply) is scored as *no*, the study's convention for an unjudged pair
(prereg §6.3), and counted (``no_label``).

**Judges** (``precision``): C (majority of the first 5 stored samples), E (the first stored
sample), the frozen C + E gate with an escalation taking the Opus reference (``gate_opus``,
the validation plan's scoring) or the tiebreaker's rep-1 label (``gate_argon``, the study's
rule), the gate on the pairs it accepts (``gate_accepted``), and each gateway judge alone.

**Metrics** (:func:`binary_metrics`): counts TP / FP / FN / TN; prevalence; precision,
recall (sensitivity), specificity, accuracy and the false-yes rate FP / (Opus no), each with a
Wilson 95 % interval; F1 with a seeded percentile-bootstrap interval (a ratio of counts, so
no Wilson interval exists); Cohen's kappa and Gwet's AC1.

**Subsets.** All unanimous pairs; without ``coll_diff``; without the pairs whose question paper
is a study (balanced-set) paper (prereg §4.3 item 6: 121 of the 587 pairs); without the pairs
that touch a study paper as question or passage paper (243 of 587). Each subset also per
stratum, and with the non-gold false-yes block (what H2's per-arm false-yes rates depend on):
FP / (Opus-no non-gold pairs) and FP / (all non-gold pairs), with the Wilson upper limits.

**Paraphrase subset** (``paraphrase-subset``): from the Opus-unanimous pairs, ``size`` / 2
positives (all of them if fewer) and the rest negatives, each class allocated to strata in
proportion to its stratum counts (largest remainder, ties by stratum name) and drawn per
stratum with ``random.Random(f"{seed}|{class}|{stratum}").sample(sorted ids, k)``.

**Paraphrase analysis** (``analyse-paraphrase``): per prompt version (``versions[]``: name,
C labels, E labels, and optionally ``e_sample`` to read another stored E sample), on the
subset: the gate (escalation -> Opus reference) accuracy, escalation share and the metrics
above, each judge alone, the pairs whose label differs from the ``baseline`` version, the
precision re-weighted to the population prevalence, and the spread of each metric across the
versions. A paraphrase whose precision or recall differs from the baseline by more than
``flag_points`` (3 points) is flagged.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import glossbench_alias_judge as jd  # noqa: E402

g, p4 = jd.g, jd.p4
STRATA = jd.STRATA
Z95 = 1.959964
Z95_ONE_SIDED = 1.644854


# ------------------------------------------------------------------ metrics

def gwet_ac1(a: Sequence[int], b: Sequence[int]) -> float | None:
    """Gwet's AC1 for two raters and two categories: (pa - pe) / (1 - pe) with
    pe = 2 pi (1 - pi), pi the mean of the two raters' yes shares."""
    n = len(a)
    if not n:
        return None
    pa = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pi = (sum(a) / n + sum(b) / n) / 2
    pe = 2 * pi * (1 - pi)
    return (pa - pe) / (1 - pe)


def _ratio(k: int, n: int) -> dict[str, Any]:
    return {"k": k, "n": n, "value": round(k / n, 4) if n else None, "ci95": jd.wilson(k, n)}


def wilson_upper_one_sided(k: int, n: int) -> float | None:
    w = jd.wilson(k, n, Z95_ONE_SIDED)
    return w[1] if w else None


def _f1(tp: int, fp: int, fn: int) -> float | None:
    d = 2 * tp + fp + fn
    return 2 * tp / d if d else None


def f1_bootstrap_ci(pred: Sequence[int], ref: Sequence[int], draws: int, seed: str) -> list[float] | None:
    """Percentile 95 % interval of F1 over ``draws`` resamples of the pairs (with replacement)."""
    n = len(pred)
    if not n:
        return None
    rng = random.Random(seed)
    vals = []
    for _ in range(draws):
        tp = fp = fn = 0
        for _ in range(n):
            i = rng.randrange(n)
            p, r = pred[i], ref[i]
            tp += p and r
            fp += p and not r
            fn += r and not p
        f = _f1(tp, fp, fn)
        if f is not None:
            vals.append(f)
    if not vals:
        return None
    vals.sort()
    return [round(vals[int(0.025 * (len(vals) - 1))], 4), round(vals[int(0.975 * (len(vals) - 1))], 4)]


def binary_metrics(pred: Mapping[str, int | None], ref: Mapping[str, int], ids: Sequence[str],
                   *, f1_draws: int = 0, seed: str = "f1") -> dict[str, Any]:
    """Confusion counts and the metrics for the label *answers* on ``ids``; a missing prediction
    is scored as no and counted in ``no_label``."""
    p = [1 if pred.get(pid) == 1 else 0 for pid in ids]
    r = [int(ref[pid]) for pid in ids]
    tp = sum(a and b for a, b in zip(p, r, strict=True))
    fp = sum(a and not b for a, b in zip(p, r, strict=True))
    fn = sum(b and not a for a, b in zip(p, r, strict=True))
    tn = len(ids) - tp - fp - fn
    n = len(ids)
    k = jd.kappa(p, r)
    ac1 = gwet_ac1(p, r)
    f1 = _f1(tp, fp, fn)
    return {"pairs": n, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "no_label": sum(pred.get(pid) is None for pid in ids),
            "prevalence": _ratio(tp + fn, n), "predicted_yes": _ratio(tp + fp, n),
            "precision": _ratio(tp, tp + fp), "recall": _ratio(tp, tp + fn),
            "specificity": _ratio(tn, tn + fp), "accuracy": _ratio(tp + tn, n),
            "false_yes_rate": {**_ratio(fp, fp + tn), "upper95_one_sided": wilson_upper_one_sided(fp, fp + tn)},
            "f1": {"value": round(f1, 4) if f1 is not None else None,
                   "ci95_bootstrap": f1_bootstrap_ci(p, r, f1_draws, seed) if f1_draws else None},
            "kappa": round(k, 4) if k is not None else None, "ac1": round(ac1, 4) if ac1 is not None else None}


def non_gold_false_yes(pred: Mapping[str, int | None], ref: Mapping[str, int], ids: Sequence[str],
                       pairs: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """The false-yes rate on the non-gold pairs: FP / (Opus-no non-gold pairs), the prereg's
    per-arm definition, and FP / (all non-gold pairs), the share of retrieved non-gold chunks
    wrongly counted as answering; each with its Wilson 95 % interval and one-sided upper limit."""
    ng = [pid for pid in ids if pairs[pid]["stratum"] != "gold"]
    neg = [pid for pid in ng if ref[pid] == 0]
    fp = sum(pred.get(pid) == 1 for pid in neg)
    return {"non_gold_pairs": len(ng), "opus_no": len(neg), "fp": fp,
            "per_opus_no": {**_ratio(fp, len(neg)), "upper95_one_sided": wilson_upper_one_sided(fp, len(neg))},
            "per_non_gold_pair": {**_ratio(fp, len(ng)), "upper95_one_sided": wilson_upper_one_sided(fp, len(ng))}}


# ------------------------------------------------------------------ inputs

def load_pairs(vdir: Path, pairs_sha: str | None) -> dict[str, dict[str, Any]]:
    path = vdir / "pairs.jsonl"
    if pairs_sha and g.file_sha(path) != pairs_sha:
        raise SystemExit(f"{path}: sha256 differs from pairs_sha256")
    return {p["pair_id"]: p for p in (json.loads(x) for x in path.read_text().splitlines() if x.strip())}


def unanimous_reference(vdir: Path, pairs: Mapping[str, Any], reps: int = 3) -> dict[str, int]:
    ref = jd.opus_reference(p4.Store(vdir / "labels-opus.jsonl").rows.values(), reps)
    return {pid: int(r["ref"]) for pid, r in ref.items() if r["unanimous"] and pid in pairs}


def qwen_rows(path: str | Path) -> dict[str, dict[str, Any]]:
    return {r["pair_id"]: r for r in p4.Store(Path(path)).rows.values()}


def gw_rep1(path: str | Path) -> dict[str, int | None]:
    out: dict[str, int | None] = {}
    for r in p4.Store(Path(path)).rows.values():
        if int(r.get("rep") or 0) == 1:
            out[r["pair_id"]] = None if r.get("verdict") is None else int(r["verdict"])
    return out


def study_papers(accepted: str | Path) -> tuple[set[str], dict[str, Any]]:
    """The balanced-set (satisfied) papers of the study's accepted-record file, with the
    record-set identity check of the prereg (§4.1)."""
    import alias_study_common as sc

    recs = sc.load_records(accepted)
    ident = sc.verify_record_set(accepted, recs)
    return sc.balanced_papers(recs), ident


# ------------------------------------------------------------------ precision (task 1)

def score_judges(judges: Mapping[str, Mapping[str, int | None]], ref: Mapping[str, int], ids: Sequence[str],
                 pairs: Mapping[str, Mapping[str, Any]], f1_draws: int, seed: str,
                 restrict: Mapping[str, Sequence[str]] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, lab in judges.items():
        keep = set(restrict[name]) if restrict and name in restrict else None
        sub = [pid for pid in ids if keep is None or pid in keep]
        res = binary_metrics(lab, ref, sub, f1_draws=f1_draws, seed=f"{seed}|{name}")
        res["non_gold"] = non_gold_false_yes(lab, ref, sub, pairs)
        res["by_stratum"] = {s: binary_metrics(lab, ref, x)
                             for s in STRATA if (x := [pid for pid in sub if pairs[pid]["stratum"] == s])}
        out[name] = res
    return out


def precision_analysis(cfg: Mapping[str, Any]) -> dict[str, Any]:
    vdir = Path(cfg["validation_dir"])
    pairs = load_pairs(vdir, cfg.get("pairs_sha256"))
    ref = unanimous_reference(vdir, pairs)
    una = sorted(ref)
    c_rows, e_rows = qwen_rows(cfg["c_labels"]), qwen_rows(cfg["e_labels"])
    if set(pairs) - set(c_rows) or set(pairs) - set(e_rows):
        raise SystemExit("precision: C or E labels missing for some pairs")
    lab_c = {pid: jd.majority(c_rows[pid]["verdicts"][:5]) for pid in pairs}
    lab_e = {pid: jd.majority(e_rows[pid]["verdicts"][:1]) for pid in pairs}
    gate = {pid: jd.gate_label(c_rows[pid]["verdicts"], e_rows[pid]["verdicts"], 5, 1) for pid in pairs}
    tb = {name: gw_rep1(path) for name, path in cfg["tiebreakers"].items()}
    argon = tb[cfg["tiebreaker_used"]]
    judges: dict[str, dict[str, int | None]] = {
        "gate_opus": {pid: (gate[pid] if gate[pid] is not None else ref.get(pid)) for pid in pairs},
        "gate_argon": {pid: (gate[pid] if gate[pid] is not None else argon.get(pid)) for pid in pairs},
        "gate_accepted": dict(gate),
        "C_maj5": lab_c, "E_n1": lab_e, **{f"alone_{k}": v for k, v in tb.items()}}
    accepted_ids = [pid for pid in una if gate[pid] is not None]
    papers, ident = study_papers(cfg["study_accepted"])
    q300 = {pid for pid, p in pairs.items() if p["q_paper"] in papers}
    touch = {pid for pid, p in pairs.items() if p["q_paper"] in papers or p["c_paper"] in papers}
    subsets = {"all": una,
               "excl_coll_diff": [pid for pid in una if pairs[pid]["stratum"] != "coll_diff"],
               "excl_q_study_paper": [pid for pid in una if pid not in q300],
               "excl_touch_study_paper": [pid for pid in una if pid not in touch]}
    draws, seed = int(cfg.get("f1_bootstrap", 2000)), str(cfg.get("seed", "20261102|judge-followups"))
    esc = [pid for pid in una if gate[pid] is None]
    res: dict[str, Any] = {
        "t": g.now_iso(),
        "definitions": {
            "reference": "Opus (alias-judge/1, 3 reps) on its unanimous pairs; positive class = answers (yes)",
            "gate": "C (Qwen3.6, majority of the first 5 samples) and E (Qwen3.8, first sample) labelled and equal",
            "gate_opus": "gate; an escalated pair takes the Opus reference (the validation plan's scoring)",
            "gate_argon": f"gate; an escalated pair takes {cfg['tiebreaker_used']} rep 1 (the study's rule); "
                          "no tiebreaker label -> no",
            "gate_accepted": "the gate on the pairs it accepts only",
            "missing_label": "scored as no (unjudged = not answering, prereg 6.3) and counted in no_label",
            "false_yes_rate": "FP / (Opus no)", "f1_ci": f"percentile bootstrap over pairs, {draws} draws",
            "excl_q_study_paper": "without pairs whose question paper is a balanced-set (study) paper",
            "excl_touch_study_paper": "without pairs whose question or passage paper is a study paper"},
        "inputs": {"pairs_sha256": g.file_sha(vdir / "pairs.jsonl"),
                   "opus_sha256": g.file_sha(vdir / "labels-opus.jsonl"),
                   "c_labels": str(cfg["c_labels"]), "c_sha256": g.file_sha(cfg["c_labels"]),
                   "e_labels": str(cfg["e_labels"]), "e_sha256": g.file_sha(cfg["e_labels"]),
                   "tiebreakers": {k: {"labels": str(v), "sha256": g.file_sha(v)} for k, v in cfg["tiebreakers"].items()},
                   "study_accepted": str(cfg["study_accepted"]), "study_record_set": ident,
                   "study_papers": len(papers)},
        "overlap_pairs": {"q_study_paper": {"all": len(q300), "unanimous": sum(pid in q300 for pid in una)},
                          "touch_study_paper": {"all": len(touch), "unanimous": sum(pid in touch for pid in una)}},
        "classes": {"pairs": len(pairs), "unanimous": len(una), "opus_yes": sum(ref.values()),
                    "unanimous_by_stratum": {s: {"pairs": len(x), "opus_yes": sum(ref[p] for p in x)}
                                             for s in STRATA if (x := [p for p in una if pairs[p]["stratum"] == s])}},
        "gate_escalations": {"unanimous": len(esc),
                             "argon_agrees_with_opus": sum(argon.get(pid) == ref[pid] for pid in esc),
                             "argon_no_label": sum(argon.get(pid) is None for pid in esc)},
        "subsets": {}}
    for name, ids in subsets.items():
        res["subsets"][name] = score_judges(judges, ref, ids, pairs, draws, f"{seed}|{name}",
                                            restrict={"gate_accepted": accepted_ids})
    return res


# ------------------------------------------------------------------ the paraphrase subset (task 2)

def _allocate(counts: Mapping[str, int], k: int) -> dict[str, int]:
    """Largest-remainder allocation of ``k`` over ``counts`` (proportional; ties by name)."""
    total = sum(counts.values())
    if k >= total:
        return dict(counts)
    raw = {s: k * c / total for s, c in counts.items()}
    out = {s: int(v) for s, v in raw.items()}
    left = k - sum(out.values())
    for s in sorted(counts, key=lambda s: (-(raw[s] - out[s]), s))[:left]:
        out[s] += 1
    return out


def paraphrase_subset(pairs: Mapping[str, Mapping[str, Any]], ref: Mapping[str, int], size: int,
                      seed: str) -> tuple[list[str], dict[str, Any]]:
    by: dict[tuple[int, str], list[str]] = defaultdict(list)
    for pid in sorted(ref):
        by[(ref[pid], pairs[pid]["stratum"])].append(pid)
    n_pos_all = sum(ref.values())
    n_pos = min(n_pos_all, size // 2)
    n_neg = size - n_pos
    chosen: list[str] = []
    alloc: dict[str, dict[str, int]] = {}
    for cls, k in ((1, n_pos), (0, n_neg)):
        counts = {s: len(v) for (c, s), v in by.items() if c == cls}
        a = _allocate(counts, k)
        alloc["yes" if cls else "no"] = a
        for s in sorted(a):
            chosen += random.Random(f"{seed}|{'yes' if cls else 'no'}|{s}").sample(by[(cls, s)], a[s])
    return sorted(chosen), {"size": len(chosen), "yes": n_pos, "no": n_neg, "allocation": alloc,
                            "population": {"unanimous": len(ref), "yes": n_pos_all}, "seed": seed}


# ------------------------------------------------------------------ the paraphrase analysis (task 2)

def reweighted_precision(recall: float | None, specificity: float | None, prevalence: float) -> float | None:
    """Precision at the population prevalence from the class-conditional rates of the subset."""
    if recall is None or specificity is None:
        return None
    num = recall * prevalence
    den = num + (1 - specificity) * (1 - prevalence)
    return round(num / den, 4) if den else None


def paraphrase_analysis(cfg: Mapping[str, Any]) -> dict[str, Any]:
    vdir = Path(cfg["validation_dir"])
    pairs = load_pairs(vdir, cfg.get("pairs_sha256"))
    ref_all = unanimous_reference(vdir, pairs)
    sub_path = Path(cfg["subset_file"])
    if cfg.get("subset_sha256") and g.file_sha(sub_path) != cfg["subset_sha256"]:
        raise SystemExit(f"{sub_path}: sha256 differs from subset_sha256")
    ids = [x.strip() for x in sub_path.read_text().splitlines() if x.strip()]
    if not set(ids) <= set(ref_all):
        raise SystemExit("paraphrase subset holds pairs without a unanimous reference")
    ref = {pid: ref_all[pid] for pid in ids}
    prev_pop = sum(ref_all.values()) / len(ref_all)
    base = str(cfg["baseline"])
    flag = float(cfg.get("flag_points", 3)) / 100
    res: dict[str, Any] = {"t": g.now_iso(), "subset": {"file": str(sub_path), "sha256": g.file_sha(sub_path),
                                                        "pairs": len(ids), "opus_yes": sum(ref.values()),
                                                        "by_stratum": dict(Counter(pairs[p]["stratum"] for p in ids))},
                           "population_prevalence": round(prev_pop, 4), "baseline": base,
                           "flag_rule": f"|precision or recall - baseline's| > {flag:.2f} for the gate, C or E",
                           "versions": {}}
    labels: dict[str, dict[str, dict[str, int | None]]] = {}
    for v in cfg["versions"]:
        name = str(v["name"])
        c_rows, e_rows = qwen_rows(v["c_labels"]), qwen_rows(v["e_labels"])
        miss = [pid for pid in ids if pid not in c_rows or pid not in e_rows]
        if miss:
            raise SystemExit(f"analyse-paraphrase: {name}: {len(miss)} subset pairs lack a C or E row")
        es = int(v.get("e_sample", 0))
        c_lab = {pid: jd.majority(c_rows[pid]["verdicts"][:5]) for pid in ids}
        e_lab = {pid: (None if len(e_rows[pid]["verdicts"]) <= es or e_rows[pid]["verdicts"][es] is None
                       else int(e_rows[pid]["verdicts"][es])) for pid in ids}
        gate = {pid: (c_lab[pid] if c_lab[pid] is not None and c_lab[pid] == e_lab[pid] else None) for pid in ids}
        final = {pid: (gate[pid] if gate[pid] is not None else ref[pid]) for pid in ids}
        labels[name] = {"gate": final, "C": c_lab, "E": e_lab, "raw_gate": gate}
        info: dict[str, Any] = {
            "c_labels": str(v["c_labels"]), "c_sha256": g.file_sha(v["c_labels"]),
            "e_labels": str(v["e_labels"]), "e_sha256": g.file_sha(v["e_labels"]), "e_sample": es,
            "judge_versions": {"C": dict(Counter(str(c_rows[p].get("judge_version")) for p in ids)),
                               "E": dict(Counter(str(e_rows[p].get("judge_version")) for p in ids))},
            "served_models": {"C": dict(Counter(str(c_rows[p].get("model")) for p in ids)),
                              "E": dict(Counter(str(e_rows[p].get("model")) for p in ids))},
            "enable_thinking": {"C": dict(Counter(str(c_rows[p].get("enable_thinking")) for p in ids)),
                                "E": dict(Counter(str(e_rows[p].get("enable_thinking")) for p in ids))},
            "invalid_samples": {"C": sum(sum(x is None for x in c_rows[p]["verdicts"][:5]) for p in ids),
                                "E": sum(e_lab[p] is None for p in ids)},
            "escalated": sum(gate[p] is None for p in ids),
            "gate_accepted_errors": sum(gate[p] is not None and gate[p] != ref[p] for p in ids)}
        for j in ("gate", "C", "E"):
            m = binary_metrics(labels[name][j], ref, ids)
            m["precision_at_population_prevalence"] = reweighted_precision(
                m["recall"]["value"], m["specificity"]["value"], prev_pop)
            info[j] = m
        info["escalation_share"] = _ratio(info["escalated"], len(ids))
        res["versions"][name] = info
    if base not in labels:
        raise SystemExit(f"baseline {base!r} is not a version")
    keys = [("gate", "accuracy"), ("gate", "precision"), ("gate", "recall"), ("gate", "kappa"), ("gate", "ac1"),
            ("C", "accuracy"), ("C", "precision"), ("C", "recall"), ("C", "kappa"),
            ("E", "accuracy"), ("E", "precision"), ("E", "recall"), ("E", "kappa"), ("-", "escalation_share")]

    def val(name: str, j: str, k: str) -> float | None:
        x = res["versions"][name]["escalation_share" if j == "-" else j]
        x = x if j == "-" else x[k]
        return x["value"] if isinstance(x, dict) else x

    names = list(res["versions"])
    spread: dict[str, Any] = {}
    for j, k in keys:
        vals = {nm: val(nm, j, k) for nm in names}
        good = [x for x in vals.values() if x is not None]
        b = vals[base]
        spread[f"{j}.{k}" if j != "-" else k] = {
            "values": vals, "range": round(max(good) - min(good), 4) if good else None,
            "delta_vs_baseline": {nm: (round(x - b, 4) if x is not None and b is not None else None)
                                  for nm, x in vals.items() if nm != base}}
    res["spread"] = spread
    res["flips_vs_baseline"] = {nm: {j: sum(labels[nm][j][p] != labels[base][j][p] for p in ids)
                                     for j in ("C", "E", "raw_gate")} for nm in names if nm != base}
    flagged = []
    for nm in cfg.get("paraphrases", []):
        for j in ("gate", "C", "E"):
            for k in ("precision", "recall"):
                d = spread[f"{j}.{k}"]["delta_vs_baseline"].get(nm)
                if d is not None and abs(d) > flag + 1e-12:
                    flagged.append({"version": nm, "judge": j, "metric": k, "delta": d})
    res["flagged"] = flagged
    return res


# ------------------------------------------------------------------ CLI

def provenance() -> dict[str, Any]:
    return jd.provenance()


def main(argv: Sequence[str] | None = None, *, prov: Mapping[str, Any] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", required=True, type=Path)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("precision", "paraphrase-subset", "analyse-paraphrase"):
        sub.add_parser(name)
    args = ap.parse_args(argv)
    os.umask(0o077)
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    out = Path(cfg["out_dir"]).resolve()
    repo = _HERE.parents[2].resolve()
    if out == repo or repo in out.parents:
        raise SystemExit("out_dir is inside the repository; private outputs must live outside it")
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    pv = dict(prov) if prov is not None else provenance()
    if args.cmd == "precision":
        res = {**precision_analysis(cfg), "provenance": pv}
        g.write_json_atomic(out / "analysis-precision.json", res)
        a = res["subsets"]["all"]
        print(json.dumps({"classes": res["classes"], "all": {k: {x: a[k][x] for x in ("tp", "fp", "fn", "tn", "kappa",
                                                                                      "ac1")} for k in a}}, indent=1))
        return 0
    if args.cmd == "paraphrase-subset":
        path = out / "paraphrase-subset.txt"
        if path.exists():
            raise SystemExit(f"{path.name} exists; the subset is frozen once written")
        vdir = Path(cfg["validation_dir"])
        pairs = load_pairs(vdir, cfg.get("pairs_sha256"))
        ids, info = paraphrase_subset(pairs, unanimous_reference(vdir, pairs), int(cfg.get("subset_size", 200)),
                                      str(cfg["subset_seed"]))
        path.write_text("".join(x + "\n" for x in ids), encoding="utf-8")
        info.update({"file": str(path), "sha256": g.file_sha(path), "provenance": pv, "t": g.now_iso()})
        g.write_json_atomic(out / "paraphrase-subset.json", info)
        print(json.dumps(info, indent=1))
        return 0
    res = {**paraphrase_analysis(cfg), "provenance": pv}
    g.write_json_atomic(out / "analysis-paraphrase.json", res)
    print(json.dumps({"flagged": res["flagged"], "spread": {k: v["values"] for k, v in res["spread"].items()}},
                     indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
