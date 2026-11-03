#!/usr/bin/env python
"""Alias study (OA-1000): the judge pipeline (DRAFT v2 prereg §6, §5.7). Built on the
calibration harness (``glossbench_alias_judge.py``, #894-#899): its prompt (``alias-judge/2``;
the drift reference on ``alias-judge/1``), its Qwen and gateway backends with the served-model
check, the size notice, PAUSE / INFLIGHT, the budget stop and the call caps.

    alias_study_judge.py --config CFG build-pairs --stage {A,B}
    alias_study_judge.py --config CFG status
    alias_study_judge.py --config CFG preflight --batch N --backend {C,E,argon,opus}
    alias_study_judge.py --config CFG label     --batch N --backend B --notice-acknowledged [--resume]
    alias_study_judge.py --config CFG finalize  --batch N
    alias_study_judge.py --config CFG commit-stage-a
    alias_study_judge.py --config CFG check-analysis-gate

**Pairs, on demand (§6.2, R9).** Only top-10 non-gold chunks are judged, and only when a leg
needs them. ``found10``: a record's top 10 in a leg is judged only when its gold chunk is
**outside** that top 10 (when it is inside, the record is a hit whatever the others are).
``rr10``: in a leg where the gold is inside the top 10, the non-gold chunks ranked **above**
it. ``build-pairs --stage A`` reads the 9 stage-A legs (all must be ``complete``) and registers
the pairs their found@10 needs; ``build-pairs --stage B`` (only after ``commit-stage-a``) reads
the 20 stage-B legs and registers the pairs their found@10 needs, then the pairs RR@10 needs in
all 29 legs, stage-A legs included. **A pair is labelled once, whatever first asked for it**:
the (record, chunk) pairs are deduplicated across legs and stages, a pair already registered is
never registered again, and ``finalize`` refuses a pair that already has a final label, so a
stage-A label is never changed by stage B. The registry (``pairs.jsonl``) is append-only: stage
A's rows, then stage B's. A pair's **arm** (drift and spot-check strata only) is the arm its
passage's paper came from: the export the chunk is in (``chunk_exports``: ``base``;
``neighbour``; ``random1`` / ``random2`` -> ``random``). The registry row carries the arm, the
stage and the need, but the prompt does not: :func:`glossbench_alias_judge.judge_prompt` is a
function of the question, its term, the passage, the passage paper's title and that paper's
definitions only, so no label can depend on which cell produced the pair, and the definitions
come from the merged glossary in every cell (a glossary file with placebo rows is refused).
Pairs are labelled in the seeded order ``sha256(261101|alias|judge|order|<pair>)``, stage A's
first, cut into batches of ``batch_size`` (stage B's batches follow stage A's).

**One batch (§6.3).** ``C`` (Qwen3.6-35B-A3B, n 5, thinking, T 0.7, 8192) and ``E``
(Qwen3.8-27B, n 1, the same) label every pair of the batch, local, at most 4 in flight, the
served model checked on every reply (a mismatch stops the run and stores nothing). When C's
majority of its valid samples and E's sample agree, that label is final; otherwise (they
disagree, C has no majority, E is invalid) the pair goes to ``argon`` (Gemini 4 Argon on the
gateway, ``alias-judge/2``, 4096, default temperature, ``fallback_model ""``). ``opus``
(Claude Opus 5.5, ``alias-judge/1``, 1 rep, 2048) re-labels the batch's **drift sample**: 5 %
of the batch, ``0.05 n`` rounded half up, allocated equally across the three arms (the remainder
to base, then neighbour), a stratum smaller than its share taken whole and its shortfall
reallocated equally to the arms that still have pairs, the first pairs of each stratum in the
order ``sha256(261101|alias|drift|<batch>|<pair>)``.

**The retry cap (§6.3, R4).** An Argon or Opus reply that is empty, truncated or unparseable is
re-issued **at most once** (:data:`MAX_GW_ATTEMPTS` answered calls per pair). The count is on
disk: every answered reply is written to ``attempts-gw-<tag>.jsonl`` before anything else
happens to it, and the gateway's calls log is read at start, so a pause, a kill or a resume
never buys a pair a third call. After the cap the pair's row is stored with no verdict: an
Argon pair is then **unjudged** (not answering in the primary analysis), an Opus pair
**no-reference** (filtered from the drift agreement, counted per arm, never a drift failure).
An HTTP error is not a reply: it is re-issued in a second pass, and every call counts against
the caps.

**Finalize and the drift alarm.** ``finalize`` needs C and E on every pair of the batch, the
tiebreaker on every escalated pair and Opus on every sampled pair; it writes the batch's final
labels (``final.jsonl``) and checks the cumulative agreement between the final label (unjudged
= no) and Opus on the Opus-answered pairs: below **0.97 overall** or **0.95 in any arm**, each
scope with at least **100** Opus-answered pairs, writes ``PAUSE`` and ``drift-alarm.json`` and
exits 1. Nothing is labelled while ``PAUSE`` exists; the owner decides the remedy (a deviation).
A batch can be labelled only after every earlier batch is finalized.

**Stage A's commit (§5.7).** ``commit-stage-a`` (every stage-A batch finalized, no PAUSE, no
drift alarm, the 9 stage-A legs complete) writes ``stage-a/`` (frozen copies of the stage-A
leg summaries, the judge summary and the pairs manifest, and ``hits.jsonl``: hit and gold-only
hit per record and stage-A leg), ``stage-a-commit.json`` (private: every stage-A file with its
byte length and sha256; the append-only files are later checked by prefix, so stage B may
append but never rewrite) and ``stage-a-commit.public.json`` (counts and sha256s only, checked
by :func:`alias_study_common.public_problems`; this is what the owner commits to the
repository), and the marker ``<runs_dir>/STAGE-A-COMMITTED``. Stage B's runs and pairs need
the marker and a commit that still verifies. **No analysis before the commit:**
:func:`analysis_gate` (``check-analysis-gate``) refuses until the commit verifies and both owner
spot checks (``spot_checks.records``, ``spot_checks.judge``, §13.4-13.5) exist; the statistics
call it before reading any outcome.

**Gateway calls** carry a size notice per batch and backend (``preflight`` writes it; ``label``
refuses without ``--notice-acknowledged`` and a preflight of the same plan, which includes the
batch and the sha256 of its selection); the harness's budget stop (exit 76, PAUSE) and call cap
(``limits``) apply; and the study's caps (§14 O7, ``caps``): the cumulative calls of a backend
(its calls log, one row per HTTP call, re-issues included) plus the planned selection may not
exceed ``argon_share`` / ``opus_share`` (0.07, §6.5) of the registry's (deduplicated) pairs, nor
``argon_max_calls`` / ``opus_max_calls`` when set; inside a run the remaining cap is the
segment's call cap, so a re-issue can never overrun it (the run pauses at the cap).

**Modes.** As the runner (``dry_run``, ``pilot``, main; a ``STUDY-MODE`` marker in the out dir;
the main run pins the frozen prereg, §9.2). The pilot's labels live in the pilot's own out dir
and are never carried into the main run (§5.6).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import shutil
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from typing import Any

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE / "glossbench"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import alias_study_common as asc  # noqa: E402
import alias_study_run as ar  # noqa: E402
import glossbench_alias_judge as jd  # noqa: E402

EXIT_OK, EXIT_HARD_STOP, EXIT_PAUSED, EXIT_BUDGET = jd.EXIT_OK, jd.EXIT_HARD_STOP, jd.EXIT_PAUSED, jd.EXIT_BUDGET
PAIRS_SCHEMA = "alias-study-pairs/1"
FINAL_SCHEMA = "alias-study-final/1"
COMMIT_SCHEMA = "alias-study-stage-a-commit/1"
COMMIT_PUBLIC_SCHEMA = "alias-study-stage-a-commit-public/1"
HITS_SCHEMA = "alias-study-stage-a-hits/1"
BACKENDS = ("C", "E", "argon", "opus")
NEEDS = ("found10", "rr10")
#: §6.3 (R4): an empty / truncated / unparseable Argon or Opus reply is re-issued at most once
MAX_GW_ATTEMPTS = 2
#: calls-log statuses of a reply that came back (an HTTP error or a budget stop is not one)
ANSWERED = ("ok", "empty", "empty0", "truncated")

#: prereg §6.3, fixed: what the config may not change (the endpoint, the cap and the timeout may)
FIXED: dict[str, dict[str, Any]] = {
    "C": {"model": "Qwen3.6-35B-A3B", "n": 5, "temperature": 0.7, "max_tokens": 8192, "enable_thinking": True,
          "judge_version": "alias-judge/2", "require_served_model": True, "variant": "C"},
    "E": {"model": "Qwen3.8-27B", "n": 1, "temperature": 0.7, "max_tokens": 8192, "enable_thinking": True,
          "judge_version": "alias-judge/2", "require_served_model": True, "variant": "E"},
    "argon": {"model": "Gemini 4 Argon", "judge_version": "alias-judge/2", "max_tokens": 4096, "temperature": None,
              "tag": "argon"},
    "opus": {"model": "Claude Opus 5.5", "judge_version": "alias-judge/1", "max_tokens": 2048, "temperature": None,
             "tag": "opus"},
}
DEFAULTS: dict[str, Any] = {
    "batch_size": 2000,
    "drift": {"share": 0.05, "min_pairs": 100, "overall": 0.97, "per_arm": 0.95},
    "caps": {"argon_share": 0.04, "opus_share": 0.07, "argon_max_calls": None, "opus_max_calls": None},
    "unjudged_flag_share": 0.01,
}
GW_ROLE = {"argon": "tiebreak", "opus": "drift_ref"}
LABEL_FILES = {"C": "labels-qwen-C.jsonl", "E": "labels-qwen-E.jsonl", "argon": "labels-gw-argon.jsonl",
               "opus": "labels-gw-opus.jsonl"}


# ------------------------------------------------------------------ config

def load_config(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return validate_config(raw)


def validate_config(raw: Mapping[str, Any]) -> dict[str, Any]:
    cfg = {**DEFAULTS, **raw}
    for k in ("drift", "caps"):
        cfg[k] = {**DEFAULTS[k], **(raw.get(k) or {})}
    for k in ("out_dir", "runs_dir", "records", "chunk_exports", "glossary", "judge_c", "judge_e", "tiebreak",
              "drift_ref"):
        if not cfg.get(k):
            raise SystemExit(f"judge config has no {k!r}")
    if "base" not in cfg["chunk_exports"] or not set(cfg["chunk_exports"]) <= set(asc.CORPUS_ARMS):
        raise SystemExit(f"chunk_exports: keys from {list(asc.CORPUS_ARMS)}, base required")
    if cfg.get("dry_run") and cfg.get("pilot"):
        raise SystemExit("dry_run and pilot exclude each other")
    out = Path(cfg["out_dir"]).resolve()
    repo = _HERE.parents[2].resolve()
    if out == repo or repo in out.parents:
        raise SystemExit("out_dir is inside the repository; private outputs must live outside it")
    for role, block in (("C", "judge_c"), ("E", "judge_e"), ("argon", "tiebreak"), ("opus", "drift_ref")):
        b = cfg[block]
        for k, v in FIXED[role].items():
            if k in ("tag", "variant"):
                continue
            if b.get(k, v if k in ("temperature",) else object()) != v:
                raise SystemExit(f"{block}.{k} must be {v!r} (prereg §6.3), got {b.get(k)!r}")
        if not b.get("base_url"):
            raise SystemExit(f"{block}.base_url is required")
    if int(cfg["batch_size"]) < 1:
        raise SystemExit("batch_size: at least 1")
    try:
        ar.check_prereg(cfg)
    except ar.f10.Refused as e:
        raise SystemExit(str(e)) from e
    return cfg


def glossary_paths(cfg: Mapping[str, Any]) -> list[Path]:
    """``glossary``: one merged-glossary file, or a list (the base file plus each expanded arm's
    added-paper file, §5.3), read together: the definitions block of a neighbour or random
    passage needs its own paper's entries (§6.2), or that arm's prompts would systematically read
    *(none listed)*, a difference by arm the judge could see."""
    g = cfg["glossary"]
    return [Path(g)] if isinstance(g, str) else [Path(x) for x in g]


def read_glossaries(paths: Sequence[Path]) -> dict[str, list[dict[str, Any]]]:
    """The merged glossaries. A placebo row (``placebo: true``) is refused: the judge's
    definitions block reads the paper's own merged glossary in every cell (§5.3), so it never
    depends on the glossary arm."""
    out: dict[str, list[dict[str, Any]]] = {}
    for p in paths:
        for paper, entries in jd.read_glossary(p).items():
            if any(e.get("placebo") for e in entries):
                raise SystemExit(f"{p.name}: placebo glossary rows; the judge reads the merged glossary in every "
                                 "cell (§5.3, §6.2)")
            out.setdefault(paper, []).extend(entries)
    return out


def definitions_coverage(pairs: Sequence[Mapping[str, Any]], glossary: Mapping[str, Sequence[Any]]) -> dict[str, Any]:
    """Per arm: pairs whose passage paper has at least one glossary entry. A paper may define
    nothing, but an expanded arm far below base points at a missing added-paper glossary file."""
    tot: Counter[str] = Counter()
    cov: Counter[str] = Counter()
    for p in pairs:
        tot[p["arm"]] += 1
        cov[p["arm"]] += int(bool(glossary.get(str(p["c_paper"]))))
    return {a: {"pairs": tot[a], "passage_paper_has_entries": cov[a],
                "share": round(cov[a] / tot[a], 4) if tot[a] else None} for a in sorted(tot)}


def harness_cfg(cfg: Mapping[str, Any], backend: str) -> dict[str, Any]:
    """The calibration harness's config for one backend (``glossbench_alias_judge.merged``)."""
    base: dict[str, Any] = {"out_dir": cfg["out_dir"], "chunks": [], "glossary": str(glossary_paths(cfg)[0]),
                            "paper_key": "path_root", "limits": dict(cfg.get("limits") or {})}
    if backend in ("C", "E"):
        blk = dict(cfg["judge_c" if backend == "C" else "judge_e"])
        q = {k: blk[k] for k in ("base_url", "model", "n", "temperature", "max_tokens", "enable_thinking",
                                 "judge_version", "require_served_model") if k in blk}
        q.update({"variant": backend, "inflight": int(blk.get("inflight", 4)),
                  "timeout_s": float(blk.get("timeout_s", 3600))})
        base["qwen"] = q
    else:
        blk = dict(cfg[GW_ROLE[backend]])
        gw = {k: blk[k] for k in ("base_url", "model", "served_models", "inflight", "empty_retry_s") if k in blk}
        gw.update({"tag": FIXED[backend]["tag"], "judge_version": FIXED[backend]["judge_version"],
                   "max_tokens": FIXED[backend]["max_tokens"], "temperature": None})
        base["judge_gw"] = gw
    merged = jd.merged(base)
    return {**base, **merged}


# ------------------------------------------------------------------ pairs on demand

def leg_rows(runs_dir: Path, legs: Sequence[tuple[str, str, str, int]] | None = None
             ) -> list[tuple[str, dict[str, Any]]]:
    """``(leg id, row)`` for every row of the given legs (default: all 29); refuses when any of
    them is not complete (§6.2: pairs are built after retrieval)."""
    import found10 as f10

    runs_dir = Path(runs_dir)
    legs = list(legs) if legs is not None else ar.study_legs()
    st = ar.run_status(runs_dir)
    bad = {asc.leg_id(*lg): st[asc.leg_id(*lg)] for lg in legs if st[asc.leg_id(*lg)] != "complete"}
    if bad:
        raise SystemExit(f"{len(bad)} leg(s) are not complete: {sorted(bad)[:6]}…; pairs are built after retrieval")
    out: list[tuple[str, dict[str, Any]]] = []
    for c, g, r, rep in legs:
        rows_path, _ = ar.leg_files(runs_dir, c, g, r, rep)
        cond = asc.RETRIEVERS[r]
        for row in f10.read_jsonl_repair_tail(rows_path):
            if row["condition"] == cond:
                out.append((asc.leg_id(c, g, r, rep), row))
    return out


def chunk_arms(cfg: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """All chunks of the exports and each chunk's arm group (§6.2)."""
    chunks: dict[str, dict[str, Any]] = {}
    arm_of: dict[str, str] = {}
    for arm in asc.CORPUS_ARMS:
        path = cfg["chunk_exports"].get(arm)
        if not path:
            continue
        group = asc.arm_group(arm)
        for cid, row in asc.read_chunk_exports([path]).items():
            prev = arm_of.get(cid)
            if prev is not None and prev != group and prev != "base":
                raise SystemExit(f"chunk {cid[:8]}… is in the {prev} and the {group} exports")
            if prev is None:
                arm_of[cid] = group
            if cid in chunks and chunks[cid]["content"] != row["content"]:
                raise SystemExit(f"chunk {cid[:8]}… has other content in the {arm} export")
            chunks.setdefault(cid, row)
    return chunks, arm_of


def pair_id(record_id: str, chunk_id: str) -> str:
    return asc.sha_text(f"{record_id}|{chunk_id}")[:16]


def demanded(rec: Mapping[str, Any], row: Mapping[str, Any], need: str) -> list[str]:
    """§6.2, judging on demand: the non-gold chunk ids one leg row asks a label for.
    ``found10``: the whole top 10 when the gold is outside it, nothing when it is inside.
    ``rr10``: the chunks ranked above the gold when the gold is inside the top 10."""
    if need not in NEEDS:
        raise ValueError(need)
    if row.get("failed"):
        return []
    gold = rec["gold_chunks"][0]
    top = list((row.get("ranked_chunk_ids") or [])[:asc.FOUND_K])
    if gold in top:
        return top[:top.index(gold)] if need == "rr10" else []
    return top if need == "found10" else []


def build_pairs(records: Sequence[Mapping[str, Any]], rows: Sequence[tuple[str, Mapping[str, Any]]],
                chunks: Mapping[str, Mapping[str, Any]], arm_of: Mapping[str, str], *,
                needs: Mapping[str, Sequence[str]] | None = None, stage: str = "A",
                known: set[str] | frozenset[str] = frozenset()) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The new deduplicated (record, chunk) pairs the legs ask for and their counts.
    ``needs``: leg id -> the needs it asks for, in order (default: ``found10`` for every leg);
    the found@10 needs of all legs are served before any RR@10 need, and a pair keeps the need
    that first asked for it. ``known``: pair ids already registered (an earlier stage); they
    are never registered again."""
    by_id = {r["record_id"]: r for r in records}
    pairs: dict[str, dict[str, Any]] = {}
    instances: Counter[str] = Counter()
    already: Counter[str] = Counter()
    per_leg: Counter[str] = Counter()
    for need in NEEDS:
        for leg, row in rows:
            if need not in (needs or {}).get(leg, ("found10",)):
                continue
            rec = by_id.get(row["record_id"])
            if rec is None:
                raise SystemExit("a run row names a record that is not in the record file")
            for cid in demanded(rec, row, need):
                instances[need] += 1
                per_leg[leg] += 1
                pid = pair_id(rec["record_id"], cid)
                if pid in known:
                    already[need] += 1
                    continue
                if pid in pairs:
                    continue
                ch = chunks.get(cid)
                if ch is None:
                    raise SystemExit(f"a retrieved chunk ({cid[:8]}…) is in no chunk export")
                pairs[pid] = {"pair_id": pid, "record_id": rec["record_id"], "chunk_id": cid,
                              "content_sha256": asc.sha_text(ch["content"]), "question": rec["question"],
                              "term": rec["primary"], "q_paper": rec["paper"], "c_paper": ch["paper"],
                              "record_kind": rec["kind"], "arm": arm_of[cid], "stage": stage, "need": need,
                              "order": asc.seeded_key(asc.SEEDS["judge_order"], pid)}
    out = sorted(pairs.values(), key=lambda p: p["order"])
    counts = {"pairs": len(out), "instances_before_dedup": sum(instances.values()),
              "instances_per_need": dict(instances), "already_registered": dict(already), "legs": len(per_leg),
              "per_arm": dict(Counter(p["arm"] for p in out)), "per_need": dict(Counter(p["need"] for p in out)),
              "same_paper": sum(p["c_paper"] == p["q_paper"] for p in out)}
    return out, counts


def batches(pairs: Sequence[Mapping[str, Any]], size: int) -> list[list[str]]:
    ids = [p["pair_id"] for p in pairs]
    return [ids[i:i + size] for i in range(0, len(ids), size)]


def drift_size(n: int, share: float) -> int:
    """§6.3: ``share x n`` rounded half up (100.5 -> 101), in exact decimal arithmetic so that no
    float product of a configured share lands just below a half."""
    x = Fraction(str(share)) * n
    return math.floor(x + Fraction(1, 2))


def drift_allocation(n_total: int, sizes: Mapping[str, int]) -> dict[str, int]:
    """§6.3: ``n_total`` split equally across the arms (the remainder to base, then neighbour); a
    stratum smaller than its share is taken whole and its shortfall is reallocated equally to
    the arms that still have pairs (the remainder again in arm order), until ``n_total`` is
    reached or every stratum is exhausted."""
    per = dict.fromkeys(asc.ARM_GROUPS, 0)
    left = min(n_total, sum(int(sizes.get(a, 0)) for a in asc.ARM_GROUPS))
    while left > 0:
        open_ = [a for a in asc.ARM_GROUPS if per[a] < int(sizes.get(a, 0))]
        share = dict.fromkeys(open_, left // len(open_))
        for a in open_[: left % len(open_)]:
            share[a] += 1
        for a in open_:
            take = min(share[a], int(sizes.get(a, 0)) - per[a])
            per[a] += take
            left -= take
    return per


def drift_sample(batch_no: int, pairs: Sequence[Mapping[str, Any]], share: float) -> list[str]:
    """§6.3: :func:`drift_size` pairs allocated by :func:`drift_allocation` (equal across the
    three arms, a capped stratum's shortfall reallocated), the first pairs of each stratum in the
    order ``sha256(261101|alias|drift|<batch>|<pair>)``."""
    seed = asc.SEEDS["drift"]
    strata = {a: sorted((p for p in pairs if p["arm"] == a),
                        key=lambda p: asc.seeded_key(seed, str(batch_no), p["pair_id"]))
              for a in asc.ARM_GROUPS}
    per = drift_allocation(drift_size(len(pairs), share), {a: len(v) for a, v in strata.items()})
    out: list[str] = []
    for a in asc.ARM_GROUPS:
        out += [p["pair_id"] for p in strata[a][: per[a]]]
    return out


# ------------------------------------------------------------------ labels -> final

def final_label(c_row: Mapping[str, Any] | None, e_row: Mapping[str, Any] | None,
                tb: Mapping[str, Any] | None, tb2: Mapping[str, Any] | None = None) -> tuple[int | None, str]:
    """The composed rule (§6.3): C + E agreement, else the tiebreaker, else unjudged. ``tb2``
    is the separate re-issue file of the first version (``tiebreak_r2``); the capped labeller
    keeps its one re-issue inside the tiebreaker's own row."""
    c = jd.majority((c_row or {}).get("verdicts") or [])
    e = jd.majority(((e_row or {}).get("verdicts") or [])[:1])
    if c is not None and c == e:
        return c, "agree"
    for row, src in ((tb, "tiebreak"), (tb2, "tiebreak_r2")):
        v = (row or {}).get("verdict")
        if v is not None:
            return int(bool(v)), src
    return None, "unjudged"


def escalated(c_row: Mapping[str, Any] | None, e_row: Mapping[str, Any] | None) -> bool:
    return final_label(c_row, e_row, None, None)[1] != "agree"


def drift_stats(final: Mapping[str, Mapping[str, Any]], opus: Mapping[str, Mapping[str, Any]],
                sampled: Sequence[str], arm_of_pair: Mapping[str, str], dcfg: Mapping[str, Any]) -> dict[str, Any]:
    """Cumulative agreement and the alarm (§6.3), per arm false yes (§6.3, D2)."""
    scopes: dict[str, dict[str, int]] = {s: Counter() for s in ("overall", *asc.ARM_GROUPS)}
    for pid in sampled:
        arm = arm_of_pair[pid]
        o = (opus.get(pid) or {}).get("verdict")
        fl = (final.get(pid) or {}).get("label")
        for s in ("overall", arm):
            sc = scopes[s]
            sc["sampled"] += 1
            if o is None:
                sc["filtered"] += 1
                continue
            o = int(bool(o))
            f = int(fl or 0)
            sc["answered"] += 1
            sc["agree"] += int(f == o)
            if o == 0:
                sc["opus_no"] += 1
                sc["final_yes_on_opus_no"] += int(f == 1)
            else:
                sc["opus_yes"] += 1
            if f == 1:
                sc["final_yes"] += 1
                sc["opus_no_on_final_yes"] += int(o == 0)
    out: dict[str, Any] = {"scopes": {}, "alarm": False, "alarm_reasons": []}
    for s, sc in scopes.items():
        thr = float(dcfg["overall"] if s == "overall" else dcfg["per_arm"])
        n = sc["answered"]
        agr = sc["agree"] / n if n else None
        fires = n >= int(dcfg["min_pairs"]) and agr is not None and agr < thr
        keys = ("sampled", "filtered", "answered", "agree", "opus_no", "opus_yes", "final_yes_on_opus_no",
                "final_yes", "opus_no_on_final_yes")
        out["scopes"][s] = {**{k: int(sc[k]) for k in keys}, "agreement": agr, "threshold": thr,
                            "evaluable": n >= int(dcfg["min_pairs"]), "fires": fires,
                            "filtered_share": (sc["filtered"] / sc["sampled"]) if sc["sampled"] else None,
                            "false_yes": {"k": int(sc["final_yes_on_opus_no"]), "n": int(sc["opus_no"]),
                                          "rate": (sc["final_yes_on_opus_no"] / sc["opus_no"]) if sc["opus_no"]
                                          else None, "wilson95": jd.wilson(sc["final_yes_on_opus_no"],
                                                                           sc["opus_no"])},
                            # §3.4 (R4): among final *answers* labels that Opus answered, the share
                            # Opus calls *not* (the false-yes bound's U_r reads its Wilson upper limit)
                            "false_yes_among_answers": {
                                "k": int(sc["opus_no_on_final_yes"]), "n": int(sc["final_yes"]),
                                "rate": (sc["opus_no_on_final_yes"] / sc["final_yes"]) if sc["final_yes"] else None,
                                "wilson95": jd.wilson(sc["opus_no_on_final_yes"], sc["final_yes"])}}
        if fires:
            out["alarm"] = True
            out["alarm_reasons"].append(f"{s}: agreement {agr:.4f} < {thr} on {n} Opus-answered pairs")
    return out


# ------------------------------------------------------------------ the pipeline object

class StudyWorld(jd.World):
    """The harness's World over the study's registry: ``pairs()`` is the current selection."""

    def __init__(self, cfg: Mapping[str, Any], chunks: Mapping[str, Mapping[str, Any]],
                 glossary: Mapping[str, list[dict[str, Any]]], selection: Sequence[Mapping[str, Any]]) -> None:
        self.cfg = cfg
        self.out = Path(cfg["out_dir"])
        self.calib_dir = self.out
        self.chunks = {cid: SimpleNamespace(content=c["content"]) for cid, c in chunks.items()}  # type: ignore[misc]
        self.titles = {}
        for c in chunks.values():
            if c.get("title"):
                self.titles.setdefault(c["paper"], c["title"])
        self.glossary = dict(glossary)
        self._selection = list(selection)

    def pairs(self) -> list[dict[str, Any]]:
        for p in self._selection:
            c = self.chunks.get(p["chunk_id"])
            if c is None or asc.sha_text(c.content) != p["content_sha256"]:
                raise jd.p4.HardStop(f"pair {p['pair_id']}: chunk text differs from the registry")
        return [dict(p) for p in self._selection]


class StudyLabeler(jd.Labeler):
    """The harness's Labeler with the batch and the selection in the plan (so a size notice for
    one batch never admits another), and the capped gateway labeller (§6.3, R4)."""

    def __init__(self, *a: Any, batch: int, role: str, max_calls: int | None = None, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.batch, self.role = batch, role
        self.max_calls = max_calls

    def plan(self, backend: str, reps: int) -> dict[str, Any]:
        p = super().plan(backend, reps)
        sel = sorted(x["pair_id"] for x in self.w.pairs())
        p.update({"study_batch": self.batch, "study_role": self.role,
                  "selection_sha256": asc.sha_text("".join(f"{x}\n" for x in sel))})
        if backend == "gateway":
            p.update({"retry_cap_answered_calls_per_pair": MAX_GW_ATTEMPTS,
                      "max_calls_worst_case": MAX_GW_ATTEMPTS * len(sel)})
        return p

    def label_gw_capped(self) -> int:
        """One gateway judge (Argon or Opus) over the selection, at most :data:`MAX_GW_ATTEMPTS`
        answered calls per pair, counted from disk (module docstring, *The retry cap*). A reply
        whose model id is not the requested one stops the run and is not stored."""
        jg, lim = self.cfg["judge_gw"], self.cfg["limits"]
        if not jg.get("base_url") or not jg.get("model"):
            raise SystemExit("judge_gw.base_url and judge_gw.model are required")
        key = self.key("gateway")
        url = str(jg["base_url"]).rstrip("/").removesuffix("/v1")
        served = set(self.gw_served())
        system = jd.JUDGE_SYSTEMS[jg["judge_version"]]
        st = self.store("gateway")
        att = jd.p4.Store(self.out / f"attempts-{key}.jsonl")
        calls_log = self.out / f"calls-{key}.jsonl"
        answered: Counter[str] = Counter(str(r.get("key")) for r in jd.p4.read_jsonl_repair_tail(calls_log)
                                         if r.get("status") in ANSWERED)
        prev: Counter[str] = Counter()
        tp = self.out / f"totals-{key}.json"
        if tp.is_file():
            segs = json.loads(tp.read_text())["segments"]
            if segs:
                prev.update({k: v for k, v in segs[-1]["totals"].items() if isinstance(v, int)
                             and k.startswith("gateway")})
        control = jd.dr.R2Control({**lim, "max_calls": self.max_calls}, self.out / "PAUSE", dict(prev))
        budget: dict[str, Any] = {}
        gw = jd.ad.AliasGateway(url, control, {**lim, "empty_retries": 1,
                                               "empty_backoff_s": float(jg["empty_retry_s"])},
                                calls_log, on_budget=lambda ev: budget.setdefault("ev", ev))
        spec = {"model": jg["model"], "tag": key, "max_tokens": int(jg["max_tokens"]),
                "temperature": jg.get("temperature")}
        ctl = jd.Ctl(self.out, int(jg["inflight"]))
        seg: Counter[str] = Counter()
        by_id = {p["pair_id"]: p for p in self.w.pairs()}

        def lk(pid: str) -> str:
            return f"{key}|{pid}|r1"

        def attempts_of(pid: str) -> list[dict[str, Any]]:
            out = []
            for i in range(1, MAX_GW_ATTEMPTS + 1):
                a = att.get(f"{lk(pid)}|a{i}")
                if a is not None:
                    out.append(a)
            return out

        def todo() -> list[str]:
            return [pid for pid in by_id if lk(pid) not in st]

        def finish(pid: str, attempts: Sequence[Mapping[str, Any]], used: int, prompt: str, cut: int) -> None:
            best = next((a for a in attempts if a.get("verdict") is not None), None)
            src = best or (attempts[-1] if attempts else {})
            st.put({"k": lk(pid), "pair_id": pid, "rep": 1, "backend": "gateway",
                    "status": src.get("status") or "no_reply_recorded",
                    "verdict": None if best is None else best["verdict"], "reason": src.get("reason") or "",
                    "text": src.get("text"), "model": src.get("model"), "requested_model": spec["model"],
                    "pt": src.get("pt"), "ct": src.get("ct"), "finish": src.get("finish"),
                    "attempts": used, "attempt_statuses": [a.get("status") for a in attempts],
                    "retry_cap": MAX_GW_ATTEMPTS, "no_verdict_after_cap": best is None,
                    "defs_cut": cut, "prompt_sha256": jd.g.sha_text(prompt), "judge_version": jg["judge_version"],
                    "system_sha256": jd.g.sha_text(system), "max_tokens": int(jg["max_tokens"]),
                    "temperature": jg.get("temperature"), "endpoint": str(jg["base_url"]), "t": jd.g.now_iso()})

        def job(pid: str) -> Callable[[], Any]:
            async def fn() -> None:
                if ctl.stopped() or control.stopped():
                    return
                k = lk(pid)
                prompt, cut = self.w.prompt(by_id[pid])
                msgs = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
                attempts = attempts_of(pid)
                used = max(answered[k], len(attempts))
                if any(a.get("verdict") is not None for a in attempts) or used >= MAX_GW_ATTEMPTS:
                    seg["gateway_cap_reached_on_resume"] += int(used >= MAX_GW_ATTEMPTS)
                    finish(pid, attempts, used, prompt, cut)
                    return
                while used < MAX_GW_ATTEMPTS:
                    res = await gw.once(self.http, spec, msgs, "judge", k)
                    if res is None:
                        if control.reason:
                            ctl.stop(control.reason)
                        return
                    if res["status"] == "error":
                        control.fail(str(res.get("error")))
                        if control.reason:
                            ctl.stop(control.reason)
                        return
                    if res.get("model") not in served:
                        seg["gateway_model_mismatch"] += 1
                        ctl.stop(f"served_model_mismatch:{res.get('model')}")
                        return
                    control.ok()
                    used += 1
                    answered[k] += 1
                    verdict, reason = jd.parse_judgement(res.get("text") or "") if res["status"] == "ok" \
                        else (None, "")
                    a = {"k": f"{k}|a{used}", "pair_id": pid, "attempt": used, "status": res["status"],
                         "verdict": verdict, "reason": reason, "text": res.get("text"), "model": res.get("model"),
                         "pt": res.get("pt"), "ct": res.get("ct"), "finish": res.get("finish"),
                         "reasoning_tokens": res.get("rt"), "t": jd.g.now_iso()}
                    att.put(a)
                    attempts.append(a)
                    if verdict is not None:
                        break
                    if used < MAX_GW_ATTEMPTS:
                        seg["gateway_no_verdict_reissued"] += 1
                        await asyncio.sleep(float(jg["empty_retry_s"]))
                seg["gateway_no_verdict_after_cap"] += int(all(x.get("verdict") is None for x in attempts))
                finish(pid, attempts, used, prompt, cut)
            return fn

        async def go(items: list[str]) -> None:
            self.http = self.http_factory()
            try:
                await jd.run_async([job(pid) for pid in items], ctl)
            finally:
                close = getattr(self.http, "aclose", None)
                if close is not None:
                    await close()

        items = todo()
        jd.notice_line("GATEWAY", f"label backend=gateway model={jg['model']}: starting {len(items)} pairs "
                       f"(<= {MAX_GW_ATTEMPTS} answered calls each; {len(st.rows)} stored, <= {ctl.inflight()} "
                       f"in flight, segment call cap {self.max_calls})", self.log_path)
        for _ in range(2):                         # a failed call is re-issued once in a second pass
            if not items or ctl.stopped() or control.stopped():
                break
            asyncio.run(go(items))
            items = todo()
        tot = {**{k: int(v) for k, v in control.totals()["since_start"].items()}, **{k: int(v) for k, v in seg.items()}}
        stopped = ctl.reason or control.reason
        if budget.get("ev"):
            ev = {**budget["ev"], "totals": tot}
            jd.g.write_json_atomic(self.out / f"budget-stop-{key}.json", ev)
            (self.out / "PAUSE").write_text(f"budget stop {ev['t']}: HTTP {ev['http_status']} ({ev['why']}); "
                                            "remove, preflight, then label --resume\n", encoding="utf-8")
            self.write_totals("gateway", tot, f"budget_stop:{ev['why']}")
            jd.notice_line("GATEWAY", f"label backend=gateway model={jg['model']} BUDGET STOP (HTTP "
                           f"{ev['http_status']}, {ev['why']}); totals {json.dumps(tot, sort_keys=True)}",
                           self.log_path)
            return EXIT_BUDGET
        left = todo()
        status = f"paused:{stopped}" if stopped else ("done" if not left else f"paused:calls_failed:{len(left)}")
        self.write_totals("gateway", tot, status)
        jd.notice_line("GATEWAY", f"label backend=gateway model={jg['model']} {status}: totals "
                       f"{json.dumps(tot, sort_keys=True)}; {len(st.rows)} labels stored", self.log_path)
        return EXIT_OK if status == "done" else (EXIT_HARD_STOP if str(stopped).startswith("served_model")
                                                 else EXIT_PAUSED)


def prefix_sha(path: Path, n: int) -> str | None:
    """sha256 of the first ``n`` bytes of ``path``; None when the file is shorter or absent."""
    if not path.is_file():
        return None
    with path.open("rb") as fh:
        data = fh.read(n)
    return None if len(data) < n else hashlib.sha256(data).hexdigest()


def file_entry(name: str, path: Path, mode: str) -> dict[str, Any]:
    """One file of the stage-A commit: ``prefix`` (append-only: stage B may append) or ``exact``."""
    return {"name": name, "path": str(path.resolve()), "mode": mode, "bytes": path.stat().st_size,
            "sha256": asc.sha256_file(path)}


def verify_stage_a_commit(path: Path, expect_sha256: str | None = None) -> dict[str, Any]:
    """Every file of the commit still holds the bytes it held when committed (a prefix for the
    append-only files), and the batches file still starts with stage A's batches. Raises
    SystemExit naming what differs."""
    if not path.is_file():
        raise SystemExit("stage-A commit file is missing")
    sha = asc.sha256_file(path)
    if expect_sha256 is not None and sha != expect_sha256:
        raise SystemExit("stage-A commit file differs from the sha256 the marker recorded")
    c = json.loads(path.read_text(encoding="utf-8"))
    if c.get("schema") != COMMIT_SCHEMA:
        raise SystemExit(f"stage-A commit schema {c.get('schema')!r}")
    bad = []
    for f in c["files"]:
        p = Path(f["path"])
        if f["mode"] == "exact":
            ok = p.is_file() and asc.sha256_file(p) == f["sha256"]
        else:
            ok = prefix_sha(p, int(f["bytes"])) == f["sha256"]
        if not ok:
            bad.append(f["name"])
    bl = Path(c["batches_file"])
    got = json.loads(bl.read_text())["batches"] if bl.is_file() else []
    if got[: len(c["batches_A"])] != c["batches_A"]:
        bad.append("batches (stage A's batches changed)")
    if bad:
        raise SystemExit(f"stage-A commit: {len(bad)} file(s) changed since the commit: {bad[:8]}")
    return {"commit_sha256": sha, "files": len(c["files"]), "batches_A": len(c["batches_A"])}


def analysis_gate(runs_dir: Path, spot_checks: Mapping[str, Any] | None = None, *, dry_run: bool = False
                  ) -> dict[str, Any]:
    """§5.7: no analysis is run until stage A is committed and both owner spot checks (§13.4
    records, §13.5 judged pairs) are committed. Raises SystemExit; returns what it checked. The
    statistics call it before they read any outcome. A dry run waives the spot checks only."""
    marker = ar.read_commit_marker(Path(runs_dir))
    if marker is None:
        raise SystemExit("no analysis before stage A is committed (§5.7): run commit-stage-a")
    v = verify_stage_a_commit(Path(marker["commit"]), marker.get("commit_sha256"))
    spot: dict[str, Any] = {}
    for k in ("records", "judge"):
        p = (spot_checks or {}).get(k)
        if p and Path(p).is_file():
            spot[k] = asc.sha256_file(p)
        elif not dry_run:
            raise SystemExit(f"no analysis before the owner's {k} spot check is committed (§5.7, §13.4-13.5): "
                             f"spot_checks.{k}")
    return {"allowed": True, "stage_a_commit_sha256": v["commit_sha256"], "spot_checks_sha256": spot,
            "spot_checks_waived": bool(dry_run and len(spot) < 2)}


class Pipeline:
    def __init__(self, cfg: dict[str, Any], *, http_factory: Callable[[], Any] | None = None,
                 provenance: Mapping[str, Any] | None = None) -> None:
        self.cfg = cfg
        self.out = Path(cfg["out_dir"])
        self.out.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.mode = ar.config_mode(cfg)
        try:
            ar.ensure_mode(self.out, self.mode)
            runs = Path(cfg["runs_dir"])
            rm = runs / ar.MODE_MARKER
            if rm.is_file() and rm.read_text(encoding="utf-8").strip() != self.mode:
                raise ar.f10.Refused(f"runs_dir is a {rm.read_text().strip()!r} directory, this config is "
                                     f"{self.mode!r} (§5.6)")
        except ar.f10.Refused as e:
            raise SystemExit(str(e)) from e
        self.http_factory = http_factory
        self.prov = dict(provenance or {})
        self._chunks: dict[str, dict[str, Any]] | None = None
        self._glossary: dict[str, list[dict[str, Any]]] | None = None

    # -- registry
    @property
    def pairs_path(self) -> Path:
        return self.out / "pairs.jsonl"

    @property
    def runs(self) -> Path:
        return Path(self.cfg["runs_dir"])

    def manifest(self) -> dict[str, Any]:
        return json.loads((self.out / "pairs-manifest.json").read_text())

    def registry(self) -> list[dict[str, Any]]:
        if not self.pairs_path.is_file():
            raise SystemExit("pairs.jsonl missing: run build-pairs --stage A first")
        if asc.sha256_file(self.pairs_path) != self.manifest()["pairs_sha256"]:
            raise jd.p4.HardStop("pairs.jsonl differs from its manifest")
        return asc.read_jsonl(self.pairs_path)

    def batch_doc(self) -> dict[str, Any]:
        return json.loads((self.out / "batches.json").read_text())

    def batch_list(self) -> list[list[str]]:
        return self.batch_doc()["batches"]

    def batch_stage(self, batch_no: int) -> str:
        for s, nos in (self.batch_doc().get("stages") or {}).items():
            if batch_no in nos:
                return str(s)
        return "A"

    def chunks(self) -> dict[str, dict[str, Any]]:
        if self._chunks is None:
            self._chunks, _ = chunk_arms(self.cfg)
        return self._chunks

    def glossary(self) -> dict[str, list[dict[str, Any]]]:
        if self._glossary is None:
            self._glossary = read_glossaries(glossary_paths(self.cfg))
        return self._glossary

    def committed(self) -> dict[str, Any] | None:
        return ar.read_commit_marker(self.runs)

    def build(self, stage: str) -> dict[str, Any]:
        """``build-pairs --stage A|B`` (module docstring, *Pairs, on demand*)."""
        if stage not in ar.STAGES:
            raise SystemExit("--stage A or B (§5.7)")
        recs = asc.load_records(self.cfg["records"])
        if stage == "A":
            if self.pairs_path.exists():
                raise SystemExit("pairs.jsonl exists; stage A's registry is frozen once built")
            legs = ar.stage_legs("A")
            needs = {asc.leg_id(*lg): ("found10",) for lg in legs}
            known: set[str] = set()
            prev_man: dict[str, Any] = {}
            prev_batches: list[list[str]] = []
            stages: dict[str, list[int]] = {}
        else:
            marker = self.committed()
            if marker is None:
                raise SystemExit("stage B's pairs need stage A committed first (§5.7): run commit-stage-a")
            verify_stage_a_commit(Path(marker["commit"]), marker.get("commit_sha256"))
            prev_man = self.manifest()
            if "B" in (prev_man.get("stages") or {}):
                raise SystemExit("stage B's pairs are registered; the registry is frozen once built")
            legs = ar.study_legs()
            needs = {asc.leg_id(*lg): (("found10", "rr10") if ar.leg_stage(*lg) == "B" else ("rr10",))
                     for lg in legs}
            known = {p["pair_id"] for p in self.registry()}
            doc = self.batch_doc()
            prev_batches, stages = doc["batches"], dict(doc.get("stages") or {"A": list(range(1, len(doc["batches"])
                                                                                            + 1))})
        rows = leg_rows(self.runs, legs)
        chunks, arm_of = chunk_arms(self.cfg)
        self._chunks = chunks
        pairs, counts = build_pairs(recs, rows, chunks, arm_of, needs=needs, stage=stage, known=known)
        start_bytes = self.pairs_path.stat().st_size if self.pairs_path.exists() else 0
        with self.pairs_path.open("a", encoding="utf-8") as fh:
            for p in pairs:
                fh.write(json.dumps(p, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        new = batches(pairs, int(self.cfg["batch_size"]))
        first = len(prev_batches) + 1
        stages[stage] = list(range(first, first + len(new)))
        jd.g.write_json_atomic(self.out / "batches.json", {"batch_size": int(self.cfg["batch_size"]),
                                                           "batches": prev_batches + new, "stages": stages,
                                                           "seed": asc.SEEDS["judge_order"]})
        counts["definitions_coverage"] = definitions_coverage(pairs, self.glossary())
        st_man = {**(prev_man.get("stages") or {}),
                  stage: {"counts": counts, "batches": stages[stage], "legs": [asc.leg_id(*lg) for lg in legs],
                          "needs": {k: list(v) for k, v in needs.items()},
                          "pairs_bytes_from": start_bytes, "pairs_bytes_to": self.pairs_path.stat().st_size,
                          "t": jd.g.now_iso()}}
        man = {"schema": PAIRS_SCHEMA, "counts": counts if stage == "A" else prev_man.get("counts"),
               "stages": st_man, "batches": len(prev_batches) + len(new), "mode": self.mode,
               "pairs_sha256": asc.sha256_file(self.pairs_path),
               "inputs_sha256": {"records": asc.sha256_file(self.cfg["records"]),
                                 "glossaries": [asc.sha256_file(x) for x in glossary_paths(self.cfg)],
                                 **{f"chunks_{a}": asc.sha256_file(p) for a, p in self.cfg["chunk_exports"].items()}},
               "provenance": self.prov, "t": jd.g.now_iso()}
        jd.g.write_json_atomic(self.out / "pairs-manifest.json", man)
        return {**man, "counts": counts}

    # -- labels on disk
    def labels(self, backend: str) -> dict[str, dict[str, Any]]:
        rows = jd.p4.read_jsonl_repair_tail(self.out / LABEL_FILES[backend])
        return {r["pair_id"]: r for r in rows}

    def finalized(self) -> set[int]:
        return {int(r["batch"]) for r in jd.p4.read_jsonl_repair_tail(self.out / "final.jsonl")
                if r.get("k", "").startswith("batch|")}

    # -- selection per backend
    def selection(self, batch_no: int, backend: str) -> list[dict[str, Any]]:
        bl = self.batch_list()
        if not 1 <= batch_no <= len(bl):
            raise SystemExit(f"--batch {batch_no}: 1..{len(bl)}")
        ids = set(bl[batch_no - 1])
        reg = [p for p in self.registry() if p["pair_id"] in ids]
        if backend in ("C", "E"):
            return reg
        if backend == "opus":
            want = set(drift_sample(batch_no, reg, float(self.cfg["drift"]["share"])))
            return [p for p in reg if p["pair_id"] in want]
        c, e = self.labels("C"), self.labels("E")
        missing = [p["pair_id"] for p in reg if p["pair_id"] not in c or p["pair_id"] not in e]
        if missing:
            raise SystemExit(f"batch {batch_no}: {len(missing)} pair(s) lack a C or E label; the tiebreaker "
                             "set is known only after both")
        return [p for p in reg if escalated(c[p["pair_id"]], e[p["pair_id"]])]

    def labeler(self, batch_no: int, backend: str, max_calls: int | None = None) -> StudyLabeler:
        hc = harness_cfg(self.cfg, backend)
        world = StudyWorld(hc, self.chunks(), self.glossary(), self.selection(batch_no, backend))
        return StudyLabeler(hc, world, http_factory=self.http_factory, provenance=self.prov, batch=batch_no,
                            role=backend, max_calls=max_calls)

    def harness_backend(self, backend: str) -> str:
        return "qwen" if backend in ("C", "E") else "gateway"

    def check_order(self, batch_no: int) -> None:
        done = self.finalized()
        earlier = [b for b in range(1, batch_no) if b not in done]
        if earlier:
            raise SystemExit(f"batch {batch_no}: batch(es) {earlier} are not finalized")
        if batch_no in done:
            raise SystemExit(f"batch {batch_no} is finalized")

    def calls_so_far(self, backend: str) -> int:
        names = ["calls-gw-argon.jsonl", "calls-gw-argon_r2.jsonl"] if backend.startswith("argon") \
            else ["calls-gw-opus.jsonl"]
        n = 0
        for nm in names:
            p = self.out / nm
            if p.is_file():
                n += sum(1 for x in p.read_text(encoding="utf-8").splitlines() if x.strip())
        return n

    def cap_limit(self, backend: str) -> int | None:
        if backend in ("C", "E"):
            return None
        kind = "argon" if backend.startswith("argon") else "opus"
        lim = math.floor(float(self.cfg["caps"][f"{kind}_share"]) * len(self.registry()))
        ceiling = self.cfg["caps"].get(f"{kind}_max_calls")
        return lim if ceiling is None else min(lim, int(ceiling))

    def check_caps(self, backend: str, planned: int) -> None:
        """§14 O7: the cumulative calls plus the planned ones within the share and the ceiling."""
        lim = self.cap_limit(backend)
        if lim is None:
            return
        kind = "argon" if backend.startswith("argon") else "opus"
        total = len(self.registry())
        share = float(self.cfg["caps"][f"{kind}_share"])
        ceiling = self.cfg["caps"].get(f"{kind}_max_calls")
        used = self.calls_so_far(backend)
        if used + planned > lim:
            raise SystemExit(f"{kind} cap (§14 O7): {used} calls made + {planned} planned > {lim} "
                             f"({share:.0%} of {total} pairs{'' if ceiling is None else f', ceiling {ceiling}'}); "
                             "the owner raises the cap or the run stops here")

    def preflight(self, batch_no: int, backend: str) -> dict[str, Any]:
        self.check_order(batch_no)
        lab = self.labeler(batch_no, backend)
        self.check_caps(backend, len(lab.w.pairs()))
        return lab.preflight(self.harness_backend(backend), 1)

    def label(self, batch_no: int, backend: str, acknowledged: bool, resume: bool) -> int:
        if (self.out / "PAUSE").exists():
            raise SystemExit("PAUSE present: remove it (after the owner's decision) and run preflight again")
        self.check_order(batch_no)
        hb = self.harness_backend(backend)
        lim = self.cap_limit(backend)
        remaining = None if lim is None else max(0, lim - self.calls_so_far(backend))
        lab = self.labeler(batch_no, backend, max_calls=remaining)
        st = lab.store(hb)
        sel = {p["pair_id"] for p in lab.w.pairs()}
        if any(r.get("pair_id") in sel for r in st.rows.values()) and not resume:
            raise SystemExit(f"{backend}: labels of this batch exist; pass --resume to continue")
        labelled = {r.get("pair_id") for r in st.rows.values()}
        self.check_caps(backend, len(sel - labelled))
        lab.gate(hb, 1, acknowledged)
        if not sel:
            return EXIT_OK
        try:
            rc = lab.label_qwen() if hb == "qwen" else lab.label_gw_capped()
        except jd.p4.HardStop as e:
            jd.g.log(f"HARD STOP: {e.reason}")
            return EXIT_HARD_STOP
        return self.served_model_stop(lab.key(hb), rc)

    def served_model_stop(self, key: str, rc: int) -> int:
        """§9.2: a served judge model id that is not the requested one is a hard stop. The
        harness stores nothing from such a reply and stops issuing; its Qwen path reports the
        stop as a pause, so the study turns it into a hard stop and writes PAUSE, which only
        the owner removes."""
        tp = self.out / f"totals-{key}.json"
        segs = json.loads(tp.read_text())["segments"] if tp.is_file() else []
        status = str(segs[-1]["status"]) if segs else ""
        if "served_model_mismatch" in status:
            (self.out / "PAUSE").write_text(f"hard stop: {key} {status} (§9.2); the owner decides\n",
                                            encoding="utf-8")
            return EXIT_HARD_STOP
        return rc

    # -- finalize
    def finalize(self, batch_no: int) -> int:
        self.check_order(batch_no)
        bl = self.batch_list()
        reg = {p["pair_id"]: p for p in self.registry()}
        ids = bl[batch_no - 1]
        already = sorted(set(ids) & set(self.final_rows()))
        if already:
            raise SystemExit(f"batch {batch_no}: {len(already)} pair(s) already have a final label; a pair is "
                             "labelled once (§6.2, §5.7)")
        c, e, tb, op = (self.labels(b) for b in BACKENDS)
        miss_ce = [p for p in ids if p not in c or p not in e]
        if miss_ce:
            raise SystemExit(f"batch {batch_no}: {len(miss_ce)} pair(s) lack a C or E label")
        esc = [p for p in ids if escalated(c[p], e[p])]
        miss_tb = [p for p in esc if p not in tb]
        sample = drift_sample(batch_no, [reg[p] for p in ids], float(self.cfg["drift"]["share"]))
        sampled = set(sample)
        miss_op = [p for p in sample if p not in op]
        if miss_tb or miss_op:
            raise SystemExit(f"batch {batch_no}: tiebreaker missing on {len(miss_tb)}, Opus on {len(miss_op)} "
                             "sampled pair(s)")
        path = self.out / "final.jsonl"
        stage = self.batch_stage(batch_no)
        for p in ids:
            lab, src = final_label(c[p], e[p], tb.get(p) if p in esc else None)
            o = op.get(p) if p in sampled else None
            t = tb.get(p) if p in esc else None
            jd.p4.append_fsync(path, {
                "k": f"pair|{p}", "schema": FINAL_SCHEMA, "batch": batch_no, "pair_id": p,
                "record_id": reg[p]["record_id"], "chunk_id": reg[p]["chunk_id"], "arm": reg[p]["arm"],
                "stage": reg[p].get("stage", stage), "need": reg[p].get("need", "found10"),
                "label": lab, "source": src,
                "c_label": jd.majority(c[p].get("verdicts") or []),
                "e_label": jd.majority((e[p].get("verdicts") or [])[:1]),
                "c_valid": sum(v is not None for v in (c[p].get("verdicts") or [])),
                "c_model": c[p].get("model"), "e_model": e[p].get("model"),
                "tiebreak_label": None if t is None or t.get("verdict") is None else int(bool(t["verdict"])),
                "tiebreak_model": None if t is None else t.get("model"),
                "tiebreak_attempts": None if t is None else t.get("attempts"),
                "drift_sampled": p in sampled,
                "opus": None if o is None else o.get("verdict"),
                "opus_status": None if o is None else ("answered" if o.get("verdict") is not None
                                                       else "no_reference"),
                "opus_attempts": None if o is None else o.get("attempts"),
                "opus_model": None if o is None else o.get("model"),
                "judge_versions": {k: FIXED[k]["judge_version"] for k in BACKENDS}})
        jd.p4.append_fsync(path, {"k": f"batch|{batch_no}", "schema": FINAL_SCHEMA, "batch": batch_no,
                                  "stage": stage, "pairs": len(ids), "escalated": len(esc), "sampled": len(sample),
                                  "t": jd.g.now_iso(), "provenance": self.prov})
        summ = self.summary()
        jd.g.write_json_atomic(self.out / "judge-summary.json", summ)
        if summ["drift"]["alarm"]:
            jd.g.write_json_atomic(self.out / "drift-alarm.json", {"batch": batch_no, **summ["drift"],
                                                                     "t": jd.g.now_iso()})
            (self.out / "PAUSE").write_text(f"drift alarm after batch {batch_no}: "
                                            f"{'; '.join(summ['drift']['alarm_reasons'])}. The owner decides the "
                                            "remedy (a deviation entry).\n", encoding="utf-8")
            jd.notice_line("DRIFT", f"ALARM after batch {batch_no}: {summ['drift']['alarm_reasons']}",
                           self.out / "LOG")
            return EXIT_HARD_STOP
        return EXIT_OK

    def final_rows(self) -> dict[str, dict[str, Any]]:
        return {r["pair_id"]: r for r in jd.p4.read_jsonl_repair_tail(self.out / "final.jsonl")
                if r.get("k", "").startswith("pair|")}

    def summary(self) -> dict[str, Any]:
        fin = self.final_rows()
        sampled = [p for p, r in fin.items() if r.get("drift_sampled")]
        opus = {p: {"verdict": r.get("opus")} for p, r in fin.items() if r.get("drift_sampled")}
        arm_of = {p: r["arm"] for p, r in fin.items()}
        src = Counter(r["source"] for r in fin.values())
        n = len(fin)
        unj = src.get("unjudged", 0)
        doc = self.batch_doc() if (self.out / "batches.json").is_file() else {}
        done = self.finalized()
        stages = {}
        for s, nos in (doc.get("stages") or {}).items():
            rows = [r for r in fin.values() if r.get("stage", "A") == s]
            stages[s] = {"batches": list(nos), "batches_finalized": sorted(b for b in nos if b in done),
                         "pairs_finalized": len(rows), "sources": dict(Counter(r["source"] for r in rows))}
        return {"pairs_finalized": n, "batches_finalized": sorted(done), "sources": dict(src),
                "unjudged": unj, "unjudged_share": unj / n if n else None,
                "judge_incomplete": bool(n and unj / n > float(self.cfg["unjudged_flag_share"])),
                "per_arm_pairs": dict(Counter(r["arm"] for r in fin.values())),
                "stages": stages, "stage_a_committed": self.committed() is not None, "mode": self.mode,
                "opus_no_reference": {"total": sum(r.get("opus_status") == "no_reference" for r in fin.values()),
                                      **{a: sum(r.get("opus_status") == "no_reference" and r["arm"] == a
                                                for r in fin.values()) for a in asc.ARM_GROUPS}},
                "retry_cap_answered_calls_per_pair": MAX_GW_ATTEMPTS,
                "drift": drift_stats(fin, opus, sampled, arm_of, self.cfg["drift"]),
                "models": {k: FIXED[k]["model"] for k in BACKENDS},
                "judge_versions": {k: FIXED[k]["judge_version"] for k in BACKENDS}, "seeds": {
                    "judge_order": asc.SEEDS["judge_order"], "drift": asc.SEEDS["drift"]}}

    def status(self) -> dict[str, Any]:
        bl = self.batch_list()
        done = self.finalized()
        nxt = next((b for b in range(1, len(bl) + 1) if b not in done), None)
        st: dict[str, Any] = {"batches": len(bl), "finalized": sorted(done), "next": nxt,
                              "next_stage": None if nxt is None else self.batch_stage(nxt),
                              "stages": self.batch_doc().get("stages"), "stage_a_committed": self.committed() is not None,
                              "paused": (self.out / "PAUSE").exists()}
        if nxt is not None:
            have = {b: self.labels(b) for b in BACKENDS}
            todo: dict[str, Any] = {}
            for b in BACKENDS:
                try:
                    sel = self.selection(nxt, b)
                except SystemExit as e:
                    todo[b] = f"blocked: {e}"
                    continue
                todo[b] = sum(p["pair_id"] not in have[b] for p in sel)
            st["next_batch_left"] = todo
        return st

    # -- stage A's commit (§5.7)
    def stage_a_hits(self, recs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Per record and stage-A leg: hit (gold in the top 10, or a top-10 chunk whose final
        label is *answers*; unjudged = no) and the gold-only hit. A demanded pair without a final
        label refuses the commit."""
        fin = self.final_rows()
        by_id = {r["record_id"]: r for r in recs}
        out: list[dict[str, Any]] = []
        missing = 0
        for leg, row in leg_rows(self.runs, ar.stage_legs("A")):
            rec = by_id[row["record_id"]]
            if row.get("failed"):
                out.append({"leg": leg, "record_id": rec["record_id"], "failed": True, "hit": None, "hit_gold": None})
                continue
            gold = rec["gold_chunks"][0]
            top = (row.get("ranked_chunk_ids") or [])[:asc.FOUND_K]
            if gold in top:
                hit = hit_gold = 1
            else:
                hit_gold = 0
                labs = []
                for cid in top:
                    f = fin.get(pair_id(rec["record_id"], cid))
                    if f is None:
                        missing += 1
                    labs.append(None if f is None else f.get("label"))
                hit = int(any(x == 1 for x in labs))
            out.append({"leg": leg, "record_id": rec["record_id"], "failed": False, "hit": hit, "hit_gold": hit_gold})
        if missing:
            raise SystemExit(f"{missing} demanded stage-A pair(s) have no final label; finalize every stage-A batch "
                             "first")
        return out

    def commit_stage_a(self) -> dict[str, Any]:
        if (self.out / "PAUSE").exists():
            raise SystemExit("PAUSE present: stage A is committed only after the owner has cleared it")
        if self.committed() is not None:
            raise SystemExit("stage A is committed already")
        doc = self.batch_doc()
        stages = doc.get("stages") or {}
        if "A" not in stages or "B" in stages:
            raise SystemExit("commit-stage-a needs stage A's registry and no stage-B registry")
        done = self.finalized()
        left = [b for b in stages["A"] if b not in done]
        if left:
            raise SystemExit(f"stage-A batch(es) {left[:8]} are not finalized")
        summ = self.summary()
        if summ["drift"]["alarm"]:
            raise SystemExit("the drift alarm has fired; the owner decides the remedy before the commit")
        recs = asc.load_records(self.cfg["records"])
        hits = self.stage_a_hits(recs)
        d = self.out / "stage-a"
        d.mkdir(mode=0o700, exist_ok=True)
        hp = d / "hits.jsonl"
        with hp.open("w", encoding="utf-8") as fh:
            for h in hits:
                fh.write(json.dumps({"schema": HITS_SCHEMA, **h}, sort_keys=True) + "\n")
        files: list[dict[str, Any]] = [file_entry("stage_a_hits", hp, "exact")]
        seen_rows: set[Path] = set()
        for c, g, r, rep in ar.stage_legs("A"):
            rows_path, sp = ar.leg_files(self.runs, c, g, r, rep)
            leg = asc.leg_id(c, g, r, rep)
            cp = d / "summaries" / c / sp.name
            cp.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(sp, cp)
            files.append(file_entry(f"summary:{leg}", cp, "exact"))
            if rows_path not in seen_rows:
                seen_rows.add(rows_path)
                files.append(file_entry(f"rows:{leg}", rows_path, "prefix"))
        for name, src in (("judge_summary", self.out / "judge-summary.json"),
                          ("pairs_manifest", self.out / "pairs-manifest.json")):
            cp = d / src.name
            shutil.copyfile(src, cp)
            files.append(file_entry(name, cp, "exact"))
        files.append(file_entry("pairs", self.pairs_path, "prefix"))
        files.append(file_entry("final", self.out / "final.jsonl", "prefix"))
        for pat in ("labels-*.jsonl", "attempts-*.jsonl", "calls-*.jsonl"):
            for p in sorted(self.out.glob(pat)):
                files.append(file_entry(pat.split("-")[0] + ":" + p.stem.split("-", 1)[1], p, "prefix"))
        fin = self.final_rows()
        counts = {"legs": len(ar.stage_legs("A")), "records": len(recs),
                  "hits_rows": len(hits), "hits_failed": sum(bool(h["failed"]) for h in hits),
                  "found10_per_leg": {lg: sum(int(h["hit"] or 0) for h in hits if h["leg"] == lg)
                                      for lg in sorted({h["leg"] for h in hits})},
                  "found10_gold_per_leg": {lg: sum(int(h["hit_gold"] or 0) for h in hits if h["leg"] == lg)
                                           for lg in sorted({h["leg"] for h in hits})},
                  "pairs": len(fin), "batches": len(stages["A"]), "sources": summ["sources"],
                  "unjudged": summ["unjudged"], "opus_no_reference": summ["opus_no_reference"]}
        commit = {"schema": COMMIT_SCHEMA, "mode": self.mode, "t": jd.g.now_iso(), "provenance": self.prov,
                  "stage_a_legs": sorted(ar.STAGE_A_LEGS), "batches_file": str((self.out / "batches.json").resolve()),
                  "batches_A": [doc["batches"][b - 1] for b in stages["A"]], "files": files, "counts": counts,
                  "drift": summ["drift"]}
        cpath = self.out / "stage-a-commit.json"
        jd.g.write_json_atomic(cpath, commit)
        csha = asc.sha256_file(cpath)
        public = {"schema": COMMIT_PUBLIC_SCHEMA, "commit_sha256": csha, "mode": self.mode, "t": commit["t"],
                  "stage_a_legs": commit["stage_a_legs"], "counts": counts,
                  "files": {f["name"]: {"sha256": f["sha256"], "bytes": f["bytes"], "mode": f["mode"]} for f in files},
                  "drift": {s: {k: v for k, v in sc.items() if k in ("sampled", "answered", "agree", "filtered",
                                                                     "agreement", "fires")}
                            for s, sc in summ["drift"]["scopes"].items()},
                  "provenance": {k: self.prov.get(k) for k in ("commit", "dirty", "untracked") if k in self.prov}}
        chunks = self.chunks()
        forbidden = [r["record_id"] for r in recs] + [r["paper"] for r in recs] + list(chunks) + \
            [c["paper"] for c in chunks.values()]
        bad = asc.public_problems(public, forbidden, [r["question"] for r in recs])
        if bad:
            raise SystemExit(f"stage-A public commit file would carry {len(bad)} private string(s); refused")
        jd.g.write_json_atomic(self.out / "stage-a-commit.public.json", public)
        jd.g.write_json_atomic(self.runs / ar.COMMIT_MARKER, {"commit": str(cpath.resolve()), "commit_sha256": csha,
                                                              "t": commit["t"]})
        return public


def provenance() -> dict[str, Any]:
    try:
        from ragstack.provenance import experiment_provenance
        return experiment_provenance()
    except Exception:  # noqa: BLE001
        return jd.provenance()


def main(argv: Sequence[str] | None = None, *, http_factory: Callable[[], Any] | None = None,
         prov: Mapping[str, Any] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", required=True, type=Path)
    sub = ap.add_subparsers(dest="cmd", required=True)
    bp = sub.add_parser("build-pairs")
    bp.add_argument("--stage", required=True, choices=ar.STAGES)
    sub.add_parser("status")
    for name in ("preflight", "label"):
        p = sub.add_parser(name)
        p.add_argument("--batch", type=int, required=True)
        p.add_argument("--backend", choices=BACKENDS, required=True)
        if name == "label":
            p.add_argument("--notice-acknowledged", action="store_true")
            p.add_argument("--resume", action="store_true")
    pf = sub.add_parser("finalize")
    pf.add_argument("--batch", type=int, required=True)
    sub.add_parser("commit-stage-a")
    sub.add_parser("check-analysis-gate")
    args = ap.parse_args(argv)
    os.umask(0o077)
    cfg = load_config(args.config)
    if args.cmd == "check-analysis-gate":
        print(json.dumps(analysis_gate(Path(cfg["runs_dir"]), cfg.get("spot_checks"),
                                       dry_run=bool(cfg.get("dry_run"))), indent=1))
        return EXIT_OK
    pl = Pipeline(cfg, http_factory=http_factory, provenance=prov if prov is not None else provenance())
    if args.cmd == "build-pairs":
        man = pl.build(args.stage)
        print(json.dumps(man["counts"], indent=1))
        return EXIT_OK
    if args.cmd == "status":
        print(json.dumps(pl.status(), indent=1))
        return EXIT_OK
    if args.cmd == "preflight":
        pl.preflight(args.batch, args.backend)
        return EXIT_OK
    if args.cmd == "label":
        return pl.label(args.batch, args.backend, args.notice_acknowledged, args.resume)
    if args.cmd == "commit-stage-a":
        print(json.dumps(pl.commit_stage_a()["counts"], indent=1))
        return EXIT_OK
    return pl.finalize(args.batch)


if __name__ == "__main__":
    sys.exit(main())
