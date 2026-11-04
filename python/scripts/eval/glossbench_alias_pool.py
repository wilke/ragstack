#!/usr/bin/env python
"""Alias benchmark: the drafting pool (G and C jobs per paper) from the alias glossary
and the chunk export, with the pre-drafting yield estimate.

Offline and read-only. Inputs (private config, ``corpora.<set>``): ``chunks`` (the
export, :func:`_glossbench_r2.read_chunks`), ``paper_key``, ``alias_glossary`` (the
glossary builder's ``merged_glossary.jsonl``, :mod:`alias_abbrev`: one entry per line,
``{doc_id, alias, primary, kind: symbol|abbreviation|both}`` plus, for an abbreviation,
``def_chunk_id`` / ``def_chunk_ids``; ``use_chunk_ids`` is ignored; a line without
``alias`` that carries ``schema`` is a header and skipped), optional ``expected_chunks``.

Into the private output directory:

* ``pool-alias-<set>.jsonl`` — PRIVATE. A header line, then one line per planned job:
  ``job_id, paper, doc_id, chunk_id, content_sha256, stratum (G|C), alias, primary,
  alias_kind, def_chunk_id, rank, wave``.
* ``pool-manifest-alias-<set>.json`` — counts only, with the provenance.

**Candidates** are the glossary builder's own definition, computed by its code
(:func:`alias_abbrev.g_candidates`, :func:`alias_abbrev.c_candidate_chunks`) on the same
export, so the pool, the recount and layer 1 (which uses the same matchers through
:mod:`glossbench_alias_checks`) cannot disagree:

* **G**: a chunk of the entry's paper holding the alias (:func:`alias_abbrev.alias_occurs`)
  and not the primary under the strict rule (:func:`alias_abbrev.has_primary_strict`),
  the primary termlike (symbol <= 5 words, abbreviation <= 8, no digit); the entry's
  definition chunks are excluded as well (counted, normally none: they spell it out).
* **C**: a chunk of the paper holding the entry's termlike primary literally
  (:func:`alias_abbrev.primary_seq_in`; a one-word primary >= 8 characters) and not
  holding any of the paper's aliases without that alias's own primary.

An entry whose primary has no word at all, whose alias is empty, or whose alias is one
character that is an English word (``a``, ``I``: the question guard would reject almost
every question) is dropped (counted).

**Papers.** Eligible: at least ``min_g_candidates`` (2) G and ``min_c_candidates`` (1)
C candidates. ``n_papers`` (null = all) are drawn by ``random.Random("<seed_base>|<set>")``
from the sorted eligible ids.

**Excluded papers** (``exclude_papers``: a path, or null). A text file with one paper id per line
(the export's ``paper_key``; blank lines and ``#`` comments ignored). Those papers are removed from
the eligible ids BEFORE the draw, so no job is ever planned on them. The file's sha256, the
number of ids it lists, and how many of them were eligible are recorded in the pool header and
the manifest (``papers_eligible`` there counts after the exclusion). The validation set of the judge (JUDGE-VALIDATION-PLAN.md) uses it to keep the
study's papers out.

**Jobs, per paper, seeded.** ``g_per_paper`` (6) G and ``c_per_paper`` (2) C jobs, the
order seeded per paper and kind (``<seed>|<kind>|<paper>``): the entries in a seeded
order, each entry's candidate chunks in a seeded order, then round-robin over the
entries, so the jobs spread over the paper's aliases; a chunk carries at most one job of
a kind per paper. ``rank`` is the job's place in that order; ``wave`` the drafting wave
(G: ``rank // goal_G``, C: ``rank // goal_C``), so the first wave drafts just enough for
the stop rule and later waves only run for papers still short (the drafter's stop rule:
after ``goal`` = 2 accepted G and 1 accepted C a paper's remaining jobs of that kind are
skipped; :mod:`glossbench_alias_draft`).

**Pre-drafting yield estimate** (the 2026-10-30 rule: count before spending). Per paper
and kind, the expected number of accepted records under the stop rule is
``E[min(goal, Binomial(jobs, p))]`` with ``p`` the per-job pass rate (layers 1-2):
``yield_calibration`` ``{"G": p, "C": p}`` when measured on a pilot, else
``prior_pass_rate`` (default 0.35 for both: round 3 set O accepted 206 records from
about 536 drafted jobs, D-5; ``calibrated: false``). ``expected_yield`` = the sum over
papers and kinds / (papers x (goal_G + goal_C)). Below ``yield_gate`` (0.5) the pool is
``YIELD-NOT-ADEQUATE`` and the drafter refuses without ``--allow-low-yield``. Printed
beside it: reachability (papers whose job counts can reach the goal at all) and the
per-job pass rate the goal needs (goal / jobs).

Usage::

    glossbench_alias_pool.py --private-config CFG build --set O

Config ``alias_pool`` (defaults :data:`DEFAULTS`): seed_base, n_papers, g_per_paper,
c_per_paper, goal {G, C}, min_g_candidates, min_c_candidates, prior_pass_rate {G, C},
yield_calibration {G, C} | null, yield_gate, exclude_papers (path | null).
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import _glossbench_r2 as g  # noqa: E402
import alias_abbrev as aa  # noqa: E402
import glossbench_alias_checks as ak  # noqa: E402
import glossbench_r2_pool as r2  # noqa: E402

from ragstack.ingestion.symbol_glossary import canonical_symbol  # noqa: E402

POOL_SCHEMA = "glossbench-alias-pool/1"
MANIFEST_SCHEMA = "glossbench-alias-pool-manifest/1"
YIELD_LOW, YIELD_OK = "YIELD-NOT-ADEQUATE", "ADEQUATE"
SET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
DEFAULTS: dict[str, Any] = {
    "seed_base": "261031|alias",
    "n_papers": None,
    "g_per_paper": 6,
    "c_per_paper": 2,
    "goal": {"G": 2, "C": 1},
    "min_g_candidates": 2,
    "min_c_candidates": 1,
    "prior_pass_rate": {"G": 0.35, "C": 0.35},
    "yield_calibration": None,
    "yield_gate": 0.5,
    "exclude_papers": None,
}


def label(set_: str) -> str:
    """The file label of a set (the alias study never shares a round-3 file name)."""
    return f"alias-{set_}"


def corpus_config(cfg: Mapping[str, Any], set_: str) -> dict[str, Any]:
    if not SET_RE.match(set_ or ""):
        raise SystemExit(f"set name {set_!r}: letters, digits, '_' or '-' (at most 32)")
    c = (cfg.get("corpora") or {}).get(set_)
    if not isinstance(c, dict) or not c.get("chunks"):
        raise SystemExit(f"private config: corpora.{set_}.chunks is required")
    return c


def pool_config(cfg: Mapping[str, Any]) -> dict[str, Any]:
    given = dict(cfg.get("alias_pool") or {})
    unknown = set(given) - set(DEFAULTS)
    if unknown:
        raise SystemExit(f"alias_pool config: unknown keys {sorted(unknown)}")
    p = {**DEFAULTS, **given}
    for k in ("goal", "prior_pass_rate"):
        p[k] = {**DEFAULTS[k], **(given.get(k) or {})}
    for kind in ak.KINDS:
        if int(p["goal"][kind]) < 1:
            raise SystemExit(f"alias_pool.goal.{kind}: at least 1")
        for blk in ("prior_pass_rate", "yield_calibration"):
            if p[blk] is None:
                continue
            v = p[blk].get(kind)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= float(v) <= 1:
                raise SystemExit(f"alias_pool.{blk}.{kind} {v!r}: a number in [0, 1]")
    yg = p["yield_gate"]
    if isinstance(yg, bool) or not isinstance(yg, (int, float)) or not 0 <= float(yg) <= 1:
        raise SystemExit(f"alias_pool.yield_gate {yg!r}: a number in [0, 1]")
    if p["exclude_papers"] is not None and not isinstance(p["exclude_papers"], str):
        raise SystemExit(f"alias_pool.exclude_papers {p['exclude_papers']!r}: a path or null")
    return p


def read_glossary(path: str | Path) -> list[dict[str, Any]]:
    """The alias glossary (JSONL). Every entry needs doc_id, alias, primary and kind
    (symbol | abbreviation | both); def_chunk_id(s) may be absent."""
    out: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if "alias" not in row and "schema" in row:
                continue                                         # a header line
            miss = [k for k in ("doc_id", "alias", "primary", "kind") if k not in row]
            if miss:
                raise SystemExit(f"{Path(path).name}:{i}: a glossary entry needs {miss}")
            if row["kind"] not in ak.ALIAS_KINDS:
                raise SystemExit(f"{Path(path).name}:{i}: kind {row['kind']!r} not in {ak.ALIAS_KINDS}")
            out.append(row)
    return out


def read_exclude_papers(path: str | Path) -> list[str]:
    """The paper ids of an ``exclude_papers`` file: one per line, blank lines and ``#`` comments
    ignored, sorted and de-duplicated. A missing or empty file is an error (an exclusion that
    silently excludes nothing is the failure this guards against)."""
    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"alias_pool.exclude_papers: {p} is not a file")
    ids = sorted({x.split("#", 1)[0].strip() for x in p.read_text(encoding="utf-8").splitlines()} - {""})
    if not ids:
        raise SystemExit(f"alias_pool.exclude_papers: {p} lists no paper")
    return ids


def binom_expected_min(n: int, p: float, goal: int) -> float:
    """``E[min(goal, X)]`` for ``X ~ Binomial(n, p)``."""
    return sum(min(goal, k) * math.comb(n, k) * p ** k * (1 - p) ** (n - k) for k in range(n + 1))


def expected_drafted(wave_sizes: list[int], goal: int, p: float, have: int = 0) -> float:
    """Expected number of jobs drafted for one paper and kind under the drafter's stop rule:
    the waves (``wave_sizes``, in order) are drafted one after another, and a wave is drafted
    only while fewer than ``goal`` jobs have passed (``have`` already did); each job passes
    independently with probability ``p``."""
    dist = {min(int(have), goal): 1.0}
    exp = 0.0
    for m in wave_sizes:
        new: dict[int, float] = {}
        for a, pr in dist.items():
            if a >= goal:
                new[a] = new.get(a, 0.0) + pr
                continue
            exp += pr * m
            for k in range(m + 1):
                b = min(goal, a + k)
                new[b] = new.get(b, 0.0) + pr * math.comb(m, k) * p ** k * (1 - p) ** (m - k)
        dist = new
    return exp


def expected_yield(jobs_per_paper: Mapping[str, Mapping[str, int]], goal: Mapping[str, int],
                   rates: Mapping[str, float], gate: float, calibrated: bool) -> dict[str, Any]:
    papers = sorted(jobs_per_paper)
    target = len(papers) * sum(int(goal[k]) for k in ak.KINDS)
    by_kind: dict[str, Any] = {}
    total = 0.0
    for k in ak.KINDS:
        exp = sum(binom_expected_min(int(jobs_per_paper[p].get(k, 0)), float(rates[k]), int(goal[k]))
                  for p in papers)
        total += exp
        jobs = [int(jobs_per_paper[p].get(k, 0)) for p in papers]
        by_kind[k] = {"jobs": sum(jobs), "goal_per_paper": int(goal[k]), "rate": float(rates[k]),
                      "expected_accepted": round(exp, 3),
                      "papers_reachable": sum(j >= int(goal[k]) for j in jobs),
                      "required_rate_min": (min(int(goal[k]) / j for j in jobs if j) if any(jobs) else None),
                      "required_rate_max": (max(int(goal[k]) / j for j in jobs if j) if any(jobs) else None)}
    y = (total / target) if target else 0.0
    return {"expected_yield": round(y, 4), "expected_accepted": round(total, 3), "target_records": target,
            "papers": len(papers), "gate": gate, "status": YIELD_OK if y >= gate else YIELD_LOW,
            "calibrated": calibrated, "by_kind": by_kind,
            "definition": "sum over papers and kinds of E[min(goal, Binomial(jobs, p))], over papers x "
                          "(goal_G + goal_C); p calibrated on a pilot or the prior"}


def _round_robin(entries: list[tuple[str, list[str]]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    i = 0
    while any(i < len(cs) for _, cs in entries):
        for e, cs in entries:
            if i < len(cs):
                out.append((e, cs[i]))
        i += 1
    return out


def build(cfg: dict[str, Any], set_: str, out: Path, *, provenance: Any = None) -> dict[str, Any]:
    pc = pool_config(cfg)
    cc = corpus_config(cfg, set_)
    if not cc.get("alias_glossary"):
        raise SystemExit(f"private config: corpora.{set_}.alias_glossary is required")
    rows = g.read_chunks(cc["chunks"])
    expected = cc.get("expected_chunks")
    if expected is not None and len(rows) != int(expected):
        raise SystemExit(f"set {set_}: the export holds {len(rows)} chunks, expected {expected}")
    paper_key = cc.get("paper_key", "doc_id")
    corpus = g.build_corpus(rows, paper_key)
    entries = read_glossary(cc["alias_glossary"])
    cnt: Counter[str] = Counter()
    usable: list[dict[str, Any]] = []
    keys: list[str] = []
    seen: set[str] = set()
    for e in entries:
        cnt["entries"] += 1
        if e["doc_id"] not in corpus.doc_paper:
            cnt["entry_doc_not_in_export"] += 1
            continue
        if not str(e["alias"]).strip() or not aa.words(str(e["primary"])):
            cnt["entry_unusable"] += 1
            continue
        akey = canonical_symbol(str(e["alias"]))
        if len(akey) == 1 and akey.casefold() in g.STOP_WORDS:
            cnt["entry_alias_is_a_word"] += 1        # "a", "I": the question guard would reject every question
            continue
        paper = corpus.doc_paper[e["doc_id"]]
        if e.get("paper") is not None and e["paper"] != paper:
            cnt["entry_paper_differs_from_paper_key"] += 1      # the export's paper_key wins
        key = g.sha_text(json.dumps([paper, e["alias"], e["primary"]]))[:12]
        if key in seen:
            cnt["entry_duplicate"] += 1
            continue
        seen.add(key)
        usable.append({**e, "paper": paper})
        keys.append(key)
    ent_by_key = dict(zip(keys, usable, strict=True))
    # the glossary builder's own candidate definition, on its own corpus view of the same rows
    acorp = aa.build_corpus(rows, lambda r: corpus.doc_paper[r.doc_id])
    g_cand, loose = aa.g_candidates(acorp, usable)
    c_cand = aa.c_candidate_chunks(acorp, usable, loose)
    # candidates per paper and kind: entry key -> chunk ids
    cand: dict[str, dict[str, dict[str, list[str]]]] = {}
    for p, lst in g_cand.items():
        for x in lst:
            e = usable[x["entry"]]
            defs = set(e.get("def_chunk_ids") or []) | ({e["def_chunk_id"]} if e.get("def_chunk_id") else set())
            chunks = [c for c in x["chunks"] if c not in defs]
            cnt["G_def_chunk_excluded"] += len(x["chunks"]) - len(chunks)
            if chunks:
                cand.setdefault(p, {"G": {}, "C": {}})["G"][keys[x["entry"]]] = chunks
                cnt["G_candidates"] += len(chunks)
                cnt["G_entries"] += 1
    for p, d in c_cand.items():
        for i, chunks in d.items():
            cand.setdefault(p, {"G": {}, "C": {}})["C"][keys[i]] = chunks
            cnt["C_candidates"] += len(chunks)
            cnt["C_entries"] += 1
    eligible = sorted(p for p, kinds in cand.items()
                      if sum(len(v) for v in kinds["G"].values()) >= int(pc["min_g_candidates"])
                      and sum(len(v) for v in kinds["C"].values()) >= int(pc["min_c_candidates"]))
    excl: dict[str, Any] | None = None
    if pc["exclude_papers"]:
        ex_ids = set(read_exclude_papers(pc["exclude_papers"]))
        excl = {"file": str(pc["exclude_papers"]), "sha256": g.file_sha(pc["exclude_papers"]),
                "ids": len(ex_ids), "eligible_excluded": sum(p in ex_ids for p in eligible),
                "ids_not_in_export": len(ex_ids - set(corpus.doc_paper.values()))}
        eligible = [p for p in eligible if p not in ex_ids]
    seed = f"{pc['seed_base']}|{set_}"
    n = pc["n_papers"]
    papers = sorted(random.Random(seed).sample(eligible, len(eligible) if n is None else min(int(n), len(eligible))))
    per = {"G": int(pc["g_per_paper"]), "C": int(pc["c_per_paper"])}
    jobs: list[dict[str, Any]] = []
    jobs_per_paper: dict[str, dict[str, int]] = {}
    for p in papers:
        jobs_per_paper[p] = {}
        for kind in ak.KINDS:
            ks = r2.seeded_order(sorted(cand[p][kind]), f"{seed}|{kind}|{p}")
            order = _round_robin([(k, r2.seeded_order(sorted(set(cand[p][kind][k])), f"{seed}|{kind}|{p}|{k}"))
                                  for k in ks])
            used: set[str] = set()
            picked: list[tuple[str, str]] = []
            for k, cid in order:
                if cid in used:
                    continue
                used.add(cid)
                picked.append((k, cid))
                if len(picked) == per[kind]:
                    break
            jobs_per_paper[p][kind] = len(picked)
            goal = int(pc["goal"][kind])
            for rank, (k, cid) in enumerate(picked):
                e = ent_by_key[k]
                ch = corpus.chunks[cid]
                jobs.append({"kind": "job", "job_id": f"{kind}-{k}-{g.sha_text(cid)[:10]}", "set": set_,
                             "paper": p, "doc_id": ch.doc_id, "chunk_id": cid,
                             "content_sha256": g.sha_text(ch.content), "stratum": kind, "entry_key": k,
                             "alias": e["alias"], "primary": e["primary"], "alias_kind": e["kind"],
                             "def_chunk_id": e.get("def_chunk_id"), "rank": rank, "wave": rank // goal})
    calib = pc["yield_calibration"]
    rates = {k: float((calib or pc["prior_pass_rate"])[k]) for k in ak.KINDS}
    yld = expected_yield(jobs_per_paper, pc["goal"], rates, float(pc["yield_gate"]), calib is not None)
    prov = (provenance or g.experiment_provenance)()
    header = {"kind": "header", "schema": POOL_SCHEMA, "set": set_, "created": g.now_iso(), "seed": seed,
              "eval_papers": papers, "paper_key": cc.get("paper_key", "doc_id"), "draft_mode": "alias",
              "inputs_sha256": {"chunks": [g.file_sha(x) for x in cc["chunks"]],
                                "alias_glossary": g.file_sha(cc["alias_glossary"])},
              "design": {k: pc[k] for k in ("g_per_paper", "c_per_paper", "goal", "min_g_candidates",
                                            "min_c_candidates", "n_papers")},
              "commit": prov.get("commit"), "yield": yld, "exclude_papers": excl}
    pool_path = out / f"pool-{label(set_)}.jsonl"
    tmp = pool_path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for row in [header, *jobs]:
            fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    tmp.replace(pool_path)
    manifest = {"schema": MANIFEST_SCHEMA, "set": set_, "created": header["created"], "seed": seed,
                "design": header["design"], "counts": {
                    **dict(sorted(cnt.items())), "chunks": len(rows), "papers_with_entries": len(cand),
                    "papers_eligible": len(eligible), "eval_papers": len(papers),
                    "jobs": dict(Counter(j["stratum"] for j in jobs)),
                    "jobs_by_wave": {f"{k}{w}": c for (k, w), c in sorted(Counter(
                        (j["stratum"], j["wave"]) for j in jobs).items())}},
                "yield": yld, "exclude_papers": excl, "pool_sha256": g.file_sha(pool_path),
                "eval_papers_sha256": g.sha_text("\n".join(papers)), "provenance": prov, "citable": g.citable(prov)}
    g.write_json_atomic(out / f"pool-manifest-{label(set_)}.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--private-config", default=None, help=f"private JSON config (or env {g.CONFIG_ENV})")
    ap.add_argument("--out-dir", default=None, help="default: the config's out_dir")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="G/C jobs per paper + the yield estimate for one set")
    b.add_argument("--set", required=True)
    args = ap.parse_args(argv)
    cfg, _ = g.load_config(args.private_config)
    m = build(cfg, args.set, g.out_dir(cfg, args.out_dir))
    print(json.dumps({"set": m["set"], "counts": m["counts"],
                      "yield": {k: m["yield"][k] for k in ("expected_yield", "gate", "status", "calibrated")}},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
