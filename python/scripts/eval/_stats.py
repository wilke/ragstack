#!/usr/bin/env python
"""Shared statistics layer for the chunking-eval harnesses (pure stdlib + numpy).

The known-item and SciFact harnesses both retain **per-query** metric arrays (one
value per eval query, per config) and hand them here to get:

- **Per-metric 95% CIs** via a paired bootstrap over the query indices
  (:func:`bootstrap_metric_ci`) — resample the *same* query indices for every
  config on each iteration so configs stay comparable.
- **Pairwise difference CIs** vs a reference config (:func:`bootstrap_diff_ci`) —
  bootstrap the paired per-query difference of a metric.
- **A paired significance test** on per-query reciprocal-rank (or any per-query
  metric) deltas vs the reference: Wilcoxon signed-rank
  (:func:`wilcoxon_signed_rank`) with **Holm–Bonferroni** multiplicity correction
  (:func:`holm_bonferroni`) across the config comparisons.
- **Benjamini–Hochberg FDR** (:func:`benjamini_hochberg`) for the *screening*
  stage of a two-stage design, where a false lead is cheap because a
  confirmatory stage will kill it and FWER control would be crippling.
- **Rank-biased overlap** (:func:`rbo`) for comparing two ranked lists — the
  replicate-stability measure for an A/A null.

Everything is implemented directly (no scipy). The bootstrap is seeded
(``numpy.random.default_rng(0)``) so a re-run reproduces the intervals exactly.

A metric is summarized per config as the **mean over per-query values** (recall@k
is a 0/1 indicator per query whose mean is the recall; reciprocal-rank's mean is
MRR; nDCG's per-query gain mean is nDCG; MAP is the mean of per-query average
precision). So "resample queries, recompute the config's mean" is the natural
paired bootstrap for all of them.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

BOOTSTRAP_ITERS = 10_000
SEED = 0


@dataclass(frozen=True)
class CI:
    """A point estimate with a percentile confidence interval."""

    point: float
    lo: float
    hi: float

    def fmt(self, places: int = 3) -> str:
        return f"{self.point:.{places}f} [{self.lo:.{places}f}, {self.hi:.{places}f}]"


def _resample_indices(
    n: int, iters: int, seed: int
) -> np.ndarray:
    """Return an (iters, n) array of query indices resampled with replacement.

    The SAME index matrix is used to resample every config, which is what makes
    the bootstrap *paired* — on iteration t all configs see query set t, so a
    difference CI reflects only the config, not resampling noise.
    """
    rng = np.random.default_rng(seed)
    return rng.integers(0, n, size=(iters, n))


def bootstrap_metric_ci(
    per_query: dict[str, list[float]],
    iters: int = BOOTSTRAP_ITERS,
    seed: int = SEED,
    alpha: float = 0.05,
) -> dict[str, CI]:
    """Paired bootstrap 95% CI for each config's mean metric.

    ``per_query`` maps config key → list of per-query metric values (all lists the
    same length = #queries, aligned by query index). Returns config key → CI.
    """
    keys = list(per_query)
    if not keys:
        return {}
    n = len(per_query[keys[0]])
    if n == 0:
        return {k: CI(0.0, 0.0, 0.0) for k in keys}
    idx = _resample_indices(n, iters, seed)
    lo_p, hi_p = 100 * (alpha / 2), 100 * (1 - alpha / 2)
    out: dict[str, CI] = {}
    for k in keys:
        arr = np.asarray(per_query[k], dtype=float)
        if len(arr) != n:
            raise ValueError(f"per-query length mismatch for {k!r}: {len(arr)} != {n}")
        means = arr[idx].mean(axis=1)  # (iters,)
        out[k] = CI(
            point=float(arr.mean()),
            lo=float(np.percentile(means, lo_p)),
            hi=float(np.percentile(means, hi_p)),
        )
    return out


def bootstrap_diff_ci(
    per_query: dict[str, list[float]],
    reference: str,
    iters: int = BOOTSTRAP_ITERS,
    seed: int = SEED,
    alpha: float = 0.05,
) -> dict[str, CI]:
    """Paired bootstrap 95% CI of ``(config - reference)`` mean-metric difference.

    Uses the same resampled query indices for both configs on every iteration, so
    the difference is fully paired. The reference itself maps to a degenerate CI at
    0. Returns config key → CI of the difference (excluding the reference).
    """
    keys = list(per_query)
    if reference not in per_query:
        raise ValueError(f"reference {reference!r} not in per_query configs")
    n = len(per_query[reference])
    if n == 0:
        return {k: CI(0.0, 0.0, 0.0) for k in keys if k != reference}
    idx = _resample_indices(n, iters, seed)
    lo_p, hi_p = 100 * (alpha / 2), 100 * (1 - alpha / 2)
    ref = np.asarray(per_query[reference], dtype=float)
    ref_means = ref[idx].mean(axis=1)
    out: dict[str, CI] = {}
    for k in keys:
        if k == reference:
            continue
        arr = np.asarray(per_query[k], dtype=float)
        diff_means = arr[idx].mean(axis=1) - ref_means
        out[k] = CI(
            point=float(arr.mean() - ref.mean()),
            lo=float(np.percentile(diff_means, lo_p)),
            hi=float(np.percentile(diff_means, hi_p)),
        )
    return out


# --------------------------------------------------------------------------- #
# Wilcoxon signed-rank (two-sided, normal approximation with tie/continuity
# correction) — implemented directly so we don't pull scipy.
# --------------------------------------------------------------------------- #
def _normal_sf(z: float) -> float:
    """Upper-tail of the standard normal via erfc (survival function)."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def wilcoxon_signed_rank(a: list[float], b: list[float]) -> tuple[float, float]:
    """Two-sided Wilcoxon signed-rank test on paired samples ``a`` vs ``b``.

    Returns ``(statistic W, p_value)``. Zero differences are dropped (Wilcoxon
    convention). Uses the normal approximation with a tie correction to the
    variance and a continuity correction — adequate for the ~300-query SciFact set
    and the known-item eval, and dependency-free. If every difference is zero the
    p-value is 1.0 (no evidence of a difference).
    """
    diffs = [x - y for x, y in zip(a, b, strict=True) if x - y != 0.0]
    n = len(diffs)
    if n == 0:
        return 0.0, 1.0
    absd = [abs(d) for d in diffs]
    order = sorted(range(n), key=lambda i: absd[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and absd[order[j + 1]] == absd[order[i]]:
            j += 1
        avg_rank = (i + 1 + j + 1) / 2.0  # average rank for ties (1-based)
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    w_plus = sum(r for r, d in zip(ranks, diffs, strict=True) if d > 0)
    w_minus = sum(r for r, d in zip(ranks, diffs, strict=True) if d < 0)
    w = min(w_plus, w_minus)
    mean_w = n * (n + 1) / 4.0
    # Tie correction to the variance.
    tie_term = 0.0
    counts: dict[float, int] = {}
    for v in absd:
        counts[v] = counts.get(v, 0) + 1
    for c in counts.values():
        if c > 1:
            tie_term += c**3 - c
    var_w = n * (n + 1) * (2 * n + 1) / 24.0 - tie_term / 48.0
    if var_w <= 0:
        return w, 1.0
    z = (w - mean_w)
    # Continuity correction toward the mean.
    z = (z + 0.5) if z < 0 else (z - 0.5)
    z /= math.sqrt(var_w)
    p = 2.0 * _normal_sf(abs(z))
    return w, min(1.0, p)


def holm_bonferroni(
    pvalues: dict[str, float], alpha: float = 0.05
) -> dict[str, tuple[float, bool]]:
    """Holm–Bonferroni step-down correction across the config comparisons.

    ``pvalues`` maps comparison key → raw p-value. Returns key → (adjusted p,
    rejected-at-alpha). Adjusted p for the i-th smallest raw p (0-based) is
    ``(m - i) * p`` clamped to a monotone non-decreasing sequence and capped at 1.
    """
    items = sorted(pvalues.items(), key=lambda kv: kv[1])
    m = len(items)
    out: dict[str, tuple[float, bool]] = {}
    prev_adj = 0.0
    still_rejecting = True
    for i, (key, p) in enumerate(items):
        adj = min(1.0, (m - i) * p)
        adj = max(adj, prev_adj)  # enforce monotonicity
        prev_adj = adj
        if still_rejecting and adj <= alpha:
            rejected = True
        else:
            rejected = False
            still_rejecting = False
        out[key] = (adj, rejected)
    return out


def benjamini_hochberg(
    pvalues: dict[str, float], q: float = 0.10
) -> dict[str, tuple[float, bool]]:
    """Benjamini–Hochberg FDR control across a family of comparisons.

    ``pvalues`` maps comparison key → raw p-value. Returns key → (BH-adjusted p,
    discovered-at-``q``).

    BH controls the *expected proportion of false discoveries* rather than the
    probability of any false discovery, which is the right criterion for a
    **screen**: carrying a false lead into a confirmatory stage costs one extra
    confirmatory test, whereas Holm over a large grid would let only a huge
    effect through and the screen would nominate nothing. Adjusted p for the
    i-th smallest raw p (1-based) is ``m * p / i``, made monotone
    non-increasing from the largest p downward and capped at 1 — so a
    BH-adjusted p is directly comparable to ``q``.
    """
    items = sorted(pvalues.items(), key=lambda kv: kv[1])
    m = len(items)
    out: dict[str, tuple[float, bool]] = {}
    running = 1.0
    # Walk from the largest p down, enforcing monotonicity of the adjusted values.
    for i in range(m - 1, -1, -1):
        key, p = items[i]
        adj = min(1.0, m * p / (i + 1))
        running = min(running, adj)
        out[key] = (running, running <= q)
    return out


def rbo(a: list[str], b: list[str], p: float = 0.9) -> float:
    """Rank-biased overlap of two ranked id lists (Webber et al., 2010).

    Top-weighted set agreement in ``[0, 1]``: 1.0 for identical prefixes, and
    disagreement deep in the list is discounted geometrically by ``p``. Used as
    the A/A replicate-stability measure (protocol §6.4) because two runs of the
    *same* configuration against an HNSW index can legitimately differ in the
    tail while agreeing exactly where it matters.

    This is the finite-depth (non-extrapolated) form: the sum is truncated at
    ``max(len(a), len(b))`` and normalized by the same truncated weight mass, so
    the value is a weighted mean of the prefix agreements actually observed
    rather than an estimate of the infinite-depth quantity.
    """
    depth = max(len(a), len(b))
    if depth == 0:
        return 1.0
    seen_a: set[str] = set()
    seen_b: set[str] = set()
    total = 0.0
    weight_mass = 0.0
    for d in range(depth):
        if d < len(a):
            seen_a.add(a[d])
        if d < len(b):
            seen_b.add(b[d])
        w = p**d
        total += w * (len(seen_a & seen_b) / (d + 1))
        weight_mass += w
    return total / weight_mass if weight_mass else 1.0


# --------------------------------------------------------------------------- #
# Per-query metric helpers
# --------------------------------------------------------------------------- #
def reciprocal_rank(rank: int | None, cap: int | None = None) -> float:
    """1/rank for a 1-based rank (None → 0). ``cap`` zeroes ranks beyond it."""
    if rank is None:
        return 0.0
    if cap is not None and rank > cap:
        return 0.0
    return 1.0 / rank


def hit_at_k(rank: int | None, k: int) -> float:
    """1.0 if a relevant item was found at rank <= k, else 0.0 (single-relevant)."""
    return 1.0 if (rank is not None and rank <= k) else 0.0


def dcg_single(rank: int | None, k: int) -> float:
    """nDCG@k for the single-relevant case (ideal DCG = 1): 1/log2(rank+1)."""
    if rank is None or rank > k:
        return 0.0
    return 1.0 / math.log2(rank + 1)


def ndcg_at_k(ranked_doc_ids: list[str], qrels: dict[str, int], k: int) -> float:
    """Graded nDCG@k for multi-relevant qrels (BEIR standard).

    ``qrels`` maps doc_id → relevance grade (>=1 relevant). DCG uses gain
    ``2**grade - 1`` and the standard ``log2(rank+1)`` discount; IDCG is computed
    over the best-possible ordering of the graded relevances.
    """
    dcg = 0.0
    for i, did in enumerate(ranked_doc_ids[:k]):
        grade = qrels.get(did, 0)
        if grade > 0:
            dcg += (2**grade - 1) / math.log2(i + 2)
    ideal = sorted((g for g in qrels.values() if g > 0), reverse=True)[:k]
    idcg = sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg > 0 else 0.0


def recall_at_k(ranked_doc_ids: list[str], qrels: dict[str, int], k: int) -> float:
    """Recall@k over the set of relevant docs (grade>=1) for multi-relevant qrels."""
    relevant = {d for d, g in qrels.items() if g > 0}
    if not relevant:
        return 0.0
    found = sum(1 for d in ranked_doc_ids[:k] if d in relevant)
    return found / len(relevant)


def average_precision(ranked_doc_ids: list[str], qrels: dict[str, int]) -> float:
    """Average precision over the full ranking for multi-relevant qrels (MAP part)."""
    relevant = {d for d, g in qrels.items() if g > 0}
    if not relevant:
        return 0.0
    hits = 0
    score = 0.0
    for i, did in enumerate(ranked_doc_ids):
        if did in relevant:
            hits += 1
            score += hits / (i + 1)
    return score / len(relevant)


# --------------------------------------------------------------------------- #
# Report assembly
# --------------------------------------------------------------------------- #
def build_stats_table(
    config_keys: list[str],
    reference: str,
    metrics: dict[str, dict[str, list[float]]],
    primary_metric: str,
    delta_per_query: dict[str, list[float]],
    iters: int = BOOTSTRAP_ITERS,
    seed: int = SEED,
) -> tuple[str, str]:
    """Build the markdown stats table + a one-line honest interpretation.

    ``metrics`` maps metric-name → {config → per-query list}. For every metric we
    emit each config's ``point [lo, hi]`` (paired bootstrap CI). We additionally
    compute, for ``primary_metric``, the pairwise **difference** CI vs the
    ``reference`` and a Wilcoxon test on the primary metric's per-query deltas
    (``delta_per_query`` — reciprocal rank for the known-item harness, nDCG for
    SciFact) with Holm–Bonferroni correction, marking each config as
    distinguishable (``*``) or not from the reference.

    Returns ``(markdown_table, interpretation_line)``.
    """
    metric_names = list(metrics)
    cis: dict[str, dict[str, CI]] = {
        m: bootstrap_metric_ci(metrics[m], iters=iters, seed=seed) for m in metric_names
    }
    diff_ci = bootstrap_diff_ci(
        metrics[primary_metric], reference, iters=iters, seed=seed
    )
    # Wilcoxon on the primary metric's per-query deltas vs reference, Holm-corrected.
    raw_p: dict[str, float] = {}
    for k in config_keys:
        if k == reference:
            continue
        _, p = wilcoxon_signed_rank(delta_per_query[k], delta_per_query[reference])
        raw_p[k] = p
    holm = holm_bonferroni(raw_p) if raw_p else {}

    header_cols = ["config"] + [f"{m}" for m in metric_names]
    header_cols += [f"Δ{primary_metric} vs {reference}", "Wilcoxon p (Holm)", "distinguishable"]
    header = "| " + " | ".join(header_cols) + " |\n"
    sep = "|" + "---|" * len(header_cols) + "\n"
    rows = ""
    n_distinct = 0
    for k in config_keys:
        cells = [f"`{k}`"]
        for m in metric_names:
            cells.append(cis[m][k].fmt())
        if k == reference:
            cells += ["— (ref)", "—", "ref"]
        else:
            d = diff_ci[k]
            adj_p, rejected = holm.get(k, (float("nan"), False))
            distinct = rejected
            if distinct:
                n_distinct += 1
            cells += [
                d.fmt(),
                "n/a" if math.isnan(adj_p) else f"{adj_p:.3f}",
                "yes *" if distinct else "no",
            ]
        rows += "| " + " | ".join(cells) + " |\n"

    if n_distinct == 0:
        interp = (
            f"**Interpretation:** no config differs from `{reference}` beyond its "
            f"95% CI on {primary_metric} (paired bootstrap difference CIs all span 0 "
            f"and no Wilcoxon test survives Holm–Bonferroni). The configs are "
            f"statistically indistinguishable on this eval."
        )
    else:
        distinct_keys = [
            k for k in config_keys
            if k != reference and holm.get(k, (1.0, False))[1]
        ]
        interp = (
            f"**Interpretation:** {n_distinct} config(s) differ from `{reference}` on "
            f"{primary_metric} after Holm–Bonferroni: "
            f"{', '.join('`' + k + '`' for k in distinct_keys)}. See the difference-CI "
            f"column for direction and magnitude."
        )
    return header + sep + rows, interp
