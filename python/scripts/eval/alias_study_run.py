#!/usr/bin/env python
"""Alias study (OA-1000): the retrieval runner (DRAFT v2 prereg §5, §9). An adapter around
round 3's found@10 runner (``glossbench/found10.py``): every round-3 gate is kept, and the
study adds what its prereg asks on top.

    alias_study_run.py --config CFG plan
    alias_study_run.py --config CFG gold-hashes --base-export CHUNKS.jsonl
    alias_study_run.py --config CFG run --stage {A,B} --corpus-arm base --glossary none \\
                       --swap-record SWAP.json [--repeat 2] \\
                       [--chunk-export X --glossary-file F | --rendered-fields R] \\
                       [--boost-declared B] [--probe-max-glossaries N]
    alias_study_run.py --config CFG placebo-glossary --merged MERGED.jsonl --base-export CHUNKS.jsonl \\
                       --out PLACEBO.jsonl

**Legs (§5.4).** Four corpus arms (``base``, ``neighbour``, ``random1``, ``random2``) x two
glossary arms (``none``, ``merged``) x three retrievers (``default`` = found10's ``base``
condition: hybrid + rerank; ``bm25``; ``norerank``) = 24 cells, plus one noise repeat per
corpus arm (default x none, ``--repeat 2``), plus the **placebo leg** (base x default x
``placebo``, §5.3, R7) = **29 legs** (:func:`study_legs`). One ``run`` is one corpus arm with
one glossary arm attached (the swap, ``swap.sh``, happens outside) and measures the legs of
that attachment that belong to its ``--stage``. A repeat run measures ``default`` only, in the
seeded order ``sha256(261101|alias|noise|<corpus arm>|<record>|<condition>)`` (§5.5), so a
repeat is not a replay of the first leg's request sequence. Every record of the accepted file
(the 900 balanced and the 191 descriptive) is run in every leg; every call asks ``top_k`` 50,
so the rows carry the top 10 and the depth-50 pool for diagnostics.

**Two stages (§5.7, R9).** Stage A is the 9 Layer-1 legs (:data:`STAGE_A_LEGS`): base x
default x none, base x default x merged, neighbour / random-1 / random-2 x default x none,
and the noise repeat of each corpus arm. Stage B is the other 19 cells and the placebo leg.
``run --stage A`` measures only stage-A legs and is refused once stage A is committed (the
judge's ``commit-stage-a`` writes ``<out_dir>/STAGE-A-COMMITTED``); ``run --stage B`` is
refused until then, and verifies the commit (every stage-A file still has the bytes it had
when committed) before it measures anything. A stage-B leg on an attachment that also has a
stage-A leg (base x none, base x merged, the expanded arms x none) is written to its own files,
``<corpus>/<glossary>.stageB.{jsonl,summary.json}``, so no stage-A file is ever opened for
writing again: :func:`leg_files` maps every leg to its files (and still reads a single-file
layout written before the stages). Because the default leg (stage A) and the norerank leg
(stage B) of one attachment are measured in different runs, the stage-B run computes the
inactive-reranker comparison against the committed default rows and records it as
``study.inactive_reranker`` (it gates the **default** leg, §9.1).

**Modes.** ``dry_run: true`` (``alias_study_dryrun.py``) skips the record-set identity and
must name no study record. ``pilot: true`` is the pre-freeze pilot (§5.6): the record set is
verified, but only the 191 records on unsatisfied papers are run. Otherwise the run is the main
run and the config must pin the frozen prereg (``prereg: {file, sha256}``); a sha mismatch is a
hard stop (§9.2). The out dir carries a ``STUDY-MODE`` marker, and a pilot dir is never reused
by the main run or the reverse (the pilot's labels are not carried over, §5.6).

**Gates kept from round 3** (found10, unchanged): the swap record's census (chunk total = the
arm's ``expected_chunks``, every stamp the glossary arm's, the glossary file's sha256) and the
optional live census before and after; the depth read-back on every call, with the BM25
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
3. *Restore point (§9.3).* ``corpora.<arm>.restore_point`` names the JSON the live-ops plan
   wrote before the arm's first store write (``schema alias-restore-point/1``: ``index``,
   ``snapshot_id``, ``total``, ``stamps``): its index must be the arm's, its snapshot id set
   and its total the arm's ``expected_chunks``. Its sha256 is in the summary.
4. *Failed calls per leg (§7, §9.2).* More than 2 % failed calls in any ONE retriever's leg is
   a hard stop (round 3 counted over the run).
5. *The record set (§4.1).* The accepted file's sha256 and the balanced-set id sha must be the
   prereg's (not in a dry run).
6. *The placebo glossary (§5.3, R7).* ``placebo-glossary`` writes the placebo entry lists: the
   merged glossary's per-paper lists permuted across the base papers that have at least one
   entry, by the derangement ``sha256(261101|alias|placebo|<paper>)`` ascending, each paper
   receiving the list of the paper after it and the last the first's (:func:`placebo_mapping`;
   no paper keeps its own list). The rows keep the merged file's schema (``paper`` rewritten to
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

RESTORE_SCHEMA = "alias-restore-point/1"
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

#: §5.7 (R9): the 9 Layer-1 legs of stage A; every other leg (19 cells + placebo) is stage B
STAGES = ("A", "B")
STAGE_A_LEGS = frozenset([asc.leg_id(c, asc.REPEAT_GLOSSARY, "default") for c in asc.CORPUS_ARMS]
                         + [asc.leg_id("base", "merged", "default")]
                         + [asc.leg_id(c, asc.REPEAT_GLOSSARY, asc.REPEAT_RETRIEVER, 2) for c in asc.CORPUS_ARMS])
STAGE_B_LABEL_SUFFIX = ".stageB"
COMMIT_MARKER = "STAGE-A-COMMITTED"
MODE_MARKER = "STUDY-MODE"
MODES = ("main", "pilot", "dry_run")


def canonical_sha(obj: Any) -> str:
    return asc.sha_text(json.dumps(obj, sort_keys=True, separators=(",", ":")))


# ------------------------------------------------------------------ legs and stages

def study_legs() -> list[tuple[str, str, str, int]]:
    """The 29 legs of §5.4: :func:`alias_study_common.all_legs` (24 cells, the 4 noise repeats
    and the placebo leg)."""
    return asc.all_legs()


def leg_stage(corpus: str, glossary: str, retriever: str, repeat: int = 1) -> str:
    return "A" if asc.leg_id(corpus, glossary, retriever, repeat) in STAGE_A_LEGS else "B"


def stage_legs(stage: str) -> list[tuple[str, str, str, int]]:
    return [lg for lg in study_legs() if leg_stage(*lg) == stage]


def attachment_legs(corpus: str, glossary: str, repeat: int = 1) -> list[tuple[str, str, str, int]]:
    """The legs one run of (corpus arm, glossary arm[, repeat]) can measure."""
    return [lg for lg in study_legs() if lg[0] == corpus and lg[1] == glossary and lg[3] == repeat]


def run_label(corpus: str, glossary: str, repeat: int, stage: str) -> str:
    """The file label of a run: found10's (``none``, ``none#2``), with ``.stageB`` for the
    stage-B legs of an attachment that also has a stage-A leg."""
    base = f10.arm_label(glossary, repeat if repeat == 2 else None)
    split = any(leg_stage(*lg) == "A" for lg in attachment_legs(corpus, glossary, repeat))
    return base + STAGE_B_LABEL_SUFFIX if stage == "B" and split else base


def leg_files(runs: Path, corpus: str, glossary: str, retriever: str, repeat: int = 1) -> tuple[Path, Path]:
    """``(rows, summary)`` of one leg. A leg written before the stages (one file per
    attachment, every retriever in it) is still found: when the staged summary is absent and
    the single-file summary holds the leg's condition, that file is the leg's."""
    stage = leg_stage(corpus, glossary, retriever, repeat)
    sp = runs / corpus / f"{run_label(corpus, glossary, repeat, stage)}.summary.json"
    legacy = runs / corpus / f"{f10.arm_label(glossary, repeat if repeat == 2 else None)}.summary.json"
    if not sp.is_file() and legacy != sp and legacy.is_file():
        s = json.loads(legacy.read_text(encoding="utf-8"))
        if asc.RETRIEVERS[retriever] in (s.get("conditions") or {}):
            sp = legacy
    return sp.with_name(sp.name.removesuffix(".summary.json") + ".jsonl"), sp


def run_status(out: Path) -> dict[str, Any]:
    """The 29 legs and each one's state from the summaries (``missing`` when never run)."""
    st: dict[str, Any] = {}
    for c, g, r, rep in study_legs():
        _, sp = leg_files(out, c, g, r, rep)
        lid = asc.leg_id(c, g, r, rep)
        if not sp.is_file():
            st[lid] = "missing"
            continue
        s = json.loads(sp.read_text(encoding="utf-8"))
        st[lid] = s["status"] if asc.RETRIEVERS[r] in (s.get("conditions") or {}) else "missing"
    return st


def read_commit_marker(out: Path) -> dict[str, Any] | None:
    p = out / COMMIT_MARKER
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def config_mode(cfg: Mapping[str, Any]) -> str:
    if cfg.get("dry_run"):
        return "dry_run"
    return "pilot" if cfg.get("pilot") else "main"


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
    arms = set(cfg["arms"])
    if not set(asc.GLOSSARY_ARMS) <= arms <= set(STUDY_GLOSSARY_ARMS):
        raise f10.Refused(f"arms must be {list(asc.GLOSSARY_ARMS)} (and {PLACEBO!r} for the placebo leg), "
                          f"got {sorted(arms)}")
    if PLACEBO in arms:
        mine = cfg["arms"][PLACEBO]
        mine_s = set(mine.get("stamps") or []) | set(mine.get("stamp_prefixes") or [])
        for other in asc.GLOSSARY_ARMS:
            o = cfg["arms"][other]
            if mine_s & (set(o.get("stamps") or []) | set(o.get("stamp_prefixes") or [])):
                raise f10.Refused(f"arms.{PLACEBO} shares a stamp with arms.{other}: the placebo has its own version "
                                  "stamp, so a census tells the arms apart (§5.3)")
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
    if not cfg.get("dry_run"):
        for c in asc.CORPUS_ARMS:
            for k in ("chunk_export", "restore_point"):
                if not cfg["corpora"][c].get(k):
                    raise f10.Refused(f"corpora.{c}.{k} is not set")
        if not cfg.get("gold_hashes"):
            raise f10.Refused("config has no 'gold_hashes' (the base export's gold-chunk content hashes)")
    if config_mode(cfg) == "main" and not cfg.get("prereg"):
        raise f10.Refused("the main run needs prereg: {file, sha256} (the frozen prereg, §9.2, §15)")
    return cfg


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


def check_restore_point(path: Path, ccfg: Mapping[str, Any]) -> dict[str, Any]:
    rp = json.loads(path.read_text(encoding="utf-8"))
    problems = []
    if rp.get("schema") != RESTORE_SCHEMA:
        problems.append(f"schema {rp.get('schema')!r} != {RESTORE_SCHEMA!r}")
    if rp.get("index") != ccfg.get("index"):
        problems.append("restore point is for another index")
    if not rp.get("snapshot_id"):
        problems.append("no snapshot_id")
    if rp.get("total") != ccfg.get("expected_chunks"):
        problems.append(f"restore-point total {rp.get('total')} != expected chunks {ccfg.get('expected_chunks')}")
    if problems:
        raise f10.Refused("restore point: " + "; ".join(problems))
    return {"sha256": asc.sha256_file(path), "snapshot_id_sha256": asc.sha_text(str(rp["snapshot_id"]))}


def noise_order(corpus_arm: str) -> Any:
    seed = asc.SEEDS["noise"]

    def key(rec: Mapping[str, Any], cond: str) -> str:
        return asc.seeded_key(seed, corpus_arm, str(rec["record_id"]), cond)
    return key


# ------------------------------------------------------------------ the runner

def field_coverage(probe_chunks: Sequence[tuple[str, str, str]] | None, unavailable: str | None,
                   source: str | None, swap: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """§5.3 / §3.3: the attached field's coverage on this collection: the share of chunks with
    a non-empty ``symbol_glossary`` and the mean rendered characters (over all chunks, and over
    the chunks with a field). Read from the probe candidates (``--chunk-export`` with
    ``--glossary-file`` or ``--rendered-fields``: every chunk of the index with its field,
    rendered offline as the backfill renders it); the backfill's own count from the swap record
    (``apply.chunks_with_glossary``) is printed beside it when present."""
    apply = (swap or {}).get("apply") or {}
    backfill = {k: apply.get(k) for k in ("chunks", "chunks_with_glossary") if k in apply}
    if not probe_chunks:
        return {"available": False, "reason": unavailable or "no probe candidates", "backfill": backfill}
    n = len(probe_chunks)
    lens = [len(f or "") for _, _, f in probe_chunks]
    with_f = [x for x in lens if x > 0]
    return {"available": True, "source": source, "chunks": n, "chunks_with_field": len(with_f),
            "share_with_field": round(len(with_f) / n, 6) if n else None,
            "mean_chars": round(sum(lens) / n, 3) if n else None,
            "mean_chars_with_field": round(sum(with_f) / len(with_f), 3) if with_f else None,
            "backfill": backfill}


def inactive_reranker(default_rows: Sequence[Mapping[str, Any]], norerank_rows: Sequence[Mapping[str, Any]]
                      ) -> dict[str, Any]:
    """§9.1 across the stages: default's ranking equal to norerank's on every record (failed
    calls left out) means the reranker was not active."""
    d = {r["record_id"]: r.get("ranked_chunk_ids") for r in default_rows if not r.get("failed")}
    n = {r["record_id"]: r.get("ranked_chunk_ids") for r in norerank_rows if not r.get("failed")}
    both = sorted(set(d) & set(n))
    same = sum(d[k] == n[k] for k in both)
    gate = None
    if both and same == len(both):
        gate = (f"{asc.GATE}: the default ranking equals the norerank ranking on every record "
                f"({same}/{len(both)}): the reranker was not active")
    return {"records": len(both), "identical": same, "gate": gate}


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

    def _cross_stage_reranker(self, done: Mapping[str, Mapping[str, Any]]) -> dict[str, Any] | None:
        """A stage-B run holding the norerank leg of an attachment whose default leg is stage A."""
        if "norerank" not in self.conditions or "base" in self.conditions:
            return None
        c, g, rep = self.study["corpus_arm"], self.study["glossary_arm"], int(self.study.get("repeat") or 1)
        if leg_stage(c, g, "default", rep) != "A":
            return None
        rows_a, _ = leg_files(Path(self.cfg["out_dir"]), c, g, "default", rep)
        default_rows = [r for r in f10.read_jsonl_repair_tail(rows_a) if r["condition"] == "base"] \
            if rows_a.is_file() else []
        nr = [r for r in done.values() if r["condition"] == "norerank"]
        return {"default_leg": asc.leg_id(c, g, "default", rep), "norerank_leg": asc.leg_id(c, g, "norerank", rep),
                "default_rows_sha256": asc.sha256_file(rows_a) if rows_a.is_file() else None,
                **inactive_reranker(default_rows, nr)}

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
        xr = self._cross_stage_reranker(done)
        if xr is not None:
            study["inactive_reranker"] = xr
        summary["study"] = study
        f10.write_json_atomic(self.summary_path, summary)
        return rc


def build_runner(cfg: dict[str, Any], recs: Sequence[Mapping[str, Any]], ident: Mapping[str, Any],
                 corpus_arm: str, glossary: str, swap_record: Path, repeat: int | None = None,
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
        raise f10.Refused(f"--stage {stage!r}: A (the 9 Layer-1 legs) or B (the rest and the placebo), §5.7")
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
            commit = aj.verify_stage_a_commit(Path(marker["commit"]))
        except SystemExit as e:
            raise f10.Refused(f"stage-A commit does not verify: {e}") from e
    ccfg = cfg["corpora"][corpus_arm]
    extra: dict[str, Any] = {}
    if not cfg.get("dry_run") or ccfg.get("chunk_export"):
        hashes = json.loads(Path(cfg["gold_hashes"]).read_text(encoding="utf-8")) if cfg.get("gold_hashes") else {}
        extra["gold_presence"] = check_gold_presence(recs, export_paths(ccfg["chunk_export"]), hashes)
    if ccfg.get("restore_point"):
        extra["restore_point"] = check_restore_point(Path(ccfg["restore_point"]), ccfg)
    label = run_label(corpus_arm, glossary, rep, stage)
    try:
        swap_doc = json.loads(Path(swap_record).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        swap_doc = {}
    extra["field_coverage"] = field_coverage(kw.get("probe_chunks"), kw.get("probe_unavailable"),
                                             kw.get("probe_source"), swap_doc)
    study = {"corpus_arm": corpus_arm, "glossary_arm": glossary, "repeat": rep, "record_set": dict(ident),
             "seeds": {"noise": asc.SEEDS["noise"]} if rep == 2 else {}, "seed_rule": asc.SEED_RULE,
             "dry_run": bool(cfg.get("dry_run")), "pilot": bool(cfg.get("pilot")), "mode": mode, "stage": stage,
             "legs": [asc.leg_id(*lg) for lg in legs], "run_label": label, "placebo": glossary == PLACEBO,
             "request_order": NOISE_ORDER_RULE if rep == 2 else "record file order",
             "prereg": prereg, "stage_a_commit": None if commit is None else
             {"sha256": commit["commit_sha256"], "verified": True}, **extra}
    conds = [asc.RETRIEVERS[lg[2]] for lg in legs]
    order = noise_order(corpus_arm) if rep == 2 else None
    return AliasRunner(cfg, records_for(recs, corpus_arm), corpus_arm, glossary, out, swap_record,
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
    """The placebo rows and their counts. ``entries``: the merged glossary's rows (``paper``,
    ``alias``, ``primary``, ``kind`` …). ``corpus_papers``: the base export's papers; an entry
    on another paper is refused (§5.3: the OA-1000 papers with at least one merged entry)."""
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
        return str(e["alias"]), tuple(aa.words(str(e["primary"])))

    rows: list[dict[str, Any]] = []
    coincide_entries = 0
    coincide_papers = 0
    for recv in placebo_order(list(by)):
        src = mapping[recv]
        own = {key(e) for e in by[recv]}
        hit = 0
        for e in by[src]:
            row = {k: v for k, v in e.items() if k not in PLACEBO_DROP_KEYS}
            row.update({"paper": recv, "placebo": True})
            rows.append(row)
            hit += int(key(e) in own)
        coincide_entries += hit
        coincide_papers += int(hit > 0)
    pairs = "".join(f"{r}\t{s}\n" for r, s in sorted(mapping.items()))
    counts = {"papers": len(mapping), "entries": len(rows), "fixed_points": sum(r == s for r, s in mapping.items()),
              "mapping_sha256": asc.sha_text(pairs),
              "kinds": dict(Counter(str(r.get("kind")) for r in rows)),
              # entries a paper receives that equal one of its own (alias, primary words): kept, as
              # the rule permutes whole lists; counted so the reading of the leg can weigh them
              "own_entry_coincidences": {"entries": coincide_entries, "papers": coincide_papers}}
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
                      "stage_a_complete": all(st[k] == "complete" for k in STAGE_A_LEGS),
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
    swap = json.loads(Path(args.swap_record).read_text(encoding="utf-8"))
    chunks, unavailable, source = f10.probe_candidates(cfg, args.corpus_arm, args.glossary, swap, args.chunk_export,
                                                       args.glossary_file, args.rendered_fields)
    runner = build_runner(cfg, recs, ident, args.corpus_arm, args.glossary, Path(args.swap_record),
                          repeat=args.repeat, stage=args.stage, probe_chunks=chunks, probe_unavailable=unavailable,
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
    r.add_argument("--swap-record", required=True)
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
