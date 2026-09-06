"""Stage 0b step 5 -- the complete SS8.5.7 calibration table and the three-outcome gate.

Every row of SS8.5.7 / P.7 is produced here. Nothing is assumed: p_flip, rho, m-bar,
binary discordance, rho_variant and n_retained are MEASURED, and where the SPEC offers
two instruments (chi2 vs bootstrap bound; model-based vs direct sigma_d) **the larger
governs**, as P.7 requires.
"""
from __future__ import annotations

import json
import math
import statistics as st

import numpy as np

import s0_common as C
import s0_math as M

# (id, family, control, candidate) -- SS8.1, control MINUS candidate
CONTRASTS = [
    ("N1", "NI", "fixed_tok512", "fixed_tok1024_ov0pct"),
    ("N2", "NI", "fixed_tok512", "fixed_tok512_ov0pct"),
    ("R1", "SUP", "fixed_tok256_ov0pct", "fixed_tok2048_ov0pct"),
    ("R2", "SUP", "header512", "fixed_tok512_ov0pct"),
    ("R3", "SUP", "parent256", "fixed_tok512"),
]
B = str(C.PRIMARY_BUDGET)


def one_way_icc(groups: list[list[float]]) -> float:
    """ANOVA one-way ICC of the per-unit paired-difference indicators within topics."""
    groups = [g for g in groups if len(g) >= 1]
    k = len(groups)
    ns = [len(g) for g in groups]
    N = sum(ns)
    if k < 2 or N <= k:
        return float("nan")
    grand = sum(sum(g) for g in groups) / N
    msb = sum(len(g) * (sum(g) / len(g) - grand) ** 2 for g in groups) / (k - 1)
    ssw = sum(sum((x - sum(g) / len(g)) ** 2 for x in g) for g in groups)
    msw = ssw / (N - k)
    n0 = (N - sum(n * n for n in ns) / N) / (k - 1)
    den = msb + (n0 - 1) * msw
    return float("nan") if den == 0 else (msb - msw) / den


def boot_sigma_upper(d: list[float], q: float = 0.80, n: int = 10000, seed=C.SEED_BOOT):
    rng = np.random.default_rng(seed)
    a = np.asarray(d, dtype=float)
    idx = rng.integers(0, len(a), size=(n, len(a)))
    sds = a[idx].std(axis=1, ddof=1)
    return float(np.quantile(sds, q))


def main() -> None:
    euc = json.loads((C.WORK / "euc.json").read_text())
    mat = json.loads((C.WORK / "unit_matrix.json").read_text())
    uinfo = json.loads((C.WORK / "units.json").read_text())
    qrels = json.loads((C.WORK / "qrels_all.json").read_text())
    plan = json.loads((C.WORK / "corpus_plan.json").read_text())
    corpus_docs = {p[0] for p in json.loads((C.WORK / "manifest.json").read_text())["pairs"]}
    labels = [json.loads(x) for x in (C.WORK / "labels.jsonl").read_text().splitlines() if x]

    topics = sorted(uinfo["m_per_topic"])
    m_per_topic = uinfo["m_per_topic"]
    mbar = st.mean(m_per_topic.values()) if m_per_topic else float("nan")
    n_rel = {t: sum(1 for _d, g in qrels[t].items() if g >= 1) for t in qrels}

    out: dict = {"row3_units_per_topic": {
        "m_per_topic": m_per_topic, "m_bar": round(mbar, 3),
        "m_median": st.median(m_per_topic.values()) if m_per_topic else None,
        "m_min": min(m_per_topic.values()) if m_per_topic else None,
        "m_max": max(m_per_topic.values()) if m_per_topic else None,
        "cap": C.UNIT_CAP,
        "cap_hit_topics": uinfo["steps"]["cap_hit_topics"],
        "cap_hit_rate": round(uinfo["steps"]["cap_hit_topics"] / max(len(m_per_topic), 1), 3),
        "d3_steps": uinfo["steps"]}}

    # ------------------------------------------------ rows 1, 2, 4, 5, 7 per contrast
    rows = {}
    for cid, fam, ctrl, cand in CONTRASTS:
        r: dict = {"family": fam, "control": ctrl, "candidate": cand}
        if ctrl not in euc or cand not in euc:
            r["status"] = "UNCALIBRATED (arm not built at Stage 0)"
            rows[cid] = r
            continue
        r["status"] = "calibrated directly"
        # ---- row 1: sigma_d(EUC@4096) on `summary`, per-topic paired differences ----
        # SS7.4 / SS8.5.6: topics with < 3 evidence units are EXCLUDED from the primary.
        # The exclusion is arm-invariant (it is computed from labels alone) and it is
        # applied here so the dev sigma_d describes the same estimator the confirmation
        # run will use.
        ts = [t for t in topics
              if t in euc[ctrl]["summary"][B] and t in euc[cand]["summary"][B]
              and m_per_topic.get(t, 0) >= 3]
        d = [euc[ctrl]["summary"][B][t]["EUC"] - euc[cand]["summary"][B][t]["EUC"]
             for t in ts]
        sd = st.stdev(d) if len(d) > 1 else float("nan")
        df = len(d) - 1
        chi = {f"{int(c*100)}%": round(sd * M.sigma_upper_multiplier(c, df), 4)
               for c in (0.80, 0.90, 0.95)}
        bs80 = boot_sigma_upper(d)
        gov_bound = max(chi["80%"], bs80)
        r["row1_sigma_d"] = {
            "n_used": len(ts),
            "topics_excluded_lt3_units": sorted(
                t for t in topics if m_per_topic.get(t, 0) < 3),
            "topics": ts, "differences": [round(x, 6) for x in d],
            "mean_diff": round(st.mean(d), 5), "point_estimate": round(sd, 5),
            "chi2_upper": chi, "chi2_multipliers_df": df,
            "bootstrap80_upper": round(bs80, 5),
            "governing_bound_80": round(gov_bound, 5),
            "governing_bound_source": "bootstrap" if bs80 >= chi["80%"] else "chi2",
            "by_n_rel_stratum": {t: n_rel[t] for t in ts}}
        # ---- row 2: unit-level p_flip and rho -------------------------------------
        gA, gB = mat[ctrl]["summary"][B], mat[cand]["summary"][B]
        groups, flips = [], []
        for t in ts:
            g = [a - b for a, b in zip(gA[t], gB[t])]
            groups.append([float(x) for x in g])
            flips.extend(abs(x) for x in g)
        p_flip = sum(flips) / len(flips) if flips else float("nan")
        rho = one_way_icc(groups)
        rho_c = 0.0 if (isinstance(rho, float) and math.isnan(rho)) else max(rho, 0.0)
        model_sd = math.sqrt(max(p_flip, 0.0) / mbar * (1 + (mbar - 1) * rho_c)) \
            if mbar and not math.isnan(p_flip) else float("nan")
        # topic cluster-bootstrap CI on rho
        rng = np.random.default_rng(C.SEED_BOOT)
        rs = []
        for _ in range(2000):
            pick = rng.integers(0, len(groups), len(groups))
            rr = one_way_icc([groups[i] for i in pick])
            if not math.isnan(rr):
                rs.append(rr)
        r["row2_pflip_rho"] = {
            "p_flip": round(p_flip, 5), "rho_icc": None if math.isnan(rho) else round(rho, 5),
            "rho_clamped_for_model": round(rho_c, 5),
            "rho_boot95": [round(float(np.quantile(rs, 0.025)), 4),
                           round(float(np.quantile(rs, 0.975)), 4)] if rs else None,
            "n_units": len(flips),
            "model_sigma_d": round(model_sd, 5),
            "direct_sigma_d": round(sd, 5),
            "governing_point_sigma_d": round(max(model_sd, sd), 5),
            "governing_source": "model" if model_sd >= sd else "direct"}
        gov_point = max(model_sd, sd)
        # the bound is applied to whichever point estimate governs
        gov_bound_final = max(gov_bound, gov_point * M.sigma_upper_multiplier(0.80, df))
        r["governing_sigma_d_point"] = round(gov_point, 5)
        r["governing_sigma_d_bound80"] = round(gov_bound_final, 5)
        # ---- row 4: binary discordance for ES-Hit@4096 ----------------------------
        hits = [(euc[ctrl]["summary"][B][t]["ES_Hit"], euc[cand]["summary"][B][t]["ES_Hit"])
                for t in ts]
        disc = sum(1 for a, b in hits if a != b)
        wl, wu = M.wilson(disc, len(hits))
        r["row4_binary_discordance"] = {
            "discordant_topics": disc, "n": len(hits),
            "d": round(disc / len(hits), 4),
            "wilson95": [round(wl, 4), round(wu, 4)],
            "requirement_d_le": 0.025,
            "resolvable_at_point": (disc / len(hits)) <= 0.025,
            "resolvable_at_wilson_upper": wu <= 0.025,
            "sigma_d_binary_implied": round(math.sqrt(disc / len(hits)), 4)}
        # ---- row 5: rho_variant ----------------------------------------------------
        tsv = [t for t in ts if t in euc[ctrl]["description"][B]
               and t in euc[cand]["description"][B]]
        d1 = [euc[ctrl]["summary"][B][t]["EUC"] - euc[cand]["summary"][B][t]["EUC"]
              for t in tsv]
        d2 = [euc[ctrl]["description"][B][t]["EUC"] - euc[cand]["description"][B][t]["EUC"]
              for t in tsv]
        d = d1 if False else d          # summary-side d is unchanged; tsv is the pair set
        if len(tsv) > 2 and st.stdev(d1) > 0 and st.stdev(d2) > 0:
            rv = float(np.corrcoef(d1, d2)[0, 1])
            div = math.sqrt(2.0 / (1.0 + rv)) if rv > -1 else float("inf")
        else:
            rv, div = float("nan"), float("nan")
        r["row5_rho_variant"] = {
            "n_topics_paired": len(tsv),
            "rho_variant": None if math.isnan(rv) else round(rv, 4),
            "measured_divisor": None if math.isnan(div) else round(div, 4),
            "assumed_divisor_in_old_text": 1.3,
            "adaptation_applies_if_divisor_ge": 1.15,
            "applies": bool(not math.isnan(div) and div >= 1.15)}
        rows[cid] = r
    out["contrasts"] = rows

    # ---------------------------------------------------- row 6: projected n_retained
    # criterion 3 is NON-OUTCOME data and is computed EXACTLY on all 80 (SS2.3, SS8.5.6)
    conf = plan["conf"]
    c3 = [t for t in conf
          if sum(1 for dd, g in qrels[t].items() if g >= 1 and dd in corpus_docs) < 5]
    dev_lt3 = [t for t, m in m_per_topic.items() if m < 3]
    rate_lt3 = len(dev_lt3) / max(len(m_per_topic), 1)
    by_t: dict[str, list] = {}
    for rec in labels:
        by_t.setdefault(rec["topic"], []).append(rec)
    fail_label = [t for t, rs in by_t.items()
                  if sum(1 for x in rs if x["dropped"]) > len(rs) / 3]
    # SS8.5.6: "> 1/2 of its labeled documents required SS6.5 windowing AND the windowed
    # union failed the self-consistency check". Self-consistency is measured on the 10%
    # duplicate sample, so a per-topic windowed-union consistency figure exists only for
    # topics that happen to carry a duplicated windowed pair; where it does not exist the
    # criterion cannot fire and that is recorded rather than approximated.
    from s0_score import jaccard as _jac
    incons_win = set()
    for rec in labels:
        if rec["windowed"] and "dup_sets" in rec:
            a = [[sp["start"], sp["end"]] for s_ in rec["sets"] for sp in s_["spans"]]
            b = [[sp["start"], sp["end"]] for s_ in rec["dup_sets"] for sp in s_["spans"]]
            if not (not a and not b) and _jac(a, b) < C.JACCARD_MERGE:
                incons_win.add(rec["topic"])
    win_fail = [t for t, rs in by_t.items()
                if sum(1 for x in rs if x["windowed"]) > len(rs) / 2 and t in incons_win]
    win_majority = [t for t, rs in by_t.items()
                    if sum(1 for x in rs if x["windowed"]) > len(rs) / 2]
    rate_label = len(fail_label) / max(len(by_t), 1)
    rate_win = len(win_fail) / max(len(by_t), 1)
    keep = (1 - rate_lt3) * (1 - rate_label) * (1 - rate_win)
    n_ret = int(round(len(conf) * keep)) - len(c3)
    n_ret = max(n_ret, 0)
    out["row6_n_retained"] = {
        "n_nominal": 80,
        "criterion_lt5_fetchable_relevants_EXACT": {"topics": c3, "n": len(c3)},
        "criterion_lt3_units_dev_rate": {"dev_topics_below_3": dev_lt3,
                                         "rate": round(rate_lt3, 4)},
        "criterion_label_failure_dev_rate": {"dev_topics": fail_label,
                                             "rate": round(rate_label, 4)},
        "criterion_windowing_failure_dev_rate": {
            "dev_topics_failing_both_limbs": win_fail, "rate": round(rate_win, 4),
            "dev_topics_majority_windowed": win_majority,
            "note": "both limbs required (SS8.5.6): majority windowed AND the windowed "
                    "union failed self-consistency"},
        "projected_n_retained": n_ret,
        "gate_n_retained_lt_60": n_ret < 60,
        "sigma_requirement_at_n": {
            str(n): round(M.sigma_for_power(n, C.EPS), 4)
            for n in (80, 76, 72, 68, 64, 60, n_ret) if n >= 5},
        "conf_n_rel_distribution": {
            "min": min(n_rel[t] for t in conf), "max": max(n_rel[t] for t in conf),
            "median": st.median([n_rel[t] for t in conf]),
            "n_below_dev_window_40": sum(1 for t in conf if n_rel[t] < 40),
            "n_above_dev_window_250": sum(1 for t in conf if n_rel[t] > 250)},
        "dev_n_rel_distribution": {t: n_rel[t] for t in C.DEV_TOPICS}}

    # ------------------------------------------------------------- row 7: power table
    for cid, _f, _c, _d in CONTRASTS:
        r = rows[cid]
        if "governing_sigma_d_point" not in r:
            continue
        pw = {}
        for lbl, sdv in (("at_point_estimate", r["governing_sigma_d_point"]),
                         ("at_80pct_upper_bound", r["governing_sigma_d_bound80"])):
            pw[lbl] = {"sigma_d": sdv,
                       "n80": {f"{dd:.2f}": round(100 * M.ni_power(sdv, 80, C.EPS, dd), 1)
                               for dd in (0.0, 0.01, 0.02)},
                       f"n{n_ret}": {f"{dd:.2f}": round(100 * M.ni_power(sdv, max(n_ret, 5),
                                                                        C.EPS, dd), 1)
                                     for dd in (0.0, 0.01, 0.02)}}
        r["row7_power"] = pw
        # ---------------------------------------------- the three-outcome gate (SS8.5.5)
        req80 = M.sigma_for_power(80, C.EPS)
        reqn = M.sigma_for_power(max(n_ret, 5), C.EPS)
        holds_point = r["governing_sigma_d_point"] <= reqn
        holds_bound = r["governing_sigma_d_bound80"] <= reqn
        r["gate"] = {
            "sigma_requirement_at_n_retained": round(reqn, 4),
            "sigma_requirement_at_n80": round(req80, 4),
            "holds_at_point_estimate": bool(holds_point),
            "holds_at_80pct_upper_bound": bool(holds_bound),
            "verdict": ("POWER GATE PASSES" if holds_bound else
                        "POWER-UNCERTAIN" if holds_point else
                        "FAILS AT THE POINT ESTIMATE -> adaptations or UNDERPOWERED")}

    out["gate_bound_multipliers_df9"] = {
        f"{int(c*100)}%": round(M.sigma_upper_multiplier(c, 9), 4)
        for c in (0.80, 0.90, 0.95, 0.975)}
    out["selftest"] = M.selftest()
    C.atomic_json(C.WORK / "stage0_table.json", out)
    print(json.dumps(out, indent=1)[:12000])


if __name__ == "__main__":
    main()
