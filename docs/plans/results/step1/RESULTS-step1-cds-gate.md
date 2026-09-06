# Phase-0 step 1 — CDS coverage gate: **PASS** (step-1 criteria) — reviewed & corrected

Measured 2026-09-04, independently re-measured under Fable review the same day.
No GoWe; local CPU + S3 fetches. Nothing written under /rag/; no store, no embedding endpoint.

## Verdict
- **Step-1 gate as pre-registered (§9: ≥60 topics, ≥70% fetchable): PASS**, by wide margins.
- **§8 check 1 (≥80% of judged docs >2,048 tokens): NOT YET CALLABLE** — see below. The
  first version of this file listed "documents are full-length … PASS" with no threshold,
  which papered over exactly that. Corrected.

## Corrected numbers (first-pass figures in parentheses where they moved)

| quantity | value | note |
|---|---|---|
| topics | **90** (30/year, all with ≥1 relevant, min 8) | unchanged |
| distinct relevant PMCIDs (grade≥1) | **12,307** | independently recounted, identical |
| (topic, relevant-doc) pairs | **13,807** | the correct denominator |
| relevant per topic | **median 109, mean 153.4** (~112 / ~137) | my mean divided by the wrong denominator, collapsing 861 cross-year duplicates |
| judged per topic | 1,265 / 1,260 / 1,257 | unchanged |
| fetchable, grade≥1 | **98.5%**, Wilson 95% CI 95.7–99.5 (100%) | n=200 properly seeded |
| fetchable, grade-0 hard negatives | **95%** (57/60) | not probed first pass; load-bearing for the keep-grade-0 design |
| body tokens, median | **4,097** (3,525) | n=197 random |
| **> 2,048 tokens** | **80.2%**, CI 74.1–85.2 (72.5%) | the number the decision rests on |
| > 1,024 tokens | 95.9% | |
| > 4,096 tokens | 50.3% | |
| abstract median | 307 (336) | |
| JATS parse | **237/237, zero failures, zero empty bodies** | the silent-empty mode did not trigger |
| local /rag/oa overlap | **1,229 / 12,307 = 10.0%** | held exactly |

**My sampling was biased.** `shuf --random-source=<(yes)` put 28 of 60 draws in one decile
of the ID space and zero in two others. Every corrected figure above comes from a properly
seeded draw. The bias understated document length — the real corpus is longer than I
reported, so the conclusion strengthens.

## The "~494 relevant/topic" discrepancy — resolved
494 is **TREC-COVID's** number, not CDS's: (14,217+10,456)/50 = 493.5 from the plan's own §3.
The plan's §6 claim that CDS topics are "similarly deep" is wrong by ~4×. The metric choice
survives unchanged — at median 109 relevant/topic recall@5 still caps at ~4.6%, so this leg
reports nDCG@10 / P@10 / MRR@10. Correct the plan text, not the metric map.

## Blocking traps for assembly
1. **All three years number topics 1–30.** A naive concatenation silently collapses 90 topics
   to 30. Year-prefix topic IDs.
2. **2014 topics are not yet downloaded** (only 2015-A and 2016 are). Leg A cannot be
   assembled until they are; also record the 2015 A-vs-B variant choice.
3. **Filter qrels to successfully fetched docs** — ~1.5% loss. Three relevants are genuinely
   withdrawn from the OA bucket (absent at versions 1–8 and under the modern
   `oa_comm|oa_noncomm|oa_other/xml/all/` prefixes).
4. **Length outliers.** Max is PMC4212304 at **435,946 tokens** — a conference-abstracts
   compendium (~213 chunks at size 2048); PMC2799006 is another at 252k. Record a cap/keep
   decision. 13/197 have empty *abstracts* (the compendia) — matters for any
   title+abstract-based filter.

## On §8 check 1
80.2% with CI 74.1–85.2 straddles the pre-registered ≥80% line, so it cannot be called now.
It becomes exact (zero sampling error) when assembly fetches all ~12k relevants. If it lands
below 80%, re-register the threshold consciously — it was calibrated against the native
corpus's 96.9% — rather than rounding it into a pass.

## Still the real gate: step 2
Abstract median 307 tokens. Length alone proves nothing: if judged evidence sits in the
abstract, a lead-chunk baseline still wins. The BM25 lead-only vs full-index ablation is
model-free and directly measures that failure mode. Two cautions carried in: year-prefix
topics before merging, and do not read the ablation gap as a per-document position measure —
compendium docs inflate the full-index side; the section-level oracle owns position.

## S3 path (from our own manifest; the plan's paths were explicitly unverified)
`https://pmc-oa-opendata.s3.amazonaws.com/PMC<id>.<ver>/PMC<id>.<ver>.xml`
Of 20 ids fetched at `.1`, only 1 has a `.2` — the bucket effectively carries a single
earliest version, so fetched copies are close to assessor-era. Accepted drift per the plan.
