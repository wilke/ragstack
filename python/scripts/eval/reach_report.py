#!/usr/bin/env python
"""The reach comparison (``docs/plans/results/reach-rechunk-2026-11/PREREG.md``): the J on-demand
builder and the report. W = the alias study's fixed 512-token windows, S = the adopted chunking.

    reach_report.py j-seeds --records ACCEPTED.jsonl --w-runs W_RUNS --s-runs S_RUNS \\
        --w-export ARM=PATH [...] --s-export ARM=PATH [...] --w-final FINAL.jsonl [...] \\
        [--s-prior-final FINAL.jsonl ... [--s-prior-pairs PAIRS.jsonl ...]] --main-pairs MAIN_JUDGE/pairs.jsonl \\
        --out-dir PRIVATE_DIR
    reach_report.py report --records ACCEPTED.jsonl --w-runs W_RUNS --s-runs S_RUNS \\
        --w-export ARM=PATH [...] --s-export ARM=PATH [...] --w-final FINAL.jsonl [...] \\
        --s-final FINAL.jsonl [...] [--s-prior-final FINAL.jsonl ... [--s-prior-pairs PAIRS.jsonl ...]] \\
        [--drift-final FINAL.jsonl ...] --main-pairs MAIN_JUDGE/pairs.jsonl --build-summary BUILD.json \\
        [--power-json power.json] --prereg PREREG.md --prereg-sha256 SHA [--tokenizer NAME|none] --out PUBLIC.json

**Inputs.** ``W_RUNS`` / ``S_RUNS``: run directories in ``alias_study_run.py``'s layout, read as
``<dir>/<corpus>/none.jsonl`` and ``none.summary.json`` for the four corpus arms (W: the alias main
run's stage-A legs; S: the reach run, ``rechunk: {all_records: true}``). ``--w-export`` /
``--s-export``: the chunk exports, ``ARM=PATH`` repeatable (W: the base export plus each arm's added
papers; S: the image-derived exports the S collections were loaded from); a chunk's text is looked
up by id within its side, and one id with two contents on a side is refused. ``--w-final``: the alias
main judge registries' ``final.jsonl`` (stage A and B rows); ``--s-prior-final``: earlier registries
of S chunks (the confirmatory control, cover-any, stage-1b runs), reused by (record, chunk) key;
``--s-final``: the reach run's own S registry (``build-control --any-paper`` on the seeds below);
``--drift-final``: the W re-judge registry. ``--s-prior-pairs``: the prior registries' ``pairs.jsonl``;
when given, every reused prior key's ``content_sha256`` must equal the sha256 of the S export's
text of that chunk (else refused: a prior label of other bytes is not reused). The judge dirs are
the directories of the ``final.jsonl`` files: the drift check reads the Qwen label rows
(``labels-qwen-C.jsonl``, ``labels-qwen-E.jsonl``) of the ``--w-final`` and ``--drift-final``
dirs. Every input is read, never written.

**quote@10 (Q; PREREG §3).** ``reach_quote.quote_hit`` on the texts of the list's top 10, the same
function on both sides. Eligible: the records whose quote is verbatim in their W gold chunk after
whitespace normalisation (``reach_quote.is_verbatim``; PREREG §2, §11 Q4: 1,089 of 1,091).

**judged found@10 (J; PREREG §3).** quote@10, **or** a top-10 chunk labelled *answers* for the
record. Every record is in J (the 2 non-verbatim records too). Labels:

- W: the ``--w-final`` rows, keyed (record, chunk); one key in several rows takes
  ``rechunk_prep.combine`` (answers if any says answers).
- S, in this order: (1) a judged (*answers* / *not*) ``--s-prior-final`` label of the key; (2) the
  **content-hash rule**: the S chunk's text is byte-identical (sha256 of the content) to a W chunk
  of the same record that carries a judged W label (several: combined); (3) the ``--s-final``
  label. A key labelled *unjudged* (or never) counts as not answering and as **unjudged**: so does
  an **orphan** (a pair whose record has no stage-A pair in ``--main-pairs``, so ``build-control``
  cannot take it; PREREG §3). The report prints the **favourable bound** beside the primary: J
  recomputed with every unjudged or orphan S pair scored *answers*; when the bound's outcome
  differs from the primary's, the J sentence says so (PREREG §7.2).

The pairs J needs are the top-10 (record, chunk) pairs of every list **without** a quote hit
(failed calls excluded), all three retrievers (§11 Q3), unique per side.

**j-seeds** writes, into ``--out-dir`` (private, outside the repository, write-once):

- ``s-seed.jsonl`` + ``s-export.jsonl``: one seed per S pair J needs that has no judged label by
  (1) or (2), in ``rechunk_prep.stage1b_seeds``' row shape (``control_of``, ``record_id``,
  ``chunk_id`` and its companions; ``stage`` = ``reach-S``) for ``alias_study_judge.py
  build-control --any-paper --seed-pairs``; ``control_of`` is a stage-A pair of the record from the
  main registry (``--main-pairs``), the first by pair id among those whose passage paper is the
  chunk's paper, else the first by pair id; and the export rows of exactly those chunks (the control
  export of that registry's config).
- ``drift-seed.jsonl`` + ``drift-export.jsonl``: the judge drift sample (§3): of the W pairs J uses
  (unique (record, chunk) over every W list without a quote hit), the first
  ``round_half_up(0.05 N)`` in ``sha256('261109|reach|drift|W|<record>|<chunk>')`` order, as seeds
  for a separate ``build-control --any-paper`` registry over W chunks (``control_of`` = the pair
  itself when it is a stage-A pair, else as above), with their W export rows.
- ``orphans.jsonl``: any seed whose record has no stage-A pair in the main registry
  (``build-control`` cannot take it; counted, never dropped silently).
- ``j-seeds.json``: counts and shas only (``alias_control_chunks.write_summary``). It holds no
  quote@10 or found@10 level, but its load counts (the S pairs J needs, about 10 per S list without
  a quote hit) reveal roughly how many S lists miss the quote, i.e. about Q's S level, before J is
  judged. PREREG §6 declares this; no discretionary step follows it.

**report** (``reach-report/1``, public, counts only, written through
``alias_control_chunks.write_public``): per family (Q, J) the primary test (§4: default retriever,
the four corpora pooled, records clustered by paper; the exact sign-flip over papers on
``X_k = 100 T_k + m_k δ`` with δ = 3 points, one-sided α = 0.05, by ``power.py``'s
``sign_flip_p_exact``, imported from the committed file; then superiority on ``T_k``, hierarchical),
the decision and its frozen sentence (PREREG §7.2, :func:`decide`); the secondary figures (per corpus,
per retriever, G vs C, context tokens per query, discordant units by the S coverage of the gold
window); the gates (§7.1); the judge drift summary (agreement, the prompt-identity check and the
Qwen cache check of ``alias_control_chunks.cache_check``); provenance and input shas. ``report``
requires ``--prereg PREREG.md --prereg-sha256 SHA`` and refuses unless the file hashes to the sha
and the sha is this checkout's committed ``PREREG.md``.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE / "glossbench", _HERE.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import alias_study_common as asc  # noqa: E402
from reach_quote import HIT_THRESHOLD, MIN_MATCH, is_verbatim, quote_hit  # noqa: E402

SCHEMA = {"report": "reach-report/1", "seeds": "reach-j-seeds/1"}
CORPORA = asc.CORPUS_ARMS
#: prereg retriever name -> the run's condition name
RETRIEVERS: dict[str, str] = dict(asc.RETRIEVERS)
PRIMARY_RETRIEVER = "default"
TOP_K = asc.FOUND_K
DELTA_PTS = 3
ALPHA = 0.05
DRIFT_SEED = "261109|reach|drift|W"
DRIFT_SHARE = 0.05
DRIFT_AGREE_MIN = 0.90
UNJUDGED_MAX = 0.05
#: below this observed discordance a rejection carries the size caveat (PREREG §4: size is close to
#: alpha only at the top of the simulated grid, 10 %)
LOW_DISCORDANCE = 0.10
EXPECTED_RECORDS = 1091
EXPECTED_ELIGIBLE = 1089
S_STAGE = "reach-S"
DRIFT_STAGE = "reach-drift-W"
GATE = asc.GATE
FAMILIES = ("Q", "J")
OUTCOMES = ("superior", "non_inferior", "ni_not_shown", "gate_not_evaluable")
POWER_PY = _HERE.parents[2] / "docs" / "plans" / "results" / "reach-rechunk-2026-11" / "power.py"
#: PREREG §9: ``report`` refuses unless the given prereg's sha is this file's (read at call time)
PREREG_FILE = POWER_PY.with_name("PREREG.md")
#: PREREG §3 / §7.1: the Qwen backends whose label rows the drift check compares
QWEN_BACKENDS = ("C", "E")


# ------------------------------------------------------------------ the shared sign-flip

def load_power() -> Any:
    """``power.py`` (the prereg's committed power script, standard library only), imported from its
    file: its :func:`sign_flip_p_exact` is the analysis's p (PREREG §4)."""
    spec = importlib.util.spec_from_file_location("reach_power", POWER_PY)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot import {POWER_PY.name}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_POWER: Any = None


def sign_flip_p_exact(values: Sequence[int]) -> float:
    global _POWER
    if _POWER is None:
        _POWER = load_power()
    return float(_POWER.sign_flip_p_exact(list(values)))


# ------------------------------------------------------------------ inputs

def read_rows(path: Path) -> list[dict[str, Any]]:
    import rechunk_prep as rp

    return rp.read_rows(path)


def parse_exports(specs: Sequence[str]) -> dict[str, list[Path]]:
    """``ARM=PATH`` (repeatable) -> ``arm -> [paths]``."""
    out: dict[str, list[Path]] = defaultdict(list)
    for s in specs:
        arm, sep, path = s.partition("=")
        if not sep or arm not in CORPORA or not path:
            raise SystemExit(f"--*-export {s!r}: ARM=PATH with ARM one of {list(CORPORA)}")
        out[arm].append(Path(path))
    return dict(out)


def export_files(spec: Mapping[str, Sequence[Path]]) -> list[Path]:
    seen: list[Path] = []
    for arm in CORPORA:
        for p in spec.get(arm, []):
            if p not in seen:
                seen.append(p)
    return seen


@dataclass
class Chunk:
    content: str
    doc_id: str
    start: int
    end: int
    paper: str


@dataclass
class Export:
    """The rows of one side's exports for the wanted ids, the spans of the wanted documents, and
    the files' shas."""
    chunks: dict[str, Chunk]
    spans: dict[str, list[tuple[int, int]]]
    sha256: dict[str, str]


def read_export(spec: Mapping[str, Sequence[Path]], want: set[str], docs: Iterable[str] = ()) -> Export:
    """Stream the exports once: the wanted chunks (content, doc, span, paper) and every span of the
    documents in ``docs``. One id with two contents is refused."""
    import _glossbench_r2 as g

    docs = set(docs)
    chunks: dict[str, Chunk] = {}
    spans: dict[str, set[tuple[int, int]]] = defaultdict(set)
    shas = {}
    for path in export_files(spec):
        shas[path.name] = asc.sha256_file(path)
        with g._open_text(path) as fh:
            for i, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                row = g.parse_chunk(json.loads(line), f"{path.name}:{i}")
                if row.doc_id in docs:
                    spans[row.doc_id].add((row.start_char, row.end_char))
                if row.chunk_id not in want:
                    continue
                prev = chunks.get(row.chunk_id)
                if prev is not None:
                    if prev.content != row.content:
                        raise SystemExit(f"{path.name}:{i}: chunk {row.chunk_id[:8]}… seen with other content")
                    continue
                chunks[row.chunk_id] = Chunk(row.content, row.doc_id, row.start_char, row.end_char,
                                             g.paper_of(row, "path_root"))
    return Export(chunks, {d: sorted(v) for d, v in spans.items()}, shas)


@dataclass
class Runs:
    """One side's run rows ``(corpus, record, condition) -> row`` and summaries ``corpus -> summary``."""
    rows: dict[tuple[str, str, str], dict[str, Any]]
    summaries: dict[str, dict[str, Any]]
    sha256: dict[str, str]
    duplicates: int = 0


def read_runs(d: Path) -> Runs:
    rows: dict[tuple[str, str, str], dict[str, Any]] = {}
    summaries: dict[str, dict[str, Any]] = {}
    shas: dict[str, str] = {}
    dup = 0
    for c in CORPORA:
        rp_, sp = d / c / "none.jsonl", d / c / "none.summary.json"
        if not rp_.is_file() or not sp.is_file():
            raise SystemExit(f"REFUSED: no run files for the {c} corpus arm ({rp_.parent.name}/none.*)")
        shas[f"{c}/none.jsonl"] = asc.sha256_file(rp_)
        shas[f"{c}/none.summary.json"] = asc.sha256_file(sp)
        summaries[c] = json.loads(sp.read_text(encoding="utf-8"))
        for r in read_rows(rp_):
            key = (c, str(r["record_id"]), str(r["condition"]))
            if key in rows:
                dup += 1
            rows[key] = r
    return Runs(rows, summaries, shas, dup)


def top_ids(runs: Runs, k: int = TOP_K) -> set[str]:
    return {cid for r in runs.rows.values() if not r.get("failed") for cid in (r.get("ranked_chunk_ids") or [])[:k]}


# ------------------------------------------------------------------ labels

@dataclass
class Registry:
    """``(record, chunk) -> label word`` (``answers`` / ``not`` / ``unjudged``) of a set of
    ``final.jsonl`` files, and the integrity problems found reading them."""
    labels: dict[tuple[str, str], str]
    problems: list[str]
    rows: int
    sha256: list[str]


def read_registry(paths: Sequence[Path], what: str) -> Registry:
    """Batch rows (``k`` starting ``batch|``) are skipped. Every other row must carry
    ``record_id`` and ``chunk_id``, and its ``pair_id`` (when present) must be
    ``sha256(record|chunk)[:16]``; one key with two different judged labels in this set is a
    conflict. Problems are returned (they are gates), never repaired."""
    import alias_study_judge as aj
    import rechunk_prep as rp

    per: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    problems: Counter[str] = Counter()
    n = 0
    for fi, path in enumerate(paths):
        for j, r in enumerate(read_rows(path)):
            k = str(r.get("k", ""))
            if k.startswith("batch|"):
                continue
            rid, cid = r.get("record_id"), r.get("chunk_id")
            if not rid or not cid:
                problems[f"{what}: a row without record_id/chunk_id"] += 1
                continue
            if r.get("pair_id") is not None and r["pair_id"] != aj.pair_id(str(rid), str(cid)):
                problems[f"{what}: a pair_id that is not sha256(record|chunk)[:16]"] += 1
                continue
            w = rp.label_word(r)
            assert w is not None
            per[(str(rid), str(cid))][f"{fi}:{j}"] = w
            n += 1
    labels: dict[tuple[str, str], str] = {}
    for key, ws in per.items():
        judged = {v for v in ws.values() if v != "unjudged"}
        if len(judged) > 1:
            problems[f"{what}: one (record, chunk) with conflicting labels"] += 1
        labels[key] = rp.combine(ws)
    return Registry(labels, [f"{p} ({c})" for p, c in sorted(problems.items())], n,
                    [asc.sha256_file(p) for p in paths])


def judged(word: str | None) -> bool:
    return word in ("answers", "not")


def sha_content(text: str) -> str:
    return asc.sha_text(text)


def w_hash_index(w_labels: Mapping[tuple[str, str], str], w_texts: Mapping[str, Chunk]
                 ) -> dict[tuple[str, str], str]:
    """``(record, sha256(content)) -> combined judged W label``: the content-hash rule's index."""
    import rechunk_prep as rp

    per: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    for (rid, cid), w in w_labels.items():
        if judged(w) and cid in w_texts:
            per[(rid, sha_content(w_texts[cid].content))][cid] = w
    return {k: rp.combine(v) for k, v in per.items()}


@dataclass
class SLabels:
    """S labels by source: prior registries (by key), the content-hash rule, the new registry."""
    prior: Registry
    new: Registry | None
    hash_index: dict[tuple[str, str], str]
    texts: Mapping[str, Chunk]
    hash_prior_disagree: int = 0

    def pre_judging(self, rid: str, cid: str) -> tuple[str | None, str | None]:
        """The label available before the reach judging: ``(word, source)``."""
        w = self.prior.labels.get((rid, cid))
        if judged(w):
            return w, "prior"
        ch = self.texts.get(cid)
        if ch is not None:
            h = self.hash_index.get((rid, sha_content(ch.content)))
            if h is not None:
                return h, "w_content_hash"
        return w, ("prior" if w is not None else None)

    def label(self, rid: str, cid: str) -> tuple[str | None, str | None]:
        w, src = self.pre_judging(rid, cid)
        if judged(w):
            return w, src
        if self.new is not None and (rid, cid) in self.new.labels:
            return self.new.labels[(rid, cid)], "new"
        return w, src

    def integrity(self) -> list[str]:
        """The stage-1b guard (PREREG §7.1): the new registry may only add labels, never relabel a
        key that already had a judged label (by key or by the content-hash rule)."""
        if self.new is None:
            return []
        relabel = 0
        for rid, cid in self.new.labels:
            w, _ = self.pre_judging(rid, cid)
            relabel += int(judged(w))
        return [f"S registry: {relabel} label(s) for (record, chunk) keys that already had a judged label "
                "(prior registry or content-hash rule); the new registry may only add labels"] if relabel else []


def check_prior_content(sl: SLabels, keys: Iterable[tuple[str, str]],
                        prior_pairs: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    """PREREG §3 (the reuse rule's integrity): every needed S key whose label is reused from a
    prior registry must have been judged on the bytes the S export holds now. With the prior
    registries' ``pairs.jsonl`` rows given, a reused key whose ``content_sha256`` differs from
    sha256 of the S export's text of that chunk is **refused** (nothing is reused across other
    bytes); a reused key with no pairs row is counted. Without pairs rows: not checked."""
    if prior_pairs is None:
        return {"given": False}
    sha_of: dict[tuple[str, str], set[str]] = defaultdict(set)
    for p in prior_pairs:
        if p.get("record_id") and p.get("chunk_id") and p.get("content_sha256"):
            sha_of[(str(p["record_id"]), str(p["chunk_id"]))].add(str(p["content_sha256"]))
    checked = without = bad = 0
    for key in sorted(set(keys)):
        w, src = sl.pre_judging(*key)
        if not (judged(w) and src == "prior"):
            continue
        shas = sha_of.get(key)
        if not shas:
            without += 1
            continue
        ch = sl.texts.get(key[1])
        if ch is None or shas != {sha_content(ch.content)}:
            bad += 1
            continue
        checked += 1
    if bad:
        raise SystemExit(f"REFUSED: {bad} reused prior S label(s) whose prior pairs.jsonl content_sha256 is not the "
                         "sha256 of the S export's text of that chunk; a label of other bytes is not reused")
    return {"given": True, "reused_keys_checked": checked, "reused_keys_without_pairs_row": without}


# ------------------------------------------------------------------ scoring

@dataclass
class ListScore:
    missing: bool = False
    failed: bool = False
    top: list[str] = field(default_factory=list)
    hit: bool = False
    unresolved: int = 0


def score_lists(records: Sequence[Mapping[str, Any]], runs: Runs, texts: Mapping[str, Chunk]
                ) -> dict[tuple[str, str, str], ListScore]:
    """``(record, corpus, retriever) -> ListScore``: the top 10 and quote@10 of every list."""
    out: dict[tuple[str, str, str], ListScore] = {}
    for rec in records:
        rid = str(rec["record_id"])
        for c in CORPORA:
            for ret, cond in RETRIEVERS.items():
                row = runs.rows.get((c, rid, cond))
                if row is None:
                    out[(rid, c, ret)] = ListScore(missing=True)
                    continue
                if row.get("failed") or row.get("ranked_chunk_ids") is None:
                    out[(rid, c, ret)] = ListScore(failed=True)
                    continue
                top = [str(x) for x in row["ranked_chunk_ids"][:TOP_K]]
                tx = [texts[x].content for x in top if x in texts]
                out[(rid, c, ret)] = ListScore(top=top, hit=quote_hit(str(rec["quote"]), tx),
                                               unresolved=len(top) - len(tx))
    return out


def needed_pairs(scores: Mapping[tuple[str, str, str], ListScore], retrievers: Iterable[str] | None = None
                 ) -> dict[tuple[str, str], list[tuple[str, str, int]]]:
    """``(record, chunk) -> [(corpus, retriever, rank), …]``: every top-10 slot of a list without a
    quote hit (failed and missing lists excluded): the pairs J needs a label for."""
    keep = set(retrievers or RETRIEVERS)
    out: dict[tuple[str, str], list[tuple[str, str, int]]] = defaultdict(list)
    for (rid, c, ret), s in sorted(scores.items()):
        if ret not in keep or s.missing or s.failed or s.hit:
            continue
        for rank, cid in enumerate(s.top, 1):
            out[(rid, cid)].append((c, ret, rank))
    return dict(out)


def round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def drift_sample(pairs: Iterable[tuple[str, str]], share: float | None = None, seed: str = DRIFT_SEED
                 ) -> list[tuple[str, str]]:
    """PREREG §3: the first ``round_half_up(share * N)`` of the pairs in
    ``sha256('<seed>|<record>|<chunk>')`` order (ties by the pair); ``share`` defaults to
    :data:`DRIFT_SHARE`."""
    share = DRIFT_SHARE if share is None else share
    ps = sorted(set(pairs), key=lambda p: (asc.seeded_key(seed, p[0], p[1]), p))
    return ps[:round_half_up(share * len(ps))]


def eligible_records(records: Sequence[Mapping[str, Any]], w_texts: Mapping[str, Chunk]) -> set[str]:
    """§2 / §11 Q4: the quote is verbatim in the W gold chunk after whitespace normalisation."""
    out = set()
    for r in records:
        g = w_texts.get(str(r["gold_chunks"][0]))
        if g is not None and is_verbatim(str(r["quote"]), g.content):
            out.add(str(r["record_id"]))
    return out


# ------------------------------------------------------------------ the test

def rate(k: int, n: int) -> dict[str, Any]:
    return {"k": k, "n": n, "rate": round(k / n, 4) if n else None}


def paired(units: Sequence[tuple[str, str, int, int]]) -> dict[str, Any]:
    """Counts of paired units ``(record, paper, s, w)``: S and W rates, the difference in points,
    S-only and W-only units, the discordance."""
    n = len(units)
    s = sum(u[2] for u in units)
    w = sum(u[3] for u in units)
    plus = sum(u[2] > u[3] for u in units)
    minus = sum(u[2] < u[3] for u in units)
    return {"units": n, "records": len({u[0] for u in units}), "papers": len({u[1] for u in units}),
            "S": rate(s, n), "W": rate(w, n), "D_pts": round(100 * (s - w) / n, 3) if n else None,
            "S_only": plus, "W_only": minus, "discordance": round((plus + minus) / n, 4) if n else None}


def cluster_sums(units: Sequence[tuple[str, str, int, int]]) -> list[tuple[int, int]]:
    """``[(m_k, T_k)]`` per paper: the number of paired differences and their sum."""
    by: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for _rid, paper, s, w in units:
        by[paper][0] += 1
        by[paper][1] += s - w
    return [(m, t) for m, t in by.values()]


def ni_test(units: Sequence[tuple[str, str, int, int]], delta_pts: int = DELTA_PTS, alpha: float = ALPHA
            ) -> dict[str, Any]:
    """PREREG §4: NI by the exact sign-flip over papers on ``X_k = 100 T_k + m_k δ`` (δ in points),
    one-sided; then, only if NI rejects, superiority by the exact sign-flip on ``T_k``."""
    cl = cluster_sums(units)
    x = [100 * t + m * delta_pts for m, t in cl]
    p_ni = sign_flip_p_exact(x) if cl else 1.0
    ni = p_ni <= alpha
    out: dict[str, Any] = {"delta_pts": delta_pts, "alpha_one_sided": alpha, "clusters": len(cl),
                           "ni": {"p": round(p_ni, 6), "reject": ni,
                                  "statistic": "sum_k X_k, X_k = 100 T_k + m_k delta"},
                           "superiority": {"tested": ni, "p": None, "reject": False}}
    if ni:
        p_sup = sign_flip_p_exact([t for _, t in cl])
        out["superiority"] = {"tested": True, "p": round(p_sup, 6), "reject": p_sup <= alpha}
    return out


# ------------------------------------------------------------------ decision wording (PREREG §7.2)

FAMILY_TEXT = {
    "Q": "quote@10 (the top 10 cover at least 90 % of the gold quote's characters)",
    "J": "judged found@10 (quote@10, or a top-10 chunk the frozen alias judge labelled answers)",
}


def size_note(power_json: Mapping[str, Any] | None) -> str:
    """The simulated size of the δ = 3 test from ``power.json`` (``size_cells``), for the caveat."""
    if not power_json:
        return "simulated size: power.json not given"
    cells = [c for c in power_json.get("size_cells") or [] if c.get("delta_pts") == DELTA_PTS]

    def at(disc: float, sizing: bool) -> Any:
        v = [c["size_score"] for c in cells if c["discordance"] == disc and c["size_score"] is not None
             and (not sizing or (c["rho_paper"] == 0.0 and c["rho_record"] == 0.75))]
        return max(v) if v else None
    return (f"simulated size at delta = {DELTA_PTS}: {at(0.10, True)} at 10 % and {at(0.05, True)} at 5 % discordance "
            f"under the measured correlations (up to {at(0.05, False)} over the grid at 5 %); not simulated below "
            "5 %")


def ungated_outcome(res: Mapping[str, Any]) -> str:
    """The outcome the test alone gives (the gates aside)."""
    t = res["test"]
    if t["ni"]["reject"]:
        return "superior" if t["superiority"]["reject"] else "non_inferior"
    return "ni_not_shown"


def decide(family: str, res: Mapping[str, Any], gates: Sequence[str], caveats: Sequence[str],
           bound: Mapping[str, Any] | None = None) -> tuple[str, str]:
    """The outcome and its frozen sentence (PREREG §7.2). ``bound`` (J): the favourable-bound
    recomputation (``counts``, ``test``, ``pairs``: the unjudged and orphan S pairs it scored
    *answers*); when its outcome differs from the test's, the sentence says so."""
    c = res["counts"]
    t = res["test"]
    s, w = c["S"]["rate"], c["W"]["rate"]

    def pct(x: float | None) -> str:
        return "n/a" if x is None else f"{100 * x:.1f} %"
    core = (f"under the default retriever pooled over the four corpus collections, {FAMILY_TEXT[family]} was "
            f"{pct(s)} under S against {pct(w)} under W on {c['units']} paired (record, corpus) units "
            f"({c['records']} records in {c['papers']} papers), a difference of "
            f"{'n/a' if c['D_pts'] is None else format(c['D_pts'], '+.1f')} points ({c['S_only']} units found under "
            f"S only, {c['W_only']} under W only; discordance {pct(c['discordance'])})")
    p_ni, p_sup = t["ni"]["p"], t["superiority"]["p"]
    tail = ""
    if bound is not None and ungated_outcome(bound) != ungated_outcome(res):
        tail += (f" Favourable bound: with the {bound['pairs']} S pair(s) J needs that carry no judged label (unjudged "
                 f"or orphan) scored answers, the outcome would be {ungated_outcome(bound)} (NI p = "
                 f"{bound['test']['ni']['p']}); this decision scores them not answering and rests on that rule.")
    tail += "" if not caveats else " Caveats: " + "; ".join(caveats) + "."
    if gates:
        return "gate_not_evaluable", (f"GATE-NOT-EVALUABLE ({family}): " + "; ".join(gates) + f". The test was computed "
                                      f"(NI p = {p_ni}, n = {c['units']} units) and decides nothing." + tail)
    if t["ni"]["reject"] and t["superiority"]["reject"]:
        return "superior", (f"Superior ({family}): {core}. Non-inferiority at delta = {DELTA_PTS} points holds (exact "
                            f"one-sided sign-flip over papers, p = {p_ni}), and S is superior to W (p = {p_sup}; "
                            "hierarchical, no extra alpha)." + tail)
    if t["ni"]["reject"]:
        return "non_inferior", (f"Non-inferior ({family}): {core}. Non-inferiority at delta = {DELTA_PTS} points holds "
                                f"(exact one-sided sign-flip over papers, p = {p_ni}): S is not worse than W by "
                                f"{DELTA_PTS} points or more. Superiority is not shown (p = {p_sup})." + tail)
    return "ni_not_shown", (f"Non-inferiority not shown ({family}): {core}. The exact one-sided sign-flip over papers "
                            f"gives p = {p_ni} > {ALPHA}: a loss of {DELTA_PTS} points or more under S is not ruled "
                            "out by this test (this is not evidence that S is worse)." + tail)


# ------------------------------------------------------------------ gates (PREREG §7.1)

def retrieval_gates(side: str, runs: Runs, records: Sequence[Mapping[str, Any]]) -> list[str]:
    """A leg with failed calls, a fired depth / reranker gate, a status other than complete, a hard
    stop, a missing or duplicate row."""
    out = []
    want = {str(r["record_id"]) for r in records}
    for c in CORPORA:
        s = runs.summaries[c]
        if s.get("status") != "complete" or s.get("hard_stop"):
            out.append(f"{side} {c}: run status {s.get('status')!r}")
        for cond, v in sorted((s.get("conditions") or {}).items()):
            if (v or {}).get("gate"):
                out.append(f"{side} {c} {asc.CONDITION_TO_RETRIEVER.get(cond, cond)}: a fired gate")
        for ret, cond in RETRIEVERS.items():
            rows = [runs.rows.get((c, rid, cond)) for rid in sorted(want)]
            missing = sum(r is None for r in rows)
            failed = sum(bool(r is not None and (r.get("failed") or r.get("ranked_chunk_ids") is None)) for r in rows)
            if missing:
                out.append(f"{side} {c} {ret}: {missing} record(s) without a row")
            if failed:
                out.append(f"{side} {c} {ret}: {failed} failed call(s)")
    if runs.duplicates:
        out.append(f"{side}: {runs.duplicates} duplicate run row(s)")
    return out


def s_mode_gates(runs: Runs) -> list[str]:
    out = []
    for c in CORPORA:
        st = runs.summaries[c].get("study") or {}
        if st.get("mode") != "rechunk" or st.get("rechunk_scope") != "all_records":
            out.append(f"S {c}: not a reach run (rechunk mode with all_records)")
    return out


def census_gates(runs: Runs, build: Mapping[str, Any] | None) -> tuple[list[str], dict[str, Any]]:
    """PREREG §7.1: each S collection's census (the run summary's live census before and after, the
    load receipt's chunk count and sha, the collection name) against ``build-summary.json``."""
    if not build:
        return ["census: no build summary given"], {}
    out = []
    info: dict[str, Any] = {}
    cols = build.get("collections") or {}
    for c in CORPORA:
        b = cols.get(c)
        s = runs.summaries[c]
        if not b:
            out.append(f"census {c}: not in the build summary")
            continue
        cen = s.get("census") or {}
        cb = (s.get("study") or {}).get("collection_build") or cen.get("collection_build") or {}
        want = {"total": int((b.get("census") or {}).get("total", -1)),
                "stamps": dict((b.get("census") or {}).get("stamps") or {})}
        checks = {
            "collection": s.get("collection") == b.get("collection"),
            "n_chunks": cb.get("n_chunks") == b.get("chunks_loaded") == want["total"],
            "load_receipt_sha256": cb.get("load_receipt_sha256") == b.get("load_receipt_sha256"),
            "live_before": _census_eq(cen.get("live_before"), want),
            "live_after": _census_eq(cen.get("live_after"), want),
        }
        info[c] = {"chunks": want["total"], **checks}
        bad = [k for k, ok in checks.items() if not ok]
        if bad:
            out.append(f"census {c}: differs from the build summary ({', '.join(bad)})")
    return out, info


def _census_eq(live: Any, want: Mapping[str, Any]) -> bool:
    if not isinstance(live, Mapping):
        return False
    return int(live.get("total", -2)) == want["total"] and dict(live.get("stamps") or {}) == want["stamps"]


# ------------------------------------------------------------------ the report

TokenCounter = Callable[[str], int]


def hf_token_counter(name: str) -> TokenCounter:
    """The SFR tokenizer from the local cache only (no download): tokens without specials."""
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(name, local_files_only=True)
    return lambda text: len(tok.encode(text, add_special_tokens=False))


@dataclass
class Inputs:
    records: list[dict[str, Any]]
    w_runs: Runs
    s_runs: Runs
    w_export: Export
    s_export: Export
    w_reg: Registry
    s_prior: Registry
    s_new: Registry | None
    drift: Registry | None
    build: dict[str, Any] | None = None
    power_json: dict[str, Any] | None = None
    #: the alias main registry's pairs (``--main-pairs``): orphans are the needed S pairs whose
    #: record has no stage-A pair here
    main_pairs: list[dict[str, Any]] | None = None
    #: the prior S registries' pairs.jsonl rows (``--s-prior-pairs``), or None
    s_prior_pairs: list[dict[str, Any]] | None = None
    #: Qwen label rows by backend and pair id, of the W judge dirs and the drift judge dir
    w_label_rows: dict[str, dict[str, dict[str, Any]]] | None = None
    drift_label_rows: dict[str, dict[str, dict[str, Any]]] | None = None
    label_row_duplicates: dict[str, int] = field(default_factory=dict)


def judge_dir_rows(finals: Sequence[Path]) -> tuple[dict[str, dict[str, dict[str, Any]]], int]:
    """The Qwen label rows (``alias_control_chunks.label_rows``: as the harness reads them, the last
    row of a pair id wins) of the judge dirs holding ``finals``, merged by pair id over the dirs, and
    the number of pair ids read more than once (within a file or across dirs)."""
    import alias_control_chunks as acc

    out: dict[str, dict[str, dict[str, Any]]] = {be: {} for be in QWEN_BACKENDS}
    dup = 0
    dirs: list[Path] = []
    for f in finals:
        if f.parent not in dirs:
            dirs.append(f.parent)
    for d in dirs:
        for be in QWEN_BACKENDS:
            rows, n_dup, _ = acc.label_rows(d, be)
            dup += n_dup + sum(p in out[be] for p in rows)
            out[be].update(rows)
    return out, dup


def load_inputs(a: argparse.Namespace, *, report: bool) -> Inputs:
    recs = asc.read_jsonl(a.records)
    for i, r in enumerate(recs, 1):
        for k in ("record_id", "paper", "kind", "quote", "gold_chunks"):
            if k not in r:
                raise SystemExit(f"{a.records.name}:{i}: record lacks {k!r}")
    w_runs, s_runs = read_runs(a.w_runs), read_runs(a.s_runs)
    w_reg = read_registry(a.w_final, "W registry")
    s_prior = read_registry(a.s_prior_final or [], "S prior registry")
    s_new = read_registry(a.s_final, "S registry") if report and a.s_final else None
    drift = read_registry(a.drift_final, "drift registry") if report and a.drift_final else None
    gold = {str(r["gold_chunks"][0]) for r in recs}
    w_want = top_ids(w_runs) | gold | {cid for _, cid in w_reg.labels}
    w_spec, s_spec = parse_exports(a.w_export), parse_exports(a.s_export)
    w_export = read_export(w_spec, w_want)
    gold_docs = {w_export.chunks[g].doc_id for g in gold if g in w_export.chunks}
    s_export = read_export(s_spec, top_ids(s_runs), gold_docs if report else ())
    build = json.loads(a.build_summary.read_text(encoding="utf-8")) if report and a.build_summary else None
    pj = json.loads(a.power_json.read_text(encoding="utf-8")) if report and a.power_json else None
    main_pairs = asc.read_jsonl(a.main_pairs) if getattr(a, "main_pairs", None) else None
    prior_pairs = ([row for p in a.s_prior_pairs for row in asc.read_jsonl(p)]
                   if getattr(a, "s_prior_pairs", None) else None)
    inp = Inputs(recs, w_runs, s_runs, w_export, s_export, w_reg, s_prior, s_new, drift, build, pj,
                 main_pairs, prior_pairs)
    if report:
        if a.drift_final and {f.parent.resolve() for f in a.drift_final} & {f.parent.resolve() for f in a.w_final}:
            raise SystemExit("REFUSED: a --drift-final shares a judge dir with a --w-final (the drift registry is its "
                             "own build-control out_dir; its label rows would be compared with themselves)")
        inp.w_label_rows, dw = judge_dir_rows(a.w_final)
        inp.label_row_duplicates["W"] = dw
        if a.drift_final:
            inp.drift_label_rows, dd = judge_dir_rows(a.drift_final)
            inp.label_row_duplicates["drift"] = dd
    return inp


def gold_coverage_band(rec: Mapping[str, Any], w: Export, s: Export) -> str:
    """The share of the W gold window covered by the single S chunk overlapping it most, in
    ``alias_control_chunks.COVERAGE_BANDS`` (``unknown`` when the window or its document is not
    found)."""
    import alias_control_chunks as acc

    g = w.chunks.get(str(rec["gold_chunks"][0]))
    if g is None or g.end <= g.start or g.doc_id not in s.spans:
        return "unknown"
    best = max((acc.overlap(g.start, g.end, a, b) for a, b in s.spans[g.doc_id]), default=0)
    return acc.band(best / (g.end - g.start), acc.COVERAGE_BANDS)


def build_report(inp: Inputs, *, token_counter: TokenCounter | None = None, tokenizer_name: str | None = None,
                 expected_records: int | None = EXPECTED_RECORDS, expected_eligible: int | None = EXPECTED_ELIGIBLE
                 ) -> dict[str, Any]:
    """Every figure of the report (a pure function of the inputs; no I/O)."""
    recs = inp.records
    by_id = {str(r["record_id"]): r for r in recs}
    paper = {rid: str(r["paper"]) for rid, r in by_id.items()}
    eligible = eligible_records(recs, inp.w_export.chunks)
    w_sc = score_lists(recs, inp.w_runs, inp.w_export.chunks)
    s_sc = score_lists(recs, inp.s_runs, inp.s_export.chunks)

    # labels
    w_labels = inp.w_reg.labels
    sl = SLabels(inp.s_prior, inp.s_new, w_hash_index(w_labels, inp.w_export.chunks), inp.s_export.chunks)

    def w_label(rid: str, cid: str) -> str | None:
        return w_labels.get((rid, cid))

    def s_label(rid: str, cid: str) -> str | None:
        return sl.label(rid, cid)[0]

    def s_label_bound(rid: str, cid: str) -> str | None:
        """The favourable bound (PREREG §3): an unjudged or orphan S pair scored answers."""
        w_ = s_label(rid, cid)
        return w_ if judged(w_) else "answers"

    def found(sc: ListScore, rid: str, lab: Callable[[str, str], str | None]) -> int:
        return int(sc.hit or any(lab(rid, c) == "answers" for c in sc.top))

    def units(family: str, ret: str, corpora: Sequence[str] = CORPORA, keep: Callable[[str], bool] | None = None,
              s_lab: Callable[[str, str], str | None] = s_label) -> list[tuple[str, str, int, int]]:
        out = []
        for rid in sorted(by_id):
            if family == "Q" and rid not in eligible:
                continue
            if keep is not None and not keep(rid):
                continue
            for c in corpora:
                ws, ss = w_sc[(rid, c, ret)], s_sc[(rid, c, ret)]
                if ws.missing or ws.failed or ss.missing or ss.failed:
                    continue
                if family == "Q":
                    out.append((rid, paper[rid], int(ss.hit), int(ws.hit)))
                else:
                    out.append((rid, paper[rid], found(ss, rid, s_lab), found(ws, rid, w_label)))
        return out

    # gates
    common = (retrieval_gates("W", inp.w_runs, recs) + retrieval_gates("S", inp.s_runs, recs)
              + s_mode_gates(inp.s_runs))
    cg, census_info = census_gates(inp.s_runs, inp.build)
    common += cg
    unres = {"W": sum(s.unresolved for s in w_sc.values()), "S": sum(s.unresolved for s in s_sc.values())}
    for side, n in unres.items():
        if n:
            common.append(f"{side}: {n} top-10 chunk id(s) with no text in the side's exports")
    if expected_records is not None and len(recs) != expected_records:
        common.append(f"records: {len(recs)}, the prereg's {expected_records}")
    if expected_eligible is not None and len(eligible) != expected_eligible:
        common.append(f"Q-eligible records: {len(eligible)}, the prereg's {expected_eligible}")
    missing_gold = sum(str(r["gold_chunks"][0]) not in inp.w_export.chunks for r in recs)
    if missing_gold:
        common.append(f"{missing_gold} W gold chunk(s) not in the W exports")

    # J's label coverage (PREREG §3, §7.1)
    w_need = needed_pairs(w_sc)
    s_need = needed_pairs(s_sc)
    prior_content = check_prior_content(sl, s_need, inp.s_prior_pairs)
    s_src: Counter[str] = Counter()
    for rid, cid in s_need:
        w_, src = sl.label(rid, cid)
        s_src[f"{src or 'none'}:{w_ if judged(w_) else 'unjudged'}"] += 1
    has_stage_a = None if inp.main_pairs is None else set(stage_a_pairs(inp.main_pairs))

    def s_unjudged_kind(rid: str, cid: str) -> str:
        """An S pair J needs without a judged label: an orphan (no stage-A pair for the record, so no
        registry could take it), unjudged by the judge (a registry row says unjudged), or with no
        registry row."""
        if has_stage_a is not None and rid not in has_stage_a:
            return "orphan"
        if inp.s_new is not None and (rid, cid) in inp.s_new.labels:
            return "unjudged_by_judge"
        if (rid, cid) in inp.s_prior.labels:
            return "unjudged_by_judge"
        return "no_registry_row" if has_stage_a is not None else "unclassified (no --main-pairs)"

    def unjudged_counts(sc: Mapping[tuple[str, str, str], ListScore], lab: Callable[[str, str], str | None],
                        rets: Iterable[str] | None) -> dict[str, Any]:
        need = needed_pairs(sc, rets)
        u = sum(not judged(lab(r, c)) for r, c in need)
        return {"pairs_needed": len(need), "unjudged": u, "share": round(u / len(need), 4) if need else None}

    unj = {"W": sum(not judged(w_label(r, c)) for r, c in w_need),
           "S": sum(not judged(s_label(r, c)) for r, c in s_need)}
    nneed = {"W": len(w_need), "S": len(s_need)}
    s_unj_kind = Counter(s_unjudged_kind(r, c) for r, c in s_need if not judged(s_label(r, c)))
    by_ret = {side: {ret: unjudged_counts(sc, lab, [ret]) for ret in RETRIEVERS}
              for side, sc, lab in (("W", w_sc, w_label), ("S", s_sc, s_label))}
    j_gates: list[str] = []
    # §7.1 (owner default): the gate reads the primary cell's pairs (default retriever, four corpora)
    for side in ("W", "S"):
        pc = by_ret[side][PRIMARY_RETRIEVER]
        if pc["pairs_needed"] and pc["unjudged"] / pc["pairs_needed"] > UNJUDGED_MAX:
            j_gates.append(f"{side}: {pc['unjudged']} of {pc['pairs_needed']} pairs J's primary cell needs are "
                           f"unjudged (> {UNJUDGED_MAX:.0%})")
    #: norerank / BM25: a shortfall above 5 % is a caveat on those secondary J figures, not a gate
    secondary_unjudged_caveat = {
        ret: "; ".join(f"{side}: {by_ret[side][ret]['unjudged']} of {by_ret[side][ret]['pairs_needed']} pairs this "
                       f"retriever's J needs are unjudged (> {UNJUDGED_MAX:.0%})" for side in ("W", "S")
                       if by_ret[side][ret]["pairs_needed"]
                       and by_ret[side][ret]["unjudged"] / by_ret[side][ret]["pairs_needed"] > UNJUDGED_MAX) or None
        for ret in RETRIEVERS if ret != PRIMARY_RETRIEVER}
    integrity = inp.w_reg.problems + inp.s_prior.problems + (inp.s_new.problems if inp.s_new else []) + sl.integrity()
    if inp.s_new is None:
        integrity.append("S registry: not given")
    j_gates += integrity

    # drift (descriptive, PREREG §3)
    drift = drift_summary(w_need, w_labels, inp.drift, inp.w_label_rows, inp.drift_label_rows)

    # the families
    fam: dict[str, Any] = {}
    for f in FAMILIES:
        u = units(f, PRIMARY_RETRIEVER)
        res: dict[str, Any] = {"counts": paired(u), "test": ni_test(u)}
        gates = common + (j_gates if f == "J" else [])
        caveats = []
        disc = res["counts"]["discordance"]
        if res["test"]["ni"]["reject"] and disc is not None and disc < LOW_DISCORDANCE:
            caveats.append(f"low observed discordance ({100 * disc:.1f} %): the test's size is only asymptotically "
                           f"alpha ({size_note(inp.power_json)})")
        bound = None
        if f == "J":
            caveats.append("W labels are from the 2026-11-04/06 judge runs and S labels from new judging; any judge "
                           "drift lands on S only")
            if drift["caveat"]:
                caveats.append(drift["caveat"])
            ne = len(by_id) - len(eligible)
            if ne:
                caveats.append(f"J includes the {ne} record(s) whose quote is not verbatim in its gold chunk")
            ub = units(f, PRIMARY_RETRIEVER, s_lab=s_label_bound)
            pc_need = needed_pairs(s_sc, [PRIMARY_RETRIEVER])
            bound = {"counts": paired(ub), "test": ni_test(ub),
                     "pairs": sum(not judged(s_label(r, c)) for r, c in pc_need),
                     "rule": "every unjudged or orphan S pair J's primary cell needs scored answers (favourable to S)"}
            bound["outcome"] = ungated_outcome(bound)
            bound["changes_outcome"] = bound["outcome"] != ungated_outcome(res)
            res["favourable_bound"] = bound
        res["gates"] = gates
        res["caveats"] = caveats
        res["outcome"], res["sentence"] = decide(f, res, gates, caveats, bound)
        res["secondary"] = secondary(f, units, by_id, secondary_unjudged_caveat if f == "J" else None)
        fam[f] = res

    # discordant units by the S coverage of the gold window (Q, primary cell)
    cov: dict[str, Counter[str]] = {"S_only": Counter(), "W_only": Counter(), "both_or_neither": Counter()}
    for rid, _p, s, w in units("Q", PRIMARY_RETRIEVER):
        k = "S_only" if s > w else "W_only" if w > s else "both_or_neither"
        cov[k][gold_coverage_band(by_id[rid], inp.w_export, inp.s_export)] += 1

    tokens = context_tokens(w_sc, s_sc, inp.w_export.chunks, inp.s_export.chunks, token_counter, tokenizer_name)
    kinds = Counter(str(r["kind"]) for r in recs)
    return {
        "schema": SCHEMA["report"],
        "records": len(recs), "papers": len(set(paper.values())), "records_by_kind": dict(sorted(kinds.items())),
        "q_eligible": len(eligible), "q_eligible_papers": len({paper[r] for r in eligible}),
        "primary_cell": {"retriever": PRIMARY_RETRIEVER, "corpora": list(CORPORA), "cluster": "paper",
                         "delta_pts": DELTA_PTS, "alpha_one_sided": ALPHA},
        "families": fam,
        "gates": {"common": common, "J_only": j_gates},
        "j_labels": {"pairs_needed": nneed, "unjudged": unj,
                     "unjudged_share": {k: round(unj[k] / nneed[k], 4) if nneed[k] else None for k in nneed},
                     "unjudged_gate": {"cell": "primary (default retriever, four corpora)", "max_share": UNJUDGED_MAX},
                     "s_unjudged_by_kind": dict(sorted(s_unj_kind.items())),
                     "s_orphans": s_unj_kind.get("orphan", 0),
                     "s_label_source": dict(sorted(s_src.items())),
                     "s_prior_content_check": prior_content,
                     "pairs_needed_by_retriever": {side: {ret: v["pairs_needed"] for ret, v in d.items()}
                                                   for side, d in by_ret.items()},
                     "unjudged_by_retriever": by_ret},
        "drift_check": drift,
        "label_row_duplicates": dict(sorted(inp.label_row_duplicates.items())),
        "discordant_by_gold_window_coverage_under_S": {k: dict(sorted(v.items())) for k, v in cov.items()},
        "context_tokens": tokens,
        "census": census_info,
        "unresolved_chunk_texts": unres,
        "scorer": {"module": "reach_quote", "min_match": MIN_MATCH, "hit_threshold": HIT_THRESHOLD, "top_k": TOP_K},
    }


def secondary(family: str, units: Callable[..., list[tuple[str, str, int, int]]],
              by_id: Mapping[str, Mapping[str, Any]], unjudged_caveat: Mapping[str, str | None] | None = None
              ) -> dict[str, Any]:
    """PREREG §4 secondary, not tested: each corpus (default retriever), each retriever (four corpora
    pooled), G vs C records (primary cell). J's norerank / BM25 figures carry ``caveat`` when more
    than 5 % of the pairs that retriever's J needs are unjudged (PREREG §7.1)."""
    by_ret = {r: paired(units(family, r)) for r in RETRIEVERS}
    for r, cav in (unjudged_caveat or {}).items():
        by_ret[r]["caveat"] = cav
    return {"by_corpus_default": {c: paired(units(family, PRIMARY_RETRIEVER, [c])) for c in CORPORA},
            "by_retriever_pooled": by_ret,
            "by_kind_primary": {k: paired(units(family, PRIMARY_RETRIEVER, keep=lambda rid, k=k: str(
                by_id[rid]["kind"]) == k)) for k in ("G", "C")},
            "tested": False}


def context_tokens(w_sc: Mapping[tuple[str, str, str], ListScore], s_sc: Mapping[tuple[str, str, str], ListScore],
                   w_texts: Mapping[str, Chunk], s_texts: Mapping[str, Chunk], counter: TokenCounter | None,
                   name: str | None) -> dict[str, Any]:
    """Descriptive (PREREG §3): the tokens of the top 10 per query, per side and retriever (four
    corpora pooled), and per corpus under the default retriever."""
    if counter is None:
        return {"computed": False, "reason": "no tokenizer"}
    cache: dict[tuple[str, str], int] = {}

    def toks(side: str, texts: Mapping[str, Chunk], cid: str) -> int:
        k = (side, cid)
        if k not in cache:
            cache[k] = counter(texts[cid].content) if cid in texts else 0
        return cache[k]

    def stats(xs: list[int]) -> dict[str, Any]:
        xs = sorted(xs)
        n = len(xs)
        if not n:
            return {"lists": 0}
        med = xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2
        return {"lists": n, "mean": round(sum(xs) / n, 1), "median": med}

    out: dict[str, Any] = {"computed": True, "tokenizer": name, "per_list": "sum of the top-10 chunk texts' tokens"}
    for side, sc, texts in (("W", w_sc, w_texts), ("S", s_sc, s_texts)):
        per: dict[tuple[str, str], list[int]] = defaultdict(list)
        for (_rid, c, ret), s in sc.items():
            if s.missing or s.failed:
                continue
            per[(c, ret)].append(sum(toks(side, texts, x) for x in s.top))
        out[side] = {"by_retriever_pooled": {r: stats([v for (c, rr), vs in per.items() if rr == r for v in vs])
                                             for r in RETRIEVERS},
                     "by_corpus_default": {c: stats(per[(c, PRIMARY_RETRIEVER)]) for c in CORPORA}}
    return out


def drift_summary(w_need: Mapping[tuple[str, str], Any], w_labels: Mapping[tuple[str, str], str],
                  drift: Registry | None, w_rows: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
                  drift_rows: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None) -> dict[str, Any]:
    """PREREG §3 (descriptive): the seeded 5 % of the W pairs J uses, re-judged; agreement of the new
    labels with the originals, overall and by original label; the *judge drift* caveat when the
    agreement on the original *answers* labels is below 0.90 (or cannot be computed).

    Two checks on the re-judge itself, on the Qwen label rows (``w_rows``: the W judge dirs';
    ``drift_rows``: the drift judge dir's; ``backend -> pair id -> row``), by
    ``alias_control_chunks.cache_check`` (the alias-confirm §6.9 rule, reused): **prompt identity**:
    each re-judged pair's ``prompt_sha256`` / ``system_sha256`` against the original row of the same
    pair id; any difference, or no compared row at all, makes the drift check *not evaluable* (it
    would compare different prompts); and the **cache check**: ``reasoning_sample`` byte-identical
    on a pair where both rows' reasoning has >= 200 characters flags *cache suspected* (a caveat), a
    backend with compared rows but no eligible pair reads *cache check not evaluable* (a caveat)."""
    import alias_control_chunks as acc
    import alias_study_judge as aj

    sample = drift_sample(w_need)
    base: dict[str, Any] = {"seed": DRIFT_SEED, "share": DRIFT_SHARE, "w_pairs_used": len(w_need), "sample": len(sample)}
    if drift is None:
        return {**base, "evaluable": False, "caveat": "judge drift check not evaluable (no drift registry given)"}
    keys = set(sample)
    extra = sum(k not in keys for k in drift.labels)
    by: dict[str, Counter[str]] = defaultdict(Counter)
    for k in sample:
        orig = w_labels.get(k) or "no_label"
        new = drift.labels.get(k) or "not_rejudged"
        by[orig][new] += 1
    both = [(w_labels.get(k), drift.labels.get(k)) for k in sample
            if judged(w_labels.get(k)) and judged(drift.labels.get(k))]
    agree = sum(a == b for a, b in both)
    ans = [(a, b) for a, b in both if a == "answers"]
    agree_ans = sum(b == "answers" for _, b in ans)
    r_all = round(agree / len(both), 4) if both else None
    r_ans = round(agree_ans / len(ans), 4) if ans else None

    # the prompt identity and the Qwen cache check (alias-confirm §6.9, reused)
    ids = sorted({aj.pair_id(r, c) for r, c in sample if (r, c) in drift.labels})
    cache = acc.cache_check(ids, w_rows or {}, drift_rows or {})
    compared = sum(cache[be]["compared"] for be in QWEN_BACKENDS)
    differs = {be: {"prompt": cache[be]["prompt_sha256_differs"], "system": cache[be]["system_sha256_differs"]}
               for be in QWEN_BACKENDS}
    mismatch = sum(v["prompt"] + v["system"] for v in differs.values())
    if w_rows is None or drift_rows is None:
        prompt_status = "absent"
    elif compared == 0:
        prompt_status = "no_compared_rows"
    else:
        prompt_status = "mismatch" if mismatch else "identical"
    evaluable = prompt_status == "identical"
    caveats: list[str] = []
    if not evaluable:
        why = {"absent": "no label rows given", "no_compared_rows": "no re-judged pair has an original Qwen label row "
               "of the same pair id", "mismatch": f"the re-judge prompt differs from the original on {mismatch} "
               "(backend, pair) row(s)"}[prompt_status]
        caveats.append(f"judge drift check not evaluable ({why})")
    elif r_ans is None:
        caveats.append("judge drift check not evaluable on answers labels (no original answers label re-judged)")
    elif r_ans < DRIFT_AGREE_MIN:
        caveats.append(f"judge drift: the re-judged W answers labels agree with the originals on {100 * r_ans:.1f} % "
                       f"(< {100 * DRIFT_AGREE_MIN:.0f} %)")
    if cache["flag"] is True:
        n_id = sum(cache[be]["reasoning_identical"] for be in QWEN_BACKENDS)
        caveats.append(f"cache suspected: {n_id} re-judged (backend, pair) row(s) with Qwen reasoning byte-identical "
                       "to the original's")
    elif cache["flag"] == "not_evaluable" and compared:
        caveats.append("cache check not evaluable (a backend with compared rows has no pair with reasoning on both "
                       "sides)")
    return {**base, "evaluable": evaluable, "rejudged": sum(k in drift.labels for k in sample),
            "labels_outside_sample": extra, "judged_both": len(both), "agreement": r_all,
            "agreement_on_answers": r_ans, "answers_judged_both": len(ans),
            "by_original_label": {k: dict(sorted(v.items())) for k, v in sorted(by.items())},
            "registry_problems": len(drift.problems),
            "prompt_identity": {"status": prompt_status, "compared_rows": compared, "differs": differs},
            "cache_check": cache, "caveat": "; ".join(caveats) or None}


def forbidden_ids(inp: Inputs) -> set[str]:
    out: set[str] = set()
    for r in inp.records:
        out |= {str(r["record_id"]), str(r["paper"]), *map(str, r["gold_chunks"])}
    for runs in (inp.w_runs, inp.s_runs):
        for row in runs.rows.values():
            out |= {str(x) for x in (row.get("ranked_chunk_ids") or [])[:TOP_K]}
    for reg in (inp.w_reg, inp.s_prior, inp.s_new, inp.drift):
        if reg is not None:
            for rid, cid in reg.labels:
                out |= {rid, cid}
    return out


def private_texts(inp: Inputs) -> list[str]:
    return [str(r.get(k)) for r in inp.records for k in ("question", "quote") if r.get(k)]


def check_prereg(path: Path | None, sha: str | None, committed: Path | None = None) -> dict[str, Any]:
    """PREREG §6 step 5 / §9: ``report`` takes ``--prereg PREREG.md --prereg-sha256 SHA`` (both
    required) and refuses unless the sha is well formed, the file hashes to it, and it is the
    committed prereg's own sha (``committed``, default :data:`PREREG_FILE`, read at call time): a
    wrong sha, or another file with its own matching sha, is refused before anything is read."""
    if path is None or sha is None:
        raise SystemExit("REFUSED: report needs --prereg PREREG.md and --prereg-sha256 SHA (the frozen sha)")
    if len(sha) != 64 or any(ch not in "0123456789abcdef" for ch in sha):
        raise SystemExit("REFUSED: --prereg-sha256 takes the prereg's sha256 (64 lower-case hex digits)")
    have = asc.sha256_file(path)
    if have != sha:
        raise SystemExit(f"REFUSED: the prereg's sha256 {have[:12]}… != the frozen {sha[:12]}…")
    if asc.sha256_file(committed or PREREG_FILE) != sha:
        raise SystemExit("REFUSED: the sha is not the committed PREREG.md's (another file was given, or the committed "
                         "prereg changed)")
    return {"prereg": "reach-rechunk-2026-11", "sha256": have, "verified": True}


def run_report(a: argparse.Namespace, token_counter: TokenCounter | None = None) -> dict[str, Any]:
    import alias_control_chunks as acc
    import rechunk_prep as rp

    prereg = check_prereg(a.prereg, a.prereg_sha256)
    inp = load_inputs(a, report=True)
    name = None
    if token_counter is None and a.tokenizer and a.tokenizer != "none":
        token_counter, name = hf_token_counter(a.tokenizer), a.tokenizer
    elif token_counter is not None:
        name = a.tokenizer or "injected"
    rec = build_report(inp, token_counter=token_counter, tokenizer_name=name,
                       expected_records=a.expected_records, expected_eligible=a.expected_eligible)
    here = Path(__file__).resolve()
    inputs = {"records": asc.sha256_file(a.records), "w_runs": inp.w_runs.sha256, "s_runs": inp.s_runs.sha256,
              "w_exports": inp.w_export.sha256, "s_exports": inp.s_export.sha256, "w_final": inp.w_reg.sha256,
              "s_prior_final": inp.s_prior.sha256, "s_final": inp.s_new.sha256 if inp.s_new else [],
              "drift_final": inp.drift.sha256 if inp.drift else [], "main_pairs": asc.sha256_file(a.main_pairs),
              "s_prior_pairs": [asc.sha256_file(p) for p in a.s_prior_pairs or []]}
    if a.build_summary:
        inputs["build_summary"] = asc.sha256_file(a.build_summary)
    if a.power_json:
        inputs["power_json"] = asc.sha256_file(a.power_json)
    rec.update({"prereg": prereg, "confirmatory": bool(prereg.get("verified")), "inputs_sha256": inputs,
                "code_sha256": {"reach_report": hashlib.sha256(here.read_bytes()).hexdigest(),
                                "reach_quote": asc.sha256_file(_HERE / "reach_quote.py"),
                                "power_py": asc.sha256_file(POWER_PY)},
                "provenance": acc.public_provenance(rp.experiment_provenance())})
    return acc.write_public(a.out, rec, forbidden_ids(inp), private_texts(inp))


# ------------------------------------------------------------------ j-seeds

def stage_a_pairs(main_pairs: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """``record -> [stage-A pair of the main registry, …]`` (the pairs ``build-control`` accepts as
    ``control_of``)."""
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in main_pairs:
        if p.get("stage", "A") == "A":
            out[str(p["record_id"])].append(dict(p))
    return out


def seed_rows(need: Mapping[tuple[str, str], Sequence[tuple[str, str, int]]], chunks: Mapping[str, Chunk],
              by_rec: Mapping[str, Sequence[Mapping[str, Any]]], stage: str, self_control: bool = False
              ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """The seed rows (``rechunk_prep.stage1b_seeds``' construction) and the orphans (no stage-A
    pair for the record). Each record's stage-A pairs are offered as the candidates (``pair_id``,
    their chunk as the window, ``arm``, the corpus of the chunk's first list); ``stage1b_seeds``
    picks the first by pair id among those whose passage paper (``c_paper``) is the chunk's paper,
    else the first by pair id. ``self_control`` (the drift registry): a pair that is itself a
    stage-A pair controls itself."""
    import alias_study_judge as aj
    import rechunk_prep as rp

    cand: dict[tuple[str, str], list[tuple[dict[str, Any], int]]] = {}
    orphans: list[dict[str, Any]] = []
    paper_of: dict[str, str] = {}
    for (rid, cid), slots in sorted(need.items()):
        corpus = sorted(slots, key=lambda s: (CORPORA.index(s[0]), s[2]))[0][0]
        best = min(s[2] for s in slots)
        pairs = list(by_rec.get(rid, []))
        if self_control:
            own = [p for p in pairs if p["pair_id"] == aj.pair_id(rid, cid)]
            pairs = own or pairs
        if not pairs:
            orphans.append({"record_id": rid, "chunk_id": cid, "pair_id": aj.pair_id(rid, cid), "stage": stage,
                            "reason": "no stage-A pair for the record in the main registry"})
            continue
        offered = []
        for p in pairs:
            win = f"window|{p['pair_id']}"
            paper_of[win] = str(p.get("c_paper"))
            offered.append(({"pair_id": p["pair_id"], "max_overlap_chunk_id": win, "stage_a_chunk_id": p["chunk_id"],
                             "arm": p.get("arm"), "corpus_arm": corpus, "draw_stratum": None}, best))
        cand[(rid, cid)] = offered
        paper_of[cid] = chunks[cid].paper
    seeds, counts = rp.stage1b_seeds(cand, paper_of, {cid: chunks[cid].doc_id for _, cid in cand})
    for s in seeds:
        s["stage"] = stage
        s["stage_a_pairs_offered"] = s.pop("lost_pairs_with_chunk")
        s["lists_with_chunk"] = len(need[(s["record_id"], s["chunk_id"])])
        s.pop("stratum", None)
    counts = {"seeds": counts["seeds"], "seed_records": counts["seed_records"],
              "seeds_same_paper_as_control": counts["seeds_same_paper_as_window"],
              "seeds_other_paper": counts["seeds_other_paper"], "seeds_by_corpus_arm": counts["seeds_by_corpus_arm"],
              "seeds_by_best_rank": counts["seeds_by_best_rank"], "orphans": len(orphans)}
    return seeds, orphans, counts


def build_seeds(inp: Inputs, main_pairs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The J seeds and the drift seeds (module docstring, *j-seeds*): rows and counts, no I/O."""
    recs = inp.records
    w_sc = score_lists(recs, inp.w_runs, inp.w_export.chunks)
    s_sc = score_lists(recs, inp.s_runs, inp.s_export.chunks)
    sl = SLabels(inp.s_prior, None, w_hash_index(inp.w_reg.labels, inp.w_export.chunks), inp.s_export.chunks)
    s_need = needed_pairs(s_sc)
    prior_content = check_prior_content(sl, s_need, inp.s_prior_pairs)
    src: Counter[str] = Counter()
    todo: dict[tuple[str, str], list[tuple[str, str, int]]] = {}
    for key, slots in s_need.items():
        w, s = sl.pre_judging(*key)
        if judged(w):
            src[f"reused_{s}"] += 1
        else:
            src["prior_unjudged" if w == "unjudged" else "unlabelled"] += 1
            todo[key] = list(slots)
    missing_text = sorted({cid for _, cid in todo if cid not in inp.s_export.chunks})
    if missing_text:
        raise SystemExit(f"REFUSED: {len(missing_text)} S seed chunk(s) have no export row; nothing written")
    by_rec = stage_a_pairs(main_pairs)
    s_seeds, s_orph, s_counts = seed_rows(todo, inp.s_export.chunks, by_rec, S_STAGE)
    w_need = needed_pairs(w_sc)
    sample = drift_sample(w_need)
    d_need = {k: w_need[k] for k in sample}
    missing_w = sorted({cid for _, cid in d_need if cid not in inp.w_export.chunks})
    if missing_w:
        raise SystemExit(f"REFUSED: {len(missing_w)} drift chunk(s) have no W export row; nothing written")
    d_seeds, d_orph, d_counts = seed_rows(d_need, inp.w_export.chunks, by_rec, DRIFT_STAGE, self_control=True)
    counts = {"s_pairs_needed": len(s_need), "s_label_before_judging": dict(sorted(src.items())),
              "s_prior_content_check": prior_content,
              "s_pairs_needed_by_retriever": {r: len(needed_pairs(s_sc, [r])) for r in RETRIEVERS},
              "s": s_counts, "w_pairs_used": len(w_need),
              "w_pairs_used_unlabelled": sum(not judged(inp.w_reg.labels.get(k)) for k in w_need),
              "drift": {"seed": DRIFT_SEED, "share": DRIFT_SHARE, "sample": len(sample), **d_counts},
              "unresolved_chunk_texts": {"W": sum(s.unresolved for s in w_sc.values()),
                                         "S": sum(s.unresolved for s in s_sc.values())},
              "failed_or_missing_lists": {"W": sum(s.failed or s.missing for s in w_sc.values()),
                                          "S": sum(s.failed or s.missing for s in s_sc.values())}}
    return {"s_seeds": s_seeds, "drift_seeds": d_seeds, "orphans": s_orph + d_orph, "counts": counts}


def run_seeds(a: argparse.Namespace) -> dict[str, Any]:
    import alias_control_chunks as acc
    import rechunk_prep as rp

    out = Path(a.out_dir)
    rp.refuse_in_repo(out / "x", "--out-dir")
    inp = load_inputs(a, report=False)
    main_pairs = inp.main_pairs or []
    res = build_seeds(inp, main_pairs)
    s_ids = {s["chunk_id"] for s in res["s_seeds"]}
    d_ids = {s["chunk_id"] for s in res["drift_seeds"]}
    s_lines, _, _ = rp.scan_exports(export_files(parse_exports(a.s_export)), s_ids)
    w_lines, _, _ = rp.scan_exports(export_files(parse_exports(a.w_export)), d_ids)
    shas = {
        "s_seed": acc.write_private(out / "s-seed.jsonl", acc.jsonl_bytes(res["s_seeds"]), "the S seeds"),
        "s_export": acc.write_private(out / "s-export.jsonl", "".join(s_lines[c] for c in sorted(s_ids)).encode(),
                                      "the S export rows"),
        "drift_seed": acc.write_private(out / "drift-seed.jsonl", acc.jsonl_bytes(res["drift_seeds"]),
                                        "the drift seeds"),
        "drift_export": acc.write_private(out / "drift-export.jsonl",
                                          "".join(w_lines[c] for c in sorted(d_ids)).encode(), "the drift export rows"),
        "orphans": acc.write_private(out / "orphans.jsonl", acc.jsonl_bytes(res["orphans"]), "the orphan seeds"),
    }
    rec = {"schema": SCHEMA["seeds"], **res["counts"],
           "rule": "S pairs J needs = top-10 (record, chunk) of every S list without a quote hit, all retrievers; "
                   "seeded unless a judged label exists by key (prior S registry) or by the content-hash rule; an "
                   "orphan (no stage-A pair for the record) cannot be seeded and counts as not answering (PREREG §3)",
           "build_control": "alias_study_judge.py build-control --any-paper --seed-pairs s-seed.jsonl (control "
                            "export s-export.jsonl); a second registry for drift-seed.jsonl (control export "
                            "drift-export.jsonl)",
           "outputs_sha256": shas,
           "inputs_sha256": {"records": asc.sha256_file(a.records), "w_runs": inp.w_runs.sha256,
                             "s_runs": inp.s_runs.sha256, "w_final": inp.w_reg.sha256,
                             "s_prior_final": inp.s_prior.sha256, "main_pairs": asc.sha256_file(a.main_pairs),
                             "s_prior_pairs": [asc.sha256_file(p) for p in a.s_prior_pairs or []],
                             "w_exports": inp.w_export.sha256, "s_exports": inp.s_export.sha256},
           "registry_problems": inp.w_reg.problems + inp.s_prior.problems,
           "provenance": acc.public_provenance(rp.experiment_provenance())}
    forbidden = forbidden_ids(inp) | {s[k] for s in res["s_seeds"] + res["drift_seeds"] + res["orphans"]
                                      for k in ("pair_id", "chunk_id", "record_id")}
    forbidden |= {str(p.get("pair_id")) for p in main_pairs}
    acc.write_summary(out / "j-seeds.json", rec, "j-seeds.json", forbidden, private_texts(inp))
    return rec


# ------------------------------------------------------------------ CLI

def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--records", required=True, type=Path)
    p.add_argument("--w-runs", required=True, type=Path)
    p.add_argument("--s-runs", required=True, type=Path)
    p.add_argument("--w-export", required=True, action="append", help="ARM=PATH (repeatable)")
    p.add_argument("--s-export", required=True, action="append", help="ARM=PATH (repeatable)")
    p.add_argument("--w-final", required=True, action="append", type=Path)
    p.add_argument("--s-prior-final", action="append", type=Path, default=None)
    p.add_argument("--s-prior-pairs", action="append", type=Path, default=None,
                   help="a prior S registry's pairs.jsonl (repeatable): reused keys' content_sha256 is checked")
    p.add_argument("--main-pairs", required=True, type=Path, help="the alias main judge registry's pairs.jsonl")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("j-seeds", help="the S seeds J needs and the W drift seeds (private)")
    _common(s)
    s.add_argument("--out-dir", required=True, type=Path)
    r = sub.add_parser("report", help="the public report (counts only)")
    _common(r)
    r.add_argument("--s-final", action="append", type=Path, default=None)
    r.add_argument("--drift-final", action="append", type=Path, default=None)
    r.add_argument("--build-summary", type=Path, default=None)
    r.add_argument("--power-json", type=Path, default=POWER_PY.with_name("power.json"))
    r.add_argument("--prereg", required=True, type=Path)
    r.add_argument("--prereg-sha256", required=True)
    r.add_argument("--tokenizer", default="Salesforce/SFR-Embedding-Mistral", help="a local HF tokenizer, or none")
    r.add_argument("--expected-records", type=int, default=EXPECTED_RECORDS)
    r.add_argument("--expected-eligible", type=int, default=EXPECTED_ELIGIBLE)
    r.add_argument("--out", required=True, type=Path)
    a = ap.parse_args(argv)
    if a.cmd == "j-seeds":
        rec = run_seeds(a)
        print(json.dumps({k: rec[k] for k in ("s_pairs_needed", "s", "drift")}, indent=1))
        return 0
    rec = run_report(a)
    print(json.dumps({f: {"outcome": rec["families"][f]["outcome"], "sentence": rec["families"][f]["sentence"]}
                      for f in FAMILIES}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
