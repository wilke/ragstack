#!/usr/bin/env python
"""Alias benchmark: drafting, layers 1-2 and the summary for the G (glossary) and C
(control) jobs of :mod:`glossbench_alias_pool`, on the round-3 drafting harness.

Subcommands (``--private-config CFG`` first; every output goes to the config's private
``out_dir``, file label ``alias-<set>``)::

    preflight --stage draft|verify --set S   print the size notice; record it
    draft  --set S --notice-acknowledged [--resume] [--allow-low-yield] [--accept-low-yield]
    check  --set S                           layer 1 over every draft (no model)
    verify --set S --notice-acknowledged [--resume]    layer 2 (Claude CLI + Codex fallback)
    summary --set S                          accepted records + counts-only summary
    preflight --stage waves --set S          the run-waves size notice (a seeded forecast)
    run-waves --set S --notice-acknowledged [--resume [--clear-hard-stop R --clear-note T]]
              [--allow-low-yield] [--accept-low-yield]
                                             draft -> check -> verify in waves keyed on ACCEPTED records

Every subcommand takes ``--pilot [N] [--papers K]`` (default 24 jobs over 3 papers): K
papers drawn by ``random.Random("<pool seed>|pilot")`` from the pool's eval papers and N
PLANNED jobs spread over them with a per-paper cap (``N // K`` each, the first ``N % K``
papers in sorted order one more), each paper's jobs taken in its drafting order (wave,
kind, rank), so e.g. ``--pilot 50 --papers 12`` plans 4-5 jobs on each of 12 papers. The
stop rule still applies inside the pilot, so fewer than N are normally drafted. Own files
(``*-alias-<set>-pilot*``), records marked ``pilot: true``.

**Size notice** (``preflight --stage draft``). ``calls`` is the EXPECTED number of gateway
calls under the stop rule: per paper and kind, :func:`glossbench_alias_pool.expected_drafted`
over the remaining waves with the per-job layer-1 pass rate ``alias.l1_pass_rate`` {G, C}
(default: the pool's yield rate, the calibration or the prior; that is a layers-1-2 rate,
so the default over-estimates the jobs drafted), times ``drafter.attempts_factor``; the
token estimates scale with it. Beside it, labelled: ``calls_if_no_stop_rule`` (every planned
job, times the factor) and ``calls_upper_bound`` (every planned job, every redraw and empty
retry used, plus ``max_fail_total`` re-issued failed calls). ``limits.notice_overrun``
applies to ``calls``, i.e. pauses at the expected calls plus that share. At the end of every
draft segment the realised calls and tokens are printed beside the notice's estimate, and
``summary`` records them (``alias.realised``: calls, tokens, calls per drafted job, the
layer-1 pass rate per kind, the inputs for ``alias_pool.yield_calibration`` and
``alias.l1_pass_rate``).

**Everything the round-3 drafter guarantees applies unchanged** (it is the same code:
:class:`AliasRun` subclasses :class:`glossbench_r2_draft.Run`): ``fallback_model: ""``
and ``max_tokens`` asserted in every body; at most 4 in flight; an empty answer retried
once, then dropped (``empty_twice``, a gateway failure); ``finish_reason length`` dropped
(``truncated``/``cut``); one redraw on a layer-1 reject except ``no_answer``; the notice
gate (``preflight`` then ``--notice-acknowledged``, a fresh preflight after any pause);
the ``PAUSE`` file and SIGTERM; 5 failures in a row / more than 20 in total; the input,
output and call caps counted across resumes plus ``prior_gateway_usage``; the yield
guards (the pool's ``expected_yield`` must be ``ADEQUATE`` unless ``--allow-low-yield``;
the yield watch pauses at a low early layer-1 pass rate, resume with
``--accept-low-yield``); the running observed-calls counter (``limits.max_calls`` /
``limits.notice_overrun``, see the round-3 module); private outputs refused inside the
repository; layer 2 through the isolated Claude CLI with the Codex fallback on a
usage-policy refusal (``verifier_unavailable`` = excluded, never accepted).

**Budget stop** (exit :data:`EXIT_BUDGET` = 76). The drafter's gateway is metered upstream
(Argo's budget, and the gateway's own): a usage / quota / budget notice is not a model
reply. The partner gateway's contract: a quota/usage stop is an error body with
``error.type: "provider_notice"`` (normally HTTP 503); a transient upstream failure is
``error.type: "provider_error"`` (503, or its own status: 502, 500, ...). Any of these
stops the run at once, without a retry and without counting it as an empty answer:

* ``error.type == "provider_notice"``, at any status;
* an HTTP 429 (whatever its body);
* an error status whose body has NO ``error.type`` and reads as a usage / quota / budget
  notice (:data:`BUDGET_RE`: a notice that reached us unconverted);
* a 200 whose content is not a JSON answer, is short and reads as one;
* :data:`UNKNOWN_503_STOP` (3) HTTP 503s in a row whose ``error.type`` is missing or
  unparseable (any other response resets the count).

Anything else at an error status (``provider_error`` at 503, 502, 500, ...; a lone untyped
503) keeps the existing failed-call path: nothing recorded, the job re-issued on the next
pass (``drafter.passes``) or on resume, counted toward the 5-in-a-row / 20-total failure
pause. On a budget stop, in-flight calls finish, the ``PAUSE`` file is written
(its content names the stop), the state and totals are written (status ``paused``,
reason ``budget_stop: ...``), ``budget-stop-<label>.json`` records the HTTP status and
the first 200 characters of the body with anything secret-shaped redacted
(:func:`redact`), and the run exits 76. To continue: remove ``PAUSE``, run
``preflight`` again, send the notice, ``draft --resume --notice-acknowledged``.

**Stop rule** (per paper and kind): once ``alias.goal`` jobs of a kind (default G 2, C 1)
have a layer-1 pass, the paper's remaining jobs of that kind are skipped. It is applied
only between drafting WAVES (the pool's ``wave``: wave 0 is the first ``goal`` jobs of
every paper and kind) and at every start or resume, never to a job already in flight, so
the set of drafted jobs is deterministic for a run that is not interrupted. Layer 1 is the
basis (layer 2 runs after drafting); a paper whose layer-2 rejects leave it short is
reported in the summary (``alias.papers_short_after_l2``), not redrafted.

**run-waves** (after the 2026-10-30 pilot, whose layer-1 stop rule halted drafting on
papers whose records the verifier then rejected). One command loops: plan the next wave
(:mod:`glossbench_alias_waves`: papers activated in a seeded order only as needed, the
next G/C jobs each unsatisfied paper still needs, no job on an alias already accepted;
with ``waves.c_first``, the default, a paper's C jobs before its G jobs)
-> draft it (the gateway, every rule above) -> layer 1 over the new drafts
(:meth:`AliasRun.check_new`: incremental; logged rows are never re-judged) -> layer 2
(the CLI verifier, counted separately as ``cli_calls``) -> recompute each paper's
satisfaction from ACCEPTED records (``goal.G`` accepted G on DISTINCT aliases,
:func:`glossbench_alias_checks.alias_key`, and ``goal.C`` accepted C) -> the next wave.
It stops on ``target_met`` (``waves.target_papers`` satisfied papers), ``pool_exhausted``,
``max_waves``, or any pause: the budget stop (exit 76), ``PAUSE``/SIGTERM, ``max_calls`` /
``notice_overrun`` (ONE observed-calls counter for every wave of the segment, never reset
between waves), the cumulative caps with ``prior_gateway_usage``, the failure pauses, the
yield guards, the verifier's ``max_calls``/``max_cost_usd``. The state file
(``waves-state-alias-<set>.json``) records the wave number, its phase
(draft/check/verify/done), its job ids, the active papers and a per-wave history; every
phase is idempotent, so ``--resume`` finishes the interrupted wave without drafting,
verifying or counting anything twice. A set drafts with ``draft`` OR ``run-waves``,
never both (each refuses the other's state file). Not with ``--pilot``. At the end the
summary is written (``alias.waves``).

The notice (``preflight --stage waves``, config block ``waves``,
:data:`glossbench_alias_waves.WAVE_DEFAULTS`): a seeded simulation of the remaining run
at the blended per-kind accept rates (the prior ``waves.accept_rate``, default the pilot's
G 0.36 / C 0.38, shrunk toward the run's measured rates as they come in): ``calls`` =
expected jobs x ``calls_per_job`` (1.32), tokens at ``prompt_tokens_per_call`` /
``completion_tokens_per_call`` (700 / 1,780); beside it the p95, a pessimistic scenario
(rates x ``pessimistic_factor``), ``calls_upper_bound`` (pessimistic p95 jobs, every redraw
and empty retry, plus ``max_fail_total``), the observed-calls stop, the expected verifier
calls, and the cumulative caps' headroom (``caps_stop_before_target`` names a cap that
would stop the run early).

**Records.** ``accepted-alias-<set>.jsonl`` (PRIVATE): ``record_id, kind (G|C), question,
answer, quote, alias, primary, alias_kind, paper, doc_id, source_chunk_id, gold_chunks
(the quote-bearing chunk ids), binding_terms ([primary]), flags, pilot, model,
prompt_sha256, prompt_version (:data:`glossbench_alias_checks.PROMPT_VERSION`; the draft rows carry it
too), verifier, verifier_refused, verifier_model, verifier_prompt_version``
(:data:`glossbench_alias_checks.VERIFY_VERSION`, also ``verify_version`` on every verify row and l2
log row; absent = alias-verify/1).

**Private config** (template: ``python/scripts/eval/examples/glossbench-alias-config.example.json``)::

    out_dir                    private output directory (never inside the repo)
    corpora.<set>              {chunks: [paths], paper_key, alias_glossary, expected_chunks}
    alias_pool                 the pool builder's block (glossbench_alias_pool.DEFAULTS)
    alias                      {goal: {G, C}, l1_pass_rate: {G, C}}  (default: the pool header's goal;
                               the pool's yield rates)
    alias_checks               layer-1 thresholds (glossbench_alias_checks.CHECK_DEFAULTS)
    waves                      run-waves (glossbench_alias_waves.WAVE_DEFAULTS): target_papers,
                               accept_rate {G, C}, prior_weight, max_new_papers_per_wave, max_waves,
                               max_jobs_per_paper {G, C}, calls_per_job, prompt/completion_tokens_per_call,
                               l1_pass_per_job {G, C}, pessimistic_factor, preflight_sims, c_first
    drafter, verifier, limits, yield_watch, prereg   as round 3 (glossbench_r2_draft.DEFAULTS)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import re
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import _glossbench_r2 as g  # noqa: E402
import deployed_docs_part4 as p4  # noqa: E402
import glossbench_alias_checks as ak  # noqa: E402
import glossbench_alias_pool as apool  # noqa: E402
import glossbench_alias_waves as wv  # noqa: E402
import glossbench_r2_checks as ck  # noqa: E402
import glossbench_r2_draft as dr  # noqa: E402

EXIT_OK, EXIT_HARD_STOP, EXIT_PAUSED = dr.EXIT_OK, dr.EXIT_HARD_STOP, dr.EXIT_PAUSED
#: a budget stop: distinct from a pause (75) so an operator script can tell them apart
EXIT_BUDGET = 76
PILOT_DEFAULT_RECORDS, PILOT_DEFAULT_PAPERS = 24, 3
CHECK_KEYS = set(ak.CHECK_DEFAULTS)
#: a usage / quota / budget notice (a body, never a model's JSON answer)
BUDGET_RE = re.compile(
    r"budget|quota|usage[ _-]?(?:limit|notice|cap|exceeded)|rate[ _-]?limit|too many requests|"
    r"insufficient[ _-]?(?:credit|funds|balance|quota)|billing|out of credits?|credit limit|"
    r"spend(?:ing)?[ _-]?(?:limit|cap)|limit (?:reached|exceeded)|exceeded (?:your|the) (?:limit|allowance)",
    re.I)
_SECRET_RE = re.compile(
    r"(bearer\s+)[^\s\"',;]+|((?:api[_-]?key|token|secret|password|authorization|key)\s*[\"']?\s*[:=]\s*[\"']?)"
    r"[^\s\"',;]+|sk-[A-Za-z0-9_-]{6,}|[A-Za-z0-9+/_=-]{32,}", re.I)


def redact(text: str, limit: int = 200) -> str:
    """The first ``limit`` characters of a response body with anything secret-shaped
    (a bearer token, ``key=...``/``token: ...``, ``sk-...``, a long opaque string) replaced."""
    def sub(m: re.Match[str]) -> str:
        return (m.group(1) or m.group(2) or "") + "[redacted]"
    return _SECRET_RE.sub(sub, " ".join((text or "").split()))[:limit]


#: the partner gateway's contract: a quota/usage stop is ``error.type: provider_notice`` (any
#: status, usually 503); a transient upstream failure is ``provider_error`` (503, 502, 500, ...)
NOTICE_TYPE, TRANSIENT_TYPE = "provider_notice", "provider_error"
#: HTTP 503s in a row with no parseable ``error.type`` that count as a budget stop
UNKNOWN_503_STOP = 3


def error_type(text: str) -> str | None:
    """``error.type`` of a JSON error body (``{"error": {"type": ...}}``), else None."""
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    err = obj.get("error") if isinstance(obj, dict) else None
    t = err.get("type") if isinstance(err, dict) else None
    return t if isinstance(t, str) and t else None


def budget_notice(text: str) -> bool:
    return bool(BUDGET_RE.search(text or ""))


class AliasGateway(dr.R2Gateway):
    """The round-3 gateway client with the budget stop (module docstring)."""

    def __init__(self, *a: Any, on_budget: Callable[[dict[str, Any]], None], **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.on_budget = on_budget
        #: HTTP 503s in a row whose body names no error.type (UNKNOWN_503_STOP of them stop the run)
        self.unknown_503 = 0

    def _budget(self, step: str, key: str, status: int, body: str, why: str) -> None:
        ev = {"http_status": status, "why": why, "body": redact(body), "key": key, "t": g.now_iso()}
        self._log(step, key, {"status": f"budget_stop:{status}", "why": why, "body": ev["body"]})
        g.log(f"BUDGET STOP: HTTP {status} ({why}): {ev['body']!r}")
        self.on_budget(ev)
        self.control.stop(f"budget_stop: HTTP {status} ({why})")

    async def once(self, http: Any, spec: dict[str, Any], msgs: list[dict[str, str]], step: str,
                   key: str) -> dict[str, Any] | None:
        while True:
            if not self.control.gateway_admit():
                return None
            body = self.body(spec, msgs)
            self.control.account("gateway")          # counted at admission (the observed-calls counter)
            try:
                r = await http.post(f"{self.url}/v1/chat/completions", json=body)
            except Exception as e:  # noqa: BLE001
                self._log(step, key, {"status": f"error:{type(e).__name__}"})
                return {"status": "error", "error": type(e).__name__}
            status, text = int(r.status_code), str(getattr(r, "text", "") or "")
            etype = error_type(text) if status >= 400 else None
            self.unknown_503 = self.unknown_503 + 1 if status == 503 and etype is None else 0
            if etype == "provider_notice":
                self._budget(step, key, status, text, "provider_notice")
                return None
            if status == 429:
                self._budget(step, key, status, text, "429")
                return None
            if status >= 400:
                if etype is None and budget_notice(text):          # a notice that reached us unconverted
                    self._budget(step, key, status, text, "notice")
                    return None
                if status == 503 and etype is None and self.unknown_503 >= UNKNOWN_503_STOP:
                    self._budget(step, key, status, text, f"503_x{UNKNOWN_503_STOP}_untyped")
                    return None
                if status == 400 and "temperature" in body and "temperature" in text.lower():
                    self.temperature_dropped.add(spec["model"])
                    g.log(f"{spec['model']}: HTTP 400 mentioning temperature; omitting temperature from now on")
                    continue
                self._log(step, key, {"status": f"error:http_{status}", "error_type": etype, "body": redact(text)})
                return {"status": "error", "error": f"http_{status}"}
            try:
                d = r.json()
                txt = d["choices"][0]["message"].get("content") or ""
                fin = d["choices"][0].get("finish_reason")
            except Exception:  # noqa: BLE001
                if budget_notice(text):
                    self._budget(step, key, status, text, "notice")
                    return None
                self._log(step, key, {"status": "error:malformed"})
                return {"status": "error", "error": "malformed"}
            stripped = txt.strip()
            if stripped and not stripped.startswith(("{", "```")) and len(stripped) < 600 and budget_notice(stripped):
                self._budget(step, key, status, stripped, "notice_in_reply")
                return None
            u = d.get("usage") or {}
            pt, ct = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
            self.control.account("gateway", pt, ct, call=False)
            if stripped:
                st = "ok"
            elif fin == "length":
                st = "truncated"
            else:
                st = "empty0" if ct == 0 else "empty"
            row = {"status": st, "pt": pt, "ct": ct, "finish": fin, "model": d.get("model")}
            self._log(step, key, row)
            return {**row, "text": txt}


class AliasRun(dr.Run):
    """One set's alias drafting directory (label ``alias-<set>``)."""

    verify_system = ak.VERIFY_SYSTEM
    verify_keys = ak.VERIFY_KEYS
    verify_version = ak.VERIFY_VERSION
    redraw_exempt = ak.REDRAW_EXEMPT

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.label = apool.label(self.set) + ("" if self.pilot is None else "-pilot")
        self.p = dr.Paths(self.out, self.label)
        self.budget_event: dict[str, Any] | None = None
        unknown = set(self.raw_cfg.get("alias_checks") or {}) - CHECK_KEYS
        if unknown:
            raise SystemExit(f"config block 'alias_checks': unknown keys {sorted(unknown)}")
        unknown = set(self.raw_cfg.get("alias") or {}) - {"goal", "l1_pass_rate"}
        if unknown:
            raise SystemExit(f"config block 'alias': unknown keys {sorted(unknown)}")

    def parse_verdict(self, text: str) -> tuple[dict[str, Any] | None, str | None]:
        return ak.parse_verdict(text)

    # -------------------------------------------------------- inputs
    def load(self) -> None:
        path = dr.Paths(self.out, apool.label(self.set)).pool
        self.header, self.pool = dr.read_pool(path)
        if self.header.get("schema") != apool.POOL_SCHEMA:
            raise SystemExit(f"{path.name}: not an alias pool ({self.header.get('schema')})")
        self.pool_sha = g.file_sha(path)
        self.checks_cfg = {**ak.CHECK_DEFAULTS, **(self.raw_cfg.get("alias_checks") or {})}
        self.mode = "alias"
        self.round3 = True                                  # the yield guards apply
        self.pool_yield = self.header.get("yield")
        self.fc, self.fields_sha, self.specs, self.pilot_plan = None, None, {}, None
        goal = {**(self.header.get("design") or {}).get("goal", {}), **((self.raw_cfg.get("alias") or {})
                                                                         .get("goal") or {})}
        self.goal = {k: int(goal.get(k, apool.DEFAULTS["goal"][k])) for k in ak.KINDS}
        jobs = [{**r, "field": None, "sampled": True, "sample_rank": r["rank"]} for r in self.pool]
        jobs.sort(key=lambda r: (r["wave"], r["paper"], r["stratum"], r["rank"]))
        if self.pilot is not None:
            n, k = self.pilot
            if n < 1 or k < 1:
                raise SystemExit(f"--pilot {n} --papers {k}: both at least 1")
            papers = sorted({r["paper"] for r in jobs})
            pick = sorted(random.Random(f"{self.header['seed']}|pilot").sample(papers, min(k, len(papers))))
            base, extra = divmod(n, len(pick)) if pick else (0, 0)
            quota = {p: base + (1 if i < extra else 0) for i, p in enumerate(pick)}
            keep: set[str] = set()
            for p in pick:
                own = sorted((r for r in jobs if r["paper"] == p), key=lambda r: (r["wave"], r["stratum"], r["rank"]))
                keep.update(r["job_id"] for r in own[:quota[p]])
            jobs = [r for r in jobs if r["job_id"] in keep]
            per = Counter(r["paper"] for r in jobs)
            self.pilot_plan = {"papers": len(pick), "papers_with_jobs": len(per),
                               "papers_sha256": g.sha_text("\n".join(pick)),
                               "jobs_planned": dict(Counter(r["stratum"] for r in jobs)),
                               "jobs_per_paper": [min(per.values(), default=0), max(per.values(), default=0)]}
        self.sampled = jobs

    @property
    def corpus(self) -> g.Corpus:
        if self._corpus is None:
            cc = apool.corpus_config(self.raw_cfg, self.set)
            if [g.file_sha(x) for x in cc["chunks"]] != self.header["inputs_sha256"]["chunks"]:
                raise p4.HardStop("chunk_export_differs_from_pool")
            self._corpus = g.build_corpus(g.read_chunks(cc["chunks"]), cc.get("paper_key", "doc_id"))
            for r in self.sampled:
                c = self._corpus.chunks.get(r["chunk_id"])
                if c is None or g.sha_text(c.content) != r["content_sha256"]:
                    raise p4.HardStop("pool_chunk_text_mismatch")
            self.idx = ck.PaperIndex(self._corpus, int(self.checks_cfg["top_frequent"]))
        return self._corpus

    # -------------------------------------------------------- jobs and the stop rule
    def draft_key(self, job: dict[str, Any], attempt: int) -> str:
        return f"draft|{job['job_id']}|a{attempt}"

    def messages_for(self, r: dict[str, Any], previous: dict[str, Any] | None) -> list[dict[str, str]]:
        prev = None if previous is None else {"reasons": previous["l1_inline"]["reasons"]}
        return ak.draft_messages(r["stratum"], self.corpus.chunks[r["chunk_id"]].content, r, prev)

    def drafted_done(self, st: p4.Store, r: dict[str, Any]) -> bool:
        return dr.Run.chunk_done(self, st, r)

    def job_passed(self, st: p4.Store, r: dict[str, Any]) -> bool:
        n = int(self.cfg["drafter"]["max_attempts_per_chunk"])
        return any((st.get(self.draft_key(r, a)) or {}).get("l1_inline", {}).get("verdict") == "pass"
                   for a in range(1, n + 1))

    def passes(self, st: p4.Store) -> Counter[tuple[str, str]]:
        return Counter((r["paper"], r["stratum"]) for r in self.sampled if self.job_passed(st, r))

    def skipped(self, st: p4.Store, r: dict[str, Any], passes: Counter[tuple[str, str]] | None = None) -> bool:
        """The stop rule: not drafted to the end, and its paper already has ``goal`` passes of its kind."""
        p = self.passes(st) if passes is None else passes
        return not self.drafted_done(st, r) and p[(r["paper"], r["stratum"])] >= self.goal[r["stratum"]]

    def chunk_done(self, st: p4.Store, r: dict[str, Any]) -> bool:
        return self.drafted_done(st, r) or self.skipped(st, r)

    def l1_tally(self, st: p4.Store) -> tuple[int, int, int]:
        """(jobs drafted, of them with a layer-1 pass, jobs neither drafted nor skipped)."""
        p = self.passes(st)
        done = sum(self.drafted_done(st, r) for r in self.sampled)
        passed = sum(self.job_passed(st, r) for r in self.sampled if self.drafted_done(st, r))
        left = sum(not self.drafted_done(st, r) and not self.skipped(st, r, p) for r in self.sampled)
        return done, passed, left

    def wave_todo(self, st: p4.Store, wave: int) -> list[dict[str, Any]]:
        p = self.passes(st)
        return [r for r in self.sampled if r["wave"] == wave and not self.drafted_done(st, r)
                and not self.skipped(st, r, p)]

    # -------------------------------------------------------- the size notice
    def stop_rule_rates(self) -> tuple[dict[str, float], str]:
        given = (self.raw_cfg.get("alias") or {}).get("l1_pass_rate")
        if given:
            rates = {k: float(given[k]) for k in ak.KINDS}
            src = "alias.l1_pass_rate"
        else:
            bk = (self.pool_yield or {}).get("by_kind") or {}
            rates = {k: float((bk.get(k) or {}).get("rate", apool.DEFAULTS["prior_pass_rate"][k])) for k in ak.KINDS}
            src = ("pool yield_calibration" if (self.pool_yield or {}).get("calibrated")
                   else "pool prior_pass_rate") + " (a layers-1-2 rate: over-estimates the jobs drafted)"
        for k, v in rates.items():
            if not 0 <= v <= 1:
                raise SystemExit(f"alias.l1_pass_rate.{k} {v}: a number in [0, 1]")
        return rates, src

    def expected_jobs(self, st: p4.Store) -> float:
        """The jobs still to draft, expected under the stop rule (module docstring)."""
        rates, _ = self.stop_rule_rates()
        have = self.passes(st)
        waves: dict[tuple[str, str], Counter[int]] = {}
        for r in self.sampled:
            if not self.chunk_done(st, r):
                waves.setdefault((r["paper"], r["stratum"]), Counter())[r["wave"]] += 1
        return sum(apool.expected_drafted([c[w] for w in sorted(c)], self.goal[k], rates[k], have[(p, k)])
                   for (p, k), c in waves.items())

    def draft_plan(self) -> dict[str, Any]:
        plan = super().draft_plan()
        st = self.drafts()
        d = self.cfg["drafter"]
        planned = int(plan["chunks"])
        exp = self.expected_jobs(st)
        share = exp / planned if planned else 0.0
        factor = float(d["attempts_factor"])
        rates, src = self.stop_rule_rates()
        upper = planned * int(d["max_attempts_per_chunk"]) * 2 + int(self.cfg["limits"]["max_fail_total"])
        plan.update({
            "calls": math.ceil(exp * factor),
            "est_input_tokens": math.ceil(plan["est_input_tokens"] * share),
            "est_output_tokens": math.ceil(exp * factor * int(d["est_output_tokens"])),
            "calls_if_no_stop_rule": plan["calls"],
            "calls_upper_bound": upper,
            "max_output_tokens": upper * int(d["max_tokens"]),
            "jobs_planned": planned, "jobs_expected": round(exp, 2),
            "stop_rule": {"goal": self.goal, "l1_pass_rate": rates, "rate_source": src, "attempts_factor": factor},
            "estimate": "calls/tokens: EXPECTED under the stop rule (jobs_expected x attempts_factor); "
                        "calls_upper_bound: every planned job, every redraw and empty retry, plus max_fail_total "
                        "re-issued failures; max_output_tokens: calls_upper_bound x max_tokens"})
        return plan

    def realised(self) -> dict[str, Any]:
        """Realised gateway use (the draft state's totals) beside the latest notice."""
        stp = self.p.state("draft")
        tot = ((json.loads(stp.read_text()) if stp.is_file() else {}).get("totals") or {}).get("since_start") or {}
        pfp = self.p.preflight("draft")
        plan = (json.loads(pfp.read_text()) if pfp.is_file() else {}).get("plan") or {}
        st = self.drafts()
        drafted = [r for r in self.sampled if self.drafted_done(st, r)]
        calls = int(tot.get("gateway_calls") or 0)
        by_kind = {}
        for k in ak.KINDS:
            dk = [r for r in drafted if r["stratum"] == k]
            npass = sum(self.job_passed(st, r) for r in dk)
            by_kind[k] = {"drafted": len(dk), "l1_passed": npass, "l1_pass_rate": (npass / len(dk)) if dk else None}
        return {"gateway_calls": calls, "gateway_prompt_tokens": int(tot.get("gateway_prompt_tokens") or 0),
                "gateway_completion_tokens": int(tot.get("gateway_completion_tokens") or 0),
                "jobs_drafted": len(drafted), "calls_per_drafted_job": (calls / len(drafted)) if drafted else None,
                "by_kind": by_kind,
                "latest_notice": {k: plan.get(k) for k in ("calls", "jobs_expected", "jobs_planned", "calls_upper_bound",
                                                           "est_input_tokens", "est_output_tokens")}}

    # -------------------------------------------------------- draft
    def _on_budget(self, ev: dict[str, Any]) -> None:
        if self.budget_event is None:
            self.budget_event = ev

    def finish(self, stage: str, body: Callable[[], None]) -> int:
        rc = super().finish(stage, body)
        if stage in ("draft", "waves"):
            t = self.control.totals()
            pf = json.loads(self.p.preflight(stage).read_text()).get("plan") or {}
            dr.notice("GATEWAY", f"draft set={self.label}: realised {t['since_start'].get('gateway_calls', 0)} gateway "
                      f"calls ({t['since_resume'].get('gateway_calls', 0)} this segment), "
                      f"{t['since_start'].get('gateway_prompt_tokens', 0)} tokens in / "
                      f"{t['since_start'].get('gateway_completion_tokens', 0)} out; this segment's notice: "
                      f"{pf.get('calls')} calls expected, upper bound {pf.get('calls_upper_bound')}")
        if rc == EXIT_PAUSED and self.budget_event is not None:
            ev = {**self.budget_event, "set": self.set, "label": self.label,
                  "totals": self.control.totals()["since_start"]}
            g.write_json_atomic(self.out / f"budget-stop-{self.label}.json", ev)
            self.p.pause.write_text(f"budget stop {ev['t']}: HTTP {ev['http_status']} ({ev['why']}); "
                                    f"see budget-stop-{self.label}.json; remove this file, run preflight, "
                                    f"then {'run-waves' if stage == 'waves' else 'draft'} --resume\n",
                                    encoding="utf-8")
            dr.notice("GATEWAY", f"{stage} set={self.label} BUDGET STOP (HTTP {ev['http_status']}, {ev['why']}); "
                      "PAUSE written; exit 76")
            return EXIT_BUDGET
        return rc

    def _draft_setup(self) -> None:
        """The gateway client and the draft store for this segment (every wave shares them)."""
        _ = self.corpus
        d = self.cfg["drafter"]
        if not d.get("base_url"):
            raise p4.HardStop("drafter.base_url missing from the private config")
        url = str(d["base_url"]).rstrip("/")
        if url.endswith("/v1"):
            url = url[: -len("/v1")]
        self._gw = AliasGateway(url, self.control, {**self.cfg["limits"], "empty_retries": 1,
                                                    "empty_backoff_s": float(d["empty_retry_s"])}, self.p.calls,
                                on_budget=self._on_budget)
        self._spec = {"model": d["model"], "tag": d["tag"], "max_tokens": int(d["max_tokens"]),
                      "temperature": d.get("temperature")}
        self._st = self.drafts()

    def _job(self, r: dict[str, Any]) -> Callable[[Any], Any]:
        d, st, gw, spec = self.cfg["drafter"], self._st, self._gw, self._spec

        async def fn(http: Any) -> None:
            prev = None
            for a in range(1, int(d["max_attempts_per_chunk"]) + 1):
                k = self.draft_key(r, a)
                row = st.get(k)
                if row is None:
                    msgs = self.messages_for(r, prev)
                    res = await gw.generate(http, spec, msgs, "draft", k, float(d["empty_retry_s"]))
                    if res is None:
                        return
                    row = self.draft_row(r, a, k, msgs, res)
                    if row["status"] == "empty_twice":
                        self.control.fail("empty")
                    st.put(row)
                if row["status"] in dr.DROPPED or row["l1_inline"]["verdict"] == "pass" \
                        or set(row["l1_inline"]["reasons"]) & self.redraw_exempt:
                    return
                prev = row

        async def watched(http: Any) -> None:
            await fn(http)
            self.watch_yield(st)
        return watched

    def _draft_list(self, todo_of: Callable[[], list[dict[str, Any]]], what: str) -> None:
        """Draft ``todo_of()`` (re-evaluated after each pass) for ``drafter.passes`` passes;
        a stop is a pause, jobs still failing after the passes pause the run."""
        d = self.cfg["drafter"]

        async def go(todo: list[dict[str, Any]]) -> None:
            http = self.http_factory()
            try:
                await dr.run_jobs([self._job(r) for r in todo], int(d["inflight"]), self.control, http)
            finally:
                close = getattr(http, "aclose", None)
                if close is not None:
                    await close()

        todo = todo_of()
        if todo:
            dr.notice("GATEWAY", f"draft set={self.label}: {what}, {len(todo)} jobs "
                      f"(<= {d['inflight']} in flight, model {d['model']})")
        for _ in range(int(d["passes"])):
            if not todo:
                break
            asyncio.run(go(todo))
            if self.control.stopped():
                raise p4.Pause(self.control.reason or "stopped")
            todo = todo_of()
        if todo:
            raise p4.Pause(f"calls_failed:{len(todo)}")

    def _draft_body(self) -> None:
        self._draft_setup()
        st = self._st
        self.watch_yield(st)
        if self.control.stopped():
            raise p4.Pause(self.control.reason or "stopped")
        for wave in sorted({r["wave"] for r in self.sampled}):
            self._draft_list(lambda w=wave: self.wave_todo(st, w), f"wave {wave}")  # type: ignore[misc]
        w = self.yield_watch
        if w is not None and w.get("status") == "pending":
            done, passed, _ = self.l1_tally(st)
            w.update({"status": "not_reached", "jobs": done, "passes": passed,
                      "rate": (passed / done) if done else None, "t": g.now_iso()})

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
        draft, err = ak.parse_draft(text) if status == "ok" else (None, None)
        row = {"k": k, "record_id": dr.record_id(self.label, r["chunk_id"], attempt, r["job_id"]), "set": self.set,
               "field": None, "job_id": r["job_id"], "pilot": self.pilot is not None,
               **{x: r.get(x) for x in ("alias", "primary", "alias_kind", "def_chunk_id", "entry_key", "wave",
                                        "rank")},
               "chunk_id": r["chunk_id"], "paper": r["paper"], "doc_id": r["doc_id"], "stratum": r["stratum"],
               "attempt": attempt, "status": status, "text": text, "draft": draft, "parse_error": err,
               "model": res.get("model"), "pt": res.get("pt"), "ct": res.get("ct"), "finish": res.get("finish"),
               "http_attempts": res.get("attempts"), "prompt_sha256": g.sha_text(json.dumps(msgs)),
               "prompt_version": ak.PROMPT_VERSION, "t": g.now_iso()}
        row["l1_inline"] = (self.run_layer1(row) if status == "ok"
                            else {"verdict": "dropped", "reasons": [status], "flags": []})
        return row

    def run_layer1(self, row: dict[str, Any]) -> dict[str, Any]:
        return ak.layer1(row["draft"], row["parse_error"], kind=row["stratum"], entry=row, paper=row["paper"],
                         source_chunk=self.corpus.chunks[row["chunk_id"]], idx=self.idx, cfg=self.checks_cfg)

    def layer1_rules(self) -> dict[str, Any]:
        return {"alias_checks": dict(sorted(self.checks_cfg.items())), "goal": self.goal}

    # -------------------------------------------------------- verify / summary
    def verify_prompt(self, rec: dict[str, Any]) -> str:
        c = self.corpus
        dc = rec.get("def_chunk_id")
        definition = c.chunks[dc].content if dc and dc in c.chunks else ""
        return ak.verify_prompt(rec["stratum"], rec["draft"], rec, c.chunks[rec["chunk_id"]].content, definition,
                                c.title(rec["paper"]))

    def accepted_row(self, r: dict[str, Any]) -> dict[str, Any]:
        d = r["draft"]
        return {"record_id": r["record_id"], "set": self.set, "kind": r["stratum"], "stratum": r["stratum"],
                "question": d["question"], "answer": d["answer"], "quote": d["quote"],
                "alias": r["alias"], "primary": r["primary"], "alias_kind": r["alias_kind"],
                "def_chunk_id": r.get("def_chunk_id"), "binding_terms": [r["primary"]],
                "paper": r["paper"], "doc_id": r["doc_id"], "source_chunk_id": r["chunk_id"],
                "job_id": r["job_id"], "pilot": bool(r.get("pilot")), "model": r.get("model"),
                "prompt_sha256": r.get("prompt_sha256"),
                # rows drafted before the field existed used the pilot's prompts (alias-draft/1)
                "prompt_version": r.get("prompt_version") or "alias-draft/1",
                "verifier": (r["l2"].get("verifier") or "claude") if r.get("l2") else None,
                "verifier_refused": (r.get("l2") or {}).get("verifier_refused"),
                "verifier_model": (r.get("l2") or {}).get("served_model"),
                # l2 rows written before the field existed used the pilots' verifier (alias-verify/1)
                "verifier_prompt_version": ((r.get("l2") or {}).get("verify_version") or "alias-verify/1")
                if r.get("l2") else None,
                "gold_chunks": r["l1"]["gold_chunk_ids"], "flags": r["l1"]["flags"]}

    def summary(self) -> int:
        rc = super().summary()
        if rc != EXIT_OK:
            return rc
        st = self.drafts()
        p = self.passes(st)
        acc = Counter((r["paper"], r["stratum"]) for r in self.accepted())
        papers = sorted({r["paper"] for r in self.sampled})
        s = json.loads(self.p.summary.read_text(encoding="utf-8"))
        s["alias"] = {
            "goal": self.goal,
            "jobs_planned": dict(Counter(r["stratum"] for r in self.sampled)),
            "jobs_drafted": dict(Counter(r["stratum"] for r in self.sampled if self.drafted_done(st, r))),
            "jobs_skipped_by_stop_rule": dict(Counter(r["stratum"] for r in self.sampled
                                                      if self.skipped(st, r, p))),
            "papers": len(papers),
            "papers_satisfied_l1": {k: sum(p[(x, k)] >= self.goal[k] for x in papers) for k in ak.KINDS},
            "papers_satisfied_accepted": {k: sum(acc[(x, k)] >= self.goal[k] for x in papers) for k in ak.KINDS},
            "papers_short_after_l2": sum(any(p[(x, k)] >= self.goal[k] > acc[(x, k)] for k in ak.KINDS)
                                         for x in papers),
            "accepted_by_kind": {k: sum(v for (_, kk), v in acc.items() if kk == k) for k in ak.KINDS},
            "realised": self.realised(),
            "prompt_versions_accepted": dict(Counter(str(r.get("prompt_version") or "alias-draft/1")
                                                     for r in self.accepted())),
            "verifier_prompt_versions_accepted": dict(Counter(
                str((r.get("l2") or {}).get("verify_version") or "alias-verify/1") for r in self.accepted())),
        }
        wst = self.read_state("waves")
        if wst is not None:
            states = self.paper_states()
            ws = wst.get("waves") or {}
            s["alias"]["waves"] = {
                "target_papers": int(self.waves_cfg["target_papers"]), "stop": ws.get("stop"),
                "waves": ws.get("wave"), "papers_active": ws.get("n_active"),
                "papers_satisfied": sum(wv.satisfied(ps, self.goal) for ps in states.values()),
                "papers_with_distinct_alias_G": sum(len(ps.acc_keys) >= self.goal["G"] for ps in states.values()),
                "papers_with_C": sum(ps.acc_c >= self.goal["C"] for ps in states.values()),
                "history": ws.get("history"),
                "totals": (wst.get("totals") or {}).get("since_start"),
                "rule": "satisfied = goal.G accepted G on distinct aliases (alias_key) and goal.C accepted C"}
        g.write_json_atomic(self.p.summary, s)
        return EXIT_OK


    # -------------------------------------------------------- waves (keyed on ACCEPTED records)
    @property
    def waves_cfg(self) -> dict[str, Any]:
        return wv.waves_config(self.raw_cfg.get("waves"))

    def caps(self) -> dict[str, int | None]:
        c = self.waves_cfg["max_jobs_per_paper"]
        return {k: None if c[k] is None else int(c[k]) for k in ak.KINDS}

    def job_outcomes(self) -> dict[str, str]:
        """job_id -> ``accepted`` | ``rejected`` | ``pending`` for every drafted job (absent:
        not drafted). Accepted = a record of the job passed layers 1 and 2 (the logs)."""
        st, log = self.drafts(), self.acceptance()
        by_job: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in st.rows.values():
            by_job[row["job_id"]].append(row)
        out: dict[str, str] = {}
        for r in self.sampled:
            rows = by_job.get(r["job_id"])
            if not rows:
                continue
            if not self.drafted_done(st, r):
                out[r["job_id"]] = "pending"
                continue
            state = "rejected"
            for row in sorted(rows, key=lambda x: x["attempt"]):
                if row["status"] in dr.DROPPED:
                    continue
                l1 = log.get(f"l1|{row['record_id']}")
                if l1 is None:
                    state = "pending"
                    break
                if l1["verdict"] != "pass":
                    continue
                l2 = log.get(f"l2|{row['record_id']}")
                if l2 is None:
                    state = "pending"
                    break
                if l2["verdict"] == "pass":
                    state = "accepted"
                    break
            out[r["job_id"]] = state
        return out

    def paper_states(self, outcomes: dict[str, str] | None = None) -> dict[str, wv.PaperState]:
        o = self.job_outcomes() if outcomes is None else outcomes
        states: dict[str, wv.PaperState] = {}
        for r in sorted(self.sampled, key=lambda x: (x["paper"], x["stratum"], x["rank"])):
            ps = states.setdefault(r["paper"], wv.PaperState(r["paper"]))
            jid, key = r["job_id"], ak.alias_key(r["alias"], r.get("alias_kind"))
            if r["stratum"] == "G":
                ps.g.append((jid, key))
            else:
                ps.c.append(jid)
            if jid in o:
                ps.drafted.add(jid)
            if o.get(jid) == "accepted":
                if r["stratum"] == "G":
                    ps.acc_keys.add(key)
                else:
                    ps.acc_c += 1
        return states

    def wave_order(self, states: dict[str, wv.PaperState]) -> list[str]:
        """The reachable papers in the seeded activation order (``<pool seed>|waves``)."""
        ok = sorted(p for p, ps in states.items() if wv.reachable(ps, self.goal))
        return random.Random(f"{self.header['seed']}|waves").sample(ok, len(ok))

    def wave_rates(self, outcomes: dict[str, str]) -> dict[str, Any]:
        """Per kind: the measured acceptance per resolved drafted job, blended with the prior."""
        c = self.waves_cfg
        kind = {r["job_id"]: r["stratum"] for r in self.sampled}
        out: dict[str, Any] = {"rates": {}, "measured": {}}
        for k in ak.KINDS:
            n = sum(1 for j, v in outcomes.items() if kind[j] == k and v != "pending")
            a = sum(1 for j, v in outcomes.items() if kind[j] == k and v == "accepted")
            out["measured"][k] = {"drafted": n, "accepted": a}
            out["rates"][k] = wv.blend(a, n, float(c["accept_rate"][k]), float(c["prior_weight"]))
        return out

    def job_info(self) -> dict[str, tuple[str, str, str]]:
        return {r["job_id"]: (r["paper"], r["stratum"], ak.alias_key(r["alias"], r.get("alias_kind")))
                for r in self.sampled}

    def waves_plan(self) -> dict[str, Any]:
        """The run-waves size notice: a seeded forecast of the remaining run (module docstring)."""
        c, d, lim = self.waves_cfg, self.cfg["drafter"], self.cfg["limits"]
        prev = (self.read_state("waves") or {}).get("waves") or {}
        outcomes = self.job_outcomes()
        states = self.paper_states(outcomes)
        order = self.wave_order(states)
        n_active, wave0 = int(prev.get("n_active") or 0), int(prev.get("wave") or 0)
        rr = self.wave_rates(outcomes)
        info = self.job_info()
        target = int(c["target_papers"])
        args = (states, order, n_active, info)
        kw: dict[str, Any] = {"goal": self.goal, "caps": self.caps(), "target": target, "max_new": int(c["max_new_papers_per_wave"]),
              "max_waves": int(c["max_waves"]), "wave0": wave0, "sims": int(c["preflight_sims"]),
              "c_first": bool(c["c_first"])}
        exp = wv.forecast(*args, rates=rr["rates"], seed=f"{self.header['seed']}|waves-preflight", **kw)
        pess_rates = {k: float(v) * float(c["pessimistic_factor"]) for k, v in rr["rates"].items()}
        pess = wv.forecast(*args, rates=pess_rates, seed=f"{self.header['seed']}|waves-preflight", **kw)
        cpj, tin, tout = float(c["calls_per_job"]), int(c["prompt_tokens_per_call"]), int(c["completion_tokens_per_call"])
        calls = math.ceil(exp["jobs_mean"] * cpj)
        attempts = int(d["max_attempts_per_chunk"]) * 2                  # every redraw and empty retry
        upper = math.ceil(pess["jobs_p95"] * attempts + int(lim["max_fail_total"]))
        left_jobs = sum(len(wv.remaining(states[p], self.caps())[0]) + len(wv.remaining(states[p], self.caps())[1])
                        for p in order)
        cli = {k: exp["jobs_mean_by_kind"][k] * float(c["l1_pass_per_job"][k]) for k in ak.KINDS}
        over = lim.get("notice_overrun")
        stop_at = [x for x in (lim.get("max_calls"), math.ceil(calls * (1 + float(over))) if over is not None else None)
                   if x is not None]
        satisfied = sum(wv.satisfied(states[p], self.goal) for p in order)
        prior = dr.resolve_prior_usage(lim.get("prior_gateway_usage"),
                                       (self.p.state("waves"), self.p.totals("waves")))
        spent = (self.read_state("waves") or {}).get("totals", {}).get("since_start", {})
        cum_out = int(prior["completion_tokens"]) + int(spent.get("gateway_completion_tokens") or 0)
        cum_in = int(prior["prompt_tokens"]) + int(spent.get("gateway_prompt_tokens") or 0)
        cum_calls = int(prior["calls"]) + int(spent.get("gateway_calls") or 0)
        headroom = {"output_token_cap": lim.get("output_token_cap"), "input_token_cap": lim.get("input_token_cap"),
                    "call_cap": lim.get("call_cap"), "spent_before": {"calls": cum_calls, "prompt_tokens": cum_in,
                                                                      "completion_tokens": cum_out},
                    "expected_after": {"calls": cum_calls + calls, "prompt_tokens": cum_in + calls * tin,
                                       "completion_tokens": cum_out + calls * tout}}
        headroom["caps_stop_before_target"] = [
            k for k, cap, v in (("output_token_cap", lim.get("output_token_cap"), cum_out + calls * tout),
                                ("input_token_cap", lim.get("input_token_cap"), cum_in + calls * tin),
                                ("call_cap", lim.get("call_cap"), cum_calls + calls))
            if cap is not None and v >= int(cap)]
        return {
            "calls": calls, "est_input_tokens": calls * tin, "est_output_tokens": calls * tout,
            "calls_p95": math.ceil(exp["jobs_p95"] * cpj),
            "calls_pessimistic": math.ceil(pess["jobs_mean"] * cpj),
            "calls_pessimistic_p95": math.ceil(pess["jobs_p95"] * cpj),
            "calls_upper_bound": upper, "max_output_tokens": upper * int(d["max_tokens"]),
            "calls_if_every_remaining_job": math.ceil(left_jobs * attempts + int(lim["max_fail_total"])),
            "run_stops_at_calls": min(stop_at) if stop_at else None,
            "cli_calls_expected": math.ceil(sum(cli.values())),
            "models": [d["model"]], "concurrency": int(d["inflight"]),
            "verifier": {"model": self.cfg["verifier"]["model"], "route": "CLI (off-gateway)",
                         "inflight": int(self.cfg["verifier"]["inflight"])},
            "target_papers": target, "satisfied_now": satisfied, "papers_reachable": len(order),
            "papers_active_now": n_active, "wave_now": wave0, "goal": self.goal,
            "rates": rr, "pessimistic_rates": pess_rates, "forecast": exp, "forecast_pessimistic": pess,
            "cost_model": {"calls_per_job": cpj, "prompt_tokens_per_call": tin, "completion_tokens_per_call": tout,
                           "l1_pass_per_job": c["l1_pass_per_job"]},
            "caps_headroom": headroom, "prompt_version": ak.PROMPT_VERSION,
            "pool_yield": {k: (self.pool_yield or {}).get(k) for k in ("expected_yield", "gate", "status")},
            "estimate": "calls: forecast jobs_mean x calls_per_job (seeded simulation of the wave policy at the "
                        "blended accept rates); calls_p95 its 95th percentile; *_pessimistic at the rates x "
                        "pessimistic_factor; calls_upper_bound: the pessimistic p95 jobs x max_attempts x 2 (every "
                        "redraw and empty retry) + max_fail_total; run_stops_at_calls: the observed-calls cap "
                        "(min of max_calls and calls x (1 + notice_overrun))"}

    def check_new(self) -> int:
        """Layer 1 over the drafts not yet logged (run-waves): the logged rows are never
        re-judged, a new record's duplicate question is judged against the logged passes
        first, and ``shared_chunk`` flags only the new record (the log is append-only)."""
        _ = self.corpus
        drafts, log = self.drafts(), self.acceptance()
        by_rid = {r["record_id"]: r for r in drafts.rows.values()}
        seen: set[tuple[str, str]] = set()
        use: Counter[str] = Counter()
        for row in log.rows.values():
            if row["layer"] == "l1" and row["verdict"] == "pass" and row["record_id"] in by_rid:
                d = by_rid[row["record_id"]]
                seen.add((d["paper"], g.gold_norm(d["draft"]["question"])))
                use.update(set(row.get("gold_chunk_ids") or []))
        added, new = 0, []
        for row in sorted(drafts.rows.values(), key=lambda x: (x["paper"], x["record_id"])):
            base = {"record_id": row["record_id"], "set": self.set, "paper": row["paper"],
                    "stratum": row["stratum"], "attempt": row["attempt"], "t": g.now_iso()}
            if row["status"] in dr.DROPPED:
                added += self.log_row(log, {"k": f"draft|{row['record_id']}", "layer": "draft", "verdict": "dropped",
                                            "reasons": [row["status"]], "flags": [], **base})
            elif f"l1|{row['record_id']}" not in log:
                new.append({**row, "l1": self.run_layer1(row)})
        kept = []
        for r in new:
            if r["l1"]["verdict"] != "pass":
                continue
            key = (r["paper"], g.gold_norm(r["draft"]["question"]))
            if key in seen:
                r["l1"]["reasons"] = sorted({*r["l1"]["reasons"], "duplicate_question"})
                r["l1"]["verdict"] = "reject"
                continue
            seen.add(key)
            kept.append(r)
        use.update(cid for r in kept for cid in set(r["l1"]["gold_chunk_ids"]))
        for r in kept:
            if any(use[cid] > 1 for cid in r["l1"]["gold_chunk_ids"]):
                r["l1"]["flags"] = sorted({*r["l1"]["flags"], "shared_chunk"})
        for r in new:
            l1 = r["l1"]
            added += self.log_row(log, {"k": f"l1|{r['record_id']}", "layer": "l1", "verdict": l1["verdict"],
                                        "reasons": l1["reasons"], "flags": l1["flags"],
                                        "gold_chunk_ids": l1["gold_chunk_ids"], "leak_overlap": l1["leak_overlap"],
                                        "leak_distinctive": l1["leak_distinctive"], "leak_ok": l1["leak_ok"],
                                        "record_id": r["record_id"], "set": self.set, "paper": r["paper"],
                                        "stratum": l1.get("record_stratum", r["stratum"]),
                                        "chunk_stratum": r["stratum"], "field": None,
                                        "attempt": r["attempt"], "t": g.now_iso()})
        return added

    def check(self) -> int:
        if self.p.state("waves").exists():          # a run-waves directory: the incremental check
            self.load()
            added = self.check_new()
            g.log(f"check set={self.label} (run-waves, incremental): {added} new log rows")
            return EXIT_OK
        return super().check()

    def draft(self, resume: bool = False, acknowledged: bool = False, allow_low_yield: bool = False,
              accept_low_yield: bool = False) -> int:
        if self.p.state("waves").exists():
            g.log(f"draft refused: {self.p.state('waves').name} exists (this set drafts with run-waves)")
            return EXIT_HARD_STOP
        return super().draft(resume, acknowledged, allow_low_yield, accept_low_yield)

    def preflight(self, stage: str) -> dict[str, Any]:
        if stage != "waves":
            return super().preflight(stage)
        if self.pilot is not None:
            raise SystemExit("run-waves: not with --pilot (the pilot has its own files and stop rule)")
        self.load()
        plan = self.waves_plan()
        rec = {"stage": stage, "set": self.set, "created": g.now_iso(), "created_epoch": time.time(),
               "plan_sha256": self.plan_sha(stage), "plan": plan}
        g.write_json_atomic(self.p.preflight(stage), rec)
        f = plan["forecast"]
        dr.notice("GATEWAY", f"run-waves set={self.label}: target {plan['target_papers']} papers "
                  f"({plan['satisfied_now']} satisfied now); expected {plan['calls']} calls (p95 {plan['calls_p95']}; "
                  f"pessimistic {plan['calls_pessimistic']}, p95 {plan['calls_pessimistic_p95']}), est. "
                  f"{plan['est_input_tokens']} tokens in / {plan['est_output_tokens']} out (upper bound "
                  f"{plan['calls_upper_bound']} calls / {plan['max_output_tokens']} tokens out), ~{f['waves_mean']} "
                  f"waves, ~{f['papers_activated_mean']} papers activated, P(target) {f['p_target_met']}; automatic "
                  f"stop at {plan['run_stops_at_calls']} observed calls; models {', '.join(plan['models'])}, "
                  f"<= {plan['concurrency']} in flight")
        dr.notice("CLI", f"run-waves set={self.label}: ~{plan['cli_calls_expected']} verifier calls "
                  f"({plan['verifier']['model']}, CLI off-gateway, <= {plan['verifier']['inflight']} in flight)")
        if plan["caps_headroom"]["caps_stop_before_target"]:
            g.log(f"run-waves preflight: WARNING the cumulative caps "
                  f"{plan['caps_headroom']['caps_stop_before_target']} stop the run before the expected end")
        return rec

    def write_state(self, stage: str, status: str, reason: str | None) -> None:
        super().write_state(stage, status, reason)
        if stage != "waves":
            return
        path = self.p.state(stage)
        st = json.loads(path.read_text())
        st.update({"waves": getattr(self, "wave_state", None), "yield_watch": self.yield_watch,
                   "low_yield_allowed": self.low_yield_allowed, "prompt_version": ak.PROMPT_VERSION})
        g.write_json_atomic(path, st)

    def write_totals(self, stage: str, status: str, reason: str | None) -> None:
        super().write_totals(stage, status, reason)
        if stage != "waves":
            return
        path = self.p.totals(stage)
        t = json.loads(path.read_text())
        rows = list(self.drafts().rows.values())
        t.update({"rows": len(rows), "row_status": dict(sorted(Counter(str(r.get("status")) for r in rows).items())),
                  "served_models": dict(sorted(Counter(str(r.get("model")) for r in rows if r.get("model")).items())),
                  "model": self.cfg["drafter"]["model"], "verifier_model": self.cfg["verifier"]["model"],
                  "cli_cost_usd": round(sum(float(r.get("cost_usd") or 0)
                                            for r in p4.Store(self.p.verify_rows).rows.values()), 4)})
        g.write_json_atomic(path, t)

    def run_waves(self, resume: bool = False, acknowledged: bool = False, allow_low_yield: bool = False,
                  accept_low_yield: bool = False) -> int:
        if self.pilot is not None:
            raise SystemExit("run-waves: not with --pilot (the pilot has its own files and stop rule)")
        _ = self.waves_cfg                                   # a config error before anything starts
        if self.p.state("draft").exists():
            g.log(f"run-waves refused: {self.p.state('draft').name} exists (this set drafted with the draft stage)")
            return EXIT_HARD_STOP
        self.wave_state: dict[str, Any] = {"wave": 0, "phase": "plan", "jobs": [], "n_active": 0, "history": [],
                                           "stop": None}
        rc = self.begin("waves", resume, acknowledged)
        if rc is not None:
            return rc
        self.wave_state = dict((self.prev_state or {}).get("waves") or self.wave_state)
        rc = self.yield_guards(allow_low_yield, accept_low_yield)
        if rc is not None:
            return rc
        pf = json.loads(self.p.preflight("waves").read_text())
        self.control.set_call_cap((pf.get("plan") or {}).get("calls"))
        rc = self.finish("waves", self._waves_body)
        if rc == EXIT_OK:
            rc = self.summary()
        return rc

    def _waves_body(self) -> None:
        c = self.waves_cfg
        ws = self.wave_state
        self._draft_setup()
        self.watch_yield(self._st)
        if self.control.stopped():
            raise p4.Pause(self.control.reason or "stopped")
        target = int(c["target_papers"])
        while True:
            if ws["phase"] in ("draft", "check", "verify"):      # a wave in progress (a resume)
                self._run_wave(ws)
                continue
            outcomes = self.job_outcomes()
            states = self.paper_states(outcomes)
            order = self.wave_order(states)
            rr = self.wave_rates(outcomes)
            n, jobs, stop = wv.plan_wave(states, order, int(ws["n_active"]), target, rr["rates"], self.goal,
                                         self.caps(), int(c["max_new_papers_per_wave"]), bool(c["c_first"]))
            if stop is None and int(ws["wave"]) >= int(c["max_waves"]):
                stop, n = "max_waves", int(ws["n_active"])
            if stop:
                ws.update({"stop": stop, "n_active": n})
                break
            by_id = self.job_by_id
            ws.update({"wave": int(ws["wave"]) + 1, "phase": "draft", "jobs": jobs, "n_active": n,
                       "planned": {"rates": rr["rates"], "measured": rr["measured"],
                                   "jobs": dict(Counter(by_id[j]["stratum"] for j in jobs)),
                                   "activated_new": n - int(ws["n_active"])}})
            self.write_state("waves", "running", None)
            self._run_wave(ws)
        sat = sum(wv.satisfied(ps, self.goal) for ps in self.paper_states().values())
        dr.notice("GATEWAY", f"run-waves set={self.label}: stopped ({ws['stop']}) after wave {ws['wave']}; "
                  f"{sat} papers satisfied (target {target})")
        w = self.yield_watch
        if w is not None and w.get("status") == "pending":
            done, passed, _ = self.l1_tally(self._st)
            w.update({"status": "not_reached", "jobs": done, "passes": passed,
                      "rate": (passed / done) if done else None, "t": g.now_iso()})

    @property
    def job_by_id(self) -> dict[str, dict[str, Any]]:
        return {r["job_id"]: r for r in self.sampled}

    def _run_wave(self, ws: dict[str, Any]) -> None:
        """Draft, check and verify one wave; every phase is idempotent, so a resume redoes
        the interrupted wave's remaining work only (nothing is drafted or counted twice)."""
        by_id = self.job_by_id
        jobs = [by_id[j] for j in ws["jobs"]]
        ws["phase"] = "draft"
        self.write_state("waves", "running", None)
        self._draft_list(lambda: [r for r in jobs if not self.drafted_done(self._st, r)], f"wave {ws['wave']}")
        ws["phase"] = "check"
        self.write_state("waves", "running", None)
        self.check_new()
        ws["phase"] = "verify"
        self.write_state("waves", "running", None)
        self._verify_body()
        outcomes = self.job_outcomes()
        states = self.paper_states(outcomes)
        mine = [outcomes.get(j) for j in ws["jobs"]]
        t = self.control.totals()["since_start"]
        hist = [h for h in ws.get("history", []) if h.get("wave") != ws["wave"]]
        hist.append({"wave": ws["wave"], "jobs": len(ws["jobs"]),
                     "jobs_by_kind": dict(Counter(by_id[j]["stratum"] for j in ws["jobs"])),
                     "accepted": sum(x == "accepted" for x in mine),
                     "papers_active": ws["n_active"],
                     "papers_satisfied": sum(wv.satisfied(ps, self.goal) for ps in states.values()),
                     "gateway_calls_since_start": int(t.get("gateway_calls") or 0),
                     "cli_calls_since_start": int(t.get("cli_calls") or 0) + int(t.get("codex_calls") or 0),
                     "t": g.now_iso()})
        ws["history"] = hist
        ws["phase"] = "done"
        self.write_state("waves", "running", None)
        h = hist[-1]
        dr.notice("GATEWAY", f"run-waves set={self.label}: wave {ws['wave']} done: {h['jobs']} jobs, "
                  f"{h['accepted']} accepted; {h['papers_satisfied']} papers satisfied; "
                  f"{h['gateway_calls_since_start']} gateway / {h['cli_calls_since_start']} verifier calls so far")


# ------------------------------------------------------------------ CLI

def main(argv: list[str] | None = None, provenance: Callable[[], dict[str, Any]] | None = None,
         http_factory: Callable[[], Any] | None = None, cli_runner: Callable[..., Any] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--private-config", default=None, help=f"private JSON config (or env {g.CONFIG_ENV})")
    ap.add_argument("--out-dir", default=None, help="default: the config's out_dir")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def with_set(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument("--set", required=True)
        p.add_argument("--pilot", type=int, nargs="?", const=PILOT_DEFAULT_RECORDS, default=None, metavar="N",
                       help=f"the pilot batch (default {PILOT_DEFAULT_RECORDS} jobs): own files, records marked")
        p.add_argument("--papers", type=int, default=PILOT_DEFAULT_PAPERS, metavar="K",
                       help=f"with --pilot: how many papers (default {PILOT_DEFAULT_PAPERS})")
        return p

    pf = with_set(sub.add_parser("preflight", help="print and record the size notice"))
    pf.add_argument("--stage", required=True, choices=(*dr.STAGES, "waves"))
    for name in dr.STAGES:
        sp = with_set(sub.add_parser(name, help=f"the {name} stage (exit 75 paused, 76 budget stop)"))
        sp.add_argument("--notice-acknowledged", action="store_true")
        sp.add_argument("--resume", action="store_true")
        dr.add_clear_args(sp)
        if name == "draft":
            sp.add_argument("--allow-low-yield", action="store_true")
            sp.add_argument("--accept-low-yield", action="store_true")
    rw = with_set(sub.add_parser("run-waves", help="draft -> check -> verify in waves keyed on ACCEPTED records "
                                                   "(exit 75 paused, 76 budget stop)"))
    rw.add_argument("--notice-acknowledged", action="store_true")
    rw.add_argument("--resume", action="store_true")
    dr.add_clear_args(rw)
    rw.add_argument("--allow-low-yield", action="store_true")
    rw.add_argument("--accept-low-yield", action="store_true")
    with_set(sub.add_parser("check", help="layer 1 over every draft (no model)"))
    with_set(sub.add_parser("summary", help="accepted records + counts-only summary"))
    args = ap.parse_args(argv)
    cfg, cfg_sha = g.load_config(args.private_config)
    out = g.out_dir(cfg, args.out_dir)
    apool.corpus_config(cfg, args.set)
    run = AliasRun(cfg, cfg_sha, out, args.set, provenance, http_factory, cli_runner,
                   pilot=None if args.pilot is None else (args.pilot, args.papers))
    run.clear_hard_stop(getattr(args, "clear_hard_stop", None), getattr(args, "clear_note", None))
    if args.cmd == "preflight":
        run.preflight(args.stage)
        return EXIT_OK
    if args.cmd == "draft":
        return run.draft(args.resume, args.notice_acknowledged, args.allow_low_yield, args.accept_low_yield)
    if args.cmd == "verify":
        return run.verify(args.resume, args.notice_acknowledged)
    if args.cmd == "run-waves":
        return run.run_waves(args.resume, args.notice_acknowledged, args.allow_low_yield, args.accept_low_yield)
    if args.cmd == "check":
        return run.check()
    return run.summary()


if __name__ == "__main__":
    sys.exit(main())
