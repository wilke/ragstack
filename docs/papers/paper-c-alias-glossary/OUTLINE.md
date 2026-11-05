# Paper C — outline

**Working title.** A glossary makes alias passages findable, and the gain is small: a pre-registered
test of glossary attachment and citation-neighbour growth in scientific-paper retrieval.

**Target.** arXiv preprint, venue-neutral. The data is complete, the pre-registration is frozen and
verified by every run, and nothing here waits on another study. Candidate venues (owner's choice) are
noted in the open questions at the end.

**Audience.** Anyone who indexes full-length scientific articles for retrieval or RAG and has to decide
whether to spend index-time text on the paper's own definitions, and whether a collection built from a
paper's citation neighbourhood is a harder retrieval target than one built from unrelated papers. The
result is not specific to the served stack: the glossary lift is reported under all three retrievers
(default / norerank / BM25) on all four corpus arms, and the growth contrast under the three retrievers
and both glossary arms.

**The argument.** When a question names a concept by its full term and the answering passage uses only
the paper's short form (a symbol, or an abbreviation defined once elsewhere), attaching the paper's own
glossary to its chunks makes the passage findable: a confirmed, alias-specific gain under a frozen
pre-registration, but one of a single percentage point under the served hybrid-plus-cross-encoder path
(`h1-net-points`), on a corpus where 591 of 600 such records were already found by some chunk
(`h1-g-found-counts`; judge validity below threshold, §13.5). On the base corpus the lift is +2.50
without the reranker and +4.17 under BM25 (Layer 2, descriptive; `h1-other-retrievers`), a pattern that
does not hold uniformly on the expanded corpora (`h1-expanded-arm-cells`). Growing the collection with
the papers' citation neighbours against the same number of random same-field papers is *not confirmed*
as a cost to judged reach: no effect larger than 1.39 points, with power .96 and .90 for the planned
3.5- and 4.0-point drops (`h2-net-points`, `h2-equivalence`, `h2-power-planning`), while it costs
2.17 points of gold-chunk reach (`h2-gold-net-points`), a pattern consistent with neighbours
displacing the gold chunk and supplying an answer in its place. Every judged figure rests on an LLM
judge whose agreement with the single human reference fell below the pre-registered 0.90 threshold
(`judge-validity-reweighted`), and the paper reports the judge-free gold-only figure beside every
judged one rather than treating either as the truth.

**Target population.** Questions that use a glossary entry's primary term, over passages in open-access
scientific full texts that carry only the short form; a collection of 1,000 papers chunked at 512 tokens
(`corpus-chunks`) and grown by 2,423 added papers per expanded arm (`added-papers-per-arm`); a served
path of hybrid BM25 + dense retrieval with a cross-encoder reranker at top 10; records drafted and
verified by language models with a 40-record human check (`record-validity`). The findings are
conditional on that population and the paper says so where each one is stated.

**The one-sentence claim.** For questions that use a glossary entry's primary term over passages that
carry only the short form, attaching the paper's merged glossary (symbols and defined abbreviations) to
its chunks yields a confirmed, alias-specific gain in judged found@10 of 1.00 point (6 of 600 records,
`h1-net-points`; 1.33 points over the matched control change, `h1b-net-points`) under a hybrid plus
cross-encoder path, a gain that is too small to act on by the pre-registered 2-point threshold
(`l2-glossary-decision`) and that, on the base corpus, is +2.50 without the reranker and +4.17 under
BM25 (Layer 2, descriptive; `h1-other-retrievers`; not uniform on the expanded corpora,
`h1-expanded-arm-cells`); growing the collection with citation neighbours rather than random
same-field papers is not confirmed as a cost to judged found@10 (+0.06 points; no effect larger than
1.39 points by the equivalence bound; power .96 / .90 for the planned 3.5- and 4.0-point drops;
`h2-net-points`, `h2-equivalence`, `h2-power-planning`) while costing 2.17 points of gold-chunk reach
(`h2-gold-net-points`); every judged figure carries *judge validity below threshold (§13.5)*
(`judge-validity-reweighted`).

**The thesis in two numbers, from the same data.** Judged found@10 rose by 1.00 point under the glossary
(`h1-net-points`); found@10-gold rose by 5.50 (`h1-gold-net-points`). The difference is the object
measured: the glossary mostly moved the gold chunk into a top 10 that already held a judged answer. The
same pair inverts for growth: judged +0.06 (`h2-net-points`), gold −2.17 (`h2-gold-net-points`), which is
the displacement reading.

**What this paper does not claim.** That the glossary should be shipped (the Layer-2 reading is *not
resolved*). That the judge's labels are correct: the single human reference agreed on 0.872 re-weighted
and 0.82 of *answers* labels (`judge-validity-reweighted`, `judge-validity-strata`), and
found@10-gold is a sensitivity, not a substitute, because a record can be answered by a chunk that is
not its gold. That questions asked *with* the alias, or with a synonym, behave the same way: nothing
measured them (prereg R3). That one paper's neighbours alone harm its own records: the estimand adds
every study paper's neighbours at once (R11). That the chunk-boundary control shows anything
confirmatory: it is exploratory and judge-only.

**What this paper does NOT claim to have discovered** — established by the literature survey in
`../bib-inbox/14-alias-glossary.md` (and the earlier surveys it reuses). Every one of these is prior
art and must be cited as such, not rediscovered:

| we do not claim | who has it |
|---|---|
| that people name the same thing with different words and that systems must carry many names for one object | `furnas-1987` — the vocabulary problem; their optimal strategy is **unlimited aliasing**, which is the word we use |
| that abbreviations are defined once in a biomedical paper and then used bare, and that many collide across papers | `yu-2002` (25 % of abbreviations defined in-article; "many abbreviations are ambiguous"); `schwartz-hearst-2003` (the definition-extraction algorithm our glossary builder's task belongs to) |
| acronym identification and disambiguation as NLP tasks on scientific text | `veyseh-2020-sciad` (COLING 2020 dataset); their stated limitation is that such work is "typically evaluated … as standalone NLP tasks" |
| expanding the *document* at index time rather than the query | `nogueira-2019-doc2query` (learned query prediction appended to passages); `anthropic-2024` (a document-level context prefix per chunk, non-peer-reviewed); `gunther-2024`, `conti-2025` (contextual chunk embeddings) |
| query-side expansion as the classical remedy for vocabulary mismatch | `carpineto-romano-2012` (survey) |
| that acronym mismatch is a measurable retrieval failure in RAG and that query- and index-time expansion repair it, with large reported gains | `alier-2026` — the nearest prior work, and the one whose magnitudes we must engage (below) |
| that dense retrieval degrades faster than BM25 as the index grows, through false positives | `reimers-gurevych-2021`, proven and measured on MS MARCO (MRR@10 BM25 79.9 → 17.6 and dense 91.5 → 28.6 from 10 k to 8.8 M passages) |
| that dense retrievers fail on rare surface forms where BM25 does not | `sciavolino-2021` (EntityQuestions; top-20 average DPR 49.7 vs BM25 71.2 in the EMNLP 2021 version (72.0 in the arXiv revision), gaps above 60 points on some patterns) |
| that *related* distractors harm a RAG reader more than unrelated ones | `cuconasu-2024-power-of-noise` — cite precisely: random documents beat *topically related* distractors for the generator; it says nothing about retrieval reach |
| that LLM relevance judges are reliable without being valid, and that validation against humans is the obligation | `faggioli-2023`, `thomas-2024`, `clarke-dietz-2024`, `soboroff-2025`, `dietz-2025-tropes`, `norman-2026-reliability-without-validity`, `alaofi-2024-cafe` |
| the powered-null rule and equivalence testing | `webber-2008-statistical-power` (a failure to find significance does not license "no difference"; state how large a difference could have been missed); `lakens-2017-equivalence-tests` (TOST, smallest effect size of interest); `fuhr-2017-common-mistakes` with `sakai-2020-on-fuhrs-guideline` |

**The claim that survives.** The field has the pieces: the vocabulary problem and aliasing as its
remedy; abbreviation extraction; index-time document expansion; and, in one recent paper, a measured
"acronym gap" in RAG retrieval with query- and index-time expansion closing it. What nobody has done is
test the narrow case the glossary is built for — the question carries the full term, the passage only
the short form, and the glossary entry is the *only* lexical bridge — under a frozen pre-registration
with a matched within-paper control, a placebo glossary, a judge-free sensitivity beside every judged
figure, and a human validity check of the judge with a pre-specified consequence. Nor has anyone
contrasted citation-neighbour growth against size-matched random growth with two independent draws and a
gold-only direction gate. The contribution is the design and the honest size of the effect it found,
not a new mechanism.

Things the survey looked for and did not find, which is what the contribution rests on
(queries in `../bib-inbox/14-alias-glossary.md`, *Searched and did not find*):

1. **A controlled test of per-paper glossary attachment to chunks** (as opposed to query expansion, or a
   document-level context prefix) with a within-document control passage that already carries the term.
2. **A pre-registered retrieval experiment in this area.** OpenAlex returns preregistration literature
   from psychology, toxicology and cognitive modelling; nothing in IR or RAG evaluation.
3. **Citation-neighbour growth against random same-field growth** as a controlled contrast. The corpus
   growth literature (`reimers-gurevych-2021`) varies size with random passages; nothing varies
   *relatedness* at matched size.
4. **A judge-validity rule fixed before data with a named consequence** (here: the flag in every result
   sentence and the gold-only sensitivity beside every figure). The LLM-judge literature validates after
   the fact or not at all.

---

## Sections

### 1. Problem — claims: `records-balanced`, `h1-g-found-counts`, `corpus-chunks`

- The decision: whether to spend index-time text on a paper's own definitions (symbols and defined
  abbreviations), whether a collection built from the papers' citation neighbourhood is a harder target
  than one built from unrelated papers, and whether the served cross-encoder default survives such
  growth.
- Why the obvious measurement answers a different question: document-level hit rates do not see
  within-document failure; hit-on-gold alone cannot credit a correct answer from another chunk or
  another paper, while judged any-chunk reach cannot be read without a validity check of the judge; and
  on a corpus where 591 of 600 alias records are already found by some chunk, the question is the size
  of the gain at the ceiling, not whether a gain exists.
- The two record kinds: G (full term in the question, short form only in the passage) and C (same
  machinery, passage already carries the term), 2 G + 1 C per paper, 900 records on 300 papers.
- Target population stated explicitly: OA scientific full texts, 512-token windows, questions with the
  primary term, LLM-drafted and LLM-verified records with a 40-record human check.

### 2. Related work — from the table above; every entry verified before it enters `DRAFT.md`

- Vocabulary mismatch and aliasing: `furnas-1987`; `carpineto-romano-2012`.
- Abbreviations in scientific text: `yu-2002`, `schwartz-hearst-2003`, `veyseh-2020-sciad`; the
  reading-side version of a glossary, `head-2021-scholarphi` (mention).
- Index-time expansion and contextualised chunks: `nogueira-2019-doc2query`, `anthropic-2024`,
  `gunther-2024`, `conti-2025`, `chen-2024-densex`.
- The acronym gap in RAG retrieval: `alier-2026`. Concede the direction generously; then say what differs:
  their queries run acronym → expansion and ours expansion → short form; their corpus is a ~10,000-document
  course repository and ours 1,000 full-length research articles; their reported gains (45.5 % average,
  Recall@10 more than doubled on acronym queries) are against baselines we could not inspect (abstract
  only; the full text is behind a publisher wall) and without a cross-encoder stage in the abstract's
  description; on our base corpus the lift is +2.50 without the reranker and +4.17 under BM25 against
  +1.00 under the cross-encoder (Layer 2, descriptive; `h1-other-retrievers`), though not uniformly so
  on the expanded corpora (`h1-expanded-arm-cells`).
- Corpus growth and distractors: `reimers-gurevych-2021` (size), `sciavolino-2021` (rare forms),
  `cuconasu-2024-power-of-noise` (related distractors harm the reader). Say plainly that none varies
  relatedness at matched size, and that the generator-side harm in Cuconasu is a different endpoint from
  retrieval reach.
- LLM judges: `faggioli-2023`, `thomas-2024`, `clarke-dietz-2024`, `soboroff-2025`,
  `norman-2026-reliability-without-validity`, `haldar-2025-rating-roulette` (self-inconsistency, which
  the exploratory control's retest reproduces at the label level).
- Statistical practice: `webber-2008-statistical-power`, `lakens-2017-equivalence-tests`,
  `fuhr-2017-common-mistakes`, `sakai-2020-on-fuhrs-guideline`.

### 3. Design and what was frozen — claims: `records-balanced`, `records-recount`, `records-alias-kind`, `corpus-chunks`, `glossary-field-coverage`, `legs-rows`, `record-validity`, `judge-validity-reweighted`, `h1-c-gate`, `h1-power-floor`, `h1b-power-floor`, `h2-power-planning`, `random-draw-chunk-gap`, `provenance-citable`

- The pre-registration: frozen before any post-freeze retrieval, sha256 verified by every run; two
  families (G: H1 then H1b in fixed sequence; N: H2), one-sided at 0.05, no level passed between
  families; Layer 2 descriptive with judgement-based thresholds fixed in advance.
- Factors and cells: 4 corpus arms (base; base + citation neighbours; base + random draw 1; base +
  random draw 2) × 3 retrievers (served default; hybrid without rerank; BM25) × 2 glossary arms (none;
  merged), plus a placebo glossary leg and 4 noise repeats: 29 legs, 1,091 rows each.
- The record set as an instrument: built blind to retrieval, closed before the freeze, recounted by
  committed code; 300 satisfied papers with exactly 2 G + 1 C; 191 held-out records as a descriptive
  leg; alias-kind composition.
- The outcome: found@10 (gold in top 10, or any other top-10 chunk judged *answers* on its own, blind
  to arm, retriever and rank; pairs deduplicated across cells); found@10-gold beside it everywhere; a
  point is one percentage point of records (0.17 per G record, 0.33 per C record, 0.11 per balanced
  record).
- The judge: two primary open-weight judges, a gateway tiebreaker, a 5 % drift reference that never
  labels; the §13.5 rule: 100 owner-labelled pairs, 0.90 threshold, consequence fixed in the frozen text.
- Constraints as results: records exist only for papers with usable glossary entries (300 of 1,000);
  the C ceiling (baseline C found@10 100.0 %); neighbour caps (5 most-cited references + 5 most recent
  citers, open access only, research articles and reviews only) and the random draws matched per study
  paper on paper count, article type and chunk-length band, 4.51 / 4.75 % short in chunks
  (`random-draw-chunk-gap`); power computed from the realised layout before the freeze; H1b under-powered at both
  planning scenarios (.67 / .54) and confirmed anyway.
- Deviations, stated in the design: D-1 (R6 inputs and tercile cuts built after stage-A outputs were
  read; first re-run binding), D-2 (H1b gold figure computed afterwards, display only), D-3 (wording
  only).

### 4. Data — claims: `corpus-chunks`, `records-balanced`, `added-papers-per-arm`, `glossary-field-coverage`, `legs-rows`, `judge-pairs-stage-a`

- Provenance and counts: 1,000 papers, 32,765 chunks (SFR tokenizer; `corpus-chunks`), 2,423 added
  papers per expanded arm (`added-papers-per-arm`, a markdown-only claim); 24,559 labelled pairs; what is released (counts, rates, CIs, model ids, commits, sha256s) and
  what cannot be (record text, chunk exports, labels, manifests with ids).
- Point at the record's `RESULTS.md` provenance table and `prereg §11` rather than restating them.

### 5. Results — every number cites `claims.json`

- **5.1 The glossary (Family G).** `h1-net-points`, `h1-p`, `h1-g-found-counts`,
  `h1-gold-net-points`, `h1-realised-discordance`, `h1-c-gate`, `h1-fragile`, `h1b-net-points`,
  `h1b-p`, `h1b-gold-net-points` (D-2). Report the minimum-attainable-p fact: both confirmations rest on
  every discordant paper moving the hypothesised way.
- **5.2 Growth (Family N).** `h2-net-points`, `h2-p`, `h2-gold-net-points`, `h2-equivalence`,
  `h2-within-draw-and-bound`, `h2-power-planning`, `h2-power-realised`, `h2-fragile`,
  `h2-cells-found10`, `h2-hits-by-origin`, `growth-vs-base`, `draw-gap`. The powered null and the
  unresolved region below 1.39 points are reported as different things. The displacement reading is a
  reading from per-cell totals and is labelled as such.
- **5.3 Serving decisions and secondaries (Layer 2, descriptive).** `l2-glossary-decision` (D-3),
  `l2-reranker-decision`, `l2-growth-decision`, `h1-other-retrievers` (base corpus only),
  `h1-expanded-arm-cells`, `h1-by-alias-kind`,
  `placebo-g`, `rr-h1-h2`. Absent is absent: the collision split was never computed.

### 6. Threats to validity — claims: `record-validity`, `judge-validity-reweighted`, `judge-validity-strata`, `judge-validity-metrics`, `disagreement-tags`, `drift-agreement`, `drift-kappa`

- The judge is the main threat and the paper says so in its own voice: single human reference who also
  designed the study; both primary judges from one model family; the hardest (tiebroken) pairs agreed on
  10 of 17; the disagreements sit on the partial-versus-full line (exploratory, owner tags).
- Reliability is not validity: drift agreement with a second LLM of 0.95–0.96 is below its own line and
  is in any case not a validity measure.
- The C ceiling, the 6-record base of H1, the LLM-drafted records, one corpus, no study-wide error
  control across families, no tools image.
- **Exploratory, if the owner wants it in the paper at all (open question):** the chunk-boundary
  control — `control-flip-rate`, `control-retest-flip-rate`, `control-agreed-answers-stratum`,
  `control-tiebroken-stratum`, `control-recovered-by-other-chunk`, `control-tiebreak-replicate`. Its
  honest reading: about half of the label change under re-chunking is the judge's own non-repeatability
  on identical text, and the rest concentrates where the judges had agreed *answers*; the evidence mostly
  moved to a neighbouring chunk. It speaks to the judge's repeatability, not to found@10.

### 7. Reproducibility — claims: `provenance-citable`, `legs-rows`

- The frozen pre-registration and its sha; the run and statistics commits; the seeds (bootstrap, pool,
  waves, judge order, drift, placebo derangement); model ids and prompt ids; what another site must
  supply (the OA-1000 corpus build, OpenAlex access for neighbour selection, the judge endpoints, a
  gateway for the tiebreaker and drift reference, the private record file).

---

## Working rules for this draft

- Every number cites a claim id from `claims.json`. Run `../check_claims.py` before any commit that
  touches the draft.
- Read the `note` field of a claim before writing its sentence; the judge-validity flag and the D-1 /
  D-2 / D-3 pointers live there.
- No status, no dates, no roadmap; those live in the lab book.
- Absent is not passed (the collision split; the gold companion for the sensitivity displays; the Opus
  call count against the 7 % cap).
- Powered nulls and unresolved contrasts are different things and are never merged: H2 is a powered null
  for the planned drop and unresolved below 1.39 points.
- Every judged figure carries *judge validity below threshold* in its sentence.
- Name the tokenizer for any token count (SFR-Embedding-Mistral).
- Quote n whenever it differs from the headline n (600 G; 300 C; 300 papers for H1b; 900 for H2).
- Exploratory is labelled exploratory in the sentence itself (the control; the disagreement tags; the
  tercile displays).

## Open questions for the owner

1. Target venue and reader: IR evaluation (SIGIR / ECIR / CIKM style) or NLP-for-science (ACL venues)?
   The framing above is IR-evaluation first.
2. Does the exploratory chunk-boundary control belong in the paper (as a judge-repeatability threat), in
   an appendix, or in a separate note? The outline parks it under §6.
3. Whether to engage `alier-2026` from its abstract only, or to obtain the full text first so the
   baseline check can be done; the outline treats it as the nearest prior work and flags it unread.
4. Whether the round-3 powered null on the earlier record type (prereg §2 item 1) is cited as a
   cross-record reading in §5.1 or left to the record.
5. Promotion of the bib-inbox entries this outline cites into `../bibliography.md` is a prerequisite for
   `DRAFT.md` §2; the roster convention is that `bibliography.md` entries are read, not only verified.
