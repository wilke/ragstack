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

Every subcommand takes ``--pilot [N] [--papers K]`` (default 24 jobs over 3 papers): K
papers drawn by ``random.Random("<pool seed>|pilot")`` from the pool's eval papers, their
first N jobs in drafting order; own files (``*-alias-<set>-pilot*``), records marked
``pilot: true``.

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

**Records.** ``accepted-alias-<set>.jsonl`` (PRIVATE): ``record_id, kind (G|C), question,
answer, quote, alias, primary, alias_kind, paper, doc_id, source_chunk_id, gold_chunks
(the quote-bearing chunk ids), binding_terms ([primary]), flags, pilot, model,
prompt_sha256, verifier, verifier_refused, verifier_model``.

**Private config** (template: ``python/scripts/eval/examples/glossbench-alias-config.example.json``)::

    out_dir                    private output directory (never inside the repo)
    corpora.<set>              {chunks: [paths], paper_key, alias_glossary, expected_chunks}
    alias_pool                 the pool builder's block (glossbench_alias_pool.DEFAULTS)
    alias                      {goal: {G, C}}  (default: the pool header's goal)
    alias_checks               layer-1 thresholds (glossbench_alias_checks.CHECK_DEFAULTS)
    drafter, verifier, limits, yield_watch, prereg   as round 3 (glossbench_r2_draft.DEFAULTS)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
from collections import Counter
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
    redraw_exempt = ak.REDRAW_EXEMPT

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.label = apool.label(self.set) + ("" if self.pilot is None else "-pilot")
        self.p = dr.Paths(self.out, self.label)
        self.budget_event: dict[str, Any] | None = None
        unknown = set(self.raw_cfg.get("alias_checks") or {}) - CHECK_KEYS
        if unknown:
            raise SystemExit(f"config block 'alias_checks': unknown keys {sorted(unknown)}")
        unknown = set(self.raw_cfg.get("alias") or {}) - {"goal"}
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
            papers = sorted({r["paper"] for r in jobs})
            pick = set(random.Random(f"{self.header['seed']}|pilot").sample(papers, min(k, len(papers))))
            jobs = [r for r in jobs if r["paper"] in pick][:n]
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

    # -------------------------------------------------------- draft
    def _on_budget(self, ev: dict[str, Any]) -> None:
        if self.budget_event is None:
            self.budget_event = ev

    def finish(self, stage: str, body: Callable[[], None]) -> int:
        rc = super().finish(stage, body)
        if rc == EXIT_PAUSED and self.budget_event is not None:
            ev = {**self.budget_event, "set": self.set, "label": self.label,
                  "totals": self.control.totals()["since_start"]}
            g.write_json_atomic(self.out / f"budget-stop-{self.label}.json", ev)
            self.p.pause.write_text(f"budget stop {ev['t']}: HTTP {ev['http_status']} ({ev['why']}); "
                                    f"see budget-stop-{self.label}.json; remove this file, run preflight, "
                                    "then draft --resume\n", encoding="utf-8")
            dr.notice("GATEWAY", f"{stage} set={self.label} BUDGET STOP (HTTP {ev['http_status']}, {ev['why']}); "
                      "PAUSE written; exit 76")
            return EXIT_BUDGET
        return rc

    def _draft_body(self) -> None:
        _ = self.corpus
        d = self.cfg["drafter"]
        if not d.get("base_url"):
            raise p4.HardStop("drafter.base_url missing from the private config")
        url = str(d["base_url"]).rstrip("/")
        if url.endswith("/v1"):
            url = url[: -len("/v1")]
        gw = AliasGateway(url, self.control, {**self.cfg["limits"], "empty_retries": 1,
                                              "empty_backoff_s": float(d["empty_retry_s"])}, self.p.calls,
                          on_budget=self._on_budget)
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

        async def go(todo: list[dict[str, Any]]) -> None:
            http = self.http_factory()
            try:
                await dr.run_jobs([job(r) for r in todo], int(d["inflight"]), self.control, http)
            finally:
                close = getattr(http, "aclose", None)
                if close is not None:
                    await close()

        self.watch_yield(st)
        if self.control.stopped():
            raise p4.Pause(self.control.reason or "stopped")
        for wave in sorted({r["wave"] for r in self.sampled}):
            todo = self.wave_todo(st, wave)
            if todo:
                dr.notice("GATEWAY", f"draft set={self.label}: wave {wave}, {len(todo)} jobs "
                          f"(<= {d['inflight']} in flight, model {d['model']})")
            for _ in range(int(d["passes"])):
                if not todo:
                    break
                asyncio.run(go(todo))
                if self.control.stopped():
                    raise p4.Pause(self.control.reason or "stopped")
                todo = self.wave_todo(st, wave)
            if todo:
                raise p4.Pause(f"calls_failed:{len(todo)}")
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
               "http_attempts": res.get("attempts"), "prompt_sha256": g.sha_text(json.dumps(msgs)), "t": g.now_iso()}
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
                "verifier": (r["l2"].get("verifier") or "claude") if r.get("l2") else None,
                "verifier_refused": (r.get("l2") or {}).get("verifier_refused"),
                "verifier_model": (r.get("l2") or {}).get("served_model"),
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
        }
        g.write_json_atomic(self.p.summary, s)
        return EXIT_OK


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
    pf.add_argument("--stage", required=True, choices=dr.STAGES)
    for name in dr.STAGES:
        sp = with_set(sub.add_parser(name, help=f"the {name} stage (exit 75 paused, 76 budget stop)"))
        sp.add_argument("--notice-acknowledged", action="store_true")
        sp.add_argument("--resume", action="store_true")
        if name == "draft":
            sp.add_argument("--allow-low-yield", action="store_true")
            sp.add_argument("--accept-low-yield", action="store_true")
    with_set(sub.add_parser("check", help="layer 1 over every draft (no model)"))
    with_set(sub.add_parser("summary", help="accepted records + counts-only summary"))
    args = ap.parse_args(argv)
    cfg, cfg_sha = g.load_config(args.private_config)
    out = g.out_dir(cfg, args.out_dir)
    apool.corpus_config(cfg, args.set)
    run = AliasRun(cfg, cfg_sha, out, args.set, provenance, http_factory, cli_runner,
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
    return run.summary()


if __name__ == "__main__":
    sys.exit(main())
