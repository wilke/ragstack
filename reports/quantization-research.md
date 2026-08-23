# Quantizing the open-access collection — research synthesis for #333

*Deep-research run, 2026-08-22: 5 search angles → 17 primary sources fetched → 84
claims extracted → 25 adversarially verified (3-vote; 23 confirmed, 2 refuted) →
11 merged findings. Numbers below are quoted from sources; where a figure is our
own arithmetic on a source's formula, it says so.*

## The question, and the answer that reframes it

We hold **47,625,155 × 4096-d float32** (SFR-Embedding-Mistral) in Qdrant
**1.18.0**, HNSW m=16 / ef_construct=100, in-RAM, no quantization — about
**770 GB of originals** on a 1.5 TB host shared with production's page cache. The
question was "int8 or binary, and how much recall does it cost".

The finding that matters most came first and is high-confidence (3-0, four
sources): **Qdrant quantization never shrinks the data. It adds a compressed copy
alongside the originals.** RAM drops *only* if the originals are demoted to the
cold/memory-mapped tier and the quantized copy is pinned — at which point every
query that rescores touches disk for its top candidates. So the real decision is
not "how much recall does int8 cost" but **"are we willing to rescore from local
NVMe on every query"**. Here that is a much easier question: `/rag` and `/scout`
are local ext4 on NVMe, not network storage.

## RAM arithmetic for our collection

From Qdrant's capacity-planning formulas (`dense = points × dims × bytes`,
`hnsw = points × m × 2 × 4 B × 1.2`, id tracker ≈ 52 B/point, +20% headroom).
Our arithmetic, their formulas:

| configuration | RAM | what every query does |
|---|---|---|
| today: float32 originals in RAM | **~770 GB** + 7 GB HNSW | pure in-memory |
| int8 pinned **+ originals cached** ("fastest mode") | **~960 GB** — worse | in-memory, faster distance math |
| int8 pinned, originals **cold** | **~240 GB** | HNSW over int8 in RAM; rescore top-k from NVMe |
| binary pinned, originals cold | **~40 GB** | same, with 32× smaller working set |
| TurboQuant 4-bit pinned, originals cold (1.18+) | ~120 GB | same |

The "fastest mode" row is the trap: enabling quantization without demoting the
originals makes memory *worse*. The win is the third row — **~530 GB freed**, and
the HNSW traversal's working set shrinks from 770 GB to 192 GB, which stays hot
in cache far more reliably than the current footprint does.

## What the measurements actually say

### Scalar int8 — medium confidence (3-0), but low-dim only

- Qdrant's own benchmarks (384-d and 960-d, ef 128–512): precision change
  **−0.3% to +0.11%**, latency **−28% to −61%**.
- RAM-starved experiment (2 GB, slow network disk): int8 + rescore took
  throughput **2 → 30 RPS at precision 0.989 vs 0.990**; int8 without rescore
  1,200 RPS at 0.974.
- Independent 2025 study (arXiv 2505.00105, 384/768-d, MTEB, **no rescoring**):
  int8 costs **1.5–3.5% nDCG@10**.
- Reconciliation: the vendor "<1%" figures are low-dim, and the tables do not
  state whether rescoring was on. The independent figure is un-rescored.
- **No int8 measurement exists at ≥1024-d in any source.**

### Binary — medium confidence; the only ≥4096-d datapoint anywhere

- Qdrant: Cohere embed-english-v2.0 (**4096-d**, Wikipedia) **0.98 recall@50 at
  2× oversampling** — the single high-dimensional number in the literature, and
  its dataset size, HNSW params and rescore setting are **undocumented**.
- At 3× oversampling with rescore: 0.9966 (3072-d), ~0.984 (1536-d); but only
  0.956 (768-d) and 0.944 (1024-d Mistral Embed). Binary "exploits the
  over-parameterization of embedding" and is explicitly expected to be poor
  under 1024-d and to require a **centered component distribution** — whether
  SFR's components are centered is unverified.
- Independent, un-rescored: binary loses **7% to >11% nDCG@10** (vs 1.5–3.5%
  for int8). Rescoring is not optional for binary; Qdrant's recommended
  production config is binary pinned, originals on disk, **oversampling ≥ 2,
  rescore = true**.
- The "up to 40×" speedup is a headline with no latency table behind it.

### Product quantization — not indicated

Up to 64× compression but distance math is not SIMD-friendly (slower than
scalar), and Qdrant's own 384-d table degrades steeply: precision 0.984 →
0.968 (4×) → 0.914 (8×) → 0.807 (16×) → 0.662 (32×). int8 already gives 4× at
far smaller loss; TurboQuant (1.18+) supersedes PQ's extreme-compression niche.
No high-dimensional PQ data exists.

### HNSW `m` interacts strongly with quantized recall — medium confidence

Qdrant's 400M × 512-d LAION binary deployment: raising **m from 6 to 16 lifted
precision@50 from 75.2% to 85%** at fixed rescore limit, versus 81.0% from
raising the rescore limit 1000 → 5000 at **3× the latency** (0.7 s → 2.2 s).
Graph density recovered more recall than oversampling in that setup. We are at
m=16; **m=32 is the lever if quantized recall comes up short** (+7 GB HNSW RAM).

### A critical asymmetry in defaults — high confidence

**Rescoring is on by default only for binary and TurboQuant; it is OFF by default
for scalar and PQ.** A naive int8-vs-binary comparison is therefore un-rescored
int8 vs rescored binary. Set `rescore=true` explicitly for int8 in any test.

## The gap nobody has filled

**There is no published quantization measurement for SFR-Embedding-Mistral or any
Mistral-7B-derived 4096-d embedding.** The model card has no precision, dtype,
or quantization guidance ("More technical details will be updated later").
Qdrant's TurboQuant benchmarks stop at 2048-d. The nearest proxies are the
Cohere 4096-d binary point and OpenAI 3072-d. **Whatever we decide will rest on
a measurement we make ourselves.**

## Two findings on *how* to measure — high confidence

1. **Mean recall@k hides tail failures.** Two indexes at identical mean
   Recall@10 = 0.90 on MSMARCO differed in Robustness-0.1@10 — one had 4.8% of
   queries at *zero* recall. Report **Robustness-δ@K** (fraction of queries with
   per-query recall ≥ δ) alongside the mean (arXiv 2507.00379).
2. **Traditional recall@k *understates* a quantized high-dim index.** Quantization
   error mostly reorders near-equidistant *irrelevant* neighbours; "semantic
   recall" over relevant neighbours only read 0.932 vs traditional 0.863 on 3072-d
   embeddings with an int8 index (SIGIR 2026, arXiv 2604.20417). With a
   cross-encoder downstream, **candidate-set overlap before the reranker** and
   end-to-end answer quality are the metrics that matter, not recall@10 alone.

## Recommended protocol

1. **Ground truth:** exact search (`exact=true`) over the float32 collection for
   **≥1,000 sampled production queries**, at k = the depth fed to the reranker
   (50 — our `rerank_candidates`). 100 queries is too coarse.
2. **Enable int8 in place on a snapshot clone** (or the live collection —
   quantization is additive and enablement is a `PATCH`; originals are always
   retained, so rollback is "disable quantization, re-promote originals"). Pin
   the quantized copy, demote originals to cold. Accept the temporary
   double-footprint and re-optimization time for 47M points.
3. **Sweep** oversampling {1, 1.5, 2, 3, 4} × rescore {true, false} × ef
   {64, 128, 256}, **with rescore explicitly on for int8**.
4. **Report** mean recall@50, Robustness-0.8@50, fraction at zero, candidate-set
   overlap vs float32 *before* the reranker, and end-to-end answer agreement
   *after* it. p50/p95/p99 latency with originals cold.
5. **If short on recall:** try m=32 before reaching for more oversampling.

## Decision rule

- **int8 + rescore, originals cold** is the conservative first step: ~530 GB
  freed, in-place, reversible, and the literature's worst case (un-rescored,
  low-dim) is 3.5% — rescored and high-dim should be well inside that.
- **Binary** only if RAM must fall below ~250 GB *and* the ≥1,000-query test
  shows Robustness-0.8@50 within tolerance. Not before.
- **PQ:** no.
- **Build quantized for the next collection only** if the in-place
  re-optimization of 47M points (double footprint, hours of optimizer time on
  the shared instance) is unacceptable during production hours — it is a
  scheduling question, not a correctness one.

## Open questions the research could not close

- int8 and binary recall on SFR-Embedding-Mistral specifically; whether its
  components are centered.
- TurboQuant 1/1.5/2/4-bit at 4096-d (Qdrant stops benchmarking at 2048-d).
- How much candidate-recall loss the cross-encoder absorbs end-to-end — i.e.
  the recall@50 threshold that actually moves answer quality for this pipeline.
- p95/p99 and re-optimization cost of in-place enablement on this host's NVMe.

## Caveats, stated plainly

Nearly every recall/latency number is Qdrant's self-reported benchmark, often on
100 queries, small corpora, and ≤1536-d, with hardware/HNSW/rescore settings
undocumented in several tables. The two independent papers do not test Qdrant's
implementations. Qdrant 1.19 renamed `always_ram`/`on_disk` to memory tiers
(pinned/cached/cold) and since 1.18 recommends TurboQuant over SQ/BQ, so the
int8-vs-binary framing is partly dated. Two claims were refuted and excluded:
that Qdrant restricts PQ to low-RAM deployments, and that binary rescoring "only
partially recovers" loss in the LAION case.

## Sources

Primary: Qdrant docs (quantization, capacity-planning, memory-tiers,
large-scale-search tutorial), Qdrant articles (scalar-, binary-, product-
quantization, binary-quantization-openai), SFR-Embedding-Mistral model card,
arXiv 2505.00105, 2507.00379, 2604.20417, 2507.21989, 2605.24297, 2606.22778,
HF embedding-quantization post, Elastic search-labs recall-vs-quantization.
