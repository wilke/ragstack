"""Stage 0b step 6 -- the SS7.6 manipulation checks (row 9), run BEFORE any contrast is read.

1. **GOLD packing control** -- pack each topic's own labeled spans directly; ``EUC`` must be
   >= 0.95. This tests the metric plumbing (D4 containment, the union algebra, the unit
   store) and nothing else.
2. **NEGATIVE control** -- contexts packed from grade-0 documents only; ``EUC`` must be
   <= 0.05.
3. **Discrimination (defect 3 closed)** -- per arm, document ``Hit@1`` over the shared
   corpus must be < 1.0, and the per-topic top-10 document sets must differ across the size
   extremes for >= 25% of topics.
4. **Budget bind** -- realised tokens at B = 4,096 inside [0.85B, B] except the A1 rank-1
   overshoot cases, which are counted.
5. dev ``EUC`` level inside [0.15, 0.90] (SS8.5.7 row 9) -- a floor or ceiling effect
   destroys a variance estimate as surely as anything else in the gate.
"""
from __future__ import annotations

import json
import statistics as st

import s0_common as C
from s0_score import covered, merge_iv

B = str(C.PRIMARY_BUDGET)


def main() -> None:
    euc = json.loads((C.WORK / "euc.json").read_text())
    uinfo = json.loads((C.WORK / "units.json").read_text())
    packed = json.loads((C.WORK / "packed.json").read_text())
    qrels = json.loads((C.WORK / "qrels_all.json").read_text())
    units = uinfo["units"]
    arms = sorted(packed)
    out: dict = {}

    # ---- 1. GOLD packing control ------------------------------------------------
    gold = {}
    for t, us in units.items():
        un: dict = {}
        for u in us:
            un.setdefault(u["docno"], []).extend([[s["start"], s["end"]] for s in u["spans"]])
        un = {d: [tuple(x) for x in merge_iv(v)] for d, v in un.items()}
        gold[t] = sum(covered(u, un) for u in us) / len(us) if us else float("nan")
    out["check1_gold_packing"] = {
        "per_topic": {t: round(v, 4) for t, v in gold.items()},
        "mean": round(st.mean(gold.values()), 4) if gold else None,
        "min": round(min(gold.values()), 4) if gold else None,
        "requirement": ">= 0.95",
        "PASS": bool(gold) and min(gold.values()) >= 0.95}

    # ---- 2. NEGATIVE control ----------------------------------------------------
    neg = {}
    for arm in arms:
        vals = []
        for t, us in units.items():
            rec = packed[arm]["summary"].get(t)
            if rec is None or not us:
                continue
            un = {d: [tuple(x) for x in iv] for d, iv in rec[B]["union"].items()
                  if qrels[t].get(d, 0) == 0}
            vals.append(sum(covered(u, un) for u in us) / len(us))
        neg[arm] = round(st.mean(vals), 5) if vals else None
    out["check2_negative_control"] = {
        "per_arm_mean_EUC_grade0_only": neg, "requirement": "<= 0.05",
        "PASS": all(v is not None and v <= 0.05 for v in neg.values())}

    # ---- 3. discrimination ------------------------------------------------------
    hit1, top10 = {}, {}
    for arm in C.INDEX_KEYS:
        p = C.WORK / f"pool_{arm}.json"
        if not p.exists():
            continue
        pool = json.loads(p.read_text())
        hs = []
        for t in C.DEV_TOPICS:
            rr = pool["summary"][t]["reranked"]
            hs.append(1 if qrels[t].get(rr[0][0], 0) >= 1 else 0)
            docs, seen = [], set()
            for d, *_ in rr:
                if d not in seen:
                    seen.add(d)
                    docs.append(d)
                if len(docs) >= 10:
                    break
            top10.setdefault(arm, {})[t] = docs
        hit1[arm] = round(sum(hs) / len(hs), 4)
    lo, hi = "fixed_tok256_ov0pct", "fixed_tok2048_ov0pct"
    differ = None
    if lo in top10 and hi in top10:
        differ = sum(1 for t in C.DEV_TOPICS if set(top10[lo][t]) != set(top10[hi][t]))
    out["check3_discrimination"] = {
        "doc_hit_at_1_per_arm": hit1, "requirement_hit1": "< 1.0",
        "topics_with_different_top10_across_size_extremes": differ,
        "of_topics": len(C.DEV_TOPICS), "requirement_differ": ">= 25% of topics",
        "PASS": bool(hit1) and all(v < 1.0 for v in hit1.values())
                and (differ is None or differ >= 0.25 * len(C.DEV_TOPICS))}

    # ---- 4. budget bind ---------------------------------------------------------
    bind = {}
    for arm in arms:
        vals, over = [], 0
        for t in C.DEV_TOPICS:
            rec = packed[arm]["summary"].get(t)
            if rec is None:
                continue
            r = rec[B]["raw_tokens"]
            vals.append(r)
            if r > C.PRIMARY_BUDGET:
                over += 1
        inband = sum(1 for r in vals
                     if 0.85 * C.PRIMARY_BUDGET <= r <= C.PRIMARY_BUDGET)
        bind[arm] = {"mean_realised": round(st.mean(vals), 1) if vals else None,
                     "in_band": inband, "n": len(vals),
                     "rank1_overshoot_cases": over}
    out["check4_budget_bind"] = {
        "per_arm": bind, "band": [0.85 * C.PRIMARY_BUDGET, C.PRIMARY_BUDGET],
        "PASS": all(v["in_band"] + v["rank1_overshoot_cases"] >= v["n"] for v in bind.values())}

    # ---- 5. dev EUC level -------------------------------------------------------
    lvl = {}
    for arm in arms:
        if arm in euc:
            vals = [v["EUC"] for v in euc[arm]["summary"][B].values()]
            lvl[arm] = round(st.mean(vals), 4)
    out["check5_dev_EUC_level"] = {
        "per_arm_mean_EUC@4096_summary": lvl, "window": [0.15, 0.90],
        "PASS": all(0.15 <= v <= 0.90 for v in lvl.values()) if lvl else False}

    out["ALL_PASS"] = all(out[k]["PASS"] for k in out if k.startswith("check"))
    C.atomic_json(C.WORK / "checks.json", out)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
