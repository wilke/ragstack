# Bib inbox 11 — Information density and context dependency: a citation check and a short gap survey

> **Provenance.** Produced 2026-10-13 for the chunking study. Its input was an LLM-written analysis plus
> a "literature review" (research date given as 2026-10-12) that the owner pasted into a session. That
> text is not in this repo. It proposed information density and "contextual dependency" as variables
> for choosing chunk size, and it cited 16 works. §0 checks every one of those citations. §1–§5 are a
> short search of the five areas the text touches, at most about 12 queries per area. Metadata comes
> from the arXiv `/abs/` page, the ACL Anthology, PubMed/PMC, Crossref, OpenAlex, the NeurIPS
> proceedings site or the publisher page; the status tag on each entry says which. The arXiv **query**
> API (`export.arxiv.org/api/query`) returned HTTP 503 or hung on every call in this session, so
> arXiv search was replaced by OpenAlex `title_and_abstract.search` and by `/abs/` pages fetched by id.
> The queries that failed are listed in *Searched and did not find*. No model endpoint was called.
> The design note that uses this file is
> [`docs/plans/results/design/PLAN-information-density.md`](../../plans/results/design/PLAN-information-density.md).

**Status key.** `verified (page)` = title, authors, venue and year read off the publisher page, ACL
Anthology, NeurIPS proceedings or the arXiv `/abs/` page. `verified (record)` = Crossref, PubMed or
OpenAlex record only. `abstract` = the abstract was read. `read` = the text was retrieved and the
cited passage checked. Nothing here rests on recall.

**Our question, asked of every paper:** *does it measure density or context dependency **before**
retrieval (a property of the text, computed without the query's outcome), and relate it to a
retrieval or evidence outcome?* Recorded as **(Q)** on each entry.

---

## 0. The citation check — the 16 works the external text cites

Verdicts: **VERIFIED** (the work exists as cited and the attributed claim is in it);
**VERIFIED-CLAIM-WRONG** (the work exists, but the claim attributed to it is wrong or materially
mis-stated); **ALREADY-IN-SURVEY** (already in `04` or `05`, cross-checked against our entry);
**NOT FOUND**.

| # | as cited | verdict | what the check found |
|---|---|---|---|
| 1 | Rubino et al., COLING 2016 | **VERIFIED**, with a caveat | Exists (C16-1072). The abstract says information-theoretic (ID) features "outperform more traditional features". The body qualifies this. At the lexical level ID features come **second**, behind n-gram frequency (F 0.984 vs 0.991). At part-of-speech level they come third (0.9112 vs POS 3-grams 0.9224). They are best only at syntax (0.88). The task classifies **sentences** of 19th- vs 20th-century abstracts. The alternative title "Information Density in Scientific Writing: A Diachronic Perspective" was **not found** as a separate work. |
| 2 | "Diachronic Changes in Lexical Density of Research Article Abstracts", Lingua 2024 | **VERIFIED** (metadata), claim from a secondary snippet | Zhu, Wang & Pang, *Lingua* 312, 103837 (Dec 2024), confirmed via Crossref. The claims (16 disciplines; lexical density rising; hard > soft disciplines) match the abstract as quoted in a search-engine result. The publisher page is a bot wall, and Crossref, OpenAlex and Semantic Scholar carry no abstract. Corpus size and method were not read. |
| 3 | Brown et al. 2008, CPIDR, PMC | **VERIFIED** | *Behavior Research Methods* 40(2):540–545, PMC2423207. Agreement as stated: CPIDR agrees with the consensus of two raters at r(80) = .97, and five raters agree with each other at r(10) = .82. These are two different comparisons with different n, so "better than humans" is the authors' gloss. |
| 4 | "Propositional Density: Cognitive Impairment and Aging", 2024 review | **VERIFIED** | Harrag, Sabil, Conceição & Radvansky, *Frontiers in Psychology* 15 (29 Aug 2024), doi:10.3389/fpsyg.2024.1434506. It is a survey, filed under "Reviews in Psychology of Language". It is about cognitive aging; nothing in it concerns retrieval. |
| 5 | Chen et al., Dense X, EMNLP 2024 | **ALREADY-IN-SURVEY** (`04` `chen-2024-densex`; `05` `chen-2024-dense-x`) | The claim (propositions beat passages under a budget) is right, with two caveats from our entries. The gap peaks at 100–200 words and closes as the budget grows. The 2026 reproductions (`zhou-2026`, `smigielski-2026` in `04`) reverse it in-corpus. **Internal disagreement:** `04` and `05` use different keys for the same paper. Dedupe to one key when promoting to `bibliography.md`. |
| 6 | de Moura Júnior et al., Adaptive Chunking, LREC 2026 | **VERIFIED-CLAIM-WRONG** (also ALREADY-IN-SURVEY, `04` `demoura-2026`) | The five metrics and the legal/technical/social-science corpus are right. **"Answer correctness rose from 62–64% to 72%" is wrong**, and the error starts in the paper's own abstract. Table 5 gives three rows. Answer Correctness is **78.01** (Adaptive) vs 70.11 / 73.33. Retrieval Completeness is 67.68 vs 58.08 / 59.09. **The 71.77 vs 62.07 / 63.80 figures are the mean of those two rows.** Answer Correctness also skips unanswered queries, so its denominators differ (65 vs 49 answered of 99). Only the Retrieval Completeness differences were significance-tested. The scale is 33 documents and 99 GPT-4.1-generated questions, with GPT-4.1 as judge. Our `04` entry does not quote the 62–64 → 72 figure and disagrees with nothing. It should gain the corpus size and this correction. |
| 7 | Günther et al. 2024, Late Chunking | **ALREADY-IN-SURVEY** (`04` `gunther-2024`) | arXiv 2409.04701 v3 still says "3rd draft"; it is not peer-reviewed. Our entry stands. |
| 8 | Sarthi et al. 2024, RAPTOR | **ALREADY-IN-SURVEY** (`04` `sarthi-2024-raptor`) | Published at ICLR 2024 (the external text gives no venue). Our entry stands. |
| 9 | Wang & Han, PropRAG, EMNLP 2025 | **VERIFIED** | EMNLP 2025 main, pp. 6224–6239 (2025.emnlp-main.317). The paper evaluates on **2Wiki**, HotpotQA and MuSiQue; the text omits 2Wiki. |
| 10 | Zhu et al., IB for noise filtering, ACL 2024 | **VERIFIED**; objective stated loosely | 2024.acl-long.59. The paper writes the objective as a **minimisation conditioned on the query**: min L_IB = I(X̃; X \| Q) − β I(X̃; Y \| Q) (§3). The text's "max I(Z;Y) − β I(Z;X)" is the same trade-off up to a reparametrised β, but it drops the conditioning on Q. The method is post-retrieval compression (2.5 % compression rate), not chunking. |
| 11 | Jiang et al., LongLLMLingua, ACL 2024 | **VERIFIED** | ACL 2024 (2024.acl-long.91; arXiv 2310.06839). The abstract says LLM performance "hinges on the density and position of key information in the input prompt", which motivates the method; the paper does not measure density as a text property. |
| 12 | Liu et al., Lost in the Middle, TACL 2024 | **ALREADY-IN-SURVEY** (`05` `liu-2024-lost-in-middle`) | TACL 12:157–173 (2024.tacl-1.9). Our entry stands. |
| 13 | Packer et al. 2023, MemGPT | **VERIFIED** (metadata) | arXiv 2310.08560; no venue on the record. Agent memory management; off-topic for chunking. |
| 14 | Xu et al. 2025, A-MEM | **VERIFIED** (metadata) | arXiv 2502.12110; NeurIPS 2025 per the arXiv comment. Off-topic for chunking. |
| 15 | Hu et al. 2025, "Memory in the Age of AI Agents" | **VERIFIED** (metadata) | arXiv 2512.13564 (15 Dec 2025), Yuyang Hu et al.; a preprint, with no venue. Off-topic for chunking. |
| 16 | "uniform information density", uncited | **VERIFIED** (canonical sources supplied) | Levy & Jaeger, "Speakers optimize information density through syntactic reduction", NIPS 19 (2006 conference, proceedings 2007). Jaeger, "Redundancy and reduction: Speakers manage syntactic information density", *Cognitive Psychology* 61(1):23–62, 2010. UID is a hypothesis about **speakers** spreading surprisal evenly. It is not a measure of how much retrievable content a text carries, and the text uses it as if it were. |

**Tally:** 11 VERIFIED (#2 at metadata level only, with its claim read from a secondary snippet;
#13–#15 at metadata level), 1 VERIFIED-CLAIM-WRONG (#6, which is also in `04`), 4
ALREADY-IN-SURVEY (#5, #7, #8, #12), 0 NOT FOUND. One alternative title given under #1 was not
found as a separate work. The claim-level error that matters is #6: it is
the paper nearest to the external text's proposal, and the external text repeated the abstract's
mislabelled number.

### 0.1 Per-entry records for the citations not already in `04`/`05`

### `rubino-2016-diachronic-id`
**Modeling Diachronic Change in Scientific Writing with Information Density.** Raphael Rubino, Stefania
Degaetano-Ortlieb, Elke Teich, Josef van Genabith. COLING 2016, pp. 750–761.
<https://aclanthology.org/C16-1072/> · **verified (page)** · **read** (§4).

Surprisal-based ("information density") features at lexical, POS and syntactic levels classify
sentences of 19th- vs 20th-century scientific abstracts (Royal Society corpus). Best combination 99.18 %
accuracy (n-gram frequency + ID features). ID features are compact (216 features) but not the single
best set at two of three levels.
**(Q) Before retrieval: yes, density is a text property. Related to retrieval: no.** Useful only as
evidence that per-token surprisal separates registers of scientific prose, so it is a candidate
*descriptor*, not a predictor of retrieval outcome.

### `zhu-2024-lingua-lexical-density`
**Diachronic changes in lexical density of research article abstracts: A corpus-based study.** Haoran
Zhu, Teng Wang, Nana Pang. *Lingua* 312, 103837, December 2024. doi:10.1016/j.lingua.2024.103837 ·
**verified (record)** · abstract **not read at source** (see `COULD NOT VERIFY`).
**(Q) Before retrieval: yes (content-word ratio). Related to retrieval: no.** It supports "density varies
by discipline", which matters for us only as a confound: density will co-vary with field.

### `brown-2008-cpidr`
**Automatic measurement of propositional idea density from part-of-speech tagging.** Cati Brown, Tony
Snodgrass, Susan J. Kemper, Ruth Herman, Michael A. Covington. *Behavior Research Methods*
40(2):540–545, 2008. doi:10.3758/brm.40.2.540 · PMC2423207 · **verified (record: PubMed)** · **abstract**
(the PMC record is a scanned author manuscript; the methods were not read).

Propositions ≈ verbs, adjectives, adverbs, prepositions and conjunctions after POS tagging, with rules to
merge auxiliaries. Validated on speech/writing samples against human counts.
**(Q) Before retrieval: yes, and cheap (a POS tagger). Related to retrieval: no.** It is the obvious
"cheap linguistic density feature", but it was validated on narrative language in aging research,
**not on scientific prose**. Its validity on methods sections, tables-as-text or chemical names is
unmeasured.

### `harrag-2024-propositional-density`
**Propositional density: cognitive impairment and aging.** Chaimae Harrag, Abdelkader Sabil, Manuel
Célio Conceição, Gabriel A. Radvansky. *Frontiers in Psychology* 15, 2024.
doi:10.3389/fpsyg.2024.1434506 · **verified (page)** · **abstract**.
**(Q) No.** It is a review of the clinical-linguistics use of idea density. Cite it, if at all, only
for the construct's lineage.

### `wang-han-2025-proprag`
**PropRAG: Guiding Retrieval with Beam Search over Proposition Paths.** Jingjin Wang, Jiawei Han. EMNLP
2025 (main), pp. 6224–6239. arXiv 2504.18070. <https://aclanthology.org/2025.emnlp-main.317/> ·
**verified (page)** · **abstract**.
Propositions instead of triples as the knowledge unit; LLM-free beam search over proposition paths;
2Wiki, HotpotQA, MuSiQue.
**(Q) No.** Propositions are the index unit, but no density measure is computed or related to
outcome.

### `zhu-2024-ib-noise-filtering`
**An Information Bottleneck Perspective for Effective Noise Filtering on Retrieval-Augmented
Generation.** Kun Zhu, Xiaocheng Feng, Xiyuan Du, Yuxuan Gu, Weijiang Yu, Haotian Wang, Qianglong
Chen, Zheng Chu, Jingchang Chen, Bing Qin. ACL 2024 (long), 2024.acl-long.59. arXiv 2406.01549 ·
**verified (page)** · **read** (§3, the objective).

Trains a filter (compressor) on retrieved passages with min I(X̃; X|Q) − β I(X̃; Y|Q), approximating
the two terms with LM likelihoods. The IB value is also used as an evaluation score, a data-selection
criterion and an RL reward. 2.5 % compression rate reported.
**(Q) No: the compression is query-conditioned and after retrieval.** It is the formal statement of
"keep what is needed for Y, drop the rest of X", that is, of task-relevant density **after** retrieval.
It offers nothing about chunking the index.

### `jiang-2024-longllmlingua` — see §3.

### `packer-2023-memgpt`, `xu-2025-amem`, `hu-2025-memory-survey`
MemGPT: Charles Packer, Sarah Wooders, Kevin Lin, Vivian Fang, Shishir G. Patil, Ion Stoica, Joseph E.
Gonzalez, arXiv 2310.08560. A-MEM: Wujiang Xu, Zujie Liang, Kai Mei, Hang Gao, Juntao Tan, Yongfeng
Zhang, arXiv 2502.12110 (NeurIPS 2025 per comment). Memory survey: Yuyang Hu, Shichun Liu, Yanwei Yue,
Guibin Zhang, Boyang Liu, Fangyi Zhu, Jiahang Lin, Honglin Guo, Shihan Dou, Zhiheng Xi, et al., arXiv
2512.13564 · all **verified (page)**, metadata only.
**(Q) No.** Agent-memory architecture. Not relevant to index chunking; do not cite in Paper B.

### `levy-jaeger-2007-uid` and `jaeger-2010-redundancy`
**Speakers optimize information density through syntactic reduction.** Roger Levy, T. Florian Jaeger.
*Advances in Neural Information Processing Systems* 19 (NIPS 2006), MIT Press 2007.
<https://proceedings.neurips.cc/paper_files/paper/2006/hash/c6a01432c8138d46ba39957a8250e027-Abstract.html>
· **verified (page; author order read off the PDF; the proceedings metadata lists them alphabetically)** · **abstract**.
**Redundancy and reduction: Speakers manage syntactic information density.** T. Florian Jaeger.
*Cognitive Psychology* 61(1):23–62, 2010. doi:10.1016/j.cogpsych.2010.02.002 · **verified (record:
Crossref)**.
Speakers omit optional words (e.g. *that*) where the following material is predictable, which keeps
surprisal per unit near-uniform.
**(Q) No.** UID is a production hypothesis. Measuring surprisal per token is legitimate, but it
measures how predictable a text is to a language model, not how many retrievable facts it holds. A
formulaic methods paragraph has low surprisal and can still carry a key number. Use with that caveat.

---

## 1. Area (a): propositional or idea density, and claim or proposition extraction, on scientific text or for retrieval

**Finding in one line:** the density measures (CPIDR, lexical density, surprisal) and the
claim-extraction methods (FActScore, Claimify, citance-supervised extraction) exist separately. **No
paper found applies a propositional or idea-density measure to scientific full text as a retrieval
feature.**

### `min-2023-factscore` — already in `03`
Atomic-fact decomposition; ChatGPT 58 % factual precision on biographies. **(Q) No.** It decomposes
generated text **after** generation. Relevant as the established name for "atomic facts" (see
*Established names*).

### `metropolitansky-2025-claimify`
**Towards Effective Extraction and Evaluation of Factual Claims.** Dasha Metropolitansky, Jonathan
Larson. ACL 2025 (main) per the arXiv comment. arXiv 2502.10855 · **verified (page: arXiv)** ·
**abstract**.
Evaluation framework for claim extraction with automated measures of **coverage** and
**decontextualization**; Claimify extracts claims only when the interpretation is unambiguous.
**(Q) Partly.** It measures decontextualisation of *extracted claims*, on LLM outputs, not source
documents, and relates it to fact-checking quality, not retrieval. Its decontextualisation measure is
the closest ready-made instrument for our "independent interpretability" a_i.

### `tan-2026-echoes-of-citations`
**Echoes of Citations: Automated Extraction of Claims from Full Scientific Papers.** Neşet Özkan Tan,
Niket Tandon, Oyvind Tafjord, Michael Witbrock, Peter A. Clark, Mark Gahegan. *Proceedings of the AAAI
Symposium Series* 8(1), 2026. doi:10.1609/aaaiss.v8i1.42589 · **verified (record: OpenAlex)** ·
**abstract**.
Uses citances as weak supervision to extract core claims from full papers. It also "analyse[s] claim
distributions across paper sections".
**(Q) Partly: before retrieval, yes (claims per section); related to retrieval, no.** It is the nearest
published measurement of **where claims sit in scientific full text**, which is what the external
text's "section-level predictions" assert without evidence. Read it before stating any
section-density hypothesis. It is also a second use of citances, the signal behind our unrun Leg C.

### `brown-2008-cpidr`, `harrag-2024-propositional-density`, `rubino-2016-diachronic-id`, `zhu-2024-lingua-lexical-density` — §0.1.

## 2. Area (b): the information bottleneck in RAG or retrieval

### `zhu-2024-ib-noise-filtering` — §0.1. The only peer-reviewed IB-for-RAG paper found.

Three later hits from OpenAlex were **found but not read**, so they are not entries:
"Reliable Industrial Retrieval-Augmented Generation via Information Bottleneck and Entropy-Gradient
Strategy Selection" (IEEE ICIBA 2026); "An Information-Theoretic Framework for Retrieval-Augmented
Generation Systems" (*Electronics* 14, 2025); "Latent Retrieval Augmented Generation" (SIGIR 2026).
**(Q)** for all IB work found: the bottleneck is applied **after** retrieval, to the retrieved context
given the query. **No IB formulation of the indexing (chunking) decision was found.** The external
text's graph-partition objective (EvidenceCoverage − λ·ContextLoss − μ·RetrievalCost) is not an IB
objective, and it does not need to be presented as one.

## 3. Area (c): context compression or pruning for RAG

All of these act **after retrieval and with the query in hand**. They are the field's answer to
"deliver task-relevant density", and every one of them changes C_return, not C_index.

### `jiang-2023-llmlingua`
**LLMLingua: Compressing Prompts for Accelerated Inference of Large Language Models.** Huiqiang Jiang,
Qianhui Wu, Chin-Yew Lin, Yuqing Yang, Lili Qiu. EMNLP 2023 (per arXiv comment). arXiv 2310.05736 ·
**verified (page: arXiv)** · **abstract**. Budget controller plus token-level iterative compression by
small-LM perplexity; up to 20× compression. **(Q) Partly: token surprisal is computed, but to delete
tokens, not to characterise the text for indexing.**

### `jiang-2024-longllmlingua`
**LongLLMLingua: Accelerating and Enhancing LLMs in Long Context Scenarios via Prompt Compression.**
Huiqiang Jiang, Qianhui Wu, Xufang Luo, Dongsheng Li, Chin-Yew Lin, Yuqing Yang, Lili Qiu. ACL 2024
(long), 2024.acl-long.91. arXiv 2310.06839 · **verified (page)** · **abstract**. Question-aware
coarse-to-fine compression plus document reordering; up to +21.4 % on NaturalQuestions at ~4× fewer
tokens. **(Q) No:** density is the motivation ("performance hinges on the density and position of key
information"), not a measured covariate.

### `pan-2024-llmlingua2`
**LLMLingua-2: Data Distillation for Efficient and Faithful Task-Agnostic Prompt Compression.**
Zhuoshi Pan, Qianhui Wu, Huiqiang Jiang, et al. (13 authors). Findings of ACL 2024 (per arXiv comment).
arXiv 2403.12968 · **verified (page: arXiv)** · **abstract**.
**A challenge to surprisal as a density measure.** It argues that "information entropy may be a
suboptimal compression metric", because it is unidirectional and not aligned with the objective, and
replaces it with a learned token classifier. **(Q) No.**

### `xu-2024-recomp`
**RECOMP: Improving Retrieval-Augmented LMs with Compression and Selective Augmentation.** Fangyuan Xu,
Weijia Shi, Eunsol Choi. arXiv 2310.04408 · **verified (page: arXiv)**, venue not checked ·
**abstract**. Extractive (sentence selection) and abstractive compressors; may return the empty string
("selective augmentation"). **(Q) No.**

### `li-2023-selective-context`
**Compressing Context to Enhance Inference Efficiency of Large Language Models** (Selective Context).
Yucheng Li, Bo Dong, Chenghua Lin, Frank Guerin. EMNLP 2023, doi:10.18653/v1/2023.emnlp-main.391.
arXiv 2310.06201 · **verified (page: arXiv; record: OpenAlex)** · **abstract**. Prunes low
self-information units; tested on **arXiv papers**, news and conversations; 50 % context reduction at a
small BERTScore/faithfulness drop. **(Q) Partly:** self-information is computed on the text before use,
and it is the one paper here that tested on scientific papers, but it is used to prune, not to choose
granularity.

### `chirkova-2025-provence`
**Provence: efficient and robust context pruning for retrieval-augmented generation.** Nadezhda
Chirkova, Thibault Formal, Vassilina Nikoulina, Stéphane Clinchant. ICLR 2025 (per arXiv comment).
arXiv 2501.16214 · **verified (page: arXiv)** · **abstract**. Sentence-level pruning as sequence
labelling, unified with reranking; "dynamically detects the needed amount of pruning for a given
context". **(Q) No:** query-conditioned. But it is the strongest existing tool for a **post-hoc**
estimate of how much of a chunk is needed. That makes it a candidate *instrument* for task-relevant
density, not a competitor.

### `wang-2023-filco`
**Learning to Filter Context for Retrieval-Augmented Generation.** Zhiruo Wang, Jun Araki, Zhengbao
Jiang, Md Rizwan Parvez, Graham Neubig. arXiv 2311.08377 · **verified (page: arXiv)**, venue not
checked · **abstract**. Identifies useful context by lexical and information-theoretic (CXMI-style)
measures, then trains filters. **(Q) No.**

### `guo-2025-acc-rag`
**Enhancing RAG Efficiency with Adaptive Context Compression.** Shuyu Guo, Shuo Zhang, Zhaochun Ren.
Findings of EMNLP 2025, doi:10.18653/v1/2025.findings-emnlp.1307 (OpenAlex). arXiv 2507.22931 ·
**verified (page: arXiv; record: OpenAlex)** · **abstract**. Adjusts the compression rate per input
"based on input complexity", "to retain minimal sufficient information". **(Q) Partly:** the
adaptivity is per query and input, after retrieval. It is the closest compression-side analogue of
"choose granularity by need", and it uses the phrase *minimal sufficient information*.

### `dettori-2026-dense-contexts` — **CHALLENGE**
**Dense Contexts Are Hard Contexts: Lexical Density Limits Effective Context in LLMs.** Giovanni
Dettori, Matteo Boffa, Danilo Giordano, Idilio Drago, Marco Mellia. arXiv 2606.06203 (4 June 2026),
preprint · **verified (page: arXiv)** · **read** (definitions, Table 3, §5).
Needle-in-a-haystack at fixed length (~12k tokens) and position, with density varied: models near-perfect
on sparse contexts fall **below 60 %** on dense ones, and lowering density restores performance. Their
"lexical density" is **lexical diversity** (MATTR, MTLD, entropy, Yule's K). That is not the
content-word ratio of #2, a name collision to avoid. §5 states that compression "increase[s] lexical
density" and may therefore hurt.
**(Q) Yes. Density is measured before generation and related to a retrieval-in-context outcome**, but
the outcome is the generator finding a needle, not a retriever finding a passage. **Why it is a
challenge:** maximising evidence per delivered token (our proposed Q3) may *lower* what the generator
uses, so Q3 cannot stand in for Q4. This adds to `jin-2025` and Yu et al. (inverted U, in `05`).

## 4. Area (d): a density or context-dependency measure used to choose chunk size or granularity per document

This is the gap the external text claims. **The claim does not survive intact.** One paper chooses the
chunking **method** per document using, among other things, a coreference-based context-dependency
metric. No paper found chooses chunk **size** per document from a density measure.

### `demoura-2026` — already in `04`; **CHALLENGE (pre-emption, partial)**
Re-read for this question (full PDF, §2.3.1, §3.5, Table 5, Limitations).
**References Completeness (RC)** = 1 − (fraction of entity–pronoun pairs, from the Maverick coreference
model, that a chunk boundary splits). It is computed **before retrieval**, per document and per
candidate chunking. Together with four other intrinsic metrics it **selects a chunker per document**,
and the selection is related to retrieval and answer outcomes (Table 5).
**(Q) Yes, for method selection, which makes this the nearest prior work by a distance.** What it does
*not* do, and what remains open for us:
1. It selects a **method**, with size **bounded** (100–1,100 tokens), not chosen.
2. RC measures whether a *given* chunking breaks references. It is not a property of the document
   alone, such as "x % of sentences open with a back-reference", which could set the size **before**
   any chunking is run.
3. It is not budget-matched: top-10 passages of differing size.
4. It is small and machine-judged (33 documents, 99 GPT-4.1 questions, GPT-4.1 judge); only Retrieval
   Completeness was significance-tested.
5. Each metric's contribution is not ablated against the outcome in what was read here.

**Correction for `04`:** add the corpus size and the Table 5 reading of the 72 % figure (§0, #6).

### `zhong-2025-mog` — already in `04`
Re-read for this question (§3). The router picks granularity **per query**, from the query, trained on
soft labels from TF-IDF/RoBERTa relevance. **(Q) No:** no text-density or dependency feature of the
document is used.

### `bhat-2025` — already in `04` and `05`
Optimal fixed size differs by **dataset** (64–128 tokens for short factual answers; 512–1,024 for
descriptive ones). **(Q) No:** the driver named is the **answer type** (a query/task property), and no
text measure is computed. It is still the best published evidence that the optimum is population-dependent.

### `zhao-2024-metachunking` — already in `04`
Perplexity-based boundary placement with dynamic merging. **(Q) Partly:** LM surprisal is computed on
the text before retrieval, but to place **boundaries**, not to set a size or to relate density to
outcome per document.

### `joren-2024-sufficient-context`
**Sufficient Context: A New Lens on Retrieval Augmented Generation Systems.** Hailey Joren, Jianyi
Zhang, Chun-Sung Ferng, Da-Cheng Juan, Ankur Taly, Cyrus Rashtchian. arXiv 2411.06037 ·
**verified (page: arXiv)**, venue not checked · **abstract**.
Defines **sufficient context** (does the context contain enough to answer?) and an autorater for it;
stratifies RAG errors by it.
**(Q) No:** sufficiency is judged on (query, context) after retrieval. It is the field's name for the
external text's "sufficient evidence" predicate (see *Established names*).

### `min-2018-minimal-context`
**Efficient and Robust Question Answering from Minimal Context over Documents.** Sewon Min, Victor
Zhong, Richard Socher, Caiming Xiong. ACL 2018 (long, per arXiv comment). arXiv 1805.08092 ·
**verified (page: arXiv)** · **abstract**.
"Most questions in existing datasets can be answered with a small set of sentences"; it trains a
selector for the **minimal set of sentences**.
**(Q) No, but it is the precedent for B_min.** The external text's minimum sufficient evidence budget
is Min et al.'s *minimal context*, measured in tokens. Its finding is on SQuAD-style data where answers
sit in one sentence. Whether full scientific articles behave the same is exactly what our pointed
questions test.

## 5. Area (e): coreference or "context dependency" of sentences as a retrieval feature

### `choi-2021-decontextualization`
**Decontextualization: Making Sentences Stand-Alone.** Eunsol Choi, Jennimaria Palomaki, Matthew Lamm,
Tom Kwiatkowski, Dipanjan Das, Michael Collins. *TACL* 9:447–461, 2021. doi:10.1162/tacl_a_00377.
arXiv 2102.05169 · **verified (page: arXiv; venue and pages read off the PDF)** · **read** (§3, Table 3,
§6).
Defines sentence decontextualisation (rewrite so the sentence is interpretable out of context while
preserving its truth-conditional meaning). Each sentence is labelled FEASIBLE (with edits), UNNECESSARY
(stands alone as is) or INFEASIBLE. On their **filtered** Wikipedia sample (test set): 68 % feasible
with edits, 20 % already stand-alone, 12 % infeasible. The selection deliberately excluded hard cases,
so these are not population rates. A pilot found **"encyclopedic text is substantially easier to
decontextualize compared to newswire or literary text"**, and the document title is crucial. It
demonstrates decontextualised sentences as a retrieval corpus for open-domain QA.
**(Q) Partly. It measures context dependency per sentence before retrieval (the feasibility label), but
it uses the label to rewrite, not to choose granularity, and it reports no relation between dependency
rate and retrieval outcome.** It is the established name and annotation scheme for the external text's
a_i.

### `newman-2023-scientific-decontext`
**A Question Answering Framework for Decontextualizing User-facing Snippets from Scientific
Documents.** Benjamin Newman, Luca Soldaini, Raymond Fok, Arman Cohan, Kyle Lo. EMNLP 2023 (per arXiv
comment). arXiv 2305.14772 · **verified (page: arXiv)** · **abstract**.
Decontextualisation **on scientific papers**, via question generation → question answering → rewriting;
gold decontextualisations by experienced scientific readers; "question generation and answering remain
challenging".
**(Q) No outcome relation**, but it is the in-domain precedent. It shows that what a scientific snippet
lacks includes **references to other documents**, which a within-document coreference count (as in
`demoura-2026`'s RC) does not see.

### `gunjal-durrett-2024-molecular-facts`
**Molecular Facts: Desiderata for Decontextualization in LLM Fact Verification.** Anisha Gunjal, Greg
Durrett. Findings of EMNLP 2024, pp. 3751–3768 (arXiv journal-ref). arXiv 2406.20079 ·
**verified (page: arXiv)** · **abstract**.
Two criteria: **decontextuality** (how well a unit stands alone) and **minimality** (how little is
added to achieve it). Fully atomic facts lose context; molecular facts balance the two.
**(Q) Partly: it is about verification units, not retrieval chunks.** But it is the **established
statement of the trade-off** the external text frames as density vs contextual dependency: atomic units
are dense and context-poor; adding context restores interpretability at a token cost. Our reach vs
containment trade-off is the retrieval-side shadow of the same tension.

### `kane-schubert-2023-gist`
**Get the gist? Using large language models for few-shot decontextualization.** Benjamin Kane,
Lenhart Schubert. arXiv 2310.06254 · **verified (page: arXiv)**, no venue · **abstract**. Few-shot LLM
decontextualisation across domains. **(Q) No.** A cheaper instrument than Choi's fine-tuned model.

### `grzywinski-2023-craqan`
**Releasing the CRaQAn (Coreference Resolution in Question-Answering) …** Rob Grzywinski et al.
NeurIPS 2023 Workshop on Instruction Tuning and Instruction Following (per arXiv comment). arXiv
2311.16338 · **verified (page: arXiv)** · **abstract**. 250+ QA pairs whose answers require resolving
coreference across chunk boundaries. **(Q) No measure-outcome relation**, but it names the failure
directly: "the resolution of coreferences in the context of chunking strategies for long documents".

### `chen-2024-densex` — `04`. Its propositions are, by construction, decontextualised units. That is
the one place where decontextualisation and granularity already meet in retrieval.

---

## CHALLENGES

Work that contradicts us, pre-empts us, or limits what the external text proposes:

1. **`demoura-2026` pre-empts "use contextual dependency to choose chunking per document"** for
   *method* choice. Any proposal must say it chooses **size**, from a **document** property computed
   **before** chunking, at a **matched budget**, behind a **reranker**, with **human** validation.
   Otherwise it is their paper again.
2. **The headline number of that pre-emption is mislabelled in its own abstract** (§0, #6). Cite
   Table 5, never the abstract.
3. **`dettori-2026`: denser delivered context can hurt the generator.** Maximising evidence per
   delivered token (Q3) is therefore not monotone in answer quality. Together with `jin-2025` / Yu et
   al. in `05`, Q3 must not be read as a proxy for Q4.
4. **`pan-2024-llmlingua2`: surprisal is a weak density measure**, because it is unidirectional and
   not aligned with the objective. This weakens the external text's first density measure (mean
   −log₂ P per token) as a predictor of what is retrievable.
5. **`choi-2021`: dependency is genre-dependent and partly infeasible to remove**, and their rates come
   from a filtered encyclopedic sample. Scientific prose is untested there, and `newman-2023` shows
   that cross-document references are a dependency type within-document coreference cannot see.
6. **`bhat-2025`: the optimum chunk size is already known to vary by dataset**, attributed to answer
   type. A density effect must be shown to add to the answer-type (query) effect, not to restate it.
7. **CPIDR (`brown-2008`) was validated on narrative language in aging research.** Using it on
   scientific full text would be a new, unvalidated use, not a borrowed measure.

## Searched and did not find

All run 2026-10-13. OpenAlex `filter=title_and_abstract.search:…` over 2015–2026 unless stated. A
query listed here returned nothing relevant to *our question*. Any partial hits are named.

**(a) density on scientific text / for retrieval**
- `"propositional density" retrieval`: 2 hits, both aphasia.
- `"idea density" retrieval`: 4 hits, memory/aphasia.
- `"idea density" scientific`: 3 hits, bibliometrics; none on text.
- `"information density" scientific articles sections`: 3 hits, none measuring density by section
  for retrieval.
- `proposition extraction scientific articles retrieval`: 0.
- `claim extraction scientific papers`: 108; top hits read by title. `tan-2026` kept; the rest are
  extraction tools without a density or retrieval relation.
- OpenAlex `search=` "Information Density in Scientific Writing: A Diachronic Perspective": no such
  work.

**(b) information bottleneck**
- `"information bottleneck" retrieval-augmented generation`: 16; only `zhu-2024` is peer-reviewed and
  read. Three others are named in §2 as unread.
- `"information bottleneck" passage retrieval`: 1 (`zhu-2024`).

**(c) compression.** Located by known identifiers, not by search. The area is well mapped; the gap is
not here.

**(d) density/dependency → granularity**
- `"information density" chunking`: 39; top 8 read by title. None selects size per document by
  density. One is a Zenodo deposit ("Dynamic Semantic Chunking for Optimal Context Window Utilization")
  without peer review, not opened.
- `"chunk size" "information density"`: 3; none relevant (RTL code completion; the Zenodo deposit; a
  synthetic-data paper).
- `chunk size adaptive per document density`: 0.
- `"optimal chunk size" document characteristics`: 0.
- `"adaptive chunk size"`: 8; all systems/networking, none text.
- `"adaptive chunking" retrieval`: 32; top 8 read by title. `demoura-2026` is the only peer-reviewed
  per-document selector; others are Zenodo deposits or query-aware variants in small venues, not
  opened.
- `chunking strategy selection per document`: 8; none.
- `retrieval granularity per query router`: 5; none beyond MoG.
- `"semantic density" chunking`: 2; one LNCS paper on boundary smoothing, not opened.
- `text complexity chunk size retrieval`: 7; none.
- OpenAlex `search=` (relevance) "adaptive chunk size information density retrieval augmented
  generation" and "document-adaptive chunking granularity selection per document RAG": no paper
  choosing size by density.
- One WebSearch ("chunk size chosen per document from information density or coreference context
  dependency retrieval-augmented generation arXiv"): returned `demoura-2026`, `guo-2025-acc-rag` and
  2607.24767 (Garrido-Lestache Belinchon, a fixed-size sweep with no text measure). None chooses
  size by density.
- arXiv API, **all failed (HTTP 503 or timeout; even the title query for a paper known to exist
  returned nothing), not run:** `abs:"information density" AND abs:chunk*`;
  `abs:"chunk size" AND abs:adaptive AND abs:retrieval`; `ti:"mix-of-granularity"`;
  `abs:"retrieval granularity" AND abs:document AND abs:adaptive`; `abs:"dynamic chunk" AND
  abs:retrieval`. Re-run these before any paper claims the gap.

**(e) context dependency**
- `"context dependency" chunk retrieval`: 0.
- `anaphora chunk boundary retrieval`: 0.
- `coreference chunking retrieval-augmented`: 5; CRaQAn kept, the rest KG construction or
  conversational systems.
- `decontextualization dense retrieval passages`: 0.
- `decontextualization retrieval`: 54; top 8 read by title. Kane & Schubert kept; the rest are psychology
  or conversational QA.
- `"contextual retrieval" chunk context prepend`: 1, a thesis in Hungarian.
- `"lexical density" retrieval`: 13. `dettori-2026` kept. "Asymmetric cross-source retrieval transfer
  … corpus semantic richness" (*Sci Rep* 2026; Liu, Chen, Chen) was read at abstract: it characterises
  **corpus** richness (MATTR, MTLD, hapax) for **embedding fine-tuning data**, not chunking. Not an entry.

**Coverage statement.** No paper found **chooses chunk size per document from a pre-chunking density or
context-dependency measure and relates that choice to a retrieval outcome at a matched budget.** That
rests on ~35 queries, with the arXiv API leg missing. Treat it as "not found by this search", not as
"does not exist".

## Established names

| the external text / we would say | the field already says | source |
|---|---|---|
| "independent interpretability" a_i of a unit | **decontextualization**; per-sentence **feasibility** (FEASIBLE / UNNECESSARY / INFEASIBLE); **decontextuality** | `choi-2021-decontextualization`; `gunjal-durrett-2024-molecular-facts` |
| "contextual dependency" (of a sentence or chunk) | the inverse of decontextualizability. For the boundary-specific case, `demoura-2026`'s **References Completeness** | same; `demoura-2026` |
| "minimum sufficient evidence budget" B_min(q,d) | **minimal context** (minimal set of sentences that answers); **rationale** = "a minimal collection of sentences … sufficient"; **sufficient context**; ERASER's **sufficiency**; "minimal sufficient information" | `min-2018-minimal-context`; `wadden-2020-scifact` (in `03`, "A rationale is a minimal collection of sentences which, taken together … can reasonably be judged by a domain expert as implying the claim"); `joren-2024-sufficient-context`; `deyoung-2020-eraser` (in `03`); `guo-2025-acc-rag` |
| "Scientific Information Density" (units / tokens) | **atomic facts** (FActScore), **propositions** (Dense X), **nuggets** (TREC). Per token: **propositional idea density** (CPIDR) | `min-2023-factscore` (`03`); `chen-2024-densex` (`04`); CPIDR |
| "task-relevant density" | **evidence per delivered token at a budget**: `EM @ l tokens` and "recall within the initial l retrieved words" | `chen-2024-densex`; our naming decision in `docs/papers/README.md` |
| density vs dependency trade-off of the unit | **decontextuality vs minimality** (molecular facts) | `gunjal-durrett-2024-molecular-facts` |
| "lexical density" | **ambiguous; specify which.** Content-word ratio (Lingua 2024) ≠ lexical diversity (MATTR/MTLD, `dettori-2026` calls it "lexical density") | §0 #2; §3 |
| "uniform information density" as a text measure | a **speaker-production hypothesis** (UID); the measure is **surprisal** | `levy-jaeger-2007-uid`, `jaeger-2010-redundancy` |

**Adopt, do not coin:** use *decontextualization* (and the Choi feasibility labels) for a_i, and
*minimal sufficient context* for B_min, citing `min-2018` and `wadden-2020`. Do **not** introduce
"RID" or "SID" as new metric names. If a combined quantity is needed, describe it as
"decontextualizable evidence per delivered token", a composite of named parts.

## COULD NOT VERIFY

- **#2's abstract at source.** Metadata verified (Crossref, Semantic Scholar, OpenAlex). The
  ScienceDirect page is a bot wall, and no metadata service carries the abstract. The 16-discipline,
  rising-density and hard > soft claims were read only in a search-engine summary of the abstract.
  Fetch the publisher abstract before citing any of them.
- **The classic definition of lexical density** (content words / total words, usually attributed to
  Ure 1971 or Halliday). Not fetched; cite only after verifying.
- **CPIDR's validation corpus and methods.** The PMC full text is a scanned manuscript, and the Europe
  PMC PDF endpoint returned HTML. Only the abstract was read.
- **Venues for RECOMP, FILCO and Sufficient Context** were not checked. They are cited above by arXiv id
  only.
- **The arXiv API searches** listed above did not run (HTTP 503/timeouts).
