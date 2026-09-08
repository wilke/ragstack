"""Confirmation run (a), step 3 — persist the packed contexts. **QUARANTINED.**

`s0b_contexts.py` (Stage 0b′, #520) applied to the confirmation population, writing the
same two forms:

* **reference form, every configuration** — ``contexts/packed-conf.jsonl.gz`` (written by
  ``s0c_pack.py``): the admitted chunk list and the per-document character-span union for
  80 topics × 2 variants × 11 arms × 3 modes × 2 rerank states × 3 budgets. The text is
  reconstructible exactly from ``work/docs.jsonl`` by character offset.
* **materialised text, the primary configuration** — ``contexts/text/<arm>.jsonl.gz`` at
  B = 16,384 SFR tokens, ``hybrid`` mode, reranker on.

``contexts/INDEX.json`` records both, with a sha256 per file. **Nothing is summarised**:
the index carries file sizes, record counts and digests, which are counts, not outcomes.
Unlike Stage 0b′'s index, this one is **not** copied into the committed ``artifacts/``
tree — a confirmation-topic artifact is not committed by this task.
"""
from __future__ import annotations

import gzip
import json
import sys
import time

import s0c_common as Q          # noqa: I001
import s0b_common as K

MODE = "hybrid"
RERANK = "on"


def main() -> None:
    t0 = time.time()
    docs = K.load_docs()
    headers = K.load_headers()
    qmeta = {q["qid"]: q for q in
             json.loads((Q.QUERIES / "query_meta.json").read_text())["queries"]}
    (Q.CTX / "text").mkdir(exist_ok=True)
    files: dict = {}
    counts: dict = {}
    handles: dict = {}
    with gzip.open(Q.CTX / "packed-conf.jsonl.gz", "rt") as f:
        for line in f:
            r = json.loads(line)
            if r["mode"] != MODE or r["rerank"] != RERANK:
                continue
            pk = r["budgets"][str(K.PRIMARY_BUDGET)]
            arm = r["arm"]
            if arm not in handles:
                handles[arm] = gzip.open(Q.CTX / "text" / f"{arm}.jsonl.gz", "wt")
            chunks = []
            for d, s, e, n in pk["items"]:
                h = headers.get((d, s), "") if arm == "header512" else ""
                chunks.append({"docno": d, "start": s, "end": e, "sfr": n,
                               "text": h + docs[d][s:e]})
            handles[arm].write(json.dumps({
                "qid": r["qid"], "query": qmeta[r["qid"]]["text"],
                "topic": r["topic"], "variant": r["variant"],
                "arm": arm, "mode": MODE, "rerank": RERANK,
                "budget_sfr": K.PRIMARY_BUDGET,
                "sfr_tokens": pk["sfr_tokens"],
                "gen_tokens_est": pk["gen_tokens_est"],
                "n_chunks": len(chunks), "n_docs": pk["n_docs"],
                "chunks": chunks}) + "\n")
            counts[arm] = counts.get(arm, 0) + 1
    for arm, h in handles.items():
        h.close()
        p = Q.CTX / "text" / f"{arm}.jsonl.gz"
        files[f"text/{arm}.jsonl.gz"] = {"bytes": p.stat().st_size,
                                         "sha256": Q.sha256_file(p),
                                         "records": counts[arm]}
    p = Q.CTX / "packed-conf.jsonl.gz"
    files["packed-conf.jsonl.gz"] = {"bytes": p.stat().st_size,
                                     "sha256": Q.sha256_file(p)}

    index = {
        "root": str(Q.CTX),
        "what": "the packed contexts of every confirmation-run configuration, in Stage "
                "0b's reference form (SPEC-synthesis-stage.md §2)",
        "QUARANTINED": True,
        "quarantine_doc": str(Q.QUARANTINE_DOC),
        "reference_form": {
            "files": ["packed-conf.jsonl.gz"],
            "record": "{qid, population, topic, variant, arm, mode, rerank, "
                      "budgets:{B:{sfr_tokens, sfr_tokens_per_source_duplicated, "
                      "gen_tokens_est, n_items, n_sources, n_docs, "
                      "items:[[docno,start,end,sfr]], union:{docno:[[start,end]]}}}}",
            "reconstruct": "text = docs[docno][start:end] from "
                           "/rag/tmp/stage0-conf/work/docs.jsonl; for header512 prepend "
                           "the header from chunks/spans_header512.jsonl",
            "configurations": "160 queries x 11 arms x 3 modes x 2 rerank states x 3 "
                              "budgets (multi256+1024 is vector-only, per r3 §3.4)"},
        "materialised_text": {
            "selection": {"budget_sfr": K.PRIMARY_BUDGET, "mode": MODE, "rerank": RERANK},
            "files": [f"text/{a}.jsonl.gz" for a in K.SCORING_ARMS]},
        "budget_units": "SFR tokens (r3 §3.3); generator tokens are the estimate "
                        f"sfr / {K.SFR_PER_GEN:.4f} and are descriptive only",
        "files": files,
        "seconds": round(time.time() - t0, 1),
        "provenance": Q.provenance(),
    }
    Q.atomic_json(Q.CTX / "INDEX.json", index)
    print(json.dumps({"files": len(files), "seconds": index["seconds"]}, indent=1),
          flush=True)


if __name__ == "__main__":
    sys.exit(main())
