# SPEC — the chunking study's confirmation run

**Status: `PROPOSED (rev. 2) — specification only`. Nothing in this document has run.** No GPU was
used, no store contacted, no model called in preparing it; every number quoted below is a
citation from the Phase-0 record (read-only) or arithmetic on those citations.

**Repo:** `/home/wilke/Development/ragstack`. Local `main` is at **`55a0fc2`** (verified at
writing time; the brief's commit). The run itself executes at a commit frozen in the PREREG
(§P.1) — expected `55a0fc2` or a descendant, recorded exactly, never assumed.

**Deliverable of the run this document specifies:** a confirmatory answer, on the 80
held-back TREC CDS topics, to the chunking decisions the Phase-0 pilots opened — measured on
an endpoint (evidence coverage at a fixed generator budget, behind a production-shaped
retrieval pipeline, against evidence labels independent of the production cross-encoder)
that has never been computed on any topic.

**How to read this document.** §§1–11 are the specification: design, protocols,
justifications, cost. §P is the **PREREG** — the freezable commitment, deliberately
self-contained and restating the operative rules compactly, with the slots Stage 0 fills
marked `[FROZEN-AT-STAGE-0]`. §12 flags what in the task's framing this specification
found to be wrong or imprecise. §13 says what the run cannot establish even if everything
goes perfectly. **§14 is the change log for revision 2** — the nine amendments two
independent external statistical reviews required before this document can be frozen, each
recorded against what it replaced.

**Revision 2 in one paragraph.** Both reviews endorsed the architecture (development-only
calibration, a fixed margin, the blinding chain, arm-identical exclusions, a hashed
pre-registration, an explicit inconclusive outcome) and both concluded that the
**statistical gate as written could not be frozen**. The primary test is now named
correctly (one-sided non-inferiority at α = 0.025, which is the test the existing
constant 2.802 always computed); the variance model now carries intra-topic correlation;
the invalid argument against a binary endpoint is deleted; the power gate is a
three-outcome decision on a pre-declared variance bound rather than a single ambiguous
number; calibration is per-contrast; the label-validation gate has acceptance criteria and
a rubric fixed before labeling; the evidence-unit, coverage, packing and
behind-the-reranker definitions are frozen here; multiplicity and exclusions are declared
and power is computed on retained n. Full detail in §14.

---

## 0. Evidence base

Everything below cites one of:

| tag | file |
|---|---|
| S1/S2/S3 | `phase0/RESULTS-step1-cds-gate.md`, `RESULTS-step2-lead-ablation.md`, `step3/RESULTS-step3-real-experiment.md` |
| SA / SB | `phase0/stage1/RESULTS-stage1-legA.md`, `phase0/stage1-legB/RESULTS-stage1-legB.md` |
| PIL | `phase0/pilots/RESULTS-legBC-pilots.md` (incl. the §7a oracle and the σ_d measurement) |
| PR / RR | `phase0/rescore/PREREG-rescore.md`, `RESULTS-rescore-small-corpora.md` |
| BK-P / BK-R | `phase0/breadth-k/PREREG-breadth-k.md`, `RESULTS-breadth-k.md` |
| D-SUF / D-PROV / D-COMP | `design/ANSWER-sufficiency-and-judges.md`, `ANSWER-provenance-and-repro.md`, `ANSWER-completeness-and-subsets.md` |
| PLAN-C / PLAN-L | `docs/plans/chunking-evaluation.md`, `docs/plans/long-doc-judged-set.md` |
| CODE | `python/ragstack/retrieval/retriever.py`, `python/ragstack/api/routers/query.py`, `python/ragstack/config.py` (read at `55a0fc2`) |

All paths under `phase0/` and `design/` are relative to
`~/Development/worktrees/phase0-rescue/` (the durable copy).

---

## 1. The eight defects, and the design element that answers each

The two external reviews credited the instrumentation and rejected the headline claims.
This table is the contract: every defect maps to a specific, checkable design element.

| # | defect | design answer | § |
|---|---|---|---|
| 1 | n = 10 topics under percentile bootstrap | n = 80 confirmation topics (power computed on the **retained** n after exclusions, §8.5.6); BCa topic bootstrap + paired t (df = n_retained − 1) + sign-flip permutation; unit-level cluster bootstrap as pre-registered sensitivity; power gated at Stage 0 on a three-outcome rule | §3, §8 |
| 2 | gold located by the same cross-encoder that reranks (`:50052`) | evidence labels from `Llama-4-Scout` (`mango:8003`, cross-family), human-validated subsample, quote-verified; `:50052` used **only** as the pipeline reranker, never as gold | §6 |
| 3 | passage result measured on a one-document corpus (localization, not retrieval) | one shared ~38k-document corpus (all 90 topics' judged docs); every query must discriminate its documents against ~37,900 real judged competitors | §4 |
| 4 | harness ≠ serving path (one-chunk-per-doc rerank, global max-rollup) | offline harness rebuilt production-shaped (full-pool rerank at depth 50, per `_retrieve_fused`); Stage 2 runs shortlisted arms through the **actual** `HybridRetriever`/router with a **blocking** concordance gate | §5, §9 |
| 5 | budget matching underdefined; a headline ratio was un-provenanced | one packing rule (A1, disambiguated), generator-tokenizer accounting, realised tokens reported per arm, and a standing rule: **every ratio quotes leg + metric + budget + table cell** | §7, §12.2 |
| 6 | "powered null" ≠ evidence of equality | noninferiority via a **one-sided non-inferiority test at α = 0.025** at a consequence-derived margin ε; the powered-null vocabulary is retained only for superiority rows | §8 |
| 7 | missing arms (headers, small-to-parent, multi-granularity) | `header512` and `parent256` arms in the confirmatory grid; multi-granularity as a zero-embed exploratory arm | §5 |
| 8 | SFR instruction prefix absent (valid as-deployed, but a confound) | bounded sensitivity arm: queries re-embedded with the model-card instruction template; documents unchanged | §10 |

---

## 2. Development set, confirmation set, and what has already touched the 80

### 2.1 The sets

* **Development set — the 10 topics of Phase-0 step 2** (S2, deterministic selection rule
  recorded there): `2014_5, 2014_11, 2014_29, 2015_8, 2015_18, 2015_23, 2016_1, 2016_9,
  2016_13, 2016_26`. Everything may be computed on these, at any time, for any purpose.
* **Confirmation set — the other 80 CDS topics** (year-prefixed ids; the merge trap of
  S1/S2 — all three years number topics 1–30 — applies and the assembled topic file must
  contain 90 distinct ids before the 80 are carved out).

**How the 10 were selected, and why it matters for calibration** (review item 8; the rule
is quoted from S2, not paraphrased): *one topic per (year × type) cell — 3 cells × 3 years
— keeping only topics with `40 ≤ n_rel(grade≥1) ≤ 250` and `n_rel(grade≥2) ≥ 10`, then
taking the one whose `n_rel` is closest to the cell's eligible median (tie → lowest
number); the 10th is the runner-up by the same rule from the 2016 cohort.* The result is
stratified across all three CDS years and all three types (4 diagnosis / 3 test / 3
treatment; 3 / 3 / 4 by year) — but it is deliberately **median-relevance-count**: both
degenerate ends were excluded by construction, and every dev topic has ≥ 84 relevants
present in the index.

**The consequence, stated rather than assumed:** the 10 dev topics are a *stratified
central sample*, not a random sample, of the 90. The 80 confirmation topics contain the
sparse tail (topics with single-digit relevants) and the 854-relevant extreme that the dev
window excluded. Evidence-unit counts per topic — hence σ_d — plausibly vary with
`n_rel`. **Every Stage-0 variance estimate is therefore reported with its `n_rel`
stratum**, and the power gate (§8.5) is read against the confirmation set's known
`n_rel` distribution (computable now: qrels counts are non-outcome data, §2.3), not
against the dev distribution. Where a dev stratum is empty, the gate says so and the
affected contrasts inherit the wider of the neighbouring strata's σ_d.

**There is no reserve.** 10 development + 80 confirmation topics is *all 90* TREC CDS
topics. Nothing is held back. Every topic an exclusion rule removes (§8.5.6) reduces n
permanently; there is no pool to draw a replacement from, and no honest way to
"backfill" a dropped confirmation topic from the development set without destroying the
held-out property that is this design's entire point. This is why §8.5.6 computes power
on the **expected retained n**, and why the exclusion criteria are fixed *before* Stage 0
rather than after the labels are seen.

### 2.2 The exposure ledger — stated, not wished away

**The brief's phrase "80 untouched topics" is not strictly true, and this spec will not
pretend otherwise.** What has already touched the 80:

1. **The §7a oracle** (PIL §1) computed a cross-encoder argmax section for up to 25 sampled
   relevants of **all 90 topics** (2,161 pairs, 2,095 docs). Those per-pair argmax records
   exist on disk (`breadth-k/lega_gold.json`, `oracle_results.jsonl`).
2. **The breadth-k run** (BK-R) embedded all 90 topics' `summary` queries and scored dense
   retrieval at the four token sizes over an 86-topic ladder (N = 100 mini-corpora),
   publishing aggregate curves (`PR@k`, budget tables) that include the 80.

What has **not** touched them: any of the 24-config grid contrasts (10 dev topics only —
SA), any reranked metric, any serving-path number, any evidence label independent of
`:50052`, and any value of the endpoint defined in §7 — that endpoint has never been
computed on **any** topic.

**Consequences, adopted:**

* The honest claim is *"held out from every config-selection contrast and from the entire
  primary-endpoint apparatus"*, not *"never observed"*. The report must use the former.
* The arm list, margins, prompts, filters and analysis code in this spec are anchored to
  the dev-set record (S3/SA) and to published Phase-0 aggregates. Where an aggregate
  included the 80 (BK-R's budget tables), that is disclosed at the point of use.
* **Sequestration rule:** from the moment this spec is accepted until the confirmation
  analysis is unblinded, no person or agent working on this study opens
  `oracle_results.jsonl`, `lega_gold.json`, or any breadth-k per-topic artifact for a
  confirmation topic. This is a commitment, not a technical guarantee (the files exist);
  the technical guarantee is that the new gold (§6) and endpoint (§7) owe those files
  nothing.

### 2.3 What may and may not be examined before the confirmation analysis

| MAY be examined at any time | MUST NOT be examined before unblinding |
|---|---|
| dev-set: everything, including all Phase-0 results | any retrieval ranking, similarity, fusion, or rerank score computed on a confirmation topic under this design |
| confirmation topics' **non-outcome** data: qrels counts and grades, fetchability, document token lengths, section counts, corpus manifest | any evidence label for a confirmation topic **read next to a retrieval outcome** (labels are produced blind, §6.4, and stored; the labeler team sees documents and topics, never rankings) |
| aggregate fleet/throughput measurements | per-topic values of any §7 metric on confirmation topics |
| the already-published Phase-0 aggregates (exposure disclosed above) | the sequestered §7a / breadth-k per-topic artifacts for the 80 (§2.2) |

Corpus assembly, power checks and labeling need the left column; nothing in the right
column is needed before unblinding, and the run order (§11) makes the separation auditable:
retrieval outputs for confirmation topics are written encrypted-by-obscurity nowhere — they
are simply not computed until labels are frozen and the PREREG hash is recorded.

---

## 3. Statistical skeleton (summary — full rules in §8, commitments in §P)

* Unit of analysis and resampling unit: **topic** (n = 80 nominal, **n_retained** after
  the §8.5.6 exclusions — every power statement is made on n_retained).
* Primary analysis: **paired**, per-topic differences between arms.
* Pre-registered sensitivity, always reported beside the primary: the **unit-level cluster
  bootstrap** (resample topics with replacement, recompute the arm difference over *all*
  units of the resampled topics), which handles unequal units-per-topic natively and does
  not assume equal per-topic precision (§8.4.3).
* Noninferiority: **one-sided non-inferiority test at α = 0.025** at margin ε (§8.2) —
  operationally, the upper bound of the two-sided 95% CI of the paired difference must sit
  below ε. Two NI contrasts, α = 0.025 each by Bonferroni within the family, so the
  per-contrast one-sided level *is* 0.025 and the family's one-sided level is 0.05.
  **This is not TOST**: TOST tests both bounds and is the wrong test for the product
  question, which is one-directional (does the cheaper configuration lose more than ε?).
  The z-constant 2.802 = z₀.₀₂₅ + z₀.₂₀ that this design has always used is the constant
  of *this* test, not of TOST (§8.5.1).
* Superiority: sign-flip permutation + BCa bootstrap, Holm across the 3-contrast family,
  house resolution rule retained (RESOLVED / powered NULL / UNRESOLVED with
  δ80 = 2.802·σ_d/√n = **0.313·σ_d** at n = 80, recomputed at n_retained).
* Multiplicity across families is **declared, not assumed**: §8.1.1 fixes the hierarchy.
* Power is gated **at the chosen ε on the primary endpoint's own dev-measured σ_d, per
  contrast** (§8.5) — not at the median σ across unrelated comparisons, and not as a
  single pooled number across contrasts that measure different things (§8.5.4). The gate
  has **three outcomes**, not pass/fail (§8.5.5). The known Phase-0 σ_d values
  (Leg A nDCG@10 paired σ_d ≈ 0.156 dense / 0.173 reranked at n = 10, PIL §4; Leg B's own
  measured 0.152 at n = 260, PIL-rerun §5) are priors and they are **not reassuring**;
  they are quoted in the gate reasoning rather than omitted.

---

## 4. Corpus — multi-document by construction

### 4.1 Composition

One **shared corpus** for all 90 topics (dev + confirmation):

* every **grade ≥ 1** judged document of every topic that fetches from
  `pmc-oa-opendata` — 12,307 distinct PMCIDs measured, 98.5% fetchable (S1), ≈ **12.1k**
  docs;
* **300 seeded grade-0 judged hard negatives per topic** (95% fetchable, S1), the same
  policy the dev pilot used (S2) — ≈ **25.7k** docs after fetch loss;
* deduplicated by PMCID (13,807 (topic, doc) pairs collapse onto 12,307 docs — the
  overlap is real and is handled in §8.4).

**The development topics retrieve against this same corpus** — that is what "one shared
corpus for all 90 topics" means, and revision 2 makes the consequence explicit (review
item 8): **Stage-0 calibration runs on the full ~38k-document corpus, not on a dev-scale
one.** The dev pilot's 4,053-document corpus (S2) is *not* the calibration corpus; a σ_d
measured against 4k documents would describe a different experiment from one run against
38k, because distractor density drives how often a relevant document is displaced from the
pool at all — the dominant term in `p_flip`. Distractor composition is therefore identical
by construction for the 10 and the 80: same judged-only policy, same 300-per-topic
grade-0 seeding, same dedup. The only permitted difference is the grade-0 **seed**
(§4.2.6). This is why §11 builds the corpus and the indexes **before** the Stage-0
variance measurement rather than after it.

Total ≈ **38k documents**, ≈ **260M SFR tokens** per index build (step-2 measured mean
6,880 tokens/doc on the same population; S2). Every topic's query then retrieves against
~37,900 documents that are *not* its relevants — other topics' relevants and its own
graded non-relevants, the hardest available competition with human judgments attached.
This is what makes the passage result a **discrimination** measurement: document hit is no
longer 1.0 by construction anywhere in the design.

### 4.2 Assembly rules (all measured traps from S1/S2/PLAN-L §13.4 carried forward)

1. **Year-prefix topic ids** before merging qrels; assert 90 distinct topics.
2. 2015 uses **Task A** (`topics-2015-A.xml` + `qrels-treceval-2015.txt`); 2014 topics from
   `trec-cds.org/topics2014.xml`.
3. **Filter qrels to fetched documents; never impute.** Recall/nDCG denominators are the
   restricted qrels, identical across arms, so no gap can be a coverage artifact.
4. **Length cap decision, made now:** documents are kept regardless of length (the
   compendium outliers moved nothing in step 2 — S2/PLAN-L §13.4.4) — **except** for the
   labeling pass, which has its own windowing rule (§6.5). The kept-outlier count is
   reported.
5. Parse with `ragstack.ingestion.jats.article_prose`; a document with an empty parsed body
   is excluded and counted (0.3% in the dev pilot).
6. Grade-0 sampling seeds: the dev 10 topics **reuse `random.Random(20260904)`** so the
   pilot corpus reproduces byte-for-byte; the 80 confirmation topics use the new seed
   **20260912**.
7. **Corpus manifest = the sorted list of (pmcid, sha256(file bytes)) pairs, hashed** —
   full 64-hex digest recorded before any embedding. This deliberately upgrades the
   Phase-0 convention (paths-only, truncated to 16 hex — the weakness D-PROV flagged):
   "same ids, different bytes" is now caught.

### 4.3 What is *not* in the corpus, and why

No unjudged OA distractors in the primary corpus. The judged pool is already ~96% distractor
for any single topic, every distractor carries a human judgment (so an "unjudged relevant"
cannot leak in), and adding OA padding would double embed cost while making the
unjudged-relevant contamination PLAN-L warns about live again. A ×1 OA-distractor rung is
pre-listed as the optional Stage-3 scale check (§11), not part of the confirmatory run.

---

## 5. Arms

### 5.1 Index arms (each is one embedding pass over the corpus)

| arm | definition | why it is in |
|---|---|---|
| `fixed_tok256_ov0pct` | `token_window`, 256 tokens, 0% | bottom of the size ladder; the budget-matched Phase-0 winner |
| `fixed_tok512_ov0pct` | 512 / 0% | overlap NI comparator |
| `fixed_tok1024_ov0pct` | 1024 / 0% | the storage lever (2.29× fewer vectors than shipping) |
| `fixed_tok2048_ov0pct` | 2048 / 0% | top of the ladder; S3-replication comparator |
| `fixed_tok512` | 512 / 64 tok = 12.5% | **shipping control** — keeps its exact Phase-0 key (PLAN-C) |
| `header512` | 512 / 0%, each chunk's **embedded and reranked text** prefixed with `«{article title} — {section title path}»\n` built from JATS metadata; `start_char`/`end_char` still index the un-prefixed content | defect 7: contextual chunk headers |

Chunker: `FixedTokenWindowChunker`, SFR tokenizer, token counter backend asserted `hf`
(never `estimate`). `budget_mode` does not reach `token_window` (verified byte-identical
across the `55a0fc2` fill change — BK-R §1), but it is **pinned to `"joined"` in the
manifest anyway**, so that if any sentence/words arm is ever added to this design the pin
already exists and the `55a0fc2` default change cannot silently move a boundary.

### 5.2 Scoring arms (no new embeddings)

| arm | definition |
|---|---|
| `parent256` | retrieval and reranking identical to `fixed_tok256_ov0pct`; at packing time each admitted chunk is **replaced by its enclosing top-level JATS `<sec>`** — for a chunk that straddles a section boundary (`token_window` cuts do not respect sections), the parent is **the section containing the chunk's `start_char`** — truncated to ≤ 1,024 generator tokens centred on the child chunk; repeated parents are packed once (budget charged once), rank order preserved |
| `multi256+1024` *(exploratory only)* | RRF-fuse (k = 60) the `tok256/0` and `tok1024/0` dense rankings, rerank the fused pool, pack normally. Zero new embeddings. Reported, never confirmatory |

### 5.3 What was cut, and why the list stops here

* **`sentence` / `words` / `semantic` kinds:** Phase-0's strongest cross-leg regularity is
  that **realised chunk tokens, not kind, is the dominant variable** (SA §5 r = +0.81,
  SB §9 r = −0.97); `semantic` costs ~7× to embed (SA §7.1); and the `55a0fc2` fill fix
  changes what `sentence`/`words` produce, breaking comparability with every Phase-0
  number. A kind grid is a *different study* and should run after the size/overlap
  question is closed.
* **25% overlap cells:** powered null on both legs at 12.5% and 25% (SA §4, SB §5–6);
  the shipping 12.5% cell plus the 0% ladder already brackets the decision.
* **`whole4096`:** a head-truncation artifact on this corpus (S3: median doc 4,573 tokens),
  pre-declared a bound, not a comparison (PR §4.3).
* Cost check: 6 index arms × ≈260M tokens ≈ **1.6B SFR tokens ≈ 2.8 fleet-hours** at the
  measured 161k tok/s (±2× band) — §11. A 7th index arm would buy less than any of the
  six answers and push the pessimistic band past a night.

### 5.4 Total scoring-arm count

8 arms enter the harness (6 index arms + `parent256` + `multi256+1024`), of which **7 are
in the confirmatory analysis** and `multi256+1024` is exploratory.

---

## 6. Gold: independent minimal-sufficient evidence labels

### 6.1 The labeler, and why it breaks the circularity

**`RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic` on `mango:8003`** — verified live
(`GET /v1/models` + 2-token chat probe, D-SUF), `max_model_len` 60,000, non-reasoning (no
reasoning-token tax: 2 completion tokens where Qwen spends 176–224). It is cross-family
from all three production models: SFR-Embedding-Mistral (retriever), `bge-reranker-v2-m3`
(reranker, `:50052`), and it authored nothing in this leg (CDS queries are human). The §7a
defect — gold chosen by the same model that reranks — cannot recur: **`:50052` touches
nothing labeled gold in this design.**

Second judge for agreement statistics: Qwen3.6-35B-A3B (`mango:8004`), `enable_thinking:
false`. Eligible here because CDS queries are human-authored (the D-SUF family-circularity
amendment bars Qwen only from legs whose queries Qwen wrote). Fallback if Scout is down:
Claude subagents with isolated contexts (the PIL Leg-B precedent), cap ~200 items.

### 6.2 What is labeled

For each (topic, grade ≥ 1 document) pair in the **labeling set** (§6.3): zero or more
**evidence sets**. An evidence set is a *minimal* collection of one or more contiguous
spans (each span = a run of whole sentences inside one structural unit) that together
suffice to justify the document's relevance to the topic's stated clinical need
(diagnosis / test / treatment — the topic's `type` field is in the prompt). Semantics:

* **multiple valid locations** — a document may carry several alternative evidence sets;
  containment of *any one complete set* counts (§7);
* **multi-passage evidence** — a set may span multiple units; *all* of its spans must be
  contained to count;
* **"no localizable evidence"** is a legal verdict (relevance is diffuse/aboutness-only);
  the pair then contributes no evidence units, and the verdict rate is reported per grade;
* labels are expressed as character offsets into the exact indexed text, derived from
  sentence indices over **structural units (JATS `<sec>`), never any chunker's cuts** —
  the §7 circularity rule (PIL) carried forward verbatim.

#### 6.2.1 Frozen definitions (review item 8 — these were missing or scattered; they are now normative)

These four definitions are part of the freeze. Nothing in the analysis may be computed
until they are fixed, because each one silently changes the endpoint's value and its
variance.

**D1 — span.** A *span* is a contiguous run of **whole sentences inside exactly one
structural unit** (JATS `<sec>`), identified by `(unit_id, first_sentence_idx,
last_sentence_idx)` and materialised as a `[start_char, end_char)` half-open interval into
the exact indexed text. Sentence segmentation is the one produced by
`ragstack.ingestion.jats.article_prose` at the pinned commit; its sha256 is in the
manifest. A span never crosses a `<sec>` boundary; evidence that does is expressed as a
multi-span set.

**D2 — evidence set.** A *minimal* collection of one or more spans that together justify
the document's relevance to the topic's stated clinical need. Minimal = no proper subset
of its spans suffices (prompt-side demand + the §6.4.3 shrinkage audit).

**D3 — evidence unit** (the endpoint's denominator atom). One unit per **(document,
evidence-set)** *after* the following deduplication, applied in this order and recorded
with counts at each step:

1. **Within-document merge.** Two evidence sets of the same document are merged into one
   unit iff their **character-span union Jaccard ≥ 0.5** — the same threshold the
   self-consistency check already uses (§6.4 rule 4), reused deliberately so one number governs
   both. Merged unit = the **intersection-preserving union**: the union of spans, with the
   *smaller* set retained as the unit's canonical span list (coverage of the canonical list
   is what is tested), so merging can never make a unit easier to cover.
2. **Within-document containment.** If set *A*'s spans are a subset of set *B*'s spans,
   *A* is retained and *B* is dropped: alternative locations survive, strictly weaker
   restatements do not.
3. **Across-document: no merging.** The same finding restated in two different documents
   is **two units**. This is deliberate — retrieving either document covers its own unit,
   and collapsing them would make coverage depend on how many documents happen to repeat a
   result. The redundancy it introduces is *within*-topic correlation, which is exactly
   what ρ (§8.5.2) measures and no longer assumes away.
4. **Cap.** ≤ 12 units per topic by seeded subsampling (§7.4), applied *after* 1–3, with
   the cap rate reported. Subsampling is **stratified by source document** so a single
   heavily-labeled document cannot monopolise a topic's denominator.

**D4 — covered.** A unit is *covered* at budget B iff **every span of its canonical span
list is fully contained** in the packed context's per-document character-span union —
full containment of the whole span, not intersection, not a token fraction, not partial
overlap. Rationale: a span is already minimal by D2, so a partially supplied span is by
construction not sufficient evidence, and any fractional-overlap rule would need a
threshold that no product consequence can justify. Two consequences are accepted and
reported rather than hidden: (i) a chunk boundary falling mid-span makes the unit
uncovered even though most of the evidence was supplied — this is a real cost of chunking
and the endpoint is *meant* to charge it; (ii) `EUC` is therefore a **lower bound** on
"evidence the generator could have used". The **overlap-tolerant variant** (unit counted
covered at ≥ 0.9 character overlap) is computed and reported as a pre-registered
descriptive column so the size of (i) is visible; it is never the primary.

### 6.3 The labeling set (pooling, with a bias bound)

Pairs labeled per topic:

1. **Pooled:** every grade ≥ 1 document appearing in the union of the top-20 documents of
   any scoring arm × query-variant (union expected ≈ 15–35 relevants/topic). Pooling is
   what bounds cost; it cannot bias the primary endpoint's numerator because a document
   outside every pool can never be packed into any arm's context.
2. **Bias-bound sample:** 10 additional grade ≥ 1 documents per topic drawn seeded from
   outside the pool (or all remaining, if fewer). These feed the evidence-recall secondary
   and measure how much labelable evidence the pools miss.

Expected volume: ≤ 45 pairs × 80 topics ≈ **≤ 3,600 pairs** (+ ~450 dev pairs in Stage 0).

**Order-of-operations note:** pooling requires retrieval to have run. Retrieval outputs for
confirmation topics are computed by the harness but **quarantined** (written to disk,
unread by humans/analysis) until labels are frozen; the labeler pipeline consumes only the
pooled (topic, doc) id list. The PREREG hash covers the analysis code before any
quarantined output is opened (§11, §P.9).

### 6.4 Protocol rules (imported from D-SUF, adapted from nuggets to spans)

1. The labeler sees: topic (`summary` + `description` + `type`), the document segmented
   into numbered structural units with numbered sentences. It never sees any ranking, any
   chunk boundary, or any other arm artifact. Labels are blind to arms by construction.
2. **Quote or it did not happen:** every span carries verbatim anchor quotes (first and
   last 10 words); a deterministic checker verifies substrings against the document;
   failure → one re-prompt → drop the pair and increment the **hallucinated-span rate**
   (gate: ≤ 0.05).
3. **Minimality prompt-side + audit:** the prompt demands the smallest sufficient set; a
   10% audit sample is re-prompted with "remove any span not strictly needed"; shrinkage
   rate reported.
4. **Self-consistency:** 10% duplicates at a different unit-presentation order;
   consistent iff primary-set char-span Jaccard ≥ 0.5; gate: ≥ 0.90 consistent.
   (Temperature 0 + seed recorded; vLLM continuous batching means temp-0 ≠ deterministic —
   measured, not assumed.)
5. Prompt sha256s, served model id (asserted per the `pilots/mango.py` `served_model()`
   pattern), sampling params, and concurrency (≤ 4, shared host — non-negotiable) recorded
   in the manifest.

### 6.5 Long documents

Documents over **48,000 Scout tokens** are labeled per top-level section group in windows
of ≤ 48k with the topic re-stated, results unioned; the windowed-document rate is recorded
(expected ≪ 1%: 18 of 4,053 dev-pilot docs exceeded 50k SFR tokens, and S2 showed the
outliers move nothing).

### 6.6 Human validation, and how label error is bounded

**Revision 2 rebuilt this section** (review item 7). The previous version specified a
sample size and no acceptance criterion, which is not a gate. Two facts forced the
rebuild:

* **40 pairs cannot certify label quality.** Zero errors in 40 leaves a one-sided 95%
  upper bound on the error rate of **1 − 0.05^(1/40) ≈ 7.2%** — an undetected 7% label
  error rate is larger than the ε = 0.05 margin the study is trying to resolve. Any read
  that can only ever conclude "≤ 7%" is not evidence about a 5-point question.
* **The 40-vs-60 inconsistency is resolved**: the two reads had different purposes and the
  old text let them be confused. They are now named separately and both are enlarged.

#### 6.6.1 The rubric is written and frozen *before* any labeling call

No label — dev or confirmation — is generated until `design/RUBRIC-evidence.md` exists,
its sha256 is recorded, and both readers have signed off on it. The rubric must settle, in
writing, with worked examples drawn **only from dev topics**:

| question | must be answered by the rubric |
|---|---|
| what is a **distinct** evidence unit vs a redundant restatement | D3's merge/containment rules (§6.2.1) with ≥ 5 worked examples, including the near-duplicate case |
| does **"covered"** require sufficiency or mere span intersection | D4: full containment of every span of the canonical list; the ≥ 0.9-overlap variant is descriptive only |
| may **several retrieved chunks jointly cover one unit** | **Yes** — containment is evaluated against the per-document character-span union of *all* admitted chunks of that document, so a multi-span set may be satisfied by several chunks, and a single span split across two adjacent admitted chunks counts as covered iff the union covers it contiguously. Chunks from *different* documents never combine (a unit is document-local by D3) |
| **missing evidence** (the document is relevant but the labeler found nothing) | legal verdict "no localizable evidence"; contributes no units; rate reported per grade; a topic driven below the §8.5.6 unit floor by such verdicts is excluded there, not here |
| **incorrect spans** (quote verifies, location wrong) | verdict `wrong-location`; counts as a label error; enters the perturbation bound |
| **ambiguous units** (readers disagree on whether it is one unit or two) | adjudication (§6.6.3); if adjudication cannot resolve it, the unit is marked `ambiguous` and a sensitivity is run with those units dropped |
| what triggers **rubric revision / relabeling / gate failure** | §6.6.4, in advance |

#### 6.6.2 The two reads, both enlarged, both two-reader

| read | when | size | drawn from | purpose |
|---|---|---|---|---|
| **R-dev (protocol validation)** | Stage 0, before freeze | **≥ 100 pairs** (was 40) | dev topics | can the rubric be applied consistently at all; κ(human–human), κ(Scout–human); is Scout's error rate small enough to proceed |
| **R-conf (label-quality bound)** | after confirmation labeling, blind, before unblinding | **≥ 100 pairs** (was 60) | confirmation topics | the error rate that feeds the §6.6.5 perturbation bound on the actual labels used |

**Two independent readers read the same blinded subset** in both reads — this is no longer
"strongly preferred", it is required, because κ(human–human) is what caps the strength of
every claim in the report (D-SUF). Readers see the topic and the segmented document; they
never see any ranking, any arm, any chunk boundary, or each other's verdicts. Order of
pairs is independently shuffled per reader.

**Sizing.** ≥ 100 pairs per read is the minimum at which a shortfall is *detectable*: zero
errors in 100 gives a one-sided 95% upper bound of 3.0% (below ε); 5 errors in 100 gives
Wilson upper 10.9%, which is unambiguously a fail rather than a shrug. Both reads are
powered for detection, not for reassurance.

**Stratification — deliberately toward hard cases, not a random sample.** Each read draws,
per stratum, with the seeded sampler recorded:

| stratum | share | why |
|---|---|---|
| **model positives** (Scout returned ≥ 1 evidence set) | 40% | the labels the endpoint's numerator actually uses |
| **model negatives** ("no localizable evidence" verdicts) | 25% | the only way to detect **omission**; a model that silently declines to label is invisible in a positives-only audit |
| **deep-section attributions** (Scout's spans sit outside the abstract/intro) | 20% | the specific Phase-0 failure mode: evidence attributed to a deep section that actually lives in the abstract (S3/§13.3's aboutness bias, and the §7a oracle's head-vs-deep argmax question). A reader checks whether the abstract already contained the same evidence |
| **long documents** (top doc-length tercile, incl. §6.5-windowed docs) | 15% | windowing is where multi-span sets are most likely to be truncated |

Within each stratum, pairs are drawn seeded and the draw is recorded before any pair is
read.

**Omissions are audited, not just supplied spans.** For every pair in the read — including
every model negative — the reader answers *both* questions: (a) is each supplied span
correct and minimal? (b) **is there evidence in this document that the labeler did not
supply?** Verdicts per pair: `correct` / `wrong-location` / `non-minimal` /
`missed-evidence` / `correctly-none` / `ambiguous`. The `missed-evidence` rate is reported
separately from the `wrong-location` rate and is the one that can bias a *contrast*: a
labeler that systematically misses evidence in, say, methods sections removes units that
one arm's chunking would have covered. This is why omission has its own gate below.

#### 6.6.3 Agreement statistics and adjudication

* **κ(human–human)** on the unit-level covered/not and on the pair-level verdict, with
  95% CIs; disagreements are **adjudicated by joint read**, the adjudicated verdict is the
  one used, and the pre-adjudication κ is the one reported (post-adjudication agreement is
  1.0 by construction and means nothing).
* **κ(Scout–human)** against the adjudicated verdicts, plus **positive-class agreement**
  (the fraction of human-positive units Scout also called positive, and conversely) —
  reported because κ is deflated by prevalence and this endpoint's prevalence is skewed.
* **κ(Scout–Qwen)** on the same subset for the cross-judge cluster analysis (§6.6.6).

#### 6.6.4 Acceptance criteria — fixed now, in advance

| statistic | value | consequence |
|---|---|---|
| κ(human–human) | **< 0.40** | `RUBRIC_FAILURE` — the rubric is not applicable by humans; **the study stops**; no amount of model agreement rescues it |
| κ(human–human) | 0.40 – 0.60 | rubric revised once (revision dated, diffed, sha256 recorded), **R-dev re-read on a fresh ≥ 100-pair draw**; a second shortfall is `RUBRIC_FAILURE` |
| κ(Scout–human) | **< 0.40** | stop; Scout is not a usable labeler for this rubric |
| κ(Scout–human) | 0.40 – 0.60 | proceed **only** at capped claim strength: every confirmatory verdict is reported `MODERATE`, and the abstract may not state a decision as established |
| κ(Scout–human) | **≥ 0.60** *or* positive-class agreement **≥ 0.85** | full-strength claims permitted |
| label-error rate (`wrong-location` + `non-minimal`, Wilson upper) | > 0.10 | relabel the affected stratum with the revised prompt; if still > 0.10, the study reports label-limited and no NI conclusion is drawn |
| **`missed-evidence` rate** (Wilson upper) | > 0.15 | pool bias is unbounded → the evidence-recall secondary (§7.5) is promoted to a reported limitation and NI claims are downgraded to UNRESOLVED-BY-LABEL-OMISSION |
| hallucinated-span rate (§6.4 rule 2) | > 0.05 | stop (unchanged) |
| self-consistency (§6.4 rule 4) | < 0.90 | stop (unchanged) |

The κ tiering is deliberately continuous with the existing P.5 gate (≥ 0.40 to proceed,
≥ 0.60 for full-strength claims) rather than replacing it: review item 7 asked for
κ ≥ 0.6 human–model, and that is now the **full-strength** threshold on a read four times
larger than the one it was originally attached to. The alternative — a hard stop below
0.60 — was rejected because it would discard a run whose own report would have said
`MODERATE` honestly.

#### 6.6.5 The perturbation bound (unchanged in mechanism, enlarged in input)
Let p̂ (Wilson 95% upper bound p_u) be the observed label-error rate from **R-conf**.
Sensitivity: re-run the primary contrasts with a random p_u fraction of labels perturbed
(contained ↔ not, 1,000 draws); report the worst-case shift of each confirmatory CI. If
any confirmatory verdict flips inside the perturbation envelope, that verdict is
downgraded to UNRESOLVED-BY-LABEL-ERROR. **Second envelope, new in revision 2:** the
`missed-evidence` rate is perturbed in the *other* direction — units the readers found and
Scout did not are added back at the observed rate, drawn from the observed distribution
over structural-unit position, and the contrasts recomputed. Omission and commission are
reported as two separate envelopes; the verdict must survive both.

#### 6.6.6 What this cannot bound, said plainly

*Correlated* label error — a systematic Scout bias toward, say, results-section prose — is
not random flipping. Two mitigations, and they are mitigations, not fixes: labels are
anchored to structural units no chunker produces (so a bias cannot align with any arm's
cuts), and the κ(Scout–Qwen) + κ(Scout–human) statistics locate disagreement clusters,
which are read and reported. Everything not human-read **remains model-derived, and the
report must say so in the results table header, not a footnote.**

**The cross-family argument, at its true strength** (review item 7). Using Llama-4-Scout
rather than the `bge` reranker removes **self-agreement circularity** — the defect that
sank the §7a oracle, where gold was chosen by the same model that ranks. It does **not**
remove **shared bias**: Scout, `bge-reranker-v2-m3` and SFR-Embedding-Mistral are all
trained on overlapping web/biomedical text and all plausibly share a preference for
lexical overlap with the query, for abstract-like prose, and for early-document position.
If that shared preference exists, it inflates coverage for whichever arm's chunks happen
to look most query-like — which is not neutral across chunk sizes. Cross-family buys
independence of *identity*, not independence of *inductive bias*. The design's actual
protections against shared bias are the structural-unit anchoring, the human read
(§6.6.2), and the deep-section stratum specifically; the report states this limitation in
these words rather than claiming independence.

---

## 7. Endpoints, budgets, and packing — completely specified

### 7.1 The retrieval pipeline the endpoint sits behind (offline harness, production-shaped)

Per (arm, topic, query-variant):

1. **Dense leg:** exact brute-force cosine (numpy, fp32 math) of the query embedding
   against all chunk embeddings; take the top **D = 50** chunks (production:
   `depth = max(top_k, rerank_candidates=50)` — CODE `config.py:826`,
   `query.py:_retrieve_fused`).
2. **Rerank the full pool:** all 50 (query, chunk) pairs through `:50052`
   (`bge-reranker-v2-m3`, 4,096-token truncation — every arm's chunks fit under it,
   PLAN-C). **Never one-chunk-per-document** — the Phase-0 harness's defect-4 shortcut
   (P3's "top-100 docs' best chunk") is retired.
3. **Pack** the reranked list under the budget rule (§7.3).

**The endpoint is computed behind the reranker — stated explicitly, not implied**
(review item 8). Every `EUC@B` value in this design, primary and secondary, offline and
serving-path, is packed from the **reranked** list produced by step 2. No `EUC` is ever
computed on the raw dense ranking. This is Phase-0's clearest lesson: dense-only and
reranked orderings of the same configs correlate only ≈ +0.55 (PIL §4), so a
configuration's dense-stage behaviour does not predict its behaviour in the pipeline that
ships. **Anything that ships behind a reranker must be evaluated behind one.** A
dense-only `EUC` column *may* be reported as a descriptive contrast (it is free — the pool
is already materialised), and if reported it carries the label "not the endpoint; retained
only to quantify the rerank stage's contribution".

No BM25/RRF in the offline harness — the hybrid stack is Stage 2 (§9), where its
agreement with this harness is a blocking gate. No graph leg (dev corpus has no KG).
`max_per_doc = 0` and boilerplate demotion off (production defaults, CODE
`config.py:775-784`).

### 7.2 Tokenizers — three, never conflated

* **Budget tokens:** the **generator's** tokenizer — the tokenizer of the served
  `LLM_MODEL` probed live at freeze (`/rag/config/unified.env` names Scout; the probe, not
  the env file, is authoritative — the D-PROV lesson that the plan's named generator and
  the run's actual generator diverged once already).
* **Embedding tokens** (SFR) and **reranker tokens** (`:50052`'s tokenizer): reported
  separately per arm as descriptive columns; they never enter a budget decision.

### 7.3 The packing rule (A1, disambiguated)

Walk the reranked list from rank 1:

* the **rank-1 chunk is always admitted**, even if alone over budget (PR Addendum A1,
  verbatim);
* each subsequent chunk is admitted iff (cumulative **raw supplied generator-tokens** +
  its own) ≤ B; **the walk stops at the first chunk that does not fit** — no skip-ahead.
  A1's "admitted only while the cumulative total stays ≤ B" is ambiguous on this point;
  stop-at-first-non-fit is chosen because skip-ahead would let variable-size arms
  (`parent256`) cherry-pick fillers no serving path would fetch. For uniform-size arms the
  two readings coincide, so continuity with the RR/BK-R **rule** is exact — the *numbers*
  will not reproduce, because budgets here are counted in the generator's tokenizer where
  RR/BK-R counted SFR tokens; nobody should try to reconcile the tables cell-for-cell;
* **chunks are never truncated** — a chunk (or parent) is admitted whole or the walk
  ends. The one exception to "ends" is a duplicate parent (already admitted): it is
  skipped at zero cost and the walk continues, since it consumes no budget;
* **raw supplied tokens**: overlapping text is counted every time it is supplied —
  overlap duplication paying its own budget cost *is* the overlap tax under measurement.
  Containment (§7.4) is evaluated on the per-document **character-span union** of admitted
  chunks. Both totals — raw supplied and deduplicated-union — are reported per arm;
* `parent256`: the parent replaces the child before counting; a parent already admitted is
  skipped (charged once); `header512`: header tokens count (they are supplied);
* the **realised** token total and chunk count are reported beside every metric, per arm
  per budget (BK-P §4 convention).

**The rule in one sentence, so it cannot be misread** (review item 8 asked for exactly
this): *walk the reranked list by rank from 1; admit each chunk whole if it fits in the
remaining budget; **stop at the first chunk that does not fit** (no skip-ahead, no partial
final chunk, never a truncated chunk); rank 1 is admitted even if it alone exceeds B; an
already-admitted parent is skipped at zero cost and does not end the walk.* A partial
final chunk is therefore **never** counted, in either the token accounting or the coverage
union.

**Budgets:** primary **B = 4,096** generator tokens; secondary B ∈ {2,048, 8,192} as
curves. 4,096 is (i) an exact multiple of every fixed arm size (the A1-clean reading),
(ii) the primary of RR/BK-R (continuity), (iii) ≈ the shipping default's context volume
(top_k 5–10 × 512-token chunks), (iv) comfortably inside the generator window.

### 7.4 Primary endpoint

Per topic `t`, pool the labeled evidence across that topic's labeled pairs into **evidence
units** per **D3** (§6.2.1): one unit per (document, evidence-set) *after* the within-
document merge, containment and stratified-cap rules — capped at **12 units per topic**
(cap rate reported). A unit is **covered** at budget B per **D4**: *all* spans of its
canonical span list lie inside the packed context's per-document char-span union (a unit
whose document was not packed is uncovered).

> **`EUC@B` (evidence-unit coverage)** = per-topic fraction of evidence units covered at
> budget B, computed **behind the reranker** (§7.1). **Primary: `EUC@4096` on `summary`
> queries**, paired per topic across arms.

#### Why continuous, and what was wrong with the previous justification

**Revision 2 deleted an invalid inference** (review item 3). The previous text argued: *a
per-topic binary hit resolves at ε = 0.05 only if topic discordance is low, and check 4
measured 10/10 topics changing their top-10 documents between size extremes, therefore
discordance will not be that low.* **This does not follow.** Check 4 measures whether the
ranked *document list* changes; the binary endpoint asks whether *at least one evidence
unit is covered*. Every ranked list can change while every topic still retrieves at least
one document carrying evidence — indeed that is the expected case at these corpus sizes.
The two quantities are not related by any inequality, and the inference is withdrawn.

**The corrected arithmetic**, kept because the bound itself is real even though the
premise attached to it was not: for a paired **binary** endpoint, σ_d ≈ √d where d is the
per-topic discordance rate (RR §5). Under this document's *actual* test — one-sided NI at
α = 0.025, constant 2.802 (§8.5.1) — resolvability at ε needs σ_d ≤ ε√n/2.802, i.e.

> **d ≤ n·ε² / 2.802² = 80 · 0.0025 / 7.851 ≈ 0.025.**

(The previous text used 2.487², giving ≈ 0.032; 2.487 belongs to neither the NI test nor
TOST. Corrected here.) So the binary form is resolvable **only if fewer than ~2 of 80
topics discord** — a genuinely demanding requirement, but one this design has **never
measured** and must not assume in either direction.

**What actually justifies the continuous primary**, stated on its own merits:

1. **It is the better measure of the thing the product cares about.** The question is how
   much of a topic's evidence reaches the generator, not whether *any* did. `ES-Hit`
   saturates: at 4,096 tokens most arms will cover at least one unit for most topics, so
   the binary endpoint spends its resolution on a question already answered.
2. **It uses the labels.** Reducing ~10 labeled units per topic to one bit discards the
   labeling effort that dominates this study's cost.
3. **Its variance is lower — by an amount that depends on ρ, and is no longer assumed.**
   §8.5.2 replaces the independence model with `σ_d² ≈ (p_flip/m)·[1+(m−1)ρ]`.

**Both endpoints are carried.** `ES-Hit@B` is **retained as a pre-registered secondary**
(§7.5, where it already sat) and **Stage 0 now measures per-topic binary discordance
directly** on the dev topics (§8.5.7), so the report can state what the binary endpoint
would have concluded and whether it was resolvable, rather than arguing about it.

**Denominator rules:** topics with < 3 evidence units after labeling are excluded from the
primary (§8.5.6 governs this and every other exclusion; rate reported and folded into
n_retained); the exclusion list is fixed at label freeze, before unblinding, identically
for all arms.

### 7.5 Secondary endpoints (all pre-registered, none confirmatory)

| endpoint | definition |
|---|---|
| `ES-Hit@B` | binary: ≥ 1 evidence unit covered (the product-shaped read; **retained** as a secondary in revision 2, reported with Wilson CIs and with the **measured** per-topic discordance beside it, so its resolvability is a number rather than an assumption — §7.4, §8.5.7) |
| `EUC@B` (overlap-tolerant) | identical to the primary but a unit counts as covered at ≥ 0.9 character overlap rather than full containment (D4); quantifies how much of the primary's signal is boundary-cut loss |
| unit-level coverage difference | the arm difference computed over **all** units pooled across topics, with the topic cluster bootstrap CI (§8.4.3) — the pre-registered sensitivity to the per-topic macro-average |
| **document `nDCG@10`** | graded gains 2^grade−1, restricted qrels, document score = max reranked chunk score in the pool (the corrected rollup — never the dense-winner shortcut). *The brief's named secondary.* |
| evidence-recall (bias bound) | coverage computed over pooled+sample labels; the pooled-vs-sample difference bounds pool bias |
| doc-vs-evidence gap | `DR@k`-style vs unit-coverage curves, descriptive continuity with RR/BK-R, with the closed-form/simulated random-ranking lift overlaid (BK-P §4/§5.1 convention: a uniform permutation of the pooled ranking, 1,000 draws/query, gives the null) |
| budget curves | `EUC@B` at B ∈ {2,048, 4,096, 8,192} |
| cost columns | vectors/doc, index vector count, realised chunk tokens, embed tokens (SFR), rerank pairs and per-size CE throughput, chunking seconds — quality never reported without cost (PLAN-C) |

### 7.6 Manipulation checks (run before any contrast is read)

1. **GOLD packing control:** pack each topic's own labeled spans directly → `EUC` must be
   ≥ 0.95 (metric plumbing).
2. **NEGATIVE control:** contexts packed from grade-0 documents only → `EUC` ≤ 0.05.
3. **Discrimination check (defect 3 closed):** per arm, document Hit@1 over the shared
   corpus must be **< 1.0** and the per-topic top-10 document sets must differ across the
   size extremes for ≥ 25% of topics (check-4 bar; it passed 10/10 on dev).
4. **Budget bind check:** for every arm, realised tokens at B = 4,096 within
   [0.85·B, B] except the A1 rank-1 overshoot cases, which are counted.

---

## 8. Hypotheses, margins, multiplicity, and power

### 8.1 The comparison families — declared before analysis

**NI family (one-sided non-inferiority at margin ε, Bonferroni α = 0.025 each — i.e. the
two-sided 95% CI's upper bound must sit below ε):**

| id | contrast (control − candidate on EUC@4096) | decision it settles |
|---|---|---|
| **N1** | `fixed_tok512` (shipping) − `fixed_tok1024_ov0pct` | adopt 1024/0 for the OA load: 2.29× fewer vectors, ~0.16 TB saved at the ~500k-article target (PLAN-C storage table) |
| **N2** | `fixed_tok512` − `fixed_tok512_ov0pct` | drop the 64-token overlap: 12.5% of the index |

**Superiority family (Holm at α = 0.05 across the three; house bar 0.05 on EUC@4096):**

| id | contrast | decision |
|---|---|---|
| **R1** | `fixed_tok256_ov0pct` − `fixed_tok2048_ov0pct` | *the replication*: does the budget-matched small-chunk advantage (RR §4.2, BK-R §7) survive independent gold, a discriminative corpus, and full-pool reranking? |
| **R2** | `header512` − `fixed_tok512_ov0pct` | build contextual headers into the ingest path? |
| **R3** | `parent256` − `fixed_tok512` | build small-to-parent expansion into the serving path? |

The two families answer disjoint product decisions and are not corrected against each
other; that judgment is stated here rather than discovered later. Everything else in the
run — budget curves, `multi256+1024`, sensitivities, all secondaries — is **descriptive
and labeled so**.

### 8.1.1 The testing hierarchy — declared, because n = 80 was sized for a single test

Review item 9: the §8.5 sample-size arithmetic is the arithmetic of **one** test. Five
confirmatory contrasts and a shelf of secondaries do not inherit it. The hierarchy below
is the commitment; it splits α where the decisions are genuinely parallel and gatekeeps
where they are genuinely conditional.

**Within families — α is split (no gatekeeping).**

* **NI family:** N1, N2 are two independent product decisions (storage vs overlap), both
  wanted regardless of the other's outcome. Bonferroni: **α = 0.025 one-sided each**.
  Family-wise one-sided error 0.05. Power (§8.5) is computed at α = 0.025 accordingly —
  this is the split the constant 2.802 already encodes.
* **Superiority family:** R1, R2, R3 — **Holm at α = 0.05** across the three, as before.
  Holm is uniformly more powerful than Bonferroni and preserves FWER; the ordering is by
  observed p-value, never by preference.

**Between families — no correction, and the reason is a design fact, not a convenience.**
The NI family asks *may we make the index cheaper*; the superiority family asks *should we
change how we chunk or pack*. A false positive in one does not license the other's
decision, and the two are not read as a combined claim anywhere in the report. Declared
here; not revisited after the data.

**Secondaries and sensitivities — gatekept, and never inferential on their own.**

1. A secondary endpoint (`ES-Hit`, document `nDCG@10`, budget curves, overlap-tolerant
   `EUC`, unit-level coverage) is reported **with a CI and no p-value adjustment, labeled
   `DESCRIPTIVE`**.
2. A secondary may **support** a decision only when its own primary contrast resolved. If
   the primary contrast is UNRESOLVED, no secondary may be used to argue the decision in
   either direction — that is the gatekeeping order, and it is the specific failure mode
   ("the primary was inconclusive so we looked at nDCG") this rule exists to forbid.
3. A secondary may **contradict** a decision at any time; a contradiction is reported and
   downgrades the claim, never silently.
4. The **P8 prediction** (nDCG orders arms coarse-first while EUC orders them fine-first)
   is pre-named precisely so that a nDCG/EUC disagreement is read as the expected
   metric-level dissociation and not as a licence to pick the friendlier metric.

**The multiple-comparison ledger is printed in the RESULTS document**: every confirmatory
test, its family, its α, its adjustment, and the count of descriptive rows — so a reader
can see how many looks were taken.

### 8.2 The margin ε, derived from consequences

**ε = 0.05 absolute EUC@4096**, proposed on three grounds and subject to user veto at
freeze:

1. **Product arithmetic.** ε = 0.05 is a drop of **0.05 in macro-averaged per-topic
   evidence coverage** — the mean over topics of each topic's covered fraction. **It is
   not literally "one lost unit in twenty"** (review item 9): units per topic are unequal
   (m ranges over roughly 3–12 after the §6.2.1 cap and floor), each topic contributes
   1/n_retained of the mean regardless of its m, and a topic with 4 units moves the
   macro-average by 0.25/n_retained per lost unit while a topic with 12 moves it by
   0.083/n_retained. The honest reading: *across the topic population, the shipping
   configuration puts a twentieth more of the located evidence in front of the generator
   than the candidate does* — a population-level statement about coverage, not a
   per-question count. The micro-averaged (unit-weighted) equivalent is reported beside it
   (§8.4.3), and the two are expected to differ; where they do, the report says which
   topics drive the gap. In consequence terms, at the dev tenant's observed volume
   (~10² queries/day) a sustained 0.05 macro-coverage deficit is several
   evidence-degraded answers per day, continuously. The prize on the other side of N1 is
   **0.16 TB and ~2.3× fewer vectors** on a host where the full 512-config index is
   0.28 TB (PLAN-C) — real, but modest on local NVMe. One-in-twenty is the largest
   quality price we will state out loud for that prize; anything larger is a product
   regression someone would notice without instruments.
2. **House precedent.** X_P = 0.05 is the established passage-level bar ("the smallest
   difference a practitioner would re-chunk a corpus for", PR §5); a noninferiority margin
   wider than the superiority bar would let an arm be simultaneously "worse than the bar"
   and "noninferior", which is incoherent.
3. **Disclosure.** ε = 0.05 is *also* near the smallest margin n = 80 can power at
   plausible σ_d — and after revision 2's correlation correction (§8.5.2) it may be
   **below** what n = 80 can power at all. The margin is chosen on grounds 1–2; the
   collision with the power ceiling is disclosed, and the pre-committed response to a
   power shortfall is to change the **endpoint's variance** (variant averaging, more
   units) or to accept UNRESOLVED — never to widen ε to fit n (§8.5). If the user vetoes
   0.05 for a tighter margin, the run's answer may be UNRESOLVED, and that is the honest
   outcome.

### 8.3 Test machinery

* **Non-inferiority (one-sided, α = 0.025):** noninferior iff the upper bound of the
  two-sided 95% CI of the mean paired difference (control − candidate) < ε, on **both**
  the paired t (df = n_retained − 1) and the **BCa** topic bootstrap (10,000 resamples,
  seeded). Both or UNRESOLVED. BCa, not percentile — the reviewers' undercoverage
  complaint is accepted and adopted.
  **Naming, corrected in revision 2:** this rule is a *one-sided non-inferiority test*,
  not TOST. TOST requires rejecting at **both** margins (−ε and +ε) and its 80%-power
  constant is z₀.₀₅ + z₀.₁₀ = 2.927 (bounds at α = 0.05) or z₀.₀₂₅ + z₀.₁₀ = 3.242
  (bounds at α = 0.025) — neither is the 2.802 this design uses. The word "TOST" was a
  mislabel of a correctly-specified CI rule; the rule is unchanged, the name is fixed, and
  the power arithmetic in §8.5 now matches it. The test is one-sided **on purpose**: the
  product question is whether the cheaper configuration *loses* more than ε. A candidate
  that turns out *better* than the control is not a failure of non-inferiority, and TOST
  would have wasted power ruling that direction out.
* **Superiority:** paired **sign-flip permutation** (10⁵ draws, seeded) for the p-value,
  BCa bootstrap for the CI; house resolution rule verbatim, in the corrected precedence
  BK-R disclosed: RESOLVED iff |mean| ≥ 0.05 ∧ CI excludes 0 ∧ δ80 ≤ |mean|; else powered
  NULL iff |mean| < 0.05 ∧ δ80 ≤ 0.05; else UNRESOLVED. δ80 = 0.313·σ_d at n = 80.
* Every proportion at 0 or 1 is read on its Wilson interval; no δ80 is ever quoted from a
  Wald SE at the boundary (the standing house hazard, BK-P §5.2.3).

### 8.4 Clustering and dependence

* **Resampling unit: topic** (n = 80), as every Phase-0 analysis clustered.
* **Shared documents across topics** (13,807 pairs on 12,307 docs ≈ 11% re-judgements)
  make topics non-independent through shared gold. Primary analysis treats topics as
  independent (the TREC convention); pre-registered sensitivity: cluster bootstrap over
  the **connected components of the topic–document bipartite graph** on grade ≥ 1 qrels
  (components computed and reported at assembly time; if a giant component swallows most
  topics, that fact is reported and the sensitivity falls back to leave-one-year-out).
* **Year** (2014/2015/2016) is 3 clusters — too few to bootstrap; year-stratified
  estimates and a year-fixed-effect check are reported instead.
* Query variants are replicates within topic, never additional n.

### 8.4.3 Unit-level analysis — pre-registered, not optional (review item 6)

Reducing each topic to a coverage fraction throws away information and manufactures
**heteroscedasticity**: a topic with 3 units has a per-topic estimate with far larger
binomial noise than a topic with 12, yet the macro-average weights them equally. That
unequal noise is precisely what the old "raise the unit cap 12 → 16" fallback was
implicitly working around, which is the wrong instrument for it.

**The pre-registered unit-level analysis** (run for every confirmatory contrast, reported
beside the primary in the same table):

> **Cluster bootstrap over topics, arm difference computed over all units.** Resample the
> n_retained topics with replacement 10,000 times (seed `20260913`); within each resample
> take **all** units of each drawn topic (a topic drawn twice contributes its units
> twice); compute the difference in the **pooled unit-level coverage rate** between arms;
> report the BCa interval. This is unit-level in its estimand, topic-level in its
> resampling — so intra-topic correlation is absorbed by construction and unequal m needs
> no weighting scheme.

A binomial mixed model with a topic random effect (`covered ~ arm + (1|topic)`) is the
stated alternative from the review; the cluster bootstrap is chosen over it because it is
numpy-only (no new dependency in a pinned environment), it makes no link-function or
distributional assumption, and it reuses the BCa machinery already specified for every
other interval in this design. **If a GLMM is later wanted, it is an addition, not a
substitution, and it goes in as a dated amendment.**

**Status of the two analyses.** The per-topic paired test remains **primary** — it matches
the ε whose product meaning §8.2 derives (macro-averaged topic coverage) and it is the
form the margin was set on. The unit-level cluster bootstrap is a **pre-registered
sensitivity that is always reported**, never conditionally. **If the two disagree in sign
or in verdict, the report says so in the results table and the affected decision is
downgraded to UNRESOLVED-BY-ESTIMAND** — a disagreement means the answer depends on
whether topics or units are weighted equally, which is a real finding about the contrast
and not a technicality to be adjudicated after the fact.

**Units are never treated as independent topics.** No analysis anywhere in this design
computes an interval with n = (number of units); the resampling unit is the topic in every
interval that is reported. Unit-level data are additionally used **diagnostically**: the
per-unit coverage matrix is retained so that correlated failures (all units of a topic
lost together, units in a particular structural position lost together, units lost only in
long documents) can be located and reported — that diagnostic is also where ρ (§8.5.2) is
re-measured on the confirmation data for the record.

### 8.5 Power — gated at Stage 0, on this endpoint's own σ_d

**This section was rebuilt in revision 2** (review items 1, 2, 4, 5, 9). Both reviews
concluded that the gate as previously written could not be frozen: it named one test and
computed another, it assumed independent units, it did not say whether its threshold was a
point estimate or a bound, and it pooled two contrasts that measure different things.

#### 8.5.1 The test, its constant, and the power it actually delivers

The primary is a **one-sided non-inferiority test at α = 0.025** (§8.3). At 80% power and
true difference Δ = 0 the requirement is

> **σ_d ≤ ε·√n / (z₀.₀₂₅ + z₀.₂₀) = 0.05 · 8.944 / 2.802 = 0.1596 ≈ 0.160.**

The superiority bar coincides: δ80 = 0.313·σ_d ≤ 0.05 ⟺ σ_d ≤ 0.160. **The constant
2.802 is the constant of this test**, which is why revision 2 fixed the name rather than
the number. Had the design genuinely meant TOST, the requirement would have been tighter
and the old text's threshold would have over-promised power:

| test | 80%-power constant | σ_d for 80% power at n = 80 | power at σ_d = 0.160 |
|---|---|---|---|
| **one-sided non-inferiority, α = 0.025 (adopted)** | 1.960 + 0.842 = **2.802** | **0.160** | **80%** |
| TOST, both bounds at α = 0.05 | 1.645 + 1.282 = 2.927 | 0.153 | 75% |
| TOST, both bounds at α = 0.025 | 1.960 + 1.282 = 3.242 | 0.138 | 60% |

*(Normal approximation, as in every previous Phase-0 sizing. The exact non-central-t
calculation at df = 79 gives σ_d ≤ **0.158** for 80% power — a 1-point power difference.
Stage 0 recomputes exactly on the measured σ_d and prints the non-central-t value; 0.160
stays the headline constant for continuity with δ80 and every Phase-0 table.)*

**Power under a non-zero true difference — the number that was missing.** The old text
reported power only at Δ = 0, which is the most favourable case that exists. A candidate
arm that is genuinely worse by an amount *inside* the margin is the realistic scenario for
N1 in particular (P3 predicts exactly that: RR §4.2 measured −0.046 for 512→1024 at
budget on Leg B). Power against Δ, exact non-central t, df = 79, α = 0.025, ε = 0.05:

| σ_d ↓ / true Δ → | 0.000 | 0.005 | 0.010 | 0.015 | 0.020 | 0.025 |
|---|---|---|---|---|---|---|
| 0.120 | 95.7% | 91.2% | 83.8% | 73.1% | 59.8% | 45.3% |
| **0.140** (the projection) | **88.4%** | 81.1% | 71.4% | 59.8% | **47.3%** | 35.1% |
| 0.160 (the threshold) | 78.8% | 70.0% | 59.8% | 48.9% | 38.1% | 28.1% |
| 0.177 (m = 16, ρ = 0.10) | 70.4% | 61.3% | 51.5% | 41.6% | 32.2% | 23.8% |
| 0.195 (m = 10, ρ = 0.10) | 62.0% | 53.1% | 44.1% | 35.4% | 27.4% | 20.4% |

**Read the row, not the corner.** At the projected σ_d = 0.14, power is 88% against a
truly identical arm but **47% against an arm that is worse by 0.02** — a difference that
lies *inside* the margin and would, if detected, still support the NI conclusion. A
variance figure alone therefore cannot establish power: **every power statement in this
design names its Δ scenario.** The scenarios that must be reported at freeze are
Δ ∈ {0, 0.01, 0.02} for each NI contrast; the Δ = 0.02 column is the one the decision
should be read against for N1, because the Phase-0 record predicts a real deficit there.

**The "~12% headroom" claim is deleted.** It was computed against the wrong test and
against a variance model (§8.5.2) that assumed away the correlation this endpoint has by
construction. There is no headroom claim in revision 2; there is a measurement and a
three-outcome decision (§8.5.5).

#### 8.5.2 The variance model now carries intra-topic correlation

The old projection was `σ_d ≈ √(p_flip/m)` — the variance of a mean of **m independent**
Bernoulli flips. **The units of a topic are not independent:** they share one query, one
embedding of that query, one retrieval, one reranking and one packed context. If the
retrieval misses the relevant document, *all* of that document's units fail together; if
the topic's query is a poor match for the corpus, the whole topic degrades at once. The
corrected model:

> **σ_d² ≈ (p_flip/m) · [1 + (m−1)·ρ]**, ρ = the intra-topic correlation of unit-level
> flips.

| p_flip = 0.2 | m = 6 | m = 10 | m = 12 | m = 16 |
|---|---|---|---|---|
| ρ = 0 (the old projection) | 0.183 | **0.141** | 0.129 | 0.112 |
| ρ = 0.03 | 0.196 | 0.159 | 0.149 | 0.135 |
| ρ = 0.05 | 0.204 | 0.170 | 0.161 | 0.148 |
| **ρ = 0.10** | 0.224 | **0.195** | 0.187 | **0.177** |
| ρ = 0.20 | 0.258 | 0.237 | 0.231 | 0.224 |

Three consequences, all adopted:

1. **The margin between projection and threshold is an artifact of ρ = 0.** At ρ = 0.10 —
   a modest correlation for units sharing a retrieval — σ_d = 0.195 and the design is
   underpowered. The requirement σ_d ≤ 0.160 survives only for **ρ ≲ 0.03** at m = 10.
2. **The old "raise the unit cap 12 → 16" fallback cannot fix this.** At ρ = 0.10 raising
   m from 10 to 16 moves σ_d from 0.195 to 0.177 — still over threshold — because the
   `1 + (m−1)ρ` term grows with m and cancels most of the 1/m gain. As m → ∞,
   σ_d → √(p_flip·ρ) = 0.141 at ρ = 0.10: **no unit cap whatsoever reaches 0.160 once
   ρ ≥ 0.13.** The fallback is retained in the ladder (§8.5.8) but is now explicitly
   labeled as effective *only* in the low-ρ regime, and it is applied only if the measured
   ρ says it will work.
3. **The same correction applies to the variant-averaging fallback.** Averaging `summary`
   and `description` gives σ_avg = σ·√((1+ρ_variant)/2), so the assumed **÷ 1.3 requires
   ρ_variant ≈ 0.18** — two query formulations of the same clinical case, retrieving over
   the same corpus, and correlated only 0.18? Implausible. At ρ_variant = 0.6 the divisor
   is 1.12; at 0.8 it is **1.05**. Stage 0 **measures** ρ_variant (it computes both
   variants' per-topic differences anyway) instead of assuming it, and the ladder step is
   applied only if the measured divisor is worth the doubled query-side cost.

**Estimation, not assumption.** Stage 0 estimates `p_flip` and ρ **at unit level** from
the ~450 labeled development pairs: for each contrast, form the per-unit covered/not
indicator under both arms, `p_flip` = the marginal rate of units whose coverage differs
between arms, and ρ = the intraclass correlation of the per-unit **difference** indicators
within topics (ANOVA/one-way ICC on the paired difference, with a topic cluster-bootstrap
CI). The model-based σ_d = √((p_flip/m̄)(1+(m̄−1)ρ)) is then reported **beside** the
direct standard deviation of the 10 per-topic differences. Both are printed in the PREREG
slot; if they disagree materially, **the larger governs the gate**, and the disagreement
itself is reported (it usually means m varies enough that a single m̄ misdescribes the
design).

#### 8.5.3 The Phase-0 priors, quoted rather than omitted

They are not reassuring and revision 2 declines to leave them out of the gate reasoning:

| source | measured σ_d | n | what it is |
|---|---|---|---|
| PIL §4, Leg A dense | **0.156** | 10 topics, 276 config pairs | nDCG@10 paired σ_d — **same topics, same retrieval, different metric** |
| PIL §4, Leg A reranked | **0.173** | 10 topics | the reranked ordering, which is the one this design uses |
| PIL §4, band on the 0.156 estimate | **[0.108, 0.286]** | — | the ×0.69–×1.83 χ² band at n = 10 |
| PIL-rerun §5, Leg B | 0.152 (comparable rung) / 0.119 (judged-only rung) | 260 queries | a *different* leg and query style; quoted for scale only |

Two of these three CDS numbers sit **at or above** the 0.160 requirement, on the same 10
topics and the same retrieval stack this design will use. `EUC` is a different metric and
may well be quieter — that is the hypothesis Stage 0 tests — but the honest prior is
"plausibly at the threshold", not "comfortably inside it". *(The brief's "0.152–0.173 on
Leg A" mixes legs: 0.152 is Leg B's own measurement. The Leg A pair is 0.156/0.173.)*

#### 8.5.4 Calibration is per-contrast, because the contrasts differ in kind

The old gate measured two pairs and read one pooled number. The two pairs are not
interchangeable:

* **Maximum separation** (`tok256/0` vs `tok2048/0`, the R1 pair) retrieves substantially
  different chunk sets and carries genuine **cross-topic effect heterogeneity** — some
  topics benefit from small chunks far more than others. That heterogeneity is part of
  σ_d and is entirely absent from the flip-noise model. Its σ_d is the **realistic upper**
  end.
* **Minimum separation** (`tok512/0` vs `tok512/64`, the N2 pair) retrieves nearly
  identical sets; most topics will show a difference of exactly 0, σ_d will be small, and
  the contrast **will look well powered whether or not the design is**. Its σ_d is a
  **lower** bound that generalises to nothing else.

**Rule adopted:** *the max-separation σ_d governs the size contrasts (N1, R1, and R3,
which changes what is packed); the min-separation σ_d governs the overlap contrast (N2)
only.* Reading a single pooled number is wrong in either direction — it would declare N2
underpowered on R1's variance, or R1 powered on N2's.

**Preferred, and affordable here:** calibrate **every designated primary comparison
directly**. Stage 0 has all six index arms built on the shared corpus (§11, revised), so
the five confirmatory contrasts (N1, N2, R1, R2, R3) can each be run on the 10 dev topics
at their own arm pair, at essentially zero marginal fleet cost beyond the embeds already
required. **The max/min-proxy scheme is the fallback**, used only if an arm is unavailable
at Stage 0, and any contrast left uncalibrated is **named explicitly** in the PREREG slot
and in the results table — never covered by another contrast's number.

#### 8.5.5 The gate — three outcomes, on a pre-declared variance bound

The old gate said "dev σ_d ≤ 0.160" without saying whether that was the point estimate or
its upper bound. Neither reading works alone:

* Read as the **upper bound at 95%**: the ×1.826 factor (χ², df = 9, one-sided 97.5%)
  requires a point estimate ≤ 0.160/1.826 = **0.087** — far below the 0.14 the design
  hopes for, so **a design working exactly as intended would still fail its own gate**.
* Read as the **point estimate**: a pass carries little evidential weight, because at
  n = 10 the estimate is uncertain by nearly a factor of two in each direction.

**Adopted, verbatim, as the gate's decision table:**

| calibration outcome | decision |
|---|---|
| required power holds at the chosen upper variance bound | **power gate passes** |
| holds at the point estimate but fails at the upper bound | **power remains uncertain** |
| fails even at the point estimate | **apply permitted adaptations, or classify as underpowered** |

**What each outcome does — because a gate whose middle row has no consequence is the old
ambiguity one level up.** Row 1: freeze as specified. **Row 2 (`power remains uncertain`):
the freeze may proceed**; the PREREG prints power at *both* the point estimate and the
80% bound, for every Δ scenario; the affected contrasts carry a **`POWER-UNCERTAIN`**
annotation in every results table and in the summary; no adaptation from §8.5.8 is forced
and none is forbidden — if one is applied, it is recorded with its measured justification.
Row 3: §8.5.8's ladder, then the closing clause. **Row 2 is the outcome this design should
expect to see** (§13: both variance instruments are weak at n = 10), which is exactly why
its consequence is written down here, before the measurement, rather than decided at
freeze time with the number in hand.

**The bound's confidence level, pre-declared: the one-sided 80% upper bound** (χ², df = 9:
factor **×1.293**). Rationale: the gate is a design decision under uncertainty, not a
hypothesis test, and a 95% bound would demand a point estimate (≤ 0.124 → ≤ 0.087) that no
plausible design achieves, converting the gate into a guaranteed "underpowered" verdict.
All three levels are **printed** so no reader is bound to this choice:

| bound level (df = 9) | multiplier | point estimate that still passes |
|---|---|---|
| **80% one-sided (the gate)** | **×1.293** | **σ̂_d ≤ 0.124** |
| 90% one-sided | ×1.469 | σ̂_d ≤ 0.109 |
| 95% one-sided | ×1.645 | σ̂_d ≤ 0.097 |
| 97.5% one-sided (= the ×1.83 of the two-sided 95% band) | ×1.826 | σ̂_d ≤ 0.087 |

**Distributional caveat, stated rather than buried:** the ×0.69–×1.83 interval and the
multipliers above come from the **χ² distribution of a sample variance under normally
distributed paired differences**. The paired differences of a *bounded* coverage endpoint
at n = 10 are neither normal nor unbounded — they are differences of fractions on [0,1],
frequently exactly 0, and skewed. **The multipliers are therefore an approximation, not a
distribution-free guarantee.** Stage 0 additionally computes a **bootstrap upper bound on
σ_d** (10,000 topic resamples of the 10 dev differences, 80th percentile of the resampled
SD), and **the gate uses the larger of the χ² and bootstrap bounds**. At n = 10 the
bootstrap is itself unreliable; that both instruments are weak at n = 10 is a limitation
of calibrating on 10 topics and is reported as such in §13.

#### 8.5.6 Exclusions, and the n that power is computed on

Review item 9: n = 80 is the count *before* any exclusion. The criteria are fixed **now**,
before Stage 0, and applied identically to every arm:

| criterion | rule | applied when |
|---|---|---|
| **too few units** | topic excluded if it has **< 3 evidence units** after D3 dedup | at label freeze |
| **label failure** | topic excluded if > 1/3 of its labeled pairs were dropped by the §6.4 rule 2 quote-verification failure path | at label freeze |
| **no fetchable relevants** | topic excluded if it has < 5 grade ≥ 1 documents present in the assembled corpus | at corpus assembly (non-outcome data — knowable *now*) |
| **windowing failure** | topic excluded if > 1/2 of its labeled documents required §6.5 windowing *and* the windowed union failed the self-consistency check | at label freeze |

Exclusions are **arm-invariant by construction** (all four criteria are computed from
labels and corpus composition, never from any arm's ranking) and the exclusion list is
frozen before unblinding. **No topic is ever excluded on the basis of its outcome, and no
excluded topic is replaced** — there is nothing to replace it with (§2.1: no reserve).

**Power is computed on n_retained, not on 80.** The requirement scales as √n:

| n_retained | σ_d for 80% power at ε = 0.05 | δ80 multiplier |
|---|---|---|
| 80 | 0.160 | 0.313·σ_d |
| 76 | 0.156 | 0.321·σ_d |
| 72 | 0.151 | 0.330·σ_d |
| 68 | 0.147 | 0.340·σ_d |
| 64 | 0.143 | 0.350·σ_d |
| 60 | 0.138 | 0.362·σ_d |

**Stage 0 must produce an expected-exclusion estimate** (from the dev topics' unit counts
and drop rates, plus the confirmation topics' *known* qrels counts, which are non-outcome
data) and **the gate is read at the projected n_retained**, with n = 80 shown alongside as
the optimistic bound. A projected loss of more than 20 topics (n_retained < 60) is itself
a gate: the design is declared underpowered at freeze and the run proceeds under §8.5.8's
terms or not at all.

#### 8.5.7 What Stage 0 must measure — the complete list

The gate cannot be read from a single number, so the Stage-0 deliverable is a table, and
every row below is `[FROZEN-AT-STAGE-0]` in P.7:

1. **σ_d(EUC@4096) per confirmatory contrast** (N1, N2, R1, R2, R3 directly if the arms
   are built; otherwise max/min proxies with the uncalibrated contrasts named), each with
   its point estimate, χ² 80/90/95% upper bounds, and bootstrap 80% upper bound.
2. **`p_flip` and ρ at unit level**, per contrast, from the ~450 labeled dev pairs, with
   the model-based σ_d = √((p_flip/m̄)(1+(m̄−1)ρ)) beside the direct per-topic SD.
3. **m̄ and the full distribution of units per topic** after D3 dedup and the cap, and the
   cap-hit rate.
4. **Per-topic binary discordance** for `ES-Hit@4096` on each contrast (review item 3):
   the measured d, and whether d ≤ 0.025 (§7.4) — i.e. whether the binary secondary is
   resolvable at all, reported as a fact rather than argued.
5. **ρ_variant**, the correlation between `summary` and `description` per-topic
   differences, and hence the *measured* divisor √(2/(1+ρ_variant)) — the variant-averaging
   fallback's actual value.
6. **Projected n_retained** from the §8.5.6 criteria, and the requirement re-read at it.
7. **Power against Δ ∈ {0, 0.01, 0.02}** at the governing σ_d for every confirmatory
   contrast (§8.5.1's table, recomputed on measured values).
8. **The label-validation table** (§6.6.4): κ(human–human), κ(Scout–human),
   positive-class agreement, `wrong-location`, `non-minimal` and `missed-evidence` rates
   with Wilson bounds, from the ≥ 100-pair two-reader R-dev read.
9. **The manipulation checks** (§7.6) and the dev `EUC` level, which must sit in
   [0.15, 0.90] or B/D are recalibrated (a floor or ceiling effect destroys variance
   estimates as surely as any of the above).

#### 8.5.8 Permitted adaptations, and what happens if calibration stays inadequate

Applied **in this order**, re-measuring after each, and only where the measurement says
the step will help:

1. **Variant averaging** — primary becomes the mean over `summary` + `description`.
   Applied only if the *measured* ρ_variant gives a divisor ≥ 1.15; the assumed ÷ 1.3 is
   not used as a justification for anything.
2. **Unit cap 12 → 16** where labels exist. Applied only if the *measured* ρ ≤ 0.05, where
   the table in §8.5.2 shows it actually moves σ_d; at higher ρ it is a cost with no
   benefit and is skipped, with the reason recorded.
3. **Re-scope the confirmatory set.** If only some contrasts pass, **the passing contrasts
   remain confirmatory and the failing ones are re-declared exploratory** before freeze,
   with α redistributed within the surviving family (a two-contrast NI family that loses
   one member becomes a single test at α = 0.025 one-sided; it does **not** widen to 0.05).
   This step is new in revision 2 and is preferred over degrading the whole run.
4. **ε does not move.** Widening the margin to fit the power available is forbidden here
   and no measurement can license it.

**If calibration remains inadequate after steps 1–3**, the previous text said "freeze
anyway". Revision 2 replaces that wording, verbatim, with:

> **If calibration remains inadequate, the run may proceed under the frozen procedure with
> its projected power and limitations stated. Failure to establish a difference must not
> be interpreted as equivalence or used to prune configurations.**

Operationally that means: the affected contrasts are pre-declared **LIKELY-UNRESOLVED** in
the PREREG with their projected power against Δ ∈ {0, 0.01, 0.02} printed; the RESULTS
document reports them as UNRESOLVED unless they resolve; **no arm is dropped, no index
configuration is changed, and no storage decision is taken on the strength of an
unresolved contrast**; and the report's summary states that the run was underpowered for
those contrasts, in the summary, not in a footnote.

### 8.6 Predictions, to be scored (house convention: failures are reported, not dropped)

| # | prediction |
|---|---|
| P1 | **R1 > 0 and RESOLVED** — the budget-matched small-chunk advantage replicates on independent gold at n = 80 |
| P2 | **N2 concludes noninferior** — overlap buys nothing at budget (extends the two-leg powered nulls to the evidence endpoint) |
| P3 | **N1 fails to conclude noninferiority** — at fixed budget the 1024 arm loses real coverage (RR §4.2's H_B@4096 drop 512→1024 was −0.046 on Leg B); the storage saving is predicted to cost more than ε |
| P4 | R2 (headers) positive but small: 0 < mean < 0.05, likely UNRESOLVED |
| P5 | R3 (parent) positive: parent packing recovers the context large chunks carry while keeping small-chunk targeting |
| P6 | the Stage-2 hybrid serving stack **preserves the sign** of every confirmatory contrast (concordance holds) |
| P7 | the SFR instruction prefix moves absolute EUC ≤ 0.02 and flips no contrast sign |
| P8 | document nDCG@10 orders arms **coarse-first** while EUC orders them **fine-first** — the Leg-A aboutness bias (S3/§13.3) reappears as a metric-level dissociation; pre-naming it so it is read as the expected artifact, not a discovery |

**Scoring a prediction is not a licence to read an underpowered contrast as a result**
(revision 2). Each prediction is scored against its contrast's *declared* verdict, and a
prediction whose contrast returned UNRESOLVED is scored **"not testable at the achieved
power"**, with that contrast's power against Δ ∈ {0, 0.01, 0.02} printed beside it. **P3
in particular** — which predicts that N1 *fails* to conclude noninferiority — must not be
counted as confirmed merely because the contrast was underpowered: failing to conclude for
want of power and failing to conclude because the deficit genuinely exceeds ε are
different outcomes, and P3 is scored only on the latter.

---

## 9. Stage 2 — serving-path validation (the actual retriever)

**Purpose:** defect 4 is only closed when the conclusions survive the code that serves
users, not a harness shaped like it.

* **Arms:** the shipping control + every arm that any confirmatory contrast promoted
  (NI-passed or superiority-RESOLVED) + `fixed_tok256_ov0pct` (R1's winner candidate).
* **Stores:** dev tenant **only** — Qdrant `:24041`, Elasticsearch `:24043`; collection/
  index names prefixed `chkconf_<runid>_`; deleted afterwards with a verifying listing;
  `:6333`/`:9200` never contacted. Vectors are **loaded from the Stage-1 `.npy`
  artifacts** (no re-embedding, ~0 GPU).
* **Path:** the real `HybridRetriever` + `_retrieve_fused` (in-process against the same
  pinned commit), production defaults explicit: `candidate_multiplier=2`, `rrf_k=60`,
  `rerank_candidates=50`, `max_per_doc=0`, boilerplate demotion off.
* **Matrix per arm:** mode ∈ {`vector`, `hybrid`} × rerank ∈ {on, off} × top_k ∈ {5, 10}
  × `context_expand` ∈ {0, 1}; sensitivity row `max_per_doc=2`.
* **Frozen candidate pools:** for every (arm, topic, variant, mode) the fused pre-rerank
  pool (chunk ids, doc ids, char spans, per-leg ranks, scores) is persisted as JSONL,
  sha256 in the manifest. Rerank-on vs rerank-off is evaluated **on the identical frozen
  pool**, so the reranker's effect is isolated from pool composition; the pools also make
  any future reranker swap a pure re-scoring exercise.
* **Endpoints:** EUC@4096 packed from the served ranking (top_k=10 rows; context expansion
  contributes its neighbours to the packable text, budget-charged) and document nDCG@10.
  Known asymmetry, tolerated: the hybrid stack can surface grade ≥ 1 documents that were
  outside every offline pool and are therefore unlabeled — they consume budget but can
  never cover a unit. The fixed per-topic unit denominator makes this roughly symmetric
  across arms, and the pooled-vs-sample evidence-recall secondary (§7.5) bounds the pool
  miss; the concordance gate reads signs, not levels, and tolerates it.
* **Concordance gate — BLOCKING, not descriptive:** for each confirmatory contrast, the
  sign of the mean paired difference under the hybrid+rerank, top_k=10 serving
  configuration must agree with the offline harness. **A disagreement blocks the
  corresponding ship/build decision** pending investigation; it is never averaged away and
  never footnoted (the PLAN-L concordance standard, made enforceable).

---

## 10. Sensitivity arms (all bounded, all descriptive)

| arm | spec | cost |
|---|---|---|
| **SFR instruction prefix** (defect 8) | re-embed the 160 confirmation queries (+20 dev) with the model-card template `Instruct: Given a clinical case narrative, retrieve passages of biomedical articles that address the case.\nQuery: {q}`; documents unchanged (the SFR recipe instructs queries only); re-run the offline harness on `fixed_tok512` and `fixed_tok256_ov0pct` | ~0 (≤ 0.2M tokens) |
| query variant | full offline repeat on `description` (S3 precedent: ordering reproduced) | CPU + CE minutes |
| grade ≥ 2 | primary contrasts recomputed on grade ≥ 2 evidence pairs only | 0 |
| packing order | packed-context chunk order randomized vs rank order (D-SUF probe: the metric must not be an order artifact — though EUC, unlike an LLM judge, is provably order-invariant; this is a plumbing check) | 0 |
| label-error perturbation | §6.6 | 0 |

---

## 11. Cost, schedule, and the staged decision structure

Rates used (all measured, sources cited; ±2× band on fleet rates — two measurements are
not a benchmark): SFR chunk-embed leg **161k tok/s** (SA §7; 171–198k achieved elsewhere);
CE **1,037 / 786 / 391 pairs/s at 256/512/2048-token chunks** (SB §530); mango prefill
~5.7k tok/s at concurrency 4 (D-SUF); S3 fetch 34–44 articles/s (PLAN-L).

| item | volume | cost | resource |
|---|---|---|---|
| corpus fetch (~38k docs, S3) | ~26k not yet fetched | ~15–20 min | CPU/network |
| chunking, 6 index arms | ~10⁶ chunks total | minutes | CPU |
| **embedding, 6 index arms** | ≈ 1.6B SFR tokens | **≈ 2.8 h** (band 1.4–5.6) | fleet `:9001–:9006` |
| `header512` overhead | ~+8% on one arm | +2 min | fleet |
| query embeds (all variants + instructed) | < 1M tokens | ~0 | fleet |
| CE rerank, 8 arms × 160 queries × 50 pairs | 64k pairs | **~2–3 min** | `:50052` (GPU 0) |
| **Scout labeling** | ≤ 4,050 pairs ≈ 30M prompt tok | **~1.5 h** (band 1–4 h) | mango `:8003` (off-fleet) |
| Qwen second-judge slice + dev κ | ~500 calls | ~20 min | mango `:8004` |
| scoring + statistics | — | CPU, minutes | — |
| Stage 2 serving runs | vector load + ES index + CE | < 0.5 h, ~0 GPU | dev tenant |
| **coconut GPU total** | | **≈ 3.2 h central, ≤ 7 h pessimistic** | |
| **human reading (revision 2)** | R-dev ≥ 100 pairs × 2 readers + adjudication; R-conf ≥ 100 pairs × 2 readers + adjudication | **≈ 8–12 h per reader per read → ≈ 32–48 person-hours total; ≈ 16–24 h wall-clock** if the two readers work in parallel (+ adjudication) | people, not GPUs |

**The human cost is the one that changed in revision 2, and it changed by a lot.** The
previous plan's "~3 h human" bought a 40-pair single-reader read that could not certify
anything (§6.6). Two readers × ≥ 100 pairs × two reads is the dominant *schedule* item in
this study even though it consumes no GPU: at a realistic 4–7 minutes per pair (read the
topic, read the segmented document, check each supplied span, and answer the omission
question) each read is a working day per reader. **This is a scheduling fact the study
lead must accept before Stage 0 starts**, not something to be discovered mid-read and
quietly cut back to 40 — if the second reader cannot be staffed, the honest response is to
say so at freeze and cap every claim at `MODERATE` (§6.6.4), not to shrink the read.

**The whole confirmatory design fits in one night on the fleet, with mango labeling
running off-fleet in parallel.** It is nowhere near the 20 GPU-hour line, so no cut-down
version is *forced* — but the run is staged anyway, because each stage carries a decision
that can stop the next:

**Stage boundaries moved in revision 2.** Corpus assembly and the six index builds now
happen **before** the Stage-0 variance measurement, not after it (review item 8; §4.1).
The total cost is unchanged — the same corpus and the same six embedding passes were
always going to be paid for, and they are non-outcome work that §2.3 already permits at
any time — but the σ_d that gates the freeze is now measured **in the experiment's own
conditions**: 38k documents, production-shaped pool, real distractor density. Blinding is
unaffected: the 80 confirmation topics' retrieval outputs are still not computed until
after the freeze, and nothing about the 80 is read at Stage 0 beyond the non-outcome
columns of §2.3.

| stage | what runs | what it decides |
|---|---|---|
| **0a — corpus and index builds** (≈ 3 fleet-h; no outcome is read) | fetch, assemble and hash the shared ~38k-doc corpus (all 90 topics); chunk and embed all 6 index arms; corpus manifest recorded | nothing — this is infrastructure. Non-outcome work per §2.3. Confirmation-topic **retrieval is not run** here |
| **0b — dev calibration on the real corpus** (dev topics only; ~0.3 fleet-h + ~0.5 h mango + ≈ 8–12 h × 2 readers) | dev retrieval + full-pool rerank + packing against the **full 38k corpus**; dev Scout labels (~450 pairs); **R-dev ≥ 100-pair two-reader read** + κ and adjudication; the complete §8.5.7 measurement table (per-contrast σ_d, `p_flip`, ρ, m̄, binary discordance, ρ_variant, projected n_retained); manipulation checks | the §8.5.5 three-outcome power gate; which adaptations of §8.5.8 apply; which contrasts stay confirmatory; endpoint form; B sanity (dev `EUC` in [0.15, 0.90]); **go/no-go to freeze**. A failed labeling gate (§6.6.4: κ(human–human) < 0.40, κ(Scout–human) < 0.40, hallucinated-span > 0.05, self-consistency < 0.90) stops the study before any confirmation labeling cost |
| **freeze** | PREREG (§P) sha256 recorded; commit pinned; every `[FROZEN-AT-STAGE-0]` slot filled from 0b | nothing runs on confirmation outcomes before this line |
| **1 — confirmation** (≈ 0.2 fleet-h + ~1.5 h mango + ≈ 8–12 h × 2 readers) | confirmation retrieval on the already-built indexes (quarantined), pooling, labeling, **R-conf ≥ 100-pair two-reader read**, unblinding, confirmatory + secondary + unit-level analysis | N1, N2, R1, R2, R3 — the size, overlap, header and parent decisions |
| **2 — serving path** (< 0.5 h, dev tenant) | §9 matrix on the shortlist | the blocking concordance gate; whether any ship decision proceeds |
| **3 — optional scale check** (+~3 fleet-h per arm-pair) | ×1 OA-distractor rung (~38k judged + 38k OA docs) on {shipping, R1 winner}; `multi256+1024` exploratory read | whether the winner's margin survives a 2× harder corpus; input to the eventual ×10 decision, **not** a confirmatory claim |

Stop rules: if fleet time passes 2× projection with builds outstanding, stop and report
the partial grid (per-arm `.npy` artifacts are the checkpoints); if mango labeling passes
2× projection, stop, freeze the labeled subset, and shrink the labeling set by dropping
the bias-bound sample first (recorded).

---

## 12. Flags on the task framing (things the brief states that the record contradicts)

1. **"80 untouched topics" — overstated.** The §7a oracle labeled pairs for all 90 topics
   and breadth-k scored dense k-curves over an 86-topic ladder at the four token sizes
   (BK-R). The defensible claim, adopted in §2.2: held out from all config-selection
   contrasts and from the entire primary-endpoint apparatus; not unobserved.
2. **The "2.2–3.8×" arithmetic-check.** The reviewer reconstructed it from Leg B's
   `PH@k` table and got 1.56–1.68 — but the published figure traces to **BK-R §7's
   budget-matched Leg A table**: `PR_B@4096` tok256/tok2048 = 0.291/0.132 = **2.2×** at
   m = 1 and 0.181/0.047 = **3.8×** at m = 16. The number was real; its provenance was
   not carried with it, which is the actual defect. Standing rule adopted (§1, defect 5):
   every ratio in every report quotes leg + metric + budget + table cell.
3. **"~658 pairs/s crossencoder" appears nowhere in the record.** The measured figures are
   1,037 / 786 / 391 pairs/s at 256/512/2048 (SB §530; simple mean 738). All CE costing
   here uses the per-size rates.
4. **"161k tok/s"** is specifically the chunk-embed leg's rate (SA §7); other legs measured
   171–218k. Immaterial to any conclusion; the ±2× band covers it.
5. **"≤2 in flight"** — the house rule is ≤ 2 in flight **per endpoint** across
   `:9001–:9006` (BK-P §9); this spec keeps the per-endpoint form.
6. **The brief's "one primary metric… with document nDCG as secondary"** is adopted. The
   primary is the *continuous* unit-coverage form and the binary `ES-Hit` is a secondary —
   but **revision 2 corrected the reason**. The previous text asserted the binary form
   "cannot be powered at n = 80" and supported that with an invalid inference from check 4
   (§7.4). The defensible statement is narrower: *the binary form is resolvable at ε = 0.05
   and n = 80 only if per-topic discordance d ≤ 0.025 (≈ 2 of 80 topics), which this design
   has never measured and which Stage 0 now measures directly.* The continuous form is
   primary because it is the better measure of coverage and because it uses the labels the
   study paid for — not because the binary form was proved impossible.
7. Minor: `mango:8003` is not the only live LLM endpoint (`:8000` Qwen-27B and `:8004`
   Qwen-35B are up — D-SUF); Scout remains the right labeler for exactly the brief's
   reasons (cross-family, no reasoning tax), and Qwen-35B is used as the second judge.

---

## 13. What this run cannot establish, even executed perfectly

* **Answer quality.** Evidence coverage at the generator's input is necessary, not
  sufficient — no LLM writes an answer in this design. A config that packs the evidence a
  generator then ignores is not measured.
* **Ground truth beyond the human subsample.** ~97% of evidence labels remain
  model-derived (Scout). The subsample bounds *random* label error; correlated Scout bias
  is mitigated (structure-unit anchoring), located (κ clusters), and disclosed — not
  eliminated.
* **The user query distribution.** CDS case narratives are one realistic clinical query
  style; nothing here speaks for short keyword queries (Leg B-shaped) or citances
  (Leg C) — the multi-leg concordance program in PLAN-L remains the answer to that.
* **Scale.** ~38k documents against a ~500k target (Stage 3's ×1 rung reaches ~76k).
  Qrels pooling incompleteness is real; it is arm-invariant by construction but not
  provably neutral for arm *contrasts* (arms could differ in which unjudged docs they
  surface) — stated, bounded by the judged-only corpus design, not corrected.
* **Anything outside biomedical JATS full text.**
* **ε is a judgment.** The margin is derived from stated product consequences, but another
  owner could weigh 0.16 TB differently; the non-inferiority machinery makes the trade explicit
  rather than settling it.
* **The 80 topics' partial prior exposure** (§2.2) caps the purity of the "confirmation"
  label: this run is best described as *a pre-registered replication on held-out topics
  with new gold, a new endpoint, and the serving path* — which is what the reviews asked
  for, under its honest name.
* **Power, if the calibration lands badly.** §8.5 can conclude that ε = 0.05 is not
  powerable at n = 80 with this endpoint's variance. If it does, the run still executes
  under §8.5.8's terms and its NI contrasts return UNRESOLVED. **An unresolved
  non-inferiority contrast is not evidence of equivalence and must never be used to prune
  a configuration** — the failure mode this whole revision exists to prevent.
* **Calibration is done on 10 topics, and 10 is few.** Both instruments for bounding σ_d
  at n = 10 (the χ² multiplier and the bootstrap, §8.5.5) are weak, and the χ² one assumes
  normality that a bounded coverage endpoint does not have. The three-outcome gate
  contains this uncertainty; it does not remove it. A "power remains uncertain" verdict is
  the expected outcome, not a pathology.
* **The dev 10 are a stratified central sample, not a random one** (§2.1): median
  relevance counts, both extremes excluded by construction. σ_d measured there may not
  transfer to the sparse and dense tails present among the 80. Reported by `n_rel`
  stratum; not corrected.
* **No reserve.** 10 + 80 = all 90 CDS topics (§2.1). Every excluded topic is a permanent
  loss of n, and no topic can be replaced. If exclusions bite harder than §8.5.6 projects,
  the run is smaller than planned and says so.
* **Shared inductive bias between labeler and retriever** is not removed by cross-family
  model choice (§6.6.6) — only self-agreement circularity is.

---

## 14. Change log — revision 2 (the nine statistical amendments)

**Provenance of this revision.** Two independent external statistical reviews read
revision 1. Both **endorsed the architecture**: development-only calibration, a fixed
equivalence margin, the blinding chain, exclusions applied identically across arms, a
hashed pre-registration, and an explicit inconclusive outcome. Both concluded that the
**statistical gate as written could not be frozen**. Their findings reached this document
as a single consolidated instruction with nine numbered items; where an item is marked
"both reviews" below, that is the consolidation's own attribution and **not** an inference
by the editor. **Per-item attribution to review A vs review B is not recoverable from the
consolidated brief** — the brief states which items were verified arithmetically by the
study lead (items 1–3, whose numbers were reproduced independently here) but does not
carry a reviewer split. This is recorded as a limitation of the audit trail rather than
guessed at.

**Independent verification.** Every arithmetic claim below was recomputed in this session
from `math`/`statistics` only (no run, no GPU, no store, no model call): the power
constants and the σ_d requirement (0.05·√80/2.802 = 0.1596), the three-test comparison
table, the exact non-central-t power surface (numerically integrated, mass check
1.0 − 2×10⁻¹²), the ρ-inflated variance table, the χ² multipliers at df = 9, and the
Wilson/exact binomial bounds for the label read. All reproduce the reviewers' figures
where they overlap. Two divergences from the brief are noted in §14.A.

| # | amendment | raised by | what it replaced | where |
|---|---|---|---|---|
| **1** | **The primary test is one-sided non-inferiority at α = 0.025, not TOST.** The power constant 2.802 = z₀.₀₂₅ + z₀.₂₀ was always this test's constant; the name was wrong, not the number. Adopting the one-sided NI test makes the existing σ_d ≤ 0.160 requirement correct rather than patching it, and it is the right test for the product question (does the cheaper configuration *lose* more than ε). Power is now also reported under non-zero true differences: at σ_d = 0.14, 88.4% at Δ = 0 but **47.3% at Δ = 0.02** — inside the margin. The **"~12% headroom" claim is deleted**; it was computed against the wrong test. | consolidated brief item 1 (both reviews; arithmetic verified by the study lead and reproduced here) | "Bonferroni-TOST (α = 0.025 one-sided)" naming in §1/§3/§8.3/§8.5/§12.6/P.7; the single Δ = 0 power figure; the headroom sentence | §3, §8.3, §8.5.1, §12.6, P.7 |
| **2** | **The variance model carries intra-topic correlation.** `σ_d ≈ √(p_flip/m)` assumed independent units that share one query and one retrieval. Replaced by `σ_d² ≈ (p_flip/m)[1+(m−1)ρ]`: at m = 10, ρ = 0.10 → **0.195**, and at m = 16, ρ = 0.10 → **0.177** — so the old "raise the unit cap 12 → 16" fallback is aimed at a failure mode it cannot fix, and is now conditioned on a measured ρ ≤ 0.05. `p_flip` and ρ are **estimated at unit level from the ~450 labeled dev pairs**; the model-based σ_d is reported beside the direct per-topic SD and the larger governs. The same correction is applied to the variant-averaging fallback (÷ 1.3 requires ρ_variant ≈ 0.18; at ρ_variant = 0.8 it is ÷ 1.05), which is now measured rather than assumed. Phase-0's measured σ_d (Leg A 0.156 dense / 0.173 reranked, n = 10) is cited **in** the gate reasoning as an unreassuring prior. | consolidated brief item 2 (both reviews; verified) | the independence projection "σ_d ≈ 0.14, inside the requirement"; the unconditional unit-cap fallback; the assumed ÷ 1.3; the omission of the Phase-0 σ_d prior from the gate | §8.5.2, §8.5.3, §8.5.8 |
| **3** | **The argument against a binary endpoint is withdrawn.** "Check 4 measured 10/10 topics changing their top-10 documents, therefore binary discordance cannot be ~3%" does not follow — every ranked list can change while each topic still retrieves a relevant document. The inference is **deleted**. The continuous endpoint stays primary, justified on its own merits (it measures coverage rather than any-coverage, and it uses the labels the study paid for). The discordance bound is retained with its **constant corrected** (2.802², giving d ≤ 0.025, not the old 2.487² → 0.032), and **Stage 0 measures binary discordance directly**. `ES-Hit@B` is **retained** as a secondary — it was already in §7.5 and is not new. | consolidated brief item 3 (both reviews; verified) | the check-4 inference in §7.4 and the "cannot be powered" assertion in §12.6 | §7.4, §7.5, §8.5.7 (row 4), §12.6 |
| **4** | **The gate is a three-outcome decision on a pre-declared variance bound.** "dev σ_d ≤ 0.160" never said point estimate or upper bound; as an upper bound it demanded σ̂_d ≤ 0.087 (unpassable for a design working as hoped), as a point estimate it carried little evidential weight. The reviewers' decision table is adopted **verbatim** (passes / power remains uncertain / apply adaptations or classify underpowered). The bound's confidence level is **pre-declared: one-sided 80% (χ², df = 9, ×1.293 → σ̂_d ≤ 0.124)**, with the 90/95/97.5% multipliers printed so the choice is auditable. The χ² band's **normality assumption is stated** — it is not distribution-free for a bounded endpoint at n = 10 — and a bootstrap bound is computed alongside, the larger governing. | consolidated brief item 4 (both reviews) | the single ambiguous threshold "If dev σ_d ≤ 0.16: freeze as specified" | §8.5.5, P.7 |
| **5** | **Calibration is per-contrast.** Max-separation (256 vs 2048) carries cross-topic effect heterogeneity the flip model omits; min-separation (512/0 vs 512/64) retrieves nearly identical sets and will look well powered regardless. Max-separation σ_d now governs the size contrasts, min-separation only the overlap contrast. **Preferred and adopted where affordable: calibrate all five confirmatory contrasts directly** (possible because §11 now builds every arm before calibration); any contrast left uncalibrated is **named explicitly** in the PREREG and the results table. | consolidated brief item 5 (both reviews) | "measure … the maximum-separation pair and the minimum pair" read as one pooled number | §8.5.4, §11, P.7 |
| **6** | **Unit-level analysis is pre-registered, not optional.** Reducing a topic to a coverage fraction discards information and creates the heteroscedasticity the unit-cap fallback was implicitly working around. A **cluster bootstrap over topics with the difference computed over all units** is pre-registered and always reported beside the per-topic primary (chosen over a binomial GLMM: numpy-only, distribution-free, reuses the BCa machinery — the review permitted either). A primary/sensitivity disagreement downgrades the decision to UNRESOLVED-BY-ESTIMAND. Unit-level data are retained to diagnose correlated failures; **units are never treated as additional independent topics**. | consolidated brief item 6 (both reviews) | per-topic macro-average as the only analysis; the unit cap as the answer to unequal m | §8.4.3, §7.5, P.7 |
| **7** | **The label-validation gate has acceptance criteria, a frozen rubric, and enough pairs to detect a shortfall.** 40 pairs could not certify anything (zero errors in 40 → one-sided 95% upper bound **7.2%**, larger than ε). Now: the rubric (`RUBRIC-evidence.md`, sha256 recorded) is **written and frozen before any labeling call** and must settle unit distinctness, sufficiency-vs-intersection, joint coverage by several chunks, and the handling of missing evidence, incorrect spans and ambiguous units; **two independent readers on the same blinded subset** with κ, adjudication, and pre-adjudication κ reported; **≥ 100 pairs per read** (both the dev and the confirmation read — resolving the old 40-vs-60 inconsistency by enlarging both); **stratified toward hard cases** (40% model positives, 25% model negatives, 20% deep-section attributions — the Phase-0 abstract-vs-deep failure mode — 15% long documents); **omissions audited** with a `missed-evidence` verdict and its own gate and its own perturbation envelope; explicit thresholds for rubric revision, relabeling and gate failure. The κ ≥ 0.60 the review asked for is adopted as the **full-strength** threshold on the enlarged read, continuous with the existing ≥ 0.40-to-proceed tier. The cross-family argument is restated at its true strength: Scout removes **self-agreement circularity**, not **shared bias** (lexical-overlap preference, abstract-like prose, early position). | consolidated brief item 7 (both reviews) | "40 dev pairs read by the study lead; a second reader strongly preferred" and "60 confirmation pairs", both without acceptance criteria; the unqualified cross-family claim | §6.6.1–§6.6.6, §11 (human cost), P.5 |
| **8** | **The missing definitions are frozen.** New §6.2.1 defines **D1 span**, **D2 evidence set**, **D3 evidence unit** — including the deduplication that was genuinely absent (within-document merge at span-union Jaccard ≥ 0.5, containment pruning, no cross-document merging, document-stratified cap) — and **D4 covered** (full containment of every span; the ≥ 0.9-overlap variant is descriptive only). The **packing rule** and the **behind-the-reranker** property existed but were scattered: both are now stated explicitly and unmissably (§7.3 one-sentence restatement; §7.1's "anything shipping behind a reranker must be evaluated behind one"). **How the 10 dev topics were selected** is quoted from S2 with its consequence stated: they are a *stratified central sample* (one per year × type, median relevance counts, both extremes excluded) — so σ_d is reported by `n_rel` stratum. **Corpus mirroring** is settled by moving corpus assembly and the index builds *before* Stage-0 calibration, so σ_d is measured against the same 38k-document distractor composition the confirmation run uses. | consolidated brief item 8 (both reviews) | undefined unit/coverage semantics; the packing and reranker facts left implicit; no record of dev-topic selection; a Stage 0 that would have calibrated on a dev-scale corpus | §6.2.1, §7.1, §7.3, §2.1, §4.1, §11, P.6 |
| **9** | **Multiplicity, exclusions, and the absence of reserve are declared.** New §8.1.1 fixes the hierarchy: α split within families (NI Bonferroni α = 0.025 each; superiority Holm α = 0.05), no cross-family correction with the reason given, secondaries gatekept behind their own primary and labeled `DESCRIPTIVE`, and a multiple-comparison ledger printed in the results. **Exclusion criteria are pre-specified** (< 3 units, label failure, < 5 fetchable relevants, windowing failure) and **power is computed on the expected retained n**, with the requirement tabulated from n = 80 down to 60 and a hard gate if n_retained < 60. **There is no reserve**: 10 + 80 = all 90 CDS topics. **ε = 0.05 is explained as macro-averaged topic coverage**, explicitly *not* "one lost unit in twenty", with the micro-averaged equivalent reported beside it. | consolidated brief item 9 (both reviews) | an undeclared testing hierarchy; power quoted at n = 80; exclusions mentioned but not enumerated; the "one in twenty" gloss on ε | §8.1.1, §8.2, §8.5.6, §2.1, §13, P.7 |
| **10** | **The "freeze anyway" fallback is replaced, verbatim**, with: *"If calibration remains inadequate, the run may proceed under the frozen procedure with its projected power and limitations stated. Failure to establish a difference must not be interpreted as equivalence or used to prune configurations."* A new adaptation step precedes it: **re-scope the confirmatory set** so passing contrasts stay confirmatory and failing ones are re-declared exploratory before freeze, α redistributed within the surviving family. **ε still does not move.** | consolidated brief, closing instruction (both reviews) | "freeze anyway with the projected power printed … pre-declared LIKELY-UNRESOLVED" as step 3 of the ladder | §8.5.8, §13, P.7, P.11 |

### 14.A Two divergences from the consolidated brief, stated rather than silently absorbed

1. **The Δ = 0.02 power figure.** The brief states power "falls from roughly 88% at Δ = 0
   to about 60% at Δ = 0.02" at σ_d = 0.14. The 88% reproduces exactly (non-central t,
   df = 79: **88.4%**), but the same calculation gives **47.3%** at Δ = 0.02; **59.8%** is
   the value at **Δ = 0.015**. The discrepancy appears to be a transcription slip in the
   consolidated brief, not a methodological disagreement — the α = 0.05 one-sided
   calculation at Δ = 0.02 also gives ≈ 60.7%, so either a shifted Δ or a shifted α
   reproduces it. **This document uses the exact table (§8.5.1), which makes the
   reviewers' point *more* strongly, not less.**
2. **"σ_d 0.152–0.173 on Leg A".** The record does not support that range as a Leg A
   figure. Leg A (the 10 CDS topics, 276 config pairs, PIL §4) measured **0.156 dense /
   0.173 reranked**; **0.152** is **Leg B's** own measurement at n = 260 queries
   (PIL-rerun §5, alongside 0.119 at the judged-only rung). The substantive point is
   unaffected and adopted: the CDS priors sit at or above the 0.160 requirement. §8.5.3
   quotes each figure with its correct leg.

**Not amended, and why.** Nothing in the two reviews' architecture endorsement was
changed: the development/confirmation split, the fixed ε with its consequence derivation,
the blinding spine (§P.9), arm-identical exclusions, the hashed PREREG, the explicit
UNRESOLVED outcome, the §9 blocking concordance gate, and the §13 limitations discipline
all stand as written in revision 1. No amendment was judged wrong; §14.A records the two
places where the brief's own numbers did not reproduce, and the editor's judgment calls
(the 80% bound level, the cluster bootstrap over a GLMM, the κ tiering, the stage
reordering) are flagged as judgment calls at each site rather than presented as
requirements of the reviews.

---
---

# PREREG — confirmation run (READY TO FREEZE after Stage 0 fills its slots)

*Everything below is a commitment. Slots marked `[FROZEN-AT-STAGE-0]` are filled from the
dev set only, then this section's sha256 is recorded in `provenance-confirmation.json`
before any confirmation-topic retrieval output is unquarantined or any confirmation label
is read next to an outcome. A change after freeze is an amendment, dated and diffed, never
an edit.*

*Revision 2 of this PREREG incorporates the nine statistical amendments of §14. The
operative changes here are: the primary test is **one-sided non-inferiority at α = 0.025**
(not TOST); the power gate is a **three-outcome decision on a pre-declared one-sided 80%
variance bound**, read **per contrast** and at the **projected retained n**; a
**unit-level cluster bootstrap** is a standing pre-registered sensitivity; the label
validation is **two readers × ≥ 100 pairs per read against a rubric frozen before
labeling**, with acceptance criteria; the evidence-unit and coverage definitions (D1–D4)
are frozen; the testing hierarchy and exclusion criteria are declared; and the
"freeze anyway" clause is replaced.*

## P.1 Identity

* Commit: `[FROZEN-AT-STAGE-0]` (expected `55a0fc2` or descendant; recorded as full hash;
  if the executing checkout differs from `origin/main`, both hashes recorded — the BK-P
  §10.1 precedent).
* Interpreter `/rag/envs/ragstack/bin/python`; `HF_HOME=/rag/cache`; tokenizer file
  sha256s recorded for SFR and the generator tokenizer.
* `pin_repo()` (meta-path-finder strip + `sys.path` pin + `ragstack.__file__` assertion)
  executed in the main process **and every worker initializer**; resolved path and
  surviving `sys.meta_path` recorded. The `/rag/repos/ragstack` editable finder must not
  win.
* Served-model gates, live, before first call, ids recorded verbatim: `:9001–:9006` all
  `Salesforce/SFR-Embedding-Mistral` (abort on any mismatch); `:50052` `/health` reports
  `BAAI/bge-reranker-v2-m3`; `mango:8003` reports `RedHatAI/Llama-4-Scout-17B-16E-
  Instruct-FP8-dynamic` (asserted per `pilots/mango.py::served_model`); `mango:8004`
  Qwen3.6-35B-A3B for the second-judge slice. The generator tokenizer is taken from the
  **served** `LLM_MODEL` probed at freeze, not from any env file.
* Fleet politeness: `:9001–:9006` only, ≤ 2 in flight per endpoint; mango concurrency
  ≤ 4; **GPUs 6 and 7 RESERVED** — `nvidia-smi` before and after, both showing them
  untouched.
* Stores: `:6333` and `:9200` never contacted, in any stage. Stage 1 constructs no store
  client (grep of harness imports recorded). Stage 2 uses dev tenant `:24041`/`:24043`
  only, prefix `chkconf_<runid>_`, deleted with a verifying listing.
* Seeds: corpus grade-0 sampling — dev topics `20260904` (pilot reproduction), confirmation
  topics `20260912`; unit-cap subsampling `20260912`; bootstrap/permutation `20260913`;
  labeling duplicates `20260914`. Any RNG that draws nothing is recorded as inert.
* `budget_mode="joined"` pinned in the manifest (inert for `token_window`; pinned so the
  `55a0fc2` default change can never silently apply to a future amendment).
* Corpus manifest: sha256 over the sorted list of `(pmcid, sha256(bytes))` pairs, computed
  before embedding; recorded.
* Package versions (python, numpy, transformers, tokenizers, httpx) recorded.

## P.2 Data

* Confirmation topics: the 80 CDS topics not in
  {2014_5, 2014_11, 2014_29, 2015_8, 2015_18, 2015_23, 2016_1, 2016_9, 2016_13, 2016_26};
  year-prefixed; 90 distinct asserted. Exposure ledger §2.2 acknowledged; sequestration of
  §7a/breadth-k per-topic artifacts for these topics in force until unblinding.
* Development topics: the 10 above, selected by the S2 rule quoted in §2.1 — one per
  (year × type), `40 ≤ n_rel(≥1) ≤ 250`, `n_rel(≥2) ≥ 10`, closest to the cell's eligible
  median. **A stratified central sample, not a random one**; all Stage-0 variance
  estimates are reported by `n_rel` stratum and the gate is read against the confirmation
  set's `n_rel` distribution. **10 + 80 = all 90 topics: there is no reserve.**
* Corpus: all fetchable grade ≥ 1 docs (all 90 topics) + 300 seeded grade-0 per topic,
  deduped by PMCID; qrels filtered to fetched, never imputed; empty-body parses excluded
  and counted; no length cap in the corpus (labeling windows per §6.5). **The same
  corpus serves development and confirmation**, with identical distractor composition
  (judged-only, 300 grade-0 per topic); it is assembled and hashed, and all six index arms
  are embedded, **before** the Stage-0 variance measurement, so calibration and
  confirmation describe the same experiment (§4.1, §11).
* Queries: CDS `summary` (primary), `description` (sensitivity), embedded raw (production
  convention); instructed variant per §10 as sensitivity only.

## P.3 Arms

Index: `fixed_tok256_ov0pct`, `fixed_tok512_ov0pct`, `fixed_tok1024_ov0pct`,
`fixed_tok2048_ov0pct`, `fixed_tok512` (shipping, 512/64), `header512` (§5.1 definition).
Scoring-only: `parent256` (§5.2). Exploratory, never confirmatory: `multi256+1024`.
No other arm may be added after freeze.

## P.4 Pipeline

Dense exact cosine → top-50 chunk pool → full-pool rerank on `:50052` → pack. Never
one-chunk-per-document anywhere. Production parameters mirrored: depth 50
(= `rerank_candidates`), `max_per_doc=0`, no boilerplate demotion, no BM25 in the offline
harness (hybrid lives in Stage 2 with a blocking gate, P.8).

## P.5 Gold

Scout labels per §6: structural-unit-anchored minimal sufficient evidence sets, multiple
sets and multi-span sets legal, "no localizable evidence" legal; pooled labeling set
(top-20 union across arms, grade ≥ 1) + 10-per-topic seeded bias-bound sample; quotes
substring-verified; hallucinated-span rate ≤ 0.05 (gate); self-consistency ≥ 0.90 on 10%
duplicates (gate); docs > 48k Scout tokens windowed, rate recorded; prompt sha256s in the
manifest. `:50052` contributes nothing to gold.

**Rubric, frozen before labeling.** No labeling call — dev or confirmation — is issued
until `design/RUBRIC-evidence.md` exists and its sha256 is recorded here. It must settle,
with worked dev-only examples: distinct unit vs redundant restatement (D3); "covered" =
full containment, not intersection (D4); that several admitted chunks of the **same
document** may jointly cover one unit and chunks of different documents never combine;
and the handling of missing evidence, incorrect spans and ambiguous units (§6.6.1).

**Human reads — two, both two-reader, both ≥ 100 pairs** (revision 2; replaces 40 dev +
60 confirmation):

* **R-dev**, Stage 0, ≥ 100 dev pairs, two independent readers on the same blinded subset,
  stratified 40% model-positive / 25% model-negative / 20% deep-section attribution /
  15% long-document, seeded draw recorded.
* **R-conf**, ≥ 100 confirmation pairs, same two-reader and stratification rules, read
  blind to all outcomes **before** unblinding.
* Per-pair verdicts: `correct` / `wrong-location` / `non-minimal` / `missed-evidence` /
  `correctly-none` / `ambiguous`. **Omission is audited on every pair**, model negatives
  included.
* Reported: pre-adjudication κ(human–human), κ(Scout–human) against adjudicated verdicts,
  positive-class agreement, κ(Scout–Qwen), all with 95% CIs.

**Acceptance criteria (§6.6.4), fixed in advance:** κ(human–human) < 0.40 →
`RUBRIC_FAILURE`, study stops; 0.40–0.60 → one dated rubric revision and a fresh ≥ 100-pair
R-dev, a second shortfall stops the study. κ(Scout–human) < 0.40 → stop; 0.40–0.60 →
proceed with **every claim capped at `MODERATE`**; ≥ 0.60 **or** positive-class agreement
≥ 0.85 → full-strength claims. Label-error rate (Wilson upper) > 0.10 → relabel, then
label-limited with no NI conclusion. `missed-evidence` rate (Wilson upper) > 0.15 →
NI verdicts downgraded to UNRESOLVED-BY-LABEL-OMISSION.

**Two perturbation envelopes** (§6.6.5): commission (labels flipped at the Wilson upper
error rate) and **omission** (missed units added back at the observed rate and positional
distribution). A confirmatory verdict must survive both.

If the second reader cannot be staffed, that is recorded here at freeze and every claim is
capped at `MODERATE`; **the read is not shrunk below 100 pairs**.

## P.6 Endpoint, budget, packing

* Primary: **EUC@4096** on `summary` queries, computed **behind the reranker** (§7.1 — no
  `EUC` is ever computed on a dense-only ranking), per-topic fraction of evidence units
  fully contained in the packed context's per-document char-span union.
* **Definitions D1–D4 (§6.2.1) are part of this freeze.** Span = contiguous whole
  sentences within one JATS `<sec>`, as `[start_char, end_char)`. Evidence set = minimal
  span collection. **Evidence unit = (document, evidence-set) after deduplication**:
  within-document merge at span-union Jaccard ≥ 0.5 (canonical span list = the smaller
  set), containment pruning (keep the subset, drop the superset), **no cross-document
  merging**, then the seeded cap at 12 units/topic **stratified by source document**.
  **Covered = full containment of every span** of the canonical list — not intersection,
  not a token fraction; the ≥ 0.9-overlap variant is a descriptive column only. Several
  admitted chunks of the same document may jointly cover a unit; chunks of different
  documents never combine.
* Exclusions (§8.5.6), fixed now and applied identically to every arm: topic excluded if
  < 3 evidence units, or > 1/3 of its labeled pairs failed quote verification, or < 5
  grade ≥ 1 documents present in the corpus, or the windowing-failure criterion applies.
  The list is frozen at label freeze, before unblinding. **No excluded topic is replaced
  (no reserve).**
* `[FROZEN-AT-STAGE-0]`: whether the primary averages `summary` + `description` — applied
  only if the **measured** ρ_variant yields a divisor ≥ 1.15 (§8.5.8 step 1); the assumed
  ÷ 1.3 is not a justification. Unit cap 12 → 16 only if the **measured** ρ ≤ 0.05
  (§8.5.8 step 2).
* Budget: B = 4,096 **generator** tokens (served-model tokenizer); packing = A1: walk the
  **reranked** list by rank from 1, admit whole chunks while they fit, **stop at the first
  chunk that does not fit** (no skip-ahead, no partial final chunk), rank-1 always
  admitted, **no mid-chunk truncation ever**
  (admitted whole or the walk ends; an already-admitted duplicate parent is skipped at
  zero cost without ending the walk), raw-supplied-token accounting, parents
  charged once; realised raw and deduped totals reported per arm; embedding-tokenizer and
  reranker-tokenizer counts reported separately and never used for budgets. Secondary
  budgets {2,048, 8,192}.
* Manipulation checks §7.6 must pass before any contrast is read; a failed GOLD/NEGATIVE
  check stops the analysis (plumbing bug), a failed discrimination check is reported and
  demotes the affected reading.

## P.7 Contrasts, margins, multiplicity, power

* NI family (**one-sided non-inferiority at α = 0.025**, Bonferroni within the family;
  operationally the two-sided 95% CI upper bound < ε on both t(df = n_retained − 1) and
  BCa-10k): **N1** shipping − tok1024/0; **N2** shipping − tok512/0. **ε = 0.05 absolute
  EUC@4096**, interpreted as **macro-averaged per-topic coverage**, explicitly not "one
  unit in twenty" (§8.2; user veto possible at freeze — the veto and any replacement
  margin recorded here). NI is declared only by the CI rule; a nonsignificant difference
  is never called equivalence. **This is not TOST** — TOST's constants (2.927 / 3.242) are
  not the 2.802 this design sizes on (§8.5.1).
* Superiority family (Holm α = 0.05; bar 0.05; sign-flip 10⁵ + BCa; resolution rule and
  precedence per §8.3): **R1** tok256/0 − tok2048/0; **R2** header512 − tok512/0;
  **R3** parent256 − shipping.
* **Testing hierarchy (§8.1.1):** α split within each family (NI Bonferroni 0.025 each;
  superiority Holm 0.05); **no cross-family correction** (two disjoint product decisions,
  declared here, never revisited after the data); **secondaries are `DESCRIPTIVE` and
  gatekept** — a secondary may support a decision only if that decision's own primary
  contrast resolved, and may contradict at any time. A **multiple-comparison ledger**
  (every confirmatory test, its family, α, adjustment, and the count of descriptive rows)
  is printed in the RESULTS document.
* **Unit-level sensitivity, always reported** (§8.4.3): cluster bootstrap over topics
  (10,000 resamples, seed `20260913`), arm difference computed over **all** units of the
  resampled topics, BCa interval. Primary/sensitivity disagreement in sign or verdict →
  **UNRESOLVED-BY-ESTIMAND**, reported in the table. Units are never counted as n.
* δ80 = 2.802·σ_d/√n_retained accompanies every descriptive row (0.313·σ_d at n = 80);
  UNRESOLVED, never "null", when δ80 exceeds the distance; boundary proportions on Wilson
  intervals only.
* **Power gate `[FROZEN-AT-STAGE-0]` — the complete §8.5.7 table**, not a single number:
  (1) σ_d(EUC@4096) **per confirmatory contrast** (N1, N2, R1, R2, R3 directly where the
  arms allow; otherwise max-separation σ_d for the size contrasts, min-separation only for
  N2, with **every uncalibrated contrast named**), each with point estimate, χ² one-sided
  80/90/95% upper bounds and the bootstrap 80% bound, **the larger governing**;
  (2) unit-level `p_flip` and ρ from the dev pairs, with the model-based
  σ_d = √((p_flip/m̄)(1+(m̄−1)ρ)) beside the direct per-topic SD, **the larger governing**;
  (3) m̄ and the units-per-topic distribution after D3, plus cap-hit rate;
  (4) measured per-topic binary discordance for `ES-Hit@4096` and whether d ≤ 0.025;
  (5) measured ρ_variant and the resulting variant-averaging divisor;
  (6) projected **n_retained** under the §8.5.6 exclusions, and the requirement re-read at
  it (n_retained < 60 is itself a gate);
  (7) power against **Δ ∈ {0, 0.01, 0.02}** for every confirmatory contrast — a variance
  figure alone is never accepted as a power statement;
  (8) the §6.6.4 label-validation table; (9) the §7.6 manipulation checks and the dev
  `EUC` level in [0.15, 0.90].
* **Gate decision rule — three outcomes, on the pre-declared one-sided 80% upper bound**
  (χ², df = 9, ×1.293; the 90/95/97.5% multipliers printed alongside; the χ² bound assumes
  normal paired differences and **is not distribution-free** for a bounded endpoint at
  n = 10, which is why the bootstrap bound is computed and the larger used):

  | calibration outcome | decision |
  |---|---|
  | required power holds at the chosen upper variance bound | power gate passes |
  | holds at the point estimate but fails at the upper bound | power remains uncertain |
  | fails even at the point estimate | apply permitted adaptations, or classify as underpowered |

  **Consequences, fixed in advance (§8.5.5):** pass → freeze as specified. **Uncertain →
  the freeze may proceed**, with power printed at both the point estimate and the bound
  for every Δ scenario and a **`POWER-UNCERTAIN`** annotation on the affected contrasts in
  every results table and in the summary; no adaptation is forced or forbidden, and any
  applied is recorded with its measured justification. Fails → the ladder below, then the
  closing clause.

* **Adaptations, in order** (§8.5.8), each applied only where the measurement says it
  helps: (1) variant averaging if measured divisor ≥ 1.15; (2) unit cap 12 → 16 if
  measured ρ ≤ 0.05; (3) **re-scope** — passing contrasts stay confirmatory, failing ones
  are re-declared exploratory before freeze, α redistributed within the surviving family
  (a one-member NI family stays at α = 0.025 one-sided; it does not widen).
  **ε never moves.** If calibration remains inadequate: *the run may proceed under the
  frozen procedure with its projected power and limitations stated. **Failure to establish
  a difference must not be interpreted as equivalence or used to prune configurations.***
  Affected contrasts are pre-declared **LIKELY-UNRESOLVED** here with their Δ-scenario
  power printed.
* Clustering: topic; sensitivities: bipartite-component cluster bootstrap,
  leave-one-year-out, year-fixed-effect. Variants are within-topic replicates, never n.

## P.8 Serving-path validation (Stage 2) — blocking

Shortlist per §9; dev tenant only; frozen pre-rerank pools persisted (sha256 in manifest);
rerank isolated on frozen pools; matrix per §9. **Gate:** each confirmatory contrast's
sign under hybrid+rerank/top_k=10 must match the offline harness, else the corresponding
ship/build decision is BLOCKED pending investigation. No averaging, no footnote.

## P.9 Order of operations (the blinding spine)

0. **Rubric written and frozen** (`RUBRIC-evidence.md`, sha256 recorded) before any
   labeling call, dev or confirmation.
1. **Stage 0a:** corpus assembly + manifest hash → chunking → all six index builds. No
   retrieval output for any confirmation topic is computed. *(Moved ahead of calibration
   in revision 2 so σ_d is measured on the real corpus; §4.1, §11.)*
2. **Stage 0b:** dev-topic retrieval, rerank, packing, labeling (~450 pairs), **R-dev
   ≥ 100-pair two-reader read** → fill every `[FROZEN-AT-STAGE-0]` slot (the full §8.5.7
   table) → apply any §8.5.8 adaptation → **record sha256 of this PREREG** in
   `provenance-confirmation.json`.
3. Confirmation retrieval runs on the already-built indexes, outputs **quarantined**
   (written, unread) → pool extraction (ids only) → Scout labeling (blind) → **R-conf
   ≥ 100-pair two-reader read** (blind) → **label freeze** (labels hashed; exclusion list
   fixed here).
4. Unblinding: quarantined outputs opened; manipulation checks; confirmatory analysis;
   **unit-level cluster-bootstrap sensitivity alongside every confirmatory contrast**;
   both label-error envelopes; other sensitivities; predictions P1–P8 scored, failures
   reported.
5. Stage 2 serving validation → concordance gate → decisions.
6. RESULTS document written with the §13 limitations section, the multiple-comparison
   ledger (P.7), the realised n_retained beside every planned n, and the
   model-derived-label disclosure in every results-table header.

## P.10 Predictions on record

P1–P8 as §8.6, verbatim, scored in the RESULTS document.

## P.11 Stop rules

Fleet: > 2× projected embed time with builds outstanding → stop, report partial grid
(per-arm `.npy` checkpoints). Mango: > 2× projected labeling time → freeze labeled subset,
drop bias-bound sample first, record. Any gate in P.5/P.6 failing → the run stops at that
gate and reports; gates are never re-negotiated after freeze.

**The human read is not a stop-rule variable.** Neither read may be shortened below 100
pairs, and neither may drop to one reader, to save schedule; the permitted response to a
staffing shortfall is the `MODERATE` claim cap recorded at freeze (P.5).

**The power gate is not a stop rule either — it is a labeling rule.** A failed gate does
not by itself stop the run (§8.5.8); it determines which contrasts are confirmatory, what
power is printed beside them, and the standing prohibition that follows:
***failure to establish a difference must not be interpreted as equivalence or used to
prune configurations.***
