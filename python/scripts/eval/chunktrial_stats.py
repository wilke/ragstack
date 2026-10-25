#!/usr/bin/env python
"""Confirmatory statistics for a page-clustered paired contrast (pure numpy + stdlib).

Generic: this module holds no data and makes no network calls. It implements the
analysis plan of a pre-registered two-arm contrast in which **questions are
clustered by page**:

Units and notation
------------------
For a contrast arm A − arm B, question ``i`` on page ``j`` has a paired binary
outcome ``(a_i, b_i)`` (arm correct or not) and a difference
``d_i = a_i − b_i ∈ {−1, 0, +1}``. *gained* = #{d = +1}, *lost* = #{d = −1},
*net* = gained − lost, ``n`` = number of questions, *net share* = net / n
(= mean d). The page sum is ``S_j = Σ_{i∈j} d_i`` (or ``Σ (d_i + δ)`` for the
non-inferiority test).

What is here
------------
- :func:`sign_flip_test` — the primary test: a sign-flip permutation test with
  pages as the exchangeable units. Statistic ``T = Σ_j S_j``; under H0 each
  ``S_j`` is symmetric about 0, so ``T* = Σ_j ε_j S_j`` with i.i.d. Rademacher
  ``ε_j`` is the reference distribution. Monte-Carlo p (one-sided "> 0")
  ``= (1 + #{T* ≥ T}) / (1 + n_flips)``; exact enumeration over all ``2^k``
  sign vectors of the ``k`` non-zero pages when ``k ≤ 20``,
  ``p = #{T* ≥ T} / 2^k`` (zero pages contribute nothing under any flip).
- :func:`noninferiority_test` — H0: mean d ≤ −δ, via the shift-to-null
  sign-flip test on ``S_j + δ·n_j`` plus the CI decision (lower bound of the
  one-sided 95% page-cluster-bootstrap interval > −δ).
- :func:`page_bootstrap_ci` — page cluster bootstrap of the net share.
- :func:`holm` — Holm step-down (delegates to ``_stats.holm_bonferroni``).
- :func:`mcnemar_exact` — exact McNemar (binomial on the discordant pairs).
- :func:`kappa_from_table` / :func:`cohen_kappa_ci` — Cohen's κ with an
  item-bootstrap CI; :func:`clopper_pearson` / :func:`proportion_ci`.
- :func:`simulate_power` — power by simulation under a beta-binomial page model.

All results are plain dicts of Python scalars / lists (JSON-serialisable).
All randomness goes through ``numpy.random.default_rng(seed)``.
"""
from __future__ import annotations

import math
import sys
from collections.abc import Hashable, Sequence
from pathlib import Path
from typing import Any, Literal

import numpy as np

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import _stats  # noqa: E402

Alternative = Literal["greater", "less", "two-sided"]
#: Anything ``np.asarray`` turns into a 1-D float array (list, tuple, ndarray).
Floats = Sequence[float] | np.ndarray

N_FLIPS = 10_000
N_BOOT = 10_000
SEED = 0
EXACT_MAX_PAGES = 20
#: Comparison tolerance for ``T* ≥ T``. Page sums are integers (or integers plus
#: multiples of δ after the non-inferiority shift); the tolerance makes ties
#: count as ties despite floating-point summation order.
_TOL = 1e-9


# --------------------------------------------------------------------------- #
# Input shaping
# --------------------------------------------------------------------------- #
def _page_index(pages: Sequence[Hashable]) -> tuple[np.ndarray, int]:
    """Map page ids (any hashables) to 0..P-1 in order of first appearance."""
    index: dict[Hashable, int] = {}
    codes = np.empty(len(pages), dtype=np.int64)
    for i, p in enumerate(pages):
        codes[i] = index.setdefault(p, len(index))
    return codes, len(index)


def paired_differences(a: Sequence[int | bool], b: Sequence[int | bool]) -> np.ndarray:
    """``d_i = a_i − b_i`` for paired binary outcomes (values in {−1, 0, +1})."""
    aa = np.asarray(a, dtype=np.int64)
    bb = np.asarray(b, dtype=np.int64)
    if aa.shape != bb.shape:
        raise ValueError("a and b must have the same length")
    if not (np.isin(aa, (0, 1)).all() and np.isin(bb, (0, 1)).all()):
        raise ValueError("a and b must be binary (0/1 or bool)")
    return aa - bb


def page_sums(
    pages: Sequence[Hashable], d: Floats, shift: float = 0.0
) -> tuple[np.ndarray, np.ndarray]:
    """Per-page ``S_j = Σ_{i∈j} (d_i + shift)`` and per-page question counts ``n_j``.

    Returns ``(sums, counts)`` in order of first appearance of each page id.
    """
    dd = np.asarray(d, dtype=float)
    if len(pages) != len(dd):
        raise ValueError("pages and d must have the same length")
    codes, n_pages = _page_index(pages)
    counts = np.bincount(codes, minlength=n_pages).astype(float)
    sums = np.bincount(codes, weights=dd, minlength=n_pages) + shift * counts
    return sums, counts


# --------------------------------------------------------------------------- #
# Sign-flip permutation test (pages exchangeable)
# --------------------------------------------------------------------------- #
def _tail_count(stats: np.ndarray, observed: float, alternative: Alternative) -> int:
    """#{T* at least as extreme as T} for the chosen alternative."""
    if alternative == "greater":
        return int(np.count_nonzero(stats >= observed - _TOL))
    if alternative == "less":
        return int(np.count_nonzero(stats <= observed + _TOL))
    if alternative == "two-sided":
        return int(np.count_nonzero(np.abs(stats) >= abs(observed) - _TOL))
    raise ValueError(f"unknown alternative {alternative!r}")


def _mid_count(stats: np.ndarray, observed: float, alternative: Alternative) -> float:
    """#{T* strictly more extreme than T} + 0.5 * #{T* = T} (the mid-p numerator).

    Ties use the same ``_TOL`` as :func:`_tail_count`; "two-sided" compares ``|T*|``
    with ``|T|``. ``_tail_count`` = strictly-more-extreme + ties, so this is
    ``_tail_count - 0.5 * ties``.
    """
    ties = int(np.count_nonzero(np.abs(stats - observed) <= _TOL))
    if alternative == "two-sided":
        ties = int(np.count_nonzero(np.abs(np.abs(stats) - abs(observed)) <= _TOL))
    return _tail_count(stats, observed, alternative) - 0.5 * ties


def sign_flip_exact_with_mid(sums: Floats, alternative: Alternative = "greater") -> tuple[float, float]:
    """Exact sign-flip ``(p, p_mid)`` by enumerating all ``2^k`` sign vectors.

    ``p`` is exactly what :func:`sign_flip_p_exact` returns. ``p_mid =
    (#{T* more extreme than T} + 0.5 * #{T* = T}) / 2^k`` over the same
    enumeration (ties at the module tolerance). Mid-p is a reported sensitivity,
    never a decision: it is not level-alpha-guaranteed. ``k = 0`` gives
    ``(1, 0.5)``.
    """
    s = np.asarray(sums, dtype=float)
    s = s[np.abs(s) > _TOL]
    k = s.size
    if k > 25:
        raise ValueError(f"exact enumeration over {k} non-zero pages is too large")
    observed = float(s.sum())
    stats = np.zeros(1)
    for v in s:
        stats = np.concatenate((stats + v, stats - v))
    return (_tail_count(stats, observed, alternative) / stats.size,
            _mid_count(stats, observed, alternative) / stats.size)


def sign_flip_p_exact(sums: Floats, alternative: Alternative = "greater") -> float:
    """Exact sign-flip p by enumerating all ``2^k`` sign vectors of the non-zero pages.

    ``p = #{ε : Σ ε_j S_j ≥ Σ S_j} / 2^k`` (one-sided "greater"; "less" mirrors
    it; "two-sided" uses ``|T*| ≥ |T|``). Pages with ``S_j = 0`` are dropped: they
    contribute 0 under every flip. ``k = 0`` gives p = 1. Refuses ``k > 25``
    (memory: the enumeration holds ``2^k`` floats).
    """
    return sign_flip_exact_with_mid(sums, alternative)[0]


def sign_flip_mc_with_mid(
    sums: Floats,
    n_flips: int = N_FLIPS,
    seed: int = SEED,
    alternative: Alternative = "greater",
) -> tuple[float, float]:
    """Monte-Carlo sign-flip ``(p, p_mid)`` from one set of ``n_flips`` draws.

    ``p = (1 + #{T* ≥ T}) / (1 + n_flips)`` — identical to :func:`sign_flip_p_mc`.
    ``p_mid = (#{T* > T} + 0.5 * #{T* = T}) / n_flips`` does NOT add the ``+1``
    (that correction exists to keep the standard p exact-level and positive; mid-p
    is a descriptive sensitivity and is not level-guaranteed). All-zero sums give
    ``(1, 0.5)``.
    """
    s = np.asarray(sums, dtype=float)
    s = s[np.abs(s) > _TOL]
    if s.size == 0:
        return 1.0, 0.5
    observed = float(s.sum())
    rng = np.random.default_rng(seed)
    count = 0
    mid = 0.0
    chunk = max(1, 2_000_000 // s.size)
    done = 0
    while done < n_flips:
        m = min(chunk, n_flips - done)
        signs = rng.integers(0, 2, size=(m, s.size), dtype=np.int8) * 2 - 1
        stats = signs @ s
        count += _tail_count(stats, observed, alternative)
        mid += _mid_count(stats, observed, alternative)
        done += m
    return (1 + count) / (1 + n_flips), mid / n_flips


def sign_flip_p_mc(
    sums: Floats,
    n_flips: int = N_FLIPS,
    seed: int = SEED,
    alternative: Alternative = "greater",
) -> float:
    """Monte-Carlo sign-flip p: ``(1 + #{T* ≥ T}) / (1 + n_flips)``.

    ``T* = Σ_j ε_j S_j`` with ``ε_j`` i.i.d. uniform on {−1, +1}, drawn for the
    non-zero pages only (zero pages are inert). The ``+1``s count the observed
    labelling as one of the permutations, which keeps the test exact-level
    (Phipson & Smyth 2010) and makes p > 0. The matching mid-p (no ``+1``) is
    :func:`sign_flip_mc_with_mid`.
    """
    return sign_flip_mc_with_mid(sums, n_flips, seed, alternative)[0]


def sign_flip_test(
    sums: Floats,
    n_flips: int = N_FLIPS,
    seed: int = SEED,
    alternative: Alternative = "greater",
    exact_max: int = EXACT_MAX_PAGES,
) -> dict[str, Any]:
    """Primary test on page sums. Returns MC p always and the exact p when ``k ≤ exact_max``.

    Keys: ``statistic`` (``T = Σ S_j``), ``mean_page_sum`` (``T / P``),
    ``p_perm`` (Monte-Carlo), ``p_exact`` (``None`` when ``k > exact_max``),
    ``p`` (exact when available, else MC — the value to carry into Holm),
    ``p_mid`` (mid-p of the same source as ``p``: exact enumeration when
    available, else MC without the ``+1``; a reported sensitivity, never a
    decision), ``p_mid_exact`` / ``p_mid_perm``,
    ``n_pages``, ``n_pages_nonzero``, ``n_flips``, ``seed``, ``alternative``.
    """
    s = np.asarray(sums, dtype=float)
    k = int(np.count_nonzero(np.abs(s) > _TOL))
    p_mc, pm_mc = sign_flip_mc_with_mid(s, n_flips=n_flips, seed=seed, alternative=alternative)
    p_ex, pm_ex = sign_flip_exact_with_mid(s, alternative) if k <= exact_max else (None, None)
    return {
        "statistic": float(s.sum()),
        "mean_page_sum": float(s.mean()) if s.size else 0.0,
        "p_perm": float(p_mc),
        "p_exact": None if p_ex is None else float(p_ex),
        "p": float(p_ex if p_ex is not None else p_mc),
        "p_mid": float(pm_ex if pm_ex is not None else pm_mc),
        "p_mid_exact": None if pm_ex is None else float(pm_ex),
        "p_mid_perm": float(pm_mc),
        "n_pages": int(s.size),
        "n_pages_nonzero": k,
        "n_flips": int(n_flips),
        "seed": int(seed),
        "alternative": alternative,
    }


# --------------------------------------------------------------------------- #
# Page cluster bootstrap
# --------------------------------------------------------------------------- #
def _bootstrap_net_shares(
    net: np.ndarray, counts: np.ndarray, n_boot: int, seed: int
) -> np.ndarray:
    """Net share ``Σ_{j∈R} net_j / Σ_{j∈R} n_j`` over ``n_boot`` page resamples ``R``."""
    rng = np.random.default_rng(seed)
    n_pages = net.size
    out = np.empty(n_boot)
    chunk = max(1, 2_000_000 // max(n_pages, 1))
    for start in range(0, n_boot, chunk):
        m = min(chunk, n_boot - start)
        idx = rng.integers(0, n_pages, size=(m, n_pages))
        out[start:start + m] = net[idx].sum(axis=1) / counts[idx].sum(axis=1)
    return out


def page_bootstrap_ci(
    pages: Sequence[Hashable],
    a: Sequence[int | bool],
    b: Sequence[int | bool],
    n_boot: int = N_BOOT,
    seed: int = SEED,
    level: float = 0.90,
) -> dict[str, Any]:
    """Percentile CI of the net share by resampling **pages** with replacement.

    Each resample draws ``P`` pages with replacement (``P`` = number of pages)
    and recomputes ``net share* = (gained* − lost*) / n*`` on the questions of
    the drawn pages (so ``n*`` varies when pages have unequal sizes — the
    statistic is a ratio estimator). CI = the ``(1−level)/2`` and
    ``(1+level)/2`` percentiles (numpy's default linear interpolation). With
    ``level = 0.90`` the two ends are the one-sided 95% lower and upper bounds.
    """
    d = paired_differences(a, b)
    net, counts = page_sums(pages, d)
    point = float(d.sum() / d.size) if d.size else float("nan")
    if net.size == 0:
        return {"point": point, "ci": [float("nan"), float("nan")], "level": level}
    vals = _bootstrap_net_shares(net, counts, n_boot, seed)
    lo, hi = np.percentile(vals, [100 * (1 - level) / 2, 100 * (1 + level) / 2])
    return {
        "point": point,
        "ci": [float(lo), float(hi)],
        "level": float(level),
        "n_boot": int(n_boot),
        "seed": int(seed),
    }


# --------------------------------------------------------------------------- #
# Exact McNemar
# --------------------------------------------------------------------------- #
def mcnemar_exact(gained: int, lost: int, alternative: Alternative = "two-sided") -> float:
    """Exact McNemar test on the discordant pairs, ``G ~ Binomial(D, 1/2)``, ``D = g + l``.

    - two-sided: ``min(1, 2 · Σ_{i=0}^{min(g,l)} C(D,i) / 2^D)`` (doubled smaller
      tail; identical to ``section_aware_trial.mcnemar_p``);
    - "greater" (A better, g > l): ``P(G ≥ g) = Σ_{i=g}^{D} C(D,i) / 2^D``;
    - "less": ``P(G ≤ g)``.

    ``D = 0`` gives 1. Integer arithmetic until the final division, so no
    under/overflow for any ``D``.
    """
    n = gained + lost
    if n == 0:
        return 1.0
    denom = 2**n
    if alternative == "two-sided":
        k = min(gained, lost)
        return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / denom)
    if alternative == "greater":
        return sum(math.comb(n, i) for i in range(gained, n + 1)) / denom
    if alternative == "less":
        return sum(math.comb(n, i) for i in range(gained + 1)) / denom
    raise ValueError(f"unknown alternative {alternative!r}")


# --------------------------------------------------------------------------- #
# Contrast and non-inferiority
# --------------------------------------------------------------------------- #
def contrast(
    pages: Sequence[Hashable],
    a: Sequence[int | bool],
    b: Sequence[int | bool],
    alternative: Alternative = "greater",
    n_flips: int = N_FLIPS,
    n_boot: int = N_BOOT,
    seed: int = SEED,
    ci_level: float = 0.90,
) -> dict[str, Any]:
    """Full superiority summary of arm A − arm B on page-clustered paired outcomes.

    Keys: ``gained``, ``lost``, ``net``, ``n``, ``net_share``, ``p_perm`` (MC
    sign-flip), ``p_exact`` (or ``None``), ``p`` (exact if available else MC), ``p_mid`` (sensitivity),
    ``p_mcnemar`` (exact McNemar, same ``alternative`` — descriptive: it ignores
    the clustering), ``p_mcnemar_two_sided``, ``ci`` (page-bootstrap percentile
    CI of the net share at ``ci_level``), ``ci_level``, ``n_pages``,
    ``n_pages_nonzero``, ``alternative``, ``n_flips``, ``n_boot``, ``seed``.
    """
    d = paired_differences(a, b)
    sums, _ = page_sums(pages, d)
    gained, lost = int((d == 1).sum()), int((d == -1).sum())
    test = sign_flip_test(sums, n_flips=n_flips, seed=seed, alternative=alternative)
    boot = page_bootstrap_ci(pages, a, b, n_boot=n_boot, seed=seed, level=ci_level)
    return {
        "gained": gained,
        "lost": lost,
        "net": gained - lost,
        "n": int(d.size),
        "net_share": (gained - lost) / d.size if d.size else float("nan"),
        "p_perm": test["p_perm"],
        "p_exact": test["p_exact"],
        "p": test["p"],
        "p_mid": test["p_mid"],
        "p_mcnemar": mcnemar_exact(gained, lost, alternative),
        "p_mcnemar_two_sided": mcnemar_exact(gained, lost, "two-sided"),
        "ci": boot["ci"],
        "ci_level": ci_level,
        "n_pages": test["n_pages"],
        "n_pages_nonzero": test["n_pages_nonzero"],
        "alternative": alternative,
        "n_flips": n_flips,
        "n_boot": n_boot,
        "seed": seed,
    }


def noninferiority_test(
    pages: Sequence[Hashable],
    a: Sequence[int | bool],
    b: Sequence[int | bool],
    delta: float,
    alpha: float = 0.05,
    n_flips: int = N_FLIPS,
    n_boot: int = N_BOOT,
    seed: int = SEED,
    ci_level: float = 0.90,
) -> dict[str, Any]:
    """Non-inferiority of A vs B with margin ``δ`` (a net share): H0 mean d ≤ −δ.

    **Permutation decision.** Shift every question's difference to
    ``d_i + δ`` and run the one-sided ("greater") sign-flip test on the shifted
    page sums ``S_j + δ·n_j``. At the H0 boundary (mean d = −δ) the shifted
    values have mean 0. *Assumption:* under H0 the shifted page sums are
    symmetric about 0 — the usual shift-to-null construction. It is approximate
    here because ``d`` is bounded in {−1, 0, +1}: a shifted distribution with
    mean 0 need not be symmetric (e.g. it depends on how the null mass is split
    between discordance and direction), so the level is nominal, not exact.
    Reject H0 (claim non-inferiority) when ``p ≤ alpha``.

    **CI decision.** Claim non-inferiority when the lower bound of the
    page-cluster-bootstrap percentile CI at ``ci_level`` (0.90 two-sided → the
    one-sided 95% lower bound) exceeds ``−δ``.

    The exact enumeration is reported when ≤ 20 shifted page sums are non-zero
    (rare: the shift makes almost every page non-zero).
    """
    d = paired_differences(a, b)
    shifted, _ = page_sums(pages, d, shift=delta)
    test = sign_flip_test(shifted, n_flips=n_flips, seed=seed, alternative="greater")
    boot = page_bootstrap_ci(pages, a, b, n_boot=n_boot, seed=seed, level=ci_level)
    gained, lost = int((d == 1).sum()), int((d == -1).sum())
    return {
        "delta": float(delta),
        "gained": gained,
        "lost": lost,
        "net": gained - lost,
        "n": int(d.size),
        "net_share": (gained - lost) / d.size if d.size else float("nan"),
        "p_perm": test["p_perm"],
        "p_exact": test["p_exact"],
        "p": test["p"],
        "p_mid": test["p_mid"],  # on the shifted page sums; sensitivity only
        "alpha": float(alpha),
        "reject_perm": bool(test["p"] <= alpha),
        "ci": boot["ci"],
        "ci_level": float(ci_level),
        "reject_ci": bool(boot["ci"][0] > -delta),
        "n_pages": test["n_pages"],
        "n_pages_nonzero": test["n_pages_nonzero"],
        "n_flips": n_flips,
        "n_boot": n_boot,
        "seed": seed,
    }


# --------------------------------------------------------------------------- #
# Holm
# --------------------------------------------------------------------------- #
def holm(pvalues: Sequence[float], alpha: float = 0.05) -> dict[str, list[Any]]:
    """Holm (1979) step-down over a family of ``m`` p-values.

    Sort ascending ``p_(1) ≤ … ≤ p_(m)``; reject ``H_(i)`` for ``i < i*`` where
    ``i*`` is the first (1-based) index with ``p_(i) > alpha / (m − i + 1)``.
    Adjusted ``p̃_(i) = max_{r≤i} min(1, (m − r + 1) p_(r))``; reject iff
    ``p̃ ≤ alpha``. Delegates to ``_stats.holm_bonferroni``; returns lists in
    the input order: ``{"p_adj": [...], "reject": [...]}``.
    """
    keyed = {str(i): float(p) for i, p in enumerate(pvalues)}
    res = _stats.holm_bonferroni(keyed, alpha=alpha)
    return {
        "p_adj": [float(res[str(i)][0]) for i in range(len(keyed))],
        "reject": [bool(res[str(i)][1]) for i in range(len(keyed))],
    }


# --------------------------------------------------------------------------- #
# Agreement: Cohen's κ and a proportion with Clopper–Pearson
# --------------------------------------------------------------------------- #
def _kappa_from_counts(counts: np.ndarray) -> np.ndarray:
    """κ for a stack of k×k count tables, shape (..., k, k). NaN where Pe = 1."""
    n = counts.sum(axis=(-2, -1))
    po = np.trace(counts, axis1=-2, axis2=-1) / n
    pe = (counts.sum(axis=-1) * counts.sum(axis=-2)).sum(axis=-1) / n**2
    with np.errstate(divide="ignore", invalid="ignore"):
        k = (po - pe) / (1.0 - pe)
    return np.where(np.isclose(pe, 1.0), np.nan, k)


def kappa_from_table(table: Sequence[Sequence[float]] | np.ndarray) -> dict[str, float]:
    """Unweighted Cohen's κ from a k×k confusion table (rows rater 1, cols rater 2).

    ``Po = Σ_i n_ii / n``, ``Pe = Σ_i (n_i· / n)(n_·i / n)``,
    ``κ = (Po − Pe) / (1 − Pe)`` (NaN when Pe = 1). Binary is the 2×2 case.
    """
    t = np.asarray(table, dtype=float)
    if t.ndim != 2 or t.shape[0] != t.shape[1]:
        raise ValueError("table must be square")
    n = t.sum()
    po = float(np.trace(t) / n)
    pe = float((t.sum(axis=1) * t.sum(axis=0)).sum() / n**2)
    return {"kappa": float(_kappa_from_counts(t)), "po": po, "pe": pe, "n": float(n)}


def cohen_kappa_ci(
    r1: Sequence[Hashable],
    r2: Sequence[Hashable],
    categories: Sequence[Hashable] | None = None,
    n_boot: int = N_BOOT,
    seed: int = SEED,
    level: float = 0.95,
) -> dict[str, Any]:
    """Unweighted Cohen's κ (any number of classes) with a percentile item-bootstrap CI.

    Items (paired ratings) are resampled with replacement; κ is recomputed on
    each resample; resamples where κ is undefined (Pe = 1) are dropped and
    counted in ``n_boot_undefined``. Point κ is the same as
    ``_stats.cohen_kappa(..., weights="none")``.
    """
    if len(r1) != len(r2):
        raise ValueError("rating vectors must be the same length")
    cats = list(categories) if categories is not None else sorted(
        set(r1) | set(r2), key=repr
    )
    k = len(cats)
    pos = {c: i for i, c in enumerate(cats)}
    codes = np.array([pos[x] * k + pos[y] for x, y in zip(r1, r2, strict=True)], dtype=np.int64)
    n = codes.size
    table = np.bincount(codes, minlength=k * k).reshape(k, k).astype(float)
    point = kappa_from_table(table)
    rng = np.random.default_rng(seed)
    vals = np.empty(n_boot)
    chunk = max(1, 2_000_000 // max(n, 1))
    for start in range(0, n_boot, chunk):
        m = min(chunk, n_boot - start)
        idx = rng.integers(0, n, size=(m, n))
        flat = (np.arange(m)[:, None] * (k * k) + codes[idx]).ravel()
        tabs = np.bincount(flat, minlength=m * k * k).reshape(m, k, k).astype(float)
        vals[start:start + m] = _kappa_from_counts(tabs)
    ok = vals[np.isfinite(vals)]
    if ok.size:
        lo, hi = np.percentile(ok, [100 * (1 - level) / 2, 100 * (1 + level) / 2])
    else:
        lo = hi = float("nan")
    return {
        "kappa": point["kappa"],
        "po": point["po"],
        "pe": point["pe"],
        "n": int(n),
        "categories": [str(c) for c in cats],
        "ci": [float(lo), float(hi)],
        "level": float(level),
        "n_boot": int(n_boot),
        "n_boot_undefined": int(n_boot - ok.size),
        "seed": int(seed),
    }


def _binom_cdf(x: int, n: int, p: float) -> float:
    """``P(X ≤ x)`` for ``X ~ Binomial(n, p)``, summed in log space (lgamma)."""
    if x < 0:
        return 0.0
    if x >= n:
        return 1.0
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 0.0
    lp, lq = math.log(p), math.log1p(-p)
    lc = math.lgamma(n + 1)
    return min(1.0, sum(
        math.exp(lc - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * lp + (n - i) * lq)
        for i in range(x + 1)
    ))


def _bisect(f: Any, lo: float, hi: float, iters: int = 200) -> float:
    """Root of a monotone ``f`` on [lo, hi] (sign change assumed)."""
    flo = f(lo)
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        fm = f(mid)
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
        if hi - lo < 1e-15:
            break
    return 0.5 * (lo + hi)


def clopper_pearson(k: int, n: int, level: float = 0.95) -> tuple[float, float]:
    """Exact (Clopper–Pearson) two-sided CI for a binomial proportion ``k/n``.

    With ``a = (1 − level)/2``: the lower bound ``p_L`` solves
    ``P(X ≥ k | p_L) = a`` (``p_L = 0`` if ``k = 0``) and the upper bound ``p_U``
    solves ``P(X ≤ k | p_U) = a`` (``p_U = 1`` if ``k = n``) — equivalently the
    ``a`` and ``1−a`` quantiles of Beta(k, n−k+1) and Beta(k+1, n−k). Solved by
    bisection on the binomial CDF (no scipy). Closed forms at the edges:
    ``k = 0 → p_U = 1 − a^{1/n}``; ``k = n → p_L = a^{1/n}``.
    """
    if n <= 0 or not 0 <= k <= n:
        raise ValueError("need 0 <= k <= n and n > 0")
    a = (1.0 - level) / 2.0
    lo = 0.0 if k == 0 else _bisect(lambda p: (1.0 - _binom_cdf(k - 1, n, p)) - a, 0.0, 1.0)
    hi = 1.0 if k == n else _bisect(lambda p: _binom_cdf(k, n, p) - a, 0.0, 1.0)
    return lo, hi


def proportion_ci(k: int, n: int, level: float = 0.95) -> dict[str, Any]:
    """A proportion (e.g. P(human sides with arm X)) with its Clopper–Pearson CI."""
    lo, hi = clopper_pearson(k, n, level)
    return {"k": int(k), "n": int(n), "p": k / n, "ci": [lo, hi], "level": float(level)}


# --------------------------------------------------------------------------- #
# Power by simulation
# --------------------------------------------------------------------------- #
def even_split(n_questions: int, n_pages: int) -> list[int]:
    """``n_questions`` spread over ``n_pages`` as evenly as possible (sizes differ by ≤ 1)."""
    base, extra = divmod(n_questions, n_pages)
    return [base + 1] * extra + [base] * (n_pages - extra)


def _beta_params(mean: float, icc: float) -> tuple[float, float]:
    """Beta(α, β) with the given mean and ``1/(α+β+1) = icc`` (beta-binomial ICC)."""
    s = (1.0 - icc) / icc
    return mean * s, (1.0 - mean) * s


def _page_probs(
    rng: np.random.Generator, mean: float, icc: float, shape: tuple[int, int]
) -> np.ndarray:
    if icc <= 0.0 or mean <= 0.0 or mean >= 1.0:
        return np.full(shape, mean)
    al, be = _beta_params(mean, icc)
    return rng.beta(al, be, size=shape)


def simulate_power(
    n_pages: int,
    questions_per_page: int | Sequence[int],
    discordance: float,
    discordant_share: float,
    icc: float = 0.0,
    share_icc: float = 0.0,
    alpha: float = 0.05,
    n_family: int = 1,
    alternative: Alternative = "greater",
    delta: float = 0.0,
    n_sims: int = 2000,
    n_flips: int = 2000,
    seed: int = SEED,
    with_ci: bool = False,
    n_boot: int = 2000,
    ci_level: float = 0.90,
) -> dict[str, Any]:
    """Power of the page sign-flip test for **one reader**, by simulation.

    Generative model (per simulated study, per page ``j`` with ``n_j`` questions):

    - discordance probability ``π_j ~ Beta`` with mean ``discordance`` and
      intra-class correlation ``icc`` (``α+β = (1−icc)/icc``); ``icc = 0`` means
      ``π_j = discordance`` for all pages. ``icc`` is exactly the correlation of
      two questions' discordance indicators on the same page (beta-binomial);
    - discordant count ``D_j ~ Binomial(n_j, π_j)``;
    - "A wins" probability given discordance ``q_j``, mean
      ``q = (1 + discordant_share)/2`` — so E[(gained − lost)/D] =
      ``discordant_share`` — with its own page ICC ``share_icc`` (default 0:
      the direction of a discordant pair is independent across questions);
    - gained ``G_j ~ Binomial(D_j, q_j)``; page sum ``S_j = 2G_j − D_j + δ·n_j``.

    The true net share is ``discordance × discordant_share``. Each simulated
    study is tested with the Monte-Carlo sign-flip test (``n_flips`` flips;
    p = (1 + #)/(1 + n_flips)) at level ``alpha / n_family`` (the Holm worst
    case — the smallest p in the family faces α/m). Power = share of studies
    rejecting (for "two-sided": rejecting in either direction). One sign matrix
    is drawn once and reused for every simulated study: each study's p is still
    a valid MC p; reuse only correlates the MC error across studies.

    ``delta > 0`` with ``alternative="greater"`` gives non-inferiority power
    (H0 mean d ≤ −δ). ``with_ci=True`` also returns the CI-decision power:
    share of studies whose page-bootstrap percentile lower bound at
    ``ci_level`` exceeds ``−δ`` (superiority: exceeds 0 when ``delta = 0``).

    ``questions_per_page``: an int (all pages equal), a sequence of length
    ``n_pages`` (fixed layout), or any other-length sequence, treated as an
    empirical distribution sampled with replacement per simulated study.

    Joint power for independent readers is the product (:func:`joint_power`).
    """
    rng = np.random.default_rng(seed)
    if isinstance(questions_per_page, int):
        nq = np.full((n_sims, n_pages), questions_per_page, dtype=np.int64)
    else:
        layout = np.asarray(questions_per_page, dtype=np.int64)
        if layout.size == n_pages:
            nq = np.broadcast_to(layout, (n_sims, n_pages)).copy()
        else:
            nq = rng.choice(layout, size=(n_sims, n_pages), replace=True)
    shape = (n_sims, n_pages)
    pi = _page_probs(rng, discordance, icc, shape)
    q_mean = (1.0 + discordant_share) / 2.0
    q = _page_probs(rng, q_mean, share_icc, shape)
    disc = rng.binomial(nq, pi)
    gained = rng.binomial(disc, q)
    net = 2 * gained - disc
    sums = net + delta * nq

    signs = (rng.integers(0, 2, size=(n_flips, n_pages), dtype=np.int8) * 2 - 1).astype(float)
    obs = sums.sum(axis=1)
    flipped = sums @ signs.T  # (n_sims, n_flips)
    if alternative == "greater":
        cnt = (flipped >= obs[:, None] - _TOL).sum(axis=1)
    elif alternative == "less":
        cnt = (flipped <= obs[:, None] + _TOL).sum(axis=1)
    elif alternative == "two-sided":
        cnt = (np.abs(flipped) >= np.abs(obs)[:, None] - _TOL).sum(axis=1)
    else:
        raise ValueError(f"unknown alternative {alternative!r}")
    p = (1 + cnt) / (1 + n_flips)
    level = alpha / n_family
    reject = p <= level
    power = float(reject.mean())
    out: dict[str, Any] = {
        "power": power,
        "mc_se": math.sqrt(power * (1 - power) / n_sims),
        "alpha_test": level,
        "true_net_share": discordance * discordant_share,
        "mean_net_share": float((net.sum(axis=1) / nq.sum(axis=1)).mean()),
        "n_pages": n_pages,
        "mean_questions": float(nq.sum(axis=1).mean()),
        "discordance": discordance,
        "discordant_share": discordant_share,
        "icc": icc,
        "share_icc": share_icc,
        "alternative": alternative,
        "delta": delta,
        "n_sims": n_sims,
        "n_flips": n_flips,
        "seed": seed,
    }
    if with_ci:
        bidx = rng.integers(0, n_pages, size=(n_boot, n_pages))
        lo_q = 100 * (1 - ci_level) / 2
        lows = np.empty(n_sims)
        chunk = max(1, 4_000_000 // (n_boot * n_pages))
        netf, nqf = net.astype(float), nq.astype(float)
        for s0 in range(0, n_sims, chunk):
            sl = slice(s0, min(n_sims, s0 + chunk))
            shares = netf[sl][:, bidx].sum(axis=2) / nqf[sl][:, bidx].sum(axis=2)
            lows[sl] = np.percentile(shares, lo_q, axis=1)
        ci_reject = lows > -delta
        cp = float(ci_reject.mean())
        out["power_ci"] = cp
        out["mc_se_ci"] = math.sqrt(cp * (1 - cp) / n_sims)
        out["ci_level"] = ci_level
    return out


def joint_power(*powers: float) -> float:
    """Joint power of independent readers (all must reject): the product."""
    return float(np.prod(powers))
