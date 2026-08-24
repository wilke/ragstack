# G1 — library retrieval parameter sweep

Run `g1-20260824T211215Z` · git `60482e375a1b`
(`eval/g1-pilot-200`) ·
started 2026-08-24T21:12:13.075376+00:00 · finished 2026-08-24T21:12:56.023418+00:00

Protocol `reports/g1-library-retrieval/PROTOCOL.md` @ `sha256:7139df210c5595d7243d9ffd682a8fdcb46938ce8b94011e2c5ce24b0a1b99fe`.
Dataset **huggingface datasets (BeIR/scifact)**, corpus
`e3818b7f7a383e71…`, qrels
`497669c59ea7ae70…`.
Build spec `fixed_tok512` /
`Salesforce/SFR-Embedding-Mistral` @ 4096-d,
spec_hash `2ae90d23`.

> ⛔ **NO RECOMMENDATION MAY BE EMITTED FROM THIS RUN.**
>
> The harness refuses to derive a `LibraryRetrievalDefaults` block (protocol §11) because this run is in a regime where the claim is not measurable. Decision rung `n200`, scale regime `brute_force`. Blocking conditions:
>
> 1. HNSW was never built at the decision rung `n200` (indexed_vectors=0 below Qdrant's indexing_threshold): the dense leg ran as an exact brute-force scan, so H1b is vacuous, the depth parameters under test do not bind, and nDCG saturates where δ=0.02 sits inside the ceiling
>
> Numbers below remain valid as *descriptions of this run*. None of them may be used to change a shipping default.

## Designated primary comparison

**Not pre-registered.** This specific pair was chosen when the harness was
written, after the protocol was hashed; PROTOCOL.md amendment A4 records it as a
*designated* primary. It carries the weight of a single pre-specified
comparison — one test, one δ, no selection over the grid — but it does **not**
carry the weight of pre-registration, and it is labelled that way everywhere it
appears.

Shipping defaults (per-leg depth D=10, realizable as
`tk1xm10 | tk5xm2 | tk10xm1`) vs. the identical
cell at D=100 (`tk1xm100 | tk5xm20 | tk10xm10 | tk20xm5`),
document-level ndcg@10, largest library size in the run, on the
**held-out confirm split**.

- reference (`n200_hybrid_rrf60_d10_rr0`, D=10): 0.8929
- candidate (`n200_hybrid_rrf60_d100_rr0`, D=100): 0.9144
- Δndcg@10 = +0.0215 [+0.0000, +0.0645] (95% paired bootstrap, confirm split, n=14, 1 discriminating)
- Δndcg@5_chunk 90% CI lower bound = +0.0000 → co-primary not worse
- Wilcoxon p = 1.0000, δ = 0.02
- **verdict: INCONCLUSIVE**
- verdict reason: only 1 of the paired per-query differences are non-zero (floor 5); the difference distribution is degenerate, so the equivalence interval carries no information

## Query split (protocol §6.4)

40% tune / 60% confirm, stratified by
per-query ndcg@10 difficulty quintiles under the shipping default,
seed 0. n_tune=10,
n_confirm=14, fixture
`/home/wilke/Development/worktrees/ragstack-200/reports/g1-library-retrieval/fixtures/g1_scifact_split.json` @ `sha256:98a38e29be43552465fc76246936acf1f7b438dc61ad1af44432025a152bb28d`
(derived).

## A/A resolution gate (protocol §6.4)

3 replicate(s) of `n200_hybrid_rrf60_d10_rr0`, ndcg@10 = 0.9526, 0.9526, 0.9526.

- A/A SD = **0.00000**; δ = 0.02 must exceed 3.0×SD = 0.00000 → PASS
- mean RBO@20 between consecutive replicates = 1.0000

## Stage 2 — confirmatory (protocol §7.2)

**Shortlist** (protocol §7.2, applied mechanically to the stage-1 output):

- `n200_hybrid_rrf60_d10_rr0` — (1) the shipping default — the reference; (3) highest mean ndcg@10 at the largest rung `n200`
- `n50_hybrid_rrf60_d100_rr0` — (2) highest mean ndcg@10 at the smallest rung `n50`
- `n50_vector_rrfna_d10_rr0` — (4) best dense-only cell

Holm–Bonferroni at α=0.05 over exactly these 2 comparison(s), **one family across the grid**, on the held-out confirm split.

| candidate | Δndcg@10 [95% CI] | Holm adj p | Δndcg@5_chunk 90% lo | co-primary not worse | verdict | recommend |
|---|---|---|---|---|---|---|
| `n50_hybrid_rrf60_d100_rr0` | +0.0000 [+0.0000, +0.0000] | 1.0000 | +0.0000 | yes | INCONCLUSIVE | no |
| `n50_vector_rrfna_d10_rr0` | +0.0000 [+0.0000, +0.0000] | 1.0000 | +0.0000 | yes | INCONCLUSIVE | no |

## Libraries built

| rung | docs | chunks | chunks/doc | queries | judged docs | collection |
|---|---|---|---|---|---|---|
| `n50` | 50 | 66 | 1.32 | 24 | 25 | `g1_lib_50docs_2ae90d23_d366c3d06bd5` |
| `n100` | 100 | 125 | 1.25 | 46 | 49 | `g1_lib_100docs_2ae90d23_6f596c8a940c` |
| `n200` | 200 | 251 | 1.25 | 90 | 100 | `g1_lib_200docs_2ae90d23_b867dbb312f3` |


> **Scale caveats — read before quoting any number above.**
>
> - Measured **1.25 chunks/doc** on this corpus. A real `fixed_tok512` PDF library measures ~36 chunks/doc, so the largest index here (251 chunks) corresponds to roughly **7 real documents**, not 200. The document-count sweep is a *prior* on the parameters, not a library-scale measurement; the distractor ladder (protocol §4.3) is what makes the chunk count realistic.
> - **HNSW was never built** at rung(s) n100, n200, n50 (points below Qdrant's `indexing_threshold`), so the dense leg ran as an exact brute-force scan. Every §5.4 dense verdict at those rungs is therefore vacuous with respect to approximate-search truncation — it confirms only that exact search is exact.

## Mechanism (Track C) — per-leg accounting

Registered prediction (protocol §1.3c / H1b): the spec's "BM25 returns 3-4 hits
against dense's 20" premise is backwards. `ElasticsearchTextIndex.search` is an
exact `size=D` query returning `min(D, |chunks sharing a term|)`;
`QdrantVectorStore.search` passes `limit` with **no `search_params`** (no
`hnsw_ef`, no `exact`), so the *dense* leg is the one that can silently
under-return.

Two different quantities, kept apart on purpose. **starved** = the leg returned
fewer than `D` for any reason, including "the index simply has no more matching
chunks" — that is the H1b measurement and it is not a bug. **deficit** = the leg
returned fewer than `min(D, matchable)`, i.e. fewer than it could have — that
*is* a bug and it voids the cell.

| cell | D | mean dense_hits | mean bm25_hits | mean bm25_matchable | union | overlap | dense starved | bm25 starved | dense deficit | bm25 deficit | §5.4 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `n50_bm25_rrfna_d100_rr0` | 100 | 0.0 | 59.5 | 59.5 | 59.5 | 0.0 | 0.000 | 1.000 | 0.000 | 0.000 | PASS |
| `n50_bm25_rrfna_d100_rr50` | 100 | 0.0 | 59.5 | 59.5 | 59.5 | 0.0 | 0.000 | 1.000 | 0.000 | 0.000 | PASS |
| `n50_bm25_rrfna_d10_rr0` | 10 | 0.0 | 10.0 | 59.5 | 10.0 | 0.0 | 0.000 | 0.000 | 0.000 | 0.000 | PASS |
| `n50_hybrid_rrf60_d100_rr0` | 100 | 66.0 | 59.5 | 59.5 | 66.0 | 59.5 | 1.000 | 1.000 | 0.000 | 0.000 | PASS |
| `n50_hybrid_rrf60_d100_rr50` | 100 | 66.0 | 59.5 | 59.5 | 66.0 | 59.5 | 1.000 | 1.000 | 0.000 | 0.000 | PASS |
| `n50_hybrid_rrf60_d10_rr0` | 10 | 10.0 | 10.0 | 59.5 | 15.8 | 4.2 | 0.000 | 0.000 | 0.000 | 0.000 | PASS |
| `n50_vector_rrfna_d100_rr0` | 100 | 66.0 | 0.0 | 59.5 | 66.0 | 0.0 | 1.000 | 0.000 | 0.000 | 0.000 | PASS |
| `n50_vector_rrfna_d100_rr50` | 100 | 66.0 | 0.0 | 59.5 | 66.0 | 0.0 | 1.000 | 0.000 | 0.000 | 0.000 | PASS |
| `n50_vector_rrfna_d10_rr0` | 10 | 10.0 | 0.0 | 59.5 | 10.0 | 0.0 | 0.000 | 0.000 | 0.000 | 0.000 | PASS |
| `n100_bm25_rrfna_d100_rr0` | 100 | 0.0 | 96.0 | 117.5 | 96.0 | 0.0 | 0.000 | 0.065 | 0.000 | 0.000 | PASS |
| `n100_bm25_rrfna_d100_rr50` | 100 | 0.0 | 96.0 | 117.5 | 96.0 | 0.0 | 0.000 | 0.065 | 0.000 | 0.000 | PASS |
| `n100_bm25_rrfna_d10_rr0` | 10 | 0.0 | 10.0 | 117.5 | 10.0 | 0.0 | 0.000 | 0.000 | 0.000 | 0.000 | PASS |
| `n100_hybrid_rrf60_d100_rr0` | 100 | 100.0 | 96.0 | 117.5 | 117.1 | 78.9 | 0.000 | 0.065 | 0.000 | 0.000 | PASS |
| `n100_hybrid_rrf60_d100_rr50` | 100 | 100.0 | 96.0 | 117.5 | 117.1 | 78.9 | 0.000 | 0.065 | 0.000 | 0.000 | PASS |
| `n100_hybrid_rrf60_d10_rr0` | 10 | 10.0 | 10.0 | 117.5 | 16.6 | 3.4 | 0.000 | 0.000 | 0.000 | 0.000 | PASS |
| `n100_vector_rrfna_d100_rr0` | 100 | 100.0 | 0.0 | 117.5 | 100.0 | 0.0 | 0.000 | 0.000 | 0.000 | 0.000 | PASS |
| `n100_vector_rrfna_d100_rr50` | 100 | 100.0 | 0.0 | 117.5 | 100.0 | 0.0 | 0.000 | 0.000 | 0.000 | 0.000 | PASS |
| `n100_vector_rrfna_d10_rr0` | 10 | 10.0 | 0.0 | 117.5 | 10.0 | 0.0 | 0.000 | 0.000 | 0.000 | 0.000 | PASS |
| `n200_bm25_rrfna_d100_rr0` | 100 | 0.0 | 96.1 | 224.4 | 96.1 | 0.0 | 0.000 | 0.089 | 0.000 | 0.000 | PASS |
| `n200_bm25_rrfna_d100_rr50` | 100 | 0.0 | 96.1 | 224.4 | 96.1 | 0.0 | 0.000 | 0.089 | 0.000 | 0.000 | PASS |
| `n200_bm25_rrfna_d10_rr0` | 10 | 0.0 | 10.0 | 224.4 | 10.0 | 0.0 | 0.000 | 0.000 | 0.000 | 0.000 | PASS |
| `n200_hybrid_rrf60_d100_rr0` | 100 | 100.0 | 96.1 | 224.4 | 146.2 | 49.9 | 0.000 | 0.089 | 0.000 | 0.000 | PASS |
| `n200_hybrid_rrf60_d100_rr50` | 100 | 100.0 | 96.1 | 224.4 | 146.2 | 49.9 | 0.000 | 0.089 | 0.000 | 0.000 | PASS |
| `n200_hybrid_rrf60_d10_rr0` | 10 | 10.0 | 10.0 | 224.4 | 16.5 | 3.5 | 0.000 | 0.000 | 0.000 | 0.000 | PASS |
| `n200_vector_rrfna_d100_rr0` | 100 | 100.0 | 0.0 | 224.4 | 100.0 | 0.0 | 0.000 | 0.000 | 0.000 | 0.000 | PASS |
| `n200_vector_rrfna_d100_rr50` | 100 | 100.0 | 0.0 | 224.4 | 100.0 | 0.0 | 0.000 | 0.000 | 0.000 | 0.000 | PASS |
| `n200_vector_rrfna_d10_rr0` | 10 | 10.0 | 0.0 | 224.4 | 10.0 | 0.0 | 0.000 | 0.000 | 0.000 | 0.000 | PASS |

## Stage 1 — exploratory screen by rung (NOT a result)

Benjamini–Hochberg FDR at q=0.1 on the **tune** split only, per protocol
§7.2. Nothing in this section may change a shipping default; only the stage-2
table above can, and only when the recommendation gate is open.

### Rung `n50`

| config | ndcg@10 | ndcg@5_chunk | recall@10 | map | Δndcg@10 vs n50_hybrid_rrf60_d10_rr0 | Wilcoxon p (Holm) | distinguishable |
|---|---|---|---|---|---|---|---|
| `n50_bm25_rrfna_d100_rr0` | 0.926 [0.815, 1.000] | 0.926 [0.815, 1.000] | 1.000 [1.000, 1.000] | 0.900 [0.750, 1.000] | -0.074 [-0.185, 0.000] | 1.000 | no |
| `n50_bm25_rrfna_d100_rr50` | 0.950 [0.850, 1.000] | 0.950 [0.850, 1.000] | 1.000 [1.000, 1.000] | 0.933 [0.800, 1.000] | -0.050 [-0.150, 0.000] | 1.000 | no |
| `n50_bm25_rrfna_d10_rr0` | 0.926 [0.815, 1.000] | 0.926 [0.815, 1.000] | 1.000 [1.000, 1.000] | 0.900 [0.750, 1.000] | -0.074 [-0.185, 0.000] | 1.000 | no |
| `n50_hybrid_rrf60_d100_rr0` | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 1.000 | no |
| `n50_hybrid_rrf60_d100_rr50` | 0.950 [0.850, 1.000] | 0.950 [0.850, 1.000] | 1.000 [1.000, 1.000] | 0.933 [0.800, 1.000] | -0.050 [-0.150, 0.000] | 1.000 | no |
| `n50_hybrid_rrf60_d10_rr0` | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | — (ref) | — | ref |
| `n50_vector_rrfna_d100_rr0` | 0.939 [0.816, 1.000] | 0.939 [0.816, 1.000] | 1.000 [1.000, 1.000] | 0.920 [0.760, 1.000] | -0.061 [-0.184, 0.000] | 1.000 | no |
| `n50_vector_rrfna_d100_rr50` | 0.950 [0.850, 1.000] | 0.950 [0.850, 1.000] | 1.000 [1.000, 1.000] | 0.933 [0.800, 1.000] | -0.050 [-0.150, 0.000] | 1.000 | no |
| `n50_vector_rrfna_d10_rr0` | 0.939 [0.816, 1.000] | 0.939 [0.816, 1.000] | 1.000 [1.000, 1.000] | 0.920 [0.760, 1.000] | -0.061 [-0.184, 0.000] | 1.000 | no |

**Stage-1 screen (exploratory, NOT a result).** Tune split, n=10. Benjamini–Hochberg FDR at q=0.1: 0 of 8 cell(s) flagged for stage 2. No cell here may change a shipping default; only the stage-2 confirm-split table below can.
### Rung `n100`

| config | ndcg@10 | ndcg@5_chunk | recall@10 | map | Δndcg@10 vs n100_hybrid_rrf60_d10_rr0 | Wilcoxon p (Holm) | distinguishable |
|---|---|---|---|---|---|---|---|
| `n100_bm25_rrfna_d100_rr0` | 0.889 [0.727, 1.000] | 0.889 [0.727, 1.000] | 1.000 [1.000, 1.000] | 0.853 [0.640, 1.000] | -0.024 [-0.223, 0.174] | 1.000 | no |
| `n100_bm25_rrfna_d100_rr50` | 0.950 [0.850, 1.000] | 0.950 [0.850, 1.000] | 1.000 [1.000, 1.000] | 0.933 [0.800, 1.000] | 0.037 [-0.113, 0.187] | 1.000 | no |
| `n100_bm25_rrfna_d10_rr0` | 0.889 [0.727, 1.000] | 0.889 [0.727, 1.000] | 1.000 [1.000, 1.000] | 0.853 [0.640, 1.000] | -0.024 [-0.223, 0.174] | 1.000 | no |
| `n100_hybrid_rrf60_d100_rr0` | 0.926 [0.815, 1.000] | 0.926 [0.815, 1.000] | 1.000 [1.000, 1.000] | 0.900 [0.750, 1.000] | 0.013 [0.000, 0.039] | 1.000 | no |
| `n100_hybrid_rrf60_d100_rr50` | 0.963 [0.889, 1.000] | 0.963 [0.889, 1.000] | 1.000 [1.000, 1.000] | 0.950 [0.850, 1.000] | 0.050 [-0.074, 0.187] | 1.000 | no |
| `n100_hybrid_rrf60_d10_rr0` | 0.913 [0.789, 1.000] | 0.913 [0.789, 1.000] | 1.000 [1.000, 1.000] | 0.883 [0.717, 1.000] | — (ref) | — | ref |
| `n100_vector_rrfna_d100_rr0` | 0.863 [0.663, 1.000] | 0.863 [0.663, 1.000] | 0.900 [0.700, 1.000] | 0.858 [0.658, 1.000] | -0.050 [-0.150, 0.000] | 1.000 | no |
| `n100_vector_rrfna_d100_rr50` | 0.963 [0.889, 1.000] | 0.963 [0.889, 1.000] | 1.000 [1.000, 1.000] | 0.950 [0.850, 1.000] | 0.050 [-0.074, 0.187] | 1.000 | no |
| `n100_vector_rrfna_d10_rr0` | 0.863 [0.663, 1.000] | 0.863 [0.663, 1.000] | 0.900 [0.700, 1.000] | 0.850 [0.650, 1.000] | -0.050 [-0.150, 0.000] | 1.000 | no |

**Stage-1 screen (exploratory, NOT a result).** Tune split, n=10. Benjamini–Hochberg FDR at q=0.1: 0 of 8 cell(s) flagged for stage 2. No cell here may change a shipping default; only the stage-2 confirm-split table below can.
### Rung `n200`

| config | ndcg@10 | ndcg@5_chunk | recall@10 | map | Δndcg@10 vs n200_hybrid_rrf60_d10_rr0 | Wilcoxon p (Holm) | distinguishable |
|---|---|---|---|---|---|---|---|
| `n200_bm25_rrfna_d100_rr0` | 0.843 [0.629, 1.000] | 0.843 [0.629, 1.000] | 0.900 [0.700, 1.000] | 0.833 [0.590, 1.000] | -0.083 [-0.340, 0.148] | 1.000 | no |
| `n200_bm25_rrfna_d100_rr50` | 0.963 [0.889, 1.000] | 0.963 [0.889, 1.000] | 1.000 [1.000, 1.000] | 0.950 [0.850, 1.000] | 0.037 [-0.074, 0.148] | 1.000 | no |
| `n200_bm25_rrfna_d10_rr0` | 0.843 [0.629, 1.000] | 0.843 [0.629, 1.000] | 0.900 [0.700, 1.000] | 0.825 [0.575, 1.000] | -0.083 [-0.340, 0.148] | 1.000 | no |
| `n200_hybrid_rrf60_d100_rr0` | 0.963 [0.889, 1.000] | 0.963 [0.889, 1.000] | 1.000 [1.000, 1.000] | 0.950 [0.850, 1.000] | 0.037 [0.000, 0.111] | 1.000 | no |
| `n200_hybrid_rrf60_d100_rr50` | 0.963 [0.889, 1.000] | 0.963 [0.889, 1.000] | 1.000 [1.000, 1.000] | 0.950 [0.850, 1.000] | 0.037 [-0.074, 0.148] | 1.000 | no |
| `n200_hybrid_rrf60_d10_rr0` | 0.926 [0.815, 1.000] | 0.926 [0.815, 1.000] | 1.000 [1.000, 1.000] | 0.900 [0.750, 1.000] | — (ref) | — | ref |
| `n200_vector_rrfna_d100_rr0` | 0.863 [0.663, 1.000] | 0.863 [0.663, 1.000] | 0.900 [0.700, 1.000] | 0.856 [0.656, 1.000] | -0.063 [-0.189, 0.000] | 1.000 | no |
| `n200_vector_rrfna_d100_rr50` | 0.963 [0.889, 1.000] | 0.963 [0.889, 1.000] | 1.000 [1.000, 1.000] | 0.950 [0.850, 1.000] | 0.037 [-0.074, 0.148] | 1.000 | no |
| `n200_vector_rrfna_d10_rr0` | 0.863 [0.663, 1.000] | 0.863 [0.663, 1.000] | 0.900 [0.700, 1.000] | 0.850 [0.650, 1.000] | -0.063 [-0.189, 0.000] | 1.000 | no |

**Stage-1 screen (exploratory, NOT a result).** Tune split, n=10. Benjamini–Hochberg FDR at q=0.1: 0 of 8 cell(s) flagged for stage 2. No cell here may change a shipping default; only the stage-2 confirm-split table below can.

## Reproducing

Full `argv`, seeds, package versions, HNSW/ES index telemetry, reranker model +
revision, query-vector cache digest and per-library `CollectionManifest`s are in
`manifest.json`. Per-cell per-query arrays are in `cells/<cell_id>.json`
(`chunk_one`-compatible, so `scripts/eval/aggregate_stats.py` reads them);
per-query Track-C counters are in `raw/<cell_id>.counters.jsonl`; the top-200
ranking per query is in `raw/<cell_id>.rankings.jsonl`, from which every metric at
every k is recomputable offline.
