"""Part 4 of the deployed-docs trial, against in-process mock servers only.

Every endpoint is a mock bound to 127.0.0.1 on an ephemeral port (gateway,
two readers, an ES-like store); arm building, token counting and retrieval
are injected fakes at the :class:`deployed_docs_part4.Env` seam. Pages,
prompts and questions are synthetic.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import shutil
import signal
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
import deployed_docs_trial as dt  # noqa: E402
from section_aware_trial import C, norm  # noqa: E402

# ------------------------------------------------------------------ mocks


class Mock:
    """A JSON HTTP server on 127.0.0.1:<ephemeral>; ``handler(path, body) -> (status, obj)``."""

    def __init__(self, handler: Any) -> None:
        class H(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                self._go(None)

            def do_POST(self) -> None:  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                self._go(json.loads(self.rfile.read(n) or b"null"))

            def _go(self, body: Any) -> None:
                status, obj = handler(self.path, body)
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


def h(s: str) -> int:
    return int(hashlib.sha256(s.encode()).hexdigest()[:8], 16)


GEN_SYSTEM = "SYNTH-GEN-SYSTEM"
GEN_USER = ("Rules:\n- Quote exactly except names of tools, commands, file formats or organisms.\n"
            "- Answer in JSON.\n\nPassage:\n{passage}")
M_USER = "Two passages.\nA:\n{a}\nB:\n{b}"
GRADER_USER = "Question: {question}\nReference:\n{gold}\nAnswer: {answer}\nGrade it."
READER_SYSTEM = "SYNTH-READER"
GEN_MODEL, FB_MODEL, G1_MODEL, G2_MODEL = "synth-gen", "synth-fallback", "synth-grader-a", "synth-grader-b"


def page_text(d: int, n_words: int) -> str:
    out, sent = [], []
    for i in range(n_words):
        sent.append(f"p{d}w{i}")
        if len(sent) == 6:
            out.append(" ".join(sent) + ".")
            sent = []
    if sent:
        out.append(" ".join(sent) + ".")
    lines = [" ".join(out[i:i + 5]) for i in range(0, len(out), 5)]
    return "\n".join(lines)


PAGES = {"d0": ("a-zero.md", page_text(0, 330), 3), "d1": ("b-one.md", page_text(1, 210), 2),
         "d2": ("a-two.md", page_text(2, 120), 1)}


def es_rows() -> list[dict[str, Any]]:
    rows = []
    for did, (fn, text, n) in PAGES.items():
        cut = [0] + [text.index(" ", len(text) * k // n) + 1 for k in range(1, n)] + [len(text)]
        for k in range(n):
            rows.append({"chunk_id": f"{did}-{k}", "doc_id": did, "content": text[cut[k]:cut[k + 1]],
                         "start_char": cut[k], "end_char": cut[k + 1], "metadata": {"filename": fn},
                         "symbol_glossary": ""})
    return rows


def full_sentence(p: str, which: int = 0) -> str:
    """The (which+1)-th complete sentence of passage ``p`` (after the first sentence end)."""
    words = p.split()
    ends = [i for i, w in enumerate(words) if w.endswith(".")]
    i, j = ends[which], ends[which + 1]
    return " ".join(words[i + 1:j + 1])


class World:
    """Mock behaviour knobs, request log and counters, shared by the mock handlers.

    The mocks are deliberately NOT idempotent: ``calls`` counts the answered
    requests per (model, prompt), and a prompt answered before is answered
    differently (a changed question, label or answer). So a call that a resume
    re-issues changes the results, and the byte-identical comparison with the
    uninterrupted reference run is load-bearing. ``calls`` survives
    ``reset(keep_calls=True)``: the "server" lives on across a pause."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.calls: Counter[tuple[str, str]] = Counter()
        self.reset(None)

    def reset(self, out: Path | None, keep_calls: bool = False) -> None:
        self.out = out
        #: (endpoint, model, step, prompt hash) per request, in arrival order
        self.log: list[tuple[str, str, str, str]] = []
        self.bodies: list[dict[str, Any]] = []
        self.count: Counter[str] = Counter()
        if not keep_calls:
            self.calls = Counter()
        self.active: Counter[str] = Counter()
        self.max_active: Counter[str] = Counter()
        self.fire_idx: int | None = None
        self.gw_429: set[str] = set()
        self.gw_429_after = 2
        self.gw_500: set[str] = set()
        self.gw_fail_every: int | None = None  # every n-th gateway request of a segment fails (HTTP 500)
        self.stop_idx: int | None = None
        self.ret_idx: int | None = None
        self.pause_on: tuple[str, int] | None = None
        self.sigterm_on: tuple[str, int] | None = None
        self.reader_503 = False
        self.reader_500 = False
        self.ret_pause = False
        self.ret_down = False
        self.ret_crash = False
        self.commit = "c0ffee"

    def trigger(self, step: str) -> None:
        self.count[step] += 1
        n = self.count[step]
        if self.pause_on == (step, n) and self.out is not None:
            (self.out / "PAUSE").write_text("operator stop\n")
            self.fire_idx = len(self.log)
        if self.sigterm_on == (step, n):
            self.fire_idx = len(self.log)
            os.kill(os.getpid(), signal.SIGTERM)

    def answered(self, model: str, prompt: str) -> int:
        """Count one answered request; returns how many times this prompt was answered."""
        with self.lock:
            self.calls[(model, prompt)] += 1
            return self.calls[(model, prompt)]

    def enter(self, ep: str) -> None:
        with self.lock:
            self.active[ep] += 1
            self.max_active[ep] = max(self.max_active[ep], self.active[ep])

    def leave(self, ep: str) -> None:
        with self.lock:
            self.active[ep] -= 1


W = World()
W.sleep_a_little = lambda: time.sleep(0.002)  # type: ignore[attr-defined]  # lets requests overlap


def chat(content: str, pt: int = 10, ct: int | None = None, finish: str = "stop", model: str = "m") -> dict[str, Any]:
    return {"model": model, "choices": [{"message": {"content": content}, "finish_reason": finish}],
            "usage": {"prompt_tokens": pt, "completion_tokens": len(content.split()) if ct is None else ct}}


def phash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


#: models that reject a temperature (HTTP 400), as the real primary grader does
NO_TEMPERATURE = {FB_MODEL, G1_MODEL}


def gateway_handler(path: str, body: Any) -> tuple[int, Any]:
    W.enter("gateway")
    try:
        W.sleep_a_little()
        return _gateway(body)
    finally:
        W.leave("gateway")


def _gateway(body: Any) -> tuple[int, Any]:
    msgs = body["messages"]
    model = body["model"]
    user = msgs[-1]["content"]
    with W.lock:
        n_before = W.calls[(model, user)]
    step = "generation" if msgs[0]["content"] == GEN_SYSTEM else ("retest" if n_before else "grading")
    with W.lock:
        W.log.append(("gateway", model, step, phash(user)))
        W.bodies.append(body)
        W.trigger(step)
        if step in W.gw_429 and W.count[step] > W.gw_429_after:
            return 429, {"error": "rate limited"}
        if step in W.gw_500:
            return 500, {"error": "boom"}
        W.trigger("gateway")
        if W.gw_fail_every and W.count["gateway"] % W.gw_fail_every == 0:
            return 500, {"error": "intermittent"}
    if model in NO_TEMPERATURE and "temperature" in body:
        return 400, {"error": {"message": "Unsupported parameter: 'temperature' is not supported with this model."}}
    if step == "generation":
        if "\nA:\n" in user:
            a, b = user.split("\nA:\n", 1)[1].split("\nB:\n", 1)
            qa, qb = full_sentence(a), full_sentence(b)
            out = {"question": f"How do {qa.split()[0]} and {qb.split()[0]} relate?", "quote_a": qa, "quote_b": qb}
        else:
            psg = user.split("Passage:\n", 1)[1]
            if model == GEN_MODEL and "p1w" in psg:
                return 200, chat("", ct=0, model=model)   # this generator never answers page d1
            q = full_sentence(psg)
            out = {"question": f"What comes with {q.split()[0]}?", "answer_quote": q}
        n = W.answered(model, user)
        if n > 1:  # not idempotent: a re-issued generation call asks a different question
            out["question"] = f"Again ({n}): " + out["question"]
        return 200, chat(json.dumps(out), model=model)
    answer = user.split("\nAnswer: ", 1)[1].rsplit("\nGrade it.", 1)[0]
    if model == G1_MODEL and h(answer) % 5 == 0:
        return 200, chat("", ct=0, model=model)           # a 0-token empty, every time
    if model == G2_MODEL and h(answer) % 6 == 1:
        return 200, chat("", ct=512, finish="length", model=model)  # max_tokens spent on hidden reasoning
    n = W.answered(model, user)
    salt = "" if n == 1 else f"#{n}"   # the retest (n = 2) and any re-issue are answered differently
    lab = p4.LABELS[h((answer if model == G1_MODEL else answer + "b") + salt) % 4]
    text = {0: f"**{lab.capitalize()}** - the answer matches.", 1: f"Grade: {lab}",
            2: f"I would say {lab}.", 3: f"{lab.upper()}"}[h(answer + "x" + salt) % 4]
    return 200, chat(text, ct=40 if model == G1_MODEL else 3, model=model)


def reader_handler(name: str) -> Any:
    def handler(path: str, body: Any) -> tuple[int, Any]:
        W.enter(name)
        try:
            W.sleep_a_little()
            return _reader(name, body)
        finally:
            W.leave(name)
    return handler


def _reader(name: str, body: Any) -> tuple[int, Any]:
    user = body["messages"][-1]["content"]
    with W.lock:
        W.log.append((name, body["model"], "readers", phash(user)))
        W.trigger("readers")
        if W.reader_503:
            return 503, {"error": "loading"}
        if W.reader_500 and h(user) % 10 == 0:
            return 500, {"error": "oops"}
    q = user.rsplit("Question: ", 1)[1]
    n = user.count("\n\n[") + 1
    fin = "length" if n == 3 else "stop"
    k = W.answered(name, user)
    again = "" if k == 1 else f" (asked {k} times)"   # not idempotent: a re-issue answers differently
    return 200, chat(f"It is {q.split()[-1].rstrip('?')} ({n % 2}, {h(user) % 3}) per {name}{again}.",
                     pt=len(user.split()),
                     finish=fin, model=body["model"])


def es_handler(path: str, body: Any) -> tuple[int, Any]:
    rows = es_rows()
    with W.lock:
        W.log.append(("es", "", path, ""))
    if path.endswith("/_count"):
        return 200, {"count": len(rows)}
    return 200, {"hits": {"hits": [{"_source": r} for r in rows]}}


GATEWAY = Mock(gateway_handler)
SCOUT = Mock(reader_handler("scout"))
QWEN = Mock(reader_handler("qwen"))
ES = Mock(es_handler)
MOCK_PORTS = {GATEWAY.port, SCOUT.port, QWEN.port, ES.port}

# ------------------------------------------------------------------ fakes

SIZES = {"T128o0": 40, "T512o0": 120, "T1024o0": 250}


def ntok(s: str) -> int:
    return len(s.split())


def word_windows(text: str, n: int) -> list[str]:
    sp = [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]
    return [text[sp[i][0]:sp[min(i + n, len(sp)) - 1][1]] for i in range(0, len(sp), n)]


def sentence_packs(text: str, k: int) -> list[str]:
    ends = [m.end() for m in dt._SENT_END.finditer(text)]
    cuts = [0] + ends[k - 1::k]
    if cuts[-1] < len(text):
        cuts.append(len(text))
    return [text[a:b].strip() for a, b in zip(cuts, cuts[1:], strict=False) if text[a:b].strip()]


def fake_build(pages: dict[str, dict[str, Any]], arms: list[str]) -> tuple[dict[str, list[C]], dict[str, int]]:
    out: dict[str, list[C]] = {a: [] for a in arms}
    for did, p in sorted(pages.items()):
        for n, r in enumerate(p["rows"]):
            out["L"].append(C(f"L|{did}|{n}", did, str(r["content"]), "", ""))
        for n, t in enumerate(sentence_packs(p["text"], 5)):
            out["SEM512"].append(C(f"SEM512|{did}|{n}", did, t, "", ""))
        for a, size in SIZES.items():
            for n, t in enumerate(word_windows(p["text"], size)):
                out[a].append(C(f"{a}|{did}|{n}", did, t, "", f"gloss-{a}" if n == 0 else ""))
    for cs in out.values():
        for c in cs:
            c.tokens = ntok(c.content)
    return out, {d: ntok(p["text"]) for d, p in pages.items()}


def fake_retrieve(arm: str, chunks: list[C], recs: list[dict[str, Any]]) -> dict[str, list[str]]:
    import httpx

    if W.ret_idx is None:
        W.ret_idx = len(W.log)
    if W.ret_down:
        raise httpx.ConnectError("synthetic: connection refused")
    if W.ret_crash:
        raise RuntimeError("synthetic: an unexpected bug")
    out = {}
    for r in recs:
        qw = set(re.findall(r"p\d+w\d+", r["question"]))
        scored = sorted(range(len(chunks)), key=lambda i: (-len(qw & set(re.findall(r"p\d+w\d+", chunks[i].content))), i))
        out[r["record_id"]] = [chunks[i].id for i in scored]
    if W.ret_pause and W.out is not None:
        (W.out / "PAUSE").write_text("x")
    return out


def fake_env() -> p4.Env:
    return p4.Env(load_pages=lambda: dt.pages_from_index(dt.read_index()), build_arms=fake_build, ntok=ntok,
                  retrieve=fake_retrieve,
                  provenance=lambda: {"commit": W.commit, "dirty": False, "untracked": 0, "recorded_at": "now"})


def expected_counts() -> dict[str, int]:
    pages, _ = dt.pages_from_index(es_rows())
    arms, _ = fake_build(pages, ["L", "SEM512", *SIZES])
    return {a: len(cs) for a, cs in arms.items()}


PRIOR_QUOTE = full_sentence(PAGES["d0"][1][1000:1400])


def make_config(path: Path, **over: Any) -> Path:
    cfg = {
        "es_url": ES.url, "index": "synthetic-index", "gen_url": QWEN.url, "judge_url": SCOUT.url,
        "gateway_url": GATEWAY.url, "deny_ports": [], "deny_reason": "shared endpoint", "out_dir": "/nonexistent",
        "stratum_prefixes": [["a-", "alpha"], ["b-", "beta"]], "strata": ["alpha", "beta"], "seed_stratum": "seed",
        "files": {}, "seed_questions": {"P": {}, "N": {}, "W": {}},
        "prompts": {"GEN_SYSTEM": GEN_SYSTEM, "GEN_USER": GEN_USER, "JUDGE_USER": "j {question} {chunk}",
                    "M_USER": M_USER, "JUDGE3_USER": "j {question} {chunk}", "READER_SYSTEM": READER_SYSTEM,
                    "GRADER_USER": GRADER_USER},
        "part4": {
            "conditions": {"B4096": {"budget": 150}, "B2048": {"budget": 70}, "k10": {"top": 3}},
            "expected_chunks": expected_counts(),
            "expected_m_pages": 1,
            "reader": {"window_reserve": 0, "retries": 2, "retry_backoff_s": 0.001},
            "generator": {"model": GEN_MODEL, "tag": "gen", "empty_retry_s": 0.001},
            "generator_fallback": {"model": FB_MODEL, "tag": "fb"},
            "grader_primary": {"model": G1_MODEL, "tag": "ga"},
            "grader_secondary": {"model": G2_MODEL, "tag": "gb", "fraction": 0.5},
            "M": {"min_page_tokens": 250, "attempts_per_page": 3, "chars": 300, "gap": 400},
            "S": {"min_live_chunks": 2, "chars": 300, "max_attempts": 4},
            "prior_question_files": ["prior-questions.jsonl"],
            "retest_n": 4,
            "limits": {"empty_backoff_s": 0.001, "rate_backoffs_s": [0.001, 0.001, 0.001],
                       "local_unreachable_s": 0.05, "local_retry_s": 0.005},
            "human": {"part_a": 3, "part_b_pairs": 2},
            "analysis": {"n_perm": 999, "n_boot": 200},
            "files": {"prereg": "PREREG-synthetic.md", "manifest": "manifest.json"},
        },
    }
    for k, v in over.items():
        if k in cfg["part4"] and isinstance(v, dict) and isinstance(cfg["part4"][k], dict):
            cfg["part4"][k] = {**cfg["part4"][k], **v}
        elif k.startswith("base_"):
            cfg[k[5:]] = v
        else:
            cfg["part4"][k] = v
    path.write_text(json.dumps(cfg, indent=1))
    return path


def prep_out(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / "PREREG-synthetic.md").write_text("synthetic prereg v1\n")
    (d / "prior-questions.jsonl").write_text(json.dumps(
        {"record_id": "g000", "doc_id": "d0", "status": "valid", "answer_quote": PRIOR_QUOTE}) + "\n")
    return d


def run(cfg: Path, out: Path, resume: bool = False, **kw: Any) -> int:
    W.out = out
    return p4.run_part4(cfg, out, fake_env(), resume=resume, **kw)


def comparable(out: Path) -> str:
    r = json.loads((out / "results-part4.json").read_text())
    r.pop("provenance")
    r.pop("run_segments")
    return json.dumps(r, sort_keys=True)


@pytest.fixture(scope="module")
def cfg(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_config(tmp_path_factory.mktemp("cfg") / "config.json")


@pytest.fixture(scope="module")
def reference(cfg: Path, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, list[tuple[str, str, str, str]],
                                                                              dict[str, Any]]:
    import httpx

    out = prep_out(tmp_path_factory.mktemp("ref"))
    W.reset(out)
    ports: list[int] = []
    orig_a, orig_s = httpx.AsyncClient.send, httpx.Client.send

    async def spy_a(self: Any, request: Any, *a: Any, **k: Any) -> Any:
        ports.append(request.url.port)
        return await orig_a(self, request, *a, **k)

    def spy_s(self: Any, request: Any, *a: Any, **k: Any) -> Any:
        ports.append(request.url.port)
        return orig_s(self, request, *a, **k)

    httpx.AsyncClient.send, httpx.Client.send = spy_a, spy_s
    try:
        assert run(cfg, out) == p4.EXIT_OK
    finally:
        httpx.AsyncClient.send, httpx.Client.send = orig_a, orig_s
    assert ports and 8004 not in ports and set(ports) <= MOCK_PORTS
    return out, list(W.log), {"bodies": list(W.bodies), "max_active": dict(W.max_active), "calls": dict(W.calls)}


# ------------------------------------------------------------------ unit: truncation


def test_truncation_fills_budget_when_text_suffices() -> None:
    # One-word sentences: a sentence boundary after every token.
    cs = {f"c{i}": C(f"c{i}", "d", " ".join(f"w{i}x{j}." for j in range(10)), "", "", 10) for i in range(5)}
    for budget in (1, 7, 10, 23, 49, 50):
        cx = p4.budget_context(list(cs), budget, cs.__getitem__, ntok)
        assert cx.ctx_tokens == budget == sum(ntok(c.content) for c in cx.chunks)
        assert cx.truncated == (budget % 10 != 0 and budget < 50)
    cx = p4.budget_context(list(cs), 80, cs.__getitem__, ntok)  # the list runs out first
    assert cx.ctx_tokens == 50 and not cx.truncated


def test_truncation_never_exceeds_budget_and_cuts_at_sentences() -> None:
    long = "Alpha beta gamma. Delta epsilon zeta eta theta. Iota kappa.\nLambda mu nu xi omicron pi rho."
    cs = {"a": C("a", "d", "one two three four", "", "", 4), "b": C("b", "d", long, "", "", ntok(long))}
    for budget in range(0, 25):
        cx = p4.budget_context(["a", "b"], budget, cs.__getitem__, ntok)
        assert cx.ctx_tokens <= budget
        if cx.truncated:
            last = cx.chunks[-1].content
            assert long.startswith(last) and last.endswith(".")
    cx = p4.budget_context(["a", "b"], 6, cs.__getitem__, ntok)   # 2 left; the first sentence has 3
    assert cx.ids == ["a"] and cx.truncated_empty and cx.ctx_tokens == 4
    cx = p4.budget_context(["a", "b"], 13, cs.__getitem__, ntok)  # 9 left: two sentences (3 + 5)
    assert cx.chunks[-1].content == "Alpha beta gamma. Delta epsilon zeta eta theta." and cx.ctx_tokens == 12
    assert cs["b"].content == long  # the arm's chunk itself is never changed


def test_truncation_stops_the_walk() -> None:
    """Hand case (the test module's word counter, ``ntok``): a = 4 tokens; b = 3 + 5 + 2
    tokens in three sentences; c = 1 token; budget 9. a fits (4); b would make 14, so it
    is cut to its first sentence (3 of the 5 left): 7. The walk stops there, although c
    (1 token) would still fit."""
    b_text = "Alpha beta gamma. Delta epsilon zeta eta theta. Iota kappa."
    cs = {"a": C("a", "d", "one two three four", "", "", 4), "b": C("b", "d", b_text, "", "", ntok(b_text)),
          "c": C("c", "d", "one", "", "", 1)}
    assert [ntok(cs[k].content) for k in "abc"] == [4, 10, 1]
    cx = p4.budget_context(["a", "b", "c"], 9, cs.__getitem__, ntok)
    assert cx.ids == ["a", "b"] and cx.ctx_tokens == 7 and cx.truncated
    assert cx.chunks[-1].content == "Alpha beta gamma." and cx.shortfall == 2 and not cx.truncated_empty


def test_truncated_empty_only_when_budget_is_left() -> None:
    cs = {"a": C("a", "d", "one two three four", "", "", 4), "b": C("b", "d", "Alpha beta gamma.", "", "", 3)}
    cx = p4.budget_context(["a", "b"], 4, cs.__getitem__, ntok)    # exactly full: nothing left, not flagged
    assert cx.ids == ["a"] and not cx.truncated_empty and cx.shortfall == 0
    cx = p4.budget_context(["a", "b"], 6, cs.__getitem__, ntok)    # 2 left, no sentence fits: flagged
    assert cx.ids == ["a"] and cx.truncated_empty and cx.shortfall == 2
    cx = p4.budget_context(["a"], 10, cs.__getitem__, ntok)        # the list runs out
    assert not cx.truncated_empty and cx.shortfall == 6
    assert p4.top_context(["a", "b"], 1, cs.__getitem__).shortfall is None


def test_truncation_with_whole_chunks_equals_the_old_walk() -> None:
    rng = random.Random(3)
    cs = {f"c{i}": C(f"c{i}", "d", "x. " * rng.randint(1, 30), "", "") for i in range(30)}
    for c in cs.values():
        c.tokens = ntok(c.content)
    for budget in (0, 5, 40, 200, 10_000):
        cx = p4.budget_context(list(cs), budget, cs.__getitem__, ntok)
        whole = dt.walk_budget(list(cs), budget, lambda k: cs[k].tokens)
        assert cx.ids[:len(whole)] == whole and len(cx.ids) - len(whole) <= 1


# ------------------------------------------------------------------ unit: parser


@pytest.mark.parametrize(("text", "label"), [
    ("**Correct** - it states the value.", "correct"),
    ("**Partial**: one part missing", "partial"),
    ("The answer is wrong. **Incorrect.**", "incorrect"),
    ("Grade: abstained", "abstained"),
    ("grade = Correct", "correct"),
    ("Grade: **Partial** even if it looks correct", "partial"),
    ("CORRECT", "correct"),
    ("I would call this incorrect.", "incorrect"),
    ("The reader abstained.", "abstained"),
    ("partially", "unparseable"),
    ("partially correct", "unparseable"),
    ("Mostly correct", "unparseable"),
    ("**Partially correct**", "unparseable"),
    ("It is largely correct.", "unparseable"),
    ("only partial", "unparseable"),
    ("Abstain", "abstained"),
    ("The reader abstains.", "abstained"),
    ("Grade: Abstain", "abstained"),
    ("**Abstention**", "abstained"),
    ("It doesn't abstain", "unparseable"),
    ("The answer is never correct", "unparseable"),
    ("Neither correct", "unparseable"),
    ("It cannot be called correct", "unparseable"),
    ("abstained; abstain", "abstained"),
    ("correctly identifies the flag", "unparseable"),
    ("The answer is not correct.", "unparseable"),
    ("It isn't correct", "unparseable"),
    ("not entirely correct", "unparseable"),
    ("The answer is not correct; it is incorrect.", "unparseable"),
    ("correct or partial?", "unparseable"),
    ("**Correct** ... on reflection **Incorrect**", "unparseable"),
    ("", "unparseable"),
    ("Maybe.", "unparseable"),
])
def test_parse_grade(text: str, label: str) -> None:
    assert p4.parse_grade(text)[0] == label


def test_parse_grade_rules_are_reported() -> None:
    assert p4.parse_grade("**Correct**")[1] == "bold_or_grade"
    assert p4.parse_grade("this is correct")[1] == "one_word"
    assert p4.parse_grade("is not correct")[1] == "negated"
    assert p4.parse_grade("correct, incorrect")[1] == "several"
    assert p4.parse_grade("partially correct")[1] == "qualified"
    assert p4.parse_grade("is n't correct")[1] == "negated"


def test_parse_grade_window_is_three_words() -> None:
    assert p4.parse_grade("not one two three correct")[0] == "correct"      # 4 words before: outside
    assert p4.parse_grade("not one two correct")[0] == "unparseable"         # 3 words before: inside
    assert p4.parse_grade("mostly one two three correct")[0] == "correct"


# ------------------------------------------------------------------ unit: allocation


def synth_pages(cfg: Path) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    dt.load_private_config(str(cfg))
    pages, _ = dt.pages_from_index(es_rows())
    return pages, {d: ntok(p["text"]) for d, p in pages.items()}


def test_allocation_and_fresh_window_exclusion(cfg: Path, tmp_path: Path) -> None:
    pages, ptok = synth_pages(cfg)
    conf = p4.part4_config(json.loads(cfg.read_text()))
    prior = prep_out(tmp_path) / "prior-questions.jsonl"
    spans, st = p4.prior_spans(pages, [prior])
    assert st["prior_quotes"] == 1 and len(spans["d0"]) == 1
    qs, qe = spans["d0"][0]
    assert norm(pages["d0"]["text"][qs:qe]) == norm(PRIOR_QUOTE)
    items, meta = p4.allocate(pages, ptok, spans, conf)
    m = [it for it in items if it["set"] == "M"]
    s = [it for it in items if it["set"] == "S"]
    # M: every page of >= 250 tokens (only d0), 3 attempts each.
    assert {it["doc_id"] for it in m} == {"d0"} and meta["sampling"]["M_planned"] == 3
    for it in m:
        assert abs(it["b"][0] - it["a"][0]) >= 400
    # S: pages with >= 2 live chunks (d0, d1; not d2), one each, then the longest first, capped at 4.
    assert meta["S_eligible_pages"] == 2 and len(s) == 4
    assert [it["id"][-2:] for it in s] == ["p0", "p0", "p1", "p1"]
    assert s[2]["doc_id"] == "d0"  # the longest page gets the first second passage
    for d in ("d0", "d1"):
        w = [tuple(it["p"]) for it in s if it["doc_id"] == d]
        assert not p4.overlaps(*w)
    for it in items:
        for w in p4.windows({k: v for k, v in it.items() if k in ("a", "b", "p")}):
            assert p4.fresh(w[0], w[1], spans.get(it["doc_id"], []))
    assert items == p4.allocate(pages, ptok, spans, conf)[0]  # seeded


def test_fresh_window_exclusion_rejects_and_gives_up(cfg: Path) -> None:
    pages, ptok = synth_pages(cfg)
    conf = p4.part4_config(json.loads(cfg.read_text()))
    n = len(pages["d0"]["text"])
    # Everything on d0 but its last 350 characters is a prior quote: only late windows survive.
    spans = {"d0": [(0, n - 350)]}
    items, meta = p4.allocate(pages, ptok, spans, conf)
    d0 = [it for it in items if it["doc_id"] == "d0"]
    assert all(it["p"][0] >= n - 350 for it in d0 if "p" in it)
    assert meta["sampling"]["rejected_draws"] > 0
    assert meta["sampling"].get("M_no_room", 0) + meta["sampling"].get("M_no_fresh_b", 0) \
        + meta["sampling"].get("M_no_fresh_a", 0) >= 1  # no fresh A/B pair 400 apart fits
    spans = {"d1": [(0, len(pages["d1"]["text"]))]}
    items, meta = p4.allocate(pages, ptok, spans, conf)
    assert not [it for it in items if it["doc_id"] == "d1"] and meta["sampling"]["S_no_fresh"] == 1


def test_duplicate_rule_and_validation(cfg: Path, tmp_path: Path) -> None:
    pages, ptok = synth_pages(cfg)
    conf = p4.part4_config(json.loads(cfg.read_text()))
    st = p4.Store(tmp_path / "g.jsonl")
    text = pages["d0"]["text"]
    q1 = full_sentence(text[:300])
    items = [{"id": f"S000p{i}", "set": "S", "doc_id": "d0", "p": [0, 300]} for i in range(3)]
    items.append({"id": "S000p3", "set": "S", "doc_id": "d0", "p": [0, 300]})
    rows = [json.dumps({"question": "Same?", "answer_quote": q1}), json.dumps({"question": " same? ", "answer_quote": q1}),
            json.dumps({"question": "Long?", "answer_quote": "x" * 301}), "not json"]
    for it, txt in zip(items, rows, strict=True):
        st.put({"k": p4.ckey("generation", it["id"], role="gen"), "status": "ok", "text": txt})
    qs, meta = p4.build_questions(items, st, pages, ptok, conf)
    assert [q["status"] for q in qs] == ["valid", "duplicate", "quote_too_long", "unparseable"]
    assert meta["near_duplicate_pairs"] == {}


# ------------------------------------------------------------------ the full pipeline


def test_reference_run(reference: tuple[Path, list[tuple[str, str, str, str]], dict[str, Any]]) -> None:
    out, reqs, extra = reference
    # The non-idempotent mocks rely on no prompt being asked twice in one run, except the retest.
    assert sorted(Counter(extra["calls"].values()).items())[1:] == [(2, 4)]
    assert {m for (m, _), n in extra["calls"].items() if n == 2} == {G1_MODEL}
    # At most 4 in flight: the gateway and each reader endpoint (and they did overlap).
    assert set(extra["max_active"]) == {"gateway", "scout", "qwen"}
    assert all(2 <= v <= 4 for v in extra["max_active"].values()), extra["max_active"]
    # Every gateway body: fallback_model ""; no temperature for the no-temperature models (the
    # mock would 400 one); the others send theirs.
    bodies = extra["bodies"]
    assert bodies and all(b.get("fallback_model") == "" for b in bodies)
    assert all("temperature" not in b for b in bodies if b["model"] in NO_TEMPERATURE)
    assert {b["model"] for b in bodies if b["model"] in NO_TEMPERATURE} == NO_TEMPERATURE
    assert all(b.get("temperature") == 0 for b in bodies if b["model"] not in NO_TEMPERATURE)
    assert json.loads((out / "results-part4.json").read_text())["design"]["temperature_dropped"] == []
    r = json.loads((out / "results-part4.json").read_text())
    st = json.loads((out / "part4-state.json").read_text())
    assert st["status"] == "complete" and st["schema"] == p4.STATE_SCHEMA
    assert r["manipulation_check"]["T128o0"]["ok"] and r["citable"] is True
    assert r["n"]["M"] >= 1 and r["n"]["S"] >= 2
    assert r["analysis"]["status"] == "complete"
    # Sol-style fallback: every d1 item went to the fallback after one retry; flagged.
    fb = r["gen_fallback"]["items"]
    assert fb and {x["doc_id"] for x in fb} == {"d1"} and all(x["gen"] == "fb" for x in fb)
    gen_rows = [json.loads(x) for x in (out / "part4-gen-calls.jsonl").read_text().splitlines()]
    empties = [x for x in gen_rows if x["k"].endswith("|gen") and x["status"] == "empty"]
    assert empties and all(x["attempts"] == 2 for x in empties)
    assert {x["k"].split("|")[1] for x in gen_rows if x["k"].endswith("|fb")} == {x["k"].split("|")[1] for x in empties}
    # Realised tokens never exceed the budget; B conditions are full here (pages suffice).
    for arm, conds in r["realised_tokens"].items():
        assert conds["B4096"]["ctx_max"] <= 150 and conds["B2048"]["ctx_max"] <= 70
        assert conds["B2048"]["ctx_min"] > 0, arm
    # Coverage: primary on all B answers, k10 only for its subset; secondary only for its subset.
    k10s, sec = set(r["subsets"]["primary_k10"]), set(r["subsets"]["secondary"])
    assert 0 < len(k10s) < len(r["questions"]) and 0 < len(sec) < len(r["questions"])
    for q, arms in r["per_item"].items():
        for cell in arms.values():
            for cond, x in cell.items():
                for rd in ("scout", "qwen"):
                    g = x["readers"][rd]
                    assert (g["ga"] is None) == (cond == "k10" and q not in k10s)
                    assert (g["gb"] is None) == (q not in sec)
    # 0-token empties became no_label (recorded, raw text stored privately in the grades file).
    assert r["grade_rates"]["ga"]["L"]["no_label"] + sum(v["no_label"] for v in r["grade_rates"]["ga"].values()) > 0
    grades = [json.loads(x) for x in (out / "part4-grades-ga.jsonl").read_text().splitlines()]
    assert any(g["v"] == "no_label" and g["attempts"] == 3 for g in grades)
    assert all(g["raw"] for g in grades if g["v"] != "no_label")
    assert r["retest"]["n"] == 4 and r["retest"]["agreement"] is not None  # the mock re-answers differently
    assert sum(1 for s, m, step, _ in reqs if s == "gateway" and step == "retest") == 4  # really re-asked
    assert sum(r["finish_length"]["scout"]["L"].values()) >= 0
    # Dedup: one grading call per (question, normalised answer) per grader.
    keys = [g["k"] for g in grades]
    assert len(keys) == len(set(keys))
    assert sum(1 for s, m, _, _ in reqs if s == "gateway" and m == G1_MODEL) >= len(keys)


def test_no_call_to_the_denied_port(cfg: Path, tmp_path: Path) -> None:
    W.reset(prep_out(tmp_path))
    bad = make_config(tmp_path / "bad.json", base_judge_url="http://127.0.0.1:8004")
    with pytest.raises(SystemExit, match="8004"):
        run(bad, tmp_path)
    bad = make_config(tmp_path / "bad2.json", base_gateway_url="http://127.0.0.1:8004")
    with pytest.raises(SystemExit, match="8004"):
        run(bad, tmp_path)
    assert W.log == []


SCENARIOS = {
    "generation-pause": ("generation", lambda: setattr(W, "pause_on", ("generation", 2)), "pause_file"),
    "generation-429": ("generation", lambda: W.gw_429.add("generation"), "rate_limit"),
    "retrieval-pause": ("retrieval", lambda: setattr(W, "ret_pause", True), "pause_file"),
    "retrieval-down": ("retrieval", lambda: setattr(W, "ret_down", True), "local_unreachable:retrieval"),
    "readers-pause": ("readers", lambda: setattr(W, "pause_on", ("readers", 20)), "pause_file"),
    "readers-down": ("readers", lambda: setattr(W, "reader_503", True), "local_unreachable:"),
    "readers-sigterm": ("readers", lambda: setattr(W, "sigterm_on", ("readers", 15)), "sigterm"),
    "grading-pause": ("grading", lambda: setattr(W, "pause_on", ("grading", 5)), "pause_file"),
    "grading-429": ("grading", lambda: W.gw_429.add("grading"), "rate_limit"),
    "grading-failures": ("grading", lambda: W.gw_500.add("grading"), "failures_5_in_a_row"),
    "retest-pause": ("retest", lambda: setattr(W, "pause_on", ("retest", 2)), "pause_file"),
    "retest-429": ("retest", lambda: (W.gw_429.add("retest"), setattr(W, "gw_429_after", 1)), "rate_limit"),
}


@pytest.fixture
def record_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Record the mock log index at the run's first stop decision (``Control.stop``)."""
    orig = p4.Control.stop

    def stop(self: p4.Control, reason: str) -> None:
        if W.stop_idx is None:
            W.stop_idx = len(W.log)
        orig(self, reason)

    monkeypatch.setattr(p4.Control, "stop", stop)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_pause_and_identical_resume(name: str, cfg: Path, reference: tuple[Path, Any], tmp_path: Path,
                                    record_stop: None) -> None:
    step, setup, reason = SCENARIOS[name]
    out = prep_out(tmp_path / "run")
    W.reset(out)
    setup()
    assert run(cfg, out) == p4.EXIT_PAUSED
    st = json.loads((out / "part4-state.json").read_text())
    assert st["status"] == "paused" and st["step"] == step and st["reason"].startswith(reason)
    assert st["commit"] == "c0ffee" and st["config_sha256"] == p4.file_sha(cfg)
    assert st["counts"][step]["remaining"] is None or st["counts"][step]["remaining"] >= 0
    assert set(st["totals"]) == {"since_start", "since_resume"}
    assert not (out / "results-part4.json").exists()
    # Nothing new is issued once the stop fires: requests after it are only the ones in flight.
    if step == "retrieval":
        assert W.ret_idx is not None and [x for x in W.log[W.ret_idx:] if x[0] != "es"] == []
    else:
        idx = W.fire_idx if W.fire_idx is not None else W.stop_idx
        assert idx is not None
        after = Counter(x[0] for x in W.log[idx:] if x[0] != "es")
        assert all(n <= 4 for n in after.values()), (idx, after)
    lines_before = {f.name: f.read_text() for f in out.glob("*.jsonl")}
    W.reset(out, keep_calls=True)  # the mock server lives on across the pause
    (out / "PAUSE").unlink(missing_ok=True)
    assert run(cfg, out, resume=True) == p4.EXIT_OK
    for fn, before in lines_before.items():  # append-only
        assert (out / fn).read_text().startswith(before)
    assert comparable(out) == comparable(reference[0])
    r = json.loads((out / "results-part4.json").read_text())
    assert [s["end"] for s in r["run_segments"]] == ["paused", "complete"]
    assert any(s["end"] == "paused" and s["reason"] == st["reason"] for s in r["run_segments"])


def test_resume_reissues_only_missing_and_keeps_given_up_empties(cfg: Path, reference: Any, tmp_path: Path) -> None:
    out = prep_out(tmp_path / "run")
    W.reset(out)
    W.pause_on = ("retest", 1)
    assert run(cfg, out) == p4.EXIT_PAUSED
    graded = [json.loads(x)["k"] for x in (out / "part4-grades-ga.jsonl").read_text().splitlines()]
    W.reset(out, keep_calls=True)
    (out / "PAUSE").unlink()
    assert run(cfg, out, resume=True) == p4.EXIT_OK
    assert W.count["grading"] == 0 and W.count["generation"] == 0  # nothing re-issued, no_label not retried
    assert [json.loads(x)["k"] for x in (out / "part4-grades-ga.jsonl").read_text().splitlines()] == graded


def test_batch_budget_pause_then_resume(cfg: Path, reference: Any, tmp_path: Path) -> None:
    out = prep_out(tmp_path / "run")
    W.reset(out)
    assert run(cfg, out, batch={"batch_calls": 3}) == p4.EXIT_PAUSED
    st = json.loads((out / "part4-state.json").read_text())
    assert st["reason"] == "batch_calls" and st["totals"]["since_resume"]["gateway_calls"] == 3
    W.reset(out)
    assert run(cfg, out, resume=True, batch={"batch_calls": 10_000}) == p4.EXIT_OK
    assert comparable(out) == comparable(reference[0])
    st = json.loads((out / "part4-state.json").read_text())
    assert st["totals"]["since_start"]["gateway_calls"] > st["totals"]["since_resume"]["gateway_calls"]


def test_output_token_cap_pauses(tmp_path: Path) -> None:
    out = prep_out(tmp_path / "run")
    W.reset(out)
    c = make_config(tmp_path / "c.json", limits={"output_token_cap": 50, "empty_backoff_s": 0.001})
    assert run(c, out) == p4.EXIT_PAUSED
    assert json.loads((out / "part4-state.json").read_text())["reason"] == "output_token_cap"
    W.reset(out, keep_calls=True)
    assert run(c, out, resume=True) == p4.EXIT_PAUSED  # the cap counts since the start, across segments
    st = json.loads((out / "part4-state.json").read_text())
    assert st["reason"] == "output_token_cap"
    since_resume = st["totals"]["since_resume"].get("gateway_completion_tokens", 0)
    since_start = st["totals"]["since_start"]["gateway_completion_tokens"]
    assert since_resume < 50 <= since_start


def test_resume_refuses_on_sha_mismatch(tmp_path: Path) -> None:
    out = prep_out(tmp_path / "run")
    c = make_config(tmp_path / "c.json")
    W.reset(out)
    W.pause_on = ("readers", 5)
    assert run(c, out) == p4.EXIT_PAUSED
    (out / "PAUSE").unlink()
    state = (out / "part4-state.json").read_text()
    c.write_text(c.read_text() + "\n")                       # config sha
    assert run(c, out, resume=True) == p4.EXIT_HARD_STOP
    c.write_text(c.read_text()[:-1])
    (out / "PREREG-synthetic.md").write_text("edited\n")     # prereg sha
    assert run(c, out, resume=True) == p4.EXIT_HARD_STOP
    (out / "PREREG-synthetic.md").write_text("synthetic prereg v1\n")
    W.commit = "deadbeef"                                    # commit
    assert run(c, out, resume=True) == p4.EXIT_HARD_STOP
    assert (out / "part4-state.json").read_text() == state   # a refused resume changes nothing
    W.commit = "c0ffee"
    n0 = len(W.log)
    assert run(c, out) == p4.EXIT_HARD_STOP and len(W.log) == n0  # a fresh start over a state file
    assert run(c, out, resume=True) == p4.EXIT_OK


def test_frozen_prereg_sha_is_checked(tmp_path: Path) -> None:
    out = prep_out(tmp_path / "run")
    W.reset(out)
    c = make_config(tmp_path / "c.json", files={"prereg_sha256": "0" * 64})
    assert run(c, out) == p4.EXIT_HARD_STOP and W.log == []


def test_manipulation_gate_hard_stops_before_any_call(tmp_path: Path) -> None:
    out = prep_out(tmp_path / "run")
    W.reset(out)
    exp = expected_counts()
    exp["T512o0"] += 1
    c = make_config(tmp_path / "c.json", expected_chunks=exp)
    assert run(c, out) == p4.EXIT_HARD_STOP
    assert [x for x in W.log if x[0] != "es"] == []
    st = json.loads((out / "part4-state.json").read_text())
    assert st["status"] == "hard_stop" and st["reason"] == "manipulation_check_mismatch:T512o0"
    assert run(c, out, resume=True) == p4.EXIT_HARD_STOP


def test_m_census_gate_hard_stops_before_any_call(tmp_path: Path) -> None:
    out = prep_out(tmp_path / "run")
    W.reset(out)
    c = make_config(tmp_path / "c.json", expected_m_pages=2)   # the synthetic census is 1 page (d0)
    assert run(c, out) == p4.EXIT_HARD_STOP
    assert [x for x in W.log if x[0] != "es"] == []
    st = json.loads((out / "part4-state.json").read_text())
    assert st["status"] == "hard_stop" and st["reason"] == "manipulation_check_mismatch:M_pages"
    base = json.loads(make_config(tmp_path / "ok.json").read_text())
    for bad in (None, "1", 1.0, True):
        with pytest.raises(SystemExit, match="expected_m_pages"):
            p4.part4_config({**base, "part4": {**base["part4"], "expected_m_pages": bad}})


def test_reader_failures_over_share_hard_stop(tmp_path: Path) -> None:
    out = prep_out(tmp_path / "run")
    W.reset(out)
    W.reader_500 = True
    c = make_config(tmp_path / "c.json")
    assert run(c, out) == p4.EXIT_HARD_STOP
    st = json.loads((out / "part4-state.json").read_text())
    assert st["reason"].startswith("reader_failures:") and st["step"] == "readers"
    assert W.count["grading"] == 0


def force_part_b_discordance(monkeypatch: pytest.MonkeyPatch, missing: str | None = None,
                             missing_qs: frozenset[str] = frozenset()) -> None:
    """The reference run has no discordant part-B pair: label the part-B x-arm answer
    correct and the y-arm answer incorrect (primary grader), so pairs exist. With
    ``missing``, the y-arm answer of ``missing_qs`` gets that MISSING label instead and
    the secondary grader's label of ``missing_qs`` is missing too."""
    orig = p4.Run.label

    def label(self: Any, tag: str, q: str, answer: str) -> str | None:
        h = self.p4["human"]
        ans = {arm: self.ans.rows[p4.ckey("readers", q, arm, h["part_b_condition"], h["part_b_reader"])]["answer"]
               for arm in h["part_b_arms"]}
        if missing and q in missing_qs and tag == self.p4["grader_secondary"]["tag"]:
            return missing
        if tag == self.p4["grader_primary"]["tag"] and ans:
            x_arm, y_arm = h["part_b_arms"]
            if answer == ans[y_arm]:
                return missing if (missing and q in missing_qs) else "incorrect"
            if answer == ans[x_arm]:
                return "correct"
        return orig(self, tag, q, answer)

    monkeypatch.setattr(p4.Run, "label", label)


def test_human_export_is_private_and_blinded(cfg: Path, reference: Any, tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    force_part_b_discordance(monkeypatch)
    out = tmp_path / "copy"                       # a copy: the patched labels must not touch the shared reference
    shutil.copytree(reference[0], out)
    W.reset(out)
    with pytest.raises(SystemExit, match="inside the repository"):
        run(cfg, out, resume=True, export_human=Path(__file__).resolve().parent / "human.json")
    dest = tmp_path / "human.json"
    assert run(cfg, out, resume=True, export_human=dest) == p4.EXIT_OK
    x = json.loads(dest.read_text())
    kx = json.loads((tmp_path / "human.key.json").read_text())
    assert x["items"] and set(kx["key"]) == {i["item"] for i in x["items"]}
    assert set(x) == {"labels", "items", "note"}                  # the items file has no key block
    for it in x["items"]:
        assert set(it) == {"item", "question", "reference", "answer"}
    # no arm / condition / reader / grade information anywhere in the items file
    text = dest.read_text()
    for token in ('"arm"', '"condition"', '"reader"', '"part"', '"ga"', '"gb"', "T512o0", "T128o0", "T1024o0",
                  "SEM512", "B4096"):
        assert token not in text, token
    assert {k["part"] for k in kx["key"].values()} <= {"A", "B"}
    assert W.count["grading"] == 0 and W.count["readers"] == 0


def test_human_export_is_shuffled_and_deterministic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    force_part_b_discordance(monkeypatch)
    out = prep_out(tmp_path / "run")
    W.reset(out)
    c = make_config(tmp_path / "c.json", human={"part_b_set": "S", "part_b_pairs": 4})   # S: several pairs
    assert run(c, out) == p4.EXIT_OK
    d1, d2 = tmp_path / "one" / "h.json", tmp_path / "two" / "h.json"
    for d in (d1, d2):
        d.parent.mkdir()
        assert run(c, out, resume=True, export_human=d) == p4.EXIT_OK
    assert d1.read_text() == d2.read_text()                       # deterministic under the seed
    key = json.loads(d1.with_name("h.key.json").read_text())["key"]
    order = [key[i["item"]] for i in json.loads(d1.read_text())["items"]]
    parts = [k["part"] for k in order]
    assert parts.count("A") >= 2 and parts.count("B") >= 4
    assert parts != sorted(parts) and parts != sorted(parts, reverse=True)   # not A..A then B..B
    pos: dict[str, list[int]] = {}
    for n, k in enumerate(order):
        if k["part"] == "B":
            pos.setdefault(k["record_id"], []).append(n)
    assert len(pos) >= 2 and all(len(v) == 2 for v in pos.values())
    assert not all(v[1] - v[0] == 1 for v in pos.values())        # pair members are not all adjacent


@pytest.mark.parametrize("missing", sorted(p4.MISSING))
def test_human_export_drops_every_missing_label(cfg: Path, reference: Any, tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    """A MISSING label (no_label, unparseable, reader_failed) on either side is never a
    part-A disagreement and never part of a part-B discordant pair."""
    out = tmp_path / "copy"                       # a copy: the patched labels must not touch the shared reference
    shutil.copytree(reference[0], out)
    W.reset(out)
    with monkeypatch.context() as m:
        force_part_b_discordance(m)
        base = tmp_path / "base.json"
        assert run(cfg, out, resume=True, export_human=base) == p4.EXIT_OK
    key = json.loads(base.with_name("base.key.json").read_text())["key"].values()
    a_ids = frozenset(k["record_id"] for k in key if k["part"] == "A")
    b_ids = frozenset(k["record_id"] for k in key if k["part"] == "B")
    assert a_ids and b_ids
    force_part_b_discordance(monkeypatch, missing, a_ids | b_ids)
    W.reset(out)
    dest = tmp_path / "dropped.json"
    assert run(cfg, out, resume=True, export_human=dest) == p4.EXIT_OK
    kept = json.loads(dest.with_name("dropped.key.json").read_text())["key"].values()
    assert not a_ids & {k["record_id"] for k in kept if k["part"] == "A"}
    assert not b_ids & {k["record_id"] for k in kept if k["part"] == "B"}


def test_cli_wires_part4(monkeypatch: pytest.MonkeyPatch, cfg: Path, tmp_path: Path) -> None:
    got = {}
    monkeypatch.setattr(p4, "cmd_part4", lambda a: got.update(vars(a)) or 75)
    assert dt.main(["--private-config", str(cfg), "--out-dir", str(tmp_path), "part4", "--resume",
                    "--batch-calls", "7"]) == 75
    assert got["resume"] and got["batch_calls"] == 7 and got["cmd"] == "part4"


# ------------------------------------------------------------------ review fixes


def test_failure_total_counts_across_segments(tmp_path: Path) -> None:
    """"More than 20 failures in total" is per run: the second segment pauses on the 21st
    failure of the run, not of the segment. (Every 3rd gateway request fails, so never 5
    in a row; 20 passes so the run keeps re-issuing the failed calls.)"""
    out = prep_out(tmp_path / "run")
    c = make_config(tmp_path / "c.json", gateway={"passes": 20})

    def failures(st: dict[str, Any], which: str) -> int:
        return sum(v for k, v in st["totals"][which].items() if k.startswith("gateway_failures:"))

    W.reset(out)
    W.gw_fail_every, W.pause_on = 3, ("gateway", 36)
    assert run(c, out) == p4.EXIT_PAUSED
    st1 = json.loads((out / "part4-state.json").read_text())
    f1 = failures(st1, "since_start")
    assert st1["reason"] == "pause_file" and 10 <= f1 <= 20
    (out / "PAUSE").unlink()
    W.reset(out, keep_calls=True)
    W.gw_fail_every = 3
    assert run(c, out, resume=True) == p4.EXIT_PAUSED
    st2 = json.loads((out / "part4-state.json").read_text())
    assert st2["reason"] == "failures_over_20"
    assert 21 <= failures(st2, "since_start") <= 24          # + at most the in-flight ones
    assert failures(st2, "since_resume") == failures(st2, "since_start") - f1 < 21
    W.reset(out, keep_calls=True)                             # the gateway recovers
    assert run(c, out, resume=True) == p4.EXIT_OK


def test_truncated_empty_is_no_label_not_a_failure(cfg: Path, tmp_path: Path) -> None:
    import asyncio

    import httpx

    resp: dict[str, Any] = {}
    seen: list[str] = []

    def handler(path: str, body: Any) -> tuple[int, Any]:
        seen.append(body["model"])
        return 200, resp["r"]

    mock = Mock(handler)
    dt.load_private_config(str(cfg))
    lim = p4.part4_config(json.loads(cfg.read_text()))["limits"]
    control = p4.Control(lim, tmp_path / "PAUSE", None)
    gw = p4.Gateway(mock.url, control, lim, tmp_path / "calls.jsonl")
    spec = {"model": "m", "max_tokens": 512, "temperature": None}
    msgs = [{"role": "user", "content": "x"}]

    async def go() -> list[dict[str, Any] | None]:
        async with httpx.AsyncClient() as http:
            return [await gw.standard(http, spec, msgs, "grading", "k1"),
                    await gw.generate(http, spec, msgs, "generation", "k2", 0.001)]

    resp["r"] = chat("", ct=512, finish="length")
    std, gen = asyncio.run(go())
    for res in (std, gen):
        assert res is not None and res["status"] == "no_label" and res["reason"] == "truncated"
        assert res["attempts"] == 1
    assert len(seen) == 2                                     # not retried
    assert control.fail_total == 0 and not [k for k in control.seg if k.startswith("gateway_failures")]
    row = p4.grade_row("k1", std)
    assert (row["v"], row["rule"], row["ct"]) == ("no_label", "truncated", 512)
    resp["r"] = chat("", ct=7, finish="stop")                 # an empty with tokens: still a failure
    std, _ = asyncio.run(go())
    assert std is None and control.fail_total == 1
    mock.srv.shutdown()
    # A truncated generator answer goes to the fallback, as an empty one does.
    st = p4.Store(tmp_path / "g.jsonl")
    pages, ptok = synth_pages(cfg)
    conf = p4.part4_config(json.loads(cfg.read_text()))
    text = pages["d0"]["text"]
    it = {"id": "S000p0", "set": "S", "doc_id": "d0", "p": [0, 300]}
    st.put({"k": p4.ckey("generation", it["id"], role="gen"), "status": "no_label", "reason": "truncated"})
    st.put({"k": p4.ckey("generation", it["id"], role="fb"), "status": "ok",
            "text": json.dumps({"question": "Q?", "answer_quote": full_sentence(text[:300])})})
    qs, _ = p4.build_questions([it], st, pages, ptok, conf)
    assert qs[0]["status"] == "valid" and qs[0]["gen"] == "fb"


def test_secondary_grader_truncations_in_the_pipeline(reference: Any) -> None:
    out = reference[0]
    rows = [json.loads(x) for x in (out / "part4-grades-gb.jsonl").read_text().splitlines()]
    trunc = [g for g in rows if g["rule"] == "truncated"]
    assert trunc and all(g["v"] == "no_label" and g["attempts"] == 1 and g["finish"] == "length" for g in trunc)
    st = json.loads((out / "part4-state.json").read_text())
    assert not [k for k in st["totals"]["since_start"] if k.startswith("gateway_failures")]
    assert p4.DEFAULTS["grader_secondary"]["max_tokens"] == 512


def test_temperature_is_dropped_after_a_400(tmp_path: Path, reference: Any) -> None:
    """A no-temperature model configured with one: the mock answers 400 mentioning
    temperature; the run drops it for that model and completes with the same grades."""
    out = prep_out(tmp_path / "run")
    W.reset(out)
    c = make_config(tmp_path / "c.json", grader_primary={"temperature": 0})
    assert run(c, out) == p4.EXIT_OK
    g1 = [b for b in W.bodies if b["model"] == G1_MODEL]
    with_t = [i for i, b in enumerate(g1) if "temperature" in b]
    assert 1 <= len(with_t) <= 4 and max(with_t) < 4         # only the first in-flight ones carried it
    r = json.loads((out / "results-part4.json").read_text())
    assert r["design"]["temperature_dropped"] == [G1_MODEL]
    ref = json.loads((reference[0] / "results-part4.json").read_text())
    assert r["per_item"] == ref["per_item"] and r["analysis"] == ref["analysis"]


def synth_valid(sizes: dict[str, int]) -> list[dict[str, Any]]:
    out = []
    for key, n in sizes.items():
        set_, stratum = key.split("|")
        out += [{"record_id": f"{set_}{stratum}{i:03d}", "set": set_, "stratum": stratum} for i in range(n)]
    return out


def test_subsets_are_stratified_with_largest_remainder_quotas(cfg: Path) -> None:
    conf = p4.part4_config(json.loads(cfg.read_text()))
    conf["grader_primary"]["k10_fraction"], conf["grader_secondary"]["fraction"] = 0.5, 0.25
    valid = synth_valid({"M|alpha": 37, "M|beta": 11, "S|alpha": 23, "S|beta": 9})   # n = 80
    ss = p4.draw_subsets(valid, conf)
    stratum = {r["record_id"]: f"{r['set']}|{r['stratum']}" for r in valid}
    # By hand. k10: 40 of 80 -> 18.5, 5.5, 11.5, 4.5 -> floors 18+5+11+4 = 38; the two
    # extra go to the largest remainders (all .5; ties by name: M|alpha, M|beta).
    assert Counter(stratum[q] for q in ss["primary_k10"]) == {"M|alpha": 19, "M|beta": 6, "S|alpha": 11, "S|beta": 4}
    # secondary: 20 of 80 -> 9.25, 2.75, 5.75, 2.25 -> floors 18; +1 to M|beta and S|alpha.
    assert Counter(stratum[q] for q in ss["secondary"]) == {"M|alpha": 9, "M|beta": 3, "S|alpha": 6, "S|beta": 2}


def test_derived_seeds_are_reproducible(cfg: Path) -> None:
    conf = p4.part4_config(json.loads(cfg.read_text()))
    valid = synth_valid({"M|alpha": 37, "M|beta": 11, "S|alpha": 23, "S|beta": 9})
    a, b = p4.draw_subsets(valid, conf), p4.draw_subsets(list(reversed(valid)), conf)
    assert a == b
    by: dict[str, list[str]] = {}
    for r in valid:
        by.setdefault(f"{r['set']}|{r['stratum']}", []).append(r["record_id"])
    seed = conf["seed"]
    n_k10 = int(round(80 * conf["grader_primary"]["k10_fraction"]))
    n_sec = int(round(80 * conf["grader_secondary"]["fraction"]))
    assert a["primary_k10"] == dt.strat_sample(by, n_k10, f"{seed}|k10")
    assert a["secondary"] == dt.strat_sample(by, n_sec, f"{seed}|secondary")
    assert a["primary_k10"] != dt.strat_sample(by, n_k10, seed)        # not the bare seed
    keys = [f"k{i:03d}" for i in range(50)]
    rt = p4.retest_sample(keys, 7, seed)
    assert rt == p4.retest_sample(list(reversed(keys)), 7, seed)
    assert rt == sorted(random.Random(f"{seed}|retest").sample(keys, 7))
    assert rt != sorted(random.Random(seed).sample(keys, 7))


def test_k10_coverage_requests_equal_the_dedup_need(reference: Any) -> None:
    """Primary-grader requests = the dedup need within coverage (every B answer, k10
    answers of the k10 subset only), one call per key plus the 0-token retries."""
    out, reqs, _ = reference
    r = json.loads((out / "results-part4.json").read_text())
    k10s = set(r["subsets"]["primary_k10"])
    need = set()
    for x in (json.loads(y) for y in (out / "part4-answers.jsonl").read_text().splitlines()):
        _, q, _, cond, _ = x["k"].split("|")
        if x["ok"] and (cond != "k10" or q in k10s):
            need.add(p4.answer_key(q, x["answer"]))
    outside = {p4.answer_key(q, x["answer"]) for x in (json.loads(y) for y in
               (out / "part4-answers.jsonl").read_text().splitlines())
               for _, q, _, cond, _ in [x["k"].split("|")] if x["ok"] and cond == "k10" and q not in k10s}
    assert outside - need                                   # non-vacuous: some k10 answers must stay ungraded
    rows = [json.loads(y) for y in (out / "part4-grades-ga.jsonl").read_text().splitlines()]
    assert {g["k"].split("|")[2] for g in rows} == need and len(rows) == len(need)
    g1 = sum(1 for e, m, step, _ in reqs if e == "gateway" and m == G1_MODEL and step == "grading")
    assert g1 == sum(g["attempts"] for g in rows)
    assert len(k10s) < len(r["questions"])                  # the subset really restricts k10


def test_generator_retry_is_counted_from_the_requests(cfg: Path, reference: Any) -> None:
    out, reqs, _ = reference
    dt.load_private_config(str(cfg))
    pages, _ = dt.pages_from_index(es_rows())
    recs = {x["record_id"]: x for x in (json.loads(y) for y in (out / "part4-questions.jsonl").read_text().splitlines())}
    gen_rows = [json.loads(x) for x in (out / "part4-gen-calls.jsonl").read_text().splitlines()]
    empties = [x for x in gen_rows if x["k"].endswith("|gen") and x["status"] == "empty"]
    assert empties
    per_prompt = Counter(ph for e, m, step, ph in reqs if e == "gateway" and m == GEN_MODEL)
    for x in empties:
        rec = recs[x["k"].split("|")[1]]
        it = {"set": rec["set"], **{w: rec[w] for w in ("a", "b", "p") if w in rec}}
        user = p4.gen_messages(it, pages[rec["doc_id"]]["text"])[-1]["content"]
        assert per_prompt[phash(user)] == 2 == x["attempts"]


def test_torn_trailing_line_is_dropped_and_repaired(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = tmp_path / "s.jsonl"
    f.write_text('{"k": "a", "v": 1}\n{"k": "b", "v": 2}\n{"k": "c", "v"')
    st = p4.Store(f)
    assert sorted(st.rows) == ["a", "b"] and "torn trailing line" in capsys.readouterr().err
    assert f.read_text() == '{"k": "a", "v": 1}\n{"k": "b", "v": 2}\n'
    assert (tmp_path / "s.jsonl.torn").read_text() == '{"k": "c", "v"\n'
    st.put({"k": "c", "v": 3})
    assert sorted(p4.Store(f).rows) == ["a", "b", "c"]
    f.write_text('{"k": "a"}\n{"k": "b"}')                 # complete, only the newline is missing
    assert sorted(p4.Store(f).rows) == ["a", "b"] and f.read_text().endswith('{"k": "b"}\n')
    f.write_text('{"k": "a"}\n{"k": \n{"k": "b"}\n')       # not a tail: refuse
    with pytest.raises(SystemExit, match="line 2"):
        p4.Store(f)


def test_resume_over_a_torn_answer_line(cfg: Path, reference: Any, tmp_path: Path) -> None:
    out = prep_out(tmp_path / "run")
    W.reset(out)
    W.pause_on = ("readers", 20)
    assert run(cfg, out) == p4.EXIT_PAUSED
    with (out / "part4-answers.jsonl").open("a") as fh:
        fh.write('{"k": "readers|M000k0|L|B4096|scout", "answer": "It is')   # a write cut short
    (out / "PAUSE").unlink()
    W.reset(out, keep_calls=True)
    assert run(cfg, out, resume=True) == p4.EXIT_OK
    assert comparable(out) == comparable(reference[0])


def test_crash_is_recorded_and_resumable(cfg: Path, reference: Any, tmp_path: Path,
                                         capsys: pytest.CaptureFixture[str]) -> None:
    out = prep_out(tmp_path / "run")
    W.reset(out)
    W.ret_crash = True
    assert run(cfg, out) == p4.EXIT_HARD_STOP
    st = json.loads((out / "part4-state.json").read_text())
    assert st["status"] == "crashed" and st["step"] == "retrieval"
    assert st["reason"].startswith("RuntimeError: synthetic") and st["totals"]["since_start"]["gateway_calls"] > 0
    assert "GATEWAY-NOTICE part4: CRASHED at step retrieval" in capsys.readouterr().out
    W.reset(out, keep_calls=True)
    W.commit = "deadbeef"
    assert run(cfg, out, resume=True) == p4.EXIT_HARD_STOP     # the sha checks still apply
    W.commit = "c0ffee"
    assert run(cfg, out, resume=True) == p4.EXIT_OK
    assert comparable(out) == comparable(reference[0])
    r = json.loads((out / "results-part4.json").read_text())
    assert [s["end"] for s in r["run_segments"]] == ["crashed", "complete"]


# ------------------------------------------------------------------ the analysis

ANALYSIS_ARMS = ["L", "SEM512", "T128o0", "T512o0", "T1024o0"]


def fixture_items(outcomes: dict[str, dict[str, list[int]]], labels: dict[tuple[str, str, int], str] | None = None,
                  gen: dict[int, str] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """One question per page (page d<i>), set M, condition B4096; ``outcomes[reader][arm][i]``
    1 = correct, 0 = incorrect; ``labels`` overrides a (reader, arm, i) label."""
    n = len(next(iter(next(iter(outcomes.values())).values())))
    per_item: dict[str, Any] = {}
    questions: dict[str, Any] = {}
    for i in range(n):
        q = f"M{i:02d}"
        questions[q] = {"set": "M", "stratum": "alpha" if i % 2 else "beta", "doc_id": f"d{i}",
                        "gen": (gen or {}).get(i, "gen")}
        per_item[q] = {}
        for arm in ANALYSIS_ARMS:
            cell = {}
            for rd, arms in outcomes.items():
                lab = (labels or {}).get((rd, arm, i)) or ("correct" if arms[arm][i] else "incorrect")
                cell[rd] = {"ga": lab, "gb": None}
            per_item[q][arm] = {"B4096": {"found": bool(outcomes["r1"][arm][i]), "readers": cell}}
    return per_item, questions


def analyse(per_item: dict[str, Any], questions: dict[str, Any], procedure: str, **over: Any) -> dict[str, Any]:
    an = {**p4.DEFAULTS["analysis"], "procedure": procedure, "n_perm": 999, "n_boot": 200, **over}
    return p4.analyse_part4(per_item, questions, conditions=["B4096"], readers=["r1", "r2"], primary="ga",
                            secondary="gb", analysis=an, seed=261025, fallback_tag="fb", arms=ANALYSIS_ARMS)


ONES, N_PAGES = [1] * 8, 8


def zeros_at(*idx: int) -> list[int]:
    return [0 if i in idx else 1 for i in range(N_PAGES)]


#: Fixture A. r1: T128 wrong on 7 pages (H1: 7 pages at +1, p = 1/2^7); H2-H4 d = 0
#: everywhere, so every shifted page sum is +0.075 > 0 and p = 1/2^8 (only the
#: all-plus flip reaches T). r2: T128 wrong on 3 pages (H1 p = 1/8); SEM512 wrong on
#: 3 pages (H2: 3 pages at -0.925, 5 at +0.075: T = -2.4, p > 0.5).
FIX_A = {"r1": {"T512o0": ONES, "T128o0": zeros_at(0, 1, 2, 3, 4, 5, 6), "L": ONES, "SEM512": ONES, "T1024o0": ONES},
         "r2": {"T512o0": ONES, "T128o0": zeros_at(0, 1, 2), "L": ONES, "SEM512": zeros_at(0, 1, 2), "T1024o0": ONES}}
#: Fixture B (both readers): pages 0-2 T512 = L = T128 = 0, SEM512 = T1024 = 1; pages 3-7
#: all 1 but T128 = 0. H1: 5 pages at +1 (p = 1/32); H2: pages 0-2 at +1 (p = 1/256);
#: H3: pages 0-2 at -1 (fails); H4: d = 0 everywhere (p = 1/256).
_B = {"T512o0": zeros_at(0, 1, 2), "T128o0": [0] * 8, "L": zeros_at(0, 1, 2), "SEM512": ONES, "T1024o0": ONES}
FIX_B = {"r1": _B, "r2": _B}


def pr(res: dict[str, Any], block: str = "family") -> dict[str, dict[str, Any]]:
    return {h: {rd: v["per_reader"][rd] for rd in ("r1", "r2")} for h, v in res[block]["hypotheses"].items()}


def status(res: dict[str, Any], block: str = "family") -> dict[str, str]:
    return {h: v["status"] for h, v in res[block]["hypotheses"].items()}


def test_analysis_holm4_hand_checked() -> None:
    res = analyse(*fixture_items(FIX_A), "holm4")
    p = pr(res)
    assert p["H1"]["r1"]["p"] == 1 / 128 and p["H1"]["r2"]["p"] == 1 / 8
    for h in ("H2", "H3", "H4"):
        assert p[h]["r1"]["p"] == 1 / 256
    assert p["H3"]["r2"]["p"] == p["H4"]["r2"]["p"] == 1 / 256 and p["H2"]["r2"]["p"] > 0.5
    # Holm per reader: r1 rejects all four (1/256 x3 <= .0125, .0167, .025; 1/128 <= .05);
    # r2: 1/256, 1/256 pass; then 1/8 > .025 stops.
    assert [p[h]["r1"]["significant"] for h in ("H1", "H2", "H3", "H4")] == [True] * 4
    assert [p[h]["r2"]["significant"] for h in ("H1", "H2", "H3", "H4")] == [False, False, True, True]
    assert status(res) == {"H1": "reader-dependent", "H2": "reader-dependent", "H3": "confirmed", "H4": "confirmed"}
    # Holm-adjusted, by hand: max over the earlier steps of (m - r + 1) p_(r) = 4/256 for all four.
    assert [p[h]["r1"]["p_holm"] for h in ("H1", "H2", "H3", "H4")] == [1 / 64] * 4
    assert p["H1"]["r1"]["gained"] == 7 and p["H1"]["r1"]["lost"] == 0 and p["H1"]["r1"]["n"] == 8
    assert p["H1"]["r1"]["p_mcnemar"] == 1 / 128                   # 7 of 7 discordant, one-sided
    # the CI level is the config's (0.90), not a hard-coded 0.95: 0.95 gives a wider interval
    assert p4.DEFAULTS["analysis"]["ci_level"] == 0.9
    wide = pr(analyse(*fixture_items(FIX_A), "holm4", ci_level=0.95))
    for h in ("H1", "H2", "H3", "H4"):
        for rd in ("r1", "r2"):
            assert p[h][rd]["ci_level"] == 0.9 and wide[h][rd]["ci_level"] == 0.95
    lo, hi = p["H1"]["r2"]["ci"]                                 # (r1's interval is degenerate at the bound)
    wlo, whi = wide["H1"]["r2"]["ci"]
    assert whi - wlo > hi - lo
    res_b = analyse(*fixture_items(FIX_B), "holm4")
    assert pr(res_b)["H1"]["r1"]["p"] == 1 / 32                    # .03125 > .025 at its Holm step
    assert status(res_b) == {"H1": "not confirmed", "H2": "confirmed", "H3": "not shown non-inferior",
                             "H4": "confirmed"}


def test_analysis_fixed_sequence_hand_checked() -> None:
    res = analyse(*fixture_items(FIX_A), "fixed_sequence")
    assert res["order"] == ["H4", "H2", "H3", "H1"]
    # H4 passes under both readers; H2 only under r1 -> reader-dependent, the sequence stops.
    assert status(res) == {"H4": "confirmed", "H2": "reader-dependent", "H3": "not reached", "H1": "not reached"}
    assert pr(res)["H3"]["r1"]["significant"] is None and pr(res)["H3"]["r1"]["p"] == 1 / 256  # reported, not tested
    res_b = analyse(*fixture_items(FIX_B), "fixed_sequence")
    # H3 fails under both readers: "not shown non-inferior"; H1 (p = 1/32 < .05) is never reached.
    assert status(res_b) == {"H4": "confirmed", "H2": "confirmed", "H3": "not shown non-inferior",
                             "H1": "not reached"}
    assert [res_b["family"]["hypotheses"][h]["sequence_step"] for h in ("H4", "H2", "H3", "H1")] == [1, 2, 3, 4]


def test_mid_p_is_reported_never_decisive() -> None:
    """FIX_B H1: 5 pages at +1, so p = 1/32 (> its Holm step .025: not confirmed) but
    p_mid = 1/64 (<= .025). Mid-p confirms; the decision, built from the standard p, does not."""
    res = analyse(*fixture_items(FIX_B), "holm4")
    h1 = res["family"]["hypotheses"]["H1"]
    for rd in ("r1", "r2"):
        assert h1["per_reader"][rd]["p"] == 1 / 32 and h1["per_reader"][rd]["p_mid"] == 1 / 64
        assert h1["per_reader"][rd]["significant"] is False and h1["per_reader"][rd]["significant_mid"] is True
        assert h1["per_reader"][rd]["holm_mid"] <= 0.05 < h1["per_reader"][rd]["p_holm"]
    assert h1["status"] == "not confirmed" and h1["confirmed"] is False
    assert h1["confirmed_mid"] is True and h1["confirmed_only_under_mid_p"] is True
    h2 = res["family"]["hypotheses"]["H2"]
    assert h2["confirmed"] and h2["confirmed_mid"] and h2["confirmed_only_under_mid_p"] is False
    h3 = res["family"]["hypotheses"]["H3"]
    assert h3["status"] == "not shown non-inferior" and h3["confirmed_mid"] is False
    # the secondary blocks carry p_mid per reader too
    assert "p_mid" in res["secondary"]["per_stratum"]["alpha"]["hypotheses"]["H1"]["per_reader"]["r1"]
    # fixed_sequence: the mid-p sequence is reported separately; decisions stay on p
    fs = analyse(*fixture_items(FIX_B), "fixed_sequence")["family"]["hypotheses"]
    assert {h: v["status"] for h, v in fs.items()} == {"H4": "confirmed", "H2": "confirmed",
                                                       "H3": "not shown non-inferior", "H1": "not reached"}
    assert {h: v["status_mid"] for h, v in fs.items()} == {"H4": "confirmed", "H2": "confirmed",
                                                           "H3": "not shown non-inferior", "H1": "not reached"}
    assert fs["H1"]["confirmed_mid"] is False and fs["H1"]["per_reader"]["r1"]["significant_mid"] is None
    assert fs["H4"]["confirmed"] and fs["H4"]["confirmed_only_under_mid_p"] is False
    assert fs["H2"]["per_reader"]["r1"]["p_mid"] is not None


def test_analysis_missing_labels_gen_fallback_and_secondary_blocks() -> None:
    fx = {rd: {arm: v + [1] for arm, v in arms.items()} for rd, arms in FIX_A.items()}   # a 9th page, all correct
    per_item, questions = fixture_items(fx, labels={("r1", "T512o0", 8): "no_label", ("r1", "L", 6): "unparseable"},
                                     gen={7: "fb"})
    res = analyse(per_item, questions, "holm4")
    p = pr(res)
    assert p["H1"]["r1"]["excluded_missing"] == 1 and p["H1"]["r1"]["n"] == 8 and p["H1"]["r1"]["p"] == 1 / 128
    assert p["H2"]["r1"]["excluded_missing"] == 1 and p["H2"]["r1"]["n"] == 8     # L is H2's arm B
    assert p["H4"]["r1"]["excluded_missing"] == 2 and p["H4"]["r1"]["n"] == 7     # T512 (A) and L (B)
    assert p["H3"]["r1"]["excluded_missing"] == 1 and p["H3"]["r1"]["n"] == 8     # T512 only
    assert p["H2"]["r2"]["excluded_missing"] == 0 and p["H2"]["r2"]["n"] == 9     # the other reader is untouched
    sens = pr(res, "family_missing_as_not_correct")
    assert sens["H1"]["r1"]["n"] == 9 and sens["H1"]["r1"]["lost"] == 1          # scored as not correct: d = -1
    nofb = res["family_without_gen_fallback"]
    assert nofb["n_excluded"] == 1 and nofb["n_questions"] == 8
    sec = res["secondary"]
    assert set(sec) == {"secondary_grader", "correct_or_partial", "abstentions", "per_stratum", "found"}
    assert all(v["per_reader"]["r1"]["n"] == 0 for v in sec["secondary_grader"]["hypotheses"].values())
    assert sec["per_stratum"].keys() == {"alpha", "beta"}
    assert sec["abstentions"]["hypotheses"]["H1"]["per_reader"]["r1"]["alternative"] == "two-sided"
    assert sec["found"]["B4096"]["hypotheses"]["H1"]["p"] == 1 / 128
    assert sec["correct_or_partial"]["hypotheses"]["H1"]["readers"] in ("both", "reader-dependent", "neither")
    json.dumps(res, allow_nan=False)                                              # strict JSON


def test_reference_run_analysis_block(reference: Any) -> None:
    r = json.loads((reference[0] / "results-part4.json").read_text())
    a = r["analysis"]
    assert a["procedure"] == "holm4" and a["family"]["set"] == "M" and a["family"]["condition"] == "B4096"
    assert set(a["family"]["hypotheses"]) == {"H1", "H2", "H3", "H4"}
    assert {"M|B2048", "M|k10", "S|B4096", "secondary_grader"} <= set(a["secondary"])
    k10 = a["secondary"]["M|k10"]["hypotheses"]["H1"]["per_reader"]["scout"]
    m_ids = [q for q, v in r["questions"].items() if v["set"] == "M"]
    assert k10["n"] + k10["excluded_missing"] + k10["outside_coverage"] == len(m_ids)
    assert k10["outside_coverage"] == sum(q not in r["subsets"]["primary_k10"] for q in m_ids)


def test_power_table_shape_and_seed() -> None:
    t = p4.power_table(n_sims=40, n_flips=99)
    assert len(t["rows"]) == 2 * 2 * 4 * 6 and t == p4.power_table(n_sims=40, n_flips=99)
    assert sum(t["questions_per_page"]) == 315 and len(t["questions_per_page"]) == 76
    holm = {r["alpha_test"] for r in t["rows"] if r["procedure"] == "holm4"}
    seq = {r["alpha_test"] for r in t["rows"] if r["procedure"] == "fixed_sequence"}
    assert holm == {0.0125} and seq == {0.05}
    ni = [r for r in t["rows"] if r["type"] == "noninferiority"]
    assert {r["ni_true_net"] for r in ni} == {0.0, -0.02, -0.04}
    for r in t["rows"]:
        assert r["power_joint_two_readers"] == round(r["power_per_reader"] ** 2, 4)


# ------------------------------------------------------------------ refactor equivalence
# Frozen copies of the inline code that was moved into shared helpers; the
# helpers must give identical outputs on synthetic inputs.


def _old_walk(lst: list[str], bud: int, by_id: dict[str, C]) -> list[str]:
    tot, keep = 0, []
    for cid in lst:
        tot += by_id[cid].tokens
        if tot > bud:
            break
        keep.append(cid)
    return keep


def _old_p3_sample(pages: dict[str, dict[str, Any]], elig: list[str]) -> tuple[list[dict[str, Any]], Counter]:
    rng = random.Random(dt.P3_SEED)
    items: list[dict[str, Any]] = []
    st: Counter[str] = Counter()
    for did in elig:
        text = pages[did]["text"]
        n = len(text)
        for k in (1, 2):
            a = dt.snap_sentence(text, rng.randint(0, max(0, n - dt.P3_M_CHARS)))
            lo_hi = [(0, a - dt.P3_M_GAP), (a + dt.P3_M_GAP, n - dt.P3_M_CHARS)]
            segs = [(x, y) for x, y in lo_hi if y >= x]
            if not segs:
                st["M_no_room"] += 1
                continue
            tot = sum(y - x + 1 for x, y in segs)
            r = rng.randint(0, tot - 1)
            for x, y in segs:
                if r <= y - x:
                    b_raw = x + r
                    break
                r -= y - x + 1
            b = dt.snap_sentence(text, b_raw)
            if abs(b - a) < dt.P3_M_GAP or (b < a + dt.P3_M_CHARS and a < b + dt.P3_M_CHARS) or b + 1 > n:
                b = b_raw
                st["M_b_unsnapped"] += 1
            items.append({"type": "M", "doc_id": did, "k": k, "a": [a, min(n, a + dt.P3_M_CHARS)],
                          "b": [b, min(n, b + dt.P3_M_CHARS)]})
        lo = int(0.1 * n)
        hi = max(lo, int(0.9 * n) - dt.PASSAGE_CHARS)
        s = dt.snap_sentence(text, rng.randint(lo, hi))
        items.append({"type": "D", "doc_id": did, "p": [s, min(n, s + dt.PASSAGE_CHARS)]})
    return items, st


def _old_reader_prompt(q: str, cids: list[str], limit: int, by_id: dict[str, C], tok: Any,
                       system: str) -> tuple[list[dict[str, str]], int]:
    cids = list(cids)
    dropped = 0
    while True:
        body = "\n\n".join(f"[{n}] {dt.passage(by_id[c], 'rules', False)}" for n, c in enumerate(cids, 1))
        user = f"Documentation excerpts:\n\n{body}\n\nQuestion: {q}"
        if tok(system + user) <= limit - 512 or len(cids) <= 1:
            return [{"role": "system", "content": system}, {"role": "user", "content": user}], dropped
        cids.pop()
        dropped += 1


def _old_validate(kind: str, d: dict[str, Any], text: str, it: dict[str, Any]) -> list[tuple[str, Any]]:
    """The old inline validation of cmd_prepare ("P2") and p3_generate ("D", "M"), as rec items."""
    rec: dict[str, Any] = {}
    if kind == "P2":
        q, quote = d["question"].strip(), d["answer_quote"]
        rec.update(question=q, answer_quote=quote)
        if not q:
            rec["status"] = "empty_question"
        elif len(quote) > dt.MAX_QUOTE_CHARS:
            rec["status"] = "quote_too_long"
        elif not norm(quote) or norm(quote) not in norm(text):
            rec["status"] = "quote_not_in_page"
        else:
            rec["status"] = "valid"
            rec["echo"] = dt.echo(q, text[it["p"][0]:it["p"][1]])
    elif kind == "D":
        q, quote = d["question"].strip(), d["answer_quote"]
        rec.update(question=q, answer_quote=quote)
        if not q:
            rec["status"] = "empty_question"
        elif len(quote) > dt.MAX_QUOTE_CHARS:
            rec["status"] = "quote_too_long"
        elif not norm(quote) or norm(quote) not in norm(text):
            rec["status"] = "quote_not_in_page"
        else:
            rec["echo"] = dt.echo(q, text[it["p"][0]:it["p"][1]])
            rec["status"] = "echo_high" if rec["echo"] >= dt.ECHO_HIGH else "valid"
    else:
        q, qa, qb = d["question"].strip(), d["quote_a"], d["quote_b"]
        rec.update(question=q, quote_a=qa, quote_b=qb)
        nt = norm(text)
        if not q:
            rec["status"] = "empty_question"
        elif len(qa) > dt.MAX_QUOTE_CHARS or len(qb) > dt.MAX_QUOTE_CHARS:
            rec["status"] = "quote_too_long"
        elif not norm(qa) or not norm(qb) or norm(qa) not in nt or norm(qb) not in nt:
            rec["status"] = "quote_not_in_page"
        else:
            ntext, pos = dt.norm_map(text)
            pa = dt.quote_pos(norm(qa), ntext, pos, tuple(it["a"]))
            pb = dt.quote_pos(norm(qb), ntext, pos, tuple(it["b"]))
            if pa is None or pb is None:
                rec["status"] = "quote_position_unresolved"
            else:
                rec["quote_gap"] = abs(pb - pa)
                rec["status"] = "valid" if abs(pb - pa) >= dt.P3_M_GAP else "quotes_too_close"
                rec["echo"] = max(dt.echo(q, text[it["a"][0]:it["a"][1]]), dt.echo(q, text[it["b"][0]:it["b"][1]]))
    return list(rec.items())


def _new_validate(kind: str, d: dict[str, Any], text: str, it: dict[str, Any]) -> list[tuple[str, Any]]:
    """The refactored code paths, verbatim from cmd_prepare / p3_generate."""
    rec: dict[str, Any] = {}
    if kind == "P2":
        q, quote = d["question"].strip(), d["answer_quote"]
        rec.update(question=q, answer_quote=quote)
        rec["status"] = dt.check_single(q, quote, text)
        if rec["status"] == "valid":
            rec["echo"] = dt.echo(q, text[it["p"][0]:it["p"][1]])
    elif kind == "D":
        q, quote = d["question"].strip(), d["answer_quote"]
        rec.update(question=q, answer_quote=quote)
        status = dt.check_single(q, quote, text)
        if status != "valid":
            rec["status"] = status
        else:
            rec["echo"] = dt.echo(q, text[it["p"][0]:it["p"][1]])
            rec["status"] = "echo_high" if rec["echo"] >= dt.ECHO_HIGH else "valid"
    else:
        q, qa, qb = d["question"].strip(), d["quote_a"], d["quote_b"]
        rec.update(question=q, quote_a=qa, quote_b=qb)
        status, gap = dt.check_pair(q, qa, qb, text, tuple(it["a"]), tuple(it["b"]), lambda: dt.norm_map(text),
                                    dt.P3_M_GAP)
        if gap is None:
            rec["status"] = status
        else:
            rec["quote_gap"] = gap
            rec["status"] = status
            rec["echo"] = max(dt.echo(q, text[it["a"][0]:it["a"][1]]), dt.echo(q, text[it["b"][0]:it["b"][1]]))
    return list(rec.items())


def _synthetic_long_pages() -> dict[str, dict[str, Any]]:
    rng = random.Random(11)
    pages = {}
    for d in range(12):
        n = rng.choice([200, 900, 2600, 4000, 7000])
        words = [f"t{d}x{i}" + ("." if rng.random() < 0.15 else "") + ("\n" if rng.random() < 0.03 else "")
                 for i in range(n // 6)]
        pages[f"p{d}"] = {"text": " ".join(words)}
    return pages


def test_equivalence_p3_sample_and_draw_b() -> None:
    pages = _synthetic_long_pages()
    elig = sorted(pages)
    assert dt.p3_sample(pages, elig) == _old_p3_sample(pages, elig)


def test_equivalence_walk_budget() -> None:
    rng = random.Random(5)
    by_id = {f"c{i}": C(f"c{i}", "d", "x", "", "", rng.randint(0, 900)) for i in range(60)}
    for _ in range(200):
        lst = rng.sample(sorted(by_id), rng.randint(0, 60))
        bud = rng.choice([0, 1, 512, 2048, 5120, 10**6])
        assert dt.walk_budget(lst, bud, lambda c: by_id[c].tokens) == _old_walk(lst, bud, by_id)


def test_equivalence_reader_prompt_and_grader_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    rng = random.Random(9)
    by_id = {f"c{i}": C(f"c{i}", "d", " ".join("w" for _ in range(rng.randint(1, 80))), "", "g" * (i % 3))
             for i in range(20)}
    for _ in range(100):
        cids = rng.sample(sorted(by_id), rng.randint(1, 12))
        limit = rng.choice([520, 560, 700, 10_000])
        old = _old_reader_prompt("Q?", cids, limit, by_id, ntok, "SYS")
        msgs, dropped, _ = dt.reader_messages("Q?", [dt.passage(by_id[c], "rules", False) for c in cids], limit,
                                              ntok, "SYS")
        assert (msgs, dropped) == old
    monkeypatch.setattr(dt, "GRADER_USER", GRADER_USER)
    old = [{"role": "user", "content": GRADER_USER.format(question="q", gold="\n".join(["a", "b"]), answer="x")}]
    assert dt.grader_messages("q", ["a", "b"], "x") == old


def test_equivalence_validation() -> None:
    pages = _synthetic_long_pages()
    rng = random.Random(4)
    n_cases = Counter()
    for _did, p in sorted(pages.items()):
        text = p["text"]
        if len(text) < 3500:
            continue
        for _ in range(40):
            a, b = rng.randint(0, 800), rng.randint(1500, len(text) - 1000)
            it = {"p": [a, a + 1200], "a": [a, a + 1000], "b": [b, b + 1000]}

            def pick(lo: int, hi: int, text: str = text) -> str:
                s = rng.randint(lo, hi)
                return text[s:s + rng.choice([0, 20, 80, 320])]

            qa, qb = pick(a, a + 900), rng.choice([pick(b, b + 900), pick(a, a + 900), "nowhere at all", ""])
            for d in ({"question": rng.choice(["", " What? ", "t0x1 t0x2"]), "answer_quote": qa},
                      {"question": "What links them?", "quote_a": qa, "quote_b": qb}):
                kinds = ("P2", "D") if "answer_quote" in d else ("M",)
                for kind in kinds:
                    old, new = _old_validate(kind, d, text, it), _new_validate(kind, d, text, it)
                    assert old == new
                    n_cases[dict(old)["status"]] += 1
    assert {"valid", "quote_too_long", "quote_not_in_page"} <= set(n_cases)


def test_legacy_parsers_unchanged() -> None:
    assert dt.grade_word("The answer is correct") == "unparseable"
    assert dt.grade_word("**Correct** - x") == "correct"


def test_example_config_is_complete_and_valid() -> None:
    ex = json.loads((Path(__file__).resolve().parent / "examples" / "part4-config.example.json").read_text())
    dt.apply_private_config(ex)
    conf = p4.part4_config(ex)
    assert set(ex["part4"]) == set(p4.DEFAULTS)  # every part-4 key is shown in the example
    for k, v in p4.DEFAULTS.items():
        if isinstance(v, dict) and k not in ("conditions",):
            assert set(ex["part4"][k]) == set(v), k
    assert conf["seed"] == 261025 and conf["limits"]["rate_backoffs_s"] == [30.0, 120.0, 600.0]
    with pytest.raises(SystemExit, match="unknown part4 key"):
        p4.part4_config({"part4": {**ex["part4"], "typo": 1}})
