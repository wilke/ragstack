# RagStack: Production-Grade Multi-Tenant RAG System (v3 — Merged)

## Context

This plan merges two design efforts:

- **plan-c2** (our plan) — Go-based, multi-tenant, legacy rag_api compat, FAISS index support
- **copilot/plan-and-spec-rag-system** — Python reference architecture with knowledge graph, query rewriting, HyDE, cross-encoder reranking

The merged plan keeps **Go** as the production runtime, incorporates the copilot branch's **knowledge graph**, **query rewriting**, and **cross-encoder reranking** features, and retains **legacy API compatibility** and **multi-tenancy** from plan-c2.

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
| Vector store | Qdrant | Native vector search, collection-per-tenant |
| Text search | PostgreSQL tsvector | No extra infra; good enough for BM25 |
| Knowledge graph | Neo4j 5 | Cypher queries, mature, multi-hop reasoning |
| Primary DB | PostgreSQL 16 | Relational data, tsvector, River job queue |
| Cache | Redis 7 | Query cache, rate limiting, semantic cache |
| Object storage | S3-compatible (MinIO dev) | Raw document storage |
| Job queue | River | Postgres-native, transactional, no extra infra |
| Embeddings | OpenAI text-embedding-3-small | Quality/cost ratio; swappable via interface |
| LLM | Claude API | Strong instruction following, long context |
| Reranker | Cohere Rerank + local cross-encoder | API reranker for quality; local for cost |
| Legacy FAISS | Python sidecar | Exact compat with existing indices |
| Observability | OpenTelemetry + Prometheus + Grafana | Traces, metrics, dashboards |
| Containerization | Docker Compose (dev) / K8s (prod) | Standard |

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
│   ├── graph/                         # Knowledge graph (NEW from copilot)
│   │   ├── store.go                   # GraphStore interface
│   │   ├── neo4j.go                   # Neo4j implementation
│   │   ├── extractor.go               # KGExtractor interface
│   │   ├── llm_extractor.go           # LLM-based entity/relation extraction
│   │   └── model.go                   # Triple, Entity types
│   ├── rewrite/                       # Query rewriting (NEW from copilot)
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
│   │   │   └── chunk.go              # Includes BM25 search via tsvector
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
│   ├── docker-compose.yml
│   └── Dockerfile
├── testdata/
├── Makefile
├── .env.example
└── go.mod
```

---

## Key Interfaces

All accept `context.Context` for tenant propagation and tracing.

### From plan-c2 (retained)

- **EmbeddingProvider** — `Embed(ctx, text)`, `EmbedBatch(ctx, texts)`, `Dimensions()`
- **LLMProvider** — `Complete(ctx, req)`, `Stream(ctx, req) <-chan StreamChunk`
- **DocumentLoader** — `Load(ctx, reader, filename)`, `SupportedMimeTypes()`
- **Chunker** — `Chunk(text) ([]Chunk, error)`
- **VectorStore** — `EnsureCollection`, `Upsert`, `Search`, `Delete`, `DeleteCollection`
- **SearchEngine** — `Search(ctx, SearchRequest) ([]SearchResult, error)`
- **ObjectStore** — `Put`, `Get`, `Delete`, `Exists`
- **DatabaseRegistry** — `GetConfigs(ctx, dbName)`, `ListDatabases(ctx, activeOnly)`

### New from copilot branch

- **GraphStore** — Knowledge graph operations:
  ```go
  type GraphStore interface {
      AddTriples(ctx context.Context, triples []Triple) error
      QueryNeighborhood(ctx context.Context, entity string, depth int) ([]Triple, error)
      DeleteByDocument(ctx context.Context, docID string) error
  }
  ```

- **KGExtractor** — Entity/relation extraction from chunks:
  ```go
  type KGExtractor interface {
      Extract(ctx context.Context, chunks []Chunk) ([]Triple, error)
  }
  ```

- **QueryRewriter** — Query expansion/reformulation:
  ```go
  type QueryRewriter interface {
      Rewrite(ctx context.Context, query string) ([]string, error)
  }
  ```

- **Reranker** — Updated to support both API and local reranking:
  ```go
  type Reranker interface {
      Rerank(ctx context.Context, query string, docs []RerankDocument, topN int) ([]RerankResult, error)
  }
  ```

---

## Query Pipeline (Updated)

The full RAG query pipeline now has 5 stages (up from 3 in plan-c2):

```
User Query
    │
    ▼
1. REWRITE (NEW)
    │  PassthroughRewriter (default)
    │  MultiQueryRewriter (LLM generates N paraphrases)
    │  HyDERewriter (LLM generates hypothetical answer, embed that)
    │  StepBackRewriter (LLM generalizes query)
    │  → produces []string of query variants
    │
    ▼
2. RETRIEVE (parallel per query variant)
    │  Vector search (Qdrant) ─────────────┐
    │  BM25 search (Postgres tsvector) ────┤── RRF merge
    │  Graph context (Neo4j, optional) ────┘
    │  → merged, deduplicated results
    │
    ▼
3. RERANK
    │  Cohere Rerank API (high quality)
    │  — or —
    │  Cross-encoder sidecar (no API cost)
    │  → top-K reranked results
    │
    ▼
4. CONTEXT ASSEMBLY
    │  Select top-K chunks within token budget
    │  Deduplicate overlapping passages
    │  Build prompt: system + context + query
    │
    ▼
5. GENERATE
    │  Claude API (streaming or batch)
    │  → answer + source references + usage stats
```

### Graph-Augmented Retrieval Detail

When `use_graph=true` in the query request:

1. Extract key entities from the query (simple NER or LLM-based)
2. Call `GraphStore.QueryNeighborhood(entity, depth=1)` for each entity
3. Convert triples to synthetic chunks: `"{subject} {predicate} {object}"`
4. Include in RRF fusion with a base score (configurable, default 0.5)
5. Graph-sourced results tagged with `retrieval_method: "graph"`

This enables multi-hop reasoning (e.g., "Who funded the company that acquired X?") that pure vector/BM25 search cannot answer.

---

## REST API Routes (Final)

### Legacy Compat (rag_api contract, no auth)

```
GET  /health                              # { status, mongodb_connected, embedding_service_available }
GET  /databases?active_only=true          # { databases: [...], total }
GET  /databases/{database_name}           # { name, program, active, description, data }
POST /query/{database_name}               # { query, database, documents: [{content, score, metadata}], embedding, total_results }
```

### v1 API (tenant-scoped, API key auth)

```
# Collections
POST   /v1/collections
GET    /v1/collections
GET    /v1/collections/{id}
DELETE /v1/collections/{id}

# Documents
POST   /v1/collections/{id}/documents     # Multipart upload → async ingestion
GET    /v1/collections/{id}/documents
GET    /v1/documents/{id}                  # Includes processing status
DELETE /v1/documents/{id}

# Query (RAG generation)
POST   /v1/query                           # Full pipeline: rewrite → retrieve → rerank → generate
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
    TopK              int               `json:"top_k,omitempty"`          // default 5
    RewriteStrategies []string          `json:"rewrite_strategies,omitempty"` // ["passthrough"], ["hyde"], ["multi_query"]
    Filters           map[string]any    `json:"filters,omitempty"`
    UseGraph          bool              `json:"use_graph,omitempty"`      // default true
    Stream            bool              `json:"stream,omitempty"`
    CollectionID      string            `json:"collection_id,omitempty"`
    UseReranker       bool              `json:"use_reranker,omitempty"`   // default true
}

// Response
type QueryResponse struct {
    Answer           string        `json:"answer"`
    Sources          []Source      `json:"sources"`
    RewrittenQueries []string      `json:"rewritten_queries"`
    Usage            UsageStats    `json:"usage,omitempty"`
}

type Source struct {
    DocumentID   string         `json:"document_id"`
    ChunkID      string         `json:"chunk_id"`
    Content      string         `json:"content"`
    Score        float64        `json:"score"`
    Method       string         `json:"retrieval_method"` // "vector", "bm25", "graph", "hybrid"
    Metadata     map[string]any `json:"metadata,omitempty"`
}
```

---

## Multi-Tenancy Design

Same as plan-c2:
- **Auth**: `Authorization: Bearer <api_key>` → hash lookup → `tenant_id` in context
- **Postgres**: `tenant_id` on every table
- **Qdrant**: Collection-per-tenant (`tenant_{id}`)
- **Neo4j**: Label-based or property-based tenant scoping (`tenant_id` on all nodes/relationships)
- **Redis**: Key prefix `tenant:{id}:`
- **S3**: Object key prefix `{tenant_id}/`
- **Legacy endpoints**: No tenant scoping (matches existing behavior)

---

## Database Schema

Migrations 001–005 same as plan-c2 (tenants, api_keys, collections, documents, chunks with tsvector).

Additional:

```sql
-- 006: Legacy database registry (mirror of MongoDB ragList)
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

-- 007: Knowledge graph triples (for Postgres fallback, Neo4j is primary)
CREATE TABLE triples (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    doc_id      UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    subject     TEXT NOT NULL,
    predicate   TEXT NOT NULL,
    object      TEXT NOT NULL,
    metadata    JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(tenant_id, subject, predicate, object)
);
CREATE INDEX idx_triples_tenant ON triples(tenant_id);
CREATE INDEX idx_triples_subject ON triples(tenant_id, subject);
CREATE INDEX idx_triples_object ON triples(tenant_id, object);
CREATE INDEX idx_triples_doc ON triples(tenant_id, doc_id);

-- 008: Query log (expanded with rewrite info)
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
    latency_ms        INT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## Implementation Phases

### Phase 1: Project Scaffold
Go module, Makefile, Docker Compose (Postgres, Qdrant, Redis, MinIO, Neo4j, MongoDB, Jaeger), config struct, error types, slog logging, health endpoint.

**Docker Compose services**: postgres, qdrant, redis, minio, minio-setup, neo4j, mongodb, jaeger

**Files**: `go.mod`, `cmd/api/main.go`, `cmd/worker/main.go`, `internal/config/config.go`, `internal/platform/errors.go`, `internal/observability/logging.go`, `deploy/docker-compose.yml`, `deploy/Dockerfile`, `Makefile`, `.env.example`, `.gitignore`

### Phase 2: Database & Storage Layer
SQL migrations (001–008), pgx pool, CRUD for tenants, API keys, collections, documents, chunks, triples. BM25 search via tsvector.

**Files**: `migrations/001-008_*.sql`, `internal/storage/postgres/{db,tenant,apikey,collection,document,chunk,triple}.go`

### Phase 3: Object Storage & Auth
S3/MinIO client, API key auth resolver, tenant context, auth middleware.

**Files**: `internal/storage/objectstore/{store,s3}.go`, `internal/auth/auth.go`, `internal/tenant/{context,middleware}.go`

### Phase 4: Legacy FAISS Support
Python sidecar for FAISS index loading/search. Go client. MongoDB registry reader. Result merger. Legacy API handlers.

**Files**: `cmd/faiss-sidecar/{main.py,requirements.txt,Dockerfile}`, `internal/legacy/{faiss,distllm,tfidf,registry,merger}.go`, `internal/storage/mongodb/registry.go`, `internal/api/handler_legacy.go`, `internal/embedding/remote.go`

**Verification**: Legacy endpoints return identical responses to existing rag_api.

### Phase 5: Document Processing
Loaders (text, markdown, PDF, HTML, DOCX), loader registry, fixed-size chunker with token overlap.

**Files**: `internal/document/{model,loader,loader_*,chunker,metadata}.go`

### Phase 6: Embedding Service
OpenAI provider with batching, Redis embedding cache, remote embedder for legacy.

**Files**: `internal/embedding/{provider,openai,remote,cache}.go`, `internal/storage/redis/{client,cache}.go`

### Phase 7: Vector Store (Qdrant)
Qdrant gRPC client, collection-per-tenant, HNSW config, batch upsert, filtered search.

**Files**: `internal/vectorstore/{store,qdrant}.go`

### Phase 8: Knowledge Graph (NEW)
Neo4j Go driver integration. GraphStore interface + Neo4j impl. LLM-based entity/relation extraction (KGExtractor). Triples stored in both Neo4j and Postgres (fallback). Graph ingestion integrated into pipeline.

**Files**: `internal/graph/{store,neo4j,extractor,llm_extractor,model}.go`

**Key operations**:
- `AddTriples` — Store extracted triples in Neo4j (nodes + relationships)
- `QueryNeighborhood` — Cypher query expanding from entity node by depth
- `DeleteByDocument` — Remove all triples for a document
- `Extract` — LLM prompt: "Extract (subject, predicate, object) triples from this text"

### Phase 9: Ingestion Pipeline
River job queue. ProcessDocument job: fetch from S3 → parse → chunk → embed → store in Postgres + Qdrant + Neo4j (entity extraction). Pipeline.Ingest: validate → upload → create record → enqueue.

**Files**: `internal/ingestion/{pipeline,jobs,worker}.go`

### Phase 10: Query Rewriting (NEW)
QueryRewriter interface + implementations:
- **PassthroughRewriter** — Returns original query unchanged (default)
- **MultiQueryRewriter** — LLM generates N paraphrases, all used for retrieval
- **HyDERewriter** — LLM generates hypothetical answer, embed it as the query
- **StepBackRewriter** — LLM generalizes query for broader context

Each rewriter produces `[]string` of query variants. All variants are searched independently, results merged via RRF.

**Files**: `internal/rewrite/{rewriter,passthrough,multi_query,hyde,step_back}.go`

### Phase 11: Retrieval Engine
Hybrid search: for each query variant → embed → run vector (Qdrant) + BM25 (Postgres) + graph (Neo4j) in parallel → RRF merge → deduplicate across variants → rerank.

**Files**: `internal/search/{engine,vector,bm25,rrf,legacy}.go`, `internal/rerank/{reranker,cohere,crossencoder}.go`

### Phase 12: Generation Service
Claude API client with streaming. Prompt templates. Context assembly with token budgeting. RAG orchestrator wiring: rewrite → retrieve → rerank → assemble → generate.

**Files**: `internal/llm/{provider,claude,prompt}.go`, `internal/generation/{service,context}.go`

### Phase 13: REST API
Chi router with all route groups (legacy, v1, admin). Middleware stack. All handlers wired. SSE streaming. Rate limiting.

**Files**: `internal/api/{router,middleware,handler_*,request,response,sse}.go`, `internal/storage/redis/ratelimit.go`

### Phase 14: Observability
OTel tracing, Prometheus metrics, slog with trace correlation, HTTP middleware.

**Files**: `internal/observability/{otel,metrics,logging}.go`

### Phase 15: Testing
Unit tests (chunking, RRF, rewriters, loaders, graph extraction). Integration tests with testcontainers-go. Legacy compat tests. API handler tests. E2E: ingest → query round-trip.

**Files**: `*_test.go`, `testdata/`

### Phase 16: Migration Tooling (Future)
Tool to migrate legacy FAISS indices into Qdrant collections, enabling retirement of Python sidecar.

---

## Docker Compose

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

  faiss-sidecar:
    build: ./cmd/faiss-sidecar
    volumes:
      - ${FAISS_DATA_DIR:-./testdata/faiss}:/data:ro
    ports: ["50051:50051"]

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
| BM25 | Postgres tsvector (not Elasticsearch) | One fewer service; sufficient for MVP |
| Graph DB | Neo4j (primary) + Postgres triples (fallback) | Cypher for complex graph queries; Postgres for simple lookups |
| Job queue | River (not Celery) | Go-native, Postgres-backed, no Redis dependency for jobs |
| Legacy FAISS | Python sidecar (not CGO) | Lower risk, exact compat, isolates complexity |
| Reranker | Cohere API + cross-encoder option | API for quality, local for cost control |
| Query rewriting | Interface-driven, per-request config | Users choose strategy per query; extensible |
| Streaming | SSE (not WebSocket) | Simpler, widely supported |
| Multi-tenancy | Baked in from day 1 | Avoids costly retrofit |
| Legacy compat | Exact rag_api contract at root | Existing clients work without changes |

---

## Verification

1. `make docker-up` — start all services (Postgres, Qdrant, Redis, MinIO, Neo4j, MongoDB, Jaeger)
2. `make migrate-up` — run SQL migrations
3. `make build` — compile api + worker binaries
4. Create tenant + API key via admin endpoint
5. Create a collection, upload a sample document
6. Poll document status until "ready"
7. Verify KG triples extracted: `GET /v1/graph/entities`
8. `POST /v1/query` with `rewrite_strategies: ["passthrough"]` — basic RAG
9. `POST /v1/query` with `rewrite_strategies: ["hyde"]` — HyDE rewriting
10. `POST /v1/query` with `use_graph: true` — graph-augmented retrieval
11. `POST /v1/query/stream` — verify SSE streaming
12. `POST /v1/retrieve` — retrieve-only (no generation)
13. Start FAISS sidecar, load sample indices
14. `GET /health` — legacy health check
15. `GET /databases` — legacy database listing
16. `POST /query/{db}` — legacy query, verify merged results match existing rag_api
17. `make test` — all unit + integration tests pass
18. Check Jaeger for traces, Prometheus `/metrics` for counters
