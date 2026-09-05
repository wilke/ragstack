# RagStack: Production-Grade Multi-Tenant RAG System (v4 — Final)

## Context

This plan merges and finalizes three prior design efforts:

- **plan-c1** — Go-based greenfield multi-tenant RAG with complete project structure and interfaces
- **plan-c2** — Legacy rag_api compatibility layer and FAISS index integration
- **plan-c3** — Knowledge graph, query rewriting, and full system merge

The merged plan keeps **Go** as the production runtime, incorporates **knowledge graph** (Neo4j), **query rewriting** (HyDE, multi-query, step-back), **cross-encoder reranking**, retains **legacy API compatibility** and **multi-tenancy**, and adds **explicit SLOs**, **CI/CD**, **deployment profiles**, and **KG extraction specifics** that prior plans lacked.

---

## SLOs & Scale Targets

| Metric | Target | Notes |
|--------|--------|-------|
| Index size | < 5M chunks | Postgres + Qdrant sized accordingly |
| Query P99 latency | < 2s | End-to-end including rewrite + retrieve + rerank + generate |
| Retrieval P99 latency | < 500ms | Rewrite + retrieve + rerank only (no LLM generation) |
| Ingestion throughput | >= 50 docs/min | Sustained; burst to 200/min with backpressure |
| Embedding P99 latency | < 200ms | Per-batch (up to 32 texts) |
| Reranker P99 latency | < 300ms | Top-40 to top-5 |
| KG extraction latency | < 5s per chunk | Async during ingestion; not on query path |
| Availability | 99.5% | Degraded mode (BM25-only) counts as available |
| FAISS sidecar P99 | < 200ms | Per-query; IndexFlatIP is O(n) but indices are small |

Latency budget breakdown for a query at P99:
```
Rewrite:     ~200ms  (LLM call for HyDE/multi-query; 0ms for passthrough)
Retrieve:    ~150ms  (Qdrant + BM25 + optional graph, parallel)
Rerank:      ~250ms  (Cohere API or cross-encoder)
Context:      ~10ms  (assembly + dedup)
Generate:   ~1300ms  (Claude streaming, time-to-first-token ~400ms)
Overhead:     ~90ms  (network, serialization, middleware)
─────────────────────
Total:      ~2000ms
```

---

## Existing Systems

### distllm (ramanathanlab/distllm)

HPC-scale distributed embedding pipeline. Key capabilities:
- Semantic chunking (splits by embedding similarity between sentences)
- Distributed embedding via Parsl across GPU clusters
- FAISS V2 indices (IndexFlatIP, HNSW, binary quantization)
- MCQA evaluation with chunk-level provenance tracking
- Scientific domain models (PubMedBert, SFR-Embedding-Mistral, ESM2)

### rag_api (cucinellclark/rag_api)

FastAPI service serving pre-built FAISS indices. Key capabilities:
- Dual-retrieval: semantic (distllm) + lexical (TF-IDF) per database
- MongoDB config registry (`copilot.ragList`)
- Score-based result merging across index types
- Lazy index caching in memory

### The Two FAISS Index Types

| Aspect | distllm (Semantic) | TF-IDF (Lexical) |
|--------|-------------------|-------------------|
| Embeddings | Pre-computed neural vectors | Sparse TF-IDF from vocabulary + IDF |
| Query encoding | Remote embedding service | Local regex tokenization + TF-IDF math |
| Storage | HuggingFace Dataset + FAISS binary | PyArrow batch files + Arrow vectorizer |
| FAISS type | IndexFlatIP | IndexFlatIP |
| Config keys | `dataset_dir`, `faiss_index_path` | `vectorizer_path`, `embeddings_path` |

---

## Tech Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Language | Go 1.23 | Performance, concurrency, single binary |
| API router | Chi v5 | Lightweight, stdlib-compatible |
| Vector store | Qdrant | Native vector search, collection-per-tenant isolation |
| Text search | PostgreSQL tsvector | No extra infra; good enough for BM25 |
| Knowledge graph | Neo4j 5 | Cypher queries, mature, multi-hop reasoning |
| Primary DB | PostgreSQL 16 | Relational data, tsvector, River job queue |
| Cache | Redis 7 | Query cache, rate limiting, semantic cache |
| Object storage | S3-compatible (MinIO dev) | Raw document storage |
| Job queue | River | Postgres-native, transactional, no extra infra |
| Embeddings | OpenAI text-embedding-3-small | Quality/cost ratio; swappable via interface |
| LLM | Claude API | Strong instruction following, long context |
| Reranker | Cohere Rerank + local cross-encoder | API reranker for quality; local for cost |
| KG extraction | Claude Haiku (async) | Cost-efficient for structured extraction |
| Cross-encoder | BAAI/bge-reranker-v2-m3 via Python sidecar | No API cost; GPU optional |
| Legacy FAISS | Python sidecar | Exact compat with existing indices |
| Observability | OpenTelemetry + Prometheus + Grafana | Traces, metrics, dashboards |
| CI/CD | GitHub Actions + Docker + Helm | Test, build, deploy pipeline |
| Containerization | Docker Compose (dev) / K8s (prod) | Standard |

---

## Deployment Profiles

Not all services are required for every deployment. The system supports three profiles controlled by config flags:

### Minimal (3 services)

For development, testing, or deployments without legacy or KG features.

| Service | Required | Notes |
|---------|----------|-------|
| PostgreSQL | Yes | Relational data, BM25, River jobs |
| Qdrant | Yes | Vector search |
| Redis | Yes | Cache, rate limiting |
| MinIO | Optional | Use local filesystem in dev |
| Neo4j | No | `GRAPH_ENABLED=false` |
| MongoDB | No | `LEGACY_ENABLED=false` |
| FAISS sidecar | No | `LEGACY_ENABLED=false` |
| Cross-encoder sidecar | No | `RERANKER_TYPE=cohere` or disabled |
| Jaeger | No | `OTEL_ENABLED=false` |

Config:
```env
LEGACY_ENABLED=false
GRAPH_ENABLED=false
RERANKER_TYPE=cohere
OTEL_ENABLED=false
```

### Standard (6 services)

For production without legacy FAISS support.

| Service | Required |
|---------|----------|
| PostgreSQL | Yes |
| Qdrant | Yes |
| Redis | Yes |
| MinIO/S3 | Yes |
| Neo4j | Yes |
| Jaeger/OTel collector | Yes |

Config:
```env
LEGACY_ENABLED=false
GRAPH_ENABLED=true
RERANKER_TYPE=cohere
OTEL_ENABLED=true
```

### Full (9 services)

For production with legacy rag_api compatibility.

All services from Standard, plus:

| Service | Required |
|---------|----------|
| MongoDB | Yes |
| FAISS sidecar | Yes |
| Cross-encoder sidecar | Optional |

Config:
```env
LEGACY_ENABLED=true
GRAPH_ENABLED=true
RERANKER_TYPE=cohere
OTEL_ENABLED=true
```

---

## Project Structure

```
ragstack/
├── cmd/
│   ├── api/main.go                    # HTTP API server
│   ├── worker/main.go                 # River background worker
│   └── faiss-sidecar/                 # Python FAISS service
│       ├── main.py
│       ├── requirements.txt
│       └── Dockerfile
├── internal/
│   ├── config/config.go               # Env-based config
│   ├── tenant/                        # Context propagation, middleware
│   │   ├── context.go
│   │   └── middleware.go
│   ├── auth/auth.go                   # API key -> tenant resolution
│   ├── document/                      # Loaders, chunkers
│   │   ├── model.go
│   │   ├── loader.go                  # DocumentLoader interface + registry
│   │   ├── loader_text.go
│   │   ├── loader_markdown.go
│   │   ├── loader_pdf.go
│   │   ├── loader_html.go
│   │   ├── loader_docx.go
│   │   ├── chunker.go                # Chunker interface + fixed-size impl
│   │   └── metadata.go
│   ├── embedding/                     # Embedding providers
│   │   ├── provider.go                # EmbeddingProvider interface
│   │   ├── openai.go
│   │   ├── remote.go                  # Generic HTTP embedding (legacy distllm)
│   │   └── cache.go                   # Redis-backed cache
│   ├── vectorstore/                   # Vector storage
│   │   ├── store.go                   # VectorStore interface
│   │   └── qdrant.go
│   ├── search/                        # Unified search engine
│   │   ├── engine.go                  # SearchEngine interface + hybrid impl
│   │   ├── vector.go                  # Qdrant vector search
│   │   ├── bm25.go                    # Postgres tsvector search
│   │   ├── rrf.go                     # Reciprocal Rank Fusion
│   │   └── legacy.go                  # FAISS sidecar adapter
│   ├── graph/                         # Knowledge graph
│   │   ├── store.go                   # GraphStore interface
│   │   ├── neo4j.go                   # Neo4j implementation
│   │   ├── extractor.go               # KGExtractor interface
│   │   ├── llm_extractor.go           # LLM-based entity/relation extraction
│   │   └── model.go                   # Triple, Entity types
│   ├── rewrite/                       # Query rewriting
│   │   ├── rewriter.go                # QueryRewriter interface
│   │   ├── passthrough.go
│   │   ├── multi_query.go             # LLM generates N paraphrases
│   │   ├── hyde.go                    # Hypothetical Document Embedding
│   │   └── step_back.go               # Generalize query for broader context
│   ├── rerank/                        # Result reranking
│   │   ├── reranker.go                # Reranker interface
│   │   ├── cohere.go                  # Cohere Rerank API
│   │   └── crossencoder.go            # Local cross-encoder via sidecar
│   ├── llm/                           # LLM providers
│   │   ├── provider.go                # LLMProvider interface
│   │   ├── claude.go                  # Claude API + streaming
│   │   └── prompt.go                  # Prompt templates
│   ├── generation/                    # RAG orchestrator
│   │   ├── service.go                 # Query pipeline: rewrite -> search -> rerank -> generate
│   │   └── context.go                 # Context assembly, token budget
│   ├── ingestion/                     # Async document pipeline
│   │   ├── pipeline.go
│   │   ├── jobs.go                    # River job definitions
│   │   └── worker.go
│   ├── legacy/                        # Legacy FAISS compat
│   │   ├── faiss.go                   # FAISS sidecar client
│   │   ├── distllm.go                 # distllm index search
│   │   ├── tfidf.go                   # TF-IDF index search
│   │   ├── registry.go                # DatabaseRegistry interface
│   │   └── merger.go                  # Score-based result merging
│   ├── storage/
│   │   ├── postgres/                  # pgx pool, all CRUD stores
│   │   │   ├── db.go
│   │   │   ├── tenant.go
│   │   │   ├── apikey.go
│   │   │   ├── collection.go
│   │   │   ├── document.go
│   │   │   ├── chunk.go              # Includes BM25 search via tsvector
│   │   │   └── triple.go             # KG triples CRUD (Postgres fallback)
│   │   ├── redis/
│   │   │   ├── client.go
│   │   │   ├── cache.go
│   │   │   └── ratelimit.go
│   │   ├── objectstore/
│   │   │   ├── store.go               # ObjectStore interface
│   │   │   └── s3.go
│   │   └── mongodb/
│   │       └── registry.go            # Read-only legacy config registry
│   ├── api/                           # HTTP transport
│   │   ├── router.go                  # All routes (v1 + legacy + admin)
│   │   ├── middleware.go
│   │   ├── handler_query.go           # v1 query + streaming
│   │   ├── handler_retrieve.go        # v1 retrieve-only (no generation)
│   │   ├── handler_document.go
│   │   ├── handler_collection.go
│   │   ├── handler_graph.go           # KG entity/neighbor endpoints
│   │   ├── handler_legacy.go          # rag_api compat endpoints
│   │   ├── handler_tenant.go
│   │   ├── request.go
│   │   ├── response.go
│   │   └── sse.go                     # Server-Sent Events
│   ├── observability/
│   │   ├── otel.go
│   │   ├── metrics.go
│   │   └── logging.go
│   └── platform/
│       ├── errors.go
│       ├── pagination.go
│       └── validation.go
├── migrations/
├── deploy/
│   ├── docker-compose.yml             # Full profile (all services)
│   ├── docker-compose.minimal.yml     # Minimal profile override
│   ├── Dockerfile
│   └── helm/
│       └── ragstack/
│           ├── Chart.yaml
│           ├── values.yaml
│           └── templates/
│               ├── api-deployment.yaml
│               ├── worker-deployment.yaml
│               ├── faiss-sidecar-deployment.yaml
│               ├── crossencoder-deployment.yaml
│               ├── configmap.yaml
│               ├── secret.yaml
│               ├── service.yaml
│               ├── ingress.yaml
│               └── hpa.yaml
├── .github/
│   └── workflows/
│       ├── ci.yml                     # Lint, test, build on PR
│       ├── release.yml                # Tag -> build image -> push -> deploy
│       └── nightly.yml                # Eval suite + regression detection
├── testdata/                          # Sample docs for testing
├── Makefile
├── .env.example
├── .golangci.yml
└── go.mod
```

---

## Key Interfaces

All accept `context.Context` for tenant propagation and tracing.

### Core

- **EmbeddingProvider** — `Embed(ctx, text) ([]float32, error)`, `EmbedBatch(ctx, texts) ([][]float32, error)`, `Dimensions() int`
- **LLMProvider** — `Complete(ctx, req) (*CompletionResponse, error)`, `Stream(ctx, req) (<-chan StreamChunk, error)`
- **DocumentLoader** — `Load(ctx, reader, filename) (*LoadedDocument, error)`, `SupportedMimeTypes() []string`
- **Chunker** — `Chunk(text) ([]Chunk, error)`
- **VectorStore** — `EnsureCollection`, `Upsert`, `Search`, `Delete`, `DeleteCollection` — all scoped by tenantID
- **SearchEngine** — `Search(ctx, SearchRequest) ([]SearchResult, error)`
- **ObjectStore** — `Put`, `Get`, `Delete`, `Exists`
- **Reranker** — `Rerank(ctx, query string, docs []RerankDocument, topN int) ([]RerankResult, error)`

### Knowledge Graph

```go
type GraphStore interface {
    AddTriples(ctx context.Context, triples []Triple) error
    QueryNeighborhood(ctx context.Context, entity string, depth int) ([]Triple, error)
    DeleteByDocument(ctx context.Context, docID string) error
}

type KGExtractor interface {
    Extract(ctx context.Context, chunks []Chunk) ([]Triple, error)
}
```

### Query Rewriting

```go
type QueryRewriter interface {
    Rewrite(ctx context.Context, query string) ([]string, error)
}
```

### Legacy

- **LegacyIndexSearcher** — `Search(ctx, query, topK, scoreThreshold) ([]LegacyResult, queryEmbedding, error)`
- **DatabaseRegistry** — `GetConfigs(ctx, dbName) ([]DatabaseConfig, error)`, `ListDatabases(ctx, activeOnly) ([]DatabaseInfo, error)`

---

## Multi-Tenancy Design

- **Auth**: `Authorization: Bearer <api_key>` -> hash lookup in `api_keys` table -> `tenant_id` in context
- **Postgres**: `tenant_id` column on every table, composite indexes with tenant_id leading
- **Qdrant**: Collection-per-tenant (`tenant_{id}`), strong isolation
- **Neo4j**: `tenant_id` property on all nodes and relationships; Cypher queries always filter by tenant
- **Redis**: Key prefix `tenant:{id}:`
- **S3**: Object key prefix `{tenant_id}/`
- **Legacy endpoints**: No tenant scoping (matches existing rag_api behavior)

---

## Database Schema

### Migrations 001-005: Core (from plan-c1)

1. **tenants** + **api_keys** — tenant registry, API key auth (SHA-256 hashed keys, scopes, expiry)
2. **collections** — logical groupings per tenant (unique name per tenant)
3. **documents** — file metadata, S3 storage key, status enum (pending/processing/ready/failed), chunk_count
4. **chunks** — text content, chunk_index, token_count, metadata JSONB, `tsvector` column (auto-generated), GIN index for BM25
5. **query_log** — audit trail with latency tracking

### Migration 006: Legacy Registry

```sql
CREATE TABLE legacy_databases (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        TEXT NOT NULL,
    program     TEXT NOT NULL,            -- 'distllm' or 'tfidf'
    active      BOOLEAN NOT NULL DEFAULT true,
    description TEXT,
    config_data JSONB NOT NULL,
    priority    INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_legacy_db_name ON legacy_databases(name, active);
```

### Migration 007: Knowledge Graph Triples

```sql
CREATE TABLE triples (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    doc_id      UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    subject     TEXT NOT NULL,
    predicate   TEXT NOT NULL,
    object      TEXT NOT NULL,
    confidence  REAL NOT NULL DEFAULT 1.0,
    metadata    JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(tenant_id, subject, predicate, object)
);
CREATE INDEX idx_triples_tenant ON triples(tenant_id);
CREATE INDEX idx_triples_subject ON triples(tenant_id, subject);
CREATE INDEX idx_triples_object ON triples(tenant_id, object);
CREATE INDEX idx_triples_doc ON triples(tenant_id, doc_id);
```

### Migration 008: Expanded Query Log

```sql
CREATE TABLE query_log (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id         UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    collection_id     UUID,
    query             TEXT NOT NULL,
    rewritten_queries TEXT[],
    rewrite_strategy  TEXT,
    top_k             INT NOT NULL,
    result_count      INT NOT NULL,
    used_graph        BOOLEAN NOT NULL DEFAULT false,
    used_reranker     BOOLEAN NOT NULL DEFAULT false,
    latency_ms        INT NOT NULL,
    retrieve_ms       INT,
    rerank_ms         INT,
    generate_ms       INT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_query_log_tenant ON query_log(tenant_id, created_at DESC);
```

River manages its own tables via built-in migrations.

---

## Query Pipeline

```
User Query
    |
    v
1. REWRITE (configurable, ~200ms with LLM, 0ms passthrough)
    |  PassthroughRewriter (default)
    |  MultiQueryRewriter (LLM generates N paraphrases)
    |  HyDERewriter (LLM generates hypothetical answer, embed that)
    |  StepBackRewriter (LLM generalizes query)
    |  -> produces []string of query variants
    |
    v
2. RETRIEVE (parallel per query variant, ~150ms)
    |  Vector search (Qdrant) ----------------+
    |  BM25 search (Postgres tsvector) -------+-- RRF merge
    |  Graph context (Neo4j, optional) -------+
    |  -> merged, deduplicated results
    |
    v
3. RERANK (~250ms)
    |  Cohere Rerank API (high quality, API cost)
    |  -- or --
    |  Cross-encoder sidecar (no API cost, self-hosted)
    |  -> top-K reranked results
    |
    v
4. CONTEXT ASSEMBLY (~10ms)
    |  Select top-K chunks within token budget
    |  Deduplicate overlapping passages
    |  Build prompt: system + context + query
    |
    v
5. GENERATE (~1300ms)
    |  Claude API (streaming or batch)
    |  -> answer + source references + usage stats
```

### Graph-Augmented Retrieval

When `use_graph=true` in the query request:

1. Extract key entities from the query (simple NER or LLM-based)
2. Call `GraphStore.QueryNeighborhood(entity, depth=1)` for each entity
3. Convert triples to synthetic chunks: `"{subject} {predicate} {object}"`
4. Include in RRF fusion with a configurable base score (default 0.5)
5. Graph-sourced results tagged with `retrieval_method: "graph"`

This enables multi-hop reasoning (e.g., "Who funded the company that acquired X?") that pure vector/BM25 search cannot answer.

### Graceful Degradation

| Failure | Behavior |
|---------|----------|
| Qdrant down | Fall back to BM25-only retrieval; log warning |
| Neo4j down | Skip graph retrieval; `use_graph` silently disabled |
| Reranker down | Return RRF-merged results without reranking |
| FAISS sidecar down | Legacy endpoints return 503; v1 endpoints unaffected |
| Embedding service down | Return error (no fallback; embeddings are required for vector search) |
| LLM down | `/v1/retrieve` still works; `/v1/query` returns error |

---

## Knowledge Graph Extraction — Detailed Design

### Model Selection

| Concern | Choice | Rationale |
|---------|--------|-----------|
| Extraction model | Claude Haiku (claude-haiku-4-5-20251001) | Cost-efficient ($0.80/MTok input); structured output support; sufficient quality for triple extraction |
| Fallback | Skip extraction, log warning | KG is supplementary; missing triples are acceptable |
| Invocation | Async via River job (separate from embed job) | Does not block ingestion; can retry independently |

### Extraction Prompt

```
Extract factual relationships from the following text as (subject, predicate, object) triples.

Rules:
- Subject and object must be named entities (people, organizations, locations, concepts, technologies)
- Predicate must be a short verb phrase in present tense (e.g., "is located in", "was founded by", "develops")
- Only extract relationships explicitly stated in the text
- Assign a confidence score 0.0-1.0 for each triple
- Return JSON array

Output format:
[{"subject": "...", "predicate": "...", "object": "...", "confidence": 0.9}]

Text:
{chunk_text}
```

### Predicate Vocabulary

Open vocabulary — predicates are free-form verb phrases extracted by the LLM. Normalization is applied post-extraction:
1. Lowercase and trim whitespace
2. Collapse synonyms via a configurable alias map (e.g., "is part of" -> "belongs to")
3. The alias map starts empty and is populated based on observed predicates over time

A closed vocabulary was considered but rejected: it limits recall for diverse document corpora and requires domain-specific curation upfront.

### Cost & Throughput

- Average chunk: ~300 tokens -> ~$0.00024 per chunk (Haiku input)
- 5M chunks full extraction: ~$1,200 one-time cost
- Incremental: only new/updated chunks are extracted
- Rate limit: 10 concurrent extraction jobs (configurable via `KG_EXTRACTION_CONCURRENCY`)

### Error Handling

- Malformed JSON from LLM: retry once with stricter prompt; if still malformed, skip chunk and log
- Duplicate triples: upserted (ON CONFLICT DO NOTHING via UNIQUE constraint)
- Low-confidence triples (< 0.5): stored but excluded from query-time retrieval by default (threshold configurable via `KG_MIN_CONFIDENCE`)

### Neo4j Schema

```cypher
// Nodes
(:Entity {name: String, tenant_id: String})

// Relationships
(:Entity)-[:RELATES {predicate: String, doc_id: String, confidence: Float, tenant_id: String}]->(:Entity)

// Indexes
CREATE INDEX entity_name FOR (e:Entity) ON (e.name, e.tenant_id)
CREATE INDEX rel_doc FOR ()-[r:RELATES]-() ON (r.doc_id)
```

---

## Cross-Encoder Reranker Sidecar — Detailed Design

### Overview

A lightweight Python HTTP service that loads a cross-encoder model and exposes a reranking endpoint. Deployed alongside the Go API as an optional sidecar.

### Model

| Concern | Choice | Rationale |
|---------|--------|-----------|
| Model | BAAI/bge-reranker-v2-m3 | Multilingual, strong quality, 568M params |
| Runtime | sentence-transformers + FastAPI | Simple, well-tested, no custom inference code |
| Hardware | CPU acceptable at < 40 docs/request; GPU recommended for higher throughput | At P99 < 300ms target, CPU handles top-40 reranking |
| Batch size | 40 (matches retriever top-K before reranking) | Single forward pass |

### API

```
POST /rerank
Content-Type: application/json

Request:
{
  "query": "string",
  "documents": ["string", ...],
  "top_n": 5
}

Response:
{
  "results": [
    {"index": 0, "score": 0.95},
    {"index": 3, "score": 0.87},
    ...
  ]
}

GET /health
Response: {"status": "healthy", "model": "BAAI/bge-reranker-v2-m3"}
```

### Deployment

```yaml
# In docker-compose.yml
crossencoder-sidecar:
  build: ./cmd/crossencoder-sidecar
  environment:
    MODEL_NAME: BAAI/bge-reranker-v2-m3
    MAX_LENGTH: 512
    DEVICE: cpu              # or "cuda:0"
  ports: ["50052:50052"]
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:50052/health"]
    interval: 10s
    timeout: 5s
    retries: 3
  deploy:
    resources:
      limits:
        memory: 2G           # ~1.2G model + overhead
```

### Files

```
cmd/crossencoder-sidecar/
├── main.py                  # FastAPI app, model loading, /rerank endpoint
├── requirements.txt         # sentence-transformers, fastapi, uvicorn
└── Dockerfile               # Python 3.11-slim, install deps, preload model
```

### Go Client

```go
// internal/rerank/crossencoder.go
type CrossEncoderReranker struct {
    baseURL    string
    httpClient *http.Client
}

func (r *CrossEncoderReranker) Rerank(ctx context.Context, query string, docs []RerankDocument, topN int) ([]RerankResult, error)
```

Config:
```go
type CrossEncoderConfig struct {
    URL     string        `env:"CROSSENCODER_URL" default:"http://localhost:50052"`
    Timeout time.Duration `env:"CROSSENCODER_TIMEOUT" default:"5s"`
}
```

### Reranker Selection

The `RERANKER_TYPE` env var controls which implementation is used:

| Value | Implementation | Notes |
|-------|---------------|-------|
| `cohere` | Cohere Rerank API | Default; requires `COHERE_API_KEY` |
| `crossencoder` | Local cross-encoder sidecar | No API cost; requires sidecar running |
| `none` | No reranking | RRF results returned directly |

---

## Backwards-Compatible API (rag_api contract)

The legacy API is served at root prefix. Exact contract:

### Endpoints

```
GET  /health
Response: {
  "status": "healthy" | "degraded" | "unhealthy",
  "mongodb_connected": bool,
  "embedding_service_available": bool
}

GET  /databases?active_only=true
Response: {
  "databases": [{ "name", "program", "active", "description", "data" }],
  "total": int
}

GET  /databases/{database_name}
Response: { "name", "program", "active", "description", "data" }

POST /query/{database_name}
Request: {
  "query": str,                    // required
  "top_k": int | null,             // 1-100, default from config
  "score_threshold": float | null  // 0.0-1.0, default from config
}
Response: {
  "query": str,
  "database": str,
  "documents": [{ "content": str, "score": float, "metadata": dict | null }],
  "embedding": [float] | null,    // query embedding vector
  "total_results": int
}
```

### Query Behavior to Preserve

1. A single `database_name` can have **multiple configurations** (one distllm + one tfidf)
2. All matching configs are searched independently
3. Results from all configs are **merged and sorted by score descending**
4. Results may exceed `top_k` when multiple indices return results (legacy behavior)
5. `score_threshold` is applied per-config before merging
6. Each result's `metadata` includes `program` field indicating source index
7. The `embedding` field in the response contains the query embedding vector

---

## FAISS Integration Strategy

### Recommended: Python sidecar (Phase 1)

Run a lightweight Python sidecar that loads FAISS indices and serves search requests over HTTP. This preserves exact compatibility with the existing Python code.

```
cmd/faiss-sidecar/main.py   # Thin Python service loading FAISS + datasets
internal/legacy/faiss.go    # Go HTTP client calling the sidecar
```

### Future: Qdrant migration (Phase 16)

Migrate legacy FAISS indices into Qdrant collections, enabling eventual retirement of the Python sidecar.

### Legacy Index Data Loading

**distllm Index**:
```go
type DistllmIndex struct {
    faissIndex  *FaissIndex          // FAISS IndexFlatIP
    dataset     *HFDataset           // HuggingFace Dataset (text + metadata)
    embedder    embedding.Provider   // Remote embedding service for query encoding
}

func (idx *DistllmIndex) Search(ctx, query, topK, threshold) ([]LegacyResult, []float32, error) {
    // 1. Embed query via remote embedding service
    // 2. L2-normalize query embedding
    // 3. FAISS inner-product search
    // 4. Filter by score threshold
    // 5. Load document text from dataset by index
    // 6. Return results with metadata
}
```

**TF-IDF Index**:
```go
type TFIDFIndex struct {
    faissIndex  *FaissIndex          // FAISS IndexFlatIP over TF-IDF vectors
    vocabulary  []string             // From vectorizer_components.arrow
    idfValues   []float64            // From vectorizer_components.arrow
    vocabIndex  map[string]int       // Reverse lookup: word -> index
    texts       []string             // Document texts from batch arrow files
}

func (idx *TFIDFIndex) Search(ctx, query, topK, threshold) ([]LegacyResult, error) {
    // 1. Tokenize query: regex `(?u)\b\w\w+\b`, lowercase
    // 2. Compute term frequencies
    // 3. Build TF-IDF query vector: count * idf for each token
    // 4. L2-normalize query vector
    // 5. FAISS inner-product search
    // 6. Filter by score threshold
    // 7. Return results with metadata
}
```

### Legacy Result Merging

```go
func MergeResults(resultSets [][]LegacyResult) []LegacyResult {
    // 1. Concatenate all result sets
    // 2. Sort by score descending
    // 3. Return all (do NOT truncate to top_k -- legacy behavior)
}
```

---

## REST API Routes

### Legacy Compat (rag_api contract, no auth)

```
GET  /health                              # { status, mongodb_connected, embedding_service_available }
GET  /databases?active_only=true          # { databases: [...], total }
GET  /databases/{database_name}           # { name, program, active, description, data }
POST /query/{database_name}               # { query, database, documents, embedding, total_results }
```

### v1 API (tenant-scoped, API key auth)

```
# Collections
POST   /v1/collections
GET    /v1/collections
GET    /v1/collections/{id}
DELETE /v1/collections/{id}

# Documents
POST   /v1/collections/{id}/documents     # Multipart upload -> async ingestion
GET    /v1/collections/{id}/documents
GET    /v1/documents/{id}                  # Includes processing status
DELETE /v1/documents/{id}

# Query (RAG generation)
POST   /v1/query                           # Full pipeline: rewrite -> retrieve -> rerank -> generate
POST   /v1/query/stream                    # Same but SSE streaming

# Retrieve only (no LLM generation)
POST   /v1/retrieve                        # Returns scored chunks + sources only

# Knowledge Graph
GET    /v1/graph/entities                  # List entities with triple counts
GET    /v1/graph/neighbors/{entity}?depth=1  # Entity neighborhood

# Health
GET    /v1/health
```

### Admin

```
POST /admin/tenants
GET  /admin/tenants
POST /admin/tenants/{id}/api-keys
```

### v1 Query Request/Response

```go
// Request
type QueryRequest struct {
    Query             string            `json:"query"`
    TopK              int               `json:"top_k,omitempty"`              // default 5
    RewriteStrategies []string          `json:"rewrite_strategies,omitempty"` // ["passthrough"], ["hyde"], ["multi_query"]
    Filters           map[string]any    `json:"filters,omitempty"`
    UseGraph          bool              `json:"use_graph,omitempty"`          // default true
    Stream            bool              `json:"stream,omitempty"`
    CollectionID      string            `json:"collection_id,omitempty"`
    UseReranker       bool              `json:"use_reranker,omitempty"`       // default true
}

// Response
type QueryResponse struct {
    Answer           string        `json:"answer"`
    Sources          []Source      `json:"sources"`
    RewrittenQueries []string      `json:"rewritten_queries"`
    Usage            UsageStats    `json:"usage,omitempty"`
    Timing           TimingStats   `json:"timing"`
}

type Source struct {
    DocumentID   string         `json:"document_id"`
    ChunkID      string         `json:"chunk_id"`
    Content      string         `json:"content"`
    Score        float64        `json:"score"`
    Method       string         `json:"retrieval_method"` // "vector", "bm25", "graph", "hybrid"
    Metadata     map[string]any `json:"metadata,omitempty"`
}

type TimingStats struct {
    RewriteMs  int `json:"rewrite_ms"`
    RetrieveMs int `json:"retrieve_ms"`
    RerankMs   int `json:"rerank_ms"`
    GenerateMs int `json:"generate_ms"`
    TotalMs    int `json:"total_ms"`
}
```

---

## Implementation Phases

### Phase 1: Project Scaffold
Go module, Makefile, Docker Compose (all profiles), config struct with env vars, error types, slog logging, health check endpoint.

**Docker Compose services**: postgres, qdrant, redis, minio, minio-setup, neo4j, mongodb, jaeger

**Files**: `go.mod`, `cmd/api/main.go`, `cmd/worker/main.go`, `internal/config/config.go`, `internal/platform/{errors,pagination,validation}.go`, `internal/observability/logging.go`, `deploy/docker-compose.yml`, `deploy/docker-compose.minimal.yml`, `deploy/Dockerfile`, `Makefile`, `.env.example`, `.gitignore`, `.golangci.yml`

### Phase 2: Database & Storage Layer
SQL migrations (001-008), pgx connection pool, CRUD for tenants, API keys, collections, documents, chunks, triples. BM25 search method on chunk store using `ts_rank_cd` + `plainto_tsquery`.

**Files**: `migrations/001-008_*.sql`, `internal/storage/postgres/{db,tenant,apikey,collection,document,chunk,triple}.go`

### Phase 3: Object Storage & Auth
S3/MinIO client (aws-sdk-go-v2), API key auth resolver (hash lookup), tenant context helpers, auth middleware.

**Files**: `internal/storage/objectstore/{store,s3}.go`, `internal/auth/auth.go`, `internal/tenant/{context,middleware}.go`

### Phase 4: Legacy FAISS Support
Python sidecar for FAISS index loading and search. Go client to call sidecar. Supports both distllm and TF-IDF index types. MongoDB registry reader. Result merger. Legacy API handlers (`/health`, `/databases`, `/query/{db}`).

**Files**: `cmd/faiss-sidecar/{main.py,requirements.txt,Dockerfile}`, `internal/legacy/{faiss,distllm,tfidf,registry,merger}.go`, `internal/storage/mongodb/registry.go`, `internal/api/handler_legacy.go`, `internal/embedding/remote.go`

**Verification**: Legacy endpoints return identical responses to existing rag_api for the same indices.

### Phase 5: Document Processing
Loaders for text, markdown, PDF, HTML, DOCX. Loader registry (keyed by MIME type). Fixed-size chunker with token-based overlap using tiktoken-go. Metadata extraction.

**Files**: `internal/document/{model,loader,loader_text,loader_markdown,loader_pdf,loader_html,loader_docx,chunker,metadata}.go`

### Phase 6: Embedding Service
OpenAI embedding provider with batching. Redis-backed embedding cache (hash text -> cached vector, tenant-prefixed keys). Remote embedder for legacy distllm query embedding.

**Files**: `internal/embedding/{provider,openai,remote,cache}.go`, `internal/storage/redis/{client,cache}.go`

### Phase 7: Vector Store (Qdrant)
Qdrant gRPC client wrapper. Collection-per-tenant CRUD. HNSW config (m=16, ef_construct=100). Cosine distance. Batch upsert. Filtered search.

**Files**: `internal/vectorstore/{store,qdrant}.go`

### Phase 8: Knowledge Graph
Neo4j Go driver integration. GraphStore interface + Neo4j implementation. LLM-based entity/relation extraction via Claude Haiku (KGExtractor). Triples stored in both Neo4j (primary) and Postgres (fallback). Predicate normalization with alias map.

**Files**: `internal/graph/{store,neo4j,extractor,llm_extractor,model}.go`

**Key operations**:
- `AddTriples` — Store extracted triples in Neo4j (nodes + relationships) and Postgres
- `QueryNeighborhood` — Cypher query expanding from entity node by depth
- `DeleteByDocument` — Remove all triples for a document (both stores)
- `Extract` — Claude Haiku prompt for (subject, predicate, object, confidence) extraction

### Phase 9: Ingestion Pipeline
River job queue. Two job types:
- **ProcessDocumentJob**: fetch from S3 -> detect MIME -> load -> chunk -> embed batch -> store chunks in Postgres -> store vectors in Qdrant -> update document status
- **ExtractTriplesJob**: for each chunk batch -> call KGExtractor -> store triples in Neo4j + Postgres

Pipeline.Ingest: validate -> upload to S3 -> create document record -> enqueue ProcessDocumentJob (which enqueues ExtractTriplesJob on completion if `GRAPH_ENABLED=true`).

**Files**: `internal/ingestion/{pipeline,jobs,worker}.go`

### Phase 10: Query Rewriting
QueryRewriter interface + implementations:
- **PassthroughRewriter** — Returns original query unchanged (default)
- **MultiQueryRewriter** — LLM generates N paraphrases, all used for retrieval
- **HyDERewriter** — LLM generates hypothetical answer, embed it as the query
- **StepBackRewriter** — LLM generalizes query for broader context

Each rewriter produces `[]string` of query variants. All variants are searched independently, results merged via RRF.

**Files**: `internal/rewrite/{rewriter,passthrough,multi_query,hyde,step_back}.go`

### Phase 11: Retrieval Engine
Hybrid search: for each query variant -> embed -> run vector (Qdrant) + BM25 (Postgres) + graph (Neo4j) in parallel via errgroup -> RRF merge (k=60, configurable weights) -> deduplicate across variants -> rerank with Cohere or cross-encoder. Graceful fallback if components fail.

**Files**: `internal/search/{engine,vector,bm25,rrf,legacy}.go`, `internal/rerank/{reranker,cohere,crossencoder}.go`

### Phase 12: Generation Service
Claude API client with streaming SSE parsing. Prompt templates (system + user with chunk context). RAG orchestrator: rewrite -> search -> rerank -> assemble context -> render prompt -> call LLM. Returns answer + source references + token usage + timing breakdown. Streaming variant returns channel of events.

**Files**: `internal/llm/{provider,claude,prompt}.go`, `internal/generation/{service,context}.go`

### Phase 13: REST API
Chi router with all route groups (legacy, v1, admin). Middleware stack (request ID, logging, recovery, CORS, auth, rate limiting). All handlers wired. SSE streaming. Response helpers with typed error serialization.

**Files**: `internal/api/{router,middleware,handler_query,handler_retrieve,handler_document,handler_collection,handler_graph,handler_legacy,handler_tenant,request,response,sse}.go`, `internal/storage/redis/ratelimit.go`

### Phase 14: Cross-Encoder Sidecar
Python sidecar for local cross-encoder reranking. FastAPI + sentence-transformers. Model: BAAI/bge-reranker-v2-m3. HTTP API with `/rerank` and `/health` endpoints.

**Files**: `cmd/crossencoder-sidecar/{main.py,requirements.txt,Dockerfile}`

### Phase 15: Observability
OTel SDK init with OTLP gRPC exporter. Prometheus counters/histograms (documents_ingested, query_duration, embedding_duration, chunks_stored, rewrite_duration, rerank_duration, graph_query_duration, kg_extraction_duration, faiss_query_duration). slog with trace correlation. HTTP middleware for tracing.

**Files**: `internal/observability/{otel,metrics,logging}.go`

### Phase 16: Testing
Unit tests (chunking, RRF, rewriters, loaders, graph extraction, merger). Integration tests with testcontainers-go (Postgres, Qdrant, Redis, MinIO, Neo4j). Legacy compat tests against golden fixtures. API handler tests with httptest. E2E: ingest -> query round-trip.

**Files**: `*_test.go`, `testdata/sample.{pdf,md,docx,html,txt}`

### Phase 17: CI/CD Pipeline
GitHub Actions workflows for continuous integration, release, and nightly evaluation.

**Files**: `.github/workflows/{ci,release,nightly}.yml`, `deploy/helm/ragstack/**`

### Phase 18: Migration Tooling (Future)
Tool to migrate legacy FAISS indices into Qdrant collections, enabling retirement of Python sidecar.

---

## CI/CD Pipeline

### CI (on every PR)

```yaml
# .github/workflows/ci.yml
name: CI
on:
  pull_request:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with: { go-version: '1.23' }
      - run: golangci-lint run ./...

  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16-alpine
        env: { POSTGRES_USER: test, POSTGRES_PASSWORD: test, POSTGRES_DB: ragstack_test }
        ports: ['5432:5432']
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with: { go-version: '1.23' }
      - run: go test -race -coverprofile=coverage.out ./...
      - run: go tool cover -func=coverage.out

  contract:
    runs-on: ubuntu-latest
    needs: [test]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with: { go-version: '1.23' }
      - run: go test -tags=contract ./internal/api/...
      # Validates v1 + legacy endpoints against OpenAPI spec / golden fixtures

  build:
    runs-on: ubuntu-latest
    needs: [lint, test]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with: { go-version: '1.23' }
      - run: go build -o bin/api ./cmd/api && go build -o bin/worker ./cmd/worker
      - uses: docker/build-push-action@v5
        with: { push: false, tags: 'ragstack:${{ github.sha }}' }
```

### Release (on tag push)

```yaml
# .github/workflows/release.yml
name: Release
on:
  push:
    tags: ['v*']

jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/login-action@v3
        with: { registry: ghcr.io, username: ${{ github.actor }}, password: ${{ secrets.GITHUB_TOKEN }} }
      - uses: docker/build-push-action@v5
        with:
          push: true
          tags: |
            ghcr.io/${{ github.repository }}/api:${{ github.ref_name }}
            ghcr.io/${{ github.repository }}/api:latest
      # Build and push faiss-sidecar and crossencoder-sidecar images similarly
      - run: helm upgrade --install ragstack ./deploy/helm/ragstack
             --set image.tag=${{ github.ref_name }}
             --namespace ragstack --create-namespace
```

### Nightly Evaluation

```yaml
# .github/workflows/nightly.yml
name: Nightly Eval
on:
  schedule:
    - cron: '0 3 * * *'  # 3 AM UTC

jobs:
  eval:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker compose up -d
      - run: make migrate-up
      - run: go test -tags=eval -timeout=30m ./internal/evaluation/...
      # Runs gold-set queries, computes nDCG@10, hit@5, faithfulness
      # Compares against baseline stored in testdata/eval_baseline.json
      # Fails if regression exceeds threshold
```

### Contract Test Gate

Legacy compat endpoints have golden request/response fixtures stored in `testdata/legacy_fixtures/`. Contract tests replay these fixtures and assert byte-level response parity (after JSON normalization).

```
testdata/legacy_fixtures/
├── health_healthy.json
├── health_degraded.json
├── databases_list.json
├── databases_get.json
├── query_single_config.json
├── query_multi_config.json
└── query_threshold_filter.json
```

---

## Docker Compose (Full Profile)

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: ragstack
      POSTGRES_PASSWORD: ragstack_dev
      POSTGRES_DB: ragstack
    ports: ["5432:5432"]
    volumes: [pgdata:/var/lib/postgresql/data]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ragstack"]
      interval: 5s
      timeout: 5s
      retries: 5

  qdrant:
    image: qdrant/qdrant:latest
    ports: ["6333:6333", "6334:6334"]
    volumes: [qdrant_data:/qdrant/storage]

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    volumes: [redis_data:/data]

  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    ports: ["9000:9000", "9001:9001"]
    volumes: [minio_data:/data]

  neo4j:
    image: neo4j:5
    environment:
      NEO4J_AUTH: neo4j/ragstack_dev
    ports: ["7474:7474", "7687:7687"]
    volumes: [neo4j_data:/data]

  mongodb:
    image: mongo:7
    ports: ["27017:27017"]
    volumes: [mongo_data:/data/db]
    healthcheck:
      test: ["CMD", "mongosh", "--eval", "db.adminCommand('ping')"]
      interval: 5s
      timeout: 5s
      retries: 5

  faiss-sidecar:
    build: ./cmd/faiss-sidecar
    volumes:
      - ${FAISS_DATA_DIR:-./testdata/faiss}:/data:ro
    ports: ["50051:50051"]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:50051/health"]
      interval: 10s
      timeout: 5s
      retries: 3

  crossencoder-sidecar:
    build: ./cmd/crossencoder-sidecar
    environment:
      MODEL_NAME: BAAI/bge-reranker-v2-m3
      MAX_LENGTH: 512
      DEVICE: cpu
    ports: ["50052:50052"]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:50052/health"]
      interval: 10s
      timeout: 5s
      retries: 3
    deploy:
      resources:
        limits:
          memory: 2G

  jaeger:
    image: jaegertracing/all-in-one:latest
    ports: ["16686:16686", "4317:4317", "4318:4318"]

volumes:
  pgdata:
  qdrant_data:
  redis_data:
  minio_data:
  neo4j_data:
  mongo_data:
```

---

## Key Dependencies (go.mod)

```
github.com/go-chi/chi/v5                    # HTTP router
github.com/jackc/pgx/v5                     # PostgreSQL driver
github.com/riverqueue/river                  # Job queue
github.com/redis/go-redis/v9                 # Redis client
github.com/qdrant/go-client                  # Qdrant vector store
github.com/neo4j/neo4j-go-driver/v5         # Neo4j graph database
github.com/aws/aws-sdk-go-v2                # S3/MinIO object storage
go.mongodb.org/mongo-driver                  # MongoDB (legacy config)
github.com/cohere-ai/cohere-go/v2           # Cohere reranker
github.com/golang-migrate/migrate/v4         # SQL migrations
github.com/pkoukk/tiktoken-go               # Token counting
github.com/ledongthuc/pdf                    # PDF parsing
golang.org/x/net                             # HTML parsing
go.opentelemetry.io/otel                     # Tracing
github.com/prometheus/client_golang           # Metrics
github.com/google/uuid                       # UUIDs
golang.org/x/sync                            # errgroup
github.com/stretchr/testify                  # Testing
github.com/testcontainers/testcontainers-go  # Integration tests
```

---

## Architectural Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Language | Go (not Python) | Production runtime perf; copilot branch Python code is reference only |
| Vector store | Qdrant (not pgvector) | Dedicated, optimized; collection-per-tenant isolation |
| BM25 | Postgres tsvector (not Elasticsearch) | One fewer service; sufficient for < 5M chunks |
| Graph DB | Neo4j (primary) + Postgres triples (fallback) | Cypher for complex graph queries; Postgres for simple lookups when Neo4j unavailable |
| KG extraction model | Claude Haiku (not local NER) | Higher quality triples; structured output; acceptable cost at ~$0.00024/chunk |
| KG predicate vocab | Open (not closed) | Avoids domain-specific curation; normalization via alias map handles synonyms |
| Job queue | River (not Celery) | Go-native, Postgres-backed, no Redis dependency for jobs |
| Legacy FAISS | Python sidecar (not CGO) | Lower risk, exact compat, isolates complexity; migrate to Qdrant later |
| Reranker | Cohere API + cross-encoder option | API for quality, local for cost control; interface-driven selection |
| Cross-encoder model | BAAI/bge-reranker-v2-m3 | Multilingual, strong benchmark results, CPU-viable at batch=40 |
| Query rewriting | Interface-driven, per-request config | Users choose strategy per query; extensible |
| Streaming | SSE (not WebSocket) | Simpler, widely supported, sufficient for LLM token streaming |
| Multi-tenancy | Baked in from day 1 | Avoids costly retrofit |
| Legacy compat | Exact rag_api contract at root | Existing clients work without changes |
| Deployment | Docker Compose (dev) + Helm (prod) | Standard toolchain; profiles for minimal/standard/full |

---

## Verification

1. `make docker-up` — start all services (Postgres, Qdrant, Redis, MinIO, Neo4j, MongoDB, Jaeger)
2. `make migrate-up` — run SQL migrations (001-008)
3. `make build` — compile api + worker binaries
4. Create tenant + API key via admin endpoint
5. Create a collection, upload a sample document
6. Poll document status until "ready"
7. Verify KG triples extracted: `GET /v1/graph/entities`
8. `POST /v1/query` with `rewrite_strategies: ["passthrough"]` — basic RAG, verify P99 < 2s
9. `POST /v1/query` with `rewrite_strategies: ["hyde"]` — HyDE rewriting
10. `POST /v1/query` with `use_graph: true` — graph-augmented retrieval
11. `POST /v1/query/stream` — verify SSE streaming
12. `POST /v1/retrieve` — retrieve-only (no generation), verify P99 < 500ms
13. Start FAISS sidecar with sample indices
14. `GET /health` — legacy health check (status, mongodb_connected, embedding_service_available)
15. `GET /databases` — legacy database listing from MongoDB ragList
16. `POST /query/{db}` — legacy query, verify merged results match existing rag_api output
17. Verify graceful degradation: stop Neo4j -> queries still work without graph; stop Qdrant -> BM25 fallback
18. `make test` — all unit + integration tests pass
19. `make lint` — golangci-lint passes
20. Check Jaeger for end-to-end traces across rewrite -> retrieve -> rerank -> generate
21. Check Prometheus `/metrics` for all registered counters/histograms
22. Run contract tests against legacy golden fixtures
