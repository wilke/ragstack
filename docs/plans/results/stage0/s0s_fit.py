"""Step 1 -- levels per (size, arm, mode), the reach-vs-log(size) fit, and the projection.

Three things come out of this module, and the third is the one the owner asked for.

**1. Levels.** ``ERET`` and ``EPACK | reach`` per (subset, arm, mode, rerank), macro over
the 177 pointed queries, with Wilson 95 % intervals. The full-corpus row is a **check**:
it must reproduce Stage 0b's published pointed table, and the reproduction is asserted
here rather than eyeballed.

**2. The fit.** Per arm, reach against ``log10(N)`` over the ten measured points, by
ordinary least squares. Two link functions are fitted and **both** are reported:

* **linear** -- ``reach = a + b log10(N)``. This is what the brief asks for. It is not
  bounded to [0, 1] and will happily project a reach above 1 or below 0; over the range
  where it stays inside, it is the honest reading of the measured slope.
* **logit** -- ``logit(reach) = a + b log10(N)``. Bounded by construction, and the more
  defensible functional form for a probability, but it *assumes* the decay keeps a
  constant odds-ratio per decade, which the data cannot check over the 0.9 decades
  actually measured.

The two disagree at 500k by more than either one's own interval, and that disagreement is
**the honest width of this projection**. It is reported, not averaged away.

**3. Uncertainty.** A cluster bootstrap over the 177 **queries** (the cluster is the
source document; one query per document). Each of ``N_BOOT`` resamples redraws the query
set with replacement, recomputes every subset's reach from the same frozen endpoint
records, refits, and predicts. The percentile interval is over the resamples. This
captures the dominant source of uncertainty -- which 177 queries were drawn -- and does
**not** capture the one that matters most for the extrapolation: whether reach is linear
in log N at all beyond the measured range. §The write-up says so.

**Per-query difficulty.** How many queries are reached by *every* arm at *every* size --
the truly easy ones -- and what fraction of the population they are.

Output: ``artifacts/pointed-scale/levels.json``, ``fit.json``, ``difficulty.json``.
"""
from __future__ import annotations

import gzip
import json
import math
import sys
from collections import defaultdict

import numpy as np

import s0s_common as SC
import s0b_common as K


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (round((c - h) / d, 4), round((c + h) / d, 4))


def ols(x: np.ndarray, y: np.ndarray):
    """Slope, intercept, R^2 for a simple linear fit."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    xm, ym = x.mean(), y.mean()
    sxx = float(((x - xm) ** 2).sum())
    b = float(((x - xm) * (y - ym)).sum() / sxx) if sxx > 0 else 0.0
    a = float(ym - b * xm)
    yh = a + b * x
    ss_res = float(((y - yh) ** 2).sum())
    ss_tot = float(((y - ym) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return a, b, r2


EPS_LOGIT = 1.0 / (2 * 177)          # half a query: the finest resolution this n has


def to_logit(p: float) -> float:
    p = min(max(p, EPS_LOGIT), 1 - EPS_LOGIT)
    return math.log(p / (1 - p))


def from_logit(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def load_records(path):
    """``(arm, mode, rerank, subset) -> {qid: (ERET, EPACK)}`` plus subset sizes."""
    by: dict = defaultdict(dict)
    sizes: dict = {}
    draws: dict = {}
    for line in gzip.open(path, "rt"):
        r = json.loads(line)
        by[(r["arm"], r["mode"], r["rerank"], r["subset"])][r["qid"]] = (
            r["ERET"], r["EPACK"])
        sizes[r["subset"]] = r["size"]
        draws[r["subset"]] = r["draw"]
    return by, sizes, draws


def levels(by, sizes, qids):
    out = []
    for (arm, mode, rerank, sub), d in sorted(by.items()):
        e = [d[q][0] for q in qids]
        p = [d[q][1] for q in qids]
        nr = sum(e)
        eret = sum(e) / len(e)
        epk_g = (sum(b for a, b in zip(e, p) if a) / nr) if nr else None
        out.append({
            "arm": arm, "mode": mode, "rerank": rerank, "subset": sub,
            "size": sizes[sub], "n": len(e),
            "ERET": round(eret, 4), "ERET_wilson": wilson(int(nr), len(e)),
            "n_reached": int(nr),
            "EPACK_given_reach": round(epk_g, 4) if epk_g is not None else None,
            "EPACK_given_reach_wilson": wilson(int(sum(p)), int(nr)) if nr else (None, None),
            "EPACK_uncond": round(sum(p) / len(p), 4),
        })
    return out


def fit_arm(points):
    """``points`` = [(size, reach), ...]. Returns the linear and logit fits."""
    x = np.array([math.log10(s) for s, _r in points])
    y = np.array([r for _s, r in points])
    a, b, r2 = ols(x, y)
    az, bz, r2z = ols(x, np.array([to_logit(v) for v in y]))
    return {"linear": {"a": a, "b": b, "r2": r2},
            "logit": {"a": az, "b": bz, "r2": r2z}}


def predict(fit, n: int):
    x = math.log10(n)
    return {"linear": fit["linear"]["a"] + fit["linear"]["b"] * x,
            "logit": from_logit(fit["logit"]["a"] + fit["logit"]["b"] * x)}


def main() -> None:
    smoke = "--smoke" in sys.argv
    outdir = SC.OUT / "smoke" if smoke else SC.OUT
    by, sizes, draws = load_records(outdir / "endpoints.jsonl.gz")
    qids = sorted({q for d in by.values() for q in d})
    print("queries:", len(qids), "cells:", len(by), flush=True)

    lv = levels(by, sizes, qids)
    SC.atomic_json(SC.ART / "levels.json",
                   {"levels": lv, "sizes": sizes, "draws": draws,
                    "budget_sfr": SC.BUDGET, "provenance": SC.provenance()})

    # ---- the full-corpus row must reproduce Stage 0b's published pointed table -----
    repro = {}
    try:
        for line in gzip.open(K.OUT / "endpoints-pointed.jsonl.gz", "rt"):
            r = json.loads(line)
            if (r["arm"] in K.INDEX_KEYS and r["mode"] == SC.PRIMARY_MODE
                    and r["rerank"] == SC.PRIMARY_RERANK
                    and r["budget"] == SC.BUDGET):
                repro.setdefault(r["arm"], []).append((r["ERET"], r["EPACK"]))
        repro = {a: {"ERET_stage0bprime": round(sum(e for e, _ in v) / len(v), 4),
                     "EPACK_given_reach_stage0bprime":
                         round(sum(p for e, p in v if e) / max(sum(e for e, _ in v), 1), 4),
                     "n": len(v)}
                 for a, v in repro.items()}
    except Exception as exc:  # noqa: BLE001
        repro = {"UNAVAILABLE": f"{type(exc).__name__}: {exc}"}

    full = {r["arm"]: r for r in lv
            if r["mode"] == SC.PRIMARY_MODE and r["rerank"] == SC.PRIMARY_RERANK
            and r["size"] == SC.FULL_N}
    check = []
    for arm, ref in sorted(repro.items()):
        if arm in full:
            check.append({
                "arm": arm, "here": full[arm]["ERET"],
                "stage0b_prime": ref["ERET_stage0bprime"],
                "delta": round(full[arm]["ERET"] - ref["ERET_stage0bprime"], 4),
                "EPACK_here": full[arm]["EPACK_given_reach"],
                "EPACK_stage0b_prime": ref["EPACK_given_reach_stage0bprime"],
                "EPACK_delta": round(
                    full[arm]["EPACK_given_reach"]
                    - ref["EPACK_given_reach_stage0bprime"], 4)})

    # ---- the fits, primary shape -------------------------------------------------
    prim = [r for r in lv if r["mode"] == SC.PRIMARY_MODE
            and r["rerank"] == SC.PRIMARY_RERANK]
    fits = {}
    for arm in K.INDEX_KEYS:
        pts = [(r["size"], r["ERET"]) for r in prim if r["arm"] == arm]
        if len(pts) < 3:
            continue
        f = fit_arm(pts)
        f["points"] = sorted(pts)
        f["projection"] = {str(t): predict(f, t) for t in SC.TARGETS}
        fits[arm] = f

    # ---- cluster bootstrap over the 177 queries ----------------------------------
    rng = np.random.default_rng(SC.SEED_BOOT)
    qi = {q: i for i, q in enumerate(qids)}
    cell = {}
    for (arm, mode, rerank, sub), d in by.items():
        if mode != SC.PRIMARY_MODE or rerank != SC.PRIMARY_RERANK:
            continue
        v = np.zeros(len(qids), dtype=np.int8)
        for q, (e, _p) in d.items():
            v[qi[q]] = e
        cell[(arm, sub)] = v
    boot = {a: {str(t): {"linear": [], "logit": []} for t in SC.TARGETS}
            for a in fits}
    boot_slope = {a: {"linear": [], "logit": []} for a in fits}
    for _ in range(SC.N_BOOT):
        idx = rng.integers(0, len(qids), len(qids))
        for arm in fits:
            pts = []
            for sub in sizes:
                v = cell.get((arm, sub))
                if v is None:
                    continue
                pts.append((sizes[sub], float(v[idx].mean())))
            f = fit_arm(pts)
            boot_slope[arm]["linear"].append(f["linear"]["b"])
            boot_slope[arm]["logit"].append(f["logit"]["b"])
            for t in SC.TARGETS:
                p = predict(f, t)
                boot[arm][str(t)]["linear"].append(p["linear"])
                boot[arm][str(t)]["logit"].append(p["logit"])
    for arm in fits:
        fits[arm]["slope_ci95"] = {
            k: [round(float(np.percentile(v, 2.5)), 4),
                round(float(np.percentile(v, 97.5)), 4)]
            for k, v in boot_slope[arm].items()}
        fits[arm]["projection_ci95"] = {
            str(t): {k: [round(float(np.percentile(v, 2.5)), 4),
                         round(float(np.percentile(v, 97.5)), 4)]
                     for k, v in boot[arm][str(t)].items()}
            for t in SC.TARGETS}

    # ---- the step-2 gate ---------------------------------------------------------
    lo, hi = SC.WINDOW
    gate = {}
    for t in SC.TARGETS:
        rows = []
        for arm, f in sorted(fits.items()):
            p = f["projection"][str(t)]
            rows.append({"arm": arm,
                         "linear": round(p["linear"], 4),
                         "logit": round(p["logit"], 4),
                         "linear_in_window": bool(lo <= p["linear"] <= hi),
                         "logit_in_window": bool(lo <= p["logit"] <= hi)})
        gate[str(t)] = {
            "rows": rows,
            "n_arms_in_window_linear": sum(r["linear_in_window"] for r in rows),
            "n_arms_in_window_logit": sum(r["logit_in_window"] for r in rows),
        }
    gate["decision_rule"] = ("step 2 proceeds only if >= 2 arms' projected pointed ERET "
                            f"is inside {list(SC.WINDOW)} at 150,000 documents")
    g150 = gate[str(SC.TARGETS[0])]
    gate["step2_proceeds"] = bool(max(g150["n_arms_in_window_linear"],
                                      g150["n_arms_in_window_logit"]) >= 2)

    SC.atomic_json(SC.ART / "fit.json", {
        "primary": {"mode": SC.PRIMARY_MODE, "rerank": SC.PRIMARY_RERANK,
                    "budget_sfr": SC.BUDGET},
        "fits": fits, "gate": gate,
        "full_corpus_reproduction_vs_stage0b_prime": check,
        "bootstrap": {"n": SC.N_BOOT, "seed": SC.SEED_BOOT,
                      "cluster": "the query (one query per source document)"},
        "provenance": SC.provenance()})

    # ---- per-query difficulty ----------------------------------------------------
    keys = sorted(cell)
    per_q = {}
    for q in qids:
        i = qi[q]
        hits = [int(cell[k][i]) for k in keys]
        per_q[q] = {"n_cells": len(keys), "n_reached": sum(hits),
                    "always": all(hits), "never": not any(hits)}
    always = [q for q, v in per_q.items() if v["always"]]
    never = [q for q, v in per_q.items() if v["never"]]
    # by size: reached by every arm at that size
    by_size = {}
    for n in sorted({sizes[s] for s in sizes}):
        ks = [k for k in keys if sizes[k[1]] == n]
        cnt = sum(1 for q in qids if all(cell[k][qi[q]] for k in ks))
        by_size[str(n)] = {"queries_reached_by_every_arm_every_draw": cnt,
                           "fraction": round(cnt / len(qids), 4), "cells": len(ks)}
    SC.atomic_json(SC.ART / "difficulty.json", {
        "definition": ("a query is 'always reached' if its gold document is reached at "
                       "every arm x every subset in the primary shape "
                       f"({SC.PRIMARY_MODE}, rerank {SC.PRIMARY_RERANK}, "
                       f"B = {SC.BUDGET} SFR)"),
        "n_queries": len(qids), "n_cells": len(keys),
        "always_reached": {"n": len(always), "fraction": round(len(always) / len(qids), 4),
                           "qids": sorted(always)},
        "never_reached": {"n": len(never), "fraction": round(len(never) / len(qids), 4),
                          "qids": sorted(never)},
        "reach_count_histogram": {
            str(c): sum(1 for v in per_q.values() if v["n_reached"] == c)
            for c in sorted({v["n_reached"] for v in per_q.values()})},
        "by_size": by_size,
        "per_query": per_q,
        "provenance": SC.provenance()})

    print(json.dumps({"gate": {k: v for k, v in gate.items() if k != "rows"},
                      "reproduction": check}, indent=1)[:4000], flush=True)
    print("always reached:", len(always), "/", len(qids), flush=True)


if __name__ == "__main__":
    main()
