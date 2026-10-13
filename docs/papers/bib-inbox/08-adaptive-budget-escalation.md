# Bib inbox 08 — Per-item budget prediction and adaptive stop/escalate policies for LLM inference and verification

> **Provenance.** Produced 2026-10-12 for the FY27 LDRD pre-proposal ("the pre-proposal" / "the draft" below; it is not in this repo). Metadata was verified as stated per entry (the status tag on each says how). arXiv API and OpenAlex HTTP 429 rate limits left the queries listed in the coverage caveat under *Searched and did not find* unrun; they are coverage gaps, not negative results.

Survey for the LDRD pre-proposal, hypotheses **H2** (cheap per-item features predict the required budget before reading) and **H4** (a stop/escalate policy for LLM verification beats fixed-k at matched cost). Written 2026-10-12.

The existing bib-inbox does not cover this area. `05-budget-eval-and-statistical-practice.md` has only `qian-2026-budget-aware-active-rag`, an adjacent entry that reports "simple baselines often match learned routers". It is relevant to the challenge section below and is not repeated here.

## Verification conventions

- **read**: I downloaded the arXiv PDF, extracted the text and read the method, the cost accounting and the limitations. Quotes are taken from that text.
- **verified (page)**: the venue was confirmed from the paper's own camera-ready header (e.g. "Published as a conference paper at ICLR 2024", a "PMLR 267" footer), from a PMLR or iclr.cc page, or from an ACL DOI in OpenAlex.
- **verified (record)**: metadata comes from OpenAlex or a search-result landing page only, and the full text was not read.
- Venues from arXiv `comments` fields are marked "(arXiv comment)". They are author-supplied, so check them once before the camera-ready.

Every entry answers the same five questions:

- **(a)** Is the stop/escalate decision about a **task answer** or a **verification/judgment**?
- **(b)** Is per-item cost or difficulty predicted **in advance**, and from which features?
- **(c)** Is the method compared to **fixed-k at matched budget**, and what is the cost accounting (tokens, $, latency, samples)?
- **(d)** Is a **human** part of the escalation?
- **(e)** What must H2/H4 **cite, adopt, or differentiate from**?

---

## 1. Adaptive sampling with a stopping rule ("read again or stop")

### `aggarwal-2023-adaptive-consistency`
**Let's Sample Step by Step: Adaptive-Consistency for Efficient Reasoning and Coding with LLMs.** Pranjal Aggarwal, Aman Madaan, Yiming Yang, Mausam. EMNLP 2023 (arXiv comment; ACL Anthology 2023.emnlp-main.761 as cited in AutoMix). arXiv:2305.11860. **read.**
- **(a)** Task answer. The method samples until the majority answer is stable under a Dirichlet, Beta, entropy or random stopping criterion.
- **(b)** No. Difficulty is revealed only by the samples drawn so far, so the method is purely sequential and posterior.
- **(c)** Compared to self-consistency (SC) at a fixed max k. It reports matched accuracy at fewer samples, and the cost is the number of samples.
- **(d)** No.
- **(e)** **Cite as the canonical "read again or stop" rule.** It is the obvious baseline that H4 must beat: H4 should be compared to an adaptive stopping rule as well as to fixed-k. Limitations, verbatim: *"The current stopping criterion, based on the majority element's stability in the sample set, may not always indicate sample agreement optimally"*, and *"Adaptive-Consistency is anticipated to fail where Self-Consistency fails."* The second sentence is our reliability ≠ validity point, stated by the authors themselves.

### `li-2024-esc`
**Escape Sky-high Cost: Early-stopping Self-Consistency for Multi-step Reasoning.** Yiwei Li, Peiwen Yuan, Shaoxiong Feng, Boyuan Pan, Xinglin Wang, Bin Sun, et al. ICLR 2024 (verified, page header). arXiv:2401.10480. **read.**
- **(a)** Task answer. Sampling proceeds in windows and stops when every answer in a window agrees (entropy = 0).
- **(b)** Partly. A "control scheme" uses the **first observation window** to predict expected cost and accuracy for different (window, max-L) settings. That is a per-dataset budget forecast built from early readings, the nearest thing here to our H1 "10 readings predict 30".
- **(c)** Yes. The paper explicitly reports "ESC can get higher accuracy under the same sampling costs" and compares against SC at the realised average L̂. The cost is the number of samples.
- **(d)** No.
- **(e)** **Cite and adopt** the window-entropy rule as a baseline. **Differentiate:** ESC's forecast is per dataset and targets a categorical answer; ours is per item and also covers set-valued *localisation*.

### `wang-2025-dsc`
**Make Every Penny Count: Difficulty-Adaptive Self-Consistency for Cost-Efficient Reasoning.** Xinglin Wang, Shaoxiong Feng, Yiwei Li, Peiwen Yuan, Yueqi Zhang, Chuyi Tan, et al. Findings of NAACL 2025 (arXiv comment). arXiv:2408.13457. **read.**
- **(a)** Task answer.
- **(b)** **Yes, a priori.** The LLM ranks question difficulty in batches of 8 (repeated 5×, ranks averaged). Hard questions are then pre-sampled to partition easy from hard, and each item's sample size is pre-allocated from its easier neighbours before adaptive extension. **This is the closest prior art to H2:** a difficulty estimate made before the budget is spent, which then sets the budget.
- **(c)** Compared to SC, ASC and ESC at a common cap of 40 samples, reporting cost and accuracy side by side (not strictly matched cost). The cost is **$ from API token prices**, and the cost of the difficulty estimation is included in the reported total.
- **(d)** No.
- **(e)** **Must cite for H2.** Differentiate on two points: our features are cheap document-structure features, not an LLM ranking pass, and our target is the verification budget, not the answer budget. Its limitations name a weakness that applies to us too: ranking *"declines when handling mixed-type problems"*, and the method *"demands an awareness of the test set"* (batch-relative ranking). Our features are absolute, per item, so H2 does not have this weakness, and we should say so.

### `ota-2026-cite`
**CITE: Anytime-Valid Statistical Inference in LLM Self-Consistency.** Hirofumi Ota, Naoto Iwase, Yuki Ichihara, Junpei Komiyama, Masaaki Imaizumi. arXiv:2605.05873 (2026), preprint. **read.**
- **(a)** Task answer, but the guarantee concerns the response distribution.
- **(b)** No.
- **(c)** Theoretical. It gives stopping-time rates and minimax bounds, with simulations and SC experiments; the cost is samples.
- **(d)** No.
- **(e)** **Cite for the reliability/validity framing and for a principled stopping rule.** The paper states, verbatim, that its certificate that a label is *"the unique mode of the model's response distribution"* is *"logically distinct from, and complementary to, answer correctness"*. That is exactly our P(reliable | budget) vs P(correct | budget) distinction, in someone else's words. Any H4 stopping rule that claims error control under data-dependent stopping should use or cite e-process methods like this one.

### `taubenfeld-2025-cisc` (brief)
**Confidence Improves Self-Consistency in LLMs.** Amir Taubenfeld, Tom Sheffer, Eran Ofek, et al. Findings of ACL 2025, DOI 10.18653/v1/2025.findings-acl.1030. **verified (record) only; not read.** It is a confidence-weighted vote that reduces the samples needed. Cite only as an alternative stopping signal, after reading it.

---

## 2. Cascades and routing ("switch to a stronger model")

### `chen-2023-frugalgpt`
**FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving Performance.** Lingjiao Chen, Matei Zaharia, James Zou. arXiv:2305.05176 (2023). **read.** I could not confirm a TMLR version (see COULD NOT VERIFY), so cite it as an arXiv preprint.
- **(a)** Task answer. A learned **generation scoring function** g(q, a) ∈ [0,1] (a small regression model on the query and answer) escalates to the next API when the score is below a threshold τᵢ.
- **(b)** No a-priori prediction. The score is computed after each answer.
- **(c)** Yes: "improve the accuracy over GPT-4 by 4% with the same cost", with cost in **$** per API pricing.
- **(d)** No.
- **(e)** **Cite as the origin of "LLM cascade".** The "switch model" arm of our H4 is a FrugalGPT cascade over judges. The paper has no limitations section.

### `aggarwal-2024-automix`
**AutoMix: Automatically Mixing Language Models.** Pranjal Aggarwal, Aman Madaan, Ankit Anand, Srividya Pranavi Potharaju, Swaroop Mishra, Pei Zhou, et al. NeurIPS 2024 (arXiv comment; OpenAlex NeurIPS record). arXiv:2310.12963. **read.**
- **(a)** Task answer. The small model **self-verifies** its own answer against the context with few-shot prompting, and a POMDP-based router decides whether to escalate.
- **(b)** No.
- **(c)** Cost–quality curves plus a new metric, **IBC (incremental benefit per cost)**, against fixed strong-only and weak-only models and against FrugalGPT. Cost is in model-call cost units.
- **(d)** No.
- **(e)** **Cite.** Its escalation signal is itself a noisy *verification*, which is the closest cascade design to ours. Its limitations restrict self-verification to *"context-grounded reasoning"* and exclude factual QA. Evidence verification is a context-grounded task, so AutoMix is in-scope prior art, not a distant cousin. **Adopt IBC** as a secondary metric.

### `yue-2024-mot-cascades`
**Large Language Model Cascades with Mixture of Thoughts Representations for Cost-efficient Reasoning.** Murong Yue, Jie Zhao, Min Zhang, Liang Du, Ziyu Yao. ICLR 2024 (verified, page header). arXiv:2310.03094. **read.**
- **(a)** Task answer. The cascade escalates from a weak to a strong LLM when the weak model's **answer consistency** across samples is low. Two variants exist: a vote threshold, and a cross-representation check (CoT vs PoT majority answers).
- **(b)** No; the signal comes from the weak model's samples.
- **(c)** Compared to GPT-4-only at matched accuracy, at roughly 40% of the cost; the cost is **$**.
- **(d)** No.
- **(e)** **This is H4's "read again, then switch model" policy, published, for task answers.** It also bears on H3: mixing *thought representations* raises the value of the consistency signal because the errors are less correlated. Cite in both places, and differentiate on the verification target and on per-item prediction.

### `ong-2025-routellm`
**RouteLLM: Learning to Route LLMs with Preference Data.** Isaac Ong, Amjad Almahairi, Vincent Wu, Wei-Lin Chiang, Tianhao Wu, Joseph E. Gonzalez, et al. ICLR 2025 (verified, page header). arXiv:2406.18665. **read.**
- **(a)** Task answer, routing between a strong and a weak model.
- **(b)** **Yes, a priori from the query text alone**, using BERT, matrix-factorisation or causal-LLM classifiers trained on preference data.
- **(c)** Yes, matched-cost metrics: **CPT** (percentage of calls to the strong model needed to reach a performance threshold) and **APGR** (average performance gap recovered); the cost is the share of strong-model calls.
- **(d)** No.
- **(e)** **Cite for H2 as "per-item routing predicted before inference"**, and **adopt CPT/APGR-style metrics** for H4. Differentiate: this is one-shot routing, not sequential; features are text embeddings, not structural features; and there is no verification target.

### `gupta-2024-lm-cascades-token`
**Language Model Cascades: Token-level Uncertainty and Beyond.** Neha Gupta, Harikrishna Narasimhan, Wittawat Jitkrittum, Ankit Singh Rawat, Aditya Krishna Menon, Sanjiv Kumar. ICLR 2024 (verified, iclr.cc poster page). arXiv:2404.10136. **read.**
- **(a)** Task answer (generation).
- **(b)** Post-hoc deferral rules trained on token-level uncertainty quantiles, optionally with embeddings. Not in advance.
- **(c)** Deferral curves (quality vs fraction deferred).
- **(d)** No.
- **(e)** **Cite for the formal "deferral rule" vocabulary** (Chow's rule) and for its warning that sequence-level confidence has length bias. Our document-length feature in H2 may interact with the same bias in a judge's confidence.

### `kolawole-2025-abc`
**Agreement-Based Cascading for Efficient Inference.** Steven Kolawole, Don Dennis, Ameet Talwalkar, Virginia Smith. TMLR, 07/2025 (verified, page header). arXiv:2407.02348. **read** (abstract and method).
- **(a)** Task answer. Agreement within an *ensemble* of cheap models is the deferral signal.
- **(b)** No.
- **(c)** Cost–accuracy against single models and cascades, in FLOPs, $ and latency.
- **(d)** No.
- **(e)** **Cite for H3/H4.** "Agreement among readings as the escalation signal" is published. Our addition is the item-level *budget law* for that agreement.

### `chuang-2025-self-ref` (brief)
**Learning to Route LLMs with Confidence Tokens.** Yu-Neng Chuang, Prathusha Kameswara Sarma, Parikshit Gopalan, John Boccio, Sara Bolouki, et al. ICML 2025, PMLR 267 (verified, page footer). arXiv:2410.13284.
- **(a)** Task answer.
- **(b)** A fine-tuned confidence token, read after generation.
- **(c)** Routing and rejection curves.
- **(d)** No.
- **(e)** Cite as one more confidence-driven router.

### `nie-2024-online-cascade` (brief)
**Online Cascade Learning for Efficient Inference over Streams.** Lunyiu Nie, Zhimin Ding, Erdong Hu, Christopher Jermaine, Swarat Chaudhuri. ICML 2024, PMLR 235 (verified, page footer). arXiv:2402.04513.
- **(a)** Task answer. It learns the deferral policy online, as an episodic MDP solved by imitation learning.
- **(b)** No.
- **(c)** Accuracy vs inference cost.
- **(d)** Explicitly **no humans**; the method is designed to "allow learning without any human annotations".
- **(e)** Cite as the RL/MDP formulation of a cascade policy.

---

## 3. Per-item budget prediction before inference (H2)

### `damani-2024-learning-how-hard`
**Learning How Hard to Think: Input-Adaptive Allocation of LM Computation.** Mehul Damani, Idan Shenfeld, Andi Peng, Andreea Bobu, Jacob Andreas. arXiv:2410.04707 (2024). **read.** I believe it appeared at ICLR 2025 but could not verify that (see COULD NOT VERIFY).
- **(a)** Task answer: adaptive best-of-k with a reward-model reranker, and weak/strong decoder routing.
- **(b)** **Yes, explicitly in advance, per item.** It predicts the **marginal reward of each extra unit of compute** for each query, from the base LM's **hidden states** (2-layer MLP probe or LoRA), trained on empirical success rates at multiple budgets.
- **(c)** **Yes, at matched average budget** against uniform best-of-k; cost is samples per query or the fraction of strong-decoder calls. The predictor's own cost is called negligible but is not counted.
- **(d)** No.
- **(e)** **The single most important H2 citation.** "Predict per item, before spending, how much budget the item needs, and allocate to beat uniform-k at matched budget" is this paper's thesis. Its stated limitation is the verifier assumption (*"we assumed that we have access to a verifier in the Math experiments"*). That is precisely where we come in: we study the budget of the verifier itself, where no ground-truth verifier exists. Differentiate on that, and on cheap pre-read structural features vs hidden-state probes, which require running the model.

### `manvi-2024-adaptive-inference-compute`
**Adaptive Inference-Time Compute: LLMs Can Predict if They Can Do Better, Even Mid-Generation.** Rohin Manvi, Anikait Singh, Stefano Ermon. arXiv:2410.02725 (2024), preprint. **read** (method).
- **(a)** Task answer. "Capability-aware self-evaluation": the model predicts P(restarting would do better), and this is used to decide whether to sample more and to prune mid-generation.
- **(b)** Partly. The prediction is made after the first sample or mid-generation, not from pre-read features.
- **(c)** Compared to fixed best-of-N with a reward model; cost is samples and FLOPs.
- **(d)** No.
- **(e)** Cite next to Damani as the "self-predicted marginal value of another sample" line.

### `moros-daval-2024-difficulty-meta-features`
**Language Task Difficulty Prediction Through LLM-Annotated Meta-Features.** Yael Moros-Daval, Fernando Martínez-Plumed, José Hernández-Orallo. *Frontiers in Artificial Intelligence and Applications* (IOS Press) 2024, DOI 10.3233/FAIA240770; I infer the venue is ECAI 2024 from the series. **verified (record); abstract only.**
- **(a)** Neither; it predicts instance difficulty for evaluation.
- **(b)** **Yes, in advance, from LLM-annotated linguistic meta-features.** It reports R² of 0.68–0.83 on MMLU and **R² = −0.07 on LSAT**.
- **(c)** Not a budget study.
- **(d)** No.
- **(e)** **Cite for H2 as the "predictable AI / assessor" lineage** (Hernández-Orallo group). The LSAT failure is a useful warning: interpretable pre-read features predict difficulty well for some tasks and not at all for others. H2 should pre-register a floor below which the features count as non-predictive.

### `rodriguez-2021-irt-leaderboards`
**Evaluation Examples are not Equally Informative: How should that change NLP Leaderboards?** Pedro Rodriguez, Joe Barrow, Alexander Hoyle, John P. Lalor, Robin Jia, Jordan Boyd-Graber. ACL 2021, DOI 10.18653/v1/2021.acl-long.346 (verified, record).
- **(a)** Neither; this is an IRT model of item difficulty and discriminability.
- **(b)** Difficulty is *estimated from responses*, not predicted from features.
- **(c)** n/a.
- **(d)** n/a.
- **(e)** Cite as the source of "item difficulty" as a latent per-item parameter. **Adopt the IRT vocabulary**: our "per-item required budget" is a difficulty parameter in a response model.

### `maia-polo-2024-tinybenchmarks`
**tinyBenchmarks: evaluating LLMs with fewer examples.** Felipe Maia Polo, Lucas Weber, Leshem Choshen, Yuekai Sun, Gongjun Xu, Mikhail Yurochkin. ICML 2024 (arXiv comment). arXiv:2402.14992. **read** (limitations).
- **(a)** Neither. It selects which *items* to evaluate, not how many readings each needs.
- **(b)** IRT item parameters fitted on prior models.
- **(c)** Estimation error vs the number of items.
- **(d)** No.
- **(e)** Cite as IRT for LLM evaluation. Its limitation is a transfer threat that also applies to our Year-3 test: *"we anticipate larger performance estimation errors for models that fail on simple questions while answering complicated ones correctly"* under distribution shift. A new model generation can reorder item difficulty.

### `hofmann-2025-fluid` (brief)
**Fluid Language Model Benchmarking.** Valentin Hofmann, David Heineman, Ian Magnusson, Kyle Lo, Jesse Dodge, et al. COLM 2025 (verified, page header). arXiv:2509.11106.
- **(a)** Neither; this is IRT-based computerized adaptive testing (CAT) over items.
- **(b)** IRT difficulty.
- **(c)** **Dynamic stopping:** testing stops once the standard error of the ability estimate is small enough.
- **(d)** No.
- **(e)** Cite for "adaptive testing with a standard-error stopping rule". The established psychometric name for our "stop when the estimate is good enough" is the **CAT stopping rule**.

### `balkir-2026-cat-continuous` (brief)
**Confident Rankings with Fewer Items: Adaptive LLM Evaluation with Continuous Scores.** Esma Balkır, Alice Pernthaller, Marco Basaldella, José Hernández-Orallo, Nigel Collier. arXiv:2601.13885 (2026). **read** (abstract).
- Extends IRT-CAT to continuous scores, *including LLM-as-judge scores*, with adaptive stopping.
- **(a)** Evaluation of models, not of items.
- **(b)** IRT.
- **(c)** Items used.
- **(d)** No.
- **(e)** Cite as evidence that IRT-CAT now reaches judge-scored evaluation. Differentiate: it allocates *items across models*; we allocate *readings within an item*.

---

## 4. Selective evaluation, abstention and escalation for LLM *judges* (H4's closest neighbours)

### `jung-2025-trust-or-escalate`
**Trust or Escalate: LLM Judges with Provable Guarantees for Human Agreement.** Jaehun Jung, Faeze Brahman, Yejin Choi. ICLR 2025, oral (verified via the iclr.cc proceedings and oral/poster pages). arXiv:2407.18370. **read.**
- **(a)** **Verification/judgment.** This is pairwise preference evaluation. The judge abstains when its confidence is below a threshold calibrated for a guaranteed human-agreement level. **Cascaded Selective Evaluation** escalates from cheap judges (e.g. Mistral-7B) to stronger ones (GPT-4) only when needed.
- **(b)** **No.** The paper states that confidence comes from the judge's own outputs at inference time. "Simulated Annotators" ensembles N ≤ 5 prompts with different in-context examples, i.e. repeated readings that vary the prompt. Length ratio and token overlap are checked only post hoc, to show that abstention is not driven by them.
- **(c)** It reports **relative API cost** (GPT-4-only = 1), with savings up to 78.5% and 87.4%, compared at **matched target agreement, not matched cost**. Baselines are GPT-4 without abstention, heuristic selection and point-estimate calibration. There is no fixed-k-readings baseline.
- **(d)** **No.** Abstained items are left unevaluated, and human labels are used only for calibration and analysis. Coverage below 100% is reported.
- **(e)** **The most important H4 citation; it partially anticipates H4.** "Escalate a judge's verdict to a stronger model only when needed, with a guarantee on agreement with humans" is published, peer-reviewed, at ICLR. H4 must cite it and **adopt the name "cascaded selective evaluation"**, extended to "cascaded selective verification". Differentiate on four points:
  1. We add *re-reading the same judge* as an action with a budget law. Jung varies only the model and uses a fixed N.
  2. We add *ask a human* as an action with a priced cost.
  3. We predict per-item budget *before* reading (H2).
  4. Our target includes set-valued localisation ("where"), not just binary preference.
- The paper has no limitations section.

### `badshah-2026-scope`
**SCOPE: Selective Conformal Optimized Pairwise LLM Judging.** Sher Badshah, Ali Emami, Hassan Sajjad. ICML 2026, PMLR 306 (verified, page footer). arXiv:2602.13110. **read** (abstract and introduction).
- **(a)** Judgment (pairwise). A conformal acceptance threshold bounds the error among non-abstained judgments (empirical FDR ≈ 0.10 at α = 0.10). **BPE** queries both orderings, i.e. two readings per item.
- **(b)** No.
- **(c)** Coverage at a fixed risk level, "up to 2.4× more judgments under the same risk constraint".
- **(d)** No.
- **(e)** Cite. This is the conformal, risk-controlled version of "stop when the judge is confident". If H4 claims a target accuracy, conformal risk control (Jung, SCOPE) is the standard of guarantee reviewers will expect.

### `yadkori-2024-conformal-abstention`
**Mitigating LLM Hallucinations via Conformal Abstention.** Yasin Abbasi Yadkori, Ilja Kuzborskij, David Stutz, András György, Adam Fisch, Arnaud Doucet, et al. arXiv:2405.01563 (2024), preprint. **read** (method).
- **(a)** Task answer: the model abstains with a bounded hallucination rate.
- **(b)** No. The score is a **self-consistency match count over k sampled responses**, with similarity judged by the LLM itself.
- **(c)** Abstention rate vs error rate; k is fixed.
- **(d)** No.
- **(e)** Cite as "repeated readings → conformal stop/abstain". Differentiate: k is fixed, not adaptive per item.

### `lail-markham-2026-cost-effective-judge`
**On Cost-Effective LLM-as-a-Judge Improvement Techniques.** Ryan Lail, Luke Markham. ICML 2026 Workshop on Statistical Frameworks for Uncertainty in Agentic Systems (verified, page header). arXiv:2604.13717. **read.**
- **(a)** **Judgment** (RewardBench 2). It compares ensemble scoring ("Monte Carlo averaging over per-call noise"), criteria injection, calibration context, and **adaptive model escalation** that uses per-response score variance as the uncertainty signal.
- **(b)** No.
- **(c)** **Yes: cost–accuracy Pareto frontier**, across GPT and Claude families, with ensemble sizes up to k = 8.
- **(d)** No.
- **(e)** **Challenging result:** *adaptive model escalation "improve[s] over baseline but [is] dominated by criteria + ensembling on the cost–accuracy Pareto frontier."* That is a published case in which a judge escalation policy **loses** to fixed-k ensembling at matched cost, which is the opposite of H4. It is workshop-level, uses short items (about 576 tokens) and does not predict anything per item, but H4 must engage with it. Its limitations list document-length inputs as untested, which is exactly our regime.

### `zhu-2026-stopping-routing-judge-panels`
**Stopping and Routing LLM Judge Panels.** Bin Zhu, Yi Xie, Yanghui Rao. arXiv:2608.19802 (2026), preprint. **read** (abstract and experimental matrix).
- **(a)** **Judgment.** This is panel design. From a labelled audit set, it classifies judges as copies, complements or specialists, then drops copies, routes specialists to slices, and **stops adding judges when validation gain falls below a threshold**.
- **(b)** **Partly yes.** Routing uses **slices declared from metadata available before routing** (problem level, subset, unit-test result). That is "cheap pre-read features select the verification spend", at slice level rather than per item.
- **(c)** Compared to single judges, flat panels, reliability juries and **frugal cascades**, with declared judge costs.
- **(d)** Human labels are audit-only.
- **(e)** **Cite. It overlaps H3 and H4 directly** (judge diversity, stopping, cost). Differentiate: its stopping is panel construction per *batch*, decided from an audit set. It is not sequential per item, does not re-read the same judge, and has no human action.

### `verga-2024-poll` (brief)
**Replacing Judges with Juries: Evaluating LLM Generations with a Panel of Diverse Models.** Pat Verga, Sebastian Hofstätter, Sophia Althammer, Yixuan Su, Aleksandra Piktus, et al. arXiv:2404.18796 (2024). **verified (record).**
- **(a)** Judgment.
- **(b)** No.
- **(c)** A panel of small diverse models outperforms one GPT-4 judge at lower cost.
- **(d)** No.
- **(e)** Cite under H3 (mixing families). It is the "jury" baseline that Zhu et al. compare against.

### `ayyagari-2026-pinned-unstable` (brief; relevant to the H1 counterclaim)
**Pinned and Still Unstable: Within-Judge Verdict Variance and the Noise Floor of LLM-as-Judge Leaderboards.** Krishna Chytanya Ayyagari. arXiv:2609.33044 (2026). **read** (abstract).
- Four frontier temperature-zero judges, pinned to a constant serving-reported model version on one enterprise cloud platform, over Arena-Hard, AlpacaEval 2 and MT-Bench. Re-running identical inputs gives per-item flip rates of **roughly 5% on average** (per-judge magnitude spanning a 40× range, **0.13% to nearly 10%**) and **about 40% on the close-call items** that decide leaderboard margins (abstract, v2; re-fetched 2026-10-12). The abstract does not state the number of re-runs.
- *Correction 2026-10-12:* an earlier version of this entry quoted "3–8% across 10 re-runs" and "30–38%"; neither figure is in the abstract.
- **(e)** This is not our area, but it bears on the "near-perfect intra-model consistency" counterclaim the draft flags as `xue-2026`. Within-judge variance is concentrated on hard items, which supports H2's premise that budget need is item-dependent. See `07-judge-reliability-budget.md` for H1.

---

## 5. Escalation to humans: learning to defer, active inference, audit allocation

### `madras-2018-learning-to-defer`
**Predict Responsibly: Improving Fairness and Accuracy by Learning to Defer.** David Madras, Toniann Pitassi, Richard Zemel. NeurIPS 2018 (verified, OpenAlex record). arXiv:1711.06664.
- **(a)** Task prediction.
- **(b)** It learns a per-item defer decision from input features.
- **(c)** n/a.
- **(d)** **Yes.** The paper introduces "learning to defer" to a downstream human decision-maker.
- **(e)** **Cite as the origin of the term.** The "ask a human" arm of H4 is learning-to-defer.

### `mozannar-2020-l2d-consistent`
**Consistent Estimators for Learning to Defer to an Expert.** Hussein Mozannar, David Sontag. ICML 2020, PMLR 119:7076–7087 (verified, PMLR page).
- **(a)** Task prediction.
- **(b)** Yes. A rejector learned from input features predicts per item whether the expert is better.
- **(c)** n/a. Deferral cost is a parameter of the loss.
- **(d)** **Yes.**
- **(e)** Cite as the standard L2D surrogate loss. If H4's human arm is *learned*, this is the formalism.

### `ren-2023-knowno`
**Robots That Ask For Help: Uncertainty Alignment for Large Language Model Planners.** Allen Z. Ren, Anushri Dixit, Alexandra Bodrova, Sumeet Singh, Stephen Tu, Noah Brown, et al. CoRL 2023, oral (arXiv comment). arXiv:2307.01928. **read** (limitations).
- **(a)** Task action (planning). Conformal prediction over LLM option scores; the robot **asks a human whenever the prediction set is not a singleton**.
- **(b)** No.
- **(c)** Task success vs **human-help rate** at a guaranteed success level.
- **(d)** **Yes.**
- **(e)** **Cite as the canonical "LLM agent asks a human, with a statistical guarantee".** Its limitations name a gap H4 can fill: *"for the task guarantee to hold, the human needs to faithfully provide help ... Future work could also incorporate human modeling/error"*. Our expert two-reader design measures human error rather than assuming it is zero.

### `zrnic-2024-active-statistical-inference`
**Active Statistical Inference.** Tijana Zrnic, Emmanuel J. Candès. ICML 2024, PMLR 235:62993–63010, oral (verified via the PMLR page in search results). arXiv:2403.03208. **read** (abstract).
- **(a)** Neither task answers nor single verdicts. The target is **population estimation**: a model's uncertainty decides which items get gold labels within a label budget, with valid confidence intervals.
- **(b)** Yes. The per-item sampling probability is a function of model uncertainty.
- **(c)** Effective sample size vs uniform labelling at a fixed label budget.
- **(d)** **Yes.** The labels are human or expensive.
- **(e)** Cite as the statistical foundation for "spend the human budget where the model is uncertain".

### `gligoric-2025-confidence-driven-inference`
**Can Unconfident LLM Annotations Be Used for Confident Conclusions?** Kristina Gligorić, Tijana Zrnic, Cinoo Lee, Emmanuel J. Candès, Dan Jurafsky. NAACL 2025, DOI 10.18653/v1/2025.naacl-long.179 (verified, OpenAlex). arXiv:2408.15204. **read** (method and limitations).
- **(a)** **Annotation/verification.** The LLM labels everything, and its **verbalized confidence** chooses which items receive human labels. Estimates stay valid.
- **(b)** Yes. Per-item LLM confidence sets the probability of human escalation.
- **(c)** Coverage and effective sample size vs baselines at a fixed human budget.
- **(d)** **Yes, this is its point.**
- **(e)** **Cite for H4's human arm.** Differentiate: it targets dataset-level estimands, not per-item verdicts, and the LLM side is a single reading. Its limitations, *"human annotations are biased ... reasonable annotators can disagree"*, again support our two-expert-reader design.

### `lail-2026-decomposing-judge-uncertainty`
**Decomposing LLM-Judge Uncertainty to Target Expert Labels.** Ryan Lail. arXiv:2609.06444 (2026), preprint. **read.**
- **(a)** **Judgment.** The question is which items to escalate to experts.
- **(b)** Yes, per item: a Bayesian regression on labels already collected separates **aleatoric** uncertainty (real expert disagreement) from **epistemic** uncertainty (the judge's ignorance), with "no sampling or further judge calls".
- **(c)** Error removed per expert label vs entropy ranking and **fewest-labels-first**.
- **(d)** **Yes.**
- **(e)** **Cite, and adopt its baseline.** Its own limitation is the most useful sentence in this survey for H4's design: *"when trust is flat our ranking follows label counts, exactly what the fewest-labels-first baseline does, so the baseline ties us"*. **Add "fewest-readings-first" as an H4 baseline.** The aleatoric/epistemic split also maps onto our whether/where finding: low agreement on "where" may be partly irreducible (aleatoric), and if so no budget fixes it. H2 must allow for an "unbounded budget" class.

### `zhang-2026-aura` (brief)
**AURA: Adaptive Uncertainty-aware Refinement for LLM-as-a-Judge Auditing.** Zilong Zhang, Yi-Ting Hung, Weiyi He, Junxi Zhang, Lei Ding, Chi-Kuang Yeh. arXiv:2606.19714 (2026). **read** (abstract).
- **(a)** Judgment, pairwise auditing.
- **(b)** Per-item uncertainty, refined iteratively.
- **(c)** Not budget-matched as far as I read.
- **(d)** **Yes.** It prioritises uncertain comparisons for human review and stops on flip-rate thresholds.
- **(e)** Cite as a recent "judge → human" escalation loop.

### `kim-2026-how-many-human-reviews` (brief)
**Augmenting Human Evaluation with LLM Judges: How Many Human Reviews Do You Need?** Jane Paik Kim. arXiv:2605.16354 (2026). **read** (abstract).
- **(a)** Judgment (evaluation).
- **(b)** Allocation by evaluation *type*: "allocating more human ratings for types of evaluations where the predictability of LLM ratings is not high".
- **(c)** Power-based sample sizing.
- **(d)** **Yes.**
- **(e)** Cite. It is type-level (stratum) human allocation. H2's features would define such strata.

### `zavattari-2026-audit-budget` (brief)
**One Human, N Agents: Audit-Budget Allocation for LLM Agent Fleets under Miscalibrated, Correlated Confidence.** Cesare Zavattari, Alessandro Tommasi, Giuseppe Prencipe. arXiv:2607.28317 (2026). **read** (abstract and limitations).
- **(a)** Agent outputs, audited by one human.
- **(b)** It relies on self-reported confidence.
- **(c)** Budgeted audit B ≪ N.
- **(d)** **Yes.**
- **(e)** **Challenging result:** five open-weight LLMs gave "operationally useless (near-constant) confidence", and past a miscalibration threshold confidence-ranked auditing is *worse than random*. Also, *"cross-family correlation is not low—shared difficulty dominates lineage"*, which threatens H3's assumption that families' errors are uncorrelated. H4 should not use verbalized confidence as its escalation signal without a check.

### `sheng-2008-get-another-label` (pre-neural ancestor)
**Get Another Label? Improving Data Quality and Data Mining Using Multiple, Noisy Labelers.** Victor S. Sheng, Foster Provost, Panagiotis G. Ipeirotis. KDD 2008 (verified, OpenAlex record and NYU archive). **Not read in full.**
- **(a)** Label acquisition, the human analogue of re-reading.
- **(b)** **Selective repeated-labeling.** Label uncertainty and model uncertainty choose which items get more labels.
- **(c)** Label quality vs labelling cost.
- **(d)** The labellers are humans (crowd).
- **(e)** **Cite as the ancestor of "read again only where needed".** H4's "read again" arm is selective repeated labelling with LLM readers. The crowdsourcing literature should be credited rather than rediscovered.

### `kossen-2021-active-testing` (brief)
**Active Testing: Sample-Efficient Model Evaluation.** Jannik Kossen, Sebastian Farquhar, Yarin Gal, Tom Rainforth. ICML 2021 (verified, OpenAlex record).
- Chooses which test items to label, with unbiased estimators.
- **(d)** Labels are human.
- **(e)** Cite next to Zrnic & Candès.

---

## 6. Budget-aware agents

### `liu-2025-bats`
**Budget-Aware Tool Use Enables Effective Agent Scaling.** Tengxiao Liu, Zifeng Wang, Jin Miao, I-Hung Hsu, Jun Yan, Jiefeng Chen, et al. COLM 2026 (arXiv comment). arXiv:2511.17006. **read** (method and limitations).
- **(a)** Task (web-search agents), including a **verification step**: BATS "dynamically adapt[s] its planning and verification strategy" to the remaining budget.
- **(b)** No. A Budget Tracker reports the remaining budget, and nothing is predicted per item.
- **(c)** **Yes.** A unified cost metric over tokens and tool calls, compared against non-budget-aware scaling.
- **(d)** No.
- **(e)** **Cite as the agent-side framing.** Its limitations, verbatim, name the gap: *"we do not explore how an agent should allocate its available resources ... models often underestimate their actual resource consumption ... Developing accurate resource estimation and principled budget allocation strategies is a promising direction for future work."* H2 and H4 are that future work, for the verification sub-step. This is the strongest "limitations name our gap" quote found in this survey.

---

## CHALLENGES: OUR NOVELTY / H4 IS ALREADY DONE

**Frank verdict.** **H4 as worded is not novel.** "An adaptive policy reaches target accuracy at lower cost than a fixed number of samples" is the headline result of at least seven peer-reviewed papers:

- for the *read-again/stop* arm: Adaptive-Consistency, ESC and DSC;
- for the *switch-model* arm: FrugalGPT, AutoMix, MoT cascades and RouteLLM;
- for *LLM judges specifically*: **Trust or Escalate (ICLR 2025)**, a cascade from cheaper to stronger judges with a human-agreement guarantee.

The *ask-a-human* arm is learning-to-defer (Madras 2018; Mozannar & Sontag 2020), KnowNo (Ren 2023) and confidence-driven inference (Gligorić 2025). A reviewer who knows Jung et al. will read H4 as "Trust-or-Escalate plus re-sampling plus a human tier". The name for that is **a cascade with deferral**, and its stopping arm is **adaptive sampling with a stopping rule**.

**H2 is partly anticipated as well.** Per-item budget prediction before spending is the thesis of Damani et al. 2024 (hidden-state probe → marginal value of compute, beating uniform-k at matched budget) and of DSC (a-priori LLM difficulty ranking → allocated sample size). RouteLLM predicts routing from query text alone. Moros-Daval et al. predict difficulty from annotated meta-features.

**Direct counter-evidence to H4's expected outcome:**

1. **Lail & Markham 2026.** For LLM judges, adaptive model escalation is *dominated* by fixed-k ensembling plus criteria injection on the cost–accuracy frontier.
2. **Lail 2026.** A sophisticated escalation ranking *ties* the trivial fewest-labels-first rule on real data.
3. **`qian-2026` (already in bib 05).** Simple baselines often match learned routers.
4. **Zavattari et al. 2026.** Open-model confidence is near-constant, so confidence-ranked escalation can be worse than random. Cross-family errors are correlated through shared difficulty, which also threatens H3.

Together these say that the burden is on H4 to beat **strong** baselines, not fixed-k. As drafted, "fixed number of readings, one frontier reading, majority vote" is a straw-man baseline set by 2026 standards.

**What survives as novel.** It is defensible, but only if stated narrowly:

1. The object whose budget is predicted is the **verification step** (an evidence-support judgment), where no ground-truth verifier exists. Damani's stated limitation is precisely the verifier assumption, and BATS names "resource estimation" as future work.
2. The predictor uses **cheap pre-read document-structure features** (length, section depth, candidate-sentence count) and runs *before any reading*. Damani's probe requires a forward pass; DSC requires batch-relative LLM ranking, a limitation its authors acknowledge.
3. The outputs are **set-valued localisations ("where")**. Adaptive-Consistency, ESC and CITE define stopping only for a categorical mode, and none of them handles a growing evidence union that has not converged.
4. The study **scores the budget prediction prospectively** (pre-registered) and tests **reliability-vs-validity against experts**. Adaptive-Consistency and CITE concede that their stopping certifies stability or the mode, not correctness, and nobody in this literature tests whether a stability-based stop predicts expert-judged correctness for verification.
5. The four actions (re-read the same judge, switch family, ask a human, stop) sit **in one priced action space with per-item costs**. Each pair has been done; I found no paper with all four for a verification step.

**Required baseline set for H4:**
- fixed-k;
- **ESC / Adaptive-Consistency** (stopping only);
- **Trust-or-Escalate-style cascaded selective evaluation** (escalation only, fixed N);
- **fixed-k ensembling at matched cost** (the Lail & Markham winner);
- **fewest-readings-first** for the human arm (the Lail 2026 tie);
- one frontier reading.

The cost of the H2 predictor must be counted, as DSC does and Damani does not.

## Established names (adopt, do not coin)

| Our phrase | Established name | Source |
|---|---|---|
| "read again or stop" | **adaptive sampling with a stopping rule**; *adaptive-consistency*; *early-stopping self-consistency* | Aggarwal 2023; Li 2024 |
| "switch to a stronger model" | **LLM / model cascade** with a **deferral rule** | Chen 2023; Gupta 2024; Varshney & Baral 2022 (EMNLP; record only) |
| "the judge stops without a verdict / escalates" | **selective evaluation**; **cascaded selective evaluation**; *abstention* | Jung 2025; Badshah 2026 |
| "ask a human" | **learning to defer** (to an expert) | Madras 2018; Mozannar & Sontag 2020 |
| "decide where human labels go" | **active statistical inference**; *confidence-driven inference*; *selective repeated-labeling* | Zrnic & Candès 2024; Gligorić 2025; Sheng 2008 |
| "per-item required budget, predicted in advance" | **input-adaptive compute allocation**; *difficulty-adaptive*; *item difficulty* (IRT); *assessor* | Damani 2024; Wang 2025; Rodriguez 2021; Moros-Daval 2024 |
| "stop when the estimate is good enough" | **CAT stopping rule** (standard-error criterion); *anytime-valid* / e-process stopping | Hofmann 2025; Ota 2026 |
| cost-normalised comparison | **CPT / APGR**, **IBC** | Ong 2025; Aggarwal 2024 |

**Recommended name for the H4 policy:** a *cascaded selective verification* policy, i.e. **a cascade with deferral (to a stronger judge or to a human) whose per-stage sample size follows an adaptive stopping rule seeded by an a-priori budget prediction**. In one phrase: **"adaptive cascade with deferral"**. It is *both* a cascade and adaptive sampling with a stopping rule. The novel component is the a-priori per-item budget prior for a verification step, not the policy shape. Do not call it a "stop/escalate policy" without citing Jung et al.

## Searched and did not find

These are the coverage evidence. All were run on 2026-10-12.

- OpenAlex `search="LLM judge adaptive number of samples per item stopping"`. No paper adapts the number of *same-judge* readings per item for verification; the nearest hits are Zhu 2026 (panel-level) and Balkır 2026 (items across models).
- OpenAlex `search="LLM-as-a-judge uncertainty repeated sampling escalate human"`. Found Lail 2026, Lail & Markham 2026, Zhu 2026 and SCOPE; none combines re-read, switch model and human in one policy.
- OpenAlex `search="predicting annotation difficulty number of annotators per item LLM"`. Only Moros-Daval 2024 (task difficulty, not reading budget).
- OpenAlex `search="early stopping self-consistency judge evaluation"`. Only answer-level early stopping; nothing for judge verdicts.
- OpenAlex `search="budget allocation LLM evaluation per-instance difficulty prediction"`. Nothing predicting a *verification* budget per instance.
- OpenAlex `search="sequential testing anytime-valid LLM evaluation"`. CITE (SC mode certification) and leaderboard e-processes; none per-item for evidence verification.
- OpenAlex `search="learning to defer large language model human expert"`. Only L2D for task prediction; nothing for evidence-verification judgments.
- WebSearch `adaptive number of LLM judge samples per item stopping rule evidence verification RAG "per-item" budget escalate human`. Its own summary: "No result combines a per-item adaptive count of LLM judge samples, a stopping rule, RAG evidence verification, and escalation to humans."
- I found **no paper that predicts per-item budget from document-structure features** (length, section depth, candidate-sentence count) for any LLM task, and **none treating span-localisation agreement as a quantity with a budget curve**.
- **Caveat on coverage:** the arXiv API rate-limited after about 15 queries (HTTP 429), and OpenAlex followed. The full-text queries above were OpenAlex and WebSearch only. A repeat arXiv sweep (`abs:"LLM-as-a-judge" AND abs:adaptive`, `abs:judge AND abs:"early stopping"`, `abs:"self-consistency" AND abs:"stopping rule"`) is still owed before submission.

## COULD NOT VERIFY

- **"Reject Before You Run: Small Assessors Anticipate Big Language Models"** (Zhou et al., recalled as 2022, Hernández-Orallo group). This would be H2's closest assessor-model ancestor. Not found by arXiv `ti:"Reject Before You Run"` (no result) or OpenAlex `title.search:Reject Before You Run` (no result). **Do not cite** until found; it may be a workshop paper under a different title.
- **Damani et al. 2024 venue.** I believe it is ICLR 2025, but a WebSearch for the title plus ICLR 2025 returned no match. Cite as arXiv:2410.04707 until confirmed.
- **FrugalGPT in TMLR (2024).** Recalled but not found (WebSearch; OpenAlex lists arXiv only). Cite as arXiv:2305.05176.
- **Varshney & Baral 2022, "Model Cascading: Towards Jointly Improving Efficiency and Accuracy of NLP Systems", EMNLP 2022.** Confirmed by OpenAlex record only (DOI 10.18653/v1/2022.emnlp-main.756); not read.
- **Snell et al. 2024, "Scaling LLM Test-Time Compute Optimally…"** (arXiv:2408.03314). OpenAlex record only, and the venue is unconfirmed. It is relevant because it bins compute allocation by *estimated question difficulty*, but it was not read here; the draft's Novelty paragraph already lists it as "to be fetched".
- **A specific "value of information for asking a human" paper for LLM agents.** Nothing verifiable beyond KnowNo (Ren 2023) turned up; that query set was cut short by the rate limit.
- **Taubenfeld 2025 CISC.** Venue record only; not read.

## Proposed corrected wording for the pre-proposal

- **H2 (corrected):** "Input-adaptive compute allocation predicts per-item budget for *task answers* from model internals or LLM difficulty rankings [Damani 2024; Wang 2025]. We test whether **cheap, pre-read document-structure features** (document length, section depth, number of candidate sentences, and the whether/where regime) predict the budget required by the **verification** step itself, before any reading is spent. That is the setting where these methods assume a verifier exists. Locating *where* the evidence lies has not converged within 20 readings, and part of that may be irreducible disagreement rather than missing budget."
- **H4 (corrected):** "Extending cascaded selective evaluation [Jung 2025] and adaptive-consistency stopping [Aggarwal 2023; Li 2024], we test whether a policy seeded by the per-item budget prediction, which chooses among re-reading, switching model family, deferring to a human expert, and stopping, reaches a target **expert-validated** accuracy at lower cost than these adaptive baselines and than fixed-k ensembling at matched cost [Lail & Markham 2026]. The policy is pre-registered in Year 1."
- **Novelty paragraph:** replace "No study we found predicts the reliability of the verification step itself as a function of budget, per item, and scores that prediction in advance" with "Adaptive stopping and cascades decide *when* to stop spending, using signals observed while spending [Aggarwal 2023; Jung 2025]. Input-adaptive allocation predicts the budget for *answers* [Damani 2024]. We predict the budget for *verifying evidence*, from features available before reading, score the prediction prospectively, and test whether the stop it licenses is correct as well as stable [cf. Ota 2026, who prove the two are distinct]."
