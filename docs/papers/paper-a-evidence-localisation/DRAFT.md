# Reliable but not canonical: LLM judges for passage-level evidence localisation

---

## Abstract

Passage-level retrieval evaluation needs gold that says where in a document the evidence for a query
lies, not only whether the document is relevant. Language models are increasingly used to produce
that gold, usually with one reading per document and no check that a second reading would agree. We
measure what that single reading hides. Two open-weight judges read 308 query-document pairs from TREC
Clinical Decision Support topics over full-length PubMed Central articles, at temperature zero, up to
twenty and ten times each, with the document's units shown in a different seeded order each time
(`r31-scout-span-self-consistency`). Across five readings the judges agree with themselves about
whether a document contains evidence on 0.9188 and 0.9805 of pairs
(`r31-scout-whether-agreement-five-presentations`, `r31-qwen-whether-agreement-five-presentations`).
Comparing two single readings, they agree about where it is on only 0.3831 and 0.5617
(`r31-scout-span-self-consistency`, `r31-qwen-span-self-consistency`). More readings do not produce
a canonical location set within the depth we measured. By twenty readings neither the union of
distinct evidence sets nor the union of distinct sentences has reached its fitted asymptote (0.6949
and 0.8118 for one judge; `r31ext-scout-set-union-fraction-of-asymptote`,
`r31ext-scout-sentence-fit-excluding-blowups`). A graded per-sentence support score is the object
that gains from depth: one judge's twenty readings reach a k-rater reliability of 0.9142
(`judge-scout-support-reliability-curve`), and thirty readings pooled across two model families reach
0.9205 (`graded-support-reliability-at-30`). At shallow depth the graded score is no more stable than
a union of readings, so the result is about what depth buys. The pooled curve's shape mostly reflects
the mix of judges, and within one judge the readings behave as Spearman-Brown predicts. Three
proprietary judges from a third family, read once each, pick sentences that the pooled local readings
support, with a modest correlation. Asking for whole-sentence anchors instead of ten-word quotes cuts
the weaker local judge's hallucinated-span rate on its first reading from 0.08204 to 0.02517
(`r3-scout-hallucinated-span-rate`, `r31-scout-hallucinated-span-rate`). Applied at scale to 3,738
pairs (`confirmation-scale-pairs-total`), the method surfaced a locator defect that the development
set could not have shown. Reliability is not validity. No human has read these labels, and we make no
claim that the graded score is correct.

---

## 1. Problem

Retrieval over long documents is increasingly evaluated at passage level. A system that retrieves the
right article but returns a chunk that lacks the evidence has failed the user, and a document-level
relevance judgment cannot see that failure. Passage-level evaluation needs gold that names where the
evidence lies inside each relevant document. On long documents this differs sharply from document
relevance, because most of a relevant document is not evidence.

Hand-labelling evidence locations across thousands of document-topic pairs is not feasible, so the
field has moved to language models as span annotators. The typical protocol reads each pair once, at
temperature zero, and treats the output as the gold. A single reading of a long document is one sample
from whatever distribution of locations the model would produce, and nothing in the protocol measures
that distribution.

That single reading is the unexamined step, and this paper measures what it hides. We ask three
questions of a language-model evidence locator:

1. If the same model reads the same document twice, does it put the evidence in the same place?
2. If it reads the document many times, do its readings converge on a canonical set of locations?
3. If they do not, is there an object built from the readings that does reproduce itself?

The answers are no; not within thirty readings; and yes, given enough readings. Neither the union of
distinct evidence sets nor the union of distinct sentences reaches its asymptote at the depth we
measured. A graded per-sentence support score clears a reliability bar
of 0.9 at twenty readings of one judge (`judge-scout-support-reliability-curve`) and at thirty readings
pooled across two (`graded-support-reliability-at-30`). At five or six readings it is no more stable than a
union. Its value is that its reliability grows with depth to a usable level, which we can measure. A
deep union split-half was not measured, so we do not claim the union would stay unstable at that depth.

**Target population.** Our findings are conditional on the population we measured. The documents are
full-length biomedical research articles from the PubMed Central Open Access subset. The queries are
TREC Clinical Decision Support topics, which describe a patient case and ask for articles that bear on
diagnosis, test or treatment. In this population evidence need not be confined to the abstract, a
topic's support is spread over several documents, and articles are long. We have not tested a
population with short documents or point answers, and we do not claim the result transfers there.

**What we claim.** Section 2 sets out what is prior art. Our contribution is narrower than "span
annotation is unreliable", which is old:

- To our knowledge, we report the first repeated-measures (within-annotator) reliability coefficient
  for evidence-span selection. We report it as a curve over the number of readings, per judge and
  pooled, rather than as an endpoint.
- We model the growth of the evidence union as a function of the number of readings, with a fit that
  was pre-registered and then extrapolated four-fold.
- We measure test-retest within one model, at temperature zero, inside a document. The span prior art
  measures agreement between annotators, and the self-reliability prior art measures it at
  whole-response granularity.
- We vary the requested anchor unit and measure its effect on quote fidelity, between the prompting
  and constrained-decoding approaches in the literature.

We do not claim that the graded score is correct. No human has read these labels. Throughout,
reliability means that the instrument reproduces itself, not that it measures what it is meant to.

---

## 2. Related work

### 2.1 Disagreement about evidence location is known

The core observation of this paper, that annotators agree that evidence exists and disagree about which
span it is, is not new. Several benchmarks measured it, concluded the field must live with it, and
built scoring rules around that concession.

FEVER separates the verdict from the evidence and uses two statistics because a chance-corrected
coefficient has no denominator for an evidence set (`thorne-2018-fever`). Against an exhaustive
super-annotator search, regular annotators reach evidence precision of 95.42 % but recall of 72.36 %
(`thorne-2018-fever`). The scoring rule asks for any one complete evidence set, and the authors concede
that "it is not feasible to ensure that the evidence selection annotations are complete"
(`thorne-2018-fever`). WiCE collects five annotations per example over long evidence documents and
finds the same set of supporting sentences chosen on 56.1 % of supported and 34.4 % of partially
supported cases (`kamoi-2023-wice`). It keeps multiple valid sets and declines to take their union, "as
there can be multiple different sets of sentences with identical information" (`kamoi-2023-wice`).
Natural Questions ran 25-way annotation on a sample of its items and reports that "there are multiple
valid long answers in multiple distinct locations on the page" (`kwiatkowski-2019-nq`). Its fix was an
instruction to pick the earliest instance, which only partly resolved the ambiguity
(`kwiatkowski-2019-nq`).

The pattern holds outside fact verification. HotpotQA scores a second crowd worker against the first
and finds supporting-fact exact match of 61.50 against answer exact match of 83.60, with
supporting-fact F1 at 90.04 (`yang-2018-hotpotqa`). Medical experts annotating PICO spans in biomedical
abstracts reach exact-boundary F1 of 0.357 to 0.576 and relaxed token-overlap F1 of 0.713 to 0.758
(`lee-sun-2019-pico`). Rationale annotation shows the same split. Annotators who agree on a label pick
partly different rationale tokens (`zaidan-2007-rationales`, `jakobsen-2023`), legal experts agree
weakly on case-outcome rationales (`xu-2023-rave`), and ERASER went back to collect comprehensive
rationales because the originals were known to be incomplete (`deyoung-2020-eraser`). The TREC HARD
track organisers called passage-level judging "a terrifically difficult task" and reported no
passage-level agreement figure (`allan-2003-hard`).

The field already has a name for agreement on the verdict alongside divergence on the reasoning:
within-label variation (`jiang-2023`, `hong-2025`). We adopt it and do not coin a new term. The field
also has a coefficient for separating where from what. Mathet's γ_cat is chance-corrected and built to
be insensitive to positional disagreement, so γ − γ_cat isolates the positional component of agreement
(`mathet-2017`). Section 3.4 explains why we do not use it as our primary measure.

### 2.2 What was not measured

Each of these studies observed span-set disagreement and worked around it. The literature surveys
behind this paper looked for four things that would have measured it, and found none.

**No repeated-measures (within-annotator) reliability coefficient for span selection.** A search for
split-half, Spearman-Brown, test-retest or ICC figures reported for evidence-span selection, by any
annotator or model, returned none. Haldar and Hockenmaier define self-reliability as "the agreement of
a judge with itself over multiple runs with the same settings", but measure it over whole responses
with three runs per item and no span or sub-response analysis (`haldar-2025-rating-roulette`). Norman
and colleagues report test-retest reliability above 0.95 over about 541,000 whole judgments
(`norman-2026-reliability-without-validity`). Neither goes inside the document. From the
annotation-methodology side, Abercrombie and colleagues find in a systematic review that
intra-annotator agreement is "rarely, if ever, reported" in NLP (`abercrombie-2025-consistency`).

**No model of union growth against effort.** FEVER's super-annotator comparison and ERASER's
comprehensive re-annotation both acknowledge that more effort finds more evidence
(`thorne-2018-fever`, `deyoung-2020-eraser`). Both treat it as a one-off correction to the data, not as
a curve to measure. Information retrieval has long studied how pooled relevance judgments grow and bias
evaluation as pools change (`zobel-1998`). That curve counts relevant documents found by more systems.
The curve we fit counts evidence locations found inside one document by repeated readings of one
judge, and the two should not be confused.

**No within-model test-retest inside a document.** The span literature is between annotators. The LLM
span-annotation work that exists reads each item once. Kasner and colleagues compare LLM spans with
human spans over three tasks, with one run per input (`kasner-2026-spans`). They report that LLM
annotations marked correct have only 24 % hard character-level overlap with human annotations
(`kasner-2026-spans`). Saha and colleagues ask an LLM to locate responsive passages in INEX documents
and score them against human highlights, again with one reading per item and, by their own account,
limited inter-annotator agreement to calibrate against (`saha-2026`). Their diagnosis is accuracy,
with models over-highlighting. Ours is stability. The two are complementary.

**No manipulation of the anchor unit.** Quote hallucination is documented and quantified: hybrid
extractive methods "frequently hallucinate quoted spans", with exact extraction below 40 % in technical
domains, and constrained decoding fixes it (`signe-2026-chyd`). Grounding prompts raise verbatim
fidelity (`weller-2024-according-to`). LongCite emits sentence indices into a pre-segmented context,
which sidesteps quoting as an engineering choice without comparing it to quoting
(`zhang-2025-longcite`). We found no study that varies the requested anchor granularity and measures
fidelity.

### 2.3 LLM judges for relevance

Our "whether" result confirms the document-level literature on LLMs as relevance assessors; the
"where" result is the new part. Faggioli and colleagues framed the question and its dissent
(`faggioli-2023`). Thomas and colleagues showed that a prompted model can match the agreement trained
assessors reach with each other, and also that rewording one prompt moves agreement substantially
(`thomas-2024`). UMBRELA reproduced that judge in the open and became the TREC RAG assessor
(`upadhyay-2024-umbrela`). In every one of these the judge is handed a candidate and asked only whether
it is relevant, and each item is read once.

Critics argue that a model-built answer key caps what an evaluation can measure (`soboroff-2025`).
Clarke and Dietz show that an LLM-built answer key can be gamed and that circularity distorts system
rankings (`clarke-dietz-2024`). Both apply to us: a reliable judge is a reliable ceiling, not a correct
one.

The nearest neighbour at sub-document granularity is the TREC RAG support study. It asks whether a
cited passage supports an answer sentence, finds GPT-4o agreeing with human assessors better than two
humans agree with each other, and defers the question of consistency to future work
(`thakur-2025-support`). The judge there is not asked to locate evidence. The human comparator
collapses, with two assessors at κ of −0.03 and 0.07 across the two conditions (`thakur-2025-support`).
It is the strongest published case that fine-grained support judgment is unstable for people too.

### 2.4 Repeated readings, pooling, and graded objects

Pooling repeated LLM runs to recover reliability is published. Reiss repeats a document-level binary
classification and reports Krippendorff's α rising from 0.75 without pooling to 0.91 when ten repeats
at temperature 1.0 are pooled by majority (`reiss-2023`). Barrie and colleagues define an intra-prompt
stability score over 30 repetitions of an identical prompt (`barrie-2024-pss`). Both pool to a hard
label over whole documents. Neither works on location.

The statistic we report has an established name. When a gold label is an aggregate of k ratings, Wong
and Paritosh argue that the right unit of reliability is the aggregate's. They call it k-rater
reliability, compute it as ICC(k), and note its equivalence to the Spearman-Brown prophecy formula
(`wong-2022`). Their worked example rises from ICC(1) = 0.590 to ICC(13) = 0.950 (`wong-2022`). That
shape, a modest single rating and a reliable aggregate, is the shape of our result. We use their name.

A graded, pooled per-unit reference is not new as a representation. STAPLE estimates a probabilistic
per-voxel reference from disagreeing image segmentations (`warfield-2004-staple`). HateXplain
aggregates several annotators' rationale tokens into a per-token score (`mathew-2021-hatexplain`).
Muscato and colleagues compare hard, union and soft rationale representations under annotator
disagreement and find the soft ones preferable (`muscato-2026-disagreeing-rationales`). Their question
is which representation to use for training and evaluating models. Ours is whether the annotation
process reproduces itself. Our claim for the graded score is therefore limited to the reliability
measurement, not the representation.

Reliability coexisting with invalidity in LLM judges is documented too: test-retest above 0.95 sits
alongside position bias above 0.10 in two deployed judges (`norman-2026-reliability-without-validity`).

### 2.5 Why passage-level gold

This work arose inside a study of how a retrieval index should chunk full-length scientific articles,
where document-level metrics answer a different question. We do not argue that case here; it is enough
that passage-level evaluation needs passage-level gold.

---

## 3. Design and what was frozen

### 3.1 The task and the instrument

A judge receives a TREC Clinical Decision Support topic and one full-length article, segmented into
units (the title, the abstract and each body section) and sentences. It is asked whether the article
contains localisable evidence for the topic and, if so, to return one or more evidence sets. Each set is
a list of spans, and each span is identified by anchors that the harness resolves to sentence locations
in the document. The rubric defines what counts as evidence. A clause allows "no localisable evidence".
Documents longer than the serving context are windowed along the presentation's unit order. One prompt
serves every judge. The rubric was hashed before the first labeling call and was not changed across the
protocol revisions below.

All local readings ran at temperature zero with a fixed sampler seed. The variation between a judge's
readings of the same pair is introduced by design in the presentation. Each reading shows the
document's units in a different seeded order, and reading k = 0 shows the natural order. The sampler is
never varied. Section 6 discusses what this does and does not attribute.

### 3.2 Three anchor protocols, each changed for a measured reason

The protocol changed twice, each time because a gate measured a specific defect.

*Index-primary localisation with ten-word quotes.* The first protocol asked the judge for each span's
unit and sentence indices, with the first and last ten words quoted for verification. The judge's
indices were unreliable: only 0.64496 of 476 attempted spans were at the indices the judge claimed, and
140 had to be relocated to where their own quote resolved (`s0-span-index-agreement`).

*Quote-primary localisation with ten-word anchors.* The second protocol made the quotes primary and
located spans by them. The hallucinated-span rate, the share of spans whose quoted words appear nowhere
in the document, rose for one judge. Of that judge's 58 hallucinated spans, 54 failed on the closing
anchor (`r3-scout-closing-anchor-failures`). The model could copy the start of a span and invented its
end.

*Whole-sentence anchors.* The third protocol asks the judge to quote the whole first sentence and the
whole last sentence of each span. A quote that cannot be located exactly, after normalisation, or by an
eight-word fallback that must land inside one segmented sentence, is a hallucinated span and is
dropped. Spans snap outward to whole sentences within one unit. All results in Sections 5.3–5.6 use
this protocol.

### 3.3 Gates, predictions and the pre-registration

Before any labels under a protocol were generated, the pre-registration fixed the rubric, the prompt,
the presentation seeding, and the gates and their thresholds. The whether-agreement gate was added with
the second protocol; the first had only the other two.

- **Whether-agreement.** A judge's readings of a pair agree about whether it contains evidence, with a
  bar of ≥ 0.90 (`r3-scout-whether-agreement`).
- **Span self-consistency.** Two single readings' span unions reach Jaccard ≥ 0.5, with two empty
  verdicts counted as consistent and a bar of ≥ 0.90 (`s0-span-self-consistency-quote-anchored`). This
  gate compares one reading with one reading. Section 5.4 reads the graded instrument, which pools
  many readings, against the same value (`graded-support-bar`). The two are not the same depth, and we
  do not compare them as if they were.
- **Hallucinated-span rate**, with a bar of ≤ 0.05 (`prereg-p-ext-3-as-written`).

A further pre-registration was committed before the extension that took the local judges to twenty and
ten readings. It fixed three predictions and the estimators that score them:

- P-ext-1 predicted that pooled graded-support reliability reaches ≥ 0.85 at thirty readings
  (`prereg-p-ext-1-as-registered`).
- P-ext-2 predicted that the sentence union at twenty readings lies within 10 % of the asymptote of a
  Michaelis-Menten fit to the first five readings, with a secondary on the accuracy of the fit's
  projection (`prereg-p-ext-2-fit-parameters`).
- P-ext-3 predicted that the hallucinated-span rate stays ≤ 0.05 on the new readings
  (`prereg-p-ext-3-as-written`).

The prediction files are the copies the scorer reads, and they were not edited after commit. Every
analysis in Section 5 that is not one of these gates or predictions is exploratory, and the sentence
that reports it says so.

### 3.4 Statistics, and why not κ

For whether-agreement we report the share of pairs on which all k readings agree. This statistic falls
with k by construction, because more readings give more chances to disagree. The informative quantity
is how fast it falls.

For location we report mean pairwise agreement, not κ. When the unit is a span inside a long document,
true negatives cannot be counted, so chance agreement is undefined. In that setting mean pairwise F1 is
identical to positive specific agreement and is a legitimate reliability statistic (`hripcsak-2005`).
Token-level κ is the discretising workaround that Mathet shows inflates agreement (`mathet-2017`). We
report two location statistics: the share of reading pairs whose span unions reach Jaccard ≥ 0.5
(`s0-span-self-consistency-quote-anchored`), and the raw mean pairwise Jaccard as a partial-credit
measure (`r31-scout-partial-credit-jaccard`). These statistics have no standard interpretive scale, so
our location numbers are meaningful against numbers computed the same way, not against κ conventions.
γ_cat would give the presence/location separation in one coefficient (`mathet-2017`). We get it instead
by measuring presence and location as two quantities on the same readings.

For the graded score, the support of a sentence over n readings is the fraction of readings whose
evidence includes it. The pre-registered estimator works per pair. It takes the first n readings in a
pooled, interleaved order, s0, q0, s1, q1 and so on, then the remaining Scout readings
(`graded-support-pooled-order`). Scout therefore supplies half of the first twenty readings and two
thirds of thirty. The universe is every sentence selected by at least one of those readings. The
readings are split at random into two halves, each sentence is scored by its support within each half,
and Pearson r is computed across the pair's sentences. The estimator averages over pairs and over
twenty seeded splits, then applies Spearman-Brown doubling to give the reliability of the full
n-reading instrument (`graded-support-reliability-at-10`). Following Wong and Paritosh, this is k-rater
reliability (`wong-2022`). The same estimator is computed on each judge's readings alone.

### 3.5 Constraints, which are results too

The development population is the ten development topics of the confirmation study, and the
confirmation population is its eighty confirmatory topics (`confirmation-scale-pairs`). Both are fixed
by the TREC Clinical Decision Support topic supply. The confirmation-topic outcomes are quarantined.
This paper reports the scale application as counts and as one defect it surfaced, and reports no
confirmation-set endpoint (`confirmation-outputs-quarantined`).

The local judges share a host with other users under a contract that caps requests in flight per
endpoint, so reading depth is expensive in wall-clock time. That is why the extension stops at twenty
and ten readings, and the pooled instrument at thirty.

The third-family judges were reached through a command-line client on a personal account with a
session budget. One judge's run stopped at 251 of 308 pairs when that budget ran out
(`fable51-incomplete-n`). Every figure for that judge is on the reduced sample and says so.

The human validation was designed and its sample drawn, but the read has not been performed
(`human-read-not-performed`). Section 6 treats this as the principal threat.

---

## 4. Data

**Development population.** 308 query-document pairs over ten TREC Clinical Decision Support
development topics: 208 pooled from retrieval and 100 drawn as a bias bound outside the pools
(`dev-set-composition`). The documents are full-length articles from the PubMed Central Open Access
subset. Every development-set number in this paper is over these pairs unless it states a different n.

**Confirmation-scale application.** 3,738 pairs over the eighty confirmation topics: 2,962 pooled from
retrieval and 776 drawn as a bias bound (`confirmation-scale-composition`). Topics contribute 46.73
pairs on average, with a median of 39 and a range of 8 to 153 (`confirmation-scale-pairs`).

**Judges.** Two open-weight models served locally: Llama-4-Scout (17B-16E-Instruct, FP8) and
Qwen3.6-35B-A3B, a reasoning model whose thinking is stripped before parsing. Three proprietary models
from a third family: Claude Sonnet 5, Claude Opus 5 and Claude Fable 5.1.

**Readings.** Table 1 gives the reading depth per judge and population.

> **Table 1: Readings per pair and in total.** Development: Scout 20 and Qwen 10 per pair
> (`r31ext-scout-whether-agreement-curve`, `r31ext-qwen-whether-agreement-curve`,
> `prereg-p-ext-3-hallucination-holds`), giving 6,160 Scout and 3,080 Qwen readings
> (`span-filter-dev-counts`). The pooled graded instrument uses all thirty readings per pair
> (`graded-support-reliability-at-30`). Third family: one reading per pair, on 308 pairs for Sonnet and
> Opus and 251 for Fable (`fable51-incomplete-n`). Confirmation scale: Scout 20 and Qwen 10 per pair
> (`confirmation-readings-per-judge`), giving 74,760 and 37,380 records (`confirmation-record-counts`)
> and 112,140 readings in total (`confirmation-total-readings`). Instrument identity between
> development and scale: prompt and rubric hashes asserted equal before the first scale call
> (`instrument-hashes-conf`).

**Cost of the third-family pass.** One reading per pair across the three proprietary judges cost
$195.32 over 867 pair readings (`claude-total-cost`). Sonnet's 308 readings cost $40.83 in total
(`claude-sonnet5-cost`) and Opus's $93.12 (`claude-opus5-cost`). Per pair, that was $0.1326 for Sonnet,
$0.3023 for Opus and $0.2445 for Fable (`claude-cost-per-pair`). The Fable figure is over 251 pairs, and
its three accountings in the artifact disagree because the run was interrupted (`claude-fable51-cost`).
These are the costs of a single reading. The depth the graded instrument needs was not priced or run
for this family.

The run package (`docs/plans/results/RUN-PACKAGE.md`) gives provenance, file inventory, sizes and what
is and is not committed. We do not restate it here.

---

## 5. Results

### 5.1 Whether is easy; where is not

The same judge, shown the same document twice, agrees with itself about whether it contains evidence
and disagrees about where. Table 2 traces this across the three protocols.

> **Table 2: Whether-agreement against span self-consistency, same judge, same pairs.** Rows: ten-word
> quote anchors, quote-primary ten-word anchors, whole-sentence anchors. Columns: whether-agreement,
> span self-consistency, n. Claim ids: `s0-whether-self-agreement`,
> `s0-span-self-consistency-quote-anchored`, `r3-scout-whether-agreement`, `r3-qwen-whether-agreement`,
> `r3-scout-span-self-consistency`, `r3-qwen-span-self-consistency`,
> `r31-scout-whether-agreement-five-presentations`, `r31-qwen-whether-agreement-five-presentations`,
> `r31-scout-span-self-consistency`, `r31-qwen-span-self-consistency`,
> `r31-span-self-consistency-on-the-31`.

Under the first protocol, the judge agreed with itself about whether on 27 of 31 re-presented pairs,
0.871 (`s0-whether-self-agreement`), and reproduced its span set on 0.3226 of the same pairs
(`s0-span-self-consistency-quote-anchored`). Under the quote-primary protocol, both local judges reached
whether-agreement of 0.9677 on 31 re-presented pairs (`r3-scout-whether-agreement`,
`r3-qwen-whether-agreement`). Their span self-consistency on the same 31 pairs was 0.6452 for Scout and
0.4194 for Qwen (`r3-scout-span-self-consistency`, `r3-qwen-span-self-consistency`).

Under the final protocol every pair was read five times. Scout's five readings all agree about whether
on 0.9188 of pairs, 283 of 308 (`r31-scout-whether-agreement-five-presentations`). Qwen's agree on
0.9805, 302 of 308 (`r31-qwen-whether-agreement-five-presentations`). Both pass the ≥ 0.90 bar
(`r31-scout-whether-agreement-five-presentations`). Comparing the first two single readings of every
pair, span self-consistency is 0.3831 for Scout and 0.5617 for Qwen (`r31-scout-span-self-consistency`,
`r31-qwen-span-self-consistency`). Both fail the bar by a wide margin.

The fall from 0.6452 to 0.3831 for Scout is not a like-for-like comparison, because the earlier figure
is on 31 pairs and the later one on 308 (`r3-scout-span-self-consistency`,
`r31-scout-span-self-consistency`). On the same 31 pairs, the final protocol gives 0.2903 for Scout, 9 of
31, and 0.5484 for Qwen, 17 of 31 (`r31-span-self-consistency-on-the-31`). Whole-sentence anchors made
Scout's spans smaller [NEEDS CLAIM: median span length in characters under ten-word and whole-sentence
anchors], and a Jaccard criterion penalises a small target more heavily for the same absolute
disagreement. Averaged over all ten pairs of the five readings, Scout's rate is 0.4201, with a range of
0.3766 to 0.4675, so the comparison of the first two readings is not an unlucky draw
(`r31-scout-span-self-consistency-all-ten-pairs`).

Two caveats bound the whether result. First, whether-agreement on all k readings is monotone decreasing
in k by construction. Scout's falls from 0.961 at two readings to 0.8539 at twenty and crosses the bar
between five and ten (`r31ext-scout-whether-agreement-curve`). Qwen's stays above the bar at its
deepest depth of ten, 0.961 (`r31ext-qwen-whether-agreement-curve`). Qwen has no value at twenty
readings, which is absent and not passed. Second, Qwen calls 0.9805 of pairs positive on its first reading, so part of its whether-agreement
is a base-rate effect (`r31-cross-judge-whether-kappa`). Between the two judges, observed
agreement on the verdict is 0.9286 over 308 pairs while κ is only 0.29 (`r31-cross-judge-whether-kappa`).

Between judges, location agreement is lower again. In an exploratory comparison, where both local
judges call a document positive on their first readings, their spans overlap at a mean Jaccard of
0.2123 and a median of 0.0805, over 281 pairs (`r31-cross-judge-span-jaccard`). Between each judge's
five-reading union, the exploratory mean falls to 0.1613 over 295 pairs
(`r31-cross-judge-span-jaccard-five-reading-unions`). The extension's recomputation at ten readings per
judge covers more pairs, because more pairs are positive somewhere at that depth, and gives a similar
value; the claim note records both. More readings make the two judges agree less about where, not more.

### 5.2 Quote anchoring hallucinates, and whole sentences fix it

The anchor change is a protocol result others can adopt directly (Table 3).

> **Table 3: Hallucinated-span rate by protocol and judge, with Wilson 95 % upper bounds.** Ten-word
> anchors: `s0-hallucinated-span-rate-ten-word-anchor`, `r3-scout-hallucinated-span-rate`,
> `r3-qwen-hallucinated-span-rate`. Whole-sentence anchors: `r31-scout-hallucinated-span-rate`,
> `r31-scout-hallucinated-span-rate-all-presentations`, `r31-qwen-hallucinated-span-rate`,
> `claude-sonnet5-hallucinated-span-rate`, `claude-opus5-hallucinated-span-rate`,
> `claude-fable51-hallucinated-span-rate`. New readings in the extension:
> `prereg-p-ext-3-hallucination-holds`. Anchor split: `r3-scout-closing-anchor-failures`,
> `r31-scout-closing-anchor-failures`.

Under ten-word anchors, the first labeling pass hallucinated 24 of 476 spans, 0.05042, failing the ≤
0.05 gate (`s0-hallucinated-span-rate-ten-word-anchor`). Under the quote-primary protocol with the same
anchors, Scout hallucinated 58 of 707 spans, 0.08204 (`r3-scout-hallucinated-span-rate`). Qwen
hallucinated 9 of 356, 0.02528 (`r3-qwen-hallucinated-span-rate`). The two judges' rates are not over
comparable exposure, because Qwen attempted about half as many spans on the same pairs
(`r3-qwen-hallucinated-span-rate`). Of Scout's 58 hallucinated spans, 54 failed on the closing ten-word
anchor and 4 on the opening one (`r3-scout-closing-anchor-failures`). The model copied the start of a
span and composed its end.

With whole-sentence anchors, Scout's rate on its first reading fell to 0.02517, 22 of 874 spans, with a
Wilson upper bound of 0.03782 (`r31-scout-hallucinated-span-rate`). Over all five presentations it is
0.01957, 85 of 4,344 spans (`r31-scout-hallucinated-span-rate-all-presentations`). Only 6 of the 22
first-reading failures were on the closing anchor (`r31-scout-closing-anchor-failures`), so the change
fixed the defect it targeted. Qwen's first-reading rate fell to 0.00279, 1 of 358, with a Wilson upper
bound of 0.01565 (`r31-qwen-hallucinated-span-rate`). The gain held on the new readings of the
extension. The pre-registered prediction that the rate stays ≤ 0.05 scored PASS, at 0.01956 for Scout
over 13,291 spans and 0.01061 for Qwen over 1,791 (`prereg-p-ext-3-hallucination-holds`).

The third-family judges were run only under whole-sentence anchors. Sonnet 5 hallucinated 1 of 525
spans, 0.0019 (`claude-sonnet5-hallucinated-span-rate`). Opus 5 hallucinated 1 of 955, 0.00105
(`claude-opus5-hallucinated-span-rate`). Fable 5.1 hallucinated none of 645 spans, on 251 pairs, with a
Wilson upper bound of 0.00592 (`claude-fable51-hallucinated-span-rate`). These models have no
ten-word-anchor measurement, so these figures show that the protocol works for them, not that it
improved them. The result holds for the two local models, against a ten-word alternative, under a
match rule that accepts exact, normalised and eight-word-fallback matches (Section 6).

### 5.3 Neither union converges within thirty readings

We accumulate two unions over a judge's readings. The coarse one counts distinct evidence sets, where
two sets merge when their span unions reach Jaccard ≥ 0.5 (`r31-scout-union-not-saturating-at-5`). The
fine one counts distinct sentences, with no merging. For each we report two statistics: the marginal
gain at the last reading, against the 0.05 bar the run used
(`r31-scout-union-not-saturating-at-5`), and the position on a fitted
Michaelis-Menten curve, n / (Km + n), against the within-10 % criterion the sentence prediction used.

> **Figure 1: Union growth against readings.** Panel (a): set-level union for Scout (to 20) and Qwen
> (to 10). Panel (b): sentence-level union, Scout and Qwen (blow-up excluded) and pooled to 30, with the
> pre-registered and refitted Michaelis-Menten curves. Claims: `r31-scout-union-not-saturating-at-5`,
> `r31-qwen-union-not-saturating-at-5`, `r31ext-scout-set-union-marginal-gain`,
> `r31ext-qwen-set-union-marginal-gain`, `r31ext-scout-set-union-fraction-of-asymptote`,
> `r31ext-qwen-set-union-fraction-of-asymptote`, `prereg-p-ext-2-fit-parameters`,
> `prereg-p-ext-2-excluding-blowups`, `r31ext-scout-sentence-fit-excluding-blowups`,
> `r31ext-qwen-sentence-fit-excluding-blowups`, `r31ext-pooled-sentence-union-still-growing`.

> **Table 4: Both statistics for both unions.** Sets: Scout 0.0274 marginal gain at k = 20 and 0.6949 of
> its asymptote; Qwen 0.0461 at k = 10 and 0.7094; sentences: Scout 0.0272 and 0.8118; Qwen 0.0694 and
> 0.7098; pooled 0.0264 at k = 30 and 0.7816; claims: `r31ext-scout-set-union-marginal-gain`,
> `r31ext-scout-set-union-fraction-of-asymptote`, `r31ext-qwen-set-union-marginal-gain`,
> `r31ext-qwen-set-union-fraction-of-asymptote`, `r31ext-scout-sentence-fit-excluding-blowups`,
> `r31ext-qwen-sentence-fit-excluding-blowups`, `r31ext-pooled-sentence-union-still-growing`.

At five readings, Scout's set union was gaining 0.1022 of its size per reading and Qwen's 0.1127
(`r31-scout-union-not-saturating-at-5`, `r31-qwen-union-not-saturating-at-5`). At full depth, Scout's
set union gains 0.0274 at twenty readings over 300 pairs (`r31ext-scout-set-union-marginal-gain`) and
Qwen's 0.0461 at ten over 307 (`r31ext-qwen-set-union-marginal-gain`). Both are under the 0.05 bar (`r31ext-set-union-saturates`). Scout's sentence union, excluding the blown-up pair, gains 0.0272 at twenty readings over 299 pairs, about the
same (`r31ext-scout-sentence-fit-excluding-blowups`). Qwen's sentence union gains 0.0694 at ten over 306
pairs (`r31ext-qwen-sentence-fit-excluding-blowups`). Pooling both judges' thirty readings, the
sentence union gains 0.0264 over 306 pairs (`r31ext-pooled-sentence-union-still-growing`). By the
marginal-gain criterion the two objects are not distinguishable.

On the fitted curves, neither object is near its asymptote. In an exploratory reading of fits that
were not pre-registered for sets, Scout's set union stands at 0.6949 of its asymptote at twenty
readings (`r31ext-scout-set-union-fraction-of-asymptote`). Qwen's stands at 0.7094 at ten
(`r31ext-qwen-set-union-fraction-of-asymptote`). Scout's sentence union stands at 0.8118
(`r31ext-scout-sentence-fit-excluding-blowups`), Qwen's at 0.7098
(`r31ext-qwen-sentence-fit-excluding-blowups`), and the pooled sentence union at 0.7816 after thirty
readings (`r31ext-pooled-sentence-union-still-growing`). None is within the 10 % of its asymptote that
the pre-registration used as its convergence criterion (`prereg-p-ext-2-fit-parameters`). Neither the
coarse nor the fine object has converged by twenty to thirty readings. This is the repeated-measures
counterpart of FEVER's finding that more effort finds more evidence (`thorne-2018-fever`), measured as
a curve.

**The union of a few readings is not a stable span set.** The direct test of merging readings is
whether the union of some reproduces the union of others. At five readings, the union of the first
three reproduces the union of the last two on 0.4708 of pairs for Scout, 145 of 308
(`r31-scout-union-as-labeler-unstable`). For Qwen the figure is 0.5779, 178 of 308
(`r31-qwen-union-as-labeler-unstable`). These are five-reading figures with halves three and two deep.
At a matched depth of six readings, the pooled graded score has a reliability of 0.4738
(`graded-support-reliability-at-6`), about the same. So at shallow depth neither object is stable. A
union split-half at depth, ten readings against ten or fifteen against fifteen, was not measured and is
absent. We therefore do not claim that a deep union would stay unstable. We claim only that it has not
converged in the counts above and that its stability at depth is unknown. WiCE declined the union for a
semantic reason, that different sentences can carry identical information (`kamoi-2023-wice`). Our
data give no reason to accept it at the depths measured.

**The pre-registered fit, scored honestly.** Before any extension label existed, a Michaelis-Menten
curve fitted to Scout's first five readings gave Vmax = 72.3277, Km = 4.6179 and r² = 0.994174
(`prereg-p-ext-2-fit-parameters`). It projected 58.7603 distinct sentences at twenty readings, which is
81.24 % of the asymptote (`prereg-p-ext-2-fit-parameters`). The prediction as written required the
observed value to lie within 10 % of the asymptote, and the pre-registration stated in advance that
this would fail; the informative test was the secondary, whether the projection lands within 10 % of
the observed value (`prereg-p-ext-2-fit-parameters`).

Scored mechanically, the prediction passed for the wrong reason. The observed mean at twenty readings
was 133.8267, which is 1.85 times the pre-registered asymptote, so the primary scored PASS while the
secondary failed with a relative error of 1.2775 (`prereg-p-ext-2-scored`). One reading in 6,160 caused
this (`locator-blowup-dev`). On a five-megabyte compendium document, a locator defect turned 22 spans
the model wrote into 986 emitted spans covering 22,366 sentences (`locator-blowup-dev`). The scored
estimator is a mean of a heavy-tailed count, and that one reading dominated it.

The following reading is exploratory, because its exclusion criterion was chosen after scoring. It was
later formalised as the post-filter of Section 5.6, which was applied identically to both populations.
Excluding the blown-up pair, the observed value at twenty readings is 58.6187 over 299 pairs, against
the projection of 58.7603, a relative error of 0.0024 (`prereg-p-ext-2-excluding-blowups`). On this
reading the primary fails, as the pre-registration said it would, and the secondary passes. A
five-point curve extrapolated four-fold landed within a quarter of a percent. The mechanical PASS is an
artefact. The exploratory reading gives a predicted FAIL and a passed secondary. The median, 42.0
sentences, is unaffected by the blow-up (`prereg-p-ext-2-excluding-blowups`). It is the estimator a
replication should pre-register.

We do not quote the unfiltered twenty-point or pooled fits. Both are degenerate, with Km at the search
ceiling and the curve linear over the measured range, so they have no identifiable asymptote. The
fraction of the asymptote reached is absent, not zero (`r31ext-scout-sentence-fit-degenerate-unfiltered`,
`r31ext-pooled-fit-degenerate-unfiltered`).

**Single-reading disagreement is not a set-equality artefact.** HotpotQA's whether/where gap lives
almost entirely in exact set match. Supporting-fact exact match is 61.50 while supporting-fact F1 is
90.04 (`yang-2018-hotpotqa`). Our one-reading-against-one-reading disagreement is different. In an
exploratory partial-credit reading, the raw mean pairwise span-union Jaccard over all ten reading pairs
is 0.4350 for Scout, with a median of 0.3534 over 2,898 comparisons (`r31-scout-partial-credit-jaccard`).
For Qwen it is 0.5430, with a median of 0.7518 over 3,056 (`r31-qwen-partial-credit-jaccard`). Qwen's
distribution is bimodal: many near-identical re-readings and a tail of near-disjoint ones. The mean
alone understates how often it reproduces itself exactly, and the median alone hides the tail. Partial
credit does not lift single-reading span agreement anywhere near HotpotQA's supporting-fact F1.

### 5.4 Graded support becomes reliable with depth

Each sentence's graded support is the fraction of readings that mark it as evidence. Table 5 and
Figure 2 give its reliability against the number of readings, pooled and per judge.

> **Table 5 / Figure 2: Graded-support reliability against readings.** Pooled k-rater reliability at k =
> 4, 6, 8, 10, 14, 20 and 30; Scout alone to 20; Qwen alone to 10; the ≥ 0.9 bar; pairs with no
> variance in a half. Claims: `graded-support-reliability-at-4`, `graded-support-reliability-at-6`,
> `graded-support-reliability-at-8`, `graded-support-reliability-at-10`,
> `graded-support-reliability-at-14`, `graded-support-reliability-at-20`,
> `graded-support-reliability-at-30`, `judge-scout-support-reliability-curve`,
> `judge-qwen-support-reliability-curve`, `graded-support-degenerate-halves-at-4`,
> `graded-support-degenerate-halves-curve`, `graded-support-bar`.

**Pooled.** At ten readings, the split-half reliability is 0.5444 and the full-instrument reliability
0.7050 (`graded-support-reliability-at-10`). At twenty, they are 0.7526 and 0.8588
(`graded-support-reliability-at-20`). At thirty, they are 0.8527 and 0.9205
(`graded-support-reliability-at-30`). Only the thirty-reading pooled point clears the bar of ≥ 0.9
(`graded-support-bar`). The bar was written for a gate that compares one reading with one reading, and
the pooled instrument is thirty readings deep. Clearing it says the thirty-reading map is reproducible,
not that the graded object beats the span gate at equal depth.

**Per judge.** In an exploratory per-judge reading of the same estimator, Scout alone reaches 0.8338 at
ten readings, 0.8821 at fourteen and 0.9142 at twenty (`judge-scout-support-reliability-curve`). Scout
clears the bar at twenty readings on its own. Qwen alone reaches 0.7216 at ten, its full depth
(`judge-qwen-support-reliability-curve`). Twenty readings therefore suffice for one judge, but not for
the pooled instrument at twenty.

**Why the pooled curve has the shape it has.** Exploratorily, inverting Spearman-Brown at each k gives
the implied single-reading reliability, ρ₁ = R_k / (k − (k − 1) R_k). For the pooled instrument it is
0.1929 at k = 10, 0.2098 at 14, 0.2332 at 20 and 0.2785 at 30
(`graded-support-implied-single-reading-pooled`). Within Scout it is 0.3341 at 10, 0.3483 at 14 and
0.3476 at 20 (`graded-support-implied-single-reading-scout`). Within Qwen it is 0.2058 at 10
(`graded-support-implied-single-reading-qwen`). Three things follow.

- The pooled single-reading value sits below each judge alone. A pooled split-half mixes readings from
  two judges whose maps agree weakly. Built independently, those maps correlate at a mean per-pair r of
  0.2976 (`judge-judge-graded-support-correlation`).
- The pooled rise from twenty to thirty readings tracks the judge mix. Scout's share of the readings goes
  from one half to two thirds over that range (`graded-support-pooled-order`). As the pool fills with
  the more self-consistent judge, its implied single-reading value moves toward Scout's.
- Within one judge the readings behave as exchangeable. Scout's implied single-reading reliability is
  flat from fourteen to twenty readings (`graded-support-implied-single-reading-scout`). That is what
  Spearman-Brown assumes, and it is the test that matters for projecting one judge's curve.

The pooled endpoint is therefore the reliability of a particular mixture of two judges, not evidence
that pooling beats the prophecy formula. Wong and Paritosh's precedent has the same shape: a modest
single rating, a reliable aggregate (`wong-2022`). The single-reading reliability is low, about a third
for Scout and a fifth for Qwen. One reading is a poor estimate of a sentence's support, and that is
the cost of the method.

The small-k points are attenuated by pairs whose half has no variance. On the pooled instrument there
are 47 such pairs at four readings (`graded-support-degenerate-halves-at-4`), and 2, 2, 1 and 1 at ten,
fourteen, twenty and thirty (`graded-support-degenerate-halves-curve`). So k = 4 and k = 6 must not be
used as Spearman-Brown anchors, and k ≥ 10 is the honest range
(`graded-support-degenerate-halves-curve`).

**The result does not rest on estimator choices.** Excluding the locator blow-up gives 0.9233, and
restricting to pairs with at least three touched sentences gives 0.9209
(`graded-support-reliability-sensitivities`). The scored value is the pre-registered estimator with no
exclusions. Over the twenty seeded splits at thirty readings, the split-half r has a standard deviation
of 0.0059 (`graded-support-reliability-at-30`). The three highest-support sentences of each document
overlap at 0.7551 between halves (`graded-support-reliability-at-30`).

**The bet was genuine.** The pre-registered prediction set the threshold at 0.85
(`prereg-p-ext-1-as-registered`). That was below the projection made before the run, 0.8826, and above
the 0.7050 that ten readings had bought (`prereg-p-ext-1-scored`, `graded-support-reliability-at-10`).
It scored PASS at 0.9205 (`prereg-p-ext-1-scored`).

### 5.5 Third-family judges land on supported sentences

We asked whether the pooled map is only the local judges' idiosyncrasy by checking it against three
judges from a separate family, each read once. Over every sentence the local pool supported or the
judge picked, a pick by the judge correlates with pooled support at a point-biserial r of 0.2353 for
Sonnet 5, 0.2758 for Opus 5 and 0.2383 for Fable 5.1 (`cross-family-support-correlation`). The
Spearman ρ values are 0.1353, 0.1279 and 0.1238 (`cross-family-support-correlation`). The association
is positive in all three and modest in all three. Fable's figures are over its 251 pairs
(`fable51-incomplete-n`).

> **Table 6: Pooled support of sentences picked by third-family judges.** Columns: sentences picked,
> pairs, mean support of picked sentences, baseline, lift, fraction of picks with zero local support,
> point-biserial r, Spearman ρ. Claims: `cross-family-sonnet5-support-lift`,
> `cross-family-opus5-support-lift`, `cross-family-fable51-support-lift`,
> `cross-family-support-correlation`, with `cross-family-local-self-reference-anchor` as an upper
> anchor.

An exploratory lift comparison gives the same direction. The 626 sentences Sonnet 5 picked carry a mean
pooled support of 10.78 of 30 readings, against a baseline of 2.86, a lift of +7.92 over 277 pairs
(`cross-family-sonnet5-support-lift`). Opus 5's 1,327 picks carry 9.18 against the same baseline, a lift
of +6.31 over 306 pairs (`cross-family-opus5-support-lift`). Fable 5.1's 825 picks carry 10.80 against a
baseline of 5.35, a lift of +5.46 (`cross-family-fable51-support-lift`). Fable's baseline differs because
Fable labeled only 251 pairs and its baseline is recomputed over its own 248 positive pairs
(`cross-family-fable51-support-lift`, `fable51-incomplete-n`). Its lift is not directly comparable to
the other two. For scale, Scout's own first reading, one of the thirty that build the map, picks
sentences at mean support 10.97, a lift of +8.11 (`cross-family-local-self-reference-anchor`). That is
an upper anchor by construction, not a comparable baseline.

Not every pick lands on supported ground. Of Sonnet's picks, 0.1262 have zero local support
(`cross-family-sonnet5-support-lift`). For Opus the figure is 0.217 (`cross-family-opus5-support-lift`)
and for Fable 0.1661 (`cross-family-fable51-support-lift`). Those sentences are either evidence the local
judges miss or noise, and the record cannot tell which without a human read.

The third-family judges agree with each other more than the local judges do. Exploratorily, Opus 5 and
Fable 5.1 overlap at a mean span Jaccard of 0.5041 over 248 pairs both call positive, with a
whether-κ of 0.7981 over 251 co-labeled pairs (`cross-family-opus-fable-agreement`). Sonnet 5 overlaps
with Opus 5 at 0.4179 over 277 pairs (`cross-family-sonnet-opus-jaccard`) and with Fable 5.1 at 0.4568
over 230 (`cross-family-sonnet-fable-jaccard`). These are agreements between single readings of
different models, not between converged sets, and not agreement with truth.

### 5.6 At scale, a defect the development set could not show

The method was applied without change to 3,738 pairs on the confirmation topics
(`confirmation-scale-pairs-total`), with 112,140 readings (`confirmation-total-readings`). The manifest
asserted that the prompt and rubric hashes equalled the development run's before the first call
(`instrument-hashes-conf`). No confirmation-topic outcome is computed or reported
(`confirmation-outputs-quarantined`).

The locator defect of Section 5.3 was fixed as a post-filter, applied identically to the development
labels and the scale labels, rather than as a change to the locator between them. On the development
set the filter rewrote 82 of Scout's 6,160 readings, 1.33 %, and none of Qwen's 3,080
(`span-filter-dev-counts`). Qwen had no locator blow-up at all in development
(`locator-blowup-qwen-dev-zero`). On that evidence the defect was Scout's.

At scale, the filter rewrote 1,050 of Scout's 74,760 readings, 1.40 %, and 9 of Qwen's 37,380
(`span-filter-conf-counts`). Those nine Qwen records carried 1,653 dropped spans, more than the 1,441
dropped across Scout's 1,050 filtered records (`span-filter-conf-counts`). The defect fires on long
documents, and the development pairs did not contain documents long enough to trigger it for Qwen.
Scout's rates, 1.33 % of 6,160 in development and 1.40 % of 74,760 at scale, are similar
(`span-filter-dev-counts`, `span-filter-conf-counts`). That is the control: the filter behaves
similarly where it was already exercised.

The filter does not change the instrument's measured property. Applied to the development labels, it
moves thirty-reading graded-support reliability by −0.0013, from 0.9205 to 0.9192, inside a stated
tolerance of ±0.01 (`span-filter-instrument-unchanged`, `span-filter-delta`). The verdict is recorded
as PASS (`span-filter-verdict`). The unfiltered recomputation reproduces the committed 0.9205 exactly,
which shows the estimator was re-run and not re-quoted (`span-filter-instrument-unchanged`).

The transferable lesson: a defect can be invisible at development scale and load-bearing at
confirmation scale, and it is caught only if the fix is a filter applied identically to both sets.
Re-tuning on the development set would have silently produced a different instrument at scale.

---

## 6. Threats to validity

**Reliability is not validity, and no human has validated these labels.** The graded score reproduces
itself. Whether it measures where the evidence for a clinical query lies in an article is not
established, because the two-reader human validation has not been performed and no agent read was
substituted for it (`human-read-not-performed`). There is no κ against a human anywhere in this work,
and the rubric was never signed off by a human reader before labeling began
(`human-read-not-performed`). Norman and colleagues show that test-retest reliability above 0.95 can
coexist with large, systematic bias in deployed LLM judges (`norman-2026-reliability-without-validity`).
A reproducible judge can be reproducibly wrong, and a stable answer key caps what an evaluation can
measure as much as an unstable one does (`soboroff-2025`).

A human read would falsify the construction if high-support sentences were judged non-evidential about
as often as low-support ones, or if two human readers agreed with each other far better than either
agreed with the graded ranking.

**The two local judges disagree with each other.** In an exploratory comparison, each judge's map built
from its own readings at full depth correlates with the other's at a mean per-pair r of 0.2976 and a
median of 0.3099, over 297 pairs (`judge-judge-graded-support-correlation`). Pooled over 40,588
sentences, r is 0.2657 (`judge-judge-graded-support-correlation`). The two judges' three
highest-support sentences per document overlap at 0.4473, and only 0.2727 of pairs reach a correlation
of 0.5 (`judge-judge-top3-overlap`). Between halves of the pooled instrument, the same top-3 overlap is
0.7551 (`graded-support-reliability-at-30`). Each judge reproduces itself far better than it agrees with
the other. Either the families locate different, equally valid evidence and the pooled map joins two
partial views, or one family is closer to the truth and pooling dilutes it. Only a human read can
decide. This disagreement is also why the pooled coefficient sits below Scout's own (Section 5.4).

**Pooling and exchangeability.** Yang and colleagues find that "repeated-sample juries add little when
errors are correlated" (`yang-2026-judge-changes`). Spearman-Brown assumes exchangeable readings. Within
Scout the assumption holds over the range we can test: the implied single-reading reliability is flat
from fourteen to twenty readings (`graded-support-implied-single-reading-scout`). Across judges it does
not hold, and the pooled curve reflects the changing judge mix (`graded-support-pooled-order`). The
universe of scored sentences also grows with k, so each point is computed over a slightly different set
of sentences. A direct mean pairwise correlation between single readings was not computed and is
absent. The inverted single-reading values stand in for it.

**Temperature zero does not mean deterministic, and the variation is not attributed in full.**
Schroeder and Wood-Doughty report a reliability coefficient of 1.0 at temperature zero, which holds by
construction for a deterministic decoder (`schroeder-2024-trust`). Shi and colleagues report repetition
stability of 0.96 to 1.00 for strong judges on pairwise tasks (`shi-2025-position-bias`). We run at
temperature zero and do not get reproduction, so we owe an account of the variation. Most of it is
introduced by design: each reading presents the units in a different seeded order, so readings are
re-presentations, not re-samples. A second source is serving: kernels that are not batch-invariant
make temperature-zero output depend on server load. One public demonstration found 80 distinct
completions in 1,000 identical temperature-zero requests to a large open model (`he-2025`, a laboratory
blog post, not peer-reviewed). We did not separate the two sources. Span self-consistency under
repeated identical presentation, which would measure the serving share, is absent. What the design does
exclude is sampling temperature.

**The competing explanation: legitimate perspectival difference.** Span variation between human
annotators is often read as legitimate disagreement and not as measurement error (`xu-2023-rave`).
Jakobsen and colleagues find systematic demographic structure in which rationale an annotator picks
(`jakobsen-2023`). That reading is available for every inter-annotator result in Section 2. It is much
weaker here: each comparison is one model, one document, one prompt and one rubric, at temperature zero,
repeated, so there is no second annotator for the variation to belong to. This argument does not
transfer to the human literature.

**The human floor.** People are not much more stable than our judges. Scholer and colleagues ran a
prospective test-retest with a between-assessor comparison in the same experiment. Self-agreement was
51.62 % against between-participant agreement of 44.80 %, so a person agrees with themselves only about
seven points better than with a stranger (`scholer-2013`). Two NIST assessors at sentence level reach F
of 0.58 for relevant and 0.46 for novel sentences (`soboroff-2003-novelty`). Medical experts reach
exact-boundary PICO F1 of 0.357 to 0.576 (`lee-sun-2019-pico`). Our one-reading-against-one-reading span
self-consistency, 0.3831 and 0.5617 (`r31-scout-span-self-consistency`,
`r31-qwen-span-self-consistency`), and our partial-credit Jaccard, 0.4350 and 0.5430
(`r31-scout-partial-credit-jaccard`, `r31-qwen-partial-credit-jaccard`), sit in the same range. The
statistics differ, so this is a framing, not a comparison. On that framing, a machine re-reading a
document is about as unstable as a person re-reading it. The TREC RAG support study makes the same point
more sharply: there, two trained assessors agreed on sentence support at κ of −0.03 and 0.07
(`thakur-2025-support`). Span disagreement may be a property of the task more than of language models.
That would not change which object we recommend scoring, only how the instability is described.

**A published counter-result in our own field.** Hofstätter and colleagues measured location agreement
separately on TREC data and found no gap (`hofstatter-2020-fira`). Agreement on relevant word selection
reached κ between 0.5 and 0.8, the same band as two-class relevance, with only four-class grading lower
at 0.3 to 0.6 (`hofstatter-2020-fira`). Presence and location agree equally there, which is a result
against our headline even though it is not the opposite ordering. We state its blunting factors rather
than use them to dismiss it: κ is computed against a majority aggregate that includes the annotator
scored, on ten query-document pairs, by student annotators, and only documents already judged relevant
were annotated, which makes the presence condition easier than ours (`hofstatter-2020-fira`).

**Model tier changes the quote-fidelity result.** Zhang and colleagues report that most current models
attach verbatim quotes to over 90 % of their claims from prompting alone
(`zhang-2026-clinical-verbatim`). Our own data agree: the third-family judges hallucinate at 0.0019 or
less (`claude-sonnet5-hallucinated-span-rate`, `claude-opus5-hallucinated-span-rate`,
`claude-fable51-hallucinated-span-rate`), and only the local judges did badly under ten-word anchors.
The anchor result of Section 5.2 holds for a stated model tier, anchor length and match rule, and does
not generalise to frontier models.

**Abstract bias in the judges.** The human-read sample was stratified to include pairs whose evidence
lies deep in the article. That stratum wanted 20 pairs, and only 3 of the 308 development pairs
qualified under the original labels (`deep-section-stratum-underpopulated`). The stratum the human read
most needs is the one the labels barely populate. A graded map built on readers with an abstract bias
may under-weight deep evidence, and do so reliably.

**Reduced and differing samples.** The Fable 5.1 run stopped at 251 of 308 pairs
(`fable51-incomplete-n`). Its figures are on that sample, and its support baseline is computed over its
own pairs. Several early figures in Section 5.1 rest on 31 re-presented pairs, not 308
(`r3-scout-span-self-consistency`). Each figure in this paper states its n where it differs from the
headline.

**Statistics that are absent.** No self-consistency, whether-agreement or union-saturation figure exists
for any third-family judge, because each was read once (`claude-self-consistency-absent`). Those
statistics are absent, neither zero nor passed. The minimality audit of the first protocol could not be
measured: its re-prompt returned an empty set on 29 of 29 audited pairs, so the instrument failed to run
(`s0-minimality-unmeasurable`). The unfiltered sentence-union fits are degenerate, and their asymptote
fractions are absent (`r31ext-scout-sentence-fit-degenerate-unfiltered`,
`r31ext-pooled-fit-degenerate-unfiltered`). A union split-half at depth (ten readings against ten,
fifteen against fifteen), span self-consistency under identical presentation, and a direct
inter-reading correlation were never measured.

**Statistic choice.** We report mean pairwise agreement and Jaccard for location, not κ, for the reasons
in Section 3.4 (`hripcsak-2005`, `mathet-2017`). We have not shown that the contrast survives a
chance-corrected γ-family coefficient (`mathet-2017`); the released labels allow that reanalysis.

---

## 7. Reproducibility

**The run package.** `docs/plans/results/RUN-PACKAGE.md` documents what a different site needs: the data
inventory, the served models and their limits, the environment, and the sizing. The harnesses that
produced every artifact cited here are committed beside their results under
`docs/plans/results/stage0/`. They keep their original pins.

**Instrument identity.** The rubric and prompt are identified by sha256, and the hashes are asserted
before the first call of every run. The final protocol's prompt is `ba09e122…` and the rubric
`2e11f368…`, at temperature 0 (`instrument-hashes-conf`). The sentence segmenter is asserted unchanged
from the study's pinned commit before every run, so sentence identifiers in the labels share one
coordinate system.

**Seeds.** The local judges use a fixed sampler seed and a presentation seed that is a pure function of
the pair index and the reading index (`instrument-hashes-conf`), so any reading can be regenerated
individually. The reliability estimator's half-splits are seeded.

**Served models.** The local judges are `RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic` and
`Qwen/Qwen3.6-35B-A3B`, served behind an OpenAI-compatible endpoint. The served model id is probed and
asserted before the first call. The third-family judges are `claude-sonnet-5`, `claude-opus-5` and
`claude-fable-5-1`. The served id is read from each call's own usage record.

**What is released and what cannot be.** The committed record holds the label records for every
development reading, the gate and prediction files, and the scale application's counts and filter
verification. It does not hold the raw model responses, which include the reasoning judge's stripped
thinking. The label records carry a hash over each raw response. The confirmation-topic label files are
not committed, and the confirmation outcomes are quarantined by the harness
(`confirmation-outputs-quarantined`). The article texts come from the PubMed Central Open Access subset
and are fetched by identifier. A corpus manifest hash fixes which bytes were used.

**What a different site must supply.** Hosting for the two open-weight judges at the stated model ids,
with enough context for full-length articles. Access to the third-family judges, which were reached
through a command-line client on a personal account, with isolation flags that keep the client's own
system prompt and tools out of the judge's context, and not through an API with pinned parameters. The
recorded invocation sets no sampling temperature. Exact reproduction of the third-family readings
therefore depends on the provider's serving defaults at the time of the run. A replication should treat
them as a reproduction of the protocol, not of the outputs.

Finally, a replication should pre-register the median, not the mean, for the sentence-union count. The
mean is what a single pathological reading turned into a spurious pass (Section 5.3).

---

## References

Keys resolve to entries in `docs/papers/bibliography.md` or the verified survey files in
`docs/papers/bib-inbox/`.

- `abercrombie-2025-consistency` — Abercrombie, Dinkar, Cercas Curry, Rieser, Hovy. Consistency is
  Key: Disentangling Label Variation in Natural Language Processing with Intra-Annotator Agreement.
  NLPerspectives workshop, 2025.
- `allan-2003-hard` — Allan. HARD Track Overview in TREC 2003. TREC 2003.
- `barrie-2024-pss` — Barrie, Palaiologou, Törnberg. Prompt Stability Scoring for Text Annotation
  with Large Language Models. arXiv 2407.02039, 2024.
- `clarke-dietz-2024` — Clarke, Dietz. LLM-based relevance assessment still can't replace human
  relevance assessment. EVIA 2025; arXiv 2412.17156.
- `deyoung-2020-eraser` — DeYoung et al. ERASER: A Benchmark to Evaluate Rationalized NLP Models.
  ACL 2020.
- `faggioli-2023` — Faggioli et al. Perspectives on Large Language Models for Relevance Judgment.
  ICTIR 2023.
- `haldar-2025-rating-roulette` — Haldar, Hockenmaier. Rating Roulette: Self-Inconsistency in
  LLM-As-A-Judge Frameworks. EMNLP 2025.
- `he-2025` — He and Thinking Machines Lab. Defeating Nondeterminism in LLM Inference. Blog post,
  2025. Not peer-reviewed.
- `hofstatter-2020-fira` — Hofstätter, Zlabinger, Sertkan, Schröder, Hanbury. Fine-Grained
  Relevance Annotations for Multi-Task Document Ranking and Question Answering. CIKM 2020.
- `hong-2025` — Hong, Chen, Peng, de Marneffe, Plank. LiTEx: A Linguistic Taxonomy of Explanations
  for Understanding Within-Label Variation in Natural Language Inference. EMNLP 2025.
- `hripcsak-2005` — Hripcsak, Rothschild. Agreement, the F-Measure, and Reliability in Information
  Retrieval. JAMIA 12(3), 2005.
- `jakobsen-2023` — Jakobsen, Cabello, Søgaard. Being Right for Whose Right Reasons? ACL 2023.
- `jiang-2023` — Jiang, Tan, de Marneffe. Ecologically Valid Explanations for Label Variation in
  NLI. Findings of EMNLP 2023.
- `kamoi-2023-wice` — Kamoi, Goyal, Rodriguez, Durrett. WiCE: Real-World Entailment for Claims in
  Wikipedia. EMNLP 2023.
- `kasner-2026-spans` — Kasner et al. LLMs as Span Annotators: A Comparative Study of LLMs and
  Humans. MME workshop at EACL 2026; arXiv 2504.08697.
- `kwiatkowski-2019-nq` — Kwiatkowski et al. Natural Questions: a Benchmark for Question Answering
  Research. TACL 7, 2019.
- `lee-sun-2019-pico` — Lee, Sun. A Study on Agreement in PICO Span Annotations. SIGIR 2019.
- `mathet-2017` — Mathet. The Agreement Measure γ_cat, a Complement to γ Focused on Categorization
  of a Continuum. Computational Linguistics 43(3), 2017.
- `mathew-2021-hatexplain` — Mathew et al. HateXplain: A Benchmark Dataset for Explainable Hate
  Speech Detection. AAAI 2021.
- `muscato-2026-disagreeing-rationales` — Muscato, Chen, Gezici, Plank, Giannotti. Disagreeing
  Rationales: Rethinking Classification and Explainability Evaluation in Hate Speech Detection.
  arXiv 2605.31563, 2026.
- `norman-2026-reliability-without-validity` — Norman, Rivera, Hughes. Reliability without
  Validity: A Systematic, Large-Scale Evaluation of LLM-as-a-Judge Models Across Agreement,
  Consistency, and Bias. arXiv 2606.19544, 2026.
- `reiss-2023` — Reiss. Testing the Reliability of ChatGPT for Text Annotation and Classification:
  A Cautionary Remark. arXiv 2304.11085, 2023.
- `saha-2026` — Saha, Mitra, Dutta. LLMs as Assessors: Right for the Right Reason? arXiv
  2601.08919, 2026.
- `scholer-2013` — Scholer, Kelly, Wu, Lee, Webber. The effect of threshold priming and need for
  cognition on relevance calibration and assessment. SIGIR 2013.
- `schroeder-2024-trust` — Schroeder, Wood-Doughty. Can You Trust LLM Judgments? Reliability of
  LLM-as-a-Judge. arXiv 2412.12509, 2024.
- `shi-2025-position-bias` — Shi et al. Judging the Judges: A Systematic Study of Position Bias in
  LLM-as-a-Judge. AACL-IJCNLP 2025.
- `signe-2026-chyd` — Signé, Boughanem, Moreno, Belkacem. Guaranteeing Faithful Evidence Extraction
  in Speculative Retrieval-Augmented Generation. arXiv 2609.10046, 2026.
- `soboroff-2003-novelty` — Soboroff, Harman. Overview of the TREC 2003 Novelty Track. TREC 2003.
- `soboroff-2025` — Soboroff. Don't Use LLMs to Make Relevance Judgments. Information Retrieval
  Research 1(1), 2025.
- `thakur-2025-support` — Thakur et al. Support Evaluation for the TREC 2024 RAG Track: Comparing
  Human versus LLM Judges. SIGIR 2025.
- `thomas-2024` — Thomas, Spielman, Craswell, Mitra. Large Language Models can Accurately Predict
  Searcher Preferences. SIGIR 2024.
- `thorne-2018-fever` — Thorne, Vlachos, Christodoulopoulos, Mittal. FEVER: a Large-scale Dataset
  for Fact Extraction and VERification. NAACL-HLT 2018.
- `upadhyay-2024-umbrela` — Upadhyay, Pradeep, Thakur, Craswell, Lin. UMBRELA: UMbrela is the
  (Open-Source Reproduction of the) Bing RELevance Assessor. arXiv 2406.06519, 2024.
- `warfield-2004-staple` — Warfield, Zou, Wells. Simultaneous Truth and Performance Level
  Estimation (STAPLE). IEEE Transactions on Medical Imaging 23(7), 2004.
- `weller-2024-according-to` — Weller et al. "According to …": Prompting Language Models Improves
  Quoting from Pre-Training Data. EACL 2024.
- `wong-2022` — Wong, Paritosh. k-Rater Reliability: The Correct Unit of Reliability for
  Aggregated Human Annotations. arXiv 2203.12913, 2022.
- `xu-2023-rave` — Xu et al. From Dissonance to Insights: Dissecting Disagreements in Rationale
  Construction for Case Outcome Classification. EMNLP 2023; arXiv 2310.11878.
- `yang-2018-hotpotqa` — Yang et al. HotpotQA: A Dataset for Diverse, Explainable Multi-hop
  Question Answering. EMNLP 2018.
- `yang-2026-judge-changes` — Yang, Hou, Yang. When the Judge Changes, So Does the Measurement:
  Auditing LLM-as-Judge Reliability. arXiv 2607.08535, 2026.
- `zaidan-2007-rationales` — Zaidan, Eisner, Piatko. Using "Annotator Rationales" to Improve
  Machine Learning for Text Categorization. NAACL-HLT 2007.
- `zhang-2025-longcite` — Zhang et al. LongCite: Enabling LLMs to Generate Fine-grained Citations
  in Long-Context QA. Findings of ACL 2025.
- `zhang-2026-clinical-verbatim` — Zhang, Chen, Commodore-Mensah, Oberst. Verifiable by
  Construction: Claim-Level Evaluation of Verbatim Citation in Clinical Question Answering. arXiv
  2609.15964, 2026.
- `zobel-1998` — Zobel. How reliable are the results of large-scale information retrieval
  experiments? SIGIR 1998.
