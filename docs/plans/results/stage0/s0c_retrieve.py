"""Confirmation run (a), step 1 — retrieval for the 80 confirmation topics. **QUARANTINED.**

This is `s0b_retrieve.py` (Stage 0b′, #520) with exactly two things changed: the query
population (the 80 confirmation topics × {summary, description} = 160 queries, instead of
the ten development topics and the pointed set) and the output directory (``work/conf/``,
under `QUARANTINE.md`). The pipeline is byte-for-byte Stage 0b′'s and is imported, not
copied: ``s0b_bm25.CorpusTokens`` / ``ArmBM25`` for the lexical leg, ``s0b_common``'s
constants for the served shape, ``pilot_common.CE`` for the reranker.

Per (arm, query), at the served shape (r3 §3.4):

* ``vector`` — exact brute-force cosine over the arm's existing embeddings, top **100**,
  truncated to the **50**-chunk pool;
* ``bm25``   — in-process BM25 over the same chunks, top **100**, truncated to 50;
* ``hybrid`` — each leg fetches ``top_k(50) × candidate_multiplier(2) = 100``, RRF with
  k = 60 over the two lists, truncated to 50.

The three pools of one (arm, query) are reranked **once** over their union on ``:50052``
(≤ 4 in flight), so a chunk in two modes costs one pair.

**Quarantine.** Nothing here prints, logs or returns a ranking, a similarity, a fusion
score or a rerank score. The progress lines carry counts and wall-clock only. The pools are
written to disk and are not read again by this task except by ``s0c_pack.py`` (which packs
them) and ``s0c_pool.py`` (which extracts document ids and reports counts).

Output: ``work/conf/pools/pool_<arm>.jsonl`` — one record per query — plus
``work/conf/pools/retrieve_manifest.json`` with a sha256 per file.
"""
from __future__ import annotations

import json
import sys
import time

import numpy as np

import s0c_common as Q           # noqa: I001  -- must precede s0_common (sys.path shim)
import s0b_common as K
import s0_common as C
from s0b_bm25 import ArmBM25, CorpusTokens
from s0b_retrieve import rrf

sys.path.insert(0, str(C.STAGE1))
sys.path.insert(0, str(C.PILOTS))

BLOCK = 200_000


def conf_queries() -> list[dict]:
    """80 confirmation topics × {summary, description}, in topic-id order."""
    tops = json.loads((C.CDS / "topics_merged.json").read_text())
    out = []
    for t in Q.conf_topics():
        for v in Q.VARIANTS:
            out.append({"qid": f"{t}::{v}", "population": "cds_conf", "topic": t,
                        "variant": v, "text": tops[t]["fields"][v]})
    Q.assert_conf_only([q["topic"] for q in out])
    Q.assert_no_dev([q["topic"] for q in out])
    assert len(out) == 160, len(out)
    return out


def embed_queries(qs: list[dict]) -> tuple[np.ndarray, dict]:
    """Embed the 160 query strings on :9001-:9006 (≤ 2 in flight each). Nothing else."""
    import stage1_common as S
    fleet = S.Fleet()
    texts = [q["text"] for q in qs]
    ntok = [len(x) // 3 + 8 for x in texts]
    t0 = time.time()
    E = fleet.embed(texts, ntok, label="s0c conf queries", every=40)
    E = E / np.linalg.norm(E, axis=1, keepdims=True)
    stats = {"n_queries": len(qs), "seconds": round(time.time() - t0, 1),
             "requests": fleet.requests, "retries": fleet.retries,
             "actual_items": fleet.actual_items, "actual_tokens": fleet.actual_tokens,
             "endpoints": "http://localhost:9001..9006, <= 2 in flight each"}
    return E.astype(np.float32), stats


def main() -> None:
    import pilot_common as P

    qs = conf_queries()
    print(f"queries: {len(qs)} (80 confirmation topics x "
          f"{len(Q.VARIANTS)} variants)", flush=True)

    qvec_path = Q.QUERIES / "query_vectors.npy"
    meta_path = Q.QUERIES / "query_meta.json"
    if qvec_path.exists() and meta_path.exists():
        E = np.load(qvec_path)
        qmeta = json.loads(meta_path.read_text())
        assert [x["qid"] for x in qmeta["queries"]] == [q["qid"] for q in qs]
        print("query vectors: reusing", qvec_path, flush=True)
    else:
        E, estats = embed_queries(qs)
        np.save(qvec_path, E)
        qmeta = {"queries": qs, "embed_stats": estats,
                 "sequestration": "every topic asserted to be one of the 80 confirmation "
                                  "topics, and none of the 10 development topics, before "
                                  "the first embed",
                 "quarantine": "query vectors are inputs, not outcomes"}
        Q.atomic_json(meta_path, qmeta)
        print("query vectors:", E.shape, json.dumps(estats), flush=True)

    docs = K.load_docs()
    print("docs loaded:", len(docs), flush=True)

    ct = CorpusTokens(docs)
    print(f"corpus tokenized: {ct.n_tokens} tokens, {len(ct.vocab)} types, "
          f"{ct.seconds}s", flush=True)
    ct_terms = {q["qid"]: ct.term_ids(q["text"]) for q in qs}
    qvocab = {t for v in ct_terms.values() for t in v}
    print("query vocabulary:", len(qvocab), flush=True)

    ce = P.CE(concurrency=4)
    manifest: dict = {"arms": {}, "queries": len(qs), "query_vocab": len(qvocab),
                      "corpus_tokens": ct.n_tokens, "corpus_types": len(ct.vocab),
                      "corpus_tokenize_seconds": ct.seconds}
    mpath = Q.POOLS / "retrieve_manifest.json"
    if mpath.exists():
        manifest = json.loads(mpath.read_text())
        manifest.pop("ce", None)
        manifest.pop("provenance", None)

    arms = list(K.INDEX_KEYS)
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    if only:
        arms = [a for a in arms if a in only]

    for arm in arms:
        outp = Q.POOLS / f"pool_{arm}.jsonl"
        if outp.exists():
            print("skip (done):", arm, flush=True)
            continue
        t0 = time.time()
        rows = K.load_rows(arm)
        headers = K.load_headers() if arm == "header512" else None
        M = np.load(K.EMB / f"emb_{arm}.npy", mmap_mode="r")
        assert M.shape[0] == len(rows), (M.shape, len(rows))

        nq = len(qs)
        best_v = np.full((nq, K.LEG_DEPTH), -2.0, dtype=np.float32)
        best_i = np.zeros((nq, K.LEG_DEPTH), dtype=np.int64)
        for s in range(0, M.shape[0], BLOCK):
            B = np.array(M[s:s + BLOCK], dtype=np.float32)
            nrm = np.linalg.norm(B, axis=1, keepdims=True)
            nrm[nrm == 0] = 1.0
            B /= nrm
            sims = E @ B.T
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
            fused = rrf([dense, lex])
            fscore = {x: s for x, s in fused}
            pools = {"vector": dense[:K.DEPTH], "bm25": lex[:K.DEPTH],
                     "hybrid": [x for x, _s in fused][:K.DEPTH]}
            union = sorted({x for p in pools.values() for x in p})
            texts = []
            for j in union:
                d, s, e, _nt = rows[j]
                h = headers.get((d, s), "") if headers else ""
                texts.append(h + docs[d][s:e])
            scores = ce.score(q["text"], texts)
            ces = {j: float(x) for j, x in zip(union, scores)}
            recs.append({
                "qid": q["qid"], "population": q["population"], "topic": q["topic"],
                "variant": q["variant"], "arm": arm,
                "chunks": {str(j): {"docno": rows[j][0], "start": rows[j][1],
                                    "end": rows[j][2], "sfr": rows[j][3], "ce": ces[j],
                                    "cos": dscore.get(j), "bm25": lscore.get(j),
                                    "rrf": fscore.get(j)} for j in union},
                "pools": pools,
                "dense_rank": {str(x): r for r, x in enumerate(dense[:K.DEPTH])},
                "bm25_rank": {str(x): r for r, x in enumerate(lex[:K.DEPTH])}})
            if (qi + 1) % 40 == 0:
                print(f"  [{arm}] {qi+1}/{nq} pooled {time.time()-t0:.0f}s", flush=True)

        tmp = outp.with_suffix(".jsonl.tmp")
        with open(tmp, "w") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
        tmp.replace(outp)
        manifest["arms"][arm] = {
            "chunks": len(rows), "sha256": Q.sha256_file(outp),
            "records": len(recs),
            "dense_seconds": round(t_dense, 1), "bm25_index_seconds": bm.seconds,
            "bm25_postings": bm.n_postings, "bm25_avgdl": round(bm.avgdl, 2),
            "seconds": round(time.time() - t0, 1)}
        Q.atomic_json(mpath, {**manifest, "ce": ce.stats(),
                              "quarantine": "pools written, never summarised",
                              "provenance": Q.provenance()})
        print(f"[{arm}] done {time.time()-t0:.0f}s", flush=True)

    Q.atomic_json(mpath, {**manifest, "ce": ce.stats(),
                          "quarantine": "pools written, never summarised",
                          "provenance": Q.provenance()})
    print("retrieval done", flush=True)


if __name__ == "__main__":
    main()
