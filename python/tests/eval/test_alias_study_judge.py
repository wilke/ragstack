"""Alias study judge pipeline: pairs (dedup, gold dropped, arms, seeded order, blindness), the
composed rule, the Argon re-issue, the drift sample and alarm, the served-model check, the
size-notice gate, the caps, PAUSE and the batch order. Fake judges, no gateway, no model."""
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

PROV = {"commit": "c" * 40, "dirty": False}
GW = "http://gw.invalid:18799/v1"


def config(w: fx.World, **over: Any) -> dict[str, Any]:
    cfg = {"out_dir": str(w.tmp / "judge"), "runs_dir": str(w.runs), "records": str(w.records_path),
           "chunk_exports": {k: str(v) for k, v in w.exports.items()}, "glossary": str(w.glossary),
           "batch_size": 40, "caps": {"argon_share": 1.0, "opus_share": 1.0},
           "judge_c": {"base_url": "http://c.invalid/v1", **{k: v for k, v in aj.FIXED["C"].items() if k != "variant"}},
           "judge_e": {"base_url": "http://e.invalid/v1", **{k: v for k, v in aj.FIXED["E"].items() if k != "variant"}},
           "tiebreak": {"base_url": GW, "empty_retry_s": 0.0, **{k: v for k, v in aj.FIXED["argon"].items() if k != "tag"}},
           "drift_ref": {"base_url": GW, "empty_retry_s": 0.0, **{k: v for k, v in aj.FIXED["opus"].items() if k != "tag"}}}
    cfg.update(over)
    return cfg


def world(tmp: Path, **over: Any) -> tuple[fx.World, Path]:
    w = fx.World(tmp)
    w.write_runs()
    cp = tmp / "jcfg.json"
    cp.write_text(json.dumps(config(w, **over)))
    return w, cp


def main(cp: Path, *a: str, http: Any = None) -> int:
    return aj.main(["--config", str(cp), *a], http_factory=(lambda: http) if http is not None else None, prov=PROV)


def label(cp: Path, b: int, be: str, http: Any, ack: bool = True) -> int:
    assert main(cp, "preflight", "--batch", str(b), "--backend", be, http=http) == 0
    return main(cp, "label", "--batch", str(b), "--backend", be, *(["--notice-acknowledged"] if ack else []), http=http)


# ------------------------------------------------------------------ pairs

def test_build_pairs_dedup_gold_arm_order(tmp_path: Path) -> None:
    w, cp = world(tmp_path)
    assert main(cp, "build-pairs") == 0
    out = tmp_path / "judge"
    pairs = asc.read_jsonl(out / "pairs.jsonl")
    man = json.loads((out / "pairs-manifest.json").read_text())
    ids = [(p["record_id"], p["chunk_id"]) for p in pairs]
    assert len(ids) == len(set(ids)) == man["counts"]["pairs"]
    assert man["counts"]["instances_before_dedup"] > len(pairs) and man["counts"]["legs"] == 28
    assert all(p["chunk_id"] != w.by_id[p["record_id"]]["gold_chunks"][0] for p in pairs)
    assert {p["arm"] for p in pairs} == {"base", "neighbour", "random"}
    assert [p["order"] for p in pairs] == sorted(p["order"] for p in pairs)
    assert pairs[0]["order"] == asc.seeded_key(asc.SEEDS["judge_order"], pairs[0]["pair_id"])
    with pytest.raises(SystemExit, match="frozen"):
        main(cp, "build-pairs")


def test_pairs_need_every_leg_complete(tmp_path: Path) -> None:
    w = fx.World(tmp_path)
    w.write_runs(status={"random2/none#2": "paused"})
    cp = tmp_path / "jcfg.json"
    cp.write_text(json.dumps(config(w)))
    with pytest.raises(SystemExit, match="not complete"):
        main(cp, "build-pairs")


def test_failed_rows_add_no_pairs(tmp_path: Path) -> None:
    w = fx.World(tmp_path)
    w.write_runs(failed=lambda leg, rec: True)
    recs = asc.load_records(w.records_path)
    rows = aj.leg_rows(w.runs)
    chunks, arm_of = aj.chunk_arms(config(w))
    pairs, counts = aj.build_pairs(recs, rows, chunks, arm_of)
    assert pairs == [] and counts["instances_before_dedup"] == 0


def test_prompt_is_blind_to_arm_and_cell(tmp_path: Path) -> None:
    w, cp = world(tmp_path)
    main(cp, "build-pairs")
    pl = aj.Pipeline(aj.load_config(cp), provenance=PROV)
    reg = pl.registry()
    p = next(x for x in reg if x["arm"] == "neighbour")
    world_ = aj.StudyWorld(aj.harness_cfg(pl.cfg, "C"), pl.chunks(), pl.glossary(), [p])
    a = world_.prompt(p)[0]
    b = world_.prompt({**p, "arm": "base", "order": "0", "record_kind": "C", "pair_id": "zz"})[0]
    assert a == b
    for word in ("neighbour", "random", "bm25", "norerank", "merged", "rank"):
        assert word not in a.split("PASSAGE:")[0].lower() + a.split(">>>")[-1].lower()


# ------------------------------------------------------------------ rule pieces

def row(verdicts: list[Any]) -> dict[str, Any]:
    return {"verdicts": verdicts}


def test_final_label_rule() -> None:
    assert aj.final_label(row([1, 1, 0, 1, None]), row([1]), None, None) == (1, "agree")
    assert aj.final_label(row([1, 1, 1, 1, 1]), row([0]), {"verdict": False}, None) == (0, "tiebreak")
    assert aj.final_label(row([1, 1, 0, 0, None]), row([1]), {"verdict": True}, None) == (1, "tiebreak")  # no C majority
    assert aj.final_label(row([0, 0, 0, 0, 0]), row([None]), {"verdict": None, "status": "ok"},
                          {"verdict": False}) == (0, "tiebreak_r2")                                   # E invalid
    assert aj.final_label(row([1, 0, 1, 0, 1]), row([0]), {"verdict": None}, {"verdict": None}) == (None, "unjudged")
    assert aj.needs_reissue({"status": "ok", "verdict": None}) and not aj.needs_reissue({"status": "empty", "verdict": None})
    assert not aj.needs_reissue({"status": "ok", "verdict": True}) and not aj.needs_reissue(None)


def test_drift_sample_allocation() -> None:
    pairs = [{"pair_id": f"b{i}", "arm": "base"} for i in range(170)] + \
            [{"pair_id": f"n{i}", "arm": "neighbour"} for i in range(25)] + \
            [{"pair_id": f"r{i}", "arm": "random"} for i in range(2)]
    s = aj.drift_sample(1, pairs, 0.05)               # round(9.85) = 10 -> 4 / 3 / 3, random capped at 2
    arms = [p[0] for p in s]
    assert (arms.count("b"), arms.count("n"), arms.count("r")) == (4, 3, 2)
    assert s == aj.drift_sample(1, pairs, 0.05) and set(s) != set(aj.drift_sample(2, pairs, 0.05))


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
    assert main(cp, "build-pairs") == 0
    out = tmp_path / "judge"
    n_batches = len(json.loads((out / "batches.json").read_text())["batches"])
    assert n_batches >= 2
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
    assert "agree" in srcs and srcs & {"tiebreak", "tiebreak_r2"}
    summ = json.loads((out / "judge-summary.json").read_text())
    assert summ["pairs_finalized"] == len(fin) and summ["batches_finalized"] == [1]
    with pytest.raises(SystemExit, match="finalized"):
        main(cp, "finalize", "--batch", "1")
    # Argon unparseable replies were re-issued once (argon_r2)
    tb = {r["pair_id"]: r for r in asc.read_jsonl(out / "labels-gw-argon.jsonl")}
    tb2 = {r["pair_id"] for r in asc.read_jsonl(out / "labels-gw-argon_r2.jsonl")} \
        if (out / "labels-gw-argon_r2.jsonl").exists() else set()
    assert {p for p, r in tb.items() if r["verdict"] is None and r["status"] == "ok"} == tb2


def test_served_model_mismatch_stops_and_stores_nothing(tmp_path: Path) -> None:
    w, cp = world(tmp_path)
    main(cp, "build-pairs")
    assert label(cp, 1, "C", WrongModel()) == aj.EXIT_HARD_STOP
    out = tmp_path / "judge"
    assert (out / "PAUSE").exists()
    assert not (out / "labels-qwen-C.jsonl").exists() or asc.read_jsonl(out / "labels-qwen-C.jsonl") == []


def test_caps_refuse(tmp_path: Path) -> None:
    w, cp = world(tmp_path, caps={"argon_share": 0.04, "opus_share": 0.0})
    main(cp, "build-pairs")
    with pytest.raises(SystemExit, match="opus cap"):
        main(cp, "preflight", "--batch", "1", "--backend", "opus", http=dry.FakeJudges())
    pl = aj.Pipeline(aj.load_config(cp), provenance=PROV)
    total = len(pl.registry())
    pl.check_caps("argon", int(0.04 * total))
    with pytest.raises(SystemExit, match="argon cap"):
        pl.check_caps("argon", int(0.04 * total) + 1)


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
    main(cp, "build-pairs")
    for be in aj.BACKENDS:
        assert label(cp, 1, be, fake) == 0
    assert main(cp, "finalize", "--batch", "1") == aj.EXIT_HARD_STOP
    out = tmp_path / "judge"
    assert (out / "PAUSE").exists() and json.loads((out / "drift-alarm.json").read_text())["alarm"]
    with pytest.raises(SystemExit, match="PAUSE"):
        main(cp, "label", "--batch", "1", "--backend", "C", "--notice-acknowledged", http=fake)
