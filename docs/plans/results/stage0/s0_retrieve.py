"""Stage 0b step 1 -- dev-topic retrieval against the FULL 32.7k corpus (SS7.1, P.4).

**Development topics only.** Nothing from the 80 confirmation topics is embedded, scored,
ranked or written here (SS2.3): the query list is ``C.DEV_TOPICS`` and the assertion is
made before the first query embed.

Per (arm, topic, variant):

1. dense leg -- exact brute-force cosine (numpy, fp32) of the query embedding against ALL
   chunk embeddings of the arm; take the top **D = 50** chunks (production
   ``depth = max(top_k, rerank_candidates=50)``);
2. **rerank the full pool** -- all 50 (query, chunk) pairs through ``:50052``
   (``bge-reranker-v2-m3``). Never one-chunk-per-document: defect 4's shortcut is retired.

No BM25/RRF (hybrid is Stage 2), no graph leg, ``max_per_doc = 0``, no boilerplate
demotion. Queries are embedded RAW -- the as-deployed convention; the SS10 instructed
variant is a sensitivity and is not needed by the gate.

Output ``pool_<arm>.json``: ``{variant: {topic: [[docno, start, end, ce_score], ...50]}}``
in RERANKED order, plus the dense order for the descriptive dense-only column.
"""
from __future__ import annotations

import json
import sys
import time

import numpy as np

import s0_common as C

sys.path.insert(0, str(C.STAGE1))
sys.path.insert(0, str(C.PILOTS))

BLOCK = 100_000


def dev_queries() -> dict[str, dict[str, str]]:
    tops = json.loads((C.CDS / "topics_merged.json").read_text())
    out = {}
    for t in C.DEV_TOPICS:
        v = tops[t]
        out[t] = {"summary": v["fields"]["summary"],
                  "description": v["fields"]["description"], "type": v["type"]}
    assert set(out) == set(C.DEV_TOPICS) and len(out) == 10
    return out


def main() -> None:
    import stage1_common as S
    import pilot_common as P

    qs = dev_queries()
    keys = [(t, v) for t in C.DEV_TOPICS for v in ("summary", "description")]
    # SEQUESTRATION ASSERTION (SS2.3, P.2): dev topics only.
    assert all(t in C.DEV_TOPICS for t, _ in keys), "confirmation topic in the query list"
    texts = [qs[t][v] for t, v in keys]

    fleet = S.Fleet()
    qtok = [len(x) // 3 + 8 for x in texts]
    Q = fleet.embed(texts, qtok, label="queries", every=1000)
    Q = Q / np.linalg.norm(Q, axis=1, keepdims=True)
    np.save(C.WORK / "dev_queries.npy", Q)
    C.atomic_json(C.WORK / "dev_query_keys.json",
                  {"keys": [list(k) for k in keys], "queries": qs,
                   "instruction_prefix": None, "convention": "raw, as-deployed"})
    print("queries embedded:", Q.shape, flush=True)

    ce = P.CE(concurrency=4)
    docs = {}
    for line in open(C.WORK / "docs.jsonl"):
        r = json.loads(line)
        docs[r["docno"]] = r["text"]

    def complete(a: str) -> bool:
        """An arm counts as available only when its embed pass FINISHED.

        ``open_memmap`` creates the full-size ``.npy`` before the first vector is
        written, so file existence proves nothing; ``estats_<arm>.json`` is written
        only after the last block, and the progress file must agree with the row count.
        """
        st = C.EMB / f"estats_{a}.json"
        pr = C.EMB / f"emb_{a}.progress.json"
        if not (st.exists() and pr.exists()):
            return False
        import json as _j
        return _j.loads(pr.read_text())["done"] == _j.loads(pr.read_text())["n"]

    arms = [a for a in C.INDEX_KEYS if complete(a)]
    print("arms available:", arms, flush=True)
    for arm in arms:
        out_path = C.WORK / f"pool_{arm}.json"
        if out_path.exists():
            print("skip (done):", arm, flush=True)
            continue
        t0 = time.time()
        rows = json.loads((C.EMB / f"rows_{arm}.json").read_text())
        hdrs = None
        if arm == "header512":
            hdrs = []
            for line in open(C.CHUNKS / f"spans_{arm}.jsonl"):
                r = json.loads(line)
                hdrs.extend(r["hdr"])
        E = np.load(C.EMB / f"emb_{arm}.npy", mmap_mode="r")
        assert E.shape[0] == len(rows), (E.shape, len(rows))
        best_v = np.full((len(keys), C.DEPTH), -2.0, dtype=np.float32)
        best_i = np.zeros((len(keys), C.DEPTH), dtype=np.int64)
        for s in range(0, E.shape[0], BLOCK):
            B = np.array(E[s:s + BLOCK], dtype=np.float32)  # copy: memmap is read-only
            nrm = np.linalg.norm(B, axis=1, keepdims=True)
            nrm[nrm == 0] = 1.0
            B /= nrm
            sims = Q @ B.T                                   # (nq, block)
            cat_v = np.concatenate([best_v, sims], axis=1)
            cat_i = np.concatenate([best_i, np.broadcast_to(
                np.arange(s, s + B.shape[0]), (len(keys), B.shape[0]))], axis=1)
            part = np.argpartition(-cat_v, C.DEPTH - 1, axis=1)[:, :C.DEPTH]
            best_v = np.take_along_axis(cat_v, part, axis=1)
            best_i = np.take_along_axis(cat_i, part, axis=1)
            del B, sims, cat_v, cat_i
        order = np.argsort(-best_v, axis=1)
        best_v = np.take_along_axis(best_v, order, axis=1)
        best_i = np.take_along_axis(best_i, order, axis=1)
        print(f"[{arm}] dense done {time.time()-t0:.0f}s", flush=True)

        pool = {"summary": {}, "description": {}}
        for qi, (t, v) in enumerate(keys):
            idx = best_i[qi].tolist()
            cand = [rows[j] for j in idx]                    # [docno, start, end, ntok]
            ctexts = [(hdrs[j] if hdrs else "") + docs[c[0]][c[1]:c[2]]
                      for j, c in zip(idx, cand)]
            scores = ce.score(qs[t][v], ctexts) if hasattr(ce, "score") else \
                ce._post(qs[t][v], ctexts)
            rr = sorted(range(len(cand)), key=lambda k: -scores[k])
            pool[v][t] = {
                "reranked": [[cand[k][0], cand[k][1], cand[k][2], float(scores[k]),
                              int(idx[k])] for k in rr],
                "dense": [[c[0], c[1], c[2], float(best_v[qi][k]), int(idx[k])]
                          for k, c in enumerate(cand)],
            }
        C.atomic_json(out_path, pool)
        print(f"[{arm}] pooled+reranked {time.time()-t0:.0f}s  ce={ce.stats() if hasattr(ce,'stats') else ''}",
              flush=True)


if __name__ == "__main__":
    main()
