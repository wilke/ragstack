#!/usr/bin/env python
"""Glossary benchmark, gold labelling: the judge harness (rubric v3, frozen).

Committed form of round 1's scratch ``judge.py``. One prompt per sampled document
(its judged text + its shuffled, arm-hidden items, from ``build_sample.py``); one
JSONL row per document with the labels, usage, served model, cost (when the CLI
reports it), duration and error. **Rubric v3 is the only rubric** (prereg §6:
no rubric change after any label is seen; round 1's v1/v2 texts stay in round 1's
private record).

    judge.py preflight --sample S.jsonl --judge gateway --model M        # size notice, no call
    judge.py probe     --judge claude|codex|gateway [--model M] ...      # ONE synthetic probe
    judge.py run       --sample S.jsonl --out labels.jsonl --judge claude [--keys a,b] [--limit N]
    judge.py run       ... --judge gateway --model M --private-config CFG --notice-acknowledged

Transports (reused from round 1):

* ``claude``: the Claude CLI headless (``claude -p --restricted --strict-mcp-config
  --tools "" --system-prompt``), prompt on stdin, cwd an empty 0700 directory;
* ``codex``: ``codex exec`` read-only, every optional feature off, output schema enforced;
* ``gateway``: an OpenAI-compatible ``/chat/completions`` (base URL from
  ``--private-config`` ``{"gateway": {"base_url": ...}}``; no default), no
  Authorization header (the proxy adds it).

Gateway terms, enforced in code (asserted on every request body): ``fallback_model: ""``,
``max_tokens`` always set, at most 4 in flight (hard cap for every judge), an empty
answer is a failure (retry once, skip after two), the served model recorded per call,
a token budget that pauses (default 5.0M input or 0.75M output tokens, counted across
resumes from the totals file), and ``run`` refuses to start against the gateway without
``--notice-acknowledged`` (the operator sends the ``preflight`` notice first).

Stops: a ``PAUSE`` file (``--pause-file``, default ``<out>.PAUSE``) or SIGTERM stops
issuing calls, lets in-flight calls finish and exits 75; a usage/rate-limit signal,
the gateway unreachable, 5 failed documents in a row or more than 20 in total, or the
budget also pause (exit 75). A document with an ``ok`` row is skipped on rerun. The
first call of a run is an isolation check: the judge's whole input context must be
explained by the prompt (CLI judges); a failure stops the run (exit 1). A totals
JSON is written at the end (``<out>.totals.json``) and a totals line every 10
documents (``<out>.totals.jsonl``).

Standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

EXIT_OK, EXIT_FAIL, EXIT_PAUSED = 0, 1, 75
MAX_INFLIGHT = 4
CALL_TIMEOUT = 300
CHARS_PER_TOKEN = 3.5
ISOLATION_FACTOR = 2.0
RUBRIC = "v3"
LABELS = ("correct", "partial", "wrong", "not-a-symbol")
CLAUDE_MODEL = "claude-opus-5-5"
#: Fixed context the Claude CLI adds under the isolation flags (round 1 measured 574
#: tokens on claude-opus-5-5 with a 25-char trivial call), with 25% headroom.
CLAUDE_FIXED_OVERHEAD = 1.25 * 574
CODEX_MODEL = "gpt-6-astra"
CODEX_EFFORT = "medium"
#: gateway models that reject a temperature parameter
GATEWAY_NO_TEMPERATURE = frozenset({"Claude Sonnet 5", "GPT-5.6 Sol"})
DEFAULT_BUDGET_IN, DEFAULT_BUDGET_OUT = 5_000_000, 750_000
FAIL_CONSECUTIVE, FAIL_TOTAL = 5, 20
BATCH = 10

_LIMIT_RE = re.compile(
    r"rate.?limit|usage.?limit|quota|overloaded|429|too many requests|"
    r"insufficient.?credit|billing|out of credit|hit your .{0,20}limit", re.I)

SYSTEM = """You check entries of a symbol glossary against ONE document. Each entry pairs a symbol with a definition. Judge every entry ONLY from the document text you are given; never from outside knowledge.

What counts as a symbol here:
- a mathematical symbol or variable of the document (e.g. a Greek letter, a subscripted quantity), or
- an abbreviation, acronym or alias that the document itself pairs with another name for the same thing (e.g. the document writes "growth factor receptor-bound protein 2 (Grb2)" or "measles virus (MeV)").
A gene, protein, strain or other name that the document uses WITHOUT giving any other name for it is NOT a symbol, even if you know what it stands for.

Text extraction may have flattened symbols (lost subscripts, superscripts, italics, or a split such as "V br" for V_br). Judge such an entry by the symbol it stands for, as long as the string identifies exactly ONE symbol of the document. If the string stands for two or more different symbols of the document (e.g. "br" where the document has both V_br and N_br), label it "wrong", because any single definition attached to it misleads.

Labels:
- "correct": a symbol in the sense above that occurs in the document, and the definition matches what the document says it denotes, completely and specifically. Small wording differences are fine.
- "partial": a symbol in the sense above, and the definition is TRUE according to the document but incomplete, truncated or less specific than what the document states (for example it drops the qualifier that distinguishes it, or the document gives a clearer, fuller definition elsewhere).
- "wrong": a symbol in the sense above, but the definition is false for it according to the document: a different meaning, the definition of another symbol, a meaning the document never states, or a fragment that stands for more than one symbol (see above).
- "not-a-symbol": anything else: an ordinary word or term, a gene/protein/strain name the document never pairs with another name, a unit, a plain number, a reference or figure label, a code, command, file or option name not used as a variable, or a string that does not occur in the document in any form.

Give one label per entry, with a short reason (at most 20 words) that points to the document's own words.

Answer with JSON only, exactly this shape, one element per entry id, in the given order:
{"labels": [{"id": "i01", "label": "correct|partial|wrong|not-a-symbol", "reason": "..."}]}"""
#: sha256 of :data:`SYSTEM`: rubric v3 exactly as round 1 froze it (pinned by a test)
SYSTEM_SHA256 = hashlib.sha256(SYSTEM.encode("utf-8")).hexdigest()

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["labels"],
    "properties": {"labels": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "label", "reason"],
        "properties": {"id": {"type": "string"}, "label": {"type": "string", "enum": list(LABELS)},
                       "reason": {"type": "string"}}}}},
}

#: A made-up, harmless probe: no benchmark text. One defined symbol, two items.
PROBE_ROW: dict[str, Any] = {
    "key": "probe-01", "corpus": "probe", "truncated": False,
    "judged_text": ("In this note the drift speed of a paper boat is written vb. "
                    "We measured vb on a still pond for ten minutes."),
    "items": [{"id": "i01", "symbol": "vb", "definition": "drift speed of a paper boat"},
              {"id": "i02", "symbol": "pond", "definition": "body of still water"}],
}


class UsageLimit(RuntimeError):
    pass


class Unreachable(RuntimeError):
    pass


def build_prompt(row: dict[str, Any]) -> str:
    items = json.dumps([{"id": i["id"], "symbol": i["symbol"], "definition": i["definition"]}
                        for i in row["items"]], ensure_ascii=False, indent=0)
    note = "\n(The document is shown as far as the glossary builders read it; the rest was cut.)" \
        if row.get("truncated") else ""
    return (f"DOCUMENT:{note}\n<<<\n{row['judged_text']}\n>>>\n\n"
            f"ENTRIES ({len(row['items'])}):\n{items}\n\n"
            "Label every entry. Answer with the JSON object only.")


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def parse_labels(text: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Strict JSON (a ```json fence is tolerated). Valid = every id once, known labels."""
    t = text.strip()
    m = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", t, re.S)
    if m:
        t = m.group(1)
    try:
        obj = json.loads(t)
    except ValueError as e:
        return {"valid": False, "labels": None, "parse_error": f"json: {e}"}
    labs = obj.get("labels") if isinstance(obj, dict) else None
    if not isinstance(labs, list):
        return {"valid": False, "labels": None, "parse_error": "no labels array"}
    want = [i["id"] for i in items]
    got: dict[str, dict[str, Any]] = {}
    problems = []
    for el in labs:
        if not isinstance(el, dict) or el.get("id") not in want:
            problems.append(f"unknown element {str(el)[:80]}")
            continue
        if el["id"] in got:
            problems.append(f"duplicate {el['id']}")
            continue
        if el.get("label") not in LABELS:
            problems.append(f"bad label for {el['id']}")
        got[el["id"]] = {"id": el["id"], "label": el.get("label"), "reason": str(el.get("reason", ""))[:400]}
    missing = [i for i in want if i not in got]
    if missing:
        problems.append(f"missing {missing}")
    return {"valid": not problems, "labels": [got[i] for i in want if i in got],
            "parse_error": "; ".join(problems) or None}


# --------------------------------------------------------------------------- transports
def claude_cmd(model: str, effort: str | None) -> list[str]:
    cmd = ["claude", "-p", "--model", model, "--output-format", "json", "--restricted",
           "--no-session-persistence", "--strict-mcp-config", "--tools", "", "--system-prompt", SYSTEM]
    if effort:
        cmd += ["--effort", effort]
    return cmd


def codex_cmd(codex: str, model: str | None, workdir: Path, sysfile: Path, schema: Path, lastmsg: Path) -> list[str]:
    cmd = [codex, "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
           "-C", str(workdir), "-s", "read-only", "-c", 'approval_policy="never"', "-c", "project_doc_max_bytes=0",
           "-c", "mcp_servers={}", "-c", 'web_search="disabled"', "-c", "skills.bundled.enabled=false",
           "-c", "skills.include_instructions=false", "-c", "agents.enabled=false",
           "-c", "include_permissions_instructions=false", "-c", "include_environment_context=false",
           "-c", "include_apps_instructions=false", "-c", "include_apply_patch_tool=false",
           "-c", f'model_instructions_file="{sysfile}"']
    for f in ("shell_tool", "unified_exec", "apps", "plugins", "remote_plugin", "multi_agent", "goals", "hooks",
              "view_image", "skill_search", "tool_suggest", "browser_use", "browser_use_external", "computer_use",
              "image_generation", "in_app_browser", "sleep_tool", "code_mode_host", "shell_snapshot", "memories",
              "personality", "skill_mcp_dependency_install", "workspace_dependencies"):
        cmd += ["--disable", f]
    if model:
        cmd += ["-m", model, "-c", f'model_reasoning_effort="{CODEX_EFFORT}"']
    cmd += ["--json", "--color", "never", "--output-schema", str(schema), "-o", str(lastmsg), "-"]
    return cmd


def call_claude(prompt: str, model: str, effort: str | None, cwd: Path) -> dict[str, Any]:
    t0 = time.time()
    p = subprocess.run(claude_cmd(model, effort), input=prompt, capture_output=True, text=True, cwd=cwd,
                       timeout=CALL_TIMEOUT)
    dt = time.time() - t0
    if p.returncode != 0:
        err = (p.stderr or p.stdout or "")[-2000:]
        if _LIMIT_RE.search(err):
            raise UsageLimit(f"claude exit {p.returncode}: {err.strip()[:500]}")
        raise RuntimeError(f"claude exit {p.returncode}: {err.strip()[:500]}")
    obj = json.loads(p.stdout)
    text = obj.get("result") or ""
    if obj.get("is_error") and _LIMIT_RE.search(str(text) + str(obj.get("api_error_status"))):
        raise UsageLimit(f"is_error: {str(text)[:500]}")
    mu = obj.get("modelUsage") or {}
    own = next(({"key": k, **v} for k, v in mu.items()
                if k == model or k.startswith(model) or v.get("canonicalModel") == model), {})
    ctx = (int(own.get("inputTokens") or 0) + int(own.get("cacheCreationInputTokens") or 0)
           + int(own.get("cacheReadInputTokens") or 0)) if own else None
    return {"text": text, "is_error": bool(obj.get("is_error")), "served_model": own.get("key"),
            "context_tokens": ctx, "output_tokens": own.get("outputTokens"), "usage": obj.get("usage"),
            "cost_usd": obj.get("total_cost_usd"), "duration_s": round(dt, 2), "tool_events": 0}


def call_codex(prompt: str, codex: str, model: str | None, tmp_root: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(dir=tmp_root) as tdn:
        td = Path(tdn)
        os.chmod(td, 0o700)
        work = td / "work"
        work.mkdir(mode=0o700)
        sysfile, schema, last = td / "system.md", td / "schema.json", td / "last.txt"
        sysfile.write_text(SYSTEM)
        schema.write_text(json.dumps(SCHEMA))
        t0 = time.time()
        p = subprocess.run(codex_cmd(codex, model, work, sysfile, schema, last), input=prompt,
                           capture_output=True, text=True, cwd=work, timeout=CALL_TIMEOUT)
        dt = time.time() - t0
        events = []
        for line in p.stdout.splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
        errs = [e for e in events if e.get("type") in ("error", "turn.failed")]
        errtext = json.dumps(errs)[:1500] + (p.stderr or "")[-1500:]
        if p.returncode != 0 or errs:
            if _LIMIT_RE.search(errtext):
                raise UsageLimit(f"codex: {errtext.strip()[:500]}")
            if p.returncode != 0:
                raise RuntimeError(f"codex exit {p.returncode}: {errtext.strip()[:500]}")
        usage: dict[str, Any] = {}
        for e in events:
            if e.get("type") == "turn.completed":
                usage = e.get("usage") or {}
        items = [e.get("item") or {} for e in events if e.get("type", "").startswith("item.")]
        tool_events = sum(1 for i in items if (i.get("type") or i.get("item_type"))
                          not in ("agent_message", "reasoning", "error", None))
        served = None
        for e in events:
            for k in ("model", "model_slug"):
                if isinstance(e.get(k), str):
                    served = e[k]
        return {"text": last.read_text() if last.exists() else "", "is_error": bool(errs),
                "served_model": served or model,
                "context_tokens": int(usage.get("input_tokens") or 0) if usage else None,
                "output_tokens": usage.get("output_tokens"), "usage": usage, "cost_usd": None,
                "duration_s": round(dt, 2), "tool_events": tool_events}


def gateway_body(prompt: str, model: str, max_tokens: int) -> dict[str, Any]:
    """The request body. Gateway terms: ``fallback_model ""`` (a refusal is final; no other
    model silently answers) and ``max_tokens`` always; no temperature for the models that
    reject it."""
    if not max_tokens or int(max_tokens) <= 0:
        raise ValueError("max_tokens must be set")
    body: dict[str, Any] = {"model": model, "max_tokens": int(max_tokens), "fallback_model": "",
                            "messages": [{"role": "system", "content": SYSTEM},
                                         {"role": "user", "content": prompt}]}
    if model not in GATEWAY_NO_TEMPERATURE:
        body["temperature"] = 0
    return body


def call_gateway(prompt: str, model: str, max_tokens: int, base_url: str) -> dict[str, Any]:
    """One OpenAI-compatible chat call. No Authorization header (the proxy adds auth).
    Connection refused = the gateway route is down = stop."""
    import urllib.error
    import urllib.request

    body = gateway_body(prompt, model, max_tokens)
    assert body.get("fallback_model") == "" and body.get("max_tokens"), "gateway terms"
    req = urllib.request.Request(f"{base_url.rstrip('/')}/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=CALL_TIMEOUT) as resp:
            status, raw = resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read().decode("utf-8", "replace")
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, ConnectionRefusedError) or "refused" in str(reason).lower():
            raise Unreachable(f"gateway unreachable: {reason}") from e
        raise RuntimeError(f"gateway transport: {reason}") from e
    except ConnectionRefusedError as e:
        raise Unreachable(f"gateway unreachable: {e}") from e
    dt = time.time() - t0
    if status == 429 or (status >= 400 and _LIMIT_RE.search(raw)):
        raise UsageLimit(f"gateway http {status}: {raw[:500]}")
    if status >= 400:
        raise RuntimeError(f"gateway http {status}: {raw[:500]}")
    obj = json.loads(raw)
    ch = (obj.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    usage = obj.get("usage") or {}
    return {"text": msg.get("content") or "", "is_error": False, "http_status": status,
            "served_model": obj.get("model"), "finish_reason": ch.get("finish_reason"),
            "context_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens"),
            "usage": usage, "cost_usd": None, "duration_s": round(dt, 2),
            "tool_events": len(msg.get("tool_calls") or [])}


def isolation_check(judge: str, prompt: str, res: dict[str, Any], codex_overhead: int | None) -> dict[str, Any]:
    """The judge's whole input context must be explained by the prompt + system text."""
    chars = len(prompt) + len(SYSTEM)
    implied = chars / CHARS_PER_TOKEN
    overhead: float = 0
    if judge == "claude":
        overhead = CLAUDE_FIXED_OVERHEAD
    elif judge == "codex":
        if codex_overhead is None:
            return {"PASS": False, "why": "no codex overhead measured: run `probe --judge codex` first"}
        overhead = codex_overhead
    budget = ISOLATION_FACTOR * implied + overhead
    seen = res.get("context_tokens")
    return {"prompt_plus_system_chars": chars, "implied_tokens": round(implied, 1),
            "fixed_overhead_tokens": overhead, "budget": round(budget, 1), "context_tokens": seen,
            "tool_events": res.get("tool_events"),
            "PASS": bool(seen is not None and seen <= budget and not res.get("tool_events"))}


# --------------------------------------------------------------------------- budget / totals
class Totals:
    """Run totals, carried across resumes from ``<out>.totals.json``."""

    KEYS = ("calls", "empty", "failed_docs", "ok_docs", "tokens_in", "tokens_out")

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        prev = json.loads(path.read_text()) if path.exists() else {}
        self.cum = {k: int(prev.get("cumulative", {}).get(k, 0)) for k in self.KEYS}
        self.cum_cost = float(prev.get("cumulative", {}).get("cost_usd", 0.0))
        self.seg = dict.fromkeys(self.KEYS, 0)
        self.seg_cost = 0.0
        self.served: dict[str, int] = dict(prev.get("served_models", {}))

    def add(self, **kw: Any) -> None:
        with self.lock:
            for k, v in kw.items():
                if k == "cost_usd":
                    self.seg_cost += float(v or 0.0)
                    self.cum_cost += float(v or 0.0)
                elif k == "served_model":
                    if v:
                        self.served[str(v)] = self.served.get(str(v), 0) + 1
                else:
                    self.seg[k] += int(v or 0)
                    self.cum[k] += int(v or 0)

    def over_budget(self, budget_in: int | None, budget_out: int | None) -> str | None:
        with self.lock:
            if budget_in and self.cum["tokens_in"] >= budget_in:
                return f"token budget: {self.cum['tokens_in']} input tokens >= {budget_in}"
            if budget_out and self.cum["tokens_out"] >= budget_out:
                return f"token budget: {self.cum['tokens_out']} output tokens >= {budget_out}"
        return None

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {"segment": {**self.seg, "cost_usd": round(self.seg_cost, 4)},
                    "cumulative": {**self.cum, "cost_usd": round(self.cum_cost, 4)},
                    "served_models": dict(self.served)}


def notice(judge: str, model: str, rows: list[dict[str, Any]], max_tokens: int | None, inflight: int) -> dict[str, Any]:
    """The size notice ``preflight`` prints and ``run`` repeats: calls, estimated tokens, model,
    concurrency. Output tokens are estimated from the item count (≈ 40 tokens per label)."""
    chars = sum(len(build_prompt(r)) + len(SYSTEM) for r in rows)
    items = sum(len(r["items"]) for r in rows)
    return {"judge": judge, "model": model, "documents": len(rows), "calls_min": len(rows),
            "calls_max_with_retry": 2 * len(rows), "items": items,
            "est_input_tokens": round(chars / CHARS_PER_TOKEN), "est_output_tokens": 40 * items,
            "max_tokens_per_call": max_tokens, "concurrency": inflight, "rubric": RUBRIC,
            "system_sha256": SYSTEM_SHA256}


# --------------------------------------------------------------------------- the run
class JudgeRun:
    def __init__(self, args: argparse.Namespace, call: Any, model: str, rows: list[dict[str, Any]]) -> None:
        self.args, self.call, self.model = args, call, model
        self.out = Path(args.out)
        self.rows = rows
        self.pause_file = Path(args.pause_file) if args.pause_file else self.out.with_suffix(".PAUSE")
        self.totals = Totals(self.out.with_suffix(".totals.json"))
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.reason: list[str] = []
        self.exit = EXIT_OK
        self.consecutive_fail = 0
        self.seg_fail = 0
        self.first_done = threading.Event()
        self.first_lock = threading.Lock()
        self.inflight = max(1, min(MAX_INFLIGHT, int(args.inflight)))

    def halt(self, reason: str, code: int = EXIT_PAUSED) -> None:
        with self.lock:
            if not self.stop.is_set():
                self.reason.append(reason)
                self.exit = code
                self.stop.set()

    def write(self, rec: dict[str, Any]) -> None:
        with self.lock:
            fd = os.open(self.out, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())

    def totals_line(self, event: str) -> None:
        rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": event, "judge": self.args.judge,
               "model": self.model, **self.totals.snapshot()}
        fd = os.open(self.out.with_suffix(".totals.jsonl"), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        print(f"TOTALS {event}: {json.dumps(rec)}", flush=True)

    def doc_done(self, ok: bool) -> None:
        with self.lock:
            if ok:
                self.consecutive_fail = 0
            else:
                self.consecutive_fail += 1
                self.seg_fail += 1
            cf, sf = self.consecutive_fail, self.seg_fail
        self.totals.add(ok_docs=int(ok), failed_docs=int(not ok))
        if cf >= FAIL_CONSECUTIVE or sf > FAIL_TOTAL:
            self.halt(f"failures: {cf} in a row, {sf} in this segment")
        snap = self.totals.snapshot()["segment"]
        n = snap["ok_docs"] + snap["failed_docs"]
        if n and n % BATCH == 0:
            self.totals_line(f"batch {n // BATCH}")

    def gate(self) -> bool:
        if self.stop.is_set():
            return False
        if self.pause_file.exists():
            self.halt("PAUSE file present")
            return False
        why = self.totals.over_budget(self.args.budget_input_tokens, self.args.budget_output_tokens)
        if why:
            self.halt(why)
            return False
        return True

    def one(self, r: dict[str, Any]) -> None:
        if not self.gate():
            return
        prompt = build_prompt(r)
        rec: dict[str, Any] = {
            "key": r["key"], "corpus": r["corpus"], "judge": self.args.judge, "model_requested": self.model,
            "effort": self.args.effort, "rubric": RUBRIC, "system_sha256": SYSTEM_SHA256,
            "max_tokens": self.args.max_tokens if self.args.judge == "gateway" else None,
            "prompt_sha256": sha(prompt), "prompt_chars": len(prompt), "n_items": len(r["items"]),
            "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "calls": []}
        res, err, empties = None, None, 0
        for attempt in range(2):
            if not self.gate():
                return
            try:
                res = self.call(prompt)
            except (UsageLimit, Unreachable) as e:
                self.halt(f"{type(e).__name__}: {e}")
                return
            except (subprocess.TimeoutExpired, RuntimeError, OSError, ValueError) as e:
                err = f"{type(e).__name__}: {str(e)[:500]}"
                res = None
                if attempt == 0:
                    time.sleep(3)
                continue
            empty = not (res.get("text") or "").strip()
            self.totals.add(calls=1, tokens_in=res.get("context_tokens") or 0,
                            tokens_out=res.get("output_tokens") or 0, cost_usd=res.get("cost_usd"),
                            served_model=res.get("served_model"), empty=int(empty))
            rec["calls"].append({"tokens_in": res.get("context_tokens"), "tokens_out": res.get("output_tokens"),
                                 "finish": res.get("finish_reason"), "duration_s": res.get("duration_s"),
                                 "served_model": res.get("served_model"), "empty": empty})
            if empty:  # an empty answer is a failure: retry once, skip after two
                empties += 1
                err, res = "empty_answer", None
                continue
            err = None
            break
        if res is None:
            rec.update(ok=False, error=err, attempts=2, empties=empties, skipped=err == "empty_answer")
            self.write(rec)
            self.doc_done(False)
            return
        if self.args.judge in ("claude", "codex") and not self.first_done.is_set():
            with self.first_lock:
                if not self.first_done.is_set():
                    iso = isolation_check(self.args.judge, prompt, res, self.codex_overhead())
                    rec["isolation"] = iso
                    self.first_done.set()
                    if not iso["PASS"]:
                        rec.update(ok=False, error=f"ISOLATION FAILED {iso}")
                        self.write(rec)
                        self.halt("isolation failed", EXIT_FAIL)
                        return
        parsed = parse_labels(res["text"], r["items"])
        rec.update({k: v for k, v in res.items() if k not in ("text", "usage")})
        rec.update(raw_text=res["text"][:20000], labels=parsed["labels"], valid=parsed["valid"],
                   parse_error=parsed["parse_error"], ok=bool(parsed["valid"] and not res.get("is_error")),
                   error=None if parsed["valid"] else "invalid_labels", empties=empties,
                   finished=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        self.write(rec)
        self.doc_done(rec["ok"])

    def codex_overhead(self) -> int | None:
        f = self.args.codex_overhead_file
        if not f or not Path(f).exists():
            return None
        try:
            return int(json.loads(Path(f).read_text())["overhead_tokens"])
        except (ValueError, KeyError):
            return None

    def execute(self) -> int:
        done = set()
        if self.out.exists():
            for line in self.out.read_text(encoding="utf-8").splitlines():
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if o.get("ok"):
                    done.add(o["key"])
        todo = [r for r in self.rows if r["key"] not in done]
        print(f"{len(self.rows)} docs, {len(done)} already ok, {len(todo)} to run; out {self.out}", flush=True)
        prev_handler = None
        try:
            prev_handler = signal.signal(signal.SIGTERM, lambda *_: self.halt("SIGTERM"))
        except ValueError:  # not the main thread
            pass
        try:
            return self._execute(todo)
        finally:
            if prev_handler is not None:
                signal.signal(signal.SIGTERM, prev_handler)

    def _execute(self, todo: list[dict[str, Any]]) -> int:
        if todo:
            self.one(todo[0])  # the first document runs alone: the isolation check gates the rest
        if not self.stop.is_set() and len(todo) > 1:
            with ThreadPoolExecutor(self.inflight) as pool:
                for f in as_completed([pool.submit(self.one, r) for r in todo[1:]]):
                    f.result()
        self.totals_line("stopped" if self.stop.is_set() else "end")
        snap = {**self.totals.snapshot(), "status": "stopped" if self.stop.is_set() else "end",
                "reason": self.reason[:1], "judge": self.args.judge, "model": self.model, "rubric": RUBRIC,
                "system_sha256": SYSTEM_SHA256, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        tmp = self.totals.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(snap, indent=1))
        os.replace(tmp, self.totals.path)
        if self.stop.is_set():
            print(f"STOPPED: {self.reason[:1]}", flush=True)
            return self.exit
        return EXIT_OK


def gateway_url(args: argparse.Namespace) -> str:
    if not args.private_config:
        raise SystemExit("--judge gateway needs --private-config with gateway.base_url (no default)")
    cfg = json.loads(Path(args.private_config).read_text(encoding="utf-8"))
    url = (cfg.get("gateway") or {}).get("base_url")
    if not url:
        raise SystemExit("private config has no gateway.base_url")
    return str(url)


def load_rows(args: argparse.Namespace) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in Path(args.sample).read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.keys:
        ks = set(args.keys.split(","))
        rows = [r for r in rows if r["key"] in ks]
    if args.limit:
        rows = rows[:args.limit]
    return rows


def make_call(args: argparse.Namespace, model: str) -> Any:
    if args.judge == "claude":
        cwd = Path(args.cwd) if args.cwd else Path(tempfile.mkdtemp(prefix="judge_cwd_"))
        cwd.mkdir(mode=0o700, exist_ok=True)
        os.chmod(cwd, 0o700)
        if any(cwd.iterdir()):
            raise SystemExit(f"{cwd} must be empty (no CLAUDE.md / AGENTS.md may reach the judge)")
        return lambda prompt: call_claude(prompt, model, args.effort, cwd)
    if args.judge == "codex":
        tmp_root = Path(tempfile.mkdtemp(prefix="judge_codex_"))
        return lambda prompt: call_codex(prompt, args.codex_bin, model, tmp_root)
    url = gateway_url(args)
    return lambda prompt: call_gateway(prompt, model, args.max_tokens, url)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--judge", required=True, choices=("claude", "codex", "gateway"))
        p.add_argument("--model", default=None, help="claude/codex: pinned default; gateway: required")
        p.add_argument("--effort", default=None, help="claude --effort (default: the CLI's)")
        p.add_argument("--max-tokens", type=int, default=8000, help="gateway max_tokens (always sent)")
        p.add_argument("--private-config", default=None, help="JSON with gateway.base_url")
        p.add_argument("--inflight", type=int, default=3, help=f"concurrent documents (hard cap {MAX_INFLIGHT})")
        p.add_argument("--cwd", default=None, help="claude: an EMPTY directory to run in (default: a fresh temp dir)")
        p.add_argument("--codex-bin", default="codex")
        p.add_argument("--codex-overhead-file", default=None, help="probe output with overhead_tokens")

    pf = sub.add_parser("preflight", help="print the size notice; no call")
    common(pf)
    pf.add_argument("--sample", required=True)
    pf.add_argument("--keys", default="")
    pf.add_argument("--limit", type=int, default=0)
    pr = sub.add_parser("probe", help="one synthetic probe (no benchmark text)")
    common(pr)
    pr.add_argument("--out", required=True)
    rn = sub.add_parser("run", help="the real run (resumable)")
    common(rn)
    rn.add_argument("--sample", required=True)
    rn.add_argument("--out", required=True, help="labels JSONL (append-only)")
    rn.add_argument("--keys", default="")
    rn.add_argument("--limit", type=int, default=0)
    rn.add_argument("--pause-file", default=None)
    rn.add_argument("--budget-input-tokens", type=int, default=DEFAULT_BUDGET_IN)
    rn.add_argument("--budget-output-tokens", type=int, default=DEFAULT_BUDGET_OUT)
    rn.add_argument("--notice-acknowledged", action="store_true",
                    help="required for --judge gateway: the preflight notice has been sent")
    args = ap.parse_args(argv)

    if args.judge == "gateway" and not args.model:
        ap.error("--judge gateway needs --model (an id from GET /v1/models)")
    model = args.model or (CLAUDE_MODEL if args.judge == "claude" else CODEX_MODEL)
    inflight = max(1, min(MAX_INFLIGHT, args.inflight))

    if args.cmd == "preflight":
        n = notice(args.judge, model, load_rows(args), args.max_tokens if args.judge == "gateway" else None,
                   inflight)
        print("NOTICE " + json.dumps(n), flush=True)
        return EXIT_OK
    if args.cmd == "probe":
        prompt = build_prompt(PROBE_ROW)
        res = make_call(args, model)(prompt)
        res["parsed"] = parse_labels(res["text"], PROBE_ROW["items"])
        chars = len(prompt) + len(SYSTEM)
        res["implied_tokens"] = round(chars / CHARS_PER_TOKEN, 1)
        if args.judge == "codex" and res.get("context_tokens") is not None:
            res["overhead_tokens"] = max(0, res["context_tokens"] - round(chars / CHARS_PER_TOKEN))
        Path(args.out).write_text(json.dumps(res, indent=1))
        print(json.dumps(res, indent=1))
        return EXIT_OK
    if args.judge == "gateway" and not args.notice_acknowledged:
        print("REFUSED: run `preflight`, send the size notice, then rerun with --notice-acknowledged",
              file=sys.stderr)
        return EXIT_FAIL
    rows = load_rows(args)
    print("NOTICE " + json.dumps(notice(args.judge, model, rows, args.max_tokens if args.judge == "gateway"
                                        else None, inflight)), flush=True)
    return JudgeRun(args, make_call(args, model), model, rows).execute()


if __name__ == "__main__":
    sys.exit(main())
