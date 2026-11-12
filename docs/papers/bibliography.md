# Bibliography

Every entry here was fetched and read before it was listed. Each records what the paper
**actually establishes**, what it controlled for, and how much weight it can carry — not what
its abstract claims. See the `study-writeup` skill for the verification rule.

Status key: **verified** = title, authors, venue and year confirmed against the publisher or
arXiv page. **read** = the full text was retrieved and the methods checked, not just the
abstract.

---

## Chunking and segmentation for retrieval

### `qu-2025` — Is Semantic Chunking Worth the Computational Cost?
Renyi Qu, Ruixuan Tu, Forrest Sheng Bao. Findings of the ACL: NAACL 2025.
<https://aclanthology.org/2025.findings-naacl.114/> · **verified, read (full PDF)**

Compares fixed-size against breakpoint-based and clustering-based semantic chunkers on three
tasks (document retrieval, evidence retrieval, answer generation) over ten datasets from BEIR
and RAGBench. Concludes the computational cost of semantic chunking is not justified by
consistent gains.

**What makes it useful to us is not the headline.** Sweeping the semantic chunkers'
hyperparameters, they observe that the hyperparameter acts *through* chunk size — "Regardless
of the threshold type, it ultimately determines chunk size" — and that as their positional
weighting parameter approaches the fixed-size case the score rises, which they read as
positional information mattering more than semantic similarity. That is our realised-size
result reached from a different direction, and it is the right thing to cite for it.

**Their stated limitations name three gaps our work fills**, which is the strongest available
framing for our contribution:
- sentence embeddings are context-free, and "further exploration of contextual embeddings is
  necessary before definitively concluding the limitations of semantic chunking";
- "Lack of Chunk Quality Measures" — they had no ground-truth query-chunk relevance and fell
  back on document or evidence mapping, which is exactly our passage-level endpoint;
- "Lack of Suitable Datasets" — an ideal dataset "would include long documents", and they
  excluded corpora whose documents were too short. That is our BEIR finding.

**Weight: strong**, and complementary rather than duplicative. Note their chunk-size
hyperparameter is *number of chunks*, not tokens, and they do not report realised token
lengths, so they share our confound rather than having controlled it.

### `wang-2025-pic` — Document Segmentation Matters for Retrieval-Augmented Generation
Zhitong Wang, Cheng Gao, Chaojun Xiao, Yufei Huang, Shuzheng Si, Kangyang Luo, Yuzhuo Bai,
Wenhao Li, Tangjian Duan, Chuancheng Lv, Guoshan Lu, Gang Chen, Fanchao Qi, Maosong Sun.
Findings of the ACL 2025. <https://aclanthology.org/2025.findings-acl.422/> · **verified**

Proposes PIC (Pseudo-Instruction for document Chunking): use a document summary as a
pseudo-instruction, compute sentence-to-summary semantic similarity, group sentences
dynamically. Reports Hits@k and end-to-end exact-match gains on open-domain QA with no
additional training.

**Weight: genuine positive**, and the mechanism matters for how we cite it. PIC is *informed*
segmentation — guided by a document-level summary — not naive local-similarity breakpoint
detection. It is evidence for the second of the two ideas that share the name "semantic
chunking", not the first.

### `zhao-2025-moc` — MoC: Mixtures of Text Chunking Learners for Retrieval-Augmented Generation System
Jihao Zhao, Zhiyuan Ji, Zhaoxin Fan, Hanyu Wang, Simin Niu, Bo Tang, Feiyu Xiong, Zhiyu Li.
ACL 2025 (long). <https://aclanthology.org/2025.acl-long.258/> · **verified**

Introduces chunking-quality metrics and a granularity-aware Mixture-of-Chunkers framework.
Argues both traditional rule-based and simple semantic chunking are limited, and proposes
learned chunkers instead.

**Weight: positive, supporting the same distinction as `wang-2025-pic`** — the approaches that
win are learned or guided, not local-similarity thresholds.

### `kreileder-2026` — Evaluating Chunking Strategies for Retrieval-Augmented Generation on Academic Texts
Valentin J. J. Kreileder, Johannes Reisinger, Andreas Fischer. arXiv 2607.01852, 2 July 2026.
<https://arxiv.org/abs/2607.01852> · **verified**

Cluster-based semantic chunking against fixed-size and recursive chunking on academic theses,
evaluated with RAGAs. Finds cluster-based chunking did not outperform the simpler strategies.

**Weight: weak. Cite with its caveats or not at all.** The documents are theses rather than
journal articles; the evaluation framework is RAGAs, and the authors themselves report that
"RAGAs based faithfulness shows limited reliability in this setup"; and the result is a null
with no power analysis. It is consistent with `qu-2025`, not independent confirmation of it.

### `allamraju-2025` — Breaking It Down: Domain-Aware Semantic Segmentation for Retrieval Augmented Generation
Aparajitha Allamraju, Maitreya Prafulla Chitale, Hiranmai Sri Adibhatla, Rahul Mishra,
Manish Shrivastava. arXiv 2512.00367, 29 November 2025.
<https://arxiv.org/abs/2512.00367> · **verified, read (full PDF)**

Two trained semantic chunkers (Projected Similarity Chunking, Metric Fusion Chunking) trained
on PubMed Central full text, evaluated on PubMedQA augmented with full-text articles. Abstract
reports a 24× improvement in MRR with PSC and higher Hits@k.

**Weight: very weak as evidence for semantic chunking, despite being the closest paper to our
domain. Do not cite the 24× without the baseline.** Read the results table: the Character,
Sentence and Recursive baselines score **0.0** MRR and the semantic baseline scores **0.01**,
so the 24× multiple is measured against a baseline that essentially never retrieves the gold.
The reason is visible in the construction — the evaluation gold is the human-authored context
sentences from PubMedQA, and their chunkers are trained on positive pairs defined as sentences
the human author placed in the same section, so the method is trained to reproduce the
structure that defines the gold while fixed-size chunkers cut across it. The authors concede
the gold "is human-authored" for both sides. Retrieval is evaluated on **115 queries**, the
only PQA-L items with full-text matches. Their chunks average 471 tokens and the baselines'
realised sizes are not reported, so the realised-size confound is present and uncontrolled.

This entry is the project's worked example of why a citation gets read before it gets listed.

---

## Annotation agreement and evidence localisation

These three are cited by `paper-a-evidence-localisation/OUTLINE.md` and were, until
2026-09-22, in neither this file nor any `bib-inbox/` survey — i.e. recalled, not verified, which
is the failure the `study-writeup` skill exists to prevent. Each was fetched on 2026-09-22; the
status line says how far it was read.

### `hofstatter-2020-fira` — Fine-Grained Relevance Annotations for Multi-Task Document Ranking and Question Answering
Sebastian Hofstätter, Markus Zlabinger, Mete Sertkan, Michael Schröder, Allan Hanbury.
CIKM 2020. <https://doi.org/10.1145/3340531.3412878> · arXiv 2008.05363 · **verified, read (full
PDF, §5 "Annotation quality")**

Extends TREC-DL 2019 document ranking with passage- and word-level graded relevance for all
relevant documents (FiRA), and reports inter-annotator agreement as Cohen's κ **of each student
annotator against the majority-vote aggregate**, on the 10 query–document pairs every student
annotated. Their own numbers, verbatim: for 2-class relevance and for "the labeling of the relevant
word phrases, substantial Kappa agreements are reached, ranging from 0.5 to 0.8"; for 4-class
grading "a mediocre agreement ranging from 0.3 to 0.6".

**What it establishes for us:** a published measurement in which agreement on *where* the evidence
is (word selection) is **as high as** agreement on *whether* the document is relevant (2-class).
That is the counter-result to our headline and Paper A must engage it. **It is not** an "opposite
ordering" — location does not out-agree presence here; they tie — and an earlier draft of the
outline mis-stated this. Weight: **moderate, with stated blunting factors** — κ against an
aggregate that contains the rater being scored inflates agreement; n = 10 pairs; non-expert
student annotators; only documents already judged relevant were annotated, so the presence
condition is easier than ours. They themselves note agreement in line with Alonso & Mizzaro for
non-expert TREC labelling. Cite it as the published tie, then say why our design differs.

### `mathew-2021-hatexplain` — HateXplain: A Benchmark Dataset for Explainable Hate Speech Detection
Binny Mathew, Punyajoy Saha, Seid Muhie Yimam, Chris Biemann, Pawan Goyal, Animesh Mukherjee.
AAAI 2021. <https://doi.org/10.1609/aaai.v35i17.17745> · arXiv 2012.10289 · **verified (title,
authors, venue, year, abstract); not read past the abstract**

Cited only as prior art for a **soft per-token rationale** — each post annotated by several
raters with the token spans their label rests on, aggregated into a per-token score. That is the
representation the outline's "not novel" table needs it for, and the abstract establishes it.
Weight for that purpose: **mention, not support**. Do not cite it for any number.

### `warfield-2004-staple` — Simultaneous Truth and Performance Level Estimation (STAPLE): An Algorithm for the Validation of Image Segmentation
Simon K. Warfield, Kelly H. Zou, William M. Wells. IEEE Transactions on Medical Imaging 23(7),
2004. <https://doi.org/10.1109/tmi.2004.828354> · **verified (title, authors, venue, year,
abstract); not read past the abstract**

The canonical **probabilistic per-unit reference estimated by EM over multiple raters** — a
per-voxel truth estimate plus per-rater sensitivity/specificity, from segmentations that
individually disagree. Cited as the representational ancestor of a graded pooled per-sentence
support map, which is all the "not novel" table claims for it. ~2,100 citations (OpenAlex).
Weight for that purpose: **mention, not support**.

## Evidence dispersion and question type

The owner's working assumption for the planned cross-section study is that questions whose answer evidence spans more than one section of a paper are *usually* composite, meaning multi-hop, comparative or aggregating, rather than simple factoid look-ups. Three surveys covered multi-hop QA, scientific and long-document QA, and RAG and chunking. They were merged, and the eleven papers that carry the argument were checked against their PDFs on 2026-11-11. Page and table references below are to those PDFs. **Verdict: the assumption is not supported as stated.** No paper measures section-level evidence spread against question type, in scientific papers or in any other domain, so *usually* has never been tested. The one dataset that allows a test in our direction (`zhu-2021-tatqa`) cuts against it. The two scientific-paper base rates show that spread evidence is common among questions that were never written to be composite. The record supports something weaker: spread evidence is common, it goes with how *much* evidence a question needs and with enumeration-style answers, and it is somewhat harder for models. Composite questions, for their part, are often answerable from one place.

### `dasigi-2021-qasper` — A Dataset of Information-Seeking Questions and Answers Anchored in Research Papers
Pradeep Dasigi, Kyle Lo, Iz Beltagy, Arman Cohan, Noah A. Smith, Matt Gardner. NAACL-HLT 2021,
pp. 4599–4610. ACL Anthology 2021.naacl-main.365 · arXiv 2105.03011 · **verified, read (full PDF)**

Questions over NLP papers. The question writers saw only the title and abstract, and separate annotators supplied the answers and the evidence paragraphs. Answer types (p4): extractive 51.8%, abstractive 24.2%, yes/no 13.9%, unanswerable 10.2%.

**What it establishes for us:** the closest measured base rate to our setting. In §3 (p3), among answerable questions whose evidence is text only, "55.5% of the answers have multi-paragraph evidence". Cite that sentence with its denominator. The introduction restates the figure loosely as a share of all questions, and that version should not be cited. The writers could not see the paper, so the spread was not built into the questions. This is direct evidence that ordinary information-seeking questions about papers often draw on several paragraphs. **What it does not give:** a breakdown by question or answer type (absent), or a multi-*section* share. The unit is the paragraph, so two adjacent paragraphs in Methods count as multi-paragraph. Two things blunt the number further:
- Annotators were told to prefer earlier paragraphs when several qualified (p3). That rule admits that evidence is sometimes redundant.
- The human lower-bound Evidence-F1 is 71.62 (Table 3, p7). Annotators often disagree about *which* paragraphs are the evidence, so the single-versus-multi label is noisy.

**Weight: strong as a paragraph-level base rate, provided the denominator is stated; absent for any relation to question type.**

**Re-measured on the released data (2026-11-11, exploratory).** The paper's 55.5% does not reproduce from the public release. Under the paper's own definition (answerable answers, text-only evidence, more than one paragraph) the v0.3 release gives 32.3% (train), 30.1% (dev) and 31.4% (test), and the v0.1 release gives the same counts; only the union of paragraphs over a question's annotators on the test split comes near (57.6%). The same measurement gives the section-level shares the paper does not report: 14.2% of train+dev answers span more than one full-path section and 10.1% more than one top-level section, with Fleiss κ 0.22–0.33 for the single- versus multi-section label ([qasper-dispersion-2026-11](../plans/results/qasper-dispersion-2026-11/RESULTS.md), `artifacts/qasper-dispersion.json`). Until the gap is explained, cite the paper's figure only as the paper's claim and use the re-measured figures for anything load-bearing.

### `baumgartner-2025-peerqa` — PeerQA: A Scientific Question Answering Dataset from Peer Reviews
Tim Baumgärtner, Ted Briscoe, Iryna Gurevych. NAACL 2025. doi 10.18653/v1/2025.naacl-long.22 ·
arXiv 2502.13668 · **verified, read (full PDF)**

Questions taken from peer reviews of scientific papers and answered by the papers' authors. 579 QA pairs.

**What it establishes for us:**
- **A second base rate.** "30% of questions have non-consecutive answer evidence" (p5). The appendix (p19) restates this as 30% of questions having more than one location in the paper.
- **A null on difficulty.** The same page reports that the authors found no consistent performance differences between questions with single and multiple evidence chunks.
- **Redundancy.** The authors also note on p19 that separate locations may carry similar information, or that one chunk may be enough on its own. In other words, multi-location evidence is often redundant rather than composite.

The paper does not cross-tabulate question class against number of locations (absent). The unit is a non-consecutive location, not a section.

**Weight: strong as a base rate and as counter-evidence, with blunting factors.** The sample is small. The redundancy point is the authors' interpretation, not a coded measurement. The performance null is unquantified and has no power analysis, so cite it as "no consistent difference reported", never as a powered null.

### `zhu-2021-tatqa` — TAT-QA: A Question Answering Benchmark on a Hybrid of Tabular and Textual Content in Finance
Fengbin Zhu, Wenqiang Lei, Youcheng Huang, Chao Wang, Shuo Zhang, Jiancheng Lv, Fuli Feng,
Tat-Seng Chua. ACL-IJCNLP 2021 (long), pp. 3277–3287. ACL Anthology 2021.acl-long.254 ·
arXiv 2105.07624 · **verified, read (full PDF)**

Table 2 (p4) crosses answer type with evidence source over 16,552 questions:

| | Table | Text | Table-text | Total |
|---|---|---|---|---|
| Span | 1,801 | 3,496 | 1,842 | 7,139 |
| Spans (multiple answer spans) | 777 | 258 | 1,037 | 2,072 |
| Counting | 106 | 5 | 266 | 377 |
| Arithmetic | 4,747 | 143 | 2,074 | 6,964 |
| Total | 7,431 | 3,902 | 5,219 | 16,552 |

**What it establishes for us:** the only published cross-tabulation found that tests our direction, P(complex | evidence spans two sources). All shares below are **derived** from the table.
- **Make-up of the 5,219 table-text questions:** 35.3% are single-span extractions, 19.9% multi-span lists, 5.1% counting and 39.7% arithmetic.
- **Whether *usually complex* holds depends on the definition.** If arithmetic and counting count as complex, 44.8% of these questions are complex and *usually* fails. If multi-span lists also count, 64.7% are complex and it holds narrowly.
- **The reverse direction.** The share needing table-text evidence is almost the same for single-span answers (25.8%) and arithmetic answers (29.8%). 68.2% of arithmetic questions are answered from the table alone. Only aggregation types rise clearly: multi-span 50.0%, counting 70.6%.

*Spans* here means several answer spans, not several evidence locations.

**Weight: strong as measured data; transfer to our setting is indirect.** "Spread" here means a table and at least two paragraphs that describe, analyse or complement it (p2), not sections of a long document.

### `min-2019-compositional` — Compositional Questions Do Not Necessitate Multi-hop Reasoning
Sewon Min, Eric Wallace, Sameer Singh, Matt Gardner, Hannaneh Hajishirzi, Luke Zettlemoyer. ACL 2019,
pp. 4249–4257. ACL Anthology P19-1416 · arXiv 1906.02900 · **verified, read (full PDF)**

A single-paragraph BERT model reaches 67.08 F1 on the HotpotQA distractor dev set.
- In manual analysis of 100 sampled bridge questions, 27% actually require multi-hop reasoning (p3; Table 2, p4).
- By rule-based automatic categorisation of comparison questions, 45% do (Table 3, p4).

**What it establishes for us:** it tests the *opposite* direction and finds that composite questions often do not need evidence from several places. It bears on our direction only by showing that the two properties come apart. Composite form and spread evidence cannot be treated as one variable, so neither can stand in for the other in a study design.

**Weight: strong for "composite does not imply dispersed"; indirect for our direction.** The questions are built from pairs of Wikipedia opening paragraphs, not paper sections, and the bridge sample is n = 100.

### `pang-2022-quality` — QuALITY: Question Answering with Long Input Texts, Yes!
Richard Yuanzhe Pang, Alicia Parrish, Nitish Joshi, Nikita Nangia, Jason Phang, Angelica Chen,
Vishakh Padmakumar, Johnny Ma, Jana Thompson, He He, Samuel R. Bowman. NAACL-HLT 2022,
pp. 5336–5358. ACL Anthology 2022.naacl-main.391 · arXiv 2112.08608 · **verified, read (full PDF)**

Validators rated how much of the text each question needs. On p4, 81.3% of hard questions and 73.9% of easy ones are rated as "needing at least a long paragraph or two".

**What it establishes for us:** needing a lot of context is the norm even for easy questions, and the gradient by difficulty is only 7.4 points. Two limits: the split is by difficulty, not by question type, and the rating measures *extent*, not dispersion. A long paragraph or two can be contiguous.

**Weight: moderate.** This is direct human judgement, but of extent rather than spread. The texts are fiction and magazine articles, not papers.

### `qin-2026-lurch` — Beyond Context Length: Characterising the Difficulty of Long-Context Reasoning
Shiwen Qin, Cameron Barker, Henry Gouk, Elliot J. Crowley, Shay B. Cohen, Mirella Lapata, Miao Li.
arXiv 2610.22067, 15 October 2026 (no venue). · **verified, read (full PDF)**

Introduces the LURCH benchmark: 6,550 instances drawn from existing datasets. It defines evidence **scope**, evidence **dispersion** and **reasoning depth** as separate properties and measures all three on the same items.

**What it establishes for us:** it is the only paper that measures dispersion and reasoning complexity separately on the same items, and it treats them as distinct axes. "Only 11% of instances have all evidence in a single contiguous block" (p6). The appendix (p19) reports scope–dispersion r = 0.55 overall (0.45 after centring), the strongest pairwise association. So dispersion goes most with *how much* evidence a question needs, which is the aggregation side of the assumption, not the depth side. These are descriptive relations between properties, not causal ones. Dispersion–depth is r = 0.10 (0.02 within-task centred; Fig. 9, p20), which supports the aggregation reading over the depth reading.

**Weight: moderate.** It is recent and not peer-reviewed. The 11% is not a natural rate: the benchmark prioritises tasks involving multi-hop reasoning, comparison, aggregation or evidence integration over single-fact retrieval (p4), which inflates dispersion directly. It is the source to cite for the established vocabulary.

### `buchmann-2024-attribute` — Attribute or Abstain: Large Language Models as Long Document Assistants
Jan Buchmann, Xiao Liu, Iryna Gurevych. EMNLP 2024 (per arXiv comment). arXiv 2407.07799 ·
**verified, read (full PDF)**

Table 1 (p4) gives evidence segments per instance at paragraph level: QASPER 1.7, Natural Questions 1, Evidence Inference 1.1, ContractNLI 1.5. WiCE's 3.7 is at sentence level and cannot be compared with the others.

**What it establishes for us:** the authors' explanation for why some datasets have more evidence segments. Of WiCE and QASPER they say instances "often contain multiple facts (e.g. an enumeration or multiple subclaims)" (p9). That supports a narrower version of the assumption: multi-evidence questions are enumeration or aggregation questions, not multi-hop chains. It agrees with the aggregation rows of `zhu-2021-tatqa` and with LURCH's scope–dispersion link.

**Weight: moderate for the descriptive counts; weak for the type explanation.** The explanation is an informed assertion at dataset level, not a per-question coding.

### `chen-2021-finqa` — FinQA: A Dataset of Numerical Reasoning over Financial Data
Zhiyu Chen, Wenhu Chen, Charese Smiley, Sameena Shah, Iana Borova, Dylan Langdon, Reema Moussa,
Matt Beane, Ting-Hao Huang, Bryan Routledge, William Yang Wang. EMNLP 2021, pp. 3697–3711.
ACL Anthology 2021.emnlp-main.300 · arXiv 2109.00122 · **verified, read (full PDF)**

On p5:
- evidence source: 23.42% of questions need text only, 62.43% table only, 14.15% both;
- program length: 59.10% one step, 32.71% two steps, 8.19% three or more.

The paper does not cross-tabulate steps against source (absent).

**What it establishes for us:** a **derived** bound in the opposite direction. At least 40.90 − 14.15 = 26.75% of all questions are multi-step yet draw on a single source, which is at least 65% of the multi-step questions. Complex questions are commonly answered from one element.

**Weight: moderate and indirect.** Each context is one report page, so table and text sit next to each other.

### `ma-2024-mmlongbench` — MMLongBench-Doc: Benchmarking Long-context Document Understanding with Visualizations
Yubo Ma, Yuhang Zang, Liangyu Chen, Meiqi Chen, Yizhu Jiao, Xinze Li, Xinyuan Lu, Ziyu Liu, Yan Ma,
Xiaoyi Dong, Pan Zhang, Liangming Pan, Yu-Gang Jiang, Jiaqi Wang, Yixin Cao, Aixin Sun. NeurIPS 2024
Datasets and Benchmarks (PDF p1 footer). arXiv 2407.01523 · **verified, read (full PDF)**

365 of the 1,082 questions (33.7%) are cross-page. For newly added questions the authors put more focus on cross-page and unanswerable questions, to balance the distribution (p5). In Table 3 (p7), GPT-4o scores 54.5 on single-page questions and 41.5 on cross-page ones.

**What it establishes for us:** for that model, cross-page questions are harder. The cross-page share was engineered, so it says nothing about a natural rate, and the paper does not cross question type with single- versus cross-page (absent).

**Weight: none for the base rate; moderate for "spread is harder"** (only one model's figures are cited here).

### `welbl-2018-wikihop` — Constructing Datasets for Multi-hop Reading Comprehension Across Documents
Johannes Welbl, Pontus Stenetorp, Sebastian Riedel. TACL 6 (2018), pp. 287–302. arXiv 1710.06481 ·
**verified, read (full PDF)**

The questions are built by following knowledge-base paths across documents, so they are composite by construction. In human validation (p7), among samples judged *follows* or *likely*, 55.9% were marked as needing multiple documents and 44.1% as needing a single document. The authors call the single-document share "larger than initially expected", and add that many such cases give a clear hint within one document that annotators completed with background knowledge.

**What it establishes for us:** a second result in the opposite direction. Questions that are composite by construction need only one source almost half the time. The denominator is the samples judged *follows* or *likely*, **not** all answerable samples.

**Weight: moderate.** The units are separate documents, not sections of one document, and the 44.1% includes cases solved by annotator background knowledge.

### `mortezaagha-2026-gralc` — Graph-Aware Late Chunking for Retrieval-Augmented Generation in Biomedical Literature
Pouria Mortezaagha, Arya Rahgozar. arXiv 2603.22633, 23 March 2026. · **verified, read (full PDF)**

2,359 IMRaD articles from PubMed Central, with 2,033 **synthetic** cross-section questions generated from five templates. Content-similarity retrievers score best on MRR yet "always retrieve from a single section".

**What it establishes for us:** this is the nearest setting to ours (scientific papers, cross-section questions), but templates impose the cross-section property. Question type and dispersion are therefore identical by construction, and the paper says nothing about our direction. Two readings of the single-section result are possible, and the paper cannot separate them:
- many template questions may be answerable from one section;
- MRR may reward the first relevant hit over synthesis.

**Weight: weak, but it must be cited and engaged** as the closest prior work.

**How these bear on the assumption.** The record does not settle the assumption, and the parts that come close lean against it. Most of the evidence tests the reverse implication, composite ⇒ dispersed, and finds it fails often: only 27% of HotpotQA bridge questions (`min-2019-compositional`), 44.1% of judged WikiHop samples needing one document (`welbl-2018-wikihop`), at least 26.75% of FinQA questions multi-step but single-source (`chen-2021-finqa`), and 68.2% of TAT-QA arithmetic answers coming from the table alone. Only `zhu-2021-tatqa` tests our direction, dispersed ⇒ composite. There, 35.3% of questions with spread evidence are plain single-span extractions, and *usually* holds only if multi-span lists count as complex. That is the enumeration pattern `buchmann-2024-attribute` describes and the scope–dispersion link `qin-2026-lurch` measures: aggregation, not multi-hop reasoning. The scientific-paper base rates show spread evidence among questions nobody designed to be composite: 55.5% multi-paragraph in `dasigi-2021-qasper` and 30% non-consecutive in `baumgartner-2025-peerqa`. Both papers also give reasons to think part of that spread is redundant restatement rather than composition. The link to difficulty is real but modest and not consistent. `pang-2022-quality` shows a 7.4-point gradient in context needed, and `ma-2024-mmlongbench` shows GPT-4o 13 points lower on cross-page questions, while `baumgartner-2025-peerqa` reports no consistent difference. **No paper measures section-level spread against question type on naturally occurring questions.** The nearest attempt (`mortezaagha-2026-gralc`) imposes cross-sectionness by template. The cross-section study should therefore treat question type as something to measure, not something to infer from dispersion. It should use the established terms (evidence dispersion, scope, reasoning depth), separate necessary from redundant multi-location evidence, and count sections rather than paragraphs. QASPER's released annotations are the obvious place to take that measurement.

---

## How these fit together

The published record does **not** say semantic chunking fails. It says:

- naive local-similarity breakpoint detection has not earned its cost (`qu-2025`, weakly
  `kreileder-2026`), and
- *informed* segmentation — guided by a summary, or learned, or domain-trained — reports gains
  (`wang-2025-pic`, `zhao-2025-moc`, and `allamraju-2025` if one accepts its baselines).

Our chunker belongs to the first family, and our result is consistent with it. We have not
tested the second. **No paper in this set controls realised chunk size across methods**, which
is where our contribution sits.
