# Bib inbox 07 — LLM-judge reliability as a function of repetition and composition

> **Provenance.** Produced 2026-10-12 for the FY27 LDRD pre-proposal ("the pre-proposal" / "the draft" below; it is not in this repo). Metadata was verified as stated per entry (the status tag on each says how). arXiv API and OpenAlex HTTP 429 rate limits left the queries listed in the tooling note below and in *Searched and did not find* unrun; they are coverage gaps, not negative results. Three full-text details that are not in the abstracts were re-checked against the arXiv HTML full text on 2026-10-12 and confirmed: `anand-2026`'s 2.17× and 99.8% caching, `sunkavalli-2026`'s "replication sharpens a biased number", and `kohli-2026`'s φ̄ ≈ 0.39 (reported as 0.391).

Written for the LDRD pre-proposal. Covers H1 (Spearman–Brown in the number of
readings), H3 (mixing model families), H4 (stop/escalate), and reliability ≠ validity.
Compiled 2026-10-12.

**This file extends `docs/papers/bib-inbox/01-llm-as-judge-relevance.md` §7 and
`02-annotator-disagreement-reliability.md` §7a; it does not repeat them.** It re-uses these
entries without re-describing them: `haldar-2025-rating-roulette`, `schroeder-2024-trust`,
`norman-2026-reliability-without-validity`, `reiss-2023`, `barrie-2024-pss`,
`shi-2025-position-bias`, `shyr-2025-repeatability`, `krishna-2024-radiology`,
`nowak-2025-self-consistency`, `yang-2026-judge-changes`, `wong-2022` (k-rater reliability),
`messing-2026`, `atil-2024`, `he-2025`, `song-2024`, `stureborg-2024`.

**Status key.** `verified` = title, authors, date and venue (or arXiv id) confirmed on the arXiv abs
page, Crossref, or the publisher's page. `read` = full text extracted (pymupdf) and the methods and
limitations sections checked. `abstract` = verified metadata and abstract only. No entry here rests
on recall.

**Tooling note (coverage limit, state it if a reviewer asks).** The OpenAlex daily credit budget was
exhausted (HTTP 429, `retry-after` ≈ 7.6 h), and `export.arxiv.org/api` returned
429 throughout. Searches therefore used the arXiv search UI (`arxiv.org/search`), arXiv abs pages,
Crossref, Europe PMC and the OSF API. OpenAlex was used once, successfully, for the Xue DOI record
before the quota ran out. Forward-citation chasing through OpenAlex was not possible. I chased
citations by hand from the reference lists of `anand-2026`, `zhu-2026-stopping` and `frohn-2026`.

**Field per entry**, as the brief requires:
(a) Does it vary the number of judge readings, and model reliability against that number?
(b) Does it predict reliability at a depth it did not observe?
(c) Does it compose families, and do the errors correlate?
(d) Does it check validity against humans or experts?
(e) What must our proposal cite or answer?

---

## Headline

1. **xue-2026 does not contradict us.** It is the *whether* regime. It scores 1–2-sentence student
   answers on 0–2/0–3 rubrics with a bare score as output. Each response is scored five times, at
   T = 0 and at T = 1. Our *whether* agreement (0.92–0.98) is the same "almost perfect" result. Our
   ≈0.34 is for a different unit, which they never measure: graded per-sentence support, located
   inside long documents. Their other two findings support the proposal. Intra-LLM consistency is
   not associated with accuracy, while inter-LLM consistency is. That is our reliability ≠ validity
   premise plus the case for H3. Details and a sentence for the proposal are in §0.
2. **Generalizability theory has already been applied to LLM judges, five times in 2025–26.**
   Inbox 01's "No paper was found applying generalizability theory with rater facets to LLM
   judges" is **now false and must be corrected**. See `song-2025-gtheory`, `anand-2026`,
   `sunkavalli-2026`, `zatuchin-2026`, and `messing-2026` (already in inbox 02). Each runs a
   G-study and a D-study. **None of the ones we read scores a D-study projection at a depth that
   was not observed.** That gap is H1's, so the proposal's novelty claim has to move there.
3. **The one-parameter law is not new algebra.** Spearman–Brown, ICC(k), the Kish design effect
   DE = 1 + (N−1)ρ, and the beta-binomial intraclass ρ are all the same function.
   `zhang-martinez-2025` uses it on repeated LLM readings. They state that varying N to test it
   "is not possible with a single N", and list it as future work. **That is H1, in a stranger's
   own words.**
4. **H3 has a mixed prior record, which makes it a real hypothesis.**
   - `kohli-2026`: 9 judges from 7 families give ≈2 effective votes, and the best single judge
     matches the panel.
   - `kim-2025-correlated`: more capable models have more correlated errors.
   - `goel-2025-capa`: the same, measured with CAPA.
   - `xue-2026`: cross-family majority voting *improves* accuracy.
   - `verga-2024-poll`: a three-family panel beats a single GPT-4 judge.
   - `anand-2026`: judges disagree 2.17× more across families than within them, yet mixing does
     not reduce the number of judges needed.
5. **H4 has direct precedents, and the proposal's baselines are too weak without them.**
   Cascaded Selective Evaluation (`jung-2024-trust-escalate`) already does confidence-driven
   escalation to a stronger judge with a human-agreement guarantee. Adaptive-Consistency and
   Early-Stopping Self-Consistency already stop sampling per item. "A fixed number of readings" is
   a straw baseline once these exist.

---

## 0. ★★ `xue-2026-scoring-consistency`: the counterclaim, read as far as was possible

**On the Consistency of Automatic Scoring with Large Language Models.** Mingfeng Xue, Xingyao Xiao,
Yunting Liu, Mark Wilson. *Educational and Psychological Measurement* 86(5): 861–882 (issue October
2026; online first 16 Feb 2026). doi:10.1177/00131644261418138 · PMID 41710058 · PMCID PMC12909151.

**Status: verified (OpenAlex DOI record, the SAGE abstract page, the PMC landing page), with the
design read from the authors' own code. Full text NOT read.** Here is what blocked the full text:
- SAGE: restricted access; the PDF and ePub return 403.
- PMC: the page states *"This article has a delayed release (embargo) and will be available in PMC
  on February 16, 2027."*
- Europe PMC: subscription only.
- No arXiv or EdArXiv preprint exists (searched by title, by author, and by the abstract phrases
  "intra-LLM" and "inter-LLM consistency").

The design was recovered from the authors' open-practices repository, **OSF `4zhux` ("Multiple LLM
Rating")**, which SAGE's page links to as *"Codes and data"*. I downloaded and read all six files:
three scoring notebooks, the item info, the scoring prompts, and a response sheet. A second,
independent account of the results comes from `frohn-2026` (below), which summarises Xue et al. in
its literature review.

**What it scores.**
- Constructed responses to science-education items. Five items appear in the OSF item file, for
  example *"Where do the plants on the mountain get water to survive?"* and *"Compare the climate at
  the top of the mountain to the bottom of the mountain."*
- Rubrics have 3–4 categories (0–2 or 0–3).
- The responses are one to two sentences. In the released sheet they include *"to get water"*,
  *"so they get water supply"* and *"muchas"*.
- The abstract adds the ASAP open-source data.
- The notebook outputs show ≈454–470 scored responses for item I01.
- The released response file is named "synthetic student responses.xlsx" and holds 50 rows for
  one item. Whether the paper's science-item analysis used real or synthetic responses cannot be
  settled without the full text.

**Models, from the code.**
- `gpt-4o-2024-11-20`
- `deepseek-chat`, which `frohn-2026` identifies as DeepSeek-V3
- `qwen-plus`
- `gemini-2.0-flash`
- `claude-3-7-sonnet-20250219`

**Readings and temperature, from the code.**
- `temprs = [0,1]` and `nrep=5`: five repeated scorings per response at each of T = 0 and T = 1.
- The system prompt is *"You are a helpful rater in science education. You should rate students'
  responses based on the scoring guide and only return the scores."*
- So there is no reasoning, and the output is one integer. The first integer is parsed, and the
  majority (mode) is taken across the five readings.
- Claude was run through the Message Batches API, with max_tokens 1024.

**Metric.**
- *Accuracy* in the notebooks is QWK, accuracy, RMSE and SMD of the five-reading mode against the
  human `Final` score.
- The *consistency* statistic is **not computed in the released code** and is not stated in the
  abstract. The reference list includes Fleiss (1971), Gwet's *Intrarater reliability* (2008) and
  his handbook, Shrout & Fleiss (1979) ICC, and Landis & Koch (1977). So a Fleiss-κ or Gwet-type
  agreement coefficient interpreted on the Landis–Koch scale is likely, which is where "almost
  perfect" comes from (κ > 0.80). **Unconfirmed; do not state the metric as fact.**

**Findings, from the abstract, verbatim in substance.**
- (a) Intra-LLM consistency was "almost perfect … regardless of temperature".
- (b) Inter-LLM consistency was moderate, and higher on easier items.
- (c) Intra-LLM consistency exceeded inter-LLM consistency.
- (d) Intra-LLM consistency was not associated with accuracy, while inter-LLM consistency was
  strongly associated with it.
- (e) Majority voting across LLMs improved accuracy.

`frohn-2026` adds a second-hand detail: intra-model majority voting (j = 5) gave *"less than 1%
improvement in quadratic weighted kappa"* over a single call, which was not significant. Ensembling
the five models helped, *"but accuracy remained modest (average QWK ≤ 0.60)"*.

**Answers to (a)–(e).**
- (a) Five readings at two temperatures. It reports consistency at k = 5 but **does not model
  reliability as a function of k**.
- (b) No.
- (c) Yes, five families. Errors are not modelled explicitly, but inter-LLM agreement is only
  moderate while voting helps, which implies partly uncorrelated errors.
- (d) Yes, against the human `Final` scores.
- (e) Cite it, scope it, and use its findings (c)–(e) in our favour (see the verdict).

**Does it contradict our finding? Frank verdict: no, and it is closer to corroboration than to
contradiction.** It differs from our ≈0.34 on every axis that predicts high self-agreement:

| Axis | Xue et al. 2026 | Our graded-support / *where* measurement |
|---|---|---|
| Unit judged | one whole response | each sentence of a long document |
| Input length | 1–2 sentences | full biomedical documents |
| Output | one integer, 3–4 categories, no reasoning | graded support per sentence, plus a location |
| Statistic | (probably) chance-corrected agreement on a categorical label | reliability of a graded per-sentence score; set overlap for *where* |
| Comparable quantity of ours | *whether* agreement **0.92–0.98** | single-reading reliability **0.33–0.35**; *where* 0.38–0.56 |

Their result is the same "almost perfect" number we get for *whether*. It is the regime that
`frohn-2026` (Fleiss κ 0.886–0.969), `shi-2025-position-bias` (repetition stability 0.89–1.00),
`sunkavalli-2026` (φ(1) = .90–1.00 at T = 0) and `tiwari-2026` (92–97% exact self-consistency) also
find: short items, few categories, one label per item.

The variance a single reading leaves unresolved lives in the many-choice, long-input, localisation
part of the task. That part has no counterpart in their design. Our own data show the same
contrast inside one study: 0.92–0.98 for *whether*, 0.38–0.56 for *where*.

**The parts of xue-2026 that help us:**
- **Finding (d)** is reliability ≠ validity, measured: self-consistency did not track accuracy,
  cross-model agreement did. It is a third independent statement of our framing, after
  `norman-2026-reliability-without-validity` and `shyr-2025-repeatability`.
- **Findings (c) and (e)** are the premise of H3: within-model agreement is an upper bound on
  cross-model agreement, and mixing families improved accuracy where repeating one model did not
  (per `frohn-2026`).

**What a reviewer could still push on.**
- "Your ≈0.34 is low because your prompt, parser or span locator is noisy, not because the task
  is." The proposal already records one locator malfunction in 6,160 readings. It needs a
  sentence saying the reliability being modelled is that of the *whole reading pipeline*,
  including the locator.
- `anand-2026`'s infrastructure warning (see its entry) is the converse risk for *any* study that
  reports near-perfect consistency: a caching gateway makes judges look perfectly self-consistent.
  We have no evidence that Xue et al. were affected, and the proposal should not suggest it.

**Sentence for the proposal**, replacing "[xue-2026, to be read and engaged before submission]":

> Xue et al. (2026) report near-perfect intra-model consistency when five model families re-score
> one-to-two-sentence student answers on three- or four-point rubrics; that matches our
> *whether*-level agreement (0.92–0.98), and the low single-reading reliability we model (≈0.34)
> belongs to a unit their design does not measure — graded, per-sentence support located inside
> long documents. Their other two findings are this project's premises: intra-model consistency
> did not track accuracy, while cross-model agreement did and cross-model voting improved it.

The short form, if space is tight: *"Near-perfect self-consistency on short whole-response scoring
(Xue et al., 2026) is our whether regime; our low reliability is localisation in long documents,
which they do not measure — and they too find that self-consistency does not predict accuracy."*

**Correction needed in inbox 01's entry.** It says "several temperatures". The code has exactly
two, T ∈ {0, 1}. Add the model versions, the five repeats, and the score-only output.

---

## 1. Reliability against the number of readings (H1)

### `yagubyan-2026` ★ The Coin Flip Judge? Reliability and Bias in LLM-as-a-Judge Evaluation
Abel Yagubyan. arXiv 2606.13685 (dated 23 Apr 2026 on the abs page).
<https://arxiv.org/abs/2606.13685> · **verified, read** (methods, §5.7–5.9, limitations)

GPT-4o-mini and GPT-4.1-mini, 29 questions, **50 pairwise and 50 pointwise trials per question**,
default T = 1.0, with a T = 0 ablation.
- **Reliability curve:** P(majority(K) = majority(50)) against K, by Monte Carlo. A single trial
  gives 86.6% "consensus fidelity". Reaching 90% takes ≈3 trials and 95% takes 11.
- **Stratified:** easy questions (flip rate < 10%) need 1 trial for 90% and 3 for 95%. Hard ones
  need 15 for 90% and **> 50 for 95%**.
- At T = 0, flip rates fall by 79% for GPT-4o-mini and 43% for GPT-4.1-mini but do not reach zero.
  One question still flips at 50%.
- Limitations, own words: only two judges *"from the same provider (OpenAI)"*; 29 questions.

(a) **Yes**, to K = 50, empirically. (b) **No.** The curve is resampled from observed trials and no
law is fitted or projected. (c) No: one provider. (d) No: the reference is the judge's own
50-trial majority.
(e) **Cite it as the nearest empirical reliability-vs-K curve for an LLM judge, and as evidence
for H2.** Its per-item stratification is the "per-item budget" phenomenon. It also challenges a
pooled H1: a population curve hides items that need more than 50 readings. The proposal must say
whether H1's ±0.05 is a population claim or a per-item one.

### `zhang-martinez-2025` ★★ Quantifying and Correcting Measurement Error in LLM-Generated Classifications: A Beta-Binomial Latent Class Model for Correlated Repeated Judgments
Yichi Zhang, Ignacio Martinez. arXiv 2510.23874 (v1 27 Oct 2025; revised and retitled, *"supersedes
earlier versions"*). <https://arxiv.org/abs/2510.23874> · **verified, read** (model, data, §6.1–6.2)

Seven open-weight judges from three families (1B–32B), VerifyBench and VerifyBench-Hard, 250 items,
**N = 10 ratings per item at T = 0.7**, three prompt conditions, 52,500 judgments, with human labels.
- They define the intraclass correlation ρ of repeated readings, the design effect
  **DE = 1 + (N−1)ρ**, and N_eff = N/DE.
- ICC is 0.50–0.93 on the balanced split, so **ten readings carry 1.07–1.81 independent
  readings' worth of information**.
- A binomial latent class model, which assumes conditionally independent readings, covered the
  human-label false-acceptance rate in *none of 42* settings.

**Gap quote, from their future work:** *"Collecting ratings at several values of N would test the
exchangeability assumption of Equation (4) directly, which is not possible with a single N."*

(a) **No.** N is fixed at 10, which is exactly what they flag. (b) No. (c) Three families, but each
is modelled separately and composition is not studied. (d) **Yes**: human labels give error rates
and coverage.
(e) **Must cite, twice.**
- **For H1's functional form.** Their DE is Spearman–Brown in another notation. The proposal
  should say H1 *is* this one-parameter exchangeable model and that **our contribution is to test
  it across depths and predict out of sample**, which they could not do.
- **For reliability ≠ validity.** Their ρ is conditional on the true label. Correlated readings
  mean pooling converges on the model's own systematic answer, not the truth.

Note the regime too. Their binary verdicts have ρ = 0.50–0.93. Our graded support has ρ₁ ≈ 0.34.
Repetition pays in our regime and barely pays in theirs. That is the xue-2026 contrast again.

### `sunkavalli-2026` ★ LLM Judges as Raters: A Pre-Registered Audit of Severity, Halo, Reliability, and Version Instability in LLM Essay Scoring on Public Corpora
Veerendra Kumar Sunkavalli. arXiv 2608.29517 (30 Aug 2026); *"Under review at the Journal of
Learning Analytics (LAK27)"*. <https://arxiv.org/abs/2608.29517> · **verified, read** (design,
§3.2, D-study results)

Twelve judges from four providers. ENEM/Essay-BR and ASAP, 2,377 essays. **K = 3 replications per
cell at T = 0.7**, plus a T = 0 anchor cell. Many-facet Rasch, then a G-study and D-study per judge:
**φ(k), with k\* = replications needed to reach φ ≥ .80.**
- φ(k) ≥ .80 at k = 1 for nine judges and at k = 2 for the other three.
- At T = 0 the cheap tier reaches φ(1) = .90–1.00: *"replication was never the bottleneck."*
- Key-findings box: *"Averaging repeated calls makes a judge's score stable but not more accurate:
  replication sharpens a biased number. Budget replications for stability and human-anchored
  calibration for validity."*

(a) **Yes, via a D-study.** (b) **Formally yes:** a D-study projects φ(k) to k beyond the K = 3
observed. **But the projection is not scored against a deeper observed k.** (c) Four providers,
modelled as judges, not composed. (d) **Yes:** accuracy against trained humans; replication *"not
human-level accuracy"*.
(e) **Cite it as the closest precedent for H1's machinery** (a D-study over replications of one
LLM judge, pre-registered) **and for reliability ≠ validity.** It is also the strongest
"repetition doesn't matter" counter-voice: in whole-essay scoring, replication was never the
bottleneck. We answer it as for xue-2026: a different unit.

### `frohn-2026` The Impact of LLM Self-Consistency and Reasoning Effort on Automated Scoring Accuracy and Cost
Scott Frohn. arXiv 2604.26954 (3 Apr 2026); presented at NCME 2026 (a meeting, not proceedings).
<https://arxiv.org/abs/2604.26954> · **verified, read** (background, R1)

900 high-school mathematics conversations scored against human ground truth; OpenAI and Google
models. **Ensemble size j = 1, 3, 5, 7 at T = 1**, compared with a single call at T = 0.
- Intra-model Fleiss κ is 0.886–0.969 at every j.
- Increasing j *"produced no significant gains"*, while temperature sampling beat deterministic
  calls.
- The author reads this as *"individual calls are too consistent to benefit meaningfully from
  aggregation."*
- It is also the independent secondary source for xue-2026's results (§0).

(a) **Yes, j to 7**, for accuracy rather than reliability. (b) No. (c) Two providers, compared
rather than mixed. (d) **Yes.**
(e) Cite it with xue-2026 as the whole-response "repetition doesn't pay" result, and as the
corroborating summary of Xue.

### `kumar-2026-verdicts` All Verdicts are Not Equal: Rethinking LLM Judge Reliability
Vineet Kumar, Darshita Rathore, Anindya Moitra. arXiv 2610.12083 (8 Oct 2026); *"Accepted at AACL
IJCNLP (Main) 2026"*. <https://arxiv.org/abs/2610.12083> · **verified, partly read** (abstract,
setup, limitations)

Six frontier judges, four benchmarks, five prompt formats, two orders, **three temperatures (0 for all six
judges; 0.5 and 1.0 for four, since reasoning models' APIs do not take a temperature), ten
repetitions per condition.** Verdicts change across identical replications at
T = 0. *"The most deterministic judge achieves perfect consistency by trivially repeating
incorrect verdicts, agreeing with ground truth only 51% of the time."* It also argues that
*"reliability is item-specific rather than model-level."*

(a) Ten repetitions, but no curve against k. (b) No. (c) No composition. (d) **Yes.**
(e) Cite it for item-specific reliability (supports H2) and for the perfectly-consistent-but-wrong
judge (reliability ≠ validity).

### `lau-2026` Same Input, Different Scores: A Multi Model Study on the Inconsistency of LLM Judge
Fiona Lau. arXiv 2603.04417 (4 Feb 2026). <https://arxiv.org/abs/2603.04417> · **verified, skimmed**

Five judges (GPT-4o, GPT-4o-mini, Gemini-2.5-Flash, Claude-Haiku-4.5, Claude-Sonnet-4.5), enterprise
RAG question–answer pairs, **ten runs at T = 0 and T = 1**. Completeness scores fluctuate most.
Lower temperature helps GPT-4o and Gemini but has *"limited or inconsistent effects for Anthropic
models"*.

(a) Ten runs, no curve. (b) No. (c) Cross-model differences described. (d) **No**: human
comparison is listed as future work.
(e) Optional. Note only that RAG-answer scoring at T = 0 is not stable, which is closer to our
setting than essay scoring. Weight: weak (single author, internal data).

### `chen-2024-more-calls` ★ Are More LLM Calls All You Need? Towards the Scaling Properties of Compound AI Systems
Lingjiao Chen, Jared Quincy Davis, Boris Hanin, Peter Bailis, Ion Stoica, Matei Zaharia, James Zou.
NeurIPS 2024 (Crossref: *Advances in Neural Information Processing Systems 37*,
doi:10.52202/079017-1455); arXiv 2403.02419. The arXiv title is "…Towards Scaling Laws of Compound
Inference Systems". **verified (Crossref + arXiv)**, abstract and model section read.

Vote and Filter-Vote accuracy as a function of the number of LM calls is **non-monotone**: it rises
on easy queries and falls on hard ones. They fit an analytical scaling model from a small number of
samples and **predict the accuracy-optimal number of calls.**

(a) **Yes**, for task accuracy, not judge reliability. (b) **Yes:** the published prediction from a
small sample to unobserved depth for the *task* side. (c) No. (d) Against ground truth.
(e) **Must cite in Novelty**, as the closest "predict performance vs number of samples" work. Its
non-monotonicity is also a **direct challenge to equating more readings with better verdicts.**
Under Spearman–Brown, reliability rises monotonically with k. Accuracy of the pooled verdict can
fall on hard items. That is reliability ≠ validity as a function of budget, and H4's policy must
allow it.

---

## 2. Generalizability theory and D-studies applied to LLM raters (H1's established machinery)

### `song-2025-gtheory` Exploring LLM Autoscoring Reliability in Large-Scale Writing Assessments Using Generalizability Theory
Dan Song, Won-Chan Lee, Hong Jiao. arXiv 2507.19980 (26 Jul 2025). <https://arxiv.org/abs/2507.19980> ·
**verified, read** (design, D-study, limitations)

AP Chinese essays: 30 students, 120 essays. Two humans and **seven AI raters** (ChatGPT-3.5, 4.0,
4o, o1, Gemini 1.5 and 2.0, Claude 3.5 Sonnet), collected over ≈18 months. Multivariate G-theory,
p × r × t, with D-studies for n′_r = 1–4 raters. Human raters were more reliable. **Composites of
human and AI raters improved reliability.** Limitations: a small sample, no fine-tuning, one
exam.

(a) Raters are *different models*, one reading each. There are no repeated readings of one model.
(b) A D-study projects to n′_r, but it is not tested out of sample. (c) It mixes humans and LLMs,
which is a composition, and reports the reliability gain. (d) Against humans as raters.
(e) Cite it as the first G-theory application to LLM raters, and as a precedent for H3-style
composition (human + AI composites).

### `anand-2026` ★★ Ask Which, Not How Good: Sizing Benchmarks Scored by an LLM
Atul Anand. arXiv 2609.27787 (17 Aug 2026). <https://arxiv.org/abs/2609.27787> · **verified, read**
(claims, infrastructure, limitations, conclusion)

The design is s × i × j × r (system, item, judge, replicate), 373,019 judgments. Its structural
result: under a single judge, generalizability is capped at σ²_s/(σ²_s + σ²_sj) whatever the item
count. *"Items saturate; judges do not."* Other findings:
- Two judges at 30 items are worth 77 items at one judge (Eρ² = 0.75).
- **"Judges disagree 2.17× more across model families than within them … so panel size overstates
  panel independence, though mixing families does not reduce the judges required."**
- LLM judges are *"more self-consistent than human experts on a matched design."*
- **Infrastructure warning, verbatim:** *"The inference gateway performed response caching: 99.8%
  of replicate cells initially returned a byte-identical cached response … Any judge-reliability
  study run through a caching gateway will conclude judges are perfectly self-consistent."*
- Limitations: the closed panel spans only two families, and *"Temperature is not separated."*

(a) Replicates are a facet (3 per cell), but the paper is about judges and items, not depth of
readings. (b) D-study projections, bootstrapped, not scored at held-out depth. (c) **Yes: family
is a facet, and the family effect is measured.** (d) A SummEval human comparison.
(e) **Must cite**, for three things:
- The G-theory framing with its σ²_pj ceiling, which is H3's composition model in closed form (see
  Established names).
- The family result that bears directly on H3.
- The caching warning. Our methods should state that readings bypass response caches and log a
  response id.

### `zatuchin-2026` Where Does the Noise Come From? A Variance-Components Decomposition of Non-Determinism in LLM Brand Answers
Dmitrij Żatuchin. arXiv 2607.13304 (14 Jul 2026). <https://arxiv.org/abs/2607.13304> · **verified,
skimmed**

A crossed random-effects G-study over within-prompt resampling, paraphrase, model and language,
with **a decision-study allocation that returns how many repeats, paraphrases, models and languages
to buy for a target reliability.** 12,933 responses; three models (GPT-5.2, Gemini 3 Flash,
Perplexity). Resampling is 34.8% of the variance once a cell term isolates it.

(a) Repeats are a facet (≈5). (b) The D-study allocation projects; there is no held-out-depth test.
(c) Model is a facet. (d) No: the outcome is sentiment polarity, with no gold.
(e) Cite it as an explicit D-study budgeting of repeats vs models. It is the nearest precedent for
"how many readings vs which models", and it sits in a non-scientific domain. Weight: weak–moderate
(single author, brand-marketing application).

### `camuffo-2025` Variance-Aware LLM Annotation for Strategy Research: Sources, Diagnostics, and a Protocol for Reliable Measurement
Arnaldo Camuffo, Alfonso Gambardella, Saeid Kazemi, Jakub Malachowski, Abhinav Pandey. arXiv 2601.02370
(dated 2 Dec 2025). <https://arxiv.org/abs/2601.02370> · **verified, abstract**

A G-theory-grounded protocol *"specifying sampling budgets, aggregation rules, and reporting
standards"* for LLM annotation. It warns that annotation errors correlated with covariates bias
downstream estimates.

(a)–(c) Framework only, as far as the abstract shows. (d) Not stated.
(e) Mention it as a further G-theory-for-LLM-annotation precedent, to pre-empt "this exists".

### `choi-2026-irt` Diagnosing the Reliability of LLM-as-a-Judge via Item Response Theory
Junhyuk Choi, Sohhyung Park, Chanhee Cho, Hyeonchu Park, Bugeun Kim. arXiv 2602.00521 (31 Jan 2026);
*"Accepted ICML 2026"*. <https://arxiv.org/abs/2602.00521> · **verified, abstract**

The IRT Graded Response Model for judges, with two axes: intrinsic consistency (under prompt
variation) and human alignment.
(a) No (prompt variation, not repetition). (b) No. (c) No. (d) Yes.
(e) Mention it as the IRT alternative to G-theory. H2's per-item difficulty features are closer to
IRT item parameters, so a reviewer may ask why not IRT.

---

## 3. Composition across families and correlated errors (H3)

### `kohli-2026` ★★ Nine Judges, Two Effective Votes: Correlated Errors Undermine LLM Evaluation Panels
Guneet Kohli. arXiv 2605.29800 (28 May 2026). <https://arxiv.org/abs/2605.29800> · **verified, read**
(setup, §4.6 robustness, limitations)

Nine frontier judges from **seven families**, all at **T = 0**, one reading each. Three ChaosNLI
datasets with 100 human labels per item, plus RewardBench.
- The Kish design-effect **n_eff ≈ 2.0–2.5**, with mean pairwise error correlation φ̄ ≈ 0.39.
- The panel falls 8–22 pp below the Condorcet independent-voting prediction.
- **The best single judge matches or beats the full panel.**
- Aggregation methods close at most 11% of the gap.
- Robust across a reframed prompt, reversed labels, chain-of-thought (which *raises* correlation,
  n_eff 1.94) and T = 0.5.
- Limitations: classification tasks only; *"inter-judge correlation may differ on open-ended
  generation"*; a snapshot of current models.

(a) Panel size, not same-model depth. (b) The Condorcet null predicts panel accuracy *assuming
independence*. That is a prediction, but of the ideal, not of the observed curve. (c) **Yes, and
yes, strongly.** (d) **Yes**, against 100-annotator labels.
(e) **The paper H3 must answer.** It predicts H3's failure mode at frontier scale. Our H3 says
mixing helps "only when errors are uncorrelated". Kohli says the errors are correlated. H3 should
therefore pre-register what it expects for our families and our *localisation* task, which differs
from the classification tasks Kohli flags as a boundary. **Note that its n_eff is Spearman–Brown
algebra again** (see Established names).

### `kim-2025-correlated` ★ Correlated Errors in Large Language Models
Elliot Kim, Avi Garg, Kenny Peng, Nikhil Garg. ICML 2025 (per the arXiv comment *"Accepted to ICML
2025"*); arXiv 2506.07962. <https://arxiv.org/abs/2506.07962> · **verified (arXiv), read**
(abstract, §4 LLM-as-judge case study, limitations)

More than 350 LLMs. *"On one leaderboard dataset, models agree 60% of the time when both models
err."* Shared architecture and provider drive correlation, but *"larger and more accurate models
have highly correlated errors, even with distinct architectures and providers."* In the
LLM-as-judge case study, *"each judge systematically inflates"* the accuracy of models similar to
itself. They recommend calibrating error metrics per model–judge pair against ground truth.

(a) No. (b) No. (c) **Yes**: the canonical correlated-errors paper. (d) Ground-truth leaderboards.
(e) **Must cite for H3.** Choosing "different families" is not enough. The proposal should measure
error correlation directly; family is a proxy.

### `goel-2025-capa` Great Models Think Alike and this Undermines AI Oversight
Shashwat Goel, Joschka Struber, Ilze Amanda Auzina, Karuna K Chandra, Ponnurangam Kumaraguru, Douwe
Kiela, Ameya Prabhu, Matthias Bethge, Jonas Geiping. arXiv 2502.04313 (6 Feb 2025).
<https://arxiv.org/abs/2502.04313> · **verified (arXiv); venue not confirmed** · abstract

CAPA (Chance Adjusted Probabilistic Agreement) measures similarity from overlapping mistakes. Judges
favour similar models, and *"model mistakes are becoming more similar with increasing
capabilities."*
(a)/(b) No. (c) **Yes.** (d) Ground-truth benchmarks.
(e) Cite it with `kim-2025-correlated`. CAPA is a ready-made metric for "errors are uncorrelated"
in H3.

### `verga-2024-poll` ★ Replacing Judges with Juries: Evaluating LLM Generations with a Panel of Diverse Models
Pat Verga, Sebastian Hofstätter, Sophia Althammer, Yixuan Su, Aleksandra Piktus, Arkady
Arkhangorodsky, Minjie Xu, Naomi White, Patrick Lewis. arXiv 2404.18796 (29 Apr 2024).
<https://arxiv.org/abs/2404.18796> · **verified (arXiv; no peer-reviewed venue found)**, read
(setup, κ tables)

PoLL is three smaller judges from **disjoint families** (Command R, Claude 3 Haiku, GPT-3.5), one
reading each. Across six datasets it beats a single GPT-4 judge on Cohen's κ against humans, with
less intra-model bias, at over 7× lower cost.
(a) Panel of three vs one, not a curve. (b) No. (c) **Composes families**, but error correlation is
not measured. (d) **Yes.**
(e) **Must cite** as the origin of the "jury" idea, and as the positive prior for H3. Note that it
did not measure what H3 says determines the result.

### `li-2026-32judges` How Many Humans Are 32 LLM Judges Worth?
Chao Li, Yingying Yu, Yunfeng Li. arXiv 2609.21277 (18 Sep 2026). <https://arxiv.org/abs/2609.21277> ·
**verified, abstract + skim**

A 32-judge panel, T = 0, ChaosNLI. The **human-equivalent panel size** is only ≈2.3–6.5 depending on
the matching criterion, and a binary-error diagnostic credits the panel with ≈2 effective votes.
They also **extrapolate the panel curve to an asymptote**: 32 judges already reach 94–96% of it.
(a) Panel size, with a fitted asymptote. (b) **Yes**, extrapolation in panel size. (c) **Yes.**
(d) Against human label distributions.
(e) Cite it with `kohli-2026`. It is a second, independent "few effective judges" result, and an
extrapolation-in-panel-size precedent (not in same-model depth).

### `awuni-2026-family` Who Judges Matters: Measuring Family-Conditioned Preference in LLM-as-Judge Panels
David Ababio Awuni, Luke E. K. Achenie, Benjamin Tei Partey, Elvis Gyasi Owusu, Nii-Nai Derrick
Sowah. arXiv 2609.17857 (15 Sep 2026). <https://arxiv.org/abs/2609.17857> · **verified, abstract**

Four open families, 9,312 pairwise judgments. Same-family lift is 3.4–8.4 pp. *"Panel composition
changes 18.5% of pairwise outcomes."*
(e) Mention it: composition changes verdicts, so H3's pooled curve is composition-dependent.

### `balasubramanian-2026-ising` Dependence-Aware Label Aggregation for LLM-as-a-Judge via Ising Models
Krishnakumar Balasubramanian, Aleksandr Podkopaev, Shiva Prasad Kasiviswanathan. arXiv 2601.22336
(29 Jan 2026). <https://arxiv.org/abs/2601.22336> · **verified, abstract**

Dawid–Skene and majority vote assume conditional independence, which LLM judges violate. Ising and
latent-factor models give correlation-adjusted aggregation. CI-based methods *"remain strictly
suboptimal as the number of judges grows."*
(e) Cite it as the aggregation-side answer to correlated judges. H3's "composition model" should
say whether it is a reliability model (G-theory) or an aggregation model (this paper).

---

## 4. Adaptive and sequential numbers of judgments (H4)

### `jung-2024-trust-escalate` ★★ Trust or Escalate: LLM Judges with Provable Guarantees for Human Agreement
Jaehun Jung, Faeze Brahman, Yejin Choi. arXiv 2407.18370 (25 Jul 2024).
<https://arxiv.org/abs/2407.18370> · **verified (arXiv)**; cited as Jung et al. 2025 by
`zhu-2026-stopping`, which suggests ICLR 2025, but **that venue was not confirmed**. Read: abstract,
method, ablations.

Selective evaluation trusts a judge only when its confidence clears a calibrated threshold, which
gives a provable human-agreement level. **Cascaded Selective Evaluation** escalates from cheap to
strong judges only when necessary. *"Simulated Annotators"* builds confidence from agreement among
K = N ≤ 5 simulated annotator prompts, with ablations over N.

(a) Ablates N simulated annotators, not readings. (b) No. (c) A cascade across models. (d) **Yes**,
a guarantee against human agreement.
(e) **Must cite, and it should be a baseline for H4.** It is "read again, switch to a stronger
model, or stop" with a guarantee. H4's distinct contribution is the *per-item budget prediction
before reading*, the human-escalation arm, and localisation. Say so explicitly.

### `zhu-2026-stopping` Stopping and Routing LLM Judge Panels
Bin Zhu, Yi Xie, Yanghui Rao. arXiv 2608.19802 (20 Aug 2026); *"Accepted at WISE 2026"*.
<https://arxiv.org/abs/2608.19802> · **verified, read** (§2, related work, discussion)

Panel design as role-conditioned allocation, fitted from a labelled audit set. Judges are copies
(no conditional information), complements, or specialists. The policy drops copies, adds
complements, routes specialists by slice, and *"stop[s] when validation gain falls below a
threshold."* *"A second prompt from the same model family can become a copy."* The comparison set
includes reliability juries and frugal cascades. Routing is slice-level, not per-item sequential.

(e) Cite it with `jung-2024-trust-escalate` as stop/route precedent. Its "copies" concept is H3's
correlated readings. Its companion, `zhu-2026-fcps`, is listed below.

### `zhu-2026-fcps` A Finite-Calibration Regime Map for LLM Judge Panels
Bin Zhu, Yi Xie, Yanghui Rao. arXiv 2606.01034 (31 May 2026); *"Accepted at WISE 2026"*. **verified,
abstract.** It selects panel size and aggregator under finite human labels.
(e) Optional companion citation.

### `aggarwal-2023-adaptive` Let's Sample Step by Step: Adaptive-Consistency for Efficient Reasoning and Coding with LLMs
Pranjal Aggarwal, Aman Madaan, Yiming Yang, Mausam. EMNLP 2023 (arXiv comment *"Published at EMNLP
2023"*); arXiv 2305.11860. **verified (arXiv), abstract**

A lightweight per-question stopping criterion on the agreement among samples so far. It cuts the
sample budget by up to 7.9× with < 0.1% accuracy loss.
(e) **Cite it, and add it as an H4 baseline.** It is the generic per-item adaptive-k rule for task
answers. H4 must beat it, or show why it fails for verification (it stops on self-agreement, which
is reliability, not validity).

### `li-2024-esc` Escape Sky-high Cost: Early-stopping Self-Consistency for Multi-step Reasoning
Yiwei Li, Peiwen Yuan, Shaoxiong Feng, Boyuan Pan, Xinglin Wang, Bin Sun, Heda Wang, Kan Li. ICLR 2024
(arXiv comment); arXiv 2401.10480. **verified (arXiv), abstract**

Window-based early stopping of self-consistency sampling, which saves 33.8–84.2% of samples.
(e) Cite it with `aggarwal-2023-adaptive`.

### `park-2025-rautoeval` Adaptive Prediction-Powered AutoEval with Reliability and Efficiency Guarantees
Sangwoo Park, Matteo Zecchin, Osvaldo Simeone. NeurIPS 2025 spotlight (arXiv comment); arXiv 2505.18659.
**verified (arXiv), abstract**

Adaptively tunes how much to rely on LLM-judge labels against human labels, with finite-sample
guarantees.
(e) Mention it for the "ask a human" arm of H4. It is the PPI line, alongside `boyeau-2024-autoeval`
(arXiv 2403.07008, verified, abstract: *"increase the effective human-labeled sample size by up
to 50%"*).

### `kim-jp-2026-howmany` Augmenting Human Evaluation with LLM Judges: How Many Human Reviews Do You Need?
Jane Paik Kim. arXiv 2605.16354 (8 May 2026). **verified, abstract**

A two-stage design: LLM ratings for all items, human ratings for a subsample, and a doubly robust
estimator. Sample sizes are chosen for power, with more human ratings allocated where prediction is
weak.
(e) Mention it as a design-based precedent for budgeting human escalation in H4.

---

## 5. Validity against humans or experts, in biomedical and scientific evidence assessment

### `lai-2024-rob` ★ Assessing the Risk of Bias in Randomized Clinical Trials With Large Language Models
Honghao Lai, Long Ge, Mingyao Sun, Bei Pan, Jiajie Huang, Liangying Hou, … Janne Estill. *JAMA Network
Open* 2024; doi:10.1001/jamanetworkopen.2024.12687. **verified (Crossref + Europe PMC), abstract**

ChatGPT and Claude assess Cochrane-style risk of bias on 30 RCTs. **Each RCT was assessed twice by
each model**, against three experts as the criterion standard.
- Correct rates: 84.5% and 89.5%.
- **Test–retest consistency: 84.0% and 87.3%**, with κ > 0.80 in 7–8 domains.
- Some domains score F1 < 0.50.

(a) Two readings. (b) No. (c) Two families, compared. (d) **Yes, experts.**
(e) **Cite it as the biomedical evidence-appraisal precedent.** It is document-level, and test–retest
there is already below "almost perfect" (≈85%). That is a domain data point between xue-2026 and
us.

### `wu-2025-sourcecheckup` ★ An automated framework for assessing how well LLMs cite relevant medical references
Kevin Wu, Eric Wu, Kevin Wei, Angela Zhang, Allison Casasola, Teresa Nguyen, Sith Riantawan, Patricia
Shi, Daniel Ho, James Zou. *Nature Communications* 2025; doi:10.1038/s41467-025-58551-6 · arXiv
2402.02008. **verified (Crossref + Europe PMC); full text retrieved, agreement passage read**

Statement–source support in medical answers, 58,000 statement–source pairs. The LLM judge reaches
*"89% agreement with a consensus of three US-licensed medical experts and a higher rate of
agreement than any pair of medical experts."*
(a) No repetition reported. (b) No. (c) No. (d) **Yes, physicians.**
(e) **Cite it as the closest task to ours** (does this source support this statement?, in medicine)
with expert validity. It is pair-level, not sentence localisation within long documents, and it
reports no self-consistency.

### `rouillard-2026` Can LLMs Accurately Score Medical Diagnoses and Clinical Reasoning?
Amy Rouillard, … Shabir A. Madhi, … Bruce A. Bassett. arXiv 2604.14892 (16 Apr 2026); *"Extended version
of a paper that appeared in Artificial Intelligence in Medicine 2026"*. **verified (arXiv), abstract +
skim**

An **LLM Jury of three frontier models** scores 3,334 diagnoses on 300 LMIC hospital cases against an
expert panel and an independent re-scoring panel. Uncalibrated scores preserve the ordinal ranking
but are systematically lower. Isotonic calibration gives excellent agreement. No self-preference
was found.
(c) A jury of three, averaged. (d) **Yes, clinician panels.**
(e) Cite it as the medical jury-with-expert-validation precedent.

### `delucia-2026` Same Verdict, Different Reasons: LLM-as-a-Judge and Clinician Disagreement on Medical Chatbot Completeness
Alexandra DeLucia, Heyuan Huang, Sonal Joshi, Mahsa Yarmohammadi, Ahmed Hassoon, Mark Dredze. arXiv
2604.16383 (26 Mar 2026). **verified, abstract**

Detecting incomplete medical answers: LLM judges discriminate *"at and slightly above near chance
(AUC 0.49–0.66)"* against clinicians, and *"rarely cite the same explanation"* even when they
agree.
(d) **Yes.** (e) **Cite it as the strongest biomedical validity warning.** It is also a "right
verdict, wrong reason" result, the *whether*-vs-*where* split from the human side.

### `tiwari-2026` When Consistency Does Not Mean Reliability: Evaluating Local LLM Judges Against Human Ratings
Aakash Kumar Tiwari. arXiv 2609.13824 (12 Sep 2026). **verified, abstract**

Two local judges (Llama-3-8B, Qwen2.5-7B), three readings each, nine human raters per item.
Exact self-consistency is 97.3% and 92.3%, but Pearson r with humans is only 0.275 and 0.340.
(e) A small, weak study, but a clean statement that self-consistency ≠ human agreement. Cite it
only if space allows.

### `dorner-2024-limits` Limits to scalable evaluation at the frontier: LLM as Judge won't beat twice the data
Florian E. Dorner, Vivian Y. Nastl, Moritz Hardt. ICLR 2025 (arXiv comment); arXiv 2410.13341. **verified
(arXiv), abstract**

When the judge is no more accurate than the evaluated model, no debiasing can cut the ground-truth
labels needed by more than half.
(e) Cite it for the validity ceiling: P(correct | budget) is bounded by judge accuracy whatever k
is. That is the formal version of "reliability does not imply validity".

---

## CHALLENGES OUR CLAIMS

Ordered by how much damage each could do. Each has a frank verdict.

1. **"The one-parameter law" is known algebra (Spearman–Brown = ICC(k) = Kish DE = beta-binomial
   ρ).** Sources: `zhang-martinez-2025`, `kohli-2026`, `wong-2022`, `sunkavalli-2026`.
   *Verdict: real, and fatal to any novelty claim built on the law itself; harmless to the
   proposal if the claim is moved.* None of these varies same-model depth and **scores a
   projection at an unobserved depth**. Zhang & Martinez name that as impossible with their
   design. The proposal's novelty sentence should read: *a pre-registered, out-of-sample test of
   the D-study projection in readings, per item, for evidence localisation*. It should not read:
   "a law".

2. **Generalizability theory for LLM judges exists.** Sources: `song-2025-gtheory`, `anand-2026`,
   `sunkavalli-2026`, `zatuchin-2026`, `messing-2026`, `camuffo-2025`.
   *Verdict: real; it corrects inbox 01.* "No study we found predicts the reliability of the
   verification step itself as a function of budget" survives only in the narrow form above. In
   the broad form, Sunkavalli's φ(k), Żatuchin's allocation and Messing's "design-study
   projections" all predict reliability as a function of budget. The proposal must cite them and
   say what is different: (i) prediction scored at a held-out depth and on held-out items;
   (ii) per item rather than per judge or benchmark; (iii) localisation inside long documents;
   (iv) linkage to expert accuracy.

3. **Repetition barely pays for whole-item scoring.** Sources: `xue-2026`, `frohn-2026`,
   `sunkavalli-2026` (*"replication was never the bottleneck"*), `zhang-martinez-2025` (10
   readings ≈ 1.1–1.8 independent ones).
   *Verdict: a scope condition, not a refutation.* It holds at *whether* level, and we observe it
   too (0.92–0.98). The proposal should state the regime boundary as a finding: repetition is
   worth buying where the single-reading reliability is low, as for *where* and graded support.
   H2's whether/where feature is exactly that boundary. Turning their result into our predictor
   is the strongest available move.

4. **More readings can make verdicts *worse*.** Source: `chen-2024-more-calls` (non-monotone vote
   accuracy on hard items). `yagubyan-2026` (hard items need more than 50 trials) and
   `kumar-2026-verdicts` (a perfectly consistent judge at 51% accuracy) point the same way.
   *Verdict: a genuine threat to H4 as worded, not to H1.* Spearman–Brown reliability is monotone
   in k; accuracy is not. H4's stop rule must be allowed to stop *because more readings will not
   help* (escalate instead), not only because reliability has been reached. Say this in the
   proposal.

5. **Cross-family mixing may not help at frontier scale.** Sources: `kohli-2026` (best single judge
   ≥ panel; n_eff ≈ 2), `kim-2025-correlated`, `goel-2025-capa`, `li-2026-32judges`, and
   `anand-2026` (mixing families does not reduce judges required).
   *Verdict: real risk to H3's sign; good for H3 as a hypothesis.* The prior record is split:
   `xue-2026` and `verga-2024-poll` say mixing helps. Every paper above is on classification or
   preference tasks, and Kohli names open-ended outputs as the boundary. H3 should pre-register
   its expected sign for localisation, and it should **measure error correlation directly (CAPA or
   Kish φ̄) rather than use family as a proxy.**

6. **Stop/escalate is not new.** Sources: `jung-2024-trust-escalate` (Cascaded Selective
   Evaluation with a human-agreement guarantee), `zhu-2026-stopping`, `aggarwal-2023-adaptive`,
   `li-2024-esc`.
   *Verdict: real for the policy, not for the predictor.* The proposal's baselines ("fixed
   readings, one frontier reading, majority vote") will look weak to any reviewer who knows
   these. **Add Cascaded Selective Evaluation and Adaptive-Consistency as baselines.** Locate H4's
   novelty in the *pre-read per-item budget prediction* and the human arm.

7. **Biomedical LLM-judge validity is uneven.** Sources: `delucia-2026` (AUC 0.49–0.66 vs
   clinicians), `lai-2024-rob` (test–retest ≈85%, some domains F1 < 0.5), and against those
   `wu-2025-sourcecheckup` (89% with expert consensus).
   *Verdict: supports the proposal's insistence on an expert read; no change needed except
   citing it.* It does mean the Year-1 expert read is not optional. If reliability stops tracking
   expert accuracy, it stops at that read.

8. **Caching and serving artefacts fake perfect consistency.** Sources: `anand-2026` (99.8% cached
   replicates); `atil-2024` and `he-2025` (inbox 02) for the converse at T = 0.
   *Verdict: a methods obligation, not a threat.* State that each reading is a fresh,
   uncached call with a logged response id.

---

## Established names: what the proposal should call things

| Our term | Established name | Source | How it applies |
|---|---|---|---|
| "readings" of one model on one item | **replications**, a random facet *r*; in G-theory, "occasions"; in survey statistics, a cluster of *N* ratings | Brennan 2001; `sunkavalli-2026` ("replications nested in cells"); `zhang-martinez-2025` | Yes: "a k-rater design" is right when the k raters are exchangeable readings. `wong-2022`'s **k-rater reliability = ICC(k)** is the precise name for pooled reliability at k readings. |
| implied single-reading reliability (0.33–0.35) | **ICC(1)**, the G-coefficient at n′_r = 1; equivalently the intraclass ρ of the beta-binomial | Shrout & Fleiss 1979; `zhang-martinez-2025` | Report it as ICC(1) with the design named. |
| H1 projection from 10 to 30 readings | **Spearman–Brown prophecy formula** = **D-study** projection in a one-facet design | Spearman 1910 (doi:10.1111/j.2044-8295.1910.tb00206.x); Brown 1910 (doi:10.1111/j.2044-8295.1910.tb00207.x); Cronbach, Rajaratnam & Gleser 1963 (doi:10.1111/j.2044-8317.1963.tb00206.x); Brennan 2001 (doi:10.1007/978-1-4757-3456-0) | **The pilot is the G-study; the budget question is the D-study.** That is the name for "budgeting raters". Use it: "we run a G-study on 10 readings and test the D-study projection at 30." |
| "readings are not independent" | **design effect** DE = 1 + (N−1)ρ and **effective sample size** n_eff = N/DE (Kish) | `kohli-2026`; `zhang-martinez-2025` | The same function as Spearman–Brown read backwards. Kohli's "9 judges ≈ 2 votes" is this formula. |
| H3 composition model | **nested G-study design p × (r:f)**, where readings *r* are nested in family *f*; the σ²_pf (item × family) term is the family-specific error | Brennan 2001; `anand-2026` (σ²_sj ceiling) | Within one family, pooling converges to σ²_p + σ²_pf, a family-specific "true score". That is why the within-family curve can be flat while the pooled one is not. Across n_f families the ceiling is σ²_p/(σ²_p + σ²_pf/n_f). **This gives H3 its closed form; "mixing helps only when errors are uncorrelated" is "when σ²_pf is large relative to σ²_r:pf".** |
| errors correlated across families | **error consistency / CAPA**, **algorithmic monoculture**, conditional dependence (Dawid–Skene violation) | `goel-2025-capa`; `kim-2025-correlated`; `balasubramanian-2026-ising` | Use CAPA or Kish φ̄ as H3's measured covariate. |
| stop / escalate policy | **selective evaluation**, **cascaded selective evaluation**; **adaptive consistency** / **early-stopping self-consistency**; learning to defer | `jung-2024-trust-escalate`; `aggarwal-2023-adaptive`; `li-2024-esc`; `zhu-2026-stopping` | Name H4's policy against these. |
| "reliability ≠ validity" | **consistency–bias paradox** (Norman); **trustworthy verdict rate** (Kumar); *"replication sharpens a biased number"* (Sunkavalli) | inbox 01; `kumar-2026-verdicts`; `sunkavalli-2026` | The concept is standard measurement theory. The phrase "reliability is necessary but not sufficient for validity" needs no citation beyond a textbook. |
| P(reliable verdict \| budget) curve | **reliability curve** / "consensus fidelity" vs K | `yagubyan-2026` | A precedent for the plot. Its metric is agreement with the 50-trial majority. |
| per-item budget (H2) | item-specific reliability; item difficulty (IRT) | `kumar-2026-verdicts`; `choi-2026-irt`; `yagubyan-2026` strata | A reviewer may ask "why not IRT?". Have one sentence ready. |

---

## Searched and did not find

All were arXiv search-UI queries (`arxiv.org/search`, all-fields unless marked T: for title),
run 2026-10-12. OpenAlex was unavailable (quota). The counts are arXiv's hit counts.

- `"Spearman-Brown" LLM`: **0**. `"Spearman-Brown"` alone: 2, neither about LLMs.
- `"k-rater" LLM`: **0**.
- T: `judge self-consistency`: **0**. T: `test-retest LLM`: **0**. T: `non-determinism
  LLM-as-a-judge`: **0**.
- `LLM judge temperature self-consistency scoring repeated`: **0**.
- `LLM-as-a-judge repeated sampling aggregation number of judgments`: **0**.
- `LLM judge uncertainty repeated sampling variance item difficulty`: **0**.
- `citation support medical LLM judge physicians agreement`: **0**.
- `evidence attribution scientific claims LLM judge expert agreement`: **0**.
- `LLM evidence support biomedical judge agreement expert`: **0**.
- `sequential testing LLM judge stopping`: 1, about multi-agent debate (a Wald-SPRT compute governor,
  2605.19193). It concerns judging, but not repeated readings of a judge.
- `adaptive sampling LLM judge budget`: 7 hits, one relevant (`park-2025-rautoeval`).
- `effective number of judges correlated LLM`: 6 hits, none relevant.
- `LLM judge reliability scaling number of judges`: 3 hits, none relevant.

**Verified absences, i.e. we looked *inside* the candidate papers and the cell is empty:**
- **No paper varies same-model depth and scores a projection at an unobserved depth.** Checked:
  `yagubyan-2026` (empirical curve, no fitted law); `zhang-martinez-2025` (fixed N = 10;
  explicitly future work); `sunkavalli-2026` (a D-study from K = 3, no deeper observed k);
  `frohn-2026` (j ≤ 7, accuracy only); `anand-2026` (replicates are a facet, but the projection is
  over judges and items); `zatuchin-2026` (an allocation, no held-out test).
  `chen-2024-more-calls` does predict out of sample, but for task accuracy, not judge reliability.
- **No paper applies repeated-reading reliability to span or sentence localisation**, in this file's
  set or in inbox 01 §7.
- **No paper composes families with a G-theory nested design and predicts the pooled curve from
  per-family curves.** `anand-2026` has the family facet but does not predict the pooled curve
  from per-family ones. `kohli-2026` predicts only the independence ideal.

---

## COULD NOT VERIFY, or verified incompletely

- **`xue-2026-scoring-consistency`, full text.** Metadata is verified, and the design comes from the
  authors' OSF code and from `frohn-2026`'s summary. **Not read:** the consistency statistic, its
  numeric values, the ASAP subset, and the limitations section. Routes tried:
  - SAGE `/doi/full`, `/doi/pdf` and `/doi/epub`: 403 or restricted access.
  - PMC PMC12909151: embargoed to 16 Feb 2027.
  - Europe PMC REST `fullTextXML`: 500. Its render endpoint: Cloudflare challenge.
  - The NCBI BioC API: no result.
  - NCBI efetch: *"The publisher of this article does not allow downloading of the full text in
    XML form."*
  - The PMC open-data S3 bucket: no key.
  - arXiv by title, author and phrase: none.
  - WebSearch: no preprint.

  **Pull it with institutional access before submission** and confirm: the metric, the
  intra-LLM values at T = 0 vs T = 1, and whether the science items used real responses.
- Venues stated only from arXiv comments, which I did not confirm on the publisher's page:
  - `kim-2025-correlated`: ICML 2025
  - `aggarwal-2023-adaptive`: EMNLP 2023
  - `li-2024-esc`: ICLR 2024
  - `dorner-2024-limits`: ICLR 2025
  - `park-2025-rautoeval`: NeurIPS 2025
  - `choi-2026-irt`: ICML 2026
  - `kumar-2026-verdicts`: AACL 2026
  - `zhu-2026-*`: WISE 2026
  - `rouillard-2026`: AI in Medicine 2026
- Venue unknown:
  - `jung-2024-trust-escalate`: possibly ICLR 2025, inferred from a "Jung et al., 2025" citation.
  - `goel-2025-capa`: possibly ICML 2025, from recall only.
  - `verga-2024-poll`: no venue found.

  ACL Anthology, OpenReview and dblp were not queried: OpenReview and dblp fail for automated
  fetch, and OpenAlex was out of quota. Cite these as arXiv preprints until checked.
- `2026.aimecon-main.31` (ACL Anthology), surfaced by WebSearch. It reportedly cites xue-2026 as
  finding that majority voting did not beat deterministic scoring without bias correction. **Not
  fetched or verified.** It is a lead for a second independent reading of Xue.
- **Kish 1965, *Survey Sampling*** (the design-effect source) and **Shrout & Fleiss 1979**: named
  from secondary sources (`zhang-martinez-2025`, xue-2026's reference list). Not verified here.

---

## Weight summary for the proposal (what to cite where)

- **Research Opportunity / reliability ≠ validity:**
  - `xue-2026` finding (d)
  - `sunkavalli-2026` ("replication sharpens a biased number")
  - `zhang-martinez-2025`
  - `dorner-2024-limits`
  - and `norman-2026` from inbox 01
- **H1:**
  - `wong-2022` (k-rater reliability) and Spearman/Brown 1910 for the formula
  - `zhang-martinez-2025` for the gap quote
  - `sunkavalli-2026` and `anand-2026` as the existing D-studies
  - `yagubyan-2026` as the empirical reliability curve
- **H2:** `yagubyan-2026` strata; `kumar-2026-verdicts` (item-specific reliability).
- **H3:**
  - `kohli-2026` and `kim-2025-correlated` (against)
  - `verga-2024-poll` and `xue-2026` (for)
  - `anand-2026` (the family facet)
- **H4:**
  - `jung-2024-trust-escalate` and `aggarwal-2023-adaptive` as baselines
  - `chen-2024-more-calls` for non-monotonicity
- **Novelty paragraph:** `chen-2024-more-calls` (task-side prediction from small samples), plus
  the G-theory set, to scope the claim.
- **Biomedical validity:** `wu-2025-sourcecheckup`, `lai-2024-rob`, `delucia-2026`.
