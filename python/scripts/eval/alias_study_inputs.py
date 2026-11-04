#!/usr/bin/env python
"""Alias study (OA-1000): build the inputs of the fixed sensitivities (prereg §7, R6) and the
declared sensitivity-set lists (§7; §4.3 items 4-6; §14 O10). A deviation: these were not
built before the stage-A analysis ran (``DEVIATIONS.md`` D-1).

    PYTHONPATH=python python python/scripts/eval/alias_study_inputs.py \\
        --study-dir <study dir> --base-export <the OA-1000 chunk export> \\
        --public-cuts docs/plans/results/alias-study-2026-11/artifacts/tercile-cuts.json

Everything is built mechanically from **pre-outcome sources only**: the frozen record set, the
build logs of the pilots, calibration and validation, the v4 corpus manifests (OpenAlex
metadata as the corpus builder recorded it), the arm chunk files and the stored embeddings.
No judge label, retrieval row, found@10, statistics output or spot-check label is read: every
path goes through :func:`guard`, which accepts only the files of :func:`allowed_inputs` and
refuses anything under :data:`DENIED` whatever the allow-list says. No network call is made.

What it writes, under ``<study-dir>/main/analysis-inputs/`` (0700 / 0600, refused if the
directory is not empty), the file names the ``alias_study_stats.py`` config keys read:

* ``paper-covariates.json`` (``paper_covariates``): per study paper (the 300 satisfied
  papers), ``topic`` (OpenAlex primary topic), ``citation_count`` (OpenAlex ``cited_by_count``
  of the study work), ``text_similarity`` (mean SFR cosine over every pair of the study paper's
  chunks and its attributed neighbours' chunks), ``xml_only`` and ``added_chunks``
  {``neighbour``, ``random1``, ``random2``} (:data:`DEFINITIONS`);
* ``neighbour-attribution.json`` (``neighbour_attribution``): added neighbour paper, keyed as
  the chunk files and the judge's pairs key it (``c_paper``), -> the study paper it was
  attributed to (§5.1, O5);
* ``tercile-cuts.json`` (``tercile_cuts``): ``citation_count`` / ``text_similarity`` -> [c1, c2],
  by :func:`tercile_cuts`; the same bytes are written to ``--public-cuts`` (numbers and
  provenance only, checked by :func:`alias_study_common.public_problems`);
* ``without-calibration-overlap.txt``, ``without-pilot-papers.txt``,
  ``without-validation-papers.txt`` (``sensitivity_sets``): one id per line, the format
  ``alias_study_stats._subset`` reads;
* ``inputs-manifest.json``: provenance, every source's sha256, the definitions, the counts
  and every output's sha256; ``stats-config-keys.json``: the config keys to add; ``SHA256SUMS``.

Each count the prereg states (2 calibration-overlap records, 13 pilot papers, 8 validation
papers with 24 balanced records, 300 study papers, 2,423 neighbours, the v4 chunk and source
counts) is asserted; a mismatch stops the build.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import math
import os
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np

_HERE = Path(__file__).resolve().parent
REPO = _HERE.parents[2]
for _p in (_HERE, _HERE / "glossbench"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import alias_study_common as asc  # noqa: E402

SCHEMA = "alias-study-analysis-inputs/1"
DEVIATION = "DEVIATIONS.md D-1 (2026-11-04): built after the stage-A analysis had run; owner-approved"

#: the base chunk export (OA-1000, prereg §4.1 ``ff16e611…``)
BASE_EXPORT_SHA256 = "ff16e611a28c48022a41d9de162b7aeabaa0e0c9b95d7ad2516fe515dc37cf8d"
#: the prereg's counts (§4.1, §4.3 items 4-6, §5.1 v4 table); a mismatch stops the build
EXPECT: dict[str, int] = {
    "study_papers": 300, "calibration_overlap_records": 2, "pilot_papers": 13, "validation_papers": 8,
    "validation_papers_balanced_records": 24, "neighbour_papers": 2423, "random_papers": 2423,
    "neighbour_jats": 2221, "neighbour_pdf": 202, "study_papers_without_neighbours": 2,
}
ARMS = {"neighbour": "neighbour", "random1": "random-1", "random2": "random-2"}

#: study-dir-relative inputs (the allow-list; the embedding shards are added per directory)
LAYOUT: dict[str, str] = {
    "records": "out/main/accepted-alias-O.jsonl",
    "main_drafts": "out/main/draft-alias-O.jsonl",
    "pilot1_drafts": "out/draft-alias-O-pilot.jsonl",
    "pilot2_drafts": "out/pilot2/draft-alias-O.jsonl",
    "calib_pairs": "calib/pairs.jsonl",
    "validation_drafts": "out/validation/draft-alias-V.jsonl",
    "counts": "corpus/manifests/counts-v4.json",
    "neighbour_selection": "corpus/manifests/neighbour-selection-v4.json",
    "random1_draw": "corpus/manifests/random-1-draw-v4.json",
    "random2_draw": "corpus/manifests/random-2-draw-v4.json",
    "per_study_chunks": "corpus/manifests/per-study-chunks-v4.json",
    "chunks_neighbour": "corpus/chunks-neighbour-v4.jsonl",
    "chunks_random1": "corpus/chunks-random-1-v4.jsonl",
    "chunks_random2": "corpus/chunks-random-2-v4.jsonl",
    "emb_base_sums": "ops/emb/base/SHA256SUMS",
    "emb_neighbour_sums": "ops/emb/neighbour/SHA256SUMS",
}
EMB_DIRS = {"base": "ops/emb/base", "neighbour": "ops/emb/neighbour"}
OUT_DIR = "main/analysis-inputs"
#: the committed cut points, repo-relative: the statistics re-run resolves it against its checkout
PUBLIC_CUTS_REL = "docs/plans/results/alias-study-2026-11/artifacts/tercile-cuts.json"
#: the sources the public cut file names (what the cuts depend on)
PUBLIC_SOURCES = ("records", "counts", "neighbour_selection", "chunks_neighbour", "base_export",
                  "emb_base_SHA256SUMS", "emb_neighbour_SHA256SUMS")
#: never read, whatever the allow-list says: outcomes, labels, statistics, spot checks
DENIED: tuple[str, ...] = ("main/judge", "main/analysis-", "main/runs", "spot", "pilot/judge", "pilot/report")
#: never read anywhere, by file name (fnmatch): the judge's, the statistics' and the spot checks' outputs
DENIED_NAMES: tuple[str, ...] = ("final.jsonl", "labels-*.jsonl", "judge-summary.json", "hits.jsonl",
                                 "*.owner-labels.jsonl", "stage-a-commit*.json", "results-stage-*.json")

DEFINITIONS: dict[str, str] = {
    "study_papers": "the 300 satisfied papers of the frozen record set (asc.split_records; prereg §4.1)",
    "topic": "the study work's OpenAlex primary topic id, as the v4 neighbour-selection manifest records it "
             "(study_works.<paper>.topic; §5.1 build facts: study-paper topics from the live OpenAlex API); §7 R6 "
             "topic-level clustering",
    "citation_count": "the study work's OpenAlex cited_by_count as the v4 neighbour-selection manifest records it "
                      "(study_works.<paper>.cited_by_count, from the corpus builder's OpenAlex work lookup, identical "
                      "to the v1 manifest's); §7 R6 citation-count tercile",
    "text_similarity": "mean cosine similarity of the stored SFR-Embedding-Mistral vectors (ops/emb, the served "
                       "embedder) over every pair (i, j), i a chunk of the study paper in the base export, j a chunk of "
                       "one of its attributed neighbour papers in the v4 neighbour chunk file (a PDF-sourced neighbour's "
                       "reference-list chunks included, as they are in the collection); equals "
                       "(sum_i u_i) . (sum_j u_j) / (n_i n_j) with u the unit vectors; null for a study paper with no "
                       "attributed neighbour; §7 R6 text-similarity tercile",
    "xml_only": "true when every attributed neighbour paper and every paper of both random draws taken for the "
                "study paper is JATS-sourced (its chunk-file key is its PMC id; a PDF-sourced paper is keyed by its "
                "OpenAlex work id); vacuously true for a study paper with no added paper; §7 R6 XML-only stratum",
    "added_chunks": "per arm, the chunks of the papers added for the study paper (neighbour: its attributed "
                    "neighbours; random1 / random2: the draw's takes for it), counted in the v4 arm chunk files; "
                    "absent for a study paper with no added paper (the prereg's 298-paper population, §7 R23; the "
                    "statistics count it in papers_left_out); equal to per-study-chunks-v4.json wherever that lists "
                    "the paper; §7 R6 / R23 chunk-volume covariate",
    "neighbour_attribution": "added neighbour paper (its chunk-file key, = the judge pairs' c_paper) -> the study "
                             "paper it is attributed to in neighbour-selection-v4.json `attributed` (§5.1, O5); §7 "
                             "origin of displacing chunks",
    "tercile_cuts": "per variable, over the study papers with a value: c1 = v_(ceil(n/3)), c2 = v_(ceil(2n/3)) of the "
                    "ascending values (the inverse empirical CDF, Hyndman-Fan type 1), read by the statistics as "
                    "T1: v <= c1; T2: c1 < v <= c2; T3: v > c2 (§7 R6). The prereg fixes the variables and that "
                    "cuts are committed, not the quantile convention; type 1 is the builder's choice (DEVIATIONS D-1)",
    "without_calibration_overlap": "balanced record ids that are calibration record ids (calib/pairs.jsonl "
                                   "record_id) and were drafted by the main run (§4.3 item 5)",
    "without_pilot_papers": "satisfied papers with a draft in either pilot's draft log (§4.3 item 4)",
    "without_validation_papers": "satisfied papers with a judge-validation draft (out/validation draft log; §4.3 "
                                 "item 6, §14 O10)",
}


# ------------------------------------------------------------------ the guard

class GuardError(SystemExit):
    pass


def denied(rel: str) -> str | None:
    """The :data:`DENIED` prefix or :data:`DENIED_NAMES` pattern ``rel`` (study-dir relative,
    posix) falls under, else None."""
    for d in DENIED:
        if d.endswith("-"):
            if rel.startswith(d):
                return d
        elif rel == d or rel.startswith(d + "/"):
            return d
    return denied_name(rel)


def denied_name(path: str) -> str | None:
    name = PurePosixPath(path).name
    return next((pat for pat in DENIED_NAMES if fnmatch.fnmatchcase(name, pat)), None)


class Guard:
    """Every read goes through :meth:`check`. A study-dir input must be allow-listed **as
    given** (no symlink resolution), contain no symlink in itself or in any parent below the
    study root, resolve under the root, and fall under no :data:`DENIED` prefix; no input
    anywhere may carry a :data:`DENIED_NAMES` name. The base export lies outside the root and is
    pinned by its sha256 (:data:`BASE_EXPORT_SHA256`) instead."""

    def __init__(self, study_dir: Path, base_export: Path, allowed: Iterable[Path] = ()) -> None:
        self.root_given = Path(os.path.abspath(study_dir))
        self.root = self.root_given.resolve()
        self.base_export = Path(os.path.abspath(base_export))
        self.allowed: set[Path] = {self.base_export}
        self.read: dict[str, str] = {}
        for p in allowed:
            self.allow(p)

    def allow(self, p: str | Path) -> None:
        self.allowed.add(Path(os.path.abspath(p)))

    def check(self, p: str | Path) -> Path:
        a = Path(os.path.abspath(p))
        if (pat := denied_name(a.as_posix())) is not None:
            raise GuardError(f"guard: refusing an outcome file name ({pat})")
        try:
            rel = a.relative_to(self.root_given).as_posix()
        except ValueError:
            rel = None
        if rel is not None and (d := denied(rel)) is not None:
            raise GuardError(f"guard: refusing to read under {d} ({rel})")
        if a not in self.allowed:
            raise GuardError(f"guard: {rel or a} is not an allow-listed input")
        if a == self.base_export:
            if a.is_symlink():
                raise GuardError("guard: the base export is a symlink")
            return a
        if rel is None:
            raise GuardError("guard: an input outside the study root")
        q = a
        while q != self.root_given:
            if q.is_symlink():
                raise GuardError(f"guard: refusing a symlink ({q.relative_to(self.root_given).as_posix()})")
            q = q.parent
        r = a.resolve()
        if r != self.root and self.root not in r.parents:
            raise GuardError(f"guard: {rel} resolves outside the study root")
        if (d := denied(r.relative_to(self.root).as_posix())) is not None:
            raise GuardError(f"guard: {rel} resolves under {d}")
        return a

    def sha(self, p: str | Path, label: str) -> str:
        q = self.check(p)
        s = asc.sha256_file(q)
        self.read[label] = s
        return s

    def jsonl(self, p: str | Path) -> list[dict[str, Any]]:
        return list(_iter_jsonl(self.check(p)))

    def json(self, p: str | Path) -> Any:
        return json.loads(self.check(p).read_text(encoding="utf-8"))

    def emb_files(self, study_dir: Path) -> dict[str, list[tuple[Path, str]]]:
        """Each embedding directory's ``SHA256SUMS`` read through the guard, and the plain
        ``*.emb.jsonl`` names it lists allow-listed: ``arm -> [(path, listed sha256)]``."""
        out: dict[str, list[tuple[Path, str]]] = {}
        for arm, d in EMB_DIRS.items():
            sums = study_dir / d / "SHA256SUMS"
            self.sha(sums, f"emb_{arm}_SHA256SUMS")
            files = []
            for name, sha in read_sums(self.check(sums)):
                if not name.endswith(".emb.jsonl"):
                    continue
                if "/" in name or name.startswith("."):
                    raise GuardError("guard: a SHA256SUMS entry names a path, not a file")
                f = study_dir / d / name
                self.allow(f)
                files.append((f, sha))
            out[arm] = files
        return out


def allowed_inputs(study_dir: Path) -> list[Path]:
    """The fixed allow-list, :data:`LAYOUT`; the embedding files are added by
    :meth:`Guard.emb_files` from their directories' ``SHA256SUMS``, read through the guard."""
    return [study_dir / rel for rel in LAYOUT.values()]


def read_sums(path: Path) -> list[tuple[str, str]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            sha, name = line.split(None, 1)
            rows.append((name.strip().lstrip("*"), sha))
    return rows


# ------------------------------------------------------------------ pure pieces

def tercile_cuts(values: Sequence[float]) -> list[float]:
    """:data:`DEFINITIONS` ``tercile_cuts``: [v_(ceil(n/3)), v_(ceil(2n/3))] of the ascending
    values (inverse empirical CDF). Needs at least 3 values."""
    v = sorted(float(x) for x in values)
    n = len(v)
    if n < 3:
        raise ValueError("tercile cuts need at least 3 values")
    return [v[math.ceil(n / 3) - 1], v[math.ceil(2 * n / 3) - 1]]


def tercile_sizes(values: Iterable[float], cuts: Sequence[float]) -> dict[str, int]:
    """Papers per tercile under the statistics' rule (``alias_study_stats.tercile_of``)."""
    c1, c2 = float(cuts[0]), float(cuts[1])
    k = Counter("T1" if v <= c1 else ("T2" if v <= c2 else "T3") for v in values)
    return {t: int(k.get(t, 0)) for t in ("T1", "T2", "T3")}


def work_key(wid: str, works: Mapping[str, Mapping[str, Any]], have: set[str]) -> str:
    """The chunk-file paper key of a manifest work: its PMC id when converted from JATS, else its
    work id (as ``alias_added_glossaries.verify_corpus`` maps them); exactly one must exist."""
    cand = [k for k in dict.fromkeys((wid, (works.get(wid) or {}).get("pmcid"))) if k and k in have]
    if len(cand) != 1:
        raise SystemExit(f"a manifest work maps to {len(cand)} chunk-file papers")
    return cand[0]


def source_of(wid: str, key: str, works: Mapping[str, Mapping[str, Any]]) -> str:
    """``jats`` when the chunk-file key is the work's PMC id, ``pdf`` when it is the work id
    (``alias_corpus_run``: ``_chunk(w, recs, w.pmcid, "jats")`` / ``_chunk(w, recs, w.wid, "pdf")``)."""
    pmcid = (works.get(wid) or {}).get("pmcid")
    if pmcid and key == pmcid and key != wid:
        return "jats"
    if key == wid:
        return "pdf"
    raise SystemExit("a chunk-file key is neither the work's PMC id nor its work id")


def mean_pair_cosine(sum_a: np.ndarray, n_a: int, sum_b: np.ndarray, n_b: int) -> float | None:
    """Mean of cos(a_i, b_j) over all pairs, from the sums of unit vectors."""
    if n_a <= 0 or n_b <= 0:
        return None
    return float(np.dot(sum_a, sum_b) / (n_a * n_b))


def sensitivity_sets(records: Sequence[Mapping[str, Any]], main_draft_ids: set[str], calib_record_ids: set[str],
                     pilot_papers: set[str], validation_papers: set[str]) -> dict[str, list[str]]:
    """:data:`DEFINITIONS` ``without_*``: the ids each declared sensitivity drops, sorted."""
    bal, _ = asc.split_records(records)
    satisfied = {str(r["paper"]) for r in bal}
    bal_ids = {str(r["record_id"]) for r in bal}
    return {"without_calibration_overlap": sorted(calib_record_ids & main_draft_ids & bal_ids),
            "without_pilot_papers": sorted(pilot_papers & satisfied),
            "without_validation_papers": sorted(validation_papers & satisfied)}


# ------------------------------------------------------------------ embeddings

def _unit_sums(path: str, want_papers: list[str] | None) -> tuple[dict[str, list[float]], dict[str, int],
                                                                  dict[str, str]]:
    """One embedding file: per paper (``metadata.source_path`` root) the sum of unit vectors and
    the chunk count, and every chunk id -> paper. ``want_papers`` None: every paper."""
    want = None if want_papers is None else set(want_papers)
    sums: dict[str, np.ndarray] = {}
    cnt: Counter[str] = Counter()
    ids: dict[str, str] = {}
    with open(path, encoding="utf-8", newline="\n") as fh:
        head = json.loads(fh.readline())
        if head.get("schema") != "ragstack.embedding_file/v1":
            raise SystemExit(f"{Path(path).name}: not a ragstack.embedding_file/v1 file")
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            sp = ((row.get("metadata") or {}).get("source_path") or "").split("#", 1)[0] or row["doc_id"]
            ids[row["id"]] = sp
            if want is not None and sp not in want:
                continue
            v = np.asarray(row["embedding"], dtype=np.float64)
            nv = float(np.linalg.norm(v))
            if not nv > 0:
                raise SystemExit(f"{Path(path).name}: a zero embedding")
            sums[sp] = sums[sp] + v / nv if sp in sums else v / nv
            cnt[sp] += 1
    return {k: v.tolist() for k, v in sums.items()}, dict(cnt), ids


def embedding_sums(files: Sequence[Path], want_papers: set[str] | None, workers: int
                   ) -> tuple[dict[str, np.ndarray], dict[str, int], dict[str, str]]:
    """:func:`_unit_sums` over several files, combined in the files' sorted order (deterministic)."""
    files = sorted(files)
    want = None if want_papers is None else sorted(want_papers)
    if workers > 1 and len(files) > 1:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            parts = list(ex.map(_unit_sums, [str(f) for f in files], [want] * len(files)))
    else:
        parts = [_unit_sums(str(f), want) for f in files]
    sums: dict[str, np.ndarray] = {}
    cnt: Counter[str] = Counter()
    ids: dict[str, str] = {}
    for s, c, i in parts:
        for k, v in s.items():
            a = np.asarray(v, dtype=np.float64)
            sums[k] = sums[k] + a if k in sums else a
        cnt.update(c)
        for cid, p in i.items():
            if cid in ids:
                raise SystemExit("a chunk id occurs in two embedding files")
            ids[cid] = p
    return sums, dict(cnt), ids


# ------------------------------------------------------------------ the build

def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    """JSON lines split on newline only (``str.splitlines`` would also split on the U+2028 /
    U+0085 a chunk's text can carry)."""
    with open(path, encoding="utf-8", newline="\n") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def _chunk_papers(rows: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """chunk id -> paper key (``source_path`` root, as ``alias_study_common.read_chunk_exports``)."""
    out: dict[str, str] = {}
    for r in rows:
        sp = ((r.get("metadata") or {}).get("source_path") or "").split("#", 1)[0] or r["doc_id"]
        if r["chunk_id"] in out:
            raise SystemExit("duplicate chunk id in a chunk file")
        out[r["chunk_id"]] = sp
    return out


def _ratio(xs: Sequence[float]) -> float | None:
    """mean / sd (ddof 1), rounded to 4 places: prereg §7 quotes 0.216 over the 298 study papers
    with added papers (``per-study-chunks-v4.json``)."""
    a = np.asarray(xs, float)
    return round(float(a.mean() / a.std(ddof=1)), 4) if a.size > 1 and a.std(ddof=1) > 0 else None


def tercile_label(v: float, cuts: Sequence[float]) -> str:
    return "T1" if v <= float(cuts[0]) else ("T2" if v <= float(cuts[1]) else "T3")


def type7_cuts(values: Sequence[float]) -> list[float]:
    """The linear-interpolation quantiles (Hyndman-Fan type 7, numpy's default) at 1/3, 2/3."""
    return [float(x) for x in np.quantile(np.asarray(values, float), [1 / 3, 2 / 3])]


def open_choice_checks(cit: Sequence[float], sim: Sequence[float], cuts: Mapping[str, Sequence[float]],
                       citer: Sequence[float]) -> dict[str, Any]:
    """D-1's measurable open choices, counts only: papers whose tercile changes (a) under the
    type-7 quantile instead of type 1, per variable; (b) under the citer-list citation count
    (``per_study.<paper>.citer_list.cited_by_count``) instead of the work record's, each with its
    own type-1 cuts. ``cit`` and ``citer`` are in the same paper order."""
    out: dict[str, Any] = {}
    for var, vals in (("citation_count", cit), ("text_similarity", sim)):
        c7 = type7_cuts(vals)
        out[f"type7_{var}"] = {"cuts": c7, "papers_changing_tercile": sum(
            tercile_label(v, cuts[var]) != tercile_label(v, c7) for v in vals),
            "tercile_sizes": tercile_sizes(vals, c7)}
    cc = tercile_cuts(citer)
    out["citer_list_citation_count"] = {
        "papers_differing": sum(a != b for a, b in zip(cit, citer, strict=True)), "cuts": cc,
        "papers_changing_tercile": sum(tercile_label(a, cuts["citation_count"]) != tercile_label(b, cc)
                                       for a, b in zip(cit, citer, strict=True))}
    return out


def _paper(row: Mapping[str, Any]) -> str:
    """A draft row's paper; a row without one is a hard failure."""
    if not row.get("paper"):
        raise SystemExit("a draft row has no paper")
    return str(row["paper"])


def _expect(name: str, got: int) -> None:
    want = EXPECT[name]
    if got != want:
        raise SystemExit(f"count {name}: {got} != the prereg's {want}")


def build(study_dir: Path, base_export: Path, workers: int = 8, verify_record_set: bool = True
          ) -> dict[str, Any]:
    """Everything in memory: ``{covariates, attribution, cuts, sets, manifest}``."""
    g = Guard(study_dir, base_export, allowed_inputs(study_dir))
    P = {k: study_dir / v for k, v in LAYOUT.items()}

    # the record set (§4.1)
    g.sha(P["records"], "records")
    records = asc.load_records(g.check(P["records"]))
    if verify_record_set:
        asc.verify_record_set(g.check(P["records"]), records)
    bal, _ = asc.split_records(records)
    study = sorted({str(r["paper"]) for r in bal})
    _expect("study_papers", len(study))

    # the declared sensitivity sets (§4.3 items 4-6, O10)
    for k in ("main_drafts", "pilot1_drafts", "pilot2_drafts", "calib_pairs", "validation_drafts"):
        g.sha(P[k], k)
    main_ids = {str(r["record_id"]) for r in g.jsonl(P["main_drafts"])}
    calib_ids = {str(p["record_id"]) for p in g.jsonl(P["calib_pairs"])}
    pilots = {_paper(r) for k in ("pilot1_drafts", "pilot2_drafts") for r in g.jsonl(P[k])}
    vpapers = {_paper(r) for r in g.jsonl(P["validation_drafts"])}
    sets = sensitivity_sets(records, main_ids, calib_ids, pilots, vpapers)
    _expect("calibration_overlap_records", len(sets["without_calibration_overlap"]))
    _expect("pilot_papers", len(sets["without_pilot_papers"]))
    _expect("validation_papers", len(sets["without_validation_papers"]))
    vset = set(sets["without_validation_papers"])
    _expect("validation_papers_balanced_records", sum(str(r["paper"]) in vset for r in bal))

    # the corpus manifests, pinned by counts-v4.json (§5.1)
    g.sha(P["counts"], "counts")
    counts = g.json(P["counts"])
    for name, want in counts["manifest_sha256"].items():
        label = {"neighbour-selection-v4.json": "neighbour_selection", "random-1-draw-v4.json": "random1_draw",
                 "random-2-draw-v4.json": "random2_draw", "per-study-chunks-v4.json": "per_study_chunks"}.get(name)
        if label is None:
            continue
        if g.sha(P[label], label) != want:
            raise SystemExit(f"{name}: sha256 != counts-v4.json")
    sel = g.json(P["neighbour_selection"])
    draws = {a: g.json(P[f"{a}_draw"]) for a in ("random1", "random2")}
    psc = g.json(P["per_study_chunks"])
    if set(sel["study_works"]) != set(study) or set(sel["attributed"]) != set(study):
        raise SystemExit("the neighbour-selection manifest's study papers are not the 300 satisfied papers")

    # arm chunk files: paper keys and chunks per paper (pinned by counts-v4.json)
    chunk_paper: dict[str, dict[str, str]] = {}
    per_paper: dict[str, Counter[str]] = {}
    for arm, carm in ARMS.items():
        p = P[f"chunks_{arm}"]
        if g.sha(p, f"chunks_{arm}") != counts[carm]["chunks_sha256"]:
            raise SystemExit(f"{p.name}: sha256 != counts-v4.json")
        cp = _chunk_papers(_iter_jsonl(g.check(p)))
        chunk_paper[arm] = cp
        per_paper[arm] = Counter(cp.values())
        if len(per_paper[arm]) != counts[carm]["papers"] or len(cp) != counts[carm]["chunks"]:
            raise SystemExit(f"{carm}: papers / chunks != counts-v4.json")

    # the neighbour arm: attribution and sources
    attribution: dict[str, str] = {}
    nb_of: dict[str, list[str]] = {s: [] for s in study}
    src: dict[str, Counter[str]] = {a: Counter() for a in ARMS}
    src_by_study: dict[str, dict[str, set[str]]] = {s: {a: set() for a in ARMS} for s in study}
    have_nb = set(per_paper["neighbour"])
    for s in study:
        for wid, _side in sel["attributed"][s]:
            key = work_key(wid, sel["works"], have_nb)
            if key in attribution:
                raise SystemExit("an added neighbour is attributed to two study papers")
            attribution[key] = s
            nb_of[s].append(key)
            so = source_of(wid, key, sel["works"])
            src["neighbour"][so] += 1
            src_by_study[s]["neighbour"].add(so)
    if set(attribution) != have_nb:
        raise SystemExit("the attributed neighbours are not the neighbour chunk file's papers")
    _expect("neighbour_papers", len(attribution))
    _expect("neighbour_jats", src["neighbour"]["jats"])
    _expect("neighbour_pdf", src["neighbour"]["pdf"])
    _expect("study_papers_without_neighbours", sum(not v for v in nb_of.values()))

    # the random draws
    takes: dict[str, dict[str, list[str]]] = {}
    for arm, d in draws.items():
        have = set(per_paper[arm])
        takes[arm] = {s: [] for s in study}
        seen: set[str] = set()
        for s in study:
            for t in (d["per_study"].get(s) or {}).get("took") or []:
                key = work_key(t["wid"], d["works"], have)
                so = source_of(t["wid"], key, d["works"])
                if so != t.get("source"):
                    raise SystemExit(f"{arm}: a take's recorded source is not its chunk-file key's")
                if key in seen:
                    raise SystemExit(f"{arm}: a paper is taken twice")
                seen.add(key)
                takes[arm][s].append(key)
                src[arm][so] += 1
                src_by_study[s][arm].add(so)
        if seen != have:
            raise SystemExit(f"{arm}: the draw's takes are not the chunk file's papers")
        _expect("random_papers", len(seen))
        pdf_share = round(src[arm]["pdf"] / len(seen), 4)
        if pdf_share != counts[ARMS[arm]]["pdf_share"]:
            raise SystemExit(f"{arm}: PDF share {pdf_share} != counts-v4.json")

    # added chunks per study paper, checked against per-study-chunks-v4.json
    added: dict[str, dict[str, int]] = {}
    for s in study:
        added[s] = {"neighbour": sum(per_paper["neighbour"][k] for k in nb_of[s])}
        for arm in ("random1", "random2"):
            added[s][arm] = sum(per_paper[arm][k] for k in takes[arm][s])
        for arm, carm in ARMS.items():
            listed = (psc.get(carm) or {}).get(s)
            if listed is not None and int(listed) != added[s][arm]:
                raise SystemExit(f"{carm}: added chunks of a study paper != per-study-chunks-v4.json")
            if listed is None and added[s][arm] != 0:
                raise SystemExit(f"{carm}: a study paper absent from per-study-chunks-v4.json has added chunks")

    # text similarity from the stored vectors (§7 R6)
    if g.sha(base_export, "base_export") != BASE_EXPORT_SHA256:
        raise SystemExit("base export sha256 != the prereg's (§4.1)")
    base_cp = _chunk_papers(_iter_jsonl(g.check(base_export)))
    study_set = set(study)
    study_chunks = Counter(p for p in base_cp.values() if p in study_set)
    if set(study_chunks) != study_set:
        raise SystemExit("a study paper has no chunk in the base export")
    emb_files: dict[str, list[Path]] = {}
    for arm, listed in g.emb_files(study_dir).items():
        for f, want in listed:
            if g.sha(f, f"emb_{arm}/{f.name}") != want:
                raise SystemExit(f"{EMB_DIRS[arm]}/{f.name}: sha256 != its SHA256SUMS")
        emb_files[arm] = [g.check(f) for f, _ in listed]
    s_sum, s_cnt, s_ids = embedding_sums(emb_files["base"], set(study), workers)
    if s_ids != base_cp:
        raise SystemExit("the base embeddings' chunk ids / papers are not the base export's")
    if dict(s_cnt) != dict(study_chunks):
        raise SystemExit("study-paper chunk counts differ between the base embeddings and the export")
    n_sum, n_cnt, n_ids = embedding_sums(emb_files["neighbour"], None, workers)
    if n_ids != chunk_paper["neighbour"]:
        raise SystemExit("the neighbour embeddings' chunk ids / papers are not the neighbour chunk file's")
    sim: dict[str, float | None] = {}
    dim = len(next(iter(s_sum.values())))
    for s in study:
        nb_vec = np.zeros(dim)
        n_nb = 0
        for k in nb_of[s]:
            nb_vec = nb_vec + n_sum[k]
            n_nb += n_cnt[k]
        sim[s] = mean_pair_cosine(s_sum[s], s_cnt[s], nb_vec, n_nb)

    # covariates
    cov: dict[str, dict[str, Any]] = {}
    for s in study:
        w = sel["study_works"][s]
        sources = set().union(*src_by_study[s].values())
        cov[s] = {"topic": w["topic"], "citation_count": int(w["cited_by_count"]), "text_similarity": sim[s],
                  "xml_only": sources <= {"jats"}, "attributed_neighbours": len(nb_of[s]),
                  "study_paper_chunks": int(s_cnt[s])}
        # §7 (R23): x over the study papers with added papers (the prereg's 298); a paper with none
        # carries no added_chunks, and the statistics leave it out (papers_left_out)
        if nb_of[s]:
            cov[s]["added_chunks"] = added[s]
        if not w.get("topic") or w.get("cited_by_count") is None:
            raise SystemExit("a study work lacks its topic or citation count")

    cit = [c["citation_count"] for c in cov.values()]
    simv = [c["text_similarity"] for c in cov.values() if c["text_similarity"] is not None]
    cuts = {"citation_count": tercile_cuts(cit), "text_similarity": tercile_cuts(simv)}
    xs = np.asarray([c["added_chunks"]["neighbour"] - 0.5 * (c["added_chunks"]["random1"]
                     + c["added_chunks"]["random2"]) for c in cov.values() if "added_chunks" in c], float)
    citer = {s: int(sel["per_study"][s]["citer_list"]["cited_by_count"]) for s in study}
    summary = {
        "study_papers": len(study), "topics": len({c["topic"] for c in cov.values()}),
        "papers_with_text_similarity": len(simv), "papers_without_text_similarity": len(study) - len(simv),
        "xml_only_papers": sum(c["xml_only"] for c in cov.values()),
        "xml_only_papers_without_added_papers": sum(c["xml_only"] and c["attributed_neighbours"] == 0
                                                    for c in cov.values()),
        "neighbour_attributions": len(attribution),
        "sources": {a: dict(sorted(c.items())) for a, c in src.items()},
        "tercile_sizes": {"citation_count": tercile_sizes(cit, cuts["citation_count"]),
                          "text_similarity": tercile_sizes(simv, cuts["text_similarity"])},
        "papers_with_added_chunks": int(xs.size),
        "volume_covariate_x": {"papers": int(xs.size), "mean": round(float(xs.mean()), 4),
                               "sd": round(float(xs.std(ddof=1)), 4), "mean_over_sd": _ratio(list(xs))},
        "open_choice_checks": open_choice_checks(cit, simv, cuts, [citer[s] for s in study]),
        "sensitivity_sets": {k: len(v) for k, v in sets.items()},
        "validation_papers_balanced_records": sum(str(r["paper"]) in vset for r in bal),
    }
    return {"covariates": cov, "attribution": dict(sorted(attribution.items())), "cuts": cuts, "sets": sets,
            "summary": summary, "sources_sha256": dict(sorted(g.read.items())),
            "forbidden": set(study) | {str(r["record_id"]) for r in records} | set(attribution)
            | set(base_cp) | set(chunk_paper["neighbour"])}


# ------------------------------------------------------------------ outputs

def provenance() -> dict[str, Any]:
    from ragstack.provenance import experiment_provenance
    return experiment_provenance(repo=str(REPO))


def public_copy(obj: Any, _in_prov: bool = False) -> Any:
    if isinstance(obj, Mapping):
        return {k: public_copy(v, k == "provenance") for k, v in obj.items()
                if not (_in_prov and k in ("host", "warnings"))}
    if isinstance(obj, list):
        return [public_copy(v) for v in obj]
    return obj


def _dump(obj: Any) -> bytes:
    return (json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _write(path: Path, data: bytes, mode: int = 0o600) -> str:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.chmod(path, mode)
    return hashlib.sha256(data).hexdigest()


META_KEYS = ("schema", "provenance", "definitions", "deviation", "sources_sha256")


def outputs(res: Mapping[str, Any], prov: Mapping[str, Any]) -> dict[str, bytes]:
    """File name -> bytes. The JSON inputs are flat maps (the statistics read ``cov.get(paper)``,
    ``cuts.get(variable)``, ``attribution.get(paper)``) with the :data:`META_KEYS` beside the ids."""
    for k in META_KEYS:
        if k in res["covariates"] or k in res["attribution"]:
            raise SystemExit(f"an id equals the metadata key {k!r}")
    src = res["sources_sha256"]
    meta = {"schema": SCHEMA, "provenance": dict(prov), "deviation": DEVIATION}
    cov = {**res["covariates"], **meta, "definitions": {k: DEFINITIONS[k] for k in
                                                        ("study_papers", "topic", "citation_count", "text_similarity",
                                                         "xml_only", "added_chunks")},
           "sources_sha256": src}
    attr = {**res["attribution"], **meta, "definitions": {"neighbour_attribution": DEFINITIONS["neighbour_attribution"]},
            "sources_sha256": {k: v for k, v in src.items() if k in ("neighbour_selection", "chunks_neighbour", "counts")}}
    cuts_pub = public_copy({
        **res["cuts"], **meta, "rule": "T1: v <= c1; T2: c1 < v <= c2; T3: v > c2 (alias_study_stats.TERCILE_RULE)",
        "definitions": {k: DEFINITIONS[k] for k in ("tercile_cuts", "citation_count", "text_similarity")},
        "counts": {"study_papers": res["summary"]["study_papers"],
                   "papers_with_value": {"citation_count": res["summary"]["study_papers"],
                                         "text_similarity": res["summary"]["papers_with_text_similarity"]},
                   "tercile_sizes": res["summary"]["tercile_sizes"],
                   "open_choice_checks": res["summary"]["open_choice_checks"]},
        "sources_sha256": {k: v for k, v in src.items() if k in PUBLIC_SOURCES}})
    files = {"paper-covariates.json": _dump(cov), "neighbour-attribution.json": _dump(attr),
             "tercile-cuts.json": _dump(cuts_pub)}
    for name, ids in res["sets"].items():
        files[name.replace("_", "-") + ".txt"] = "".join(f"{i}\n" for i in ids).encode("utf-8")
    return files


def write_all(res: Mapping[str, Any], study_dir: Path, public_cuts: Path | None, prov: Mapping[str, Any]) -> dict[str, Any]:
    out = study_dir / OUT_DIR
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{OUT_DIR} is not empty; outputs are written once")
    files = outputs(res, prov)
    probs = asc.public_problems(json.loads(files["tercile-cuts.json"]), res["forbidden"])
    if probs:
        raise SystemExit(f"the public tercile-cut file fails the §11 check: {sorted(set(probs))}")
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(out, 0o700)
    shas = {name: _write(out / name, data) for name, data in files.items()}
    keys = {"paper_covariates": str(out / "paper-covariates.json"),
            "tercile_cuts": PUBLIC_CUTS_REL,
            "neighbour_attribution": str(out / "neighbour-attribution.json"),
            "sensitivity_sets": {k: str(out / (k.replace("_", "-") + ".txt")) for k in res["sets"]}}
    shas["stats-config-keys.json"] = _write(out / "stats-config-keys.json", _dump(keys))
    manifest = {"schema": SCHEMA, "provenance": dict(prov), "deviation": DEVIATION, "definitions": DEFINITIONS,
                "sources_sha256": res["sources_sha256"], "counts": res["summary"], "outputs_sha256": dict(shas),
                "never_read": list(DENIED)}
    shas["inputs-manifest.json"] = _write(out / "inputs-manifest.json", _dump(manifest))
    _write(out / "SHA256SUMS", "".join(f"{s}  {n}\n" for n, s in sorted(shas.items())).encode("utf-8"))
    if public_cuts is not None:
        public_cuts.parent.mkdir(parents=True, exist_ok=True)
        public_cuts.write_bytes(files["tercile-cuts.json"])
    return {"out_dir": str(out), "outputs_sha256": shas, "counts": res["summary"]}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--study-dir", required=True, type=Path)
    ap.add_argument("--base-export", required=True, type=Path)
    ap.add_argument("--public-cuts", type=Path, default=None)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--allow-dirty", action="store_true", help="tests only: write from a dirty tree")
    a = ap.parse_args(argv)
    prov = provenance()
    if not prov.get("citable") and not a.allow_dirty:
        raise SystemExit("provenance is not citable (dirty or untracked tree); run from a clean checkout")
    res = build(a.study_dir, a.base_export, workers=a.workers)
    rep = write_all(res, a.study_dir, a.public_cuts, prov)
    print(json.dumps({"out_dir": rep["out_dir"], "outputs_sha256": rep["outputs_sha256"], "counts": rep["counts"]},
                     indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
