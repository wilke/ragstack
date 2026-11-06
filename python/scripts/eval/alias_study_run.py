#!/usr/bin/env python
"""Alias study (OA-1000): the retrieval runner (DRAFT v2 prereg §5, §9). An adapter around
round 3's found@10 runner (``glossbench/found10.py``): every round-3 gate is kept, and the
study adds what its prereg asks on top.

    alias_study_run.py --config CFG plan
    alias_study_run.py --config CFG gold-hashes --base-export CHUNKS.jsonl
    alias_study_run.py --config CFG run --stage {A,B} --corpus-arm base --glossary none [--repeat 2] \\
                       [--chunk-export X --glossary-file F | --rendered-fields R] \\
                       [--boost-declared B] [--probe-max-glossaries N]
    alias_study_run.py --config CFG placebo-glossary --merged MERGED.jsonl --base-export CHUNKS.jsonl \\
                       --out PLACEBO.jsonl

**Legs (§5.4).** Four corpus arms (``base``, ``neighbour``, ``random1``, ``random2``) x two
glossary arms (``none``, ``merged``) x three retrievers (``default`` = found10's ``base``
condition: hybrid + rerank; ``bm25``; ``norerank``) = 24 cells, plus one noise repeat per
corpus arm (default x none, ``--repeat 2``), plus the **placebo leg** (base x default x
``placebo``, §5.3, R7) = **29 legs** (:func:`study_legs`).

**One collection per attachment (owner, 2026-11-03).** Each (corpus arm, glossary arm) is its
own bench collection, loaded once from the corpus arm's embedding shards with that arm's
glossary file (``load-embeddings.cwl``; the none arm from an explicit file of empty rows with
its own stamp) and never written again: base x {none, merged,
placebo} and the three expanded arms x {none, merged}, nine collections
(``collections.<corpus>.<glossary>``: ``collection``, ``index``, ``load_receipt``, optional
``es_id_prefix`` / ``bm25_scope``). The runner triggers no backfill, swap or store write of any
kind. Its build manifest is the load receipt (:func:`check_load_receipt`: no failed shard, the
corpus arm's chunk count, the arm's glossary file sha256, and for none a file that renders no
field), and every leg reads the collection's
live census before and after (:meth:`AliasRunner._census_ok`: the chunk total, every chunk's
``metadata.symbol_glossary_version`` one of the arm's stamps and of the loaded file's
versions; a chunk stamped for another arm, or unstamped, refuses the run). An arm whose stamps
carry ``+attach=mention`` must declare ``attach: "mention"``, so the census also checks the
attach slot (§5.3, R27). Restore points are gone with the
swaps: a collection is immutable after its load, verified by census at the start and end of
every leg. One ``run`` measures the legs of one attachment that belong to its ``--stage``. A repeat run measures ``default`` only, in the
seeded order ``sha256(261101|alias|noise|<corpus arm>|<record>|<condition>)`` (§5.5), so a
repeat is not a replay of the first leg's request sequence. Every record of the accepted file
(the 900 balanced and the 191 descriptive) is run in every leg; every call asks ``top_k`` 50,
so the rows carry the top 10 and the depth-50 pool for diagnostics.

**Two stages (§5.7, R9; owner decisions of 2026-11-03).** Every leg has a **retrieval stage**
and a **judging stage** (:func:`leg_stages`). Retrieval stage A is 19 legs
(:data:`RETRIEVAL_A_LEGS`): the five Layer-1 attachments (base x none, base x merged,
neighbour / random-1 / random-2 x none, :data:`LAYER1_ATTACHMENTS`) at all three retrievers,
and the noise repeat of each corpus arm. So each Layer-1 attachment is run once, found10 computes the inactive-reranker gate (§9.1) inside that run
(``conditions.base.identical_to_norerank`` and ``conditions.base.gate``), and every Layer-1
gate is readable after stage A. Judging stage A is 14 of them (:data:`JUDGING_A_LEGS`):
default and norerank of the five attachments and the repeats; the five **bm25** legs are
retrieved in stage A but **judged in stage B**. Retrieval stage B is the other 9 cells and the
placebo leg. ``run --stage`` names the retrieval stage: ``A`` is refused once stage A is
committed (the judge's ``commit-stage-a`` writes ``<out_dir>/STAGE-A-COMMITTED``), ``B`` is
refused until then and verifies the commit before it measures anything. Every attachment is
retrieved in one stage only, so one run writes one file pair, ``<corpus>/<glossary>.{jsonl,
summary.json}`` (:func:`leg_files`).

**Modes.** ``dry_run: true`` (``alias_study_dryrun.py``) skips the record-set identity and
must name no study record. ``pilot: true`` is the pre-freeze pilot (§5.6): the record set is
verified, but only the 191 records on unsatisfied papers are run. Otherwise the run is the main
run and the config must pin the frozen prereg (``prereg: {file, sha256}``); a sha mismatch is a
hard stop (§9.2). The out dir carries a ``STUDY-MODE`` marker, and a pilot dir is never reused
by the main run or the reverse (the pilot's labels are not carried over, §5.6). A pilot leg
summary carries no found@10 level per condition (:data:`PILOT_WITHHELD_CONDITION_KEYS`; R28,
owner 2026-11-03: the pilot stays blind to outcomes); the rows keep every rank, privately.

``rechunk: {label_table: PATH}`` is the **exploratory rechunk stage 1** (never with ``dry_run``
or ``pilot``; the prereg is optional): the four corpus arms re-chunked under the confirmatory
control's chunker, one none collection each (``collections.<arm>.none`` only; ``arms`` needs
only ``none``; no ``gold_hashes``). The record set is verified as in the main run, then only the
records of the label table's pairs (``rechunk_prep.py gold-map``) are kept, and each run of a
corpus arm asks the records with a pair in that arm. Gold presence is replaced by
``rechunk_prep.check_rechunk_presence``: every chunk id the table maps for the arm's pairs (the
max-overlap chunk and the cover-any chunks) must be in the arm's new export. Retriever settings,
``top_k`` 50, ``rerank_candidates`` 50, the load-receipt and census checks and the provenance are
the main run's. Run it with ``--stage A`` in a fresh out dir (every ``<arm> x none`` leg is a
retrieval stage-A leg). The summaries carry ``mode: rechunk`` and ``exploratory: true`` (and the
``study`` block the same); found10's gold-id levels are left out of them (the gold ids are stage-A
chunk ids); outcomes come from ``rechunk_stage1.py``.

**Gates kept from round 3** (found10): the live census before and after (here against the load
receipt, above, in place of round 3's swap record); the depth read-back on every call, with the BM25
exhaustion verifier (``exhausted_verified`` scored, ``clamp`` / ``query_mismatch`` hard
stops, ``under_served`` gated); the glossary-boost probe (``active``, or ``declared`` when not
testable; never ``pass``); hit stamps on bm25 hits; the inactive-reranker gate; > 2 % failed
calls a hard stop; PAUSE / SIGTERM / resume; at most 4 in flight; provenance and ``citable``.

**Added for the study.**

1. *Admin config hash (§9.2).* ``/v1/config`` must be readable (an admin credential) at the
   start, and is read again after the last call; the two canonical-JSON sha256s must be equal,
   else the run is a hard stop. Both hashes are in the summary.
2. *Gold presence (§9.2).* Every record's gold chunk must be in the corpus arm's chunk export
   (``corpora.<arm>.chunk_export``) with the content hash ``gold-hashes`` recorded from the base
   export: an expanded arm holds the base chunks with identical ids and content (§5.1).
3. *Collections (§9.3 as amended by the owner, 2026-11-03).* The load receipt and the live
   census, above; ``census.collection_build`` in the summary.
4. *Failed calls per leg (§7, §9.2).* More than 2 % failed calls in any ONE retriever's leg is
   a hard stop (round 3 counted over the run).
5. *The record set (§4.1).* The accepted file's sha256 and the balanced-set id sha must be the
   prereg's (not in a dry run).
6. *The placebo glossary (§5.3, R7).* ``placebo-glossary`` writes the placebo entry lists: the
   merged glossary's per-paper lists permuted across the base papers that have at least one
   entry, by the derangement ``sha256(261101|alias|placebo|<paper>)`` ascending, each paper
   receiving the list of the paper after it and the last the first's (:func:`placebo_mapping`;
   no paper keeps its own list). From each received list every entry equal to one of the
   receiving paper's own merged entries is dropped (owner, 2026-11-03), matched on the merged
   glossary's own key, the alias and the primary's ``alias_abbrev.words`` (``alias_abbrev.merge``);
   a paper left with no entry gets no placebo field. The drops are counted per paper (by rank in
   the placebo order) and in total, in the manifest and, through ``arms.placebo.manifest``, in
   the placebo leg's summary (``study.placebo_glossary``). The rows keep the merged file's schema (``paper`` rewritten to
   the receiving paper, the source paper's location fields dropped, ``placebo: true`` added) so
   the one glossary-file writer renders both by the same mention rule and caps; the judge
   refuses a glossary file with placebo rows (its definitions block reads the merged glossary in
   every cell, §5.3). The config's ``arms.placebo`` must carry stamps of its own, so the census
   tells it from merged.
7. *Field coverage (§5.3, §3.3).* ``study.field_coverage``: the share of the collection's
   chunks with a rendered field and the mean rendered characters, from the probe candidates
   (``--chunk-export`` + ``--glossary-file`` / ``--rendered-fields``, the backfill's offline
   rendering of every chunk), with the backfill's own ``chunks_with_glossary`` beside it; the
   placebo is read beside merged with it.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE / "glossbench"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import alias_study_common as asc  # noqa: E402
import found10 as f10  # noqa: E402

#: every attachment's collection is a bench copy: its id starts with this (never a served collection)
BENCH_PREFIX = "bench-"
#: a stamp whose version carries this mark was rendered under the mention rule (§5.3, R27)
MENTION_MARK = "+attach=mention"
STUDY_SCHEMA = "alias-study-run/1"
PLACEBO_SCHEMA = "alias-placebo-glossary/1"
#: §5.2 / round 3: every retriever asks 50 (the depth-50 pool); default reranks a pool of 50
REQUIRED_TOP_K = 50

#: §5.3 (R7): the placebo glossary arm, run on base x default only
PLACEBO = asc.PLACEBO
STUDY_GLOSSARY_ARMS = (*asc.GLOSSARY_ARMS, PLACEBO)
PLACEBO_LEG = asc.PLACEBO_LEG
#: §5.5: the derangement of entry lists across papers (``…|<paper>``)
SEED_PLACEBO = f"{asc.SEED_BASE}|placebo"
#: §5.4: the request order of each noise-repeat leg
NOISE_ORDER_RULE = "sha256('261101|alias|noise|<corpus arm>|<record id>|<condition>') ascending"
#: fields of a merged-glossary entry that locate it in its own paper (dropped from placebo rows)
PLACEBO_DROP_KEYS = frozenset({"doc_id", "def_chunk_id", "def_span", "chunk_id", "chunk_ids"})

#: §5.7 (R9; owner, 2026-11-03): the attachments the Layer-1 members read (H1: base x none /
#: merged; H2: the expanded arms x none)
LAYER1_ATTACHMENTS = (("base", "none"), ("base", "merged"), ("neighbour", "none"), ("random1", "none"),
                      ("random2", "none"))
STAGES = ("A", "B")
_REPEATS = [asc.leg_id(c, asc.REPEAT_GLOSSARY, asc.REPEAT_RETRIEVER, 2) for c in asc.CORPUS_ARMS]
#: retrieval stage A (19): the five attachments at every retriever, and the noise repeats
RETRIEVAL_A_LEGS = frozenset([asc.leg_id(c, g, r) for c, g in LAYER1_ATTACHMENTS for r in asc.RETRIEVERS]
                             + _REPEATS)
#: judging stage A (14): the default and norerank legs of the five attachments and the repeats;
#: the bm25 legs are retrieved in stage A and judged in stage B (owner, 2026-11-03)
JUDGING_A_LEGS = frozenset([asc.leg_id(c, g, r) for c, g in LAYER1_ATTACHMENTS for r in ("default", "norerank")]
                           + _REPEATS)
#: the name the first version used for the retrieval stage-A set
STAGE_A_LEGS = RETRIEVAL_A_LEGS
#: outcome levels found10 writes per condition into a leg summary; left out in the pilot (R28)
PILOT_WITHHELD_CONDITION_KEYS = ("found10", "found10_gold", "rr10", "mrr10")
COMMIT_MARKER = "STAGE-A-COMMITTED"
MODE_MARKER = "STUDY-MODE"
MODES = ("main", "pilot", "dry_run", "rechunk")
#: the exploratory rechunk stage 1 (``rechunk: {label_table: PATH}``; module docstring, *Modes*)
RECHUNK = "rechunk"
#: the rechunk mode's leg-summary note in place of found10's gold-id outcome levels
RECHUNK_OUTCOMES_NOTE = ("not scored here: the records' gold ids are stage-A chunk ids, absent under the new "
                         "chunking; rechunk outcomes come from the label table (rechunk_stage1.py)")


def canonical_sha(obj: Any) -> str:
    return asc.sha_text(json.dumps(obj, sort_keys=True, separators=(",", ":")))


# ------------------------------------------------------------------ legs and stages

def study_legs() -> list[tuple[str, str, str, int]]:
    """The 29 legs of §5.4: :func:`alias_study_common.all_legs` (24 cells, the 4 noise repeats
    and the placebo leg)."""
    return asc.all_legs()


def leg_stage(corpus: str, glossary: str, retriever: str, repeat: int = 1) -> str:
    """The leg's **retrieval** stage."""
    return "A" if asc.leg_id(corpus, glossary, retriever, repeat) in RETRIEVAL_A_LEGS else "B"


def judging_stage(corpus: str, glossary: str, retriever: str, repeat: int = 1) -> str:
    """The stage in which the pairs a leg asks for found@10 are judged."""
    return "A" if asc.leg_id(corpus, glossary, retriever, repeat) in JUDGING_A_LEGS else "B"


def leg_stages(corpus: str, glossary: str, retriever: str, repeat: int = 1) -> dict[str, str]:
    return {"retrieval": leg_stage(corpus, glossary, retriever, repeat),
            "judging": judging_stage(corpus, glossary, retriever, repeat)}


def stage_legs(stage: str) -> list[tuple[str, str, str, int]]:
    """The legs of one retrieval stage."""
    return [lg for lg in study_legs() if leg_stage(*lg) == stage]


def judging_legs(stage: str) -> list[tuple[str, str, str, int]]:
    """The legs whose found@10 pairs are judged in one stage."""
    return [lg for lg in study_legs() if judging_stage(*lg) == stage]


def attachment_legs(corpus: str, glossary: str, repeat: int = 1) -> list[tuple[str, str, str, int]]:
    """The legs one run of (corpus arm, glossary arm[, repeat]) can measure."""
    return [lg for lg in study_legs() if lg[0] == corpus and lg[1] == glossary and lg[3] == repeat]


def run_label(corpus: str, glossary: str, repeat: int, stage: str | None = None) -> str:
    """The file label of a run: found10's (``none``, ``none#2``). Every attachment is
    retrieved in one stage, so the stage does not enter the label."""
    return f10.arm_label(glossary, repeat if repeat == 2 else None)


def leg_files(runs: Path, corpus: str, glossary: str, retriever: str, repeat: int = 1) -> tuple[Path, Path]:
    """``(rows, summary)`` of one leg: its attachment's files (one run per attachment)."""
    sp = runs / corpus / f"{run_label(corpus, glossary, repeat)}.summary.json"
    return sp.with_name(sp.name.removesuffix(".summary.json") + ".jsonl"), sp


#: a leg whose summary lacks the runner's ``study`` block (or its mode / stage): the run was
#: killed between found10 writing its summary and this runner completing it
INCOMPLETE_NO_STUDY = "incomplete"


def run_status(out: Path) -> dict[str, Any]:
    """The 29 legs and each one's state from the summaries (``missing`` when never run).

    A summary without the runner's ``study`` block, or whose block lacks ``mode`` or ``stage``,
    is **incomplete** whatever its ``status`` says: found10 writes the summary first and
    :meth:`AliasRunner._finish` then adds the block, so a kill between the two leaves
    ``status: complete`` on a summary the study never completed. Read as incomplete, the leg is
    re-run on resume (which rewrites the summary from the rows already done) and
    ``commit-stage-a`` cannot pin it."""
    st: dict[str, Any] = {}
    for c, g, r, rep in study_legs():
        _, sp = leg_files(out, c, g, r, rep)
        lid = asc.leg_id(c, g, r, rep)
        if not sp.is_file():
            st[lid] = "missing"
            continue
        s = json.loads(sp.read_text(encoding="utf-8"))
        if asc.RETRIEVERS[r] not in (s.get("conditions") or {}):
            st[lid] = "missing"
            continue
        study = s.get("study")
        if not isinstance(study, dict) or not study.get("mode") or not study.get("stage"):
            st[lid] = INCOMPLETE_NO_STUDY
            continue
        st[lid] = s["status"]
    return st


def read_commit_marker(out: Path) -> dict[str, Any] | None:
    p = out / COMMIT_MARKER
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def config_mode(cfg: Mapping[str, Any]) -> str:
    if cfg.get("dry_run"):
        return "dry_run"
    if cfg.get("pilot"):
        return "pilot"
    return RECHUNK if cfg.get(RECHUNK) else "main"


def ensure_mode(d: Path, mode: str) -> None:
    """The out dir's ``STUDY-MODE`` marker: written on first use, refused when it names another
    mode (a pilot dir is never the main run's, §5.6)."""
    if mode not in MODES:
        raise f10.Refused(f"mode {mode!r}")
    d.mkdir(parents=True, exist_ok=True)
    m = d / MODE_MARKER
    if m.is_file():
        have = m.read_text(encoding="utf-8").strip()
        if have != mode:
            raise f10.Refused(f"{d.name}/{MODE_MARKER} says {have!r}, this config is {mode!r}: a pilot directory is "
                              "never reused by the main run, nor the reverse (§5.6)")
    else:
        m.write_text(mode + "\n", encoding="utf-8")


def check_prereg(cfg: Mapping[str, Any]) -> dict[str, Any] | None:
    """§9.2: a frozen-prereg sha mismatch in any stage that reads ``prereg.file`` is a hard
    stop. Required in the main run (the prereg is frozen before it starts)."""
    pr = cfg.get("prereg")
    if not pr:
        if config_mode(cfg) == "main":
            raise f10.Refused("the main run needs prereg: {file, sha256} (the frozen prereg, §9.2, §15)")
        return None
    if not pr.get("file") or not pr.get("sha256"):
        raise f10.Refused("prereg needs both file and sha256")
    have = asc.sha256_file(pr["file"])
    if have != pr["sha256"]:
        raise f10.Refused(f"HARD STOP: the prereg file's sha256 {have[:12]}… != the frozen {str(pr['sha256'])[:12]}… "
                          "(§9.2)")
    return {"sha256": have, "verified": True}


# ------------------------------------------------------------------ config and records

def load_config(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return validate_config(raw)


def validate_config(raw: Mapping[str, Any]) -> dict[str, Any]:
    """found10's normalisation plus the study's shape. Raises :class:`found10.Refused`."""
    cfg = f10.normalise_config(dict(raw))
    if set(cfg["corpora"]) != set(asc.CORPUS_ARMS):
        raise f10.Refused(f"corpora must be exactly {list(asc.CORPUS_ARMS)}, got {sorted(cfg['corpora'])}")
    rechunk = bool(cfg.get(RECHUNK))
    if rechunk:
        check_rechunk_config(cfg)
    arms = set(cfg["arms"])
    if rechunk and not {asc.REPEAT_GLOSSARY} <= arms <= set(STUDY_GLOSSARY_ARMS):
        raise f10.Refused(f"rechunk: arms must hold {asc.REPEAT_GLOSSARY!r} (the only glossary arm it runs), got "
                          f"{sorted(arms)}")
    if not rechunk and not set(asc.GLOSSARY_ARMS) <= arms <= set(STUDY_GLOSSARY_ARMS):
        raise f10.Refused(f"arms must be {list(asc.GLOSSARY_ARMS)} (and {PLACEBO!r} for the placebo leg), "
                          f"got {sorted(arms)}")
    names = sorted(arms)
    for i, a in enumerate(names):
        sa_ = set(cfg["arms"][a].get("stamps") or []) | set(cfg["arms"][a].get("stamp_prefixes") or [])
        for b in names[i + 1:]:
            sb_ = set(cfg["arms"][b].get("stamps") or []) | set(cfg["arms"][b].get("stamp_prefixes") or [])
            if sa_ & sb_:
                raise f10.Refused(f"arms.{a} shares a stamp with arms.{b}: every glossary arm has its own version "
                                  "stamp, so a census tells the collections apart (§5.3)")
    for a in names:
        acfg = cfg["arms"][a]
        marks = [x for x in (acfg.get("stamps") or []) + (acfg.get("stamp_prefixes") or []) if MENTION_MARK in str(x)]
        if marks and acfg.get("attach") != "mention":
            raise f10.Refused(f"arms.{a}: its stamps carry {MENTION_MARK!r}, so the arm must declare attach: \"mention\" "
                              "(the census then checks the attach slot, §5.3, R27)")
    for c, spec in cfg["conditions"].items():
        if int(spec["top_k"]) != REQUIRED_TOP_K:
            raise f10.Refused(f"condition {c}: top_k {spec['top_k']} != {REQUIRED_TOP_K} (the depth-50 pool)")
    if int(cfg["conditions"]["base"].get("rerank_candidates") or 0) != 50:
        raise f10.Refused("condition base (the default retriever): rerank_candidates must be 50 (§5.2)")
    if cfg["conditions"]["base"].get("rerank") is not True or cfg["conditions"]["norerank"].get("rerank") is not False:
        raise f10.Refused("default must rerank and norerank must not (§5.2)")
    sa = cfg["server_assumed"]
    if sa.get("retrieval_candidate_multiplier") != 2 or sa.get("rrf_k") != 60:
        raise f10.Refused("server_assumed must pin retrieval_candidate_multiplier 2 and rrf_k 60 (§5.2)")
    for k in ("records", "out_dir"):
        if not cfg.get(k):
            raise f10.Refused(f"config has no {k!r}")
    if cfg.get("dry_run") and cfg.get("pilot"):
        raise f10.Refused("dry_run and pilot exclude each other")
    for c in asc.CORPUS_ARMS:
        cc = cfg["corpora"][c]
        if "collection" in cc or "restore_point" in cc:
            raise f10.Refused(f"corpora.{c}: name each attachment's own collection in collections.{c}.<glossary> "
                              "(collections are built once and never written; no swap, no restore point)")
        if not isinstance(cc.get("expected_chunks"), int):
            raise f10.Refused(f"corpora.{c}.expected_chunks is not set")
        if not cfg.get("dry_run") and not cc.get("chunk_export"):
            raise f10.Refused(f"corpora.{c}.chunk_export is not set")
    cols = cfg.get("collections") or {}
    want = required_attachments(cfg)
    have = {(c, g) for c, gs in cols.items() for g in (gs or {})}
    if have != set(want):
        raise f10.Refused(f"collections must name exactly the attachments {sorted(want)}; got {sorted(have)}")
    seen: dict[str, tuple[str, str]] = {}
    for c, g in want:
        col = cols[c][g]
        for k in ("collection", "index", "load_receipt"):
            if not col.get(k):
                raise f10.Refused(f"collections.{c}.{g}.{k} is not set")
        if not str(col["collection"]).startswith(BENCH_PREFIX):
            raise f10.Refused(f"collections.{c}.{g}.collection must be a bench collection (its id starts with "
                              f"{BENCH_PREFIX!r})")
        for k in ("collection", "index"):
            key = f"{k}:{col[k]}"
            if key in seen:
                raise f10.Refused(f"collections.{c}.{g} shares its {k} with {seen[key]}: one collection per attachment")
            seen[key] = (c, g)
    if not cfg.get("dry_run") and not rechunk and not cfg.get("gold_hashes"):
        raise f10.Refused("config has no 'gold_hashes' (the base export's gold-chunk content hashes)")
    if config_mode(cfg) != "dry_run" and not (cfg.get("census") or {}).get("es_url"):
        raise f10.Refused("census.es_url is required: every leg reads its collection's census before and after (§9.2)")
    if config_mode(cfg) == "main" and not cfg.get("prereg"):
        raise f10.Refused("the main run needs prereg: {file, sha256} (the frozen prereg, §9.2, §15)")
    return cfg


def check_rechunk_config(cfg: Mapping[str, Any]) -> None:
    """The rechunk mode's own shape: ``rechunk: {label_table: PATH}``, never with ``dry_run`` or
    ``pilot``. Raises :class:`found10.Refused`."""
    rc = cfg.get(RECHUNK)
    if cfg.get("dry_run") or cfg.get("pilot"):
        raise f10.Refused("rechunk excludes dry_run and pilot")
    if not isinstance(rc, Mapping) or not rc.get("label_table"):
        raise f10.Refused("rechunk needs {label_table: PATH} (rechunk_prep.py gold-map)")


def rechunk_table(cfg: Mapping[str, Any]) -> Any:
    """The rechunk mode's label table (``rechunk_prep.LabelTable``); SystemExit -> Refused."""
    import rechunk_prep as rp

    try:
        return rp.read_label_table(cfg[RECHUNK]["label_table"])
    except SystemExit as e:
        raise f10.Refused(f"rechunk.label_table: {e}") from e


def required_attachments(cfg: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Every (corpus arm, glossary arm) with its own collection: the 8 of the 24 cells, and
    base x placebo when the placebo arm is configured. The rechunk mode: the four none
    collections only."""
    if config_mode(cfg) == RECHUNK:
        return [(c, asc.REPEAT_GLOSSARY) for c in asc.CORPUS_ARMS]
    out = [(c, g) for c in asc.CORPUS_ARMS for g in asc.GLOSSARY_ARMS]
    return out + [("base", PLACEBO)] if PLACEBO in cfg["arms"] else out


def attachment_cfg(cfg: Mapping[str, Any], corpus: str, glossary: str) -> dict[str, Any]:
    """The config found10 runs one attachment with: ``corpora.<corpus>`` carrying the
    attachment's own collection and index."""
    import copy

    out = copy.deepcopy(dict(cfg))
    col = cfg["collections"][corpus][glossary]
    out["corpora"][corpus] = {**cfg["corpora"][corpus],
                              **{k: v for k, v in col.items() if k != "load_receipt"}}
    return out


def check_load_receipt(cfg: Mapping[str, Any], corpus: str, glossary: str) -> dict[str, Any]:
    """The attachment's build manifest is its load receipt (``load_embeddings.py`` summary of
    ``load-embeddings.cwl``): no failed shard, the corpus arm's chunk count, and the glossary
    file the arm declares, for every arm (the receipt's ``glossary_file.sha256`` =
    ``arms.<glossary>.glossary_file_sha256.<corpus>``; ``glossary_file`` is
    ``ragstack.ingestion.glossary_file.file_provenance``, a flat record, as ``load_embeddings.py``
    writes it); the none arm's file must render no
    field (its ``counts.merged_entries`` 0, and no non-zero ``*with_glossary*`` count).
    Raises :class:`found10.Refused`; returns what goes into the summary."""
    col = cfg["collections"][corpus][glossary]
    rp = Path(col["load_receipt"])
    if not rp.is_file():
        raise f10.Refused(f"collections.{corpus}.{glossary}.load_receipt: no such file")
    rec = json.loads(rp.read_text(encoding="utf-8"))
    problems = []
    if int(rec.get("n_shards_failed", -1)) != 0 or rec.get("failed_shards"):
        problems.append("the load has failed shards")
    exp = cfg["corpora"][corpus]["expected_chunks"]
    if rec.get("n_chunks") != exp:
        problems.append(f"loaded {rec.get('n_chunks')} chunks, the corpus arm has {exp}")
    gf = rec.get("glossary_file") or None
    want = ((cfg["arms"][glossary].get("glossary_file_sha256")) or {}).get(corpus)
    # every arm, none included, is loaded from an explicit glossary file with its own stamp (the
    # none file holds empty rows: it renders no field, §5.3, §9.2)
    if not gf:
        problems.append(f"the {glossary} collection was loaded without a glossary file")
    elif not want or gf.get("sha256") != want:
        problems.append(f"the {glossary} collection was loaded from another glossary file than "
                        f"arms.{glossary}.glossary_file_sha256.{corpus}")
    elif glossary == asc.REPEAT_GLOSSARY:
        counts = dict(gf.get("counts") or {})
        rendering = {k: v for k, v in counts.items() if (k == "merged_entries" or "with_glossary" in k) and v}
        if "merged_entries" not in counts or rendering:
            problems.append(f"the none collection's glossary file renders a field (counts {counts}); it must hold "
                            "empty rows only (merged_entries 0)")
    if problems:
        raise f10.Refused(f"collection {col['collection']}: " + "; ".join(problems))
    return {"collection": col["collection"], "index": col["index"], "load_receipt_sha256": asc.sha256_file(rp),
            "n_chunks": rec["n_chunks"], "n_shards": rec.get("n_shards"),
            "glossary_file_sha256": None if not gf else gf.get("sha256"),
            "glossary_versions": [] if not gf else list(gf.get("glossary_versions") or []),
            "provenance": rec.get("provenance"), "embed_provenance": rec.get("embed_provenance"),
            "chunk_cap": rec.get("chunk_cap")}


def load_study_records(cfg: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The accepted records in found10's record schema (``corpus`` set per run), with the
    record-set identity check (§4.1) unless ``dry_run``. The pilot (§5.6) keeps only the
    records on unsatisfied papers."""
    path = Path(cfg["records"])
    recs = asc.load_records(path)
    if cfg.get("dry_run"):
        return recs, {"dry_run": True, "records": len(recs), "sha256": asc.sha256_file(path)}
    ident = asc.verify_record_set(path, recs)
    if cfg.get("pilot"):
        _, desc = asc.split_records(recs)
        keep = {r["record_id"] for r in desc}
        recs = [r for r in recs if r["record_id"] in keep]
        ident = {**ident, "pilot": True, "pilot_records": len(recs)}
    elif config_mode(cfg) == RECHUNK:
        # the full accepted file is verified above, unchanged; then only the records of the
        # label table's pairs (each is run once per corpus arm of its pairs, build_runner)
        keep = rechunk_table(cfg).records()
        have = {r["record_id"] for r in recs}
        if keep - have:
            raise f10.Refused(f"rechunk: {len(keep - have)} label-table record(s) are not in the accepted file")
        recs = [r for r in recs if r["record_id"] in keep]
        ident = {**ident, RECHUNK: True, "rechunk_records": len(recs)}
    return recs, ident


def records_for(recs: Sequence[Mapping[str, Any]], corpus_arm: str) -> list[dict[str, Any]]:
    """found10 filters records by ``corpus``: every record runs in every corpus arm."""
    out = []
    for r in recs:
        out.append({**r, "corpus": corpus_arm, "set": corpus_arm, "stratum": r["kind"]})
    return out


def export_paths(spec: str | Sequence[str]) -> list[Path]:
    """``corpora.<arm>.chunk_export``: one export, or a list (the base export plus the arm's
    added papers) read together."""
    return [Path(spec)] if isinstance(spec, str) else [Path(x) for x in spec]


def check_gold_presence(recs: Sequence[Mapping[str, Any]], exports: Sequence[Path], gold_hashes: Mapping[str, str]
                        ) -> dict[str, Any]:
    """§9.2: every gold chunk in this arm's export(s), with the base export's content hash."""
    want = {r["gold_chunks"][0] for r in recs}
    missing_hash = sorted(c for c in want if c not in gold_hashes)
    if missing_hash:
        raise f10.Refused(f"{len(missing_hash)} gold chunk(s) have no recorded base hash (run gold-hashes)")
    found: dict[str, str] = {}
    chunks = asc.read_chunk_exports(exports)
    for c in want:
        if c in chunks:
            found[c] = asc.sha_text(chunks[c]["content"])
    absent = [c for c in want if c not in found]
    differ = [c for c in want if c in found and found[c] != gold_hashes[c]]
    if absent or differ:
        raise f10.Refused(f"gold presence: {len(absent)} gold chunk(s) absent from the arm's export, "
                          f"{len(differ)} with other content than the base export")
    return {"gold_chunks": len(want), "present": len(found),
            "export_sha256": [asc.sha256_file(x) for x in exports], "chunks_in_export": len(chunks)}


def noise_order(corpus_arm: str) -> Any:
    seed = asc.SEEDS["noise"]

    def key(rec: Mapping[str, Any], cond: str) -> str:
        return asc.seeded_key(seed, corpus_arm, str(rec["record_id"]), cond)
    return key


# ------------------------------------------------------------------ the runner

def field_coverage(probe_chunks: Sequence[tuple[str, str, str]] | None, unavailable: str | None,
                   source: str | None, receipt: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """§5.3 / §3.3: the attached field's coverage on this collection: the share of chunks with
    a non-empty ``symbol_glossary`` and the mean rendered characters (over all chunks, and over
    the chunks with a field). Read from the probe candidates (``--chunk-export`` with
    ``--glossary-file`` or ``--rendered-fields``: every chunk of the index with its field,
    rendered offline as the loader renders it); the glossary file's own counts from the load
    receipt are printed beside it when present (``glossary_file_counts``)."""
    gf = (receipt or {}).get("glossary_file") or {}
    backfill = dict(gf.get("counts") or {})
    if not probe_chunks:
        return {"available": False, "reason": unavailable or "no probe candidates", "glossary_file_counts": backfill}
    n = len(probe_chunks)
    lens = [len(f or "") for _, _, f in probe_chunks]
    with_f = [x for x in lens if x > 0]
    return {"available": True, "source": source, "chunks": n, "chunks_with_field": len(with_f),
            "share_with_field": round(len(with_f) / n, 6) if n else None,
            "mean_chars": round(sum(lens) / n, 3) if n else None,
            "mean_chars_with_field": round(sum(with_f) / len(with_f), 3) if with_f else None,
            "glossary_file_counts": backfill}


class AliasRunner(f10.Runner):
    """found10's runner with the study's additions (module docstring, items 1 and 4) and the
    stage's file label."""

    hard_stop: str | None

    def __init__(self, *a: Any, study: Mapping[str, Any] | None = None, label: str | None = None,
                 **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.study = dict(study or {})
        self._config_start: str | None = None
        if label is not None and label != self.label:
            self.label = label
            self.rows_path = self.out_dir / f"{label}.jsonl"
            self.summary_path = self.out_dir / f"{label}.summary.json"

    def _attachment_census(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """The collection's load receipt (checked in :func:`build_runner`) in place of round
        3's swap record: the census to expect is the arm's chunk total, every stamp the arm's."""
        build = self.study["collection_build"]
        return ({"collection_build": build},
                {"total": int(build["n_chunks"]), "stamps": None, "shuffle_suffix": None})

    def _census_ok(self, live: Mapping[str, Any], census: Mapping[str, Any]) -> bool:
        """The live census of the collection (``metadata.symbol_glossary_version`` terms): the
        chunk total of the load, and every chunk's stamp the glossary arm's (another arm's
        stamp, or an unstamped chunk, refuses), its version one of the loaded glossary file's."""
        self.census_source = "the load receipt's"
        if int(live.get("total", -1)) != int(census["total"]):
            return False
        stamps = dict(live.get("stamps") or {})
        if sum(stamps.values()) != int(census["total"]):
            return False
        versions = set(self.study["collection_build"].get("glossary_versions") or [])
        for st in stamps:
            if not f10.stamp_allowed(st, self.acfg):
                return False
            if versions and st.split(f10.FILE_STAMP)[0] not in versions:
                return False
        return True

    async def _server_info(self, client: httpx.AsyncClient) -> dict[str, Any]:
        info = await super()._server_info(client)
        if not info.get("config_readable"):
            raise f10.Refused("GET /v1/config is not readable: the admin config-hash check (§9.2) needs an "
                              "admin credential; nothing measured")
        self._config_start = canonical_sha(info["/v1/config"])
        info["config_sha256_start"] = self._config_start
        return info

    async def _after_jobs(self, client: httpx.AsyncClient, server: dict[str, Any]) -> None:
        base = self.cfg["api"]["base_url"].rstrip("/")
        end: str | None = None
        try:
            r = await client.get(base + "/v1/config")
            if r.status_code == 200:
                end = canonical_sha(r.json())
        except (httpx.HTTPError, ValueError):
            end = None
        server["config_sha256_end"] = end
        server["config_hash_identical"] = end is not None and end == self._config_start
        if not server["config_hash_identical"]:
            self.hard_stop = self.hard_stop or (
                "admin /v1/config differs between the start and the end of the leg (or the end read failed): "
                "the leg is re-run after the cause is found and recorded (§9.2)")

    def _finish(self, done: dict[str, dict[str, Any]], swap: Mapping[str, Any], census: Mapping[str, Any],
                live_before: Any, live_after: Any, server: Mapping[str, Any]) -> int:
        per_leg = {}
        expected = len(self.records)
        for cond in self.conditions:
            rows = [r for r in done.values() if r["condition"] == cond]
            failed = sum(bool(r.get("failed")) for r in rows)
            per_leg[cond] = {"rows": len(rows), "failed": failed}
            if len(rows) == expected and rows and failed / len(rows) > f10.MAX_FAILED_SHARE and not self.hard_stop:
                self.hard_stop = (f"{failed}/{len(rows)} calls failed in the {asc.CONDITION_TO_RETRIEVER[cond]} leg "
                                  f"(> {f10.MAX_FAILED_SHARE:.0%}, §7)")
        rc = super()._finish(done, swap, census, live_before, live_after, server)
        summary = json.loads(self.summary_path.read_text(encoding="utf-8"))
        study = {"schema": STUDY_SCHEMA, **self.study, "per_leg_failed": per_leg,
                 "retrievers": [asc.CONDITION_TO_RETRIEVER[c] for c in self.conditions],
                 "config_sha256_start": server.get("config_sha256_start"),
                 "config_sha256_end": server.get("config_sha256_end"),
                 "config_hash_identical": server.get("config_hash_identical")}
        summary["study"] = study
        cen = summary.get("census") or {}
        cen.pop("swap_utc", None)
        cen.update({"swap_record_sha256": None, "collection_build": self.study.get("collection_build"),
                    "source": "load_receipt+live" if live_before is not None else "load_receipt"})
        summary["census"] = cen
        if self.study.get("mode") == RECHUNK:
            # exploratory: found10's found@10 is gold-id based, and the gold ids are stage-A chunk
            # ids that the new chunking does not have; the levels are left out, not reported as 0
            summary["exploratory"] = True
            summary["mode"] = RECHUNK
            for c in (summary.get("conditions") or {}).values():
                for k in PILOT_WITHHELD_CONDITION_KEYS:
                    c.pop(k, None)
                c["outcomes"] = RECHUNK_OUTCOMES_NOTE
        if self.study.get("mode") == "pilot":
            # R28 (owner, 2026-11-03): the pilot stays blind to outcomes; its leg summaries carry
            # no found@10 level (the rows keep every rank, privately; the pilot report reads rows
            # and labels only)
            for c in (summary.get("conditions") or {}).values():
                for k in PILOT_WITHHELD_CONDITION_KEYS:
                    c.pop(k, None)
                c["outcomes"] = "withheld in the pilot (owner, 2026-11-03)"
        f10.write_json_atomic(self.summary_path, summary)
        return rc


def placebo_manifest_record(cfg: Mapping[str, Any]) -> dict[str, Any] | None:
    """``arms.placebo.manifest``: the manifest of the placebo glossary file the base x placebo
    collection was loaded from (this module's ``placebo-glossary`` manifest, or the glossary-file
    writer's). Its ``glossary_file_sha256`` (or ``placebo_sha256``) must be the arm's declared
    base glossary file, and its ``counts`` must carry ``placebo_dropped_own_entries``; the counts
    go into the placebo leg's summary. Required outside a dry run."""
    acfg = cfg["arms"].get(PLACEBO) or {}
    mp = acfg.get("manifest")
    if not mp:
        if cfg.get("dry_run"):
            return None
        raise f10.Refused("arms.placebo.manifest is not set (the placebo glossary's manifest, §5.3)")
    man = json.loads(Path(mp).read_text(encoding="utf-8"))
    want = (acfg.get("glossary_file_sha256") or {}).get("base")
    named = man.get("glossary_file_sha256") or man.get("placebo_sha256")
    counts = man.get("counts") or {}
    if named != want or "placebo_dropped_own_entries" not in counts:
        raise f10.Refused("arms.placebo.manifest is not the manifest of the arm's declared glossary file (or carries "
                          "no placebo_dropped_own_entries)")
    return {"manifest_sha256": asc.sha256_file(mp), "glossary_file_sha256": named,
            "schema": man.get("schema"), "source_sha256": man.get("source_sha256"), "seed": man.get("seed"),
            "counts": counts}


def build_runner(cfg: dict[str, Any], recs: Sequence[Mapping[str, Any]], ident: Mapping[str, Any],
                 corpus_arm: str, glossary: str, repeat: int | None = None,
                 stage: str | None = None, **kw: Any) -> AliasRunner:
    if corpus_arm not in asc.CORPUS_ARMS:
        raise f10.Refused(f"--corpus-arm {corpus_arm!r}: one of {list(asc.CORPUS_ARMS)}")
    if glossary not in STUDY_GLOSSARY_ARMS:
        raise f10.Refused(f"--glossary {glossary!r}: one of {list(STUDY_GLOSSARY_ARMS)}")
    if glossary not in cfg["arms"]:
        raise f10.Refused(f"--glossary {glossary!r} is not configured (arms.{glossary})")
    rep = int(repeat or 1)
    if rep not in (1, 2):
        raise f10.Refused("--repeat: 1 or 2 (one noise repeat per corpus arm, §9.4)")
    if rep == 2 and glossary != asc.REPEAT_GLOSSARY:
        raise f10.Refused("the noise repeat is default x none (§5.4)")
    if glossary == PLACEBO and corpus_arm != "base":
        raise f10.Refused("the placebo leg is base x default only (§5.3)")
    if stage not in STAGES:
        raise f10.Refused(f"--stage {stage!r}: retrieval stage A (the 5 Layer-1 attachments and the repeats) or B "
                          "(the rest and the placebo), §5.7")
    legs = [lg for lg in attachment_legs(corpus_arm, glossary, rep) if leg_stage(*lg) == stage]
    if not legs:
        raise f10.Refused(f"{corpus_arm} x {glossary}{' (repeat)' if rep == 2 else ''} has no stage-{stage} leg (§5.7)")
    out = Path(cfg["out_dir"])
    mode = config_mode(cfg)
    ensure_mode(out, mode)
    prereg = check_prereg(cfg)
    marker = read_commit_marker(out)
    commit: dict[str, Any] | None = None
    if stage == "A" and marker is not None:
        raise f10.Refused("stage A is committed: a stage-A leg is never re-run after the commit (§5.7)")
    if stage == "B":
        if marker is None:
            raise f10.Refused("stage B starts only after stage A is committed (§5.7): run the judge's "
                              "commit-stage-a first")
        import alias_study_judge as aj

        try:
            commit = aj.verify_marker(out)
        except SystemExit as e:
            raise f10.Refused(f"stage-A commit does not verify: {e}") from e
    ccfg = cfg["corpora"][corpus_arm]
    extra: dict[str, Any] = {}
    if mode == RECHUNK:
        if glossary != asc.REPEAT_GLOSSARY:
            raise f10.Refused(f"rechunk runs the {asc.REPEAT_GLOSSARY} collections only")
        import rechunk_prep as rp

        table = rechunk_table(cfg)
        extra["rechunk_presence"] = rp.check_rechunk_presence(table, export_paths(ccfg["chunk_export"]), corpus_arm)
        extra["exploratory"] = True
        arm_records = table.records(corpus_arm)
        recs = [r for r in recs if r["record_id"] in arm_records]
        if not recs:
            raise f10.Refused(f"rechunk: no label-table pair is run in {corpus_arm}")
    elif not cfg.get("dry_run") or ccfg.get("chunk_export"):
        hashes = json.loads(Path(cfg["gold_hashes"]).read_text(encoding="utf-8")) if cfg.get("gold_hashes") else {}
        extra["gold_presence"] = check_gold_presence(recs, export_paths(ccfg["chunk_export"]), hashes)
    build = check_load_receipt(cfg, corpus_arm, glossary)
    extra["collection_build"] = build
    label = run_label(corpus_arm, glossary, rep, stage)
    if glossary == PLACEBO:
        extra["placebo_glossary"] = placebo_manifest_record(cfg)
    receipt = json.loads(Path(cfg["collections"][corpus_arm][glossary]["load_receipt"]).read_text(encoding="utf-8"))
    extra["field_coverage"] = field_coverage(kw.get("probe_chunks"), kw.get("probe_unavailable"),
                                             kw.get("probe_source"), receipt)
    study = {"corpus_arm": corpus_arm, "glossary_arm": glossary, "repeat": rep, "record_set": dict(ident),
             "seeds": {"noise": asc.SEEDS["noise"]} if rep == 2 else {}, "seed_rule": asc.SEED_RULE,
             "dry_run": bool(cfg.get("dry_run")), "pilot": bool(cfg.get("pilot")), "mode": mode, "stage": stage,
             "legs": [asc.leg_id(*lg) for lg in legs],
             "leg_stages": {asc.leg_id(*lg): leg_stages(*lg) for lg in legs}, "run_label": label, "placebo": glossary == PLACEBO,
             "request_order": NOISE_ORDER_RULE if rep == 2 else "record file order",
             "prereg": prereg, "stage_a_commit": None if commit is None else
             {"sha256": commit["commit_sha256"], "verified": True}, **extra}
    conds = [asc.RETRIEVERS[lg[2]] for lg in legs]
    order = noise_order(corpus_arm) if rep == 2 else None
    return AliasRunner(attachment_cfg(cfg, corpus_arm, glossary), records_for(recs, corpus_arm), corpus_arm, glossary,
                       out, Path(cfg["collections"][corpus_arm][glossary]["load_receipt"]),
                       repeat=rep if rep == 2 else None, conditions=conds, job_order=order, study=study, label=label,
                       **kw)


# ------------------------------------------------------------------ the placebo glossary (§5.3, R7)

def placebo_order(papers: Sequence[str]) -> list[str]:
    """The papers in the order ``sha256(261101|alias|placebo|<paper>)`` ascending."""
    return sorted(set(papers), key=lambda p: (asc.seeded_key(SEED_PLACEBO, p), p))


def placebo_mapping(papers: Sequence[str]) -> dict[str, str]:
    """``receiving paper -> source paper``: each paper receives the entry list of the paper
    that follows it in :func:`placebo_order`, the last the first's (§5.3). A cyclic shift by
    one of two or more distinct papers has no fixed point; that is checked, not assumed."""
    order = placebo_order(papers)
    n = len(order)
    if n < 2:
        raise SystemExit(f"placebo: {n} paper(s) with entries; a derangement needs at least 2")
    mapping = {order[i]: order[(i + 1) % n] for i in range(n)}
    fixed = [r for r, s in mapping.items() if r == s]
    if fixed or sorted(mapping.values()) != sorted(mapping):
        raise SystemExit(f"placebo: not a derangement ({len(fixed)} fixed point(s))")
    return mapping


def build_placebo(entries: Sequence[Mapping[str, Any]], corpus_papers: set[str] | None = None
                  ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The placebo rows and their counts: a pure function (no I/O; SystemExit on a refusal),
    importable by the glossary-file writer. ``entries``: the merged glossary's rows (``paper``,
    ``alias``, ``primary``, ``kind`` …; rows without ``alias`` are skipped). ``corpus_papers``:
    the base export's papers; an entry on another paper is refused (§5.3: the OA-1000 papers
    with at least one merged entry). Returns the receiving papers' rows in the merged schema
    (``paper`` = receiver, location fields dropped, ``placebo: true``), in placebo order, and
    the counts (``placebo_dropped_own_entries`` among them)."""
    import alias_abbrev as aa

    by: dict[str, list[Mapping[str, Any]]] = {}
    for e in entries:
        if "alias" not in e:
            continue
        if e.get("placebo"):
            raise SystemExit("placebo: the input is already a placebo glossary")
        by.setdefault(str(e["paper"]), []).append(e)
    if corpus_papers is not None:
        foreign = sorted(set(by) - corpus_papers)
        if foreign:
            raise SystemExit(f"placebo: {len(foreign)} glossary paper(s) are not in the base export")
    mapping = placebo_mapping(list(by))

    def key(e: Mapping[str, Any]) -> tuple[str, tuple[str, ...]]:
        """The merged glossary's own entry key without the paper (``alias_abbrev.merge``: the
        alias as stored, the primary's ``alias_abbrev.words``)."""
        return str(e["alias"]), tuple(aa.words(str(e["primary"])))

    rows: list[dict[str, Any]] = []
    dropped_by_rank: dict[str, int] = {}
    emptied = 0
    received = 0
    for rank, recv in enumerate(placebo_order(list(by)), 1):
        src = mapping[recv]
        own = {key(e) for e in by[recv]}
        drop = 0
        kept = 0
        for e in by[src]:
            received += 1
            # owner, 2026-11-03: an entry equal to one of the receiving paper's own merged
            # entries is not placebo text; it is dropped and counted
            if key(e) in own:
                drop += 1
                continue
            row = {k: v for k, v in e.items() if k not in PLACEBO_DROP_KEYS}
            row.update({"paper": recv, "placebo": True})
            rows.append(row)
            kept += 1
        if drop:
            dropped_by_rank[str(rank)] = drop
        emptied += int(kept == 0)
    pairs = "".join(f"{r}\t{s}\n" for r, s in sorted(mapping.items()))
    counts = {"papers": len(mapping), "entries_received": received, "entries": len(rows),
              "papers_with_placebo_entries": len(mapping) - emptied,
              "fixed_points": sum(r == s for r, s in mapping.items()), "mapping_sha256": asc.sha_text(pairs),
              "kinds": dict(Counter(str(r.get("kind")) for r in rows)),
              # per paper by its rank in the placebo order (1-based), never by id (§11)
              "placebo_dropped_own_entries": {"entries": sum(dropped_by_rank.values()),
                                              "papers": len(dropped_by_rank), "papers_emptied": emptied,
                                              "per_paper_by_rank": dropped_by_rank}}
    return rows, counts


def cmd_placebo(merged: Path, base_export: Path, out: Path) -> int:
    repo = _HERE.parents[2].resolve()
    dst = out.resolve()
    if dst == repo or repo in dst.parents:
        raise SystemExit("--out is inside the repository; glossaries are private (§11)")
    entries = asc.read_jsonl(merged)
    papers = {c["paper"] for c in asc.read_chunk_exports([base_export]).values()}
    rows, counts = build_placebo(entries, papers)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(dst)
    try:
        from ragstack.provenance import experiment_provenance
        prov: dict[str, Any] = experiment_provenance()
    except Exception:  # noqa: BLE001
        prov = {}
    man = {"schema": PLACEBO_SCHEMA, "seed": SEED_PLACEBO, "rule": "receiver i gets the list of paper i+1 in "
           "sha256('<seed>|<paper>') ascending order; the last gets the first's", "counts": counts,
           "source_sha256": asc.sha256_file(merged), "base_export_sha256": asc.sha256_file(base_export),
           "placebo_sha256": asc.sha256_file(dst), "provenance": prov}
    f10.write_json_atomic(dst.with_name(dst.name + ".manifest.json"), man)
    print(json.dumps({"placebo_sha256": man["placebo_sha256"], **counts}, indent=1))
    return 0


# ------------------------------------------------------------------ commands

def cmd_plan(cfg: Mapping[str, Any]) -> int:
    out = Path(cfg["out_dir"])
    st = run_status(out)
    runs = []
    seen: set[tuple[str, str, int, str]] = set()
    for c, g, r, rep in study_legs():
        stage = leg_stage(c, g, r, rep)
        if (c, g, rep, stage) in seen:
            continue
        seen.add((c, g, rep, stage))
        legs = [lg for lg in attachment_legs(c, g, rep) if leg_stage(*lg) == stage]
        runs.append({"stage": stage, "run": f"--stage {stage} --corpus-arm {c} --glossary {g}"
                     + (" --repeat 2" if rep == 2 else ""),
                     "legs": {asc.leg_id(*lg): st[asc.leg_id(*lg)] for lg in legs}})
    runs.sort(key=lambda x: x["stage"])
    print(json.dumps({"legs": len(st), "complete": sum(v == "complete" for v in st.values()),
                      "stage_a_complete": all(st[k] == "complete" for k in RETRIEVAL_A_LEGS),
                      "stage_a_committed": read_commit_marker(out) is not None, "runs": runs}, indent=1))
    return 0


def cmd_gold_hashes(cfg: Mapping[str, Any], base_export: Path) -> int:
    recs, _ = load_study_records(cfg)
    chunks = asc.read_chunk_exports([base_export])
    want = {r["gold_chunks"][0] for r in recs}
    missing = [c for c in want if c not in chunks]
    if missing:
        print(f"REFUSED: {len(missing)} gold chunk(s) are not in the base export", file=sys.stderr)
        return f10.EXIT_REFUSED
    dst = Path(cfg.get("gold_hashes") or Path(cfg["out_dir"]) / "gold-hashes.json")
    dst.parent.mkdir(parents=True, exist_ok=True)
    f10.write_json_atomic(dst, {c: asc.sha_text(chunks[c]["content"]) for c in sorted(want)})
    print(json.dumps({"gold_chunks": len(want), "out": str(dst), "base_export_sha256": asc.sha256_file(base_export)}))
    return 0


def cmd_run(cfg: dict[str, Any], args: argparse.Namespace,
            client_factory: Any = None, census_client_factory: Any = None) -> int:
    recs, ident = load_study_records(cfg)
    if (args.corpus_arm, args.glossary) not in required_attachments(cfg):
        raise f10.Refused(f"{args.corpus_arm} x {args.glossary} has no collection")
    receipt = json.loads(Path(cfg["collections"][args.corpus_arm][args.glossary]["load_receipt"]).read_text(
        encoding="utf-8"))
    chunks, unavailable, source = f10.probe_candidates(attachment_cfg(cfg, args.corpus_arm, args.glossary),
                                                       args.corpus_arm, args.glossary, receipt, args.chunk_export,
                                                       args.glossary_file, args.rendered_fields)
    runner = build_runner(cfg, recs, ident, args.corpus_arm, args.glossary, repeat=args.repeat, stage=args.stage, probe_chunks=chunks, probe_unavailable=unavailable,
                          probe_source=source, boost_declared=args.boost_declared,
                          probe_max_glossaries=args.probe_max_glossaries, client_factory=client_factory,
                          census_client_factory=census_client_factory)
    return asyncio.run(runner.run())


def main(argv: Sequence[str] | None = None, *, client_factory: Any = None, census_client_factory: Any = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", type=Path, default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan", help="the 29 legs, their stage and state")
    gh = sub.add_parser("gold-hashes", help="record the gold chunks' content hashes from the base export")
    gh.add_argument("--base-export", required=True, type=Path)
    r = sub.add_parser("run", help="one corpus arm x one glossary arm: the attachment's legs of one stage")
    r.add_argument("--stage", required=True, choices=STAGES)
    r.add_argument("--corpus-arm", required=True, choices=asc.CORPUS_ARMS)
    r.add_argument("--glossary", required=True, choices=STUDY_GLOSSARY_ARMS)
    r.add_argument("--repeat", type=int, default=None)
    r.add_argument("--chunk-export", default=None)
    r.add_argument("--glossary-file", default=None)
    r.add_argument("--rendered-fields", default=None)
    r.add_argument("--boost-declared", type=float, default=None)
    r.add_argument("--probe-max-glossaries", type=int, default=f10.gbp.PROBE_TOP_K)
    pg = sub.add_parser("placebo-glossary", help="the placebo entry lists from the merged glossary (§5.3)")
    pg.add_argument("--merged", required=True, type=Path)
    pg.add_argument("--base-export", required=True, type=Path)
    pg.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "placebo-glossary":
            return cmd_placebo(args.merged, args.base_export, args.out)
        if args.config is None:
            ap.error("--config is required")
        cfg = load_config(args.config)
        if args.cmd == "plan":
            return cmd_plan(cfg)
        if args.cmd == "gold-hashes":
            return cmd_gold_hashes(cfg, args.base_export)
        return cmd_run(cfg, args, client_factory, census_client_factory)
    except f10.Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr, flush=True)
        return f10.EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
