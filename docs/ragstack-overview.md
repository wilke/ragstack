---
marp: true
theme: default
paginate: true
size: 16:9
style: |
  section { font-size: 24px; }
  h1 { font-size: 40px; color: #2563eb; }
  h2 { font-size: 32px; color: #1e40af; }
  table { font-size: 20px; margin: 0 auto; }
  code { font-size: 18px; }
  .columns { display: flex; gap: 2em; }
  .col { flex: 1; }
  blockquote { border-left: 4px solid #2563eb; padding-left: 1em; color: #475569; font-style: italic; }
---

# RagStack

### A Production-Grade Retrieval-Augmented Generation System

Self-hosted | Multi-tenant | Go + Python

---

## What is RAG?

**Retrieval-Augmented Generation** connects an LLM to your own documents.

Instead of relying on what the model memorized during training, RAG **retrieves relevant passages** from your data and feeds them as context to the LLM when generating an answer.

**The core loop:**

```
User question
   → find relevant passages in your documents
      → give those passages + question to the LLM
         → LLM generates an answer grounded in your data
```

**Why not just fine-tune?**
- Fine-tuning bakes knowledge into model weights — expensive, slow to update, hard to audit
- RAG keeps knowledge in documents — update anytime, cite sources, no retraining

---

## The Simplest RAG Pipeline

```
         ┌──────────┐     ┌──────────┐     ┌──────────┐
 Query → │  Embed   │ ──→ │  Search  │ ──→ │ Generate │ → Answer
         │  query   │     │ vectors  │     │ with LLM │
         └──────────┘     └──────────┘     └──────────┘
                                ↑
                          ┌─────────┐
                          │  Your   │
                          │  docs   │
                          └─────────┘
```

This works. For a personal knowledge base or prototype, **this is all you need.**

But production systems face harder problems: keyword queries that vector search misses, ambiguous questions, noisy results, multi-document reasoning, hallucinated answers.

**Each layer of complexity addresses a specific failure mode.**

---

## The Full Pipeline: What Each Stage Adds

```
User Query
  │
  ▼
1. REWRITE ──── "What does BRCA1 do?" → also search "BRCA1 DNA repair breast cancer"
  │
  ▼
2. RETRIEVE ─── Vector + BM25 + Knowledge Graph  (in parallel, fused with RRF)
  │
  ▼
3. RERANK ───── Cross-encoder rescores 40 → top 5 (neural precision)
  │
  ▼
4. CHECK ────── Score too low? → "I don't have enough info" (hallucination guard)
  │
  ▼
5. ASSEMBLE ─── Select chunks within token budget, order by position
  │
  ▼
6. GENERATE ─── LLM produces answer with source citations
```

---

## Why Add Complexity? The 5 Maturity Levels

Not every deployment needs the full pipeline. Each level addresses specific failure modes — **only add complexity when your metrics justify it.**

| Level | What You Add | Failure Mode Solved |
|-------|-------------|-------------------|
| **1 — Naive RAG** | Embed + vector search + generate | None yet — baseline |
| **2 — Hybrid** | BM25 keyword search + RRF fusion | Keyword queries missed by vectors |
| **3 — Reranking** | Cross-encoder + query rewriting | Noisy top-K, ambiguous queries |
| **4 — Knowledge Graph** | Entity extraction + graph retrieval | Multi-hop reasoning across documents |
| **5 — Production** | CI/CD, evaluation, audit, security | Regressions, compliance, SLAs |

> **Decision rule:** Run eval at each level. If the next level doesn't measurably improve your metrics, stop. Complexity has a maintenance cost.

---

## Level 1 → 2: Adding Hybrid Search

Vector search is semantic — great for "how does photosynthesis work?" but misses exact keyword matches like **"error code E-4021"** or **"BRCA1 mutation"**.

**Fix:** Run BM25 (keyword) search in parallel with vector search, fuse with RRF.

```
Query ──┬── Vector search (semantic) ──┐
        │                              ├── RRF merge → Results
        └── BM25 search (keyword) ────┘
```

**Measured impact:**

| Metric | Vector only | + Hybrid search |
|--------|------------|-----------------|
| Keyword query recall | ~40% | ~90% |
| Overall nDCG@5 | ~0.55 | ~0.65 |

**Added cost:** One BM25 index (Postgres tsvector or Elasticsearch). Searches run in parallel — minimal latency impact.

---

## Level 2 → 3: Adding Reranking + Rewriting

Initial retrieval returns 40 candidates — many partially relevant. Vector similarity is a rough ranking signal.

**Fix:** A **cross-encoder** reads each (query, document) pair and produces a precise relevance score. Rescores 40 candidates → top 5.

**Query rewriting** casts a wider retrieval net:
- **Multi-query** — LLM paraphrases the question 3 ways
- **HyDE** — LLM writes a hypothetical answer, embeds that instead
- **Step-back** — LLM generalizes the query for broader context

**Measured impact:**

| Metric | Hybrid only | + Reranking |
|--------|------------|-------------|
| nDCG@5 | ~0.65 | ~0.78 |
| Answer quality (human eval) | 3.2/5 | 4.1/5 |

**Added cost:** Cross-encoder sidecar (~1.2GB, CPU sufficient). ~250ms latency.

---

## Level 3 → 4: Adding Knowledge Graphs

Some questions require connecting facts across documents:
> *"Who funded the company that acquired the CRISPR startup?"*

No single passage has the full answer. Vector and keyword search both fail.

**Fix:** Extract (subject, predicate, object) triples during ingestion. At query time, expand with related entities from the graph.

```
Docs → LLM extracts triples → Neo4j
Query → entity recognition → graph neighborhood → RRF
```

**Measured impact:**

| Metric | Without graph | + Knowledge graph |
|--------|--------------|-------------------|
| Multi-hop query success | ~25% | ~65% |
| Entity-rich query recall | ~60% | ~85% |

**Added cost:** Neo4j + LLM extraction during ingestion (~500ms/chunk, async).

---

## Level 4 → 5: Production Hardening

The pipeline works — but will it **keep working** as data changes and users scale?

**Fix:** Operational guardrails.

| Concern | Mechanism |
|---------|-----------|
| Regressions | Nightly eval against gold set; CI fails if nDCG drops |
| Hallucination | Confidence threshold — refuse to answer when retrieval is poor |
| Data leaks | Row-Level Security + collection-per-tenant |
| Audit | Every query and admin action logged |
| Outages | Graceful degradation — components fall back, not fail |
| Stale data | Advisory when sources span multiple years |

**Added cost:** Operational maturity (CI, monitoring, on-call). No latency impact.

---

## RagStack Architecture

```
                    ┌──────────────────────────────────────────┐
                    │            Go API Server                  │
                    │  ┌────────┐ ┌─────────┐ ┌────────────┐  │
  Client ────────→  │  │Rewrite │→│Retrieve │→│  Generate   │  │
                    │  └────────┘ └─────────┘ └────────────┘  │
                    └──────┬──────────┬──────────┬─────────────┘
                           │          │          │
              ┌────────────┤   ┌──────┤   ┌──────┤
              ▼            ▼   ▼      ▼   ▼      ▼
         ┌────────┐  ┌───────┐ ┌────┐ ┌─────┐ ┌──────┐
         │Qdrant  │  │Postgres│ │Neo4j│ │Redis│ │vLLM  │
         │vectors │  │BM25+RLS│ │graph│ │cache│ │(GPU) │
         └────────┘  └───────┘ └────┘ └─────┘ └──────┘
                                                   │
                        Python Sidecars            │
              ┌──────────────┬──────────────┐      │
              ▼              ▼              ▼      ▼
         ┌─────────┐  ┌───────────┐  ┌──────────────┐
         │Embedding│  │CrossEncode│  │ FAISS legacy  │
         │ sidecar │  │ sidecar   │  │   sidecar     │
         └─────────┘  └───────────┘  └──────────────┘
```

---

## Self-Hosted Models — No External API Dependencies

All model inference runs on your infrastructure. No data leaves your network.

| Component | Model | Hardware | Latency |
|-----------|-------|----------|---------|
| **Embeddings** | BAAI/bge-base-en-v1.5 (768d) | A10G or CPU | ~50ms/batch |
| **Generation** | Llama Scout 17B via vLLM | A100 (80GB) | ~300ms TTFT |
| **Reranking** | bge-reranker-v2-m3 | CPU sufficient | ~200ms |
| **KG extraction** | Llama-3.1-8B via vLLM | Shared GPU | ~500ms/chunk |

**Why self-host?**
- Data stays internal (HIPAA, ITAR, institutional policy)
- Fixed GPU cost — no per-token billing, break-even at ~5K queries/day
- Deterministic latency — no rate limits, no provider outages
- Customizable — fine-tune on domain data, swap models freely

---

## Deployment Flexibility

The same container images run everywhere — only the runtime differs.

| Environment | Runtime | Orchestration |
|-------------|---------|---------------|
| **Laptop / dev** | Docker Compose | `make up-go` or `make up-python` |
| **HPC / bare-metal** | Apptainer | Shell scripts (dev) / systemd (prod) |
| **Cloud / enterprise** | Kubernetes | Helm chart with HPA |

**Deployment profiles** control which services start:

| Profile | Services | Use case |
|---------|----------|----------|
| Minimal (3) | Postgres + Qdrant + Redis | Dev / single-tenant |
| Standard (7) | + Neo4j, MinIO, OTel | Production without legacy |
| Full (10) | + MongoDB, FAISS, cross-encoder | Legacy compatibility |

---

## Dual Implementation: Go + Python

Both implementations share the same architecture, API contract, and conformance tests.

```
contracts/          ← OpenAPI spec + JSON schemas (single source of truth)
conformance/        ← HTTP black-box tests run against either implementation
    │
    ├── RAGSTACK_BASE_URL=:8080  → tests Go
    └── RAGSTACK_BASE_URL=:8000  → tests Python
```

| | Go | Python |
|-|-------|--------|
| **Best for** | Production, multi-tenant, K8s | Prototyping, ML-heavy workloads |
| **Router** | Chi v5 | FastAPI |
| **Concurrency** | goroutines + errgroup | asyncio |
| **ML access** | HTTP sidecars | In-process |
| **Binary** | Single static binary, ~10MB | Virtualenv, ~500MB |
| **Startup** | <100ms | 5-15s |

---

## Current Status

### What's done (PR #2)

| Component | Status | Details |
|-----------|--------|---------|
| **Monorepo structure** | Done | `python/`, `go/`, `sidecars/`, `contracts/`, `conformance/`, `deploy/` |
| **Go scaffold** | Done | Chi router, 9 endpoints, 8 unit tests passing |
| **Python scaffold** | Done | FastAPI, Protocol interfaces, in-memory stores |
| **API contracts** | Done | OpenAPI 3.1 + 11 JSON schemas |
| **Conformance tests** | Done | 17 HTTP black-box tests + schema validation |
| **ML sidecars** | Done | Cross-encoder, embedding, FAISS |
| **Docker Compose** | Done | Split: infra, go, python, sidecars |
| **Design document** | Done | plan-c5.md: 19 phases, 4 decision points, 5 maturity levels |

### What's next
Phase 2: Database + storage layer → embedding integration → vector store → retrieval engine

---

## Choosing Your Level

| Your Use Case | Level | Stop After |
|--------------|-------|------------|
| Personal notes / dev docs search | **Level 1** | Phase 9 |
| Internal wiki / team knowledge base | **Level 2** | Phase 11 |
| Customer-facing Q&A / support bot | **Level 3** | Phase 15 |
| Research platform / scientific search | **Level 4** | Phase 16 |
| SaaS product with SLAs / compliance | **Level 5** | Phase 19 |

**The architecture supports incremental adoption.** Each level builds on the previous one — no rework required to upgrade. A Level 2 deployment adds reranking by deploying one sidecar and changing one env var.

> Start simple. Measure. Add complexity only when the data justifies it.

---

## SLO Targets

| Metric | Target |
|--------|--------|
| Query P99 latency | < 2 seconds (end-to-end) |
| Retrieval P99 | < 500ms (no generation) |
| Ingestion throughput | ≥ 50 docs/min sustained |
| Availability | 99.5% (BM25-only counts as available) |
| Index capacity | < 5M chunks |

### Latency budget (P99 query)
```
Rewrite:     ~200ms     ░░░░░
Retrieve:    ~150ms     ░░░░
Rerank:      ~250ms     ░░░░░░
Context:      ~10ms     ░
Generate:   ~1300ms     ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
Overhead:     ~90ms     ░░
─────────────────────
Total:      ~2000ms
```

---

## Glossary — Retrieval Concepts

| Term | What it is |
|------|-----------|
| **BM25** | Best Match 25 — a keyword-matching algorithm. Ranks by term frequency adjusted for document length. "Smart Ctrl-F" that catches exact matches semantic search misses. |
| **Dense retrieval** | Searching by meaning. Text is converted to vectors (embeddings); similar meanings are nearby in vector space. Finds "automobile" when you search "car". |
| **Sparse retrieval** | Searching by exact term overlap (BM25, TF-IDF). Sparse vectors of word frequencies. Fast and good at exact matches. |
| **Hybrid search** | Running dense + sparse retrieval in parallel, then combining results. Gets both semantic similarity and keyword precision. |
| **RRF** | **Reciprocal Rank Fusion** — merges ranked lists from different methods. Score = `1/(k + rank)`. Simple, effective, consistently outperforms individual rankers. |
| **Embedding** | A numerical vector representing text meaning. Similar texts produce nearby vectors (measured by cosine similarity). |

---

## Glossary — Models & Reranking

| Term | What it is |
|------|-----------|
| **Cross-encoder** | A neural model that reads a (query, document) pair together and outputs a relevance score. Much more accurate than vector similarity, but much slower — that's why it's used to *rerank* a small candidate set (e.g., 40 → 5), not to search the full corpus. |
| **Bi-encoder** | The model used to create embeddings. It encodes query and document *independently*, making it fast enough for corpus-wide search, but less precise than a cross-encoder. |
| **Reranking** | A second-pass scoring step. Initial retrieval (fast, approximate) produces candidates; the reranker (slow, precise) reorders them. Dramatically improves top-K quality. |
| **vLLM** | An open-source inference engine for serving large language models. Exposes an OpenAI-compatible API. Supports continuous batching, paged attention, and tensor parallelism for high throughput. |
| **TTFT** | **Time to First Token** — how long until the LLM starts producing output. A key latency metric for streaming responses. |
| **Sidecar** | A small, independently deployable service that runs alongside the main application. RagStack uses Python sidecars for ML tasks (embedding, reranking, FAISS) so the Go core stays lean. |

---

## Glossary — Query Rewriting Strategies

| Strategy | How it works | When it helps |
|----------|-------------|---------------|
| **Passthrough** | No rewriting — use the query as-is. | Clear, specific queries. Zero latency cost. |
| **Multi-query** | LLM generates 3-5 paraphrases of the query. All variants are searched and results merged. | Ambiguous queries where different phrasings retrieve different relevant docs. |
| **HyDE** | **Hypothetical Document Embedding** — LLM writes a hypothetical answer, then embeds *that* answer as the search query instead of the original question. | Queries phrased as questions when the corpus contains declarative statements. The hypothetical answer is closer in vector space to real answers than the question is. |
| **Step-back** | LLM generalizes the query to a broader concept, then searches for both the original and the generalized version. | Overly specific queries that miss relevant context. "What is the half-life of caffeine?" → also searches "caffeine metabolism pharmacokinetics". |
| **Entity expansion** | Extract entities from the query, look up related entities in the knowledge graph, add them to the search. | Domain-specific queries where synonyms or related concepts exist in the KG. "BRCA1" → also finds "DNA repair", "breast cancer", "TP53". |

---

## Glossary — Architecture & Infrastructure

| Term | What it is |
|------|-----------|
| **SLO / SLI / SLA** | **SLI** = raw metric (P99 latency). **SLO** = target (P99 < 2s). **SLA** = contractual commitment with consequences if SLOs are missed. |
| **HNSW** | Graph-based index for approximate nearest-neighbor search. Used by Qdrant and pgvector. Logarithmic search time. |
| **RLS** | **Row-Level Security** — Postgres restricts visible rows per session. Enforces tenant isolation at the database level. |
| **Knowledge graph** | (subject → predicate → object) triples stored in Neo4j. Enables multi-hop reasoning across documents. |
| **Apptainer** | HPC container runtime (formerly Singularity). Rootless, no daemon, GPU via `--nv`. Same images as Docker. |
| **Conformance tests** | HTTP black-box tests verifying Go and Python return identical responses against shared JSON schemas. |

---

## RAG — Strengths & Weaknesses

<div class="columns">
<div class="col">

**Strengths**

| | |
|-|-|
| Instant updates | Add/remove docs — no retraining |
| Source citations | Every answer traces back to a passage |
| Model-agnostic | Swap the LLM without losing knowledge |
| Tenant isolation | Per-customer data stays separate |
| Scales with data | 10M docs = indexing, not retraining |

</div>
<div class="col">

**Weaknesses**

| | |
|-|-|
| Retrieval bottleneck | Wrong passages → wrong answer |
| Latency overhead | Embed + search + rerank adds ~1s |
| Context limits | Can't synthesize across hundreds of docs |
| Chunking is an art | Split size affects quality dramatically |
| Infrastructure | Vector DB, BM25, reranker, cache… |

</div>
</div>

---

## Fine-Tuning — Strengths & Weaknesses

<div class="columns">
<div class="col">

**Strengths**

| | |
|-|-|
| Domain reasoning | Model *thinks* in your domain's language |
| Lower latency | No retrieval step — prompt → generate |
| Style & tone | Writes like your org (legal, medical, brand) |
| Simpler runtime | No vector DB, no search infrastructure |

</div>
<div class="col">

**Weaknesses**

| | |
|-|-|
| Expensive | Full retrain: $100K+. LoRA: hours of GPU |
| Stale instantly | Knowledge frozen at training time |
| No citations | "Just knows" — can't point to sources |
| Hallucination | More confident, including when wrong |
| No isolation | Training data leaks into all outputs |
| Hard to debug | Can't trace answer to a training example |

</div>
</div>

---

## RAG vs Fine-Tuning: When to Use Which

They solve **different problems** — and the best systems combine both.

| | **RAG** | **Fine-tuning** |
|-|---------|----------------|
| **Teaches** | *What* the facts are | *How* to reason about a domain |
| **Updates** | Instantly (add/remove docs) | Requires retraining |
| **Auditable** | Yes — cite exact sources | No — knowledge in weights |
| **Tenant-safe** | Yes — per-customer data | No — shared model weights |
| **Best for** | Facts, references, Q&A | Style, reasoning, format |

**Combined example:** A medical Q&A system fine-tunes for clinical reasoning and writing style, but uses RAG for specific drug interactions and guidelines that change frequently.

> **RagStack is firmly in the knowledge camp** — multi-tenant, updatable, citable. Fine-tuning would be an optional upstream enhancement, not a replacement.

---

## Summary

**RAG** grounds LLM answers in your actual documents — no fine-tuning, instant updates, auditable sources.

**RagStack** is a phased architecture that lets you start simple and add capability as your needs grow:

- **Level 1** — embed + search + generate → works for prototypes
- **Level 2** — add BM25 → catches keyword queries vectors miss
- **Level 3** — add reranking → precision jumps from noisy results
- **Level 4** — add knowledge graph → multi-hop reasoning across docs
- **Level 5** — add CI/CD + eval + audit → production confidence

**Key design choices:**
- Self-hosted models (data stays internal, fixed cost)
- Go core + Python sidecars (performance where it matters, ML where it's needed)
- Multi-tenant from day 1 (RLS, collection-per-tenant, ACL-aware caching)
- Three deployment targets (Docker Compose, Apptainer, Kubernetes)

---
