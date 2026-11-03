"""Alias study judge pipeline: pairs on demand by stage (dedup, gold dropped, arms, seeded order,
label once, blindness), the composed rule, the one-retry cap on Argon and Opus (resume-safe), the
drift sample and alarm, the served-model check, the size-notice gate, the caps, PAUSE, the batch
order, stage A's commit and the analysis gate. Fake judges, no gateway, no model."""
from __future__ import annotations

import asyncio
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
import alias_study_dryrun as dry  # noqa: E402
import alias_study_fixture as fx  # noqa: E402
import alias_study_judge as aj  # noqa: E402
import alias_study_run as ar  # noqa: E402

PROV = {"commit": "c" * 40, "dirty": False}
GW = "http://gw.invalid:18799/v1"


def config(w: fx.World, **over: Any) -> dict[str, Any]:
    cfg = {"out_dir": str(w.tmp / "judge"), "runs_dir": str(w.runs), "records": str(w.records_path),
           "chunk_exports": {k: str(v) for k, v in w.exports.items()}, "glossary": str(w.glossary),
           "batch_size": 40, "caps": {"argon_share": 1.0, "opus_share": 1.0}, "dry_run": True,
           "judge_c": {"base_url": "http://c.invalid/v1", **{k: v for k, v in aj.FIXED["C"].items() if k != "variant"}},
           "judge_e": {"base_url": "http://e.invalid/v1", **{k: v for k, v in aj.FIXED["E"].items() if k != "variant"}},
           "tiebreak": {"base_url": GW, "empty_retry_s": 0.0, **{k: v for k, v in aj.FIXED["argon"].items() if k != "tag"}},
           "drift_ref": {"base_url": GW, "empty_retry_s": 0.0, **{k: v for k, v in aj.FIXED["opus"].items() if k != "tag"}}}
    cfg.update(over)
    return cfg


def world(tmp: Path, **over: Any) -> tuple[fx.World, Path]:
    w = fx.World(tmp)
    w.write_staged_runs("A")
    cp = tmp / "jcfg.json"
    cp.write_text(json.dumps(config(w, **over)))
    return w, cp


def main(cp: Path, *a: str, http: Any = None) -> int:
    return aj.main(["--config", str(cp), *a], http_factory=(lambda: http) if http is not None else None, prov=PROV)


def label(cp: Path, b: int, be: str, http: Any, ack: bool = True) -> int:
    assert main(cp, "preflight", "--batch", str(b), "--backend", be, http=http) == 0
    return main(cp, "label", "--batch", str(b), "--backend", be, *(["--notice-acknowledged"] if ack else []), http=http)


def out_of(w: fx.World) -> Path:
    return w.tmp / "judge"


def stage_batches(w: fx.World, stage: str) -> list[int]:
    return json.loads((out_of(w) / "batches.json").read_text())["stages"][stage]


def judge_stage(w: fx.World, cp: Path, stage: str, fake: Any) -> None:
    assert main(cp, "build-pairs", "--stage", stage) == 0
    for b in stage_batches(w, stage):
        for be in aj.BACKENDS:
            assert label(cp, b, be, fake) == 0, (b, be)
        assert main(cp, "finalize", "--batch", str(b)) == 0


def through_commit(tmp: Path, **over: Any) -> tuple[fx.World, Path, dry.FakeJudges]:
    w, cp = world(tmp, **over)
    fake = dry.FakeJudges()
    judge_stage(w, cp, "A", fake)
    assert main(cp, "commit-stage-a") == 0
    w.write_staged_runs("B")
    return w, cp, fake


def expected_demand(w: fx.World, legs: list[tuple[str, str, str, int]], need: str) -> set[tuple[str, str]]:
    """The on-demand rule computed from the leg rows, independently of the pipeline."""
    want: set[tuple[str, str]] = set()
    for c, g, r, rep in legs:
        leg = asc.leg_id(c, g, r, rep)
        for rec in w.records:
            top = w.mixed_top10(leg, rec)
            gold = rec["gold_chunks"][0]
            if need == "found10" and gold not in top:
                want |= {(rec["record_id"], x) for x in top}
            if need == "rr10" and gold in top:
                want |= {(rec["record_id"], x) for x in top[:top.index(gold)]}
    return want


# ------------------------------------------------------------------ pairs on demand

def test_demanded_rule() -> None:
    rec = {"gold_chunks": ["g"]}
    assert aj.demanded(rec, {"ranked_chunk_ids": ["a", "b", "c"]}, "found10") == ["a", "b", "c"]
    assert aj.demanded(rec, {"ranked_chunk_ids": ["a", "b", "c"]}, "rr10") == []
    assert aj.demanded(rec, {"ranked_chunk_ids": ["a", "b", "g", "c"]}, "found10") == []
    assert aj.demanded(rec, {"ranked_chunk_ids": ["a", "b", "g", "c"]}, "rr10") == ["a", "b"]
    assert aj.demanded(rec, {"ranked_chunk_ids": [f"x{i}" for i in range(12)] + ["g"]}, "found10") == \
        [f"x{i}" for i in range(10)]                         # gold at rank 13: outside the top 10
    assert aj.demanded(rec, {"failed": True, "ranked_chunk_ids": None}, "found10") == []


def test_stage_a_pairs_are_the_found10_demand_of_the_14_legs(tmp_path: Path) -> None:
    w, cp = world(tmp_path)
    assert main(cp, "build-pairs", "--stage", "A") == 0
    out = out_of(w)
    pairs = asc.read_jsonl(out / "pairs.jsonl")
    man = json.loads((out / "pairs-manifest.json").read_text())
    got = {(p["record_id"], p["chunk_id"]) for p in pairs}
    assert got == expected_demand(w, ar.stage_legs("A"), "found10")
    assert len(pairs) == len(got) == man["stages"]["A"]["counts"]["pairs"]
    assert man["stages"]["A"]["counts"]["legs"] == 14 and len(man["stages"]["A"]["legs"]) == 14
    assert {lg.split("|")[2].split("#")[0] for lg in man["stages"]["A"]["legs"]} == {"default", "norerank"}
    assert man["stages"]["A"]["counts"]["instances_before_dedup"] > len(pairs)
    assert all(p["chunk_id"] != w.by_id[p["record_id"]]["gold_chunks"][0] for p in pairs)
    assert {p["stage"] for p in pairs} == {"A"} and {p["need"] for p in pairs} == {"found10"}
    assert {p["arm"] for p in pairs} == {"base", "neighbour", "random"}
    assert [p["order"] for p in pairs] == sorted(p["order"] for p in pairs)
    assert pairs[0]["order"] == asc.seeded_key(asc.SEEDS["judge_order"], pairs[0]["pair_id"])
    assert stage_batches(w, "A") == list(range(1, man["batches"] + 1))
    with pytest.raises(SystemExit, match="frozen"):
        main(cp, "build-pairs", "--stage", "A")


def test_pairs_need_the_stage_legs_complete(tmp_path: Path) -> None:
    w = fx.World(tmp_path)
    w.write_staged_runs("A", status={"random2/none#2": "paused"})
    cp = tmp_path / "jcfg.json"
    cp.write_text(json.dumps(config(w)))
    with pytest.raises(SystemExit, match="not complete"):
        main(cp, "build-pairs", "--stage", "A")


def test_failed_rows_add_no_pairs(tmp_path: Path) -> None:
    w = fx.World(tmp_path)
    w.write_staged_runs("A", failed=lambda leg, rec: True)
    recs = asc.load_records(w.records_path)
    rows = aj.leg_rows(w.runs, ar.stage_legs("A"))
    chunks, arm_of = aj.chunk_arms(config(w))
    pairs, counts = aj.build_pairs(recs, rows, chunks, arm_of)
    assert pairs == [] and counts["instances_before_dedup"] == 0


def test_stage_b_pairs_wait_for_the_commit(tmp_path: Path) -> None:
    w, cp = world(tmp_path)
    fake = dry.FakeJudges()
    judge_stage(w, cp, "A", fake)
    w.write_staged_runs("B")
    with pytest.raises(SystemExit, match="stage A committed first"):
        main(cp, "build-pairs", "--stage", "B")
    assert len(json.loads((out_of(w) / "batches.json").read_text())["stages"]) == 1


def test_stage_b_registers_only_new_pairs_and_never_relabels(tmp_path: Path) -> None:
    w, cp, fake = through_commit(tmp_path)
    out = out_of(w)
    reg_a = asc.read_jsonl(out / "pairs.jsonl")
    final_a = (out / "final.jsonl").read_bytes()
    assert main(cp, "build-pairs", "--stage", "B") == 0
    reg = asc.read_jsonl(out / "pairs.jsonl")
    assert reg[: len(reg_a)] == reg_a                                   # append-only
    ids = [(p["record_id"], p["chunk_id"]) for p in reg]
    assert len(ids) == len(set(ids))                                    # a pair is registered once
    new = reg[len(reg_a):]
    assert {p["stage"] for p in new} == {"B"}
    a_ids = {(p["record_id"], p["chunk_id"]) for p in reg_a}
    want_f = expected_demand(w, ar.stage_legs("B"), "found10")
    want_r = expected_demand(w, ar.study_legs(), "rr10")
    assert {(p["record_id"], p["chunk_id"]) for p in new} == (want_f | want_r) - a_ids
    assert {(p["record_id"], p["chunk_id"]) for p in new if p["need"] == "found10"} == want_f - a_ids
    assert {(p["record_id"], p["chunk_id"]) for p in new if p["need"] == "rr10"} == want_r - want_f - a_ids
    man = json.loads((out / "pairs-manifest.json").read_text())
    already = man["stages"]["B"]["counts"]["already_registered"]
    assert sum(already.values()) > 0                                    # B asked for pairs A had labelled
    with pytest.raises(SystemExit, match="frozen"):
        main(cp, "build-pairs", "--stage", "B")
    for b in stage_batches(w, "B"):
        assert b > max(stage_batches(w, "A"))
        for be in aj.BACKENDS:
            assert label(cp, b, be, fake) == 0
        assert main(cp, "finalize", "--batch", str(b)) == 0
    assert (out / "final.jsonl").read_bytes()[: len(final_a)] == final_a
    fin = [r for r in asc.read_jsonl(out / "final.jsonl") if r["k"].startswith("pair|")]
    assert len(fin) == len({r["pair_id"] for r in fin}) == len(reg)
    assert aj.verify_stage_a_commit(out / "stage-a-commit.json")["batches_A"] == len(stage_batches(w, "A"))
    summ = json.loads((out / "judge-summary.json").read_text())
    assert summ["stages"]["A"]["pairs_finalized"] == len(reg_a) and summ["stage_a_committed"] is True


def test_finalize_refuses_a_pair_that_has_a_final_label(tmp_path: Path) -> None:
    w, cp = world(tmp_path, batch_size=20)
    fake = dry.FakeJudges()
    assert main(cp, "build-pairs", "--stage", "A") == 0
    assert len(stage_batches(w, "A")) >= 2
    for be in aj.BACKENDS:
        assert label(cp, 1, be, fake) == 0
    assert main(cp, "finalize", "--batch", "1") == 0
    for be in aj.BACKENDS:
        assert label(cp, 2, be, fake) == 0
    victim = json.loads((out_of(w) / "batches.json").read_text())["batches"][1][0]
    with (out_of(w) / "final.jsonl").open("a") as fh:
        fh.write(json.dumps({"k": f"pair|{victim}", "pair_id": victim, "label": 1, "source": "agree"}) + "\n")
    with pytest.raises(SystemExit, match="labelled once"):
        main(cp, "finalize", "--batch", "2")


# ------------------------------------------------------------------ blindness

def test_prompt_is_blind_to_arm_stage_and_cell(tmp_path: Path) -> None:
    w, cp = world(tmp_path)
    main(cp, "build-pairs", "--stage", "A")
    pl = aj.Pipeline(aj.load_config(cp), provenance=PROV)
    reg = pl.registry()
    # the registry row names no leg, retriever or glossary arm: only the passage paper's arm
    assert set(reg[0]) == {"pair_id", "record_id", "chunk_id", "content_sha256", "question", "term", "q_paper",
                           "c_paper", "record_kind", "arm", "stage", "need", "order"}
    p = next(x for x in reg if x["arm"] == "neighbour")
    world_ = aj.StudyWorld(aj.harness_cfg(pl.cfg, "C"), pl.chunks(), pl.glossary(), [p])
    a = world_.prompt(p)[0]
    b = world_.prompt({**p, "arm": "base", "stage": "B", "need": "rr10", "order": "0", "record_kind": "C",
                       "pair_id": "zz"})[0]
    assert a == b
    for word in ("neighbour", "random", "bm25", "norerank", "merged", "placebo", "rank", "stage"):
        assert word not in a.split("PASSAGE:")[0].lower() + a.split(">>>")[-1].lower()


def test_judge_refuses_a_placebo_glossary(tmp_path: Path) -> None:
    w = fx.World(tmp_path)
    placebo = tmp_path / "placebo.jsonl"
    rows, _ = ar.build_placebo(asc.read_jsonl(w.glossary))
    fx.write_jsonl(placebo, rows)
    with pytest.raises(SystemExit, match="placebo"):
        aj.read_glossaries([w.glossary, placebo])
    assert aj.read_glossaries([w.glossary])


def test_glossary_files_per_arm_feed_the_definitions_block(tmp_path: Path) -> None:
    """Review of #904: an expanded arm's passages need their own papers' entries. With only the
    base glossary, every neighbour prompt reads "(none listed)" and the manifest says so."""
    w, cp = world(tmp_path)
    assert main(cp, "build-pairs", "--stage", "A") == 0
    man = json.loads((out_of(w) / "pairs-manifest.json").read_text())
    cov = man["stages"]["A"]["counts"]["definitions_coverage"]
    assert cov["neighbour"]["share"] == 0.0 and cov["base"]["share"] == 1.0
    added = fx.write_jsonl(tmp_path / "glossary-neighbour.jsonl",
                           [{"alias": "AB", "primary": "alpha bravo", "kind": "abbreviation", "paper": p,
                             "doc_id": f"d-{p}"} for p in w.added["neighbour"]])
    tmp2 = tmp_path / "two"
    tmp2.mkdir()
    w2, cp2 = world(tmp2, glossary=[str(w.glossary), str(added)])
    assert main(cp2, "build-pairs", "--stage", "A") == 0
    man2 = json.loads((tmp2 / "judge" / "pairs-manifest.json").read_text())
    assert man2["stages"]["A"]["counts"]["definitions_coverage"]["neighbour"]["share"] == 1.0
    assert len(man2["inputs_sha256"]["glossaries"]) == 2
    pl = aj.Pipeline(aj.load_config(cp2), provenance=PROV)
    p = next(x for x in pl.registry() if x["arm"] == "neighbour")
    prompt = aj.StudyWorld(aj.harness_cfg(pl.cfg, "C"), pl.chunks(), pl.glossary(), [p]).prompt(p)[0]
    assert '"AB" stands for "alpha bravo"' in prompt


# ------------------------------------------------------------------ rule pieces

def row(verdicts: list[Any]) -> dict[str, Any]:
    return {"verdicts": verdicts}


def test_final_label_rule() -> None:
    assert aj.final_label(row([1, 1, 0, 1, None]), row([1]), None) == (1, "agree")
    assert aj.final_label(row([1, 1, 1, 1, 1]), row([0]), {"verdict": False}) == (0, "tiebreak")
    assert aj.final_label(row([1, 1, 0, 0, None]), row([1]), {"verdict": True}) == (1, "tiebreak")  # no C majority
    assert aj.final_label(row([0, 0, 0, 0, 0]), row([None]), {"verdict": None, "status": "ok"},
                          {"verdict": False}) == (0, "tiebreak_r2")                  # E invalid; v1's re-issue file
    assert aj.final_label(row([1, 0, 1, 0, 1]), row([0]), {"verdict": None}) == (None, "unjudged")


def test_drift_sample_allocation() -> None:
    pairs = [{"pair_id": f"b{i}", "arm": "base"} for i in range(170)] + \
            [{"pair_id": f"n{i}", "arm": "neighbour"} for i in range(25)] + \
            [{"pair_id": f"r{i}", "arm": "random"} for i in range(2)]
    s = aj.drift_sample(1, pairs, 0.05)               # round(9.85) = 10 -> 4 / 3 / 3, random capped at 2
    arms = [p[0] for p in s]
    # random's shortfall of 1 goes to the arms that still have pairs (remainder to base first)
    assert (arms.count("b"), arms.count("n"), arms.count("r")) == (5, 3, 2) and len(s) == 10
    assert s == aj.drift_sample(1, pairs, 0.05) and set(s) != set(aj.drift_sample(2, pairs, 0.05))


def test_drift_size_rounds_half_up_exactly() -> None:
    assert aj.drift_size(2000, 0.05) == 100
    assert aj.drift_size(2010, 0.05) == 101            # 100.5 -> 101 (half up, not to even)
    assert aj.drift_size(2009, 0.05) == 100 and aj.drift_size(30, 0.05) == 2   # 100.45, 1.5
    assert aj.drift_size(29, 0.05) == 1                                          # 1.45


def test_drift_allocation_reallocates_until_reached_or_exhausted() -> None:
    assert aj.drift_allocation(100, {"base": 1000, "neighbour": 1000, "random": 1000}) == \
        {"base": 34, "neighbour": 33, "random": 33}
    # random holds 5: its shortfall 28 splits 14 / 14 over base and neighbour
    assert aj.drift_allocation(100, {"base": 1000, "neighbour": 1000, "random": 5}) == \
        {"base": 48, "neighbour": 47, "random": 5}
    # a second round: neighbour also runs dry, base takes the rest
    assert aj.drift_allocation(100, {"base": 1000, "neighbour": 20, "random": 5}) == \
        {"base": 75, "neighbour": 20, "random": 5}
    # every stratum exhausted before the size is reached
    assert aj.drift_allocation(100, {"base": 30, "neighbour": 20, "random": 5}) == \
        {"base": 30, "neighbour": 20, "random": 5}
    assert aj.drift_allocation(0, {"base": 3}) == {"base": 0, "neighbour": 0, "random": 0}


def drift_inputs(n: int, agree: int, arm: str = "base", filtered: int = 0
                 ) -> tuple[dict[str, Any], dict[str, Any], list[str], dict[str, str]]:
    fin: dict[str, Any] = {}
    op: dict[str, Any] = {}
    arm_of: dict[str, str] = {}
    for i in range(n + filtered):
        pid = f"{arm}{i}"
        fin[pid] = {"label": 1}
        op[pid] = {"verdict": None if i >= n else (True if i < agree else False)}
        arm_of[pid] = arm
    return fin, op, list(fin), arm_of


def test_drift_alarm_thresholds_and_minimum() -> None:
    cfg = aj.DEFAULTS["drift"]
    ok = aj.drift_stats(*drift_inputs(100, 97), cfg)
    assert not ok["alarm"]
    fire = aj.drift_stats(*drift_inputs(100, 96), cfg)
    assert fire["alarm"] and fire["scopes"]["overall"]["fires"]
    small = aj.drift_stats(*drift_inputs(99, 90), cfg)
    assert not small["alarm"] and not small["scopes"]["overall"]["evaluable"]
    filt = aj.drift_stats(*drift_inputs(99, 90, filtered=30), cfg)
    assert not filt["alarm"] and filt["scopes"]["overall"]["filtered"] == 30
    assert fire["scopes"]["base"]["false_yes"]["k"] == 4 and fire["scopes"]["base"]["false_yes"]["n"] == 4
    # §3.4: among the 100 final *answers* labels Opus answered, 4 are Opus-*not*
    fya = fire["scopes"]["base"]["false_yes_among_answers"]
    assert (fya["k"], fya["n"]) == (4, 100) and fya["wilson95"][1] > 0.04


def test_drift_alarm_per_arm() -> None:
    cfg = aj.DEFAULTS["drift"]
    fa, oa, sa, aa = drift_inputs(400, 400, "base")
    fb, ob, sb, ab = drift_inputs(100, 94, "neighbour")
    r = aj.drift_stats({**fa, **fb}, {**oa, **ob}, sa + sb, {**aa, **ab}, cfg)                    # overall 494/500 = 0.988 >= 0.97
    assert r["alarm"] and r["scopes"]["neighbour"]["fires"] and not r["scopes"]["overall"]["fires"]


def test_config_pins_the_judges(tmp_path: Path) -> None:
    w = fx.World(tmp_path)
    good = config(w)
    aj.validate_config(good)
    for blk, k, v in (("judge_c", "n", 3), ("judge_c", "model", "Qwen3.5"), ("judge_e", "enable_thinking", False),
                      ("tiebreak", "judge_version", "alias-judge/1"), ("drift_ref", "max_tokens", 4096),
                      ("judge_c", "require_served_model", False)):
        bad = json.loads(json.dumps(good))
        bad[blk][k] = v
        with pytest.raises(SystemExit):
            aj.validate_config(bad)


def test_main_config_pins_the_prereg_and_a_pilot_dir_stays_the_pilot_s(tmp_path: Path) -> None:
    w = fx.World(tmp_path)
    with pytest.raises(SystemExit, match="prereg"):
        aj.validate_config(config(w, dry_run=False))
    aj.Pipeline(aj.validate_config(config(w, dry_run=False, pilot=True)), provenance=PROV)
    pr = tmp_path / "prereg.md"
    pr.write_text("frozen\n")
    main_cfg = aj.validate_config(config(w, dry_run=False, prereg={"file": str(pr), "sha256": asc.sha256_file(pr)}))
    with pytest.raises(SystemExit, match="STUDY-MODE"):
        aj.Pipeline(main_cfg, provenance=PROV)
    pr.write_text("edited\n")
    with pytest.raises(SystemExit, match="HARD STOP"):
        aj.validate_config(config(w, dry_run=False, prereg={"file": str(pr), "sha256": "0" * 64}))


# ------------------------------------------------------------------ the pipeline with fake judges

class WrongModel(dry.FakeJudges):
    async def post(self, url: str, json: dict[str, Any]) -> Any:  # noqa: A002
        r = await super().post(url, json)
        body = r.json()
        body["model"] = "Qwen3.6-35B-A3B-other"
        return dry.Resp(200, body)


def test_whole_batch_cycle_and_controls(tmp_path: Path) -> None:
    w, cp = world(tmp_path)
    fake = dry.FakeJudges()
    assert main(cp, "build-pairs", "--stage", "A") == 0
    out = out_of(w)
    assert len(stage_batches(w, "A")) >= 2
    # the notice gate: no acknowledgement, no labels
    with pytest.raises(SystemExit, match="notice"):
        label(cp, 1, "C", fake, ack=False)
    # batch order: batch 2 before batch 1 is finalized
    with pytest.raises(SystemExit, match="not finalized"):
        label(cp, 2, "C", fake)
    # the tiebreaker set is known only after C and E
    with pytest.raises(SystemExit, match="C or E"):
        main(cp, "preflight", "--batch", "1", "--backend", "argon", http=fake)
    for be in aj.BACKENDS:
        assert label(cp, 1, be, fake) == 0, be
    # a preflight of batch 1 does not admit batch 2
    assert main(cp, "finalize", "--batch", "1") == 0
    with pytest.raises(SystemExit, match="plan changed|preflight"):
        main(cp, "label", "--batch", "2", "--backend", "C", "--notice-acknowledged", http=fake)
    fin = {r["pair_id"]: r for r in asc.read_jsonl(out / "final.jsonl") if r["k"].startswith("pair|")}
    srcs = {r["source"] for r in fin.values()}
    assert "agree" in srcs and "tiebreak" in srcs
    r0 = next(iter(fin.values()))
    assert r0["stage"] == "A" and r0["need"] == "found10" and r0["c_model"] == "Qwen3.6-35B-A3B"
    assert r0["e_model"] == "Qwen3.8-27B" and r0["judge_versions"]["opus"] == "alias-judge/1"
    summ = json.loads((out / "judge-summary.json").read_text())
    assert summ["pairs_finalized"] == len(fin) and summ["batches_finalized"] == [1]
    with pytest.raises(SystemExit, match="finalized"):
        main(cp, "finalize", "--batch", "1")
    # Argon unparseable replies were re-issued once, inside the tiebreaker's own row
    tb = asc.read_jsonl(out / "labels-gw-argon.jsonl")
    assert all(r["attempts"] in (1, 2) and r["retry_cap"] == 2 for r in tb)
    assert all(r["verdict"] is not None or r["attempt_statuses"] == ["ok", "ok"] for r in tb)


def test_served_model_mismatch_stops_and_stores_nothing(tmp_path: Path) -> None:
    w, cp = world(tmp_path)
    main(cp, "build-pairs", "--stage", "A")
    assert label(cp, 1, "C", WrongModel()) == aj.EXIT_HARD_STOP
    out = out_of(w)
    assert (out / "PAUSE").exists()
    assert not (out / "labels-qwen-C.jsonl").exists() or asc.read_jsonl(out / "labels-qwen-C.jsonl") == []


def test_caps_refuse(tmp_path: Path) -> None:
    w, cp = world(tmp_path, caps={"argon_share": 0.04, "opus_share": 0.0})
    main(cp, "build-pairs", "--stage", "A")
    with pytest.raises(SystemExit, match="opus cap"):
        main(cp, "preflight", "--batch", "1", "--backend", "opus", http=dry.FakeJudges())
    pl = aj.Pipeline(aj.load_config(cp), provenance=PROV)
    total = len(pl.registry())
    pl.check_caps("argon", int(0.04 * total))
    with pytest.raises(SystemExit, match="argon cap"):
        pl.check_caps("argon", int(0.04 * total) + 1)


def test_opus_cap_default_is_7_percent_and_counts_reissued_calls(tmp_path: Path) -> None:
    assert aj.DEFAULTS["caps"]["opus_share"] == 0.07
    w, cp = world(tmp_path, batch_size=100000, drift={"share": 0.5})
    fake = dry.FakeJudges()
    assert main(cp, "build-pairs", "--stage", "A") == 0
    for be in aj.BACKENDS:
        assert label(cp, 1, be, fake) == 0, be
    pl = aj.Pipeline(aj.load_config(cp), provenance=PROV)
    sampled = len(pl.selection(1, "opus"))
    opus_http = fake.calls["Claude Opus 5.5"]
    # the fake answers some pairs empty: the harness re-issues them once, so HTTP calls > sampled
    # pairs (and at most twice them), and the cap's count is every HTTP call, re-issues included
    assert sampled < opus_http <= 2 * sampled
    assert pl.calls_so_far("opus") == opus_http
    cfg = aj.load_config(cp)
    cfg["caps"]["opus_share"] = (opus_http + 0.5) / len(pl.registry())
    tight = aj.Pipeline(cfg, provenance=PROV)
    tight.check_caps("opus", 0)
    with pytest.raises(SystemExit, match="opus cap"):
        tight.check_caps("opus", 1)


# ------------------------------------------------------------------ the one-retry cap (§6.3, R4)

class GatewayScript(dry.FakeJudges):
    """FakeJudges, with the Opus and Argon replies scripted per call number of each prompt."""

    def __init__(self, opus: Any = None, argon: Any = None) -> None:
        super().__init__()
        self.script = {"Claude Opus 5.5": opus, "Gemini 4 Argon": argon}
        self.per_prompt: dict[tuple[str, str], int] = {}

    async def post(self, url: str, json: dict[str, Any]) -> Any:  # noqa: A002
        model = json["model"]
        fn = self.script.get(model)
        if fn is None:
            return await super().post(url, json)
        self.calls[model] = self.calls.get(model, 0) + 1
        k = (model, json["messages"][-1]["content"])
        self.per_prompt[k] = self.per_prompt.get(k, 0) + 1
        text = fn(self.per_prompt[k])
        await asyncio.sleep(0)
        return dry.Resp(200, {"model": model, "choices": [{"index": 0, "message": {"content": text},
                                                           "finish_reason": "stop"}],
                              "usage": {"prompt_tokens": 300, "completion_tokens": 30 if text else 0}})


def labelled_through_tiebreak(tmp: Path, fake: Any, **over: Any) -> tuple[fx.World, Path, aj.Pipeline]:
    w, cp = world(tmp, batch_size=100000, drift={"share": 0.3}, **over)
    assert main(cp, "build-pairs", "--stage", "A") == 0
    for be in ("C", "E", "argon"):
        assert label(cp, 1, be, fake) == 0
    return w, cp, aj.Pipeline(aj.load_config(cp), provenance=PROV)


def test_opus_empty_twice_is_no_reference_after_exactly_two_calls(tmp_path: Path) -> None:
    fake = GatewayScript(opus=lambda n: "")
    w, cp, pl = labelled_through_tiebreak(tmp_path, fake)
    sampled = [p["pair_id"] for p in pl.selection(1, "opus")]
    assert label(cp, 1, "opus", fake) == 0
    assert fake.calls["Claude Opus 5.5"] == 2 * len(sampled)
    assert {n for k, n in fake.per_prompt.items() if k[0] == "Claude Opus 5.5"} == {2}
    rows = {r["pair_id"]: r for r in asc.read_jsonl(out_of(w) / "labels-gw-opus.jsonl")}
    assert set(rows) == set(sampled)
    assert all(r["verdict"] is None and r["attempts"] == 2 and r["no_verdict_after_cap"] for r in rows.values())
    # a resume makes no further call
    assert main(cp, "label", "--batch", "1", "--backend", "opus", "--notice-acknowledged", "--resume",
                http=fake) == 0
    assert fake.calls["Claude Opus 5.5"] == 2 * len(sampled)
    assert main(cp, "finalize", "--batch", "1") == 0
    fin = [r for r in asc.read_jsonl(out_of(w) / "final.jsonl") if r["k"].startswith("pair|") and r["drift_sampled"]]
    assert {r["opus_status"] for r in fin} == {"no_reference"} and {r["opus_attempts"] for r in fin} == {2}
    summ = json.loads((out_of(w) / "judge-summary.json").read_text())
    assert summ["drift"]["scopes"]["overall"]["filtered"] == len(sampled)
    assert summ["drift"]["scopes"]["overall"]["answered"] == 0 and not summ["drift"]["alarm"]
    assert summ["opus_no_reference"]["total"] == len(sampled)


def test_unparseable_then_answered_uses_the_second_reply(tmp_path: Path) -> None:
    fake = GatewayScript(opus=lambda n: "I cannot decide." if n == 1 else dry.json_verdict(True))
    w, cp, pl = labelled_through_tiebreak(tmp_path, fake)
    sampled = pl.selection(1, "opus")
    assert label(cp, 1, "opus", fake) == 0
    rows = asc.read_jsonl(out_of(w) / "labels-gw-opus.jsonl")
    assert fake.calls["Claude Opus 5.5"] == 2 * len(sampled)
    assert all(r["verdict"] is True and r["attempts"] == 2 and r["attempt_statuses"] == ["ok", "ok"] for r in rows)


def test_argon_empty_twice_is_unjudged(tmp_path: Path) -> None:
    fake = GatewayScript(argon=lambda n: "")
    w, cp = world(tmp_path, batch_size=100000)
    assert main(cp, "build-pairs", "--stage", "A") == 0
    for be in ("C", "E"):
        assert label(cp, 1, be, fake) == 0
    pl = aj.Pipeline(aj.load_config(cp), provenance=PROV)
    esc = pl.selection(1, "argon")
    assert esc
    for be in ("argon", "opus"):
        assert label(cp, 1, be, fake) == 0
    assert fake.calls["Gemini 4 Argon"] == 2 * len(esc)
    assert main(cp, "finalize", "--batch", "1") == 0
    fin = {r["pair_id"]: r for r in asc.read_jsonl(out_of(w) / "final.jsonl") if r["k"].startswith("pair|")}
    assert all(fin[p["pair_id"]]["source"] == "unjudged" and fin[p["pair_id"]]["label"] is None
               and fin[p["pair_id"]]["tiebreak_attempts"] == 2 for p in esc)


def test_the_cap_is_counted_from_disk_across_a_pause(tmp_path: Path) -> None:
    """A run killed between a pair's first and second call (the first reply in the attempts
    file, or only in the calls log) gets one more call, never two; a pair with two answered
    calls on disk gets none."""
    fake = GatewayScript(opus=lambda n: "")
    w, cp, pl = labelled_through_tiebreak(tmp_path, fake)
    sel = pl.selection(1, "opus")
    sampled = [p["pair_id"] for p in sel]
    assert len(sampled) >= 3
    one_att, one_log, two_log = sampled[:3]
    out = out_of(w)
    k = "gw-opus|{}|r1"
    with (out / "attempts-gw-opus.jsonl").open("a") as fh:
        fh.write(json.dumps({"k": k.format(one_att) + "|a1", "pair_id": one_att, "attempt": 1, "status": "empty",
                             "verdict": None}) + "\n")
    with (out / "calls-gw-opus.jsonl").open("a") as fh:
        fh.write(json.dumps({"key": k.format(one_att), "status": "empty"}) + "\n")
        fh.write(json.dumps({"key": k.format(one_log), "status": "empty0"}) + "\n")
        fh.write(json.dumps({"key": k.format(two_log), "status": "empty"}) + "\n")
        fh.write(json.dumps({"key": k.format(two_log), "status": "truncated"}) + "\n")
        fh.write(json.dumps({"key": k.format(two_log), "status": "error:http_502"}) + "\n")   # not a reply
    assert label(cp, 1, "opus", fake) == 0
    world_ = pl.labeler(1, "opus").w
    per = {p["pair_id"]: fake.per_prompt.get(("Claude Opus 5.5", world_.prompt(p)[0]), 0) for p in sel}
    assert per[one_att] == 1 and per[one_log] == 1 and per[two_log] == 0
    assert all(per[p] == 2 for p in sampled[3:])
    rows = {r["pair_id"]: r for r in asc.read_jsonl(out / "labels-gw-opus.jsonl")}
    assert rows[two_log]["attempts"] == 2 and rows[two_log]["verdict"] is None
    assert all(rows[p]["attempts"] == 2 for p in sampled)


def test_the_call_cap_holds_inside_a_run(tmp_path: Path) -> None:
    """Re-issues cannot overrun the Opus cap: the segment's call cap is what is left of it."""
    fake = GatewayScript(opus=lambda n: "")
    w, cp, pl = labelled_through_tiebreak(tmp_path, fake)
    sampled = len(pl.selection(1, "opus"))
    cfg = json.loads(cp.read_text())
    cfg["caps"]["opus_share"] = (sampled + 1.5) / len(pl.registry())   # room for every pair once, plus one
    cp.write_text(json.dumps(cfg))
    assert label(cp, 1, "opus", fake) == aj.EXIT_PAUSED
    assert fake.calls["Claude Opus 5.5"] == sampled + 1
    assert "max_calls" in json.loads((out_of(w) / "totals-gw-opus.json").read_text())["segments"][-1]["status"]


class Disagree(dry.FakeJudges):
    """Opus says the opposite of every other judge: the drift alarm must fire."""

    async def post(self, url: str, json: dict[str, Any]) -> Any:  # noqa: A002
        model = json["model"]
        user = json["messages"][-1]["content"]
        v = dry.h("truth", user) % 4 == 0
        if model == "Claude Opus 5.5":
            v = not v
        n = int(json.get("n") or 1)
        await asyncio.sleep(0)
        return dry.Resp(200, {"model": model, "choices": [{"index": i, "message": {"content": dry.json_verdict(v)},
                                                           "finish_reason": "stop"} for i in range(n)]})


def test_drift_alarm_pauses_labelling(tmp_path: Path) -> None:
    w, cp = world(tmp_path, batch_size=100000, drift={"min_pairs": 3, "share": 0.2})
    fake = Disagree()
    main(cp, "build-pairs", "--stage", "A")
    for be in aj.BACKENDS:
        assert label(cp, 1, be, fake) == 0
    assert main(cp, "finalize", "--batch", "1") == aj.EXIT_HARD_STOP
    out = out_of(w)
    assert (out / "PAUSE").exists() and json.loads((out / "drift-alarm.json").read_text())["alarm"]
    with pytest.raises(SystemExit, match="PAUSE"):
        main(cp, "label", "--batch", "1", "--backend", "C", "--notice-acknowledged", http=fake)
    with pytest.raises(SystemExit, match="PAUSE"):
        main(cp, "commit-stage-a")


# ------------------------------------------------------------------ stage A's commit and the analysis gate

def test_commit_needs_every_stage_a_batch_finalized(tmp_path: Path) -> None:
    w, cp = world(tmp_path, batch_size=20)
    fake = dry.FakeJudges()
    assert main(cp, "build-pairs", "--stage", "A") == 0
    for be in aj.BACKENDS:
        assert label(cp, 1, be, fake) == 0
    assert main(cp, "finalize", "--batch", "1") == 0
    with pytest.raises(SystemExit, match="not finalized"):
        main(cp, "commit-stage-a")
    assert ar.read_commit_marker(w.runs) is None


def test_commit_writes_hits_shas_public_file_and_marker(tmp_path: Path) -> None:
    w, cp, _ = through_commit(tmp_path)
    out = out_of(w)
    marker = ar.read_commit_marker(w.runs)
    assert marker is not None and marker["commit_sha256"] == asc.sha256_file(out / "stage-a-commit.json")
    hits = asc.read_jsonl(out / "stage-a" / "hits.jsonl")
    assert len(hits) == 14 * len(w.records)
    fin = {(r["record_id"], r["chunk_id"]): r["label"] for r in asc.read_jsonl(out / "final.jsonl")
           if r["k"].startswith("pair|")}
    for h in hits:
        rec = w.by_id[h["record_id"]]
        top = w.mixed_top10(h["leg"], rec)
        gold_in = rec["gold_chunks"][0] in top
        assert h["hit_gold"] == int(gold_in)
        assert h["hit"] == int(gold_in or any(fin[(rec["record_id"], c)] == 1 for c in top))
    pub = json.loads((out / "stage-a-commit.public.json").read_text())
    forbidden = [r["record_id"] for r in w.records] + w.papers + list(asc.read_chunk_exports([w.base_export]))
    assert asc.public_problems(pub, forbidden, [r["question"] for r in w.records]) == []
    assert pub["counts"]["legs"] == 14 and pub["commit_sha256"] == marker["commit_sha256"]
    assert "rows:base|none|default" in pub["files"] and "final" in pub["files"]
    with pytest.raises(SystemExit, match="committed already"):
        main(cp, "commit-stage-a")


def test_analysis_gate_refuses_before_the_commit_and_the_spot_checks(tmp_path: Path) -> None:
    w, cp = world(tmp_path)
    fake = dry.FakeJudges()
    judge_stage(w, cp, "A", fake)
    with pytest.raises(SystemExit, match="before stage A is committed"):
        aj.analysis_gate(w.runs, dry_run=True)
    with pytest.raises(SystemExit, match="before stage A is committed"):
        main(cp, "check-analysis-gate")
    assert main(cp, "commit-stage-a") == 0
    assert aj.analysis_gate(w.runs, dry_run=True)["allowed"]
    with pytest.raises(SystemExit, match="records spot check"):
        aj.analysis_gate(w.runs)
    sr, sj = tmp_path / "spot-records.json", tmp_path / "spot-judge.json"
    sr.write_text("{}")
    with pytest.raises(SystemExit, match="judge spot check"):
        aj.analysis_gate(w.runs, {"records": str(sr)})
    sj.write_text("{}")
    g = aj.analysis_gate(w.runs, {"records": str(sr), "judge": str(sj)})
    assert g["allowed"] and set(g["spot_checks_sha256"]) == {"records", "judge"} and not g["spot_checks_waived"]
    # a stage-A label rewritten after the commit: no analysis
    fp = out_of(w) / "final.jsonl"
    data = fp.read_bytes()
    fp.write_bytes(data.replace(b'"label": 0', b'"label": 1', 1))
    with pytest.raises(SystemExit, match="changed since the commit"):
        aj.analysis_gate(w.runs, {"records": str(sr), "judge": str(sj)})
    fp.write_bytes(data)
    assert aj.analysis_gate(w.runs, {"records": str(sr), "judge": str(sj)})["allowed"]
