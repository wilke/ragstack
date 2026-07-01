"""Unit tests for the chunking-eval statistics layer (scripts/eval/_stats.py).

The module lives under ``scripts/eval`` (not an installed package), so we add it
to ``sys.path`` the same way the harnesses do.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

_EVAL_DIR = Path(__file__).resolve().parents[2] / "scripts" / "eval"
sys.path.insert(0, str(_EVAL_DIR))

import _stats  # noqa: E402


def test_bootstrap_metric_ci_brackets_point():
    per_query = {"a": [1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 1.0, 0.0]}
    cis = _stats.bootstrap_metric_ci(per_query, iters=2000, seed=0)
    ci = cis["a"]
    assert math.isclose(ci.point, 0.625, rel_tol=1e-9)
    assert ci.lo <= ci.point <= ci.hi
    assert 0.0 <= ci.lo and ci.hi <= 1.0


def test_bootstrap_diff_ci_reference_excluded_and_paired():
    # b is uniformly 0.1 better than a per query → diff point ~0.1, CI tight & >0.
    a = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0] * 5
    b = [min(1.0, x + 0.1) for x in a]
    diff = _stats.bootstrap_diff_ci({"a": a, "b": b}, reference="a", iters=2000)
    assert "a" not in diff
    assert diff["b"].point > 0
    assert diff["b"].lo > 0  # consistently better → CI excludes 0


def test_wilcoxon_all_zero_diffs_is_p1():
    x = [0.5, 0.5, 0.5]
    w, p = _stats.wilcoxon_signed_rank(x, x)
    assert w == 0.0 and p == 1.0


def test_wilcoxon_detects_consistent_shift():
    # b strictly greater than a on every pair → small p-value.
    a = [0.0] * 20
    b = [1.0] * 20
    _, p = _stats.wilcoxon_signed_rank(b, a)
    assert p < 0.01


def test_holm_bonferroni_monotone_and_rejects():
    raw = {"c1": 0.001, "c2": 0.02, "c3": 0.5}
    out = _stats.holm_bonferroni(raw, alpha=0.05)
    # c1: 3*0.001=0.003 rejected; c2: 2*0.02=0.04 rejected; c3: 1*0.5=0.5 not.
    assert out["c1"][1] is True
    assert out["c2"][1] is True
    assert out["c3"][1] is False
    # Adjusted p-values are non-decreasing in raw order.
    assert out["c1"][0] <= out["c2"][0] <= out["c3"][0]


def test_holm_stops_after_first_non_rejection():
    raw = {"a": 0.04, "b": 0.9}  # m=2: a->0.08 (>0.05) not rejected, b not rejected
    out = _stats.holm_bonferroni(raw, alpha=0.05)
    assert out["a"][1] is False
    assert out["b"][1] is False


def test_ndcg_multi_relevant_graded():
    # Two relevant docs, grades 2 and 1; perfect order gives ndcg 1.0.
    qrels = {"d1": 2, "d2": 1}
    perfect = _stats.ndcg_at_k(["d1", "d2", "x", "y"], qrels, k=10)
    assert math.isclose(perfect, 1.0, rel_tol=1e-9)
    worse = _stats.ndcg_at_k(["x", "y", "d2", "d1"], qrels, k=10)
    assert worse < perfect


def test_recall_and_ap_multi_relevant():
    qrels = {"d1": 1, "d2": 1}
    assert _stats.recall_at_k(["d1", "x", "d2"], qrels, k=10) == 1.0
    assert _stats.recall_at_k(["d1", "x", "y"], qrels, k=10) == 0.5
    # AP: relevant at ranks 1 and 3 → (1/1 + 2/3)/2.
    ap = _stats.average_precision(["d1", "x", "d2", "y"], qrels)
    assert math.isclose(ap, (1.0 + 2.0 / 3.0) / 2.0, rel_tol=1e-9)


def test_rr_and_hit_helpers():
    assert _stats.reciprocal_rank(1) == 1.0
    assert _stats.reciprocal_rank(None) == 0.0
    assert _stats.reciprocal_rank(20, cap=10) == 0.0
    assert _stats.hit_at_k(3, 5) == 1.0
    assert _stats.hit_at_k(7, 5) == 0.0


def test_build_stats_table_smoke():
    keys = ["ref", "better", "same"]
    n = 40
    rr = {
        "ref": [0.3] * n,
        "better": [0.9] * n,
        "same": [0.3] * n,
    }
    metrics = {"mrr": rr, "recall@10": {k: [1.0] * n for k in keys}}
    table, interp = _stats.build_stats_table(
        keys, "ref", metrics, "mrr", rr, iters=1000
    )
    assert "config" in table and "distinguishable" in table
    assert "Interpretation" in interp
    # 'better' has a huge consistent RR shift → should be flagged distinguishable.
    assert "better" in interp or "differ" in interp
