#!/usr/bin/env python
"""Glossary benchmark round 2, harness item 2c: eval papers, the arm-blind
value-statement pool, the strata and the reach gate (prereg §3.1).

Offline and read-only: it reads one corpus's chunk export (the 512 index) and
the per-arm ``glossary.jsonl`` files, and writes two files into the private
output directory:

* ``pool-<set>.jsonl`` — PRIVATE. A header line, then one line per pool chunk
  of the eval papers: ids, offsets, ``content_sha256``, the value-statement
  tokens, the stratum, one reach boolean per arm, and whether (and in which
  seeded order) the chunk is sampled for drafting. No glossary text, no chunk
  text. The drafter (item 2d) reads chunk text from the same export and checks
  ``content_sha256``.
* ``pool-manifest-<set>.json`` — counts only (no id, path, text or glossary
  entry): what the record may publish, with ``experiment_provenance()``.

Steps (prereg §3.1):

1. **Eval papers.** Value-statement chunks are found with no glossary
   (:func:`_glossbench_r2.value_statements`). Papers with at least
   ``min_vs_chunks`` of them are eligible (papers listed in the corpus's
   ``exclude_papers`` file are removed first: set P drops the papers of the
   holder's round-1 gold). A seeded sample (``<seed_base>|<set>``, e.g.
   ``261101|O``) of ``n_papers`` is drawn from the sorted eligible ids. This
   step never reads a glossary file, so the frontier batch does not change it.
2. **Strata**, per value-statement chunk of an eval paper, from the rendered
   field of each reach arm (``Glossary.for_chunk`` under the file's own config,
   which must be the served value-only attachment): **S1** when at least one
   arm renders a non-empty field, else **S2**. **S3** candidates are the eval
   papers' chunks with no value statement but a printed number; whether one
   yields a words-only record is the drafter's call (§3.2).
3. **Reach gate.** S1 >= ``reach_gate`` (150) chunks -> the corpus's retrieval
   leg is ``EVALUABLE``, else ``GATE-NOT-EVALUABLE``. Without a ``frontierp``
   file the reading is ``dry`` (plan step 4); with it, ``final`` (step 6).
4. **Chunk sample.** Per paper and stratum a seeded order
   (``<seed_base>|<set>|chunks|<stratum>|<paper>``); the first
   ``s1_per_paper`` / ``s2_per_paper`` / ``s3_per_paper`` are sampled.

Manipulation checks before anything is written: the export's chunk count must
equal ``expected_chunks`` when configured; every arm file must resolve every
eval-paper document (``verified`` or ``verified_ws``; no missing, mismatched
or unverifiable row); a file whose config is not value-only attachment is
refused.

**Fields mode** (set P only: ``corpora.P.draft_mode: fields``, see
:mod:`glossbench_r2_fields`). Steps 1 and 4 change; strata and reach do not.
Eligibility counts, per paper, the chunks the arm-blind lexical prefilter
admits for a planned field (a printed number, digits or words, and a field term
or a number followed by a unit of the field's dimension); the pool holds those
chunks of the eval papers (``field_candidates``), and the per-field quota plan
(conditions excluded, constants capped, new-ground fields weighted, leftover
redistributed; :func:`glossbench_r2_fields.plan_fields`) assigns the drafting
jobs (``field_jobs``). The header records the field list's sha256, the plan,
the computed ``in_set`` (with the held set's sha256), the constants and the
field specs the drafter is given. A chunk with no value statement stays S3.

Usage::

    glossbench_r2_pool.py --private-config CFG build --set O [--corpus-reach]
    glossbench_r2_pool.py --private-config CFG build --set P --in-set-records HELD.jsonl

Config (private JSON; see :mod:`_glossbench_r2`)::

    out_dir                 private output directory (never inside the repo)
    corpora.<set>           {chunks: [paths], expected_chunks, paper_key ("doc_id" | "path_root"),
                             arms: {arm: glossary.jsonl}, exclude_papers: path | null}
    pool                    {seed_base, n_papers, min_vs_chunks, s1_per_paper, s2_per_paper,
                             s3_per_paper, reach_gate, reach_arms, vs_stoplist, per_set}   (defaults: DEFAULTS)

``per_set`` overrides the per-paper stratum quotas for one set (e.g. a larger S3
quota for P: ``{"P": {"s3_per_paper": 4}}``); the effective quotas are in the
header and the manifest (``quotas_per_paper``).

``vs_stoplist`` (default :data:`_glossbench_r2.STAT_TOKENS`: p, n, N, df, R2, r,
t, F, z, χ2, chi2, CI, SD, SE, SEM, OR, HR, RR, IQR, AUC) names statistical
tokens that do not make a chunk a value statement; the header and the manifest
record the list used, and the drafter's layer-1 checks read it from the header.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import _glossbench_r2 as g  # noqa: E402
import glossbench_r2_fields as fl  # noqa: E402

from ragstack.ingestion.glossary_file import (  # noqa: E402
    GlossaryFile,
    file_sha256,
    read_glossary_file,
    resolve_documents,
)
from ragstack.ingestion.symbol_glossary import Glossary  # noqa: E402

POOL_SCHEMA = "glossbench-r2-pool/1"
MANIFEST_SCHEMA = "glossbench-r2-pool-manifest/1"
STRATA = ("S1", "S2", "S3")
FRONTIER_ARM = "frontierp"
DEFAULTS: dict[str, Any] = {
    "seed_base": "261101",
    "n_papers": 25,
    "min_vs_chunks": 3,
    "s1_per_paper": 12,
    "s2_per_paper": 2,
    "s3_per_paper": 2,
    "reach_gate": 150,
    "reach_arms": ["rules", "scoutp", "qwen38p", "qwen36p", FRONTIER_ARM],
    #: statistical tokens that do not make a value statement (``_glossbench_r2.STAT_TOKENS``)
    "vs_stoplist": sorted(g.STAT_TOKENS),
    #: per-set overrides of the per-paper stratum quotas, e.g. {"P": {"s3_per_paper": 4}};
    #: a set not named inherits the global values (the effective ones are recorded)
    "per_set": {},
}
PER_SET_KEYS = ("s1_per_paper", "s2_per_paper", "s3_per_paper")


PREFILTER_NOTE = ("arm-blind lexical prefilter on the chunk text: a printed number (digits or a number word) "
                  "and (a field term at word boundaries, or a number followed by a unit of the field's dimension)")


def pool_config(cfg: dict[str, Any], set_: str | None = None) -> dict[str, Any]:
    """The pool config; with ``set_``, that set's per-set quota overrides applied."""
    p = {**DEFAULTS, **(cfg.get("pool") or {})}
    unknown = set(cfg.get("pool") or {}) - set(DEFAULTS)
    if unknown:
        raise SystemExit(f"pool config: unknown keys {sorted(unknown)}")
    per = p.get("per_set") or {}
    for s_, over in per.items():
        if s_ not in g.SETS or not isinstance(over, dict) or set(over) - set(PER_SET_KEYS):
            raise SystemExit(f"pool.per_set: {s_!r} must be a set with keys from {PER_SET_KEYS}")
    if set_ is not None:
        p.update({k: int(v) for k, v in (per.get(set_) or {}).items()})
    return p


def read_exclusions(path: str | None) -> tuple[set[str], str | None]:
    """Paper ids to exclude (one per line, or a JSON list). Ids only, never text."""
    if not path:
        return set(), None
    raw = Path(path).read_text(encoding="utf-8")
    try:
        ids = json.loads(raw)
        if not isinstance(ids, list):
            raise ValueError
    except ValueError:
        ids = [x.strip() for x in raw.splitlines() if x.strip() and not x.startswith("#")]
    return {str(x) for x in ids}, g.file_sha(path)


def select_papers(vs_count: dict[str, int], exclude: set[str], n: int, min_vs: int,
                  seed: str) -> tuple[list[str], list[str]]:
    """(eligible sorted, eval papers sorted). Arm-blind: only value-statement counts."""
    eligible = sorted(p for p, k in vs_count.items() if k >= min_vs and p not in exclude)
    pick = random.Random(seed).sample(eligible, min(n, len(eligible)))
    return eligible, sorted(pick)


def seeded_order(ids: list[str], seed: str) -> list[str]:
    out = sorted(ids)
    random.Random(seed).shuffle(out)
    return out


def load_arm(path: str, doc_ids: set[str], corpus: g.Corpus, arm: str) -> tuple[GlossaryFile, dict[str, Glossary],
                                                                              dict[str, int]]:
    """Read one arm file, resolve it against the reassembled documents, refuse any problem."""
    gfile = read_glossary_file(path, doc_ids=doc_ids)
    if not gfile.config.attach_only_with_value:
        raise SystemExit(f"arm {arm}: the file's config is not value-only attachment (the served default)")
    res = resolve_documents(gfile, [corpus.docs[d] for d in sorted(doc_ids)])
    if res.problems or res.unverified:
        kinds = Counter(k for _, k in res.problems)
        raise SystemExit(f"arm {arm}: the glossary file does not resolve every document "
                         f"({dict(kinds)}; unverified {len(res.unverified)}): manipulation check failed")
    return gfile, res.glossaries, res.counts()


def render_reach(glossaries: dict[str, Glossary], gfile: GlossaryFile, chunk: g.ChunkRow) -> bool:
    gl = glossaries[chunk.doc_id]
    return bool(gl.for_chunk(chunk.content, gfile.config, start_char=chunk.start_char))


def build(cfg: dict[str, Any], set_: str, out: Path, corpus_reach: bool = False,
          provenance: Any = None, in_set_records: str | None = None) -> dict[str, Any]:
    pc = pool_config(cfg, set_)
    cc = g.corpus_config(cfg, set_)
    fc = fl.field_config(cc, set_)
    fres = fl.resolve_fields(fc, in_set_records) if fc else None
    rows = g.read_chunks(cc["chunks"])
    expected = cc.get("expected_chunks")
    if expected is not None and len(rows) != int(expected):
        raise SystemExit(f"set {set_}: the export holds {len(rows)} chunks, expected {expected}: "
                         "manipulation check failed")
    corpus = g.build_corpus(rows, cc.get("paper_key", "doc_id"))
    exclude, exclude_sha = read_exclusions(cc.get("exclude_papers"))
    stoplist = [str(t) for t in pc["vs_stoplist"]]

    # 1. value statements (no glossary) and the eval papers
    vs: dict[str, list[str]] = {}
    vs_count: Counter[str] = Counter()
    for c in rows:
        toks = g.value_statements(c.content, stoplist)
        if toks:
            vs[c.chunk_id] = toks
            vs_count[corpus.doc_paper[c.doc_id]] += 1
    seed = f"{pc['seed_base']}|{set_}"
    cand: dict[str, list[str]] = {}          # fields mode: chunk id -> candidate fields
    if fres is not None:
        # field mode: eligibility counts the chunks the lexical prefilter admits for a
        # planned field (a number in digits or words + a term or a unit of the field's
        # dimension), so a paper stating its setting in prose stays eligible
        names = set(fl.planned_names(fres["fields"], fc))
        planned = [f for f in fres["fields"] if f.field in names]
        field_count: Counter[str] = Counter()
        for c in rows:
            fs = fl.candidate_fields(c.content, planned)
            if fs:
                cand[c.chunk_id] = fs
                field_count[corpus.doc_paper[c.doc_id]] += 1
        eligible, papers = select_papers(dict(field_count), exclude, int(pc["n_papers"]),
                                         int(fc["min_field_chunks"]), seed)
    else:
        eligible, papers = select_papers(dict(vs_count), exclude, int(pc["n_papers"]),
                                         int(pc["min_vs_chunks"]), seed)
    eval_docs = {d for p in papers for d in corpus.paper_docs[p]}

    # 2. arms: resolve, then render the field of every pool chunk
    arm_paths = cc.get("arms") or {}
    reach_arms = [a for a in pc["reach_arms"] if a in arm_paths]
    missing = [a for a in pc["reach_arms"] if a not in arm_paths and a != FRONTIER_ARM]
    if missing:
        raise SystemExit(f"set {set_}: no glossary file for reach arm(s) {missing}")
    reading = "final" if FRONTIER_ARM in reach_arms else "dry"
    arms: dict[str, tuple[GlossaryFile, dict[str, Glossary], dict[str, int]]] = {
        a: load_arm(arm_paths[a], eval_docs, corpus, a) for a in reach_arms}

    pool: list[dict[str, Any]] = []
    for p in papers:
        for c in corpus.paper_chunks(p):
            toks = vs.get(c.chunk_id)
            if fres is not None:
                if c.chunk_id not in cand:
                    continue
            elif toks is None and not g.words_only_candidate(c.content, stoplist):
                continue
            reach = {a: render_reach(gl, gf, c) for a, (gf, gl, _) in arms.items()}
            stratum = ("S1" if any(reach.values()) else "S2") if toks else "S3"
            row = {"kind": "chunk", "set": set_, "paper": p, "doc_id": c.doc_id,
                   "chunk_id": c.chunk_id, "start_char": c.start_char, "end_char": c.end_char,
                   "content_sha256": g.sha_text(c.content), "vs_tokens": toks or [],
                   "stratum": stratum, "reach": reach, "sampled": False, "sample_rank": None}
            if fres is not None:
                row["field_candidates"] = cand[c.chunk_id]
                row["field_jobs"] = []
            pool.append(row)

    # 4. the seeded chunk sample (open mode) / the per-field quotas (fields mode)
    by_key = {r["chunk_id"]: r for r in pool}
    field_plan: dict[str, Any] | None = None
    if fres is not None:
        per_field: dict[str, list[str]] = {}
        for r in pool:
            for f in r["field_candidates"]:
                per_field.setdefault(f, []).append(r["chunk_id"])
        field_plan = fl.plan_fields(fres["fields"], fc, per_field, int(fc["target_jobs"]))
        jobs = fl.assign(field_plan, per_field, {r["chunk_id"]: r["paper"] for r in pool}, seed)
        for cid, fs in jobs.items():
            by_key[cid]["field_jobs"] = fs
            by_key[cid]["sampled"] = True
    else:
        caps = {"S1": int(pc["s1_per_paper"]), "S2": int(pc["s2_per_paper"]), "S3": int(pc["s3_per_paper"])}
        for p in papers:
            for s in STRATA:
                ids = [r["chunk_id"] for r in pool if r["paper"] == p and r["stratum"] == s]
                for rank, cid in enumerate(seeded_order(ids, f"{seed}|chunks|{s}|{p}")):
                    by_key[cid]["sample_rank"] = rank
                    by_key[cid]["sampled"] = rank < caps[s]

    # 3. counts, reach and the gate
    strata = Counter(r["stratum"] for r in pool)
    vs_pool = [r for r in pool if r["stratum"] in ("S1", "S2")]
    reach_counts = {a: sum(1 for r in vs_pool if r["reach"][a]) for a in reach_arms}
    reach_block: dict[str, Any] = {
        a: {"vs_chunks_reached": reach_counts[a], "vs_chunks": len(vs_pool),
            "share": (reach_counts[a] / len(vs_pool)) if vs_pool else None,
            "corpus": None} for a in reach_arms}
    if corpus_reach:
        all_docs = set(corpus.docs)
        for a in reach_arms:
            if a == FRONTIER_ARM:
                reach_block[a]["corpus"] = {"note": "eval papers only (frontier scope)"}
                continue
            gf, gl, _ = load_arm(arm_paths[a], all_docs, corpus, a)
            vs_all = [corpus.chunks[cid] for cid in vs]
            hit = sum(1 for c in vs_all if render_reach(gl, gf, c))
            reach_block[a]["corpus"] = {"vs_chunks_reached": hit, "vs_chunks": len(vs_all),
                                        "share": hit / len(vs_all) if vs_all else None}
    gate_n = int(pc["reach_gate"])
    sampled = Counter(r["stratum"] for r in pool if r["sampled"])
    prov = (provenance or g.experiment_provenance)()
    created = g.now_iso()

    pool_path = out / f"pool-{set_}.jsonl"
    header = {"kind": "header", "schema": POOL_SCHEMA, "set": set_, "created": created, "reading": reading,
              "seed": seed, "eval_papers": papers, "reach_arms": reach_arms,
              "paper_key": cc.get("paper_key", "doc_id"), "vs_stoplist": stoplist,
              "inputs_sha256": {"chunks": [g.file_sha(x) for x in cc["chunks"]], "exclude_papers": exclude_sha,
                                "arms": {a: file_sha256(arm_paths[a]) for a in reach_arms}},
              "commit": prov.get("commit"),
              "quotas_per_paper": {k: int(pc[k]) for k in PER_SET_KEYS}, "draft_mode": "fields" if fres is not None else "open"}
    if fres is not None:
        names = set(field_plan["fields"])
        header.update({"fields_sha256": fres["sha256"], "field_plan": field_plan, "in_set": fres["in_set"],
                       "constants": fres["constants"], "new_ground": fres["new_ground"],
                       "field_specs": [f.as_dict() for f in fres["fields"] if f.field in names],
                       "prefilter": PREFILTER_NOTE})
    tmp = pool_path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for row in [header, *pool]:
            fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    tmp.replace(pool_path)

    manifest = {
        "schema": MANIFEST_SCHEMA, "set": set_, "created": created, "reading": reading,
        "design": {**{k: pc[k] for k in ("n_papers", "min_vs_chunks", "s1_per_paper", "s2_per_paper",
                                         "s3_per_paper", "reach_gate")},
                   "reach_arms_configured": list(pc["reach_arms"]), "reach_arms_present": reach_arms,
                   "quotas_per_paper": {k: int(pc[k]) for k in PER_SET_KEYS},
                   "quotas_overridden": sorted((pc.get("per_set") or {}).get(set_) or {}),
                   "paper_key": cc.get("paper_key", "doc_id"), "seed": seed,
                   "chunk_seed": f"{seed}|chunks|<stratum>|<paper>",
                   "value_statement": "value_pattern on the chunk's own tokens (no glossary)",
                   "vs_stoplist": stoplist,
                   "attachment": "for_chunk, value-only (file config)"},
        "manipulation": {"chunks": len(rows), "expected_chunks": expected,
                         "match": None if expected is None else len(rows) == int(expected),
                         "arm_resolution": {a: arms[a][2] for a in reach_arms}},
        "counts": {"chunks": len(rows), "documents": len(corpus.docs), "papers": len(corpus.paper_docs),
                   "excluded_papers": len(exclude & set(corpus.paper_docs)),
                   "papers_with_vs": len(vs_count), "eligible_papers": len(eligible), "eval_papers": len(papers),
                   "vs_chunks_corpus": len(vs), "vs_chunks_eval": len(vs_pool),
                   "strata": {s: strata.get(s, 0) for s in STRATA},
                   "sampled": {s: sampled.get(s, 0) for s in STRATA}},
        "reach": reach_block,
        "gate": {"threshold": gate_n, "s1": strata.get("S1", 0), "reading": reading,
                 "status": "EVALUABLE" if strata.get("S1", 0) >= gate_n else "GATE-NOT-EVALUABLE"},
        "arms": {a: {"sha256": file_sha256(arm_paths[a]), "producer": arms[a][0].header.get("producer"),
                     "glossary_versions": arms[a][0].glossary_versions,
                     "file_counts": dict(arms[a][0].header.get("counts") or {})} for a in reach_arms},
        "fields": None if fres is None else {
            "draft_mode": "fields", "fields_sha256": fres["sha256"], "in_set": fres["in_set"],
            "constants": fres["constants"], "new_ground": fres["new_ground"], "plan": field_plan,
            "jobs": sum(len(r.get("field_jobs") or []) for r in pool), "prefilter": PREFILTER_NOTE},
        "eval_papers_sha256": g.sha_text("\n".join(papers)),
        "pool_sha256": g.file_sha(pool_path),
        "provenance": prov, "citable": g.citable(prov),
    }
    g.write_json_atomic(out / f"pool-manifest-{set_}.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--private-config", default=None, help=f"private JSON config (or env {g.CONFIG_ENV})")
    ap.add_argument("--out-dir", default=None, help="default: the config's out_dir")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="eval papers + pool + strata + reach gate for one set")
    b.add_argument("--set", required=True, choices=g.SETS)
    b.add_argument("--corpus-reach", action="store_true",
                   help="also report each local arm's reach over the whole corpus's value-statement chunks")
    b.add_argument("--in-set-records", default=None,
                   help="fields mode: the held set's records (JSONL); in_set is computed from them, sha recorded")
    args = ap.parse_args(argv)
    cfg, _ = g.load_config(args.private_config)
    out = g.out_dir(cfg, args.out_dir)
    m = build(cfg, args.set, out, corpus_reach=args.corpus_reach, in_set_records=args.in_set_records)
    print(json.dumps({"set": m["set"], "reading": m["reading"], "counts": m["counts"], "gate": m["gate"]},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
