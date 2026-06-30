---
marp: true
title: Chunking Strategies for Scientific RAG — RAGStack
author: RAGStack team
theme: default
paginate: true
size: 16:9
header: 'Chunking Strategies for Scientific RAG'
footer: 'RAGStack · 2026'
style: |
  section { font-size: 24px; }
  h1 { color: #1e3a8a; }
  h2 { color: #2563eb; }
  table { font-size: 18px; }
  code { font-size: 18px; }
  .small { font-size: 18px; }
  .accent { color: #2563eb; font-weight: 700; }
  .good { color: #16a34a; font-weight: 700; }
  .warn { color: #dc2626; font-weight: 700; }
---

<!-- _class: lead -->
# Chunking Strategies for Scientific RAG
## A controlled comparison in **RAGStack**

Architecture · Methods · Token-exact sizing · Experimental design · Results

<span class="small">Embedding model: Salesforce/SFR-Embedding-Mistral (4096-dim / 4096-token) · Draft 2026-06-30</span>

---

## Agenda

1. **System & architecture overview** — what RAGStack is and how it serves RAG
2. **Where chunking sits** in the pipeline, and why it decides quality
3. **The chunking algorithms** — fixed (char/token), sentence, words, semantic
4. **Token-exact, lossless sizing** — the controlled variable
5. **Experimental design** — 7 configurations, single-variable contrasts
6. **Hypotheses** (registered before results)
7. **Results & recommendation**

---

# Part I — System & Architecture

---

## RAGStack: what it is

- A **multi-tenant Retrieval-Augmented Generation API** for scientific literature
- **Contract-first & polyglot:** one OpenAPI contract, two implementations
  - Python / FastAPI (`:8000`) · Go / Chi (`:8080`) — verified by a shared conformance suite
- **Hybrid retrieval:** dense vectors **+** lexical BM25 **+** optional knowledge graph → fused → reranked
- **Tenant isolation:** `tenant_id` derived server-side from the API key; reads = own tenant **+** `public`
- **Deterministic provenance:** every chunk keeps exact source offsets + a `uuid5` id → idempotent
  re-ingest, citations, tenant scoping

> Online **serving** system — distinct from offline/HPC batch embedding tools (see ecosystem slide).

---

## Architecture — components

```
                 ┌──────────────────────────────────────────────┐
   client ──▶    │  RAGStack API   (FastAPI :8000 / Go Chi :8080) │
   (X-API-Key)   │  auth → tenant · query · retrieve · ingest     │
                 └───────┬───────────────┬───────────────┬───────┘
                         │               │               │
                   dense │         lexical│          graph │ (optional)
                         ▼               ▼               ▼
                  Qdrant :6333    Elasticsearch    Neo4j :7687
                  (vectors)        :9200 (BM25)    (entities)
                         │               │
                         └──────┬────────┘
                          RRF fusion → Cross-encoder rerank (:50052)
                                          │
                                          ▼
                                   LLM generation
```

<span class="small">Model backends are **sidecars** over HTTP: embedding (:50053), cross-encoder (:50052), faiss (:50051). Persistence: Qdrant · Elasticsearch · Neo4j · Postgres · Redis.</span>

---

## Data flow — ingestion (where chunking lives)

```
 documents (JSONL)
      │
      ▼
  ❶ Load + enrich      doc metadata: DOI, title, authors, citations
      │
      ▼
  ❷ CHUNK   ◀──────────  ★ the subject of this study
      │                   (fixed / sentence / words / semantic; char or token)
      ▼
  ❸ Embed              SFR-Embedding-Mistral, 4096-dim  (vLLM fleet)
      │
      ▼
  ❹ Index              Qdrant (vectors) + Elasticsearch (BM25)
```

**Chunking is step ❷** — it fixes the *unit of retrieval*. Everything downstream can only return what a
chunk boundary captured.

---

## Data flow — query

```
 query ─▶ embed query ─┬─▶ Qdrant dense top-k ─┐
                       └─▶ Elasticsearch BM25  ─┤
                                                ▼
                                    Reciprocal Rank Fusion (RRF)
                                                │
                                  top-50 ─▶ Cross-encoder rerank
                                                │
                                       top-k passages ─▶ LLM answer (+ citations)
```

- The **same retrieval + rerank pipeline** is used to evaluate every chunking configuration
- Only **chunking** changes between experimental arms → a clean single-variable comparison

---

## Deployment & infrastructure

- **Apptainer** (rootless, no Docker) on host *coconut*; every writable path bind-mounted & observable
- **Embedding fleet:** `SFR-Embedding-Mistral` served by **vLLM** (`--runner pooling`) across **16 GPU
  endpoints** (8 local + 8 remote), OpenAI-compatible `/v1/embeddings`, fanned out for throughput
- **Stores:** Qdrant `:6333` · Elasticsearch `:9200` · Neo4j · Postgres · Redis
- **Token budget auto-detected** from the model: `GET /v1/models → max_model_len = 4096`

<span class="small">Same model + fleet used for all experimental arms → fairness held at the infrastructure level.</span>

---

## Ecosystem context

| Stage | System | Mode |
|---|---|---|
| Parse PDFs (Oreo) | *HiPerRAG / pdfwf* | batch / HPC |
| Embed at scale, RAG eval | **distllm** *(= HiPerRAG backbone)* | batch / HPC |
| Batch LLM inference | **ExaForge** (Aurora) | batch / HPC |
| **Serve retrieval online** | **RAGStack** | **online, multi-tenant** |

- The HPC tools optimize **batch throughput** and tolerate truncation
- RAGStack optimizes **online serving** → demands token-exact, **lossless** chunking + provenance
- *This study isolates the chunking stage; it is not a RAG-quality claim against HiPerRAG*

---

# Part II — The Chunking Study

---

## Why chunking decides RAG quality

Chunks are the **unit of retrieval** — a governing tension:

- **Too large** → mixed topics; averaged embedding matches nothing sharply; **may overflow the
  4096-token window** → silent truncation → text *never embedded, never retrievable*
- **Too small** → topical coherence and cross-sentence context lost → recall drops

Two questions:

1. **Which segmentation strategy?** boundary-agnostic windows vs. sentence / word / topic boundaries
2. **Which sizing unit & size?** characters (legacy) vs. **tokens** (what the embedder consumes)

---

## The token budget is a hard wall

- Embedders tokenize to **sub-word tokens** with a fixed maximum (**SFR: 4096**)
- Most chunkers size by **characters** — a proxy that **drifts**: dense text (formulae, references,
  non-Latin) packs more tokens/char → a "safe" char size can still overflow
- Prior tooling on overflow:
  - <span class="warn">silent truncation</span> (drops the tail) · <span class="warn">arbitrary char-cap</span> · <span class="warn">reject the document</span> — all **lossy**
- **RAGStack:** size against the **true token budget** and split **losslessly** → next slide

---

## Methods — fixed windows

**Fixed — character window** (`RecursiveCharacterChunker`)
- Slide an `N`-char window, advance `N − overlap`; boundary-agnostic (cuts mid-word)
- Simplest, cheapest, uniform sizes — the legacy production default

**Fixed — token window** *(new)*
- Tokenize once (model's own tokenizer) keeping a **char-offset map**
- Slide an `N`-**token** window, advance `N − overlap` tokens; map back to exact source spans
- Guarantees ≤ `N` tokens per chunk — boundary-agnostic but **context-window-exact**

---

## Methods — sentence & words

**Sentence** (`SentenceChunker`)
- Segment with **NLTK Punkt** (trained, language-aware — *not* a naive period split)
- **Greedy-pack whole sentences** to the budget; overlap re-adds trailing whole sentences
- Respects sentence boundaries; sizes approximate

**Words** (`WordChunker`)
- Same packing, atomic unit = **whitespace word** (`\S+`, gapless span tiling → exact offsets)
- Finer-grained boundary control than sentences

---

## Methods — semantic (topic boundaries)

`SemanticChunker` — lineage: llama_index (same family as HiPerRAG / distllm)

1. Split into sentences (Punkt)
2. Build **buffers**: sentence *i* ± `buffer_size` neighbours (default 3 → up to 7 sentences)
3. **Embed** every buffer
4. **Cosine distance** between consecutive buffers
5. **Breakpoint** where distance > the `breakpoint_percentile`-th percentile **of this doc's own
   distances** (default 80) — *adaptive per document*, no fixed size
6. Merge chunks < `min_chunk_length`; whole-doc fallback

> Most expensive: one embedding pass **per document** just to find boundaries.

---

## Token-exact, lossless sizing (the controlled variable)

Layered on every method:

- **Counting:** `TokenCounter` — default = the **model's own HF tokenizer** (exact, offline);
  fallbacks: vLLM `/tokenize` endpoint, or char-ratio estimator
- **Budget auto-detected:** `max_model_len` (4096) − 16 reserve → **hard cap 4080**
- **Lossless:** over-budget units **split by tokens** — <span class="good">no chunk overflows, no text dropped</span>
  - sentence/words: token value = *packing budget* · semantic: token value = *overflow guard*

<span class="small">Contrast with batch tools: truncate (lossy) / char-cap (arbitrary) / reject (lossy).</span>

---

## Experimental design — 7 configurations

Same **1,500 article docs** (balanced 500/500/500 across 3 shards) feed every arm; only chunking varies.

| # | key | method | sizing | params |
|---|---|---|---|---|
| 1 | `fixed_char512` | fixed | char | 512 / 64 — legacy baseline |
| 2 | `fixed_char2048` | fixed | char | 2048 / 256 — size-matched control |
| 3 | `fixed_tok256` | fixed | token | 256 / 32 |
| 4 | `fixed_tok512` | fixed | token | 512 / 64 |
| 5 | `sentence_tok512` | sentence | token | ≤512 tok |
| 6 | `words_tok512` | words | token | ≤512 tok |
| 7 | `semantic_tokcap` | semantic | adaptive+cap | buf 3 / pct 80 / min 500 / cap 4080 |

---

## What the design isolates

- **Chunk size:** #1↔#2 (char) and #3↔#4 (token)
- **Char vs token *unit*, size-matched:** **#2 (2048 char ≈ 512 tok) vs #4 (512 tok)**
- **Boundary strategy at a common budget:** #4 vs #5 / #6 / #7
- **Token-safety:** uniform 4080 cap → *overflow is measured, never suffered*

**Held constant:** embedder (SFR/4096, 16 endpoints) · subset · query set · retrieval+rerank pipeline.

---

## Evaluation — known-item retrieval

- **Query = document title**; correct result = a chunk from that same document
- Deterministic **1,000-query** sample
- Metrics (dense+BM25 **and** after rerank):
  - **recall@{1,5,10}** · **MRR@10** · **nDCG@10** · mean top-1 rerank score
- Also recorded per config: #chunks, chunks/doc, size in **chars *and* tokens** (median/p95),
  **over-cap count**, ingest time, throughput

<span class="small">Caveats: title-query flatters lexical match (report both fused & reranked); single-relevant-doc; one domain (biomedical).</span>

---

## Hypotheses (registered before results)

<span class="small">Informed by a prior full-corpus 3-mode run: all tied ~0.88–0.90 recall; `fixed` cheapest; `semantic` overflowed 12% & lowest rerank confidence.</span>

- **H1** Quality dominated by **size, not method** (strategies cluster within noise at fixed budget)
- **H2** **Smaller chunks → better known-item recall**; mean rerank score falls as size rises
- **H3** Char-vs-token **unit ≈ neutral** once size-matched (#2 ≈ #4); token's gain is *safety*
- **H4** **Token-safety is free** — 0 overflow, no quality penalty vs legacy char baseline
- **H5** **Semantic doesn't pay for itself** here (most cost, no quality gain)
- **H6** If anything edges ahead post-rerank, it's **sentence**, by a small margin

**Decision rule:** cheapest config statistically tied with the best on reranked recall@5 / MRR@10.

---

## Results — retrieval quality  *(to be populated)*

| config | R@1 | R@5 | R@10 | MRR@10 | nDCG@10 | rerank R@5 | rerank MRR | mean rerank |
|---|---|---|---|---|---|---|---|---|
| fixed_char512 | … | | | | | | | |
| fixed_char2048 | … | | | | | | | |
| fixed_tok256 | … | | | | | | | |
| fixed_tok512 | … | | | | | | | |
| sentence_tok512 | … | | | | | | | |
| words_tok512 | … | | | | | | | |
| semantic_tokcap | … | | | | | | | |

---

## Results — structure & cost  *(to be populated)*

| config | #chunks | chunks/doc | median tok | p95 tok | over-cap | ingest s | chunks/s |
|---|---|---|---|---|---|---|---|
| … | | | | | | | |

**The three contrasts (fill from data):**
- (a) char vs token *unit*, size-matched · (b) chunk *size* · (c) boundary strategy · (d) overflow eliminated

---

## Discussion & recommendation  *(to be populated)*

- Which hypotheses held / were refuted
- **Recommended configuration** for the full production rebuild (3 shards, ~2.5M chunks)
- Cost/quality trade-off summary

---

## Reproducibility

- **Harness:** `python/scripts/eval/chunking_compare_7way.py`
- **Chunkers:** `ragstack/ingestion/chunkers.py` · **token logic:** `ragstack/ingestion/tokenization.py` (v0.13.0)
- **Embedder:** SFR-Embedding-Mistral (4096) via vLLM, 16 endpoints
- **Stores:** isolated `chunkcmp_m7_*` (torn down after); production corpus untouched
- **Determinism:** seeded subset + query sample; deterministic chunk ids

---

<!-- _class: lead -->
## Thank you

**Chunking Strategies for Scientific RAG — RAGStack**

References:
HiPerRAG (PASC '25) — arXiv:2505.04846 · Cross-system justification: `chunking-comparison.md`

<span class="small">Export: `marp chunking-comparison-deck.md --pptx` (or `--pdf`)</span>
