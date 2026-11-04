"""The alias-judge review follow-ups: prevalence-aware metrics, the paraphrase subset and the
paraphrase analysis. Synthetic label files; no network, no model."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

_EVAL = Path(__file__).resolve().parents[2] / "scripts" / "eval"
if str(_EVAL) not in sys.path:
    sys.path.insert(0, str(_EVAL))

import alias_judge_followups as fu  # noqa: E402

PROV = {"commit": "c" * 40, "dirty": False}


def test_gwet_ac1_and_binary_metrics() -> None:
    assert fu.gwet_ac1([1, 1, 0, 0], [1, 0, 0, 0]) == pytest.approx(0.28125 / 0.53125)
    assert fu.gwet_ac1([], []) is None
    pred = {"a": 1, "b": 1, "c": 0, "d": None}
    ref = {"a": 1, "b": 0, "c": 0, "d": 0}
    m = fu.binary_metrics(pred, ref, ["a", "b", "c", "d"], f1_draws=200, seed="t")
    assert (m["tp"], m["fp"], m["fn"], m["tn"], m["no_label"]) == (1, 1, 0, 2, 1)   # missing -> no
    assert m["precision"]["value"] == 0.5 and m["recall"]["value"] == 1.0
    assert m["specificity"]["value"] == round(2 / 3, 4) and m["prevalence"]["value"] == 0.25
    assert m["false_yes_rate"]["value"] == round(1 / 3, 4) and m["f1"]["value"] == round(2 / 3, 4)
    assert m["kappa"] == 0.5 and m["ac1"] == round(fu.gwet_ac1([1, 1, 0, 0], [1, 0, 0, 0]) or 0, 4)
    assert m["precision"]["ci95"] == fu.jd.wilson(1, 2)
    lo, hi = m["f1"]["ci95_bootstrap"]
    assert 0 <= lo <= hi <= 1
    empty = fu.binary_metrics({}, {"x": 0}, ["x"])
    assert empty["precision"]["value"] is None and empty["recall"]["value"] is None


def test_allocate_and_paraphrase_subset() -> None:
    assert fu._allocate({"a": 5, "b": 3, "c": 2}, 5) == {"a": 3, "b": 1, "c": 1}
    assert fu._allocate({"a": 1, "b": 1}, 9) == {"a": 1, "b": 1}
    strata = ["gold", "lex_same", "lex_other", "coll_same", "coll_diff"]
    pairs, ref = {}, {}
    for i in range(100):
        pid = f"p{i:03d}"
        pairs[pid] = {"stratum": strata[i % 5]}
        ref[pid] = int(i < 30 and strata[i % 5] in ("gold", "lex_same"))
    ids, info = fu.paraphrase_subset(pairs, ref, 20, "seed")
    assert len(ids) == 20 and sum(ref[p] for p in ids) == 10 and info["yes"] == 10 and info["no"] == 10
    assert ids == fu.paraphrase_subset(pairs, ref, 20, "seed")[0]                     # deterministic
    assert ids != fu.paraphrase_subset(pairs, ref, 20, "other")[0]
    assert info["allocation"]["yes"] == {"gold": 5, "lex_same": 5}
    assert sum(info["allocation"]["no"].values()) == 10
    few = {p: int(p in ("p000", "p001")) for p in ref}
    ids2, info2 = fu.paraphrase_subset(pairs, few, 20, "seed")
    assert info2["yes"] == 2 and info2["no"] == 18 and {"p000", "p001"} <= set(ids2)


# ------------------------------------------------------------------ a synthetic validation directory

STRATA = {"p0": "gold", "p1": "gold", "p2": "lex_same", "p3": "lex_same", "p4": "lex_other", "p5": "coll_same",
          "p6": "coll_diff", "p7": "coll_same", "p8": "lex_other", "p9": "lex_other"}
TRUTH = {"p0": 1, "p1": 1, "p2": 1}                       # Opus yes; every other pair Opus no
# (C verdict for all 5 samples, E samples)
CE = {"p0": (1, [1, 1, 1]), "p1": (1, [0, 1, 1]), "p2": (0, [0, 0, 0]), "p3": (1, [1, 0, 0]),
      "p4": (0, [0, 0, 0]), "p5": (0, [1, 1, 1]), "p6": (0, [0, 0, 0]), "p7": (0, [0, 0, 0]),
      "p8": (0, [0, 0, 0]), "p9": (0, [0, 0, 0])}
ARGON = {"p1": 0, "p5": 1}                                # rep 1; every other pair agrees with Opus


def _put(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def make_validation(tmp: Path) -> dict[str, Any]:
    v = tmp / "validation"
    v.mkdir()
    _put(v / "pairs.jsonl", [{"pair_id": p, "stratum": s, "q_paper": "QA" if p < "p5" else "QB",
                              "c_paper": "QA" if p == "p5" else "X", "record_kind": "G"} for p, s in STRATA.items()])
    opus = []
    for p in STRATA:
        if p == "p9":
            continue                                       # no reference
        for r in (1, 2, 3):
            val = TRUTH.get(p, 0) if not (p == "p8" and r == 3) else 1      # p8: a 2-1 split
            opus.append({"k": f"opus|{p}|r{r}", "pair_id": p, "rep": r, "verdict": bool(val)})
    _put(v / "labels-opus.jsonl", opus)
    meta = {"model": "q", "judge_version": "alias-judge/2", "enable_thinking": True}
    c = _put(v / "labels-qwen-C.jsonl", [{"k": f"qwen|{p}", "pair_id": p, "n": 5, "verdicts": [cv] * 5, **meta}
                                          for p, (cv, _) in CE.items()])
    e = _put(v / "labels-qwen-E.jsonl", [{"k": f"qwen|{p}", "pair_id": p, "n": 3, "verdicts": ev, **meta}
                                          for p, (_, ev) in CE.items()])
    argon = _put(tmp / "labels-gw-argon.jsonl", [{"k": f"gw|{p}|r1", "pair_id": p, "rep": 1,
                                                  "verdict": bool(ARGON.get(p, TRUTH.get(p, 0)))} for p in STRATA])
    return {"out_dir": str(tmp / "out"), "validation_dir": str(v), "c_labels": str(c), "e_labels": str(e),
            "tiebreakers": {"argon": str(argon)}, "tiebreaker_used": "argon", "study_accepted": "unused",
            "f1_bootstrap": 100}


def test_precision_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fu, "study_papers", lambda path: ({"QA"}, {"stub": True}))
    cfg = make_validation(tmp_path)
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(cfg))
    assert fu.main(["--config", str(path), "precision"], prov=PROV) == 0
    res = json.loads((tmp_path / "out" / "analysis-precision.json").read_text())
    assert res["classes"]["unanimous"] == 8 and res["classes"]["opus_yes"] == 3
    a = res["subsets"]["all"]
    cnt = {k: (a[k]["tp"], a[k]["fp"], a[k]["fn"], a[k]["tn"]) for k in a}
    assert cnt["gate_opus"] == (2, 1, 1, 4)              # p1 escalated -> Opus yes; p5 escalated -> Opus no
    assert cnt["gate_argon"] == (1, 2, 2, 3)             # Argon says no on p1 and yes on p5
    assert cnt["gate_accepted"] == (1, 1, 1, 3)          # the 6 accepted pairs only
    assert cnt["C_maj5"] == (2, 1, 1, 4) and cnt["E_n1"] == (1, 2, 2, 3)
    assert a["gate_opus"]["prevalence"]["value"] == round(3 / 8, 4)
    ng = a["gate_opus"]["non_gold"]
    assert (ng["non_gold_pairs"], ng["opus_no"], ng["fp"]) == (6, 5, 1)
    assert ng["per_opus_no"]["value"] == 0.2 and ng["per_non_gold_pair"]["value"] == round(1 / 6, 4)
    assert ng["per_opus_no"]["ci95"][1] == fu.jd.wilson(1, 5)[1]
    assert a["gate_opus"]["by_stratum"]["lex_same"]["fp"] == 1
    assert a["gate_opus"]["by_stratum"]["gold"]["specificity"]["value"] is None
    assert res["overlap_pairs"] == {"q_study_paper": {"all": 5, "unanimous": 5},
                                    "touch_study_paper": {"all": 6, "unanimous": 6}}
    assert res["subsets"]["excl_q_study_paper"]["gate_opus"]["pairs"] == 3
    assert res["subsets"]["excl_touch_study_paper"]["gate_opus"]["pairs"] == 2
    assert res["subsets"]["excl_coll_diff"]["gate_opus"]["pairs"] == 7
    assert res["gate_escalations"] == {"unanimous": 2, "argon_agrees_with_opus": 0, "argon_no_label": 0}


def test_paraphrase_analysis_flags_a_moved_precision(tmp_path: Path) -> None:
    cfg = make_validation(tmp_path)
    v = Path(cfg["validation_dir"])
    sub = _put(tmp_path / "subset.txt", [])
    sub.write_text("".join(p + "\n" for p in sorted(STRATA) if p not in ("p8", "p9")))
    rows_c = [json.loads(x) for x in Path(cfg["c_labels"]).read_text().splitlines()]
    rows_e = [json.loads(x) for x in Path(cfg["e_labels"]).read_text().splitlines()]
    for r in rows_c + rows_e:
        if r["pair_id"] == "p3":
            r["verdicts"] = [0] * len(r["verdicts"])      # p1: the false yes on p3 is gone
    c1 = _put(tmp_path / "labels-qwen-Cp1.jsonl", rows_c)
    e1 = _put(tmp_path / "labels-qwen-Ep1.jsonl", [{**r, "n": 1, "verdicts": r["verdicts"][:1]} for r in rows_e])
    pcfg = {**cfg, "subset_file": str(sub), "baseline": "orig", "paraphrases": ["p1"],
            "versions": [{"name": "orig", "c_labels": cfg["c_labels"], "e_labels": cfg["e_labels"]},
                         {"name": "orig_e1", "c_labels": cfg["c_labels"], "e_labels": cfg["e_labels"], "e_sample": 1},
                         {"name": "p1", "c_labels": str(c1), "e_labels": str(e1)}]}
    path = tmp_path / "pcfg.json"
    path.write_text(json.dumps(pcfg))
    assert fu.main(["--config", str(path), "analyse-paraphrase"], prov=PROV) == 0
    res = json.loads((tmp_path / "out" / "analysis-paraphrase.json").read_text())
    o, p1, e1v = res["versions"]["orig"], res["versions"]["p1"], res["versions"]["orig_e1"]
    assert o["gate"]["precision"]["value"] == round(2 / 3, 4) and p1["gate"]["precision"]["value"] == 1.0
    assert o["escalated"] == 2 and o["gate_accepted_errors"] == 2
    assert e1v["E"]["tp"] == 2                            # E's second sample says yes on p1
    assert res["spread"]["gate.precision"]["delta_vs_baseline"]["p1"] == round(1 - 2 / 3, 4)
    assert res["flips_vs_baseline"]["p1"]["raw_gate"] == 1
    assert {(f["version"], f["judge"], f["metric"]) for f in res["flagged"]} >= {("p1", "gate", "precision")}
    assert all(f["version"] == "p1" for f in res["flagged"])                      # only paraphrases flag
    assert res["population_prevalence"] == round(3 / 8, 4)
    assert o["gate"]["precision_at_population_prevalence"] == fu.reweighted_precision(
        o["gate"]["recall"]["value"], o["gate"]["specificity"]["value"], 3 / 8)
    del rows_c[0]
    _put(c1, rows_c)
    with pytest.raises(SystemExit, match="lack a C or E row"):
        fu.main(["--config", str(path), "analyse-paraphrase"], prov=PROV)
    assert v.is_dir()


def test_out_dir_inside_repo_refused(tmp_path: Path) -> None:
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({"out_dir": str(_EVAL / "x")}))
    with pytest.raises(SystemExit, match="inside the repository"):
        fu.main(["--config", str(path), "precision"], prov=PROV)
