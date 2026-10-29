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
