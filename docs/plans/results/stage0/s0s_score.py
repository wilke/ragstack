"""Step 1 -- pack at 16,384 SFR and score the pointed endpoints, per (subset, arm, mode).

The walk is **Stage 0b's own** ``s0b_pack.pack_groups`` (revision 2 SS7.3's A1 rule, budget
counted in SFR tokens), and the ordering is **Stage 0b's own** ``s0b_pack.ordered_pool``.
Nothing about packing is re-implemented here; the only thing that changes between rows of
the output table is which documents were in the corpus when the pool was formed.

Endpoints, per r3 SS3.1 as Stage 0b' read them on this population:

* ``ERET``  = 1 iff the gold document has a chunk admitted into the packed context;
* ``EPACK`` = 1 iff the gold character-span union is **fully contained** (D4) in that
  document's admitted union. Reported **given reach** (the conditional Stage 0b' tabled)
  and unconditionally.

Both are macro-averaged over the 177 queries; one query per source document, so the
cluster is the query.

Only the six **index** arms are scored. Stage 0b's four delivery arms and ``multi256+1024``
are re-derivations of the same pools and would not change the reach-vs-size shape this
study is fitting; leaving them out is stated here rather than left to be noticed.

Output: ``work/pointed-scale/endpoints.jsonl.gz`` (one record per subset x arm x mode x
rerank x query) and ``artifacts/pointed-scale/levels.json``.
"""
from __future__ import annotations

import gzip
import json
import sys
import time

import s0s_common as SC
import s0b_common as K
from s0b_pack import ordered_pool, pack_groups
from s0b_score import contained


def main() -> None:
    t0 = time.time()
    smoke = "--smoke" in sys.argv
    outdir = SC.OUT / "smoke" if smoke else SC.OUT

    qs = {q["qid"]: q for q in SC.load_pointed()}
    docs = K.load_docs()
    headers = K.load_headers()
    tok = K.SfrCount()
    hdr_tokens: dict = {}

    def header_tokens(d, s):
        h = headers.get((d, s), "")
        if not h:
            return 0
        if (d, s) not in hdr_tokens:
            hdr_tokens[(d, s)] = tok.count(h)
        return hdr_tokens[(d, s)]

    arms = [a for a in K.INDEX_KEYS if (outdir / f"pool_{a}.jsonl").exists()]
    print("arms with pools:", arms, flush=True)
    assert arms, "no pools; run s0s_retrieve.py first"

    outp = outdir / "endpoints.jsonl.gz"
    n = 0
    with gzip.open(outp, "wt") as out:
        for arm in arms:
            for line in open(outdir / f"pool_{arm}.jsonl"):
                rec = json.loads(line)
                g = qs[rec["qid"]]
                ch = rec["chunks"]
                for rerank in K.RERANK_STATES:
                    for mode in K.MODES:
                        order = ordered_pool(rec, mode, rerank)
                        groups = []
                        for j in order:
                            c = ch[str(j)]
                            extra = (header_tokens(c["docno"], c["start"])
                                     if arm == "header512" else 0)
                            groups.append([(f"c{j}", c["docno"], c["start"], c["end"],
                                            c["sfr"] + extra, True)])
                        packed = pack_groups(groups, (SC.BUDGET,))[str(SC.BUDGET)]
                        iv = {d: [tuple(x) for x in v]
                              for d, v in packed["union"].items()}
                        d = g["docno"]
                        reach = d in iv
                        cont = bool(reach and all(contained(tuple(s), iv[d])
                                                  for s in g["spans"]))
                        out.write(json.dumps({
                            "subset": rec["subset"], "size": rec["size"],
                            "draw": rec["draw"], "arm": arm, "mode": mode,
                            "rerank": rerank, "qid": rec["qid"], "docno": d,
                            "budget": SC.BUDGET, "ERET": int(reach),
                            "EPACK": int(cont), "sfr_tokens": packed["sfr_tokens"],
                            "n_items": packed["n_items"], "n_docs": packed["n_docs"],
                        }) + "\n")
                        n += 1
            print(f"  scored {arm}  records={n}  {time.time()-t0:.0f}s", flush=True)

    SC.atomic_json(outdir / "score_meta.json", {
        "records": n, "budget_sfr": SC.BUDGET,
        "arms": arms, "modes": list(K.MODES), "rerank_states": list(K.RERANK_STATES),
        "packing": "s0b_pack.pack_groups (A1 walk), s0b_pack.ordered_pool -- Stage 0b's, "
                   "unmodified",
        "endpoint_definitions": {
            "ERET": "gold document has a chunk admitted into the packed context",
            "EPACK": "gold character-span union fully contained (D4) in that document's "
                     "admitted union"},
        "delivery_arms_excluded": sorted(set(K.SCORING_ARMS) - set(K.INDEX_KEYS)),
        "tokenizer_calls": tok.calls,
        "seconds": round(time.time() - t0, 1),
        "sha256": K.sha256_file(outp),
        "provenance": SC.provenance(),
    })
    print("scored", n, "records in", round(time.time() - t0, 1), "s", flush=True)


if __name__ == "__main__":
    main()
