#!/usr/bin/env python3
"""Power for the alias boundary confirmation (PREREG.md §3), under the record-clustered design.
Standard library only; seeded; no study outcome is read.

    python3 -I -B power.py [--out power.json] [--sims 5000]

**Design simulated** (PREREG §2–§4). Each eligible stage-A *agreed answers* pair is judged twice by the
frozen judge: against its boundary-respecting control chunk (X = 1 when the final label is *not*) and
against the identical stage-A window (Y = 1 when *not*). The draw takes at most **one pair per
(record, arm)**, so a record (a question) can contribute up to three pairs, one per corpus arm. The
**primary test** is the exact one-sided sign-flip test over records: per record j the score
S_j = sum over its pairs of (x_i - y_i); T = sum_j S_j; under the sharp null each S_j's sign is a fair
coin; p = P(T* >= T) (``chunktrial_stats.sign_flip_test_exact``, alternative "greater", whose exact p
is by enumeration or lattice convolution; here the same null distribution is computed by convolving the
binomial distributions of the records grouped by |S_j|, which is exact). The **pre-specified
sensitivity** is the exact one-sided McNemar test on the pooled discordant pairs. Both reject at
p <= 0.05.

**The draw, simulated as the rule specifies.** The pool's record x arm structure is embedded below as
anonymised counts (:data:`POOL_RECORDS`: per record, how many eligible pairs it has in base /
neighbour / random; 262 records, 1,871 pairs, 470 (record, arm) cells = 198 / 154 / 118). Quotas are
equal across the arms (remainder to base, then neighbour; a short arm's shortfall to the others, as
``alias_study_judge.drift_allocation``). Within an arm, pairs are visited in a seeded hash order and a
pair is taken when its (record, arm) cell is free: the first pair of a record to appear is the minimum
of its pairs' hashes, so a record with w eligible pairs in the arm is reached with probability
proportional to w — successive sampling proportional to size, which the simulation reproduces with
Efraimidis-Spirakis keys (u^(1/w)). The one-pair-per-window rule of the draw (PREREG §4.3) is **not**
simulated: shared windows lie within an arm, and the achievable cells under that rule are 197 / 154 /
118 against the owner's quotas of 117 / 117 / 117. Each drawn pair is then dropped independently with
probability :data:`EXCLUSION` (the pre-judging exclusions the oversample is for), and the realised n is
what remains; its distribution is reported.

**Outcome model.** Pair-level marginals pX (control flips) and pY (retest flips) with the phi
coefficient between X and Y on one pair:

    p11 = pX*pY + phi*sqrt(pX(1-pX) pY(1-pY)),  p10 = pX - p11,  p01 = pY - p11.

phi = 0 is the sizing case: among non-negative phi it gives the most discordant pairs and the smallest
share of them in the hypothesised direction, so the least power for a fixed pX - pY (a negative phi —
a pair that flips under one cut being *less* likely to flip under the other — has no mechanism here;
both arms re-judge the same borderline passage). **Within-record correlation** rho: with probability
rho every pair of a record shares one (X, Y) draw; otherwise its pairs draw independently. The
intra-record correlation of X, of Y and of X - Y is then rho. The sizing case is **rho = 0.5, assumed,
not estimated**; 0, 0.3 and 1.0 are sensitivities (rho = 1 means a record is one unit, the worst case
for the design effect).

**Outputs** (``power.json``): the sizing scenario on a grid of draw sizes n_draw up to the whole
availability (470), each cell with both tests' power, the realised-n distribution and the cluster
structure (records drawn in 1 / 2 / 3 arms); the 90 % and 80 % grid draw sizes (the smallest grid value
at which sign-flip power reaches the target and stays there at every larger grid value) and whether
90 % is reachable at all; **the owner's draw size** :data:`N_DRAW_OWNER` with its cell, the floor
:data:`N_FLOOR` on the realised n with the share of simulated studies that fall below it; **a
power-by-realised-n curve** (exact realised sizes, no exclusion) from which the result sentence
interpolates the power at the realised n; every sensitivity scenario at the owner's draw (the null
scenario shows each test's size under within-record correlation); the sizing grid again at rho 0 and
0.3; the minimum detectable pX (80 % power) at the owner's draw on a 0.5-point grid; and, for
continuity with the earlier draft, the exact McNemar n for independent pairs (one pair per record)
computed analytically. Every cell is its own simulation with its own slice of the seeded stream, so the
same design point simulated in two places (for example n_draw 351 in the grid and as the sizing
scenario's owner cell) gives two independent Monte-Carlo estimates that differ by sampling error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import subprocess
import sys
from pathlib import Path

ALPHA = 0.05
EXCLUSION = 0.05
TARGETS = (0.80, 0.90)
SEED_STRING = "261105|alias|confirm|power"
ARMS = ("base", "neighbour", "random")
N_DRAW_GRID = (240, 260, 280, 300, 316, 334, 351, 370, 390, 410, 430, 450, 470)
#: the owner's draw size (2026-11-05, v3; PREREG §3.3) and the harness-failure floor on the realised n
N_DRAW_OWNER = 351
N_FLOOR = 316  # 0.90 x 351, rounded down (PREREG §8 gate 1)
#: the power-by-realised-n curve: exact realised sizes (no exclusion), for the result sentence
REALISED_GRID = tuple(sorted(set(range(290, 356, 5)) | {N_FLOOR, 333}))
MDE_PX_GRID = (0.100, 0.105, 0.110, 0.115, 0.120, 0.125, 0.130, 0.135, 0.140)
DEFAULT_SIMS = 5000

#: planning scenarios: (label, pX, pY, phi, rho). The first is the sizing case.
SCENARIOS = [
    ("sizing: 13 % vs 6 %, phi 0, rho 0.5", 0.13, 0.06, 0.0, 0.5),
    ("13 % vs 6 %, phi 0, rho 0", 0.13, 0.06, 0.0, 0.0),
    ("13 % vs 6 %, phi 0, rho 0.3", 0.13, 0.06, 0.0, 0.3),
    ("13 % vs 6 %, phi 0, rho 1.0", 0.13, 0.06, 0.0, 1.0),
    ("13 % vs 6 %, phi 0.2, rho 0.5", 0.13, 0.06, 0.2, 0.5),
    ("13 % vs 6 %, phi 0.4, rho 0.5", 0.13, 0.06, 0.4, 0.5),
    ("smaller effect: 11 % vs 6 %, phi 0, rho 0.5", 0.11, 0.06, 0.0, 0.5),
    ("noisier null: 13 % vs 8 %, phi 0, rho 0.5", 0.13, 0.08, 0.0, 0.5),
    ("10 % vs 6 %, phi 0, rho 0.5", 0.10, 0.06, 0.0, 0.5),
    ("null: 6 % vs 6 %, phi 0, rho 0.5 (size check)", 0.06, 0.06, 0.0, 0.5),
    ("reference only (selected, not for sizing): 18.7 % vs 4.0 %, phi 0, rho 0.5", 0.187, 0.04, 0.0, 0.5),
]
SIZING = SCENARIOS[0]

#: the eligible pool's record x arm structure, anonymised (PREREG §4.1): per record, the number of
#: eligible pairs in base / neighbour / random. Counts only; no id. 262 records, 1,871 pairs.
POOL_RECORDS: list[tuple[int, int, int]] = [tuple(r) for r in [  # type: ignore[misc]
    [22,13,16],[19,7,9],[17,5,16],[16,7,20],[16,6,21],[16,6,18],[15,1,7],[12,11,9],[12,5,12],[12,0,0],[10,1,8],
    [10,0,5],[9,6,14],[9,3,11],[9,0,0],[8,6,7],[8,0,0],[8,0,0],[7,9,11],[7,9,9],[7,6,8],[7,5,14],[7,5,12],[7,5,0],
    [7,3,13],[7,1,3],[7,0,0],[7,0,0],[6,8,21],[6,8,10],[6,7,3],[6,6,10],[6,5,15],[6,4,0],[6,3,0],[6,1,6],[6,1,0],
    [6,1,0],[6,0,8],[6,0,2],[6,0,0],[6,0,0],[6,0,0],[6,0,0],[5,9,9],[5,5,0],[5,5,0],[5,4,7],[5,4,6],[5,4,6],[5,3,7],
    [5,3,1],[5,3,0],[5,2,0],[5,2,0],[5,2,0],[5,1,2],[5,0,0],[5,0,0],[5,0,0],[5,0,0],[4,8,5],[4,4,13],[4,4,6],[4,2,6],
    [4,2,1],[4,2,0],[4,1,5],[4,1,0],[4,0,5],[4,0,2],[4,0,0],[4,0,0],[4,0,0],[4,0,0],[4,0,0],[4,0,0],[4,0,0],[4,0,0],
    [4,0,0],[4,0,0],[3,7,1],[3,5,0],[3,5,0],[3,4,0],[3,4,0],[3,3,3],[3,2,4],[3,2,0],[3,1,8],[3,1,0],[3,1,0],[3,1,0],
    [3,0,1],[3,0,0],[3,0,0],[3,0,0],[2,12,0],[2,8,11],[2,4,0],[2,4,0],[2,3,2],[2,3,0],[2,2,10],[2,2,3],[2,2,2],
    [2,2,2],[2,2,0],[2,1,6],[2,1,5],[2,1,4],[2,1,4],[2,1,1],[2,1,0],[2,1,0],[2,1,0],[2,0,7],[2,0,2],[2,0,2],[2,0,1],
    [2,0,1],[2,0,0],[2,0,0],[2,0,0],[2,0,0],[2,0,0],[2,0,0],[2,0,0],[2,0,0],[2,0,0],[2,0,0],[2,0,0],[2,0,0],[2,0,0],
    [2,0,0],[2,0,0],[2,0,0],[1,8,17],[1,7,0],[1,6,3],[1,6,0],[1,5,6],[1,4,0],[1,4,0],[1,3,0],[1,2,11],[1,2,1],
    [1,2,1],[1,2,0],[1,2,0],[1,2,0],[1,2,0],[1,2,0],[1,1,2],[1,1,2],[1,1,1],[1,1,1],[1,1,0],[1,1,0],[1,1,0],[1,1,0],
    [1,0,11],[1,0,7],[1,0,6],[1,0,4],[1,0,1],[1,0,1],[1,0,1],[1,0,0],[1,0,0],[1,0,0],[1,0,0],[1,0,0],[1,0,0],
    [1,0,0],[1,0,0],[1,0,0],[1,0,0],[1,0,0],[1,0,0],[1,0,0],[1,0,0],[1,0,0],[1,0,0],[1,0,0],[1,0,0],[1,0,0],[1,0,0],
    [1,0,0],[1,0,0],[1,0,0],[1,0,0],[1,0,0],[1,0,0],[1,0,0],[1,0,0],[1,0,0],[1,0,0],[0,6,8],[0,5,3],[0,5,1],[0,5,0],
    [0,4,0],
    [0,3,5],[0,3,3],[0,3,0],[0,3,0],[0,3,0],[0,3,0],[0,3,0],[0,2,4],[0,2,2],[0,2,1],[0,2,1],[0,2,0],[0,2,0],[0,2,0],
    [0,2,0],[0,2,0],[0,2,0],[0,1,5],[0,1,4],[0,1,3],[0,1,2],[0,1,1],[0,1,1],[0,1,1],[0,1,1],[0,1,1],[0,1,1],[0,1,0],
    [0,1,0],[0,1,0],[0,1,0],[0,1,0],[0,1,0],[0,1,0],[0,1,0],[0,1,0],[0,1,0],[0,1,0],[0,1,0],[0,1,0],[0,1,0],[0,1,0],
    [0,0,5],[0,0,5],[0,0,4],[0,0,4],[0,0,4],[0,0,3],[0,0,2],[0,0,2],[0,0,2],[0,0,1],[0,0,1],[0,0,1],[0,0,1],[0,0,1],
    [0,0,1],[0,0,1],[0,0,1]]]
AVAILABLE = {a: sum(1 for r in POOL_RECORDS if r[i] > 0) for i, a in enumerate(ARMS)}


def seed_int(seed: str) -> int:
    """The project's rule: the first 8 bytes of sha256(seed string), big-endian."""
    return int.from_bytes(hashlib.sha256(seed.encode("utf-8")).digest()[:8], "big")


def joint(px: float, py: float, phi: float) -> tuple[float, float, float]:
    p11 = px * py + phi * math.sqrt(px * (1 - px) * py * (1 - py))
    p10, p01 = px - p11, py - p11
    if p11 < 0 or p10 < 0 or p01 < 0 or p10 + p01 > 1:
        raise ValueError(f"impossible joint for px={px}, py={py}, phi={phi}")
    return p10, p01, p11


# ------------------------------------------------------------------ the draw rule

def quotas(n: int, available: dict[str, int]) -> dict[str, int]:
    """Equal across arms, remainder to base then neighbour; a short arm gives its shortfall to the
    others (``alias_study_judge.drift_allocation``'s rule)."""
    left = dict(available)
    out = dict.fromkeys(ARMS, 0)
    todo = n
    open_arms = list(ARMS)
    while todo > 0 and open_arms:
        q, rem = divmod(todo, len(open_arms))
        want = {a: q + (1 if i < rem else 0) for i, a in enumerate(open_arms)}
        todo = 0
        nxt = []
        for a in open_arms:
            take = min(want[a], left[a])
            out[a] += take
            left[a] -= take
            todo += want[a] - take
            if left[a] > 0:
                nxt.append(a)
        open_arms = nxt
    if todo > 0:
        raise ValueError(f"n={n} exceeds the capped availability {available}")
    return out


def draw(rng: random.Random, n_draw: int) -> list[list[int]]:
    """One draw: the list of records drawn, each as the list of arm indices it contributes."""
    q = quotas(n_draw, AVAILABLE)
    picked: dict[int, list[int]] = {}
    for i, a in enumerate(ARMS):
        keyed = [(rng.random() ** (1.0 / r[i]), j) for j, r in enumerate(POOL_RECORDS) if r[i] > 0]
        keyed.sort(reverse=True)
        for _, j in keyed[: q[a]]:
            picked.setdefault(j, []).append(i)
    return list(picked.values())


# ------------------------------------------------------------------ the two tests

def log_binom_pmf(k: int, n: int, p: float) -> float:
    if p <= 0.0:
        return 0.0 if k == 0 else -math.inf
    if p >= 1.0:
        return 0.0 if k == n else -math.inf
    return (math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
            + k * math.log(p) + (n - k) * math.log1p(-p))


def binom_sf(k: int, n: int, p: float) -> float:
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    return sum(math.exp(log_binom_pmf(j, n, p)) for j in range(k, n + 1))


def mcnemar_p_greater(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    return sum(math.comb(n, i) for i in range(b, n + 1)) / 2 ** n


_GROUP: dict[tuple[int, int], tuple[int, list[float]]] = {}


def group_pmf(s: int, c: int) -> tuple[int, list[float]]:
    """The distribution of s*(2B - c), B ~ Binomial(c, 1/2): (offset, pmf over offset + 2*s*j)."""
    key = (s, c)
    if key not in _GROUP:
        pm = [math.comb(c, j) / 2 ** c for j in range(c + 1)]
        _GROUP[key] = (-s * c, pm)
    return _GROUP[key]


def sign_flip_p_greater(scores: list[int]) -> float:
    """Exact P(T* >= T) under per-record sign symmetry, by convolving the |S| groups."""
    t_obs = sum(scores)
    counts: dict[int, int] = {}
    for s in scores:
        if s:
            counts[abs(s)] = counts.get(abs(s), 0) + 1
    if not counts:
        return 1.0
    dist = {0: 1.0}
    for s, c in sorted(counts.items()):
        off, pm = group_pmf(s, c)
        nxt: dict[int, float] = {}
        for v, p in dist.items():
            for j, pj in enumerate(pm):
                w = v + off + 2 * s * j
                nxt[w] = nxt.get(w, 0.0) + p * pj
        dist = nxt
    return sum(p for v, p in dist.items() if v >= t_obs)


# ------------------------------------------------------------------ simulation

def percentile(sorted_vals: list[int], q: float) -> float:
    """Linear-interpolation percentile (numpy's default) of an ascending list."""
    if not sorted_vals:
        return float("nan")
    pos = q * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def simulate(rng: random.Random, n_draw: int, px: float, py: float, phi: float, rho: float, sims: int,
             exclusion: float = EXCLUSION) -> dict[str, object]:
    p10, p01, p11 = joint(px, py, phi)
    cum = (p11, p11 + p10, p11 + p10 + p01)

    def one_pair() -> tuple[int, int]:
        u = rng.random()
        if u < cum[0]:
            return 1, 1
        if u < cum[1]:
            return 1, 0
        if u < cum[2]:
            return 0, 1
        return 0, 0

    rej_sf = rej_mc = 0
    n_rec = n_multi = 0
    arms_hist = [0, 0, 0, 0]
    realised: list[int] = []
    for _ in range(sims):
        recs = draw(rng, n_draw)
        scores = []
        b = c = 0
        n_real = 0
        for arms in recs:
            kept = [a for a in arms if exclusion <= 0.0 or rng.random() >= exclusion]
            if not kept:
                continue
            n_real += len(kept)
            n_rec += 1
            arms_hist[len(kept)] += 1
            n_multi += len(kept) > 1
            if rng.random() < rho:
                x, y = one_pair()
                outs = [(x, y)] * len(kept)
            else:
                outs = [one_pair() for _ in kept]
            s = 0
            for x, y in outs:
                s += x - y
                b += x == 1 and y == 0
                c += x == 0 and y == 1
            scores.append(s)
        realised.append(n_real)
        rej_sf += sign_flip_p_greater(scores) <= ALPHA
        rej_mc += mcnemar_p_greater(b, c) <= ALPHA
    realised.sort()
    return {"power_sign_flip": rej_sf / sims, "power_mcnemar": rej_mc / sims,
            "mean_realised_n": round(sum(realised) / sims, 1),
            "realised_n": {"min": realised[0], "p1": round(percentile(realised, 0.01), 1),
                           "p5": round(percentile(realised, 0.05), 1), "p50": round(percentile(realised, 0.5), 1),
                           "p95": round(percentile(realised, 0.95), 1), "max": realised[-1]},
            "share_realised_below_floor": round(sum(n < N_FLOOR for n in realised) / sims, 4),
            "mean_records": round(n_rec / sims, 1),
            "mean_records_in_2_or_3_arms": round(n_multi / sims, 1),
            "mean_records_by_arms_drawn": {str(k): round(arms_hist[k] / sims, 1) for k in (1, 2, 3)},
            "exclusion": exclusion, "sims": sims,
            "mc_se_at_0.9": round(math.sqrt(0.9 * 0.1 / sims), 4), "mc_se_at_0.8": round(math.sqrt(0.8 * 0.2 / sims), 4)}


# ------------------------------------------------------------------ the analytic reference (one pair per record)

_KCRIT: dict[int, int] = {}


def k_crit(m: int) -> int:
    if m not in _KCRIT:
        k = m + 1
        for bb in range(m, -1, -1):
            if binom_sf(bb, m, 0.5) <= ALPHA:
                k = bb
            else:
                break
        _KCRIT[m] = k
    return _KCRIT[m]


def exact_mcnemar_power(n: int, p10: float, p01: float) -> float:
    psi = p10 + p01
    pi = p10 / psi
    total = 0.0
    for m in range(0, n + 1):
        pm = math.exp(log_binom_pmf(m, n, psi))
        if pm < 1e-15:
            continue
        kc = k_crit(m)
        if kc <= m:
            total += pm * binom_sf(kc, m, pi)
    return total


def smallest_stable_n(p10: float, p01: float, target: float, window: int = 25) -> int | None:
    n = 20
    while n <= 2000:
        if exact_mcnemar_power(n, p10, p01) >= target and \
                all(exact_mcnemar_power(n + j, p10, p01) >= target for j in range(1, window + 1)):
            return n
        n += 1
    return None


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


def stable_n(grid: dict[str, dict[str, object]], target: float) -> int | None:
    """The smallest grid draw size at which sign-flip power reaches the target and stays at or above it
    for every larger grid size (so Monte-Carlo noise cannot pick a lucky point)."""
    return next((n for i, n in enumerate(N_DRAW_GRID)
                 if all(float(grid[str(m)]["power_sign_flip"]) >= target for m in N_DRAW_GRID[i:])), None)  # type: ignore[arg-type]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "power.json")
    ap.add_argument("--sims", type=int, default=DEFAULT_SIMS)
    a = ap.parse_args(argv)
    here = Path(__file__).resolve()
    state = git_state(here.parent)  # read before anything is written
    rng = random.Random(seed_int(SEED_STRING))

    label0, px0, py0, phi0, rho0 = SIZING
    grid = {str(n): simulate(rng, n, px0, py0, phi0, rho0, a.sims) for n in N_DRAW_GRID}
    n_draw90, n_draw80 = stable_n(grid, 0.90), stable_n(grid, 0.80)
    feasible90 = n_draw90 is not None and n_draw90 <= sum(AVAILABLE.values())
    owner_cell = grid[str(N_DRAW_OWNER)]

    scen = []
    for label, px, py, phi, rho in SCENARIOS:
        row: dict[str, object] = {"label": label, "px_control_flip": px, "py_retest_flip": py, "phi": phi, "rho": rho}
        p10, p01, p11 = joint(px, py, phi)
        row.update({"p10": round(p10, 6), "p01": round(p01, 6), "p11": round(p11, 6),
                    "discordant_share": round(p10 + p01, 6),
                    "at_n_draw_owner": simulate(rng, N_DRAW_OWNER, px, py, phi, rho, a.sims)})
        scen.append(row)

    # the minimum detectable control rate (80 % power) at the owner's draw, on a 0.5-point grid
    curve = {}
    for px in MDE_PX_GRID:
        curve[f"{px:.3f}"] = simulate(rng, N_DRAW_OWNER, px, py0, phi0, rho0, a.sims)["power_sign_flip"]
    hit = next((px for px in MDE_PX_GRID if float(curve[f"{px:.3f}"]) >= 0.80), None)  # type: ignore[arg-type]
    mde = {"n_draw": N_DRAW_OWNER, "py": py0, "phi": phi0, "rho": rho0, "power": 0.80, "grid_step": 0.005,
           "px": hit, "excess_points": None if hit is None else round(100 * (hit - py0), 2), "power_by_px": curve}

    # the sizing grid again at rho 0 and 0.3: what within-record correlation would make 90 % reachable
    by_rho: dict[str, object] = {}
    for rho in (0.0, 0.3):
        g = {str(n): simulate(rng, n, px0, py0, phi0, rho, a.sims) for n in N_DRAW_GRID}
        by_rho[f"{rho:.1f}"] = {"power_sign_flip_by_n_draw": {k: v["power_sign_flip"] for k, v in g.items()},
                                "n_draw_90": stable_n(g, 0.90)}

    # power by realised n: exact realised sizes (no exclusion), sizing scenario; the result sentence
    # interpolates linearly between the two grid points around the realised n
    realised_curve = {str(n): {k: simulate(rng, n, px0, py0, phi0, rho0, a.sims, exclusion=0.0)[k]
                               for k in ("power_sign_flip", "power_mcnemar")} for n in REALISED_GRID}

    p10, p01, _ = joint(px0, py0, phi0)
    ref = {"design": "one pair per record, independent pairs, exact McNemar (analytic)",
           "n_exact": {f"{t:.2f}": smallest_stable_n(p10, p01, t) for t in TARGETS}}

    out = {
        "schema": "alias-boundary-confirm-power/3",
        "alpha_one_sided": ALPHA, "exclusion": EXCLUSION, "targets": list(TARGETS),
        "primary_test": "exact one-sided sign-flip over records on S_j = sum(x_i - y_i), p = P(T* >= T)",
        "sensitivity_test": "exact one-sided McNemar on the pooled discordant pairs",
        "draw_rule": "one pair per (record, arm); equal arm quotas, remainder base then neighbour, shortfall "
                     "to the other arms; within an arm records reached with probability proportional to "
                     "their eligible pair count; the one-pair-per-window rule is not simulated",
        "pool": {"records": len(POOL_RECORDS), "pairs": sum(sum(r) for r in POOL_RECORDS),
                 "record_arm_cells": AVAILABLE, "records_in_arms": {
                     str(k): sum(1 for r in POOL_RECORDS if sum(v > 0 for v in r) == k) for k in (1, 2, 3)}},
        "seed_string": SEED_STRING, "seed_int": seed_int(SEED_STRING), "sims": a.sims,
        "mc_se": {"at_0.9": round(math.sqrt(0.9 * 0.1 / a.sims), 4), "at_0.8": round(math.sqrt(0.8 * 0.2 / a.sims), 4)},
        "sizing_scenario": label0, "n_draw_grid": list(N_DRAW_GRID),
        "sizing_power_by_n_draw": grid,
        "n_draw_90_grid": n_draw90, "n_draw_80_grid": n_draw80, "feasible_90_within_availability": feasible90,
        "max_n_draw": sum(AVAILABLE.values()),
        "n_draw_owner": N_DRAW_OWNER, "owner_decision": "n_draw 351, equal arms 117 / 117 / 117 (owner, 2026-11-05, v3)",
        "quotas_at_owner_draw": quotas(N_DRAW_OWNER, AVAILABLE),
        "owner_cell": owner_cell,
        "n_floor": N_FLOOR, "n_floor_rule": "0.90 x n_draw, rounded down",
        "power_at_floor": realised_curve[str(N_FLOOR)],
        "share_realised_below_floor_at_owner_draw": owner_cell["share_realised_below_floor"],
        "power_by_realised_n": realised_curve,
        "sizing_power_by_n_draw_by_rho": by_rho,
        "mde": mde, "scenarios": scen, "analytic_reference": ref,
        "provenance": {"script_sha256": hashlib.sha256(here.read_bytes()).hexdigest(),
                       "python": sys.version.split()[0], "python_full": sys.version,
                       "implementation": sys.implementation.name, **state},
    }
    a.out.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"n_draw_owner": N_DRAW_OWNER, "quotas": out["quotas_at_owner_draw"],
                      "owner_cell": {k: owner_cell[k] for k in ("power_sign_flip", "power_mcnemar", "mean_realised_n",
                                                                "realised_n", "share_realised_below_floor",
                                                                "mean_records", "mean_records_by_arms_drawn")},
                      "n_floor": N_FLOOR, "power_at_floor": out["power_at_floor"],
                      "n_draw_90_grid": n_draw90, "n_draw_80_grid": n_draw80, "feasible_90": feasible90,
                      "mde": {k: mde[k] for k in ("px", "excess_points")},
                      "n_draw_90_by_rho": {k: v["n_draw_90"] for k, v in by_rho.items()},  # type: ignore[index]
                      "sizing_power_by_n_draw": {n: (g["power_sign_flip"], g["power_mcnemar"], g["mean_realised_n"])
                                                 for n, g in grid.items()},
                      "power_by_realised_n": realised_curve,
                      "scenarios": [{"label": r["label"],
                                     "sf": r["at_n_draw_owner"]["power_sign_flip"],  # type: ignore[index]
                                     "mc": r["at_n_draw_owner"]["power_mcnemar"]}  # type: ignore[index]
                                    for r in scen],
                      "analytic_reference": ref}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
