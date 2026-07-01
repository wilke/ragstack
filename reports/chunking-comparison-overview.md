# Chunking Strategies for Scientific RAG — A Controlled Comparison

*Overview report and experimental design. Results (§6) are populated when the benchmark completes.
This document is the source for a scientific slide deck — each numbered section maps to one or two slides.*

**System under study:** RAGStack — a polyglot (Python/FastAPI + Go/Chi), contract-first, multi-tenant
Retrieval-Augmented Generation API with hybrid retrieval (dense + lexical + optional graph) and
cross-encoder reranking. Embedding model: **Salesforce/SFR-Embedding-Mistral**, 4096-dim, 4096-token
context window, served via vLLM.

_Draft 2026-06-30. Status: design + hypotheses complete; results pending the 7-config run._

---

## 1. Motivation (slide: "Why chunking decides RAG quality")

Retrieval-Augmented Generation answers a query by **retrieving passages** from a corpus and conditioning
a language model on them. The corpus is not stored as whole documents — each document is split into
**chunks**, embedded into vectors, and indexed. Chunks are the *unit of retrieval*: the system can only
return what a chunk boundary captured.

This makes chunking a first-order design choice, governed by a tension:

- **Too large** → each chunk mixes multiple topics; the embedding is an average that matches no query
  sharply; precision drops, and the chunk may **exceed the embedder's context window** (4096 tokens
  for SFR) and be silently truncated — text past the cut is *never embedded and never retrievable*.
- **Too small** → topical coherence is lost, cross-sentence context is severed, and recall suffers.

Two questions follow, and they are the subject of this study:
1. **Which segmentation strategy** (boundary-agnostic windows vs. sentence/word/topic boundaries) best
   serves retrieval?
2. **Which sizing unit** — *characters* (the legacy default) or *tokens* (what the embedder actually
   consumes) — and what size?

## 2. Background: the context-window constraint (slide: "The token budget is a hard wall")

Embedding models tokenize text into sub-word **tokens** and accept a fixed maximum (SFR: 4096). Yet most
chunkers size by **characters**, a proxy that drifts: dense scientific text (formulae, references,
non-Latin scripts) packs more tokens per character, so a "safe" character size can still overflow.
Prior tooling handles overflow by **silent truncation** (drops the tail), **arbitrary character
capping**, or **rejecting the document** — all *lossy*. RAGStack instead sizes against the **true token
budget** and splits losslessly (§3). Establishing whether this token-awareness costs or gains retrieval
quality is the practical motivation for the comparison.

## 3. Methods — the chunking algorithms (slides: one per family)

All methods emit chunks carrying **exact source character offsets** and a deterministic id
(`uuid5(doc_id:start:end)`), so re-ingestion is idempotent and every chunk is traceable to its document
(provenance for citations and tenant isolation).

**(a) Fixed — character window.** Slide a window of `N` characters across the raw text, advancing by
`N − overlap`. Boundary-agnostic: cuts mid-word, mid-sentence. Simplest and cheapest; uniform sizes.
*(`RecursiveCharacterChunker`.)*

**(b) Fixed — token window.** The token-space analogue: tokenize the document once (with the model's own
tokenizer, retaining a character offset map), slide a window of `N` **tokens** advancing by
`N − overlap` tokens, and map each window back to its exact source character span. Guarantees every
chunk is ≤ `N` tokens — boundary-agnostic but **context-window-exact**.

**(c) Sentence.** Segment into sentences with the **NLTK Punkt** tokenizer (a trained, language-aware
model — not a naive period split), then **greedy-pack whole sentences** until the next would exceed the
budget; overlap re-adds trailing whole sentences. Respects sentence boundaries; sizes approximate
(breaks only at sentence ends). *(`SentenceChunker`.)*

**(d) Words.** As sentence, but the atomic unit is a **whitespace-delimited word** (`\S+`, with gapless
span tiling for exact offsets). Finer-grained boundary control than sentences. *(`WordChunker`.)*

**(e) Semantic — embedding-similarity breakpoints.** Topic-aware segmentation (lineage: llama_index;
the same family HiPerRAG/distllm and embedding_app use):
1. Split into sentences (Punkt).
2. For each sentence build a **buffer** of itself ± `buffer_size` neighbours (default 3 → up to 7
   sentences of context).
3. Embed every buffer.
4. Compute **cosine distance** (`1 − cos`) between consecutive buffers.
5. Place a breakpoint wherever that distance exceeds the **`breakpoint_percentile`-th percentile of
   this document's own distance distribution** (default 80). The threshold is **adaptive per document**,
   so there is no fixed chunk size.
6. Merge any chunk shorter than `min_chunk_length` into a neighbour; fall back to whole-document if the
   procedure yields nothing. *(`SemanticChunker`.)*

### 3.1 Token-based sizing (the controlled variable) (slide: "Lossless token-exact sizing")

Layered on (a)–(e):
- **Token counting** uses a `TokenCounter` — by default the **model's own HuggingFace tokenizer**
  (exact, offline), with the vLLM `/tokenize` endpoint or a character-ratio estimator as fallbacks.
- **The budget is auto-detected** from the serving model: a `GET /v1/models` reports `max_model_len`
  (4096); we reserve 16 tokens for special tokens → an effective **hard cap of 4080**.
- **Over-budget units are split losslessly** by tokens — no chunk ever overflows, and **no text is ever
  dropped**. For sentence/words the token value is the *packing budget*; for semantic it is purely an
  *overflow guard* (its boundaries remain adaptive).

## 4. Experimental design (slide: "Controlled, single-variable comparison")

**Corpus.** A scientific-article corpus (PubMed-class papers with DOI/title/author metadata) supplied as
three JSONL shards. We sample **1,500 article-class documents, balanced 500/500/500** across the three
shards, deterministically. The **same 1,500 documents feed every configuration** — the only thing that
varies is how they are chunked.

**The seven configurations.**

| # | key | method | sizing | parameters | role |
|---|---|---|---|---|---|
| 1 | `fixed_char512` | fixed | char | 512 / overlap 64 | legacy / production baseline |
| 2 | `fixed_char2048` | fixed | char | 2048 / overlap 256 | size-matched control for #4 |
| 3 | `fixed_tok256` | fixed | token | 256 / overlap 32 | small token window |
| 4 | `fixed_tok512` | fixed | token | 512 / overlap 64 | standard token window |
| 5 | `sentence_tok512` | sentence | token budget | ≤512 tok | sentence boundaries |
| 6 | `words_tok512` | words | token budget | ≤512 tok | word boundaries |
| 7 | `semantic_tokcap` | semantic | adaptive + cap | buf 3 / pct 80 / min 500 / cap 4080 | topic boundaries |

This design **factors the two questions apart**: #1↔#2 and #3↔#4 isolate *size*; **#2 (≈512 tok of chars)
vs #4 (512 tok)** isolates the *char-vs-token unit* at matched size; #4 vs #5/#6/#7 isolates *boundary
strategy* at a common budget.

**Held constant (fairness).** Identical embedder (SFR-Embedding-Mistral / 4096-dim, served across 16
vLLM endpoints), identical 1,500-doc subset, identical query set, identical retrieval + rerank pipeline,
and a uniform 4080-token hard cap so **no configuration overflows** (overflow is measured, not suffered).

**Retrieval pipeline (identical for all).** Dense vector search (Qdrant, SFR embeddings) **+** lexical
BM25 (Elasticsearch) fused by **Reciprocal Rank Fusion**, then a **cross-encoder reranker** over the
top-50 candidates.

**Evaluation — known-item retrieval.** For each document we issue its **title as the query**; the
correct result is a chunk from that same document. Over a deterministic **1,000-query sample** we report:

- **recall@{1,5,10}** — was the source document retrieved within the top *k*?
- **MRR@10** — mean reciprocal rank (how near the top?).
- **nDCG@10** — rank-discounted gain.
- The same five metrics **after reranking**, plus **mean top-1 rerank score** (a label-free relevance
  proxy).

**Structure & cost recorded per configuration:** chunk count, chunks/doc, chunk-size distribution in
**both characters and tokens** (median/p95), **count of chunks that hit the 4080-token cap** (the
token-safety payoff), ingest wall-time, and throughput (docs/s, chunks/s).

**Caveats (stated up front).** Title→own-document is a *known-item proxy* that flatters lexical matching;
we therefore report dense+lexical **and** reranked numbers so no single figure is over-read. Ground truth
is single-relevant-document. One corpus, one domain (biomedical), one embedder.

## 5. Expected performance — hypotheses (slide: "What we predict, and why")

*Registered before results, informed by a prior full-corpus 3-mode run (fixed/sentence/semantic at
char-512) in which all three tied at ~0.88–0.90 recall, `fixed` was cheapest, and `semantic` overflowed
12% of chunks with the lowest rerank confidence.*

- **H1 — Quality is dominated by size, not method.** Retrieval metrics will cluster within noise across
  *segmentation strategies* at a fixed budget; chunk *size* will move the needle more than boundary type.
- **H2 — Smaller chunks favour known-item recall.** `fixed_tok256` and `fixed_char512` (small) should
  match or slightly beat the larger configs on recall@1/MRR, because a tight chunk concentrates the
  title's lexical/semantic signal; larger chunks dilute it (and we expect the **mean rerank score to fall
  as chunk size rises**).
- **H3 — The char-vs-token *unit* is nearly neutral once size is matched.** `fixed_char2048` ≈
  `fixed_tok512` on retrieval; the token unit's advantage is **safety and predictability**, not accuracy.
- **H4 — Token-safety is free.** All seven configs report **0 overflow** by construction, with **no
  retrieval penalty** versus the legacy char baseline — i.e., we get guaranteed-no-truncation at no
  quality cost (the central practical claim).
- **H5 — Semantic does not pay for itself here.** `semantic_tokcap` will not beat the cheap fixed
  baselines on these metrics while costing the most (per-document buffer-embedding pass), echoing the
  prior run.
- **H6 — Boundary-aware methods help precision modestly.** If any method edges ahead post-rerank, expect
  `sentence_tok512` (clean sentence boundaries) by a small margin, as in the prior 3-mode run.

**Decision rule.** Recommend the cheapest configuration that is statistically tied with the best on
reranked recall@5 / MRR@10. (Prediction: a small/standard *fixed* token config.)

## 6. Results

*1,500 docs (500 balanced per shard) · 1,000 known-item queries · SFR/4096 across 16 vLLM endpoints.*

**6.1 Retrieval quality** (all within a ~0.01 band — differences are at/below noise)

| config | recall@1 | recall@5 | recall@10 | MRR@10 | nDCG@10 | rerank R@5 | rerank MRR | mean rerank |
|---|---|---|---|---|---|---|---|---|
| fixed_char512 | 0.880 | 0.898 | 0.903 | 0.889 | 0.892 | 0.902 | 0.891 | 8.280 |
| fixed_char2048 | 0.885 | **0.905** | **0.912** | **0.894** | **0.898** | 0.900 | **0.894** | 7.396 |
| fixed_tok256 | 0.882 | 0.898 | 0.903 | 0.890 | 0.893 | **0.905** | 0.893 | **8.315** |
| **fixed_tok512** | 0.883 | 0.897 | 0.905 | 0.890 | 0.893 | 0.902 | **0.894** | 7.861 |
| sentence_tok512 | 0.884 | 0.897 | 0.903 | 0.891 | 0.894 | 0.902 | 0.892 | 7.937 |
| words_tok512 | 0.885 | 0.899 | 0.904 | 0.892 | 0.895 | 0.899 | 0.890 | 8.180 |
| semantic_tokcap | **0.886** | 0.897 | 0.905 | 0.892 | 0.895 | 0.898 | 0.891 | 7.536 |

**6.2 Structure & cost**

| config | #chunks | chunks/doc | median chars | median tok | p95 tok | **over-cap** | ingest s | chunks/s |
|---|---|---|---|---|---|---|---|---|
| fixed_char512 | 149,677 | 99.8 | 512 | 171 | 261 | **0** | 566 | 264 |
| fixed_char2048 | 37,832 | 25.2 | 2048 | 681 | 1000 | **0** | 210 | 180 |
| fixed_tok256 | 108,032 | 72.0 | 722 | 257 | 257 | **0** | 426 | 253 |
| fixed_tok512 | 54,270 | 36.2 | 1456 | 513 | 513 | **0** | 261 | 208 |
| sentence_tok512 | 58,099 | 38.7 | 1276 | 475 | 504 | **0** | 292 | 199 |
| words_tok512 | 77,264 | 51.5 | 1046 | 367 | 419 | **0** | 530 | 146 |
| semantic_tokcap | 29,536 | 19.7 | 1573 | 521 | **2749** | **0** | **1529** | **19** |

**6.3 The three contrasts**
- **(a) char vs token *unit*, size-matched (#2 vs #4):** second-order. `fixed_char2048` (median 681 tok)
  ≈ `fixed_tok512` (513 tok): reranked recall@5 0.900 vs 0.902. The token window buys
  **determinism + a guaranteed cap**, not accuracy.
- **(b) chunk *size* (#3 vs #4):** barely matters for known-item retrieval — 0.905 vs 0.902 reranked
  recall@5, at 2× the chunks (72 vs 36/doc) and 1.6× the ingest time for the smaller size.
- **(c) boundary strategy at a common budget (#4 vs #5/#6/#7):** no boundary-aware method beats the plain
  token window. `fixed_tok256`'s 0.905 is the top reranked recall@5; sentence/words/semantic do not
  separate from it.
- **(d) overflow eliminated:** **all 7 configs = 0 chunks over 4080 tokens.** Note semantic's raw p95 was
  2,749 tokens with a chunk hitting exactly the 4080 cap — i.e., without the cap it *would* overflow;
  the token-sized configs never can, by construction. **Token-safety is structural, not a quality trade.**

**Observation for the deck:** *mean rerank confidence falls as chunk size grows* (8.3 at ~170–260 tok →
7.4–7.9 at ~500–680 tok) — larger chunks dilute the per-chunk relevance signal even when recall is
unchanged. And **chars-per-token ran < 4** on this corpus (2048 chars ≈ 681 tok), the exact drift that
makes character sizing unsafe and motivates token sizing.

## 7. Discussion & recommendation

**Which hypotheses held**

| | prediction | verdict |
|---|---|---|
| H1 | size dominates method | ✅ *stronger* — retrieval near-**invariant** to both (all within ~0.01) |
| H2 | smaller → better recall; rerank score falls with size | ⚠️ **split** — recall flat; but rerank confidence **did** fall with size |
| H3 | char↔token unit ≈ neutral, size-matched | ✅ held (char2048 ≈ tok512) |
| H4 | token-safety is free | ✅✅ **0 overflow all 7, no quality penalty** |
| H5 | semantic doesn't pay for itself | ✅ held — 6× the cost, no gain |
| H6 | sentence edges ahead post-rerank | ❌ refuted — plain `fixed_tok256` led |

**Recommendation for the full production rebuild: `fixed_tok512`.**
On this benchmark every configuration is statistically tied on retrieval quality, so the decision falls to
**safety, determinism, and cost** — where a 512-token sliding window wins: it can never overflow the
embedder window (0/54,270), produces reproducible boundaries, matches the production corpus' effective
chunk size, and ingests **~6× faster than semantic** (261 s vs 1,529 s). `fixed_tok256` is the alternative
if slightly higher rerank confidence is worth 2× the chunk count (storage + index cost). **Semantic is not
justified** by these results.

*Scope caveats (unchanged): title→own-doc is a known-item proxy that flatters lexical matching (we report
fused **and** reranked); single-relevant-doc ground truth; one biomedical corpus; one embedder. A harder
query set (paraphrased questions, multi-relevant docs) could separate the methods more — a sensible
follow-up (tracked as issue #46, cross-system head-to-head).*

## 8. Reproducibility (appendix slide)
- Harness: `python/scripts/eval/chunking_compare_7way.py` (RAGStack); chunkers in
  `ragstack/ingestion/chunkers.py`, token logic in `ragstack/ingestion/tokenization.py` (v0.13.0).
- Embedder: `Salesforce/SFR-Embedding-Mistral` (4096-dim) via vLLM `--runner pooling`, 16 endpoints.
- Stores: Qdrant (dense) + Elasticsearch (BM25) + cross-encoder sidecar; isolated `chunkcmp_m7_*` stores,
  torn down after; production corpus untouched.
- Determinism: fixed subset and query sample seeded; deterministic chunk ids.

## References
- HiPerRAG: High-Performance Retrieval Augmented Generation for Scientific Insights, PASC '25 —
  [arXiv:2505.04846](https://arxiv.org/abs/2505.04846).
- Cross-system chunking justification: `chunking-comparison.md` (this directory).
