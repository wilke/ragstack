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

From `../bibliography.md`. Two threads: LLM-as-judge for relevance, and the chunking and
segmentation literature that motivates passage-level gold. The honest framing for the second
thread is that no published comparison controls realised chunk size, and that the negatives and
positives differ by whether segmentation is informed or naive rather than by whether it is
"semantic".

State plainly what is already known about LLM judges agreeing on binary relevance, so that the
*whether* result reads as confirmation and the *where* result as new.

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

**5.4 Graded support does converge.** The constructive half. Reliability against readings:
`graded-support-reliability-at-10` (0.7050), `-at-20` (0.8588), `-at-30` (0.9205), against the
0.90 bar the pre-registration had written for the span gate. Ten readings do not suffice and
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
