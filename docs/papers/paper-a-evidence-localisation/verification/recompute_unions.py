#!/usr/bin/env python3
"""Union growth: set-level and sentence-level unions, marginal gains, Michaelis-Menten fits,
the locator blow-up and the P-ext-2 scoring, recomputed.

Definitions (DRAFT §5.3; RESULTS-stage0b-relabel-r31.md and -r31ext.md; PREDICTIONS.md):
* set union at k = number of D3-distinct evidence sets among the first k readings (a set is
  new unless its span-union Jaccard with an accepted set is >= 0.5), presentation order;
* sentence union at k = number of distinct (unit, sentence) pairs selected by the first k;
* curves are means over the pairs positive somewhere within the judge's full depth (fixed
  denominator across k);
* marginal gain at k = (U(k) - U(k-1)) / U(k) on the MEAN curve;
* MM fit U(k) = Vmax k / (Km + k), least squares on the mean curve at k = 1..n;
  fraction of asymptote at n = n / (Km + n);
* blow-up = a reading with spans_emitted >= 10 x max(spans_seen, 1); the excluded curves drop
  every pair flagged for EITHER judge.

Alternatives computed beside each, because the text does not pin them: marginal gain relative
to U(k-1); per-pair mean of relative gains; observed U(n)/Vmax as the 'fraction of asymptote'.
"""
from __future__ import annotations

import vcommon as V

N = {"scout": 20, "qwen": 10}


def curve_stats(curves: dict, n: int, exclude=frozenset()) -> dict:
    """curves: {pair: [U(1), ..., U(n)]}. Mean over pairs positive somewhere (U(n) > 0)."""
    keys = [k for k, c in curves.items() if c[n - 1] > 0 and k not in exclude]
    means = [V.fmean([curves[k][m] for k in keys]) for m in range(n)]
    med = [V.median([curves[k][m] for k in keys]) for m in range(n)]
    g = lambda m: (means[m] - means[m - 1]) / means[m]          # noqa: E731
    fit = V.mm_fit(list(range(1, n + 1)), means)
    pp = [V.fmean([(curves[k][m] - curves[k][m - 1]) / curves[k][m]
                   for k in keys if curves[k][m] > 0]) for m in range(1, n)]
    return {"n_pairs_positive_somewhere": len(keys),
            "mean_at_k": {str(m + 1): means[m] for m in range(n)},
            "median_at_n": med[n - 1],
            "marginal_gain_at_n": g(n - 1),
            "marginal_gain_at_5": g(4) if n >= 5 else None,
            "alt_gain_over_previous_at_n": (means[n - 1] - means[n - 2]) / means[n - 2],
            "alt_per_pair_mean_relative_gain_at_n": pp[-1],
            "mm_fit": fit, "linear_r2": V.linear_r2(list(range(1, n + 1)), means),
            "frac_of_asymptote_at_n": n / (fit["Km"] + n),
            "alt_observed_over_vmax": means[n - 1] / fit["Vmax"]}


def set_curves(by, n) -> dict:
    out = {}
    for k, v in by.items():
        out[k] = []
        acc_input = []
        for m in range(n):
            acc_input.append(v[m]["sets"])
            out[k].append(len(V.distinct_sets(acc_input)))
    return out


def sentence_curves(readings: dict, n) -> dict:
    out = {}
    for k, R in readings.items():
        seen, c = set(), []
        for m in range(n):
            seen |= R[m]
            c.append(len(seen))
        out[k] = c
    return out


def main() -> None:
    prov = V.provenance()
    res: dict = {}

    # ---- r3.1, five presentations: set-level union marginal gain at k = 5
    for j in ("scout", "qwen"):
        by = V.load("r31", j)
        res[f"r31.{j}.sets_k5"] = curve_stats(set_curves(by, 5), 5)

    # ---- pre-registration fit (P-ext-2): scout sentence union, committed r3.1 labels, k=1..5
    r31s = V.load("r31", "scout")
    pre = curve_stats(sentence_curves({k: [V.sentence_set(v[m]) for m in range(5)]
                                       for k, v in r31s.items()}, 5), 5)
    f = pre["mm_fit"]
    pre["projected_U20"] = f["Vmax"] * 20 / (f["Km"] + 20)
    pre["projected_U20_over_Vmax"] = 20 / (f["Km"] + 20)
    pre["threshold_U20_within_10pct"] = 0.9 * f["Vmax"]
    res["prefit.scout.sentences_k1_5"] = pre

    # ---- extension
    ext = {j: V.load("r31ext", j) for j in ("scout", "qwen")}
    blow = {}
    for j in ext:
        hits = []
        for k, v in sorted(ext[j].items()):
            for p in range(N[j]):
                vs = v[p].get("vstats") or {}
                if vs.get("spans_emitted", 0) >= 10 * max(vs.get("spans_seen", 0), 1):
                    hits.append({"topic": k[0], "docno": k[1], "presentation": p,
                                 "spans_seen": vs.get("spans_seen", 0),
                                 "spans_emitted": vs.get("spans_emitted", 0),
                                 "distinct_sentences": len(V.sentence_set(v[p]))})
        blow[j] = {"n_readings_examined": len(ext[j]) * N[j], "n_flagged": len(hits),
                   "readings": hits}
    res["blowups"] = blow
    excl = frozenset((h["topic"], h["docno"]) for j in blow for h in blow[j]["readings"])
    sizes = sorted((len(V.sentence_set(v[p])) for j in ext for v in ext[j].values()
                    for p in range(N[j])), reverse=True)
    res["reading_size_distribution"] = {"largest": sizes[:3], "median": V.median(sizes)}

    R = {j: {k: [V.sentence_set(v[m]) for m in range(N[j])] for k, v in ext[j].items()}
         for j in ext}
    for j in ext:
        sc = sentence_curves(R[j], N[j])
        res[f"r31ext.{j}.sentences"] = curve_stats(sc, N[j])
        res[f"r31ext.{j}.sentences_excluding_blowups"] = curve_stats(sc, N[j], excl)
        res[f"r31ext.{j}.sets"] = curve_stats(set_curves(ext[j], N[j]), N[j])

    pooled = {}
    for k in sorted(set(R["scout"]) & set(R["qwen"])):
        inter = [x for pair in zip(R["scout"][k][:10], R["qwen"][k]) for x in pair]
        pooled[k] = inter + R["scout"][k][10:]
    pc = sentence_curves(pooled, 30)
    res["pooled30.sentences"] = curve_stats(pc, 30)
    res["pooled30.sentences_excluding_blowups"] = curve_stats(pc, 30, excl)

    # ---- P-ext-2 scored against the committed pre-registration values
    import json
    prereg = json.loads((V.ART / "r31ext" / "predictions.json").read_text())["P_ext_2"]
    u20 = res["r31ext.scout.sentences"]["mean_at_k"]["20"]
    u20x = res["r31ext.scout.sentences_excluding_blowups"]["mean_at_k"]["20"]
    proj = prereg["projected_U20"]
    res["p_ext_2"] = {
        "observed_U20": u20, "observed_over_prereg_Vmax": u20 / prereg["Vmax_prereg"],
        "SCORED": "PASS" if u20 >= prereg["threshold_U20"] else "FAIL",
        "relative_error": abs(u20 - proj) / proj,
        "observed_U20_excluding_blowups": u20x,
        "SCORED_excluding": "PASS" if u20x >= prereg["threshold_U20"] else "FAIL",
        "relative_error_excluding": abs(u20x - proj) / proj,
        "median_U20_all_pairs": res["r31ext.scout.sentences"]["median_at_n"],
        "refit_unfiltered": res["r31ext.scout.sentences"]["mm_fit"],
        "refit_unfiltered_linear_r2": res["r31ext.scout.sentences"]["linear_r2"]}
    V.write("recompute_unions", prov, {"excluded_pairs": sorted(excl), "results": res})


if __name__ == "__main__":
    main()
