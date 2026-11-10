#!/usr/bin/env python
"""Split-answer follow-up: OFFLINE feasibility counts (exploratory; no store, no retrieval, no LLM call).

    HF_HUB_OFFLINE=1 PYTHONPATH=python:python/scripts/eval python \\
        docs/plans/results/split-answer-2026-11/split_feasibility.py \\
        --confirm-dir CONFIRM_DIR --label-table LABEL_TABLE.jsonl --out artifacts/feasibility.json

``CONFIRM_DIR`` is the confirmatory test's private run directory (it holds ``run/`` and ``retest/``);
``LABEL_TABLE.jsonl`` is rechunk stage 1's private label table. Both are read only. The output is
public: counts only, checked before it is written (no id, no private text, no host field, no path).

**What it measures.** For every confirmatory pair (the 351; groups from the label table: the 18
*split* pairs (cover class ``none``), the 53 *recovered* (``other_only``), the 4 ``primary_only``,
and the 276 pairs whose control chunk kept *answers*, split into the 56 whose control chunk is
byte-identical to the window and the 220 others), it locates the stage-A window ``W`` in its
document and asks, under the adopted chunking and under alternative chunkings of the SAME
documents, how far ``W``'s text is held by one passage:

- ``contain``: one chunk holds all of ``W``;
- ``best_cov``: the largest share of ``W`` one chunk holds (mean, and the count at >= 0.8);
- ``bridged_k`` (k = 1, 2, 3 sentences): every cut the ADOPTED chunking makes inside ``W`` lies inside
  one chunk of the alternative with at least k sentences of ``W`` on each side of it (clipped to
  ``W``). An answer that straddles an adopted cut and reaches no further than k sentences either
  side would then sit in one chunk. This is a proxy: the answer's position inside ``W`` is not
  known (no answer span was annotated), and an alternative can cut ``W`` somewhere new.

It also classifies every adopted cut inside ``W`` as a **section wall** (a cut-segment edge of
``doc_cut_segments``) or a **size cut** (inside one segment), because overlap under
``structure_cuts`` restarts at every segment (``chunkers._pack_segments``) and cannot bridge a wall.

**Chunkings** (all built by ``chunker_config.build_chunker`` from collection params, read by the
production readers, applied by ``alias_control_chunks.chunk_document``: ``attach_structure`` with
``markdown/1``, chunk, stamp). ``adopted`` must reproduce the confirmatory control chunks exactly
(ids and spans of every document), or the script refuses. Document text is the concatenation of the
control chunks, which tile it (``alias_control_chunks.doc_text``), checked against every window.

**Neighbour passages** (no re-chunking): the max-overlap adopted chunk with its ``k`` neighbours
on each side in document order (``context_window`` k = 1, 2, as ``retriever.expand_context`` walks
them), and every pair of adjacent adopted chunks overlapping ``W``. Their SFR token length is the
judge-input size.

**Judge cost.** For each chunking: the (record, chunk) items a cover-any style judging would need
(every chunk overlapping ``W``, at most ``COVER_ANY_CAP`` by overlap, plus the max-overlap chunk) that
have no label row in the label table. Chunk ids are ``uuid5(doc:start:end)``, so an equal id is an
equal span of the same text and its label is reused; every neighbour passage is a new item.
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[3]
for _p in (_REPO / "python", _REPO / "python" / "scripts" / "eval", _REPO / "python" / "scripts" / "eval" / "glossbench"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import alias_control_chunks as acc  # noqa: E402
import alias_study_common as asc  # noqa: E402

SCHEMA = "split-answer-feasibility/1"
SENT_BASE = {"size_unit": "tokens", "pack": "318", "structure_cuts": "sections/1"}
#: name -> (method, chunk_size, chunk_overlap, params). ``adopted`` is the confirmatory control.
CONFIGS: dict[str, tuple[str, int, int, dict[str, Any]]] = {
    "adopted": (acc.CONTROL_METHOD, acc.CONTROL_SIZE, acc.CONTROL_OVERLAP, dict(acc.CONTROL_PARAMS)),
    "tol0.50": ("sentence", 512, 0, {**SENT_BASE, "size_tolerance": 0.5, "min_piece": 128}),
    "tol1.00": ("sentence", 512, 0, {**SENT_BASE, "size_tolerance": 1.0, "min_piece": 128}),
    "strict": ("sentence", 512, 0, dict(SENT_BASE)),
    "strict_ov256": ("sentence", 512, 256, dict(SENT_BASE)),
    "strict_ov512": ("sentence", 512, 512, dict(SENT_BASE)),
    "strict_ov1024": ("sentence", 512, 1024, dict(SENT_BASE)),
    "nosections_ov512": ("sentence", 512, 512, {"size_unit": "tokens", "pack": "318"}),
    "ft512o64s": ("fixed_token", 512, 64, {"structure_cuts": "sections/1"}),
}
GROUPS = ("split", "recovered", "primary_only", "kept_identical", "kept_changed")
KS = (1, 2, 3)
NEIGHBOUR_KS = (1, 2)
PATH_MARKERS = ("/rag/", "/home/", "/tmp/", "/etc/", "/proc/", "file:")


# ------------------------------------------------------------------ inputs

def read(path: Path) -> list[dict[str, Any]]:
    return list(asc.read_jsonl(path))


def group_of(p: Mapping[str, Any], identical: bool) -> str:
    if p["lost"]:
        return {"none": "split", "other_only": "recovered", "primary_only": "primary_only"}[p["cover_class"]]
    return "kept_identical" if identical else "kept_changed"


def build(name: str) -> tuple[Any, Any]:
    from ragstack.ingestion.chunker_config import (
        build_chunker,
        semantic_cap_from_params,
        size_unit_from_params,
        structure_from_params,
    )

    method, size, ov, params = CONFIGS[name]
    markers = structure_from_params(method, params)
    tol = semantic_cap_from_params(method, params, recorded_size=size)
    chunker, counter, _budget = build_chunker(
        method, chunk_size=size, chunk_overlap=ov, model=acc.TOKENIZER, token_backend="hf",
        max_tokens=acc.MODEL_WINDOW, size_unit=size_unit_from_params(method, params),
        structure_cuts=markers.structure_cuts, **tol)
    return chunker, counter


def descriptor(name: str) -> str:
    from ragstack.provenance import chunk_descriptor

    method, size, ov, params = CONFIGS[name]
    return chunk_descriptor(method, size, ov, params)


# ------------------------------------------------------------------ geometry

def covers(spans: Sequence[tuple[int, int]], lo: int, hi: int) -> bool:
    return any(s <= lo and e >= hi for s, e in spans)


def best_cov(spans: Sequence[tuple[int, int]], ws: int, we: int) -> float:
    return max((acc.overlap(ws, we, s, e) for s, e in spans), default=0) / (we - ws)


def interior_cuts(spans: Sequence[tuple[int, int]], ws: int, we: int) -> list[int]:
    """Chunk starts strictly inside ``(ws, we)`` (the adopted chunks tile the text)."""
    return sorted({s for s, _e in spans if ws < s < we})


def bridge_need(bounds: Sequence[int], c: int, k: int, ws: int, we: int) -> tuple[int, int]:
    """The span ``[lo, hi)`` that holds k sentence units of ``W`` on each side of cut ``c``.
    ``bounds``: sorted sentence boundaries (sentence-span starts, the text end) plus every adopted cut,
    so a cut inside a sentence (a segment edge splitting it, or an over-ceiling hard split) is a unit
    edge too."""
    i = bisect.bisect_left(bounds, c)
    lo = bounds[i - k] if i - k >= 0 else 0
    j = bisect.bisect_right(bounds, c)
    hi = bounds[j + k - 1] if j + k - 1 < len(bounds) else bounds[-1]
    return max(ws, lo), min(we, hi)


def stats(xs: Sequence[float]) -> dict[str, Any]:
    if not xs:
        return {"n": 0}
    s = sorted(xs)

    def q(f: float) -> float:
        return s[min(len(s) - 1, int(f * len(s)))]
    return {"n": len(s), "mean": round(statistics.fmean(s), 3), "p10": q(0.1), "p50": q(0.5), "p90": q(0.9),
            "max": s[-1], "min": s[0]}


# ------------------------------------------------------------------ public output

def path_problems(obj: Any) -> list[str]:
    bad = []
    for s in asc.string_values(obj):
        if s.startswith("/") or any(m in s for m in PATH_MARKERS):
            bad.append("a path")
    return bad


def write_public(path: Path, obj: Mapping[str, Any], forbidden: set[str], texts: Sequence[str]) -> None:
    bad = asc.public_problems(obj, forbidden, texts) + path_problems(obj)
    if bad:
        raise SystemExit(f"REFUSED: the public file would carry {len(bad)} private string(s) "
                         f"({sorted(set(bad))}); nothing written")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# ------------------------------------------------------------------ main

def run(confirm_dir: Path, label_table: Path, out: Path) -> dict[str, Any]:
    env = acc.require_env()
    if out.resolve().is_relative_to(confirm_dir.resolve()):
        raise SystemExit("REFUSED: --out inside the private confirm directory")
    seeds = read(confirm_dir / "run" / "pairs-seed.jsonl")
    cover = read(confirm_dir / "run" / "pairs-seed-cover.jsonl")
    cdocs = acc.control_docs(confirm_dir / "run" / "chunks-control.jsonl")
    windows = {r["chunk_id"]: r for r in read(confirm_dir / "retest" / "chunks-retest.jsonl")}
    table = read(label_table)
    meta = [r for r in table if r.get("kind") == "meta"]
    if len(meta) != 1 or meta[0].get("schema") != "rechunk-label-table/1":
        raise SystemExit("REFUSED: the label table has no rechunk-label-table/1 meta row")
    tpairs = {r["pair_id"]: r for r in table if r.get("kind") == "pair"}
    labelled = {(r["record_id"], r["chunk_id"]) for r in table if r.get("kind") == "label"}
    if len(seeds) != 351 or set(tpairs) != {s["control_of"] for s in seeds}:
        raise SystemExit("REFUSED: pairs-seed and the label table do not hold the same 351 pairs")

    # document texts (the control chunks tile them)
    texts: dict[str, str] = {}
    for d, rows in cdocs.items():
        t = acc.doc_text(rows)
        if t is None:
            raise SystemExit("REFUSED: a document's control chunks do not tile its text")
        texts[d] = t
    ctl_text = {r["chunk_id"]: r["content"] for rows in cdocs.values() for r in rows}

    # pairs and their windows
    pairs: list[dict[str, Any]] = []
    checks: Counter[str] = Counter()
    for s in seeds:
        w = windows.get(s["stage_a_chunk_id"])
        if w is None or w["doc_id"] != s["doc_id"]:
            raise SystemExit("REFUSED: a pair's stage-A window is missing from the retest export")
        ws, we = w["start_char"], w["end_char"]
        if texts[s["doc_id"]][ws:we] != w["content"] or we - ws != s["window_chars"]:
            raise SystemExit("REFUSED: a window does not reproduce from the document text")
        tp = tpairs[s["control_of"]]
        if tp["max_overlap_chunk_id"] != s["chunk_id"] or tp["record_id"] != s["record_id"]:
            raise SystemExit("REFUSED: the label table's max-overlap chunk is not pairs-seed's")
        identical = ctl_text[s["chunk_id"]] == w["content"]
        pairs.append({"pid": s["control_of"], "rid": s["record_id"], "doc": s["doc_id"], "ws": ws, "we": we,
                      "group": group_of(tp, identical), "max_id": s["chunk_id"],
                      "n_overlapping_seed": s["n_overlapping"]})
        checks["windows_reproduced"] += 1
    # cross-check: the cover-any seeds put the same windows in the same place
    by_pair_cover: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in cover:
        by_pair_cover[c["control_of"]].append(c)
    spans_of = {r["chunk_id"]: (r["start_char"], r["end_char"]) for rows in cdocs.values() for r in rows}
    for p in pairs:
        cs = by_pair_cover.get(p["pid"])
        if not cs:
            continue
        cs = sorted(cs, key=lambda c: spans_of[c["chunk_id"]][0])
        ws = spans_of[cs[0]["chunk_id"]][1] - cs[0]["overlap_chars"]
        we = spans_of[cs[-1]["chunk_id"]][0] + cs[-1]["overlap_chars"] if len(cs) > 1 else ws + cs[0]["overlap_chars"]
        checks["cover_windows_agree" if (ws, we) == (p["ws"], p["we"]) else "cover_windows_disagree"] += 1
    if checks["cover_windows_disagree"]:
        raise SystemExit("REFUSED: a cover-any seed places a window differently from the retest export")
    groups = Counter(p["group"] for p in pairs)

    # chunk every document under every config
    docs = sorted({p["doc"] for p in pairs})
    chunked: dict[str, dict[str, list[tuple[int, int, str]]]] = {}
    seg_edges: dict[str, set[int]] = {}
    chunk_summary: dict[str, Any] = {}
    counter = None
    for name in CONFIGS:
        chunker, counter = build(name)
        cc = SimpleNamespace(chunker=chunker)
        per: dict[str, list[tuple[int, int, str]]] = {}
        toks: list[int] = []
        adj_overlap: list[int] = []
        cut_counts: Counter[str] = Counter()
        for d in docs:
            cd = acc.chunk_document(d, texts[d], cc)
            per[d] = sorted((c.start_char, c.end_char, c.id) for c in cd.chunks)
            cut_counts.update({k: v for k, v in cd.counts.items() if str(k).startswith("cuts:")})
            if name == "adopted":
                seg_edges[d] = {g.start for g in acc.doc_segments(cd.doc)} - {0}
            toks += [counter.count(texts[d][s:e]) for s, e, _ in per[d]]
            adj_overlap += [max(0, a[1] - b[0]) for a, b in zip(per[d], per[d][1:], strict=False)]
        chunked[name] = per
        doc_tokens = sum(counter.count(texts[d]) for d in docs) if name == "adopted" else None
        chunk_summary[name] = {
            "descriptor": descriptor(name), "documents": len(docs), "chunks": len(toks),
            "tokens": stats(toks), "tokens_total": sum(toks),
            "adjacent_pairs": len(adj_overlap), "adjacent_pairs_overlapping": sum(1 for x in adj_overlap if x > 0),
            "adjacent_overlap_chars": stats([x for x in adj_overlap if x > 0]),
            "structure_counts": dict(sorted(cut_counts.items()))}
        if doc_tokens is not None:
            chunk_summary[name]["document_tokens_total"] = doc_tokens
    base_total = chunk_summary["adopted"]["tokens_total"]
    for name in CONFIGS:
        chunk_summary[name]["tokens_total_vs_adopted"] = round(chunk_summary[name]["tokens_total"] / base_total, 4)

    # gate: the adopted chunking reproduces the confirmatory control chunks
    mism = [d for d in docs if [(s, e, i) for s, e, i in chunked["adopted"][d]] !=
            [(r["start_char"], r["end_char"], r["chunk_id"]) for r in cdocs[d]]]
    if mism:
        raise SystemExit(f"REFUSED: the adopted chunking does not reproduce the control chunks on {len(mism)} of "
                         f"{len(docs)} documents (environment differs from the confirmatory one)")
    checks["adopted_documents_reproduced"] = len(docs)

    # sentence units per document (sentence-span starts + every adopted cut)
    bounds: dict[str, list[int]] = {}
    from ragstack.ingestion.chunkers import sentence_spans
    for d in docs:
        b = {s for s, _e in sentence_spans(texts[d])} | {s for s, _e, _ in chunked["adopted"][d]} | {len(texts[d])}
        bounds[d] = sorted(b)

    # per pair, per config
    res: dict[str, dict[str, Counter[str]]] = {name: defaultdict(Counter) for name in CONFIGS}
    cov: dict[str, dict[str, list[float]]] = {name: defaultdict(list) for name in CONFIGS}
    new_items: dict[str, dict[str, set[tuple[str, str]]]] = {name: defaultdict(set) for name in CONFIGS}
    over: dict[str, dict[str, list[int]]] = {name: defaultdict(list) for name in CONFIGS}
    cuts_info: dict[str, Counter[str]] = defaultdict(Counter)
    ncuts: dict[str, list[int]] = defaultdict(list)
    wtok: dict[str, list[int]] = defaultdict(list)
    nb: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    nb_tok: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    nb_items: dict[str, dict[str, set[tuple[str, int, int]]]] = defaultdict(lambda: defaultdict(set))
    for p in pairs:
        d, ws, we, g = p["doc"], p["ws"], p["we"], p["group"]
        text = texts[d]
        a_spans = [(s, e) for s, e, _ in chunked["adopted"][d]]
        cuts = interior_cuts(a_spans, ws, we)
        ncuts[g].append(len(cuts))
        wtok[g].append(counter.count(text[ws:we]))
        walls = [c for c in cuts if c in seg_edges[d]]
        cuts_info[g]["pairs"] += 1
        cuts_info[g]["cuts"] += len(cuts)
        cuts_info[g]["cuts_section_wall"] += len(walls)
        cuts_info[g]["cuts_size"] += len(cuts) - len(walls)
        cuts_info[g]["pairs_with_a_wall_cut"] += bool(walls)
        cuts_info[g]["pairs_only_wall_cuts"] += bool(cuts) and len(walls) == len(cuts)
        cuts_info[g]["pairs_only_size_cuts"] += bool(cuts) and not walls
        cuts_info[g]["pairs_no_cut"] += not cuts
        cuts_info[g]["window_crosses_segment_edge"] += any(ws < x < we for x in seg_edges[d])
        if len(cuts) + 1 != p["n_overlapping_seed"]:
            raise SystemExit("REFUSED: interior cuts disagree with pairs-seed's n_overlapping")
        for name in CONFIGS:
            spans = [(s, e) for s, e, _ in chunked[name][d]]
            r = res[name][g]
            r["pairs"] += 1
            r["contain"] += covers(spans, ws, we)
            bc = best_cov(spans, ws, we)
            cov[name][g].append(bc)
            r["best_cov_ge_0.8"] += bc >= 0.8
            hits = [(acc.overlap(ws, we, s, e), s, e, cid) for s, e, cid in chunked[name][d]]
            hits = [h for h in hits if h[0] > 0]
            over[name][g].append(len(hits))
            for k in KS:
                ok = all(covers(spans, *bridge_need(bounds[d], c, k, ws, we)) for c in cuts)
                r[f"all_cuts_bridged_k{k}"] += ok
                r[f"cuts_bridged_k{k}"] += sum(covers(spans, *bridge_need(bounds[d], c, k, ws, we)) for c in cuts)
                r[f"size_cuts_bridged_k{k}"] += sum(covers(spans, *bridge_need(bounds[d], c, k, ws, we))
                                                    for c in cuts if c not in seg_edges[d])
                r[f"wall_cuts_bridged_k{k}"] += sum(covers(spans, *bridge_need(bounds[d], c, k, ws, we))
                                                    for c in walls)
            kept = sorted(hits, key=lambda h: (-h[0], h[1]))[:acc.COVER_ANY_CAP]
            for _ov, _s, _e, cid in kept:
                if (p["rid"], cid) not in labelled:
                    new_items[name][g].add((p["rid"], cid))
        # neighbour passages on the adopted chunks
        ids = [cid for _s, _e, cid in chunked["adopted"][d]]
        m = ids.index(p["max_id"])
        for k in NEIGHBOUR_KS:
            lo, hi = max(0, m - k), min(len(ids) - 1, m + k)
            s0, e0 = a_spans[lo][0], a_spans[hi][1]
            key = f"context_window_{k}"
            nb[key][g]["pairs"] += 1
            nb[key][g]["contain"] += s0 <= ws and e0 >= we
            nb[key][g]["all_cuts_inside"] += all(s0 < c < e0 for c in cuts)
            nb[key][g]["crosses_section_wall"] += any(s0 < x < e0 for x in seg_edges[d])
            nb[key][g]["best_cov_ge_0.8"] += acc.overlap(ws, we, s0, e0) / (we - ws) >= 0.8
            if (s0, e0) == a_spans[m]:
                nb[key][g]["passage_is_max_overlap_chunk"] += 1  # no neighbour: M's own label is reused
            else:
                nb_tok[key][g].append(counter.count(text[s0:e0]))
                nb_items[key][g].add((p["rid"], s0, e0))
        ov_idx = [i for i, (s, e) in enumerate(a_spans) if acc.overlap(ws, we, s, e) > 0]
        nb["adjacent_pairs"][g]["pairs"] += 1
        nb["adjacent_pairs"][g]["contain"] += len(ov_idx) <= 2
        nb["adjacent_pairs"][g]["all_cuts_inside"] += 1  # every interior cut lies inside one adjacent pair
        for i, j in zip(ov_idx, ov_idx[1:], strict=False):
            nb_tok["adjacent_pairs"][g].append(counter.count(text[a_spans[i][0]:a_spans[j][1]]))
            nb_items["adjacent_pairs"][g].add((p["rid"], a_spans[i][0], a_spans[j][1]))

    def rows_for(name: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for g in GROUPS:
            r = dict(res[name][g])
            r["best_cov"] = stats([round(x, 4) for x in cov[name][g]])
            r["chunks_overlapping_window"] = stats(over[name][g])
            r["new_judge_items"] = len(new_items[name][g])
            out[g] = r
        return out

    per_config = {name: rows_for(name) for name in CONFIGS}
    for name in CONFIGS:
        allg = set().union(*(new_items[name][g] for g in GROUPS))
        per_config[name]["new_judge_items_all_groups"] = len(allg)
        per_config[name]["new_judge_items_split_recovered_kept_changed"] = len(
            new_items[name]["split"] | new_items[name]["recovered"] | new_items[name]["kept_changed"])
    neighbours = {key: {g: {**dict(nb[key][g]), "passage_tokens": stats(nb_tok[key][g]),
                            "new_judge_items": len(nb_items[key][g])} for g in GROUPS}
                  for key in [*(f"context_window_{k}" for k in NEIGHBOUR_KS), "adjacent_pairs"]}

    rec: dict[str, Any] = {
        "schema": SCHEMA, "exploratory": True, "offline": True,
        "calls": {"store": 0, "retrieval": 0, "llm": 0},
        "groups": {g: groups[g] for g in GROUPS},
        "checks": dict(checks),
        "tokenizer": acc.TOKENIZER, "model_window": acc.MODEL_WINDOW, "cover_any_cap": acc.COVER_ANY_CAP,
        "label_rows": len(labelled),
        "adopted_cuts_in_window": {g: {**dict(cuts_info[g]), "per_pair": stats(ncuts[g])} for g in GROUPS},
        "window_tokens": {g: stats(wtok[g]) for g in GROUPS},
        "chunkings": chunk_summary,
        "per_chunking": per_config,
        "neighbour_passages": neighbours,
        "definitions": {
            "contain": "one chunk holds the whole stage-A window",
            "best_cov": "largest share of the window's characters held by one chunk",
            "all_cuts_bridged_kN": "every adopted cut inside the window lies inside one chunk with >= N sentence "
                                   "units of the window on each side (clipped to the window)",
            "section_wall": "a cut at a doc_cut_segments edge; overlap under structure_cuts restarts there",
            "new_judge_items": "distinct (record, chunk) items among the <= cover_any_cap chunks overlapping the "
                               "window with no label row in the rechunk label table",
            "context_window_k": "the max-overlap adopted chunk with k neighbours each side in document order; a "
                                "passage equal to that chunk (a one-chunk document) reuses its label and is not "
                                "a new item; passage_tokens counts the new passages only",
            "adjacent_pairs": "every pair of adjacent adopted chunks overlapping the window",
            "tokens": "SFR-Embedding-Mistral tokenizer (HF), joined text"},
        "environment": env,
        "inputs_sha256": {"pairs_seed": asc.sha256_file(confirm_dir / "run" / "pairs-seed.jsonl"),
                          "pairs_seed_cover": asc.sha256_file(confirm_dir / "run" / "pairs-seed-cover.jsonl"),
                          "chunks_control": asc.sha256_file(confirm_dir / "run" / "chunks-control.jsonl"),
                          "chunks_retest": asc.sha256_file(confirm_dir / "retest" / "chunks-retest.jsonl"),
                          "label_table": asc.sha256_file(label_table)},
        "provenance": acc.public_provenance(acc.provenance(
            [texts[d] for d in docs], None, [[(s, e) for s, e, _ in chunked["adopted"][d]] for d in docs])),
    }
    forbidden = set(docs) | {p["pid"] for p in pairs} | {p["rid"] for p in pairs} | \
        {cid for name in CONFIGS for d in docs for _s, _e, cid in chunked[name][d]} | set(windows)
    write_public(out, rec, forbidden, [texts[d] for d in docs])
    return rec


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--confirm-dir", type=Path, required=True)
    ap.add_argument("--label-table", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    rec = run(a.confirm_dir, a.label_table, a.out)
    print(json.dumps({"groups": rec["groups"], "checks": rec["checks"],
                      "split": {n: {k: rec["per_chunking"][n]["split"].get(k) for k in
                                    ("contain", "all_cuts_bridged_k1", "all_cuts_bridged_k2", "new_judge_items")}
                                for n in CONFIGS}}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
