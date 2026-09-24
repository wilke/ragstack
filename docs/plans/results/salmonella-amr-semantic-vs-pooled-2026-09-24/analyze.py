"""Analysis over the offline runs (compare_corpus.py run1/run2) and the ES read-back.

Pure-python, runs on the host (python >= 3.6). Produces analysis.json and prints the
tables the write-up quotes. Spearman uses AVERAGE ranks for ties (the 09-18 script's
`rank()` broke ties by input order; pooled's 6-decimal rounding makes ties possible at
corpus scale, so the proper estimator is used here and the 09-18-style figure is
reported beside it for comparability).
"""
import json
import os
import sys

OFF = sys.argv[1]      # offline dir (distances-*.json, spans-*.json, summary-*.json)
RB = sys.argv[2]       # readback dir (readback-*.json)
OUT = sys.argv[3] if len(sys.argv) > 3 else os.path.join(OFF, "analysis.json")
ARMS = ("semantic", "semantic_pooled")


def rank_avg(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def rank_ordinal(xs):  # the 09-18 estimator, ties broken by position
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    for pos, i in enumerate(order):
        r[i] = float(pos)
    return r


def pearson(a, b):
    n = len(a)
    if n < 2:
        return float("nan")
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = sum((x - ma) ** 2 for x in a) ** 0.5
    db = sum((y - mb) ** 2 for y in b) ** 0.5
    return num / (da * db) if da and db else float("nan")


def spearman(a, b):
    return pearson(rank_avg(a), rank_avg(b))


def spearman_0918(a, b):
    return pearson(rank_ordinal(a), rank_ordinal(b))


def quantiles(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return {}
    q = lambda p: xs[min(n - 1, int(round(p * (n - 1))))]  # noqa: E731
    return {"n": n, "min": xs[0], "p10": q(.1), "p25": q(.25), "p50": q(.5), "p75": q(.75),
            "p90": q(.9), "max": xs[-1], "mean": round(sum(xs) / n, 1), "sum": sum(xs)}


def load(kind, arm, tag):
    return json.load(open(os.path.join(OFF, "%s-%s-%s.json" % (kind, arm, tag))))


def spanset(spans):
    return set((s["start"], s["end"]) for s in spans)


summ = {t: json.load(open(os.path.join(OFF, "summary-%s.json" % t))) for t in ("run1", "run2")}
D = {(a, t): load("distances", a, t) for a in ARMS for t in ("run1", "run2")}
S = {(a, t): load("spans", a, t) for a in ARMS for t in ("run1", "run2")}
docs = sorted(D[("semantic", "run1")].keys())
out = {"docs": docs, "n_docs": len(docs), "chars": summ["run1"]["chars"], "cost": {}, "per_doc": {},
       "between_arms": {}, "control": {}, "chunk_lengths_offline": {}, "offline_vs_es": {}}

# --- cost (the "7x at buffer 3" claim, measured) ---
for a in ARMS:
    out["cost"][a] = {t: {k: summ[t]["arms"][a][k] for k in
                          ("texts_embedded", "tokens_embedded", "embed_calls", "embed_s", "wall_s",
                           "n_chunks", "n_distance_pairs", "max_tokens", "distance_round")}
                      for t in ("run1", "run2")}
L, P = summ["run1"]["arms"]["semantic"], summ["run1"]["arms"]["semantic_pooled"]
out["cost"]["ratio_tokens_run1"] = round(L["tokens_embedded"] / max(P["tokens_embedded"], 1), 3)
out["cost"]["ratio_wall_run1"] = round(L["wall_s"] / max(P["wall_s"], 1e-9), 3)
out["cost"]["ratio_embed_s_run1"] = round(L["embed_s"] / max(P["embed_s"], 1e-9), 3)

# --- between arms (run1) ---
allL, allP, ti, tu = [], [], 0, 0
for d in docs:
    dl, dp = D[("semantic", "run1")][d], D[("semantic_pooled", "run1")][d]
    assert len(dl) == len(dp), (d, len(dl), len(dp))
    sl, sp = spanset(S[("semantic", "run1")][d]), spanset(S[("semantic_pooled", "run1")][d])
    inter, uni = len(sl & sp), len(sl | sp)
    ti += inter
    tu += uni
    allL += dl
    allP += dp
    out["per_doc"][d] = {
        "n_sentences": len(dl) + 1, "n_pairs": len(dl),
        "chunks_semantic": len(sl), "chunks_pooled": len(sp),
        "jaccard": round(inter / uni, 4) if uni else None, "shared_spans": inter,
        "spearman": round(spearman(dl, dp), 4), "spearman_0918_estimator": round(spearman_0918(dl, dp), 4),
        "ties_pooled": len(dp) - len(set(dp)), "ties_semantic": len(dl) - len(set(dl)),
    }
out["between_arms"] = {
    "n_pairs": len(allL),
    "spearman_overall": round(spearman(allL, allP), 4),
    "spearman_overall_0918_estimator": round(spearman_0918(allL, allP), 4),
    "spearman_per_doc_median": round(sorted(v["spearman"] for v in out["per_doc"].values())[len(docs) // 2], 4),
    "spearman_per_doc_min": min(v["spearman"] for v in out["per_doc"].values()),
    "spearman_per_doc_max": max(v["spearman"] for v in out["per_doc"].values()),
    "span_jaccard_overall": round(ti / tu, 4), "shared_spans": ti, "union_spans": tu,
    "chunks_semantic": sum(v["chunks_semantic"] for v in out["per_doc"].values()),
    "chunks_pooled": sum(v["chunks_pooled"] for v in out["per_doc"].values()),
    "docs_with_any_shared_span": sum(1 for v in out["per_doc"].values() if v["shared_spans"]),
    "docs_identical_spans": sum(1 for v in out["per_doc"].values()
                                if v["jaccard"] == 1.0),
}

# --- control: each arm run1 vs run2 ---
for a in ARMS:
    d1 = [x for d in docs for x in D[(a, "run1")][d]]
    d2 = [x for d in docs for x in D[(a, "run2")][d]]
    same = sum(1 for x, y in zip(d1, d2) if x == y)
    per_doc_rho = [spearman(D[(a, "run1")][d], D[(a, "run2")][d]) for d in docs]
    s1 = {d: spanset(S[(a, "run1")][d]) for d in docs}
    s2 = {d: spanset(S[(a, "run2")][d]) for d in docs}
    ident_docs = sum(1 for d in docs if s1[d] == s2[d])
    ji = sum(len(s1[d] & s2[d]) for d in docs)
    ju = sum(len(s1[d] | s2[d]) for d in docs)
    maxabs = max(abs(x - y) for x, y in zip(d1, d2))
    out["control"][a] = {
        "spearman_run1_run2": round(spearman(d1, d2), 4), "n_pairs": len(d1),
        "identical_distances": same, "identical_distances_frac": round(same / len(d1), 4),
        "max_abs_distance_delta": maxabs,
        "per_doc_spearman_min": round(min(per_doc_rho), 4),
        "spans_identical_all_docs": ident_docs == len(docs), "docs_with_identical_spans": ident_docs,
        "span_jaccard_run1_run2": round(ji / ju, 4),
        "chunks_run1": sum(len(s1[d]) for d in docs), "chunks_run2": sum(len(s2[d]) for d in docs),
    }

# --- chunk-length distributions (offline run1) ---
for a in ARMS:
    toks = [s["tokens"] for d in docs for s in S[(a, "run1")][d]]
    chars = [s["chars"] for d in docs for s in S[(a, "run1")][d]]
    out["chunk_lengths_offline"][a] = {"tokens": quantiles(toks), "chars": quantiles(chars),
                                       "at_budget_4080": sum(1 for t in toks if t >= 4079)}

# --- offline (run1) spans vs what the API/GoWe path wrote to ES ---
for a in ARMS:
    rb = json.load(open(os.path.join(RB, "readback-%s.json" % a)))
    es = {}
    for r in rb:
        es.setdefault(r["filename"], set()).add((r["start"], r["end"]))
    off = {d: spanset(S[(a, "run1")][d]) for d in docs}
    ji = sum(len(off[d] & es.get(d, set())) for d in docs)
    ju = sum(len(off[d] | es.get(d, set())) for d in docs)
    out["offline_vs_es"][a] = {
        "es_chunks": len(rb), "offline_chunks": sum(len(off[d]) for d in docs),
        "span_jaccard": round(ji / ju, 4) if ju else None,
        "docs_identical": sum(1 for d in docs if off[d] == es.get(d, set())),
        "per_doc_es_chunks": {d: len(es.get(d, set())) for d in docs},
        "per_doc_offline_chunks": {d: len(off[d]) for d in docs},
    }

json.dump(out, open(OUT, "w"), indent=1)

# --- print ---
print("COST (run1)              %14s %16s" % ARMS)
for k in ("texts_embedded", "tokens_embedded", "embed_calls", "embed_s", "wall_s", "n_chunks"):
    print("  %-22s %14s %16s" % (k, out["cost"]["semantic"]["run1"][k], out["cost"]["semantic_pooled"]["run1"][k]))
print("  token ratio legacy/pooled %.3fx   wall ratio %.3fx   embed-time ratio %.3fx" % (
    out["cost"]["ratio_tokens_run1"], out["cost"]["ratio_wall_run1"], out["cost"]["ratio_embed_s_run1"]))
b = out["between_arms"]
print("\nBETWEEN ARMS: pairs=%d Spearman overall=%.4f (09-18 estimator %.4f)  per-doc median=%.4f [%.4f..%.4f]" % (
    b["n_pairs"], b["spearman_overall"], b["spearman_overall_0918_estimator"], b["spearman_per_doc_median"],
    b["spearman_per_doc_min"], b["spearman_per_doc_max"]))
print("  span Jaccard overall=%.4f (%d shared / %d union)  chunks %d vs %d  docs with any shared span %d/%d  identical %d/%d" % (
    b["span_jaccard_overall"], b["shared_spans"], b["union_spans"], b["chunks_semantic"], b["chunks_pooled"],
    b["docs_with_any_shared_span"], len(docs), b["docs_identical_spans"], len(docs)))
print("\nPER DOC")
for d in docs:
    v = out["per_doc"][d]
    print("  %-14s sent=%5d chunks %3d vs %3d  Jaccard=%.3f  rho=%.4f  ties(pooled)=%d" % (
        d, v["n_sentences"], v["chunks_semantic"], v["chunks_pooled"], v["jaccard"], v["spearman"], v["ties_pooled"]))
print("\nCONTROL (run1 vs run2)")
for a in ARMS:
    c = out["control"][a]
    print("  %-16s Spearman=%.4f identical distances %d/%d (%.1f%%) max|delta|=%.2e spans identical=%s (%d/%d docs) Jaccard=%.4f chunks %d/%d" % (
        a, c["spearman_run1_run2"], c["identical_distances"], c["n_pairs"], 100 * c["identical_distances_frac"],
        c["max_abs_distance_delta"], c["spans_identical_all_docs"], c["docs_with_identical_spans"], len(docs),
        c["span_jaccard_run1_run2"], c["chunks_run1"], c["chunks_run2"]))
print("\nCHUNK LENGTH (offline run1, tokens)")
for a in ARMS:
    q = out["chunk_lengths_offline"][a]["tokens"]
    print("  %-16s n=%d min=%d p10=%d p50=%d p90=%d max=%d mean=%.1f at-budget=%d" % (
        a, q["n"], q["min"], q["p10"], q["p50"], q["p90"], q["max"], q["mean"], out["chunk_lengths_offline"][a]["at_budget_4080"]))
print("\nOFFLINE vs ES (API/GoWe-built)")
for a in ARMS:
    v = out["offline_vs_es"][a]
    print("  %-16s es=%d offline=%d span Jaccard=%s docs identical=%d/%d" % (
        a, v["es_chunks"], v["offline_chunks"], v["span_jaccard"], v["docs_identical"], len(docs)))
print("\nwrote", OUT)
