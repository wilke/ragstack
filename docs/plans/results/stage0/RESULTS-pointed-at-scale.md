# RESULTS — the pointed population at corpus scale

**Specification:** [`../design/SPEC-confirmation-run-r3.md`](../design/SPEC-confirmation-run-r3.md)
§10 item 5, **option (b)**, read against §11 (the pointed population and its guards), §3.1
(the split endpoints), §3.2 (B = 16,384 SFR primary), §3.3 (one tokenizer, the embedder's),
§3.4 (the three modes) and §1 / PLAN-C (the owner's ~500k-article target corpus).

**Scope note, stated first because it bounds everything below.** Step 1 is a **projection**
from a corpus that was made *smaller*, not a measurement of a corpus that was made *bigger*.
Every extrapolated number here is labeled a projection and none of them is a measurement.
No corpus chunk was embedded, no confirmation topic was read, and no store of any kind was
contacted. **Step 2 did not run**, and §5 says exactly why, with the numbers that decided it
and the one reading under which the owner should overrule me.

---

**Verdict: `THE POINTED POPULATION DOES NOT BECOME DISCRIMINATIVE BY GROWING THE CORPUS.
REACH FALLS 0.032–0.046 PER DECADE; THE ARMS SEPARATE BY 0.017 AT 32,663 DOCUMENTS AND THE
SEPARATION GROWS SLOWER THAN THE NOISE. STEP 2 IS NOT WORTH ITS 10 GPU-HOURS.`**

* **Reach falls, slowly, and the fall is real.** Over the 0.91 decades this study can
  measure — 4,000 to 32,663 documents — pointed `ERET` falls from 0.974–0.983 to
  0.938–0.955, a slope of **−0.032 to −0.046 per decade** with every arm's bootstrap
  interval excluding zero. §3.
* **Projected to the owner's targets it stays at the ceiling.** The brief's own fit (linear
  in log₁₀ N) puts every arm at **0.909–0.932 at 150k** and **0.885–0.915 at 500k** — above
  the [0.15, 0.90] window on both. A logit fit, which this run added as a sensitivity, puts
  them at 0.843–0.897 and 0.721–0.838. **The two links disagree about the gate and the data
  cannot adjudicate between them**: every bootstrap interval straddles 0.90. §4.
* **So the gate was decided on what the window is a proxy for, and that answer is not
  close.** Between-arm `ERET` spread grows only **0.009 → 0.017** across the measured range,
  while σ_d of every paired size contrast **grows faster**: the query count 80 % power at
  ε = 0.05 would need rises from **32–64** at N = 4,000 to **107–125** at N = 32,663 on the
  three non-degenerate contrasts. A
  bigger corpus buys a **noisier** pointed population, not a more discriminative one. §5.
* **The population is 91 % trivial.** **161 of 177** queries have their gold document
  reached in **every** one of the 60 (arm × subset) cells, and one is reached in none. The
  entire signal lives in ~16 queries. §6.
* **The harness reproduces Stage 0b′ exactly.** At N = 32,663 every arm's `ERET` and
  `EPACK | reach` match `RESULTS-stage0b-prime.md`'s published pointed table to
  **Δ = 0.0000**, on all six arms and both endpoints. §7.

---

## 0. Provenance

| item | value |
|---|---|
| specification | `../design/SPEC-confirmation-run-r3.md` §10 item 5 (b), §11 |
| harness | `s0s_common.py`, `s0s_retrieve.py`, `s0s_score.py`, `s0s_fit.py`, `s0s_report.py`, `s0s_fig.py`, `s0s_writeup.py`, driven by `run_s0s.sh`. Stage 0b′'s `s0b_*.py` are **imported, not modified** — `s0b_bm25` (the lexical leg), `s0b_pack.pack_groups` / `ordered_pool` (the A1 walk), `s0b_retrieve.rrf` (the fusion), `s0b_score.contained` (D4 containment), `s0b_common` (paths, arms, modes, budgets, the BM25 pin, the SFR tokenizer). The only new degree of freedom in this study is **which documents are in the corpus** |
| repo commit | recorded in every artifact's `provenance` block, together with the Stage 0 pin `55a0fc2` and the assertion that the segmenter's diff against that pin is **EMPTY** |
| interpreter | `/rag/envs/ragstack/bin/python3`, `HF_HOME=/rag/cache`, `PYTHONPATH=…/ragstack/python`, `STAGE0_HELPERS=…/phase0-rescue/phase0` |
| **new embeddings** | **0.** A subsample is a **row mask** over the frozen matrices under `/rag/tmp/stage0-conf/emb/`, which are opened `mmap_mode="r"` and never written |
| query vectors | **reused** from `stage0b-prime/query_vectors.npy` — not re-embedded. This is what makes the full-corpus row of every table *the same number* Stage 0b′ published rather than a number that agrees with it |
| reranker | `:50052` → `BAAI/bge-reranker-v2-m3`, ≤ 4 in flight, **389,697 pairs** in 21,364 requests, **0 retries** |
| **stores** | **none.** No Qdrant, no Elasticsearch, no tenant API, never `mango`. No store client is constructed in this module or anything it imports |
| GPUs 6 and 7 | untouched — no endpoint started, no device selected |
| sequestration | `s0b_common.assert_dev_only` runs over all 177 pointed `draw_topic`s before anything else. **No confirmation topic's query or label was read** |
| seeds | subsample `20260917` (= `SEED_BIASBOUND + 1`, the brief's); bootstrap `20260913` (Stage 0b's) |
| budget | **16,384 SFR tokens**, Stage 0b′'s primary; shape `hybrid` + reranker on |
| artifacts | `artifacts/pointed-scale/` — `levels.json`, `fit.json`, `separation.json`, `difficulty.json`, `TABLES.md`, `INDEX.json`, plus copies of `retrieve_manifest.json` and `score_meta.json`. The 167 MB of frozen pools lives under `/rag/tmp/stage0-conf/work/pointed-scale/` with sha256s in `INDEX.json` |
| figure | [`figures/fig-pointed-reach-vs-scale.svg`](figures/fig-pointed-reach-vs-scale.svg) |

---

## 1. The question, and why subsampling down answers it

Stage 0b′ ([`RESULTS-stage0b-prime.md`](RESULTS-stage0b-prime.md) §6) found the pointed
population **at its ceiling**: `ERET` 0.904–0.955 and `EPACK | reach` 0.870–1.000, so r3
§11 guard 1's window half failed for ten of eleven arms and the population became
`DESCRIPTIVE`. It had the query count to be powered — guard 3 sized the joint requirement
at 100–400 against a 600 cap — so **it is not the sizing that disqualified it, it is the
ceiling**.

Retrieval difficulty rises with corpus size. The owner's target is ~500k PMC OA articles,
**15×** the 32,663 this study has. The question r3 §10 item 5 (b) asks is whether the
ceiling is an artefact of a small corpus that a big one would remove.

**Subsampling down is the free half of the answer and it is not a substitute for the paid
half.** Two things it does honestly, and one it cannot do:

* It measures a **real slope**. Every arm's reach is measured at four corpus sizes with the
  same queries, the same gold, the same packing rule and the same reranker; the only thing
  that changes is how many documents are competing. Three seeded draws at each of the three
  reduced sizes bound the draw-to-draw noise.
* It keeps the **gold documents in**, always, so `ERET` is never trivially zero and the
  denominator never moves. The subsets are **nested** — a prefix of one seeded permutation
  of the non-gold documents — so the four sizes of one draw are a genuine ladder rather
  than four unrelated corpora.
* It **cannot** tell you the functional form beyond the measured range. Extrapolating 0.91
  measured decades out to 1.19 further decades is an assumption about the shape of the
  curve, and §4 reports the two shapes that fit the data equally well and disagree at the
  end. **Neither is a measurement.**

**One more thing the projection cannot do, and it cuts in a specific direction.** The
existing corpus is CDS-qrels-derived — every document is judged against one of the ninety
TREC CDS topics, so it is *topically concentrated*. Uniform thinning preserves that
concentration, which means each document this study removes (or, running the fit backwards,
adds) is a comparatively **strong** distractor. A real 500k PMC OA corpus is topically
broad, and the pointed queries are built on **rare entities**: `RESULTS-stage0b-pointed-gen.md`
§5.4 measured that **177 of 177** accepted queries ask for at least one rare term the source
document's own front matter never names. A broad corpus adds few documents that can compete
for such a term. So the measured slope is more likely an **over**-estimate of the reach loss
a real corpus growth would cause than an under-estimate — the projections below are, if
anything, **optimistic about the population becoming discriminative**, and they still say it
does not.

---

## 2. What was run

| step | what | cost |
|---|---|---|
| subsets | 10: three seeded draws at 4,000 / 8,000 / 16,000 documents plus the full 32,663 once. Gold documents (177, one per query) always retained; sizes exact; nested within a draw | seconds |
| corpus tokenization for BM25 | one pass shared by every arm and every subset: 109,496,847 tokens, 621,826 types | **42 s** |
| retrieval | 6 index arms × 10 subsets × 3 modes × 177 queries. `vector` = exact cosine over the arm's frozen embeddings restricted to the subset's rows, top 100 → 50; `bm25` = `s0b_bm25.ArmBM25` **rebuilt on the subset's rows** so `df`, `N` and `avgdl` are the subset's own, top 100 → 50; `hybrid` = per-leg 100, RRF k = 60, top 50 | **29 min** wall for all six arms |
| reranking | the union of one (arm, query, subset)'s three pools, on `:50052`, ≤ 4 in flight | **389,697 pairs**, 0 retries |
| packing | `s0b_pack.pack_groups`, A1 walk, B = 16,384 SFR, 6 arms × 3 modes × 2 rerank states × 10 subsets × 177 queries = **63,720** packed contexts | **12 s** |
| scoring | pointed `ERET` / `EPACK`, 63,720 endpoint records | included above |
| fit + 2,000-draw cluster bootstrap | per arm, both link functions, both targets | **~40 s** |

**Two economies, and both are exact rather than approximate.** The dense similarity matrix
for the 177 queries is computed **once per arm** over the whole embedding matrix and then
*masked* per subset — a subset's top-100 is the top-100 of the masked columns, so nothing is
approximated, and the six full passes cost 1–7 s each instead of sixty. Reranker scores are
cached per `(query, chunk row)` **across subsets**: the crossencoder scores a (query, chunk
text) pair, and neither side of that pair depends on which other documents are in the
corpus. Without the cache this run would have made ~900k reranker calls; it made 389,697.

---

## 3. Reach against corpus size

<!-- TABLE: Pointed reach vs corpus size -->

**Pointed reach vs corpus size — `hybrid` + rerank, B = 16,384 SFR (mean over draws ± half-range)**

| arm | N = 4,000 | N = 8,000 | N = 16,000 | N = 32,663 | Δ 4k→32.7k |
|---|---|---|---|---|---|
| `fixed_tok256_ov0pct` | 0.981 ±0.003 | 0.976 ±0.003 | 0.961 ±0.000 | 0.955 | -0.026 |
| `fixed_tok512_ov0pct` | 0.983 ±0.006 | 0.966 ±0.000 | 0.962 ±0.003 | 0.955 | -0.028 |
| `fixed_tok512` | 0.981 ±0.003 | 0.966 ±0.000 | 0.955 ±0.000 | 0.938 | -0.043 |
| `header512` | 0.981 ±0.006 | 0.966 ±0.000 | 0.959 ±0.003 | 0.955 | -0.026 |
| `fixed_tok1024_ov0pct` | 0.981 ±0.003 | 0.970 ±0.003 | 0.964 ±0.006 | 0.938 | -0.043 |
| `fixed_tok2048_ov0pct` | 0.974 ±0.003 | 0.966 ±0.006 | 0.953 ±0.003 | 0.944 | -0.030 |

<!-- /TABLE -->

<!-- TABLE: Pointed EPACK | reach vs corpus size -->

**Pointed EPACK | reach vs corpus size — `hybrid` + rerank, B = 16,384 SFR**

| arm | N = 4,000 | N = 8,000 | N = 16,000 | N = 32,663 | Δ 4k→32.7k |
|---|---|---|---|---|---|
| `fixed_tok256_ov0pct` | 0.935 ±0.003 | 0.923 ±0.003 | 0.900 ±0.000 | 0.870 | -0.065 |
| `fixed_tok512_ov0pct` | 0.977 ±0.009 | 0.949 ±0.003 | 0.951 ±0.003 | 0.923 | -0.054 |
| `fixed_tok512` | 0.983 ±0.006 | 0.965 ±0.006 | 0.963 ±0.003 | 0.952 | -0.031 |
| `header512` | 0.981 ±0.003 | 0.961 ±0.003 | 0.955 ±0.003 | 0.935 | -0.046 |
| `fixed_tok1024_ov0pct` | 1.000 ±0.000 | 0.994 ±0.000 | 0.981 ±0.003 | 0.982 | -0.018 |
| `fixed_tok2048_ov0pct` | 0.990 ±0.003 | 0.988 ±0.000 | 0.988 ±0.000 | 0.988 | -0.002 |

<!-- /TABLE -->

**Reach does fall, and the ± column says the fall is not draw noise.** The half-range across
the three draws at a given size is 0.000–0.006, against a 4k→32.7k drop of 0.026–0.043. The
shipping arm `fixed_tok512` falls furthest (−0.043) and the 256 and 512/0 arms least
(−0.026, −0.028).

**Containment falls too, and faster in proportion.** `EPACK | reach` drops 0.002–0.065,
and the arms that drop most are the *fine* ones — the 256 arm goes 0.935 → 0.870 while the
2048 arm goes 0.990 → 0.988. That is the same trade Stage 0b′ §3 found across chunk sizes,
now showing up along the corpus-size axis: as the pool fills with better-scoring distractors,
a fine arm spends its budget on more documents and contains less of each. It is the one
place in this study where corpus size moves the arms *apart* (§5's second column).

<!-- TABLE: Pointed ERET by mode and corpus size -->

**Pointed ERET by mode and corpus size — reranker on, B = 16,384 SFR**

| arm | mode | N = 4,000 | N = 8,000 | N = 16,000 | N = 32,663 |
|---|---|---|---|---|---|
| `fixed_tok256_ov0pct` | `vector` | 0.976 | 0.961 | 0.947 | 0.904 |
| `fixed_tok256_ov0pct` | `bm25` | 0.976 | 0.964 | 0.944 | 0.921 |
| `fixed_tok256_ov0pct` | `hybrid` | 0.981 | 0.976 | 0.961 | 0.955 |
| `fixed_tok512_ov0pct` | `vector` | 0.959 | 0.945 | 0.906 | 0.887 |
| `fixed_tok512_ov0pct` | `bm25` | 0.983 | 0.968 | 0.944 | 0.938 |
| `fixed_tok512_ov0pct` | `hybrid` | 0.983 | 0.966 | 0.962 | 0.955 |
| `fixed_tok512` | `vector` | 0.947 | 0.932 | 0.923 | 0.887 |
| `fixed_tok512` | `bm25` | 0.987 | 0.970 | 0.940 | 0.927 |
| `fixed_tok512` | `hybrid` | 0.981 | 0.966 | 0.955 | 0.938 |
| `header512` | `vector` | 0.949 | 0.936 | 0.902 | 0.876 |
| `header512` | `bm25` | 0.976 | 0.964 | 0.944 | 0.938 |
| `header512` | `hybrid` | 0.981 | 0.966 | 0.959 | 0.955 |
| `fixed_tok1024_ov0pct` | `vector` | 0.949 | 0.921 | 0.913 | 0.870 |
| `fixed_tok1024_ov0pct` | `bm25` | 0.974 | 0.964 | 0.953 | 0.927 |
| `fixed_tok1024_ov0pct` | `hybrid` | 0.981 | 0.970 | 0.964 | 0.938 |
| `fixed_tok2048_ov0pct` | `vector` | 0.945 | 0.910 | 0.881 | 0.842 |
| `fixed_tok2048_ov0pct` | `bm25` | 0.972 | 0.964 | 0.945 | 0.927 |
| `fixed_tok2048_ov0pct` | `hybrid` | 0.974 | 0.966 | 0.953 | 0.944 |

<!-- /TABLE -->

**The mode table says the ceiling is `hybrid`'s doing.** `vector` alone falls much faster —
`fixed_tok2048_ov0pct` goes 0.945 → 0.842 over the measured range, a −0.11 slope per decade,
nearly three times the fused arm's — and `bm25` alone sits between the two. The served shape
fuses two rankings that fail on different queries, and the union of their successes is what
holds reach at 0.94 when the dense leg alone would be at 0.84. **This is the same finding
Stage 0b′ §5 stated for chunk size — "`hybrid` is not the average of its legs" — restated on
the corpus-size axis**, and it is the mechanism behind the ceiling: to lose a pointed query
you now have to defeat *both* legs.

---

## 4. The fit, and the projection to 150k and 500k

<!-- TABLE: Reach vs log₁₀(corpus size) -->

**Reach vs log₁₀(corpus size) — fit and projection (PROJECTION, not a measurement)**

| arm | slope / decade (linear) | slope 95 % CI | R² | reach @150k linear [95 %] | reach @150k logit [95 %] | reach @500k linear [95 %] | reach @500k logit [95 %] |
|---|---|---|---|---|---|---|---|
| `fixed_tok256_ov0pct` | -0.0323 | [-0.0618, -0.0082] | 0.908 | 0.932 [0.878, 0.977] | 0.897 [0.677, 0.970] | 0.915 [0.847, 0.971] | 0.828 [0.344, 0.961] |
| `fixed_tok512_ov0pct` | -0.0311 | [-0.0594, -0.0089] | 0.819 | 0.931 [0.879, 0.974] | 0.887 [0.668, 0.966] | 0.915 [0.849, 0.969] | 0.807 [0.362, 0.956] |
| `fixed_tok512` | -0.0455 | [-0.0805, -0.0176] | 0.982 | 0.909 [0.847, 0.959] | 0.843 [0.601, 0.941] | 0.885 [0.805, 0.949] | 0.721 [0.262, 0.913] |
| `header512` | -0.0318 | [-0.0610, -0.0089] | 0.831 | 0.929 [0.874, 0.973] | 0.886 [0.670, 0.964] | 0.912 [0.846, 0.967] | 0.809 [0.361, 0.955] |
| `fixed_tok1024_ov0pct` | -0.0386 | [-0.0651, -0.0171] | 0.814 | 0.922 [0.873, 0.961] | 0.883 [0.727, 0.951] | 0.901 [0.840, 0.952] | 0.800 [0.454, 0.923] |
| `fixed_tok2048_ov0pct` | -0.0343 | [-0.0673, -0.0086] | 0.894 | 0.921 [0.865, 0.969] | 0.894 [0.737, 0.961] | 0.903 [0.831, 0.962] | 0.838 [0.481, 0.951] |

<!-- /TABLE -->

**Read the two link functions as the width of the projection, not as two answers.** The
brief's own words are *"fit reach vs log(corpus size)"*, so the **linear** fit is primary
here and the **logit** fit is this run's added sensitivity, reported beside it and never in
place of it. They fit the ten measured points about equally well (linear R² 0.81–0.98) and
they disagree at the end by more than either one's own interval — 0.885–0.915 against
0.721–0.838 at 500k. That disagreement **is** the honest width of the extrapolation, and
averaging it away would have manufactured a precision the 0.91 measured decades do not
support.

Three things the intervals are not:

* They are a **cluster bootstrap over the 177 queries** (2,000 resamples, seed 20260913,
  the cluster the source document since there is exactly one query per document). They
  capture *which 177 queries were drawn*, which is the dominant source of sampling
  uncertainty here.
* They do **not** capture whether reach is linear in log N at all outside the measured
  range. Nothing in this design can.
* They do **not** capture the composition effect §1 named — that a real 500k PMC OA corpus
  is topically broader than a uniformly thinned CDS pool, which should make the true decay
  *slower* than either fit.

---

## 5. Guard 1, the step-2 gate, and why step 2 did not run

<!-- TABLE: r3 §11 guard 1 -->

**r3 §11 guard 1 (the window half) at each measured corpus size**

| N | arms with ERET in [0.15, 0.90] | arms with EPACK\|reach in window | guard 1 verdict |
|---|---|---|---|
| 4,000 | 0/6 | 0/6 | FAIL |
| 8,000 | 0/6 | 0/6 | FAIL |
| 16,000 | 0/6 | 1/6 | FAIL |
| 32,663 | 0/6 | 1/6 | FAIL |

<!-- /TABLE -->

**Guard 1's window half fails at every measured size, and it fails harder as the corpus
grows only in the sense that it gets closer.** No arm's `ERET` is inside [0.15, 0.90] at any
corpus size this study measured; `EPACK | reach` enters the window for exactly one arm (the
256 arm, at 0.900 and 0.870) at the two largest sizes. The population is at its ceiling at
4,000 documents and it is at its ceiling at 32,663.

<!-- TABLE: The step-2 gate -->

**The step-2 gate — ≥ 2 arms inside [0.15, 0.90] at 150,000 documents**

| arm | projected ERET @150k, linear | in window | logit | in window |
|---|---|---|---|---|
| `fixed_tok1024_ov0pct` | 0.922 | OUT | 0.883 | IN |
| `fixed_tok2048_ov0pct` | 0.921 | OUT | 0.894 | IN |
| `fixed_tok256_ov0pct` | 0.932 | OUT | 0.897 | IN |
| `fixed_tok512` | 0.909 | OUT | 0.843 | IN |
| `fixed_tok512_ov0pct` | 0.931 | OUT | 0.887 | IN |
| `header512` | 0.929 | OUT | 0.886 | IN |
| **arms in window** | **0/6** | | **6/6** | |

<!-- /TABLE -->

**The gate as written cannot be decided by this data.** The brief's rule is *"proceed to
step 2 only if the extrapolation puts at least two arms' pointed `ERET` inside [0.15, 0.90]
at 150k"*. Under the linear fit — the brief's own — **0 of 6** arms are inside. Under the
logit fit **6 of 6** are inside, but by margins of 0.003–0.057 and with bootstrap intervals
that straddle 0.90 in both directions. A ten-GPU-hour decision that turns entirely on a
choice of link function is not a decision the data made.

So the tie was broken on **what the window is a proxy for**: whether the population can
register a difference *between* arms. That is measurable directly, on the records this study
already has, and it is not close.

<!-- TABLE: Do the arms separate? -->

**Do the arms separate? — between-arm spread at each corpus size**

| N | lowest arm's ERET | highest arm's ERET | between-arm spread | between-arm spread, EPACK unconditional |
|---|---|---|---|---|
| 4,000 | 0.974 | 0.983 | **0.009** | 0.064 |
| 8,000 | 0.966 | 0.976 | **0.009** | 0.064 |
| 16,000 | 0.953 | 0.964 | **0.011** | 0.081 |
| 32,663 | 0.938 | 0.955 | **0.017** | 0.102 |

<!-- /TABLE -->

<!-- TABLE: The paired size contrasts at each corpus size -->

**The paired size contrasts at each corpus size — d, σ_d, and the query count 80 % power at ε = 0.05 would need**

| contrast | N = 4,000 | N = 8,000 | N = 16,000 | N = 32,663 |
|---|---|---|---|---|
| **N1** `fixed_tok512` → `fixed_tok1024_ov0pct` | d -0.0000<br>σ_d 0.101<br>n₈₀ 32 | d +0.0038<br>σ_d 0.079<br>n₈₀ 20 | d +0.0094<br>σ_d 0.097<br>n₈₀ 30 | d +0.0000<br>σ_d 0.185<br>n₈₀ 108 |
| **N3** `fixed_tok512` → `fixed_tok2048_ov0pct` | d -0.0075<br>σ_d 0.100<br>n₈₀ 32 | d +0.0000<br>σ_d 0.094<br>n₈₀ 28 | d -0.0019<br>σ_d 0.186<br>n₈₀ 110 | d +0.0056<br>σ_d 0.199<br>n₈₀ 125 |
| **R1** `fixed_tok256_ov0pct` → `fixed_tok2048_ov0pct` | d -0.0075<br>σ_d 0.142<br>n₈₀ 64 | d -0.0094<br>σ_d 0.130<br>n₈₀ 54 | d -0.0075<br>σ_d 0.170<br>n₈₀ 91 | d -0.0113<br>σ_d 0.184<br>n₈₀ 107 |
| **R2** `header512` → `fixed_tok512_ov0pct` | d +0.0019<br>σ_d 0.025<br>n₈₀ 2 | d +0.0000<br>σ_d 0.000<br>n₈₀ — | d +0.0038<br>σ_d 0.050<br>n₈₀ 8 | d +0.0000<br>σ_d 0.107<br>n₈₀ 36 |

<!-- /TABLE -->

**Every paired size contrast's `d` is an order of magnitude below ε = 0.05 at every corpus
size** — |d| ≤ 0.011 across N1, N3, R1 and R2 and across 4,000 to 32,663 documents — and
**σ_d grows with corpus size on all four**: N1 0.101 → 0.185, N3 0.100 → 0.199, R1 0.142 →
0.184, R2 0.025 → 0.107. The query count 80 % power at ε = 0.05 would need therefore rises
from **32–64** to **107–125** on N1, N3 and R1, and from 2 to 36 on R2 — whose two arms are
very nearly the same arm at 4,000 documents (σ_d = 0.025) and are not at 32,663. The between-arm spread does grow
(0.009 → 0.017 on `ERET`, 0.064 → 0.102 on unconditional `EPACK`), but slower than the noise
it has to beat.

**`STEP 2 DOES NOT RUN.`** A 5× corpus, at ≈ 10 GPU-hours of embedding, ≈ 130 GB of disk and
a day of fetch/parse/chunk, would buy a population whose arms are *further apart in absolute
terms and harder to separate statistically* than the one already in hand. The brief's own
fallback applies: **stop after step 1 and report; the owner decides on 500k directly.**

**Where I could be wrong, stated so it can be checked rather than trusted.** If the logit
fit is the right functional form *and* the decay accelerates below 0.90 — which it must, at
some corpus size, since reach cannot stay above 0.9 forever — then somewhere past 500k the
population would leave the ceiling. The 500k logit projections (0.721–0.838, with intervals
reaching down to 0.26) are the first numbers in this study that are plausibly *inside* the
window. **If the owner wants the pointed population as a gate rather than as a descriptive
set, 500k is the size worth buying and 150k is not**, and §11 says what that would cost.

---

## 6. Per-query difficulty

<!-- TABLE: Per-query difficulty -->

**Per-query difficulty — the truly easy queries**

| N | queries reached by every arm and every draw | fraction |
|---|---|---|
| 4,000 | 170 | 0.961 |
| 8,000 | 168 | 0.949 |
| 16,000 | 163 | 0.921 |
| 32,663 | 161 | 0.910 |
| **every size** | **161** | **0.910** |

<!-- /TABLE -->

**161 of 177 queries (91.0 %) have their gold document reached in every one of the 60
(arm × subset) cells.** The full reach-count histogram is
`{0: 1, 5: 1, 8: 1, 14: 1, 15: 1, 24: 1, 50: 1, 51: 1, 52: 2, 53: 2, 56: 1, 58: 1, 59: 2, 60: 161}`
— a single query is reached nowhere, five are reached in fewer than half the cells, and the
remaining 171 are reached in 50 or more. **The population's entire discriminating capacity
lives in about sixteen queries**, and that is the ceiling stated as a count instead of a
rate.

This is the number to regenerate against. r3 §11's construction — a rare entity named in a
deep section — produced 177 questions of which 161 are answerable by a retriever that only
has to find one uniquely-named thing. `RESULTS-stage0b-prime.md` §11 item 3 already proposed
the harder construction (deeper sections, entities that also occur elsewhere in the corpus,
longer source documents); **this document's contribution to that proposal is the measurement
that corpus size is not an alternative to it.**

---

## 7. The full-corpus check

<!-- TABLE: Full-corpus check -->

**Full-corpus check — this harness against Stage 0b′'s published table**

| arm | ERET here (N = 32,663) | ERET Stage 0b′ | Δ | EPACK here | EPACK Stage 0b′ | Δ |
|---|---|---|---|---|---|---|
| `fixed_tok256_ov0pct` | 0.955 | 0.955 | +0.000 | 0.870 | 0.870 | +0.000 |
| `fixed_tok512_ov0pct` | 0.955 | 0.955 | +0.000 | 0.923 | 0.923 | +0.000 |
| `fixed_tok512` | 0.938 | 0.938 | +0.000 | 0.952 | 0.952 | +0.000 |
| `header512` | 0.955 | 0.955 | +0.000 | 0.935 | 0.935 | +0.000 |
| `fixed_tok1024_ov0pct` | 0.938 | 0.938 | +0.000 | 0.982 | 0.982 | +0.000 |
| `fixed_tok2048_ov0pct` | 0.944 | 0.944 | +0.000 | 0.988 | 0.988 | +0.000 |

<!-- /TABLE -->

The N = 32,663 row of every table in this document is Stage 0b′'s published pointed table,
to **Δ = 0.0000** on all six arms and both endpoints. That is stronger than agreement: the
query vectors are Stage 0b′'s own frozen ones and the packing walk is Stage 0b′'s own
function, so the full-corpus point is the *same computation*, and this check confirms that
the subsetting machinery does not perturb it when the subset is everything.

---

## 8. Cost

| item | measured |
|---|---|
| new corpus embeddings | **0** |
| query embeddings | **0** — Stage 0b′'s frozen vectors reused |
| corpus tokenization for BM25 | 42 s, once, shared by 60 (arm, subset) index builds |
| retrieval, 6 arms × 10 subsets × 3 modes × 177 queries | **1,740 s** (29 min) wall |
| reranker | **389,697 pairs**, 21,364 requests, 0 retries, ≤ 4 in flight |
| packing + scoring, 63,720 contexts | **12 s** |
| fit + 2,000-draw cluster bootstrap | ~40 s |
| **total wall clock** | **≈ 35 min**, plus a discarded 9-minute partial run (deviation D3) |
| disk written | **167 MB** under `work/pointed-scale/`; `/rag` free before and after ≈ **1,358 GB** |
| GPUs 6 and 7 | untouched |
| **step 2, not spent** | ≈ 120k S3 fetches, ≈ 6B SFR tokens ≈ 10 GPU-hours, ≈ +130 GB |

---

## 9. Deviations

Each is a decision this run made where the specification was silent, unavailable or in
tension with itself. None was taken after seeing an endpoint, except D2, which is a decision
*about* an endpoint and says so.

| # | deviation | why, and what it costs |
|---|---|---|
| **D1** | **The full corpus is one draw, not three.** | Three seeded draws of "everything" are the same set. The fit therefore has ten points, not twelve, and the 32,663 column of §3's tables carries no ± because it has no replicate to spread. Saying so is cheaper than manufacturing a replicate that does not exist |
| **D2** | **The step-2 gate was decided on the separation evidence, not on the mechanical rule.** | The two link functions disagree (linear 0/6 in the window, logit 6/6) and every bootstrap interval straddles 0.90, so the mechanical rule returns different answers depending on a modelling choice the data cannot adjudicate. §5 breaks the tie on what the window is a proxy for — whether the arms separate — and that evidence is one-directional: σ_d grows on all four contrasts. **This is the deviation the owner should check first**, and §5's last paragraph states the reading under which it is wrong |
| **D3** | **A 9-minute partial run was discarded rather than used.** | A launch bug left **two** copies of `s0s_retrieve.py` writing the same output file. The surviving file was 1,770 lines, every line valid JSON, with exactly the 1,770 expected `(subset, qid)` keys — because both processes computed identical content and one overwrote the other's bytes with the same bytes. It was deleted and the arm recomputed under a single writer anyway: a file that *looks* perfect and is a mixture of two writers is the worst kind of artifact, and "I checked and it happened to be fine" is not a provenance claim. `s0s_common.RunLock` now refuses to start a second writer, naming the live pid |
| **D4** | **Only the six index arms are scored.** Stage 0b′'s four delivery arms and `multi256+1024` are not. | They are re-derivations of the same six pools and cannot change the reach-vs-size *shape* this study fits — a neighbour arm reaches a document exactly when its source arm does. Excluding them halves the packing work. It costs R3 and R4 from §5's contrast table, which is why that table has four rows and not six |
| **D5** | **Reranker scores are cached across subsets.** | A crossencoder scores a (query, chunk text) pair; neither side depends on the corpus. The cache is an exact identity, not an approximation, and it is what made 389,697 pairs do the work of ~900,000. It is stated because a reader who audits the pair count against the subset count will otherwise find it short |
| **D6** | **Query vectors are reused from Stage 0b′ rather than re-embedded.** | Step 1 makes **zero** embedding calls, and the full-corpus point becomes the *same computation* as Stage 0b′'s rather than one that agrees with it (§7). A re-embed would have added a 0.4-second cost and a source of drift |
| **D7** | **The logit fit is this run's own addition**, not the brief's. | The brief asked for reach against log(corpus size); a linear fit in that variable is unbounded and will project a probability above 1 given enough decades. The logit fit is reported as a sensitivity so the reader can see how much of the 500k projection is the functional form. The **linear** fit remains primary everywhere, including in the gate |
| **D8** | **`EPACK` is reported both conditionally and unconditionally**, and §3's table is the conditional one. | Stage 0b′ tabled `EPACK | reach`, so the comparison in §7 has to be against that. The unconditional column is in `levels.json` and is what §5's spread table uses, because a spread over a conditional quantity with a moving denominator is not a spread |

---

## 10. Reproduction

```bash
export HF_HOME=/rag/cache
export PYTHONPATH=/home/wilke/Development/ragstack/python
export STAGE0_HELPERS=/home/wilke/Development/worktrees/phase0-rescue/phase0
cd docs/plans/results/stage0
./run_s0s.sh smoke      # 500 documents, one arm -- the stop-before-scaling check
./run_s0s.sh            # retrieve -> score -> fit -> report
/rag/envs/ragstack/bin/python3 s0s_fig.py       # figures/fig-pointed-reach-vs-scale.svg
/rag/envs/ragstack/bin/python3 s0s_writeup.py   # splices TABLES.md into this document
```

Each stage writes a log and a pid file under `run/` and is polled **by the recorded pid**,
never by process name. `s0s_retrieve.py` is idempotent per arm (a finished `pool_<arm>.jsonl`
is skipped) and holds a single-writer lock on its output directory for the duration.

Every number in this document is printed by `s0s_report.py` from `levels.json`, `fit.json`,
`separation.json` and `difficulty.json`; none is transcribed by hand. The 167 MB of frozen
pools lives under `/rag/tmp/stage0-conf/work/pointed-scale/` with sha256s in
`artifacts/pointed-scale/INDEX.json`.

**The smoke check, for the record.** 500 documents plus the 177 gold, one arm
(`fixed_tok512_ov0pct`): `ERET` 0.994 under `hybrid` + rerank against 0.955 at the full
corpus — the direction the whole study exists to measure, seen before scaling.

---

## 11. What this hands the next step

1. **Corpus size is not the fix for the pointed population.** Reach falls 0.032–0.046 per
   decade; the arms separate by 0.017 at 32,663 documents and the noise grows faster than
   the separation. The population's ceiling is a property of its **construction** — a rare
   entity, named once, in a corpus where nothing else names it — not of the corpus's size.
   `RESULTS-stage0b-prime.md` §11 item 3's harder construction is the live option; this
   document removes the alternative.
2. **If the owner wants the pointed set as a gate anyway, 500k is the size to buy, not
   150k.** 150k is inside the window on one of two equally-well-fitting link functions and
   outside it on the other; 500k is the first size where the pessimistic fit puts every arm
   plausibly inside. The cost scales from step 2's ≈ 10 GPU-hours to roughly **50** and from
   +130 GB to roughly **+520 GB**, and `/rag` currently has 1,358 GB free, so it is
   affordable — but §5's separation table says the arms still will not separate, and that
   argument does not depend on the level at all.
3. **The one number that would change this conclusion is a construction, not a corpus.** A
   pointed set whose 60-cell reach histogram is not 91 % at the maximum would be
   discriminative at 32,663 documents today, for free. The histogram in §6 is the
   acceptance criterion to write into that regeneration.
4. **`hybrid` holds the ceiling up.** `vector` alone loses reach nearly three times as fast
   with corpus size (2048 arm: −0.11 per decade against −0.034 fused). Any future reading of
   this population on a single leg will see a much steeper size dependence than the served
   path does — which is Stage 0b′ §5's "the served shape is the shape in which chunk size
   matters least", restated for corpus size.
5. **CDS is untouched by all of this.** No CDS topic was retrieved, packed or scored in this
   study, and had step 2 run, CDS could **not** have been re-scored on an enlarged corpus:
   its judgments are relative to its own pool, and every added document would be unjudged,
   so a `ERET` computed over an enlarged corpus would silently change the estimand rather
   than measure the same one at a new size.
