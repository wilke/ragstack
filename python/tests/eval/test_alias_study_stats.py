"""Alias study statistics (prereg DRAFT v3): the two families (G: H1 then H1b in fixed sequence; N:
H2 with the gold-only direction gate), the C gate (rule C), the reach and cluster gates, the
Layer-2 rules (harm, the C cost rule of 2026-11-03, reranker non-inferiority, growth) and the
serving statements, the judge false-yes bound (U = max), the fixed sensitivities, *fragile*,
the outcomes and the public check. Each test breaks the rule it guards: the asserted outcome
flips if the rule is removed (checked by mutation, see the PR)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE.parents[1] / "scripts" / "eval", _HERE.parents[1] / "scripts" / "eval" / "glossbench"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import alias_study_common as asc  # noqa: E402
import alias_study_fixture as fx  # noqa: E402
import alias_study_stats as st  # noqa: E402

AN = {"n_perm": 2000, "n_boot": 500, "perm_seed": 1, "boot_seed": 2}


def m(p: float | None, min_p: float = 1e-6, gates: list[str] | None = None, gold: bool = True) -> dict[str, Any]:
    return {"p": p, "min_attainable_p": min_p, "gates": gates or [], "gold_gate_pass": gold}


# ------------------------------------------------------------------ Family G and Family N (§3.1, R1)

def test_family_g_h1_rejected_then_h1b_tested() -> None:
    r = st.family_g(m(0.03), m(0.04))
    assert r["H1"]["status"] == st.CONFIRMED and r["H1"]["level"] == 0.05 and r["H1"]["family"] == "G"
    assert r["H1b"]["tested"] is True and r["H1b"]["status"] == st.CONFIRMED and r["H1b"]["level"] == 0.05
    r2 = st.family_g(m(0.03), m(0.2))
    assert r2["H1b"]["tested"] is True and r2["H1b"]["status"] == st.NOT_CONFIRMED
    assert r2["H1b"]["p_in_procedure"] == 0.2


def test_family_g_h1_not_rejected_h1b_not_tested() -> None:
    """H1 just above 0.05: H1b is not tested however small its p (fixed sequence)."""
    r = st.family_g(m(0.051), m(1e-9))
    assert r["H1"]["status"] == st.NOT_CONFIRMED
    assert r["H1b"]["status"] == st.NOT_TESTED and r["H1b"]["tested"] is False
    assert r["H1b"]["rejected"] is False and r["H1b"]["level"] is None and r["H1b"]["p_in_procedure"] is None


def test_family_g_gated_or_absent_h1_leaves_h1b_untested() -> None:
    g = st.family_g(m(1e-4, gates=["C gate fired (rule C)"]), m(1e-9))
    assert g["H1"]["status"] == st.GATE and g["H1"]["p_in_procedure"] == 1.0
    assert g["H1b"]["status"] == st.NOT_TESTED
    a = st.family_g(m(None), m(1e-9))
    assert a["H1"]["status"] == "absent" and a["H1"]["p_in_procedure"] == 1.0
    assert a["H1b"]["status"] == st.NOT_TESTED


def test_each_family_at_005_and_no_level_passes() -> None:
    """Both at p 0.03: v1's Holm pair (0.025 each) confirmed neither; each family at 0.05
    confirms both. H1 at 0.06 stays not confirmed whatever H2 does (no level passes)."""
    g, n = st.family_g(m(0.03), m(0.5)), st.family_n(m(0.03))
    assert g["H1"]["status"] == n["H2"]["status"] == st.CONFIRMED
    assert g["H1"]["level"] == n["H2"]["level"] == 0.05
    assert st.family_g(m(0.06), m(0.001))["H1"]["status"] == st.NOT_CONFIRMED
    assert n["H2"]["family"] == "N"


def test_h2_gold_only_direction_gate() -> None:
    bad = st.family_n(m(0.001, gold=False))["H2"]
    assert bad["status"] == st.NOT_CONFIRMED_GOLD and bad["rejected"] is False
    assert st.family_n(m(0.001, gold=True))["H2"]["status"] == st.CONFIRMED
    assert st.family_n(m(0.2, gold=False))["H2"]["status"] == st.NOT_CONFIRMED


def test_min_attainable_p_is_judged_at_005() -> None:
    """k = 5 non-zero papers: min p 1/32 <= 0.05 (evaluable); k = 4: 1/16 > 0.05 (GATE)."""
    assert st.family_g(m(1 / 32, min_p=1 / 32), m(0.5))["H1"]["status"] == st.CONFIRMED
    g = st.family_g(m(1 / 16, min_p=1 / 16), m(0.001))
    assert g["H1"]["status"] == st.GATE and "minimum attainable p" in g["H1"]["gate_reasons"][0]
    assert g["H1b"]["status"] == st.NOT_TESTED
    assert st.family_n(m(1 / 16, min_p=1 / 16))["H2"]["status"] == st.GATE


def test_h1b_gate_at_005() -> None:
    r = st.family_g(m(0.001), m(0.04, min_p=0.0625))
    assert r["H1b"]["tested"] and r["H1b"]["status"] == st.GATE and r["H1b"]["p_in_procedure"] == 1.0


# ------------------------------------------------------------------ synthetic outcomes

def recs_balanced(n_papers: int = 12) -> list[dict[str, Any]]:
    return asc.split_records(fx.make_records(n_papers))[0]


def outcomes(recs: list[dict[str, Any]], cell: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """``cell[leg](rec) -> (hit, hit_gold)``; every other leg all hits."""
    out: dict[str, dict[str, Any]] = {}
    for c, g, r, rep in asc.all_legs():
        leg = asc.leg_id(c, g, r, rep)
        fn = cell.get(leg, lambda rec: (1, 1))
        out[leg] = {}
        for rec in recs:
            h, hg = fn(rec)
            out[leg][rec["record_id"]] = {"hit": h, "hit_gold": hg, "rr": float(h), "rr_gold": float(hg),
                                          "kind": "gold" if hg else ("other" if h else None)}
    return out


def idx(rec: dict[str, Any]) -> int:
    return int(rec["paper"][1:])


def full_reach(recs: list[dict[str, Any]]) -> dict[str, int]:
    n_g = sum(r["kind"] == "G" for r in recs)
    return {"count": n_g, "of": n_g}


def test_c_gate_fires_on_c_rise_keeping_pace() -> None:
    recs = recs_balanced(30)
    none = lambda r: (0, 0)  # noqa: E731
    merged = lambda r: (1, 1) if (r["kind"] == "G" and idx(r) < 20) or (r["kind"] == "C" and idx(r) < 20) else (0, 0)  # noqa: E731
    o = outcomes(recs, {st.L("base", "none", "default"): none, st.L("base", "merged", "default"): merged})
    cg = st.c_gate(recs, o, AN)
    assert cg["verdict"] == "fires" and cg["fires"] and all(cg["conditions"].values())
    # through Layer 1: H1 GATE-NOT-EVALUABLE, H1b not tested
    r = st.layer1(recs, o, {}, full_reach(recs), AN)
    assert r["H1"]["status"] == st.GATE and "C gate fired (rule C)" in r["H1"]["gate_reasons"]
    assert r["H1b"]["status"] == st.NOT_TESTED
    # a C rise below half the G rise does not fire
    merged2 = lambda r: (1, 1) if (r["kind"] == "G" and idx(r) < 20) or (r["kind"] == "C" and idx(r) < 4) else (0, 0)  # noqa: E731
    o2 = outcomes(recs, {st.L("base", "none", "default"): none, st.L("base", "merged", "default"): merged2})
    assert st.c_gate(recs, o2, AN)["verdict"] == "passes"


def test_c_gate_needs_the_ci_to_exclude_zero() -> None:
    """ΔC > 0 and ≥ ½ ΔG, but noisy (11 C up, 5 C down of 30): condition (c) fails, no fire."""
    recs = recs_balanced(30)
    none = lambda r: (0, 0) if r["kind"] == "G" else ((1, 1) if 11 <= idx(r) < 16 else (0, 0))  # noqa: E731
    merged = lambda r: ((1, 1) if idx(r) < 10 else (0, 0)) if r["kind"] == "G" else ((1, 1) if idx(r) < 11 else (0, 0))  # noqa: E731
    o = outcomes(recs, {st.L("base", "none", "default"): none, st.L("base", "merged", "default"): merged})
    cg = st.c_gate(recs, o, AN)
    assert cg["conditions"]["a_same_sign"] and cg["conditions"]["b_half"]
    assert not cg["conditions"]["c_ci_excludes_0"] and cg["verdict"] == "passes"
    # the CI is the paper bootstrap on the boot seed (§5.5), not the permutation seed
    clc, vc, _ = st.contrast_values([r for r in recs if r["kind"] == "C"], o, st.H1_SPEC)
    _, sc, nc = st.cluster_sums(clc, vc)
    assert cg["delta_c_ci95"] == st.boot_ci(sc, nc, AN["n_boot"], AN["boot_seed"])
    assert cg["delta_c_ci95"] != st.boot_ci(sc, nc, AN["n_boot"], AN["perm_seed"])


def test_c_drop_never_fires_and_moot_when_g_does_not_rise() -> None:
    recs = recs_balanced(30)
    none = lambda r: (1, 1)  # noqa: E731
    drop = lambda r: (0, 0) if r["kind"] == "C" else (1, 1)  # noqa: E731
    o = outcomes(recs, {st.L("base", "none", "default"): none, st.L("base", "merged", "default"): drop})
    cg = st.c_gate(recs, o, AN)
    assert cg["verdict"] == "moot" and cg["fires"] is False and cg["note"] == "C gate moot, ΔG ≤ 0"
    gup_cdrop = lambda r: (1, 1) if r["kind"] == "G" else (0, 0)  # noqa: E731
    none0 = lambda r: (0, 0) if r["kind"] == "G" else (1, 1)  # noqa: E731
    o2 = outcomes(recs, {st.L("base", "none", "default"): none0, st.L("base", "merged", "default"): gup_cdrop})
    cg2 = st.c_gate(recs, o2, AN)
    assert cg2["delta_c_points"] < 0 and cg2["verdict"] == "passes" and not cg2["conditions"]["a_same_sign"]


def test_c_gate_ceiling_note() -> None:
    recs = recs_balanced(30)
    none = lambda r: (0, 0) if r["kind"] == "G" else (1, 1)  # noqa: E731
    merged = lambda r: (1, 1)  # noqa: E731
    o = outcomes(recs, {st.L("base", "none", "default"): none, st.L("base", "merged", "default"): merged})
    cg = st.c_gate(recs, o, AN)
    assert cg["headroom_records"] == 0 and cg.get("ceiling_limited") and cg["note"] == "C gate ceiling-limited"
    assert cg["headroom_points"] == 0.0


def h1_world(n: int, g_gain: int) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    recs = recs_balanced(n)
    none = lambda r: (0, 0) if r["kind"] == "G" else (1, 1)  # noqa: E731
    merged = lambda r: (1, 1) if (r["kind"] == "C" or idx(r) < g_gain) else (0, 0)  # noqa: E731
    return recs, outcomes(recs, {st.L("base", "none", "default"): none, st.L("base", "merged", "default"): merged})


def test_layer1_h1_rejected_h1b_tested() -> None:
    recs, o = h1_world(30, 30)
    r = st.layer1(recs, o, {}, full_reach(recs), AN)
    assert r["H1"]["status"] == st.CONFIRMED and r["H1"]["level"] == 0.05
    assert r["H1b"]["tested"] is True and r["H1b"]["status"] == st.CONFIRMED and "p" in r["H1b"]
    assert r["families"]["G"]["members"] == ["H1", "H1b"] and r["families"]["N"]["members"] == ["H2"]


def test_layer1_h1_not_rejected_h1b_not_tested() -> None:
    """3 papers gain both G records, 3 lose both: k = 6 (evaluable), p = 42/64, not confirmed;
    H1b is then absent (not tested) and carries no p, only its estimate, CI and power."""
    recs = recs_balanced(30)
    none = lambda r: ((1, 1) if 10 <= idx(r) < 13 else (0, 0)) if r["kind"] == "G" else (1, 1)  # noqa: E731
    merged = lambda r: ((1, 1) if idx(r) < 3 else (0, 0)) if r["kind"] == "G" else (1, 1)  # noqa: E731
    o = outcomes(recs, {st.L("base", "none", "default"): none, st.L("base", "merged", "default"): merged})
    r = st.layer1(recs, o, {}, full_reach(recs), AN)
    assert r["H1"]["status"] == st.NOT_CONFIRMED and r["H1"]["p"] == pytest.approx(42 / 64)
    assert r["H1"]["c_gate"]["verdict"] == "moot"
    assert r["H1b"]["status"] == st.NOT_TESTED and "p" not in r["H1b"]
    assert r["H1b"]["ci95"] is not None
    # H1b's power is the §8 label-randomization simulation, never H1's normal formula
    assert "power_floor_normal" not in r["H1b"] and "power_floor_normal" in r["H1"]
    assert r["H1b"]["power_floor"]["q0.05_p0.85"] == 0.67 and r["H1b"]["power_floor"]["q0.1_p0.7"] == 0.54
    assert r["H1b"]["power_floor"]["source"].startswith("see §8 simulation")


def test_h1b_power_reads_a_simulation_from_the_power_artifact(tmp_path: Path) -> None:
    art = tmp_path / "power.json"
    art.write_text(json.dumps({"simulation": {"H1b": {"p0.85_q0.05": {"0.05": 0.671}, "p0.7_q0.1": {"0.05": 0.538}}}}))
    got = st.h1b_power(art)
    assert (got["q0.05_p0.85"], got["q0.1_p0.7"]) == (0.671, 0.538) and got["sha256"] == asc.sha256_file(art)
    # the committed artifact has no H1b simulation (yet): §8's figures, named as such
    assert st.h1b_power()["q0.05_p0.85"] == st.H1B_POWER_SECTION8["q0.05_p0.85"]


def test_reach_gate() -> None:
    recs, o = h1_world(30, 30)
    n_g = sum(r["kind"] == "G" for r in recs)
    ok = st.layer1(recs, o, {}, {"count": n_g, "of": n_g}, AN)
    assert ok["H1"]["status"] == st.CONFIRMED
    edge = st.layer1(recs, o, {}, {"count": int(np.ceil(0.8 * n_g)), "of": n_g}, AN)
    assert edge["H1"]["status"] == st.CONFIRMED                                  # exactly 80 % passes
    low = st.layer1(recs, o, {}, {"count": int(np.ceil(0.8 * n_g)) - 1, "of": n_g}, AN)
    assert low["H1"]["status"] == st.GATE and any("reach" in x for x in low["H1"]["gate_reasons"])
    absent = st.layer1(recs, o, {}, None, AN)
    assert absent["H1"]["status"] == st.GATE and absent["H1"]["reach"]["note"] == "reach count not supplied"
    assert low["H1b"]["status"] == st.NOT_TESTED


def test_min_clusters_gate() -> None:
    recs, o = h1_world(7, 7)
    r = st.layer1(recs, o, {}, full_reach(recs), AN)
    assert r["H1"]["status"] == st.GATE and any("papers < 8" in x for x in r["H1"]["gate_reasons"])
    assert r["H2"]["status"] == st.GATE and any("papers < 8" in x for x in r["H2"]["gate_reasons"])


def test_cell_gate_from_the_runner() -> None:
    recs, o = h1_world(30, 30)
    gates = {st.L("base", "merged", "default"): "GATE-NOT-EVALUABLE: the reranker was not active"}
    r = st.layer1(recs, o, gates, full_reach(recs), AN)
    assert r["H1"]["status"] == st.GATE and r["H1b"]["status"] == st.NOT_TESTED
    gates2 = {st.L("base", "merged", "norerank"): "GATE-NOT-EVALUABLE: depth"}
    assert st.layer1(recs, o, gates2, full_reach(recs), AN)["H1"]["status"] == st.CONFIRMED


def h2_world(gold_follows: bool, n_drop: int = 15) -> list[Any]:
    recs = recs_balanced(30)
    nb = lambda r: ((0, 0 if gold_follows else 1) if idx(r) < n_drop else (1, 1))  # noqa: E731
    rnd = lambda r: ((1, 1 if gold_follows else 0) if idx(r) < n_drop else (1, 1))  # noqa: E731
    o = outcomes(recs, {st.L("neighbour", "none", "default"): nb, st.L("random1", "none", "default"): rnd,
                        st.L("random2", "none", "default"): rnd})
    return [recs, o]


def test_h2_two_draw_average_and_gold_gate() -> None:
    recs, o = h2_world(True)
    r = st.layer1(recs, o, {}, None, AN)
    assert r["H2"]["status"] == st.CONFIRMED and r["H2"]["net_points"] < 0 and r["H2"]["gold_only_net_points"] < 0
    recs, o = h2_world(False)
    r = st.layer1(recs, o, {}, None, AN)
    assert r["H2"]["p"] <= 0.05 and r["H2"]["status"] == st.NOT_CONFIRMED_GOLD


def test_h2_gate_not_evaluable_paths() -> None:
    """A gated random-draw cell, or 4 non-zero papers (min p 1/16 > 0.05), make H2 GATE."""
    recs, o = h2_world(True)
    r = st.layer1(recs, o, {st.L("random1", "none", "default"): "GATE-NOT-EVALUABLE: depth"}, None, AN)
    assert r["H2"]["status"] == st.GATE and r["H2"]["p_in_procedure"] == 1.0
    recs4, o4 = h2_world(True, n_drop=4)
    r4 = st.layer1(recs4, o4, {}, None, AN)
    assert r4["H2"]["n_nonzero"] == 4 and r4["H2"]["status"] == st.GATE
    assert any("minimum attainable p" in x for x in r4["H2"]["gate_reasons"])


def test_h2_averages_the_draws() -> None:
    recs = recs_balanced(10)
    nb = lambda r: (0, 0)  # noqa: E731
    r1 = lambda r: (1, 1)  # noqa: E731
    r2 = lambda r: (0, 0)  # noqa: E731
    o = outcomes(recs, {st.L("neighbour", "none", "default"): nb, st.L("random1", "none", "default"): r1,
                        st.L("random2", "none", "default"): r2})
    cl, v, _ = st.contrast_values(recs, o, st.H2_SPEC)
    assert set(v) == {-0.5}


def test_missing_records_excluded_or_scored() -> None:
    recs = recs_balanced(10)
    o = outcomes(recs, {})
    o[st.L("base", "merged", "default")][recs[0]["record_id"]] = None
    g = [r for r in recs if r["kind"] == "G"]
    _, v, miss = st.contrast_values(g, o, st.H1_SPEC)
    assert miss == 1 and len(v) == len(g) - 1
    _, v2, miss2 = st.contrast_values(g, o, st.H1_SPEC, missing="as_not_found")
    assert miss2 == 1 and len(v2) == len(g) and -1.0 in v2


def test_h1b_per_paper_statistic() -> None:
    recs = recs_balanced(10)
    none = lambda r: (0, 0)  # noqa: E731
    merged = lambda r: (1, 1) if r["kind"] == "G" and r["record_id"].endswith("g0") else (0, 0)  # noqa: E731
    o = outcomes(recs, {st.L("base", "none", "default"): none, st.L("base", "merged", "default"): merged})
    cl, v, _ = st.h1b_values(recs, o)
    assert len(cl) == 10 and set(v) == {0.5}


def test_equivalence_reading() -> None:
    """§3.3: *no effect larger than delta* only when the 90 % CI lies within +-delta."""
    e = st.equivalence(600, 30, [-1.0, 1.0])
    assert e["delta_points"] == pytest.approx(round(st.apw.mde80_points(600, 0.05), 4))
    assert e["within"] and e["reading"].startswith("no effect larger than")
    assert not st.equivalence(600, 30, [-1.0, 2.6])["within"]



def test_zero_discordance_reads_not_resolved() -> None:
    """Owner 2026-11-03: with no discordant record the MDE is 0 and there is no equivalence
    bound: the reading is *not resolved*, with the discordant count (0) beside it, even when
    the CI is exactly [0, 0]."""
    z = st.equivalence(600, 0, [0.0, 0.0])
    assert z["reading"] == "not resolved" and z["discordant"] == 0 and z["within"] is False
    assert z["delta_points"] is None
    assert st.equivalence(600, 30, [-1.0, 1.0])["discordant"] == 30


# ------------------------------------------------------------------ Layer 2 rules (§3.3)

def f(net: float, lo: float, hi: float) -> dict[str, Any]:
    return {"net_points": net, "ci95": [lo, hi]}


def attach(lift: float, lci: tuple[float, float], dc: float, cci: tuple[float, float]) -> str:
    return st.attach_rule(f(lift, *lci), f(dc, *cci))["reading"]


def test_harm_rule() -> None:
    assert attach(-1, (-3, -0.01), 0, (-1, 1)) == "harmful, do not attach"
    assert attach(-1, (-3, 0.0), 0, (-1, 1)) == "not resolved"                  # L_hi = 0 is not < 0
    assert attach(-1, (-3, -0.5), -5, (-8, -2)) == "harmful, do not attach"     # harm is read first


def test_c_cost_point_boundary() -> None:
    """Do not attach when the ΔC point estimate is **below** -1 (strict): ΔC = -1 exactly passes."""
    assert attach(3, (1, 5), -1.0, (-3, 1)) == "attach"
    assert attach(3, (1, 5), -1.0001, (-3, 1)) == "do not attach (C cost)"


def test_c_cost_lower_bound_boundary() -> None:
    """Do not attach when C_lo is **below** -3.6 (strict): C_lo = -3.6 exactly passes."""
    assert attach(3, (1, 5), 0.0, (-3.6, 2)) == "attach"
    assert attach(3, (1, 5), 0.0, (-3.6001, 2)) == "do not attach (C cost)"


def test_attach_needs_lift_2_and_lower_bound_above_0() -> None:
    assert attach(2.0, (0.1, 4), 0, (-1, 1)) == "attach"                         # L = 2 exactly attaches
    assert attach(1.99, (0.1, 4), 0, (-1, 1)) == "not resolved"
    assert attach(3, (0.0, 5), 0, (-1, 1)) == "not resolved"                     # L_lo must be > 0
    assert st.attach_rule({"net_points": None, "ci95": None}, f(0, -1, 1))["reading"] == "not resolved"


def test_attach_rule_compares_unrounded_figures() -> None:
    """Printed -1.0 / -3.6, but the unrounded figure lies past the bound: the rule fires."""
    lift = {"net_points": 3.0, "ci95": [1.0, 5.0]}
    c_pt = {"net_points": -1.0, "net_exact": -1.00004, "ci95": [-3.0, 1.0], "ci95_exact": [-3.0, 1.0]}
    c_lo = {"net_points": 0.0, "net_exact": 0.0, "ci95": [-3.6, 2.0], "ci95_exact": [-3.60004, 2.0]}
    assert st.attach_rule(lift, c_pt)["reading"] == "do not attach (C cost)"
    assert st.attach_rule(lift, c_lo)["reading"] == "do not attach (C cost)"
    assert st.attach_rule(lift, {**c_pt, "net_exact": -1.0})["reading"] == "attach"


def test_reranker_non_inferiority_at_minus_3() -> None:
    rr = lambda net, lo, hi: st.reranker_rule(f(net, lo, hi))["reading"]  # noqa: E731
    assert rr(-1, -2.9, 1) == "keep the default (non-inferior to BM25)"
    assert rr(-1, -3.0, 1) == "not resolved"                                      # D_lo must be > -3
    assert rr(-5, -7, -3.01) == "default inferior to BM25 by more than the margin: reconsider"
    assert rr(-5, -7, -3.0) == "not resolved"                                     # D_hi must be < -3


def test_growth_rule() -> None:
    """A concern at <= -3 with the CI excluding 0; *not a concern* only through the
    equivalence bound (§3.3); a wide CI reads *not resolved*."""
    eq = lambda within: {"within": within, "reading": "no effect larger than 2.1 points" if within else "not resolved"}  # noqa: E731
    assert st.growth_rule(f(-3, -5, -0.1))["reading"] == "a concern"
    assert st.growth_rule(f(-3, -5, 0.0))["reading"] == "not resolved"
    assert st.growth_rule(f(-2.9, -4, -1))["reading"] == "not resolved"
    assert st.growth_rule({**f(0.2, -1, 1.5), "equivalence": eq(True)})["reading"] == \
        "not a concern: no effect larger than 2.1 points"
    assert st.growth_rule({**f(0.2, -4, 4.5), "equivalence": eq(False)})["reading"] == "not resolved"


def l1_stub(h1: str, h1b: str = st.NOT_TESTED, h2: str = st.NOT_CONFIRMED, gates: list[str] | None = None
            ) -> dict[str, Any]:
    return {"H1": {"status": h1, "gate_reasons": gates or []}, "H1b": {"status": h1b}, "H2": {"status": h2}}


def decisions(att: str, gro: str = "not resolved", point: bool = False, lower: bool = False) -> dict[str, Any]:
    return {"attach_merged_glossary": {"reading": att, "n_c": 300,
                                       "conditions": {"c_point_lt_-1": point, "c_lower_lt_-3.6": lower}},
            "related_paper_growth_concern": {"reading": gro}, "keep_reranker_default": {"reading": "not resolved"}}


C36 = "; a cost on term-bearing passages smaller than about 3.6 points is not ruled out (300 C records)"


def test_serving_statements_follow_layer1() -> None:
    s = lambda att, l1, **kw: st.serving_statements(decisions(att, **kw), l1)["glossary"]["statement"]  # noqa: E731
    # every attach statement carries the 3.6-point C cost limit (R12)
    assert s("attach", l1_stub(st.CONFIRMED, st.CONFIRMED)) == "attach, for questions using the glossary's primary term" + C36
    assert s("attach", l1_stub(st.CONFIRMED, st.NOT_CONFIRMED)) == \
        "attach, for questions using the glossary's primary term (alias-specificity unresolved)" + C36
    assert s("attach", l1_stub(st.NOT_CONFIRMED)) == "attach provisionally; the confirmatory claim was not confirmed" + C36
    assert s("attach", l1_stub(st.GATE, gates=["C gate fired (rule C)"])) == \
        "attach provisionally, claim not evaluable (C gate fired (rule C))" + C36
    assert s("harmful, do not attach", l1_stub(st.CONFIRMED)) == "harmful, do not attach"
    # C cost with H1 confirmed names the part of the rule that fired, both when both did
    head = "do not attach: the gain on alias questions is confirmed, but "
    assert s("do not attach (C cost)", l1_stub(st.CONFIRMED), point=True) == head + "term-bearing passages lost more than 1 point"
    assert s("do not attach (C cost)", l1_stub(st.CONFIRMED), lower=True) == \
        head + "a loss on term-bearing passages larger than 3.6 points is not ruled out"
    assert s("do not attach (C cost)", l1_stub(st.CONFIRMED), point=True, lower=True) == \
        head + "term-bearing passages lost more than 1 point and a loss on term-bearing passages larger than 3.6 points is not ruled out"
    assert s("do not attach (C cost)", l1_stub(st.NOT_CONFIRMED), point=True) == "do not attach (C cost)"
    assert s("not resolved", l1_stub(st.CONFIRMED)) == "not resolved"
    g = lambda h2: st.serving_statements(decisions("not resolved", "a concern"), l1_stub(st.NOT_CONFIRMED, h2=h2))  # noqa: E731
    assert g(st.CONFIRMED)["growth"]["statement"] == "related-paper growth is a concern"
    assert g(st.NOT_CONFIRMED_GOLD)["growth"]["statement"].endswith("the confirmatory claim was not confirmed")
    assert g(st.GATE)["growth"]["statement"] == "a concern provisionally; not evaluable"


# ------------------------------------------------------------------ the judge false-yes bound on H2 (§3.4)

def frows(spec: list[tuple[str, int | None, Any, int]], sampled: bool = True) -> dict[Any, dict[str, Any]]:
    """(arm, final label, opus verdict, count) -> final rows."""
    out: dict[Any, dict[str, Any]] = {}
    for arm, lab, opus, n in spec:
        for _ in range(n):
            out[len(out)] = {"arm": arm, "label": lab, "opus": opus, "drift_sampled": sampled}
    return out


def test_false_yes_counts_read_answers_labels_opus_answered() -> None:
    rows = frows([("random", 1, 0, 3), ("random", 1, 1, 7), ("random", 0, 0, 5), ("random", 1, None, 2),
                  ("base", 1, 1, 10)])
    rows.update({1000 + k: v for k, v in frows([("random", 1, 0, 9)], sampled=False).items()})
    c = st.false_yes_counts(rows)
    assert (c["random"]["k"], c["random"]["n"]) == (3, 10) and (c["pooled"]["k"], c["pooled"]["n"]) == (3, 20)
    assert c["neighbour"]["n"] == 0 and c["neighbour"]["wilson95"] is None


def test_u_is_max_of_random_and_pooled() -> None:
    """U = max(U_random, U_pooled), always (owner 2026-11-03). (a) random larger with only 10
    pairs: v1's '< 30 -> pooled' rule would have taken the pooled one; (b) pooled larger: a
    random-only U would be too small; (c) no random pair: the pooled one."""
    a = st.false_yes_bound(-2.0, st.false_yes_counts(frows([("random", 1, 0, 3), ("random", 1, 1, 7),
                                                            ("base", 1, 1, 10)])), 90.0, 900)
    assert a["U_random"] == 0.6033 and a["U_pooled"] == 0.3605 and a["U"] == 0.6033 and a["U_from"] == "random"
    assert a["B_points"] == pytest.approx(100 * 0.6033 * 90 / 900) and a["within_bound"] is True
    b = st.false_yes_bound(-3.0, st.false_yes_counts(frows([("random", 1, 1, 40), ("base", 1, 0, 10),
                                                            ("base", 1, 1, 10)])), 90.0, 900)
    assert b["U_random"] == 0.0877 and b["U_pooled"] == 0.2804 and b["U"] == 0.2804 and b["U_from"] == "pooled"
    assert b["B_points"] == pytest.approx(2.804) and b["within_bound"] is False   # |-3| > 2.804
    c = st.false_yes_bound(-1.0, st.false_yes_counts(frows([("base", 1, 0, 3), ("base", 1, 1, 7)])), 90.0, 900)
    assert c["U_random"] is None and c["U"] == c["U_pooled"] == 0.6033
    none = st.false_yes_bound(-1.0, st.false_yes_counts({}), 90.0, 900)
    assert none["B_points"] is None and none["within_bound"] is False


def test_false_yes_bound_boundary_and_denominator() -> None:
    """|H2 net| = B exactly is *within* the bound (<=); B divides by the contrast's N, not 900."""
    cnt = st.false_yes_counts(frows([("random", 1, 0, 3), ("random", 1, 1, 7)]))
    b = 100.0 * 0.6033 * 90.0 / 900
    assert st.false_yes_bound(-b, cnt, 90.0, 900)["within_bound"] is True
    assert st.false_yes_bound(-b * 1.0001, cnt, 90.0, 900)["within_bound"] is False
    half = st.false_yes_bound(-1.0, cnt, 90.0, 450)
    assert half["N"] == 450 and half["B_points"] == pytest.approx(100 * 0.6033 * 90 / 450)


def test_wilson_limits_round_outward() -> None:
    assert st._wilson(3, 10) == [0.1077, 0.6033]          # 0.10779… down, 0.60322… up
    assert st._wilson(10, 10)[1] == 1.0 and st._wilson(0, 40) == [0.0, 0.0877]
    assert st._wilson(0, 0) is None


def test_judged_only_hits_average_the_draws() -> None:
    recs = recs_balanced(10)
    o = outcomes(recs, {st.L("random1", "none", "default"): lambda r: (1, 0) if idx(r) < 4 else (1, 1),
                        st.L("random2", "none", "default"): lambda r: (1, 0) if idx(r) < 2 else (0, 0)})
    assert st.judged_only_hits(recs, o) == pytest.approx((12 + 6) / 2)


# ------------------------------------------------------------------ fixed sensitivities (§7, R6)

def test_studentized_equals_the_primary_p_when_exact() -> None:
    """One score per paper: the studentized statistic is increasing in the sum, so its exact p
    (enumerated over the studentized statistic itself at k <= 20) is the primary sign-flip p.
    Beyond k = 20 the p is the lattice p, never the Monte-Carlo one (reported as ``p_perm``)."""
    import chunktrial_stats as cs

    rng = np.random.default_rng(5)
    for alt in ("greater", "less"):
        for _ in range(4):
            s = rng.choice([-2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0], size=40)
            s[np.flatnonzero(s)[12:]] = 0.0                                       # k <= 12: enumeration
            got = st.studentized_sign_flip(s, alt, AN)
            assert got["p_method"] == "enumeration"
            assert got["p"] == pytest.approx(cs.sign_flip_test_exact(s, alternative=alt)["p"], abs=1e-12)
    big = np.array([1.0] * 18 + [-1.0] * 12 + [0.5] * 6)
    lat = st.studentized_sign_flip(big, "greater", AN)
    ex = cs.sign_flip_test_exact(big, alternative="greater")
    assert lat["p_method"] == "lattice (monotone in the sum)" and lat["p"] == pytest.approx(ex["p"], abs=1e-15)
    assert lat["p_perm"] != lat["p"] and abs(lat["p_perm"] - ex["p"]) < 0.03     # MC cross-check only
    assert lat["min_attainable_p"] == pytest.approx(2.0 ** -36)


def data_stub(recs: list[dict[str, Any]], cfg: dict[str, Any]) -> Any:
    d = st.Data.__new__(st.Data)
    d.cfg, d.bal, d.rows, d.pair_paper, d.pair_arm = cfg, recs, {}, {}, {}
    return d


def test_h2_gold_gate_applies_to_every_re_test(tmp_path: Path) -> None:
    """The judged H2 test rejects but the gold-only change points the other way: the primary is
    *not confirmed (gold-only direction gate)*. Each H2 re-test that rejects (bootstrap-t, topic
    clustering, volume covariate) must read the same, or it would 'confirm' H2 and make the
    primary fragile spuriously."""
    recs, o = h2_world(False)
    l1 = st.layer1(recs, o, {}, None, AN)
    assert l1["H2"]["status"] == st.NOT_CONFIRMED_GOLD
    cov = {f"P{i:02d}": {"topic": f"T{i % 10}",
                         "added_chunks": {"neighbour": 30 + i % 2, "random1": 30, "random2": 30}} for i in range(30)}
    (tmp_path / "cov.json").write_text(json.dumps(cov))
    fs = st.fixed_sensitivities(data_stub(recs, {"paper_covariates": str(tmp_path / "cov.json")}), o, l1, AN)
    for name in ("bootstrap_t", "topic_clustering", "volume_covariate"):
        assert fs["H2"][name]["p"] <= 0.05, name                         # it would confirm without the gate
        assert fs["H2"][name]["verdict"] == st.NOT_CONFIRMED_GOLD, name
    assert st.fragile("H2", l1["H2"]["status"], fs["H2"])["fragile"] is False


def test_topic_clustering_pools_papers() -> None:
    names = [f"P{i}" for i in range(10)]
    sums = np.array([1.0] * 10)
    tsum, k, no_topic = st.topic_sums(names, sums, {p: ("A" if i < 5 else "B") for i, p in enumerate(names[:8])})
    assert k == 4 and no_topic == 2 and sorted(tsum.tolist()) == [1.0, 1.0, 3.0, 5.0]   # untopicked: own clusters


def test_freedman_lane_holds_level_where_the_fixed_slope_method_did_not() -> None:
    """The reviewer's null case (O13): S = 0.5 x + e, no effect at x = 0, x-bar / s_x = 1. The
    fixed-slope adjust-then-flip method rejected at about .15 at nominal .05; the Freedman–Lane
    sign-flip with the studentized intercept stays near .05 (small reps, loose bound)."""
    rng = np.random.default_rng(20261103)
    reps, rej = 150, 0
    for i in range(reps):
        x = rng.normal(1.0, 1.0, 40)
        y = 0.5 * x + rng.normal(0.0, 1.0, 40)
        rej += st.freedman_lane_intercept(y, x, "less", 400, i)["p"] <= 0.05
    assert rej / reps <= 0.10


def test_freedman_lane_reads_the_intercept_not_the_volume() -> None:
    """A pure volume effect (S = -0.5 x + e, x > 0) is not an H2 effect at equal volume; a
    shifted intercept (S = -1 - 0.5 x + e) is. The p is Monte Carlo and says so."""
    rng = np.random.default_rng(3)
    x = rng.uniform(0.5, 3.0, 60)
    e = rng.normal(0.0, 0.3, 60)
    X = np.column_stack([np.ones(60), x])
    e = e - X @ np.linalg.lstsq(X, e, rcond=None)[0]       # noise orthogonal to [1, x]: fitted intercepts exact
    vol = st.freedman_lane_intercept(-0.5 * x + e, x, "less", 2000, 1)
    eff = st.freedman_lane_intercept(-1.0 - 0.5 * x + e, x, "less", 2000, 1)
    assert vol["p"] > 0.05 and eff["p"] <= 0.05 and eff["intercept_per_paper"] == pytest.approx(-1.0)
    assert vol["intercept_per_paper"] == pytest.approx(0.0, abs=1e-9)
    assert eff["p_method"].startswith("monte_carlo (Freedman–Lane") and eff["min_attainable_p"] == pytest.approx(1 / 2001)
    assert st.freedman_lane_intercept(np.ones(5), np.ones(5), "less", 100, 1)["p"] is None   # constant covariate


def test_freedman_lane_flips_the_restricted_residuals() -> None:
    """Freedman–Lane flips the residuals of the **restricted** model S = g x (no intercept), not
    the full model's: re-implemented here independently, the p and the null law match exactly.
    Under an effect the two differ (the restricted residuals carry the intercept)."""
    rng = np.random.default_rng(8)
    x = rng.uniform(0.5, 3.0, 30)
    y = -0.4 + 0.3 * x + rng.normal(0.0, 0.5, 30)
    got = st.freedman_lane_intercept(y, x, "less", 500, 4)
    X = np.column_stack([np.ones(30), x])
    inv = np.linalg.inv(X.T @ X)

    def t_int(v: np.ndarray) -> float:
        coef = inv @ X.T @ v
        r = v - X @ coef
        return float(coef[0] / np.sqrt(r @ r / 28 * inv[0, 0]))

    def p_with(resid: np.ndarray, fit: np.ndarray) -> float:
        eps = np.random.default_rng(4).integers(0, 2, size=(500, 30)).astype(float) * 2 - 1
        t_obs = t_int(y)
        return (sum(t_int(fit + e * resid) <= t_obs + 1e-9 for e in eps) + 1) / 501

    g_r = float(x @ y / (x @ x))
    restricted = p_with(y - g_r * x, g_r * x)
    coef = inv @ X.T @ y
    full = p_with(y - X @ coef, g_r * x)
    assert got["p"] == pytest.approx(restricted) and got["restricted_slope"] == pytest.approx(g_r)
    assert restricted != pytest.approx(full)


def test_bootstrap_t_type_one_error_under_a_skewed_mean_zero_null() -> None:
    """S_j = Exp(1) - 1 (mean 0, right-skewed), 50 papers: the one-sided bootstrap-t rejects
    near .05 (small reps, loose bound), in both directions."""
    rng = np.random.default_rng(11)
    reps = 200
    for alt in ("greater", "less"):
        rej = sum(st.bootstrap_t(rng.exponential(1.0, 50) - 1.0, alt, 400, i)["p"] <= 0.05 for i in range(reps))
        assert 0.005 <= rej / reps <= 0.11, (alt, rej / reps)


def test_bootstrap_t_can_disagree_with_the_primary() -> None:
    """29 papers at +1 and one at -7: the sign-flip (symmetric null) rejects, the bootstrap-t
    (the weak null, skew-aware) does not; the re-test then makes a confirmed H1 fragile."""
    import chunktrial_stats as cs

    s = np.array([-7.0] + [1.0] * 29)
    prim = cs.sign_flip_test_exact(s, alternative="greater")
    bt = st.bootstrap_t(s, "greater", 2000, 11)
    assert prim["p"] <= 0.05 < bt["p"] and bt["n_boot"] == 2000 and bt["seed"] == 11
    v = st.retest_verdict(bt["p"], bt["min_attainable_p"])
    assert st.fragile("H1", st.CONFIRMED, {"bootstrap_t": {"verdict": v}})["flipped_by"] == ["bootstrap_t"]


def test_tercile_boundaries_are_read_not_computed() -> None:
    assert st.tercile_of(10.0, [10, 20]) == "T1" and st.tercile_of(10.01, [10, 20]) == "T2"
    assert st.tercile_of(20.0, [10, 20]) == "T2" and st.tercile_of(20.5, [10, 20]) == "T3"


def test_origin_counts_of_displacing_chunks() -> None:
    recs = recs_balanced(10)
    target = next(r for r in recs if r["kind"] == "G" and idx(r) == 3)
    rid = target["record_id"]
    o = outcomes(recs, {st.L("neighbour", "none", "default"): lambda r: (0, 0) if r["record_id"] == rid else (1, 1)})
    rows = {st.L("neighbour", "none", "default"): [{"record_id": rid, "ranked_chunk_ids": ["N00-c0", "N01-c0", "P05-c1"]}]}
    paper = {(rid, "N00-c0"): "N00", (rid, "N01-c0"): "N01", (rid, "P05-c1"): "P05"}
    arm = {(rid, "N00-c0"): "neighbour", (rid, "N01-c0"): "neighbour", (rid, "P05-c1"): "base"}
    got = st.origin_counts(recs, o, rows, paper, arm, {"N00": "P03", "N01": "P07"})
    assert got["displacing_chunks"] == {"records": 1, "base": 1, "own_paper_neighbour": 1,
                                        "other_study_paper_neighbour": 1}
    unattr = st.origin_counts(recs, o, rows, paper, arm, None)["displacing_chunks"]
    assert unattr["neighbour (attribution not supplied)"] == 2


# ------------------------------------------------------------------ fragile (§7, owner 2026-11-03)

def sv(verdict: str) -> dict[str, str]:
    return {"verdict": verdict}


def test_fragile_allowed_cases() -> None:
    for name in ("bootstrap_t", "topic_clustering"):
        r = st.fragile("H1", st.CONFIRMED, {name: sv(st.NOT_CONFIRMED)})
        assert r["fragile"] and r["flipped_by"] == [name]
    assert st.fragile("H1", st.NOT_CONFIRMED, {"topic_clustering": sv(st.CONFIRMED)})["fragile"]
    assert st.fragile("H2", st.CONFIRMED, {"volume_covariate": sv(st.NOT_CONFIRMED_GOLD)})["fragile"]
    assert st.fragile("H2", st.NOT_CONFIRMED_GOLD, {"bootstrap_t": sv(st.CONFIRMED)})["fragile"]


def test_fragile_forbidden_cases() -> None:
    # the volume covariate re-tests H2 only
    assert not st.fragile("H1", st.CONFIRMED, {"volume_covariate": sv(st.NOT_CONFIRMED)})["fragile"]
    # subgroups and declared sensitivities never make anything fragile
    sub = {k: sv(st.NOT_CONFIRMED) for k in ("xml_only_stratum", "terciles", "without_codex_verdicts",
                                             "H2_vs_random1_alone", "unjudged_yes")}
    assert not st.fragile("H2", st.CONFIRMED, sub)["fragile"]
    # H1b is exempt
    hb = st.fragile("H1b", st.CONFIRMED, {"bootstrap_t": sv(st.NOT_CONFIRMED)})
    assert not hb["fragile"] and hb["considered"] == []
    # a GATE primary cannot be flipped; a GATE re-test flips nothing; gold vs plain not confirmed is no flip
    assert not st.fragile("H1", st.GATE, {"bootstrap_t": sv(st.CONFIRMED)})["fragile"]
    # the studentized sign-flip is an identity check: it decides nothing
    assert not st.fragile("H1", st.CONFIRMED, {"studentized_identity_check": sv(st.NOT_CONFIRMED),
                                               "studentized": sv(st.NOT_CONFIRMED)})["fragile"]
    assert not st.fragile("H1", st.CONFIRMED, {"topic_clustering": sv(st.GATE)})["fragile"]
    assert not st.fragile("H2", st.NOT_CONFIRMED, {"bootstrap_t": sv(st.NOT_CONFIRMED_GOLD)})["fragile"]


# ------------------------------------------------------------------ outcomes from rows

def test_leg_outcomes_gold_judged_unjudged_and_rr() -> None:
    recs = {r["record_id"]: r for r in fx.make_records(2)}
    rid = "alias-O-P00-g0"
    labels = {(rid, "X1"): None, (rid, "X2"): 1, (rid, "X3"): 1}
    rows = [{"record_id": rid, "ranked_chunk_ids": ["X1", "X2", "P00-c0"], "failed": False}]
    out = st.leg_outcomes(rows, recs, lambda r, c: labels.get((r, c)), {(rid, "X2"): "P07"})
    # the gold is in the top 10: the hit rests on the gold, whatever is judged above it (RR@10 sees X2)
    assert out[rid] == {"hit": 1, "hit_gold": 1, "rr": 0.5, "rr_gold": 1 / 3, "kind": "gold"}
    judged = [{"record_id": rid, "ranked_chunk_ids": ["X1", "X2", "Z9"], "failed": False}]
    assert st.leg_outcomes(judged, recs, lambda r, c: labels.get((r, c)), {(rid, "X2"): "P07"})[rid]["kind"] == "other"
    out2 = st.leg_outcomes(judged, recs, lambda r, c: labels.get((r, c)), {(rid, "X2"): "P00"})
    assert out2[rid]["kind"] == "same" and out2[rid]["hit_gold"] == 0
    rows3 = [{"record_id": rid, "ranked_chunk_ids": ["X1"] + [f"Y{i}" for i in range(9)] + ["P00-c0"]}]
    o3 = st.leg_outcomes(rows3, recs, lambda r, c: None, {})
    assert o3[rid]["hit"] == 0 and o3[rid]["hit_gold"] == 0          # gold at rank 11; unjudged = no
    assert st.leg_outcomes([{"record_id": rid, "failed": True}], recs, lambda r, c: 1, {})[rid] is None


def test_labeller_sensitivity_modes() -> None:
    d = st.Data.__new__(st.Data)
    d.final = {("r", "a"): {"source": "unjudged", "label": None, "c_label": 1, "e_label": 0},
               ("r", "b"): {"source": "tiebreak", "label": 1, "c_label": 0, "e_label": 1},
               ("r", "d"): {"source": "tiebreak_r2", "label": 1, "c_label": 0, "e_label": 1},   # the first version
               ("r", "c"): {"source": "agree", "label": 0, "c_label": 0, "e_label": 0}}
    p, u, c, e = (d.labeller(x) for x in ("primary", "unjudged_yes", "tb_as_c", "tb_as_e"))
    assert (p("r", "a"), u("r", "a"), c("r", "a"), e("r", "a")) == (None, 1, 1, 0)
    assert (p("r", "b"), c("r", "b"), e("r", "b")) == (1, 0, 1)
    assert p("r", "c") == u("r", "c") == c("r", "c") == 0
    assert (p("r", "d"), c("r", "d"), e("r", "d")) == (1, 0, 1)


def test_draw_gap_flag() -> None:
    assert st.within_draw_variation(-1.0, 1.5) and not st.within_draw_variation(-2.0, 1.5)
    assert not st.within_draw_variation(-1.5, 1.5) and not st.within_draw_variation(1.5, -1.5)  # "smaller than"
    assert not st.within_draw_variation(None, 1.0)


# ------------------------------------------------------------------ end to end on files

def write_final(world: fx.World, label: Any, extra_legs: tuple[str, ...] = ()) -> None:
    jdir = world.tmp / "judge"
    jdir.mkdir(exist_ok=True)
    rows = []
    pairs = []
    legs = [asc.leg_id(*x) for x in asc.all_legs()] + list(extra_legs)
    for leg in legs:
        for rec in world.records:
            for cid in world.default_top10(leg, rec):
                if cid == rec["gold_chunks"][0]:
                    continue
                rows.append({"k": f"pair|{rec['record_id']}|{cid}", "pair_id": f"{rec['record_id']}|{cid}",
                             "record_id": rec["record_id"], "chunk_id": cid, "arm": "base",
                             "label": label(rec, cid), "source": "agree", "c_label": 0, "e_label": 0,
                             "drift_sampled": True, "opus": 1})
                pairs.append({"pair_id": f"{rec['record_id']}|{cid}", "record_id": rec["record_id"], "chunk_id": cid,
                              "c_paper": cid.split("-")[0], "arm": "base"})
    fx.write_jsonl(jdir / "final.jsonl", rows)
    fx.write_jsonl(jdir / "pairs.jsonl", pairs)


def patch_summary(world: fx.World, run: str, fn: Any) -> None:
    """Edit one run summary (``<corpus>/<label>``) in place, before stage A is committed."""
    sp = world.runs / f"{run}.summary.json"
    s = json.loads(sp.read_text())
    fn(s)
    sp.write_text(json.dumps(s))


def commit_stage_a(world: fx.World, mode: str = "dry_run", runs_mode: str | None = None,
                   judge_mode: str | None = None, judge_summary: dict[str, Any] | None = None) -> Path:
    """What the judge's ``commit-stage-a`` leaves behind, enough for ``verify_marker``: the
    ``STUDY-MODE`` markers, ``stage-a/hits.jsonl`` (computed here independently: gold in the top
    10, or a top-10 chunk labelled *answers*), the frozen summaries, the commit, its public file
    and the marker. Returns the judge dir."""
    import alias_study_judge as aj
    import alias_study_run as ar

    jdir = world.tmp / "judge"
    (world.runs / ar.MODE_MARKER).write_text((runs_mode or mode) + "\n")
    (jdir / ar.MODE_MARKER).write_text((judge_mode or mode) + "\n")
    fin = {(x["record_id"], x["chunk_id"]): x for x in asc.read_jsonl(jdir / "final.jsonl")}
    sa = jdir / "stage-a"
    sa.mkdir(exist_ok=True)
    hits = []
    for c, g, r, rep_ in ar.judging_legs("A"):
        rp, _ = ar.leg_files(world.runs, c, g, r, rep_)
        for row in asc.read_jsonl(rp):
            if row["condition"] != asc.RETRIEVERS[r]:
                continue
            rec = world.by_id[row["record_id"]]
            leg = asc.leg_id(c, g, r, rep_)
            if row.get("failed"):
                hits.append({"leg": leg, "record_id": rec["record_id"], "failed": True, "hit": None, "hit_gold": None})
                continue
            top = row["ranked_chunk_ids"][:10]
            gold = rec["gold_chunks"][0] in top
            judged = any((fin.get((rec["record_id"], x)) or {}).get("label") == 1 for x in top)
            hits.append({"leg": leg, "record_id": rec["record_id"], "failed": False,
                         "hit": int(gold or judged), "hit_gold": int(gold)})
    fx.write_jsonl(sa / "hits.jsonl", hits)
    for c, g, r, rep_ in ar.stage_legs("A"):
        _, sp = ar.leg_files(world.runs, c, g, r, rep_)
        (sa / "summaries" / c).mkdir(parents=True, exist_ok=True)
        (sa / "summaries" / c / sp.name).write_bytes(sp.read_bytes())
    if judge_summary is not None:
        for d in (jdir, sa):
            (d / "judge-summary.json").write_text(json.dumps(judge_summary))
    (jdir / "batches.json").write_text(json.dumps({"batches": []}))
    pub = jdir / "stage-a-commit.public.json"
    files = [aj.file_entry("stage_a_hits", sa / "hits.jsonl", "exact"),
             aj.file_entry("final", jdir / "final.jsonl", "prefix"), aj.file_entry("pairs", jdir / "pairs.jsonl", "prefix")]
    cpath = jdir / "stage-a-commit.json"
    cpath.write_text(json.dumps({"schema": aj.COMMIT_SCHEMA, "mode": mode, "files": files,
                                 "batches_file": str(jdir / "batches.json"), "batch_numbers_A": [], "batches_A": [],
                                 "public_file": str(pub)}))
    sha = asc.sha256_file(cpath)
    pub.write_text(json.dumps({"commit_sha256": sha}))
    (world.runs / ar.COMMIT_MARKER).write_text(json.dumps({"commit": str(cpath), "commit_sha256": sha,
                                                           "public": str(pub)}))
    return jdir


def committed_world(tmp_path: Path, label: Any = None, patch: Any = None, inject: bool = True, **kw: Any) -> fx.World:
    """The e2e world: H1's base x none x default G records lose their gold (an unlabelled P99-c0 in
    its place), every other leg holds the gold at rank 1; final labels; stage A committed."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    w = fx.World(tmp_path)

    def top(leg: str, rec: dict[str, Any]) -> list[str]:
        t = w.default_top10(leg, rec)
        if inject and leg == st.L("base", "none", "default") and rec["kind"] == "G":
            t = [x for x in t if x != rec["gold_chunks"][0]] + ["P99-c0"]
        return t

    w.write_runs(top10=top, mode=kw.get("runs_mode") or kw.get("mode") or "dry_run")
    if patch:
        patch(w)
    write_final(w, label or (lambda rec, cid: 0))
    commit_stage_a(w, **kw)
    return w


def e2e_config(w: fx.World, tmp_path: Path, **extra: Any) -> Path:
    n_g = sum(1 for r in asc.split_records(w.records)[0] if r["kind"] == "G")
    cov = {p: {"topic": f"T{int(p[1:]) % 3}", "citation_count": int(p[1:]) * 10, "text_similarity": 0.1 * int(p[1:]),
               "xml_only": int(p[1:]) % 2 == 0, "added_chunks": {"neighbour": 30 + int(p[1:]), "random1": 30, "random2": 31}}
           for p in w.papers if p.startswith("P")}
    (tmp_path / "cov.json").write_text(json.dumps(cov))
    (tmp_path / "cuts.json").write_text(json.dumps({"citation_count": [30, 70], "text_similarity": [0.3, 0.7]}))
    (tmp_path / "attr.json").write_text(json.dumps({"N00": "P00", "N01": "P01"}))
    cfg = {"records": str(w.records_path), "runs_dir": str(w.runs), "judge_dir": str(tmp_path / "judge"),
           "reach": {"count": n_g, "of": n_g}, "dry_run": True, "n_perm": 2000, "n_boot": 300,
           "paper_covariates": str(tmp_path / "cov.json"), "tercile_cuts": str(tmp_path / "cuts.json"),
           "neighbour_attribution": str(tmp_path / "attr.json"), **extra}
    cp = tmp_path / "cfg.json"
    cp.write_text(json.dumps(cfg))
    return cp


FIELD_COV = {"merged": {"available": True, "share_with_field": 0.18}, "placebo": {"available": True, "share_with_field": 0.05}}


def _e2e_patch(w: fx.World) -> None:
    def study(fc: dict[str, Any], extra: dict[str, Any] | None = None) -> Any:
        def f(s: dict[str, Any]) -> None:
            s["study"] = {"mode": "dry_run", "field_coverage": fc, **(extra or {})}
            s["census"] = {"source": "load_receipt", "collection_build": {"collection": "bench-x", "n_chunks": 52}}
        return f
    patch_summary(w, "base/merged", study(FIELD_COV["merged"]))
    patch_summary(w, "base/placebo", study(FIELD_COV["placebo"], {
        "stage": "B", "stage_a_commit": {"verified": True},
        "placebo_glossary": {"counts": {"placebo_dropped_own_entries": 7}}}))


def test_public_copy_strips_host_and_public_problems_refuses_one() -> None:
    res = {"provenance": {"host": "box-1", "warnings": ["w"], "commit": "c" * 40, "dirty": False},
           "runs": {"base/none": {"provenance": {"host": "box-2", "commit": "d"}}}, "n": 1}
    pub = st.public_copy(res)
    assert pub["provenance"] == {"commit": "c" * 40, "dirty": False} and pub["runs"]["base/none"]["provenance"] == {"commit": "d"}
    assert res["provenance"]["host"] == "box-1"                            # the private copy keeps it
    assert asc.public_problems(pub, []) == []
    assert asc.public_problems({"a": [{"host": None}]}, []) == ["a host field"]
    assert asc.public_problems({"Hostname": "x"}, []) == ["a Hostname field"]


def test_analyse_end_to_end_and_public_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import ragstack.provenance as rp

    monkeypatch.setattr(rp, "experiment_provenance", lambda **kw: {"host": "private-box", "warnings": ["x"],
                                                                   "commit": "e" * 40, "dirty": False, "untracked": 0})
    w = committed_world(tmp_path, patch=_e2e_patch)
    cp = e2e_config(w, tmp_path)
    assert st.main(["--config", str(cp), "--out", str(tmp_path / "res.json"), "--public", str(tmp_path / "pub.json")]) == 0
    res = json.loads((tmp_path / "res.json").read_text())
    assert res["schema"] == "alias-study-results/2"
    pub = json.loads((tmp_path / "pub.json").read_text())
    assert res["provenance"]["host"] == "private-box" and "host" not in pub["provenance"]
    assert "warnings" not in pub["provenance"] and asc.host_keys(pub) == set()
    l1 = res["layer1"]
    assert l1["H1"]["status"] == st.CONFIRMED and l1["H1"]["net_points"] == 100.0 and l1["H1"]["level"] == 0.05
    assert l1["H1"]["c_gate"]["verdict"] == "passes" and l1["H1b"]["tested"] is True
    assert l1["H2"]["status"] == GATE_OR_NOT(res)
    assert "holm_drop_gated" not in l1 and l1["families"]["N"]["alpha"] == 0.05
    assert l1["H2"]["judge_false_yes_bound"]["rule"] == "U = max(U_random, U_pooled)"
    assert l1["H1"]["fragile"]["considered"] == ["bootstrap_t", "topic_clustering"]
    assert l1["H1b"]["fragile"]["fragile"] is False
    dec = res["layer2"]["decisions"]["attach_merged_glossary"]
    assert dec["reading"] == "attach" and dec["delta_c"] == 0.0
    assert res["layer2"]["serving_statements"]["glossary"]["statement"].startswith("attach, for questions")
    assert res["layer2"]["serving_statements"]["glossary"]["statement"].endswith(
        "smaller than about 3.6 points is not ruled out (12 C records)")
    # every input is hashed
    inp = res["inputs"]
    jdir = tmp_path / "judge"
    assert inp["judge"]["final.jsonl"] == asc.sha256_file(jdir / "final.jsonl")
    assert inp["judge"]["pairs.jsonl"] == asc.sha256_file(jdir / "pairs.jsonl") and inp["judge"]["judge-summary.json"] is None
    assert len(inp["runs"]) == 13 and all(v["rows_sha256"] and v["summary_sha256"] for v in inp["runs"].values())
    assert inp["runs"]["base/placebo"]["rows_sha256"] == asc.sha256_file(w.runs / "base" / "placebo.jsonl")
    assert inp["runs"]["base/merged"]["collection_build_sha256"] == asc.sha_text(
        json.dumps({"collection": "bench-x", "n_chunks": 52}, sort_keys=True, separators=(",", ":")))
    assert inp["runs"]["base/merged"]["census_source"] == "load_receipt"
    assert inp["stage_a"]["stage_a_commit_sha256"] == res["study"]["gate"]["stage_a_commit_sha256"] is not None
    assert inp["stage_a"]["hits.jsonl"] == asc.sha256_file(jdir / "stage-a" / "hits.jsonl")
    assert set(inp["files"]) >= {"paper_covariates", "tercile_cuts", "neighbour_attribution", "power_artifact"}
    assert inp["records"] == asc.sha256_file(w.records_path) and inp["config_canonical_sha256"]
    # the mode, the gate, the stage: recorded; Layer 1 cross-checked against the frozen stage-A hits
    assert res["study"]["mode"] == "dry_run" and res["study"]["spot_checks_waiver"] == "dry run"
    assert res["analysis"]["stage"] == "B" and res["analysis"]["needs"] == ["found10", "rr10"]
    assert res["frozen_stage_a"]["agree"] is True and res["frozen_stage_a"]["legs"] == 14
    assert len(res["legs_read"]) == 29 and res["legs_read"][st.PLACEBO_LEG] == len(w.records)
    assert res["runs"]["base/placebo"]["stage_a_commit_verified"] is True
    # the placebo leg, read through the 29-leg list, with the summaries' field coverage beside it
    plc = res["layer2"]["placebo"]
    assert plc["G"]["placebo_minus_none"]["net_points"] == 100.0
    assert plc["field_coverage"] == FIELD_COV and plc["placebo_dropped_own_entries"] == 7
    fs = res["fixed_sensitivities"]
    assert fs["H1"]["bootstrap_t"]["verdict"] == st.CONFIRMED and fs["H1"]["bootstrap_t"]["n_boot"] == 300
    assert "verdict" not in fs["H1"]["studentized_identity_check"] and fs["H1"]["topic_clustering"]["clusters"] == 3
    assert fs["H2"]["volume_covariate"]["p_method"].startswith("monte_carlo (Freedman–Lane")
    assert fs["H2"]["xml_only_stratum"]["papers"] == 6 and fs["H2"]["terciles"]["citation_count"]["T1"]["papers"] == 4
    assert isinstance(fs["H2"]["volume_covariate"], dict) and fs["inputs"]["tercile_cuts"]["sha256"]
    # the injected P99-c0 has no final label: counted (a dry run), refused otherwise (review of #904)
    assert res["label_coverage"]["pairs_without_final_label"] == sum(r["kind"] == "G" for r in w.records)
    assert res["citable"] is False and res["dry_run"] is True
    assert res["layer2"]["cells"][st.L("base", "none", "default")]["descriptive_leg"]["found10"]["n"] == 2
    assert res["sensitivities"]["without_codex_verdicts"]["records"] < len(asc.split_records(w.records)[0])
    # the public check refuses a record id smuggled into the results
    assert asc.public_problems(pub, {r["record_id"] for r in w.records}) == []
    assert asc.public_problems(res, []) == ["a host field"]                 # the private copy is not publishable
    assert asc.public_problems({"x": w.records[0]["record_id"]}, {w.records[0]["record_id"]}) == ["an id"]
    assert asc.public_problems({"x": "…" + w.records[0]["question"][:45]}, [], [w.records[0]["question"]])


def test_fragile_annotates_and_never_changes_the_label(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A bootstrap-t re-test that disagrees makes the confirmed H1 fragile; H1 stays confirmed."""
    w = committed_world(tmp_path)
    cfg = json.loads(e2e_config(w, tmp_path).read_text())
    monkeypatch.setattr(st, "bootstrap_t",
                        lambda s, alt, b, seed: {"p": 0.5, "p_method": "stub", "n_boot": b, "min_attainable_p": 1 / (b + 1)})
    res = st.analyse(cfg)
    assert res["layer1"]["H1"]["status"] == st.CONFIRMED
    assert res["layer1"]["H1"]["fragile"] == {"considered": ["bootstrap_t", "topic_clustering"],
                                              "flipped_by": ["bootstrap_t"], "fragile": True}


def test_label_coverage_reads_the_demanded_pairs_only() -> None:
    """found@10 demands the top 10 only when the gold is outside it; RR@10 the chunks above the
    gold. A chunk ranked below the gold is never judged, so never missing."""
    rec = {"record_id": "r1", "gold_chunks": ["g"]}
    rows = {"base|none|default": [{"record_id": "r1", "ranked_chunk_ids": ["a", "g", "b"]}],   # b below gold
            "base|none|bm25": [{"record_id": "r1", "ranked_chunk_ids": ["b", "c"]},
                               {"record_id": "r1", "failed": True, "ranked_chunk_ids": None}]}
    cov = st.label_coverage(rows, {"r1": rec}, {})
    assert cov["pairs_without_final_label"] == 3 and cov["instances_per_leg"] == {"base|none|default": 1,
                                                                                  "base|none|bm25": 2}
    f10_only = st.label_coverage(rows, {"r1": rec}, {}, needs=("found10",))
    assert f10_only["instances_per_leg"] == {"base|none|bm25": 2}                  # "a" is an RR@10 pair
    full = {("r1", "a"): {}, ("r1", "b"): {}, ("r1", "c"): {}}
    assert st.label_coverage(rows, {"r1": rec}, full)["pairs_without_final_label"] == 0


def test_coverage_scope_after_stage_a_and_after_stage_b() -> None:
    """After stage A only the judging-stage-A legs' found@10 pairs must be labelled: an unlabelled
    bm25 pair (judged in stage B) is not a hole then, and is one after stage B."""
    legs_a, needs_a = st.coverage_scope("A")
    legs_b, needs_b = st.coverage_scope("B")
    assert len(legs_a) == 14 and needs_a == ("found10",) and st.L("base", "none", "bm25") not in legs_a
    assert len(legs_b) == 29 and needs_b == ("found10", "rr10")
    rec = {"record_id": "r1", "gold_chunks": ["g"]}
    rows = {st.L("base", "none", "bm25"): [{"record_id": "r1", "ranked_chunk_ids": ["x"]}],
            st.L("base", "none", "default"): [{"record_id": "r1", "ranked_chunk_ids": ["y", "g"]}]}
    assert st.label_coverage(rows, {"r1": rec}, {}, legs_a, needs_a)["pairs_without_final_label"] == 0
    assert st.label_coverage(rows, {"r1": rec}, {}, legs_b, needs_b)["pairs_without_final_label"] == 2


def test_stage_a_analysis_reads_layer1_only(tmp_path: Path) -> None:
    w = committed_world(tmp_path)
    res = st.analyse(json.loads(e2e_config(w, tmp_path, analysis_stage="A").read_text()))
    assert res["analysis"]["stage"] == "A" and res["analysis"]["coverage_legs"] == 14
    assert res["layer2"].startswith("absent (analysis stage A") and res["layer1"]["H1"]["status"] == st.CONFIRMED
    with pytest.raises(SystemExit, match="analysis_stage"):
        st.study_preflight(json.loads(e2e_config(w, tmp_path, analysis_stage="C").read_text()))


def test_the_gate_runs_before_anything_is_read(tmp_path: Path) -> None:
    """No marker: refused by the analysis gate, before the (here missing) record file is read."""
    w = fx.World(tmp_path)
    w.write_runs()
    write_final(w, lambda rec, cid: 0)
    import alias_study_run as ar

    for d in (w.runs, tmp_path / "judge"):
        (d / ar.MODE_MARKER).write_text("dry_run\n")
    cfg = json.loads(e2e_config(w, tmp_path).read_text())
    cfg["records"] = str(tmp_path / "no-such-records.jsonl")
    with pytest.raises(SystemExit, match="no analysis before stage A is committed"):
        st.Data(cfg)


def test_retired_config_keys_are_refused(tmp_path: Path) -> None:
    w = committed_world(tmp_path)
    for k in ("spot_records", "spot_judge", "field_coverage"):
        cfg = json.loads(e2e_config(w, tmp_path, **{k: "x"}).read_text())
        with pytest.raises(SystemExit, match="retired"):
            st.study_preflight(cfg)


def test_modes_must_agree(tmp_path: Path) -> None:
    w = committed_world(tmp_path / "a", runs_mode="pilot")
    with pytest.raises(SystemExit, match="runs_dir/STUDY-MODE is 'pilot'"):
        st.study_preflight(json.loads(e2e_config(w, tmp_path / "a").read_text()))
    w2 = committed_world(tmp_path / "b", judge_mode="pilot")
    with pytest.raises(SystemExit, match="judge_dir/STUDY-MODE is 'pilot'"):
        st.study_preflight(json.loads(e2e_config(w2, tmp_path / "b").read_text()))
    w3 = committed_world(tmp_path / "c", patch=lambda w: patch_summary(
        w, "neighbour/none", lambda s: s.update(study={"mode": "pilot"})))
    with pytest.raises(SystemExit, match="measured in mode 'pilot'"):
        st.Data(json.loads(e2e_config(w3, tmp_path / "c").read_text()))


def test_main_run_refuses_a_u_cross_check_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Outside a dry run the frozen stage-A summary's false_yes_among_answers must equal the stage-A
    final rows; here the summary claims 5 Opus-answered *answers* pairs the rows do not hold."""
    import alias_study_judge as aj

    monkeypatch.setattr(aj, "public_tracked_at_head", lambda path, repo=None: True)
    monkeypatch.setattr(asc, "verify_record_set", lambda path, recs: {"stub": True})
    js = {"drift": {"scopes": {sc: {"false_yes_among_answers": {"k": 1, "n": 5}}
                               for sc in ("overall", "base", "neighbour", "random")}}}
    w = committed_world(tmp_path, inject=False, mode="main", judge_summary=js)
    (tmp_path / "rec.json").write_text(json.dumps({"valid": 38, "n": 40, "sheet_sha256": "ab" * 32}))
    fx.write_jsonl(tmp_path / "judge-spot.jsonl", [])
    cfg = json.loads(e2e_config(w, tmp_path, dry_run=False, analysis_stage="A", n_perm=10_000, n_boot=10_000,
                                spot_checks={"records": str(tmp_path / "rec.json"),
                                             "judge": str(tmp_path / "judge-spot.jsonl")}).read_text())
    with pytest.raises(SystemExit, match="differs from the stage-A final rows"):
        st.analyse(cfg)


def test_commit_mode_must_be_the_stats_mode(tmp_path: Path) -> None:
    """Markers that agree with the config do not cover a commit made in another mode."""
    w = committed_world(tmp_path, mode="pilot", runs_mode="dry_run", judge_mode="dry_run")
    with pytest.raises(SystemExit, match="committed in mode 'pilot'"):
        st.study_preflight(json.loads(e2e_config(w, tmp_path).read_text()))


def test_judge_dir_must_be_the_committed_one(tmp_path: Path) -> None:
    import shutil

    w = committed_world(tmp_path)
    other = tmp_path / "judge-copy"
    shutil.copytree(tmp_path / "judge", other)
    cfg = json.loads(e2e_config(w, tmp_path, judge_dir=str(other)).read_text())
    with pytest.raises(SystemExit, match="not the judge directory stage A was committed from"):
        st.study_preflight(cfg)
    assert st.study_preflight(json.loads(e2e_config(w, tmp_path).read_text()))["stage_a_commit"]["verified"]


def test_pilot_and_main_spot_check_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The statistics refuse a pilot directory outright (R28: the pilot's only reader is
    alias_study_pilot.py), though the analysis gate itself waives the spot checks there; the main
    run refuses without them and records their sha256 with them."""
    import alias_study_judge as aj

    wp = committed_world(tmp_path / "p", mode="pilot")
    with pytest.raises(SystemExit, match="alias_study_pilot.py"):
        st.study_preflight(json.loads(e2e_config(wp, tmp_path / "p", dry_run=False, pilot=True).read_text()))
    assert aj.analysis_gate(wp.runs)["spot_checks_waiver"] == "pilot (descriptive, §5.6)"
    monkeypatch.setattr(aj, "public_tracked_at_head", lambda path, repo=None: True)
    wm = committed_world(tmp_path / "m", mode="main")
    cfg = json.loads(e2e_config(wm, tmp_path / "m", dry_run=False).read_text())
    with pytest.raises(SystemExit, match="spot check"):
        st.study_preflight(cfg)
    (tmp_path / "rec.json").write_text(json.dumps({"valid": 38, "n": 40, "sheet_sha256": "ab" * 32}))
    fx.write_jsonl(tmp_path / "judge-spot.jsonl", [])
    cfg["spot_checks"] = {"records": str(tmp_path / "rec.json"), "judge": str(tmp_path / "judge-spot.jsonl")}
    pre = st.study_preflight(cfg)
    assert pre["spot_checks_waiver"] is None and pre["gate"]["spot_checks_sha256"]["records"]
    assert pre["stage_a_commit"]["public_tracked_at_head"] is True


def test_frozen_stage_a_cross_check(tmp_path: Path) -> None:
    """A hit that differs from stage-a/hits.jsonl, a summary that differs from its frozen copy,
    or a leg missing from the hits: refused."""
    w = committed_world(tmp_path)
    cfg = json.loads(e2e_config(w, tmp_path).read_text())
    d = st.Data(cfg)
    prim = d.outcomes("primary")
    jdir = tmp_path / "judge"
    assert st.check_frozen_stage_a(jdir, w.runs, prim, d.by_id)["agree"]
    hp = jdir / "stage-a" / "hits.jsonl"
    orig = hp.read_text()
    rows = asc.read_jsonl(hp)
    rows[0]["hit"] = 1 - int(rows[0]["hit"])
    fx.write_jsonl(hp, rows)
    with pytest.raises(SystemExit, match="differ from the frozen"):
        st.check_frozen_stage_a(jdir, w.runs, prim, d.by_id)
    fx.write_jsonl(hp, [r for r in asc.read_jsonl(jdir / "stage-a" / "hits.jsonl") if r["leg"] != st.L("random2", "none", "default")])
    with pytest.raises(SystemExit, match="judging-stage-A legs"):
        st.check_frozen_stage_a(jdir, w.runs, prim, d.by_id)
    hp.write_text(orig)
    cp = jdir / "stage-a" / "summaries" / "neighbour" / "none.summary.json"
    cp.write_text(cp.read_text() + " ")
    with pytest.raises(SystemExit, match="frozen copies"):
        st.check_frozen_stage_a(jdir, w.runs, prim, d.by_id)


def test_inactive_reranker_gates_the_default_leg(tmp_path: Path) -> None:
    """found10's identical_to_norerank on every record gates the default leg (H1), not norerank."""
    def same(s: dict[str, Any]) -> None:
        s["conditions"]["base"] = {"gate": None, "identical_to_norerank": {"records": 26, "identical": 26}}
    w = committed_world(tmp_path, patch=lambda w: patch_summary(w, "base/merged", same))
    d = st.Data(json.loads(e2e_config(w, tmp_path).read_text()))
    assert d.gates[st.L("base", "merged", "default")] == st.INACTIVE_RERANKER
    assert d.gates[st.L("base", "merged", "norerank")] is None
    assert not st._identical_to_norerank({"records": 26, "identical": 25}) and st._identical_to_norerank(True)
    res = st.layer1(d.bal, d.outcomes(), d.gates, {"count": 24, "of": 24}, AN)
    assert res["H1"]["status"] == st.GATE and any("inactive reranker" in g for g in res["H1"]["gate_reasons"])


def test_false_yes_from_the_judge_summary_and_filters() -> None:
    js = {"drift": {"scopes": {"overall": {"false_yes_among_answers": {"k": 3, "n": 20}},
                               "random": {"false_yes_among_answers": {"k": 3, "n": 10}},
                               "base": {"false_yes_among_answers": {"k": 0, "n": 10}}}}}
    c = st.false_yes_from_summary(js)
    assert (c["pooled"]["k"], c["pooled"]["n"]) == (3, 20) and c["random"]["wilson95"] == [0.1077, 0.6033]
    assert st.false_yes_from_summary({"drift": {"scopes": {"overall": {}}}}) is None
    rows = frows([("random", 1, 0, 3)])
    rows[100] = {"arm": "random", "label": 1, "opus": 0, "opus_status": "no_reference", "drift_sampled": True}
    rows[101] = {"arm": "random", "label": 1, "opus": 0, "drift_sampled": True, "stage": "B"}
    assert st.false_yes_counts(rows)["random"]["n"] == 4
    assert st.false_yes_counts(rows, stage="A")["random"]["n"] == 3          # stage-B rows left out
    # U reads the frozen stage-A summary when it has the counts; the rows are its cross-check
    cnt, src, same = st.false_yes_inputs(js, rows, strict=False)
    assert cnt["random"]["n"] == 10 and src.startswith("stage-a/judge-summary.json") and same is False
    cnt2, src2, same2 = st.false_yes_inputs({}, rows, strict=False)          # a dry run only: the rows
    assert cnt2["random"]["n"] == 3 and src2.startswith("final.jsonl") and same2 is None
    with pytest.raises(SystemExit, match="carries no false_yes_among_answers"):
        st.false_yes_inputs({}, rows)                                         # outside a dry run: refused (§3.4, P2)
    with pytest.raises(SystemExit, match="carries no false_yes_among_answers"):
        st.false_yes_inputs({"drift": {"scopes": {"overall": {}}}}, rows, strict=True)
    with pytest.raises(SystemExit, match="differs from the stage-A final rows"):
        st.false_yes_inputs(js, rows, strict=True)                          # outside a dry run: refused
    assert st.false_yes_inputs(js, rows, strict=False)[2] is False          # a dry run records it


def test_agreement_metrics_by_hand() -> None:
    m6 = st.agreement_metrics(tp=40, fp=10, fn=5, tn=25)
    assert m6["precision"] == 0.8 and m6["recall"] == pytest.approx(40 / 45) and m6["specificity"] == pytest.approx(25 / 35)
    po, pe = 65 / 80, (50 * 45 + 30 * 35) / 80 ** 2
    assert m6["kappa"] == pytest.approx((po - pe) / (1 - pe)) and m6["f1"] == pytest.approx(2 * 0.8 * (40 / 45) / (0.8 + 40 / 45))
    pi = (50 / 80 + 45 / 80) / 2
    assert m6["gwet_ac1"] == pytest.approx((po - 2 * pi * (1 - pi)) / (1 - 2 * pi * (1 - pi)))


def test_judge_spot_check_r15(tmp_path: Path) -> None:
    """Strata from the final label (answers / not where C and E agreed, tiebroken where Argon
    decided), agreement re-weighted to the stage-A label shares, and the < 0.90 rule on the
    re-weighted agreement or the answers stratum."""
    d = st.Data.__new__(st.Data)
    fin = {}
    for i in range(60):                                 # the 60 sheet pairs: 40 not / 15 answers / 5 tiebroken, base
        src, lab = ("agree", 0) if i < 40 else (("agree", 1) if i < 55 else (("tiebreak", 1) if i < 58
                                                                             else ("tiebreak_r2", 1)))
        fin[("r", f"c{i}")] = {"pair_id": f"p{i}", "arm": "base", "source": src, "label": lab, "stage": "A"}
    for i in range(360):                                # stage A holds many more *not* labels than the sheet
        fin[("r", f"n{i}")] = {"pair_id": f"n{i}", "arm": "base", "source": "agree", "label": 0, "stage": "A"}
    fin[("r", "b1")] = {"pair_id": "b1", "arm": "base", "source": "agree", "label": 0, "stage": "B"}   # not stage A
    fin[("r", "u")] = {"pair_id": "pu", "arm": "base", "source": "unjudged", "label": None, "stage": "A"}
    d.final = fin
    spot = [{"pair_id": f"p{i}", "owner_label": (0 if i < 40 else 1)} for i in range(60)]
    spot[40]["owner_label"] = 0                         # one answers pair the owner calls not
    spot.append({"pair_id": "pu", "owner_label": 1})
    fx.write_jsonl(tmp_path / "spot.jsonl", spot)
    (tmp_path / "rec.json").write_text(json.dumps({"valid": 33, "n": 40, "sheet_sha256": "cd" * 32}))
    out = st.spot_flags({"spot_checks": {"records": str(tmp_path / "rec.json"), "judge": str(tmp_path / "spot.jsonl")}}, d)
    rv = out["record_validity"]
    assert rv["rate"] == 33 / 40 and rv["below_threshold"] is True                 # 0.825 < 0.85
    assert rv["sheet_sha256"] == "cd" * 32
    jv = out["judge_validity"]
    assert jv["strata"]["answers"] == {"agree": 14, "n": 15, "rate": 14 / 15, "wilson95": st._wilson(14, 15)}
    assert jv["unjudged"] == 1 and jv["n"] == 60
    # the first version's re-issue source (tiebreak_r2) is tiebroken too: 3 tiebreak + 2 tiebreak_r2
    assert jv["strata"]["tiebroken"]["n"] == 5
    assert jv["reweighted_agreement"] == pytest.approx((400 * 1 + 15 * 14 / 15 + 5 * 1) / 420)
    assert jv["reweighted_agreement"] != pytest.approx(59 / 60)                   # not the raw agreement
    assert jv["below_threshold"] is False                                         # 0.933 and 0.983
    spot[41]["owner_label"] = 0                         # answers stratum now 13/15 = 0.867 < 0.90
    fx.write_jsonl(tmp_path / "spot.jsonl", spot)
    out2 = st.spot_flags({"spot_checks": {"judge": str(tmp_path / "spot.jsonl")}}, d)
    assert out2["judge_validity"]["below_threshold"] is True
    assert out2["record_validity"].startswith("absent")


def test_spot_check_files_are_enforced(tmp_path: Path) -> None:
    """§13.4: n = 40 and the sheet's sha256; §13.5: every judge pair labelled in stage A."""
    d = st.Data.__new__(st.Data)
    d.final = {("r", "a"): {"pair_id": "pa", "arm": "base", "source": "agree", "label": 1, "stage": "A"},
               ("r", "b"): {"pair_id": "pb", "arm": "base", "source": "agree", "label": 1, "stage": "B"}}
    rec = tmp_path / "rec.json"
    for bad, msg in (({"valid": 30, "n": 39, "sheet_sha256": "ab" * 32}, "not the 40 records"),
                     ({"valid": 30, "n": 40}, "sheet_sha256"), ({"valid": 30, "n": 40, "sheet_sha256": "xyz"}, "sheet_sha256"),
                     ({"valid": 41, "n": 40, "sheet_sha256": "ab" * 32}, "valid = 41")):
        rec.write_text(json.dumps(bad))
        with pytest.raises(SystemExit, match=msg):
            st.spot_flags({"spot_checks": {"records": str(rec)}}, d)
    fx.write_jsonl(tmp_path / "j.jsonl", [{"pair_id": "pa", "owner_label": 1}, {"pair_id": "pb", "owner_label": 1}])
    with pytest.raises(SystemExit, match="from stage B"):
        st.spot_flags({"spot_checks": {"judge": str(tmp_path / "j.jsonl")}}, d)
    fx.write_jsonl(tmp_path / "j.jsonl", [{"pair_id": "pa", "owner_label": 1}])
    assert st.spot_flags({"spot_checks": {"judge": str(tmp_path / "j.jsonl")}}, d)["judge_validity"]["n"] == 1


def test_citable_needs_every_check() -> None:
    ok = {"mode": "main", "runs_citable": True, "prereg_verified": True, "stage_b_runs_commit_verified": True,
          "stage_a_commit": {"public_tracked_at_head": True}}
    clean = {"dirty": False, "untracked": 0}
    assert st.is_citable(ok, clean, False)
    assert not st.is_citable(ok, clean, True)
    assert not st.is_citable(ok, {"dirty": True, "untracked": 0}, False)
    for k, v in (("mode", "pilot"), ("runs_citable", False), ("prereg_verified", False),
                 ("stage_b_runs_commit_verified", False), ("stage_a_commit", {"public_tracked_at_head": None})):
        assert not st.is_citable({**ok, k: v}, clean, False), k


def GATE_OR_NOT(res: dict[str, Any]) -> str:  # noqa: N802
    """No neighbour effect in this world: every paper sum is zero, so H2 is GATE (k = 0)."""
    return st.GATE if res["layer1"]["H2"]["n_nonzero"] == 0 else st.NOT_CONFIRMED


def test_record_set_identity_is_enforced(tmp_path: Path) -> None:
    w = fx.World(tmp_path)
    with pytest.raises(SystemExit, match="not the frozen one"):
        asc.verify_record_set(w.records_path, w.records)


def test_seed_rule_is_fixed() -> None:
    assert asc.seed_int("261101|alias|perm") == asc.seed_int(asc.SEEDS["perm"])
    assert asc.seed_int(asc.SEEDS["perm"]) != asc.seed_int(asc.SEEDS["boot"])
    assert asc.SEEDS["boot"] == "261101|alias|boot" and asc.SEEDS["perm"] == "261101|alias|perm"
    assert asc.seeded_key("s", "a", "b") == asc.sha_text("s|a|b")


PRIVATE = Path("/rag/tmp/alias-study-2026-11/out/main/accepted-alias-O.jsonl")


@pytest.mark.skipif(not PRIVATE.is_file(), reason="the private accepted-record file is not on this host")
def test_the_frozen_record_set_reproduces() -> None:
    """Read only, counts only: the committed rule reproduces §4.1's two shas."""
    recs = asc.load_records(PRIVATE)
    ident = asc.verify_record_set(PRIVATE, recs)
    assert (ident["records"], ident["balanced"], ident["descriptive"]) == (1091, 900, 191)


# ------------------------------------------------------------------ the exact member p (§7)

def test_member_test_uses_the_exact_lattice_p_above_20_papers() -> None:
    """30 papers, scores multiples of 1/2: the p is the exact lattice p (not Monte Carlo), and
    the minimum attainable p is 2^-30, not the Monte-Carlo floor 1/(n_perm + 1) = 1/2001."""
    import chunktrial_stats as cs

    clusters, vals = [], []
    for i in range(30):
        clusters += [f"P{i}", f"P{i}"]
        vals += [1.0, 0.5 if i % 3 else -1.5]
    t = st.member_test(clusters, vals, "greater", AN)
    assert t["p_method"] == "lattice" and t["n_nonzero"] == 30
    _, sums, _ = st.cluster_sums(clusters, vals)
    assert t["p"] == pytest.approx(cs.sign_flip_lattice(sums, "greater")["p"], abs=1e-15)
    assert t["p"] != t["p_perm"]
    assert t["min_attainable_p"] == pytest.approx(2.0 ** -30)
    # all 30 papers positive: p = 2^-30 is reachable, where the round-3 MC p bottoms at 1/2001
    top = st.member_test([f"P{i}" for i in range(30)], [1.0] * 30, "greater", AN)
    assert top["p"] == pytest.approx(2.0 ** -30) and top["p_perm"] == pytest.approx(1 / 2001)


# ------------------------------------------------------------------ H1b / H2: label randomization (§7, 2026-11-02)

def test_h1b_label_supports_by_hand() -> None:
    recs = recs_balanced(2)
    none = lambda r: (0, 0)  # noqa: E731
    merged = lambda r: (1, 1) if r["kind"] == "G" and r["record_id"].endswith("g0") else (0, 0)  # noqa: E731
    o = outcomes(recs, {st.L("base", "none", "default"): none, st.L("base", "merged", "default"): merged})
    cl, v, sup, _ = st.h1b_label_supports(recs, o)
    # D = (1, 0 | C 0): C on the gaining record -> 0 - 1 = -1, else 1/2 - 0
    assert v == [0.5, 0.5] and [sorted(s) for s in sup] == [[-1.0, 0.5, 0.5]] * 2
    # one G record excluded: 1 G + 1 C, two assignments, +-D_G
    gone = next(r for r in recs if r["kind"] == "G" and r["record_id"].endswith("g0"))
    o[st.L("base", "merged", "default")][gone["record_id"]] = None
    cl1, v1, sup1, miss = st.h1b_label_supports(recs, o)
    i = cl1.index(str(gone["paper"]))
    assert miss == 1 and len(sup1[i]) == 2 and sorted(sup1[i]) == sorted([v1[i], -v1[i]])
    assert len(sup1[1 - i]) == 3


def test_exchange_supports_permute_the_three_arm_labels() -> None:
    recs = recs_balanced(1)[:1]
    o = outcomes(recs, {st.L("neighbour", "none", "default"): lambda r: (0, 0),
                        st.L("random1", "none", "default"): lambda r: (1, 1),
                        st.L("random2", "none", "default"): lambda r: (1, 1)})
    (sup,) = st.exchange_supports(recs, o, st.H2_SPEC)
    assert sorted(sup) == [-1.0, -1.0, 0.5, 0.5, 0.5, 0.5]
    two = [(1.0, st.L("neighbour", "none", "default")), (-1.0, st.L("random1", "none", "default"))]
    assert sorted(st.exchange_supports(recs, o, two)[0]) == [-1.0, 1.0]     # two labels: the sign flip


def test_layer1_tests_h1b_by_label_randomization_h1_and_h2_by_sign_flip() -> None:
    import chunktrial_stats as cs

    recs, o = h1_world(30, 30)
    r = st.layer1(recs, o, {}, full_reach(recs), AN)
    assert "sign-flip" in r["H1"]["test"] and "sign-flip" in r["H2"]["test"]
    assert "randomization" in r["H1b"]["test"] and "paper" in r["H1b"]["test"]
    # H1b's p is the G / C label randomization p (H1 confirmed, so H1b is tested)
    clb, vb, supb, _ = st.h1b_label_supports(recs, o)
    wb = cs.cluster_randomization_lattice(supb, sum(vb), "greater")
    assert wb is not None and r["H1b"]["p"] == pytest.approx(wb["p"], abs=1e-15)
    recs2, o2 = h2_world(True)
    r2 = st.layer1(recs2, o2, {}, None, AN)
    cl, v, _ = st.contrast_values(recs2, o2, st.H2_SPEC)
    _, sums, _ = st.cluster_sums(cl, v)
    want = cs.sign_flip_test_exact(sums, alternative="less")
    assert r2["H2"]["p"] == pytest.approx(want["p"], abs=1e-15)
    assert r2["H2"]["min_attainable_p"] == pytest.approx(want["min_attainable_p"])
    # not the arm-label randomization p (decided against for H2: scatter is not a mean drop)
    rand = cs.cluster_randomization_lattice(st.exchange_supports(recs2, o2, st.H2_SPEC), sum(v), "less")
    assert rand is not None and r2["H2"]["p"] != pytest.approx(rand["p"])


def test_randomization_floor_drives_gate_not_evaluable() -> None:
    """H1b's minimum attainable p is its randomization floor: 3 papers each (1, 0 | C 0) have
    floor (2/3)^3 ~ 0.30 > 0.05 under "greater", so H1b is GATE-NOT-EVALUABLE however extreme."""
    t = st.member_randomization(["P1", "P2", "P3"], [0.5, 0.5, 0.5], [[-1.0, 0.5, 0.5]] * 3,
                                "greater", "paper", AN)
    assert t["min_attainable_p"] == pytest.approx((2 / 3) ** 3) and t["p"] == pytest.approx((2 / 3) ** 3)
    r = st.family_g(m(0.001), {**t, "gates": []})
    assert r["H1b"]["status"] == st.GATE
