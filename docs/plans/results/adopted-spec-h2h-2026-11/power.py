#!/usr/bin/env python3
"""Planning power for the adopted-spec head-to-head (PREREG.md §5). Standard library only,
no data read, deterministic (closed-form normal approximation; nothing is simulated).

    python3 -I -B power.py [--out power.json]

Paired binary outcome per question, n = 2,035 (the decision set). With discordance π (the
share of questions on which the two arms differ) and true difference d (as a proportion),
the paired mean difference D in hit@5 points has SE(D) = 100·√((π − d²)/n). A test on the
paired-bootstrap lower bound at two-sided 95 % is approximated by D − z·SE with z = 1.96:

* non-inferiority at the frozen margin M = 2.0 passes when D − z·SE ≥ −M,
  power = Φ((d·100 + M)/SE − z);
* superiority passes when D − z·SE > 0, power = Φ(d·100/SE − z).

The discordance rates are the Addendum C k = 5 values on the decision set (PREREG §5 table;
private score aggregates). A cell is ``null`` (printed *infeasible*) where |d|/100 > π: two
arms cannot differ by more than their discordance. The dry run of 2026-11-11 found the S and
S-regex chunk ids identical on the 40 sentinel documents, so H3's discordance may be far
below the 0.02 reference row: the rows at 0.005 and 0.001 show what that does — the true
difference is then bounded by 0.5 / 0.1 points, every feasible cell passes, and the
non-inferiority test cannot fail; the descriptive chunk-id overlap carries the finding.
"""
from __future__ import annotations

import argparse
import json
import math
import sys

N = 2035
M = 2.0
Z = 1.959964
Z_POWER = {"0.80": 0.841621, "0.90": 1.281552}
#: π (k = 5, decision set) per proxy pair, by setting — PREREG §5
PI = {
    "sent512s vs ft512o64s (proxy for S − ft512o64s, H1–H2)": {"a": 0.047, "d": 0.081, "b": 0.103, "c": 0.048},
    "ft512o64s vs ft512o64 (gate G1)": {"a": 0.103, "d": 0.172, "b": 0.224, "c": 0.106},
    "sent512s vs ft512o64 (C2)": {"a": 0.109, "d": 0.174, "b": 0.223, "c": 0.112},
    "sent512s vs sent512sh (text-only perturbation; reference for S-regex − S)": {"a": 0.020, "d": 0.044, "b": 0.076, "c": 0.000},
}


def phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def se(pi: float, d_pts: float, n: int = N) -> float:
    d = d_pts / 100
    return 100 * math.sqrt(max(pi - d * d, 1e-12) / n)


def feasible(pi: float, d_pts: float) -> bool:
    """Two arms cannot differ by more than their discordance: |d| ≤ π (as proportions)."""
    return abs(d_pts) / 100 <= pi + 1e-12


def ni_power(pi: float, d_pts: float, margin: float = M, z: float = Z, n: int = N) -> float | None:
    if not feasible(pi, d_pts):
        return None
    s = se(pi, d_pts, n)
    return phi((d_pts + margin) / s - z)


def sup_power(pi: float, d_pts: float, z: float = Z, n: int = N) -> float | None:
    if not feasible(pi, d_pts):
        return None
    s = se(pi, d_pts, n)
    return phi(d_pts / s - z)


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 3)


def half_width(pi: float, z: float = Z, n: int = N) -> float:
    return z * se(pi, 0.0, n)


def mde(pi: float, power: float, z: float = Z, n: int = N) -> float:
    """Minimal superiority detectable with the given power (points)."""
    return (z + Z_POWER[f"{power:.2f}"]) * se(pi, 0.0, n)


def tables() -> dict:
    hw = {f"{pi:.2f}" if pi >= 0.01 else f"{pi:.3f}": round(half_width(pi), 2)
          for pi in (0.001, 0.005, 0.02, 0.05, 0.10, 0.15, 0.20)}
    ni_d = (-2.0, -1.5, -1.0, -0.5, 0.0, 0.5)
    sup_d = (0.0, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5)
    rows = [
        {"test": "H1 NI at 2.0", "pi": 0.05, "powers": {f"{d:+.1f}": _r(ni_power(0.05, d)) for d in ni_d}},
        {"test": "H1 NI at 2.0", "pi": 0.10, "powers": {f"{d:+.1f}": _r(ni_power(0.10, d)) for d in ni_d}},
        {"test": "H2 superiority", "pi": 0.05, "powers": {f"{d:+.1f}": _r(sup_power(0.05, d)) for d in sup_d}},
        {"test": "G1 (ft512o64s − ft512o64 > 0)", "pi": 0.10, "powers": {f"{d:+.1f}": _r(sup_power(0.10, d)) for d in sup_d}},
        {"test": "H3 NI at 2.0", "pi": 0.02, "powers": {f"{d:+.1f}": _r(ni_power(0.02, d)) for d in ni_d}},
        {"test": "H3 NI at 2.0 (if the dry run's identical chunks generalise)", "pi": 0.005,
         "powers": {f"{d:+.1f}": _r(ni_power(0.005, d)) for d in ni_d}},
        {"test": "H3 NI at 2.0 (if the dry run's identical chunks generalise)", "pi": 0.001,
         "powers": {f"{d:+.1f}": _r(ni_power(0.001, d)) for d in ni_d}},
    ]
    m = {f"{pi:.2f}": {"0.80": round(mde(pi, 0.80), 2), "0.90": round(mde(pi, 0.90), 2)} for pi in (0.05, 0.10, 0.15, 0.20)}
    return {"schema": "adopted-spec-h2h-power/1", "method": "normal approximation, paired binary; SE = 100*sqrt((pi - d^2)/n)",
            "n": N, "margin": M, "z": Z, "discordance_inputs": PI, "half_width_95": hw, "power": rows,
            "minimal_detectable_superiority": m,
            "infeasible_cells": "null where |d|/100 > pi: two arms cannot differ by more than their discordance",
            "note_h3": ("with pi below 0.02 the true difference cannot exceed 100*pi points (0.5 at pi = 0.005), so H3 "
                        "cannot fail its 2.0-point margin at all: H3 can fail only if roughly 2% or more of the questions "
                        "(about 41) change hit status between S and S-regex; below that the chunk-id overlap carries the finding")}


def render(t: dict) -> str:
    out = ["half-width of the 95% paired CI at n = 2,035 (points), by discordance pi"]
    out += [f"  pi {k}: {v:.2f}" for k, v in t["half_width_95"].items()]
    for r in t["power"]:
        out.append(f"\n{r['test']}, pi {r['pi']}: " + "  ".join(f"d{d} " + ("infeasible" if p is None else f"{p:.2f}")
                                                                 for d, p in r["powers"].items()))
    out.append("\nminimal detectable superiority (power 0.80 / 0.90)")
    out += [f"  pi {k}: {v['0.80']:.2f} / {v['0.90']:.2f}" for k, v in t["minimal_detectable_superiority"].items()]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=None, help="write power.json here")
    args = ap.parse_args(argv)
    t = tables()
    print(render(t))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(t, f, indent=1)
            f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
