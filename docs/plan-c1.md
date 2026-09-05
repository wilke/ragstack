# RagStack: Production-Ready Multi-Tenant RAG System

## Context

Building a greenfield multi-tenant RAG system in Go. The system needs to ingest documents (text, markdown, PDF, HTML, DOCX), chunk and embed them, store vectors in Qdrant, support hybrid search (vector + BM25), rerank with Cohere, and generate answers via Claude API with streaming. Multi-tenancy is required from day one.

**Tech stack**: Go, Chi, PostgreSQL (relational + tsvector BM25 + River job queue), Qdrant (vector search), Redis (cache/rate limiting), MinIO/S3 (object storage), OpenAI embeddings, Claude LLM, Cohere reranker, OpenTelemetry, Docker Compose.

---

## Project Structure

```
ragstack/
├── cmd/
│   ├── api/main.go                    # HTTP API server
│   └── worker/main.go                 # River background worker
├── internal/
│   ├── config/config.go               # Env-based config loading
│   ├── tenant/                        # Context propagation, middleware
│   ├── auth/auth.go                   # API key -> tenant resolution
│   ├── document/                      # Loaders (text/md/pdf/html/docx), chunker
│   ├── embedding/                     # EmbeddingProvider interface, OpenAI impl, cache
│   ├── vectorstore/                   # VectorStore interface, Qdrant impl
│   ├── search/                        # Hybrid search engine, BM25, vector, RRF merge
│   ├── rerank/                        # Reranker interface, Cohere impl
│   ├── llm/                           # LLMProvider interface, Claude impl, prompts
│   ├── generation/                    # RAG orchestrator (search -> rerank -> generate)
│   ├── ingestion/                     # River job pipeline (upload -> parse -> chunk -> embed -> store)
│   ├── storage/
│   │   ├── postgres/                  # DB pool, tenant/collection/document/chunk CRUD
│   │   ├── redis/                     # Cache, rate limiting
│   │   └── objectstore/               # S3/MinIO client
│   ├── api/                           # Chi router, handlers, middleware, SSE
│   ├── observability/                 # OTel tracing, Prometheus metrics, slog logging
│   └── platform/                      # Error types, pagination, validation
├── migrations/                        # SQL up/down migrations
├── deploy/
│   ├── docker-compose.yml             # Postgres, Qdrant, Redis, MinIO, Jaeger
│   └── Dockerfile
├── testdata/                          # Sample docs for testing
├── Makefile
├── .env.example
└── go.mod
```

---

## Key Interfaces

All accept `context.Context` for tenant propagation and tracing.

- **EmbeddingProvider** — `Embed(ctx, text) ([]float32, error)`, `EmbedBatch(ctx, texts) ([][]float32, error)`, `Dimensions() int`
- **LLMProvider** — `Complete(ctx, req) (*CompletionResponse, error)`, `Stream(ctx, req) (<-chan StreamChunk, error)`
- **Reranker** — `Rerank(ctx, query, docs, topN) ([]RerankResult, error)`
- **DocumentLoader** — `Load(ctx, reader, filename) (*LoadedDocument, error)`, `SupportedMimeTypes() []string`
- **VectorStore** — `EnsureCollection`, `Upsert`, `Search`, `Delete`, `DeleteCollection` — all scoped by tenantID
- **SearchEngine** — `Search(ctx, SearchRequest) ([]SearchResult, error)` — orchestrates hybrid search
- **ObjectStore** — `Put`, `Get`, `Delete`, `Exists`
- **Chunker** — `Chunk(text) ([]Chunk, error)` — fixed-size with overlap, extensible to semantic

---

## Multi-Tenancy Design

- **Identification**: `Authorization: Bearer <api_key>` -> hash lookup in `api_keys` table -> `tenant_id` injected into `context.Context`
- **Postgres**: `tenant_id` column on every table, composite indexes with tenant_id leading
- **Qdrant**: Collection-per-tenant (`tenant_{id}`), strong isolation, simple deletion
- **Redis**: Key prefix `tenant:{id}:`
- **S3**: Object key prefix `{tenant_id}/`

---

## Database Schema

5 migrations:

1. **tenants** + **api_keys** — tenant registry, API key auth (SHA-256 hashed keys, scopes, expiry)
2. **collections** — logical groupings per tenant (unique name per tenant)
3. **documents** — file metadata, S3 storage key, status enum (pending/processing/ready/failed), chunk_count
4. **chunks** — text content, chunk_index, token_count, metadata JSONB, `tsvector` column (auto-generated from content), GIN index for BM25
5. **query_log** — audit trail with latency tracking

River manages its own tables via built-in migrations.

---

## Implementation Phases

### Phase 1: Project Scaffold
Go module, Makefile, Docker Compose (Postgres, Qdrant, Redis, MinIO, Jaeger), config struct with env vars, error types, slog logging, health check endpoint.

**Files**: `go.mod`, `cmd/api/main.go`, `internal/config/config.go`, `internal/platform/errors.go`, `internal/observability/logging.go`, `deploy/docker-compose.yml`, `deploy/Dockerfile`, `Makefile`, `.env.example`, `.gitignore`

### Phase 2: Database & Storage Layer
SQL migrations, pgx connection pool, CRUD for tenants, API keys, collections, documents, chunks. BM25 search method on chunk store using `ts_rank_cd` + `plainto_tsquery`.

**Files**: `migrations/001-005_*.sql`, `internal/storage/postgres/{db,tenant,apikey,collection,document,chunk}.go`

### Phase 3: Object Storage & Auth
S3/MinIO client (aws-sdk-go-v2), API key auth resolver (hash lookup), tenant context helpers, auth middleware.

**Files**: `internal/storage/objectstore/{store,s3}.go`, `internal/auth/auth.go`, `internal/tenant/{context,middleware}.go`

### Phase 4: Document Processing
Loaders for text, markdown, PDF, HTML, DOCX. Loader registry (keyed by MIME type). Fixed-size chunker with token-based overlap using tiktoken-go. Metadata extraction.

**Files**: `internal/document/{model,loader,loader_text,loader_markdown,loader_pdf,loader_html,loader_docx,chunker,metadata}.go`

### Phase 5: Embedding Service
OpenAI embedding provider with batching. Redis-backed embedding cache (hash text -> cached vector, tenant-prefixed keys). Redis client initialization.

**Files**: `internal/embedding/{provider,openai,cache}.go`, `internal/storage/redis/{client,cache}.go`

### Phase 6: Vector Store (Qdrant)
Qdrant gRPC client wrapper. Collection-per-tenant CRUD. HNSW config (m=16, ef_construct=100). Cosine distance. Batch upsert. Filtered search.

**Files**: `internal/vectorstore/{store,qdrant}.go`

### Phase 7: Ingestion Pipeline
River job queue. `ProcessDocumentArgs` job: fetch from S3 -> detect MIME -> load -> chunk -> embed batch -> store chunks in Postgres -> store vectors in Qdrant -> update document status. `Pipeline.Ingest()`: validate -> upload to S3 -> create document record -> enqueue job.

**Files**: `internal/ingestion/{pipeline,jobs,worker}.go`, update `cmd/worker/main.go`

### Phase 8: Retrieval Engine
Hybrid search: embed query, run vector search (Qdrant) and BM25 search (Postgres) in parallel via errgroup, merge with Reciprocal Rank Fusion (k=60, configurable weights), optionally rerank with Cohere. Graceful fallback if reranker fails.

**Files**: `internal/search/{engine,vector,bm25,rrf}.go`, `internal/rerank/{reranker,cohere}.go`

### Phase 9: Generation Service
Claude API client with streaming SSE parsing. Prompt templates (system + user with chunk context). RAG orchestrator: search -> assemble context -> render prompt -> call LLM. Returns answer + source references + token usage. Streaming variant returns channel of events.

**Files**: `internal/llm/{provider,claude,prompt}.go`, `internal/generation/{service,context}.go`

### Phase 10: REST API
Chi router, middleware stack (request ID, logging, recovery, CORS, auth, rate limiting). Handlers for collections CRUD, document upload/list/get/delete, query (normal + SSE streaming), search-only, admin tenant management. Response helpers with typed error serialization.

**Routes**:
```
GET  /health, /ready
POST /v1/collections, GET /v1/collections, GET/DELETE /v1/collections/{id}
POST /v1/collections/{id}/documents, GET /v1/collections/{id}/documents
GET/DELETE /v1/documents/{id}
POST /v1/query, POST /v1/query/stream, POST /v1/search
POST /admin/tenants, GET /admin/tenants, POST /admin/tenants/{id}/api-keys
```

**Files**: `internal/api/{router,middleware,handler_document,handler_query,handler_collection,handler_tenant,request,response,sse}.go`, `internal/storage/redis/ratelimit.go`

### Phase 11: Observability
OTel SDK init with OTLP gRPC exporter. Prometheus counters/histograms (documents_ingested, query_duration, embedding_duration, chunks_stored). slog with trace correlation. HTTP middleware for tracing.

**Files**: `internal/observability/{otel,metrics,logging}.go`

### Phase 12: Testing
Unit tests for chunking, loaders, RRF, cache. Integration tests with testcontainers-go (Postgres, Qdrant, Redis, MinIO). HTTP handler tests with httptest. Interface mocks. Sample test documents in `testdata/`.

**Files**: `*_test.go` files, `testdata/sample.{pdf,md,docx,html,txt}`

---

## Key Dependencies (go.mod)

chi/v5, pgx/v5, river, go-redis/v9, qdrant/go-client, aws-sdk-go-v2, cohere-go/v2, golang-migrate/v4, tiktoken-go, ledongthuc/pdf, golang.org/x/net, otel, prometheus/client_golang, google/uuid, golang.org/x/sync, testify, testcontainers-go

---

## Verification

1. `make docker-up` — start all infra services
2. `make migrate-up` — run SQL migrations
3. `make build` — compile both binaries
4. Create tenant + API key via admin endpoint
5. Create a collection, upload a sample document
6. Poll document status until "ready"
7. Run a query — verify retrieval + generation
8. Run streaming query — verify SSE events
9. `make test` — all unit + integration tests pass
10. Check Jaeger UI for traces, Prometheus `/metrics` for counters
