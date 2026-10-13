#!/usr/bin/env python3
"""Hallucinated-span rates, Wilson 95 % upper bounds and the anchor split, recomputed.

Definition (DRAFT §3.2, §5.2; RESULTS-stage0b-relabel*.md): a hallucinated span is one whose
quoted anchor cannot be located in the document; the rate is failed spans over ALL spans the
judge attempted. The per-record counts are the labeler's own bookkeeping (`vstats.spans_seen`,
`vstats.hallucinated`); this script re-sums them independently and cross-checks the failure
count against the record's `problems` list (one entry per failed anchor), which is written by a
different code path in the labeler.

Re-verifying each quote against the article text would need the documents and a re-implementation
of the exact / normalised / eight-word locator ladder; that is NOT done here (see README).
"""
from __future__ import annotations

import vcommon as V

FIRST, LAST, EMPTY = "quote_not_in_document", "last_quote_not_in_document", "no_first_words"


def tally(records) -> dict:
    seen = fail = 0
    anchors = {FIRST: 0, LAST: 0, EMPTY: 0}
    for r in records:
        vs = r.get("vstats") or {}
        seen += vs.get("spans_seen", 0)
        fail += vs.get("hallucinated", 0)
        for p in r["problems"]:
            if p in anchors:
                anchors[p] += 1
    out = {"failed_spans": fail, "attempted_spans": seen,
           "rate": fail / seen if seen else None,
           "wilson95_upper": V.wilson_upper(fail, seen) if seen else None,
           "by_anchor_from_problems": anchors,
           "failures_from_problems": sum(anchors.values())}
    out["problems_agree_with_vstats"] = out["failures_from_problems"] == fail
    return out


def main() -> None:
    prov = V.provenance()
    res: dict = {}

    s0 = V.read_jsonl(V.F["s0"])
    t = tally(s0)
    seen = t["attempted_spans"]
    ok = sum((r.get("vstats") or {}).get("index_ok", 0) for r in s0)
    mis = sum((r.get("vstats") or {}).get("misindexed", 0) for r in s0)
    t["index_agreement"] = {"index_ok": ok, "misindexed_relocated": mis,
                            "rate": ok / seen,
                            "misindexed_from_problems": sum(
                                p == "misindexed_relocated" for r in s0 for p in r["problems"])}
    res["s0"] = t

    for j in ("scout", "qwen"):
        recs = V.read_jsonl(V.F["r3"][j])
        t = tally(recs)                                 # primary presentation only
        dseen = sum((r.get("dup_vstats") or {}).get("spans_seen", 0) for r in recs)
        dfail = sum((r.get("dup_vstats") or {}).get("hallucinated", 0) for r in recs)
        t["duplicate_presentation"] = {"failed": dfail, "attempted": dseen}
        t["including_duplicates_rate"] = (t["failed_spans"] + dfail) / (
            t["attempted_spans"] + dseen)
        t["spans_per_positive_pair"] = (t["attempted_spans"] /
                                        sum(1 for r in recs if r["sets"] or r["dropped"]))
        res[f"r3.{j}"] = t

    for j in ("scout", "qwen"):
        by = V.load("r31", j)
        res[f"r31.{j}.k0"] = tally([v[0] for v in by.values()])
        res[f"r31.{j}.all5"] = tally([v[p] for v in by.values() for p in range(5)])

    ext = {j: V.load("r31ext", j) for j in ("scout", "qwen")}
    res["p_ext_3.scout.k5_19"] = tally([v[p] for v in ext["scout"].values()
                                        for p in range(5, 20)])
    res["p_ext_3.qwen.k5_9"] = tally([v[p] for v in ext["qwen"].values()
                                      for p in range(5, 10)])
    res["p_ext_3.qwen.k0_4"] = tally([v[p] for v in ext["qwen"].values() for p in range(5)])
    res["p_ext_3.SCORED"] = ("PASS" if res["p_ext_3.scout.k5_19"]["rate"] <= 0.05
                             and res["p_ext_3.qwen.k5_9"]["rate"] <= 0.05 else "FAIL")

    for j in ("sonnet5", "opus5", "fable51"):
        recs = V.read_jsonl(V.F["claude"][j])
        t = tally(recs)
        t["pairs"] = len(recs)
        res[f"claude.{j}"] = t

    V.write("recompute_hallucination", prov, {"results": res})


if __name__ == "__main__":
    main()
