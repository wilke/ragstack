"""Part 4 (confirmatory) of the deployed-docs chunk-size trial: the ``part4`` subcommand.

The pre-registration is kept with the outputs, outside the repo. This module
holds the pipeline; :mod:`deployed_docs_trial` holds the shared pieces
(private config, page reading, arm building, prompts, validation) and the CLI
(``deployed_docs_trial.py part4``). Nothing private is in this file: every
prompt, endpoint, model id, file name and seed comes from ``--private-config``
(the base keys of :mod:`deployed_docs_trial` plus the ``part4`` block below).

Steps, in order (each resumable): ``generation`` -> ``retrieval`` ->
``readers`` -> ``grading`` -> ``retest``; then the results file.

* generation: the S and M allocation (fresh-window exclusion against the gold
  quotes of earlier parts), one gateway call per attempt (generator at
  temperature 0; an empty answer gets one retry, then the fallback model,
  flagged ``gen=<fallback tag>``), parts-2/3 validation, the duplicate rule.
* retrieval: the served pipeline per arm (reranked list per question).
* readers: every (question, arm, condition, reader). B<n> conditions walk the
  reranked list and truncate the overflowing chunk at the last sentence
  boundary within the remaining budget; k10 takes the top 10 whole chunks.
* grading: the primary grader on every B answer plus k10 on a seeded
  stratified question subset; the secondary grader on every answer of a
  seeded stratified question subset; deduplicated by (question, normalised
  answer); the full raw grade text stored privately; :func:`parse_grade`.
* retest: a seeded sample of primary grades re-graded once, bypassing the
  cache, into a separate file.

**Call keys.** Every call result is a row of an append-only JSONL file (one
fsync per write) keyed ``step|question|arm|condition|role``. For grading and
retest the (arm, condition) fields hold the dedup key (sha256 of question id
+ normalised answer) and ``-``, because one call serves every (arm,
condition, reader) with that answer. A key that has a row is never re-issued.

**Pause and resume.** Pause triggers: the gateway output-token cap, a per-batch
budget of gateway calls / input / output tokens, the failure stops (N in a
row within a segment, more than M in total over the run; 0-token empties are
retried and then recorded as ``no_label (empty)``, a ``finish_reason`` "length"
with no visible content is ``no_label (truncated)``; neither counts), HTTP 429 / a quota error after the configured
backoffs, a local endpoint unreachable for the configured time, a ``PAUSE``
file in the output directory, SIGTERM. On a pause the run stops issuing
calls, lets the in-flight calls finish and write, writes ``part4-state.json``,
prints one ``GATEWAY-NOTICE`` line and exits 75. ``--resume`` refuses unless
the commit, the prereg sha256 and the config sha256 match the state file.

Exit codes: 0 complete, 75 paused (resumable), 1 hard stop (manipulation-check
mismatch, reader failures over the configured share, sha mismatch on resume,
a hard-stopped state) or a crash. A crash (any unexpected exception) writes
``status: crashed``, the reason and the totals to the state file and prints the
notice line; unlike a hard stop it may be resumed (``--resume``, same sha checks).

``part4`` config block (every key optional unless marked required; defaults in
:data:`DEFAULTS`)::

    seed                       int, all part-4 draws (allocation, subsets, retest, export)
    arms                       list of arm names (L, SEM512, T128o0, T512o0, T1024o0)
    conditions                 {name: {"budget": tokens} | {"top": k}}
    expected_chunks            REQUIRED {arm: chunk count}: the manipulation-check gate
    expected_m_pages           REQUIRED int: the M census page count (pages >= M.min_page_tokens); a gate too
    readers                    list of reader keys of deployed_docs_trial.K10_READERS
    reader                     {max_tokens, temperature, inflight, window_reserve, retries, retry_backoff_s}
    generator                  {model, tag, max_tokens, temperature, empty_retry_s}
    generator_fallback         {model, tag, max_tokens, temperature (null = not sent)}
    grader_primary             {model, tag, max_tokens, temperature, k10_fraction}
    grader_secondary           {model, tag, max_tokens, temperature, fraction}
    M                          {min_page_tokens, attempts_per_page, chars, gap}
    S                          {min_live_chunks, chars, max_attempts}
    max_draw_tries             rejection-sampling tries per window
    prior_question_files       REQUIRED list of question JSONL files (relative to out_dir)
                               whose valid gold quotes new windows may not overlap
    retest_n                   primary grades re-graded once
    gateway                    {inflight, passes}
    limits                     {output_token_cap, batch_calls, batch_input_tokens,
                                batch_output_tokens, max_fail_consecutive, max_fail_total,
                                empty_retries, empty_backoff_s, rate_backoffs_s,
                                local_unreachable_s, local_retry_s, reader_fail_share}
    human                      {part_a, part_b_pairs, part_b_arms, part_b_set,
                                part_b_condition, part_b_reader}
    analysis                   {delta, n_perm, n_boot, alpha, ci_level, procedure ("holm4" | "fixed_sequence"),
                                order (the fixed sequence), hypotheses {H: [arm A, arm B, "superiority" |
                                "noninferiority"]}, family_set, family_condition}
    files                      {prereg (REQUIRED), prereg_sha256 (the frozen sha; checked when set),
                                manifest, state, gen_calls, questions, generation, retrieval,
                                answers, grades ("{tag}" placeholder), retest ("{tag}"),
                                call_log, results, pause_file}
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
import random
import re
import signal
import statistics
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import chunktrial_stats as cs  # noqa: E402
import deployed_docs_trial as dt  # noqa: E402
import section_aware_trial as sat  # noqa: E402
from section_aware_trial import C, log, norm, read_jsonl  # noqa: E402

EXIT_OK, EXIT_HARD_STOP, EXIT_PAUSED = 0, 1, 75
STEPS = ("generation", "retrieval", "readers", "grading", "retest")
LABELS = ("correct", "partial", "incorrect", "abstained")
STATE_SCHEMA = "part4-state/1"
#: generator outcomes that go to the fallback model: still empty after the retry, or truncated empty
FALLBACK_ON = ("empty", "no_label")
RESULTS_SCHEMA = "part4-results/1"

DEFAULTS: dict[str, Any] = {
    "seed": 261025,
    "arms": ["L", "SEM512", "T128o0", "T512o0", "T1024o0"],
    "conditions": {"B4096": {"budget": 4096}, "B2048": {"budget": 2048}, "k10": {"top": 10}},
    "expected_chunks": None,
    "expected_m_pages": None,
    "readers": ["scout", "qwen"],
    "reader": {"max_tokens": 600, "temperature": 0, "inflight": 4, "window_reserve": 512,
               "retries": 3, "retry_backoff_s": 3.0},
    "generator": {"model": "Claude Opus 4.8", "tag": "opus48", "max_tokens": 1024, "temperature": 0,
                  "empty_retry_s": 2.0},
    "generator_fallback": {"model": "GPT-5.6 Sol", "tag": "sol", "max_tokens": 4096, "temperature": None},
    "grader_primary": {"model": "GPT-5.6 Sol", "tag": "sol", "max_tokens": 4096, "temperature": None,
                       "k10_fraction": 0.5},
    "grader_secondary": {"model": "Claude Opus 4.8", "tag": "opus48", "max_tokens": 512, "temperature": 0,
                         "fraction": 0.25},
    "M": {"min_page_tokens": 2048, "attempts_per_page": 6, "chars": 1000, "gap": 1500},
    "S": {"min_live_chunks": 2, "chars": 1200, "max_attempts": 420},
    "max_draw_tries": 200,
    "prior_question_files": None,
    "retest_n": 200,
    "gateway": {"inflight": 4, "passes": 2},
    "limits": {"output_token_cap": 3_000_000, "batch_calls": None, "batch_input_tokens": None,
               "batch_output_tokens": None, "max_fail_consecutive": 5, "max_fail_total": 20,
               "empty_retries": 2, "empty_backoff_s": 30.0, "rate_backoffs_s": [30.0, 120.0, 600.0],
               "local_unreachable_s": 600.0, "local_retry_s": 10.0, "reader_fail_share": 0.02},
    "human": {"part_a": 40, "part_b_pairs": 30, "part_b_arms": ["T512o0", "L"], "part_b_set": "M",
              "part_b_condition": "B4096", "part_b_reader": "scout"},
    "analysis": {"delta": 0.075, "n_perm": 10000, "n_boot": 10000, "alpha": 0.05, "ci_level": 0.90,
                 "procedure": "holm4", "order": ["H4", "H2", "H3", "H1"],
                 "hypotheses": {"H1": ["T512o0", "T128o0", "superiority"], "H2": ["SEM512", "L", "noninferiority"],
                                "H3": ["T512o0", "T1024o0", "noninferiority"],
                                "H4": ["T512o0", "L", "noninferiority"]},
                 "family_set": "M", "family_condition": "B4096"},
    "files": {"prereg": None, "prereg_sha256": None, "manifest": "manifest-261023.json",
              "state": "part4-state.json", "gen_calls": "part4-gen-calls.jsonl",
              "questions": "part4-questions.jsonl", "generation": "part4-generation.json",
              "retrieval": "part4-retrieval.jsonl", "answers": "part4-answers.jsonl",
              "grades": "part4-grades-{tag}.jsonl", "retest": "part4-retest-{tag}.jsonl",
              "call_log": "part4-calls.jsonl", "results": "results-part4.json", "pause_file": "PAUSE"},
}
REQUIRED = ("expected_chunks", "expected_m_pages", "prior_question_files")


def part4_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """The ``part4`` block merged over :data:`DEFAULTS` (one level of nesting)."""
    user = cfg.get("part4")
    if not isinstance(user, dict):
        raise SystemExit("private config: the 'part4' block is missing")
    out = copy.deepcopy(DEFAULTS)
    for k, v in user.items():
        if k not in DEFAULTS:
            raise SystemExit(f"private config: unknown part4 key {k!r}")
        if isinstance(DEFAULTS[k], dict) and k not in ("conditions",) and isinstance(v, dict):
            unknown = sorted(set(v) - set(DEFAULTS[k])) if k != "expected_chunks" else []
            if unknown:
                raise SystemExit(f"private config: unknown part4.{k} keys {unknown}")
            out[k].update(v)
        else:
            out[k] = v
    for k in REQUIRED:
        if out[k] is None:
            raise SystemExit(f"private config: part4.{k} is required")
    if not isinstance(out["expected_m_pages"], int) or isinstance(out["expected_m_pages"], bool):
        raise SystemExit("private config: part4.expected_m_pages must be an int (the M census page count)")
    if out["analysis"]["procedure"] not in PROCEDURES:
        raise SystemExit(f"private config: part4.analysis.procedure must be one of {list(PROCEDURES)}")
    if sorted(out["analysis"]["order"]) != sorted(out["analysis"]["hypotheses"]):
        raise SystemExit("private config: part4.analysis.order must list every hypothesis once")
    if not out["files"]["prereg"]:
        raise SystemExit("private config: part4.files.prereg is required")
    for c, spec in out["conditions"].items():
        if not (("budget" in spec) ^ ("top" in spec)):
            raise SystemExit(f"private config: condition {c} needs exactly one of budget / top")
    return out


# ------------------------------------------------------------------ signals

class Pause(Exception):  # noqa: N818 - a control-flow signal, not an error
    """The run pauses (exit 75); ``reason`` says why."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class HardStop(Exception):  # noqa: N818
    """The run stops for good (exit 1); no resume without a new decision."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def ckey(step: str, question: str, arm: str = "-", condition: str = "-", role: str = "-") -> str:
    """The call key: (step, question, arm, condition, reader or grader)."""
    return "|".join((step, question, arm, condition, role))


def answer_key(qid: str, answer: str) -> str:
    """The grading dedup key: sha256(question id + NUL + normalised answer) (as the k10 test)."""
    return dt.sha(qid + "\x00" + norm(answer))


def file_sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# -------------------------------------------------------------------- stores

def read_jsonl_repair_tail(path: Path) -> list[dict[str, Any]]:
    """The rows of a part-4 append-only JSONL file, after repairing a torn tail.

    A torn trailing line (a crash mid-write: the last line has no newline and is not
    valid JSON) is dropped with a log line: it is moved to ``<file>.torn`` and cut
    from the file, so the next append starts on a fresh line. A complete last row
    that only lacks its newline gets one. Any other line that is not valid JSON is
    not a torn tail: SystemExit. (``section_aware_trial.read_jsonl``, which the older
    subcommands use, is unchanged.)"""
    if not path.exists():
        return []
    data = path.read_bytes()
    lines = data.split(b"\n")
    tail = lines[-1]  # b"" when the file ends with a newline
    rows: list[dict[str, Any]] = []
    for i, ln in enumerate(lines[:-1], 1):
        if not ln.strip():
            continue
        try:
            rows.append(json.loads(ln))
        except json.JSONDecodeError:
            raise SystemExit(f"part4: {path.name}: line {i} is not valid JSON and is not the last line") from None
    if tail.strip():
        try:
            rows.append(json.loads(tail))
        except json.JSONDecodeError:
            log(f"part4: {path.name}: dropped a torn trailing line ({len(tail)} bytes, no newline: "
                f"a write cut short); kept in {path.name}.torn")
            with path.with_name(path.name + ".torn").open("ab") as fh:
                fh.write(tail + b"\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.truncate(path, len(data) - len(tail))
        else:
            with path.open("ab") as fh:
                fh.write(b"\n")
                fh.flush()
                os.fsync(fh.fileno())
    return rows


class Store:
    """Append-only JSONL of ``{"k": call key, ...}`` rows, one fsync per write (the
    last row of a key wins; a torn tail is repaired on read: :func:`read_jsonl_repair_tail`).

    That every write is fsynced is checked by inspection of :func:`append_fsync`, not
    by a test (a test cannot observe an fsync without a crash)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.rows: dict[str, dict[str, Any]] = {x["k"]: x for x in read_jsonl_repair_tail(path)}

    def __contains__(self, k: str) -> bool:
        return k in self.rows

    def get(self, k: str) -> dict[str, Any] | None:
        return self.rows.get(k)

    def put(self, row: dict[str, Any]) -> None:
        append_fsync(self.path, row)
        self.rows[row["k"]] = row


def append_fsync(path: Path, row: dict[str, Any]) -> None:
    with path.open("a") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def write_json_atomic(path: Path, obj: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as fh:
        fh.write(json.dumps(obj, indent=1, sort_keys=True, default=str) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


# ------------------------------------------------------------------- control

class Control:
    """Pause triggers and call/token totals for one run segment.

    The failure stops: "more than ``max_fail_total``" counts over the whole run, so
    ``fail_total`` starts from the persisted ``gateway_failures:*`` totals of the
    earlier segments (``since_start`` in the state file). "``max_fail_consecutive``
    in a row" restarts at 0 on every resume: a pause breaks the run of failures, and
    the operator has looked at the cause before resuming.
    """

    def __init__(self, limits: dict[str, Any], pause_path: Path, prev_totals: dict[str, int] | None) -> None:
        self.lim = limits
        self.pause_path = pause_path
        self.reason: str | None = None
        self.seg: Counter[str] = Counter()
        self.prev: Counter[str] = Counter(prev_totals or {})
        self.fail_total = sum(int(v) for k, v in self.prev.items() if k.startswith("gateway_failures:"))
        self.fail_consec = 0

    def stop(self, reason: str) -> None:
        if self.reason is None:
            self.reason = reason
            log(f"part4: pausing ({reason}); draining in-flight calls")

    def stopped(self) -> bool:
        if self.reason is None and self.pause_path.exists():
            self.stop("pause_file")
        return self.reason is not None

    def gateway_admit(self) -> bool:
        """Checked before every gateway request: the operator stops, the output cap, the batch budget."""
        if self.stopped():
            return False
        lim, seg = self.lim, self.seg
        cap = lim.get("output_token_cap")
        if cap is not None and self.prev["gateway_completion_tokens"] + seg["gateway_completion_tokens"] >= cap:
            self.stop("output_token_cap")
        elif lim.get("batch_calls") is not None and seg["gateway_calls"] >= lim["batch_calls"]:
            self.stop("batch_calls")
        elif lim.get("batch_input_tokens") is not None and seg["gateway_prompt_tokens"] >= lim["batch_input_tokens"]:
            self.stop("batch_input_tokens")
        elif (lim.get("batch_output_tokens") is not None
              and seg["gateway_completion_tokens"] >= lim["batch_output_tokens"]):
            self.stop("batch_output_tokens")
        return self.reason is None

    def account(self, kind: str, pt: int = 0, ct: int = 0, call: bool = True) -> None:
        if call:
            self.seg[f"{kind}_calls"] += 1
        self.seg[f"{kind}_prompt_tokens"] += pt
        self.seg[f"{kind}_completion_tokens"] += ct

    def fail(self, kind: str) -> None:
        self.fail_total += 1
        self.fail_consec += 1
        self.seg[f"gateway_failures:{kind}"] += 1
        if self.fail_consec >= self.lim["max_fail_consecutive"]:
            self.stop(f"failures_{self.lim['max_fail_consecutive']}_in_a_row")
        elif self.fail_total > self.lim["max_fail_total"]:
            self.stop(f"failures_over_{self.lim['max_fail_total']}")

    def ok(self) -> None:
        self.fail_consec = 0

    def totals(self) -> dict[str, dict[str, int]]:
        return {"since_start": dict(self.prev + self.seg), "since_resume": dict(self.seg)}

    async def sleep(self, seconds: float) -> bool:
        """Sleep, waking early on a stop; False when the run is stopping."""
        end = time.monotonic() + seconds
        while True:
            if self.stopped():
                return False
            left = end - time.monotonic()
            if left <= 0:
                return True
            await asyncio.sleep(min(left, 0.25))


def notice(msg: str) -> None:
    """A one-line notice for the gateway owner (printed only; nothing is sent anywhere)."""
    print(f"GATEWAY-NOTICE part4: {msg}", flush=True)


# ------------------------------------------------------------------- gateway

def is_rate_limited(status: int, text: str) -> bool:
    t = text.lower()
    return status == 429 or (status >= 400 and ("quota" in t or "rate limit" in t or "rate_limit" in t))


class Gateway:
    """The gateway client: one request per call plus the configured safeguards."""

    def __init__(self, url: str, control: Control, limits: dict[str, Any], call_log: Path) -> None:
        dt.check_url(url)
        self.url = url.rstrip("/")
        self.control = control
        self.lim = limits
        self.call_log = call_log
        read_jsonl_repair_tail(call_log)
        self.temperature_dropped: set[str] = set()

    def body(self, spec: dict[str, Any], msgs: list[dict[str, str]]) -> dict[str, Any]:
        b: dict[str, Any] = {"model": spec["model"], "messages": msgs, "max_tokens": spec["max_tokens"],
                             "fallback_model": ""}
        if spec.get("temperature") is not None and spec["model"] not in self.temperature_dropped:
            b["temperature"] = spec["temperature"]
        return b

    def _log(self, step: str, key: str, row: dict[str, Any]) -> None:
        append_fsync(self.call_log, {"t": round(time.time(), 2), "step": step, "key": key, **row})

    async def once(self, http: Any, spec: dict[str, Any], msgs: list[dict[str, str]], step: str,
                   key: str) -> dict[str, Any] | None:
        """One logical request with the rate-limit backoffs. None when the run is stopping."""
        rate_i = 0
        while True:
            if not self.control.gateway_admit():
                return None
            body = self.body(spec, msgs)
            self.control.account("gateway")  # counted at admission, so the batch budget is exact
            try:
                r = await http.post(f"{self.url}/v1/chat/completions", json=body)
            except Exception as e:  # noqa: BLE001
                self._log(step, key, {"status": f"error:{type(e).__name__}"})
                return {"status": "error", "error": type(e).__name__}
            if is_rate_limited(r.status_code, r.text):
                backoffs = self.lim["rate_backoffs_s"]
                self._log(step, key, {"status": f"rate_limited:{r.status_code}", "backoff": rate_i})
                if rate_i < len(backoffs):
                    log(f"gateway HTTP {r.status_code} (rate/quota); backoff {backoffs[rate_i]}s")
                    if not await self.control.sleep(backoffs[rate_i]):
                        return None
                    rate_i += 1
                    continue
                self.control.stop("rate_limit")
                return None
            if r.status_code == 400 and "temperature" in body and "temperature" in r.text.lower():
                self.temperature_dropped.add(spec["model"])
                log(f"{spec['model']}: HTTP 400 mentioning temperature; omitting temperature from now on")
                continue
            if r.status_code >= 400:
                self._log(step, key, {"status": f"error:http_{r.status_code}"})
                return {"status": "error", "error": f"http_{r.status_code}"}
            try:
                d = r.json()
                txt = d["choices"][0]["message"].get("content") or ""
                fin = d["choices"][0].get("finish_reason")
            except Exception:  # noqa: BLE001
                self._log(step, key, {"status": "error:malformed"})
                return {"status": "error", "error": "malformed"}
            u = d.get("usage") or {}
            pt, ct = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
            self.control.account("gateway", pt, ct, call=False)
            if txt.strip():
                status = "ok"
            elif fin == "length":
                status = "truncated"  # max_tokens spent before any visible output (hidden reasoning)
            else:
                status = "empty0" if ct == 0 else "empty"
            row = {"status": status, "pt": pt, "ct": ct, "finish": fin, "model": d.get("model")}
            self._log(step, key, row)
            return {**row, "text": txt}

    @staticmethod
    def truncated(res: dict[str, Any], attempts: int) -> dict[str, Any]:
        """``no_label (truncated)``: ``finish_reason == "length"`` with no visible content.
        Not a failure and not retried (the same max_tokens would truncate again)."""
        return {**res, "status": "no_label", "reason": "truncated", "text": "", "attempts": attempts}

    async def standard(self, http: Any, spec: dict[str, Any], msgs: list[dict[str, str]], step: str,
                       key: str) -> dict[str, Any] | None:
        """The standing safeguards: a 0-token empty is retried ``empty_retries`` times
        ``empty_backoff_s`` apart, then ``no_label (empty)`` (not a failure); a
        truncated empty is ``no_label (truncated)`` at once (not a failure); an empty
        with tokens or an error is a failure (None, nothing recorded, re-issued later)."""
        for attempt in range(1 + int(self.lim["empty_retries"])):
            if attempt and not await self.control.sleep(self.lim["empty_backoff_s"]):
                return None
            res = await self.once(http, spec, msgs, step, key)
            if res is None:
                return None
            if res["status"] == "ok":
                self.control.ok()
                return {**res, "attempts": attempt + 1}
            if res["status"] == "truncated":
                self.control.ok()
                return self.truncated(res, attempt + 1)
            if res["status"] != "empty0":
                self.control.fail(res["status"] if res["status"] != "error" else res["error"])
                return None
        return {"status": "no_label", "reason": "empty", "text": "", "pt": 0, "ct": 0, "finish": None,
                "model": None, "attempts": 1 + int(self.lim["empty_retries"])}

    async def generate(self, http: Any, spec: dict[str, Any], msgs: list[dict[str, str]], step: str,
                       key: str, retry_s: float) -> dict[str, Any] | None:
        """The generator: an empty answer (any token count) gets one retry after
        ``retry_s``; still empty -> status ``empty`` (the caller falls back). A
        truncated empty is ``no_label (truncated)`` at once, and the caller falls
        back too (an empty response, whatever its cause)."""
        last = None
        attempt = 0
        for attempt in range(1, 3):
            if attempt > 1 and not await self.control.sleep(retry_s):
                return None
            res = await self.once(http, spec, msgs, step, key)
            if res is None:
                return None
            if res["status"] == "ok":
                self.control.ok()
                return {**res, "attempts": attempt}
            if res["status"] == "truncated":
                self.control.ok()
                return self.truncated(res, attempt)
            if res["status"] == "error":
                self.control.fail(res["error"])
                return None
            last = res
        assert last is not None
        return {**last, "status": "empty", "attempts": attempt}


# --------------------------------------------------------------- local readers

class LocalClient:
    """Reader endpoints: <= ``inflight`` per endpoint; an endpoint that refuses
    connections (or answers 502/503/504) is retried every ``local_retry_s``
    until it has been unreachable for ``local_unreachable_s`` (then a pause);
    any other error is retried ``retries`` times, then recorded as a failed call."""

    def __init__(self, control: Control, limits: dict[str, Any], rcfg: dict[str, Any]) -> None:
        self.control = control
        self.lim = limits
        self.rcfg = rcfg
        self.down_since: dict[str, float] = {}

    async def call(self, http: Any, url: str, body: dict[str, Any], name: str) -> dict[str, Any] | None:
        import httpx

        errors = 0
        while True:
            if self.control.stopped():
                return None
            try:
                r = await http.post(f"{url}/v1/chat/completions", json=body)
                self.control.account("local")
                if r.status_code in (502, 503, 504):
                    raise ConnectionRefusedError(f"HTTP {r.status_code}")
                r.raise_for_status()
                d = r.json()
                txt = d["choices"][0]["message"].get("content") or ""
                fin = d["choices"][0].get("finish_reason")
                u = d.get("usage") or {}
                pt, ct = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
                self.control.account("local", pt, ct, call=False)
                self.down_since.pop(url, None)
                return {"ok": True, "answer": txt, "finish": fin, "pt": pt, "ct": ct, "error": None}
            except (httpx.ConnectError, httpx.ConnectTimeout, ConnectionRefusedError):
                t = time.monotonic()
                since = self.down_since.setdefault(url, t)
                if t - since >= self.lim["local_unreachable_s"]:
                    self.control.stop(f"local_unreachable:{name}")
                    return None
                if not await self.control.sleep(self.lim["local_retry_s"]):
                    return None
            except Exception as e:  # noqa: BLE001
                errors += 1
                if errors > int(self.rcfg["retries"]):
                    return {"ok": False, "answer": "", "finish": None, "pt": 0, "ct": 0,
                            "error": f"{type(e).__name__}:{str(e)[:120]}"}
                if not await self.control.sleep(self.rcfg["retry_backoff_s"] * errors):
                    return None


async def run_jobs(jobs: list[tuple[str, Callable[[Any], Any]]], sems: dict[str, asyncio.Semaphore] | int,
                   control: Control, timeout: float = 900) -> None:
    """Run ``(endpoint, async fn(http))`` jobs with per-endpoint concurrency; a job
    not yet started when the run stops is skipped; in-flight jobs finish."""
    import httpx

    if isinstance(sems, int):
        n = sems
        sems = defaultdict(lambda: asyncio.Semaphore(n))
    async with httpx.AsyncClient(timeout=timeout) as http:
        async def one(ep: str, fn: Callable[[Any], Any]) -> None:
            async with sems[ep]:
                if control.stopped():
                    return
                await fn(http)

        await asyncio.gather(*(one(ep, fn) for ep, fn in jobs))


# ------------------------------------------------------------------- parsing

#: label words -> label (``abstain`` / ``abstains`` / ``abstention`` count as ``abstained``)
LABEL_WORDS = {"correct": "correct", "partial": "partial", "incorrect": "incorrect", "abstained": "abstained",
               "abstain": "abstained", "abstains": "abstained", "abstention": "abstained"}
_LABEL_RE = "|".join(sorted(LABEL_WORDS, key=len, reverse=True))
_BOLD = re.compile(rf"\*\*\s*({_LABEL_RE})\s*[.:!]?\s*\*\*", re.I)
_GRADE = re.compile(rf"\bgrade\s*[:=\-]\s*\**\s*({_LABEL_RE})\b", re.I)
_WORDS = re.compile(rf"\b({_LABEL_RE})\b", re.I)
_TOKEN = re.compile(r"[a-z']+")
#: plus any word ending in n't
NEGATORS = frozenset({"not", "never", "no", "nor", "neither", "hardly", "cannot"})
QUALIFIERS = frozenset({"partially", "partly", "mostly", "largely", "somewhat", "half", "nearly", "almost", "only"})
NEGATION_WINDOW = 3


def parse_grade(text: str) -> tuple[str, str]:
    """The part-4 grade parser: (label, rule).

    1. a bold label (``**Correct**``) or ``Grade: <label>``: when they name exactly
       one distinct label, it wins (rule ``bold_or_grade``); two distinct ones are
       ``unparseable``;
    2. otherwise, exactly one distinct label word anywhere (whole words,
       case-insensitive; ``correct`` does not match inside ``incorrect``; the
       synonyms of :data:`LABEL_WORDS` map to their label), accepted only if no
       occurrence of it is negated or qualified: a word of :data:`NEGATORS` (or one
       ending in ``n't``) or of :data:`QUALIFIERS` within the
       :data:`NEGATION_WINDOW` words before it (rule ``one_word``);
    3. otherwise ``unparseable`` (rule ``none``, ``several``, ``negated`` or ``qualified``).
    """
    marked = ({LABEL_WORDS[m.group(1).lower()] for m in _BOLD.finditer(text)}
              | {LABEL_WORDS[m.group(1).lower()] for m in _GRADE.finditer(text)})
    if len(marked) == 1:
        return next(iter(marked)), "bold_or_grade"
    if len(marked) > 1:
        return "unparseable", "several"
    hits = list(_WORDS.finditer(text))
    distinct = {LABEL_WORDS[m.group(1).lower()] for m in hits}
    if not distinct:
        return "unparseable", "none"
    if len(distinct) > 1:
        return "unparseable", "several"
    for m in hits:
        before = _TOKEN.findall(text[:m.start()].lower().replace("’", "'"))[-NEGATION_WINDOW:]
        if any(w in NEGATORS or w.endswith("n't") for w in before):
            return "unparseable", "negated"
        if any(w in QUALIFIERS for w in before):
            return "unparseable", "qualified"
    return next(iter(distinct)), "one_word"


# --------------------------------------------------------------- truncation

def truncate_at_sentence(text: str, remaining: int, ntok: Callable[[str], int]) -> str:
    """The longest prefix of ``text`` ending at a sentence boundary (the
    harness's ``_SENT_END`` rule, trailing whitespace dropped) whose token
    count is <= ``remaining``; "" when none fits."""
    if remaining <= 0:
        return ""
    ends = sorted({len(text[:m.end()].rstrip()) for m in dt._SENT_END.finditer(text)} - {0})
    lo, hi, best = 0, len(ends) - 1, -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if ntok(text[:ends[mid]]) <= remaining:
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    while best >= 0 and ntok(text[:ends[best]]) > remaining:  # guard a non-monotone count
        best -= 1
    return text[:ends[best]] if best >= 0 else ""


@dataclass
class Context:
    chunks: list[C]          # as delivered (the last one possibly truncated)
    ids: list[str]
    ctx_tokens: int          # realised chunk-text tokens
    truncated: bool
    #: the overflowing chunk had no sentence boundary within a remaining budget > 0
    #: (a budget filled exactly is not flagged)
    truncated_empty: bool
    #: budget - ctx_tokens for a B<n> condition (None for top-k)
    shortfall: int | None = None


def budget_context(cids: list[str], budget: int, chunk_of: Callable[[str], C],
                   ntok: Callable[[str], int]) -> Context:
    """B<n>: whole chunks in reranked order while the running chunk-text token
    total stays <= ``budget``; the chunk that would exceed it is truncated at
    the last sentence boundary within the remaining budget and added; then stop."""
    out: list[C] = []
    tot = 0
    trunc = trunc_empty = False
    for cid in cids:
        c = chunk_of(cid)
        if tot + c.tokens <= budget:
            out.append(c)
            tot += c.tokens
            continue
        t = truncate_at_sentence(c.content, budget - tot, ntok)
        if t:
            tc = ntok(t)
            out.append(replace(c, content=t, tokens=tc))
            tot += tc
            trunc = True
        else:
            trunc_empty = budget - tot > 0
        break
    return Context(out, [c.id for c in out], tot, trunc, trunc_empty, budget - tot)


def top_context(cids: list[str], k: int, chunk_of: Callable[[str], C]) -> Context:
    cs = [chunk_of(c) for c in cids[:k]]
    return Context(cs, [c.id for c in cs], sum(c.tokens for c in cs), False, False, None)


# --------------------------------------------------------------- allocation

def quote_spans(quote: str, ntext: str, pos: list[int]) -> list[tuple[int, int]]:
    """Every raw (start, end) span of ``quote`` under the gold normalisation."""
    nq = norm(quote)
    out: list[tuple[int, int]] = []
    if not nq:
        return out
    i = ntext.find(nq)
    while i >= 0:
        out.append((pos[i], pos[i + len(nq) - 1] + 1))
        i = ntext.find(nq, i + 1)
    return out


def prior_spans(pages: dict[str, dict[str, Any]], files: list[Path]) -> tuple[dict[str, list[tuple[int, int]]], Counter]:
    """Spans of every valid gold quote of the earlier parts, per page. A quote not
    found on its page excludes its whole recorded window instead."""
    spans: dict[str, list[tuple[int, int]]] = defaultdict(list)
    st: Counter[str] = Counter()
    nm: dict[str, tuple[str, list[int]]] = {}
    for f in files:
        if not f.exists():
            raise SystemExit(f"part4: prior question file not found: {f.name}")
        for r in read_jsonl(f):
            if r.get("status") != "valid":
                continue
            did = str(r.get("doc_id"))
            if did not in pages:
                st["prior_page_missing"] += 1
                continue
            if did not in nm:
                nm[did] = dt.norm_map(pages[did]["text"])
            for fld in ("answer_quote", "quote_a", "quote_b"):
                q = r.get(fld)
                if not q:
                    continue
                st["prior_quotes"] += 1
                sp = quote_spans(q, *nm[did])
                if sp:
                    spans[did] += sp
                else:
                    st["prior_quote_unlocated"] += 1
                    for w in ("p", "a", "b"):
                        if isinstance(r.get(w), list):
                            spans[did].append((int(r[w][0]), int(r[w][1])))
                    if "p_start" in r:
                        spans[did].append((int(r["p_start"]), int(r["p_end"])))
    return dict(spans), st


def fresh(s: int, e: int, spans: list[tuple[int, int]]) -> bool:
    return all(e <= a or s >= b for a, b in spans)


def page_order(pages: dict[str, dict[str, Any]]) -> list[str]:
    """Pages by stratum (config order; unknown strata last) then doc id."""
    rank = {s: i for i, s in enumerate(dt.STRATA)}
    return sorted(pages, key=lambda d: (rank.get(pages[d]["stratum"], len(rank)), d))


def allocate(pages: dict[str, dict[str, Any]], page_tokens: dict[str, int],
             spans: dict[str, list[tuple[int, int]]], p4: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The S and M generation attempts (seeded), with the fresh-window exclusion."""
    seed, tries = p4["seed"], int(p4["max_draw_tries"])
    order = page_order(pages)
    st: Counter[str] = Counter()
    items: list[dict[str, Any]] = []

    def draw(rng: random.Random, text: str, chars: int, sp: list[tuple[int, int]],
             ok: Callable[[int], bool] = lambda s: True) -> int | None:
        n = len(text)
        for _ in range(tries):
            s = 0 if n <= chars else dt.snap_sentence(text, rng.randint(0, n - chars))
            if fresh(s, min(n, s + chars), sp) and ok(s):
                return s
            st["rejected_draws"] += 1
            if n <= chars:
                return None
        return None

    # ---- M: every page of >= min_page_tokens, attempts_per_page new (A, B) pairs.
    mc = p4["M"]
    rng = random.Random(f"{seed}|M")
    m_pages = [d for d in order if page_tokens[d] >= mc["min_page_tokens"]]
    for pi, did in enumerate(m_pages):
        text = pages[did]["text"]
        n, sp, chars = len(text), spans.get(did, []), int(mc["chars"])
        for k in range(int(mc["attempts_per_page"])):
            st["M_planned"] += 1
            a = None
            for _ in range(tries):
                x = dt.snap_sentence(text, rng.randint(0, max(0, n - chars)))
                if fresh(x, min(n, x + chars), sp):
                    a = x
                    break
                st["M_a_rejected"] += 1
            if a is None:
                st["M_no_fresh_a"] += 1
                continue
            b = None
            for _ in range(tries):
                got = dt.draw_b(rng, text, a, chars, int(mc["gap"]))
                if got is None:
                    st["M_no_room"] += 1
                    break
                y, unsnapped = got
                if fresh(y, min(n, y + chars), sp):
                    b = y
                    st["M_b_unsnapped"] += int(unsnapped)
                    break
                st["M_b_rejected"] += 1
            else:
                st["M_no_fresh_b"] += 1
            if b is None:
                continue
            items.append({"id": f"M{pi:03d}k{k}", "set": "M", "doc_id": did, "a": [a, min(n, a + chars)],
                          "b": [b, min(n, b + chars)]})

    # ---- S: pages with >= min_live_chunks; one passage each, then a second on the longest pages.
    sc = p4["S"]
    rng = random.Random(f"{seed}|S")
    chars, cap = int(sc["chars"]), int(sc["max_attempts"])
    s_pages = [d for d in order if len(pages[d]["rows"]) >= sc["min_live_chunks"]]
    log(f"part4 S: {len(s_pages)} eligible pages (>= {sc['min_live_chunks']} live chunks)")
    first_pass = s_pages
    if len(s_pages) > cap:
        by: dict[str, list[str]] = defaultdict(list)
        for d in s_pages:
            by[pages[d]["stratum"]].append(d)
        keep = dt.strat_sample(dict(by), cap, seed)
        first_pass = [d for d in s_pages if d in keep]
        st["S_first_pass_sampled_down"] = len(s_pages) - len(first_pass)
    s_index = {d: i for i, d in enumerate(s_pages)}
    first: dict[str, int] = {}
    for did in first_pass:
        text = pages[did]["text"]
        s = draw(rng, text, chars, spans.get(did, []))
        if s is None:
            st["S_no_fresh"] += 1
            continue
        first[did] = s
        items.append({"id": f"S{s_index[did]:03d}p0", "set": "S", "doc_id": did,
                      "p": [s, min(len(text), s + chars)]})
    n_s = sum(1 for it in items if it["set"] == "S")
    for did in sorted(first, key=lambda d: (-page_tokens[d], d)):
        if n_s >= cap:
            break
        text = pages[did]["text"]
        if len(text) < 2 * chars:
            st["S_second_no_room"] += 1
            continue
        a0 = first[did]
        s = draw(rng, text, chars, spans.get(did, []), lambda x, a0=a0: x + chars <= a0 or x >= a0 + chars)
        if s is None:
            st["S_second_no_fresh"] += 1
            continue
        items.append({"id": f"S{s_index[did]:03d}p1", "set": "S", "doc_id": did,
                      "p": [s, min(len(text), s + chars)]})
        n_s += 1
    meta = {"M_pages": len(m_pages), "S_eligible_pages": len(s_pages), "sampling": dict(st),
            "attempts": {"M": sum(it["set"] == "M" for it in items), "S": n_s}}
    return items, meta


def draw_subsets(valid: list[dict[str, Any]], p4: dict[str, Any]) -> dict[str, set[str]]:
    """The grading subsets of question ids, each stratified by set x stratum
    (largest-remainder quotas, :func:`deployed_docs_trial.strat_sample`) with its own
    derived seed, as :func:`allocate` derives ``{seed}|M`` / ``{seed}|S``:
    ``primary_k10`` (``{seed}|k10``) and ``secondary`` (``{seed}|secondary``)."""
    by: dict[str, list[str]] = defaultdict(list)
    for r in valid:
        by[f"{r['set']}|{r['stratum']}"].append(r["record_id"])
    n, seed = len(valid), int(p4["seed"])
    return {"primary_k10": dt.strat_sample(dict(by), int(round(n * float(p4["grader_primary"]["k10_fraction"]))),
                                           f"{seed}|k10"),
            "secondary": dt.strat_sample(dict(by), int(round(n * float(p4["grader_secondary"]["fraction"]))),
                                         f"{seed}|secondary")}


def retest_sample(keys: list[str], n: int, seed: int) -> list[str]:
    """The test-retest sample: ``n`` of the sorted grade keys, seed ``{seed}|retest``."""
    return sorted(random.Random(f"{seed}|retest").sample(sorted(keys), n))


def gen_messages(it: dict[str, Any], text: str) -> list[dict[str, str]]:
    if it["set"] == "M":
        u = dt.M_USER.format(a=text[it["a"][0]:it["a"][1]], b=text[it["b"][0]:it["b"][1]])
    else:
        u = dt.GEN_USER.format(passage=text[it["p"][0]:it["p"][1]])
    return [{"role": "system", "content": dt.GEN_SYSTEM}, {"role": "user", "content": u}]


def windows(rec: dict[str, Any]) -> list[tuple[int, int]]:
    return [tuple(rec[w]) for w in ("a", "b", "p") if w in rec]  # type: ignore[misc]


def overlaps(x: tuple[int, int], y: tuple[int, int]) -> bool:
    return x[0] < y[1] and y[0] < x[1]


def build_questions(items: list[dict[str, Any]], gen: Store, pages: dict[str, dict[str, Any]],
                    page_tokens: dict[str, int], p4: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Generation outcomes -> question records (validation + the duplicate rule)."""
    g_tag, f_tag = p4["generator"]["tag"], p4["generator_fallback"]["tag"]
    out: list[dict[str, Any]] = []
    st: dict[str, Counter] = {"M": Counter(), "S": Counter()}
    usage: Counter[str] = Counter()
    seen: dict[str, set[str]] = defaultdict(set)
    nm: dict[str, tuple[str, list[int]]] = {}
    for it in items:
        page = pages[it["doc_id"]]
        text = page["text"]
        r1 = gen.get(ckey("generation", it["id"], role=g_tag)) or {}
        r2 = gen.get(ckey("generation", it["id"], role=f_tag)) or {}
        for r in (r1, r2):
            usage["prompt_tokens"] += int(r.get("pt") or 0)
            usage["completion_tokens"] += int(r.get("ct") or 0)
        rec: dict[str, Any] = {"record_id": it["id"], **{k: v for k, v in it.items() if k != "id"},
                               "stratum": page["stratum"], "filename": page["filename"],
                               "page_tokens": page_tokens[it["doc_id"]]}
        if r1.get("status") == "ok":
            src, rec["gen"] = r1, g_tag
        elif r1.get("status") in FALLBACK_ON and r2.get("status") == "ok":
            src, rec["gen"] = r2, f_tag
        else:
            rec["gen"] = f_tag if r1.get("status") in FALLBACK_ON else g_tag
            rec["status"] = "gen_no_label" if r2.get("status") == "no_label" else "gen_empty"
            st[it["set"]][rec["status"]] += 1
            out.append(rec)
            continue
        rec["finish"] = src.get("finish")
        d = dt.parse_m(src["text"]) if it["set"] == "M" else dt.parse_json_answer(src["text"])
        if d is None:
            rec["status"] = "unparseable"
        elif it["set"] == "S":
            q, quote = d["question"].strip(), d["answer_quote"]
            rec.update(question=q, answer_quote=quote, gold=[quote])
            rec["status"] = dt.check_single(q, quote, text)
            if rec["status"] == "valid":
                rec["echo"] = dt.echo(q, text[it["p"][0]:it["p"][1]])
        else:
            q, qa, qb = d["question"].strip(), d["quote_a"], d["quote_b"]
            rec.update(question=q, quote_a=qa, quote_b=qb, gold=[qa, qb])

            def nm_of(did: str = it["doc_id"], text: str = text) -> tuple[str, list[int]]:
                if did not in nm:
                    nm[did] = dt.norm_map(text)
                return nm[did]

            status, gap = dt.check_pair(q, qa, qb, text, tuple(it["a"]), tuple(it["b"]), nm_of, int(p4["M"]["gap"]))
            rec["status"] = status
            if gap is not None:
                rec["quote_gap"] = gap
                rec["echo"] = max(dt.echo(q, text[it["a"][0]:it["a"][1]]), dt.echo(q, text[it["b"][0]:it["b"][1]]))
        if rec["status"] == "valid":
            nq = norm(rec["question"])
            if nq in seen[it["doc_id"]]:
                rec["status"] = "duplicate"
            seen[it["doc_id"]].add(nq)
        st[it["set"]][rec["status"]] += 1
        out.append(rec)
    valid = [r for r in out if r["status"] == "valid"]
    near: Counter[str] = Counter()
    by_page: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in valid:
        by_page[r["doc_id"]].append(r)
    for rs in by_page.values():
        for i, x in enumerate(rs):
            for y in rs[i + 1:]:
                if x["set"] == y["set"] and any(overlaps(u, v) for u in windows(x) for v in windows(y)):
                    near[x["set"]] += 1
    meta = {"status": {k: dict(v) for k, v in st.items()}, "usage": dict(usage),
            "valid": {s: sum(r["set"] == s for r in valid) for s in ("M", "S")},
            "near_duplicate_pairs": dict(near),
            "gen_fallback": {s: sum(r["set"] == s and r["gen"] == f_tag for r in valid) for s in ("M", "S")}}
    return out, meta


# ------------------------------------------------------------------ analysis

PROCEDURES = ("holm4", "fixed_sequence")
#: labels that exclude a question from a contrast (either side); the sensitivity reading scores them 0
MISSING = frozenset({"no_label", "unparseable", "reader_failed"})
ENDPOINTS: dict[str, frozenset[str]] = {"correct": frozenset({"correct"}),
                                        "correct_or_partial": frozenset({"correct", "partial"}),
                                        "abstained": frozenset({"abstained"})}


def paired_vectors(per_item: dict[str, Any], questions: dict[str, dict[str, Any]], qids: list[str], arm_a: str,
                   arm_b: str, cond: str, reader: str, tag: str, endpoint: str = "correct",
                   missing: str = "exclude") -> tuple[list[str], list[int], list[int], dict[str, int]]:
    """Page ids and the paired 0/1 outcomes of arm A and arm B for one reader and grader.

    A question outside the grader's coverage (label None on either side) is not in
    the contrast. A question with a :data:`MISSING` label on either side is excluded
    (``missing="exclude"``, counted) or kept with that side scored 0
    (``missing="as_not_correct"``, the sensitivity reading)."""
    hit = ENDPOINTS[endpoint]
    pages: list[str] = []
    a: list[int] = []
    b: list[int] = []
    n_missing = n_cov = 0
    for q in qids:
        la = per_item[q][arm_a][cond]["readers"][reader].get(tag)
        lb = per_item[q][arm_b][cond]["readers"][reader].get(tag)
        if la is None or lb is None:
            n_cov += 1
            continue
        if la in MISSING or lb in MISSING:
            n_missing += 1
            if missing == "exclude":
                continue
        pages.append(questions[q]["doc_id"])
        a.append(int(la in hit))
        b.append(int(lb in hit))
    return pages, a, b, {"excluded_missing": n_missing, "outside_coverage": n_cov}


def contrast_test(pages: list[str], a: list[int], b: list[int], kind: str, an: dict[str, Any], seed: int,
                  alternative: str = "greater") -> dict[str, Any]:
    """The part-4 test of one contrast: the page sign-flip permutation test (standard
    p: exact when <= 20 non-zero pages, else Monte-Carlo (1 + #)/(1 + n); the mid-p ``p_mid`` is
    reported alongside as a sensitivity, never decisive),
    the page-cluster bootstrap CI of the net share, McNemar as a descriptive.
    ``kind`` superiority (one-sided unless ``alternative`` says otherwise) or
    non-inferiority (shifted by +delta, one-sided)."""
    if not a:
        return {"n": 0, "p": None, "type": kind, "note": "no questions in this contrast"}
    kw = {"n_flips": int(an["n_perm"]), "n_boot": int(an["n_boot"]), "seed": seed,
          "ci_level": float(an["ci_level"])}
    if kind == "superiority":
        r = cs.contrast(pages, a, b, alternative=alternative, **kw)  # type: ignore[arg-type]
    elif kind == "noninferiority":
        r = cs.noninferiority_test(pages, a, b, float(an["delta"]), alpha=float(an["alpha"]), **kw)
        r["p_mcnemar_two_sided"] = cs.mcnemar_exact(r["gained"], r["lost"], "two-sided")
        r["ci_lower_above_minus_delta"] = r.pop("reject_ci")
        r.pop("reject_perm")
    else:
        raise ValueError(f"unknown contrast type {kind!r}")
    return {"type": kind, **r}


def hypothesis_tests(per_item: dict[str, Any], questions: dict[str, dict[str, Any]], qids: list[str], cond: str,
                     tag: str, readers: list[str], an: dict[str, Any], seed: int, endpoint: str = "correct",
                     missing: str = "exclude", two_sided: bool = False) -> dict[str, Any]:
    """Every hypothesis of ``an["hypotheses"]`` per reader, on the questions ``qids``."""
    out: dict[str, Any] = {}
    for h, (arm_a, arm_b, kind) in an["hypotheses"].items():
        if two_sided:
            kind = "superiority"
        per: dict[str, Any] = {}
        for rd in readers:
            pages, a, b, cnt = paired_vectors(per_item, questions, qids, arm_a, arm_b, cond, rd, tag, endpoint, missing)
            per[rd] = {**contrast_test(pages, a, b, kind, an, seed, "two-sided" if two_sided else "greater"), **cnt}
        out[h] = {"contrast": f"{arm_a} - {arm_b}", "type": kind, "per_reader": per}
    return out


def _status(sig: list[bool], kind: str) -> str:
    if all(sig):
        return "confirmed"
    if any(sig):
        return "reader-dependent"
    return "not confirmed" if kind == "superiority" else "not shown non-inferior"


def decide(hyps: dict[str, Any], readers: list[str], an: dict[str, Any]) -> dict[str, Any]:
    """The confirmatory decision, in place: per reader ``significant``, per hypothesis ``status``.

    * ``holm4``: Holm over the hypotheses at ``alpha``, separately per reader;
    * ``fixed_sequence``: ``an["order"]``, each at one-sided ``alpha``; a step passes only
      when both readers are significant (intersection-union); the first step that does
      not pass stops the sequence and every later hypothesis is ``not reached``
      (distinct from ``not confirmed``: it was never tested).

    Either way a hypothesis is ``confirmed`` when significant under both readers and
    ``reader-dependent`` when under exactly one.

    **Mid-p is reported, never decisive.** ``status``/``confirmed``/``significant`` come
    only from the standard ``p``. Alongside, the same procedure is run on ``p_mid``
    (Holm per reader -> ``holm_mid``, or its own fixed sequence) giving
    ``significant_mid``, ``status_mid``, ``confirmed_mid`` and
    ``confirmed_only_under_mid_p`` (confirmed_mid and not confirmed)."""
    alpha, order = float(an["alpha"]), list(an["order"])

    def pv(h: str, rd: str, key: str) -> float | None:
        return hyps[h]["per_reader"][rd].get(key)

    if an["procedure"] == "holm4":
        for rd in readers:
            ps = [hyps[h]["per_reader"][rd]["p"] for h in order]
            hm = cs.holm([1.0 if p is None else p for p in ps], alpha)
            for h, padj, rej in zip(order, hm["p_adj"], hm["reject"], strict=True):
                hyps[h]["per_reader"][rd]["p_holm"] = padj
                hyps[h]["per_reader"][rd]["significant"] = bool(rej)
            # sensitivity only: Holm over the mid-p values, same family, same reader
            pm = cs.holm([1.0 if pv(h, rd, "p_mid") is None else pv(h, rd, "p_mid") for h in order], alpha)  # type: ignore[misc]
            for h, padj, rej in zip(order, pm["p_adj"], pm["reject"], strict=True):
                hyps[h]["per_reader"][rd]["holm_mid"] = padj
                hyps[h]["per_reader"][rd]["significant_mid"] = bool(rej)
        for h in order:
            hyps[h]["status"] = _status([hyps[h]["per_reader"][rd]["significant"] for rd in readers], hyps[h]["type"])
            hyps[h]["status_mid"] = _status([hyps[h]["per_reader"][rd]["significant_mid"] for rd in readers],
                                            hyps[h]["type"])
    elif an["procedure"] == "fixed_sequence":
        stopped = stopped_mid = False
        for step, h in enumerate(order, 1):
            hyps[h]["sequence_step"] = step
            if stopped:
                hyps[h]["status"] = "not reached"
                for rd in readers:
                    hyps[h]["per_reader"][rd]["significant"] = None
            else:
                for rd in readers:
                    p = hyps[h]["per_reader"][rd]["p"]
                    hyps[h]["per_reader"][rd]["significant"] = p is not None and p <= alpha
                hyps[h]["status"] = _status([hyps[h]["per_reader"][rd]["significant"] for rd in readers],
                                            hyps[h]["type"])
                stopped = hyps[h]["status"] != "confirmed"
            # the mid-p sequence, reported separately: its own stopping rule
            if stopped_mid:
                hyps[h]["status_mid"] = "not reached"
                for rd in readers:
                    hyps[h]["per_reader"][rd]["significant_mid"] = None
            else:
                for rd in readers:
                    p = pv(h, rd, "p_mid")
                    hyps[h]["per_reader"][rd]["significant_mid"] = p is not None and p <= alpha
                hyps[h]["status_mid"] = _status([hyps[h]["per_reader"][rd]["significant_mid"] for rd in readers],
                                                hyps[h]["type"])
                stopped_mid = hyps[h]["status_mid"] != "confirmed"
    else:
        raise ValueError(f"unknown procedure {an['procedure']!r}")
    for h in order:
        hyps[h]["confirmed"] = hyps[h]["status"] == "confirmed"
        hyps[h]["confirmed_mid"] = hyps[h]["status_mid"] == "confirmed"
        hyps[h]["confirmed_only_under_mid_p"] = hyps[h]["confirmed_mid"] and not hyps[h]["confirmed"]
    return hyps


def readout(hyps: dict[str, Any], readers: list[str], alpha: float) -> dict[str, Any]:
    """A secondary block: unadjusted, outside the family; ``readers`` both / reader-dependent / neither."""
    for h in hyps.values():
        sig = []
        for rd in readers:
            p = h["per_reader"][rd]["p"]
            h["per_reader"][rd]["significant_unadjusted"] = s = p is not None and p <= alpha
            sig.append(s)
        h["readers"] = "both" if all(sig) else ("reader-dependent" if any(sig) else "neither")
    return {"note": "secondary: same tests, outside the confirmatory family, unadjusted", "hypotheses": hyps}


def analyse_part4(per_item: dict[str, Any], questions: dict[str, dict[str, Any]], *, conditions: list[str],
                  readers: list[str], primary: str, secondary: str, analysis: dict[str, Any], seed: int,
                  fallback_tag: str, arms: list[str]) -> dict[str, Any]:
    """The part-4 analysis block, from ``per_item`` (as written by :meth:`Run.write_results`).

    * ``per_item[qid][arm][condition]`` -> ``{"found", ..., "readers": {reader: {<grader
      tag>: label | None, ...}}}``; None = outside that grader's coverage;
    * ``questions[qid]`` -> ``{"set", "stratum", "doc_id" (the page: the exchangeable
      cluster), "gen"}``.

    **Family:** ``analysis["hypotheses"]`` on ``family_set`` under ``family_condition``
    with the primary grader, per reader, decided by ``analysis["procedure"]``
    (:func:`decide`). Reported with all questions, without the ``gen=<fallback>``
    questions, and with missing labels scored as not correct (sensitivity).
    **Secondary** (same tests, unadjusted, outside the family; :func:`readout`): the
    other conditions (k10 only on the primary grader's k10 subset, by coverage), the
    other sets, the secondary grader on its subset, correct+partial, abstentions
    (two-sided), per-stratum nets, found@condition (reader-independent).
    """
    an = analysis
    fam_set, fam_cond, alpha = an["family_set"], an["family_condition"], float(an["alpha"])
    by_set: dict[str, list[str]] = defaultdict(list)
    for q in sorted(questions):
        by_set[questions[q]["set"]].append(q)
    fam_q = by_set.get(fam_set, [])

    def fam(qids: list[str], **kw: Any) -> dict[str, Any]:
        return {"n_questions": len(qids), "n_pages": len({questions[q]["doc_id"] for q in qids}),
                "hypotheses": decide(hypothesis_tests(per_item, questions, qids, fam_cond, primary, readers, an,
                                                      seed, **kw), readers, an)}

    def sec(qids: list[str], cond: str = fam_cond, tag: str = primary, **kw: Any) -> dict[str, Any]:
        return {"n_questions": len(qids), "condition": cond, "grader": tag,
                **readout(hypothesis_tests(per_item, questions, qids, cond, tag, readers, an, seed, **kw),
                          readers, alpha)}

    no_fb = [q for q in fam_q if questions[q].get("gen") != fallback_tag]
    out: dict[str, Any] = {
        "status": "complete", "procedure": an["procedure"], "order": list(an["order"]),
        "alpha": alpha, "delta": float(an["delta"]), "ci_level": float(an["ci_level"]),
        "n_perm": int(an["n_perm"]), "n_boot": int(an["n_boot"]), "seed": seed,
        "test": "page sign-flip permutation (standard p; exact when <= 20 non-zero pages), "
                "page-cluster bootstrap percentile CI, McNemar descriptive",
        "confirmation_rule": "significant under both readers (intersection-union)",
        "family": {"set": fam_set, "condition": fam_cond, "grader": primary, **fam(fam_q)},
        "family_without_gen_fallback": {"excluded_gen": fallback_tag, "n_excluded": len(fam_q) - len(no_fb),
                                        **fam(no_fb)},
        "family_missing_as_not_correct": fam(fam_q, missing="as_not_correct"),
    }
    secondary_blocks: dict[str, Any] = {}
    for cond in conditions:
        if cond != fam_cond:
            secondary_blocks[f"{fam_set}|{cond}"] = sec(fam_q, cond)
    for s_, qids in sorted(by_set.items()):
        if s_ != fam_set:
            secondary_blocks[f"{s_}|{fam_cond}"] = sec(qids)
    secondary_blocks["secondary_grader"] = sec(fam_q, tag=secondary)
    secondary_blocks["correct_or_partial"] = sec(fam_q, endpoint="correct_or_partial")
    abst = sec(fam_q, endpoint="abstained", two_sided=True)
    abst["counts"] = {rd: {arm: sum(per_item[q][arm][fam_cond]["readers"][rd].get(primary) == "abstained"
                                    for q in fam_q) for arm in arms} for rd in readers}
    secondary_blocks["abstentions"] = abst
    strata: dict[str, list[str]] = defaultdict(list)
    for q in fam_q:
        strata[questions[q]["stratum"]].append(q)
    secondary_blocks["per_stratum"] = {st: sec(qs) for st, qs in sorted(strata.items())}
    found: dict[str, Any] = {}
    for cond in conditions:
        rate = {arm: (round(sum(bool(per_item[q][arm][cond]["found"]) for q in fam_q) / len(fam_q), 4)
                      if fam_q else None) for arm in arms}
        tests = {}
        for h, (arm_a, arm_b, kind) in an["hypotheses"].items():
            a = [int(bool(per_item[q][arm_a][cond]["found"])) for q in fam_q]
            b = [int(bool(per_item[q][arm_b][cond]["found"])) for q in fam_q]
            tests[h] = contrast_test([questions[q]["doc_id"] for q in fam_q], a, b, kind, an, seed)
        found[cond] = {"rate": rate, "hypotheses": tests}
    secondary_blocks["found"] = {"note": "retrieval: every gold quote in the delivered context; reader-independent",
                                 **found}
    out["secondary"] = secondary_blocks
    return out


def power_table(seed: int = 261025, n_pages: int = 76, n_questions: int = 315, delta: float = 0.075,
                alpha: float = 0.05, n_sims: int = 2000, n_flips: int = 2000) -> dict[str, Any]:
    """The part-4 power table (:func:`chunktrial_stats.simulate_power`, one seed for every cell).

    ``n_questions`` spread evenly over ``n_pages``; discordance .20/.25/.30/.35;
    H1 (superiority) at a discordant share of .3/.4/.5; H2-H4 (non-inferiority at
    ``delta``) at a true net share of 0/-.02/-.04; direction page-ICC 0 and 0.1;
    ``holm4`` at alpha/4 (the Holm worst case) and ``fixed_sequence`` at alpha. Per
    reader, and joint over two readers as the product (independent readers; positively
    correlated readers make the real joint power higher)."""
    layout = cs.even_split(n_questions, n_pages)
    rows: list[dict[str, Any]] = []
    for proc, n_family in (("holm4", 4), ("fixed_sequence", 1)):
        for share_icc in (0.0, 0.1):
            for disc in (0.20, 0.25, 0.30, 0.35):
                cells = ([("H1", "superiority", share, None, 0.0) for share in (0.3, 0.4, 0.5)]
                         + [("H2-H4", "noninferiority", net / disc, net, delta) for net in (0.0, -0.02, -0.04)])
                for hyp, kind, share, net, dlt in cells:
                    r = cs.simulate_power(n_pages, layout, disc, share, share_icc=share_icc, alpha=alpha,
                                          n_family=n_family, delta=dlt, n_sims=n_sims, n_flips=n_flips, seed=seed)
                    rows.append({"procedure": proc, "hypothesis": hyp, "type": kind, "discordance": disc,
                                 "discordant_share": round(share, 6), "true_net_share": round(disc * share, 6),
                                 "share_icc": share_icc, "alpha_test": r["alpha_test"],
                                 "power_per_reader": r["power"], "mc_se": round(r["mc_se"], 4),
                                 "power_joint_two_readers": round(cs.joint_power(r["power"], r["power"]), 4),
                                 **({"ni_true_net": net} if net is not None else {})})
    return {"schema": "part4-power/1", "n_pages": n_pages, "n_questions": n_questions,
            "questions_per_page": layout, "delta": delta, "alpha": alpha, "n_sims": n_sims, "n_flips": n_flips,
            "seed": seed, "discordance_icc": 0.0,
            "note": ("power of one test; under fixed_sequence a later hypothesis is tested only when every "
                     "earlier one passed, so its power is at most the product along the sequence"),
            "rows": rows}


# --------------------------------------------------------------------- env

@dataclass
class Env:
    """The seams between the pipeline and the world (production: :func:`production_env`)."""
    load_pages: Callable[[], tuple[dict[str, dict[str, Any]], dict[str, Any]]]
    build_arms: Callable[[dict[str, dict[str, Any]], list[str]], tuple[dict[str, list[C]], dict[str, int]]]
    ntok: Callable[[str], int]
    #: (arm, its chunks, question records) -> {question id: reranked chunk ids}
    retrieve: Callable[[str, list[C], list[dict[str, Any]]], dict[str, list[str]]]
    provenance: Callable[[], dict[str, Any]]


def production_env(out: Path, embed_urls: str, rerank_url: str) -> Env:
    from ragstack.ingestion.tokenization import make_token_counter
    from ragstack.provenance import experiment_provenance

    urls = [u for u in embed_urls.split(",") if u]
    for u in [*urls, rerank_url]:
        dt.check_url(u)
    tok_holder: dict[str, Any] = {}

    def ntok(s: str) -> int:
        if "tok" not in tok_holder:
            tok_holder["tok"] = make_token_counter("hf", model=sat.SFR_MODEL)._tokenizer()
        return len(tok_holder["tok"]([s], add_special_tokens=False)["input_ids"][0])

    def load_pages() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        return dt.pages_from_index(dt.read_index())

    def build(pages: dict[str, dict[str, Any]], arms: list[str]) -> tuple[dict[str, list[C]], dict[str, int]]:
        built, ameta = dt.build_arms(pages)
        built.update(dt.build_sem_arms(out, pages, urls))
        return {a: built[a] for a in arms}, ameta["page_tokens"]

    def retrieve(arm: str, chunks: list[C], recs: list[dict[str, Any]]) -> dict[str, list[str]]:
        records = [{"record_id": r["record_id"], "pair": r["record_id"], "kind": r["set"], "query": r["question"]}
                   for r in recs]
        pairs = {r["record_id"]: {"expected": {"quote": r["gold"][0]}} for r in recs}
        su = sat.Setup(arms={arm: chunks}, pools={dt.POOL: set()}, records=records,
                       gold=sat.Gold(records, pairs), gold_fold=sat.Gold(records, pairs, sat.fold),
                       rec_paper={r["record_id"]: "" for r in recs}, jats_docs=set())
        ns = argparse.Namespace(embed_urls=embed_urls, embed_model=sat.SFR_MODEL, rerank_url=rerank_url,
                                out_dir=str(out))
        sat.score_config = dt._score_with_lists
        try:
            res = asyncio.run(sat.run_configs(su, [(arm, "rules", 0, dt.POOL, 1.0)], ns,
                                              {arm: sat.ArmSpec(arm, False, False, False)}))
        finally:
            sat.score_config = dt._orig_score
        per = res[sat.config_name(arm, "rules", 0, dt.POOL, 1.0)]
        return {r["record_id"]: per[r["record_id"]]["rr_list"] for r in recs}

    return Env(load_pages, build, ntok, retrieve, lambda: experiment_provenance(repo=str(sat._REPO_PY.parent)))


# ------------------------------------------------------------------- the run

class Run:
    def __init__(self, cfg_path: Path, out: Path, env: Env, resume: bool,
                 batch: dict[str, int | None] | None = None) -> None:
        self.cfg = dt.load_private_config(str(cfg_path))
        self.p4 = part4_config(self.cfg)
        for k, v in (batch or {}).items():
            if v is not None:
                self.p4["limits"][k] = v
        self.out = out
        self.env = env
        self.resume = resume
        self.files = self.p4["files"]
        self.config_sha = file_sha(cfg_path)
        prereg = out / self.files["prereg"]
        if not prereg.is_file():
            raise SystemExit(f"part4: prereg not found in the output directory: {self.files['prereg']}")
        self.prereg_sha = file_sha(prereg)
        self.state_path = out / self.files["state"]
        self.planned: dict[str, int | None] = dict.fromkeys(STEPS)
        self.step = "setup"
        self.started = now_iso()
        self.readers = {r: dt.K10_READERS[r] for r in self.p4["readers"]}
        for _, (url, _, _, _) in self.readers.items():
            dt.check_url(url)
        dt.check_url(dt.OPUS_URL)

    def path(self, key: str, tag: str | None = None) -> Path:
        name = self.files[key]
        return self.out / (name.format(tag=tag) if tag else name)

    # ------------------------------------------------------------ state
    def write_state(self, status: str, reason: str | None) -> None:
        segs = list(self.prev_state.get("segments", [])) if self.prev_state else []
        segs.append(self.segment(status, reason))
        write_json_atomic(self.state_path, {
            "schema": STATE_SCHEMA, "status": status, "step": self.step, "reason": reason,
            "counts": self.progress(), "totals": self.control.totals(),
            "commit": self.commit, "prereg_sha256": self.prereg_sha, "config_sha256": self.config_sha,
            "segments": segs, "updated": now_iso()})

    def segment(self, status: str, reason: str | None) -> dict[str, Any]:
        return {"started": self.started, "ended": now_iso(), "end": status, "reason": reason, "step": self.step,
                "totals_since_resume": dict(self.control.seg), "resumed": self.resume,
                "limits": {k: self.p4["limits"][k] for k in ("output_token_cap", "batch_calls",
                                                              "batch_input_tokens", "batch_output_tokens")},
                **self.seg_extra}

    def progress(self) -> dict[str, dict[str, int | None]]:
        done = {"generation": len(self.gen_done()) if hasattr(self, "items") else None,
                "retrieval": len(self.ret.rows) if hasattr(self, "ret") else None,
                "readers": len(self.ans.rows) if hasattr(self, "ans") else None,
                "grading": sum(len(s.rows) for s in self.grades.values()) if hasattr(self, "grades") else None,
                "retest": len(self.retest_store.rows) if hasattr(self, "retest_store") else None}
        return {s: {"done": done[s], "remaining": (None if self.planned[s] is None or done[s] is None
                                                   else max(0, self.planned[s] - done[s]))} for s in STEPS}

    # ------------------------------------------------------------ main
    def execute(self, export_human: Path | None = None) -> int:
        self.prev_state = json.loads(self.state_path.read_text()) if self.state_path.exists() else None
        prov = self.env.provenance()
        self.prov = prov
        self.commit = prov.get("commit")
        self.seg_extra: dict[str, Any] = {}
        prev_tot = None
        if self.resume:
            st = self.prev_state
            if st is None:
                log("part4 --resume: no state file")
                return EXIT_HARD_STOP
            if st.get("status") == "hard_stop":
                log(f"part4 --resume refused: the run hard-stopped ({st.get('reason')}); a new decision is needed")
                return EXIT_HARD_STOP
            bad = [k for k, v in (("commit", self.commit), ("prereg_sha256", self.prereg_sha),
                                  ("config_sha256", self.config_sha)) if st.get(k) != v]
            if bad:
                log(f"part4 --resume refused (hard stop): {', '.join(bad)} differ from {self.files['state']}")
                return EXIT_HARD_STOP
            prev_tot = st.get("totals", {}).get("since_start")
        elif self.prev_state is not None:
            log(f"part4: {self.files['state']} exists; continue with --resume (or move the outputs away)")
            return EXIT_HARD_STOP
        frozen = self.files.get("prereg_sha256")
        self.control = Control(self.p4["limits"], self.out / self.files["pause_file"], prev_tot)
        if frozen and frozen != self.prereg_sha:
            self.write_state("hard_stop", "prereg_sha_mismatch")
            log("part4: the prereg file does not match the frozen sha256 (hard stop)")
            return EXIT_HARD_STOP
        old = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, lambda *_: self.control.stop("sigterm"))
        try:
            self.write_state("running", None)
            self.pipeline(export_human)
        except Pause as p:
            self.write_state("paused", p.reason)
            t = self.control.totals()
            notice(f"PAUSED at step {self.step} ({p.reason}); totals since start {json.dumps(t['since_start'])}; "
                   f"since last resume {json.dumps(t['since_resume'])}; resume with: part4 --resume")
            return EXIT_PAUSED
        except HardStop as h:
            self.write_state("hard_stop", h.reason)
            log(f"part4 HARD STOP at step {self.step}: {h.reason}")
            return EXIT_HARD_STOP
        except Exception as e:  # noqa: BLE001 - any unexpected error: record it, exit 1, resumable
            reason = f"{type(e).__name__}: {str(e)[:200]}"
            self.write_state("crashed", reason)
            t = self.control.totals()
            log(f"part4 CRASHED at step {self.step}: {reason}")
            notice(f"CRASHED at step {self.step} ({type(e).__name__}); totals since start "
                   f"{json.dumps(t['since_start'])}; since last resume {json.dumps(t['since_resume'])}; "
                   f"after fixing the cause, resume with: part4 --resume")
            return EXIT_HARD_STOP
        finally:
            signal.signal(signal.SIGTERM, old)
        self.write_state("complete", None)
        t = self.control.totals()
        notice(f"complete; totals since start {json.dumps(t['since_start'])}")
        return EXIT_OK

    def check(self) -> None:
        if self.control.stopped():
            raise Pause(self.control.reason or "stopped")

    def pipeline(self, export_human: Path | None) -> None:
        p4 = self.p4
        self.check()
        pages, imeta = self.env.load_pages()
        man = dt.write_or_check_manifest(pages, self.out / self.files["manifest"])
        self.seg_extra["manifest"] = man.get("manifest")
        self.manifest_drift = int(man.get("drift_docs") or 0)
        arms, page_tokens = self.env.build_arms(pages, list(p4["arms"]))
        self.pages, self.arms, self.page_tokens = pages, arms, page_tokens
        self.by_id = {c.id: c for cs in arms.values() for c in cs}
        # ---- manipulation check: a gate before anything is called
        exp = p4["expected_chunks"]
        self.manip = {a: {"chunks": len(arms.get(a, [])), "expected": exp.get(a),
                          "ok": len(arms.get(a, [])) == exp.get(a)} for a in p4["arms"]}
        n_m = sum(1 for d in pages if page_tokens[d] >= int(p4["M"]["min_page_tokens"]))
        self.manip["M_pages"] = {"pages": n_m, "expected": p4["expected_m_pages"],
                                 "ok": n_m == p4["expected_m_pages"]}
        log(f"part4 manipulation check: {json.dumps(self.manip)}")
        bad = [k for k, v in self.manip.items() if not v["ok"]]
        if bad:
            raise HardStop(f"manipulation_check_mismatch:{','.join(bad)}")
        self.imeta = imeta
        self.gateway = Gateway(dt.OPUS_URL, self.control, p4["limits"], self.path("call_log"))
        self.gsem_n = int(p4["gateway"]["inflight"])
        self.step_generation()
        self.step_retrieval()
        self.step_readers()
        self.step_grading()
        self.step_retest()
        self.step = "results"
        self.write_results()
        if export_human is not None:
            self.export_human(export_human)

    # ------------------------------------------------------- generation
    def gen_done(self) -> list[str]:
        g, f = self.p4["generator"]["tag"], self.p4["generator_fallback"]["tag"]
        out = []
        for it in self.items:
            r1 = self.gen.get(ckey("generation", it["id"], role=g))
            if r1 is None:
                continue
            if r1["status"] in FALLBACK_ON and ckey("generation", it["id"], role=f) not in self.gen:
                continue
            out.append(it["id"])
        return out

    def step_generation(self) -> None:
        self.step = "generation"
        p4 = self.p4
        files = [Path(f) if Path(f).is_absolute() else self.out / f for f in p4["prior_question_files"]]
        spans, pst = prior_spans(self.pages, files)
        self.gen = Store(self.path("gen_calls"))
        self.items, ameta = allocate(self.pages, self.page_tokens, spans, p4)
        self.alloc_meta = {**ameta, "prior": dict(pst)}
        self.planned["generation"] = len(self.items)
        g, f = p4["generator"], p4["generator_fallback"]
        texts = {it["id"]: self.pages[it["doc_id"]]["text"] for it in self.items}

        def job(it: dict[str, Any]) -> Callable[[Any], Any]:
            async def fn(http: Any) -> None:
                k1 = ckey("generation", it["id"], role=g["tag"])
                msgs = gen_messages(it, texts[it["id"]])
                r1 = self.gen.get(k1)
                if r1 is None:
                    res = await self.gateway.generate(http, g, msgs, "generation", k1, float(g["empty_retry_s"]))
                    if res is None:
                        return
                    r1 = {"k": k1, **res}
                    self.gen.put(r1)
                if r1["status"] in FALLBACK_ON:
                    k2 = ckey("generation", it["id"], role=f["tag"])
                    if k2 in self.gen:
                        return
                    res = await self.gateway.standard(http, f, msgs, "generation", k2)
                    if res is not None:
                        self.gen.put({"k": k2, **res})
            return fn

        def missing() -> list[dict[str, Any]]:
            done = set(self.gen_done())
            return [it for it in self.items if it["id"] not in done]

        self.gateway_passes("generation", missing, job)
        self.questions, gmeta = build_questions(self.items, self.gen, self.pages, self.page_tokens, p4)
        qp = self.path("questions")
        tmp = qp.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in self.questions))
        os.replace(tmp, qp)
        self.gen_meta = {**self.alloc_meta, **gmeta}
        write_json_atomic(self.path("generation"), self.gen_meta)
        self.valid = [r for r in self.questions if r["status"] == "valid"]
        self.qrec = {r["record_id"]: r for r in self.valid}
        log(f"part4 generation: {json.dumps(gmeta['valid'])} valid; {json.dumps(gmeta['status'])}")

    def gateway_passes(self, step: str, missing: Callable[[], list[Any]],
                       job: Callable[[Any], Callable[[Any], Any]]) -> None:
        todo = missing()
        if not todo:
            return
        notice(f"batch: step {step}, {len(todo)} calls to issue")
        for _ in range(int(self.p4["gateway"]["passes"])):
            asyncio.run(run_jobs([("gateway", job(x)) for x in todo], self.gsem_n, self.control))
            self.check()
            todo = missing()
            if not todo:
                break
        if todo:
            raise Pause(f"calls_failed:{len(todo)}")
        t = self.control.totals()
        notice(f"step {step} done; totals since start {json.dumps(t['since_start'])}")

    # -------------------------------------------------------- retrieval
    def step_retrieval(self) -> None:
        import httpx

        self.step = "retrieval"
        self.ret = Store(self.path("retrieval"))
        arms = list(self.p4["arms"])
        self.planned["retrieval"] = len(self.valid) * len(arms)
        lim = self.p4["limits"]
        for arm in arms:
            self.check()
            recs = [r for r in self.valid if ckey("retrieval", r["record_id"], arm) not in self.ret]
            if not recs:
                continue
            down = None
            while True:
                try:
                    got = self.env.retrieve(arm, self.arms[arm], recs)
                    break
                except (httpx.TransportError, ConnectionError, OSError) as e:
                    t = time.monotonic()
                    down = down or t
                    log(f"retrieval {arm}: endpoint error {type(e).__name__}")
                    if t - down >= lim["local_unreachable_s"]:
                        raise Pause("local_unreachable:retrieval") from e
                    asyncio.run(self.control.sleep(lim["local_retry_s"]))
                    self.check()
            for r in recs:
                self.ret.put({"k": ckey("retrieval", r["record_id"], arm), "rr": list(got[r["record_id"]])})
        self.check()

    def rr(self, qid: str, arm: str) -> list[str]:
        return self.ret.rows[ckey("retrieval", qid, arm)]["rr"]

    def contexts(self) -> dict[tuple[str, str, str], Context]:
        if hasattr(self, "_ctx"):
            return self._ctx
        out = {}
        for r in self.valid:
            q = r["record_id"]
            for arm in self.p4["arms"]:
                lst = self.rr(q, arm)
                for cond, spec in self.p4["conditions"].items():
                    if "budget" in spec:
                        out[(q, arm, cond)] = budget_context(lst, int(spec["budget"]), self.by_id.__getitem__,
                                                             self.env.ntok)
                    else:
                        out[(q, arm, cond)] = top_context(lst, int(spec["top"]), self.by_id.__getitem__)
        self._ctx = out
        return out

    def prompt(self, q: str, arm: str, cond: str, reader: str) -> tuple[list[dict[str, str]], int, str]:
        cx = self.contexts()[(q, arm, cond)]
        window = self.readers[reader][2]
        return dt.reader_messages(self.qrec[q]["question"], [sat.passage(c, "rules", False) for c in cx.chunks],
                                  window, self.env.ntok, dt.READER_SYSTEM, int(self.p4["reader"]["window_reserve"]))

    # ---------------------------------------------------------- readers
    def reader_keys(self) -> list[tuple[str, str, str, str]]:
        return [(r["record_id"], a, c, rd) for r in self.valid for a in self.p4["arms"]
                for c in self.p4["conditions"] for rd in self.readers]

    def step_readers(self) -> None:
        self.step = "readers"
        self.ans = Store(self.path("answers"))
        keys = self.reader_keys()
        self.planned["readers"] = len(keys)
        rc = self.p4["reader"]
        client = LocalClient(self.control, self.p4["limits"], rc)

        def job(q: str, arm: str, cond: str, rd: str) -> Callable[[Any], Any]:
            k = ckey("readers", q, arm, cond, rd)

            async def fn(http: Any) -> None:
                url, model, _, extra = self.readers[rd]
                msgs, dropped, _ = self.prompt(q, arm, cond, rd)
                body = {"model": model, "messages": msgs, "temperature": rc["temperature"],
                        "max_tokens": int(rc["max_tokens"]), **(extra or {})}
                res = await client.call(http, url, body, rd)
                if res is not None:
                    self.ans.put({"k": k, **res, "dropped": dropped})
            return fn

        todo = [x for x in keys if ckey("readers", *x) not in self.ans]
        if todo:
            log(f"part4 readers: {len(todo)} calls, <= {rc['inflight']} in flight per endpoint")
            asyncio.run(run_jobs([(self.readers[x[3]][0], job(*x)) for x in todo], int(rc["inflight"]),
                                 self.control))
            self.check()
        missing = [x for x in keys if ckey("readers", *x) not in self.ans]
        if missing:
            raise Pause(f"reader_calls_missing:{len(missing)}")
        failed = sum(1 for x in keys if not self.ans.rows[ckey("readers", *x)]["ok"])
        self.reader_failed = failed
        if keys and failed / len(keys) > float(self.p4["limits"]["reader_fail_share"]):
            raise HardStop(f"reader_failures:{failed}/{len(keys)}")

    # ---------------------------------------------------------- grading
    def subsets(self) -> dict[str, set[str]]:
        if hasattr(self, "_subsets"):
            return self._subsets
        self._subsets = draw_subsets(self.valid, self.p4)
        return self._subsets

    def grade_jobs(self) -> dict[str, dict[str, tuple[str, str]]]:
        """{grader tag: {dedup key: (question id, representative answer)}}, built in sorted key order."""
        ss = self.subsets()
        pt, stag = self.p4["grader_primary"]["tag"], self.p4["grader_secondary"]["tag"]
        need: dict[str, dict[str, tuple[str, str]]] = {pt: {}, stag: {}}
        budgets = {c for c, s in self.p4["conditions"].items() if "budget" in s}
        for q, arm, cond, rd in sorted(self.reader_keys()):
            row = self.ans.rows[ckey("readers", q, arm, cond, rd)]
            if not row["ok"]:
                continue
            gk = answer_key(q, row["answer"])
            if cond in budgets or q in ss["primary_k10"]:
                need[pt].setdefault(gk, (q, row["answer"]))
            if q in ss["secondary"]:
                need[stag].setdefault(gk, (q, row["answer"]))
        return need

    def step_grading(self) -> None:
        self.step = "grading"
        specs = {self.p4["grader_primary"]["tag"]: self.p4["grader_primary"],
                 self.p4["grader_secondary"]["tag"]: self.p4["grader_secondary"]}
        self.grades = {t: Store(self.path("grades", t)) for t in specs}
        need = self.grade_jobs()
        self.need = need
        self.planned["grading"] = sum(len(v) for v in need.values())

        def missing() -> list[tuple[str, str]]:
            return [(t, gk) for t in specs for gk in sorted(need[t])
                    if ckey("grading", need[t][gk][0], gk, "-", t) not in self.grades[t]]

        def job(x: tuple[str, str]) -> Callable[[Any], Any]:
            t, gk = x
            q, ans = need[t][gk]
            k = ckey("grading", q, gk, "-", t)

            async def fn(http: Any) -> None:
                msgs = dt.grader_messages(self.qrec[q]["question"], self.qrec[q]["gold"], ans)
                res = await self.gateway.standard(http, specs[t], msgs, "grading", k)
                if res is not None:
                    self.grades[t].put(grade_row(k, res))
            return fn

        self.gateway_passes("grading", missing, job)

    def label(self, tag: str, q: str, answer: str) -> str | None:
        st = self.grades.get(tag)
        if st is None:
            return None
        gk = answer_key(q, answer)
        row = st.get(ckey("grading", q, gk, "-", tag))
        return None if row is None else row["v"]

    # ----------------------------------------------------------- retest
    def step_retest(self) -> None:
        self.step = "retest"
        spec = self.p4["grader_primary"]
        t = spec["tag"]
        self.retest_store = Store(self.path("retest", t))
        keys = sorted(gk for gk, (q, _) in self.need[t].items()
                      if self.grades[t].rows[ckey("grading", q, gk, "-", t)]["v"] != "no_label")
        n = min(int(self.p4["retest_n"]), len(keys))
        self.retest_keys = retest_sample(keys, n, int(self.p4["seed"]))
        self.planned["retest"] = n

        def missing() -> list[str]:
            return [gk for gk in self.retest_keys if ckey("retest", self.need[t][gk][0], gk, "-", t)
                    not in self.retest_store]

        def job(gk: str) -> Callable[[Any], Any]:
            q, ans = self.need[t][gk]
            k = ckey("retest", q, gk, "-", t)

            async def fn(http: Any) -> None:
                msgs = dt.grader_messages(self.qrec[q]["question"], self.qrec[q]["gold"], ans)
                res = await self.gateway.standard(http, spec, msgs, "retest", k)
                if res is not None:
                    self.retest_store.put(grade_row(k, res))
            return fn

        self.gateway_passes("retest", missing, job)

    # ---------------------------------------------------------- results
    def write_results(self) -> None:
        p4 = self.p4
        arms, conds, readers = list(p4["arms"]), list(p4["conditions"]), list(self.readers)
        pt, stag = p4["grader_primary"]["tag"], p4["grader_secondary"]["tag"]
        ctxs = self.contexts()
        budgets = {c for c, s in p4["conditions"].items() if "budget" in s}
        per_item: dict[str, Any] = {}
        prompt_tok: dict[tuple[str, str, str], int] = {}
        for r in self.valid:
            q = r["record_id"]
            golds = [norm(g) for g in r["gold"]]
            per_item[q] = {}
            for arm in arms:
                per_item[q][arm] = {}
                for cond in conds:
                    cx = ctxs[(q, arm, cond)]
                    texts = [norm(c.content) for c in cx.chunks]
                    found = all(any(sat.hit(g, t) for t in texts) for g in golds)
                    cell: dict[str, Any] = {}
                    for rd in readers:
                        row = self.ans.rows[ckey("readers", q, arm, cond, rd)]
                        _, dropped, body = self.prompt(q, arm, cond, rd)
                        prompt_tok[(q, arm, cond)] = self.env.ntok(body)
                        labels: dict[str, str | None] = {}
                        for tag in (pt, stag):
                            labels[tag] = self.label(tag, q, row["answer"]) if row["ok"] else "reader_failed"
                        # Coverage defines the grade: a dedup hit outside a grader's coverage is not used.
                        if q not in self.subsets()["secondary"]:
                            labels[stag] = None
                        if cond not in budgets and q not in self.subsets()["primary_k10"]:
                            labels[pt] = None
                        elif row["ok"] and labels[pt] is None:
                            raise HardStop(f"internal: primary grade missing for {q}")
                        cell[rd] = {"ok": row["ok"], "finish": row.get("finish"), "reader_prompt_tokens": row["pt"],
                                    "dropped": dropped, **labels}
                    per_item[q][arm][cond] = {"ctx_tokens": cx.ctx_tokens, "prompt_tokens_sfr": prompt_tok[(q, arm, cond)],
                                              "chunks": len(cx.ids), "truncated": cx.truncated,
                                              "truncated_empty": cx.truncated_empty, "shortfall": cx.shortfall,
                                              "found": found, "readers": cell}
        realised: dict[str, Any] = {}
        for arm in arms:
            realised[arm] = {}
            for cond, spec in p4["conditions"].items():
                xs = [ctxs[(r["record_id"], arm, cond)].ctx_tokens for r in self.valid]
                ps = [prompt_tok[(r["record_id"], arm, cond)] for r in self.valid]
                row = {"n": len(xs), "ctx_mean": round(statistics.mean(xs), 2) if xs else None,
                       "ctx_min": min(xs, default=None), "ctx_median": statistics.median(xs) if xs else None,
                       "ctx_max": max(xs, default=None),
                       "prompt_mean": round(statistics.mean(ps), 2) if ps else None,
                       "truncated": sum(ctxs[(r["record_id"], arm, cond)].truncated for r in self.valid),
                       "truncated_empty": sum(ctxs[(r["record_id"], arm, cond)].truncated_empty for r in self.valid)}
                if "budget" in spec and xs:
                    row["budget"] = int(spec["budget"])
                    row["mean_shortfall_share"] = round(1 - statistics.mean(xs) / int(spec["budget"]), 4)
                    row["limitation_over_2pct_under_budget"] = statistics.mean(xs) < 0.98 * int(spec["budget"])
                realised[arm][cond] = row
        finish_len = {rd: {arm: {cond: sum(per_item[r["record_id"]][arm][cond]["readers"][rd]["finish"] == "length"
                                           for r in self.valid) for cond in conds} for arm in arms} for rd in readers}
        rates: dict[str, Any] = {}
        for tag in (pt, stag):
            rates[tag] = {}
            for arm in arms:
                labs = [per_item[q][arm][c]["readers"][rd][tag] for q in per_item for c in conds for rd in readers]
                labs = [x for x in labs if x is not None]
                cnt = Counter(labs)
                rates[tag][arm] = {"graded": len(labs), "unparseable": cnt["unparseable"], "no_label": cnt["no_label"],
                                   "reader_failed": cnt["reader_failed"], "distribution": dict(cnt)}
        # ---- test-retest
        rt = []
        for gk in self.retest_keys:
            q = self.need[pt][gk][0]
            a = self.grades[pt].rows[ckey("grading", q, gk, "-", pt)]["v"]
            b = self.retest_store.rows[ckey("retest", q, gk, "-", pt)]["v"]
            rt.append((a, b))
        rt_ok = [(a, b) for a, b in rt if "no_label" not in (a, b)]
        k_cv = dt.kappa([a == "correct" for a, _ in rt_ok], [b == "correct" for _, b in rt_ok]) if rt_ok else None
        retest = {"n": len(rt), "n_labelled_both": len(rt_ok),
                  "agreement": round(sum(a == b for a, b in rt_ok) / len(rt_ok), 4) if rt_ok else None,
                  "kappa_4class": dt.kappa([a for a, _ in rt_ok], [b for _, b in rt_ok]) if rt_ok else None,
                  "kappa_correct_vs_not": k_cv,
                  "caveat_kappa_below_0_8": (k_cv is not None and k_cv < 0.8)}
        questions = {r["record_id"]: {"set": r["set"], "stratum": r["stratum"], "doc_id": r["doc_id"],
                                      "page_tokens": r["page_tokens"], "gen": r["gen"]} for r in self.valid}
        f_tag = p4["generator_fallback"]["tag"]
        fb = [questions[q] | {"record_id": q} for q in sorted(questions) if questions[q]["gen"] == f_tag]
        rest = [questions[q] for q in questions if questions[q]["gen"] != f_tag]
        gen_fb = {"items": fb, "by_stratum": dict(Counter(x["stratum"] for x in fb)),
                  "rest_by_stratum": dict(Counter(x["stratum"] for x in rest)),
                  "page_tokens_median": statistics.median([x["page_tokens"] for x in fb]) if fb else None,
                  "rest_page_tokens_median": statistics.median([x["page_tokens"] for x in rest]) if rest else None}
        ss = self.subsets()
        subsets = {"primary_k10": sorted(ss["primary_k10"]), "secondary": sorted(ss["secondary"]),
                   "retest": [k[:16] for k in self.retest_keys]}
        an = p4["analysis"]
        analysis = analyse_part4(per_item, questions, conditions=conds, readers=readers, primary=pt, secondary=stag,
                                 analysis=an, seed=int(p4["seed"]), fallback_tag=f_tag, arms=arms)
        prov = self.prov
        citable = bool(prov.get("commit")) and prov.get("dirty") is False and prov.get("untracked") == 0
        segs = list(self.prev_state.get("segments", [])) if self.prev_state else []
        segs.append(self.segment("complete", None))
        uses = {t: {"prompt_tokens": sum(int(x.get("pt") or 0) for x in s.rows.values()),
                    "completion_tokens": sum(int(x.get("ct") or 0) for x in s.rows.values()),
                    "calls_recorded": len(s.rows)} for t, s in self.grades.items()}
        report = {
            "schema": RESULTS_SCHEMA,
            "prereg": {"file": self.files["prereg"], "sha256": self.prereg_sha},
            "config_sha256": self.config_sha,
            "provenance": prov, "citable": citable,
            "run_segments": segs,
            "design": {"arms": arms, "conditions": p4["conditions"], "readers": readers, "seed": p4["seed"],
                       "reader": p4["reader"], "generator": p4["generator"],
                       "generator_fallback": p4["generator_fallback"], "grader_primary": p4["grader_primary"],
                       "grader_secondary": p4["grader_secondary"], "analysis": an,
                       "temperature_dropped": sorted(self.gateway.temperature_dropped)},
            "index": {k: v for k, v in self.imeta.items() if k != "reassembly_failed_ids"},
            "manifest_drift_docs": self.manifest_drift,
            "manipulation_check": self.manip,
            "size_distributions": {a: dt.size_dist(self.arms[a]) for a in arms},
            "generation": self.gen_meta,
            "n": {s: sum(r["set"] == s for r in self.valid) for s in ("M", "S")},
            "by_stratum": {s: dict(Counter(r["stratum"] for r in self.valid if r["set"] == s)) for s in ("M", "S")},
            "gen_fallback": gen_fb,
            "subsets": subsets,
            "questions": questions,
            "per_item": per_item,
            "realised_tokens": realised,
            "finish_length": finish_len,
            "reader_failures": {"failed": self.reader_failed, "calls": len(self.reader_keys())},
            "grade_rates": rates,
            "grader_usage": uses,
            "retest": retest,
            "analysis": analysis,
        }
        write_json_atomic(self.path("results"), report)
        log(f"part4: wrote {self.files['results']}")

    # ------------------------------------------------------ human export
    def export_human(self, path: Path) -> None:
        """The items the blinded human grading page needs (§5), privately: a shuffled
        item list without arm / condition / reader / model grades; the blinding key is the
        sibling file ``<name>.key.json``."""
        repo = sat._REPO_PY.parent.resolve()
        rp = path.resolve()
        if rp == repo or repo in rp.parents:
            raise SystemExit("part4: refusing to write the human-grade export inside the repository")
        p4, h = self.p4, self.p4["human"]
        pt, stag = p4["grader_primary"]["tag"], p4["grader_secondary"]["tag"]
        rng = random.Random(f"{p4['seed']}|human")  # derived seed, as the other subsets (prereg §5)
        # Part A: Sol vs Opus disagree on correct-vs-not, within the secondary subset.
        dis = []
        for gk, (q, ans) in sorted(self.need[stag].items()):
            a = self.label(pt, q, ans)
            b = self.label(stag, q, ans)
            if a is None or b is None or a in MISSING or b in MISSING:
                continue
            if (a == "correct") != (b == "correct"):
                dis.append((gk, q, ans))
        part_a = rng.sample(dis, min(int(h["part_a"]), len(dis)))
        # Part B: discordant pairs of the part-B contrast under the primary grader.
        x_arm, y_arm = h["part_b_arms"]
        cond, rd = h["part_b_condition"], h["part_b_reader"]
        pairs = []
        for r in self.valid:
            if r["set"] != h["part_b_set"]:
                continue
            q = r["record_id"]
            ax = self.ans.rows[ckey("readers", q, x_arm, cond, rd)]
            ay = self.ans.rows[ckey("readers", q, y_arm, cond, rd)]
            if not (ax["ok"] and ay["ok"]):
                continue
            lx, ly = self.label(pt, q, ax["answer"]), self.label(pt, q, ay["answer"])
            if lx is None or ly is None or lx in MISSING or ly in MISSING:
                continue
            if (lx == "correct") != (ly == "correct"):
                pairs.append(q)
        part_b = rng.sample(pairs, min(int(h["part_b_pairs"]), len(pairs)))
        items, key = [], {}
        for gk, q, ans in part_a:
            items.append({"question": self.qrec[q]["question"], "reference": self.qrec[q]["gold"], "answer": ans})
            key_entry = {"part": "A", "record_id": q, "answer_key": gk[:16], pt: self.label(pt, q, ans),
                         stag: self.label(stag, q, ans)}
            items[-1]["_key"] = key_entry
        for q in part_b:
            for arm in (x_arm, y_arm):
                ans = self.ans.rows[ckey("readers", q, arm, cond, rd)]["answer"]
                items.append({"question": self.qrec[q]["question"], "reference": self.qrec[q]["gold"], "answer": ans,
                              "_key": {"part": "B", "record_id": q, "arm": arm, "condition": cond, "reader": rd,
                                       pt: self.label(pt, q, ans)}})
        rng.shuffle(items)
        out_items = []
        for n, it in enumerate(items):
            iid = f"h{n:03d}"
            key[iid] = it.pop("_key")
            out_items.append({"item": iid, **it})
        kpath = path.with_name(path.stem + ".key.json")
        write_json_atomic(path, {"labels": list(LABELS), "items": out_items,
                                 "note": "PRIVATE. The grading page shows items only; the key is a separate file."})
        write_json_atomic(kpath, {"key": key, "note": "PRIVATE blinding key: never shown to a grader."})
        log(f"part4: human-grade export: {len(out_items)} items ({len(part_a)} part A, {len(part_b)} part-B pairs)")


def grade_row(k: str, res: dict[str, Any]) -> dict[str, Any]:
    if res["status"] == "no_label":
        v, rule = "no_label", res.get("reason") or "empty"   # no_label (empty) / no_label (truncated)
    else:
        v, rule = parse_grade(res["text"])
    return {"k": k, "v": v, "rule": rule, "raw": res.get("text", ""), "pt": int(res.get("pt") or 0),
            "ct": int(res.get("ct") or 0), "finish": res.get("finish"), "model": res.get("model"),
            "attempts": res.get("attempts")}


def run_part4(cfg_path: Path, out: Path, env: Env, resume: bool = False,
              batch: dict[str, int | None] | None = None, export_human: Path | None = None) -> int:
    run = Run(cfg_path, out, env, resume, batch)
    return run.execute(export_human)


def cmd_part4(args: argparse.Namespace) -> int:
    if getattr(args, "power_table", None):
        p4c = part4_config(dt.load_private_config(args.private_config))
        an = p4c["analysis"]
        table = power_table(seed=int(p4c["seed"]), delta=float(an["delta"]), alpha=float(an["alpha"]))
        write_json_atomic(Path(args.power_table), table)
        log(f"part4: power table ({len(table['rows'])} rows) -> {args.power_table}")
        return EXIT_OK
    out = Path(args.out_dir)
    env = production_env(out, args.embed_urls, args.rerank_url)
    batch = {"batch_calls": args.batch_calls, "batch_input_tokens": args.batch_input_tokens,
             "batch_output_tokens": args.batch_output_tokens}
    return run_part4(Path(args.private_config), out, env, args.resume, batch,
                     Path(args.export_human) if args.export_human else None)


def add_part4_parser(sub: Any) -> None:
    r = sub.add_parser("part4", help="confirmatory part 4 (pause/resume; exit 75 = paused)")
    r.add_argument("--embed-urls", default=",".join(f"http://127.0.0.1:{p}" for p in range(9001, 9007)))
    r.add_argument("--rerank-url", default="http://127.0.0.1:50052")
    r.add_argument("--resume", action="store_true", help="continue a paused run (commit/prereg/config sha must match)")
    r.add_argument("--batch-calls", type=int, default=None, help="pause after this many gateway calls this segment")
    r.add_argument("--batch-input-tokens", type=int, default=None)
    r.add_argument("--batch-output-tokens", type=int, default=None)
    r.add_argument("--power-table", default=None, metavar="OUT_JSON",
                   help="compute the power table (simulation; no calls) into OUT_JSON and exit")
    r.add_argument("--export-human", default=None,
                   help="after a complete run: write the human-grading items (PRIVATE; outside the repo) here")
