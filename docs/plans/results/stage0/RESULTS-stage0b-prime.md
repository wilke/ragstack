# RESULTS — Stage 0b′, the revision-3 calibration on the existing indexes

**Specification:** [`../design/SPEC-confirmation-run-r3.md`](../design/SPEC-confirmation-run-r3.md)
§5 **step 4**, read against §3.1 (the split endpoints and the intersection estimand), §3.2
(B = 16,384 primary), §3.3 (Stage 0b′ counts both sides in SFR tokens), §3.4 (three modes at
the served shape, plus the dev-tenant BM25 concordance check), §3.5 (the delivery arms), §3.6
(the NI family and joint power) and **§11** (the pointed population and its guards).

**Scope note, stated first because it bounds everything below.** This run produces the
**calibration**, not a decision. No confirmation topic was retrieved, packed, scored or read;
no new embedding of a corpus chunk was computed; the human read (r3 §3.7 item 4) is still
`PENDING-HUMAN`, so every `EPACK` number here inherits the reliability of a machine gold whose
*validity* nobody has established. r3 §10 item 4 is still open, which is why all three of its
readings are computed and printed side by side rather than one of them being called the answer.

---

**Verdict: `THE GATE IS SPLIT. CDS PASSES ON CONTAINMENT AND FAILS ON REACH FOR THREE ARMS;
THE POINTED POPULATION FAILS GUARD 1's WINDOW AT THE CEILING AND IS THEREFORE A DESCRIPTIVE
POPULATION, NOT A FOURTH GATE.`**

<!-- HEADLINE -->

---

## 0. Provenance

| item | value |
|---|---|
| specification | `../design/SPEC-confirmation-run-r3.md` §5 step 4 |
| harness | `s0b_common.py`, `s0b_bm25.py`, `s0b_retrieve.py`, `s0b_pack.py`, `s0b_gold.py`, `s0b_score.py`, `s0b_checks.py`, `s0b_stats.py`, `s0b_contexts.py`, `s0b_es.py`, `s0b_report.py`, driven by `run_0bp.sh`. Stage 0b's `s0_*.py` are **read, not modified** — `s0_common` (paths, seeds, arm list, `Fleet`, `CE`), `s0_math` (the distribution functions), `s0_score` (the D3 interval algebra), `s0_label.segment` (the sentence identity the gold is expressed in) |
| repo commit | see `artifacts/stage0b-prime/checks.json` → `provenance.repo_head`; the Stage 0 pin `55a0fc2` is recorded beside it and the **segmenter diff against that pin is asserted EMPTY** before any gold is built, which is the only file whose drift could move a label |
| interpreter | `/rag/envs/ragstack/bin/python3`, `HF_HOME=/rag/cache`, `PYTHONPATH=…/ragstack/python`, `STAGE0_HELPERS=…/phase0-rescue/phase0` |
| **new embeddings of corpus chunks** | **0** — r3 §3.3's Stage 0b′ path. The 34 GB under `/rag/tmp/stage0-conf/emb/` is read, never written |
| query embeddings | **197** (20 CDS + 177 pointed), 13 requests on `:9001`–`:9006`, ≤ 2 in flight each, 0 retries, 8,514 tokens, **0.4 s**. Every call is recorded in `query_meta.json` |
| **CDS query vectors reproduce Stage 0's** | minimum cosine **1.000000** over all 20, against the frozen `dev_queries.npy` — the queries are the same queries |
| reranker | `:50052` → `BAAI/bge-reranker-v2-m3` (probed live), ≤ 4 in flight, **89,668 pairs in 270 s** (332 pairs/s) over 4,134 requests, 0 retries. The three modes of one (arm, query) are reranked **once over their union**, which is what keeps the pair count under 90k rather than at 177k |
| **stores** | one, once: the **dev tenant's** Elasticsearch at `http://localhost:24043` (read from `/rag/data/tenants/dev/config/tenant.env`, asserted to end in `:24043`, with `:9200`/`:6333`/`:24041` refused by an explicit check), for the §3.4 concordance check only. One index, `chkconf_20260907210115_tok512`, **deleted with a verifying listing**. No Qdrant client is constructed anywhere; `mango` is never contacted |
| GPUs 6 and 7 | untouched — no endpoint was started and no device selected; the only GPU work is on the shared `:9001`–`:9006` and `:50052` services |
| sequestration | asserted before the first embed: all 10 CDS topics and all 177 pointed `draw_topic`s ∈ `C.DEV_TOPICS`. The label loader repeats the assertion. **No confirmation topic's query or label was read** |
| seeds | bootstrap / joint power `20260913`; unit cap per document `20260918` |
| budgets | **SFR tokens on both sides** (r3 §3.3). 4,096 / **16,384** / 32,768 |
| **the generator's tokenizer** | **ABSENT.** `Llama-4-Scout` is not in `/rag/cache` and this run's harness rules forbid contacting `mango:8003/tokenize`. Generator-token columns are the declared estimate `gen = sfr / 1.2564` (r3 §3.3's own measured ratio 2,048/1,630) and are **descriptive only** — no budget, size or contrast depends on them. This is deviation D1 (§9) |

---

## 1. What was run

| step | what | cost |
|---|---|---|
| retrieval | 6 index arms × 3 modes × 197 queries; dense = exact cosine over the arm's whole embedding matrix, top 100 → 50; BM25 = in-process, top 100 → 50; hybrid = per-leg 100, RRF k = 60, top 50 | **7.5 min** wall for all six arms, of which 4.5 min is reranking |
| corpus tokenization for BM25 | one pass, shared by all six arms: 109,496,847 tokens, 621,829 types | **41 s** |
| BM25 indexes | postings restricted to the 1,296-term query vocabulary | **3–13 s per arm** |
| packing | 11 scoring arms × 3 modes × 2 rerank states × 3 budgets × 197 queries = **12,214 packed contexts** | **3.6 min**, 147,655 SFR tokenizer calls |
| gold | 30 pooled readings per CDS pair (Scout ×20 + Qwen ×10) → graded support; the k = 0 union → D3 units; the pointed construction spans | seconds |
| scoring | 36,642 (configuration, query) endpoint records | 1.6 s |
| concordance | 423,386 chunks into the dev tenant's Elasticsearch, 197 queries, index deleted | **112 s** |

---

## 2. The plumbing check

The brief's check is that the dense-mode 4k reach reproduces Stage 0 §5's `P(doc packed)`.
Stage 0 counted that budget in the **generator's** tokenizer and Stage 0b′ counts in the
**embedder's** (r3 §3.3), and the generator's tokenizer is not available to this run, so the
check is **decomposed into three parts** rather than fudged into one — a single "reproduces"
number would have been a tokenizer conversion in disguise.

* **A — retrieval identity.** This harness's `vector` pool against Stage 0's frozen
  `pool_<arm>.json`, same twenty CDS queries: pool overlap@50 is **1.000** for five arms and
  **0.999** for the shipping arm, **rank 1 is identical for every arm on every query**, and
  the whole 50-item reranked order is identical on 85–100 % of queries. The residual is
  reranker score ties across two service invocations, not retrieval.
* **B — scoring identity.** `P(doc packed)` recomputed by this harness **from Stage 0's own
  frozen packed unions**: max absolute difference **0.0000** against `floor_diagnostic.json`
  on all seven arms. The scoring path is exact.
* **C — end to end.** This harness's own dense pools, packed at **5,146 SFR tokens** — the
  budget r3 §3.3's measured ratio makes equivalent to Stage 0's 4,096 generator tokens.

<!-- PLUMBING -->

Six of seven arms are inside the ±0.01 target; `fixed_tok1024_ov0pct` is **0.0182** out,
which is **exactly two evidence units of 110** — the smallest deviation the estimator can
express above one unit. It is **under the 0.02 stop** and it is what a tokenizer-conversion
residual looks like: the conversion is a single scalar applied to a step function, so an arm
whose walk stops near a chunk boundary can gain or lose one chunk. **The run continued**, and
the residual is named here rather than smoothed away.

---

## 3. The gate tables

<!-- GATES -->

---

## 4. The contrasts

<!-- CONTRASTS -->

---

## 5. The mode × size table

<!-- MODEXSIZE -->

---

## 6. The pointed population and its guards

<!-- GUARDS -->

---

## 7. The BM25 concordance check

<!-- CONCORDANCE -->

---

## 8. Cost

<!-- COST -->

---

## 9. Deviations

<!-- DEVIATIONS -->

---

## 10. Reproduction

<!-- REPRO -->
