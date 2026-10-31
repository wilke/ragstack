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
    path, cfg = world(tmp, n_papers=n_papers)
    base._with(path, waves=waves)
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
    ("How many cores per sample were drilled in group 2 to measure retention time?", True),
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
