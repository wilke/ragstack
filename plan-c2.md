# RagStack: Production-Ready Multi-Tenant RAG System (v2)

## Context

Building a multi-tenant RAG system in Go that replaces and extends two existing Python systems:

1. **distllm** (ramanathanlab/distllm) — HPC-scale distributed embedding pipeline with semantic chunking, FAISS indexing, and MCQA evaluation
2. **rag_api** (cucinellclark/rag_api) — FastAPI service that serves queries against pre-built FAISS indices (both semantic and TF-IDF)

The new system must support the **rag_api REST contract for backwards compatibility**, incorporate the **two existing FAISS index types** (distllm semantic + TF-IDF), and add new capabilities (Qdrant, hybrid search, LLM generation, multi-tenancy).

**Tech stack**: Go, Chi, PostgreSQL (relational + tsvector BM25 + River job queue), Qdrant (vector search), Redis (cache/rate limiting), MinIO/S3 (object storage), OpenAI embeddings, Claude LLM, Cohere reranker, OpenTelemetry, Docker Compose.

---

## Existing Systems Analysis

### distllm — Strengths

- **Semantic chunking**: Splits documents by embedding similarity between sentences (not just token count) — produces higher-quality chunks
- **HPC-native**: Parsl-based distributed embedding across GPU clusters
- **Scientific domain focus**: Supports biomedical models (PubMedBert, SFR-Embedding-Mistral), protein encoders (ESM2)
- **FAISS V2**: Supports IndexFlatIP, HNSW, binary quantization with rescoring
- **Rich evaluation**: MCQA module tracks retrieved chunk IDs, verifies source attribution, runs benchmarks (SciQ, PubMedQA, LitQA)
- **Flexible embedding**: Multiple encoder/pooler/embedder combinations, configurable via YAML

### rag_api — Strengths

- **Dual-retrieval**: Combines neural semantic search (distllm indices) with TF-IDF lexical search per database
- **Simple API**: Three endpoints — easy to integrate, stable contract
- **Multi-database**: Single API instance serves many databases with independent indices
- **Lazy index caching**: Indices loaded on first query and cached in memory
- **Score-based merging**: Results from both index types merged and sorted by similarity score

### The Two FAISS Index Types

| Aspect | distllm (Semantic) | TF-IDF (Lexical) |
|--------|-------------------|-------------------|
| **Embeddings** | Pre-computed neural vectors (e.g., SFR-Embedding-Mistral) | Sparse TF-IDF vectors from vocabulary + IDF weights |
| **Query encoding** | Remote embedding service API call | Local regex tokenization + TF-IDF computation (no API call) |
| **Storage on disk** | HuggingFace Dataset dir + FAISS binary index file | PyArrow batch files + Arrow vectorizer components file |
| **FAISS index type** | IndexFlatIP | IndexFlatIP |
| **Similarity** | Cosine (L2-normalized inner product) | Cosine (L2-normalized inner product) |
| **Config keys** | `dataset_dir`, `faiss_index_path` | `vectorizer_path`, `embeddings_path` |
| **Strength** | Semantic understanding, paraphrase matching | Exact keyword matching, fast, no external embedding deps |
| **Index registry** | MongoDB collection `ragList` in database `copilot` | Same |

---

## Backwards-Compatible API (rag_api contract)

The legacy API must be served under a `/compat` or root prefix. Exact contract:

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

## Project Structure (Updated)

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
│   ├── embedding/                     # EmbeddingProvider interface, OpenAI + remote impl, cache
│   │   ├── provider.go               # Interface
│   │   ├── openai.go                  # OpenAI implementation (new indices)
│   │   ├── remote.go                  # Generic HTTP embedding service (legacy distllm compat)
│   │   └── cache.go                   # Redis-backed cache
│   ├── vectorstore/                   # VectorStore interface, Qdrant impl
│   │   ├── store.go                   # Interface
│   │   └── qdrant.go                  # Qdrant implementation
│   ├── legacy/                        # Legacy FAISS index support
│   │   ├── faiss.go                   # FAISS index loading and search (CGO or subprocess)
│   │   ├── distllm.go                 # distllm index: HF Dataset + FAISS search
│   │   ├── tfidf.go                   # TF-IDF index: Arrow vectorizer + FAISS search
│   │   ├── registry.go               # Database config registry (MongoDB or Postgres)
│   │   └── merger.go                  # Multi-config result merging (sort by score)
│   ├── search/                        # Unified search engine
│   │   ├── engine.go                  # SearchEngine interface + hybrid engine
│   │   ├── vector.go                  # Qdrant vector search
│   │   ├── bm25.go                    # Postgres tsvector BM25 search
│   │   ├── rrf.go                     # Reciprocal Rank Fusion
│   │   └── legacy.go                  # Legacy FAISS search adapter
│   ├── rerank/                        # Reranker interface, Cohere impl
│   ├── llm/                           # LLMProvider interface, Claude impl, prompts
│   ├── generation/                    # RAG orchestrator (search -> rerank -> generate)
│   ├── ingestion/                     # River job pipeline
│   ├── storage/
│   │   ├── postgres/                  # DB pool, all CRUD
│   │   ├── redis/                     # Cache, rate limiting
│   │   ├── objectstore/               # S3/MinIO client
│   │   └── mongodb/                   # MongoDB client (legacy config registry, read-only)
│   ├── api/                           # Chi router, handlers, middleware, SSE
│   │   ├── router.go                  # Main router with both v1 and legacy routes
│   │   ├── handler_query.go           # New v1 query handler
│   │   ├── handler_legacy.go          # Legacy rag_api compat handlers
│   │   ├── handler_document.go
│   │   ├── handler_collection.go
│   │   ├── handler_tenant.go
│   │   └── ...
│   ├── observability/
│   └── platform/
├── migrations/
├── deploy/
│   ├── docker-compose.yml             # Postgres, Qdrant, Redis, MinIO, Jaeger, MongoDB
│   └── Dockerfile
├── testdata/
├── Makefile
├── .env.example
└── go.mod
```

### Key Changes from plan-c1

- **`internal/legacy/`** — New package for FAISS index support (distllm + TF-IDF)
- **`internal/embedding/remote.go`** — Generic HTTP embedding service client for legacy distllm queries
- **`internal/search/legacy.go`** — Adapter wrapping legacy FAISS search behind the SearchEngine interface
- **`internal/storage/mongodb/`** — Read-only MongoDB client for legacy database config registry
- **`internal/api/handler_legacy.go`** — Legacy rag_api endpoint handlers

---

## Key Interfaces (Updated)

All from plan-c1, plus:

- **LegacyIndexSearcher** — `Search(ctx, query, topK, scoreThreshold) ([]LegacyResult, queryEmbedding, error)` — wraps a single legacy FAISS index (distllm or tfidf)
- **DatabaseRegistry** — `GetConfigs(ctx, dbName) ([]DatabaseConfig, error)`, `ListDatabases(ctx, activeOnly) ([]DatabaseInfo, error)` — abstracts MongoDB (legacy) or Postgres (new) config storage
- **RemoteEmbedder** — Implementation of EmbeddingProvider that calls an external HTTP embedding service (for legacy distllm index queries)

---

## FAISS Integration Strategy

### Option: CGO with FAISS C bindings

Go can call FAISS via CGO using the FAISS C API. This avoids subprocess overhead.

```go
// internal/legacy/faiss.go
// #cgo LDFLAGS: -lfaiss_c
// #include <faiss/c_api/Index_c.h>
import "C"

type FaissIndex struct {
    index *C.FaissIndex
}

func LoadIndex(path string) (*FaissIndex, error) { ... }
func (idx *FaissIndex) Search(vectors []float32, topK int) (scores []float32, ids []int64, error) { ... }
func (idx *FaissIndex) Close() { ... }
```

### Option: Python subprocess (simpler, avoids CGO complexity)

Run a lightweight Python sidecar that loads FAISS indices and serves search requests over a Unix socket or HTTP. This preserves exact compatibility with the existing Python code.

```
cmd/faiss-sidecar/main.py   # Thin Python service loading FAISS + datasets
internal/legacy/client.go   # Go HTTP client calling the sidecar
```

### Recommendation

Start with the **Python sidecar** approach for Phase 1 (lower risk, exact compatibility). Migrate to CGO or pure-Go vector search (Qdrant) for new indices. Legacy FAISS indices remain read-only and served via sidecar until migrated to Qdrant.

---

## Legacy Index Data Loading

### distllm Index

```go
// internal/legacy/distllm.go
type DistllmIndex struct {
    faissIndex  *FaissIndex          // FAISS IndexFlatIP
    dataset     *HFDataset           // HuggingFace Dataset (text + metadata)
    embedder    embedding.Provider   // Remote embedding service for query encoding
}

func (idx *DistllmIndex) Search(ctx context.Context, query string, topK int, threshold float64) ([]LegacyResult, []float32, error) {
    // 1. Embed query via remote embedding service
    // 2. L2-normalize query embedding
    // 3. FAISS inner-product search
    // 4. Filter by score threshold
    // 5. Load document text from dataset by index
    // 6. Return results with metadata
}
```

Config from MongoDB:
```json
{ "dataset_dir": "/path/to/hf_dataset", "faiss_index_path": "/path/to/index.faiss" }
```

### TF-IDF Index

```go
// internal/legacy/tfidf.go
type TFIDFIndex struct {
    faissIndex  *FaissIndex          // FAISS IndexFlatIP over TF-IDF vectors
    vocabulary  []string             // From vectorizer_components.arrow
    idfValues   []float64            // From vectorizer_components.arrow
    vocabIndex  map[string]int       // Reverse lookup: word -> index
    texts       []string             // Document texts from batch arrow files
}

func (idx *TFIDFIndex) Search(ctx context.Context, query string, topK int, threshold float64) ([]LegacyResult, error) {
    // 1. Tokenize query: regex `(?u)\b\w\w+\b`, lowercase
    // 2. Compute term frequencies
    // 3. Build TF-IDF query vector: count * idf for each token
    // 4. L2-normalize query vector
    // 5. FAISS inner-product search
    // 6. Filter by score threshold
    // 7. Return results with metadata
}
```

Config from MongoDB:
```json
{ "vectorizer_path": "/path/to/dir", "embeddings_path": "/path/to/dir" }
```

---

## Legacy Result Merging

When a database name has multiple configs (e.g., both distllm and tfidf):

```go
// internal/legacy/merger.go
func MergeResults(resultSets [][]LegacyResult) []LegacyResult {
    // 1. Concatenate all result sets
    // 2. Sort by score descending
    // 3. Return all (do NOT truncate to top_k — legacy behavior)
}
```

Each result includes `metadata["program"]` = `"distllm"` or `"tfidf"` to indicate source.

---

## Database Config Registry

### Legacy Mode (MongoDB)

Read existing `ragList` collection from MongoDB:

```go
// internal/storage/mongodb/registry.go
type MongoRegistry struct {
    collection *mongo.Collection  // copilot.ragList
}

func (r *MongoRegistry) GetConfigs(ctx context.Context, dbName string) ([]DatabaseConfig, error) {
    // Find all documents with name=dbName, active=true
    // Return sorted by priority ascending
}

func (r *MongoRegistry) ListDatabases(ctx context.Context, activeOnly bool) ([]DatabaseInfo, error) {
    // List all or active-only configs
}
```

### New Mode (Postgres)

New databases registered via the v1 API use Postgres `collections` table.

### Unified Interface

```go
// internal/legacy/registry.go
type DatabaseRegistry interface {
    GetConfigs(ctx context.Context, dbName string) ([]DatabaseConfig, error)
    ListDatabases(ctx context.Context, activeOnly bool) ([]DatabaseInfo, error)
}
```

The legacy handler uses MongoRegistry. The v1 handler uses Postgres. Both can be composed if needed.

---

## REST API Routes (Updated)

```
# Legacy compat routes (no auth required — matches existing behavior)
GET  /health                              # HealthResponse (status, mongodb, embedding)
GET  /databases                           # DatabaseListResponse
GET  /databases/{database_name}           # DatabaseInfo
POST /query/{database_name}               # QueryResponse (merged results from FAISS indices)

# New v1 routes (tenant-scoped, API key auth)
GET  /v1/health
POST /v1/collections                      # Create collection
GET  /v1/collections                      # List collections
GET  /v1/collections/{id}                 # Get collection
DELETE /v1/collections/{id}               # Delete collection
POST /v1/collections/{id}/documents       # Upload document
GET  /v1/collections/{id}/documents       # List documents
GET  /v1/documents/{id}                   # Get document
DELETE /v1/documents/{id}                 # Delete document
POST /v1/query                            # Query with RAG generation
POST /v1/query/stream                     # Streaming query (SSE)
POST /v1/search                           # Search only (no generation)

# Admin routes
POST /admin/tenants                       # Create tenant
GET  /admin/tenants                       # List tenants
POST /admin/tenants/{id}/api-keys         # Create API key
```

---

## Multi-Tenancy Design

Same as plan-c1. Legacy endpoints operate **outside** the tenant model (no auth, matching existing behavior). The v1 endpoints are fully tenant-scoped.

---

## Database Schema

Same 5 migrations as plan-c1, plus:

6. **legacy_databases** (optional) — Mirror of MongoDB `ragList` for operational visibility:
```sql
CREATE TABLE legacy_databases (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        TEXT NOT NULL,
    program     TEXT NOT NULL,         -- 'distllm' or 'tfidf'
    active      BOOLEAN NOT NULL DEFAULT true,
    description TEXT,
    config_data JSONB NOT NULL,        -- dataset_dir, faiss_index_path, etc.
    priority    INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_legacy_db_name ON legacy_databases(name, active);
```

---

## Implementation Phases (Updated)

### Phase 1: Project Scaffold
Same as plan-c1. Add MongoDB to Docker Compose.

### Phase 2: Database & Storage Layer
Same as plan-c1. Add migration 006 for `legacy_databases`. Add MongoDB client in `internal/storage/mongodb/`.

### Phase 3: Object Storage & Auth
Same as plan-c1.

### Phase 4: Legacy FAISS Support (NEW)
Python sidecar for FAISS index loading and search. Go client to call sidecar. Supports both distllm and TF-IDF index types. MongoDB registry reader. Result merger. Legacy API handlers (`/health`, `/databases`, `/query/{db}`).

**Files**: `cmd/faiss-sidecar/main.py`, `internal/legacy/{faiss,distllm,tfidf,registry,merger}.go`, `internal/storage/mongodb/registry.go`, `internal/api/handler_legacy.go`, `internal/embedding/remote.go`

**Verification**: Legacy endpoints return identical responses to existing rag_api for the same indices.

### Phase 5: Document Processing
Same as plan-c1.

### Phase 6: Embedding Service
Same as plan-c1. Also add `RemoteEmbedder` for legacy distllm query embedding.

### Phase 7: Vector Store (Qdrant)
Same as plan-c1.

### Phase 8: Ingestion Pipeline
Same as plan-c1.

### Phase 9: Retrieval Engine
Same as plan-c1, plus `search/legacy.go` adapter that wraps legacy FAISS search behind the SearchEngine interface. This allows v1 collections to optionally include legacy indices as a search source.

### Phase 10: Generation Service
Same as plan-c1.

### Phase 11: REST API
Same as plan-c1, plus legacy compat routes mounted at root.

### Phase 12: Observability
Same as plan-c1.

### Phase 13: Testing
Same as plan-c1, plus:
- Compat tests: verify legacy endpoints match rag_api contract exactly
- FAISS sidecar integration tests with sample indices

### Phase 14: Legacy Index Migration Path (Future)
Tool to migrate legacy FAISS indices into Qdrant collections, enabling eventual retirement of the Python sidecar.

---

## Docker Compose (Updated)

Add MongoDB and FAISS sidecar:

```yaml
  mongodb:
    image: mongo:7
    ports:
      - "27017:27017"
    volumes:
      - mongo_data:/data/db
    healthcheck:
      test: ["CMD", "mongosh", "--eval", "db.adminCommand('ping')"]
      interval: 5s
      timeout: 5s
      retries: 5

  faiss-sidecar:
    build: ./cmd/faiss-sidecar
    volumes:
      - ${FAISS_DATA_DIR}:/data:ro    # Mount directory containing FAISS indices
    ports:
      - "50051:50051"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:50051/health"]
      interval: 5s
      timeout: 5s
      retries: 5
```

---

## Config (Updated)

Add to config struct:

```go
type LegacyConfig struct {
    Enabled           bool   `env:"LEGACY_ENABLED" default:"true"`
    FaissSidecarURL   string `env:"FAISS_SIDECAR_URL" default:"http://localhost:50051"`
    MongoDBURL        string `env:"MONGODB_URL"`
    MongoDBDatabase   string `env:"MONGODB_DATABASE" default:"copilot"`
    MongoDBCollection string `env:"MONGODB_COLLECTION" default:"ragList"`
}

type RemoteEmbeddingConfig struct {
    URL      string `env:"LEGACY_EMBEDDING_URL"`
    Model    string `env:"LEGACY_EMBEDDING_MODEL" default:"Salesforce/SFR-Embedding-Mistral"`
    APIKey   string `env:"LEGACY_EMBEDDING_API_KEY"`
    Timeout  time.Duration `env:"LEGACY_EMBEDDING_TIMEOUT" default:"30s"`
}
```

---

## Key Dependencies (Updated from plan-c1)

Same as plan-c1, plus:
- `go.mongodb.org/mongo-driver` — MongoDB client for legacy config registry

---

## Verification (Updated)

1-10: Same as plan-c1
11. Start FAISS sidecar with sample indices
12. `GET /health` — returns healthy with mongodb_connected and embedding_service_available
13. `GET /databases` — returns databases from MongoDB ragList
14. `POST /query/{db}` — returns merged results from distllm + tfidf indices
15. Compare legacy endpoint responses against existing rag_api output for identical queries
