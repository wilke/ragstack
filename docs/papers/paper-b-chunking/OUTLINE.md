# Paper B — outline

**Working title.** How should a retrieval index chunk full-length scientific articles? Cut position
is what matters, and only at the passage level.

**Target.** arXiv preprint, venue-neutral. Gated: the confirmatory judged results cannot be written as
findings until the two-reader human read has produced κ (§6); the judge-free results are complete.

**Audience.** Anyone who builds or operates a retrieval index over long documents and must pick a
chunking before ingest, knowing the choice cannot be changed in place. The result is specific to
full-length open-access scientific articles (PMC OA, bacteria and viruses), one embedder, one reranker
and one question-writing procedure; it is not specific to the served stack, because every contrast is
reported under vector-only, BM25-only, hybrid and hybrid-plus-reranker retrieval.

**The one-sentence claim.** For full-length scientific articles indexed at about 512 tokens per chunk and
read through hybrid retrieval with a cross-encoder reranker, where a chunk is cut matters and how it is
cut does not: cutting at section boundaries gains +3.49 hit@5 points [+2.11, +4.86] over the fixed
window it is applied to (`oa-sections-ft-setting-a`), a sentence-and-section chunker ties that window
(+0.34 [−0.64, +1.33], non-inferior at a 2-point margin, `h2h-s-minus-ft512o64s`) and keeps reach on an
independent question set (+1.15 points, `reach-quote10-rates`), semantic breakpoint chunking does not
earn its cost (`oa-semantic-pooled-vs-sections-setting-a`), hybrid fusion and reranking compress the
differences without erasing them (`oa-c1-fusion-compresses`, `oa-c1-sd-ratio-hybrid`,
`oa-c2-sd-ratio-vector`), and an evaluation that judges one passage at a time registers answers moved
across a new boundary as losses that retrieval reach does not show (`confirm-excess-d`,
`found10-recovered-a-own`), on short single-section quotes and under a judge whose validity is below
threshold.

**The thesis in two numbers, from the same data.** Under single-passage judging, re-chunking at
sentence and section boundaries loses the *answers* label on 21.4 % of pairs the judge had accepted on
the fixed window, against 4.6 % on a re-read of the identical window (`confirm-control-not-rate`,
`confirm-retest-not-rate`). Under retrieval, the same chunking reaches the gold quote in the top 10 on
91.48 % of units against 90.34 % for the windows (`reach-quote10-rates`). The difference is the object
measured: a passage-level label attaches to a chunk, and a chunking that moves the answer to the
neighbouring chunk changes the label without changing whether the answer is retrieved.

**What this paper does not claim.** That any chunking produces better *answers*: every primary metric is
containment of a short gold quote, and quote coverage is not answer quality. That the judged figures are
correct: the frozen judge's agreement with the single human reader is 0.872 re-weighted
(`judge-validity-reweighted`) against a pre-registered 0.90, 0.82 on the *answers* stratum
(`judge-validity-answers-stratum`), and the two-reader read has not been performed; the paper says so in
its own voice (§6). That the adopted chunker beats the eval's pick: the head-to-head is a tie decided by
a cost order fixed before the run, and the other order would have picked the other arm
(`h2h-decision-and-other-order`). That section cuts are safe for answers that span sections: no gold
quote in the deciding evaluation crosses one (`oa-no-quote-crosses-section`).

**What this paper does NOT claim to have discovered** — established by the literature surveys. Every one
of these is prior art and must be cited as such, not rediscovered. Keys marked *inbox* are verified
entries in `../bib-inbox/04-chunking-segmentation-wide.md` or `05-budget-eval-and-statistical-practice.md`
that have not been promoted to `../bibliography.md`; they must be before the sentence is drafted (§2).

| we do not claim | who has it |
|---|---|
| that naive similarity-breakpoint semantic chunking has not earned its cost | `qu-2025` (NAACL Findings 2025; ten BEIR/RAGBench datasets; they observe the threshold "ultimately determines chunk size") |
| that *informed* segmentation (summary-guided, learned, domain-trained) can beat fixed windows | `wang-2025-pic`, `zhao-2025-moc`; `allamraju-2025` only with its zero-scoring baselines stated |
| that the chunk-size confound was asked about | `zhou-2026` (*inbox*) asks it verbatim and answers with a per-query correlation, not a matched comparison |
| that nominal size is controlled, realised size bounded or reported, or delivered budget matched | `amiri-2025`, `demoura-2026`, `duarte-2024`, `smith-2024-chroma`, `chen-2024-densex`, `lu-2025-hichunk` (*inbox*) |
| the name for budget-normalised evaluation | **`EM @ l tokens`**, `chen-2024-densex` (*inbox*); adopt it |
| the name for the reach/containment phenomenon | **"within-document retrieval failure"**, `kobeissi-2026` (*inbox*); adopt it, keep *reach* and *containment* for the two measured quantities |
| that effectiveness can be flat across passage lengths of 50–450 words | `kaszkiel-2001` (*inbox*): the threat that "the labels were meaningless" does not imply "and it cost performance" |
| that segment-length distribution confounds segmentation metrics | `pevzner-2002` (*inbox*), WindowDiff replacing P_k for exactly this reason |
| that long-document benchmarks front-load their evidence | `boytsov-2025` (*inbox*); answered here by `position-oracle-past-1024` |
| that answer quality against delivered context is an inverted U, so retrieval quality is an upper bound | `jin-2025` (*inbox*) |
| the powered-null rule and equivalence testing | `webber-2008` (*inbox*), `lakens-2017` (*inbox*), `fuhr-2017` with `sakai-2020` (*inbox*) |
| that judges can be reliable without being valid | `norman-2026` (*inbox*) |
| that evidence for ordinary questions about papers often spans several paragraphs or locations | `dasigi-2021-qasper` (55.5 % multi-paragraph among text-evidence answerable questions), `baumgartner-2025-peerqa` (30 % non-consecutive) |
| that cross-section questions over IMRaD articles are a setting | `mortezaagha-2026-gralc`, synthetic template questions |

**The claim that survives.** The field controls *nominal* chunk size, bounds or reports *realised* size,
and matches *delivered* budget, but no published comparison of chunking methods conditions on matched
realised length while scoring at the passage level over full-length articles. We do both, with
pre-registered margins and the construction facts stated: at matched realised size and delivered
context, the method label (fixed, sentence, semantic) moves hit@5 by less than the cut position does,
the one large effect (section cuts) is partly a shorter-chunk effect under the reranker
(`oa-sections-vs-random-cuts-setting-a`), and the document-level metrics that most of the literature
reports would not have seen any of it (`rescore-gap-k1-tok512-n1`).

Things the surveys looked for and did not find, which is what the contribution rests on:

1. **No published method comparison at matched realised chunk length.** Searched the chunking survey's
   full set (`../bib-inbox/04-chunking-segmentation-wide.md`, "searched and did not find"); the nearest
   is `zhou-2026`'s correlation.
2. **No non-inferiority test in IR or NLP.** Every hit was clinical or biostatistical (`05-…`); the
   margins here (2.0 and 3.0 hit points) are stated as smallest effects of interest per `lakens-2017`.
3. **No passage-level, budget-normalised chunking comparison over full-length scientific articles with a
   count-matched random-cut control.** `mortezaagha-2026-gralc` is the nearest setting and imposes its
   question property by template.
4. **No measurement of what single-passage judging does to a chunking comparison.** The judged-loss
   versus retrieval-reach contrast (§5.7) has no precedent in the surveys.

**The roster thesis, read against the evidence.** The roster states the argument in four clauses. Each
is tested here against the committed record, and the thesis is restated afterwards.

| clause | verdict | what the record says |
|---|---|---|
| *Document-level metrics answer a different question* | **supported, and narrowed to top-1** | Gap@1 of 0.369 at one document, resolved, judge-free (`rescore-gap-k1-tok512-n1`); the gap vanishes at k = 10 and two pre-registered k = 10 predictions failed. True for a consumer that acts on the first hits; not badly misleading for one that reads ten passages. |
| *Realised chunk size confounds chunking method* | **changed: from a claim about the literature to a control in our own design, and the confound is inside our own headline** | Where realised size and delivered context were matched, the method label did little: S ties ft512o64s at matched context (`h2h-s-minus-ft512o64s`, `h2h-ctx5`); overlap's gain is not a size effect (`oa-overlap-chunk-size-unchanged`). Where they were not matched, the one large effect is partly a size effect: under the reranker, section cuts do not beat count-matched random cuts (`oa-sections-vs-random-cuts-setting-a`), and the judged adopted-spec chunks are 12 % shorter than the windows (`confirm-realised-chunk-tokens`). The older study's "semantic chunker collapsed to ~350 tokens" exists only in design notes, not in a committed artifact, and is dropped. |
| *Reach and containment trade against each other* | **weakened to a calibration reading; restated as a measurement effect** | The trade is visible on 10 development topics under a judge (`s0bprime-reach-containment-256`, `s0bprime-reach-containment-2048`); the confirmatory run never ran. What the newer record shows is the same tension from the other side: a chunking that keeps reach (`reach-quote10-rates`) loses single-passage *answers* labels (`confirm-excess-d`) because the answer moved to the neighbouring chunk (`confirm-cover-any`, `found10-recovered-a-own`). Containment is a property of the evaluation unit as much as of the chunking. |
| *Hybrid retrieval flattens the contrast* | **supported in part and made precise: fusion compresses, reranking does not add to it, and neither erases** | Hybrid fusion halves the vector-only spread across arms (`oa-c1-fusion-compresses`); the reranker adds a near-uniform lift (`oa-c1-reranker-lift`) and no significant further compression on top of fusion (`oa-c1-sd-ratio-hybrid`); on a single source it compresses by 25–40 % and keeps the order (`oa-c2-sd-ratio-vector`). Chunking moves hit@5 by 2–6 points in production even so, and the size effect that fusion hides (`oa-size-setting-a`) survives under vector + reranker. |

**Revised thesis.** Chunking full-length scientific articles must be decided at the passage level and at
matched realised chunk length and delivered context, because document-level metrics are blind to it at
top-1 and unmatched comparisons measure size, not method. Under those controls, *where* a chunk is cut
is the only chunking choice with a confirmed effect in the production setting: section boundaries gain
3.5 hit@5 points on a fixed window, a sentence-and-section chunker ties that window and keeps reach on
an independent question set at 13 % less context, and semantic breakpoint chunking is worse than either
at several times the cost. Hybrid fusion compresses the differences between chunkings and the reranker
compresses the same thing, so they do not add up and they do not reach zero. What a boundary-respecting
chunking loses is visible only to an evaluation that judges one chunk at a time: the answer moves to the
neighbouring chunk and remains retrieved. All of this holds for short, single-section answer quotes and
one embedder, reranker and domain; every judged figure carries a judge whose validity is below threshold,
and the quote-based figures measure containment, not answer quality.

---

## Sections

### 1. Problem

The decision: a collection's chunking is fixed at creation and cannot be changed in place, so an operator
must choose method, size, overlap and cut rule before ingest. The obvious measurement, document-level
nDCG or recall on a benchmark of short documents, answers a different question: at a corpus of one
document the document-level hit is 1.000 by arithmetic while the top-ranked chunk lands in the answering
section on 0.63 of queries (`rescore-gap-k1-tok512-n1`), and the gap persists at 100 documents
(`rescore-gap-k1-tok512-n100`); the passage task is NOT TRIVIAL where the document task is TRIVIAL
(`rescore-trivial-passage-n1`). Both are judge-free. The gap is a top-1 phenomenon and vanishes at
k = 10, which is reported as two failed pre-registered predictions, not hidden: it bounds the claim to
consumers that act on the first hits.

State the target population: full-length PMC OA scientific articles, where the evidence is usually not in
the front matter. 55.4 % of judged evidence starts past token 1,024 and 11.9 % sits in the abstract or
introduction (`position-oracle-past-1024`; judge-dependent, Markdown-only). This is the direct answer to
`boytsov-2025`'s front-loading threat, and it is why short-abstract benchmarks (SciFact: 1.18 chunks per
document, `scifact-tok256-delta`) cannot test the question.

### 2. Related work

From `../bibliography.md` only. Each entry is marked **support** (a controlled result we lean on) or
**mention** (cited for a concept, a name or a counter-reading; never for a number). Concede generously,
then locate the gap.

**Chunking and segmentation** (`../bibliography.md` § *Chunking and segmentation for retrieval*):

| key | weight | what it controlled for | role here |
|---|---|---|---|
| `qu-2025` | **support** | fixed vs breakpoint vs clustering chunkers, ten datasets, three tasks; did *not* report realised token length; chunk-size hyperparameter is number of chunks | the published negative on naive semantic chunking; their limitations name our three gaps (contextual embeddings, chunk-level gold, long documents); they share our confound rather than having controlled it |
| `wang-2025-pic` | mention | summary-guided segmentation, open-domain QA Hits@k | the positive result for *informed* segmentation; distinguishes the two things called "semantic chunking"; we tested only the first |
| `zhao-2025-moc` | mention | learned mixture of chunkers | same distinction |
| `kreileder-2026` | mention, with caveats | theses, RAGAs, unpowered null | consistent with `qu-2025`, not independent confirmation |
| `allamraju-2025` | mention, engage | PMC full text, PubMedQA; baselines at MRR 0.00–0.01, 115 queries, realised sizes unreported | the closest domain; the 24× is a statement about the baseline; realised-size confound present and uncontrolled |

**Evidence dispersion and question type** (`../bibliography.md` § *Evidence dispersion and question
type*): this section exists because the deciding evaluation's quotes never cross a section
(`oa-no-quote-crosses-section`) and are short single spans (`oa-quote-median-chars`), so the paper must
say what population of questions that excludes.

| key | weight | role here |
|---|---|---|
| `dasigi-2021-qasper` | **support** (base rate, denominator stated) | 55.5 % multi-paragraph evidence among text-evidence answerable questions: ordinary questions about papers often draw on several paragraphs |
| `baumgartner-2025-peerqa` | **support** (base rate), mention (difficulty null) | 30 % non-consecutive evidence; no consistent difficulty difference reported, unpowered |
| `qin-2026-lurch` | mention | the vocabulary: evidence scope, dispersion, reasoning depth as separate axes |
| `buchmann-2024-attribute` | mention | multi-evidence questions are enumeration or aggregation, an assertion at dataset level |
| `mortezaagha-2026-gralc` | mention, engage | nearest setting (IMRaD PMC, cross-section questions), template-imposed; content retrievers "always retrieve from a single section" |
| `zhu-2021-tatqa`, `min-2019-compositional`, `chen-2021-finqa`, `welbl-2018-wikihop`, `pang-2022-quality`, `ma-2024-mmlongbench` | mention | composite form and dispersed evidence come apart; cite only for that |

**Annotation agreement** (`hofstatter-2020-fira`, `mathew-2021-hatexplain`, `warfield-2004-staple`):
not cited here; they belong to Paper A.

**Gaps the related-work section cannot fill from `../bibliography.md` as it stands** (defects to fix
before drafting; each needs a verified entry, not a recalled one):

1. The chunk-size confound thread named in `../README.md`: `zhou-2026`, `amiri-2025`, `demoura-2026`,
   `duarte-2024`, `smith-2024-chroma`, `chen-2024-densex`, `lu-2025-hichunk`, `kaszkiel-2001`,
   `pevzner-2002`, `boytsov-2025`, `jin-2025`, `cuconasu-2024`, `kobeissi-2026` — all in
   `../bib-inbox/04-…` only.
2. The statistical-practice thread: `webber-2008`, `lakens-2017`, `fuhr-2017`, `sakai-2020` — in
   `../bib-inbox/05-…` only.
3. Hybrid fusion and cross-encoder reranking as normalisers of first-stage differences: no entry in
   either file; §5.4's C1/C2 result has no literature to be placed against.
4. Parent-document / small-to-big retrieval and late chunking (`gunther-2024`, `conti-2025`, inbox
   only): the absence of a peer-reviewed base is itself a finding and needs the search recorded.
5. Section-aware chunking of IMRaD articles beyond `mortezaagha-2026-gralc`; structure-aware
   segmentation of JATS or PDF: no entry.
6. LLM question-writing for retrieval evaluation and the "echo" selection rule: no entry; the
   question-writer threat (§6) cannot be placed.

### 3. Design and what was frozen

Four pre-registered instruments and two exploratory follow-ups, in the order a reader needs them, not
the order they were run:

- **The OA chunking eval** (`../../plans/results/oa-chunking-eval-2026-10/PLAN-AND-PASS-RULE.md`):
  ten arms, six retrieval settings, hit@5 on quote coverage ≥ 90 %, a pass rule with cost classes
  (cheap: non-inferiority; mid and expensive: superiority), margins set from a 300-question noise run and
  frozen at the 2.0-point floor (`oa-margins-floor`), a Bonferroni stratum guard over journal family and
  length band, a paired-ladder tie-break by cost rank. What was frozen before the main run: the arms,
  the decision set rule, the margins, the primary setting (hybrid + reranker). What was added after the
  freeze and before any main-run retrieval: setting (d), hybrid without reranker. What is post-hoc and
  labelled so: hypotheses A–D and settings (e), (f).
- **The head-to-head** (`../../plans/results/adopted-spec-h2h-2026-11/PREREG.md`): the adopted spec S
  against the eval's pick on the eval's own questions and collections; G1 drift gate, H1
  non-inferiority at 2.0, H2 superiority, H3 segmenter; cost order B fixed before the run with the
  analyst's exposure to every reused arm declared and the other order's decision always printed.
- **The reach test** (`../../plans/results/reach-rechunk-2026-11/PREREG.md`): quote@10 under S versus
  fixed windows on an independent question set; non-inferiority at δ = 3 then superiority, exact
  sign-flip over papers; judged found@10 demoted to descriptive before any S outcome existed.
- **The boundary-confirmation test** (`../../plans/results/alias-boundary-confirm-2026-11/PREREG.md`):
  single-passage judge loss on a fresh seeded draw against the judge's own re-read noise; six validity
  gates read before the p.
- **Exploratory, labelled so in every sentence:** rechunk stage 1/1b (is the neighbouring chunk
  retrieved) and the split-answer step (do judge-time neighbours recover split answers).
- **The older chunking study** (`../../plans/results/design/SPEC-confirmation-run-r3.md`,
  `../../plans/results/stage0/`, `rescore/`): the pre-registered passage-vs-document family R1 (resolved)
  and the calibration that stopped on power. Its constraints are results: TREC CDS has 90 topics in
  total, the joint bootstrap power at 80 topics was 0.37–0.56 on five of six contrasts
  (`s0-joint-power-at-80`), the corpus is 32,663 documents (`s0-corpus-documents`), and the 80
  confirmation topics stay quarantined.

Constraints that bounded the design and belong here, not in a limitations paragraph: one embedder
(SFR-Embedding-Mistral, 4,096-token window, so 1,024-token chunks fit and 4,080 is the cap); one
reranker over 50 candidates (so first-stage misses survive it, `oa-c2-sd-ratio-vector` note); the
question writer's shared endpoint and politeness limits (≤ 4 in flight); the 1,200-character passage
carried over from an earlier experiment with no written rationale, which is why answers are short; the
per-tenant collection quota; and the decision that the noise subset which set the margins is excluded
from the decision set.

### 4. Data

- **Corpus (OA eval, head-to-head):** 22,593 PMC OA bacteria/virus articles (`oa-corpus-articles`),
  extracted once into 241,930 article, table and figure records identical in every arm
  (`h2h-records-main`); about 20,000 articles hold no question and serve as distractors.
- **Questions (OA eval):** 2,592 passages (median 1,242 characters, `oa-passage-median-chars`; section
  types `oa-passage-section-types`) → 2,437 LLM-written question/quote pairs passing programmatic checks
  → 2,335 clean → **2,035 decision set** (`oa-decision-set-n`); quotes median 119 characters
  (`oa-quote-median-chars`), none crossing a section (`oa-no-quote-crosses-section`).
- **Questions (reach, confirmation, stage 1, split-answer):** the alias study's 1,091 accepted term
  questions on 408 papers, 1,089 Q-eligible, in four corpus collections → 4,356 paired units
  (`reach-units`); 452,729 adopted-spec chunks (`reach-s-chunks-total`); 351 confirmation pairs from
  218 questions (`confirm-pairs`).
- **Older study:** 32,663-document PMC OA corpus, 90 TREC CDS topics (10 development, 80 quarantined);
  260 Leg-B known-item queries for the re-score (`rescore-gap-k1-tok512-n1`).
- **Released and not released:** aggregates, gates, hashes, provenance blocks and the harness code are
  committed; question texts, passages, quotes, per-question files, the PMCID list and the judge
  registries are private with sha256 pins. Point at `../../plans/results/RUN-PACKAGE.md` for the older
  study and at each study's `TEST-DATA.md` for the chain from source to analysed sample; do not restate
  them.

### 5. Results

Every number cites a claim id. Each subsection states its label (confirmatory / exploratory /
descriptive), its n, whether it is judge-free, and the power floor beside every null or unresolved
contrast.

**5.1 Document-level metrics answer a different question.** Confirmatory (pre-registered family R1),
judge-free, n = 260. Gap@1 of 0.369 at one document and 0.338 at 100, resolved against a floor of 0.084
(`rescore-gap-k1-tok512-n1`, `rescore-gap-k1-tok512-n100`); passage NOT TRIVIAL where document TRIVIAL
(`rescore-trivial-passage-n1`). Report the two failed k = 10 predictions as failures: the gap is a
precision phenomenon.

**5.2 Where the evidence is.** Descriptive, judge-dependent, 90 topics. 55.4 % past token
1,024 (`position-oracle-past-1024`). Markdown-only; a committed JSON aggregate is a precondition for
drafting this paragraph.

**5.3 Reach and containment trade against each other.** Descriptive calibration, judge-dependent, n = 10
development topics; the confirmatory run did not proceed. 256-token chunks: reach 0.277, containment
0.188; 2,048-token: 0.112 and 0.696 (`s0bprime-reach-containment-256`,
`s0bprime-reach-containment-2048`). Overlap at the document level on Leg A: unresolved, n = 10, floor
0.077 (`lega-overlap-unresolved`); at the passage level: unresolved by the pre-registered rule
(`rescore-overlap-512-unresolved`). Never read either as a null.

**5.4 What chunking does to hit@5 on 22,593 articles.** Judge-free, n = 2,035, half-width 1.22 points in
the production setting, where the server default reaches 79.02 % (`oa-hit5-setting-a-baseline`).
- *Section cuts* (pre-specified contrasts, Holm): +3.49 [+2.11, +4.86] on the fixed window
  (`oa-sections-ft-setting-a`), +2.51 on sentence chunks (`oa-sections-sent-setting-a`); +7.96 and +6.09
  under vector-only (`oa-sections-ft-vector-only`, `oa-sections-sent-vector-only`); −0.29 [−1.72, +1.13]
  under BM25, unresolved at a 1.4-point half-width (`oa-sections-ft-bm25-null`). Against count-matched
  random cuts: +4.82 under vector-only (`oa-sections-vs-random-cuts-vector`) but +0.84 [−0.39, +2.11]
  under the reranker (`oa-sections-vs-random-cuts-setting-a`): the realised-size confound inside the
  headline result, stated in the sentence. The eval's pick retrieves 14 % less text
  (`oa-ft512o64s-context-ratio`).
- *Overlap* (pre-specified whole-set contrast; mechanism post-hoc): +2.06 [+0.59, +3.49], all of it on
  the 119 questions a plain cut splits (`oa-overlap-setting-a`); chunks are not larger
  (`oa-overlap-chunk-size-unchanged`); 5.85 % of quotes split without overlap against 0.20 % under
  sentence cuts (`oa-split-quote-share-ft512o0`). Exploratory label: hypotheses B and D.
- *Size* (exploratory A, two sizes): −5.80 under vector-only (`oa-size-vector-only`), +5.31 under BM25
  (`oa-size-bm25-only`), −0.29 [−1.82, +1.23] in production, unresolved (`oa-size-setting-a`);
  methods-section questions −13.9 under vector-only (`oa-size-methods-vector-only`, subgroup, hint).
- *Semantic chunking* (frozen-rule class tests: did not clear; descriptive contrasts): −2.21 [−3.73,
  −0.69] against sections in production (`oa-semantic-pooled-vs-sections-setting-a`); competitive without
  sections under vector-only, +7.76 (`oa-semantic-pooled-vs-baseline-vector-only`).
- *Fusion and reranking* (exploratory C1, C2): fusion halves the vector-only spread
  (`oa-c1-fusion-compresses`); the reranker adds +14.3 to +17.7 to every arm (`oa-c1-reranker-lift`) and
  compresses hybrid spread by a ratio of 0.90 [0.68, 1.19], not significant (`oa-c1-sd-ratio-hybrid`);
  on a single source it compresses to 0.75 [0.65, 0.88] (vector) and 0.60 [0.45, 0.81] (BM25) and keeps
  the order (`oa-c2-sd-ratio-vector`). After both, chunking matters by 2–6 points in production.

**5.5 The adopted chunker against the eval's pick.** Confirmatory (H1, H2, H3), judge-free, n = 2,035,
power 0.981 at equality (`h2h-power-ni-at-equality`), minimum detectable superiority 1.39
(`h2h-mde-superiority`). 82.85 % against 82.51 % (`h2h-hit5-s`); +0.34 [−0.64, +1.33]
(`h2h-s-minus-ft512o64s`), 5.3 % discordant (`h2h-discordance`), 11 strata none failed
(`h2h-stratum-guard-k`); the tie goes to S under the frozen cost order and to ft512o64s under the other
(`h2h-decision-and-other-order`), with S storing 767,984 chunks against 859,280 (`h2h-stored-chunks-s`)
at the same estimated context (`h2h-ctx5`). Secondary: S − sent512s +0.79 (`h2h-s-minus-sent512s`),
S − server default +3.83 (`h2h-s-minus-ft512o64`), S's lead larger without the reranker
(`h2h-s-minus-ft512o64s-vector-only`), the regex segmenter changing 13 questions and 0.8 % of chunk ids
(`h2h-regex-minus-punkt`, `h2h-chunk-id-overlap`), sensitivity set the same reading
(`h2h-sensitivity-set`).

**5.6 Reach on an independent question set.** Confirmatory (non-inferiority at δ = 3, then
superiority), judge-free, 4,356 units in 408 papers, power 0.966 at the sizing case (`reach-power`).
91.48 % against 90.34 %, +1.15 points (`reach-quote10-rates`); outcome superior (`reach-outcome`),
p = 0.0079 with the size caveat at 4 % discordance (`reach-superiority-p`); 13 % less context
(`reach-context-tokens`); the discordant units sit where the new chunk keeps little of the old window and
the direction is against the construction bias (`reach-s-only-low-coverage`). Secondary and untested:
+2.27 without the reranker, +2.11 under BM25 (`reach-secondary-norerank`, `reach-secondary-bm25`).
Judged found@10 is saturated and descriptive (`reach-j-rates`), with a drift check that measures
stability, not validity (`reach-drift-check`).

**5.7 Single-passage judging registers moved answers as losses.** Confirmatory, judge-dependent,
351 pairs / 218 questions, power 0.81, MDE 7.5 points (`confirm-power-and-mde`). 21.4 % against 4.6 %
(`confirm-control-not-rate`, `confirm-retest-not-rate`), excess 16.8 points (`confirm-excess-d`),
p = 1.2e-11 (`confirm-sign-flip-p`). Descriptive: loss at noise where ≥ 80 % of the window survives
(`confirm-coverage-band-high`); 0 of 67 in single-section documents (`confirm-one-untitled-section`);
the judged chunks are shorter than the windows, median 453 against 512 tokens
(`confirm-realised-chunk-tokens`); 53 of 75 recovered by a neighbouring chunk, 18 split
(`confirm-cover-any`). Exploratory: the neighbouring chunk is retrieved for 35 of 53, the same rate as
the 178 of 276 reference (`found10-recovered-a-own`, `found10-reference-a-own`); "any chunk answers"
saturates at 75 of 75 (`found10-any-answers-after-1b`); the 18 split pairs remain a real loss under
per-pair evaluation (`found10-split-own`); judge-time neighbours recover 8 of 18 against a 23 %
false-*answers* floor, overlapping intervals (`split-neighbour-recovery`, `split-negative-control`,
`split-kept-pairs`).

**5.8 What small document-level evaluations could not see.** Descriptive, judge-free, different
populations. Title-query known-item on 300 records: nothing separates from fixed_tok512, unresolved
(`sevenway-fixed-tok512-rerank-mrr`); SciFact abstracts: +0.023 for 256 tokens, Holm p = 0.077,
1.18 chunks per document (`scifact-tok256-delta`). State why: these corpora are short, the queries are
titles or claims, and the metric is document-level.

### 6. Threats to validity

Lead with the judge, in the paper's own voice. The frozen LLM judge agreed with the one human reader on
0.872 re-weighted (`judge-validity-reweighted`), 0.82 on *answers* (`judge-validity-answers-stratum`),
κ 0.615 (`judge-validity-kappa`), below the pre-registered 0.90, and the two-reader read that would give
κ between readers has not been performed. **What it touches:** every number in 5.2, 5.3, the J family in
5.6, and all of 5.7, including the confirmatory 21.4 % / 4.6 % contrast and every "lost", "recovered" and
"split" count. **What it does not touch:** 5.1, 5.4, 5.5, the Q family in 5.6, and 5.8, which score byte
offsets or quote coverage with no model label. The paper's headline must be built from the judge-free
set, and the judged set presented as the mechanism it suggests, not as the finding.

Then, each named as a competing reading of the main result:

- **Quote coverage is not answer quality.** hit@5 and quote@10 credit the quote's characters in the
  retrieved set; no reader or model was asked whether the answer is usable. `jin-2025`'s inverted U makes
  retrieval quality an upper bound. An answer-level check does not exist in this record.
- **The gold never crosses a section and is short.** 0 of 2,035 quotes cross a section
  (`oa-no-quote-crosses-section`); median 119 characters, p90 214 (`oa-quote-median-chars`); every
  decision-set question comes from one passage grown inside one section region, with 85 of 2,592
  passages drawn over an unheaded body (`oa-passage-section-types`). The deciding
  evaluation therefore cannot register any cost of section cuts on answers that span sections, and
  measures "don't split the answer" for overlap and sentence cuts (hypothesis D). The base rates in
  `dasigi-2021-qasper` and `baumgartner-2025-peerqa` say such answers are common.
- **Single domain, embedder, reranker, question writer.** Bacteria/virus PMC OA only; one embedder
  (SFR-Embedding-Mistral); one cross-encoder (bge-reranker-v2-m3) over 50 candidates; one question-writing
  model and selection rule (lowest echo) for the OA eval, and term questions only for the alias set. The
  reranker's truncation of 1,024-token chunks is a plausible, unverified mechanism behind
  `oa-size-setting-a`.
- **The realised-size confound is inside our own section result.** Under the reranker, real section
  boundaries do not beat count-matched random cuts (`oa-sections-vs-random-cuts-setting-a`); the judged
  adopted-spec chunks are shorter than the windows (`confirm-realised-chunk-tokens`). Report section cuts
  as "cut position and shorter chunks together" wherever the reranker is on.
- **Adoption preceded some of the evidence.** The production chunker was adopted on the OA eval and the
  reach test; the head-to-head against the eval's own pick, and the tolerance-and-merge knobs' only
  evidence (`h2h-s-minus-sent512s`), came after. Both later tests were pre-registered with the analyst's
  exposure declared and the other cost order's decision printed.
- **The head-to-head is a tie decided by cost.** +0.34 [−0.64, +1.33] with MDE 1.39
  (`h2h-s-minus-ft512o64s`, `h2h-mde-superiority`); under order A the decision is ft512o64s
  (`h2h-decision-and-other-order`). The paper reports a tie and a cost argument, never a win.
- **Biases that favour the baseline in the reach test** (quotes drawn from inside one window; chance
  matches at mid-sentence edges) run against the hypothesis; the W runs are from an older server version
  with the same configuration hash; J's orphans count against S.
- **Saturation.** Judged found@10 near 98 % (`reach-j-rates`) and "any chunk answers" at 75 of 75
  (`found10-any-answers-after-1b`) cannot separate chunkings on term questions; only per-pair measures can.
- **Low-discordance tests have asymptotic size.** The reach test's size at 4 % discordance was not
  simulated (`reach-superiority-p`); H3's margin could not have failed (`h2h-regex-minus-punkt`).
- **Many subgroup cells were read.** Hypotheses A–D and the per-section, per-density cells are hints;
  the paper quotes them as exploratory in the sentence.
- **Reproducibility of the deciding numbers.** The OA eval's per-arm, per-setting values and
  pre-specified contrasts exist in the public record only as Markdown aggregates; the per-question files
  are private. A committed JSON aggregate is a precondition, not a nicety.

### 7. Reproducibility

Per study: the pre-registration sha256 and signed tag (`prereg-reach-rechunk-2026-11` at `f78af6ee`,
`prereg-adopted-spec-h2h-2026-11` at `f4d4ef9a`, `prereg-alias-boundary-confirm-2026-11` at `016fb61`),
the frozen commit, the `provenance` block with `citable: true` (`reach-provenance-citable`), the seeds
(bootstrap seed 0, 10,000 resamples; the draw and drift seeds in the artifacts), the model ids
(SFR-Embedding-Mistral 4096-d; bge-reranker-v2-m3; Qwen3.6-35B-A3B and Qwen3.8-27B judges with a
Gemini tiebreak and a Claude drift reference; the Qwen3.8 question writer), the tools-image builds
(v1.6.24 build 1 with Punkt, build 2 without) and the server version checked on every retrieval row.
For the older study, `../../plans/results/RUN-PACKAGE.md`. What another site must supply: the PMC OA
corpus and the private PMCID list, the question and quote files (hash-pinned), a RAGStack deployment
with the same retriever settings (the configuration hash is compared), gateway access for the judge
tiebreak, and the two-reader human read this record lacks.

---

## Working rules for this draft

- Every number cites a claim id from `claims.json` (90 entries, all verified). Run `../check_claims.py`
  with `/rag/envs/ragstack/bin/python3` before any commit that touches the draft.
- Read the `note` field before writing the sentence. Three note tags change what may be said:
  `JUDGE-DEPENDENT` (the validity flag goes in the sentence), `MARKDOWN-ONLY` (the number has no committed
  JSON; the defect is fixed before the sentence is drafted, or the sentence says the record is a
  Markdown aggregate), `SECONDARY` / `EXPLORATORY` (labelled in the sentence, not the caption).
- No status, no dates, no roadmap; those live in the lab book.
- Absent is not passed. Subgroup n for the OA eval's section-type cells is *absent*.
- Powered nulls and unresolved contrasts are never merged: the BM25 section contrast, the production size
  contrast, the h2h superiority and the Leg A overlap are unresolved; none is a null.
- A tie decided by cost order is reported as a tie, with the other order's decision beside it.
- Name the tokenizer for every token count; h2h ctx@5 is chars/4 and says so.
- Quote n whenever it differs from the headline n (119 split quotes; 53 recovered pairs; 18 split pairs;
  10 development topics).
