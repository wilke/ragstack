"""Hand-checkable tests for the round-2 found@10 analysis (tiny tables, exact p)."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import found10_analysis as fa  # noqa: E402

AN = {**fa.DEFAULT_ANALYSIS, "n_perm": 2000, "n_boot": 2000}


def recs(spec: list[tuple[str, str, str]]) -> list[dict[str, Any]]:
    """[(record_id, corpus, paper)] -> records in stratum S1."""
    return [{"record_id": r, "corpus": c, "paper": p, "stratum": "S1"} for r, c, p in spec]


def table(rows: dict[str, dict[str, int | None]], cond: str = "base") -> dict[str, dict[str, Any]]:
    return {rid: {arm: {"found": {cond: v}, "proxy": None} for arm, v in arms.items()} for rid, arms in rows.items()}


# ------------------------------------------------------------- small pure pieces
def test_min_attainable_p() -> None:
    assert fa.min_attainable_p(0, 10_000) == 1.0
    assert fa.min_attainable_p(3, 10_000) == pytest.approx(1 / 8)
    # 4 papers: 1/16 = .0625 > .05 -> a contrast on 4 non-zero papers can never reject
    assert fa.min_attainable_p(4, 10_000) > 0.05
    # above the exact limit the MC floor 1/(1+n) binds
    assert fa.min_attainable_p(25, 10_000) == pytest.approx(1 / 10_001)


def test_mde_split() -> None:
    # D = 5: P(G >= 5) = 1/32 = .031 <= .05; P(G >= 4) = 6/32 = .19 -> needs 5/0
    assert fa.mde_split(5, 0.05) == [5, 0]
    # D = 3: even 3/0 gives 1/8 > .05
    assert fa.mde_split(3, 0.05) is None
    # D = 10 at .05: P(G >= 9) = 11/1024 = .0107, P(G >= 8) = 56/1024 = .0547 -> 9/1
    assert fa.mde_split(10, 0.05) == [9, 1]


def test_paired_missing_rules() -> None:
    r = recs([("q1", "O", "p1"), ("q2", "O", "p1"), ("q3", "O", "p2")])
    out = {"q1": {"a": 1, "b": 0}, "q2": {"a": None, "b": 1}, "q3": {"a": 0, "b": 0}}
    pages, a, b, cnt = fa.paired(r, out, "a", "b")
    assert (pages, a, b, cnt) == (["O/p1", "O/p2"], [1, 0], [0, 0], {"excluded_missing": 1})
    pages, a, b, cnt = fa.paired(r, out, "a", "b", missing="as_not_found")
    assert (a, b, cnt) == ([1, 0, 0], [0, 1, 0], {"scored_not_found": 1})


# ------------------------------------------------------------- contrasts
def test_three_papers_all_gained_is_gated() -> None:
    # paper sums +1, +1, +1 -> exact p = 1/8; min attainable p = 1/8 > .05 -> GATE
    pages, a, b = ["p1", "p2", "p3"], [1, 1, 1], [0, 0, 0]
    r = fa.contrast_result(pages, a, b, "superiority", AN)
    assert r["p"] == pytest.approx(1 / 8)
    assert (r["gained"], r["lost"], r["net_share"]) == (3, 0, 1.0)
    assert r["gate"] == fa.GATE


def test_six_papers_all_gained() -> None:
    # paper sums +1 x 6 -> exact p = 1/64 = .0156, not gated
    pages = [f"p{i}" for i in range(6)]
    r = fa.contrast_result(pages, [1] * 6, [0] * 6, "superiority", AN)
    assert r["p"] == pytest.approx(1 / 64)
    assert r["min_attainable_p"] == pytest.approx(1 / 64)
    assert "gate" not in r


def test_clustering_counts_papers_not_records() -> None:
    # 6 gained records but all on ONE paper: one non-zero paper sum -> p = 1/2, gated
    r = fa.contrast_result(["p1"] * 6, [1] * 6, [0] * 6, "superiority", AN)
    assert r["p"] == pytest.approx(0.5)
    assert r["n_pages_nonzero"] == 1
    assert r["gate"] == fa.GATE


def test_noninferiority_shift() -> None:
    # 6 papers x 2 records, all ties (d = 0). Shift +0.05 per record -> each paper
    # sum 0.1, T = 0.6; only the all-plus flip reaches it -> exact p = 1/64.
    pages = [f"p{i}" for i in range(6) for _ in range(2)]
    r = fa.contrast_result(pages, [1, 0] * 6, [1, 0] * 6, "noninferiority", AN)
    assert r["type"] == "noninferiority"
    assert r["p"] == pytest.approx(1 / 64)
    assert r["net_share"] == 0.0
    # without a shift the same table is all-zero: superiority p = 1 and gated
    s = fa.contrast_result(pages, [1, 0] * 6, [1, 0] * 6, "superiority", AN)
    assert s["p"] == 1.0 and s["gate"] == fa.GATE


def test_noninferiority_large_loss_not_shown() -> None:
    # 6 papers, each 1 record lost of 2: d mean = -0.5 << -0.05; shifted sums -0.9 each
    pages = [f"p{i}" for i in range(6) for _ in range(2)]
    r = fa.contrast_result(pages, [0, 0] * 6, [1, 0] * 6, "noninferiority", AN)
    assert r["p"] == pytest.approx(1.0)


def test_holm_family_hand_checked() -> None:
    # p: .01, .04 (NI), .03, missing -> sorted .01,.03,.04,1 ; adjusted
    # .04, max(.04, 3*.03=.09)=.09, max(.09, 2*.04=.08)=.09, 1
    tests = {"H1": {"p": 0.01, "p_mid": 0.005, "type": "superiority", "discordant": 10},
             "H2": {"p": 0.04, "p_mid": 0.03, "type": "noninferiority"},
             "H3": {"p": 0.03, "p_mid": 0.02, "type": "superiority", "discordant": 10},
             "H4": {"p": None, "p_mid": None, "type": "superiority", "gate": fa.GATE}}
    out = fa.holm_family(tests, ["H1", "H2", "H3", "H4"], 0.05)
    assert out["H1"]["p_holm"] == pytest.approx(0.04)
    assert out["H3"]["p_holm"] == pytest.approx(0.09)
    assert out["H2"]["p_holm"] == pytest.approx(0.09)
    assert out["H4"]["p_holm"] == pytest.approx(1.0)
    assert out["H1"]["status"] == "confirmed"
    assert out["H2"]["status"] == "not shown non-inferior"
    assert out["H3"]["status"] == "not confirmed"
    assert out["H4"]["status"] == fa.GATE
    # the MDE is quoted beside a superiority null: D = 10 at .05/4 = .0125 -> 9/1 (p .0107)
    assert out["H3"]["mde_split_holm_worst"] == [9, 1]
    assert "mde_split_holm_worst" not in out["H2"]


# ------------------------------------------------------------- the whole block
def _study() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """O: 4 papers where qwen36p gains over rules; A: 4 papers where it loses."""
    spec, rows = [], {}
    for c, (qa, ra) in (("O", (1, 0)), ("A", (0, 1))):
        for p in range(4):
            for k in range(2):
                rid = f"{c}{p}{k}"
                spec.append((rid, c, f"p{p}"))
                rows[rid] = {"rules": ra if k == 0 else 0, "qwen36p": qa if k == 0 else 0,
                             "qwen36p-shuf": 0, "frontierp": qa if k == 0 else 0, "rules#2": ra if k == 0 else 0}
    per = table(rows)
    for rid in per:
        for arm in per[rid]:
            per[rid][arm]["found"]["bm25"] = per[rid][arm]["found"]["base"]
            per[rid][arm]["found"]["norerank"] = per[rid][arm]["found"]["base"]
            per[rid][arm]["proxy"] = 1 if arm == "qwen36p" else 0
    return recs(spec), per


def test_analyse_smoke_and_corpus_dependence() -> None:
    records, per = _study()
    res = fa.analyse(records, per, {"n_perm": 500, "n_boot": 500}, power=False)
    fam = res["family"]["hypotheses"]
    # pooled H1: +4 on O, -4 on A -> net 0
    assert fam["H1"]["gained"] == 4 and fam["H1"]["lost"] == 4
    assert res["secondary"]["per_corpus"]["_corpus_dependent"]["H1"] is True
    # H4 on the proxy: qwen36p passes everywhere, rules nowhere: +16 over 8 papers
    assert fam["H4"]["gained"] == 16 and fam["H4"]["p"] == pytest.approx(1 / 256)
    assert fam["H4"]["status"] == "confirmed"
    # noise floor: rules#2 equals rules -> 0 discordant
    nf = res["descriptives"]["noise_floor"]["O"]["base"]
    assert nf["discordant"] == 0 and nf["n"] == 8
    assert res["descriptives"]["found_at_10"]["O"]["base"]["qwen36p"]["found"] == 4


def test_gated_arm_is_not_tested() -> None:
    records, per = _study()
    res = fa.analyse(records, per, {"n_perm": 200, "n_boot": 200},
                     gated_arms={"frontierp|base": "depth short"}, power=False)
    h2 = res["family"]["hypotheses"]["H2"]
    assert h2["gate"] == fa.GATE and h2["status"] == fa.GATE and h2["p_holm"] == 1.0


def test_unmeasured_arm_is_gated_not_a_keyerror() -> None:
    records, per = _study()
    for rid in per:
        if rid.startswith("A"):
            del per[rid]["frontierp"]
    res = fa.analyse(records, per, {"n_perm": 200, "n_boot": 200}, power=False)
    assert res["family"]["hypotheses"]["H2"]["status"] == fa.GATE


def test_power_table_deterministic() -> None:
    a = fa.power_table([10] * 8, AN, n_sims=40, n_flips=40)
    b = fa.power_table([10] * 8, AN, n_sims=40, n_flips=40)
    assert a == b
    kinds = {(r["test"], r["discordance"]) for r in a["rows"]}
    assert ("superiority", 0.17) in kinds and ("noninferiority", 0.05) in kinds
