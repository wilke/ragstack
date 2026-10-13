# Bib inbox 09 — Science agents, evidence/claim verification, and the Argonne/DOE context

> **Provenance.** Produced 2026-10-12 for the FY27 LDRD pre-proposal ("the pre-proposal" / "the draft" below; it is not in this repo). Metadata was verified as stated per entry (the status tag on each says how). arXiv API and OpenAlex HTTP 429 rate limits left the queries listed in *Searched and did not find* ("Not executed: HTTP 429") unrun; they are coverage gaps, not negative results.

Survey for the LDRD pre-proposal *Predicting the Budget a Science Agent Needs to Verify Its
Evidence*. Surveyed 2026-10-12.

**Scope.** This file covers:
- literature-reading science agents, and how (or whether) each one verifies that a retrieved source supports a claim;
- the unsupported-citation rates they report;
- scientific claim-verification and attribution benchmarks;
- the cost of verification inside agent pipelines;
- the Argonne and DOE programmes the call says we must not duplicate.

**Already covered elsewhere.** `docs/papers/bib-inbox/03-attribution-rationales-evidence.md` already covers SciFact, ALCE, Liu et al. 2023, AIS, AttrScore and FActScore at depth. Here they get only the treatment our question needs, and the deep read is cross-referenced rather than repeated.

**Our question, asked of every item.**
- (a) Does it verify evidence support, and how: one LLM call, repeated calls, or a human?
- (b) Does it report the reliability or the cost of that verification step?
- (c) Does it treat the verification budget as a decision?
- (d) What must the pre-proposal cite, or differentiate itself from?

**Verification method.**
- Papers were checked through the arXiv API or the arXiv abstract/HTML pages, through the OpenAlex and Crossref APIs, and through ACL Anthology pages.
- Programmes were checked against official DOE, Argonne, ORNL, AmSC and grants.gov pages, plus the RFA PDF itself (DE-FOA-0003612 Amendment 000003, downloaded from simpler.grants.gov and read in full text).
- Numbers quoted below come from the source text I read (abstract, HTML body or RFA). Where only an abstract was read, the entry says so.
- Both the arXiv API and the OpenAlex API rate-limited (HTTP 429) part-way through. Some late items were therefore verified through arXiv `/abs/` pages only.

---

## Summary table

| # | Item | (a) Verifies support? How | (b) Reports reliability / cost of the check? | (c) Budget as a decision? |
|---|---|---|---|---|
| 1 | PaperQA2 (Skarlinski 2024) | Relevance-scores each chunk with **one LLM call** (top-k=30, score 0–10). No separate check that a claim is supported. | System-level human grading only (77.5% inter-grader agreement on 40 items). Cost $1–3/query. | No. k is a fixed hyperparameter. |
| 2 | OpenScholar (Asai, Nature 2026) | **One** self-feedback pass plus a post-hoc citation-insertion pass. Scored by **one** fine-tuned NLI judge (AttrScore-FLAN-T5-XL). | Judge never validated against humans. Cost per query reported for the whole system. | No. Capped at 3 feedback items. |
| 3 | AI co-scientist (Gottweis 2025) | Reflection agent: LLM reviews, some with web search, plus a "deep verification" decomposition. **One pass per review type.** | **None.** No accuracy figure for the reviews, no hallucination rate, no cost. | Scales *generation* compute, not the verification budget. |
| 4 | AI Scientist (Lu 2024 / Nature 2026) | No support check. Citation existence comes from Semantic Scholar BibTeX. Reviewer = **5-ensemble × 5 reflection rounds**. | Reviewer balanced accuracy 0.65 vs human 0.66; $0.25–0.50/review. | Ensemble size tuned once, as a global configuration. |
| 5 | Beel et al. 2025 (audit of 4) | Human audit. | 57% of manuscripts had hallucinated numbers. ~$6/paper. | n/a |
| 6 | Robin (Ghareeb, Nature 2026) | No support check. LLM-judge tournament for *ranking* (300 pairs). | Judge self-consistency 88% vs humans 61%. No cost given. | No. |
| 7 | Kosmos (Mitchener 2025) | Every statement is cited, but **no automated check**. Human graders afterwards. | 79.4% of statements accurate overall; 57.9% for synthesis statements. | No. Authors state no automated method exists. |
| 8 | Biomni (Huang 2025) | Not described in the abstract. | None in the abstract. | No. |
| 9 | Elicit vs ChatGPT (Helms Andersen 2025) | Human double extraction used as gold. | 92% F1; confabulations in 4% of data points. | Recommends AI in place of the second human (a budget decision, made by hand). |
| 10 | SourceCheckup (Wu, Nat Comm 2025) | Automated **GPT-4** verifier, **one call** per statement–source pair. | Verifier agrees 88% with a panel of physicians. 50–90% of responses are not fully supported. | No. |
| 11 | Liu et al. 2023 (verifiability) | Human audit. | 51.5% of sentences fully supported; 74.5% of citations support their sentence. | No. |
| 12 | ALCE (Gao 2023) | One NLI (TRUE) call per citation. | See bib-inbox/03. | No. |
| 13 | AttributionBench (Li 2024) | Benchmark *of* verifiers. | Best fine-tuned verifier ≈80% macro-F1. | No. |
| 14 | SciFact / SciFact-Open (Wadden 2020/2022) | Benchmark. | See bib-inbox/03. | No. |
| 15 | HealthVer (Sarrouti 2021) | Benchmark (14,330 pairs). | — | No. |
| 16 | Check-COVID (Wang 2023) | Benchmark (1,504 claims). | GPT-3.5 F1 69.90. | No. |
| 17 | SAFE (Wei, NeurIPS 2024) | Multi-step LLM + search agent per fact. | 72% agreement with humans; >20× cheaper than humans. | Cost is reported, but not as a decision variable. |
| 18 | Goo et al. 2026 (citation faithfulness) | Audits the **verifier** in OpenScholar/PaperQA2-style pipelines. | Unsupported rate ranges **3–18%, depending only on verifier strictness**. Negative-specific agreement 0.27–0.30. | Partly: the human-review budget at a guaranteed catch rate (conformal). |
| 19 | Rao et al. 2026 (URL hallucination) | Deterministic URL / Wayback check. | 3–13% of URLs hallucinated in deep-research agents. | No. |
| 20 | Hirsch et al. 2026 (EMNLP) | Per-agent faithfulness localisation. | 84.7% of AI-Q errors start at the orchestrator. | No. |
| 21 | HALLMARK (Reizinger 2026) | Benchmark *of* citation-existence verifiers. | The false-positive rate decides whether a verifier can be deployed. | No. |
| 22 | CiteGuard (Gangwani, NeurIPS 2025 workshop) | Retrieval scoring, then escalation of uncertain cases to a small LM (18% escalated). | $0.0028/review; 11.7 s/paper. | **Yes, a calibrated escalation cascade.** It checks reference metadata, not claim support. |
| 23 | Adaptive-Consistency / ESC / DSC (2023–2025) | Not verification: answer sampling for reasoning. | Up to 7.9× fewer samples. | **Yes: a per-item stopping rule; DSC adds a prior difficulty estimate.** |
| 24 | LLM-as-Judge on a Budget (Saha 2026) | Repeated judge queries. | Bound of Õ(√(Σσᵢ²/B)). | **Yes: variance-adaptive allocation across items.** |
| 25 | Coin Flip Judge (Yagubyan 2026) | 50 repeated trials per item. | Empirical reliability curve: 11 trials to reach 95% (15 for high-variance items). | Empirical, no law. |
| 26 | Reliability without Validity (Norman 2026) | 21 judges, ~541k judgments. | Test–retest >0.95 alongside position bias >0.10. | No. |
| 27 | Calibrate-Then-Act (Ding 2026) | Cost-aware stop/act for agents. | — | **Yes: explicit cost–uncertainty trade-off, including on RAG QA.** |
| 28 | AI Agents That Matter (Kapoor 2024) | n/a | Argues for cost-controlled agent evaluation. | Methodological. |
| 29 | EAIRA (Cappello 2025, Argonne) | Methodology for evaluating LLMs as research assistants. | Abstract does not address verification of evidence. | No. |
| P1–P7 | IDeA, BV-BRC Copilot, Genesis/EO 14363, DE-FOA-0003612, ModCon, AmSC, FORUM-AI | See § E. | None of them funds the reliability or budget of evidence checking, as far as the public text shows. | — |

---

## A. Literature and science agents

### `skarlinski-2024-paperqa2`
**Language agents achieve superhuman synthesis of scientific knowledge.** Michael D. Skarlinski,
Sam Cox, Jon M. Laurent, James D. Braza, Michaela Hinks, et al. (9 authors), FutureHouse.
- **Venue:** arXiv:2409.13740 (2024). I found no peer-reviewed venue.
- **Predecessor:** PaperQA, Lála et al., arXiv:2312.07559 (2023), verified.

**What it does.**
- The "RCS" step (rerank and contextual summarisation) maps one LLM completion over each of the top-k chunks (default k = 30).
- Each completion returns a summary and an integer relevance score out of 10.

**(a) Verification.** One LLM call per chunk. It scores *relevance to the question*, not *support for the claim being written*. No separate step checks that a claim is entailed by its source.

**(b) Reliability and cost.**
- WikiCrow vs Wikipedia, graded by four contracted experts on 375 statements:
  - cited-and-unsupported: 13.5% vs 24.9%;
  - precision: 86.1% vs 71.2%.
- The human graders themselves agreed on 77.5% of the 40 statements that were double-graded.
- ContraCrow, the contradiction detector:
  - 88% precision at threshold 8;
  - experts agreed with it only 60.4% of the time, while agreeing with each other 75.5% of the time.
- Cost: WikiCrow $4.48 ± $1.02 per article; PaperQA2 "$1 to $3 per query".
- LitQA runs were **repeated three times** "to control for the inherent noise in LLM inference, even at 0 temperature" (precision 85.2 ± 1.1%).

**(c) Budget as a decision.** No. k and the thresholds are fixed hyperparameters.

**Limitations they state.** ContraCrow's "overconfidence" is the "primary driver" of disagreement with humans. The authors also say the system is "expensive compared to lower accuracy commercial systems".

**(d) What we cite or differentiate.**
- **Must cite.** This is the canonical literature agent and the source of the "agents beat Wikipedia on unsupported citations" number.
- **Differentiate.** Its verification is a single relevance reading per chunk, and its reported reliability is *system-level*. The repeat-to-control-noise practice is evidence *for* our premise: even the builders treat a single run as noisy. They average it rather than model it.

### `asai-2026-openscholar`
**Synthesizing scientific literature with retrieval-augmented language models.** Akari Asai,
Jacqueline He, Rulin Shao, Weijia Shi, Amanpreet Singh, et al. (28 authors).
- **Venue:** *Nature* (2026), doi:10.1038/s41586-025-10072-4.
- **Preprint:** arXiv:2411.14199, "OpenScholar: Synthesizing Scientific Literature with Retrieval-augmented LMs".

**What it does.** The model writes a draft, generates self-feedback (capped at three feedback items), revises, and then runs a post-hoc citation-verification pass that inserts citations for statements that are worth citing but lack one.

**(a) Verification.**
- Inside the system: a single verification pass.
- In evaluation: citation precision and recall come from **one** fine-tuned NLI judge, `osunlp/attrscore-flan-t5-xl`, applied at sentence level (sentences under 50 characters are dropped).

**(b) Reliability and cost.**
- Headline: "GPT-4o hallucinates citations 78–90% of the time". In Table 3, the Biomedicine figure is 94.8%; the preprint's §4.2 also says "78–98%", an internal inconsistency worth knowing about.
- **The citation judge is not validated against humans.** The authors leave "detailed human analysis of citation accuracy" to future work.
- Cost per query is tabulated for the whole system: OS-8B $0.003, OS-GPT4o $0.05, PaperQA2 $0.3–2.3.

**(c) Budget as a decision.** No.

**Limitations they state.** They "do not claim that LM-based systems can fully automate scientific literature synthesis". Annotators "may not have captured more nuanced differences".

**(d) What we cite or differentiate.**
- **Must cite.** It is the most-cited peer-reviewed literature agent.
- **Differentiate.** Its "citation accuracy on par with human experts" claim rests on a single, unvalidated NLI judge. This is exactly the verification-reliability gap we target; Goo et al. 2026 (#18) makes the same point empirically.

### `gottweis-2025-coscientist`
**Towards an AI co-scientist** (v1 title, 26 Feb 2025). The current v2 is titled **"Accelerating scientific discovery with Co-Scientist"**. Juraj Gottweis, Wei-Hung Weng, Alexander Daryin, … Vivek Natarajan (51 authors), Google. arXiv:2502.18864.
- I found no peer-reviewed venue in OpenAlex. **Cite the arXiv id and state the title change.**

**What it does.** A Reflection agent runs six review types:
- initial review, with no tools;
- full review, with web search;
- deep verification, which decomposes a hypothesis into assumptions;
- observation review;
- simulation review;
- recurrent/tournament review.

**(a) Verification.** One LLM pass per review type. The paper describes no check that cited literature supports a specific claim.

**(b) Reliability and cost.** **No** accuracy or reliability figures for any review, no hallucination rate, and no cost. Elo "is auto-evaluated and not based on independent ground truth."

**(c) Budget as a decision.** Compute is the headline variable ("scaling test-time compute"), but it is spent on generating and ranking hypotheses, not on the reliability of the check.

**Limitations they state.**
- "imperfect factuality and hallucinations, which may be propagated in the co-scientist system".
- It may "miss critical prior works due to reliance on open-access literature".
- Its future work lists "improved factuality checking, and increased citation recall", with no method attached.

**(d) What we cite or differentiate.**
- **Must cite.** This is the template the IDeA project names itself after ("AI co-scientist").
- **Differentiate.** It scales generation compute. We model what verification costs. Its own future-work list names our gap.

### `lu-2026-ai-scientist`
- **Nature version:** **Towards end-to-end automation of AI research.** Chris Lu, Cong Lu, Robert Tjarko Lange, Yutaro Yamada, et al. (8 authors), *Nature* (2026), doi:10.1038/s41586-026-10265-5 (OpenAlex; I did not read the Nature text).
- **Preprint read:** "The AI Scientist: Towards Fully Automated Open-Ended Scientific Discovery", Lu, Lu, Lange, Foerster, Clune, Ha, arXiv:2408.06292 (2024).
- **Successor:** AI Scientist-v2, Yamada et al., arXiv:2504.08066 (2025), verified.

**(a) Verification.**
- Novelty is checked by Semantic Scholar search. The model self-assesses the "novel" flag.
- Citations come from 20 rounds of Semantic Scholar search, with BibTeX appended "to guarantee correctness". That guarantees the reference **exists**, not that it supports the claim.
- The automated reviewer is GPT-4o with 5 self-reflection rounds × 5 ensembled reviews plus a meta-review.

**(b) Reliability and cost.**
- Reviewer balanced accuracy 0.65 ± 0.04 against 0.66 for humans (NeurIPS consistency experiment).
- $0.25–0.50 per review; about $10–15 per paper.

**(c) Budget as a decision.** Ensemble and reflection counts were tuned once, globally. That is the "fixed k" baseline our H4 targets.

**Limitations they state.** "can hallucinate entire results"; "frequently hallucinates facts"; "we do not recommend taking the scientific content … at face value."

**(d) What we cite or differentiate.** Cite it as an agent whose verification budget is a fixed global configuration.

### `beel-2025-evaluating-ai-scientist`
**Evaluating Sakana's AI Scientist: Bold Claims, Mixed Results, and a Promising Future?** Joeran Beel, Min-Yen Kan, Moritz Baumgart. *SIGIR Forum* (2025); arXiv:2502.14297.

**(a) Verification.** A human audit by one researcher (gpt-4o-2024-05-13, MovieLens-100k).

**(b) Findings.**
- 4 of 7 manuscripts (57%) "contained incorrect or hallucinated numerical results".
- Novelty checking relies on "simplistic keyword searches": all 10 generated ideas were classed as novel, including known techniques.
- Median of 5 references per paper, "often with irrelevant citations".
- About $6 per manuscript in API cost, plus about 3.5 hours of human time per manuscript.

**(c) Budget as a decision.** n/a.

**(d) What we cite or differentiate.** Independent evidence that unverified agent output is unreliable. It counts human time, which our resource accounting also does.

### `ghareeb-2026-robin`
**A multi-agent system for automating scientific discovery.** Ali Essam Ghareeb, Benjamin Chang, Ludovico Mitchener, Angela Yiu, et al. *Nature* (2026), doi:10.1038/s41586-026-10652-y. Preprint: "Robin: A multi-agent system for automating scientific discovery", arXiv:2505.13400 (2025), FutureHouse.

**What it does.** Literature agents Crow and Falcon (both PaperQA2-based) write reports. Candidates are ranked by an LLM-judge tournament: Claude 3.7 Sonnet, all pairs when there are ≤25 candidates, otherwise 300 sampled pairs, aggregated with Bradley–Terry.

**(a) Verification.** There is no check that the literature supports the hypotheses. Humans review the ranked list before the wet lab.

**(b) Reliability.**
- On repeated identical comparisons, the judge chose the same hypothesis **88%** of the time; human experts did so **61%** of the time.
- Its top 10 overlapped the experts' top 10 by 7.25 on average.
- No cost is given.

**(c) Budget as a decision.** No. The number of pairs is fixed.

**(d) What we cite or differentiate.**
- **Cite.** It is the one agent paper that *does* measure repeated-judgment consistency.
- **Watch out.** A consistent judge (88%) that disagrees with noisier humans is the reliability ≠ validity problem in miniature. It sits in a ranking role, not evidence checking.

### `mitchener-2025-kosmos`
**Kosmos: An AI Scientist for Autonomous Discovery.** Ludovico Mitchener, Angela Yiu, Benjamin Chang, et al. (37 authors), FutureHouse/Edison. arXiv:2511.02824 (2025), v2.

**(a) Verification.**
- Every statement cites a paper or a notebook, but there is **no automated check** that cited papers support statements.
- Accuracy was graded afterwards by expert evaluators as Supported / Refuted / UNSURE. The evaluators could not see the cited sources, and their number is not stated in the part I read.

**(b) Reliability and cost.**
- 79.4% of 102 statements were accurate:
  - data analysis 85.5%;
  - literature 82.1%;
  - synthesis/interpretation **57.9%**.
- Per run: about 1,500 papers read, about 42k lines of code, up to 12 hours. No dollar figure.

**(c) Budget as a decision.** No.

**Limitations they state.** "there exists no automated method to reliably evaluate if a claim is accurate, novel, and significant"; "Kosmos tends to make excessively strong claims."

**(d) What we cite or differentiate.**
- **Must cite.** It is the strongest current statement, *from an agent builder*, that automated claim checking is unsolved.
- The literature-statement accuracy (82.1%) is a useful population base rate.

### `huang-2025-biomni`
**Biomni: A General-Purpose Biomedical AI Agent.** Kexin Huang, Serena Zhang, Hanchen Wang, Yuanhao Qu, Yingzhou Lu, Yusuf Roohani, et al. (23 authors). bioRxiv, doi:10.1101/2025.05.30.656746 (2025-06-02). I read the abstract via OpenAlex; the bioRxiv page returned 429.

**(a)–(c)** The abstract describes retrieval-augmented planning, code execution and tool and database discovery mined "from tens of thousands of publications". It describes no evidence-verification step and gives no reliability or cost figure for one.

**(d)** Cite it as a representative biology agent whose reliability story is task benchmarks, not checked evidence. It is a weak entry; read the full text before citing anything stronger.

### `helms-andersen-2025-elicit`
**Using Artificial Intelligence Tools as Second Reviewers for Data Extraction in Systematic Reviews: A Performance Comparison of Two AI Tools Against Human Reviewers.** T. Helms Andersen, T. M. Marcussen, A. D. Termannsen, T. W. H. Lawaetz, O. Nørgaard. *Cochrane Evidence Synthesis and Methods* 3(4), 2025, doi:10.1002/cesm.70036 (Crossref).

- **(a)** Human double extraction is the gold standard, over 30 articles.
- **(b)** Elicit P/R/F1 = 92%; ChatGPT 91/89/90%. Confabulations in **4%** of data points.
- **(c)** It recommends replacing the second human extractor with AI, and redirecting the human to reconciling discrepancies. That is a hand-made verification-budget decision.
- **(d)** Elicit has no peer-reviewed system paper. Cite this as the evidence for Elicit-style tools. It shows that practitioners already make the human-vs-AI escalation decision, with no model of when to make it.

---

## B. Measured unsupported-citation rates

### `wu-2025-sourcecheckup`
**An automated framework for assessing how well LLMs cite relevant medical references.** Kevin Z. L. Wu, Eric Qiong Wu, Kevin Wei, Angela Zhang, …, Daniel E. Ho, James Zou. *Nature Communications* (2025), doi:10.1038/s41467-025-58551-6. Preprint arXiv:2402.02008.

- **(a)** SourceCheckup uses GPT-4 as the verifier, with one judgment per statement–source pair, over more than 40k pairs.
- **(b)**
  - 50–90% of responses from five top LLMs are not fully supported.
  - GPT-4 with RAG still leaves about 30% of statements unsupported.
  - The verifier agrees with a panel of medical doctors **88%** of the time.
- **(c)** No.
- **(d)** **Must cite**, as a biomedical analogue of our problem. They validate the verifier once against experts, at one reading. We would ask what that 88% becomes as a function of readings and items.

### `liu-2023-verifiability`
Already in bib-inbox/03. *Findings of EMNLP 2023*, pp. 7001–7025.
- 51.5% of generated sentences are fully supported.
- 74.5% of citations support their sentence.
- Human audit; no budget.

### `rao-2026-reference-hallucinations`
**Detecting and Correcting Reference Hallucinations in Commercial LLMs and Deep Research Agents.** Delip Rao, Eric Wong, Chris Callison-Burch. arXiv:2604.03173 (2026). Abstract read.

- **(a)** A deterministic URL-liveness and Wayback check (`urlhealth`).
- **(b)**
  - 3–13% of citation URLs are hallucinated; 5–18% do not resolve.
  - Deep-research agents hallucinate more URLs than search-augmented LLMs.
  - Self-correction with the tool cuts non-resolving URLs 6–79×.
- **(c)** No.
- **(d)** Shows that the *existence* layer is cheap and deterministic. The *support* layer is the stochastic, expensive one, and it is ours.

### `hirsch-2026-who-is-the-agent-to-blame`
**Who is the Agent to Blame? Localizing Faithfulness and Citation Mistakes in Agentic Deep Research.** Eran Hirsch, David Wan, Han Wang, Elias Stengel-Eskin, Mohit Bansal, Ido Dagan. arXiv:2608.24306; accepted at EMNLP 2026 (main). Abstract read.

- **(a)** Tests each agent invocation for faithfulness against that agent's own inputs.
- **(b)** In AI-Q, 84.7% of final-report errors start at the orchestrator; about 31% of those are hallucinations and the rest are citation errors. Single-document summariser agents are the exception.
- **(c)** Not in the abstract.
- **(d)** Shows *where* in a pipeline verification is most needed. A natural place to spend a per-item verification budget.

### `goo-2026-citation-faithfulness` (the most important challenge in this file)
**Evaluating and Guarding Citation Faithfulness in Agentic Scientific Synthesis.** Taewan Goo, Junsik Kim, Kyulhee Han, GwonYul Jo, Jong-Soo Kim, Tae-Hyung Kim. arXiv:2607.20527 (submitted 10 Jul 2026). Abstract and HTML read.

**(a) Verification.** It audits the *verifier* used by OpenScholar- and PaperQA2-style systems: four open 27–35B models, three agentic pipelines, and three datasets (SciFact, QASA, PubMedQA).

**(b) Reliability and cost.**
- On identical outputs, the unsupported-citation rate ranges **3% to 18% depending only on verifier strictness**.
- Verifiers agree on what is supported but not on what to flag: **negative-specific agreement 0.27–0.30**. Their conclusion is that "no single flag set is trustworthy".
- Remedy: gold-anchored validation plus a split-conformal guard that gives a finite-sample bound on unsupported citations that slip through.
- **No repeated readings.** Generators run at temperature 0. There is no per-citation cost and no agreement-vs-readings analysis.

**(c) Budget as a decision.** Partly. It reports a *review budget*: the flag rate needed at a guaranteed catch rate (for example 6.1% of PaperQA2 citations at a 90% target).

**Limitations they state.** The bound assumes exchangeability; "the guaranteed catch rate must therefore be read as optimistic"; "must not be a sole gate in safety-critical settings".

**(d) Must cite and engage. It is very close to our premise.**
- Its 0.27–0.30 between-verifier agreement independently echoes our r ≈ 0.30 between local judges.
- **Differentiation:**
  - (i) It varies *which* verifier and how strict; we vary *how many readings*, and predict reliability as a function of budget.
  - (ii) Its per-item difficulty is only set-level; ours (H2) is a per-item predictor.
  - (iii) Its guarantee is conformal on catch rate; ours is a predictive law plus a policy scored in advance.
- Conformal guards are a natural **baseline or complement** for H4.

### `reizinger-2026-hallmark`
**HALLMARK: Diagnosing Three Failure Modes in LLM Citation Verifiers.** Patrik Reizinger, Wieland Brendel. arXiv:2607.18360 (2026). Abstract read.

- Benchmark of 2,526 BibTeX entries.
- Agentic lookups gain recall but inflate false positives.
- Base-rate sensitivity: "the false-positive rate, not recall, decides whether a verifier is deployable".
- Most LLMs over-flag papers published after their training cutoff.

**(d)** It is about reference *existence*, not claim support. Its base-rate argument parallels our own κ = 0.29 base-rate caveat. Worth one line.

---

## C. Scientific claim verification and attribution benchmarks

| Key | Citation (verified) | Relevance to us |
|---|---|---|
| `wadden-2020-scifact` | Wadden, Lin, Lo, Wang, van Zuylen, Cohan, Hajishirzi. "Fact or Fiction: Verifying Scientific Claims." EMNLP 2020; arXiv:2004.14974. | Deep read in bib-inbox/03 (the per-sentence κ is there). It is the standard test set; Goo 2026 uses it. Single gold label per item. |
| `wadden-2022-scifact-open` | Wadden, Lo, Kuehl, Cohan, Beltagy, Wang, Hajishirzi. "SciFact-Open: Towards open-domain scientific claim verification." Findings of EMNLP 2022; arXiv:2210.13777. | Open-corpus version. Pooling-based labels make it incomplete, which supports our case for repeated reading (title and venue verified; body not read). |
| `sarrouti-2021-healthver` | Sarrouti, Ben Abacha, Mrabet, Demner-Fushman. "Evidence-based Fact-Checking of Health-related Claims." Findings of EMNLP 2021, pp. 3499–3512. | 14,330 evidence–claim pairs labelled SUPPORT/REFUTE/NEUTRAL, built from COVID claims. A candidate second biomedical test domain. |
| `wang-2023-checkcovid` | Wang, Harwood, Chillrud, Ananthram, Subbiah, McKeown. "Check-COVID: Fact-Checking COVID-19 News Claims with Scientific Evidence." Findings of ACL 2023, pp. 14114–14127. | 1,504 expert-annotated claims with **sentence-level** evidence. That is a gold standard for the "where" regime. Best system F1 76.99; GPT-3.5 69.90. |
| `li-2024-attributionbench` | Li, Yue, Liao, Sun. "AttributionBench: How Hard is Automatic Attribution Evaluation?" Findings of ACL 2024, pp. 14919–14935; arXiv:2402.15089. | Best fine-tuned verifier is about 80% macro-F1. Failures come from "inability to process nuanced information" and from a mismatch between what the model sees and what human annotators saw. **This establishes that a single-shot verifier is a hard, unsolved task.** |
| `gao-2023-alce` | Gao, Yen, Yu, Chen. "Enabling Large Language Models to Generate Text with Citations." EMNLP 2023; arXiv:2305.14627. | Deep read in bib-inbox/03. Single NLI call per citation. |
| `liu-2023-verifiability` | See § B. | |
| `wei-2024-safe` | Wei, Yang, Song, Lu, Hu, et al. "Long-form factuality in large language models." NeurIPS 2024; arXiv:2403.18802. | The verifier is a multi-step search agent: 72% agreement with humans on about 16k facts, wins 76% of disagreements, **>20× cheaper than humans**. The canonical "verification cost" number, reported as a ratio and not as a curve. |

**(a)–(c) for the benchmarks as a class.** They supply items and a single gold label. None of them repeats a verifier reading, models reliability against the number of readings, or treats the verification budget as a decision.

---

## D. The cost of verification and budgeted judging (adjacent work)

This is the closest prior art to H1, H2 and H4. Only one item here verifies *evidence support*. These overlap the judge-reliability and inference-scaling survey files, so integrate and deduplicate.

| Key | Verified citation | What it does | Threat to |
|---|---|---|---|
| `aggarwal-2023-adaptive-consistency` | Aggarwal, Madaan, Yang, Mausam. "Let's Sample Step by Step: Adaptive-Consistency for Efficient Reasoning and Coding with LLMs." EMNLP 2023, pp. 12375–12396. | Per-item stopping rule from agreement among samples so far: up to 7.9× fewer samples, <0.1% accuracy loss. | **H4.** An agreement-driven stopping rule is established. Our H4 baseline set must include it. |
| `li-2024-esc` | Li, Yuan, Feng, Pan, et al. "Escape Sky-high Cost: Early-stopping Self-Consistency for Multi-step Reasoning." arXiv:2401.10480 (2024). OpenAlex record only. | Early stopping for self-consistency. | H4 |
| `wang-2025-dsc` | Wang, Feng, Li, Yuan, Zhang, Tan, Pan, Hu, Li. "Make Every Penny Count: Difficulty-Adaptive Self-Consistency for Cost-Efficient Reasoning." Findings of NAACL 2025, pp. 6919–6932. | Allocates samples using **prior** (pre-sampling) as well as posterior difficulty, batch-wise. | **H2.** "Predict per-item budget from difficulty before reading" exists for reasoning answers. Our novelty must rest on *verification of evidence*, on *validity*, and on a *pre-registered law*. |
| `saha-2026-judge-on-a-budget` | Saha, Wagde, Kveton. "LLM-as-Judge on a Budget." arXiv:2602.15481 (2026). | Variance-adaptive allocation of a fixed budget of judge queries across items, with a bandit bound of Õ(√(Σσᵢ²/B)). Variance is estimated adaptively, not from features. | **H2/H4.** Budgeted allocation of repeated judgments is published. |
| `yagubyan-2026-coin-flip-judge` | Yagubyan. "The Coin Flip Judge? Reliability and Bias in LLM-as-a-Judge Evaluation." arXiv:2606.13685 (2026). | 50 trials per item. Majority vote needs 11 trials on average (15 for high-variance items) to reach 95% recovery of the 50-trial verdict. Pairwise verdicts flip 13.6% of the time. | **H1.** An empirical reliability-vs-trials curve exists. As far as the abstract shows, it has no parametric law, does not predict out of sample, and does not test evidence support. |
| `norman-2026-reliability-without-validity` | Norman, Rivera, Hughes. "Reliability without Validity: A Systematic, Large-Scale Evaluation of LLM-as-a-Judge Models Across Agreement, Consistency, and Bias." arXiv:2606.19544 (2026). | 21 judges, ~541k judgments. Test–retest >0.95 coexists with position bias >0.10. | **Our framing sentence.** "Reliability does not imply validity" is now a published title. Cite it; do not present the point as ours. |
| `ding-2026-calibrate-then-act` | Ding, Tomlin, Durrett. "Calibrate-Then-Act: Cost-Aware Exploration in LLM Agents." arXiv:2602.16699 (2026, v3). | Formalises when to stop gathering information as sequential decisions under uncertainty with explicit cost, including on a RAG QA task. | **H4 framing.** The "read again / escalate / stop" decision under cost is an established agent-decision framing. |
| `gangwani-2025-citeguard` | Gangwani, Bansal. "CiteGuard: Retrieval-Augmented Citation Verification for LLM-Powered Peer Review." NeurIPS 2025 workshop (AI for Science), spotlight. | Calibrated score (isotonic regression), then escalation of uncertain cases to a small LM (18% escalated); $0.0028 per review. | H4, as a cascade/escalation pattern. It verifies reference metadata, not claim support. |
| `kapoor-2024-agents-that-matter` | Kapoor, Stroebl, Siegel, Nadgir, Narayanan. "AI Agents That Matter." arXiv:2407.01502 (2024). | Argues agent evaluation must jointly report cost and accuracy. | Supports our evaluation design (matched-cost baselines). |

---

## E. Argonne and DOE programmes (duplication check)

### P1. IDeA: Intelligent Design Assistant for Enzyme Discovery and Biosynthetic Pathway Optimization

**Sources.**
- ORNL project page (fetched): http://www.ornl.gov/projects/idea-intelligent-design-assistant-enzyme-discovery-and-biosynthetic-pathway-optimization
- Argonne press release "Argonne puts artificial intelligence to work on biology's big challenges", Newswise DOE Science, **14 Sep 2026** (fetched).
- Argonne Genesis-award release, 12 Mar 2026 (Newswise, fetched).
- The DOE Genomic Science page `genomicscience.energy.gov/ai-idea/` (marked DRAFT in search results) returned 404 when fetched.

**Facts.**
- **Lead and team.** Argonne leads, with Arvind Ramanathan as lead (Argonne releases). ORNL is the experimental and enzyme-engineering partner; Josh Michener is the ORNL contact.
- **Funding.** "IDeA is supported by the DOE Biological and Environmental Research program", "funded in concert with DOE's Genesis Mission".
- **What was not found.** No award amount, start date or end date on any page.
- **Aim.** "establish the first BER-centered autonomous agentic system functioning as an AI co-scientist". Agents search literature and databases; "roughly 3 million scientific documents in about a week on a supercomputer". The initial target is enzymes for nylon-like biopolymers.

**Verification language.**
- The ORNL page contains none of: hypothesis, verification, evidence, uncertainty, trust, reliability, validation.
- The Argonne release says that when agents disagree ("a real risk when multiple systems run independently"), the system is designed to resolve the conflict and stay grounded, "ensuring results are trustworthy and reproducible". No method is described.

**Duplication verdict.** **Low risk on substance; moderate risk on perception.**
- IDeA's public scope is building an agentic co-scientist. Nothing public says it studies the reliability or cost of evidence checking as a function of budget.
- However, IDeA *does* publicly claim an agent-disagreement and conflict-resolution mechanism for trustworthy, reproducible results. A reviewer could read our project as that component.

**What the draft should do.**
- (i) Quote that sentence and position our work as the *measurement and theory* underneath any such mechanism: how many readings, and at what cost, are needed before "agents agree" means "evidence supports".
- (ii) **Contact Ramanathan before submission** to confirm that IDeA has no verification-budget workstream, and ideally obtain a letter naming IDeA as a consumer.
- (iii) Drop "Genesis-funded" in favour of "BER-funded, part of the Genesis Mission". That is what the official pages say, and I could not find that IDeA was funded through DE-FOA-0003612.

### P2. BV-BRC Copilot

**Citation.** Shukla, Wattam, Aleman, Bhattacharya, Bowers, Brettin, et al. (43 authors). "BV-BRC: a unified bacterial and viral bioinformatics resource with expanded functionality and AI integration." *Nucleic Acids Research* **54(D1): D715–D723**. Online 18 Nov 2025; issue dated 6 Jan 2026. doi:10.1093/nar/gkaf1254 (Crossref).
- **Correction to the draft:** cite it as NAR 2026;54(D1), published online 2025, not "NAR 2025".

**Scope.** "BV-BRC Copilot, provides an AI-powered natural-language interface that combines large language models with retrieval-augmented generation to guide users through data exploration, analysis workflows, and knowledge integration". 14M+ genomes, 33 services.

**(a)–(c).** The abstract describes no evidence-verification step and no reliability evaluation.

**(d)** A legitimate deployment target. It shows verification is absent from a production DOE/NIAID-adjacent RAG system, which argues for need, not duplication. The "NIAID, to 2029" claim in the draft was **not verified** by me.

### P3. Genesis Mission (EO 14363)

- Executive Order 14363, "Launching the Genesis Mission", signed **24 Nov 2025**; published at 90 FR 55035 on 28 Nov 2025 (whitehouse.gov and govinfo.gov via search results).
- On **10 Dec 2025**, DOE announced "over $320 million" across four initiatives: AmSC, ModCon, robotics/automation (14 projects), and Foundational AI (37 awards). Source: energy.gov/node/4855500, fetched.
- The only reliability language in that announcement: Foundational AI awards should make systems "robust, reliable, and rigorously validated for scientific applications".

### P4. DE-FOA-0003612, "The Genesis Mission: Transforming Science and Energy with AI"

Read in full text, Amendment 000003.

**Dates and money.**
- Issued 17 Mar 2026; amendments on 10, 17 and 20 Apr 2026.
- Deadlines:
  - FY26 Phase I applications and Phase II LOIs: 1 May 2026;
  - Phase II applications: 19 May 2026;
  - Phase II applications from FY26 Phase I awardees: 17 Dec 2026.
- About $293.76M in total.
- Phase I: $500k–$750k for about 9 months (budget period 1 Jul 2026 – 31 Mar 2027).
- Phase II: 3 years, "3 to 5 times the Phase I award per year".

**Confirmed for the draft.** "In FY27, DOE plans to amend the RFA or to issue an alternative funding opportunity to update the topic and focus areas to allow a second competition of Phase I small team applications and Phase II large team applications." The draft's claim is correct.

**Topics.** 21 topics. Those nearest to us:
- **Topic 19, AI for Scientific Reasoning** (ASCR). Its challenge text says LLMs "often lack the rigorous, verifiable, and multi-modal reasoning abilities". Focus areas:
  - A, Trustworthy Mathematical and Symbolic Reasoning;
  - **B, Hypothesis Generation from Multi-Modal Data**, which includes "scientific literature";
  - C, Composable and Modular Foundation Models.
- Topic 20B, Data Provenance and Integrity Verification. This is about data poisoning, not evidence support.
- Topic 18E, Trustworthy AI for Scientific Software.
- Topic 2, Biotechnology.

**Not in the RFA.** It does not fund or mention the reliability or cost of verifying literature evidence, judge reliability, or verification budgets. The word "verification" appears only for formal methods, code, and data provenance.

**Risk.** A future Topic 19 award could absorb our question. Today, our work is *complementary* to 19B: it is the verification step any 19B hypothesis generator needs.

### P5. ModCon (Transformational AI Models Consortium)

- Argonne-led, with Rick Stevens as lead.
- Mission: "building and deploying self-improving AI models that advance science, engineering and energy missions". Source: anl.gov/genesis-mission/modcon, fetched, undated.
- Award list: "$30 Million for Transformational AI Models Consortium", LAB 25-3560 (science.osti.gov award list, via search).
- The **13 seed teams with Argonne involvement** are all domain model teams (code, microelectronics, quantum, grid, combustion, facilities, minerals, FAMOUS biology, HEP, fusion, magnets, accelerators, nuclear). **None** is about evaluation, verification, or literature agents.
- The page does not mention evaluation, benchmarks, verification or trust.
- The LDRD call text describes ModCon as building "self-improving AI models and evaluation tools". Our contribution as an *evaluation instrument* fits that description, and no seed team occupies it.

### P6. AmSC (American Science Cloud)

- Source: amsc.energy.gov, fetched.
- "an integrated, federated platform that connects AI models, curated scientific data, workflows, and computing resources". Twelve lab infrastructure partners, including Argonne.
- Led by ORNL (Georgia Tourassi), per the Argonne and LBNL releases.
- The page does not mention agents, evaluation, verification, provenance or trust.
- The draft's "run at scale on AmSC" is plausible but aspirational. AmSC says it will power use cases "once deployed".

### P7. Other Argonne Genesis awards to flag

From the Argonne release of 12 Mar 2026:
- **FORUM-AI** (LBNL-led; Maria Chan at Argonne): "an AI research planner for materials science papers", "a registry of AI reasoning agents", and a framework that grounds predictions in data. **This is the nearest Genesis award to "literature agents plus grounding".** It is a materials domain and has no stated verification-budget aim.
- **ISAAC** (SLAC-led): agentic AI for catalysis.
- **MIRAGE** (Sandia-led): "Reliable/Interpretable AI".

None states a verification-reliability or budget aim. Name FORUM-AI in the non-duplication sentence.

### EAIRA, Argonne's own evaluation programme

`cappello-2025-eaira`: Cappello, Madireddy, Underwood, Getty, … Ian Foster, Rick Stevens (26 authors, Argonne). "EAIRA: Establishing a Methodology for Evaluating AI Models as Scientific Research Assistants." arXiv:2502.20309 (2025). Journal version: "EAIRA: Establishing a methodology for evaluating LLMs as scientific research assistants", *Int. J. High Performance Computing Applications* (2026), doi:10.1177/10943420261467786 (OpenAlex).

- **(a)–(c)** Four evaluation classes: MCQ, open response, lab-style experiments, and field-style experiments. The abstract does not address evidence verification, repeated judgments, or the cost of evaluation.
- **(d)** **Must cite.** It is Argonne's existing evaluation line, led by the ModCon lead's group. We are a component it lacks: the reliability and budget of the verification step, per item. **It is also the most likely home-lab reviewer objection** ("isn't this EAIRA?"). Differentiate explicitly.

---

## CHALLENGES OUR FRAMING / DUPLICATION RISK

**Frank verdict.** The *gap* survives, but it is narrower than the draft's Novelty paragraph implies. Three of the draft's implicit novelty claims are already published, and need to be conceded and cited.

1. **"Reliability is not validity" for LLM judges is now a paper title** (Norman et al. 2026). Robin's 88% vs 61% consistency result is a concrete instance inside a science agent. The draft's sentence "reliability does not imply validity" must cite this and claim no originality for it. Our originality is *testing whether reliability-at-budget predicts expert accuracy, per item, for evidence support*.

2. **Budgeted, adaptive allocation of repeated LLM judgments exists.** These include Saha et al. 2026 (variance-adaptive judge queries), Adaptive-Consistency (EMNLP 2023), ESC (2024) and DSC (NAACL Findings 2025, which uses a *prior* difficulty estimate).
   - H4 ("a policy driven by a per-item predictor beats fixed k") and H2 ("cheap features predict budget before reading") are therefore **not new as ideas**.
   - H4's baselines must include an Adaptive-Consistency-style agreement stopping rule and a variance-adaptive allocator, not just fixed k, one frontier reading and majority vote. Otherwise a reviewer will say the policy beats a straw man.
   - What remains new:
     - (i) the object is *evidence-support verification of retrieved documents*, a graded, localisation-bearing judgment, not a final answer;
     - (ii) a *pre-registered parametric law* (Spearman–Brown) scored out of sample;
     - (iii) the whether/where split;
     - (iv) validity against expert gold;
     - (v) composition across model families (H3).

3. **The empirical "reliability vs number of trials" curve for judges exists** (Coin Flip Judge, 2026: 11–15 trials to 95%). H1's novelty is the *law and its out-of-sample prediction*, not the curve.

4. **Goo et al. 2026 is the closest paper to our premise, and it is in our exact application.** It audits citation verifiers in OpenScholar- and PaperQA2-style science agents and finds between-verifier negative agreement of 0.27–0.30 and verifier-dependent unsupported rates of 3–18%. It already supplies a deployable guard (split-conformal). We differ on axis (readings and budget, not verifier choice), on granularity (per item), and on the product (a predictive law plus a cost policy). But a reviewer who knows it will ask why a conformal guard is not enough. The draft should name it and say: conformal bounds the catch rate at a fixed verifier; we predict what budget is required to reach a reliability, and whether that reliability is accuracy.

5. **Agent builders do not frame verification as a budget decision.** No surveyed agent (PaperQA2, OpenScholar, co-scientist, AI Scientist, Robin, Kosmos, Biomni) does, which supports the gap. But the reason may be that they skip verification altogether (Kosmos: "there exists no automated method to reliably evaluate if a claim is accurate"). A reviewer may counter that the field's bottleneck is *having any* reliable verifier (AttributionBench reaches about 80% at best), not *budgeting* one. Our answer must be that budgeting repeated readings is one of the ways to *get* a reliable verifier from unreliable readings. Make that explicit.

6. **IDeA and Genesis duplication.**
   - **IDeA: low substantive risk, real perception risk.** Its release claims a mechanism that resolves agent disagreement to make results "trustworthy and reproducible". Address it by name. Get Ramanathan's confirmation and, ideally, a consumer letter.
   - **Genesis RFA DE-FOA-0003612: no topic funds verification budgets.** Topic 19B, hypothesis generation from literature, is the adjacent area and a plausible FY27 follow-on vehicle. That is consistent with the draft.
   - **ModCon seed teams: none is about evaluation.**
   - **EAIRA (Argonne, Cappello/Foster/Stevens)** is the in-house evaluation programme a reviewer will think of first. It is not Genesis-funded per anything I found, but it is the duplication question an Argonne panel is most likely to raise.

7. **Smaller corrections for the draft.**
   - BV-BRC Copilot citation year: NAR 2026;54(D1), online Nov 2025.
   - Co-scientist title changed in v2.
   - "Genesis-funded IDeA" should read "BER-funded, part of the Genesis Mission".

---

## Searched and did not find

| Query (tool) | Result |
|---|---|
| "science agent citation verification cost 'verification budget' literature evidence LLM" (WebSearch) | No paper uses "verification budget" for evidence checking. Found CiteAudit, CiteGuard and Goo 2026, none of which models budget vs reliability of claim support. |
| "repeated LLM judge samples reliability attribution verification number of samples per item budget" (WebSearch) | Judge-reliability papers (Coin Flip, Judge on a Budget, Reliability without Validity). **None on attribution or evidence support.** |
| Agent papers read for a repeated-verification step: PaperQA2 (HTML), OpenScholar (HTML, incl. limitations), co-scientist (HTML, incl. limitations), AI Scientist (HTML), Robin (HTML), Kosmos (HTML) | None runs its support/citation check more than once per item. Only PaperQA2 repeats *whole-system* runs (×3). Only Robin measures judge self-consistency (in ranking). |
| "IDeA enzyme discovery co-scientist Argonne" (OpenAlex) | No IDeA publication indexed. Only Gueroudji et al. 2026, "Beyond Centralized Labs: Federating the Co-Scientist" (Argonne/UChicago; federated architecture, no verification). |
| "BV-BRC Copilot large language model retrieval augmented" (OpenAlex) | Only the NAR resource paper. No standalone Copilot evaluation paper. |
| ModCon page, AmSC page, DOE 10 Dec 2025 release, DE-FOA-0003612 full text: grep for verif / trustworth / reliab / literature / hallucinat | No funded scope on the reliability or cost of evidence verification by literature agents. |
| arXiv API: `abs:"claim verification" AND abs:"self-consistency"`; `abs:"citation verification" AND abs:agent AND abs:cost`; `abs:attribution AND abs:verification AND abs:"compute budget"` | **Not executed: HTTP 429.** These need rerunning before the "no study we found" sentence is final. |
| OpenAlex: "Spearman-Brown LLM judge repeated sampling reliability"; "self-consistency scientific claim verification repeated LLM samples"; "budget allocation verification citations agent per-claim adaptive" | **Not executed: HTTP 429.** Same caveat. |

---

## Established names (adopt; do not coin)

| Our phrasing | Established name and source |
|---|---|
| "read again until readings agree, then stop" | **Adaptive-Consistency** (Aggarwal 2023); **early-stopping self-consistency** (ESC, Li 2024) |
| "predict the per-item budget from difficulty" | **difficulty-adaptive self-consistency** (DSC, Wang 2025); **variance-adaptive allocation** (Saha 2026) |
| "read again / escalate / stop" | **cost-aware exploration** (Calibrate-Then-Act, Ding 2026); **escalation / cascade** (CiteGuard) |
| "the verdict reproduces" | **test–retest reliability** (Norman 2026); **flip rate** (Yagubyan 2026) |
| "reliability ≠ validity" | Norman et al. 2026 title; classical psychometrics |
| "does the document support the claim" | **attribution / AIS** (bib-inbox/03); **citation faithfulness** (Goo 2026); **verifiability** (Liu 2023); **unsupported-citation rate** (Goo 2026; Wu 2025) |
| "review budget at a guaranteed catch rate" | **split-conformal guard** (Goo 2026) |
| "cost-matched comparison" | **cost-controlled agent evaluation** (Kapoor 2024) |
| Hallucinated reference vs unsupported citation | **reference/citation hallucination** (existence; Rao 2026, HALLMARK) vs **citation faithfulness/support** (ours). The pre-proposal should use "unsupported citation", not "hallucinated citation", for our object. |

---

## COULD NOT VERIFY

- **IDeA award amount, dates and funding vehicle.** Tried: ORNL page, the Argonne releases of Mar and Sep 2026, and `genomicscience.energy.gov/ai-idea/` (404). Whether IDeA was funded via DE-FOA-0003612 or a separate BER lab call is unknown.
- **The Nature versions** of the AI Scientist (doi:10.1038/s41586-026-10265-5), Robin and OpenScholar exist per OpenAlex. I read the arXiv preprints only, so any numbers above are preprint numbers.
- **Kosmos evaluator count.** Not in the portion read (the HTML was truncated at 100k characters).
- **BV-BRC "NIAID, to 2029"** (from the draft). Not checked.
- **ModCon $30M award list** (science.osti.gov `transformational-AI-models-3560.pdf`). Seen in search results only, not fetched.
- **Schroeder & Wood-Doughty, "Can You Trust LLM Judgments?" (arXiv:2412.12509).** Seen only as a search snippet. See `07-judge-reliability-budget.md` (`schroeder-2024-trust`).
- **LAB-Bench** (Laurent et al., arXiv:2407.10362). Metadata verified, content not read. It is FutureHouse's benchmark behind LitQA2; cite only for the existence of LitQA2.
- **CiteAudit (arXiv:2602.23452).** Seen only on alphaxiv. Not verified.
- **xue-2026** (the draft's "near-perfect intra-model consistency" counterclaim). Not searched here; see `07-judge-reliability-budget.md` §0.
