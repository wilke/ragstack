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

### The problem
Vector search is semantic — great for "how does photosynthesis work?" but misses exact keyword matches like **"error code E-4021"** or **"BRCA1 mutation"**.

### The solution
Run BM25 (keyword) search **in parallel** with vector search, then fuse results using **Reciprocal Rank Fusion (RRF)**.

```
Query ──┬── Vector search (semantic) ──┐
        │                              ├── RRF merge ──→ Results
        └── BM25 search (keyword) ────┘
```

### The benefit
| Metric | Level 1 (vector only) | Level 2 (hybrid) |
|--------|----------------------|------------------|
| Keyword query recall | ~40% | ~90% |
| Overall nDCG@5 | ~0.55 | ~0.65 |

**Cost:** One BM25 index (Postgres tsvector or Elasticsearch). Minimal latency impact — searches run in parallel.

---

## Level 2 → 3: Adding Reranking + Rewriting

### The problem
Initial retrieval returns 40 candidates — many are partially relevant. The **ranking order** matters for generation quality, but vector similarity is a rough signal.

### The solution
A **cross-encoder** model reads each (query, document) pair together and produces a precise relevance score. It rescores 40 candidates down to the top 5.

**Query rewriting** generates alternative phrasings to cast a wider retrieval net:
- **Multi-query:** LLM paraphrases the question 3 ways
- **HyDE:** LLM writes a hypothetical answer, then searches for passages similar to it
- **Step-back:** LLM generalizes the question for broader context

### The benefit
| Metric | Level 2 | Level 3 |
|--------|---------|---------|
| nDCG@5 | ~0.65 | ~0.78 |
| Answer quality (human eval) | 3.2/5 | 4.1/5 |

**Cost:** Cross-encoder sidecar (~1.2GB memory, CPU is sufficient). ~250ms added latency.

---

## Level 3 → 4: Adding Knowledge Graphs

### The problem
Some questions require connecting information across documents:
> *"Who funded the company that acquired the startup working on CRISPR delivery?"*

No single passage contains the full answer. Vector and keyword search both fail.

### The solution
Extract **(subject, predicate, object)** triples from documents during ingestion. Store in Neo4j. At query time, expand the query with related entities from the graph.

```
Docs → LLM extracts triples → Neo4j graph
                                    ↓
Query → entity recognition → graph neighborhood → synthetic chunks → RRF
```

### The benefit
| Metric | Level 3 | Level 4 |
|--------|---------|---------|
| Multi-hop query success | ~25% | ~65% |
| Entity-rich query recall | ~60% | ~85% |

**Cost:** Neo4j instance + LLM extraction during ingestion (~500ms/chunk, async).

---

## Level 4 → 5: Production Hardening

### The problem
The pipeline works, but you need confidence it will **keep working** as data changes, models update, and users scale.

### The solution

| Concern | Mechanism |
|---------|-----------|
| **Regression detection** | Nightly eval against gold set; CI fails if nDCG drops |
| **Hallucination guard** | Confidence threshold — refuse to generate when retrieval quality is low |
| **Data isolation** | Row-Level Security + collection-per-tenant in Qdrant |
| **Audit trail** | Every query, every admin action logged with tenant context |
| **Graceful degradation** | If reranker/graph/vector store fails, pipeline falls back instead of erroring |
| **Freshness conflicts** | Advisory when sources span multiple years |

**Cost:** Operational maturity — CI pipelines, monitoring, on-call. No additional latency.

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
