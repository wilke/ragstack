#!/usr/bin/env python3
"""Feasibility counts for the reach comparison (PREREG.md §2, §3, §5, §8, §10). Counts only.

    python -I -B feasibility.py --records ACCEPTED.jsonl --oa-chunks CHUNKS-OA.jsonl \\
        --corpus-dir CORPUS_DIR --runs-dir RUNS_DIR --judge-final FINAL.jsonl \\
        --main-pairs MAIN_JUDGE/pairs.jsonl --s-export ARM=PATH [...] \\
        --s-prior-final NAME=FINAL.jsonl [...] --s-prior-pairs NAME=PAIRS.jsonl [...] \\
        --s-stage1-runs STAGE1_RUNS_DIR [--out feasibility.json]

The committed scorer ``python/scripts/eval/reach_quote.py`` (the function that will score W and S)
and ``reach_report.read_registry`` (the registry reader the report gates on), both imported from
this checkout; run it with the ragstack environment's Python (3.12). Reads the W-side inputs: the
accepted records, the W chunk exports (the base export plus
``chunks-{neighbour,random-1,random-2}-v4.jsonl`` in CORPUS_DIR; each corpus collection is the base
export plus its own added documents), the alias study's stage-A retrieval runs
``RUNS_DIR/<corpus>/none.jsonl``, the alias judge registry (``final.jsonl``; stage A and stage B
rows, keyed by record_id and chunk_id) and its ``pairs.jsonl`` (``--main-pairs``: which records
have a stage-A pair). On the S side it reads **only the S chunk exports** (``--s-export``: chunk
texts, for the reachability check of §2) and the **prior S registries' label files**
(``--s-prior-final``: the integrity check of §3) and their ``pairs.jsonl`` (``--s-prior-pairs``: the
prior-content check of §3), and the rechunk stage-1 S run **summaries** only
(``--s-stage1-runs``: ``<corpus>/none.summary.json``, which hold no per-record outcome in rechunk mode;
the settings gate of §7.1 run on real files). **No S retrieval output, no S run row and no S outcome
is read** (the prereg's blinding; the reach runs do not exist yet). Paths are arguments, never written: the output holds counts, rates and
input sha256 digests only, and :func:`assert_no_identifiers` refuses to write any string that looks
like a path, host, record, paper or chunk id.

Outputs (``feasibility.json``):

* ``eligibility``: records; quotes that are verbatim substrings of their gold chunk, raw and after
  whitespace normalisation (``reach_quote.is_verbatim``); **S reachability** of the W-verbatim
  quotes (``quote_cover(quote, every S chunk of the gold document) >= 0.90``; ``split``: reached only
  by the union of two or more S chunks, the quote crosses an S chunk boundary); the Q-eligible set
  (W-verbatim and S-reachable) and its paper-cluster size histogram (the counts ``power.py``
  embeds).
* ``orphans``: records without a stage-A pair in the main registry (``build-control`` cannot take
  an S pair of theirs, PREREG §3), and a W-side proxy of what they carry: W lists without a quote
  hit, and the unique pairs in them, on orphan records per retriever.
* ``registry_integrity``: per registry (the W registry and each prior S registry), the rows, the
  distinct keys and the problems ``reach_report.read_registry`` finds (malformed rows, forged pair
  ids, conflicting labels within the registry); for the prior S registries read together, the keys
  on which two registries' judged labels differ (combined answers-if-any, declared, not gated).
* ``prior_content``: PREREG §3's check, run on every prior S key: per prior registry against its own
  ``pairs.jsonl``, and combined (every key against all three registries' rows, as ``report`` reads
  them), the keys whose ``content_sha256`` is the sha256 of the S export's text of the chunk
  (``match``), another sha (``mismatch``), no S export row (``no_s_text``), no pairs row
  (``no_pairs_row``); all keys and the keys with a judged label (``reach_report.prior_content_status``).
* ``settings_gates``: ``reach_report.settings_gates`` on real summaries: the W summaries against
  themselves (the gate's own fields exist and agree), and the stage-1 S summaries against the W ones
  (the gate the reach run will face, on the stage-1 run of the same config but for ``out_dir`` and the
  rechunk keys, §2); with, for the latter, the names of the summary fields the gate does not compare
  that differ or exist on one side only (names only; no value is written).
* ``w_side[corpus][retriever]``: lists; quote@10 hits (``reach_quote.quote_hit`` on the top-10 texts),
  for all records and for the Q-eligible ones; lists without a hit; top-10 slots in those lists;
  how many of those slots already carry a final label in the registry (by (record_id, chunk_id));
  and the same for unique (record, chunk) pairs.
* ``w_side_totals``: the same summed, and the unique (record, chunk) pairs over all corpora and
  retrievers (a chunk of a base document has one id in every corpus, so pairs repeat).
* ``s_side_estimate``: the S judging load assumed equal to W's: (lists without a hit) x 10 per
  retriever, as slots and, scaled by W's unique-per-slot ratio, as unique pairs; expected Argon calls
  at 7 / 12 / 17 % escalation and Opus drift calls at 5 %, both of the unique new pairs.
* ``w_miss_correlation``: W-only proxies for the power simulation's within-paper correlation:
  the pairwise correlation of the default retriever's quote@10 miss indicator between two lists of
  the same record in different corpora, and between lists of different records of the same paper.
  (The power simulation needs the correlation of d = S - W, which is not observable before S; this
  is an indication only.)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

_EVAL = Path(__file__).resolve().parents[4] / "python" / "scripts" / "eval"
#: this checkout's eval scripts and its ``ragstack`` package (``reach_report``'s imports need both;
#: ``-I`` ignores PYTHONPATH)
for _p in (_EVAL.parents[1], _EVAL):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from reach_quote import HIT_THRESHOLD, MIN_MATCH, is_verbatim, quote_cover, quote_hit  # noqa: E402

S_ARMS = ("base", "neighbour", "random1", "random2")
#: PREREG §3: the prior S registries reused by (record, chunk) key
PRIOR_NAMES = ("control", "cover_any", "stage1b")

CORPORA = ("base", "neighbour", "random1", "random2")
CORPUS_FILES = {"neighbour": "chunks-neighbour-v4.jsonl", "random1": "chunks-random-1-v4.jsonl",
                "random2": "chunks-random-2-v4.jsonl"}
RETRIEVERS = {"base": "default", "norerank": "norerank", "bm25": "bm25"}  # run condition -> prereg name
TOP_K = 10
ESCALATION = (0.07, 0.12, 0.17)
OPUS_RATE = 0.05

_FORBIDDEN = [re.compile(p) for p in (
    r"(?:^|[\s\"'=(])[~.]?/", r"\w+/\w+/\w", r"\\", r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", r"PMC\d", r"alias-O-",
    r"coconut", r"mango", r"\.jsonl", r"\.env", r"\btmp\b")]


def assert_no_identifiers(obj: object, where: str = "$") -> None:
    """Refuse any string (key or value) that looks like a path, host or id."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            assert_no_identifiers(k, f"{where}.<key>")
            assert_no_identifiers(v, f"{where}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            assert_no_identifiers(v, f"{where}[{i}]")
    elif isinstance(obj, str):
        for pat in _FORBIDDEN:
            if pat.search(obj):
                raise SystemExit(f"refusing to write {where}: matches {pat.pattern!r}")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def git_state(here: Path) -> dict[str, object]:
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=here, capture_output=True, text=True,
                                check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=here, capture_output=True, text=True,
                               check=True).stdout.strip() != ""
        return {"commit": commit, "dirty": dirty}
    except Exception:  # noqa: BLE001 - provenance is best effort outside a checkout
        return {"commit": None, "dirty": None}


def read_jsonl(p: Path):
    with p.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def pairwise_corr(groups: list[list[int]]) -> dict[str, object]:
    """Pairwise within-group Pearson correlation of a 0/1 indicator (pooled mean and variance):
    sum over groups of sum_{i != j} (y_i - m)(y_j - m) / (number of ordered pairs * var)."""
    ys = [y for g in groups for y in g]
    n = len(ys)
    if n == 0:
        return {"r": None, "ordered_pairs": 0, "units": 0}
    m = sum(ys) / n
    var = sum((y - m) ** 2 for y in ys) / n
    num = 0.0
    npairs = 0
    for g in groups:
        s = sum(y - m for y in g)
        ss = sum((y - m) ** 2 for y in g)
        num += s * s - ss
        npairs += len(g) * (len(g) - 1)
    r = None if npairs == 0 or var == 0 else round(num / (npairs * var), 4)
    return {"r": r, "ordered_pairs": npairs, "units": n, "mean": round(m, 4)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--records", type=Path, required=True)
    ap.add_argument("--oa-chunks", type=Path, required=True)
    ap.add_argument("--corpus-dir", type=Path, required=True)
    ap.add_argument("--runs-dir", type=Path, required=True)
    ap.add_argument("--judge-final", type=Path, required=True)
    ap.add_argument("--main-pairs", type=Path, required=True, help="the alias main judge registry's pairs.jsonl")
    ap.add_argument("--s-export", action="append", required=True, help="ARM=PATH (repeatable): the S chunk exports")
    ap.add_argument("--s-prior-final", action="append", default=[],
                    help=f"NAME=PATH (repeatable), NAME one of {list(PRIOR_NAMES)}: a prior S registry's final.jsonl")
    ap.add_argument("--s-prior-pairs", action="append", default=[],
                    help=f"NAME=PATH (repeatable), NAME one of {list(PRIOR_NAMES)}: that registry's pairs.jsonl")
    ap.add_argument("--s-stage1-runs", type=Path, required=True,
                    help="the rechunk stage-1 runs dir: only <corpus>/none.summary.json is read")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "feasibility.json")
    a = ap.parse_args(argv)
    here = Path(__file__).resolve()
    state = git_state(here.parent)

    def named(specs: list[str], names: tuple[str, ...], flag: str) -> dict[str, Path]:
        out: dict[str, Path] = {}
        for spec in specs:
            k, sep, v = spec.partition("=")
            if not sep or k not in names or not v or k in out:
                raise SystemExit(f"{flag} {spec!r}: NAME=PATH, NAME one of {list(names)}, each once")
            out[k] = Path(v)
        return out
    s_exports = named(a.s_export, S_ARMS, "--s-export")
    priors = named(a.s_prior_final, PRIOR_NAMES, "--s-prior-final")
    prior_pairs = named(a.s_prior_pairs, PRIOR_NAMES, "--s-prior-pairs")
    if set(prior_pairs) != set(priors):
        raise SystemExit("--s-prior-pairs: one pairs.jsonl per --s-prior-final registry")

    inputs = {"records": a.records, "oa_chunks": a.oa_chunks, "judge_final": a.judge_final,
              "main_pairs": a.main_pairs}
    inputs.update({f"s_export_{k}": v for k, v in s_exports.items()})
    inputs.update({f"s_prior_final_{k}": v for k, v in priors.items()})
    inputs.update({f"corpus_{c}": a.corpus_dir / f for c, f in CORPUS_FILES.items()})
    inputs.update({f"s_prior_pairs_{k}": v for k, v in prior_pairs.items()})
    inputs.update({f"runs_{c}": a.runs_dir / c / "none.jsonl" for c in CORPORA})
    inputs.update({f"runs_summary_{c}": a.runs_dir / c / "none.summary.json" for c in CORPORA})
    inputs.update({f"s_stage1_summary_{c}": a.s_stage1_runs / c / "none.summary.json" for c in CORPORA})
    import reach_report as rr
    prior_regs = {k: rr.read_registry([priors[k]], f"S prior registry {k}") for k in PRIOR_NAMES if k in priors}
    prior_cids = {cid for reg in prior_regs.values() for _, cid in reg.labels}

    recs = {r["record_id"]: r for r in read_jsonl(a.records)}
    runs: dict[str, list[dict]] = {c: list(read_jsonl(inputs[f"runs_{c}"])) for c in CORPORA}
    needed = {cid for c in CORPORA for row in runs[c] for cid in row["ranked_chunk_ids"][:TOP_K]}
    needed |= {r["gold_chunks"][0] for r in recs.values()}

    gold = {r["gold_chunks"][0] for r in recs.values()}
    base_text: dict[str, str] = {}
    gold_doc: dict[str, str] = {}
    for d in read_jsonl(a.oa_chunks):
        if d["chunk_id"] in needed:
            base_text[d["chunk_id"]] = d["content"]
            if d["chunk_id"] in gold:
                gold_doc[d["chunk_id"]] = d["doc_id"]
    added: dict[str, dict[str, str]] = {}
    for c, fn in CORPUS_FILES.items():
        added[c] = {d["chunk_id"]: d["content"] for d in read_jsonl(a.corpus_dir / fn) if d["chunk_id"] in needed}

    def text_of(corpus: str, cid: str) -> str | None:
        if cid in base_text:
            return base_text[cid]
        return added.get(corpus, {}).get(cid)

    # eligibility (PREREG §2, §11 Q4)
    verb_raw = {rid for rid, r in recs.items() if r["quote"] in base_text.get(r["gold_chunks"][0], "")}
    verb_norm = {rid for rid, r in recs.items() if is_verbatim(r["quote"], base_text.get(r["gold_chunks"][0], ""))}

    # S reachability (PREREG §2): the S chunk exports only (chunk texts; W and S share document ids)
    docs = set(gold_doc.values())
    s_doc_texts: dict[str, dict[str, str]] = defaultdict(dict)
    s_prior_text: dict[str, str] = {}
    for k in S_ARMS:
        if k not in s_exports:
            continue
        for d in read_jsonl(s_exports[k]):
            if d["chunk_id"] in prior_cids:
                if s_prior_text.setdefault(d["chunk_id"], d["content"]) != d["content"]:
                    raise SystemExit("an S chunk id with two contents in the S exports")
            if d["doc_id"] in docs:
                prev = s_doc_texts[d["doc_id"]].setdefault(d["chunk_id"], d["content"])
                if prev != d["content"]:
                    raise SystemExit("an S chunk id with two contents in the S exports")
    reach: dict[str, str] = {}
    for rid in verb_norm:
        r = recs[rid]
        texts = list(s_doc_texts.get(gold_doc.get(r["gold_chunks"][0], ""), {}).values())
        if not texts or quote_cover(r["quote"], texts) < HIT_THRESHOLD:
            reach[rid] = "unreachable"
        elif any(quote_cover(r["quote"], [t]) >= HIT_THRESHOLD for t in texts):
            reach[rid] = "single"
        else:
            reach[rid] = "split"
    eligible = {rid for rid in verb_norm if reach[rid] != "unreachable"}
    paper_n = Counter(recs[rid]["paper"] for rid in eligible)
    paper_n_raw = Counter(recs[rid]["paper"] for rid in verb_raw)
    paper_n_all = Counter(r["paper"] for r in recs.values())

    # orphans (PREREG §3): records without a stage-A pair in the main registry
    stage_a: set[str] = set()
    stage_b: set[str] = set()
    for d in read_jsonl(a.main_pairs):
        (stage_a if d.get("stage", "A") == "A" else stage_b).add(d["record_id"])
    orphan = {rid for rid in recs if rid not in stage_a}
    orph_lists: dict[str, Counter] = {r: Counter() for r in RETRIEVERS.values()}
    orph_pairs: dict[str, set[tuple[str, str]]] = {r: set() for r in RETRIEVERS.values()}

    labelled: set[tuple[str, str]] = set()
    label_rows = 0
    for d in read_jsonl(a.judge_final):
        if d.get("record_id") and d.get("chunk_id") and d.get("label") is not None:
            labelled.add((d["record_id"], d["chunk_id"]))
            label_rows += 1

    w: dict[str, dict[str, dict[str, object]]] = {}
    unresolved = 0
    all_nohit_pairs: set[tuple[str, str]] = set()
    nohit_pairs_by_ret: dict[str, set[tuple[str, str]]] = defaultdict(set)
    miss_default: dict[tuple[str, str], int] = {}  # (record, corpus) -> 1 if no hit (eligible, default)
    for c in CORPORA:
        w[c] = {}
        per: dict[str, dict[str, int]] = {r: Counter() for r in RETRIEVERS.values()}
        per_pairs: dict[str, set[tuple[str, str]]] = {r: set() for r in RETRIEVERS.values()}
        seen: set[tuple[str, str]] = set()
        for row in runs[c]:
            ret = RETRIEVERS[row["condition"]]
            rid = row["record_id"]
            if (rid, ret) in seen:
                raise SystemExit(f"duplicate run row in corpus {c}")
            seen.add((rid, ret))
            if row.get("failed"):
                per[ret]["failed"] += 1
                continue
            top = row["ranked_chunk_ids"][:TOP_K]
            texts = []
            for cid in top:
                t = text_of(c, cid)
                if t is None:
                    unresolved += 1
                else:
                    texts.append(t)
            hit = quote_hit(recs[rid]["quote"], texts)
            if rid in orphan:
                orph_lists[ret]["lists"] += 1
                orph_lists[ret]["lists_without_hit"] += not hit
                if not hit:
                    orph_pairs[ret].update((rid, cid) for cid in top)
            p = per[ret]
            p["lists"] += 1
            p["hits"] += hit
            p["short_lists"] += len(top) < TOP_K
            if rid in eligible:
                p["lists_eligible"] += 1
                p["hits_eligible"] += hit
                if ret == "default":
                    miss_default[(rid, c)] = 0 if hit else 1
            if not hit:
                p["lists_without_hit"] += 1
                p["slots"] += len(top)
                lab = sum((rid, cid) in labelled for cid in top)
                p["slots_labelled"] += lab
                for cid in top:
                    per_pairs[ret].add((rid, cid))
                    nohit_pairs_by_ret[ret].add((rid, cid))
                    all_nohit_pairs.add((rid, cid))
        for ret in RETRIEVERS.values():
            p = dict(per[ret])
            pairs = per_pairs[ret]
            p["unique_pairs"] = len(pairs)
            p["unique_pairs_labelled"] = sum(k in labelled for k in pairs)
            p["unique_pairs_unlabelled"] = p["unique_pairs"] - p["unique_pairs_labelled"]
            p["quote10"] = round(p["hits"] / p["lists"], 4) if p.get("lists") else None
            p["quote10_eligible"] = round(p["hits_eligible"] / p["lists_eligible"], 4) if p.get("lists_eligible") else None
            for k in ("hits", "hits_eligible", "lists_without_hit", "slots", "slots_labelled", "short_lists", "failed"):
                p.setdefault(k, 0)
            w[c][ret] = p

    tot: dict[str, dict[str, int]] = {}
    for ret in RETRIEVERS.values():
        t = Counter()
        for c in CORPORA:
            for k in ("lists", "hits", "lists_without_hit", "slots", "slots_labelled"):
                t[k] += int(w[c][ret][k])  # type: ignore[arg-type]
        t["unique_pairs_over_corpora"] = len(nohit_pairs_by_ret[ret])
        t["unique_pairs_over_corpora_labelled"] = sum(k in labelled for k in nohit_pairs_by_ret[ret])
        tot[ret] = dict(t)
    grand_slots = sum(v["slots"] for v in tot.values())
    grand_lab = sum(v["slots_labelled"] for v in tot.values())
    uniq = len(all_nohit_pairs)
    uniq_lab = sum(k in labelled for k in all_nohit_pairs)
    totals = {"by_retriever": tot, "slots": grand_slots, "slots_labelled": grand_lab,
              "slots_unlabelled": grand_slots - grand_lab,
              "unique_pairs": uniq, "unique_pairs_labelled": uniq_lab, "unique_pairs_unlabelled": uniq - uniq_lab,
              "unique_per_slot": round(uniq / grand_slots, 4) if grand_slots else None}

    # S-side estimate: same miss rate as W, so the same number of lists without a hit, x 10 slots
    ratio = uniq / grand_slots if grand_slots else 0.0
    s_by = {ret: {"lists_without_hit": v["lists_without_hit"], "slots": v["lists_without_hit"] * TOP_K}
            for ret, v in tot.items()}
    s_slots = sum(v["slots"] for v in s_by.values())
    s_unique = round(s_slots * ratio)
    w_new = uniq - uniq_lab
    calls = {}
    for label, n_new in (("s_side_unique_new", s_unique), ("w_side_unique_unlabelled", w_new),
                         ("both", s_unique + w_new)):
        calls[label] = {"pairs": n_new,
                        "argon_calls": {f"{int(e * 100)}%": round(n_new * e) for e in ESCALATION},
                        "opus_calls_5%": round(n_new * OPUS_RATE)}
    d_slots = tot["default"]["lists_without_hit"] * TOP_K
    d_ratio = tot["default"]["unique_pairs_over_corpora"] / tot["default"]["slots"] if tot["default"]["slots"] else 0.0
    d_unique = round(d_slots * d_ratio)
    calls["s_side_default_retriever_only"] = {
        "pairs": d_unique, "argon_calls": {f"{int(e * 100)}%": round(d_unique * e) for e in ESCALATION},
        "opus_calls_5%": round(d_unique * OPUS_RATE),
        "note": "if J were judged for the default retriever only (owner Q3 chose all three)"}
    s_est = {"assumption": "S lists without a quote hit = W's, per retriever and corpus; 10 slots each; "
                           "S unique pairs = S slots x W's unique-per-slot ratio; no reuse of W labels counted for "
                           "S (the content-hash rule can only lower this)",
             "by_retriever": s_by, "slots": s_slots, "unique_per_slot_from_w": round(ratio, 4),
             "unique_pairs_est": s_unique,
             "qwen_judge_calls_est": {"s_side": 2 * s_unique, "w_side": 2 * w_new,
                                      "note": "C and E each judge every new pair"},
             "escalation_rates": list(ESCALATION), "opus_rate": OPUS_RATE, "calls": calls}

    # W-only correlation proxies (default retriever, Q-eligible)
    by_rec: dict[str, list[int]] = defaultdict(list)
    for (rid, _c), y in miss_default.items():
        by_rec[rid].append(y)
    by_paper_rec: dict[str, list[list[int]]] = defaultdict(list)
    for rid, ys in by_rec.items():
        by_paper_rec[recs[rid]["paper"]].append(ys)
    same_record = pairwise_corr(list(by_rec.values()))
    # different records of the same paper: all pairs within paper minus same-record pairs
    ys_all = [y for ys in by_rec.values() for y in ys]
    m = sum(ys_all) / len(ys_all)
    var = sum((y - m) ** 2 for y in ys_all) / len(ys_all)
    num = 0.0
    npairs = 0
    for recs_ys in by_paper_rec.values():
        sums = [sum(y - m for y in ys) for ys in recs_ys]
        tot_s = sum(sums)
        num += tot_s * tot_s - sum(s * s for s in sums)
        sizes = [len(ys) for ys in recs_ys]
        npairs += sum(sizes) ** 2 - sum(s * s for s in sizes)
    cross_record = {"r": round(num / (npairs * var), 4) if npairs and var else None, "ordered_pairs": npairs}
    corr = {"indicator": "default retriever quote@10 miss on W, Q-eligible records",
            "miss_rate": round(m, 4), "same_record_other_corpus": {k: same_record[k] for k in ("r", "ordered_pairs")},
            "other_record_same_paper": cross_record}

    # orphans: the W-side proxy per retriever (all corpora pooled)
    orphans = {
        "records": len(orphan), "eligible_records": len(orphan & eligible),
        "records_with_stage_b_pairs_only": len(orphan & stage_b), "records_with_no_main_pair": len(orphan - stage_b),
        "papers": len({recs[rid]["paper"] for rid in orphan}),
        "w_proxy_by_retriever": {
            ret: {"lists": orph_lists[ret]["lists"], "lists_without_hit": orph_lists[ret]["lists_without_hit"],
                  "lists_without_hit_all_records": tot[ret]["lists_without_hit"],
                  "unique_pairs_over_corpora": len(orph_pairs[ret]),
                  "unique_pairs_over_corpora_all_records": tot[ret]["unique_pairs_over_corpora"]}
            for ret in RETRIEVERS.values()},
        "primary_cell_units_of_eligible_orphan_records": 4 * len(orphan & eligible),
        "note": "an S pair of an orphan record cannot be seeded (build-control needs a stage-A pair of the record as "
                "control_of) and counts as not answering and as unjudged (PREREG §3)"}

    # the prior-content check (PREREG §3): every prior key's content_sha256 against the S export's text
    pairs_rows = {k: list(read_jsonl(v)) for k, v in prior_pairs.items()}

    def content(keys: dict[tuple[str, str], str], rows: list[dict]) -> dict[str, object]:
        sha_of = rr.prior_content_sha_index(rows)
        allk: Counter = Counter()
        jud: Counter = Counter()
        for key, word in keys.items():
            st = rr.prior_content_status(key, sha_of, s_prior_text.get)
            allk[st] += 1
            if rr.judged(word):
                jud[st] += 1
        z = ("match", "mismatch", "no_s_text", "no_pairs_row")
        return {"keys": len(keys), **{k: allk[k] for k in z},
                "judged_keys": sum(jud.values()), "judged": {k: jud[k] for k in z}}
    prior_combined = rr.read_registry([priors[k] for k in PRIOR_NAMES if k in priors], "S prior registries")
    prior_content = {
        "per_registry": {k: content(reg.labels, pairs_rows[k]) for k, reg in prior_regs.items()},
        "combined": content(prior_combined.labels, [r for k in PRIOR_NAMES if k in pairs_rows for r in pairs_rows[k]]),
        "rule": "match: the pairs rows' content_sha256 is exactly sha256 of the S export's text of the chunk; a "
                "judged label that does not match is not reused and counts as unjudged, a key with no pairs row is "
                "counted (caveats on J, PREREG §3)"}

    # the settings gate (PREREG §7.1) on real summaries: W vs W, stage-1 S vs W (summaries only)
    w_sum = rr.read_summaries(a.runs_dir)
    s1_sum = rr.read_summaries(a.s_stage1_runs)
    for c in CORPORA:
        oc = [v.get("outcomes") for v in (s1_sum.summaries[c].get("conditions") or {}).values()]
        if any(not isinstance(x, (str, type(None))) for x in oc) or any(
                "found10" in (v or {}) for v in (s1_sum.summaries[c].get("conditions") or {}).values()):
            raise SystemExit("REFUSED: a stage-1 S summary carries per-condition outcomes (blinding); stop")

    def flat(d: object, pre: str = "") -> dict[str, object]:
        if isinstance(d, dict):
            out_: dict[str, object] = {}
            for k_, v_ in d.items():
                out_.update(flat(v_, f"{pre}.{k_}" if pre else str(k_)))
            return out_
        return {pre: d}

    def field_name(k_: str) -> str:
        k_ = re.sub(r"^conditions\.[^.]+\.", "conditions.<condition>.", k_)
        k_ = re.sub(r"stamps\.[^.]+$", "stamps.<stamp>", k_)
        return re.sub(r"^study\.per_leg_failed\.[^.]+\.", "study.per_leg_failed.<leg>.", k_)
    compared_prefixes = ("conditions.<condition>.request", "conditions.<condition>.requested", *rr.SETTINGS_SAME,
                         "study.config_sha256", "server.config_sha256")
    uncmp: dict[str, set[str]] = defaultdict(set)
    for c in CORPORA:
        fw, fs = flat(w_sum.summaries[c]), flat(s1_sum.summaries[c])
        for k_ in set(fw) | set(fs):
            st = "w_only" if k_ not in fs else "s_only" if k_ not in fw else None if fw[k_] == fs[k_] else "differs"
            name = field_name(k_)
            if st and not name.startswith(compared_prefixes):
                uncmp[st].add(name)

    def gate_rec(w_: object, s_: object) -> dict[str, object]:
        g, info = rr.settings_gates(w_, s_)  # type: ignore[arg-type]
        return {"gates": g, "per_corpus": info["per_corpus"], "compared": info["compared"]}
    settings = {"w_vs_w": gate_rec(w_sum, w_sum), "stage1_s_vs_w": gate_rec(w_sum, s1_sum),
                "stage1_s_vs_w_fields_not_compared": {k: sorted(v) for k, v in sorted(uncmp.items())},
                "expected_differences": list(rr.SETTINGS_EXPECTED),
                "note": "stage-1 S: the rechunk stage-1 runs (the reach config but for out_dir and the rechunk keys, "
                        "PREREG §2); summaries only, no per-record outcome in rechunk mode"}

    # registry integrity (PREREG §3, §7.1): the reader the report gates on, per registry
    def integ(reg: object) -> dict[str, object]:
        return {"rows": reg.rows, "keys": len(reg.labels), "problems": list(reg.problems)}  # type: ignore[attr-defined]
    integrity: dict[str, object] = {"W": integ(rr.read_registry([a.judge_final], "W registry"))}
    integrity["s_prior"] = {k: integ(reg) for k, reg in prior_regs.items()}
    combined = prior_combined
    integrity["s_prior_combined"] = {"keys": len(combined.labels), "within_registry_problems": list(combined.problems),
                                     "cross_registry_conflicts": combined.cross_conflicts,
                                     "rule": "answers if any registry says answers (declared, not gated)"}

    out = {
        "schema": "reach-rechunk-feasibility/1",
        "blinding": "W side, plus the S chunk exports (chunk texts, for S reachability and the prior-content check), "
                    "the prior S registries' label and pairs files (integrity, content sha) and the stage-1 S run "
                    "summaries (the settings gate; no per-record outcome); no S retrieval row and no S outcome was "
                    "read",
        "scorer": {"module": "reach_quote", "min_match": MIN_MATCH, "hit_threshold": HIT_THRESHOLD, "top_k": TOP_K},
        "eligibility": {
            "records": len(recs), "papers": len(paper_n_all),
            "verbatim_raw": len(verb_raw), "non_verbatim_raw": len(recs) - len(verb_raw),
            "verbatim_whitespace_normalised": len(verb_norm),
            "non_verbatim_whitespace_normalised": len(recs) - len(verb_norm),
            "quotes_inside_one_w_window": len(verb_norm),
            "s_reach_of_w_verbatim": dict(sorted(Counter(reach.values()).items())),
            "s_reach_rule": "quote_cover(quote, every S chunk of the gold document) >= 0.90; split = reached only by "
                            "the union of two or more S chunks (the quote crosses an S chunk boundary)",
            "s_gold_documents_found": sum(d in s_doc_texts for d in docs), "gold_documents": len(docs),
            "eligible": len(eligible), "non_eligible": len(recs) - len(eligible),
            "eligible_papers": len(paper_n),
            "papers_by_eligible_records": {str(k): v for k, v in sorted(Counter(paper_n.values()).items())},
            "papers_by_raw_verbatim_records": {str(k): v for k, v in sorted(Counter(paper_n_raw.values()).items())},
            "papers_by_records_all": {str(k): v for k, v in sorted(Counter(paper_n_all.values()).items())},
        },
        "registry": {"label_rows_with_record_and_chunk": label_rows, "labelled_pairs": len(labelled)},
        "registry_integrity": integrity, "prior_content": prior_content, "settings_gates": settings,
        "orphans": orphans,
        "unresolved_chunk_texts": unresolved,
        "w_side": w, "w_side_totals": totals, "s_side_estimate": s_est, "w_miss_correlation": corr,
        "provenance": {"script_sha256": hashlib.sha256(here.read_bytes()).hexdigest(),
                       "reach_quote_sha256": hashlib.sha256((_EVAL / "reach_quote.py").read_bytes()).hexdigest(),
                       "inputs_sha256": {k: sha256_file(p) for k, p in inputs.items()},
                       "python": sys.version.split()[0], "implementation": sys.implementation.name, **state},
    }
    assert_no_identifiers(out)
    a.out.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"eligibility": out["eligibility"], "orphans": orphans, "registry_integrity": integrity,
                      "prior_content": prior_content, "settings_gates": {
                          k: v for k, v in settings.items() if k != "expected_differences"},
                      "w_quote10_default": {
        c: (w[c]["default"]["quote10"], w[c]["default"]["quote10_eligible"]) for c in CORPORA},
        "w_side_totals": totals, "s_unique_est": s_unique, "calls": calls, "corr": corr,
        "unresolved": unresolved}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
