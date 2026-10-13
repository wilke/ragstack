# Bib inbox 06 — inference-time compute scaling, and predicting success from budget

> **Provenance.** Produced 2026-10-12 for the FY27 LDRD pre-proposal ("the pre-proposal" / "the draft" below; it is not in this repo). Metadata was verified as stated per entry (the status tag on each says how). arXiv API and OpenAlex HTTP 429 rate limits left the queries listed in *Searched and did not find* ("Lost to HTTP 429") unrun; they are coverage gaps, not negative results. Kazdan's "withheld dataset of 10 000 samples per problem" was re-checked against the arXiv HTML full text on 2026-10-12.

Scope: work that predicts P(success | samples, tokens, compute), work that allocates compute per item, and work on verifiers as the bottleneck. This survey was written for the LDRD pre-proposal (H1–H4 and the Novelty paragraph).

Method: metadata comes from the export.arxiv.org API (batch `id_list`) and, where that hit HTTP 429, from arxiv.org `/abs/` meta tags or OpenAlex. For the papers marked **read**, the full text was taken from the arXiv PDF with pdfminer and the passages quoted were checked by hand. **abstract** means only the abstract was read. Any venue not on the arXiv record is marked *(venue unverified)*.

Already covered elsewhere, so not repeated here: judge self-consistency and test–retest work (`reiss-2023`, `barrie-2024`, `stureborg-2024`, `schroeder-2024-trust`, `nowak-2025-self-consistency`, `xue-2026-scoring-consistency`) is in `bib-inbox/01` and `bib-inbox/02`.

**Key for the four questions:**
- (a) Does it predict success as a function of budget? Is that prediction tested out of sample, i.e. made before the outcome is observed?
- (b) Is the prediction per item or per population?
- (c) Is the verifier's own reliability treated as a function of budget, or is a verifier/oracle assumed?
- (d) What must H1–H4 cite or answer?

---

## 1. Coverage and repeated-sampling scaling laws (oracle verifier)

### `brown-2024-monkeys`: Large Language Monkeys: Scaling Inference Compute with Repeated Sampling
Bradley Brown, Jordan Juravsky, Ryan Ehrlich, Ronald Clark, Quoc V. Le, Christopher Ré, Azalia Mirhoseini. arXiv:2407.21787 (2024) *(venue unverified)*.
https://arxiv.org/abs/2407.21787 · **verified** (arXiv API) · **read** (§4, §5 Discussion and Limitations).

- (a) Coverage (pass@k) over k is fitted with an exponentiated power law. The fit is **in-sample**: it uses 40–100 log-spaced points along the whole observed curve (App.). Nothing is predicted before it is observed.
- (b) Population: the fraction of problems solved.
- (c) An **oracle verifier** is assumed for coverage. The paper's own §4 then shows that the realistic verifiers (majority vote, and an ArmoRM reward model with best-of-N) "plateau beyond several hundred samples" while coverage keeps rising. §4.2 gives "two cautionary tales" of unit-test false positives and negatives. The limitations section names verification as the open problem: "Our results from Section 4 highlight the importance of improving sample verification methods when tools for automatically doing so are unavailable."
- (d) This is the canonical "[repeated sampling]" citation for the Novelty paragraph, and the description fits: success vs. samples, verifier assumed. It also supplies our motivation. Its stated gap is verification in domains without automatic checkers, and evidence-support judgement is such a domain.

Worth it: **must cite**. It is the anchor for "inference scaling assumes a reliable verifier", and its limitations section names our gap in its own words.

### `schaeffer-2025-power-laws`: How Do Large Language Monkeys Get Their Power (Laws)?
Rylan Schaeffer, Joshua Kazdan, John Hughes, Jordan Juravsky, et al. (10 authors). arXiv:2502.17578 (2025) *(venue unverified)*.
https://arxiv.org/abs/2502.17578 · **verified** · **read** (§2–§4).

- (a) Yes. It forecasts the power-law exponent from fewer samples, and shows backtests that subsample problems and samples. **Caveat: the backtest is run on synthetic data with a known ground-truth exponent (Fig. 7).** On real data the two estimators are only compared with each other (Fig. 6).
- (b) **Both levels, and this is the important part.** Per problem, pass_i@k = 1 − (1 − pass_i@1)^k, so the failure rate falls exponentially in k. That is a one-parameter law per item. The aggregate power law arises only from a heavy-tailed distribution of pass_i@1 across items.
- (c) An oracle verifier is assumed. The authors also note (appendix) that "independent attempts" is "not fully correct" for best-of-N jailbreaking.
- (d) **H1 must cite this.** Spearman–Brown in readings is the reliability-theory analogue of their one-parameter per-item law, and their result predicts what we should expect. The per-item curves are simple, but **the population curve is governed by the tail of hard items**. That is exactly why H2 (per-item prediction) should beat the population curve, and why our "locating *where*" items, still gaining at 20 readings, can dominate pooled extrapolation. Reviewers will ask why we need Spearman–Brown rather than a beta-binomial over items. We need an answer.

Worth it: **must cite**. It is the closest structural precedent for H1+H2: a per-item one-parameter law, with a population law emerging from heterogeneity.

### `kazdan-2025-passk`: Efficient Prediction of Pass@k Scaling in Large Language Models
Joshua Kazdan, Rylan Schaeffer, Youssef Allouah, Colin Sullivan, Kyssen Yu, Noam Levi, et al. (7 authors). arXiv:2510.05197 (2025) *(venue unverified)*.
https://arxiv.org/abs/2510.05197 · **verified** · **read** (§1–§6).

- (a) **Yes, and out of sample in k.** A budget of B/m ∈ [1, 100] samples per problem predicts pass@k for k up to 10⁴. The prediction is scored against "a withheld dataset of 10 000 samples per problem" (§1, Fig. 1), and the figure separates the region that can be estimated directly from the region that has to be extrapolated. This is the same design as our "ten readings predict thirty".
- (b) The target is the population passD@k. A beta-binomial is fitted over per-problem success counts. Their **dynamic sampling** sends more budget to the problems with the fewest successes (Alg. 1), which is per-item allocation. But the allocation serves the *population forecast*, not a per-item decision.
- (c) Oracle verifier.
- (d) H1's projection from k=10 to k=30 is an instance of an existing method family: small-budget extrapolation, scored against withheld samples. Cite it, and consider the beta-binomial as a baseline estimator against Spearman–Brown. Our pre-registered projection (0.88 projected, 0.92 observed) is *less* novel as a method than as a target (verification reliability).

Worth it: **must cite**. It is the methodological precedent for H1's design, and H1's projection should be compared against its estimator.

### `wu-2024-inference-scaling`: Inference Scaling Laws: An Empirical Analysis of Compute-Optimal Inference for Problem-Solving with Language Models
Yangzhen Wu, Zhiqing Sun, Shanda Li, Sean Welleck, Yiming Yang. arXiv:2408.00724 (2024) *(venue unverified)*.
https://arxiv.org/abs/2408.00724 · **verified** (metadata) · **not read**. Content is recalled and should not be quoted.

- (a) Population accuracy is studied vs. FLOPs for voting and tree search. It is descriptive, not a forecast.
- (b) Population. (c) Reward model / majority vote assumed. (d) Background only.

Worth it: optional, as a background citation for "inference scaling laws".

---

## 2. Compute-optimal and per-item adaptive allocation

### `snell-2024-test-time`: Scaling LLM Test-Time Compute Optimally can be More Effective than Scaling Model Parameters
Charlie Snell, Jaehoon Lee, Kelvin Xu, Aviral Kumar. arXiv:2408.03314 (2024) *(venue unverified on the arXiv record)*.
https://arxiv.org/abs/2408.03314 · **verified** · **read** (§3.2, §8).

- (a) The strategy is chosen per difficulty bin with two-fold cross-validation inside the test set. It does not forecast the success *level* before observing it.
- (b) **Per item via difficulty bins** (five quantile bins). "Oracle difficulty" is the pass@1 over **2048 samples** with ground truth. "Model-predicted difficulty" is the PRM-averaged score over **the same 2048 samples**. Their §3.2 admits that this costs inference compute and leaves it out of the accounting.
- (c) A learned PRM verifier is assumed. Verifier reliability is not modelled as a function of budget.
- (d) The limitations section is load-bearing for H2: "estimating our notion of difficulty requires applying a non-trivial amount of test-time compute itself. Future work should consider alternative ways of more efficiently estimating question difficulty (e.g., by pretraining or finetuning models to directly predict difficulty of a question) or dynamically switching between assessing difficulty and attempting to solve a question." **H2 (difficulty from cheap item features *before* reading) and H4 (updating as readings arrive) are exactly these two future-work items, moved to the verification step.** This is the "[compute-optimal test-time scaling]" citation.

Worth it: **must cite**. Its limitations section states our H2/H4 gap verbatim.

### `damani-2024-hard-to-think`: Learning How Hard to Think: Input-Adaptive Allocation of LM Computation
Mehul Damani, Idan Shenfeld, Andi Peng, Andreea Bobu, Jacob Andreas. arXiv:2410.04707 (2024) *(venue unverified)*.
https://arxiv.org/abs/2410.04707 · **verified** · **read** (§3).

- (a) **Yes, and before computing.** A model Δ̂(x) "predicts the distribution of rewards given an input and computation budget". It is trained on held-out queries, and at deployment it sees only x (§3.1). Allocation solves a knapsack over the predicted marginal rewards.
- (b) **Per item.** This is the strongest precedent for H2's "predict each item's required budget before it is read". The predictors are LM-hidden-state probes or fine-tuned LMs, not hand features.
- (c) The reward model / reranker is taken as given. The quantity predicted is *task reward*, not the reliability of the verifier.
- (d) **H2 must cite this and position against it.** The idea of a per-input marginal-benefit predictor, used to allocate budget, exists already. What is new for us is (i) the target is the verifier's reliability, not the generator's reward, and (ii) the features are interpretable document properties. Their "routing to an expensive but accurate procedure" is also a precedent for H4's local→frontier escalation.

Worth it: **must cite**. It is the closest precedent for per-item, pre-hoc budget prediction.

### `wang-2024-dsc`: Make Every Penny Count: Difficulty-Adaptive Self-Consistency for Cost-Efficient Reasoning
Xinglin Wang, Shaoxiong Feng, Yiwei Li, Peiwen Yuan, et al. (9 authors). arXiv:2408.13457; NAACL 2025 Findings (per the arXiv comment).
https://arxiv.org/abs/2408.13457 · **verified** · **skimmed**.

- (a) No law, but sample counts are allocated from a **prior** difficulty estimate. An LLM batch-ranks the questions' difficulty before solving, and posterior agreement is used afterwards.
- (b) Per item. (c) Majority vote is the aggregator, and correctness is checked against gold.
- (d) H2's "decide before reading" has a direct precedent in answer generation. **H4's baselines should include this.**

Worth it: cite as a precedent and as a candidate H4 baseline.

### `aggarwal-2023-adaptive-consistency`: Let's Sample Step by Step: Adaptive-Consistency for Efficient Reasoning and Coding with LLMs
Pranjal Aggarwal, Aman Madaan, Yiming Yang, Mausam. arXiv:2305.11860; EMNLP 2023 (per the arXiv comment).
https://arxiv.org/abs/2305.11860 · **verified** · **skimmed** (§3, stopping criteria).

- (a) This is online, not pre-hoc. After each sample, a **Dirichlet stopping criterion** computes the probability that the current majority answer remains the majority, and sampling stops above a threshold.
- (b) Per item.
- (c) **That stopping probability is a per-item, budget-indexed estimate of the *reliability* (stability) of the aggregate verdict, not of its correctness.** This is formally the quantity H1/H4 track, applied to answer generation rather than to judging evidence.
- (d) **H4 must beat this, not only fixed-k.** The pre-proposal's baselines (fixed k, one frontier reading, majority vote) leave out the obvious adaptive-stopping family: Adaptive-Consistency, ESC, DSC, ReASC, MARS. A reviewer from the inference-scaling community will see that gap.

Worth it: **must cite**, and add it as a baseline.

### `li-2024-esc`: Escape Sky-high Cost: Early-stopping Self-Consistency for Multi-step Reasoning
Yiwei Li, Peiwen Yuan, Shaoxiong Feng, Boyuan Pan, et al. (8 authors). arXiv:2401.10480; ICLR 2024 (per the arXiv comment).
https://arxiv.org/abs/2401.10480 · **verified** (metadata) · **not read**.

- (a)–(c) Window-based early stopping once samples agree. Per item, online, majority vote, gold for scoring.
- (d) H4 baseline family.

Worth it: cite in the H4 baseline list.

### `chen-2024-more-calls`: Are More LLM Calls All You Need? Towards Scaling Laws of Compound Inference Systems
Lingjiao Chen, Jared Quincy Davis, Boris Hanin, Peter Bailis, Ion Stoica, Matei Zaharia, James Zou. arXiv:2403.02419 (2024) *(venue unverified)*.
https://arxiv.org/abs/2403.02419 · **verified** · **read** (§3–§5).

- (a) **Yes.** An analytical scaling model is fitted on 2–100 calls and used to predict the optimal number of calls. It is validated against measured performance.
- (b) The mechanism is per item (an item-difficulty indicator d(x)), but the prediction is population-level.
- (c) The verdict is a majority vote. Correctness is against gold.
- (d) **This is the strongest published support for our reliability ≠ validity argument, and H4's expert test must cite it.** With more votes, majority vote converges to the right answer on "easy" items and to the *wrong* answer on "hard" items (where the per-call probability of the correct answer is below the competitors'). So pooled reliability rises toward 1 on hard items while their accuracy falls. Aggregate performance can be non-monotone (inverse-U) in the number of calls. Our pooled graded-support reliability of 0.92 at 30 readings cannot by itself tell us which regime an item is in, which is why the expert read is decisive.

Worth it: **must cite**, both in the Research Opportunity paragraph and for H4.

### `manvi-2024-adaptive`: Adaptive Inference-Time Compute: LLMs Can Predict if They Can Do Better, Even Mid-Generation
Rohin Manvi, Anikait Singh, Stefano Ermon. arXiv:2410.02725 (2024) *(venue unverified)*.
https://arxiv.org/abs/2410.02725 · **verified** (metadata) · **abstract** (recalled content flagged).

- (a) The model self-evaluates mid-generation whether resampling would help. Per item, online.
- (b) Per item. (c) Generator self-evaluation, trained against an RM/oracle.
- (d) A precedent for H4's "update as readings arrive".

Worth it: optional.

### `han-2024-tale`: Token-Budget-Aware LLM Reasoning
Tingxu Han, Zhenting Wang, Chunrong Fang, Shiyu Zhao, et al. (6 authors). arXiv:2412.18547 (2024) *(venue unverified)*.
https://arxiv.org/abs/2412.18547 · **verified** (metadata) · **not read**.

- (a)–(b) Estimates a per-problem token budget before reasoning. (c) Gold-scored. (d) Another pre-hoc per-item budget precedent for H2. It covers tokens within one reading, not a number of readings.

Worth it: optional, one line in the H2 related work.

### `zhao-2026-roi-reasoning`: ROI-Reasoning: Rational Optimization for Inference via Pre-Computation Meta-Cognition
Muyang Zhao, Qi Qi, Hao Sun. arXiv:2601.03822 (2026) *(venue unverified)*.
https://arxiv.org/abs/2601.03822 · **verified** (arXiv /abs/) · **abstract**.

- (a) The model is trained to "predict reasoning cost and expected utility before generation" and to make solve-or-skip decisions under a global token budget (a stochastic knapsack).
- (b) Per item. (c) Task utility with gold. (d) This is the 2026 state of "predict compute needed before solving". It shows that H2's framing is current. The target is still task success, not verification reliability.

Worth it: cite as recent evidence that pre-hoc budget prediction is active.

### `nguyen-2026-uab`: Uncertainty-Aware Budget Allocation for Adaptive Test-Time Reasoning
Manh Nguyen, Sunil Gupta, Hung Le. arXiv:2605.26849 (2026) *(venue unverified)*.
https://arxiv.org/abs/2605.26849 · **verified** (OpenAlex record, arXiv DOI) · **abstract**.

- (a) A two-phase design. Phase 1 gives every question a few samples. The vote entropy from those samples then drives a concave integer allocation of the remaining budget.
- (b) Per item. (c) Gold-scored majority vote.
- (d) This is nearly identical in shape to H4: pilot readings, then a per-item budget. **It is a baseline H4 has to beat at matched cost.**

Worth it: cite as an H4 baseline.

### `chen-2026-mars`: MARS: Margin-Adversarial Risk-controlled Stopping for Parallel LLM Test-time Scaling
Wenbo Chen, Puheng Li, Mengyang Liu, Weijie Su, Tianpei Xie. arXiv:2606.12935 (2026) *(venue unverified)*.
https://arxiv.org/abs/2606.12935 · **verified** (arXiv /abs/) · **abstract**.

- (a) **A stopping rule that guarantees, with high probability, that "the early-stopped answer matches the full-budget vote".** A five-feature logistic model learns trace-level switch probabilities.
- (b) Per item. **(c) The guaranteed quantity is agreement with the full-budget verdict, which is reliability, not correctness.** This is the same target as our P(reliable verdict | budget), for answer generation.
- (d) See CHALLENGES below. H1/H4 must cite it, and the Novelty sentence must not claim "per-item reliability vs. budget" in general.

Worth it: **must cite**. It is the closest existing object to "per-item reliability as a function of budget".

---

## 3. Verifiers: imperfection, scaling the verifier, verifier ensembles

### `cobbe-2021-gsm8k`: Training Verifiers to Solve Math Word Problems
Karl Cobbe, Vineet Kosaraju, Mohammad Bavarian, Mark Chen, et al. (12 authors). arXiv:2110.14168 (2021).
https://arxiv.org/abs/2110.14168 · **verified** (metadata) · **not re-read**.

- (a)–(c) A trained outcome verifier with best-of-N. Gold-labelled. Population. The verifier is assumed, and is trained against gold.
- (d) The origin of the "[best-of-N verifiers]" citation. (Recalled, and not quoted from the PDF: they report that best-of-N accuracy degrades at very large N as the verifier is fooled. Check before quoting.)

Worth it: cite as the origin of the verifier paradigm.

### `lightman-2023-verify`: Let's Verify Step by Step
Hunter Lightman, Vineet Kosaraju, Yura Burda, Harri Edwards, et al. (10 authors). arXiv:2305.20050 (2023) *(venue unverified on the arXiv record)*.
https://arxiv.org/abs/2305.20050 · **verified** (metadata) · **not re-read**.

- (a)–(c) PRM vs. ORM best-of-N vs. N. Population. The verifier is trained and then fixed. Its reliability is not a function of budget.
- (d) Background for "verifiers"; Snell builds on it.

Worth it: cite in one line.

### `wang-2022-self-consistency`: Self-Consistency Improves Chain of Thought Reasoning in Language Models
Xuezhi Wang, Jason Wei, Dale Schuurmans, Quoc Le, et al. (8 authors). arXiv:2203.11171; ICLR 2023 (per the arXiv comment).
https://arxiv.org/abs/2203.11171 · **verified** · **not re-read**.

- (a) Accuracy vs. number of sampled paths. In-sample. (b) Population. (c) No verifier: agreement is the selector, and correctness is against gold.
- (d) This is the "[self-consistency]" citation. Note the terminology clash: "self-consistency" here is a *decoding method*, whereas our inbox uses the word for judge *test–retest*. The paper must disambiguate.

Worth it: **must cite**, and say which meaning we use.

### `gao-2022-overoptimization`: Scaling Laws for Reward Model Overoptimization
Leo Gao, John Schulman, Jacob Hilton. arXiv:2210.10760 (2022) *(venue unverified)*.
https://arxiv.org/abs/2210.10760 · **verified** (metadata) · **not re-read**.

- (a) Yes: functional forms for gold reward vs. KL distance under best-of-N and RL against a proxy RM. It is **predictive across RM sizes**.
- (b) Population. (c) **The verifier's imperfection is modelled explicitly.** The proxy and gold diverge as optimisation pressure (N) grows.
- (d) This is the "reward hacking / best-of-N degrades" reference. It is a precedent for H3's argument that more selection against an imperfect judge can lower validity.

Worth it: cite for verifier imperfection.

### `stroebl-2024-resampling-limits`: The Limits of Inference Scaling Through Resampling
Benedikt Stroebl, Sayash Kapoor, Arvind Narayanan. arXiv:2411.17501 (v1 titled "Inference Scaling fLaws…"; v3 2026) *(venue unverified)*.
https://arxiv.org/abs/2411.17501 · **verified** · **read** (§1–§4).

- (a) With an imperfect verifier that has a non-zero false-positive rate, "resampling cannot decrease this probability, so it imposes an upper bound to the accuracy … regardless of compute budget". The optimal number of attempts is "often fewer than 10" once false positives carry a cost.
- (b) Population, though false-positive propensity is shown to correlate with model strength.
- (c) **Verifier imperfection is central, but fixed. It is not a function of the verifier's own budget.**
- (d) H4's value-of-information framing should cite this: a ceiling set by verifier error, and an optimal k well below "as many as possible".

Worth it: **must cite**. It is the cleanest statement of "the verifier is the bottleneck".

### `dorner-2025-roc-n-reroll`: ROC-n-reroll: How verifier imperfection affects test-time scaling
Florian E. Dorner, Yatong Chen, André F. Cruz, Fanny Yang. arXiv:2507.12399 (2025) *(venue unverified)*.
https://arxiv.org/abs/2507.12399 · **verified** · **read** (§1, §4.3, §5.3, conclusion).

- (a) The paper proves that instance-level accuracy of best-of-N and rejection sampling is fixed by the geometry of the verifier's ROC curve. **It also proves that extrapolation from the low-compute regime is impossible in general**: "when the ROC curve is unknown, it is impossible to extrapolate the performance of rejection sampling based on the low-compute regime", and "we cannot rely on extrapolation to predict performance for high levels of compute."
- (b) **Per instance** (the theory is instance-level, and is validated per instance on GSM8K).
- (c) The verifier is imperfect, characterised by its ROC, and fixed with respect to budget.
- (d) **This is a direct theoretical challenge to H1's extrapolation premise, and must be answered.** Our defence: H1 extrapolates the *agreement of repeated verdicts*, which under exchangeable readings follows a known functional form (Spearman–Brown, or binomial). It does not extrapolate selection accuracy, which depends on an unknown ROC. The defence holds only if readings are exchangeable. The pooled-across-families non-flatness in our pilot is a warning sign.

Worth it: **must cite and answer**. It is the strongest "you can't extrapolate" result in the area.

### `zhang-2024-genrm`: Generative Verifiers: Reward Modeling as Next-Token Prediction
Lunjun Zhang, Arian Hosseini, Hritik Bansal, Mehran Kazemi, et al. (6 authors). arXiv:2408.15240; ICLR 2025 (per the arXiv comment).
https://arxiv.org/abs/2408.15240 · **verified** · **skimmed**.

- (a) Verification accuracy improves with majority voting over several sampled verification rationales.
- (b) Population. **(c) This is the first verifier whose quality is a budget-dependent quantity (number of verification CoTs).** It is measured as downstream best-of-N accuracy, not as the reliability of the verdict per item, and it is not forecast.
- (d) H1's "readings of the verifier" have a precedent here. Cite it so we don't appear to have invented "sample the verifier repeatedly".

Worth it: **must cite**.

### `singhi-2025-solve-verify`: When To Solve, When To Verify: Compute-Optimal Problem Solving and Generative Verification for LLM Reasoning
Nishad Singhi, Hritik Bansal, Arian Hosseini, Aditya Grover, et al. (7 authors). arXiv:2504.01005; COLM 2025 (per the arXiv comment).
https://arxiv.org/abs/2504.01005 · **verified** · **read** (§3–§5).

- (a) **Yes: inference scaling laws over both the number of solutions and the number of verifications.** They fit S_opt ∝ C^a and V_opt ∝ C^b, and find that "solutions should scale 1.5–2× faster than the number of verifications". GenRM beats self-consistency only at high budgets.
- (b) Population (MATH etc.). (c) **Verification budget is an explicit axis**, scored against gold task success. The reliability of a single verification verdict is not estimated.
- (d) **This is the closest population-level precedent for "verification budget as a scaling axis".** The Novelty sentence must not imply that nobody scales the verifier's budget. Our difference: the target is the verdict's reliability/validity on evidence-support items, per item, scored in advance.

Worth it: **must cite**. It narrows the novelty claim.

### `zhao-2025-sample-scrutinize`: Sample, Scrutinize and Scale: Effective Inference-Time Search by Scaling Verification
Eric Zhao, Pranjal Awasthi, Sreenivas Gollapudi. arXiv:2502.01839 (2025) *(venue unverified)*.
https://arxiv.org/abs/2502.01839 · **verified** · **read** (§1–§2).

- (a) Accuracy is reported as a heatmap over (k_inf responses × **k_verif verification samples per response**) (Fig. 2.1). It is descriptive, not a forecast.
- (b) Population. (c) Verifier budget is an explicit axis. They also find that "frontier models demonstrate remarkably weak out-of-box verification capabilities".
- (d) Same as Singhi: verification compute already scales. Cite in H1, and for the motivation that verification is weak.

Worth it: **must cite**.

### `saad-falcon-2025-weaver`: Shrinking the Generation-Verification Gap with Weak Verifiers
Jon Saad-Falcon, E. Kelly Buchanan, Mayee F. Chen, Tzu-Heng Huang, et al. (12 authors). arXiv:2506.18203; NeurIPS 2025 (per the arXiv journal-ref).
https://arxiv.org/abs/2506.18203 · **verified** · **read** (§4, §5 excerpts).

- (a) No budget law. (b) Population.
- (c) **Verifier accuracies are estimated without labels using weak supervision (a Dawid–Skene-style latent-variable model)**, under the assumption that verifiers are "conditionally independent given Y". On the budget axis they find: "gains diminish as more models are added … additional verifiers contribute redundant signal due to correlated biases on hard examples". Scaling "the number of scores per verifier — via prompt tuning or temperature variation … yields modest improvements, increasing verifier count remains the more effective strategy."
- (d) **H3 must cite this.** It already states the qualitative H3 result (correlated errors limit ensemble gains), and the readings-per-verifier vs. more-verifiers trade-off that sits between H1 and H3. Our composition model has to go beyond this qualitative claim, to a quantitative prediction of the pooled curve. The term "generation–verification gap" should be adopted.

Worth it: **must cite**. It is the H3 precedent.

### `chen-2024-provable-scaling`: Provable Scaling Laws for the Test-Time Compute of Large Language Models
Yanxi Chen, Xuchen Pan, Yaliang Li, Bolin Ding, Jingren Zhou. arXiv:2411.19477; NeurIPS 2025 (per the arXiv comment).
https://arxiv.org/abs/2411.19477 · **verified** · **abstract** (recalled detail flagged).

- (a) A knockout or league tournament of pairwise LLM comparisons. Failure probability decays exponentially in compute **under the assumption that the comparator beats chance on each item (p_comp > 0.5)**.
- (b) The assumption is per item. (c) The comparator's reliability is a parameter and is assumed. Repeated comparisons raise the effective reliability.
- (d) A theoretical analogue for H1: repeated stochastic judgments amplify per-item reliability only when single-judgment accuracy exceeds 0.5 on that item. This is the same Condorcet condition as `chen-2024-more-calls`.

Worth it: cite as theory.

### `kim-2025-agent-scaling`: Towards a Science of Scaling Agent Systems
Yubin Kim, Ken Gu, Chanwoo Park, Chunjong Park, et al. (20 authors). arXiv:2512.08296 (2025) *(venue unverified)*.
https://arxiv.org/abs/2512.08296 · **verified** (arXiv API + OpenAlex) · **abstract**.

- (a) A predictive model of agent-system performance over coordination, capability and task factors. Cross-validated R² = 0.373, and it picks the best architecture for 87% of held-out configurations.
- (b) Configuration and population, not item. (c) It notes that "architectures without centralized verification tend to propagate errors more".
- (d) This is the "scaling laws for agents" reference. It is configuration-level, and its low R² is a useful contrast with a per-item verification law.

Worth it: cite in one line.

### `gema-2025-inverse-scaling`: Inverse Scaling in Test-Time Compute
Aryo Pradipta Gema, Alexander Hägele, Runjin Chen, Andy Arditi, et al. (14 authors). arXiv:2507.14417; TMLR 12/2025 (per the arXiv comment).
https://arxiv.org/abs/2507.14417 · **verified** (arXiv /abs/) · **abstract**.

- (a) Accuracy *falls* with longer reasoning on constructed tasks, including distractor tasks. (b) Per task family. (c) Gold.
- (d) The budget axis is not always monotone. If "budget" in H4 includes reasoning-token budget per reading (not only the number of readings), H1's monotone law may fail. State that H1's budget is the number of readings.

Worth it: optional, as a caveat citation.

---

## 4. Adjacent, recent (2026) work on stopping and routing verifier/judge calls under cost

### `hajihashemi-2026-omvv`: Online Verification of Language Model Responses Under Cost Constraints
Erfan Hajihashemi, Yanning Shen. arXiv:2610.02632 (Oct 2026) *(venue unverified)*.
https://arxiv.org/abs/2610.02632 · **verified** (arXiv /abs/) · **abstract**.

- (a) Online routing among a pool of K weak verifiers of differing cost, chosen per round by an online score combiner and exponential-weights routing. The abstract attributes the weak-verifier-then-costly-strong-verifier escalation scheme to *prior work* (a single fixed weak verifier deciding when to query the strong one); OMVV's contribution is adaptive routing across the weak-verifier pool. It gives distribution-free finite-time bounds on false-accept and false-reject rates and sublinear regret.
- (b) Per round/item routing. (c) **Verifier reliability is varied by choosing between verifiers, with guarantees.** It is not a function of repeated readings of one verifier.
- (d) **This is a direct competitor to H4's "local or frontier model, or ask a human".** H4 must cite it and position against it: ours predicts from item features and readings, while theirs is a bandit with guarantees. It is ten days old.

Worth it: **must cite in H4**.

### `roh-2026-cover`: CoVeR: Coverage-Based Routing of Verifier Calls in Agentic Retrieval
Daeyoung Roh, Donghee Han. arXiv:2609.26086 (2026, under review).
https://arxiv.org/abs/2609.26086 · **verified** (arXiv /abs/) · **abstract**.

- (a) A cheap embedding-coverage gate decides when to call an LLM verifier in agentic retrieval. Verifier calls fall 62–68% at matched EM. The evaluation protocol was fixed before the full run.
- (b) Per state. (c) The verifier is assumed reliable. Their closing line is useful: "matching a claim is far easier than deciding the claim is supported", which parallels our whether/where split.
- (d) H2 (cheap features) and H4 in the same RAG setting. Cite it.

Worth it: cite. It shares our setting.

### `banka-2026-pvp`: More Claims, Less Evidence: Bounded Verification of AI-Generated Digital Knowledge Artifacts
Feliks Bańka, Jarosław A. Chudziak. arXiv:2610.05547; ICADL 2026 (per the arXiv comment).
https://arxiv.org/abs/2610.05547 · **verified** · **abstract**.

- (a) A Bayesian model predicts the Predictive Value of a Pass as a function of verification budget and artifact size. It is tested on FEVER/FEVEROUS and yields the "minimum verification budget required to maintain a target PVP".
- (b) Artifact-level (number of claims checked). (c) The checker is assumed correct.
- (d) It is "budget needed to verify" in its title sense, but the budget is *coverage of claims*, not *repeated readings of one claim*. Cite it to pre-empt the reviewer who finds it.

Worth it: cite, and distinguish.

### `zhu-2026-judge-panels`: Stopping and Routing LLM Judge Panels
Bin Zhu, Yi Xie, Yanghui Rao. arXiv:2608.19802 (2026) *(venue unverified)*.
https://arxiv.org/abs/2608.19802 · **verified** (OpenAlex record with arXiv DOI) · **abstract**.

- (a) From a labelled audit set, it decides which judges to call and when to stop adding judges ("stop when validation gain falls below a threshold"). It compares against reliability juries and frugal cascades.
- (b) Slice-level, not item-level. (c) Judge roles (copy, complement, specialist) are estimated against a target. This is a correlation-aware composition, i.e. H3 territory.
- (d) Cite for H3/H4. Their "copies add no conditional information" is H3's correlated-errors condition.

Worth it: cite.

### `kumar-2026-verdicts`: All Verdicts are Not Equal: Rethinking LLM Judge Reliability
Vineet Kumar, Darshita Rathore, Anindya Moitra. arXiv:2610.12083; AACL-IJCNLP 2026 Main (per the arXiv comment).
https://arxiv.org/abs/2610.12083 · **verified** · **abstract**.

- It uses ten repetitions per condition and six frontier judges. "Verdicts change across identical replications at temperature zero". "The most deterministic judge achieves perfect consistency by trivially repeating incorrect verdicts, agreeing with ground truth only 51% of the time". "Reliability is item-specific rather than model-level".
- (a) No budget law. (b) Per item, descriptive. (c) The judge itself is the object, at a fixed budget.
- (d) **This is direct support for our reliability ≠ validity premise and for the per-item framing**, and the counterweight to `xue-2026` that the pre-proposal needs. It is days old, so `01-llm-as-judge-relevance.md` may not have it.

Worth it: **must cite** in the Research Opportunity paragraph.

### `zhu-2026-preregistered-failure`: Clean Engineering, Unstable Measurement: A Preregistered Reliability Failure of Black-Box LLM Observers on Shared Endpoints
Haoyuan Zhu (arXiv spells it "Haoyaun"), Jie Zhang. arXiv:2609.04198 (2026) *(venue unverified)*.
https://arxiv.org/abs/2609.04198 · **verified** · **abstract**.

- Pre-registered reliability gates failed. Same-window repeat rankings agreed at Spearman 0.400 (0.90 required). Byte-identical next-day replays agreed at 0.78 (0.99 required). "Self-hosting on batch-invariant kernels helped only while the server was quiet."
- (d) **This is a threat to H1's transfer claim**: readings from API frontier models are not exchangeable across days. H1's law should be stated for a pinned, self-hosted instrument, and the frontier arm needs a drift check. It is also a precedent for our pre-registration practice, and a cautionary one.

Worth it: cite in Evaluation/risks.

---

## CHALLENGES OUR NOVELTY CLAIM

The sentence under test:

> "Inference-scaling work predicts *task* success from the number of samples, assuming a reliable verifier [...]. Judge-reliability studies measure agreement at a fixed budget. No study we found predicts the reliability of the verification step itself as a function of budget, per item, and scores that prediction in advance."

Clause by clause:

1. **"assuming a reliable verifier" is too strong for the field as of 2025–26.** Imperfect verifiers are a mainstream topic, and several of these papers treat the verifier's own budget as a scaling axis:
   - `stroebl-2024-resampling-limits`, `dorner-2025-roc-n-reroll` and `gao-2022-overoptimization` model verifier imperfection explicitly.
   - `zhang-2024-genrm`, `singhi-2025-solve-verify` and `zhao-2025-sample-scrutinize` scale the **verifier's** budget (verifications per solution) and fit scaling laws to it (Singhi).

   A reviewer from this community will reject the first sentence as written. The accurate version: they model verifier imperfection as a fixed error rate, or verifier budget as a lever on *task* accuracy, but never the verdict's own reliability as the predicted quantity.

2. **"per item … reliability as a function of budget" already exists for answer aggregation.** Two papers estimate, per item and as samples arrive, the probability that the current aggregated verdict is stable, and stop on it:
   - `aggarwal-2023-adaptive-consistency`, via a Dirichlet posterior that the majority holds;
   - `chen-2026-mars`, via a high-probability guarantee that the early-stopped answer matches the full-budget vote.

   That is P(reliable verdict | budget), per item. The differences from us are real but must be stated precisely:
   - They target *answers* to math/code questions, not *verification verdicts on evidence*.
   - They are online posteriors, not predictions from item features before reading (H2).
   - They do not pre-register and score a forecast at a future budget (H1).
   - They do not handle graded or localisation ("where") verdicts.
   - They do not test whether reliability tracks correctness (our expert read).

3. **"predict before observing, per item" exists for task reward:**
   - `damani-2024-hard-to-think` (marginal reward per input and budget, predicted from x alone);
   - `zhao-2026-roi-reasoning`;
   - `wang-2024-dsc` (prior difficulty);
   - `han-2024-tale`.

   H2 is new only in its target (verifier reliability) and its feature set (document structure).

4. **"scores that prediction in advance" exists at population level for pass@k:**
   - `kazdan-2025-passk` extrapolates from a small budget and scores against 10⁴ withheld samples.
   - `schaeffer-2025-power-laws` backtests, though on synthetic data.

   They forecast task coverage, with an oracle, at population level. Our pre-registered, hashed forecast is stricter than anything here (none pre-register), but the design is not new.

5. **H4's policy has close 2026 competitors:**
   - `hajihashemi-2026-omvv` routes among verifiers of different cost with false-accept/reject guarantees;
   - `roh-2026-cover` gates verifier calls in agentic retrieval;
   - `nguyen-2026-uab` does pilot-then-allocate;
   - `zhu-2026-judge-panels` stops and routes judge panels.

   None predicts per-item verification reliability vs. readings. All are baselines or related work that H4 must name.

**Verdict.** Nothing found does the conjunction: *the reliability (and validity) of an LLM verification verdict on evidence support, as a function of the number of readings, per item, predicted from item features before reading, and scored against a pre-registered forecast.* The narrow claim survives. The sentence as written does not:
- its first clause mischaracterises the inference-scaling literature;
- its last clause, without the qualifier "of a *verification* verdict on evidence", is contradicted by adaptive-consistency and MARS for answer aggregation.

**Proposed wording:**

> "Inference-scaling work predicts *task* success from the number of samples. Where it models the verifier, it treats the verifier's error as fixed or as a lever on task accuracy [stroebl-2024; dorner-2025; singhi-2025]. Adaptive self-consistency stops when an *answer's* majority is stable [aggarwal-2023; chen-2026-mars], and input-adaptive allocation predicts *task* reward per input [damani-2024]. Judge-reliability studies measure agreement at a fixed budget. We found no study that predicts, per item and before reading, how the reliability of an LLM's *verification* verdict on retrieved evidence grows with the number of readings, scores that prediction against a pre-registered forecast, and tests it against expert correctness."

---

## What H1–H4 must change or add, from this area

- **H1.** Compare Spearman–Brown against a beta-binomial over items (`kazdan-2025-passk`, `schaeffer-2025-power-laws`). Answer `dorner-2025-roc-n-reroll`'s impossibility result by stating the exchangeability assumption. Pin the instrument (`zhu-2026-preregistered-failure`).
- **H2.** Cite Snell's limitations section as the stated gap, and `damani-2024` as the method precedent.
- **H3.** Cite `saad-falcon-2025-weaver` (correlated biases on hard examples; scores per verifier vs. more verifiers) and the Condorcet-independence literature. The novelty is the quantitative composition prediction.
- **H4.** Add adaptive-stopping baselines (Adaptive-Consistency, ESC, DSC, UAB or MARS) alongside fixed-k. Cite `chen-2024-more-calls` for why reliability can rise while accuracy falls on hard items. That result is the strongest argument for the expert read.

---

## Searched and did not find

Tools: export.arxiv.org API (`ti:`/`abs:` fielded queries; several later queries hit HTTP 429 and are marked), arxiv.org HTML search (`searchtype=all`/`abstract`/`title`), and OpenAlex `search=` with `from_publication_date ≥ 2022`.

**Zero relevant hits:**
- `abs:"Spearman-Brown" AND abs:LLM`: zero results (arXiv API).
- `abs:"Spearman-Brown" AND abs:"language model"`: zero results (arXiv API).
- `Spearman-Brown LLM` and `Spearman-Brown language model judge`: zero results (arXiv HTML search, all fields).
- `verification budget LLM judge per-item`: zero results (arXiv HTML).
- `abs:"scaling laws" AND abs:verification AND abs:"test-time" AND abs:judge`: zero results (arXiv API).
- OpenAlex `Spearman-Brown large language model judge`: no paper applying Spearman–Brown to LLM judges in the top 12.
- OpenAlex `predicting number of samples needed per question language model`: only `kadavath-2022` ("know what they know", which predicts P(correct) before answering, not budget) and unrelated work.

**Only adjacent work:**
- `scaling laws LLM-as-a-judge sampling` (arXiv HTML): one irrelevant hit.
- `test-time scaling judge` (title): J1 (2505.11875), JETTS (2504.15253), MCTS-Judge (2502.12468), DAJ (2601.22230). These use judges *as* test-time selectors. None predicts judge reliability vs. budget.
- `adaptive self-consistency LLM judge`, `number of samples LLM judge reliability` and `predict judge agreement before`: no judge-specific adaptive-sampling or budget-forecast paper.

**Lost to HTTP 429 and not recovered**, so these remain coverage gaps: `ti:"self-consistency" AND ti:judge`, `abs:"LLM-as-a-judge" AND abs:"repeated sampling" AND abs:variance`, `abs:"pass@k" AND abs:extrapolat`, and `ti:"inference scaling" AND ti:agents`. Rerun them before any "no study we found" sentence is final.

Not searched: the cascade/routing literature beyond FrugalGPT and AutoMix, and conformal/PAC stopping beyond `quach-2023`. See `08-adaptive-budget-escalation.md` for cascades and stopping rules.

## Established names for what we are describing

- **Spearman–Brown prophecy formula.** Psychometrics, for reliability vs. number of readings. In ML the per-item analogue is **pass_i@k = 1 − (1 − p_i)^k**, and the population analogue is the **beta-binomial** over items (`schaeffer-2025`, `kazdan-2025`). Use both names and map them onto each other.
- **Adaptive self-consistency / early-stopping self-consistency / adaptive sampling.** The established names for "read again or stop, per item, from agreement so far" (`aggarwal-2023`, `li-2024-esc`, `wang-2024-dsc`). Our H4 policy is an instance aimed at verification. Do not coin a new name.
- **Input-adaptive (or difficulty-adaptive) compute allocation; compute-optimal test-time scaling.** For H2 (`damani-2024`, `snell-2024`).
- **Generation–verification gap** (`saad-falcon-2025`) and **imperfect verifier / verifier ROC** (`dorner-2025`). For the motivation.
- **Condorcet jury theorem.** For H3's correlated-errors condition, and for the result that majority vote converges to the wrong answer when per-item accuracy is below 0.5 (`chen-2024-more-calls`). Recent LLM-specific instances, metadata verified: Lefort et al. 2024, arXiv:2409.00094, independence fails across LLMs; Sasahara et al. 2026, arXiv:2609.14438.
- **Model cascades / routing / deferral.** For H4's local→frontier→human escalation (`chen-2023-frugalgpt`, `aggarwal-2023-automix`, `hajihashemi-2026-omvv`).
- **Rational metareasoning / value of computation.** For the decision-theoretic framing of "is another reading worth it" (`desabbata-2024-metareasoning`).

### Further verified entries (metadata only, cited as leads)

- `kadavath-2022-know`: Language Models (Mostly) Know What They Know. Kadavath, Conerly, Askell, et al. (36 authors). arXiv:2207.05221. P(IK), a per-item prediction of correctness before answering. An H2 precedent of a different kind (self-knowledge, not features).
- `chen-2023-frugalgpt`: FrugalGPT. Lingjiao Chen, Matei Zaharia, James Zou. arXiv:2305.05176. An LLM cascade with a learned scorer.
- `aggarwal-2023-automix`: AutoMix: Automatically Mixing Language Models. Aggarwal, Madaan, Anand, Potharaju, et al. (13 authors). arXiv:2310.12963; NeurIPS 2024 (per the arXiv comment). Noisy few-shot self-verification with a POMDP router to a larger model. **This is the H4 "escalate on a noisy verifier signal" precedent.** The PDF text did not extract, so this rests on the abstract and recall.
- `quach-2023-conformal-lm`: Conformal Language Modeling. Quach, Fisch, Schuster, Yala, et al. arXiv:2306.10193; ICLR 2024 (per the arXiv comment). Sample until a calibrated stopping rule gives coverage guarantees. A conformal route to H4.
- `desabbata-2024-metareasoning`: Rational Metareasoning for Large Language Models. De Sabbata, Sumers, AlKhamissi, Bosselut, Griffiths. arXiv:2410.05563.
- `taubenfeld-2025-cisc`: Confidence Improves Self-Consistency in LLMs. Taubenfeld, Sheffer, Ofek, Feder, et al. arXiv:2502.06233; ACL 2025 Findings (OpenAlex DOI 10.18653/v1/2025.findings-acl.1030).
- `kim-2026-reasc`: Reliability-Aware Adaptive Self-Consistency. Kim, Yang, Min, Jung. arXiv:2601.02970; ACL 2026 Findings (OpenAlex DOI 10.18653/v1/2026.findings-acl.1085).
- `hu-2023-passuntil`: Predicting Emergent Abilities with Infinite Resolution Evaluation. Shengding Hu, Xin Liu, Xu Han, et al. arXiv:2310.03262. Sample until pass, for predicting tiny success rates. An ancestor of the Kazdan line.
- `montgomery-2025-budget-verif`: Budget-aware Test-time Scaling via Discriminative Verification. Montgomery, Tan, Chen, et al. arXiv:2510.14913.
- `fu-2025-deepconf`: Deep Think with Confidence. Fu, Wang, Tian, Zhao. arXiv:2508.15260.
- `ayyagari-2026-pinned`: Pinned and Still Unstable: Within-Judge Verdict Variance. arXiv:2609.33044 (OpenAlex). Temperature-zero judge flip rates of about 5% per item, and about 40% on close calls. A counter to `xue-2026`. Fuller treatment in `08-adaptive-budget-escalation.md`.

## COULD NOT VERIFY

- **"How do LLMs' success rates scale with attempts" as a title.** No paper with this title was found. The recalled phrase is most likely `schaeffer-2025-power-laws`. Queries: arXiv `ti:"pass@k" AND ti:scaling` (returned Kazdan 2510.05197 and Di et al. 2510.03199 "Best-of-Majority", neither under that title); OpenAlex `predicting number of samples needed per question`.
- **`levi-2024`, "A Simple Model of Inference Scaling Laws" (recalled as arXiv:2410.16377).** Not fetched, because of the 429s. Leave it out until it is checked.
- **Venues for Brown 2024, Snell 2024 and Lightman 2023.** The arXiv records carry no venue. They are recalled as ICLR 2025 (Snell) and ICLR 2024 (Lightman) but unconfirmed, so cite them as arXiv.
