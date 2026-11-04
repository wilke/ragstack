"""Paired bootstrap, one-sided bootstrap p-values and Holm, on per-question 0/1 hits.

All differences are in PERCENTAGE POINTS (hit rate x 100), the unit the margins in
PLAN-AND-PASS-RULE.md are written in. The bootstrap resamples question indices with
replacement; resampling the per-question difference vector is the same as resampling
both arms with the same indices, so the CI is paired. A fixed seed gives the same
index stream for every comparison of the same length (as ``scripts/eval/_stats.py``
does), so a re-run reproduces every interval exactly.

Holm–Bonferroni comes from the shared ``scripts/eval/_stats.py``.
"""
from __future__ import annotations

import math
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

try:  # the shared stats layer lives one directory up (scripts/eval/_stats.py)
    import _stats  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - depends on how the package was put on sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import _stats  # type: ignore[import-not-found,no-redef]

holm_bonferroni = _stats.holm_bonferroni

_CHUNK = 1000  # bootstrap iterations generated per block (bounds memory at large N)


def boot_means(values: Sequence[float], iters: int, seed: int) -> np.ndarray:
    """Bootstrap distribution of the mean of ``values`` (``iters`` resamples, seeded).

    When ``values`` take few distinct levels (0/1 hits, -100/0/+100 paired diffs) a
    resample is fully described by how often each level is drawn, which is
    Multinomial(n, level shares): that is drawn directly. Same distribution as index
    resampling, orders of magnitude faster at N = 2000 x 10,000 resamples.
    """
    arr = np.asarray(values, dtype=float)
    n = len(arr)
    if n == 0:
        return np.zeros(0)
    rng = np.random.default_rng(seed)
    levels, counts = np.unique(arr, return_counts=True)
    if len(levels) <= 16:
        draws = rng.multinomial(n, counts / n, size=iters)
        return draws @ levels / n
    out = np.empty(iters, dtype=float)
    done = 0
    while done < iters:
        m = min(_CHUNK, iters - done)
        idx = rng.integers(0, n, size=(m, n))
        out[done:done + m] = arr[idx].mean(axis=1)
        done += m
    return out


def paired_diff(arm: Sequence[float], base: Sequence[float], iters: int = 10_000, seed: int = 0,
                alpha: float = 0.05) -> dict[str, Any]:
    """Paired bootstrap CI of mean(arm - base), in points. ``arm``/``base`` are 0/1 per
    question, aligned. Returns point, lo, hi, n and the bootstrap distribution
    (``_dist``, not serialised by callers)."""
    a = np.asarray(arm, dtype=float)
    b = np.asarray(base, dtype=float)
    if a.shape != b.shape:
        raise ValueError("paired vectors must have the same length")
    n = int(a.size)
    if n == 0:
        return {"n": 0, "point": None, "lo": None, "hi": None, "_dist": np.zeros(0)}
    d = (a - b) * 100.0
    dist = boot_means(d, iters, seed)
    lo_p, hi_p = 100 * alpha / 2, 100 * (1 - alpha / 2)
    return {"n": n, "point": float(d.mean()), "lo": float(np.percentile(dist, lo_p)),
            "hi": float(np.percentile(dist, hi_p)), "_dist": dist}


def mean_ci(values: Sequence[float], iters: int = 10_000, seed: int = 0, alpha: float = 0.05,
            scale: float = 100.0) -> dict[str, Any]:
    arr = np.asarray(values, dtype=float) * scale
    if arr.size == 0:
        return {"n": 0, "point": None, "lo": None, "hi": None}
    dist = boot_means(arr, iters, seed)
    return {"n": int(arr.size), "point": float(arr.mean()),
            "lo": float(np.percentile(dist, 100 * alpha / 2)),
            "hi": float(np.percentile(dist, 100 * (1 - alpha / 2)))}


def p_greater(dist: np.ndarray, threshold: float) -> float:
    """One-sided bootstrap p-value for H0: mean diff <= threshold vs H1: > threshold.

    ``(#{dist <= threshold} + 1) / (B + 1)``: never 0, so Holm stays meaningful.
    A 95% two-sided lower bound >= threshold corresponds to p <= 0.025.
    """
    b = len(dist)
    if b == 0:
        return 1.0
    return float((np.count_nonzero(dist <= threshold) + 1) / (b + 1))


def strip_private(d: dict[str, Any]) -> dict[str, Any]:
    """Drop ``_``-prefixed keys (bootstrap arrays) and round floats for output."""
    out = {}
    for k, v in d.items():
        if k.startswith("_"):
            continue
        out[k] = round(v, 4) if isinstance(v, float) else v
    return out


def discordance(a: Sequence[float], b: Sequence[float]) -> float | None:
    if not len(a):
        return None
    return float(sum(1 for x, y in zip(a, b, strict=True) if bool(x) != bool(y)) / len(a))


def half_width_at(diffs_points: Sequence[float], n: int, z: float = 1.959964) -> float | None:
    """Normal-approximation 95% half-width (points) of a paired mean diff at sample size ``n``,
    from per-question diffs in points."""
    arr = np.asarray(diffs_points, dtype=float)
    if arr.size < 2 or n <= 0:
        return None
    sd = float(arr.std(ddof=1))
    return z * sd / math.sqrt(n)
