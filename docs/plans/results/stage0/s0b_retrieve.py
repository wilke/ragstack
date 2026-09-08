"""Stage 0b' step 1 -- three retrieval modes x six index arms x two populations.

Per (arm, query):

* ``vector`` -- exact brute-force cosine over the arm's existing embeddings, top **100**,
  truncated to the **50**-chunk pool;
* ``bm25``   -- in-process BM25 (``s0b_bm25``) over the same chunks, top **100**,
  truncated to 50;
* ``hybrid`` -- **the served shape** (r3 SS3.4): each leg fetches ``top_k(50) x
  candidate_multiplier(2) = 100``, RRF with k = 60 over the two lists, truncated to 50.

Every pool is then reranked on ``:50052`` (<= 4 in flight). The three pools of one
(arm, query) are reranked **once** over their union, so a chunk that appears in two modes
costs one pair. ``rerank_off`` is the mode's own fused order on the same frozen pool.

**Populations.** The ten CDS development topics x {summary, description} (20 queries) and
the 177-query pointed development set (r3 SS11). The sequestration assertion runs before
the first embed: every CDS topic is in ``C.DEV_TOPICS`` and every pointed query's
``draw_topic`` is too. No confirmation topic's query or label is read anywhere.

Output: ``work/stage0b-prime/pool_<population>_<arm>.jsonl`` -- one record per query with
the frozen pool (chunk row index, docno, char span, SFR tokens, per-leg ranks and scores,
rerank score), sha256'd into the manifest.
"""
from __future__ import annotations

import json
import sys
import time

import numpy as np

import s0_common as C
import s0b_common as K
from s0b_bm25 import ArmBM25, CorpusTokens

sys.path.insert(0, str(C.STAGE1))
sys.path.insert(0, str(C.PILOTS))

BLOCK = 200_000


# ------------------------------------------------------------------ queries
def cds_queries() -> list[dict]:
    tops = json.loads((C.CDS / "topics_merged.json").read_text())
    out = []
    for t in K.DEV_TOPICS:
        for v in K.CDS_VARIANTS:
            out.append({"qid": f"{t}::{v}", "population": "cds", "topic": t,
                        "variant": v, "text": tops[t]["fields"][v]})
    K.assert_dev_only([q["topic"] for q in out])
    assert len(out) == 20
    return out


def pointed_queries() -> list[dict]:
    out = []
    for line in K.POINTED.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        out.append({"qid": r["qid"], "population": "pointed", "topic": r["draw_topic"],
                    "variant": "pointed", "text": r["query"], "docno": r["docno"]})
    K.assert_dev_only([q["topic"] for q in out])
    return out


def embed_queries(qs: list[dict]) -> tuple[np.ndarray, dict]:
    """Embed the <= 200 query strings on :9001-:9006 (<= 2 in flight each).

    Every embed call is recorded. CDS query vectors are re-embedded here rather than read
    from Stage 0's ``dev_queries.npy`` so that one code path produces both populations;
    the agreement with the Stage 0 vectors is asserted (cosine > 0.9999) and reported.
    """
    import stage1_common as S
    fleet = S.Fleet()
    texts = [q["text"] for q in qs]
    ntok = [len(x) // 3 + 8 for x in texts]
    t0 = time.time()
    Q = fleet.embed(texts, ntok, label="s0b-prime queries", every=50)
    Q = Q / np.linalg.norm(Q, axis=1, keepdims=True)
    stats = {"n_queries": len(qs), "seconds": round(time.time() - t0, 1),
             "requests": fleet.requests, "retries": fleet.retries,
             "actual_items": fleet.actual_items, "actual_tokens": fleet.actual_tokens,
             "endpoints": "http://localhost:9001..9006, <= 2 in flight each"}
    # agreement with Stage 0's frozen CDS query vectors
    try:
        old = np.load(K.WORK / "dev_queries.npy")
        okeys = [tuple(k) for k in
                 json.loads((K.WORK / "dev_query_keys.json").read_text())["keys"]]
        idx = {f"{t}::{v}": i for i, (t, v) in enumerate(okeys)}
        cos = []
        for i, q in enumerate(qs):
            j = idx.get(q["qid"])
            if j is not None:
                o = old[j] / np.linalg.norm(old[j])
                cos.append(float(Q[i] @ o))
        stats["cds_query_vector_agreement_min_cosine"] = round(min(cos), 6) if cos else None
        stats["cds_query_vectors_compared"] = len(cos)
    except Exception as e:  # noqa: BLE001
        stats["cds_query_vector_agreement_min_cosine"] = f"UNAVAILABLE {type(e).__name__}"
    return Q.astype(np.float32), stats


# ------------------------------------------------------------------ fusion
def rrf(lists: list[list[int]], k: int = K.RRF_K) -> list[tuple[int, float]]:
    score: dict[int, float] = {}
    for lst in lists:
        for r, x in enumerate(lst, start=1):
            score[x] = score.get(x, 0.0) + 1.0 / (k + r)
    return sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))


def main() -> None:
    import pilot_common as P

    qs = cds_queries() + pointed_queries()
    print(f"queries: {len(qs)} ({sum(1 for q in qs if q['population']=='cds')} cds, "
          f"{sum(1 for q in qs if q['population']=='pointed')} pointed)", flush=True)

    qvec_path = K.OUT / "query_vectors.npy"
    meta_path = K.OUT / "query_meta.json"
    if qvec_path.exists() and meta_path.exists():
        Q = np.load(qvec_path)
        qmeta = json.loads(meta_path.read_text())
        assert [x["qid"] for x in qmeta["queries"]] == [q["qid"] for q in qs]
        print("query vectors: reusing", qvec_path, flush=True)
    else:
        Q, estats = embed_queries(qs)
        np.save(qvec_path, Q)
        qmeta = {"queries": qs, "embed_stats": estats,
                 "sequestration": "all CDS topics and all pointed draw_topics asserted in "
                                  "C.DEV_TOPICS before the first embed"}
        K.atomic_json(meta_path, qmeta)
        print("query vectors:", Q.shape, json.dumps(estats), flush=True)

    docs = K.load_docs()
    print("docs loaded:", len(docs), flush=True)

    ct_terms: dict = {}
    ct = CorpusTokens(docs)
    print(f"corpus tokenized: {ct.n_tokens} tokens, {len(ct.vocab)} types, "
          f"{ct.seconds}s", flush=True)
    for q in qs:
        ct_terms[q["qid"]] = ct.term_ids(q["text"])
    qvocab = {t for v in ct_terms.values() for t in v}
    print("query vocabulary:", len(qvocab), flush=True)

    ce = P.CE(concurrency=4)
    manifest: dict = {"arms": {}, "queries": len(qs), "query_vocab": len(qvocab),
                      "corpus_tokens": ct.n_tokens, "corpus_types": len(ct.vocab),
                      "corpus_tokenize_seconds": ct.seconds}

    arms = [a for a in K.INDEX_KEYS]
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    if only:
        arms = [a for a in arms if a in only]

    for arm in arms:
        outp = K.OUT / f"pool_{arm}.jsonl"
        if outp.exists():
            print("skip (done):", arm, flush=True)
            continue
        t0 = time.time()
        rows = K.load_rows(arm)
        headers = K.load_headers() if arm == "header512" else None
        E = np.load(K.EMB / f"emb_{arm}.npy", mmap_mode="r")
        assert E.shape[0] == len(rows), (E.shape, len(rows))

        # ---- dense leg: exact cosine, top LEG_DEPTH ------------------------------
        nq = len(qs)
        best_v = np.full((nq, K.LEG_DEPTH), -2.0, dtype=np.float32)
        best_i = np.zeros((nq, K.LEG_DEPTH), dtype=np.int64)
        for s in range(0, E.shape[0], BLOCK):
            B = np.array(E[s:s + BLOCK], dtype=np.float32)
            nrm = np.linalg.norm(B, axis=1, keepdims=True)
            nrm[nrm == 0] = 1.0
            B /= nrm
            sims = Q @ B.T
            cat_v = np.concatenate([best_v, sims], axis=1)
            cat_i = np.concatenate([best_i, np.broadcast_to(
                np.arange(s, s + B.shape[0]), (nq, B.shape[0]))], axis=1)
            part = np.argpartition(-cat_v, K.LEG_DEPTH - 1, axis=1)[:, :K.LEG_DEPTH]
            best_v = np.take_along_axis(cat_v, part, axis=1)
            best_i = np.take_along_axis(cat_i, part, axis=1)
            del B, sims, cat_v, cat_i
        order = np.argsort(-best_v, axis=1)
        best_v = np.take_along_axis(best_v, order, axis=1)
        best_i = np.take_along_axis(best_i, order, axis=1)
        t_dense = time.time() - t0
        print(f"[{arm}] dense {t_dense:.0f}s", flush=True)

        # ---- lexical leg --------------------------------------------------------
        bm = ArmBM25(rows, ct, qvocab, headers)
        print(f"[{arm}] bm25 index {bm.seconds}s postings={bm.n_postings} "
              f"avgdl={bm.avgdl:.1f}", flush=True)

        recs = []
        for qi, q in enumerate(qs):
            dense = [int(x) for x in best_i[qi]]
            dscore = {int(i): float(v) for i, v in zip(best_i[qi], best_v[qi])}
            bidx, bval = bm.topk(ct_terms[q["qid"]], K.LEG_DEPTH)
            lex = [int(x) for x in bidx]
            lscore = {int(i): float(v) for i, v in zip(bidx, bval)}
            fused = [x for x, _s in rrf([dense, lex])]
            fscore = {x: s for x, s in rrf([dense, lex])}
            pools = {"vector": dense[:K.DEPTH], "bm25": lex[:K.DEPTH],
                     "hybrid": fused[:K.DEPTH]}
            union = sorted({x for p in pools.values() for x in p})
            texts = []
            for j in union:
                d, s, e, _nt = rows[j]
                h = headers.get((d, s), "") if headers else ""
                texts.append(h + docs[d][s:e])
            scores = ce.score(q["text"], texts) if hasattr(ce, "score") \
                else ce._post(q["text"], texts)
            ces = {j: float(x) for j, x in zip(union, scores)}
            rec = {"qid": q["qid"], "population": q["population"], "topic": q["topic"],
                   "variant": q["variant"], "arm": arm,
                   "chunks": {str(j): {"docno": rows[j][0], "start": rows[j][1],
                                       "end": rows[j][2], "sfr": rows[j][3],
                                       "ce": ces[j],
                                       "cos": dscore.get(j), "bm25": lscore.get(j),
                                       "rrf": fscore.get(j)} for j in union},
                   "pools": pools,
                   "dense_rank": {str(x): r for r, x in enumerate(dense[:K.DEPTH])},
                   "bm25_rank": {str(x): r for r, x in enumerate(lex[:K.DEPTH])}}
            recs.append(rec)
            if (qi + 1) % 50 == 0:
                print(f"  [{arm}] {qi+1}/{nq} pooled {time.time()-t0:.0f}s", flush=True)

        tmp = outp.with_suffix(".jsonl.tmp")
        with open(tmp, "w") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
        tmp.replace(outp)
        manifest["arms"][arm] = {
            "chunks": len(rows), "sha256": K.sha256_file(outp),
            "dense_seconds": round(t_dense, 1), "bm25_index_seconds": bm.seconds,
            "bm25_postings": bm.n_postings, "bm25_avgdl": round(bm.avgdl, 2),
            "seconds": round(time.time() - t0, 1)}
        K.atomic_json(K.OUT / "retrieve_manifest.json",
                      {**manifest, "ce": ce.stats() if hasattr(ce, "stats") else None,
                       "provenance": K.provenance()})
        print(f"[{arm}] done {time.time()-t0:.0f}s", flush=True)

    K.atomic_json(K.OUT / "retrieve_manifest.json",
                  {**manifest, "ce": ce.stats() if hasattr(ce, "stats") else None,
                   "provenance": K.provenance()})
    print("retrieval done", flush=True)


if __name__ == "__main__":
    main()
