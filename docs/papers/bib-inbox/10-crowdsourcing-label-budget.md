# Bib inbox 10 — Crowdsourcing label budgets: how many labels per item, adaptively, to a target reliability

> **Provenance.** Produced 2026-10-12 for the FY27 LDRD pre-proposal (it is not in this repo).
> Metadata was verified as stated per entry (the status tag on each says how). This file was written
> to close a blind spot a red-team pass found in `06`–`09`: the 2008–2016 crowdsourcing literature on
> label budgets, which already poses H2 and H4 for human annotators. No arXiv-API or OpenAlex queries
> for this file were lost to HTTP 429 (arXiv `/abs/` pages, arXiv search, Crossref and OpenAlex all
> answered); the 429-unrun queries of the sibling surveys are listed in `06`, `07`, `08` and `09`.

**Why this file exists.** LLM judges are noisy annotators, and `zhang-martinez-2025` (in `07`) shows
their repeated readings are correlated, which is exactly the conditional-independence assumption of
Dawid–Skene that this literature builds on. H2 (predict the per-item budget before reading) and H4
(re-read / switch / defer / stop beats fixed-k at matched cost) are, for human workers, the
crowdsourcing control problem of 2010–2016. A reviewer who knows Dai/Mausam/Weld, Ipeirotis or
Karger/Oh/Shah will say so unless the proposal names the lineage and what differs. What differs, in
our setting:

1. **Thirty readings per item are affordable.** Crowd work prices each label; the literature
   optimises *which* items get a second or third label. We can observe the whole per-item curve to
   k = 30, and so score a budget forecast against the observed curve rather than only against a
   stopping outcome.
2. **Labels are set-valued spans** (*where* the evidence is), not binary or multiple-choice answers.
   Dawid–Skene confusion matrices and majority vote are not defined on them without a reduction.
3. **The annotator is one model re-read**, not a stream of fleeting workers. Readings are
   correlated, so the independence that gives these papers their rates does not hold.
4. **Forecasts are pre-registered** and scored out of sample. Nothing in this literature scores a
   per-item budget prediction made before any label is collected.

**Status key.** `verified (page)` = title, authors, venue and year read off the publisher page (AAAI
OJS, JMLR, NeurIPS proceedings, ACL Anthology) or the arXiv `/abs/` page. `verified (record)` =
Crossref or OpenAlex DOI record; publisher page not fetched or not fetchable. `abstract` = the
abstract was read. `read` = the paper's text was retrieved and the cited passage checked. Nothing
here rests on recall.

**Four questions asked of every entry** (the brief's):

- **(a)** Does it decide the number of labels per item **adaptively**?
- **(b)** Does it **predict** per-item difficulty or budget **before** labelling?
- **(c)** Does it give **budget-to-target-reliability theory**?
- **(d)** What must H2/H4 **cite, or differentiate from**?

---

## 1. Decision-theoretic control of crowd workflows (Dai, Mausam, Weld, Lin)

### `dai-2010-turkontrol`
**Decision-Theoretic Control of Crowd-Sourced Workflows.** Peng Dai, Mausam, Daniel S. Weld.
*Proceedings of the AAAI Conference on Artificial Intelligence* 24(1):1168–1174, 2010 (AAAI-10).
doi:10.1609/aaai.v24i1.7760 · <https://ojs.aaai.org/index.php/AAAI/article/view/7760> ·
**verified (page)** · **abstract**.

TurKontrol, a planner that "formulates workflow control as a decision-theoretic optimization
problem, trading off the implicit quality of a solution artifact against the cost for workers to
achieve it", for iterative improve-and-vote workflows.

- **(a) Yes.** Whether to ask for another improvement or another ballot is a per-artifact decision.
- **(b) No.** Quality is a belief updated from ballots, so it is posterior, not a pre-labelling forecast.
- **(c)** Decision-theoretic optimality of the policy under its model. There is no closed-form
  budget-to-reliability law.
- **(d) Must cite as the origin of H4's policy shape.** "Read again or stop, decided per item from the
  history so far" is TurKontrol for human voters. We differ on (b), on the annotator and on the label
  type.

### `dai-2011-ai4aai`
**Artificial Intelligence for Artificial Artificial Intelligence.** Peng Dai, Mausam, Daniel S. Weld.
*Proceedings of the AAAI Conference on Artificial Intelligence* 25(1):1153–1160, 2011 (AAAI-11).
doi:10.1609/aaai.v25i1.8096 · <https://ojs.aaai.org/index.php/AAAI/article/view/8096> ·
**verified (page)** · **abstract**.

Learns TurKontrol's POMDP parameters from real Mechanical Turk data and runs the controller live:
"our system produces significantly superior artifacts compared to those generated through
nonadaptive workflows using the same amount of money".

- **(a) Yes.** **(b) No.** **(c) No** (empirical, matched-money comparison).
- **(d)** This is the matched-cost, adaptive-vs-fixed comparison H4 proposes, already run on humans in
  2011. Cite it beside `dai-2010` when H4 claims to beat fixed-k at matched cost.

### `dai-2013-pomdp-aij`
**POMDP-based control of workflows for crowdsourcing.** Peng Dai, Christopher H. Lin, Mausam,
Daniel S. Weld. *Artificial Intelligence* 202:52–85, 2013. doi:10.1016/j.artint.2013.06.002 ·
**verified (record, Crossref)** · **read** (the authors' copy,
<https://homes.cs.washington.edu/~weld/papers/dai-aij13.pdf>: abstract and contributions list).

Three scenarios: "(1) control of voting to answer a binary-choice question, (2) control of an
iterative improvement workflow, and (3) control of switching between alternate workflows for a
task". Each has a Bayes net relating "worker competency, task difficulty and worker response
quality", and a POMDP whose solution is the control policy. The first contribution, verbatim: "a
principled solution to dynamically decide the number of votes for a task based on the exact history
of workers who worked on a task instance, their answers and a notion of varying problem
difficulty" (pp. 53–54; the sentence runs across a page break and footnotes).

- **(a) Yes**, explicitly: the number of votes per task is the decision variable.
- **(b) Partly.** Task difficulty is a latent variable in the model, inferred as votes arrive. It is
  not forecast from item features before the first vote.
- **(c)** Optimal policy under the POMDP. No reliability-vs-budget law is fitted or forecast.
- **(d) The single most important citation for H4.** Scenario (3), switching workflows, is H4's
  "switch model family". The proposal must say, in one sentence, that H4 is this control problem with
  an LLM annotator, and name what changes: correlated readings of one model, span-valued labels, a
  pre-read budget prior, and a human as a costlier and better annotator at the top of the cascade.

### `lin-2012-agenthunt`
**Dynamically Switching between Synergistic Workflows for Crowdsourcing.** Christopher H. Lin,
Mausam, Daniel S. Weld. *Proceedings of the AAAI Conference on Artificial Intelligence*
26(1):87–93, 2012 (AAAI-12). doi:10.1609/aaai.v26i1.8121 ·
<https://ojs.aaai.org/index.php/AAAI/article/view/8121> · **verified (page)** · **abstract**.
(Crossref dates the DOI record 2021, which is the OJS migration; the OJS page gives 2012 and vol. 26.)

AgentHunt, a POMDP controller that "dynamically switches between these workflows", on the premise
that "alternative workflows can compose synergistically" and a single best workflow is suboptimal.
Live MTurk experiments report "up to 50% error reduction".

- **(a) Yes**, per item, including which workflow the next label comes from.
- **(b) No.** **(c) No** (empirical).
- **(d) Must cite under H3/H4.** "Mixing two model families beats more readings of one" is AgentHunt's
  synergy claim for workflows. Our H3 adds the measurement of *why* (error correlation within vs.
  across families, `kohli-2026` and `anand-2026` in `07`).

---

## 2. Per-item stopping rules and selective repeated labelling

### `abraham-2016-how-many-workers`
**How Many Workers to Ask? Adaptive Exploration for Collecting High Quality Labels.** Ittai Abraham,
Omar Alonso, Vasilis Kandylas, Rajesh Patel, Steven Shelford, Aleksandrs Slivkins. *SIGIR 2016*,
pp. 473–482. doi:10.1145/2911451.2911514 · arXiv:1411.0149 ·
<https://arxiv.org/abs/1411.0149> · **verified (page, arXiv; record, Crossref)** · **abstract**.

"A stopping rule is an algorithm that, given a HIT, decides for any given set of worker answers to
stop and output an answer or iterate and ask one more worker." The SIGIR version adds that the
approach "assumes the historical performance of a worker is known and tries to estimate the HIT
difficulty and answer at the same time." Framed explicitly for IR relevance experiments.

- **(a) Yes.** This is a per-item stopping rule; it is H4's title question asked of humans, in the IR
  community, in 2016.
- **(b) No.** HIT difficulty is estimated *while* answers arrive, not before.
- **(c)** No reliability law; simulation and real-workload comparison against naive rules.
- **(d) Must cite, and in the IR-facing paper especially.** Differentiate: our annotator's "quality
  score" is not known in advance per item, our labels are spans, and we forecast the stopping point
  before the first reading and score the forecast.

### `yang-2010-overlapping-labels`
**Collecting high quality overlapping labels at low cost.** Hui Yang, Anton Mityagin, Krysta M.
Svore, Sergey Markov. *SIGIR 2010*, pp. 459–466. doi:10.1145/1835449.1835526 ·
**verified (record, Crossref and OpenAlex)** · **abstract** (OpenAlex). Found while verifying
`abraham-2016`; not in the red-team list.

Studies "whether, when, and for which samples one should obtain overlapping training labels, as
well as how many labels per sample are needed", for web-search relevance labels. The selective
scheme collects additional labels only for samples a first judge labels relevant, at "around 1.4
labels per sample".

- **(a) Yes**, a one-step rule conditioned on the first label.
- **(b) Weakly.** The first label is a cheap signal for which items need more. It is the nearest
  thing in IR to a pre-budget rule, but it is a label, not a pre-read feature.
- **(c) No.**
- **(d) Cite under H2** as the IR precedent for "spend more labels where a cheap first signal says
  the item is hard". Our *whether*/*where* regime split plays the same role and is cheaper still.

### `sheng-2008-get-another-label`
**Get another label? Improving data quality and data mining using multiple, noisy labelers.**
Victor S. Sheng, Foster Provost, Panagiotis G. Ipeirotis. *KDD 2008*, pp. 614–622.
doi:10.1145/1401890.1401965 · **verified (record, Crossref and OpenAlex)** · **abstract**.
Also listed briefly in `08` §5; this entry supersedes that one-liner for H2/H4 purposes.

Results, verbatim: "(i) Repeated-labeling can improve label quality and model quality, but not
always … (iv) Repeatedly labeling a carefully chosen set of points is generally preferable, and we
present a robust technique that combines different notions of uncertainty to select data points for
which quality should be improved."

- **(a) Yes.** Selective repeated labelling: label uncertainty (and model uncertainty) chooses which
  items get another label.
- **(b) Partly.** Model uncertainty is computed from item features before the extra label, which is a
  feature-based prior on need. It targets training-label quality, not a verification verdict.
- **(c) No** closed-form law; empirical quality-vs-cost curves.
- **(d) Must cite under H2 and H4.** "Repeated labelling helps, but not always" is our
  reliability ≠ validity caution in 2008 form. Point (iv) is the selective-relabelling baseline H4
  must beat, or at least match at lower cost.

### `ipeirotis-2014-repeated-labeling`
**Repeated labeling using multiple noisy labelers.** Panagiotis G. Ipeirotis, Foster Provost,
Victor S. Sheng, Jing Wang. *Data Mining and Knowledge Discovery* 28(2):402–441, 2014 (online 2013).
doi:10.1007/s10618-013-0306-1 · **verified (record, Crossref)**. **Abstract not read**: the publisher
elides it from Crossref and Semantic Scholar, and the Springer page did not return content to an
automated fetch.

The journal extension of `sheng-2008`. Its content is not characterised here beyond that, because it
was not read.

- **(a)–(c)** Not assessed from the text. Cite it as the archival version of `sheng-2008` only.
- **(d)** Read it before the proposal cites anything specific from it.

---

## 3. Budget-to-target-reliability theory (Dawid–Skene and generalisations)

### `karger-2014-budget-optimal`
**Budget-Optimal Task Allocation for Reliable Crowdsourcing Systems.** David R. Karger, Sewoong Oh,
Devavrat Shah. *Operations Research* 62(1):1–24, 2014. doi:10.1287/opre.2013.1235 ·
arXiv:1110.3564 · **verified (record, Crossref; page, arXiv)** · **abstract**.

Poses "the problem of minimizing the total price (i.e., number of task assignments) that must be paid
to achieve a target overall reliability", gives a belief-propagation / low-rank algorithm that is
order-optimal, and shows "perhaps surprisingly, the minimum price necessary to achieve a target
reliability scales in the same manner under both adaptive and nonadaptive scenarios". The authors
note this "strongly relies on the fact that workers are fleeting and cannot be exploited".

- **(a)** Compares adaptive and non-adaptive assignment; the main algorithm is non-adaptive.
- **(b) No.** Items are exchangeable under the model.
- **(c) Yes, and this is the headline.** Price-to-target-reliability with proofs. This *is*
  P(reliable | budget) for crowds.
- **(d) Must cite for H1/H4 and confront directly.** Its negative result says adaptivity buys nothing
  in order under the simple Dawid–Skene model. H4's claim that an adaptive policy beats fixed-k at
  matched cost therefore needs a reason the model does not hold: item difficulty heterogeneity
  (`khetan-oh-2016`) and a non-fleeting, correlated annotator (our setting). Say so explicitly.

### `khetan-oh-2016-adaptive`
**Achieving Budget-optimality with Adaptive Schemes in Crowdsourcing.** Ashish Khetan, Sewoong Oh.
*Advances in Neural Information Processing Systems* 29 (NeurIPS 2016). arXiv:1602.03481 ·
<https://papers.nips.cc/paper_files/paper/2016/hash/03e7ef47cee6fa4ae7567394b99912b7-Abstract.html>
· **verified (page, NeurIPS proceedings and arXiv)** · **abstract**.

Under a "generalized Dawid-Skene model" (which adds per-task difficulty), "we characterize the
fundamental trade-off between budget and accuracy", give "a novel adaptive scheme that matches this
fundamental limit", and show "the gap is significant" between adaptive and non-adaptive schemes.

- **(a) Yes.** Adaptive rounds give unresolved, harder items more budget.
- **(b) No.** Difficulty is latent and revealed by responses.
- **(c) Yes.** Budget-to-accuracy limits with matching achievability, adaptive vs. non-adaptive.
- **(d) Must cite as the theoretical licence for H4.** It is the result that says adaptive allocation
  *should* beat fixed-k when items differ in difficulty, which is our two-regime finding. Our
  contribution is not this theorem; it is measuring the per-item difficulty in advance and on a
  correlated annotator with span labels, where the model's independence assumptions fail.

### `chen-2015-optimal-budget-crowd`
**Statistical Decision Making for Optimal Budget Allocation in Crowd Labeling.** Xi Chen, Qihang Lin,
Dengyong Zhou. *Journal of Machine Learning Research* 16, 2015.
<https://jmlr.org/papers/v16/chen15a.html> · **verified (page, JMLR)** · **abstract**.

"Since the data instances have different levels of labeling difficulty and the workers have different
reliability … it is desirable to wisely allocate the budget among all the instances and workers".
Formulated as a Bayesian MDP, solved approximately by "optimistic knowledge gradient". It "can also
incorporate the contextual information of instances when they are available".

- **(a) Yes.** Sequential per-instance allocation under a fixed total budget.
- **(b) Partly, and this is the H2 threat in this section.** Instance-level contextual features can
  enter the prior, so a feature-informed budget allocation exists here in principle. It is not a
  scored forecast of a per-item budget.
- **(c)** Bayes-optimal policy via DP, with an approximate policy. No closed-form reliability law.
- **(d) Must cite under H2 and H4.** H2 must differentiate on what is predicted (the reading count to a
  target reliability, stated before reading and scored) and on the features (document structure for
  evidence verification).

---

## 4. Adaptive stopping for LLM evaluation (the direct modern descendant)

### `pilditch-2026-optstop`
**Knowing When to Stop: Bayesian Optimal Stopping for LLM Evaluations.** Toby D. Pilditch.
arXiv:2608.14425 (Aug 2026), *(venue unverified; no arXiv comment)*.
<https://arxiv.org/abs/2608.14425> · **verified (page)** · **abstract**.

"optstop", a "precision-based adaptive stopping framework that treats evaluation as a sequential
measurement problem: keep sampling where uncertainty remains high, and stop where estimates are
precise or stable enough". Hierarchical Bayesian; binary, ordinal and continuous outcomes. In a
200-item, 10-epoch evaluation it "removes 57%-97% of planned trials across nine validation settings,
with overall conclusions equivalent to the full run".

- **(a) Yes**, per item, for repeated LLM trials.
- **(b) No.** Posterior precision drives stopping; there is no pre-sampling forecast.
- **(c) No** reliability-vs-budget law; empirical savings.
- **(d) Must cite under H4** as the nearest LLM-era adaptive-stopping baseline. It stops repeated
  *task* trials of the model under test; H4 stops repeated *judge* readings of a verification. Run it
  (or its rule) as a baseline at matched cost.

---

## 5. Predicting disagreement before labelling (H2 for human annotators)

### `wan-2023-everyones-voice`
**Everyone's Voice Matters: Quantifying Annotation Disagreement Using Demographic Information.**
Ruyuan Wan, Jaehyung Kim, Dongyeop Kang. *Proceedings of the AAAI Conference on Artificial
Intelligence* 37(12):14523–14530, 2023. doi:10.1609/aaai.v37i12.26698 · arXiv:2301.05036 ·
**verified (record, Crossref; page, arXiv)** · **abstract**.

**ID note.** The red-team list gave "Wan et al. 2023 (arXiv 2305.06626)". arXiv 2305.06626 is a
different paper, `fleisig-2023` below. Wan et al. is arXiv:2301.05036. Both are relevant and both
are listed.

"This paper examines whether the text of the task and annotators' demographic background information
can be used to estimate the level of disagreement among annotators", fine-tuning language models to
predict disagreement on five subjective datasets.

- **(a) No.** **(c) No.**
- **(b) Yes.** Disagreement is predicted from item text (plus annotator demographics) before labels
  are collected. That is H2's shape for human annotators.
- **(d) Must cite under H2.** Differentiate: the target is disagreement *across people* on subjective
  items, partly explained by who the annotators are. Ours is disagreement of one model *with itself*
  on an evidence-location task, and the predicted quantity is a budget to a reliability target.

### `fleisig-2023-majority-wrong`
**When the Majority is Wrong: Modeling Annotator Disagreement for Subjective Tasks.** Eve Fleisig,
Rediet Abebe, Dan Klein. *EMNLP 2023*. doi:10.18653/v1/2023.emnlp-main.415 · arXiv:2305.06626 ·
<https://aclanthology.org/2023.emnlp-main.415/> · **verified (page, ACL Anthology and arXiv)** ·
**abstract**.

Predicts individual annotator ratings on potentially offensive text, combined with the predicted
target group; reports gains "by 22% at predicting individual annotators' ratings and by 33% at
predicting variance among annotators, which provides a metric for model uncertainty downstream".

- **(a) No.** **(c) No.**
- **(b) Yes**, item-level annotator variance is predicted before labels are seen.
- **(d) Cite under H2**, with `wan-2023`. Same differentiation.

### `zhang-coltekin-2026-graded`
**Quantifying and Predicting Disagreement in Graded Human Ratings.** Leixin Zhang, Çağrı Çöltekin.
arXiv:2605.01168 (May 2026); "Accepted by the 5th Workshop on Perspectivist Approaches to NLP at
LREC" (arXiv comment). <https://arxiv.org/abs/2605.01168> · **verified (page)** · **abstract**.

"We examine whether the degree of annotation disagreement can be predicted from textual features."
Finds "a moderate positive correlation between estimated and observed annotation variance", and that
high-opposition items "are more difficult to predict and are often underestimated by models".

- **(a) No.** **(c) No.**
- **(b) Yes**, item variance from text features, before labelling.
- **(d) Cite under H2 as the honest prior on effect size.** Text-feature prediction of disagreement is
  *moderate*, and the hardest items are under-predicted. H2's forecast should expect the same failure
  at the top of the difficulty range and pre-register what it does about it.

---

## 6. How many raters per item (reliability and power)

### `homan-2026-ratings-per-item`
**How Many Ratings per Item are Necessary for Reliable Significance Testing?** Christopher M. Homan,
Flip Korn, Deepak Pandita, Chris Welty. *Findings of EACL 2026*.
doi:10.18653/v1/2026.findings-eacl.223 · arXiv:2412.02968 · **verified (page, arXiv; record,
OpenAlex)** · **abstract**.

Adapts a method "to determine whether an (existing or planned) dataset has enough responses per item
to assure reliable null hypothesis statistical testing", and finds "for many common metrics,
collecting even 5-10 responses per item (from each model and team of human evaluators) is not
sufficient". Applies to model outputs as well as humans.

- **(a) No**, it is a dataset-level design calculation.
- **(b) No** per-item prediction.
- **(c) Yes, at population level**: responses-per-item needed for reliable significance testing.
- **(d) Must cite under H1/H2.** It is the closest published statement that "a few readings per item"
  is too few, for both humans and models, and it uses the responses-per-item framing we use. This is
  the paper the red-team brief most likely meant by "power analysis for raters per task" (see COULD
  NOT VERIFY). We differ by predicting the count *per item* and by scoring the prediction.

### `inel-2023-collect-measure-repeat`
**Collect, Measure, Repeat: Reliability Factors for Responsible AI Data Collection.** Oana Inel,
Tim Draws, Lora Aroyo. *Proceedings of the AAAI Conference on Human Computation and Crowdsourcing*
(HCOMP) 11(1):51–64, 2023. doi:10.1609/hcomp.v11i1.27547 · arXiv:2308.12885 ·
**verified (page, arXiv; record, Crossref)** · **abstract**.

**Content note.** The red-team list described arXiv 2308.12885 as "Aroyo et al. 2023 — power analysis
for raters per task". The abstract does not describe a power analysis. It proposes "a granular set of
measurements to inform on the internal reliability of a dataset and its external stability over
time", validated on nine datasets, i.e. repeated *collections* of the same annotation task.

- **(a) No.** **(b) No.** **(c) No** budget theory.
- **(d) Cite only for test–retest of a whole annotation collection over time** (internal reliability
  vs. external stability), which parallels our repeat-the-whole-run checks. Do not cite it for power
  analysis.

---

## CHALLENGES OUR NOVELTY

1. **H4's policy is not new; its annotator and its prior are.** Per-item "ask another or stop",
   with a switch between workflows, is TurKontrol / AgentHunt (`dai-2010`, `dai-2013-pomdp-aij`,
   `lin-2012`) and the IR stopping rules of `abraham-2016`. The defensible novelty is (i) a pre-read
   budget prior scored before any reading, (ii) span-valued labels, (iii) a correlated, re-readable
   annotator, and (iv) a human as the top tier of the same cascade. The sentence the proposal needs:
   *"H4 is the decision-theoretic crowd-control problem of Dai, Mausam & Weld (2010, 2013), made cheap
   enough by LLM readers to observe each item's full reliability curve at 30 labels and to score a
   pre-registered budget forecast against it."*
2. **"Adaptive beats fixed-k" has a published negative and a published positive.** `karger-2014`
   proves no order gain from adaptivity under simple Dawid–Skene; `khetan-oh-2016` proves a
   significant gain once items differ in difficulty. H4 must say which regime it is in and why;
   our two-regime (*whether*/*where*) finding is exactly the difficulty heterogeneity that
   `khetan-oh-2016` needs.
3. **P(reliable | budget) with proofs exists** (`karger-2014`, `khetan-oh-2016`, `chen-2015`). Any
   wording that presents a budget-to-reliability relation as new is indefensible. What is new is an
   *empirical, per-item, prospectively scored* relation on a non-independent annotator.
4. **H2 has human-annotator precedents.** Disagreement is predicted from item text before labelling
   (`wan-2023`, `fleisig-2023`, `zhang-coltekin-2026`), feature-informed allocation is allowed for in
   `chen-2015`, and `sheng-2008` uses model uncertainty to pick items for relabelling. H2 survives
   only as: *a per-item reading budget to a stated reliability target, for an evidence-verification
   task, predicted from document-structure features, and scored out of sample.* And
   `zhang-coltekin-2026` warns the effect will be moderate and worst on the hardest items.
5. **"A few readings is too few" is published** for models and humans alike (`homan-2026`). Cite it
   rather than claim the observation.

## Searched and did not find

All run on 2026-10-12.

- arXiv search (`searchtype=all`) `Aroyo power analysis raters`: no results.
- arXiv search (`searchtype=all`) `"power analysis" raters`: one unrelated hit (2609.10421, ED
  revisit review).
- arXiv search (`searchtype=title`) `how many raters do you need`: no results.
- Crossref `query.bibliographic` "How many raters do you need power analysis foundation models
  Aroyo": no relevant record.
- OpenAlex `search=` "How many raters do you need power analysis": no relevant record.
- OpenAlex `search=` "Aroyo raters power analysis reliability number of raters per item": found
  `homan-2026` (listed above); no Aroyo-authored power analysis.
- None of the papers in this file predicts a per-item label budget from item features **and** scores
  that prediction prospectively; none uses set-valued (span) labels; none models a single annotator
  re-read with correlated errors. These negatives rest on the abstracts read here, not on a
  systematic search of the crowdsourcing literature, which is large; treat them as "not found in the
  canonical papers", not "does not exist".

Not searched: the active-learning-with-noisy-labelers line beyond `sheng-2008`; GLAD-style latent
item-difficulty models (Whitehill et al. 2009) beyond the Dawid–Skene entries in `02`; the
crowdsourcing budget literature after 2016 (e.g. WSDM/HCOMP label-budget papers surfaced by OpenAlex
but not fetched).

## Established names

| Our phrasing | Established name | Source |
|---|---|---|
| "read again / stop / switch model, per item" | **decision-theoretic (POMDP) control of crowd workflows** | `dai-2010`, `dai-2013-pomdp-aij`, `lin-2012` |
| "how many readings for this item" | **stopping rule** (per HIT) | `abraham-2016` |
| "spend extra readings only where needed" | **selective repeated labelling** | `sheng-2008`, `ipeirotis-2014` |
| "budget needed for a reliability target" | **budget-optimal task allocation**; price vs. target reliability | `karger-2014` |
| "adaptive vs. fixed allocation" | **adaptive vs. non-adaptive task assignment** (generalized Dawid–Skene) | `khetan-oh-2016` |
| "allocate a fixed budget across items" | **optimal budget allocation in crowd labelling**; Bayesian MDP, knowledge gradient | `chen-2015` |
| "predict which items people disagree on" | **annotator-disagreement prediction**; perspectivism | `wan-2023`, `fleisig-2023`, `zhang-coltekin-2026` |
| "enough readings per item" | **responses per item for reliable significance testing** | `homan-2026` |
| "readings of one LLM are not independent" | **conditional-independence violation** of Dawid–Skene | `07` (`zhang-martinez-2025`, `balasubramanian-2026-ising`) |

## COULD NOT VERIFY

- **"Aroyo et al. 2023 (arXiv 2308.12885) — power analysis for raters per task."** The arXiv ID
  resolves to Inel, Draws & Aroyo, HCOMP 2023 (`inel-2023`), which is not a power analysis. No
  Aroyo-authored power analysis for raters per item was found. Queries: arXiv search `Aroyo power
  analysis raters` (all fields), `"power analysis" raters` (all fields), `how many raters do you need`
  (title); Crossref `query.bibliographic` with the same words plus "Aroyo"; OpenAlex `search=` twice
  (see above). The paper that fits the description is `homan-2026` (Homan, Korn, Pandita, Welty,
  Findings of EACL 2026). Confirm which one was meant before citing either for power analysis.
- **"Wan et al. 2023 (arXiv 2305.06626)."** Author/ID mismatch, resolved: 2305.06626 is
  `fleisig-2023`; Wan et al. is 2301.05036. Both verified and listed.
- **`ipeirotis-2014-repeated-labeling`, content.** Metadata verified (Crossref); abstract and text
  not retrieved (publisher-elided abstract; Springer page not fetchable automatically). Characterised
  only as the journal version of `sheng-2008`.
