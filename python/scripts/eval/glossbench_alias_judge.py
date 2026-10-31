#!/usr/bin/env python
"""Alias study: the ANSWER-JUDGE calibration. Can a local Qwen judge, sampled ``n`` times
with a majority vote, replace Opus as the judge of "does this passage answer this question"?

Subcommands (``--config CFG`` first; every output goes to the config's private ``out_dir``)::

    build-calib                          the calibration set (offline: no model, no index)
    preflight --backend B [--reps R]     print + log the size notice for one backend
    label --backend opus --reps 3 --notice-acknowledged [--resume]   the partner gateway
    label --backend cli --notice-acknowledged [--resume]              the Claude CLI
    label --backend qwen --notice-acknowledged [--resume]             local Qwen, n samples
    analyse                              agreement, the n-curve, calibration, the cascade
    overlap --main-drafts PATH           round 2: calibration records whose id is in the study log
    analyse-variants                     round 2: every ``analysis.variants`` Qwen label set
    analyse-gate                         validation: the frozen C + E agreement rule (``gate`` block)
    label --backend gateway --notice-acknowledged [--resume]          any gateway model (``judge_gw``)
    tiebreak-subset                      tiebreaker: the self-consistency subset (TIEBREAKER-PLAN.md)
    analyse-tiebreaker                   tiebreaker: each gateway judge vs Opus, refusals, the selection

**Round 2** (a config with ``calib_dir`` = round 1's directory, read for ``pairs.jsonl`` and
``labels-opus.jsonl``, optionally pinned by ``pairs_sha256``): ``qwen.variant`` names a Qwen
configuration (files ``labels-qwen-<variant>.jsonl`` etc.), ``qwen.judge_version`` picks the
system prompt (:data:`JUDGE_SYSTEMS`: ``alias-judge/2`` is /1 plus :data:`JUDGE_RULE_V2`) and
``qwen.enable_thinking`` the thinking switch (:data:`THINKING_SWITCH`). Each stored row carries
its prompt version, system sha, thinking flag, reasoning tokens and endpoint (``qwen.base_url``).

**Round 3** (another model on another endpoint): ``qwen.base_url`` / ``qwen.model`` name it, and
``qwen.require_served_model`` puts the endpoint in the plan (so a preflight for one server does
not admit a run on another) and stops the run, storing nothing, on a reply whose served model
id is not ``qwen.model``.

**Validation** (JUDGE-VALIDATION-PLAN.md): ``calib.records: "accepted"`` builds the set from
records with a layer-2 pass only; ``analyse-gate`` scores the frozen rule (``gate.c_labels`` /
``gate.e_labels``: judge C's majority of its first ``c_n`` samples and judge E's first ``e_n``
must agree, else the pair is escalated to the Opus reference) on the Opus-unanimous pairs, with
and without ``coll_diff``, plus each judge alone and the split / no-reference pairs.

**The judge task** (the study rule). Given a QUESTION (phrased with a primary TERM) and a
PASSAGE (from any paper), does the passage answer the question? A passage from another paper
counts if it answers it; a short form (abbreviation or symbol) in the passage counts only if it
means the TERM in that passage's own paper. Output ``{"reason": ..., "answers": "yes"|"no"}``.

**One prompt for every judge** (:data:`JUDGE_VERSION` ``alias-judge/1``: :data:`JUDGE_SYSTEM`
and :func:`judge_prompt`). The passage's context is its paper's title and the short forms
the PASSAGE'S OWN paper defines (its merged-glossary entries whose alias occurs in the
passage, :func:`alias_abbrev.alias_occurs`, sorted, at most :data:`MAX_DEFS`). Nothing is
keyed on the question: the question's paper's glossary is never shown, so a collision (the
same short form meaning something else in another paper) is visible only through the
passage paper's own definition, as it would be to a reader of that paper.

**Calibration set** (``build-calib``): only PILOT records (each ``pilots[]`` entry is one
pilot's draft + acceptance logs; records with a layer-1 pass; ``out/main`` is refused). Per
question, seeded (``calib.seed``): the gold chunk (the record's source chunk); a local BM25
over the chunk export (question text as the query, :class:`BM25`) gives ``lex_same`` (the top
non-gold chunk of the question's paper) and ``lex_other`` (the top chunks of other papers, one
per paper); ``coll_diff`` / ``coll_same``: chunks of OTHER papers holding the record's alias
(:func:`alias_abbrev.alias_occurs`) whose paper's glossary maps that alias to a primary with
disjoint / overlapping content words (the rule of :func:`alias_abbrev.collision`), ranked by
BM25, one per paper; a paper with no glossary entry for the alias is not used. Strata per
question: ``calib.per_question`` (default gold 1, lex_same 1, lex_other 2, collisions 2: one
of each meaning when both exist, else two of the one that does).

**Backends.**

* ``opus`` — the partner gateway (``gateway`` block: ``base_url``, ``model``) through
  :class:`glossbench_alias_draft.AliasGateway`: ``fallback_model: ""`` and ``max_tokens`` in
  every body, temperature omitted (the model's default) unless set, the drafter's budget-stop
  classifier (``provider_notice`` at any status, HTTP 429, an untyped notice body, three
  untyped 503s in a row -> exit 76 with ``PAUSE`` and ``budget-stop-judge.json``), the
  part-4 failure pauses (5 in a row / 20 in total), the cumulative caps (``limits``). ``reps``
  labels per pair (keys ``opus|<pair>|r<i>``). At most 4 in flight.
* ``cli`` — the local Claude CLI exactly as the alias verifier calls it
  (:meth:`glossbench_r2_draft.Run.call_cli`: ``--restricted --strict-mcp-config --tools ""
  --no-session-persistence``, prompt on stdin, an empty cwd with no CLAUDE.md above), every
  call isolation-checked; a safeguard stop answered by another model (#891,
  :func:`glossbench_r2_draft.cli_usage` ``fallback``) is recorded ``refused`` and has no
  verdict; a usage limit stops the run.
* ``qwen`` — the OpenAI-compatible endpoint (``qwen`` block): ONE request per pair with ``n``
  samples at ``temperature`` (0.7), every sample's verdict stored, the served model id
  recorded; ``enable_thinking`` is passed through ``chat_template_kwargs``.

**Controls (every backend).** A ``PAUSE`` file in ``out_dir`` stops issuing (in-flight calls
finish), exit 75; ``--resume`` continues without re-issuing a stored key. ``INFLIGHT`` in
``out_dir`` (an integer, re-read before every admission, capped at 4) lowers concurrency
mid-run. The notice gate: ``preflight`` writes ``preflight-<backend>.json`` and a
``<KIND>-NOTICE`` line to ``LOG``; ``label`` refuses without ``--notice-acknowledged`` and a
preflight of the same plan.

**Analysis** (``analyse``): the Opus reference is the majority of its ``reps`` labels;
pairs where Opus is not unanimous are reported separately. For every odd ``n`` up to the
samples stored, ``bootstrap`` draws (1,000) of an ``n``-subsample WITHOUT replacement per
pair (a hypergeometric draw of the yes count from the pair's valid samples): majority-vote
accuracy and Cohen's kappa against the reference, per stratum; the plateau is the smallest
``n`` whose mean accuracy is within ``plateau_eps`` (0.005) of the largest ``n``'s. The vote
share is the confidence: a reliability table, and the cascade (accept Qwen when the share is
>= tau or <= 1 - tau, escalate the rest to Opus) for tau in 0.6/0.7/0.8/0.9.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import re
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import _glossbench_r2 as g  # noqa: E402
import alias_abbrev as aa  # noqa: E402
import deployed_docs_part4 as p4  # noqa: E402
import glossbench_alias_draft as ad  # noqa: E402
import glossbench_r2_draft as dr  # noqa: E402

EXIT_OK, EXIT_HARD_STOP, EXIT_PAUSED, EXIT_BUDGET = 0, 1, 75, ad.EXIT_BUDGET
BACKENDS = ("opus", "cli", "qwen", "gateway")
EMPTY = ("empty", "empty0")
STRATA = ("gold", "lex_same", "lex_other", "coll_same", "coll_diff")
CALIB_SCHEMA = "alias-judge-calib/1"
CALIB_RECORDS = ("l1_pass", "accepted")
JUDGE_VERSION = "alias-judge/1"
MAX_DEFS = 25
MAX_INFLIGHT = g.MAX_INFLIGHT  # 4

JUDGE_SYSTEM = """You judge whether a PASSAGE from a scientific paper answers a QUESTION. Judge only from the text you are given, never from outside knowledge of any specific paper.

Rules:
- The QUESTION is phrased with a TERM. The passage may come from ANY paper, not necessarily the paper the question was written from. If it answers the question, it counts.
- The passage may use a short form (an abbreviation or a symbol) instead of the TERM. A short form counts as the TERM only if, in the passage's own paper, it stands for the TERM (the same concept). Use the passage paper's definitions given with the passage; when the passage or its definitions show that the short form stands for something else, it is NOT the TERM. The same short form often means different things in different papers.
- "yes": the passage states the information the question asks for, about the TERM (or a short form that means the TERM in that paper), under the conditions the question names (organism, population, material, method, setting). A passage that states the core of what is asked is "yes" even if it adds or omits details.
- "no": the passage does not state the asked-for information; it only mentions the term, the topic or related facts; it is about a different concept that shares a short form; or it concerns conditions that contradict those the question names.

Answer with a JSON object only, exactly these keys, reason first:
{"reason": "at most 25 words", "answers": "yes" | "no"}"""

#: alias-judge/2 (round 2 of the calibration): /1 plus ONE rule, inserted after the short-form
#: rule. Round 1 found Qwen's errors were mostly confident false "no"s on G pairs: a passage
#: using a short form its paper defines as the TERM, judged as not about the TERM.
JUDGE_RULE_V2 = ("- If the passage uses a short form (an abbreviation or a symbol) that its paper defines as the "
                 "question's TERM, treat the short form as the TERM itself.")
_V2_ANCHOR = '- "yes": the passage states'
JUDGE_SYSTEM_V2 = JUDGE_SYSTEM.replace(_V2_ANCHOR, JUDGE_RULE_V2 + "\n" + _V2_ANCHOR, 1)
JUDGE_SYSTEMS: dict[str, str] = {"alias-judge/1": JUDGE_SYSTEM, "alias-judge/2": JUDGE_SYSTEM_V2}
#: how thinking is switched for Qwen3 on vLLM: the chat template's ``enable_thinking`` flag,
#: sent as ``chat_template_kwargs``; the server returns the thinking in ``message.reasoning``
#: and counts it in ``usage.completion_tokens_details.reasoning_tokens``.
THINKING_SWITCH = "chat_template_kwargs.enable_thinking"


def definitions_for(passage: str, entries: Sequence[Mapping[str, Any]]) -> tuple[list[tuple[str, str]], int]:
    """The passage paper's glossary entries whose alias occurs in the passage: sorted
    (alias, primary) pairs, at most :data:`MAX_DEFS`, and how many were cut."""
    seen: set[tuple[str, str]] = set()
    for e in entries:
        a, p = str(e.get("alias") or ""), str(e.get("primary") or "")
        if a and p and aa.alias_occurs(a, str(e.get("kind") or "abbreviation"), passage):
            seen.add((a, p))
    out = sorted(seen, key=lambda x: (x[0].casefold(), x[0], x[1]))
    return out[:MAX_DEFS], max(0, len(out) - MAX_DEFS)


def judge_prompt(question: str, term: str, passage: str, title: str,
                 defs: Sequence[tuple[str, str]]) -> str:
    """The judge's user message (identical for every backend)."""
    if defs:
        dl = "\n".join(f'- "{a}" stands for "{p}"' for a, p in defs)
    else:
        dl = "(none listed)"
    return (f"PASSAGE'S PAPER TITLE: {title or '(unknown)'}\n"
            f"SHORT FORMS THE PASSAGE'S PAPER DEFINES (those occurring in the passage):\n{dl}\n\n"
            f"PASSAGE:\n<<<\n{passage}\n>>>\n\n"
            f"TERM: {term}\n"
            f"QUESTION: {question}\n\n"
            "Does the passage answer the question? Answer with the JSON object only.")


_JSON_OBJ = re.compile(r"\{[^{}]*\}", re.S)


def parse_judgement(text: str) -> tuple[bool | None, str]:
    """``(True|False|None, reason)`` from a judge reply: the LAST JSON object carrying an
    ``answers`` key of yes/no (case-insensitive); fences and surrounding prose tolerated."""
    t = (text or "").strip()
    t = re.sub(r"<think>.*?</think>", "", t, flags=re.S)
    cands = []
    try:
        cands.append(json.loads(t.strip().strip("`").removeprefix("json").strip()))
    except ValueError:
        pass
    for m in _JSON_OBJ.finditer(t):
        try:
            cands.append(json.loads(m.group(0)))
        except ValueError:
            continue
    for obj in reversed(cands):
        if isinstance(obj, dict) and isinstance(obj.get("answers"), str):
            a = obj["answers"].strip().casefold()
            if a in ("yes", "no"):
                return a == "yes", str(obj.get("reason") or "")[:300]
    return None, ""


# ------------------------------------------------------------------ config

DEFAULTS: dict[str, Any] = {
    "calib": {"seed": "20261031|judge-calib", "per_question": {"gold": 1, "lex_same": 1, "lex_other": 2,
                                                                "collision": 2},
              "bm25_k1": 1.2, "bm25_b": 0.75, "bm25_top": 200,
              # which records become questions: "l1_pass" (round 1: every layer-1 pass) or
              # "accepted" (the validation set: a layer-1 AND a layer-2 pass, as the study uses)
              "records": "l1_pass"},
    "gateway": {"base_url": None, "model": None, "tag": "opus", "max_tokens": 2048, "temperature": None,
                "inflight": 4, "empty_retry_s": 2.0},
    "cli": {"model": "claude-opus-5-5", "cli": "claude", "inflight": 3, "timeout_s": 300, "cwd": None,
            "isolation_check": True, "max_calls": 1000, "max_cost_usd": 60.0},
    "qwen": {"base_url": None, "model": None, "n": 100, "temperature": 0.7, "max_tokens": 200,
             "enable_thinking": False, "inflight": 4, "timeout_s": 900, "judge_version": JUDGE_VERSION,
             "variant": None,
             # round 3: refuse a reply whose served model id is not ``model`` (a shared endpoint
             # can be redeployed under another model between preflight and run)
             "require_served_model": False},
    "limits": {**dr.DEFAULTS["limits"], "input_token_cap": 3_000_000, "output_token_cap": 600_000,
               "call_cap": 1500},
    "analysis": {"bootstrap": 1000, "seed": 20261031, "plateau_eps": 0.005,
                 "taus": [0.6, 0.7, 0.8, 0.9], "cascade_n": [11, 21, 0],
                 # analyse-variants: [{"name", "labels"}] Qwen label files scored against the
                 # Opus reference of ``calib_dir``; the n values and taus it reports
                 "variants": [], "report_n": [1, 3, 11, 21], "variant_taus": [0.8, 0.9]},
    # backend ``gateway`` (TIEBREAKER-PLAN.md): any model on the partner gateway as a judge, one
    # model per config. ``tag`` names the files (``labels-gw-<tag>.jsonl``); ``served_models``
    # lists the reply ``model`` ids accepted for ``model`` (default: ``model`` itself) and any
    # other id stops the run, storing nothing; every pair is labelled once (rep 1) and the pairs
    # of ``subset_file`` (one pair id per line, pinned by ``subset_sha256``) ``subset_reps`` times
    "judge_gw": {"base_url": None, "model": None, "tag": None, "served_models": None,
                 "judge_version": "alias-judge/2", "max_tokens": 4096, "temperature": None, "inflight": 4,
                 "empty_retry_s": 2.0, "subset_file": None, "subset_sha256": None, "subset_reps": 3},
    # tiebreak-subset / analyse-tiebreaker (TIEBREAKER-PLAN.md)
    "tiebreak": {"subset_seed": "20261031|tiebreaker|selfcons", "subset_unanimous": 100, "labels": [],
                 "max_filtered_refusal": 0.05, "plan_commit": None},
    # analyse-gate (JUDGE-VALIDATION-PLAN.md): the frozen two-judge agreement rule
    "gate": {"c_labels": None, "e_labels": None, "c_n": 5, "e_n": 1, "e_report_n": 3,
             "min_accuracy": 0.98, "max_escalation": 0.05, "plan_commit": None},
}


def merged(cfg: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in DEFAULTS.items():
        given = cfg.get(k) or {}
        unknown = set(given) - set(v)
        if unknown:
            raise SystemExit(f"config block {k!r}: unknown keys {sorted(unknown)}")
        out[k] = {**v, **given}
    if out["calib"]["records"] not in CALIB_RECORDS:
        raise SystemExit(f"calib.records {out['calib']['records']!r}: one of {list(CALIB_RECORDS)}")
    if out["judge_gw"]["judge_version"] not in JUDGE_SYSTEMS:
        raise SystemExit(f"judge_gw.judge_version {out['judge_gw']['judge_version']!r}: one of {sorted(JUDGE_SYSTEMS)}")
    t = out["judge_gw"]["tag"]
    if t is not None and not re.fullmatch(r"[A-Za-z0-9_]+", str(t)):
        raise SystemExit(f"judge_gw.tag {t!r}: letters, digits and _ only")
    if out["qwen"]["judge_version"] not in JUDGE_SYSTEMS:
        raise SystemExit(f"qwen.judge_version {out['qwen']['judge_version']!r}: one of {sorted(JUDGE_SYSTEMS)}")
    v = out["qwen"]["variant"]
    if v is not None and not re.fullmatch(r"[A-Za-z0-9_]+", str(v)):
        raise SystemExit(f"qwen.variant {v!r}: letters, digits and _ only")
    for blk in ("gateway", "cli", "qwen", "judge_gw"):
        n = int(out[blk]["inflight"])
        if not 1 <= n <= MAX_INFLIGHT:
            raise SystemExit(f"{blk}.inflight = {n}: at most {MAX_INFLIGHT} in flight")
    return out


# ------------------------------------------------------------------ BM25

_BM_TOK = re.compile(r"[a-z0-9]+")
_BM_STOP = frozenset("""a an the of in on at to for from by with and or as is are was were be been being this that
these those which what how does do did it its into than then their there such can may might will would should
could not no between among about after before during under over within without per via""".split())


def bm_tokens(text: str) -> list[str]:
    return [w for w in _BM_TOK.findall(text.lower()) if w not in _BM_STOP and len(w) > 1]


class BM25:
    """A small Okapi BM25 over (id, text) documents."""

    def __init__(self, docs: Sequence[tuple[str, str]], k1: float = 1.2, b: float = 0.75) -> None:
        self.ids = [d for d, _ in docs]
        self.k1, self.b = k1, b
        self.post: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.dl: list[int] = []
        for i, (_, text) in enumerate(docs):
            toks = bm_tokens(text)
            self.dl.append(len(toks))
            for w, c in Counter(toks).items():
                self.post[w].append((i, c))
        self.n = len(docs)
        self.avg = (sum(self.dl) / self.n) if self.n else 0.0

    def idf(self, w: str) -> float:
        df = len(self.post.get(w, ()))
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5))

    def scores(self, query: str) -> dict[str, float]:
        acc: dict[int, float] = defaultdict(float)
        for w in set(bm_tokens(query)):
            idf = self.idf(w)
            for i, c in self.post.get(w, ()):
                norm = self.k1 * (1 - self.b + self.b * self.dl[i] / (self.avg or 1))
                acc[i] += idf * c * (self.k1 + 1) / (c + norm)
        return {self.ids[i]: s for i, s in acc.items()}

    def top(self, query: str, k: int) -> list[tuple[str, float]]:
        s = self.scores(query)
        return sorted(s.items(), key=lambda x: (-x[1], x[0]))[:k]


# ------------------------------------------------------------------ calibration set

def meaning(primary_a: str, primary_b: str) -> str:
    """``same`` when the primaries share a content word (>= 5 chars) or, lacking any, a word;
    else ``diff``: the rule of :func:`alias_abbrev.collision`."""
    def key(p: str) -> set[str]:
        pw = aa.words(p)
        return aa.content5(pw) or set(pw)
    return "same" if key(primary_a) & key(primary_b) else "diff"


def load_pilot_records(pilots: Sequence[Mapping[str, Any]], records: str = "l1_pass") -> list[dict[str, Any]]:
    """Records with a layer-1 pass from each pilot's draft + acceptance logs (never main);
    with ``records="accepted"`` only those whose layer-2 verdict is also ``pass``."""
    if records not in CALIB_RECORDS:
        raise SystemExit(f"records {records!r}: one of {list(CALIB_RECORDS)}")
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for pi, p in enumerate(pilots):
        for key in ("drafts", "acceptance"):
            path = Path(p[key])
            if "main" in path.parts:
                raise SystemExit(f"pilots[{pi}].{key}: {path} is under a 'main' directory (study records)")
        drafts = {r["record_id"]: r for r in p4.read_jsonl_repair_tail(Path(p["drafts"]))}
        log = p4.read_jsonl_repair_tail(Path(p["acceptance"]))
        l2 = {r["record_id"]: r["verdict"] for r in log if r.get("layer") == "l2"}
        for r in log:
            if r.get("layer") != "l1" or r.get("verdict") != "pass":
                continue
            d = drafts.get(r["record_id"])
            if d is None or not d.get("draft"):
                continue
            if records == "accepted" and l2.get(r["record_id"]) != "pass":
                continue
            q = d["draft"]["question"]
            k = (d["paper"], g.gold_norm(q))
            if k in seen:
                continue
            seen.add(k)
            out.append({"record_id": d["record_id"], "pilot": p.get("name", str(pi)), "kind": d["stratum"],
                        "question": q, "primary": d["primary"], "alias": d["alias"],
                        "alias_kind": d.get("alias_kind") or "abbreviation", "paper": d["paper"],
                        "source_chunk_id": d["chunk_id"], "gold_chunk_ids": r.get("gold_chunk_ids") or [d["chunk_id"]],
                        "l2": l2.get(r["record_id"])})
    return sorted(out, key=lambda x: x["record_id"])


def read_glossary(path: Path) -> dict[str, list[dict[str, Any]]]:
    by_paper: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in p4.read_jsonl_repair_tail(path):
        if "alias" not in e:
            continue
        by_paper[str(e["paper"])].append(e)
    return dict(by_paper)


def alias_kind_for_match(kind: str) -> str:
    return "symbol" if kind == "symbol" else "abbreviation"


def build_calib(records: Sequence[Mapping[str, Any]], chunks: Mapping[str, Any], paper_of: Mapping[str, str],
                glossary: Mapping[str, Sequence[Mapping[str, Any]]], cfg: Mapping[str, Any]
                ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The pairs (module docstring). ``chunks``: chunk id -> object with ``content``;
    ``paper_of``: chunk id -> paper. Returns (pairs, counts)."""
    c = cfg["calib"]
    per = {**DEFAULTS["calib"]["per_question"], **(c.get("per_question") or {})}
    ids = sorted(chunks)
    bm = BM25([(i, chunks[i].content) for i in ids], float(c["bm25_k1"]), float(c["bm25_b"]))
    tok_index: dict[str, set[str]] = defaultdict(set)
    for i in ids:
        for t in set(aa.TOK.findall(aa.canonical_text(chunks[i].content))):
            tok_index[t].add(i)
    pairs: list[dict[str, Any]] = []
    short: Counter[str] = Counter()
    for rec in records:
        salt = f"{c['seed']}|{rec['record_id']}"

        def tie(cid: str, salt: str = salt) -> str:      # a seeded tie-break independent of hash order
            return g.sha_text(f"{salt}|{cid}")

        gold = set(rec["gold_chunk_ids"]) | {rec["source_chunk_id"]}
        used: set[str] = set()

        def add(cid: str, stratum: str, extra: dict[str, Any] | None = None, rec: Mapping[str, Any] = rec,
                used: set[str] = used) -> None:
            used.add(cid)
            pairs.append({"pair_id": g.sha_text(f"{rec['record_id']}|{cid}")[:16], "record_id": rec["record_id"],
                          "pilot": rec["pilot"], "record_kind": rec["kind"], "stratum": stratum,
                          "question": rec["question"], "term": rec["primary"], "alias": rec["alias"],
                          "alias_kind": rec["alias_kind"], "q_paper": rec["paper"], "chunk_id": cid,
                          "c_paper": paper_of[cid], "content_sha256": g.sha_text(chunks[cid].content),
                          "l2": rec.get("l2"), **(extra or {})})

        if per["gold"]:
            add(rec["source_chunk_id"], "gold")
        scores = bm.scores(rec["question"])
        ranked = sorted(scores.items(), key=lambda x: (-x[1], tie(x[0])))[: int(c["bm25_top"])]
        same = [cid for cid, _ in ranked if paper_of[cid] == rec["paper"] and cid not in gold]
        for cid in same[: int(per["lex_same"])]:
            add(cid, "lex_same", {"bm25": round(scores[cid], 3)})
        short["lex_same"] += max(0, int(per["lex_same"]) - len(same))
        papers_used = {rec["paper"]}
        n_other = 0
        for cid, s in ranked:
            if n_other >= int(per["lex_other"]):
                break
            if paper_of[cid] in papers_used or cid in used:
                continue
            papers_used.add(paper_of[cid])
            add(cid, "lex_other", {"bm25": round(s, 3)})
            n_other += 1
        short["lex_other"] += int(per["lex_other"]) - n_other
        # collisions: other papers holding the alias, whose glossary gives the alias a meaning
        akind = alias_kind_for_match(rec["alias_kind"])
        cand = set().union(*(tok_index.get(t, set()) for t in aa.alias_tokens(rec["alias"], akind)))
        by_class: dict[str, list[tuple[float, float, str, str]]] = {"same": [], "diff": []}
        for cid in cand:
            pp = paper_of[cid]
            if pp == rec["paper"] or cid in used:
                continue
            prim = [e["primary"] for e in glossary.get(pp, ()) if e.get("alias") == rec["alias"]]
            if not prim:
                continue
            cls = "same" if any(meaning(rec["primary"], p) == "same" for p in prim) else "diff"
            by_class[cls].append((-scores.get(cid, 0.0), tie(cid), cid, prim[0]))
        picked: dict[str, list[tuple[str, str]]] = {}
        for cls in ("diff", "same"):
            seenp: set[str] = set()
            lst = []
            for _, _, cid, prim in sorted(by_class[cls]):
                if paper_of[cid] in seenp or paper_of[cid] in papers_used:
                    continue
                seenp.add(paper_of[cid])
                lst.append((cid, prim))
            picked[cls] = lst
        k = int(per["collision"])
        ns = min(len(picked["same"]), k // 2)
        nd = min(len(picked["diff"]), k - ns)
        ns = min(len(picked["same"]), k - nd)
        for cls, n in (("diff", nd), ("same", ns)):
            for cid, prim in picked[cls][:n]:
                papers_used.add(paper_of[cid])
                add(cid, f"coll_{cls}", {"c_primary": prim, "bm25": round(scores.get(cid, 0.0), 3)})
        short["collision"] += k - nd - ns
    order = random.Random(f"{c['seed']}|order")
    order.shuffle(pairs)
    counts = {"pairs": len(pairs), "questions": len(records),
              "by_stratum": dict(Counter(p["stratum"] for p in pairs)),
              "by_record_kind": dict(Counter(p["record_kind"] for p in pairs)),
              "by_pilot": dict(Counter(p["pilot"] for p in pairs)),
              "short_of_target": dict(short),
              "records_by_l2": dict(Counter(str(r.get("l2")) for r in records))}
    return pairs, counts


# ------------------------------------------------------------------ the shared context

def calib_dir(cfg: Mapping[str, Any]) -> Path:
    """Where the frozen set (``pairs.jsonl``) and the Opus reference (``labels-opus.jsonl``)
    live: ``calib_dir`` when set (a later round re-uses round 1's set and reference without
    copying them), else ``out_dir``."""
    return Path(cfg.get("calib_dir") or cfg["out_dir"])


def check_pairs_sha(cfg: Mapping[str, Any], path: Path) -> None:
    want = cfg.get("pairs_sha256")
    if want and g.file_sha(path) != want:
        raise p4.HardStop(f"{path}: sha256 differs from the config's pairs_sha256")


class World:
    """The corpus, the glossary and the pairs, with the judge prompt per pair."""

    def __init__(self, cfg: Mapping[str, Any]) -> None:
        self.cfg = cfg
        self.out = Path(cfg["out_dir"])
        self.calib_dir = calib_dir(cfg)
        rows = g.read_chunks(cfg["chunks"])
        rule = cfg.get("paper_key", "path_root")
        self.chunks = {r.chunk_id: r for r in rows}
        self.paper_of = {r.chunk_id: g.paper_of(r, rule) for r in rows}
        self.titles: dict[str, str] = {}
        for r in rows:
            if r.title:
                self.titles.setdefault(self.paper_of[r.chunk_id], r.title)
        self.glossary = read_glossary(Path(cfg["glossary"]))

    def pairs(self) -> list[dict[str, Any]]:
        path = self.calib_dir / "pairs.jsonl"
        if not path.is_file():
            raise SystemExit("pairs.jsonl missing: run build-calib first")
        check_pairs_sha(self.cfg, path)
        rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
        for p in rows:
            c = self.chunks.get(p["chunk_id"])
            if c is None or g.sha_text(c.content) != p["content_sha256"]:
                raise p4.HardStop(f"pair {p['pair_id']}: chunk text differs from the calibration set")
        return rows

    def prompt(self, p: Mapping[str, Any]) -> tuple[str, int]:
        c = self.chunks[p["chunk_id"]]
        defs, cut = definitions_for(c.content, self.glossary.get(p["c_paper"], ()))
        return judge_prompt(p["question"], p["term"], c.content, self.titles.get(p["c_paper"], ""), defs), cut


# ------------------------------------------------------------------ run control

class Ctl:
    """PAUSE / INFLIGHT files and the stop reason, shared by every backend."""

    def __init__(self, out: Path, cap: int) -> None:
        self.out, self.cap = out, cap
        self.reason: str | None = None
        self.lock = threading.Lock()

    def stop(self, reason: str) -> None:
        with self.lock:
            if self.reason is None:
                self.reason = reason
                g.log(f"judge: stopping ({reason}); draining in-flight calls")

    def stopped(self) -> bool:
        if self.reason is None and (self.out / "PAUSE").exists():
            self.stop("pause_file")
        return self.reason is not None

    def inflight(self) -> int:
        f = self.out / "INFLIGHT"
        try:
            n = int(f.read_text().strip()) if f.is_file() else self.cap
        except ValueError:
            n = self.cap
        return max(1, min(n, self.cap, MAX_INFLIGHT))


async def run_async(jobs: Sequence[Callable[[], Any]], ctl: Ctl) -> None:
    """``async fn()`` jobs; at most ``ctl.inflight()`` running (re-read at every admission)."""
    running: set[asyncio.Task[Any]] = set()
    for fn in jobs:
        while len(running) >= ctl.inflight():
            done, running = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                t.result()
        if ctl.stopped():
            break
        running.add(asyncio.create_task(fn()))
    if running:
        done, _ = await asyncio.wait(running)
        for t in done:
            t.result()


def notice_line(kind: str, msg: str, log_path: Path) -> str:
    line = f"{kind}-NOTICE alias-judge: {msg}"
    print(line, flush=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(f"{g.now_iso()} {line}\n")
    return line


# ------------------------------------------------------------------ backends

class Labeler:
    def __init__(self, cfg: dict[str, Any], world: World, *, http_factory: Callable[[], Any] | None = None,
                 cli_runner: Callable[..., Any] | None = None, provenance: Mapping[str, Any] | None = None) -> None:
        self.cfg, self.w = cfg, world
        self.out = world.out
        self.http_factory = http_factory or dr.default_http
        self.cli_runner = cli_runner or subprocess.run
        self.provenance = dict(provenance or {})
        self.log_path = self.out / "LOG"

    # -- plan / gate
    def plan(self, backend: str, reps: int) -> dict[str, Any]:
        pairs = self.w.pairs()
        chars = [len(self.w.prompt(p)[0]) + len(JUDGE_SYSTEM) for p in pairs]
        calls = len(pairs) * (reps if backend == "opus" else 1)
        q = self.cfg["qwen"]
        est_in = int(sum(chars) / dr.CHARS_PER_TOKEN_GATEWAY) * (reps if backend == "opus" else 1)
        plan = {"backend": backend, "pairs": len(pairs), "reps": reps if backend == "opus" else 1, "calls": calls,
                "est_input_tokens": est_in, "judge_version": JUDGE_VERSION,
                "pairs_sha256": g.file_sha(self.w.calib_dir / "pairs.jsonl"),
                "system_sha256": g.sha_text(JUDGE_SYSTEM)}
        if backend == "opus":
            gw = self.cfg["gateway"]
            plan.update({"model": gw["model"], "max_tokens": gw["max_tokens"], "inflight": gw["inflight"],
                         "est_output_tokens": calls * 80, "max_output_tokens": calls * int(gw["max_tokens"]),
                         "temperature": gw["temperature"]})
        elif backend == "cli":
            plan.update({"model": self.cfg["cli"]["model"], "inflight": self.cfg["cli"]["inflight"],
                         "max_cost_usd": self.cfg["cli"]["max_cost_usd"]})
        elif backend == "gateway":
            jg = self.cfg["judge_gw"]
            sub = self.gw_subset()
            reps_sub = int(jg["subset_reps"])
            calls = len(pairs) + len(sub) * (reps_sub - 1)
            system = JUDGE_SYSTEMS[jg["judge_version"]]
            plan.update({"model": jg["model"], "tag": jg["tag"], "base_url": str(jg["base_url"]),
                         "served_models": self.gw_served(), "judge_version": jg["judge_version"],
                         "system_sha256": g.sha_text(system), "max_tokens": jg["max_tokens"],
                         "temperature": jg["temperature"], "inflight": jg["inflight"], "reps": 1,
                         "subset_pairs": len(sub), "subset_reps": reps_sub,
                         "subset_sha256": g.file_sha(jg["subset_file"]) if jg["subset_file"] else None,
                         "calls": calls, "est_input_tokens": int(sum(chars) / len(chars) / dr.CHARS_PER_TOKEN_GATEWAY
                                                                 * calls) if chars else 0,
                         "max_output_tokens": calls * int(jg["max_tokens"])})
        else:
            plan.update({"model": q["model"], "n": q["n"], "temperature": q["temperature"],
                         "max_tokens": q["max_tokens"], "enable_thinking": q["enable_thinking"],
                         "inflight": q["inflight"], "samples": len(pairs) * int(q["n"]),
                         "judge_version": q["judge_version"],
                         "system_sha256": g.sha_text(JUDGE_SYSTEMS[q["judge_version"]])})
            if q["variant"] is not None:        # a round-2 variant; round 1's plan is unchanged
                plan.update({"variant": q["variant"], "thinking_switch": THINKING_SWITCH})
                if q["require_served_model"]:   # round 3: the endpoint is part of the plan
                    plan.update({"base_url": str(q["base_url"]), "require_served_model": True})
        return plan

    def plan_sha(self, plan: Mapping[str, Any]) -> str:
        return g.sha_text(json.dumps(dict(plan), sort_keys=True))

    def key(self, backend: str) -> str:
        """The file key of a backend: ``qwen-<variant>`` for a round-2 Qwen variant, ``gw-<tag>``
        for a gateway judge."""
        if backend == "gateway":
            tag = self.cfg["judge_gw"]["tag"]
            if not tag:
                raise SystemExit("judge_gw.tag is required")
            return f"gw-{tag}"
        v = self.cfg["qwen"]["variant"]
        return f"qwen-{v}" if backend == "qwen" and v is not None else backend

    def preflight(self, backend: str, reps: int) -> dict[str, Any]:
        plan = self.plan(backend, reps)
        g.write_json_atomic(self.out / f"preflight-{self.key(backend)}.json",
                            {"plan": plan, "plan_sha256": self.plan_sha(plan), "t": g.now_iso(),
                             "provenance": self.provenance})
        kind = {"opus": "GATEWAY SIZE", "cli": "CLI", "qwen": "QWEN", "gateway": "GATEWAY SIZE"}[backend]
        msg = (f"label backend={backend} model={plan.get('model')}: {plan['calls']} calls "
               f"({plan['pairs']} pairs x {plan['reps']}), ~{plan['est_input_tokens']} tokens in")
        if backend == "opus":
            msg += (f", ~{plan['est_output_tokens']} out (cap {plan['max_output_tokens']}), "
                    f"<= {plan['inflight']} in flight, fallback_model \"\"")
        if backend == "gateway":
            msg = (f"label backend=gateway model={plan['model']}: {plan['calls']} calls ({plan['pairs']} pairs x 1"
                   f" + {plan['subset_pairs']} subset pairs x {plan['subset_reps'] - 1} more), "
                   f"~{plan['est_input_tokens']} tokens in, max_tokens {plan['max_tokens']} (cap "
                   f"{plan['max_output_tokens']} out), <= {plan['inflight']} in flight, fallback_model \"\", "
                   f"prompt={plan['judge_version']}, served model in {plan['served_models']}")
        if backend == "qwen":
            msg += f", n={plan['n']} samples/call at T={plan['temperature']}, thinking={plan['enable_thinking']}"
            if plan.get("variant") is not None:
                msg += f", variant={plan['variant']} prompt={plan['judge_version']}"
        notice_line(kind, msg, self.log_path)
        return plan

    def gate(self, backend: str, reps: int, acknowledged: bool) -> None:
        if not acknowledged:
            raise SystemExit("label: run preflight, send the notice, then pass --notice-acknowledged")
        pf = self.out / f"preflight-{self.key(backend)}.json"
        if not pf.is_file():
            raise SystemExit(f"label: no preflight-{self.key(backend)}.json; run preflight first")
        if json.loads(pf.read_text())["plan_sha256"] != self.plan_sha(self.plan(backend, reps)):
            raise SystemExit("label: the plan changed since the preflight; run preflight again")

    def store(self, backend: str) -> p4.Store:
        return p4.Store(self.out / f"labels-{self.key(backend)}.jsonl")

    def write_totals(self, backend: str, totals: Mapping[str, Any], status: str) -> None:
        path = self.out / f"totals-{self.key(backend)}.json"
        prev = json.loads(path.read_text()) if path.is_file() else {"segments": []}
        prev["segments"].append({"t": g.now_iso(), "status": status, "totals": dict(totals),
                                 "provenance": self.provenance})
        g.write_json_atomic(path, prev)

    # -- opus via the gateway
    def label_opus(self, reps: int) -> int:
        gw_cfg, lim = self.cfg["gateway"], self.cfg["limits"]
        if not gw_cfg.get("base_url") or not gw_cfg.get("model"):
            raise SystemExit("gateway.base_url and gateway.model are required")
        url = str(gw_cfg["base_url"]).rstrip("/").removesuffix("/v1")
        st = self.store("opus")
        prev = Counter()
        tp = self.out / "totals-opus.json"
        if tp.is_file():
            segs = json.loads(tp.read_text())["segments"]
            if segs:                                # since_start of the last segment is cumulative
                prev.update({k: v for k, v in segs[-1]["totals"].items() if isinstance(v, int)
                             and k.startswith("gateway")})
        control = dr.R2Control(lim, self.out / "PAUSE", dict(prev))
        budget: dict[str, Any] = {}
        gw = ad.AliasGateway(url, control, {**lim, "empty_retries": 1, "empty_backoff_s": float(gw_cfg["empty_retry_s"])},
                             self.out / "calls-opus.jsonl", on_budget=lambda ev: budget.setdefault("ev", ev))
        spec = {"model": gw_cfg["model"], "tag": gw_cfg["tag"], "max_tokens": int(gw_cfg["max_tokens"]),
                "temperature": gw_cfg.get("temperature")}
        ctl = Ctl(self.out, int(gw_cfg["inflight"]))
        empties = {"n": 0}
        pairs = self.w.pairs()

        def need(k: str) -> bool:
            """Not labelled yet: no row, or a row written empty (an empty answer is re-issued)."""
            row = st.get(k)
            return row is None or row.get("status") in EMPTY

        todo = [(p, r) for r in range(1, reps + 1) for p in pairs if need(f"opus|{p['pair_id']}|r{r}")]

        def job(p: dict[str, Any], r: int) -> Callable[[], Any]:
            async def fn() -> None:
                if ctl.stopped() or control.stopped():
                    return
                k = f"opus|{p['pair_id']}|r{r}"
                prompt, cut = self.w.prompt(p)
                msgs = [{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": prompt}]
                res = None
                for attempt in range(2):
                    res = await gw.once(self.http, spec, msgs, "judge", k)
                    if res is None:
                        if control.reason:
                            ctl.stop(control.reason)
                        return
                    if res["status"] in ("empty", "empty0") and attempt == 0:
                        await asyncio.sleep(float(gw_cfg["empty_retry_s"]))
                        continue
                    break
                assert res is not None
                if res["status"] == "error":
                    control.fail(str(res.get("error")))
                    if control.reason:
                        ctl.stop(control.reason)
                    return
                if res["status"] in EMPTY:          # not stored: the next pass or --resume re-issues it
                    empties["n"] += 1
                    return
                verdict, reason = parse_judgement(res.get("text") or "") if res["status"] == "ok" else (None, "")
                control.ok()
                st.put({"k": k, "pair_id": p["pair_id"], "rep": r, "backend": "opus", "status": res["status"],
                        "verdict": verdict, "reason": reason, "text": res.get("text"), "model": res.get("model"),
                        "requested_model": spec["model"], "pt": res.get("pt"), "ct": res.get("ct"),
                        "finish": res.get("finish"), "defs_cut": cut, "prompt_sha256": g.sha_text(prompt),
                        "judge_version": JUDGE_VERSION, "t": g.now_iso()})
            return fn

        async def go() -> None:
            self.http = self.http_factory()
            try:
                await run_async([job(p, r) for p, r in todo], ctl)
            finally:
                close = getattr(self.http, "aclose", None)
                if close is not None:
                    await close()

        g.log(f"opus: {len(todo)} calls to make ({len(pairs)} pairs x {reps} reps; {len(st.rows)} stored)")
        for _ in range(2):                         # a failed call is re-issued once in a second pass
            if not todo or ctl.stopped() or control.stopped():
                break
            asyncio.run(go())
            todo = [(p, r) for r in range(1, reps + 1) for p in pairs if need(f"opus|{p['pair_id']}|r{r}")]
        tot = {k: int(v) for k, v in control.totals()["since_start"].items()}
        stopped = ctl.reason or control.reason
        if budget.get("ev"):
            ev = {**budget["ev"], "totals": tot}
            g.write_json_atomic(self.out / "budget-stop-judge.json", ev)
            (self.out / "PAUSE").write_text(f"budget stop {ev['t']}: HTTP {ev['http_status']} ({ev['why']}); "
                                            "remove, preflight, then label --resume\n", encoding="utf-8")
            self.write_totals("opus", tot, f"budget_stop:{ev['why']}")
            notice_line("GATEWAY", f"label backend=opus BUDGET STOP (HTTP {ev['http_status']}, {ev['why']}); "
                        f"totals {json.dumps(tot, sort_keys=True)}", self.log_path)
            return EXIT_BUDGET
        status = f"paused:{stopped}" if stopped else "done"
        left = [1 for r in range(1, reps + 1) for p in pairs if need(f"opus|{p['pair_id']}|r{r}")]
        tot["empty_answers_not_stored"] = empties["n"]
        if not stopped and left:
            status = f"paused:calls_failed:{len(left)}"
        self.write_totals("opus", tot, status)
        notice_line("GATEWAY", f"label backend=opus {status}: totals {json.dumps(tot, sort_keys=True)}; "
                    f"{len(st.rows)} labels stored", self.log_path)
        return EXIT_OK if status == "done" else EXIT_PAUSED

    # -- any model on the partner gateway (TIEBREAKER-PLAN.md)
    def gw_served(self) -> list[str]:
        jg = self.cfg["judge_gw"]
        return sorted({str(jg["model"]), *(str(x) for x in (jg["served_models"] or []))})

    def gw_subset(self) -> list[str]:
        """The self-consistency subset's pair ids (``judge_gw.subset_file``), checked against the
        pinned sha256 and the pairs file."""
        jg = self.cfg["judge_gw"]
        if not jg["subset_file"]:
            return []
        path = Path(jg["subset_file"])
        if jg["subset_sha256"] and g.file_sha(path) != jg["subset_sha256"]:
            raise p4.HardStop(f"{path}: sha256 differs from judge_gw.subset_sha256")
        ids = [x.strip() for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
        known = {p["pair_id"] for p in self.w.pairs()}
        if len(set(ids)) != len(ids) or not set(ids) <= known:
            raise SystemExit(f"{path.name}: duplicate pair ids or ids not in pairs.jsonl")
        return ids

    def label_gw(self) -> int:
        """One gateway model as a judge: rep 1 on every pair, reps 2.. on the subset. An empty
        answer is re-issued once and then STORED (status ``empty``, no verdict): it is part of
        what is measured. A reply whose model id is not in :meth:`gw_served` stops the run and
        is not stored."""
        jg, lim = self.cfg["judge_gw"], self.cfg["limits"]
        if not jg.get("base_url") or not jg.get("model"):
            raise SystemExit("judge_gw.base_url and judge_gw.model are required")
        key = self.key("gateway")
        url = str(jg["base_url"]).rstrip("/").removesuffix("/v1")
        served = set(self.gw_served())
        system = JUDGE_SYSTEMS[jg["judge_version"]]
        st = self.store("gateway")
        prev: Counter[str] = Counter()
        tp = self.out / f"totals-{key}.json"
        if tp.is_file():
            segs = json.loads(tp.read_text())["segments"]
            if segs:
                prev.update({k: v for k, v in segs[-1]["totals"].items() if isinstance(v, int)
                             and k.startswith("gateway")})
        control = dr.R2Control(lim, self.out / "PAUSE", dict(prev))
        budget: dict[str, Any] = {}
        gw = ad.AliasGateway(url, control, {**lim, "empty_retries": 1, "empty_backoff_s": float(jg["empty_retry_s"])},
                             self.out / f"calls-{key}.jsonl", on_budget=lambda ev: budget.setdefault("ev", ev))
        spec = {"model": jg["model"], "tag": key, "max_tokens": int(jg["max_tokens"]),
                "temperature": jg.get("temperature")}
        ctl = Ctl(self.out, int(jg["inflight"]))
        seg: Counter[str] = Counter()
        pairs = self.w.pairs()
        by_id = {p["pair_id"]: p for p in pairs}
        sub = self.gw_subset()
        wanted = [(p["pair_id"], 1) for p in pairs] + [(pid, r) for r in range(2, int(jg["subset_reps"]) + 1)
                                                        for pid in sub]

        def todo() -> list[tuple[str, int]]:
            return [(pid, r) for pid, r in wanted if f"{key}|{pid}|r{r}" not in st]

        def job(pid: str, r: int) -> Callable[[], Any]:
            async def fn() -> None:
                if ctl.stopped() or control.stopped():
                    return
                p = by_id[pid]
                k = f"{key}|{pid}|r{r}"
                prompt, cut = self.w.prompt(p)
                msgs = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
                res = None
                t0 = time.time()
                attempts = 0
                for attempt in range(2):
                    attempts += 1
                    res = await gw.once(self.http, spec, msgs, "judge", k)
                    if res is None:
                        if control.reason:
                            ctl.stop(control.reason)
                        return
                    if res["status"] in ("ok", "empty", "empty0", "truncated") and res.get("model") not in served:
                        seg["gateway_model_mismatch"] += 1
                        ctl.stop(f"served_model_mismatch:{res.get('model')}")
                        return
                    if res["status"] in EMPTY and attempt == 0:
                        seg["gateway_empty_reissued"] += 1
                        await asyncio.sleep(float(jg["empty_retry_s"]))
                        continue
                    break
                assert res is not None
                if res["status"] == "error":
                    control.fail(str(res.get("error")))
                    if control.reason:
                        ctl.stop(control.reason)
                    return
                control.ok()
                verdict, reason = parse_judgement(res.get("text") or "") if res["status"] == "ok" else (None, "")
                st.put({"k": k, "pair_id": pid, "rep": r, "backend": "gateway", "status": res["status"],
                        "verdict": verdict, "reason": reason, "text": res.get("text"), "model": res.get("model"),
                        "requested_model": spec["model"], "pt": res.get("pt"), "ct": res.get("ct"),
                        "reasoning_tokens": res.get("rt"), "finish": res.get("finish"), "attempts": attempts,
                        "latency_s": round(time.time() - t0, 2), "defs_cut": cut,
                        "prompt_sha256": g.sha_text(prompt), "judge_version": jg["judge_version"],
                        "system_sha256": g.sha_text(system), "max_tokens": int(jg["max_tokens"]),
                        "temperature": jg.get("temperature"), "endpoint": str(jg["base_url"]), "t": g.now_iso()})
            return fn

        async def go(items: list[tuple[str, int]]) -> None:
            self.http = self.http_factory()
            try:
                await run_async([job(pid, r) for pid, r in items], ctl)
            finally:
                close = getattr(self.http, "aclose", None)
                if close is not None:
                    await close()

        items = todo()
        notice_line("GATEWAY", f"label backend=gateway model={jg['model']}: starting {len(items)} calls "
                    f"({len(st.rows)} stored, <= {ctl.inflight()} in flight)", self.log_path)
        for _ in range(2):                         # a failed call is re-issued once in a second pass
            if not items or ctl.stopped() or control.stopped():
                break
            asyncio.run(go(items))
            items = todo()
        tot = {**{k: int(v) for k, v in control.totals()["since_start"].items()}, **{k: int(v) for k, v in seg.items()}}
        stopped = ctl.reason or control.reason
        if budget.get("ev"):
            ev = {**budget["ev"], "totals": tot}
            g.write_json_atomic(self.out / f"budget-stop-{key}.json", ev)
            (self.out / "PAUSE").write_text(f"budget stop {ev['t']}: HTTP {ev['http_status']} ({ev['why']}); "
                                            "remove, preflight, then label --resume\n", encoding="utf-8")
            self.write_totals("gateway", tot, f"budget_stop:{ev['why']}")
            notice_line("GATEWAY", f"label backend=gateway model={jg['model']} BUDGET STOP (HTTP "
                        f"{ev['http_status']}, {ev['why']}); totals {json.dumps(tot, sort_keys=True)}", self.log_path)
            return EXIT_BUDGET
        left = todo()
        status = f"paused:{stopped}" if stopped else ("done" if not left else f"paused:calls_failed:{len(left)}")
        self.write_totals("gateway", tot, status)
        notice_line("GATEWAY", f"label backend=gateway model={jg['model']} {status}: totals "
                    f"{json.dumps(tot, sort_keys=True)}; {len(st.rows)} labels stored", self.log_path)
        return EXIT_OK if status == "done" else (EXIT_HARD_STOP if str(stopped).startswith("served_model")
                                                 else EXIT_PAUSED)

    # -- the Claude CLI
    def label_cli(self) -> int:
        c = self.cfg["cli"]
        shim = _CliShim(c, self.cli_runner)
        cwd = self.cli_cwd()
        st = self.store("cli")
        ctl = Ctl(self.out, int(c["inflight"]))
        pairs = self.w.pairs()
        todo = [p for p in pairs if f"cli|{p['pair_id']}" not in st]
        seg: Counter[str] = Counter()
        lock = threading.Lock()
        spent = {"usd": sum(float(r.get("cost_usd") or 0) for r in st.rows.values()),
                 "calls": len(st.rows), "iso_total": 0}
        checked = {"done": not bool(c["isolation_check"])}

        def one(p: dict[str, Any]) -> None:
            k = f"cli|{p['pair_id']}"
            prompt, cut = self.w.prompt(p)
            in_a_row = errs = 0
            while True:
                with lock:
                    if ctl.stopped():
                        return
                    if spent["calls"] >= int(c["max_calls"]):
                        ctl.stop("cli_call_cap")
                        return
                    if spent["usd"] >= float(c["max_cost_usd"]):
                        ctl.stop("cli_cost_cap")
                        return
                    spent["calls"] += 1
                    seg["cli_calls"] += 1
                try:
                    res = shim.call_cli(prompt, cwd)
                except subprocess.TimeoutExpired:
                    res = {"status": "error", "error": "timeout"}
                with lock:
                    spent["usd"] += float(res.get("cost_usd") or 0)
                    seg["cli_prompt_tokens"] += int(res.get("context_tokens") or 0)
                    seg["cli_completion_tokens"] += int(res.get("output_tokens") or 0)
                if (res["status"] in ("ok", "empty") or res.get("fallback")) and c["isolation_check"]:
                    iso = shim.isolation(prompt, res)
                    if not iso["PASS"]:
                        with lock:
                            seg["cli_isolation_anomalies"] += 1
                            spent["iso_total"] += 1
                            in_a_row += 1
                            g.log(f"cli: isolation anomaly on {p['pair_id']} ({in_a_row} in a row): {iso}")
                            if in_a_row > dr.ISOLATION_RETRIES or spent["iso_total"] > max(
                                    dr.ISOLATION_RETRIES, dr.ISOLATION_WAVE_RATE * len(todo)):
                                ctl.stop("isolation_failed")
                                return
                        continue
                    checked["done"] = True
                if res["status"] == "limit":
                    ctl.stop("usage_limit")
                    return
                if res["status"] == "error":
                    errs += 1
                    with lock:
                        seg["cli_errors"] += 1
                    if errs >= 2:
                        return
                    continue
                if res["status"] == "refused":
                    with lock:
                        seg["cli_refusals"] += 1
                        seg["cli_safety_fallbacks"] += int(bool(res.get("fallback")))
                verdict, reason = parse_judgement(res.get("text") or "") if res["status"] == "ok" else (None, "")
                st.put({"k": k, "pair_id": p["pair_id"], "backend": "cli", "status": res["status"],
                        "verdict": verdict, "reason": reason, "text": res.get("text"), "model": res.get("model"),
                        "served_models": res.get("served_models"), "safety_stops": res.get("safety_stops"),
                        "fallback": bool(res.get("fallback")), "error": res.get("error"),
                        "context_tokens": res.get("context_tokens"), "output_tokens": res.get("output_tokens"),
                        "cost_usd": res.get("cost_usd"), "duration_s": res.get("duration_s"), "defs_cut": cut,
                        "isolation_anomalies": in_a_row, "prompt_sha256": g.sha_text(prompt),
                        "judge_version": JUDGE_VERSION, "t": g.now_iso()})
                return

        if todo:
            notice_line("CLI", f"label backend=cli: starting {len(todo)} pairs (<= {ctl.inflight()} in flight)",
                        self.log_path)
        # the first call alone: the isolation probe
        while todo and not checked["done"] and not ctl.stopped():
            one(todo[0])
            todo = todo[1:]
        with ThreadPoolExecutor(max_workers=int(c["inflight"])) as ex:
            ex_jobs = []
            for p in todo:
                while sum(1 for f in ex_jobs if not f.done()) >= ctl.inflight():
                    time.sleep(0.2)
                if ctl.stopped():
                    break
                ex_jobs.append(ex.submit(one, p))
            for f in ex_jobs:
                f.result()
        left = sum(1 for p in pairs if f"cli|{p['pair_id']}" not in st)
        status = f"paused:{ctl.reason}" if ctl.reason else ("done" if not left else f"paused:calls_failed:{left}")
        tot = {**{k: int(v) for k, v in seg.items()}, "cost_usd_total": round(spent["usd"], 4)}
        self.write_totals("cli", tot, status)
        notice_line("CLI", f"label backend=cli {status}: {json.dumps(tot, sort_keys=True)}", self.log_path)
        return EXIT_OK if status == "done" else (EXIT_HARD_STOP if ctl.reason == "isolation_failed" else EXIT_PAUSED)

    def cli_cwd(self) -> Path:
        c = self.cfg["cli"]
        d = Path(c["cwd"]) if c.get("cwd") else self.out / "cli_cwd"
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        d.chmod(0o700)
        if any(d.iterdir()):
            raise p4.HardStop("cli_cwd_not_empty")
        for anc in [d, *d.resolve().parents]:
            if (anc / "CLAUDE.md").exists() or (anc / "AGENTS.md").exists():
                raise p4.HardStop("cli_cwd_has_instructions_above")
        return d

    # -- local Qwen
    def label_qwen(self) -> int:
        q = self.cfg["qwen"]
        if not q.get("base_url") or not q.get("model"):
            raise SystemExit("qwen.base_url and qwen.model are required")
        url = str(q["base_url"]).rstrip("/").removesuffix("/v1")
        st = self.store("qwen")
        ctl = Ctl(self.out, int(q["inflight"]))
        pairs = self.w.pairs()
        todo = [p for p in pairs if f"qwen|{p['pair_id']}" not in st]
        seg: Counter[str] = Counter()
        fails = {"row": 0}
        t0 = time.time()

        def job(p: dict[str, Any]) -> Callable[[], Any]:
            async def fn() -> None:
                if ctl.stopped():
                    return
                prompt, cut = self.w.prompt(p)
                system = JUDGE_SYSTEMS[q["judge_version"]]
                body: dict[str, Any] = {
                    "model": q["model"], "n": int(q["n"]), "temperature": float(q["temperature"]),
                    "max_tokens": int(q["max_tokens"]),
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                    "chat_template_kwargs": {"enable_thinking": bool(q["enable_thinking"])}}
                ts = time.time()
                seg["qwen_calls"] += 1
                try:
                    r = await self.http.post(f"{url}/v1/chat/completions", json=body)
                    status = int(r.status_code)
                    d = r.json() if status == 200 else None
                except Exception as e:  # noqa: BLE001
                    status, d = -1, None
                    g.log(f"qwen: {p['pair_id']}: {type(e).__name__}")
                if d is None or not d.get("choices"):
                    seg["qwen_failures"] += 1
                    fails["row"] += 1
                    if fails["row"] >= 5:
                        ctl.stop("qwen_failures_5_in_a_row")
                    return
                if q["require_served_model"] and d.get("model") != q["model"]:
                    seg["qwen_model_mismatch"] += 1
                    ctl.stop(f"served_model_mismatch:{d.get('model')}")
                    return
                fails["row"] = 0
                ch = sorted(d["choices"], key=lambda x: int(x.get("index") or 0))
                verdicts, finishes = [], Counter()
                for x in ch:
                    v, _ = parse_judgement((x.get("message") or {}).get("content") or "")
                    verdicts.append(None if v is None else int(v))
                    finishes[str(x.get("finish_reason"))] += 1
                u = d.get("usage") or {}
                rt = int((u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
                seg["qwen_prompt_tokens"] += int(u.get("prompt_tokens") or 0)
                seg["qwen_completion_tokens"] += int(u.get("completion_tokens") or 0)
                seg["qwen_reasoning_tokens"] += rt
                seg["qwen_samples"] += len(ch)
                extra: dict[str, Any] = {}
                if q["variant"] is not None:
                    extra = {"variant": q["variant"], "system_sha256": g.sha_text(system),
                             "thinking_switch": THINKING_SWITCH, "reasoning_tokens": rt,
                             "max_tokens": int(q["max_tokens"]), "endpoint": str(q["base_url"]),
                             "reasoning_sample": [((x.get("message") or {}).get("reasoning")
                                                   or (x.get("message") or {}).get("reasoning_content") or "")[:4000]
                                                  for x in ch[:1]]}
                st.put({"k": f"qwen|{p['pair_id']}", "pair_id": p["pair_id"], "backend": "qwen",
                        "model": d.get("model"), "requested_model": q["model"], "n": len(ch),
                        "verdicts": verdicts, "invalid": sum(v is None for v in verdicts),
                        "finish": dict(finishes), "texts_sample": [(x.get("message") or {}).get("content")
                                                                   for x in ch[:3]],
                        "pt": u.get("prompt_tokens"), "ct": u.get("completion_tokens"),
                        "temperature": q["temperature"], "enable_thinking": q["enable_thinking"],
                        "duration_s": round(time.time() - ts, 2), "defs_cut": cut,
                        "prompt_sha256": g.sha_text(prompt), "judge_version": q["judge_version"], "t": g.now_iso(),
                        **extra})
            return fn

        async def go() -> None:
            import httpx

            self.http = self.http_factory() if self.http_factory is not dr.default_http else httpx.AsyncClient(
                timeout=float(q["timeout_s"]))
            try:
                await run_async([job(p) for p in todo], ctl)
            finally:
                close = getattr(self.http, "aclose", None)
                if close is not None:
                    await close()

        notice_line("QWEN", f"label backend=qwen: {len(todo)} pairs x n={q['n']}", self.log_path)
        asyncio.run(go())
        left = sum(1 for p in pairs if f"qwen|{p['pair_id']}" not in st)
        status = f"paused:{ctl.reason}" if ctl.reason else ("done" if not left else f"paused:calls_failed:{left}")
        wall = time.time() - t0
        tot = {**{k: int(v) for k, v in seg.items()}, "wall_s": round(wall, 1),
               "samples_per_s": round(seg["qwen_samples"] / wall, 2) if wall else None,
               "completion_tokens_per_s": round(seg["qwen_completion_tokens"] / wall, 1) if wall else None}
        self.write_totals("qwen", tot, status)
        notice_line("QWEN", f"label backend=qwen {status}: {json.dumps(tot, sort_keys=True)}", self.log_path)
        return EXIT_OK if status == "done" else EXIT_PAUSED


class _CliShim:
    """The alias verifier's CLI call (:meth:`glossbench_r2_draft.Run.call_cli`, with the #891
    safeguard-fallback detection) and its isolation check, with the judge's system prompt."""

    call_cli = dr.Run.call_cli
    claude_cmd = dr.Run.claude_cmd
    verify_system = JUDGE_SYSTEM

    def isolation(self, prompt: str, res: dict[str, Any]) -> dict[str, Any]:
        """The verifier's check, with the budget scaled by the requests the CLI made: on a
        safeguard stop it re-asks the SAME prompt (``safety_stops`` more requests) and the
        configured model's ``modelUsage`` then carries the input of every request, even when
        the same model answers (seen 2026-10-31: one model, ``num_turns`` 2, context ~2x).
        Such an answer is ``refused`` anyway (#891); this only keeps the check from
        discarding it as an anomaly. A real leak (CLAUDE.md, tool definitions: tens of
        thousands of tokens) still fails."""
        iso = dr.Run.isolation(self, prompt, res)
        if "requests" in iso:      # the verifier's check already scaled it (same-model re-ask)
            return iso
        k = 1 + int(res.get("safety_stops") or 0)
        if k > 1:
            iso["budget"] = round(iso["budget"] * k, 1)
            iso["requests"] = k
            seen = iso["context_tokens"]
            iso["PASS"] = seen is not None and seen <= iso["budget"] and not iso["tool_events"]
        return iso

    def __init__(self, c: Mapping[str, Any], runner: Callable[..., Any]) -> None:
        self.cfg = {"verifier": dict(c)}
        self.cli_runner = runner


# ------------------------------------------------------------------ analysis

def kappa(a: Sequence[int], b: Sequence[int]) -> float | None:
    n = len(a)
    if not n:
        return None
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return None if pe >= 1 else (po - pe) / (1 - pe)


def opus_reference(rows: Iterable[Mapping[str, Any]], reps: int) -> dict[str, dict[str, Any]]:
    by: dict[str, dict[int, bool | None]] = defaultdict(dict)
    for r in rows:
        by[r["pair_id"]][int(r["rep"])] = r.get("verdict")
    out = {}
    for pid, d in by.items():
        vs = [d.get(i) for i in range(1, reps + 1)]
        valid = [int(v) for v in vs if v is not None]
        if not valid:
            continue
        yes = sum(valid)
        out[pid] = {"labels": vs, "valid": len(valid), "yes": yes,
                    "ref": int(yes * 2 > len(valid)) if yes * 2 != len(valid) else None,
                    "unanimous": len(valid) == reps and yes in (0, reps)}
    return out


def _ci(xs: Sequence[float]) -> list[float]:
    s = sorted(xs)
    return [round(s[int(0.025 * (len(s) - 1))], 4), round(s[int(0.975 * (len(s) - 1))], 4)]


def n_curve(yes: Sequence[int], valid: Sequence[int], ref: Sequence[int], strata: Sequence[str],
            ns: Sequence[int], draws: int, seed: int,
            groups: Mapping[str, Sequence[str]] | None = None) -> dict[int, dict[str, Any]]:
    """Majority-vote agreement per odd ``n`` over ``draws`` hypergeometric subsamples; ``fp`` /
    ``fn`` are the mean counts of false yes / false no per draw. ``groups``: further per-pair
    labellings (e.g. the record kind) scored like the strata, as ``per_<name>_acc``."""
    import numpy as np

    rng = np.random.default_rng(seed)
    yes_a, val_a, ref_a = np.array(yes), np.array(valid), np.array(ref)
    strata_a = np.array(strata)
    out: dict[int, dict[str, Any]] = {}
    for n in ns:
        m = np.minimum(val_a, n)                        # a pair with fewer valid samples uses them all
        sub = np.stack([rng.hypergeometric(yes_a, val_a - yes_a, m) for _ in range(draws)])  # draws x pairs
        vote = (sub * 2 > m).astype(int)
        tie = sub * 2 == m
        vote = np.where(tie, rng.integers(0, 2, size=vote.shape), vote)
        acc = (vote == ref_a).mean(axis=1)
        ks = []
        for row in vote:
            ks.append(kappa(list(row), list(ref_a)) or 0.0)
        per = {}
        for s in sorted(set(strata)):
            msk = strata_a == s
            per[s] = round(float((vote[:, msk] == ref_a[msk]).mean()), 4)
        fp = ((vote == 1) & (ref_a == 0)).sum(axis=1)
        fn = ((vote == 0) & (ref_a == 1)).sum(axis=1)
        out[n] = {"acc": round(float(acc.mean()), 4), "acc_ci": _ci(list(acc)), "kappa": round(float(np.mean(ks)), 4),
                  "kappa_ci": _ci(ks), "per_stratum_acc": per,
                  "fp": round(float(fp.mean()), 2), "fn": round(float(fn.mean()), 2)}
        for gname, lab in (groups or {}).items():
            la = np.array(lab)
            out[n][f"per_{gname}_acc"] = {v: round(float((vote[:, la == v] == ref_a[la == v]).mean()), 4)
                                          for v in sorted(set(lab))}
    return out


def cascade(share: Sequence[float], ref: Sequence[int], taus: Sequence[float]) -> list[dict[str, Any]]:
    rows = []
    n = len(ref)
    for t in taus:
        acc_ok = acc_n = 0
        for s, r in zip(share, ref, strict=True):
            if s >= t or s <= 1 - t:
                acc_n += 1
                acc_ok += int((s >= t) == bool(r))
        esc = n - acc_n
        rows.append({"tau": t, "accepted": acc_n, "escalated": esc, "escalation_rate": round(esc / n, 4) if n else None,
                     "accepted_accuracy": round(acc_ok / acc_n, 4) if acc_n else None,
                     "cascade_accuracy": round((acc_ok + esc) / n, 4) if n else None})
    return rows


def analyse(cfg: Mapping[str, Any], out: Path, reps: int = 3) -> dict[str, Any]:
    import numpy as np

    a = cfg["analysis"]
    pairs = {p["pair_id"]: p for p in (json.loads(x) for x in (out / "pairs.jsonl").read_text().splitlines() if x)}
    res: dict[str, Any] = {"judge_version": JUDGE_VERSION, "pairs": len(pairs), "t": g.now_iso(), "inputs": {}}
    for b in BACKENDS:
        f = out / f"labels-{b}.jsonl"
        if f.is_file():
            res["inputs"][f.name] = g.file_sha(f)
    res["inputs"]["pairs.jsonl"] = g.file_sha(out / "pairs.jsonl")
    opus_rows = list(p4.Store(out / "labels-opus.jsonl").rows.values()) if (out / "labels-opus.jsonl").is_file() else []
    ref = opus_reference(opus_rows, reps)
    served = Counter(str(r.get("model")) for r in opus_rows)
    una = [pid for pid, r in ref.items() if r["unanimous"]]
    agree_pairs = []
    for r in ref.values():
        v = [x for x in r["labels"] if x is not None]
        agree_pairs.extend(int(v[i] == v[j]) for i in range(len(v)) for j in range(i + 1, len(v)))
    res["opus"] = {"served_models": dict(served), "pairs_labelled": len(ref),
                   "invalid_labels": sum(1 for r in opus_rows if r.get("verdict") is None),
                   "unanimous": len(una), "unanimous_rate": round(len(una) / len(ref), 4) if ref else None,
                   "pairwise_rep_agreement": round(sum(agree_pairs) / len(agree_pairs), 4) if agree_pairs else None,
                   "yes_rate_ref": round(sum(r["ref"] or 0 for r in ref.values()) / len(ref), 4) if ref else None,
                   "by_stratum": {s: {"pairs": sum(1 for pid in ref if pairs[pid]["stratum"] == s),
                                      "yes_ref": sum(1 for pid in ref if pairs[pid]["stratum"] == s and ref[pid]["ref"]),
                                      "unanimous": sum(1 for pid in una if pairs[pid]["stratum"] == s)}
                                  for s in STRATA}}
    if (out / "labels-cli.jsonl").is_file():
        cli = {r["pair_id"]: r for r in p4.Store(out / "labels-cli.jsonl").rows.values()}
        both = [pid for pid in ref if ref[pid]["ref"] is not None and cli.get(pid, {}).get("verdict") is not None]
        bu = [pid for pid in both if ref[pid]["unanimous"]]
        res["cli"] = {"labelled": len(cli), "with_verdict": sum(1 for r in cli.values() if r.get("verdict") is not None),
                      "refused": sum(1 for r in cli.values() if r.get("status") == "refused"),
                      "safety_fallbacks": sum(1 for r in cli.values() if r.get("fallback")),
                      "served_models": dict(Counter(str(r.get("model")) for r in cli.values())),
                      "cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in cli.values()), 3)}
        for name, ids in (("all", both), ("opus_unanimous", bu)):
            x = [int(cli[pid]["verdict"]) for pid in ids]
            y = [int(ref[pid]["ref"]) for pid in ids]
            res["cli"][f"vs_opus_{name}"] = {"n": len(ids), "agreement": round(sum(i == j for i, j in zip(x, y, strict=True)) / len(ids), 4) if ids else None,
                                             "kappa": None if not ids else round(kappa(x, y) or 0, 4),
                                             "per_stratum": {s: round(float(np.mean([x[i] == y[i] for i, pid in enumerate(ids) if pairs[pid]["stratum"] == s])), 4)
                                                             for s in STRATA if any(pairs[pid]["stratum"] == s for pid in ids)}}
    if (out / "labels-qwen.jsonl").is_file():
        qw = {r["pair_id"]: r for r in p4.Store(out / "labels-qwen.jsonl").rows.values()}
        res["qwen"] = {"labelled": len(qw), "served_models": dict(Counter(str(r.get("model")) for r in qw.values())),
                       "samples": sum(r["n"] for r in qw.values()),
                       "invalid_samples": sum(r["invalid"] for r in qw.values())}
        maxn = min((r["n"] - r["invalid"]) for r in qw.values()) if qw else 0
        top = max((r["n"] for r in qw.values()), default=0)
        ns = list(range(1, top + 1, 2))
        for name, sel in (("opus_unanimous", lambda pid: ref[pid]["unanimous"]),
                          ("opus_split", lambda pid: not ref[pid]["unanimous"] and ref[pid]["ref"] is not None),
                          ("all", lambda pid: ref[pid]["ref"] is not None)):
            ids = sorted(pid for pid in ref if pid in qw and sel(pid))
            if not ids:
                continue
            yes = [sum(v for v in qw[pid]["verdicts"] if v is not None) for pid in ids]
            val = [sum(v is not None for v in qw[pid]["verdicts"]) for pid in ids]
            rr = [int(ref[pid]["ref"]) for pid in ids]
            st = [pairs[pid]["stratum"] for pid in ids]
            keep = [i for i, v in enumerate(val) if v > 0]
            yes, val, rr, st = ([z[i] for i in keep] for z in (yes, val, rr, st))
            curve = n_curve(yes, val, rr, st, ns, int(a["bootstrap"]), int(a["seed"]))
            last = curve[ns[-1]]["acc"]
            plateau = next((n for n in ns if curve[n]["acc"] >= last - float(a["plateau_eps"])), ns[-1])
            share = [y / v for y, v in zip(yes, val, strict=True)]
            bins = []
            for lo in [i / 10 for i in range(10)]:
                idx = [i for i, s in enumerate(share) if lo <= s < lo + 0.1 or (lo == 0.9 and s == 1.0)]
                bins.append({"share": [lo, round(lo + 0.1, 1)], "pairs": len(idx),
                             "opus_yes_rate": round(sum(rr[i] for i in idx) / len(idx), 4) if idx else None})
            casc = {"n_all": cascade(share, rr, a["taus"])}
            rng = np.random.default_rng(int(a["seed"]) + 7)
            for cn in a["cascade_n"]:
                if not cn or cn > maxn:
                    continue
                draws = []
                for _ in range(int(a["bootstrap"])):
                    sub = rng.hypergeometric(np.array(yes), np.array(val) - np.array(yes), np.minimum(np.array(val), cn))
                    draws.append(cascade(list(sub / np.minimum(np.array(val), cn)), rr, a["taus"]))
                casc[f"n{cn}"] = [{"tau": t, **{k: round(float(np.mean([d[i][k] for d in draws if d[i][k] is not None])), 4)
                                               for k in ("escalation_rate", "accepted_accuracy", "cascade_accuracy")}}
                                  for i, t in enumerate(a["taus"])]
            res["qwen"][name] = {"pairs": len(rr), "curve": {str(n): curve[n] for n in ns}, "plateau_n": plateau,
                                 "plateau_rule": f"smallest odd n with mean acc >= acc(n={ns[-1]}) - {a['plateau_eps']}",
                                 "reliability": bins, "cascade": casc,
                                 "ref_yes_rate": round(sum(rr) / len(rr), 4)}
    return res


# ------------------------------------------------------------------ round 2: variants

def overlap_records(pairs: Iterable[Mapping[str, Any]], main_drafts: Path) -> dict[str, Any]:
    """The calibration records whose ``record_id`` also occurs in the study's (main) draft log.
    Only the ids are read; nothing of a main record is used. The main log grows while the study
    runs, so the result carries the log's sha and line count at the time it was taken."""
    ids: set[str] = set()
    lines = 0
    for r in p4.read_jsonl_repair_tail(main_drafts):
        lines += 1
        if r.get("record_id"):
            ids.add(str(r["record_id"]))
    pl = list(pairs)
    hit = sorted({p["record_id"] for p in pl} & ids)
    return {"records": hit, "questions": len(hit), "pairs": sum(p["record_id"] in hit for p in pl),
            "main_drafts": str(main_drafts), "main_drafts_sha256": g.file_sha(main_drafts),
            "main_drafts_rows": lines, "rule": "calibration record_id present in the main draft log",
            "t": g.now_iso()}


def score_variant(qw: Mapping[str, Mapping[str, Any]], ref: Mapping[str, Mapping[str, Any]],
                  pairs: Mapping[str, Mapping[str, Any]], ids: Sequence[str], a: Mapping[str, Any]
                  ) -> dict[str, Any] | None:
    """One Qwen label set on the pairs ``ids`` (Opus-unanimous, labelled): the n-curve at
    ``report_n`` with false-yes / false-no counts and per-stratum and per-record-kind accuracy,
    and the cascade at ``variant_taus`` for every ``cascade_n`` (bootstrapped) and at all samples."""
    import numpy as np

    ids = [pid for pid in ids if pid in qw and sum(v is not None for v in qw[pid]["verdicts"]) > 0]
    if not ids:
        return None
    yes = [sum(v for v in qw[pid]["verdicts"] if v is not None) for pid in ids]
    val = [sum(v is not None for v in qw[pid]["verdicts"]) for pid in ids]
    rr = [int(ref[pid]["ref"]) for pid in ids]
    st = [pairs[pid]["stratum"] for pid in ids]
    kinds = [pairs[pid]["record_kind"] for pid in ids]
    top = max(val)
    ns = [n for n in a["report_n"] if n <= top]
    curve = n_curve(yes, val, rr, st, ns, int(a["bootstrap"]), int(a["seed"]), {"record_kind": kinds})
    share = [y / v for y, v in zip(yes, val, strict=True)]
    casc: dict[str, Any] = {"n_all": cascade(share, rr, a["variant_taus"])}
    rng = np.random.default_rng(int(a["seed"]) + 7)
    ya, va = np.array(yes), np.array(val)
    for cn in a["cascade_n"]:
        if not cn or cn > top:
            continue
        m = np.minimum(va, cn)
        draws = [cascade(list(rng.hypergeometric(ya, va - ya, m) / m), rr, a["variant_taus"])
                 for _ in range(int(a["bootstrap"]))]
        casc[f"n{cn}"] = [{"tau": t, **{k: round(float(np.mean([d[i][k] for d in draws if d[i][k] is not None])), 4)
                                        for k in ("escalation_rate", "accepted_accuracy", "cascade_accuracy")},
                           "escalated_mean": round(float(np.mean([d[i]["escalated"] for d in draws])), 2)}
                          for i, t in enumerate(a["variant_taus"])]
    return {"pairs": len(ids), "ref_yes": sum(rr), "curve": {str(n): curve[n] for n in ns}, "cascade": casc,
            "pairs_with_fewer_valid_than_max_n": sum(v < max(ns) for v in val)}


def analyse_variants(cfg: Mapping[str, Any], out: Path, reps: int = 3) -> dict[str, Any]:
    """Every ``analysis.variants`` label file against the Opus reference of ``calib_dir``, on
    the Opus-unanimous pairs, in four subsets: all, without the pairs of records that overlap
    the study (``overlap.json``), without ``coll_diff`` (decidable from the definitions block,
    so not evidence of judgement), and without both."""
    a = cfg["analysis"]
    cdir = calib_dir(cfg)
    check_pairs_sha(cfg, cdir / "pairs.jsonl")
    pairs = {p["pair_id"]: p for p in (json.loads(x) for x in (cdir / "pairs.jsonl").read_text().splitlines() if x)}
    ref = opus_reference(p4.Store(cdir / "labels-opus.jsonl").rows.values(), reps)
    ov_path = out / "overlap.json"
    ov = set(json.loads(ov_path.read_text())["records"]) if ov_path.is_file() else set()
    una = sorted(pid for pid, r in ref.items() if r["unanimous"])
    subsets = {"unanimous": una,
               "excl_overlap": [pid for pid in una if pairs[pid]["record_id"] not in ov],
               "excl_coll_diff": [pid for pid in una if pairs[pid]["stratum"] != "coll_diff"],
               "excl_both": [pid for pid in una if pairs[pid]["record_id"] not in ov
                             and pairs[pid]["stratum"] != "coll_diff"]}
    res: dict[str, Any] = {"t": g.now_iso(), "reference": {"labels": str(cdir / "labels-opus.jsonl"),
                                                           "sha256": g.file_sha(cdir / "labels-opus.jsonl"),
                                                           "unanimous": len(una)},
                           "pairs_sha256": g.file_sha(cdir / "pairs.jsonl"),
                           "overlap": {"file": str(ov_path) if ov else None, "records": len(ov),
                                       "pairs": sum(p["record_id"] in ov for p in pairs.values())},
                           "subset_sizes": {k: len(v) for k, v in subsets.items()}, "variants": {}}
    for v in a["variants"]:
        f = Path(v["labels"])
        rows = list(p4.Store(f).rows.values())
        qw = {r["pair_id"]: r for r in rows}
        tot_f = f.with_name(f.name.replace("labels-", "totals-").replace(".jsonl", ".json"))
        tot = json.loads(tot_f.read_text())["segments"] if tot_f.is_file() else []
        n_samples = sum(int(r["n"]) for r in rows)
        info: dict[str, Any] = {
            "labels": str(f), "sha256": g.file_sha(f), "pairs_labelled": len(qw),
            "judge_versions": dict(Counter(str(r.get("judge_version")) for r in rows)),
            "enable_thinking": dict(Counter(str(r.get("enable_thinking")) for r in rows)),
            "served_models": dict(Counter(str(r.get("model")) for r in rows)),
            "endpoints": dict(Counter(str(r.get("endpoint")) for r in rows)),
            "samples": n_samples, "invalid_samples": sum(int(r["invalid"]) for r in rows),
            "finish": dict(sum((Counter(r.get("finish") or {}) for r in rows), Counter())),
            "completion_tokens_per_sample": round(sum(int(r.get("ct") or 0) for r in rows) / n_samples, 1)
            if n_samples else None,
            "reasoning_tokens_per_sample": round(sum(int(r.get("reasoning_tokens") or 0) for r in rows) / n_samples, 1)
            if n_samples else None,
            "totals_segments": [{"status": s["status"], **{k: s["totals"].get(k) for k in
                                 ("wall_s", "samples_per_s", "completion_tokens_per_s", "qwen_samples")}}
                                for s in tot]}
        info["subsets"] = {k: score_variant(qw, ref, pairs, ids, a) for k, ids in subsets.items()}
        res["variants"][v["name"]] = info
    return res


# ------------------------------------------------------------------ the agreement gate (validation)

def majority(verdicts: Sequence[bool | int | None]) -> int | None:
    """The majority of the valid verdicts; None when there is none or the valid ones tie."""
    v = [int(x) for x in verdicts if x is not None]
    if not v or 2 * sum(v) == len(v):
        return None
    return int(2 * sum(v) > len(v))


def gate_label(c_verdicts: Sequence[bool | int | None], e_verdicts: Sequence[bool | int | None],
               c_n: int = 5, e_n: int = 1) -> int | None:
    """The frozen rule (JUDGE-VALIDATION-PLAN.md section 1): judge C's label is the majority of its
    first ``c_n`` stored samples, judge E's the majority of its first ``e_n`` (``e_n`` = 1: the first
    sample). Both labelled and equal: that label. Anything else (disagreement, no C majority, an
    invalid E sample): None, i.e. escalated to the Opus reference."""
    c, e = majority(list(c_verdicts)[:c_n]), majority(list(e_verdicts)[:e_n])
    return c if c is not None and c == e else None


def wilson(k: int, n: int, z: float = 1.959964) -> list[float] | None:
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(mid - half, 4), round(mid + half, 4)]


def _judge_alone(lab: Mapping[str, int | None], ref: Mapping[str, int], ids: Sequence[str]) -> dict[str, Any]:
    """One judge's own labels on ``ids``: a pair without a label counts as an error."""
    ok = sum(lab.get(pid) == ref[pid] for pid in ids)
    fp = sum(lab.get(pid) == 1 and ref[pid] == 0 for pid in ids)
    fn = sum(lab.get(pid) == 0 and ref[pid] == 1 for pid in ids)
    have = [pid for pid in ids if lab.get(pid) is not None]
    return {"pairs": len(ids), "accuracy": round(ok / len(ids), 4) if ids else None,
            "ci95": wilson(ok, len(ids)), "correct": ok, "fp": fp, "fn": fn, "no_label": len(ids) - len(have),
            "kappa": kappa([int(lab[pid]) for pid in have], [ref[pid] for pid in have])}  # type: ignore[arg-type]


def score_gate(ids: Sequence[str], final: Mapping[str, int | None], ref: Mapping[str, int],
               pairs: Mapping[str, Mapping[str, Any]], g_cfg: Mapping[str, Any]) -> dict[str, Any]:
    """The plan's primary metric on ``ids`` (pairs with a unanimous reference): an escalated pair
    takes the reference label. Accuracy, escalation share, the verdict, the accepted subset,
    and per stratum / record kind."""
    esc = [pid for pid in ids if final[pid] is None]
    acc_ids = [pid for pid in ids if final[pid] is not None]
    wrong = [pid for pid in acc_ids if final[pid] != ref[pid]]
    n = len(ids)
    correct = n - len(wrong)
    accuracy = correct / n if n else None
    esc_share = len(esc) / n if n else None
    verdict = None
    if n:
        verdict = "PASS" if (accuracy >= float(g_cfg["min_accuracy"])            # type: ignore[operator]
                             and esc_share <= float(g_cfg["max_escalation"])) else "FAIL"  # type: ignore[operator]

    def by(key: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for val in sorted({str(pairs[pid][key]) for pid in ids}):
            sub = [pid for pid in ids if str(pairs[pid][key]) == val]
            bad = sum(final[pid] is not None and final[pid] != ref[pid] for pid in sub)
            out[val] = {"pairs": len(sub), "accuracy": round(1 - bad / len(sub), 4), "errors": bad,
                        "escalated": sum(final[pid] is None for pid in sub)}
        return out

    return {"pairs": n, "accuracy": round(accuracy, 4) if accuracy is not None else None,
            "accuracy_ci95": wilson(correct, n), "correct": correct,
            "escalated": len(esc), "escalation_share": round(esc_share, 4) if esc_share is not None else None,
            "escalation_ci95": wilson(len(esc), n), "verdict": verdict,
            "accepted": {"pairs": len(acc_ids), "errors": len(wrong),
                         "accuracy": round(1 - len(wrong) / len(acc_ids), 4) if acc_ids else None,
                         "fp": sum(final[pid] == 1 for pid in wrong), "fn": sum(final[pid] == 0 for pid in wrong)},
            "by_stratum": by("stratum"), "by_record_kind": by("record_kind")}


def analyse_gate(cfg: Mapping[str, Any], out: Path, reps: int = 3) -> dict[str, Any]:
    """JUDGE-VALIDATION-PLAN.md section 4, deterministic (the samples as stored): the reference
    classes (unanimous / split / no reference), the gate's accuracy and escalation share on the
    unanimous pairs with and without ``coll_diff``, each judge alone, and what the gate does on
    the split and no-reference pairs."""
    gc = cfg["gate"]
    if not gc["c_labels"] or not gc["e_labels"]:
        raise SystemExit("gate.c_labels and gate.e_labels are required")
    cdir = calib_dir(cfg)
    check_pairs_sha(cfg, cdir / "pairs.jsonl")
    pairs = {p["pair_id"]: p for p in (json.loads(x) for x in (cdir / "pairs.jsonl").read_text().splitlines() if x)}
    opus_path = cdir / "labels-opus.jsonl"
    ref_all = opus_reference(p4.Store(opus_path).rows.values(), reps)
    c_rows = {r["pair_id"]: r for r in p4.Store(Path(gc["c_labels"])).rows.values()}
    e_rows = {r["pair_id"]: r for r in p4.Store(Path(gc["e_labels"])).rows.values()}
    c_n, e_n, e_rn = int(gc["c_n"]), int(gc["e_n"]), int(gc["e_report_n"])
    missing = {"c": sorted(set(pairs) - set(c_rows)), "e": sorted(set(pairs) - set(e_rows))}
    if missing["c"] or missing["e"]:
        raise SystemExit(f"analyse-gate: pairs without a C row {len(missing['c'])}, without an E row "
                         f"{len(missing['e'])}; finish labelling first")
    short = [pid for pid in pairs if int(c_rows[pid]["n"]) < c_n or int(e_rows[pid]["n"]) < max(e_n, e_rn)]
    if short:
        raise SystemExit(f"analyse-gate: {len(short)} pairs have fewer stored samples than the plan reads")
    final = {pid: gate_label(c_rows[pid]["verdicts"], e_rows[pid]["verdicts"], c_n, e_n) for pid in pairs}
    lab_c = {pid: majority(c_rows[pid]["verdicts"][:c_n]) for pid in pairs}
    lab_e1 = {pid: majority(e_rows[pid]["verdicts"][:e_n]) for pid in pairs}
    lab_e3 = {pid: majority(e_rows[pid]["verdicts"][:e_rn]) for pid in pairs}
    una = sorted(pid for pid, r in ref_all.items() if r["unanimous"] and pid in pairs)
    split = sorted(pid for pid, r in ref_all.items() if not r["unanimous"] and pid in pairs)
    noref = sorted(set(pairs) - set(ref_all))
    ref = {pid: int(ref_all[pid]["ref"]) for pid in una}
    subsets = {"all": una, "excl_coll_diff": [pid for pid in una if pairs[pid]["stratum"] != "coll_diff"]}
    res: dict[str, Any] = {
        "t": g.now_iso(), "plan_commit": gc["plan_commit"],
        "rule": {"c": f"majority of the first {c_n} stored samples", "e": f"majority of the first {e_n} stored "
                 "sample(s)", "final": "C and E labelled and equal; otherwise escalated to the Opus reference",
                 "min_accuracy": gc["min_accuracy"], "max_escalation": gc["max_escalation"]},
        "inputs": {"pairs_sha256": g.file_sha(cdir / "pairs.jsonl"), "opus_sha256": g.file_sha(opus_path),
                   "c_labels": str(gc["c_labels"]), "c_sha256": g.file_sha(gc["c_labels"]),
                   "e_labels": str(gc["e_labels"]), "e_sha256": g.file_sha(gc["e_labels"])},
        "judges": {k: {"served_models": dict(Counter(str(r.get("model")) for r in rows.values())),
                       "endpoints": dict(Counter(str(r.get("endpoint")) for r in rows.values())),
                       "judge_versions": dict(Counter(str(r.get("judge_version")) for r in rows.values())),
                       "enable_thinking": dict(Counter(str(r.get("enable_thinking")) for r in rows.values())),
                       "n": dict(Counter(int(r["n"]) for r in rows.values())),
                       "invalid_samples": sum(int(r.get("invalid") or 0) for r in rows.values())}
                   for k, rows in (("C", c_rows), ("E", e_rows))},
        "classes": {"pairs": len(pairs), "unanimous": len(una), "split": len(split), "no_reference": len(noref),
                    "unanimous_by_stratum": dict(Counter(pairs[pid]["stratum"] for pid in una)),
                    "no_reference_by_stratum": dict(Counter(pairs[pid]["stratum"] for pid in noref))},
        "primary": {}, "alone": {}}
    for name, ids in subsets.items():
        res["primary"][name] = score_gate(ids, final, ref, pairs, gc)
        res["alone"][name] = {"C_maj5": _judge_alone(lab_c, ref, ids), "E_n1": _judge_alone(lab_e1, ref, ids),
                              f"E_maj{e_rn}": _judge_alone(lab_e3, ref, ids)}
    split_ref = [pid for pid in split if ref_all[pid]["ref"] is not None]
    res["split"] = {"pairs": len(split), "escalated": sum(final[pid] is None for pid in split),
                    "with_majority": len(split_ref),
                    "accepted_agree_with_majority": sum(final[pid] is not None and final[pid] == ref_all[pid]["ref"]
                                                        for pid in split_ref),
                    "accepted_disagree_with_majority": sum(final[pid] is not None
                                                           and final[pid] != ref_all[pid]["ref"] for pid in split_ref)}
    res["no_reference"] = {"pairs": len(noref), "escalated": sum(final[pid] is None for pid in noref),
                           "accepted_yes": sum(final[pid] == 1 for pid in noref),
                           "accepted_no": sum(final[pid] == 0 for pid in noref)}
    res["escalation_all_pairs"] = {"pairs": len(pairs), "escalated": sum(v is None for v in final.values()),
                                   "share": round(sum(v is None for v in final.values()) / len(pairs), 4)
                                   if pairs else None}
    return res


# ------------------------------------------------------------------ CLI

def tiebreak_subset(cfg: Mapping[str, Any], reps: int = 3) -> list[str]:
    """TIEBREAKER-PLAN.md section 3: the no-reference pairs plus ``subset_unanimous`` Opus-unanimous
    pairs drawn with ``random.Random(subset_seed).sample(sorted(unanimous ids), k)``; sorted."""
    tb = cfg["tiebreak"]
    cdir = calib_dir(cfg)
    check_pairs_sha(cfg, cdir / "pairs.jsonl")
    pids = sorted(json.loads(x)["pair_id"] for x in (cdir / "pairs.jsonl").read_text().splitlines() if x)
    ref = opus_reference(p4.Store(cdir / "labels-opus.jsonl").rows.values(), reps)
    noref = sorted(set(pids) - set(ref))
    una = sorted(pid for pid, r in ref.items() if r["unanimous"] and pid in set(pids))
    k = min(int(tb["subset_unanimous"]), len(una))
    return sorted(noref + random.Random(str(tb["subset_seed"])).sample(una, k))


def _pctl(xs: Sequence[float], q: float) -> float | None:
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))], 2) if s else None


def analyse_tiebreaker(cfg: Mapping[str, Any], reps: int = 3) -> dict[str, Any]:
    """TIEBREAKER-PLAN.md sections 4-5, per ``tiebreak.labels`` entry (``{"name", "labels"}``, a
    ``labels-gw-<tag>.jsonl``): agreement with the Opus-unanimous reference (a pair without a
    valid label counts as a disagreement), kappa on the valid pairs, false yes / no, refusal /
    empty rates on all pairs and on the no-reference pairs, self-consistency on the subset,
    tokens and latency; on the no-reference pairs the labels against the C + E gate (``gate``
    block, optional) and against each other; then the selection rule."""
    tb, gc = cfg["tiebreak"], cfg["gate"]
    cdir = calib_dir(cfg)
    check_pairs_sha(cfg, cdir / "pairs.jsonl")
    pairs = {p["pair_id"]: p for p in (json.loads(x) for x in (cdir / "pairs.jsonl").read_text().splitlines() if x)}
    opus_path = cdir / "labels-opus.jsonl"
    ref_all = opus_reference(p4.Store(opus_path).rows.values(), reps)
    una = sorted(pid for pid, r in ref_all.items() if r["unanimous"] and pid in pairs)
    noref = sorted(set(pairs) - set(ref_all))
    ref = {pid: int(ref_all[pid]["ref"]) for pid in una}
    gate_final: dict[str, int | None] | None = None
    inputs: dict[str, Any] = {"pairs_sha256": g.file_sha(cdir / "pairs.jsonl"), "opus_sha256": g.file_sha(opus_path)}
    if gc["c_labels"] and gc["e_labels"]:
        c_rows = {r["pair_id"]: r for r in p4.Store(Path(gc["c_labels"])).rows.values()}
        e_rows = {r["pair_id"]: r for r in p4.Store(Path(gc["e_labels"])).rows.values()}
        gate_final = {pid: gate_label(c_rows[pid]["verdicts"], e_rows[pid]["verdicts"], int(gc["c_n"]), int(gc["e_n"]))
                      for pid in noref if pid in c_rows and pid in e_rows}
        inputs.update({"c_sha256": g.file_sha(gc["c_labels"]), "e_sha256": g.file_sha(gc["e_labels"])})
    res: dict[str, Any] = {"t": g.now_iso(), "plan_commit": tb["plan_commit"], "inputs": inputs,
                           "classes": {"pairs": len(pairs), "unanimous": len(una), "no_reference": len(noref)},
                           "judges": {}}
    rep1: dict[str, dict[str, int | None]] = {}
    for ent in tb["labels"]:
        name, path = str(ent["name"]), Path(ent["labels"])
        rows = list(p4.Store(path).rows.values())
        by: dict[str, dict[int, Mapping[str, Any]]] = defaultdict(dict)
        for r in rows:
            by[r["pair_id"]][int(r["rep"])] = r
        missing = sorted(set(pairs) - {pid for pid, d in by.items() if 1 in d})
        if missing:
            raise SystemExit(f"analyse-tiebreaker: {name}: {len(missing)} pairs without a rep-1 row")
        lab = {pid: (None if by[pid][1].get("verdict") is None else int(by[pid][1]["verdict"])) for pid in pairs}
        rep1[name] = lab

        def agree(ids: Sequence[str], lab: Mapping[str, int | None] = lab) -> dict[str, Any]:
            valid = [pid for pid in ids if lab[pid] is not None]
            ok = sum(lab[pid] == ref[pid] for pid in valid)
            return {"pairs": len(ids), "agree": ok, "agreement": round(ok / len(ids), 4) if ids else None,
                    "agreement_ci95": wilson(ok, len(ids)), "invalid": len(ids) - len(valid),
                    "false_yes": sum(lab[pid] == 1 and ref[pid] == 0 for pid in valid),
                    "false_no": sum(lab[pid] == 0 and ref[pid] == 1 for pid in valid),
                    "kappa_valid": (round(k, 4) if (k := kappa([int(lab[pid] or 0) for pid in valid],
                                                                 [ref[pid] for pid in valid])) is not None else None),
                    "by_stratum": {s_: {"pairs": len(x), "agree": sum(lab[pid] == ref[pid] for pid in x)}
                                   for s_ in STRATA if (x := [pid for pid in ids if pairs[pid]["stratum"] == s_])}}

        def refusal(ids: Sequence[str], lab: Mapping[str, int | None] = lab,
                    by: Mapping[str, Mapping[int, Mapping[str, Any]]] = by) -> dict[str, Any]:
            inv = [pid for pid in ids if lab[pid] is None]
            return {"pairs": len(ids), "invalid": len(inv), "rate": round(len(inv) / len(ids), 4) if ids else None,
                    "statuses": dict(Counter(str(by[pid][1].get("status")) for pid in inv))}

        sub = sorted(pid for pid, d in by.items() if len(d) > 1)
        rmax = max((len(by[pid]) for pid in sub), default=1)

        def consistency(ids: Sequence[str], by: Mapping[str, Mapping[int, Mapping[str, Any]]] = by,
                        rmax: int = rmax) -> dict[str, Any]:
            full = [pid for pid in ids if all(i in by[pid] for i in range(1, rmax + 1))]
            vs = {pid: [by[pid][i].get("verdict") for i in range(1, rmax + 1)] for pid in full}
            same = sum(None not in v and len(set(v)) == 1 for v in vs.values())
            return {"pairs": len(full), "reps": rmax, "all_valid_and_equal": same,
                    "rate": round(same / len(full), 4) if full else None,
                    "any_invalid": sum(None in v for v in vs.values())}

        pt = [int(r.get("pt") or 0) for r in rows]
        ct = [int(r.get("ct") or 0) for r in rows]
        rt = [int(r.get("reasoning_tokens") or 0) for r in rows]
        lat = [float(r["latency_s"]) for r in rows if r.get("latency_s") is not None]
        n = len(rows) or 1
        res["judges"][name] = {
            "labels": str(path), "labels_sha256": g.file_sha(path), "rows": len(rows),
            "models": dict(Counter(str(r.get("model")) for r in rows)),
            "requested_models": dict(Counter(str(r.get("requested_model")) for r in rows)),
            "judge_versions": dict(Counter(str(r.get("judge_version")) for r in rows)),
            "agreement": {"all": agree(una), "excl_coll_diff": agree([pid for pid in una
                                                                      if pairs[pid]["stratum"] != "coll_diff"])},
            "refusal": {"all": refusal(sorted(pairs)), "no_reference": refusal(noref)},
            "self_consistency": {"no_reference": consistency([pid for pid in sub if pid in set(noref)]),
                                 "unanimous": consistency([pid for pid in sub if pid in ref])},
            "cost": {"calls": len(rows), "prompt_tokens_mean": round(sum(pt) / n, 1),
                     "completion_tokens_mean": round(sum(ct) / n, 1), "reasoning_tokens_mean": round(sum(rt) / n, 1),
                     "total_tokens_mean": round((sum(pt) + sum(ct)) / n, 1), "prompt_tokens": sum(pt),
                     "completion_tokens": sum(ct), "reasoning_tokens": sum(rt),
                     "reissued_empty": sum(int(r.get("attempts") or 1) > 1 for r in rows)},
            "latency_s": {"median": _pctl(lat, 0.5), "p90": _pctl(lat, 0.9), "max": max(lat) if lat else None},
            "no_reference_labels": {"yes": sum(lab[pid] == 1 for pid in noref), "no": sum(lab[pid] == 0 for pid in noref),
                                    "invalid": sum(lab[pid] is None for pid in noref)}}
        if gate_final is not None:
            both = [pid for pid in noref if lab[pid] is not None and gate_final.get(pid) is not None]
            res["judges"][name]["vs_gate_no_reference"] = {
                "pairs_both_labelled": len(both), "agree": sum(lab[pid] == gate_final[pid] for pid in both),
                "judge_yes_gate_no": sum(lab[pid] == 1 and gate_final[pid] == 0 for pid in both),
                "judge_no_gate_yes": sum(lab[pid] == 0 and gate_final[pid] == 1 for pid in both)}
    names = sorted(rep1)
    res["pairwise_no_reference"] = {
        f"{a}|{b}": {"both_labelled": len(x := [pid for pid in noref if rep1[a][pid] is not None
                                                and rep1[b][pid] is not None]),
                     "agree": sum(rep1[a][pid] == rep1[b][pid] for pid in x)}
        for i, a in enumerate(names) for b in names[i + 1:]}
    cap = float(tb["max_filtered_refusal"])
    elig = [nm for nm in names if (res["judges"][nm]["refusal"]["no_reference"]["rate"] or 0) <= cap]
    elig.sort(key=lambda nm: (-res["judges"][nm]["agreement"]["all"]["agree"],
                              res["judges"][nm]["cost"]["total_tokens_mean"]))
    res["selection"] = {"rule": f"highest agreement on the unanimous pairs among judges with refusal <= {cap:.0%} "
                                "on the no-reference pairs; tie -> lower mean total tokens per call",
                        "eligible": elig, "selected": elig[0] if elig else None}
    return res


def provenance() -> dict[str, Any]:
    def git(*a: str) -> str:
        return subprocess.run(["git", "-C", str(_HERE), *a], capture_output=True, text=True).stdout.strip()
    return {"commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain", "--untracked-files=no"))}


def load_config(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    out = Path(raw["out_dir"]).resolve()
    repo = _HERE.parents[2].resolve()
    if out == repo or repo in out.parents:
        raise SystemExit("out_dir is inside the repository; private outputs must live outside it")
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    out.chmod(0o700)
    cfg = {**raw, **merged(raw)}
    return raw, cfg


def main(argv: Sequence[str] | None = None, *, http_factory: Callable[[], Any] | None = None,
         cli_runner: Callable[..., Any] | None = None, prov: Mapping[str, Any] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", required=True, type=Path)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build-calib")
    for name in ("preflight", "label"):
        p = sub.add_parser(name)
        p.add_argument("--backend", choices=BACKENDS, required=True)
        p.add_argument("--reps", type=int, default=3)
        if name == "label":
            p.add_argument("--notice-acknowledged", action="store_true")
            p.add_argument("--resume", action="store_true")
    pa = sub.add_parser("analyse")
    pa.add_argument("--reps", type=int, default=3)
    pv_ = sub.add_parser("analyse-variants")
    pv_.add_argument("--reps", type=int, default=3)
    pg_ = sub.add_parser("analyse-gate")
    pg_.add_argument("--reps", type=int, default=3)
    pt_ = sub.add_parser("tiebreak-subset")
    pt_.add_argument("--reps", type=int, default=3)
    pat = sub.add_parser("analyse-tiebreaker")
    pat.add_argument("--reps", type=int, default=3)
    po = sub.add_parser("overlap")
    po.add_argument("--main-drafts", required=True, type=Path)
    args = ap.parse_args(argv)
    os.umask(0o077)
    _, cfg = load_config(args.config)
    out = Path(cfg["out_dir"])
    pv = dict(prov) if prov is not None else provenance()
    if args.cmd == "analyse":
        res = analyse(cfg, out, args.reps)
        res["provenance"] = pv
        g.write_json_atomic(out / "analysis.json", res)
        print(json.dumps({k: res[k] for k in ("pairs", "opus") if k in res}, indent=1)[:4000])
        return EXIT_OK
    if args.cmd == "analyse-variants":
        res = analyse_variants(cfg, out, args.reps)
        res["provenance"] = pv
        g.write_json_atomic(out / "analysis-variants.json", res)
        print(json.dumps({k: res[k] for k in ("subset_sizes", "overlap")}, indent=1))
        return EXIT_OK
    if args.cmd == "analyse-gate":
        res = analyse_gate(cfg, out, args.reps)
        res["provenance"] = pv
        g.write_json_atomic(out / "analysis-gate.json", res)
        print(json.dumps({"classes": res["classes"], "primary": {k: {x: v[x] for x in (
            "pairs", "accuracy", "escalation_share", "verdict")} for k, v in res["primary"].items()}}, indent=1))
        return EXIT_OK
    if args.cmd == "tiebreak-subset":
        path = out / "tiebreak-subset.txt"
        if path.exists():
            raise SystemExit(f"{path.name} exists; the subset is frozen once written")
        ids = tiebreak_subset(cfg, args.reps)
        path.write_text("".join(x + "\n" for x in ids), encoding="utf-8")
        print(json.dumps({"subset": str(path), "pairs": len(ids), "sha256": g.file_sha(path)}))
        return EXIT_OK
    if args.cmd == "analyse-tiebreaker":
        res = analyse_tiebreaker(cfg, args.reps)
        res["provenance"] = pv
        g.write_json_atomic(out / "analysis-tiebreaker.json", res)
        print(json.dumps({"selection": res["selection"], "judges": {k: {
            "agreement": v["agreement"]["all"]["agreement"], "refusal_no_reference": v["refusal"]["no_reference"]["rate"]}
            for k, v in res["judges"].items()}}, indent=1))
        return EXIT_OK
    if args.cmd == "overlap":
        cdir = calib_dir(cfg)
        check_pairs_sha(cfg, cdir / "pairs.jsonl")
        prs = [json.loads(x) for x in (cdir / "pairs.jsonl").read_text().splitlines() if x]
        ov = {**overlap_records(prs, args.main_drafts), "provenance": pv}
        g.write_json_atomic(out / "overlap.json", ov)
        print(json.dumps({k: ov[k] for k in ("questions", "pairs", "main_drafts_rows")}))
        return EXIT_OK
    world = World(cfg)
    if args.cmd == "build-calib":
        if calib_dir(cfg) != out:
            raise SystemExit("build-calib with calib_dir set: the set is frozen in calib_dir")
        if (out / "pairs.jsonl").exists():
            raise SystemExit("pairs.jsonl exists; the calibration set is frozen once built")
        recs = load_pilot_records(cfg["pilots"], cfg["calib"]["records"])
        pairs, counts = build_calib(recs, world.chunks, world.paper_of, world.glossary, cfg)
        with (out / "pairs.jsonl").open("w", encoding="utf-8") as fh:
            for p in pairs:
                fh.write(json.dumps(p, sort_keys=True) + "\n")
        inputs = {"chunks": [g.file_sha(x) for x in cfg["chunks"]], "glossary": g.file_sha(cfg["glossary"]),
                  "pilots": [{k: g.file_sha(v) for k, v in pl.items() if k in ("drafts", "acceptance")}
                             for pl in cfg["pilots"]]}
        man = {"schema": CALIB_SCHEMA, "counts": counts, "inputs_sha256": inputs, "calib": cfg["calib"],
               "pairs_sha256": g.file_sha(out / "pairs.jsonl"), "provenance": pv, "t": g.now_iso()}
        g.write_json_atomic(out / "calib-manifest.json", man)
        print(json.dumps(counts, indent=1))
        return EXIT_OK
    lab = Labeler(cfg, world, http_factory=http_factory, cli_runner=cli_runner, provenance=pv)
    if args.cmd == "preflight":
        lab.preflight(args.backend, args.reps)
        return EXIT_OK
    if (out / "PAUSE").exists():
        raise SystemExit("PAUSE present: remove it (and run preflight again) to continue")
    st = lab.store(args.backend)
    if st.rows and not args.resume:
        raise SystemExit(f"labels-{lab.key(args.backend)}.jsonl has rows; pass --resume to continue")
    lab.gate(args.backend, args.reps, args.notice_acknowledged)
    try:
        if args.backend == "opus":
            return lab.label_opus(args.reps)
        if args.backend == "cli":
            return lab.label_cli()
        if args.backend == "gateway":
            return lab.label_gw()
        return lab.label_qwen()
    except p4.HardStop as e:
        g.log(f"HARD STOP: {e.reason}")
        return EXIT_HARD_STOP


if __name__ == "__main__":
    sys.exit(main())
