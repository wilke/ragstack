"""Review checks on WHY.md's headline claims (PR #886 review; exploratory, post-hoc, offline).

Added by the independent review, after every output of ``analyse_why.py`` had been seen, so everything here
is **REVIEW, post-hoc**: not in ``PLAN.md``. It reads the same inputs as ``analyse_why.py`` (and reuses its
definitions), makes no retrieval, reranker, store or network call, and writes counts only
(``review-checks.json``): no query, quote or chunk text, no record ids, no paths.

It asks four things of the Q1 / Q2 / Q3 headline:
  (a) equal depth windows: the same-paper share of the top k of each list (the "above gold" sets differ in
      depth: BM25's lies inside its top 10 for a lost record, the reranker's runs to rank 31);
  (b) per distinct query: records that share a query share every list, so each query is counted once;
  (c) the base rate: the source paper's share of the pools the lists are drawn from;
  (d)/(e) the Q1 class split and the Q2 rare-token split per distinct query.

    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=python python3 -I \\
        docs/plans/results/reranker-test-2026-11/why/review_checks.py \\
        --root RUN_DIR --dump DUMP.jsonl --records-dir EVALSET_DIR \\
        --out docs/plans/results/reranker-test-2026-11/why/review-checks.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyse_why as aw  # noqa: E402

rt = aw.rt


def frac(a: int, b: int) -> dict[str, Any]:
    return {"same": a, "chunks": b, "share": round(a / b, 4) if b else None}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True)
    ap.add_argument("--dump", required=True)
    ap.add_argument("--records-dir", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    prov = aw.experiment_provenance(repo=aw.REPO)
    out_dir = Path(a.root) / "out"
    f = {"P_pools": out_dir / "P-pools-r2.json", "P_rerank": out_dir / "P-rerank-r2.json",
         "P_gold": out_dir / "P-gold-r2.json", "P_dump": Path(a.dump),
         "P_held": Path(a.records_dir) / aw.HELD, "P_pairs": Path(a.records_dir) / aw.PAIRS}
    res: dict[str, Any] = {"schema": "rr-why-review/1",
                           "label": "REVIEW, post-hoc: added after every analyse_why.py output was seen; descriptive",
                           "inputs_sha256": {k: aw.sha256_file(p) for k, p in f.items()}}

    questions, _ = rt.load_partner_held(f["P_held"], f["P_pairs"])
    qtext = {q["id"]: q["query"] for q in questions}
    rows = rt.read_jsonl(f["P_dump"])
    corpus = rt.corpus_from_rows(rows, {})
    pools = json.loads(f["P_pools"].read_text())["lists"]
    a4 = json.loads(f["P_rerank"].read_text())["lists"]["rr:hybrid@50:content+gloss"]
    bm = pools["bm25@100"]
    g = json.loads(f["P_gold"].read_text())
    pairs = {str(p["id"]): p for p in rt.read_jsonl(f["P_pairs"])}
    doc_of_file: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        doc_of_file[Path(((row.get("metadata") or {}).get("source_path") or "")).stem].add(str(row["doc_id"]))
    src = {q["id"]: doc_of_file.get(str(pairs[q["pair"]]["paper"].get("pmcid")), set()) for q in questions}
    cdoc = {c.id: c.doc_id for c in corpus.chunks}

    def same(r: str, c: str) -> bool:
        return cdoc.get(c) in src[r]

    rids = sorted(r for r, v in g["records"].items() if not v["excluded"])
    r0 = {r: aw.rank(bm[r][:aw.TOP], g["records"][r]) for r in rids}
    r4 = {r: aw.rank(pools["live:hybrid@50"][r], g["records"][r]) for r in rids}
    grp = {r: aw.outcome_class(r0[r], r4[r]) for r in rids}
    gid = {r: aw.gold_ids(g["records"][r]) for r in rids}
    inpool = {r: aw.rank(pools["hybrid@50"][r], g["records"][r]) is not None for r in rids}

    def window(sel: list[str], lst: dict[str, list[str]], k: int, exclude_gold: bool) -> dict[str, Any]:
        tot = sm = 0
        for r in sel:
            w = [c for c in lst[r][:k] if not (exclude_gold and c in gid[r])]
            tot += len(w)
            sm += sum(same(r, c) for c in w)
        return frac(sm, tot)

    # sanity: records sharing a query share both lists and the source paper
    byq_all: dict[str, list[str]] = defaultdict(list)
    for r in rids:
        byq_all[qtext[r]].append(r)
    res["checks"] = {
        "queries_with_identical_lists_across_records": sum(
            1 for rs in byq_all.values() if all(a4[r] == a4[rs[0]] and bm[r] == bm[rs[0]] for r in rs)),
        "queries_with_one_source_paper": sum(1 for rs in byq_all.values() if len({frozenset(src[r]) for r in rs}) == 1),
        "distinct_queries": len(byq_all)}

    # (a) equal windows, records
    eq: dict[str, Any] = {}
    for o in ("lost", "kept", "won", "all"):
        sel = [r for r in rids if o == "all" or grp[r] == o]
        eq[o] = {f"top{k}{'_excl_gold' if ex else ''}": {"A4-50": window(sel, a4, k, ex), "A0 (bm25@100)": window(sel, bm, k, ex)}
                 for k in (5, 10, 20) for ex in (False, True)}
    res["a_equal_windows_records"] = eq

    # (b) per distinct query, lost records (one representative record per query: lists are shared)
    lost = [r for r in rids if grp[r] == "lost"]
    byq: dict[str, list[str]] = defaultdict(list)
    for r in lost:
        byq[qtext[r]].append(r)
    reps = [rs[0] for rs in byq.values()]
    big = max(byq.values(), key=len)
    per_q_top10 = [(sum(same(r, c) for c in a4[r][:aw.TOP]), sum(same(r, c) for c in bm[r][:aw.TOP])) for r in reps]
    res["b_per_distinct_query_lost"] = {
        "distinct_queries": len(byq), "records_per_query_sorted": sorted(len(v) for v in byq.values()),
        "top10_incl_gold_pooled": {"A4-50": frac(sum(x for x, _ in per_q_top10), aw.TOP * len(reps)),
                                   "A0 (bm25@100)": frac(sum(y for _, y in per_q_top10), aw.TOP * len(reps))},
        "top10_queries_A4_more_same_fewer_same_equal": [sum(1 for x, y in per_q_top10 if x > y),
                                                        sum(1 for x, y in per_q_top10 if x < y),
                                                        sum(1 for x, y in per_q_top10 if x == y)],
        "records_excluding_the_largest_query": {
            "records": len(lost) - len(big),
            "top10_excl_gold": {"A4-50": window([r for r in lost if r not in big], a4, aw.TOP, True),
                                "A0 (bm25@100)": window([r for r in lost if r not in big], bm, aw.TOP, True)},
            "above_gold": {
                name: frac(sum(sum(same(r, c) for c in aw.above_gold(lst[r], g["records"][r]) or [])
                               for r in lost if r not in big),
                           sum(len(aw.above_gold(lst[r], g["records"][r]) or []) for r in lost if r not in big))
                for name, lst in (("A4-50", a4), ("A0 (bm25@100)", bm))}},
        "largest_query": {"records": len(big),
                          "top10_excl_gold": {"A4-50": window(big, a4, aw.TOP, True),
                                              "A0 (bm25@100)": window(big, bm, aw.TOP, True)}}}

    # (c) base rate: the source paper's share of the pools, per distinct lost query
    res["c_base_rate_lost_queries"] = {
        "hybrid@50_pool": frac(sum(sum(same(r, c) for c in pools["hybrid@50"][r]) for r in reps),
                               sum(len(pools["hybrid@50"][r]) for r in reps)),
        "bm25@100": frac(sum(sum(same(r, c) for c in bm[r]) for r in reps), sum(len(bm[r]) for r in reps)),
        "source_paper_chunks": sorted({sum(1 for c in corpus.chunks if c.doc_id in src[r]) for r in reps}),
        "bench_chunks": len(corpus.chunks)}

    # (d) Q1 per distinct query; (e) Q2 rare-token presence per distinct query
    cls = {r: aw.classes(qtext[r]) for r in rids}
    q1q: dict[str, Any] = {}
    for c in ("symbol", "digit", "unit", "none"):
        sel = [r for r in rids if cls[r][c]]
        q1q[c] = {"queries": len({qtext[r] for r in sel}),
                  "queries_A0_found_any_record": len({qtext[r] for r in sel if (r0[r] or 99) <= aw.TOP}),
                  "queries_with_a_lost_record": len({qtext[r] for r in sel if grp[r] == "lost"}),
                  "queries_with_a_won_record": len({qtext[r] for r in sel if grp[r] == "won"})}
    res["d_Q1_per_distinct_query"] = q1q
    dfc: Counter[str] = Counter()
    for c in corpus.chunks:
        dfc.update(aw.tokens(rt.render(c, "content+gloss")))
    thr = math.floor(aw.RARE_SHARE * len(corpus.chunks))
    q2q = {}
    for o in ("lost", "kept", "won", "neither"):
        qs = {qtext[r] for r in rids if grp[r] == o and inpool[r]}
        q2q[o] = {"distinct_in_pool_queries": len(qs),
                  "no_rare_query_token": sum(1 for q in qs if not {w for w in aw.tokens(q) if dfc.get(w, 0) <= thr})}
    res["e_Q2_per_distinct_query"] = q2q

    drop = {"host", "image", "installed_distribution", "python"}
    res["provenance"] = {**{k: v for k, v in prov.items() if k not in drop}, "redacted": sorted(drop)}
    blob = json.dumps(res, indent=1, ensure_ascii=False)
    if any(len(t) >= 12 and t in blob for t in qtext.values()):
        raise SystemExit("text leak check failed")
    Path(a.out).write_text(blob + "\n")
    print(json.dumps({"written": Path(a.out).name, "citable": prov.get("citable")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
