"""The alias answer-judge calibration: the prompt, the calibration-set builder, the three
backends (fake gateway, fake CLI, fake Qwen endpoint), the controls and the analysis.

A synthetic corpus; no store, no network, no model.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_EVAL = Path(__file__).resolve().parents[2] / "scripts" / "eval"
if str(_EVAL) not in sys.path:
    sys.path.insert(0, str(_EVAL))

import glossbench_alias_judge as jd  # noqa: E402

PROV = {"commit": "b" * 40, "dirty": False}

# ------------------------------------------------------------------ a synthetic world

PAPERS = {
    # paper: (title, [chunk texts])
    "P1": ("Ward ventilation study", [
        "The air change rate (ACH) of the ward was measured with tracer gas.",
        "The ACH fell to 2 per hour when the windows of the ward were closed.",
        "Staff rotate shifts at seven in the ventilated ward.",
    ]),
    "P2": ("Hospital airflow survey", [
        "Here ACH denotes air changes per hour; an ACH of 6 was required in isolation rooms.",
        "Closing windows lowered the ventilation of the wards markedly.",
    ]),
    "P3": ("Cholinesterase in farm workers", [
        "ACH (acetylcholine) levels rose after exposure; the ACH response was blunted in winter.",
        "Farm workers were sampled twice per season.",
    ]),
    "P4": ("Classroom air quality", [
        "The ACH in classrooms averaged 3.",
    ]),
}


def make_world(tmp: Path) -> Path:
    data = tmp / "data"
    data.mkdir(parents=True, exist_ok=True)
    rows = []
    for paper, (title, texts) in PAPERS.items():
        s = 0
        for i, t in enumerate(texts):
            rows.append({"chunk_id": f"{paper}-c{i}", "doc_id": f"d-{paper}", "content": t, "start_char": s,
                         "end_char": s + len(t), "metadata": {"source_path": paper, "title": title}})
            s += len(t) + 1
    (data / "chunks.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    gl = [{"alias": "ACH", "primary": "air change rate", "kind": "abbreviation", "paper": "P1", "doc_id": "d-P1"},
          {"alias": "ACH", "primary": "air changes per hour", "kind": "abbreviation", "paper": "P2", "doc_id": "d-P2"},
          {"alias": "ACH", "primary": "acetylcholine", "kind": "abbreviation", "paper": "P3", "doc_id": "d-P3"},
          {"alias": "ACH", "primary": "air change rate", "kind": "abbreviation", "paper": "P4", "doc_id": "d-P4"}]
    (data / "glossary.jsonl").write_text("".join(json.dumps(r) + "\n" for r in gl))
    pdir = data / "pilot"
    pdir.mkdir()
    draft = {"record_id": "rec-1", "k": "draft|x|a1", "stratum": "G", "paper": "P1", "chunk_id": "P1-c1",
             "alias": "ACH", "primary": "air change rate", "alias_kind": "abbreviation",
             "draft": {"question": "How did the air change rate of the ward change when windows were closed?",
                       "answer": "fell to 2 per hour", "quote": "The ACH fell to 2 per hour"}}
    rej = {**draft, "record_id": "rec-2", "k": "draft|y|a1",
           "draft": {**draft["draft"], "question": "A rejected question about the ward?"}}
    (pdir / "draft.jsonl").write_text(json.dumps(draft) + "\n" + json.dumps(rej) + "\n")
    acc = [{"k": "l1|rec-1", "layer": "l1", "verdict": "pass", "record_id": "rec-1", "gold_chunk_ids": ["P1-c1"]},
           {"k": "l2|rec-1", "layer": "l2", "verdict": "reject", "record_id": "rec-1"},
           {"k": "l1|rec-2", "layer": "l1", "verdict": "reject", "record_id": "rec-2"}]
    (pdir / "acceptance.jsonl").write_text("".join(json.dumps(r) + "\n" for r in acc))
    out = tmp / "out"
    cfg = {"out_dir": str(out), "chunks": [str(data / "chunks.jsonl")], "paper_key": "path_root",
           "glossary": str(data / "glossary.jsonl"),
           "pilots": [{"name": "p", "drafts": str(pdir / "draft.jsonl"), "acceptance": str(pdir / "acceptance.jsonl")}],
           "calib": {"per_question": {"gold": 1, "lex_same": 1, "lex_other": 1, "collision": 2}},
           "gateway": {"base_url": "http://127.0.0.1:18999/v1", "model": "Claude Opus X", "inflight": 4,
                       "empty_retry_s": 0.0},
           "cli": {"cwd": str(tmp / "cli_cwd"), "inflight": 2},
           "qwen": {"base_url": "http://127.0.0.1:18998/v1", "model": "qwen-x", "n": 7, "inflight": 2},
           "analysis": {"bootstrap": 50}}
    path = tmp / "cfg.json"
    path.write_text(json.dumps(cfg))
    return path


@pytest.fixture()
def world(tmp_path: Path) -> Path:
    cfg = make_world(tmp_path)
    assert jd.main(["--config", str(cfg), "build-calib"], prov=PROV) == 0
    return cfg


def out_of(cfg: Path) -> Path:
    return Path(json.loads(cfg.read_text())["out_dir"])


def pairs_of(cfg: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in (out_of(cfg) / "pairs.jsonl").read_text().splitlines()]


# ------------------------------------------------------------------ fakes

class Resp:
    def __init__(self, status: int, body: Any) -> None:
        self.status_code = status
        self.text = body if isinstance(body, str) else json.dumps(body)
        self._body = body

    def json(self) -> Any:
        return json.loads(self.text)


class FakeHTTP:
    """An async client: ``reply(body) -> Resp``; records bodies and peak concurrency."""

    def __init__(self, reply: Any) -> None:
        self.reply, self.bodies, self.now, self.peak = reply, [], 0, 0

    async def post(self, url: str, json: dict[str, Any]) -> Resp:  # noqa: A002
        self.bodies.append(json)
        self.now += 1
        self.peak = max(self.peak, self.now)
        await asyncio.sleep(0.005)
        self.now -= 1
        return self.reply(json)

    async def aclose(self) -> None:
        pass


def chat(text: str, model: str = "served-opus") -> Resp:
    return Resp(200, {"model": model, "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
                      "usage": {"prompt_tokens": 100, "completion_tokens": 20}})


YES = '{"reason": "states it", "answers": "yes"}'
NO = '{"reason": "other concept", "answers": "no"}'


def label(cfg: Path, backend: str, http: Any = None, runner: Any = None, extra: list[str] | None = None) -> int:
    assert jd.main(["--config", str(cfg), "preflight", "--backend", backend, *(extra or [])], prov=PROV) == 0
    return jd.main(["--config", str(cfg), "label", "--backend", backend, "--notice-acknowledged", *(extra or [])],
                   http_factory=(lambda: http) if http is not None else None, cli_runner=runner, prov=PROV)


# ------------------------------------------------------------------ prompt and parsing

def test_parse_judgement_variants() -> None:
    assert jd.parse_judgement(YES) == (True, "states it")
    assert jd.parse_judgement("```json\n" + NO + "\n```")[0] is False
    assert jd.parse_judgement("<think>maybe {\"answers\": \"yes\"}</think>" + NO)[0] is False
    assert jd.parse_judgement('blah {"reason": "x", "answers": "YES"} trailing')[0] is True
    assert jd.parse_judgement('{"answers": "maybe"}')[0] is None
    assert jd.parse_judgement("")[0] is None


def test_definitions_are_the_passage_papers_only() -> None:
    gl = [{"alias": "ACH", "primary": "acetylcholine", "kind": "abbreviation"},
          {"alias": "Qv", "primary": "outdoor airflow", "kind": "symbol"}]
    defs, cut = jd.definitions_for("ACH rose; the ACHs fell.", gl)
    assert defs == [("ACH", "acetylcholine")] and cut == 0
    p = jd.judge_prompt("What is the air change rate?", "air change rate", "ACH rose.", "T", defs)
    assert '"ACH" stands for "acetylcholine"' in p and "TERM: air change rate" in p
    assert "(none listed)" in jd.judge_prompt("q", "t", "x", "T", [])


def test_meaning_rule() -> None:
    assert jd.meaning("air change rate", "air changes per hour") == "same"
    assert jd.meaning("air change rate", "acetylcholine") == "diff"


# ------------------------------------------------------------------ calibration set

def test_build_calib_strata_and_determinism(world: Path, tmp_path: Path) -> None:
    pairs = pairs_of(world)
    by = {p["stratum"]: p for p in pairs}
    assert by["gold"]["chunk_id"] == "P1-c1"
    assert by["lex_same"]["c_paper"] == "P1" and by["lex_same"]["chunk_id"] != "P1-c1"
    assert by["coll_diff"]["c_paper"] == "P3" and by["coll_diff"]["c_primary"] == "acetylcholine"
    assert by["coll_same"]["c_paper"] == "P4"
    assert {p["record_id"] for p in pairs} == {"rec-1"}          # the layer-1 reject is not used
    assert len({p["pair_id"] for p in pairs}) == len(pairs)
    man = json.loads((out_of(world) / "calib-manifest.json").read_text())
    assert man["counts"]["pairs"] == len(pairs) and man["schema"] == jd.CALIB_SCHEMA
    # a second build elsewhere gives the same file
    cfg2 = make_world(tmp_path / "again")
    assert jd.main(["--config", str(cfg2), "build-calib"], prov=PROV) == 0
    assert (out_of(cfg2) / "pairs.jsonl").read_text() == (out_of(world) / "pairs.jsonl").read_text()
    # frozen once built
    with pytest.raises(SystemExit):
        jd.main(["--config", str(world), "build-calib"], prov=PROV)


def test_main_records_are_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="main"):
        jd.load_pilot_records([{"drafts": str(tmp_path / "out" / "main" / "d.jsonl"),
                                "acceptance": str(tmp_path / "a.jsonl")}])


def test_out_dir_inside_repo_refused(tmp_path: Path) -> None:
    cfg = make_world(tmp_path)
    raw = json.loads(cfg.read_text())
    raw["out_dir"] = str(_EVAL / "judge_out_should_not_exist")
    cfg.write_text(json.dumps(raw))
    with pytest.raises(SystemExit, match="inside the repository"):
        jd.main(["--config", str(cfg), "build-calib"], prov=PROV)


def test_bm25_ranks_the_matching_doc_first() -> None:
    bm = jd.BM25([("a", "tracer gas ward ventilation"), ("b", "farm workers sampled"), ("c", "ward shifts")])
    assert bm.top("ward ventilation tracer", 1)[0][0] == "a"


# ------------------------------------------------------------------ opus via the gateway

def test_opus_three_reps_terms_and_resume(world: Path) -> None:
    http = FakeHTTP(lambda b: chat(YES))
    n = len(pairs_of(world))
    assert label(world, "opus", http, extra=["--reps", "3"]) == 0
    assert len(http.bodies) == 3 * n
    for b in http.bodies:
        assert b["fallback_model"] == "" and b["max_tokens"] == 2048 and "temperature" not in b
        assert b["model"] == "Claude Opus X" and b["messages"][0]["content"] == jd.JUDGE_SYSTEM
    assert http.peak <= 4
    rows = [json.loads(x) for x in (out_of(world) / "labels-opus.jsonl").read_text().splitlines()]
    assert len(rows) == 3 * n and all(r["verdict"] is True and r["model"] == "served-opus" for r in rows)
    # resume: nothing re-issued
    http2 = FakeHTTP(lambda b: chat(YES))
    assert jd.main(["--config", str(world), "label", "--backend", "opus", "--reps", "3", "--notice-acknowledged",
                    "--resume"], http_factory=lambda: http2, prov=PROV) == 0
    assert http2.bodies == []
    log = (out_of(world) / "LOG").read_text()
    assert "GATEWAY SIZE-NOTICE" in log and "label backend=opus done" in log


def test_opus_budget_stop_provider_notice(world: Path) -> None:
    http = FakeHTTP(lambda b: Resp(503, {"error": {"type": "provider_notice", "message": "monthly quota"}}))
    assert label(world, "opus", http, extra=["--reps", "1"]) == jd.EXIT_BUDGET
    out = out_of(world)
    assert (out / "PAUSE").exists() and (out / "budget-stop-judge.json").exists()
    assert len(http.bodies) <= 4                                     # in-flight calls only
    with pytest.raises(SystemExit, match="PAUSE"):
        jd.main(["--config", str(world), "label", "--backend", "opus", "--reps", "1", "--notice-acknowledged",
                 "--resume"], http_factory=lambda: http, prov=PROV)


def test_opus_429_is_a_budget_stop(world: Path) -> None:
    http = FakeHTTP(lambda b: Resp(429, "slow down"))
    assert label(world, "opus", http, extra=["--reps", "1"]) == jd.EXIT_BUDGET


def test_pause_file_and_inflight_file(world: Path) -> None:
    out = out_of(world)
    (out / "PAUSE").write_text("stop")
    with pytest.raises(SystemExit, match="PAUSE"):
        label(world, "opus", FakeHTTP(lambda b: chat(YES)), extra=["--reps", "1"])
    (out / "PAUSE").unlink()
    (out / "INFLIGHT").write_text("1")
    http = FakeHTTP(lambda b: chat(NO))
    assert label(world, "opus", http, extra=["--reps", "2"]) == 0
    assert http.peak == 1


def test_gate_refuses_without_notice_or_changed_plan(world: Path) -> None:
    with pytest.raises(SystemExit, match="preflight"):
        jd.main(["--config", str(world), "label", "--backend", "opus", "--notice-acknowledged"], prov=PROV)
    assert jd.main(["--config", str(world), "preflight", "--backend", "opus", "--reps", "1"], prov=PROV) == 0
    with pytest.raises(SystemExit, match="plan changed"):
        jd.main(["--config", str(world), "label", "--backend", "opus", "--reps", "3", "--notice-acknowledged"],
                prov=PROV)
    with pytest.raises(SystemExit, match="notice"):
        jd.main(["--config", str(world), "label", "--backend", "opus", "--reps", "1"], prov=PROV)


# ------------------------------------------------------------------ the Claude CLI

def cli_out(text: str, *, fallback: bool = False, ctx: int = 600) -> str:
    mu = {"claude-opus-5-5": {"inputTokens": ctx, "outputTokens": 0 if fallback else 30}}
    if fallback:
        mu["claude-opus-5"] = {"inputTokens": ctx, "outputTokens": 30}
    return json.dumps({"result": text, "is_error": False, "total_cost_usd": 0.01, "modelUsage": mu,
                       "safety_stops": 1 if fallback else 0})


def test_cli_labels_and_fallback_is_refused(world: Path) -> None:
    seen: list[dict[str, Any]] = []

    def runner(cmd: list[str], **kw: Any) -> Any:
        seen.append({"cmd": cmd, "input": kw["input"], "cwd": kw["cwd"]})
        fb = "acetylcholine" in kw["input"]          # the collision passage's paper defines ACH so
        return SimpleNamespace(returncode=0, stdout=cli_out(NO if fb else YES, fallback=fb), stderr="")

    assert label(world, "cli", runner=runner) == 0
    cmd = seen[0]["cmd"]
    for flag in ("--restricted", "--strict-mcp-config", "--no-session-persistence"):
        assert flag in cmd
    assert cmd[cmd.index("--tools") + 1] == "" and cmd[cmd.index("--system-prompt") + 1] == jd.JUDGE_SYSTEM
    rows = {r["pair_id"]: r for r in map(json.loads, (out_of(world) / "labels-cli.jsonl").read_text().splitlines())}
    pairs = {p["pair_id"]: p for p in pairs_of(world)}
    coll = next(pid for pid, p in pairs.items() if p["stratum"] == "coll_diff")
    assert rows[coll]["status"] == "refused" and rows[coll]["verdict"] is None and rows[coll]["fallback"]
    gold = next(pid for pid, p in pairs.items() if p["stratum"] == "gold")
    assert rows[gold]["verdict"] is True


def test_cli_isolation_anomaly_stops(world: Path) -> None:
    def runner(cmd: list[str], **kw: Any) -> Any:
        return SimpleNamespace(returncode=0, stdout=cli_out(YES, ctx=50_000), stderr="")

    assert label(world, "cli", runner=runner) == jd.EXIT_HARD_STOP
    assert not (out_of(world) / "labels-cli.jsonl").exists() or \
        (out_of(world) / "labels-cli.jsonl").read_text() == ""


def test_cli_usage_limit_pauses(world: Path) -> None:
    def runner(cmd: list[str], **kw: Any) -> Any:
        return SimpleNamespace(returncode=1, stdout="", stderr="You have hit your usage limit")

    assert label(world, "cli", runner=runner) == jd.EXIT_PAUSED


# ------------------------------------------------------------------ Qwen

def test_qwen_n_samples_stored(world: Path) -> None:
    def reply(b: dict[str, Any]) -> Resp:
        ch = [{"index": i, "message": {"content": YES if i % 3 else "garbled"}, "finish_reason": "stop"}
              for i in range(b["n"])]
        return Resp(200, {"model": "served-qwen", "choices": ch, "usage": {"prompt_tokens": 9, "completion_tokens": 70}})

    http = FakeHTTP(reply)
    assert label(world, "qwen", http) == 0
    b = http.bodies[0]
    assert b["n"] == 7 and b["temperature"] == 0.7 and b["chat_template_kwargs"] == {"enable_thinking": False}
    assert http.peak <= 2
    rows = [json.loads(x) for x in (out_of(world) / "labels-qwen.jsonl").read_text().splitlines()]
    assert all(r["n"] == 7 and r["invalid"] == 3 and r["model"] == "served-qwen" for r in rows)
    assert rows[0]["verdicts"][:3] == [None, 1, 1]


def test_same_prompt_for_every_backend(world: Path) -> None:
    gw = FakeHTTP(lambda b: chat(YES))
    assert label(world, "opus", gw, extra=["--reps", "1"]) == 0
    qw = FakeHTTP(lambda b: Resp(200, {"model": "q", "choices": [{"index": 0, "message": {"content": YES}}],
                                       "usage": {}}))
    assert label(world, "qwen", qw) == 0
    inputs: list[str] = []

    def runner(cmd: list[str], **kw: Any) -> Any:
        inputs.append(kw["input"])
        return SimpleNamespace(returncode=0, stdout=cli_out(YES), stderr="")

    assert label(world, "cli", runner=runner) == 0
    assert sorted(b["messages"][1]["content"] for b in gw.bodies) == sorted(
        b["messages"][1]["content"] for b in qw.bodies) == sorted(inputs)
    assert all(b["messages"][0]["content"] == jd.JUDGE_SYSTEM for b in qw.bodies)


# ------------------------------------------------------------------ analysis

def test_kappa_and_cascade() -> None:
    assert jd.kappa([1, 0, 1, 0], [1, 0, 1, 0]) == 1.0
    assert jd.kappa([1, 1, 0, 0], [1, 0, 1, 0]) == 0.0
    rows = jd.cascade([1.0, 0.0, 0.65, 0.95], [1, 0, 0, 0], [0.6, 0.9])
    assert rows[0] == {"tau": 0.6, "accepted": 4, "escalated": 0, "escalation_rate": 0.0,
                       "accepted_accuracy": 0.5, "cascade_accuracy": 0.5}
    assert rows[1]["escalated"] == 1 and rows[1]["accepted_accuracy"] == pytest.approx(2 / 3, abs=1e-4)
    assert rows[1]["cascade_accuracy"] == 0.75


def test_n_curve_hypergeometric() -> None:
    # pair 1: 100/100 yes, ref yes; pair 2: 0/100, ref no; pair 3: 60/100 yes, ref no
    cur = jd.n_curve([100, 0, 60], [100, 100, 100], [1, 0, 0], ["gold", "lex_other", "coll_diff"],
                     [1, 99], 400, 1)
    assert cur[99]["per_stratum_acc"]["gold"] == 1.0 and cur[99]["per_stratum_acc"]["lex_other"] == 1.0
    # n = 99 of 100 without replacement: 60 yes of 100 -> majority yes in every draw
    assert cur[99]["per_stratum_acc"]["coll_diff"] == 0.0
    # n = 1: P(no) = 0.4
    assert cur[1]["per_stratum_acc"]["coll_diff"] == pytest.approx(0.4, abs=0.08)


def test_opus_reference_majority_and_unanimity() -> None:
    rows = [{"pair_id": "a", "rep": i, "verdict": v} for i, v in ((1, True), (2, True), (3, False))]
    rows += [{"pair_id": "b", "rep": i, "verdict": False} for i in (1, 2, 3)]
    ref = jd.opus_reference(rows, 3)
    assert ref["a"]["ref"] == 1 and not ref["a"]["unanimous"]
    assert ref["b"]["ref"] == 0 and ref["b"]["unanimous"]


def test_analyse_end_to_end(world: Path) -> None:
    pairs = pairs_of(world)
    coll = {p["pair_id"] for p in pairs if p["stratum"] == "coll_diff"}

    def opus(b: dict[str, Any]) -> Resp:
        return chat(NO if "acetylcholine" in b["messages"][1]["content"] else YES)

    assert label(world, "opus", FakeHTTP(opus), extra=["--reps", "3"]) == 0

    def qwen(b: dict[str, Any]) -> Resp:
        bad = "acetylcholine" in b["messages"][1]["content"]
        ch = [{"index": i, "message": {"content": YES if (not bad or i < 3) else NO}} for i in range(b["n"])]
        return Resp(200, {"model": "q", "choices": ch, "usage": {}})

    assert label(world, "qwen", FakeHTTP(qwen)) == 0
    assert jd.main(["--config", str(world), "analyse"], prov=PROV) == 0
    res = json.loads((out_of(world) / "analysis.json").read_text())
    assert res["opus"]["unanimous"] == len(pairs)
    q = res["qwen"]["opus_unanimous"]
    assert set(q["curve"]) == {"1", "3", "5", "7"}
    assert q["curve"]["7"]["acc"] == 1.0 and q["plateau_n"] <= 7
    assert len(coll) == 1
    assert q["cascade"]["n_all"][0]["tau"] == 0.6


def test_build_calib_independent_of_hash_seed(tmp_path: Path) -> None:
    import os
    import subprocess

    shas = []
    for seed in ("1", "2"):
        cfg = make_world(tmp_path / seed)
        env = {**os.environ, "PYTHONHASHSEED": seed}
        code = (f"import sys; sys.path.insert(0, {str(_EVAL)!r}); import glossbench_alias_judge as j; "
                f"sys.exit(j.main(['--config', {str(cfg)!r}, 'build-calib'], prov={{'commit': 'x', 'dirty': False}}))")
        subprocess.run([sys.executable, "-c", code], check=True, env=env, capture_output=True)
        shas.append((out_of(cfg) / "pairs.jsonl").read_text())
    assert shas[0] == shas[1]


def test_opus_empty_answer_is_reissued_not_stored(world: Path) -> None:
    calls = {"n": 0}

    def reply(b: dict[str, Any]) -> Resp:
        calls["n"] += 1
        if calls["n"] <= 2:                       # one job: empty, then empty again on its retry
            return Resp(200, {"model": "m", "choices": [{"message": {"content": ""}, "finish_reason": "stop"}],
                              "usage": {"prompt_tokens": 10, "completion_tokens": 0}})
        return chat(YES)

    out = out_of(world)
    (out / "INFLIGHT").write_text("1")
    n = len(pairs_of(world))
    assert label(world, "opus", FakeHTTP(reply), extra=["--reps", "1"]) == 0
    rows = [json.loads(x) for x in (out / "labels-opus.jsonl").read_text().splitlines()]
    assert len(rows) == n and all(r["verdict"] is True for r in rows)
    assert calls["n"] == n + 2
    # a stored empty row (an older run) is re-issued on resume
    with (out / "labels-opus.jsonl").open("a") as fh:
        fh.write(json.dumps({**rows[0], "status": "empty0", "verdict": None}) + "\n")
    http = FakeHTTP(lambda b: chat(NO))
    assert jd.main(["--config", str(world), "label", "--backend", "opus", "--reps", "1", "--notice-acknowledged",
                    "--resume"], http_factory=lambda: http, prov=PROV) == 0
    assert len(http.bodies) == 1


def test_cli_safety_retry_on_same_model_is_refused_not_an_anomaly(world: Path) -> None:
    # a safeguard stop re-asked on the SAME model: one modelUsage entry carrying both requests
    def runner(cmd: list[str], **kw: Any) -> Any:
        stop = "acetylcholine" in kw["input"]
        one = 2 * (len(kw["input"]) + len(jd.JUDGE_SYSTEM)) / 3.5 + 1.25 * 574   # the one-request budget
        ctx = int(1.6 * one) if stop else 600          # two requests: over one budget, under two
        mu = {"claude-opus-5-5": {"inputTokens": ctx, "outputTokens": 30}}
        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(
            {"result": NO, "is_error": False, "total_cost_usd": 0.01, "modelUsage": mu,
             "safety_stops": 1 if stop else 0, "num_turns": 2 if stop else 1}))

    assert label(world, "cli", runner=runner) == 0
    rows = [json.loads(x) for x in (out_of(world) / "labels-cli.jsonl").read_text().splitlines()]
    stopped = [r for r in rows if r["safety_stops"]]
    assert len(stopped) == 1 and stopped[0]["status"] == "refused" and stopped[0]["verdict"] is None
    # a real leak still fails the check
    sh = jd._CliShim({"model": "claude-opus-5-5"}, runner)
    assert not sh.isolation("x" * 100, {"context_tokens": 40_000, "safety_stops": 1})["PASS"]


def test_cli_shim_does_not_scale_a_same_model_reask_twice() -> None:
    # the verifier's check now scales a same-model re-ask itself (k = 2); the shim must not
    # scale it again (k^2 = 4 would let a 2.5x context through)
    sh = jd._CliShim({"model": "claude-opus-5-5"}, lambda *a, **k: None)
    p = "x" * 3000
    one = 2 * (len(p) + len(jd.JUDGE_SYSTEM)) / 3.5 + 1.25 * 574
    res = {"context_tokens": int(2.5 * one), "safety_stops": 1, "served_models": {"claude-opus-5-5": [0, 30]}}
    iso = sh.isolation(p, res)
    assert iso["requests"] == 2 and iso["budget"] == round(2 * one, 1) and not iso["PASS"]
    assert sh.isolation(p, {**res, "context_tokens": int(1.6 * one)})["PASS"]


# ------------------------------------------------------------------ round 2: variants

def test_v2_system_is_v1_plus_one_rule() -> None:
    v1, v2 = jd.JUDGE_SYSTEMS["alias-judge/1"], jd.JUDGE_SYSTEMS["alias-judge/2"]
    assert v1 == jd.JUDGE_SYSTEM and v2.count(jd.JUDGE_RULE_V2) == 1
    assert v2.replace(jd.JUDGE_RULE_V2 + "\n", "", 1) == v1
    assert v2.index(jd.JUDGE_RULE_V2) < v2.index('- "yes":')


def round2_cfg(world: Path, variant: str, version: str, thinking: bool, **extra: Any) -> Path:
    raw = json.loads(world.read_text())
    r1 = raw["out_dir"]
    raw["out_dir"] = str(Path(r1).parent / "round2")
    raw["calib_dir"] = r1
    raw["qwen"] = {**raw["qwen"], "variant": variant, "judge_version": version, "enable_thinking": thinking,
                   "max_tokens": 4096}
    raw.update(extra)
    path = world.parent / f"cfg-{variant}.json"
    path.write_text(json.dumps(raw))
    return path


def qwen_reply(yes_if: Any) -> Any:
    def reply(b: dict[str, Any]) -> Resp:
        sys_, user = b["messages"][0]["content"], b["messages"][1]["content"]
        ans = YES if yes_if(sys_, user) else NO
        ch = [{"index": i, "message": {"content": ans, "reasoning": "thinking..."}, "finish_reason": "stop"}
              for i in range(b["n"])]
        return Resp(200, {"model": "q", "choices": ch,
                          "usage": {"prompt_tokens": 9, "completion_tokens": 700,
                                    "completion_tokens_details": {"reasoning_tokens": 600}}})
    return reply


def test_qwen_variant_files_prompt_and_thinking(world: Path) -> None:
    cfg = round2_cfg(world, "C", "alias-judge/2", True)
    http = FakeHTTP(qwen_reply(lambda s, u: True))
    assert label(cfg, "qwen", http) == 0
    out = out_of(cfg)
    assert (out / "labels-qwen-C.jsonl").is_file() and (out / "preflight-qwen-C.json").is_file()
    assert not (out / "pairs.jsonl").exists()          # the set is read from calib_dir, not copied
    b = http.bodies[0]
    assert b["messages"][0]["content"] == jd.JUDGE_SYSTEM_V2
    assert b["chat_template_kwargs"] == {"enable_thinking": True} and b["max_tokens"] == 4096
    rows = [json.loads(x) for x in (out / "labels-qwen-C.jsonl").read_text().splitlines()]
    assert all(r["judge_version"] == "alias-judge/2" and r["variant"] == "C" and r["enable_thinking"]
               and r["reasoning_tokens"] == 600 and r["system_sha256"] == jd.g.sha_text(jd.JUDGE_SYSTEM_V2)
               and r["thinking_switch"] == jd.THINKING_SWITCH for r in rows)
    # round 1's own Qwen file is untouched
    assert not (out_of(world) / "labels-qwen.jsonl").exists()


def test_round2_pairs_sha_pin(world: Path) -> None:
    cfg = round2_cfg(world, "A", "alias-judge/2", False, pairs_sha256="0" * 64)
    with pytest.raises(jd.p4.HardStop):
        jd.main(["--config", str(cfg), "preflight", "--backend", "qwen"], prov=PROV)


def test_unknown_judge_version_refused(world: Path) -> None:
    cfg = round2_cfg(world, "A", "alias-judge/9", False)
    with pytest.raises(SystemExit):
        jd.main(["--config", str(cfg), "preflight", "--backend", "qwen"], prov=PROV)


def test_analyse_variants_counts_and_overlap(world: Path, tmp_path: Path) -> None:
    pairs = pairs_of(world)

    def opus(b: dict[str, Any]) -> Resp:
        return chat(NO if "acetylcholine" in b["messages"][1]["content"] else YES)

    assert label(world, "opus", FakeHTTP(opus), extra=["--reps", "3"]) == 0
    # A: says no to everything except under prompt /2 -> false no's only on /1
    cfg_a = round2_cfg(world, "A", "alias-judge/1", False)
    assert label(cfg_a, "qwen", FakeHTTP(qwen_reply(lambda s, u: False))) == 0
    cfg_c = round2_cfg(world, "C", "alias-judge/2", True)
    assert label(cfg_c, "qwen", FakeHTTP(qwen_reply(lambda s, u: jd.JUDGE_RULE_V2 in s))) == 0
    main_log = tmp_path / "main-draft.jsonl"
    main_log.write_text(json.dumps({"record_id": "rec-1"}) + "\n" + json.dumps({"record_id": "other"}) + "\n")
    assert jd.main(["--config", str(cfg_c), "overlap", "--main-drafts", str(main_log)], prov=PROV) == 0
    out = out_of(cfg_c)
    ov = json.loads((out / "overlap.json").read_text())
    assert ov["records"] == ["rec-1"] and ov["pairs"] == len(pairs) and ov["main_drafts_rows"] == 2
    raw = json.loads(cfg_c.read_text())
    raw["analysis"] = {**raw.get("analysis", {}), "report_n": [1, 3, 7],
                       "variants": [{"name": "A", "labels": str(out / "labels-qwen-A.jsonl")},
                                    {"name": "C", "labels": str(out / "labels-qwen-C.jsonl")}]}
    cfg_c.write_text(json.dumps(raw))
    assert jd.main(["--config", str(cfg_c), "analyse-variants"], prov=PROV) == 0
    res = json.loads((out / "analysis-variants.json").read_text())
    n_yes = sum(1 for p in pairs if p["stratum"] != "coll_diff")
    a = res["variants"]["A"]["subsets"]["unanimous"]
    assert a["curve"]["7"]["fn"] == n_yes and a["curve"]["7"]["fp"] == 0
    c = res["variants"]["C"]["subsets"]["unanimous"]
    assert c["curve"]["1"]["fp"] == 1 and c["curve"]["1"]["fn"] == 0     # coll_diff over-corrected to yes
    assert c["curve"]["1"]["per_stratum_acc"]["coll_diff"] == 0.0
    assert set(c["curve"]["1"]["per_record_kind_acc"]) == {"G"}
    assert res["variants"]["C"]["subsets"]["excl_overlap"] is None     # every pair is from rec-1
    assert res["variants"]["C"]["subsets"]["excl_coll_diff"]["curve"]["1"]["acc"] == 1.0
    assert res["variants"]["C"]["reasoning_tokens_per_sample"] == pytest.approx(600 / 7, abs=0.1)
    assert res["variants"]["C"]["judge_versions"] == {"alias-judge/2": len(pairs)}
    assert res["variants"]["C"]["subsets"]["unanimous"]["cascade"]["n_all"][0]["tau"] == 0.8
