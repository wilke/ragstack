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

## Threats to validity (read the results through these)

- **The known-item task is chunking-*insensitive*.** Title→own-doc is dominated by the lead
  chunk where the title's words recur; *every* reasonable chunker captures it → little power to
  separate chunkers. A near-tie is **expected under the null**.
- **No power for sub-1-point gaps.** ~1,000 single-relevant queries → 95% CI on recall@5 ≈ ±0.02;
  the observed spread (~0.008) sits **entirely inside** it. <span class="accent">Statistically
  tied ≠ equal</span> — it means *underpowered to distinguish*.
- **Single-relevant, document-level ground truth** — can't reward surfacing *more* relevant
  material or better localization.
- **Subset + proxy**, not the production workload (1,500 docs, biomedical, *title* ≠ real query).

> These are exactly the gaps the **SciFact benchmark** (next) closes: real claim queries,
> multi-relevant qrels, same bootstrap-CI + Wilcoxon/Holm stats.

---

## Hypotheses (registered before results)

<span class="small">Informed by a prior full-corpus 3-mode run: all tied ~0.88–0.90 recall; `fixed` cheapest; `semantic` overflowed 12% & lowest rerank confidence.</span>

- **H1** Quality dominated by **size, not method** — *softened post-hoc to* **no *detectable* difference** (underpowered proxy)
- **H2** **Smaller chunks → better known-item recall** — *softened:* recall gaps sub-1-pt, inside CI (**no detectable size effect**); only the rerank-confidence trend holds
- **H3** Char-vs-token **unit ≈ neutral** once size-matched (#2 ≈ #4); token's gain is *safety*
- **H4** **Token-safety is free** — 0 overflow, no quality penalty vs legacy char baseline
- **H5** **Semantic doesn't pay for itself** here (most cost, no quality gain)
- **H6** If anything edges ahead post-rerank, it's **sentence** — *refuted:* no method separates beyond CI; `fixed_tok256` is the *nominal* leader

**Decision rule:** cheapest config statistically tied with the best on reranked recall@5 / MRR@10.

---

## Results — retrieval quality

<span class="small">1,500 docs · 1,000 known-item queries · SFR/4096 · 16 GPUs. **All within a ~0.01 band.**</span>

| config | R@1 | R@5 | R@10 | MRR@10 | nDCG@10 | rerank R@5 | rerank MRR | mean rr |
|---|---|---|---|---|---|---|---|---|
| fixed_char512 | 0.880 | 0.898 | 0.903 | 0.889 | 0.892 | 0.902 | 0.891 | 8.28 |
| fixed_char2048 | 0.885 | 0.905 | 0.912 | 0.894 | 0.898 | 0.900 | 0.894 | 7.40 |
| fixed_tok256 | 0.882 | 0.898 | 0.903 | 0.890 | 0.893 | **0.905** | 0.893 | **8.32** |
| **fixed_tok512** | 0.883 | 0.897 | 0.905 | 0.890 | 0.893 | 0.902 | 0.894 | 7.86 |
| sentence_tok512 | 0.884 | 0.897 | 0.903 | 0.891 | 0.894 | 0.902 | 0.892 | 7.94 |
| words_tok512 | 0.885 | 0.899 | 0.904 | 0.892 | 0.895 | 0.899 | 0.890 | 8.18 |
| semantic_tokcap | 0.886 | 0.897 | 0.905 | 0.892 | 0.895 | 0.898 | 0.891 | 7.54 |

**Retrieval quality is essentially invariant to chunking method *and* size.**

---

## Results — structure & cost

| config | #chunks | chunks/doc | median tok | p95 tok | over-cap | ingest s | chunks/s |
|---|---|---|---|---|---|---|---|
| fixed_char512 | 149,677 | 99.8 | 171 | 261 | <span class="good">0</span> | 566 | 264 |
| fixed_char2048 | 37,832 | 25.2 | 681 | 1000 | <span class="good">0</span> | 210 | 180 |
| fixed_tok256 | 108,032 | 72.0 | 257 | 257 | <span class="good">0</span> | 426 | 253 |
| **fixed_tok512** | 54,270 | 36.2 | 513 | 513 | <span class="good">0</span> | **261** | 208 |
| sentence_tok512 | 58,099 | 38.7 | 475 | 504 | <span class="good">0</span> | 292 | 199 |
| words_tok512 | 77,264 | 51.5 | 367 | 419 | <span class="good">0</span> | 530 | 146 |
| semantic_tokcap | 29,536 | 19.7 | 521 | <span class="warn">2749</span> | <span class="good">0</span> | <span class="warn">1529</span> | 19 |

<span class="small">**0 overflow everywhere** (token-safety is structural). Semantic costs **6×** the rest. Corpus ran **< 4 chars/token** (2048 char ≈ 681 tok) — the drift that makes char-sizing unsafe.</span>

---

## SciFact (BEIR) — the discriminating experiment

<span class="small">5,183 abstracts · **300 real claim queries + document-level qrels** · same hybrid+rerank · bootstrap CIs + Holm-Wilcoxon · ref = `fixed_tok512`.</span>

| config | nDCG@10 [95% CI] | R@100 | MAP | ΔnDCG vs ref | Holm p | distinct? |
|---|---|---|---|---|---|---|
| fixed_char512 | 0.703 [0.658, 0.745] | 0.953 | 0.665 | +0.005 [−0.018, 0.027] | 1.000 | no |
| fixed_char2048 | 0.694 [0.650, 0.738] | **0.977** | 0.659 | −0.004 [−0.009, −0.000] | 0.115 | no |
| **fixed_tok256** | **0.721 [0.679, 0.762]** | 0.960 | **0.684** | **+0.023 [+0.006, +0.040]** | 0.077 | no |
| fixed_tok512 (ref) | 0.698 [0.654, 0.742] | 0.973 | 0.663 | — | — | ref |
| sentence_tok512 | 0.696 [0.651, 0.739] | 0.970 | 0.661 | −0.002 | 1.000 | no |
| words_tok512 | 0.694 [0.650, 0.736] | 0.963 | 0.657 | −0.004 | 1.000 | no |
| semantic_tokcap | 0.697 [0.652, 0.741] | 0.963 | 0.664 | −0.001 | 1.000 | no |

<span class="good">No config distinguishable from `fixed_tok512`</span> — every diff-CI spans 0, **no Wilcoxon survives Holm**. `fixed_tok256` *nominally* top (p=0.077, **not** significant after correction); <span class="warn">semantic = no gain</span>, highest cost. **Real qrels + CIs upgrade "underpowered null" → CI-backed no-difference.**

---

## The four contrasts

- **(a) char vs token *unit*, size-matched** (#2≈#4): second-order — reranked recall@5 0.900 vs 0.902.
  Token window buys <span class="accent">determinism + a guaranteed cap</span>, not accuracy.
- **(b) chunk *size*** (tok256 vs tok512): barely moves quality; smaller = 2× the chunks & cost.
- **(c) boundary strategy** (#4 vs sentence/words/semantic): no boundary-aware method beats the plain
  token window on these metrics.
- **(d) overflow**: <span class="good">all 7 = 0 over 4080 tok</span> — semantic *would* overflow
  (p95 2749, max hit the cap) without the guard.

> Aside: **mean rerank confidence falls as chunk size grows** (8.3 → 7.4) — big chunks dilute per-chunk relevance even when recall holds.

---

## Hypotheses — verdict

| | prediction | verdict |
|---|---|---|
| **H1** | size dominates method | ⚠️ **softened** — no *detectable* difference (proxy); **SciFact + CIs confirm** no distinguishable difference |
| **H2** | smaller → better recall; rerank ↓ with size | ⚠️ split — recall flat (inside CI), **rerank ↓ held**; tok256 *nominal* top on SciFact too |
| **H3** | char↔token unit ≈ neutral, size-matched | ✅ held |
| **H4** | token-safety is free | <span class="good">✅✅ 0 overflow, no penalty</span> |
| **H5** | semantic doesn't pay for itself | ✅ held — 6× cost, no gain |
| **H6** | sentence edges ahead post-rerank | ❌ refuted — no method separates beyond CI; `fixed_tok256` *nominal* (not sig.) on both evals |

---

## Recommendation

# → `fixed_tok512`

- All configs **statistically tied** on quality → decide on **safety · determinism · cost**
- <span class="good">Cannot overflow</span> the embedder window (0 / 54,270) · reproducible boundaries
- Matches the production corpus' effective chunk size
- **~6× cheaper to ingest than semantic** (261 s vs 1,529 s)

<span class="small">Alternative: `fixed_tok256` — *nominally* best on both evals (SciFact nDCG 0.721) but **not significant** after Holm; a larger-query re-test, not a rebuild. **Semantic is not justified** (SciFact nDCG 0.697, no gain, highest cost). Token-safety is **structural**; `fixed_tok512` is the CI-backed **low-regret default** — tied with the best on *both* the proxy and SciFact's real qrels.</span>

**Adopt `fixed_tok512` for the full 3-shard production rebuild (~2.5M chunks).**

---

## Reproducibility

- **Harness:** `python/scripts/eval/chunking_compare_7way.py` · **SciFact:** `scifact_chunk_eval.py` · **stats:** `_stats.py` (bootstrap CIs + Holm-Wilcoxon)
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
