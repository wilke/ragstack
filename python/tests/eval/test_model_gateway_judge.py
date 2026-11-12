"""The model-gateway client of judge validation 2 (JUDGE-VALIDATION-2-PLAN.md §3, §4, §10; Amendments 4-6):
pinned sampling, the per-class identity gate, the outage pause, the configuration reads, the canaries,
the epochs, the self-check guard, the canary selection and the dry run.

Everything runs against an injected fake transport with FAKE values: no gateway, no model, no
network. The private config's address, header names and key path are invented here; nothing real
is read. Each test names the mutation it catches (the guarded logic removed -> the test fails).
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

_EVAL = Path(__file__).resolve().parents[2] / "scripts" / "eval"
if str(_EVAL) not in sys.path:
    sys.path.insert(0, str(_EVAL))

import alias_judge3_selfcheck as sc  # noqa: E402
import model_gateway_judge as mg  # noqa: E402

FP_H, BE_H, KEY_H, FB_H = "X-Fake-Fingerprint", "X-Fake-Backend", "X-Fake-Auth", "X-Fake-Fallback"
PIN_C = "v1:fakepinc"
PIN_OTHER = "v1:fakeother"
PART_LOCAL = "v1-partial:fakelocal"
HOSTED_OK, HOSTED_OTHER = "v1-partial:fakehosta", "v1-partial:fakehostb"
GRADES = ("full", "partial", "none")


# ------------------------------------------------------------------ fakes

class Resp:
    def __init__(self, status: int, headers: dict[str, str], data: Any) -> None:
        self.status_code, self.headers, self._d = status, headers, data

    def json(self) -> Any:
        return self._d


class FakeTransport:
    """A scripted model gateway. ``script`` overrides successive chat replies with
    ``(status, fingerprint, served_model)``; ``answer(question, k)`` returns the grade for the k-th
    time a question is asked; ``configs`` are successive configuration-endpoint entries for the
    model (``None`` = unreadable)."""

    def __init__(self, model: str, fp: str | None, *, script: list | None = None, answer: Any = None,
                 configs: list | None = None, config_default: dict | None = None) -> None:
        self.model, self.fp = model, fp
        self.script = list(script or [])
        self.answer = answer or (lambda q, k: "full")
        self.configs = list(configs or [])
        self.config_default = config_default if config_default is not None else {"fingerprint": fp, "mismatch": []}
        self.posts: list[dict[str, Any]] = []
        self.headers_seen: list[dict[str, str]] = []
        self.gets = 0
        self.seen: Counter[str] = Counter()

    def post(self, url: str, headers: dict[str, str] | None = None, json: dict | None = None) -> Resp:  # noqa: A002
        assert json is not None and headers is not None
        self.posts.append(json)
        self.headers_seen.append(headers)
        ov = self.script.pop(0) if self.script else None
        status, fp, model = ov if ov is not None else (200, self.fp, self.model)
        if status != 200:
            return Resp(status, {}, {"error": "gateway"})
        user = json["messages"][1]["content"]
        q = re.search(r"QUESTION: (.*)\n", user).group(1)
        self.seen[q] += 1
        graded = "How fully" in user
        n = int(json.get("n", 1))
        grade = self.answer(q, self.seen[q]) if graded else "yes"
        data = {"model": model, "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                                          "completion_tokens_details": {"reasoning_tokens": 3}},
                "choices": [{"index": i, "finish_reason": "stop",
                             "message": {"content": f'{{"reason": "r", "answers": "{grade}"}}', "reasoning": "t"}}
                            for i in range(n)]}
        h = {BE_H: "fake-instance-1"}
        if fp:
            h[FP_H] = fp
        return Resp(200, h, data)

    def get(self, url: str, headers: dict[str, str] | None = None) -> Resp:
        self.gets += 1
        e = self.configs.pop(0) if self.configs else self.config_default
        if e is None:
            return Resp(503, {}, {})
        return Resp(200, {}, {"models": {self.model: e}})


class Sleeper:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, s: float) -> None:
        self.calls.append(s)


def priv(tmp: Path) -> dict[str, Any]:
    key = tmp / "fake.key"
    key.write_text("fake-key-value\n")
    return mg.validate_private({"base_url": "fake-scheme://fake-gateway", "chat_path": "fake/chat",
                                "config_path": "fake/config", "key_file": str(key), "auth_header": KEY_H,
                                "auth_template": "Fake {key}", "fallback_header": FB_H,
                                "fallback_off_value": "off", "fingerprint_header": FP_H, "backend_header": BE_H})


LOCAL = {"model": "fake-local-model", "class": "local", "pinned_fingerprint": PIN_C,
         "sampling": {"thinking": True, "max_tokens": 8192, "temperature": 0.7, "top_p": 0.95, "top_k": 20, "n": 5},
         "canary": {"mode": "deterministic", "temperature": 0, "seed": 7}}
LOCAL_N1 = {**LOCAL, "sampling": {**LOCAL["sampling"], "n": 1}}
HOSTED = {"model": "fake-hosted-model", "class": "hosted", "freeze_set": [HOSTED_OK],
          "sampling": {"max_tokens": 4096, "temperature": None, "n": 1}, "canary": {"mode": "batch"},
          "h_reps": 2}

CASES = [{"name": f"c{i}", "rule": "r", "title": "T", "defs": [], "passage": f"Canary passage {i}.",
          "term": "zorb", "question": f"Canary question {i}?", "expected_label": GRADES[i % 3]} for i in range(12)]
EXPECTED = {c["question"]: c["expected_label"] for c in CASES}


def owner_answer(q: str, k: int) -> str:
    return EXPECTED.get(q, "full")


def items(n: int) -> list[dict[str, Any]]:
    return [{"item_id": f"p{i}", "question": f"Item question {i}?", "term": "zorb", "passage": f"Passage {i}.",
             "title": "T", "defs": [["ZB", "zorb"]]} for i in range(n)]


def study(tmp: Path, **judges: dict) -> dict[str, Any]:
    return mg.validate_study({"out_dir": str(tmp / "out"), "judges": judges or {"C": LOCAL},
                              "canary_cases": [f"c{i}" for i in range(10)]})


def gw(tmp: Path, t: FakeTransport) -> mg.Gateway:
    return mg.Gateway(priv(tmp), t)


# ------------------------------------------------------------------ config and requests

def test_local_judge_must_pin_every_sampling_setting() -> None:
    # mutation: removing the LOCAL_SAMPLING check lets a local judge run on the engine's top_k default
    bad = {**LOCAL, "sampling": {k: v for k, v in LOCAL["sampling"].items() if k != "top_k"}}
    with pytest.raises(SystemExit, match="top_k"):
        mg.validate_judge("C", bad)
    with pytest.raises(SystemExit, match="full fingerprint"):
        mg.validate_judge("C", {**LOCAL, "pinned_fingerprint": PART_LOCAL})
    with pytest.raises(SystemExit, match="deterministic"):
        mg.validate_judge("C", {**LOCAL, "canary": {"mode": "batch"}})


def test_every_request_carries_the_pin_the_fallback_off_header_and_the_key(tmp_path: Path) -> None:
    # mutation: dropping any pinned key from the body, or the fallback-off header, fails this
    t = FakeTransport(LOCAL["model"], PIN_C)
    cfg = study(tmp_path)
    g = gw(tmp_path, t)
    body, sent, _ = mg.build_body(cfg["judges"]["C"], "alias-judge/3", items(1)[0])
    raw = g.chat(body)
    assert raw.status == 200 and raw.fingerprint == PIN_C and raw.backend == "fake-instance-1"
    b = t.posts[0]
    assert {k: b[k] for k in ("max_tokens", "temperature", "top_p", "top_k", "n")} == \
        {"max_tokens": 8192, "temperature": 0.7, "top_p": 0.95, "top_k": 20, "n": 5}
    assert b["chat_template_kwargs"] == {"enable_thinking": True} and b["model"] == LOCAL["model"]
    assert sent == {"thinking": True, "max_tokens": 8192, "temperature": 0.7, "top_p": 0.95, "top_k": 20, "n": 5}
    assert t.headers_seen[0] == {KEY_H: "Fake fake-key-value", FB_H: "off"}


def test_canary_setting_and_hosted_body() -> None:
    # mutation: a canary sent without temperature 0 / the seed / n = 1, or a hosted body with a temperature
    loc = mg.validate_judge("C", LOCAL)
    body, sent, _ = mg.build_body(loc, "alias-judge/3", CASES[0], canary=True)
    assert (body["temperature"], body["seed"], body["n"], body["top_k"]) == (0, 7, 1, 20)
    assert sent["seed"] == 7 and sent["n"] == 1
    h = mg.validate_judge("H", HOSTED)
    hb, hs, _ = mg.build_body(h, "alias-judge/3", CASES[0], canary=True)
    assert "temperature" not in hb and "seed" not in hb and "chat_template_kwargs" not in hb
    assert hb["n"] == 1 and hb["max_tokens"] == 4096 and hs == {"max_tokens": 4096, "n": 1}


def test_same_user_message_for_every_judge() -> None:
    # mutation: a per-judge or per-class user message fails this (K0/K3 see one context, prereg §6.2)
    loc, h = mg.validate_judge("C", LOCAL), mg.validate_judge("H", HOSTED)
    for v in ("alias-judge/2", "alias-judge/3"):
        a, _, ua = mg.build_body(loc, v, items(1)[0])
        b, _, ub = mg.build_body(h, v, items(1)[0])
        assert ua == ub and a["messages"] == b["messages"]


def test_private_config_required_keys_and_key_field(tmp_path: Path) -> None:
    # mutation: dropping the required-keys check lets a run start without the fallback-off header name
    with pytest.raises(SystemExit, match="fallback_header"):
        mg.validate_private({"base_url": "x", "chat_path": "c", "config_path": "k", "key_file": "f",
                             "auth_header": "a", "fallback_off_value": "off", "fingerprint_header": "h"})
    kf = tmp_path / "env.json"
    kf.write_text(json.dumps({"fake": "k1"}))
    assert mg.load_key({"key_file": str(kf), "key_field": "fake"}) == "k1"


def test_no_address_or_header_name_in_the_module() -> None:
    # mutation: hard-coding a URL, a port or a header name in the client fails this
    src = (_EVAL / "model_gateway_judge.py").read_text(encoding="utf-8")
    assert not re.search(r"https?://|localhost|127\.0\.0\.1|:\d{4,5}\b", src)
    assert not re.search(r"""["'](?:X-[A-Za-z-]+|Authorization)["']""", src)


# ------------------------------------------------------------------ classify_reply

def _raw(status: int = 200, fp: str | None = PIN_C, model: str = "fake-local-model") -> mg.Raw:
    return mg.Raw(status=status, data={"model": model, "choices": [{"message": {"content": "{}"}}]}, fingerprint=fp)


def test_classify_reply_per_class(tmp_path: Path) -> None:
    # mutation: each line fails when its branch of classify_reply is removed or swapped
    p = priv(tmp_path)
    loc, h = mg.validate_judge("C", LOCAL), mg.validate_judge("H", HOSTED)
    assert mg.classify_reply(loc, _raw(), p) == ("ok", "")
    assert mg.classify_reply(loc, _raw(fp=PART_LOCAL), p)[0] == "outage"
    assert mg.classify_reply(loc, mg.Raw(status=503), p)[0] == "outage"
    assert mg.classify_reply(loc, mg.Raw(status=-1), p)[0] == "outage"
    assert mg.classify_reply(loc, _raw(fp=PIN_OTHER), p)[0] == "mismatch"
    assert mg.classify_reply(loc, _raw(fp=None), p) == ("mismatch", "fingerprint_missing")
    assert mg.classify_reply(loc, _raw(model="other"), p)[0] == "mismatch"
    assert mg.classify_reply(loc, mg.Raw(status=400, data={}), p)[0] == "error"
    hm = "fake-hosted-model"
    assert mg.classify_reply(h, _raw(fp=HOSTED_OK, model=hm), p) == ("ok", "")
    assert mg.classify_reply(h, _raw(fp=HOSTED_OTHER, model=hm), p)[0] == "mismatch"
    assert mg.classify_reply(h, _raw(fp=None, model=hm), p)[0] == "mismatch"
    assert mg.classify_reply(h, _raw(fp=HOSTED_OTHER, model=hm), p, observe_hosted=True) == ("ok", "")


def test_config_endpoint_reader(tmp_path: Path) -> None:
    # mutation: dropping a field from parse_config, or ignoring the contradiction list, fails this
    t = FakeTransport(LOCAL["model"], PIN_C, configs=[
        {"declared": {"rev": "a"}, "observed": {"engine": "x"}, "mismatch": [], "fingerprint": PIN_C},
        {"declared": {}, "observed": {}, "mismatch": ["context"], "fingerprint": PIN_C}, None])
    g = gw(tmp_path, t)
    loc = mg.validate_judge("C", LOCAL)
    e = g.read_config()
    assert e[LOCAL["model"]] == {"declared": {"rev": "a"}, "observed": {"engine": "x"}, "mismatch": [],
                                 "fingerprint": PIN_C, "fingerprint_kind": "full"}
    assert mg.check_config_read(loc, e) == ("pass", "")
    assert mg.check_config_read(loc, g.read_config())[0] == "mismatch"
    assert g.read_config() is None and mg.check_config_read(loc, None)[0] == "transient"


# ------------------------------------------------------------------ batches: the gate paths

def labels(out: Path, jid: str = "C") -> list[dict[str, Any]]:
    return mg._rows(mg.paths(out, jid)["labels"])


def test_batch_pass_and_the_stored_record(tmp_path: Path) -> None:
    # mutation: dropping any stored field (model, fingerprint, sampling as sent, date, private backend)
    t = FakeTransport(LOCAL["model"], PIN_C, answer=owner_answer)
    cfg = study(tmp_path)
    s = Sleeper()
    res = mg.run_batch(cfg, gw(tmp_path, t), "C", "b1", "alias-judge/3", items(3), CASES, sleep=s)
    assert res["status"] == "done" and res["stored"] == 3 and not res["void"] and s.calls == []
    assert len(t.posts) == 3 + 20 and t.gets == 2  # 10 canaries at start and end; start and end reads
    r = labels(Path(cfg["out_dir"]))[0]
    for k in ("grades", "reasons", "judge_version", "system_sha256", "prompt_sha256", "model", "fingerprint",
              "sampling_sent", "thinking_served", "reasoning_tokens", "date", "epoch", "private"):
        assert k in r, k
    assert r["grades"] == ["full"] * 5 and r["fingerprint"] == PIN_C and r["model"] == LOCAL["model"]
    assert r["sampling_sent"]["top_k"] == 20 and r["thinking_served"] is True and r["reasoning_tokens"] == 3
    assert r["private"] == {"backend": "fake-instance-1"} and "private" not in mg.public_view(r)
    assert len(mg.valid_rows(Path(cfg["out_dir"]), "C")) == 3


def test_outage_then_recover_reissues_the_paused_item(tmp_path: Path) -> None:
    # mutation: storing the partial-fingerprint reply, or not re-issuing the paused item, or not
    # re-reading the configuration endpoint before resuming, fails this
    script = [None] * 10 + [(503, None, None), (200, PART_LOCAL, LOCAL["model"])]
    t = FakeTransport(LOCAL["model"], PIN_C, script=script, answer=owner_answer,
                      configs=[{"fingerprint": PIN_C, "mismatch": []},           # start read
                               {"fingerprint": PART_LOCAL, "mismatch": []},      # pause 1, try 1
                               {"fingerprint": PIN_C, "mismatch": []}])          # pause 1, try 2 -> resume
    cfg = study(tmp_path)
    s = Sleeper()
    res = mg.run_batch(cfg, gw(tmp_path, t), "C", "b1", "alias-judge/3", items(2), CASES, sleep=s)
    assert res["status"] == "done" and res["stored"] == 2 and res["pauses"] == 2
    assert s.calls == [60.0, 60.0, 60.0]  # pause 1 (503): 2 tries; pause 2 (partial): 1 try
    rows = labels(Path(cfg["out_dir"]))
    assert [r["item_id"] for r in rows] == ["p0", "p1"] and all(r["fingerprint"] == PIN_C for r in rows)
    assert rows[0]["attempts"] == 3


def test_sustained_partial_is_a_mismatch_and_voids_the_batch(tmp_path: Path) -> None:
    # mutation: treating 5 partial reads as a pass, or not voiding / not stopping the judge, fails this
    t = FakeTransport(LOCAL["model"], PIN_C, script=[None] * 10 + [(200, PART_LOCAL, LOCAL["model"])],
                      answer=owner_answer, configs=[{"fingerprint": PIN_C, "mismatch": []}] +
                      [{"fingerprint": PART_LOCAL, "mismatch": []}] * 5)
    cfg = study(tmp_path)
    s = Sleeper()
    res = mg.run_batch(cfg, gw(tmp_path, t), "C", "b1", "alias-judge/3", items(2), CASES, sleep=s)
    assert res["status"].startswith("stopped:mismatch:transient_sustained") and res["void"] is True
    assert s.calls == [60.0] * 5 and res["stored"] == 0
    assert mg.paths(Path(cfg["out_dir"]), "C")["stopped"].exists()
    with pytest.raises(SystemExit, match="stopped"):
        mg.run_batch(cfg, gw(tmp_path, FakeTransport(LOCAL["model"], PIN_C)), "C", "b2", "alias-judge/3",
                     items(1), CASES, sleep=s)


def test_different_full_fingerprint_stops_at_once(tmp_path: Path) -> None:
    # mutation: routing a different full value through the outage pause (or accepting it) fails this
    t = FakeTransport(LOCAL["model"], PIN_C, script=[None] * 11 + [(200, PIN_OTHER, LOCAL["model"])],
                      answer=owner_answer)
    cfg = study(tmp_path)
    s = Sleeper()
    res = mg.run_batch(cfg, gw(tmp_path, t), "C", "b1", "alias-judge/3", items(3), CASES, sleep=s)
    assert res["status"] == f"stopped:mismatch:fingerprint_different:{PIN_OTHER}" and s.calls == []
    assert res["void"] is True and res["stored"] == 1
    assert mg.valid_rows(Path(cfg["out_dir"]), "C") == []  # the stopped pinned batch is void


def test_missing_fingerprint_on_a_reply_is_a_mismatch(tmp_path: Path) -> None:
    # mutation: storing a forwarded reply that carries no fingerprint fails this
    t = FakeTransport(LOCAL["model"], PIN_C, script=[None] * 10 + [(200, None, LOCAL["model"])],
                      answer=owner_answer)
    cfg = study(tmp_path)
    res = mg.run_batch(cfg, gw(tmp_path, t), "C", "b1", "alias-judge/3", items(2), CASES, sleep=Sleeper())
    assert res["status"] == "stopped:mismatch:fingerprint_missing" and res["stored"] == 0


def test_hosted_out_of_set_stops_that_candidate_rows_kept(tmp_path: Path) -> None:
    # mutation: voiding the hosted rows before the stop, or not stopping the candidate, fails this
    hm = HOSTED["model"]
    t = FakeTransport(hm, HOSTED_OK, script=[None] * 12 + [(200, HOSTED_OTHER, hm)], answer=owner_answer)
    cfg = study(tmp_path, H=HOSTED)
    res = mg.run_batch(cfg, gw(tmp_path, t), "H", "b1", "alias-judge/2", items(4), CASES, sleep=Sleeper())
    assert res["status"] == f"stopped:mismatch:fingerprint_not_in_freeze_set:{HOSTED_OTHER}"
    assert res["void"] is False and res["stored"] == 2
    out = Path(cfg["out_dir"])
    assert [r["item_id"] for r in mg.valid_rows(out, "H")] == ["p0", "p1"]
    assert mg.paths(out, "H")["stopped"].exists()
    assert labels(out, "H")[0]["verdict"] == 1 and "grade" not in labels(out, "H")[0]


def test_hosted_needs_a_freeze_set(tmp_path: Path) -> None:
    # mutation: running a hosted batch with no recorded set (every value would pass) fails this
    cfg = study(tmp_path, H={**HOSTED, "freeze_set": []})
    with pytest.raises(SystemExit, match="freeze_set"):
        mg.run_batch(cfg, gw(tmp_path, FakeTransport(HOSTED["model"], HOSTED_OK)), "H", "b1", "alias-judge/3",
                     items(1), CASES, sleep=Sleeper())


def test_contradiction_at_start_read_is_a_mismatch(tmp_path: Path) -> None:
    # mutation: ignoring a non-empty contradiction list on the configuration record fails this
    t = FakeTransport(LOCAL["model"], PIN_C, configs=[{"fingerprint": PIN_C, "mismatch": ["context"]}])
    cfg = study(tmp_path)
    res = mg.run_batch(cfg, gw(tmp_path, t), "C", "b1", "alias-judge/3", items(1), CASES, sleep=Sleeper())
    assert res["status"].startswith("stopped:mismatch:contradictions") and t.posts == []


def test_failed_end_read_voids_rows_since_the_last_passing_check(tmp_path: Path) -> None:
    # mutation: keeping the rows after a different full value at the end read fails this
    t = FakeTransport(LOCAL["model"], PIN_C, answer=owner_answer,
                      configs=[{"fingerprint": PIN_C, "mismatch": []}, {"fingerprint": PIN_OTHER, "mismatch": []}])
    cfg = study(tmp_path)
    res = mg.run_batch(cfg, gw(tmp_path, t), "C", "b1", "alias-judge/3", items(3), CASES, sleep=Sleeper())
    assert res["status"].startswith("void:config_end") and res["void"] and res["void_from_seq"] == 0
    assert mg.valid_rows(Path(cfg["out_dir"]), "C") == []


def test_a_batch_id_runs_once(tmp_path: Path) -> None:
    # mutation: allowing a re-issue of a batch fails this
    cfg = study(tmp_path)
    mg.run_batch(cfg, gw(tmp_path, FakeTransport(LOCAL["model"], PIN_C, answer=owner_answer)), "C", "b1",
                 "alias-judge/3", items(1), CASES, sleep=Sleeper())
    with pytest.raises(SystemExit, match="never re-issued"):
        mg.run_batch(cfg, gw(tmp_path, FakeTransport(LOCAL["model"], PIN_C)), "C", "b1", "alias-judge/3",
                     items(1), CASES, sleep=Sleeper())


def test_invalid_reply_reissued_once_at_n1(tmp_path: Path) -> None:
    # mutation: no re-issue (or more than one) of an invalid n = 1 reply fails this
    seq = iter(["unsure", "full", "unsure", "unsure"])
    t = FakeTransport(LOCAL["model"], PIN_C,
                      answer=lambda q, k: owner_answer(q, k) if q.startswith("Canary") else next(seq))
    cfg = study(tmp_path, C=LOCAL_N1)
    res = mg.run_batch(cfg, gw(tmp_path, t), "C", "b1", "alias-judge/3", items(2), CASES, sleep=Sleeper())
    rows = labels(Path(cfg["out_dir"]))
    assert res["invalid_reissues"] == 2 and [r["grade"] for r in rows] == ["full", None]
    assert [r["attempts"] for r in rows] == [2, 2]


# ------------------------------------------------------------------ canaries and epochs

def _flip(n: int):
    """An answer that flips the end canary (2nd ask) on the first ``n`` canary cases."""
    def ans(q: str, k: int) -> str:
        base = owner_answer(q, k)
        i = int(q.split()[-1].rstrip("?")) if q.startswith("Canary") else 99
        if k == 2 and i < n:
            return GRADES[(GRADES.index(base) + 1) % 3]
        return base
    return ans


@pytest.mark.parametrize("flips,void", [(2, False), (3, True)])
def test_canary_void_at_three_of_ten(tmp_path: Path, flips: int, void: bool) -> None:
    # mutation: a void threshold other than 3 (or no canary comparison) fails one of the two cases
    t = FakeTransport(LOCAL["model"], PIN_C, answer=_flip(flips))
    cfg = study(tmp_path)
    res = mg.run_batch(cfg, gw(tmp_path, t), "C", "b1", "alias-judge/3", items(2), CASES, sleep=Sleeper())
    assert res["canary"]["differ"] == flips and res["void"] is void
    if void:
        assert res["status"] == "void:canary" and res["drift_report"] == "template_parser_or_default_drift"
        assert mg.valid_rows(Path(cfg["out_dir"]), "C") == []
    else:
        assert res["status"] == "done" and res["canary"]["reported"] is True  # deterministic: reported


def test_compare_canaries_owner_agreement_clause() -> None:
    # mutation: dropping the owner-agreement clause or the deterministic report fails this
    exp = {f"c{i}": "full" for i in range(10)}
    start = dict(exp)
    end = {**exp, "c0": "none", "c1": "none", "c2": None}
    r = mg.compare_canaries(start, end, exp, deterministic=False)
    assert r["void"] and r["reasons"] == ["differ:3", "owner_agreement_drop:3"] and not r["reported"]
    r2 = mg.compare_canaries(start, {**exp, "c0": "none"}, exp, deterministic=True)
    assert not r2["void"] and r2["reported"]
    assert not mg.compare_canaries(start, {**exp, "c0": "none"}, exp, deterministic=False)["reported"]


@pytest.mark.parametrize("changed,boundary", [(2, False), (3, True)])
def test_epoch_chaining(tmp_path: Path, changed: int, boundary: bool) -> None:
    # mutation: not comparing a start canary with the previous end canary (or a threshold other than 3)
    cfg = study(tmp_path)
    mg.run_batch(cfg, gw(tmp_path, FakeTransport(LOCAL["model"], PIN_C, answer=owner_answer)), "C", "b1",
                 "alias-judge/3", items(1), CASES, sleep=Sleeper())

    def shifted(q: str, k: int) -> str:  # batch 2: the first `changed` canaries answer differently throughout
        base = owner_answer(q, k)
        i = int(q.split()[-1].rstrip("?")) if q.startswith("Canary") else 99
        return GRADES[(GRADES.index(base) + 1) % 3] if i < changed else base

    res = mg.run_batch(cfg, gw(tmp_path, FakeTransport(LOCAL["model"], PIN_C, answer=shifted)), "C", "b2",
                       "alias-judge/3", items(1), CASES, sleep=Sleeper())
    assert res["status"] == "done" and res["epoch_boundary"] is boundary
    assert res["differ_from_previous_end"] == changed and res["epoch"] == (1 if boundary else 0)
    assert labels(Path(cfg["out_dir"]))[-1]["epoch"] == (1 if boundary else 0)


def test_canaries_need_owner_labels_and_ten_cases(tmp_path: Path) -> None:
    # mutation: running canaries against an empty owner label fails this
    unlabelled = [{**c, "expected_label": ""} if c["name"] == "c3" else c for c in CASES]
    cfg = study(tmp_path)
    with pytest.raises(SystemExit, match="owner-set"):
        mg.run_batch(cfg, gw(tmp_path, FakeTransport(LOCAL["model"], PIN_C)), "C", "b1", "alias-judge/3",
                     items(1), unlabelled, sleep=Sleeper())
    with pytest.raises(SystemExit, match="10 distinct"):
        mg.validate_study({"out_dir": "x", "judges": {"C": LOCAL}, "canary_cases": ["c0"]})


# ------------------------------------------------------------------ self-check and selection

def test_selfcheck_refuses_while_any_label_is_empty(tmp_path: Path) -> None:
    # mutation: removing require_owner_labels from run_selfcheck fails this
    cfg = study(tmp_path)
    t = FakeTransport(LOCAL["model"], PIN_C)
    with pytest.raises(SystemExit, match="owner-set"):
        mg.run_selfcheck(cfg, gw(tmp_path, t), [{**CASES[0], "expected_label": ""}], sleep=Sleeper())
    with pytest.raises(SystemExit, match="no cases"):
        mg.run_selfcheck(cfg, gw(tmp_path, t), sc.load_cases(), sleep=Sleeper())
    assert t.posts == []


def test_select_canaries_rule() -> None:
    # mutation: ignoring a judge, accepting an all-invalid case, or filling out of consistency order
    order = [f"c{i}" for i in range(13)]
    stable = {c: ["full"] * 5 for c in order}
    a = dict(stable)
    b = dict(stable)
    b["c1"] = ["full", "full", "full", "full", "none"]       # unstable on judge B only: consistency 9
    a["c2"] = [None] * 5                                     # invalid everywhere: consistency 5
    b["c4"] = ["full", "none", "full", "none", "partial"]   # consistency 8
    r = mg.select_canaries({"A": a, "B": b}, order)
    assert r["qualifying"] == 10 and r["canaries"] == [c for c in order if c not in ("c1", "c2", "c4")]
    assert r["filled"] == []
    r2 = mg.select_canaries({"A": a, "B": b}, order[:12])
    assert r2["qualifying"] == 9 and r2["filled"] == ["c1"]
    assert r2["flip_rates"]["c1"] == {"A": 0.0, "B": 0.4}
    assert r2["all_flip_rates"]["c4"]["B"] == 0.8


def test_selfcheck_runs_five_times_under_the_canary_setting(tmp_path: Path) -> None:
    # mutation: running the self-check at batch settings, or not recording the hosted fingerprints
    hm = HOSTED["model"]
    t = FakeTransport(hm, HOSTED_OK, script=[None] * 7 + [(200, HOSTED_OTHER, hm)], answer=owner_answer)
    cfg = study(tmp_path, H={**HOSTED, "freeze_set": []})
    res = mg.run_selfcheck(cfg, gw(tmp_path, t), CASES, sleep=Sleeper())
    assert len(t.posts) == 5 * len(CASES) and all(b["n"] == 1 for b in t.posts)
    assert all("How fully" in b["messages"][1]["content"] for b in t.posts)
    j = res["judges"]["H"]
    assert j["status"] == "done" and j["observed_fingerprints"] == sorted([HOSTED_OK, HOSTED_OTHER])
    assert all(v == 5 for v in j["agree_with_owner"].values())
    assert res["selection"]["canaries"] == [f"c{i}" for i in range(10)]
    t2 = FakeTransport(LOCAL["model"], PIN_C, answer=owner_answer)
    mg.run_selfcheck(study(tmp_path / "l"), gw(tmp_path, t2), CASES, runs=1, sleep=Sleeper())
    assert all((b["temperature"], b["seed"], b["n"]) == (0, 7, 1) for b in t2.posts)


# ------------------------------------------------------------------ dry run and the warning limit

def plan_cfg(tmp: Path) -> dict[str, Any]:
    j = {"C": {**LOCAL}, "E": {**LOCAL, "pinned_fingerprint": "v1:fakepine"},
         "KX1": {**LOCAL_N1, "h_reps": 2}, "KX2": HOSTED, "KX3": HOSTED, "KX4": {**HOSTED, "optional": True}}
    return mg.validate_study({"out_dir": str(tmp / "out"), "judges": j})


def test_projection_reproduces_the_plan_figures(tmp_path: Path) -> None:
    # mutation: dropping canaries or the self-check from the count, or counting C/E, fails this
    cfg = plan_cfg(tmp_path)
    pr = mg.project(cfg, 850, 170, 20, 7580)
    assert pr["per_judge"]["KX2"]["total"] == 2200 and pr["per_judge"]["C"]["total"] == 1840
    assert pr["counted"] == ["KX1", "KX2", "KX3", "KX4"]
    assert pr["total"] == 7580 + 4 * 2200 and pr["capped_total"] == 18018 and not pr["warning"]


def test_warning_is_printed_with_the_drop_order_never_cut(tmp_path: Path) -> None:
    # mutation: cutting a run automatically, or not printing the warning, fails this
    cfg = plan_cfg(tmp_path)
    lines: list[str] = []
    pr = mg.dry_run(cfg, None, 1000, 170, [], 8700, n_cases=20, echo=lines.append)
    assert pr["capped_total"] == 20570 and pr["warning"]
    assert [s["capped_total_after"] for s in pr["drop_order"]][0] == 19470
    assert pr["per_judge"]["KX4"]["labels_by_prompt"]["alias-judge/2"] == 1000  # nothing removed
    assert any(x.startswith("WARNING") for x in lines) and any("drop the optional" in x for x in lines)


def test_cli_dry_run_builds_requests_without_sending(tmp_path: Path) -> None:
    # mutation: a dry run that opens a transport or sends fails this (no transport and no private config)
    cfg = tmp_path / "study.json"
    cfg.write_text(json.dumps({"out_dir": str(tmp_path / "out"), "judges": {"KX2": HOSTED},
                               "warning_limit": 10}))
    it = tmp_path / "items.jsonl"
    it.write_text("".join(json.dumps(r) + "\n" for r in items(3)))
    cs = tmp_path / "cases.json"
    cs.write_text(json.dumps(CASES))
    lines: list[str] = []

    class Boom:
        def post(self, *a: Any, **k: Any) -> None:
            raise AssertionError("sent")

        get = post

    rc = mg.main(["--config", str(cfg), "dry-run", "--items", str(it), "--cases", str(cs)], transport=Boom(),
                 echo=lines.append)
    assert rc == 0 and any(x.startswith("WARNING") for x in lines)
    assert json.loads(lines[-1])["requests_built"] == {"KX2": 2 * 3 + len(CASES)}


def test_cli_label_and_read_config(tmp_path: Path) -> None:
    # mutation: a CLI path that bypasses run_batch's gate or the private config fails this
    p = priv(tmp_path)
    pc = tmp_path / "priv.json"
    pc.write_text(json.dumps({k: v for k, v in p.items() if k in mg.PRIVATE_KEYS and k != "config_fields"}))
    cfg = tmp_path / "study.json"
    cfg.write_text(json.dumps({"out_dir": str(tmp_path / "out"), "judges": {"C": LOCAL},
                               "canary_cases": [f"c{i}" for i in range(10)]}))
    it = tmp_path / "items.jsonl"
    it.write_text("".join(json.dumps(r) + "\n" for r in items(2)))
    cs = tmp_path / "cases.json"
    cs.write_text(json.dumps(CASES))
    lines: list[str] = []
    t = FakeTransport(LOCAL["model"], PIN_C, answer=owner_answer)
    rc = mg.main(["--config", str(cfg), "--private-config", str(pc), "label", "--judge", "C", "--batch", "b1",
                  "--version", "alias-judge/3", "--items", str(it), "--cases", str(cs)], transport=t,
                 sleep=Sleeper(), echo=lines.append)
    assert rc == 0 and json.loads(lines[-1])["status"] == "done"
    rc = mg.main(["--config", str(cfg), "--private-config", str(pc), "read-config"], transport=t, echo=lines.append)
    assert rc == 0 and json.loads(lines[-1])["C"]["check"] == "pass"
    with pytest.raises(SystemExit, match="private-config"):
        mg.main(["--config", str(cfg), "read-config"], transport=t)
