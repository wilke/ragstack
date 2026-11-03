"""Alias study statistics (prereg v2): the two families (G: H1 then H1b in fixed sequence; N:
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
    assert r["H1b"]["ci95"] is not None and r["H1b"]["power_floor_normal"] is not None


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
    assert st.equivalence(600, 0, [0.0, 0.0])["reading"].startswith("not resolved")


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


def test_reranker_non_inferiority_at_minus_3() -> None:
    rr = lambda net, lo, hi: st.reranker_rule(f(net, lo, hi))["reading"]  # noqa: E731
    assert rr(-1, -2.9, 1) == "keep the default (non-inferior to BM25)"
    assert rr(-1, -3.0, 1) == "not resolved"                                      # D_lo must be > -3
    assert rr(-5, -7, -3.01) == "default inferior to BM25 by more than the margin: reconsider"
    assert rr(-5, -7, -3.0) == "not resolved"                                     # D_hi must be < -3


def test_growth_rule() -> None:
    assert st.growth_rule(f(-3, -5, -0.1))["reading"] == "a concern"
    assert st.growth_rule(f(-3, -5, 0.0))["reading"] == "not resolved"
    assert st.growth_rule(f(-2.9, -4, -1))["reading"] == "not a concern by the rule"


def l1_stub(h1: str, h1b: str = st.NOT_TESTED, h2: str = st.NOT_CONFIRMED, gates: list[str] | None = None
            ) -> dict[str, Any]:
    return {"H1": {"status": h1, "gate_reasons": gates or []}, "H1b": {"status": h1b}, "H2": {"status": h2}}


def decisions(att: str, gro: str = "not a concern by the rule") -> dict[str, Any]:
    return {"attach_merged_glossary": {"reading": att}, "related_paper_growth_concern": {"reading": gro},
            "keep_reranker_default": {"reading": "not resolved"}}


def test_serving_statements_follow_layer1() -> None:
    s = lambda att, l1: st.serving_statements(decisions(att), l1)["glossary"]["statement"]  # noqa: E731
    assert s("attach", l1_stub(st.CONFIRMED, st.CONFIRMED)) == "attach, for questions using the glossary's primary term"
    assert s("attach", l1_stub(st.CONFIRMED, st.NOT_CONFIRMED)).endswith("(alias-specificity unresolved)")
    assert s("attach", l1_stub(st.NOT_CONFIRMED)) == "attach provisionally; the confirmatory claim was not confirmed"
    assert s("attach", l1_stub(st.GATE, gates=["C gate fired (rule C)"])) == \
        "attach provisionally, claim not evaluable (C gate fired (rule C))"
    assert s("harmful, do not attach", l1_stub(st.CONFIRMED)) == "harmful, do not attach"
    assert s("do not attach (C cost)", l1_stub(st.CONFIRMED)).startswith("do not attach: the gain on alias questions")
    assert s("do not attach (C cost)", l1_stub(st.NOT_CONFIRMED)) == "do not attach (C cost)"
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
    assert a["U_random"] == 0.6032 and a["U_pooled"] == 0.3604 and a["U"] == 0.6032 and a["U_from"] == "random"
    assert a["B_points"] == pytest.approx(100 * 0.6032 * 90 / 900) and a["within_bound"] is True
    b = st.false_yes_bound(-3.0, st.false_yes_counts(frows([("random", 1, 1, 40), ("base", 1, 0, 10),
                                                            ("base", 1, 1, 10)])), 90.0, 900)
    assert b["U_random"] == 0.0876 and b["U_pooled"] == 0.2803 and b["U"] == 0.2803 and b["U_from"] == "pooled"
    assert b["B_points"] == pytest.approx(2.803) and b["within_bound"] is False   # |-3| > 2.803
    c = st.false_yes_bound(-1.0, st.false_yes_counts(frows([("base", 1, 0, 3), ("base", 1, 1, 7)])), 90.0, 900)
    assert c["U_random"] is None and c["U"] == c["U_pooled"] == 0.6032
    none = st.false_yes_bound(-1.0, st.false_yes_counts({}), 90.0, 900)
    assert none["B_points"] is None and none["within_bound"] is False


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


def test_topic_clustering_pools_papers() -> None:
    names = [f"P{i}" for i in range(10)]
    sums = np.array([1.0] * 10)
    tsum, k, no_topic = st.topic_sums(names, sums, {p: ("A" if i < 5 else "B") for i, p in enumerate(names[:8])})
    assert k == 4 and no_topic == 2 and sorted(tsum.tolist()) == [1.0, 1.0, 3.0, 5.0]   # untopicked: own clusters


def test_volume_covariate_removes_a_pure_volume_effect() -> None:
    """S_j = -0.1 x_j: the whole H2 effect is added volume; adjusted to x = 0 nothing is left."""
    names = [f"P{i}" for i in range(12)]
    x = {p: float(i - 3) for i, p in enumerate(names)}
    sums = np.array([-0.1 * x[p] for p in names])
    adj, slope, left = st.volume_adjusted(names + ["Pmissing"], np.append(sums, -1.0), x)
    assert slope == pytest.approx(-0.1) and left == 1 and np.allclose(adj, 0.0)
    assert sums.sum() < 0


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
    for name in ("studentized", "topic_clustering"):
        r = st.fragile("H1", st.CONFIRMED, {name: sv(st.NOT_CONFIRMED)})
        assert r["fragile"] and r["flipped_by"] == [name]
    assert st.fragile("H1", st.NOT_CONFIRMED, {"topic_clustering": sv(st.CONFIRMED)})["fragile"]
    assert st.fragile("H2", st.CONFIRMED, {"volume_covariate": sv(st.NOT_CONFIRMED_GOLD)})["fragile"]
    assert st.fragile("H2", st.NOT_CONFIRMED_GOLD, {"studentized": sv(st.CONFIRMED)})["fragile"]


def test_fragile_forbidden_cases() -> None:
    # the volume covariate re-tests H2 only
    assert not st.fragile("H1", st.CONFIRMED, {"volume_covariate": sv(st.NOT_CONFIRMED)})["fragile"]
    # subgroups and declared sensitivities never make anything fragile
    sub = {k: sv(st.NOT_CONFIRMED) for k in ("xml_only_stratum", "terciles", "without_codex_verdicts",
                                             "H2_vs_random1_alone", "unjudged_yes")}
    assert not st.fragile("H2", st.CONFIRMED, sub)["fragile"]
    # H1b is exempt
    hb = st.fragile("H1b", st.CONFIRMED, {"studentized": sv(st.NOT_CONFIRMED)})
    assert not hb["fragile"] and hb["considered"] == []
    # a GATE primary cannot be flipped; a GATE re-test flips nothing; gold vs plain not confirmed is no flip
    assert not st.fragile("H1", st.GATE, {"studentized": sv(st.CONFIRMED)})["fragile"]
    assert not st.fragile("H1", st.CONFIRMED, {"topic_clustering": sv(st.GATE)})["fragile"]
    assert not st.fragile("H2", st.NOT_CONFIRMED, {"studentized": sv(st.NOT_CONFIRMED_GOLD)})["fragile"]


# ------------------------------------------------------------------ outcomes from rows

def test_leg_outcomes_gold_judged_unjudged_and_rr() -> None:
    recs = {r["record_id"]: r for r in fx.make_records(2)}
    rid = "alias-O-P00-g0"
    labels = {(rid, "X1"): None, (rid, "X2"): 1, (rid, "X3"): 1}
    rows = [{"record_id": rid, "ranked_chunk_ids": ["X1", "X2", "P00-c0"], "failed": False}]
    out = st.leg_outcomes(rows, recs, lambda r, c: labels.get((r, c)), {(rid, "X2"): "P07"})
    assert out[rid] == {"hit": 1, "hit_gold": 1, "rr": 0.5, "rr_gold": 1 / 3, "kind": "other"}
    out2 = st.leg_outcomes(rows, recs, lambda r, c: labels.get((r, c)), {(rid, "X2"): "P00"})
    assert out2[rid]["kind"] == "same"
    rows3 = [{"record_id": rid, "ranked_chunk_ids": ["X1"] + [f"Y{i}" for i in range(9)] + ["P00-c0"]}]
    o3 = st.leg_outcomes(rows3, recs, lambda r, c: None, {})
    assert o3[rid]["hit"] == 0 and o3[rid]["hit_gold"] == 0          # gold at rank 11; unjudged = no
    assert st.leg_outcomes([{"record_id": rid, "failed": True}], recs, lambda r, c: 1, {})[rid] is None


def test_labeller_sensitivity_modes() -> None:
    d = st.Data.__new__(st.Data)
    d.final = {("r", "a"): {"source": "unjudged", "label": None, "c_label": 1, "e_label": 0},
               ("r", "b"): {"source": "tiebreak", "label": 1, "c_label": 0, "e_label": 1},
               ("r", "c"): {"source": "agree", "label": 0, "c_label": 0, "e_label": 0}}
    p, u, c, e = (d.labeller(x) for x in ("primary", "unjudged_yes", "tb_as_c", "tb_as_e"))
    assert (p("r", "a"), u("r", "a"), c("r", "a"), e("r", "a")) == (None, 1, 1, 0)
    assert (p("r", "b"), c("r", "b"), e("r", "b")) == (1, 0, 1)
    assert p("r", "c") == u("r", "c") == c("r", "c") == 0


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
                pairs.append({"record_id": rec["record_id"], "chunk_id": cid, "c_paper": cid.split("-")[0],
                              "arm": "base"})
    fx.write_jsonl(jdir / "final.jsonl", rows)
    fx.write_jsonl(jdir / "pairs.jsonl", pairs)


def write_placebo(world: fx.World) -> None:
    d = world.runs / "base"
    rows = [{"record_id": r["record_id"], "condition": "base", "failed": False,
             "ranked_chunk_ids": world.default_top10(st.PLACEBO_LEG, r)} for r in world.records]
    fx.write_jsonl(d / "placebo.jsonl", rows)
    (d / "placebo.summary.json").write_text(json.dumps({"status": "complete", "conditions": {"base": {"gate": None}},
                                                        "provenance": {"commit": "a" * 40}, "citable": True}))


def e2e_config(w: fx.World, tmp_path: Path, **extra: Any) -> Path:
    n_g = sum(1 for r in asc.split_records(w.records)[0] if r["kind"] == "G")
    cov = {p: {"topic": f"T{int(p[1:]) % 3}", "citation_count": int(p[1:]) * 10, "text_similarity": 0.1 * int(p[1:]),
               "xml_only": int(p[1:]) % 2 == 0, "added_chunks": {"neighbour": 30 + int(p[1:]), "random1": 30, "random2": 31}}
           for p in w.papers if p.startswith("P")}
    (tmp_path / "cov.json").write_text(json.dumps(cov))
    (tmp_path / "cuts.json").write_text(json.dumps({"citation_count": [30, 70], "text_similarity": [0.3, 0.7]}))
    (tmp_path / "attr.json").write_text(json.dumps({"N00": "P00", "N01": "P01"}))
    (tmp_path / "coverage.json").write_text(json.dumps({"merged": {"share": 0.18}, "placebo": {"share": 0.05}}))
    cfg = {"records": str(w.records_path), "runs_dir": str(w.runs), "judge_dir": str(tmp_path / "judge"),
           "reach": {"count": n_g, "of": n_g}, "dry_run": True, "n_perm": 2000, "n_boot": 300,
           "paper_covariates": str(tmp_path / "cov.json"), "tercile_cuts": str(tmp_path / "cuts.json"),
           "neighbour_attribution": str(tmp_path / "attr.json"), "field_coverage": str(tmp_path / "coverage.json"),
           **extra}
    cp = tmp_path / "cfg.json"
    cp.write_text(json.dumps(cfg))
    return cp


def test_analyse_end_to_end_and_public_check(tmp_path: Path) -> None:
    w = fx.World(tmp_path)

    def top(leg: str, rec: dict[str, Any]) -> list[str]:
        t = w.default_top10(leg, rec)
        if leg == st.L("base", "none", "default") and rec["kind"] == "G":
            t = [x for x in t if x != rec["gold_chunks"][0]] + ["P99-c0"]
        return t

    w.write_runs(top10=top)
    write_placebo(w)
    write_final(w, lambda rec, cid: 0, extra_legs=(st.PLACEBO_LEG,))
    cp = e2e_config(w, tmp_path)
    assert st.main(["--config", str(cp), "--out", str(tmp_path / "res.json"), "--public", str(tmp_path / "pub.json")]) == 0
    res = json.loads((tmp_path / "res.json").read_text())
    assert res["schema"] == "alias-study-results/2"
    l1 = res["layer1"]
    assert l1["H1"]["status"] == st.CONFIRMED and l1["H1"]["net_points"] == 100.0 and l1["H1"]["level"] == 0.05
    assert l1["H1"]["c_gate"]["verdict"] == "passes" and l1["H1b"]["tested"] is True
    assert l1["H2"]["status"] == GATE_OR_NOT(res)
    assert "holm_drop_gated" not in l1 and l1["families"]["N"]["alpha"] == 0.05
    assert l1["H2"]["judge_false_yes_bound"]["rule"] == "U = max(U_random, U_pooled)"
    assert l1["H1"]["fragile"]["considered"] == ["studentized", "topic_clustering"]
    assert l1["H1b"]["fragile"]["fragile"] is False
    dec = res["layer2"]["decisions"]["attach_merged_glossary"]
    assert dec["reading"] == "attach" and dec["delta_c"] == 0.0
    assert res["layer2"]["serving_statements"]["glossary"]["statement"].startswith("attach, for questions")
    # the placebo leg, read when the runner wrote it, with the field coverage beside it
    plc = res["layer2"]["placebo"]
    assert plc["G"]["placebo_minus_none"]["net_points"] == 100.0 and plc["field_coverage"]["placebo"]["share"] == 0.05
    fs = res["fixed_sensitivities"]
    assert fs["H1"]["studentized"]["verdict"] == st.CONFIRMED and fs["H1"]["topic_clustering"]["clusters"] == 3
    assert fs["H2"]["xml_only_stratum"]["papers"] == 6 and fs["H2"]["terciles"]["citation_count"]["T1"]["papers"] == 4
    assert isinstance(fs["H2"]["volume_covariate"], dict) and fs["inputs"]["tercile_cuts"]["sha256"]
    # the injected P99-c0 has no final label: counted (a dry run), refused otherwise (review of #904)
    assert res["label_coverage"]["pairs_without_final_label"] == sum(r["kind"] == "G" for r in w.records)
    assert res["citable"] is False and res["dry_run"] is True
    assert res["layer2"]["cells"][st.L("base", "none", "default")]["descriptive_leg"]["found10"]["n"] == 2
    assert res["sensitivities"]["without_codex_verdicts"]["records"] < len(asc.split_records(w.records)[0])
    # the public check refuses a record id smuggled into the results
    assert asc.public_problems(res, {r["record_id"] for r in w.records}) == []
    assert asc.public_problems({"x": w.records[0]["record_id"]}, {w.records[0]["record_id"]}) == ["an id"]
    assert asc.public_problems({"x": "…" + w.records[0]["question"][:45]}, [], [w.records[0]["question"]])


def test_fragile_annotates_and_never_changes_the_label(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A studentized re-test that disagrees makes the confirmed H1 fragile; H1 stays confirmed."""
    w = fx.World(tmp_path)

    def top(leg: str, rec: dict[str, Any]) -> list[str]:
        t = w.default_top10(leg, rec)
        if leg == st.L("base", "none", "default") and rec["kind"] == "G":
            t = [x for x in t if x != rec["gold_chunks"][0]] + ["P99-c0"]
        return t

    w.write_runs(top10=top)
    write_final(w, lambda rec, cid: 0)
    cfg = json.loads(e2e_config(w, tmp_path).read_text())
    monkeypatch.setattr(st, "studentized_sign_flip",
                        lambda s, alt, an: {"p": 0.5, "p_method": "stub", "statistic": 0.0, "n_nonzero": 12,
                                            "min_attainable_p": 2.0 ** -12})
    res = st.analyse(cfg)
    assert res["layer1"]["H1"]["status"] == st.CONFIRMED
    assert res["layer1"]["H1"]["fragile"] == {"considered": ["studentized", "topic_clustering"],
                                              "flipped_by": ["studentized"], "fragile": True}


def test_label_coverage_counts_pairs_without_a_final_label() -> None:
    rec = {"record_id": "r1", "gold_chunks": ["g"]}
    rows = {"base|none|default": [{"record_id": "r1", "ranked_chunk_ids": ["g", "a", "b"]}],
            "base|none|bm25": [{"record_id": "r1", "ranked_chunk_ids": ["b", "c"]},
                               {"record_id": "r1", "failed": True, "ranked_chunk_ids": None}]}
    final = {("r1", "a"): {}, ("r1", "b"): {}}
    cov = st.label_coverage(rows, {"r1": rec}, final)
    assert cov["pairs_without_final_label"] == 1 and cov["instances_per_leg"] == {"base|none|bm25": 1}
    assert st.label_coverage(rows, {"r1": rec}, {**final, ("r1", "c"): {}})["pairs_without_final_label"] == 0


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
