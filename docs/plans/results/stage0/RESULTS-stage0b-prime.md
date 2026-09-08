# RESULTS — Stage 0b′, the revision-3 calibration on the existing indexes

**Specification:** [`../design/SPEC-confirmation-run-r3.md`](../design/SPEC-confirmation-run-r3.md)
§5 **step 4**, read against §3.1 (the split endpoints and the intersection estimand), §3.2
(B = 16,384 primary), §3.3 (Stage 0b′ counts both sides in SFR tokens), §3.4 (three modes at
the served shape, plus the dev-tenant BM25 concordance check), §3.5 (the delivery arms), §3.6
(the NI family and joint power) and **§11** (the pointed population and its guards).

**Scope note, stated first because it bounds everything below.** This run produces the
**calibration**, not a decision. No confirmation topic was retrieved, packed, scored or read;
no corpus chunk was re-embedded; the two-reader human read (r3 §3.7 item 4) is still
`PENDING-HUMAN`, so every `EPACK` number here inherits the *reliability* of a machine gold
whose **validity nobody has established**. r3 §10 item 4 is still open, which is why all three
of its readings are computed and printed side by side rather than one being called the answer.

---

**Verdict: `THE GATE FAILS ON POWER, NOT ON LEVELS. THE CONFIRMATION RUN'S 80 TOPICS BUY
37–56 % JOINT POWER ON FIVE OF SIX CONTRASTS; THE POINTED POPULATION SITS AT ITS CEILING AND
IS A DESCRIPTIVE POPULATION, NOT A FOURTH GATE.`**

* **The plumbing reproduces.** Retrieval is an identity against Stage 0's frozen pools (pool
  overlap@50 1.000 on five arms, 0.999 on the shipping arm; **rank 1 identical on every arm
  and every query**), the scoring path recomputes `floor_diagnostic.json`'s `P(doc packed)`
  from Stage 0's own unions to **0.0000**, and end to end six of seven arms are inside the
  ±0.01 target with the 1024 arm **0.0182** out — two evidence units of 110, under the 0.02
  stop. §2.
* **Splitting the endpoint worked. `EPACK` is inside [0.15, 0.90] for every arm** (0.188 →
  0.696 from 256 to 2048 tokens), so **prediction Q2 passes**. `ERET@16k` is inside for eight
  arms of eleven and **below the 0.15 floor for three** — `fixed_tok2048_ov0pct` (0.112),
  `nbr1_512` (0.101), `nbr2_512` (0.086). **Q3 fails**, and those three arms are **demoted to
  descriptive on `ERET`** by r3 §3.1's window rule. §3.
* **The size trade is now visible and it is close to one for one.** From 256 to 2048 tokens
  reach falls 0.277 → 0.112 while containment rises 0.188 → 0.696; the product sits at
  0.046–0.100 for every arm. Chunk size at 16k does not make the agent's evidence better or
  worse so much as **move it between the two factors**. §3.
* **The gate that fails is power.** σ_d on the confirmatory `EPACK` is **0.148–0.379** against
  r2 §8.5.7's 0.158 requirement — inside it for **R2 only**. The **joint** bootstrap power at
  the planned 80 topics is **0.369–0.560** for N1, N3, R1, R3 and R4, and 0.948 for R2. On the
  conservative σ-bound reading the confirmation run would need **286–453 topics**; on the
  bootstrap reading, 140–220. Either way **80 is not enough**, and this is exactly the failure
  mode r3 §3.6 predicted when it made the joint figure the gate rather than the marginals —
  the `ERET` marginal is 0.95–1.00 at n = 80 and carries none of the weight. §4.
* **The pointed population fails guard 1 at the ceiling.** Discrimination passes emphatically
  (top-10 document sets differ for **100 %** of queries at every mode), but `ERET` is
  **0.904–0.955** for every arm and confirmatory `EPACK` is **0.870–1.000**, so the window
  holds for **1 arm of 11**. This is r3 §11's pre-registered risk (i) — generated queries too
  easy to separate arms — measured rather than argued. Per guard 1 the pointed set is
  **reported as a descriptive population** and the CDS population alone carries the decision
  under §1.1's limitation. §6.
* **The BM25 concordance check passes**: mean overlap@50 **0.9469** against the 0.90 bar over
  197 queries, Spearman **0.966** on the intersection, rank-1 agreement **0.959**, on the dev
  tenant's Elasticsearch, one index, deleted with a verifying listing. §7.
* **The three readings of "where" agree.** Support-weighted, core-at-0.5 and unit-based
  `EPACK` have the **same sign on all six contrasts**, and the confirmatory and reached-set
  estimands agree in sign on all six as well: **no contrast is `UNRESOLVED-BY-ESTIMAND`**. The
  open §10 item 4 decision does not change any sign here. §4.

---

## 0. Provenance

| item | value |
|---|---|
| specification | `../design/SPEC-confirmation-run-r3.md` §5 step 4 |
| harness | `s0b_common.py`, `s0b_bm25.py`, `s0b_retrieve.py`, `s0b_pack.py`, `s0b_gold.py`, `s0b_score.py`, `s0b_checks.py`, `s0b_stats.py`, `s0b_contexts.py`, `s0b_es.py`, `s0b_report.py`, `s0b_writeup.py`, driven by `run_0bp.sh`. Stage 0b's `s0_*.py` are **read, not modified** — `s0_common` (paths, seeds, arm list, `Fleet`, `CE`), `s0_math` (the distribution functions), `s0_score` (the D3 interval algebra), `s0_label.segment` (the sentence identity the gold is expressed in) |
| repo commit | recorded in every artifact's `provenance` block. The Stage 0 pin `55a0fc2` is recorded beside the current HEAD and the **segmenter diff against that pin is asserted EMPTY** before any gold is built — that is the one file whose drift could move a label |
| interpreter | `/rag/envs/ragstack/bin/python3`, `HF_HOME=/rag/cache`, `PYTHONPATH=…/ragstack/python`, `STAGE0_HELPERS=…/phase0-rescue/phase0` |
| **new embeddings of corpus chunks** | **0** — r3 §3.3's Stage 0b′ path. The 34 GB under `/rag/tmp/stage0-conf/emb/` is read, never written |
| query embeddings | **197** (20 CDS + 177 pointed), 13 requests on `:9001`–`:9006`, ≤ 2 in flight each, 0 retries, 8,514 tokens, **0.4 s**. Every call recorded in `query_meta.json` |
| **CDS query vectors reproduce Stage 0's** | minimum cosine **1.000000** over all 20 against the frozen `dev_queries.npy` — the queries are the same queries |
| reranker | `:50052` → `BAAI/bge-reranker-v2-m3`, ≤ 4 in flight, **89,668 pairs in 270 s** (332 pairs/s), 4,134 requests, 0 retries. The three modes of one (arm, query) are reranked **once over their union**, which is what keeps the pair count under 90k instead of at 177k |
| **stores** | one, once: the **dev tenant's** Elasticsearch at `http://localhost:24043`, read from `/rag/data/tenants/dev/config/tenant.env` and asserted to end in `:24043`, with `:9200` / `:6333` / `:24041` refused by an explicit check — for the §3.4 concordance check only. One index, `chkconf_20260907210115_tok512`, **deleted with a verifying listing** (`chkconf_indices_remaining: []`). No Qdrant client is constructed anywhere; `mango` is never contacted |
| GPUs 6 and 7 | untouched — no endpoint started, no device selected. The only GPU work is on the shared `:9001`–`:9006` and `:50052` services |
| sequestration | asserted before the first embed: all 10 CDS topics and all 177 pointed `draw_topic`s ∈ `C.DEV_TOPICS`; the label loader repeats it. **No confirmation topic's query or label was read** |
| seeds | bootstrap / joint power `20260913`; unit cap per document `20260918` |
| budgets | **SFR tokens on both sides** (r3 §3.3): 4,096 / **16,384** / 32,768 |
| **the generator's tokenizer** | **ABSENT.** `Llama-4-Scout` is not in `/rag/cache` and this run's harness rules forbid contacting `mango:8003/tokenize`. Generator-token columns are the declared estimate `gen = sfr / 1.2564` (r3 §3.3's own measured ratio 2,048/1,630) and are **descriptive only**. Deviation D1, §9 |
| artifacts | `artifacts/stage0b-prime/` — `checks.json`, `stats.json`, `es_concordance.json`, `TABLES.md`, `contexts-INDEX.json`, `pack_meta.json`, `score_meta.json`, `retrieve_manifest.json`, `gold-summary.json`. Frozen pools and packed contexts live under `/rag/tmp/stage0-conf/work/stage0b-prime/` with sha256s in the manifests (45 MB of contexts; too large to commit, indexed instead) |

---

## 1. What was run

| step | what | cost |
|---|---|---|
| retrieval | 6 index arms × 3 modes × 197 queries. `vector` = exact cosine over the arm's whole embedding matrix, top 100 → 50; `bm25` = in-process, top 100 → 50; `hybrid` = per-leg 100, RRF k = 60, top 50 | **7.5 min** wall for all six arms, of which 4.5 min is reranking |
| corpus tokenization for BM25 | one pass, shared by all six arms: 109,496,847 tokens, 621,829 types | **41 s** |
| BM25 indexes | postings restricted to the 1,296-term query vocabulary — a term in no query can change no ranking | **3.3–12.5 s per arm** |
| packing | 11 scoring arms × 3 modes × 2 rerank states × 3 budgets × 197 queries = **12,214 packed contexts** | **3.6 min**, 147,655 SFR tokenizer calls |
| gold | 30 pooled readings per CDS pair (Scout ×20 + Qwen ×10) → graded support; the k = 0 union → D3 units; the pointed construction spans | seconds |
| scoring | **36,642** (configuration, query) endpoint records | 1.6 s |
| statistics | levels, σ_d, 10,000-draw joint power, mode × size, guards | 41 s |
| concordance | 423,386 chunks into the dev tenant's Elasticsearch, 197 queries, index deleted | **112 s** |

Chunk counts per arm, and the BM25 index each one produced:

| arm | chunks | BM25 postings | avg chunk length (tokens) | dense (s) | BM25 index (s) |
|---|---|---|---|---|---|
| `fixed_tok256_ov0pct` | 737,698 | 29,947,103 | 148.4 | 6.9 | 12.5 |
| `fixed_tok512_ov0pct` | 376,516 | 23,546,491 | 290.8 | 4.1 | 7.7 |
| `fixed_tok512` (shipping) | 423,386 | 26,729,653 | 293.7 | 4.4 | 8.6 |
| `header512` | 376,516 | 24,291,926 | 306.6 | 4.3 | 10.8 |
| `fixed_tok1024_ov0pct` | 196,247 | 18,112,199 | 558.0 | 2.2 | 4.9 |
| `fixed_tok2048_ov0pct` | 106,353 | 13,662,957 | 1,029.6 | 1.0 | 3.3 |

**The gold.** 308 development pairs, 208 pooled and 100 from the §7.5 bias-bound sample, over
the ten development topics. Pooled per-sentence support comes from **30 readings** — Scout's
twenty and Qwen's ten from `artifacts/r31ext/` — with the **one flagged locator blow-up**
(#513: Scout, `2016_1`/`4212306`, presentation 11; 22 model spans, 986 emitted) dropped by
#512's own `vstats` criterion, so that pair's denominator is 29. Evidence-bearing documents
per topic: **7 to 36, mean 20.8**. Three documents exceed 120,000 characters and are
**flagged, not excluded** (they are excluded on the pointed set already, #513); the
`ERET`-without-them sensitivity moves every arm by ≤ 0.008 and never changes a window verdict.

Shape of the gold, from `artifacts/stage0b-prime/gold-summary.json`: mean support mass
**10.16** per document, **5.15** sentences at support ≥ 0.5, **2.52** D3 units. **25 of 308
documents have no sentence at support ≥ 0.5** — 13 pooled, 12 from the bias-bound sample — and
contribute nothing to reading (b); **5 have no k = 0 unit**, all five from the bias-bound
sample, so reading (c) loses none of the pooled set. `ERET`'s denominator is unaffected either
way: it is defined on support > 0, which every pooled document has.

---

## 2. The plumbing check

The brief's check is that the dense-mode 4k reach reproduces Stage 0 §5's `P(doc packed)`.
Stage 0 counted that budget in the **generator's** tokenizer and Stage 0b′ counts in the
**embedder's** (r3 §3.3), and the generator's tokenizer is not available to this run — so the
check is **decomposed into three parts** rather than fudged into one. A single "reproduces"
number here would have been a tokenizer conversion in disguise.

* **A — retrieval identity.** This harness's `vector` pool against Stage 0's frozen
  `pool_<arm>.json`, same twenty CDS queries: pool overlap@50 **1.000** for five arms and
  **0.999** for the shipping arm, **rank 1 identical for every arm on every query**, whole
  50-item reranked order identical on 85–100 % of queries. The residual is reranker score
  ties across two service invocations, not retrieval.
* **B — scoring identity.** `P(doc packed)` recomputed by this harness **from Stage 0's own
  frozen packed unions**: max absolute difference **0.0000** on all seven arms.
* **C — end to end.** This harness's own dense pools, packed at **5,146 SFR tokens** — the
  budget r3 §3.3's measured ratio (2,048/1,630) makes equivalent to Stage 0's 4,096 generator
  tokens.

<!-- TABLE: Plumbing reproduction -->

**Plumbing reproduction**

| arm | Stage 0 `P(doc packed)` @4,096 gen | this harness @5,146 SFR | |Δ| | Stage 0 unions re-scored here |
|---|---|---|---|---|
| `fixed_tok256_ov0pct` | 0.2091 | 0.2000 | 0.0091 | 0.2091 (Δ 0.0000) |
| `fixed_tok512_ov0pct` | 0.0909 | 0.1000 | 0.0091 | 0.0909 (Δ 0.0000) |
| `fixed_tok1024_ov0pct` | 0.0818 | 0.1000 | 0.0182 | 0.0818 (Δ 0.0000) |
| `fixed_tok2048_ov0pct` | 0.0455 | 0.0455 | 0.0000 | 0.0455 (Δ 0.0000) |
| `fixed_tok512` | 0.1000 | 0.1091 | 0.0091 | 0.1000 (Δ 0.0000) |
| `header512` | 0.1455 | 0.1455 | 0.0000 | 0.1455 (Δ 0.0000) |
| `parent256` | 0.0636 | 0.0727 | 0.0091 | 0.0636 (Δ 0.0000) |

<!-- /TABLE -->

Six arms of seven are inside the ±0.01 target; `fixed_tok1024_ov0pct` is **0.0182** out, which
is **exactly two evidence units of 110** — the smallest deviation this estimator can express
above one unit. It is **under the 0.02 stop**, and it is what a tokenizer-conversion residual
looks like: the conversion is one scalar applied to a step function, so an arm whose walk stops
near a chunk boundary gains or loses a whole chunk. **The run continued.** The residual is
named here rather than smoothed away.

**Manipulation checks (r3 §3.1).** The **GOLD packing control** passes on all three readings —
pack each evidence document's own gold text and `ERET` = **1.000**, `EPACK` (a) = (b) = (c) =
**1.000** over 208 documents, against the ≥ 0.95 bar. The **NEGATIVE control** is **0 by
construction and vacuous twice over**: the labeling set contains **no grade-0 pairs at all**
(every pooled and sample document is grade ≥ 1), so there is nothing for it to score. It is
reported as a plumbing check, not a finding, exactly as r3 §3.1 says. **Discrimination**
passes at 1.000 on both populations and all three modes (§6). The **budget-bind** check is in
`checks.json`: at 16,384 SFR the fine arms exhaust the D = 50 pool before the budget binds
(the 256 arm realises 12,747 of 16,384 tokens with 43.3 documents packed), which is the
asymmetry r3 §3.2 predicted and called "the real trade-off", not a confound.

---

## 3. The gate tables

<!-- TABLE: CDS development topics -->

**Read the first two columns together.** From 256 to 2048 tokens `ERET` falls **0.277 →
0.112** and `EPACK` rises **0.188 → 0.696**; the product never leaves 0.046–0.100. That is the
picture r3 §3.1 split the endpoint to see, and it is the first time the study has seen it:
**chunk size at an agent-sized budget moves evidence between reach and containment rather than
creating or destroying it.**

* **Q2 passes.** Confirmatory `EPACK` is inside [0.15, 0.90] for **every** arm, on all three
  readings, with the nearest margin 0.038 (the 256 arm at 0.188 on reading (a)).
* **Q3 fails.** `ERET@16k` is below the 0.15 floor for `fixed_tok2048_ov0pct` (**0.112**),
  `nbr1_512` (**0.101**) and `nbr2_512` (**0.086**). Those three are **demoted to descriptive
  for `ERET`'s contrasts** under r3 §3.1's window rule. The prediction's own basis was that
  the 2048 arm's *unit-weighted* reach at 16k was ≥ 0.164 by arithmetic from
  `floor_diagnostic.json`; the *document-weighted* `ERET` this revision actually defines is
  **0.112**, and the gap between the two is the arithmetic's, not the measurement's.
* The two **descriptive** arms are worth a line each. `multi256+1024` has the **highest reach
  of any arm** (0.320) and a mid-range containment (0.327), giving it the best product (0.100)
  — the only arm that buys reach without paying containment, because it fuses two rankings
  rather than shrinking chunks. `nbr1_256` matches `parent256`'s reach at a higher containment
  (0.465 vs 0.405).

<!-- TABLE: Pointed development set -->

**Every arm is at the ceiling.** `ERET` 0.904–0.955 and `EPACK | reach` 0.870–1.000 put ten of
eleven arms **outside** the [0.15, 0.90] window on at least one endpoint, and `nbr2_512`
delivers the gold span in **every** query it reaches. This is the population behaving exactly
as r3 §11's objection (i) said it might. §6 reads the guard.

<!-- TABLE: Budget curve — CDS -->

**Budget curve — CDS, `hybrid` + rerank**

| arm | ERET @4k | ERET @16k | ERET @32k | EPACK @4k | EPACK @16k | EPACK @32k |
|---|---|---|---|---|---|---|
| `fixed_tok256_ov0pct` | 0.139 | 0.277 | 0.277 | 0.193 | 0.188 | 0.188 |
| `fixed_tok512_ov0pct` | 0.062 | 0.217 | 0.267 | 0.469 | 0.395 | 0.386 |
| `fixed_tok512` | 0.084 | 0.217 | 0.260 | 0.355 | 0.371 | 0.385 |
| `header512` | 0.083 | 0.258 | 0.329 | 0.394 | 0.403 | 0.389 |
| `fixed_tok1024_ov0pct` | 0.061 | 0.166 | 0.296 | 0.490 | 0.527 | 0.490 |
| `fixed_tok2048_ov0pct` | 0.026 | 0.112 | 0.176 | 0.666 | 0.696 | 0.671 |
| `parent256` | 0.058 | 0.186 | 0.277 | 0.410 | 0.405 | 0.403 |
| `nbr1_512` | 0.037 | 0.101 | 0.194 | 0.577 | 0.604 | 0.588 |
| `nbr1_256` | 0.055 | 0.186 | 0.277 | 0.382 | 0.465 | 0.473 |
| `nbr2_512` | 0.019 | 0.086 | 0.162 | 0.760 | 0.662 | 0.658 |
| `multi256+1024` | 0.095 | 0.320 | 0.485 | 0.336 | 0.327 | 0.371 |

<!-- /TABLE -->

<!-- TABLE: Budget curve — pointed -->

**Budget curve — pointed, `hybrid` + rerank**

| arm | ERET @4k | ERET @16k | ERET @32k | EPACK @4k | EPACK @16k | EPACK @32k |
|---|---|---|---|---|---|---|
| `fixed_tok256_ov0pct` | 0.932 | 0.955 | 0.955 | 0.806 | 0.870 | 0.870 |
| `fixed_tok512_ov0pct` | 0.915 | 0.955 | 0.955 | 0.864 | 0.923 | 0.941 |
| `fixed_tok512` | 0.904 | 0.938 | 0.938 | 0.925 | 0.952 | 0.958 |
| `header512` | 0.938 | 0.955 | 0.955 | 0.861 | 0.935 | 0.947 |
| `fixed_tok1024_ov0pct` | 0.898 | 0.938 | 0.960 | 0.912 | 0.982 | 0.976 |
| `fixed_tok2048_ov0pct` | 0.842 | 0.944 | 0.955 | 0.919 | 0.988 | 0.994 |
| `parent256` | 0.842 | 0.944 | 0.955 | 0.859 | 0.928 | 0.947 |
| `nbr1_512` | 0.825 | 0.915 | 0.938 | 0.932 | 0.994 | 0.988 |
| `nbr1_256` | 0.847 | 0.949 | 0.955 | 0.933 | 0.958 | 0.964 |
| `nbr2_512` | 0.763 | 0.904 | 0.921 | 0.933 | 1 | 1 |
| `multi256+1024` | 0.881 | 0.910 | 0.910 | 0.923 | 0.975 | 0.981 |

<!-- /TABLE -->

The curves say the 16k choice is not the top of the reach curve for the coarse arms: at 32,768
SFR tokens `fixed_tok1024_ov0pct` reaches **0.296** while keeping `EPACK` at 0.490 (product
0.145, the best number anywhere in this run), and the 2048 arm reaches 0.176. **The coarse
arms are budget-limited at 16k and the fine arms are pool-limited** — the 256 arm's reach is
identical at 16k and 32k because its whole D = 50 pool already fits.

---

## 4. The contrasts

<!-- TABLE: Contrasts — cds -->

**Contrasts — cds, `hybrid` + rerank, B = 16,384 SFR**

| id | control − candidate | d ERET [95 % CI] | σ_d(ERET) bound₈₀ | NI ERET | d EPACK∩ [95 % CI] | σ_d(EPACK) bound₈₀ | NI EPACK | n_ret ERET / EPACK | dropped |
|---|---|---|---|---|---|---|---|---|---|
| **N1** | `fixed_tok512` − `fixed_tok1024_ov0pct` | +0.051 [-0.024, +0.112] | 0.152 | no | -0.024 [-0.175, +0.115] | 0.301 | no | 10 / 8 | 2 |
| **N3** | `fixed_tok512` − `fixed_tok2048_ov0pct` | +0.106 [+0.038, +0.174] | 0.152 | no | -0.120 [-0.313, +0.052] | 0.379 | no | 10 / 8 | 2 |
| **R1** | `fixed_tok256_ov0pct` − `fixed_tok2048_ov0pct` | +0.165 [+0.090, +0.238] | 0.167 | no | -0.468 [-0.616, -0.317] | 0.312 | yes | 10 / 8 | 2 |
| **R2** | `header512` − `fixed_tok512_ov0pct` | +0.042 [-0.017, +0.117] | 0.149 | no | +0.027 [-0.039, +0.096] | 0.148 | no | 10 / 9 | 1 |
| **R3** | `parent256` − `fixed_tok512` | -0.031 [-0.081, +0.021] | 0.112 | yes | -0.112 [-0.283, +0.017] | 0.324 | no | 10 / 8 | 2 |
| **R4** | `nbr1_512` − `fixed_tok512` | -0.117 [-0.199, -0.054] | 0.162 | yes | +0.065 [-0.090, +0.231] | 0.328 | no | 10 / 7 | 3 |

<!-- /TABLE -->

<!-- TABLE: The three EPACK readings -->

**The three EPACK readings, side by side (CDS, confirmatory intersection)**

| id | (a) support-weighted | (b) core ≥ 0.5 | (c) unit-based |
|---|---|---|---|
| **N1** | -0.024 [-0.175, +0.115] | -0.002 [-0.177, +0.186] | -0.089 [-0.268, +0.053] |
| **N3** | -0.120 [-0.313, +0.052] | -0.113 [-0.321, +0.105] | -0.189 [-0.433, +0.035] |
| **R1** | -0.468 [-0.616, -0.317] | -0.478 [-0.606, -0.339] | -0.547 [-0.771, -0.333] |
| **R2** | +0.027 [-0.039, +0.096] | +0.017 [-0.122, +0.170] | +0.012 [-0.074, +0.111] |
| **R3** | -0.112 [-0.283, +0.017] | -0.087 [-0.248, +0.065] | -0.141 [-0.320, +0.016] |
| **R4** | +0.065 [-0.090, +0.231] | +0.011 [-0.192, +0.184] | +0.100 [-0.104, +0.327] |

<!-- /TABLE -->

<!-- TABLE: Estimand agreement -->

**What the six contrasts say, descriptively — none of them is a decision, because §5's power
gate fails.**

* **N3 (2048).** `d ERET` = **+0.106 [+0.038, +0.174]**, whose whole interval is above
  ε = 0.05: the coarse arm is **inferior on reach**, not merely non-inferior-unproven.
  Containment goes the other way (−0.120), which is the trade in one row. **Q5's direction is
  borne out**; its own basis expected a contest and got a clear loss. Read as descriptive: the
  candidate's `ERET` is outside the window.
* **N1 (1024).** `d ERET` = +0.051 straddles ε and `d EPACK∩` = −0.024 [−0.175, +0.115] is
  centred just the right side of zero. **This is the genuine contest Q4 predicted** and the
  reason the power gate matters: at n = 80 the study would resolve it with 56 % probability.
* **R1 (256 vs 2048).** The replication reproduces Stage 0's sign at four times the budget:
  the fine arm wins reach by **+0.165** and loses containment by **−0.468**.
* **R2 (`header512`).** +0.042 reach and +0.027 containment, both small, both with the
  tightest σ_d in the run. It is the **only contrast that clears the power gate**, and it
  clears it because contextual headers change little and change it consistently.
* **R3 (`parent256`).** `d EPACK∩` = **−0.112**: section expansion *loses* containment against
  the shipping arm at 16k. Stage 0 measured the opposite at 4k (0.43 vs 0.18) — and the reason
  is in the gate table: at 16k the shipping arm already packs 25.7 documents, so the section
  a chunk sits in is mostly already there, while the parent's cost pushes other documents out.
  **Expanding to the section is a 4k move.**
* **R4 (`nbr1_512`).** Containment **+0.065**, above Q6's 0.05 bar at the point estimate and
  not at the interval; reach **−0.117 [−0.199, −0.054]**, far outside ε. Under r3 §3.5's
  conjunctive rule this is *exactly* "a win on containment bought by pushing documents out of
  the budget", and it is reported as that. **Q6 partially supported, Q7 fails.**

**The estimand held.** Confirmatory (intersection) and reached-set `EPACK` agree in sign on all
six contrasts, and so does the mandatory `EPACK := 0` imputation, so **no contrast is
`UNRESOLVED-BY-ESTIMAND`**. The magnitudes differ in the direction r3 §3.1 predicted: the
reached-set difference is larger than the intersection difference wherever the fine arm reaches
many more documents (N1 −0.156 vs −0.024; N3 −0.325 vs −0.120), which is the selection effect
the intersection was introduced to remove. **The three readings of "where" agree in sign on all
six contrasts**, so the open §10 item 4 decision does not move a single conclusion here.

**Dropped topics.** The drop-from-both rule costs 1–3 of 10 topics per contrast
(`n_retained` 7–9 on `EPACK`, 10 on `ERET`). Every drop is a topic where one arm reached no
evidence-bearing document that the other also reached — a reach failure, charged to `ERET`
where r3 §3.1 says it belongs.

### The power gate — this is where Stage 0b′ stops

<!-- TABLE: Joint bootstrap power -->

**Joint bootstrap power (both endpoints resampled together, 10,000 draws, seed 20260913)**

| id | population | n | Δ = 0 | Δ = 0.01 | Δ = 0.02 | marginals at Δ = 0 (ERET / EPACK) |
|---|---|---|---|---|---|---|
| **N1** | CDS | 80 | 0.560 | 0.374 | 0.186 | 0.987 / 0.562 |
| **N3** | CDS | 80 | 0.369 | 0.214 | 0.082 | 0.990 / 0.376 |
| **R1** | CDS | 80 | 0.513 | 0.304 | 0.127 | 0.970 / 0.534 |
| **R2** | CDS | 80 | 0.948 | 0.814 | 0.557 | 0.954 / 0.987 |
| **R3** | CDS | 80 | 0.495 | 0.314 | 0.154 | 1.000 / 0.495 |
| **R4** | CDS | 80 | 0.529 | 0.371 | 0.238 | 1.000 / 0.529 |
| **N1** | pointed | 177 | 0.769 | 0.507 | 0.214 | 0.935 / 0.823 |
| **N3** | pointed | 177 | 0.754 | 0.472 | 0.208 | 0.913 / 0.826 |
| **R1** | pointed | 177 | 0.416 | 0.239 | 0.106 | 0.917 / 0.452 |
| **R2** | pointed | 177 | 0.891 | 0.738 | 0.517 | 0.999 / 0.893 |
| **R3** | pointed | 177 | 0.604 | 0.389 | 0.188 | 0.948 / 0.639 |
| **R4** | pointed | 177 | 0.919 | 0.794 | 0.620 | 1.000 / 0.919 |

<!-- /TABLE -->

<!-- TABLE: CDS sizing -->

**σ_d on the confirmatory `EPACK` is 0.148–0.379 against r2 §8.5.7's 0.158 requirement, and
only R2 is inside it.** The `ERET` side is comfortable (0.112–0.167, inside for four of six).
The **joint** power at the planned **80 topics** is therefore **0.369–0.560** for N1, N3, R1,
R3 and R4 — against the 80 % bar — while the `ERET` marginal alone reads 0.95–1.00. That gap
is the whole reason r3 §3.6 made the joint figure the gate: *"its power is not the
per-component power"*, and here the per-component power on one endpoint would have told the
study it was ready when it was not.

**Two sizing numbers are printed and they disagree, deliberately.** The analytic column uses
each contrast's **σ_d bound₈₀** (the larger of the χ² and bootstrap bounds, as P.7 requires)
in the exact non-central-t; the joint column resamples the observed differences, whose
dispersion is the point estimate rather than a conservative bound. **The conservative reading
governs**, so the planning number is **286–453 topics** for N1/N3/R1/R3/R4 and **71** for R2.
At ten development topics the σ_d estimate itself is imprecise, which is why the bound and not
the point estimate is quoted.

<!-- TABLE: Contrasts — pointed -->

**Contrasts — pointed, `hybrid` + rerank, B = 16,384 SFR**

| id | control − candidate | d ERET [95 % CI] | σ_d(ERET) bound₈₀ | NI ERET | d EPACK∩ [95 % CI] | σ_d(EPACK) bound₈₀ | NI EPACK | n_ret ERET / EPACK | dropped |
|---|---|---|---|---|---|---|---|---|---|
| **N1** | `fixed_tok512` − `fixed_tok1024_ov0pct` | +0.000 [-0.028, +0.028] | 0.213 | yes | -0.031 [-0.067, +0.000] | 0.260 | yes | 177 / 163 | 14 |
| **N3** | `fixed_tok512` − `fixed_tok2048_ov0pct` | -0.006 [-0.034, +0.023] | 0.226 | yes | -0.031 [-0.067, +0.006] | 0.261 | yes | 177 / 163 | 14 |
| **R1** | `fixed_tok256_ov0pct` − `fixed_tok2048_ov0pct` | +0.011 [-0.017, +0.040] | 0.213 | yes | -0.103 [-0.158, -0.055] | 0.367 | yes | 177 / 165 | 12 |
| **R2** | `header512` − `fixed_tok512_ov0pct` | +0.000 [-0.017, +0.017] | 0.130 | yes | +0.012 [-0.018, +0.042] | 0.219 | yes | 177 / 168 | 9 |
| **R3** | `parent256` − `fixed_tok512` | +0.006 [-0.017, +0.034] | 0.199 | yes | -0.018 [-0.061, +0.024] | 0.311 | yes | 177 / 164 | 13 |
| **R4** | `nbr1_512` − `fixed_tok512` | -0.023 [-0.045, -0.006] | 0.181 | yes | +0.025 [+0.006, +0.049] | 0.189 | yes | 177 / 162 | 15 |

<!-- /TABLE -->

On the pointed population every contrast is non-inferior on both endpoints at ε = 0.05 — which
is what a population at its ceiling looks like, and is why guard 1 exists.

---

## 5. The mode × size table

<!-- TABLE: Mode × size — cds, B = 16,384 SFR, reranker **on** -->

**Mode × size — cds, B = 16,384 SFR, reranker **on****

| id | vector: d ERET / d EPACK∩ | bm25: d ERET / d EPACK∩ | hybrid: d ERET / d EPACK∩ |
|---|---|---|---|
| **N1** | -0.028 [-0.131, +0.059] / -0.120 [-0.261, -0.000] | +0.043 [+0.008, +0.078] / -0.196 [-0.273, -0.111] | +0.051 [-0.024, +0.112] / -0.024 [-0.175, +0.115] |
| **N3** | +0.053 [-0.049, +0.156] / -0.323 [-0.504, -0.114] | +0.066 [+0.020, +0.125] / -0.378 [-0.533, -0.231] | +0.106 [+0.038, +0.174] / -0.120 [-0.313, +0.052] |
| **R1** | +0.165 [+0.028, +0.285] / -0.390 [-0.541, -0.239] | +0.050 [-0.003, +0.114] / -0.543 [-0.650, -0.444] | +0.165 [+0.090, +0.238] / -0.468 [-0.616, -0.317] |
| **R2** | +0.030 [-0.069, +0.146] / +0.064 [+0.004, +0.138] | -0.004 [-0.025, +0.017] / +0.002 [-0.042, +0.040] | +0.042 [-0.017, +0.117] / +0.027 [-0.039, +0.096] |
| **R3** | +0.001 [-0.046, +0.050] / +0.031 [-0.176, +0.213] | -0.043 [-0.086, -0.002] / -0.056 [-0.193, +0.099] | -0.031 [-0.081, +0.021] / -0.112 [-0.283, +0.017] |
| **R4** | -0.116 [-0.170, -0.063] / +0.074 [-0.119, +0.249] | -0.064 [-0.123, -0.019] / +0.158 [+0.083, +0.247] | -0.117 [-0.199, -0.054] / +0.065 [-0.090, +0.231] |

<!-- /TABLE -->

<!-- TABLE: Mode × size — cds, reranker **off** -->

**Mode × size — cds, reranker **off** (same frozen pools)**

| id | vector: d ERET / d EPACK∩ | bm25: d ERET / d EPACK∩ | hybrid: d ERET / d EPACK∩ |
|---|---|---|---|
| **N1** | +0.067 [+0.014, +0.113] / -0.039 [-0.175, +0.081] | +0.053 [+0.023, +0.089] / -0.239 [-0.339, -0.133] | +0.008 [-0.044, +0.059] / -0.114 [-0.264, -0.001] |
| **N3** | +0.054 [-0.021, +0.123] / -0.128 [-0.292, +0.021] | +0.087 [+0.035, +0.149] / -0.494 [-0.673, -0.314] | +0.030 [-0.030, +0.092] / -0.256 [-0.427, -0.118] |
| **R1** | +0.168 [+0.028, +0.290] / -0.209 [-0.379, -0.061] | +0.083 [+0.037, +0.139] / -0.580 [-0.695, -0.450] | +0.121 [+0.038, +0.198] / -0.469 [-0.645, -0.303] |
| **R2** | +0.059 [-0.028, +0.155] / +0.035 [-0.003, +0.096] | -0.007 [-0.020, +0.000] / +0.004 [+0.000, +0.012] | +0.005 [-0.042, +0.053] / +0.050 [-0.037, +0.161] |
| **R3** | -0.036 [-0.084, +0.013] / -0.065 [-0.276, +0.104] | -0.067 [-0.116, -0.026] / -0.055 [-0.170, +0.075] | -0.042 [-0.138, +0.026] / -0.097 [-0.249, +0.020] |
| **R4** | -0.132 [-0.182, -0.086] / +0.080 [-0.035, +0.216] | -0.090 [-0.153, -0.039] / +0.381 [+0.245, +0.520] | -0.083 [-0.120, -0.045] / +0.151 [+0.048, +0.264] |

<!-- /TABLE -->

<!-- TABLE: Mode × size — pointed, B = 16,384 SFR, reranker **on** -->

**Mode × size — pointed, B = 16,384 SFR, reranker **on****

| id | vector: d ERET / d EPACK∩ | bm25: d ERET / d EPACK∩ | hybrid: d ERET / d EPACK∩ |
|---|---|---|---|
| **N1** | +0.017 [-0.017, +0.051] / -0.033 [-0.079, +0.007] | +0.000 [-0.028, +0.028] / -0.068 [-0.112, -0.025] | +0.000 [-0.028, +0.028] / -0.031 [-0.067, +0.000] |
| **N3** | +0.045 [+0.006, +0.085] / -0.048 [-0.096, +0.000] | +0.000 [-0.034, +0.034] / -0.069 [-0.119, -0.019] | -0.006 [-0.034, +0.023] / -0.031 [-0.067, +0.006] |
| **R1** | +0.062 [+0.023, +0.102] / -0.115 [-0.176, -0.054] | -0.006 [-0.045, +0.034] / -0.223 [-0.299, -0.153] | +0.011 [-0.017, +0.040] / -0.103 [-0.158, -0.055] |
| **R2** | -0.011 [-0.028, +0.000] / +0.000 [-0.032, +0.032] | +0.000 [+0.000, +0.000] / +0.012 [-0.012, +0.036] | +0.000 [-0.017, +0.017] / +0.012 [-0.018, +0.042] |
| **R3** | -0.006 [-0.051, +0.034] / +0.020 [-0.034, +0.074] | -0.023 [-0.056, +0.006] / -0.044 [-0.095, +0.006] | +0.006 [-0.017, +0.034] / -0.018 [-0.061, +0.024] |
| **R4** | -0.011 [-0.028, +0.000] / +0.039 [+0.013, +0.071] | -0.023 [-0.045, -0.006] / +0.037 [+0.013, +0.069] | -0.023 [-0.045, -0.006] / +0.025 [+0.006, +0.049] |

<!-- /TABLE -->

<!-- TABLE: Mode × size — pointed, reranker **off** -->

**Q8 is supported.** BM25 gives the coarser arm a *larger* containment advantage than dense
does on every size contrast: N1 −0.196 under `bm25` against −0.120 under `vector` and −0.024
under `hybrid`; N3 −0.378 / −0.323 / −0.120; R1 −0.543 / −0.390 / −0.468. Term coverage grows
with chunk length and BM25 rewards it, exactly as the prediction's basis said. The same table
also shows BM25 costing the coarse arm slightly *more* reach (N3 +0.066 under `bm25` against
+0.053 under `vector`), so the lexical leg sharpens the trade at both ends rather than tilting
it.

**Q9 is supported.** Turning the reranker off on the **same frozen pools** reverses the sign of
several contrasts: `N1`'s `d ERET` under `vector` goes **−0.028 → +0.067**, `R3`'s goes
**+0.001 → −0.036** and its `d EPACK∩` **+0.031 → −0.065**; on the pointed set `R4`'s
`d EPACK∩` under `vector` goes **+0.039 → −0.007**. The reranker is not a monotone improvement
applied on top of the ranking — it changes which arm wins.

**`hybrid` is not the average of its legs.** On N1 and N3 the fused `d EPACK∩` (−0.024,
−0.120) is *smaller in magnitude* than either leg alone (`vector` −0.120/−0.323, `bm25`
−0.196/−0.378). RRF over two rankings that disagree admits documents neither leg would have
ranked highly, and that flattens the size contrast. **The served shape is the shape in which
chunk size matters least** — which is a finding about the served path, and the reason r3 §3.4
made `hybrid` confirmatory rather than reading the legs.

---

## 6. The pointed population and its guards

<!-- TABLE: Pointed guard 1 -->

**Guard 1 fails on its second half.** Discrimination is emphatic — the top-10 *retrieved*
document sets differ between the size extremes for **100 %** of the 177 queries under every
mode (Stage 0's `check3` reading, taken off the ranked pool rather than the packed context, so
it is not a budget artefact). But confirmatory `EPACK@16k` sits inside [0.15, 0.90] for **one
arm of eleven**: every other arm is **above** the ceiling, and `ERET` is above it for all
eleven. r3 §11 guard 1's own instruction is then unambiguous — *"if it fails discrimination it
is reported as a descriptive population, labeled so, and the CDS population alone carries the
decision under §1.1's limitation"* — and although it is the **window** half rather than the
discrimination half that failed, the consequence is the one the guard was written to produce:
**the pointed set is `DESCRIPTIVE`. It is not a fourth gate, and the four-way conjunction of
§11's amended decision rule does not apply.**

This is objection (i) of §11, measured. The Leg B pilot's `PH@10` ≈ 0.97–0.99 predicted it; the
construction that makes these queries answerable by one passage — a rare entity named in a deep
section — also makes that passage easy to retrieve. What the population *can* still do is
serve the synthesis stage, where the same construction gives a **gold answer** rather than a
gold location, and where a ceiling on evidence delivery is a feature: it isolates the
generator's contribution from the retriever's.

<!-- TABLE: Pointed guard 3 -->

**Guard 3's sizing is computed anyway, because it is cheap and because guard 1's verdict could
be revisited if the population were regenerated harder.** At α = 0.025 one-sided per endpoint,
ε = 0.05 and 80 % power, with the cluster the source document (one query per document), the
joint requirement is **100–400 queries** and every contrast is **within the 600 cap**. The
analytic per-endpoint numbers on the conservative σ bound are 115–424, also within the cap. So
had the population discriminated, it would have been affordable: **it is not the sizing that
disqualifies it, it is the ceiling.**

---

## 7. The BM25 concordance check

<!-- TABLE: BM25 concordance -->

**BM25 concordance against the dev tenant's Elasticsearch**

| statistic | value |
|---|---|
| queries | 197 |
| **mean overlap@50** | **0.9469** (bar ≥ 0.90) |
| median overlap@50 | 0.9600 |
| min overlap@50 | 0.2200 |
| mean Spearman on the intersection | 0.9657 |
| same rank-1 | 0.9594 |
| overlap@50, cds (20 queries) | 0.9340 |
| overlap@50, pointed (177 queries) | 0.9484 |
| verdict | **PASS** |

<!-- /TABLE -->

r3 §3.4 moved this check into Stage 0b′ so that *"a miss is fixed in the harness before any
contrast is read"*. It is not a miss. The in-process BM25 — `\w+` on lowercased text, no
stemming, no stopwords, Lucene's `idf = ln(1 + (N − df + 0.5)/(df + 0.5))`, k1 = 1.2, b = 0.75,
plain OR over query token occurrences — reproduces the served Elasticsearch's top-50 at
**0.9469** mean overlap, with rank-1 agreement **0.959** and Spearman **0.966** on the
intersection.

**The known departures are enumerated in the manifest, not discovered here**
(`s0b_common.BM25_PIN`): UAX#29 keeps `0.05` and `patient's` as single tokens where `\w+`
splits them; Lucene stores a lossy 1-byte length norm where this implementation uses the exact
chunk length; a token straddling a chunk boundary is charged to the chunk it starts in, where
Elasticsearch would analyse the chunk's own text and see a truncated word. **The minimum
overlap over 197 queries is 0.22** — a small number of short pointed queries whose decisive
term is exactly one of those departures. The mean clears the bar comfortably and the tail is
recorded rather than trimmed.

**The store discipline, stated because it is the only store this run touched.** The URL came
from `/rag/data/tenants/dev/config/tenant.env` and was asserted to end in `:24043` with
`:9200`, `:6333` and `:24041` refused by an explicit check. One index, `chkconf_<runid>_tok512`,
423,386 chunks, 53 s to load. It was deleted in a `finally` block and the verifying listing
recorded `index_gone: true`, `chkconf_indices_remaining: []`, two indices in the cluster —
the two that were there before.

---

## 8. Cost

| item | measured | r3 §4's estimate |
|---|---|---|
| new corpus embeddings | **0** | 0 |
| query embeddings | 197, **0.4 s**, 8,514 tokens | ≤ 200 |
| BM25 indexes, 6 arms | 41 s of shared tokenization + **3.3–12.5 s per arm** | "CPU minutes" ✓ |
| three-mode retrieval + rerank | **7.5 min** wall, 89,668 reranker pairs at 332 pairs/s | "minutes"; the estimate said 18–20k pairs, the run needed 89,668 because it covers **two populations** (197 queries, not 20) |
| packing 11 arms × 3 modes × 2 rerank × 3 budgets | **3.6 min** | not estimated |
| statistics (10,000-draw joint bootstrap) | **41 s** | not estimated |
| dev-tenant concordance | **112 s** | "minutes; one arm's chunks loaded and deleted" ✓ |
| **total wall clock** | **≈ 25 min** including the two re-runs of the statistics module | — |
| GPUs 6 and 7 | untouched | reserved ✓ |

---

## 9. Deviations

Each is a decision this run made where the specification was silent, unavailable or in tension
with itself. None was taken after seeing an endpoint.

| # | deviation | why, and what it costs |
|---|---|---|
| **D1** | **Generator tokens are an estimate, not a measurement.** `gen = sfr / 1.2564`. | `Llama-4-Scout`'s tokenizer is not in `/rag/cache` and the harness rules forbid contacting `mango:8003/tokenize`. The estimate is r3 §3.3's own measured ratio. **No budget, chunk size or contrast depends on it** — every one of those is in SFR tokens, which is what §3.3's Stage 0b′ path requires. The generator column is descriptive |
| **D2** | `parent256`'s cap is **1,024 SFR tokens**, where §5.2 wrote 1,024 generator tokens. | One tokenizer on both sides is §3.3's whole point; converting this one constant would have reintroduced the two-tokenizer accounting §3.3 exists to remove. It makes `parent256`'s parents ~20 % shorter than Stage 0's |
| **D3** | The plumbing check is **three checks**, and the end-to-end one is read at **5,146 SFR** rather than 4,096. | See §2. 4,096 SFR is ~3,261 generator tokens, a 20 % smaller context than Stage 0's; comparing it to Stage 0's number would have measured the tokenizer, not the harness |
| **D4** | **Evidence-bearing = any pooled support > 0**, per the brief. | This makes **all 208** pooled documents evidence-bearing, so `ERET`'s denominator is 20.8 documents per topic against r3 §3.1's "~10". The stricter unit-based denominator (≥ 1 D3 unit from the k = 0 union) is carried in every record and is **identical** here — every pooled document also has a k = 0 unit — so the choice moves no number |
| **D5** | The CDS primary query variant is **`summary`**. | Stage 0's convention, kept so σ_d is comparable. `description` is computed and stored (`stats.json → levels.cds_description`) |
| **D6** | The NEGATIVE control is **vacuous**, and is reported as such. | The labeling set has **no grade-0 pairs at all**, so there is nothing to score. r3 §3.1 already marked it 0 by construction; this is a second reason |
| **D7** | Three CDS documents over 120,000 characters are **flagged, not excluded**. | #513 excluded them from the *pointed* population; on CDS the brief says flag. The without-them sensitivity moves `ERET` by ≤ 0.008 per arm and changes no window verdict |
| **D8** | The neighbour **group** is the packing unit. | A source and its not-yet-admitted neighbours are admitted together or not at all. This is what makes r3 §3.5's "±1 at 512 costs 3× per source" true under a stop-at-first-non-fit walk; charging members individually would let a walk stop between a source and its neighbour. Dedup is by chunk id per §3.5, and the per-source-duplicated total is stored beside every record |
| **D9** | The `fixed_tok2048_ov0pct` pool was built in the **smoke** pass and its manifest row was overwritten by the full pass. | Same code, same inputs; its sha256 is `e74cb5b51f9ab7e97a4ddd523d0bc84d25ec21b824354fb4fe8aaaeac09475d8` and it is listed here rather than left missing |
| **D10** | Packed contexts are persisted in **reference form** for every configuration and as literal text for the primary one. | Materialising all 396 configurations would be several gigabytes of duplicated corpus. `contexts/INDEX.json` (committed as `artifacts/stage0b-prime/contexts-INDEX.json`) states the reconstruction rule exactly |

**Two things this run did not do.** It did not re-run the labeler — the gold is #512's,
unchanged. And it did **not** attempt the r2 §8.5.7 row-9 B/D recalibration for the three arms
whose `ERET` left the window: r3 §3.1 authorises it *"once, with its result recorded before any
contrast is read"*, and the contrasts in §4 are already read. The budget curve in §3 is the
information that recalibration would have produced, and it is reported as a curve.

---

## 10. Reproduction

```bash
export HF_HOME=/rag/cache
export PYTHONPATH=/home/wilke/Development/ragstack/python
export STAGE0_HELPERS=/home/wilke/Development/worktrees/phase0-rescue/phase0
cd docs/plans/results/stage0
./run_0bp.sh            # retrieve → gold → pack → score → checks → stats → contexts → es
/rag/envs/ragstack/bin/python3 s0b_report.py     # renders artifacts/stage0b-prime/TABLES.md
/rag/envs/ragstack/bin/python3 s0b_writeup.py    # splices those tables into this document
```

Each step writes a log and a pid file under `run/` and is polled **by the recorded pid**, never
by process name. `s0b_retrieve.py` is idempotent per arm (a finished `pool_<arm>.jsonl` is
skipped), so an interrupted run resumes. Every number in this document is printed by
`s0b_report.py` from `stats.json` / `checks.json` / `es_concordance.json`; none is transcribed
by hand.

The frozen pools (6 × 3.5 MB), the packed contexts (45 MB) and the endpoint records live under
`/rag/tmp/stage0-conf/work/stage0b-prime/` with sha256s in `retrieve_manifest.json`,
`pack_meta.json` and `score_meta.json`. They are indexed rather than committed; the
**synthesis stage** consumes `contexts/packed-<population>.jsonl.gz` and
`contexts/text/<population>/<arm>.jsonl.gz` per `contexts/INDEX.json`.

---

## 11. What this hands the next step

1. **The confirmation run cannot be powered at 80 topics on the current endpoint.** The gate
   in r3 §5 step 5 is failed, on power, for five of six contrasts. The choices are: raise the
   topic count toward the 286–453 the conservative bound asks for; narrow the `EPACK`
   estimand so its σ_d falls (the intersection already halved the reached-set's spread on N1
   and N3 — a per-**document** rather than per-topic cluster would go further, at the cost of
   a different denominator); or accept a larger ε with the reasoning stated. **ε did not move
   in this run and this document does not propose moving it.**
2. **Three arms are outside the reach window and are descriptive on `ERET`**:
   `fixed_tok2048_ov0pct`, `nbr1_512`, `nbr2_512`. The 2048 arm is the candidate of N3, so
   **N3 cannot be read as a decision at this budget** — which is a stronger statement than
   "N3 fails", and a cheaper one to act on: at 32,768 SFR tokens its reach is 0.176 and inside
   the window.
3. **The pointed population is `DESCRIPTIVE`.** It is at its ceiling on both endpoints. It
   should either be regenerated with a harder construction (deeper sections, entities that
   also occur elsewhere in the corpus, longer source documents) or repurposed — and the
   synthesis stage (`SPEC-synthesis-stage.md`) is the natural repurposing, because a ceiling
   on evidence delivery is what isolates the generator's contribution.
4. **`hybrid` flattens the size contrast.** The served shape is where chunk size matters least
   of the three modes. Whatever the size decision turns out to be, it will be a smaller
   decision on the served path than either leg suggests.
5. **The human read is still the blocking item.** Every `EPACK` in this document rests on a
   gold that reproduces itself at 0.92 and whose validity is unestablished. The three readings
   agreeing in sign is reassurance about the *reading*, not about the gold.
