"""Round-2 harness item 2d: drafter, layer-1 checks, verifier, acceptance log,
spot-check sheet.

Fake transports only: the gateway is an in-process object with an async
``post`` (no socket), the Claude CLI is a fake ``subprocess.run``. Synthetic
corpus and glossary files (``glossbench_r2_support``); no store, no network, no
model.
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.eval import glossbench_r2_support as sup

_EVAL = Path(__file__).resolve().parents[2] / "scripts" / "eval"
if str(_EVAL) not in sys.path:
    sys.path.insert(0, str(_EVAL))

import _glossbench_r2 as g  # noqa: E402
import glossbench_r2_checks as ck  # noqa: E402
import glossbench_r2_draft as dr  # noqa: E402
import glossbench_r2_pool as pool  # noqa: E402

PROV = {"commit": "c" * 40, "dirty": False, "untracked": 0}
SERVED = "served-drafter-2026"


class FakeResp:
    def __init__(self, status: int, payload: dict[str, Any] | None = None, text: str = "") -> None:
        self.status_code = status
        self._payload = payload
        self.text = text or json.dumps(payload or {})

    def json(self) -> dict[str, Any]:
        assert self._payload is not None
        return self._payload


class FakeGateway:
    """Answers like an OpenAI-compatible gateway. ``behave(user_text, n)`` may
    override: return ``"empty"``, ``"length"``, ``"429"``, ``"500"`` or None."""

    def __init__(self, behave: Any = None, prompt_tokens: int = 100, on_call: Any = None) -> None:
        self.behave = behave or (lambda user, n: None)
        self.bodies: list[dict[str, Any]] = []
        self.urls: list[str] = []
        self.per_prompt: Counter[str] = Counter()
        self.active = 0
        self.max_active = 0
        self.pt = prompt_tokens
        self.on_call = on_call
        self.closed = 0

    async def post(self, url: str, json: dict[str, Any]) -> FakeResp:  # noqa: A002 - httpx's name
        self.urls.append(url)
        self.bodies.append(json)
        user = json["messages"][-1]["content"]
        self.per_prompt[user] += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01)
            if self.on_call:
                self.on_call(len(self.bodies))
            how = self.behave(user, self.per_prompt[user])
            if how == "429":
                return FakeResp(429, text="rate limit")
            if how == "500":
                return FakeResp(500, text="boom")
            content = "" if how in ("empty", "length") else __import__("json").dumps(sup.fake_draft(user))
            fin = "length" if how == "length" else "stop"
            ct = 0 if how == "empty" else 50
            return FakeResp(200, {"model": SERVED, "choices": [{"message": {"content": content},
                                                                "finish_reason": fin}],
                                  "usage": {"prompt_tokens": self.pt, "completion_tokens": ct}})
        finally:
            self.active -= 1

    async def aclose(self) -> None:
        self.closed += 1


class FakeCLI:
    """A fake ``subprocess.run`` for the Claude CLI."""

    def __init__(self, verdict: Any = None, limit_after: int | None = None, ctx: int = 400) -> None:
        self.verdict = verdict or (lambda prompt: {"supports": True, "single_reading": True, "same_case": True,
                                                   "symbol_unique": True, "reason": "ok"})
        self.calls: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.limit_after = limit_after
        self.ctx = ctx

    def __call__(self, cmd: list[str], *, input: str, capture_output: bool, text: bool, cwd: str,  # noqa: A002
                 timeout: int) -> SimpleNamespace:
        with self.lock:
            self.calls.append({"cmd": cmd, "input": input, "cwd": cwd, "cwd_files": list(Path(cwd).iterdir())})
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            n = len(self.calls)
        try:
            time.sleep(0.01)
            if self.limit_after is not None and n > self.limit_after:
                return SimpleNamespace(returncode=1, stdout="", stderr="Claude AI usage limit reached")
            v = self.verdict(input)
            out = {"result": "" if v is None else json.dumps(v), "is_error": False, "total_cost_usd": 0.01,
                   "modelUsage": {"claude-opus-5-5": {"inputTokens": self.ctx, "outputTokens": 30}}}
            return SimpleNamespace(returncode=0, stdout=json.dumps(out), stderr="")
        finally:
            with self.lock:
                self.active -= 1


def setup(tmp: Path, *, drafter: dict[str, Any] | None = None, limits: dict[str, Any] | None = None,
          verifier: dict[str, Any] | None = None, pool_cfg: dict[str, Any] | None = None) -> tuple[Path, dict]:
    d = {"base_url": "http://127.0.0.1:18999/v1", "empty_retry_s": 0.0, **(drafter or {})}
    lim = {"rate_backoffs_s": [0.0, 0.0, 0.0], **(limits or {})}
    path, cfg, _ = sup.make_config(tmp, drafter=d, limits=lim, verifier={"cwd": str(tmp / "vcwd"), **(verifier or {})},
                                   pool=pool_cfg)
    pool.build(cfg, "O", g.out_dir(cfg), provenance=lambda: PROV)
    return path, cfg


def run(path: Path, *args: str, gw: FakeGateway | None = None, cli: FakeCLI | None = None) -> int:
    return dr.main(["--private-config", str(path), *args], provenance=lambda: PROV,
                   http_factory=(lambda: gw) if gw else None, cli_runner=cli)


def out(cfg: dict) -> Path:
    return Path(cfg["out_dir"])


def drafts(cfg: dict) -> list[dict[str, Any]]:
    return [json.loads(x) for x in (out(cfg) / "draft-O.jsonl").read_text().splitlines() if x.strip()]


def sampled(cfg: dict) -> int:
    rows = [json.loads(x) for x in (out(cfg) / "pool-O.jsonl").read_text().splitlines()[1:]]
    return sum(r["sampled"] for r in rows)


# ------------------------------------------------------------------ gate

def test_notice_gate(tmp_path: Path) -> None:
    path, cfg = setup(tmp_path)
    gw = FakeGateway()
    with pytest.raises(SystemExit, match="notice-acknowledged"):
        run(path, "draft", "--set", "O", gw=gw)
    with pytest.raises(SystemExit, match="no preflight"):
        run(path, "draft", "--set", "O", "--notice-acknowledged", gw=gw)
    assert not gw.bodies
    assert run(path, "preflight", "--stage", "draft", "--set", "O") == 0
    pf = json.loads((out(cfg) / "preflight-draft-O.json").read_text())
    assert pf["plan"]["chunks"] == sampled(cfg) and pf["plan"]["concurrency"] == 4
    assert run(path, "draft", "--set", "O", "--notice-acknowledged", gw=gw) == 0
    assert gw.bodies


def test_preflight_prints_the_notice(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path, _ = setup(tmp_path)
    run(path, "preflight", "--stage", "draft", "--set", "O")
    line = capsys.readouterr().out
    assert line.startswith("GATEWAY-NOTICE glossbench-r2: draft set=O:")
    assert "calls" in line and "tokens in" in line and "Gemini 2.5 Pro" in line and "<= 4 in flight" in line


# ------------------------------------------------------------------ gateway terms

def test_every_body_carries_the_gateway_terms_and_no_glossary(tmp_path: Path) -> None:
    path, cfg = setup(tmp_path)
    gw = FakeGateway()
    run(path, "preflight", "--stage", "draft", "--set", "O")
    assert run(path, "draft", "--set", "O", "--notice-acknowledged", gw=gw) == 0
    assert gw.bodies and all(u == "http://127.0.0.1:18999/v1/chat/completions" for u in gw.urls)
    for b in gw.bodies:
        assert b["fallback_model"] == "" and b["max_tokens"] == 4096 and b["temperature"] == 0
        assert b["model"] == "Gemini 2.5 Pro"
        text = json.dumps(b["messages"]).casefold()
        for banned in ("glossary", "rules", "qwen", "scout", "frontier", "symbol_glossary", "arm"):
            assert banned not in text.replace("warm", ""), banned
    rows = drafts(cfg)
    assert {r["model"] for r in rows} == {SERVED}                  # served model recorded per call
    calls = [json.loads(x) for x in (out(cfg) / "draft-calls-O.jsonl").read_text().splitlines()]
    assert {c["model"] for c in calls} == {SERVED}
    tot = json.loads((out(cfg) / "draft-totals-O.json").read_text())
    assert tot["status"] == "complete" and tot["served_models"] == {SERVED: len(rows)}
    assert tot["totals"]["since_start"]["gateway_calls"] == len(gw.bodies)
    assert tot["provenance"] == PROV and tot["citable"] is True


def test_assertion_guards_the_body(tmp_path: Path) -> None:
    ctl = dr.R2Control(dict(dr.DEFAULTS["limits"]), tmp_path / "PAUSE", None)
    gw = dr.R2Gateway("http://127.0.0.1:18999", ctl, {**dr.DEFAULTS["limits"], "empty_retries": 1,
                                                       "empty_backoff_s": 0}, tmp_path / "calls.jsonl")
    with pytest.raises(AssertionError, match="gateway terms"):
        gw.body({"model": "m", "max_tokens": 0}, [])


def test_caps_cumulative_vs_per_segment(tmp_path: Path) -> None:
    """call_cap / input_token_cap / output_token_cap count prior_gateway_usage + earlier
    segments + this one (output_token_cap too, though the parent Control gets it nulled);
    max_calls counts this segment only."""
    base = {**dr.DEFAULTS["limits"], "input_token_cap": None, "output_token_cap": None, "call_cap": None}
    prior = {"calls": 5, "prompt_tokens": 50, "completion_tokens": 30}
    for cap, val in (("call_cap", 9), ("input_token_cap", 90), ("output_token_cap", 50)):
        ctl = dr.R2Control({**base, cap: val, "prior_gateway_usage": prior}, tmp_path / "PAUSE",
                           {"gateway_calls": 3, "gateway_prompt_tokens": 30, "gateway_completion_tokens": 15})
        assert ctl.lim["output_token_cap"] is None and ctl.gateway_admit()      # 8 / 80 / 45 so far
        ctl.account("gateway", 10, 5)                                            # 9 / 90 / 50
        assert not ctl.gateway_admit() and ctl.reason == cap, cap
    ctl = dr.R2Control({**base, "max_calls": 2, "prior_gateway_usage": prior}, tmp_path / "PAUSE",
                       {"gateway_calls": 100, "gateway_prompt_tokens": 0, "gateway_completion_tokens": 0})
    assert ctl.gateway_admit()
    ctl.account("gateway")
    ctl.account("gateway")
    assert not ctl.gateway_admit() and ctl.reason.startswith("max_calls")


def test_at_most_four_in_flight(tmp_path: Path) -> None:
    path, cfg = setup(tmp_path, pool_cfg={"n_papers": 6, "s1_per_paper": 3, "s2_per_paper": 2, "s3_per_paper": 2})
    gw = FakeGateway()
    run(path, "preflight", "--stage", "draft", "--set", "O")
    assert run(path, "draft", "--set", "O", "--notice-acknowledged", gw=gw) == 0
    assert len(gw.bodies) > 8
    assert gw.max_active == 4


def test_more_than_four_in_flight_is_refused(tmp_path: Path) -> None:
    path, _ = setup(tmp_path, drafter={"inflight": 5})
    with pytest.raises(SystemExit, match="at most 4"):
        run(path, "preflight", "--stage", "draft", "--set", "O")
    with pytest.raises(SystemExit, match="at most 4"):
        asyncio.run(dr.run_jobs([], 8, dr.R2Control(dict(dr.DEFAULTS["limits"]), tmp_path / "P", None), None))


# ------------------------------------------------------------------ empties

def test_empty_is_retried_once_then_skipped(tmp_path: Path) -> None:
    path, cfg = setup(tmp_path)
    target: dict[str, str] = {}

    def behave(user: str, n: int) -> str | None:
        target.setdefault("u", user)
        return "empty" if user == target["u"] else None

    gw = FakeGateway(behave)
    run(path, "preflight", "--stage", "draft", "--set", "O")
    assert run(path, "draft", "--set", "O", "--notice-acknowledged", gw=gw) == 0
    assert gw.per_prompt[target["u"]] == 2                          # one retry, then skipped
    dropped = [r for r in drafts(cfg) if r["status"] == "empty_twice"]
    assert len(dropped) == 1 and dropped[0]["attempt"] == 1          # no redraw after an empty
    assert not [r for r in drafts(cfg) if r["chunk_id"] == dropped[0]["chunk_id"] and r["attempt"] == 2]
    tot = json.loads((out(cfg) / "draft-totals-O.json").read_text())
    assert tot["totals"]["since_start"]["gateway_failures:empty"] == 1
    assert tot["row_status"]["empty_twice"] == 1


def test_one_empty_then_an_answer_is_kept(tmp_path: Path) -> None:
    path, cfg = setup(tmp_path)
    gw = FakeGateway(lambda user, n: "empty" if n == 1 else None)
    run(path, "preflight", "--stage", "draft", "--set", "O")
    assert run(path, "draft", "--set", "O", "--notice-acknowledged", gw=gw) == 0
    rows = drafts(cfg)
    assert rows and all(r["status"] == "ok" for r in rows)
    assert all(r["http_attempts"] == 2 for r in rows if r["attempt"] == 1)


def test_cut_answers_are_dropped_and_counted(tmp_path: Path) -> None:
    path, cfg = setup(tmp_path)
    gw = FakeGateway(lambda user, n: "length")
    run(path, "preflight", "--stage", "draft", "--set", "O")
    assert run(path, "draft", "--set", "O", "--notice-acknowledged", gw=gw) == 0
    assert {r["status"] for r in drafts(cfg)} == {"truncated"}
    assert len(gw.bodies) == sampled(cfg)                           # not retried, not redrawn


# ------------------------------------------------------------------ pause, resume, budget

def test_pause_file_stops_and_resume_needs_a_fresh_notice(tmp_path: Path) -> None:
    path, cfg = setup(tmp_path)
    pause = out(cfg) / "PAUSE"
    gw = FakeGateway(on_call=lambda n: pause.touch() if n >= 3 else None)
    run(path, "preflight", "--stage", "draft", "--set", "O")
    assert run(path, "draft", "--set", "O", "--notice-acknowledged", gw=gw) == 75
    st = json.loads((out(cfg) / "draft-state-O.json").read_text())
    assert st["status"] == "paused" and st["reason"] == "pause_file"
    n_first = len(gw.bodies)
    assert 3 <= n_first <= 3 + 4                                     # in-flight calls finish, nothing new
    assert json.loads((out(cfg) / "draft-totals-O.json").read_text())["status"] == "paused"
    pause.unlink()
    with pytest.raises(SystemExit, match="fresh notice"):
        run(path, "draft", "--set", "O", "--notice-acknowledged", "--resume", gw=gw)
    assert run(path, "draft", "--set", "O", "--notice-acknowledged", gw=gw) == 1   # state exists: --resume
    run(path, "preflight", "--stage", "draft", "--set", "O")
    gw2 = FakeGateway()
    assert run(path, "draft", "--set", "O", "--notice-acknowledged", "--resume", gw=gw2) == 0
    rows = drafts(cfg)
    assert len({r["k"] for r in rows}) == len(rows)                 # nothing drafted twice
    st = json.loads((out(cfg) / "draft-state-O.json").read_text())
    assert st["status"] == "complete" and len(st["segments"]) == 2       # refused starts write nothing
    assert st["totals"]["since_start"]["gateway_calls"] == len(gw.bodies) + len(gw2.bodies)


def test_resume_refused_against_another_config(tmp_path: Path) -> None:
    path, cfg = setup(tmp_path)
    pause = out(cfg) / "PAUSE"
    gw = FakeGateway(on_call=lambda n: pause.touch() if n >= 2 else None)
    run(path, "preflight", "--stage", "draft", "--set", "O")
    assert run(path, "draft", "--set", "O", "--notice-acknowledged", gw=gw) == 75
    pause.unlink()
    cfg["checks"] = {"leak_threshold": 0.4}
    path.write_text(json.dumps(cfg))
    run(path, "preflight", "--stage", "draft", "--set", "O")
    assert run(path, "draft", "--set", "O", "--notice-acknowledged", "--resume", gw=FakeGateway()) == 1


def test_budget_stop_on_input_tokens(tmp_path: Path) -> None:
    path, cfg = setup(tmp_path, limits={"input_token_cap": 500})
    gw = FakeGateway(prompt_tokens=200)
    run(path, "preflight", "--stage", "draft", "--set", "O")
    assert run(path, "draft", "--set", "O", "--notice-acknowledged", gw=gw) == 75
    st = json.loads((out(cfg) / "draft-state-O.json").read_text())
    assert st["reason"] == "input_token_cap"
    assert len(gw.bodies) <= 3 + 4


def test_budget_counts_prior_usage_and_calls(tmp_path: Path) -> None:
    path, cfg = setup(tmp_path, limits={"call_cap": 10, "prior_gateway_usage": {"calls": 10}})
    gw = FakeGateway()
    run(path, "preflight", "--stage", "draft", "--set", "O")
    assert run(path, "draft", "--set", "O", "--notice-acknowledged", gw=gw) == 75
    assert not gw.bodies
    assert json.loads((out(cfg) / "draft-state-O.json").read_text())["reason"] == "call_cap"


def test_prior_usage_is_read_from_earlier_stages_files(tmp_path: Path) -> None:
    """``prior_gateway_usage`` as paths: this script's totals JSON, part 4's shape, a
    flat dict file and a frontier ``glossary.jsonl`` are summed, recorded with their
    sha256, and count toward the caps. The stage's own record is refused."""
    prior = tmp_path / "prior"
    prior.mkdir()
    (prior / "draft-totals-A.json").write_text(json.dumps(
        {"totals": {"since_start": {"gateway_calls": 3, "gateway_prompt_tokens": 1000, "gateway_completion_tokens": 10}}}))
    (prior / "part4-state.json").write_text(json.dumps(
        {"since_start": {"gateway_calls": 2, "gateway_prompt_tokens": 500, "gateway_completion_tokens": 5}}))
    (prior / "hand.json").write_text(json.dumps({"calls": 1, "prompt_tokens": 100, "completion_tokens": 1}))
    rows = [{"kind": "header", "schema": "x"},
            {"kind": "document", "doc_id": "d1", "provenance": {"llm": {"calls": [
                {"prompt_tokens": 700, "completion_tokens": 7, "retries": 1}, {"prompt_tokens": 300, "completion_tokens": 3}]}}},
            {"kind": "document", "doc_id": "d2", "provenance": {"llm": None}}]
    (prior / "frontier.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    files = [str(prior / n) for n in ("draft-totals-A.json", "part4-state.json", "hand.json", "frontier.jsonl")]
    res = dr.resolve_prior_usage(files)
    assert (res["calls"], res["prompt_tokens"], res["completion_tokens"]) == (3 + 2 + 1 + 3, 2600, 26)
    assert [s["name"] for s in res["sources"]] == [Path(f).name for f in files]
    assert all(len(s["sha256"]) == 64 for s in res["sources"])
    assert dr.resolve_prior_usage({"calls": 1, "prompt_tokens": 2, "completion_tokens": 3, "files": files[:1]}) \
        == {"calls": 4, "prompt_tokens": 1002, "completion_tokens": 13, "sources": res["sources"][:1]}
    assert dr.resolve_prior_usage(None)["calls"] == 0 and dr.resolve_prior_usage({})["sources"] == []
    with pytest.raises(SystemExit, match="not found"):
        dr.resolve_prior_usage(str(prior / "missing.json"))
    with pytest.raises(SystemExit, match="unknown keys"):
        dr.resolve_prior_usage({"call": 1})
    (prior / "bad.json").write_text(json.dumps({"something": 1}))
    with pytest.raises(SystemExit, match="no totals"):
        dr.resolve_prior_usage(str(prior / "bad.json"))
    with pytest.raises(SystemExit, match="own record"):
        dr.resolve_prior_usage(files[:1], own_files=[prior / "draft-totals-A.json"])
    # through the run: 1000 + 500 + 100 + 1000 = 2600 prior input tokens against a 2500 cap -> pause at once
    path, cfg = setup(tmp_path, limits={"input_token_cap": 2500, "prior_gateway_usage": files})
    gw = FakeGateway()
    run(path, "preflight", "--stage", "draft", "--set", "O")
    assert run(path, "draft", "--set", "O", "--notice-acknowledged", gw=gw) == 75
    assert not gw.bodies
    tot = json.loads((out(cfg) / "draft-totals-O.json").read_text())
    assert tot["prior_gateway_usage"]["prompt_tokens"] == 2600 and len(tot["prior_gateway_usage"]["sources"]) == 4
    assert tot["cumulative_with_prior"]["gateway_prompt_tokens"] == 2600
    # the stage's own totals file as a prior is refused (a resume already carries it)
    own = tmp_path / "own"
    path2, cfg2 = setup(own, limits={"prior_gateway_usage": [str(own / "out" / "draft-totals-O.json")]})
    (own / "out" / "draft-totals-O.json").write_text(json.dumps({"calls": 1, "prompt_tokens": 1, "completion_tokens": 1}))
    run(path2, "preflight", "--stage", "draft", "--set", "O")
    gw2 = FakeGateway()
    with pytest.raises(SystemExit, match="own record"):
        run(path2, "draft", "--set", "O", "--notice-acknowledged", gw=gw2)
    assert not gw2.bodies


def test_failures_in_a_row_pause(tmp_path: Path) -> None:
    path, cfg = setup(tmp_path)
    gw = FakeGateway(lambda user, n: "500")
    run(path, "preflight", "--stage", "draft", "--set", "O")
    assert run(path, "draft", "--set", "O", "--notice-acknowledged", gw=gw) == 75
    assert json.loads((out(cfg) / "draft-state-O.json").read_text())["reason"] == "failures_5_in_a_row"


def test_rate_limit_after_three_backoffs_pauses(tmp_path: Path) -> None:
    path, cfg = setup(tmp_path)
    gw = FakeGateway(lambda user, n: "429")
    run(path, "preflight", "--stage", "draft", "--set", "O")
    assert run(path, "draft", "--set", "O", "--notice-acknowledged", gw=gw) == 75
    assert json.loads((out(cfg) / "draft-state-O.json").read_text())["reason"] == "rate_limit"


def test_frozen_prereg_mismatch_is_a_hard_stop(tmp_path: Path) -> None:
    prereg = tmp_path / "PREREG.md"
    prereg.write_text("frozen text")
    path, cfg = sup.make_config(tmp_path, drafter={"base_url": "http://127.0.0.1:18999", "empty_retry_s": 0},
                                prereg={"file": str(prereg), "sha256": "0" * 64})[:2]
    pool.build(cfg, "O", g.out_dir(cfg), provenance=lambda: PROV)
    run(path, "preflight", "--stage", "draft", "--set", "O")
    gw = FakeGateway()
    assert run(path, "draft", "--set", "O", "--notice-acknowledged", gw=gw) == 1
    assert not gw.bodies


# ------------------------------------------------------------------ redraw

def test_layer1_reject_is_redrawn_once_with_its_reasons(tmp_path: Path) -> None:
    path, cfg = setup(tmp_path)

    class Leaky(FakeGateway):
        async def post(self, url: str, json: dict[str, Any]) -> FakeResp:  # noqa: A002
            r = await super().post(url, json)
            user = json["messages"][-1]["content"]
            if r.status_code == 200 and "rejected" not in user and "WITHOUT a symbol" not in user:
                d = __import__("json").loads(r._payload["choices"][0]["message"]["content"])
                if "question" in d:
                    d["question"] = d["question"].rstrip("?") + f" ({d['symbol']})?"
                    r._payload["choices"][0]["message"]["content"] = __import__("json").dumps(d)
            return r

    gw = Leaky()
    run(path, "preflight", "--stage", "draft", "--set", "O")
    assert run(path, "draft", "--set", "O", "--notice-acknowledged", gw=gw) == 0
    rows = drafts(cfg)
    first = [r for r in rows if r["attempt"] == 1 and r["stratum"] != "S3" and r["draft"] and "question" in r["draft"]]
    assert first and all("symbol_in_question" in r["l1_inline"]["reasons"] for r in first)
    second = {r["chunk_id"]: r for r in rows if r["attempt"] == 2}
    assert set(second) == {r["chunk_id"] for r in first}
    redraw_prompts = [b["messages"][-1]["content"] for b in gw.bodies if "rejected" in b["messages"][-1]["content"]]
    assert redraw_prompts and all("the question contained the symbol" in p for p in redraw_prompts)
    assert not [r for r in rows if r["attempt"] == 3]


def test_redraw_is_once_and_attempts_are_countable(tmp_path: Path) -> None:
    """A drafter that leaks the symbol every time: exactly two attempts per symbol
    chunk, both logged, and the summary gives attempts per accepted record."""
    path, cfg = setup(tmp_path)

    class AlwaysLeaky(FakeGateway):
        async def post(self, url: str, json: dict[str, Any]) -> FakeResp:  # noqa: A002
            r = await super().post(url, json)
            user = json["messages"][-1]["content"]
            if r.status_code == 200 and "WITHOUT a symbol" not in user:
                d = __import__("json").loads(r._payload["choices"][0]["message"]["content"])
                if "question" in d:
                    d["question"] = d["question"].rstrip("?") + f" ({d['symbol']})?"
                    r._payload["choices"][0]["message"]["content"] = __import__("json").dumps(d)
            return r

    gw = AlwaysLeaky()
    run(path, "preflight", "--stage", "draft", "--set", "O")
    assert run(path, "draft", "--set", "O", "--notice-acknowledged", gw=gw) == 0
    rows = drafts(cfg)
    sym = [r for r in rows if r["stratum"] != "S3" and r["draft"] and "question" in r["draft"]]
    per_chunk = Counter(r["chunk_id"] for r in sym)
    assert per_chunk and set(per_chunk.values()) == {2}               # one redraw, never a third
    assert all("symbol_in_question" in r["l1_inline"]["reasons"] for r in sym)
    assert len(gw.bodies) == len(rows)                                  # one gateway call per logged attempt
    assert run(path, "check", "--set", "O") == 0
    run(path, "preflight", "--stage", "verify", "--set", "O")
    assert run(path, "verify", "--set", "O", "--notice-acknowledged", cli=FakeCLI()) == 0
    assert run(path, "summary", "--set", "O") == 0
    s = json.loads((out(cfg) / "acceptance-summary-O.json").read_text())["counts"]
    assert s["draft_attempts"]["S1"] == 2 * len({r["chunk_id"] for r in sym if r["stratum"] == "S1"})
    assert s["attempts_by_number"]["a2"] == len(per_chunk)
    assert s["accepted"].get("S1", 0) == 0 and s["attempts_per_accepted_by_stratum"]["S1"] is None
    assert s["attempts_per_accepted"] == sum(s["draft_attempts"].values()) / s["accepted_total"]  # S3 accepted only
    assert s["gateway_calls_per_accepted"] >= s["attempts_per_accepted"]


# ------------------------------------------------------------------ the whole chain

def _drafted(tmp_path: Path, **kw: Any) -> tuple[Path, dict]:
    path, cfg = setup(tmp_path, **kw)
    run(path, "preflight", "--stage", "draft", "--set", "O")
    assert run(path, "draft", "--set", "O", "--notice-acknowledged", gw=FakeGateway()) == 0
    assert run(path, "check", "--set", "O") == 0
    return path, cfg


def acceptance(cfg: dict) -> list[dict[str, Any]]:
    return [json.loads(x) for x in (out(cfg) / "acceptance-O.jsonl").read_text().splitlines() if x.strip()]


def test_check_logs_layer1_and_is_append_only(tmp_path: Path) -> None:
    path, cfg = _drafted(tmp_path)
    log = acceptance(cfg)
    l1 = [r for r in log if r["layer"] == "l1"]
    assert l1 and any(r["verdict"] == "pass" for r in l1)
    for r in l1:
        if r["verdict"] == "pass":
            assert r["gold_chunk_ids"] and r["leak_ok"] is True
    size = (out(cfg) / "acceptance-O.jsonl").stat().st_size
    assert run(path, "check", "--set", "O") == 0                    # idempotent: nothing new
    assert (out(cfg) / "acceptance-O.jsonl").stat().st_size == size
    cfg["checks"] = {"leak_threshold": -1.0}                        # a changed check is refused
    path.write_text(json.dumps(cfg))
    with pytest.raises(SystemExit, match="append-only"):
        dr.Run(cfg, g.file_sha(path), out(cfg), "O", provenance=lambda: PROV).check()


def test_verify_summary_and_spot_check(tmp_path: Path) -> None:
    path, cfg = _drafted(tmp_path)
    cli = FakeCLI(verdict=lambda p: {"supports": True, "single_reading": "QUESTION: How many samples" not in p,
                                     "same_case": True, "symbol_unique": True, "reason": "r"})
    with pytest.raises(SystemExit, match="notice-acknowledged"):
        run(path, "verify", "--set", "O", cli=cli)
    run(path, "preflight", "--stage", "verify", "--set", "O")
    assert run(path, "verify", "--set", "O", "--notice-acknowledged", cli=cli) == 0
    passes = [r for r in acceptance(cfg) if r["layer"] == "l1" and r["verdict"] == "pass"]
    assert len(cli.calls) == len(passes) > 0
    for c in cli.calls:
        cmd = c["cmd"]
        assert cmd[:2] == ["claude", "-p"] and cmd[cmd.index("--model") + 1] == "claude-opus-5-5"
        for flag in ("--restricted", "--no-session-persistence", "--strict-mcp-config", "--system-prompt"):
            assert flag in cmd
        assert cmd[cmd.index("--tools") + 1] == "" and cmd[cmd.index("--output-format") + 1] == "json"
        assert c["cwd_files"] == [] and "QUESTION:" in c["input"]
        assert "glossary" not in c["input"].casefold()
    assert cli.max_active <= 3
    l2 = [r for r in acceptance(cfg) if r["layer"] == "l2"]
    assert {r["record_id"] for r in l2} == {r["record_id"] for r in passes}
    assert any("l2_single_reading" in r["reasons"] for r in l2)
    assert all(r["served_model"] == "claude-opus-5-5" for r in l2)
    assert run(path, "summary", "--set", "O") == 0
    s = json.loads((out(cfg) / "acceptance-summary-O.json").read_text())
    acc = [json.loads(x) for x in (out(cfg) / "accepted-O.jsonl").read_text().splitlines()]
    assert s["counts"]["accepted_total"] == len(acc) == sum(r["verdict"] == "pass" for r in l2)
    assert "l2_single_reading" in s["rejects_by_code"]
    for r in acc:  # the wording proxy's record schema (sibling PR #861)
        assert isinstance(r["record_id"], str) and r["record_id"]
        assert isinstance(r["gold_chunks"], list) and r["gold_chunks"] and all(isinstance(c, str) for c in r["gold_chunks"])
        assert isinstance(r["binding_terms"], list) and r["binding_terms"]
        assert isinstance(r["quantity"], str) and r["stratum"] in ("S1", "S2", "S3")
    for st in g.string_values(s):                                   # counts only
        assert "boat" not in st and "PMC" not in st and str(tmp_path) not in st
    # the owner's sheet: seeded, blind, re-importable
    sheet = tmp_path / "sheet.json"
    assert run(path, "spot-export", "--set", "O", str(sheet)) == 0
    data = json.loads(sheet.read_text())
    assert len(data["items"]) == min(40, len(acc))
    raw = sheet.read_text()
    for banned in ("record_id", "stratum", "S1", "S2", "S3", "rules", "qwen", "frontier"):
        assert banned not in raw
    sheet2 = tmp_path / "sheet2.json"
    run(path, "spot-export", "--set", "O", str(sheet2))
    assert json.loads(sheet2.read_text())["items"] == data["items"]          # seeded
    for i, it in enumerate(data["items"]):
        it["label"] = "valid" if i else "invalid"
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps(data))
    assert run(path, "spot-import", "--set", "O", str(labels), "--key", str(tmp_path / "sheet.key.json")) == 0
    res = json.loads((out(cfg) / "spot-results-O.json").read_text())
    n = len(data["items"])
    assert res["n_graded"] == n and res["n_valid"] == n - 1
    lo, hi = res["wilson95"]
    assert lo < res["validity"] < hi
    data["export_id"] = "spot-000000000000"
    labels.write_text(json.dumps(data))
    with pytest.raises(SystemExit, match="export_id"):
        run(path, "spot-import", "--set", "O", str(labels), "--key", str(tmp_path / "sheet.key.json"))


def test_verifier_empty_is_retried_once_then_rejected(tmp_path: Path) -> None:
    path, cfg = _drafted(tmp_path)
    seen: Counter[str] = Counter()

    def verdict(p: str) -> Any:
        seen[p] += 1
        return None if "QUESTION: How many samples" in p else {"supports": True, "single_reading": True, "same_case": True,
                                             "symbol_unique": True, "reason": "r"}

    cli = FakeCLI(verdict=verdict)
    run(path, "preflight", "--stage", "verify", "--set", "O")
    assert run(path, "verify", "--set", "O", "--notice-acknowledged", cli=cli) == 0
    empties = [p for p in seen if "QUESTION: How many samples" in p]
    assert empties and all(seen[p] == 2 for p in empties)
    assert any(r["reasons"] == ["l2_empty"] for r in acceptance(cfg) if r["layer"] == "l2")


def test_verifier_usage_limit_pauses_and_resumes(tmp_path: Path) -> None:
    path, cfg = _drafted(tmp_path)
    run(path, "preflight", "--stage", "verify", "--set", "O")
    assert run(path, "verify", "--set", "O", "--notice-acknowledged", cli=FakeCLI(limit_after=2)) == 75
    assert json.loads((out(cfg) / "verify-state-O.json").read_text())["reason"] == "usage_limit"
    run(path, "preflight", "--stage", "verify", "--set", "O")
    cli = FakeCLI()
    assert run(path, "verify", "--set", "O", "--notice-acknowledged", "--resume", cli=cli) == 0
    l2 = [r["record_id"] for r in acceptance(cfg) if r["layer"] == "l2"]
    assert len(l2) == len(set(l2))


def test_verifier_isolation_failure_is_a_hard_stop(tmp_path: Path) -> None:
    path, cfg = _drafted(tmp_path)
    run(path, "preflight", "--stage", "verify", "--set", "O")
    cli = FakeCLI(ctx=10_000_000)
    assert run(path, "verify", "--set", "O", "--notice-acknowledged", cli=cli) == 1
    # the probe runs alone; a persistent anomaly is re-issued twice, the third is the stop
    assert len(cli.calls) == 3 and len({c["input"] for c in cli.calls}) == 1
    st = json.loads((out(cfg) / "verify-state-O.json").read_text())
    assert st["reason"] == "isolation_failed" and st["totals"]["since_start"]["cli_isolation_anomalies"] == 3
    assert not (out(cfg) / "verify-O.jsonl").exists() or not [
        x for x in (out(cfg) / "verify-O.jsonl").read_text().splitlines() if '"verify|' in x]


class FlakyCtxCLI(FakeCLI):
    """Context ``bad`` for the calls whose 1-based number is in ``bad_calls`` (or whose prompt
    ``bad_prompt`` matches, every time); 400 otherwise."""

    def __init__(self, bad_calls: set[int] = frozenset(), bad_prompt: str | None = None) -> None:
        super().__init__()
        self.bad_calls, self.bad_prompt = set(bad_calls), bad_prompt

    def __call__(self, cmd: list[str], **kw: Any) -> SimpleNamespace:  # type: ignore[override]
        with self.lock:
            self.n_seen = getattr(self, "n_seen", 0) + 1
            n = self.n_seen
        res = super().__call__(cmd, **kw)
        if n in self.bad_calls or (self.bad_prompt and self.bad_prompt in kw["input"]):
            o = json.loads(res.stdout)
            o["modelUsage"]["claude-opus-5-5"]["inputTokens"] = 10_000_000
            res.stdout = json.dumps(o)
        return res


def _verify_rows(cfg: dict) -> list[dict[str, Any]]:
    return [json.loads(x) for x in (out(cfg) / "verify-O.jsonl").read_text().splitlines()
            if x.strip() and '"verify|' in x]


def test_every_verifier_call_is_isolation_checked_and_a_transient_anomaly_is_reissued(tmp_path: Path) -> None:
    path, cfg = _drafted(tmp_path)
    run(path, "preflight", "--stage", "verify", "--set", "O")
    n_todo = len([r for r in acceptance(cfg) if r["layer"] == "l1" and r["verdict"] == "pass"])
    assert n_todo >= 3
    cli = FlakyCtxCLI(bad_calls={2})          # not the probe: the old check never saw call 2
    assert run(path, "verify", "--set", "O", "--notice-acknowledged", cli=cli) == 0
    rows = _verify_rows(cfg)
    assert len(rows) == n_todo and len(cli.calls) == n_todo + 1        # one call discarded, re-issued
    assert sum(r["isolation_anomalies"] for r in rows) == 1
    assert all(r["context_tokens"] == 400 for r in rows)                # the kept verdicts are clean
    st = json.loads((out(cfg) / "verify-state-O.json").read_text())
    assert st["status"] == "complete" and st["totals"]["since_start"]["cli_isolation_anomalies"] == 1


_persistent_marker: list[str] = [""]


def test_a_persistent_anomaly_on_one_record_is_a_hard_stop(tmp_path: Path) -> None:
    path, cfg = _drafted(tmp_path)
    run(path, "preflight", "--stage", "verify", "--set", "O")
    probe = FakeCLI()
    first: list[str] = []

    def verdict(p: str) -> Any:
        first.append(p)
        return {"supports": True, "single_reading": True, "same_case": True, "symbol_unique": True, "reason": "r"}

    probe.verdict = verdict
    # find the second record's prompt with a dry run in another world
    path2, _ = _drafted(tmp_path / "dry")
    run(path2, "preflight", "--stage", "verify", "--set", "O")
    assert run(path2, "verify", "--set", "O", "--notice-acknowledged", cli=probe) == 0
    marker = first[1][-200:]
    _persistent_marker[0] = marker
    cli = FlakyCtxCLI(bad_prompt=marker)
    assert run(path, "verify", "--set", "O", "--notice-acknowledged", cli=cli) == 1
    assert sum(marker in c["input"] for c in cli.calls) == 3
    st = json.loads((out(cfg) / "verify-state-O.json").read_text())
    assert st["status"] == "hard_stop" and st["reason"] == "isolation_failed"
    assert all(marker not in (r.get("text") or "") for r in _verify_rows(cfg))


def test_clear_hard_stop_resumes_only_with_the_matching_reason_and_a_note(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path, cfg = _drafted(tmp_path)
    run(path, "preflight", "--stage", "verify", "--set", "O")
    assert run(path, "verify", "--set", "O", "--notice-acknowledged", cli=FakeCLI(ctx=10_000_000)) == 1
    state = out(cfg) / "verify-state-O.json"
    before = state.read_text()
    cli = FakeCLI()
    # a plain resume, a wrong reason, a missing note: all refused, no call, state untouched
    assert run(path, "verify", "--set", "O", "--notice-acknowledged", "--resume", cli=cli) == 1
    assert run(path, "verify", "--set", "O", "--notice-acknowledged", "--resume", "--clear-hard-stop",
               "codex_isolation_failed", "--clear-note", "x", cli=cli) == 1
    with pytest.raises(SystemExit, match="clear-note"):
        run(path, "verify", "--set", "O", "--notice-acknowledged", "--resume", "--clear-hard-stop",
            "isolation_failed", cli=cli)
    with pytest.raises(SystemExit, match="needs --resume"):
        run(path, "verify", "--set", "O", "--notice-acknowledged", "--clear-hard-stop", "isolation_failed",
            "--clear-note", "x", cli=cli)
    assert not cli.calls and state.read_text() == before
    # the sanctioned resume, at a new commit (the fix): logged and kept in the state
    new = {**PROV, "commit": "d" * 40}
    rc = dr.main(["--private-config", str(path), "verify", "--set", "O", "--notice-acknowledged", "--resume",
                  "--clear-hard-stop", "isolation_failed", "--clear-note", "owner: CLI fallback, see PR"],
                 provenance=lambda: new, cli_runner=cli)
    assert rc == 0 and cli.calls
    err = capsys.readouterr().err
    assert ("verify HARD STOP CLEARED (isolation_failed) by" in err and "owner: CLI fallback, see PR" in err
            and f"[commit {'c' * 40} -> {'d' * 40}]" in err)
    st = json.loads(state.read_text())
    assert st["status"] == "complete" and st["commit"] == "d" * 40
    (c,) = st["clearances"]
    assert c["reason"] == "isolation_failed" and c["note"] == "owner: CLI fallback, see PR"
    assert c["commit_before"] == "c" * 40 and c["commit_after"] == "d" * 40
    assert st["segments"][-1]["cleared"] == c
    # a commit change without a clearance is still refused
    assert dr.main(["--private-config", str(path), "verify", "--set", "O", "--notice-acknowledged", "--resume"],
                   provenance=lambda: PROV, cli_runner=FakeCLI()) == 1


def test_clear_hard_stop_refuses_a_run_that_did_not_hard_stop(tmp_path: Path) -> None:
    path, cfg = _drafted(tmp_path)
    run(path, "preflight", "--stage", "verify", "--set", "O")
    assert run(path, "verify", "--set", "O", "--notice-acknowledged", cli=FakeCLI(limit_after=2)) == 75
    run(path, "preflight", "--stage", "verify", "--set", "O")
    cli = FakeCLI()
    assert run(path, "verify", "--set", "O", "--notice-acknowledged", "--resume", "--clear-hard-stop",
               "usage_limit", "--clear-note", "x", cli=cli) == 1
    assert not cli.calls


def test_verifier_cwd_must_be_empty(tmp_path: Path) -> None:
    path, cfg = _drafted(tmp_path)
    (tmp_path / "vcwd").mkdir(exist_ok=True)
    (tmp_path / "vcwd" / "stray.txt").write_text("x")
    run(path, "preflight", "--stage", "verify", "--set", "O")
    cli = FakeCLI()
    assert run(path, "verify", "--set", "O", "--notice-acknowledged", cli=cli) == 1
    assert not cli.calls


# ------------------------------------------------------------------ layer 1 units

def _index(tmp_path: Path, top: int = 200) -> tuple[g.Corpus, ck.PaperIndex]:
    rows, _ = sup.corpus(2)
    c = g.build_corpus([g.parse_chunk(r, "t") for r in rows], "path_root")
    return c, ck.PaperIndex(c, top)


def _good(c: g.Corpus) -> tuple[dict[str, Any], g.ChunkRow]:
    chunk = next(x for x in c.paper_chunks("PMC1") if "Qb = 0.15" in x.content)
    return ({"question": "How fast did the folded paper boats drift in the calm case?",
             "quote": "measured Qb = 0.15 m/s", "quantity": "boat drift speed", "value": "0.15 m/s",
             "binding_terms": ["boat drift speed"], "symbol": "Qb"}, chunk)


def _l1(c: g.Corpus, idx: ck.PaperIndex, draft: dict[str, Any] | None, chunk: g.ChunkRow, stratum: str = "S1",
        **cfg: Any) -> dict[str, Any]:
    return ck.layer1(draft, None if draft else "json: x", stratum=stratum, paper="PMC1", source_chunk=chunk,
                     idx=idx, cfg=cfg)


def test_layer1_passes_a_good_record(tmp_path: Path) -> None:
    c, idx = _index(tmp_path)
    d, chunk = _good(c)
    r = _l1(c, idx, d, chunk)
    assert r["verdict"] == "pass", r
    assert chunk.chunk_id in r["gold_chunk_ids"]
    # gold width = quote-bearing chunks only, never the paper
    assert set(r["gold_chunk_ids"]) == {x.chunk_id for x in c.paper_chunks("PMC1")
                                        if "measured Qb = 0.15 m/s" in x.content}


@pytest.mark.parametrize("change,code", [
    ({"question": "What is Qb in the calm case?"}, "symbol_in_question"),
    ({"question": "Was the drift speed 0.15 in the calm case?"}, "number_in_question"),
    ({"value": "0.99 m/s"}, "quantity_not_in_quote"),
    ({"quote": "measured Qb = 0.15 m/h"}, "quote_not_in_paper"),
    ({"quote": "x" * 301}, "quote_too_long"),
    ({"quote": "Observers wrote every count"}, "quote_not_unique"),
    ({"binding_terms": ["boat drift speed of paper"]}, "binding_terms_invalid"),
    ({"binding_terms": []}, "binding_terms_invalid"),
    ({"binding_terms": ["glide velocity"]}, "binding_word_not_in_paper"),
    ({"symbol": ""}, "symbol_missing"),
    ({"symbol": "Zk"}, "symbol_not_in_chunk"),
    ({"quote": "small in that season (p = 0.01)", "value": "0.01"}, "quote_not_in_source"),
])
def test_layer1_reason_codes(tmp_path: Path, change: dict[str, Any], code: str) -> None:
    c, idx = _index(tmp_path)
    d, chunk = _good(c)
    r = _l1(c, idx, {**d, **change}, chunk)
    assert r["verdict"] == "reject" and code in r["reasons"], r


def test_layer1_unparseable_and_none(tmp_path: Path) -> None:
    c, idx = _index(tmp_path)
    _, chunk = _good(c)
    assert _l1(c, idx, None, chunk)["reasons"] == ["unparseable"]
    assert _l1(c, idx, {"none": True}, chunk)["reasons"] == ["no_quantity"]
    assert ck.parse_draft("```json\n{\"none\": true}\n```") == ({"none": True}, None)
    assert ck.parse_draft('{"question": "q"}')[0] is None


def test_layer1_cut_by_boundary(tmp_path: Path) -> None:
    c, idx = _index(tmp_path)
    d, chunk = _good(c)
    text = c.docs["d1"].text
    # a span that straddles two chunks' shared overlap entirely: take 150 chars across a boundary
    first = c.by_doc["d1"][0]
    span = text[first.end_char - 20: first.end_char + 130]
    assert not any(span in x.content for x in c.paper_chunks("PMC1"))
    r = _l1(c, idx, {**d, "quote": span}, chunk)
    assert "cut_by_boundary" in r["reasons"]


def test_layer1_s3_rules(tmp_path: Path) -> None:
    c, idx = _index(tmp_path)
    chunk = next(x for x in c.paper_chunks("PMC1") if "eleven knots" in x.content)
    d = {"question": "How strong was the wind gust the pond keeper reported?",
         "quote": "wind gust of eleven knots near jetty 1", "quantity": "wind gust", "value": "eleven knots",
         "binding_terms": ["wind gust"], "symbol": ""}
    assert _l1(c, idx, d, chunk, "S3")["verdict"] == "pass"
    assert "s3_has_symbol" in _l1(c, idx, {**d, "symbol": "Wg"}, chunk, "S3")["reasons"]
    assert "number_in_question" in _l1(c, idx, {**d, "question": "Was the gust eleven knots?"}, chunk, "S3")["reasons"]
    # a quote that states a value through a symbol is not words-only; a stoplisted statistic is
    vs_chunk = next(x for x in c.paper_chunks("PMC1") if "Nb = 11" in x.content)
    vs = {"question": "How many samples were taken in that season?", "quote": "number of samples Nb = 11",
          "quantity": "number of samples", "value": "11", "binding_terms": ["number of samples"], "symbol": ""}
    assert "s3_value_statement" in _l1(c, idx, vs, vs_chunk, "S3")["reasons"]
    p_chunk = next(x for x in c.paper_chunks("PMC1") if "(p = 0.01)" in x.content)
    pv = {"question": "What significance level did that season's small sample give?",
          "quote": "season (p = 0.01)", "quantity": "significance level", "value": "0.01",
          "binding_terms": ["season"], "symbol": ""}
    assert "s3_value_statement" not in _l1(c, idx, pv, p_chunk, "S3")["reasons"]
    assert "s3_value_statement" in _l1(c, idx, pv, p_chunk, "S3", vs_stoplist=[])["reasons"]


def test_checks_use_the_pool_headers_stoplist(tmp_path: Path) -> None:
    """One statistical stoplist per round: the drafter's layer-1 checks read the list
    the pool recorded, not a second copy of the default."""
    pv = {"question": "What significance level did that season's small sample give?", "quote": "season (p = 0.01)",
          "quantity": "significance level", "value": "0.01", "binding_terms": ["season"], "symbol": ""}
    seen = {}
    for name, pc in (("default", None), ("none", {"vs_stoplist": []})):
        path, cfg = setup(tmp_path / name, pool_cfg=pc)
        r = dr.Run(cfg, g.file_sha(path), out(cfg), "O", provenance=lambda: PROV)
        r.load()
        _ = r.corpus
        chunk = next(x for x in r.corpus.paper_chunks("PMC1") if "(p = 0.01)" in x.content)
        seen[name] = ck.layer1(pv, None, stratum="S3", paper="PMC1", source_chunk=chunk, idx=r.idx, cfg=r.checks_cfg)
    assert seen["default"]["verdict"] == "pass"
    assert "s3_value_statement" in seen["none"]["reasons"]


def test_lexical_leak_guard(tmp_path: Path) -> None:
    # with no frequent-token exclusion, every quote token is distinctive
    c, idx = _index(tmp_path, top=0)
    d, chunk = _good(c)
    leaky = {**d, "question": "What was measured for the folded boats, in m per s?"}
    r = _l1(c, idx, leaky, chunk)
    assert r["leak_overlap"] is not None
    ov, n = ck.leak_overlap("measured m s", "measured Qb = 0.15 m/s", set())
    assert n == 5 and ov == pytest.approx(2 / 5)                    # {measured, qb, 0, 15, m}; "s" is a stop word
    assert "lexical_leak" in _l1(c, idx, {**d, "question": "How was it measured, in m, for qb?"},
                                 chunk)["reasons"]
    # the threshold is a strict "> 50%": exactly half passes, 3 of 5 is rejected
    assert ck.leak_overlap("alpha", "alpha beta", set())[0] == 0.5
    half = {**d, "question": "How fast did the folded boats go, as measured in the calm case, in m?"}
    r = _l1(c, idx, half, chunk)   # distinctive {measured, qb, 0, 15, m}; question has measured, m -> 2/5
    assert r["leak_overlap"] == pytest.approx(0.4) and r["leak_ok"] is True
    r = _l1(c, idx, {**d, "question": "How fast was qb measured in the calm case, in m?"}, chunk)
    assert r["leak_overlap"] == pytest.approx(0.6) and r["leak_ok"] is False and "lexical_leak" in r["reasons"]
    r = _l1(c, idx, {**d, "question": "How fast did the boats go in the calm case, in m?"}, chunk, leak_threshold=0.1)
    assert r["leak_overlap"] == pytest.approx(0.2) and r["leak_ok"] is False
    r = _l1(c, idx, {**d, "question": "How fast did the boats go in the calm case, in m?"}, chunk, leak_threshold=0.2)
    assert r["leak_ok"] is True                                        # exactly at the threshold passes


def test_symbol_collision_and_multi_value_flag(tmp_path: Path) -> None:
    texts = ["where Kd is the decay constant of the dye. Later, where Kd is the dissociation rate of the ligand. "
             "We set Kd = 0.2 in case one and Kd = 0.4 in case two."]
    assert ck.symbol_collision("Kd", texts, 0.5) is True
    assert ck.symbol_collision("Kd", texts[:1][0].split(" Later")[:1], 0.5) is False
    assert ck.symbol_values("Kd", texts) == {"0.2", "0.4"}


def test_symbol_collision_is_a_flag_by_default_and_a_question_to_the_verifier() -> None:
    """Default ``flag``: the record population does not depend on the rules arm's
    definition sentences; the collision goes to the verifier as a note. ``reject``
    and ``off`` remain configurable."""
    assert ck.CHECK_DEFAULTS["collision"] == "flag"
    text = ("Dye study. where Kd is the decay constant of the dye. " + sup.FILLER
            + "Binding. where Kd is the dissociation rate of the ligand. " + sup.FILLER
            + "In the bright case we found Kd = 0.2 per hour for the dye. " + sup.FILLER)
    rows = [g.parse_chunk(r, "t") for r in sup.chunk_rows("k1", text, "PMCK")]
    c = g.build_corpus(rows, "path_root")
    idx = ck.PaperIndex(c, 200)
    chunk = next(x for x in rows if "Kd = 0.2" in x.content)
    d = {"question": "What decay constant of the dye was found in the bright case?", "quote": "found Kd = 0.2 per hour",
         "quantity": "decay constant", "value": "0.2 per hour", "binding_terms": ["decay constant"], "symbol": "Kd"}
    r = ck.layer1(d, None, stratum="S1", paper="PMCK", source_chunk=chunk, idx=idx, cfg={})
    assert r["verdict"] == "pass" and "symbol_collision" in r["flags"] and "symbol_collision" not in r["reasons"]
    r2 = ck.layer1(d, None, stratum="S1", paper="PMCK", source_chunk=chunk, idx=idx, cfg={"collision": "reject"})
    assert r2["verdict"] == "reject" and "symbol_collision" in r2["reasons"]
    r3 = ck.layer1(d, None, stratum="S1", paper="PMCK", source_chunk=chunk, idx=idx, cfg={"collision": "off"})
    assert "symbol_collision" not in r3["flags"] and "symbol_collision" not in r3["reasons"]
    plain = ck.verify_prompt(d, chunk.content, text[:200], "")
    noted = ck.verify_prompt(d, chunk.content, text[:200], "", flags=r["flags"])
    assert "NOTE:" not in plain and "symbol_unique" in noted and "more than one way" in noted
    assert "glossary" not in noted.casefold() and "SYMBOL: Kd" in noted
    assert "same_case" in ck.verify_prompt(d, chunk.content, "", "", flags=["multi_value"])


def test_cross_record_duplicates_and_shared_chunks() -> None:
    def rec(rid: str, q: str, gold: list[str]) -> dict[str, Any]:
        return {"record_id": rid, "paper": "P", "draft": {"question": q},
                "l1": {"verdict": "pass", "reasons": [], "flags": [], "gold_chunk_ids": gold}}

    rs = [rec("a", "How fast?", ["c1"]), rec("b", "How  FAST?", ["c2"]), rec("c", "How slow?", ["c1", "c3"])]
    ck.cross_record(rs)
    assert rs[1]["l1"]["reasons"] == ["duplicate_question"] and rs[1]["l1"]["verdict"] == "reject"
    assert "shared_chunk" in rs[0]["l1"]["flags"] and "shared_chunk" in rs[2]["l1"]["flags"]


def test_wilson() -> None:
    lo, hi = dr.wilson(34, 40)
    assert lo == pytest.approx(0.7091, abs=1e-3) and hi == pytest.approx(0.9300, abs=1e-3)
    assert dr.wilson(0, 0) == (None, None)


def test_promotion_condition_at_the_boundary() -> None:
    """Prereg §3.5 / DEVIATIONS D-1: the bound is from the raw counts at z = 1.959963984540054,
    compared with <= at full float precision. 8 of 100 (0.149981) passes; 9 of 100 fails;
    the z = 1.96 variant gives the same verdicts (0.149983)."""
    assert dr.WILSON_Z == 1.959963984540054 and dr.PROMOTION_THRESHOLD == 0.15 and dr.PROMOTION_MIN_JUDGED == 30
    ok = dr.promotion_condition(100, 8)
    assert ok["met"] is True and ok["upper_bound_6dp"] == "0.149981" and ok["upper_bound"] <= 0.15
    assert ok["upper_bound"] == pytest.approx(0.14998107700948735, abs=1e-12)
    bad = dr.promotion_condition(100, 9)
    assert bad["met"] is False and bad["upper_bound_6dp"] == "0.162262"
    assert dr.wilson(8, 100, z=1.96)[1] <= 0.15 < dr.wilson(9, 100, z=1.96)[1]
    # the table in DEVIATIONS D-1
    for n, k, six, met in ((30, 0, "0.113513", True), (30, 1, "0.166704", False),
                           (50, 2, "0.134601", True), (50, 3, "0.162171", False)):
        c = dr.promotion_condition(n, k)
        assert (c["upper_bound_6dp"], c["met"]) == (six, met), (n, k)
    # the 30-judged floor: a smaller judged n fails whatever the bound
    assert dr.promotion_condition(29, 0)["met"] is False and dr.promotion_condition(29, 0)["upper_bound"] < 0.15
    assert dr.promotion_condition(30, 0)["met"] is True


class FirstSeenBadCLI(FakeCLI):
    """An anomalous context on the FIRST call of each of the first ``k`` distinct prompts after
    the probe's; every re-issue is clean (so no record has two anomalies in a row)."""

    def __init__(self, k: int) -> None:
        super().__init__()
        self.k, self.seen, self.bad = k, [], set()

    def __call__(self, cmd: list[str], **kw: Any) -> SimpleNamespace:  # type: ignore[override]
        with self.lock:
            p = kw["input"]
            mark = p not in self.seen and len(self.seen) >= 1 and len(self.bad) < self.k
            if p not in self.seen:
                self.seen.append(p)
            if mark:
                self.bad.add(p)
        res = super().__call__(cmd, **kw)
        if mark:
            o = json.loads(res.stdout)
            o["modelUsage"]["claude-opus-5-5"]["inputTokens"] = 10_000_000
            res.stdout = json.dumps(o)
        return res


def test_isolation_anomalies_across_records_hit_the_segment_rate(tmp_path: Path) -> None:
    """Single anomalies on distinct records (never two in a row): max(2, 5% of the segment)
    are tolerated, one more is the hard stop."""
    path, cfg = _drafted(tmp_path)
    run(path, "preflight", "--stage", "verify", "--set", "O")
    n_todo = len([r for r in acceptance(cfg) if r["layer"] == "l1" and r["verdict"] == "pass"])
    assert 4 <= n_todo < 40                                    # 5% of the segment < 2: the floor applies
    assert run(path, "verify", "--set", "O", "--notice-acknowledged", cli=FirstSeenBadCLI(2)) == 0
    st = json.loads((out(cfg) / "verify-state-O.json").read_text())
    assert st["status"] == "complete" and st["totals"]["since_start"]["cli_isolation_anomalies"] == 2
    path, cfg = _drafted(tmp_path / "b")
    run(path, "preflight", "--stage", "verify", "--set", "O")
    assert run(path, "verify", "--set", "O", "--notice-acknowledged", cli=FirstSeenBadCLI(3)) == 1
    st = json.loads((out(cfg) / "verify-state-O.json").read_text())
    assert st["status"] == "hard_stop" and st["reason"] == "isolation_failed"


def test_three_anomalies_in_a_row_stop_even_under_a_lax_segment_rate(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(dr, "ISOLATION_WAVE_RATE", 100.0)           # the segment rule can never fire
    test_a_persistent_anomaly_on_one_record_is_a_hard_stop(tmp_path)


@pytest.mark.parametrize("usage", [None, {}, {"claude-opus-5-5": {"input_tokens": 400, "output_tokens": 30}},
                                   {"claude-opus-5-5": "400"}])
def test_an_unreadable_usage_block_is_an_isolation_anomaly_not_a_clean_call(tmp_path: Path, usage: Any) -> None:
    """Fail closed: a CLI that drops or renames ``modelUsage`` / its token fields must not
    pass the isolation check with an unknown (or 0) context."""
    path, cfg = _drafted(tmp_path)
    run(path, "preflight", "--stage", "verify", "--set", "O")

    class Bare(FakeCLI):
        def __call__(self, cmd: list[str], **kw: Any) -> SimpleNamespace:  # type: ignore[override]
            res = super().__call__(cmd, **kw)
            o = json.loads(res.stdout)
            if usage is None:
                del o["modelUsage"]
            else:
                o["modelUsage"] = usage
            res.stdout = json.dumps(o)
            return res

    cli = Bare()
    assert run(path, "verify", "--set", "O", "--notice-acknowledged", cli=cli) == 1
    st = json.loads((out(cfg) / "verify-state-O.json").read_text())
    assert st["reason"] == "isolation_failed" and len(cli.calls) == 3
    assert dr.cli_usage({"result": "{}", "modelUsage": usage}, "claude-opus-5-5")["context_tokens"] is None


def test_clear_hard_stop_never_clears_a_budget_or_limit_stop(tmp_path: Path) -> None:
    """Even a state that (by a bug or a hand edit) says hard_stop with a budget reason."""
    path, cfg = _drafted(tmp_path)
    run(path, "preflight", "--stage", "verify", "--set", "O")
    assert run(path, "verify", "--set", "O", "--notice-acknowledged", cli=FakeCLI(ctx=10_000_000)) == 1
    state = out(cfg) / "verify-state-O.json"
    for reason in ("budget_stop: HTTP 503 (provider_notice)", "usage_limit", "cli_cost_cap"):
        st = json.loads(state.read_text())
        st["reason"] = reason
        state.write_text(json.dumps(st))
        cli = FakeCLI()
        assert run(path, "verify", "--set", "O", "--notice-acknowledged", "--resume", "--clear-hard-stop",
                   reason, "--clear-note", "x", cli=cli) == 1
        assert not cli.calls and json.loads(state.read_text())["reason"] == reason
