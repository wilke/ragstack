"""Stage 0b' step 4 -- gates, sigma_d, joint power, the mode x size table, the guards.

Everything r3 SS5 step 5 reads is produced here:

* **levels and the window.** ``ERET`` and ``EPACK`` per arm on the development topics, with
  r3 SS3.1's [0.15, 0.90] window applied to each endpoint separately. An arm outside it is
  **demoted to descriptive** for that endpoint's contrasts, and the demotion is printed.
* **the estimand.** Confirmatory ``EPACK`` on the per-topic **intersection** of the
  documents both arms of a contrast reached; reached-set ``EPACK`` beside it; a topic whose
  intersection is empty is **dropped from both arms** of that contrast; ``n_retained`` is
  reported per contrast x endpoint; the **``EPACK := 0``** imputation sensitivity is
  mandatory and is computed. Sign or verdict disagreement between the two estimands is
  ``UNRESOLVED-BY-ESTIMAND``.
* **sigma_d** per contrast per endpoint with r2 SS8.5.7's bounds (chi2 upper at 80/90/95 %
  and a 10,000-draw bootstrap 80th percentile; **the larger governs**, as P.7 requires).
* **joint bootstrap power** -- both endpoints resampled **together** over topics (the
  cluster), 10,000 draws, seed 20260913, at Delta in {0, 0.01, 0.02}, against the 80 % bar.
  An intersection-union test's power is *not* the per-component power, which is exactly why
  r3 SS3.6 made the joint figure the gate; the marginals are printed beside it.
* **the mode x size table** -- each contrast's paired difference under ``vector``, ``bm25``
  and ``hybrid``, with cluster-bootstrap CIs, and the same table with the reranker off.
* **the pointed guards** -- r3 SS11 guard 1 (discrimination + window) and guard 3 (sizing
  from the measured sigma_d against the 600-query cap).
"""
from __future__ import annotations

import gzip
import json
import math
import statistics as st
import sys
import time

import numpy as np

import s0b_common as K
import s0_common as C  # noqa: F401
import s0_math as M

CONTRASTS = [
    ("N1", "NI", "fixed_tok512", "fixed_tok1024_ov0pct"),
    ("N3", "NI", "fixed_tok512", "fixed_tok2048_ov0pct"),
    ("R1", "SUP", "fixed_tok256_ov0pct", "fixed_tok2048_ov0pct"),
    ("R2", "SUP", "header512", "fixed_tok512_ov0pct"),
    ("R3", "SUP", "parent256", "fixed_tok512"),
    ("R4", "SUP", "nbr1_512", "fixed_tok512"),
]
EPACK_READINGS = ("a", "b", "c")
N_CONF_TOPICS = 80
N_BOOT = 10_000
PRIMARY_VARIANT = "summary"


# ------------------------------------------------------------------ loading
def load_cds() -> dict:
    by: dict = {}
    with gzip.open(K.OUT / "endpoints-cds.jsonl.gz", "rt") as f:
        for line in f:
            r = json.loads(line)
            by[(r["arm"], r["mode"], r["rerank"], r["budget"], r["variant"],
                r["topic"])] = r
    return by


def load_pointed() -> dict:
    by: dict = {}
    with gzip.open(K.OUT / "endpoints-pointed.jsonl.gz", "rt") as f:
        for line in f:
            r = json.loads(line)
            by.setdefault((r["arm"], r["mode"], r["rerank"], r["budget"]), {})[
                r["qid"]] = r
    return by


# ------------------------------------------------------------------ small stats
def sigma_block(d: list[float]) -> dict:
    n = len(d)
    if n < 2:
        return {"n": n, "sd": None, "governing_bound_80": None}
    sd = st.stdev(d)
    df = n - 1
    chi = {f"{int(c*100)}%": round(sd * M.sigma_upper_multiplier(c, df), 5)
           for c in (0.80, 0.90, 0.95)}
    rng = np.random.default_rng(K.SEED_BOOT)
    a = np.asarray(d, float)
    idx = rng.integers(0, n, size=(N_BOOT, n))
    bs80 = float(np.quantile(a[idx].std(axis=1, ddof=1), 0.80))
    gov = max(chi["80%"], bs80)
    return {"n": n, "mean_diff": round(st.mean(d), 6), "sd": round(sd, 6),
            "chi2_upper": chi, "bootstrap80_upper": round(bs80, 6),
            "governing_bound_80": round(gov, 6),
            "governing_source": "bootstrap" if bs80 >= chi["80%"] else "chi2",
            "sigma_requirement_0.158": round(gov, 6) <= 0.158}


def boot_ci(d: list[float], conf: float = 0.95, seed: int = K.SEED_BOOT) -> dict:
    if not d:
        return {"mean": None, "lo": None, "hi": None, "n": 0}
    a = np.asarray(d, float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(a), size=(N_BOOT, len(a)))
    means = a[idx].mean(axis=1)
    lo, hi = np.quantile(means, [(1 - conf) / 2, 1 - (1 - conf) / 2])
    return {"mean": round(float(a.mean()), 6), "lo": round(float(lo), 6),
            "hi": round(float(hi), 6), "n": len(a),
            "one_sided_upper_95": round(float(np.quantile(means, 0.95)), 6)}


def ni_verdict(d: list[float], eps: float = K.EPS) -> dict:
    """One-sided non-inferiority at alpha = 0.025 on the paired differences."""
    n = len(d)
    if n < 2:
        return {"n": n, "verdict": "UNDERPOWERED (n < 2)"}
    m, s = st.mean(d), st.stdev(d)
    se = s / math.sqrt(n)
    tcrit = M.t_ppf(0.975, n - 1)
    upper = m + tcrit * se
    return {"n": n, "mean_diff": round(m, 6), "sd": round(s, 6),
            "ci95_upper_one_sided": round(upper, 6), "eps": eps,
            "non_inferior": bool(upper < eps)}


def n_for_power(sigma: float, eps: float = K.EPS, delta: float = 0.0,
                power: float = 0.80, alpha: float = 0.025, cap: int = 100_000) -> int | None:
    lo, hi = 3, 64
    while hi < cap and M.ni_power(sigma, hi, eps, delta, alpha) < power:
        lo, hi = hi, hi * 2
    if hi >= cap:
        return None
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if M.ni_power(sigma, mid, eps, delta, alpha) >= power:
            hi = mid
        else:
            lo = mid
    return hi


def joint_power(pairs: list[tuple[float, float]], n: int, deltas=(0.0, 0.01, 0.02),
                eps: float = K.EPS, alpha: float = 0.025, draws: int = N_BOOT,
                seed: int = K.SEED_BOOT) -> dict:
    """Bootstrap power of the conjunctive (intersection-union) test.

    Both endpoints are resampled **together** over the clusters, so the correlation between
    them is carried into the draw. Each draw takes ``n`` clusters with replacement from the
    development set's paired differences, shifts each endpoint so its true mean is
    ``Delta``, and applies the one-sided NI t-test to each; the reported power is the
    fraction of draws in which **both** reject.
    """
    if len(pairs) < 2:
        return {"n_clusters_available": len(pairs), "status": "UNDERPOWERED"}
    A = np.asarray([p[0] for p in pairs], float)
    Bv = np.asarray([p[1] for p in pairs], float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(A), size=(draws, n))
    tcrit = M.t_ppf(1 - alpha, n - 1)
    out = {"n": n, "draws": draws, "clusters_available": len(A), "eps": eps,
           "alpha_per_endpoint": alpha, "seed": seed, "by_delta": {}}
    for dlt in deltas:
        a = A[idx] - A.mean() + dlt
        b = Bv[idx] - Bv.mean() + dlt
        sa = a.std(axis=1, ddof=1)
        sb = b.std(axis=1, ddof=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            ua = a.mean(axis=1) + tcrit * sa / math.sqrt(n)
            ub = b.mean(axis=1) + tcrit * sb / math.sqrt(n)
        ra = ua < eps
        rb = ub < eps
        out["by_delta"][str(dlt)] = {
            "power_ERET": round(float(ra.mean()), 4),
            "power_EPACK": round(float(rb.mean()), 4),
            "joint_power": round(float((ra & rb).mean()), 4),
            "meets_80pct": bool((ra & rb).mean() >= 0.80)}
    return out


# ------------------------------------------------------------------ CDS endpoints
def cds_levels(by: dict, budget: int, variant: str = PRIMARY_VARIANT) -> dict:
    out: dict = {}
    for arm in K.SCORING_ARMS:
        for mode in K.MODES:
            for rr in K.RERANK_STATES:
                rows = [by.get((arm, mode, rr, budget, variant, t))
                        for t in K.DEV_TOPICS]
                if any(r is None for r in rows):
                    continue
                eret = [len(r["reached"]) / r["n_evidence_docs"] for r in rows]
                lev = {"ERET": round(st.mean(eret), 6)}
                for k in EPACK_READINGS:
                    per = []
                    for r in rows:
                        v = [r["epack"][d][k] for d in r["reached"]
                             if r["epack"][d][k] is not None]
                        if v:
                            per.append(st.mean(v))
                    lev[f"EPACK_{k}"] = round(st.mean(per), 6) if per else None
                    lev[f"EPACK_{k}_topics"] = len(per)
                prod = []
                for r, e in zip(rows, eret):
                    v = [r["epack"][d]["a"] for d in r["reached"]
                         if r["epack"][d]["a"] is not None]
                    prod.append(e * (st.mean(v) if v else 0.0))
                lev["product_ERETxEPACKa"] = round(st.mean(prod), 6)
                lev["mean_sfr_realised"] = round(st.mean(r["sfr_tokens"] for r in rows))
                lev["mean_gen_tokens_est"] = round(
                    st.mean(r["gen_tokens_est"] for r in rows))
                lev["mean_docs_packed"] = round(st.mean(r["n_docs"] for r in rows), 2)
                lev["mean_sources"] = round(st.mean(r["n_sources"] for r in rows), 2)
                lev["mean_items"] = round(st.mean(r["n_items"] for r in rows), 2)
                lev["window"] = {
                    "ERET": "IN" if K.WINDOW[0] <= lev["ERET"] <= K.WINDOW[1] else "OUT",
                    "EPACK_a": ("IN" if lev["EPACK_a"] is not None
                                and K.WINDOW[0] <= lev["EPACK_a"] <= K.WINDOW[1]
                                else "OUT")}
                out[f"{arm}|{mode}|{rr}"] = lev
    return out


def cds_contrast(by: dict, cid: str, ctrl: str, cand: str, mode: str, rr: str,
                 budget: int, variant: str, reading: str = "a") -> dict:
    """One contrast: ERET, confirmatory (intersection) EPACK, reached-set, EPACK := 0."""
    d_eret, d_int, d_reach, d_zero = [], [], [], []
    dropped, retained = [], []
    per_topic = {}
    for t in K.DEV_TOPICS:
        a = by.get((ctrl, mode, rr, budget, variant, t))
        b = by.get((cand, mode, rr, budget, variant, t))
        if a is None or b is None:
            continue
        ea = len(a["reached"]) / a["n_evidence_docs"]
        eb = len(b["reached"]) / b["n_evidence_docs"]
        d_eret.append(ea - eb)
        ra, rb = set(a["reached"]), set(b["reached"])
        inter = sorted(ra & rb)
        va = [a["epack"][d][reading] for d in inter
              if a["epack"][d][reading] is not None]
        vb = [b["epack"][d][reading] for d in inter
              if b["epack"][d][reading] is not None]
        ma = [a["epack"][d][reading] for d in a["reached"]
              if a["epack"][d][reading] is not None]
        mb = [b["epack"][d][reading] for d in b["reached"]
              if b["epack"][d][reading] is not None]
        # EPACK := 0 imputation: an arm that reached nothing scores 0
        za = st.mean(ma) if ma else 0.0
        zb = st.mean(mb) if mb else 0.0
        d_zero.append(za - zb)
        if va and vb:
            d_int.append(st.mean(va) - st.mean(vb))
            retained.append(t)
        else:
            dropped.append({"topic": t, "n_intersection": len(inter),
                            "reached_control": len(ra), "reached_candidate": len(rb)})
        if ma and mb:
            d_reach.append(st.mean(ma) - st.mean(mb))
        per_topic[t] = {"ERET_control": round(ea, 6), "ERET_candidate": round(eb, 6),
                        "n_intersection": len(inter),
                        "EPACK_control_int": round(st.mean(va), 6) if va else None,
                        "EPACK_candidate_int": round(st.mean(vb), 6) if vb else None}
    ci_int = boot_ci(d_int)
    ci_reach = boot_ci(d_reach)
    disagree = (ci_int["mean"] is not None and ci_reach["mean"] is not None
                and (ci_int["mean"] > 0) != (ci_reach["mean"] > 0))
    return {
        "contrast": cid, "control": ctrl, "candidate": cand, "mode": mode,
        "rerank": rr, "budget": budget, "variant": variant, "epack_reading": reading,
        "ERET": {"differences": [round(x, 6) for x in d_eret], "ci": boot_ci(d_eret),
                 "sigma": sigma_block(d_eret), "ni": ni_verdict(d_eret),
                 "n_retained": len(d_eret)},
        "EPACK_confirmatory_intersection": {
            "differences": [round(x, 6) for x in d_int], "ci": ci_int,
            "sigma": sigma_block(d_int), "ni": ni_verdict(d_int),
            "n_retained": len(d_int), "topics_retained": retained,
            "topics_dropped": dropped, "n_dropped": len(dropped)},
        "EPACK_reached_set": {"ci": ci_reach, "sigma": sigma_block(d_reach),
                              "n_retained": len(d_reach)},
        "EPACK_zero_imputation_sensitivity": {"ci": boot_ci(d_zero),
                                              "sigma": sigma_block(d_zero),
                                              "n_retained": len(d_zero)},
        "estimand_agreement": "UNRESOLVED-BY-ESTIMAND" if disagree else "agree",
        "joint_power": joint_power(
            _pair_up(by, ctrl, cand, mode, rr, budget, variant, reading),
            N_CONF_TOPICS),
    }


def _pair_up(by, ctrl, cand, mode, rr, budget, variant, reading):
    """(d_ERET, d_EPACK_intersection) for the topics retained on BOTH endpoints."""
    out = []
    for t in K.DEV_TOPICS:
        a = by.get((ctrl, mode, rr, budget, variant, t))
        b = by.get((cand, mode, rr, budget, variant, t))
        if a is None or b is None:
            continue
        inter = sorted(set(a["reached"]) & set(b["reached"]))
        va = [a["epack"][d][reading] for d in inter if a["epack"][d][reading] is not None]
        vb = [b["epack"][d][reading] for d in inter if b["epack"][d][reading] is not None]
        if not (va and vb):
            continue
        ea = len(a["reached"]) / a["n_evidence_docs"]
        eb = len(b["reached"]) / b["n_evidence_docs"]
        out.append((ea - eb, st.mean(va) - st.mean(vb)))
    return out


# ------------------------------------------------------------------ pointed
def pointed_levels(by: dict, budget: int) -> dict:
    out = {}
    for arm in K.SCORING_ARMS:
        for mode in K.MODES:
            for rr in K.RERANK_STATES:
                rows = by.get((arm, mode, rr, budget))
                if not rows:
                    continue
                e = [r["ERET"] for r in rows.values()]
                p = [r["EPACK"] for r in rows.values()]
                pe = [r["EPACK"] for r in rows.values() if r["ERET"]]
                out[f"{arm}|{mode}|{rr}"] = {
                    "ERET": round(st.mean(e), 6),
                    "EPACK_given_reach": round(st.mean(pe), 6) if pe else None,
                    "EPACK_unconditional": round(st.mean(p), 6),
                    "n_queries": len(e), "n_reached": sum(e),
                    "ERET_wilson95": [round(x, 4) for x in M.wilson(sum(e), len(e))],
                    "EPACK_given_reach_wilson95":
                        [round(x, 4) for x in M.wilson(sum(pe), len(pe))] if pe else None,
                    "mean_sfr_realised": round(st.mean(r["sfr_tokens"]
                                                       for r in rows.values())),
                    "mean_docs_packed": round(st.mean(r["n_docs"]
                                                      for r in rows.values()), 2),
                    "window": {
                        "ERET": "IN" if K.WINDOW[0] <= st.mean(e) <= K.WINDOW[1] else "OUT",
                        "EPACK_given_reach": ("IN" if pe and K.WINDOW[0] <= st.mean(pe)
                                              <= K.WINDOW[1] else "OUT")}}
    return out


def pointed_contrast(by, cid, ctrl, cand, mode, rr, budget) -> dict:
    a = by.get((ctrl, mode, rr, budget))
    b = by.get((cand, mode, rr, budget))
    if not (a and b):
        return {"contrast": cid, "status": "ABSENT"}
    qids = sorted(set(a) & set(b))
    d_eret = [a[q]["ERET"] - b[q]["ERET"] for q in qids]
    # confirmatory containment: the queries BOTH arms reached (the pointed analogue of the
    # intersection estimand -- the same gold document, delivered by each arm's chunks)
    both = [q for q in qids if a[q]["ERET"] and b[q]["ERET"]]
    d_int = [a[q]["EPACK"] - b[q]["EPACK"] for q in both]
    d_zero = [a[q]["EPACK"] - b[q]["EPACK"] for q in qids]   # unconditional = := 0
    return {"contrast": cid, "control": ctrl, "candidate": cand, "mode": mode,
            "rerank": rr, "budget": budget,
            "ERET": {"ci": boot_ci(d_eret), "sigma": sigma_block(d_eret),
                     "ni": ni_verdict(d_eret), "n_retained": len(d_eret)},
            "EPACK_confirmatory_intersection": {
                "ci": boot_ci(d_int), "sigma": sigma_block(d_int),
                "ni": ni_verdict(d_int), "n_retained": len(d_int),
                "n_dropped": len(qids) - len(both)},
            "EPACK_zero_imputation_sensitivity": {"ci": boot_ci(d_zero),
                                                  "sigma": sigma_block(d_zero)},
            "joint_power_at_177": joint_power(
                [(a[q]["ERET"] - b[q]["ERET"], a[q]["EPACK"] - b[q]["EPACK"])
                 for q in both], len(both) or 2),
            "_pairs": [(a[q]["ERET"] - b[q]["ERET"], a[q]["EPACK"] - b[q]["EPACK"])
                       for q in both]}


# ------------------------------------------------------------------ tables
def mode_x_size(by_cds, by_pt, budget: int) -> dict:
    out = {"cds": {}, "pointed": {}}
    for cid, _f, ctrl, cand in CONTRASTS:
        for rr in K.RERANK_STATES:
            for mode in K.MODES:
                c = cds_contrast(by_cds, cid, ctrl, cand, mode, rr, budget,
                                 PRIMARY_VARIANT)
                out["cds"][f"{cid}|{mode}|rerank_{rr}"] = {
                    "d_ERET": c["ERET"]["ci"],
                    "d_EPACK_int": c["EPACK_confirmatory_intersection"]["ci"],
                    "d_EPACK_reached": c["EPACK_reached_set"]["ci"],
                    "n_retained_EPACK": c["EPACK_confirmatory_intersection"]["n_retained"],
                    "n_dropped": c["EPACK_confirmatory_intersection"]["n_dropped"]}
                p = pointed_contrast(by_pt, cid, ctrl, cand, mode, rr, budget)
                if p.get("status") != "ABSENT":
                    out["pointed"][f"{cid}|{mode}|rerank_{rr}"] = {
                        "d_ERET": p["ERET"]["ci"],
                        "d_EPACK_int": p["EPACK_confirmatory_intersection"]["ci"],
                        "n_retained_EPACK":
                            p["EPACK_confirmatory_intersection"]["n_retained"]}
    return out


def budget_curves(by_cds, by_pt) -> dict:
    out = {"cds": {}, "pointed": {}}
    for B in K.BUDGETS:
        out["cds"][str(B)] = cds_levels(by_cds, B)
        out["pointed"][str(B)] = pointed_levels(by_pt, B)
    return out


def main() -> None:
    t0 = time.time()
    by_cds = load_cds()
    by_pt = load_pointed()
    B = K.PRIMARY_BUDGET
    checks = json.loads((K.OUT / "checks.json").read_text()) \
        if (K.OUT / "checks.json").exists() else {}

    out: dict = {
        "budget_primary": B, "budgets": list(K.BUDGETS), "eps": K.EPS,
        "window": list(K.WINDOW), "primary_variant": PRIMARY_VARIANT,
        "levels": {"cds": {str(b): cds_levels(by_cds, b) for b in K.BUDGETS},
                   "cds_description": {str(B): cds_levels(by_cds, B, "description")},
                   "pointed": {str(b): pointed_levels(by_pt, b) for b in K.BUDGETS}},
        "contrasts": {"cds": {}, "pointed": {}},
        "epack_readings": {},
        "mode_x_size": mode_x_size(by_cds, by_pt, B),
        "provenance": K.provenance(),
    }

    for cid, fam, ctrl, cand in CONTRASTS:
        c = cds_contrast(by_cds, cid, ctrl, cand, "hybrid", "on", B, PRIMARY_VARIANT)
        c["family"] = fam
        out["contrasts"]["cds"][cid] = c
        out["epack_readings"][cid] = {
            r: cds_contrast(by_cds, cid, ctrl, cand, "hybrid", "on", B,
                            PRIMARY_VARIANT, r)["EPACK_confirmatory_intersection"]["ci"]
            for r in EPACK_READINGS}
        p = pointed_contrast(by_pt, cid, ctrl, cand, "hybrid", "on", B)
        pairs = p.pop("_pairs", [])
        p["family"] = fam
        out["contrasts"]["pointed"][cid] = p

    # ------------------------------------------------------------- guard 1
    disc = (checks.get("discrimination_and_budget_bind", {})
            .get("discrimination_top10_doc_sets_differ", {}))
    lv = out["levels"]["pointed"][str(B)]
    epack_in = {k: v["window"]["EPACK_given_reach"] for k, v in lv.items()
                if k.endswith("|hybrid|on")}
    out["guard1_discrimination"] = {
        "top10_doc_sets_differ": {k: v for k, v in disc.items()
                                  if k.startswith("pointed")},
        "bar": ">= 0.25 of queries",
        "EPACK_window_by_arm_hybrid_rerank_on": epack_in,
        "ERET_window_by_arm_hybrid_rerank_on": {
            k: v["window"]["ERET"] for k, v in lv.items() if k.endswith("|hybrid|on")},
        "status": None}
    d_ok = all(v["rate"] >= 0.25 for k, v in disc.items() if k.startswith("pointed"))
    w_ok = all(v == "IN" for v in epack_in.values())
    out["guard1_discrimination"]["status"] = (
        "PASS" if (d_ok and w_ok) else
        f"FAIL (discrimination {'ok' if d_ok else 'below 0.25'}; "
        f"EPACK window {'ok' if w_ok else 'outside for some arm'})")

    # ------------------------------------------------------------- guard 3 (sizing)
    sizing = {}
    for cid, fam, ctrl, cand in CONTRASTS:
        p = pointed_contrast(by_pt, cid, ctrl, cand, "hybrid", "on", B)
        if p.get("status") == "ABSENT":
            continue
        pairs = p.pop("_pairs")
        se = p["ERET"]["sigma"]["governing_bound_80"]
        sp = p["EPACK_confirmatory_intersection"]["sigma"]["governing_bound_80"]
        row = {"sigma_d_ERET_bound80": se, "sigma_d_EPACK_bound80": sp,
               "n_for_80pct_ERET": n_for_power(se) if se else None,
               "n_for_80pct_EPACK": n_for_power(sp) if sp else None}
        row["n_required_per_endpoint_max"] = max(
            [x for x in (row["n_for_80pct_ERET"], row["n_for_80pct_EPACK"])
             if x is not None] or [0]) or None
        for n in (177, 300, 600):
            row[f"joint_power_at_{n}"] = joint_power(pairs, n)["by_delta"]
        # smallest n whose JOINT power at Delta = 0 reaches 0.80, searched on the cap
        need = None
        for n in range(20, 1201, 10):
            if joint_power(pairs, n, deltas=(0.0,), draws=2000)["by_delta"]["0.0"][
                    "joint_power"] >= 0.80:
                need = n
                break
        row["n_for_80pct_JOINT_delta0"] = need
        row["within_600_cap"] = (need is not None and need <= 600)
        sizing[cid] = row
    out["guard3_sizing"] = {
        "rule": "alpha = 0.025 one-sided per endpoint, eps = 0.05, 80 % power, "
                "cluster = source document (one query per document)",
        "cap": 600, "by_contrast": sizing,
        "status": ("PASS" if all(v["within_600_cap"] for v in sizing.values())
                   else "OVER CAP for at least one contrast -- r3 SS11 guard 3 makes the "
                        "pointed population a reported secondary in that case")}

    # ------------------------------------------------------------- demotions
    dem = []
    for pop, lvl in (("cds", out["levels"]["cds"][str(B)]),
                     ("pointed", out["levels"]["pointed"][str(B)])):
        for k, v in lvl.items():
            if not k.endswith("|hybrid|on"):
                continue
            for ep, w in v["window"].items():
                if w == "OUT":
                    dem.append({"population": pop, "arm_mode": k, "endpoint": ep,
                                "level": v.get(ep)})
    out["window_demotions"] = {
        "rule": "r3 SS3.1: an arm whose endpoint leaves [0.15, 0.90] on the dev topics is "
                "DEMOTED TO DESCRIPTIVE for that endpoint's contrasts",
        "demoted": dem, "n": len(dem)}

    out["seconds"] = round(time.time() - t0, 1)
    K.atomic_json(K.OUT / "stats.json", out)
    K.atomic_json(K.ART / "stats.json", out)
    print(json.dumps({"window_demotions": out["window_demotions"]["n"],
                      "guard1": out["guard1_discrimination"]["status"],
                      "guard3": out["guard3_sizing"]["status"],
                      "seconds": out["seconds"]}, indent=1), flush=True)


if __name__ == "__main__":
    sys.exit(main())
