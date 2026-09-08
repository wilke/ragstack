"""Confirmation run (a), step 4 — the labeling set (r2 §6.3). **COUNTS ONLY.**

Per confirmation topic:

1. **Pooled** — every grade ≥ 1 document appearing in the union of the top-20 *documents*
   of every scoring ranking, and
2. **Bias-bound sample** — 10 additional grade ≥ 1 documents drawn seeded
   (``SEED_BIASBOUND``, per-topic string seed, exactly `s0_label.labeling_set`'s draw) from
   **outside** the pool, or all remaining if fewer.

**What "every scoring ranking" means here, stated because r3 widened it.** Stage 0's
development pooling (`s0_label.labeling_set`) took the top-20 documents of the six index
arms' reranked lists × 2 query variants — 12 rankings, because Stage 0b had one retrieval
mode and one rerank state. Revision 3 §3.4 adds three modes and a rerank-off column, and
§3.5 adds four delivery arms plus ``multi256+1024``, and reports **all** of them (hybrid +
rerank-on confirmatory, the rest as pre-registered secondaries with identical tables). A
document outside every pool can never be packed into any arm's context, so under-pooling
would bias the numerator of the secondaries. This module therefore pools over **every
distinct document ranking any registered analysis reads**:

* 6 index arms × 3 modes × 2 rerank states × 2 variants = 72 rankings, plus
* ``multi256+1024`` (vector only, per r3 §3.4) × 2 rerank states × 2 variants = 4.

The four delivery arms (``parent256``, ``nbr1_512``, ``nbr1_256``, ``nbr2_512``) contribute
**no new documents**: a parent slice and a neighbour chunk carry the *same* ``docno`` as the
source chunk they expand, and the packing order is the source arm's, so their top-20
document sets are their base arm's. That is asserted here rather than assumed.

The narrower pool (hybrid + rerank-on only, the confirmatory path) is reported beside the
adopted one as a count, so the widening's cost is visible.

**Quarantine.** The output is an **id list**. No rank, score, mode, arm or position is
carried into it: which arm surfaced a document is exactly the kind of retrieval outcome
r2 §2.3 forbids reading before unblinding, and the labeler must not see it (r2 §6.4 rule 1).
Only counts are printed. Counts are not outcomes.

Output: ``work/conf/pool/labeling_set.json`` (ids), ``work/conf/pool/pool_counts.json``.
"""
from __future__ import annotations

import json
import random
import sys
import time

import s0c_common as Q          # noqa: I001
import s0b_common as K
import s0_common as C
from s0b_pack import ordered_pool


def top_docs(rec, mode: str, rerank: str, n: int = Q.POOL_TOP_DOCS) -> list[str]:
    """The first ``n`` DISTINCT documents of one ranking — Stage 0's own walk."""
    seen: list[str] = []
    for j in ordered_pool(rec, mode, rerank):
        d = rec["chunks"][str(j)]["docno"]
        if d not in seen:
            seen.append(d)
        if len(seen) >= n:
            break
    return seen


def multi_top_docs(recs: dict, rerank: str, n: int = Q.POOL_TOP_DOCS) -> list[str]:
    from s0b_pack import multi_order
    seen: list[str] = []
    for a, j in multi_order(recs, rerank):
        d = recs[a]["chunks"][str(j)]["docno"]
        if d not in seen:
            seen.append(d)
        if len(seen) >= n:
            break
    return seen


def main() -> None:
    t0 = time.time()
    qrels = json.loads((C.WORK / "qrels_all.json").read_text())
    docs_present = set()
    for line in open(C.WORK / "docs.jsonl"):
        docs_present.add(json.loads(line)["docno"])

    pools: dict = {}
    for arm in K.INDEX_KEYS:
        p = Q.POOLS / f"pool_{arm}.jsonl"
        assert p.exists(), f"missing pool for {arm}; run s0c_retrieve.py first"
        by = {}
        for line in open(p):
            r = json.loads(line)
            by[r["qid"]] = r
        pools[arm] = by

    topics = Q.conf_topics()
    Q.assert_no_dev(topics)

    # -- the delivery arms add no documents. ASSERTED, on real groups, not assumed. ------
    # A delivery arm's packing order is its source arm's ordered pool, and each group's
    # members carry the source chunk's docno (a parent slice is a slice of the same
    # document; a neighbour chunk is an adjacent chunk of the same document). So the first
    # 20 distinct documents a delivery arm would deliver are its base arm's. This is
    # checked by building the arms' actual groups on a sample of queries covering every
    # (delivery arm, mode, rerank) cell — if it ever failed, the pool would be short.
    from s0b_pack import Packer
    _pk = Packer(K.load_docs(), K.load_units(), K.load_headers(), K.SfrCount())
    checked = 0
    for t in topics[:3]:
        for v in Q.VARIANTS:
            recs0 = {a: pools[a][f"{t}::{v}"] for a in K.INDEX_KEYS}
            for arm, (src, _kind, _w) in K.DELIVERY_ARMS.items():
                for mode in K.MODES:
                    for rr in K.RERANK_STATES:
                        rec = recs0[src]
                        seen: list[str] = []
                        for g in _pk.groups_for(arm, rec, ordered_pool(rec, mode, rr)):
                            for _k, d, *_rest in g:
                                if d not in seen:
                                    seen.append(d)
                            if len(seen) >= Q.POOL_TOP_DOCS:
                                break
                        assert seen[:Q.POOL_TOP_DOCS] == top_docs(rec, mode, rr), \
                            f"{arm} ({mode}/{rr}) ranks documents its source {src} does not"
                        checked += 1
    del _pk
    delivery_note = (f"asserted on {checked} (arm, mode, rerank, query) cells: parent256 / "
                     "nbr1_512 / nbr1_256 / nbr2_512 deliver the same first-20 documents "
                     "as their source index arm, so they contribute no document outside "
                     "their base arm's top-20")

    lset: dict = {}
    n_rankings = 0
    per_topic = []
    for t in topics:
        rel = {d for d, g in qrels[t].items() if g >= 1 and d in docs_present}
        pooled: set[str] = set()
        pooled_conf: set[str] = set()          # hybrid + rerank-on only, for the count
        rk = 0
        for v in Q.VARIANTS:
            qid = f"{t}::{v}"
            recs = {a: pools[a][qid] for a in K.INDEX_KEYS}
            for arm in K.INDEX_KEYS:
                for mode in K.MODES:
                    for rr in K.RERANK_STATES:
                        ds = top_docs(recs[arm], mode, rr)
                        rk += 1
                        pooled |= {d for d in ds if d in rel}
                        if mode == "hybrid" and rr == "on":
                            pooled_conf |= {d for d in ds if d in rel}
            for rr in K.RERANK_STATES:
                ds = multi_top_docs(recs, rr)
                rk += 1
                pooled |= {d for d in ds if d in rel}
        n_rankings = rk
        rng = random.Random(f"{Q.SEED_BIASBOUND}:{t}")   # s0_label.labeling_set's draw
        rest = sorted(rel - pooled)
        sample = rng.sample(rest, min(Q.BIAS_BOUND_N, len(rest)))
        lset[t] = {"pooled": sorted(pooled), "sample": sorted(sample),
                   "n_rel_total": len(rel)}
        per_topic.append({"topic": t, "n_pooled": len(pooled), "n_sample": len(sample),
                          "n_pairs": len(pooled) + len(sample),
                          "n_pooled_confirmatory_path_only": len(pooled_conf),
                          "n_rel_indexed": len(rel),
                          "n_rel_qrels": sum(1 for g in qrels[t].values() if g >= 1)})

    Q.atomic_json(Q.POOL / "labeling_set.json", lset)

    npool = [x["n_pooled"] for x in per_topic]
    nsamp = [x["n_sample"] for x in per_topic]
    npair = [x["n_pairs"] for x in per_topic]
    nconf = [x["n_pooled_confirmatory_path_only"] for x in per_topic]

    def dist(xs):
        s = sorted(xs)
        n = len(s)
        return {"n_topics": n, "total": sum(s), "min": s[0], "max": s[-1],
                "mean": round(sum(s) / n, 2), "median": s[n // 2],
                "p10": s[max(0, int(0.10 * n) - 1)], "p90": s[min(n - 1, int(0.90 * n))],
                "histogram": {str(b): sum(1 for x in s if b <= x < b + 10)
                              for b in range(0, (max(s) // 10 + 1) * 10, 10)}}

    counts = {
        "WHAT_THIS_IS": "counts of the labeling set. Counts are not outcomes: no ranking, "
                        "score, arm or mode is recorded here or in labeling_set.json.",
        "topics": len(topics),
        "rankings_pooled_per_topic": n_rankings,
        "rankings_composition": "6 index arms x 3 modes x 2 rerank states x 2 variants "
                                "(72) + multi256+1024 (vector only) x 2 rerank x 2 "
                                "variants (4) = 76",
        "delivery_arms": delivery_note,
        "top_documents_per_ranking": Q.POOL_TOP_DOCS,
        "bias_bound_per_topic": Q.BIAS_BOUND_N,
        "bias_bound_seed": f"random.Random(f'{Q.SEED_BIASBOUND}:<topic>') — "
                           "s0_label.labeling_set's own per-topic string seed",
        "pooled": dist(npool),
        "bias_bound_sample": dist(nsamp),
        "pairs_per_topic": dist(npair),
        "pooled_confirmatory_path_only": dist(nconf),
        "totals": {"pooled": sum(npool), "sample": sum(nsamp), "pairs": sum(npair),
                   "pooled_confirmatory_path_only": sum(nconf)},
        "r2_6_3_expectation": {"pooled": "<= 3,600", "bias_bound": "<= 800"},
        "seconds": round(time.time() - t0, 1),
        "provenance": Q.provenance(),
    }
    Q.atomic_json(Q.POOL / "pool_counts.json", counts)
    Q.atomic_json(Q.ART / "pool_counts.json",
                  {k: v for k, v in counts.items() if k != "provenance"})
    print(json.dumps({k: counts[k] for k in
                      ("topics", "rankings_pooled_per_topic", "pooled",
                       "bias_bound_sample", "pairs_per_topic",
                       "pooled_confirmatory_path_only", "totals")}, indent=1), flush=True)


if __name__ == "__main__":
    sys.exit(main())
