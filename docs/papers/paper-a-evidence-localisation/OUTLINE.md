# Paper A — outline

**Working title.** Reliable but not canonical: LLM judges for passage-level evidence
localisation.

**Target.** arXiv preprint, venue-neutral. Chosen because the result is complete and does not
wait on the chunking verdict.

**Audience.** Anyone building passage-level or span-level relevance gold with a language
model, which is now most people building retrieval evaluations over long documents. The result
is not specific to chunking, to biomedicine, or to our retrieval stack.

**The one-sentence claim.** A language model asked to locate evidence inside a document agrees
with itself about *whether* the document contains evidence and disagrees about *where*; the
set of locations it marks does not converge with more readings; but a graded per-sentence
support score pooled over many readings does reproduce itself, clears a usable reliability bar,
and is not one model family's artefact.

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

**5.3 The location set does not converge.** Union size against readings, the fitted saturation
curve, the observed union at twenty readings and the fraction of the asymptote, scored against
the prediction written before the run. The pre-registered prediction is the asset here; report
that the mechanical pass was an artefact of a single locator blow-up and that the cleaned
reading failed as predicted.

**5.4 Graded support does converge.** Spearman-Brown reliability against readings, the bar, and
where it is crossed. This is the constructive half of the paper: the stable object is not a span
set, it is a per-sentence score.

**5.5 It is not one family's artefact.** Three models from a separate family, one reading each,
land on sentences carrying several times the pooled support of an average touched sentence.
Report this as agreement between models, never as truth.

**5.6 At scale, and a defect the development set could not surface.** The method applied to the
full confirmation population, and the cross-unit locator defect that fired nine times on one
judge there against zero times on the development set. The general lesson: a post-filter applied
identically to both sets is what let this be found and fixed without changing the instrument
between them.

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

- Every number cites a claim id. Run `check_claims.py` before any commit that touches the draft.
- No status, no dates, no roadmap; those live in the lab book.
- Absent is not passed. Where a statistic was not measured, the word is *absent*.
- Powered nulls and unresolved contrasts are different things and are never merged.
- The human read is pending, and every sentence that depends on validity says so.
