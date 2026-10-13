# Bib inbox 12 — LLM uncertainty quantification and calibrated decision models

> **Provenance.** Produced 2026-10-13 for the FY27 LDRD pre-proposal ("the pre-proposal" / "the
> draft" below; it is not in this repo). It covers (A) uncertainty quantification (UQ) for LLMs as it
> bears on repeated-reading reliability, and (B) calibrated typed-decision models, chiefly TypeSafe
> Jev, together with earlier work on cascades and deferral. It was red-teamed the same day, and this
> version applies that review.
>
> **What was checked, and by whom.** The first pass checked every arXiv id against the arXiv query
> API (`export.arxiv.org/api/query`), venues against the arXiv comment or OpenAlex, and Jev facts
> against typesafe.ai, docs.typesafe.ai, The Register and TechCrunch. The red-team pass re-queried
> all 51 original arXiv ids (all resolve, with correct titles and authors), read the PDFs of six
> 2026 preprints (TypedBench, Just Ask Jev, Lail, Li et al., Denney & DiGiuseppe, Rafe & Das
> 2609.24052), read the Farquhar et al. full text on nature.com, and checked the vendor's docs
> sources, the Master Customer Agreement (MCA), and the OpenRouter and Vercel gateway listings. In
> revision, the 18 arXiv ids added here were queried through the arXiv API and their claims checked
> against the abstracts; Chow 1970 was checked on Crossref and Geifman & El-Yaniv 2017 on the NeurIPS
> proceedings site; and the MCA clauses (2.1, 2.3, 14.1) and two vendor cookbooks
> (`consistency_choice_cookbook`, `semantic_find`) were re-fetched and the quoted text confirmed.
>
> **Hypothesis numbering.** This file follows the current draft: H1 per-item budget forecast, H2
> reliability vs expert correctness, H3 cascade with deferral, H4 error correlation between readers.
> Bib inbox [06](06-inference-scaling.md)–[10](10-crowdsourcing-label-budget.md) were written against an
> earlier numbering in which H4 is the stop/escalate policy (now H3).
>
> **What was not re-checked.** Venues marked "not confirmed" (OpenAlex's free budget ran out during
> the red-team pass and dblp did not respond). The full text of any paper added in revision; their
> entries rest on the abstract. Whether Jev is still early-access on 2026-10-13. The OpenAI
> "Decisions API" report. The vendor's separate "workflow evals" site. Liu et al. 2503.15850's
> four-source taxonomy, which was not checked in the PDF.
>
> Related surveys: conformal stopping and conformal abstention (Quach 2023 in
> [06](06-inference-scaling.md); Abbasi Yadkori 2024 conformal abstention in
> [08](08-adaptive-budget-escalation.md)); Adaptive-Consistency and early-stopping self-consistency
> ([06](06-inference-scaling.md), [08](08-adaptive-budget-escalation.md)); Trust-or-Escalate and
> judge repetition ([07](07-judge-reliability-budget.md), [08](08-adaptive-budget-escalation.md));
> SCOPE and Goo 2026 ([08](08-adaptive-budget-escalation.md),
> [09](09-science-agents-claim-verification.md)); crowdsourcing label budgets
> ([10](10-crowdsourcing-label-budget.md)). They are not repeated here.

**Status key.** `api` = title, authors, date and comment read off the arXiv API record. `record` =
OpenAlex or Crossref record only. `page` = publisher or proceedings page. `abstract` = the cited claim
is in the abstract. `full text` = the cited claim was checked in the PDF or full text (by the red-team
pass unless stated). `vendor` = read on a TypeSafe primary page. Nothing here rests on recall.

Each UQ paper gets three columns, matching our three questions:
- **Pre?** Does it predict uncertainty *before* sampling, per item, from features?
- **Gold?** Does it validate the uncertainty against human or expert gold, or only against automatic correctness?
- **Out:** Is the output a single answer (`single`), a set (`set`), or sub-claims, sentences or spans (`fine`)?

---

## A. Uncertainty quantification for LLMs

### A1. Sampling-based uncertainty and consistency as confidence

| Paper | Pre? | Gold? | Out | Bearing on us |
|---|---|---|---|---|
| Kuhn, Gal, Farquhar. *Semantic Uncertainty: Linguistic Invariances for Uncertainty Estimation in NLG*. ICLR 2023 (Spotlight). arXiv 2302.09664. `api` `abstract` | No: needs ~5–10 samples | No: automatic correctness (ROUGE / LLM grading) on QA | single (free-form answer clustered by meaning) | The canonical "agreement across samples ⇒ correctness" result. Our whether-agreement is the binary special case: on a yes/no question, semantic clustering does nothing. |
| Farquhar, Kossen, Kuhn, Gal. *Detecting hallucinations in large language models using semantic entropy*. **Nature** 630(8017):625–630 (2024), doi 10.1038/s41586-024-07421-0. `record` `full text` | No | Partly: mostly automatic grading. The biography experiment uses 150 **manually labelled** claims. | single; a biography is broken into factoids | Targets *confabulations*: arbitrary errors that vary between samples. The full text says semantic entropy is not suited to "consistent errors learned from the training data" and "does not help when LLM outputs are systematically bad". That is the case our H2 expert test is built to expose, and the case any agreement signal misses (see challenge 7). |
| Kossen et al. *Semantic Entropy Probes*. arXiv 2406.15927 (2024). `api` `abstract` | **Partly.** A linear probe reads semantic entropy from hidden states, including at the token before generation, i.e. from the prompt alone. | No | single | The closest white-box analogue of H1's target: the *dispersion* of readings is predicted without sampling. It needs model internals, which our API readers do not expose. |
| Wang et al. *Self-Consistency Improves CoT Reasoning*. ICLR 2023. arXiv 2203.11171. `api` | No | No | single (majority vote) | Origin of fixed-k voting, our baseline. |
| Lyu et al. *Calibrating LLMs with Sample Consistency*. AAAI 2025 (OpenAlex: aaai.v39; the arXiv comment says "AAAI 2024"). arXiv 2402.13904. `api` `record` | No | No | single | Agreement, entropy and first-second distance used as confidence. They are calibrated better than verbalised confidence. |
| Lin, Trivedi, Sun. *Generating with Confidence: UQ for Black-box LLMs*. TMLR 05/2024. arXiv 2305.19187. `api` | No | No: automatic | single | Black-box dispersion and similarity-graph measures. Usable directly on our recorded readings. |
| Manakul, Liusie, Gales. *SelfCheckGPT*. EMNLP 2023. arXiv 2303.08896. `api` | No | **Yes: human sentence-level factuality labels** (WikiBio) | **fine** (per sentence) | A sentence-level consistency signal checked against human labels. It scores the *model's own generated* sentences, not *selected source* sentences. |
| Chen & Mueller. *Quantifying Uncertainty in Answers from any LM (BSDetector)*. ACL 2024 (OpenAlex: 2024.acl-long.283). arXiv 2308.16175. `api` `record` | No | No | single | Combines observed consistency with self-reflection certainty. A template for mixing sampled and verbalised signals. |

### A2. Verbalised confidence, self-knowledge and calibration

| Paper | Pre? | Gold? | Out | Bearing on us |
|---|---|---|---|---|
| Kadavath et al. (Anthropic). *Language Models (Mostly) Know What They Know*. arXiv 2207.05221 (2022). `api` `abstract` | **Yes: P(IK)**, "probability I know", is predicted from the question alone, before any answer is sampled. It requires training a head. | No: automatic correctness | single | **Related precedent for H1, not its established name.** P(IK) forecasts *correctness*. H1 forecasts *how many readings an item needs*, which is the dispersion of readings. An item can have low P(IK) and zero dispersion (a consistent error, Farquhar's blind spot). The abstract also says models "struggle with calibration of P(IK) on new tasks", and that P(IK) increases "in the presence of relevant source materials in the context", which bears on WHETHER given a paper. |
| Lin, Hilton, Evans. *Teaching Models to Express Their Uncertainty in Words*. arXiv 2205.14334 (2022). TMLR venue not confirmed (OpenAlex lists arXiv only). `api` | No | No | single | Verbalised calibration can be trained. |
| Tian et al. *Just Ask for Calibration*. EMNLP 2023. arXiv 2305.14975. `api` | No | No | single | For RLHF models, verbalised confidence is better calibrated than token probabilities. |
| Xiong et al. *Can LLMs Express Their Uncertainty?* ICLR 2024. arXiv 2306.13063. `api` | No | No | single | Verbalised confidence is overconfident. Combining it with sample consistency works best. This is evidence *for* using multi-reading agreement. |
| Kapoor et al. *LLMs Must Be Taught to Know What They Don't Know*. NeurIPS 2024. arXiv 2406.08391. `api` `abstract` | Partly: a trained uncertainty head (LoRA, through model features) on the question and answer | Includes a user study of how people use the confidence | single | "A thousand graded examples are sufficient" to fine-tune calibration. White-box. The cheap supervised route to a per-item correctness forecaster. |
| Yin et al. *Do LLMs Know What They Don't Know?* Findings ACL 2023. arXiv 2305.18153. `api` | Yes (unanswerable-question detection) | No | single | Self-knowledge of unanswerability, relevant to "no evidence in this paper". |
| Chen, de Melo, Suchanek, Varoquaux. *Query-Level Uncertainty in LLMs*. ICLR 2026. arXiv 2506.09669. `api` `abstract` | **Yes, explicitly: "before generating any tokens"** (training-free "Internal Confidence" across layers and tokens) | No | single | Related name for a forecast made before generation: "query-level uncertainty". Used for routing in RAG and in model cascades. White-box, and its target is correctness, not reading dispersion. |

### A3. Calibration of LLM-as-judge

| Paper | Pre? | Gold? | Out | Bearing on us |
|---|---|---|---|---|
| Liu et al. *Calibrating LLM-Based Evaluator*. LREC-COLING 2024 (OpenAlex). arXiv 2309.13308. `api` `record` | No | Yes: aligns the judge to human scores | single (score) | Recalibrates the judge to human preferences. |
| Wagner et al. *Black-box UQ for LLM-as-a-Judge*. arXiv 2410.11594 (2024), venue not confirmed. `api` | No | No: judge accuracy | single | Uses a confusion matrix over candidate ratings as the uncertainty signal. |
| Radharapu et al. *Calibrating LLM Judges: Linear Probes*. arXiv 2512.22245 (2025). `api` `abstract` | Partly: probes on hidden states | Includes human-preference sets | single | Brier-trained probes beat verbalised and multi-sample confidence at ≈10× lower cost. |
| Lail. *Decomposing LLM-Judge Uncertainty to Target Expert Labels*. arXiv 2609.06444 (2026), "9 pages (4 pages of content)", from a company repo (composo-ai), unrefereed. `api` `full text` | **No.** A post-hoc Bayesian regression on a judge's prediction plus labels already collected; no sampling and no extra judge calls, but not a forecast made before reading. | Partly: the "exactly known truth" experiment uses synthetic dice; the human-disagreement experiment uses ChaosNLI crowd labels, not experts | single | Separates aleatoric uncertainty (real disagreement) from epistemic uncertainty (judge ignorance), so that labels go where the judge is ignorant. It reports that "stated confidence is no guide to its actual error". Its "fewest-labels-first" baseline ranks items by how many of ChaosNLI's 100 crowd labels are already collected, and ties the epistemic ranking only there (14.3 vs 15.0; CI vs entropy [−0.0, +12.4]). **That baseline does not transfer to us.** Our items start with zero expert labels and receive at most two plus an adjudicator, so "least-labelled first" is all ties, which is random ordering. With two experts we also cannot separate real disagreement from annotator noise the way Lail can with 100 annotators. |
| Baan, Aziz, Plank, Fernández. *Stop Measuring Calibration When Humans Disagree*. EMNLP 2022. arXiv 2210.16133. `api` | n/a | Yes: human label distributions | single | When annotators disagree, accuracy-based calibration is ill-posed. Our two experts plus an adjudicator should report the *disagreement distribution*, not only the adjudicated label. Refereed; the preferred citation for this point. |
| Plank. *The "Problem" of Human Label Variation*. EMNLP 2022, doi 10.18653/v1/2022.emnlp-main.731. `record` | n/a | n/a | n/a | Supplies the name "human label variation". Plank's term treats disagreement as *signal* (plausible plurality). Our two judges' r≈0.30 may instead be low inter-rater reliability (noise or guideline ambiguity). Both readings are open; the adjudicator data should decide between them, not the choice of name. |

### A4. Separating aleatoric from epistemic uncertainty

- **Hou et al.**, *Decomposing Uncertainty for LLMs through Input Clarification Ensembling*. ICML 2024. arXiv 2311.08718. `api`
  - Method: rewrite an ambiguous input into its clarifications, then ensemble over them. Disagreement *across* clarifications is aleatoric (the input is ambiguous). Disagreement *within* a clarification is epistemic.
  - Pre? Partly (the clarifications are generated, not sampled answers). Gold? Ambiguity datasets. Out: single.
  - For us, a query can be ambiguous about what counts as evidence. That predicts low where-agreement that no number of extra readings will remove.
- **Abbasi Yadkori, Kuzborskij, György, Szepesvári**, *To Believe or Not to Believe Your LLM*. arXiv 2406.02543 (2024), venue not confirmed (OpenAlex: arXiv only). `api`
  - Method: iterative prompting yields an information-theoretic bound that flags *epistemic* uncertainty even when there are several valid answers.
  - This is distinct from the same group's conformal-abstention paper, which is in [08](08-adaptive-budget-escalation.md).
- **Ling et al.**, *UQ for In-Context Learning of LLMs*. NAACL 2024. arXiv 2402.10189. `api`
  - Splits uncertainty into demonstration-induced (aleatoric) and model-configuration (epistemic) parts.
- **Cross-model disagreement as an epistemic proxy.** It appears in the surveys below, but we found no paper that validates cross-model disagreement against expert gold in an evidence-attribution task.
- **Implication for us.**
  - Our "switch model" action is an epistemic probe, but only if the second model errs differently. Rao & Callison-Burch (B2) find that Jev and LLM judges are "wrong in the same places", so a switch can repeat the error.
  - "Ask a human" is only worth its cost where the uncertainty is epistemic. Where it is aleatoric, the experts will disagree too.
  - Hou gives that argument a refereed basis. Lail illustrates it but is an unrefereed 4-page preprint, and with two experts we cannot estimate the aleatoric part the way it does.

### A5. Fine-grained, set-valued and extraction outputs

- **Fisch, Schuster, Jaakkola, Barzilay**, *Efficient Conformal Prediction via Cascaded Inference with Expanded Admission*. ICLR 2021. arXiv 2007.03114. `api` (claim details from the red-team reading)
  - Conformal *sets* of evidence sentences drawn from ~40K Wikipedia sentences (FEVER retrieval), plus QA answer spans in passages, with coverage guaranteed against human-annotated evidence.
  - Set-valued UQ over **source-document sentences**, validated against human evidence annotation. Not an LLM; the annotators are not domain experts; no repeated readings. **The nearest set-valued precedent.**
- **Chakraborty, Yang, Khashabi, Lawrie, Duh**, *Principled Context Engineering for RAG: Statistical Guarantees via Conformal Prediction*. ECIR 2026 (arXiv comment). arXiv 2511.17908. `api` `abstract`
  - Conformal filtering with embedding- and LLM-based scoring keeps a *set of retrieved snippets* with a target recall of relevant snippets, on NeuCLIR and RAGTIME. The abstract states the coverage is of "relevant snippets"; it does not itself say how relevance was judged. The red-team pass reports that relevance comes from the collections' assessor judgments. We did not confirm that in the paper.
  - The closest LLM-era set-valued precedent over source content.
- **Jagannatha & Yu**, *Calibrating Structured Output Predictors for NLP*. ACL 2020. arXiv 2004.04361. `api` `abstract`
  - Calibrated confidence for extracted entity spans (NER, POS, QA), motivated by safety-critical healthcare use. Pre-LLM; gold spans from annotated benchmarks.
- **Singer, Sengupta, Pazdernik**, *UQ for NER via Full-Sequence and Subsequence Conformal Prediction*. arXiv 2601.16999 (2026). `api` `abstract`
  - Conformal prediction sets over span labelings, against benchmark gold (not experts).
- **Huang et al.**, *Can Jev Judge Radiology Reports?* arXiv 2609.27607 (2026). `api` `abstract`
  - One support question per statement, aggregated, scored against **expert** error counts: Kendall 0.573 (RadEvalX) and 0.398 (RadEvalExpert). Statement-level support checking by a calibrated decision model against expert annotation, in a clinical domain.
- **Vázquez et al.**, *SemEval-2025 Task 3: Mu-SHROOM*. SemEval-2025. arXiv 2504.11975. `api` (soft-label detail from the red-team reading)
  - Span-level hallucination detection, scored against soft labels (the share of annotators marking each character). Span probabilities validated against a human disagreement distribution, i.e. Baan's recommendation implemented. The spans are in *generated* text.
- **Niu et al.**, *RAGTruth*. arXiv 2401.00396 (ACL 2024 venue not re-verified). `api` `abstract`
  - Word-level human hallucination annotations of ~18,000 RAG responses. The standard span-level human gold for RAG, but over generated output, not source.
- **Mohri & Hashimoto**, *Language Models with Conformal Factuality Guarantees*. arXiv 2402.10978 (2024), venue not confirmed. `api` `abstract`
  - Set-valued: the subset of generated claims that survives a threshold. Gold: yes, a small human-annotated calibration set ("requires very few human-annotated samples"). Over generated claims, not source spans.
  - Extended to multi-hop RAG by Rehman & Yeh, arXiv 2609.38222 (2026). `api`
- **Fadeeva et al.**, *Fact-Checking the Output of LLMs via Token-Level UQ (Claim Conditioned Probability)*. Findings ACL 2024. arXiv 2403.04696. `api`
  - Claim-level uncertainty, validated against human-annotated claims. Out: fine. White-box. Generated claims.
- **Li et al.**, *Set-Valued Prediction for LLMs with Feasibility-Aware Coverage Guarantees*. arXiv 2603.22966 (2026). `api`
  - Set-valued, but a set of *answers*, not of source spans.
- **Ling et al.**, *Improving Open IE with LLMs: Demonstration Uncertainty*. arXiv 2309.03433 (2023). `api`
  - Uses uncertainty to select demonstrations for extraction. It does not quantify uncertainty over the extracted spans.
- **Mahbub et al.**, *A Multi-Stage Validation Framework for Trustworthy Large-scale Clinical Information Extraction using LLMs*. arXiv 2604.06028 (2026). `api` `abstract`
  - Not span-set UQ. Prior art for "LLM judge, then selective expert review" in large-scale extraction (judge vs subject-matter expert, Gwet's AC1 = 0.80), relevant to H2 and the policy.
- **The vendor's "Line-by-line search" cookbook** (docs.typesafe.ai/cookbooks/semantic_find). `vendor`
  - One Choice question ranks 218 line ids against a query (its function is named `where_question`), and a Noul in the same request checks "whether the document contains an answer". That is our WHETHER/WHERE design almost exactly. It is not validated against any gold. See B1.
- **What survives of the gap** (see "Searched and did not find"): no paper we found combines *repeated LLM readings*, *set-valued sentence selection in scientific papers*, and *evaluation against domain-expert span annotations with measured expert disagreement*. Each ingredient exists separately.

### A6. Surveys and benchmarks, 2023–2026

- **Geng et al.**, *A Survey of Confidence Estimation and Calibration in LLMs*. NAACL 2024 (OpenAlex: 2024.naacl-long.366). arXiv 2311.08298. `api` `record`
- **Huang et al.**, *A Survey of Uncertainty Estimation in LLMs: Theory Meets Practice*. arXiv 2410.15326 (2024). `api`
- **Shorinwa et al.**, *A Survey on UQ of LLMs: Taxonomy, Open Research Challenges, and Future Directions*. **ACM Computing Surveys** 2025, doi 10.1145/3744238. arXiv 2412.05563. `api` `record`
- **Liu et al.**, *UQ and Confidence Calibration in LLMs: A Survey*. **KDD 2025**, doi 10.1145/3711896.3736569. arXiv 2503.15850. `api` `record`
  - Its taxonomy has four sources of uncertainty: input, reasoning, parameter and prediction (not checked in the PDF). Input ambiguity and decoding stochasticity are named sources. A good single map for situating our "reliability".
- **Baan et al.**, *Uncertainty in NLG: From Theory to Applications*. arXiv 2307.15703 (2023). `api`
- **Vashurin et al.**, *Benchmarking UQ Methods for LLMs with LM-Polygraph*. **TACL** 13 (2025), 220–248. arXiv 2406.15627. `api` (journal_ref). Tool paper: Fadeeva et al., EMNLP 2023 demo, arXiv 2311.07383. `api`
  - The standard harness. To make our sampled-consistency baselines comparable to the literature, we could reuse its estimators.

---

## B. Calibrated decision models ("System One" models): TypeSafe Jev

### B1. Facts, sorted by how they are sourced

| Claim in our brief | Status | Source |
|---|---|---|
| Released 2026-09-15 | **Verified, independent press.** The Register (dated 16 Sep 2026, 02:35 UTC) says "on Tuesday" = 15 Sep. Tang & Zheng (arXiv 2609.32160) state the same date. TechCrunch, 18 Sep 2026, covers the launch week. | theregister.com/ai-and-ml/2026/09/16/typesafe_ai_debuts_model_for_machines/5296711; techcrunch.com/2026/09/18/a-new-kind-of-ai-model-from-a-chatgpt-inventor-is-thrilling-developers/ |
| Trained with "Reinforcement Learning for Calibrated Decisions" (RLCD) | **Vendor claim, named but not described.** The blog and the docs describe RLCD only by its output contract. TechCrunch quotes the company: "trained exclusively on synthetic data". Third parties claim RLCD-style open recipes (Deka, arXiv 2610.02486; "OpenJev-RLCD", arXiv 2609.38850; neither re-checked in revision). TypeSafe's own recipe remains undisclosed. | typesafe.ai/blog/introducing-system-one-models-and-jev |
| Typed outputs with calibrated probabilities | **Verified as an interface.** Three primitives: **Choice** (a distribution over options), **Score** (ordinal rubric levels) and **Noul** (P(statement true) in [0,1]). "Calibrated" is a vendor claim. | docs.typesafe.ai/introduction, /confidence |
| 70–500 ms latency | **Vendor claim**, repeated by the press (The Register writes "supposedly"). The vendor demo shows 0.114 s versus 8.566 s for GPT-5.6 Terra; the blog says its evals "are generally run from our laptops on the West Coast". Independent medians roughly agree: 0.31 s (Guo et al.) and 0.27–0.31 s (Madrid-García & Merino-Barbancho). | blog; The Register; arXiv 2609.29429, 2609.34024 |
| $0.042 / MTok input | **Verified on the vendor pricing docs.** "Output tokens are free". The blog does not rule out subsidy. Denney & DiGiuseppe (B2) find **no cost advantage over GPT-6 Luna at batch prices**. Under an Order, the MCA makes "all pricing information" TypeSafe Confidential Information (see Risks). | docs.typesafe.ai/models |
| Proprietary | **Verified, and licensed under stated terms.** Access is API-only, under the **Master Customer Agreement**, which grants "a limited, non-exclusive, non-transferable, non-sublicensable license" (§2.1). §2.3(b) forbids using "the Services or any Output" to "perform model distillation, train a model to imitate the output of the Services, or develop … a similar or competing product or service". §2.3(c) forbids attempts to "derive … the underlying ideas, algorithms, structure". TechCrunch says the architecture is undisclosed. Direct access was "early access" with a waitlist at launch; current status not checked. | typesafe.ai/legal/mca (re-fetched in revision); blog; TechCrunch |
| Third-party gateways | **Corrected.** Neither the blog nor TechCrunch says gateways serve Jev's typed API. The blog mentions OpenRouter only as the source of *LLM* latency numbers, and TechCrunch names Vercel as a *customer*. Live listings (red-team check) show OpenRouter serving "TypeSafe: Jev Router", an LLM router "running on Jev", not the typed API; and Vercel AI Gateway listing "TypeSafe AI" as a provider. | OpenRouter and Vercel AI Gateway listings |
| 64k-token input limit | **Needs correcting.** The docs say "64k tokens per request; 32k tokens for `state` plus the longest question". The 64k covers state plus *all* questions combined, so hundreds of per-sentence Nouls share it. A typical paper's main text (~8–15k tokens) fits in 32k; supplements, long reviews and reference lists push it over. The larger issue is that the vendor's jaggedness page says Jev "suffers from context rot", so accuracy may drop well before 32k. | docs.typesafe.ai/models (jev-1.13.0) |
| Cardinality 255 | **Verified**: "A Choice question accepts up to 255 options". Choice is **single-select**: it selects one option from a defined set, and the probabilities sum to 1. Multi-select is not documented. | docs.typesafe.ai/primitives/choice |

Further facts from the primary docs that matter to us:

- **Not deterministic, and no more self-consistent than the LLMs it was compared with.** The vendor's consistency cookbook runs one borderline post through a moderation rubric of 8 questions, 15 repeats per condition (run of 2026-09-11, `jev-latest`). It reports "the LLM distribution settings repeat their plurality labels 87.5% to 100% of the time, compared with TypeSafe's 90.8%". Jev "flips on 2 of the 8 questions" (splits of 11/4 and 8/7). With a 0.60 abstain threshold its repeat agreement rises to 99.2%, with automatic labels on 74.2% of answers.
  - Jev's figure sits inside the LLM range. The sample is one post, 8 questions and 15 repeats.
  - So repeated readings of Jev vary, and our reliability framework applies to Jev unchanged. Whether extra Jev passes are worth buying is a separate question: Li & Chen (B2) find that uniform extra passes on prefill-only decision models buy calibration but almost no accuracy.
  - Source: docs.typesafe.ai/cookbooks/consistency_choice_cookbook (re-fetched in revision).
- **The jev-1.13 "jaggedness" page lists its weaknesses**: literal reading, numbers and counting, dates, multi-hop or indirect questions, contradictory instructions and criteria, distraction by large irrelevant state ("context rot"), adversarial content, a lean toward the first Choice option, and no generation.
  - Its recommended way to do extraction is to enumerate candidates (with regex or an LLM) and let Jev *pick* among them.
- **The closest vendor design to ours is the "Line-by-line search" cookbook** (cookbooks/semantic_find). In one request, a Choice question scores 218 line ids against a query (WHERE) and a Noul checks "whether the document contains an answer" (WHETHER). It reports no validation against gold. Two further cookbooks are related:
  - **"Double-checking citations"** (cookbooks/citation_check) decides whether a quote's context supports, contradicts or says nothing about a claim.
  - **"Classifying RAG passages"** runs one Noul per passage ("does this passage state usable answer evidence?") with hand-set thresholds, on 81 passages and 6 queries, at passage level, with no accuracy or calibration numbers.
  - The docs also ship "Confidence-gated routing" and an "SDE cascade" pattern, so a Jev stage-0 gate is the vendor's own recommended use, not a novel design.
- **The confidence field is a heuristic.** For Choice it is (p_max − 1/n)/(1 − 1/n). The docs call it "a sensible default", not a validated statistic, and advise users to tune thresholds on their own labelled data.
- **The vendor's headline accuracy is agreement with frontier LLMs.** The blog's "workflow evals" score Jev against *the average of GPT-6 Astra and Fable 5.1 as reference probabilities*, not against ground truth. A comparison of "Jev vs frontier readings" is therefore partly circular by construction.

### B2. Independent evaluations

**These are a sample of a fast-growing literature.** An arXiv query (`abs:Jev OR abs:"System One model(s)" OR abs:TypeSafe OR abs:"typed decision"`, run by the red-team pass) returns about 100 preprints posted between 19 Sep and 9 Oct 2026. All are unrefereed and 1–5 weeks old. The map is **Tang & Zheng**, *Typed Decision Models: An Early Evidence Audit and Evaluation Checklist*, arXiv 2609.32160 (`api` `abstract`). It reviews 28 papers posted 19–24 Sep and finds that "the typed readout itself has not shown an independent accuracy advantage over comparable label-probability readouts"; Jev's clearest gains are latency and cost, with accuracy gaps on harder tasks. It derives a 14-item evaluation checklist that our replay should follow.

All entries below are `api`; the tag after each says how the claim was checked.

| Preprint | Finding relevant to us |
|---|---|
| Denney & DiGiuseppe. *JEV versus LLMs: Accuracy, Cost and Calibration on Seven Political Science Replications*. arXiv 2610.06625. `full text` | Compared against **published human coders**. Jev matches or comes close to the LLMs. With one query per question, it is better calibrated than GPT-6 Luna's token probabilities, but **not consistently better than open Qwen3.8-27B**. No cost advantage at batch prices. |
| Rafe & Das. *Calibrated Decisions at Scale: Police Crash Narratives … (Jev)*. arXiv 2609.24052. `full text` | Audited against **2,416 blinded human judgments**: F1 0.908; one frontier LLM is +0.059. "Calibration varies by model rather than by paradigm, so each model must be audited". Recalibration cuts calibration error 3.3×. Includes a review-budget formula for how many records a human must read. |
| Rafe & Das. *Benchmarking System One decision models against trained classifiers and language models for automated decision gates*. arXiv 2610.00346. `abstract` | On typed decisions, "swapping yes and no flips 50.5 answers per hundred for Jev", against at least 16.8 for every generative model tested and at most 6.5 for four fine-tuned decision checkpoints. **A per-sentence Noul design must control question polarity.** A fine-tuned DeBERTa-v3-large is most accurate where task labels exist. |
| **Rao & Callison-Burch. *JEV vs. LLMs as Rubric Judges: Cheaper, Faster, and Wrong in the Same Places*. arXiv 2609.29769.** `abstract` | Nine panels from seven benchmarks with human judgments. Jev can often stand in for flash-tier LLM judges at 16–325× lower cost. But "on Jev's most confident errors, about 96% of LLM verdicts repeat its wrong answer, where independent errors would give about half". Cascades that defer Jev's uncertain verdicts to an LLM judge "only lower cost while adding little accuracy: even with oracle thresholds, none beats the best single judge by more than 2.7 points". "The cascade also needs a fallback that errs elsewhere." **The strongest published result against a Jev-then-LLM cascade.** |
| Y. Liu. *Evaluating System One Models for Agent Security Decisions*. arXiv 2609.33401. `abstract` | Covers Jev, Laya, Decider and Bespoke Nimble. Low *average* calibration error hides **confident errors inside subgroups**. An LLM judge used as reviewer can repeat the System One model's confident mistakes: the same correlated-error finding as Rao & Callison-Burch, in security. |
| **Madrid-García & Merino-Barbancho. *Jev in Medicine: A Benchmark Evaluation*. arXiv 2609.34024.** `abstract` | Jev 1.13 against GPT-6 Sol on four medical benchmarks (8,469 requests). **PubMedQA, built on research abstracts: 78.4% vs 78.2%** for GPT-6 Sol with medium reasoning. Lower on MetaMedQA (74.8% vs 82.7%), much lower on DiagnosisArena-MCQ (59.8% vs 82.4%) and NEJM cases (61.8% vs 82.4%). On MetaMedQA Jev is best calibrated (ECE 0.063 vs 0.146). On 162 "I don't know or cannot answer" items it picks that option 10.5% of the time (GPT-6 Sol 8.6%). **Jev has been tested on scientific research text, with results comparable to a frontier LLM there.** |
| **Shankaranarayana, Runje, Jannink. *Beyond Answer Confidence: A Controlled Audit of Self-Knowledge in a Black-Box Decision Model*. arXiv 2610.01006.** `abstract` | Jev's confidence is calibrated on familiar closed-choice tasks but "fails as an indicator of missing knowledge": with no answer-relevant information it assigns up to 0.80 to a salient option. A targeted yes/no question on **whether the evidence suffices reaches AUROC 0.95, against 0.85 for answer confidence** on the same items. **The closest published result to our WHETHER question**, and the black-box analogue of P(IK) for Jev. |
| **Zhong, Li, Lai. *When the Right Answer Is Missing: An Arithmetic-Dependent Rejection Bottleneck in Jev*. arXiv 2609.39496.** `abstract` | When the correct answer is absent from a Choice, the explicit "none-of-the-above" rejection is correct only 7% of the time; Boolean (Noul) verification on the same cases reaches 99%. A threshold tuned on development data raises rejection to 79%. Arithmetic tasks only. Supports using Nouls, not a Choice with a "none" option, for "no evidence". |
| Porcedda. *Jev thinks "I don't know" but doesn't say it: Sys1Cal-v1*. arXiv 2609.35342. `full text` | On **synthetic** True/False propositions with known P(A), Choice answers appear to suppress a third "unknown" mass. Recovering it raises soft accuracy from 0.771 to 0.978. Bears on WHETHER: "no evidence" versus "can't tell" must be its own outcome. |
| **Huang et al. *Can Jev Judge Radiology Reports?* arXiv 2609.27607.** `abstract` | Per-statement support questions validated against **expert** error counts (Kendall 0.573 RadEvalX, 0.398 RadEvalExpert); beats an open NLI judge under matched decomposition. One support question per statement keeps expert agreement while using 43–45% fewer input tokens. Local RadMatch is stronger on clinically significant errors. Statement-level, expert-validated, clinical: the nearest design to our WHERE in another domain. |
| Xu et al. *Same-Number Citation Swaps: Stress-Testing Jev as a Financial Evidence Judge*. arXiv 2610.08675. `abstract` | Jev as a source-support verifier for cited evidence. Finds a trade-off between catching wrong-role citations and retaining valid alternative citations. |
| Li & Chen. *Readout Stability in Prefill-Only Decision Models: Zero-Label Prediction and Inference-Time Compute Allocation*. arXiv 2610.07716. `abstract` | Studies prefill-only decision models "inspired by the Jev model" across seven model families (the abstract does not say Jev itself is among them). "Uniform extra passes buy calibration but almost no accuracy; at matched cost a confidence cascade outperforms every scheme that re-asks the same model." Challenges spending budget on repeated readings of the same decision model. |
| Sharma, Duncan, Marceau Caron, Vollmer. *TypedBench*. arXiv 2610.11392. `full text` | The abstract says "a hosted model"; the PDF names it as Jev 1.13.0. The model is **wording-sensitive and systematically underconfident**. Under asymmetric costs, acting on its probabilities "can be worse than taking its top answer". |
| Li, He, Li. *Beyond Calibration: Do a Typed-Decision Model's Probabilities Obey the Probability Axioms?* arXiv 2609.33209. `full text` | Uses ChaosNLI and PubMedQA items. P(X) + P(not X) misses 1 by 0.064 on average. The three single-label probabilities sum to 1.14. Its incoherence is ~5× its repeat noise, and the violations concentrate where Jev is uncertain. Still more coherent than Qwen first-token readout. Label-free. |
| Guo et al. *Just Ask Jev: RLCD as a Zero-Shot Detector of AI Alignment Failures*. arXiv 2609.29429. `full text` | Median AUROC 0.886 zero-shot. Matches the reference scorer's agreement with human labels at 63× lower cost, but **human labels exist on only 2 of 44 benchmarks**, and §4.3 says "the parity holds pooled, not per generator" (on GPT-3.5 outputs, κ 0.668 vs 0.790). §4.4: calibration is good pooled (**ECE 0.047**) but not within benchmarks (**median per-benchmark ECE 0.168**, against 0.074 for the reference), which the authors attribute to base-rate mismatch. The abstract does not mention base-rate mismatch; these figures are from the PDF. |
| Nandakishore. *Laya as a Typed Probabilistic Assessor: An Independent Reproduction and a Preregistered Study of Calibration and Selective Escalation*. arXiv 2609.33843. `abstract` | Preregistered study of **Laya**, the open checkpoint (421M-parameter ModernBERT-large) we name as the open control. It is uniformly **under-confident** (signed gap −0.214); one fitted temperature fixes most of it (held-out ECE 0.204 → 0.037). "The frozen gate beats random escalation but misses its 10% accepted-set error target on both tracks", and it shows no zero-shot out-of-distribution transfer (accuracy 0.617). |
| Barbosa. *Calibrated Decision Models for Pentest Harnesses: JEV and Laya*. arXiv 2609.28940. `abstract` | A single run each with and without Jev. It quotes a vendor-reported ECE of 0.246 that we could not find in the vendor's primary pages (see COULD NOT VERIFY). |

**Bottom line on Jev:**

- The vendor has published **no calibration metrics against ground truth**. Its headline workflow evals measure agreement with two frontier LLMs.
- Independent preprints are mixed on calibration: best-calibrated on MetaMedQA (Madrid-García), roughly a mid-tier LLM (Denney), underconfident and wording-sensitive (TypedBench), well-calibrated pooled but not per benchmark (Just Ask Jev), "audit each model" (Rafe & Das). The early meta-review finds no independent accuracy advantage for the typed readout (Tang & Zheng).
- **Jev has been tested on scientific text**: PubMedQA accuracy matches GPT-6 Sol on research abstracts (Madrid-García), and evidence-sufficiency questions discriminate well (Shankaranarayana). It has also been tested on statement-level support against expert counts in radiology (Huang). **None of these evaluates sentence-set evidence localisation in full scientific papers against expert span annotation.**
- **Its errors are correlated with LLM errors** (Rao & Callison-Burch; Y. Liu). That limits what a Jev-then-LLM cascade can gain, whatever Jev's calibration.
- Competitors exist: the open Laya, OpenAI's "Decisions API" (TechCrunch, 30 Sep 2026; not re-verified), Decider and Bespoke Nimble, and a Microsoft effort reported by The Register (≈10 Oct; headline only, not re-verified). Our proposal should name the *category* ("calibrated typed-decision model"), not only the vendor.

### B3. Earlier work: cheap calibrated scorers, cascades and deferral

| Paper | What it is | Relevance |
|---|---|---|
| Chow. *On optimum recognition error and reject tradeoff*. IEEE Trans. Inf. Theory 16(1):41–46, 1970. `record` (Crossref) | The reject option | The origin of "classify or abstain". |
| Geifman & El-Yaniv. *Selective Classification for Deep Neural Networks*. NeurIPS 2017. arXiv 1705.08500. `api` `page` | Selective classification with a guaranteed risk level | The standard modern citation for "selective prediction". |
| Madras, Pitassi, Zemel. *Predict Responsibly: … Learning to Defer*. NeurIPS 2018. arXiv 1711.06664. `api` | Learning to defer to a human expert | The formal origin of our "ask a human" action. |
| Mozannar & Sontag. *Consistent Estimators for Learning to Defer to an Expert*. ICML 2020. arXiv 2006.01862. `api` | A consistent surrogate loss for the classifier-plus-deferral decision | Gives the theory for a learned stop/escalate policy. |
| Chen, Zaharia, Zou. *FrugalGPT*. arXiv 2305.05176 (2023; venue not confirmed). `api` (the DistilBERT scorer is in the body) | LLM cascade gated by a **small learned scorer** that judges answer reliability | The direct precedent for "a cheap model as first-stage verifier". Jev would be a general-purpose, off-the-shelf version of FrugalGPT's scorer. |
| Aggarwal, Madaan et al. *AutoMix*. NeurIPS 2024. arXiv 2310.12963. `api` | Few-shot self-verification feeding a POMDP router | Self-verification noise is handled by a meta-verifier. Analogous to deciding whether to trust the first reading. |
| Yue et al. *LLM Cascades with Mixture of Thoughts*. ICLR 2024. arXiv 2310.03094. `api` | **The weak model's answer consistency decides whether to escalate** | Sampling consistency used as the deferral signal: our whether-agreement used as a gate. |
| Ding et al. *Hybrid LLM: Cost-Efficient and Quality-Aware Query Routing*. ICLR 2024. arXiv 2404.14618. `api` | A router predicts query difficulty *before* calling either model | A pre-call forecast, related to H1. |
| Ong et al. *RouteLLM*. arXiv 2406.18665 (2024; venue not confirmed). `api` | Routers trained on preference data | A router learned from preferences, not from correctness. |
| Gupta et al. *Language Model Cascades: Token-level Uncertainty and Beyond*. arXiv 2404.10136 (2024; venue not confirmed). `api` | Learned deferral from token-uncertainty quantiles | Simple aggregate uncertainty is a weak deferral rule; a learned post-hoc rule does better. |
| Chuang et al. *Learning to Route LLMs with Confidence Tokens*. arXiv 2410.13284 (2024). `api` | A model emits a confidence token that drives routing | n/a |
| Damani et al. *Learning How Hard to Think: Input-Adaptive Allocation of LM Computation*. arXiv 2410.04707 (2024). `api` `abstract` | **Predicts per input the reward distribution, and allocates samples accordingly** | The closest published analogue of H1's "how many readings". Also in [06](06-inference-scaling.md) and [08](08-adaptive-budget-escalation.md). |

---

## Work that challenges us

1. **Sampling consistency is already a good proxy for correctness in QA.** Kuhn 2023, Farquhar 2024, Xiong 2024 and Lyu 2025 all report this.
   - A reviewer may say H2 is settled.
   - Our counter has three parts:
     - The validation in these papers is mostly automatic and on single answers. Farquhar's biography experiment uses 150 manually labelled claims, but none of them uses expert span gold or a set-valued output.
     - Farquhar explicitly cannot detect *consistent* errors.
     - Our own numbers point the same way. Whether-agreement is 0.96–0.99, so it is near ceiling and cannot discriminate between items; where-agreement is 0.38–0.56. Consistency on WHETHER may be uninformative exactly because it is near ceiling.
2. **Single-pass predictors replace sampling.** Semantic Entropy Probes, Query-Level Uncertainty (ICLR 2026), Kadavath's P(IK) and Kapoor 2024 all predict uncertainty in one pass from internals or a trained head.
   - If one pass is enough, repeated readings are wasted spend.
   - Counter: these need white-box access or training. Most forecast correctness, not reading dispersion, and they are validated on single answers.
   - Our H1 is a cheap black-box forecaster of dispersion. It should be benchmarked *against* a correctness forecaster such as P(IK), and against Damani 2024's per-input allocation.
3. **Calibrated single-pass decision models (Jev and its category).**
   - If Noul probabilities were calibrated, one sub-second call per sentence would give P(evidence) for every sentence directly. That would make repeated frontier readings unnecessary for WHETHER, and plausibly for WHERE.
   - This is not hypothetical. Jev matches GPT-6 Sol on PubMedQA research abstracts (Madrid-García), discriminates evidence sufficiency at AUROC 0.95 (Shankaranarayana), and tracks expert error counts on per-statement support in radiology (Huang).
   - Counters:
     - Jev's own cookbook shows it is non-deterministic, inside the LLM range (90.8% vs 87.5–100%).
     - TypedBench finds it wording-sensitive and underconfident; Rafe & Das (2610.00346) find it flips half its answers when yes and no are swapped.
     - Li et al. find it incoherent at ~5× its repeat noise.
     - None of the tests covers sentence-set localisation in full papers against expert spans.
     - This is an empirical question our replay harness can answer, so we should run it rather than argue it.
4. **Lail 2026: a naive label-count baseline matches an epistemic ranking** on ChaosNLI.
   - This does not transfer: with zero expert labels at the start, label-count ordering is random ordering.
   - What does transfer: the policy evaluation must include *random* escalation and, where items carry prior labels, *label-count* baselines. Nandakishore shows a calibrated gate can beat random escalation and still miss its error target.
5. **Rafe & Das 2026 (2609.24052) already present calibrated typed decisions, audited against blinded humans, with a human review budget.** That is our H2 plus our policy, in a different domain (crash narratives). Mahbub et al. 2026 do LLM-judge-then-expert review for clinical extraction.
   - Cite both as close prior art.
   - What differs in ours: scientific evidence, span-set outputs, and repeated reading.
6. **Just Ask Jev: it matches the reference scorer's agreement with human labels, at 63× lower cost.**
   - The expert read could be replaced by a cheap judge whose agreement with humans is comparable.
   - Counters: human labels exist on only 2 of its 44 benchmarks, and the parity holds pooled, not per generator. In our domain, two expert judges correlate at only r≈0.30, so "agreement with humans" is itself the quantity under study.
7. **Rao & Callison-Burch 2026: Jev and LLM judges are wrong in the same places.** About 96% of LLM verdicts repeat Jev's most confident errors, and no Jev-then-LLM cascade beats the best single judge by more than 2.7 points, even with oracle thresholds. Y. Liu finds the same in security.
   - This undercuts the assumption that a Jev stage 0 saves frontier readings without losing accuracy.
   - It connects to Farquhar: consistent errors are invisible to any agreement signal, whether across readings or across stages of a cascade.
   - Counter: their tasks are rubric judging, not evidence localisation. Whether the correlation holds for our task is H4 (error correlation), and the replay must measure it.

## Searched and did not find

- **UQ that combines repeated LLM readings, set-valued sentence selection in scientific papers, and evaluation against domain-expert span annotations with measured expert disagreement.**
  - Each ingredient exists alone: set-valued evidence sentences against human annotation (Fisch 2021); LLM-scored conformal sets of source snippets (Chakraborty 2026); calibrated entity spans (Jagannatha & Yu 2020; Singer 2026); per-statement support against expert counts (Huang 2026, clinical); span probabilities against annotator soft labels (Mu-SHROOM, in generated text); word-level human gold for RAG output (RAGTruth); conformal subsets of generated claims (Mohri & Hashimoto; Rehman & Yeh).
  - Searches tried (first pass): arXiv `ti:uncertainty AND ti:attribution`; `ti:uncertainty AND ti:citation`; `ti:conformal AND ti:span`; `ti:uncertainty AND ti:"information extraction"`; a web search for "uncertainty quantification LLM attribution evidence sentence selection".
  - Searches tried (red-team pass): arXiv `ti:uncertainty AND ti:rationale`; `abs:uncertainty AND abs:rationale AND abs:SciFact`; `abs:"span-level" AND abs:uncertainty AND abs:"expert annotat"`; `abs:conformal AND abs:citation/attribution AND abs:"language model"`.
  - The narrow combination looks like a real gap. The broad claim ("no UQ over source spans against human annotation") does not hold.
- **Cross-model disagreement validated against expert gold** for evidence or attribution tasks. It is discussed in the surveys but we found no instance. Rao & Callison-Burch measure cross-judge error correlation against human rubric labels, which is adjacent.
- **Any vendor-published calibration metric for Jev against ground truth** (ECE, Brier, reliability diagram). There is none on typesafe.ai or docs.typesafe.ai; the FAQ questions about benchmarks are unanswered. The workflow evals score agreement with two frontier LLMs.
- **Any evaluation of Jev on sentence-level evidence localisation in full scientific papers.** The nearest are PubMedQA (Madrid-García; Li et al.), evidence sufficiency (Shankaranarayana), radiology statements (Huang) and financial citations (Xu).
- A docs page on Jev limits (docs.typesafe.ai/limitations returned 404). The limits are on /models, /primitives/choice and the jaggedness page.

## Established names for what we invented

| Our term | Established name(s) |
|---|---|
| Forecasting per item, before reading, how many readings it will need (H1) | No exact name found for a black-box forecast of reading dispersion. Nearest: **semantic-entropy prediction without sampling** (Semantic Entropy Probes, Kossen 2024); **input-adaptive compute allocation** (Damani 2024); **adaptive / early-stopping self-consistency** (see [06](06-inference-scaling.md), [08](08-adaptive-budget-escalation.md)). Related but with a correctness target: **P(IK)** (Kadavath 2022), **query-level uncertainty** (Chen et al., ICLR 2026), and in routing **difficulty prediction / pre-generation routing** (Hybrid LLM). |
| Agreement across readings ("reliability") | **Self-consistency** / **sample consistency** (Wang 2023; Lyu 2025). **Semantic entropy** for free text. In psychometrics, **test–retest reliability** and **intra-rater reliability**. |
| Expert disagreement (judges r≈0.30) | Two readings, not yet decided: **human label variation** (Plank 2022), the aleatoric component (Hou 2024; Baan 2022); or low **inter-rater reliability** (noise or guideline ambiguity). |
| Stop / re-read / switch / ask a human | **Selective prediction** / reject option (Chow 1970; Geifman & El-Yaniv 2017); **learning to defer** (Madras 2018; Mozannar & Sontag 2020); **model cascades** / **routing** (FrugalGPT, AutoMix, RouteLLM). |
| "Decision model" | Vendor term: **"System One model"**. Literature term: **typed decision model** (Tang & Zheng; Li et al.; TypedBench). The older ML term is a **calibrated classifier with a reject option** (Chow 1970). |

## Implications for the pre-proposal

1. **Where Jev fits in the cascade** (call question 3). Jev is a candidate sub-second stage 0, and the replay tests it rather than assuming it saves cost.
   - Design: one Noul on WHETHER per (query, paper), then one Noul per candidate sentence, giving P(evidence) per sentence. Per-sentence Nouls avoid Choice's single-select limit and its weak "none" rejection (Zhong et al.). The vendor's "Line-by-line search" cookbook is the reference design to compare with.
   - Escalate to small, then frontier repeated readings, then the expert, when its probability falls in a middle band.
   - **The Jev stage is a test of H4 (error correlation), not an assumed saving.** Rao & Callison-Burch show that a cascade gains little when its stages are wrong in the same places. The replay must measure the error correlation between stage 0 and the frontier readings on our items, alongside calibration, and compare the cascade against fixed-k, Adaptive-Consistency, Trust-or-Escalate and random escalation.
   - Control question polarity (Rafe & Das 2610.00346) and keep the 64k budget shared across all per-sentence questions in mind.
2. **Risks:**
   - Proprietary and API-only, with an undisclosed architecture and RLCD training.
   - **Licence terms.** MCA §2.3(b) forbids using the Services or any Output to "perform model distillation, train a model to imitate the output of the Services, or develop … a similar or competing product or service". Training H1's forecaster or a probe on recorded Jev probabilities, or distilling Jev into an open control, needs a legal read before it is planned. Under an Order, MCA §14.1 makes "all pricing information" and other non-public information about the Services TypeSafe Confidential Information. This is load-bearing for a DOE-lab LDRD.
   - Calibration *unverified* by the vendor against ground truth; independent preprints are mixed (underconfident, wording-sensitive, polarity-sensitive, incoherent across related questions; best-calibrated on one medical benchmark).
   - Errors correlated with LLM errors, which limits cascade gains.
   - Early-access at launch, with pricing that may be subsidised and limits that "can change without notice".
   - Usable state is 32k tokens, and context rot may bite earlier.
   - Choice is single-select with ≤255 options, so it cannot natively return a span *set*.
   - Non-deterministic.
   - Mitigation: treat Jev as one interchangeable "decision model" slot. The open Laya or a Brier-trained probe (Radharapu 2025) can fill it as the open control. Laya needs recalibration first: it is uniformly under-confident and missed its preregistered error target (Nandakishore).
3. **Citations for the one-page draft, if only two or three fit:**
   - **Farquhar et al., *Nature* 2024** (already in the draft): sampling consistency as uncertainty, and its blind spot for consistent errors.
   - **Baan et al., EMNLP 2022**: calibration is ill-posed when human judges disagree, so expert disagreement must be reported as a distribution.
   - **Rao & Callison-Burch 2026** (arXiv 2609.29769), if Jev is named: calibrated decision models and LLM judges err in the same places, which limits cascades. A reviewer will raise it.
   - Drop Kadavath / Chen and Lail from the one-pager. P(IK) and query-level uncertainty forecast correctness, not dispersion; Lail is an unrefereed 4-page preprint whose baseline does not transfer.
4. **Jev is a testable hypothesis.**
   - Claim: one calibrated typed call per sentence matches k frontier readings on WHETHER but not on WHERE, and its errors are not independent of the frontier readings'.
   - It costs almost nothing to run on our recorded items.
   - It answers the strongest challenge to the project directly.
5. **State the gap narrowly.** Do not write "no published UQ work validates source-span uncertainty against expert readers"; Fisch 2021 and Chakraborty 2026 are set-valued precedents over source content. Write instead: no published work combines repeated LLM readings, set-valued sentence selection in scientific papers, and evaluation against domain-expert span annotations with measured expert disagreement. That combination is the contribution.

## COULD NOT VERIFY

- **"TypeSafe reports ECE 0.246 for Jev on their own evaluation set".** Quoted in Barbosa (arXiv 2609.28940). Not found on typesafe.ai, the launch blog or docs.typesafe.ai/confidence. Treat it as unverified secondhand.
- **Funding and adoption claims**: the $40M seed (DCVC), the later $870M at a $7.5B valuation (TechCrunch, 9 Oct 2026, seen only in a search snippet), "a third of the Fortune 500", "13% of Vercel paid teams in 24h". Press-reported or vendor claims, not checked against primary sources, and irrelevant to the proposal.
- **The RLCD reward being a Brier / proper scoring rule.** It appears in secondary explainers (eesel.ai, theneuron.ai) but not in the primary blog or docs.
- **OpenAI's "Decisions API"** (TechCrunch, 30 Sep 2026) and **The Register's report of a Microsoft model to challenge Jev** (≈10 Oct): not re-verified.
- **Whether Jev is still early-access with a waitlist** on 2026-10-13.
- **Chakraborty et al. 2511.17908's relevance labels**: the red-team pass reports assessor judgments on NeuCLIR and RAGTIME; the abstract does not say so, and we did not read the paper.
- **Venues** for FrugalGPT, RouteLLM, Gupta et al. 2024, Abbasi Yadkori et al. 2024 and Mohri & Hashimoto 2024 (OpenAlex lists them as arXiv only); the Lin, Hilton, Evans 2022 TMLR venue; the RAGTruth ACL 2024 venue. Do not quote these venues without checking further.
- **The full text of papers added in revision** (Tang & Zheng, Madrid-García, Shankaranarayana, Zhong, Rafe & Das 2610.00346, Huang, Xu, Li & Chen, Nandakishore, Rao & Callison-Burch). Their entries rest on the abstract only.
- **Queries tried:**
  - typesafe.ai; typesafe.ai/legal/mca; docs.typesafe.ai/{introduction, confidence, models, primitives/choice, model-jaggedness/jev-1.13, cookbooks/*}
  - web: "TypeSafe Jev TechCrunch OR The Register"; "Jev calibration arXiv"
  - arXiv: `abs:Jev OR abs:"System One model(s)" OR abs:TypeSafe OR abs:"typed decision"` (red-team pass)
  - OpenAlex title searches for all of the above.
