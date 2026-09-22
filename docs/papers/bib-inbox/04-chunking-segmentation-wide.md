# Bib inbox 04 — Chunking and segmentation for retrieval, wide survey

Companion to the five entries already in `docs/papers/bibliography.md` (`qu-2025`, `wang-2025-pic`,
`zhao-2025-moc`, `kreileder-2026`, `allamraju-2025`). Those are not repeated here; their forward and
backward citation graphs were walked and the results are below.

**Verification rule applied.** Every entry below was fetched. "verified" means the exact title,
full author list, venue and year were confirmed against the publisher's own record — ACL Anthology,
the arXiv API (`export.arxiv.org`, arXiv's own metadata service), the AAAI OJS page, PubMed, or the
publisher's article page. "read" means the full text was retrieved as PDF and the methods section
checked. Where only metadata could be obtained, the entry says so in its status line and the
attempts are recorded in § 4.

**Four questions asked of every paper**, recorded in the `Size` / `Corpus` / `Stage` block:

- **Size** — does it report *realised* chunk lengths, or only the nominal/requested size, and does
  it hold chunk size constant across the methods it compares?
- **Corpus** — what documents, and how long are they? (A corpus whose documents are shorter than
  the largest chunk size tested cannot test that size.)
- **Stage** — first-stage retrieval alone, behind a reranker, or end-to-end generation?

---

## 1. Late chunking and contextualised chunk embeddings

### `gunther-2024` — Late Chunking: Contextual Chunk Embeddings Using Long-Context Embedding Models
Michael Günther, Isabelle Mohr, Daniel James Williams, Bo Wang, Han Xiao. arXiv 2409.04701,
v1 7 September 2024, v3 7 July 2025. <https://arxiv.org/abs/2409.04701> · **verified, read (full PDF)**

Embeds the whole document with a long-context model, then applies chunk boundaries to the token
embeddings and mean-pools per chunk. No training required. Evaluated on four small BEIR datasets
(SciFact, NFCorpus, FiQA, TREC-COVID) with three embedding models and three boundary strategies
(fixed 256 tokens; 5 sentences; LlamaIndex semantic splitter at defaults).

**The reported effect is small and the paper is honest about it**: averaged over three models and
four datasets, +3.63% relative (1.9 points absolute) for sentence boundaries, +3.46% for fixed-size,
+2.70% for semantic. Section 4.2 sweeps chunk size on NFCorpus and LongEmbed and finds late chunking
helps mainly *at small chunk sizes*, with naive chunking winning at large sizes on some reading-
comprehension tasks — i.e. the method's advantage is itself a function of chunk size. Section 4.5's
comparison against Anthropic's contextual embedding is **one fictional financial document and one
query**, scored by cosine similarity; it is an illustration, not an evaluation.

**Size**: the three boundary strategies are *not* size-matched (256 tokens vs 5 sentences vs a
similarity threshold) and no realised chunk-length statistics are reported. The size sweep in §4.2
uses fixed-size boundaries only, where nominal equals realised by construction.
**Corpus**: the authors say so themselves — "most retrieval tasks contain relatively short texts",
so they take NFCorpus as the only BEIR set with "comparable long text documents", and go to
LongEmbed for anything longer, truncating at 8,192 tokens. This is the corpus-adequacy problem
stated by the people who ran into it.
**Stage**: first-stage retrieval only; chunk ranking converted to document ranking by first
occurrence. No reranker.

**Weight: moderate, and cite it for the mechanism, not the magnitude.** Note for the paper: this is
**not peer-reviewed** — it is an arXiv preprint whose own comments field reads "11 pages, 3rd
draft", despite being the canonical reference for late chunking. Its central claims have since been
independently reproduced (`zhou-2026`) and independently failed to replicate at scale
(`caspari-2026`), and both of those are the better citations for whether it works.

### `conti-2025` — Context is Gold to find the Gold Passage: Evaluating and Training Contextual Document Embeddings
Max Conti, Manuel Faysse, Gautier Viaud, Antoine Bosselut, Céline Hudelot, Pierre Colombo.
arXiv 2505.24782, v1 30 May 2025 (v2 6 June 2025, comments field: "Under Review").
<https://arxiv.org/abs/2505.24782> · **verified, read (full PDF)**

Introduces ConTEB, a benchmark of retrieval tasks that *require* document-wide context, and InSeNT,
a contrastive post-training objective used together with late-chunking pooling. Shows standard
embedding models fail when the gold chunk is not self-contained.

**The ablation that matters to us is §6, "Robustness to chunking."** They take self-contained SQuAD
chunks and split them progressively into smaller sub-chunks, tracking the gold answer span, and plot
retrieval quality against chunk size. Contextually-trained embeddings give "a much more uniform
retrieval performance across a wide range of chunk sizes" — that is, *the chunk-size response curve
is flattened by the representation, not by the segmentation method*.

**Size**: chunk size is swept as an explicit independent variable (by recursive sub-splitting), which
is the right instinct; realised lengths are not tabulated and no cross-*method* size matching is
attempted, because the paper compares representations, not segmenters.
**Corpus**: ConTEB is assembled from several sources; the chunking ablation uses SQuAD (Wikipedia
paragraphs — short). The corpus-size scaling study uses templated near-duplicate documents.
**Stage**: first-stage retrieval, nDCG@10. No reranker.

**Weight: strong for one specific use** — it is direct evidence that *how much chunk size matters
depends on the embedding model*, which complicates any single-model claim about size, ours included.
Cite it in the limitations, not as support.

### `caspari-2026` — When Is Complex Chunking Worth It? A Multi-Objective Evaluation of Chunking Methods at Scale
Laura Caspari, Kanishka Ghosh Dastidar, Michael Dinzinger, Jelena Mitrović, Michael Granitzer.
arXiv 2608.16586, 17 August 2026; the paper states "accepted at the ACM CIKM Conference on Knowledge
and Information Management (CIKM'26)". <https://arxiv.org/abs/2608.16586> · **verified, read (full PDF)**

Eight chunking strategies — token, sentence, late, enriched(title), enriched(summary), contextual
(Anthropic's method), summary-only, semantic — across two scalable corpora (CoRE, derived from
MS MARCO v2; KILT with NQ qrels) at 10K/100K/1M/6–10M documents, three embedding models, with
Fisher randomisation tests (10,000 permutations, Bonferroni over 28 pairwise comparisons) reported
as significant-win rates. Also measures indexing throughput, query throughput and peak memory.

**Result: expensive methods rarely win.** "Semantic chunking, summary-only indexing, contextual
chunking, and late chunking rarely achieve significant wins over simpler baselines." The best
method varies by model, dataset, corpus size and metric. The one partial exception is enriched
(summary) under nDCG@10. Number of significant differences grows with corpus size.

**Size**: the strongest nominal control in this survey — "For token, sentence, late, enriched, and
contextual chunking, we use 512-token chunks with an overlap of 25 tokens before adding optional
metadata or generated context." Six of eight methods share a nominal window. **But**: the phrase
"before adding" is load-bearing. Enriched and contextual chunking prepend a title, a summary or
LLM-generated context, so their *realised* chunk length is 512 + extra — and the methods that win
under nDCG@10 are exactly the ones carrying extra text. No realised-length statistics are reported
anywhere, so this confound is not addressable from the paper. Semantic (95th-percentile threshold)
and summary-only (one vector per document) are not size-matched at all.
**Corpus**: MS MARCO v2 web documents and KILT Wikipedia; document-length statistics are not
reported, which matters given they chunk at 512 tokens.
**Stage**: both — nDCG@10 for single-stage ranking and Recall@100 explicitly framed as "first-stage
retrieval performance when a downstream reranker or generator may consume a larger candidate set".
No reranker is actually run. The ranking of methods *changes between the two metrics*: token and
sentence chunking become much more competitive at Recall@100.

**Weight: very strong.** This is the peer-reviewed, at-scale evaluation of Anthropic's contextual
retrieval that did not previously exist, and the answer is that it does not reliably beat a 512-token
window. It is also the cleanest published demonstration that *the chunking method you should pick
depends on whether a reranker follows* — which is exactly the reranker question we ask.

---

## 2. Anthropic's contextual retrieval

### `anthropic-2024` — Introducing Contextual Retrieval
Anthropic (no named authors). Engineering blog post, 19 September 2024.
<https://www.anthropic.com/engineering/contextual-retrieval> · **verified (fetched), read**

Prepends chunk-specific LLM-generated context to each chunk before embedding ("Contextual
Embeddings") and before BM25 indexing ("Contextual BM25"). Headline numbers, metric is 1 − recall@20:
Contextual Embeddings cut the top-20 failure rate by 35% (5.7% → 3.7%); with Contextual BM25, by 49%
(5.7% → 2.9%); adding a Cohere reranker, by 67% (5.7% → 1.9%).

**Size**: "usually no more than a few hundred tokens" is the entire specification. No chunk sizes
are given, no realised lengths, no size comparison. Note the mechanism *necessarily increases
realised chunk length* — every chunk gets 50–100 extra tokens of context — and nothing in the post
separates that from the semantic effect of the added context.
**Corpus**: "codebases, fiction, ArXiv papers, Science Papers". No document-length statistics.
**Stage**: both; the reranked configuration is reported separately and is the largest effect
(35% → 67% when the reranker is added), i.e. **more than half of the published improvement comes
from the reranker, not from the contextualisation**.

**Weight: not citable as evidence, citable as the origin of a widely-deployed practice.** It is a
blog post with no released evaluation set, no baselines at matched chunk size, and no variance.
The peer-reviewed evaluations of it are `caspari-2026` (it does not reliably win at scale) and, at
the level of a single worked example, `gunther-2024` §4.5. Any claim we make about contextual
retrieval should be sourced to those, with the blog cited only for the method definition.

---

## 3. LLM-driven and learned dynamic segmentation

### `duarte-2024` — LumberChunker: Long-Form Narrative Document Segmentation
André V. Duarte, João DS Marques, Miguel Graça, Miguel Freire, Lei Li, Arlindo L. Oliveira.
Findings of the ACL: EMNLP 2024, pages 6473–6486. DOI 10.18653/v1/2024.findings-emnlp.377.
<https://aclanthology.org/2024.findings-emnlp.377/> · **verified, read (full PDF)**

Splits a document into paragraphs, concatenates them into groups until a token threshold θ, and asks
an LLM (Gemini 1.0-Pro) to name the paragraph where content shifts. Introduces GutenQA: 100 Project
Gutenberg books, 3,000 generated QA pairs, ground truth = a verbatim answer span, so a chunk counts
as correct iff it contains the substring. Beats the best baseline by 7.37% DCG@20.

**This paper is the one place in the pre-2026 literature that documents the nominal-versus-realised
gap explicitly, in its own method.** Appendix F, Table 10 gives realised average tokens per chunk:
Semantic 185, Paragraph 79, Recursive 399, Proposition 12, LumberChunker 334, and the text states
that LumberChunker's realised average "is approximately 40% below the intended input size of 550
tokens". They also sweep θ ∈ {450, 550, 650, 1000} and find 550 best — a within-method size sweep
that changes realised size.

**Size**: realised mean lengths **are reported per method** (rare and creditable) and are **not
matched** — they span 12 to 399 tokens across the compared methods. The winning method sits at 334
tokens against a 399-token recursive runner-up, so size alone does not explain the win; but the
evaluation metric (does the retrieved chunk contain the gold substring) is monotonically easier for
longer chunks, which the paper does not control for.
**Corpus**: 100 full narrative books — genuinely long, among the best corpora in this survey.
Note the documents are dialogue-heavy, which the authors invoke to explain why paragraph and
semantic chunks come out so short.
**Stage**: first-stage retrieval with `text-embedding-ada-002`; a separate 280-question generation
study on four autobiographies. No reranker.

**Weight: strong, and the best available precedent for reporting realised sizes.** Cite Table 10
directly: a method asked for 550 tokens delivered 334. The reproduction in `zhou-2026` confirms the
GutenQA result and simultaneously shows it does not transfer to in-corpus retrieval.

### `zhao-2024-metachunking` — Meta-Chunking: Learning Text Segmentation and Semantic Completion via Logical Perception
Jihao Zhao, Zhiyuan Ji, Yuchen Feng, Pengnian Qi, Simin Niu, Bo Tang, Feiyu Xiong, Zhiyu Li.
arXiv 2410.12788, v1 16 October 2024, v3 21 May 2025. <https://arxiv.org/abs/2410.12788> ·
**verified (arXiv record), abstract and related-work only — not read in full**

Perplexity Chunking and Margin Sampling Chunking: use an LLM's token probabilities or a binary
split/no-split margin to place boundaries, plus dynamic merging and a hierarchical-summary "global
information compensation" step. Predecessor of `zhao-2025-moc` from the same group. Reported example:
+1.32 over similarity chunking on 2WikiMultihopQA at 45.8% of the time.

**Weight: listed for completeness of the LLM-segmentation family; do not cite a number from it
without reading the full paper.** The title changed between arXiv versions (v1 "Learning Efficient
Text Segmentation"; current "Learning Text Segmentation and Semantic Completion"), so any citation
must pin a version. No peer-reviewed venue is recorded on the arXiv entry.

### `zhong-2025-mog` — Mix-of-Granularity: Optimize the Chunking Granularity for Retrieval-Augmented Generation
Zijie Zhong, Hanwen Liu, Xiaoya Cui, Xiaofan Zhang, Zengchang Qin. COLING 2025 (per the arXiv
comments field: "COLING 2025 conference paper"). arXiv 2406.00456, v1 1 June 2024, v2 26 January 2025.
<https://arxiv.org/abs/2406.00456> · **verified (arXiv record), not read in full**

Pre-segments each document at several granularities and trains a router to pick the granularity per
query. Relevant because it treats granularity as a *per-query* variable rather than a corpus-level
constant — which is a different answer to the same confound.

**Weight: worth citing as prior art for "granularity is the variable, not the method", but read it
before quoting results.**

### `liu-2025-lgmgc` — Passage Segmentation of Documents for Extractive Question Answering
Zuhong Liu, Charles-Elie Simon, Fabien Caspani. ECIR 2025, Lecture Notes in Computer Science,
pages 345–352. DOI 10.1007/978-3-031-88714-7_33; preprint arXiv 2501.09940, 17 January 2025.
<https://arxiv.org/abs/2501.09940> · **verified (arXiv record + OpenAlex/DOI for the ECIR version),
not read in full**

The Logits-Guided Multi-Granular Chunker (LGMGC): LLM-logit-guided boundary detection combined with
recursive subdivision of parent chunks into multiple granularity levels, all ranked against the
query. Peer-reviewed (ECIR short paper).

**Weight: moderate; one of the few genuinely peer-reviewed LLM-chunker papers. Read before citing
results.**

### `lu-2025-hichunk` — HiChunk: Evaluating and Enhancing Retrieval-Augmented Generation with Hierarchical Chunking
Wensheng Lu, Keyu Chen, Ruizhi Qiao, Xing Sun. arXiv 2509.11552, v1 15 September 2025, v3
9 October 2025. <https://arxiv.org/abs/2509.11552> · **verified (arXiv record), read (full PDF,
methods and experimental setup)**

A fine-tuned Qwen3-4B places hierarchical chunk boundaries; an Auto-Merge retrieval algorithm
assembles a query-conditioned context under a token budget. Introduces HiCBench, a benchmark with
manually annotated hierarchical structure and evidence-dense QA. Trains on the WIKI-727K family
(`koshorek-2018`) among others.

**The sentence to cite is in their experimental setup**: "Due to the varying sizes of chunks
resulting from semantic-based chunking, we limit the length of the retrieved context based on the
number of tokens rather than the number of chunks for a fair comparison." They cap retrieved context
at 4,096 tokens and additionally compare methods at 2k/2.5k/3k/3.5k/4k budgets, concluding "it is
necessary to compare different chunking methods under the same retrieval token budget".

**Size**: this is **budget control, not chunk-size control** — the total delivered text is equalised
across methods while the per-chunk realised length is left free and unreported. It is nonetheless an
explicit, quotable recognition that comparing chunkers at fixed *k* is unfair because their chunks
differ in size.
**Corpus**: HiCBench plus LongBench, Qasper, GutenQA, OHRBench — long documents throughout.
**Stage**: retrieval (evidence recall) and end-to-end generation with Llama3.1-8B, Qwen3-8B,
Qwen3-32B; bge-m3 as retriever. No reranker.

**Weight: strong for the fair-comparison argument.** It shows the field has already internalised
*budget* control and has still not taken the next step to *size* control.

---

## 4. Propositions and the fine-granularity end

### `chen-2024-densex` — Dense X Retrieval: What Retrieval Granularity Should We Use?
Tong Chen, Hongwei Wang, Sihao Chen, Wenhao Yu, Kaixin Ma, Xinran Zhao, Hongming Zhang, Dong Yu.
EMNLP 2024, pages 15159–15177. DOI 10.18653/v1/2024.emnlp-main.845.
<https://aclanthology.org/2024.emnlp-main.845/> · **verified, read (full PDF)**

Indexes Wikipedia three ways — 100-word passages, sentences, propositions (atomic, decontextualised
facts produced by a trained propositioniser) — releasing FACTOIDWIKI, and compares six retrievers
plus downstream QA with LLaMA-2-7B.

**This paper does control a size-like variable, and it is worth being precise about which one.**
Table 1 reports realised average unit lengths: passages 58.5 words, sentences 21.0, propositions
11.2. Retrieval is then compared two ways: at fixed *k* (Recall@5/@20), and — the honest comparison —
at a **fixed retrieved-word budget**, with the finding that "for a fixed word retrieval budget,
proposition retrieval shows a higher success rate than sentence and passage retrieval", the gap
peaking at 100–200 words, "roughly 10 propositions, 5 sentences, or 2 passages", and closing as the
budget grows. Downstream QA is scored as EM@l with l ∈ {100, 500} tokens.

**Size**: realised unit lengths **are reported**, and the *delivered* quantity is equalised across
granularities. What is *not* done — and could not be, given the design — is holding the retrieval
unit's own length constant while varying the method that produced it. The comparison is between
granularities, so granularity is the treatment.
**Corpus**: English Wikipedia, 2021-10-13 dump, pre-segmented into 100-word passages. **The largest
unit ever tested is 100 words**, so this corpus cannot say anything about chunks above ~130 tokens.
**Stage**: first-stage dense retrieval and end-to-end QA. No reranker.

**Weight: strong, and an important precedent for the *budget-matched* comparison.** Its
fine-granularity conclusion has since been reversed for in-corpus retrieval: `zhou-2026` finds
proposition chunking the worst of six methods on BEIR (15–27% below paragraph chunking) and
attributes it to lost topical context, and `smigielski-2026` finds DenseX bottom of the table on
average. Cite Dense X for the budget-matching method, and cite the 2026 reproductions for whether
propositions actually work.

---

## 5. Hierarchical and recursive summarisation indexes

### `sarthi-2024-raptor` — RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval
Parth Sarthi, Salman Abdullah, Aditi Tuli, Shubh Khanna, Anna Goldie, Christopher Manning.
ICLR 2024. <https://proceedings.iclr.cc/paper_files/paper/2024/hash/8a2acd174940dbca361a6398a4f9df91-Abstract-Conference.html>
· **verified, read (full PDF, arXiv 2401.18059)**

Recursively embeds, clusters and summarises 100-token leaf chunks into a tree, then retrieves from
the collapsed tree — leaves and summaries compete in one flat index. +20% absolute on QuALITY over
the previous best.

**RAPTOR controls the delivered token budget and says so**: retrieval adds nodes "until you reach a
predefined maximum number of tokens"; the main results use a collapsed tree with 2,000 maximum tokens
(≈ top-20 nodes), 400 tokens for the UnifiedQA reader, and — the sentence to cite — "**We provide
the same amount of tokens of context to RAPTOR and to the baselines.**"

**Size**: leaves are a nominal 100 tokens with a sentence-preserving rule (so realised ≤ nominal,
unreported); summary-node lengths are not reported at all, and they are the units RAPTOR adds over
the baselines. The delivered budget is matched; the unit-length distribution is not, and the whole
point of the method is that the unit lengths differ.
**Corpus**: NarrativeQA (full books and film scripts), QASPER (full NLP papers), QuALITY
(medium-length passages). Genuinely long documents.
**Stage**: retrieval feeding a reader (UnifiedQA-3B, GPT-3, GPT-4); F1/accuracy, no reranker.

**Weight: strong**, and the second clear precedent for budget matching. It is also a warning: a
hierarchical index changes the realised length distribution of the retrievable units by design, so
"method" and "size" are fused in it, not merely correlated.

---

## 6. Parent-document retrieval, small-to-big, sentence-window

This is the thinnest evidence base in the entire survey. The pattern is ubiquitous in LlamaIndex and
LangChain documentation and essentially absent from the refereed literature. The only peer-reviewed
evaluation found is a two-row table inside a larger paper:

### `wang-2024-bestpractices` — Searching for Best Practices in Retrieval-Augmented Generation
Xiaohua Wang, Zhenghua Wang, Xuan Gao, Feiran Zhang, Yixin Wu, Zhibo Xu, Tianyuan Shi, Zhengyuan
Wang, Shizheng Li, Qi Qian, Ruicheng Yin, Changze Lv, Xiaoqing Zheng, Xuanjing Huang. EMNLP 2024,
pages 17716–17736. DOI 10.18653/v1/2024.emnlp-main.981.
<https://aclanthology.org/2024.emnlp-main.981/> · **verified, read (full PDF)**

A pipeline-wide best-practices sweep. Appendix A.2 contains the chunking evidence.

Table 3 (chunk size, faithfulness / relevancy): 2048 → 80.37 / 91.11; 1024 → 94.26 / 95.56;
512 → 97.59 / 97.41; 256 → 97.22 / 97.78; 128 → 95.74 / 97.22.
Table 4 ("chunk skills"): Original 95.74 / 95.37; small2big 96.67 / 95.37; sliding window 97.41 /
96.85, with the small chunk at 175 tokens, the large at 512 and overlap 20.

**Read the setup before quoting either table.** Both are computed on **one document** — the first
sixty pages of Lyft's 2021 10-K — with roughly 170 queries generated by an LLM from that same
document, scored by LlamaIndex's faithfulness and relevancy metrics with GPT-3.5-turbo as judge.
No variance, no confidence intervals, no significance test, one embedding model, one generator.
The differences in Table 4 are 1–2 points on an LLM-judged 0–100 scale from a single document.

**Size**: nominal only; realised lengths not reported; small2big and sliding window are by
construction *not* size-matched against the baseline — that is the method.
**Corpus**: a single 60-page financial filing.
**Stage**: end-to-end generation, LLM-as-judge. No retrieval metrics, no reranker.

**Weight: weak as evidence, essential as a citation.** This is, as far as this survey could
establish, **the entire peer-reviewed empirical basis for small-to-big and sentence-window
retrieval**. State that plainly: the most widely deployed "advanced" chunking pattern in production
RAG rests on a 2×3 table computed on one 10-K with an LLM judge. That is a finding in its own right.

Related, weaker: `smigielski-2026` and `zhou-2026` both omit parent-document retrieval entirely;
`chen-2024-densex` and `sarthi-2024-raptor` are the nearest rigorous analogues (retrieve fine,
deliver coarse / deliver from a different level), and both control delivered budget.

---

## 7. Classical topic segmentation — the uncited ancestor

### `hearst-1997` — Text Tiling: Segmenting Text into Multi-paragraph Subtopic Passages
Marti A. Hearst. *Computational Linguistics* 23(1):33–64, 1997.
<https://aclanthology.org/J97-1003/> · **verified, read (full PDF)**

The original lexical-cohesion segmenter: tokenise into fixed-length token-sequences, score adjacent
blocks by lexical similarity, place boundaries at the valleys. Every modern "semantic chunker" that
thresholds adjacent-sentence similarity is TextTiling with sentence embeddings substituted for word
overlap, and the modern papers generally do not say so.

**Size**: the algorithm's own parameters (token-sequence length, block size *k*) are the size knobs,
and Hearst notes that earlier passage work "chose block lengths that approximated the average
subtopic segment length" — i.e. the size/method entanglement was visible in 1997.
**Corpus**: 12 magazine articles of 1,800–2,500 words.
**Stage**: **not retrieval at all.** Evaluation is agreement with boundaries marked by seven human
judges per article. The retrieval connection is a separate interface (TileBars).

**Weight: essential citation, zero evidential weight for retrieval.** Its use is to establish that
the idea is thirty years old and that its original evaluation target — agreement with human
boundaries — is not the same target as retrieval effectiveness. This is the same substitution that
makes `allamraju-2025`'s 24× unreadable.

### `pevzner-2002` — A Critique and Improvement of an Evaluation Metric for Text Segmentation
Lev Pevzner, Marti A. Hearst. *Computational Linguistics* 28(1):19–36, 2002.
DOI 10.1162/089120102317341756. <https://aclanthology.org/J02-1002/> · **verified, read (full PDF,
abstract and §2)**

Shows that Pk, the standard segmentation metric, "penalizes false negatives more heavily than false
positives, overpenalizes near misses, and is **affected by variation in segment size distribution**",
and proposes WindowDiff instead. Pk itself is defined by setting the window k to half the average
true segment size.

**Weight: strong and directly on our thesis, from an unexpected direction.** The segmentation
community discovered twenty-four years ago that its own quality metric is confounded by segment
length, and fixed the metric. The retrieval community inherited the segmenters and not the lesson.
This is the best single citation for "the realised-length confound is not a new kind of problem".

### `choi-2000` — Advances in domain independent linear text segmentation
Freddy Y. Y. Choi. 1st Meeting of the North American Chapter of the ACL (NAACL 2000).
<https://aclanthology.org/A00-2004/> · **verified (ACL Anthology), not read in full**

C99: replaces inter-sentence similarity with rank in the local context and finds boundaries by
divisive clustering. Twice as accurate and seven times faster than the then state of the art.
Introduced the synthetic Choi dataset of concatenated Brown-corpus excerpts, on which most
subsequent segmentation work was evaluated.

### `riedl-2012` — TopicTiling: A Text Segmentation Algorithm based on LDA
Martin Riedl, Chris Biemann. Proceedings of the ACL 2012 Student Research Workshop, pages 37–42.
<https://aclanthology.org/W12-3307/> · **verified (ACL Anthology), not read in full**

TextTiling with LDA topic IDs replacing lexical overlap.

### `koshorek-2018` — Text Segmentation as a Supervised Learning Task
Omri Koshorek, Adir Cohen, Noam Mor, Michael Rotman, Jonathan Berant. NAACL-HLT 2018 (Volume 2,
Short Papers), pages 469–473. DOI 10.18653/v1/N18-2075. <https://aclanthology.org/N18-2075/> ·
**verified (ACL Anthology), not read in full**

Reframes segmentation as supervised learning and releases WIKI-727K: 727,746 English Wikipedia
documents labelled with their table-of-contents structure. This is the training set that modern
learned chunkers — including `lu-2025-hichunk` — still use.

**A caution to carry into the paper**: WIKI-727K's labels are *author-imposed section boundaries*.
A chunker trained on it is trained to reproduce human document structure. When such a chunker is
then evaluated against gold evidence that is itself defined by human structure, the evaluation is
partly circular — which is precisely the defect diagnosed in `allamraju-2025`.

### `arnold-2019-sector` — SECTOR: A Neural Model for Coherent Topic Segmentation and Classification
Sebastian Arnold, Rudolf Schneider, Philippe Cudré-Mauroux, Felix A. Gers, Alexander Löser.
*Transactions of the ACL* 7:169–184, 2019. DOI 10.1162/tacl_a_00261.
<https://aclanthology.org/Q19-1011/> · **verified (ACL Anthology), not read in full**

### `lukasik-2020` — Text Segmentation by Cross Segment Attention
Michal Lukasik, Boris Dadachev, Kishore Papineni, Gonçalo Simões. EMNLP 2020, pages 4707–4716.
DOI 10.18653/v1/2020.emnlp-main.380. <https://aclanthology.org/2020.emnlp-main.380/> ·
**verified (ACL Anthology), not read in full**

### `glavas-2020-cats` — Two-Level Transformer and Auxiliary Coherence Modeling for Improved Text Segmentation
Goran Glavaš, Swapna Somasundaran. *Proceedings of the AAAI Conference on Artificial Intelligence*
34(05):7797–7804, 2020. DOI 10.1609/aaai.v34i05.6284.
<https://ojs.aaai.org/index.php/AAAI/article/view/6284> · **verified (AAAI OJS), not read in full**

**Weight for `choi-2000`, `riedl-2012`, `koshorek-2018`, `arnold-2019-sector`, `lukasik-2020`,
`glavas-2020-cats` as a group: cite as a block, not individually.** They establish that there is a
mature, twenty-five-year segmentation literature with its own benchmarks (Choi, WIKI-727K, Cities,
Elements) and its own metrics (Pk, WindowDiff), and that **not one of them evaluates retrieval**.
The modern chunking literature imports the segmenters and reinvents the evaluation, usually without
the citation. That gap is a paragraph of our related work.

---

## 8. Classical passage retrieval — the part that did control length

### `callan-1994` — Passage-Level Evidence in Document Retrieval
James P. Callan. Proceedings of the 17th Annual International ACM-SIGIR Conference on Research and
Development in Information Retrieval (SIGIR '94), pages 302–310, Dublin. Springer, 1994.
DOI 10.1007/978-1-4471-2099-5_31 · **verified (OpenAlex/DOI record; ACM DL returned 403), not read
in full — content characterised only via `kaszkiel-2001`'s description of it**

Defines the fixed-length word window as a passage type and establishes passage-level evidence for
document ranking. `kaszkiel-2001` credits Callan with the word-based sliding window and reports that
his and their own experiments found "an effective length for windows is any size between 150 and 350
words".

### `kaszkiel-1997` — Passage retrieval revisited
Marcin Kaszkiel, Justin Zobel. Proceedings of the 20th Annual International ACM SIGIR Conference
(SIGIR '97), pages 178–185. DOI 10.1145/258525.258561 · **verified (OpenAlex/DOI record; ACM DL
returned 403), not read in full**

### `kaszkiel-2001` — Effective Ranking with Arbitrary Passages
Marcin Kaszkiel, Justin Zobel. *Journal of the American Society for Information Science and
Technology* 52(4):344–364, 2001. <https://people.eng.unimelb.edu.au/jzobel/fulltext/jasist01.pdf>
· **verified (JASIST record via DOI + the authors' full-text PDF), read (full PDF)**

**The most important pre-neural paper in this survey, and the closest thing to a size-controlled
method comparison that exists anywhere.** They compare five passage types — paragraphs, "pages"
(≈2,000-character paragraph-bounded units), windows-150, windows-350, and **tiles produced by
Hearst's TextTiling implementation** — against whole-document ranking and against fixed-length
arbitrary passages, on five TREC collections.

The fixed-length sweep is exhaustive: "We chose a set of fixed passage lengths from 50 to 600 words
in increments of 50, that is, twelve different lengths", with passages starting every 25 words.
600 words was chosen because it "well exceeds the median document length for the TREC data".

Two results matter to us:

1. **Effectiveness is remarkably flat in passage length.** "The consistent effectiveness for
   different passage lengths is quite remarkable. For both query sets, any passage length in the
   range of 50–450 words outperforms whole-document ranking." They call this robustness, and it is
   the 2001 version of the observation that the size knob matters less than everyone assumes over a
   wide plateau.
2. **Where a segmentation method loses, they attribute it to realised length, not to segmentation
   quality**: "paragraphs do not perform well because many of them are very short."

**Size**: fixed-length passages are nominal = realised by construction, and are swept across twelve
values. The *predefined* types (paragraphs, pages, tiles) have uncontrolled realised lengths, and
those lengths are not tabulated — so even here the method comparison is not size-matched, although
the authors reason about size informally when explaining the differences.
**Corpus**: five TREC collections chosen for document-length contrast. FR-12: 45,820 documents,
median 3.4 KB, longest 2,577 KB; FR-24 longest 6,245 KB; WSJ-12 deliberately short-document. The
average length of a relevant document in the FR collections is 145 KB, "ten times greater than" in
the others. **This is the only corpus family in the survey explicitly selected so that document
length varies as a controlled factor.**
**Stage**: first-stage ranking, cosine and pivoted-cosine, TREC average precision. No reranker
(none existed).

**Weight: very strong, and the best framing device we have.** Modern chunking papers rediscover, at
higher cost and with weaker evidence, results that the passage-retrieval literature established on
TREC-scale long-document collections twenty-five years ago — including that the effect is flat over
a broad length range and that "paragraph chunking lost" often means "paragraph chunks were short".
Almost none of the 2024–2026 papers in this survey cite it.

---

## 9. Systematic comparisons, benchmarks and reproducibility studies

### `zhou-2026` — Beyond Chunk-Then-Embed: A Comprehensive Taxonomy and Evaluation of Document Chunking Strategies for Information Retrieval
Yongjie Zhou, Shuai Wang, Bevan Koopman, Guido Zuccon. arXiv 2602.16974, 19 February 2026.
<https://arxiv.org/abs/2602.16974> · **verified (arXiv record), read (full PDF)**

**The single most important paper in this inbox.** A reproducibility study from the Zuccon group
that unifies the two lines that never met: it reproduces LumberChunker on GutenQA and Late Chunking
on BEIR, then crosses six segmentation methods (paragraph, fixed-size 256, sentence-of-5, semantic
at the 95th percentile, proposition, LumberChunker) with two embedding-chunking orderings
(pre-embedding vs contextualised) over four embedding models, two task types and seven datasets.

Findings: reproductions hold. For **in-corpus** retrieval (six BEIR sets, MaxP document
aggregation), structure-based methods win and LLM-guided methods offer nothing for their cost
(paragraph: 1,854 docs/s vs LumberChunker: 1.11 docs/s); proposition chunking is worst by 15–27%.
For **in-document** retrieval (GutenQA), the ranking reverses and LumberChunker wins by 10–30%.
Contextualised chunking helps in-corpus (most for proposition chunking, +15% to +27%) and *degrades
in-document retrieval in every configuration tested*, by up to −63% for Nomic.

**Their RQ4 is our question, and it is worth quoting exactly**: "Different segmentation methods
produce chunks of varying sizes, yet prior work has not controlled for this variable. This raises a
simple question: do effectiveness differences reflect segmentation quality, or merely chunk size?"

**How they answer it, and why it is not our answer.** They compute, per query, the average chunk
size in tokens of the relevant document (or relevant paragraph) and **correlate** it with
effectiveness. Under pre-embedding chunking on GutenQA: paragraph r = 0.57, sentence r = 0.52,
proposition r = 0.41, semantic r = 0.10, fixed-size r = 0.00, LumberChunker r = −0.04. In-corpus,
all correlations fall to r = 0.07–0.16 (significant but weak). Contextualised chunking attenuates
the in-document correlations (0.57 → 0.44, 0.52 → 0.35). Their conclusion: "effectiveness
differences in RQ1 are not purely driven by chunk size, though it may still play a minor role."

**Size**: realised chunk size is measured and used as a **covariate**, not as a controlled or matched
factor. No per-method realised-length distributions are tabulated — the size axis appears only as a
plot axis. The methods themselves run at their own natural sizes. So: **they name the confound, they
measure its correlation, and they do not control it.**
**Corpus**: GutenQA (100 books — long) plus six BEIR sets (FiQA, ArguAna, SciDocs, TREC-COVID,
SciFact, NFCorpus) chosen partly because they are small enough to run LLM chunkers over. The BEIR
sets are the short-document problem.
**Stage**: first-stage retrieval throughout, nDCG@10 / DCG@10, MaxP aggregation following Dai and
Callan. No reranker.

**Weight: very strong, and it is the paper our contribution must be positioned against.** They ask
the exact question, answer it correlationally, and reach a weaker conclusion than a controlled
design would support — a per-query correlation between "average chunk size of the relevant document"
and effectiveness is not the same estimand as "effect of method at matched realised size", and a
near-zero correlation within a method (fixed-size r = 0.00) is expected when that method has almost
no size variance to correlate with. Our design answers what theirs cannot.

### `smigielski-2026` — Chunking Methods on Retrieval-Augmented Generation – Effectiveness Evaluation Against Computational Cost and Limitations
Mateusz Śmigielski, Michał Rajkowski, Mateusz Zbrocki, Michał Bernacki-Janson, Karol Kunicki,
Julianna Godziszewska, Maciej Piasecki, Konrad Wojtasik (Wrocław University of Science and
Technology). 30th International Conference on Knowledge-Based and Intelligent Information &
Engineering Systems (KES 2026), *Procedia Computer Science*, Elsevier, CC BY-NC-ND; preprint
arXiv 2606.00881, 30 May 2026. <https://arxiv.org/abs/2606.00881> · **verified (arXiv record +
the Procedia front matter in the PDF), read (full PDF)**

Eight chunkers (DenseX, recursive semantic, fixed-size 512/50, Sequential HAC, TextTiling, Max-Min,
GraphSeg, LumberChunker) over ten dataset configurations, with a 48-hour per-run time limit.
Notable for reporting *failures*: "T" (exceeded 48 h) and "S" (spaCy memory error) fill much of the
results table — LumberChunker times out on six of eleven datasets, DenseX on seven. Averages:
recursive semantic 89.36 Accuracy@5, fixed-size 87.71, GraphSeg 86.85, LumberChunker 85.44,
Max-Min 85.75, TextTiling 84.96, Sequential HAC 80.09, DenseX 69.10.

**The methodological sentence to quote**: "Consequently, chunk sizes were not normalized across
methods, as we wanted each method to freely adapt its chunking strategy according to its design
assumptions." That is the confound, stated as a deliberate design decision, by authors who were
otherwise unusually careful.

**Size**: explicitly **not** normalised, and realised token lengths are not reported — only chunk
*counts* per method (their Figure 4), from which they infer that "chunk quality and structural
coherence are more important than chunk quantity".
**Corpus**: document-length statistics in characters are given (Appendix A, Table A.5): GutenQA
36,917 docs averaging 1,814 characters; NovelQA 60 docs averaging 1,007,786; LiteraryQA 411,471
average; PoQuAD 922; Qasper 1,014. They also concatenate three datasets into single multi-million-
character documents as a stress test, and state the corpus-adequacy principle directly: "many widely
used QA datasets were excluded or deemphasized because their documents are too short."
**Stage**: **behind a reranker** — bge-m3 retriever with bge-reranker-v2-m3 — plus an end-to-end
generation study with GPT-OSS-20B as both generator and LLM judge on a five-point Likert scale.

**Weight: strong, with one caveat.** Its retrieval numbers are post-reranker, so it cannot separate
chunking effects from reranker rescue — which is itself useful to us, since it is one of the few
studies in the survey that places the comparison behind a reranker at all. Cite it for (a) the
explicit non-normalisation statement, (b) the honest failure reporting, and (c) the finding that
recursive semantic and plain fixed-size beat every expensive method on average.

### `smith-2024-chroma` — Evaluating Chunking Strategies for Retrieval
Brandon Smith, Anton Troynikov. Chroma technical report, 3 July 2024.
<https://www.trychroma.com/research/evaluating-chunking> · **verified (publisher page fetched),
read (page and results tables)**

Compares RecursiveCharacterTextSplitter, TokenTextSplitter, KamradtSemanticChunker, a modified
Kamradt chunker, a novel ClusterSemanticChunker and an LLM chunker, scoring token-level recall,
precision, Precision_Ω and IoU against annotated gold excerpts.

**This is the only source in the survey that prints the realised chunk size next to the requested
one, in the results table itself.** Verbatim rows (text-embedding-3-large, n = 5):
Recursive 800 (~661) overlap 400 → recall 85.4; Recursive 400 (~312) overlap 200 → 88.1;
Recursive 400 (~276) overlap 0 → 89.5; Recursive 200 (~137) → 88.1; Cluster 200 (~103) → 87.3,
precision 8.0, IoU 8.0; LLM (GPT-4o) N/A (~240) → recall 91.9, precision 3.9.

Read that column: a requested 800 produced ~661, a requested 400 produced ~312 or ~276 depending on
overlap, and a requested 200 produced ~137. **The nominal-to-realised gap is 17–35% in a standard
LangChain splitter, and it is not constant across settings.**

**Size**: realised means reported; **not matched across methods** — at the "200" setting the
realised means are 137 (recursive), 200 (token) and 103 (cluster), and the LLM chunker sits at 240
with no nominal size at all. Their precision and IoU metrics are monotonically favourable to short
chunks, so the ClusterSemanticChunker's precision win is at least partly a size artifact that the
report does not separate out.
**Corpus**: five corpora with token counts given — State of the Union 10,444; Wikitext 26,649;
Chatlogs 7,727; Finance 166,177; Pubmed 117,211. Long enough to support 800-token chunks.
**Stage**: first-stage retrieval only, token-level IoU/recall/precision. No reranker.

**Weight: not peer-reviewed, but the closest published precedent for our nominal-versus-realised
observation, and it must be cited.** It shows the gap exists and is visible to anyone who prints the
column — and that nobody in the refereed literature printed it.

### `demoura-2026` — Adaptive Chunking: Optimizing Chunking-Method Selection for RAG
Paulo Roberto de Moura Júnior, Jean Lelong, Annabelle Blangero (Ekimetrics). LREC 2026 (per the
arXiv comments field: "Accepted at LREC 2026"). arXiv 2603.25333, 26 March 2026.
<https://arxiv.org/abs/2603.25333> · **verified (arXiv record), read (full PDF, methods and tables)**

Proposes five intrinsic, document-level chunking-quality metrics — References Completeness,
Intrachunk Cohesion, Document Contextual Coherence, Block Integrity and **Size Compliance** — and
uses them to pick a chunker per document. Also introduces an LLM-regex splitter and a
split-then-merge recursive splitter.

**Two things here bear directly on us.**

First, **Size Compliance is a nominal-versus-realised metric**: "the proportion of chunks whose
token length falls within predefined bounds (100–1,100 tokens in our experiments)". Second, they
apply a **two-stage post-processing pipeline to every method before comparing them** — oversized
chunks above 1,100 tokens are re-split, chunks below 100 tokens are merged (max merged size 1,150) —
and report what it does: the LLM-regex method's SC goes from 58.3% to 99.6%, the semantic chunker's
from 48.1% to 99.9%.

Their Table 2 gives realised chunk-size statistics per method in o200k_base tokens
(mean / max / min / std / #chunks): LLM regex 518 / 1146 / 69 / 332; recursive s=1100
878 / 1141 / 104 / 217; recursive s=600 496 / 691 / 102 / 101; page (post-processed)
663 / 1146 / 72 / 235; Adaptive Chunking 724 / 1146 / 86 / 247. The un-post-processed rows are the
warning: **raw semantic chunking has mean 693, max 17,146, min 1, std 1,095.**

**Size**: realised lengths fully reported (mean, max, min, std), and **bounded into a common band
across methods** by post-processing — the strongest size intervention found anywhere in this survey.
But a 100–1,100 band is a very loose bound: the post-processed means still range from 496 to 878, so
sizes are *bounded*, not *matched*.
**Corpus**: a heterogeneous corpus spanning legal, technical and social-science documents.
**Stage**: a full pipeline — semantic retrieval, **reranking** (a named state-of-the-art reranker),
then generation, with retrieval completeness and answer correctness as outcomes.

**Weight: very strong, and the nearest competitor to our contribution.** Anyone reviewing our paper
who knows this one will ask why a Size Compliance metric plus normalising post-processing is not
already the control we claim to introduce. The answer is in their own Table 2 — a 100–1,100 band
leaves a 1.8× spread in realised means between the methods being compared — but we must make that
argument explicitly rather than claim nobody has looked.

### `amiri-2025` — Chunk Twice, Embed Once: A Systematic Study of Segmentation and Representation Trade-offs in Chemistry-Aware Retrieval-Augmented Generation
Mahmoud Amiri, Thomas Bocklitz. arXiv 2506.17277, v1 13 June 2025, v2 17 September 2026 (44 pages).
<https://arxiv.org/abs/2506.17277> · **verified (arXiv record), read (full PDF, methods and results)**

Builds ChemQuests (952 QA pairs from 151 ChemRxiv papers across 17 subfields), screens 41 embedding
models on two external chemistry benchmarks, then runs a **constrained factorial grid**: five
chunking strategies (fixed-token, recursive-token, semantic-fixed, semantic-recursive,
hierarchical-section) × seven chunk sizes (128, 192, 256, 320, 384, 448, 512 tokens) × five overlaps
(0–64), subject to O ≤ min(64, 0.3L), L − O ≥ 96, L + O ≤ 512. Metric Geom@10 = √(nDCG@10 · Recall@10).

Headline: "differences among embedding models are larger than differences among chunking strategies."
128-token chunks underperform; performance rises to medium-large sizes then plateaus; overlap is
neutral to negative; every configuration in the top 1% uses `intfloat/e5-large-v2` with 384–512-token
chunks and ≤32 overlap, and fixed-token, recursive-token and hierarchical-section chunking dominate
that region while semantic strategies are scarce.

**Size**: this is the **best crossed design** in the survey. Chunking strategy and nominal chunk size
are fully crossed, and the chunk-size analysis is "performed at fixed overlap values" while the
overlap analysis is "performed at fixed chunk sizes". Critically, their semantic strategies are
size-enforcing by construction — "Semantic-fixed and semantic-recursive first identify semantic
regions and then enforce size using fixed or recursive splitting" — so at a given L, all five
strategies target the same L. **That is much closer to realised-size control than anything else
published.** What is missing: no realised chunk-length distributions are reported (the paper
acknowledges only that "nominal chunk sizes are model-tokenizer dependent"), so whether the enforced
sizes actually converged is unverifiable; and the model×strategy heatmap aggregates by taking the
*maximum* Geom@10 over the grid per pair, which is a best-case selection.
**Corpus**: 151 full ChemRxiv papers — long documents. But the grid caps at 512 tokens, so nothing
above 512 is tested.
**Stage**: first-stage retrieval via the MTEB pipeline. No reranker, no generation.

**Weight: very strong, and the closest published thing to the design we are claiming.** If a
reviewer names one paper as prior art for controlling size across methods, it will be this one. The
honest distinction is nominal versus realised: they enforce a target and never verify what came out.

### `bhat-2025` — Rethinking Chunk Size For Long-Document Retrieval: A Multi-Dataset Analysis
Sinchana Ramakanth Bhat, Max Rudat, Jannis Spiekermann, Nicolas Flores-Herr (Fraunhofer IAIS).
arXiv 2505.21700, v1 27 May 2025. <https://arxiv.org/abs/2505.21700> · **verified (arXiv record),
read (full PDF)**

Fixed-size chunking only, swept over 64/128/256/512/1024 tokens with LlamaIndex's TokenTextSplitter,
across six extractive-QA datasets and multiple embedding models. Smaller chunks (64–128) win where
answers are short and factual; larger (512–1024) win where answers are descriptive — TechQA
Recall@1 goes from 16.5% at 128 tokens to 61.3% at 512. Embedding models differ in their chunk-size
sensitivity (Stella prefers large, Snowflake small).

**Size**: nominal = realised by construction (token splitter); no method comparison, so no
cross-method size control is possible or attempted.
**Corpus**: the best-documented in the survey. Table 1 gives tokens/doc: NarrativeQA 51,830;
NewsQA 8,484; COVID-QA 10,009; TechQA 7,597; SQuAD 7,998; NQ 6,918. Four of the six are **stitched
together from shorter documents** to reach a minimum length, and the paper says why: "A significant
challenge in evaluating long RAG datasets is the scarcity of datasets with sufficiently long
documents." Independent confirmation of the corpus-adequacy problem, and of the workaround.
**Stage**: first-stage retrieval, Recall@1 primarily. No reranker.

**Weight: strong for the size×dataset×model interaction.** Their closing call — "emphasizing the
need for improved chunk quality measures, and more comprehensive datasets" — is the same gap
`qu-2025` names.

### `wu-2026-code` — How Does Chunking Affect Retrieval-Augmented Code Completion? A Controlled Empirical Study
Xinjian Wu, Jingzhi Gong, Gunel Jahangirova, Jie Zhang. arXiv 2605.04763, 6 May 2026.
<https://arxiv.org/abs/2605.04763> · **verified (arXiv record), read (full PDF)**

Four chunking strategies (function, declaration, sliding window, cAST) × three chunk sizes
(1,000 / 2,000 / 3,000 non-whitespace characters) × three cross-file context budgets
(2,048 / 4,096 / 8,192 tokens), fully crossed, on RepoEval and CrossCodeEval, with multiple
retrievers and generators; Wilcoxon signed-rank with Bonferroni correction and Cliff's δ.

Findings: **context budget dominates** (+4.2 pp EM from 2,048 → 8,192 tokens, significant with
medium-to-large effect sizes) while "chunk size has a weaker, non-monotonic" effect that *interacts*
with budget — at 2,048 tokens larger chunks *reduce* EM by up to 1.9 pp. Function chunking is worst
by 3.57–5.64 pp (Cliff's δ = −1.0) and, tellingly, "Function is insensitive to chunk size, within
0.6 pp" — because a function's realised length is set by the code, not by the size parameter.

**Size**: nominal size fully crossed with the other factors; **realised chunk lengths are never
reported**, which is exactly why their own "Function is insensitive to chunk size" observation
cannot be followed up inside the paper. This is our confound appearing as an unexplained result in
someone else's ablation.
**Corpus**: source-code repositories (RepoEval; CrossCodeEval filtered to post-March-2023 repos for
leakage control).
**Stage**: end-to-end code completion, Exact Match, with retrieval feeding the prompt. No reranker.

**Weight: moderate for us (different domain), strong as a methodological template.** It is the most
statistically careful chunking ablation in the survey, and it still leaves the realised-length
question open in its own data.

### `jimeno-yepes-2024` — Financial Report Chunking for Effective Retrieval Augmented Generation
Antonio Jimeno Yepes, Yao You, Jan Milczek, Sebastian Laverde, Renyu Li (listed as "Leah Li" on the
PDF title page). arXiv 2402.05131, v1 5 February 2024, v3 16 March 2024.
<https://arxiv.org/abs/2402.05131> · **verified (arXiv record), read (PDF, abstract and results
sections)**

Chunks SEC 10-K/10-Q filings by *structural element type* (title, table, list, narrative text)
detected by a document-understanding model, rather than by paragraph or fixed size. Claim: element-
type chunking "yields the best chunk size without tuning".

**Size**: the claim *is* a size claim — they argue structural chunking lands on a good realised size
automatically, and they report chunk counts and the relationship "between accuracy and total chunk
size", noting their chunks "are closer in size to Base 512". Realised length distributions per
element type are not given.
**Corpus**: SEC filings — long, highly structured, table-heavy.
**Stage**: retrieval plus RAG QA.

**Weight: moderate.** Useful as the structural-chunking representative and because its own framing
("the best chunk size without tuning") concedes that what a chunking method really delivers is a
size. Note the author-name discrepancy between the arXiv record (Renyu Li) and the PDF title page
(Leah Li) if we cite it.

### `bennani-2026` — A Systematic Analysis of Chunking Strategies for Reliable Question Answering
Sofia Bennani, Charles Moslonka. arXiv 2601.14123, 20 January 2026 (3-page preprint).
<https://arxiv.org/abs/2601.14123> · **verified (arXiv record), read (full 3 pages)**

Four methods (token, sentence, semantic, code) across a grid of chunk sizes, with retrieved chunks
filling a context budget C ∈ {500, 1k, 2.5k, 5k, 10k} tokens. Their stated rationale: the fill
policy "ensures fair comparison across chunk sizes and methods (no [bias from differing chunk
counts])". Reports a "context cliff" beyond ~2.5k tokens and that token chunking matches semantic
chunking up to ~5k.

**Size**: budget-matched, not size-matched; realised lengths unreported.
**Weight: weak (3 pages, no venue, minimal detail), but a third independent instance of the
budget-control idiom.** Cite only alongside `lu-2025-hichunk` and `chen-2024-densex` to show the
idiom is standard.

### `gomez-cabello-2025` — Comparative Evaluation of Advanced Chunking for Retrieval-Augmented Generation in Large Language Models for Clinical Decision Support
Cesar A. Gomez-Cabello, Srinivasagam Prabha, Syed Ali Haider, Ariana Genovese, Bernardo G. Collaço,
Nadia G. Wood, Sanjay P. Bagaria, Antonio J. Forte. *Bioengineering* (Basel) 12(11):1194, 2025.
DOI 10.3390/bioengineering12111194, PMID 41301150.
<https://pubmed.ncbi.nlm.nih.gov/41301150/> · **verified (PubMed record + OpenAlex/DOI),
NOT READ — full text could not be retrieved (see § 4)**

Peer-reviewed comparison of proposition-based, semantic and adaptive chunking for clinical decision
support; the abstract states that "fixed-length chunks can split concepts or add noise, reducing
precision". `smigielski-2026` characterises it as showing chunking methodology significantly
influences RAG-LLM performance in that setting, while noting "the scope of chunking strategies
considered in that study was relatively limited".

**Weight: hold. Do not cite until read** — this is the nearest peer-reviewed biomedical analogue to
our setting and therefore exactly the kind of entry the `allamraju-2025` rule exists for.

---

## 10. Adjacent — theory of semantic chunking

### `zhong-2026-entropy` — Semantic Chunking and the Entropy of Natural Language
Weishun Zhong, Doron Sivan, Tankut Can, Mikhail Katkov, Misha Tsodyks (IAS Princeton, Weizmann,
Emory). arXiv 2602.13194, February 2026 (29 pages). <https://arxiv.org/abs/2602.13194> ·
**verified (arXiv record), read (abstract and introduction only)**

A statistical-physics model of language as a self-similar recursive partition into semantically
coherent chunks down to the word level, recovering the ~1 bit/character entropy rate of English and
predicting that the entropy rate rises with corpus semantic complexity.

**Weight: not retrieval evidence; cite only if we want a theoretical hook for why a single
"semantic" scale does not exist.** Its central premise — that semantic chunking is inherently
*multi-scale and recursive* — is an argument that any single-threshold chunker will collapse toward
one characteristic scale, which is congenial to our realised-size finding but is not evidence for it.

---

# 1. Does anyone control realised chunk size?

**No. Nobody matches realised chunk-length distributions across the methods they compare.** The
search was specifically for this and the answer is negative. What exists is four weaker things, and
we should name all four rather than claim the space is empty.

**(a) The confound is named, and answered correlationally — `zhou-2026`.** Zhou, Wang, Koopman and
Zuccon state it exactly: "Different segmentation methods produce chunks of varying sizes, yet prior
work has not controlled for this variable. This raises a simple question: do effectiveness
differences reflect segmentation quality, or merely chunk size?" Their answer is a per-query
correlation between the average chunk size of the relevant document and retrieval effectiveness
(in-document r = −0.04 to 0.57 by method; in-corpus r = 0.07–0.16), concluding size "may still play
a minor role". A correlation over queries is not a matched comparison: it cannot separate the
within-method size gradient from the between-method size shift, and a method with little size
variance (fixed-size, r = 0.00) will show no correlation however much its size drives its score.
**This is the paper we must cite and distinguish, in that order.**

**(b) Nominal size is matched; realised size is not measured — `caspari-2026`, `amiri-2025`.**
Caspari et al. run six of eight methods at a nominal 512 tokens with 25-token overlap — then let two
of those six prepend a title or an LLM-written summary, so the winning methods carry more realised
text, and no realised lengths are reported. Amiri and Bocklitz go further and fully cross five
strategies with seven nominal sizes, with all five strategies *enforcing* the target size (their
semantic variants "first identify semantic regions and then enforce size"). That is the best crossed
design published, and it still reports only that "nominal chunk sizes are model-tokenizer dependent"
— it never verifies what came out.

**(c) Realised size is measured, and not matched — `duarte-2024`, `smith-2024-chroma`,
`demoura-2026`.** LumberChunker's Appendix F prints realised means per method (12 to 399 tokens) and
states its own method delivered 334 tokens against an intended 550 — "approximately 40% below". The
Chroma report prints the realised mean beside the requested size in the results table itself
(800 → ~661, 400 → ~312, 200 → ~137). Adaptive Chunking goes furthest: it defines Size Compliance,
reports mean/max/min/std per method, and *normalises* every method into a 100–1,100-token band by
re-splitting oversized and merging tiny chunks — after which the compared methods' realised means
still span 496 to 878 tokens. **Bounded, not matched.**

**(d) The delivered token budget is matched, which is a different control — `chen-2024-densex`,
`sarthi-2024-raptor`, `lu-2025-hichunk`, `bennani-2026`, `wu-2026-code`.** This idiom is standard
and well understood: RAPTOR — "We provide the same amount of tokens of context to RAPTOR and to the
baselines"; HiChunk — "Due to the varying sizes of chunks resulting from semantic-based chunking, we
limit the length of the retrieved context based on the number of tokens rather than the number of
chunks for a fair comparison"; Dense X — recall at a fixed retrieved-word budget. Budget control
equalises how much text the reader sees. It says nothing about the *granularity* of the units, which
is the thing our result is about.

**Consequence for the paper.** The novel contribution survives, but the claim has to be stated at a
finer grain than "nobody controls for chunk size". The defensible form is: *the literature controls
nominal size (Amiri and Bocklitz, Caspari et al.), bounds realised size (de Moura Júnior et al.),
reports realised size (Duarte et al., Smith and Troynikov) and matches delivered budget (RAPTOR,
HiChunk, Dense X) — but no published study conditions its method comparison on matched realised
chunk length, and the one study that poses the question directly answers it with a per-query
correlation.*

---

# 2. Contradicts or complicates us

Looked for aggressively. Seven things.

1. **`zhou-2026`'s own conclusion runs against ours.** Their reading of the correlations is that
   "effectiveness differences in RQ1 are not purely driven by chunk size" — i.e. method matters
   beyond size. If a reviewer takes their result at face value, our finding is the minority position.
   Our counter is about estimand, not data, and must be made carefully.

2. **`zhou-2026`'s rank reversal is a real method effect.** LumberChunker goes from 2nd–4th
   in-corpus to best in-document across all four embedding models (+10–30% on GutenQA), and the
   reversal is *task-dependent*, not size-dependent — the same chunks, the same sizes, opposite
   ranking. A pure size story does not predict that. The honest reading is that method can matter a
   great deal when the retrieval task is in-document localisation, and little when it is in-corpus
   ranking. If our setting is in-corpus, we should say so and scope the claim to it.

3. **`amiri-2025` already runs a crossed design.** Five strategies × seven sizes with size
   enforcement in every strategy, and it reports a genuine (if small) strategy effect on top of a
   clear size effect. If the referee reads this one paper, "method contribution nearly vanishes once
   size is controlled" will look like it has already been tested and partly contradicted. We need
   the nominal/realised distinction to hold up under direct questioning.

4. **`conti-2025` shows the size response curve is a property of the representation.** Contextually
   trained embeddings deliver "much more uniform retrieval performance across a wide range of chunk
   sizes". If realised size explains method differences under one embedding model, that may not
   generalise to contextualised or late-chunked models — and `zhou-2026` independently finds that
   contextualised chunking *attenuates* the in-document size correlation (0.57 → 0.44). Our result
   may be model-conditional.

5. **`wu-2026-code` finds a large, statistically significant method effect at fixed size.** Function
   chunking loses by 3.57–5.64 pp with Cliff's δ = −1.0 across a fully crossed size × budget grid.
   Their explanation is structural (function chunking discards module-level code), not size-based —
   though note the same paper reports function chunking is "insensitive to chunk size, within 0.6 pp",
   which is a realised-vs-nominal artifact they do not pursue.

6. **`caspari-2026`'s winner is the method that adds text.** Enriched (summary) chunking has the
   best significant-win rate under nDCG@10. That is consistent with a size story (more realised text
   per chunk) *and* with a semantic story (document context helps), and the paper cannot separate
   them — but neither can we, from their data, so it cuts both ways. Note their result also complicates
   any single-metric claim: the method ranking changes between nDCG@10 and Recall@100.

7. **`kaszkiel-2001` complicates the framing rather than the finding.** If effectiveness is flat
   over 50–450 words, then a chunker whose realised length collapses to a single value in that
   plateau should not be *penalised* much either — which weakens any claim that the collapse is
   harmful, as opposed to merely making the four nominal settings indistinguishable. Their result
   supports "the labels were meaningless" and is neutral-to-unhelpful for "and this cost us
   performance".

---

# 3. Corpus adequacy

Which of these evaluate on documents long enough to test large chunks at all. Ranked.

**Adequate — genuinely long documents, verified statistics:**

- `kaszkiel-2001`. Five TREC collections chosen *for* length contrast: FR-12 median document 3.4 KB
  and longest 2,577 KB; FR-24 longest 6,245 KB; WSJ-12 included deliberately as the short-document
  control. Passage lengths up to 600 words were chosen because that "well exceeds the median
  document length for the TREC data" — the only paper here that reasoned about adequacy before
  choosing its sizes.
- `smigielski-2026`. Reports min/max/average document length per dataset: NovelQA averages 1,007,786
  characters, LiteraryQA 411,471; and they additionally concatenate corpora into single
  67-million-character documents as a stress test. Explicitly excludes datasets "because their
  documents are too short".
- `bhat-2025`. NarrativeQA 51,830 tokens/document; four of six datasets stitched from shorter ones
  to reach a minimum length, for exactly this reason.
- `duarte-2024` / GutenQA, and hence `zhou-2026`'s in-document half. 100 full books.
- `sarthi-2024-raptor`. NarrativeQA full books and scripts, QASPER full papers.
- `amiri-2025`. 151 full ChemRxiv papers — though the grid caps at 512 tokens, so length adequacy is
  not the binding constraint there.
- `smith-2024-chroma`. Finance 166,177 tokens, Pubmed 117,211, Wikitext 26,649 — comfortably
  supports the 800-token setting.
- `lu-2025-hichunk`. HiCBench plus LongBench/Qasper/GutenQA/OHRBench.

**Inadequate for large chunks:**

- `chen-2024-densex`. Wikipedia pre-segmented into 100-word passages; the largest unit ever indexed
  is 100 words. Nothing above ~130 tokens is testable in that design.
- `gunther-2024`. Says so itself: "most retrieval tasks contain relatively short texts", so only
  NFCorpus is used from BEIR for the size sweep, with LongEmbed brought in for length and truncated
  at 8,192.
- `zhou-2026`'s in-corpus half, and `qu-2025` before it. Six BEIR datasets (FiQA, ArguAna, SciDocs,
  TREC-COVID, SciFact, NFCorpus) chosen partly for being small enough to run LLM chunkers over. These
  are the corpora where a nominal 1,024-token setting cannot be distinguished from a 512-token one
  because the documents end first. `qu-2025`'s own stated limitation — an ideal dataset "would
  include long documents" — is the same observation.
- `wang-2024-bestpractices`. One 60-page filing.
- `caspari-2026`. MS MARCO v2 web documents and KILT Wikipedia, chunked at 512 tokens, with **no
  document-length statistics reported at all** — so adequacy is unverifiable, which for a CIKM paper
  at 10M-document scale is itself worth a sentence.
- `hearst-1997`. 12 magazine articles of 1,800–2,500 words, and not a retrieval evaluation anyway.

**Unverifiable:** `gomez-cabello-2025` (full text not obtained), `zhao-2024-metachunking`,
`zhong-2025-mog`, `liu-2025-lgmgc` (not read in full).

---

# 4. Searched and did not find

**Not found, after targeted searching:**

- **Any study that matches realised chunk-length distributions across chunking methods.** Queries
  tried, via WebSearch and then via the arXiv and OpenAlex APIs after the search budget was
  exhausted: `abs:"chunk size" AND abs:"matched"`; `abs:"chunking" AND abs:"confound"`;
  `ti:"chunking" AND abs:"chunk size" AND abs:"controlled"`; `ti:"chunking" AND ti:"retrieval" AND
  abs:"average chunk length"`; plus OpenAlex full-text search on the forward citations of
  `qu-2025`. The nearest hits are catalogued in § 1 and none of them match realised size.
- **Any peer-reviewed evaluation of parent-document retrieval, small-to-big, or sentence-window
  retrieval beyond `wang-2024-bestpractices` Table 4.** Searched for "sentence window", "parent
  document retrieval", "small-to-big" as retrieval evaluation; results were framework documentation,
  vendor blogs and Medium posts. `zhou-2026` and `smigielski-2026`, the two most comprehensive
  comparisons in the field, do not include the pattern at all. This absence is itself reportable.
- **Any peer-reviewed evaluation of Anthropic's contextual retrieval other than `caspari-2026`**
  (and the single-example comparison in `gunther-2024` §4.5). `conti-2025` cites the blog post as
  motivation but evaluates its own method, not Anthropic's.
- **A published re-examination of `allamraju-2025`'s baselines.** Nothing cites it critically yet.
- **Any chunking study that reports the realised-length *distribution* (not just the mean) per
  method.** Only `demoura-2026` reports max/min/std alongside the mean, and only for its own
  corpus.

**Found but not retrievable (listed so the attempts are on record):**

- `gomez-cabello-2025` full text. Tried: `mdpi.com/2306-5354/12/11/1194` (HTTP 403),
  `mdpi.com/.../pdf` and `/pdf?version=` (both returned an HTML interstitial, not a PDF),
  `pmc.ncbi.nlm.nih.gov/articles/pmid/41301150/` (reCAPTCHA). Metadata verified via PubMed and
  OpenAlex; entry is marked NOT READ.
- `callan-1994` and `kaszkiel-1997` full texts. ACM Digital Library returned HTTP 403 and
  link.springer.com required an authentication redirect; dblp was behind an Anubis challenge.
  Metadata verified via OpenAlex/DOI; `callan-1994`'s content is characterised only through
  `kaszkiel-2001`'s account of it, and the entry says so.

**Tooling note for whoever picks this up:** the session's WebSearch budget (200 calls) was exhausted
partway through. The remainder of the verification was done with `curl` against
`export.arxiv.org/api/query` (arXiv's own metadata service — authoritative for title, author list,
version dates, comments and journal-ref) and `api.openalex.org` (for DOI, venue, volume and pages
where the publisher page was blocked). The Semantic Scholar API returned HTTP 429 throughout. These
two endpoints are a better default than WebSearch for this task and are not rate-limited in practice.
