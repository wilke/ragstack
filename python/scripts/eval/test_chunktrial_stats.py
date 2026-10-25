"""Hand-checkable tests for chunktrial_stats (page-clustered confirmatory statistics)."""
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _stats  # noqa: E402
import chunktrial_stats as cs  # noqa: E402


# ------------------------------------------------------------- sign-flip, exact
def test_exact_three_positive_pages() -> None:
    # Sums +1,+1,+1: T = 3; of the 2^3 = 8 sign vectors only (+,+,+) reaches 3.
    assert cs.sign_flip_p_exact([1, 1, 1]) == pytest.approx(1 / 8)
    # Two-sided: |T*| >= 3 for (+,+,+) and (-,-,-) -> 2/8.
    assert cs.sign_flip_p_exact([1, 1, 1], "two-sided") == pytest.approx(2 / 8)
    # Zero pages are inert under every flip.
    assert cs.sign_flip_p_exact([1, 0, 1, 0, 1]) == pytest.approx(1 / 8)


def test_exact_mixed_signs() -> None:
    # Sums 2, 1, -1: T = 2. The 8 values of ±2 ±1 ±(-1) are
    # 2, 4, 0, 2, -2, 0, -4, -2 -> #{>= 2} = 3, p = 3/8; "less": #{<= 2} = 7.
    assert cs.sign_flip_p_exact([2, 1, -1]) == pytest.approx(3 / 8)
    assert cs.sign_flip_p_exact([2, 1, -1], "less") == pytest.approx(7 / 8)
    assert cs.sign_flip_p_exact([0, 0]) == 1.0


def test_page_sums_from_questions() -> None:
    pages = ["p1", "p1", "p2", "p3", "p3", "p3"]
    d = cs.paired_differences([1, 1, 0, 1, 0, 0], [0, 1, 1, 0, 0, 1])
    assert d.tolist() == [1, 0, -1, 1, 0, -1]
    sums, counts = cs.page_sums(pages, d)
    assert sums.tolist() == [1, -1, 0]
    assert counts.tolist() == [2, 1, 3]


def test_mc_converges_to_exact() -> None:
    sums = [3, 2, 2, 1, 1, 1, -1, -1, 2, -2, 1, 1]
    exact = cs.sign_flip_p_exact(sums)
    mc = cs.sign_flip_p_mc(sums, n_flips=200_000, seed=1)
    # MC SE at p ~ 0.07 with 2e5 flips is ~ 6e-4.
    assert abs(mc - exact) < 0.003
    res = cs.sign_flip_test(sums, n_flips=10_000, seed=1)
    assert res["p_exact"] == pytest.approx(exact)
    assert res["p"] == res["p_exact"]
    assert res["n_pages_nonzero"] == 12


def test_mc_p_floor_and_no_exact_above_20() -> None:
    sums = [1] * 25
    res = cs.sign_flip_test(sums, n_flips=999, seed=0)
    assert res["p_exact"] is None
    assert res["p"] == res["p_perm"]
    # 2^-25 is far below 1/1000, so with 999 flips p = (1+0)/(1+999).
    assert res["p_perm"] == pytest.approx(1 / 1000)


# ------------------------------------------------------------------- McNemar
@pytest.mark.parametrize("g,l", [(0, 0), (5, 0), (3, 1), (10, 4), (7, 7), (30, 18), (2, 9)])
def test_mcnemar_matches_section_aware(g: int, l: int) -> None:  # noqa: E741
    import section_aware_trial as sat

    assert cs.mcnemar_exact(g, l) == pytest.approx(sat.mcnemar_p(g, l), abs=1e-15)


def test_mcnemar_one_sided_by_hand() -> None:
    # (5, 0): P(G >= 5 | D = 5) = 1/32; two-sided doubles it.
    assert cs.mcnemar_exact(5, 0, "greater") == pytest.approx(1 / 32)
    assert cs.mcnemar_exact(5, 0) == pytest.approx(2 / 32)
    # (3, 1): P(G >= 3 | D = 4) = (C(4,3) + C(4,4)) / 16 = 5/16.
    assert cs.mcnemar_exact(3, 1, "greater") == pytest.approx(5 / 16)
    assert cs.mcnemar_exact(3, 1, "less") == pytest.approx(15 / 16)


# ---------------------------------------------------------------------- Holm
def test_holm_textbook() -> None:
    # m = 4, alpha = .05. Sorted: .005*4 = .02 (rej), .01*3 = .03 (rej),
    # .03*2 = .06 > .05 (stop) -> .04 retained. Adjusted (monotone): .02 .03 .06 .06.
    res = cs.holm([0.01, 0.04, 0.03, 0.005], alpha=0.05)
    assert res["reject"] == [True, False, False, True]
    assert res["p_adj"] == pytest.approx([0.03, 0.06, 0.06, 0.02])


def test_holm_matches_stats_holm() -> None:
    rng = np.random.default_rng(3)
    for _ in range(50):
        ps = rng.uniform(0, 0.1, size=5).tolist()
        ref = _stats.holm_bonferroni({str(i): p for i, p in enumerate(ps)}, alpha=0.05)
        res = cs.holm(ps, alpha=0.05)
        assert res["reject"] == [ref[str(i)][1] for i in range(5)]
        # Direct step-down rule, independently.
        order = np.argsort(ps)
        expect = [False] * 5
        for rank, i in enumerate(order):
            if ps[i] <= 0.05 / (5 - rank):
                expect[i] = True
            else:
                break
        assert res["reject"] == expect


# ------------------------------------------------------------------- kappa
def test_kappa_textbook_2x2() -> None:
    # Classic table (rows rater 1, cols rater 2): yes/yes 20, yes/no 5,
    # no/yes 10, no/no 15; n = 50. Po = 35/50 = .70. Rater 1 yes = 25/50 = .5,
    # rater 2 yes = 30/50 = .6 -> Pe = .5*.6 + .5*.4 = .50. kappa = .2/.5 = 0.40.
    res = cs.kappa_from_table([[20, 5], [10, 15]])
    assert res["po"] == pytest.approx(0.70)
    assert res["pe"] == pytest.approx(0.50)
    assert res["kappa"] == pytest.approx(0.40)


def test_kappa_labels_ci_and_multiclass() -> None:
    r1 = [1] * 20 + [1] * 5 + [0] * 10 + [0] * 15
    r2 = [1] * 20 + [0] * 5 + [1] * 10 + [0] * 15
    res = cs.cohen_kappa_ci(r1, r2, n_boot=2000, seed=0)
    assert res["kappa"] == pytest.approx(0.40)
    lo, hi = res["ci"]
    assert lo < 0.40 < hi
    assert res == cs.cohen_kappa_ci(r1, r2, n_boot=2000, seed=0)  # seeded
    # Multi-class point estimate agrees with _stats.cohen_kappa.
    rng = np.random.default_rng(7)
    a = rng.integers(0, 3, size=120).tolist()
    b = [x if rng.random() < 0.6 else int(rng.integers(0, 3)) for x in a]
    got = cs.cohen_kappa_ci(a, b, categories=[0, 1, 2], n_boot=500)["kappa"]
    assert got == pytest.approx(_stats.cohen_kappa(a, b, [0, 1, 2]))
    # Perfect agreement -> 1.
    assert cs.kappa_from_table([[3, 0, 0], [0, 4, 0], [0, 0, 5]])["kappa"] == pytest.approx(1.0)


# ----------------------------------------------------------- Clopper-Pearson
def test_clopper_pearson_known_values() -> None:
    # 0/10: lower 0, upper = 1 - 0.025^(1/10) = 0.30850.
    lo, hi = cs.clopper_pearson(0, 10)
    assert lo == 0.0
    assert hi == pytest.approx(1 - 0.025 ** 0.1, abs=1e-9)
    assert hi == pytest.approx(0.30850, abs=1e-5)
    # 5/10: [0.18709, 0.81291] (standard tables).
    lo, hi = cs.clopper_pearson(5, 10)
    assert lo == pytest.approx(0.187086, abs=1e-5)
    assert hi == pytest.approx(0.812914, abs=1e-5)
    # 10/10 mirrors 0/10.
    lo, hi = cs.clopper_pearson(10, 10)
    assert lo == pytest.approx(0.025 ** 0.1, abs=1e-9)
    assert hi == 1.0
    res = cs.proportion_ci(7, 20)
    assert res["p"] == pytest.approx(0.35)
    lo, hi = res["ci"]
    # Defining equations of the bounds.
    assert 1 - cs._binom_cdf(6, 20, lo) == pytest.approx(0.025, abs=1e-9)
    assert cs._binom_cdf(7, 20, hi) == pytest.approx(0.025, abs=1e-9)


# ----------------------------------------------------------- non-inferiority
def test_noninferiority_shift_constructed() -> None:
    # Three pages x 4 questions, every d = 0 (net share 0). With delta = .25 the
    # shifted page sums are 4 * .25 = 1, 1, 1 -> exact one-sided p = 1/8.
    pages = [p for p in ("a", "b", "c") for _ in range(4)]
    a = [1, 0, 1, 0] * 3
    res = cs.noninferiority_test(pages, a, a, delta=0.25, n_boot=500, n_flips=2000)
    assert res["p_exact"] == pytest.approx(1 / 8)
    assert res["net_share"] == 0.0
    # Every page has net 0, so every bootstrap share is 0: CI [0, 0] > -.25.
    assert res["ci"] == [0.0, 0.0]
    assert res["reject_ci"] is True
    assert res["reject_perm"] is False  # 1/8 > .05

    # At the boundary: one loss per page (net share -.25) -> shifted sums all 0.
    b = [1, 1, 1, 0] * 3
    a2 = [0, 1, 1, 0] * 3
    res = cs.noninferiority_test(pages, a2, b, delta=0.25, n_boot=500, n_flips=2000)
    assert res["net_share"] == pytest.approx(-0.25)
    assert res["n_pages_nonzero"] == 0
    assert res["p"] == 1.0
    assert res["reject_ci"] is False


def test_noninferiority_many_pages_rejects() -> None:
    # 30 pages x 4 questions with net 0 on each: shifted sums all +0.3, so the
    # sign-flip p is (1 + #{all-plus}) / (1 + n) -> ~1/(1+n).
    pages = [i for i in range(30) for _ in range(4)]
    a = [1, 0, 1, 1] * 30
    b = [0, 1, 1, 1] * 30
    res = cs.noninferiority_test(pages, a, b, delta=0.075, n_flips=4000, n_boot=1000)
    assert res["p_perm"] == pytest.approx(1 / 4001)
    assert res["reject_perm"] and res["reject_ci"]


# ----------------------------------------------------------------- bootstrap
def test_bootstrap_deterministic_and_contrast_dict() -> None:
    rng = np.random.default_rng(11)
    pages = [i for i in range(40) for _ in range(4)]
    a = (rng.random(160) < 0.6).astype(int).tolist()
    b = (rng.random(160) < 0.5).astype(int).tolist()
    r1 = cs.page_bootstrap_ci(pages, a, b, n_boot=3000, seed=5)
    r2 = cs.page_bootstrap_ci(pages, a, b, n_boot=3000, seed=5)
    r3 = cs.page_bootstrap_ci(pages, a, b, n_boot=3000, seed=6)
    assert r1 == r2
    assert r1["ci"] != r3["ci"]
    assert r1["ci"][0] <= r1["point"] <= r1["ci"][1]
    c = cs.contrast(pages, a, b, n_flips=2000, n_boot=2000)
    d = np.asarray(a) - np.asarray(b)
    assert c["gained"] == int((d == 1).sum()) and c["lost"] == int((d == -1).sum())
    assert c["net_share"] == pytest.approx(d.mean())
    assert c["n_pages"] == 40 and c["n"] == 160
    json.dumps(c)  # plain JSON types throughout


# --------------------------------------------------------------------- power
def test_power_large_effect_is_one() -> None:
    r = cs.simulate_power(76, 4, discordance=0.4, discordant_share=0.8,
                          alpha=0.05, n_family=4, n_sims=300, n_flips=1000)
    assert r["power"] > 0.99


@pytest.mark.parametrize("alternative", ["greater", "two-sided"])
def test_power_null_is_alpha(alternative: str) -> None:
    n_sims = 4000
    r = cs.simulate_power(76, 4, discordance=0.25, discordant_share=0.0, alpha=0.05,
                          alternative=alternative, n_sims=n_sims, n_flips=1000, seed=2)
    se = math.sqrt(0.05 * 0.95 / n_sims)
    # Valid (size <= alpha) but somewhat conservative: page sums live on an integer
    # lattice and T* = T mod 2, so the reference distribution has atoms and
    # P(p <= .05) lands near .04 at 4 questions/page (jittering the sums by 1e-3
    # restores ~.05). Upper bound = validity; lower bound = not degenerate.
    assert r["power"] < 0.05 + 3.5 * se
    assert r["power"] > 0.03


def test_power_null_with_page_correlation_is_alpha() -> None:
    r = cs.simulate_power(76, 4, discordance=0.25, discordant_share=0.0, icc=0.2,
                          share_icc=0.2, alpha=0.05, n_sims=4000, n_flips=1000, seed=4)
    assert 0.03 < r["power"] < 0.05 + 3.5 * math.sqrt(0.05 * 0.95 / 4000)


def test_power_null_less_discrete_is_near_alpha() -> None:
    # 40 questions/page, discordance .5: the lattice is fine relative to the SD of
    # T, so the size approaches alpha (measured .046 at 8000 sims).
    n_sims = 4000
    r = cs.simulate_power(76, 40, discordance=0.5, discordant_share=0.0, alpha=0.05,
                          n_sims=n_sims, n_flips=1000, seed=2)
    assert abs(r["power"] - 0.05) < 3.5 * math.sqrt(0.05 * 0.95 / n_sims)


def test_one_sided_beats_two_sided_and_joint() -> None:
    kw = {"n_pages": 76, "questions_per_page": cs.even_split(315, 76), "discordance": 0.25,
          "discordant_share": 0.3, "alpha": 0.05, "n_sims": 1500, "n_flips": 1000, "seed": 9}
    one = cs.simulate_power(alternative="greater", **kw)
    two = cs.simulate_power(alternative="two-sided", **kw)
    assert one["power"] > two["power"]
    assert one["mean_questions"] == 315
    assert cs.joint_power(one["power"], one["power"]) == pytest.approx(one["power"] ** 2)


def test_power_deterministic_and_ni_ci() -> None:
    kw = {"n_pages": 40, "questions_per_page": [3, 4, 5], "discordance": 0.25,
          "discordant_share": 0.0, "delta": 0.075, "n_sims": 200, "n_flips": 500,
          "with_ci": True, "n_boot": 300, "seed": 1}
    a, b = cs.simulate_power(**kw), cs.simulate_power(**kw)
    assert a == b
    assert 0.0 <= a["power_ci"] <= 1.0


def test_even_split() -> None:
    s = cs.even_split(315, 76)
    assert sum(s) == 315 and len(s) == 76 and set(s) == {4, 5} and s.count(5) == 11


# --------------------------------------------------------------------------- #
# mid-p: a reported sensitivity, never a decision
# --------------------------------------------------------------------------- #
def test_mid_p_three_positive_pages() -> None:
    p, pm = cs.sign_flip_exact_with_mid([1, 1, 1])
    assert p == 1 / 8 and pm == 1 / 16          # only +++ reaches T=3 (a tie): 0.5/8


def test_mid_p_mixed_signs_enumerated() -> None:
    # sums [2, 1, -1], T = 2. Patterns (e1,e2,e3) -> 2e1 + e2 - e3:
    #   +++ 2 | ++- 4 | +-+ 0 | +-- 2 | -++ -2 | -+- 0 | --+ -4 | --- -2
    # T* >= 2: {2, 4, 2} = 3 -> p = 3/8.  T* > 2: {4} = 1; T* = 2: 2 ties.
    # p_mid = (1 + 0.5 * 2) / 8 = 2/8.
    p, pm = cs.sign_flip_exact_with_mid([2, 1, -1])
    assert p == 3 / 8 and pm == 2 / 8
    # "less": T* <= 2 is 7 patterns (all but 4); T* < 2: {0,-2,0,-4,-2} = 5; ties 2 -> (5 + 1)/8
    p_l, pm_l = cs.sign_flip_exact_with_mid([2, 1, -1], "less")
    assert p_l == 7 / 8 and pm_l == 6 / 8
    # two-sided: |T*| >= 2: {2,4,2,-2,-4,-2} = 6; |T*| > 2: 2; ties 4 -> (2 + 2)/8
    p_t, pm_t = cs.sign_flip_exact_with_mid([2, 1, -1], "two-sided")
    assert p_t == 6 / 8 and pm_t == 4 / 8


def test_mid_p_all_zero() -> None:
    assert cs.sign_flip_exact_with_mid([0, 0, 0]) == (1.0, 0.5)
    assert cs.sign_flip_mc_with_mid([0, 0], n_flips=50) == (1.0, 0.5)
    r = cs.sign_flip_test([0.0, 0.0, 0.0])
    assert r["p"] == 1.0 and r["p_mid"] == 0.5


def test_mid_p_mc_converges_to_exact_and_has_no_plus_one() -> None:
    s = [2, 1, -1, 3, 1, 1, 2, -1]
    exact = cs.sign_flip_exact_with_mid(s)
    p_mc, pm_mc = cs.sign_flip_mc_with_mid(s, n_flips=200_000, seed=7)
    assert abs(pm_mc - exact[1]) < 0.01 and abs(p_mc - exact[0]) < 0.01
    # no +1: with few flips mid-p can be exactly 0 where the standard p is >= 1/(1+n)
    _, pm0 = cs.sign_flip_mc_with_mid([5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5], n_flips=10, seed=1)
    assert pm0 == 0.0


def test_mid_p_passes_through_test_contrast_noninferiority() -> None:
    r = cs.sign_flip_test([1, 1, 1], n_flips=500)
    assert r["p"] == 1 / 8 and r["p_mid"] == 1 / 16 and r["p_mid_exact"] == 1 / 16
    pages = ["a", "b", "c"]
    c = cs.contrast(pages, [1, 1, 1], [0, 0, 0], n_flips=200, n_boot=50)
    assert c["p"] == 1 / 8 and c["p_mid"] == 1 / 16
    ni = cs.noninferiority_test(pages, [1, 1, 1], [1, 1, 1], 0.075, n_flips=200, n_boot=50)
    shifted = cs.sign_flip_test(cs.page_sums(pages, np.zeros(3), shift=0.075)[0], n_flips=200)
    assert ni["p_mid"] == shifted["p_mid"] == 1 / 16 and ni["p"] == 1 / 8


def test_standard_p_unchanged_vs_frozen_reference() -> None:
    """The standard p is bit-identical to a frozen copy of the pre-mid-p algorithm."""
    tol = cs._TOL

    def old_tail(stats: np.ndarray, obs: float, alt: str) -> int:
        if alt == "greater":
            return int(np.count_nonzero(stats >= obs - tol))
        if alt == "less":
            return int(np.count_nonzero(stats <= obs + tol))
        return int(np.count_nonzero(np.abs(stats) >= abs(obs) - tol))

    def old_exact(sums: Any, alt: str) -> float:
        s = np.asarray(sums, dtype=float)
        s = s[np.abs(s) > tol]
        stats = np.zeros(1)
        for v in s:
            stats = np.concatenate((stats + v, stats - v))
        return old_tail(stats, float(s.sum()), alt) / stats.size

    def old_mc(sums: Any, n: int, seed: int, alt: str) -> float:
        s = np.asarray(sums, dtype=float)
        s = s[np.abs(s) > tol]
        if s.size == 0:
            return 1.0
        rng = np.random.default_rng(seed)
        count, done, chunk = 0, 0, max(1, 2_000_000 // s.size)
        while done < n:
            m = min(chunk, n - done)
            signs = rng.integers(0, 2, size=(m, s.size), dtype=np.int8) * 2 - 1
            count += old_tail(signs @ s, float(s.sum()), alt)
            done += m
        return (1 + count) / (1 + n)

    rng = np.random.default_rng(123)
    for _ in range(60):
        k = int(rng.integers(0, 13))
        sums = rng.integers(-4, 5, size=k) * rng.choice([1.0, 0.5, 0.075], size=k)
        for alt in ("greater", "less", "two-sided"):
            assert cs.sign_flip_p_exact(sums, alt) == old_exact(sums, alt)
            assert cs.sign_flip_p_mc(sums, n_flips=300, seed=5, alternative=alt) == old_mc(sums, 300, 5, alt)
            t = cs.sign_flip_test(sums, n_flips=300, seed=5, alternative=alt)
            assert t["p"] == old_exact(sums, alt) and t["p_perm"] == old_mc(sums, 300, 5, alt)
            assert 0.0 <= t["p_mid"] <= t["p"] + 1e-12
