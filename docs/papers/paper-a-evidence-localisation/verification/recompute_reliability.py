#!/usr/bin/env python3
"""Graded-support split-half reliability with Spearman-Brown, pooled and per judge; the
judge-judge support correlation; and the span-filter dev delta, recomputed.

Estimator (PREDICTIONS.md P-ext-1, DRAFT §3.4), implemented from the text:
  per pair, the first n readings (pooled order s0,q0,s1,q1,...,s9,q9,s10..s19; per judge,
  that judge's readings in presentation order); universe = sentences selected by >= 1 of
  them; split the n readings at random into halves of n/2; each sentence's support in a half
  = fraction of that half's readings selecting it; Pearson r across the pair's sentences;
  mean over pairs; repeat for 20 seeded splits and average; Spearman-Brown doubling.

Choices the text leaves open, and the reading taken here (which is also the harness's):
  (1) a pair whose universe has < 2 sentences contributes nothing;
  (2) a pair with no variance in either half (r undefined) is dropped from that split's mean
      and counted;
  (3) the seed is 20260914 + n, one random.Random stream per n, consumed pair by pair in
      sorted (topic, docno) order, one shuffle of range(n) per pair with a non-empty universe
      (the shuffle happens before the < 2 check); halves are the first and last n/2 of the
      shuffled order.
Alternatives (Fisher-z averaging, undefined r counted as 0, other seeds) are reported as
sensitivities, so the reader can see how much each choice moves the number.
"""
from __future__ import annotations

import os
import random

import numpy as np

import vcommon as V

NS = (4, 6, 8, 10, 14, 20, 30)
SEED = 20260914
DRAWS = 20
NSEEDS = int(os.environ.get("PAPER_A_SEEDS", "100"))


def membership(readings) -> np.ndarray | None:
    uni = sorted(set().union(*readings))
    if not uni:
        return None
    col = {s: i for i, s in enumerate(uni)}
    M = np.zeros((len(readings), len(uni)), dtype=np.float64)
    for r, R in enumerate(readings):
        for s in R:
            M[r, col[s]] = 1.0
    return M


def top3(a, b):
    """Overlap of the two halves' top-3 sentences; ties go to the earlier sentence."""
    m = min(3, len(a))
    ta = set(sorted(range(len(a)), key=lambda i: (-a[i], i))[:m])
    tb = set(sorted(range(len(b)), key=lambda i: (-b[i], i))[:m])
    return len(ta & tb) / m


def split_half(per_pair: dict, n: int, seed: int | None = None, draws: int = DRAWS,
               exclude=frozenset(), min_sentences: int = 2, fisher=False,
               undefined_as_zero=False, float_test=False) -> dict:
    """float_test=True reproduces a pure-Python float semantics for the no-variance test:
    support fractions are Python floats, the mean is sum/len, and a half counts as having no
    variance only if sqrt(sum((x - mean)^2)) is exactly 0.0. A half whose values are all
    equal to e.g. 1/5 can then show ~1e-17 spread and yield an r from rounding noise. This is
    NOT the stated definition; it is computed to explain the committed values (see README)."""
    rng = random.Random(SEED + n if seed is None else seed)
    mats = {k: (membership(R[:n]) if k not in exclude else None)
            for k, R in sorted(per_pair.items())}
    per_draw_r, per_draw_t, novar = [], [], 0
    for _ in range(draws):
        rs, ts = [], []
        for k, M in mats.items():
            if M is None:
                continue
            idx = list(range(n))
            rng.shuffle(idx)
            if M.shape[1] < min_sentences:
                continue
            # integer vote counts per half: the no-variance test is then exact (a float
            # mean of identical fractions can carry ~1e-17 spread and pass a std() == 0 test)
            ca = M[idx[: n // 2]].sum(axis=0)
            cb = M[idx[n // 2:]].sum(axis=0)
            a, b = ca / (n // 2), cb / (n - n // 2)
            if float_test:
                la = [sum(1 for i in idx[: n // 2] if M[i, c]) / len(idx[: n // 2])
                      for c in range(M.shape[1])]
                lb = [sum(1 for i in idx[n // 2:] if M[i, c]) / len(idx[n // 2:])
                      for c in range(M.shape[1])]
                rr = V.pearson_float(la, lb)
                if rr is None:
                    novar += 1
                else:
                    rs.append(rr)
                ts.append(top3(la, lb))
                continue
            if ca.min() == ca.max() or cb.min() == cb.max():
                novar += 1
                if undefined_as_zero:
                    rs.append(0.0)
            else:
                r = float(np.corrcoef(a, b)[0, 1])
                rs.append(float(np.arctanh(max(-0.999999, min(r, 0.999999)))) if fisher else r)
            ts.append(top3(list(a), list(b)))
        m = float(np.mean(rs))
        per_draw_r.append(float(np.tanh(m)) if fisher else m)
        per_draw_t.append(float(np.mean(ts)))
    r_half = float(np.mean(per_draw_r))
    return {"n_readings": n, "split_half_r": r_half,
            "spearman_brown_full_n": V.spearman_brown(r_half),
            "split_half_r_sd_over_draws": float(np.std(per_draw_r)),
            "top3_overlap_between_halves": float(np.mean(per_draw_t)),
            "pairs_without_variance_in_a_half": novar // draws,
            "pairs_without_variance_total_over_draws": novar}


def main() -> None:
    prov = V.provenance()
    ext = {j: V.load("r31ext", j) for j in ("scout", "qwen")}
    depth = {"scout": 20, "qwen": 10}
    R = {j: {k: [V.sentence_set(v[p]) for p in range(depth[j])] for k, v in ext[j].items()}
         for j in ext}
    keys = sorted(set(R["scout"]) & set(R["qwen"]))
    pooled = {k: [x for pr in zip(R["scout"][k][:10], R["qwen"][k]) for x in pr]
              + R["scout"][k][10:] for k in keys}
    blow = frozenset({("2016_1", "4212306")})       # identified in recompute_unions.py

    res: dict = {"pooled": [split_half(pooled, n) for n in NS],
                 "scout": [split_half(R["scout"], n) for n in NS if n <= 20],
                 "qwen": [split_half(R["qwen"], n) for n in NS if n <= 10]}
    res["float_test"] = {
        "pooled": [split_half(pooled, n, float_test=True) for n in NS],
        "scout": [split_half(R["scout"], n, float_test=True) for n in NS if n <= 20],
        "qwen": [split_half(R["qwen"], n, float_test=True) for n in NS if n <= 10]}
    res["pooled_30_excluding_blowup"] = split_half(pooled, 30, exclude=blow)
    res["pooled_30_min3_sentences"] = split_half(pooled, 30, min_sentences=3)
    res["pooled_30_fisher_z_average"] = split_half(pooled, 30, fisher=True)
    res["pooled_30_undefined_r_as_zero"] = split_half(pooled, 30, undefined_as_zero=True)

    # implied single-reading reliability (Spearman-Brown inverted)
    res["implied_single_reading"] = {
        name: {str(x["n_readings"]): V.sb_invert(x["spearman_brown_full_n"],
                                                 x["n_readings"])
               for x in res[name] if x["n_readings"] >= 10}
        for name in ("pooled", "scout", "qwen")}

    # seed sensitivity: 100 alternative seeds, 20 draws each, at every k
    sens = {}
    for name, data, ns in (("pooled", pooled, NS), ("scout", R["scout"], (10, 14, 20)),
                           ("qwen", R["qwen"], (10,))):
        for n in ns:
            if name == "pooled" or n >= 10:
                sb = [split_half(data, n, seed=1_000_003 * (i + 1) + n)["spearman_brown_full_n"]
                      for i in range(NSEEDS)]
                sens[f"{name}.{n}"] = {"seeds": NSEEDS, "mean": float(np.mean(sb)),
                                       "sd": float(np.std(sb)), "min": float(np.min(sb)),
                                       "max": float(np.max(sb))}
    res["seed_sensitivity_spearman_brown"] = sens

    # judge-judge graded support correlation, each judge at full depth
    rs, t3, px, py = [], [], [], []
    px_nb, py_nb = [], []
    for k in keys:
        uni = sorted(set().union(*R["scout"][k]) | set().union(*R["qwen"][k]))
        if not uni:
            continue
        a = [sum(s in r for r in R["scout"][k]) / 20 for s in uni]
        b = [sum(s in r for r in R["qwen"][k]) / 10 for s in uni]
        px += a
        py += b
        r = V.pearson(a, b)
        if r is not None:
            rs.append(r)
        t3.append(top3(a, b))
        if k not in blow:
            px_nb += a
            py_nb += b
    res["judge_judge"] = {"n_pairs": len(rs), "mean_r": V.fmean(rs), "median_r": V.median(rs),
                          "frac_pairs_r_ge_0.5": sum(x >= 0.5 for x in rs) / len(rs),
                          "pooled_r": V.pearson(px, py), "n_sentences_pooled": len(px),
                          "top3_overlap": V.fmean(t3),
                          "pooled_r_excluding_blowup_pair": V.pearson(px_nb, py_nb),
                          "n_sentences_excluding_blowup_pair": len(px_nb)}

    # span-filter dev verification: the same estimator on the post-filtered DEV labels
    fd = {}
    for j in ("scout", "qwen"):
        p = V.FILTERED_DEV / f"labels-r31-{j}.jsonl"
        fd[j] = V.by_pair_presentation(V.read_jsonl(p)) if p.exists() else None
    if all(fd.values()):
        F = {j: {k: [V.sentence_set(v[p]) for p in range(depth[j])] for k, v in fd[j].items()}
             for j in fd}
        fpooled = {k: [x for pr in zip(F["scout"][k][:10], F["qwen"][k]) for x in pr]
                   + F["scout"][k][10:] for k in keys}
        filt = split_half(fpooled, 30)
        counts = {}
        for j in ("scout", "qwen"):
            changed = spans = sents = 0
            for k, v in ext[j].items():
                for p in range(depth[j]):
                    a, b = v[p], fd[j][k][p]
                    na = sum(len(s["spans"]) for s in a["sets"])
                    nb = sum(len(s["spans"]) for s in b["sets"])
                    if (na, V.sentence_set(a)) != (nb, V.sentence_set(b)):
                        changed += 1
                    spans += na - nb
                    sents += len(V.sentence_set(a)) - len(V.sentence_set(b))
            book = [fd[j][k][p].get("span_filter") or {} for k in fd[j] for p in range(depth[j])]
            counts[j] = {"records": len(ext[j]) * depth[j], "records_changed": changed,
                         "filter_bookkeeping": {
                             "records_applied": sum(bool(b.get("applied")) for b in book),
                             "spans_dropped": sum(b.get("spans_dropped", 0) for b in book),
                             "sentences_dropped": sum(b.get("sentences_dropped", 0)
                                                      for b in book),
                             "records_flagged_blowup": sum(bool(b.get("blowup"))
                                                           for b in book)},
                         "spans_removed": spans, "distinct_sentences_removed": sents}
        res["span_filter_dev"] = {
            "unfiltered_sb30": res["pooled"][-1]["spearman_brown_full_n"],
            "filtered_split_half_r": filt["split_half_r"],
            "filtered_sb30": filt["spearman_brown_full_n"],
            "delta": filt["spearman_brown_full_n"] - res["pooled"][-1]["spearman_brown_full_n"],
            "counts": counts, "source": V.portable(V.FILTERED_DEV)}
    else:
        res["span_filter_dev"] = {"ABSENT": f"{V.FILTERED_DEV} not on disk"}

    V.write("recompute_reliability", prov, {"results": res})


if __name__ == "__main__":
    main()
