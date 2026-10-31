"""Why the reranker orders papers-OC worse than BM25: exploratory, post-hoc, offline.

Implements ``PLAN.md`` (committed before this script). Reads only existing outputs of the reranker test
(lists, gold, the hash-keyed score cache) plus the bench chunk dump and the papers-OC record files; makes
no retrieval, reranker, store or network call and recomputes no score. Writes counts only
(``why-results.json``): no query, quote or chunk text, no record ids, no paths.

    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=python python3 -I \\
        docs/plans/results/reranker-test-2026-11/why/analyse_why.py \\
        --root RUN_DIR --records-dir EVALSET_DIR --v4-gold P-gold-v4.json --v4-builder variant4.py \\
        --out docs/plans/results/reranker-test-2026-11/why/why-results.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO / "python" / "scripts" / "eval"))
sys.path.insert(0, str(REPO / "python"))
import reranker_test as rt  # noqa: E402
from ragstack.provenance import experiment_provenance  # noqa: E402

TOP = 10
RARE_SHARE = 0.01
HELD = "glossary_split_records.jsonl"
PAIRS = "pairs.dedup2-261022.jsonl"

# ------------------------------------------------------------------ tokens and classes (PLAN § Fixed definitions)

_COMB = re.compile(r"\s+(?=[̀-ͯ])")
_WORD = re.compile(r"[\ẁ-ͯ]+")
_SUBFORM = re.compile(r"^[a-z][a-z0-9]*_[a-z0-9]+$")
_SUBSUP = set("²³¹") | {chr(c) for c in range(0x2070, 0x20A0)}
_LATEX = re.compile(r"\\[A-Za-z]+")
UNITS = ("k kev mev gev tev ev nm µm μm mm cm m km s ms ns µs μs ps fs hz khz mhz ghz thz t g mg kg µg μg mol "
         "mmol l ml µl μl pa kpa mpa gpa bar mbar atm torr w kw mw j kj v kv mv a ma ω ohm ppm ppb da kda gy sv "
         "bq ci °c k kb mb gb tb kib mib gib rpm dpi px min h hr d yr").split()
NOT_ANYWHERE = set("min hr yr bar pa ms ns ps fs mb gb kb tb mw kw mv ma ml mm cm km ev da".split())
_UALT = "|".join(sorted({re.escape(u) for u in UNITS}, key=len, reverse=True))
_NUM_UNIT = re.compile(rf"\d(?:[.,]\d+)*\s?(?:{_UALT})(?![\ẁ-ͯ])")
ANYWHERE = {u for u in UNITS if len(u) >= 2 and u not in NOT_ANYWHERE}


def norm(text: str) -> str:
    return _COMB.sub("", rt.partner_norm(text))


def tokens(text: str) -> set[str]:
    return set(_WORD.findall(norm(text)))


def is_symbol_token(w: str) -> bool:
    return any((ch.isalpha() and ord(ch) > 127) or 0x300 <= ord(ch) <= 0x36F for ch in w) or bool(_SUBFORM.match(w))


def classes(query: str) -> dict[str, bool]:
    n = norm(query)
    words = _WORD.findall(n)
    symbol = (any(ch in _SUBSUP for ch in query) or bool(_LATEX.search(query))
              or any(is_symbol_token(w) for w in words))
    digit = bool(re.search(r"[0-9]", n))
    unit = ("%" in n or "°" in n or bool(_NUM_UNIT.search(n)) or any(w in ANYWHERE for w in words))
    return {"symbol": symbol, "digit": digit, "unit": unit, "none": not (symbol or digit or unit)}


def symbol_density(content: str) -> float:
    if not content:
        return 0.0
    k = sum(1 for ch in content if (ch.isalpha() and ord(ch) > 127) or 0x300 <= ord(ch) <= 0x36F
            or unicodedata.category(ch) == "Sm" or ch in _SUBSUP)
    return 100.0 * k / len(content)


# ------------------------------------------------------------------ small statistics (descriptive only)

def mcnemar_exact(b: int, c: int) -> float | None:
    n = b + c
    if n == 0:
        return None
    tail = sum(math.comb(n, i) for i in range(0, min(b, c) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def avg_ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(x: list[float], y: list[float]) -> dict[str, Any]:
    n = len(x)
    if n < 3:
        return {"n": n, "rho": None}
    rx, ry = avg_ranks(x), avg_ranks(y)
    mx, my = sum(rx) / n, sum(ry) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    rho = sxy / math.sqrt(sxx * syy) if sxx and syy else None
    return {"n": n, "rho": None if rho is None else round(rho, 4)}


def dist(xs: list[float]) -> dict[str, Any]:
    if not xs:
        return {"n": 0}
    q = statistics.quantiles(xs, n=4, method="inclusive") if len(xs) > 1 else [xs[0]] * 3
    return {"n": len(xs), "median": round(statistics.median(xs), 4), "q1": round(q[0], 4), "q3": round(q[2], 4),
            "min": round(min(xs), 4), "max": round(max(xs), 4)}


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ------------------------------------------------------------------ shared pieces

def rank(lst: list[str] | None, grec: dict[str, Any]) -> int | None:
    return rt.rank_of_gold(lst, grec, "any") if lst is not None else None


def gold_ids(grec: dict[str, Any]) -> set[str]:
    return {x for q in grec["quotes"] for x in q}


def outcome_class(r0: int | None, r4: int | None) -> str:
    a = r0 is not None and r0 <= TOP
    b = r4 is not None and r4 <= TOP
    return {(True, False): "lost", (False, True): "won", (True, True): "kept", (False, False): "neither"}[(a, b)]


def q1_table(rids: list[str], qtext: dict[str, str], arms: dict[str, dict[str, list[str]]],
             gold: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    cls = {r: classes(qtext[r]) for r in rids}
    for c in ("symbol", "digit", "unit", "none", "all"):
        sel = [r for r in rids if c == "all" or cls[r][c]]
        f = {a: {r: (rank(arms[a].get(r), gold["records"][r]) or 99) <= TOP for r in sel} for a in arms}
        gained = sum(1 for r in sel if f["A4-50"][r] and not f["A0"][r])
        lost = sum(1 for r in sel if f["A0"][r] and not f["A4-50"][r])
        out[c] = {"records": len(sel), "distinct_queries": len({qtext[r] for r in sel}),
                  **{f"found@10 {a}": sum(f[a].values()) for a in arms},
                  "A4-50 vs A0: gained / lost": [gained, lost],
                  "mcnemar_exact_two_sided_descriptive": mcnemar_exact(gained, lost)}
    return out


def above_gold(lst: list[str], grec: dict[str, Any]) -> list[str] | None:
    g = gold_ids(grec)
    for i, c in enumerate(lst):
        if c in g:
            return lst[:i]
    return None


def q3_block(rids: list[str], lists: dict[str, dict[str, list[str]]], gold: dict[str, Any],
             group_of: dict[str, str], same: Any) -> dict[str, Any]:
    """Chunks ranked above the first gold, and their same-paper (same-page) share, per list and group."""
    out: dict[str, Any] = {}
    for name, per in lists.items():
        blk: dict[str, Any] = {}
        for grp in ("lost", "kept", "won", "neither", "all"):
            sel = [r for r in rids if grp == "all" or group_of[r] == grp]
            n_rec, n_above, n_same, shares, absent = 0, 0, 0, [], 0
            for r in sel:
                ab = above_gold(per[r], gold["records"][r]) if r in per else None
                if ab is None:
                    absent += 1
                    continue
                n_rec += 1
                s = sum(1 for c in ab if same(r, c))
                n_above += len(ab)
                n_same += s
                if ab:
                    shares.append(s / len(ab))
            blk[grp] = {"records_gold_in_list": n_rec, "records_gold_not_in_list": absent,
                        "records_gold_at_rank_1": n_rec - len(shares), "chunks_above_gold": n_above,
                        "same_above_gold": n_same,
                        "same_share_pooled": round(n_same / n_above, 4) if n_above else None,
                        "same_share_per_record_median": round(statistics.median(shares), 4) if shares else None}
        out[name] = blk
    return out


def top10_same(rids: list[str], lists: dict[str, dict[str, list[str]]], same: Any) -> dict[str, Any]:
    out = {}
    for name, per in lists.items():
        tot = sm = 0
        for r in rids:
            t = per[r][:TOP]
            tot += len(t)
            sm += sum(1 for c in t if same(r, c))
        out[name] = {"chunks": tot, "same": sm, "share": round(sm / tot, 4) if tot else None}
    return out


# ------------------------------------------------------------------ main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True, help="the reranker test's private run directory (holds out/)")
    ap.add_argument("--records-dir", required=True, help="directory with the papers-OC held records and pairs file")
    ap.add_argument("--v4-gold", required=True, help="the addendum's v4 gold, rebuilt (sensitivity)")
    ap.add_argument("--v4-builder", required=True, help="variant4.py, for its sha256 only")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    prov = experiment_provenance(repo=REPO)  # before anything is written

    out_dir = Path(a.root) / "out"
    f = {"P_pools": out_dir / "P-pools-r2.json", "P_rerank": out_dir / "P-rerank-r2.json",
         "P_cache": out_dir / "P-rerank-cache.jsonl", "P_gold": out_dir / "P-gold-r2.json",
         "P_dump": out_dir / "dump-openchia.jsonl", "P_held": Path(a.records_dir) / HELD,
         "P_pairs": Path(a.records_dir) / PAIRS, "P_gold_v4": Path(a.v4_gold), "v4_builder": Path(a.v4_builder),
         "docsA_pools": out_dir / "docsA-pools-r2.json", "docsA_rerank": out_dir / "docsA-rerank-r2.json",
         "docsA_gold": out_dir / "docsA-gold-r2.json", "docsA_questions": out_dir / "part4-questions.jsonl"}
    res: dict[str, Any] = {"schema": "rr-why/1", "label": "EXPLORATORY, post-hoc; plan committed before computing",
                           "inputs_sha256": {k: sha256_file(p) for k, p in f.items()},
                           "parameters": {"top": TOP, "rare_df_share": RARE_SHARE, "gold_rule": "any"}}

    # ---------------- set P
    questions, _ = rt.load_partner_held(f["P_held"], f["P_pairs"])
    qtext = {q["id"]: q["query"] for q in questions}
    rows = rt.read_jsonl(f["P_dump"])
    corpus = rt.corpus_from_rows(rows, {})
    pools = json.loads(f["P_pools"].read_text())["lists"]
    rr = json.loads(f["P_rerank"].read_text())["lists"]
    g_run = json.loads(f["P_gold"].read_text())
    g_v4 = json.loads(f["P_gold_v4"].read_text())

    a4full = rr["rr:hybrid@50:content+gloss"]
    checks = {"records": len(questions),
              "a4_offline_top10_equals_live": sum(1 for r in qtext if a4full[r][:TOP] == pools["live:hybrid@50"][r])}
    arms_P = {"A0": {r: lst[:TOP] for r, lst in pools["bm25@100"].items()},
              "A4-50": pools["live:hybrid@50"], "A5-50": pools["live:bm25@50"]}

    # source paper, as v4 maps it
    pairs = {str(p["id"]): p for p in rt.read_jsonl(f["P_pairs"])}
    doc_of_file: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        doc_of_file[Path(((row.get("metadata") or {}).get("source_path") or "")).stem].add(str(row["doc_id"]))
    src = {q["id"]: doc_of_file.get(str(pairs[q["pair"]]["paper"].get("pmcid")), set()) for q in questions}
    checks["records_source_paper_mapped_to_one_doc"] = sum(1 for s in src.values() if len(s) == 1)
    chunk_doc = {c.id: c.doc_id for c in corpus.chunks}
    same_paper = lambda r, c: chunk_doc.get(c) in src[r]  # noqa: E731

    def p_block(gold: dict[str, Any]) -> dict[str, Any]:
        rids = sorted(r for r, v in gold["records"].items() if not v["excluded"])
        r0 = {r: rank(pools["bm25@100"][r][:TOP], gold["records"][r]) for r in rids}
        r4 = {r: rank(pools["live:hybrid@50"][r], gold["records"][r]) for r in rids}
        grp = {r: outcome_class(r0[r], r4[r]) for r in rids}
        inpool = {r: rank(pools["hybrid@50"][r], gold["records"][r]) is not None for r in rids}
        blk = {"n_included": len(rids), "outcome_counts": dict(Counter(grp.values())),
               "outcome_counts_in_hybrid50_pool": dict(Counter(grp[r] for r in rids if inpool[r])),
               "Q1": q1_table(rids, qtext, arms_P, gold),
               "Q3": q3_block(rids, {"A4-50 (rr hybrid@50, 50 long)": a4full, "A0 (bm25@100)": pools["bm25@100"]},
                              gold, grp, same_paper),
               "Q3_top10_same_paper_all_records": top10_same(rids, {"A0": arms_P["A0"], "A4-50": arms_P["A4-50"]},
                                                             same_paper)}
        return blk, rids, grp, inpool  # type: ignore[return-value]

    P, rids, grp, inpool = p_block(g_run)  # type: ignore[misc]
    res["P"] = P
    res["P"]["checks"] = checks

    # ---------------- Q2: rare tokens
    dfc: Counter[str] = Counter()
    ctoks: dict[str, set[str]] = {}
    for c in corpus.chunks:
        t = tokens(rt.render(c, "content+gloss"))
        ctoks[c.id] = t
        dfc.update(t)
    thr = math.floor(RARE_SHARE * len(corpus.chunks))
    res["parameters"]["rare_df_max_chunks"] = thr
    q2: dict[str, Any] = {}
    rec_q2 = {}
    for r in rids:
        if not inpool[r]:
            continue
        rare = {w for w in tokens(qtext[r]) if dfc.get(w, 0) <= thr}
        lst = a4full[r]
        g = gold_ids(g_run["records"][r])
        gi = next(i for i, c in enumerate(lst) if c in g)
        ng = next(c for c in lst if c not in g)
        if not rare:
            rec_q2[r] = None
            continue
        gs = len(rare & ctoks[lst[gi]])
        ns = len(rare & ctoks[ng])
        above = [len(rare & ctoks[c]) for c in lst[:gi]]
        rec_q2[r] = {"cmp": "fewer" if ns < gs else ("equal" if ns == gs else "more"),
                     "beats_all_above": (None if not above else gs > max(above)),
                     "gold_has_any": gs > 0, "nongold_has_any": ns > 0}
    for g_ in ("lost", "kept", "won", "neither", "all"):
        sel = [r for r in rec_q2 if g_ == "all" or grp[r] == g_]
        meas = [rec_q2[r] for r in sel if rec_q2[r] is not None]
        q2[g_] = {"records_in_pool": len(sel), "no_rare_query_token": len(sel) - len(meas),
                  "top_nongold_vs_gold_rare_tokens": dict(Counter(m["cmp"] for m in meas)),
                  "gold_carries_any_rare": sum(1 for m in meas if m["gold_has_any"]),
                  "top_nongold_carries_any_rare": sum(1 for m in meas if m["nongold_has_any"]),
                  "gold_at_rank_1": sum(1 for m in meas if m["beats_all_above"] is None),
                  "gold_strictly_more_than_every_chunk_above": sum(1 for m in meas if m["beats_all_above"] is True),
                  "gold_not_more_than_some_chunk_above": sum(1 for m in meas if m["beats_all_above"] is False)}
    res["P"]["Q2"] = q2

    # ---------------- Q4: score margins from the cache
    cache = rt.RerankCache(f["P_cache"])
    q4_ok, q4_missing, q4_mismatch = 0, 0, 0
    margins: dict[str, list[float]] = defaultdict(list)
    dens, rk_rr, rk_bm, dens_bm, symq = [], [], [], [], []
    for r in rids:
        if not inpool[r]:
            continue
        ids = pools["hybrid@50"][r]
        docs = [rt.render(corpus.by_id[x], "content+gloss") for x in ids]
        hit = cache.get(rt.RerankCache.key(qtext[r], docs))
        if hit is None:
            q4_missing += 1
            continue
        order = [ids[i] for i in hit["i"]]
        if order != a4full[r]:
            q4_mismatch += 1
            continue
        q4_ok += 1
        sc = hit["s"]
        g = gold_ids(g_run["records"][r])
        gi = next(i for i, c in enumerate(order) if c in g)
        ni = next(i for i, c in enumerate(order) if c not in g)
        margins[grp[r]].append(sc[ni] - sc[gi])
        d = symbol_density(corpus.by_id[order[gi]].content)
        dens.append(d)
        rk_rr.append(gi + 1)
        symq.append(classes(qtext[r])["symbol"])
        rb = rank(pools["bm25@100"][r], g_run["records"][r])
        if rb is not None:
            dens_bm.append(d)
            rk_bm.append(rb)
    scores_desc = all(all(x >= y for x, y in zip(h["s"], h["s"][1:])) for h in cache.d.values())
    res["P"]["Q4"] = {
        "cache_rows": len(cache.d), "cache_scores_descending_in_every_row": scores_desc,
        "in_pool_records_scored": q4_ok, "cache_key_missing": q4_missing, "cache_order_mismatch": q4_mismatch,
        "margin_top_nongold_minus_gold": {g_: dist(margins[g_]) for g_ in ("lost", "kept", "won", "neither")},
        "margin_positive_count": {g_: sum(1 for m in margins[g_] if m > 0) for g_ in ("lost", "kept", "won", "neither")},
        "gold_symbol_density_per_100_chars": dist(dens),
        "spearman_density_vs_reranked_rank": spearman(dens, [float(x) for x in rk_rr]),
        "spearman_density_vs_bm25_rank": spearman(dens_bm, [float(x) for x in rk_bm]),
        "reranked_rank_by_query_class": {"symbol": dist([float(x) for x, s in zip(rk_rr, symq, strict=True) if s]),
                                         "not_symbol": dist([float(x) for x, s in zip(rk_rr, symq, strict=True) if not s])},
        "note": "records sharing a query share lists; values are descriptive, not independent observations"}

    # ---------------- Q5: glossary renderings
    rends = {k: rr[f"rr:hybrid@50:{k}"] for k in ("content", "content+gloss", "content+shuffled")}
    q5: dict[str, Any] = {}
    for carrier in ("gold_carries_glossary", "gold_carries_no_glossary"):
        for qc in ("symbol", "not_symbol", "all"):
            sel = []
            for r in rids:
                if not inpool[r]:
                    continue
                gpool = gold_ids(g_run["records"][r]) & set(pools["hybrid@50"][r])
                has = any(corpus.by_id[c].glossary for c in gpool)
                if has != (carrier == "gold_carries_glossary"):
                    continue
                s = classes(qtext[r])["symbol"]
                if qc != "all" and s != (qc == "symbol"):
                    continue
                sel.append(r)
            rk = {k: {r: rank(v[r], g_run["records"][r]) for r in sel} for k, v in rends.items()}
            mv = Counter("up" if rk["content+gloss"][r] < rk["content"][r] else
                         ("down" if rk["content+gloss"][r] > rk["content"][r] else "same") for r in sel)
            mvs = Counter("up" if rk["content+shuffled"][r] < rk["content"][r] else
                          ("down" if rk["content+shuffled"][r] > rk["content"][r] else "same") for r in sel)
            q5[f"{carrier} / {qc}"] = {
                "records": len(sel),
                **{f"found@10 {k}": sum(1 for r in sel if rk[k][r] <= TOP) for k in rends},
                "gloss_vs_content_gold_rank": dict(mv),
                "shuffled_vs_content_gold_rank": dict(mvs),
                "median_rank": {k: (statistics.median(rk[k].values()) if sel else None) for k in rends}}
    res["P"]["Q5"] = q5

    # ---------------- Q6: v4
    P4, rids4, grp4, _ = p_block(g_v4)  # type: ignore[misc]
    res["P_v4"] = {"note": "sensitivity: the addendum's v4 gold (source-paper scoped)", **P4}

    # ---------------- docs-A contrast (Q1, Q3)
    dq = [x for x in rt.read_jsonl(f["docsA_questions"]) if str(x.get("status")) == "valid"]
    dtext = {str(x["record_id"]): str(x["question"]) for x in dq}
    dpools = json.loads(f["docsA_pools"].read_text())["lists"]
    drr = json.loads(f["docsA_rerank"].read_text())["lists"]
    dg = json.loads(f["docsA_gold"].read_text())
    drids = sorted(r for r, v in dg["records"].items() if not v["excluded"])
    arms_D = {"A0": {r: lst[:TOP] for r, lst in dpools["bm25@200"].items()},
              "A4-50": drr["rr:hybrid@50:content+gloss"], "A5-50": drr["rr:bm25@50:content+gloss"]}
    page = lambda c: c.split("|")[1]  # noqa: E731
    gpages = {r: {page(c) for c in gold_ids(dg["records"][r])} for r in drids}
    same_page = lambda r, c: page(c) in gpages[r]  # noqa: E731
    d0 = {r: rank(arms_D["A0"][r], dg["records"][r]) for r in drids}
    d4 = {r: rank(arms_D["A4-50"][r][:TOP], dg["records"][r]) for r in drids}
    dgrp = {r: outcome_class(d0[r], d4[r]) for r in drids}
    res["docsA"] = {"n_included": len(drids), "outcome_counts": dict(Counter(dgrp.values())),
                    "Q1": q1_table(drids, dtext, arms_D, dg),
                    "Q3_same_page": q3_block(drids, {"A4-50 (rr hybrid@50, 50 long)": arms_D["A4-50"],
                                                     "A0 (bm25@200)": dpools["bm25@200"]}, dg, dgrp, same_page),
                    "Q3_top10_same_page_all_records": top10_same(drids, {"A0": arms_D["A0"], "A4-50": arms_D["A4-50"]},
                                                                 same_page),
                    "not_run": "Q2, Q4, Q5: docs-A chunk text is only in the live index (out of scope)"}

    res["provenance"] = prov
    blob = json.dumps(res, indent=1, sort_keys=False, ensure_ascii=False)
    texts = [q["query"] for q in questions] + list(dtext.values())
    leaks = [t for t in texts if len(t) >= 12 and t in blob]
    if leaks:
        raise SystemExit(f"text leak check failed ({len(leaks)} query texts in the output)")
    Path(a.out).write_text(blob + "\n")
    print(json.dumps({"written": Path(a.out).name, "citable": prov.get("citable")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
