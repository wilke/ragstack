# Paper B — outline

**Working title.** How should a retrieval index chunk full-length scientific articles? Section-cut
windows, matched sizes, and what passage-level evaluation can and cannot see.

**Target.** arXiv preprint, venue-neutral.

**Audience.** Anyone who builds or operates a retrieval index over long documents and must pick a
chunking before ingest, knowing the choice cannot be changed in place. The result is specific to
full-length open-access scientific articles (PMC OA, bacteria and viruses), one embedder, one reranker
and one question-writing procedure; it is not specific to the served stack, because every contrast is
reported under vector-only, BM25-only, hybrid and hybrid-plus-reranker retrieval.

**The one-sentence claim.** For full-length scientific articles indexed at about 512 tokens per chunk and
read through hybrid retrieval with a cross-encoder reranker, scored by whether the top-5 chunks contain a
short single-section gold quote: a 512/64 window cut at section boundaries beats the same window without
section cuts by +3.49 hit@5 points [+2.11, +4.86] (`oa-sections-ft-setting-a`; the section arms were
pre-specified in a private Addendum C after the six-arm main run); in the production setting, on sentence
chunks (the only arm with a count-matched random-cut control), real section cuts are not shown to beat
random cuts: +0.84 [−0.39, +2.11], unresolved, against a section gain of +2.51
(`oa-sections-vs-random-cuts-setting-a`, `oa-sections-sent-setting-a`), while under vector + reranker they
do, +2.11 [+0.69, +3.54] (`oa-sections-vs-random-cuts-vector-reranker`); no random-cut control exists for
the fixed window; overlap moves hit@5 too (+2.06
[+0.59, +3.49], `oa-overlap-setting-a`), as do a size tolerance and small-piece merge (+0.79 [+0.10, +1.47],
secondary, `h2h-s-minus-sent512s`); a sentence-and-section chunker is non-inferior to the section-cut
window at a 2-point margin and not shown better (+0.34 [−0.64, +1.33], `h2h-s-minus-ft512o64s`) and,
against plain `fixed_token/512/0` windows on an independent term-question set, reaches the quote more
often (+1.15 points, superior, `reach-quote10-rates`) with 13 % less context (`reach-context-tokens`);
capped semantic_pooled chunking scores −2.21 [−3.73, −0.69] below the section-cut window (unadjusted,
descriptive, `oa-semantic-pooled-vs-sections-setting-a`), does not clear its frozen superiority test, and
its realised chunk size was not measured. Exploratorily, hybrid fusion compresses the differences between
chunkings and the reranker adds a near-uniform lift, and its further compression on hybrid is not
significant (SD ratio 0.90 [0.67, 1.19]; `oa-c1-fusion-compresses`, `oa-c1-sd-ratio-hybrid`). Exploratory
and judge-dependent: when a
boundary-respecting chunking makes a single-passage judge withdraw *answers*, another chunk answers for
53 of 75 pairs and is in the top 10 for 35 of 53, the rate for unaffected pairs (178 of 276); 18 have no
single answering chunk (`confirm-cover-any`, `found10-recovered-a-own`, `found10-reference-a-own`).

**The thesis in two numbers, on the same question set and chunkings.** Both numbers compare the
sentence-and-section chunker with plain `fixed_token/512/0` windows on the alias study's term questions,
but on different units and metrics. Judge-free, 4,356 question-collection units: the new chunking reaches
the gold quote in the top 10 on 91.48 % against 90.34 % (`reach-quote10-rates`), with 62 units reached only
by the windows. Judge-dependent, 351 drawn pairs: a single-passage judge withdraws *answers* from the
largest-overlap new chunk on 21.4 % of pairs it had accepted on the window, against 4.6 % on a re-read of
the identical window (`confirm-control-not-rate`, `confirm-retest-not-rate`; judge validity below threshold,
`judge-validity-reweighted`). The paper's finding is the first number; the second is the mechanism it
suggests, offered with its flag: a passage-level label attaches to one chunk, and a chunking that moves
the answer to a neighbouring chunk can change the label without changing whether the quote is retrieved.

**What this paper does not claim.** That any chunking produces better *answers*: every primary metric is
containment of a short gold quote, and quote coverage is not answer quality. That the judged figures are
correct: the frozen judge's agreement with the single human reader, who also designed the study, is
0.872 re-weighted (`judge-validity-reweighted`) against a pre-registered 0.90, 0.82 on the *answers*
stratum (`judge-validity-answers-stratum`), and the two-reader read has not been performed; the paper says
so in its own voice (§6). That the adopted chunker beats the eval's pick: the head-to-head is a tie
decided by a cost order fixed before the run, and the other order would have picked the other arm
(`h2h-decision-and-other-order`). That the section-cut result rests on a committed pre-specification: the
six-arm pass rule is committed, the four section and random-cut arms were added in a private Addendum C
after the six-arm run, and the head-to-head's re-measurement of +3.49 is a drift gate, not a test. That
section cuts are safe for answers that span sections: no gold quote in the deciding evaluation crosses
one (`oa-no-quote-crosses-section`). That *where* a chunk is cut is all that matters: overlap and the
tolerance-and-merge settings move hit@5 as well.

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
| that evidence for ordinary questions about papers often spans several paragraphs or locations | `dasigi-2021-qasper` (55.5 % multi-paragraph among text-evidence answerable questions as published; re-measured from the released data, 31.4 %, `qasper-multi-paragraph`), `baumgartner-2025-peerqa` (30 % non-consecutive) |
| that cross-section questions over IMRaD articles are a setting | `mortezaagha-2026-gralc`, synthetic template questions |

**The claim that survives.** The field controls *nominal* chunk size, bounds or reports *realised* size,
and matches *delivered* budget, but no published comparison of chunking methods conditions on matched
realised length while scoring at the passage level over full-length articles. What this record adds is
narrower than "we do both": one pre-registered contrast at matched delivered context (the adopted
chunker against the section-cut window, 2,016 against 2,000 estimated tokens at k = 5, `h2h-ctx5`;
matched by outcome and by a chars/4 estimate, not by design), a count-matched random-cut control on the
sentence arm that makes the size-versus-position question answerable and leaves it unresolved in the
production setting (+0.84 [−0.39, +2.11], `oa-sections-vs-random-cuts-setting-a`) while resolving it in
favour of position under vector-only and vector + reranker (+4.82 and +2.11 [+0.69, +3.54],
`oa-sections-vs-random-cuts-vector`, `oa-sections-vs-random-cuts-vector-reranker`), measured realised
lengths for the judged chunks
(`confirm-realised-chunk-tokens`), and one demonstration that the article-level metric misses a chunk-level
effect: overlap moves chunk-level hit@5 by +2.06 [+0.59, +3.49] (`oa-overlap-setting-a`) and article-level
hit@5 by +0.64 [−0.39, +1.67] (`oa-overlap-article-level`), and at a corpus of one document the
document-level hit is 1.000 by arithmetic while the top chunk lands in the gold section on 0.63
(`rescore-gap-k1-tok512-n1`).

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
| *Document-level metrics answer a different question* | **supported, and narrowed to top-1 and to one demonstrated contrast** | Gap@1 of 0.369 at one document, resolved, judge-free (`rescore-gap-k1-tok512-n1`); the gap vanishes at k = 10 and two pre-registered k = 10 predictions failed. On the OA eval, overlap's chunk-level +2.06 (`oa-overlap-setting-a`) is +0.64 [−0.39, +1.67] at the article level (`oa-overlap-article-level`). True for a consumer that acts on the first hits; not badly misleading for one that reads ten passages. |
| *Realised chunk size confounds chunking method* | **changed: from a claim about the literature to a control in our own design, and the confound is inside our own headline** | Where delivered context happened to match, the method label did little: S ties ft512o64s at 2,016 against 2,000 estimated tokens (`h2h-s-minus-ft512o64s`, `h2h-ctx5`); overlap's gain is not a size effect (`oa-overlap-chunk-size-unchanged`). Where sizes were not matched, how much of the one large effect is size depends on the retriever: in the production setting, on the sentence arm, real section cuts are not shown to beat count-matched random cuts (+0.84 [−0.39, +2.11], unresolved, `oa-sections-vs-random-cuts-setting-a`), while under vector-only and vector + reranker most of the gain is position (+4.82 of +6.09; +2.11 [+0.69, +3.54]; `oa-sections-vs-random-cuts-vector`, `oa-sections-vs-random-cuts-vector-reranker`); no random-cut control exists for the fixed window. The judged adopted-spec chunks have a median of 453 tokens against the windows' 512 (`confirm-realised-chunk-tokens`). The semantic arms' realised size was never measured. The older study's "semantic chunker collapsed to ~350 tokens" exists only in design notes, not in a committed artifact, and is dropped. |
| *Reach and containment trade against each other* | **weakened to a calibration reading; restated, exploratorily, as a measurement effect** | The trade is visible on 10 development topics under a judge (`s0bprime-reach-containment-256`, `s0bprime-reach-containment-2048`); the confirmatory run never ran. The newer record shows the tension from the other side, exploratory and judge-dependent: a chunking that keeps quote reach against plain windows (`reach-quote10-rates`, with 62 units reached only by the windows) loses single-passage *answers* labels (`confirm-excess-d`); for 53 of the 75 lost pairs another chunk answers (`confirm-cover-any`), and the pair's own answering chunk is retrieved for 39 of 75 (`found10-all-lost-a-own`) against a 178-of-276 reference (`found10-reference-a-own`); 18 pairs have no single answering chunk, and one reader's offline check of the wall-crossing windows found most answerable from one side (`split-wall-read-one-side`). Containment is a property of the evaluation unit as much as of the chunking. |
| *Hybrid retrieval flattens the contrast* | **supported in part and made precise, exploratorily: fusion compresses, reranking does not add to it, and neither erases** | Exploratory C1/C2: hybrid fusion halves the vector-only spread across arms (`oa-c1-fusion-compresses`); the reranker adds a near-uniform lift (`oa-c1-reranker-lift`) and no significant further compression on top of fusion (`oa-c1-sd-ratio-hybrid`); on a single source it compresses by 25–40 % and keeps the order (`oa-c2-sd-ratio-vector`). Chunking moves hit@5 by about 2–6 points under the reranker even so (`oa-chunking-matters-under-reranker`), and the size effect that the production setting hides (`oa-size-setting-a`) is −3.54 [−5.21, −1.92] under vector + reranker (`oa-size-vector-reranker`). |

**Revised thesis.** Chunking full-length scientific articles should be decided at the passage level,
because at top-1 a document-level metric cannot see which chunk was retrieved, and at matched realised
chunk length and delivered context, because an unmatched comparison can mix size with method: in the
production setting the sentence-chunk section gain is not separated from count-matched random cuts,
while under vector-only most of it is position. On 22,593 PMC articles and 2,035 short single-section
quotes under hybrid retrieval with a cross-encoder reranker, a 512/64 window cut at section boundaries
beats the unsectioned window by +3.49 hit@5 points [+2.11, +4.86]; on the sentence arm, the only one with
a random-cut control, section cuts are not shown to beat random cuts in that setting (+0.84 [−0.39,
+2.11], unresolved) and do under vector + reranker (+2.11 [+0.69, +3.54]); overlap and a size tolerance
with small-piece merge move hit@5 as well. The sentence-and-section chunker adopted for production is
non-inferior to the section-cut window at a 2-point margin and not shown better; against plain 512-token
windows on an independent term-question set it reaches the quote more often (+1.15 points) with 13 % less
context (not size-matched: S's top 10 carries 13 % less text). Capped semantic_pooled chunking scores
−2.21 [−3.73, −0.69] below the section-cut window (unadjusted, descriptive), fails its frozen superiority
test, and its realised chunk size was not measured. Exploratorily, hybrid fusion compresses the differences
between chunkings and the reranker adds a near-uniform lift whose further compression on hybrid is not
significant (SD ratio 0.90 [0.67, 1.19]); about 2–6 points remain. Exploratory and
judge-dependent: when a boundary-respecting chunking makes a single-passage judge withdraw *answers*,
another chunk answers for 53 of 75 pairs and is retrieved for 35 of 53, the rate for unaffected pairs,
while 18 pairs have no single answering chunk. All of this holds for one domain, embedder, reranker and
question writer; the section result's pre-specification is private; every judged figure rests on a judge
whose validity is below threshold; and the quote-based figures measure containment, not answer quality.

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
consumers that act on the first hits. On the deciding evaluation the same blindness shows in one
contrast: overlap moves chunk-level hit@5 by +2.06 (`oa-overlap-setting-a`) and article-level hit@5 by
+0.64 [−0.39, +1.67] (`oa-overlap-article-level`).

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
| `dasigi-2021-qasper` | **support** for the re-measured figures (exploratory, descriptive); mention for the published one | The paper reports 55.5 % multi-paragraph evidence among text-evidence answerable questions; the unit is the paragraph, part of the spread is redundant, and no multi-*section* share is published. The project's re-measurement from the released v0.3 annotations does not reproduce the 55.5 % (30.1 % on the dev split under the paper's definition, `qasper-55-5-not-reproduced`): pooled train+dev, 31.4 % of answers have multi-paragraph evidence (`qasper-multi-paragraph`), 14.2 % multi-section by full path (`qasper-multi-section-full-path`), 10.1 % by top-level section (`qasper-multi-section-top-level`), with Fleiss κ 0.22 / 0.33 / 0.43 between annotators at the three levels (`qasper-kappa-top-level`). Cite the re-measured figures, labelled exploratory, with the paper's figure as what it published. |
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
7. A bibliography note under `dasigi-2021-qasper` recording the re-measurement and its artifact.

### 3. Design and what was frozen

Four pre-registered instruments and two exploratory follow-ups, in the order a reader needs them, not
the order they were run:

- **The OA chunking eval** (`../../plans/results/oa-chunking-eval-2026-10/PLAN-AND-PASS-RULE.md`):
  **six arms frozen** (two fixed windows at 512, one at 1,024, sentence 512, two capped semantic
  chunkers), hit@5 on quote coverage ≥ 90 %, a pass rule with cost classes (cheap: non-inferiority; mid
  and expensive: superiority), margins set from a 300-question noise run and frozen at the 2.0-point
  floor (`oa-margins-floor`), a Bonferroni stratum guard over journal family and length band, a
  paired-ladder tie-break by cost rank. What was frozen before the main run: those arms, the decision set
  rule, the margins, the primary setting (hybrid + reranker). What was added after the freeze and before
  any main-run retrieval: setting (d), hybrid without reranker. **What was added after the six-arm run,
  in an Addendum C that is not committed to this repository:** the four section-cut and random-cut arms
  (ft512o64s, sent512s, sent512sh, sent512rc) and their four pre-specified contrasts (Holm). The paper
  states that the section result rests on a private pre-specification unless Addendum C is committed
  first; the head-to-head's re-measurement of its headline is a drift gate (`in_family: false`), not a
  test. What is post-hoc and labelled so: hypotheses A–D and settings (e), (f), with unadjusted intervals.
- **The head-to-head** (`../../plans/results/adopted-spec-h2h-2026-11/PREREG.md`): the adopted spec S
  against the eval's pick on the eval's own questions and collections; G1 drift gate, H1
  non-inferiority at 2.0, H2 superiority, H3 segmenter; cost order B fixed before the run with the
  analyst's exposure to every reused arm declared and the other order's decision always printed
  (`h2h-prereg-sha`, `h2h-harness-provenance`).
- **The reach test** (`../../plans/results/reach-rechunk-2026-11/PREREG.md`): quote@10 under S versus
  plain `fixed_token/512/0` windows, the alias study's chunking, on an independent question set;
  non-inferiority at δ = 3 then superiority, exact sign-flip over papers; judged found@10 demoted to
  descriptive before any S outcome existed (`reach-prereg-sha`).
- **The boundary-confirmation test** (`../../plans/results/alias-boundary-confirm-2026-11/PREREG.md`):
  single-passage judge loss on a fresh seeded draw against the judge's own re-read noise; six validity
  gates read before the p (`confirm-prereg-sha`, `confirm-provenance`).
- **Exploratory, labelled so in every sentence:** rechunk stage 1/1b (is the neighbouring chunk
  retrieved) and the split-answer step (do judge-time neighbours recover split answers).
- **The older chunking study** (`../../plans/results/design/SPEC-confirmation-run-r3.md`,
  `../../plans/results/stage0/`, `rescore/`): the pre-registered passage-vs-document family R1 (resolved)
  and the calibration that stopped on power. Its constraints are results: TREC CDS has 90 topics in
  total, the joint bootstrap power at 80 topics was 0.37–0.56 on five of six contrasts
  (`s0-joint-power-at-80`), the corpus is 32,663 documents (`s0-corpus-documents`), and the 80
  confirmation topics stay quarantined.

Constraints that bounded the design and belong here, not in a limitations paragraph: one embedder
(SFR-Embedding-Mistral, 4,096-token window with a 4,080-token chunk budget, `chunker-window-budget`, so
1,024-token chunks fit); one reranker over 50 candidates (so first-stage misses survive it,
`oa-c2-sd-ratio-vector` note); the question writer's shared endpoint and politeness limits (≤ 4 in
flight); the 1,200-character passage carried over from an earlier experiment with no written rationale,
which is why answers are short; the per-tenant collection quota; and the decision that the noise subset
which set the margins is excluded from the decision set.

### 4. Data

- **Corpus (OA eval, head-to-head):** 22,593 PMC OA bacteria/virus articles (`oa-corpus-articles`),
  extracted once into 241,930 article, table and figure records identical in every arm
  (`h2h-records-main`); about 20,000 articles hold no question and serve as distractors.
- **Questions (OA eval):** 2,592 passages (median 1,242 characters, `oa-passage-median-chars`; section
  types `oa-passage-section-types`) → 2,437 LLM-written question/quote pairs passing programmatic checks
  (`oa-questions-passing`) → 2,335 clean (`oa-clean-set`) → **2,035 decision set** (`oa-decision-set-n`);
  quotes median 119 characters (`oa-quote-median-chars`), none crossing a section
  (`oa-no-quote-crosses-section`).
- **Questions (reach, confirmation, stage 1, split-answer):** the alias study's 1,091 accepted term
  questions on 408 papers, 1,089 Q-eligible, in four corpus collections → 4,356 paired units
  (`reach-units`); 452,729 adopted-spec chunks (`reach-s-chunks-total`); 351 confirmation pairs from
  218 questions (`confirm-pairs`).
- **Older study:** 32,663-document PMC OA corpus (`s0-corpus-documents`), 90 TREC CDS topics (10
  development, 80 quarantined); 260 Leg-B known-item queries for the re-score
  (`rescore-gap-k1-tok512-n1`).
- **Released and not released:** aggregates, gates, hashes, provenance blocks and the harness code are
  committed; question texts, passages, quotes, per-question files, the PMCID list, the judge registries
  and the OA eval's Addendum C and RESULTS files are private with sha256 pins. Point at
  `../../plans/results/RUN-PACKAGE.md` for the older study and at each study's `TEST-DATA.md` for the
  chain from source to analysed sample; do not restate them.

### 5. Results

Every number cites a claim id. Each subsection states its label (confirmatory / exploratory /
descriptive), its n, whether it is judge-free, and the power floor beside every null or unresolved
contrast. Post-hoc intervals are unadjusted unless marked Holm.

**5.1 Document-level metrics answer a different question.** Confirmatory (pre-registered family R1),
judge-free, n = 260. Gap@1 of 0.369 at one document and 0.338 at 100, resolved against a floor of 0.084
(`rescore-gap-k1-tok512-n1`, `rescore-gap-k1-tok512-n100`); passage NOT TRIVIAL where document TRIVIAL
(`rescore-trivial-passage-n1`). Report the two failed k = 10 predictions as failures: the gap is a
precision phenomenon. Beside it, descriptive and unadjusted: the article-level overlap contrast on the OA
eval, +0.64 [−0.39, +1.67] (`oa-overlap-article-level`), against the chunk-level +2.06.

**5.2 Where the evidence is.** Descriptive, judge-dependent, 90 topics. 55.4 % past token
1,024 (`position-oracle-past-1024`). Markdown-only; a committed JSON aggregate is a precondition for
drafting this paragraph.

**5.3 Reach and containment trade against each other.** Descriptive calibration, judge-dependent, n = 10
development topics; the confirmatory run did not proceed. 256-token chunks: reach 0.277, containment
0.188; 2,048-token: 0.112 and 0.696 (`s0bprime-reach-containment-256`,
`s0bprime-reach-containment-2048`). Overlap at the document level on Leg A: unresolved, n = 10, floor
0.077 (`lega-overlap-unresolved`); at the passage level: +0.025 [0.011, 0.040] exceeds its δ80 of 0.021
but not the pre-registered X_P of 0.05 (`rescore-overlap-512-unresolved`, `rescore-x-p`), unresolved.
Never read either as a null.

**5.4 What chunking does to hit@5 on 22,593 articles.** Judge-free, n = 2,035, half-width 1.22 points in
the production setting, where the server default reaches 79.02 % (`oa-hit5-setting-a-baseline`). Every figure
below resolves to the committed aggregate artifact (`../../plans/results/oa-chunking-eval-2026-10/artifacts/
oa-eval-aggregates.json`, aggregates only, citable provenance), which labels each contrast confirmatory
(the frozen six-arm run), Addendum C (pre-specified privately after the main run) or exploratory; the
section's sentences carry those labels. Two numbers remain Markdown-only and are said to be: the article
count (`oa-corpus-articles`) and the sentence and semantic arms' split-quote shares
(`oa-split-quote-share-sent512`).
- *Section cuts* (four contrasts pre-specified in the private Addendum C after the six-arm run, Holm; the
  paper labels them so): +3.49 [+2.11, +4.86] on the fixed window (`oa-sections-ft-setting-a`,
  re-measured by the head-to-head's drift gate), +2.51 on sentence chunks (`oa-sections-sent-setting-a`);
  +7.91 and +6.09 under vector-only (`oa-sections-ft-vector-only`, `oa-sections-sent-vector-only`); −0.29
  [−1.72, +1.13] under BM25, unresolved at a 1.4-point half-width (`oa-sections-ft-bm25-null`). Against
  count-matched random cuts, a control that exists only on the sentence arm (sent512s − sent512rc; none
  for the fixed window): +4.82 under vector-only (`oa-sections-vs-random-cuts-vector`), +2.11 [+0.69,
  +3.54] under vector + reranker (`oa-sections-vs-random-cuts-vector-reranker`), and +0.84 [−0.39, +2.11]
  in the production setting (`oa-sections-vs-random-cuts-setting-a`): there, real section cuts are not
  shown to beat random cuts against a section gain of +2.51, an unresolved contrast that the sentence
  states as such, never as a null. The eval's pick retrieves 14 % less text
  (`oa-ft512o64s-context-ratio`).
- *Overlap* (pre-specified whole-set contrast; mechanism post-hoc): +2.06 [+0.59, +3.49], all of it on
  the 119 questions a plain cut splits (`oa-overlap-setting-a`); chunks are not larger
  (`oa-overlap-chunk-size-unchanged`); 5.85 % of quotes split without overlap against 0.20 % under
  sentence cuts (`oa-split-quote-share-ft512o0`, `oa-split-quote-share-sent512`); at the article level +0.64
  [−0.39, +1.67]
  (`oa-overlap-article-level`). Exploratory label: hypotheses B and D.
- *Size* (exploratory A, two sizes): −5.80 under vector-only (`oa-size-vector-only`), +5.31 under BM25
  (`oa-size-bm25-only`), −0.29 [−1.82, +1.23] in production, unresolved (`oa-size-setting-a`), −3.54
  [−5.21, −1.92] under vector + reranker (`oa-size-vector-reranker`); methods-section questions −13.9
  under vector-only (`oa-size-methods-vector-only`, subgroup, hint).
- *Semantic chunking.* Capped semantic_pooled scores −2.21 [−3.73, −0.69] below the section-cut window
  in production (unadjusted, descriptive, `oa-semantic-pooled-vs-sections-setting-a`), 80.29 % against the
  server default's 79.02 % (`oa-sempool-hit5-setting-a`), and does not clear its frozen mid-class
  superiority test; it is competitive without sections under vector-only, +7.76 [+5.80, +9.78], the frozen
  run's secondary-setting report (`oa-semantic-pooled-vs-baseline-vector-only`). There is no S − sempool512 contrast. The 4.5–7× embedding
  cost is the *planned* cost of the legacy semantic arm, not semantic_pooled's
  (`oa-legacy-semantic-cost`). The semantic arms' realised chunk size was not measured, so by the paper's
  own rule these are unmatched comparisons.
- *Fusion and reranking* (exploratory C1, C2): fusion halves the vector-only spread
  (`oa-c1-fusion-compresses`); the reranker adds +14.3 to +17.7 to every arm (`oa-c1-reranker-lift`) and
  compresses hybrid spread by a ratio of 0.90 [0.67, 1.19], not significant (`oa-c1-sd-ratio-hybrid`);
  on a single source it compresses to 0.75 [0.65, 0.88] (vector) and 0.60 [0.45, 0.81] (BM25) and keeps
  the order (`oa-c2-sd-ratio-vector`). After both, chunking matters by about 2–6 points
  (`oa-chunking-matters-under-reranker`).

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

**5.6 Reach on an independent question set, against plain windows.** Confirmatory (non-inferiority at
δ = 3, then superiority), judge-free, 4,356 units in 408 papers, power 0.966 at the sizing case
(`reach-power`). The comparator is `fixed_token/512/0`, the alias study's chunking, not the eval's pick.
91.48 % against 90.34 %, +1.15 points (`reach-quote10-rates`); outcome superior (`reach-outcome`),
p = 0.0079 with the size caveat at 4 % discordance (`reach-superiority-p`); 13 % less context than those
windows (`reach-context-tokens`); the discordant units sit where the new chunk keeps little of the old
window and the direction is against the construction bias (`reach-s-only-low-coverage`). Secondary and
untested: +2.27 without the reranker, +2.11 under BM25 (`reach-secondary-norerank`,
`reach-secondary-bm25`). Judged found@10 is saturated and descriptive (`reach-j-rates`), with a drift
check that measures stability, not validity (`reach-drift-check`).

**5.7 Single-passage judging and where the answer went.** Confirmatory, judge-dependent, 351 pairs /
218 questions, power 0.81, MDE 7.5 points (`confirm-power-and-mde`). 21.4 % against 4.6 %
(`confirm-control-not-rate`, `confirm-retest-not-rate`), excess 16.8 points (`confirm-excess-d`),
p = 1.2e-11 (`confirm-sign-flip-p`). Descriptive: loss at noise where ≥ 80 % of the window survives
(`confirm-coverage-band-high`); 0 of 67 in single-section documents (`confirm-one-untitled-section`);
the judged chunks are shorter than the windows, median 453 against 512 tokens
(`confirm-realised-chunk-tokens`); for 53 of 75 lost pairs another overlapping chunk answers, for 18 none
does alone, 4 were judge noise (`confirm-cover-any`). Exploratory, judge-dependent, no test: the pair's
own answering chunk is in production retrieval's top 10 for 39 of 75 lost pairs (`found10-all-lost-a-own`)
against 178 of 276 for unaffected pairs (`found10-reference-a-own`); for the 53 recovered pairs it is 35
of 53 (`found10-recovered-a-own`); "any chunk answers" saturates at 75 of 75 on term questions
(`found10-any-answers-after-1b`); retrieval of the own chunk falls from 73.5 % on the base corpus to
54–62 % on the three larger ones (`found10-a-own-by-corpus`). The 18 split pairs are credited by none of
their own chunks under per-pair evaluation (`found10-split-own`), and one reader's offline check of the
10 wall-crossing split windows found 9 answerable from one side (`split-wall-read-one-side`), so some may
not be split at all; judge-time neighbours recover 8 of 18 against a 23 % false-*answers* floor, with
overlapping intervals (`split-neighbour-recovery`, `split-negative-control`, `split-kept-pairs`).

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
offsets or quote coverage with no model label. The paper's headline is built from the judge-free set,
and the judged set is presented as the mechanism it suggests, not as the finding.

Then, each named as a competing reading of the main result:

- **The rater and the judge.** The single human rater also designed the study. The judge's precision
  against that rater is 0.783 (`judge-validity-kappa`): the human rejected about one in five of the
  judge's *answers* labels. That bears on every "lost" count in 5.7 and on the 75-of-75 saturation
  (`found10-any-answers-after-1b`), which rests on the judge's *answers* labels over 671 newly judged
  chunks.
- **Addendum C is private and post-dates the six-arm run.** The committed pass rule freezes six arms; the
  four section-cut and random-cut arms and their contrasts were pre-specified in an addendum that is not
  in this repository, after the six-arm main run. The head-to-head re-measures the +3.49 as a drift gate
  (`oa-sections-ft-setting-a`), which is not a test of it. Either Addendum C is committed or the section
  result is labelled as resting on a private pre-specification.
- **Quote coverage is not answer quality.** hit@5 and quote@10 credit the quote's characters in the
  retrieved set; no reader or model was asked whether the answer is usable. `jin-2025`'s inverted U makes
  retrieval quality an upper bound. An answer-level check does not exist in this record.
- **The gold never crosses a section and is short.** 0 of 2,035 quotes cross a section
  (`oa-no-quote-crosses-section`); median 119 characters, p90 214 (`oa-quote-median-chars`); every
  decision-set question comes from one passage grown inside one section region, with 85 of 2,592
  passages drawn over an unheaded body (`oa-passage-section-types`). The deciding evaluation therefore
  cannot register any cost of section cuts on answers that span sections, and measures "don't split the
  answer" for overlap and sentence cuts (hypothesis D). How common such answers are is not settled:
  `dasigi-2021-qasper` publishes 55.5 % multi-paragraph evidence among text-evidence answerable questions,
  at the paragraph unit and with redundant spread included, and no multi-section share; the project's
  exploratory re-measurement from the released data gives 31.4 % multi-paragraph (`qasper-multi-paragraph`;
  the published figure does not reproduce, `qasper-55-5-not-reproduced`) and 14.2 % / 10.1 % multi-section by
  full path / top level (`qasper-multi-section-full-path`, `qasper-multi-section-top-level`), with Fleiss κ
  0.22 at the top level (`qasper-kappa-top-level`): about one answer in ten crosses a top-level section.
  `baumgartner-2025-peerqa` gives 30 % non-consecutive at the location unit.
- **Single domain, embedder, reranker, question writer.** Bacteria/virus PMC OA only; one embedder
  (SFR-Embedding-Mistral); one cross-encoder (bge-reranker-v2-m3) over 50 candidates; one question-writing
  model and selection rule (lowest echo) for the OA eval, and term questions only for the alias set. The
  reranker's truncation of 1,024-token chunks is a plausible, unverified mechanism behind
  `oa-size-setting-a`.
- **The realised-size confound is inside our own section result.** In the production setting, on
  sentence chunks, real section boundaries are not shown to beat count-matched random cuts (+0.84 [−0.39,
  +2.11], unresolved, `oa-sections-vs-random-cuts-setting-a`), while under vector + reranker they are
  (+2.11 [+0.69, +3.54], `oa-sections-vs-random-cuts-vector-reranker`); no random-cut control was run for
  the fixed window, so the +3.49 headline has no such control at all. The judged adopted-spec chunks are
  shorter than the windows (`confirm-realised-chunk-tokens`); the semantic arms' realised size is
  unmeasured. Report the production section gain as "cut position and shorter chunks together, not
  separated". Matching of delivered context holds only for S against ft512o64s, by a chars/4 estimate and
  by outcome, not by design (`h2h-ctx5`).
- **The reach comparator is weaker than the eval's pick.** The reach test compares S with plain
  `fixed_token/512/0` windows, which the OA eval's own pick beats by +3.49 on the eval's questions
  (`oa-sections-ft-setting-a`); the +1.15 and the 13 % context saving are against those windows, not
  against ft512o64s, where context is matched (`h2h-ctx5`).
- **Adoption preceded the direct comparison.** The production chunker S was adopted on the OA eval, which
  never ran S (its plain-sentence section arm sent512s tied the eval's pick and lost on the cost ladder),
  and on the reach test against plain windows; the head-to-head against the eval's own pick, and the
  only evidence for the tolerance-and-merge knobs (`h2h-s-minus-sent512s`), came after. The head-to-head
  was pre-registered with the analyst's exposure declared and the other cost order's decision printed.
- **The head-to-head is a tie decided by cost.** +0.34 [−0.64, +1.33] with MDE 1.39
  (`h2h-s-minus-ft512o64s`, `h2h-mde-superiority`); under order A the decision is ft512o64s
  (`h2h-decision-and-other-order`). The paper reports a tie and a cost argument, never a win.
- **Biases that favour the baseline in the reach test** (quotes drawn from inside one window; chance
  matches at mid-sentence edges) run against the hypothesis; the W runs are from an older server version
  with the same configuration hash; J's orphans count against S.
- **Distractor scale.** Retrieval of the pair's own answering chunk is 73.5 % on the base corpus and
  54–62 % on the three larger ones (`found10-a-own-by-corpus`); the exploratory 5.7 figures depend on
  collection size.
- **Saturation.** Judged found@10 near 98 % (`reach-j-rates`) and "any chunk answers" at 75 of 75
  (`found10-any-answers-after-1b`) cannot separate chunkings on term questions; only per-pair measures can.
- **Low-discordance tests have asymptotic size.** The reach test's size at 4 % discordance was not
  simulated (`reach-superiority-p`); H3's margin could not have failed (`h2h-regex-minus-punkt`).
- **Unadjusted intervals and many subgroup cells.** Every post-hoc interval in 5.4 (hypotheses A–D,
  settings (e) and (f), the semantic and random-cut contrasts) is an unadjusted paired bootstrap; the
  per-section and per-density cells are hints. The paper quotes them as exploratory in the sentence.
- **Reproducibility of the deciding numbers.** The OA eval's per-arm, per-setting values and every
  contrast the paper cites are in a committed aggregate artifact with citable provenance and the sha256 of
  each private source file; the per-question files, the main-run RESULTS and Addendum C itself remain
  private, so a reader can verify the aggregates against their hashes but cannot recompute them. The
  article count and the sentence and semantic arms' split-quote shares exist only in Markdown.

### 7. Reproducibility

Per study: the pre-registration sha256 and signed tag (`prereg-reach-rechunk-2026-11`,
`reach-prereg-sha`; `prereg-adopted-spec-h2h-2026-11`, `h2h-prereg-sha`;
`prereg-alias-boundary-confirm-2026-11`, `confirm-prereg-sha`), the frozen commit and the `provenance`
block with `citable: true` (`reach-provenance-citable`, `h2h-harness-provenance`, `confirm-provenance`),
the seeds (bootstrap seed 0, 10,000 resamples; the draw and drift seeds in the artifacts), the model ids
(SFR-Embedding-Mistral 4096-d; bge-reranker-v2-m3; Qwen3.6-35B-A3B and Qwen3.8-27B judges with a
Gemini tiebreak and a Claude drift reference; the Qwen3.8 question writer), the tools-image builds
(v1.6.24 build 1 with Punkt, build 2 without) and the server version checked on every retrieval row.
For the older study, `../../plans/results/RUN-PACKAGE.md`. What another site must supply: the PMC OA
corpus and the private PMCID list, the question and quote files (hash-pinned), the OA eval's Addendum C,
a RAGStack deployment with the same retriever settings (the configuration hash is compared), gateway
access for the judge tiebreak, and the two-reader human read this record lacks.

---

## Working rules for this draft

- Every number cites a claim id from `claims.json` (114 entries; every one resolves under
  `../check_claims.py`; 9 are tagged `MARKDOWN-ONLY` and resolve to a Markdown file rather than a
  committed JSON: the OA article count and sentence-arm split share, the position oracle, the older
  study's corpus size and power, the 7-way and SciFact reports, and the step-0b wall read; the section,
  random-cut, overlap, size, semantic and C1/C2 claims the thesis leans on resolve to the OA aggregate
  artifact). Run `../check_claims.py` with `/rag/envs/ragstack/bin/python3` before any commit that touches
  the draft.
- Read the `note` field before writing the sentence. Four note tags change what may be said:
  `JUDGE-DEPENDENT` (the validity flag goes in the sentence), `MARKDOWN-ONLY` (the number has no committed
  JSON; the defect is fixed before the sentence is drafted; for §5.2 there is no Markdown-aggregate
  exception), `ADDENDUM-C` (the sentence says "pre-specified privately after the main run"), `SECONDARY` /
  `EXPLORATORY` (labelled in the sentence, not the caption).
- No status, no dates, no roadmap; those live in the lab book.
- Absent is not passed. The semantic arms' realised chunk size is *absent*; the semantic_pooled embedding
  cost on this corpus is *absent* (only the legacy arm's planned 4.5–7× exists); the sentence and semantic
  arms' split-quote shares are Markdown-only. Subgroup n for the section-type cells is in the aggregate
  (methods n = 368, `oa-size-methods-vector-only`).
- Powered nulls and unresolved contrasts are never merged: the BM25 section contrast, the production size
  contrast, the random-cut contrast in the production setting, the h2h superiority and the Leg A overlap are
  unresolved; none is a null.
- A tie decided by cost order is reported as a tie, with the other order's decision beside it.
- Name the tokenizer for every token count; h2h ctx@5 is chars/4 and says so.
- Quote n whenever it differs from the headline n (119 split quotes; 75 lost pairs; 53 recovered pairs;
  18 split pairs; 10 development topics).
