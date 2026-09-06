"""Stage 0b step 6a -- the EUC floor: its mechanism, and the B/D recalibration SS8.5.7 row 9
calls for when the dev level falls outside [0.15, 0.90].

Row 9 says the dev ``EUC`` level "must sit in [0.15, 0.90] **or B/D are recalibrated**".
It sat at 0.025-0.09. This module does three things and nothing else:

1. **Decomposes** ``EUC = P(unit's document packed) x P(covered | packed)`` per arm, which
   is what makes the floor diagnosable rather than mysterious.
2. **Recalibrates B**: re-packs the SAME frozen pools at larger budgets and reports where,
   if anywhere, the level enters the window -- including the ``B = infinity`` ceiling
   implied by the frozen depth D = 50, which is the most any budget can reach without
   changing D.
3. **Recalibrates D's consequence**: reports the ceiling separately per arm, because a
   50-chunk pool holds very different numbers of DOCUMENTS at 256 vs 2048 tokens.

Nothing here is primary. The primary endpoint stays ``EUC@4096`` as frozen in P.6.
"""
from __future__ import annotations

import json
import statistics as st

import s0_common as C
from s0_pack import Tok, pack_one, parent_span
from s0_score import covered, inter, total

BIG_BUDGETS = (4096, 8192, 16384, 32768, 10**9)


def main() -> None:
    docs, units_map = {}, {}
    for line in open(C.WORK / "docs.jsonl"):
        r = json.loads(line)
        docs[r["docno"]] = r["text"]
    for line in open(C.WORK / "units.jsonl"):
        r = json.loads(line)
        units_map[r["docno"]] = r["units"]
    hdr = {}
    for line in open(C.CHUNKS / "spans_header512.jsonl"):
        r = json.loads(line)
        for i, (s, _e, _n) in enumerate(r["spans"]):
            hdr[(r["docno"], s)] = r["hdr"][i]
    U = json.loads((C.WORK / "units.json").read_text())["units"]
    packed = json.loads((C.WORK / "packed.json").read_text())
    tok = Tok()

    # ---------------- 1. decomposition at the frozen primary B = 4096 --------------
    decomp = {}
    for arm in sorted(packed):
        dp, cd = [], []
        for t, us in U.items():
            if not us:
                continue
            rec = packed[arm]["summary"][t][str(C.PRIMARY_BUDGET)]
            un = {d: [tuple(x) for x in iv] for d, iv in rec["union"].items()}
            for u in us:
                inp = u["docno"] in un
                dp.append(int(inp))
                if inp:
                    cd.append(int(covered(u, un)))
        decomp[arm] = {
            "P_doc_packed": round(st.mean(dp), 4),
            "P_covered_given_packed": round(st.mean(cd), 4) if cd else None,
            "product": round(st.mean(dp) * (st.mean(cd) if cd else 0), 4),
            "units_considered": len(dp)}

    # ---------------- 2/3. re-pack the frozen pools at larger budgets --------------
    pools = {a: json.loads((C.WORK / f"pool_{a}.json").read_text())
             for a in C.INDEX_KEYS if (C.WORK / f"pool_{a}.json").exists()}
    curve: dict = {}
    for arm in list(pools) + ["parent256"]:
        src = "fixed_tok256_ov0pct" if arm == "parent256" else arm
        for t, rec in pools[src]["summary"].items():
            if not U.get(t):
                continue
            cand = rec["reranked"]
            if arm == "parent256":
                items = []
                for d, s, e, _sc, _ri in cand:
                    a, b = parent_span(units_map.get(d, []), docs[d], s, e, tok)
                    items.append((d, a, b, tok.count(docs[d][a:b])))
            else:
                texts = [(hdr.get((d, s), "") if arm == "header512" else "")
                         + docs[d][s:e] for d, s, e, _sc, _ri in cand]
                tok.warm(texts)
                items = [(d, s, e, tok.count(x))
                         for (d, s, e, _sc, _ri), x in zip(cand, texts)]
            out = pack_one(items, BIG_BUDGETS)
            for B in BIG_BUDGETS:
                un = {d: [tuple(x) for x in iv]
                      for d, iv in out[str(B)]["union"].items()}
                us = U[t]
                curve.setdefault(arm, {}).setdefault(str(B), []).append(
                    (sum(covered(u, un) for u in us) / len(us),
                     out[str(B)]["raw_tokens"], len(un)))
        print("recalibrated", arm, flush=True)

    table = {arm: {B: {"EUC": round(st.mean([x[0] for x in v]), 4),
                       "mean_realised_tokens": round(st.mean([x[1] for x in v])),
                       "mean_docs_packed": round(st.mean([x[2] for x in v]), 2)}
                   for B, v in bs.items()} for arm, bs in curve.items()}

    reaches = {}
    for arm, bs in table.items():
        hit = [B for B in map(str, BIG_BUDGETS) if bs[B]["EUC"] >= 0.15]
        reaches[arm] = hit[0] if hit else "NEVER (not even at the D=50 pool ceiling)"

    out = {
        "why": "SS8.5.7 row 9: the dev EUC level must sit in [0.15, 0.90] or B/D are "
               "recalibrated. It did not. This is the recalibration, reported as a "
               "diagnostic; the frozen primary remains EUC@4096 (P.6).",
        "decomposition_at_B4096": decomp,
        "budget_curve": table,
        "smallest_budget_reaching_0.15": reaches,
        "ceiling_note": "B = 1e9 packs the WHOLE frozen D = 50 pool: it is the ceiling any "
                        "budget can reach without raising the depth D, which is frozen at "
                        "50 by P.4 (= production rerank_candidates).",
    }
    C.atomic_json(C.WORK / "floor_diagnostic.json", out)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
