"""Stage 0b' -- persist the packed contexts the synthesis stage will consume.

``SPEC-synthesis-stage.md`` SS2 consumes "exactly what SS7.3's rule admits" per
(query, configuration). Two forms are written, and the index committed to the repo says
which is which:

* **reference form, every configuration** -- ``contexts/packed-<population>.jsonl.gz``
  (written by ``s0b_pack.py``): the admitted chunk list and the per-document character-span
  union for all 2 populations x 11 arms x 3 modes x 2 rerank states x 3 budgets. The text
  is reconstructible exactly from ``work/docs.jsonl`` by character offset, which is why the
  reference form is the primary artifact: materialising every configuration as literal text
  would be several gigabytes of duplicated corpus.
* **materialised text, the primary configuration** -- ``contexts/text/<population>/
  <arm>.jsonl.gz`` at B = 16,384 SFR tokens, ``hybrid`` mode, reranker on: the context
  string a generator would receive, chunk by chunk, with the citation identity
  (``docno``, char span) beside each piece.

``contexts/INDEX.json`` is copied into ``artifacts/stage0b-prime/`` and committed.
"""
from __future__ import annotations

import gzip
import json
import sys
import time

import s0b_common as K
import s0_common as C  # noqa: F401

MODE = "hybrid"
RERANK = "on"


def main() -> None:
    t0 = time.time()
    docs = K.load_docs()
    headers = K.load_headers()
    qmeta = {q["qid"]: q for q in
             json.loads((K.OUT / "query_meta.json").read_text())["queries"]}
    (K.CTX / "text").mkdir(exist_ok=True)
    files = {}
    counts: dict = {}
    for pop in ("cds", "pointed"):
        (K.CTX / "text" / pop).mkdir(exist_ok=True)
        handles = {}
        with gzip.open(K.CTX / f"packed-{pop}.jsonl.gz", "rt") as f:
            for line in f:
                r = json.loads(line)
                if r["mode"] != MODE or r["rerank"] != RERANK:
                    continue
                pk = r["budgets"][str(K.PRIMARY_BUDGET)]
                arm = r["arm"]
                if arm not in handles:
                    handles[arm] = gzip.open(
                        K.CTX / "text" / pop / f"{arm}.jsonl.gz", "wt")
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
                counts[f"{pop}/{arm}"] = counts.get(f"{pop}/{arm}", 0) + 1
        for arm, h in handles.items():
            h.close()
            p = K.CTX / "text" / pop / f"{arm}.jsonl.gz"
            files[f"text/{pop}/{arm}.jsonl.gz"] = {
                "bytes": p.stat().st_size, "sha256": K.sha256_file(p),
                "records": counts[f"{pop}/{arm}"]}
    for pop in ("cds", "pointed"):
        p = K.CTX / f"packed-{pop}.jsonl.gz"
        files[f"packed-{pop}.jsonl.gz"] = {
            "bytes": p.stat().st_size, "sha256": K.sha256_file(p)}

    index = {
        "root": str(K.CTX),
        "what": "the packed contexts of every Stage 0b' configuration, for the synthesis "
                "stage (SPEC-synthesis-stage.md SS2)",
        "reference_form": {
            "files": [f"packed-{p}.jsonl.gz" for p in ("cds", "pointed")],
            "record": "{qid, population, topic, variant, arm, mode, rerank, "
                      "budgets:{B:{sfr_tokens, sfr_tokens_per_source_duplicated, "
                      "gen_tokens_est, n_items, n_sources, n_docs, "
                      "items:[[docno,start,end,sfr]], union:{docno:[[start,end]]}}}}",
            "reconstruct": "text = docs[docno][start:end] from "
                           "/rag/tmp/stage0-conf/work/docs.jsonl; for header512 prepend "
                           "the header from chunks/spans_header512.jsonl",
            "configurations": "2 populations x 11 arms x 3 modes x 2 rerank states x "
                              "3 budgets (multi256+1024 is vector-only, per r3 SS3.4)"},
        "materialised_text": {
            "selection": {"budget_sfr": K.PRIMARY_BUDGET, "mode": MODE,
                          "rerank": RERANK},
            "files": [f"text/{p}/{a}.jsonl.gz" for p in ("cds", "pointed")
                      for a in K.SCORING_ARMS]},
        "budget_units": "SFR tokens (r3 SS3.3); generator tokens are the estimate "
                        f"sfr / {K.SFR_PER_GEN:.4f} and are descriptive only",
        "files": files,
        "seconds": round(time.time() - t0, 1),
        "provenance": K.provenance(),
    }
    K.atomic_json(K.CTX / "INDEX.json", index)
    K.atomic_json(K.ART / "contexts-INDEX.json", index)
    print(json.dumps({"files": len(files), "seconds": index["seconds"]}, indent=1),
          flush=True)


if __name__ == "__main__":
    sys.exit(main())
