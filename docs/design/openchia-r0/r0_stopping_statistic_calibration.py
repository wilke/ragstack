#!/usr/bin/env python3
"""R0 — calibration of OpenChia's Episode stopping statistic on populations of KNOWN richness.

Independent reimplementation (stdlib only) of the documented estimator chain at
chian/OpenChia@98d74fb, numeric_control_library/{rarefaction,credit_assignment,continuation}.py,
for ONE result channel:

  component_alpha = alpha / (2*width + 1)                                   # Bonferroni, width = 1
  usability       = Wilson(observed, observed + failed; component_alpha)    # clamped to [0, 1]
  conditional     = f1/n point; Jeffreys-gamma shape f1+0.5, mean shape/n, var shape/n^2,
                    radius sqrt(var / component_alpha)                       # Chebyshev radius
  remaining       = bias-corrected Chao1 ((n-1)/n) f1 (f1-1) / (2 (f2+1)), delta-method variance,
                    radius sqrt(var / component_alpha); point = max(chao, conditional.value);
                    upper = max(point, chao + radius, conditional.upper)
  total           = count + remaining
  next_yield      = conditional * usability   (value*value, lower*lower, upper*upper)
  projected_next.upper = min(1, (count + next.upper) / max(1, total.lower, count+1))
                         - count / max(1, total.upper, count+1)
  STOP            iff projected_next.upper <= theta                        # predicted_credit_upper_bound

CHECK AGAINST A RECORDED NUMBER: epistemic_yield_acceptance.md (2026-10-02) reports a projected
upper bound of 0.749286267 after two units with one accepted identity seen twice. This script
recomputes that state first and prints both numbers; a mismatch would mean the reimplementation
is not the shipped statistic and the rest is void.

SCOPE OF THAT CHECK: at the recorded state f1 = 0, so the Chao1 point and variance are identically
zero (stop_statistic(1, 2, 0, 1) == stop_statistic(1, 2, 0, 0)). A match pins only the Wilson x
Jeffreys-gamma conditional next-yield x projection chain. The Chao1 term, which drives every
coverage result, is verified by line-by-line comparison against _remaining_richness in
numeric_control_library/rarefaction.py at 98d74fb, not by the recorded number.

The 'premature_stop_rate_gt10pct' below divides by the replicates that STOPPED at that theta,
not by all replicates.

NOT reimplemented: multi-channel conjunction, hypervolume over several columns, nano-graphrag's
separate 'preferential_incidence_estimator_v2'. Pre-registration: PREREG-R0-stopping-statistic-calibration.json.
"""
from __future__ import annotations

import json
import math
import random
import statistics
import sys
import time
from statistics import NormalDist

ALPHA = 0.05
WIDTH = 1
COMPONENT_ALPHA = ALPHA / (2 * WIDTH + 1)
THETAS = (0.01, 0.02, 0.05)
_Z = NormalDist().inv_cdf(1.0 - COMPONENT_ALPHA / 2.0)


def wilson(successes: int, trials: int) -> tuple[float, float, float] | None:
    if trials == 0:
        return None
    p = successes / trials
    z2 = _Z * _Z
    den = 1.0 + z2 / trials
    center = (p + z2 / (2.0 * trials)) / den
    radius = _Z * math.sqrt(p * (1.0 - p) / trials + z2 / (4.0 * trials**2)) / den
    return p, min(p, max(0.0, center - radius)), max(p, min(1.0, center + radius))


def conditional_next(f1: int, n: int) -> tuple[float, float, float] | None:
    if n == 0:
        return None
    point = f1 / n
    shape = f1 + 0.5
    mean = shape / n
    var = shape / (n * n)
    radius = math.sqrt(var / COMPONENT_ALPHA)
    return point, max(0.0, min(point, mean - radius)), max(point, mean + radius)


def remaining(f1: int, f2: int, n: int, cond: tuple[float, float, float]) -> tuple[float, float, float]:
    corr = (n - 1.0) / n
    chao = corr * f1 * max(0.0, f1 - 1.0) / (2.0 * (f2 + 1.0))
    d1 = corr * (2.0 * f1 - 1.0) / (2.0 * (f2 + 1.0))
    d2 = -corr * f1 * max(0.0, f1 - 1.0) / (2.0 * (f2 + 1.0) ** 2)
    var = d1 * d1 * f1 + d2 * d2 * f2
    radius = math.sqrt(max(0.0, var) / COMPONENT_ALPHA)
    point = max(chao, cond[0])
    lower = max(0.0, min(point, chao - radius))
    upper = max(point, chao + radius, cond[2])
    return point, lower, upper


def stop_statistic(count: int, n: int, f1: int, f2: int, failed: int = 0) -> tuple[float, tuple[float, float]] | None:
    """Return (projected_next.upper, (remaining.lower, remaining.upper)) or None if not ready."""
    if n == 0:
        return None
    usab = wilson(n, n + failed)
    cond = conditional_next(f1, n)
    rem = remaining(f1, f2, n, cond)
    total = (count + rem[0], count + rem[1], count + rem[2])
    next_upper = cond[2] * usab[2]
    projected_upper = min(1.0, (count + next_upper) / max(1.0, total[1], count + 1.0))
    current_lower = count / max(1.0, total[2], count + 1.0)
    return projected_upper - current_lower, (rem[1], rem[2])


# ------------------------------------------------------------------ the recorded-number check
def acceptance_check() -> dict:
    s = stop_statistic(count=1, n=2, f1=0, f2=1, failed=0)
    recorded = 0.749286267
    return {"recomputed": s[0], "recorded": recorded, "abs_diff": abs(s[0] - recorded),
            "match_to_1e-6": abs(s[0] - recorded) < 1e-6}


# ------------------------------------------------------------------ the simulation
def weights(K: int, sigma: float, rng: random.Random) -> list[float]:
    if sigma == 0.0:
        return [1.0] * K
    return [math.exp(rng.gauss(0.0, sigma)) for _ in range(K)]


def poisson(lam: float, rng: random.Random) -> int:
    # Knuth; lam small
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= L:
            return k - 1


def run_one(K: int, sigma: float, rng: random.Random, max_units: int) -> dict:
    w = weights(K, sigma, rng)
    cum, acc = [], 0.0
    for x in w:
        acc += x
        cum.append(acc)
    total_w = acc
    incidence = [0] * K
    count = n = f1 = f2 = 0
    stops = {t: None for t in THETAS}
    undiscovered_at_stop = {t: None for t in THETAS}
    cover_hits = cover_n = 0
    miss_above = miss_below = 0   # truth above the band's upper (estimator too low) vs below its lower
    first_two_stopped = False
    for unit in range(1, max_units + 1):
        m = poisson(5.0, rng)
        seen = set()
        for _ in range(m):
            r = rng.random() * total_w
            lo, hi = 0, K - 1
            while lo < hi:  # bisect on cum
                mid = (lo + hi) // 2
                if cum[mid] < r:
                    lo = mid + 1
                else:
                    hi = mid
            seen.add(lo)
        for i in seen:
            c = incidence[i]
            if c == 0:
                count += 1
            elif c == 1:
                f1 -= 1
            elif c == 2:
                f2 -= 1
            incidence[i] = c + 1
            if c + 1 == 1:
                f1 += 1
            elif c + 1 == 2:
                f2 += 1
        n += 1
        s = stop_statistic(count, n, f1, f2)
        upper, (rem_lo, rem_hi) = s
        true_remaining = K - count
        cover_n += 1
        if rem_lo <= true_remaining <= rem_hi:
            cover_hits += 1
        elif true_remaining > rem_hi:
            miss_above += 1
        else:
            miss_below += 1
        for t in THETAS:
            if stops[t] is None and upper <= t:
                stops[t] = unit
                undiscovered_at_stop[t] = true_remaining / K
                if t == 0.01 and unit <= 2:
                    first_two_stopped = True
        if all(v is not None for v in stops.values()):
            break
    return {"stops": stops, "undiscovered": undiscovered_at_stop,
            "coverage": cover_hits / max(1, cover_n), "units": n,
            "miss_above_rate": miss_above / max(1, cover_n), "miss_below_rate": miss_below / max(1, cover_n),
            "stopped_at_n_le_2_default": first_two_stopped,
            "discovered_fraction_end": count / K}


def main() -> int:
    out = {"experiment_id": "openchia-R0-stopping-statistic-calibration-v1", "alpha": ALPHA,
           "component_alpha": COMPONENT_ALPHA, "thetas": THETAS, "acceptance_check": acceptance_check(),
           "conditions": {}}
    print("acceptance-record check:", json.dumps(out["acceptance_check"]))
    if not out["acceptance_check"]["match_to_1e-6"]:
        print("!! reimplementation does not reproduce the recorded statistic; aborting", file=sys.stderr)
        json.dump(out, open(sys.argv[1], "w"), indent=1)
        return 2
    reps = int(sys.argv[2]) if len(sys.argv) > 2 else 300
    seed = 20261005
    t0 = time.time()
    for K in (50, 200, 1000):
        for name, sigma in (("homogeneous", 0.0), ("heterogeneous_mild", 1.0), ("heterogeneous_heavy", 2.0)):
            rng = random.Random(f"{seed}-{K}-{name}")
            max_units = 50 * K // 5
            runs = [run_one(K, sigma, rng, max_units) for _ in range(reps)]
            cond = {"K": K, "sigma": sigma, "replicates": reps, "max_units": max_units,
                    "coverage_mean": statistics.mean(r["coverage"] for r in runs),
                    "miss_above_mean": statistics.mean(r["miss_above_rate"] for r in runs),
                    "miss_below_mean": statistics.mean(r["miss_below_rate"] for r in runs),
                    "stopped_at_n_le_2_default_rate": statistics.mean(1.0 if r["stopped_at_n_le_2_default"] else 0.0 for r in runs),
                    "per_theta": {}}
            for t in THETAS:
                stopped = [r for r in runs if r["stops"][t] is not None]
                und = [r["undiscovered"][t] for r in stopped]
                cond["per_theta"][str(t)] = {
                    "never_stop_rate": 1.0 - len(stopped) / reps,
                    "stop_unit_median": statistics.median(r["stops"][t] for r in stopped) if stopped else None,
                    "undiscovered_at_stop_median": statistics.median(und) if und else None,
                    "undiscovered_at_stop_p90": (sorted(und)[int(0.9 * (len(und) - 1))] if und else None),
                    "premature_stop_rate_gt10pct": (sum(1 for u in und if u > 0.10) / len(stopped)) if stopped else None,
                }
            out["conditions"][f"K{K}_{name}"] = cond
            pt = cond["per_theta"]["0.01"]
            print(f"K={K:<5} {name:<20} coverage={cond['coverage_mean']:.3f}  theta=0.01: never_stop={pt['never_stop_rate']:.2f} "
                  f"stop_med={pt['stop_unit_median']}  undiscovered_med={pt['undiscovered_at_stop_median']}  "
                  f"premature(>10%)={pt['premature_stop_rate_gt10pct']}  [{time.time()-t0:.0f}s]", flush=True)
    json.dump(out, open(sys.argv[1], "w"), indent=1)
    print("wrote", sys.argv[1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
