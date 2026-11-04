"""``questions``: n writer samples per passage, programmatic checks, lowest-echo pick.

Writer calls are OpenAI-compatible chat completions with ``stream: false`` and an explicit
``max_tokens`` on every request; ``extra_body`` keys (e.g. ``chat_template_kwargs``) are
merged into the request body. An optional ``api_key_file`` in the writer block is read at
call time and sent as ``Authorization: Bearer``; it is never logged or written.

Ledger ``questions.ledger.jsonl`` (append-only, one row per writer call, fsync'd): the key
is (passage_id, writer, i). Every row carries the passage's ``sha256`` (``passage_sha256``)
and the writer ``model``; a row whose sha256 or model differs from the current passage /
config is ignored (a re-cut passage or a model change starts that passage afresh). A rerun
skips keys that already have an ``ok`` row, so it resumes; failed calls are retried on the
next run, up to ``questions.max_attempts`` (3) attempts per call in total. A call that has
used up its attempts without an ``ok`` row is final ("exhausted"): selection then runs on
the passage's completed calls. A call whose reply is empty is retried once after
``empty_retry_s`` (30 s) inside the same attempt; still empty is a failure. Pause file and
failure stops are those of :mod:`runner`.

Checks (no model judging; :func:`check_candidate`):

- ``json``: the reply holds a JSON object with string ``question`` and ``answer_quote``;
- ``quote_substring``: the quote is an exact substring of the passage;
- ``question_len``: 25-400 chars; ``quote_len``: 10-300 chars (after strip);
- ``copied_run``: the question shares no run of 4+ consecutive words with the passage
  (words: split on whitespace, edge punctuation stripped, internal ``-`` and ``.`` kept,
  Unicode casefold; so "SARS-CoV-2" and "E. coli" stay single words and "β-lactamase"
  keeps its Greek letter);
- ``self_contained``: no "this/the study|paper|article|authors|text|passage", no
  "according to".

Selection: among passing candidates the lowest echo wins (echo = share of the question's
distinct words that occur in the passage); ties go to the lower sample index. Selection
runs for passages whose n calls are all final (ok, or exhausted); a passage with no passer
among its ok calls goes to ``skips.jsonl`` with its strata. Passages with a call still
open (attempts left, e.g. after a stop) are written to ``pending.jsonl`` with their strata,
so ``score`` counts them in the skip loss.

Outputs: ``questions.jsonl`` (main writer), ``questions.check.jsonl`` (check writer, on a
seeded ``fraction`` of passages), ``skips.jsonl`` / ``skips.check.jsonl``,
``pending.jsonl`` / ``pending.check.jsonl``,
``questions.summary.json`` (counts only).
"""
from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

from .common import iter_jsonl, log, read_jsonl, seeded_unit, write_json, write_jsonl
from .config import in_flight, out_path, pause_path, read_key, require
from .runner import Control, chat_url, prior_failures, run_pool

#: kept inside a word: hyphen-minus, Unicode hyphens, full stop ("SARS-CoV-2", "7.4", "E.coli")
_KEEP_INTERNAL = frozenset("-.\u2010\u2011")


def _is_punct(ch: str) -> bool:
    """Unicode punctuation (P*) or symbol (S*)."""
    return unicodedata.category(ch)[0] in "PS"


def _strip_edges(tok: str) -> str:
    a, b = 0, len(tok)
    while a < b and _is_punct(tok[a]):
        a += 1
    while b > a and _is_punct(tok[b - 1]):
        b -= 1
    return tok[a:b]


def _split_internal(tok: str) -> list[str]:
    """Split on internal punctuation/symbols other than ``-`` and ``.`` ("and/or")."""
    parts, cur = [], []
    for ch in tok:
        if _is_punct(ch) and ch not in _KEEP_INTERNAL:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


SELF_CONTAINED = re.compile(
    r"\b(?:this|the)\s+(?:study|paper|article|authors|text|passage)\b|\baccording\s+to\b",
    re.IGNORECASE)
LEDGER = "questions.ledger.jsonl"


def words(text: str) -> list[str]:
    """Whitespace-split words, Unicode casefolded, leading/trailing punctuation and symbols
    stripped; internal ``-`` and ``.`` kept, other internal punctuation splits.
    ``"SARS-CoV-2 spike,"`` -> ``["sars-cov-2", "spike"]``; ``"and/or"`` -> ``["and", "or"]``."""
    out = []
    for tok in (text or "").split():
        for piece in _split_internal(tok):
            w = _strip_edges(piece).casefold()
            if w:
                out.append(w)
    return out


def copied_run(question: str, passage: str, n: int = 4) -> bool:
    """True when the question shares a run of ``n``+ consecutive words with the passage."""
    q = words(question)
    p = words(passage)
    if len(q) < n or len(p) < n:
        return False
    grams = {tuple(p[i:i + n]) for i in range(len(p) - n + 1)}
    return any(tuple(q[i:i + n]) in grams for i in range(len(q) - n + 1))


def echo(question: str, passage: str) -> float:
    """Share of the question's distinct words that occur in the passage (0..1)."""
    q = set(words(question))
    if not q:
        return 1.0
    return len(q & set(words(passage))) / len(q)


def parse_reply(content: str | None) -> dict[str, Any] | None:
    """The first JSON object in the reply (code fences and prose around it tolerated)."""
    if not content:
        return None
    s = content.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", s):
        try:
            obj, _ = dec.raw_decode(s[m.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def check_candidate(obj: dict[str, Any] | None, passage: str, checks: dict[str, Any]) -> dict[str, Any]:
    """All checks on one parsed candidate. Returns ``{"pass", "failed": [...], "question",
    "answer_quote", "echo"}``; the quote is kept unstripped when it is a substring."""
    failed: list[str] = []
    q = obj.get("question") if isinstance(obj, dict) else None
    a = obj.get("answer_quote") if isinstance(obj, dict) else None
    if not isinstance(q, str) or not isinstance(a, str):
        return {"pass": False, "failed": ["json"], "question": None, "answer_quote": None, "echo": None}
    q = q.strip()
    a_stripped = a.strip()
    quote = a if (a and a in passage and a == a_stripped) else a_stripped
    if not quote or quote not in passage:
        failed.append("quote_substring")
    if not checks["question_min"] <= len(q) <= checks["question_max"]:
        failed.append("question_len")
    if not checks["quote_min"] <= len(quote) <= checks["quote_max"]:
        failed.append("quote_len")
    if copied_run(q, passage, int(checks["max_copied_run"]) + 1):
        failed.append("copied_run")
    if SELF_CONTAINED.search(q):
        failed.append("self_contained")
    return {"pass": not failed, "failed": failed, "question": q, "answer_quote": quote,
            "echo": round(echo(q, passage), 6)}


def select(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Lowest echo among passing candidates; ties to the lower sample index ``i``."""
    ok = [c for c in candidates if c.get("pass")]
    if not ok:
        return None
    return min(ok, key=lambda c: (c["echo"], c["i"]))


# ------------------------------------------------------------------- writer client


def request_body(wcfg: dict[str, Any], passage: str, prompt: dict[str, str]) -> dict[str, Any]:
    msgs = []
    if prompt.get("system"):
        msgs.append({"role": "system", "content": prompt["system"]})
    msgs.append({"role": "user", "content": prompt["user"].format(passage=passage)})
    body: dict[str, Any] = {"model": wcfg["model"], "messages": msgs, "stream": False,
                            "max_tokens": int(wcfg["max_tokens"])}
    if wcfg.get("temperature") is not None:
        body["temperature"] = wcfg["temperature"]
    if wcfg.get("top_p") is not None:
        body["top_p"] = wcfg["top_p"]
    for k, v in (wcfg.get("extra_body") or {}).items():
        if k not in ("stream", "max_tokens", "messages", "model"):
            body[k] = v
    return body


def reply_content(data: Any) -> str:
    try:
        msg = data["choices"][0]["message"]
        return (msg.get("content") or "").strip()
    except (KeyError, IndexError, TypeError, AttributeError):
        return ""


async def call_writer(http: Any, wcfg: dict[str, Any], body: dict[str, Any], control: Control) -> dict[str, Any]:
    """One logical writer call with the single empty-reply retry. Returns
    ``{"status", "content", "usage", "attempts"}``."""
    url = chat_url(wcfg["url"])
    attempts = 0
    while True:
        attempts += 1
        headers = {}
        key = read_key(wcfg.get("api_key_file"))
        if key:
            headers["Authorization"] = f"Bearer {key}"
        try:
            r = await http.post(url, json=body, headers=headers, timeout=float(wcfg.get("timeout_s") or 300))
        except Exception as e:  # noqa: BLE001
            return {"status": f"error:{type(e).__name__}", "attempts": attempts}
        if r.status_code != 200:
            return {"status": f"http_{r.status_code}", "attempts": attempts}
        try:
            data = r.json()
        except ValueError:
            data = None
        content = reply_content(data)
        usage = data.get("usage") if isinstance(data, dict) else None
        if content:
            return {"status": "ok", "content": content, "usage": usage, "attempts": attempts}
        if attempts >= 2:
            return {"status": "empty", "usage": usage, "attempts": attempts}
        await control.sleep(float(wcfg.get("empty_retry_s", 30)))


# ------------------------------------------------------------------- plan / select


def writer_sets(cfg: dict[str, Any], passages: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any], list[dict[str, Any]]]]:
    out = [("main", cfg["writer"], passages)]
    cw = cfg.get("check_writer")
    if isinstance(cw, dict) and cw.get("url") and cw.get("model"):
        frac = float(cw.get("fraction") or 0)
        seed = cfg["sample"]["seed"]
        chosen = [p for p in passages if seeded_unit(seed, "check", p["passage_id"]) < frac]
        out.append(("check", cw, chosen))
    return out


def max_attempts(cfg: dict[str, Any]) -> int:
    return max(1, int((cfg.get("questions") or {}).get("max_attempts") or 3))


def ledger_state(cfg: dict[str, Any], passages: list[dict[str, Any]]) -> tuple[
        dict[tuple[str, str, int], dict[str, Any]], dict[tuple[str, str, int], int]]:
    """``(ok rows, attempt counts)`` per (passage_id, writer, i), counting only rows whose
    ``passage_sha256`` and ``model`` match the current passage and writer config. Rows
    written before the sha256 was recorded have none and are ignored too."""
    sha = {p["passage_id"]: p["sha256"] for p in passages}
    models = {w: wcfg["model"] for w, wcfg, _ in writer_sets(cfg, passages)}
    ok: dict[tuple[str, str, int], dict[str, Any]] = {}
    attempts: dict[tuple[str, str, int], int] = defaultdict(int)
    for r in iter_jsonl(out_path(cfg, LEDGER)):
        if "passage_id" not in r:
            continue
        pid, w = r["passage_id"], r.get("writer")
        if r.get("passage_sha256") != sha.get(pid) or r.get("model") != models.get(w):
            continue
        key = (pid, w, int(r["i"]))
        attempts[key] += 1
        if r.get("status") == "ok":
            ok.setdefault(key, r)
    return ok, attempts


def done_keys(ledger: Path) -> set[tuple[str, str, int]]:
    """Keys with an ok row, unfiltered (kept for inspection; the run uses ledger_state)."""
    return {(r["passage_id"], r["writer"], int(r["i"])) for r in iter_jsonl(ledger)
            if r.get("status") == "ok" and "passage_id" in r}


def make_job(http: Any, writer: str, wcfg: dict[str, Any], passage: dict[str, Any], i: int,
             cfg: dict[str, Any], control: Control):
    async def job() -> dict[str, Any]:
        body = request_body(wcfg, passage["text"], cfg["prompt"])
        res = await call_writer(http, wcfg, body, control)
        row = {"passage_id": passage["passage_id"], "passage_sha256": passage["sha256"], "writer": writer,
               "model": wcfg["model"], "i": i, **res}
        if res["status"] == "ok":
            row["check"] = check_candidate(parse_reply(res["content"]), passage["text"], cfg["checks"])
        return row
    return job


def build_outputs(cfg: dict[str, Any], passages: list[dict[str, Any]]) -> dict[str, Any]:
    """Selection from the ledger. Pure over (ledger, passages, config)."""
    ok_rows, attempts = ledger_state(cfg, passages)
    limit = max_attempts(cfg)
    summary: dict[str, Any] = {}
    for writer, wcfg, plist in writer_sets(cfg, passages):
        n = int(wcfg["n"])
        chosen, skips, pending = [], [], []
        exhausted_calls = 0
        fail_counts: dict[str, int] = defaultdict(int)
        for p in plist:
            strata = {k: p.get(k) for k in ("journal_family", "length_band", "year_band", "section_type")}
            keys = [(p["passage_id"], writer, i) for i in range(n)]
            open_calls = [k for k in keys if k not in ok_rows and attempts.get(k, 0) < limit]
            if open_calls:
                pending.append({"passage_id": p["passage_id"], "pmcid": p["pmcid"], "writer": writer,
                                "open_calls": len(open_calls), **strata})
                continue
            exhausted = [k for k in keys if k not in ok_rows]
            exhausted_calls += len(exhausted)
            cands = []
            for k in keys:
                if k not in ok_rows:
                    continue
                c = dict(ok_rows[k]["check"])
                c["i"] = k[2]
                cands.append(c)
                for f in c["failed"]:
                    fail_counts[f] += 1
            best = select(cands)
            if best is None:
                skips.append({"passage_id": p["passage_id"], "pmcid": p["pmcid"], "writer": writer,
                              "n_candidates": len(cands), "n_exhausted": len(exhausted), **strata})
                continue
            if best["answer_quote"] not in p["text"]:  # cannot happen with the sha256 filter; be loud
                raise ValueError(f"{p['passage_id']}: selected quote is not in the passage text")
            qs = p["start"] + p["text"].index(best["answer_quote"])
            chosen.append({
                "question_id": p["passage_id"] if writer == "main" else f"{p['passage_id']}:check",
                "passage_id": p["passage_id"], "pmcid": p["pmcid"], "record_path": p["record_path"],
                "writer": writer, "model": wcfg["model"], "sample_i": best["i"],
                "question": best["question"], "answer_quote": best["answer_quote"],
                "quote_start": qs, "quote_end": qs + len(best["answer_quote"]),
                "echo": best["echo"], "n_passing": sum(1 for c in cands if c["pass"]), **strata})
        suffix = "" if writer == "main" else ".check"
        write_jsonl(out_path(cfg, f"questions{suffix}.jsonl"), chosen)
        write_jsonl(out_path(cfg, f"skips{suffix}.jsonl"), skips)
        write_jsonl(out_path(cfg, f"pending{suffix}.jsonl"), pending)
        summary[writer] = {"passages": len(plist), "selected": len(chosen), "skipped": len(skips),
                           "pending": len(pending), "exhausted_calls": exhausted_calls,
                           "max_attempts": limit, "check_failures": dict(sorted(fail_counts.items()))}
    write_json(out_path(cfg, "questions.summary.json"), summary)
    return summary


async def run_async(cfg: dict[str, Any], http: Any = None) -> tuple[int, Control]:
    require(cfg, "out_dir", "writer.url", "writer.model", "writer.n", "writer.max_tokens", "sample.seed")
    passages = read_jsonl(out_path(cfg, "passages.jsonl"))
    ledger = out_path(cfg, LEDGER)
    ok_rows, attempts = ledger_state(cfg, passages)
    limit = max_attempts(cfg)
    stop = cfg["stop"]
    control = Control(stop["max_failures"], stop["max_consecutive"], pause_path(cfg), prior_failures(ledger))
    own = http is None
    if own:
        import httpx
        http = httpx.AsyncClient()
    try:
        for writer, wcfg, plist in writer_sets(cfg, passages):
            jobs = (make_job(http, writer, wcfg, p, i, cfg, control)
                    for p in plist for i in range(int(wcfg["n"]))
                    if (p["passage_id"], writer, i) not in ok_rows
                    and attempts.get((p["passage_id"], writer, i), 0) < limit)
            started = await run_pool(jobs, control, in_flight(wcfg), ledger)
            log(f"questions[{writer}]: {started} calls this run")
            if control.reason:
                break
    finally:
        if own:
            await http.aclose()
    summary = build_outputs(cfg, passages)
    log(f"questions: {json.dumps({k: {kk: v[kk] for kk in ('selected', 'skipped', 'pending')} for k, v in summary.items()})}")
    return (75 if control.reason else 0), control


def run(cfg: dict[str, Any]) -> int:
    code, control = asyncio.run(run_async(cfg))
    if control.reason:
        log(f"questions: stopped ({control.reason}); rerun to resume")
    return code
