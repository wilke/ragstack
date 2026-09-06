# Pre-registration — step 3: real dense-pipeline chunking contrast on the CDS pilot set

Written 2026-09-04, **before any embedding call**. Purpose: the step-2 BM25 ablation is a
*proxy* whose conclusion ("chunking configs will not separate on Leg A") was never tested
against the actual experiment it vetoes. This run tests it directly, on the real embedder
(SFR-Embedding-Mistral, endpoints :9001–:9006) and the real reranker (:50052), on the same
4,053-doc / 10-topic pilot set, same qrels, same metrics.

## Design

- **Corpus:** the step-2 pilot set unchanged — 4,053 fetched CDS judged docs (incl. grade-0
  hard negatives), text = `title \n\n abstract \n\n body`, re-parsed from `step2/xml/` with
  `ragstack.ingestion.jats.article_prose` (same code path).
- **Configs (repo `FixedTokenWindowChunker`, SFR tokenizer):**
  - `tok256_ov32` — 256 tokens, overlap 32 (12.5%) — the small extreme
  - `tok512_ov64` — 512 tokens, overlap 64 (12.5%) — the shipping default (prod = fixed_token 512/64)
  - `tok2048_ov256` — 2048 tokens, overlap 256 (12.5%) — the large extreme
  - `lead512` — chunk 0 of `tok512_ov64` only (identical to step 2's 512/0 chunk 0: overlap
    shifts only later chunks) — the dense transfer test of step 2's check 3
  - optional if fleet throughput allows: `whole4096` — one embedding per doc, text truncated
    at 4096 tokens — the dense analog of step 2's whole-doc control (first to drop)
  Deviation noted: plan §8 check 4 names `fixed_tok256/0` vs `fixed_tok2048/0` (zero
  overlap); we use the brief's 12.5%-overlap extremes + shipping control. Second-order.
  Also noted: the brief's `STAGE1_CONFIGS` with `fixed_tok256_ov12_5pct` etc. does **not**
  exist in repo main's `chunking_compare_7way.py` (grepped); config names here are ours.
- **Retrieval:** exact brute-force cosine over all chunk embeddings (numpy, fp16 storage,
  fp32 math). **No vector store is written — nothing touches Qdrant or ES at all.** Doc
  score = max over its chunks (the pipeline's rollup). Top-200 docs per query.
- **Queries:** the 10 topics' `summary` (primary, matching step 2) and `description`
  (sensitivity — the brief's "cheapest empirical contest"). Embedded raw, no instruction
  prefix, matching `python/scripts/search.py` prod convention.
- **Rerank arm (secondary/descriptive at n=10):** for each query and config, top-100 docs'
  best chunks through `POST :50052/rerank`; re-rank docs by CE score; report nDCG@10, MRR@10.
- **Metrics:** recall@100, nDCG@10, MRR@10, recall@10; grade≥1 primary, grade≥2 secondary;
  denominators = qrels restricted to indexed docs (identical across configs). Paired
  per-topic bootstrap (10k resamples) for the named contrasts.
- **Check-4 instrument (plan §8, never previously run):** fraction of the 10 queries whose
  top-10 *doc* set changes between tok256 and tok2048 (bar: ≥25% i.e. ≥3/10), and
  non-degeneracy of per-topic paired deltas (σ_d > 0).

## Pre-registered predictions (what step 2's demotion logic implies)

If the BM25 ablation is a good proxy for "Leg A cannot separate chunking configs":

- **P1 — configs do not separate.** All pairwise contrasts among {tok256, tok512, tok2048}
  on dense retrieval have |Δ nDCG@10| < 0.05 and |Δ recall@100| < 0.05 (point estimate,
  grade≥1, summary queries), and no pairwise CI excludes zero.
- **P2 — check 4 fails.** < 25% of queries (i.e. ≤2 of 10) change their top-10 doc set
  between tok256 and tok2048. (Step 2's argument, taken at face value, implies the config
  contrast is not live.)
- **P3 — the lead/full null transfers to dense.** full(tok512) − lead512 gaps stay under
  the same gates: recall@100 gap < 0.15 and nDCG@10 gap < 0.10. If dense full−lead clears
  0.15 recall@100, check 3's verdict does not transfer to the real retriever and the FAIL
  call was BM25-specific.
- **P4 — reranking changes nothing.** Reranked pairwise config contrasts also < 0.05
  nDCG@10; the reranker (which reads passages) does not open a separation the doc-level
  qrels then record.
- **Ordering prediction:** step 2's own mechanism (whole > full under max-rollup,
  +0.064 recall@100) implies coarser granularity wins recall@100: tok2048 ≥ tok256. A
  *reversed* ordering at ≥0.05 also counts against the proxy.

## Falsification bar (X), stated before the run

**X = 0.05 absolute** on nDCG@10 or recall@100 for any pairwise config contrast, with
either a paired-bootstrap CI excluding zero or ≥7/10 per-topic sign consistency.
Justification: (a) 0.05 is the plan §6 power table's middle δ tier — the edge of what the
full 90-topic Leg A can resolve (n≈95 at σ_d=0.15, 90% power) — so separation ≥0.05 on the
pilot means the full Leg A would be genuinely informative for config ranking, and the
demotion to "sign-check only" was wrong; (b) 0.05 ≈ the whole−lead effect (+0.059) that
step 2 itself measured and deemed sub-bar, so it is a scale this pilot demonstrably can
detect at n=10 when 9/10 topics agree.

**Asymmetry, stated in advance:** at n=10, separation ≥ X falsifies the demotion; small
separation only *weakly* confirms it — in that case the demotion stands on
power-and-instrument grounds ("Leg A cannot resolve δ<0.05 anyway"), not on the structural
claim that document-level qrels are blind to chunking.

## Budget & safety

~4,053 docs ≈ 28M tokens/config × 3 (+18M optional whole4096) ≈ 90–110M embed tokens,
~20–40 min on 6 endpoints at the quoted ~80k tok/s ±2×; ≤2 in-flight requests per endpoint
(politeness). Reranker: ≤ 100 docs × 10 queries × 2 variants × 4 arms ≈ 8k pairs ≈ 15 s.
Timebox 2 h of fleet time: overruns drop whole4096 first, then the description variant's
rerank arm. No store writes anywhere; all outputs under `scratchpad/phase0/step3/`.
