#!/usr/bin/env python3
"""Power for the reach comparison's primary test (PREREG.md §4, §5, §11 Q1-Q2). Standard library
only; seeded; no study outcome is read (the S side does not exist yet, and nothing W-side is read
either: the cluster structure below is embedded as anonymised counts).

    python3 -I -B power.py [--out power.json] [--sims 10000] [--valid 500]

**Design simulated.** The primary cell is quote@10 under the default retriever, pooled over the four
corpus collections (base, neighbour, random1, random2). Each Q-eligible record i contributes one paired
difference per corpus, d_ic = S_ic - W_ic in {-1, 0, +1}, so a record contributes four. Records are
clustered by paper (a paper has 1-3 records: up to 2 G and 1 C), and a cluster's units are all its
records' differences in all four corpora: m_k = 4 n_k units, cluster sum T_k = sum d_ic. The pooled
difference is D = sum_k T_k / M, M = sum_k m_k; the estimand is Delta = E[S] - E[W] pooled over the four
corpora with equal weight (every record appears in every corpus).

**Cluster structure** (:data:`PAPER_SIZES`). Records per paper among the Q-eligible records, derived
read-only from the accepted record set's ``paper`` field: counts only, no id. Q-eligible means the
whitespace-normalised quote is a substring of the whitespace-normalised gold chunk text
(``reach_quote.is_verbatim``; PREREG §2, §11 Q4). That test leaves **1,089** records (2 excluded) in
408 papers (25 / 85 / 298 papers with 1 / 2 / 3 records). The prereg draft's "26" is the *raw*
substring count (no whitespace normalisation): 1,065 records, 27 / 93 / 284 papers
(:data:`PAPER_SIZES_RAW`, run as a sensitivity). See ``feasibility.json`` → ``eligibility``.

**Outcome model.** Per corpus c the pair (W, S) has P(d = +1) = a_c, P(d = -1) = b_c with
a_c + b_c = pi (the discordance: the share of (record, corpus) units where S and W differ) and
a_c - b_c = Delta (the true difference). Equal rates in every corpus unless a corpus cannot carry them:
d = +1 needs a W miss (a_c <= 1 - w_c) and d = -1 needs a W hit (b_c <= w_c), w_c the W baseline
(:data:`W_BASELINE`, default retriever: base 94.2 %, neighbour 87.6 %, random1 90.0 %, random2 89.5 %).
A capped corpus's excess goes to the others equally, so the pooled a and b are unchanged (only
pi = 10 %, Delta = +2 needs this: base can carry a = 5.8 %, not 6 %). The W baselines matter only
through these caps; d is all the test sees.

**Correlation.** Two parameters, as nested mixtures: with probability rho_paper a paper's whole
cluster shares one d draw (all 4 n_k units equal); otherwise each record, with probability
rho_rec' = (rho_record - rho_paper) / (1 - rho_paper), shares one draw across its four corpora;
otherwise its four units draw independently by corpus. Then corr(d, d') = rho_paper between units of
different records of the same paper and rho_record between two corpora of the same record (exact when
the per-corpus rates are equal). The brief's grid is rho_paper in {0, 0.25, 0.5} with
rho_record = rho_paper (one correlation for every pair in a paper); each is also run with
rho_record = 0.75, because the W-side proxy (``feasibility.json`` → ``w_miss_correlation``) shows the
default retriever's quote@10 misses correlate 0.74 between corpora of the same record and ~0 between
records of the same paper: the gold document is chunked identically in all four corpora, so d is
expected to repeat across corpora far more than across records. Cluster sums T_k are drawn from their
exact distribution per paper size (convolutions of the mixture), one draw per paper.

**The NI test (primary) and why.** H0: Delta <= -delta vs H1: Delta > -delta, one-sided alpha 0.05. With
X_k = T_k + m_k delta (the cluster sums of d + delta), the statistic is sum_k X_k, referred to its
**sign-flip (randomisation) distribution over papers**: sum_k s_k X_k, s_k = +-1 fair and independent;
p = P(sum s_k X_k >= sum X_k). With delta in whole points, 100 X_k is an integer, so the randomisation
distribution is computed **exactly** by lattice convolution (:func:`sign_flip_p_exact`), with no
Monte-Carlo error and no seed in the analysis.

*Justification.* (1) It honours the clustering: papers are the exchangeable units, as in the alias
studies' sign-flip over records. (2) "Exact" here means exact *randomisation* p. Sign symmetry of X_k
is a sharp hypothesis that the composite NI null does not imply: at the boundary E[X_k] = 0 but X_k is
skewed (most clusters are +m_k delta, a few are large and negative). The test is nevertheless the
randomisation form of the cluster **score** test with the null-restricted variance: its standardised
form is z = sum X_k / sqrt(sum X_k^2), and E[sum X_k^2] = sum Var(T_k) at the boundary, so the
reference variance is right under H0 without estimating Delta; with 408 papers and |T_k| <= 12 the
Lindeberg condition holds when discordant clusters are not rare. Its size is therefore asymptotic, not
exact, and this script **measures** it: every cell at Delta = -delta is a size cell (``size_cells``,
``size_summary_max_over_rho``). (3) The score form is preferred to the Wald form (robust SE around the
estimate) because Wald intervals for paired binary differences with sparse discordance are
anti-conservative near the boundary (Nam 1997; Tango 1998, for the unclustered case); the Wald test is
simulated alongside as a check (here the two are nearly identical). (4) Two skewness remedies were
tried and not chosen: Johnson's (1978) skewness-corrected t on the X_k (simulated in every cell as
``ni_power_johnson``: it does not lower the worst sizes and its power is not monotone in delta when
discordance is low) and a cluster bootstrap-t lower bound (prototyped on the worst cells during design
with B = 399: no better size there; it would also put a seed and resampling into the analysis).

**What the size cells show** (read the numbers in ``power.json``). Size is close to alpha when
discordance is 10 %, and inflated when discordance is low *and* the discordant units are concentrated
in few clusters: at the boundary Delta = -delta with discordance near delta, all discordant units are
losses (p_plus ~ 0), and with paper-level sharing (rho_paper >= 0.25) the whole test rests on a handful
of discordant papers. No large-sample construction is valid there, and the exact randomisation p does
not rescue it (the third validation cell). In the evidence-based correlation (rho_paper ~ 0) the excess
is small. This is a property of the data the design can produce, not of the convolution.

*In the simulation* the NI decision uses the standardised form z > z_0.95 (normal approximation of
the randomisation distribution; the exact convolution costs ~0.1-1 s per test, too slow for the grid).
The **validation block** computes the exact randomisation p for a subsample of simulated studies in
three cells (the sizing case at the boundary Delta = -2 and at Delta = 0, and the worst size corner)
and reports the decision agreement, the rejection rates and the largest |p_exact - p_normal|.

**Superiority (hierarchical, after NI).** H0: Delta <= 0 by the sign-flip over papers on the integer
cluster sums T_k (exact randomisation p by the same convolution; under exchangeable S and W within a
paper, the Delta = 0 boundary of interest, the sign symmetry is implied and the test is exact), tested
only when NI rejects; no extra alpha (fixed sequence). In the grid it is decided by
z = sum T_k / sqrt(sum T_k^2) > z_0.95 and validated like NI.

**Outputs** (``power.json``): the assumptions; one cell per (true Delta, discordance, rho_paper,
rho_record), each with the NI power for delta in {1, 2, 3} points (score, Wald, Johnson), the
hierarchical superiority power, the design effect and the per-corpus rates used; size cells at
Delta = -delta for every delta; the raw-exclusion sensitivity; the validation block; power tables at
three correlation settings; the recommendation (rule, power and size at the sizing case, and the
owner's delta = 2 by discordance); provenance (script sha256, Python, git commit and dirty flag).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import subprocess
import sys
from bisect import bisect_right
from collections import Counter
from pathlib import Path

ALPHA = 0.05
Z_CRIT = 1.6448536269514722  # the 0.95 quantile of N(0, 1)
SEED_STRING = "261109|reach|rechunk|power"
DEFAULT_SIMS = 10000
DEFAULT_VALID = 500
TARGETS = (0.80, 0.90)

CORPORA = ("base", "neighbour", "random1", "random2")
#: W quote@10, default retriever (PREREG §5; feasibility.json reproduces them to 0.1 point)
W_BASELINE = {"base": 0.942, "neighbour": 0.876, "random1": 0.900, "random2": 0.895}

DELTAS_PTS = (1, 2, 3)
TRUE_PTS = (-1, 0, 1, 2)
DISCORDANCE = (0.02, 0.05, 0.10)
RHO_PAPER = (0.0, 0.25, 0.5)
RHO_RECORD_EXTRA = 0.75  # the second rho_record value (the first is rho_record = rho_paper)

#: records per paper, Q-eligible (whitespace-normalised verbatim): {size: number of papers}. Counts only.
PAPER_SIZES = {1: 25, 2: 85, 3: 298}  # 408 papers, 1,089 records
#: the same under the raw (un-normalised) substring test, the prereg draft's 26 exclusions
PAPER_SIZES_RAW = {1: 27, 2: 93, 3: 284}  # 404 papers, 1,065 records

#: the sizing case for the recommendation (assumed, not estimated; see the module doc)
SIZING = {"discordance": 0.10, "rho_paper": 0.0, "rho_record": 0.75, "true_pts": 0}
TARGET_RULE = 0.90


def seed_int(seed: str) -> int:
    """The project's rule: the first 8 bytes of sha256(seed string), big-endian."""
    return int.from_bytes(hashlib.sha256(seed.encode("utf-8")).digest()[:8], "big")


# ------------------------------------------------------------------ the outcome model

def corpus_rates(pi: float, delta_true: float) -> dict[str, tuple[float, float]] | None:
    """Per-corpus (a_c, b_c) = (P(d=+1), P(d=-1)); equal unless capped by the W baselines, a capped
    corpus's excess spread equally over the others. None when no allocation exists."""
    a, b = (pi + delta_true) / 2, (pi - delta_true) / 2
    if a < -1e-12 or b < -1e-12:
        return None
    a, b = max(a, 0.0), max(b, 0.0)
    out = {}
    for which, total, cap in (("a", a, {c: 1 - W_BASELINE[c] for c in CORPORA}),
                              ("b", b, {c: W_BASELINE[c] for c in CORPORA})):
        alloc = dict.fromkeys(CORPORA, 0.0)
        need = total * len(CORPORA)
        open_c = list(CORPORA)
        while need > 1e-15 and open_c:
            share = need / len(open_c)
            nxt = []
            for c in open_c:
                take = min(share, cap[c] - alloc[c])
                alloc[c] += take
                need -= take
                if cap[c] - alloc[c] > 1e-15:
                    nxt.append(c)
            open_c = nxt
        if need > 1e-12:
            return None
        out[which] = alloc
    rates = {c: (out["a"][c], out["b"][c]) for c in CORPORA}
    if any(x + y > 1 for x, y in rates.values()):
        return None
    return rates


def conv(p: dict[int, float], q: dict[int, float]) -> dict[int, float]:
    r: dict[int, float] = {}
    for i, x in p.items():
        for j, y in q.items():
            r[i + j] = r.get(i + j, 0.0) + x * y
    return r


def mix(w: float, p: dict[int, float], q: dict[int, float]) -> dict[int, float]:
    r = {k: w * v for k, v in p.items()}
    for k, v in q.items():
        r[k] = r.get(k, 0.0) + (1 - w) * v
    return r


def cluster_dists(rates: dict[str, tuple[float, float]], rho_paper: float, rho_record: float,
                  sizes: list[int]) -> dict[int, tuple[list[int], list[float]]]:
    """Exact distribution of T_k for each paper size: (values, cumulative weights)."""
    a = sum(r[0] for r in rates.values()) / len(rates)
    b = sum(r[1] for r in rates.values()) / len(rates)
    shared = {1: a, -1: b, 0: 1 - a - b}  # one pooled draw, used by the shared modes
    indep: dict[int, float] = {0: 1.0}
    for c in CORPORA:
        ac, bc = rates[c]
        indep = conv(indep, {1: ac, -1: bc, 0: 1 - ac - bc})
    rho_r = 0.0 if rho_paper >= 1 else (rho_record - rho_paper) / (1 - rho_paper)
    if not -1e-12 <= rho_r <= 1 + 1e-12:
        raise ValueError("need rho_paper <= rho_record <= 1")
    record = mix(rho_r, {4 * k: v for k, v in shared.items()}, indep)
    out = {}
    for n in sorted(set(sizes)):
        rn: dict[int, float] = {0: 1.0}
        for _ in range(n):
            rn = conv(rn, record)
        dist = mix(rho_paper, {4 * n * k: v for k, v in shared.items()}, rn)
        vals = sorted(k for k, v in dist.items() if v > 0)
        cum, s = [], 0.0
        for k in vals:
            s += dist[k]
            cum.append(s)
        out[n] = (vals, cum)
    return out


# ------------------------------------------------------------------ the tests

def score_z(pairs: Counter, delta: float) -> float:
    """z = sum X_k / sqrt(sum X_k^2), X_k = T_k + m_k delta; pairs counts (m_k, T_k)."""
    s = ss = 0.0
    for (m, t), c in pairs.items():
        x = t + m * delta
        s += c * x
        ss += c * x * x
    return s / math.sqrt(ss) if ss > 0 else (math.inf if s > 0 else -math.inf)


def wald_z(pairs: Counter, delta: float, k_clusters: int, m_total: int) -> float:
    """Cluster-robust Wald: (D + delta) / SE, SE^2 = K/(K-1) sum (T_k - m_k D)^2 / M^2."""
    d = sum(c * t for (m, t), c in pairs.items()) / m_total
    v = sum(c * (t - m * d) ** 2 for (m, t), c in pairs.items()) * k_clusters / (k_clusters - 1) / m_total ** 2
    if v <= 0:
        return math.inf if d + delta > 0 else -math.inf
    return (d + delta) / math.sqrt(v)


def johnson_t(pairs: Counter, delta: float) -> float:
    """Johnson's (1978) skewness-corrected t on the cluster values X_k = T_k + m_k delta (H0 mean 0):
    t1 = (xbar + mu3 / (6 s^2 K) + mu3 xbar^2 / (3 s^4)) / (s / sqrt(K)), mu3 the unbiased third
    central moment, s^2 the sample variance, K the number of papers."""
    k = sum(pairs.values())
    xbar = sum(c * (t + m * delta) for (m, t), c in pairs.items()) / k
    s2 = sum(c * (t + m * delta - xbar) ** 2 for (m, t), c in pairs.items()) / (k - 1)
    if s2 <= 0:
        return math.inf if xbar > 0 else -math.inf
    mu3 = sum(c * (t + m * delta - xbar) ** 3 for (m, t), c in pairs.items()) * k / ((k - 1) * (k - 2))
    return (xbar + mu3 / (6 * s2 * k) + mu3 * xbar * xbar / (3 * s2 * s2)) / math.sqrt(s2 / k)


def sign_flip_p_exact(values: list[int]) -> float:
    """Exact P(sum s_k x_k >= sum x_k) over independent fair signs, for integer x_k: lattice
    convolution of the |x| groups after dividing by their gcd. The analysis's NI p (on
    100 * (T_k + m_k delta / 100)) and superiority p (on T_k)."""
    xs = [abs(v) for v in values if v]
    t_obs = sum(values)
    if not xs:
        return 1.0
    g = 0
    for v in xs:
        g = math.gcd(g, v)
    xs = [v // g for v in xs]
    if t_obs % g:
        raise ValueError("observed sum not on the lattice")
    t_obs //= g
    total = sum(xs)
    # dist[i] = P(sum = i - total); start with the empty sum at offset total
    dist = [0.0] * (2 * total + 1)
    dist[total] = 1.0
    lo = hi = total  # current support bounds
    for v in sorted(xs):
        new_lo, new_hi = lo - v, hi + v
        seg = dist[lo:hi + 1]
        out = [0.0] * (new_hi - new_lo + 1)
        # out[j] = (seg[j] + seg[j - 2v]) / 2 in support coordinates
        for j, x in enumerate(seg):
            if x:
                h = 0.5 * x
                out[j] += h
                out[j + 2 * v] += h
        dist[new_lo:new_hi + 1] = out
        lo, hi = new_lo, new_hi
    start = max(t_obs + total, lo)
    return min(1.0, sum(dist[start:hi + 1]))


# ------------------------------------------------------------------ simulation

def draw_study(rng: random.Random, dists: dict[int, tuple[list[int], list[float]]],
               sizes: dict[int, int]) -> Counter:
    pairs: Counter = Counter()
    for n, n_papers in sizes.items():
        vals, cum = dists[n]
        top = cum[-1]
        for _ in range(n_papers):
            t = vals[min(bisect_right(cum, rng.random() * top), len(vals) - 1)]
            pairs[(4 * n, t)] += 1
    return pairs


def simulate_cell(rng: random.Random, true_pts: float, pi: float, rho_paper: float, rho_record: float,
                  sims: int, sizes: dict[int, int] = PAPER_SIZES,
                  deltas: tuple[int, ...] = DELTAS_PTS) -> dict[str, object] | None:
    rates = corpus_rates(pi, true_pts / 100)
    if rates is None:
        return None
    size_list = [n for n, c in sizes.items() for _ in range(c)]
    dists = cluster_dists(rates, rho_paper, rho_record, size_list)
    k_clusters = len(size_list)
    m_total = 4 * sum(size_list)
    rej_s = Counter()
    rej_w = Counter()
    rej_j = Counter()
    rej_h = Counter()
    sup_alone = 0
    d_sum = d_sq = disc_sum = 0.0
    for _ in range(sims):
        pairs = draw_study(rng, dists, sizes)
        d = sum(c * t for (m, t), c in pairs.items()) / m_total
        d_sum += d
        d_sq += d * d
        disc_sum += sum(c * abs(t) for (m, t), c in pairs.items())  # a lower bound on discordant units
        sup = score_z(pairs, 0.0) > Z_CRIT
        sup_alone += sup
        for dl in deltas:
            ni = score_z(pairs, dl / 100) > Z_CRIT
            rej_s[dl] += ni
            rej_w[dl] += wald_z(pairs, dl / 100, k_clusters, m_total) > Z_CRIT
            rej_j[dl] += johnson_t(pairs, dl / 100) > Z_CRIT
            rej_h[dl] += ni and sup
    mean_d = d_sum / sims
    sd_d = math.sqrt(max(d_sq / sims - mean_d ** 2, 0.0))
    se_indep = math.sqrt(max(pi - (true_pts / 100) ** 2, 1e-12) / m_total)
    return {
        "true_pts": true_pts, "discordance": pi, "rho_paper": rho_paper, "rho_record": rho_record,
        "rates_by_corpus": {c: {"p_plus": round(a, 5), "p_minus": round(b, 5),
                                "implied_S": round(W_BASELINE[c] + a - b, 4)} for c, (a, b) in rates.items()},
        "capped": any(abs(a - (pi + true_pts / 100) / 2) > 1e-9 for a, _ in rates.values()),
        "ni_power_score": {str(dl): round(rej_s[dl] / sims, 4) for dl in deltas},
        "ni_power_wald": {str(dl): round(rej_w[dl] / sims, 4) for dl in deltas},
        "ni_power_johnson": {str(dl): round(rej_j[dl] / sims, 4) for dl in deltas},
        "hier_superiority_power": {str(dl): round(rej_h[dl] / sims, 4) for dl in deltas},
        "superiority_power_alone": round(sup_alone / sims, 4),
        "mean_D_pts": round(100 * mean_d, 3), "sd_D_pts": round(100 * sd_d, 3),
        "design_effect": round((sd_d / se_indep) ** 2, 2),
        "mean_abs_cluster_sum_total": round(disc_sum / sims, 1),
        "sims": sims, "mc_se_at_0.9": round(math.sqrt(0.09 / sims), 4),
        "mc_se_at_0.05": round(math.sqrt(0.05 * 0.95 / sims), 4),
    }


def validate(rng: random.Random, true_pts: float, pi: float, rho_paper: float, rho_record: float,
             delta_pts: int, n: int) -> dict[str, object]:
    """Exact randomisation p vs the normal approximation on n simulated studies of one cell."""
    rates = corpus_rates(pi, true_pts / 100)
    assert rates is not None
    size_list = [s for s, c in PAPER_SIZES.items() for _ in range(c)]
    dists = cluster_dists(rates, rho_paper, rho_record, size_list)
    agree_ni = agree_sup = 0
    rej_ni_exact = rej_ni_norm = rej_sup_exact = rej_sup_norm = 0
    max_diff_ni = max_diff_sup = 0.0
    for _ in range(n):
        pairs = draw_study(rng, dists, PAPER_SIZES)
        x_int = [100 * t + m * delta_pts for (m, t), c in pairs.items() for _ in range(c)]
        t_int = [t for (m, t), c in pairs.items() for _ in range(c)]
        p_ni = sign_flip_p_exact(x_int)
        z_ni = score_z(pairs, delta_pts / 100)
        p_ni_n = 0.5 * math.erfc(z_ni / math.sqrt(2))
        p_sup = sign_flip_p_exact(t_int)
        z_sup = score_z(pairs, 0.0)
        p_sup_n = 0.5 * math.erfc(z_sup / math.sqrt(2))
        e_ni, n_ni = p_ni <= ALPHA, p_ni_n <= ALPHA
        e_sup, n_sup = p_sup <= ALPHA, p_sup_n <= ALPHA
        agree_ni += e_ni == n_ni
        agree_sup += e_sup == n_sup
        rej_ni_exact += e_ni
        rej_ni_norm += n_ni
        rej_sup_exact += e_sup
        rej_sup_norm += n_sup
        max_diff_ni = max(max_diff_ni, abs(p_ni - p_ni_n))
        max_diff_sup = max(max_diff_sup, abs(p_sup - p_sup_n))
    return {"true_pts": true_pts, "discordance": pi, "rho_paper": rho_paper, "rho_record": rho_record,
            "delta_pts": delta_pts, "studies": n,
            "ni": {"decision_agreement": round(agree_ni / n, 4), "reject_exact": round(rej_ni_exact / n, 4),
                   "reject_normal": round(rej_ni_norm / n, 4), "max_abs_p_diff": round(max_diff_ni, 5)},
            "superiority": {"decision_agreement": round(agree_sup / n, 4),
                            "reject_exact": round(rej_sup_exact / n, 4),
                            "reject_normal": round(rej_sup_norm / n, 4), "max_abs_p_diff": round(max_diff_sup, 5)}}


# ------------------------------------------------------------------ main

def git_state(here: Path) -> dict[str, object]:
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=here, capture_output=True, text=True,
                                check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=here, capture_output=True, text=True,
                               check=True).stdout.strip() != ""
        return {"commit": commit, "dirty": dirty}
    except Exception:  # noqa: BLE001 - provenance is best effort outside a checkout
        return {"commit": None, "dirty": None}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "power.json")
    ap.add_argument("--sims", type=int, default=DEFAULT_SIMS)
    ap.add_argument("--valid", type=int, default=DEFAULT_VALID)
    a = ap.parse_args(argv)
    here = Path(__file__).resolve()
    state = git_state(here.parent)  # read before anything is written
    rng = random.Random(seed_int(SEED_STRING))

    rho_grid = [(rp, rp) for rp in RHO_PAPER] + [(rp, RHO_RECORD_EXTRA) for rp in RHO_PAPER]
    cells = []
    skipped = []
    for tp in TRUE_PTS:
        for pi in DISCORDANCE:
            for rp, rr in rho_grid:
                cell = simulate_cell(rng, tp, pi, rp, rr, a.sims)
                if cell is None:
                    skipped.append({"true_pts": tp, "discordance": pi, "reason": "no allocation"})
                else:
                    cells.append(cell)

    # size at the NI boundary for each delta (Delta = -delta): delta 1 is in the grid already (true -1)
    tests = ("score", "wald", "johnson")
    size_cells = []
    for dl in DELTAS_PTS:
        for pi in DISCORDANCE:
            for rp, rr in rho_grid:
                row: dict[str, object] = {"delta_pts": dl, "discordance": pi, "rho_paper": rp, "rho_record": rr}
                if dl == 1:
                    c = next(c for c in cells if c["true_pts"] == -1 and c["discordance"] == pi
                             and c["rho_paper"] == rp and c["rho_record"] == rr)
                    row["from_grid"] = True
                else:
                    c = simulate_cell(rng, -dl, pi, rp, rr, a.sims, deltas=(dl,))
                    row["from_grid"] = False
                if c is None:
                    row.update({f"size_{t}": None for t in tests})
                    row["note"] = "infeasible: discordance below |Delta|"
                else:
                    row.update({f"size_{t}": c[f"ni_power_{t}"][str(dl)] for t in tests})  # type: ignore[index]
                size_cells.append(row)

    # raw-exclusion sensitivity (1,065 records) at the sizing correlation, true 0 and +1
    raw = []
    for tp in (0, 1):
        for pi in DISCORDANCE:
            c = simulate_cell(rng, tp, pi, SIZING["rho_paper"], SIZING["rho_record"], a.sims, sizes=PAPER_SIZES_RAW)
            raw.append(c)

    valid = []
    if a.valid > 0:
        for tp in (-2, 0):
            valid.append(validate(rng, tp, SIZING["discordance"], SIZING["rho_paper"], SIZING["rho_record"], 2, a.valid))
        # the worst size corner of the grid: does the exact randomisation p rescue it? (it does not)
        valid.append(validate(rng, -2, 0.02, 0.25, 0.25, 2, a.valid))

    def find(tp, pi, rp, rr):
        return next(c for c in cells if c["true_pts"] == tp and c["discordance"] == pi
                    and c["rho_paper"] == rp and c["rho_record"] == rr)

    def size_of(dl, pi, rp, rr):
        return next(r["size_score"] for r in size_cells if r["delta_pts"] == dl and r["discordance"] == pi
                    and r["rho_paper"] == rp and r["rho_record"] == rr)

    # recommendation: the smallest delta with NI power >= TARGET_RULE at the sizing case
    sz = find(SIZING["true_pts"], SIZING["discordance"], SIZING["rho_paper"], SIZING["rho_record"])
    rec = next((dl for dl in DELTAS_PTS if float(sz["ni_power_score"][str(dl)]) >= TARGET_RULE), None)  # type: ignore[index]
    size_limit = ALPHA + 2 * math.sqrt(ALPHA * (1 - ALPHA) / a.sims)
    max_size = max(float(r["size_score"]) for r in size_cells if r["size_score"] is not None)  # type: ignore[arg-type]
    size_summary = {f"delta {dl}": {f"disc {int(pi * 100)}%": max(
        (float(r["size_score"]) for r in size_cells  # type: ignore[arg-type]
         if r["delta_pts"] == dl and r["discordance"] == pi and r["size_score"] is not None), default=None)
        for pi in DISCORDANCE} for dl in DELTAS_PTS}
    tables = {}
    for rp, rr in ((SIZING["rho_paper"], SIZING["rho_record"]), (0.25, 0.75), (0.25, 0.25)):
        tables[f"rho_paper {rp}, rho_record {rr}"] = {
            f"disc {int(pi * 100)}%": {f"true {tp:+d}": {
                f"delta {dl}": find(tp, pi, rp, rr)["ni_power_score"][str(dl)]  # type: ignore[index]
                for dl in DELTAS_PTS} for tp in TRUE_PTS} for pi in DISCORDANCE}
    sup_table = {f"disc {int(pi * 100)}%": {f"true {tp:+d}": find(
        tp, pi, SIZING["rho_paper"], SIZING["rho_record"])["hier_superiority_power"]
        for tp in TRUE_PTS} for pi in DISCORDANCE}
    rec_block = {
        "recommended_delta_pts": rec,
        "rule": f"smallest delta in {list(DELTAS_PTS)} points with NI power >= {TARGET_RULE} at true Delta = 0 "
                "under the sizing case: discordance 10 % (the top of the grid; the S-W discordance is not "
                "observable before S exists), rho_paper 0 and rho_record 0.75 (the W-side proxy: 0.005 and "
                "0.74, feasibility.json)",
        "power_at_sizing_case": sz["ni_power_score"],
        "size_at_sizing_case": {str(dl): size_of(dl, SIZING["discordance"], SIZING["rho_paper"],
                                                 SIZING["rho_record"]) for dl in DELTAS_PTS},
        "size_limit_alpha_plus_2mcse": round(size_limit, 4),
        "owner_delta_2": {f"disc {int(pi * 100)}%": find(0, pi, SIZING["rho_paper"], SIZING["rho_record"])[
            "ni_power_score"]["2"] for pi in DISCORDANCE},  # type: ignore[index]
    }

    out = {
        "schema": "reach-rechunk-power/1",
        "alpha_one_sided": ALPHA, "z_crit": Z_CRIT, "targets": list(TARGETS),
        "primary_test": "one-sided NI, H0 Delta <= -delta: sign-flip randomisation test over papers on "
                        "X_k = T_k + m_k*delta (exact p by lattice convolution in the analysis; "
                        "standardised form z = sum X / sqrt(sum X^2) > z_0.95 in the simulation)",
        "check_test": "cluster-robust Wald z = (D + delta) / SE_robust > z_0.95",
        "superiority": "after NI rejects: exact sign-flip over papers on T_k (z form in the simulation)",
        "assumptions": {
            "estimand": "pooled quote@10(S) - quote@10(W), default retriever, 4 corpora equally weighted",
            "units": "one paired difference per (Q-eligible record, corpus); clusters = papers",
            "eligibility": "whitespace-normalised quote is a substring of the normalised gold chunk text",
            "records": sum(n * c for n, c in PAPER_SIZES.items()), "papers": sum(PAPER_SIZES.values()),
            "papers_by_records": {str(k): v for k, v in PAPER_SIZES.items()},
            "raw_exclusion_sensitivity": {"records": sum(n * c for n, c in PAPER_SIZES_RAW.items()),
                                          "papers": sum(PAPER_SIZES_RAW.values()),
                                          "papers_by_records": {str(k): v for k, v in PAPER_SIZES_RAW.items()}},
            "w_baseline_default": W_BASELINE,
            "deltas_pts": list(DELTAS_PTS), "true_pts": list(TRUE_PTS), "discordance": list(DISCORDANCE),
            "rho_paper": list(RHO_PAPER), "rho_record": "rho_paper, and 0.75",
            "correlation_model": "nested mixture: paper-shared draw w.p. rho_paper, else record-shared draw "
                                 "across the 4 corpora w.p. (rho_record - rho_paper)/(1 - rho_paper), else "
                                 "independent by corpus",
            "rate_allocation": "equal per corpus; a corpus that cannot carry it (W baseline cap) passes its "
                               "excess to the others equally",
        },
        "seed_string": SEED_STRING, "seed_int": seed_int(SEED_STRING), "sims_per_cell": a.sims,
        "mc_se": {"at_0.9": round(math.sqrt(0.09 / a.sims), 4), "at_0.8": round(math.sqrt(0.16 / a.sims), 4),
                  "at_0.05": round(math.sqrt(0.0475 / a.sims), 4)},
        "cells": cells, "skipped_cells": skipped, "size_cells": size_cells,
        "max_size_score": max_size, "size_summary_max_over_rho": size_summary,
        "raw_exclusion_cells": raw, "validation": valid,
        "sizing_case": SIZING, "power_tables": tables, "hier_superiority_table_sizing": sup_table,
        "recommendation": rec_block,
        "provenance": {"script_sha256": hashlib.sha256(here.read_bytes()).hexdigest(),
                       "python": sys.version.split()[0], "python_full": sys.version,
                       "implementation": sys.implementation.name, **state},
    }
    a.out.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"recommendation": rec_block, "max_size_score": max_size, "size_summary": size_summary,
                      "power_tables": tables, "hier_superiority_table_sizing": sup_table,
                      "validation": valid}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
