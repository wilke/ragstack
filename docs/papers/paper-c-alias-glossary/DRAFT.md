# A glossary makes alias passages findable, and the gain is small: a pre-registered test of glossary attachment and citation-neighbour growth in scientific-paper retrieval

> Drafting convention: every number cites its claim id inline, in the form
> value (`claim-id`), e.g. 1.00 point (`h1-net-points`). Add the claim to `claims.json` before writing
> the sentence. No status words, dates or roadmap. Citations in this file are restricted to entries in
> `../bibliography.md`; the related-work entries surveyed for this paper live in
> `../bib-inbox/14-alias-glossary.md` until they are promoted.

## Abstract

[Intent: four to six sentences in the two-number form — judged +1.00 against gold +5.50 for the
glossary, judged +0.06 against gold −2.17 for growth — with the judge-validity flag and the serving
reading *not resolved* stated in the abstract itself.]

## 1. Problem

A scientific paper defines its short forms once. A symbol is introduced where it first appears, an
abbreviation is expanded in parentheses on first use, and from then on the text carries the short form
alone. A reader who has the whole paper in hand is not troubled by this. A retrieval index that holds the
paper as 512-token windows is: the window that answers a question may contain only the short form, while
the question, asked by someone who does not yet know the paper's conventions, uses the full term. The
only lexical bridge between the two is the definition, and the definition is in another window.

The obvious remedy is to carry the paper's definitions with every window that uses them. The decision an
index builder faces is whether that text is worth its cost: it is index-time text on most chunks (the
merged glossary renders a field on 24,749 of 32,765 chunks of the corpus studied here, 75.5 %,
`glossary-field-coverage`), it competes for the reranker's attention, and it is only useful on the
records where the short form is the whole problem. Two further decisions sit beside it. A real
collection holds a paper together with its references and its citers, which use the same short forms,
often with different meanings; an index builder wants to know whether growing the collection with such
neighbours is a harder retrieval target than growing it with the same number of unrelated papers from
the same field. And a served path that reranks with a cross-encoder has to be shown to survive that
growth against the plain lexical baseline it replaced.

The obvious measurements answer different questions. A document-level hit rate cannot see a failure
that happens inside a document: the right paper is retrieved and the answering window is not. A hit on
the gold chunk alone is judge-free and exact, but it cannot credit a correct answer that another window
of the same paper, or a window of a neighbouring paper, supplies in the gold chunk's place; on a
collection grown with related papers that is precisely the case at issue. A judged any-chunk measure
credits those answers, but then every figure inherits the judge's validity, and a language-model judge
read against a single human reference is a measurement that has to be reported with its own error. This
paper reports both measures beside each other on every contrast, and it fixes in advance what a failed
judge-validity check does to the wording.

There is a third reason the obvious measurement misleads here, and it is a property of the corpus
rather than of the method. On the 600 records built so that the short form is the only bridge, the
served path already finds an answering window for 591 of 600 without any glossary
(`h1-g-found-counts`). The question is therefore not whether a glossary helps at all but how large the
help is at a ceiling, and whether a gain of a handful of records is one an operator should act on. A
design that can confirm a one-point gain and then say, in the same breath, that one point is below the
threshold that was fixed as worth acting on, is the design the decision needs.

The population to which every finding applies is stated here once and assumed thereafter. The corpus is
1,000 open-access scientific papers chunked into 32,765 windows of 512 tokens in the
SFR-Embedding-Mistral tokenizer (`corpus-chunks`). The records are questions that name a concept by the
primary term of a glossary entry; a **G** record's answering passage carries only the short form, a
**C** record's passage already carries the term, and every paper contributes exactly two G and one C,
900 records on 300 papers (`records-balanced`). Of the 600 G records, 508 rest on abbreviation aliases
and 88 on symbols (`records-alias-kind`). The records were drafted and verified by language models and
checked by one human reader on a sample of 40, of which 37 were valid (`record-validity`). Nothing here
measures questions asked with the alias itself, or with a synonym, or in other words. The served path
is hybrid lexical-plus-dense retrieval with a cross-encoder reranker at top 10, and the glossary lift is
also reported without the reranker and under the lexical baseline alone, so that a reader with a
different stack can locate their own case.

## 2. Related work

[Intent: concede the vocabulary problem, abbreviation extraction, index-time document expansion, the
measured acronym gap in RAG and the corpus-growth results as prior art, then locate the gap at the
controlled, pre-registered test of the narrow case. Entries are cited only once promoted to
`../bibliography.md`; the survey is `../bib-inbox/14-alias-glossary.md`.]

## 3. Design and what was frozen

**The pre-registration.** The hypotheses, the cells, the record set, the outcome, the judge, the tests,
the thresholds, the seeds and the consequence of every gate were fixed in a pre-registration frozen
before any post-freeze retrieval, and every run and every analysis verified its hash before proceeding
(`provenance-citable`). What follows is that design as it was frozen; the three post-freeze deviations are
stated where they bear, and each one is recorded in the study record as a dated entry rather than as an
edit.

**Two families, one-sided, no level passed between them.** Family G asks whether the merged glossary
helps the alias records. H1 tests merged against no glossary on the 600 G records under the served path,
one-sided for a gain, by an exact conditional sign-flip over papers. H1b follows in fixed sequence and
asks whether the gain on G exceeds the change on C within the same papers, a difference-in-differences
over 300 papers tested by exact label randomization within paper; it is the test that separates an
alias-specific gain from a general change in what is retrieved. Family N asks whether growing the
collection with citation neighbours costs more than growing it with random same-field papers. H2 tests
the neighbour arm against the average of two independent random draws on all 900 records, one-sided for
a drop, with a gold-only direction gate that must pass before the judged test is read. Each family runs
at a one-sided level of 0.05, and no level is passed between families. A member whose minimum
attainable p exceeds the level is reported as *GATE-NOT-EVALUABLE*, a third outcome alongside confirmed
and not confirmed, and it is preserved as such in every table.

**Cells.** Four corpus arms (the base corpus; base plus the citation neighbours of the 300 study papers;
base plus a first random draw of the same number of same-field papers; base plus a second, independent
draw), three retrievers (the served hybrid-plus-cross-encoder default; the same hybrid retrieval
without reranking; BM25 alone), and two glossary arms (none; the paper's merged glossary of symbols and
defined abbreviations, rendered on every chunk where an alias occurs as a whole token) define 24 cells
in which every record is run. One further leg, a placebo glossary that renders another paper's entries
on each paper's chunks with that paper's own entries removed, runs on the base corpus under the default
retriever as a descriptive control for extra text. Four noise repeats re-run the default retriever on
each corpus arm with a seeded request order. Twenty-nine legs in all, each reading 1,091 rows: the 900
balanced records and 191 held-out records that enter no test (`legs-rows`, `records-balanced`).

**The instrument.** The record set was built blind to retrieval and closed before the pre-registration
was written. Committed code recounts the pre-registration's record table exactly: 1,091 accepted records
on 408 papers, of which the 300 papers holding at least two accepted G records on distinct aliases and
one accepted C form the balanced set of 600 G and 300 C (`records-recount`). That layout is exactly
balanced by construction, so every per-paper statistic the tests need is defined for every paper, and
satisfaction is a property of the build, fixed before any retrieval, so the split cannot depend on an
outcome. The 191 records on the remaining 108 papers are run in every cell and reported descriptively.

**The outcome, and what a point is.** A record has a hit in a cell when its gold chunk is in the top
10, or when some other top-10 chunk, read on its own, is judged to state the core of the answer.
found@10 is the share of records with a hit; it measures reach, not answer quality, and it credits a hit
from another paper. found@10-gold is the judge-free version, the gold chunk in the top 10 and nothing
judged, and it is printed beside every confirmatory, serving-decision and per-cell figure. A *point* is
one percentage point of that share: one record is 0.17 points on the 600 G records, 0.33 points on the
300 C records and 0.11 points on the 900 balanced records (`records-balanced`).

**The judge.** Only non-gold top-10 chunks are judged, and only when the record's gold chunk is outside
that cell's top 10 or, for the rank-based secondary, when the chunk outranks the gold. The judge sees one pair at a
time, the question, its term, the passage, the passage paper's title and that paper's own definitions
for the short forms in the passage, and nothing of the arm, the retriever, the rank or the other chunks;
pairs are deduplicated across cells before labelling, so no label can depend on which cell produced it.
Two primary open-weight judges label every pair; when they agree, the label is final, and otherwise a
third model breaks the tie. A fourth model re-labels a seeded 5 % of pairs, allocated equally across
arms, as a drift reference that never sets a label. Stage A labelled 9,205 deduplicated pairs, 8,580 by
agreement and 625 by the tiebreak (`judge-pairs-stage-a`); over both stages 24,559 pairs were labelled
and none was left unjudged (`legs-rows`).

**The judge-validity rule, and what it decided.** Before any analysis, the single human reference
labelled 100 non-gold pairs, drawn stratified by the final label and blind to label, stratum and arm,
and committed the sheet's hash. The frozen rule set a threshold of 0.90 agreement, re-weighted to the
label shares, with a named consequence: below it, every result sentence carries *judge validity below
threshold*, and found@10-gold is reported beside every judged figure as the sensitivity. The agreement
was 0.872 re-weighted (`judge-validity-reweighted`), and 0.82 on the *answers* stratum
(`judge-validity-strata`), so the rule fired on both of its clauses. Nothing was re-labelled and no
figure changed; the consequence is the wording of this paper, which carries the flag on every judged
result. A second human check, of 40 records drawn by seeded hash and labelled valid or invalid blind to
kind and arm, found 37 valid (`record-validity`), above its own 0.85 rule.

**Power, computed from the realised layout before the freeze.** With 300 papers fixed, power was
simulated at two planning scenarios for the share of discordant records and the share moving the
hypothesised way. H1's power floor was .987 and .930 (`h1-power-floor`). H1b's was .67 and .54
(`h1b-power-floor`), below the conventional .80 at both scenarios: the design went ahead with one control
record per paper and an under-powered H1b, and no null is read from an under-powered member. H2's simulated power to confirm was .96 and .90 (`h2-power-planning`), so a drop of the
planned size would have been seen. The reading of H2 at smaller effects is by an equivalence bound set
at the realised minimum detectable effect, and the paper reports that bound beside the null rather than
reading the null as "no effect".

**Constraints that bounded the design, which are results too.** Records exist only for papers with
usable glossary entries, so 300 of the 1,000 corpus papers carry the balanced set and the corpus's
remaining papers act as distractors. The control records sat at a ceiling: baseline C found@10 under the
served path was 300 of 300, which the design anticipated and which makes the C gate on H1 unable to fire
on a C rise and H1b's control able only to fall or stay. Neighbour selection was capped at the five
most-cited open-access references and the five most recent open-access citers of each study paper,
research articles and reviews only, and the random draws were matched on paper count rather than chunk
count, leaving them 4.5 to 4.8 % short of the neighbour arm's chunks; a chunk-volume covariate re-test
is reported beside H2 for that reason. The merged glossary is rendered under the served caps of twelve
lines and 1,200 characters per chunk. Every token count is in the SFR-Embedding-Mistral tokenizer.

**Deviations.** Three departures from the frozen text are recorded, and each result that depends on one
says so. First, the inputs behind the fixed sensitivities (topic clustering, the chunk-volume covariate,
the citation-count and text-similarity terciles) and three declared subset lists were built, by a
committed script that reads no outcome, only after the stage-A analysis had been run and read; the first
re-run with them is the binding stage-A analysis, and every tercile display says that its cut points were
fixed after the stage-A outputs had been read. Second, the committed statistics printed no gold-only
figure beside H1b; it was computed afterwards by a separate script that recomputes the judged H1b
exactly before writing anything, and it is a display that decides nothing (`h1b-gold-net-points`).
Third, the frozen wording table has no row for a serving reading of *not resolved* beside a confirmed
H1; the nearest frozen rule is used and no threshold or reading was changed (`l2-glossary-decision`).

## 4. Data

[Intent: provenance, counts and sizes of the corpus, the expanded arms, the record set and the labelled
pairs; what is released (counts, rates, intervals, model and prompt ids, commits, hashes) and what cannot
be (record text, chunk exports, labels, manifests with ids). Point at the record's provenance table rather
than restating it.]

## 5. Results

[Intent: one subsection per family and one for the descriptive serving decisions; every number as value
(`claim-id`); the powered null and the unresolved region below 1.39 points reported as different things;
every judged sentence carrying *judge validity below threshold* with found@10-gold beside it.]

## 6. Threats to validity

[Intent: the judge is the main threat, in the paper's own voice: single human reference, one model
family for both primary judges, 10 of 17 on the hardest pairs, disagreements on the partial-versus-full
line (exploratory tags); reliability against a second model is not validity; the C ceiling; six records
behind H1; LLM-drafted records; one corpus; no study-wide error control; the exploratory chunk-boundary
control as a judge-repeatability reading, if it stays.]

## 7. Reproducibility

[Intent: the frozen pre-registration and its hash, the run and statistics commits, the seeds, the model
and prompt ids, the committed public artifacts, and an honest statement of what another site must
supply: the corpus build, bibliographic metadata access for neighbour selection, judge endpoints, a
gateway for the tiebreaker and drift reference, and the private record file.]
