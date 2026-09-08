"""Confirmation run (a), step 2 — packing the 11 scoring arms. **QUARANTINED.**

`s0b_pack.py` (Stage 0b′, #520) applied to the confirmation pools. The packing walk, the
group semantics, the parent slice, the neighbour de-duplication and the header accounting
are that module's and are **imported, not copied** (``pack_groups``, ``ordered_pool``,
``multi_order``, ``Packer``), so a rule that holds at Stage 0b′ holds here by construction.

Eleven scoring arms from six retrieval pools, three modes, two rerank states, three budgets
in SFR tokens (4,096 / **16,384** / 32,768 — r3 §3.2, counted in the one tokenizer r3 §3.3
mandates for this path).

**Quarantine.** The record written per (arm, mode, rerank, query) carries the admitted chunk
list, the per-document character-span union and the realised token totals — the reference
form Stage 0b′ defined. None of it is summarised, printed or aggregated here. The progress
lines carry counts and wall-clock only.

Output: ``work/conf/contexts/packed-conf.jsonl.gz`` + ``work/conf/contexts/pack_meta.json``.
"""
from __future__ import annotations

import gzip
import json
import sys
import time

import s0c_common as Q          # noqa: I001
import s0b_common as K
from s0b_pack import Packer, multi_order, ordered_pool, pack_groups


def main() -> None:
    t0 = time.time()
    docs = K.load_docs()
    units = K.load_units()
    headers = K.load_headers()
    tok = K.SfrCount()
    pk = Packer(docs, units, headers, tok)

    pools = {}
    for arm in K.INDEX_KEYS:
        p = Q.POOLS / f"pool_{arm}.jsonl"
        assert p.exists(), f"missing pool for {arm}; run s0c_retrieve.py first"
        by = {}
        for line in open(p):
            r = json.loads(line)
            Q.assert_conf_only([r["topic"]])
            by[r["qid"]] = r
        pools[arm] = by
    qids = list(pools[K.INDEX_KEYS[0]])
    for a in K.INDEX_KEYS:
        assert list(pools[a]) == qids, a
    assert len(qids) == 160, len(qids)
    print(f"pools loaded: {len(K.INDEX_KEYS)} arms x {len(qids)} queries "
          f"{time.time()-t0:.0f}s", flush=True)

    out_path = Q.CTX / "packed-conf.jsonl.gz"
    tmp = out_path.with_suffix(".gz.tmp")
    n_records = 0
    with gzip.open(tmp, "wt") as h:
        for qi, qid in enumerate(qids):
            recs = {a: pools[a][qid] for a in K.INDEX_KEYS}
            base = {"qid": qid, "population": "cds_conf",
                    "topic": recs[K.INDEX_KEYS[0]]["topic"],
                    "variant": recs[K.INDEX_KEYS[0]]["variant"]}
            for rerank in K.RERANK_STATES:
                for mode in K.MODES:
                    for arm in K.SCORING_ARMS:
                        if arm == K.MULTI_ARM:
                            if mode != "vector":
                                continue
                            order = multi_order(recs, rerank)
                            g = []
                            for a, j in order:
                                c = recs[a]["chunks"][str(j)]
                                g.append([(f"{a}:{j}", c["docno"], c["start"], c["end"],
                                           c["sfr"], True)])
                        else:
                            src = arm if arm in K.INDEX_KEYS else K.DELIVERY_ARMS[arm][0]
                            rec = recs[src]
                            g = pk.groups_for(arm, rec, ordered_pool(rec, mode, rerank))
                        packed = pack_groups(g, K.BUDGETS)
                        h.write(json.dumps({**base, "arm": arm, "mode": mode,
                                            "rerank": rerank, "budgets": packed}) + "\n")
                        n_records += 1
            if (qi + 1) % 20 == 0:
                print(f"  packed {qi+1}/{len(qids)} {time.time()-t0:.0f}s", flush=True)
    tmp.replace(out_path)

    meta = {
        "rule": "A1: stop-at-first-non-fit, rank-1 always admitted, never truncated, "
                "already-supplied text free and does not end the walk",
        "imported_from": "s0b_pack.py (Stage 0b', #520) — pack_groups / ordered_pool / "
                         "multi_order / Packer are that module's, not re-implemented",
        "budget_tokenizer": K.SFR_TOKENIZER_ID,
        "budget_units": "SFR tokens (r3 §3.3, the Stage 0b' path; the confirmation run's "
                        "own re-chunk in the generator's tokenizer is NOT done here — see "
                        "the write-up's deviations)",
        "generator_tokens": K.GEN_TOKENIZER_STATUS,
        "budgets": list(K.BUDGETS), "primary": K.PRIMARY_BUDGET,
        "scoring_arms": K.SCORING_ARMS, "modes": list(K.MODES),
        "rerank_states": list(K.RERANK_STATES),
        "queries": len(qids), "records": n_records,
        "tokenizer_calls": tok.calls,
        "seconds": round(time.time() - t0, 1),
        "sha256": Q.sha256_file(out_path),
        "bytes": out_path.stat().st_size,
        "quarantine": "packed contexts written, never summarised; no endpoint value is "
                      "computed from them by this task",
        "provenance": Q.provenance(),
    }
    Q.atomic_json(Q.CTX / "pack_meta.json", meta)
    print(json.dumps({k: meta[k] for k in
                      ("queries", "records", "tokenizer_calls", "seconds", "bytes",
                       "sha256")}, indent=1), flush=True)


if __name__ == "__main__":
    sys.exit(main())
