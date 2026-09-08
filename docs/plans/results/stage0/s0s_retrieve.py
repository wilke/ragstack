"""Step 1 -- retrieval for the pointed set over nested SUBSAMPLES of the existing corpus.

For each of the six index arms and each subset (10 of them: 3 draws x {4k, 8k, 16k} plus
the one full corpus), the three Stage 0b' modes are run **exactly as Stage 0b' ran them**:

* ``vector`` -- exact cosine over the arm's frozen embeddings, restricted to the subset's
  rows, top ``LEG_DEPTH`` = 100, truncated to the 50-chunk pool;
* ``bm25``   -- ``s0b_bm25.ArmBM25`` rebuilt **on the subset's rows**, so ``df``, ``N``
  and ``avgdl`` are the subset's own -- which is the whole point: a smaller corpus has a
  different idf, and a projection that froze the full-corpus idf would be measuring
  something else;
* ``hybrid`` -- RRF (k = 60) over the two 100-item legs, truncated to 50.

Then the union of the three pools is reranked on ``:50052`` (<= 4 in flight).

**Two economies, both exact.**

1. The dense similarity matrix for the 177 queries is computed **once per arm** over the
   whole embedding matrix and then *masked* per subset. A subset's top-100 is the top-100
   of the masked columns; nothing is approximated.
2. Reranker scores are cached per ``(query, chunk row)`` **across subsets**. The
   crossencoder scores a (query, chunk text) pair; neither depends on which other
   documents are in the corpus. The cache is what keeps the pair count near one subset's
   worth instead of ten.

**Query vectors are Stage 0b's frozen ones** (``stage0b-prime/query_vectors.npy``), not
re-embedded: the full-corpus point of this study must be the *same* number Stage 0b'
published, and reusing the vectors is how that is guaranteed rather than asserted. Zero
embedding calls are made by step 1.

Output: ``work/pointed-scale/pool_<arm>.jsonl`` -- one record per (subset, query).
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

import s0s_common as SC
import s0b_common as K
import s0_common as C
from s0b_bm25 import ArmBM25, CorpusTokens
from s0b_retrieve import rrf

sys.path.insert(0, str(C.STAGE1))
sys.path.insert(0, str(C.PILOTS))

BLOCK = 200_000


def frozen_query_vectors(qs: list[dict]) -> np.ndarray:
    """Stage 0b's own pointed query vectors, in this module's query order."""
    src = K.OUT / "query_vectors.npy"
    meta = json.loads((K.OUT / "query_meta.json").read_text())
    Q = np.load(src)
    idx = {q["qid"]: i for i, q in enumerate(meta["queries"])}
    rows = [idx[q["qid"]] for q in qs]
    V = Q[rows].astype(np.float32)
    V /= np.linalg.norm(V, axis=1, keepdims=True)
    return V


def dense_topk(sims: np.ndarray, cols: np.ndarray, k: int):
    """Top-``k`` GLOBAL row indices per query over the subset's columns ``cols``."""
    sub = sims[:, cols]
    kk = min(k, sub.shape[1])
    part = np.argpartition(-sub, kk - 1, axis=1)[:, :kk]
    val = np.take_along_axis(sub, part, axis=1)
    order = np.argsort(-val, axis=1)
    part = np.take_along_axis(part, order, axis=1)
    val = np.take_along_axis(val, order, axis=1)
    return cols[part], val


def main() -> None:
    import pilot_common as P

    smoke = "--smoke" in sys.argv
    only = [a for a in sys.argv[1:] if not a.startswith("-")]

    qs = SC.load_pointed()
    gold = {q["docno"] for q in qs}
    print(f"pointed queries: {len(qs)}, gold documents: {len(gold)}", flush=True)

    docs = K.load_docs()
    all_docnos = sorted(docs)
    print("docs loaded:", len(all_docnos), flush=True)

    if smoke:
        subs = SC.subsets(all_docnos, gold, sizes=(SC.SMOKE_SIZE + len(gold),), draws=1)
        arms = [K.INDEX_KEYS[1]]
        outdir = SC.OUT / "smoke"
        outdir.mkdir(parents=True, exist_ok=True)
    else:
        subs = SC.subsets(all_docnos, gold)
        arms = [a for a in K.INDEX_KEYS if not only or a in only]
        outdir = SC.OUT
    print("subsets:", [(s["key"], s["size"]) for s in subs], flush=True)

    Q = frozen_query_vectors(qs)
    print("query vectors (frozen, Stage 0b'):", Q.shape, flush=True)

    ct = CorpusTokens(docs)
    print(f"corpus tokenized: {ct.n_tokens} tokens, {len(ct.vocab)} types, {ct.seconds}s",
          flush=True)
    qterms = {q["qid"]: ct.term_ids(q["text"]) for q in qs}
    qvocab = {t for v in qterms.values() for t in v}
    print("query vocabulary:", len(qvocab), flush=True)

    ce = P.CE(concurrency=4)
    manifest: dict = {"arms": {}, "queries": len(qs), "gold_documents": len(gold),
                      "query_vocab": len(qvocab), "corpus_tokens": ct.n_tokens,
                      "corpus_types": len(ct.vocab),
                      "subsets": [{"key": s["key"], "size": s["size"], "draw": s["draw"]}
                                  for s in subs],
                      "query_vectors": "REUSED from stage0b-prime/query_vectors.npy "
                                       "(0 embedding calls in step 1)"}

    for arm in arms:
        outp = outdir / f"pool_{arm}.jsonl"
        if outp.exists():
            print("skip (done):", arm, flush=True)
            continue
        t0 = time.time()
        rows = K.load_rows(arm)
        headers = K.load_headers() if arm == "header512" else None
        E = np.load(K.EMB / f"emb_{arm}.npy", mmap_mode="r")
        assert E.shape[0] == len(rows), (E.shape, len(rows))

        # -- one dense pass over the whole matrix, masked per subset afterwards ----
        sims = np.empty((len(qs), E.shape[0]), dtype=np.float32)
        for s in range(0, E.shape[0], BLOCK):
            B = np.array(E[s:s + BLOCK], dtype=np.float32)
            nrm = np.linalg.norm(B, axis=1, keepdims=True)
            nrm[nrm == 0] = 1.0
            B /= nrm
            sims[:, s:s + B.shape[0]] = Q @ B.T
            del B
        del E
        t_dense = time.time() - t0
        print(f"[{arm}] dense pass {t_dense:.0f}s  sims {sims.shape}", flush=True)

        row_doc = [r[0] for r in rows]
        ce_cache: dict[tuple[int, int], float] = {}
        arm_stat = {"chunks": len(rows), "dense_seconds": round(t_dense, 1),
                    "subsets": {}}
        recs_out = open(outp.with_suffix(".jsonl.tmp"), "w")

        for sub in subs:
            ts = time.time()
            keep = set(sub["docnos"])
            cols = np.fromiter((i for i, d in enumerate(row_doc) if d in keep),
                              dtype=np.int64)
            srows = [rows[i] for i in cols]
            bm = ArmBM25(srows, ct, qvocab, headers)
            t_bm = time.time() - ts
            dense_idx, dense_val = dense_topk(sims, cols, K.LEG_DEPTH)

            def work(qi_q):
                qi, q = qi_q
                dv, dvv = dense_idx[qi], dense_val[qi]
                dense = [int(x) for x in dv]
                dscore = {int(i): float(v) for i, v in zip(dv, dvv)}
                bidx, bval = bm.topk(qterms[q["qid"]], K.LEG_DEPTH)
                lex = [int(cols[x]) for x in bidx]
                lscore = {int(cols[i]): float(v) for i, v in zip(bidx, bval)}
                fused = rrf([dense, lex])
                fscore = {x: s for x, s in fused}
                pools = {"vector": dense[:K.DEPTH], "bm25": lex[:K.DEPTH],
                         "hybrid": [x for x, _s in fused][:K.DEPTH]}
                union = sorted({x for p in pools.values() for x in p})
                todo = [j for j in union if (qi, j) not in ce_cache]
                if todo:
                    texts = []
                    for j in todo:
                        d, a, b, _nt = rows[j]
                        h = headers.get((d, a), "") if headers else ""
                        texts.append(h + docs[d][a:b])
                    sc = ce.score(q["text"], texts)
                    for j, x in zip(todo, sc):
                        ce_cache[(qi, j)] = float(x)
                return {
                    "subset": sub["key"], "size": sub["size"], "draw": sub["draw"],
                    "qid": q["qid"], "topic": q["topic"], "docno": q["docno"],
                    "arm": arm,
                    "chunks": {str(j): {"docno": rows[j][0], "start": rows[j][1],
                                        "end": rows[j][2], "sfr": rows[j][3],
                                        "ce": ce_cache[(qi, j)], "cos": dscore.get(j),
                                        "bm25": lscore.get(j), "rrf": fscore.get(j)}
                               for j in union},
                    "pools": pools}

            with ThreadPoolExecutor(4) as ex:
                for rec in ex.map(work, list(enumerate(qs))):
                    recs_out.write(json.dumps(rec) + "\n")
            arm_stat["subsets"][sub["key"]] = {
                "size": sub["size"], "chunks_in_subset": int(cols.size),
                "bm25_index_seconds": round(t_bm, 1), "bm25_postings": bm.n_postings,
                "bm25_avgdl": round(bm.avgdl, 2), "seconds": round(time.time() - ts, 1)}
            print(f"  [{arm}] {sub['key']} chunks={cols.size} "
                  f"{time.time()-ts:.0f}s  ce_pairs={ce.pairs}", flush=True)
            del bm, dense_idx, dense_val

        recs_out.close()
        outp.with_suffix(".jsonl.tmp").replace(outp)
        arm_stat["sha256"] = K.sha256_file(outp)
        arm_stat["seconds"] = round(time.time() - t0, 1)
        arm_stat["ce_cache_entries"] = len(ce_cache)
        manifest["arms"][arm] = arm_stat
        manifest["ce"] = {"pairs": ce.pairs, "requests": ce.requests,
                          "retries": ce.retries, "seconds": round(ce.seconds, 1)}
        SC.atomic_json(outdir / "retrieve_manifest.json",
                       {**manifest, "provenance": SC.provenance()})
        print(f"[{arm}] done {time.time()-t0:.0f}s", flush=True)
        del sims, ce_cache

    SC.atomic_json(outdir / "retrieve_manifest.json",
                   {**manifest, "provenance": SC.provenance()})
    print("retrieval done", flush=True)


if __name__ == "__main__":
    main()
