"""Alias study statistics: the Option-B procedure, the C gate (rule C), the reach and cluster
gates, H2's gold-only direction gate, the Layer-2 decision rules, the outcomes and the public
check. Each test breaks the rule it guards: the asserted outcome flips if the rule is removed."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

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


# ------------------------------------------------------------------ the procedure

def test_holm_pair_passes_level_and_h1b_follows_both() -> None:
    r = st.layer1_procedure({"H1": m(0.02), "H2": m(0.04), "H1b": m(0.049)})
    assert r["H1"]["status"] == "confirmed" and r["H1"]["level"] == 0.025
    assert r["H2"]["status"] == "confirmed" and r["H2"]["level"] == pytest.approx(0.05)
    assert r["H1b"]["status"] == "confirmed" and r["H1b"]["level"] == 0.05


def test_both_at_003_reject_nothing() -> None:
    """Holm over 2: neither p reaches 0.025, so neither passes a level on (a test of each at
    0.05 would reject both)."""
    r = st.layer1_procedure({"H1": m(0.03), "H2": m(0.03), "H1b": m(0.001)})
    assert r["H1"]["status"] == r["H2"]["status"] == "not confirmed"
    assert r["H1b"]["status"] == "not tested"


def test_h2_gold_gate_failure_passes_no_level() -> None:
    r = st.layer1_procedure({"H1": m(0.04), "H2": m(0.001, gold=False), "H1b": m(0.001)})
    assert r["H2"]["status"] == "not confirmed (gold-only direction gate)" and not r["H2"]["rejected"]
    assert r["H1"]["status"] == "not confirmed" and r["H1"]["level"] == 0.025
    assert r["H1b"]["status"] == "not tested"
    ok = st.layer1_procedure({"H1": m(0.04), "H2": m(0.001, gold=True), "H1b": m(0.001)})
    assert ok["H1"]["status"] == "confirmed" and ok["H1b"]["status"] == "confirmed"


def test_h1b_never_tested_without_h2() -> None:
    r = st.layer1_procedure({"H1": m(0.001), "H2": m(0.2), "H1b": m(1e-9)})
    assert r["H1"]["status"] == "confirmed" and r["H1b"]["status"] == "not tested"
    assert r["H1b"]["rejected"] is False


def test_gated_member_enters_at_p1_and_absent_is_written() -> None:
    r = st.layer1_procedure({"H1": m(0.0001, gates=["C gate fired (rule C)"]), "H2": m(0.03), "H1b": m(0.001)})
    assert r["H1"]["status"] == st.GATE and r["H1"]["p_in_procedure"] == 1.0
    assert r["H2"]["status"] == "not confirmed" and r["H2"]["level"] == 0.025
    a = st.layer1_procedure({"H1": m(None), "H2": m(0.001), "H1b": m(0.001)})
    assert a["H1"]["status"] == "absent" and a["H2"]["status"] == "confirmed" and a["H1b"]["status"] == "not tested"


def test_min_attainable_p_is_judged_at_the_level_reached() -> None:
    """k = 5 non-zero papers: min p 1/32 > 0.025 but <= 0.05 (§8 evaluability floor)."""
    r = st.layer1_procedure({"H1": m(1 / 32, min_p=1 / 32), "H2": m(0.001), "H1b": m(0.2)})
    assert r["H1"]["status"] == "confirmed" and r["H1"]["level"] == pytest.approx(0.05)
    g = st.layer1_procedure({"H1": m(1 / 32, min_p=1 / 32), "H2": m(0.5), "H1b": m(0.2)})
    assert g["H1"]["status"] == st.GATE and "minimum attainable p" in g["H1"]["gate_reasons"][0]


def test_h1b_gate_at_005() -> None:
    r = st.layer1_procedure({"H1": m(0.001), "H2": m(0.001), "H1b": m(0.04, min_p=0.0625)})
    assert r["H1b"]["status"] == st.GATE


def test_holm_drop_gated_sensitivity() -> None:
    r = st.holm_drop_gated({"H1": m(0.04, gates=["x"]), "H2": m(0.04)})
    assert r["family"] == ["H2"] and r["H2"]["rejected"] and r["H2"]["level"] == 0.05


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


def test_c_gate_fires_on_c_rise_keeping_pace() -> None:
    recs = recs_balanced(30)
    none = lambda r: (0, 0)  # noqa: E731
    merged = lambda r: (1, 1) if (r["kind"] == "G" and idx(r) < 20) or (r["kind"] == "C" and idx(r) < 20) else (0, 0)  # noqa: E731
    o = outcomes(recs, {st.L("base", "none", "default"): none, st.L("base", "merged", "default"): merged})
    cg = st.c_gate(recs, o, AN)
    assert cg["verdict"] == "fires" and cg["fires"] and all(cg["conditions"].values())
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


def h1_world(n: int, g_gain: int) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    recs = recs_balanced(n)
    none = lambda r: (0, 0) if r["kind"] == "G" else (1, 1)  # noqa: E731
    merged = lambda r: (1, 1) if (r["kind"] == "C" or idx(r) < g_gain) else (0, 0)  # noqa: E731
    return recs, outcomes(recs, {st.L("base", "none", "default"): none, st.L("base", "merged", "default"): merged})


def test_reach_gate() -> None:
    recs, o = h1_world(30, 30)
    n_g = sum(r["kind"] == "G" for r in recs)
    ok = st.layer1(recs, o, {}, {"count": n_g, "of": n_g}, AN)
    assert ok["H1"]["status"] == "confirmed"
    low = st.layer1(recs, o, {}, {"count": int(0.8 * n_g) - 1, "of": n_g}, AN)
    assert low["H1"]["status"] == st.GATE and any("reach" in x for x in low["H1"]["gate_reasons"])
    absent = st.layer1(recs, o, {}, None, AN)
    assert absent["H1"]["status"] == st.GATE and absent["H1"]["reach"]["note"] == "reach count not supplied"
    assert low["H1b"]["status"] == "not tested"


def test_min_clusters_gate() -> None:
    recs, o = h1_world(7, 7)
    n_g = sum(r["kind"] == "G" for r in recs)
    r = st.layer1(recs, o, {}, {"count": n_g, "of": n_g}, AN)
    assert r["H1"]["status"] == st.GATE and any("papers < 8" in x for x in r["H1"]["gate_reasons"])


def test_cell_gate_from_the_runner() -> None:
    recs, o = h1_world(30, 30)
    n_g = sum(r["kind"] == "G" for r in recs)
    gates = {st.L("base", "merged", "default"): "GATE-NOT-EVALUABLE: the reranker was not active"}
    r = st.layer1(recs, o, gates, {"count": n_g, "of": n_g}, AN)
    assert r["H1"]["status"] == st.GATE
    gates2 = {st.L("base", "merged", "norerank"): "GATE-NOT-EVALUABLE: depth"}
    assert st.layer1(recs, o, gates2, {"count": n_g, "of": n_g}, AN)["H1"]["status"] == "confirmed"


def h2_world(gold_follows: bool) -> list[Any]:
    recs = recs_balanced(30)
    nb = lambda r: ((0, 0 if gold_follows else 1) if idx(r) < 15 else (1, 1))  # noqa: E731
    rnd = lambda r: ((1, 1 if gold_follows else 0) if idx(r) < 15 else (1, 1))  # noqa: E731
    o = outcomes(recs, {st.L("neighbour", "none", "default"): nb, st.L("random1", "none", "default"): rnd,
                        st.L("random2", "none", "default"): rnd})
    return [recs, o]


def test_h2_two_draw_average_and_gold_gate() -> None:
    recs, o = h2_world(True)
    r = st.layer1(recs, o, {}, None, AN)
    assert r["H2"]["status"] == "confirmed" and r["H2"]["net_points"] < 0 and r["H2"]["gold_only_net_points"] < 0
    recs, o = h2_world(False)
    r = st.layer1(recs, o, {}, None, AN)
    assert r["H2"]["p"] <= 0.025 and r["H2"]["status"] == "not confirmed (gold-only direction gate)"


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


# ------------------------------------------------------------------ Layer 2 rules

def f(net: float, lo: float, hi: float) -> dict[str, Any]:
    return {"net_points": net, "ci95": [lo, hi]}


def test_attach_rule() -> None:
    ok = st.decision_rules(f(2.5, 0.5, 4.5), f(0.5, -1, 2), f(0, -1, 1), f(0, -1, 1))["attach_merged_glossary"]
    assert ok["fires"] and ok["reading"] == "attach"
    unres = st.decision_rules(f(2.5, -0.1, 4.5), f(0.5, -1, 2), f(0, -1, 1), f(0, -1, 1))["attach_merged_glossary"]
    assert not unres["fires"] and unres["reading"] == "not resolved"
    small = st.decision_rules(f(1.9, 0.5, 3), f(0.5, -1, 2), f(0, -1, 1), f(0, -1, 1))["attach_merged_glossary"]
    assert not small["fires"]
    costly = st.decision_rules(f(3, 1, 5), f(1.1, 0, 2), f(0, -1, 1), f(0, -1, 1))["attach_merged_glossary"]
    assert not costly["fires"] and not costly["conditions"]["c_cost_le_1"]


def test_reranker_and_growth_rules() -> None:
    r = st.decision_rules(f(0, -1, 1), f(0, -1, 1), f(-3.5, -6, -1), f(-3.2, -5, -0.5))
    assert r["keep_reranker_default"]["keep"] is False and r["related_paper_growth_concern"]["concern"] is True
    r2 = st.decision_rules(f(0, -1, 1), f(0, -1, 1), f(-3.5, -7, 0.5), f(-3.2, -6, 0.1))
    assert r2["keep_reranker_default"]["keep"] and r2["keep_reranker_default"]["reading"] == "keep (not resolved)"
    assert not r2["related_paper_growth_concern"]["concern"] and r2["related_paper_growth_concern"]["reading"] == "not resolved"
    r3 = st.decision_rules(f(0, -1, 1), f(0, -1, 1), f(-2.9, -4, -1), f(-2.9, -4, -1))
    assert r3["keep_reranker_default"]["keep"] and not r3["related_paper_growth_concern"]["concern"]


def test_draw_gap_flag() -> None:
    assert st.within_draw_variation(-1.0, 1.5) and not st.within_draw_variation(-2.0, 1.5)
    assert not st.within_draw_variation(-1.5, 1.5) and not st.within_draw_variation(1.5, -1.5)  # "smaller than"
    assert not st.within_draw_variation(None, 1.0)


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


# ------------------------------------------------------------------ end to end on files

def write_final(world: fx.World, label: Any) -> None:
    jdir = world.tmp / "judge"
    jdir.mkdir(exist_ok=True)
    rows = []
    pairs = []
    for c, g, r, rep in asc.all_legs():
        leg = asc.leg_id(c, g, r, rep)
        for rec in world.records:
            for cid in world.default_top10(leg, rec):
                if cid == rec["gold_chunks"][0]:
                    continue
                rows.append({"k": f"pair|{rec['record_id']}|{cid}", "pair_id": f"{rec['record_id']}|{cid}",
                             "record_id": rec["record_id"], "chunk_id": cid, "arm": "base",
                             "label": label(rec, cid), "source": "agree", "c_label": 0, "e_label": 0})
                pairs.append({"record_id": rec["record_id"], "chunk_id": cid, "c_paper": cid.split("-")[0]})
    fx.write_jsonl(jdir / "final.jsonl", rows)
    fx.write_jsonl(jdir / "pairs.jsonl", pairs)


def test_analyse_end_to_end_and_public_check(tmp_path: Path) -> None:
    w = fx.World(tmp_path)

    def top(leg: str, rec: dict[str, Any]) -> list[str]:
        t = w.default_top10(leg, rec)
        if leg == st.L("base", "none", "default") and rec["kind"] == "G":
            t = [x for x in t if x != rec["gold_chunks"][0]] + ["P99-c0"]
        return t

    w.write_runs(top10=top)
    write_final(w, lambda rec, cid: 0)
    n_g = sum(1 for r in asc.split_records(w.records)[0] if r["kind"] == "G")
    cfg = {"records": str(w.records_path), "runs_dir": str(w.runs), "judge_dir": str(tmp_path / "judge"),
           "reach": {"count": n_g, "of": n_g}, "dry_run": True, "n_perm": 2000, "n_boot": 300}
    cp = tmp_path / "cfg.json"
    cp.write_text(json.dumps(cfg))
    assert st.main(["--config", str(cp), "--out", str(tmp_path / "res.json"), "--public", str(tmp_path / "pub.json")]) == 0
    res = json.loads((tmp_path / "res.json").read_text())
    assert res["layer1"]["H1"]["status"] == "confirmed" and res["layer1"]["H1"]["net_points"] == 100.0
    assert res["layer1"]["H1"]["c_gate"]["verdict"] == "passes"
    assert res["layer1"]["H2"]["status"] == GATE_OR_NOT(res)
    assert res["layer2"]["decisions"]["attach_merged_glossary"]["fires"] is True
    # the injected P99-c0 has no final label: counted (a dry run), refused otherwise (review of #904)
    assert res["label_coverage"]["pairs_without_final_label"] == sum(r["kind"] == "G" for r in w.records)
    assert res["citable"] is False and res["dry_run"] is True
    assert res["layer2"]["cells"][st.L("base", "none", "default")]["descriptive_leg"]["found10"]["n"] == 2
    assert res["sensitivities"]["without_codex_verdicts"]["records"] < len(asc.split_records(w.records)[0])
    # the public check refuses a record id smuggled into the results
    assert asc.public_problems(res, {r["record_id"] for r in w.records}) == []
    assert asc.public_problems({"x": w.records[0]["record_id"]}, {w.records[0]["record_id"]}) == ["an id"]
    assert asc.public_problems({"x": "…" + w.records[0]["question"][:45]}, [], [w.records[0]["question"]])


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
    return st.GATE if res["layer1"]["H2"]["n_nonzero"] == 0 else "not confirmed"


def test_record_set_identity_is_enforced(tmp_path: Path) -> None:
    w = fx.World(tmp_path)
    with pytest.raises(SystemExit, match="not the frozen one"):
        asc.verify_record_set(w.records_path, w.records)


def test_seed_rule_is_fixed() -> None:
    assert asc.seed_int("261101|alias|perm") == asc.seed_int(asc.SEEDS["perm"])
    assert asc.seed_int(asc.SEEDS["perm"]) != asc.seed_int(asc.SEEDS["boot"])
    assert asc.seeded_key("s", "a", "b") == asc.sha_text("s|a|b")


PRIVATE = Path("/rag/tmp/alias-study-2026-11/out/main/accepted-alias-O.jsonl")


@pytest.mark.skipif(not PRIVATE.is_file(), reason="the private accepted-record file is not on this host")
def test_the_frozen_record_set_reproduces() -> None:
    """Read only, counts only: the committed rule reproduces §4.1's two shas."""
    recs = asc.load_records(PRIVATE)
    ident = asc.verify_record_set(PRIVATE, recs)
    assert (ident["records"], ident["balanced"], ident["descriptive"]) == (1091, 900, 191)
