#!/usr/bin/env python
"""Glossary benchmark round 3: eval papers, the arm-blind populations V and
M_num, per-stratum quotas and the gold-differs gate (prereg round 3 §3.1).

Round 3 holds the glossary fixed and makes the **attachment rule** the arm:
``value`` (the served rule, ``attach_only_with_value: true``) against
``mention`` (``false``), two files that differ in nothing but the header's rule
and the ``+attach=`` version mark (``glossary_file.py rederive --attach``).
This module is the round-3 counterpart of :mod:`glossbench_r2_pool`, which it
reuses and does not change (round 2's frozen record stays reproducible).

Offline and read-only: one corpus's chunk export (the 512 index) and the arm
files in; into the private output directory:

* ``pool-<set>.jsonl`` — PRIVATE (not written by ``--dry``). A header line,
  then one line per population chunk of the eval papers: ids, offsets,
  ``content_sha256``, ``vs_tokens`` (the chunk's value-statement tokens, V),
  ``sigma_tokens`` (the Σ(paper) tokens it mentions, M_num), the stratum
  (``V`` | ``M_num``), one reach boolean per arm, ``gold_differs`` and the
  seeded sample order. No glossary text, no chunk text. Row keys are round
  2's plus ``sigma_tokens`` and ``gold_differs``, so the drafter reads it as a
  round-2 pool (a stratum other than ``S3`` gets the symbol prompt).
* ``pool-manifest-<set>.json`` — counts only (no id, path, text or glossary
  entry), with ``experiment_provenance()``; per-paper densities by paper RANK.

Steps (prereg round 3 §3.1):

1. **Populations, per chunk, no glossary.** **V**: the chunk states a value
   (:func:`_glossbench_r2.value_statements`, the ``vs_stoplist``). **Σ(paper)**:
   the union of the V tokens over all chunks of the paper. **M_num**: no value
   statement, mentions a Σ(paper) token as a whole token (``symbol_pattern`` on
   the canonical text) and prints a number (:func:`_glossbench_r2.has_number`).
   Everything else is outside the population.
2. **Eligible papers**: V >= ``min_v`` and M_num >= ``min_m`` and V + M_num >=
   ``min_pop`` and (``require_glossary``) the MENTION arm's file renders a
   non-empty field on at least one chunk of the paper. The glossary is a fixed
   factor shared by both arms, so this condition cannot favour either rule.
   Papers of the ``exclude_papers`` file(s) are removed first.
3. **Paper sample**: ``random.Random("<seed_base>|<set>").sample`` of
   ``n_papers`` from the sorted eligible ids (all of them when fewer) — the
   draw of :func:`glossbench_r2_pool.select_papers`.
4. **Chunk sample**: per paper and stratum the seeded order
   ``<seed_base>|<set>|chunks|<stratum>|<paper>``; the first ``v_per_paper``
   V and ``m_per_paper`` M_num chunks are sampled (all when fewer).
5. **Gate, read before any draft**: the number of SAMPLED chunks whose rendered
   field differs between the value and the mention arm (each rendered by
   ``Glossary.for_chunk`` under its own file's config, as the backfill renders)
   must be >= ``gold_differs_gate`` and, per stratum, >= the
   ``gold_differs_gate_per_stratum``; else the set is ``GATE-NOT-EVALUABLE``.
   Each arm's reach (sampled and pool-wide, per stratum) is printed beside it.

Manipulation checks before anything is written: the export's chunk count equals
``expected_chunks`` when configured; the value arm's file says ``value`` and the
mention arm's ``mention`` (header rule, and every ``+attach=`` mark agrees with
it); the two files hold the same documents with identical halves, entries,
drops and identity, configs equal but for the rule, and versions equal but for
the attach mark (:func:`arm_pair_problems`); every arm file resolves every
document the build renders (no missing, mismatched or unverifiable row).

Usage::

    glossbench_r3_pool.py --private-config CFG build --set O [--dry] [--corpus-reach]

Config (private JSON; see :mod:`_glossbench_r2`)::

    out_dir                 private output directory (never inside the repo)
    corpora.<set>           {chunks: [paths], expected_chunks, paper_key, arms: {arm: glossary.jsonl},
                             exclude_papers: path | [paths] | null}
    pool_r3                 {seed_base, n_papers, min_v, min_m, min_pop, require_glossary, v_per_paper,
                             m_per_paper, gold_differs_gate, gold_differs_gate_per_stratum, value_arm,
                             mention_arm, reach_arms, vs_stoplist, per_set}         (defaults: DEFAULTS)

``per_set`` overrides (keys :data:`PER_SET_KEYS`) are merged over the
pre-registered ones in :data:`DEFAULTS` per set and per key; the effective
values are in the header and the manifest. ``reach_arms`` names further arms
(e.g. ``rules-value``, ``rules-mention``) whose reach is printed, descriptively.

Fields mode (``corpora.<set>.draft_mode: fields``, set P): populations,
eligibility, paper sample and gate are the same; round 2's arm-blind lexical
prefilter names each population chunk's candidate fields and round 2's quota plan
(:func:`glossbench_r2_fields.plan_strata`, on the strata V / M_num) assigns
``v_per_paper`` / ``m_per_paper`` jobs per paper and stratum. A chunk with a job is
"sampled" (the gate counts those chunks); the header carries the field plan and
specs the drafter reads, as in round 2.

**Field assignment by number, ranking by term** (fields mode, after the set-P
pilot). A (chunk, field) job is eligible iff the chunk prints a number whose unit is
dimension-compatible with the field (:data:`glossbench_r2_fields.RELATED` counts;
a dimensionless or count field takes a printed number with no incompatible unit:
:func:`glossbench_r2_fields.compatible`). The field's terms never exclude a chunk —
a term gate would drop exactly the symbol-only passages (``Qb = 0.5 m3/h``) the
glossary is meant to rescue and bias its measured effect toward zero — they RANK the
eligible chunks of a (paper, stratum, field) cell: chunks naming a term (the wording
proxy's normalisation and stemming) are drafted first, the seeded order within each
rank. Per row: ``field_prefilter`` (round 2's rule, a term OR a unit, recorded and
counted), ``field_candidates`` (eligible), ``field_terms`` (eligible fields whose
terms the chunk names); per job the drafter records ``term_in_chunk``, and the
manifest gives the share of planned jobs with and without a term (symbol-only) per
stratum. Populations, paper eligibility, the paper sample and the gate's definition
are untouched: only the field a P chunk is paired with changes (and with it which
chunks carry a job).

**Pre-drafting yield estimate** (every mode). The lesson of the set-P pilot: 88 of
100 first attempts came back ``{"none": true}`` and 7 passed layer 1 — and nothing
before the drafter said so; the share of jobs passing a lexical eligibility rule
would not have either (75 of the pilot's 100 jobs print a compatible number). The
build records ``yield.expected_yield`` (predicted layer-1 passes / target jobs):

* fields mode: every planned job gets a class (:func:`glossbench_r2_fields.job_class`:
  ``bound`` — a compatible number bound to the field by a term just before it in the
  same sentence or by a value statement; ``eligible``; ``ineligible``). With a
  calibration (``yield_calibration``: per-class layer-1 pass rates, written by the
  ``backtest`` subcommand from a drafted pilot) the estimate is the sum of the class
  rates; without one it is the ``bound`` share, an optimistic proxy (``calibrated:
  false``). ``backtest`` replays the estimator on a pilot's jobs as assigned and
  reports predicted against observed (in sample and leave-one-paper-out).
* open mode: the share of jobs whose chunk holds a value statement (V) or a printed
  number (M_num), recomputed from the text.

The plan's fill (jobs / target) is printed beside it. Below ``yield_gate`` (default
0.5) the set is ``YIELD-NOT-ADEQUATE`` (header and manifest) and the drafter refuses
to start without ``--allow-low-yield``; the drafter's yield watch (``yield_watch``)
is the guard during drafting.

``backtest --set P --drafts DRAFTS --out OUT [--negative-controls NC]`` (offline,
counts only): the estimator on the drafted jobs, the per-class observed rates (the
calibration written to OUT), and optionally the jobs whose first attempt answered
``{"none": true}`` as a labelled negative-control file (``chunk_id, field, stratum,
label``; no text).
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import _glossbench_r2 as g  # noqa: E402
import glossbench_r2_fields as fl  # noqa: E402
import glossbench_r2_pool as r2  # noqa: E402

from ragstack.ingestion.glossary_file import (  # noqa: E402
    GlossaryFile,
    attach_mark_problems,
    attach_rule_of_config,
    config_dict,
    file_sha256,
    read_glossary_file,
    resolve_documents,
    version_attach,
)
from ragstack.ingestion.symbol_glossary import (  # noqa: E402
    Glossary,
    canonical_text,
    symbol_pattern,
)

POOL_SCHEMA = "glossbench-r3-pool/1"
MANIFEST_SCHEMA = "glossbench-r3-pool-manifest/1"
STRATA = ("V", "M_num")
GATE = "GATE-NOT-EVALUABLE"
YIELD_LOW = "YIELD-NOT-ADEQUATE"
YIELD_OK = "ADEQUATE"
#: the pre-registered design (prereg round 3 §3.1); ``per_set`` holds the P and A sizes
DEFAULTS: dict[str, Any] = {
    "seed_base": "261103",
    "n_papers": 50,
    "min_v": 2,
    "min_m": 2,
    "min_pop": 10,
    "require_glossary": True,
    "v_per_paper": 6,
    "m_per_paper": 6,
    "gold_differs_gate": 100,
    "gold_differs_gate_per_stratum": 40,
    "value_arm": "value",
    "mention_arm": "mention",
    "reach_arms": [],
    "vs_stoplist": sorted(g.STAT_TOKENS),
    #: expected_yield below this -> YIELD-NOT-ADEQUATE (the drafter refuses without --allow-low-yield)
    "yield_gate": 0.5,
    #: fields mode: a calibration JSON from ``backtest`` (per-class layer-1 pass rates), or null
    "yield_calibration": None,
    "per_set": {
        "P": {"n_papers": 25, "gold_differs_gate": 60, "gold_differs_gate_per_stratum": 0},
        "A": {"n_papers": 25, "v_per_paper": 2, "m_per_paper": 6, "gold_differs_gate": 40,
              "gold_differs_gate_per_stratum": 0},
    },
}
PER_SET_KEYS = ("n_papers", "v_per_paper", "m_per_paper", "gold_differs_gate", "gold_differs_gate_per_stratum")
QUOTA_KEY = {"V": "v_per_paper", "M_num": "m_per_paper"}


def pool_config(cfg: Mapping[str, Any], set_: str | None = None) -> dict[str, Any]:
    """The round-3 pool config (``cfg["pool_r3"]`` over :data:`DEFAULTS`); with
    ``set_``, that set's ``per_set`` overrides applied (defaults' per set merged
    with the config's per set, key by key)."""
    given = dict(cfg.get("pool_r3") or {})
    unknown = set(given) - set(DEFAULTS)
    if unknown:
        raise SystemExit(f"pool_r3 config: unknown keys {sorted(unknown)}")
    per: dict[str, dict[str, Any]] = {s: dict(v) for s, v in DEFAULTS["per_set"].items()}
    for s_, over in (given.pop("per_set", None) or {}).items():
        if s_ not in g.SETS or not isinstance(over, dict) or set(over) - set(PER_SET_KEYS):
            raise SystemExit(f"pool_r3.per_set: {s_!r} must be a set with keys from {PER_SET_KEYS}")
        per[s_] = {**per.get(s_, {}), **over}
    p = {**DEFAULTS, **given, "per_set": per}
    if set_ is not None:
        p.update({k: int(v) for k, v in (per.get(set_) or {}).items()})
    if p["value_arm"] == p["mention_arm"]:
        raise SystemExit("pool_r3: value_arm and mention_arm must be two different arms")
    yg = p["yield_gate"]
    if isinstance(yg, bool) or not isinstance(yg, (int, float)) or not 0 <= float(yg) <= 1:
        raise SystemExit(f"pool_r3.yield_gate {yg!r}: a number in [0, 1]")
    return p


def read_exclusions(spec: str | list[str] | None) -> tuple[set[str], list[str]]:
    """Paper ids to exclude from one file or several (round 2's reader per file)."""
    paths = [spec] if isinstance(spec, str) else list(spec or [])
    ids: set[str] = set()
    shas: list[str] = []
    for p in paths:
        got, sha = r2.read_exclusions(p)
        ids |= got
        shas.append(str(sha))
    return ids, shas


# --------------------------------------------------------------------------- populations
def populations(rows: list[g.ChunkRow], corpus: g.Corpus, stoplist: list[str]
                ) -> tuple[dict[str, str], dict[str, list[str]], dict[str, list[str]], dict[str, set[str]]]:
    """``(stratum by chunk id — population chunks only, value-statement tokens by chunk,
    Σ tokens mentioned by M_num chunk, Σ by paper)``. Uses no glossary."""
    vs: dict[str, list[str]] = {}
    sigma: dict[str, set[str]] = {}
    for c in rows:
        toks = g.value_statements(c.content, stoplist)
        if toks:
            vs[c.chunk_id] = toks
            sigma.setdefault(corpus.doc_paper[c.doc_id], set()).update(toks)
    stratum: dict[str, str] = {}
    mentioned: dict[str, list[str]] = {}
    for c in rows:
        if c.chunk_id in vs:
            stratum[c.chunk_id] = "V"
            continue
        sig = sigma.get(corpus.doc_paper[c.doc_id])
        if not sig or not g.has_number(c.content):
            continue
        canon = canonical_text(c.content)
        hit = sorted(t for t in sig if symbol_pattern(t).search(canon))
        if hit:
            stratum[c.chunk_id] = "M_num"
            mentioned[c.chunk_id] = hit
    return stratum, vs, mentioned, sigma


# --------------------------------------------------------------------------- arms
def arm_pair_problems(value: GlossaryFile, mention: GlossaryFile) -> list[str]:
    """Why the two attachment arms are not the same glossary under two rules
    (empty = they are). Both files read in full."""
    out: list[str] = []
    rv, rm = attach_rule_of_config(value.config), attach_rule_of_config(mention.config)
    if rv != "value":
        out.append(f"the value arm's header rule is {rv!r}")
    if rm != "mention":
        out.append(f"the mention arm's header rule is {rm!r}")
    cv, cm = config_dict(value.config), config_dict(mention.config)
    diff = sorted(k for k in cv if k != "attach_only_with_value" and cv[k] != cm.get(k))
    if diff:
        out.append(f"the configs differ beyond the attachment rule: {diff}")
    for name, gf in (("value", value), ("mention", mention)):
        bad = attach_mark_problems(gf)
        if bad:
            out.append(f"the {name} arm's version marks disagree with its header ({len(bad)} rows, e.g. {bad[0]})")
    if set(value.rows) != set(mention.rows):
        out.append(f"the files hold different documents ({len(set(value.rows) ^ set(mention.rows))} differ)")
    n_bad = 0
    for d in sorted(set(value.rows) & set(mention.rows)):
        a, b = value.rows[d], mention.rows[d]
        same = (a.text_sha256 == b.text_sha256 and a.text_chars == b.text_chars
                and a.text_ws_sha256 == b.text_ws_sha256 and a.rules == b.rules and a.llm == b.llm
                and a.merged.entries == b.merged.entries and a.drops == b.drops and a.degraded == b.degraded
                and version_attach(a.glossary_version)[0] == version_attach(b.glossary_version)[0])
        n_bad += int(not same)
    if n_bad:
        out.append(f"{n_bad} document row(s) differ in more than the attach mark (halves, entries, identity, version)")
    return out


def load_arm(path: str, doc_ids: set[str], corpus: g.Corpus, arm: str) -> tuple[GlossaryFile, dict[str, Glossary],
                                                                              dict[str, int]]:
    """Round 2's :func:`glossbench_r2_pool.load_arm` without its value-only refusal:
    the arm's rule is whatever its file's header says (checked by the caller)."""
    gfile = read_glossary_file(path, doc_ids=doc_ids)
    res = resolve_documents(gfile, [corpus.docs[d] for d in sorted(doc_ids)])
    if res.problems or res.unverified:
        kinds = Counter(k for _, k in res.problems)
        raise SystemExit(f"arm {arm}: the glossary file does not resolve every document "
                         f"({dict(kinds)}; unverified {len(res.unverified)}): manipulation check failed")
    return gfile, res.glossaries, res.counts()


def render(arm: tuple[GlossaryFile, dict[str, Glossary], Any], chunk: g.ChunkRow) -> str:
    gf, gl, _ = arm
    return gl[chunk.doc_id].for_chunk(chunk.content, gf.config, start_char=chunk.start_char)


def _quantiles(xs: list[int]) -> dict[str, float] | None:
    if not xs:
        return None
    s = sorted(xs)

    def q(f: float) -> float:
        return float(s[int(round(f * (len(s) - 1)))])

    return {"n": len(s), "min": float(s[0]), "p25": q(.25), "median": q(.5), "p75": q(.75), "p90": q(.9),
            "max": float(s[-1])}


# --------------------------------------------------------------------------- yield
CALIBRATION_SCHEMA = "glossbench-r3-yield-calibration/1"
YIELD_DEFINITION = {
    "fields": "sum over planned jobs of the calibrated layer-1 pass rate of the job's class "
              "(bound / eligible / ineligible), over the target jobs; uncalibrated: the bound share",
    "open": "jobs whose chunk holds a value statement (V) or a printed number (M_num), over the target jobs"}


def read_calibration(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    rates = obj.get("rates") if isinstance(obj, dict) else None
    if obj.get("schema") != CALIBRATION_SCHEMA or not isinstance(rates, dict) or set(rates) != set(fl.JOB_CLASSES) \
            or not all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in rates.values()):
        raise SystemExit(f"yield_calibration {Path(path).name}: not a {CALIBRATION_SCHEMA} file with a rate "
                         f"in [0, 1] for each of {fl.JOB_CLASSES}")
    return {"rates": {k: float(v) for k, v in rates.items()}, "sha256": g.file_sha(path),
            "source": obj.get("source"), "jobs": obj.get("jobs")}


def term_ranks(rows: list[dict[str, Any]]) -> dict[tuple[str, str], int]:
    """(chunk id, field) -> 0 when the chunk names one of the field's terms, else 1."""
    return {(r["chunk_id"], f): (0 if f in (r.get("field_terms") or ()) else 1)
            for r in rows for f in r.get("field_candidates") or ()}


def content_ok(text: str, stratum: str, stoplist: list[str]) -> bool:
    """Open mode's content check for one job, recomputed from the chunk text."""
    if stratum == "V":
        return bool(g.value_statements(text, stoplist))
    return g.has_number(text)


def predict(classes: list[str], calibration: Mapping[str, Any] | None) -> float:
    """Predicted layer-1 passes of jobs of these classes."""
    if calibration is None:
        return float(sum(c == "bound" for c in classes))
    return float(sum(calibration["rates"][c] for c in classes))


def expected_yield(jobs: list[tuple[dict[str, Any], str | None]], corpus: g.Corpus, specs: Mapping[str, Any],
                   stoplist: list[str], target: int, gate: float, fields_mode: bool,
                   calibration: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The pre-drafting yield estimate (module docstring): per stratum and overall."""
    def block(sel: list[tuple[dict[str, Any], str | None]], tgt: int | None) -> dict[str, Any]:
        out: dict[str, Any] = {"jobs": len(sel), "target_jobs": tgt, "fill": (len(sel) / tgt) if tgt else None}
        if fields_mode:
            cls = [fl.job_class(corpus.chunks[r["chunk_id"]].content, specs[f]) for r, f in sel]
            pred = predict(cls, calibration)
            terms = sum(f in (r.get("field_terms") or ()) for r, f in sel)
            out.update({"classes": {c: cls.count(c) for c in fl.JOB_CLASSES}, "predicted_passes": round(pred, 3),
                        "term_in_chunk": {"true": terms, "false": len(sel) - terms,
                                          "share_true": (terms / len(sel)) if sel else None}})
        else:
            pred = float(sum(content_ok(corpus.chunks[r["chunk_id"]].content, r["stratum"], stoplist)
                             for r, _ in sel))
            out["jobs_passing"] = int(pred)
        out["share_of_jobs"] = (pred / len(sel)) if sel else None
        out["expected_yield"] = (pred / tgt) if tgt else (None if tgt is None else 0.0)
        return out

    out = block(jobs, int(target))
    y = float(out["expected_yield"] or 0.0)
    out.update({"by_stratum": {s: block([(r, f) for r, f in jobs if r["stratum"] == s], None) for s in STRATA},
                "gate": gate, "status": YIELD_OK if y >= gate else YIELD_LOW,
                "mode": "fields" if fields_mode else "open",
                "calibrated": bool(calibration) if fields_mode else None,
                "calibration": None if not calibration else {k: calibration[k] for k in ("rates", "sha256", "jobs")},
                "definition": YIELD_DEFINITION["fields" if fields_mode else "open"]})
    return out


def assignment_counts(pool: list[dict[str, Any]], plan: Mapping[str, Any]) -> dict[str, Any]:
    """Fields mode: round 2's prefilter against the number gate over the eval papers'
    population chunks (pairs, chunks, papers), term presence among the eligible pairs,
    and the plan's shortfalls."""
    def cnt(key: str, sel: list[dict[str, Any]]) -> dict[str, int]:
        return {"pairs": sum(len(r.get(key) or []) for r in sel),
                "chunks": sum(bool(r.get(key)) for r in sel),
                "papers": len({r["paper"] for r in sel if r.get(key)})}

    def both(sel: list[dict[str, Any]]) -> dict[str, Any]:
        return {"prefilter_round2": cnt("field_prefilter", sel), "eligible": cnt("field_candidates", sel),
                "eligible_with_term": cnt("field_terms", sel)}

    return {"rule": "a printed number of a compatible dimension; terms rank, never exclude",
            **both(pool), "by_stratum": {s: both([r for r in pool if r["stratum"] == s]) for s in STRATA},
            "papers": len({r["paper"] for r in pool}),
            "shortfall": {s: {k: v[k] for k in ("target", "shortfall", "unplaced", "realised") if k in v}
                          for s, v in (plan.get("strata") or {}).items()}}


def backtest(cfg: dict[str, Any], set_: str, drafts_path: str, out_path: Path, pool_path: str | None = None,
             negative_controls: Path | None = None, provenance: Any = None) -> dict[str, Any]:
    """The yield estimator replayed on drafted jobs as they were assigned (a pilot):
    per class, jobs and observed layer-1 passes (a job passes when any attempt did);
    predicted against observed — uncalibrated (the bound share), the share eligible
    under the number gate, calibrated in sample, and calibrated leave-one-paper-out
    (each paper's jobs predicted from the other papers' class rates; a class with no
    job elsewhere takes the others' overall rate). Writes the calibration (the in-sample
    class rates) to ``out_path``; counts only. ``negative_controls``: the jobs whose
    first attempt answered ``{"none": true}`` (``no_quantity``), ``chunk_id, field,
    stratum, label`` — no text."""
    for p_ in (out_path, negative_controls):
        if p_ is not None:
            g.refuse_in_repo(p_, "the backtest output")
    cc = g.corpus_config(cfg, set_)
    pp = Path(pool_path) if pool_path else g.out_dir(cfg) / f"pool-{set_}.jsonl"
    header = json.loads(pp.read_text(encoding="utf-8").split("\n", 1)[0])
    specs = {d["field"]: fl.FieldSpec.from_dict(d) for d in header.get("field_specs") or []}
    if not specs:
        raise SystemExit("backtest: the pool header has no field specs (fields mode only)")
    rows = [json.loads(x) for x in Path(drafts_path).read_text(encoding="utf-8").splitlines() if x.strip()]
    jobs: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        if r.get("field") is None:
            raise SystemExit("backtest: a draft row without a field (fields mode only)")
        jobs.setdefault((r["chunk_id"], r["field"]), []).append(r)
    need = {c for c, _ in jobs}
    text = {c.chunk_id: c.content for c in g.read_chunks(cc["chunks"]) if c.chunk_id in need}
    missing = need - set(text)
    if missing:
        raise SystemExit(f"backtest: {len(missing)} drafted chunks are not in the export")
    recs = []
    for (cid, f), rs in sorted(jobs.items()):
        first = min(rs, key=lambda x: x["attempt"])
        recs.append({"chunk_id": cid, "field": f, "paper": first["paper"], "stratum": first["stratum"],
                     "class": fl.job_class(text[cid], specs[f]), "term_in_chunk": bool(fl.terms_present(text[cid],
                                                                                              specs[f])),
                     "eligible": fl.field_eligible(text[cid], specs[f]),
                     "passed": any((x.get("l1_inline") or {}).get("verdict") == "pass" for x in rs),
                     "first_none": "no_quantity" in ((first.get("l1_inline") or {}).get("reasons") or [])})
    n = len(recs)
    obs = sum(r["passed"] for r in recs)

    def rates(sel: list[dict[str, Any]]) -> dict[str, float]:
        base = (sum(r["passed"] for r in sel) / len(sel)) if sel else 0.0
        out = {}
        for c in fl.JOB_CLASSES:
            cs = [r for r in sel if r["class"] == c]
            out[c] = (sum(r["passed"] for r in cs) / len(cs)) if cs else base
        return out

    cal = rates(recs)
    lopo = 0.0
    for p_ in sorted({r["paper"] for r in recs}):
        rt = rates([r for r in recs if r["paper"] != p_])
        lopo += sum(rt[r["class"]] for r in recs if r["paper"] == p_)
    by_class = {c: {"jobs": sum(r["class"] == c for r in recs), "passed": sum(r["passed"] for r in recs
                                                                            if r["class"] == c)}
                for c in fl.JOB_CLASSES}
    prov = (provenance or g.experiment_provenance)()
    res = {"schema": CALIBRATION_SCHEMA, "set": set_, "source": "backtest of drafted jobs as assigned",
           "drafts_sha256": g.file_sha(drafts_path), "pool_sha256": g.file_sha(pp), "jobs": n,
           "papers": len({r["paper"] for r in recs}), "observed": {"passes": obs, "rate": obs / n if n else None},
           "by_class": by_class, "rates": cal,
           "predicted": {"uncalibrated_bound_share": (by_class["bound"]["jobs"] / n) if n else None,
                         "number_gate_share": (sum(r["eligible"] for r in recs) / n) if n else None,
                         "calibrated_in_sample": (sum(cal[r["class"]] for r in recs) / n) if n else None,
                         "calibrated_leave_one_paper_out": (lopo / n) if n else None},
           "term_in_chunk": {"true": sum(r["term_in_chunk"] for r in recs),
                             "passed_true": sum(r["passed"] for r in recs if r["term_in_chunk"])},
           "first_attempt_none": sum(r["first_none"] for r in recs),
           "window_chars": fl.BOUND_WINDOW, "provenance": prov, "citable": g.citable(prov), "created": g.now_iso()}
    g.write_json_atomic(out_path, res)
    if negative_controls is not None:
        tmp = negative_controls.with_suffix(negative_controls.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for r in recs:
                if r["first_none"]:
                    fh.write(json.dumps({"chunk_id": r["chunk_id"], "field": r["field"], "stratum": r["stratum"],
                                         "label": "no_quantity"}, sort_keys=True) + "\n")
        tmp.replace(negative_controls)
    return res


# --------------------------------------------------------------------------- build
def build(cfg: dict[str, Any], set_: str, out: Path, *, dry: bool = False, corpus_reach: bool = False,
          provenance: Any = None, in_set_records: str | None = None) -> dict[str, Any]:
    pc = pool_config(cfg, set_)
    cc = g.corpus_config(cfg, set_)
    fc = fl.field_config(cc, set_)
    fields_mode = fc is not None
    fres = fl.resolve_fields(fc, in_set_records) if fc is not None else None
    rows = g.read_chunks(cc["chunks"])
    expected = cc.get("expected_chunks")
    if expected is not None and len(rows) != int(expected):
        raise SystemExit(f"set {set_}: the export holds {len(rows)} chunks, expected {expected}: "
                         "manipulation check failed")
    corpus = g.build_corpus(rows, cc.get("paper_key", "doc_id"))
    exclude, exclude_shas = read_exclusions(cc.get("exclude_papers"))
    stoplist = [str(t) for t in pc["vs_stoplist"]]
    arm_paths = cc.get("arms") or {}
    va, ma = pc["value_arm"], pc["mention_arm"]
    extra = [a for a in pc["reach_arms"] if a not in (va, ma)]
    missing = [a for a in (va, ma, *extra) if a not in arm_paths]
    if missing:
        raise SystemExit(f"set {set_}: no glossary file for arm(s) {missing}")

    # manipulation: the two attachment arms are one glossary under two rules
    pair = arm_pair_problems(read_glossary_file(arm_paths[va]), read_glossary_file(arm_paths[ma]))
    if pair:
        raise SystemExit(f"set {set_}: the attachment arms fail the identity check: {'; '.join(pair)}")

    # 1. populations (no glossary)
    stratum, vs, mentioned, sigma = populations(rows, corpus, stoplist)
    dens: dict[str, Counter[str]] = {s: Counter() for s in STRATA}
    for cid, s in stratum.items():
        dens[s][corpus.doc_paper[corpus.chunks[cid].doc_id]] += 1

    # 2. eligibility: densities, then the fixed glossary renders under mention
    papers_all = sorted(corpus.paper_docs)
    dense = [p for p in papers_all if p not in exclude and dens["V"][p] >= int(pc["min_v"])
             and dens["M_num"][p] >= int(pc["min_m"]) and dens["V"][p] + dens["M_num"][p] >= int(pc["min_pop"])]
    glossary_chunks: Counter[str] = Counter()
    dense_chunks = 0
    if dense:
        dense_docs = {d for p in dense for d in corpus.paper_docs[p]}
        m_dense = load_arm(arm_paths[ma], dense_docs, corpus, ma)
        for p in dense:
            for c in corpus.paper_chunks(p):
                dense_chunks += 1
                glossary_chunks[p] += int(bool(render(m_dense, c)))
    eligible = [p for p in dense if glossary_chunks[p] > 0 or not pc["require_glossary"]]

    # 3. the seeded paper sample (round 2's draw)
    seed = f"{pc['seed_base']}|{set_}"
    papers = sorted(random.Random(seed).sample(eligible, min(int(pc["n_papers"]), len(eligible))))
    eval_docs = {d for p in papers for d in corpus.paper_docs[p]}

    # reach and gold-differs over the eval papers' population chunks
    arms = {a: load_arm(arm_paths[a], eval_docs, corpus, a) for a in (va, ma, *extra)} if eval_docs else {}
    pool: list[dict[str, Any]] = []
    for p in papers:
        for c in corpus.paper_chunks(p):
            s = stratum.get(c.chunk_id)
            if s is None:
                continue
            fields_ = {a: render(arm, c) for a, arm in arms.items()}
            pool.append({"kind": "chunk", "set": set_, "paper": p, "doc_id": c.doc_id, "chunk_id": c.chunk_id,
                         "start_char": c.start_char, "end_char": c.end_char,
                         "content_sha256": g.sha_text(c.content), "vs_tokens": vs.get(c.chunk_id, []),
                         "sigma_tokens": mentioned.get(c.chunk_id, []), "stratum": s,
                         "reach": {a: bool(f) for a, f in fields_.items()},
                         "gold_differs": fields_[va] != fields_[ma],
                         "sampled": False, "sample_rank": None})

    # 4. the seeded chunk sample (open mode) / the per-field job plan (fields mode)
    by_key = {r["chunk_id"]: r for r in pool}
    field_plan: dict[str, Any] | None = None
    per_paper_q = {s: int(pc[QUOTA_KEY[s]]) for s in STRATA}
    if fres is not None:
        assert fc is not None
        # round 2's fields machinery on round 3's strata: the arm-blind lexical prefilter
        # names each population chunk's candidate fields; the quota plan assigns the jobs
        # (v_per_paper / m_per_paper jobs per paper and stratum); a chunk with a job is sampled
        names = set(fl.planned_names(fres["fields"], fc))
        planned = [f for f in fres["fields"] if f.field in names]
        for r in pool:
            text = corpus.chunks[r["chunk_id"]].content
            # round 2's prefilter (a term OR a unit), recorded and counted; the plan runs
            # over the content-eligible pairs (a term AND a compatible unit) only
            r["field_prefilter"] = fl.candidate_fields(text, planned)
            r["field_candidates"] = fl.eligible_fields(text, planned)
            r["field_terms"] = [f.field for f in planned
                                if f.field in r["field_candidates"] and fl.terms_present(text, f)]
            r["field_jobs"] = []
        tj = fc.get("target_jobs")
        targets = fl.cell_targets(papers, per_paper_q, None if tj is None else int(tj))
        cand_rows = [r for r in pool if r["field_candidates"]]
        field_plan, jobs = fl.plan_strata(fres["fields"], fc, cand_rows, targets, seed, rank=term_ranks(cand_rows))
        field_plan["per_paper"] = per_paper_q
        for cid, fs in jobs.items():
            by_key[cid]["field_jobs"] = fs
            by_key[cid]["sampled"] = True
    else:
        for p in papers:
            for s in STRATA:
                ids = [r["chunk_id"] for r in pool if r["paper"] == p and r["stratum"] == s]
                for rank, cid in enumerate(r2.seeded_order(ids, f"{seed}|chunks|{s}|{p}")):
                    by_key[cid]["sample_rank"] = rank
                    by_key[cid]["sampled"] = rank < int(pc[QUOTA_KEY[s]])

    # 4b. the pre-drafting yield estimate (offline proxy; prevents a pilot like set P's)
    if field_plan is not None:
        assert fres is not None
        specs = {f.field: f for f in fres["fields"]}
        jobs_ = [(r, f) for r in pool for f in r.get("field_jobs") or []]
        target = int(field_plan["total_target"])
    else:
        specs = {}
        jobs_ = [(r, None) for r in pool if r["sampled"]]
        target = len(papers) * sum(per_paper_q.values())
    yld = expected_yield(jobs_, corpus, specs, stoplist, target, float(pc["yield_gate"]), fields_mode,
                         read_calibration(pc["yield_calibration"]) if fields_mode else None)
    if field_plan is not None:
        yld["assignment"] = assignment_counts(pool, field_plan)

    # 5. counts and the gate
    def tally(sel: list[dict[str, Any]]) -> dict[str, Any]:
        return {"chunks": len(sel), "gold_differs": sum(r["gold_differs"] for r in sel),
                "reach": {a: sum(r["reach"][a] for r in sel) for a in arms}}

    sampled = [r for r in pool if r["sampled"]]
    by_stratum = {s: {"pool": tally([r for r in pool if r["stratum"] == s]),
                      "sampled": tally([r for r in sampled if r["stratum"] == s])} for s in STRATA}
    gd_total = sum(r["gold_differs"] for r in sampled)
    gd_per = {s: by_stratum[s]["sampled"]["gold_differs"] for s in STRATA}
    gate_n, gate_s = int(pc["gold_differs_gate"]), int(pc["gold_differs_gate_per_stratum"])
    short = [f"total {gd_total} < {gate_n}"] if gd_total < gate_n else []
    short += [f"{s} {gd_per[s]} < {gate_s}" for s in STRATA if gd_per[s] < gate_s]
    gate = {"threshold": gate_n, "threshold_per_stratum": gate_s, "gold_differs_sampled": gd_total,
            "gold_differs_sampled_by_stratum": gd_per, "status": GATE if short else "EVALUABLE",
            "reason": "; ".join(short) or None,
            "definition": "sampled chunks whose rendered field differs between the value and the mention arm"}

    # per-paper densities by rank (no ids): population size descending, ties by sorted id
    order = sorted(papers, key=lambda p: (-(dens["V"][p] + dens["M_num"][p]), p))
    per_paper = []
    for rank, p in enumerate(order, 1):
        rs = [r for r in pool if r["paper"] == p]
        sm = [r for r in rs if r["sampled"]]
        per_paper.append({
            "rank": rank, "chunks": len(corpus.paper_chunks(p)), "V": dens["V"][p], "M_num": dens["M_num"][p],
            "sigma_size": len(sigma.get(p, ())), "mention_glossary_chunks": glossary_chunks[p],
            "sampled": {s: sum(r["stratum"] == s for r in sm) for s in STRATA},
            "gold_differs_sampled": {s: sum(r["gold_differs"] for r in sm if r["stratum"] == s) for s in STRATA},
            "reach_sampled": {a: sum(r["reach"][a] for r in sm) for a in arms}})

    corpus_block = None
    if corpus_reach:
        corpus_block = {}
        all_docs = set(corpus.docs)
        for a in (va, ma, *extra):
            arm = load_arm(arm_paths[a], all_docs, corpus, a)
            hit = Counter()
            for c in rows:
                if render(arm, c):
                    hit["chunks_with_field"] += 1
                    hit[stratum.get(c.chunk_id, "outside")] += 1
            corpus_block[a] = {"chunks_with_field": hit["chunks_with_field"],
                               "by_population": {k: hit[k] for k in (*STRATA, "outside")}}

    prov = (provenance or g.experiment_provenance)()
    created = g.now_iso()
    reading = "dry" if dry else "final"
    quotas = {k: int(pc[k]) for k in PER_SET_KEYS}
    rules = {a: attach_rule_of_config(arms[a][0].config) for a in arms} if arms else {}
    pool_sha = None
    if not dry:
        header = {"kind": "header", "schema": POOL_SCHEMA, "set": set_, "created": created, "reading": reading,
                  "seed": seed, "eval_papers": papers, "reach_arms": list(arms), "value_arm": va,
                  "mention_arm": ma, "attachment": rules, "paper_key": cc.get("paper_key", "doc_id"),
                  "vs_stoplist": stoplist, "strata": list(STRATA),
                  "inputs_sha256": {"chunks": [g.file_sha(x) for x in cc["chunks"]], "exclude_papers": exclude_shas,
                                    "arms": {a: file_sha256(arm_paths[a]) for a in arms}},
                  "commit": prov.get("commit"), "quotas_per_paper": quotas,
                  "draft_mode": "fields" if fres is not None else "open", "yield": yld}
        if fres is not None:
            assert field_plan is not None
            fnames = set(field_plan["fields"])
            header.update({"fields_sha256": fres["sha256"], "field_plan": field_plan, "in_set": fres["in_set"],
                           "constants": fres["constants"], "new_ground": fres["new_ground"],
                           "field_specs": [f.as_dict() for f in fres["fields"] if f.field in fnames],
                           "prefilter": r2.PREFILTER_NOTE})
        pool_path = out / f"pool-{set_}.jsonl"
        tmp = pool_path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for row in [header, *pool]:
                fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
        tmp.replace(pool_path)
        pool_sha = g.file_sha(pool_path)

    manifest = {
        "schema": MANIFEST_SCHEMA, "set": set_, "created": created, "reading": reading,
        "design": {**{k: pc[k] for k in ("n_papers", "min_v", "min_m", "min_pop", "require_glossary",
                                         "v_per_paper", "m_per_paper", "gold_differs_gate",
                                         "gold_differs_gate_per_stratum", "value_arm", "mention_arm",
                                         "yield_gate")},
                   "reach_arms": [va, ma, *extra], "quotas_per_paper": quotas,
                   "quotas_overridden": sorted(pc["per_set"].get(set_) or {}),
                   "paper_key": cc.get("paper_key", "doc_id"), "seed": seed,
                   "chunk_seed": f"{seed}|chunks|<stratum>|<paper>", "vs_stoplist": stoplist,
                   "populations": {"V": "value_statements non-empty (no glossary)",
                                   "M_num": "no value statement; mentions a Sigma(paper) token (symbol_pattern); "
                                            "prints a number (has_number)"},
                   "attachment": rules, "fields_mode": fields_mode},
        "manipulation": {"chunks": len(rows), "expected_chunks": expected,
                         "match": None if expected is None else len(rows) == int(expected),
                         "arm_pair_identity": "ok",
                         "arm_resolution": {a: arms[a][2] for a in arms}},
        "counts": {"chunks": len(rows), "documents": len(corpus.docs), "papers": len(papers_all),
                   "excluded_papers": len(exclude & set(papers_all)),
                   "population_corpus": {s: sum(dens[s].values()) for s in STRATA},
                   "papers_dense": len(dense), "papers_eligible": len(eligible),
                   "papers_dense_without_glossary": len(dense) - len(eligible) if pc["require_glossary"] else 0,
                   "eval_papers": len(papers),
                   "eligible_paper_chunks_with_mention_field": {
                       "chunks": sum(len(corpus.paper_chunks(p)) for p in eligible),
                       "with_field": sum(glossary_chunks[p] for p in eligible)},
                   "dense_paper_chunks": dense_chunks,
                   "by_stratum": by_stratum},
        "eligible_density": {s: _quantiles([dens[s][p] for p in eligible]) for s in STRATA},
        "per_paper_by_rank": per_paper,
        "gate": gate,
        "yield": yld,
        "corpus_reach": corpus_block,
        "fields": None if fres is None else {
            "draft_mode": "fields", "fields_sha256": fres["sha256"], "in_set": fres["in_set"],
            "constants": fres["constants"], "new_ground": fres["new_ground"],
            "plan": {**{k: v for k, v in (field_plan or {}).items() if k != "cells"},
                     "cells": fl.anonymised_cells(field_plan or {"cells": {}})},
            "jobs": sum(len(r.get("field_jobs") or []) for r in pool), "prefilter": r2.PREFILTER_NOTE,
            "sampled_means": "a chunk with at least one field job"},
        "arms": {a: {"sha256": file_sha256(arm_paths[a]), "producer": arms[a][0].header.get("producer"),
                     "glossary_versions": arms[a][0].glossary_versions, "attachment": rules[a],
                     "file_counts": dict(arms[a][0].header.get("counts") or {})} for a in arms},
        "eval_papers_sha256": g.sha_text("\n".join(papers)),
        "pool_sha256": pool_sha,
        "provenance": prov, "citable": g.citable(prov),
    }
    g.write_json_atomic(out / f"pool-manifest-{set_}.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--private-config", default=None, help=f"private JSON config (or env {g.CONFIG_ENV})")
    ap.add_argument("--out-dir", default=None, help="default: the config's out_dir")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="eval papers + V/M_num pool + quotas + gold-differs gate for one set")
    b.add_argument("--set", required=True, choices=g.SETS)
    b.add_argument("--dry", action="store_true", help="the manifest only (no pool file to draft from)")
    b.add_argument("--in-set-records", default=None,
                   help="fields mode: the held set's records (JSONL); in_set is computed from them, sha recorded")
    b.add_argument("--corpus-reach", action="store_true",
                   help="also count every arm's chunks with a field over the whole corpus, by population")
    bt = sub.add_parser("backtest", help="the yield estimator on drafted jobs (a pilot); writes the calibration")
    bt.add_argument("--set", required=True, choices=g.SETS)
    bt.add_argument("--drafts", required=True, help="the drafter's draft-<set>[-pilot].jsonl")
    bt.add_argument("--pool", default=None, help="default: <out_dir>/pool-<set>.jsonl")
    bt.add_argument("--out", required=True, help="the calibration / backtest JSON (private, outside the repo)")
    bt.add_argument("--negative-controls", default=None,
                    help="also write the first-attempt no_quantity jobs (chunk_id, field, stratum; no text)")
    args = ap.parse_args(argv)
    cfg, _ = g.load_config(args.private_config)
    if args.cmd == "backtest":
        r = backtest(cfg, args.set, args.drafts, Path(args.out), args.pool,
                     Path(args.negative_controls) if args.negative_controls else None)
        print(json.dumps({k: r[k] for k in ("jobs", "papers", "observed", "predicted", "by_class", "rates",
                                            "term_in_chunk", "first_attempt_none")}, sort_keys=True))
        return 0
    out = g.out_dir(cfg, args.out_dir)
    m = build(cfg, args.set, out, dry=args.dry, corpus_reach=args.corpus_reach,
              in_set_records=args.in_set_records)
    print(json.dumps({"set": m["set"], "reading": m["reading"], "counts": {
        k: m["counts"][k] for k in ("papers_eligible", "eval_papers", "population_corpus")},
        "gate": m["gate"], "yield": {k: m["yield"][k] for k in ("expected_yield", "gate", "status")}},
        sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
