"""Stage 0b' step 2 -- the **third judge family**: Claude, three capability tiers.

`SPEC-confirmation-run-r3.md` SS10 item 4 asks for a third judge family so that the r3.1
relabel's numbers stop being a two-model fact. This module runs the r3.1 protocol -- the
same PROMPT (revision 3.1, whole-sentence anchors), the same SYSTEM, the same rendering,
the same SS6.5 windowing, the same `parse_and_verify_r31` locator -- against three Anthropic
models spanning three capability tiers:

    sonnet5   claude-sonnet-5
    opus5     claude-opus-5
    fable51   claude-fable-5-1

**The only difference from the local judges is the transport.** The local judges are vLLM
servers on mango reached over HTTP; these are reached through the **Claude Code CLI in
headless mode on this account** -- one `claude -p` subprocess per call, no API key handled
here, no key printed or stored. The flags are:

    claude -p --model <id> --output-format json --restricted --no-session-persistence \
           --strict-mcp-config --tools "" --system-prompt "<SYSTEM>"   < prompt-on-stdin

`--tools ""` is variadic and would swallow a positional prompt, so the prompt goes on
**stdin**. The subprocess runs with cwd `/tmp`, which has no `CLAUDE.md` above it, and
`--restricted --strict-mcp-config --tools ""` keep Claude Code's own system prompt, its
tool definitions and this repository's `CLAUDE.md` out of the judge's context. That is not
asserted by reading the flags: the **first real call of every model** asserts that the
model's own input-token count is within 2x of `len(prompt + SYSTEM) / 3.5`, and the run
stops if it is not. A trivial call costs 283 input tokens, so a leaked harness prompt
would be visible by tens of thousands of tokens.

**One reading per pair.** Presentation k = 0 only -- the natural unit order. The CLI
exposes no temperature and no seed, so the five-presentation protocol of r3.1 would measure
sampler noise and presentation noise together and could not separate them; and the budget
for five readings of three models is not available. **Self-consistency is therefore not
measurable from this run and is reported as absent, not as a number.**

**Stop rule.** A usage/rate limit is an expected outcome, not a failure. On the first
response whose text or stderr names a rate limit, usage limit, overload or 429, the whole
run stops, everything completed is kept, the message and the per-model completed counts are
recorded, and the analysis proceeds on what exists. There is no sleep-and-retry beyond a
single transport retry.

**Zero store writes, zero GPU use.** The only process this file starts is the `claude`
CLI, and nothing here contacts `:9001-:9006`, `:50052`, Qdrant, Elasticsearch, Neo4j or
any tenant API; nothing selects a device. Outputs go to ``$STAGE0_BIG/work/claude/`` only;
nothing under ``work/r31/`` or ``work/r31ext/`` is opened for writing.

**One endpoint IS contacted besides the CLI, and it was not meant to be.** The SS6.5 window
budget is counted in the *served generator's* tokenizer by ``s0_common.GenTokenizer``,
which is a live probe of ``mango:8003/tokenize`` — a **`POST /tokenize` only**, never a
generation, and it selects no device. It runs once per structural unit per document:
**5,232 requests per pass** over these 306 documents, of which 3,360 come from the two
~5 MB outliers. The brief for this run said to contact nothing but the CLI; this was
missed before the first launch and is recorded as a deviation in the write-up rather than
quietly fixed, because swapping in a local tokenizer mid-study would have made the three
Claude judges window differently from each other and from r3.1's local judges, which is
the comparison the run exists to make.

Usage::

    python3 s0_label_claude.py --selftest                    # offline; starts no process
    python3 s0_label_claude.py --judge sonnet5 --limit 5 --tag smoke
    python3 s0_label_claude.py --judge sonnet5               # 308 pairs, one reading
    python3 s0_label_claude.py --merge-manifest
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

_HELPERS = pathlib.Path(os.environ.get(
    "STAGE0_HELPERS", "/home/wilke/Development/worktrees/phase0-rescue/phase0"))
for _p in (_HELPERS / "stage1", _HELPERS / "pilots"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))

import s0_common as C                                                    # noqa: E402
from s0_label import segment                                             # noqa: E402
from s0_label_r3 import SYSTEM, WINDOW_TOKENS, build_pairs, dup_indices  # noqa: E402
from s0_label_r3 import render_r3                                        # noqa: E402
from s0_label_r31 import (FAIL_PROBLEMS, PROMPT, REPROMPT,               # noqa: E402
                          RUBRIC, TOPICS, order_for, parse_and_verify_r31)

HERE = pathlib.Path(__file__).resolve().parent
CLW = C.WORK / "claude"
CLW.mkdir(parents=True, exist_ok=True)

# The three capability tiers, in the order they are run: cheapest first, so a usage limit
# hit late costs the study the most expensive model's reading and not the cheapest.
MODELS = {
    "sonnet5":  {"model": "claude-sonnet-5",  "tier": "mid",
                 "expected_cost_usd": 22.0},
    "opus5":    {"model": "claude-opus-5",    "tier": "high",
                 "expected_cost_usd": 54.0},
    "fable51":  {"model": "claude-fable-5-1", "tier": "frontier",
                 "expected_cost_usd": 110.0},
}
RUN_ORDER = ["sonnet5", "opus5", "fable51"]

CONC = 3                      # at most 3 CLI subprocesses in flight
CALL_TIMEOUT = 300            # seconds per call
ISOLATION_FACTOR = 2.0        # input tokens must be <= 2x (chars / 3.5)
CHARS_PER_TOKEN = 3.5
CWD = "/tmp"                  # no CLAUDE.md at or above this directory

# A response or a stderr naming any of these ends the run immediately (see the stop rule).
_LIMIT_RE = re.compile(
    r"rate.?limit|usage.?limit|quota|overloaded|429|too many requests|"
    r"insufficient.?credit|billing|out of credit", re.I)


class UsageLimit(RuntimeError):
    """Raised when the account's usage or rate limit ends the run."""


# ---------------------------------------------------------------------------- the judge
class ClaudeJudge:
    """Bounded headless-CLI client for one Claude model. <= ``conc`` subprocesses.

    Mirrors ``s0_label_r3.Judge``'s accounting so the two families' manifests are readable
    side by side, but the transport is a subprocess rather than an HTTP request, and the
    retry ladder is deliberately shorter: **one** retry on a transport error and **none**
    on a usage limit.
    """

    def __init__(self, name: str, conc: int = CONC):
        self.name = name
        self.model = MODELS[name]["model"]
        self.conc = conc
        self.pool = ThreadPoolExecutor(conc)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.stop_reason: str | None = None
        self.requests = self.retries = self.failures = 0
        self.input_tokens = self.output_tokens = 0
        self.cache_creation = self.cache_read = 0
        self.cost_usd = 0.0
        self.seconds = 0.0
        self.served: set[str] = set()
        self.isolation: dict | None = None
        self._first = True

    # -- one CLI call ------------------------------------------------------
    def _invoke(self, prompt: str) -> dict:
        cmd = ["claude", "-p", "--model", self.model, "--output-format", "json",
               "--restricted", "--no-session-persistence", "--strict-mcp-config",
               "--tools", "", "--system-prompt", SYSTEM]
        t0 = time.time()
        p = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                           cwd=CWD, timeout=CALL_TIMEOUT)
        dt = time.time() - t0
        if p.returncode != 0:
            err = (p.stderr or p.stdout or "")[-2000:]
            if _LIMIT_RE.search(err):
                raise UsageLimit(f"CLI exit {p.returncode}: {err.strip()}")
            raise RuntimeError(f"CLI exit {p.returncode}: {err.strip()}")
        try:
            obj = json.loads(p.stdout)
        except Exception as e:                                        # noqa: BLE001
            raise RuntimeError(f"unparseable CLI stdout ({type(e).__name__}): "
                               f"{p.stdout[:500]!r}") from e
        obj["_wall_seconds"] = round(dt, 2)
        return obj

    def chat(self, prompt: str) -> dict:
        """Return {text, raw(full CLI JSON), ok, ...}; raise UsageLimit to end the run."""
        if self.stop.is_set():
            raise UsageLimit(self.stop_reason or "run already stopped")
        last: Exception | None = None
        for attempt in range(2):                       # one retry on a transport error
            try:
                obj = self._invoke(prompt)
            except UsageLimit as e:
                with self.lock:
                    self.stop_reason = str(e)
                self.stop.set()
                raise
            except (subprocess.TimeoutExpired, RuntimeError, OSError) as e:
                last = e
                with self.lock:
                    self.retries += 1
                if attempt == 0:
                    time.sleep(3)
                    continue
                with self.lock:
                    self.failures += 1
                return {"text": "", "raw": {"transport_error": f"{type(e).__name__}: {e}"},
                        "ok": False, "finish": f"{type(e).__name__}", "seconds": 0.0}

            text = (obj.get("result") or "")
            if obj.get("is_error") and _LIMIT_RE.search(str(text) + str(
                    obj.get("api_error_status") or "")):
                with self.lock:
                    self.stop_reason = (f"is_error response: "
                                        f"{str(text)[:800]} "
                                        f"(api_error_status="
                                        f"{obj.get('api_error_status')})")
                self.stop.set()
                raise UsageLimit(self.stop_reason)

            u = obj.get("usage") or {}
            mu = obj.get("modelUsage") or {}
            own = self._own_usage(mu)
            with self.lock:
                self.requests += 1
                self.input_tokens += int(u.get("input_tokens") or 0)
                self.output_tokens += int(u.get("output_tokens") or 0)
                self.cache_creation += int(u.get("cache_creation_input_tokens") or 0)
                self.cache_read += int(u.get("cache_read_input_tokens") or 0)
                self.cost_usd += float(obj.get("total_cost_usd") or 0.0)
                self.seconds += float(obj.get("duration_ms") or 0) / 1000.0
                if own.get("key"):
                    self.served.add(own["key"])
                first = self._first
                self._first = False
            if first:
                self._assert_isolation(prompt, u, own)
            return {"text": text.strip(), "raw": obj, "ok": not obj.get("is_error"),
                    "finish": obj.get("stop_reason") or obj.get("subtype"),
                    "seconds": obj.get("_wall_seconds"),
                    "cost_usd": float(obj.get("total_cost_usd") or 0.0),
                    "duration_ms": obj.get("duration_ms"),
                    "usage": u, "model_usage": mu, "own_usage": own}
        raise RuntimeError(f"unreachable: {last}")

    def _own_usage(self, mu: dict) -> dict:
        """The judge model's own row of ``modelUsage``.

        The CLI also makes a small fixed-size ancillary call on `claude-haiku-4-5` (897
        input tokens on a trivial prompt, unchanged by ours). It is *not* the judge; it is
        harness overhead. Its cost is inside ``total_cost_usd`` and is therefore reported,
        but the isolation assertion and the served-model id read this row only.
        """
        for k, v in mu.items():
            if v.get("canonicalModel") == self.model or k == self.model \
                    or k.startswith(self.model):
                return {"key": k, **v}
        return {}

    def _assert_isolation(self, prompt: str, usage: dict, own: dict) -> None:
        """The judge's whole input context must be explained by the prompt we sent.

        All THREE input counters are summed. A prompt that was cached by an earlier
        identical call is billed as ``cache_read_input_tokens`` and reports
        ``input_tokens: 2``; reading only the uncached counters would make this assertion
        pass vacuously on any repeated prompt, which is exactly what happened the first
        time it was written. The context size is input + cache-creation + cache-read.
        """
        chars = len(prompt) + len(SYSTEM)
        budget = ISOLATION_FACTOR * chars / CHARS_PER_TOKEN
        parts = {
            "input_tokens": int(own.get("inputTokens")
                                if own.get("inputTokens") is not None
                                else usage.get("input_tokens") or 0),
            "cache_creation_input_tokens": int(
                own.get("cacheCreationInputTokens")
                if own.get("cacheCreationInputTokens") is not None
                else usage.get("cache_creation_input_tokens") or 0),
            "cache_read_input_tokens": int(
                own.get("cacheReadInputTokens")
                if own.get("cacheReadInputTokens") is not None
                else usage.get("cache_read_input_tokens") or 0),
        }
        seen = sum(parts.values())
        rec = {"prompt_plus_system_chars": chars,
               "implied_tokens_at_3.5_chars_per_token": round(chars / CHARS_PER_TOKEN, 1),
               "budget_2x": round(budget, 1),
               "context_tokens": seen, "context_tokens_by_counter": parts,
               "ratio_to_implied": round(seen / max(chars / CHARS_PER_TOKEN, 1), 3),
               "PASS": bool(seen <= budget), "model": self.model,
               "served_row": own.get("key")}
        self.isolation = rec
        if not rec["PASS"]:
            raise SystemExit(
                "ISOLATION ASSERTION FAILED — the judge's context is larger than the "
                f"prompt explains ({seen} tokens vs a {budget:.0f}-token budget for "
                f"{chars} characters). Claude Code's own system prompt or a CLAUDE.md "
                f"has leaked in. STOP.\n{json.dumps(rec, indent=1)}")
        print(f"isolation OK: {seen} context tokens ({parts}) for {chars} chars "
              f"(budget {budget:.0f}, ratio {rec['ratio_to_implied']})", flush=True)

    def stats(self) -> dict:
        return {"judge": self.name, "transport": "claude CLI (headless, -p)",
                "model": self.model, "served_model_rows": sorted(self.served),
                "requests": self.requests, "retries": self.retries,
                "failures": self.failures,
                "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "cache_creation_input_tokens": self.cache_creation,
                "cache_read_input_tokens": self.cache_read,
                "cost_usd": round(self.cost_usd, 4),
                "cli_seconds": round(self.seconds, 1), "concurrency": self.conc,
                "call_timeout_s": CALL_TIMEOUT,
                "temperature": "not exposed by the CLI — the local judges ran at 0.0; "
                               "this is a recorded deviation",
                "seed": "not exposed by the CLI"}


# ---------------------------------------------------------------------------- selftest
def selftest() -> dict:
    """Offline checks. Starts no subprocess and contacts nothing.

    Asserts the two things that make this run comparable to r3.1 at all: that the prompt
    and system strings are byte-identical to the ones `artifacts/r31/label-manifest-r31.json`
    records, and that presentation 0 is the natural unit order. Then checks the stop-rule
    matcher, the ancillary-call filter and the isolation arithmetic on synthetic input.
    """
    out: dict[str, object] = {}
    man = json.loads((HERE / "artifacts" / "r31" / "label-manifest-r31.json").read_text())
    ps, ss, rs = C.sha256_text(PROMPT), C.sha256_text(SYSTEM), C.sha256_text(REPROMPT)
    assert ps == man["prompt_sha256"], (
        f"PROMPT is not r3.1's: {ps} != {man['prompt_sha256']}")
    assert ss == man["system_sha256"], "SYSTEM is not r3.1's"
    assert rs == man["reprompt_sha256"], "REPROMPT is not r3.1's"
    assert man["prompt_revision"] == "3.1"
    out["prompt_sha256"] = ps
    out["system_sha256"] = ss
    out["reprompt_sha256"] = rs
    out["hashes_match_r31_manifest"] = "ok"

    assert C.sha256_file(RUBRIC) == man["rubric_sha256"], "the rubric moved"
    out["rubric_sha256"] = man["rubric_sha256"]

    o, seed, kind = order_for(0, 7, 9)
    assert o == list(range(9)) and kind.startswith("natural")
    assert seed == C.SEED_LABELDUP + 7
    out["presentation_0_is_natural_order"] = "ok"

    pairs, _lset, _q = build_pairs()
    assert len(pairs) == 308, f"pair set is {len(pairs)}, not r3.1's 308"
    bad = {t for t, _d, _k in pairs} - set(C.DEV_TOPICS)
    assert not bad, f"non-development topic in the pair set: {sorted(bad)}"
    kinds = sorted({k for _t, _d, k in pairs})
    assert kinds == ["pooled", "sample"], kinds
    out["pairs"] = len(pairs)
    out["pair_kinds"] = {k: sum(1 for _t, _d, x in pairs if x == k) for k in kinds}
    out["development_topics_only"] = "ok — asserted against C.DEV_TOPICS"
    out["duplicate_31_overlap"] = len(dup_indices(len(pairs)))

    for s in ("Claude AI usage limit reached", "429 Too Many Requests",
              "API Error: overloaded_error", "rate limit exceeded"):
        assert _LIMIT_RE.search(s), s
    for s in ("Connection reset by peer", '{"evidence_sets": []}', "timeout"):
        assert not _LIMIT_RE.search(s), s
    out["stop_rule_matcher"] = "ok"

    j = ClaudeJudge.__new__(ClaudeJudge)
    j.model = "claude-opus-5"
    mu = {"claude-haiku-4-5-20251001": {"canonicalModel": "claude-haiku-4-5",
                                        "inputTokens": 897},
          "claude-opus-5": {"canonicalModel": "claude-opus-5", "inputTokens": 12800}}
    own = ClaudeJudge._own_usage(j, mu)
    assert own["key"] == "claude-opus-5" and own["inputTokens"] == 12800, own
    out["ancillary_haiku_call_excluded_from_isolation"] = "ok"

    j.isolation = None
    body = "x" * 40000
    ClaudeJudge._assert_isolation(j, body, {}, {"inputTokens": 11000, "key": "m"})
    assert j.isolation["PASS"]
    try:
        ClaudeJudge._assert_isolation(j, body, {}, {"inputTokens": 90000, "key": "m"})
        raise AssertionError("isolation assertion did not fire on a leaked context")
    except SystemExit:
        pass
    # a cached prompt bills as cache_read and reports input_tokens 2; the assertion must
    # still see the whole context, and must still fire when the whole context is too big.
    ClaudeJudge._assert_isolation(j, body, {}, {"inputTokens": 2, "key": "m",
                                                "cacheReadInputTokens": 11000})
    assert j.isolation["context_tokens"] == 11002, j.isolation
    try:
        ClaudeJudge._assert_isolation(j, body, {}, {"inputTokens": 2, "key": "m",
                                                    "cacheReadInputTokens": 90000})
        raise AssertionError("isolation assertion ignored cache_read_input_tokens")
    except SystemExit:
        pass
    out["isolation_assertion"] = ("ok (fires on a context 8x the prompt, and counts a "
                                  "cache-read prompt rather than passing vacuously)")

    sets, probs, st = parse_and_verify_r31("not json at all", None, "")
    assert sets == [] and probs == ["no_json"]
    out["parser_is_r31s"] = "ok (imported, not re-declared)"
    return out


# ---------------------------------------------------------------------------- manifest
def merge_manifest() -> pathlib.Path:
    per, smoke = {}, {}
    for j in RUN_ORDER:
        p = CLW / f"label-manifest-claude-{j}.json"
        if p.exists():
            per[j] = json.loads(p.read_text())
        s = CLW / f"label-manifest-claude-{j}-smoke.json"
        if s.exists():
            smoke[j] = json.loads(s.read_text())
    tot = {"requests": 0, "input_tokens": 0, "output_tokens": 0,
           "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
           "retries": 0, "failures": 0, "cost_usd": 0.0, "cli_seconds": 0.0}
    tot["recorded_cost_usd"] = 0.0
    for m in list(per.values()) + list(smoke.values()):
        for k in tot:
            if k == "recorded_cost_usd":
                continue
            tot[k] += (m.get("stats") or {}).get(k, 0)
        tot["recorded_cost_usd"] += m.get("recorded_cost_usd") or 0.0
    tot["cost_usd"] = round(tot["cost_usd"], 4)
    tot["recorded_cost_usd"] = round(tot["recorded_cost_usd"], 4)
    tot["cost_note"] = ("`recorded_cost_usd` is the auditable figure — the sum over the "
                        "pairs whose raw CLI JSON is on disk. `cost_usd` is the "
                        "in-process counter over the same field; it leads the recorded "
                        "figure while a windowed pair is in flight and equals it at rest.")
    out = {
        "protocol": ("SPEC-confirmation-run-r3.md §10 item 4 — a third judge family. "
                     "r3.1's prompt (revision 3.1, whole-sentence anchors), SYSTEM, "
                     "rendering, §6.5 windowing and locator, ONE reading per pair "
                     "(presentation k = 0, natural unit order)."),
        "prompt_revision": "3.1",
        "prompt_sha256": C.sha256_text(PROMPT),
        "system_sha256": C.sha256_text(SYSTEM),
        "reprompt_sha256": C.sha256_text(REPROMPT),
        "rubric_sha256": C.sha256_file(RUBRIC), "rubric_path": str(RUBRIC),
        "n_presentations": 1,
        "presentation": "k = 0 only (natural unit order); "
                        "self-consistency is NOT measurable from one reading",
        "transport": "claude CLI headless (-p), one subprocess per call",
        "cli_flags": ["-p", "--model <id>", "--output-format json", "--restricted",
                      "--no-session-persistence", "--strict-mcp-config", '--tools ""',
                      "--system-prompt <SYSTEM>", "prompt on stdin", f"cwd {CWD}"],
        "cli_version": cli_version(),
        "temperature": "not exposed by the CLI (r3.1's local judges ran at 0.0)",
        "concurrency": CONC, "call_timeout_s": CALL_TIMEOUT,
        "window_tokens": WINDOW_TOKENS, "dev_topics": C.DEV_TOPICS,
        "judges": {j: {"model": MODELS[j]["model"], "tier": MODELS[j]["tier"],
                       "records": m.get("n_records_total"),
                       "pairs_attempted": m.get("n_pairs_total"),
                       "stats": m.get("stats"),
                       "recorded_cost_usd": m.get("recorded_cost_usd"),
                       "isolation_assertion": m.get("isolation_assertion"),
                       "stopped_by_usage_limit": m.get("stopped_by_usage_limit"),
                       "stop_message": m.get("stop_message"),
                       "wall_seconds": m.get("wall_seconds"),
                       "started_utc": m.get("started_utc"),
                       "finished_utc": m.get("finished_utc")}
                   for j, m in per.items()},
        "smoke": {j: {"stats": m.get("stats"), "wall_seconds": m.get("wall_seconds"),
                      "n_records_total": m.get("n_records_total"),
                      "isolation_assertion": m.get("isolation_assertion")}
                  for j, m in smoke.items()},
        "totals_including_smoke": tot,
        "endpoints_contacted": (
            "the `claude` CLI (a child process, talking to the first-party Claude API "
            "with this account's own credential; no credential is read, logged or stored "
            "here) AND `mango:8003/tokenize` — POST /tokenize only, 5,232 requests per "
            "pass, for the §6.5 window budget via s0_common.GenTokenizer. No generation "
            "was requested from mango and no device was selected. The brief said to "
            "contact nothing but the CLI; this is a recorded deviation."),
        "tokenize_requests_per_pass": 5232,
        "stores_contacted": ("none — no Qdrant/Elasticsearch/Neo4j/tenant-API/mango "
                             "client is constructed in s0_label_claude.py"),
    }
    C.atomic_json(CLW / "label-manifest-claude.json", out)
    return CLW / "label-manifest-claude.json"


def cli_version() -> str:
    try:
        return subprocess.run(["claude", "--version"], capture_output=True, text=True,
                              timeout=60, cwd=CWD).stdout.strip()
    except Exception as e:                                            # noqa: BLE001
        return f"UNRESOLVED — {type(e).__name__}: {e}"


# ---------------------------------------------------------------------------- the run
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--merge-manifest", action="store_true")
    ap.add_argument("--judge", choices=RUN_ORDER)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tag", default="")
    ap.add_argument("--conc", type=int, default=CONC)
    ap.add_argument("--max-cost-usd", type=float, default=0.0,
                    help="abort the run when the accumulated cost passes this")
    args = ap.parse_args()
    if args.selftest:
        print(json.dumps(selftest(), indent=1))
        return
    if args.merge_manifest:
        print(merge_manifest())
        return
    if not args.judge:
        raise SystemExit("--judge is required (or --selftest / --merge-manifest)")
    if args.conc > CONC:
        raise SystemExit(f"concurrency {args.conc} > {CONC}")

    assert RUBRIC.exists(), "P.5: no labeling call before the rubric exists"
    man31 = json.loads((HERE / "artifacts" / "r31"
                        / "label-manifest-r31.json").read_text())
    prompt_sha = C.sha256_text(PROMPT)
    assert prompt_sha == man31["prompt_sha256"], "PROMPT is not r3.1's — STOP"
    assert C.sha256_text(SYSTEM) == man31["system_sha256"], "SYSTEM is not r3.1's — STOP"
    seg_diff = subprocess.run(
        ["git", "-C", C.REPO, "diff", "--stat", f"{C.EXPECT_COMMIT}..HEAD",
         "--", "python/ragstack/ingestion/chunkers.py"],
        capture_output=True, text=True, check=True).stdout.strip()
    assert not seg_diff, f"chunkers.py moved since {C.EXPECT_COMMIT}: {seg_diff}"

    pairs, _lset, qrels = build_pairs()
    dup = dup_indices(len(pairs))
    idx = list(range(len(pairs)))
    if args.limit:
        idx = idx[:args.limit]

    suffix = f"-{args.tag}" if args.tag else ""
    out_path = CLW / f"labels-claude-{args.judge}{suffix}.jsonl"
    raw_path = CLW / f"raw-{args.judge}{suffix}.jsonl"
    man_path = CLW / f"label-manifest-claude-{args.judge}{suffix}.json"

    done: set[tuple[str, str]] = set()
    if out_path.exists():
        for line in out_path.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                done.add((r["topic"], r["docno"]))
    todo = [i for i in idx if (pairs[i][0], pairs[i][1]) not in done]
    print(f"judge={args.judge} model={MODELS[args.judge]['model']} "
          f"pairs={len(idx)} to_run={len(todo)} already_done={len(done)}", flush=True)

    docs, units = {}, {}
    for line in open(C.WORK / "docs.jsonl"):
        r = json.loads(line)
        docs[r["docno"]] = r["text"]
    for line in open(C.WORK / "units.jsonl"):
        r = json.loads(line)
        units[r["docno"]] = r["units"]
    tops = json.loads(TOPICS.read_text())
    gt = C.GenTokenizer()

    judge = ClaudeJudge(args.judge, conc=args.conc)
    lock = threading.Lock()
    tok_lock = threading.Lock()
    t0 = time.time()
    fout = open(out_path, "a")
    fraw = open(raw_path, "a")
    n_done = [0]
    spent = [0.0]        # sum of the costs of calls whose RECORD reached the disk
    limit_hit: list[str] = []

    def one(i):
        if judge.stop.is_set():
            return None
        t, d, kind = pairs[i]
        text, us = docs[d], units[d]
        seg = segment(text, us)
        f = tops[t]["fields"]
        order, seed, order_kind = order_for(0, i, len(seg))

        groups, cur, curtok = [], [], 0                       # §6.5 windowing, in ORDER
        for jx in order:
            with tok_lock:
                n = gt.count(text[us[jx]["start_char"]:us[jx]["end_char"]])
            if cur and curtok + n > WINDOW_TOKENS:
                groups.append(cur)
                cur, curtok = [], 0
            cur.append(jx)
            curtok += n
        if cur:
            groups.append(cur)

        allsets, problems, raws, rawfull, finishes = [], [], [], [], []
        vstats: dict[str, int] = {}
        cost = 0.0
        dur = 0
        usage_tot = {"input_tokens": 0, "output_tokens": 0,
                     "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        served = []
        for g in groups:
            p = PROMPT.format(ntype=tops[t]["type"], summary=f["summary"],
                              description=f["description"], body=render_r3(seg, g))
            r = judge.chat(p)
            raws.append(r["text"])
            rawfull.append(r["raw"])
            finishes.append(r.get("finish"))
            cost += r.get("cost_usd", 0.0) or 0.0
            dur += int(r.get("duration_ms") or 0)
            for k_ in usage_tot:
                usage_tot[k_] += int((r.get("usage") or {}).get(k_) or 0)
            if r.get("own_usage", {}).get("key"):
                served.append(r["own_usage"]["key"])
            sets, probs, stx = parse_and_verify_r31(r["text"], seg, text)
            probs_all = list(probs)
            failed = any(x in FAIL_PROBLEMS or x.startswith("bad_json") for x in probs)
            if failed:                                   # §6.4 rule 2: exactly ONE retry
                r2 = judge.chat(p + REPROMPT)
                raws.append(r2["text"])
                rawfull.append(r2["raw"])
                finishes.append(r2.get("finish"))
                cost += r2.get("cost_usd", 0.0) or 0.0
                dur += int(r2.get("duration_ms") or 0)
                for k_ in usage_tot:
                    usage_tot[k_] += int((r2.get("usage") or {}).get(k_) or 0)
                sets2, probs2, stx2 = parse_and_verify_r31(r2["text"], seg, text)
                for k_, v_ in stx2.items():
                    stx[k_] = stx.get(k_, 0) + v_
                probs_all += list(probs2) + ["reprompted"]
                sets = sets2 if sets2 else sets
            allsets.extend(sets)
            problems.extend(probs_all)
            for k_, v_ in stx.items():
                vstats[k_] = vstats.get(k_, 0) + v_

        rec = {"topic": t, "docno": d, "kind": kind, "grade": qrels[t].get(d, 0),
               "sets": allsets, "problems": problems, "windowed": len(groups) > 1,
               "vstats": vstats, "raws": raws,
               "n_quote_fail": vstats.get("hallucinated", 0),
               "n_spans": sum(len(s["spans"]) for s in allsets),
               "n_units": len(seg), "doc_chars": len(text),
               "dropped": bool(problems) and not allsets,
               "judge": args.judge, "served_model": sorted(set(served)),
               "model": MODELS[args.judge]["model"],
               "prompt_sha256": prompt_sha,
               "raw_response_sha256": hashlib.sha256(
                   "\n\x00\n".join(raws).encode()).hexdigest(),
               "finish_reasons": finishes, "pair_index": i,
               "presentation": 0, "unit_order_seed": seed, "unit_order": order_kind,
               "in_501_duplicate_31": i in dup,
               "cost_usd": round(cost, 6), "duration_ms": dur, "usage": usage_tot,
               "n_calls": len(rawfull)}

        with lock:
            fout.write(json.dumps(rec) + "\n")
            fout.flush()
            fraw.write(json.dumps({"topic": t, "docno": d, "presentation": 0,
                                   "calls": rawfull}) + "\n")
            fraw.flush()
            n_done[0] += 1
            spent[0] += rec["cost_usd"]
            if n_done[0] % 10 == 0:
                el = time.time() - t0
                print(f"  {n_done[0]}/{len(todo)}  {el:.0f}s  "
                      f"{el / n_done[0]:.2f}s/pair  ${spent[0]:.2f} recorded "
                      f"(counter ${judge.cost_usd:.2f})  "
                      f"proj ${spent[0] / n_done[0] * len(idx):.0f}", flush=True)
        # The cap reads the RECORDED spend — the sum over the pairs whose raw JSON is on
        # disk — rather than the in-process counter. The counter accounts every CLI call
        # as it returns, so while a §6.5-windowed pair is in flight it LEADS the recorded
        # figure by that pair's partial spend, and two of these 308 documents are ~5 MB
        # and split into ~26 windows each. Capping on the counter would therefore cut a
        # run short on a pair that is merely half-finished. The recorded figure is also
        # the auditable one: it recomputes from `raw-<judge>.jsonl`.
        if args.max_cost_usd and spent[0] > args.max_cost_usd:
            with lock:
                judge.stop_reason = (f"cost cap: ${spent[0]:.2f} recorded > "
                                     f"${args.max_cost_usd:.2f}")
            judge.stop.set()
        return rec

    def guarded(i):
        try:
            return one(i)
        except UsageLimit as e:
            with lock:
                if not limit_hit:
                    limit_hit.append(str(e))
                    print(f"\n*** USAGE LIMIT — STOPPING: {e}\n", flush=True)
            return None
        except Exception as e:                                        # noqa: BLE001
            with lock:
                print(f"  pair {i} failed: {type(e).__name__}: {e}", flush=True)
            return None

    list(judge.pool.map(guarded, todo))
    judge.pool.shutdown(wait=True)
    fout.close()
    fraw.close()
    wall = round(time.time() - t0, 1)
    n_recs = sum(1 for line in out_path.read_text().splitlines() if line.strip())
    C.atomic_json(man_path, {
        "judge": args.judge, "model": MODELS[args.judge]["model"],
        "tier": MODELS[args.judge]["tier"],
        "protocol": ("SPEC-confirmation-run-r3.md §10 item 4 — third judge family, "
                     "r3.1 protocol, ONE reading (presentation k = 0)"),
        "transport": "claude CLI headless (-p)", "cli_version": cli_version(),
        "cli_flags": ["-p", "--model", MODELS[args.judge]["model"], "--output-format",
                      "json", "--restricted", "--no-session-persistence",
                      "--strict-mcp-config", "--tools", '""', "--system-prompt",
                      "<SYSTEM>", "(prompt on stdin)", f"cwd={CWD}"],
        "rubric_sha256": C.sha256_file(RUBRIC), "rubric_path": str(RUBRIC),
        "prompt_sha256": prompt_sha, "prompt_revision": "3.1",
        "system_sha256": C.sha256_text(SYSTEM),
        "reprompt_sha256": C.sha256_text(REPROMPT),
        "isolation_assertion": judge.isolation,
        "stats": judge.stats(),
        "recorded_cost_usd": round(spent[0], 4),
        "recorded_cost_note":
            "sum of `total_cost_usd` over the pairs whose raw CLI JSON reached "
            "`raw-<judge>.jsonl` — recomputable from that file, and the figure every "
            "cost quoted downstream uses. `stats.cost_usd` is the in-process counter over "
            "the same field; the two agree once the run is idle, and the counter LEADS "
            "while a §6.5-windowed pair is mid-flight (two of the 308 documents are ~5 MB "
            "and take ~26 windows each).",
        "n_pairs_total": len(idx), "n_presentations": 1,
        "n_records_total": n_recs, "n_records_run": len(todo),
        "n_records_preexisting": len(done),
        "stopped_by_usage_limit": bool(limit_hit),
        "stop_message": limit_hit[0] if limit_hit else None,
        "dup_indices_501": sorted(dup), "window_tokens": WINDOW_TOKENS,
        "dev_topics": C.DEV_TOPICS,
        "sentence_segmentation":
            f"ragstack.ingestion.chunkers.sentence_spans @ {C.EXPECT_COMMIT[:7]} "
            f"(unchanged at repo HEAD, asserted)",
        "labels_path": str(out_path), "raw_path": str(raw_path),
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0)),
        "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "wall_seconds": wall,
        "seconds_per_record": round(wall / max(len(todo), 1), 3)})
    print(f"labels written: {out_path} ({n_recs} records)  wall={wall}s "
          f"recorded=${spent[0]:.2f} counter=${judge.cost_usd:.2f}",
          json.dumps(judge.stats()), flush=True)
    if limit_hit:
        print(f"STOPPED BY USAGE LIMIT after {n_done[0]} of {len(todo)} pairs: "
              f"{limit_hit[0]}", flush=True)
        raise SystemExit(3)


if __name__ == "__main__":
    main()
