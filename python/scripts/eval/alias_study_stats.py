#!/usr/bin/env python
"""Alias study (OA-1000): the statistics (DRAFT prereg v3 §3, §3.3, §3.4, §6.6, §7, §8, §9.1).

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

**Layer 1: two families (§3.1, R1), each at one-sided alpha = 0.05.** No level passes between
them. *Family G*: **H1** (G records, base x default, merged - none, per-paper sum) by the exact
conditional paper sign-flip (``chunktrial_stats.sign_flip_test_exact``); **H1b** (per paper,
the mean of its G records' H1 difference minus its C record's) is tested **only if H1 is
confirmed**, at 0.05 in fixed sequence, by the exact G / C label randomization
(``chunktrial_stats.cluster_randomization_test`` over ``cluster_randomization_lattice``);
otherwise H1b is *absent (not tested)* and only its estimate, CI and power are printed.
*Family N*: **H2** (all balanced records, default x none, neighbour - mean(random-1,
random-2), per-paper sum) by the exact sign-flip, *confirmed* only when the test rejects
**and** the gold-only net is below 0 (the direction gate; otherwise *not confirmed (gold-only
direction gate)*). A member is **GATE-NOT-EVALUABLE** (a third answer, p = 1.0 in its family)
when its minimum attainable p exceeds 0.05, it has fewer than 8 papers, any of its cells is
gated by the runner, or, for H1 (and so H1b), the C gate fires (rule C: evaluated only when
dG > 0; 95 % paper-cluster bootstrap, seed ``261101|alias|boot``) or the reach gate fails
(fewer than 80 % of the G records render their own entry; a reach count not supplied fails
it). Monte Carlo (10,000 draws, seed ``261101|alias|perm``, (b + 1)/(m + 1)) only beyond the
lattice.

**Beside H2 (§3.4):** the gold-only net, the draw-to-draw gap and its flag, the per-arm judge
false-yes rates and the **judge false-yes bound** B = 100 U J_r / N with **U = max(U_random,
U_pooled)** (R13, owner 2026-11-03), the Wilson 95 % upper limits (rounded up) of the share of Opus-*not* among
the drift pairs the final label calls *answers*, in the random arm and over the three arms.

**Layer 2 (§3.3), descriptive.** Every figure in points with a 95 % paper-bootstrap percentile
CI (10,000 resamples, seed ``261101|alias|boot``) and, for found@10 contrasts, the equivalence
reading (90 % CI within +-delta, delta the realised 80 %-power MDE). The serving rules,
mechanically (:func:`decision_rules`): *attach* in order harm (L_hi < 0), **C cost (R12, owner
2026-11-03): do not attach when dC < -1 point or C_lo < -3.6 points**, attach when L >= 2,
L_lo > 0, dC >= -1 and C_lo >= -3.6, else not resolved; *reranker* non-inferiority at -3
points; *growth* a concern at <= -3 points with the CI excluding 0, *not a concern* only
through the equivalence bound, else not resolved. The rules compare unrounded figures. The
Layer-1 verdict governs the wording (:func:`serving_statements`); every *attach* statement adds
that a C cost smaller than about 3.6 points is not ruled out (R12).

**Before anything is read (§5.7, §5.6)** (:func:`study_preflight`): a **pilot** directory is
refused outright (R28, owner 2026-11-03: the pilot stays blind to outcomes, and this module writes
every cell's found@10 and found@10-gold; the pilot's only reader is ``alias_study_pilot.py``); the
``STUDY-MODE`` of ``runs_dir`` and ``judge_dir`` must be this config's mode,
:func:`alias_study_judge.analysis_gate` must pass (stage A committed and verified;
``spot_checks: {records, judge}`` present, waived and recorded only in a dry run), and
``judge_dir`` must be the directory stage A was committed from. Legs are read through :func:`alias_study_run.leg_files` (the 29 legs of
:func:`alias_study_common.all_legs`); a leg is complete only as
:func:`alias_study_run.run_status` reads it (a summary without the runner's study block, or
without its mode or stage, was killed mid-write and is gated as incomplete); a complete run
measured in another mode is refused; found10's inactive-reranker verdict gates the default leg.

**Two analysis stages** (``analysis_stage``). ``A``: Layer 1 after stage A (and its
sensitivities); every found@10 pair the 14 judging-stage-A legs demand
(:func:`alias_study_judge.demanded`) must carry a final label; no Layer 2. ``B`` (default):
everything, every found@10 and RR@10 pair of the 29 legs labelled. In both, Layer 1 is
cross-checked against the frozen ``stage-a/hits.jsonl`` and ``stage-a/summaries/``
(:func:`check_frozen_stage_a`), and U reads the frozen stage-A judge summary.

**Inputs.** Every file read is hashed into ``inputs`` (:func:`input_hashes`): the records, the
judge's ``final.jsonl`` / ``pairs.jsonl`` / ``judge-summary.json``, the frozen stage-A files and
the commit's sha256, every run summary and row file with its collection build and census source,
both spot checks, and every optional file the config names; plus the config's canonical sha256.

**Sensitivities (§7), never deciding.** The declared ones (subsets, judge modes, missing as
not found, H2 against each draw) and the fixed ones of R6: the studentized sign-flip (H1,
H2: an identity check, deciding nothing), the paper-cluster bootstrap-t (H1, H2; the weak-null
re-test), topic clustering (H1, H2), H2 with the chunk-volume covariate (Freedman–Lane sign-flip,
studentized intercept), the XML-only stratum, H2
by citation-count and text-similarity tercile (cut points read from a committed file), the
origin counts and the placebo leg. **Fragile (owner 2026-11-03):** only H1 and H2, and only
through the bootstrap-t and topic clustering (H1) plus the volume covariate (H2); a
subgroup never makes anything fragile, H1b is exempt, and *fragile* never changes a label.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import re
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
SCHEMA = "alias-study-results/2"
Spec = list[tuple[float, str]]

#: §3.3 serving rules (thresholds judgement-based, fixed; owner 2026-11-03 for the C cost)
ATTACH_LIFT_POINTS = 2.0
C_COST_POINT = -1.0     # do not attach when the dC point estimate is below this (strictly)
C_COST_LOWER = -3.6     # do not attach when the dC 95 % lower bound is below this (strictly)
RERANK_MARGIN = -3.0    # non-inferiority margin of default - BM25
GROWTH_MARGIN = -3.0    # neighbour - random at or below this, with the CI excluding 0
#: §3.4: U = max(U of the random arm, U pooled over the three arms) (owner 2026-11-03)
FALSE_YES_SCOPES = ("random", "pooled")
#: §7 (owner 2026-11-03): the only sensitivities that can make a member fragile. The studentized
#: sign-flip is printed as an identity check (its exact p is the primary's) and decides nothing.
FRAGILE_BY: dict[str, tuple[str, ...]] = {
    "H1": ("bootstrap_t", "topic_clustering"),
    "H2": ("bootstrap_t", "topic_clustering", "volume_covariate"),
}
CONFIRMED = "confirmed"
NOT_CONFIRMED = "not confirmed"
NOT_CONFIRMED_GOLD = "not confirmed (gold-only direction gate)"
NOT_TESTED = "absent (not tested)"
TERCILE_RULE = "T1: v <= c1; T2: c1 < v <= c2; T3: v > c2 (cut points read, never computed here)"


def L(c: str, g: str, r: str, rep: int = 1) -> str:  # noqa: N802 - a cell name, short on purpose
    return asc.leg_id(c, g, r, rep)


H1_SPEC: Spec = [(1.0, L("base", "merged", "default")), (-1.0, L("base", "none", "default"))]
H2_SPEC: Spec = [(1.0, L("neighbour", "none", "default")), (-0.5, L("random1", "none", "default")),
                 (-0.5, L("random2", "none", "default"))]
H3_SPEC: Spec = [(1.0, L("neighbour", "none", "default")), (-1.0, L("base", "none", "default")),
                 (-1.0, L("neighbour", "none", "bm25")), (1.0, L("base", "none", "bm25"))]
#: §5.3 (R7): the placebo leg, base x default only; read when the runner wrote it
PLACEBO_LEG = L(*asc.PLACEBO_LEG)


# ------------------------------------------------------------------ outcomes

def leg_outcomes(rows: Sequence[Mapping[str, Any]], records: Mapping[str, Mapping[str, Any]],
                 label: Callable[[str, str], int | None], pair_paper: Mapping[tuple[str, str], str]
                 ) -> dict[str, dict[str, Any] | None]:
    """Per record: hit, hit_gold, rr, rr_gold, kind, or None for a failed call. ``kind`` says
    what the hit rests on: ``gold`` whenever the gold is in the top 10 (whatever is ranked above
    it), else the first top-10 chunk labelled *answers*, ``same`` or ``other`` paper. So the
    split does not move when stage B adds the RR@10 labels above the gold."""
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
        kind = "gold" if rank_gold is not None else None
        for i, c in enumerate(top, 1):
            if c == gold:
                rank_any = i
                break
            if label(rid, c) == 1:
                rank_any = i
                if kind is None:
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


def boot_raw(sums: np.ndarray, counts: np.ndarray, n_boot: int, seed: int) -> list[float] | None:
    """``[lo95, hi95, lo90, hi90]`` of the paper-bootstrap percentile CIs of 100 x sum / n
    (points), unrounded, from one set of resamples (seed ``261101|alias|boot``; the 90 % CI is
    the equivalence bound, §3.3). The rules compare these; the printed CIs are rounded."""
    if sums.size == 0:
        return None
    vals = cs._bootstrap_net_shares(np.asarray(sums, float), np.asarray(counts, float), n_boot, seed)
    return [100 * float(x) for x in np.percentile(vals, [2.5, 97.5, 5.0, 95.0])]


def _r4(xs: Sequence[float] | None) -> list[float] | None:
    return None if xs is None else [round(float(x), 4) for x in xs]


def boot_cis(sums: np.ndarray, counts: np.ndarray, n_boot: int, seed: int
             ) -> tuple[list[float] | None, list[float] | None]:
    """The 95 % and the 90 % CI of :func:`boot_raw`, rounded to 4 decimals for printing."""
    raw = boot_raw(sums, counts, n_boot, seed)
    return (None, None) if raw is None else (_r4(raw[:2]), _r4(raw[2:]))


def boot_ci(sums: np.ndarray, counts: np.ndarray, n_boot: int, seed: int) -> list[float] | None:
    """95 % paper-bootstrap percentile CI of 100 x sum / n (points)."""
    return boot_cis(sums, counts, n_boot, seed)[0]


def equivalence(n: int, discordant: int, ci90: list[float] | None) -> dict[str, Any]:
    """§3.3 *statements of no effect*: the figure may read *no effect larger than delta points*
    only when its 90 % CI lies within +-delta, delta the realised 80 %-power MDE (§8's formula at
    the contrast's realised discordance). Otherwise *not resolved*."""
    if not n or ci90 is None:
        return {"delta_points": None, "within": False, "discordant": discordant, "reading": "not resolved"}
    if discordant == 0:
        # owner 2026-11-03: zero discordance reads *not resolved*, the discordant count beside it
        return {"delta_points": None, "within": False, "realised_discordance": 0.0, "discordant": 0,
                "reading": "not resolved", "note": "no discordant records: the MDE is 0, so no equivalence bound"}
    q = discordant / n
    delta_exact = apw.mde80_points(n, q)
    delta = round(delta_exact, 4)
    within = -delta_exact <= ci90[0] and ci90[1] <= delta_exact
    return {"delta_points": delta, "realised_discordance": round(q, 4), "discordant": discordant,
            "ci90": _r4(ci90), "within": bool(within),
            "reading": f"no effect larger than {delta:g} points" if within else "not resolved"}


def figure(recs: Sequence[Mapping[str, Any]], outcomes: Mapping[str, Mapping[str, Any]], spec: Spec,
           an: Mapping[str, Any], key: str = "hit", gates: Mapping[str, str | None] | None = None,
           missing: str = "exclude") -> dict[str, Any]:
    """A Layer-2 figure: net in points, the CI, the counts, the gate verdicts of its cells."""
    cl, v, miss = contrast_values(recs, outcomes, spec, key, missing)
    _, sums, cnt = cluster_sums(cl, v)
    n = len(v)
    arr = np.asarray(v)
    raw = boot_raw(sums, cnt, int(an["n_boot"]), int(an["boot_seed"]))
    net = 100 * float(arr.sum()) / n if n else None
    out: dict[str, Any] = {"n": n, "missing": miss, "papers": int(sums.size),
                           "net_points": None if net is None else round(net, 4), "net_exact": net,
                           "ci95": _r4(raw[:2]) if raw else None, "ci95_exact": raw[:2] if raw else None,
                           "gained": int((arr > 0).sum()), "lost": int((arr < 0).sum()),
                           "cells": [leg for _, leg in spec]}
    if key in ("hit", "hit_gold"):
        out["equivalence"] = equivalence(n, out["gained"] + out["lost"], raw[2:] if raw else None)
    if gates is not None:
        out["cell_gates"] = {leg: gates.get(leg) for _, leg in spec if gates.get(leg)}
    return out


def found_rate(recs: Sequence[Mapping[str, Any]], outcomes: Mapping[str, Mapping[str, Any]], leg: str,
               key: str = "hit") -> dict[str, Any]:
    vals = [o[key] for r in recs if (o := (outcomes.get(leg) or {}).get(r["record_id"])) is not None]
    n = len(vals)
    return {"n": n, "found": float(sum(vals)) if key.startswith("rr") else int(sum(vals)),
            "points": round(100 * sum(vals) / n, 4) if n else None}


# ------------------------------------------------------------------ Layer 1: the tests

def member_test(clusters: Sequence[str], vals: Sequence[float], alternative: str, an: Mapping[str, Any]
                ) -> dict[str, Any]:
    """H1's and H2's test (§7): the exact conditional paper sign-flip p (H1: the sharp null per
    question; H2: per-paper symmetry of the mean difference)."""
    names, sums, cnt = cluster_sums(clusters, vals)
    if sums.size == 0:
        return {"n": 0, "papers": 0, "p": None, "min_attainable_p": 1.0, "n_nonzero": 0}
    t = cs.sign_flip_test_exact(sums, n_flips=int(an["n_perm"]), seed=int(an["perm_seed"]),
                                alternative=alternative)  # type: ignore[arg-type]
    return {**_member_figures(vals, sums, cnt, alternative, an), "test": "paper sign-flip (exact)",
            "n_nonzero": int(t["n_pages_nonzero"]), "statistic": t["statistic"], "p": t["p"],
            "p_exact": t["p_exact"], "p_perm": t["p_perm"], "p_mid": t["p_mid"], "p_method": t["p_method"],
            "min_attainable_p": float(t["min_attainable_p"])}


def member_randomization(clusters: Sequence[str], vals: Sequence[float], supports: Sequence[Sequence[float]],
                         alternative: str, unit: str, an: Mapping[str, Any]) -> dict[str, Any]:
    """H1b's test (§7, owner 2026-11-02): the exact label-exchangeability randomization
    p (``chunktrial_stats.cluster_randomization_test``, exact by ``cluster_randomization_lattice``;
    Monte Carlo ``(b + 1)/(m + 1)`` with ``n_perm`` draws, seed ``perm_seed``, only beyond the
    lattice threshold). ``supports[i]`` is unit ``i``'s statistic under each equally likely
    relabelling; ``unit`` names the unit (paper for H1b). ``min_attainable_p`` is the test's
    exact floor."""
    names, sums, cnt = cluster_sums(clusters, vals)
    if sums.size == 0:
        return {"n": 0, "papers": 0, "p": None, "min_attainable_p": 1.0, "n_nonzero": 0}
    t = cs.cluster_randomization_test(supports, float(np.sum(vals)), n_draws=int(an["n_perm"]),
                                      seed=int(an["perm_seed"]), alternative=alternative)  # type: ignore[arg-type]
    return {**_member_figures(vals, sums, cnt, alternative, an),
            "test": f"label-exchangeability randomization (unit: {unit})",
            "n_nonzero": int(t["n_nonzero"]), "statistic": t["statistic"], "p": t["p"], "p_exact": t["p_exact"],
            "p_perm": t["p_perm"], "p_mid": t["p_mid"], "p_method": t["p_method"],
            "min_attainable_p": float(t["min_attainable_p"])}


def _member_figures(vals: Sequence[float], sums: np.ndarray, cnt: np.ndarray, alternative: str,
                    an: Mapping[str, Any]) -> dict[str, Any]:
    arr = np.asarray(vals)
    raw = boot_raw(sums, cnt, int(an["n_boot"]), int(an["boot_seed"]))
    return {"n": len(vals), "papers": int(sums.size), "net_points": round(100 * float(arr.sum()) / len(vals), 4),
            "ci95": _r4(raw[:2]) if raw else None, "ci90": _r4(raw[2:]) if raw else None,
            "ci90_exact": raw[2:] if raw else None,
            "gained": int((arr > 0).sum()), "lost": int((arr < 0).sum()), "alternative": alternative}


def exchange_supports(recs: Sequence[Mapping[str, Any]], outcomes: Mapping[str, Mapping[str, Any]], spec: Spec,
                      key: str = "hit", missing: str = "exclude") -> list[list[float]]:
    """Per record kept by :func:`contrast_values` (same order, same exclusion, a missing outcome
    scored 0 under ``missing != "exclude"``): the contrast ``Σ c_i x_π(i)`` under every
    permutation ``π`` of the spec's leg labels over the record's outcomes ``x`` (H2: the three
    arm labels, 3! = 6 relabellings, equally likely under label exchangeability). Not used by
    :func:`layer1`: the arm-label randomization was decided against for H2 (§7, S8); kept for
    the test that shows the wired H2 p is not it."""
    out: list[list[float]] = []
    coefs = [c for c, _ in spec]
    for r in recs:
        xs: list[float] = []
        miss = False
        for _, leg in spec:
            o = (outcomes.get(leg) or {}).get(r["record_id"])
            if o is None:
                miss = True
                xs.append(0.0)
            else:
                xs.append(float(o[key]))
        if miss and missing == "exclude":
            continue
        out.append([sum(c * xs[i] for c, i in zip(coefs, perm, strict=True))
                    for perm in itertools.permutations(range(len(spec)))])
    return out


def h1b_label_supports(recs: Sequence[Mapping[str, Any]], outcomes: Mapping[str, Mapping[str, Any]],
                       key: str = "hit", missing: str = "exclude"
                       ) -> tuple[list[str], list[float], list[list[float]], int]:
    """Per paper: the H1b score (mean over its G records of D minus the mean over its C records,
    D = merged - none, base x default), and its values under every assignment of the paper's
    G / C labels to its remaining records (``n choose n_C`` assignments: 3 for 2 G + 1 C),
    equally likely under label exchangeability. A paper whose C record, or every G record, is
    excluded is dropped (counted)."""
    by: dict[str, dict[str, list[float]]] = defaultdict(lambda: {"G": [], "C": []})
    miss = 0
    for r in recs:
        _, v, m = contrast_values([r], outcomes, H1_SPEC, key, missing)
        miss += m
        if v:
            by[str(r["paper"])][r["kind"]].append(v[0])
    clusters: list[str] = []
    vals: list[float] = []
    supports: list[list[float]] = []
    dropped = 0
    for paper, d in by.items():
        if not d["G"] or not d["C"]:
            dropped += 1
            continue
        clusters.append(paper)
        vals.append(sum(d["G"]) / len(d["G"]) - sum(d["C"]) / len(d["C"]))
        xs = d["G"] + d["C"]
        n_c = len(d["C"])
        sup = []
        for cs_idx in itertools.combinations(range(len(xs)), n_c):
            g = [x for i, x in enumerate(xs) if i not in cs_idx]
            c = [xs[i] for i in cs_idx]
            sup.append(sum(g) / len(g) - sum(c) / len(c))
        supports.append(sup)
    return clusters, vals, supports, miss + dropped


def h1b_values(recs: Sequence[Mapping[str, Any]], outcomes: Mapping[str, Mapping[str, Any]], key: str = "hit",
               missing: str = "exclude") -> tuple[list[str], list[float], int]:
    """Per paper: mean over its G records of D minus its C record's D, D = merged - none (base x
    default). A paper whose C record, or every G record, is excluded is dropped (counted)."""
    clusters, vals, _, n = h1b_label_supports(recs, outcomes, key, missing)
    return clusters, vals, n


def c_gate(recs: Sequence[Mapping[str, Any]], outcomes: Mapping[str, Mapping[str, Any]], an: Mapping[str, Any],
           missing: str = "exclude") -> dict[str, Any]:
    """Rule C (§3.1; owner 2026-11-01, O1). Evaluated only when dG > 0 (H1 is one-sided):
    ``moot`` otherwise. Fires when (a) dC > 0, (b) |dC| >= |dG| / 2 and (c) dC's 95 %
    paper-cluster bootstrap CI (seed ``261101|alias|boot``) excludes 0."""
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
                           "baseline_c_found10": base_c, "headroom_records": headroom_records,
                           "headroom_points": round(100 * headroom_records / n_c, 4)
                           if n_c and headroom_records is not None else None}
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


# ------------------------------------------------------------------ Layer 1: the two families

def _level_reasons(m: Mapping[str, Any], alpha: float) -> list[str]:
    """The gate reasons of a member at level ``alpha``: its level-independent gates plus a
    minimum attainable p above the level (§9.1)."""
    reasons = list(m.get("gates") or [])
    if m.get("p") is not None and float(m["min_attainable_p"]) > alpha:
        reasons.append(f"minimum attainable p {float(m['min_attainable_p']):.4g} > level {alpha:g}")
    return reasons


def _status(m: Mapping[str, Any], reasons: Sequence[str], alpha: float, gold_gate: bool | None = None) -> str:
    if m.get("p") is None:
        return "absent"
    if reasons:
        return GATE
    if float(m["p"]) > alpha:
        return NOT_CONFIRMED
    if gold_gate is not None and not gold_gate:
        return NOT_CONFIRMED_GOLD
    return CONFIRMED


def family_g(h1: Mapping[str, Any], h1b: Mapping[str, Any], alpha: float = ALPHA) -> dict[str, dict[str, Any]]:
    """Family G (§3.1, R1): H1 at ``alpha``; H1b at ``alpha`` in fixed sequence, tested **only
    if H1 is confirmed**. FWER <= alpha in the strong sense by the fixed-sequence argument.

    ``h1`` / ``h1b``: ``p`` (None = absent), ``gates`` (level-independent gate reasons) and
    ``min_attainable_p``. Returns per member ``status``, ``family``, ``level`` (None when not
    tested), ``tested``, ``rejected``, ``gate_reasons`` and ``p_in_procedure`` (1.0 for a gated
    or absent member, never shrinking the family)."""
    r1 = _level_reasons(h1, alpha)
    st1 = _status(h1, r1, alpha)
    out: dict[str, dict[str, Any]] = {
        "H1": {"status": st1, "family": "G", "level": alpha, "tested": h1.get("p") is not None,
               "rejected": st1 == CONFIRMED, "gate_reasons": r1,
               "p_in_procedure": 1.0 if (h1.get("p") is None or r1) else float(h1["p"])}}
    if st1 == CONFIRMED:
        rb = _level_reasons(h1b, alpha)
        stb = _status(h1b, rb, alpha)
        out["H1b"] = {"status": stb, "family": "G", "level": alpha, "tested": True, "rejected": stb == CONFIRMED,
                      "gate_reasons": rb,
                      "p_in_procedure": 1.0 if (h1b.get("p") is None or rb) else float(h1b["p"])}
    else:
        out["H1b"] = {"status": NOT_TESTED, "family": "G", "level": None, "tested": False, "rejected": False,
                      "gate_reasons": [], "p_in_procedure": None,
                      "note": f"H1 is {st1}: H1b is tested only if H1 is confirmed (fixed sequence, §3.1); "
                              "its estimate, CI and power at 0.05 are printed descriptively"}
    return out


def family_n(h2: Mapping[str, Any], alpha: float = ALPHA) -> dict[str, dict[str, Any]]:
    """Family N (§3.1, R1): H2 alone at ``alpha``, *confirmed* only when the test rejects and
    the gold-only direction gate passes (``h2["gold_gate_pass"]``)."""
    r = _level_reasons(h2, alpha)
    st = _status(h2, r, alpha, gold_gate=bool(h2.get("gold_gate_pass", False)))
    return {"H2": {"status": st, "family": "N", "level": alpha, "tested": h2.get("p") is not None,
                   "rejected": st == CONFIRMED, "gate_reasons": r,
                   "p_in_procedure": 1.0 if (h2.get("p") is None or r) else float(h2["p"])}}


def retest_verdict(p: float | None, min_p: float, alpha: float = ALPHA, gold_gate: bool | None = None) -> str:
    """A sensitivity's own verdict at the member's level (§7): the member's statuses, with
    GATE-NOT-EVALUABLE when its own minimum attainable p exceeds the level."""
    m = {"p": p, "min_attainable_p": min_p}
    return _status(m, _level_reasons(m, alpha), alpha, gold_gate)


# ------------------------------------------------------------------ the analysis inputs

ANALYSIS_STAGES = ("A", "B")
#: config keys of the first version, replaced by ``spot_checks: {records, judge}`` (§5.7, R15)
RETIRED_KEYS = ("spot_records", "spot_judge", "field_coverage")
INACTIVE_RERANKER = f"{GATE}: the default ranking equals the norerank ranking on every record (inactive reranker)"


def study_preflight(cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Before any outcome is read (§5.7): the modes agree, the analysis gate passes, and the
    judge directory is the one stage A was committed from.

    * A **pilot** config is refused (R28, owner 2026-11-03): the statistics write every cell's
      found@10 and found@10-gold, and the pilot stays blind to outcomes; ``alias_study_pilot.py``
      is the pilot's only reader.
    * ``STUDY-MODE`` of ``runs_dir`` and of ``judge_dir`` must both be this config's mode
      (``dry_run`` / ``main``, :func:`alias_study_run.config_mode`), and a pilot directory under a
      main or dry-run config is refused by the same marker check (§5.6).
    * :func:`alias_study_judge.analysis_gate` (``spot_checks: {records, judge}``): stage A
      committed and verified, both owner spot checks present (waived, and recorded, only in a dry
      run). The commit's mode must be this mode.
    * The commit file, the committed ``final`` and ``pairs`` files and ``stage-a/hits.jsonl``
      must sit in ``judge_dir`` (and its ``stage-a/``): the labels analysed are the committed ones.
    Raises SystemExit."""
    import alias_study_judge as aj
    import alias_study_run as ar

    old = [k for k in RETIRED_KEYS if k in cfg]
    if old:
        raise SystemExit(f"config keys {old} are retired: spot checks are spot_checks: {{records, judge}}, and the "
                         "field coverage is read from the leg summaries")
    stage = str(cfg.get("analysis_stage", "B"))
    if stage not in ANALYSIS_STAGES:
        raise SystemExit(f"analysis_stage {stage!r}: 'A' (Layer 1 after stage A) or 'B' (everything, after stage B)")
    runs, jdir = Path(cfg["runs_dir"]), Path(cfg["judge_dir"])
    mode = ar.config_mode(cfg)
    if mode == "pilot":
        raise SystemExit("a pilot config: the statistics never read the pilot (R28, owner 2026-11-03: the pilot stays "
                         "blind to outcomes, and this module writes every cell's found@10); the pilot's only reader is "
                         "alias_study_pilot.py")
    for name, d in (("runs_dir", runs), ("judge_dir", jdir)):
        m = d / ar.MODE_MARKER
        have = m.read_text(encoding="utf-8").strip() if m.is_file() else None
        if have != mode:
            raise SystemExit(f"{name}/{ar.MODE_MARKER} is {have!r}, the statistics config is {mode!r}: a directory of "
                             "one mode is never analysed as another (§5.6)")
    gate = aj.analysis_gate(runs, cfg.get("spot_checks"), dry_run=bool(cfg.get("dry_run")))
    if gate.get("mode") != mode:
        raise SystemExit(f"stage A was committed in mode {gate.get('mode')!r}, the statistics config is {mode!r}")
    marker = ar.read_commit_marker(runs) or {}
    cpath = Path(marker["commit"]).resolve()
    commit = json.loads(cpath.read_text(encoding="utf-8"))
    want = jdir.resolve()
    where = {"commit file": (cpath.parent, want)}
    for f in commit.get("files") or []:
        if f["name"] in ("final", "pairs"):
            where[f["name"]] = (Path(f["path"]).resolve().parent, want)
        elif f["name"] == "stage_a_hits":
            where[f["name"]] = (Path(f["path"]).resolve().parent, want / "stage-a")
    for need in ("final", "pairs", "stage_a_hits"):
        if need not in where:
            raise SystemExit(f"the stage-A commit lists no {need!r} file")
    bad = sorted(k for k, (have, exp) in where.items() if have != exp)
    if bad:
        raise SystemExit(f"judge_dir is not the judge directory stage A was committed from ({bad}): the labels "
                         "analysed must be the committed ones")
    return {"mode": mode, "analysis_stage": stage, "gate": gate,
            "stage_a_commit": {"sha256": gate["stage_a_commit_sha256"], "verified": True,
                               "public_tracked_at_head": gate.get("public_tracked_at_head")},
            "spot_checks_waiver": gate.get("spot_checks_waiver")}


class Data:
    """Records, the legs' outcomes and gates, the judge's labels. :func:`study_preflight` runs
    first: nothing is read before the gate passes."""

    def __init__(self, cfg: Mapping[str, Any]) -> None:
        import alias_study_run as ar
        import found10 as f10

        self.cfg = cfg
        self.preflight = study_preflight(cfg)
        self.stage = self.preflight["analysis_stage"]
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
        for c, g, r, rep in asc.all_legs():                  # the 29 legs, the placebo leg among them
            leg = L(c, g, r, rep)
            rp, sp = ar.leg_files(runs, c, g, r, rep)
            key = f"{c}/{sp.name.removesuffix('.summary.json')}"
            if not sp.is_file():
                self.gates[leg] = f"{GATE}: leg not run"
                self.rows[leg] = []
                continue
            s = json.loads(sp.read_text())
            cond = asc.RETRIEVERS[r]
            cst = (s.get("conditions") or {}).get(cond) or {}
            gate = None
            # the runner's own reading (alias_study_run.run_status): a summary whose study block, or
            # its mode or stage, is missing was killed between found10's write and the runner's, and
            # is incomplete whatever its status says
            run_st = status.get(leg)
            if run_st != "complete":
                gate = f"{GATE}: run {key} is {run_st}"
            elif cst.get("gate"):
                gate = cst["gate"]
            elif r == "default" and _identical_to_norerank(cst.get("identical_to_norerank")):
                gate = INACTIVE_RERANKER
            self.gates[leg] = gate
            if key not in self.runs:
                st = s.get("study") or {}
                if run_st == "complete" and st.get("mode") != self.preflight["mode"]:
                    raise SystemExit(f"run {key} was measured in mode {st.get('mode')!r}, the statistics config is "
                                     f"{self.preflight['mode']!r} (§5.6)")
                cen = s.get("census") or {}
                self.runs[key] = {
                    "status": run_st, "citable": s.get("citable"),
                    "commit": (s.get("provenance") or {}).get("commit"), "summary_sha256": asc.sha256_file(sp),
                    "rows_sha256": asc.sha256_file(rp) if rp.is_file() else None,
                    "mode": st.get("mode"), "stage": st.get("stage"), "leg_stages": st.get("leg_stages"),
                    "prereg_verified": bool((st.get("prereg") or {}).get("verified")),
                    "stage_a_commit_verified": (None if st.get("stage") != "B"
                                                else bool((st.get("stage_a_commit") or {}).get("verified"))),
                    "census_source": cen.get("source"),
                    "collection_build_sha256": (None if cen.get("collection_build") is None else
                                                asc.sha_text(json.dumps(cen["collection_build"], sort_keys=True,
                                                                        separators=(",", ":")))),
                    "field_coverage": st.get("field_coverage"),
                    "placebo_dropped_own_entries": ((st.get("placebo_glossary") or {}).get("counts") or {}).get(
                        "placebo_dropped_own_entries")}
            have = status.get(leg) != "missing" and rp.is_file()
            self.rows[leg] = [x for x in f10.read_jsonl_repair_tail(rp) if x["condition"] == cond] if have else []
        jdir = Path(cfg["judge_dir"])
        self.final: dict[tuple[str, str], dict[str, Any]] = {}
        for x in asc.read_jsonl(jdir / "final.jsonl") if (jdir / "final.jsonl").is_file() else []:
            if str(x.get("k", "")).startswith("pair|"):
                self.final[(x["record_id"], x["chunk_id"])] = x
        self.pair_paper: dict[tuple[str, str], str] = {}
        self.pair_arm: dict[tuple[str, str], str] = {}
        if (jdir / "pairs.jsonl").is_file():
            for p in asc.read_jsonl(jdir / "pairs.jsonl"):
                self.pair_paper[(p["record_id"], p["chunk_id"])] = p["c_paper"]
                if p.get("arm"):
                    self.pair_arm[(p["record_id"], p["chunk_id"])] = p["arm"]
        self.judge_summary = json.loads((jdir / "judge-summary.json").read_text()) \
            if (jdir / "judge-summary.json").is_file() else {}
        sa = jdir / "stage-a"
        self.stage_a_judge_summary = json.loads((sa / "judge-summary.json").read_text()) \
            if (sa / "judge-summary.json").is_file() else {}

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


def _identical_to_norerank(x: Any) -> bool:
    """found10's ``conditions.base.identical_to_norerank`` (``{records, identical}``, or a bare
    bool): the default top 10 equals norerank's on every record (§9.1)."""
    if isinstance(x, Mapping):
        return int(x.get("records") or 0) > 0 and int(x.get("identical") or 0) == int(x["records"])
    return x is True


def coverage_scope(stage: str) -> tuple[list[str], tuple[str, ...]]:
    """The legs and needs whose demanded pairs must all carry a final label (§6.2): after stage
    A, the found@10 pairs of the judging-stage-A legs (the bm25 legs and every RR@10 pair are
    judged in stage B); after stage B, found@10 and RR@10 in all 29 legs."""
    import alias_study_run as ar

    if stage == "A":
        return [L(*lg) for lg in ar.judging_legs("A")], ("found10",)
    return [L(*lg) for lg in asc.all_legs()], ("found10", "rr10")


def label_coverage(rows: Mapping[str, Sequence[Mapping[str, Any]]], by_id: Mapping[str, Mapping[str, Any]],
                   final: Mapping[tuple[str, str], Mapping[str, Any]], legs: Sequence[str] | None = None,
                   needs: Sequence[str] = ("found10", "rr10")) -> dict[str, Any]:
    """Every pair a leg row **demands** (:func:`alias_study_judge.demanded`: ``found10`` the top
    10 when the gold is outside it; ``rr10`` the chunks ranked above the gold) must have a final
    judge row. A chunk ranked below the gold is never judged and never asked for. A missing pair
    would be scored *not answering* silently, unlike an *unjudged* pair, which the judge counts
    and flags (§6.3). Returns the counts."""
    import alias_study_judge as aj

    missing: set[tuple[str, str]] = set()
    per_leg: Counter[str] = Counter()
    demanded = 0
    for leg in (legs if legs is not None else list(rows)):
        for row in rows.get(leg) or []:
            rec = by_id.get(row["record_id"])
            if rec is None:
                continue
            for need in needs:
                for c in aj.demanded(rec, row, need):
                    demanded += 1
                    if (row["record_id"], c) not in final:
                        missing.add((row["record_id"], c))
                        per_leg[leg] += 1
    return {"pairs_without_final_label": len(missing), "instances_per_leg": dict(per_leg),
            "demanded_instances": demanded, "legs": len(legs if legs is not None else rows), "needs": list(needs)}


def check_frozen_stage_a(judge_dir: Path, runs_dir: Path, outcomes: Mapping[str, Mapping[str, Any]],
                         by_id: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Layer 1 against the frozen stage-A files (§5.7): every row of ``stage-a/hits.jsonl`` (hit
    and gold-only hit per record and judging-stage-A leg, as the judge computed them at the
    commit) must equal the outcome computed here, and every retrieval-stage-A leg summary read
    here must be byte-identical to its frozen copy in ``stage-a/summaries/``. Raises SystemExit."""
    import alias_study_run as ar

    sa = Path(judge_dir) / "stage-a"
    hp = sa / "hits.jsonl"
    if not hp.is_file():
        raise SystemExit("stage-a/hits.jsonl is missing: Layer 1 is cross-checked against the frozen stage-A hits")
    hits = asc.read_jsonl(hp)
    bad = 0
    legs: set[str] = set()
    for h in hits:
        if h["record_id"] not in by_id:
            continue
        legs.add(h["leg"])
        o = (outcomes.get(h["leg"]) or {}).get(h["record_id"])
        if h.get("failed"):
            ok = o is None
        else:
            ok = o is not None and int(o["hit"]) == int(h["hit"]) and int(o["hit_gold"]) == int(h["hit_gold"])
        bad += int(not ok)
    want = {L(*lg) for lg in ar.judging_legs("A")}
    if legs != want:
        raise SystemExit(f"stage-a/hits.jsonl covers {len(legs)} leg(s), not the {len(want)} judging-stage-A legs")
    if bad:
        raise SystemExit(f"{bad} stage-A hit(s) computed here differ from the frozen stage-a/hits.jsonl: the rows or "
                         "the labels changed after the commit")
    diff = []
    seen: set[Path] = set()
    for c, g, r, rep in ar.stage_legs("A"):
        _, sp = ar.leg_files(Path(runs_dir), c, g, r, rep)
        if sp in seen:
            continue
        seen.add(sp)
        cp = sa / "summaries" / c / sp.name
        if not cp.is_file() or not sp.is_file() or asc.sha256_file(cp) != asc.sha256_file(sp):
            diff.append(f"{c}/{sp.name}")
    if diff:
        raise SystemExit(f"stage-A leg summaries differ from their frozen copies: {diff[:6]}")
    return {"hits_rows": len(hits), "hits_sha256": asc.sha256_file(hp), "legs": len(legs),
            "summaries_checked": len(seen), "agree": True}


def cell_gates(spec: Spec, gates: Mapping[str, str | None]) -> list[str]:
    return [f"{leg}: {gates[leg]}" for _, leg in spec if gates.get(leg)]


# ------------------------------------------------------------------ Layer 1

def layer1(bal: Sequence[Mapping[str, Any]], outcomes: Mapping[str, Mapping[str, Any]],
           gates: Mapping[str, str | None], reach: Mapping[str, Any] | None, an: Mapping[str, Any],
           missing: str = "exclude", h2_spec: Spec = H2_SPEC, power_artifact: str | Path | None = None
           ) -> dict[str, Any]:
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
    clb, vb, supb, missb = h1b_label_supports(bal, outcomes, "hit", missing)
    h1b = {**member_randomization(clb, vb, supb, "greater", "paper", an), "missing_or_dropped": missb}
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
    proc = {**family_g(members["H1"], members["H1b"]), **family_n(members["H2"])}
    res: dict[str, Any] = {}
    for h in ("H1", "H2", "H1b"):
        m = members[h]
        res[h] = {**{k: val for k, val in m.items() if k != "gates"}, **proc[h]}
        disc = int(m.get("gained", 0)) + int(m.get("lost", 0))
        res[h]["mde_split"] = _mde_split(disc, ALPHA)
        n = int(m.get("n") or 0)
        if h == "H1b":
            res[h]["power_floor"] = h1b_power(Path(power_artifact) if power_artifact else None)
        else:
            res[h]["power_floor_normal"] = {f"q{q:g}_p{p:g}": round(apw.normal_power(n, q, p, ALPHA), 4)
                                            for q, p in ((0.05, 0.85), (0.10, 0.70))} if n else None
        res[h]["realised_discordance"] = round(disc / n, 4) if n else None
        if h != "H1b":
            res[h]["equivalence"] = equivalence(n, disc, m.get("ci90_exact"))
    if not res["H1b"]["tested"]:
        # §3.1: an untested H1b carries its estimate, CI and power only, never a p
        for k in ("p", "p_exact", "p_perm", "p_mid", "p_method", "statistic"):
            res["H1b"].pop(k, None)
    res["H1"]["c_gate"] = cg
    res["H1"]["reach"] = {"ok": reach_ok, "note": reach_note}
    res["H2"]["gold_gate_pass"] = gold_pass
    res["H1b"]["baseline_c_found10"] = cg["baseline_c_found10"]
    res["H1b"]["headroom_points"] = cg["headroom_points"]
    res["families"] = {"G": {"members": ["H1", "H1b"], "alpha": ALPHA, "rule": "H1, then H1b in fixed sequence"},
                       "N": {"members": ["H2"], "alpha": ALPHA, "rule": "H2 with the gold-only direction gate"},
                       "note": "no level passes between the families (R1)"}
    return res


#: §8: the committed power artifact; H1b's power is read from its label-randomization simulation
POWER_ARTIFACT = _HERE.parents[2] / "docs" / "plans" / "results" / "alias-study-2026-11" / "artifacts" / \
    "power-prefreeze.json"
#: §8's simulation table, H1b by label randomization, C with no effect, at alpha 0.05 (printed
#: when the power artifact carries no H1b simulation)
H1B_POWER_SECTION8 = {"q0.05_p0.85": 0.67, "q0.1_p0.7": 0.54}


def h1b_power(path: Path | None = None) -> dict[str, Any]:
    """H1b's power at 0.05 at the two planning scenarios, from the label-randomization
    simulation (§8), never H1's normal formula: the power artifact's ``simulation.H1b`` when it
    has one (its sha256 recorded), else *see §8 simulation* with §8's values."""
    p = Path(path) if path is not None else POWER_ARTIFACT
    sim = None
    if p.is_file():
        sim = (json.loads(p.read_text()).get("simulation") or {}).get("H1b")
    if sim:
        return {"q0.05_p0.85": sim["p0.85_q0.05"]["0.05"], "q0.1_p0.7": sim["p0.7_q0.1"]["0.05"],
                "source": "power artifact, simulation.H1b (label randomization)", "sha256": asc.sha256_file(p)}
    return {**H1B_POWER_SECTION8,
            "source": "see §8 simulation (H1b label randomization, C with no effect, alpha 0.05); "
                      "the power artifact carries no H1b simulation"}


def _mde_split(discordant: int, alpha: float) -> list[int] | None:
    for g in range(discordant + 1):
        if cs.mcnemar_exact(g, discordant - g, "greater") <= alpha:
            return [g, discordant - g]
    return None


# ------------------------------------------------------------------ §3.4: the judge false-yes bound on H2

def false_yes_counts(final: Mapping[Any, Mapping[str, Any]], stage: str | None = None) -> dict[str, dict[str, Any]]:
    """§3.4: per scope (each arm and ``pooled``), among the drift-sampled pairs that the final
    label calls *answers* and Opus answers, the share Opus calls *not*, with its Wilson 95 %
    interval. A pair with no Opus reference (``opus_status`` ``no_reference``, filtered) does not
    count. ``stage``: only the final rows of that judging stage (a row without one is stage A)."""
    cnt: dict[str, Counter[str]] = {s: Counter() for s in (*asc.ARM_GROUPS, "pooled")}
    for x in final.values():
        if stage is not None and x.get("stage", "A") != stage:
            continue
        if x.get("opus_status") == "no_reference":
            continue
        if not x.get("drift_sampled") or x.get("opus") is None or x.get("label") is None:
            continue
        if int(x["label"]) != 1:
            continue
        no = int(not bool(x["opus"]))
        for s in (x.get("arm"), "pooled"):
            if s in cnt:
                cnt[s]["n"] += 1
                cnt[s]["k"] += no
    return {s: {"k": int(c["k"]), "n": int(c["n"]), "share": c["k"] / c["n"] if c["n"] else None,
                "wilson95": _wilson(int(c["k"]), int(c["n"]))} for s, c in cnt.items()}


def false_yes_from_summary(js: Mapping[str, Any]) -> dict[str, dict[str, Any]] | None:
    """The judge summary's per-scope ``false_yes_among_answers`` {k, n} (R13's U_r input),
    ``overall`` read as ``pooled``; the Wilson interval recomputed here, rounded outward. None
    when the summary carries none."""
    scopes = ((js or {}).get("drift") or {}).get("scopes") or {}
    if not any("false_yes_among_answers" in (v or {}) for v in scopes.values()):
        return None
    out = {}
    for s, src in (*((a, a) for a in asc.ARM_GROUPS), ("pooled", "overall")):
        f = (scopes.get(src) or {}).get("false_yes_among_answers") or {}
        k, n = int(f.get("k") or 0), int(f.get("n") or 0)
        out[s] = {"k": k, "n": n, "share": k / n if n else None, "wilson95": _wilson(k, n)}
    return out


def false_yes_inputs(stage_a_summary: Mapping[str, Any], final: Mapping[Any, Mapping[str, Any]],
                     strict: bool = True) -> tuple[dict[str, dict[str, Any]], str, bool | None]:
    """§3.4 (R13): U's counts from the **frozen stage-A judge summary** (H2's judged pairs are all
    stage A), cross-checked against the stage-A final rows. Returns (counts, source, cross-check
    equal). With ``strict`` (every mode but the dry run) U is **always** the summary's: a summary
    without ``false_yes_among_answers`` is refused, and so is a mismatch with the rows. Only a dry
    run (``strict`` False) falls back to the rows when the summary carries no counts."""
    rows_cnt = false_yes_counts(final, stage="A")
    sum_cnt = false_yes_from_summary(stage_a_summary)
    if sum_cnt is None:
        if strict:
            raise SystemExit("the frozen stage-A judge summary carries no false_yes_among_answers (§3.4, R13): U "
                             "is always read from it outside a dry run; nothing analysed")
        return rows_cnt, "final.jsonl (stage A rows; dry run only)", None
    same = all((sum_cnt[s]["k"], sum_cnt[s]["n"]) == (rows_cnt[s]["k"], rows_cnt[s]["n"]) for s in FALSE_YES_SCOPES)
    if strict and not same:
        raise SystemExit("the frozen stage-A judge summary's false_yes_among_answers differs from the stage-A final "
                         "rows (§3.4, R13): the labels or the summary changed after the commit; nothing analysed")
    return sum_cnt, "stage-a/judge-summary.json false_yes_among_answers", same


def false_yes_bound(h2_net: float | None, counts: Mapping[str, Mapping[str, Any]], j_r: float | None,
                    n_records: int) -> dict[str, Any]:
    """B = 100 U J_r / N (§3.4) with **U = max(U_random, U_pooled), always** (owner
    2026-11-03): the upper Wilson limits of the random arm's and the three arms' pooled shares.
    A scope with no pair contributes nothing; with neither, B is absent. *Within the judge
    false-yes bound* when |H2's net| <= B. B never changes H2's label."""
    us = {s: (counts.get(s) or {}).get("wilson95") for s in FALSE_YES_SCOPES}
    have = {s: float(w[1]) for s, w in us.items() if w}
    out: dict[str, Any] = {"U_random": have.get("random"), "U_pooled": have.get("pooled"),
                           "rule": "U = max(U_random, U_pooled)", "J_r": j_r, "N": n_records,
                           "counts": {s: dict(counts.get(s) or {}) for s in FALSE_YES_SCOPES}}
    if not have or j_r is None or not n_records:
        out.update(U=None, B_points=None, within_bound=False, note="absent (no drift pair or no J_r)")
        return out
    u = max(have.values())
    b = 100.0 * u * j_r / n_records
    out.update(U=u, U_from=max(have, key=lambda s: have[s]), B_points=round(b, 4),
               within_bound=bool(h2_net is not None and abs(h2_net) <= b))
    return out


def judged_only_hits(recs: Sequence[Mapping[str, Any]], outcomes: Mapping[str, Mapping[str, Any]]
                     ) -> float | None:
    """J_r (§3.4): the records whose hit in a random-draw cell (default x none) rests on judged
    pairs only (gold outside the top 10), averaged over the two draws."""
    js = []
    for draw in ("random1", "random2"):
        o = outcomes.get(L(draw, "none", "default")) or {}
        if not o:
            return None
        js.append(sum(1 for r in recs if (x := o.get(r["record_id"])) is not None and x["hit"] and not x["hit_gold"]))
    return sum(js) / len(js)


# ------------------------------------------------------------------ Layer 2

def _point_ci(f: Mapping[str, Any]) -> tuple[Any, Any]:
    """A figure's unrounded point and 95 % CI when it carries them, else the printed ones."""
    v = f.get("net_exact", f.get("net_points"))
    ci = f.get("ci95_exact", f.get("ci95"))
    return v, ci


def attach_rule(g_lift: Mapping[str, Any], c_change: Mapping[str, Any]) -> dict[str, Any]:
    """§3.3, the glossary decision, applied in this order:

    1. **harm:** ``L_hi < 0`` -> *harmful, do not attach*;
    2. **C cost (owner 2026-11-03):** ``dC < -1`` point **or** ``C_lo < -3.6`` points -> *do not
       attach (C cost)*; both inequalities are strict, so ``dC = -1`` exactly and ``C_lo = -3.6``
       exactly pass the cost rule;
    3. ``L >= 2`` **and** ``L_lo > 0`` (and ``dC >= -1`` and ``C_lo >= -3.6``) -> *attach*;
    4. otherwise *not resolved*.

    dC is merged - none on the C records (the C cost is -dC); all figures in points. The rule
    compares the **unrounded** figures (``net_exact`` / ``ci95_exact`` of :func:`figure`; the
    printed values when a figure carries no exact ones)."""
    lv, lci = _point_ci(g_lift)
    dc, cci = _point_ci(c_change)
    out: dict[str, Any] = {"g_lift": g_lift.get("net_points"), "g_lift_ci95": g_lift.get("ci95"),
                           "delta_c": c_change.get("net_points"), "delta_c_ci95": c_change.get("ci95"),
                           "n_c": c_change.get("n"),
                           "thresholds": {"lift_points": ATTACH_LIFT_POINTS, "c_point": C_COST_POINT,
                                          "c_lower": C_COST_LOWER},
                           "label": "Layer 2, descriptive; thresholds judgement-based"}
    if lv is None or lci is None or dc is None or cci is None:
        out.update(reading="not resolved", note="a figure is missing")
        return out
    harmful = lci[1] < 0
    c_cost = dc < C_COST_POINT or cci[0] < C_COST_LOWER
    attach = lv >= ATTACH_LIFT_POINTS and lci[0] > 0 and dc >= C_COST_POINT and cci[0] >= C_COST_LOWER
    out["conditions"] = {"harm_L_hi_lt_0": harmful, "c_point_lt_-1": dc < C_COST_POINT,
                         "c_lower_lt_-3.6": cci[0] < C_COST_LOWER, "lift_ge_2": lv >= ATTACH_LIFT_POINTS,
                         "lift_lower_gt_0": lci[0] > 0}
    if harmful:
        out["reading"] = "harmful, do not attach"
    elif c_cost:
        out["reading"] = "do not attach (C cost)"
    elif attach:
        out["reading"] = "attach"
    else:
        out["reading"] = "not resolved"
    return out


def reranker_rule(d: Mapping[str, Any]) -> dict[str, Any]:
    """§3.3: non-inferiority of default against BM25 at -3 points (neighbour arm, none)."""
    v, ci = _point_ci(d)
    out: dict[str, Any] = {"default_minus_bm25": d.get("net_points"), "ci95": d.get("ci95"), "margin": RERANK_MARGIN,
                           "label": "Layer 2, descriptive"}
    if v is None or ci is None:
        out["reading"] = "not resolved"
    elif ci[0] > RERANK_MARGIN:
        out["reading"] = "keep the default (non-inferior to BM25)"
    elif ci[1] < RERANK_MARGIN:
        out["reading"] = "default inferior to BM25 by more than the margin: reconsider"
    else:
        out["reading"] = "not resolved"
    return out


def growth_rule(f: Mapping[str, Any]) -> dict[str, Any]:
    """§3.3: *a concern* if neighbours cost >= 3 points more than random with the CI excluding 0.
    *Not a concern* only through the equivalence bound (the figure's 90 % CI within +-delta:
    *not a concern: no effect larger than delta points*); anything else, a wide CI included,
    reads *not resolved*."""
    v, ci = _point_ci(f)
    point = v is not None and v <= GROWTH_MARGIN
    excl = ci is not None and ci[1] < 0
    eq = f.get("equivalence") or {}
    if point and excl:
        reading = "a concern"
    elif eq.get("within"):
        reading = f"not a concern: {eq['reading']}"
    else:
        reading = "not resolved"
    return {"neighbour_minus_random": f.get("net_points"), "ci95": f.get("ci95"), "margin": GROWTH_MARGIN,
            "concern": bool(point and excl), "equivalence": eq or None, "reading": reading,
            "label": "Layer 2, descriptive"}


def decision_rules(g_lift: Mapping[str, Any], c_change: Mapping[str, Any], rr_gap: Mapping[str, Any],
                   growth: Mapping[str, Any]) -> dict[str, Any]:
    """§3.3, the three serving rules, thresholds fixed."""
    return {"attach_merged_glossary": attach_rule(g_lift, c_change),
            "keep_reranker_default": reranker_rule(rr_gap),
            "related_paper_growth_concern": growth_rule(growth)}


def serving_statements(decisions: Mapping[str, Any], l1: Mapping[str, Any]) -> dict[str, Any]:
    """§3.3 *Layer 1 governs the wording*: each serving statement in the words fixed there,
    with the Layer-1 verdict beside it. A combination the table does not list reads the
    Layer-2 phrase with the verdict beside it."""
    h1, h1b, h2 = (l1[h]["status"] for h in ("H1", "H1b", "H2"))
    h1_gates = "; ".join(l1["H1"].get("gate_reasons") or [])
    ad = decisions["attach_merged_glossary"]
    att = ad["reading"]
    not_eval = h1 in (GATE, "absent")
    if att == "attach" and h1 == CONFIRMED:
        s = "attach, for questions using the glossary's primary term"
        if h1b != CONFIRMED:
            s += " (alias-specificity unresolved)"
    elif att == "attach" and not_eval:
        s = "attach provisionally, claim not evaluable" + (f" ({h1_gates})" if h1_gates else "")
    elif att == "attach":
        s = "attach provisionally; the confirmatory claim was not confirmed"
    elif att == "do not attach (C cost)" and h1 == CONFIRMED:
        cond = ad.get("conditions") or {}
        parts = []
        if cond.get("c_point_lt_-1"):
            parts.append("term-bearing passages lost more than 1 point")
        if cond.get("c_lower_lt_-3.6"):
            parts.append("a loss on term-bearing passages larger than 3.6 points is not ruled out")
        s = "do not attach: the gain on alias questions is confirmed, but " + " and ".join(parts)
    else:
        s = att
    if att == "attach":
        # §3.3 (R12): every attach statement says what 300 C records cannot rule out
        s += f"; a cost on term-bearing passages smaller than about 3.6 points is not ruled out ({ad.get('n_c')} C records)"
    gro = decisions["related_paper_growth_concern"]["reading"]
    if gro == "a concern" and h2 == CONFIRMED:
        g = "related-paper growth is a concern"
    elif gro == "a concern" and h2 in (GATE, "absent"):
        g = "a concern provisionally; not evaluable"
    elif gro == "a concern":
        g = "a concern provisionally; the confirmatory claim was not confirmed"
    else:
        g = gro
    return {"glossary": {"statement": s, "scope": "for questions using the glossary's primary term",
                         "layer2_reading": att, "beside": {"H1": h1, "H1b": h1b}},
            "growth": {"statement": g, "layer2_reading": gro, "beside": {"H2": h2}},
            "reranker": {"statement": decisions["keep_reranker_default"]["reading"],
                         "label": "Layer 2, descriptive (no Layer-1 member)"}}


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

    rr_spec: Spec = [(1.0, L("neighbour", "none", "default")), (-1.0, L("neighbour", "none", "bm25"))]
    out: dict[str, Any] = {"label": "Layer 2, descriptive"}
    out["g_lift"] = fig(g, H1_SPEC)
    out["c_change"] = fig(c, H1_SPEC)
    out["c_change"]["note"] = "ΔC = merged - none on the C records; the C cost is -ΔC"
    out["default_minus_bm25_neighbour"] = fig(bal, rr_spec)
    out["neighbour_minus_random"] = fig(bal, H2_SPEC)
    out["H3"] = fig(bal, H3_SPEC)
    out["decisions"] = decision_rules(out["g_lift"], out["c_change"], out["default_minus_bm25_neighbour"],
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
    legs = [L(*x) for x in asc.all_legs()]                  # the 29 legs, the placebo leg among them
    for leg in legs:
        o = outcomes.get(leg) or {}
        kinds = Counter((o.get(r["record_id"]) or {}).get("kind") for r in bal if o.get(r["record_id"]) is not None)
        cells[leg] = {"found10": found_rate(bal, outcomes, leg), "found10_gold": found_rate(bal, outcomes, leg, "hit_gold"),
                      "mean_rr": found_rate(bal, outcomes, leg, "rr"), "mean_rr_gold": found_rate(bal, outcomes, leg, "rr_gold"),
                      "hits_by_kind": {k: int(kinds.get(k, 0)) for k in ("gold", "same", "other")},
                      "descriptive_leg": {"found10": found_rate(d.desc, outcomes, leg),
                                          "found10_gold": found_rate(d.desc, outcomes, leg, "hit_gold")},
                      "gate": gates.get(leg)}
    out["cells"] = cells
    out["placebo"] = placebo_leg(bal, outcomes, gates, an, d.runs)
    return out


def placebo_leg(bal: Sequence[Mapping[str, Any]], outcomes: Mapping[str, Mapping[str, Any]],
                gates: Mapping[str, str | None], an: Mapping[str, Any], runs: Mapping[str, Any]) -> Any:
    """§3.3 / §5.3 (R7), descriptive: base x default x placebo against none and against merged,
    on G and on C. Beside it, from the leg summaries: ``study.field_coverage`` of base x merged
    and of base x placebo, and the placebo's ``placebo_dropped_own_entries`` (the entries equal
    to the receiving paper's own, dropped; owner 2026-11-03)."""
    if not (outcomes.get(PLACEBO_LEG) or {}):
        return "absent (the placebo leg was not run)"
    out: dict[str, Any] = {"label": "Layer 2, descriptive (placebo glossary leg; enters no test and no decision)"}
    for kind in ("G", "C"):
        recs = [r for r in bal if r["kind"] == kind]
        out[kind] = {
            "placebo_minus_none": figure(recs, outcomes, [(1.0, PLACEBO_LEG), (-1.0, L("base", "none", "default"))],
                                         an, "hit", gates),
            "placebo_minus_merged": figure(recs, outcomes, [(1.0, PLACEBO_LEG), (-1.0, L("base", "merged", "default"))],
                                           an, "hit", gates),
            "found10": {leg: found_rate(recs, outcomes, leg)
                        for leg in (L("base", "none", "default"), L("base", "merged", "default"), PLACEBO_LEG)}}
    merged, placebo = runs.get("base/merged") or {}, runs.get(f"base/{asc.PLACEBO}") or {}
    out["field_coverage"] = {"merged": merged.get("field_coverage") or "absent (no study.field_coverage)",
                             "placebo": placebo.get("field_coverage") or "absent (no study.field_coverage)"}
    dropped = placebo.get("placebo_dropped_own_entries")
    out["placebo_dropped_own_entries"] = dropped if dropped is not None else \
        "absent (no study.placebo_glossary.counts in the placebo leg's summary)"
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


#: §13.4: the record spot check is exactly 40 records, committed as counts with the sheet's sha256
SPOT_RECORDS_N = 40
SPOT_JUDGE_N = 100
SPOT_RECORDS_RULE = 0.85
SPOT_JUDGE_RULE = 0.90
SPOT_STRATA = ("answers", "not", "tiebroken")


def agreement_metrics(tp: int, fp: int, fn: int, tn: int) -> dict[str, Any]:
    """§6.6: the 2 x 2 table (final label x reference, *answers* / *not*) and from it precision,
    recall and F1 for *answers*, specificity, Cohen's kappa and Gwet's AC1, with the counts."""
    n = tp + fp + fn + tn

    def div(a: float, b: float) -> float | None:
        return a / b if b else None

    prec, rec = div(tp, tp + fp), div(tp, tp + fn)
    f1 = div(2 * prec * rec, prec + rec) if prec is not None and rec is not None else None
    po = div(tp + tn, n)
    kappa = ac1 = None
    if n:
        pe = ((tp + fp) * (tp + fn) + (fn + tn) * (fp + tn)) / (n * n)
        kappa = div(po - pe, 1 - pe) if po is not None else None
        pi = ((tp + fp) / n + (tp + fn) / n) / 2
        pe1 = 2 * pi * (1 - pi)
        ac1 = div(po - pe1, 1 - pe1) if po is not None else None
    return {"table": {"tp": tp, "fp": fp, "fn": fn, "tn": tn}, "n": n, "agreement": po, "precision": prec,
            "recall": rec, "f1": f1, "specificity": div(tn, tn + fp), "kappa": kappa, "gwet_ac1": ac1}


def _metrics_of(pairs: Sequence[tuple[int, int]]) -> dict[str, Any]:
    """``(final, reference)`` pairs -> :func:`agreement_metrics`."""
    c = Counter(pairs)
    return agreement_metrics(c[(1, 1)], c[(1, 0)], c[(0, 1)], c[(0, 0)])


def spot_stratum(f: Mapping[str, Any]) -> str | None:
    """§13.5: *answers* or *not* where C and E agreed, *tiebroken* where the Argon label is final
    (either verdict); an unjudged pair is in no stratum."""
    if f.get("source") == "agree":
        return "answers" if int(f.get("label") or 0) == 1 else "not"
    if f.get("source") in ("tiebreak", "tiebreak_r2"):
        return "tiebroken"
    return None


def spot_flags(cfg: Mapping[str, Any], d: Data) -> dict[str, Any]:
    """§13.4-13.5 (R15), from ``spot_checks: {records, judge}``. *Records*: a JSON ``{valid, n,
    sheet_sha256}`` (the sheet's counts and sha256; n must be 40, the sha present); validity below
    0.85 flags every result sentence. A judge pair whose final label is not from stage A is refused. *Judge*: one
    row per owner-labelled pair (``pair_id``, ``owner_label``), 100 pairs drawn from stage A's
    labels, stratified by the final label: per-stratum agreement with Wilson intervals, the §6.6
    metrics, per-arm false yes, and the agreement re-weighted to the stage-A label shares (each
    stratum x arm cell weighted by its share of stage-A labels). Flag: re-weighted agreement
    below 0.90 or *answers*-stratum agreement below 0.90."""
    sc = cfg.get("spot_checks") or {}
    out: dict[str, Any] = {}
    rp = sc.get("records")
    if rp and Path(rp).is_file():
        r = json.loads(Path(rp).read_text())
        k, n = int(r["valid"]), int(r["n"])
        sheet = str(r.get("sheet_sha256") or "")
        if n != SPOT_RECORDS_N:
            raise SystemExit(f"the record spot check has n = {n}, not the {SPOT_RECORDS_N} records of §13.4")
        if not re.fullmatch(r"[0-9a-f]{64}", sheet):
            raise SystemExit("the record spot check names no sheet_sha256 (§13.4: committed as counts with the "
                             "sheet's sha)")
        if not 0 <= k <= n:
            raise SystemExit(f"the record spot check has valid = {k} of n = {n}")
        out["record_validity"] = {"valid": k, "n": n, "sheet_sha256": sheet, "rate": k / n,
                                  "wilson95": _wilson(k, n), "below_threshold": k / n < SPOT_RECORDS_RULE}
    else:
        out["record_validity"] = "absent (owner spot check not committed)"
    jp = sc.get("judge")
    if not (jp and Path(jp).is_file()):
        out["judge_validity"] = "absent (owner spot check not committed)"
        return out
    fin_by_pair = {x["pair_id"]: x for x in d.final.values()}
    cells: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
    no_final = no_stratum = 0
    per_arm: dict[str, Counter[str]] = defaultdict(Counter)
    rows = asc.read_jsonl(jp)
    late = [x["pair_id"] for x in rows if (fin_by_pair.get(x["pair_id"]) or {}).get("stage", "A") != "A"]
    if late:
        raise SystemExit(f"{len(late)} judge spot-check pair(s) have a final label from stage B: the 100 pairs are "
                         "drawn from stage A's labels (§13.5)")
    for x in rows:
        f = fin_by_pair.get(x["pair_id"])
        if f is None:
            no_final += 1
            continue
        st = spot_stratum(f)
        if st is None:
            no_stratum += 1
            continue
        fl, ol = int(f["label"] or 0), int(x["owner_label"])
        cells[(st, f["arm"])].append((fl, ol))
        if ol == 0:
            per_arm[f["arm"]]["no"] += 1
            per_arm[f["arm"]]["final_yes"] += int(fl == 1)
    share = Counter((st, f["arm"]) for f in d.final.values()
                    if f.get("stage", "A") == "A" and (st := spot_stratum(f)) is not None)
    tot = sum(share.values())
    strata: dict[str, dict[str, Any]] = {}
    for st in SPOT_STRATA:
        prs = [p for (s_, _), v in cells.items() if s_ == st for p in v]
        a = sum(fl == ol for fl, ol in prs)
        strata[st] = {"agree": a, "n": len(prs), "rate": a / len(prs) if prs else None, "wilson95": _wilson(a, len(prs))}
    num = den = 0.0
    for cell, prs in cells.items():
        w = share.get(cell, 0) / tot if tot else 0.0
        num += w * sum(fl == ol for fl, ol in prs) / len(prs)
        den += w
    rew = num / den if den else None
    allp = [p for v in cells.values() for p in v]
    ans = strata["answers"]["rate"]
    out["judge_validity"] = {
        "n": len(allp), "n_expected": SPOT_JUDGE_N, "without_final_label": no_final, "unjudged": no_stratum,
        "strata": strata, "reweighted_agreement": rew, "reweight_coverage": den,
        "metrics": _metrics_of(allp),
        "below_threshold": bool((rew is not None and rew < SPOT_JUDGE_RULE) or (ans is not None and ans < SPOT_JUDGE_RULE)),
        "false_yes_per_arm": {a: {"k": c["final_yes"], "n": c["no"], "wilson95": _wilson(c["final_yes"], c["no"])}
                              for a, c in sorted(per_arm.items())}}
    return out


def drift_metrics(final: Mapping[Any, Mapping[str, Any]]) -> dict[str, Any]:
    """§6.6 for the drift reference: the final label against Opus on the Opus-answered drift
    pairs, per arm and overall (no-reference pairs filtered)."""
    by: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for x in final.values():
        if not x.get("drift_sampled") or x.get("opus_status") == "no_reference" or x.get("opus") is None:
            continue
        pr = (int(x.get("label") or 0), int(bool(x["opus"])))
        by[x.get("arm", "?")].append(pr)
        by["overall"].append(pr)
    return {k: _metrics_of(v) for k, v in sorted(by.items())}


def _wilson(k: int, n: int, z: float = 1.959964) -> list[float] | None:
    """The Wilson score 95 % interval, rounded **outward** to 4 decimals (the lower limit down,
    the upper limit up), so a bound read from it is never rounded towards the estimate."""
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [max(0.0, math.floor((mid - half) * 1e4) / 1e4), min(1.0, math.ceil((mid + half) * 1e4) / 1e4)]


# ------------------------------------------------------------------ §7: the fixed sensitivities (R6)

def _tstats(signed: np.ndarray, n: int) -> np.ndarray:
    """The studentized statistic, mean paper score over its standard error, of each row of
    ``signed`` (the non-zero paper scores with their signs; the ``n - k`` zero papers count in
    ``n``). A zero standard error gives +-inf (0 for a zero mean)."""
    mean = signed.sum(axis=1) / n
    ss = (signed ** 2).sum(axis=1)
    var = np.maximum(ss - n * mean ** 2, 0.0) / (n - 1)
    se = np.sqrt(var / n)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(se > 1e-12, mean / np.where(se > 1e-12, se, 1.0), np.sign(mean) * np.inf)
    return np.where(np.abs(mean) <= 1e-15, 0.0, t)


def studentized_sign_flip(sums: Sequence[float] | np.ndarray, alternative: str, an: Mapping[str, Any]) -> dict[str, Any]:
    """Printed identity check, deciding nothing (owner 2026-11-03; the weak-null re-test is
    :func:`bootstrap_t`): the paper sign-flip test with the studentized statistic in place of
    the sum. Ties in the tail at a relative tolerance of 1e-9.

    With one score per paper, ``Σ (ε_j S_j)^2`` does not depend on the signs, so the
    studentized statistic is a strictly increasing function of the sum over the whole sign
    group, and its exact p is the sum's. Computed exactly (§7): by enumerating the studentized
    statistic over the ``2^k`` sign vectors when ``k <= chunktrial_stats.EXACT_MAX_PAGES``
    (``enumeration``, which also shows the identity); above that by the sum's lattice law
    (``chunktrial_stats.sign_flip_lattice``, ``lattice (monotone in the sum)``); Monte Carlo
    (``n_perm`` draws, seed ``perm_seed``, ``(b + 1)/(m + 1)``) only where the lattice is
    infeasible. Above ``k = 20`` the Monte-Carlo studentized p is always reported as ``p_perm``,
    a cross-check."""
    s = np.asarray(sums, float)
    n = int(s.size)
    nz = s[np.abs(s) > 1e-12]
    k = int(nz.size)
    if n < 2 or k == 0:
        return {"p": 1.0 if n else None, "p_method": "degenerate", "statistic": 0.0, "n_nonzero": k,
                "min_attainable_p": 1.0}
    t_obs = float(_tstats(nz[None, :], n)[0])
    tol = 1e-9 * (1.0 if not math.isfinite(t_obs) else max(1.0, abs(t_obs)))

    def tail(t: np.ndarray) -> int:
        return int(np.count_nonzero(t >= t_obs - tol if alternative == "greater" else t <= t_obs + tol))

    floor = 2.0 ** -k
    p_perm: float | None = None
    if k <= cs.EXACT_MAX_PAGES:
        b = 0
        total = 1 << k
        bitpos = np.arange(k)
        for start in range(0, total, 1 << 16):
            idx = np.arange(start, min(total, start + (1 << 16)))[:, None]
            signs = 1.0 - 2.0 * ((idx >> bitpos) & 1)
            b += tail(_tstats(signs * nz, n))
        p, method = b / total, "enumeration"
    else:
        rng = np.random.default_rng(int(an["perm_seed"]))
        m = int(an["n_perm"])
        b = 0
        step = max(1, 2_000_000 // k)
        for start in range(0, m, step):
            mm = min(step, m - start)
            signs = rng.integers(0, 2, size=(mm, k)).astype(float) * 2 - 1
            b += tail(_tstats(signs * nz, n))
        p_perm = (b + 1) / (m + 1)
        lat = cs.sign_flip_lattice(nz, alternative)  # type: ignore[arg-type]
        if lat is not None:
            p, method = float(lat["p"]), "lattice (monotone in the sum)"
        else:
            p, method = p_perm, "monte_carlo"
            floor = max(floor, 1.0 / (m + 1))
    return {"p": float(p), "p_method": method, "p_perm": p_perm, "statistic": t_obs, "n_nonzero": k,
            "min_attainable_p": float(floor)}


def topic_sums(names: Sequence[str], sums: np.ndarray, topic_of: Mapping[str, Any]) -> tuple[np.ndarray, int, int]:
    """Paper scores pooled by the study paper's OpenAlex primary topic (one cluster score per
    topic). A paper with no topic stays its own cluster (counted). Returns (topic sums, number
    of clusters, papers without a topic)."""
    by: dict[str, float] = defaultdict(float)
    no_topic = 0
    for name, v in zip(names, sums, strict=True):
        t = topic_of.get(name)
        if t is None:
            no_topic += 1
            t = f"__paper__{len(by)}"
        by[str(t)] += float(v)
    arr = np.array(list(by.values()), float)
    return arr, int(arr.size), no_topic


def paper_scores(recs: Sequence[Mapping[str, Any]], outcomes: Mapping[str, Mapping[str, Any]], spec: Spec,
                 key: str = "hit") -> tuple[list[str], np.ndarray, np.ndarray, int]:
    cl, v, _ = contrast_values(recs, outcomes, spec, key)
    names, sums, cnt = cluster_sums(cl, v)
    return names, sums, cnt, len(v)


def _safe_ratio(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    """``num / den``, with a zero denominator giving +-inf (0 for a zero numerator)."""
    with np.errstate(divide="ignore", invalid="ignore"):
        q = np.where(den > 1e-12, num / np.where(den > 1e-12, den, 1.0), np.sign(num) * np.inf)
    return np.where(np.abs(num) <= 1e-15, 0.0, q)


def _tail(t: np.ndarray, t_obs: float, alternative: str) -> int:
    tol = 1e-9 * (max(1.0, abs(t_obs)) if math.isfinite(t_obs) else 1.0)
    return int(np.count_nonzero(t >= t_obs - tol if alternative == "greater" else t <= t_obs + tol))


def bootstrap_t(sums: Sequence[float] | np.ndarray, alternative: str, n_boot: int, seed: int) -> dict[str, Any]:
    """§7 (owner 2026-11-03), the weak-null re-test of H1 and H2: a one-sided paper-cluster
    bootstrap-t of the mean paper score. ``t = mean / se`` (se = sd / sqrt(P), all P papers,
    zeros included); ``B`` resamples of the papers with replacement (seed ``261101|alias|boot``)
    give ``t*_b = (mean*_b - mean) / se*_b``, whose own skewness the comparison carries (the
    bootstrap-t's skew correction); ``p = (#{t* >= t} + 1) / (B + 1)`` ("greater"; "less"
    mirrors it). It tests the mean claim under the weak null E[S_j] = 0, which the sign-flip
    does not address. Monte Carlo by construction; ``B`` is recorded."""
    s = np.asarray(sums, float)
    n = int(s.size)
    if n < 2:
        return {"p": None, "p_method": "degenerate", "n_boot": int(n_boot), "min_attainable_p": 1.0}
    m = float(s.mean())
    t_obs = float(_safe_ratio(np.array([m]), np.array([float(s.std(ddof=1)) / math.sqrt(n)]))[0])
    rng = np.random.default_rng(int(seed))
    b = 0
    step = max(1, 2_000_000 // n)
    for start in range(0, n_boot, step):
        mm = min(step, n_boot - start)
        x = s[rng.integers(0, n, size=(mm, n))]
        b += _tail(_safe_ratio(x.mean(axis=1) - m, x.std(axis=1, ddof=1) / math.sqrt(n)), t_obs, alternative)
    return {"p": (b + 1) / (n_boot + 1), "p_method": "monte_carlo (paper-cluster bootstrap-t, one-sided)",
            "statistic": t_obs, "mean_paper_score": m, "papers": n, "n_boot": int(n_boot), "seed": int(seed),
            "min_attainable_p": 1.0 / (n_boot + 1)}


def freedman_lane_intercept(s: np.ndarray, x: np.ndarray, alternative: str, n_flips: int, seed: int
                            ) -> dict[str, Any]:
    """§7 (O13, owner 2026-11-03), H2 with the chunk-volume covariate: the effect at equal added
    volume is the intercept of ``S_j = a + g x_j + e_j``. **Freedman–Lane sign-flip with the
    studentized intercept**: fit the restricted model ``S = g x`` (no intercept), flip the signs
    of its residuals, ``S* = g_r x + eps r``, refit the full model and compare the studentized
    intercepts ``t* = a* / se(a*)`` with the observed one. ``n_flips`` flips (seed
    ``261101|alias|perm``), ``p = (b + 1)/(n_flips + 1)``: a Monte-Carlo p."""
    n = int(s.size)
    X = np.column_stack([np.ones(n), x])
    if n < 3 or np.linalg.matrix_rank(X) < 2 or float((x * x).sum()) <= 1e-12:
        return {"p": None, "p_method": "degenerate (fewer than 3 papers or a constant covariate)",
                "min_attainable_p": 1.0}
    xtx_inv = np.linalg.inv(X.T @ X)
    hat = xtx_inv @ X.T

    def tstat(y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        coef = y @ hat.T
        rss = ((y - coef @ X.T) ** 2).sum(axis=1)
        se = np.sqrt(np.maximum(rss, 0.0) / (n - 2) * xtx_inv[0, 0])
        return _safe_ratio(coef[:, 0], se), coef

    t_obs_a, coef_obs = tstat(s[None, :])
    t_obs = float(t_obs_a[0])
    g_r = float((x * s).sum() / (x * x).sum())
    resid = s - g_r * x
    rng = np.random.default_rng(int(seed))
    b = 0
    step = max(1, 2_000_000 // n)
    for start in range(0, n_flips, step):
        mm = min(step, n_flips - start)
        eps = rng.integers(0, 2, size=(mm, n)).astype(float) * 2 - 1
        b += _tail(tstat(g_r * x + eps * resid)[0], t_obs, alternative)
    return {"p": (b + 1) / (n_flips + 1),
            "p_method": f"monte_carlo (Freedman–Lane sign-flip of the restricted residuals, studentized intercept, "
                        f"{n_flips} flips)",
            "statistic": t_obs, "intercept_per_paper": float(coef_obs[0, 0]), "slope_per_chunk": float(coef_obs[0, 1]),
            "restricted_slope": g_r, "papers": n, "n_flips": int(n_flips), "seed": int(seed),
            "min_attainable_p": 1.0 / (n_flips + 1)}


def tercile_of(v: float, cuts: Sequence[float]) -> str:
    """:data:`TERCILE_RULE`."""
    c1, c2 = float(cuts[0]), float(cuts[1])
    return "T1" if v <= c1 else ("T2" if v <= c2 else "T3")


def _subset_figure(recs: Sequence[Mapping[str, Any]], papers: set[str], outcomes: Mapping[str, Mapping[str, Any]],
                   spec: Spec, an: Mapping[str, Any]) -> dict[str, Any]:
    sub = [r for r in recs if str(r["paper"]) in papers]
    f = figure(sub, outcomes, spec, an)
    f.pop("equivalence", None)
    return {**f, "label": "subgroup display (no test at the member's level)"}


def origin_counts(bal: Sequence[Mapping[str, Any]], outcomes: Mapping[str, Mapping[str, Any]],
                  rows: Mapping[str, Sequence[Mapping[str, Any]]], pair_paper: Mapping[tuple[str, str], str],
                  pair_arm: Mapping[tuple[str, str], str], attribution: Mapping[str, str] | None) -> dict[str, Any]:
    """§7 / §3.3, descriptive. (a) Hits of each Layer-1 cell by origin: gold, same-paper judged,
    other-paper judged. (b) For each record that is a hit in a random draw but not in the
    neighbour arm (H2 contrast < 0), the top-10 chunks of the neighbour cell that outrank its
    gold, counted as its **own paper's neighbour**, **another study paper's neighbour** or
    **base** (``attribution``: added paper -> the study paper it was attributed to)."""
    g = [r for r in bal if r["kind"] == "G"]
    hits: dict[str, Any] = {}
    for leg, recs in ((L("base", "none", "default"), g), (L("base", "merged", "default"), g),
                      (L("neighbour", "none", "default"), bal), (L("random1", "none", "default"), bal),
                      (L("random2", "none", "default"), bal)):
        o = outcomes.get(leg) or {}
        k = Counter((o.get(r["record_id"]) or {}).get("kind") for r in recs if o.get(r["record_id"]) is not None)
        hits[leg] = {"records": "G" if recs is g else "all", **{x: int(k.get(x, 0)) for x in ("gold", "same", "other")}}
    out: dict[str, Any] = {"label": "descriptive; cannot flip anything", "hits_by_origin": hits}
    nb_leg = L("neighbour", "none", "default")
    nb = outcomes.get(nb_leg) or {}
    r1, r2 = (outcomes.get(L(x, "none", "default")) or {} for x in ("random1", "random2"))
    row_of = {row["record_id"]: row for row in rows.get(nb_leg) or [] if not row.get("failed")}
    disp: Counter[str] = Counter()
    n_rec = 0
    for r in bal:
        rid = r["record_id"]
        a, b1, b2 = nb.get(rid), r1.get(rid), r2.get(rid)
        if a is None or b1 is None or b2 is None or a["hit"] or not (b1["hit"] or b2["hit"]):
            continue
        n_rec += 1
        top = (row_of.get(rid) or {}).get("ranked_chunk_ids") or []
        gold = r["gold_chunks"][0]
        for cid in top[:asc.FOUND_K]:
            if cid == gold:
                break
            arm = pair_arm.get((rid, cid))
            if arm == "base":
                disp["base"] += 1
            elif arm == "neighbour":
                if attribution is None:
                    disp["neighbour (attribution not supplied)"] += 1
                elif attribution.get(str(pair_paper.get((rid, cid)))) == str(r["paper"]):
                    disp["own_paper_neighbour"] += 1
                else:
                    disp["other_study_paper_neighbour"] += 1
            else:
                disp["unknown"] += 1
    out["displacing_chunks"] = {"records": n_rec, **{k: int(v) for k, v in sorted(disp.items())}}
    return out


#: config keys naming one optional input file each (hashed into ``inputs.files``)
INPUT_FILE_KEYS = ("collision", "paper_covariates", "tercile_cuts", "neighbour_attribution", "power_artifact")
JUDGE_FILES = ("final.jsonl", "pairs.jsonl", "judge-summary.json")


def _sha_or_none(p: str | Path | None) -> str | None:
    return asc.sha256_file(p) if p and Path(p).is_file() else None


def input_hashes(cfg: Mapping[str, Any], d: Data) -> dict[str, Any]:
    """The sha256 of every input the analysis reads (None when a named file is absent): the
    record file, the judge's three files and the frozen stage-A files, the stage-A commit (the
    gate's ``stage_a_commit_sha256``), every run summary and row file with its collection build
    (``census.collection_build``, canonical JSON) and census source, both spot checks, each
    optional file the config names, each sensitivity-set list, the power artifact H1b's power
    is read from, and the config itself (canonical JSON)."""
    jdir = Path(cfg["judge_dir"])
    files = {k: _sha_or_none(cfg[k]) for k in INPUT_FILE_KEYS if cfg.get(k)}
    files.setdefault("power_artifact", _sha_or_none(POWER_ARTIFACT))
    pre = getattr(d, "preflight", None) or {}
    return {"config_canonical_sha256": asc.sha_text(json.dumps(cfg, sort_keys=True, separators=(",", ":"))),
            "records": _sha_or_none(cfg["records"]),
            "judge": {name: _sha_or_none(jdir / name) for name in JUDGE_FILES},
            "stage_a": {"stage_a_commit_sha256": (pre.get("gate") or {}).get("stage_a_commit_sha256"),
                        "hits.jsonl": _sha_or_none(jdir / "stage-a" / "hits.jsonl"),
                        "judge-summary.json": _sha_or_none(jdir / "stage-a" / "judge-summary.json")},
            "runs": {k: {"summary_sha256": v.get("summary_sha256"), "rows_sha256": v.get("rows_sha256"),
                         "collection_build_sha256": v.get("collection_build_sha256"),
                         "census_source": v.get("census_source")}
                     for k, v in sorted(d.runs.items())},
            "spot_checks": {k: _sha_or_none(v) for k, v in sorted((cfg.get("spot_checks") or {}).items())},
            "files": files,
            "sensitivity_sets": {k: _sha_or_none(v) for k, v in sorted((cfg.get("sensitivity_sets") or {}).items())}}


def _json_file(cfg: Mapping[str, Any], key: str) -> tuple[Any, dict[str, Any] | None]:
    p = cfg.get(key)
    if not p:
        return None, None
    return json.loads(Path(p).read_text()), {"sha256": asc.sha256_file(p)}


def fixed_sensitivities(d: Data, outcomes: Mapping[str, Mapping[str, Any]], l1: Mapping[str, Any],
                        an: Mapping[str, Any]) -> dict[str, Any]:
    """§7 (R6), displayed beside H1 and H2 whatever the verdict, never deciding.

    Inputs read from the config, each optional (``absent`` when not supplied), sha256 recorded:
    ``paper_covariates`` (private JSON, study paper -> ``topic``, ``citation_count``,
    ``text_similarity``, ``xml_only``, ``added_chunks`` {``neighbour``, ``random1``,
    ``random2``}), ``tercile_cuts`` (committed JSON, ``citation_count`` / ``text_similarity``
    -> [c1, c2], fixed before the stage-A outputs are read) and ``neighbour_attribution``
    (private JSON, added neighbour paper -> study paper)."""
    cov, cov_id = _json_file(d.cfg, "paper_covariates")
    cuts, cuts_id = _json_file(d.cfg, "tercile_cuts")
    attr, attr_id = _json_file(d.cfg, "neighbour_attribution")
    out: dict[str, Any] = {"label": "fixed sensitivity (R6), never deciding",
                           "inputs": {"paper_covariates": cov_id, "tercile_cuts": cuts_id,
                                      "neighbour_attribution": attr_id}}
    bal = d.bal
    g = [r for r in bal if r["kind"] == "G"]
    gold2 = bool(l1["H2"].get("gold_gate_pass"))
    members = {"H1": (g, H1_SPEC, "greater", None), "H2": (bal, H2_SPEC, "less", gold2)}
    scores = {h: paper_scores(recs, outcomes, spec) for h, (recs, spec, _, _) in members.items()}
    for h, (_, _, alt, gate) in members.items():
        names, sums, _, n = scores[h]
        bt = bootstrap_t(sums, alt, int(an["n_boot"]), int(an["boot_seed"]))
        out.setdefault(h, {})["bootstrap_t"] = {**bt, "verdict": retest_verdict(bt["p"], bt["min_attainable_p"],
                                                                                gold_gate=gate)}
        st = studentized_sign_flip(sums, alt, an)
        out[h]["studentized_identity_check"] = {
            **st, "label": "identity check, decides nothing: with one score per paper its exact p is the primary's"}
        if cov is None:
            out[h]["topic_clustering"] = "absent (no paper covariates supplied)"
            continue
        tsum, k_topics, no_topic = topic_sums(names, sums, {p: (cov.get(p) or {}).get("topic") for p in names})
        t = cs.sign_flip_test_exact(tsum, n_flips=int(an["n_perm"]), seed=int(an["perm_seed"]),
                                    alternative=alt)  # type: ignore[arg-type]
        out[h]["topic_clustering"] = {"p": t["p"], "p_method": t["p_method"], "clusters": k_topics,
                                      "papers_without_topic": no_topic, "n_nonzero": t["n_pages_nonzero"],
                                      "min_attainable_p": t["min_attainable_p"],
                                      "verdict": retest_verdict(t["p"], t["min_attainable_p"], gold_gate=gate)}
    # H2 only: the chunk-volume covariate, the XML-only stratum, the terciles
    names, sums, _, n2 = scores["H2"]
    if cov is None:
        for k in ("volume_covariate", "xml_only_stratum", "terciles"):
            out["H2"][k] = "absent (no paper covariates supplied)"
    else:
        x_of: dict[str, float] = {}
        for p in names:
            ac = (cov.get(p) or {}).get("added_chunks")
            if ac and all(a in ac for a in ("neighbour", "random1", "random2")):
                x_of[p] = float(ac["neighbour"]) - 0.5 * (float(ac["random1"]) + float(ac["random2"]))
        keep = [i for i, nm in enumerate(names) if nm in x_of]
        if keep:
            sv = np.asarray([sums[i] for i in keep], float)
            xv = np.asarray([x_of[names[i]] for i in keep], float)
            fl = freedman_lane_intercept(sv, xv, "less", int(an["n_perm"]), int(an["perm_seed"]))
            n_kept = sum(int(scores["H2"][2][i]) for i in keep)
            a = fl.get("intercept_per_paper")
            out["H2"]["volume_covariate"] = {
                **fl, "papers_left_out": len(names) - len(keep),
                "adjusted_net_points": round(100 * a * len(keep) / n_kept, 4) if (a is not None and n_kept) else None,
                "gold_gate": "the primary's unadjusted gold-only direction gate",
                "verdict": retest_verdict(fl["p"], fl["min_attainable_p"], gold_gate=gold2)}
        else:
            out["H2"]["volume_covariate"] = "absent (no added-chunk counts supplied)"
        xml = {p for p in names if (cov.get(p) or {}).get("xml_only") is True}
        out["H2"]["xml_only_stratum"] = {"papers": len(xml), **_subset_figure(bal, xml, outcomes, H2_SPEC, an)}
        terc: dict[str, Any] = {}
        for var in ("citation_count", "text_similarity"):
            cp = (cuts or {}).get(var)
            if not cp:
                terc[var] = "absent (no committed cut points)"
                continue
            groups: dict[str, set[str]] = {"T1": set(), "T2": set(), "T3": set()}
            no_val = 0
            for p in names:
                v = (cov.get(p) or {}).get(var)
                if v is None:
                    no_val += 1
                    continue
                groups[tercile_of(float(v), cp)].add(p)
            terc[var] = {"cuts": list(cp), "rule": TERCILE_RULE, "papers_without_value": no_val,
                         **{tk: {"papers": len(ps), **_subset_figure(bal, ps, outcomes, H2_SPEC, an)}
                            for tk, ps in groups.items()}}
        out["H2"]["terciles"] = terc
    out["origin"] = origin_counts(bal, outcomes, d.rows, d.pair_paper, d.pair_arm, attr)
    return out


def fragile(member: str, primary_status: str, sens: Mapping[str, Any]) -> dict[str, Any]:
    """§7, *fragile* (owner 2026-11-03): only H1 and H2 can be fragile, and only through the
    re-tests named in :data:`FRAGILE_BY` (H1: bootstrap-t, topic clustering; H2: those two and the
    volume covariate). A re-test **flips** the verdict when, at the member's level, it gives the
    other verdict (confirmed <-> not confirmed); a re-test that is itself GATE-NOT-EVALUABLE or
    absent flips nothing, a primary that is GATE-NOT-EVALUABLE or absent cannot be flipped.
    Subgroups never make anything fragile, H1b is exempt, and the label is never changed."""
    allowed = FRAGILE_BY.get(member, ())
    out: dict[str, Any] = {"considered": list(allowed), "flipped_by": [], "fragile": False}
    if not allowed:
        out["note"] = f"{member} is exempt from fragile (owner 2026-11-03)"
        return out
    decided = (CONFIRMED, NOT_CONFIRMED, NOT_CONFIRMED_GOLD)
    if primary_status not in decided:
        out["note"] = f"primary verdict {primary_status}: nothing to flip"
        return out
    prim = primary_status == CONFIRMED
    for name in allowed:
        v = sens.get(name)
        verdict = v.get("verdict") if isinstance(v, Mapping) else None
        if verdict in decided and (verdict == CONFIRMED) != prim:
            out["flipped_by"].append(name)
    out["fragile"] = bool(out["flipped_by"])
    return out


# ------------------------------------------------------------------ the analysis

def study_block(d: Data) -> dict[str, Any]:
    """§9.2 / §5.6: the study's mode, the analysis stage, the gate, and per run what the harness
    verified (``study.mode``, ``prereg.verified``, ``stage_a_commit.verified``, ``citable``)."""
    pre = getattr(d, "preflight", None) or {}
    runs = d.runs
    return {"mode": pre.get("mode"), "analysis_stage": pre.get("analysis_stage", "B"), "gate": pre.get("gate"),
            "stage_a_commit": pre.get("stage_a_commit"), "spot_checks_waiver": pre.get("spot_checks_waiver"),
            "prereg_verified": bool(runs) and all(r.get("prereg_verified") for r in runs.values()),
            "stage_b_runs_commit_verified": all(r.get("stage_a_commit_verified") for r in runs.values()
                                                if r.get("stage") == "B"),
            "runs_citable": bool(runs) and all(r.get("citable") for r in runs.values())}


def is_citable(study: Mapping[str, Any], prov: Mapping[str, Any], dry_run: bool) -> bool:
    """Citable only for the main run, from a clean tree, with every run citable and its frozen
    prereg verified, every stage-B run's stage-A commit verified, and the commit's public file
    tracked at HEAD."""
    return bool(not dry_run and study.get("mode") == "main" and prov.get("dirty") is False
                and prov.get("untracked") == 0 and study.get("runs_citable") and study.get("prereg_verified")
                and study.get("stage_b_runs_commit_verified")
                and (study.get("stage_a_commit") or {}).get("public_tracked_at_head") is True)


def analyse(cfg: Mapping[str, Any], d: Data | None = None) -> dict[str, Any]:
    d = d or Data(cfg)
    an = {"n_perm": int(cfg.get("n_perm", cs.N_FLIPS)), "n_boot": int(cfg.get("n_boot", cs.N_BOOT)),
          "perm_seed": asc.seed_int(asc.SEEDS["perm"]), "boot_seed": asc.seed_int(asc.SEEDS["boot"])}
    if not cfg.get("dry_run") and (an["n_perm"] != 10_000 or an["n_boot"] != 10_000):
        raise SystemExit("n_perm and n_boot are 10,000 (prereg §7); other values only in a dry run")
    stage = getattr(d, "stage", "B")
    reach = cfg.get("reach")
    legs, needs = coverage_scope(stage)
    cov = label_coverage(d.rows, d.by_id, d.final, legs, needs)
    if cov["pairs_without_final_label"] and not cfg.get("dry_run"):
        raise SystemExit(f"{cov['pairs_without_final_label']} demanded pair(s) of the analysed legs have no final judge "
                         f"label (analysis stage {stage}: judging not finished, or a leg changed after build-pairs); "
                         "nothing analysed")
    prim = d.outcomes("primary")
    study = study_block(d)
    res: dict[str, Any] = {"schema": SCHEMA, "record_set": d.identity, "label_coverage": cov, "study": study,
                           "analysis": {"stage": stage, "coverage_legs": len(legs), "needs": list(needs),
                                        "reads": "Layer 1 only (stage A)" if stage == "A" else "everything (stage B)"},
                           "seeds": {"perm": asc.SEEDS["perm"], "boot": asc.SEEDS["boot"], "rule": asc.SEED_RULE},
                           "n_perm": an["n_perm"], "n_boot": an["n_boot"], "leg_gates": dict(d.gates), "runs": d.runs,
                           "legs_read": {leg: len(rows) for leg, rows in d.rows.items()}}
    if getattr(d, "preflight", None) is not None:
        res["frozen_stage_a"] = check_frozen_stage_a(Path(cfg["judge_dir"]), Path(cfg["runs_dir"]), prim, d.by_id)
    l1 = layer1(d.bal, prim, d.gates, reach, an, power_artifact=cfg.get("power_artifact"))
    gap = figure(d.bal, prim, [(1.0, L("random1", "none", "default")), (-1.0, L("random2", "none", "default"))], an)
    l1["H2"]["draw_gap_points"] = gap["net_points"]
    l1["H2"]["within_random_draw_variation"] = within_draw_variation(l1["H2"].get("net_points"), gap["net_points"])
    js = d.judge_summary
    jsa = getattr(d, "stage_a_judge_summary", None) or {}
    l1["H2"]["false_yes_per_arm"] = {a: (js.get("drift", {}).get("scopes", {}).get(a) or {}).get("false_yes")
                                     for a in asc.ARM_GROUPS} if js else "absent (no judge summary)"
    counts, source, cross = false_yes_inputs(jsa, d.final, strict=not cfg.get("dry_run"))
    fy = false_yes_bound(l1["H2"].get("net_points"), counts, judged_only_hits(d.bal, prim), int(l1["H2"].get("n") or 0))
    fy["source"], fy["cross_check_with_final_rows"] = source, cross
    l1["H2"]["judge_false_yes_bound"] = fy
    fixed = fixed_sensitivities(d, prim, l1, an)
    for h in ("H1", "H2", "H1b"):
        l1[h]["fragile"] = fragile(h, l1[h]["status"], fixed.get(h) or {})
    res["layer1"] = l1
    if stage == "B":
        res["layer2"] = layer2(d, prim, an)
        res["layer2"]["serving_statements"] = serving_statements(res["layer2"]["decisions"], l1)
    else:
        res["layer2"] = "absent (analysis stage A: Layer 2 needs stage B's legs and labels)"
    res["noise_floor"] = noise_floor(d.bal, prim)
    res["judge"] = {k: js.get(k) for k in ("pairs_finalized", "sources", "unjudged", "unjudged_share",
                                           "judge_incomplete", "per_arm_pairs", "opus_no_reference", "stages",
                                           "drift")} if js else "absent"
    if isinstance(res["judge"], dict):
        res["judge"]["metrics_6_6_drift"] = drift_metrics(d.final)
    res["spot_checks"] = spot_flags(cfg, d)
    res["sensitivities"] = sensitivities(cfg, d, an, reach)
    res["fixed_sensitivities"] = fixed
    res["inputs"] = input_hashes(cfg, d)
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
    """§7, declared; printed beside H1, H1b and H2, never deciding and never making a verdict
    fragile (owner 2026-11-03: only the re-tests of :data:`FRAGILE_BY` can)."""
    out: dict[str, Any] = {"label": "declared sensitivity, never deciding, never fragile"}
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


#: dropped from every ``provenance`` mapping of the public copy (as chunktrial_export_public.py
#: does): a host name can be private, and the warnings are context, not identity
PUBLIC_DROP_PROVENANCE = ("host", "warnings")


def public_copy(obj: Any, _in_prov: bool = False) -> Any:
    """The ``--public`` copy: ``obj`` with ``host`` and ``warnings`` dropped from every
    ``provenance`` mapping. :func:`alias_study_common.public_problems` then refuses any host
    field left anywhere."""
    if isinstance(obj, Mapping):
        return {k: public_copy(v, k == "provenance") for k, v in obj.items()
                if not (_in_prov and k in PUBLIC_DROP_PROVENANCE)}
    if isinstance(obj, list):
        return [public_copy(v) for v in obj]
    return obj


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
    res["citable"] = is_citable(res["study"], prov, bool(cfg.get("dry_run")))
    dst = args.out or Path(cfg["judge_dir"]).parent / "results-alias-study.json"
    dst.write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")
    if args.public:
        forbidden = {r["record_id"] for r in d.records} | {str(r["paper"]) for r in d.records} | \
                    {r["gold_chunks"][0] for r in d.records} | {c for _, c in d.final}
        texts = [r["question"] for r in d.records]
        pub = public_copy(res)
        bad = asc.public_problems(pub, forbidden, texts)
        if bad:
            print(f"REFUSED: the public copy would carry {len(bad)} private string(s) ({sorted(set(bad))})",
                  file=sys.stderr)
            return 1
        args.public.write_text(json.dumps(pub, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps({h: {k: res["layer1"][h].get(k) for k in ("status", "p", "net_points", "level")}
                      for h in ("H1", "H2", "H1b")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
