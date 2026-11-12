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
import threading
import time
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
                 configs: list | None = None, config_default: dict | None = None, delay: float = 0.0,
                 n_served: int | None = None) -> None:
        self.model, self.fp = model, fp
        self.script = list(script or [])
        self.answer = answer or (lambda q, k: "full")
        self.configs = list(configs or [])
        self.config_default = config_default if config_default is not None else {"fingerprint": fp, "mismatch": []}
        self.posts: list[dict[str, Any]] = []
        self.headers_seen: list[dict[str, str]] = []
        self.gets = 0
        self.seen: Counter[str] = Counter()
        self.delay, self.now, self.peak = delay, 0, 0
        self.n_served = n_served
        self.lock = threading.Lock()

    def post(self, url: str, headers: dict[str, str] | None = None, json: dict | None = None) -> Resp:  # noqa: A002
        assert json is not None and headers is not None
        with self.lock:
            self.posts.append(json)
            self.headers_seen.append(headers)
            ov = self.script.pop(0) if self.script else None
            self.now += 1
            self.peak = max(self.peak, self.now)
        if self.delay:
            time.sleep(self.delay)
        with self.lock:
            self.now -= 1
        status, fp, model = ov if ov is not None else (200, self.fp, self.model)
        if status != 200:
            return Resp(status, {}, {"error": "gateway"})
        user = json["messages"][1]["content"]
        q = re.search(r"QUESTION: (.*)\n", user).group(1)
        with self.lock:
            self.seen[q] += 1
        graded = "How fully" in user
        n = self.n_served if self.n_served is not None else int(json.get("n", 1))
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
        return Resp(200, {}, config_doc(self.model, e))


def config_doc(model: str, e: dict) -> dict:
    """The configuration endpoint's real shape for one model; ``e`` is either ``{"backends": [...]}``
    (raw) or the shorthand ``{"fingerprint", "mismatch"}`` for one backend."""
    if "backends" in e:
        bs = e["backends"]
    else:
        fp = e.get("fingerprint")
        b = {"name": "fake-backend-1", "route": "fake-route", "state": "up", "config": fp,
             "partial": bool(fp) and "-partial:" in fp,
             "observed": e.get("observed", {"engine_version": "0.0-fake", "started": "t0"})}
        if e.get("mismatch"):
            b["mismatch"] = e["mismatch"]
        bs = [b]
    return {"time": "2026-11-12T00:00:00Z", "models": [{"id": model, "declared": {"repo": "fake/repo"},
                                                        "backends": bs}]}


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

CASES = [{"name": f"c{i}", "rules": ["r"], "title": "T", "defs": [], "passage": f"Canary passage {i}.",
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


def test_config_endpoint_reader_real_shape(tmp_path: Path) -> None:
    # mutation: reading one fingerprint per model (not per backend), dropping a field, or ignoring a
    # backend's contradiction list fails this
    two = {"backends": [{"name": "fake-a", "route": "r1", "state": "up", "config": HOSTED_OK, "partial": True},
                        {"name": "fake-b", "route": "r2", "state": "up", "config": HOSTED_OTHER, "partial": True}]}
    t = FakeTransport(LOCAL["model"], PIN_C, configs=[
        {"fingerprint": PIN_C}, {"fingerprint": PIN_C, "mismatch": ["context"]}, None, two])
    g = gw(tmp_path, t)
    loc = mg.validate_judge("C", LOCAL)
    e = g.read_config()
    assert e[LOCAL["model"]]["declared"] == {"repo": "fake/repo"} and e[LOCAL["model"]]["time"]
    b0 = e[LOCAL["model"]]["backends"][0]
    assert {k: b0[k] for k in ("name", "route", "state", "fingerprint", "fingerprint_kind", "observed", "mismatch")} \
        == {"name": "fake-backend-1", "route": "fake-route", "state": "up", "fingerprint": PIN_C,
            "fingerprint_kind": "full", "observed": {"engine_version": "0.0-fake", "started": "t0"},
            "mismatch": []}
    assert mg.check_config_read(loc, e) == ("pass", "")
    assert mg.check_config_read(loc, g.read_config())[0] == "mismatch"
    assert g.read_config() is None and mg.check_config_read(loc, None)[0] == "transient"
    e2 = g.read_config()
    assert mg.entry_fingerprints(e2[LOCAL["model"]]) == [HOSTED_OK, HOSTED_OTHER]
    assert [b["fingerprint_kind"] for b in e2[LOCAL["model"]]["backends"]] == ["partial", "partial"]
    # the partial flag decides over the prefix
    assert mg.fingerprint_kind("v1:x", partial=True) == "partial"
    # a local model with one pinned and one different full backend is a mismatch
    mixed = mg.parse_config(config_doc("m", {"backends": [{"config": PIN_C, "partial": False},
                                                         {"config": PIN_OTHER, "partial": False}]}))
    assert mg.check_config_read({**loc, "model": "m"}, mixed)[0] == "mismatch"


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
    with pytest.raises(SystemExit, match="owner-set"):  # the committed draft: 23 cases, labels not yet set
        mg.run_selfcheck(cfg, gw(tmp_path, t), sc.load_cases(), sleep=Sleeper())
    with pytest.raises(SystemExit, match="no cases"):
        mg.run_selfcheck(cfg, gw(tmp_path, t), [], sleep=Sleeper())
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


# ------------------------------------------------------------------ pause is not a void: resume

def ledger(out: Path, jid: str = "C") -> list[dict[str, Any]]:
    return mg._rows(mg.paths(out, jid)["batches"])


def test_error_pause_then_resume_after_restart(tmp_path: Path) -> None:
    # mutation: spending the batch id on a pause, re-running stored items or the start canary on resume,
    # or not persisting the checkpoint (a restart then cannot continue) fails this
    cfg = study(tmp_path)
    out = Path(cfg["out_dir"])
    t1 = FakeTransport(LOCAL["model"], PIN_C, script=[None] * 12 + [(400, None, None)], answer=owner_answer)
    r1 = mg.run_batch(cfg, gw(tmp_path, t1), "C", "b1", "alias-judge/3", items(5), CASES, sleep=Sleeper())
    assert r1["status"] == "paused:error:http_400" and r1["stored"] == 2 and not r1["void"]
    assert ledger(out) == [] and (out / "checkpoint-mg-C-b1.json").exists()
    assert not mg.paths(out, "C")["stopped"].exists()
    with pytest.raises(SystemExit, match="--resume"):
        mg.run_batch(cfg, gw(tmp_path, FakeTransport(LOCAL["model"], PIN_C)), "C", "b1", "alias-judge/3",
                     items(5), CASES, sleep=Sleeper())
    with pytest.raises(SystemExit, match="another batch is paused"):
        mg.run_batch(cfg, gw(tmp_path, FakeTransport(LOCAL["model"], PIN_C)), "C", "b2", "alias-judge/3",
                     items(1), CASES, sleep=Sleeper())
    # a "restart": a fresh study config, gateway and transport; only the files on disk carry over
    cfg2 = study(tmp_path)
    t2 = FakeTransport(LOCAL["model"], PIN_C, answer=owner_answer)
    t2.seen.update({c["question"]: 1 for c in CASES[:10]})  # the server-side ask count, for the end canary
    r2 = mg.run_batch(cfg2, gw(tmp_path, t2), "C", "b1", "alias-judge/3", items(5), CASES, sleep=Sleeper(),
                      resume=True)
    assert r2["status"] == "done" and r2["stored"] == 5 and r2["resumes"] == 1
    assert len(t2.posts) == 3 + 10 and t2.gets == 2          # 3 items left + the end canary; resume + end reads
    assert [r["item_id"] for r in labels(out)] == ["p0", "p1", "p2", "p3", "p4"]
    assert [b["status"] for b in ledger(out)] == ["done"] and not (out / "checkpoint-mg-C-b1.json").exists()
    assert [c["at"] for c in r2["config_reads"]] == ["start", "resume1", "end"]
    assert len(mg._rows(mg.paths(out, "C")["canary"])) == 20


def test_outage_cap_per_item_is_a_resumable_pause(tmp_path: Path) -> None:
    # mutation: turning the per-item outage cap into a void (or a stop marker) fails this
    cfg = study(tmp_path)
    out = Path(cfg["out_dir"])
    t = FakeTransport(LOCAL["model"], PIN_C, script=[None] * 10 + [(503, None, None)] * 4, answer=owner_answer)
    r = mg.run_batch(cfg, gw(tmp_path, t), "C", "b1", "alias-judge/3", items(2), CASES, sleep=Sleeper())
    assert r["status"].startswith("paused:outage:outages_per_item") and not r["void"]
    assert ledger(out) == [] and not mg.paths(out, "C")["stopped"].exists()
    t2 = FakeTransport(LOCAL["model"], PIN_C, answer=owner_answer)
    t2.seen.update({c["question"]: 1 for c in CASES[:10]})
    r2 = mg.run_batch(cfg, gw(tmp_path, t2), "C", "b1", "alias-judge/3", items(2), CASES, sleep=Sleeper(),
                      resume=True)
    assert r2["status"] == "done" and r2["stored"] == 2 and r2["pauses"] == 4


def test_hosted_sustained_outage_pauses(tmp_path: Path) -> None:
    # mutation: treating a hosted outage that outlasts the re-reads as a mismatch fails this
    hm = HOSTED["model"]
    t = FakeTransport(hm, HOSTED_OK, script=[None] * 10 + [(503, None, None)], answer=owner_answer,
                      configs=[{"fingerprint": HOSTED_OK}] + [None] * 5)
    cfg = study(tmp_path, H=HOSTED)
    s = Sleeper()
    r = mg.run_batch(cfg, gw(tmp_path, t), "H", "b1", "alias-judge/3", items(2), CASES, sleep=s)
    assert r["status"].startswith("paused:outage:outage_sustained") and s.calls == [60.0] * 5
    assert not mg.paths(Path(cfg["out_dir"]), "H")["stopped"].exists()


def test_pause_in_start_canary_resumes_only_missing_cases(tmp_path: Path) -> None:
    # mutation: re-sending the canary cases already answered (a second start snapshot) fails this
    cfg = study(tmp_path)
    out = Path(cfg["out_dir"])
    t = FakeTransport(LOCAL["model"], PIN_C, script=[None] * 4 + [(400, None, None)], answer=owner_answer)
    r = mg.run_batch(cfg, gw(tmp_path, t), "C", "b1", "alias-judge/3", items(1), CASES, sleep=Sleeper())
    assert r["status"] == "paused:error:http_400" and r["stopped_at"] == "start"
    t2 = FakeTransport(LOCAL["model"], PIN_C, answer=owner_answer)
    r2 = mg.run_batch(cfg, gw(tmp_path, t2), "C", "b1", "alias-judge/3", items(1), CASES, sleep=Sleeper(),
                      resume=True)
    asked = [re.search(r"QUESTION: (.*)\n", b["messages"][1]["content"]).group(1) for b in t2.posts]
    assert asked[:6] == [f"Canary question {i}?" for i in range(4, 10)]
    assert r2["status"] == "done" and r2["canary"]["differ"] == 0
    assert [x["kind"] for x in mg._rows(mg.paths(out, "C")["canary"])].count("canary_start") == 10


def test_resume_guards(tmp_path: Path) -> None:
    # mutation: resuming with other items, or "resuming" a batch that never paused, fails this
    cfg = study(tmp_path)
    with pytest.raises(SystemExit, match="no paused checkpoint"):
        mg.run_batch(cfg, gw(tmp_path, FakeTransport(LOCAL["model"], PIN_C)), "C", "b1", "alias-judge/3",
                     items(1), CASES, sleep=Sleeper(), resume=True)
    t = FakeTransport(LOCAL["model"], PIN_C, script=[None] * 10 + [(400, None, None)], answer=owner_answer)
    mg.run_batch(cfg, gw(tmp_path, t), "C", "b1", "alias-judge/3", items(2), CASES, sleep=Sleeper())
    with pytest.raises(SystemExit, match="items_sha256"):
        mg.run_batch(cfg, gw(tmp_path, FakeTransport(LOCAL["model"], PIN_C)), "C", "b1", "alias-judge/3",
                     items(3), CASES, sleep=Sleeper(), resume=True)


def test_mismatch_on_resume_read_voids(tmp_path: Path) -> None:
    # mutation: resuming a paused batch without re-reading the configuration fails this
    cfg = study(tmp_path)
    out = Path(cfg["out_dir"])
    t = FakeTransport(LOCAL["model"], PIN_C, script=[None] * 11 + [(400, None, None)], answer=owner_answer)
    mg.run_batch(cfg, gw(tmp_path, t), "C", "b1", "alias-judge/3", items(3), CASES, sleep=Sleeper())
    t2 = FakeTransport(LOCAL["model"], PIN_C, configs=[{"fingerprint": PIN_OTHER}])
    r = mg.run_batch(cfg, gw(tmp_path, t2), "C", "b1", "alias-judge/3", items(3), CASES, sleep=Sleeper(),
                     resume=True)
    assert r["status"].startswith("stopped:mismatch:fingerprint_different") and r["void"] and t2.posts == []
    assert mg.valid_rows(out, "C") == [] and [b["batch"] for b in ledger(out)] == ["b1"]


def test_max_in_flight(tmp_path: Path) -> None:
    # mutation: ignoring max_in_flight (or letting it exceed 4) fails this
    with pytest.raises(SystemExit, match="max_in_flight"):
        mg.validate_judge("C", {**LOCAL, "max_in_flight": 5})
    cfg = study(tmp_path, C={**LOCAL, "max_in_flight": 3})
    t = FakeTransport(LOCAL["model"], PIN_C, answer=owner_answer, delay=0.02)
    r = mg.run_batch(cfg, gw(tmp_path, t), "C", "b1", "alias-judge/3", items(7), CASES, sleep=Sleeper())
    assert r["status"] == "done" and t.peak == 3
    assert sorted(x["item_id"] for x in labels(Path(cfg["out_dir"]))) == [f"p{i}" for i in range(7)]
    t1 = FakeTransport(LOCAL["model"], PIN_C, answer=owner_answer, delay=0.01)
    mg.run_batch(study(tmp_path / "one"), gw(tmp_path, t1), "C", "b1", "alias-judge/3", items(4), CASES,
                 sleep=Sleeper())
    assert t1.peak == 1


def test_cap_ends_the_labels_phase(tmp_path: Path) -> None:
    # mutation: pausing (or voiding / stopping the judge) at the cap, skipping the end canary, or counting
    # an outage against the cap fails this
    cfg = mg.validate_study({"out_dir": str(tmp_path / "out"), "judges": {"C": LOCAL_N1}, "cap_factor": 1.0,
                             "canary_cases": [f"c{i}" for i in range(10)]})
    out = Path(cfg["out_dir"])
    seq = iter(["unsure", "full", "full", "full"])  # item 0 is re-issued once: item 2 finds no slot left
    t = FakeTransport(LOCAL["model"], PIN_C, script=[None] * 10 + [(503, None, None)],
                      answer=lambda q, k: owner_answer(q, k) if q.startswith("Canary") else next(seq))
    r = mg.run_batch(cfg, gw(tmp_path, t), "C", "b1", "alias-judge/3", items(3), CASES, sleep=Sleeper())
    assert r["status"] == "ended:cap" and r["stored"] == 2 and r["unlabelled"] == 1 and not r["void"]
    assert r["canary"]["differ"] == 0 and [c["at"] for c in r["config_reads"]] == ["start", "end"]
    assert sum("Item question" in b["messages"][1]["content"] for b in t.posts) == 4  # incl. the 503
    assert not mg.paths(out, "C")["stopped"].exists() and len(mg.valid_rows(out, "C")) == 2
    with pytest.raises(SystemExit, match="already closed"):
        mg.run_batch(cfg, gw(tmp_path, FakeTransport(LOCAL["model"], PIN_C)), "C", "b1", "alias-judge/3",
                     items(3), CASES, sleep=Sleeper(), resume=True)


def test_cap_reservation_has_no_overshoot_in_flight(tmp_path: Path) -> None:
    # mutation: checking the cap without reserving the slot under the lock lets parallel calls overshoot
    cfg = mg.validate_study({"out_dir": str(tmp_path / "out"), "judges": {"C": {**LOCAL_N1, "max_in_flight": 4}},
                             "cap_factor": 1.0, "canary_cases": [f"c{i}" for i in range(10)]})
    t = FakeTransport(LOCAL["model"], PIN_C, delay=0.01,
                      answer=lambda q, k: owner_answer(q, k) if q.startswith("Canary") else
                      ("unsure" if k == 1 else "full"))  # every item needs 2 answered calls
    r = mg.run_batch(cfg, gw(tmp_path, t), "C", "b1", "alias-judge/3", items(8), CASES, sleep=Sleeper())
    item_posts = sum("Item question" in b["messages"][1]["content"] for b in t.posts)
    assert r["status"] == "ended:cap" and item_posts == 8 and r["answered"] == 8 + 20



# ------------------------------------------------------------------ review round 2

def test_unreadable_reads_are_an_outage_not_a_mismatch(tmp_path: Path) -> None:
    # mutation: returning mismatch after 5 unreadable re-reads (a serving outage would void C's batch
    # and stop the judge for good) fails this
    cfg = study(tmp_path)
    out = Path(cfg["out_dir"])
    t = FakeTransport(LOCAL["model"], PIN_C, script=[None] * 10 + [(503, None, None)], answer=owner_answer,
                      configs=[{"fingerprint": PIN_C}] + [None] * 5)
    r = mg.run_batch(cfg, gw(tmp_path, t), "C", "b1", "alias-judge/3", items(2), CASES, sleep=Sleeper())
    assert r["status"].startswith("paused:outage:outage_sustained:unread") and not r["void"]
    assert not mg.paths(out, "C")["stopped"].exists() and ledger(out) == []


def test_mixed_unreadable_and_partial_reads_are_an_outage(tmp_path: Path) -> None:
    # mutation: counting a mix of unreadable and partial reads as "all partial" fails this
    cfg = study(tmp_path)
    t = FakeTransport(LOCAL["model"], PIN_C, script=[None] * 10 + [(200, PART_LOCAL, LOCAL["model"])],
                      answer=owner_answer, configs=[{"fingerprint": PIN_C}, None, {"fingerprint": PART_LOCAL},
                                                    None, {"fingerprint": PART_LOCAL}, None])
    r = mg.run_batch(cfg, gw(tmp_path, t), "C", "b1", "alias-judge/3", items(2), CASES, sleep=Sleeper())
    assert r["status"] == "paused:outage:outage_sustained:fingerprint_partial,unread"


def test_end_read_outage_pauses_and_resume_rereads(tmp_path: Path) -> None:
    # mutation: voiding on an unreadable end read, or re-sending the end canary on resume, fails this
    cfg = study(tmp_path)
    out = Path(cfg["out_dir"])
    t = FakeTransport(LOCAL["model"], PIN_C, answer=owner_answer, configs=[{"fingerprint": PIN_C}] + [None] * 6)
    r = mg.run_batch(cfg, gw(tmp_path, t), "C", "b1", "alias-judge/3", items(2), CASES, sleep=Sleeper())
    assert r["status"].startswith("paused:outage") and r["stopped_at"] == "config_end" and not r["void"]
    t2 = FakeTransport(LOCAL["model"], PIN_C)
    r2 = mg.run_batch(cfg, gw(tmp_path, t2), "C", "b1", "alias-judge/3", items(2), CASES, sleep=Sleeper(),
                      resume=True)
    assert r2["status"] == "done" and t2.posts == [] and r2["canary"]["differ"] == 0
    assert len(mg.valid_rows(out, "C")) == 2


@pytest.mark.parametrize("change", ["seed", "top_k", "pin", "label", "text"])
def test_resume_refuses_a_changed_configuration(tmp_path: Path, change: str) -> None:
    # mutation: a resume guard that compares only the prompt, items and canary names fails this
    cfg = study(tmp_path)
    t = FakeTransport(LOCAL["model"], PIN_C, script=[None] * 11 + [(400, None, None)], answer=owner_answer)
    mg.run_batch(cfg, gw(tmp_path, t), "C", "b1", "alias-judge/3", items(3), CASES, sleep=Sleeper())
    j, cases = dict(LOCAL), [dict(c) for c in CASES]
    if change == "seed":
        j["canary"] = {**LOCAL["canary"], "seed": 8}
    elif change == "top_k":
        j["sampling"] = {**LOCAL["sampling"], "top_k": 40}
    elif change == "pin":
        j["pinned_fingerprint"] = PIN_OTHER
    elif change == "label":
        cases[0]["expected_label"] = "none" if cases[0]["expected_label"] != "none" else "full"
    else:
        cases[1]["passage"] += " (edited)"
    with pytest.raises(SystemExit, match="different (judge|canary)_sha256"):
        mg.run_batch(study(tmp_path, C=j), gw(tmp_path, FakeTransport(LOCAL["model"], PIN_C)), "C", "b1",
                     "alias-judge/3", items(3), cases, sleep=Sleeper(), resume=True)


def test_later_batch_refuses_a_changed_configuration(tmp_path: Path) -> None:
    # mutation: not pinning the judge's configuration across batches (canary chaining) fails this
    cfg = study(tmp_path)
    mg.run_batch(cfg, gw(tmp_path, FakeTransport(LOCAL["model"], PIN_C, answer=owner_answer)), "C", "b1",
                 "alias-judge/3", items(1), CASES, sleep=Sleeper())
    j = {**LOCAL, "sampling": {**LOCAL["sampling"], "temperature": 0.6}}
    with pytest.raises(SystemExit, match="judge_sha256 differs"):
        mg.run_batch(study(tmp_path, C=j), gw(tmp_path, FakeTransport(LOCAL["model"], PIN_C)), "C", "b2",
                     "alias-judge/3", items(1), CASES, sleep=Sleeper())


def test_resume_refuses_same_length_different_items(tmp_path: Path) -> None:
    # mutation (P1): checking only the number of items on resume fails this
    cfg = study(tmp_path)
    t = FakeTransport(LOCAL["model"], PIN_C, script=[None] * 11 + [(400, None, None)], answer=owner_answer)
    mg.run_batch(cfg, gw(tmp_path, t), "C", "b1", "alias-judge/3", items(3), CASES, sleep=Sleeper())
    other = items(3)
    other[2] = {**other[2], "passage": "A different passage."}
    with pytest.raises(SystemExit, match="items_sha256"):
        mg.run_batch(cfg, gw(tmp_path, FakeTransport(LOCAL["model"], PIN_C)), "C", "b1", "alias-judge/3",
                     other, CASES, sleep=Sleeper(), resume=True)


def test_answered_count_restored_across_resume(tmp_path: Path) -> None:
    # mutation (P3): not restoring the answered count from the checkpoint lets a resumed batch overrun
    cfg = mg.validate_study({"out_dir": str(tmp_path / "out"), "judges": {"C": LOCAL_N1}, "cap_factor": 1.0,
                             "canary_cases": [f"c{i}" for i in range(10)]})
    seq = iter(["unsure", "full"])
    t = FakeTransport(LOCAL["model"], PIN_C, script=[None] * 12 + [(400, None, None)],
                      answer=lambda q, k: owner_answer(q, k) if q.startswith("Canary") else next(seq))
    r = mg.run_batch(cfg, gw(tmp_path, t), "C", "b1", "alias-judge/3", items(3), CASES, sleep=Sleeper())
    assert r["status"] == "paused:error:http_400" and r["answered"] == 12
    t2 = FakeTransport(LOCAL["model"], PIN_C, answer=owner_answer)
    t2.seen.update({c["question"]: 1 for c in CASES[:10]})
    r2 = mg.run_batch(cfg, gw(tmp_path, t2), "C", "b1", "alias-judge/3", items(3), CASES, sleep=Sleeper(),
                      resume=True)
    # cap 23, labels phase 13: 12 used, so one of the two remaining items is sent
    assert r2["status"] == "ended:cap" and r2["unlabelled"] == 1 and r2["answered"] == 23


class ShortN(FakeTransport):
    """Serves 3 samples whenever 5 are requested (the n = 1 canaries are served in full)."""

    def post(self, url: str, headers: dict | None = None, json: dict | None = None) -> Resp:  # noqa: A002
        self.n_served = 3 if (json or {}).get("n") == 5 else None
        return super().post(url, headers=headers, json=json)


def test_served_sample_count_is_checked(tmp_path: Path) -> None:
    # mutation: storing a row with fewer samples than sent (C's vote would change quietly) fails this
    ok = study(tmp_path / "ok")
    mg.run_batch(ok, gw(tmp_path, FakeTransport(LOCAL["model"], PIN_C, answer=owner_answer)), "C", "b1",
                 "alias-judge/3", items(1), CASES, sleep=Sleeper())
    assert labels(Path(ok["out_dir"]))[0]["n_served"] == 5
    cfg = study(tmp_path)
    r = mg.run_batch(cfg, gw(tmp_path, ShortN(LOCAL["model"], PIN_C, answer=owner_answer)), "C", "b1",
                     "alias-judge/3", items(1), CASES, sleep=Sleeper())
    assert r["status"] == "paused:error:n_served:3/5" and r["stored"] == 0 and not r["void"]


def test_valid_rows_exclude_open_and_paused_batches(tmp_path: Path) -> None:
    # mutation: counting rows of a batch with no closed ledger row fails this
    cfg = study(tmp_path)
    out = Path(cfg["out_dir"])
    mg.run_batch(cfg, gw(tmp_path, FakeTransport(LOCAL["model"], PIN_C, answer=owner_answer)), "C", "b1",
                 "alias-judge/3", items(2), CASES, sleep=Sleeper())
    t = FakeTransport(LOCAL["model"], PIN_C, script=[None] * 11 + [(400, None, None)], answer=owner_answer)
    mg.run_batch(cfg, gw(tmp_path, t), "C", "b2", "alias-judge/3", items(3), CASES, sleep=Sleeper())
    rep = mg.valid_rows_report(out, "C")
    assert [r["batch"] for r in rep["rows"]] == ["b1", "b1"] and rep["excluded_open"] == {"b2": 1}


def test_canary_rows_rebuilt_from_the_file_after_a_crash(tmp_path: Path) -> None:
    # mutation: taking the answered canary cases from the checkpoint only (a crash between the row write
    # and the checkpoint save then re-asks the case and duplicates the row) fails this
    cfg = study(tmp_path)
    out = Path(cfg["out_dir"])
    t = FakeTransport(LOCAL["model"], PIN_C, script=[None] * 4 + [(400, None, None)], answer=owner_answer)
    mg.run_batch(cfg, gw(tmp_path, t), "C", "b1", "alias-judge/3", items(1), CASES, sleep=Sleeper())
    rows = mg._rows(mg.paths(out, "C")["canary"])
    mg._append(mg.paths(out, "C")["canary"], {**rows[-1], "item_id": "c4", "grade": "partial"})  # written, unsaved
    t2 = FakeTransport(LOCAL["model"], PIN_C, answer=owner_answer)
    mg.run_batch(cfg, gw(tmp_path, t2), "C", "b1", "alias-judge/3", items(1), CASES, sleep=Sleeper(), resume=True)
    asked = [re.search(r"QUESTION: (.*)\n", b["messages"][1]["content"]).group(1) for b in t2.posts]
    assert asked[0] == "Canary question 5?"
    starts = [r["item_id"] for r in mg._rows(mg.paths(out, "C")["canary"]) if r["kind"] == "canary_start"]
    assert sorted(starts) == sorted(f"c{i}" for i in range(10))


def test_judge_lock_file_and_halt(tmp_path: Path) -> None:
    # mutation: no O_EXCL lock (two processes on one judge), or sending after a mismatch, fails this
    cfg = study(tmp_path)
    out = Path(cfg["out_dir"])
    out.mkdir(parents=True)
    mg.paths(out, "C")["lock"].write_text("12345\n")
    with pytest.raises(SystemExit, match="another process"):
        mg.run_batch(cfg, gw(tmp_path, FakeTransport(LOCAL["model"], PIN_C)), "C", "b1", "alias-judge/3",
                     items(1), CASES, sleep=Sleeper())
    mg.paths(out, "C")["lock"].unlink()
    mg.run_batch(cfg, gw(tmp_path, FakeTransport(LOCAL["model"], PIN_C, answer=owner_answer)), "C", "b1",
                 "alias-judge/3", items(1), CASES, sleep=Sleeper())
    assert not mg.paths(out, "C")["lock"].exists()
    t = FakeTransport(LOCAL["model"], PIN_C)
    ctx = mg.Ctx(gw=gw(tmp_path, t), priv=priv(tmp_path), marker=mg.PARTIAL_MARKER, sleep=Sleeper(), log=lambda e: None)
    ctx.halt.set()
    with pytest.raises(mg.Stop, match="halted"):
        mg.gated_call(ctx, mg.validate_judge("C", LOCAL), "alias-judge/3", items(1)[0], canary=False)
    assert t.posts == []
    # a mismatch sets the halt for the wave's other workers
    t3 = FakeTransport(LOCAL["model"], PIN_OTHER)
    ctx3 = mg.Ctx(gw=gw(tmp_path, t3), priv=priv(tmp_path), marker=mg.PARTIAL_MARKER, sleep=Sleeper(),
                  log=lambda e: None)
    with pytest.raises(mg.Stop, match="mismatch"):
        mg.gated_call(ctx3, mg.validate_judge("C", LOCAL), "alias-judge/3", items(1)[0], canary=False)
    assert ctx3.halt.is_set()


def test_start_canary_rows_carry_the_new_epoch(tmp_path: Path) -> None:
    # mutation: tagging the start-canary rows before the epoch boundary is computed fails this
    cfg = study(tmp_path)
    out = Path(cfg["out_dir"])
    mg.run_batch(cfg, gw(tmp_path, FakeTransport(LOCAL["model"], PIN_C, answer=owner_answer)), "C", "b1",
                 "alias-judge/3", items(1), CASES, sleep=Sleeper())
    shifted = _flip(10)
    r = mg.run_batch(cfg, gw(tmp_path, FakeTransport(LOCAL["model"], PIN_C, answer=lambda q, k: shifted(q, 2))),
                     "C", "b2", "alias-judge/3", items(1), CASES, sleep=Sleeper())
    assert r["epoch"] == 1
    b2 = [x for x in mg._rows(mg.paths(out, "C")["canary"]) if x["batch"] == "b2"]
    assert {x["epoch"] for x in b2} == {1}


def test_observed_block_recorded_and_restart_reported(tmp_path: Path) -> None:
    # mutation: not recording the observed block, or voiding a same-fingerprint restart, fails this
    cfg = study(tmp_path)
    t = FakeTransport(LOCAL["model"], PIN_C, answer=owner_answer,
                      configs=[{"fingerprint": PIN_C, "observed": {"started": "t0"}},
                               {"fingerprint": PIN_C, "observed": {"started": "t1"}}])
    r = mg.run_batch(cfg, gw(tmp_path, t), "C", "b1", "alias-judge/3", items(1), CASES, sleep=Sleeper())
    assert r["status"] == "done" and r["observed_changed"] is True
    assert [c["observed"] for c in r["config_reads"]] == [[{"started": "t0"}], [{"started": "t1"}]]


def test_contradiction_without_fingerprint_is_a_mismatch() -> None:
    # mutation: skipping a backend that has a contradiction list but no fingerprint fails this
    loc = mg.validate_judge("C", LOCAL)
    e = mg.parse_config(config_doc(LOCAL["model"], {"backends": [
        {"config": PIN_C, "partial": False}, {"config": None, "mismatch": ["revision"]}]}))
    assert mg.check_config_read(loc, e) == ("mismatch", "contradictions:revision")


def test_selection_needs_every_requested_judge(tmp_path: Path) -> None:
    # mutation (P10): selecting canaries over the judges that happened to finish fails this
    t = FakeTransport(LOCAL["model"], PIN_C, answer=owner_answer)
    other = {**LOCAL, "model": "fake-other-model"}  # its replies come back as LOCAL's model: a mismatch
    cfg = study(tmp_path, C=LOCAL, X=other)
    res = mg.run_selfcheck(cfg, gw(tmp_path, t), CASES, runs=1, sleep=Sleeper())
    assert res["judges"]["C"]["status"] == "done" and res["judges"]["X"]["status"].startswith("stopped:mismatch")
    assert res["selection"] is None and "X" in res["selection_reason"]



# ------------------------------------------------------------------ re-check N1, N2, N4

def test_end_read_mismatch_with_canary_void_voids_the_whole_batch(tmp_path: Path) -> None:
    # mutation: letting the end-read branch void only since the last passing check when the canary
    # also voids (N1) fails this
    cfg = study(tmp_path)
    out = Path(cfg["out_dir"])
    script = [None] * 12 + [(503, None, None)]  # a pause after two rows: last_check_seq = 2 > seq0 = 0
    t = FakeTransport(LOCAL["model"], PIN_C, script=script, answer=_flip(3),
                      configs=[{"fingerprint": PIN_C}, {"fingerprint": PIN_C}, {"fingerprint": PIN_OTHER}])
    r = mg.run_batch(cfg, gw(tmp_path, t), "C", "b1", "alias-judge/3", items(3), CASES, sleep=Sleeper())
    assert r["status"].startswith("void:config_end") and r["canary"]["void"] and r["void_from_seq"] == 0
    assert mg.valid_rows(out, "C") == []
    # without the canary void, only the rows since the last passing check are voided
    cfg2 = study(tmp_path / "b")
    t2 = FakeTransport(LOCAL["model"], PIN_C, script=list(script), answer=owner_answer,
                       configs=[{"fingerprint": PIN_C}, {"fingerprint": PIN_C}, {"fingerprint": PIN_OTHER}])
    r2 = mg.run_batch(cfg2, gw(tmp_path, t2), "C", "b1", "alias-judge/3", items(3), CASES, sleep=Sleeper())
    assert not r2["canary"]["void"] and r2["void_from_seq"] == 2
    assert r2["status"].startswith("void:config_end") and len(mg.valid_rows(Path(cfg2["out_dir"]), "C")) == 2


def test_halt_rechecked_after_waiting_for_the_pause_lock(tmp_path: Path) -> None:
    # mutation: re-reading after a mismatch was seen while waiting for the pause lock (N2) fails this
    t = FakeTransport(LOCAL["model"], PIN_C, script=[(503, None, None)])
    s = Sleeper()
    ctx = mg.Ctx(gw=gw(tmp_path, t), priv=priv(tmp_path), marker=mg.PARTIAL_MARKER, sleep=s, log=lambda e: None)

    class SetHaltOnAcquire:
        def __enter__(self) -> None:
            ctx.halt.set()  # another worker saw a mismatch while this one waited

        def __exit__(self, *a: Any) -> None:
            pass

    ctx.pause_lock = SetHaltOnAcquire()
    with pytest.raises(mg.Stop, match="halted"):
        mg.gated_call(ctx, mg.validate_judge("C", LOCAL), "alias-judge/3", items(1)[0], canary=False)
    assert s.calls == [] and t.gets == 0


def test_operational_keys_do_not_pin_but_prompts_do(tmp_path: Path) -> None:
    # mutation: hashing max_in_flight / optional / h_reps into judge_sha256, or leaving prompts out (N4)
    cfg = study(tmp_path)
    mg.run_batch(cfg, gw(tmp_path, FakeTransport(LOCAL["model"], PIN_C, answer=owner_answer)), "C", "b1",
                 "alias-judge/3", items(1), CASES, sleep=Sleeper())
    ops = {**LOCAL, "max_in_flight": 2, "optional": True, "h_reps": 2}
    r = mg.run_batch(study(tmp_path, C=ops), gw(tmp_path, FakeTransport(LOCAL["model"], PIN_C, answer=owner_answer)),
                     "C", "b2", "alias-judge/3", items(1), CASES, sleep=Sleeper())
    assert r["status"] == "done"
    with pytest.raises(SystemExit, match="judge_sha256 differs"):
        mg.run_batch(study(tmp_path, C={**ops, "prompts": ["alias-judge/3"]}),
                     gw(tmp_path, FakeTransport(LOCAL["model"], PIN_C)), "C", "b3", "alias-judge/3", items(1),
                     CASES, sleep=Sleeper())
