# OA chunking evaluation: arms, metrics, pass rule

STATUS: DRAFT 2026-10-28. Not frozen. Freeze = owner says "freeze", then this file is
committed unchanged before the first scored run; later edits go in a dated addendum.

Private material (passages, questions, gold quotes, answers, ledgers with text):
wilke/ragstack-experiments (private), oa-chunking-eval-2026-10/. Only hashes, IDs and
aggregates are committed here.

## Question
Which chunking config should be the default for the OA (bacteria/virus) re-ingest?
Not decided by the 2026-10-23 trial (one software-docs collection, exploratory).

## Corpus and sample
- Source: the OA corpus manifest (PMC OA), tight bacteria/virus definition.
- Strata: journal family (normalised names), length band, year, section type, plus a
  short-article stratum. Report every metric per stratum, not only pooled.
- Sample size and seed: set at freeze. Seed recorded here.
- Journal-confusion check: do retrieved wrong chunks come from the same journal family?

## Arms (each its own dev collection; dev cap is 20)
1. fixed_token 512/64 (live baseline)   2. fixed_token 512/0   3. fixed_token 1024/0
4. sentence, 512 tokens                 5. semantic_pooled, cap 512
6. legacy semantic, cap 512 (costs ~4.5-7x embed tokens; workflow acceptance unproven)
Waiting on #828: section-bounded 512, section-bounded 1024, random-boundary control.
Embedder for all arms: SFR-Embedding-Mistral 4096-d. Same reranker settings.

## Questions
- Writer: Qwen3.8 (shared endpoint), 10 samples/passage, temp 0.8, thinking off;
  second-family check set from GPT-5.6 Sol. Qwen3.6 (dedicated endpoint) allowed as extra
  sampler, not an independent grader of Qwen3.8 questions.
- Programmatic selection, no model judging: quote is an exact substring; question 25-400
  chars, quote 10-300; no copied 4-word run; self-contained regex. Lowest-echo pick
  among passers. Passages with no passer are logged by topic (skip log), not dropped silently.
- Gold-label audit: does the quote also occur elsewhere or in duplicate articles (#799)?
  Items with ambiguous gold are flagged and scored separately.
- Owner blind rating of 30 items before the main writer is fixed.

## Metrics
Primary: hit@k (k = 1, 5, 10) on gold-quote span coverage (retrieved chunk contains the
quote), and context tokens per query. Secondary: LLM-graded answer correctness on a
subset (Sonnet 5.5 grader, Sol second; Scout local; Opus not a primary grader).
Report paired differences with bootstrap 95% CI over questions, per stratum and pooled.

## Pass rule (structure decided 2026-10-28; numbers set from a noise run)
Baseline = fixed_token 512/64. Arms fall in cost classes; each class has its own test.
All tests are paired over questions, bootstrap 95% CI, on hit@5 unless stated.

| Class | Arms | Test |
|---|---|---|
| Cheap | fixed 512/0, sentence 512 | Non-inferiority: lower CI bound of (arm - baseline) >= -M_ni |
| Mid | fixed 1024/0, semantic_pooled cap 512 | Superiority: lower CI bound >= +M_mid |
| Expensive | legacy semantic cap 512 | Superiority: lower CI bound >= +M_high, plus workflow feasibility |

Guards that apply to every arm:
- Stratum guard: no stratum loses more than 2.0 points (upper-side CI), short-article
  stratum included.
- Context tokens per query reported; > 1.5x baseline needs the next class's margin.
- Embed cost and GoWe path feasibility stated (semantic arms need pdf-ingest-scatter or
  ingest-bulk; jats-ingest.cwl does not accept them).
- Nothing clears its test: keep fixed_token 512/64. Ties go to the cheaper arm.

Numbers (M_ni, M_mid, M_high): NOT YET SET. Procedure, fixed before the main run:
1. Run the baseline arm on two disjoint question subsets (and bootstrap the full set) to
   measure noise and the paired discordance rate between two arms.
2. Pick the sample size N from the budget so the paired 95% CI half-width is <= H.
3. Set M_ni = max(1.0, noise floor); M_mid = max(2.0, CI half-width at N);
   M_high = 2 x M_mid. Record the values here as a dated addendum, then freeze.
Working placeholders for planning only: M_ni 1.0, M_mid 2.0, M_high 4.0.

## Operating rules
- Gateway: <=4 in flight, stream false, always max_tokens, notice to OpenChia before
  and totals after each batch, stop on "pause".
- Checkpointed ledger; stop after >20 failures or 5 in a row.
- GPUs 6 and 7 reserved (6 = reranker). Thinking off on both Qwen servers.

## Addendum A, 2026-11-03 (APPROVED by the owner 2026-11-03; from the Opus plan review and the v1.6.22 ingest review)
Fixes (approved):
1. Non-inferiority margin: M_ni = max(2.0, paired CI half-width at N). With M_ni 1.0 and a
   half-width of 1.4-2.4 points at N=2000, a cheap arm would have to be better to pass.
2. Metric: a quote cut by a chunk boundary must not count as a miss. Primary hit@k becomes
   "the union of the top-k retrieved chunks covers >= 90% of the gold quote's characters";
   strict single-chunk containment is reported as secondary. Context tokens per query stay
   reported, so large chunks pay for their size.
3. Noise run uses TWO arms (baseline 512/64 and fixed 512/0) on ~300 questions, plus a
   split-half of the baseline. The discordance rate needs a second arm.
4. Stratum guard: computed on the pooled strata actually reported (journal family, length
   band), not their cross product; an arm fails the guard if any reported stratum's point
   estimate is below -2.0 AND its CI upper bound is below 0. Holm correction across the
   challenger arms. Tie-break between passing arms: cheaper ingest, then fewer context tokens.
5. Skip bias: passages with no passing question are reported by stratum; results are given
   with and without the strata that lose more than 10% of passages.
6. N = 2000 is a planning assumption until the noise run; the state sheet that said
   "decided" was wrong.

Ingest facts checked 2026-11-03 at v1.6.22 (dev's checkout):
- Only cwl/ingest-bulk.cwl accepts all six methods; jats-ingest.cwl lacks semantic and
  semantic_pooled. Plan: extract once with jats_extract.py, feed the same JSONL to all arms.
- jats_extract.py on 5 articles (tools v1.6.20 image): 40 records (5 article, 4 table,
  31 figure), 0 failures. Tables and figures are separate records, identical in all arms.
- ingest_shard.py uses a bare JsonlLoader: pmcid/pmid/journal/licence are dropped from chunk
  payloads; doc_id = hash of path (PMC123 / PMC123#table-2). The harness keeps its own
  doc_id -> pmcid map from the extract shard.
- No LLM calls (glossary_llm default off); no chunk cap (max_chunks 0; the CWL comment that
  a registry override applies is not implemented in ingest_shard.py).
- SFR max_model_len 4096 on all three SFR endpoints, so 1024-token chunks fit.
- Collections: chunk spec at create is admin-only; owner quota 5 (admins exempt). Sentence
  needs chunk_params size_unit tokens; semantic arms get size_cap tokens automatically.
- tenant defaults to "public" and is part of every point id: choose it before the first ingest.

## Owner decisions, 2026-11-03
- Arms: all six.
- Tenant: dev. Collection names: oa-eval-* (created with a dev admin service account).
- Corpus per arm: the eval sample plus about 20,000 background OA articles, identical in every arm.
- Addendum A approved. Freeze after the noise run (margins, N, seed recorded then).
- Harness: go (worktree, this branch). Branch is pushed for the record; eval text stays private.

## Owner decisions, 2026-11-04
- Scope: "bacteria/virus" = PubMed MeSH Major Topic Bacteria OR Viruses, open-access filter;
  171,994 PMCIDs present in the OA corpus (list kept privately).
- Sample: 2,600 eval articles (expected ~2,260 usable questions) + 20,000 background,
  stratified by journal family (8 families) x length band (tertiles); seed recorded privately.
  The manifest has no year field, so year is not a stratum. Smallest cell: 13 eval articles.
- Retrieval: run BOTH production settings (reranker on, graph off, hybrid) and vector-only,
  no reranker. Which one is primary is named before the freeze (proposal: production primary).
- Tie-break: cheapest first stands as the working rule, to be discussed before the freeze.
  Open point: a cheap arm passes by non-inferiority while a mid/expensive arm passes by
  proven superiority; options are (a) cheapest passing arm, (b) highest passing class,
  cheapest within it, (c) the costlier arm only if it beats the cheaper passing arm in a
  direct paired test. Fixed before any main-run result is seen.
- Stratum guard size: report all strata. The minimum stratum size for the guard is chosen
  at the freeze from the noise run (interval width vs stratum size), not from main-run
  results; strata below it are reported, marked "below cut-off".
- Grader endpoint note (2026-11-04): from about 2026-11-04 23:30 UTC until at most 2026-11-30,
  the local endpoint that served Llama-4-Scout answers with Qwen3.6-35B-A3B under Scout's
  model id (owner decision on the inference host). Rule: no grading call goes to that endpoint
  under the Scout id while the alias is in place; Scout counts as a grader only after the real
  model is confirmed back (dated notice). Every grading row records the model id the endpoint
  reported and the call time, so rows from the alias window can be found and excluded.
- Graders (owner, 2026-11-04): LLM grading runs on Argo models through the gateway; the local
  Scout endpoint is dropped from the grader list. Primary/second grader choice (incl. whether
  Opus may be primary, since no Claude model writes the answers) and the subset size are set
  before grading, with an inter-grader agreement check on a shared subset.

## Addendum B, 2026-11-05 UTC: noise run and freeze (owner decisions 2026-11-05 UTC)

Noise run (300 questions, seeded subset excluding ambiguous-gold and within-record repeats; baseline
fixed_token 512/64 vs fixed_token 512/0; primary metric hit@5; full collections, server v1.6.22):

| Retrieval setting | Baseline | 512/0 | Diff [95% CI] | Discordance | Half-width at planned N |
|---|---|---|---|---|---|
| a) hybrid + reranker, graph off | 81.0 | 78.7 | -2.3 [-5.7, +0.7] | 0.077 | 1.15 |
| b) vector only, no reranker | 48.3 | 48.3 | 0.0 [-4.7, +4.7] | 0.173 | 1.70 |
| c) BM25 (text) only, no reranker | 51.7 | 52.7 | +1.0 [-2.7, +4.7] | 0.110 | 1.34 |

The noise compares a cheap challenger by design, so the 512/0 result on these 300 questions was seen
before the freeze; no parameter was changed after seeing it (margins sit at the pre-set floor).

Frozen:
1. Margins (Addendum A rule; half-widths are below the 2.0 floor in every setting): M_ni = M_mid = 2.0,
   M_high = 4.0 points of hit@5, the same for all three settings.
2. Retrieval settings: all three are run on all arms. (a) production settings (rerank on, graph off,
   hybrid) is the PRIMARY decision; (b) vector-only and (c) BM25-only are secondary reports, and any
   disagreement with (a) is reported explicitly (owner: a possible reranker problem makes the extra
   signals useful). Setting (a) keeps the reranker on (owner 2026-11-05); no hybrid-without-
   reranker arm is run.
3. Decision set: all selected questions except ambiguous-gold items, within-record repeats (both
   scored separately, as HARNESS-SPEC already specifies; 102 questions, 27 carry both flags) AND the
   300 noise-subset questions: N = 2,035 (half-width at that N: 1.22 in (a), 1.79 in (b), 1.41 in
   (c)). A sensitivity result on the same clean set with the noise subset included (N = 2,335) is
   reported beside it and is not the decision. (Owner preferred keeping all, leaving exclusion to the
   analyst; exclusion chosen to keep margin-setting and testing data separate.) Correction before
   freeze: an earlier draft of this item kept within-record repeats; the harness and the noise
   selection exclude them, and that rule stands.
4. Stratum guard: strata = journal_family (8) and length_band (3); the smallest has 165 questions, so
   min_stratum_n (30) never binds. Journal x length cells are reported, not tested.
5. Stratum guard multiplicity: Bonferroni. Per arm, a stratum fails only if its point difference < -2.0
   AND the upper bound of its two-sided (1 - 0.05/k) paired bootstrap interval < 0, k = strata tested.
6. Tie-break: "paired ladder". Candidates = baseline + passing challengers ordered by cost_rank; start
   with the cheapest; a costlier candidate replaces the incumbent only if its paired difference over the
   incumbent has a 95% lower bound > 0. Context-token rule unchanged.
   cost_rank: fixed_token 512/0 (1) < fixed_token 512/64 baseline (2) < sentence 512 (3) <
   fixed_token 1024/0 (4) < semantic_pooled 512 (5) < legacy semantic 512 (6) [class order, then
   stored chunks / ingest compute within class; owner confirmed 2026-11-05].
7. Legacy semantic arm: workflow-feasible (ingests through the standard bulk workflow); its cost
   counts through M_high.
Harness changes for 3, 5, 6 are made and tested before any main-run retrieval; the commit that sets
the margins in the private config and these harness changes is the freeze point.
