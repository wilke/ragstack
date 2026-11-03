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
    assert got == expected_demand(w, ar.judging_legs("A"), "found10")
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


def test_found10_pairs_are_labelled_before_rr10_pairs(tmp_path: Path) -> None:
    """§6.2: within a stage, every found@10 pair comes before any RR@10 pair in the labelling
    order (each group in the seeded order)."""
    w = fx.World(tmp_path)
    recs = asc.load_records(w.records_path)
    chunks, arm_of = aj.chunk_arms(config(w))
    p0, p1 = w.papers[0], w.papers[1]
    rows = []
    for rec in recs:
        gold = rec["gold_chunks"][0]
        others = [c for c in chunks if c != gold and chunks[c]["paper"] in (p0, p1)]
        rows.append(("A|x|default", {"record_id": rec["record_id"], "ranked_chunk_ids": others[:10]}))
        rows.append(("B|x|default", {"record_id": rec["record_id"],
                                     "ranked_chunk_ids": [c for c in chunks if c not in others[:10]][:2] + [gold]}))
    pairs, _ = aj.build_pairs(recs, rows, chunks, arm_of, needs={"A|x|default": ("found10",),
                                                                 "B|x|default": ("rr10",)}, stage="B")
    needs = [p["need"] for p in pairs]
    assert set(needs) == {"found10", "rr10"} and needs == sorted(needs, key=aj.NEEDS.index)
    for nd in aj.NEEDS:
        grp = [p["order"] for p in pairs if p["need"] == nd]
        assert grp == sorted(grp)
    assert aj.batches(pairs, 7)[0][0] == pairs[0]["pair_id"] and pairs[0]["need"] == "found10"


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
    rows = aj.leg_rows(w.runs, ar.judging_legs("A"))
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
    needs = [p["need"] for p in new]                                    # found@10 first, then RR@10
    assert needs == sorted(needs, key=aj.NEEDS.index)
    a_ids = {(p["record_id"], p["chunk_id"]) for p in reg_a}
    want_f = expected_demand(w, ar.judging_legs("B"), "found10")
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
    assert aj.verify_marker(w.runs)["batches_A"] == len(stage_batches(w, "A"))
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


def arm_glossaries(w: fx.World) -> dict[str, str]:
    """One merged-glossary file per corpus arm (the base file, and the added papers' entries)."""
    files = {"base": str(w.glossary)}
    for arm in ("neighbour", "random1", "random2"):
        rows = [{"alias": "AB", "primary": "alpha bravo", "kind": "abbreviation", "paper": p, "doc_id": f"d-{p}"}
                for p in w.added[arm]]
        files[arm] = str(fx.write_jsonl(w.tmp / f"glossary-{arm}.jsonl", rows))
    return files


def strict_config(w: fx.World, mode: str, **over: Any) -> dict[str, Any]:
    """A main or pilot judge config: the prereg-fixed numbers, a stated budget, one glossary
    file per arm (and the frozen prereg for the main run)."""
    pr = w.tmp / "prereg.md"
    if not pr.exists():
        pr.write_text("frozen\n")
    cfg = config(w, dry_run=False, batch_size=2000, glossary=arm_glossaries(w),
                 limits={"input_token_cap": 10 ** 8, "output_token_cap": 10 ** 7, "call_cap": 10 ** 6})
    cfg.pop("dry_run")
    if mode == "pilot":
        cfg["pilot"] = True
    else:
        cfg["prereg"] = {"file": str(pr), "sha256": asc.sha256_file(pr)}
    cfg.update(over)
    return cfg


def test_main_config_pins_the_prereg_and_a_pilot_dir_stays_the_pilot_s(tmp_path: Path) -> None:
    w = fx.World(tmp_path)
    with pytest.raises(SystemExit, match="prereg"):
        aj.validate_config({k: v for k, v in strict_config(w, "main").items() if k != "prereg"})
    aj.Pipeline(aj.validate_config(strict_config(w, "pilot")), provenance=PROV)
    main_cfg = aj.validate_config(strict_config(w, "main"))
    with pytest.raises(SystemExit, match="STUDY-MODE"):
        aj.Pipeline(main_cfg, provenance=PROV)
    (tmp_path / "prereg.md").write_text("edited\n")
    with pytest.raises(SystemExit, match="HARD STOP"):
        aj.validate_config(strict_config(w, "main", prereg={"file": str(tmp_path / "prereg.md"), "sha256": "0" * 64}))


def test_prereg_fixed_numbers_are_pinned_outside_a_dry_run(tmp_path: Path) -> None:
    w = fx.World(tmp_path)
    for mode in ("main", "pilot"):
        aj.validate_config(strict_config(w, mode))
        for over, msg in (({"batch_size": 40}, "batch_size"), ({"drift": {"share": 0.1}}, "drift.share"),
                          ({"drift": {"overall": 0.96}}, "drift.overall"), ({"drift": {"per_arm": 0.9}}, "per_arm"),
                          ({"drift": {"min_pairs": 50}}, "min_pairs"), ({"retry_cap": 3}, "retry_cap"),
                          ({"limits": {}}, "limits must state"), ({"glossary": str(w.glossary)}, "glossary must"),
                          ({"drift_ref": {**config(w)["drift_ref"], "served_models": ["other"]}}, "served_models")):
            with pytest.raises(SystemExit, match=f"HARD STOP.*{msg}"):
                aj.validate_config(strict_config(w, mode, **over))
    cfg = aj.validate_config(config(w, batch_size=40, drift={"share": 0.3}, retry_cap=3))     # a dry run may
    assert cfg["retry_cap"] == 3 and cfg["drift_ref"]["served_models"] == aj.OPUS_SERVED_MODELS


def test_a_missing_arm_glossary_is_a_hard_stop(tmp_path: Path) -> None:
    w = fx.World(tmp_path)
    w.write_staged_runs("A")
    g = arm_glossaries(w)
    blind = {**g, "neighbour": str(w.glossary)}                  # the neighbour papers' entries are missing
    pl = aj.Pipeline(aj.validate_config(strict_config(w, "pilot", glossary=blind)), provenance=PROV)
    with pytest.raises(SystemExit, match="none listed"):
        pl.build("A")
    assert not (out_of(w) / "pairs.jsonl").exists()
    with pytest.raises(SystemExit, match="glossary.neighbour: file missing"):
        aj.validate_config(strict_config(w, "pilot", glossary={**g, "neighbour": str(tmp_path / "gone.jsonl")}))


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
        op = "Claude Opus 5.5"
        fh.write(json.dumps({"key": k.format(one_att), "status": "empty", "model": op}) + "\n")
        fh.write(json.dumps({"key": k.format(one_log), "status": "empty0", "model": op}) + "\n")
        # a reply from another model is a hard stop, never stored: it does not use up the pair's cap
        fh.write(json.dumps({"key": k.format(one_log), "status": "ok", "model": "Claude Opus 5"}) + "\n")
        fh.write(json.dumps({"key": k.format(two_log), "status": "empty", "model": op}) + "\n")
        fh.write(json.dumps({"key": k.format(two_log), "status": "truncated", "model": op}) + "\n")
        fh.write(json.dumps({"key": k.format(two_log), "status": "error:http_502"}) + "\n")   # not a reply
    assert label(cp, 1, "opus", fake) == 0
    world_ = pl.labeler(1, "opus").w
    per = {p["pair_id"]: fake.per_prompt.get(("Claude Opus 5.5", world_.prompt(p)[0]), 0) for p in sel}
    assert per[one_att] == 1 and per[one_log] == 1 and per[two_log] == 0
    assert all(per[p] == 2 for p in sampled[3:])
    rows = {r["pair_id"]: r for r in asc.read_jsonl(out / "labels-gw-opus.jsonl")}
    assert rows[two_log]["attempts"] == 2 and rows[two_log]["verdict"] is None
    assert all(rows[p]["attempts"] == 2 for p in sampled)
    # every call counts against the 7 % cap: the mismatched reply and the error included
    assert pl.calls_so_far("opus") == 6 + sum(per.values())


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
    assert pub["counts"]["retrieval_legs"] == 19 and pub["counts"]["judging_legs"] == 14 and pub["commit_sha256"] == marker["commit_sha256"]
    assert "rows:base|none|default" in pub["files"] and "final" in pub["files"]
    with pytest.raises(SystemExit, match="committed already"):
        main(cp, "commit-stage-a")


def git(repo: Path, *a: str) -> None:
    import subprocess

    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def strict_stage_a(tmp: Path, mode: str) -> tuple[fx.World, Path]:
    w = fx.World(tmp)
    w.write_staged_runs("A")
    cp = tmp / "jcfg.json"
    cp.write_text(json.dumps(strict_config(w, mode)))
    judge_stage(w, cp, "A", dry.FakeJudges())
    assert main(cp, "commit-stage-a") == 0
    return w, cp


def test_analysis_gate_refuses_before_the_commit_and_the_spot_checks(tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@example.invalid")
    git(repo, "config", "user.name", "t")
    (repo / "README").write_text("x\n")
    git(repo, "add", "README")
    git(repo, "commit", "-qm", "init")
    monkeypatch.setattr(aj, "REPO", repo)
    w = fx.World(tmp_path)
    w.write_staged_runs("A")
    cp = tmp_path / "jcfg.json"
    cp.write_text(json.dumps(strict_config(w, "main")))
    judge_stage(w, cp, "A", dry.FakeJudges())
    with pytest.raises(SystemExit, match="before stage A is committed"):
        aj.analysis_gate(w.runs)
    with pytest.raises(SystemExit, match="before stage A is committed"):
        main(cp, "check-analysis-gate")
    assert main(cp, "commit-stage-a") == 0
    # the main run: the public record must be committed to the repository first
    with pytest.raises(SystemExit, match="not tracked at HEAD"):
        aj.analysis_gate(w.runs)
    pub = out_of(w) / "stage-a-commit.public.json"
    (repo / "results").mkdir()
    (repo / "results" / pub.name).write_bytes(pub.read_bytes())
    git(repo, "add", "results")
    git(repo, "commit", "-qm", "stage A")
    with pytest.raises(SystemExit, match="records spot check"):
        aj.analysis_gate(w.runs)
    sr, sj = tmp_path / "spot-records.json", tmp_path / "spot-judge.json"
    sr.write_text("{}")
    with pytest.raises(SystemExit, match="judge spot check"):
        aj.analysis_gate(w.runs, {"records": str(sr)})
    sj.write_text("{}")
    g = aj.analysis_gate(w.runs, {"records": str(sr), "judge": str(sj)})
    assert g["allowed"] and set(g["spot_checks_sha256"]) == {"records", "judge"} and not g["spot_checks_waived"]
    assert g["mode"] == "main" and g["public_tracked_at_head"] is True
    # a stage-A label rewritten after the commit: no analysis
    fp = out_of(w) / "final.jsonl"
    data = fp.read_bytes()
    fp.write_bytes(data.replace(b'"label": 0', b'"label": 1', 1))
    with pytest.raises(SystemExit, match="changed since the commit"):
        aj.analysis_gate(w.runs, {"records": str(sr), "judge": str(sj)})
    fp.write_bytes(data)
    assert aj.analysis_gate(w.runs, {"records": str(sr), "judge": str(sj)})["allowed"]


def test_pilot_waives_the_spot_checks_and_records_it(tmp_path: Path) -> None:
    w, _ = strict_stage_a(tmp_path, "pilot")
    g = aj.analysis_gate(w.runs)
    assert g["allowed"] and g["mode"] == "pilot" and g["spot_checks_waived"]
    assert g["spot_checks_waiver"].startswith("pilot") and g["public_tracked_at_head"] is None


def test_an_appended_row_cannot_relabel_a_committed_pair(tmp_path: Path) -> None:
    """Readers keep the last row per key: one appended row naming a committed pair would
    relabel it, so the commit refuses any such row (final, labels, calls, pairs)."""
    w, cp, _ = through_commit(tmp_path)
    out = out_of(w)
    pid = asc.read_jsonl(out / "pairs.jsonl")[0]["pair_id"]
    for name, row in (("final.jsonl", {"k": f"pair|{pid}", "pair_id": pid, "label": 1, "source": "agree"}),
                      ("labels-qwen-C.jsonl", {"k": f"qwen|{pid}", "pair_id": pid, "verdicts": [1, 1, 1, 1, 1]}),
                      ("calls-gw-opus.jsonl", {"key": f"gw-opus|{pid}|r1", "status": "ok"}),
                      ("pairs.jsonl", {"pair_id": pid, "record_id": "x", "chunk_id": "y"})):
        f = out / name
        keep = f.read_bytes()
        with f.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
        with pytest.raises(SystemExit, match="names a committed pair"):
            aj.verify_marker(w.runs)
        f.write_bytes(keep)
    with (out / "final.jsonl").open("a") as fh:                         # a new pair's row is stage B's right
        fh.write(json.dumps({"k": "pair|newpair", "pair_id": "newpair", "label": 1}) + "\n")
    assert aj.verify_marker(w.runs)["files"] > 0


def test_an_edited_original_summary_breaks_the_commit(tmp_path: Path) -> None:
    w, cp, _ = through_commit(tmp_path)
    sp = w.runs / "base" / "none.summary.json"
    keep = sp.read_bytes()
    s = json.loads(keep)
    s["status"] = "complete "
    sp.write_text(json.dumps(s))
    with pytest.raises(SystemExit, match="summary:base"):
        aj.verify_marker(w.runs)
    sp.write_bytes(keep)
    rows = w.runs / "base" / "none.jsonl"                    # the bm25 rows are pinned too (retrieval done)
    with rows.open("a") as fh:
        fh.write(json.dumps({"record_id": "x", "condition": "bm25"}) + "\n")
    with pytest.raises(SystemExit, match="rows:base"):
        aj.verify_marker(w.runs)


def test_bm25_pairs_wait_for_stage_b(tmp_path: Path) -> None:
    """Owner, 2026-11-03: the five Layer-1 bm25 legs are retrieved in stage A and judged in
    stage B. No pair that only a bm25 leg asks for is labelled before the commit; the stage-B
    build registers them."""
    l1_bm25 = [lg for lg in ar.stage_legs("A") if lg[2] == "bm25"]
    assert len(l1_bm25) == 5 and all(ar.judging_stage(*lg) == "B" for lg in l1_bm25)
    w, cp, fake = through_commit(tmp_path)
    out = out_of(w)
    reg_a = {(p["record_id"], p["chunk_id"]) for p in asc.read_jsonl(out / "pairs.jsonl")}
    only_bm25 = expected_demand(w, l1_bm25, "found10") - expected_demand(w, ar.judging_legs("A"), "found10")
    assert only_bm25 and not only_bm25 & reg_a
    fin_a = {(r["record_id"], r["chunk_id"]) for r in asc.read_jsonl(out / "final.jsonl") if r["k"].startswith("pair|")}
    assert not only_bm25 & fin_a
    assert main(cp, "build-pairs", "--stage", "B") == 0
    new = {(p["record_id"], p["chunk_id"]): p for p in asc.read_jsonl(out / "pairs.jsonl") if p["stage"] == "B"}
    assert only_bm25 <= set(new) and all(new[k]["need"] == "found10" for k in only_bm25)


def test_a_drift_alarm_is_cleared_only_by_a_signed_remedy(tmp_path: Path) -> None:
    w, cp = world(tmp_path, batch_size=100000, drift={"min_pairs": 3, "share": 0.2})
    fake = Disagree()
    main(cp, "build-pairs", "--stage", "A")
    for be in aj.BACKENDS:
        assert label(cp, 1, be, fake) == 0
    assert main(cp, "finalize", "--batch", "1") == aj.EXIT_HARD_STOP
    out = out_of(w)
    fired = asc.read_jsonl(out / "drift-alarms.jsonl")[0]["scopes"]
    assert "overall" in fired
    rd = out / "drift-remedies"
    rd.mkdir()
    (rd / "bad.json").write_text(json.dumps({"deviation_id": "D-1", "date": "2026-11-04", "action": "x",
                                             "scope": "overall", "through_batch": 1}))
    with pytest.raises(SystemExit, match="signed_by"):
        aj.load_remedies(out)
    (rd / "bad.json").write_text(json.dumps({"deviation_id": "D-1", "date": "2026-11-04", "action": "x",
                                             "scope": "overall", "through_batch": 2, "signed_by": "owner"}))
    with pytest.raises(SystemExit, match="no overall alarm at batch 2"):
        aj.load_remedies(out)
    (rd / "bad.json").unlink()
    for sc in fired:
        (rd / f"D-1-{sc}.json").write_text(json.dumps({"deviation_id": "D-1", "date": "2026-11-04",
                                                       "action": "prompt reviewed; labels kept", "scope": sc,
                                                       "through_batch": 1, "signed_by": "owner"}))
    pl = aj.Pipeline(aj.load_config(cp), provenance=PROV)
    summ = pl.summary()
    assert not summ["drift"]["alarm"] and len(summ["drift"]["remedies"]) == len(fired)
    assert summ["drift"]["scopes"]["overall"]["alarm_window"]["after_batch"] == 1
    assert summ["drift"]["scopes"]["overall"]["agreement"] < 0.5          # the cumulative figure is still reported
    with pytest.raises(SystemExit, match="PAUSE"):                      # never auto-cleared: the owner removes PAUSE
        main(cp, "commit-stage-a")
    (out / "PAUSE").unlink()
    assert main(cp, "commit-stage-a") == 0
    commit = json.loads((out / "stage-a-commit.json").read_text())
    assert [r["deviation_id"] for r in commit["drift_remedies"]] == ["D-1"] * len(fired)
    pub = json.loads((out / "stage-a-commit.public.json").read_text())
    assert pub["counts"]["drift_remedies"] == len(fired)


def test_build_pairs_recovers_from_a_kill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import hashlib

    w, cp = world(tmp_path)
    real = aj.Pipeline._apply_pending

    def killed(self: aj.Pipeline) -> None:
        raise KeyboardInterrupt("killed after the registry was renamed")
    monkeypatch.setattr(aj.Pipeline, "_apply_pending", killed)
    with pytest.raises(KeyboardInterrupt):
        main(cp, "build-pairs", "--stage", "A")
    out = out_of(w)
    assert (out / "build-pending.json").exists() and not (out / "pairs-manifest.json").exists()
    monkeypatch.setattr(aj.Pipeline, "_apply_pending", real)
    pl = aj.Pipeline(aj.load_config(cp), provenance=PROV)
    assert pl.recovered == "finished" and len(pl.registry()) == len(asc.read_jsonl(out / "pairs.jsonl"))
    assert pl.batch_doc()["stages"]["A"] and not (out / "build-pending.json").exists()
    # a kill before the rename: the old (empty) registry stands and the pending build is dropped
    (tmp_path / "two").mkdir()
    w2, cp2 = world(tmp_path / "two")
    out2 = out_of(w2)
    out2.mkdir(parents=True, exist_ok=True)
    (out2 / "build-pending.json").write_text(json.dumps({"stage": "A", "old_sha256": hashlib.sha256(b"").hexdigest(),
                                                         "manifest": {"pairs_sha256": "f" * 64}, "batches": {}}))
    pl2 = aj.Pipeline(aj.load_config(cp2), provenance=PROV)
    assert pl2.recovered == "rolled_back"
    assert main(cp2, "build-pairs", "--stage", "A") == 0


class OpusDown(dry.FakeJudges):
    async def post(self, url: str, json: dict[str, Any]) -> Any:  # noqa: A002
        if json["model"] == "Claude Opus 5.5":
            self.calls[json["model"]] = self.calls.get(json["model"], 0) + 1
            return dry.Resp(502, {"error": {"message": "bad gateway"}})
        return await super().post(url, json)


def test_http_errors_are_capped_per_pair_across_resumes(tmp_path: Path) -> None:
    fake = OpusDown()
    w, cp, pl = labelled_through_tiebreak(tmp_path, fake,
                                          limits={"max_fail_total": 10 ** 6, "max_fail_consecutive": 10 ** 6})
    sampled = [p["pair_id"] for p in pl.selection(1, "opus")]
    rc = label(cp, 1, "opus", fake)
    for _ in range(4):
        if rc == 0:
            break
        rc = main(cp, "label", "--batch", "1", "--backend", "opus", "--notice-acknowledged", "--resume", http=fake)
    assert rc == 0
    rows = {r["pair_id"]: r for r in asc.read_jsonl(out_of(w) / "labels-gw-opus.jsonl")}
    assert set(rows) == set(sampled)
    assert all(r["verdict"] is None and r["no_verdict_reason"] == "http_errors"
               and r["http_errors"] == aj.MAX_HTTP_ERRORS for r in rows.values())
    assert fake.calls["Claude Opus 5.5"] == aj.MAX_HTTP_ERRORS * len(sampled)
    disp = asc.read_jsonl(out_of(w) / "dispatch-gw-opus.jsonl")
    assert len(disp) == fake.calls["Claude Opus 5.5"] and pl.calls_so_far("opus") == len(disp)
    # a call a kill left in flight (dispatched, no reply logged) still counts against the cap
    with (out_of(w) / "dispatch-gw-opus.jsonl").open("a") as fh:
        fh.write(json.dumps({"key": "gw-opus|x|r1"}) + "\n")
    assert pl.calls_so_far("opus") == len(disp) + 1


def test_a_pair_with_three_errors_on_disk_gets_no_further_call(tmp_path: Path) -> None:
    fake = GatewayScript(opus=lambda n: dry.json_verdict(True))
    w, cp, pl = labelled_through_tiebreak(tmp_path, fake)
    sel = pl.selection(1, "opus")
    victim = sel[0]["pair_id"]
    with (out_of(w) / "calls-gw-opus.jsonl").open("a") as fh:
        for _ in range(aj.MAX_HTTP_ERRORS):
            fh.write(json.dumps({"key": f"gw-opus|{victim}|r1", "status": "error:http_502"}) + "\n")
    assert label(cp, 1, "opus", fake) == 0
    prompt = pl.labeler(1, "opus").w.prompt(sel[0])[0]
    assert fake.per_prompt.get(("Claude Opus 5.5", prompt), 0) == 0
    row = {r["pair_id"]: r for r in asc.read_jsonl(out_of(w) / "labels-gw-opus.jsonl")}[victim]
    assert row["verdict"] is None and row["no_verdict_reason"] == "http_errors"
    assert fake.calls["Claude Opus 5.5"] == len(sel) - 1
