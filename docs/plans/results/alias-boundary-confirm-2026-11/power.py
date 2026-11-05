#!/usr/bin/env python3
"""Paired power for the alias boundary confirmation (PREREG.md §3). Standard library only.

    python3 power.py [--out power.json]

**Design.** Each fresh stage-A *agreed answers* pair is judged twice by the frozen judge: against
its boundary-respecting control chunk (X = 1 when the final label is *not*) and against the identical
stage-A window (Y = 1 when the final label is *not*). The primary test is the one-sided exact McNemar
test on the discordant pairs: with b = #(X=1, Y=0) and c = #(X=0, Y=1), under H0 b ~ Binomial(b+c, 1/2),
and H1 is b > c (the control loses *answers* more often than the retest does). The test rejects when
P(Binomial(b+c, 1/2) >= b) <= alpha.

**Discordance model.** The marginal flip rates pX (control) and pY (retest) are planning assumptions,
set below the exploratory point estimates (ADDENDUM-2026-11-05, 18.7 % and 4.0 % on the selected
stratum; 13.3 % and 6.7 % overall) because those were read after the fact. Their joint distribution is
set by the phi coefficient between X and Y:

    p11 = pX*pY + phi*sqrt(pX(1-pX) pY(1-pY)),  p10 = pX - p11,  p01 = pY - p11,  psi = p10 + p01.

phi = 0 (independence) gives the most discordant pairs and the smallest share of them in the
hypothesised direction, so it is the conservative case for a fixed pX - pY; a positive phi (the same
borderline pairs flip in both arms) makes the test more powerful. Both are reported.

**Exact power.** For n pairs, m = b + c ~ Binomial(n, psi) and b | m ~ Binomial(m, p10/psi), so

    power(n) = sum_m P(m) * P(Binomial(m, p10/psi) >= k_crit(m)),  k_crit(m) = min k: P(Bin(m,1/2) >= k) <= alpha.

No simulation, no RNG, no seed: the numbers are exact to floating point and reproducible byte for byte.

**Outputs** (``power.json``): for each scenario the smallest n with exact power >= 0.80 and >= 0.90
(the smallest n after which power never again drops below the target within the next 25 pairs, so a
sawtooth dip of the discrete test cannot pick a lucky n), the normal-approximation n beside it, the
power of the chosen n under every scenario, the minimum detectable pX at the chosen n (pY fixed, phi 0,
80 % power), and the draw size with the 5 % oversample for exclusions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

ALPHA = 0.05
TARGETS = (0.80, 0.90)
OVERSAMPLE = 0.05
Z_ALPHA = 1.6448536269514722  # one-sided 0.05
Z_BETA = {0.80: 0.8416212335729143, 0.90: 1.2815515655446004}
STABLE_WINDOW = 25  # power must stay >= target for this many further n

#: planning scenarios: (label, pX control flip, pY retest flip, phi). The first is the sizing case.
SCENARIOS = [
    ("primary: 13 % vs 6 %, independent", 0.13, 0.06, 0.0),
    ("primary marginals, phi 0.2", 0.13, 0.06, 0.2),
    ("primary marginals, phi 0.4", 0.13, 0.06, 0.4),
    ("smaller effect: 11 % vs 6 %, independent", 0.11, 0.06, 0.0),
    ("smaller effect: 11 % vs 6 %, phi 0.2", 0.11, 0.06, 0.2),
    ("noisier null: 13 % vs 8 %, independent", 0.13, 0.08, 0.0),
    ("noisier null: 13 % vs 8 %, phi 0.2", 0.13, 0.08, 0.2),
    ("10 % vs 6 %, independent", 0.10, 0.06, 0.0),
    ("reference only (selected, not for sizing): 18.7 % vs 4.0 %, independent", 0.187, 0.04, 0.0),
]
#: the sizing scenario and the power target the chosen n is read from (PREREG.md §3.3)
SIZING = SCENARIOS[0]
SIZING_TARGET = 0.90


def joint(px: float, py: float, phi: float) -> tuple[float, float, float]:
    """(p10, p01, p11) from the marginals and the phi coefficient; refuses an impossible joint."""
    p11 = px * py + phi * math.sqrt(px * (1 - px) * py * (1 - py))
    p10, p01 = px - p11, py - p11
    if p11 < 0 or p10 < 0 or p01 < 0 or p10 + p01 > 1:
        raise ValueError(f"impossible joint for px={px}, py={py}, phi={phi}")
    return p10, p01, p11


def log_binom_pmf(k: int, n: int, p: float) -> float:
    if p <= 0.0:
        return 0.0 if k == 0 else -math.inf
    if p >= 1.0:
        return 0.0 if k == n else -math.inf
    return (math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
            + k * math.log(p) + (n - k) * math.log1p(-p))


def binom_sf(k: int, n: int, p: float) -> float:
    """P(Binomial(n, p) >= k)."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    return sum(math.exp(log_binom_pmf(j, n, p)) for j in range(k, n + 1))


_KCRIT: dict[int, int] = {}


def k_crit(m: int, alpha: float = ALPHA) -> int:
    """The smallest b such that P(Binomial(m, 1/2) >= b) <= alpha; m + 1 when no b rejects."""
    if m not in _KCRIT:
        k = m + 1
        for b in range(m, -1, -1):
            if binom_sf(b, m, 0.5) <= alpha:
                k = b
            else:
                break
        _KCRIT[m] = k
    return _KCRIT[m]


def exact_power(n: int, p10: float, p01: float, alpha: float = ALPHA) -> float:
    psi = p10 + p01
    pi = p10 / psi
    total = 0.0
    for m in range(0, n + 1):
        pm = math.exp(log_binom_pmf(m, n, psi))
        if pm < 1e-15:
            continue
        kc = k_crit(m, alpha)
        if kc > m:
            continue
        total += pm * binom_sf(kc, m, pi)
    return total


def normal_n(p10: float, p01: float, target: float) -> int:
    """The textbook normal-approximation n for McNemar (Connor 1987 form), one-sided alpha."""
    psi, d = p10 + p01, p10 - p01
    num = (Z_ALPHA * math.sqrt(psi) + Z_BETA[target] * math.sqrt(psi - d * d)) ** 2
    return math.ceil(num / (d * d))


def smallest_stable_n(p10: float, p01: float, target: float, start: int = 20, stop: int = 2000) -> int | None:
    """The smallest n with exact power >= target that holds for the next STABLE_WINDOW values of n."""
    n = start
    while n <= stop:
        if exact_power(n, p10, p01) >= target:
            if all(exact_power(n + j, p10, p01) >= target for j in range(1, STABLE_WINDOW + 1)):
                return n
        n += 1
    return None


def mde_px(n: int, py: float, phi: float, target: float = 0.80) -> float:
    """The smallest control flip rate pX detectable with power >= target at n pairs (bisection)."""
    lo, hi = py + 1e-6, 0.95
    for _ in range(40):
        mid = (lo + hi) / 2
        p10, p01, _ = joint(mid, py, phi)
        if exact_power(n, p10, p01) >= target:
            hi = mid
        else:
            lo = mid
    return hi


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
    a = ap.parse_args(argv)
    here = Path(__file__).resolve()

    rows = []
    for label, px, py, phi in SCENARIOS:
        p10, p01, p11 = joint(px, py, phi)
        row = {"label": label, "px_control_flip": px, "py_retest_flip": py, "phi": phi,
               "p10": round(p10, 6), "p01": round(p01, 6), "p11": round(p11, 6),
               "discordant_share": round(p10 + p01, 6), "share_in_direction": round(p10 / (p10 + p01), 6),
               "n_exact": {}, "n_normal": {}}
        for t in TARGETS:
            row["n_exact"][f"{t:.2f}"] = smallest_stable_n(p10, p01, t)
            row["n_normal"][f"{t:.2f}"] = normal_n(p10, p01, t)
        rows.append(row)

    sizing = next(r for r in rows if r["label"] == SIZING[0])
    n_chosen = int(sizing["n_exact"][f"{SIZING_TARGET:.2f}"])
    n_80 = int(sizing["n_exact"]["0.80"])
    n_draw = math.ceil(n_chosen * (1 + OVERSAMPLE))
    for r in rows:
        r["power_at_chosen_n"] = round(exact_power(n_chosen, r["p10"], r["p01"]), 4)
        r["power_at_n80"] = round(exact_power(n_80, r["p10"], r["p01"]), 4)

    _, px0, py0, _ = SIZING
    mde = {"at_chosen_n": {"py": py0, "phi": 0.0, "power": 0.80, "px": round(mde_px(n_chosen, py0, 0.0), 4)},
           "at_n80": {"py": py0, "phi": 0.0, "power": 0.80, "px": round(mde_px(n_80, py0, 0.0), 4)}}
    for k in mde:
        mde[k]["excess_points"] = round(100 * (mde[k]["px"] - py0), 2)
    # the realised-n floor: the MDE curve the results quote when exclusions bite
    floor_curve = {str(n): round(mde_px(n, py0, 0.0), 4) for n in range(n_80, n_draw + 1, 10)}

    out = {
        "schema": "alias-boundary-confirm-power/1",
        "alpha_one_sided": ALPHA, "test": "exact McNemar on discordant pairs, one-sided (control > retest)",
        "targets": list(TARGETS), "stable_window": STABLE_WINDOW, "oversample": OVERSAMPLE,
        "sizing_scenario": SIZING[0], "sizing_target": SIZING_TARGET,
        "n_chosen": n_chosen, "n_80": n_80, "n_draw": n_draw,
        "mde": mde, "mde_px_by_realised_n": floor_curve,
        "scenarios": rows,
        "provenance": {"script_sha256": hashlib.sha256(here.read_bytes()).hexdigest(),
                       "python": sys.version.split()[0], **git_state(here.parent)},
    }
    a.out.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"n_chosen": n_chosen, "n_80": n_80, "n_draw": n_draw, "mde": mde,
                      "scenarios": [{"label": r["label"], "n_exact": r["n_exact"],
                                     "power_at_chosen_n": r["power_at_chosen_n"]} for r in rows]},
                     indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
