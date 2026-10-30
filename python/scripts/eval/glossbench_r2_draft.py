#!/usr/bin/env python
"""Glossary benchmark round 2, harness item 2d: drafting, the three-layer
acceptance and the owner's spot-check sheet (prereg §3.2–3.3).

Subcommands (``--private-config CFG`` first; every output goes to the config's
private ``out_dir``)::

    preflight --stage draft|verify --set S   print the size notice; record it
    draft  --set S --notice-acknowledged [--resume]    drafter calls (gateway)
    check  --set S                           layer 1 over every draft (no model)
    verify --set S --notice-acknowledged [--resume]    layer 2 (Claude CLI, off-gateway)
    summary --set S                          accepted records + counts-only summary
    spot-export --set S OUT                  the owner's blind 40-record sheet (+ OUT key)
    spot-import --set S LABELS --key KEY     validity with a Wilson CI
    export-partner --set P OUT [--candidates]   the papers-OC owner's JSONL (fields mode)
    import-partner-verdicts --set P FILE     the owner's verdicts -> error rate + status label

Every set subcommand takes ``--pilot [N] [--papers K]`` (default 50 jobs over 3
papers): the pilot batch, drafted under the same rules from K seeded eval papers
(``<seed>|pilot``), written to its own ``*-<set>-pilot*`` files with records
marked ``pilot: true``, so the set's own accepted file and export never hold one.

**Fields mode** (set P, ``corpora.P.draft_mode: fields``; see
:mod:`glossbench_r2_fields`): one job per (chunk, field) from the pool's quota
plan; the drafter is asked for a value of the named field (name, definition,
unit; no glossary, no arm, no symbol hint, no term list); ``binding_terms`` are
the field's terms. The field list's sha256 is in the pool header and the state,
and a changed list is refused on load and on resume.

**Drafter** (one gateway model, default Gemini 2.5 Pro): the part-4 client
(:class:`deployed_docs_part4.Gateway`): ``fallback_model: ""`` and
``max_tokens`` in every body (asserted before each request), temperature 0
unless the model refuses it, no Authorization header, the served model id
recorded per call in the call log and the draft row. At most
:data:`_glossbench_r2.MAX_INFLIGHT` (4) requests in flight: a config asking for
more is refused. An empty answer is retried once after ``empty_retry_s`` (2 s);
still empty, the attempt is dropped and counted (``empty_twice``) and counts as a
gateway failure; ``finish_reason length`` is dropped and counted (``truncated``
/ ``cut``). Neither is redrawn. A draft that fails layer 1 (other than
``no_quantity``) is redrawn once (``max_attempts_per_chunk`` 2) with its
reject codes in the prompt.

**Stops.** A ``PAUSE`` file in the output directory or SIGTERM stops issuing,
lets in-flight calls finish and write, writes the state and the totals, exits
75. Also pausing: 5 failures in a row or more than 20 in total, HTTP 429/quota
after 3 backoffs, and the budget: cumulative gateway input tokens >=
``input_token_cap`` (default 5.0M) or output >= ``output_token_cap`` (0.75M) or
calls >= ``call_cap`` (4,000), counted across resumes plus
``prior_gateway_usage`` (what earlier steps of the round spent: a path or list of
paths to their totals JSON / the frontier ``glossary.jsonl``, summed automatically
by :func:`resolve_prior_usage`; a hand-entered dict is also accepted). Exit 1 = hard
stop (frozen-prereg sha mismatch, a resume against a different commit, config
or pool, a failed isolation check). ``--resume`` continues a paused run.

**Notice gate.** ``preflight`` prints one ``GATEWAY-NOTICE`` (or
``CLI-NOTICE``) line with the planned calls, estimated tokens in/out, models and
concurrency, and records it. ``draft``/``verify`` refuse to start without
``--notice-acknowledged`` (the human sends the notice), without a preflight for
the same plan, or after a pause without a fresh preflight.

**Verifier** (layer 2): ``claude -p --model <id> --output-format json
--restricted --no-session-persistence --strict-mcp-config --tools ""
--system-prompt ...``, the prompt on stdin, cwd an empty 0700 directory with no
``CLAUDE.md``/``AGENTS.md`` above it; the first call of a run must pass the
context-size isolation check (round 1's judge rule). Empty or unparseable:
retried once, then the record is rejected (``l2_empty`` / ``l2_unparseable``).
A usage-limit signal pauses.

**Codex fallback verifier.** A usage-policy refusal by the Claude CLI (its safeguards
flag the verifier prompt: :func:`is_refusal` on the error text, never a transport error
or a usage limit) is not retried on Claude: that record alone is verified by the Codex
CLI (``codex exec --ephemeral --ignore-user-config --ignore-rules -s read-only -m
<verifier.codex_model>`` with every optional instruction source and tool switched off,
:meth:`Run.codex_cmd`), the same system prompt and verifier prompt (stdin), the same
JSON contract, the same empty 0700 cwd (its system-prompt, schema and last-message
files live in a private temporary directory under the output directory, never in the
cwd). One codex call at a time (it answers 429 at 3). The first codex call of a run
passes its own isolation check (Codex's fixed harness overhead,
``verifier.codex_overhead_tokens``, and no tool event). Every verify row and ``l2`` log
row records ``verifier: claude | codex`` and, for a codex row, Claude's refusal text.
A codex limit, timeout or transport error pauses the run (exit 75) with the record still
pending, retried on resume. Only a definitive codex failure — codex refuses the record
too, or answers empty / malformed twice — makes it ``verifier_unavailable``: logged as
verdict ``unavailable`` with the reason, excluded (not rejected), never accepted.
In the set-P pilot one record was refused deterministically and the run paused on it
for ever as a ``cli_error``.

**Yield guards (round-3 pools).** ``draft`` refuses to start when the pool's
pre-drafting estimate (``glossbench_r3_pool``, ``yield.status``) is not ``ADEQUATE``
unless ``--allow-low-yield`` (recorded in the state). During drafting, once the first
``yield_watch.after_jobs`` jobs (default 100) are done, a layer-1 pass rate below
``yield_watch.min_pass`` (default 0.30) pauses the run (exit 75, the counts in the
reason) while jobs remain; a resume needs ``--accept-low-yield`` (recorded). The watch
runs once per run; its result is in the state.

**Accepted records** ``accepted-<set>.jsonl`` (PRIVATE): one line per record
that passed layers 1 and 2, with the field names the wording proxy
(``wording_proxy.py --records``) reads — ``record_id``, ``gold_chunks`` (the
quote-bearing chunk ids), ``binding_terms``, ``quantity``, ``stratum`` — plus
``question``, ``quote``, ``value``, ``symbol``, ``paper``, ``doc_id``,
``source_chunk_id``, ``flags``.

**Acceptance log** ``acceptance-<set>.jsonl``: append-only, one row per
(layer, record) — ``draft`` (dropped attempts), ``l1``, ``l2`` — with the
verdict, every reason code and the flags. Re-running ``check`` appends only
what is new and refuses a verdict that differs from a logged one.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import re
import signal
import subprocess
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import _glossbench_r2 as g  # noqa: E402
import deployed_docs_part4 as p4  # noqa: E402
import glossbench_r2_checks as ck  # noqa: E402
import glossbench_r2_fields as fl  # noqa: E402

EXIT_OK, EXIT_HARD_STOP, EXIT_PAUSED = 0, 1, 75
STAGES = ("draft", "verify")
STATE_SCHEMA = "glossbench-r2-state/1"
TOTALS_SCHEMA = "glossbench-r2-totals/1"
SUMMARY_SCHEMA = "glossbench-r2-acceptance-summary/1"
SPOT_SCHEMA = "glossbench-r2-spot/1"
SPOT_LABELS = ("valid", "invalid")
PARTNER_SCHEMA = "glossbench-r2-partner-export/1"
PARTNER_VERDICTS_SCHEMA = "glossbench-r2-partner-verdicts/1"
PARTNER_VERDICTS = ("accept", "reject")
PARTNER_BASES = ("scripted", "sampled")
PILOT_DEFAULT_RECORDS, PILOT_DEFAULT_PAPERS = 50, 3
#: the round-3 pool's header schema (glossbench_r3_pool.POOL_SCHEMA)
R3_POOL_SCHEMA = "glossbench-r3-pool/1"
#: prereg §3.5 (frozen 39c579e1) and DEVIATIONS D-1: the set-P promotion condition is the
#: Wilson 95% upper bound of the partner's judged pilot error rate <= 0.15 with at least 30
#: judged; computed from the raw counts at z = 1.959963984540054, compared at full float
#: precision (8 of 100 -> 0.149981 passes, 9 of 100 -> 0.162262 fails), never from a bound
#: the partner reports rounded.
WILSON_Z = 1.959963984540054
PROMOTION_THRESHOLD, PROMOTION_MIN_JUDGED = 0.15, 30
DROPPED = ("empty_twice", "truncated", "cut")
LIMIT_RE = re.compile(
    r"rate.?limit|usage.?limit|quota|overloaded|429|too many requests|"
    r"insufficient.?credit|billing|out of credit|hit your .{0,20}limit", re.I)
CHARS_PER_TOKEN_GATEWAY = 3.4
CHARS_PER_TOKEN_CLI = 3.5
ISOLATION_FACTOR = 2.0
CLAUDE_FIXED_OVERHEAD = 1.25 * 574
#: Codex's fixed context under the isolation flags (its own instructions, which no flag
#: removes), measured by a synthetic probe on gpt-6-astra: 2,025 tokens
CODEX_FIXED_OVERHEAD = 2025
#: a usage-policy refusal (Claude's safeguards), told apart from a limit or a transport error
REFUSAL_RE = re.compile(
    r"safeguards?\b.{0,40}\bflagged|flagged\b.{0,40}\b(?:usage|acceptable use) polic|"
    r"usage polic(?:y|ies)|acceptable use polic(?:y|ies)|/legal/aup\b|\(AUP\)|\bAUP\b", re.I)

#: codex refusing the record too (a policy block in its error events or stderr): definitive.
#: No bare "refused"/"refusal": a transport error ("Connection refused", os error 111) must
#: pause, not exclude the record.
CODEX_REFUSAL_RE = re.compile(
    REFUSAL_RE.pattern + r"|content.?polic|policy.?violation|cyber.?(?:safety|polic)|"
    r"invalid_prompt|flagged as potentially", re.I)
#: what the codex fallback switches off (the gold-labelling judge's proven list)
CODEX_DISABLED_FEATURES = (
    "shell_tool", "unified_exec", "apps", "plugins", "remote_plugin", "multi_agent", "goals", "hooks",
    "view_image", "skill_search", "tool_suggest", "browser_use", "browser_use_external", "computer_use",
    "image_generation", "in_app_browser", "sleep_tool", "code_mode_host", "shell_snapshot", "memories",
    "personality", "skill_mcp_dependency_install", "workspace_dependencies")
#: the verifier's JSON contract (ck.VERIFY_SYSTEM) as codex's output schema
VERIFY_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {**{k: {"type": "boolean"} for k in ck.VERIFY_KEYS}, "reason": {"type": "string"}},
    "required": [*ck.VERIFY_KEYS, "reason"]}


def is_refusal(stdout: str, stderr: str, obj: dict[str, Any] | None) -> bool:
    """A usage-policy refusal by the Claude CLI: its JSON says ``stop_reason: refusal``, or
    the error text (the result, ``api_error_status``, stderr; stdout when it is not JSON)
    carries the safeguards / usage-policy wording (:data:`REFUSAL_RE`). Only asked of a
    failed call (non-zero exit or ``is_error``); :meth:`Run.call_cli` also takes
    ``stop_reason: refusal`` on a successful exit."""
    if obj is not None:
        if str(obj.get("stop_reason") or "").casefold() == "refusal":
            return True
        blob = " ".join(str(obj.get(k) or "") for k in ("result", "api_error_status", "error"))
    else:
        blob = stdout
    return bool(REFUSAL_RE.search(blob + " " + (stderr or "")))


DEFAULTS: dict[str, Any] = {
    # max_tokens 4096: Gemini 2.5 Pro's hidden thinking can spend ~1024 before any visible
    # output, and a cut answer is dropped, not retried. Always set (operator term).
    "drafter": {"model": "Gemini 2.5 Pro", "tag": "gemini25pro", "max_tokens": 4096, "temperature": 0,
                "base_url": None, "inflight": 4, "max_attempts_per_chunk": 2, "empty_retry_s": 2.0,
                "attempts_factor": 1.3, "est_output_tokens": 250, "passes": 2},
    "verifier": {"model": "claude-opus-5-5", "cli": "claude", "inflight": 3, "timeout_s": 300,
                 "head_chars": 2000, "cwd": None, "isolation_check": True, "max_calls": 4000,
                 "max_cost_usd": 100.0, "est_output_tokens": 120,
                 # the fallback for a usage-policy refusal (one record at a time)
                 "codex_fallback": True, "codex_cli": "codex", "codex_model": "gpt-6-astra",
                 "codex_effort": "medium", "codex_overhead_tokens": CODEX_FIXED_OVERHEAD},
    "limits": {"input_token_cap": 5_000_000, "output_token_cap": 750_000, "call_cap": 4000,
               "prior_gateway_usage": {}, "max_fail_consecutive": 5, "max_fail_total": 20,
               "rate_backoffs_s": [30.0, 120.0, 600.0], "batch_calls": None,
               "batch_input_tokens": None, "batch_output_tokens": None},
    "checks": dict(ck.CHECK_DEFAULTS),
    "spot": {"n": 40, "seed_base": "261101|spot", "threshold": 0.85},
    #: round-3 pools only: after the first ``after_jobs`` done jobs, a layer-1 pass rate below
    #: ``min_pass`` pauses the draft (resume with --accept-low-yield)
    "yield_watch": {"after_jobs": 100, "min_pass": 0.30},
    "prereg": {"file": None, "sha256": None},
}


def merged_config(cfg: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in DEFAULTS.items():
        given = cfg.get(k) or {}
        unknown = set(given) - set(v)
        if unknown:
            raise SystemExit(f"config block {k!r}: unknown keys {sorted(unknown)}")
        out[k] = {**v, **given}
    for blk in ("drafter", "verifier"):
        n = int(out[blk]["inflight"])
        if not 1 <= n <= g.MAX_INFLIGHT:
            raise SystemExit(f"{blk}.inflight = {n}: at most {g.MAX_INFLIGHT} in flight (hard cap)")
    return out


def notice(kind: str, msg: str) -> None:
    """One line for the human to send (printed only; nothing is sent anywhere)."""
    print(f"{kind}-NOTICE glossbench-r2: {msg}", flush=True)


class Paths:
    def __init__(self, out: Path, set_: str) -> None:
        self.out, self.set = out, set_

    def __getattr__(self, name: str) -> Path:
        names = {"pool": "pool-{s}.jsonl", "drafts": "draft-{s}.jsonl", "calls": "draft-calls-{s}.jsonl",
                 "verify_rows": "verify-{s}.jsonl", "acceptance": "acceptance-{s}.jsonl",
                 "accepted": "accepted-{s}.jsonl", "summary": "acceptance-summary-{s}.json",
                 "spot_results": "spot-results-{s}.json", "pause": "PAUSE",
                 "partner_export": "partner-export-{s}.json", "partner_verdicts": "partner-verdicts-{s}.json"}
        if name not in names:
            raise AttributeError(name)
        return self.out / names[name].format(s=self.set)

    def state(self, stage: str) -> Path:
        return self.out / f"{stage}-state-{self.set}.json"

    def preflight(self, stage: str) -> Path:
        return self.out / f"preflight-{stage}-{self.set}.json"

    def totals(self, stage: str) -> Path:
        return self.out / f"{stage}-totals-{self.set}.json"


# ------------------------------------------------------------------ control

PRIOR_KEYS = ("calls", "prompt_tokens", "completion_tokens")


def _usage_of_file(path: Path) -> dict[str, int]:
    """The gateway usage one earlier-stage file records, as ``{calls, prompt_tokens,
    completion_tokens}``. Accepted shapes: this script's totals/state JSON or part
    4's (``totals.since_start.gateway_*`` or ``since_start.gateway_*``); a flat
    ``{calls, prompt_tokens, completion_tokens}`` JSON; a ``glossary.jsonl`` written
    by the glossary step (the frontier batch: every row's ``provenance.llm.calls``
    summed, each call record counting ``1 + retries`` calls)."""
    if path.suffix == ".jsonl":
        u = Counter()
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("kind") == "header":
                    continue
                lp = (row.get("provenance") or {}).get("llm")
                for call in (lp or {}).get("calls") or []:
                    u["calls"] += 1 + int(call.get("retries") or 0)
                    u["prompt_tokens"] += int(call.get("prompt_tokens") or 0)
                    u["completion_tokens"] += int(call.get("completion_tokens") or 0)
        return {k: int(u[k]) for k in PRIOR_KEYS}
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise SystemExit(f"prior_gateway_usage: {path.name}: not a JSON object")
    since = (obj.get("totals") or {}).get("since_start") if isinstance(obj.get("totals"), dict) else None
    since = since if since is not None else obj.get("since_start")
    if isinstance(since, dict):
        return {k: int(since.get(f"gateway_{k}") or 0) for k in PRIOR_KEYS}
    if all(k in obj for k in PRIOR_KEYS):
        return {k: int(obj[k]) for k in PRIOR_KEYS}
    raise SystemExit(f"prior_gateway_usage: {path.name}: no totals.since_start, since_start or "
                     f"{{{', '.join(PRIOR_KEYS)}}} found")


def resolve_prior_usage(spec: Any, own_files: Sequence[Path] = ()) -> dict[str, Any]:
    """``limits.prior_gateway_usage``: a hand-entered ``{calls, prompt_tokens,
    completion_tokens}`` dict, or a path / list of paths to earlier stages' files
    (see :func:`_usage_of_file`), summed. A dict may also carry ``"files": [...]``,
    added to its numbers. ``own_files`` (this stage's own state/totals) are refused:
    a resume already carries them. Returns ``{"calls", "prompt_tokens",
    "completion_tokens", "sources": [{name, sha256, usage}]}``."""
    total = Counter()
    sources: list[dict[str, Any]] = []
    paths: list[str] = []
    if spec is None or spec == {} or spec == []:
        pass
    elif isinstance(spec, str):
        paths = [spec]
    elif isinstance(spec, list):
        paths = [str(p) for p in spec]
    elif isinstance(spec, dict):
        unknown = set(spec) - {*PRIOR_KEYS, "files"}
        if unknown:
            raise SystemExit(f"prior_gateway_usage: unknown keys {sorted(unknown)}")
        total.update({k: int(spec.get(k) or 0) for k in PRIOR_KEYS})
        paths = [str(p) for p in (spec.get("files") or [])]
    else:
        raise SystemExit("prior_gateway_usage: a dict, a path or a list of paths")
    own = {p.resolve() for p in own_files}
    for p in paths:
        path = Path(p)
        if not path.is_file():
            raise SystemExit(f"prior_gateway_usage: file not found: {path}")
        if path.resolve() in own:
            raise SystemExit(f"prior_gateway_usage: {path.name} is this stage's own record (a resume "
                             "already carries it); list earlier stages and other sets only")
        usage = _usage_of_file(path)
        total.update(usage)
        sources.append({"name": path.name, "sha256": g.file_sha(path), "usage": usage})
    return {**{k: int(total[k]) for k in PRIOR_KEYS}, "sources": sources}


class R2Control(p4.Control):
    """Part 4's pause triggers, plus the round's cumulative budget: gateway input
    tokens, output tokens and calls since the first segment, plus the usage of
    earlier steps (``prior_gateway_usage``, resolved by :func:`resolve_prior_usage`)."""

    def __init__(self, limits: dict[str, Any], pause_path: Path, prev_totals: dict[str, int] | None,
                 own_files: Sequence[Path] = ()) -> None:
        super().__init__({**limits, "output_token_cap": None}, pause_path, prev_totals)
        self.caps = limits
        self.prior_resolved = resolve_prior_usage(limits.get("prior_gateway_usage"), own_files)
        self.prior = Counter({"gateway_calls": self.prior_resolved["calls"],
                              "gateway_prompt_tokens": self.prior_resolved["prompt_tokens"],
                              "gateway_completion_tokens": self.prior_resolved["completion_tokens"]})

    def cumulative(self) -> Counter[str]:
        return self.prior + self.prev + self.seg

    def gateway_admit(self) -> bool:
        if not super().gateway_admit():
            return False
        tot, lim = self.cumulative(), self.caps
        if lim.get("input_token_cap") is not None and tot["gateway_prompt_tokens"] >= lim["input_token_cap"]:
            self.stop("input_token_cap")
        elif lim.get("output_token_cap") is not None and tot["gateway_completion_tokens"] >= lim["output_token_cap"]:
            self.stop("output_token_cap")
        elif lim.get("call_cap") is not None and tot["gateway_calls"] >= lim["call_cap"]:
            self.stop("call_cap")
        return self.reason is None


class R2Gateway(p4.Gateway):
    """The part-4 client with the operator terms asserted on every body."""

    def body(self, spec: dict[str, Any], msgs: list[dict[str, str]]) -> dict[str, Any]:
        b = super().body(spec, msgs)
        assert b.get("fallback_model") == "" and int(b.get("max_tokens") or 0) > 0, "gateway terms"
        return b


async def run_jobs(jobs: list[Callable[[Any], Any]], inflight: int, control: p4.Control, http: Any) -> None:
    """``async fn(http)`` jobs, at most ``inflight`` (<= 4) at once; a job not yet
    started when the run stops is skipped; in-flight jobs finish."""
    if not 1 <= inflight <= g.MAX_INFLIGHT:
        raise SystemExit(f"inflight {inflight}: at most {g.MAX_INFLIGHT}")
    sem = asyncio.Semaphore(inflight)

    async def one(fn: Callable[[Any], Any]) -> None:
        async with sem:
            if control.stopped():
                return
            await fn(http)

    await asyncio.gather(*(one(fn) for fn in jobs))


def default_http() -> Any:
    import httpx

    return httpx.AsyncClient(timeout=900)


# ------------------------------------------------------------------ inputs

def read_pool(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not path.is_file():
        raise SystemExit(f"pool file not found: {path.name} (run glossbench_r2_pool.py build first)")
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    if not rows or rows[0].get("kind") != "header":
        raise SystemExit(f"{path.name}: no header line")
    return rows[0], rows[1:]


def record_id(set_: str, chunk_id: str, attempt: int, field: str | None = None) -> str:
    """Open mode: ``<set>-<sha(chunk)>-a<n>``; fields mode: the chunk and the field."""
    key = chunk_id if field is None else f"{chunk_id}|{field}"
    return f"{set_}-{g.sha_text(key)[:12]}-a{attempt}"


class Run:
    """One set's drafting/acceptance directory."""

    def __init__(self, cfg: dict[str, Any], cfg_sha: str, out: Path, set_: str,
                 provenance: Callable[[], dict[str, Any]] | None = None,
                 http_factory: Callable[[], Any] | None = None,
                 cli_runner: Callable[..., Any] | None = None,
                 pilot: tuple[int, int] | None = None) -> None:
        self.raw_cfg = cfg
        self.cfg = merged_config(cfg)
        self.cfg_sha = cfg_sha
        self.out, self.set = out, set_
        if pilot is not None and (int(pilot[0]) < 1 or int(pilot[1]) < 1):
            raise SystemExit("--pilot N --papers K: both at least 1")
        self.pilot = None if pilot is None else (int(pilot[0]), int(pilot[1]))
        #: a pilot writes its own files (``<set>-pilot``) and its records carry ``pilot: true``,
        #: so the hypothesis sets (the set's own accepted file and export) never contain them
        self.label = set_ if self.pilot is None else f"{set_}-pilot"
        self.p = Paths(out, self.label)
        self.provenance = provenance or g.experiment_provenance
        self.http_factory = http_factory or default_http
        self.cli_runner = cli_runner or subprocess.run
        self._corpus: g.Corpus | None = None
        self._lock = threading.Lock()
        self._codex_lock = threading.Lock()
        #: the draft's yield guards (round-3 pools; written into the state)
        self.yield_watch: dict[str, Any] | None = None
        self.low_yield_allowed: dict[str, Any] | None = None

    # -------------------------------------------------------- inputs
    def load(self) -> None:
        self.header, self.pool = read_pool(Paths(self.out, self.set).pool)
        self.pool_sha = g.file_sha(Paths(self.out, self.set).pool)
        # the pool's statistical stoplist is the one the checks use (one list per round)
        self.checks_cfg = {**self.cfg["checks"], "vs_stoplist": self.header.get("vs_stoplist")}
        self.mode = self.header.get("draft_mode", "open")
        #: round 3's pool (glossbench_r3_pool.py): strata V / M_num; M_num chunks carry the
        #: paper's symbols they mention (``sigma_tokens``) and ask for ``symbol_is_the_quantity``
        self.round3 = self.header.get("schema") == R3_POOL_SCHEMA
        #: the round-3 pool's pre-drafting yield estimate (glossbench_r3_pool, ``yield``)
        self.pool_yield: dict[str, Any] | None = self.header.get("yield") if self.round3 else None
        cc = g.corpus_config(self.raw_cfg, self.set)
        self.fc = fl.field_config(cc, self.set)
        if (self.fc is not None) != (self.mode == "fields"):
            raise SystemExit(f"set {self.set}: the config's draft_mode differs from the pool's ({self.mode})")
        self.fields_sha: str | None = None
        self.specs: dict[str, fl.FieldSpec] = {}
        if self.fc is not None:
            # the field list's sha is the pool header's; a changed file is refused (and the
            # state records it, so a resume against another list is refused too)
            self.fields_sha = g.file_sha(self.fc["path"])
            if self.fields_sha != self.header.get("fields_sha256"):
                raise SystemExit("the field list differs from the one the pool was built with "
                                 "(fields_sha256): rebuild the pool")
            self.specs = {d["field"]: fl.FieldSpec.from_dict(d) for d in self.header["field_specs"]}
        self.pilot_plan: dict[str, Any] | None = None
        self.sampled = self.pilot_jobs() if self.pilot is not None else self.jobs_of(
            [r for r in self.pool if r["sampled"]])
        for j in self.sampled:
            if j["field"] is not None and j["field"] not in self.specs:
                raise SystemExit(f"job field {j['field']!r} is not in the field list: refused")
        self.sampled.sort(key=lambda r: (r["paper"], r["stratum"], r["sample_rank"] or 0, r["chunk_id"],
                                         r["field"] or ""))

    def jobs_of(self, rows: list[dict[str, Any]], assigned: dict[str, list[str]] | None = None) -> list[dict[str, Any]]:
        """Drafting jobs: one per sampled chunk (open mode), one per (chunk, field) (fields mode)."""
        if self.mode != "fields":
            return [{**r, "field": None} for r in rows]
        # round 3: does the chunk name one of the job's field terms (the pool's ranking)?
        return [{**r, "field": f, "term_in_chunk": (f in r["field_terms"]) if "field_terms" in r else None}
                for r in rows
                for f in (assigned.get(r["chunk_id"], []) if assigned is not None else r["field_jobs"])]

    def pilot_jobs(self) -> list[dict[str, Any]]:
        """``--pilot N --papers K``: K eval papers (seeded ``<seed>|pilot``) and N jobs
        among them under the same rules (fields mode: the same quota plan, scaled to N)."""
        n, k = self.pilot  # type: ignore[misc]
        seed = f"{self.header['seed']}|pilot"
        if self.mode == "fields":
            rows = [r for r in self.pool if r.get("field_candidates")]
        else:
            rows = [r for r in self.pool if r["sampled"]]
        papers = sorted({r["paper"] for r in rows})
        pick = set(random.Random(seed).sample(papers, min(k, len(papers))))
        rows = [r for r in rows if r["paper"] in pick]
        if self.mode != "fields":
            rows.sort(key=lambda r: (r["sample_rank"], r["paper"], r["chunk_id"]))
            return self.jobs_of(rows[:n])
        assert self.fc is not None
        per_paper = self.header["field_plan"]["per_paper"]
        targets = fl.cell_targets(sorted(pick), per_paper, n)
        # round 3's pool ranks a field's chunks by term presence (field_terms); round 2's has none
        rank = ({(r["chunk_id"], f): (0 if f in r["field_terms"] else 1)
                 for r in rows for f in r.get("field_candidates") or ()}
                if rows and all("field_terms" in r for r in rows) else None)
        self.pilot_plan, assigned = fl.plan_strata(list(self.specs.values()), self.fc, rows, targets, seed,
                                                   rank=rank)
        return self.jobs_of(rows, assigned)

    @property
    def corpus(self) -> g.Corpus:
        if self._corpus is None:
            cc = g.corpus_config(self.raw_cfg, self.set)
            shas = [g.file_sha(x) for x in cc["chunks"]]
            if shas != self.header["inputs_sha256"]["chunks"]:
                raise p4.HardStop("chunk_export_differs_from_pool")
            self._corpus = g.build_corpus(g.read_chunks(cc["chunks"]), cc.get("paper_key", "doc_id"))
            for r in self.sampled:
                c = self._corpus.chunks.get(r["chunk_id"])
                if c is None or g.sha_text(c.content) != r["content_sha256"]:
                    raise p4.HardStop("pool_chunk_text_mismatch")
            self.idx = ck.PaperIndex(self._corpus, int(self.cfg["checks"]["top_frequent"]))
        return self._corpus

    def prereg_sha(self) -> str | None:
        pr = self.cfg["prereg"]
        if not pr.get("file"):
            return None
        return g.file_sha(pr["file"])

    def plan_sha(self, stage: str) -> str:
        return g.sha_text(json.dumps({"stage": stage, "set": self.set, "config": self.cfg_sha,
                                      "pool": self.pool_sha, "fields": self.fields_sha,
                                      "pilot": list(self.pilot) if self.pilot else None}, sort_keys=True))

    # -------------------------------------------------------- drafting plan
    def drafts(self) -> p4.Store:
        return p4.Store(self.p.drafts)

    def draft_key(self, job: dict[str, Any], attempt: int) -> str:
        if job.get("field") is None:
            return f"draft|{job['chunk_id']}|a{attempt}"
        return f"draft|{job['chunk_id']}|{job['field']}|a{attempt}"

    def chunk_done(self, st: p4.Store, r: dict[str, Any]) -> bool:
        n = int(self.cfg["drafter"]["max_attempts_per_chunk"])
        for a in range(1, n + 1):
            row = st.get(self.draft_key(r, a))
            if row is None:
                return False
            if row["status"] in DROPPED or row["l1_inline"]["verdict"] == "pass" \
                    or set(row["l1_inline"]["reasons"]) & ck.REDRAW_EXEMPT:
                return True
        return True

    def messages_for(self, r: dict[str, Any], previous: dict[str, Any] | None) -> list[dict[str, str]]:
        c = self.corpus.chunks[r["chunk_id"]]
        prev = None if previous is None else {"reasons": previous["l1_inline"]["reasons"],
                                              "draft": previous.get("draft")}
        if r.get("field") is not None:
            if r["stratum"] == "M_num":
                return ck.field_draft_messages(c.content, self.specs[r["field"]], prev, symbol_bound=True)
            return ck.field_draft_messages(c.content, self.specs[r["field"]], prev)
        if r["stratum"] == "M_num":
            return ck.draft_messages(c.content, r["stratum"], r["vs_tokens"], prev,
                                     sigma_tokens=r.get("sigma_tokens") or [])
        return ck.draft_messages(c.content, r["stratum"], r["vs_tokens"], prev)

    # -------------------------------------------------------- preflight
    def preflight(self, stage: str) -> dict[str, Any]:
        self.load()
        if stage == "draft":
            st = self.drafts()
            todo = [r for r in self.sampled if not self.chunk_done(st, r)]
            d = self.cfg["drafter"]
            chars = sum(sum(len(m["content"]) for m in self.messages_for(r, None)) for r in todo)
            factor = float(d["attempts_factor"])
            plan = {"calls": math.ceil(len(todo) * factor), "chunks": len(todo),
                    "est_input_tokens": math.ceil(chars / CHARS_PER_TOKEN_GATEWAY * factor),
                    "est_output_tokens": math.ceil(len(todo) * factor * int(d["est_output_tokens"])),
                    "max_output_tokens": math.ceil(len(todo) * factor * int(d["max_tokens"])),
                    "models": [d["model"]], "concurrency": int(d["inflight"]),
                    "caps": {k: self.cfg["limits"][k] for k in ("input_token_cap", "output_token_cap", "call_cap")},
                    "draft_mode": self.mode, "fields_sha256": self.fields_sha,
                    "pool_yield": None if not self.round3 else {
                        k: (self.pool_yield or {}).get(k) for k in ("expected_yield", "gate", "status")},
                    "yield_watch": dict(self.cfg["yield_watch"]) if self.round3 else None,
                    "pilot": None if self.pilot is None else {"records": self.pilot[0], "papers": self.pilot[1],
                                                              "plan": self.pilot_plan}}
            kind = "GATEWAY"
        else:
            todo = self.verify_todo()
            v = self.cfg["verifier"]
            chars = sum(len(self.verify_prompt(rec)) + len(ck.VERIFY_SYSTEM) for rec in todo)
            plan = {"calls": len(todo), "est_input_tokens": math.ceil(chars / CHARS_PER_TOKEN_CLI),
                    "est_output_tokens": len(todo) * int(v["est_output_tokens"]), "models": [v["model"]],
                    "concurrency": int(v["inflight"]), "route": "CLI (off-gateway)",
                    "refusal_fallback": (f"codex {v['codex_model']}, 1 in flight, only on a usage-policy refusal"
                                         if v["codex_fallback"] else None),
                    "caps": {"max_calls": v["max_calls"], "max_cost_usd": v["max_cost_usd"]}}
            kind = "CLI"
        rec = {"stage": stage, "set": self.set, "created": g.now_iso(), "created_epoch": time.time(),
               "plan_sha256": self.plan_sha(stage), "plan": plan}
        g.write_json_atomic(self.p.preflight(stage), rec)
        notice(kind, f"{stage} set={self.label}: {plan['calls']} calls, est. {plan['est_input_tokens']} tokens in / "
               f"{plan['est_output_tokens']} out, models {', '.join(plan['models'])}, "
               f"<= {plan['concurrency']} in flight")
        return rec

    def gate(self, stage: str, acknowledged: bool, state: dict[str, Any] | None) -> None:
        if not acknowledged:
            raise SystemExit(f"{stage}: refusing to start without --notice-acknowledged "
                             "(run preflight, send the notice, then pass the flag)")
        pf = self.p.preflight(stage)
        if not pf.is_file():
            raise SystemExit(f"{stage}: no preflight record; run preflight --stage {stage} first")
        rec = json.loads(pf.read_text())
        if rec.get("plan_sha256") != self.plan_sha(stage):
            raise SystemExit(f"{stage}: the preflight was for a different plan; run preflight again")
        if state and state.get("paused_epoch") and rec.get("created_epoch", 0) <= state["paused_epoch"]:
            raise SystemExit(f"{stage}: the run paused after the last preflight; run preflight again "
                             "and send a fresh notice before resuming")

    # -------------------------------------------------------- state
    def read_state(self, stage: str) -> dict[str, Any] | None:
        p = self.p.state(stage)
        return json.loads(p.read_text()) if p.exists() else None

    def write_state(self, stage: str, status: str, reason: str | None) -> None:
        segs = list((self.prev_state or {}).get("segments", []))
        segs.append({"started": self.started, "ended": g.now_iso(), "end": status, "reason": reason,
                     "totals_since_resume": dict(self.control.seg), "resumed": self.resume})
        st = {"schema": STATE_SCHEMA, "stage": stage, "set": self.set, "status": status, "reason": reason,
              "totals": self.control.totals(), "commit": self.commit, "config_sha256": self.cfg_sha,
              "pool_sha256": self.pool_sha, "prereg_sha256": self.prereg, "segments": segs,
              "fields_sha256": self.fields_sha, "pilot": list(self.pilot) if self.pilot else None,
              "updated": g.now_iso(),
              **({"yield_watch": self.yield_watch, "low_yield_allowed": self.low_yield_allowed}
                 if stage == "draft" and self.round3 else {}),
              "paused_epoch": time.time() if status in ("paused", "crashed") else
              (self.prev_state or {}).get("paused_epoch")}
        g.write_json_atomic(self.p.state(stage), st)

    def begin(self, stage: str, resume: bool, acknowledged: bool) -> int | None:
        """Common start: inputs, state/resume checks, the notice gate, the prereg sha."""
        self.resume = resume
        self.started = g.now_iso()
        self.load()
        self.prev_state = self.read_state(stage)
        prov = self.provenance()
        self.prov, self.commit = prov, prov.get("commit")
        self.prereg = self.prereg_sha()
        prev_tot = None
        if self.prev_state is not None:
            st = self.prev_state
            if not resume:
                g.log(f"{stage}: {self.p.state(stage).name} exists; continue with --resume")
                return EXIT_HARD_STOP
            if st.get("status") == "hard_stop":
                g.log(f"{stage} --resume refused: the run hard-stopped ({st.get('reason')})")
                return EXIT_HARD_STOP
            bad = [k for k, v in (("commit", self.commit), ("config_sha256", self.cfg_sha),
                                  ("pool_sha256", self.pool_sha), ("prereg_sha256", self.prereg),
                                  ("fields_sha256", self.fields_sha),
                                  ("pilot", list(self.pilot) if self.pilot else None))
                   if st.get(k) != v]
            if bad:
                g.log(f"{stage} --resume refused (hard stop): {', '.join(bad)} differ from the state file")
                return EXIT_HARD_STOP
            prev_tot = st.get("totals", {}).get("since_start")
        elif resume:
            g.log(f"{stage} --resume: no state file")
            return EXIT_HARD_STOP
        self.gate(stage, acknowledged, self.prev_state)
        self.control = R2Control(self.cfg["limits"], self.p.pause, prev_tot,
                                 own_files=(self.p.state(stage), self.p.totals(stage)))
        frozen = self.cfg["prereg"].get("sha256")
        if frozen and frozen != self.prereg:
            self.write_state(stage, "hard_stop", "prereg_sha_mismatch")
            g.log(f"{stage}: the prereg file does not match the frozen sha256 (hard stop)")
            return EXIT_HARD_STOP
        return None

    def finish(self, stage: str, body: Callable[[], None]) -> int:
        old = signal.getsignal(signal.SIGTERM)
        try:
            signal.signal(signal.SIGTERM, lambda *_: self.control.stop("sigterm"))
        except ValueError:  # not the main thread (tests)
            old = None
        try:
            self.write_state(stage, "running", None)
            body()
        except p4.Pause as e:
            self.write_state(stage, "paused", e.reason)
            self.write_totals(stage, "paused", e.reason)
            notice("GATEWAY" if stage == "draft" else "CLI",
                   f"{stage} set={self.set} PAUSED ({e.reason}); totals "
                   f"{json.dumps(self.control.totals()['since_start'], sort_keys=True)}; "
                   "run preflight again, send the notice, then resume with --resume")
            return EXIT_PAUSED
        except p4.HardStop as e:
            self.write_state(stage, "hard_stop", e.reason)
            self.write_totals(stage, "hard_stop", e.reason)
            g.log(f"{stage} HARD STOP: {e.reason}")
            return EXIT_HARD_STOP
        except Exception as e:  # noqa: BLE001 - recorded; resumable after the cause is fixed
            reason = f"{type(e).__name__}: {str(e)[:200]}"
            self.write_state(stage, "crashed", reason)
            self.write_totals(stage, "crashed", reason)
            g.log(f"{stage} CRASHED: {reason}")
            return EXIT_HARD_STOP
        finally:
            if old is not None:
                signal.signal(signal.SIGTERM, old)
        self.write_state(stage, "complete", None)
        self.write_totals(stage, "complete", None)
        notice("GATEWAY" if stage == "draft" else "CLI",
               f"{stage} set={self.set} complete; totals "
               f"{json.dumps(self.control.totals()['since_start'], sort_keys=True)}")
        return EXIT_OK

    def write_totals(self, stage: str, status: str, reason: str | None) -> None:
        rows = (self.drafts() if stage == "draft" else p4.Store(self.p.verify_rows)).rows.values()
        served = Counter(str(r.get("model")) for r in rows if r.get("model"))
        statuses = Counter(str(r.get("status")) for r in rows)
        g.write_json_atomic(self.p.totals(stage), {
            "schema": TOTALS_SCHEMA, "stage": stage, "set": self.set, "status": status, "reason": reason,
            "totals": self.control.totals(), "cumulative_with_prior": dict(self.control.cumulative()),
            "prior_gateway_usage": self.control.prior_resolved,
            "rows": len(rows), "row_status": dict(sorted(statuses.items())),
            "served_models": dict(sorted(served.items())),
            "cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in rows), 4) if stage == "verify" else None,
            "model": self.cfg["drafter" if stage == "draft" else "verifier"]["model"],
            "prereg_sha256": self.prereg, "config_sha256": self.cfg_sha, "pool_sha256": self.pool_sha,
            "provenance": self.prov, "citable": g.citable(self.prov), "updated": g.now_iso()})

    # -------------------------------------------------------- draft
    def draft(self, resume: bool = False, acknowledged: bool = False, allow_low_yield: bool = False,
              accept_low_yield: bool = False) -> int:
        rc = self.begin("draft", resume, acknowledged)
        if rc is not None:
            return rc
        if self.round3:
            rc = self.yield_guards(allow_low_yield, accept_low_yield)
            if rc is not None:
                return rc
        return self.finish("draft", self._draft_body)

    def yield_guards(self, allow_low_yield: bool, accept_low_yield: bool) -> int | None:
        """Round-3 pools: the pool's pre-drafting estimate must be ``ADEQUATE`` unless
        ``--allow-low-yield``; a run the yield watch paused resumes only with
        ``--accept-low-yield``. Both acknowledgements are recorded in the state."""
        prev = self.prev_state or {}
        status = (self.pool_yield or {}).get("status") or "YIELD-NOT-ASSESSED"
        self.low_yield_allowed = prev.get("low_yield_allowed")
        if status != "ADEQUATE" and self.low_yield_allowed is None:
            if not allow_low_yield:
                g.log(f"draft refused: the pool's pre-drafting yield estimate is {status} "
                      f"(expected_yield {(self.pool_yield or {}).get('expected_yield')}, gate "
                      f"{(self.pool_yield or {}).get('gate')}); rebuild the pool, or pass --allow-low-yield")
                return EXIT_HARD_STOP
            self.low_yield_allowed = {"status": status, "expected_yield": (self.pool_yield or {}).get("expected_yield"),
                                      "gate": (self.pool_yield or {}).get("gate"), "t": g.now_iso()}
        yw = self.cfg["yield_watch"]
        w = dict(prev.get("yield_watch") or {"after_jobs": int(yw["after_jobs"]), "min_pass": float(yw["min_pass"]),
                                             "status": "pending"})
        if w.get("status") == "below":
            if not accept_low_yield:
                g.log(f"draft --resume refused: the yield watch paused the run ({w.get('jobs')} jobs done, "
                      f"{w.get('passes')} passed layer 1, rate {w.get('rate'):.3f} < {w.get('min_pass')}); "
                      "resume with --accept-low-yield to continue")
                return EXIT_PAUSED
            w.update({"status": "below_accepted", "accepted": g.now_iso()})
        self.yield_watch = w
        return None

    def l1_tally(self, st: p4.Store) -> tuple[int, int, int]:
        """(jobs done, jobs with a layer-1 pass on some attempt, jobs not done)."""
        done = passes = 0
        n = int(self.cfg["drafter"]["max_attempts_per_chunk"])
        for r in self.sampled:
            if not self.chunk_done(st, r):
                continue
            done += 1
            passes += any((st.get(self.draft_key(r, a)) or {}).get("l1_inline", {}).get("verdict") == "pass"
                          for a in range(1, n + 1))
        return done, passes, len(self.sampled) - done

    def watch_yield(self, st: p4.Store) -> None:
        """The yield watch, once per run: at the first ``after_jobs`` done jobs, a layer-1
        pass rate below ``min_pass`` stops the run (a pause) while jobs remain."""
        w = self.yield_watch
        if w is None or w.get("status") != "pending":
            return
        done, passes, left = self.l1_tally(st)
        if done < int(w["after_jobs"]):
            return
        rate = passes / done
        w.update({"jobs": done, "passes": passes, "rate": rate, "t": g.now_iso()})
        if rate >= float(w["min_pass"]):
            w["status"] = "passed"
        elif not left:
            w["status"] = "below_run_complete"
            notice("GATEWAY", f"draft set={self.label}: yield watch: {passes} of {done} jobs passed layer 1 "
                   f"({rate:.1%} < {float(w['min_pass']):.0%}); no job left, the run completes")
        else:
            w["status"] = "below"
            self.control.stop(f"low_yield: {passes} of {done} jobs passed layer 1 ({rate:.1%} < "
                              f"{float(w['min_pass']):.0%}), {left} jobs left; resume with --accept-low-yield")

    def _draft_body(self) -> None:
        _ = self.corpus  # the integrity checks, before any call
        d = self.cfg["drafter"]
        if not d.get("base_url"):
            raise p4.HardStop("drafter.base_url missing from the private config")
        url = str(d["base_url"]).rstrip("/")
        if url.endswith("/v1"):
            url = url[: -len("/v1")]
        gw = R2Gateway(url, self.control, {**self.cfg["limits"], "empty_retries": 1,
                                           "empty_backoff_s": float(d["empty_retry_s"])}, self.p.calls)
        spec = {"model": d["model"], "tag": d["tag"], "max_tokens": int(d["max_tokens"]),
                "temperature": d.get("temperature")}
        st = self.drafts()

        def job(r: dict[str, Any]) -> Callable[[Any], Any]:
            async def fn(http: Any) -> None:
                prev = None
                for a in range(1, int(d["max_attempts_per_chunk"]) + 1):
                    k = self.draft_key(r, a)
                    row = st.get(k)
                    if row is None:
                        msgs = self.messages_for(r, prev)
                        res = await gw.generate(http, spec, msgs, "draft", k, float(d["empty_retry_s"]))
                        if res is None:
                            return  # stopping, or a failed call: re-issued on the next pass / resume
                        row = self.draft_row(r, a, k, msgs, res)
                        if row["status"] == "empty_twice":
                            self.control.fail("empty")
                        st.put(row)
                    if row["status"] in DROPPED or row["l1_inline"]["verdict"] == "pass" \
                            or set(row["l1_inline"]["reasons"]) & ck.REDRAW_EXEMPT:
                        return
                    prev = row

            async def watched(http: Any) -> None:
                await fn(http)
                self.watch_yield(st)
            return watched

        async def go(todo: list[dict[str, Any]]) -> None:
            http = self.http_factory()
            try:
                await run_jobs([job(r) for r in todo], int(d["inflight"]), self.control, http)
            finally:
                close = getattr(http, "aclose", None)
                if close is not None:
                    await close()

        self.watch_yield(st)                     # a resume past the watch point is judged first
        if self.control.stopped():
            raise p4.Pause(self.control.reason or "stopped")
        todo = [r for r in self.sampled if not self.chunk_done(st, r)]
        if todo:
            notice("GATEWAY", f"draft set={self.set}: starting {len(todo)} chunks "
                   f"(<= {d['inflight']} in flight, model {d['model']})")
        for _ in range(int(d["passes"])):
            if not todo:
                break
            asyncio.run(go(todo))
            if self.control.stopped():
                raise p4.Pause(self.control.reason or "stopped")
            todo = [r for r in self.sampled if not self.chunk_done(st, r)]
        if todo:
            raise p4.Pause(f"calls_failed:{len(todo)}")
        w = self.yield_watch
        if w is not None and w.get("status") == "pending":     # fewer jobs than the watch point
            done, passes, _ = self.l1_tally(st)
            w.update({"status": "not_reached", "jobs": done, "passes": passes,
                      "rate": (passes / done) if done else None, "t": g.now_iso()})

    def draft_row(self, r: dict[str, Any], attempt: int, k: str, msgs: list[dict[str, str]],
                  res: dict[str, Any]) -> dict[str, Any]:
        text = res.get("text") or ""
        if res.get("status") == "empty":
            status = "empty_twice"
        elif res.get("status") == "no_label":
            status = "truncated"
        elif res.get("finish") == "length":
            status = "cut"
        else:
            status = "ok"
        spec = self.specs.get(r["field"]) if r.get("field") is not None else None
        if status != "ok":
            draft, err = None, None
        elif r["stratum"] == "M_num":
            draft, err = ck.parse_draft(text, "fields" if spec else "open", symbol_bound=True)
        else:
            draft, err = ck.parse_draft(text, "fields" if spec else "open")
        if spec is not None and draft is not None and not draft.get("none"):
            draft["binding_terms"] = list(spec.terms)      # the field's terms, never model-invented
        l1 = (ck.layer1(draft, err, stratum=r["stratum"], paper=r["paper"],
                        source_chunk=self.corpus.chunks[r["chunk_id"]], idx=self.idx, cfg=self.checks_cfg,
                        field=spec)
              if status == "ok" else {"verdict": "dropped", "reasons": [status], "flags": []})
        return {"k": k, "record_id": record_id(self.label, r["chunk_id"], attempt, r.get("field")), "set": self.set,
                "field": r.get("field"), "pilot": self.pilot is not None,
                **({"term_in_chunk": r.get("term_in_chunk")} if self.round3 and r.get("field") is not None else {}),
                "chunk_id": r["chunk_id"], "paper": r["paper"], "doc_id": r["doc_id"], "stratum": r["stratum"],
                "attempt": attempt, "status": status, "text": text, "draft": draft, "parse_error": err,
                "model": res.get("model"), "pt": res.get("pt"), "ct": res.get("ct"), "finish": res.get("finish"),
                "http_attempts": res.get("attempts"), "prompt_sha256": g.sha_text(json.dumps(msgs)),
                "l1_inline": l1, "t": g.now_iso()}

    # -------------------------------------------------------- layer 1 (check)
    def acceptance(self) -> p4.Store:
        return p4.Store(self.p.acceptance)

    def log_row(self, log: p4.Store, row: dict[str, Any]) -> bool:
        """Append unless an identical verdict is logged; refuse a different one."""
        old = log.get(row["k"])
        same = ("verdict", "reasons", "flags")
        if old is not None:
            if any(old.get(x) != row.get(x) for x in same):
                raise SystemExit(f"acceptance log: {row['k']} differs from the logged verdict "
                                 "(the log is append-only; a changed check needs a new output directory)")
            return False
        log.put(row)
        return True

    def check(self) -> int:
        self.load()
        st = self.read_state("draft")
        if not st or st.get("status") != "complete":
            g.log("check: the draft stage is not complete (duplicates and shared chunks need every draft)")
            return EXIT_HARD_STOP
        _ = self.corpus
        drafts = self.drafts()
        records: list[dict[str, Any]] = []
        log = self.acceptance()
        added = 0
        for row in sorted(drafts.rows.values(), key=lambda x: (x["paper"], x["record_id"])):
            base = {"record_id": row["record_id"], "set": self.set, "paper": row["paper"],
                    "stratum": row["stratum"], "attempt": row["attempt"], "t": g.now_iso()}
            if row["status"] in DROPPED:
                added += self.log_row(log, {"k": f"draft|{row['record_id']}", "layer": "draft",
                                            "verdict": "dropped", "reasons": [row["status"]], "flags": [], **base})
                continue
            l1 = ck.layer1(row["draft"], row["parse_error"], stratum=row["stratum"], paper=row["paper"],
                           source_chunk=self.corpus.chunks[row["chunk_id"]], idx=self.idx, cfg=self.checks_cfg,
                           field=self.spec_of(row))
            records.append({**row, "l1": l1})
        ck.cross_record(records)
        for r in records:
            l1 = r["l1"]
            added += self.log_row(log, {"k": f"l1|{r['record_id']}", "layer": "l1", "verdict": l1["verdict"],
                                        "reasons": l1["reasons"], "flags": l1["flags"],
                                        "gold_chunk_ids": l1["gold_chunk_ids"], "leak_overlap": l1["leak_overlap"],
                                        "leak_distinctive": l1["leak_distinctive"], "leak_ok": l1["leak_ok"],
                                        "record_id": r["record_id"], "set": self.set, "paper": r["paper"],
                                        "stratum": l1.get("record_stratum", r["stratum"]),
                                        "chunk_stratum": r["stratum"], "field": r.get("field"),
                                        "attempt": r["attempt"], "t": g.now_iso()})
        g.log(f"check set={self.set}: {len(records)} drafts checked, {added} new log rows")
        return EXIT_OK

    def spec_of(self, row: dict[str, Any]) -> fl.FieldSpec | None:
        f = row.get("field")
        if f is None:
            return None
        if f not in self.specs:
            raise SystemExit(f"draft row field {f!r} is not in the field list: refused")
        return self.specs[f]

    # -------------------------------------------------------- layer 2 (verify)
    def l1_passes(self) -> list[dict[str, Any]]:
        log = self.acceptance()
        drafts = self.drafts().rows
        by_rid = {r["record_id"]: r for r in drafts.values()}
        out = []
        for row in log.rows.values():
            if row["layer"] == "l1" and row["verdict"] == "pass":
                out.append({**by_rid[row["record_id"]], "l1": row})
        return sorted(out, key=lambda r: r["record_id"])

    def verify_todo(self) -> list[dict[str, Any]]:
        log = self.acceptance()
        return [r for r in self.l1_passes() if f"l2|{r['record_id']}" not in log]

    def verify_prompt(self, rec: dict[str, Any]) -> str:
        c = self.corpus
        head = c.paper_texts(rec["paper"])[0][: int(self.cfg["verifier"]["head_chars"])]
        return ck.verify_prompt(rec["draft"], c.chunks[rec["chunk_id"]].content, head, c.title(rec["paper"]),
                                flags=rec["l1"].get("flags") or ())

    def cli_cwd(self) -> Path:
        v = self.cfg["verifier"]
        d = Path(v["cwd"]) if v.get("cwd") else self.out / "verifier_cwd"
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        d.chmod(0o700)
        if any(d.iterdir()):
            raise p4.HardStop("verifier_cwd_not_empty")
        for anc in [d, *d.resolve().parents]:
            if (anc / "CLAUDE.md").exists() or (anc / "AGENTS.md").exists():
                raise p4.HardStop("verifier_cwd_has_instructions_above")
        return d

    def claude_cmd(self) -> list[str]:
        v = self.cfg["verifier"]
        return [v["cli"], "-p", "--model", v["model"], "--output-format", "json", "--restricted",
                "--no-session-persistence", "--strict-mcp-config", "--tools", "", "--system-prompt",
                ck.VERIFY_SYSTEM]

    def call_cli(self, prompt: str, cwd: Path) -> dict[str, Any]:
        v = self.cfg["verifier"]
        t0 = time.time()
        p = self.cli_runner(self.claude_cmd(), input=prompt, capture_output=True, text=True, cwd=str(cwd),
                            timeout=int(v["timeout_s"]))
        try:
            obj = json.loads(p.stdout) if (p.stdout or "").strip() else None
        except ValueError:
            obj = None
        obj = obj if isinstance(obj, dict) else None
        failed = p.returncode != 0 or (obj is not None and bool(obj.get("is_error")))
        # a usage-policy refusal first: it is neither a usage limit nor a transport error. The
        # structured ``stop_reason: refusal`` counts on any exit (a refusal can come back as a
        # normal, empty result, which would otherwise be rejected as l2_empty); the wording
        # (REFUSAL_RE) is asked of a failed call only, never of a verifier's answer.
        if (obj is not None and str(obj.get("stop_reason") or "").casefold() == "refusal") or failed:
            if not failed or is_refusal(p.stdout or "", p.stderr or "", obj):
                text = str((obj or {}).get("result") or p.stderr or p.stdout or "")
                return {"status": "refused", "error": text.strip()[:300]}
        if p.returncode != 0:
            err = (p.stderr or p.stdout or "")[-2000:]
            return {"status": "limit" if LIMIT_RE.search(err) else "error", "error": err.strip()[:300]}
        if obj is None:
            return {"status": "error", "error": "stdout is not JSON"}
        text = obj.get("result") or ""
        if obj.get("is_error"):
            lim = LIMIT_RE.search(str(text) + str(obj.get("api_error_status")))
            return {"status": "limit" if lim else "error", "error": str(text)[:300]}
        mu = obj.get("modelUsage") or {}
        model = v["model"]
        own = next(({"key": k, **val} for k, val in mu.items()
                    if k == model or k.startswith(model) or val.get("canonicalModel") == model), {})
        ctx = (int(own.get("inputTokens") or 0) + int(own.get("cacheCreationInputTokens") or 0)
               + int(own.get("cacheReadInputTokens") or 0)) if own else None
        return {"status": "ok" if text.strip() else "empty", "text": text, "model": own.get("key"),
                "context_tokens": ctx, "output_tokens": own.get("outputTokens"),
                "cost_usd": obj.get("total_cost_usd"), "duration_s": round(time.time() - t0, 2)}

    def isolation(self, prompt: str, res: dict[str, Any], overhead: float = CLAUDE_FIXED_OVERHEAD
                  ) -> dict[str, Any]:
        chars = len(prompt) + len(ck.VERIFY_SYSTEM)
        budget = ISOLATION_FACTOR * chars / CHARS_PER_TOKEN_CLI + overhead
        seen = res.get("context_tokens")
        tools = int(res.get("tool_events") or 0)
        return {"chars": chars, "budget": round(budget, 1), "context_tokens": seen, "tool_events": tools,
                "PASS": seen is not None and seen <= budget and not tools}

    # -------------------------------------------------------- the codex fallback
    def codex_cmd(self, cwd: Path, sysfile: Path, schema: Path, lastmsg: Path) -> list[str]:
        """The Codex CLI with every optional instruction source, tool and feature off
        (the invocation the gold-labelling judge proved), read-only, ephemeral."""
        v = self.cfg["verifier"]
        cmd = [str(v["codex_cli"]), "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
               "--skip-git-repo-check", "-C", str(cwd), "-s", "read-only",
               "-c", 'approval_policy="never"', "-c", "project_doc_max_bytes=0", "-c", "mcp_servers={}",
               "-c", 'web_search="disabled"', "-c", "skills.bundled.enabled=false",
               "-c", "skills.include_instructions=false", "-c", "agents.enabled=false",
               "-c", "include_permissions_instructions=false", "-c", "include_environment_context=false",
               "-c", "include_apps_instructions=false", "-c", "include_apply_patch_tool=false",
               "-c", f'model_instructions_file="{sysfile}"']
        for f in CODEX_DISABLED_FEATURES:
            cmd += ["--disable", f]
        cmd += ["-m", str(v["codex_model"]), "-c", f'model_reasoning_effort="{v["codex_effort"]}"',
                "--json", "--color", "never", "--output-schema", str(schema), "-o", str(lastmsg), "-"]
        return cmd

    def call_codex(self, prompt: str, cwd: Path) -> dict[str, Any]:
        """One codex verification: ``status`` ok | empty | limit | error, the last message
        as ``text``, usage from the ``turn.completed`` event, the tool events seen."""
        import tempfile

        v = self.cfg["verifier"]
        base = self.out / "codex_tmp"
        base.mkdir(mode=0o700, exist_ok=True)
        base.chmod(0o700)
        with tempfile.TemporaryDirectory(dir=base) as td_:
            td = Path(td_)
            td.chmod(0o700)
            sysfile, schema, last = td / "system.md", td / "schema.json", td / "last.txt"
            sysfile.write_text(ck.VERIFY_SYSTEM, encoding="utf-8")
            schema.write_text(json.dumps(VERIFY_SCHEMA), encoding="utf-8")
            t0 = time.time()
            try:
                p = self.cli_runner(self.codex_cmd(cwd, sysfile, schema, last), input=prompt, capture_output=True,
                                    text=True, cwd=str(cwd), timeout=int(v["timeout_s"]))
            except subprocess.TimeoutExpired:
                return {"status": "timeout", "error": "timeout"}
            events = []
            for line in (p.stdout or "").splitlines():
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if isinstance(e, dict):
                    events.append(e)
            errs = [e for e in events if e.get("type") in ("error", "turn.failed")]
            errtext = json.dumps(errs)[:1500] + (p.stderr or "")[-1500:]
            if p.returncode != 0 or errs:
                st = ("refused" if CODEX_REFUSAL_RE.search(errtext) else
                      "limit" if LIMIT_RE.search(errtext) else "error")
                return {"status": st, "error": errtext.strip()[:300]}
            text = last.read_text(encoding="utf-8") if last.exists() else ""
        usage: dict[str, Any] = {}
        for e in events:
            if e.get("type") == "turn.completed":
                usage = e.get("usage") or {}
        items = [e.get("item") or {} for e in events if str(e.get("type", "")).startswith("item.")]
        tools = sum(1 for i in items if (i.get("type") or i.get("item_type")) not in
                    ("agent_message", "reasoning", "error", None))
        served = next((e[k] for e in events for k in ("model", "model_slug") if isinstance(e.get(k), str)), None)
        return {"status": "ok" if text.strip() else "empty", "text": text, "model": served or v["codex_model"],
                "context_tokens": int(usage.get("input_tokens") or 0) if usage else None,
                "output_tokens": usage.get("output_tokens"), "cost_usd": None, "tool_events": tools,
                "duration_s": round(time.time() - t0, 2)}

    def verify(self, resume: bool = False, acknowledged: bool = False) -> int:
        rc = self.begin("verify", resume, acknowledged)
        if rc is not None:
            return rc
        return self.finish("verify", self._verify_body)

    def _verify_body(self) -> None:
        v = self.cfg["verifier"]
        cwd = self.cli_cwd()
        rows = p4.Store(self.p.verify_rows)
        log = self.acceptance()
        todo = self.verify_todo()
        checked = {"done": not bool(v["isolation_check"])}
        codex_checked = {"done": not bool(v["isolation_check"])}
        hard: list[str] = []

        def one(rec: dict[str, Any]) -> str:
            """Verify one record; returns ``refused`` when Claude refused it (whatever codex did)."""
            rid = rec["record_id"]
            prompt = self.verify_prompt(rec)
            last: dict[str, Any] = {}
            refusal: str | None = None
            for attempt in (1, 2):
                with self._lock:
                    if self.control.stopped() or hard:
                        return "stopped"
                    if self.control.seg["cli_calls"] + self.control.prev["cli_calls"] >= int(v["max_calls"]):
                        self.control.stop("cli_call_cap")
                        return "stopped"
                    spent = sum(float(x.get("cost_usd") or 0) for x in rows.rows.values())
                    if spent >= float(v["max_cost_usd"]):
                        self.control.stop("cli_cost_cap")
                        return "stopped"
                    self.control.account("cli")
                try:
                    res = self.call_cli(prompt, cwd)
                except subprocess.TimeoutExpired:
                    res = {"status": "error", "error": "timeout"}
                if res["status"] == "refused":
                    refusal = str(res.get("error") or "")
                    with self._lock:
                        self.control.seg["cli_refusals"] += 1
                    if not v["codex_fallback"]:
                        return "refused"   # not recorded: the operator decides (no fallback configured)
                    last = self.codex_verify(prompt, cwd, codex_checked, hard)
                    if not last:
                        return "refused"   # stopping before the codex call: re-issued on resume
                    break
                with self._lock:
                    if res["status"] == "limit":
                        self.control.stop("usage_limit")
                        return "stopped"
                    if res["status"] == "error":
                        self.control.fail("cli_error")
                        if attempt == 2:
                            return "error"  # not recorded: re-issued on resume
                        continue
                    self.control.account("cli", int(res.get("context_tokens") or 0),
                                         int(res.get("output_tokens") or 0), call=False)
                    if not checked["done"]:
                        iso = self.isolation(prompt, res)
                        checked["done"] = True
                        if not iso["PASS"]:
                            hard.append("isolation_failed")
                            self.control.stop("isolation_failed")
                            return "stopped"
                    verdict, err = ck.parse_verdict(res.get("text") or "") if res["status"] == "ok" else (None, None)
                    last = {**res, "parse_error": err, "verdict": verdict, "attempt": attempt, "verifier": "claude"}
                    if verdict is not None:
                        self.control.ok()
                        break
            with self._lock:
                if not last:
                    return "error"
                verdict = last.get("verdict")
                if last.get("unavailable"):
                    reasons, l2_verdict = ["verifier_unavailable"], "unavailable"
                elif verdict is None:
                    reasons = ["l2_empty" if last["status"] == "empty" else "l2_unparseable"]
                    l2_verdict = "reject"
                else:
                    reasons = [f"l2_{k}" for k in ck.VERIFY_KEYS if verdict[k] is False]
                    l2_verdict = "reject" if reasons else "pass"
                rows.put({"k": f"verify|{rid}", "record_id": rid, "status": last["status"],
                          "text": last.get("text"), "model": last.get("model"),
                          "context_tokens": last.get("context_tokens"), "output_tokens": last.get("output_tokens"),
                          "cost_usd": last.get("cost_usd"), "attempts": last["attempt"],
                          "verifier": last["verifier"], "verifier_refused": "claude" if refusal is not None else None,
                          "refusal_text": refusal, "error": last.get("error"),
                          "unavailable_reason": last.get("unavailable_reason"),
                          "prompt_sha256": g.sha_text(prompt), "t": g.now_iso()})
                self.log_row(log, {"k": f"l2|{rid}", "layer": "l2", "verdict": l2_verdict,
                                   "reasons": reasons, "flags": [], "fields": verdict, "served_model": last.get("model"),
                                   "verifier": last["verifier"],
                                   "verifier_refused": "claude" if refusal is not None else None,
                                   "unavailable_reason": last.get("unavailable_reason"),
                                   "record_id": rid, "set": self.set, "paper": rec["paper"],
                                   "stratum": rec["l1"]["stratum"], "attempt": rec["attempt"], "t": g.now_iso()})
            return "refused" if refusal is not None else "done"

        if todo:
            notice("CLI", f"verify set={self.set}: starting {len(todo)} records (<= {v['inflight']} in flight)")
        # the isolation check runs alone, before anything else is in flight; a record Claude
        # refuses (verified by codex instead) does not check Claude, so the next one probes
        while todo and not checked["done"]:
            outcome = one(todo[0])
            todo = todo[1:]
            if hard or self.control.stopped():
                break
            if not checked["done"] and outcome != "refused":
                raise p4.Pause(self.control.reason or "isolation_probe_failed")
        with ThreadPoolExecutor(max_workers=int(v["inflight"])) as ex:
            list(ex.map(one, todo))
        if hard:
            raise p4.HardStop(hard[0])
        if self.control.stopped():
            raise p4.Pause(self.control.reason or "stopped")
        left = self.verify_todo()
        if left:
            raise p4.Pause(f"calls_failed:{len(left)}")

    def codex_verify(self, prompt: str, cwd: Path, checked: dict[str, bool], hard: list[str]) -> dict[str, Any]:
        """The codex fallback for one refused record, one codex call at a time. Returns the
        ``last`` row for the record, or ``{}`` when the record stays PENDING (re-issued on
        resume): the run is stopping, or codex hit a limit, a timeout or a transport error —
        those stop the run (a pause, exit 75), never exclude the record. Only a definitive
        codex failure makes the record ``unavailable``: codex refuses it too, or its answer
        is empty or malformed twice."""
        with self._codex_lock:
            for attempt in (1, 2):
                with self._lock:
                    if self.control.stopped() or hard:
                        return {}
                    self.control.account("codex")
                try:
                    res = self.call_codex(prompt, cwd)
                except Exception as e:  # noqa: BLE001 - a transport failure: pause, the record stays pending
                    res = {"status": "error", "error": f"{type(e).__name__}: {str(e)[:200]}"}
                if any(cwd.iterdir()):
                    with self._lock:
                        hard.append("verifier_cwd_written_by_codex")
                        self.control.stop("verifier_cwd_written_by_codex")
                    return {}
                with self._lock:
                    base = {**res, "attempt": attempt, "verifier": "codex"}
                    if res["status"] in ("limit", "timeout", "error"):
                        self.control.seg[f"codex_{res['status']}"] += 1
                        self.control.stop(f"codex_{res['status']}")
                        return {}
                    if res["status"] == "refused":
                        self.control.seg["codex_refusals"] += 1
                        return {**base, "verdict": None, "parse_error": None, "unavailable": True,
                                "unavailable_reason": "codex_refused"}
                    self.control.account("codex", int(res.get("context_tokens") or 0),
                                         int(res.get("output_tokens") or 0), call=False)
                    if not checked["done"]:
                        iso = self.isolation(prompt, res, float(self.cfg["verifier"]["codex_overhead_tokens"]))
                        checked["done"] = True
                        if not iso["PASS"]:
                            hard.append("codex_isolation_failed")
                            self.control.stop("codex_isolation_failed")
                            return {}
                    verdict, err = ck.parse_verdict(res.get("text") or "") if res["status"] == "ok" \
                        else (None, "empty")
                    if verdict is not None:
                        return {**base, "verdict": verdict, "parse_error": None}
                    self.control.seg["codex_malformed"] += 1
                    if attempt == 2:
                        return {**base, "verdict": None, "parse_error": err, "unavailable": True,
                                "unavailable_reason": "codex_malformed_twice"}
        return {}

    # -------------------------------------------------------- summary
    def accepted(self) -> list[dict[str, Any]]:
        log = self.acceptance()
        out = []
        for r in self.l1_passes():
            l2 = log.get(f"l2|{r['record_id']}")
            if l2 is not None and l2["verdict"] == "pass":
                out.append({**r, "l2": l2})
        return out

    def accepted_row(self, r: dict[str, Any]) -> dict[str, Any]:
        """One record as the wording proxy and the partner export read it. ``stratum`` is
        the record's (``S3`` = words-only, identifiable per record); ``chunk_stratum`` the pool's."""
        return {"record_id": r["record_id"], "set": self.set, "paper": r["paper"], "doc_id": r["doc_id"],
                "source_chunk_id": r["chunk_id"], **r["draft"], "stratum": r["l1"]["stratum"],
                "chunk_stratum": r["stratum"], "words_only": r["l1"]["stratum"] == "S3",
                "field": r.get("field"), "pilot": bool(r.get("pilot")), "model": r.get("model"),
                "prompt_sha256": r.get("prompt_sha256"),
                # layer 2's verifier (rows logged before the field existed: claude); None for a candidate
                "verifier": (r["l2"].get("verifier") or "claude") if r.get("l2") else None,
                "verifier_refused": (r.get("l2") or {}).get("verifier_refused"),
                "verifier_model": (r.get("l2") or {}).get("served_model"),
                "gold_chunks": r["l1"]["gold_chunk_ids"], "flags": r["l1"]["flags"]}

    def summary(self) -> int:
        self.load()
        log = self.acceptance()
        if any(log.get(f"l2|{r['record_id']}") is None for r in self.l1_passes()):
            g.log("summary: layer 2 is not complete; run verify first")
            return EXIT_HARD_STOP
        acc = self.accepted()
        with (self.p.accepted.with_suffix(".jsonl.tmp")).open("w", encoding="utf-8") as fh:
            for r in acc:
                fh.write(json.dumps(self.accepted_row(r), sort_keys=True, ensure_ascii=False) + "\n")
        self.p.accepted.with_suffix(".jsonl.tmp").replace(self.p.accepted)
        rows = list(log.rows.values())
        by_code: dict[str, Counter[str]] = {}
        for row in rows:
            if row["verdict"] in ("reject", "dropped"):
                for code in row["reasons"]:
                    by_code.setdefault(code, Counter())[row["stratum"]] += 1
        flags: dict[str, Counter[str]] = {}
        for row in rows:
            if row["layer"] == "l1" and row["verdict"] == "pass":
                for f in row["flags"]:
                    flags.setdefault(f, Counter())[row["stratum"]] += 1
        overlaps = sorted(float(r["leak_overlap"]) for r in rows if r["layer"] == "l1"
                          and r.get("leak_overlap") is not None)
        sampled = Counter(r["stratum"] for r in self.sampled)
        draft_rows = list(self.drafts().rows.values())
        attempts = Counter(r["stratum"] for r in draft_rows)            # every attempt, dropped ones included
        attempt_no = Counter(f"a{r['attempt']}" for r in draft_rows)
        http_calls = sum(int(r.get("http_attempts") or 1) for r in draft_rows)
        accepted = Counter(r["l1"]["stratum"] for r in acc)
        prov = self.provenance()

        def q(p: float) -> float | None:
            return overlaps[min(len(overlaps) - 1, int(p * len(overlaps)))] if overlaps else None

        summary = {
            "schema": SUMMARY_SCHEMA, "set": self.set, "status": "our three-layer check (layers 1-2)",
            "counts": {"sampled_chunks": dict(sampled), "draft_attempts": dict(attempts),
                       "accepted": dict(accepted), "accepted_total": len(acc),
                       "papers_with_accepted": len({r["paper"] for r in acc}),
                       "yield_per_sampled_chunk": {s: (accepted[s] / sampled[s]) if sampled[s] else None
                                                   for s in sorted(sampled)},
                       "attempts_per_accepted": (sum(attempts.values()) / len(acc)) if acc else None,
                       "attempts_per_accepted_by_stratum": {s: (attempts[s] / accepted[s]) if accepted[s] else None
                                                            for s in sorted(attempts)},
                       "attempts_by_number": dict(sorted(attempt_no.items())),
                       "gateway_calls_per_accepted": (http_calls / len(acc)) if acc else None},
            "rejects_by_code": {k: dict(v) for k, v in sorted(by_code.items())},
            "layer2_by_verifier": {k: dict(v) for k, v in sorted(_l2_by_verifier(rows).items())},
            # excluded, not rejected: no verifier could judge the record (never a record defect)
            "excluded": {"verifier_unavailable": sum(1 for r in rows if r["layer"] == "l2"
                                                     and r["verdict"] == "unavailable")},
            "verifier_refused": dict(Counter(str(r["verifier_refused"]) for r in rows
                                             if r["layer"] == "l2" and r.get("verifier_refused"))),
            "flags_on_l1_passes": {k: dict(v) for k, v in sorted(flags.items())},
            "lexical_overlap": {"threshold": self.cfg["checks"]["leak_threshold"], "n": len(overlaps),
                                "quantiles": {f"p{int(p * 100)}": q(p) for p in (0.1, 0.25, 0.5, 0.75, 0.9)},
                                "over_threshold": sum(1 for x in overlaps if x > self.cfg["checks"]["leak_threshold"])},
            "draft_mode": self.mode, "pilot": self.pilot is not None,
            **({"round3": {"symbol_bound_by_stratum": {
                s: dict(Counter(str(r["draft"].get(ck.SYMBOL_BOUND_KEY)) for r in acc if r["l1"]["stratum"] == s))
                for s in ck.R3_STRATA}}} if self.round3 else {}),
            "fields": None if self.mode != "fields" else {
                "fields_sha256": self.fields_sha,
                "jobs_by_field": dict(sorted(Counter(j["field"] for j in self.sampled).items())),
                "term_in_chunk": dict(Counter(str(j.get("term_in_chunk")) for j in self.sampled)),
                "accepted_by_field": dict(sorted(Counter(r["field"] for r in acc).items())),
                "words_only_accepted": sum(1 for r in acc if r["l1"]["stratum"] == "S3"),
                "pilot_plan": self.pilot_plan},
            "pool_sha256": self.pool_sha, "accepted_sha256": g.file_sha(self.p.accepted),
            "config_sha256": self.cfg_sha, "provenance": prov, "citable": g.citable(prov),
        }
        g.write_json_atomic(self.p.summary, summary)
        g.log(f"summary set={self.set}: {len(acc)} accepted")
        return EXIT_OK

    # -------------------------------------------------------- spot check (layer 3)
    def spot_export(self, path: Path) -> int:
        g.refuse_in_repo(path, "the spot-check sheet")
        self.load()
        if not self.p.accepted.is_file():
            g.log("spot-export: no accepted file; run summary first")
            return EXIT_HARD_STOP
        acc = {json.loads(x)["record_id"]: json.loads(x)
               for x in self.p.accepted.read_text(encoding="utf-8").splitlines() if x.strip()}
        sp = self.cfg["spot"]
        seed = f"{sp['seed_base']}|{self.set}"
        pick = random.Random(seed).sample(sorted(acc), min(int(sp["n"]), len(acc)))
        corpus = self.corpus
        items, key = [], {}
        for n, rid in enumerate(pick):
            r = acc[rid]
            iid = f"s{n:03d}"
            items.append({"item": iid, "question": r["question"], "quantity_asked": r["quantity"],
                          "answer": r["value"], "quote": r["quote"],
                          "passage": corpus.chunks[r["source_chunk_id"]].content, "label": None})
            key[iid] = rid
        export_id = "spot-" + g.sha_text(json.dumps(items, sort_keys=True, ensure_ascii=False))[:12]
        g.write_json_atomic(path, {"schema": SPOT_SCHEMA, "export_id": export_id, "labels": list(SPOT_LABELS),
                                   "items": items,
                                   "note": "PRIVATE. Mark each item valid or invalid; the key is a separate file."})
        g.write_json_atomic(path.with_name(path.stem + ".key.json"),
                            {"key": key, "seed": seed, "set": self.set, "export_id": export_id,
                             "note": "PRIVATE blinding key: never shown to the rater."})
        g.log(f"spot-export set={self.set}: {len(items)} records")
        return EXIT_OK

    def spot_import(self, labels_path: Path, key_path: Path) -> int:
        lab = json.loads(labels_path.read_text(encoding="utf-8"))
        key = json.loads(key_path.read_text(encoding="utf-8"))
        if key.get("set") != self.set:
            raise SystemExit("spot-import: the key is for another set")
        if lab.get("export_id") != key.get("export_id"):
            raise SystemExit("spot-import: the labels' export_id does not match the key (another sheet)")
        items = ({str(k): v for k, v in lab["grades"].items()} if "grades" in lab
                 else {it["item"]: it.get("label") for it in lab["items"]})
        unknown = set(items) - set(key["key"])
        if unknown:
            raise SystemExit(f"spot-import: {len(unknown)} item ids not in the key")
        bad = [i for i, v in items.items() if v not in (*SPOT_LABELS, None, "")]
        if bad:
            raise SystemExit(f"spot-import: labels must be valid/invalid ({len(bad)} others)")
        graded = {i: v for i, v in items.items() if v in SPOT_LABELS}
        k = sum(1 for v in graded.values() if v == "valid")
        n = len(graded)
        lo, hi = wilson(k, n)
        thr = float(self.cfg["spot"]["threshold"])
        prov = self.provenance()
        res = {"schema": SPOT_SCHEMA, "set": self.set, "n_items": len(key["key"]), "n_graded": n,
               "n_valid": k, "validity": (k / n) if n else None, "wilson95": [lo, hi], "threshold": thr,
               "below_threshold": (k / n < thr) if n else None,
               "rule": "validity below threshold -> the set's results carry 'validity below threshold'; "
                       "the set is not rebuilt",
               "labels_sha256": g.file_sha(labels_path), "provenance": prov, "citable": g.citable(prov)}
        g.write_json_atomic(self.p.spot_results, res)
        g.log(f"spot-import set={self.set}: {k}/{n} valid")
        return EXIT_OK


    # -------------------------------------------------------- the papers-OC owner's format
    def own_paths(self) -> set[Path]:
        """Every file this run writes (the set's and the pilot's), with the ``.tmp`` its
        atomic write goes through: a partner file given as one of them would be overwritten
        (the set-P pilot's export path once equalled the export manifest's)."""
        out: set[Path] = set()
        for label in (self.set, f"{self.set}-pilot"):
            p = Paths(self.out, label)
            names = ["pool", "drafts", "calls", "verify_rows", "acceptance", "accepted", "summary",
                     "spot_results", "pause", "partner_export", "partner_verdicts"]
            files = [getattr(p, n) for n in names] + [f(st) for st in STAGES
                                                      for f in (p.state, p.preflight, p.totals)]
            out |= {x.resolve() for f in files for x in (f, f.with_suffix(f.suffix + ".tmp"))}
        return out

    def refuse_own_path(self, path: Path, what: str) -> None:
        cand = {path.resolve(), path.with_suffix(path.suffix + ".tmp").resolve()}
        if cand & self.own_paths():
            raise SystemExit(f"{what} {path} is (or would overwrite) a file this run writes: refused")

    def export_partner(self, path: Path, candidates: bool = False) -> int:
        """Accepted records (default) or the layer-1 passes (``candidates``) as the
        papers-OC owner's JSONL: ``id, question, source{doc_id, locator}, quote, field,
        case, model, prompt_sha256`` (+ ``value`` as printed, + ``pilot: true`` on a
        pilot's records, + the verifier provenance :func:`verifier_provenance`). The
        output may not be a file the run writes itself (:meth:`own_paths`). A record whose PMC id / DOI cannot be resolved from its
        document's chunk metadata or doc_id is refused and counted."""
        g.refuse_in_repo(path, "the partner export")
        self.refuse_own_path(path, "export-partner: the output")
        self.load()
        if self.mode != "fields":
            raise SystemExit("export-partner: the partner format names a field; the set is not in draft_mode fields")
        if candidates:
            recs = [self.accepted_row({**r, "l1": r["l1"]}) for r in self.l1_passes()]
        else:
            if not self.p.accepted.is_file():
                g.log("export-partner: no accepted file; run summary first (or pass --candidates)")
                return EXIT_HARD_STOP
            recs = [json.loads(x) for x in self.p.accepted.read_text(encoding="utf-8").splitlines() if x.strip()]
        corpus = self.corpus
        refused: Counter[str] = Counter()
        rows = []
        for r in recs:
            if bool(r.get("pilot")) != (self.pilot is not None):
                raise SystemExit("export-partner: a pilot record in a hypothesis export (or the reverse)")
            if r.get("field") not in self.specs:
                refused["field_not_in_list"] += 1
                continue
            loc = fl.locator(r["doc_id"], [c.metadata for c in corpus.by_doc.get(r["doc_id"], [])])
            if loc is None:
                refused["unresolved_locator"] += 1
                continue
            row = {"id": r["record_id"], "question": r["question"],
                   "source": {"doc_id": r["doc_id"], "locator": loc}, "quote": r["quote"], "field": r["field"],
                   "case": r.get("case") or "", "model": r.get("model"), "prompt_sha256": r.get("prompt_sha256"),
                   "value": r["value"], **verifier_provenance(r)}
            if self.pilot is not None:
                row["pilot"] = True
            rows.append(row)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
        tmp.replace(path)
        prov = self.provenance()
        g.write_json_atomic(self.p.partner_export, {
            "schema": PARTNER_SCHEMA, "set": self.set, "pilot": self.pilot is not None,
            "source": "candidates (layer-1 passes)" if candidates else "accepted (layers 1-2)",
            "records": len(recs), "exported": len(rows), "refused": dict(refused),
            "ids": [r["id"] for r in rows], "export_sha256": g.file_sha(path), "fields_sha256": self.fields_sha,
            "provenance": prov, "created": g.now_iso()})
        g.log(f"export-partner set={self.label}: {len(rows)} exported, refused {dict(refused)}")
        return EXIT_OK

    def import_partner_verdicts(self, path: Path) -> int:
        """The papers-OC owner's verdicts: ``{"records": [{"id", "verdict": accept|reject,
        "reason", "basis": scripted|sampled}], "sample": {"n", "errors", "seed"}}``.
        Every id must be in our last export; a reject needs a reason; when sampled rows
        are given they must agree with the summary (n rows, ``errors`` rejects). The
        error rate is errors/n with a Wilson 95% interval, and the set's status label
        is ``scripted-clean, judged sample of N, error rate X% (95% CI a–b)`` (a, b in
        percent)."""
        self.refuse_own_path(path, "import-partner-verdicts: the verdicts file")
        if not self.p.partner_export.is_file():
            raise SystemExit("import-partner-verdicts: no partner export on record; run export-partner first")
        exp = json.loads(self.p.partner_export.read_text(encoding="utf-8"))
        data = json.loads(path.read_text(encoding="utf-8"))
        recs = data.get("records") or []
        sample = data.get("sample") or {}
        try:
            n, errors = int(sample["n"]), int(sample["errors"])
        except (KeyError, TypeError, ValueError):
            raise SystemExit("import-partner-verdicts: sample {n, errors, seed} is required") from None
        if "seed" not in sample:
            raise SystemExit("import-partner-verdicts: the sample summary needs its seed")
        if n < 1 or not 0 <= errors <= n:
            raise SystemExit("import-partner-verdicts: need n >= 1 and 0 <= errors <= n")
        known = set(exp["ids"])
        seen: set[str] = set()
        for r in recs:
            rid = r.get("id")
            if rid not in known:
                raise SystemExit(f"import-partner-verdicts: id {rid!r} is not in our export")
            if rid in seen:
                raise SystemExit(f"import-partner-verdicts: id {rid!r} twice")
            seen.add(rid)
            if r.get("verdict") not in PARTNER_VERDICTS or r.get("basis") not in PARTNER_BASES:
                raise SystemExit(f"import-partner-verdicts: {rid}: verdict in {PARTNER_VERDICTS}, "
                                 f"basis in {PARTNER_BASES}")
            if r["verdict"] == "reject" and not str(r.get("reason") or "").strip():
                raise SystemExit(f"import-partner-verdicts: {rid}: a reject needs a reason")
        sampled = [r for r in recs if r["basis"] == "sampled"]
        if sampled and (len(sampled) != n or sum(r["verdict"] == "reject" for r in sampled) != errors):
            raise SystemExit("import-partner-verdicts: the sampled rows disagree with the sample summary "
                             f"({len(sampled)} rows, {sum(r['verdict'] == 'reject' for r in sampled)} rejects; "
                             f"summary n={n}, errors={errors})")
        lo, hi = wilson(errors, n)
        label = fl.status_label(n, errors, wilson)
        scripted = [r for r in recs if r["basis"] == "scripted" and r["verdict"] == "reject"]
        prov = self.provenance()
        promotion = promotion_condition(n, errors) if self.pilot is not None else None
        g.write_json_atomic(self.p.partner_verdicts, {
            "schema": PARTNER_VERDICTS_SCHEMA, "set": self.set, "pilot": self.pilot is not None,
            "status_label": label, "error_rate": errors / n, "wilson95": [lo, hi],
            "promotion": promotion,
            "sample": {"n": n, "errors": errors, "seed": sample["seed"]},
            "counts": {"exported": len(known), "verdicts": len(recs), "scripted_rejects": len(scripted),
                       "sampled": len(sampled), "sampled_rejects": sum(r["verdict"] == "reject" for r in sampled)},
            "rejects": {r["id"]: {"basis": r["basis"], "reason": r.get("reason")} for r in recs
                        if r["verdict"] == "reject"},
            "verdicts_sha256": g.file_sha(path), "export_sha256": exp.get("export_sha256"),
            "provenance": prov, "created": g.now_iso()})
        print(label, flush=True)
        if promotion is not None:
            print(f"promotion condition (prereg §3.5): n={n} errors={errors} "
                  f"wilson95_upper={promotion['upper_bound_6dp']} -> {'met' if promotion['met'] else 'not met'}",
                  flush=True)
        return EXIT_OK


def verifier_provenance(r: dict[str, Any]) -> dict[str, Any]:
    """The export's verifier provenance: ``verifier`` (claude | codex; None for a layer-1
    candidate), ``verifier_refused`` (the verifiers that refused the record, possibly
    empty) and ``verifier_model`` (the served model, when the l2 row recorded one)."""
    ref = r.get("verifier_refused")
    out: dict[str, Any] = {"verifier": r.get("verifier"),
                           "verifier_refused": [] if not ref else ([ref] if isinstance(ref, str) else list(ref))}
    if r.get("verifier_model"):
        out["verifier_model"] = r["verifier_model"]
    return out


def _l2_by_verifier(rows: list[dict[str, Any]]) -> dict[str, Counter[str]]:
    """Layer-2 verdicts per verifier (``claude`` for rows logged before the field existed)."""
    out: dict[str, Counter[str]] = {}
    for r in rows:
        if r["layer"] == "l2":
            out.setdefault(str(r.get("verifier") or "claude"), Counter())[r["verdict"]] += 1
    return out


def wilson(k: int, n: int, z: float = WILSON_Z) -> tuple[float | None, float | None]:
    if n == 0:
        return None, None
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def promotion_condition(n: int, errors: int) -> dict[str, Any]:
    """Prereg §3.5 on the pilot's imported verdicts, mechanically: the Wilson 95% upper
    bound of ``errors / n`` (``WILSON_Z``, no continuity correction, no rounding) is
    ``<= PROMOTION_THRESHOLD`` and ``n >= PROMOTION_MIN_JUDGED``. The bound is written at
    full precision and to 6 decimals; the comparison is at full precision."""
    _, hi = wilson(errors, n)
    assert hi is not None
    return {"rule": "Wilson 95% upper bound of the judged error rate <= threshold, at least min_judged judged; "
                    "from the raw counts, compared at full float precision",
            "z": WILSON_Z, "threshold": PROMOTION_THRESHOLD, "min_judged": PROMOTION_MIN_JUDGED,
            "n": n, "errors": errors, "upper_bound": hi, "upper_bound_6dp": f"{hi:.6f}",
            "met": n >= PROMOTION_MIN_JUDGED and hi <= PROMOTION_THRESHOLD}


# ------------------------------------------------------------------ CLI

def main(argv: list[str] | None = None, provenance: Callable[[], dict[str, Any]] | None = None,
         http_factory: Callable[[], Any] | None = None, cli_runner: Callable[..., Any] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--private-config", default=None, help=f"private JSON config (or env {g.CONFIG_ENV})")
    ap.add_argument("--out-dir", default=None, help="default: the config's out_dir")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def with_set(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument("--set", required=True, choices=g.SETS)
        p.add_argument("--pilot", type=int, nargs="?", const=PILOT_DEFAULT_RECORDS, default=None, metavar="N",
                       help=f"the pilot batch (default {PILOT_DEFAULT_RECORDS} jobs): its own files, "
                            "records marked pilot: true, never in the hypothesis sets")
        p.add_argument("--papers", type=int, default=PILOT_DEFAULT_PAPERS, metavar="K",
                       help=f"with --pilot: how many eval papers (default {PILOT_DEFAULT_PAPERS})")
        return p

    pf = with_set(sub.add_parser("preflight", help="print and record the size notice"))
    pf.add_argument("--stage", required=True, choices=STAGES)
    for name in STAGES:
        sp = with_set(sub.add_parser(name, help=f"the {name} stage (pause/resume; exit 75 = paused)"))
        sp.add_argument("--notice-acknowledged", action="store_true",
                        help="the human has sent the preflight notice; required")
        sp.add_argument("--resume", action="store_true")
        if name == "draft":
            sp.add_argument("--allow-low-yield", action="store_true",
                            help="round-3 pools: start although the pool's yield estimate is not ADEQUATE (recorded)")
            sp.add_argument("--accept-low-yield", action="store_true",
                            help="round-3 pools: resume a run the yield watch paused (recorded)")
    with_set(sub.add_parser("check", help="layer 1 over every draft (no model)"))
    with_set(sub.add_parser("summary", help="accepted records + counts-only summary"))
    se = with_set(sub.add_parser("spot-export", help="the owner's blind spot-check sheet (PRIVATE)"))
    se.add_argument("out")
    si = with_set(sub.add_parser("spot-import", help="the owner's labels -> validity with a Wilson CI"))
    si.add_argument("labels")
    si.add_argument("--key", required=True)
    ep = with_set(sub.add_parser("export-partner", help="records in the papers-OC owner's JSONL (PRIVATE)"))
    ep.add_argument("out")
    ep.add_argument("--candidates", action="store_true", help="the layer-1 passes instead of the accepted records")
    iv = with_set(sub.add_parser("import-partner-verdicts", help="the owner's verdicts -> error rate + status label"))
    iv.add_argument("verdicts")
    args = ap.parse_args(argv)
    cfg, cfg_sha = g.load_config(args.private_config)
    out = g.out_dir(cfg, args.out_dir)
    run = Run(cfg, cfg_sha, out, args.set, provenance, http_factory, cli_runner,
              pilot=None if args.pilot is None else (args.pilot, args.papers))
    if args.cmd == "preflight":
        run.preflight(args.stage)
        return EXIT_OK
    if args.cmd == "draft":
        return run.draft(args.resume, args.notice_acknowledged, args.allow_low_yield, args.accept_low_yield)
    if args.cmd == "verify":
        return run.verify(args.resume, args.notice_acknowledged)
    if args.cmd == "check":
        return run.check()
    if args.cmd == "summary":
        return run.summary()
    if args.cmd == "spot-export":
        return run.spot_export(Path(args.out))
    if args.cmd == "export-partner":
        return run.export_partner(Path(args.out), args.candidates)
    if args.cmd == "import-partner-verdicts":
        return run.import_partner_verdicts(Path(args.verdicts))
    return run.spot_import(Path(args.labels), Path(args.key))


if __name__ == "__main__":
    sys.exit(main())
