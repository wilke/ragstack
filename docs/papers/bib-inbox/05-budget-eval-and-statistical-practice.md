# 05 — Evaluating retrieval under a context budget; statistical and methodological practice

Bibliography inbox entry. Two areas:

- **Area A** — evaluating retrieval under a token budget; document vs. passage units; position-of-evidence; RAG evaluation frameworks and their critiques; the reach/containment decomposition.
- **Area B** — pre-registration, statistical power, multiple comparisons, reproducibility, and equivalence/non-inferiority testing in IR and NLP.

## Verification conventions used here

Every entry below was checked against a primary or authoritative source. The tag on each entry says exactly which:

- **verified (page)** — I fetched the publisher landing page (ACL Anthology, arXiv `/abs/`, PMLR, CEUR, Springer, sigir.org, institutional repository) and read title / full author list / venue / year off it.
- **read** — I additionally fetched and read the full text (PDF or HTML), and anything quoted below is quoted from that text.
- **verified (record)** — the publisher page was unreachable in this session (ACM DL returns HTTP 403; dblp.org is behind an Anubis bot wall; Springer bounces to an IdP). Metadata was confirmed from an authoritative bibliographic index (OpenAlex with DOI, or the publisher's own institutional-repository record). Flagged individually so you can re-check before the camera-ready.

Nothing appears here that was only seen in a search-result snippet. Items that could not be confirmed are in the **COULD NOT VERIFY** section at the end, with the queries tried.

`docs/papers/bibliography.md` did not exist on disk when this file was written, so the `allamraju-2025` worked example could not be read. The baseline-check discipline it encodes has been applied anyway: every entry reporting a dramatic headline gain carries an explicit **Baseline check** line.

---

# Area A — evaluating retrieval under a context budget

## A.1 Budget-normalised retrieval evaluation (the closest prior art we have)

### `chen-2024-dense-x`
**Dense X Retrieval: What Retrieval Granularity Should We Use?**
Tong Chen, Hongwei Wang, Sihao Chen, Wenhao Yu, Kaixin Ma, Xinran Zhao, Hongming Zhang, Dong Yu.
Proceedings of the 2024 Conference on Empirical Methods in Natural Language Processing (EMNLP 2024), pp. 15159–15177. 2024.
<https://aclanthology.org/2024.emnlp-main.845/> — **read**

**This is the single most important entry in the file.** It is the closest published thing to budget-normalised retrieval evaluation, and it already has a notation for it. The paper compares three indexing granularities over Wikipedia — passage, sentence, proposition — on NQ, TriviaQA, WebQ, SQuAD and EntityQuestions. Two budget-normalised constructs appear verbatim:

- *"To fairly compare different granularity with the same computation budget, we limit the number of retrieved tokens for input to the language model at l = 100 or 500 tokens."* The resulting downstream metric is written **`EM @ l tokens`** — *"the maximum number of retrieved tokens is capped at l = 100 or 500, i.e. only the top l tokens from passage, sentence, or proposition level retrieval are fed into the language model as input… We denote our metric as EM @ l tokens."*
- A retrieval-side counterpart: *"we calculate the recall of the gold answer within the initial l retrieved words… with l ranging from 0 to 500 across all five datasets. For a fixed word retrieval budget, proposition retrieval shows a higher success rate than sentence and passage retrieval."*

Their stated mechanism is the one our study rests on: finer units give *"a higher density of question-related information in the prompts."*

- **Unit of evaluation:** both — Recall@k at fixed k for the retrieval tables, and a token/word budget (`EM @ l tokens`, gold-answer recall within top-*l* words) for the budget analysis.
- **Corpus lengths:** English Wikipedia, ~100-word passages (DPR-style), decomposed to sentences and to propositions. Short documents — not a long-document study.
- **Behind a reranker?** No. Bi-encoder retrievers only (SimCSE, Contriever, DPR, ANCE, TAS-B, GTR), no cross-encoder stage.

**Weight:** Load-bearing — adopt `EM @ l tokens` / "fixed word retrieval budget" as the existing name and cite this as prior art rather than coining a new term.

---

### `lu-2025-hichunk`
**HiChunk: Evaluating and Enhancing Retrieval-Augmented Generation with Hierarchical Chunking**
Wensheng Lu, Keyu Chen, Ruizhi Qiao, Xing Sun.
arXiv:2509.11552, 15 Sep 2025 (17 pages, 5 figures, 6 tables; no peer-reviewed venue stated on the abs page).
<https://arxiv.org/abs/2509.11552> — **read** (PDF)

States the budget-parity rule explicitly as a *fairness* requirement for chunking comparisons: *"to ensure fair comparison, we fix the retrieval token budget"*, because different chunking strategies consume different numbers of tokens when retrieving the same number of chunks. They run at a 4K-token retrieval budget. Also diagnoses why existing RAG benchmarks cannot assess chunking at all — **"evidence sparsity"**: on standard QA benchmarks the gold evidence is so concentrated that chunking barely moves the score, so they build HiCBench with evidence-dense QA pairs.

- **Unit:** token budget (4K) for the generator input; chunk-level evidence recall for retrieval.
- **Corpus:** HiCBench plus GutenQA/OHRBench-style long documents; hierarchical multi-level annotated chunk boundaries.
- **Behind a reranker?** Not clearly specified in the text I extracted; the Auto-Merge retrieval algorithm is the aggregation step.

**Weight:** High — the explicit statement that fixed-*k* comparison across chunk sizes is unfair, and that budget parity is the fix, is exactly our premise, stated by someone else first. Also gives us "evidence sparsity" as the name for why most benchmarks cannot see this effect.

---

### `smith-2024-chroma-chunking`
**Evaluating Chunking Strategies for Retrieval**
Brandon Smith, Anton Troynikov. Chroma Research technical report, 3 July 2024.
<https://www.trychroma.com/research/evaluating-chunking> — **verified (page)**, metric definitions read

Defines **token-level** retrieval metrics against gold excerpt tokens `t_e` and retrieved tokens `t_r`:
recall `= |t_e ∩ t_r| / |t_e|`; precision `= |t_e ∩ t_r| / |t_r|`; and **IoU** `= |t_e ∩ t_r| / |t_e ∪ t_r|`, with the note that *"in the numerator, we count each t_e among t_r only once, while counting all retrieved tokens in |t_r| in the denominator"* — i.e. the denominator is the delivered-token cost, which is precisely budget normalisation.

- **Unit:** tokens, both sides.
- **Corpus:** their own generated benchmark over several corpora; not long-document.
- **Behind a reranker?** No.

**Weight:** Medium-high — not peer-reviewed, but it is the most-cited statement of *token-level* precision/recall/IoU for chunking, and it is where practitioners get the vocabulary. Cite as a technical report, not as evidence.

---

### `dallaire-2025-practical-rag-eval`
**Practical RAG Evaluation: A Rarity-Aware Set-Based Metric and Cost-Latency-Quality Trade-offs**
Etienne Dallaire. arXiv:2511.09545, 12 Nov 2025 (cs.IR; single author; no venue stated).
<https://arxiv.org/abs/2511.09545> — **verified (page)**

Argues rank-based IR metrics are the wrong shape for RAG and proposes a set-based metric `RA-nWG@K` measuring *"whether the prompt at cutoff K contains the decisive evidence"*, with oracle ceilings (PROC, %PROC), and sweeps configurations to retain Pareto-optimal points in a Cost–Latency–Quality space for *"reproducible, budget/SLA-aware decisions."*

- **Unit:** the set of passages in the prompt at cutoff K; cost measured in a CLQ frame, not tokens delivered.
- **Corpus:** a scientific-papers corpus.
- **Behind a reranker?** Yes — rerankers and quantization are among the swept dimensions.

**Weight:** Medium — establishes "budget-aware decision" framing and set-based (rather than rank-based) RAG retrieval metrics. Single-author preprint; treat claims as unrefereed.

---

### `qian-2026-budget-aware-active-rag`
**When Should Active RAG Retrieve? A Budget-Aware Evaluation of Utility, Calibration, and Cost**
Pin Qian, Su Wang, Chong Peng, Junxian You, Lifei Liu, Haoran Yu, Yihang Chen, Xiaochong Jiang.
arXiv:2607.24010, 27 Jul 2026. Venue field: ACM SIGKDD KDD 2026 Workshop on Evaluation and Trustworthiness of Agentic AI.
<https://arxiv.org/abs/2607.24010> — **verified (page)**

Recasts active retrieval as *utility estimation* — retrieval has value only through marginal correctness change over a no-retrieval answer — and makes the budget-comparability point sharply: two systems can both claim a 50% retrieval budget while realising different held-out usage rates, so higher accuracy may reflect a looser budget rather than a better policy. Recommends reporting frontiers, realised usage, threshold-transfer error, harm rates and cost decompositions rather than single-point accuracy. Cost is normalised to token-equivalents against always-RAG.

- **Unit:** token-equivalent cost normalised by always-RAG; retrieval-invocation budget.
- **Behind a reranker?** Not the focus; routing/retrieval-decision policies.

**Baseline check:** The paper is itself deflationary — it reports that *simple baselines often match learned routers*. No inflated headline.

**Weight:** Medium — the "realised budget vs. claimed budget" hazard is directly transferable to our delivery-strategy comparison. Workshop paper.

---

### `peng-2025-adagres`
**AdaGReS: Adaptive Greedy Context Selection via Redundancy-Aware Scoring for Token-Budgeted RAG**
Chao Peng, Bin Wang, Zhilei Long, Jinfang Sheng. arXiv:2512.25052, 31 Dec 2025. Comments field: "Preprint. Under review."
<https://arxiv.org/abs/2512.25052> — **verified (page)**

Frames context selection as greedy selection under a token-budget constraint, combining query-chunk relevance with an intra-set redundancy penalty; motivated by top-*k* returning near-duplicate chunks that *"waste token budget."*

**Weight:** Low-medium — useful as evidence that "token-budgeted RAG" is now standing terminology, and that redundancy is a first-class term in the budget frame. Unrefereed.

---

### `pradeep-2024-autonuggetizer`
**Initial Nugget Evaluation Results for the TREC 2024 RAG Track with the AutoNuggetizer Framework**
Ronak Pradeep, Nandan Thakur, Shivani Upadhyay, Daniel Campos, Nick Craswell, Jimmy Lin.
arXiv:2411.09607, 14 Nov 2024.
<https://arxiv.org/abs/2411.09607> — **verified (page)**

Revives the TREC 2003 QA nugget methodology for RAG: LLM-generated atomic information units (nuggets) are automatically assigned to system answers. Reports strong correlation between fully automatic nugget scores and (mostly) manual nugget assessment over 21 topics and 45 runs.

- **Unit:** nuggets (atomic facts) present in the generated answer — *not* tokens, and not passages.
- **Behind a reranker?** TREC RAG runs vary; not controlled here.

**Weight:** Medium — nugget recall is the strongest existing *content*-normalised alternative to rank-based metrics, and is the official TREC direction. It normalises by information units rather than by delivered tokens, so it is adjacent to but not the same as our question.

---

## A.2 Document-level vs. passage-level units, and max-passage aggregation

### `dai-2019-deeper-text`
**Deeper Text Understanding for IR with Contextual Neural Language Modeling**
Zhuyun Dai, Jamie Callan. SIGIR 2019 (42nd ACM SIGIR). arXiv:1905.09217, 22 May 2019; journal-ref field: SIGIR 2019.
<https://arxiv.org/abs/1905.09217> — **verified (page)**

Origin of the FirstP / MaxP / SumP passage-score-aggregation family for applying a 512-token BERT to full documents: split the document into passages, score each, aggregate to a document score.

- **Unit:** document (scored via passages).
- **Corpus:** Robust04 (news, long) and ClueWeb09-B.
- **Behind a reranker?** Yes — BERT reranks a BM25/SDM first-stage list.

**Weight:** High — this is where MaxP comes from, and the mechanism it papers over (a long document gets *n* chances to produce a high-scoring passage) is exactly the artefact our budget normalisation is designed to remove.

---

### `zhang-2021-score-aggregation`
**Comparing Score Aggregation Approaches for Document Retrieval with Pretrained Transformers**
Xinyu Zhang, Andrew Yates, Jimmy Lin.
Advances in Information Retrieval — 43rd European Conference on IR Research (ECIR 2021), LNCS 12657, Part II, pp. 150–163. Springer, 2021. DOI 10.1007/978-3-030-72240-1_11.
<https://cs.uwaterloo.ca/~jimmylin/publications/ZhangXinyu_etal_ECIR2021.pdf> — **read** (author PDF; the Springer landing page bounces to an IdP)

Reproduction of Dai & Callan. Conclusion, quoted: *"We found that the MaxP aggregation approach is the most effective, and furthermore, the differences between MaxP and AvgP are larger than in the original work. We generalized this finding by conducting the same experiments on the GOV2 dataset and reaching the same conclusion."* Also: MaxP benefits from pre-fine-tuning on MS MARCO passages, but not from swapping BERT for a newer variant.

- **Unit:** document, via aggregated passage scores.
- **Corpus:** Robust04 and GOV2 — both long-document, broad-information-need collections.
- **Behind a reranker?** Yes — this *is* the reranking stage, over a BM25 first stage.

**Weight:** High — the canonical citation for "max-passage is the default, and it wins", which is the practice our decomposition questions.

---

### `li-2020-parade`
**PARADE: Passage Representation Aggregation for Document Reranking**
Canjia Li, Andrew Yates, Sean MacAvaney, Ben He, Yingfei Sun.
arXiv:2008.09093, v1 20 Aug 2020, v2 10 Jun 2021. **No venue stated on the arXiv abs page** — widely cited as ACM TOIS 42(2), 2024, but I could not confirm that from a publisher page in this session; verify before citing the TOIS reference.
<https://arxiv.org/abs/2008.09093> — **verified (page)**

Aggregates passage *representations* rather than passage *scores*. The finding that matters to us, quoted from the abstract: aggregation *"can significantly improve over techniques proposed in prior work, such as taking the maximum passage score"*, and — critically — the advantage appears **on collections where relevance signal is spread through the document** (Robust04, GOV2) but not where relevance is concentrated in a single passage (TREC DL, TREC Genomics).

- **Unit:** document, via aggregated passage representations.
- **Corpus:** Robust04, GOV2, TREC DL, TREC Genomics — the long/short contrast is the point.
- **Behind a reranker?** Yes.

**Weight:** High — the dispersion-vs-concentration split is the retrieval-side twin of our containment question, and PARADE is the paper that demonstrates it changes which method wins.

---

### `boytsov-2025-positional-bias`
**Positional Bias in Long-Document Ranking: Impact, Assessment, and Mitigation**
Leonid Boytsov, David Akinpelu, Nipun Katyal, Tianyi Lin, Fangwei Gao, Yutian Zhao, Jeffrey Huang, Eric Nyberg.
arXiv:2207.01262, v1 4 Jul 2022, v4 12 Nov 2025. Comments field: accepted at IJCNLP-AACL 2025 Main.
<https://arxiv.org/abs/2207.01262> — **verified (page)**

Evaluates 20+ transformer rankers on long documents. Two results we need: (1) most long-document models **fail to substantially beat a baseline that reads only the first 512 tokens**; (2) the reason is that in existing benchmarks (including BEIR collections) *"most relevant passages tend to occur early in documents."* On a purpose-built test set where relevant content sits beyond the first 512 tokens, many long-context models — including RankGPT — perform **at random-baseline level**.

- **Unit:** document ranking; position of evidence within the document measured in tokens.
- **Corpus:** MS MARCO documents, BEIR collections, plus a constructed late-evidence set.
- **Behind a reranker?** Yes — these are rerankers.

**Weight:** Very high, and partly adversarial to us. It is the strongest published warning that *document-level metrics on standard collections overstate performance on long documents*, and that the overstatement is a corpus artefact (evidence is front-loaded), not a model property. Any claim we make about long-chunk advantage has to survive this.

---

### `callan-1994-passage-level-evidence` — see COULD NOT VERIFY

---

### `li-2025-long-doc-survey`
**A Survey of Long-Document Retrieval in the PLM and LLM Era**
Minghan Li, Miyang Luo, Tianrui Lv, Yishuai Zhang, Siqi Zhao, Ercong Nie, Guodong Zhou.
arXiv:2509.07759, v1 9 Sep 2025, v2 25 Oct 2025. 32 pages, 6 figures.
<https://arxiv.org/abs/2509.07759> — **verified (page)**

Survey covering passage aggregation, hierarchical encoding and efficient attention for long-document retrieval; frames long documents as posing distinct *evaluation* challenges (length, dispersed evidence, complex structure) and calls for span-sensitive and structure-aware metrics.

**Weight:** Medium — useful as the one-stop "the field agrees long-document evaluation is under-served" citation and as a source of related work we may have missed.

---

## A.3 Chunk size / granularity studies (all of which meet our question at an angle)

### `qu-2025-semantic-chunking`
**Is Semantic Chunking Worth the Computational Cost?**
Renyi Qu, Ruixuan Tu, Forrest Sheng Bao.
Findings of the Association for Computational Linguistics: NAACL 2025, pp. 2155–2177. (arXiv:2410.13070, 16 Oct 2024.)
<https://aclanthology.org/2025.findings-naacl.114/> — **verified (page)**

Evaluates semantic chunking against fixed-size chunking on document retrieval, evidence retrieval and retrieval-based answer generation, and concludes *"the computational costs associated with semantic chunking are not justified by consistent performance gains."*

- **Unit:** three tasks at document, evidence and answer level.
- **Behind a reranker?** Not established from the abstract; I did not read the methodology section.
- **Budget control:** **not confirmed.** The abstract page contains no statement about holding retrieved tokens fixed. Do not cite this as budget-normalised without checking §methods.

**Weight:** Medium-high as a deflationary result on chunking sophistication; **unverified** on whether the comparison is budget-fair, which matters because the same critique could apply to it.

---

### `bhat-2025-rethinking-chunk-size`
**Rethinking Chunk Size For Long-Document Retrieval: A Multi-Dataset Analysis**
Sinchana Ramakanth Bhat, Max Rudat, Jannis Spiekermann, Nicolas Flores-Herr.
arXiv:2505.21700, 27 May 2025. No venue stated on the abs page.
<https://arxiv.org/abs/2505.21700> — **verified (page)**

Sweeps fixed chunk sizes 64/128/256/512/1024 tokens (no overlap) across short-form and long-form datasets and several embedding models. Finds 64–128 tokens optimal for concise fact-based answers and 512–1024 for broader contextual needs, and that embedding models differ in chunking sensitivity (Stella favours large chunks, Snowflake small). Explicitly calls for *"improved chunk quality measures."*

- **Unit:** retrieval effectiveness at fixed k per chunk size — i.e. **exactly the fixed-k comparison our study argues is confounded**.
- **Behind a reranker?** No — bi-encoder retrieval.

**Weight:** High as the foil. This is the shape of study we are correcting: chunk size swept, metrics at fixed k, so the larger chunk silently delivers 16× the text of the smaller one.

---

### `kreileder-2026-chunking-academic`
**Evaluating Chunking Strategies for Retrieval-Augmented Generation on Academic Texts**
Valentin J. J. Kreileder, Johannes Reisinger, Andreas Fischer. arXiv:2607.01852, 2 Jul 2026.
<https://arxiv.org/abs/2607.01852> — **verified (page)**

Compares cluster-based semantic chunking against fixed-size and recursive chunking on academic theses using the RAGAs framework. Cluster-based chunking **did not outperform** the simpler strategies. And, from the authors' own abstract: ***"RAGAs based faithfulness shows limited reliability in this setup."***

**Weight:** High for Area A.4 — this is the entry where the authors themselves report RAGAs faithfulness as unreliable in their own setup. Chunk sizes and whether the comparison was at fixed k or fixed budget are **not stated on the abs page**; check the PDF before citing on that axis.

---

## A.4 RAG evaluation frameworks and their critiques

### `es-2024-ragas`
**RAGAs: Automated Evaluation of Retrieval Augmented Generation**
Shahul Es, Jithin James, Luis Espinosa Anke, Steven Schockaert.
Proceedings of the 18th Conference of the European Chapter of the ACL: System Demonstrations (EACL 2024), pp. 150–158.
<https://aclanthology.org/2024.eacl-demo.16/> — **verified (page)**

Reference-free framework scoring faithfulness, answer relevance and context relevance without ground-truth annotations. Validated on the authors' own WikiEval.

**Baseline check:** The validation set is the authors' own small purpose-built WikiEval; the reported human agreement (~0.95 faithfulness on WikiEval) is an in-house number on an in-house set, and the entries below show it does not transfer. **Note also this is a four-page system-demonstration paper, not a full refereed evaluation paper** — the field cites it as if it were the latter.

**Weight:** Essential to cite, essential to caveat.

---

### `saad-falcon-2024-ares`
**ARES: An Automated Evaluation Framework for Retrieval-Augmented Generation Systems**
Jon Saad-Falcon, Omar Khattab, Christopher Potts, Matei Zaharia.
Proceedings of NAACL-HLT 2024 (Vol. 1: Long Papers), pp. 338–354.
<https://aclanthology.org/2024.naacl-long.20/> — **verified (page)**

Generates synthetic training data, fine-tunes lightweight LM judges for context relevance / answer faithfulness / answer relevance, and wraps them in **prediction-powered inference (PPI)** with a small human-annotated set to produce confidence intervals.

**Weight:** High, and methodologically the better-behaved of the two frameworks: PPI gives statistically valid intervals rather than a bare judge score. If we need an LLM-judge number with an interval, this is the citable construction.

---

### `roychowdhury-2024-rag-metrics-telecom`
**Evaluation of RAG Metrics for Question Answering in the Telecom Domain**
Sujoy Roychowdhury, Sumit Soman, H G Ranjani, Neeraj Gunda, Vansh Chhabra, Sai Krishna Bala.
arXiv:2407.12873, 15 Jul 2024. Comments field: accepted for publication in the ICML 2024 Workshop on Foundation Models in the Wild.
<https://arxiv.org/abs/2407.12873> — **verified (page)**

Modifies the RAGAs package to expose intermediate prompt outputs, compares against expert evaluations in a domain setting, and reports *"challenges of using it in the telecom domain"*, singling out *"the lack of details of derivation of numerical value of the evaluation metrics"* as a disadvantage.

**Weight:** Medium — a concrete domain-transfer failure of RAGAs from practitioners who instrumented it. Workshop paper; I verified the abstract page, not the full results.

---

### `muller-2025-grouse`
**GroUSE: A Benchmark to Evaluate Evaluators in Grounded Question Answering**
Sacha Muller, António Loison, Bilel Omrani, Gautier Viaud.
Proceedings of the 31st International Conference on Computational Linguistics (COLING 2025), Abu Dhabi. (arXiv:2409.06595, 10 Sep 2024; rev. 30 Jan 2025.)
<https://arxiv.org/abs/2409.06595> — **verified (page)**

A meta-evaluation benchmark of 144 unit tests for judge models in grounded QA. Headline finding: *"existing automated RAG evaluation frameworks often overlook important failure modes, even when using GPT-4 as a judge"*, and *correlation with GPT-4 is an incomplete proxy* for judge quality.

- Does **not** name RAGAs specifically in the material I verified.

**Weight:** High — the cleanest "these frameworks do not measure what they claim" citation, with a mechanism (unit-tested failure modes) rather than a correlation number.

---

### `gienapp-2025-crowdsourcing-rag`
**The Viability of Crowdsourcing for RAG Evaluation**
Lukas Gienapp, Tim Hagen, Maik Fröbe, Matthias Hagen, Benno Stein, Martin Potthast, Harrisen Scells.
SIGIR 2025 (arXiv:2504.15689, 22 Apr 2025; comments field: "Accepted at SIGIR'25").
<https://arxiv.org/abs/2504.15689> — **verified (page)**

Releases the Crowd RAG Corpus 2025 (903 human-written + 903 LLM-generated responses) and finds *"human pairwise judgments provide reliable and cost-effective results compared to LLM-based pairwise or human/LLM-based pointwise judgments, as well as automated comparisons with human-written reference responses."*

- Compares judgment *modalities*, not RAGAs/ARES specifically.

**Weight:** Medium-high — the citation for "pointwise LLM judgments are the weakest of the available options", which is the modality RAGAs uses.

---

## A.5 Position of evidence in the context, and how much of a long context is used

*(Fourteen entries in this subsection were gathered and verified by a delegated pass; each was confirmed against an arXiv `/abs/` page and/or ACL Anthology page. I re-verified `cuconasu-2024` and `liu-2024` independently. The baseline-check lines are the delegate's and are reproduced because they are the part most worth reading.)*

### `liu-2024-lost-in-middle`
**Lost in the Middle: How Language Models Use Long Contexts**
Nelson F. Liu, Kevin Lin, John Hewitt, Ashwin Paranjape, Michele Bevilacqua, Fabio Petroni, Percy Liang.
Transactions of the Association for Computational Linguistics, Vol. 12, pp. 157–173. 2024.
<https://aclanthology.org/2024.tacl-1.9/> — **verified (page)**

Multi-document QA and key-value retrieval with the position of the relevant information varied. Performance is highest when the relevant information is at the beginning or end and *"significantly degrades when models must access relevant information in the middle of long contexts, even for explicitly long-context models."*

- **Unit:** position of the gold document among *k* distractor documents in the prompt.
- **Behind a reranker?** No — position is set by construction.

**Weight:** Essential. This is why "delivered into context" is not the same as "used", and why a budget-normalised retrieval metric is still an upper bound on what the generator will exploit.

---

### `hsieh-2024-found-in-the-middle`
**Found in the Middle: Calibrating Positional Attention Bias Improves Long Context Utilization**
Cheng-Yu Hsieh, Yung-Sung Chuang, Chun-Liang Li, Zifeng Wang, Long T. Le, Abhishek Kumar, James Glass, Alexander Ratner, Chen-Yu Lee, Ranjay Krishna, Tomas Pfister.
Findings of the ACL 2024 (confirmed via the arXiv Comments field). arXiv:2406.16008.
<https://arxiv.org/abs/2406.16008> — **verified (page)**

Mechanistic follow-up: LLM attention has a U-shaped positional bias favouring context start and end independent of content relevance; a training-free attention calibration recovers up to 15 points on affected RAG-style QA.

**Baseline check:** FLAG — the "+15 points" figure's comparison condition (presumably uncalibrated attention on the same backbone) was not extracted. Name the baseline before quoting the number.

**Weight:** High — gives "context utilization" as a term of art and a mechanism for lost-in-the-middle.

---

### `an-2024-effective-context-length`
**Why Does the Effective Context Length of LLMs Fall Short?**
Chenxin An, Jun Zhang, Ming Zhong, Lei Li, Shansan Gong, Yao Luo, Jingjing Xu, Lingpeng Kong.
arXiv:2410.18745, Oct 2024. **Venue unconfirmed** — an OpenReview id exists suggesting ICLR 2025, but OpenReview was behind a bot wall and the arXiv Comments field is empty.
<https://arxiv.org/abs/2410.18745> — **verified (page)**

Argues open-source LLMs' effective context length is typically ≤ half the trained/claimed length, attributes it to a left-skewed distribution of relative positions seen in training, and proposes STRING (training-free positional remapping).

**Baseline check:** FLAG — "improves Llama3.1-70B/Qwen2-72B by >10 points, outperforming GPT-4-128K" is measured on RULER and InfiniteBench (synthetic long-context stress tests), so it is a benchmark-specific claim, not a general capability claim.

**Weight:** High — **this is the paper that owns the term "effective context length."**

---

### `hsieh-2024-ruler`
**RULER: What's the Real Context Size of Your Long-Context Language Models?**
Cheng-Ping Hsieh, Simeng Sun, Samuel Kriman, Shantanu Acharya, Dima Rekesh, Fei Jia, Yang Zhang, Boris Ginsburg.
arXiv:2404.06654, Apr 2024 (NVIDIA; no peer-reviewed venue confirmed on the abs page).
<https://arxiv.org/abs/2404.06654> — **verified (page)**

13 synthetic tasks (multi-needle NIAH, multi-hop tracing, aggregation, QA) at controlled token lengths 4K–128K+. Models that ace vanilla single-needle NIAH degrade sharply on the harder variants; only a handful held satisfactory performance at 32K despite advertising ≥32K.

**Weight:** High — the standard citation for "advertised context ≠ usable context."

---

### `modarressi-2025-nolima`
**NoLiMa: Long-Context Evaluation Beyond Literal Matching**
Ali Modarressi, Hanieh Deilamsalehy, Franck Dernoncourt, Trung Bui, Ryan A. Rossi, Seunghyun Yoon, Hinrich Schütze.
ICML 2025. arXiv:2502.05167, Feb 2025.
<https://arxiv.org/abs/2502.05167> — **verified (page)**

NIAH variant with lexical overlap between question and needle removed, forcing associative retrieval. At 32K tokens, 10 of 13 models claiming ≥128K fall below 50% of their own ~1K-token baseline; GPT-4o drops 99.3% → 69.7%.

**Baseline check:** clean — each model is compared against its own short-context accuracy, which strengthens the finding.

**Weight:** High — the sharpest quantification of context-length degradation, and the one that controls for lexical shortcutting.

---

### `levy-2024-same-task-more-tokens`
**Same Task, More Tokens: the Impact of Input Length on the Reasoning Performance of Large Language Models**
Mosh Levy, Alon Jacoby, Yoav Goldberg. ACL 2024 (Vol. 1: Long Papers). arXiv:2402.14848.
<https://aclanthology.org/2024.acl-long.818/> — **verified (page)**

Pads the *same* QA sample to different lengths, isolating length from task difficulty and content. Reasoning degrades well short of the advertised maximum across every padding variant, and next-token-prediction perplexity is negatively correlated with reasoning performance.

**Weight:** High — the controlled-confound version of the length claim; the padding design is the right template for isolating a delivery variable.

---

### `jin-2025-long-context-meets-rag`
**Long-Context LLMs Meet RAG: Overcoming Challenges for Long Inputs in RAG**
Bowen Jin, Jinsung Yoon, Jiawei Han, Sercan O. Arik. ICLR 2025 (OpenReview id `oU3tpaR8fm`; OpenReview itself was behind a bot wall, so **venue corroborated indirectly**). arXiv:2410.05983, Oct 2024.
<https://arxiv.org/abs/2410.05983> — **verified (page)**

**The canonical non-monotonic result.** As the number of retrieved passages given to a long-context LLM increases, answer quality rises then falls, attributed to accumulating **"hard negatives"**. Proposes training-free retrieval reordering plus fine-tuning fixes.

- **Unit:** number of retrieved passages, not raw token count.

**Weight:** Very high — "more retrieved context can hurt" is a precondition for any budget-frontier argument; this is the cleanest citation for it.

---

### `yu-2024-in-defense-of-rag`
**In Defense of RAG in the Era of Long-Context Language Models**
Tan Yu, Anbang Xu, Rama Akkiraju. arXiv:2409.01666, Sep 2024 (NVIDIA; **no peer-reviewed venue confirmed**).
<https://arxiv.org/abs/2409.01666> — **verified (page)**

Order-preserving RAG (OP-RAG): retrieved chunks kept in original document order. Reports an inverted-U of quality vs. chunk count with a "sweet point" that beats feeding the whole long document, using far fewer tokens.

**Baseline check:** FLAG — the long-context baseline is a model given the **whole document, uncurated**. That is a weak comparator; a fair test would be long-context *plus* reordering vs. OP-RAG.

**Weight:** Medium — cite the inverted-U, discount the "beats long-context" framing.

---

### `leng-2024-long-context-rag`
**Long Context RAG Performance of Large Language Models**
Quinn Leng, Jacob Portes, Sam Havens, Matei Zaharia, Michael Carbin.
NeurIPS 2024 Workshop on Adaptive Foundation Models. arXiv:2411.03538, Nov 2024.
<https://arxiv.org/abs/2411.03538> — **verified (page)**

20 LLMs, RAG pipelines with **total context varied 2K–128K tokens** (retrieved-document count varying to fill the budget), 3 domain QA datasets. Only a handful of the newest models hold accuracy above 64K; most degrade well before their advertised maximum (~32K for Llama-3.1-405B). Catalogues distinct qualitative failure modes at long context rather than smooth degradation.

- **Unit:** total prompt token budget — this is a genuine budget sweep.

**Weight:** High — methodologically the closest large-scale sweep to ours on the generator side.

---

### `cuconasu-2024-power-of-noise`
**The Power of Noise: Redefining Retrieval for RAG Systems**
Florin Cuconasu, Giovanni Trappolini, Federico Siciliano, Simone Filice, Cesare Campagnano, Yoelle Maarek, Nicola Tonellotto, Fabrizio Silvestri.
SIGIR 2024. arXiv:2401.14887, v1 26 Jan 2024, v4 1 May 2024. (ACM DL page returned 403; venue confirmed from the arXiv abs page's linked ACM DOI.)
<https://arxiv.org/abs/2401.14887> — **verified (page)**

Systematic study of document count, position and relevance *type* in the prompt. Distinguishes **gold**, **distracting** (topically related, not answer-bearing) and **random** (unrelated) documents.

**Baseline check:** FLAG, and this matters. The famous "+35% from adding random documents" is **not** "noise beats no noise" and **not** "noise beats gold context." It is *random/unrelated documents beat topically-related distractors* in certain count/position settings. The popular reading ("more noise helps") oversimplifies the actual finding, which is about distractor **type**. Cite precisely or not at all.

- **Unit:** number/type/position of passages. No cross-encoder in the loop — placement is by construction.

**Weight:** High, with the caveat above. The distractor-vs-random distinction is doing real work and should not be collapsed.

---

### `xu-2024-retrieval-meets-long-context`
**Retrieval meets Long Context Large Language Models**
Peng Xu, Wei Ping, Xianchao Wu, Lawrence McAfee, Chen Zhu, Zihan Liu, Sandeep Subramanian, Evelina Bakhturina, Mohammad Shoeybi, Bryan Catanzaro. ICLR 2024. arXiv:2310.03025.
<https://arxiv.org/abs/2310.03025> — **verified (page)**

Retrieval-augmentation vs. context-window extension on 9 long-context tasks. A 4K-context model with simple retrieval augmentation ≈ a finetuned 16K-context model without retrieval; best is both together.

**Baseline check:** FLAG (mild) — the headline comparison is NVIDIA's retrieval-augmented Llama2-70B against GPT-3.5-turbo-16K, a smaller/older proprietary model. Real but not scale-matched.

**Weight:** Medium-high — the standard "retrieval is a budget-efficient substitute for context length" citation.

---

### `jiang-2024-longrag`
**LongRAG: Enhancing Retrieval-Augmented Generation with Long-context LLMs**
Ziyan Jiang, Xueguang Ma, Wenhu Chen. arXiv:2406.15319, Jun 2024. Comments field: "Technical Report" — **no peer-reviewed venue**.
<https://arxiv.org/abs/2406.15319> — **verified (page)**

Retrieval units are 4K-token groupings of related Wikipedia documents (≈30× longer than 100-word DPR passages), cutting corpus units 22M → 600K. 62.7% EM on NQ, 64.3% on HotpotQA full-wiki, no fine-tuning.

**Baseline check:** FLAG — the "on par with state of the art" framing is relative to fine-tuned systems of the period; the specific SOTA numbers were not independently re-verified. Check the table before repeating "on par."

**Weight:** Medium — directly relevant as the "very large retrieval unit" end of our design space.

---

### `bai-2025-longbench-v2`
**LongBench v2: Towards Deeper Understanding and Reasoning on Realistic Long-context Multitasks**
Yushi Bai, Shangqing Tu, Jiajie Zhang, Hao Peng, Xiaozhi Wang, Xin Lv, Shulin Cao, Jiazheng Xu, Lei Hou, Yuxiao Dong, Jie Tang, Juanzi Li. ACL 2025. arXiv:2412.15204.
<https://aclanthology.org/2025.acl-long.183/> — **verified (page)**

503 multiple-choice questions over real documents spanning **8K–2M words**, 6 task categories. Best direct-answer model 50.1%; human experts 53.7%; a reasoning model with extended inference 57.7%.

**Baseline check:** FLAG — the "model beats humans" framing rests on a human baseline collected under a **15-minute time limit**, which is plausibly a weakened comparator. Do not cite the human comparison.

**Weight:** Medium — useful for realistic document-length ranges.

---

### `yen-2025-helmet`
**HELMET: How to Evaluate Long-Context Language Models Effectively and Thoroughly**
Howard Yen, Tianyu Gao, Minmin Hou, Ke Ding, Daniel Fleischer, Peter Izsak, Moshe Wasserblat, Danqi Chen. ICLR 2025 (confirmed via arXiv Comments field). arXiv:2410.02694.
<https://arxiv.org/abs/2410.02694> — **verified (page)**

7 application-centric task categories up to 128K tokens with model-based metrics. Key methodological finding: **synthetic NIAH scores do not reliably predict downstream performance**, and the 7 categories correlate weakly with each other — there is no single scalar "long-context ability."

**Weight:** High — the citation for "do not use a needle test as a proxy for your application."

---

### `zhang-2024-infinitebench`
**∞Bench: Extending Long Context Evaluation Beyond 100K Tokens**
Xinrong Zhang, Yingfa Chen, Shengding Hu, Zihang Xu, Junhao Chen, Moo Khai Hao, Xu Han, Zhen Leng Thai, Shuo Wang, Zhiyuan Liu, Maosong Sun. ACL 2024 (Long Papers). arXiv:2402.13718.
<https://aclanthology.org/2024.acl-long.814/> — **verified (page)**

First benchmark with average example length >100K tokens; 12 tasks explicitly designed so simple passage retrieval is insufficient.

**Weight:** Medium.

---

## A.6 Reach vs. containment — the decomposition

### `kobeissi-2026-decomposing-retrieval-failures`
**Decomposing Retrieval Failures in RAG for Long-Document Financial Question Answering**
Amine Kobeissi, Philippe Langlais. arXiv:2602.17981, 20 Feb 2026. No venue stated.
<https://arxiv.org/abs/2602.17981> — **read** (HTML full text)

**This is the closest published match to our reach/containment decomposition, and it does not use those words.** It studies *"a frequent failure mode in which the correct document is retrieved but the page or chunk that contains the answer is missed"*, and names it **"within-document retrieval failure"**. It evaluates retrieval at three granularities — document, page, chunk — with:

- **document recall / "document discovery"** (with one gold filing per question, *"document recall at k is equivalent to hit"*) — this is our *reach*;
- **page recall at k** and chunk-level proxies (max ROUGE-L / BLEU between any retrieved chunk and gold evidence, reported as `CtxROUGE-L@k`, plus chunk-level P/R/F1) — this is our *containment*;
- an **oracle analysis** that separates the two: an *oracle document* setting restricting candidates to the correct filing quantifies *"headroom due to imperfect page and chunk discovery"*; an *oracle page* setting quantifies *"headroom due to imperfect chunk retrieval."*

Finding: across methods, gains in document discovery tend to translate into stronger page recall, but oracle performance shows substantial remaining headroom at page and chunk level. They propose a domain-fine-tuned **page scorer** treating the page as an intermediate retrieval unit.

- **Unit:** document / page / chunk — three-level, not token-budget.
- **Corpus:** 150-question subset of FinanceBench; US SEC filings (long regulatory documents; exact length statistics not given).
- **Behind a reranker?** **Yes** — cross-encoder reranking with `BAAI/bge-reranker-v2-m3` is among the compared strategies. This is the one paper in the set that makes the decomposition *behind* a reranker.

**Weight:** Very high. If we want an existing name, **"within-document retrieval failure"** and the **oracle-document / oracle-page headroom decomposition** are it. Preprint, unrefereed, and very recent — cite as prior art for the *concept*, and be explicit that our version is budget-normalised, which theirs is not.

---

### Related, weaker forms of the same split

`chen-2024-dense-x` (above) supplies the retrieval-side half under a different name: *"recall of the gold answer within the initial l retrieved words"* is containment measured against a budget. `li-2020-parade` supplies the corpus-side condition (dispersed vs. concentrated relevance) that determines whether containment is the binding constraint. `boytsov-2025-positional-bias` supplies the warning that on standard collections containment is *artificially easy* because evidence is front-loaded.

---

# Area B — statistical and methodological practice in IR/NLP evaluation

## B.1 The founding guideline, and the reply to it

### `fuhr-2017-common-mistakes`
**Some Common Mistakes In IR Evaluation, And How They Can Be Avoided**
Norbert Fuhr. ACM SIGIR Forum, Vol. 51, No. 3, December 2017, pp. 32–41. DOI 10.1145/3190580.3190586.
<http://sigir.org/wp-content/uploads/2018/01/p032.pdf> — **read** (full text)

Ten "Thou shalt not"s. The four that bear on us, quoted from the text:

- **§2.6 "Thou shalt not formulate hypotheses after the experiment."** *"The most crucial point here is that the complete set of hypotheses to be tested must be known before the experiment, as this has consequences for the test setup… it would be incorrect if an author first determines which of the competing methods performs best on his data set, and then compares his own approach only to this one."* — **this is the pre-registration argument, made in IR, in 2017, without using the word.**
- **§2.7 "Thou shalt not test multiple hypotheses without correction."** Gives the 20-symptoms drug example; recommends Bonferroni as a simple conservative default and Tukey's HSD as the right post-hoc test for all-pairs comparisons; adds the sharper point that **collection reuse is itself sequential multiple testing**: *"As a minimum requirement for reuse… one should consider the set of tests already published for this collection, add the number of own tests, and then apply a correction for the total number of tests."*
- **§2.8 "Thou shalt not ignore effect sizes."** *"Even when a (properly exercised) significance test rejects the null hypothesis, we can only infer that it is unlikely that the methods compared yield exactly the same result."*
- **§2.10 "Thou shalt not claim proof by experimentation."** *"Proofs are about universally valid statements, while experiments only demonstrate the validity for a single or a few data sets."*

**Weight:** Essential. Cite §2.6 for pre-registration and §2.7 for multiple comparisons. Note it does **not** state the "no difference ≠ equivalence" rule — see `webber-2008` for that.

---

### `sakai-2020-on-fuhrs-guideline`
**On Fuhr's Guideline for IR Evaluation** (Opinion Paper)
Tetsuya Sakai. ACM SIGIR Forum, Vol. 54, No. 1, June 2020, pp. 1–8. DOI 10.1145/3451964.3451976.
<http://www.sigir.org/wp-content/uploads/2020/06/p14.pdf> — **read** (full text)

Sakai's rebuttal, written because ECIR 2019/2020 CFPs began citing Fuhr's opinion piece as a guideline. He disagrees at length on MRR/ERR (§2.1), MAP (§2.2) and relative improvements (§2.4), and closes: *"Researchers should be aware of different views; conference programme chairs and journal editors should be very careful when providing a guideline for evaluation practices. Let's agree to disagree."*

**But on the two rules we follow, he concurs without qualification:**
- §2.6 *"Thou Shalt Not Formulate Hypotheses after the Experiment?"* → ***"Agreed."***
- §2.7 *"Thou Shalt Not Test Multiple Hypotheses without Correction?"* → ***"Agreed. Note that I used a randomised Tukey HSD test in Section 2.2."***

**Weight:** Very high, and unusually useful: the field's most prominent critic of the field's most prominent evaluation guideline agrees, in print, with exactly the two rules our design rests on. This is the strongest available "this is not controversial" citation for pre-specified hypotheses plus multiplicity correction in IR.

---

## B.2 Statistical power in IR — including the best statement of the rule we follow

### `webber-2008-statistical-power`
**Statistical Power in Retrieval Experimentation**
William Webber, Alistair Moffat, Justin Zobel.
Proceedings of the 17th ACM Conference on Information and Knowledge Management (CIKM 2008), pp. 571–580.
<https://people.eng.unimelb.edu.au/jzobel/fulltext/cikm08.pdf> — **read** (full text)

**The best citable statement, in an IR context, of "failure to find a difference is not evidence of equivalence."** From the introduction, quoted verbatim:

> *"However, if the experiment fails to find significance, then one cannot simply conclude that no consequential difference exists. Instead, the experimenter wishes to know how large an actual difference in performance could have been missed."*

That second sentence **is** the power floor. It is a direct warrant for reporting, beside every null, the smallest effect the experiment could have detected.

Substantively the paper shows: (a) estimating between-system score-delta variability from prior experience or from trial experiments leaves wide margins of error; (b) iteratively adding topics until power is reached **biases the experiment toward finding both power and significance** — a sequential-testing hazard directly analogous to optional stopping; (c) a hybrid methodology with explicit reporting requirements is proposed; (d) for fixed assessment effort, **many topics judged shallowly yields greater power than few topics judged deeply**.

**Weight:** Essential. This is the anchor citation for the power-floor practice, for the "do not grow the sample until it is significant" hazard, and for the IR-context version of Altman & Bland.

---

### `sakai-2016-topic-set-size-design`
**Topic set size design**
Tetsuya Sakai. Information Retrieval Journal, Vol. 19, No. 3, pp. 256–283. Springer, 2016. DOI 10.1007/s10791-015-9273-z.
<https://waseda.elsevierpure.com/en/publications/topic-set-size-design> — **verified (record)** (Springer bounces to an IdP; metadata from the author's institutional repository record with DOI)

Principled determination of how many topics a test collection needs, from explicit statistical requirements, via Nagata's three sample-size design techniques (paired t-test, one-way ANOVA, confidence intervals), using topic-by-run score matrices from past collections to estimate **within-system** population variance (correcting Sakai's own earlier use of total variance). Key result: *"different evaluation measures can have vastly different within-system variances, [so] they require substantially different topic set sizes under the same set of statistical requirements."*

**Weight:** High — the citation for "how many topics do you need", and the one that makes the point that the answer depends on which measure you are powering for. Our power floors are metric-specific for exactly this reason.

---

### `sakai-2016-systematic-review`
**Statistical significance, power, and sample sizes: A systematic review of SIGIR and TOIS, 2006–2015**
Tetsuya Sakai. SIGIR 2016 — Proceedings of the 39th International ACM SIGIR Conference, pp. 5–14. DOI 10.1145/2911451.2911492.
<https://waseda.elsevierpure.com/en/publications/statistical-significance-power-and-sample-sizes-a-systematic-revi/> — **verified (record)** (ACM DL 403; metadata from the author's institutional repository record with DOI)

Reviews 840 SIGIR full papers and 215 TOIS papers over a decade. Finds *"many IR effectiveness papers either lack significance testing or fail to report p-values and/or test statistics"*, which itself blocks retrospective power analysis; identifies both **underpowered** and **overpowered** experiments; releases R scripts and raw data.

**Weight:** High — the empirical evidence that reporting power is *not* standard practice in IR, which is the gap our paper's practice fills.

---

### `voorhees-2002-topic-set-size`
**The effect of topic set size on retrieval experiment error**
Ellen M. Voorhees, Chris Buckley.
Proceedings of the 25th Annual International ACM SIGIR Conference (SIGIR '02), pp. 316–323. DOI 10.1145/564376.564432.
— **verified (record)** (ACM DL 403; OpenAlex record with DOI, authors and venue)

The empirical precursor: relates topic set size to the error rate of concluding that one system beats another, giving the error-rate-vs-topic-count curves that later power work builds on.

**Weight:** High — the standard citation for "50 topics is not enough for small differences."

---

### `card-2020-little-power`
**With Little Power Comes Great Responsibility**
Dallas Card, Peter Henderson, Urvashi Khandelwal, Robin Jia, Kyle Mahowald, Dan Jurafsky.
Proceedings of EMNLP 2020, pp. 9263–9274.
<https://aclanthology.org/2020.emnlp-main.745/> — **verified (page)**

*"Despite its importance to experimental design, statistical power… has largely been ignored by the NLP community."* Meta-analysis finds underpowered experiments widespread: several GLUE tasks have inadequate power because of small test sets; typical human-rating designs cannot detect small model differences; MT test sets of 2000 sentences give ≈75% power to detect a 1 BLEU difference. Ships computational notebooks for power analysis.

**Weight:** Essential — the NLP twin of Sakai's systematic review, and the paper to cite for "power analysis is the fix, and here is the tooling."

---

## B.3 Multiple comparisons and Type I error in IR

### `carterette-2012-multiple-testing`
**Multiple testing in statistical analysis of systems-based information retrieval experiments**
Ben Carterette. ACM Transactions on Information Systems, Vol. 30, No. 1, Article 4, pp. 4:1–4:34. 2012. DOI 10.1145/2094072.2094076.
— **verified (record)** (ACM DL 403 and the author's PDF host refused connections; OpenAlex record with DOI, author, journal, year)

The extended IR-specific treatment of the multiple comparisons problem; Fuhr cites it as *"an extensive treatment of this problem from an IR point of view."* Covers family-wise error rate vs. FDR, ANOVA-based approaches, and the consequences for evaluation-campaign all-pairs comparison.

**Weight:** Essential for the multiplicity rule. **Flagged:** confirm pagination from a publisher copy before camera-ready.

---

### `carterette-2015-best-published-result` — see COULD NOT VERIFY

---

### `urbano-2019-type-errors`
**Statistical Significance Testing in Information Retrieval: An Empirical Analysis of Type I, Type II and Type III Errors**
Julián Urbano, Harlley Lima, Alan Hanjalic. SIGIR 2019. arXiv:1905.11096.
<https://arxiv.org/abs/1905.11096> — **verified (page)**

Simulation from TREC data giving actual control over the null, analysing **over 500 million p-values** across tests, systems, measures, topic set sizes and effect sizes — the largest empirical comparison of t-test vs. bootstrap vs. permutation vs. Wilcoxon under realistic IR conditions, with Type I, **Type II** and Type III error rates.

**Weight:** High — this is where a Type II / power claim about a specific IR test should be sourced, and the only place where actual Type II rates (not assumed ones) are available.

---

### `ferro-2024-uncontextualized-significance`
**Uncontextualized significance considered dangerous**
Nicola Ferro, Mark Sanderson.
Proceedings of the 47th International ACM SIGIR Conference (SIGIR '24), Washington DC, 10 pages. DOI 10.1145/3626772.3657827.
<https://www.dei.unipd.it/~ferro/papers/2024/SIGIR2024-FS.pdf> — **read** (author PDF; ACM DL 403)

From the abstract, quoted: *"we show that ignoring the context of a test risks Type I errors, leading to potential publication bias. We examine two contexts: multiple testing and the types of the retrieval systems being compared. Our results show that multiple testing corrections are critical for experimental work. In addition, we find that past research on the reliability of test collections maybe flawed owing to the type of systems examined… Together our results suggest substantial numbers of Type I errors in offline IR experiments."*

**Weight:** Very high — the most recent, most direct empirical demonstration that multiple-testing correction is not optional in IR, from the authors of the topic-splitting reliability line itself. Pairs with Fuhr §2.7 and Sakai's "Agreed."

---

### `zobel-2022-when-measurement-misleads`
**When Measurement Misleads: The Limits of Batch Assessment of Retrieval Systems** (Opinion Paper)
Justin Zobel. ACM SIGIR Forum, Vol. 56, No. 1, June 2022, 20 pp.
<https://sigir.org/wp-content/uploads/2022/07/p12.pdf> — **read** (full text)

Argues that *"measured scores are inherently incomplete as a representation of human activity"* — an innate gap between measured scores and human satisfaction that batch experiments **cannot** close — and imports Goodhart's law and the Lucas critique to argue that *"blind pursuit of performance gains based on optimisation of scores, and analysis based solely on aggregated measurements, can lead to misleading and unreliable outcomes."*

**Weight:** High — the right citation for the limits of *any* offline budget metric, including ours. Use it in the threats-to-validity section rather than pretending it does not apply.

---

## B.4 Pre-registration in NLP / ML — proposals and critiques

### `van-miltenburg-2021-preregistering`
**Preregistering NLP research**
Emiel van Miltenburg, Chris van der Lee, Emiel Krahmer. NAACL-HLT 2021, pp. 613–623. (Best Thematic Paper.)
<https://aclanthology.org/2021.naacl-main.51/> — **verified (page)**

*"Preregistration refers to the practice of specifying what you are going to do, and what you expect to find in your study, before carrying out the study. This practice is increasingly common in medicine and psychology, but is rarely discussed in NLP."* Proposes preregistration questions for different study types and argues for **registered reports**.

**Weight:** Essential — the primary proposal citation for NLP pre-registration.

---

### `sogaard-2023-two-sided-preregistration`
**A Two-Sided Discussion of Preregistration of NLP Research**
Anders Søgaard, Daniel Hershcovich, Miryam de Lhoneux. EACL 2023. (arXiv:2302.10086.)
<https://aclanthology.org/2023.eacl-main.6/> — **verified (page)**

Structured as a dialogue responding directly to van Miltenburg et al., laying out seven concerns: hypotheses retrofitted once results are known; pre-registration biasing the field toward confirmatory work; the need to permit re-classifying a study as exploratory; risk of **increased** publication bias; flag-planting; p-hacking persisting under pre-registration; reduced risk tolerance.

**Weight:** Very high — this is the critique we must engage rather than ignore, and the source of the "let a study be re-classified as exploratory" requirement. Our three-outcome scheme (resolved / unresolved / gate-not-evaluable) is a partial answer to it; say so.

---

### `bertinetto-2021-preregistration-workshop`
**Proceedings of the NeurIPS 2020 Workshop on Pre-registration in Machine Learning** (volume preface)
Luca Bertinetto, João F. Henriques, Samuel Albanie, Michela Paganini, Gül Varol (editors).
PMLR Volume 148 (workshop 11 Dec 2020; proceedings published 8 Jul 2021).
<https://proceedings.mlr.press/v148/> — **verified (page)**

Confirms the two-phase review model: a proposal is reviewed before experiments are run, and the final paper is accepted regardless of outcome provided the registered protocol was followed. A second edition exists as **PMLR Volume 181** (NeurIPS 2021), edited by Albanie, Henriques, Bertinetto, Hernández-García, Doughty, Varol — useful evidence that the format persisted.

**Weight:** High — the ML-side existence proof that pre-registration has been run at a top venue, twice.

---

### `forde-2019-scientific-method`
**The Scientific Method in the Science of Machine Learning**
Jessica Zosa Forde, Michela Paganini. arXiv:1904.10922 (also ICLR 2019 "Debugging Machine Learning Models" workshop).
<https://arxiv.org/abs/1904.10922> — **verified (page)**

Argues deep-learning research is missing hypothesis formulation/testing and uncertainty estimation. Pre-registration-adjacent rather than a pre-registration proposal.

**Weight:** Medium.

---

### `bouthillier-2019-unreproducible`
**Unreproducible Research is Reproducible**
Xavier Bouthillier, César Laurent, Pascal Vincent. ICML 2019, PMLR Vol. 97, pp. 725–734.
<https://proceedings.mlr.press/v97/bouthillier19a.html> — **verified (page)**

Distinguishes reproducibility of *method* from reproducibility of *finding*; shows a single-seed single-dataset comparison used to declare a winner frequently flips under different seeds.

**Weight:** Medium-high.

---

### `bouthillier-2021-accounting-variance`
**Accounting for Variance in Machine Learning Benchmarks**
Xavier Bouthillier, Pierre Delaunay, Mirko Bronzi, Assya Trofimov, Brennan Nichyporuk, Justin Szeto, Naz Sepah, Edward Raff, Kanika Madan, Vikram Voleti, Samira Ebrahimi Kahou, Vincent Michalski, Dmitriy Serdyuk, Tal Arbel, Chris Pal, Gaël Varoquaux, Pascal Vincent.
arXiv:2103.03098. Comments field says "Submitted to MLSys2021" — **final MLSys publication not confirmed** from the arXiv page.
<https://arxiv.org/abs/2103.03098> — **verified (page)**

Models sources of variance (initialisation, data sampling, hyperparameter search) in benchmark comparisons; randomising more sources gives a better estimator per unit of compute (reported as a 51× reduction in compute to reach comparable estimator quality); recommends randomising all feasible sources and reporting variance, not point estimates.

**Weight:** Medium-high — power-analysis-adjacent, and the best ML-side argument for budgeting compute toward variance reduction rather than more seeds of one configuration.

---

### `gorman-2019-standard-splits`
**We Need to Talk about Standard Splits**
Kyle Gorman, Steven Bedrick. ACL 2019, pp. 2786–2791. (Outstanding Paper.)
<https://aclanthology.org/P19-1267/> — **verified (page)**

Replicates 9 published POS taggers (2000–2018); system rankings are not stable across newly generated random splits. Recommends reporting over multiple random splits plus significance tests.

### `sogaard-2021-random-splits`
**We Need To Talk About Random Splits**
Anders Søgaard, Sebastian Ebert, Jasmijn Bastings, Katja Filippova. EACL 2021, pp. 1823–1832.
<https://aclanthology.org/2021.eacl-main.156/> — **verified (page)**

The direct rebuttal: random splits *also* give overly optimistic generalisation estimates; worst-case/biased splits under-estimate but sit closer to true generalisation error. Recommends multiple independent test sets, or multiple biased splits as fallback.

**Weight (both):** Medium-high, and cite them as a *pair* — the second undercuts the obvious fix implied by the first.

---

### `bowman-2021-fix-benchmarking`
**What Will it Take to Fix Benchmarking in Natural Language Understanding?**
Samuel R. Bowman, George E. Dahl. NAACL-HLT 2021, pp. 4843–4855. (arXiv:2104.02145.)
<https://aclanthology.org/2021.naacl-main.385/> — **verified (page)**

Four criteria a NLU benchmark must meet, one of which is **sufficient statistical power / headroom** to distinguish real improvements from noise.

**Weight:** High — the only verified paper in this set that names statistical power as a *design requirement for a benchmark*, which is the closest thing in NLP to our power-floor discipline applied prospectively.

---

### `dror-2018-hitchhikers-guide`
**The Hitchhiker's Guide to Testing Statistical Significance in Natural Language Processing**
Rotem Dror, Gili Baumer, Segev Shlomov, Roi Reichart. ACL 2018 (Vol. 1: Long Papers), pp. 1383–1392.
<https://aclanthology.org/P18-1128/> — **verified (page)**

Surveys ACL/TACL 2017 and finds significance testing often ignored or misused; maps test choice to task/metric type.

### `dror-2017-replicability-analysis`
**Replicability Analysis for Natural Language Processing: Testing Significance with Multiple Datasets**
Rotem Dror, Gili Baumer, Marina Bogomolov, Roi Reichart. Transactions of the ACL, Vol. 5, 2017.
<https://aclanthology.org/Q17-1033/> — **verified (page)**

Formal framework for multiple-comparisons-corrected significance testing **across multiple datasets**, so "A beats B" generalises beyond one dataset. Validated on 4 NLP applications.

**Weight (dror-2017):** High — this is the NLP-side citation for our across-corpus multiplicity correction, and it is the right shape (multiplicity across datasets, not across metrics).

---

### `ulmer-2022-deep-significance`
**deep-significance: Easy and Meaningful Statistical Significance Testing in the Age of Neural Networks**
Dennis Ulmer, Christian Hardmeier, Jes Frellsen. ML Evaluation Standards Workshop at ICLR 2022.
<https://backend.orbit.dtu.dk/ws/files/295252493/deep_significance_paper.pdf> — **read** (full PDF)

> *"Nevertheless, statistical significance testing (SST) is still not widely used. This endangers true progress, as seeming improvements over a baseline might be statistical flukes, leading follow-up research astray while wasting human and computational resources."*

Ships tests suited to few, expensive runs.

### `ulmer-2022-experimental-standards`
**Experimental Standards for Deep Learning in Natural Language Processing Research**
Dennis Ulmer, Elisa Bassignana, Max Müller-Eberstein, Daniel Varab, Mike Zhang, Rob van der Goot, Christian Hardmeier, Barbara Plank. arXiv:2204.06251. Widely listed as Findings of EMNLP 2022 — **that venue is unconfirmed here**; the arXiv record is confirmed.
<https://arxiv.org/abs/2204.06251> — **verified (page)**

Distils community best-practice discussion into one applicable methodology (which test, how many seeds, what to report), maintained as a living public repository.

**Weight (both):** Medium.

---

## B.5 Reproducibility tracks in IR

### `ferro-2019-centre-clef`
**CENTRE@CLEF2019: Overview of the Replicability and Reproducibility Tasks**
Nicola Ferro, Norbert Fuhr, Maria Maistro, Tetsuya Sakai, Ian Soboroff.
CLEF 2019, CEUR Workshop Proceedings Vol-2380, paper_258.
<https://ceur-ws.org/Vol-2380/paper_258.pdf> — **read**

Second edition of the CENTRE lab. Reports that even now *"best TREC systems still outperform off-the-shelf open source systems"*, partly through lack of tuning and missing advanced components, and that **additivity is a real problem** — gains do not stack the same way on a weak baseline as on a strong one.

### `sakai-2019-ntcir14-centre`
**Overview of the NTCIR-14 CENTRE Task**
Tetsuya Sakai, Nicola Ferro, Ian Soboroff, Zhaohao Zeng, Peng Xiao, Maria Maistro.
Proceedings of the 14th NTCIR Conference, Tokyo, June 2019.
<https://research.nii.ac.jp/ntcir/workshop/OnlineProceedings14/pdf/ntcir/01-NTCIR14-OV-CENTRE-SakaiT.pdf> — **read**

*"The first-ever metatask that operates across the three major IR evaluation venues: CLEF, NTCIR, and TREC."* Three subtasks (T1 replicability, T2TREC / T2OPEN reproducibility). Establishes **Effect Ratio** as the comparison metric. Striking sociological finding: only **one team** (MPII) participated across all three CENTRE editions combined.

### `clancy-2019-osirrc`
**The SIGIR 2019 Open-Source IR Replicability Challenge (OSIRRC 2019)**
Ryan Clancy, Nicola Ferro, Claudia Hauff, Jimmy Lin, Tetsuya Sakai, Ze Zhong Wu. SIGIR 2019.
<https://www.dei.unipd.it/~ferro/papers/2019/SIGIR2019-OSIRRC.pdf> — **read** (author PDF; ACM DL 403)

Plus the extended workshop overview, **Overview of the 2019 Open-Source IR Replicability Challenge (OSIRRC 2019)**, same authors, CEUR Vol-2409, invited01 — <https://ceur-ws.org/Vol-2409/invited01.pdf> — **read**. Defines repeatability / replicability / reproducibility per ACM badging; the deliverable is "the jig", a common Docker interface spec. **13 teams submitted 17 images**, mostly targeting TREC 2004 Robust. Explicitly motivated by Armstrong et al. (2009) and Yang et al. (2019) on weak baselines.

### `breuer-2020-measure-reproducibility`
**How to Measure the Reproducibility of System-oriented IR Experiments**
Timo Breuer, Nicola Ferro, Norbert Fuhr, Maria Maistro, Tetsuya Sakai, Philipp Schaer, Ian Soboroff.
SIGIR 2020, pp. 349–358. arXiv:2010.13447 (venue field: "SIGIR 2020 Full Conference Paper"). DOI 10.1145/3397271.3401036.
<https://arxiv.org/abs/2010.13447> — **verified (page)**

*"We do not have any means to assess when reproduced is reproduced."* Proposes measures comparing reproduced vs. original results at three granularities — ranked-list level, effectiveness-score level, and significant-difference/effect level — rather than a single pass/fail.

**Weight (this cluster):** Medium-high. The three-granularity reproducibility framing is a useful structural precedent for our three-outcome decision scheme, and CENTRE's participation numbers are honest evidence about how much of this the field actually does.

---

## B.6 Weak baselines and the additivity problem

### `armstrong-2009-improvements-dont-add-up`
**Improvements that don't add up: ad-hoc retrieval results since 1998**
Timothy G. Armstrong, Alistair Moffat, William Webber, Justin Zobel. CIKM 2009, Hong Kong.
<https://people.eng.unimelb.edu.au/ammoffat/abstracts/amwz09cikm.html> — **verified (page)** (author's abstract page; ACM DL and IR Anthology blocked)

Examines TREC ad-hoc results 1998–2008 and finds little evidence of improvement over the decade despite dozens of individually-published wins, because comparisons are to weak in-paper baselines rather than the best known systems.

### `yang-2019-neural-hype`
**Critically Examining the "Neural Hype": Weak Baselines and the Additivity of Effectiveness Gains from Neural Ranking Models**
Wei Yang, Kuang Lu, Peilin Yang, Jimmy Lin. SIGIR 2019. arXiv:1904.09171.
<https://arxiv.org/abs/1904.09171> — **verified (page)**

Meta-analysis of Robust04 finds no upward trend; but the authors' own five neural rerankers *do* give additive gains when stacked on strong, well-tuned baselines. Conclusion: neural gains are real **only** on top of strong baselines, and are illusory on top of weak ones.

### `lin-2018-neural-hype` and `lin-2019-recantation`
**The Neural Hype and Comparisons Against Weak Baselines** — Jimmy Lin, SIGIR Forum Vol. 52 No. 2, December 2018, pp. 40–51. <http://sigir.org/wp-content/uploads/2019/01/p040.pdf> — **read**
> *"I am disappointed that it is not difficult to find neural ranking papers that demonstrate winning by showing statistically significant improvements over weak or inadequately-tuned baselines."*

**The Neural Hype, Justified! A Recantation** — Jimmy Lin, SIGIR Forum Vol. 53 No. 2, December 2019, p. 88ff. <https://www.sigir.org/wp-content/uploads/2019/december/p088.pdf> — **read**
Lin's own reversal one year later: with pretrained transformer rerankers, effectiveness *has* substantially improved even without vast training data.

### `rendle-2019-difficulty-evaluating-baselines`
**On the Difficulty of Evaluating Baselines: A Study on Recommender Systems**
Steffen Rendle, Li Zhang, Yehuda Koren. arXiv:1905.01395. RecSys 2019 Best Paper **per Lin (2019)'s citation**; the arXiv page carries no journal-ref, so that venue is confirmed only secondarily.
<https://arxiv.org/abs/1905.01395> — **verified (page)**

A carefully tuned vanilla matrix-factorisation baseline beats five years of reported "state of the art" on MovieLens 10M.

**Weight (this cluster):** High, and directly relevant to our own baseline-check discipline. Cite `lin-2018` **with** `lin-2019` — using the 2018 piece alone misrepresents its author's current position.

---

## B.7 Non-inferiority and equivalence testing

**There is no prior art for non-inferiority testing in IR or NLP evaluation that I could find.** See §4 below for the searches. The citable sources are therefore from outside the field:

### `lakens-2017-equivalence-tests`
**Equivalence Tests: A Practical Primer for t Tests, Correlations, and Meta-Analyses**
Daniël Lakens. Social Psychological and Personality Science, Vol. 8, No. 4, pp. 355–362. 2017. DOI 10.1177/1948550617697177.
— **verified (record)** (OpenAlex record with DOI, author, journal, volume, year; the SAGE page was not fetched)

The standard methodological primer: researchers *"often incorrectly conclude an effect is absent based on a nonsignificant result"*; the frequentist remedy is equivalence testing via **TOST (two one-sided tests)**, with upper and lower equivalence bounds set from the **smallest effect size of interest**. Non-inferiority is the one-sided special case.

Companion, also verified via OpenAlex record: **Equivalence Testing for Psychological Research: A Tutorial**, Daniël Lakens, Anne M. Scheel, Peder M. Isager, *Advances in Methods and Practices in Psychological Science*, 2018, DOI 10.1177/2515245918770963 — the more operational of the two.

**Weight:** Essential — this is where our pre-registered margin and its one-sided test come from. The "smallest effect size of interest" framing is also the honest name for how a margin should be justified.

---

### `altman-1995-absence-of-evidence`
**Statistics notes: Absence of evidence is not evidence of absence**
Douglas G. Altman, J. Martin Bland. BMJ, Vol. 311, p. 485. 19 August 1995.
— **verified (record)** (OpenAlex record with authors, journal, year; PubMed 7647644)

Trials showing no significant difference are routinely called "negative", wrongly implying no difference was shown, when *"usually all that has been shown is an absence of evidence of a difference"*; the authors tie this directly to inadequate sample size and consequent lack of power.

**Weight:** Essential — the origin statement of the rule, and the one everyone recognises. Pair it with `webber-2008` for the IR-context version.

---

# 1. Established names for what we invented

**This is the headline answer: yes for budget-normalised evaluation; partly, under a different name, for reach/containment.**

## 1a. Budget-normalised retrieval evaluation — **an established notation exists. Adopt it.**

`chen-2024-dense-x` (Dense X Retrieval, EMNLP 2024) already does what we do and already names it:

| Their term | Definition | Our equivalent |
|---|---|---|
| **`EM @ l tokens`** | exact-match answer accuracy when *"the maximum number of retrieved tokens is capped at l"* and only the top *l* tokens are fed to the LM | downstream metric under budget B |
| **"fixed word retrieval budget"** | recall of the gold answer within the initial *l* retrieved words | retrieval-side budget-normalised recall |
| **"the same computation budget"** | the fairness condition under which granularities may be compared | budget parity |
| **"higher density of question-related information in the prompts"** | their mechanism | information density per delivered token |

`lu-2025-hichunk` states the fairness rule in its own words — *"to ensure fair comparison, we fix the retrieval token budget"* — and supplies **"evidence sparsity"** as the name for why most benchmarks are blind to this effect. `smith-2024-chroma-chunking` supplies the token-level metric family (**token-level precision / recall / IoU**), with delivered tokens in the denominator. `peng-2025-adagres` shows **"token-budgeted RAG"** is now standing terminology in titles.

**Recommendation:** use `EM @ l tokens` / "at a fixed retrieval token budget" as the generic name, cite Chen et al. 2024 as the notation's origin and Lu et al. 2025 for the fairness argument, and reserve any new term strictly for what we add — which is the *comparison of delivery strategies* at matched budget, not the budget normalisation itself. Do **not** coin a new name for budget normalisation; it will read as a failure to cite.

Two adjacent frames we should name and distinguish from ours, so reviewers do not conflate them:
- **Cost–Latency–Quality / Pareto-frontier framing** (`dallaire-2025`, `qian-2026`) — normalises by dollars/latency/token-equivalents across a config sweep, not by delivered evidence at a fixed budget.
- **Nugget recall** (`pradeep-2024-autonuggetizer`) — normalises by information units in the *answer*, not by tokens in the *context*.

## 1b. Reach vs. containment — **a name exists, it is recent, and it is only a partial match.**

The established vocabulary, from `kobeissi-2026-decomposing-retrieval-failures`:

- the failure mode is **"within-document retrieval failure"** — correct document retrieved, answer-bearing chunk missed;
- the *reach* leg is **"document discovery"** / document recall (= hit, when there is one gold document);
- the *containment* leg is **page recall** / chunk-level recall, plus `CtxROUGE-L@k` as a soft proxy;
- the separation is achieved by an **oracle-document / oracle-page headroom decomposition** — restrict candidates to the gold document to isolate page/chunk headroom, restrict to gold pages to isolate chunk headroom.

Caveats before adopting: it is a February 2026 unrefereed preprint on a 150-question FinanceBench subset; "within-document retrieval failure" names the *failure*, not the *decomposition*; and their version is **not budget-normalised** (three granularity levels, fixed k).

Older, weaker vocabulary in the same space: `dai-2019` / `zhang-2021` / `li-2020-parade` use **"passage-level evidence"** and **score/representation aggregation** for the mechanism, and PARADE's **dispersed vs. concentrated relevance** is the corpus property that determines whether containment binds. `chen-2024-dense-x`'s *"recall of the gold answer within the initial l retrieved words"* is containment already measured against a budget, but not decomposed from reach.

**Recommendation:** adopt **"within-document retrieval failure"** for the phenomenon and cite Kobeissi & Langlais as prior art; keep **reach / containment** as our own names for the two *measured quantities*, since no one has named those, and state explicitly that our contribution is the **budget-normalised** version of a decomposition that exists in fixed-k form.

---

# 2. Contradicts or complicates us

1. **`boytsov-2025-positional-bias` is the hardest one.** Most long-document benchmarks front-load the evidence (*"most relevant passages tend to occur early in documents"*), and simple first-512-token baselines are hard to beat. If our corpora share that property, a large-chunk advantage may be a corpus artefact rather than a delivery-strategy effect. We should report where in the source document our gold evidence sits.

2. **`jin-2025-long-context-meets-rag` and `yu-2024-in-defense-of-rag` both report an inverted U.** If quality is non-monotonic in delivered context, then "which strategy puts the most relevant evidence in front of the generator at budget B" can have a different answer from "which strategy produces the best answer at budget B." Budget-normalised *retrieval* quality is an upper bound, not a proxy.

3. **`liu-2024-lost-in-middle` + `hsieh-2024-found-in-the-middle` + `an-2024-effective-context-length` together mean delivered ≠ used.** The generator's *effective* context is often ≤ half the nominal one, and position matters independently of content. Two strategies that deliver identical evidence sets at budget B are not interchangeable if they place the evidence differently.

4. **`cuconasu-2024-power-of-noise` complicates "relevant evidence density is what matters."** What harms the generator is *topically related but non-answer-bearing* distractors specifically — unrelated filler is less harmful. A pure density metric will not see that distinction. (And the famous 35% figure means something narrower than it is usually quoted as meaning — see the baseline check.)

5. **`sakai-2020-on-fuhrs-guideline` is a reminder that IR evaluation guidelines are contested.** Sakai agrees with us on pre-specified hypotheses and multiplicity correction, but he explicitly rejects treating any such list as absolute. We should not cite Fuhr as settled law.

6. **`zobel-2022-when-measurement-misleads` applies to our metric too.** Goodhart's law does not exempt budget-normalised metrics. If we propose one, someone will optimise against it.

7. **`sogaard-2023-two-sided-preregistration` argues pre-registration can *increase* publication bias and encourage flag-planting**, and that studies must be allowed to be re-classified as exploratory. Our three-outcome scheme is an answer to part of that; it should be presented as an answer, not as if the objection did not exist.

8. **`zhang-2021-score-aggregation` finds MaxP simply wins**, and `li-2020-parade` finds better-than-MaxP aggregation only helps on dispersed-relevance collections. If our result is "max-passage overstates", the relevant question is whether our corpora have dispersed or concentrated relevance — the same conditioning variable PARADE identified.

9. **`qu-2025-semantic-chunking` is a deflationary chunking result whose budget control I could not confirm.** If it is *not* budget-fair, it is a supporting example of the problem; if it *is*, it is a competing null result. Read §methods before citing it either way.

10. **`ferro-2024-uncontextualized-significance` implies uncorrected offline IR experiments contain substantial numbers of Type I errors.** Any *positive* result we report needs the same correction we apply to the nulls — and our multiplicity family must be declared, not reconstructed.

---

# 3. Best citable statement of each methodological rule we follow

| Rule we follow | Best citation | The statement |
|---|---|---|
| **A null result is not evidence of equivalence; report what you could have missed (power floor)** | **`webber-2008-statistical-power`** (Webber, Moffat & Zobel, CIKM 2008) | *"if the experiment fails to find significance, then one cannot simply conclude that no consequential difference exists. Instead, the experimenter wishes to know how large an actual difference in performance could have been missed."* — in an IR context, and it *is* the power-floor argument. |
| *(same rule, the canonical outside-IR source)* | `altman-1995-absence-of-evidence` (BMJ) | *"absence of evidence of a difference"* ≠ no difference; ties it to inadequate power. |
| *(same rule, the NLP-side evidence that it is widely violated)* | `card-2020-little-power` (EMNLP 2020) | Power *"has largely been ignored by the NLP community"*; underpowered designs are widespread; ships power-analysis notebooks. |
| **Pre-register hypotheses before running the experiment** | **`fuhr-2017-common-mistakes` §2.6** + **`sakai-2020-on-fuhrs-guideline` §2.6** | Fuhr: *"the complete set of hypotheses to be tested must be known before the experiment."* Sakai, who disputes most of Fuhr's list: ***"Agreed."*** Citing the pair pre-empts the "that is just one researcher's opinion" objection. |
| *(same rule, NLP-native proposal)* | `van-miltenburg-2021-preregistering` (NAACL 2021) | The NLP pre-registration proposal and registered-report argument. |
| *(same rule, the critique to engage)* | `sogaard-2023-two-sided-preregistration` (EACL 2023) | Seven concerns, including increased publication bias and the need to allow exploratory re-classification. |
| **Correct for multiple comparisons; declare the family in advance** | **`fuhr-2017-common-mistakes` §2.7** (statement) + **`carterette-2012-multiple-testing`** (the IR treatment) + **`ferro-2024-uncontextualized-significance`** (the 2024 empirical proof) | Fuhr gives the rule and the collection-reuse corollary; Carterette gives the method; Ferro & Sanderson show empirically that *"multiple testing corrections are critical"* and that ignoring context yields *"substantial numbers of Type I errors in offline IR experiments."* |
| *(same rule, across datasets rather than across metrics)* | `dror-2017-replicability-analysis` (TACL 2017) | Corrected significance testing across multiple datasets — the right shape if our family spans corpora. |
| **Non-inferiority at a pre-registered margin** | **`lakens-2017-equivalence-tests`** (+ `lakens-2018` tutorial) | TOST with bounds set from the **smallest effect size of interest**; non-inferiority is the one-sided case. **No IR/NLP prior art exists — see §4.** |
| **How many topics / queries are needed** | `sakai-2016-topic-set-size-design` (IRJ 2016); `voorhees-2002-topic-set-size` (SIGIR '02) | Principled topic-set-size design from stated statistical requirements; measure-specific because within-system variance differs by measure. |
| **Do not grow the sample until it becomes significant** | `webber-2008-statistical-power` | Iteratively adding topics until power is reached *"leads to a bias in favour of finding both power and significance."* |
| **Report effect sizes, not just p-values** | `sakai-2014-statistical-reform` (SIGIR Forum 48(1), pp. 3–12 — **read**) + `fuhr-2017` §2.8 | Sakai: a large *t* may mean a large sample *or* a large effect, and *"a p-value does not tell us which is the case."* |
| **Baselines must be strong before a gain means anything** | `armstrong-2009-improvements-dont-add-up`; `yang-2019-neural-hype`; `lin-2018-neural-hype` (with `lin-2019` recantation) | Gains are additive on strong baselines and illusory on weak ones. |
| **Budget parity is a fairness condition for comparing retrieval granularities** | `chen-2024-dense-x`; `lu-2025-hichunk` | *"To fairly compare different granularity with the same computation budget, we limit the number of retrieved tokens."* / *"to ensure fair comparison, we fix the retrieval token budget."* |
| **Offline metrics cannot close the human–machine gap** | `zobel-2022-when-measurement-misleads` | Goodhart's law and the Lucas critique applied to IR batch evaluation. |

---

# 4. Searched and did not find

**a) Non-inferiority or equivalence testing applied to IR or NLP system evaluation — genuinely absent.**
Queries run (WebSearch, then OpenAlex after the WebSearch budget was exhausted; Semantic Scholar's API returned HTTP 429 throughout):
`"equivalence test" OR "non-inferiority" information retrieval system comparison evaluation margin TOST`;
`non-inferiority equivalence testing TOST natural language processing information retrieval evaluation`;
`"non-inferiority" evaluation machine learning model comparison "margin" NLP benchmark prior art statistical`;
OpenAlex: `equivalence testing information retrieval system evaluation TOST`, `non-inferiority test natural language processing evaluation`, `statistical equivalence testing machine learning model comparison`.
Every hit was clinical/biostatistical (SMARTs, bioequivalence, diagnostic AUC non-inferiority with a δ margin) or generic methodology (Lakens; TOSTER; intersection-union permutation tests). The only ML-adjacent use found was **clinical** ML — non-inferiority on AUC with the DeLong estimator and δ = 0.05 — which is a medical-device framing, not an IR evaluation one.
**Conclusion: we appear to be first to apply a pre-registered non-inferiority margin to a retrieval-evaluation decision family. Claim it, cite Lakens for the method and Webber et al. for the IR motivation, and say plainly that no IR/NLP prior art was found.**

**b) A standing term for "budget-normalised retrieval evaluation" as a named methodology.** The *practice* and the *notation* exist (§1a) but no one has named the methodology. `EM @ l tokens` is a metric name, not a methodology name.

**c) A single established term for the reach/containment decomposition.** `kobeissi-2026` names the failure mode, not the decomposition. No standard IR term was found. (`retrievability`, Azzopardi & Vinay, is a different concept — retrieval *bias* across a collection, not within-document localisation — and should not be borrowed.)

**d) Anyone reporting statistical power floors alongside null results as a standard practice.** Explicitly checked across the pre-registration, reproducibility and weak-baseline literature. The closest are `bowman-2021-fix-benchmarking` (power/headroom as a *benchmark design criterion*, not a per-result disclosure), `webber-2008` (states the principle, does not make it a reporting norm), `sakai-2016-systematic-review` (documents that power is not reported), `card-2020-little-power` (provides the tooling), and `bouthillier-2021-accounting-variance` (variance-budget argument). **No one requires it. Our practice appears to be ahead of the field on this specific point** — though `webber-2008` supplies the argument for why it should be required, so this is executing an existing recommendation rather than inventing one.

**e) A retrospective on how the NeurIPS 2020 pre-registration workshop went.** Only the PMLR v148 preface and the existence of the v181 second edition were found. No "how did pre-registration work out for ML" paper surfaced.

**f) An ACM artifact-badging discussion in IR as a standalone research paper.** It appears only as background inside the CENTRE and OSIRRC overviews.

**g) A RAGAs critique paper that is *specifically and primarily* a RAGAs meta-evaluation.** `muller-2025-grouse` critiques automated RAG evaluation frameworks broadly without naming RAGAs in the verified material; `kreileder-2026` reports RAGAs faithfulness unreliable but as a side finding of a chunking study; `roychowdhury-2024` is a domain-transfer study. A dedicated, refereed RAGAs meta-evaluation does not appear to exist.

---

# COULD NOT VERIFY

Listed rather than dropped, because each is a real and likely-citable work that I could not confirm from a primary source in this session.

- **`callan-1994-passage-level-evidence` — "Passage-Level Evidence in Document Retrieval", James P. Callan, SIGIR 1994.** The foundational citation for passage-level evidence in document retrieval. Queries tried: WebSearch `document-level versus passage-level retrieval evaluation metrics overstate long documents` (returned only ResearchGate and academia.edu mirrors, which I do not accept as publisher pages); OpenAlex `Passage-level evidence in document retrieval Callan` (returned unrelated works — the record did not surface). ACM DL 403s. **Do not cite until the ACM DL or a Springer/author copy is fetched.**
- **`kaszkiel-1997-passage-retrieval-revisited` — Kaszkiel & Zobel, SIGIR 1997.** OpenAlex query `Passage retrieval revisited Kaszkiel Zobel` did not return it. Same treatment.
- **`carterette-2015-best-published-result` — "The best published result is random: Sequential testing and its effect on reported effectiveness", Ben Carterette, SIGIR 2015, pp. 747–750.** Known only from Fuhr's reference list (reference [3] in `fuhr-2017`, which I read in full). Not independently fetched; ACM DL 403. **Highly relevant to us** — it is the collection-reuse / sequential-testing hazard — so worth one more attempt from a primary source.
- **`li-2020-parade` venue.** The arXiv abs page states no venue. Commonly cited as ACM TOIS 42(2), 2024. Confirm before citing the TOIS reference.
- **`bouthillier-2021-accounting-variance` venue.** arXiv Comments says "Submitted to MLSys2021"; final MLSys publication unconfirmed.
- **`ulmer-2022-experimental-standards` venue.** Widely listed as Findings of EMNLP 2022; the Findings landing page was not fetched.
- **`jin-2025-long-context-meets-rag`, `an-2024-effective-context-length` venues.** OpenReview is behind a bot wall; ICLR 2025 is indicated but not confirmed from a publisher page.
- **`yu-2024-in-defense-of-rag`, `jiang-2024-longrag`, `hsieh-2024-ruler` venues.** No peer-reviewed venue stated on their arXiv abs pages. Treat as preprints.
- **`rendle-2019-difficulty-evaluating-baselines` venue.** RecSys 2019 Best Paper is attested only by Lin (2019)'s citation, not by the arXiv record.
- **`breuer-2020-measure-reproducibility` pagination.** SIGIR 2020 pp. 349–358 and DOI 10.1145/3397271.3401036 come from secondary records; the arXiv page confirms title, authors and "SIGIR 2020 Full Conference Paper".
- **A "registered reports in NLP" venue or instance** beyond the van Miltenburg et al. proposal. A lead pointing to the Northern European Journal of Language Technology surfaced in a search snippet but was not fetched.
- **`qu-2025-semantic-chunking` budget control.** Whether their chunking comparison holds retrieved tokens fixed is not stated on the ACL Anthology page. Read §methods.
- **`kreileder-2026-chunking-academic` chunk sizes and fixed-k vs. fixed-budget.** Not stated on the arXiv abs page.

## Access notes for whoever picks this up

- `dl.acm.org` returned HTTP 403 on every attempt. `dblp.org` is behind an Anubis bot wall. `link.springer.com` 303s to an IdP. `openreview.net` has a bot-check wall. `api.semanticscholar.org` returned HTTP 429 throughout.
- What worked: ACL Anthology, arXiv `/abs/`, PMLR, CEUR (`ceur-ws.org`), `sigir.org/wp-content/uploads/...` for SIGIR Forum PDFs, author institutional pages (`dei.unipd.it/~ferro`, `people.eng.unimelb.edu.au/jzobel`, `cs.uwaterloo.ca/~jimmylin`), institutional repositories (`waseda.elsevierpure.com`, `backend.orbit.dtu.dk`), NII (`research.nii.ac.jp`) and the OpenAlex API.
- WebFetch on a PDF saves it locally and prints the path; extract with
  `/rag/envs/ragstack/bin/python3 -c "import fitz; d=fitz.open('<path>'); print(''.join(p.get_text() for p in d))"`.
- The session's WebSearch budget (200 calls) was exhausted partway through; the remainder was done with WebFetch, the arXiv API and the OpenAlex API via Bash.
