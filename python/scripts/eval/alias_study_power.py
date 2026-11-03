#!/usr/bin/env python
"""Alias study (OA-1000): the committed power calculation (DRAFT prereg §8; §15 item 8).

    alias_study_power.py [--records ACCEPTED.jsonl] [--neighbour-counts COUNTS.json] \\
                         [--sims 2000] [--flips 2000] --out power.json

Two methods, on the realised layout (300 clusters x (2 G + 1 C); read from the accepted-record
file with the committed balanced-set rule when ``--records`` is given, else the prereg's):

1. **Normal approximation** (§8): each record gets d in {-1, 0, +1}, non-zero with probability
   q and in the hypothesised direction with probability p; mu = q (2p - 1), sigma^2 = q - mu^2,
   power = Phi(sqrt(n) mu / sigma - z), z = 1.95996 (one-sided 0.025, the Holm worst case) or
   1.64485 (0.05, the conditional rows). The 80 %-power MDE is mu = k sqrt(q / (n + k^2)),
   k = 1.960 + 0.842, in points.
2. **Simulation of each member's test** (§8, *The committed calculation*; tests per §7, owner
   2026-11-02): 2,000 simulated studies, the paper's direction share p_j ~ Beta with mean p and
   between-paper SD 0.15.

   * **H1**: the 2 G records of each paper, the paper sum of hit(merged) - hit(none) > 0, by the
     cluster sign-flip test (2,000 flips, Monte-Carlo p = (1 + #) / (1 + flips), one seeded sign
     matrix reused across studies, as ``chunktrial_stats.simulate_power``).
   * **H1b**: 2 G + 1 C per paper, mean(D_G) - D_C summed > 0, by the exact G / C label
     randomization test (:func:`simulate_h1b`; C discordant at the same q with no effect).
   * **H2**: all 3 records, d = (d_1 + d_2) / 2 with d_k = hit(neighbour) - hit(random-k), the
     sum < 0, by the exact arm-label randomization test per question (:func:`simulate_h2`). ``q`` is a record's discordance between the neighbour arm and EACH draw
     (q scaled per paper by its realised neighbour count over the mean count when
     ``--neighbour-counts`` is given); a share ``rho`` of it is shared by both draws (same
     record, same direction: d = +-1) and the rest occurs per draw independently (d = +-1/2).
     rho = 1 is the prereg's three-valued approximation; rho = 0.5 shows what averaging two
     draws buys (§8, *H2 is approximated conservatively*). The neighbour arm loses a
     discordant record with probability p_j. **The gold-only direction gate** is simulated
     under a stated agreement ``a``: each record's gold-only change equals its judged change
     with probability a and is its negative otherwise; H2 is confirmed when p <= level AND
     the gold-only net < 0.

**The caps lever (§8)** reads H2's power to *confirm* (the judged test AND the gold-only gate) at
q 5 %, p .85, rho 1, level 0.025, at the agreement ``--lever-agreement``; below .80 it fires.
**a = 0.7** (owner, 2026-11-02, §14 O11), with a = 0.6 reported as a sensitivity
(``caps_lever_by_agreement``). ``--lever-agreement`` overrides it; a None lever agreement leaves
the lever undecided.

**Stated assumptions** (in the output): SD of p 0.15; rho in {1.0, 0.5}; agreement a in
{1.0, 0.8, 0.7, 0.6}; the seed ``261101|alias|power`` (not in the prereg's
seed table, §5.5, which fixes "a fixed seed" without naming it). The realised neighbour and
random counts (§15 item 4) do not exist yet: without ``--neighbour-counts`` H2 assumes equal
q across papers, and the output says so.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import alias_study_common as asc  # noqa: E402
import chunktrial_stats as cs  # noqa: E402

Z = {0.025: 1.959964, 0.05: 1.644854}
QS = (0.02, 0.03, 0.05, 0.10, 0.20)
PS = (0.85, 0.70)
SD_P = 0.15
AGREEMENTS = (1.0, 0.8, 0.7, 0.6)
#: §8: the agreement ``a`` between the gold-only and the judged change that the caps lever reads.
#: Owner, 2026-11-02 (§14 O11): 0.7; 0.6 is the reported sensitivity.
LEVER_AGREEMENT: float | None = 0.7
LEVER_SENSITIVITY_AGREEMENT = 0.6
#: §8: the lever fires when H2's simulated power to confirm is below this
LEVER_POWER = 0.80
#: the share of a record's neighbour-vs-draw discordance that both random draws share: 1.0 is the
#: prereg's three-valued approximation (the draws identical); 0.5 lets the average cut noise
RHOS = (1.0, 0.5)
POWER_SEED = f"{asc.SEED_BASE}|power"
_TOL = 1e-9


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def normal_power(n: int, q: float, p: float, alpha: float) -> float:
    """§8: Phi(sqrt(n) mu / sigma - z)."""
    mu = q * (2 * p - 1)
    var = q - mu * mu
    if var <= 0:
        return 1.0 if mu > 0 else 0.0
    return _phi(math.sqrt(n) * mu / math.sqrt(var) - Z[alpha])


def mde80_points(n: int, q: float) -> float:
    k = 1.960 + 0.842
    return 100.0 * k * math.sqrt(q / (n + k * k))


def beta_draws(rng: np.random.Generator, mean: float, sd: float, shape: tuple[int, ...]) -> np.ndarray:
    if sd <= 0:
        return np.full(shape, mean)
    s = mean * (1 - mean) / (sd * sd) - 1
    if s <= 0:
        raise ValueError(f"SD {sd} too large for mean {mean}")
    return rng.beta(mean * s, (1 - mean) * s, size=shape)


def _mc_p(sums: np.ndarray, signs: np.ndarray, alternative: str) -> np.ndarray:
    obs = sums.sum(axis=1)
    flipped = sums @ signs.T
    if alternative == "greater":
        cnt = (flipped >= obs[:, None] - _TOL).sum(axis=1)
    else:
        cnt = (flipped <= obs[:, None] + _TOL).sum(axis=1)
    return np.asarray((1 + cnt) / (1 + signs.shape[0]))


def simulate_h1(n_clusters: int, g_per: int, q: float, p: float, *, sd_p: float = SD_P, n_sims: int = 2000,
                n_flips: int = 2000, seed: int = 0) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    pj = beta_draws(rng, p, sd_p, (n_sims, n_clusters))
    disc = rng.random((n_sims, n_clusters, g_per)) < q
    up = rng.random((n_sims, n_clusters, g_per)) < pj[:, :, None]
    d = np.where(disc, np.where(up, 1.0, -1.0), 0.0)
    sums = d.sum(axis=2)
    signs = (rng.integers(0, 2, size=(n_flips, n_clusters), dtype=np.int8) * 2 - 1).astype(float)
    pv = _mc_p(sums, signs, "greater")
    return {str(lvl): float((pv <= lvl).mean()) for lvl in (0.025, 0.05)} | {"n_sims": n_sims, "n_flips": n_flips}


def _rand_p(groups: Sequence[tuple[Sequence[int], int]], obs2: int, alternative: str) -> float:
    """Exact label-exchangeability randomization p on the doubled lattice
    (``chunktrial_stats.randomization_law``)."""
    dist, lo = cs.randomization_law(groups)
    vals = np.arange(lo, lo + dist.size)
    tail = dist[vals >= obs2] if alternative == "greater" else dist[vals <= obs2]
    return min(1.0, float(tail.sum()))


#: H2 per question under arm-label exchangeability, doubled: one arm hits and two miss
#: ("single one": the contrast is +1 when the odd arm is the neighbour, -1/2 otherwise), or one
#: arm misses and two hit ("single zero": -1 / +1/2). Each relabelling equally likely.
H2_SINGLE_ONE = (2, -1, -1)
H2_SINGLE_ZERO = (-2, 1, 1)


def simulate_h2(n_clusters: int, rec_per: int, q: float, p: float, *, rho: float = 1.0,
                q_scale: Sequence[float] | None = None, sd_p: float = SD_P,
                agreements: Sequence[float] = AGREEMENTS, n_sims: int = 2000, n_flips: int = 2000,
                seed: int = 0) -> dict[str, Any]:
    """H2 with the two-draw average and the exact arm-label randomization test (§7, owner
    2026-11-02). ``q`` is the record's discordance between the neighbour arm and EACH random
    draw; a share ``rho`` of it is shared by both draws (the same record, the same direction:
    the neighbour is the odd arm), the rest occurs per draw independently (the draw is the odd
    arm). A record with opposite own changes on the two draws (impossible for binary hits) keeps
    the first draw's change only. ``n_flips`` is unused (the p is exact); kept for the signature."""
    rng = np.random.default_rng(seed)
    shape = (n_sims, n_clusters, rec_per)
    pj = beta_draws(rng, p, sd_p, (n_sims, n_clusters))[:, :, None]
    qj = (np.full(n_clusters, q) if q_scale is None
          else np.clip(q * np.asarray(q_scale, dtype=float), 0.0, 1.0))[None, :, None]

    def direction() -> np.ndarray:            # -1: the neighbour arm loses the record
        return np.where(rng.random(shape) < pj, -1.0, 1.0)

    shared = rng.random(shape) < qj * rho
    ds = direction()
    d_draw = []
    for _ in range(2):
        own = rng.random(shape) < qj * (1 - rho)
        d_draw.append(np.where(shared, ds, np.where(own, direction(), 0.0)))
    d1, d2 = d_draw
    d2 = np.where(~shared & (d1 * d2 < 0), 0.0, d2)
    d = 0.5 * (d1 + d2)
    one_nz = (d1 == 0) != (d2 == 0)
    single_one = ((d1 == 1) & (d2 == 1)) | (one_nz & (d1 + d2 == -1))
    single_zero = ((d1 == -1) & (d2 == -1)) | (one_nz & (d1 + d2 == 1))
    n_one = single_one.sum(axis=(1, 2))
    n_zero = single_zero.sum(axis=(1, 2))
    obs2 = np.rint(2 * d.sum(axis=(1, 2))).astype(int)
    pv = np.array([_rand_p([(H2_SINGLE_ONE, int(a)), (H2_SINGLE_ZERO, int(b))], int(o), "less")
                   for a, b, o in zip(n_one, n_zero, obs2, strict=True)])
    out: dict[str, Any] = {"n_sims": n_sims, "test": "arm-label randomization (exact)", "rho": rho}
    for lvl in (0.025, 0.05):
        sig = pv <= lvl
        row: dict[str, Any] = {"test_only": float(sig.mean())}
        for a in agreements:
            same = rng.random(shape) < a
            gold = np.where(same, d, -d)
            gate = gold.sum(axis=(1, 2)) < 0
            row[f"confirm_a{a:g}"] = float((sig & gate).mean())
        out[str(lvl)] = row
    return out


def simulate_h1b(n_clusters: int, q: float, p: float, *, q_c: float | None = None, p_c: float = 0.5,
                 sd_p: float = SD_P, n_sims: int = 2000, seed: int = 0) -> dict[str, Any]:
    """H1b with the exact G / C label randomization test (§7, owner 2026-11-02), 2 G + 1 C per
    paper: each G record discordant with probability ``q``, gaining with the paper's share
    ``p_j`` (Beta, mean ``p``, SD ``sd_p``, as H1); the C record discordant with probability
    ``q_c`` (default ``q``), gaining with probability ``p_c`` (0.5: no C effect). Statistic
    ``Σ_papers mean(D_G) − D_C`` > 0; under label exchangeability each of the 3 choices of the C
    record is equally likely (doubled: the paper's ``Σx − 3 x_i``)."""
    rng = np.random.default_rng(seed)
    qc = q if q_c is None else q_c
    pj = beta_draws(rng, p, sd_p, (n_sims, n_clusters))
    g = np.where(rng.random((n_sims, n_clusters, 2)) < q,
                 np.where(rng.random((n_sims, n_clusters, 2)) < pj[:, :, None], 1, -1), 0)
    c = np.where(rng.random((n_sims, n_clusters)) < qc, np.where(rng.random((n_sims, n_clusters)) < p_c, 1, -1), 0)
    x = np.concatenate([g, c[:, :, None]], axis=2)
    xs = np.sort(x, axis=2) + 1
    keys = xs[:, :, 0] * 9 + xs[:, :, 1] * 3 + xs[:, :, 2]
    obs2 = (g.sum(axis=2) - 2 * c).sum(axis=1)
    supports = {}
    for kk in range(27):
        tr = (kk // 9 - 1, kk // 3 % 3 - 1, kk % 3 - 1)
        supports[kk] = tuple(sum(tr) - 3 * v for v in tr)
    pv = np.empty(n_sims)
    for i in range(n_sims):
        ks, cnt = np.unique(keys[i], return_counts=True)
        groups = [(supports[int(k)], int(n)) for k, n in zip(ks, cnt, strict=True)
                  if len(set(supports[int(k)])) > 1]
        pv[i] = _rand_p(groups, int(obs2[i]), "greater")
    return {str(lvl): float((pv <= lvl).mean()) for lvl in (0.025, 0.05)} | \
        {"n_sims": n_sims, "test": "G/C label randomization (exact)", "q_c": qc, "p_c": p_c}


def layout_from_records(path: Path) -> dict[str, Any]:
    recs = asc.load_records(path)
    bal, desc = asc.split_records(recs)
    per: dict[str, list[int]] = {}
    for r in bal:
        c = per.setdefault(str(r["paper"]), [0, 0])
        c[0 if r["kind"] == "G" else 1] += 1
    shapes = sorted({tuple(v) for v in per.values()})
    return {"source": "accepted-record file (committed balanced-set rule)", "sha256": asc.sha256_file(path),
            "balanced_ids_sha256": asc.balanced_ids_sha(r["record_id"] for r in bal),
            "clusters": len(per), "shapes": [list(s) for s in shapes], "records": len(bal), "descriptive": len(desc),
            "papers": sorted(per)}


def compute(layout: Mapping[str, Any], q_scale: Sequence[float] | None, n_sims: int, n_flips: int,
            lever_agreement: float | None = LEVER_AGREEMENT) -> dict[str, Any]:
    """Both methods on the layout, and the caps lever (§8): H2's simulated power to *confirm* at
    q 5 %, p .85, rho 1, level 0.025 -- the judged test rejects AND the gold-only direction gate
    passes at agreement ``lever_agreement`` (default 0.7, §14 O11) -- below .80 fires it; the
    verdict at every simulated ``a`` (0.6 the declared sensitivity) is in
    ``caps_lever_by_agreement``. With ``lever_agreement`` None ``h2_caps_lever_fires`` is None:
    undecided, not "does not fire"."""
    if lever_agreement is not None and not 0.0 <= lever_agreement <= 1.0:
        raise SystemExit(f"lever agreement {lever_agreement!r}: a share in [0, 1]")
    agreements = tuple(AGREEMENTS) + (() if lever_agreement is None or lever_agreement in AGREEMENTS
                                      else (float(lever_agreement),))
    if layout["shapes"] != [[2, 1]]:
        raise SystemExit(f"layout is not exactly 2 G + 1 C per paper: {layout['shapes']}")
    nc = int(layout["clusters"])
    n_h1, n_h2 = 2 * nc, 3 * nc
    seed = asc.seed_int(POWER_SEED)
    normal: dict[str, dict[str, Any]] = {"H1": {}, "H2": {}}
    sim: dict[str, Any] = {"H1": {}, "H2": {}, "H1b": {}}
    for i, p in enumerate(PS):
        for j, q in enumerate(QS):
            key = f"p{p:g}_q{q:g}"
            normal["H1"][key] = {str(a): round(normal_power(n_h1, q, p, a), 4) for a in (0.025, 0.05)}
            normal["H2"][key] = {str(a): round(normal_power(n_h2, q, p, a), 4) for a in (0.025, 0.05)}
            s1 = simulate_h1(nc, 2, q, p, n_sims=n_sims, n_flips=n_flips, seed=seed + 100 * i + j)
            sim["H1"][key] = {k: v for k, v in s1.items() if k in ("0.025", "0.05")}
            s1b = simulate_h1b(nc, q, p, n_sims=n_sims, seed=seed + 50_000 + 100 * i + j)
            sim["H1b"][key] = {k: v for k, v in s1b.items() if k in ("0.025", "0.05")}
            for t, rho in enumerate(RHOS):
                s2 = simulate_h2(nc, 3, q, p, rho=rho, q_scale=q_scale, agreements=agreements, n_sims=n_sims,
                                 n_flips=n_flips, seed=seed + 10_000 * (t + 1) + 100 * i + j)
                sim["H2"].setdefault(f"rho{rho:g}", {})[key] = {k: v for k, v in s2.items() if k in ("0.025", "0.05")}
    mde = {"H1": {f"q{q:g}": round(mde80_points(n_h1, q), 2) for q in QS},
           "H2": {f"q{q:g}": round(mde80_points(n_h2, q), 2) for q in QS}}
    h2_lever = sim["H2"]["rho1"]["p0.85_q0.05"]["0.025"]
    by_a = {f"a{a:g}": bool(h2_lever[f"confirm_a{a:g}"] < LEVER_POWER) for a in agreements}
    if lever_agreement is None:
        fires: bool | None = None
        lever_note = "no lever agreement given: the lever is undecided; per-a verdicts in caps_lever_by_agreement"
    else:
        fires = by_a[f"a{lever_agreement:g}"]
        lever_note = (f"confirm power (test and gold-only gate) at a = {lever_agreement:g}; "
                      f"sensitivity a = {LEVER_SENSITIVITY_AGREEMENT:g}")
    return {"normal_approximation": normal, "simulation": sim, "mde80_points": mde,
            "h2_at_q05_p85_alpha025": h2_lever,
            "caps_lever_agreement": lever_agreement, "caps_lever_by_agreement": by_a,
            "caps_lever_note": lever_note,
            "h2_caps_lever_fires": fires,
            "evaluability_floor": {"alpha_0.025_min_nonzero_clusters": 6, "alpha_0.05_min_nonzero_clusters": 5}}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--records", type=Path, default=None)
    ap.add_argument("--neighbour-counts", type=Path, default=None,
                    help="JSON {paper: attributed neighbour count} from the neighbour manifest (§15 item 4)")
    ap.add_argument("--sims", type=int, default=2000)
    ap.add_argument("--flips", type=int, default=2000)
    ap.add_argument("--lever-agreement", type=float, default=LEVER_AGREEMENT,
                    help="agreement a the caps lever reads (§8, §14 O11: 0.7 by the owner's decision of "
                         "2026-11-02; 0.6 is reported as the sensitivity)")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    if args.records is not None:
        layout = layout_from_records(args.records)
        papers = layout.pop("papers")
    else:
        layout = {"source": "prereg §4.1 (300 x (2 G + 1 C))", "clusters": 300, "shapes": [[2, 1]], "records": 900}
        papers = None
    q_scale = None
    counts_note = "realised neighbour counts not given (§15 item 4 open): equal q across papers"
    if args.neighbour_counts is not None:
        if papers is None:
            raise SystemExit("--neighbour-counts needs --records (the paper order)")
        cnt = json.loads(args.neighbour_counts.read_text())
        vals = np.array([float(cnt.get(p, 0)) for p in papers])
        q_scale = list(vals / vals.mean()) if vals.mean() > 0 else None
        counts_note = (f"q scaled per paper by its attributed neighbour count / mean count (mean {vals.mean():.2f}, "
                       f"min {vals.min():g}, max {vals.max():g}); counts sha256 {asc.sha256_file(args.neighbour_counts)}")
    res = compute(layout, q_scale, args.sims, args.flips, args.lever_agreement)
    try:
        from ragstack.provenance import experiment_provenance
        prov = experiment_provenance()
    except Exception:  # noqa: BLE001
        prov = {}
    out = {"schema": "alias-study-power/1", "layout": layout, **res,
           "assumptions": {"sd_p_between_papers": SD_P, "rho_shared_discordance": list(RHOS),
                           "gold_agreement": list(AGREEMENTS), "lever_agreement": args.lever_agreement,
                           "neighbour_counts": counts_note,
                           "seed": POWER_SEED, "seed_rule": asc.SEED_RULE, "sims": args.sims, "flips": args.flips},
           "provenance": prov, "citable": bool(prov.get("dirty") is False and prov.get("untracked") == 0)}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"H1_sim": res["simulation"]["H1"]["p0.85_q0.05"], "H2_sim": res["h2_at_q05_p85_alpha025"],
                      "caps_lever": res["h2_caps_lever_fires"], "caps_lever_by_agreement": res["caps_lever_by_agreement"]},
                     indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
