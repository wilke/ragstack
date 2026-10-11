# Paper A — outline

**Working title.** Reliable but not canonical: LLM judges for passage-level evidence
localisation.

**Target.** arXiv preprint, venue-neutral. Chosen because the result is complete and does not
wait on the chunking verdict.

**Audience.** Anyone building passage-level or span-level relevance gold with a language
model, which is now most people building retrieval evaluations over long documents. The result
is not specific to chunking, to biomedicine, or to our retrieval stack.

**The one-sentence claim.** A language model asked to locate evidence inside a document agrees with
itself about *whether* the document contains evidence and disagrees about *where*; within twenty to
thirty readings neither the union of evidence sets nor the union of evidence sentences converges, but a
graded per-sentence support score built from the readings becomes reliable with depth — k-rater
reliability 0.9205 pooled across two families at thirty readings, and, in an exploratory per-judge
reading, 0.9142 for one judge at twenty — which makes it a reproducible, though not a validated, gold
object.

**The thesis in two numbers, with their depths stated.** Graded per-sentence support reaches k-rater
reliability 0.9205 at thirty pooled readings (`graded-support-reliability-at-30`); in an exploratory
per-judge reading, Scout alone reaches 0.9142 at twenty (`judge-scout-support-reliability-curve`). Span
agreement read one reading against one reading is 0.3831 / 0.5617 (`r31-scout-span-self-consistency`,
`r31-qwen-span-self-consistency`). These are not the same depth: at five or six readings the union
split-half (0.4708 Scout, 0.5779 Qwen; `r31-scout-union-as-labeler-unstable`,
`r31-qwen-union-as-labeler-unstable`) and the graded score (0.4738 at k = 6,
`graded-support-reliability-at-6`) are no different, and a deep union split-half was never measured.
The finding is that graded support is the object whose reliability grows with depth to a usable,
measurable level — not that it beats the union at equal depth.

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
nobody has reported **a repeated-measures reliability coefficient** for evidence-span selection at
all. We do both: neither the union of sets nor the union of sentences converges within thirty
readings, and a graded per-sentence score is the object whose reliability can be measured and
grows with depth to a usable level.

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

`DRAFT.md` §5 is the authoritative text; every number there cites a claim id. The skeleton, with the
point each subsection must make:

**5.1 Whether is easy, where is not.** Whether-agreement passes the gate; one-reading-against-one
span self-consistency fails it. Like-for-like on the 31 pairs; base-rate caveat on Qwen's whether;
cross-judge location agreement, exploratory.

**5.2 Quote anchoring hallucinates, and whole sentences fix it.** First-reading and all-presentation
rates; the closing-anchor failure mode; the third family has no ten-word "before".

**5.3 Neither union converges within thirty readings.** Report marginal gain *and* fitted fraction of
the asymptote for both the set union and the sentence union; by the first they are indistinguishable,
by the second neither is near its asymptote. Union split-half is a five-reading figure; compare it
only with the matched-depth graded value (k = 6); the deep union split-half is absent. The
pre-registered fit scored mechanically and on the exploratory blow-up-excluded reading. Partial credit,
exploratory.

**5.4 Graded support becomes reliable with depth.** Pooled curve; per-judge curves, exploratory; the
inverted single-reading reliability shows the pooled shape is judge composition, and within Scout the
readings behave as exchangeable from k = 14 to 20. Degenerate halves; sensitivities; the
pre-registered bet.

**5.5 Third-family judges land on supported sentences.** Point-biserial r and Spearman ρ,
exploratory; the lift comparison, exploratory, with Fable's different baseline; agreement between
single readings of different models is not agreement with truth.

**5.6 At scale, and a defect the development set could not surface.** The locator post-filter applied
identically to both sets; Qwen's zero in development against nine at scale; the instrument unchanged.

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

- Every number cites a claim id from `claims.json` (105 entries, all verified). Run
  `../check_claims.py` before any commit that touches the draft.
- Read the `note` field before writing the sentence. Several claims carry a caveat that changes
  what may be said: differing `n`, a key name in `gates-r31ext.json` that says `k5` but holds the
  full-depth value, Fable's reduced n and non-comparable baseline, and the Claude judges having
  no ten-word-anchor "before".
- No status, no dates, no roadmap; those live in the lab book.
- Absent is not passed. Where a statistic was not measured, the word is *absent*.
- Powered nulls and unresolved contrasts are different things and are never merged.
- The human read is pending, and every sentence that depends on validity says so.
