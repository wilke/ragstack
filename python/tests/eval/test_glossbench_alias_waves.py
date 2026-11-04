"""run-waves: waves keyed on ACCEPTED records (target / exhaustion / budget stop / max_calls,
resume mid-wave without double counting, the distinct-alias rule), the pure planner, the
incremental layer-1 check, the prompt version, and the layer-1 patterns added from the
2026-10-30 pilot's layer-2 rejects.

Fake gateway and fake CLI on the synthetic corpus of ``test_glossbench_alias``; no store,
no network, no model.
"""
from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_EVAL = Path(__file__).resolve().parents[2] / "scripts" / "eval"
if str(_EVAL) not in sys.path:
    sys.path.insert(0, str(_EVAL))

import glossbench_alias_checks as ak  # noqa: E402
import glossbench_alias_draft as ad  # noqa: E402
import glossbench_alias_waves as wv  # noqa: E402
import test_glossbench_alias as base  # noqa: E402
from test_glossbench_alias import (  # noqa: E402
    NOTICE,
    AliasCLI,
    Gateway,
    Resp,
    fake_draft,
    jsonl,
    ok_resp,
    run,
    world,
)

GOAL = {"G": 2, "C": 1}
NOCAP: dict[str, int | None] = {"G": None, "C": None}


def setup(tmp: Path, n_papers: int = 3, **waves: Any) -> tuple[Path, Path]:
    """The world; ``c_first`` off unless given (most tests count G and C in one wave)."""
    path, cfg = world(tmp, n_papers=n_papers)
    base._with(path, waves={"c_first": False, **waves})
    return path, Path(cfg["out_dir"])


def waves(path: Path, gw: Any, cli: Any = None, *extra: str) -> int:
    assert run(path, "preflight", "--stage", "waves", "--set", "T") == 0
    return run(path, "run-waves", "--set", "T", "--notice-acknowledged", *extra, gw=gw, cli=cli or AliasCLI())


def state(out: Path) -> dict[str, Any]:
    return json.loads((out / "waves-state-alias-T.json").read_text())


# ------------------------------------------------------------------ the pure planner

def ps(paper: str = "p", g: list[tuple[str, str]] | None = None, c: list[str] | None = None) -> wv.PaperState:
    return wv.PaperState(paper, g if g is not None else [("g0", "A"), ("g1", "A"), ("g2", "B"), ("g3", "B")],
                         c if c is not None else ["c0", "c1"])


def test_plan_paper_distinct_aliases_and_exhaustion() -> None:
    p = ps()
    assert wv.plan_paper(p, GOAL, NOCAP) == ["g0", "g2", "c0"]           # one job per alias in a wave
    p.drafted |= {"g0", "g2", "c0"}
    p.acc_keys.add("A")                                                  # A accepted, B and C rejected
    assert wv.plan_paper(p, GOAL, NOCAP) == ["g3", "c1"]                 # g1 (alias A) skipped for good
    p.drafted |= {"g3", "c1"}
    assert wv.plan_paper(p, GOAL, NOCAP) is None                         # no unaccepted alias left: exhausted
    q = ps(g=[("g0", "A"), ("g1", "A"), ("g2", "A")])
    assert not wv.reachable(q, GOAL) and wv.plan_paper(q, GOAL, NOCAP) is None
    done = ps()
    done.acc_keys |= {"A", "B"}
    done.acc_c = 1
    assert wv.satisfied(done, GOAL) and wv.plan_paper(done, GOAL, NOCAP) == []
    capped = ps()
    capped.drafted |= {"g0", "g1"}
    assert wv.plan_paper(capped, GOAL, {"G": 2, "C": None}) is None      # the per-paper cap is spent


def test_plan_paper_c_first() -> None:
    p = ps()
    assert wv.plan_paper(p, GOAL, NOCAP, c_first=True) == ["c0"]          # C only until its goal is met
    p.drafted.add("c0")
    assert wv.plan_paper(p, GOAL, NOCAP, c_first=True) == ["c1"]
    p.drafted.add("c1")
    assert wv.plan_paper(p, GOAL, NOCAP, c_first=True) is None            # both C failed: retired, no G spent
    q = ps()
    q.drafted.add("c0")
    q.acc_c = 1
    assert wv.plan_paper(q, GOAL, NOCAP, c_first=True) == ["g0", "g2"]    # then G, distinct aliases
    r = ps(g=[("g0", "A"), ("g1", "A")])
    assert wv.plan_paper(r, GOAL, NOCAP, c_first=True) is None            # feasibility judged on both kinds
    assert wv.waves_config(None)["c_first"] is True
    with pytest.raises(SystemExit):
        wv.waves_config({"c_first": "yes"})


def test_waves_c_first_end_to_end(tmp_path: Path) -> None:
    """Default policy: wave 1 drafts only C jobs, wave 2 the G jobs of papers whose C was
    accepted; a paper whose C jobs all fail never drafts a G job."""
    path, out = setup(tmp_path, n_papers=2, target_papers=2, c_first=True, accept_rate={"G": 1.0, "C": 1.0})
    gw = Gateway()
    assert waves(path, gw) == 0
    w = state(out)["waves"]
    assert w["stop"] == "target_met" and w["wave"] == 2
    assert [h["jobs_by_kind"] for h in w["history"]] == [{"C": 2}, {"G": 4}]
    path, out = setup(tmp_path / "rej", n_papers=2, target_papers=2, c_first=True)
    gw = Gateway()
    assert waves(path, gw, AliasCLI(reject_if="SHORT FORM: (none: a control record)")) == 0   # every C rejected
    assert state(out)["waves"]["stop"] == "pool_exhausted"
    drafted = jsonl(out / "draft-alias-T.jsonl")
    assert drafted and all(d["stratum"] == "C" for d in drafted)


def test_probabilities_and_activation() -> None:
    assert wv.prob_at_least([0.5, 0.5], 2) == pytest.approx(0.25)
    assert wv.prob_at_least([0.5, 0.5], 1) == pytest.approx(0.75)
    assert wv.prob_at_least([], 0) == 1.0 and wv.prob_at_least([0.9], 2) == 0.0
    rates = {"G": 0.5, "C": 0.5}
    # keys A (2 jobs), B (2 jobs): P(A) = P(B) = 0.75; C: 1 - 0.25
    assert wv.p_satisfy(ps(), GOAL, rates, NOCAP) == pytest.approx(0.75 * 0.75 * 0.75)
    assert wv.blend(0, 0, 0.36, 20) == pytest.approx(0.36) and wv.blend(10, 10, 0.0, 10) == pytest.approx(0.5)
    states = {f"p{i}": ps(f"p{i}") for i in range(10)}
    order = sorted(states)
    one = wv.p_satisfy(states["p0"], GOAL, rates, NOCAP)
    n = wv.activate(states, order, 0, 2, rates, GOAL, NOCAP, max_new=100)
    assert n == math.ceil(2 / one)                                        # expected satisfied just reaches 2
    assert wv.activate(states, order, 0, 2, rates, GOAL, NOCAP, max_new=3) == 3
    assert wv.activate(states, order, 0, 100, rates, GOAL, NOCAP, max_new=100) == 10   # never past the pool
    with pytest.raises(SystemExit):
        wv.waves_config({"bogus": 1})
    with pytest.raises(SystemExit):
        wv.waves_config({"accept_rate": {"G": 0}})


def test_forecast_is_seeded_and_reaches_target() -> None:
    states = {f"p{i}": wv.PaperState(f"p{i}", [(f"p{i}g{j}", k) for j, k in enumerate("AABB")],
                                      [f"p{i}c0", f"p{i}c1"]) for i in range(40)}
    order = sorted(states)
    info = {j: (p, "G", k) for p, s in states.items() for j, k in s.g} | \
        {j: (p, "C", "") for p, s in states.items() for j in s.c}
    kw: dict[str, Any] = {"goal": GOAL, "caps": NOCAP, "target": 5, "max_new": 100, "max_waves": 40, "wave0": 0,
                          "sims": 50}
    a = wv.forecast(states, order, 0, info, rates={"G": 0.4, "C": 0.4}, seed="s", **kw)
    b = wv.forecast(states, order, 0, info, rates={"G": 0.4, "C": 0.4}, seed="s", **kw)
    assert a == b and a["p_target_met"] > 0.9 and a["jobs_p95"] >= a["jobs_mean"] > 0
    assert all(not s.drafted for s in states.values())                    # the simulation copies, never mutates
    hard = wv.forecast(states, order, 0, info, rates={"G": 0.05, "C": 0.05}, seed="s", **kw)
    assert hard["jobs_mean"] > a["jobs_mean"] and hard["papers_activated_mean"] > a["papers_activated_mean"]


def test_alias_key_is_the_matchers_normalisation() -> None:
    assert ak.alias_key("MSCs", "abbreviation") == ak.alias_key("MSC", "abbreviation") == "MSC"
    assert ak.alias_key("MSCs", "both") == "MSC"
    assert ak.alias_key("Q_v", "symbol") == ak.alias_key("Qv", "symbol")
    assert ak.alias_key("Ts", "symbol") != ak.alias_key("T", "symbol")     # a symbol has no plural


# ------------------------------------------------------------------ run-waves end to end

def test_waves_stop_on_target_and_activate_only_as_needed(tmp_path: Path) -> None:
    path, out = setup(tmp_path, n_papers=6, target_papers=2, accept_rate={"G": 1.0, "C": 1.0})
    gw = Gateway()
    assert waves(path, gw) == 0
    st = state(out)
    w = st["waves"]
    assert st["status"] == "complete" and w["stop"] == "target_met" and w["wave"] == 1
    assert w["n_active"] == 2 and len(gw.bodies) == 2 * 3                # 2 papers x (2 G + 1 C), 4 never started
    assert len({d["paper"] for d in jsonl(out / "draft-alias-T.jsonl")}) == 2
    s = json.loads((out / "acceptance-summary-alias-T.json").read_text())
    assert s["alias"]["waves"]["papers_satisfied"] == 2 and s["alias"]["waves"]["stop"] == "target_met"
    acc = jsonl(out / "accepted-alias-T.jsonl")
    assert len(acc) == 6 and all(a["prompt_version"] == ak.PROMPT_VERSION for a in acc)
    assert all(d["prompt_version"] == ak.PROMPT_VERSION for d in jsonl(out / "draft-alias-T.jsonl"))
    for p in {a["paper"] for a in acc}:                                   # the two G on distinct aliases
        assert len({ak.alias_key(a["alias"], a["alias_kind"]) for a in acc if a["paper"] == p and a["kind"] == "G"}) == 2
    assert st["totals"]["since_start"]["gateway_calls"] == 6 and st["totals"]["since_start"]["cli_calls"] == 6
    # the same command on a finished run: nothing new is drafted
    assert run(path, "preflight", "--stage", "waves", "--set", "T") == 0
    gw2 = Gateway()
    assert run(path, "run-waves", "--set", "T", "--notice-acknowledged", "--resume", gw=gw2, cli=AliasCLI()) == 0
    assert not gw2.bodies


def test_waves_distinct_alias_rule_and_exhaustion(tmp_path: Path) -> None:
    """Every Qv record is rejected by the verifier: ACH is accepted once and never drafted
    again; the paper tries every Qv job, then is exhausted (one alias cannot satisfy it)."""
    path, out = setup(tmp_path, n_papers=1, target_papers=1)
    gw = Gateway()
    assert waves(path, gw, AliasCLI(reject_if="SHORT FORM: Qv")) == 0
    w = state(out)["waves"]
    assert w["stop"] == "pool_exhausted"
    pool = jsonl(out / "pool-alias-T.jsonl")[1:]
    drafted = {d["job_id"] for d in jsonl(out / "draft-alias-T.jsonl")}
    g_drafted = [j for j in pool if j["job_id"] in drafted and j["stratum"] == "G"]
    assert sum(j["alias"] == "ACH" for j in g_drafted) == 1               # accepted alias: later jobs skipped
    assert sum(j["alias"] == "Qv" for j in g_drafted) == sum(j["alias"] == "Qv" for j in pool if j["stratum"] == "G")
    assert sum(j["stratum"] == "C" for j in pool if j["job_id"] in drafted) == 1    # C accepted in wave 1
    s = json.loads((out / "acceptance-summary-alias-T.json").read_text())["alias"]["waves"]
    assert s["papers_satisfied"] == 0 and s["papers_with_C"] == 1 and s["papers_with_distinct_alias_G"] == 0
    assert [h["wave"] for h in w["history"]] == list(range(1, w["wave"] + 1))


def test_waves_exhaust_the_pool_when_nothing_is_accepted(tmp_path: Path) -> None:
    path, out = setup(tmp_path, n_papers=2, target_papers=5)
    gw = Gateway()

    class RejectAll(AliasCLI):
        def __call__(self, cmd: list[str], **kw: Any) -> Any:
            r = super().__call__(cmd, **kw)
            o = json.loads(r.stdout)
            o["result"] = json.dumps({**dict.fromkeys(ak.VERIFY_KEYS, True), "single_reading": False, "reason": "x"})
            return SimpleNamespace(returncode=0, stdout=json.dumps(o), stderr="")
    assert waves(path, gw, RejectAll()) == 0
    w = state(out)["waves"]
    assert w["stop"] == "pool_exhausted" and w["n_active"] == 2
    pool = jsonl(out / "pool-alias-T.jsonl")[1:]
    drafted = {d["job_id"] for d in jsonl(out / "draft-alias-T.jsonl")}
    # both C jobs fail in waves 1-2: the paper is exhausted, its last G jobs are never drafted
    assert {j["job_id"] for j in pool if j["stratum"] == "C"} <= drafted
    assert {j["job_id"] for j in pool if j["stratum"] == "G"} - drafted
    assert len(gw.bodies) == len(drafted) == 2 * (2 + 2 + 1 + 1)           # each job drafted once


def test_waves_budget_stop_mid_wave_then_resume_without_double_counting(tmp_path: Path) -> None:
    path, out = setup(tmp_path, n_papers=3, target_papers=3, accept_rate={"G": 1.0, "C": 1.0})

    def respond(body: dict[str, Any], n: int) -> Resp:
        return Resp(503, None, NOTICE) if n == 4 else ok_resp(json.dumps(fake_draft(body["messages"])))
    gw = Gateway(respond)
    assert waves(path, gw) == ad.EXIT_BUDGET == 76
    st = state(out)
    assert st["status"] == "paused" and st["reason"].startswith("budget_stop")
    assert st["waves"]["wave"] == 1 and st["waves"]["phase"] == "draft" and (out / "PAUSE").exists()
    assert "run-waves --resume" in (out / "PAUSE").read_text()
    assert st["totals"]["since_start"]["gateway_calls"] == 4
    (out / "PAUSE").unlink()
    with pytest.raises(SystemExit, match="fresh notice"):
        run(path, "run-waves", "--set", "T", "--notice-acknowledged", "--resume", gw=Gateway(), cli=AliasCLI())
    assert run(path, "preflight", "--stage", "waves", "--set", "T") == 0
    gw2 = Gateway()
    assert run(path, "run-waves", "--set", "T", "--notice-acknowledged", "--resume", gw=gw2, cli=AliasCLI()) == 0
    st = state(out)
    assert st["waves"]["stop"] == "target_met" and st["waves"]["wave"] == 1
    assert len(gw2.bodies) == 9 - 3                                        # 3 drafted before the stop
    assert st["totals"]["since_start"]["gateway_calls"] == 4 + 6
    assert len(jsonl(out / "draft-alias-T.jsonl")) == 9 and [h["wave"] for h in st["waves"]["history"]] == [1]


def test_waves_resume_mid_verify(tmp_path: Path) -> None:
    path, out = setup(tmp_path, n_papers=2, target_papers=2, accept_rate={"G": 1.0, "C": 1.0})

    class LimitAfter(AliasCLI):
        def __call__(self, cmd: list[str], **kw: Any) -> Any:
            if len(self.calls) >= 2:
                self.calls.append({"cmd": cmd, "input": kw["input"]})
                return SimpleNamespace(returncode=1, stdout="", stderr="usage limit reached")
            return super().__call__(cmd, **kw)
    gw = Gateway()
    assert waves(path, gw, LimitAfter()) == ad.EXIT_PAUSED
    st = state(out)
    assert st["waves"]["phase"] == "verify" and st["reason"] == "usage_limit" and len(gw.bodies) == 6
    assert run(path, "preflight", "--stage", "waves", "--set", "T") == 0
    gw2, cli2 = Gateway(), AliasCLI()
    assert run(path, "run-waves", "--set", "T", "--notice-acknowledged", "--resume", gw=gw2, cli=cli2) == 0
    st = state(out)
    assert not gw2.bodies and st["waves"]["stop"] == "target_met"           # nothing re-drafted
    log = jsonl(out / "acceptance-alias-T.jsonl")
    l2 = [r for r in log if r["layer"] == "l2"]
    assert len(l2) == len({r["record_id"] for r in l2}) == 6               # every record verified once
    assert st["totals"]["since_start"]["gateway_calls"] == 6


def test_waves_max_calls_is_one_counter_across_waves(tmp_path: Path) -> None:
    """Wave 1 drafts 9 jobs; Qv rejects force a wave 2; max_calls 11 pauses inside wave 2."""
    path, out = setup(tmp_path, n_papers=3, target_papers=3)
    base._with(path, limits={"max_calls": 11})
    gw = Gateway()
    assert waves(path, gw, AliasCLI(reject_if="SHORT FORM: Qv")) == ad.EXIT_PAUSED
    st = state(out)
    assert len(gw.bodies) == 11 and st["reason"].startswith("max_calls") and st["waves"]["wave"] == 2


def test_waves_preflight_notice(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path, out = setup(tmp_path, n_papers=6, target_papers=2, preflight_sims=60)
    base._with(path, limits={"output_token_cap": 1000, "notice_overrun": 0.1})
    assert run(path, "preflight", "--stage", "waves", "--set", "T") == 0
    plan = json.loads((out / "preflight-waves-alias-T.json").read_text())["plan"]
    f = plan["forecast"]
    assert plan["calls"] == math.ceil(f["jobs_mean"] * 1.32) and plan["est_output_tokens"] == plan["calls"] * 1780
    assert plan["calls_p95"] >= plan["calls"] and plan["calls_pessimistic"] >= plan["calls"]
    assert plan["calls_upper_bound"] >= plan["calls_pessimistic_p95"]
    assert plan["run_stops_at_calls"] == math.ceil(plan["calls"] * 1.1)
    assert plan["rates"]["rates"] == {"G": 0.36, "C": 0.38} and plan["papers_reachable"] == 6
    assert plan["caps_headroom"]["caps_stop_before_target"] == ["output_token_cap"]
    printed = capsys.readouterr().out
    assert "GATEWAY-NOTICE" in printed and "CLI-NOTICE" in printed and "run-waves" in printed


def test_waves_refusals(tmp_path: Path) -> None:
    path, out = setup(tmp_path)
    with pytest.raises(SystemExit, match="pilot"):
        run(path, "preflight", "--stage", "waves", "--set", "T", "--pilot", "4")
    assert waves(path, Gateway()) == 0
    assert run(path, "preflight", "--stage", "draft", "--set", "T") == 0
    assert run(path, "draft", "--set", "T", "--notice-acknowledged", gw=Gateway()) == ad.EXIT_HARD_STOP
    # without --resume an existing waves state is refused
    assert run(path, "preflight", "--stage", "waves", "--set", "T") == 0
    assert run(path, "run-waves", "--set", "T", "--notice-acknowledged", gw=Gateway()) == ad.EXIT_HARD_STOP
    p2, _ = setup(tmp_path / "b")
    base.chain(p2, Gateway())
    assert run(p2, "preflight", "--stage", "waves", "--set", "T") == 0
    assert run(p2, "run-waves", "--set", "T", "--notice-acknowledged", gw=Gateway()) == ad.EXIT_HARD_STOP


def test_incremental_check_judges_a_later_duplicate_without_rewriting_the_log(tmp_path: Path) -> None:
    """Two waves draft the same question for one paper: the later record is the duplicate,
    and re-running check never conflicts with the append-only log."""
    path, out = setup(tmp_path, n_papers=1, target_papers=1)

    def same_q(body: dict[str, Any], n: int) -> Resp:
        d = fake_draft(body["messages"])
        if "SHORT FORM: Qv" in body["messages"][-1]["content"]:
            d["question"] = "What is stated about the outdoor airflow in hospital ventilation?"
        return ok_resp(json.dumps(d))
    assert waves(path, Gateway(same_q), AliasCLI(reject_if="SHORT FORM: Qv")) == 0
    log = jsonl(out / "acceptance-alias-T.jsonl")
    l1 = [r for r in log if r["layer"] == "l1"]
    assert any("duplicate_question" in r["reasons"] for r in l1)
    assert run(path, "check", "--set", "T") == 0                             # idempotent, no SystemExit
    assert len(jsonl(out / "acceptance-alias-T.jsonl")) == len(log)


# ------------------------------------------------------------------ layer 1: patterns from the pilot's rejects

@pytest.mark.parametrize("q,bad", [
    ("According to the list of abbreviations, which letters stand for hydraulic retention time?", True),
    ("According to the regression analysis, what was the mean hydraulic retention time?", True),
    ("Which loss function performed best on the leaf vein dataset?", True),
    ("How did hydraulic retention time vary within a wastewater dataset?", True),
    ("What did the experiment show about the hydraulic retention time of reactors?", True),
    ("What drives the hydraulic retention time in a simulation of rivers?", True),
    ("Which mineral was found to raise the hydraulic retention time of soils?", True),
    ("Which reagent was added to Buffer B to stabilise the hydraulic retention time?", True),
    ("How many cores per sample were drilled in arm 2 to measure retention time?", True),
    ("How many cores were taken from each sample to measure hydraulic retention time?", True),
    ("What does the passage say about the hydraulic retention time?", True),
    ("What do the results indicate about the hydraulic retention time of reactors?", True),
    ("What do the patients report after a long hydraulic retention time?", True),
    # standalone questions stay clean
    ("How does hydraulic retention time affect nitrogen removal in activated sludge reactors?", False),
    ("Which bacterial genus dominates anaerobic digesters operated at long hydraulic retention times?", False),
    ("What is the typical hydraulic retention time of constructed wetlands treating dairy effluent?", False),
    ("What do the results of nitrogen removal depend on in activated sludge reactors?", False),
    ("Which groups of microbes thrive when the hydraulic retention time is long?", False),
    ("What is the analysis of variance used for in reactor studies?", False),
])
def test_standalone_pilot_patterns(q: str, bad: bool) -> None:
    assert bool(ak.self_references(q)) is bad, ak.self_references(q)


#: legitimate standalone questions (review of #889): the branch's patterns rejected 19 of these
#: 20 — compound nouns ("the sample size"), named datasets and theories, standard names ("group A
#: streptococcus", "step 1 of glycolysis"), established facts in the past passive ("was identified")
LEGIT_STANDALONE = [
    "How does the sample size affect the statistical power of a two-sided t-test?",
    "What limits the measurement accuracy of a Michelson interferometer?",
    "What is the proposed mechanism of action of metformin in hepatocytes?",
    "Which gene was identified as the cause of cystic fibrosis?",
    "From which organism was Taq polymerase originally isolated?",
    "Which boson was detected at the Large Hadron Collider in 2012?",
    "How many images does the ImageNet dataset contain?",
    "Which handwritten digits are in the MNIST data set?",
    "According to the Standard Model, which particle mediates the weak interaction?",
    "According to the Bose-Einstein statistics, how do identical bosons occupy energy states?",
    "Which diseases does group A streptococcus cause in children?",
    "What cytokines do group 2 innate lymphoid cells secrete in allergic airway inflammation?",
    "Why are group 1 metabotropic glutamate receptors coupled to phospholipase C?",
    "What does site 1 protease cleave in the endoplasmic reticulum stress response?",
    "How many chromosomes does each animal cell of Drosophila melanogaster carry?",
    "Which enzyme catalyses step 1 of glycolysis in the cytosol?",
    "What does the simulation time step limit in molecular dynamics of proteins?",
    "What role does the analysis pipeline play in reducing batch effects in single-cell RNA sequencing?",
    "How does the trial design of a crossover study control for between-subject variability?",
    "What determines the survey depth of a radio telescope array?",
]


@pytest.mark.parametrize("q", LEGIT_STANDALONE)
def test_standalone_patterns_spare_legitimate_questions(q: str) -> None:
    assert not ak.self_references(q) and not ak.open_question(q), ak.self_references(q)


@pytest.mark.parametrize("q,bad", [
    ("What was significant about the discovery of hydraulic retention time effects in Spain?", True),
    ("What is the general relationship between hydraulic retention time and sludge age?", True),
    ("What is involved in setting the hydraulic retention time of membrane reactors?", True),
    ("Which sludge age accompanies a hydraulic retention time of two days in membrane reactors?", False),
    ("Why is hydraulic retention time important for nitrifying bacteria in membrane reactors?", False),
])
def test_open_question(q: str, bad: bool) -> None:
    assert bool(ak.open_question(q)) is bad, ak.open_question(q)


def test_layer1_rejects_open_and_study_specific_questions(tmp_path: Path) -> None:
    c, idx = base.corpus_idx(tmp_path)
    kw = {"kind": "G", "entry": base.G_ENTRY, "paper": "d1", "source_chunk": c.chunks["d1-c1"], "idx": idx, "cfg": {}}
    out = ak.layer1({**base.GOOD_G, "question": "What was significant about the air change rate with shut windows?"},
                    None, **kw)
    assert "open_question" in out["reasons"] and out["open_question"]
    out = ak.layer1({**base.GOOD_G, "question": "How low was the air change rate found to fall once windows are shut?"},
                    None, **kw)
    assert out["verdict"] == "pass", out["reasons"]                        # "was ... found" is not adjacent
    out = ak.layer1({**base.GOOD_G, "question": "How low did the air change rate fall in each sample with shut windows?"},
                    None, **kw)
    assert "not_standalone" in out["reasons"]
    assert "open_question" in ak.REASON_WORDS and "own experiment" in ak.REASON_WORDS["not_standalone"]


def test_prompts_carry_the_revised_rules_and_version() -> None:
    for system in (ak.DRAFT_SYSTEM_G, ak.DRAFT_SYSTEM_C):
        assert "distinguishing context" in system and "single reading" in system
        assert "character for character" in system and "at most 300 characters" in system
        assert '{"question": "...", "answer": "...", "quote": "..."}' in system and '{"none": true}' in system
    assert "SHORT FORM" in ak.DRAFT_SYSTEM_G and "spelled exactly as the passage spells it" in ak.DRAFT_SYSTEM_C
    assert ak.PROMPT_VERSION == "alias-draft/2"
    msgs = ak.draft_messages("C", "passage", {"primary": "term"}, {"reasons": ["quote_too_long"]})
    assert "one sentence" in msgs[-1]["content"]


def test_seeded_activation_order_is_stable(tmp_path: Path) -> None:
    path, out = setup(tmp_path, n_papers=6, target_papers=2, accept_rate={"G": 1.0, "C": 1.0})
    assert waves(path, Gateway()) == 0
    first = sorted({d["paper"] for d in jsonl(out / "draft-alias-T.jsonl")})
    p2, out2 = setup(tmp_path / "again", n_papers=6, target_papers=2, accept_rate={"G": 1.0, "C": 1.0})
    assert waves(p2, Gateway()) == 0
    assert sorted({d["paper"] for d in jsonl(out2 / "draft-alias-T.jsonl")}) == first
    papers = sorted(f"d{i}" for i in range(1, 7))
    assert first == sorted(random.Random("test|T|waves").sample(papers, 6)[:2])


# ------------------------------------------------------------------ --reseal-config

import glossbench_r2_draft as dr  # noqa: E402


def _finished(tmp: Path) -> tuple[Path, Path, Path]:
    """A run-waves run stopped at ``target_met`` (2 of 6 papers), and a copy of its sealed config."""
    path, out = setup(tmp, n_papers=6, target_papers=2, accept_rate={"G": 1.0, "C": 1.0})
    base._with(path, limits={"max_calls": 50})
    assert waves(path, Gateway()) == 0
    st = state(out)
    assert st["status"] == "complete" and st["waves"]["stop"] == "target_met" and st["waves"]["n_active"] == 2
    sealed = tmp / "sealed.json"
    sealed.write_text(path.read_text())
    return path, out, sealed


def _reseal(path: Path, sealed: Path, gw: Any, *extra: str, note: str = "owner: extend to 4 papers",
            provenance: Any = None) -> int:
    assert run(path, "preflight", "--stage", "waves", "--set", "T") == 0
    return ad.main(["--private-config", str(path), "run-waves", "--set", "T", "--notice-acknowledged", "--resume",
                    "--reseal-config", "--reseal-from", str(sealed), "--reseal-note", note, *extra],
                   provenance=provenance or (lambda: base.PROV), http_factory=lambda: gw, cli_runner=AliasCLI())


def test_reseal_raised_target_continues_a_finished_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Whitelisted keys only (target up, max_calls, output cap, notice_overrun): the finished
    run continues from its state — the same seeded order, the first two papers kept, nothing
    re-drafted or counted twice — and the re-seal is in the state and the log."""
    path, out, sealed = _finished(tmp_path)
    before = state(out)
    drafted_before = {d["job_id"] for d in jsonl(out / "draft-alias-T.jsonl")}
    base._with(path, waves={"target_papers": 4}, limits={"max_calls": 60, "output_token_cap": 900_000,
                                                          "notice_overrun": 0.5})
    # without --reseal-config a changed config is refused, naming the way out
    assert run(path, "preflight", "--stage", "waves", "--set", "T") == 0
    gw0 = Gateway()
    assert run(path, "run-waves", "--set", "T", "--notice-acknowledged", "--resume", gw=gw0,
               cli=AliasCLI()) == ad.EXIT_HARD_STOP
    assert not gw0.bodies and "--reseal-config" in capsys.readouterr().err
    gw = Gateway()
    new_prov = {**base.PROV, "commit": "b" * 40}
    assert _reseal(path, sealed, gw, provenance=lambda: new_prov) == 0
    st = state(out)
    w = st["waves"]
    assert st["status"] == "complete" and w["stop"] == "target_met" and w["wave"] == 2 and w["n_active"] == 4
    assert len(gw.bodies) == 2 * 3                                           # only the two new papers
    drafts = jsonl(out / "draft-alias-T.jsonl")
    assert len(drafts) == len({d["record_id"] for d in drafts}) == 12
    assert drafted_before < {d["job_id"] for d in drafts}
    assert st["totals"]["since_start"]["gateway_calls"] == 6 + 6               # no double counting
    assert [h["wave"] for h in w["history"]] == [1, 2] and w["history"][1]["papers_satisfied"] == 4
    first = {d["paper"] for d in drafts if d["job_id"] in drafted_before}
    assert len(first) == 2 and len({d["paper"] for d in drafts}) == 4
    rs = st["reseals"]
    assert len(rs) == 1 and rs[0]["note"] == "owner: extend to 4 papers" and rs[0]["by"]
    assert rs[0]["config_sha_before"] == before["config_sha256"] == dr.g.file_sha(sealed)
    assert rs[0]["config_sha_after"] == st["config_sha256"] == dr.g.file_sha(path)
    assert rs[0]["commit_before"] == "a" * 40 and rs[0]["commit_after"] == st["commit"] == "b" * 40
    assert rs[0]["status_before"] == "complete" and rs[0]["t"]
    assert rs[0]["diff"] == {"waves.target_papers": {"old": 2, "new": 4},
                             "limits.max_calls": {"old": 50, "new": 60},
                             "limits.output_token_cap": {"old": None, "new": 900_000},
                             "limits.notice_overrun": {"old": None, "new": 0.5}}
    assert st["segments"][-1]["resealed"] == rs[0] and "resealed" not in st["segments"][0]
    err = capsys.readouterr().err
    assert "CONFIG RESEALED" in err and "owner: extend to 4 papers" in err and "waves.target_papers" in err
    # the next resume runs against the new seal, with no flag
    assert run(path, "preflight", "--stage", "waves", "--set", "T") == 0
    assert ad.main(["--private-config", str(path), "run-waves", "--set", "T", "--notice-acknowledged", "--resume"],
                   provenance=lambda: new_prov, http_factory=Gateway, cli_runner=AliasCLI()) == 0
    assert len(state(out)["reseals"]) == 1


def test_reseal_refuses_keys_outside_the_whitelist(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path, out, sealed = _finished(tmp_path)
    before = (out / "waves-state-alias-T.json").read_text()
    base._with(path, waves={"target_papers": 4, "max_new_papers_per_wave": 3}, limits={"max_calls": 60},
               drafter={"max_tokens": 1234, "inflight": 2})
    gw = Gateway()
    assert _reseal(path, sealed, gw) == ad.EXIT_HARD_STOP
    err = capsys.readouterr().err
    assert "refused" in err
    for k in ("waves.max_new_papers_per_wave", "drafter.max_tokens", "drafter.inflight"):
        assert k in err
    assert "limits.max_calls (" not in err and "waves.target_papers (" not in err      # allowed keys not named
    assert not gw.bodies and (out / "waves-state-alias-T.json").read_text() == before


def test_reseal_refuses_a_lowered_target(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path, out, sealed = _finished(tmp_path)
    base._with(path, waves={"target_papers": 1})
    gw = Gateway()
    assert _reseal(path, sealed, gw) == ad.EXIT_HARD_STOP
    assert "waves.target_papers (increase only: 2 -> 1)" in capsys.readouterr().err and not gw.bodies


def test_reseal_from_must_be_the_sealed_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path, out, sealed = _finished(tmp_path)
    base._with(path, waves={"target_papers": 4})
    wrong = tmp_path / "wrong.json"
    wrong.write_text(path.read_text())                                       # the NEW config, not the sealed one
    gw = Gateway()
    assert _reseal(path, wrong, gw) == ad.EXIT_HARD_STOP
    assert "not the config the run was sealed with" in capsys.readouterr().err and not gw.bodies
    assert _reseal(path, tmp_path / "missing.json", gw) == ad.EXIT_HARD_STOP
    # nothing to re-seal: the config is the sealed one
    path.write_text(sealed.read_text())
    assert _reseal(path, sealed, gw) == ad.EXIT_HARD_STOP
    assert "unchanged" in capsys.readouterr().err and not gw.bodies


def test_reseal_flags_and_stops(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The note and --reseal-from are required, --resume is required, and a re-seal never
    clears a hard stop or a budget/limit pause by itself."""
    path, out, sealed = _finished(tmp_path)
    base._with(path, waves={"target_papers": 4})
    assert run(path, "preflight", "--stage", "waves", "--set", "T") == 0
    with pytest.raises(SystemExit, match="reseal-note"):
        run(path, "run-waves", "--set", "T", "--notice-acknowledged", "--resume", "--reseal-config",
            "--reseal-from", str(sealed), gw=Gateway())
    with pytest.raises(SystemExit, match="reseal-from"):
        run(path, "run-waves", "--set", "T", "--notice-acknowledged", "--resume", "--reseal-config",
            "--reseal-note", "x", gw=Gateway())
    with pytest.raises(SystemExit, match="go with --reseal-config"):
        run(path, "run-waves", "--set", "T", "--notice-acknowledged", "--resume", "--reseal-note", "x", gw=Gateway())
    with pytest.raises(SystemExit, match="needs --resume"):
        run(path, "run-waves", "--set", "T", "--notice-acknowledged", "--reseal-config", "--reseal-from",
            str(sealed), "--reseal-note", "x", gw=Gateway())
    sp = out / "waves-state-alias-T.json"
    st = json.loads(sp.read_text())
    sp.write_text(json.dumps({**st, "status": "hard_stop", "reason": "isolation_failed"}))
    gw = Gateway()
    assert _reseal(path, sealed, gw) == ad.EXIT_HARD_STOP
    assert "hard-stopped" in capsys.readouterr().err and not gw.bodies
    after = json.loads(sp.read_text())
    assert after["status"] == "hard_stop" and not after.get("reseals")
    # a budget-stopped (paused) run: the reseal does not lift PAUSE or skip the fresh notice
    sp.write_text(json.dumps({**st, "status": "paused", "reason": "budget_stop: HTTP 503",
                              "paused_epoch": 4e9}))
    assert run(path, "preflight", "--stage", "waves", "--set", "T") == 0
    with pytest.raises(SystemExit, match="fresh notice"):
        ad.main(["--private-config", str(path), "run-waves", "--set", "T", "--notice-acknowledged", "--resume",
                 "--reseal-config", "--reseal-from", str(sealed), "--reseal-note", "x"],
                provenance=lambda: base.PROV, http_factory=Gateway, cli_runner=AliasCLI())


def test_reseal_diff_rules() -> None:
    old = {"waves": {"target_papers": 100, "seed": 1}, "limits": {"prior_gateway_usage": {"calls": 5, "files": ["a"]}},
           "verifier": {"model": "m"}}
    ok, bad = dr.config_reseal_diff(old, {**old, "limits": {"prior_gateway_usage": {"calls": 7, "files": ["a", "b"]}}})
    assert not bad and ok == {"limits.prior_gateway_usage": {"old": {"calls": 5, "files": ["a"]},
                                                             "new": {"calls": 7, "files": ["a", "b"]}}}
    for prior in ({"calls": 4, "files": ["a"]}, {"calls": 5, "files": ["b", "a"]}, {"calls": 5}, "b"):
        _, bad = dr.config_reseal_diff(old, {**old, "limits": {"prior_gateway_usage": prior}})
        assert bad and bad[0].startswith("limits.prior_gateway_usage (append only")
    _, bad = dr.config_reseal_diff(old, {**old, "verifier": {"model": "n"}, "waves": {"target_papers": 100}})
    assert bad == ["verifier.model (not re-sealable)", "waves.seed (not re-sealable)"]
    ok, bad = dr.config_reseal_diff({"waves": {}}, {"waves": {"target_papers": 200}})       # absent = the default
    assert not bad and ok == {"waves.target_papers": {"old": 100, "new": 200}}
    _, bad = dr.config_reseal_diff(old, {**old, "waves": {"target_papers": True, "seed": 1}})
    assert bad and "increase only" in bad[0]


def test_reseal_diff_caps_are_changed_never_dropped() -> None:
    """A whitelisted cap may change but never vanish, go null or change type; a key under a
    whitelisted parent that is not itself whitelisted is refused; prior_gateway_usage stays
    append-only even in its dict form (no unknown keys, no malformed numbers)."""
    old = {"waves": {"target_papers": 100, "c_first": True},
           "limits": {"max_calls": 1892, "notice_overrun": 0.1, "output_token_cap": 3, "call_cap": 2500,
                      "prior_gateway_usage": ["a", "b"]}}

    def diff(**limits: Any) -> tuple[dict[str, Any], list[str]]:
        new = json.loads(json.dumps(old))
        for k, v in limits.items():
            if v is ...:
                new["limits"].pop(k)
            else:
                new["limits"][k] = v
        return dr.config_reseal_diff(old, new)

    for k in ("max_calls", "output_token_cap"):
        for v in (..., None, "9e9", True, 0, -1, 1.5, {"x": 1}):
            ok, bad = diff(**{k: v})
            assert not ok and bad and bad[0].startswith(f"limits.{k} (a positive int"), (k, v)
        ok, bad = diff(**{k: 5000})
        assert not bad and list(ok) == [f"limits.{k}"]
    for v in (..., None, -0.1, float("inf"), "0.2", False):
        _, bad = diff(notice_overrun=v)
        assert bad and bad[0].startswith("limits.notice_overrun (a number >= 0"), v
    assert not diff(notice_overrun=0)[1] and not diff(notice_overrun=0.25)[1]
    assert not diff(call_cap=9999)[1]
    assert diff(foo=1)[1] == ["limits.foo (not re-sealable)"]
    for prior in ({"files": ["a", "b"], "zzz": 5}, {"files": ["a", "b"], "calls": "x"},
                  {"files": ["a", "b"], "calls": -1}, {"files": "a"}, 5):
        _, bad = diff(prior_gateway_usage=prior)
        assert bad and bad[0].startswith("limits.prior_gateway_usage (malformed"), prior
    assert not diff(prior_gateway_usage={"files": ["a", "b", "c"], "calls": 5})[1]
    for k, v in (("c_first", False), ("max_waves", 9)):
        new = json.loads(json.dumps(old))
        new["waves"][k] = v
        assert dr.config_reseal_diff(old, new)[1] == [f"waves.{k} (not re-sealable)"]


def test_reseal_diff_budget_caps_raise_only() -> None:
    """call_cap and input_token_cap (cumulative budget caps) may only go up: a decrease, an
    equal-or-lower value, removal, null, a retype or a non-positive value is refused; a sealed
    config that left the cap out is compared with DEFAULTS; a sealed null (no cap) cannot be
    re-sealed. Both raise passes and both refusals name the key."""
    old = {"limits": {"call_cap": 2500, "input_token_cap": 4_000_000, "max_calls": 10}}

    def diff(**limits: Any) -> tuple[dict[str, Any], list[str]]:
        new = json.loads(json.dumps(old))
        for k, v in limits.items():
            if v is ...:
                new["limits"].pop(k)
            else:
                new["limits"][k] = v
        return dr.config_reseal_diff(old, new)

    for k, sealed in (("call_cap", 2500), ("input_token_cap", 4_000_000)):
        ok, bad = diff(**{k: sealed + 1})
        assert not bad and ok == {f"limits.{k}": {"old": sealed, "new": sealed + 1}}
        for v in (sealed - 1, 1, ..., None, 0, -1, True, float(sealed * 2), str(sealed * 2), [sealed * 2]):
            ok, bad = diff(**{k: v})
            assert not ok and bad and bad[0].startswith(f"limits.{k} (raise only"), (k, v)
    ok, bad = diff(call_cap=3000, input_token_cap=5_000_000, max_calls=5)    # count may still go down
    assert not bad and set(ok) == {"limits.call_cap", "limits.input_token_cap", "limits.max_calls"}
    # absent in the sealed config = DEFAULTS
    for k in ("call_cap", "input_token_cap"):
        d = dr.DEFAULTS["limits"][k]
        ok, bad = dr.config_reseal_diff({"limits": {}}, {"limits": {k: d + 1}})
        assert not bad and ok == {f"limits.{k}": {"old": d, "new": d + 1}}
        _, bad = dr.config_reseal_diff({"limits": {}}, {"limits": {k: d - 1}})
        assert bad and bad[0].startswith(f"limits.{k} (raise only")
        _, bad = dr.config_reseal_diff({"limits": {k: None}}, {"limits": {k: 10**12}})
        assert bad and bad[0].startswith(f"limits.{k} (raise only")
