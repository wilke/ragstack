# Lab book: glossary-model benchmark, round 1 (2026-10-28 to 2026-10-29)

**EXPLORATORY throughout.** Nothing here was pre-registered. Every number below is exploratory,
including those in tables, and none is a confirmatory finding. This page is the lab book: it is
append-only and dated. An entry that later turns out wrong is superseded by a new dated entry, never
edited. There is no `RESULTS-*.md` for round 1, and none will be written. A pre-registered round 2 would
get one.

Times are UTC. "The owner" is the project owner. "OpenChia" is the collaborating evaluation that ran the
papers-OC retrieval measurements against its own gold set.

Committed counts-only artifacts are in [artifacts/](artifacts/). [README.md](README.md) has the pointers
and the public/private split.

## Contents

- [The question](#the-question)
- [Design](#design)
- [Chronology](#chronology)
- [Results](#results), in four parts:
  1. [Intrinsic: coverage, cost, agreement](#part-1-intrinsic-coverage-cost-agreement)
  2. [docs-A retrieval: a design property, not a null](#part-2-docs-a-retrieval-a-design-property-not-a-null)
  3. [papers-OC retrieval (OpenChia)](#part-3-papers-oc-retrieval-openchia)
  4. [Gold precision](#part-4-gold-precision)
- [Two measures, not one ranking](#two-measures-not-one-ranking)
- [OpenChia's review, 2026-10-29](#openchias-review-2026-10-29)
- [Lab-book recommendation](#lab-book-recommendation-qwen36-as-the-default-glossary-model)
- [Threats](#threats)
- [What's next](#whats-next)

## The question

Which model should write the symbol glossary that ingestion attaches to chunks? The glossary maps a
document's symbols, abbreviations and aliases to their definitions. It is stored per chunk in
`symbol_glossary`, boosted in BM25 and read by the reranker. The candidates were the in-process `rules`
extractor (the baseline) and three served LLMs. Each was asked two separate questions:
- **quality:** are the entries right? (gold precision);
- **usefulness:** does retrieval find more? (extrinsic).

The second question needs an evaluation set where the glossary can reach the retriever. Only one corpus
had one.

## Design

**Corpora** (aliases; the record names no tenant, collection or host):

| alias | what | documents | chunks at 512 tokens | extrinsic test |
|---|---|---|---|---|
| docs-A | the software-documentation collection of the part-4 study | 336 | 1,113 | yes: the part-4 question set, retrieval-only (step 7) |
| papers-OC | OpenChia's scientific-paper collection | 275 | 14,456 | yes: OpenChia's 155-record gold set (step 8) |
| OA-1000 | 1,000 open-access JATS articles, as 12,560 article + float units | 12,560 | 32,765 | **absent** |
| ASM-1000 | 1,000 journal articles | 1,000 | 27,418 | **absent** |

The text was reassembled from each corpus's bench index at 512 tokens. That gave 0 gaps and 0 length
mismatches. The OA-1000 and ASM-1000 text was cross-checked against the extractor output and the
sample: 0 mismatches.

**Arms:**
- `rules`: `rules/3`, the baseline.
- `scout`: `RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic`, run at concurrency 16.
- `qwen38`: `Qwen3.8-27B`, run at concurrency **8**. That endpoint is shared with interactive users, and
  its operator limited us to 8 requests in flight.
- `qwen36`: `Qwen3.6-35B-A3B`, run at concurrency 16.

**Step settings.** These were identical across the models and are recorded in each glossary's version
string, `llm/2:<model>:dcb924271b1e:w80000x4:t4096:k0:r0`:
- thinking off; repair rounds 0;
- cut-off retry `split`; max tokens 4,096;
- windows of 80,000 characters, at most 4 per document;
- timeout 300 s; seed 20261028;
- `--llm-min-chars 1500` on OA-1000 only, because JATS float units are documents there;
- no extra request body.

The step ran from the tools image `ragstack-tools-v1.6.20-b1` (commit 4a0571b). Six modules in it are
byte-identical to main at 0e22c55:
- `ingestion/glossary_step.py`;
- `symbol_glossary.py`;
- `symbol_glossary_llm.py`;
- `glossary_file.py`;
- `scripts/glossary_step.py`;
- `scripts/backfill_symbol_glossary.py`.

**The merge rule.** For each document, the `merged` glossary is the LLM half if that half has at least
one entry, and otherwise the rules half. So every LLM condition is a **mixture** of LLM-glossaried and
rules-glossaried documents, and the mixture differs between models. OpenChia's review counts the
consequences; see [below](#openchias-review-2026-10-29).

**Attachment.** A glossary entry attaches to a chunk only where the symbol occurs there *with a value*
(`attach_only_with_value`, the default). That is why the chunks carrying a glossary are so few.

**Holding the variants.** One glossary file is made per corpus × model. It serves every chunk size,
because the glossary is per document. In-memory evaluation (docs-A) holds all four files at once. An
index evaluation (papers-OC) holds one at a time, and the arms are swapped by re-attaching a file with
`backfill_symbol_glossary.py --glossary-file`. No re-embedding is needed.

## Chronology

**2026-10-28 → 29, steps 1–6 (intrinsic, read-only).** The steps ran from 04:19Z to 05:22Z on the 29th:
- **step 1:** probes and a stamp census. All bench indices were 100% `rules/3`.
- **step 2:** shard export by reassembly.
- **step 3:** rules files and dry-run backfills on every chunk size. All were verified, with no mismatch.
- **step 4:** a 10-document smoke test per model. Nothing degraded, and no reasoning output appeared.
- **step 5:** twelve full runs, 04:29–05:19Z, all with rc 0. At most 0.20% of documents degraded
  (ASM-1000 under Qwen3.6, 2 cut-off).
- **step 6:** analysis, then the after-census, which was unchanged.

No store was written in steps 1–6.

**2026-10-29, step 7: docs-A extrinsic, retrieval-only.** It ran 14:30–14:33Z:
- It used a clean detached worktree at db97c28, which carries the harness's `--glossary-file` from #856.
- It ran against a bench copy of docs-A (1,113 chunks), not the live index.
- The result is **citable** (dirty false, untracked 0). See [Part 2](#part-2-docs-a-retrieval-a-design-property-not-a-null).

**2026-10-29, step 8: papers-OC swaps, measured by OpenChia.** Each LLM file was dry-run, applied and
censused on the papers-OC bench copy (512 tokens), and then OpenChia ran its evaluation:
- scout: 15:48:15–15:48:42Z;
- qwen38: 15:53:28–15:54:08Z. A first attempt at 15:52:46Z was refused by the guard, and nothing was
  written;
- qwen36: 15:58:06–15:58:49Z;
- the rules restore, with `--replace-llm`: 16:02:39–16:03:06Z.

The final census was 14,456 of 14,456 chunks at `rules/3@file/1`. The restore stamp is `@file/1`, not
the original `rules/3`; the plan recorded this as a deliberate, documented delta. OpenChia then re-ran `rules` after
the restore, as a noise control.

**2026-10-29, gold judging and the rubric history.**
- **The sample.** 60 documents, 15 per corpus. Within each corpus they were stratified by terciles of
  the rules-entry count (seed 20261028). The sample holds **528 distinct entries** (symbol + definition)
  across all four arms. Each was shown blind to its arm, in shuffled order, with the document text. The
  judges were:
  - Claude (`claude-opus-5-5`, through the CLI judge route);
  - Codex (`gpt-6-astra`, concurrency 1);
  - Gemini 2.5 Pro, through a lab gateway.
- **Rubric v1.** Three labels: `correct`, `wrong`, `not-a-symbol`. Acronyms counted as not-a-symbol.
  - Claude 60/60 documents ($10.78); Codex 60/60, as 7 + 53 after a rate-limit stop; Gemini 58/60. Two
    documents failed twice, both with invalid labels.
  - The owner labelled 40 entries, and then 42. Owner-vs-judge κ was about 0.03–0.13 for Claude and
    Codex, and 0.28–0.38 for Gemini, on 36–42 items. These come from private label files and are not
    committed, so read them as a lab-book note only.
  - The confusions were systematic:
    - Claude and Codex called *not-a-symbol* 8 and 11 entries that the owner called correct. That is
      consistent with v1 classing acronyms as not-a-symbol;
    - the judges called *correct* 6–10 entries that the owner called wrong.
  - Committed as [artifacts/gold-r1.json](artifacts/gold-r1.json), for the history only.
- **Rubric v2**, an owner decision of 2026-10-29:
  - an abbreviation or alias counts when the document pairs it with the other name;
  - a bare gene or protein name, with no other name in the document, is not a symbol;
  - an imprecise or partial definition is wrong.

  Claude ran in full. Codex stopped at 26/60. v2 was **superseded before analysis**: folding "true but
  incomplete" into "wrong" made harm unreadable.
- **Rubric v3**, an owner decision of 2026-10-29:
  - it adds `partial` (true according to the document, but incomplete or less specific);
  - a symbol flattened by text extraction is judged by the symbol it stands for, provided the string
    identifies exactly one symbol of the document;
  - a fragment that collides with two or more symbols is `wrong`.

  The owner relabelled the 42 entries under v3. The runs:
  - Claude 60/60, with one retry;
  - Codex 60/60;
  - Gemini 57/60, 17:30:44–17:41:50Z. Three documents returned incomplete answers twice.

  The analysis is [artifacts/gold-r3.json](artifacts/gold-r3.json). See [Part 4](#part-4-gold-precision).
- **Gateway audit.** OpenChia, which operates the gateway, audited the Gemini v1 window, 16:02:41–16:13:47Z:
  - 63 calls were routed: 62 succeeded, and 1 provider error returned no answer;
  - the token counts were identical to ours: 713,499 in and 19,743 out;
  - no model was substituted.
- **Caution.** Each rubric change came after the owner had seen judge labels. That is a forking path.
  See [Threats](#threats).

## Results

### Part 1: intrinsic (coverage, cost, agreement)

Source: [artifacts/intrinsic.json](artifacts/intrinsic.json) and
[artifacts/agreement.json](artifacts/agreement.json). Both are `citable: false`, because the analysis
ran from a scratch script.

**Documents with at least one merged entry, and the merged-entry total** (exploratory):

| corpus | rules | scout | qwen38 | qwen36 |
|---|---|---|---|---|
| docs-A (336) | 3 / 3 | 14 / 30 | 11 / 29 | 10 / 22 |
| papers-OC (275) | 145 / 866 | 175 / 1,086 | 186 / 1,842 | 188 / 1,860 |
| OA-1000 (12,560 units) | 398 / 764 | 752 / 2,002 | 853 / 2,813 | 920 / 3,378 |
| ASM-1000 (1,000) | 143 / 199 | 429 / 984 | 471 / 1,240 | 573 / 2,041 |

**Chunks carrying a glossary at 512 tokens**, from the backfill dry runs (exploratory):

| corpus | chunks | rules | scout | qwen38 | qwen36 |
|---|---|---|---|---|---|
| docs-A | 1,113 | 1 | 1 | 1 | 1 |
| papers-OC | 14,456 | 241 | 394 | 498 | 509 |
| OA-1000 | 32,765 | 104 | 233 | 330 | 307 |
| ASM-1000 | 27,418 | 3 | 6 | 7 | 7 |

**Windowing.** These documents hit the cap of 4 × 80,000 characters. The tail past the cap was not read.
The counts were the same under all three models (exploratory):
- papers-OC: 5 of 275;
- ASM-1000: 2 of 1,000;
- OA-1000: 0;
- docs-A: 0.

**Cost.** On papers-OC, Qwen3.6 made 358 calls with 4.56M prompt tokens and 262k completion tokens, in
5.0 min at concurrency 16. Qwen3.8 took 8.5 min at concurrency 8, and Scout 6.0 min at concurrency 16.
The wall times are **not comparable** across models, because the concurrency differed (exploratory).

**Agreement.** Papers-OC Jaccard on canonical symbol keys was 0.79 for Qwen3.8 against Qwen3.6, and
0.51 for Scout against either Qwen. The three-way intersection share was 0.45. The Qwen pair agrees with
each other far more than either agrees with Scout. The same pattern holds on OA-1000 (0.59 against
0.37–0.38) and ASM-1000 (0.40 against 0.27–0.30). All exploratory.

Coverage is not quality. Coverage also failed to predict retrieval in the October trial, and failed
again here (Part 3).

### Part 2: docs-A retrieval (a design property, not a null)

Source: [artifacts/docsA-found.json](artifacts/docsA-found.json). It is **citable** (commit db97c28,
clean).

On docs-A the glossary reaches **1 chunk in 1,113** under every arm. This test therefore **could not have
shown anything**. It is a property of the design, and it is not a null finding about glossary models.

For the record (exploratory): `found` was 603 of 735 at B4096 and 622 of 735 at k10, under all four
conditions. The per-item results were identical. Every contrast had 0 discordant pairs, with p = 1. The
**minimum attainable p was 1.0** for all 6 contrasts (McNemar and sign-flip). The reranker cache shows
why: every passage was a cache hit, so the rendered reranker inputs were byte-identical across the
conditions.

### Part 3: papers-OC retrieval (OpenChia)

These results were run by OpenChia against the papers-OC bench copy and reported to us by message. They
are transcribed exactly. OpenChia's own write-up is `docs/reviews/bench-glossary-261029.md` in
OpenChia's episode-study repository, merge 257a27e. That is an external pointer: we have not fetched it.

**Setup.** Records found, out of 155, at top_k 10, matched on quote text. One run per condition.
EXPLORATORY.

| arm | rules | scout | qwen38 | qwen36 | rules (repeat after restore) |
|---|---|---|---|---|---|
| base (hybrid+rerank) | 52 | 55 | 68 | 70 | 52 |
| bm25 | 62 | 67 | 79 | 82 | 61 |
| norerank | 48 | 56 | 67 | 61 | 50 |

**Per-record sign tests**, as gained / lost, two-sided exact, uncorrected (exploratory). The power-floor
column gives the split a two-sided test at 0.05 would have needed, with the same number of discordant
records.

| contrast | arm | gained / lost | p | power floor |
|---|---|---|---|---|
| scout vs rules | base | not reported by OpenChia (net +3; see the bootstrap rows below) | | |
| | bm25 | 20 / 15 | 0.50 | 24 / 11 of 35 |
| | norerank | not reported by OpenChia (net +8; see the bootstrap rows below) | | |
| qwen38 vs rules | base | 20 / 4 | .002 | |
| | bm25 | 27 / 10 | .008 | |
| | norerank | 25 / 6 | .001 | |
| qwen36 vs rules | base | 22 / 4 | .001 | |
| | bm25 | 27 / 7 | .001 | |
| | norerank | 19 / 6 | .015 | |
| qwen38 vs scout | base | 21 / 8 | .024 | |
| | bm25 | 23 / 11 | .058 | 24 / 10 of 34 |
| | norerank | 22 / 11 | .080 | 23 / 10 of 33 |
| qwen36 vs scout | base | 22 / 7 | .008 | |
| | bm25 | 24 / 9 | .014 | |
| | norerank | 19 / 14 | .487 | 23 / 10 of 33 |
| qwen36 vs qwen38 | base | 4 / 2 | .688 | 6 / 0 of 6 (min attainable p .031) |
| | bm25 | 5 / 2 | .453 | 7 / 0 of 7 (min attainable p .016) |
| | norerank | 6 / 12 | .238 | 14 / 4 of 18 |

**The noise control.** The rules arm was repeated after the restore. Its gained / lost against the first
rules run was 0 / 0 (base), 0 / 1 (bm25) and 2 / 0 (norerank). The run-to-run noise floor is therefore
0–2 records (exploratory).

**The bootstrap.** **138 of the 155 records come from ONE paper.** Paper-clustered CIs all reach 0, so
there is **no corpus-level claim**. Within that paper, OpenChia resampled records 4,000 times
(exploratory). Records gained, with 95% intervals:

| | base | bm25 | norerank |
|---|---|---|---|
| rules → qwen38 | +16 [7, 25] | +17 [5, 29] | +19 [8, 30] |
| rules → qwen36 | +18 [9, 28] | +20 [9, 31] | +13 [4, 23] |
| qwen38 → qwen36 | +2 [−3, 7] | +3 [−2, 8] | −6 [−14, 2] |
| rules → scout | +3 [−7, 13] | +5 [−6, 17] | +8 [−2, 18] |

**Reading (exploratory, one paper):**
- **Both Qwen models beat rules** in every arm. **Scout does not.**
- **Qwen3.6 and Qwen3.8 are indistinguishable.** In base and bm25 the within-paper intervals, [−3, 7]
  and [−2, 8], exclude a Qwen3.6 advantage of more than about 8 records and a Qwen3.8 advantage of more
  than about 3. In norerank, Qwen3.8 is ahead by 6, and the interval [−14, 2] reaches +2 for Qwen3.6.
- **Coverage did not predict retrieval; wording did.** Scout changed 35 records against rules (bm25), but
  its gains and losses were balanced (20 / 15, p = 0.50). It carries a glossary on 394 chunks, against 241
  for rules, so its coverage rose and its retrieval did not. The Qwen models carry glossaries on 498 and
  509 chunks and gain. Round 1 does not isolate why. The candidates are the wording of the definitions,
  which OpenChia's proxy in point 4 measures directly. It is not the merge-rule mixture on the gold
  documents: all 7 source papers are LLM-glossed under all three models (point 2).
- **BM25 beat the reranked default in every condition** (62/52, 67/55, 79/68, 82/70). On the live
  papers-OC collection it was 85 against 65. That figure is OpenChia's earlier live-collection
  measurement: external, and not re-measured here. On this corpus the reranker costs recall. That is a larger
  effect than any glossary difference here, and it deserves its own look ([What's next](#whats-next),
  item d).

### Part 4: gold precision

Source: [artifacts/gold-r3.json](artifacts/gold-r3.json), under rubric v3. It is `citable: false`
(scratch analysis script). The analysis takes the majority of the three judges. Items without a strict
majority are `split`: 25 of 528. Document-clustered bootstrap, 2,000 draws. EXPLORATORY.

| arm | n (majority) | harm (`wrong`) | strict (`correct`) | lenient (`correct`+`partial`) | not-a-symbol |
|---|---|---|---|---|---|
| rules | 103 | **0.165** [0.083, 0.299] | 0.621 [0.484, 0.702] | 0.786 [0.614, 0.872] | 0.049 |
| scout | 189 | 0.032 [0.007, 0.063] | 0.767 [0.652, 0.856] | 0.889 [0.811, 0.939] | 0.079 |
| qwen38 | 258 | 0.027 [0.007, 0.057] | 0.791 [0.679, 0.879] | **0.961** [0.923, 0.985] | 0.012 |
| qwen36 | 272 | **0.011** [0.000, 0.025] | **0.871** [0.805, 0.915] | 0.941 [0.884, 0.978] | 0.048 |

**Reading (exploratory):**
- **Rules has the highest harm rate.** About 16.5% of its entries were judged wrong. Every LLM arm is
  below it, and every difference against rules excludes 0. On papers-OC alone, rules has 15 wrong of 83.
- **Qwen3.6 has the lowest harm rate, 1.1%.** But it is not distinguishable from Qwen3.8: the harm
  difference is [−0.043, +0.001]. It is distinguishable from Scout only at the edge: [−0.047, −0.001].
- **Strict precision depends on the correct/partial line.** Under strict precision Qwen3.6 leads Qwen3.8,
  0.871 against 0.791. Under lenient precision the order reverses, 0.941 against 0.961. Neither
  difference excludes 0 (strict [−0.014, 0.175], lenient [−0.075, 0.022]).

**Judge agreement** (exploratory):
- **Judge against judge.** 4-label κ was 0.77 (Claude~Codex, n 528), 0.73 (Claude~Gemini, n 335) and
  0.69 (Codex~Gemini, n 335).
- **Against the owner, on 42 items:**
  - 4-label κ: Claude 0.51, Codex 0.57, Gemini 0.61, and the majority 0.61 (n 41);
  - with partial collapsed into correct: 0.62–0.70.
- **The largest owner→judge confusion is correct→partial:** 6 items for Claude, 3 for Codex and the
  majority, 1 for Gemini. Claude and Codex are stricter about completeness than the owner.

## Two measures, not one ranking

Precision and retrievability are **reported separately and must not be combined into one ranking.** This
point is from OpenChia's review, 2026-10-29. Scout looks far better than rules on entry quality, with
harm at 3.2% against 16.5%. Yet it gains exactly nothing in retrieval: 20 / 15, p = 0.50. So in round 1,
entry correctness did not predict retrieval usefulness (exploratory).

- **Precision is the right headline for a glossary people read**, such as a glossary panel or a tooltip.
- **Retrieval is the right headline for search.**

- **On retrieval**, both Qwen models beat rules and Scout within the one paper.
- **On precision**, Qwen3.6 separates from Scout on strict and lenient precision, and on harm only at
  the edge. Qwen3.8 separates from Scout only on lenient precision.
- **The two Qwen models are indistinguishable** from each other on every measure (exploratory).

## OpenChia's review, 2026-10-29

OpenChia sent a critical review of this benchmark. It is attributed here, and folded into the framing
above and the threats and next steps below.

1. **Two measures.** Report precision and retrievability separately, never as one ranking. See the
   section above.
2. **The merge rule confounds the comparison.** "LLM if any entry, else rules" makes every condition a
   mixture. On papers-OC:
   - Scout has 162 LLM-glossaried documents and 113 rules-glossaried ones;
   - Qwen3.8 and Qwen3.6 have 185 LLM documents each;
   - 155 documents are LLM under all three models, and 83 are rules under all three.

   Some of the Scout-vs-Qwen difference might come from the 23 extra documents. OpenChia was asked for a
   restricted re-score on the 155-document subset, using the existing runs with no new searches.

   **Answered later on 2026-10-29: the retrieval table is free of this confound.** OpenChia's 155 records
   come from **7 source papers, all LLM-glossed under all three models**. That includes the dominant
   paper: each carries the model's own glossary under scout, qwen38 and qwen36. The four-condition
   retrieval table in [Part 3](#part-3-papers-oc-retrieval-openchia) therefore compares the models' own
   glossaries on every gold document, and every figure in it stands unchanged. Two residuals remain:
   - **(i)** the mixture confound **does** apply to our corpus-wide analyses. These are the intrinsic
     coverage, the gold precision, and any statement about all 275 papers-OC documents, or about the
     other corpora;
   - **(ii)** 37 non-gold documents change kind between conditions, from LLM to rules or back. They could
     act as distractors in retrieval. That effect is second-order, but it is not formally zero.

   The recommendation is to run one **pure-LLM condition**, with no rules fallback, before any
   corpus-wide claim.
3. **The value-only attachment has a blind spot.** An entry attaches only where its symbol occurs with a
   value, so quantities printed in words are missed: floor area, occupant count, room height. OpenChia's
   metric shows that these are the fields that fail. A phrase index would reach them.
4. **A direct extrinsic proxy.** The proposal: does a gold chunk carry an entry whose definition
   contains every word of the field's binding term? OpenChia's numbers (exploratory, theirs):
   - On the live collection: Scout 41 of 63 pairs, Qwen 42, Opus 43.
   - On JATS 512/64: rules 23, Scout 25, Qwen 42, Opus 39.
   - **The shuffled-glossary control** for the reranker: content only 53; with the glossary 65; with a
     shuffled glossary 57. So about 4 of the +12 comes from having *any* glossary, and about 8 from
     having the *right* one.

   Both the proxy and the control are worth adopting for round 2.
5. **Earlier OpenChia findings.** These are external pointers, reported to us and not re-measured here:
   - raising the glossary field boost from 0 to 1 on BM25 took papers-OC from 28 to 85 of 155 records;
   - giving the glossary to the reading/extraction step had no measurable effect. The glossary earns its
     keep in retrieval, not in reading;
   - stemming the glossary field was refuted: 83, against a baseline of 84.
6. **Windowing.** Report the documents that hit the 4 × 80k cap. That is done in
   [Part 1](#part-1-intrinsic-coverage-cost-agreement): papers-OC 5 of 275, ASM-1000 2 of 1,000,
   OA-1000 0, docs-A 0, the same under all three models.
7. **docs-A is a design property.** At 1 chunk in 1,113, it could not have shown anything. State it that
   way, not as a null finding. That is done in
   [Part 2](#part-2-docs-a-retrieval-a-design-property-not-a-null).
8. **The next steps, in OpenChia's order.** See [What's next](#whats-next).

## Lab-book recommendation: Qwen3.6 as the default glossary model

This is a **lab-book recommendation, not a finding.** It is an **operational** choice between two models
that round 1 cannot tell apart. It is not a quality ranking. The Qwen3.6-over-Qwen3.8 ordering is
**exploratory** wherever it appears.

- **Retrieval:** indistinguishable from Qwen3.8 on papers-OC (Part 3). Both beat rules within the one
  dominant paper.
- **Harm:** Qwen3.6 has the lowest harm rate, 1.1% (exploratory). It is not separable from Qwen3.8's
  2.7%.
- **Speed:** Qwen3.6 is about 1.5× faster. The October trial note recorded Qwen3.8 at about 1.5× slower
  than Qwen3.6, and Scout at about 2× slower; that is the section-aware trial of 2026-10-22, held
  privately. This benchmark's own wall times can't confirm it, because Qwen3.8 ran at half the
  concurrency.
- **Capacity:** the vLLM operator's serving numbers, reported to us and not in this record, show that the
  Qwen3.6 endpoint has headroom. The Qwen3.8 endpoint is shared with interactive users and limited to
  8 requests in flight. For Qwen3.6 at 4,096 tokens, the #847 throughput measurement gave 5,131
  documents/h at concurrency 16 and 6,847 at concurrency 32.

So the two are indistinguishable, and the recommendation is to pick the faster one, which has headroom.
The final choice between Qwen3.6 and Qwen3.8 comes last, after the steps below, on cost, speed and licence
([What's next](#whats-next), item e).

## Threats

- **One run per condition.** The rules repeat gives a noise floor of 0–2 records on papers-OC. Nothing
  else was repeated, and the LLM runs' own run-to-run variance was not measured.
- **Multiplicity.**
  - About 18 papers-OC comparisons were made, uncorrected. For two of them, scout vs rules on base and on
    norerank, OpenChia reported no per-record split, only the within-paper bootstrap net (+3 and +8).
  - Six qwen-vs-rules p-values are at 0.015 or below. A Bonferroni correction over 18 needs p ≤ 0.0028,
    which four of the six meet: qwen38 base and norerank, and qwen36 base and bm25.
  - The 18 gold difference CIs (6 pairs × 3 measures) are uncorrected too. The Scout-vs-Qwen3.6 harm CI,
    [−0.047, −0.001], would not survive any correction.
- **Reach.** 138 of 155 records come from one paper, and the paper-clustered CIs all reach 0. The
  retrieval effect is shown **for one paper only**.
- **The chunking.** The papers-OC bench copy is chunked differently from the live collection, so the
  absolute counts don't transfer. The live BM25-against-reranked figure of 85 against 65 is a different
  index.
- **The merge rule.** The conditions are mixtures, with different LLM-document sets per model (OpenChia
  point 2).
  - The retrieval table is not affected, because all 7 gold source papers are LLM-glossed under every
    model.
  - The intrinsic and precision comparisons **are** affected.
  - 37 non-gold papers-OC documents switch between LLM and rules across the conditions, and could act as
    distractors. That is second-order, but not zero.
- **Judge validity.**
  - The judges agree with the owner only moderately: κ 0.51–0.61 on 42 items. The owner is one labeller,
    and is not blind to the hypotheses.
  - Each rubric change, v1 → v2 → v3, was made after the owner had seen judge labels. The precision
    figures are conditional on v3.
- **Strict precision depends on the correct/partial line.** The Qwen order flips between the strict and
  the lenient measures.
- **Gemini's coverage.** Gemini lost 3 documents in v3, to answers that were incomplete twice, and 2 in
  v1, to labels that were malformed twice. It labelled 335 of the 528 v3 items, and 373 in v1, so the
  three-judge majority rests on two judges for many items.
- **Split majorities.** 25 items had split majorities and are excluded from the majority rates.
- **No extrinsic test on two corpora.** The extrinsic test is **absent** for ASM-1000 and OA-1000. The
  intrinsic and precision numbers there say nothing about retrieval.
- **docs-A is null by construction:** the glossary reaches 1 chunk in 1,113.
- **Attachment.** Value-only attachment misses quantities that are printed in words (OpenChia point 3).
  Round 1 measures the glossary as attached, not the glossary as extracted.
- **Windowing.** 5 papers-OC documents and 2 ASM-1000 documents were read only up to 320k characters.

## What's next

This is OpenChia's order, adopted.

- **(a) A multi-paper evaluation set.** Of the order of 10–20 questions from each of about 20 papers,
  pre-registered as **round 2**. It is the only way past the one-paper reach limit.
- **(b) The restricted-subset re-score.** **Done, 2026-10-29.** All 7 gold source papers are LLM under
  all three models, so the retrieval table stands. The follow-up is a **pure-LLM condition** with no rules
  fallback, run before any corpus-wide claim (coverage, precision, or "all 275 documents"). It also removes
  the 37 documents that switch kind between conditions.
- **(c) The wording metric as the primary extrinsic proxy.** Does a gold chunk carry an entry whose
  definition contains the binding term? Use it with the shuffled-glossary control. Report precision as a
  separate quality measure, never merged into the proxy.
- **(d) A dedicated test of the reranker.** BM25 beat the reranked default everywhere: 62/52, 67/55,
  79/68 and 82/70, and 85 against 65 on the live papers-OC collection. That is a larger effect than any
  glossary difference.
- **(e) Only then, choose the model.** Choose between Qwen3.6 and Qwen3.8 on cost, speed and licence.
  Until then, the lab-book default is Qwen3.6, as above.
- **Housekeeping:**
  - fill in the private-data commit in [README.md](README.md) and the artifacts' `private_data` field;
  - re-run the step-6 and gold analyses from committed scripts, if any of their numbers is to become
    citable.

## Status, 2026-10-29

| | |
|---|---|
| Steps 1–8 | complete. Every bench index was restored to `rules/3@file/1` (papers-OC) or left untouched. |
| Gold judging (v3) | complete. 3 judges, plus the owner on 42 items. |
| OpenChia restricted re-score | **done.** All 7 gold source papers are LLM-glossed under all three models; the retrieval table stands. |
| Pure-LLM condition (no rules fallback) | not started; needed before any corpus-wide claim |
| Round 2 (multi-paper, pre-registered) | not started |
| Default glossary model | lab-book recommendation: Qwen3.6, operational, exploratory |

## Addendum, 2026-10-29 (evening): "85 against 65" pairs two different tests

Corrected by OpenChia on 2026-10-29. Part 3's reading, the Threats section and What's next item (d) quote
"85 against 65 on the live papers-OC collection" as a BM25-against-reranked comparison, and attribute it
to OpenChia. Both parts are wrong. The two numbers come from different tests:

- **85 of 155** is BM25 alone with the glossary field boost raised from **0 to 1** (28 → 85; review point
  5). No reranker is involved.
- **65 of 155** is the **reranker** with `content` + `symbol_glossary` as its input, against 53 for
  `content` alone and 57 for a shuffled glossary. It reranks a **50-candidate pool that holds a gold
  chunk for only 105 of the 155 records**, so no reranker could exceed 105 at that depth. The 50-pool's
  own fused top 10, unreranked, finds 33.

The source is [Test 3 in the symbol-glossary plan](../../symbol-glossary.md). It was measured **on this
host** by another session, against the quote gold. It was not measured through OpenChia's gateway, which
has never served a 50-candidate pool.

So "85 against 65" is **not** evidence that the reranker costs recall on the live collection. It is
withdrawn as a contrast wherever it appears above. The bench-copy figures in Part 3 (62/52, 67/55, 79/68,
82/70) are unaffected and stand as recorded: one run each, uncorrected, exploratory. What's next item (d)
now reads: *a dedicated test of the reranker that reports the candidate pool's ceiling (gold present at
depth D) beside every reranked figure.* That test is being pre-registered separately.

The earlier text stays in place; this entry supersedes it.

## Addendum, 2026-10-29 (evening): status

- **Round 2 and the reranker test are being pre-registered.** Both drafts follow OpenChia's order:
  - multi-paper evaluation sets;
  - a pure-LLM condition;
  - the wording proxy with a shuffled-glossary control;
  - precision kept separate from retrieval;
  - the model choice last.

  The harness code comes first, then a review, then the freeze. No gateway call and no store write
  happens before the freeze.
- **OpenChia's user lifted the "never copy" rule** on its 155-record evaluation set for this project's
  **private** experiments repository. Answer quotes still never go into a public repository; this record
  keeps counts and ids only.

## Addendum, 2026-10-30: round 2 stopped at its reach gate; the reranker test has not started

Both pre-registrations froze at 2026-10-30T02:19:33Z (commit 9e109c3). What happened next is in their own
records; this entry only points at them.

- **Round 2 is closed — GATE-NOT-EVALUABLE on every set, no confirmatory hypothesis tested.** The record
  is [`../glossbench-round2-2026-11/RESULTS.md`](../glossbench-round2-2026-11/RESULTS.md); its deviations
  D-1 to D-5 are in [`../glossbench-round2-2026-11/DEVIATIONS.md`](../glossbench-round2-2026-11/DEVIATIONS.md);
  the only artifact is the counts-only
  [`reach-r2.json`](../glossbench-round2-2026-11/artifacts/reach-r2.json). In one line each:
  - the 25 eval papers of each set hold **144 / 119 / 72** value-statement chunks (OA-1000 / ASM-1000 /
    papers-OC), so the confirmatory stratum S1 could never reach the gate of 150 under any glossary arm.
    The gate was unattainable by construction of the sample — a design error the frozen text did not catch,
    knowable before freezing from a count that was not run (D-5). The owner stopped the round at the gate:
    no drafting, no pilot, no swap, no retrieval, no proxy, no precision;
  - the one batch that ran, GPT-5.5 on the 26 set-O documents, hit `max_tokens` 4,096 on 30 of 58 calls
    (the papers-OC owner's gateway audit, external); its glossaries are truncated outputs, not a measure
    of the model (D-4);
  - descriptively, the served value-only attachment reaches **8.8 %** of OA-1000's value-statement
    chunks under pure Qwen3.6 and **2.1 %** of ASM-1000's (rules 3.1 % / 0.9 %). Part 3's papers-OC gain
    was therefore measured on a question set concentrated on glossaried chunks; the R1 set's own
    attachment rate was not measured (absent).
- **The reranker test has not run an arm.** Its preparation found three input problems before `pools`
  (the part-4 identity reference's key layout, the held set's live-collection ids and missing quotes,
  the docs-A cluster field); the fixes are PR #871 (merged `b300d0e`, 2026-10-30T03:32:47Z). Its deviations D-1 to
  D-3 — the pre-committed re-score yields A4-50 only; the reader fixes and pin; **set P's gold uses the
  papers-OC owner's actual quote rule (whitespace collapsed, 60 % boundary clause), which differs from the
  rule the frozen Terms name on 16 of 155 records** — are in
  [`../reranker-test-2026-11/DEVIATIONS.md`](../reranker-test-2026-11/DEVIATIONS.md).
- **What's next, item (a)** (a multi-paper evaluation set) is not done: round 2 did not produce one.
  Round 3 is to be pre-registered with the value-statement count per paper measured before its sample
  size, gate and population are frozen. Items (b)–(e) are unchanged; (d) is the reranker test above.

## Addendum, 2026-10-30 (afternoon): the reranker test is complete — What's next item (d)

Item (d) — *a dedicated test of the reranker that reports the candidate pool's ceiling beside every reranked
figure* — is done. The record is [`../reranker-test-2026-11/RESULTS.md`](../reranker-test-2026-11/RESULTS.md),
its deviations D-1 to D-7 in [`../reranker-test-2026-11/DEVIATIONS.md`](../reranker-test-2026-11/DEVIATIONS.md),
the counts-only artifacts in [`../reranker-test-2026-11/artifacts/`](../reranker-test-2026-11/artifacts/).
Run at `f408b7a`, clean, 2026-10-30T13:49–14:31Z. In one line each:

- **docs-A / T512o0 (confirmatory, 732 of 735 questions, 208 pages):** H3 **confirmed** — the reranker fed
  BM25's own 50 candidates is non-inferior to BM25's own order (688 vs 629 at top 10, 65 gained / 6 lost,
  Holm 3.0 × 10⁻⁴, CI lower bound +0.060 above −δ = 0.05). H1 and H2 **not confirmed**, and both reversed:
  the default finds 707 to BM25's 629 (84 / 6); the fused 50-pool's recall is 727 to BM25's 703. Reranker
  inputs never exceeded the declared 4,096 XLM-R tokens (max 692).
- **papers-OC, the owner's 155 (exploratory):** every hypothesis **GATE-NOT-EVALUABLE** — 7 papers (< 8),
  and `bm25@100` served short on 19 records (verified as index exhaustion, D-5; the API route cannot tell,
  so the mark stands). Descriptively: pool ceilings at depth 50 are **82 (BM25) vs 83 (fused)**, so the
  loss against BM25 alone is ordering, not the pool — the default finds 52 (31 ordering losses), rerank over
  BM25's pool 54, BM25 alone 61. Over the same 50 candidates the reranker reading `content` alone finds 43,
  `content+gloss` 52, `content+shuffled` 41 (our seeded permutation over all 241 glossaried chunks, not the
  owner's derangement over pool chunks). One run, 153 records, 138 in one paper; no inference.
- **Absent:** family B (round 2 produced no set), the L replication (D-7: the docs-A bench copy is chunked
  512 / 0, so L there equals T512o0 chunk for chunk; no copy at the live chunking exists), A8, E7, E8,
  depth 200 on P.
- **Part 3's reading stands as recorded** (one run each, exploratory). What this test adds is the
  decomposition: on papers-OC the reranker's ordering, not the fused pool, is where the records are lost;
  and the glossary in the reranker's input is where part of the gain comes back. Both descriptive.

What's next items (a)–(c) and (e) are unchanged; (d) is closed by the record above.

## Addendum, 2026-10-30 (evening): the papers-OC owner's corrected reading of Part 3 — Scout over rules is unresolved

**Mapping.** The papers-OC owner calls the measurement behind Part 3 (Results)
its **"round 2"**. In this lab book it is **round 1, step 8** (2026-10-29): the four-condition table on the
owner's 155 records. This entry follows that table. It is not about this project's glossbench round 2, which
stopped at its reach gate and produced no retrieval result.

The owner re-scored the step-8 runs with a corrected matching rule and then with source-paper scoping. Its
addenda are at 4a9030d (the Scout figures; it supersedes 7a10f05's) and 7a10f05 (the quote-rule
correction), with the matching rule at aad81fe. The owner later replaced that rule with an assignment rule
(6c71ef1) and reports that the two agree on every scoring decision of this measurement. All four are
external: they are in the owner's repository, and we have not re-measured them. The corrected rule disproportionately removed
false credit from the rules baseline. The figures below are the owner's, exploratory, and from one run
each. The bootstrap is within the dominant paper (138 records), not at the corpus level.

**Scout vs rules, no-reranker arm.** Records gained, with 95 % interval, under three defensible scorings:

| scoring | rules → scout, norerank |
|---|---|
| as recorded in Part 3 (the owner's original quote matching) | +8 [−2, +18] |
| corrected matching rule, 138-record single-paper analysis | **+11 [+1, +22]** |
| corrected rule + source-paper scoping | +6 [−4, +16] |

Under the corrected rule and under scoping, Scout's other two arms (base, bm25) still include zero.

**Reading.**

- **Scout over rules is unresolved.** The one contrast that excluded zero did so under one of three
  scorings, and it includes zero again under the third. Part 3's "Scout does not [beat rules]" was too
  strong as a null, and the owner's own earlier reading, that Scout's gain was "noise", is too strong as a
  dismissal; the owner says so too. Neither "Scout beats rules" nor "Scout does
  not" is supported.
- **Both Qwen models beat rules on every arm under every scoring.** Under the corrected rule the effects
  grew slightly. That finding of Part 3 stands.
- **Qwen3.6 and Qwen3.8 remain indistinguishable.**

The reranker test's [gold-scope addendum](../reranker-test-2026-11/ADDENDUM-2026-10-30-gold-scope.md) finds
the same pattern on the reranker arms. A loose quote rule and cross-paper gold bias in opposite directions,
so a scoring choice can move a small contrast across zero. What's next is unchanged.

## Addendum, 2026-10-30 (night): round 3 is complete — mention attachment did not find more

Round 3 held the glossary fixed (pure Qwen3.6, round 2's files) and changed only the attachment rule:
`value` (served; an entry attaches where its symbol states a value) against `mention` (on every chunk that
mentions the symbol). The record is [`../glossbench-round3-2026-11/RESULTS.md`](../glossbench-round3-2026-11/RESULTS.md).
Deviations D-1 to D-6 are in [`../glossbench-round3-2026-11/DEVIATIONS.md`](../glossbench-round3-2026-11/DEVIATIONS.md),
and the counts-only artifact is
[`results-r3.json`](../glossbench-round3-2026-11/artifacts/results-r3.json). Retrieval and analysis ran at
`6b434e8` (clean), 2026-10-30T22:33–22:56Z. In one line each:

- **H1 (set O, M_num, the served rule's blind spot): not confirmed.** `mention` found 134 against
  `value`'s 142 of 149 at top 10, with 1 gained and 9 lost. The power was .90 at the planned effect, so
  this is a powered null for the hypothesised gain. The test was one-sided, so the adverse direction is
  descriptive.
- **H2 (V) and H3 (right against shuffled entries): GATE-NOT-EVALUABLE.** Their discordant records sit in 5
  and 3 papers. Neither is a null.
- **Input against outcome.** Under `mention`, more records' gold chunks carry the asked quantity's words (7
  → 12 of 206), but `mention` puts a field on 18 % of OA-1000's chunks instead of 0.9 %. On the
  unadjusted secondaries, the losses are largest under `bm25` and nearly absent under `norerank`.
- **Value-only stays the served rule, as a no-change decision, not a demonstrated benefit.** On this set
  `mention` did not beat `value`, and `value` did not beat no glossary: under `base` it found exactly the
  records that no glossary found. Nothing here supports switching to `mention`; nothing shows value-only
  better than none.
- **Set A** (descriptive) was drafted after the drafter model's upstream change (D-6). **Set P** stopped at
  its pilot and was not promoted (D-1). The R1 replication and the layer-3 spot-check are **absent**.

What's next is unchanged, apart from this: the attachment question that round 3 was built to inform now
has its record, and the record does not support switching the served rule to `mention`.

## Addendum, 2026-10-30 (late night): round 3's spot-check validity is measured

Round 3's record reported the layer-3 owner spot-check as **absent**: the labels had not been imported when
`RESULTS.md` was committed. They are now. The figures are in the record's
[spot-validity addendum](../glossbench-round3-2026-11/ADDENDUM-2026-10-30-spot-validity.md), with the
counts-only artifact [`spot-validity-r3.json`](../glossbench-round3-2026-11/artifacts/spot-validity-r3.json).
The rule is round 2's §3.3, carried into round 3: point-estimate validity below 0.85 marks a set *validity
below threshold*.

- **Set O: 39 / 40 valid, 0.975, Wilson 95% [0.871, 0.996].** As typed it was 40 / 40. One item was
  labelled valid, but the owner's own note called its question ambiguous or wrong, and the record is one
  of the census's 12 flags. The owner counted it as not valid. Either way, O is above the threshold, and
  its status label is now *our three-layer check*, owner-spot-checked.
- **Set A: 40 / 40 valid, 1.000, Wilson 95% [0.912, 1.000].** It is descriptive only, and D-6 concerns
  its drafting, not this check.
- **8 of O's 40 sampled records** are among the census's 53 discriminating records, and 1 is among its 12
  flags. The two checks cover different populations.
- A sample of 40 bounds the invalid rate (upper bound 0.129 on O); it does not show zero. The labels were
  made after `RESULTS.md`'s figures existed, from sheets that name no arm.

`RESULTS.md` is unchanged, and so is what's next.
