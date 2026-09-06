# Phase-0 step 2 — BM25 lead-only vs full-index ablation on TREC CDS: **FAIL**

Measured 2026-09-04. Model-free: BM25 only, no GPU, no embedding endpoint, no crossencoder.
Dev-tenant Elasticsearch only (`http://localhost:24043`); `:9200` never touched. Nothing
written under `/rag/`. All working files under
`scratchpad/phase0/step2/` (scripts, `runs2.json`, `report2.json`, `position_diag.json`).

## Verdict

**The CDS judged set does not clear the pre-registered lead-chunk-insufficiency bar.**
On the pre-registered instrument (§7b: full docs at `fixed_tok512/0`, max-chunk rollup, vs
first-chunk-only), the full index is **not better than lead-only** on any recall or nDCG
metric — the gap is slightly *negative*. Even the most generous alternative arm
(whole-document, no chunk rollup) tops out at a **+0.059 recall@100** gap whose 95% CI
upper bound is **+0.098** — below the 0.15 threshold — and **+0.064 nDCG@10**, whose CI
spans zero and whose point estimate is below the 0.10 deep-leg threshold.

| gate | instrument | threshold | measured | call |
|---|---|---|---|---|
| recall@100 gap (brief's ≥0.15, read onto @100) | full − lead | ≥ 0.15 | **−0.006** (whole−lead: +0.059, CI +0.018…+0.098) | **FAIL** |
| nDCG@10 gap (plan §8 check 3, deep leg) | full − lead | ≥ 0.10 | **−0.017** (whole−lead: +0.064, CI −0.065…+0.208) | **FAIL** |

The one metric where the full index does win is **MRR@10 (+0.122)** — deep chunks help put
*one* good document first — but the CI spans zero at n=10 topics (win/loss 3/2/5).

**Do not read `recall@10` against 0.15.** These topics carry 84–150 relevant documents each,
so recall@10 is arithmetically capped at 10/n_rel ≈ **0.07–0.12**. A 0.15 gap is unreachable
there no matter how chunking-sensitive the set is; that is why the plan's §6 metric map puts
deep legs on nDCG@10/P@10/MRR@10, and why the brief's "recall gap ≥ 0.15" is evaluated here
at depth 100. Both readings fail, so the conflict does not change the call.

## What was built

**Topics — 10, deterministic rule, recorded.** One per (year × type) cell; within a cell,
keep topics with 40 ≤ n_rel(grade≥1) ≤ 250 **and** n_rel(grade≥2) ≥ 10, then take the one
whose n_rel is closest to the cell's eligible median (tie → lowest number). The 10th is the
runner-up by the same rule from the 2016 cohort (most recent snapshot), scanning
diagnosis→test→treatment. The window excludes both degenerate ends: a topic with 8 relevants
tells you nothing, and an 854-relevant topic makes every depth-100 denominator hopeless.

| topic | type | n_rel≥1 | n_rel≥2 | n_rel≥1 **present in index** |
|---|---|---|---|---|
| 2014_5 | diagnosis | 133 | 66 | 130 |
| 2014_11 | test | 100 | 48 | 100 |
| 2014_29 | treatment | 85 | 85 | 84 |
| 2015_8 | diagnosis | 128 | 79 | 126 |
| 2015_18 | test | 135 | 99 | 135 |
| 2015_23 | treatment | 109 | 50 | 109 |
| 2016_1 | diagnosis | 128 | 31 | 127 |
| 2016_9 | diagnosis | 123 | 57 | 123 |
| 2016_13 | test | 152 | 45 | 150 |
| 2016_26 | treatment | 113 | 61 | 111 |

Spread: 3 topics from 2014, 3 from 2015, 4 from 2016; 4 diagnosis, 3 test, 3 treatment.
Every topic has ≥84 relevants present, so no topic is measurement-starved.

**2015 variant, recorded per step 1's blocking-trap list: Task A.** Topics came from
`topics-2015-A.xml` paired with `qrels-treceval-2015.txt` — a consistent Task A pair. Task B
was not used. Topic ids were year-prefixed before merging (step 1's trap 1); the merged file
holds 90 distinct topics, not 30.

**Corpus — 4,053 documents.** All grade≥1 docs for the 10 topics plus a seeded sample of
300 grade-0 docs per topic as hard negatives (`random.Random(20260904)`), deduped across
topics and years — the same PMCID can be grade-2 for one topic and grade-0 for another; it
is indexed once and the per-topic qrels carry the roles. 4,099 unique PMCIDs requested,
**4,053 fetched (98.9%)**, 46 misses (absent at versions 1–3), listed in
`step2/fetch_misses.txt`. Fetch was 32-way parallel against
`pmc-oa-opendata.s3.amazonaws.com`; 4,019 resolved at `.1`, one at `.2`.

**Parsing — `ragstack.ingestion.jats.article_prose`, stdlib ElementTree.**
**Zero parse failures out of 4,053.** 12 documents have an empty `<body>` (0.3%) and 303
have an empty abstract (7.5%, the conference-abstract compendia). Empty-body documents were
indexed anyway — for them the lead index and the full index hold the same text, which is the
honest treatment. Document text = `title \n\n abstract \n\n body`.

**Length (SFR tokenizer, `Salesforce/SFR-Embedding-Mistral`, `HF_HOME=/rag/cache`):**
median **4,573** tokens, mean 6,880, **84.9% over 2,048 tokens**, median **9 chunks**/doc,
56,561 chunks total, 58 single-chunk documents. 18 documents exceed 50k tokens; the largest
is 1.61M tokens. (Step 1's 80.2% ±CI for the >2,048 check lands at 84.9% on this larger,
differently-drawn sample — still a sample, not the full-corpus figure.)

**Indices (all three on dev ES, default standard analyzer, default BM25, 1 shard/0 replicas,
same document text throughout — only the unit of indexing differs):**

- `phase0_ablation_lead_*` — first 512-token chunk only, 4,053 docs.
- `phase0_ablation_full_*` — every chunk, `fixed_tok512` overlap 0, 56,561 chunks.
- `phase0_ablation_whole_*` — **added control**, one ES doc per article, whole text in one
  field, 4,053 docs. Not in the plan; added because without it a null result cannot be
  attributed between "the body carries no retrievable signal" and "the max-chunk rollup
  discards it". It turns out to matter (below).

**Queries: the topic `summary` field** — the condensed form, the CDS convention, and the
variant the plan's §5 nominates for Leg A. `description` is the long case narrative and is
the sensitivity variant, not run here. Retrieval depth **200** per query (not 100), so the
>50k-token exclusion variant filters a full list rather than a truncated one. Chunk→document
rollup is ES `collapse` on `docno`, i.e. score = best chunk (`full_sum3` below re-ranks the
same depth-200 candidate set by the sum of a document's top-3 chunk scores).

**Recall denominators are the qrels restricted to documents actually present in the index**
— the "present" column above — and the three arms hold identical document sets, so the
denominators are identical across arms and the gaps are not a coverage artifact.

## Results

Means over the 10 topics. Gaps are paired per-topic differences; CIs are 10,000-resample
paired bootstraps over topics.

### grade≥1 relevant (trec_eval binary convention)

| metric | lead | full | whole | full_sum3 | **full − lead** | **whole − lead** |
|---|---|---|---|---|---|---|
| recall@10 | 0.0309 | 0.0267 | 0.0348 | 0.0300 | −0.0042 | +0.0039 |
| recall@100 | 0.1983 | 0.1925 | **0.2567** | 0.2194 | −0.0058 | **+0.0585** |
| nDCG@10 | 0.3522 | 0.3353 | **0.4160** | 0.3672 | −0.0168 | +0.0638 |
| MRR@10 | 0.4728 | **0.5950** | 0.6367 | 0.5667 | **+0.1222** | +0.1639 |

### grade≥2 relevant

| metric | lead | full | whole | full_sum3 | **full − lead** | **whole − lead** |
|---|---|---|---|---|---|---|
| recall@10 | 0.0404 | 0.0298 | 0.0405 | 0.0331 | −0.0106 | +0.0001 |
| recall@100 | 0.1927 | 0.1917 | **0.2784** | 0.2288 | −0.0010 | **+0.0857** |
| nDCG@10 | 0.2224 | 0.1930 | 0.2576 | 0.2070 | −0.0294 | +0.0353 |
| MRR@10 | 0.3311 | 0.4325 | 0.4960 | 0.3833 | +0.1014 | +0.1648 |

### Paired bootstrap (grade≥1 / grade≥2), n = 10 topics

| gap | mean | 95% CI | topic win/loss/tie |
|---|---|---|---|
| full − lead, recall@100 | −0.0058 / −0.0010 | [−0.029,+0.019] / [−0.036,+0.035] | 4/5/1 · 5/4/1 |
| **whole − lead, recall@100** | **+0.0585 / +0.0857** | **[+0.018,+0.098] / [+0.037,+0.139]** | 9/1/0 · 9/1/0 |
| full − lead, nDCG@10 | −0.0168 / −0.0294 | [−0.097,+0.060] / [−0.124,+0.055] | 4/5/1 · 4/5/1 |
| whole − lead, nDCG@10 | +0.0638 / +0.0353 | [−0.065,+0.208] / [−0.105,+0.198] | 5/4/1 · 6/3/1 |
| full − lead, MRR@10 | +0.1222 / +0.1014 | [−0.067,+0.357] / [−0.161,+0.369] | 3/2/5 · 5/2/3 |

Only one gap in the whole experiment is statistically distinguishable from zero:
**whole-document over lead-only at recall@100** (9 of 10 topics improve). Its CI upper bound
is +0.098 / +0.139 — **below 0.15 in both grade variants**.

How far the CI-level rejection reaches, stated precisely:

- **The pre-registered §7b instrument (chunked-full vs lead) fails at both the point estimate
  and the CI upper bound, on both gates**: recall@100 CI upper +0.019 < 0.15; nDCG@10 CI
  upper +0.060 < 0.10. This is the arm the gate is defined on, and it is rejected outright.
- **The whole-document control also fails at CI level on recall@100** (upper +0.098 / +0.139
  < 0.15).
- **It does not fail at CI level on nDCG@10**: whole−lead is +0.064 with CI [−0.065, +0.208],
  whose upper bound exceeds the 0.10 deep-leg bar. At n = 10 topics that gap cannot be
  excluded from 0.10 — see *What this does not settle*. The point estimate is below the bar
  and the control arm is not the gated instrument, so this does not change the verdict, but
  it is the one place the CI-level claim does not hold.

### Outlier-excluded variant (drop the 18 documents over 50k SFR tokens)

Nothing moves. grade≥1: full−lead recall@100 goes −0.0058 → −0.0074, nDCG@10 unchanged at
−0.0168; whole−lead recall@100 +0.0585 → +0.0558. grade≥2: full−lead recall@100
−0.0010 → +0.0000. **The compendium documents (PMC4212304 and friends) are not manufacturing
or masking anything** — they are 18 of 4,053 docs and they barely enter the top-200 lists.
That caution from review was worth checking and is now discharged.

### Per-topic detail (grade≥1, all docs)

| topic | type | n_rel present | R@100 lead | R@100 full | R@100 whole | nDCG lead | nDCG full | nDCG whole | MRR lead | MRR full |
|---|---|---|---|---|---|---|---|---|---|---|
| 2014_5 | diagnosis | 130 | 0.146 | 0.146 | 0.154 | 0.301 | 0.290 | 0.466 | 0.200 | 1.000 |
| 2014_11 | test | 100 | 0.190 | 0.170 | 0.210 | 0.283 | 0.000 | 0.444 | 0.333 | 0.000 |
| 2014_29 | treatment | 84 | 0.310 | 0.321 | 0.405 | 0.306 | 0.502 | 0.363 | 0.250 | 1.000 |
| 2015_8 | diagnosis | 126 | 0.175 | 0.127 | 0.270 | 0.240 | 0.180 | 0.750 | 0.333 | 0.250 |
| 2015_18 | test | 135 | 0.000 | 0.007 | 0.007 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| 2015_23 | treatment | 109 | 0.156 | 0.138 | 0.312 | 0.249 | 0.307 | 0.078 | 0.500 | 0.500 |
| 2016_1 | diagnosis | 127 | 0.276 | 0.346 | 0.370 | 0.627 | 0.703 | 0.474 | 1.000 | 1.000 |
| 2016_9 | diagnosis | 123 | 0.244 | 0.285 | 0.268 | 0.684 | 0.552 | 0.552 | 1.000 | 1.000 |
| 2016_13 | test | 150 | 0.307 | 0.240 | 0.247 | 0.766 | 0.666 | 0.606 | 1.000 | 1.000 |
| 2016_26 | treatment | 111 | 0.180 | 0.144 | 0.324 | 0.066 | 0.155 | 0.427 | 0.111 | 0.200 |

The per-topic signs are a coin flip for full−lead (4 win, 5 lose, 1 tie on both recall@100
and nDCG@10). `2015_18` scores ~0 on every arm — *below* the ~2.5% a random pull from the
pool would give. That is the hard-negative design working, not a topic↔qrels mismatch: each
topic's 300 sampled grade-0 documents are its own pooled near-misses, so BM25 on a
lexically hard topic retrieves precisely them. Checked by hand: the query is an ordinary
heart-failure case ("progressive dyspnea on exertion … bilateral basilar crackles"), its
top-5 retrieved are on-topic dyspnea case reports that NIST graded 0, and its grade-2
relevants are broader clinical articles ("The measurement of lung water", "Acute respiratory
failure in the elderly") that share few surface terms. The topic hurts all four arms equally
and is included rather than dropped.

## The finding underneath the number

The evidence in these documents is **not** concentrated in the lead — and the set still fails.

Diagnostic: for the 369 relevant documents that the full index surfaces in its top-200
across the 10 topics, which chunk won the max-rollup?

- chunk 0 (the lead): **130 / 369 = 35.2%**
- chunks 1–2: 111 (30.1%)
- chunks ≥3: **128 (34.7%)**

So ~65% of relevant documents are matched on text *beyond* the lead chunk. The plan's §2
failure mode — "the judged evidence sits in the abstract" — is **not** what is happening.

What is happening is a different, and for this study a worse, problem: **CDS qrels are
document-level and topical.** A patient-case summary matches an article's *aboutness*, and
aboutness is fully declared in the title+abstract. Body text supplies more matching terms,
but they identify the same documents the abstract already identified, while simultaneously
lifting non-relevant documents that mention the query's terms somewhere deep. The two
effects nearly cancel: net −0.006 recall@100 under max-chunk rollup, net +0.059 when the
rollup is removed. Chunking changes *which passage* is retrieved; the document-level
judgments cannot see that, so they cannot score it.

That is also why the rollup control earns its place. The pre-registered instrument's
negative gap is roughly half rollup artifact (whole beats full by +0.064 recall@100) and
half genuine ceiling. Reporting only the pre-registered arm would have overstated the
negative; reporting only the whole-doc arm would have understated it. Both are above.

## Read on the question that matters

**Is the TREC CDS judged set chunking-sensitive? On this evidence: no — not enough to gate
a chunking study on.** Long documents are confirmed (median 4.6k tokens, 85% over 2,048) and
the evidence is confirmed to be distributed through document depth (65% of matches beyond
the lead), so CDS is far better than scifact on both preconditions. But the acceptance test
asks a behavioural question — *can a lead-chunk baseline already solve this set?* — and the
answer is yes, to within 0.006 recall@100 and 0.017 nDCG@10 of the full index. A chunking
grid run on Leg A would be measuring differences smaller than the lead/full difference,
which itself is not distinguishable from zero at n=10.

This is a plan-level result, not a bug: it says **document-level relevance judgments are the
wrong instrument for a chunking study**, however long the documents are. Legs B and C do not
inherit the defect — Leg B records the source section by construction and scores
passage-provenance recall, Leg C filters on evidence position — which is precisely why the
plan built three legs.

## What this does *not* settle

- **Dense retrieval.** This is BM25 only, as specified. A dense retriever truncating at 512
  tokens cannot see past the lead at all, so the full/lead contrast could look different —
  but the ceiling identified here is a property of the *judgments*, not the scorer, and a
  document-level qrel will bound any retriever the same way. The plan's §9 confirmatory
  dense run would sharpen this; it was not in scope for step 2.
- **Statistical power.** n = 10 topics. Every gap except whole−lead recall@100 has a CI
  spanning zero. The design rejects "≥0.15 recall@100" at CI level on every arm, and
  "≥0.10 nDCG@10" at CI level on the *gated* arm — but **not** on the whole-document control,
  whose nDCG@10 CI reaches +0.208. If anyone wants to defend Leg A on the whole-document
  arm's nDCG@10, that is the one gap this run cannot close; it needs more topics, not a
  different argument. Running all 90 would tighten every CI; it would not move the point
  estimates (−0.017 gated, +0.064 control) far enough to reach 0.10.
- **The `description` variant.** Only `summary` was run. A longer query has more terms to
  match deep text and *might* widen the gap; that is the single cheapest follow-up if anyone
  wants to contest this result.
- **The 46 unfetchable documents** (1.1%) are excluded from both the index and the recall
  denominators. Uniform across arms, so directionally neutral.
- **`n_rel` vs the pooling depth.** CDS qrels come from pooled runs of 2014–16 systems. A
  document neither pooled nor judged counts as non-relevant here, as everywhere.

## Recommendation

**Do not build Leg A as the chunking study's anchor on the strength of this test.** Options,
in the order I would put them to the user:

1. **Demote Leg A from acceptance-gated anchor to a sign-check corpus.** Keep the 90 topics
   as a real-human-judged sanity check that a config ranking is not absurd, and state
   plainly that it cannot resolve chunking contrasts. Cheap — the fetch machinery works.
2. **Re-register check 3 for document-level legs.** The 0.15/0.10 thresholds were calibrated
   against passage-provenance legs. If the user wants Leg A kept as a gate, the threshold
   has to be set consciously against what document-level judgments *can* express — but note
   the measured full−lead gap is negative, so no honest re-registration rescues it.
3. **Put the weight on Legs B and C**, which control evidence position by construction, and
   spend the saved Leg A assembly budget (1–2 days eng, ~40k document fetches) there.

A fourth option exists and I would not take it: re-judging CDS at passage level. That is a
new annotation project, not a pilot step.

## Reproduction

```
scratchpad/phase0/step2/
  select_topics.py    -> selected_topics.json     (the 10 topics + rule)
  build_fetchlist.py  -> fetchlist.txt, qrels_sel.json, fetch_stats.json
  fetch.py            -> xml/ (4,053 files), fetch_log.txt, fetch_misses.txt
  parse_chunk.py      -> chunks.jsonl (56,561), docs.jsonl (per-doc tokens/chunks)
  index_es.py         -> lead + full indices
  index_whole.py      -> whole-document control index
  evaluate2.py        -> runs2.json, report2.json, position_diag.json
  boot.py             -> paired bootstrap CIs
```

Every python invocation ran as
`HF_HOME=/rag/cache PYTHONPATH=/home/wilke/Development/ragstack/python /rag/envs/ragstack/bin/python`
where the repo package was needed (the miniconda env has no `transformers`).

## Cleanup

All three scratch indices were deleted at the end of the run; the verifying `_cat/indices`
listing is at the bottom of this file.

```
$ curl -s http://localhost:24043/_cat/indices?v          # BEFORE cleanup
health status index                                                                pri rep docs.count store.size
yellow open   ragstack                                                               1   1          0       249b
green  open   phase0_ablation_whole_20260904                                         1   0       4053       90mb
green  open   phase0_ablation_lead_20260904                                          1   0       4053      7.7mb
yellow open   ragstack_lib_oa_dev_salesforce_sfr_embedding_4096_fixed_token_512_64…  1   1      24263     45.8mb
green  open   phase0_ablation_full_20260904                                          1   0      56561     94.1mb

$ curl -s -XDELETE http://localhost:24043/phase0_ablation_full_20260904    {"acknowledged":true}
$ curl -s -XDELETE http://localhost:24043/phase0_ablation_lead_20260904    {"acknowledged":true}
$ curl -s -XDELETE http://localhost:24043/phase0_ablation_whole_20260904   {"acknowledged":true}

$ curl -s http://localhost:24043/_cat/indices?v          # AFTER cleanup
health status index                                                                pri rep docs.count store.size
yellow open   ragstack                                                               1   1          0       249b
yellow open   ragstack_lib_oa_dev_salesforce_sfr_embedding_4096_fixed_token_512_64…  1   1      24263     45.8mb

$ curl -s 'http://localhost:24043/_cat/indices/phase0_ablation_*?v'
health status index uuid pri rep docs.count docs.deleted store.size pri.store.size dataset.size
                                                        # <- header only: none remain
```

The two surviving indices (`ragstack`, `ragstack_lib_oa_dev_…`) pre-existed this run and
were not touched. Production ES on `:9200` was never contacted.
