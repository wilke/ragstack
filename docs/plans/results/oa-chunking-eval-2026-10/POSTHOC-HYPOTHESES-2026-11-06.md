# OA chunking eval: post-hoc hypotheses A-D (2026-11-06)

STATUS: exploratory analysis after the main run (2026-11-05) and Addendum C (section-bounded arms,
2026-11-06). It is **not** part of the frozen pass rule and changes no decision. Intervals are
unadjusted paired-bootstrap 95% CIs (10,000 resamples) unless marked Holm. Many subgroup comparisons
were made, so single cells are hints, not findings.

Only aggregates are given here. Questions, quotes, passages and per-question files are kept in the
private experiments repository.

## Overview

**What was tested.** Ten ways of chunking were compared on 22,593 open-access PubMed Central (PMC)
articles on bacteria and viruses:
- fixed-size token windows of 512 or 1,024 tokens, with and without overlap;
- sentence-aware chunks;
- two semantic chunkers, which split where the embedding similarity between neighbouring sentences
  drops;
- sentence and fixed-size chunks also cut at the articles' section boundaries, with one variant that
  adds the section headings to the embedded text;
- a control that makes the same number of cuts at random positions.

There were 2,035 questions, each with an exact answer quote taken from one article. The other
~20,000 articles have no question and serve only as distractors. A question counts as answered when
the top 5 retrieved chunks contain at least 90% of the quote (hit@5).

Retrieval ran in six settings:
- the production setting: hybrid search (keyword BM25 and vector results merged), followed by a
  reranker, a cross-encoder model that reorders the top 50 candidates;
- hybrid search without the reranker;
- vector search alone and keyword search alone, each with and without the reranker.

**Decision.** The decision comes from the main run plus Addendum C, not from this analysis. Under the
pass rule fixed before the run, **fixed-size 512-token chunks with a 64-token overlap, cut at section
boundaries (ft512o64s), replace the current 512/64 chunks**. They score +3.5 hit@5 points and
retrieve 14% less text.

**Hypotheses and conclusions:**

| Question / hypothesis | Conclusion |
|---|---|
| Do section cuts help retrieval? | **Yes, for any retrieval that uses vectors.** +2.5 to +7.9 points; no effect on keyword (BM25) search. Without the reranker, real section boundaries also beat the same number of random cuts. |
| Does overlap have no advantage over no overlap? | **No: overlap helps, by about 2 points, in production and keyword search.** There is no effect for vector-only search or for hybrid search without the reranker. It only matters for the ~6% of answers that a plain fixed-size cut splits in two. |
| Is semantic chunking better than fixed or sentence chunking with sections? | **No.** It is worse or tied in every setting. The legacy semantic chunker also costs ~4.5-7x the embedding tokens. |
| **A.** Chunk size matters, and the effect depends on question type and how dense the text is | **Mostly supported.** Larger chunks hurt vector search (-5.8) and help keyword search (+5.3). Under vector search, methods, results and number-heavy answers suffer most. In production (hybrid + reranker) the effect mostly disappears (-0.3). |
| **B.** Overlap wins because it makes chunks larger | **Rejected.** Chunks are the same size with and without overlap. The gain comes entirely from answers that would otherwise be split. |
| **C1.** The reranker evens out chunking differences in hybrid search | **Not supported as stated.** Combining keyword and vector search already compresses the differences, roughly halving the vector-only spread. The reranker then adds a near-uniform +14 to +18 points to every method. Chunking still matters by 2-6 points. |
| **C2.** The reranker evens out chunking differences whatever the retrieval source | **Partly supported.** With a single source, the reranker narrows the gaps by 25-40% but keeps the order. On top of hybrid fusion, it adds no significant further compression. |
| **D.** Overlap's advantage is an artifact of how the questions were built | **Partly supported.** The mechanism is real: overlap stops answers being cut in half. Its measured size is set by the benchmark's short, sentence-shaped, single-span answers. |

**In short:**
- Cut chunks at section boundaries.
- Keep 512-token chunks for vector-based retrieval.
- Keep the overlap, or cut on sentence boundaries.
- Don't adopt semantic chunking.
- Hybrid search and the reranker shrink the differences between chunking methods but don't erase them.
- The benchmark mainly tests "don't split the answer". Longer, multi-part answers are needed to test
  chunk size and overlap properly.

## Setup in brief

- Corpus: 22,593 PMC OA bacteria/virus articles. Each article is extracted once with `jats_extract.py`,
  giving 241,930 records (articles, tables, figures). Every arm gets identical text and its own
  collection.
- Questions: one 1,200-character passage per eval article, taken from inside a single section. An LLM
  writes one paraphrased question plus an exact answer quote of at most 300 characters.
- **n** is the number of questions a figure is computed over. Every hit@k value and paired comparison
  in this document uses the **decision set** (n = 2,035) unless marked otherwise. That is the subset
  the frozen pass rule decides on:

  | Step | Count | What happens |
  |---|---|---|
  | Sample | 22,600 articles | 2,600 eval articles, stratified by journal family × length band, plus 20,000 background articles (seed 20261104) |
  | Corpus | 22,593 articles | 7 sampled articles gave no extracted record (6 background, 1 eval). The rest are chunked and indexed in every arm, so every question is searched against all 22,593 |
  | Passages | 2,592 | One 1,200-character passage per eval article. 8 articles skipped: 6 no fitting passage, 1 no target region, 1 no article record |
  | Questions | 2,437 | One LLM question + exact answer quote per passage. 155 passages failed the automatic checks in 3 attempts (quote not an exact substring, question not self-contained, quote too long, copied wording) |
  | Clean set | 2,335 | Minus 63 ambiguous-gold items (the quote also appears elsewhere) and 66 within-record repeats; some had both flags |
  | **Decision set** (`clean_excl_noise`) | **2,035** | Minus the 300-question noise subset |

  So each decision-set question comes from a different eval article. About 20,000 articles have no
  question and serve only as distractors.
- **Passage selection.** The passage is not random text: it comes from a seeded draw under fixed rules.
  1. The article text is split into section regions at its headings. Text before the first heading
     is the abstract.
  2. Only target regions are used: those whose heading, or parent heading, contains results,
     discussion, methods, introduction or abstract.
  3. A seeded draw picks one target type present in the article, then a region of that type, then a
     start sentence. This spreads section types across the sample.
  4. The passage is grown from whole sentences, using the sentence chunker's splitter, inside that one
     region. It grows until it reaches 1,200 characters. If it overshoots 1,500, the last sentence is
     dropped, as long as at least 900 remain.
  5. Articles with no usable target region, such as short letters without headings, are retried over
     the whole body (type `body`, 85 articles).

  Result:
  - Median length 1,242 characters (p10 979, p90 1,371).
  - Section types: abstract 657, methods 482, results 471, introduction 467, discussion 430, body 85.

  Two properties used later follow from these rules. Every gold quote lies inside one section, and
  quotes are sentence-shaped.
- **Why 1,200 characters.** The plan gives no written rationale; the value was carried over from an
  earlier chunking experiment. Our reading (analysis, not a documented decision):
  - At about 300 tokens, a passage is well under one 512-token chunk (about 2,000 characters).
  - It gives the question writer enough context for a self-contained question about one fact.

  The downside is that such passages yield only short, single-span answers. That is the limit
  discussed under hypothesis D.

  - The noise subset (300 questions) was used beforehand to measure run-to-run noise, which set the
    pass rule's margins. It is excluded so that the same questions don't both set the margins and
    decide the outcome.
  - The clean set (n = 2,335), with the noise subset kept in, is the sensitivity check.
- Metric: hit@k = the union of the top-k chunks covers >= 90% of the gold quote's characters.
  Primary k = 5.
- Retrieval settings:
  - (a) hybrid + cross-encoder reranker (production; reranks the top 50 candidates)
  - (d) hybrid without the reranker
  - (b) vector only
  - (c) BM25 only
  - (e) vector + reranker and (f) BM25 + reranker: post-hoc, for hypothesis C2.
- Arms:

  | Arm | Chunking |
  |---|---|
  | ft512o64 (baseline) | fixed_token 512/64 |
  | ft512o0 | fixed_token 512/0 |
  | ft1024o0 | fixed_token 1024/0 |
  | sent512 | sentence 512 |
  | sempool512 | semantic_pooled, capped at 512 |
  | sem512 | legacy semantic, capped at 512 (~4.5-7x the embedding tokens, per the plan) |
  | ft512o64s | fixed_token 512/64 + section cuts (Addendum C) |
  | sent512s | sentence 512 + section cuts (Addendum C) |
  | sent512sh | sentence 512 + section cuts + heading path in the embedded text (Addendum C) |
  | sent512rc | sentence 512 + random cuts, count-matched to the section cuts (control) |

hit@5, decision set:

| Arm | (a) | (d) | (b) | (c) |
|---|---|---|---|---|
| ft512o64 | 79.02 | 61.28 | 49.19 | 55.68 |
| ft512o0 | 76.95 | 60.00 | 50.57 | 53.46 |
| sent512 | 79.56 | 63.64 | 52.87 | 54.40 |
| ft1024o0 | 76.66 | 62.36 | 44.77 | 58.77 |
| sempool512 | 80.29 | 64.67 | 56.95 | 51.74 |
| sem512 | 79.16 | 62.95 | 57.40 | 49.34 |
| ft512o64s | **82.51** | 65.41 | 57.10 | 55.38 |
| sent512s | 82.06 | 66.49 | 58.97 | 55.04 |
| sent512sh | 81.87 | **67.37** | **59.21** | 55.04 |
| sent512rc | 81.23 | 64.23 | 54.15 | 54.20 |

Decision under the frozen rule plus Addendum C: in setting (a), ft512o64s replaces ft512o64, by
+3.49 [+2.11, +4.86] (Holm k = 3). It also retrieves less text: 0.86x the baseline's context at
k = 5, i.e. 14% less.

---

## Preliminary questions: section cuts, overlap, semantic chunking

**Section cuts help retrieval wherever vectors are involved.** Holm-adjusted across the four
pre-specified contrasts (C1 to C4 below):

| Setting | sent512s - sent512 | ft512o64s - ft512o64 |
|---|---|---|
| (a) | +2.51 [+1.13, +3.88] | +3.49 [+2.11, +4.86] |
| (d) | +2.85 [+1.13, +4.57] | +4.13 [+2.36, +5.95] |
| (b) | +6.09 [+4.18, +7.96] | +7.91 [+5.90, +9.93] |
| (c) | +0.64 (n.s.) | -0.29 (n.s.) |

**Real section boundaries vs count-matched random cuts (sent512s - sent512rc):**

| Setting | Difference |
|---|---|
| (b) | +4.82 [+3.05, +6.58] |
| (d) | +2.26 [+0.64, +3.88] |
| (a) | +0.84 [-0.39, +2.11], n.s. |

So with the reranker, part of the gain comes from shorter, more numerous chunks, wherever the cuts
fall. The heading prefix (sent512sh - sent512s) is not significant in any setting. No gold quote
crosses a section boundary (0 of 2,035), because passages were sampled inside a single section.

**Overlap is not "no advantage".** ft512o64 - ft512o0:

| Setting | Difference |
|---|---|
| (a) | +2.06 [+0.59, +3.49] |
| (c) | +2.21 [+0.79, +3.64] |
| (d) | +1.28, n.s. |
| (b) | -1.38, n.s. |

Hypotheses B and D explain why.

**Semantic chunking does not beat the section arms.** In (a):

| Comparison | Difference |
|---|---|
| sempool512 - ft512o64s | -2.21 [-3.73, -0.69] |
| sem512 - ft512o64s | -3.34 [-4.91, -1.87] |

In (b) they tie fixed+sections (-0.15 and +0.29), and in (c) they are clearly worse (-3.6 to -6.0).
Without sections, semantic chunkers are competitive or better under vector-heavy retrieval: in (b),
sempool512 - ft512o64 = +7.76.

---

## Hypothesis A: chunk size matters, and the effect depends on question type and information density

Test: ft1024o0 - ft512o0. Both have no overlap, so only the size differs.

**Size matters, and its direction depends on the retriever:**

| Setting | 1024 - 512 |
|---|---|
| (b) vector only | **-5.80** [-7.67, -3.93] |
| (d) hybrid, no reranker | **+2.36** [+0.64, +4.08] |
| (c) BM25 only | **+5.31** [+3.78, +6.83] |
| (a) hybrid + reranker | -0.29 [-1.82, +1.23] |

**Dependence on the kind of text, clearest under vector only (b):**

| Group | 1024 - 512 |
|---|---|
| methods | -13.9 [-18.5, -9.5] |
| results | -10.4 [-15.0, -5.9] |
| introduction | -7.1 [-11.6, -2.6] |
| discussion | -2.4 (n.s.) |
| abstract | +0.8 (n.s.) |
| quote digit share, top third | -10.1 [-13.3, -6.8] |
| quote digit share, bottom third | -3.6 [-6.6, -0.6] |
| non-word token share, top third | -9.3 |
| non-word token share, bottom third | -3.8 |
| long articles | -8.0 |
| short articles | -2.5 (n.s.) |

**Question type**, proxied by the lexical overlap between question and quote (the writer's "echo"
score):

| Setting | High echo | Low echo |
|---|---|---|
| (b) | -8.3 | -3.3 |
| (d) | +1.3 | +5.1 |

**Under the production setting (a)** most of this dependence disappears. Only discussion (-3.9
[-7.4, -0.3]) and introduction (+3.7, n.s.) move.

**Verdict: mostly supported.**
- Chunk size matters, and dense, numeric text (methods, results, quotes full of figures) is hurt most
  by large chunks under dense retrieval.
- BM25 prefers large chunks.
- The production reranker absorbs most of the size effect.

**Limits:**
- Only two sizes were tested.
- Density is measured with crude proxies on the gold quote.
- There are no question-type labels; section type and echo stand in for them.

**Next:** a size sweep (256/384/512/768) with section cuts, and question-type labels (for example,
LLM classification).

---

## Hypothesis B: overlap performs better because it has larger chunks

**Overlap chunks are not larger.** Measured in the stores, over the articles behind the questions:

| | ft512o0 | ft512o64 | ft1024o0 |
|---|---|---|---|
| Mean chunk length (chars) | 1,771 | 1,781 | 3,451 |
| Context at k = 5, setting (a) (tokens, chars/4) | 2,300 | 2,316 | 4,601 |
| Gold quote inside one chunk | 94.15% | 99.75% | 97.3% |

With overlap, the chunks keep their 512-token size; the window advances by 448 tokens instead of 512.
Overlap moves the boundaries; it doesn't enlarge the chunks.

**The whole advantage sits on the quotes that a boundary splits in ft512o0** (ft512o64 - ft512o0):

| Setting | All (n = 2,035) | Quote split in ft512o0 (n = 119) | Quote inside one chunk (n = 1,916) |
|---|---|---|---|
| (a) | +2.06 [+0.59, +3.49] | +34.5 [+25.2, +43.7] | +0.05 [-1.36, +1.46] |
| (d) | +1.28 | +30.3 | -0.52 |
| (b) | -1.38 | +19.3 | -2.66 [-4.38, -0.94] |
| (c) | +2.21 | +31.9 | +0.37 |

Larger chunks would do worse: ft1024o0 - ft512o64 = -2.36 [-4.03, -0.79] in (a).

**Verdict: rejected.** Overlap works by repairing boundaries, not by making chunks bigger.

---

## Hypothesis C: the reranker normalises the effects of the chunking methods

### C1: when text and vector search are combined (hybrid)

**How far apart the ten arms are in each setting:**

| Setting | Mean hit@5 | SD across arms | Best - worst | Questions where arms disagree |
|---|---|---|---|---|
| (b) vector only | 54.1 | **4.75** | **14.5** | 53.5% |
| (c) BM25 only | 54.3 | 2.49 | 9.4 | 34.7% |
| (d) hybrid | 63.8 | 2.29 | 7.4 | 45.2% |
| (a) hybrid + reranker | 79.9 | 2.05 | 5.9 | 32.6% |

- **Spread across arms.** With/without reranker, the ratio is 0.90 [0.68, 1.19], so there is no
  significant compression. The median gap between two arms is 2.31 with the reranker and 2.36
  without.
- **Pairwise differences.** Those under (a) correlate 0.77 with those under (d), with a slope of 0.78
  (about 22% shrinkage). The order of the arms is largely kept.
- **Gain per arm.** The reranker adds +14.3 to +17.7 points to every arm, the smallest gain going to
  ft1024o0. A plausible but unverified explanation is that the cross-encoder truncates 1,024-token
  chunks.
- **Agreement between arms.** The drop in disagreement (45% to 33%) mostly follows the 16-point rise
  in hit rate, so it is a ceiling effect.
- **What does compress the arms** is hybrid fusion itself: vector-only SD 4.75 becomes hybrid SD 2.29.

**Verdict C1: not supported as stated.** Combining text and vector search compresses the chunking
differences. The reranker adds a near-uniform lift, with mild shrinkage and some reordering at the
top (for example, sempool512's lead over the baseline goes from +3.4 to +1.3). Chunking still
matters by about 2-6 points under the reranker.

### C2: independent of retrieval source (reranker on vector-only and BM25-only results)

Two post-hoc settings, run on all ten arms (v1.6.25, 0 failed calls):
- (e) vector + reranker
- (f) BM25 + reranker

The reranker ran on every call. Top-hit scores are cross-encoder probabilities (median 0.97), as in
(a), and there were no fallback events. The same script reproduces the C1 numbers exactly.

**hit@5, decision set (n = 2,035):**

| Arm | (b) vector | (e) vector + rr | (c) BM25 | (f) BM25 + rr |
|---|---|---|---|---|
| ft512o64 | 49.19 | 70.91 | 55.68 | 71.94 |
| ft512o0 | 50.57 | 69.63 | 53.46 | 69.68 |
| sent512 | 52.87 | 73.51 | 54.40 | 71.99 |
| ft1024o0 | 44.77 | 66.09 | **58.77** | **73.56** |
| sempool512 | 56.95 | 75.53 | 51.74 | 71.15 |
| sem512 | 57.40 | 74.99 | 49.34 | 69.29 |
| ft512o64s | 57.10 | 76.22 | 55.38 | 73.12 |
| sent512s | 58.97 | 76.76 | 55.04 | 73.27 |
| sent512sh | **59.21** | **77.10** | 55.04 | 73.27 |
| sent512rc | 54.15 | 74.64 | 54.20 | 72.09 |

**How far apart the arms are, with and without the reranker:**

| | SD across arms | Best - worst | SD ratio (rr / no rr) | Median pairwise gap | Slope of pairwise diffs | Corr. | Rank corr. | Reranker gain per arm |
|---|---|---|---|---|---|---|---|---|
| Hybrid (a) vs (d) | 2.05 vs 2.29 | 5.9 vs 7.4 | 0.90 [0.67, 1.19] | 2.31 vs 2.36 | 0.78 | 0.77 | 0.90 | +14.3 to +17.7 |
| Vector (e) vs (b) | 3.58 vs 4.75 | 11.0 vs 14.5 | **0.75 [0.65, 0.88]** | 3.54 vs 4.82 | 0.73 | 0.96 | 0.95 | +17.6 to +21.7 |
| BM25 (f) vs (c) | 1.50 vs 2.49 | 4.3 vs 9.4 | **0.60 [0.45, 0.81]** | 1.47 vs 2.41 | 0.49 | 0.88 | 0.76 | +14.8 to +20.0 |

The SD ratio CI comes from a bootstrap over questions (10,000 resamples). The slope is through the
origin, over the 45 pairwise arm differences.

**Key contrasts, without → with the reranker:**

| Contrast | Vector (b) → (e) | BM25 (c) → (f) |
|---|---|---|
| Size: ft1024o0 - ft512o0 | -5.80 → **-3.54** [-5.21, -1.92] | +5.31 → **+3.88** [+2.36, +5.45] |
| Overlap: ft512o64 - ft512o0 | -1.38 (n.s.) → +1.28 [-0.25, +2.75] | +2.21 → **+2.26** [+0.79, +3.69] |
| Sections: ft512o64s - ft512o64 | +7.91 → **+5.31** [+3.69, +6.93] | -0.29 → +1.18 [-0.25, +2.60] |
| Sections: sent512s - sent512 | +6.09 → **+3.24** [+1.67, +4.82] | +0.64 → +1.28 [-0.05, +2.65] |
| Structure: sent512s - sent512rc | +4.82 → **+2.11** [+0.69, +3.54] | +0.84 → +1.18 [-0.05, +2.41] |
| Semantic: sempool512 - ft512o64 | +7.76 → **+4.62** [+3.00, +6.24] | -3.93 → -0.79 [-2.26, +0.69] |

Bold = CI excludes 0 (unadjusted).

- **With a single retrieval source, the reranker does compress the arms.** The spread is about 25%
  narrower for vector and 40% for BM25. Both CIs exclude 1. Large chunk effects shrink by a third to a
  half: the vector size penalty, the vector section gain, and the BM25 semantic penalty, which
  disappears.
- **It narrows the gaps but keeps the order.** Rank correlation is 0.95 for vector and 0.76 for BM25.
  Most effects that were significant without the reranker stay significant, with the same sign:
  - vector size penalty, section gain, structure-over-random gain;
  - BM25 size gain, overlap gain.

  Under BM25 + reranker, ft1024o0 is still the best arm, and under vector + reranker the worst.
- **The reranker can only reorder the 50 candidates it is given.** If first-stage retrieval misses the
  gold chunk, the reranker can't recover it. Each source's characteristic misses therefore survive:
  - vector misses large mixed chunks;
  - BM25 misses semantic chunks with diluted terms.
- **Fusion and reranking compress the same thing, and they don't add up.** In hybrid the arms are
  already close (SD 2.29), and the reranker removes no significant further spread (ratio 0.90). The
  two single-source reranked settings (73.5 and 71.9 mean) both stay well below hybrid + reranker
  (79.9). They still spread more than hybrid + reranker for vector (SD 3.58 vs 2.05).

**Verdict C2: partly supported.** Independent of the retrieval source, the reranker narrows chunking
differences by 25-40%, roughly halving the largest effects. It does not remove them, and it keeps
the order. Chunking still matters by 4-11 points under a reranked single source. The reranker is a
partial normaliser: it overlaps with what hybrid fusion already does, rather than adding to it. That
is why the effect is invisible in C1 and visible here.

---

## Hypothesis D: overlap's advantage is an artifact of how the questions were built

**1. Quote length vs overlap width.**
- The prompt and filter capped quotes at 10-300 characters. The median is 119; p10 is 54 and p90 is
  214.
- The 64-token overlap is a median of 229 characters wide (p10 163, p90 290).
- Of the quotes that ft512o0 splits, ft512o64 puts 98-100% back into a single chunk, in every length
  band.

**2. Split quotes are not over-represented.** 5.85% are split, slightly below the ~7% expected from
quote length ÷ chunk length.

**3. Quotes are whole sentences, so sentence-aware chunking gets the same protection with no
overlap.** Share of gold quotes split by a chunk boundary:

| ft512o0 | ft1024o0 | sem512 | sempool512 | ft512o64 | sent512 | sent512s | ft512o64s | sent512rc |
|---|---|---|---|---|---|---|---|---|
| 5.85% | 2.70% | 0.64% | 0.54% | 0.25% | 0.20% | 0.29% | 0.15% | 0.10% |

**4. The effect's size depends on the coverage threshold.** ft512o64 - ft512o0 in (a):

| Hit requires | Overlap gain |
|---|---|
| >= 50% coverage | +1.28 [-0.10, +2.65], n.s. |
| >= 70% | +1.82 [+0.44, +3.24] |
| >= 90% (frozen primary) | +2.06 [+0.59, +3.49] |
| 100% | +2.21 [+0.74, +3.69] |
| whole quote inside one chunk | +3.59 [+2.01, +5.11] |

A split quote still has about half its characters retrieved without overlap: mean coverage 0.54 in
(a).

**5. Overlap does not help find the right article.** Article-level hit@5 (any top-5 chunk from the
gold article), ft512o64 - ft512o0:

| Setting | Difference |
|---|---|
| (a) | +0.64 [-0.39, +1.67] |
| (d) | +0.34, n.s. |
| (b) | -0.29, n.s. |
| (c) | +0.64, n.s. |

The only significant gain is in BM25, on the split questions (+9.2 [+4.2, +15.1]).

**Verdict: partly supported.**
- **The mechanism is real.** A chunk holding half an answer gives a reader half the evidence, and
  fixed-token chunking without overlap causes exactly that.
- **The measured size (~2 points) comes from the benchmark's design.** The answers are short,
  sentence-shaped and single-span, and the metric needs 90% of the quote.
- **What the eval actually measures** is "don't cut answers in half". Overlap, sentence chunking and
  section cuts all achieve it.
- **A fair test of overlap** needs answers longer than the overlap width, or spanning several
  sentences or paragraphs.

---

## Summary

| Hypothesis | Verdict | Key evidence |
|---|---|---|
| Section cuts help retrieval | Supported (vector-based retrieval) | +2.5 to +7.9 in (a), (d), (b); none in BM25. Real boundaries beat random cuts without the reranker. |
| Overlap has no advantage | Not supported | +2.06 in (a), +2.21 in (c); null in (b) and (d) |
| Semantic chunking beats fixed/sentence + sections | Not supported | worse than the section arms in (a) and (c); tied in (b) |
| A: size matters, depends on question/text | Mostly supported | vector -5.8 vs BM25 +5.3; methods/results and digit-dense quotes hurt most; hybrid + reranker flattens it (-0.3), vector + reranker does not (-3.5) |
| B: overlap wins via larger chunks | Rejected | same chunk size; the gain is entirely on the 5.85% of quotes that are split |
| C1: reranker normalises in hybrid | Not supported as stated | hybrid fusion compresses the spread; the reranker adds a near-uniform +14-18 |
| C2: reranker normalises for any source | Partly supported | single source: spread 25-40% narrower (SD ratio 0.75 vector, 0.60 BM25, both significant); order kept, vector size penalty and section gain remain significant; no extra compression on top of hybrid fusion |
| D: overlap gain is a benchmark artifact | Partly supported | real mechanism; size set by the 300-character, sentence-shaped quotes and the 90% threshold |

**Practical consequences:**
- Use section cuts.
- Keep the overlap, or cut on sentence boundaries.
- Keep chunks at 512 tokens for vector-based retrieval.
- Don't adopt semantic chunking.

**Benchmark improvements for next time:**
- longer and multi-span answers;
- question-type labels;
- a chunk size sweep;
- LLM-graded answers alongside hit@k.

Analysis scripts and outputs are in the private experiments repository
(`oa-chunking-eval-2026-10/sections-2026-11/`).
