# PLAN — Verification budget: forecasting how many readings an evidence check needs (FY27 LDRD)

Status: **proposal plan**, 2026-10-20. MyLDRD record: LDRD Number 2027-0323. Pre-proposal due 2026-10-21 (FY27 LDRD Prime, focus area
AI for Science, call "Agents for Science and Scalable Cognition", v.2026.10.07). Nothing in this
plan has been run. Numbers under §2 resolve to `docs/papers/paper-a-evidence-localisation/claims.json`
(claim ids in brackets) and will be recomputed with current code after submission
(Paper A corrections list). This file is the long form of the one-page pre-proposal; the one-pager
must stay consistent with it.

## 1. Question and hypotheses

Science agents that read the literature check, at every step, whether a document supports a claim.
Each check is one stochastic LLM reading, and agents run it once. The call's priority question 2
asks for measures of "the difficulty of verifying a solution" and for P(success | budget). We
measure both for evidence checking, and use them for the decision the agent must take at every step:
stop, re-read, switch model or ask a human (call question 3; questions 6 and 7 for validity and
correlated errors).

| # | Hypothesis | Measurement | Owners |
|---|---|---|---|
| H1 | Cheap features, recorded before any reading, predict each item's reliability at every budget (equivalently, the readings it needs) | §3: per-item curve, scored for calibration, ranking and accuracy against deep recorded readings | Graziani, Wilke |
| H2 | Reliability at that budget tracks expert correctness (**month-8 renewal milestone**; passes if Spearman ρ ≥ 0.5 between reliability and expert agreement across budgets) | Two expert readers + adjudicator on 150–200 items, stratified random draw | Davis, Graziani, readers |
| H3 | A cascade with deferral (stop / re-read / switch model / ask a human) beats published baselines at matched cost | Replay of recorded readings; cost–accuracy frontier | Graziani, Wilke |
| H4 | A second model family is worth its measured error correlation | Effective independent readers per family; shared errors with a calibrated decision model (Jev) | Wilke, Graziani |

Uncertainty-quantification framing: repeated readings measure the model's uncertainty; the expert
read tests whether that uncertainty is calibrated. Repeated with each model generation, the test set
separates aleatoric difficulty (a property of the evidence) from epistemic difficulty (a property of
the model).

## 2. What is in hand

308 TREC CDS query–document pairs, read 20 times by Llama-4-Scout and 10 times by Qwen3.6, once by
three frontier models.

- *Whether* a document holds evidence: self-agreement 0.96–0.99 [`r3-scout-whether-agreement`,
  `r31ext-qwen-whether-agreement-curve`].
- *Where* the evidence is: span self-consistency 0.38–0.56 [`r31-scout-span-self-consistency`,
  `r31-qwen-span-self-consistency`].
- Pre-registered forecast "graded-support reliability ≥ 0.85 at thirty readings": PASS, observed
  0.9205 [`prereg-p-ext-1-scored`, `graded-support-reliability-at-30`]. This forecast was for the
  pooled instrument, not per item; per-item forecasting is new.
- The two local judges' support maps correlate at mean per-pair r 0.2976
  [`judge-judge-graded-support-correlation`]: reliability alone does not establish correctness.
- Scale run: 112,140 readings over 3,738 further pairs [`confirmation-total-readings`], not yet
  analysed; the training pool for the H1 forecaster.
- Frontier cost: USD 0.13–0.30 per pair per reading [`claude-cost-per-pair`].

## 3. The forecast and how it is measured

**Unit.** One question–document pair (item). Each reading returns a whether verdict and a set of
evidence sentences.

**Observed target (needs no expert).** Each item gets a deep pile of K readings (K = 30 from open
models on local GPUs; fewer from frontier models). For every budget k:

1. draw two disjoint groups of k readings;
2. form each group's consensus set (sentences picked by at least half of its readings);
3. score the agreement of the two consensus sets (overlap, with equivalent sentences grouped, §4.2);
4. average over draws → **R_i(k)**, the item's reliability curve.

**n_i\*** = smallest k with R_i(k) ≥ target (0.85, fixed before data). Items not reaching the target
within K are right-censored ("> K"), never imputed. n_i\* carries a bootstrap interval; scoring
accounts for that noise (the same attenuation issue as the noise-corrected cross-judge r on the
Paper A corrections list).

**Forecast.** A model fitted on the existing 308 + 3,738 pairs predicts, per new item, a curve
R̂_i(k) with an interval and n̂_i, from pre-read features only (§4). Forecasts are hashed and
registered before the new items are read.

**Scoring.**

| Test | Question | Measure |
|---|---|---|
| Calibration | Stated 80 % reliable at k = 5 → right 80 % of the time? | Reliability diagram, Brier score |
| Ranking | Are the hard items identified? | Rank correlation n̂_i vs n_i\* |
| Accuracy | How far off in readings? | Ratio n̂_i / n_i\*; 80 % interval coverage; censoring-aware |
| Beats the simple answer | Better than one budget for all? | Same scores for the population curve and a whether/where-only rule |
| Extrapolation | Unobserved depths? | Fit at k ≤ 10, predict 30 |
| Pays for itself (H3) | Does forecast-driven allocation save readings? | Total readings to bring all items to target, forecast vs uniform, matched cost |

**Stability, then correctness.** R_i(k) is P(answer settled | k). The call's success is a correct
answer: on the expert items, estimate P(expert agrees | k) per regime, weighted by the inclusion
probabilities of the stratified draw. If the two curves track, the stability forecast is a usable
proxy for P(success | budget); if not, that is the reported negative result (H2).

## 4. What the forecast depends on

readings needed = f(question, document, what the reader is shown).

### 4.1 Question complexity

| Feature | Expectation | Status |
|---|---|---|
| Whether vs where | Where is far harder | Measured (§2); strongest feature |
| Number of facets (what, how much, where, when) | More facets → more readings | Hypothesis |
| Set-valued vs single answer | Sets harder to agree on | Hypothesis, consistent with §2 |
| Question length | Weak proxy for facets; record, don't rely on it | Hypothesis |

In the chunking study, query construction difficulty moved first-hit retrieval quality by −0.43 to
−0.48, about 15× any breadth effect (`PLAN-information-density.md`): a retrieval result, but a
reason to expect the question term to dominate.

### 4.2 Information density (three properties, `PLAN-information-density.md`)

| Property | Expected effect on readings needed | Pre-read feature |
|---|---|---|
| Text density (facts per sentence) | Readings pick different facts in dense methods/results | Propositional density (CPIDR), content-word ratio |
| Redundancy (same evidence stated several times) | Readings split over equivalent sentences: lower agreement without error | Candidate-sentence count; **score with equivalent sentences grouped** so redundancy is not counted as difficulty |
| Context dependency (sentences that cannot stand alone) | Evidence spans two sentences; readings disagree on which | **Back-reference share** (rule-based opener list, fixed before data; `PLAN-information-density.md` §4.4) |

None is tested yet; on TREC CDS they are descriptive only. Year 1 records them; it does not test
them as hypotheses.

### 4.3 Retrieval (RAG)

Year 1 gives the reader the whole document, which keeps retrieval out of the measurement. A deployed
agent reads retrieved chunks instead, and that changes three things:

1. **Retrieval sets the candidate set**: fewer candidates, fewer readings — items can look easy.
2. **Retrieval can drop the evidence** (within-document retrieval failure, measured as reach and
   containment in the chunking study). Readings then agree on "no support" or on the wrong sentence:
   **stable but wrong**. A stability forecast says "stop early"; only the expert read catches it.
   This is the strongest argument for H2 as the renewal milestone.
3. **Chunking cuts context**: a back-referencing sentence loses its antecedent and invites false
   support, so context dependency matters more under RAG.

The budget becomes two-dimensional: **delivered context** (tokens shown per reading, cf. `EM @ l
tokens`) and **number of readings**. P(success | budget) becomes a surface; the useful question is
the cheapest mix at matched cost. Year 2 adds delivered context (full document vs retrieved chunks at
two or three sizes) as a design factor in the live loop. Year 1 records the retrieval score as a
feature.

## 5. Design by year

The project runs **two years** (MyLDRD: FY2027–2028). H2 is the month-8 renewal milestone; a negative result ends the project and is reported.

**Year 1.**
1. Build the viral/bacterial metagenomics and wastewater-surveillance question set with the expert
   readers (Davis, Owens, Wilton); answers sit in methods and results — the hard *where* case.
2. Fit the forecaster on the existing pairs; register per-item forecasts (hashed) before reading.
3. Collect readings: open models on local GPUs (deep piles), frontier models through Argo, scaled
   out on ALCF through AmSC. Every reading logs tokens, GPU-hours, dollars and a response id; fresh,
   uncached calls; frontier readings repeated across days to catch drift.
4. Expert read: 150–200 items, stratified random draw fixed in advance, inclusion probabilities
   recorded and used as weights (preferential-sampling guard); two independent readers, Weisenhorn
   adjudicates disagreements only.
5. Test H1 and H2 (Wilke, Graziani, second researcher).
6. Replay: stop / re-read / switch / defer policy against fixed-k ensembling, Adaptive-Consistency,
   Trust-or-Escalate and fewest-readings-first at matched cost; Jev as a fast first reader, tested
   not relied on, with an open-model control in the same slot and its error correlation with the
   LLM readers measured (H3, H4).
7. Renewal milestone at month 8: H2 passes if Spearman ρ ≥ 0.5 between reliability and expert agreement across budgets.

**Year 2.** Live agent verification loop with retrieval (delivered context as a
second budget, §4.3); transfer of the forecasts, unchanged, to a new model generation and a second
domain; release of the test set, forecasts and code to ModCon.

## 6. Milestones

| Milestone | Date | Criterion |
|---|---|---|
| Forecasts registered, question set built | [early 2027] | Hashed before reading; set signed off by the readers |
| Renewal milestone | month 8 | Expert read done; forecasts scored; H2 decided (ρ ≥ 0.5) |
| Year 1 final | 2027-09-30 | H1 and H2 answered; policy evaluated in replay; paper submitted |
| Year 2 final | 2028-09-30 | Live policy with retrieval; transfer to a new model generation and a second domain |

## 7. Team and resources

Andreas Wilke (DSL) lead, 50 %; Carlo Graziani (MCS) co-PI, statistics — forecast validation and
calibration (H1–H3), 0.05 FTE (agreed 2026-10-20); James Davis (DSL) co-PI, domain question set and
expert read, 0.05 FTE; a staff researcher (to be identified), 30 %, kept flexible so effort can shift between people by period; expert readers Sarah Owens, Rosemarie Wilton
(BIO), 100 h each, adjudicator Pamela Weisenhorn (BIO), 40 h. A work computer (USD 15K); USD 10K travel per year; frontier
readings through Argo about USD 2K per month (USD 24K per year). Open models run on an ALCF allocation to be requested, and on AmSC as it becomes available, with existing group GPUs for development; no equipment is requested. Budget from the division estimate (CELS divisional rate 30% on labor, materials factor 5%, DOE administration charge 3%): FY27 USD 389,899, FY28 about USD 373,878 at FY27 rates, total about USD 763.8K; the pre-proposal carries the quarterly budget table.

## 8. Risks

| Risk | Mitigation |
|---|---|
| Reliability does not predict correctness | It is the month-8 renewal milestone; the negative result ends the project and is reported |
| Redundant evidence read as difficulty | Equivalent sentences grouped before scoring (§4.2) |
| Stable but wrong under retrieval | Expert read; delivered context as a year-2 factor (§4.3) |
| Expert items chosen by difficulty | Stratified random draw, inclusion probabilities as weights |
| Noisy observed target | Bootstrap intervals; censoring-aware, noise-aware scoring |
| Frontier API drift | Repeat across days; pin open models locally |
| Prior work (adaptive labelling, cascades) | Named and used as baselines; novelty is set-valued evidence, pre-registered per-item forecasts, expert gold standard |
| Overlap with EAIRA / IDeA | EAIRA reads each item once and leaves judge reliability open; IDeA as a consumer |
| Proprietary decision model | Tested, not relied on; open-model control; vendor terms bar training on outputs |

## 9. Candidate second testbed (not in scope until confirmed)

OpenChia (Chia; Episode workflows with a reactive stop rule) runs the same "read until stable" loop
on physics-paper reproduction and records every call and decision. Its ~40 recorded runs with
repeated readings could serve as a dry run of §3's scoring code on a non-biological domain, with no
new model calls. Its funding status and Chia's participation are unconfirmed; it is not named in the
pre-proposal and must not be claimed as LDRD scope. Candidate year-2 second domain for the full
proposal.

## 10. Open items

| ID | Item | Owner |
|---|---|---|
| LP-1 | Reliability target (0.85) fixed before data; H2 pass criterion set: Spearman ρ ≥ 0.5 (Graziani to confirm the exact statistic) | Wilke, Graziani |
| LP-2 | Equivalent-sentence grouping rule for scoring | Wilke |
| LP-3 | Back-reference opener list and other density features, committed before reading (`PLAN-information-density.md` ID-4) | Wilke |
| LP-4 | AmSC access path; request the ALCF allocation; choose the open models | Wilke |
| LP-5 | Done: division estimate received 2026-10-20 (FY27 only); confirm FY28 escalation and the MCS rate for Graziani | division budget rep |
| LP-6 | OpenChia as second testbed: Chia's agreement and funding status | Wilke, Chia |
| LP-7 | Resolved: two-year project (MyLDRD FY2027–2028) with a month-8 renewal milestone | — |

## Sources

- Call text: FY27 LDRD Prime, AI for Science, v.2026.10.07.
- `docs/papers/paper-a-evidence-localisation/claims.json` (preliminary numbers).
- `docs/plans/results/design/PLAN-information-density.md` (density, dependency, retrieval terms).
- `docs/papers/bib-inbox/06`–`13` (literature: inference scaling, judge reliability, adaptive budget,
  science agents, crowdsourcing, information density, UQ and decision models, preferential sampling).
