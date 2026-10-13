#!/usr/bin/env python3
"""Whether-agreement, span self-consistency, partial-credit Jaccard, union split-half,
cross-judge kappa / Jaccard and the Claude within-family agreement, recomputed.

Definitions (DRAFT §3.3-3.4, §5.1, §5.3, §5.5):
* whether verdict of a reading = it carries at least one verified evidence set;
* whether-agreement at k = share of pairs on which ALL of the first k readings agree;
  mean pairwise = share of (pair, reading-pair) comparisons that agree;
* span self-consistency = span-union character Jaccard >= 0.5 between two single readings,
  two empty verdicts counting as consistent;
* partial credit = raw span-union Jaccard over all reading pairs, 0/0 comparisons excluded;
* union split-half = union of readings 0-2 vs union of 3-4 (D3-distinct sets), same rule;
* kappa = Cohen's kappa on the binary whether verdict.

Sensitivity reported beside each whether figure: a reading whose spans ALL failed
verification (`dropped`) carries no set and so reads as 'no evidence', although the judge
claimed evidence. The alternative reading counts it positive.
"""
from __future__ import annotations

import itertools

import vcommon as V


def whether(rec, dropped_positive=False) -> bool:
    return V.positive(rec) or (dropped_positive and bool(rec.get("dropped")))


def whether_all_agree(by, k, dropped_positive=False) -> dict:
    keys = sorted(by)
    agree = [len({whether(by[p][j], dropped_positive) for j in range(k)}) == 1 for p in keys]
    combos = list(itertools.combinations(range(k), 2))
    pw = [whether(by[p][a], dropped_positive) == whether(by[p][b], dropped_positive)
          for a, b in combos for p in keys]
    return {"n_pairs": len(keys), "all_agree": sum(agree), "rate": sum(agree) / len(keys),
            "mean_pairwise": sum(pw) / len(pw) if pw else None}


def self_consistency(by, a, b, keys=None) -> dict:
    keys = sorted(by) if keys is None else keys
    ok = [V.consistent_union(by[p][a]["sets"], by[p][b]["sets"]) for p in keys]
    return {"n": len(ok), "consistent": sum(ok), "rate": sum(ok) / len(ok)}


def all_ten(by, n=5) -> dict:
    rates, raw = [], []
    for a, b in itertools.combinations(range(n), 2):
        rates.append(self_consistency(by, a, b)["rate"])
        for p in sorted(by):
            A, B = by[p][a]["sets"], by[p][b]["sets"]
            if A or B:
                raw.append(V.char_jaccard(V.char_spans(A), V.char_spans(B)))
    return {"mean_rate": V.fmean(rates), "min_rate": min(rates), "max_rate": max(rates),
            "partial_credit_jaccard": {"mean": V.fmean(raw), "median": V.median(raw),
                                       "n": len(raw)}}


def flat(acc):
    return [iv for s in acc for iv in s]


def split_half_union(by) -> dict:
    ok, js = [], []
    for p in sorted(by):
        a = flat(V.distinct_sets([by[p][j]["sets"] for j in (0, 1, 2)]))
        b = flat(V.distinct_sets([by[p][j]["sets"] for j in (3, 4)]))
        if not a and not b:
            ok.append(True)
            continue
        if not a or not b:
            ok.append(False)
            continue
        x = V.char_jaccard(a, b)
        js.append(x)
        ok.append(x >= 0.5)
    # sensitivity: union as the raw span union rather than D3-distinct representatives
    ok2 = []
    for p in sorted(by):
        a = [iv for j in (0, 1, 2) for iv in V.char_spans(by[p][j]["sets"])]
        b = [iv for j in (3, 4) for iv in V.char_spans(by[p][j]["sets"])]
        ok2.append(True if not a and not b else
                   (False if not a or not b else V.char_jaccard(a, b) >= 0.5))
    return {"n_pairs": len(ok), "consistent": sum(ok), "rate": sum(ok) / len(ok),
            "mean_jaccard_where_both_positive": V.fmean(js),
            "sensitivity_raw_span_union_rate": sum(ok2) / len(ok2)}


def cross_judge(A, B, depth_a, depth_b) -> dict:
    keys = sorted(set(A) & set(B))
    va = [V.positive(A[k][0]) for k in keys]
    vb = [V.positive(B[k][0]) for k in keys]
    kap = V.cohen_kappa(va, vb)
    both = [k for k in keys if va[keys.index(k)] and vb[keys.index(k)]]
    j0 = [V.char_jaccard(V.char_spans(A[k][0]["sets"]), V.char_spans(B[k][0]["sets"]))
          for k in both]
    ua = {k: flat(V.distinct_sets([A[k][j]["sets"] for j in range(depth_a)])) for k in keys}
    ub = {k: flat(V.distinct_sets([B[k][j]["sets"] for j in range(depth_b)])) for k in keys}
    j5 = [V.char_jaccard(ua[k], ub[k]) for k in keys if ua[k] and ub[k]]
    ra = {k: [iv for j in range(depth_a) for iv in V.char_spans(A[k][j]["sets"])] for k in keys}
    rb = {k: [iv for j in range(depth_b) for iv in V.char_spans(B[k][j]["sets"])] for k in keys}
    j5raw = [V.char_jaccard(ra[k], rb[k]) for k in keys if ra[k] and rb[k]]
    return {"kappa_k0": kap,
            "jaccard_k0": {"n": len(j0), "mean": V.fmean(j0), "median": V.median(j0),
                           "frac_ge_0.5": sum(x >= 0.5 for x in j0) / len(j0)},
            f"jaccard_unions_{depth_a}_{depth_b}": {"n": len(j5), "mean": V.fmean(j5),
                                                    "median": V.median(j5)},
            f"jaccard_raw_span_unions_{depth_a}_{depth_b}": {
                "n": len(j5raw), "mean": V.fmean(j5raw), "median": V.median(j5raw),
                "note": "sensitivity: union of ALL spans rather than of D3-distinct sets"}}


def main() -> None:
    prov = V.provenance()
    res: dict = {}

    # ---- Stage 0 (index-primary, ten-word quotes): 31 duplicated pairs
    s0 = V.read_jsonl(V.F["s0"])
    dup = [r for r in s0 if "dup_sets" in r]
    res["s0.whether_self_agreement"] = {
        "n": len(dup), "agree": sum(bool(r["sets"]) == bool(r["dup_sets"]) for r in dup)}
    res["s0.whether_self_agreement"]["rate"] = (res["s0.whether_self_agreement"]["agree"]
                                                / len(dup))
    sc = [V.consistent_union(r["sets"], r["dup_sets"]) for r in dup]
    res["s0.span_self_consistency"] = {"n": len(sc), "consistent": sum(sc),
                                       "rate": sum(sc) / len(sc)}
    aud = [r for r in s0 if "audit_sets" in r]
    shrank = sum(sum(len(s["spans"]) for s in r["audit_sets"])
                 < sum(len(s["spans"]) for s in r["sets"]) for r in aud)
    res["s0.minimality"] = {"n_audited": len(aud), "shrank": shrank,
                            "rate": shrank / len(aud),
                            "audit_returned_empty": sum(not r["audit_sets"] for r in aud)}
    pos = [r for r in s0 if r["sets"]]
    res["s0.deep_section_pairs"] = {
        "positive_pairs": len(pos),
        "every_span_unit_ge_2": sum(all(sp["unit"] >= 2 for s in r["sets"]
                                        for sp in s["spans"]) for r in pos)}

    # ---- r3 (quote-primary, ten-word anchors): 31 duplicated pairs per judge
    for j in ("scout", "qwen"):
        recs = V.read_jsonl(V.F["r3"][j])
        dup = [r for r in recs if "dup_sets" in r]
        w = [bool(r["sets"]) == bool(r["dup_sets"]) for r in dup]
        sc = [V.consistent_union(r["sets"], r["dup_sets"]) for r in dup]
        res[f"r3.{j}"] = {"n": len(dup), "whether_agree": sum(w),
                          "whether_rate": sum(w) / len(w),
                          "span_consistent": sum(sc), "span_rate": sum(sc) / len(sc)}

    # ---- r3.1 (whole-sentence anchors): five presentations per pair
    r31 = {j: V.load("r31", j) for j in ("scout", "qwen")}
    for j, by in r31.items():
        dup31 = sorted(k for k, v in by.items() if v[0].get("in_501_duplicate_31"))
        res[f"r31.{j}"] = {
            "whether_all5": whether_all_agree(by, 5),
            "whether_all5_dropped_counted_positive": whether_all_agree(by, 5, True),
            "dropped_readings": sum(bool(v[p].get("dropped")) for v in by.values()
                                    for p in range(5)),
            "self_consistency_0v1": self_consistency(by, 0, 1),
            "self_consistency_0v1_on_the_31": self_consistency(by, 0, 1, dup31),
            "all_ten_presentation_pairs": all_ten(by),
            "split_half_union_0to2_vs_3to4": split_half_union(by),
            "positive_rate_k0": sum(V.positive(v[0]) for v in by.values()) / len(by),
        }
    res["r31.cross_judge"] = cross_judge(r31["scout"], r31["qwen"], 5, 5)

    # ---- r3.1 extension: whether curve at 2/5/10/20
    ext = {j: V.load("r31ext", j) for j in ("scout", "qwen")}
    first5_identical = all(
        ext[j][k][p]["raw_response_sha256"] == r31[j][k][p]["raw_response_sha256"]
        for j in ext for k in r31[j] for p in range(5))
    res["r31ext.first_five_identical_to_r31"] = first5_identical
    for j, n in (("scout", 20), ("qwen", 10)):
        res[f"r31ext.{j}.whether_curve"] = {
            str(k): whether_all_agree(ext[j], k)["rate"] for k in (2, 5, 10, 20) if k <= n}
        res[f"r31ext.{j}.whether_curve_dropped_counted_positive"] = {
            str(k): whether_all_agree(ext[j], k, True)["rate"]
            for k in (2, 5, 10, 20) if k <= n}
    res["r31ext.cross_judge_at_depth_10"] = cross_judge(ext["scout"], ext["qwen"], 10, 10)

    # ---- Claude within family: one reading each
    cl = {j: {k: v[0] for k, v in V.load("claude", j).items()}
          for j in ("sonnet5", "opus5", "fable51")}
    for a, b in (("sonnet5", "opus5"), ("sonnet5", "fable51"), ("opus5", "fable51")):
        keys = sorted(set(cl[a]) & set(cl[b]))
        va = [V.positive(cl[a][k]) for k in keys]
        vb = [V.positive(cl[b][k]) for k in keys]
        js = [V.char_jaccard(V.char_spans(cl[a][k]["sets"]), V.char_spans(cl[b][k]["sets"]))
              for k in keys if V.positive(cl[a][k]) and V.positive(cl[b][k])]
        res[f"claude.{a}_vs_{b}"] = {"co_labeled": len(keys),
                                     "kappa": V.cohen_kappa(va, vb),
                                     "jaccard": {"n": len(js), "mean": V.fmean(js),
                                                 "median": V.median(js)}}
    V.write("recompute_agreement", prov, {"results": res})


if __name__ == "__main__":
    main()
