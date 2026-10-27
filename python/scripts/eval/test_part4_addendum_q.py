"""Addendum Q to part 4, against an in-process mock gateway only.

The gateway is a mock bound to 127.0.0.1 on an ephemeral port; questions,
quotes, the prompt, the product name and the part-4 results are synthetic.
No real model, gateway, embedding or store endpoint is called.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import sys
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import deployed_docs_part4 as p4  # noqa: E402
import part4_addendum_q as aq  # noqa: E402

# ------------------------------------------------------------------ mock gateway

OPUS, SOL = "synth-classifier-a", "synth-classifier-b"
PRODUCT = "Widgetry"
ADDQ_USER = (f"A user asked about {PRODUCT}.\n\nQuestion: {{question}}\n\nA {PRODUCT} sentence:\n<quote>\n{{quote}}\n"
             "</quote>\n\nanswer / setup / neither? One word.")
_PARSE = re.compile(r"Question: (.*?)\n\n.*<quote>\n(.*?)\n</quote>", re.S)


def h(s: str) -> int:
    return int(hashlib.sha256(s.encode()).hexdigest()[:8], 16)


class World:
    """Knobs, request log and counters. Not idempotent: a prompt answered before is
    answered with a different label, so a re-issued call changes the results and the
    byte-identical resume comparison is load-bearing."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset(None)

    def reset(self, out: Path | None) -> None:
        self.out = out
        self.calls: Counter[tuple[str, str]] = Counter()
        self.bodies: list[dict[str, Any]] = []
        self.n = 0
        self.active = 0
        self.max_active = 0
        self.pause_at: int | None = None
        self.rate_limit_from: int | None = None
        self.fail_re: re.Pattern[str] | None = None   # classifier-A quotes matching this get an HTTP 500
        self.ports: list[int] = []


W = World()


def chat(content: str, ct: int, finish: str = "stop", model: str = "m") -> dict[str, Any]:
    return {"model": model, "choices": [{"message": {"content": content}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 50, "completion_tokens": ct}}


def answer_text(lab: str, style: int) -> str:
    return {0: lab, 1: f"**{lab.capitalize()}**", 2: f"Label: {lab}", 3: f"I would say {lab}.",
            4: lab.upper(), 5: f"Answer: {lab}"}[style]


def _gateway(body: dict[str, Any]) -> tuple[int, Any]:
    with W.lock:
        W.n += 1
        n = W.n
        W.bodies.append(body)
        if W.pause_at is not None and n == W.pause_at and W.out is not None:
            (W.out / "PAUSE").write_text("operator stop\n")
        if W.rate_limit_from is not None and n >= W.rate_limit_from:
            return 429, {"error": "rate limited"}
    model = body["model"]
    if model == SOL and "temperature" in body:
        return 400, {"error": {"message": "temperature is not supported with this model"}}
    user = body["messages"][-1]["content"]
    m = _PARSE.search(user)
    assert m, user
    q, quote = m.group(1), m.group(2)
    if model == OPUS and W.fail_re is not None and W.fail_re.search(quote):
        return 500, {"error": "boom"}
    if model == OPUS and "never-answered" in quote:
        return 200, chat("", ct=0, model=model)                     # a 0-token empty, every time
    if model == SOL and "long-think" in quote:
        return 200, chat("", ct=4096, finish="length", model=model)  # hidden reasoning ate max_tokens
    with W.lock:
        W.calls[(model, user)] += 1
        k = W.calls[(model, user)]
    salt = "" if k == 1 else f"#{k}"
    if "hint-answer" in quote:
        lab = "answer"
    elif "hint-setup" in quote:
        lab = "setup" if model == OPUS or h(q + salt) % 3 else "answer"
    else:
        lab = aq.LABELS[h(model + q + quote + salt) % 3]
    if "garbled" in quote:
        return 200, chat(f"not {lab}", ct=3, model=model)          # negated -> unparseable
    return 200, chat(answer_text(lab, h(quote + model + salt) % 6), ct=3, model=model)


class Mock:
    def __init__(self) -> None:
        class H(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n))
                with W.lock:
                    W.active += 1
                    W.max_active = max(W.max_active, W.active)
                try:
                    time.sleep(0.003)  # lets requests overlap
                    status, obj = _gateway(body)
                finally:
                    with W.lock:
                        W.active -= 1
                data = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a: Any) -> None:
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


GATEWAY = Mock()

# ------------------------------------------------------------------ synthetic inputs

ARMS = ["L", "SEM512", "T128o0", "T512o0", "T1024o0"]
READERS = ["r1", "r2"]
N_M = 12


def synth_questions() -> list[dict[str, Any]]:
    rows = []
    for i in range(N_M):
        a = f"Quote A of item {i} says the widget size is {i}."
        b = f"Quote B of item {i} describes step {i}."
        if i % 4 == 1:
            b += " hint-setup"
        if i % 4 == 0:
            a += " hint-answer"
            b += " hint-answer"
        if i == 5:
            a += " never-answered"
        if i == 6:
            b += " long-think"
        if i == 7:
            a += " garbled"
        rows.append({"record_id": f"M{i:03d}k0", "set": "M", "status": "valid", "question": f"How big is widget {i}?",
                     "quote_a": a, "quote_b": b, "gold": [a, b], "doc_id": f"d{i // 2}",
                     "stratum": "alpha" if i % 3 else "beta", "gen": "gen"})
    rows.append({"record_id": "M900k0", "set": "M", "status": "duplicate", "question": "dup?", "gold": ["x", "y"],
                 "doc_id": "d0", "stratum": "alpha"})
    rows.append({"record_id": "S000p0", "set": "S", "status": "valid", "question": "single?", "gold": ["only one"],
                 "doc_id": "d0", "stratum": "alpha"})
    return rows


def part4_design(n_perm: int = 999, n_boot: int = 200) -> dict[str, Any]:
    an = {**p4.DEFAULTS["analysis"], "n_perm": n_perm, "n_boot": n_boot}
    return {"analysis": an, "readers": list(READERS), "seed": 261025, "grader_primary": {"tag": "sol"}}


def part4_results(per_item: dict[str, Any], questions: dict[str, Any]) -> dict[str, Any]:
    """A part-4 results file (the keys addendum Q reads), its analysis by part 4's own code."""
    d = part4_design()
    analysis = p4.analyse_part4(per_item, questions, conditions=["B4096"], readers=READERS, primary="sol",
                                secondary="opus48", analysis=d["analysis"], seed=d["seed"], fallback_tag="fb",
                                arms=ARMS)
    return {"design": d, "questions": questions, "per_item": per_item, "analysis": analysis}


def items_from(outcomes: dict[str, dict[str, list[int]]], qids: list[str], pages: list[str],
               extra: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    per_item: dict[str, Any] = {}
    questions: dict[str, Any] = {}
    for i, q in enumerate(qids):
        questions[q] = {"set": "M", "stratum": "alpha", "doc_id": pages[i], "gen": "gen", "page_tokens": 3000}
        per_item[q] = {arm: {"B4096": {"found": True, "readers": {
            rd: {"sol": "correct" if outcomes[rd][arm][i] else "incorrect", "opus48": None} for rd in READERS}}}
            for arm in ARMS}
    for q, v in (extra or {}).items():
        questions[q] = v
        per_item[q] = {arm: {"B4096": {"found": False, "readers": {rd: {"sol": "incorrect", "opus48": None}
                                                                   for rd in READERS}}} for arm in ARMS}
    return per_item, questions


def synth_part4() -> dict[str, Any]:
    qs = [r for r in synth_questions() if r["status"] == "valid" and r["set"] == "M"]
    qids = [r["record_id"] for r in qs]
    out = {rd: {arm: [int(h(f"{rd}{arm}{q}") % 3 != 0) for q in qids] for arm in ARMS} for rd in READERS}
    per_item, questions = items_from(out, qids, [r["doc_id"] for r in qs],
                                     extra={"S000p0": {"set": "S", "stratum": "alpha", "doc_id": "d0", "gen": "gen",
                                                       "page_tokens": 3000}})
    return part4_results(per_item, questions)


def base_config(**addq: Any) -> dict[str, Any]:
    block = {"primary": {"model": OPUS, "tag": "opus55"}, "secondary": {"model": SOL, "tag": "sol"},
             "limits": {"empty_backoff_s": 0.001, "rate_backoffs_s": [0.001, 0.001]},
             "kappa": {"n_boot": 200}, "human": {"n": 6},
             "files": {"prereg": "PREREG-addq-synthetic.md"}, "expected_questions": N_M}
    for k, v in addq.items():
        block[k] = {**block[k], **v} if isinstance(v, dict) and isinstance(block.get(k), dict) else v
    return {
        "es_url": "http://127.0.0.1:1", "index": "synthetic-index", "gen_url": "http://127.0.0.1:1",
        "judge_url": "http://127.0.0.1:1", "gateway_url": GATEWAY.url, "deny_ports": [],
        "deny_reason": "shared endpoint", "out_dir": "/nonexistent", "stratum_prefixes": [["a-", "alpha"]],
        "strata": ["alpha", "beta"], "seed_stratum": "seed", "files": {},
        "seed_questions": {"P": {}, "N": {}, "W": {}},
        "prompts": {"GEN_SYSTEM": "g", "GEN_USER": "except names of tools, commands, file formats or organisms.\n{passage}",
                    "JUDGE_USER": "j", "M_USER": "m {a} {b}", "JUDGE3_USER": "j", "READER_SYSTEM": "r",
                    "GRADER_USER": "g {question} {gold} {answer}", "ADDQ_USER": ADDQ_USER},
        "addendum_q": block,
    }


def make_config(path: Path, **addq: Any) -> Path:
    path.write_text(json.dumps(base_config(**addq), indent=1))
    return path


def prep_out(d: Path, part4: dict[str, Any] | None = None) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / "PREREG-addq-synthetic.md").write_text("synthetic addendum prereg v1\n")
    (d / "part4-questions.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in synth_questions()))
    (d / "results-part4.json").write_text(json.dumps(part4 or SYNTH_PART4, sort_keys=True))
    return d


SYNTH_PART4 = synth_part4()
COMMIT = {"c": "c0ffee"}


def prov() -> dict[str, Any]:
    return {"commit": COMMIT["c"], "dirty": False, "untracked": 0}


def run(cfg: Path, out: Path, resume: bool = False, **batch: Any) -> int:
    W.out = out
    return aq.AddQ(cfg, out, prov, batch or None).execute(resume)


def comparable(out: Path) -> str:
    r = json.loads((out / "results-part4-addendum-q.json").read_text())
    r.pop("provenance")
    r.pop("run_segments")
    return json.dumps(r, sort_keys=True)


def store_rows(out: Path) -> dict[str, dict[str, Any]]:
    rows = {}
    for f in sorted(out.glob("addq-labels-*.jsonl")):
        for ln in f.read_text().splitlines():
            r = json.loads(ln)
            rows[r["k"]] = r
    return rows


@pytest.fixture(scope="module")
def cfg(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_config(tmp_path_factory.mktemp("cfg") / "config.json")


@pytest.fixture(scope="module")
def reference(cfg: Path, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, Any]]:
    import httpx

    out = prep_out(tmp_path_factory.mktemp("ref"))
    W.reset(out)
    orig = httpx.AsyncClient.send

    async def spy(self: Any, request: Any, *a: Any, **k: Any) -> Any:
        W.ports.append(request.url.port)
        return await orig(self, request, *a, **k)

    httpx.AsyncClient.send = spy  # type: ignore[method-assign]
    try:
        assert run(cfg, out) == aq.EXIT_OK
    finally:
        httpx.AsyncClient.send = orig  # type: ignore[method-assign]
    return out, {"bodies": list(W.bodies), "max_active": W.max_active, "ports": list(W.ports), "n": W.n}


# ------------------------------------------------------------------ the parser


@pytest.mark.parametrize(("text", "label"), [
    ("answer", "answer"), ("Setup", "setup"), ("NEITHER", "neither"), ("**Setup**", "setup"),
    ("**Neither.**", "neither"), ("Label: neither", "neither"), ("Answer: setup", "setup"),
    ("Answer: answer", "answer"), ("I would say setup.", "setup"), ("setup: it only describes the step", "setup"),
    ("Neither.", "neither"), ("neither; neither", "neither"),
    ("not answer", "unparseable"), ("It isn't setup", "unparseable"), ("never neither", "unparseable"),
    ("only setup", "unparseable"), ("partly answer", "unparseable"), ("mostly setup", "unparseable"),
    ("neither an answer", "unparseable"), ("answer or setup", "unparseable"),
    ("**Answer** ... no, **Setup**", "unparseable"),
    ("**Answer:** setup", "setup"), ("**Answer:** neither", "neither"), ("**Answer:** answer", "answer"),
    ("**Label:** setup", "setup"),
    ("set-up", "unparseable"), ("answers", "unparseable"),
    ("correct", "unparseable"), ("", "unparseable"),
])
def test_label_parser(text: str, label: str) -> None:
    assert aq.parse_label(text)[0] == label


def test_label_parser_rules_and_window() -> None:
    assert aq.parse_label("**Setup**")[1] == "bold_or_grade"
    assert aq.parse_label("Label: setup")[1] == "bold_or_grade"
    assert aq.parse_label("setup")[1] == "one_word"
    assert aq.parse_label("is not setup")[1] == "negated"
    assert aq.parse_label("only setup")[1] == "qualified"
    assert aq.parse_label("setup, neither")[1] == "several"
    assert aq.parse_label("not one two three setup")[0] == "setup"       # 4 words before: outside
    assert aq.parse_label("not one two setup")[0] == "unparseable"        # 3 words before: inside
    # "neither" is both a label and a negator: it still negates another label
    assert aq.parse_label("neither one two answer")[1] in ("several", "negated")
    assert aq.parse_label("It is neither of these: neither")[0] == "neither"


def _original_parse_grade(text: str) -> tuple[str, str]:
    """A frozen copy of part 4's parse_grade as merged (#824), for the equivalence test."""
    words = {"correct": "correct", "partial": "partial", "incorrect": "incorrect", "abstained": "abstained",
             "abstain": "abstained", "abstains": "abstained", "abstention": "abstained"}
    lre = "|".join(sorted(words, key=len, reverse=True))
    bold = re.compile(rf"\*\*\s*({lre})\s*[.:!]?\s*\*\*", re.I)
    grade = re.compile(rf"\bgrade\s*[:=\-]\s*\**\s*({lre})\b", re.I)
    any_w = re.compile(rf"\b({lre})\b", re.I)
    tok = re.compile(r"[a-z']+")
    neg = frozenset({"not", "never", "no", "nor", "neither", "hardly", "cannot"})
    qual = frozenset({"partially", "partly", "mostly", "largely", "somewhat", "half", "nearly", "almost", "only"})
    marked = ({words[m.group(1).lower()] for m in bold.finditer(text)}
              | {words[m.group(1).lower()] for m in grade.finditer(text)})
    if len(marked) == 1:
        return next(iter(marked)), "bold_or_grade"
    if len(marked) > 1:
        return "unparseable", "several"
    hits = list(any_w.finditer(text))
    distinct = {words[m.group(1).lower()] for m in hits}
    if not distinct:
        return "unparseable", "none"
    if len(distinct) > 1:
        return "unparseable", "several"
    for m in hits:
        before = tok.findall(text[:m.start()].lower().replace("’", "'"))[-3:]
        if any(w in neg or w.endswith("n't") for w in before):
            return "unparseable", "negated"
        if any(w in qual for w in before):
            return "unparseable", "qualified"
    return next(iter(distinct)), "one_word"


def test_part4_parse_grade_unchanged() -> None:
    vocab = ["correct", "Correct", "CORRECT", "incorrect", "partial", "abstained", "abstain", "abstains",
             "abstention", "not", "never", "no", "nor", "neither", "isn't", "doesn’t", "partially", "only", "mostly",
             "grade:", "Grade =", "**", "**Correct**", "**Partial.**", "answer", "setup", "is", "the", "it", "a",
             "x", ".", ",", ";", "-", "\n", "correctly", "Grade:**Incorrect**", "label:", "answer:"]
    rng = random.Random(824)
    corpus = ["", "Maybe.", "Neither correct", "neither; neither", "correct correct", "not correct correct"]
    for _ in range(20000):
        corpus.append(" ".join(rng.choice(vocab) for _ in range(rng.randint(1, 9))))
    assert all(p4.parse_grade(t) == _original_parse_grade(t) for t in corpus)
    # the compiled patterns are the original strings
    lre = "|".join(sorted(p4.LABEL_WORDS, key=len, reverse=True))
    assert p4._GRADE.pattern == rf"\bgrade\s*[:=\-]\s*\**\s*({lre})\b"
    assert p4._BOLD.pattern == rf"\*\*\s*({lre})\s*[.:!]?\s*\*\*" and p4._WORDS.pattern == rf"\b({lre})\b"


# ------------------------------------------------------------------ the run


def test_reference_run(reference: tuple[Path, dict[str, Any]]) -> None:
    out, info = reference
    r = json.loads((out / "results-part4-addendum-q.json").read_text())
    an = r["analysis"]
    assert r["status"] == "EXPLORATORY" and r["citable"] is True
    assert an["n_questions"] == N_M and an["n_pairs"] == 2 * N_M  # the duplicate and the S question are not in
    rows = store_rows(out)
    assert len(rows) == 2 * 2 * N_M                                # one row per (pair, classifier)
    assert rows[aq.pair_key("M005k0", "A", "opus55")]["v"] == "no_label"
    assert rows[aq.pair_key("M005k0", "A", "opus55")]["rule"] == "empty"
    assert rows[aq.pair_key("M006k0", "B", "sol")]["rule"] == "truncated"
    assert rows[aq.pair_key("M007k0", "A", "opus55")]["v"] == "unparseable"
    # one call per pair and classifier, plus the empty retries of the one 0-token empty pair
    assert info["n"] == 2 * 2 * N_M + 2
    assert set(info["ports"]) == {GATEWAY.port}
    st = json.loads((out / "addq-state.json").read_text())
    assert st["status"] == "complete" and st["counts"] == {"planned": 48, "done": 48}
    assert an["sensitivity"]["reproduces_part4"] is True


def test_fallback_model_and_temperature_at_the_mock(reference: tuple[Path, dict[str, Any]]) -> None:
    bodies = reference[1]["bodies"]
    assert bodies and all(b["fallback_model"] == "" for b in bodies)
    sol = [b for b in bodies if b["model"] == SOL]
    opus = [b for b in bodies if b["model"] == OPUS]
    assert len(sol) == 2 * N_M and len(opus) == 2 * N_M + 2
    assert all("temperature" not in b and b["max_tokens"] == 4096 for b in sol)
    assert all(b["temperature"] == 0 and b["max_tokens"] == 1024 for b in opus)
    assert all(b["messages"][0]["role"] == "user" and PRODUCT in b["messages"][0]["content"] for b in bodies)


def test_concurrency_at_most_four(reference: tuple[Path, dict[str, Any]], tmp_path: Path) -> None:
    assert 2 <= reference[1]["max_active"] <= 4
    with pytest.raises(SystemExit, match="inflight"):
        aq.addq_config(base_config(gateway={"inflight": 5}))
    with pytest.raises(SystemExit, match="inflight"):
        aq.addq_config(base_config(gateway={"inflight": 0}))


def test_pause_file_then_identical_resume(cfg: Path, reference: tuple[Path, dict[str, Any]], tmp_path: Path,
                                          capsys: pytest.CaptureFixture[str]) -> None:
    out = prep_out(tmp_path / "run")
    W.reset(out)
    W.pause_at = 15
    assert run(cfg, out) == aq.EXIT_PAUSED
    st = json.loads((out / "addq-state.json").read_text())
    assert st["status"] == "paused" and st["reason"] == "pause_file" and 0 < st["counts"]["done"] < 48
    assert "GATEWAY-NOTICE part4-addq: PAUSED (pause_file)" in capsys.readouterr().out
    assert not (out / "results-part4-addendum-q.json").exists()
    assert run(cfg, out, resume=True) == aq.EXIT_PAUSED          # the PAUSE file is still there
    (out / "PAUSE").unlink()
    n_first = W.n
    W.pause_at = None
    assert run(cfg, out, resume=True) == aq.EXIT_OK
    assert comparable(out) == comparable(reference[0])
    assert store_rows(out) == store_rows(reference[0])
    # nothing answered was re-issued (a 0-token empty interrupted mid-retry is retried, by design)
    assert n_first < W.n and max(W.calls.values()) == 1 and sum(W.calls.values()) == 2 * 2 * N_M - 2
    r = json.loads((out / "results-part4-addendum-q.json").read_text())
    assert [s["end"] for s in r["run_segments"]] == ["paused", "paused", "complete"]


def test_rate_limit_pause_then_identical_resume(cfg: Path, reference: tuple[Path, dict[str, Any]],
                                                tmp_path: Path) -> None:
    out = prep_out(tmp_path / "run")
    W.reset(out)
    W.rate_limit_from = 20                                        # 429 from the 20th request on, past the backoffs
    assert run(cfg, out) == aq.EXIT_PAUSED
    st = json.loads((out / "addq-state.json").read_text())
    assert st["reason"] == "rate_limit"
    log = [json.loads(x) for x in (out / "addq-calls.jsonl").read_text().splitlines()]
    seq: dict[str, list[int]] = {}
    for x in log:
        if x["status"] == "rate_limited:429":
            seq.setdefault(x["key"], []).append(x["backoff"])
    assert [0, 1, 2] in seq.values()                              # two backoffs, then the pause
    W.rate_limit_from = None
    assert run(cfg, out, resume=True) == aq.EXIT_OK
    assert comparable(out) == comparable(reference[0])


def test_resume_refuses_on_sha_mismatch_and_frozen_prereg(cfg: Path, tmp_path: Path) -> None:
    out = prep_out(tmp_path / "run")
    W.reset(out)
    W.pause_at = 5
    assert run(cfg, out) == aq.EXIT_PAUSED
    (out / "PAUSE").unlink()
    W.pause_at = None
    COMMIT["c"] = "deadbeef"
    try:
        assert run(cfg, out, resume=True) == aq.EXIT_HARD_STOP
    finally:
        COMMIT["c"] = "c0ffee"
    q = out / "part4-questions.jsonl"
    keep = q.read_text()
    q.write_text(keep + "\n")                                     # an input changed under a paused run
    assert run(cfg, out, resume=True) == aq.EXIT_HARD_STOP
    q.write_text(keep)
    assert run(cfg, out) == aq.EXIT_HARD_STOP                     # a state file exists: --resume needed
    # the frozen prereg sha, from the config
    out2 = prep_out(tmp_path / "run2")
    bad = make_config(tmp_path / "bad.json", files={"prereg_sha256": "0" * 64})
    W.reset(out2)
    assert run(bad, out2) == aq.EXIT_HARD_STOP and W.n == 0
    assert json.loads((out2 / "addq-state.json").read_text())["reason"] == "prereg_sha_mismatch"
    good = make_config(tmp_path / "good.json",
                       files={"prereg_sha256": p4.file_sha(out2 / "PREREG-addq-synthetic.md")})
    out3 = prep_out(tmp_path / "run3")
    W.reset(out3)
    assert run(good, out3) == aq.EXIT_OK


def test_inputs_mismatch_hard_stops_before_any_call(cfg: Path, tmp_path: Path) -> None:
    p = json.loads(json.dumps(SYNTH_PART4))
    p["questions"].pop("M003k0")
    out = prep_out(tmp_path / "run", p)
    W.reset(out)
    assert run(cfg, out) == aq.EXIT_HARD_STOP and W.n == 0
    assert json.loads((out / "addq-state.json").read_text())["reason"].startswith("inputs_mismatch")
    out2 = prep_out(tmp_path / "run2")
    W.reset(out2)
    assert run(make_config(tmp_path / "c.json", expected_questions=13), out2) == aq.EXIT_HARD_STOP and W.n == 0


def test_refuses_private_outputs_inside_the_repo(cfg: Path, reference: tuple[Path, dict[str, Any]]) -> None:
    inside = aq.REPO / "python" / "scripts" / "eval" / "addq-outputs-must-not-exist"
    with pytest.raises(SystemExit, match="inside the repository"):
        aq.AddQ(cfg, inside, prov)
    assert not inside.exists()
    a = aq.AddQ(cfg, reference[0], prov)
    with pytest.raises(SystemExit, match="inside the repository"):
        a.export_human(aq.REPO / "human-sample.json")
    assert not (aq.REPO / "human-sample.json").exists()


def test_prompt_template_guards() -> None:
    c = base_config()
    assert aq.prompt_template(c) == ADDQ_USER
    for bad in (ADDQ_USER.replace(PRODUCT, "<PRODUCT>"), ADDQ_USER.replace("{quote}", "quote"),
                ADDQ_USER + " {question}", ""):
        c["prompts"]["ADDQ_USER"] = bad
        with pytest.raises(SystemExit):
            aq.prompt_template(c)


def test_example_snippet_is_complete_and_valid() -> None:
    ex_dir = Path(__file__).resolve().parent / "examples"
    base = json.loads((ex_dir / "part4-config.example.json").read_text())
    snip = json.loads((ex_dir / "part4-addendum-q-config.example.json").read_text())
    merged = {**base, "prompts": {**base["prompts"], **snip["prompts"]}, "addendum_q": snip["addendum_q"]}
    conf = aq.addq_config(merged)
    assert set(snip["addendum_q"]) == set(aq.DEFAULTS)
    for k, v in aq.DEFAULTS.items():
        if isinstance(v, dict):
            assert set(snip["addendum_q"][k]) == set(v), k
    assert conf["gateway"]["inflight"] == 4 and conf["human"]["seed"] == "261026|Q"
    assert (conf["primary"]["max_tokens"], conf["limits"]["output_token_cap"], conf["expected_questions"]) == (
        1024, 1_000_000, 372)
    with pytest.raises(SystemExit, match="PRODUCT"):          # the synthetic prompt cannot be run as is
        aq.prompt_template(merged)
    filled = json.loads(json.dumps(merged).replace("<PRODUCT>", "Example Product"))
    tpl = aq.prompt_template(filled)
    assert "{question}" in tpl and "{quote}" in tpl
    with pytest.raises(SystemExit, match="unknown addendum_q key"):
        aq.addq_config({"addendum_q": {**snip["addendum_q"], "typo": 1}})


def test_defaults_match_the_spec() -> None:
    d = aq.DEFAULTS
    assert d["primary"] == {"model": "Claude Opus 5.5", "tag": "opus55", "max_tokens": 1024, "temperature": 0}
    assert d["secondary"] == {"model": "GPT-5.6 Sol", "tag": "sol", "max_tokens": 4096, "temperature": None}
    assert d["primary"]["max_tokens"] == 1024 and d["limits"]["output_token_cap"] == 1_000_000
    assert d["expected_questions"] == 372
    assert d["gateway"]["inflight"] == 4
    assert d["files"]["state"] == "addq-state.json" and d["files"]["labels"].startswith("addq-")
    assert d["files"]["call_log"].startswith("addq-") and d["files"]["results"] == "results-part4-addendum-q.json"


def test_cli_analyse_recomputes_the_results(cfg: Path, reference: tuple[Path, dict[str, Any]],
                                            tmp_path: Path) -> None:
    import shutil

    out = tmp_path / "copy"
    shutil.copytree(reference[0], out)
    (out / "results-part4-addendum-q.json").unlink()
    W.reset(out)
    assert aq.main(["--private-config", str(cfg), "--out-dir", str(out), "analyse"], provenance=prov) == aq.EXIT_OK
    assert W.n == 0
    a = json.loads((out / "results-part4-addendum-q.json").read_text())["analysis"]
    b = json.loads((reference[0] / "results-part4-addendum-q.json").read_text())["analysis"]
    assert a == b


# ------------------------------------------------------------------ the analysis


def test_clean_set_filter() -> None:
    recs = [{"record_id": q} for q in ("q1", "q2", "q3", "q4", "q5")]
    lab = {("q1", "A"): "answer", ("q1", "B"): "answer",
           ("q2", "A"): "answer", ("q2", "B"): "setup",
           ("q3", "A"): "neither", ("q3", "B"): "answer",
           ("q4", "A"): "answer", ("q4", "B"): "unparseable",
           ("q5", "A"): "no_label", ("q5", "B"): "answer"}
    assert aq.clean_ids(recs, lab) == ["q1"]


def test_clean_sets_in_the_reference_run(reference: tuple[Path, dict[str, Any]]) -> None:
    out = reference[0]
    an = json.loads((out / "results-part4-addendum-q.json").read_text())["analysis"]
    rows = store_rows(out)
    for tag, name in (("opus55", "primary"), ("sol", "secondary")):
        exp = sorted(f"M{i:03d}k0" for i in range(N_M)
                     if all(rows[aq.pair_key(f"M{i:03d}k0", p, tag)]["v"] == "answer" for p in "AB"))
        assert an["clean_sets"][name]["ids"] == exp
    inter = sorted(set(an["clean_sets"]["primary"]["ids"]) & set(an["clean_sets"]["secondary"]["ids"]))
    assert an["clean_sets"]["intersection"]["ids"] == inter
    assert set(an["sensitivity"]["clean"]) == {"primary", "secondary", "intersection"}
    for name, blk in an["sensitivity"]["clean"].items():
        assert blk["n_questions"] == an["clean_sets"][name]["n"]
    # prevalence by position: every hint-setup quote is B, labelled setup by the primary
    pos = an["prevalence"]["opus55"]["by_position"]
    assert pos["B"]["counts"]["setup"] >= 3 and set(pos) == {"A", "B"}
    assert an["prevalence"]["opus55"]["overall"]["per_quote"]["n"] == 2 * N_M


#: 13 pages, one M question each; both readers alike. Clean set = pages 8..12.
#: T512 = L = SEM512 = 1 on pages 0..11, 0 on page 12; T128 = 0 on 0..7, 1 on 8..12; T1024 = 1.
#: H1 (T512 - T128, superiority): +1 on 0..7, -1 on 12 -> 9 non-zero pages, S = 7: the flips with
#:   at most one minus reach it: p = 10/512 (full). Clean: page 12 only (-1): p = 1; net share - .
#: H2, H4 (d = 0 everywhere, NI): every shifted page sum is +.075: p = 1/2^13 (full), 1/2^5 (clean).
#: H3 (T512 - T1024, NI): 0 on 0..11, -1 on 12: the shifted sum is negative, p >= .5 on both sets.
#: Holm (alpha .05, 4): full: 1/8192, 1/8192, .0195 <= .0125, .0167, .025 -> H1, H2, H4 confirmed;
#:   clean: 1/32 = .03125 > .0125 -> nothing confirmed.
#: Reading: H3 unchanged; H1 changed (net share 7/13 -> -1/5: the sign flips);
#:   H2 and H4 unresolved on the clean set (confirmed -> not shown non-inferior, net share 0 -> 0).
SENS_PAGES = 13


def sens_fixture() -> tuple[dict[str, Any], list[str]]:
    t512 = [1] * 12 + [0]
    arms = {"T512o0": t512, "L": t512, "SEM512": t512, "T128o0": [0] * 8 + [1] * 5, "T1024o0": [1] * 13}
    qids = [f"M{i:03d}k0" for i in range(SENS_PAGES)]
    per_item, questions = items_from(dict.fromkeys(READERS, arms), qids, [f"d{i}" for i in range(SENS_PAGES)],
                                     extra={"S000p0": {"set": "S", "stratum": "alpha", "doc_id": "d0", "gen": "gen",
                                                       "page_tokens": 3000}})
    return part4_results(per_item, questions), qids[8:]


def test_sensitivity_reading_hand_checked() -> None:
    part4, clean = sens_fixture()
    s = aq.sensitivity(part4, {"primary": clean})
    assert s["reproduces_part4"] is True
    full = s["full"]["hypotheses"]
    assert {h: v["status"] for h, v in full.items()} == {
        "H1": "confirmed", "H2": "confirmed", "H3": "not shown non-inferior", "H4": "confirmed"}
    for rd in READERS:
        assert full["H1"]["per_reader"][rd]["p"] == pytest.approx(10 / 512)
        assert full["H2"]["per_reader"][rd]["p"] == pytest.approx(1 / 8192)
        assert full["H4"]["per_reader"][rd]["p"] == pytest.approx(1 / 8192)
        assert full["H3"]["per_reader"][rd]["p"] >= 0.5
        assert full["H1"]["per_reader"][rd]["net_share"] == pytest.approx(7 / 13)
    c = s["clean"]["primary"]
    assert c["n_questions"] == 5 and c["dropped"] == 8
    hy = c["hypotheses"]
    for rd in READERS:
        assert hy["H1"]["clean_detail"]["per_reader"][rd]["p"] == pytest.approx(1.0)
        assert hy["H1"]["clean_detail"]["per_reader"][rd]["net_share"] == pytest.approx(-1 / 5)
        assert hy["H2"]["clean_detail"]["per_reader"][rd]["p"] == pytest.approx(1 / 32)
        assert hy["H3"]["clean_detail"]["per_reader"][rd]["p"] >= 0.5
    assert {h: v["clean"] for h, v in hy.items()} == {
        "H1": "not confirmed", "H2": "not shown non-inferior", "H3": "not shown non-inferior",
        "H4": "not shown non-inferior"}
    assert {h: v["reading"] for h, v in hy.items()} == {
        "H1": "changed", "H2": "unresolved on the clean set", "H3": "unchanged", "H4": "unresolved on the clean set"}
    assert c["summary"].startswith("not robust")
    # all questions clean: every verdict unchanged -> robust
    allq = sorted(q for q, v in part4["questions"].items() if v["set"] == "M")
    assert aq.sensitivity(part4, {"all": allq})["clean"]["all"]["summary"].startswith("robust")


@pytest.mark.parametrize("key", ["p", "p_holm", "net_share"])
def test_sensitivity_flags_a_perturbed_stored_family(key: str) -> None:
    part4, clean = sens_fixture()
    assert aq.sensitivity(part4, {"primary": clean})["reproduces_part4"] is True
    rd = part4["analysis"]["family"]["hypotheses"]["H1"]["per_reader"][READERS[0]]
    assert key in rd, "the stored family must carry this key for the test to mean anything"
    rd[key] = rd[key] + 0.01                                       # statuses all still match
    s = aq.sensitivity(part4, {"primary": clean, "other": clean[:2]})
    assert s["reproduces_part4"] is False and s["reproduces_part4_by_hypothesis"]["H1"] is False
    for blk in s["clean"].values():
        assert blk["summary"] == "not read: full set does not reproduce part 4"


def test_sensitivity_flags_a_part4_mismatch() -> None:
    part4, clean = sens_fixture()
    part4["analysis"]["family"]["hypotheses"]["H3"]["status"] = "confirmed"
    s = aq.sensitivity(part4, {"primary": clean})
    assert s["reproduces_part4"] is False and s["reproduces_part4_by_hypothesis"]["H3"] is False
    assert s["clean"]["primary"]["summary"] == "not read: full set does not reproduce part 4"


def test_reading_rule_unit() -> None:
    def hy(status: str, *net: float) -> dict[str, Any]:
        return {"status": status, "per_reader": {rd: {"net_share": x} for rd, x in zip(READERS, net, strict=True)}}

    assert aq.reading(hy("confirmed", .1, .1), hy("confirmed", .05, .2), READERS) == "unchanged"
    assert aq.reading(hy("confirmed", .1, .1), hy("reader-dependent", .05, .02), READERS) == \
        "unresolved on the clean set"
    assert aq.reading(hy("confirmed", .1, .1), hy("not confirmed", .05, -.02), READERS) == "changed"
    assert aq.reading(hy("not confirmed", .0, .0), hy("confirmed", .3, .3), READERS) == "changed"
    assert aq.reading(hy("confirmed", .0, .1), hy("not confirmed", .0, .1), READERS) == "unresolved on the clean set"


def test_kappa_hand_checked() -> None:
    # 10 pairs both labelled, plus one excluded (a no_label). Table (rows A, cols B):
    #   answer: 5 answer, 1 setup | setup: 2 setup, 1 answer | neither: 1 neither
    # po = 8/10; pe = (6*6 + 3*3 + 1*1)/100 = .46; kappa = (.8 - .46)/.54 = .6296
    a = ["answer"] * 6 + ["setup"] * 3 + ["neither", "answer"]
    b = ["answer"] * 5 + ["setup", "setup", "setup", "answer", "neither", "no_label"]
    k = aq.kappa_block(a, b, {"n_boot": 200, "level": 0.95, "seed": 1})
    assert k["n_labelled_both"] == 10 and k["n_excluded"] == 1 and k["agreement"] == 0.8
    assert k["three_class"]["kappa"] == pytest.approx((0.8 - 0.46) / 0.54)
    lo, hi = k["three_class"]["ci"]
    assert lo <= k["three_class"]["kappa"] <= hi
    # answer vs not: 2x2 [[5, 1], [1, 3]]: po = .8, pe = .6*.6 + .4*.4 = .52, kappa = .28/.48
    assert k["answer_vs_not"]["kappa"] == pytest.approx(0.28 / 0.48)


# ------------------------------------------------------------------ the human sample


def test_human_export_is_blinded_and_deterministic(cfg: Path, reference: tuple[Path, dict[str, Any]],
                                                   tmp_path: Path) -> None:
    a = aq.AddQ(cfg, reference[0], prov)
    p1, p2 = tmp_path / "h1" / "sample.json", tmp_path / "h2" / "sample.json"
    p1.parent.mkdir()
    p2.parent.mkdir()
    a.export_human(p1)
    a.export_human(p2)
    assert p1.read_text() == p2.read_text()
    ex = json.loads(p1.read_text())
    key = json.loads((tmp_path / "h1" / "sample.key.json").read_text())["key"]
    assert len(ex["items"]) == 6 and set(key) == {it["item"] for it in ex["items"]}
    assert all(set(it) == {"item", "question", "quote"} for it in ex["items"])
    text = p1.read_text()
    for leak in ("record_id", "opus55", '"sol"', OPUS, SOL, "M00", '"position"', '"v"'):
        assert leak not in text
    # the draw: seed "261026|Q" over the sorted pairs
    pairs = sorted((f"M{i:03d}k0", p) for i in range(N_M) for p in "AB")
    exp = random.Random("261026|Q").sample(pairs, 6)
    assert [(key[f"q{n:03d}"]["record_id"], key[f"q{n:03d}"]["position"]) for n in range(6)] == exp


def test_human_import_kappa(cfg: Path, reference: tuple[Path, dict[str, Any]], tmp_path: Path) -> None:
    a = aq.AddQ(cfg, reference[0], prov)
    sample = tmp_path / "sample.json"
    a.export_human(sample)
    key = json.loads((tmp_path / "sample.key.json").read_text())["key"]
    rows = store_rows(reference[0])
    ex = json.loads(sample.read_text())
    for it in ex["items"]:   # the human agrees with the primary wherever it has a label
        v = rows[aq.pair_key(key[it["item"]]["record_id"], key[it["item"]]["position"], "opus55")]["v"]
        it["label"] = v if v in aq.LABELS else "neither"
    graded = tmp_path / "graded.json"
    graded.write_text(json.dumps(ex))
    out = tmp_path / "copy"
    import shutil

    shutil.copytree(reference[0], out)
    b = aq.AddQ(cfg, out, prov)
    assert b.import_human(graded, tmp_path / "sample.key.json") == aq.EXIT_OK
    res = json.loads((out / "addq-human.json").read_text())
    assert res["n_graded"] == 6 and res["missing"] == []
    assert res["opus55"]["agreement"] == 1.0
    assert aq.AddQ(cfg, out, prov).analyse_only() == aq.EXIT_OK
    assert json.loads((out / "results-part4-addendum-q.json").read_text())["analysis"]["human"]["n_graded"] == 6
    ex["items"][0]["label"] = "partial"
    graded.write_text(json.dumps(ex))
    with pytest.raises(SystemExit, match="labels must be one of"):
        b.import_human(graded, tmp_path / "sample.key.json")


# ------------------------------------------------------------------ review fixes


def test_prevalence_clean_count_equals_clean_set(reference: tuple[Path, dict[str, Any]]) -> None:
    an = json.loads((reference[0] / "results-part4-addendum-q.json").read_text())["analysis"]
    for tag, name in (("opus55", "primary"), ("sol", "secondary")):
        assert an["clean_sets"][name]["n"] > 0
        assert an["prevalence"][tag]["overall"]["per_question"]["counts"]["clean"] == len(an["clean_sets"][name]["ids"])
    # also when nothing is clean: the key is present and 0
    recs = [{"record_id": "q1", "stratum": "s"}]
    lab = {("q1", "A"): "setup", ("q1", "B"): "answer"}
    assert aq.prevalence(recs, lab)["overall"]["per_question"]["counts"]["clean"] == 0
    assert aq.clean_ids(recs, lab) == []


def test_length_cuts_reported(reference: tuple[Path, dict[str, Any]], tmp_path: Path) -> None:
    an = json.loads((reference[0] / "results-part4-addendum-q.json").read_text())["analysis"]["length_cuts"]
    assert an["primary"]["truncated"] == 0 and an["truncated_share_over_1pct"] is False
    assert an["secondary"]["truncated"] == 1 and an["secondary"]["share"] == pytest.approx(1 / 24, abs=1e-4)
    rows = {"a": {"v": "no_label", "rule": "truncated"}, "b": {"v": "no_label", "rule": "empty"},
            "c": {"v": "answer", "rule": "one_word"}, "d": {"v": "setup", "rule": "one_word"}}
    assert aq.length_cuts(rows) == {"n": 4, "truncated": 1, "share": 0.25}
    # the roles swapped: the long-think classifier is the primary -> over 1 %
    out = prep_out(tmp_path / "swap")
    c = make_config(tmp_path / "swap.json", primary={"model": SOL, "tag": "solp"},
                    secondary={"model": OPUS, "tag": "opusp"})
    W.reset(out)
    assert run(c, out) == aq.EXIT_OK
    lc = json.loads((out / "results-part4-addendum-q.json").read_text())["analysis"]["length_cuts"]
    assert lc["primary"]["tag"] == "solp" and lc["primary"]["truncated"] == 1
    assert lc["truncated_share_over_1pct"] is True


def test_resume_after_hard_stop_is_refused(tmp_path: Path) -> None:
    out = prep_out(tmp_path / "run")
    c = make_config(tmp_path / "c.json", expected_questions=N_M + 1)
    W.reset(out)
    assert run(c, out) == aq.EXIT_HARD_STOP
    st = json.loads((out / "addq-state.json").read_text())
    assert st["status"] == "hard_stop" and st["reason"].startswith("expected_questions_mismatch")
    assert len(st["segments"]) == 1
    # every sha still matches, so only the hard-stop guard can refuse this
    assert run(c, out, resume=True) == aq.EXIT_HARD_STOP
    st2 = json.loads((out / "addq-state.json").read_text())
    assert st2 == st and W.n == 0                                  # refused: state untouched, no call


def test_addendum_failures_five_in_a_row(tmp_path: Path) -> None:
    out = prep_out(tmp_path / "run")
    c = make_config(tmp_path / "c.json", gateway={"inflight": 1})
    W.reset(out)
    W.fail_re = re.compile(r"item (2|3|4) ")                       # 2A 2B 3A 3B 4A: five in a row
    try:
        assert run(c, out) == aq.EXIT_PAUSED
    finally:
        W.fail_re = None
    st = json.loads((out / "addq-state.json").read_text())
    assert st["status"] == "paused" and st["reason"] == "failures_5_in_a_row"
    assert st["totals"]["since_start"]["gateway_failures:http_500"] == 5


def test_addendum_failures_over_20_persist_across_resume(tmp_path: Path) -> None:
    out = prep_out(tmp_path / "run")
    c = make_config(tmp_path / "c.json", gateway={"inflight": 1}, limits={"max_fail_total": 3})
    W.reset(out)
    W.fail_re = re.compile(r"Quote A of item (0|1|2|3) ")           # spaced: each B succeeds in between
    try:
        assert run(c, out) == aq.EXIT_PAUSED
        st = json.loads((out / "addq-state.json").read_text())
        assert st["reason"] == "failures_over_3"
        assert st["totals"]["since_start"]["gateway_failures:http_500"] == 4
        # resume: ONE more failure; the run total (4 + 1) is over the limit, one alone would not be
        W.fail_re = re.compile(r"Quote A of item 8 ")
        assert run(c, out, resume=True) == aq.EXIT_PAUSED
    finally:
        W.fail_re = None
    st = json.loads((out / "addq-state.json").read_text())
    assert st["reason"] == "failures_over_3"
    assert st["totals"]["since_start"]["gateway_failures:http_500"] == 5
    assert st["segments"][-1]["totals_since_resume"]["gateway_failures:http_500"] == 1


def test_analyse_enforces_the_frozen_prereg_sha(cfg: Path, reference: tuple[Path, dict[str, Any]],
                                                tmp_path: Path) -> None:
    import shutil

    out = tmp_path / "copy"
    shutil.copytree(reference[0], out)
    res = out / "results-part4-addendum-q.json"
    res.unlink()
    bad = make_config(tmp_path / "bad.json", files={"prereg_sha256": "0" * 64})
    W.reset(out)
    assert aq.main(["--private-config", str(bad), "--out-dir", str(out), "analyse"],
                   provenance=prov) == aq.EXIT_HARD_STOP
    assert not res.exists() and W.n == 0
    good = make_config(tmp_path / "good.json",
                       files={"prereg_sha256": p4.file_sha(out / "PREREG-addq-synthetic.md")})
    assert aq.main(["--private-config", str(good), "--out-dir", str(out), "analyse"], provenance=prov) == aq.EXIT_OK
    assert res.exists()


@pytest.mark.parametrize("bad", [0, -1, True, "2", 1.5, None])
def test_gateway_passes_must_be_a_positive_int(bad: Any) -> None:
    with pytest.raises(SystemExit, match="passes"):
        aq.addq_config({"addendum_q": {"files": {"prereg": "p.md"}, "gateway": {"passes": bad}}})
    assert aq.addq_config({"addendum_q": {"files": {"prereg": "p.md"}, "gateway": {"passes": 1}}})["gateway"]["passes"] == 1
