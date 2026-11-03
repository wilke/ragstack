#!/usr/bin/env python
"""Alias study prereg (DRAFT v3), §3.3: the C cost rule's operating characteristics (R12).

    python -I c_cost_rule.py [--studies 10000] [--boot 10000] --out c_cost_rule.json

A planning calculation, not a study result: it reads no record, no retrieval and no judge label.

**Model.** The 300 C records, one per study paper, under base x default, glossary merged vs none.
Each record's change d = hit(merged) - hit(none) is in {-1, 0, +1}: non-zero with probability q
(the C discordance), with mean mu = (true C change in points) / 100, so P(+1) = (q + mu) / 2 and
P(-1) = (q - mu) / 2. Records are independent (one per paper, so the paper cluster is the record).

**The rule's CI.** The 95 % paper-bootstrap percentile CI of the prereg (§3.3; the committed
``alias_study_stats.boot_ci``: 10,000 paper resamples, ``numpy.percentile`` at 2.5 / 97.5, linear
interpolation). With one record per paper and d in {-1, 0, +1}, a paper resample's net count is
N+ - N- with (N+, N-, N0) ~ Multinomial(300, observed shares): the same distribution as resampling
paper indices, drawn here directly.

**The two rules compared** ("pass" = the C cost step does not block attaching):

* ``as_written`` (prereg v2, R2): pass when C_lo >= -1 point.
* ``v3`` (R12, owner 2026-11-03): pass when the point estimate dC >= -1 point **and**
  C_lo >= -3.6 points.

**Why -3.6.** It is the smallest C change that 300 C records detect with 80 % power at 5 %
discordance, two-sided 0.05: the prereg §8 formula mu = k sqrt(q / (n + k^2)), k = z(0.975) +
z(0.80), at n = 300, q = 0.05 (printed under ``bound_derivation`` with its normal-approximation
power check).

**Seeds.** The prereg §5.5 convention: numeric seed = first 8 bytes of sha256(seed string),
big-endian; seed string ``261101|alias|power|c-cost|q<q>|cost<cost>`` per scenario (the power
seed ``261101|alias|power`` followed by ``|`` and the named parts).

Run from a clean checkout so the embedded provenance is citable. The provenance block comes from
``ragstack.provenance.experiment_provenance`` of this repository's ``python/`` tree when it imports;
otherwise it is recorded as absent with the reason.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np

SEED = "261101|alias|power"
N_C = 300
QS = (0.02, 0.05, 0.10)
COSTS = (0.0, -1.0, -2.0, -3.6)
MARGIN_POINT = -1.0      # both rules: the point-estimate / v2 CI margin, points
BOUND_V3 = -3.6          # R12: the CI lower-bound limit, points
REPO = Path(__file__).resolve().parents[6]


def seed_int(s: str) -> int:
    return int.from_bytes(hashlib.sha256(s.encode("utf-8")).digest()[:8], "big")


def mde_points(q: float, n: int, alpha_two_sided: float = 0.05, power: float = 0.80) -> float:
    nd = NormalDist()
    k = nd.inv_cdf(1 - alpha_two_sided / 2) + nd.inv_cdf(power)
    return 100 * k * math.sqrt(q / (n + k * k))


def normal_power(mu_points: float, q: float, n: int, alpha_two_sided: float = 0.05) -> float:
    nd = NormalDist()
    mu = abs(mu_points) / 100
    sigma = math.sqrt(q - mu * mu)
    return nd.cdf(math.sqrt(n) * mu / sigma - nd.inv_cdf(1 - alpha_two_sided / 2))


def scenario(q: float, cost: float, studies: int, boot: int) -> dict[str, Any]:
    mu = cost / 100
    pp, pm = (q + mu) / 2, (q - mu) / 2
    s = f"{SEED}|c-cost|q{q:g}|cost{cost:g}"
    rng = np.random.default_rng(seed_int(s))
    obs = rng.multinomial(N_C, [pp, pm, 1 - pp - pm], size=studies)      # (N+, N-, N0) per study
    est = 100 * (obs[:, 0] - obs[:, 1]) / N_C
    lo = np.empty(studies)
    states: dict[tuple[int, int], list[int]] = {}
    for i, (a, b, _) in enumerate(obs):
        states.setdefault((int(a), int(b)), []).append(i)
    for (a, b), idx in sorted(states.items()):
        if a + b == 0:
            lo[idx] = 0.0
            continue
        p = [a / N_C, b / N_C, 1 - (a + b) / N_C]
        for start in range(0, len(idx), 100):
            part = idx[start:start + 100]
            m = rng.multinomial(N_C, p, size=(len(part), boot))
            vals = 100 * (m[..., 0] - m[..., 1]) / N_C
            lo[part] = np.percentile(vals, 2.5, axis=1)
    as_written = lo >= MARGIN_POINT
    v3 = (est >= MARGIN_POINT) & (lo >= BOUND_V3)

    def rate(x: np.ndarray) -> dict[str, float]:
        r = float(x.mean())
        return {"pass_rate": round(r, 4), "mc_se": round(math.sqrt(r * (1 - r) / studies), 4)}

    return {"q": q, "true_c_change_points": cost, "p_plus": round(pp, 5), "p_minus": round(pm, 5),
            "seed_string": s, "as_written": rate(as_written), "v3": rate(v3),
            "v3_blocked_by_point_estimate": round(float((est < MARGIN_POINT).mean()), 4),
            "v3_blocked_by_ci_bound": round(float((lo < BOUND_V3).mean()), 4),
            "mean_c_lo_points": round(float(lo.mean()), 3)}


def provenance() -> dict[str, Any]:
    sys.path.insert(0, str(REPO / "python"))
    try:
        from ragstack.provenance import experiment_provenance
    except Exception as e:  # noqa: BLE001 - recorded, never raised
        return {"absent": f"ragstack.provenance did not import: {type(e).__name__}"}
    return experiment_provenance(repo=REPO)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--studies", type=int, default=10_000)
    ap.add_argument("--boot", type=int, default=10_000)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    prov = provenance()   # at launch: the tree that ran
    rows = [scenario(q, c, a.studies, a.boot) for q in QS for c in COSTS if abs(c) / 100 <= q]
    bound = mde_points(0.05, N_C)
    doc = {
        "schema": "alias-prereg-c-cost-rule/1",
        "what": "operating characteristics of the C cost rule (prereg DRAFT v3 §3.3, R12): pass rates of the "
                "v2 rule and the v3 rule on 300 C records; a planning calculation, no study data",
        "model": "d in {-1,0,+1} per C record, P(d != 0) = q, mean = true change / 100; records independent "
                 "(one per paper)",
        "bootstrap": "95 % paper-bootstrap percentile CI, numpy.percentile 2.5 (linear), resamples drawn as "
                     "Multinomial(300, observed shares) (equal in distribution to paper-index resampling)",
        "rules": {"as_written": "pass iff C_lo >= -1 point (prereg v2, R2)",
                  "v3": "pass iff dC >= -1 point and C_lo >= -3.6 points (R12)"},
        "n_c_records": N_C, "studies_per_scenario": a.studies, "bootstrap_resamples": a.boot,
        "seed": SEED, "seed_rule": "numpy seed = int.from_bytes(sha256(seed string)[:8], 'big'); "
                                   "seed string '<seed>|c-cost|q<q>|cost<cost>' per scenario",
        "bound_derivation": {
            "formula": "mu = k sqrt(q / (n + k^2)), k = z(0.975) + z(0.80) (prereg §8)",
            "n": N_C, "q": 0.05, "mde80_two_sided_points": round(bound, 3),
            "rounded": round(bound, 1),
            "normal_power_at_rounded_two_sided_0.05": round(normal_power(round(bound, 1), 0.05, N_C), 4),
            "mde80_points_other_q": {f"q{q:g}": round(mde_points(q, N_C), 3) for q in QS},
        },
        "scenarios": rows,
        "provenance": prov,
    }
    Path(a.out).write_text(json.dumps(doc, indent=1, sort_keys=False) + "\n")
    for r in rows:
        print(f"q {r['q']:<5g} true {r['true_c_change_points']:>5g}: as_written {r['as_written']['pass_rate']:.3f}"
              f"  v3 {r['v3']['pass_rate']:.3f}")
    print(f"bound: {bound:.3f} points")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
