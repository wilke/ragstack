# Paper A — outline

**Working title.** Reliable but not canonical: LLM judges for passage-level evidence
localisation.

**Target.** arXiv preprint, venue-neutral. Chosen because the result is complete and does not
wait on the chunking verdict.

**Audience.** Anyone building passage-level or span-level relevance gold with a language
model, which is now most people building retrieval evaluations over long documents. The result
is not specific to chunking, to biomedicine, or to our retrieval stack.

**The one-sentence claim.** A language model asked to locate evidence inside a document agrees
with itself about *whether* the document contains evidence and disagrees about *where*; what
converges under more readings depends on how coarsely you ask — the family of distinct span
*sets* saturates, the set of *sentences* ever marked does not, and pooling readings into a
union does not yield a stable span set either; but a graded per-sentence support score over the
same readings does reproduce itself, clears a usable reliability bar, and is not one model
family's artefact.

**The thesis in two numbers, from the same thirty readings of the same pairs.** Graded
per-sentence support reaches Spearman-Brown 0.9205 (`graded-support-reliability-at-30`). The
span-set union built from those same readings reproduces itself on 0.4708 of pairs
(`r31-scout-union-as-labeler-unstable`). One object is reliable and the other is not, and the
difference is the object scored, not the data.

**What this paper does not claim.** That the graded score is *correct*. Reliability is not
validity, the human read has not happened, and the paper says so in its own voice.

**What this paper does NOT claim to have discovered** — established 2026-09-22 by the
literature survey, and the outline was rewritten because of it. Every one of these is prior
art and must be cited as such, not rediscovered:

| we do not claim | who has it |
|---|---|
| annotators disagree about *which* span while agreeing evidence exists | `kamoi-2023-wice` (5 workers, exact-set match 56.1 % / 34.4 %); Natural Questions (25-way, "multiple valid long answers in multiple distinct locations") |
| the evidence union keeps growing with effort | `thorne-2018-fever`'s super-annotator study — 95.42 % precision but **72.36 % recall** against unlimited-effort exhaustive annotation; one claim gained 34 sentences |
| multiple valid evidence sets should be retained rather than merged | `kamoi-2023-wice` keeps them and **explicitly refuses the union**, "as there can be multiple different sets of sentences with identical information" |
| a soft/graded rationale beats a hard set under annotator variation | `muscato-2026-disagreeing-rationales`, token-level |
| models hallucinate quoted spans | `signe-2026-chyd`, quantified, with a constrained-decoding fix |
| asking an LLM to *locate* evidence inside a document | `saha-2026`, "LLMs as Assessors: Right for the Right Reason?" — LLM highlights scored against human highlights on INEX. One reading per item, no agreement baseline, framed as accuracy not stability |
| measuring a judge against *itself* over repeated runs | `haldar-2025-rating-roulette` defines "self-reliability" verbatim — but whole-response only, three runs, no span or sub-response analysis |
| reliability coexisting with invalidity in LLM judges | `norman-2026-reliability-without-validity`: test–retest > 0.95 alongside position bias > 0.10, over 541k judgments |
| a strict/relaxed two-number shape for span agreement | `lee-sun-2019-pico`, SIGIR 2019: medical experts on biomedical abstracts, exact-boundary F1 0.357–0.576 against relaxed token-overlap F1 0.713–0.758 |
| decomposing agreement into *presence* and *location* | **`mathet-2017`, γ_cat** (Computational Linguistics) — a chance-corrected coefficient built for exactly this, where γ − γ_cat isolates the positional component. Barely cited, and it must be engaged by name |
| a graded pooled per-unit support object | `warfield-2004-staple` (probabilistic per-voxel reference by EM over raters), `mathew-2021-hatexplain` (soft per-token rationales), `muscato-2026` (SOFT vs HARD vs union) — **novel as a representation: no.** Our claim narrows to the reliability *measurement* |
| pooling repeated LLM runs and reporting α | `reiss-2023` (α 0.75 → 0.91 over ten repetitions), Barrie and colleagues' intra-PSS over 30 identical-prompt repetitions |
| the name for agree-on-verdict / diverge-on-reasoning | **"within-label variation"** (`hong-2025`, `jiang-2023`). Do not coin a new term |
| the estimator itself | **k-rater reliability**, `wong-2022`: ICC(k), equivalent to Spearman-Brown |

**The claim that survives, and it is narrower and better.** The field has repeatedly *observed*
span-set disagreement and then **worked around it**: FEVER scores against any one annotated set
and concedes completeness "is not feasible"; WiCE keeps multiple sets and declines the union;
ERASER went back to collect "comprehensive" rationales precisely because the originals were
known to be incomplete. Nobody has measured how bad it is **as a function of effort**, and
nobody has reported **a reliability coefficient** for evidence-span selection at all. We do
both, and we show that the workaround the field reaches for — the union — is the wrong object,
while a graded per-sentence score is the right one.

Four things the survey looked for and did not find, which is what the contribution now rests on:

1. **No reliability coefficient — split-half, Spearman-Brown, test-retest or ICC — is reported
   anywhere for evidence-span selection.** `haldar-2025-rating-roulette` measures self-reliability
   for whole responses and `norman-2026` for whole judgments; neither goes inside the document.
   Ours is the first at span level.
2. **No study models union growth as a function of the number of readings.** Ours is fitted,
   and the fit was pre-registered and extrapolated four-fold to within 0.24 %.
3. **Test-retest within one model**, at temperature zero, *inside* a document. The span prior art
   is all *between* annotators, which carries a competing reading (see §6); the self-reliability
   prior art is all at whole-response granularity.
4. **The anchor unit as an independent variable.** `signe-2026-chyd` documents quote
   hallucination and fixes it at the decoder; `weller-2024-according-to` shows a prompt-level
   instruction moves verbatim fidelity; `zhang-2025-longcite` adopts sentence-index citation as a
   format choice without comparing it to quoting. Nobody varies requested anchor granularity and
   measures fidelity. We sit between prompting and constrained decoding.

---

## Sections

### 1. Problem

Passage-level retrieval evaluation needs gold that says which passage answers the query, not
merely which document is relevant. Document-level judgments answer a different question, and on
long documents the difference is large. Hand-labelling spans across thousands of document-topic
pairs is infeasible, so the field has moved to language models as span annotators, usually with
a single reading per pair and no reproducibility check.

That single reading is the unexamined step. This paper measures what it hides.

Frame the target explicitly: full-length scientific articles, where the evidence is usually not
in the abstract, and a topic's support is spread over several documents.

### 2. Related work

From `../bibliography.md`, integrated from the surveys in `../bib-inbox/`. Four threads, and the
section's job is to concede generously and then locate the gap precisely.

**Evidence-span disagreement is known.** Lead with it rather than burying it. WiCE, FEVER and
Natural Questions all measured it, all concluded the field must live with multiple valid sets,
and all built scoring rules around that concession. Quote FEVER's own admission that completeness
"is not feasible" and WiCE's reason for refusing the union. A reviewer who knows this literature
must see that we know it too, in the first paragraph.

**What was never measured.** No reliability coefficient for span selection; no model of union
growth against effort; no within-model test-retest; no manipulation of the anchor unit. Support
each with the survey's "searched and did not find" evidence, not with an assertion.

**LLM judges for relevance** — the document-level binary literature, so that our *whether* result
reads as confirmation of theirs and the *where* result as the new part.

**The chunking motivation**, briefly: passage-level gold exists because document-level metrics
answer a different question. Do not re-argue Paper B here.

### 3. Design and what was frozen

The instrument, and the discipline around it. What was pre-registered before any data was seen:
the rubric, the prompt, the gates and their thresholds, the presentation seeding, the
predictions in `artifacts/r31ext/PREDICTIONS.md`. Temperature zero throughout, so the variation
measured is not sampling temperature.

The constraints that bounded the design are results in their own right and belong here, not in
a limitations paragraph: the topic supply, the endpoint concurrency, the shared-host politeness
limits, the session budget that capped one judge's run.

Two protocol revisions are part of the argument rather than embarrassments, and the paper shows
both: index-primary localisation, then ten-word quote anchors, then whole-sentence anchors. Each
was changed for a measured reason and the measurement is reported.

### 4. Data

3,738 document-topic pairs on the confirmation population, 308 on the development population,
across TREC CDS topics over PMC Open Access full text. Judges: two open-weight models served
locally, three proprietary models from a third family. Readings per pair and per judge, and the
totals.

Point at `../../plans/results/RUN-PACKAGE.md` for the reproducibility detail rather than
restating it.

### 5. Results

Each subsection cites claim ids from `claims.json`; no bare numbers.

**5.1 Whether is easy, where is not.** The same model, the same pair, twice: agreement on the
binary verdict against agreement on the span set. This is the paper's hook.

**5.2 Quote anchoring hallucinates, and whole sentences fix it.** Hallucinated-span rates before
and after the anchor change, for both local judges and all three of the third family. Report the
failure mode that motivated it: the closing anchor. This is a clean, transferable protocol
result that others can adopt directly.

**5.3 What converges, and what does not.** This is the section a reviewer will attack, so it
must be precise about granularity. Three objects, one data set:

- The family of distinct span *sets*, merged at Jaccard ≥ 0.5, **does** saturate:
  `r31ext-scout-set-union-marginal-gain` (0.0274 at twenty readings) and
  `r31ext-qwen-set-union-marginal-gain` (0.0461 at ten), both under the 0.05 bar, after failing
  it at five readings (`r31-scout-union-not-saturating-at-5`).
- The set of distinct *sentences* ever marked does **not**:
  `r31ext-pooled-sentence-union-still-growing` puts thirty pooled readings at 0.7816 of the
  fitted asymptote and still gaining.
- Pooling readings into a union does **not** produce a stable span set either:
  `r31-scout-union-as-labeler-unstable`, 0.4708.

The pre-registered fit is the asset. `prereg-p-ext-2-fit-parameters` was committed before the
first new label and predicted its own primary would fail and said why. The mechanical scoring
passed for the wrong reason, on one blow-up in 6,160 readings (`prereg-p-ext-2-scored`,
`locator-blowup-dev`). On the honest reading the primary fails as predicted **and** the
secondary lands at a relative error of 0.0024 — a five-point curve extrapolated four-fold to
within a quarter of a percent (`prereg-p-ext-2-excluding-blowups`). Report both readings and
say which is which.

Do not quote the degenerate unfiltered fits: `r31ext-scout-sentence-fit-degenerate-unfiltered`
and `r31ext-pooled-fit-degenerate-unfiltered` have no identifiable asymptote, and the artifacts
say so.

**Pre-empt the metric-artefact objection, in this section, before a reviewer raises it.**
HotpotQA's Table 8 shows a whether/where gap that lives almost entirely in *exact set match* —
supporting-fact EM 61.50 against F1 90.04. So a reader will ask whether our 0.47-against-0.92
contrast is just set equality being a harsh measure. It is not, and our own data settles it:
under partial credit the raw mean pairwise span-union Jaccard is **0.4350** for Scout
(`r31-scout-partial-credit-jaccard`) and **0.5430** for Qwen
(`r31-qwen-partial-credit-jaccard`), against graded-support reliability of **0.9205** on the same
readings. The contrast survives partial credit. Report Qwen's median of 0.7518 beside its mean,
because that distribution is bimodal and the mean alone misrepresents it.

**5.4 Graded support does converge — and report the curve, never the endpoint.** The constructive
half, and the section most exposed to a methodological objection. `wong-2022` gives the
established name for what we are computing (**k-rater reliability**, ICC(k), equivalent to
Spearman-Brown) and a precedent of the same shape (ICC(1) 0.590 to ICC(13) 0.950). Which invites
the obvious attack: a high coefficient at thirty readings is arithmetically implied by
Spearman-Brown from any moderate single-reading value, so 0.9205 on its own says nothing.

**Answer it with the curve, which we have** (`graded-support-reliability-at-4` through `-at-30`). Inverting
Spearman-Brown at each k, the implied single-reading reliability is not constant — it rises from
0.1929 at ten readings to 0.2785 at thirty. If the readings were exchangeable it would be flat.
Because it rises, the observed curve beats every projection from an earlier anchor: +0.0429 from
k = 10, +0.0320 from k = 14, +0.0193 from k = 20. Pooling buys more than the prophecy formula
predicts, and that is the finding rather than the endpoint.

Two honesties belong in the same paragraph. The **single-reading reliability is low**, around
0.19 to 0.28, and saying so is what makes the thirty-reading number meaningful. And the small-k
points are **attenuated by halves with no variance** — 47 such pairs at k = 4 against 1 at k = 30
(`graded-support-degenerate-halves-at-4`) — so k ≥ 10 is the honest range; the 44 % rise survives
there, where the degeneracy difference is two pairs against one out of about 306.

Reliability against readings: `graded-support-reliability-at-10` (0.7050), `-at-20` (0.8588),
`-at-30` (0.9205), against the 0.90 bar the pre-registration had written for the span gate. Ten readings do not suffice and
twenty do not either, which is worth stating because it is the cost of the method.
`graded-support-reliability-sensitivities` shows the result does not rest on estimator choices,
and `prereg-p-ext-1-scored` shows the bet was registered below the projection and above what ten
readings bought, so it was a genuine bet in both directions.

**5.5 It is not one family's artefact.** Three models from a separate family, one reading each,
land on sentences carrying several times the pooled support of an average touched sentence.
Report this as agreement between models, never as truth.

**5.6 At scale, and a defect the development set could not surface.**
`span-filter-dev-counts` against `span-filter-conf-counts`: Qwen filtered **zero** of 3,080
development readings and **nine** of 37,380 at scale, and those nine carry more dropped spans
(1,653) than Scout's 1,050 filtered records do (1,441). Scout's rate is the control that says
the filter did not drift: 1.33 % to 1.40 %. `span-filter-instrument-unchanged` (Δ −0.0013)
proves the instrument is the same on both sets, which is what makes the comparison legitimate.

The general lesson is the transferable one: a defect invisible at development scale and
load-bearing at confirmation scale, caught because the fix was written as a post-filter applied
identically to both sets rather than as a change to the locator between them.

### 6. Threats to validity

Lead with the big one in the paper's own voice: the graded score is reliable and its validity is
unestablished, because the two-reader human read has not been performed. State what would falsify
the construction.

**The competing explanation, and it is the reviewer's strongest card.** `xu-2023-rave` and
`jakobsen-2023` frame span variation as legitimate perspectival difference rather than
measurement instability — and `jakobsen-2023` finds systematic *demographic* structure in which
span an annotator picks. That reading is available for every inter-annotator result in §2. Our
answer has to be explicit: one model, one document, one prompt, temperature zero, repeated. There
is no second perspective for the variation to belong to. Say it plainly, and concede that this
argument does not transfer to the human-annotator literature.

**Model tier changes the quote-fidelity result.** `zhang-2026-clinical-verbatim` reports frontier
models attaching verbatim quotes to over 90 % of claims from prompting alone. Our own data agrees:
the third-family judges hallucinate at or below 0.002 while the local judges were far worse before
the anchor change. So the anchor finding must state the model tier, the anchor length and the
match rule every time it is quoted, and must not be generalised to frontier models.

**Temperature zero does not mean deterministic, and we owe the reader an account.**
`schroeder-2024-trust` reports ω = 1.0 at temperature 0 *by construction*, and
`shi-2025-position-bias` reports repetition stability of 0.96–1.00 for frontier judges on
pairwise tasks. We run at temperature zero and do not get reproduction, so the paper must say
where the variation comes from — presentation order is seeded and varied by design, and batching
and non-deterministic kernels remain. If we cannot attribute it, a reviewer will call it a
pipeline artefact.

**The human floor, and the one number that frames the whole paper.** `scholer-2013` ran a
prospective test-retest with the between-assessor comparison in the same experiment:
self-agreement 51.62 % against between-participant 44.80 %. A person agrees with themselves only
about seven points better than with a stranger. `soboroff-2003-novelty` gives two NIST assessors
at sentence level an F of 0.58 relevant and 0.46 novel; `lee-sun-2019-pico` gives medical experts
exact-boundary F1 of 0.357–0.576. Our 0.38–0.56 sits inside that band, which is the honest
framing: a machine re-reading the same document is about as unstable as a person is, and roughly
as unstable as two different people.

**The published counter-result, and it is in our domain.** `hofstatter-2020-fira` measured
location agreement separately on TREC data and found **no gap**: word-selection κ "ranging from
0.5 to 0.8", the same band as 2-class relevance (0.5–0.8), with only the harder 4-class grading
lower (0.3–0.6). That is not the opposite ordering — it is presence and location agreeing
*equally*, which is still a result against our headline. Blunting factors that must be stated
rather than used to dismiss it: κ is each student's annotation against a majority aggregate that
includes that student, n = 10 query–document pairs, non-expert (student) annotators, and only
already-relevant documents were annotated. It is a real published result and the paper engages
it directly. (Verified against the CIKM 2020 text, §5, on 2026-09-22 — see `bibliography.md`.)

**Statistic choice is a finding, not a detail.** Report mean pairwise F1 for the span condition
(`hripcsak-2005`), not κ — negatives are uncountable when the unit is a span in a long document —
and not token-κ, which `mathet-2017` §3.4.1 shows is the discretising workaround that inflates
agreement. Say why in the paper; a reviewer will otherwise ask for κ.

**Spearman-Brown assumes what may not hold.** `yang-2026-judge-changes` finds repeated-sample
juries add little when errors are correlated. Report the observed pairwise correlation between
readings next to any Spearman-Brown projection, so the extrapolation is auditable.

Then: the two local judges' graded scores correlate weakly with each other even though the pooled
score is reliable, which is a real tension and is reported rather than smoothed. The judges'
abstract bias, measured directly by the human-read draw finding only three deep-evidence pairs in
the whole development set. One judge's run truncated by a session budget, with its reduced n
stated everywhere it appears. Temperature zero means the variation is the model's, not sampling,
which narrows what the instability can be attributed to.

### 7. Reproducibility

The run package, the commit pin, the seeds, the served model ids, the prompt and rubric hashes,
what is released and what cannot be. An honest statement of what a different site must supply,
including that the third-family judges were reached through a CLI on a personal account rather
than an API, and what that means for exact reproduction.

---

## Working rules for this draft

- Every number cites a claim id from `claims.json` (79 entries, all verified). Run
  `../check_claims.py` before any commit that touches the draft.
- Read the `note` field before writing the sentence. Several claims carry a caveat that changes
  what may be said: differing `n`, a key name in `gates-r31ext.json` that says `k5` but holds the
  full-depth value, Fable's reduced n and non-comparable baseline, and the Claude judges having
  no ten-word-anchor "before".
- No status, no dates, no roadmap; those live in the lab book.
- Absent is not passed. Where a statistic was not measured, the word is *absent*.
- Powered nulls and unresolved contrasts are different things and are never merged.
- The human read is pending, and every sentence that depends on validity says so.
