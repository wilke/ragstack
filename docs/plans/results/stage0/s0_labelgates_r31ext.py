#!/usr/bin/env python3
"""r3.1 EXTENSION gate reader -- Scout x20, Qwen x10, on the same 308 development pairs.

What this adds to ``s0_labelgates_r31.py``, which it imports rather than copies (the r3.1
gate arithmetic is not re-implemented here, so a number that appears in both documents is
the same code path):

1. **The gate table at depth.** Every r3.1 gate re-read over ALL readings -- self-consistency
   over all C(k,2) presentation pairs, whether-agreement over all k, the hallucinated-span
   rate all-in -- beside the unchanged k = 0 reading.
2. **Saturation in sentences, not only in D3-distinct sets.** The unit of the union is the
   evidence SENTENCE (a span carries ``unit`` and ``first_sentence``/``last_sentence`` as
   indices into the segmentation, so a sentence is identified as ``(unit, index)`` without
   re-segmenting anything). Curves for scout k = 1..20, qwen k = 1..10 and the interleaved
   pool k = 1..30, with a Michaelis-Menten fit refit on all points.
3. **The reliability of GRADED per-sentence support** -- the fraction of readings that
   include a sentence -- as a function of how many readings you buy. Split-half r at
   n in {4,6,8,10,14,20,30} with the Spearman-Brown full-n reliability beside it, plus the
   top-3 overlap and the share of votes the top 3 carry. This is the number r3 SS10 item 4 (a)
   turns on, and it is measured here rather than assumed.
4. **Cross-judge support correlation** -- do the two judges agree about WHICH sentences are
   most supported, even though single readings disagree about where the span is?

Everything is scored against ``artifacts/r31ext/PREDICTIONS.md``, which was committed before
the first new label was generated (P-ext-1/2/3).

No endpoint is contacted and no store client is constructed. Reads the label files under
``$STAGE0_BIG/work/r31ext/`` (and, for ``--prefit``, the committed ``artifacts/r31/``); writes
only to ``artifacts/r31ext/`` and ``$STAGE0_BIG/work/r31ext/``.

Absent statistics are called absent, never null: no human read was performed, so no kappa
against a human and no enumeration recall appears anywhere below.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import pathlib
import random
import statistics
import sys

_HELPERS = pathlib.Path(os.environ.get(
    "STAGE0_HELPERS", "/home/wilke/Development/worktrees/phase0-rescue/phase0"))
for _p in (_HELPERS / "stage1", _HELPERS / "pilots"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))

import s0_common as C                                      # noqa: E402
import s0_labelgates_r31 as G                              # noqa: E402
from s0_score import jaccard                               # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
EXT = C.WORK / "r31ext"
ART = HERE / "artifacts" / "r31ext"
ART31 = HERE / "artifacts" / "r31"

JUDGES = ("scout", "qwen")
N_SCOUT = 20
N_QWEN = 10
N_POOLED = N_SCOUT + N_QWEN

GATE_RELIABILITY = 0.90     # r3 SS3.7's labeler bar, read on graded support (SS10 item 4 (a))
RELIABILITY_NS = (4, 6, 8, 10, 14, 20, 30)
N_DRAWS = 20                # random half-splits per n
SEED_ANALYSIS = 20260914    # C.SEED_LABELDUP; the analysis draws are seeded too

# A LOCATOR BLOW-UP, defined by mechanism and not by size. When a quoted span's first and
# last sentence locate in DIFFERENT units, the r3.1 locator emits one span per intervening
# unit covering that unit whole; a single model span can therefore become hundreds of
# emitted spans covering most of the document. ``vstats`` records both counts, so the
# pathology is identified from the record's own bookkeeping rather than from a threshold on
# the answer's size: a reading is a blow-up when it emitted at least this many spans per
# span the model actually offered. In these 9,240 readings exactly ONE crosses it (Scout,
# 2016_1 / 4212306, presentation 11: 22 seen -> 986 emitted -> 22,366 distinct sentences).
# The next-largest reading in the corpus selects 520 sentences; the median selects 6.
OUTLIER_EMITTED_RATIO = 10
KM_SEARCH_HI = 500.0        # the Km grid ceiling in mm_fit; a fit that reaches it is linear


# ------------------------------------------------------------------ sentences
def sentences(rec) -> set[tuple[int, int]]:
    """The set of evidence SENTENCES a reading selects, as ``(unit, sentence_index)``.

    A span is contiguous whole sentences inside exactly one unit (D1), and the label record
    carries the unit index and the first/last sentence indices of every span, so the sentence
    identity is read straight off the record. No document is re-segmented and no character
    offset is re-derived.
    """
    out: set[tuple[int, int]] = set()
    for s in rec["sets"]:
        for sp in s["spans"]:
            for i in range(sp["first_sentence"], sp["last_sentence"] + 1):
                out.add((sp["unit"], i))
    return out


def readings_for(by, key, order) -> list[set[tuple[int, int]]]:
    return [sentences(by[key][k]) for k in order]


def blowups(by, keys, n: int) -> dict:
    """Readings where the locator emitted far more spans than the model offered.

    Identified from ``vstats`` -- ``spans_emitted`` against ``spans_seen`` -- so the
    criterion is the mechanism (a span split across units, expanded to cover every
    intervening unit whole), not a threshold chosen on the answer's size after seeing it.
    Returns the offending readings and the set of PAIRS they touch, which the sensitivity
    curves drop.
    """
    hits = []
    for k in keys:
        for j in range(n):
            v = by[k][j].get("vstats") or {}
            seen, emitted = v.get("spans_seen", 0), v.get("spans_emitted", 0)
            if emitted >= OUTLIER_EMITTED_RATIO * max(seen, 1):
                hits.append({"topic": k[0], "docno": k[1], "presentation": j,
                             "spans_seen": seen, "spans_emitted": emitted,
                             "distinct_sentences": len(sentences(by[k][j])),
                             "split_across_units": v.get("split_across_units", 0)})
    return {"criterion": f"spans_emitted >= {OUTLIER_EMITTED_RATIO} * max(spans_seen, 1) "
                         f"in a single reading",
            "n_readings_flagged": len(hits), "n_readings_examined": len(keys) * n,
            "readings": hits,
            "pairs": sorted({(h["topic"], h["docno"]) for h in hits})}


# ------------------------------------------------------------------ curve fitting
def mm_fit(ks, us) -> dict:
    """Michaelis-Menten least squares: U(k) = Vmax * k / (Km + k).

    Vmax is linear given Km, so the fit is a 1-D search over Km (grid then refinement) with
    Vmax solved in closed form at each step -- deterministic, and no scipy on this host.
    """
    ks = [float(k) for k in ks]
    us = [float(u) for u in us]

    def sse_at(km):
        x = [k / (km + k) for k in ks]
        den = sum(v * v for v in x)
        if den <= 0:
            return float("inf"), 0.0
        v = sum(u * xi for u, xi in zip(us, x)) / den
        return sum((u - v * xi) ** 2 for u, xi in zip(us, x)), v

    best = (float("inf"), 0.0, 0.0)
    lo, hi, step = 1e-4, KM_SEARCH_HI, 0.01
    for _ in range(4):                      # coarse-to-fine, 4 refinements
        km = lo
        cur = (float("inf"), 0.0, 0.0)
        while km <= hi:
            s, v = sse_at(km)
            if s < cur[0]:
                cur = (s, v, km)
            km += step
        best = cur if cur[0] < best[0] else best
        lo, hi = max(1e-4, best[2] - step * 5), best[2] + step * 5
        step /= 50.0
    sse, vmax, km = best
    mu = statistics.fmean(us)
    sst = sum((u - mu) ** 2 for u in us)
    out = {"Vmax": round(vmax, 4), "Km": round(km, 4),
           "sse": round(sse, 6), "r2": round(1 - sse / sst, 6) if sst else None,
           "n_points": len(ks), "k_fitted": ks,
           "form": "U(k) = Vmax * k / (Km + k)"}
    # A Km that runs to the search ceiling is not a saturating fit that happens to be wide:
    # it is the optimiser reporting that the least-squares optimum is Km -> infinity, where
    # Michaelis-Menten degenerates to the straight line U(k) = (Vmax/Km)*k. Vmax and Km are
    # then both meaningless individually -- only their ratio is identified -- so an
    # "asymptote" read off such a fit is an artefact of where the grid stopped.
    if km >= 0.98 * KM_SEARCH_HI:
        out["DEGENERATE"] = True
        out["degenerate_note"] = (
            f"Km reached the search ceiling ({KM_SEARCH_HI}): the least-squares optimum is "
            f"Km -> infinity, i.e. the curve is LINEAR over the range fitted and has no "
            f"identifiable asymptote. Vmax = {round(vmax, 1)} and Km = {round(km, 1)} are "
            f"artefacts of the grid; only the slope Vmax/Km = "
            f"{round(vmax / km, 4) if km else None} sentences per reading is identified. "
            f"Do not quote this Vmax as an asymptote.")
    else:
        out["DEGENERATE"] = False
    return out


def linear_fit(ks, us) -> dict:
    """Ordinary least squares U(k) = a*k + b -- the null model saturation must beat."""
    ks = [float(k) for k in ks]
    us = [float(u) for u in us]
    n = len(ks)
    mk, mu = sum(ks) / n, sum(us) / n
    den = sum((k - mk) ** 2 for k in ks)
    a = sum((k - mk) * (u - mu) for k, u in zip(ks, us)) / den if den else 0.0
    b = mu - a * mk
    sse = sum((u - (a * k + b)) ** 2 for k, u in zip(ks, us))
    sst = sum((u - mu) ** 2 for u in us)
    return {"slope_per_reading": round(a, 4), "intercept": round(b, 4),
            "sse": round(sse, 6), "r2": round(1 - sse / sst, 6) if sst else None,
            "form": "U(k) = a*k + b",
            "note": "the no-saturation null; if it fits as well as Michaelis-Menten over "
                    "the range measured, the data do not show saturation"}


def mm_at(fit, k):
    return fit["Vmax"] * k / (fit["Km"] + k)


# ------------------------------------------------------------------ correlation
def pearson(x, y):
    n = len(x)
    if n < 2:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((b - my) ** 2 for b in y))
    if sx == 0 or sy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy)


def spearman_brown(r, m):
    """Reliability of an instrument m times as long as the one measured at r."""
    if r is None:
        return None
    d = 1 + (m - 1) * r
    return None if d == 0 else m * r / d


# ------------------------------------------------------------------ graded support
def support_reliability(per_pair_readings: dict, n: int, draws: int = N_DRAWS,
                        seed: int = SEED_ANALYSIS, exclude: frozenset = frozenset(),
                        min_sentences: int = 2) -> dict:
    """Split-half reliability of graded per-sentence support from ``n`` readings.

    For each pair: take the first ``n`` readings in the pooled interleaved order, split them
    at random into two halves of n/2, score every sentence in the union of the n readings by
    the fraction of each half that includes it, and correlate the two halves ACROSS THE
    PAIR'S SENTENCES. The reported r is the mean over pairs (a pair whose union has fewer
    than two sentences, or no variance in either half, contributes nothing and is counted);
    Spearman-Brown doubles it to the reliability of the full n-reading instrument.

    Also reported: the overlap of the two halves' TOP-3 supported sentences (|A n B| / 3,
    ties broken by sentence order, pairs with < 3 sentences use |union|), which is what a
    graded gold actually gets used for.
    """
    rng = random.Random(seed + n)
    rs, tops, novar, small = [], [], 0, 0
    for _ in range(draws):
        per_r, per_t = [], []
        for key, R in per_pair_readings.items():
            if key in exclude:
                continue
            R = R[:n]
            uni = sorted(set().union(*R)) if R else []
            if not uni:
                continue
            idx = list(range(n))
            rng.shuffle(idx)
            # AFTER the shuffle, so that raising min_sentences does not perturb the random
            # stream and the pre-registered reading (min_sentences = 2) stays bit-identical
            # to the number `--prefit` published before the run.
            if len(uni) < min_sentences:
                continue
            A, B = idx[:n // 2], idx[n // 2:]
            a = [sum(1 for i in A if s in R[i]) / len(A) for s in uni]
            b = [sum(1 for i in B if s in R[i]) / len(B) for s in uni]
            if len(uni) < 2:
                small += 1
            r = pearson(a, b)
            if r is None:
                novar += 1
            else:
                per_r.append(r)
            m = min(3, len(uni))
            ta = {s for _v, s in sorted(zip(a, uni), key=lambda t: (-t[0], t[1]))[:m]}
            tb = {s for _v, s in sorted(zip(b, uni), key=lambda t: (-t[0], t[1]))[:m]}
            per_t.append(len(ta & tb) / m)
        rs.append(statistics.fmean(per_r) if per_r else None)
        tops.append(statistics.fmean(per_t) if per_t else None)
    rr = [x for x in rs if x is not None]
    tt = [x for x in tops if x is not None]
    r_half = statistics.fmean(rr) if rr else None
    return {"n_readings": n, "draws": draws,
            "min_sentences_in_union": min_sentences,
            "pairs_excluded": sorted(exclude),
            "split_half_r": round(r_half, 4) if r_half is not None else None,
            "split_half_r_sd_over_draws": (round(statistics.pstdev(rr), 4)
                                           if len(rr) > 1 else None),
            "spearman_brown_full_n": (round(spearman_brown(r_half, 2), 4)
                                      if r_half is not None else None),
            "top3_overlap_between_halves": round(statistics.fmean(tt), 4) if tt else None,
            "pairs_without_variance_in_a_half": novar // draws,
            "pairs_with_one_sentence": small // draws}


def support_shape(per_pair_readings: dict, n: int) -> dict:
    """Where the support mass sits: the share of all votes carried by a pair's top-3."""
    shares, sizes, tops = [], [], []
    for key, R in per_pair_readings.items():
        R = R[:n]
        uni = sorted(set().union(*R)) if R else []
        if not uni:
            continue
        votes = {s: sum(1 for r in R if s in r) for s in uni}
        tot = sum(votes.values())
        top = sorted(votes.values(), reverse=True)[:3]
        shares.append(sum(top) / tot if tot else None)
        sizes.append(len(uni))
        tops.append(max(votes.values()) / n)
    shares = [x for x in shares if x is not None]
    return {"n_readings": n, "n_pairs": len(sizes),
            "mean_union_sentences": round(statistics.fmean(sizes), 4) if sizes else None,
            "median_union_sentences": (round(statistics.median(sizes), 4)
                                       if sizes else None),
            "share_of_votes_on_top3": round(statistics.fmean(shares), 4) if shares else None,
            "mean_max_support": round(statistics.fmean(tops), 4) if tops else None,
            "definition": "votes = number of readings that include a sentence; the top-3 "
                          "share is per pair and averaged over pairs"}


# ------------------------------------------------------------------ saturation
def sentence_saturation(per_pair_readings: dict, n: int, label: str,
                        exclude: frozenset = frozenset()) -> dict:
    """Mean distinct evidence SENTENCES in the union of the first k readings, k = 1..n.

    The MEAN over pairs positive somewhere is the pre-registered estimator and is what
    P-ext-2 is scored on. It is also, being a mean of a heavy-tailed count, sensitive to a
    single locator blow-up, so the MEDIAN curve is reported beside it and ``exclude`` lets a
    caller re-read the same curve without the pairs ``blowups()`` flagged.
    """
    keys = [k for k, R in per_pair_readings.items()
            if any(R[:n]) and k not in exclude]
    means, allmeans, medians = [], [], []
    for m in range(1, n + 1):
        vals = [len(set().union(*per_pair_readings[k][:m])) for k in keys]
        means.append(round(statistics.fmean(vals), 4) if vals else None)
        medians.append(round(statistics.median(vals), 4) if vals else None)
        av = [len(set().union(*R[:m])) for k, R in per_pair_readings.items()
              if k not in exclude]
        allmeans.append(round(statistics.fmean(av), 4) if av else None)
    gains = [None] + [round((means[i] - means[i - 1]) / means[i], 4) if means[i] else None
                      for i in range(1, n)]
    fit = mm_fit(list(range(1, n + 1)), means)
    lin = linear_fit(list(range(1, n + 1)), means)
    out = {"label": label, "n_pairs_positive_somewhere": len(keys),
           "n_pairs_all": len(per_pair_readings) - len(exclude),
           "pairs_excluded": sorted(exclude),
           "mean_distinct_sentences_at_k": {str(i + 1): means[i] for i in range(n)},
           "median_distinct_sentences_at_k": {str(i + 1): medians[i] for i in range(n)},
           "mean_over_all_pairs_at_k": {str(i + 1): allmeans[i] for i in range(n)},
           "marginal_gain_at_k": {str(i + 1): gains[i] for i in range(n)},
           "marginal_gain_at_n": gains[-1] if n > 1 else None,
           "mm_fit": fit, "linear_fit": lin,
           "mm_fit_projection": {str(k): round(mm_at(fit, k), 4)
                                 for k in (n, 20, 30, 50, 100)},
           "frac_of_asymptote_at_n": round(n / (fit["Km"] + n), 4),
           "unit": "distinct evidence sentences, identified as (unit, sentence index); "
                   "no merge rule is applied -- a sentence is either selected or not"}
    if fit.get("DEGENERATE"):
        out["frac_of_asymptote_at_n"] = None
        out["frac_of_asymptote_note"] = (
            "ABSENT, not zero: the fit is degenerate (Km at the search ceiling), so there "
            "is no identifiable asymptote to take a fraction of. See mm_fit.")
    return out


def set_saturation(by, keys, n: int, label: str) -> dict:
    """D3-distinct SETS, accumulated incrementally (the r3.1 rule, one reading at a time)."""
    accs = {k: [] for k in keys}
    lens = {k: [] for k in keys}
    for m in range(n):
        for k in keys:
            for s in by[k][m]["sets"]:
                iv = G.spans_of(s)
                if all(jaccard(iv, a) < C.JACCARD_MERGE for a in accs[k]):
                    accs[k].append(iv)
            lens[k].append(len(accs[k]))
    pos = [k for k in keys if lens[k][-1]]          # positive somewhere in the n readings,
    means, gains = [], [None]                       # FIXED across k, as r3.1's curve was
    for m in range(n):
        means.append(round(statistics.fmean([lens[k][m] for k in pos]), 4) if pos else None)
        if m:
            gains.append(round((means[m] - means[m - 1]) / means[m], 4) if means[m] else None)
    fit = mm_fit(list(range(1, n + 1)), means)
    return {"label": label,
            "mean_distinct_sets_at_k": {str(i + 1): means[i] for i in range(n)},
            "marginal_gain_at_k": {str(i + 1): gains[i] for i in range(n)},
            "marginal_gain_at_n": gains[-1] if n > 1 else None,
            "mm_fit": fit,
            "mm_fit_projection": {str(k): round(mm_at(fit, k), 4) for k in (n, 30, 100)},
            "distinct_rule": f"D3 rule 1 -- two sets merge iff span-union Jaccard >= "
                             f"{C.JACCARD_MERGE}; accumulated in reading order",
            "n_pairs_positive_somewhere": len(pos),
            "denominator": "pairs positive somewhere in the n readings, fixed across k — "
                           "the denominator r3.1's own curve used, so k = 1..5 reproduces "
                           "artifacts/r31/gates-r31.json exactly"}


# ------------------------------------------------------------------ pre-registration
def prefit() -> dict:
    """The P-ext-2 fit, computed from the COMMITTED r3.1 labels only. Run before the run."""
    by = {}
    for j in JUDGES:
        b = {}
        for line in (ART31 / f"labels-r31-{j}.jsonl").read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                b.setdefault((r["topic"], r["docno"]), {})[r["presentation"]] = r
        by[j] = b
    keys = sorted(set(by["scout"]) & set(by["qwen"]))
    out = {"source": str(ART31), "n_pairs": len(keys), "k_available": 5}
    for j in JUDGES:
        R = {k: readings_for(by[j], k, range(5)) for k in keys}
        out[j] = sentence_saturation(R, 5, f"{j} (r3.1, k = 1..5)")
    pooled = {k: [x for pair in zip(readings_for(by["scout"], k, range(5)),
                                    readings_for(by["qwen"], k, range(5)))
                  for x in pair] for k in keys}
    out["pooled_interleaved"] = sentence_saturation(pooled, 10, "pooled s0,q0,s1,q1,...")
    out["reliability_at_10"] = support_reliability(pooled, 10)
    out["support_shape_at_10"] = support_shape(pooled, 10)
    f = out["scout"]["mm_fit"]
    out["P_ext_2_projection"] = {
        "fit_on": "scout, distinct evidence sentences, k = 1..5",
        "Vmax": f["Vmax"], "Km": f["Km"], "r2": f["r2"],
        "projected_U20": round(mm_at(f, 20), 4),
        "projected_U20_as_fraction_of_Vmax": round(20 / (f["Km"] + 20), 4),
        "within_10pct_of_asymptote_requires_U20_at_least": round(0.90 * f["Vmax"], 4)}
    return out


# ------------------------------------------------------------------ main analysis
def load_ext(judge: str, tag: str = "") -> dict:
    p = EXT / f"labels-r31-{judge}{('-' + tag) if tag else ''}.jsonl"
    by: dict = {}
    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        by.setdefault((r["topic"], r["docno"]), {})[r["presentation"]] = r
    bad = {t for t, _d in by} - set(C.DEV_TOPICS)
    assert not bad, f"{p} contains non-development topics: {sorted(bad)}"
    return by


def whether_curve(by, keys, n: int) -> dict:
    """Whether-agreement as a function of how many readings must agree."""
    out = {}
    for m in (2, 5, 10, n):
        if m > n:
            continue
        agree = [len({bool(by[k][j]["sets"]) for j in range(m)}) == 1 for k in keys]
        out[str(m)] = round(sum(agree) / len(agree), 4) if agree else None
    return out


def hallucination_by_block(by, keys, n: int) -> dict:
    """P-ext-3: the rate at the NEW presentations, beside the old ones."""
    def block(rng):
        seen = fail = 0
        for k in keys:
            for j in rng:
                v = by[k][j].get("vstats") or {}
                seen += v.get("spans_seen", 0)
                fail += v.get("hallucinated", 0)
        lo, up = (0.0, 1.0) if not seen else __import__("s0_math").wilson(fail, seen)
        return {"failed": fail, "attempted": seen,
                "rate": round(fail / seen, 5) if seen else None,
                "wilson95": [round(lo, 5), round(up, 5)]}
    return {"k_0_to_4_r31": block(range(5)), "k_5_and_up_new": block(range(5, n)),
            "all": block(range(n))}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefit", action="store_true",
                    help="the P-ext-2 fit from the committed r3.1 labels; writes nothing")
    ap.add_argument("--tag", default="")
    ap.add_argument("--scout", type=int, default=N_SCOUT)
    ap.add_argument("--qwen", type=int, default=N_QWEN)
    ap.add_argument("--outdir", default=str(ART))
    ap.add_argument("--workdir", default="r31ext",
                    help="directory holding the label files: a subdirectory of "
                         "$STAGE0_BIG/work, or an absolute path (used for offline dry runs "
                         "so that nothing under $STAGE0_BIG outside work/r31ext is touched)")
    args = ap.parse_args()
    global EXT
    EXT = (pathlib.Path(args.workdir) if os.path.isabs(args.workdir)
           else C.WORK / args.workdir)

    if args.prefit:
        print(json.dumps(prefit(), indent=1))
        return

    ns = {"scout": args.scout, "qwen": args.qwen}
    loaded = {j: load_ext(j, args.tag) for j in JUDGES}
    blow = {j: blowups(loaded[j], G.complete(loaded[j], ns[j]), ns[j]) for j in JUDGES}
    blow_pairs = frozenset().union(*[frozenset(map(tuple, blow[j]["pairs"]))
                                     for j in JUDGES])
    judges: dict = {}
    for j in JUDGES:
        by, n = loaded[j], ns[j]
        keys = G.complete(by, n)
        mp = EXT / f"label-manifest-r31-{j}{('-' + args.tag) if args.tag else ''}.json"
        man = json.loads(mp.read_text()) if mp.exists() else None
        # r3.1's own gate arithmetic, unchanged, at k = 5 (the published reading) ...
        d5 = G.per_judge(j, by, man, 5)
        # ... and at this run's depth.
        d = G.per_judge(j, by, man, n)
        d["r31_reading_at_k5"] = {
            "self_consistency": d5["self_consistency"]["rate"],
            "hallucinated_span_rate": d5["hallucinated_span_rate"]["rate"],
            "whether_agreement": d5["whether_agreement"]["rate"],
            "ALL_THREE_GATES_PASS": d5["ALL_THREE_GATES_PASS"],
            "note": "the committed r3.1 numbers, recomputed from this file's first five "
                    "presentations; they must reproduce artifacts/r31/gates-r31.json"}
        R = {k: readings_for(by, k, range(n)) for k in keys}
        d["sentence_saturation"] = sentence_saturation(R, n, f"{j} k = 1..{n}")
        d["locator_blowups"] = blow[j]
        d["sentence_saturation_excluding_blowups"] = sentence_saturation(
            R, n, f"{j} k = 1..{n}, blow-up pairs dropped", exclude=blow_pairs)
        d["set_saturation_incremental"] = set_saturation(by, keys, n, f"{j} k = 1..{n}")
        d["whether_agreement_curve"] = whether_curve(by, keys, n)
        d["hallucination_by_block"] = hallucination_by_block(by, keys, n)
        d["support_reliability"] = [support_reliability(R, m)
                                    for m in RELIABILITY_NS if m <= n]
        d["support_shape"] = support_shape(R, n)
        judges[j] = d

    keys = sorted(set(G.complete(loaded["scout"], args.scout))
                  & set(G.complete(loaded["qwen"], args.qwen)))
    Rs = {k: readings_for(loaded["scout"], k, range(args.scout)) for k in keys}
    Rq = {k: readings_for(loaded["qwen"], k, range(args.qwen)) for k in keys}
    pooled = {}
    for k in keys:
        inter = [x for pair in zip(Rs[k][:args.qwen], Rq[k]) for x in pair]
        pooled[k] = inter + Rs[k][args.qwen:]
    n_pool = args.scout + args.qwen

    # --- cross-judge support correlation: do they agree on WHICH sentences carry evidence?
    per_pair_r, per_pair_top3, pooled_x, pooled_y = [], [], [], []
    for k in keys:
        uni = sorted(set().union(*Rs[k]) | set().union(*Rq[k]))
        if not uni:
            continue
        a = [sum(1 for r in Rs[k] if s in r) / args.scout for s in uni]
        b = [sum(1 for r in Rq[k] if s in r) / args.qwen for s in uni]
        pooled_x += a
        pooled_y += b
        r = pearson(a, b)
        if r is not None:
            per_pair_r.append(r)
        m = min(3, len(uni))
        ta = {s for _v, s in sorted(zip(a, uni), key=lambda t: (-t[0], t[1]))[:m]}
        tb = {s for _v, s in sorted(zip(b, uni), key=lambda t: (-t[0], t[1]))[:m]}
        per_pair_top3.append(len(ta & tb) / m)

    cross = G.cross(loaded["scout"], loaded["qwen"], min(args.scout, args.qwen))
    cross["support_correlation"] = {
        "definition": f"per pair, over the union of both judges' sentences: scout's "
                      f"{args.scout}-reading support fraction against qwen's "
                      f"{args.qwen}-reading fraction; Pearson r, averaged over pairs",
        "n_pairs": len(per_pair_r),
        "mean_r": round(statistics.fmean(per_pair_r), 4) if per_pair_r else None,
        "median_r": round(statistics.median(per_pair_r), 4) if per_pair_r else None,
        "frac_pairs_r_at_least_0.5": (round(sum(1 for x in per_pair_r if x >= 0.5)
                                            / len(per_pair_r), 4) if per_pair_r else None),
        "pooled_r_over_all_sentences": (round(pearson(pooled_x, pooled_y), 4)
                                        if pooled_x else None),
        "top3_overlap": round(statistics.fmean(per_pair_top3), 4) if per_pair_top3 else None,
        "n_sentences_pooled": len(pooled_x)}
    cross["n_presentations_used"] = min(args.scout, args.qwen)
    cross["note_depth"] = (f"G.cross is read at the common depth "
                           f"{min(args.scout, args.qwen)}; the support correlation above "
                           f"uses each judge's FULL depth ({args.scout} / {args.qwen}).")

    pool = {"label": f"pooled, interleaved s0,q0,s1,q1,... then scout {args.qwen}.."
                     f"{args.scout - 1}",
            "n_readings": n_pool,
            "sentence_saturation": sentence_saturation(pooled, n_pool, "pooled"),
            "sentence_saturation_excluding_blowups": sentence_saturation(
                pooled, n_pool, "pooled, blow-up pairs dropped", exclude=blow_pairs),
            "support_reliability": [support_reliability(pooled, m)
                                    for m in RELIABILITY_NS if m <= n_pool],
            "support_reliability_excluding_blowups": [
                support_reliability(pooled, m, exclude=blow_pairs)
                for m in RELIABILITY_NS if m <= n_pool],
            "support_reliability_min3_sentences": [
                support_reliability(pooled, m, min_sentences=3)
                for m in RELIABILITY_NS if m <= n_pool],
            "support_shape": [support_shape(pooled, m) for m in (10, n_pool)],
            "note_n2_omitted": (
                "n = 2 is not on the curve and is not reported: a 1-vs-1 split makes both "
                "halves binary indicators of a single reading each, so the 'reliability' it "
                "measures is the agreement of two readings, not the stability of a graded "
                "score. The curve starts at n = 4.")}

    # --- predictions, scored
    pre = json.loads((ART / "predictions.json").read_text())
    rel30 = next((x for x in pool["support_reliability"]
                  if x["n_readings"] == 30), None)
    p1_val = rel30["spearman_brown_full_n"] if rel30 else None
    p1 = {"prediction": "P-ext-1: Spearman-Brown reliability of graded per-sentence support "
                        "at 30 pooled readings >= 0.85",
          "predicted": pre["P_ext_1"]["threshold"],
          "projected_at_preregistration": pre["P_ext_1"]["projected"],
          "observed": p1_val,
          "observed_split_half_r": rel30["split_half_r"] if rel30 else None,
          "SCORED": ("ABSENT — 30 readings were not produced" if p1_val is None else
                     ("PASS" if p1_val >= pre["P_ext_1"]["threshold"] else "FAIL")),
          "sensitivity": {
              "excluding_locator_blowups": next(
                  (x for x in pool["support_reliability_excluding_blowups"]
                   if x["n_readings"] == 30), None),
              "pairs_with_at_least_3_touched_sentences": next(
                  (x for x in pool["support_reliability_min3_sentences"]
                   if x["n_readings"] == 30), None),
              "note": "the scored value is the pre-registered estimator (every pair whose "
                      "union has >= 2 sentences, no exclusions). Both sensitivities are "
                      "reported so the reader can see the conclusion does not rest on the "
                      "estimator's choices."}}

    ss = judges["scout"]["sentence_saturation"]
    u20 = ss["mean_distinct_sentences_at_k"][str(args.scout)]
    vmax0 = pre["P_ext_2"]["Vmax_prereg"]
    p2 = {"prediction": "P-ext-2: scout's k = 20 union of distinct evidence sentences is "
                        "within 10 % of the asymptote of the MM fit to its k = 1..5 curve",
          "prereg_Vmax": vmax0, "prereg_Km": pre["P_ext_2"]["Km_prereg"],
          "prereg_projected_U20": pre["P_ext_2"]["projected_U20"],
          "prereg_threshold_U20_at_least": pre["P_ext_2"]["threshold_U20"],
          "observed_U20": u20,
          "observed_over_prereg_Vmax": round(u20 / vmax0, 4) if u20 else None,
          "SCORED": ("PASS" if u20 and u20 >= pre["P_ext_2"]["threshold_U20"] else "FAIL"),
          "secondary_projection_accuracy": {
              "predicted_U20": pre["P_ext_2"]["projected_U20"],
              "observed_U20": u20,
              "relative_error": (round(abs(u20 - pre["P_ext_2"]["projected_U20"])
                                       / pre["P_ext_2"]["projected_U20"], 4)
                                 if u20 else None),
              "threshold": 0.10,
              "SCORED": ("PASS" if u20 and abs(u20 - pre["P_ext_2"]["projected_U20"])
                         / pre["P_ext_2"]["projected_U20"] <= 0.10 else "FAIL")},
          "refit_on_all_20_points": ss["mm_fit"],
          "refit_linear_on_all_20_points": ss["linear_fit"],
          "refit_frac_of_asymptote_at_20": ss["frac_of_asymptote_at_n"]}
    ss_x = judges["scout"]["sentence_saturation_excluding_blowups"]
    u20x = ss_x["mean_distinct_sentences_at_k"][str(args.scout)]
    relx = (abs(u20x - pre["P_ext_2"]["projected_U20"]) / pre["P_ext_2"]["projected_U20"]
            if u20x else None)
    p2["THE_PASS_IS_AN_ARTEFACT_UNLESS_THIS_AGREES"] = {
        "why": ("the scored estimator is a MEAN of a heavy-tailed count. One reading in "
                f"{blow['scout']['n_readings_examined']} (Scout, "
                f"{blow['scout']['pairs'][0] if blow['scout']['pairs'] else 'none'}) had a "
                "span split across units expanded by the locator into whole-unit spans "
                "covering most of the document, contributing tens of thousands of distinct "
                "sentences to one pair's union. Dropping the pairs `locator_blowups` "
                "identifies BY MECHANISM re-reads the same prediction."),
        "blowups": blow["scout"],
        "observed_U20_excluding_blowups": u20x,
        "n_pairs": ss_x["n_pairs_positive_somewhere"],
        "SCORED_AS_WRITTEN_ON_THIS_READING": (
            "PASS" if u20x and u20x >= pre["P_ext_2"]["threshold_U20"] else "FAIL"),
        "secondary_projection_relative_error": round(relx, 4) if relx is not None else None,
        "SCORED_SECONDARY_ON_THIS_READING": (
            "PASS" if relx is not None and relx <= 0.10 else "FAIL"),
        "median_U20_all_pairs": ss["median_distinct_sentences_at_k"][str(args.scout)],
        "median_note": "the median is unaffected by the blow-up and is reported for both "
                       "readings; it is the estimator a re-run should pre-register."}

    p3s = judges["scout"]["hallucination_by_block"]["k_5_and_up_new"]
    p3q = judges["qwen"]["hallucination_by_block"]["k_5_and_up_new"]
    p3 = {"prediction": "P-ext-3: hallucinated-span rate at the NEW presentations <= 0.05 "
                        "for both judges",
          "threshold": G.GATE_HALL, "scout": p3s, "qwen": p3q,
          "SCORED": ("ABSENT — one judge produced no new presentation"
                     if p3s["rate"] is None or p3q["rate"] is None else
                     ("PASS" if p3s["rate"] <= G.GATE_HALL and p3q["rate"] <= G.GATE_HALL
                      else "FAIL"))}

    out = {
        "protocol": ("SPEC-confirmation-run-r3.md SS3.7 item 6 / SS10 item 4, extended: "
                     "scout x20 and qwen x10 presentations of the same 308 development "
                     "pairs, same prompt revision 3.1, temperature 0, seeded unit orders"),
        "extends": "artifacts/r31/gates-r31.json (#507)",
        "n_presentations": ns, "n_pooled_readings": n_pool,
        "dev_topics": C.DEV_TOPICS,
        "gates": {"self_consistency": ">= 0.90", "hallucinated_span": "<= 0.05",
                  "whether_agreement": ">= 0.90",
                  "graded_support_reliability": f">= {GATE_RELIABILITY} (r3's labeler bar, "
                                                f"read on the graded instrument -- this is "
                                                f"the number SS10 item 4 (a) turns on)"},
        "judges": judges, "cross_judge": cross, "pooled": pool,
        "locator_blowups": blow,
        "predictions": {"P_ext_1": p1, "P_ext_2": p2, "P_ext_3": p3},
    }

    reads = []
    for j in JUDGES:
        d = judges[j]
        reads.append(f"{j}: self-consistency {d['self_consistency']['rate']} "
                     f"({'PASS' if d['self_consistency']['PASS'] else 'FAIL'}), "
                     f"hallucinated {d['hallucinated_span_rate']['rate']} "
                     f"({'PASS' if d['hallucinated_span_rate']['PASS'] else 'FAIL'}), "
                     f"whether {d['whether_agreement']['rate']} "
                     f"({'PASS' if d['whether_agreement']['PASS'] else 'FAIL'})")
    winners = [j for j in JUDGES if judges[j]["ALL_THREE_GATES_PASS"]]
    out["DECISION_SPAN_GATES"] = (
        ("**NEITHER JUDGE PASSES THE CONJUNCTION on any reading** — the r3.1 stop stands. "
         if not winners else f"**{', '.join(winners)} passes all three gates.** ")
        + "; ".join(reads) + ".")
    out["DECISION_GRADED_SUPPORT"] = (
        f"Graded per-sentence support at {n_pool} pooled readings has Spearman-Brown "
        f"reliability {p1_val}, against the >= {GATE_RELIABILITY} bar r3 sets for a labeler: "
        + ("ABSENT — 30 readings were not produced."
           if p1_val is None else
           "**MEETS IT**." if p1_val >= GATE_RELIABILITY else "**does NOT meet it**.")
        + " This is a measurement of the instrument SS10 item 4 (a) proposes, not a decision "
          "about it; the decision is the owner's and needs the human read besides.")
    out["HUMAN_HALF"] = ("PENDING-HUMAN — no human read was performed, no kappa against a "
                         "human is computed, and r3 SS3.7 item 5's enumeration recall is "
                         "ABSENT, not zero. The R-dev pairs were not read.")

    od = pathlib.Path(args.outdir)
    od.mkdir(parents=True, exist_ok=True)
    C.atomic_json(od / "gates-r31ext.json", out)
    (od / "gates-r31ext.md").write_text(markdown(out))
    print(out["DECISION_SPAN_GATES"])
    print(out["DECISION_GRADED_SUPPORT"])
    for p in ("P_ext_1", "P_ext_2", "P_ext_3"):
        print(f"{p}: {out['predictions'][p]['SCORED']}")


# ------------------------------------------------------------------ rendering
def markdown(out: dict) -> str:
    L = ["# r3.1 extension — gate table, saturation and the reliability of graded support",
         "", f"*{out['protocol']}*", "", f"Extends `{out['extends']}`.", ""]
    js = list(JUDGES)
    L += ["## Gate table (all readings)", "",
          "| gate | requirement | " + " | ".join(js) + " |",
          "|---|---|" + "---|" * len(js)]

    def row(name, req, f):
        L.append(f"| {name} | {req} | " + " | ".join(str(f(out['judges'][j])) for j in js)
                 + " |")

    row("readings", "—", lambda d: d["presentations"])
    row("self-consistency (k=0 vs k=1, union)", "≥ 0.90",
        lambda d: f"{d['self_consistency']['rate']} "
                  f"{'PASS' if d['self_consistency']['PASS'] else 'FAIL'}")
    row("— mean over all C(k,2) presentation pairs", "reported",
        lambda d: d["self_consistency"]["reading_ii_all_ten_presentation_pairs"]["union"]
                   ["mean_rate"])
    row("— mean pairwise raw span-union Jaccard", "reported",
        lambda d: d["self_consistency"]["reading_ii_all_ten_presentation_pairs"]
                   ["mean_pairwise_span_union_jaccard"]["mean"])
    row("hallucinated-span rate, k = 0", "≤ 0.05",
        lambda d: f"{d['hallucinated_span_rate']['rate']} "
                  f"{'PASS' if d['hallucinated_span_rate']['PASS'] else 'FAIL'}")
    row("— all readings", "reported",
        lambda d: d["hallucinated_span_rate_all_presentations"]["rate"])
    row("— the NEW presentations only (P-ext-3)", "≤ 0.05",
        lambda d: d["hallucination_by_block"]["k_5_and_up_new"]["rate"])
    row("whether-agreement, all readings agree", "≥ 0.90",
        lambda d: f"{d['whether_agreement']['rate']} "
                  f"{'PASS' if d['whether_agreement']['PASS'] else 'FAIL'}")
    row("— mean pairwise", "reported", lambda d: d["whether_agreement"]["mean_pairwise"])
    row("ALL THREE GATES", "conjunctive",
        lambda d: "**PASS**" if d["ALL_THREE_GATES_PASS"] else "**FAIL**")
    L += ["", out["DECISION_SPAN_GATES"], ""]
    bl = out["locator_blowups"]
    nbl = sum(bl[j]["n_readings_flagged"] for j in js)
    if nbl:
        L += ["", "### A locator blow-up contaminates the mean — read this before the table",
              "",
              f"`{bl['scout']['criterion']}` flags **{nbl}** reading(s) of "
              f"{sum(bl[j]['n_readings_examined'] for j in js)}:", ""]
        for j in js:
            for h in bl[j]["readings"]:
                L.append(f"* **{j}** {h['topic']} / {h['docno']}, presentation "
                         f"{h['presentation']}: the model offered **{h['spans_seen']}** "
                         f"spans, the locator emitted **{h['spans_emitted']}**, covering "
                         f"**{h['distinct_sentences']:,}** distinct sentences "
                         f"(`split_across_units` = {h['split_across_units']}).")
        L += ["", "One span whose first and last quoted sentence located in *different* "
              "units is expanded by the r3.1 locator into one whole-unit span per "
              "intervening unit. Every column below is therefore given three ways: the "
              "**mean** (the pre-registered estimator, and what P-ext-2 is scored on), the "
              "**median** (immune to this), and the mean with the flagged pairs dropped.", ""]
    L += ["", "## Saturation — distinct evidence sentences",
          "", "| k | " + " | ".join(f"{j} mean" for j in js)
          + " | scout median | scout mean, blow-up dropped | pooled mean |",
          "|---|" + "---|" * (len(js) + 3)]
    npool = out["n_pooled_readings"]
    for k in range(1, npool + 1):
        cells = []
        for j in js:
            m = out["judges"][j]["sentence_saturation"]["mean_distinct_sentences_at_k"]
            cells.append(str(m.get(str(k), "—")))
        sc = out["judges"]["scout"]
        cells.append(str(sc["sentence_saturation"]
                         ["median_distinct_sentences_at_k"].get(str(k), "—")))
        cells.append(str(sc["sentence_saturation_excluding_blowups"]
                         ["mean_distinct_sentences_at_k"].get(str(k), "—")))
        cells.append(str(out["pooled"]["sentence_saturation"]
                         ["mean_distinct_sentences_at_k"].get(str(k), "—")))
        L.append(f"| {k} | " + " | ".join(cells) + " |")
    L += ["", "Michaelis–Menten refits (U(k) = Vmax·k/(Km+k)), with the no-saturation "
          "linear null beside each:", ""]

    def fitline(name, s):
        f, lin = s["mm_fit"], s["linear_fit"]
        if f.get("DEGENERATE"):
            return (f"* **{name}** — **the MM fit is DEGENERATE**: {f['degenerate_note']} "
                    f"The linear null fits it at r² {lin['r2']} with slope "
                    f"{lin['slope_per_reading']} sentences per reading. **No asymptote is "
                    f"measurable from these points.**")
        return (f"* **{name}** Vmax {f['Vmax']}, Km {f['Km']}, r² {f['r2']}; at its own "
                f"depth the union is {s['frac_of_asymptote_at_n']} of the asymptote "
                f"(linear null r² {lin['r2']}, slope {lin['slope_per_reading']})")

    for j in js:
        L.append(fitline(j, out["judges"][j]["sentence_saturation"]))
    L.append(fitline("scout, blow-up pairs dropped",
                     out["judges"]["scout"]["sentence_saturation_excluding_blowups"]))
    L.append(fitline("pooled", out["pooled"]["sentence_saturation"])
             + " — the pooled curve is a staircase (a scout reading adds many sentences, "
               "the qwen reading after it adds few), so its fit is the worst of the three")
    L += ["", "Marginal gain at the last reading, and the same curve in D3-**distinct "
          "sets** rather than sentences:", ""]
    for j in js:
        d = out["judges"][j]
        ss_, sv = d["sentence_saturation"], d["set_saturation_incremental"]
        L.append(f"* **{j}** sentences +{ss_['marginal_gain_at_n']} of the union at "
                 f"k = {d['presentations']}; distinct sets "
                 f"{sv['mean_distinct_sets_at_k']['1']} → "
                 f"{sv['mean_distinct_sets_at_k'][str(d['presentations'])]} "
                 f"(+{sv['marginal_gain_at_n']} at the last reading), MM Vmax "
                 f"{sv['mm_fit']['Vmax']}")
    sh = out["pooled"]["support_shape"][-1]
    L += ["", f"Where the support mass sits at {sh['n_readings']} readings: the union holds "
          f"**{sh['mean_union_sentences']}** sentences per pair (median "
          f"{sh['median_union_sentences']}), the top 3 carry **{sh['share_of_votes_on_top3']}** "
          f"of all votes, and the best-supported sentence is in "
          f"**{sh['mean_max_support']}** of readings.",
          "", "## Reliability of graded per-sentence support (pooled readings)", "",
          "| n readings | split-half r | Spearman–Brown full-n | top-3 overlap | SB, blow-up "
          "dropped | SB, pairs with ≥ 3 sentences |",
          "|---|---|---|---|---|---|"]
    bx = {x["n_readings"]: x for x in out["pooled"]["support_reliability_excluding_blowups"]}
    b3 = {x["n_readings"]: x for x in out["pooled"]["support_reliability_min3_sentences"]}
    for x in out["pooled"]["support_reliability"]:
        n = x["n_readings"]
        L.append(f"| {n} | {x['split_half_r']} | "
                 f"{x['spearman_brown_full_n']} | {x['top3_overlap_between_halves']} | "
                 f"{bx.get(n, {}).get('spearman_brown_full_n')} | "
                 f"{b3.get(n, {}).get('spearman_brown_full_n')} |")
    L.append("")
    L.append(f"*{out['pooled']['note_n2_omitted']}*")
    c = out["cross_judge"]["support_correlation"]
    L += ["", "## Cross-judge", "",
          f"* per-sentence support correlation, scout vs qwen: mean r **{c['mean_r']}** "
          f"(median {c['median_r']}, pooled over sentences {c['pooled_r_over_all_sentences']}), "
          f"top-3 overlap {c['top3_overlap']}",
          f"* κ(scout–qwen) on *whether*, k = 0: "
          f"{out['cross_judge']['at_presentation_0']['pair_level_binary_kappa']['kappa']}",
          "", "## Predictions", "", "| prediction | observed | scored |", "|---|---|---|"]
    p = out["predictions"]
    L.append(f"| P-ext-1 (SB reliability at 30 ≥ 0.85) | {p['P_ext_1']['observed']} | "
             f"**{p['P_ext_1']['SCORED']}** |")
    a = p["P_ext_2"]["THE_PASS_IS_AN_ARTEFACT_UNLESS_THIS_AGREES"]
    L.append(f"| P-ext-2 (scout U(20) within 10 % of asymptote) | "
             f"{p['P_ext_2']['observed_U20']} vs Vmax {p['P_ext_2']['prereg_Vmax']} | "
             f"**{p['P_ext_2']['SCORED']}** |")
    L.append(f"| — P-ext-2 with the locator blow-up dropped | "
             f"{a['observed_U20_excluding_blowups']} vs threshold "
             f"{p['P_ext_2']['prereg_threshold_U20_at_least']} | "
             f"**{a['SCORED_AS_WRITTEN_ON_THIS_READING']}** |")
    L.append(f"| — P-ext-2 secondary (projection accurate to 10 %), blow-up dropped | "
             f"{a['observed_U20_excluding_blowups']} vs predicted "
             f"{p['P_ext_2']['prereg_projected_U20']} "
             f"(relative error {a['secondary_projection_relative_error']}) | "
             f"**{a['SCORED_SECONDARY_ON_THIS_READING']}** |")
    L.append(f"| P-ext-3 (new-presentation hallucination ≤ 0.05) | "
             f"scout {p['P_ext_3']['scout']['rate']}, qwen {p['P_ext_3']['qwen']['rate']} | "
             f"**{p['P_ext_3']['SCORED']}** |")
    L += ["", out["DECISION_GRADED_SUPPORT"], "", f"*{out['HUMAN_HALF']}*", ""]
    return "\n".join(L)


if __name__ == "__main__":
    main()
