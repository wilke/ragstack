#!/usr/bin/env python
"""Alias study (OA-1000): the statistics (DRAFT prereg §3, §7, §8, §9.1).

    alias_study_stats.py --config CFG [--out results.json] [--public public.json]

Reads the runner's legs (``alias_study_run.py``), the judge's final labels
(``alias_study_judge.py``: ``final.jsonl``, ``judge-summary.json``, ``pairs.jsonl``) and the
accepted records, and writes one results file. Nothing in it is a record id or text; the
``--public`` copy is checked (:func:`alias_study_common.public_problems`) and refused when it
carries any.

**Outcomes (§6.1).** Per record and leg: ``hit`` (the gold chunk in the top 10, or a top-10
chunk whose final label is *answers*; unjudged = no), ``hit_gold`` (gold only), ``rr`` /
``rr_gold`` (RR@10), and the hit's kind (gold, same-paper judged, other-paper judged). A
failed call is ``None``; a record failed in any cell of a contrast is excluded from it and
counted (§7 *Missing data*); ``family_missing_as_not_found`` scores it 0 (a sensitivity).

**Layer 1 (§3.1).** Each member is a linear contrast of cell outcomes per record (H1:
merged - none on base x default, G records; H2: neighbour - (random-1 + random-2)/2 on
default x none, all 900; H1b: per paper, the mean of its G records' H1 difference minus its C
record's), summed per paper, tested by the paper sign-flip test (``chunktrial_stats``:
10,000 flips, exact at <= 20 non-zero papers, seed ``261101|alias|perm``). The members run
through the Option-B procedure (:func:`layer1_procedure`): H1 and H2 start at 0.025 each and a
rejected member passes its level to the other (Holm over 2); H1b is tested at 0.05 only when
both are rejected. A member is GATE-NOT-EVALUABLE (p = 1.0 in the procedure) when its minimum
attainable p exceeds the level it is being tested at, it has fewer than 8 papers, any of its
cells is gated by the runner (depth read-back, inactive reranker, boost probe, a run that is
not ``complete``), or, for H1 and H1b, the C gate fires (rule C) or the reach gate fails
(fewer than 480 of the 600 G records render their own entry; a reach count not supplied
fails it). H2 is *confirmed* only when its judged test rejects **and** its gold-only net is
below 0; otherwise *not confirmed (gold-only direction gate)*, not rejected.

**Layer 2 (§3.3).** Every figure in points with a 95 % paper-bootstrap percentile CI (10,000
resamples, seed ``261101|alias|boot``), the gate verdicts of its cells printed beside it, and
the three serving rules applied mechanically (:func:`decision_rules`).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE / "glossbench"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import alias_study_common as asc  # noqa: E402
import alias_study_power as apw  # noqa: E402
import chunktrial_stats as cs  # noqa: E402

GATE = asc.GATE
ALPHA = 0.05
MIN_CLUSTERS = 8
REACH_SHARE = 0.80
SCHEMA = "alias-study-results/1"
Spec = list[tuple[float, str]]


def L(c: str, g: str, r: str, rep: int = 1) -> str:  # noqa: N802 - a cell name, short on purpose
    return asc.leg_id(c, g, r, rep)


H1_SPEC: Spec = [(1.0, L("base", "merged", "default")), (-1.0, L("base", "none", "default"))]
H2_SPEC: Spec = [(1.0, L("neighbour", "none", "default")), (-0.5, L("random1", "none", "default")),
                 (-0.5, L("random2", "none", "default"))]
H3_SPEC: Spec = [(1.0, L("neighbour", "none", "default")), (-1.0, L("base", "none", "default")),
                 (-1.0, L("neighbour", "none", "bm25")), (1.0, L("base", "none", "bm25"))]


# ------------------------------------------------------------------ outcomes

def leg_outcomes(rows: Sequence[Mapping[str, Any]], records: Mapping[str, Mapping[str, Any]],
                 label: Callable[[str, str], int | None], pair_paper: Mapping[tuple[str, str], str]
                 ) -> dict[str, dict[str, Any] | None]:
    """Per record: hit, hit_gold, rr, rr_gold, kind, or None for a failed call."""
    out: dict[str, dict[str, Any] | None] = {}
    for row in rows:
        rid = row["record_id"]
        rec = records.get(rid)
        if rec is None:
            continue
        if row.get("failed"):
            out[rid] = None
            continue
        gold = rec["gold_chunks"][0]
        top = (row.get("ranked_chunk_ids") or [])[:asc.FOUND_K]
        rank_gold = next((i for i, c in enumerate(top, 1) if c == gold), None)
        rank_any = None
        kind = None
        for i, c in enumerate(top, 1):
            if c == gold:
                rank_any, kind = i, "gold"
                break
            if label(rid, c) == 1:
                rank_any = i
                kind = "same" if pair_paper.get((rid, c)) == rec["paper"] else "other"
                break
        out[rid] = {"hit": int(rank_any is not None), "hit_gold": int(rank_gold is not None),
                    "rr": 1.0 / rank_any if rank_any else 0.0, "rr_gold": 1.0 / rank_gold if rank_gold else 0.0,
                    "kind": kind}
    return out


def contrast_values(recs: Sequence[Mapping[str, Any]], outcomes: Mapping[str, Mapping[str, Any]], spec: Spec,
                    key: str = "hit", missing: str = "exclude") -> tuple[list[str], list[float], int]:
    """Per record ``sum(coef x outcome[leg][key])``: (clusters, values, records missing)."""
    clusters: list[str] = []
    vals: list[float] = []
    n_missing = 0
    for r in recs:
        tot = 0.0
        miss = False
        for coef, leg in spec:
            o = (outcomes.get(leg) or {}).get(r["record_id"])
            if o is None:
                miss = True
                v = 0.0
            else:
                v = float(o[key])
            tot += coef * v
        if miss:
            n_missing += 1
            if missing == "exclude":
                continue
        clusters.append(str(r["paper"]))
        vals.append(tot)
    return clusters, vals, n_missing


def cluster_sums(clusters: Sequence[str], vals: Sequence[float]) -> tuple[list[str], np.ndarray, np.ndarray]:
    order: dict[str, int] = {}
    for c in clusters:
        order.setdefault(c, len(order))
    sums = np.zeros(len(order))
    cnt = np.zeros(len(order))
    for c, v in zip(clusters, vals, strict=True):
        sums[order[c]] += v
        cnt[order[c]] += 1
    return list(order), sums, cnt


def boot_ci(sums: np.ndarray, counts: np.ndarray, n_boot: int, seed: int) -> list[float] | None:
    """95 % paper-bootstrap percentile CI of 100 x sum / n (points)."""
    if sums.size == 0:
        return None
    vals = cs._bootstrap_net_shares(np.asarray(sums, float), np.asarray(counts, float), n_boot, seed)
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return [round(100 * float(lo), 4), round(100 * float(hi), 4)]


def figure(recs: Sequence[Mapping[str, Any]], outcomes: Mapping[str, Mapping[str, Any]], spec: Spec,
           an: Mapping[str, Any], key: str = "hit", gates: Mapping[str, str | None] | None = None,
           missing: str = "exclude") -> dict[str, Any]:
    """A Layer-2 figure: net in points, the CI, the counts, the gate verdicts of its cells."""
    cl, v, miss = contrast_values(recs, outcomes, spec, key, missing)
    _, sums, cnt = cluster_sums(cl, v)
    n = len(v)
    arr = np.asarray(v)
    out: dict[str, Any] = {"n": n, "missing": miss, "papers": int(sums.size),
           "net_points": round(100 * float(arr.sum()) / n, 4) if n else None,
           "ci95": boot_ci(sums, cnt, int(an["n_boot"]), int(an["boot_seed"])),
           "gained": int((arr > 0).sum()), "lost": int((arr < 0).sum()),
           "cells": [leg for _, leg in spec]}
    if gates is not None:
        out["cell_gates"] = {leg: gates.get(leg) for _, leg in spec if gates.get(leg)}
    return out


def found_rate(recs: Sequence[Mapping[str, Any]], outcomes: Mapping[str, Mapping[str, Any]], leg: str,
               key: str = "hit") -> dict[str, Any]:
    vals = [o[key] for r in recs if (o := (outcomes.get(leg) or {}).get(r["record_id"])) is not None]
    n = len(vals)
    return {"n": n, "found": float(sum(vals)) if key.startswith("rr") else int(sum(vals)),
            "points": round(100 * sum(vals) / n, 4) if n else None}


# ------------------------------------------------------------------ Layer 1

def member_test(clusters: Sequence[str], vals: Sequence[float], alternative: str, an: Mapping[str, Any]
                ) -> dict[str, Any]:
    names, sums, cnt = cluster_sums(clusters, vals)
    if sums.size == 0:
        return {"n": 0, "papers": 0, "p": None, "min_attainable_p": 1.0, "n_nonzero": 0}
    t = cs.sign_flip_test(sums, n_flips=int(an["n_perm"]), seed=int(an["perm_seed"]), alternative=alternative)  # type: ignore[arg-type]
    k = int(t["n_pages_nonzero"])
    mp = 2.0 ** -k if k <= cs.EXACT_MAX_PAGES else max(2.0 ** -k, 1.0 / (1 + int(an["n_perm"])))
    arr = np.asarray(vals)
    return {"n": len(vals), "papers": int(sums.size), "n_nonzero": k, "statistic": t["statistic"], "p": t["p"],
            "p_exact": t["p_exact"], "p_perm": t["p_perm"], "p_mid": t["p_mid"], "min_attainable_p": mp,
            "net_points": round(100 * float(arr.sum()) / len(vals), 4),
            "ci95": boot_ci(sums, cnt, int(an["n_boot"]), int(an["boot_seed"])),
            "gained": int((arr > 0).sum()), "lost": int((arr < 0).sum()), "alternative": alternative}


def h1b_values(recs: Sequence[Mapping[str, Any]], outcomes: Mapping[str, Mapping[str, Any]], key: str = "hit",
               missing: str = "exclude") -> tuple[list[str], list[float], int]:
    """Per paper: mean over its G records of D minus its C record's D, D = merged - none (base x
    default). A paper whose C record, or every G record, is excluded is dropped (counted)."""
    by: dict[str, dict[str, list[float]]] = defaultdict(lambda: {"G": [], "C": []})
    miss = 0
    for r in recs:
        _, v, m = contrast_values([r], outcomes, H1_SPEC, key, missing)
        miss += m
        if v:
            by[str(r["paper"])][r["kind"]].append(v[0])
    clusters, vals = [], []
    dropped = 0
    for paper, d in by.items():
        if not d["G"] or not d["C"]:
            dropped += 1
            continue
        clusters.append(paper)
        vals.append(sum(d["G"]) / len(d["G"]) - sum(d["C"]) / len(d["C"]))
    return clusters, vals, miss + dropped


def c_gate(recs: Sequence[Mapping[str, Any]], outcomes: Mapping[str, Mapping[str, Any]], an: Mapping[str, Any],
           missing: str = "exclude") -> dict[str, Any]:
    """Rule C (§3.1)."""
    g = [r for r in recs if r["kind"] == "G"]
    c = [r for r in recs if r["kind"] == "C"]
    clg, vg, _ = contrast_values(g, outcomes, H1_SPEC, "hit", missing)
    clc, vc, _ = contrast_values(c, outcomes, H1_SPEC, "hit", missing)
    dg = 100 * sum(vg) / len(vg) if vg else 0.0
    dc = 100 * sum(vc) / len(vc) if vc else 0.0
    _, sc, nc = cluster_sums(clc, vc)
    ci = boot_ci(sc, nc, int(an["n_boot"]), int(an["boot_seed"]))
    base_c = found_rate(c, outcomes, L("base", "none", "default"))
    n_c = base_c["n"]
    headroom_records = n_c - int(base_c["found"]) if n_c else None
    half_dg_records = 0.5 * dg / 100 * n_c if n_c else None
    out: dict[str, Any] = {"delta_g_points": round(dg, 4), "delta_c_points": round(dc, 4), "delta_c_ci95": ci,
                           "ratio_c_over_g": round(dc / dg, 4) if dg else None,
                           "baseline_c_found10": base_c, "headroom_records": headroom_records}
    if dg <= 0:
        out.update(verdict="moot", fires=False, note="C gate moot, ΔG ≤ 0")
        return out
    same_sign = dc > 0
    big = abs(dc) >= 0.5 * abs(dg)
    excl = ci is not None and (ci[0] > 0 or ci[1] < 0)
    fires = bool(same_sign and big and excl)
    out.update(verdict="fires" if fires else "passes", fires=fires,
               conditions={"a_same_sign": same_sign, "b_half": big, "c_ci_excludes_0": excl})
    if headroom_records is not None and half_dg_records is not None and headroom_records < half_dg_records:
        out["ceiling_limited"] = True
        out["note"] = "C gate ceiling-limited"
    return out


def layer1_procedure(members: Mapping[str, Mapping[str, Any]], alpha: float = ALPHA) -> dict[str, dict[str, Any]]:
    """Option B (§3.1): Holm over H1, H2 in graphical form (0.025 each, weight 1 to each other),
    then H1b at ``alpha`` iff both are rejected.

    ``members[h]``: ``p`` (None = absent), ``gates`` (level-independent gate reasons),
    ``min_attainable_p``, and for H2 ``gold_gate_pass``. Returns per member ``status``,
    ``level`` (the level it was finally tested at), ``rejected`` and ``gate_reasons``."""
    level = {"H1": alpha / 2, "H2": alpha / 2}
    rejected: set[str] = set()

    def evaluable(h: str, lvl: float) -> bool:
        m = members[h]
        return m.get("p") is not None and not m.get("gates") and float(m["min_attainable_p"]) <= lvl

    changed = True
    while changed:
        changed = False
        for h, other in (("H1", "H2"), ("H2", "H1")):
            if h in rejected:
                continue
            m = members[h]
            ok = evaluable(h, level[h]) and float(m["p"]) <= level[h]
            if ok and h == "H2" and not m.get("gold_gate_pass", False):
                ok = False
            if ok:
                rejected.add(h)
                if other not in rejected:
                    level[other] += level[h]
                changed = True
    out: dict[str, dict[str, Any]] = {}
    for h in ("H1", "H2"):
        m = members[h]
        reasons = list(m.get("gates") or [])
        if m.get("p") is not None and float(m["min_attainable_p"]) > level[h]:
            reasons.append(f"minimum attainable p {float(m['min_attainable_p']):.4g} > level {level[h]:g}")
        if h in rejected:
            st = "confirmed"
        elif m.get("p") is None:
            st = "absent"
        elif reasons:
            st = GATE
        elif h == "H2" and float(m["p"]) <= level[h] and not m.get("gold_gate_pass", False):
            st = "not confirmed (gold-only direction gate)"
        else:
            st = "not confirmed"
        out[h] = {"status": st, "level": level[h], "rejected": h in rejected, "gate_reasons": reasons,
                  "p_in_procedure": 1.0 if (m.get("p") is None or reasons) else float(m["p"])}
    hb = members.get("H1b") or {}
    if {"H1", "H2"} <= rejected:
        reasons = list(hb.get("gates") or [])
        if hb.get("p") is not None and float(hb["min_attainable_p"]) > alpha:
            reasons.append(f"minimum attainable p {float(hb['min_attainable_p']):.4g} > level {alpha:g}")
        if hb.get("p") is None:
            st = "absent"
        elif reasons:
            st = GATE
        else:
            st = "confirmed" if float(hb["p"]) <= alpha else "not confirmed"
        out["H1b"] = {"status": st, "level": alpha, "rejected": st == "confirmed", "gate_reasons": reasons,
                      "tested": True}
    else:
        why = "H1 and H2 not both confirmed"
        out["H1b"] = {"status": "not tested", "level": 0.0, "rejected": False, "gate_reasons": [], "tested": False,
                      "note": f"{why}; estimate, CI and power printed descriptively"}
    return out


def holm_drop_gated(members: Mapping[str, Mapping[str, Any]], alpha: float = ALPHA) -> dict[str, Any]:
    """The labelled sensitivity of §7: Holm over the members of {H1, H2} that are not gated
    or absent (a family of one tests at ``alpha``). Never decides."""
    live = [h for h in ("H1", "H2") if members[h].get("p") is not None and not members[h].get("gates")]
    m = len(live)
    out: dict[str, Any] = {"family": live}
    ps = sorted(live, key=lambda h: float(members[h]["p"]))
    stop = False
    for i, h in enumerate(ps):
        lvl = alpha / (m - i)
        rej = not stop and float(members[h]["p"]) <= lvl
        if h == "H2" and not members[h].get("gold_gate_pass", False):
            rej = False
        stop = stop or not rej
        out[h] = {"level": lvl, "rejected": bool(rej)}
    return out


# ------------------------------------------------------------------ the analysis

class Data:
    """Records, the legs' outcomes and gates, the judge's labels."""

    def __init__(self, cfg: Mapping[str, Any]) -> None:
        import alias_study_run as ar
        import found10 as f10

        self.cfg = cfg
        self.records = asc.load_records(cfg["records"])
        if cfg.get("dry_run"):
            self.identity: dict[str, Any] = {"dry_run": True, "records_sha256": asc.sha256_file(cfg["records"])}
        else:
            self.identity = asc.verify_record_set(cfg["records"], self.records)
        self.bal, self.desc = asc.split_records(self.records)
        self.by_id = {r["record_id"]: r for r in self.records}
        runs = Path(cfg["runs_dir"])
        self.rows: dict[str, list[dict[str, Any]]] = {}
        self.gates: dict[str, str | None] = {}
        self.runs: dict[str, Any] = {}
        status = ar.run_status(runs)
        for c, g, r, rep in asc.all_legs():
            leg = L(c, g, r, rep)
            label = f10.arm_label(g, rep if rep == 2 else None)
            sp = runs / c / f"{label}.summary.json"
            if not sp.is_file():
                self.gates[leg] = f"{GATE}: leg not run"
                self.rows[leg] = []
                continue
            s = json.loads(sp.read_text())
            cond = asc.RETRIEVERS[r]
            gate = None
            if s["status"] != "complete":
                gate = f"{GATE}: run {c}/{label} is {s['status']}"
            elif (s["conditions"].get(cond) or {}).get("gate"):
                gate = s["conditions"][cond]["gate"]
            self.gates[leg] = gate
            self.runs[f"{c}/{label}"] = {"status": s["status"], "citable": s.get("citable"),
                                         "commit": (s.get("provenance") or {}).get("commit"),
                                         "summary_sha256": asc.sha256_file(sp)}
            rp = sp.with_name(f"{label}.jsonl")
            self.rows[leg] = [x for x in f10.read_jsonl_repair_tail(rp) if x["condition"] == cond] \
                if status[leg] != "missing" else []
        jdir = Path(cfg["judge_dir"])
        self.final: dict[tuple[str, str], dict[str, Any]] = {}
        for x in asc.read_jsonl(jdir / "final.jsonl") if (jdir / "final.jsonl").is_file() else []:
            if str(x.get("k", "")).startswith("pair|"):
                self.final[(x["record_id"], x["chunk_id"])] = x
        self.pair_paper: dict[tuple[str, str], str] = {}
        if (jdir / "pairs.jsonl").is_file():
            for p in asc.read_jsonl(jdir / "pairs.jsonl"):
                self.pair_paper[(p["record_id"], p["chunk_id"])] = p["c_paper"]
        self.judge_summary = json.loads((jdir / "judge-summary.json").read_text()) \
            if (jdir / "judge-summary.json").is_file() else {}

    def labeller(self, mode: str = "primary") -> Callable[[str, str], int | None]:
        """``primary``: the final label, unjudged = no; ``unjudged_yes``; ``tb_as_c`` / ``tb_as_e``:
        tiebreaker-labelled pairs set to that primary judge's own label."""
        fin = self.final

        def lab(rid: str, cid: str) -> int | None:
            x = fin.get((rid, cid))
            if x is None:
                return None
            if mode == "unjudged_yes" and x["source"] == "unjudged":
                return 1
            if mode in ("tb_as_c", "tb_as_e") and x["source"] in ("tiebreak", "tiebreak_r2", "unjudged"):
                v = x.get("c_label" if mode == "tb_as_c" else "e_label")
                return None if v is None else int(v)
            return None if x["label"] is None else int(x["label"])
        return lab

    def outcomes(self, mode: str = "primary") -> dict[str, dict[str, Any]]:
        lab = self.labeller(mode)
        return {leg: leg_outcomes(rows, self.by_id, lab, self.pair_paper) for leg, rows in self.rows.items()}


def cell_gates(spec: Spec, gates: Mapping[str, str | None]) -> list[str]:
    return [f"{leg}: {gates[leg]}" for _, leg in spec if gates.get(leg)]


def layer1(bal: Sequence[Mapping[str, Any]], outcomes: Mapping[str, Mapping[str, Any]],
           gates: Mapping[str, str | None], reach: Mapping[str, Any] | None, an: Mapping[str, Any],
           missing: str = "exclude", h2_spec: Spec = H2_SPEC) -> dict[str, Any]:
    g_recs = [r for r in bal if r["kind"] == "G"]
    # H1
    cl, v, miss1 = contrast_values(g_recs, outcomes, H1_SPEC, "hit", missing)
    h1 = {**member_test(cl, v, "greater", an), "missing": miss1}
    clg, vg, _ = contrast_values(g_recs, outcomes, H1_SPEC, "hit_gold", missing)
    h1["gold_only_net_points"] = round(100 * sum(vg) / len(vg), 4) if vg else None
    cg = c_gate(bal, outcomes, an, missing)
    n_g = len(g_recs)
    reach_ok, reach_note = False, "reach count not supplied"
    if reach is not None:
        if int(reach.get("of", -1)) != n_g:
            reach_note = f"reach count is of {reach.get('of')} G records, not the {n_g} here"
        else:
            reach_ok = int(reach["count"]) >= int(np.ceil(REACH_SHARE * n_g))
            reach_note = f"{reach['count']} of {n_g} (need {int(np.ceil(REACH_SHARE * n_g))})"
    h1_gates = cell_gates(H1_SPEC, gates)
    if h1["papers"] < MIN_CLUSTERS:
        h1_gates.append(f"{h1['papers']} papers < {MIN_CLUSTERS}")
    if cg["fires"]:
        h1_gates.append("C gate fired (rule C)")
    if not reach_ok:
        h1_gates.append(f"reach gate: {reach_note}")
    # H1b
    clb, vb, missb = h1b_values(bal, outcomes, "hit", missing)
    h1b = {**member_test(clb, vb, "greater", an), "missing_or_dropped": missb}
    h1b_gates = list(h1_gates)
    if h1b["papers"] < MIN_CLUSTERS and f"{h1b['papers']} papers < {MIN_CLUSTERS}" not in h1b_gates:
        h1b_gates.append(f"{h1b['papers']} papers < {MIN_CLUSTERS}")
    # H2
    cl2, v2, miss2 = contrast_values(bal, outcomes, h2_spec, "hit", missing)
    h2 = {**member_test(cl2, v2, "less", an), "missing": miss2}
    _, v2g, _ = contrast_values(bal, outcomes, h2_spec, "hit_gold", missing)
    gold_net = 100 * sum(v2g) / len(v2g) if v2g else None
    h2["gold_only_net_points"] = None if gold_net is None else round(gold_net, 4)
    gold_pass = gold_net is not None and gold_net < 0
    h2_gates = cell_gates(h2_spec, gates)
    if h2["papers"] < MIN_CLUSTERS:
        h2_gates.append(f"{h2['papers']} papers < {MIN_CLUSTERS}")
    members = {"H1": {**h1, "gates": h1_gates}, "H2": {**h2, "gates": h2_gates, "gold_gate_pass": gold_pass},
               "H1b": {**h1b, "gates": h1b_gates}}
    proc = layer1_procedure(members)
    res: dict[str, Any] = {}
    for h in ("H1", "H2", "H1b"):
        m = members[h]
        res[h] = {**{k: v for k, v in m.items() if k != "gates"}, **proc[h]}
        disc = int(m.get("gained", 0)) + int(m.get("lost", 0))
        lvl = proc[h]["level"] or ALPHA
        res[h]["mde_split"] = _mde_split(disc, lvl)
        n = int(m.get("n") or 0)
        res[h]["power_floor_normal"] = {f"q{q:g}_p{p:g}": round(apw.normal_power(n, q, p, 0.05 if lvl > 0.03 else 0.025), 4)
                                        for q, p in ((0.05, 0.85), (0.10, 0.70))} if n else None
        res[h]["realised_discordance"] = round(disc / n, 4) if n else None
    res["H1"]["c_gate"] = cg
    res["H1"]["reach"] = {"ok": reach_ok, "note": reach_note}
    res["H2"]["gold_gate_pass"] = gold_pass
    res["H1b"]["baseline_c_found10"] = cg["baseline_c_found10"]
    res["holm_drop_gated"] = holm_drop_gated(members)
    return res


def _mde_split(discordant: int, alpha: float) -> list[int] | None:
    for g in range(discordant + 1):
        if cs.mcnemar_exact(g, discordant - g, "greater") <= alpha:
            return [g, discordant - g]
    return None


def decision_rules(g_lift: Mapping[str, Any], c_cost: Mapping[str, Any], rr_gap: Mapping[str, Any],
                   growth: Mapping[str, Any]) -> dict[str, Any]:
    """§3.3, the three serving rules, thresholds fixed. A rule whose threshold is met at the point
    estimate but whose CI condition fails reads *not resolved*."""
    out: dict[str, Any] = {}
    gl, gci, cc = g_lift.get("net_points"), g_lift.get("ci95"), c_cost.get("net_points")
    lift_ok = gl is not None and gl >= 2
    ci_ok = gci is not None and gci[0] > 0
    cost_ok = cc is not None and cc <= 1
    fires = lift_ok and ci_ok and cost_ok
    out["attach_merged_glossary"] = {
        "fires": fires, "g_lift": gl, "g_lift_ci95": gci, "c_cost": cc,
        "reading": "attach" if fires else ("not resolved" if lift_ok and cost_ok and not ci_ok
                                           else "the rule does not fire"),
        "conditions": {"g_lift_ge_2": lift_ok, "g_lift_ci_lower_gt_0": ci_ok, "c_cost_le_1": cost_ok}}

    def adverse(f: Mapping[str, Any]) -> tuple[bool, bool, Any, Any]:
        v, ci = f.get("net_points"), f.get("ci95")
        point = v is not None and v <= -3
        excl = ci is not None and (ci[0] > 0 or ci[1] < 0)
        return point and excl, point and not excl, v, ci

    trails, unres, v, ci = adverse(rr_gap)
    out["keep_reranker_default"] = {
        "keep": not trails, "default_minus_bm25": v, "ci95": ci,
        "reading": "default trails BM25 by >= 3 points in the neighbour arm (CI excludes 0)" if trails
        else ("keep (not resolved)" if unres else "keep")}
    concern, unres, v, ci = adverse(growth)
    out["related_paper_growth_concern"] = {
        "concern": concern, "neighbour_minus_random": v, "ci95": ci,
        "reading": "a concern" if concern else ("not resolved" if unres else "not a concern by the rule")}
    return out


def layer2(d: Data, outcomes: Mapping[str, Mapping[str, Any]], an: Mapping[str, Any]) -> dict[str, Any]:
    bal = d.bal
    g = [r for r in bal if r["kind"] == "G"]
    c = [r for r in bal if r["kind"] == "C"]
    gates = d.gates

    def fig(recs: Sequence[Mapping[str, Any]], spec: Spec, key: str = "hit") -> dict[str, Any]:
        f = figure(recs, outcomes, spec, an, key, gates)
        if key == "hit":
            f["gold_only"] = figure(recs, outcomes, spec, an, "hit_gold")
        return f

    c_cost_spec: Spec = [(-a, leg) for a, leg in H1_SPEC]
    rr_spec: Spec = [(1.0, L("neighbour", "none", "default")), (-1.0, L("neighbour", "none", "bm25"))]
    out: dict[str, Any] = {"label": "Layer 2, descriptive"}
    out["g_lift"] = fig(g, H1_SPEC)
    out["c_cost"] = fig(c, c_cost_spec)
    out["default_minus_bm25_neighbour"] = fig(bal, rr_spec)
    out["neighbour_minus_random"] = fig(bal, H2_SPEC)
    out["H3"] = fig(bal, H3_SPEC)
    out["decisions"] = decision_rules(out["g_lift"], out["c_cost"], out["default_minus_bm25_neighbour"],
                                      out["neighbour_minus_random"])
    out["rr"] = {name: {"judged": figure(recs, outcomes, spec, an, "rr", gates),
                        "gold_only": figure(recs, outcomes, spec, an, "rr_gold", gates)}
                 for name, recs, spec in (("H1", g, H1_SPEC), ("H2", bal, H2_SPEC), ("H3", bal, H3_SPEC))}
    out["h1_by_alias_kind"] = {k: fig([r for r in g if r["alias_kind"] == k], H1_SPEC)
                               for k in ("symbol", "abbreviation", "both")}
    out["h1_by_alias_kind"]["both"]["note"] = "printed, never interpreted"
    coll = d.cfg.get("collision")
    if coll:
        flags = json.loads(Path(coll).read_text())
        out["h1_by_collision"] = {k: fig([r for r in g if bool(flags.get(r["record_id"])) == v], H1_SPEC)
                                  for k, v in (("collides", True), ("no_collision", False))}
    else:
        out["h1_by_collision"] = "absent (no collision annotation supplied)"
    other: dict[str, Any] = {}
    for corp in asc.CORPUS_ARMS:
        for ret in asc.RETRIEVERS:
            if (corp, ret) == ("base", "default"):
                continue
            spec: Spec = [(1.0, L(corp, "merged", ret)), (-1.0, L(corp, "none", ret))]
            other[f"H1@{corp}|{ret}"] = fig(g, spec)
    out["h1_other_cells"] = other
    out["h2_other"] = {f"H2@{gl}|{ret}": fig(bal, [(1.0, L("neighbour", gl, ret)), (-0.5, L("random1", gl, ret)),
                                                   (-0.5, L("random2", gl, ret))])
                       for gl in asc.GLOSSARY_ARMS for ret in asc.RETRIEVERS if (gl, ret) != ("none", "default")}
    out["neighbour_minus_base"] = fig(bal, [(1.0, L("neighbour", "none", "default")), (-1.0, L("base", "none", "default"))])
    out["random_minus_base"] = fig(bal, [(0.5, L("random1", "none", "default")), (0.5, L("random2", "none", "default")),
                                         (-1.0, L("base", "none", "default"))])
    out["random1_minus_random2"] = fig(bal, [(1.0, L("random1", "none", "default")),
                                             (-1.0, L("random2", "none", "default"))])
    cells: dict[str, Any] = {}
    for corp, gl, ret, rep in asc.all_legs():
        leg = L(corp, gl, ret, rep)
        o = outcomes.get(leg) or {}
        kinds = Counter((o.get(r["record_id"]) or {}).get("kind") for r in bal if o.get(r["record_id"]) is not None)
        cells[leg] = {"found10": found_rate(bal, outcomes, leg), "found10_gold": found_rate(bal, outcomes, leg, "hit_gold"),
                      "mean_rr": found_rate(bal, outcomes, leg, "rr"), "mean_rr_gold": found_rate(bal, outcomes, leg, "rr_gold"),
                      "hits_by_kind": {k: int(kinds.get(k, 0)) for k in ("gold", "same", "other")},
                      "descriptive_leg": {"found10": found_rate(d.desc, outcomes, leg),
                                          "found10_gold": found_rate(d.desc, outcomes, leg, "hit_gold")},
                      "gate": gates.get(leg)}
    out["cells"] = cells
    return out


def noise_floor(bal: Sequence[Mapping[str, Any]], outcomes: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    out = {}
    for corp in asc.CORPUS_ARMS:
        a, b = outcomes.get(L(corp, "none", "default")) or {}, outcomes.get(L(corp, "none", "default", 2)) or {}
        both = [r["record_id"] for r in bal if a.get(r["record_id"]) is not None and b.get(r["record_id"]) is not None]
        out[corp] = {"records": len(both),
                     "discordant": sum(a[x]["hit"] != b[x]["hit"] for x in both),
                     "discordant_gold": sum(a[x]["hit_gold"] != b[x]["hit_gold"] for x in both)}
    return out


def spot_flags(cfg: Mapping[str, Any], d: Data) -> dict[str, Any]:
    out: dict[str, Any] = {}
    sr = cfg.get("spot_records")
    if sr:
        k, n = int(sr["valid"]), int(sr["n"])
        out["record_validity"] = {"valid": k, "n": n, "rate": k / n, "wilson95": _wilson(k, n),
                                  "below_threshold": k / n < 0.85}
    else:
        out["record_validity"] = "absent (owner spot check not committed)"
    sj = cfg.get("spot_judge")
    if sj:
        rows = asc.read_jsonl(sj)
        fin_by_pair = {x["pair_id"]: x for x in d.final.values()}
        agree = n = 0
        per_arm: dict[str, Counter[str]] = defaultdict(Counter)
        for x in rows:
            f = fin_by_pair.get(x["pair_id"])
            if f is None:
                continue
            fl = int(f["label"] or 0)
            n += 1
            agree += int(fl == int(x["owner_label"]))
            if int(x["owner_label"]) == 0:
                per_arm[f["arm"]]["no"] += 1
                per_arm[f["arm"]]["final_yes"] += int(fl == 1)
        out["judge_validity"] = {"agree": agree, "n": n, "rate": agree / n if n else None,
                                 "below_threshold": bool(n and agree / n < 0.90),
                                 "false_yes_per_arm": {a: {"k": c["final_yes"], "n": c["no"],
                                                           "wilson95": _wilson(c["final_yes"], c["no"])}
                                                       for a, c in per_arm.items()}}
    else:
        out["judge_validity"] = "absent (owner spot check not committed)"
    return out


def _wilson(k: int, n: int) -> list[float] | None:
    import glossbench_alias_judge as jd
    return jd.wilson(k, n)


def analyse(cfg: Mapping[str, Any], d: Data | None = None) -> dict[str, Any]:
    d = d or Data(cfg)
    an = {"n_perm": int(cfg.get("n_perm", cs.N_FLIPS)), "n_boot": int(cfg.get("n_boot", cs.N_BOOT)),
          "perm_seed": asc.seed_int(asc.SEEDS["perm"]), "boot_seed": asc.seed_int(asc.SEEDS["boot"])}
    if not cfg.get("dry_run") and (an["n_perm"] != 10_000 or an["n_boot"] != 10_000):
        raise SystemExit("n_perm and n_boot are 10,000 (prereg §7); other values only in a dry run")
    reach = cfg.get("reach")
    prim = d.outcomes("primary")
    res: dict[str, Any] = {"schema": SCHEMA, "record_set": d.identity,
                           "seeds": {"perm": asc.SEEDS["perm"], "boot": asc.SEEDS["boot"], "rule": asc.SEED_RULE},
                           "n_perm": an["n_perm"], "n_boot": an["n_boot"], "leg_gates": dict(d.gates), "runs": d.runs}
    l1 = layer1(d.bal, prim, d.gates, reach, an)
    gap = figure(d.bal, prim, [(1.0, L("random1", "none", "default")), (-1.0, L("random2", "none", "default"))], an)
    within = within_draw_variation(l1["H2"].get("net_points"), gap["net_points"])
    l1["H2"]["draw_gap_points"] = gap["net_points"]
    l1["H2"]["within_random_draw_variation"] = within
    js = d.judge_summary
    l1["H2"]["false_yes_per_arm"] = {a: (js.get("drift", {}).get("scopes", {}).get(a) or {}).get("false_yes")
                                     for a in asc.ARM_GROUPS} if js else "absent (no judge summary)"
    res["layer1"] = l1
    res["layer2"] = layer2(d, prim, an)
    res["noise_floor"] = noise_floor(d.bal, prim)
    res["judge"] = {k: js.get(k) for k in ("pairs_finalized", "sources", "unjudged", "unjudged_share",
                                           "judge_incomplete", "per_arm_pairs", "drift")} if js else "absent"
    res["spot_checks"] = spot_flags(cfg, d)
    res["sensitivities"] = sensitivities(cfg, d, an, reach)
    return res


def within_draw_variation(h2_net: float | None, gap_net: float | None) -> bool:
    """§3.4: |H2's net effect| smaller than the draw-to-draw gap |random-1 - random-2|."""
    return h2_net is not None and gap_net is not None and abs(h2_net) < abs(gap_net)


def _subset(recs: Sequence[Mapping[str, Any]], cfg: Mapping[str, Any], name: str) -> list[Mapping[str, Any]] | None:
    sets = cfg.get("sensitivity_sets") or {}
    if name == "without_codex_verdicts":
        return [r for r in recs if r.get("verifier") == "claude"]
    path = sets.get(name)
    if not path:
        return None
    ids = {x.strip() for x in Path(path).read_text().splitlines() if x.strip()}
    if name == "without_calibration_overlap":
        return [r for r in recs if r["record_id"] not in ids]
    return [r for r in recs if str(r["paper"]) not in ids]


def _brief(l1: Mapping[str, Any]) -> dict[str, Any]:
    return {h: {k: l1[h].get(k) for k in ("status", "p", "net_points", "n", "papers")} for h in ("H1", "H2", "H1b")}


def sensitivities(cfg: Mapping[str, Any], d: Data, an: Mapping[str, Any], reach: Any) -> dict[str, Any]:
    """§7, declared; printed beside H1, H1b and H2, never deciding."""
    out: dict[str, Any] = {"label": "sensitivity, never deciding"}
    prim = d.outcomes("primary")
    for name in ("without_codex_verdicts", "without_calibration_overlap", "without_pilot_papers",
                 "without_validation_papers"):
        sub = _subset(d.bal, cfg, name)
        if sub is None:
            out[name] = "absent (list not supplied)"
            continue
        n_g = sum(r["kind"] == "G" for r in sub)
        out[name] = {"records": len(sub), **_brief(layer1(sub, prim, d.gates, reach_for(reach, n_g), an))}
    for mode in ("unjudged_yes", "tb_as_c", "tb_as_e"):
        out[mode] = _brief(layer1(d.bal, d.outcomes(mode), d.gates, reach, an))
    out["family_missing_as_not_found"] = _brief(layer1(d.bal, prim, d.gates, reach, an, missing="as_not_found"))
    for draw in ("random1", "random2"):
        spec: Spec = [(1.0, L("neighbour", "none", "default")), (-1.0, L(draw, "none", "default"))]
        out[f"H2_vs_{draw}_alone"] = _brief(layer1(d.bal, prim, d.gates, reach, an, h2_spec=spec))["H2"]
    return out


def reach_for(reach: Mapping[str, Any] | None, n_g: int) -> Mapping[str, Any] | None:
    """A subset sensitivity reads the reach gate as the full set's verdict, rescaled: the gate
    is a property of the build, not of the subset."""
    if reach is None:
        return None
    ok = int(reach["count"]) >= int(np.ceil(REACH_SHARE * int(reach["of"])))
    return {"count": n_g if ok else 0, "of": n_g}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--public", type=Path, default=None)
    args = ap.parse_args(argv)
    cfg = json.loads(args.config.read_text())
    d = Data(cfg)
    res = analyse(cfg, d)
    try:
        from ragstack.provenance import experiment_provenance
        prov = experiment_provenance()
    except Exception:  # noqa: BLE001
        prov = {}
    res["provenance"] = prov
    res["dry_run"] = bool(cfg.get("dry_run"))
    res["citable"] = bool(not cfg.get("dry_run") and prov.get("dirty") is False and prov.get("untracked") == 0
                          and all(r.get("citable") for r in d.runs.values()))
    dst = args.out or Path(cfg["judge_dir"]).parent / "results-alias-study.json"
    dst.write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")
    if args.public:
        forbidden = {r["record_id"] for r in d.records} | {str(r["paper"]) for r in d.records} | \
                    {r["gold_chunks"][0] for r in d.records} | {c for _, c in d.final}
        texts = [r["question"] for r in d.records]
        bad = asc.public_problems(res, forbidden, texts)
        if bad:
            print(f"REFUSED: the public copy would carry {len(bad)} private string(s) ({sorted(set(bad))})",
                  file=sys.stderr)
            return 1
        args.public.write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps({h: {k: res["layer1"][h].get(k) for k in ("status", "p", "net_points", "level")}
                      for h in ("H1", "H2", "H1b")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
