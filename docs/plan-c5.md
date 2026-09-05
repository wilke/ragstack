# RagStack: Production-Grade Multi-Tenant RAG System (v5 — Consolidated)

## Context

This plan consolidates all prior design work (C1–C4, G1–G4) into a single authoritative implementation spec. It incorporates:

- **plan-c4** — Full implementation spec with project structure, interfaces, schemas, CI/CD, KG extraction design, cross-encoder sidecar, deployment profiles, and SLOs
- **plan-g4** — OIDC auth, mTLS, RLS policies, risk register, entity expansion rewriting, ACL-aware caching, audit logging

New in this version:

- **Decision Points** — Elaborated trade-off analysis for key architectural choices (Go vs Python, Qdrant vs pgvector, self-hosted model infrastructure via vLLM + Ray, deployment strategy)
- **Security hardening** — OIDC, mTLS, RLS, audit logging
- **Entity expansion** — 5th query rewriting strategy using KG entities
- **Cache intelligence** — ACL-aware keys, rewrite/KG bypass
- **Risk register** — Explicit risk/mitigation pairs

---

## Decision Points

These are the key architectural choices that shape the system. Each is analyzed with trade-offs so the team can make informed decisions and revisit them as requirements evolve.

### Decision 1: Go vs Python

The existing systems (distllm, rag_api) are Python. This plan recommends Go for the new system.

#### Option A: Go (Recommended)

**Advantages:**
- **Concurrency model** — Goroutines and channels handle the parallel retrieve (vector + BM25 + graph) pattern naturally with `errgroup`. Python requires `asyncio` or thread pools, which are harder to reason about under load.
- **Single binary deployment** — `go build` produces one static binary. No virtualenv, no pip conflicts, no Python version management across environments. The Dockerfile is `FROM scratch` + binary.
- **Memory footprint** — A Go API server serving 1000 req/s uses ~50-100MB RSS. An equivalent FastAPI/uvicorn setup uses ~300-500MB with workers. At multi-tenant scale, this matters.
- **Startup time** — Go binary starts in <100ms. Python with model loading, import chains, and uvicorn warmup takes 5-15s. This affects pod scaling, rolling deploys, and health check timing.
- **Type safety at compile time** — Interface contracts (`EmbeddingProvider`, `VectorStore`, `Reranker`) are enforced by the compiler. Refactoring is safer. Python relies on runtime duck typing or optional mypy.
- **Standard library** — `net/http`, `encoding/json`, `crypto`, `context` cover most needs without third-party deps.

**Disadvantages:**
- **ML ecosystem gap** — No native FAISS, no HuggingFace transformers, no sentence-transformers. ML components must be accessed via sidecars or API calls.
- **PDF/DOCX parsing** — Go libraries for document parsing are less mature than Python's (pdfplumber, python-docx, BeautifulSoup). May need to accept lower fidelity or use a Python parsing sidecar.
- **Team familiarity** — If the team is primarily Python, the learning curve for Go's interfaces, error handling, and concurrency patterns adds 2-4 weeks of ramp-up.
- **CGO pain** — Any Go library that wraps C code (FAISS, some PDF parsers) introduces CGO, which complicates cross-compilation and Docker builds.

**Mitigation for disadvantages:**
- FAISS: Python sidecar (already planned)
- Document parsing: Go libraries (ledongthuc/pdf, golang.org/x/net for HTML) are sufficient for text extraction; complex layout parsing can be deferred
- Team: Go's simplicity means a Python developer can be productive in 1-2 weeks for application-level code

#### Option B: Python

**When to choose Python instead:**
- The team is exclusively Python and has no Go experience
- The system will run heavy ML workloads in-process (fine-tuning, custom model inference)
- Time-to-prototype is more important than production performance
- The deployment target is a single machine, not Kubernetes

**If Python is chosen**, the recommended stack would be: FastAPI + asyncio, pgvector via asyncpg, Qdrant via qdrant-client, Celery or arq for job queue, pydantic for validation. The project structure and interfaces in this plan can be translated directly.

#### Decision

Both implementations are documented and share the same architecture, interfaces, and service dependencies.

**Python (current scaffold):** A working Python/FastAPI scaffold exists on the `main` branch (SPEC.md + `ragstack/` package). It implements the core protocols (`Embedder`, `VectorStore`, `TextIndex`, `GraphStore`, `QueryRewriter`, `Scorer`), in-memory dev stores, API route stubs, and Celery-based async ingestion. This provides a rapid-development path and serves as the reference implementation. Stack: FastAPI + asyncio, pydantic v2, Celery + Redis for job queue, pytest + pytest-asyncio.

**Go (production target):** Go with Python sidecars for ML-specific components (FAISS index serving, cross-encoder reranking). This isolates the ML complexity while keeping the core system fast and operationally simple. The Go implementation translates the same interfaces (`EmbeddingProvider`, `VectorStore`, `Reranker`, etc.) and pipeline stages into compiled, statically typed code.

**Shared architecture:** Both implementations use:
- The same service dependencies (Qdrant, Postgres, Redis, Neo4j, vLLM, cross-encoder sidecar)
- The same API contract (routes, request/response models)
- The same pipeline stages (rewrite → retrieve → rerank → generate)
- The same configuration model (env vars)

**When to use which:**
| Criterion | Python scaffold | Go implementation |
|-----------|----------------|-------------------|
| Prototyping & iteration speed | Preferred | — |
| Production multi-tenant deployment | — | Preferred |
| Team is Python-only | Use Python for production too | — |
| Team has Go experience | — | Preferred |
| Memory/startup constrained (K8s) | — | Preferred |
| In-process ML workloads needed | Preferred | — |

---

### Decision 2: Qdrant vs pgvector

Both are viable vector stores for <5M chunks. The choice affects operational complexity, tenant isolation, and performance characteristics.

#### Option A: Qdrant (Recommended for multi-tenant)

**Advantages:**
- **Collection-per-tenant isolation** — Each tenant gets a separate Qdrant collection (`tenant_{id}`). Hard isolation: one tenant's data cannot leak to another even with a bug in query filtering. Deletion is `DeleteCollection` — clean and atomic.
- **Purpose-built HNSW** — Qdrant's HNSW implementation is tuned for vector search. It supports quantization (scalar, product, binary), payload filtering during search (not post-filter), and configurable HNSW parameters per collection.
- **No shared resources** — Vector search workload doesn't compete with relational queries, BM25 search, or job queue operations for Postgres connections, memory, or I/O.
- **Horizontal scaling** — Qdrant supports sharding and replication natively. When you outgrow a single node, scaling is a config change, not an application rewrite.
- **Filtered search** — Qdrant applies payload filters during HNSW traversal (pre-filtering), not after. This means `top_k=5 WHERE tenant_id=X` returns exactly 5 results, not "5 results then filter down to 2."

**Disadvantages:**
- **Additional service** — One more thing to deploy, monitor, back up, and upgrade. Adds ~500MB-1GB memory for <5M vectors.
- **Network hop** — Vector search goes over gRPC instead of a local Postgres query. Adds ~1-5ms latency per call (negligible vs HNSW search time).
- **No transactional consistency** — Chunk insert to Postgres and vector upsert to Qdrant are two separate operations. If one fails, you need compensation logic (retry or cleanup). With pgvector, both are in one transaction.
- **Schema sync** — Metadata in Qdrant payloads must be kept in sync with Postgres. This is manageable but adds a class of bugs that pgvector avoids.

#### Option B: pgvector

**Advantages:**
- **One fewer service** — Vectors live in Postgres alongside metadata, BM25 index, and job queue. One backup, one connection pool, one monitoring target.
- **Transactional consistency** — `INSERT INTO chunks (..., embedding) VALUES (...)` is atomic. No distributed transaction coordination.
- **Simpler queries** — A single SQL query can join metadata filters, BM25 scores, and vector distance:
  ```sql
  SELECT c.*, c.embedding <=> $1 AS distance, ts_rank_cd(c.tsv, $2) AS bm25
  FROM chunks c
  WHERE c.tenant_id = $3
  ORDER BY distance
  LIMIT 10
  ```
- **Existing Postgres expertise** — Most teams already know Postgres operations (backup, replication, tuning, monitoring).

**Disadvantages:**
- **Soft tenant isolation** — All vectors share one table with a `tenant_id` column. Isolation depends entirely on correct `WHERE` clauses. A missing filter is a data leak.
- **Resource contention** — Vector similarity search (CPU-intensive HNSW traversal) competes with relational queries, BM25 search, and River job processing. Under load, one workload degrades the others.
- **Index rebuild cost** — Adding vectors requires periodic `REINDEX` for IVFFlat, or ongoing maintenance for HNSW. At 5M rows, HNSW index builds take 10-30 minutes and lock the table.
- **Scaling ceiling** — pgvector on a single Postgres instance tops out around 5-10M vectors with acceptable latency. Beyond that, you need to shard Postgres or migrate to a dedicated vector store.
- **Post-filtering** — pgvector applies WHERE filters after the HNSW scan. A query for tenant X's 5 most similar chunks may scan 500 chunks, filter to 3, and return fewer than requested.

#### Decision

**Qdrant** for production multi-tenant deployments. The hard isolation, dedicated resources, and pre-filtered search justify the operational overhead. **pgvector** is acceptable for single-tenant deployments or prototyping where operational simplicity is paramount.

The `VectorStore` interface abstracts this choice — switching from Qdrant to pgvector requires only a new implementation file, no changes to the retrieval pipeline.

```go
// internal/vectorstore/store.go — identical interface regardless of backend
type VectorStore interface {
    EnsureCollection(ctx context.Context, tenantID string, dim int) error
    Upsert(ctx context.Context, tenantID string, vectors []Vector) error
    Search(ctx context.Context, tenantID string, query []float32, topK int, filters map[string]any) ([]SearchResult, error)
    Delete(ctx context.Context, tenantID string, ids []string) error
    DeleteCollection(ctx context.Context, tenantID string) error
}
```

**Provide both implementations:**
- `internal/vectorstore/qdrant.go` — Qdrant gRPC client (default)
- `internal/vectorstore/pgvector.go` — pgvector via pgx
- Selected by `VECTOR_STORE=qdrant|pgvector` env var

---

### Decision 2b: BM25 Engine — Postgres tsvector vs Elasticsearch

Both are viable for keyword/BM25 search. The choice affects operational complexity, query capabilities, and consistency with the existing Python scaffold.

#### Option A: Postgres tsvector (Recommended for simplicity)

**Advantages:**
- **No extra service** — BM25 search uses the same Postgres instance as metadata, job queue, and RLS policies. One fewer thing to deploy, monitor, and back up.
- **Transactional consistency** — Chunk text and tsvector index are updated in the same transaction. No sync lag between ingestion and searchability.
- **Good enough for most RAG workloads** — `ts_rank_cd` + `plainto_tsquery` with GIN index handles < 5M chunks with acceptable latency.
- **Simpler tenant isolation** — RLS policies on the `chunks` table automatically scope BM25 queries by tenant.

**Disadvantages:**
- **Limited query DSL** — No field boosting, no fuzzy matching, no proximity queries, no aggregations. For simple keyword recall in a RAG pipeline, this is usually sufficient.
- **Scaling ceiling** — At > 5M chunks, GIN index size and query latency may become a concern on a single Postgres instance.
- **Language support** — tsvector supports multiple languages but requires explicit configuration per language. Elasticsearch handles this more transparently.

#### Option B: Elasticsearch 8.x

**Advantages:**
- **Rich query DSL** — Field boosting, fuzzy matching, phrase queries, span queries, aggregations. Useful if BM25 retrieval quality is critical and needs fine-tuning.
- **Purpose-built for text search** — Optimized inverted index, better tokenization, built-in analyzers for 30+ languages.
- **Horizontal scaling** — Native sharding and replication for > 5M chunks.
- **Used in existing scaffold** — The Python scaffold on `main` uses Elasticsearch, so adopting it maintains consistency with the working prototype.

**Disadvantages:**
- **Additional service** — One more JVM-based service to deploy, monitor, tune, and upgrade. Adds ~1-2GB memory for a small index.
- **Sync complexity** — Chunk ingestion must write to both Postgres (metadata) and Elasticsearch (text index). If one fails, compensation logic is needed.
- **Tenant isolation** — Requires index-per-tenant or query-time filtering. No built-in equivalent to Postgres RLS.
- **JVM operational overhead** — Heap tuning, GC pauses, shard rebalancing, split-brain risk in clusters.

#### Decision

**Provide both implementations:**
- Postgres tsvector: `internal/search/bm25_postgres.go` / `ragstack/stores/postgres_text.py` (default)
- Elasticsearch: `internal/search/bm25_elasticsearch.go` / `ragstack/stores/elasticsearch.py`
- Selected by `BM25_ENGINE=postgres|elasticsearch` env var

Default to Postgres tsvector for simplicity. Switch to Elasticsearch when the query DSL or scaling requirements exceed what tsvector provides. The `TextIndex` interface is the same regardless of backend.

---

### Decision 3: Self-Hosted Model Infrastructure (vLLM + Ray)

All model inference — embeddings, LLM generation, KG extraction, and reranking — runs on self-hosted infrastructure using open-source models. vLLM is the unified serving layer and Ray provides distributed GPU orchestration.

**Note on extensibility:** All model interactions go through interfaces (`EmbeddingProvider`, `LLMProvider`, `KGExtractor`, `Reranker`). These interfaces are backend-agnostic. Commercial API implementations (OpenAI, Claude, Cohere) can be added as additional provider implementations if requirements change, but they are not shipped or documented as defaults.

#### Model Infrastructure

| Component | Model | Runtime | Hardware | Latency |
|-----------|-------|---------|----------|---------|
| Embeddings | BAAI/bge-base-en-v1.5 (768 dims) | vLLM or sentence-transformers sidecar | 1x A10G (24GB) | ~50ms/batch |
| LLM (generation) | Llama Scout 17B / Mistral-7B-Instruct-v0.3 | vLLM + Ray | 1-2x A100 (80GB) | ~200-500ms TTFT |
| KG extraction | Llama-3.1-8B-Instruct (shared vLLM endpoint) | vLLM (shared) | Shared with LLM | ~500ms |
| Reranker | BAAI/bge-reranker-v2-m3 | sentence-transformers sidecar | CPU or 1x T4 | ~150ms |

#### vLLM + Ray Architecture

- **vLLM** serves all LLM workloads (generation, KG extraction, query rewriting) via its OpenAI-compatible API (`/v1/chat/completions`, `/v1/completions`)
- **Ray** serves as the distributed orchestration layer for multi-GPU tensor parallelism and multi-node serving. It handles worker health monitoring, automatic restart, and GPU scheduling
- **Embedding** can be served either through vLLM's `/v1/embeddings` endpoint or a separate lightweight sentence-transformers HTTP sidecar (recommended for isolation from LLM workloads)
- The Go API client talks standard OpenAI wire format to all vLLM endpoints — no custom protocol

#### Model Selection for vLLM Serving

| Model | Parameters | Use Case | Min VRAM | Context | Notes |
|-------|-----------|----------|----------|---------|-------|
| Llama Scout 17B | 17B | Generation (default) | 40GB (FP16) / 20GB (INT8) | 128K | Best quality/size ratio for RAG generation |
| Mistral-7B-Instruct-v0.3 | 7B | Generation (resource-constrained) | 16GB (FP16) / 8GB (INT8) | 32K | Fast inference; limited context window |
| Llama-3.1-8B-Instruct | 8B | KG extraction, rewriting | 18GB (FP16) / 9GB (INT8) | 128K | Good for structured output (JSON triples) |
| Llama-3.1-70B-Instruct | 70B | High-quality generation | 140GB (FP16, multi-GPU) | 128K | Requires Ray tensor parallelism across 2+ A100s |

**Quantization:** vLLM supports AWQ and GPTQ quantization, reducing VRAM requirements by ~50% with minimal quality loss. INT8 quantization is recommended for deployments on A10G GPUs.

**Context window implications:** For typical RAG workloads (system prompt ~200 tokens + 5-10 chunks at 300 tokens each + query ~50 tokens = ~1,750-3,250 tokens), even Mistral-7B's 32K window is sufficient. The larger 128K windows become relevant for multi-document synthesis or extensive conversation history.

**Ray distributed serving:** For models exceeding single-GPU VRAM (e.g., Llama-3.1-70B), Ray distributes the model across GPUs using tensor parallelism. Configuration: `VLLM_TENSOR_PARALLEL_SIZE=2` (or more). Ray also handles worker health monitoring and automatic restart.

#### Cost Model

All costs are GPU infrastructure, not per-token billing:

| Deployment | Hardware | Monthly Cost (spot) | Capacity |
|------------|----------|--------------------|---------|
| Small (7-8B models) | 1x A10G (24GB) | ~$500/month | Unlimited queries |
| Standard (17B models) | 1x A100 (80GB) | ~$2,000/month | Unlimited queries |
| Large (70B models) | 2x A100 (80GB) | ~$4,000/month | Unlimited queries |

Scaling is achieved via Ray adding GPU workers, not per-token billing. Monitor GPU utilization; right-size instances to avoid waste.

#### Advantages

- **Data stays internal** — No documents or queries leave your infrastructure. Full compliance with HIPAA, ITAR, and internal policies.
- **Deterministic latency** — No network variability, no rate limits, no provider outages.
- **Cost at scale** — GPU cost is fixed regardless of query volume. Break-even vs commercial APIs at ~5K queries/day.
- **Customization** — Fine-tune embeddings on domain data, use specialized models (PubMedBert for biomedical), adjust quantization for speed/quality trade-off.

#### Trade-offs

- **Operational complexity** — GPU drivers, CUDA versions, model loading, VRAM monitoring, OOM handling, model versioning, quantization tuning.
- **Scaling lag** — GPU node provisioning takes 5-10 minutes. Cannot burst instantly like API providers.
- **Quality considerations** — Open-weight models (7-17B) are strong for RAG generation and structured extraction but may lag frontier commercial models on complex reasoning tasks. For RAG use cases where the context provides the answer, this gap is minimal.

#### Future Extension: ALCF Sophia vLLM Endpoints

Argonne Leadership Computing Facility (ALCF) Sophia cluster provides GPU resources with vLLM-compatible inference endpoints.

- **Authentication:** Globus Auth integration required for endpoint access
- **Deployment model:** RagStack's vLLM client connects to ALCF-managed vLLM endpoints instead of self-managed infrastructure
- **Configuration:** Same `VLLM_URL` env var, plus `GLOBUS_CLIENT_ID` and `GLOBUS_CLIENT_SECRET` for auth
- **Scope:** Not designed in detail for v5. Requires: Globus auth middleware in the vLLM client, endpoint discovery, and token refresh logic

This is architecturally straightforward because the Go client already talks to vLLM's OpenAI-compatible API. Switching from a self-hosted endpoint to an ALCF-hosted endpoint is a URL change plus auth configuration.

ALCF clusters use Apptainer as the container runtime. The same `.sif` images built for Apptainer production deployment (see Decision 4, Option B) run on ALCF compute nodes with `--nv` for GPU access. The vLLM client configuration is identical — only `VLLM_URL` and Globus auth credentials change.

---

### Decision 4: Deployment Strategy

Docker Compose for development. Choose Apptainer or Kubernetes for production based on your infrastructure. The same container images are used in all environments — only the runtime and orchestration differ.

#### Option A: Docker Compose (Development & CI)

**When appropriate:** Local development, CI pipelines, single-node staging, small teams.

**Advantages:** Simple, `docker compose up` and you're running. Profiles control which services start.

**Disadvantages:** No auto-scaling, no rolling deploys, no health-based routing. Single node only.

#### Option B: Apptainer (HPC / Bare-Metal Production)

**When appropriate:** HPC clusters, bare-metal servers without Kubernetes, environments where only Apptainer is available (e.g., ALCF, university clusters).

**Advantages:**
- **Rootless by default** — No daemon, no root privileges required. HPC-friendly.
- **Same images** — `.sif` images built directly from Docker images (`apptainer build X.sif docker://ghcr.io/...`)
- **GPU access** — Simple `--nv` flag for NVIDIA GPU passthrough (no device reservation config)
- **Host networking** — Services communicate via `localhost:<port>` on single node. No overlay network complexity.
- **Portable** — `.sif` files are single-file, immutable, easy to transfer to air-gapped or HPC environments
- **Systemd integration** — Production deployments use systemd units for auto-restart, dependency ordering, and logging

**Disadvantages:** No built-in orchestration (no Compose equivalent). Multi-node requires explicit host configuration. No auto-scaling — manual process management.

**Orchestration approach:**
1. **Dev/testing** — Shell scripts (`deploy/apptainer/start.sh`, `stop.sh`) start all services as Apptainer instances
2. **Production** — Systemd unit files (one per service) with `After=/Requires=` dependency ordering, `Restart=on-failure`

**Deployment topology (single node):**

```
Host (bare-metal or VM)
├── apptainer instance: postgres     (port 5432, --bind /data/postgres)
├── apptainer instance: qdrant       (port 6333/6334, --bind /data/qdrant)
├── apptainer instance: redis        (port 6379, --bind /data/redis)
├── apptainer instance: neo4j        (port 7474/7687, --bind /data/neo4j, optional)
├── apptainer instance: minio        (port 9000/9001, --bind /data/minio, optional)
├── apptainer instance: api          (port 8080, --env-file ragstack.env)
├── apptainer instance: worker       (--env-file ragstack.env)
├── apptainer instance: crossencoder (port 50052, --nv optional)
└── GPU node (same or separate):
    └── vLLM (managed separately or via apptainer --nv)
```

**Multi-node:** Set explicit host addresses in env config (`POSTGRES_HOST=db-node`, `QDRANT_HOST=vector-node`, `VLLM_URL=http://gpu-node:8000`). No code changes — only env var configuration.

#### Option C: Kubernetes with Helm (Cloud / Enterprise Production)

**When appropriate:** Cloud infrastructure, multi-node with auto-scaling, >100 users, SLO commitments.

**Advantages:**
- **Rolling deploys** — Zero-downtime updates with readiness probes and rolling strategy
- **Auto-scaling** — HPA on CPU/memory/custom metrics (query QPS)
- **Health routing** — Liveness/readiness probes automatically restart unhealthy pods and remove them from load balancing
- **Resource isolation** — CPU/memory limits prevent one component from starving others
- **Secrets management** — Native integration with Vault, SealedSecrets, or cloud KMS

**Deployment topology:**

```
Namespace: ragstack
├── Deployment: api (2-5 replicas, HPA on CPU)
│   ├── Container: ragstack-api
│   └── Probes: /v1/health (liveness), /ready (readiness)
├── Deployment: worker (1-3 replicas, HPA on job queue depth)
│   └── Container: ragstack-worker
├── Deployment: faiss-sidecar (1 replica, optional)
│   └── Container: faiss-sidecar
│   └── Volume: faiss-data (ReadOnlyMany PVC)
├── Deployment: crossencoder-sidecar (1 replica, optional)
│   └── Container: crossencoder-sidecar
├── StatefulSet: postgres (1 replica or managed)
├── StatefulSet: qdrant (1 replica or managed)
├── StatefulSet: neo4j (1 replica, optional)
├── StatefulSet: redis (1 replica or managed)
├── Service: api-service (ClusterIP)
├── Ingress: api-ingress (TLS termination)
├── ConfigMap: ragstack-config
├── Secret: ragstack-secrets
└── HPA: api-hpa, worker-hpa
```

**Rollout strategy:**
1. **Blue/green for major versions** — Deploy new version alongside old, switch traffic via Ingress, keep old version for 1 hour rollback window
2. **Rolling for patches** — `maxUnavailable: 0, maxSurge: 1` ensures no downtime
3. **Canary for risky changes** — Route 10% of traffic to new version, monitor error rate and latency, promote or rollback

---

## SLOs & Scale Targets

| Metric | Target | Notes |
|--------|--------|-------|
| Index size | < 5M chunks | Vector store + Postgres sized accordingly |
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
Retrieve:    ~150ms  (vector + BM25 + optional graph, parallel)
Rerank:      ~250ms  (cross-encoder sidecar)
Context:      ~10ms  (assembly + dedup)
Generate:   ~1300ms  (vLLM streaming, TTFT ~200-500ms depending on model)
Overhead:     ~90ms  (network, serialization, middleware)
─────────────────────
Total:      ~2000ms
```

**Self-hosted latency note:** TTFT (time-to-first-token) for vLLM-served models depends on model size, quantization, and GPU type. Typical values: 150-300ms for 7-8B models on A10G, 300-500ms for 17B models on A100. Token generation speed: 30-80 tokens/sec. The 2s P99 SLO is achievable with models up to ~17B parameters on A100 hardware.

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
| Language | Go 1.23 | See Decision 1 |
| API router | Chi v5 | Lightweight, stdlib-compatible |
| Vector store | Qdrant (default) / pgvector (option) | See Decision 2 |
| Text search | PostgreSQL tsvector (default) / Elasticsearch 8.x (option) | See Decision 2b |
| Knowledge graph | Neo4j 5 | Cypher queries, mature, multi-hop reasoning |
| Primary DB | PostgreSQL 16 | Relational data, tsvector, River job queue |
| Cache | Redis 7 | Query cache, rate limiting, semantic cache |
| Object storage | S3-compatible (MinIO dev) | Raw document storage |
| Job queue | River | Postgres-native, transactional, no extra infra |
| Embeddings | Self-hosted BAAI/bge-base-en-v1.5 via HTTP sidecar | See Decision 3 |
| LLM | vLLM (Llama Scout / Mistral-7B-Instruct) + Ray | See Decision 3 |
| Reranker | Local cross-encoder sidecar (bge-reranker-v2-m3) | See Decision 3 |
| KG extraction | Local LLM via vLLM (shared instance) | See Decision 3 |
| Cross-encoder | BAAI/bge-reranker-v2-m3 via Python sidecar | Self-hosted; GPU optional |
| Legacy FAISS | Python sidecar | Exact compat with existing indices |
| Auth | OIDC + API key | Enterprise SSO + programmatic access |
| Observability | OpenTelemetry + Prometheus + Grafana | Traces, metrics, dashboards |
| CI/CD | GitHub Actions + Docker + Apptainer/Helm | Test, build, deploy pipeline |
| Containerization | Docker Compose (dev) / Apptainer or K8s (prod) | See Decision 4 |
| In-cluster security | mTLS via service mesh or cert-manager | Zero-trust networking |

---

## Model Choices

This section catalogs every ML/AI model used in the system, with selection rationale, alternatives considered, and upgrade paths.

### Embedding Models

| Concern | Default | Alternative | Notes |
|---------|---------|-------------|-------|
| Provider | BAAI/bge-base-en-v1.5 (self-hosted) | bge-large-en-v1.5, nomic-embed-text-v1.5, SFR-Embedding-Mistral | Swappable via `EMBEDDING_PROVIDER` |
| Dimensions | 768 | 1024 (bge-large), 768 (nomic), 4096 (SFR) | Dimension stored per collection; mixed dims not supported within a collection |
| Context window | 8191 tokens | Same for all options | Default chunk size 300 tokens (configurable 200-500); well within limit |
| Normalization | L2-normalized by serving layer | Cosine similarity requires normalized vectors | vLLM and sentence-transformers handle normalization |
| Batch size | 32 texts per request | Configurable via `EMBEDDING_BATCH_SIZE` | Balances latency and throughput |

**Model comparison:**

| Model | Dims | MTEB Avg | Hardware | Latency (batch=32) |
|-------|------|----------|----------|---------------------|
| bge-base-en-v1.5 | 768 | 63.5 | A10G (24GB) or CPU | ~50ms |
| bge-large-en-v1.5 | 1024 | 64.2 | A10G (24GB) | ~80ms |
| nomic-embed-text-v1.5 | 768 | 62.3 | A10G (24GB) or CPU | ~40ms |
| SFR-Embedding-Mistral | 4096 | 67.6 | A100 (80GB) required | ~150ms |

**Selection rationale:** bge-base-en-v1.5 offers the best quality/cost ratio at 768 dimensions with MTEB 63.5. It runs on modest GPU hardware (A10G) or even CPU for low-throughput deployments. For scientific domains already using distllm, SFR-Embedding-Mistral maintains compatibility with existing FAISS indices (same vector space) but requires A100 hardware.

**Upgrade path:**
1. Start with bge-base-en-v1.5 (768 dims, fast inference, low VRAM)
2. If quality is insufficient for domain, evaluate bge-large-en-v1.5 (1024 dims)
3. For scientific domains, evaluate SFR-Embedding-Mistral (4096 dims, requires A100)
4. Changing embedding model requires re-embedding all documents (new collection, backfill, switchover)

**Re-embedding strategy when changing models:**
```
1. Create new Qdrant collection with new dimensions
2. River job: iterate all chunks, embed with new model, upsert to new collection
3. Atomic switchover: update collection config to point to new collection
4. Delete old collection after verification
```

### LLM Models (Generation)

| Concern | Default | Alternative | Notes |
|---------|---------|-------------|-------|
| Provider | vLLM (Llama Scout 17B) | Mistral-7B-Instruct-v0.3, Llama-3.1-8B-Instruct, Llama-3.1-70B-Instruct | Swappable via `LLM_PROVIDER` + `LLM_MODEL` |
| Context window | 128K tokens (Llama Scout) | 128K (Llama-3.1), 32K (Mistral-7B) | Token budget auto-adjusts |
| Streaming | SSE via OpenAI-compatible API | All vLLM models use the same streaming wire format | Standard `/v1/chat/completions` endpoint |
| Output quality | Very good (Llama Scout 17B) | Good (Llama-3.1-8B), Adequate (Mistral-7B) | See evaluation section |

**Model comparison for RAG generation:**

| Model | Context | Quality (human eval) | Hardware | Latency (TTFT) |
|-------|---------|---------------------|----------|-----------------|
| Llama Scout 17B | 128K | Very good | 1x A100 (80GB) | ~300ms |
| Llama-3.1-8B-Instruct | 128K | Good | 1x A10G (24GB) | ~200ms |
| Llama-3.1-70B-Instruct | 128K | Very good | 2x A100 (80GB, Ray TP) | ~500ms |
| Mistral-7B-Instruct-v0.3 | 32K | Adequate | 1x A10G (24GB) | ~150ms |

**Selection rationale:** Llama Scout 17B is the recommended default — best quality/size ratio for RAG generation. Llama-3.1-8B-Instruct is the cost-optimized alternative, requiring less VRAM and offering faster inference. Mistral-7B-Instruct-v0.3 is suitable for deployments with limited GPU resources or when the 32K context window is acceptable. All models are served via vLLM with Ray for distributed multi-GPU inference.

**Context window note:** For typical RAG workloads (system prompt ~200 tokens + 5-10 chunks at 300 tokens each + query ~50 tokens = ~1,750-3,250 tokens), even Mistral-7B's 32K window is sufficient. The larger 128K windows become relevant for multi-document synthesis or extensive conversation history.

**Prompt template:**
```
System: You are a helpful assistant that answers questions based on the provided context.
Use ONLY the information in the context to answer. If the context does not contain
enough information to answer, say so. Cite sources by referencing document IDs.

Context:
{chunks_with_metadata}

User: {query}
```

### LLM Models (KG Extraction)

| Concern | Default | Alternative | Notes |
|---------|---------|-------------|-------|
| Model | Llama-3.1-8B-Instruct via vLLM | Phi-3-mini, Mistral-7B-Instruct | Swappable via `KG_EXTRACTOR` + `KG_EXTRACTION_MODEL` |
| Structured output | JSON mode | Same | All options support structured output |
| Cost per chunk | GPU amortized (~$0.00005 at scale) | Shared vLLM instance | ~300 tokens/chunk average |

**Selection rationale:** KG extraction requires instruction following for structured output (JSON triples) but not deep reasoning. Llama-3.1-8B-Instruct with JSON mode provides reliable extraction quality and shares the vLLM infrastructure with the generation LLM. Phi-3-mini (3.8B) is viable for simpler corpora but has lower extraction recall.

### LLM Models (Query Rewriting)

| Concern | Default | Alternative | Notes |
|---------|---------|-------------|-------|
| Model | Same as generation LLM | Dedicated smaller model via separate vLLM endpoint | Shares LLM provider; no separate config |
| Cost per rewrite | GPU amortized | Shared vLLM endpoint | 1 LLM call per rewrite strategy |

Rewriting uses the same vLLM LLM as generation. If latency is a concern for rewriting, a dedicated smaller model (e.g., Mistral-7B) can be served on a separate vLLM endpoint. This optimization is deferred to Phase 2.

### Reranker Models

| Concern | Default | Alternative | Notes |
|---------|---------|-------------|-------|
| Model | BAAI/bge-reranker-v2-m3 (local) | bge-reranker-large, mxbai-rerank-large-v1 | Swappable via `RERANKER_TYPE` |
| Input | 40 documents (top-K from retrieval) | Configurable via `RERANK_INPUT_SIZE` | |
| Output | 5 documents (top-N after reranking) | Configurable via `RERANK_OUTPUT_SIZE` | |

**Model comparison:**

| Model | Params | Quality (nDCG@10) | Hardware | Latency (40 docs) |
|-------|--------|-------------------|----------|--------------------|
| bge-reranker-v2-m3 | 568M | 72.1 | CPU or GPU | ~200ms (CPU) |
| bge-reranker-large | 560M | 71.5 | CPU or GPU | ~200ms (CPU) |
| mxbai-rerank-large-v1 | 435M | 70.8 | CPU or GPU | ~180ms (CPU) |

**Selection rationale:** bge-reranker-v2-m3 is the default because it's multilingual, high-quality, and CPU-viable. All reranking runs locally via the cross-encoder sidecar — no external API dependency.

### Model Configuration Summary

```env
# Embedding
EMBEDDING_PROVIDER=local               # local (self-hosted via HTTP sidecar)
EMBEDDING_MODEL=BAAI/bge-base-en-v1.5  # model name for provider
EMBEDDING_DIMENSIONS=768
EMBEDDING_BATCH_SIZE=32

# LLM (generation + rewriting)
LLM_PROVIDER=vllm                      # vllm (self-hosted via OpenAI-compatible API)
LLM_MODEL=meta-llama/Llama-Scout-17B   # model name served by vLLM
LLM_MAX_TOKENS=4096                    # max output tokens
LLM_TEMPERATURE=0.1

# KG Extraction
KG_EXTRACTOR=local_llm                 # local_llm (vLLM, shared endpoint)
KG_EXTRACTION_MODEL=meta-llama/Llama-3.1-8B-Instruct

# Chunking
CHUNK_SIZE_TOKENS=300                  # target chunk size in tokens (200-500 range)
CHUNK_OVERLAP_TOKENS=75               # overlap between chunks (~25%)
CHUNK_MIN_TOKENS=50                   # minimum chunk size; smaller chunks are merged
CHUNK_SECTION_AWARE=true              # respect heading boundaries

# Retrieval
BM25_ENGINE=postgres                  # postgres (tsvector) | elasticsearch
ELASTICSEARCH_URL=http://localhost:9200  # only used when BM25_ENGINE=elasticsearch

RRF_K=60                              # RRF constant
RRF_DENSE_WEIGHT=0.7                  # dense (vector) weight in RRF fusion
RRF_SPARSE_WEIGHT=0.3                 # sparse (BM25) weight in RRF fusion
RRF_GRAPH_WEIGHT=0.5                  # graph-sourced result weight
RETRIEVE_OVER_FACTOR=3                # over-retrieve Nx candidates before reranking
ADJACENT_CHUNK_WINDOW=1               # fetch ±N adjacent chunks (0 to disable)
ADJACENT_SCORE_DECAY=0.8              # score multiplier for adjacent chunks

# Reranker
RERANKER_TYPE=crossencoder             # crossencoder | none
CROSSENCODER_URL=http://localhost:50052
CROSSENCODER_MODEL=BAAI/bge-reranker-v2-m3
RERANK_INPUT_SIZE=40
RERANK_OUTPUT_SIZE=5

# Confidence & Quality
MIN_RELEVANCE_THRESHOLD=0.25          # min top reranked score to proceed with generation
LOW_CONFIDENCE_BEHAVIOR=advisory      # advisory | proceed | error
FRESHNESS_CONFLICT_THRESHOLD=365      # days; flag if sources span more than this
FRESHNESS_BOOST_ENABLED=false         # boost recent sources in context assembly

# vLLM + Ray Infrastructure
VLLM_URL=http://localhost:8000           # vLLM OpenAI-compatible endpoint (generation + KG extraction)
VLLM_EMBEDDING_URL=http://localhost:8001 # vLLM or sentence-transformers embedding endpoint
RAY_HEAD_ADDRESS=ray://localhost:10001   # Ray head node for distributed serving
VLLM_TENSOR_PARALLEL_SIZE=1             # GPU count per model (increase for large models)
VLLM_MAX_MODEL_LEN=32768               # Max sequence length (adjust per model)
```

---

## Deployment Profiles

Not all services are required for every deployment. The system supports three profiles controlled by config flags. These profiles are **runtime-agnostic** — the same env vars (`BM25_ENGINE`, `GRAPH_ENABLED`, `RERANKER_TYPE`, etc.) control which services are needed whether running under Docker Compose, Apptainer, or Kubernetes.

### Minimal (3 services)

For development, testing, or single-tenant deployments without legacy or KG features. Apptainer: 3 instances (postgres, qdrant/pgvector, redis).

| Service | Required | Notes |
|---------|----------|-------|
| PostgreSQL | Yes | Relational data, BM25 (when `BM25_ENGINE=postgres`), River jobs |
| Qdrant or pgvector | Yes | If pgvector, no additional service needed |
| Redis | Yes | Cache, rate limiting |
| Elasticsearch | Optional | Only when `BM25_ENGINE=elasticsearch` |
| MinIO | Optional | Use local filesystem in dev |
| Neo4j | No | `GRAPH_ENABLED=false` |
| MongoDB | No | `LEGACY_ENABLED=false` |
| FAISS sidecar | No | `LEGACY_ENABLED=false` |
| Cross-encoder sidecar | No | `RERANKER_TYPE=none` |
| Jaeger | No | `OTEL_ENABLED=false` |

Config:
```env
LEGACY_ENABLED=false
GRAPH_ENABLED=false
VECTOR_STORE=pgvector
BM25_ENGINE=postgres              # postgres (no extra service) | elasticsearch
RERANKER_TYPE=none
OTEL_ENABLED=false
AUTH_MODE=apikey
```

### Standard (6–7 services)

For production without legacy FAISS support. Apptainer: 6–7 instances with systemd for auto-restart.

| Service | Required | Notes |
|---------|----------|-------|
| PostgreSQL | Yes | |
| Qdrant | Yes | |
| Redis | Yes | |
| MinIO/S3 | Yes | |
| Neo4j | Yes | |
| Jaeger/OTel collector | Yes | |
| Elasticsearch | Optional | Only when `BM25_ENGINE=elasticsearch` |

Config:
```env
LEGACY_ENABLED=false
GRAPH_ENABLED=true
VECTOR_STORE=qdrant
BM25_ENGINE=postgres              # postgres | elasticsearch
RERANKER_TYPE=crossencoder
OTEL_ENABLED=true
AUTH_MODE=oidc+apikey
```

### Full (9–10 services)

For production with legacy rag_api compatibility. Apptainer: 9–10 instances including legacy sidecars.

All services from Standard, plus:

| Service | Required |
|---------|----------|
| MongoDB | Yes |
| FAISS sidecar | Yes |
| Cross-encoder sidecar | Yes |

Config:
```env
LEGACY_ENABLED=true
GRAPH_ENABLED=true
VECTOR_STORE=qdrant
BM25_ENGINE=postgres              # postgres | elasticsearch
RERANKER_TYPE=crossencoder
OTEL_ENABLED=true
AUTH_MODE=oidc+apikey
```

---

## Project Structure

```
ragstack/
├── cmd/
│   ├── api/main.go                    # HTTP API server
│   ├── worker/main.go                 # River background worker
│   ├── faiss-sidecar/                 # Python FAISS service
│   │   ├── main.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   └── crossencoder-sidecar/          # Python cross-encoder service
│       ├── main.py
│       ├── requirements.txt
│       └── Dockerfile
├── internal/
│   ├── config/config.go               # Env-based config with sections
│   ├── tenant/                        # Context propagation, middleware
│   │   ├── context.go
│   │   └── middleware.go
│   ├── auth/                          # Authentication + authorization
│   │   ├── auth.go                    # Unified auth resolver (OIDC or API key)
│   │   ├── oidc.go                    # OIDC bearer token validation
│   │   ├── apikey.go                  # API key hash lookup
│   │   └── audit.go                   # Audit logger for auth + admin actions
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
│   │   ├── local.go                   # Self-hosted HTTP embedding service (default)
│   │   ├── remote.go                  # Generic HTTP embedding (legacy distllm)
│   │   └── cache.go                   # Redis-backed cache
│   ├── vectorstore/                   # Vector storage
│   │   ├── store.go                   # VectorStore interface
│   │   ├── qdrant.go                  # Qdrant gRPC implementation
│   │   └── pgvector.go               # pgvector implementation
│   ├── search/                        # Unified search engine
│   │   ├── engine.go                  # SearchEngine interface + hybrid impl
│   │   ├── vector.go                  # Vector search via VectorStore interface
│   │   ├── bm25.go                    # TextIndex interface
│   │   ├── bm25_postgres.go           # Postgres tsvector implementation (default)
│   │   ├── bm25_elasticsearch.go      # Elasticsearch 8.x implementation (option)
│   │   ├── rrf.go                     # Reciprocal Rank Fusion
│   │   ├── adjacent.go                # Adjacent chunk expansion (±N chunks)
│   │   ├── filters.go                 # ACL + freshness filters
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
│   │   ├── step_back.go               # Generalize query for broader context
│   │   └── entity_expand.go           # Expand query with KG entity synonyms/relations
│   ├── rerank/                        # Result reranking
│   │   ├── reranker.go                # Reranker interface
│   │   └── crossencoder.go            # Local cross-encoder via sidecar
│   ├── llm/                           # LLM providers
│   │   ├── provider.go                # LLMProvider interface
│   │   ├── vllm.go                    # vLLM OpenAI-compatible client + streaming
│   │   └── prompt.go                  # Prompt templates
│   ├── generation/                    # RAG orchestrator
│   │   ├── service.go                 # Query pipeline: rewrite -> search -> rerank -> check -> generate
│   │   ├── context.go                 # Context assembly, token budget
│   │   ├── confidence.go              # Relevance threshold check
│   │   └── freshness.go               # Date/freshness conflict detection
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
│   │   │   ├── triple.go             # KG triples CRUD (Postgres fallback)
│   │   │   └── querylog.go           # Query audit log
│   │   ├── redis/
│   │   │   ├── client.go
│   │   │   ├── cache.go              # ACL-aware query cache
│   │   │   └── ratelimit.go
│   │   ├── objectstore/
│   │   │   ├── store.go               # ObjectStore interface
│   │   │   └── s3.go
│   │   └── mongodb/
│   │       └── registry.go            # Read-only legacy config registry
│   ├── api/                           # HTTP transport
│   │   ├── router.go                  # All routes (v1 + legacy + admin)
│   │   ├── middleware.go              # Request ID, logging, recovery, CORS, auth, rate limit
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
│   ├── apptainer/
│   │   ├── start.sh                     # Single-node: start all Apptainer instances
│   │   ├── stop.sh                      # Single-node: stop all instances
│   │   ├── build-sif.sh                 # Build .sif images from Docker images
│   │   ├── ragstack.env.example         # Env file template for Apptainer
│   │   └── systemd/                     # Systemd unit files for production
│   │       ├── ragstack-postgres.service
│   │       ├── ragstack-qdrant.service
│   │       ├── ragstack-redis.service
│   │       ├── ragstack-api.service
│   │       ├── ragstack-worker.service
│   │       └── ragstack-crossencoder.service
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
│   ├── legacy_fixtures/               # Golden request/response for compat tests
│   └── eval_baseline.json             # nDCG/hit@5 baselines for regression
├── Makefile
├── .env.example
├── .golangci.yml
└── go.mod
```

---

## Key Interfaces

All accept `context.Context` for tenant propagation, tracing, and cancellation.

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

## Security

### Authentication

Two modes, selectable via `AUTH_MODE` env var:

**API Key (`AUTH_MODE=apikey`)**:
- `Authorization: Bearer <api_key>` -> SHA-256 hash lookup in `api_keys` table -> `tenant_id` injected into `context.Context`
- Scopes per key: `read`, `write`, `admin`
- Key expiry with automatic rejection

**OIDC + API Key (`AUTH_MODE=oidc+apikey`)**:
- OIDC bearer tokens validated against IdP (Keycloak, Auth0, Okta)
- `tenant_id` extracted from token claim (configurable claim name)
- API keys still supported for programmatic/CI access
- OIDC config:
  ```go
  type OIDCConfig struct {
      Issuer        string `env:"OIDC_ISSUER"`          // e.g., https://keycloak.example.com/realms/ragstack
      Audience      string `env:"OIDC_AUDIENCE"`        // e.g., ragstack-api
      TenantClaim   string `env:"OIDC_TENANT_CLAIM" default:"tenant_id"`
      JWKSURL       string `env:"OIDC_JWKS_URL"`        // auto-discovered from issuer if not set
      CacheTTL      time.Duration `env:"OIDC_CACHE_TTL" default:"5m"`
  }
  ```

**Legacy endpoints**: No auth (matches existing rag_api behavior).

### Authorization (RLS)

Row-Level Security policies apply to all tenant-scoped tables, including credentials and audit:

```sql
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE collections ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE triples ENABLE ROW LEVEL SECURITY;
ALTER TABLE query_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON tenants
    USING (id = current_setting('app.current_tenant_id')::uuid);

CREATE POLICY tenant_isolation ON api_keys
    USING (tenant_id = current_setting('app.current_tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id')::uuid);

CREATE POLICY tenant_isolation ON collections
    USING (tenant_id = current_setting('app.current_tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id')::uuid);

CREATE POLICY tenant_isolation ON documents
    USING (tenant_id = current_setting('app.current_tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id')::uuid);

CREATE POLICY tenant_isolation ON chunks
    USING (tenant_id = current_setting('app.current_tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id')::uuid);

CREATE POLICY tenant_isolation ON triples
    USING (tenant_id = current_setting('app.current_tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id')::uuid);

CREATE POLICY tenant_isolation ON query_log
    USING (tenant_id = current_setting('app.current_tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id')::uuid);

CREATE POLICY tenant_isolation ON audit_log
    USING (tenant_id = current_setting('app.current_tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id')::uuid);
```

**Setting `app.current_tenant_id`**

Auth middleware sets a session-local setting per request/job and the pgx pool must clear it on release. Use a connection helper that refuses to run without setting it:

```go
// internal/storage/postgres/tenantctx.go
func WithTenantConn(ctx context.Context, pool *pgxpool.Pool, tenantID uuid.UUID, fn func(ctx context.Context, conn *pgx.Conn) error) error {
    return pool.AcquireFunc(ctx, func(c *pgxpool.Conn) error {
        if _, err := c.Exec(ctx, "SET LOCAL app.current_tenant_id = $1", tenantID); err != nil {
            return err
        }
        defer c.Exec(ctx, "RESET app.current_tenant_id")
        return fn(ctx, c.Conn())
    })
}

func RequireTenant(ctx context.Context, pool *pgxpool.Pool) error {
    return pool.AcquireFunc(ctx, func(c *pgxpool.Conn) error {
        var hasSetting bool
        if err := c.QueryRow(ctx, "select current_setting('app.current_tenant_id', true) is not null").Scan(&hasSetting); err != nil {
            return err
        }
        if !hasSetting {
            return errors.New("tenant context missing")
        }
        return nil
    })
}
```

Handlers call `WithTenantConn` right after auth resolution; workers set it at job start. A test helper should assert the setting exists to prevent silent RLS bypass.

### In-Cluster mTLS

All inter-service communication inside the cluster uses mutual TLS:

- **Option A: Service mesh (Istio/Linkerd)** — Automatic mTLS between all pods; no application changes
- **Option B: cert-manager + app-level TLS** — cert-manager issues certs; Go servers configured with `tls.Config`

Recommended: Service mesh for Kubernetes production (transparent, no code changes). Skip for dev/Docker Compose. For Apptainer single-node deployments, all services communicate via localhost — no TLS needed. For multi-node Apptainer, use a reverse proxy (Caddy/nginx) with TLS termination, or configure application-level TLS via `tls.Config` in Go.

### Audit Logging

All admin actions and sensitive queries are logged to a dedicated audit table:

```sql
-- Migration 009
CREATE TABLE audit_log (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id   UUID,                    -- NULL for system-level actions
    actor       TEXT NOT NULL,           -- API key ID or OIDC subject
    action      TEXT NOT NULL,           -- e.g., "tenant.create", "apikey.create", "collection.delete"
    resource    TEXT NOT NULL,           -- e.g., "tenant:abc123", "collection:def456"
    metadata    JSONB NOT NULL DEFAULT '{}',
    ip_address  INET,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_tenant ON audit_log(tenant_id, created_at DESC);
CREATE INDEX idx_audit_action ON audit_log(action, created_at DESC);
```

```go
// internal/auth/audit.go
type AuditLogger interface {
    Log(ctx context.Context, action string, resource string, metadata map[string]any) error
}
```

Audit events are emitted for: tenant CRUD, API key CRUD, collection delete, document delete, config changes.

---

## Multi-Tenancy Design

- **Auth**: OIDC bearer or API key -> `tenant_id` in context
- **Postgres**: `tenant_id` column + RLS policies on every tenant-scoped table
- **Qdrant**: Collection-per-tenant (`tenant_{id}`), hard isolation
- **pgvector**: Same table, `tenant_id` column + RLS (soft isolation)
- **Neo4j**: `tenant_id` property on all nodes and relationships; Cypher queries always filter by tenant
- **Redis**: Key prefix `tenant:{id}:`
- **S3**: Object key prefix `{tenant_id}/`
- **Legacy endpoints**: No tenant scoping (matches existing rag_api behavior)

---

## Database Schema

### Migrations 001-005: Core

1. **tenants** + **api_keys** — tenant registry, API key auth (SHA-256 hashed keys, scopes, expiry)
2. **collections** — logical groupings per tenant (unique name per tenant)
3. **documents** — file metadata, S3 storage key, status enum (pending/processing/ready/failed), chunk_count, checksum
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

### Migration 009: Audit Log

```sql
CREATE TABLE audit_log (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id   UUID NOT NULL,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    resource    TEXT NOT NULL,
    metadata    JSONB NOT NULL DEFAULT '{}',
    ip_address  INET,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_tenant ON audit_log(tenant_id, created_at DESC);
CREATE INDEX idx_audit_action ON audit_log(action, created_at DESC);
```

### Migration 010: RLS Policies

```sql
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE collections ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE triples ENABLE ROW LEVEL SECURITY;
ALTER TABLE query_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;

-- app.current_tenant_id is set per-request in the pgx connection hook (see Multi-Tenancy Design)

CREATE POLICY tenant_isolation ON tenants
    USING (id = current_setting('app.current_tenant_id')::uuid);

CREATE POLICY tenant_isolation ON api_keys
    USING (tenant_id = current_setting('app.current_tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id')::uuid);

CREATE POLICY tenant_isolation ON collections
    USING (tenant_id = current_setting('app.current_tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id')::uuid);

CREATE POLICY tenant_isolation ON documents
    USING (tenant_id = current_setting('app.current_tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id')::uuid);

CREATE POLICY tenant_isolation ON chunks
    USING (tenant_id = current_setting('app.current_tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id')::uuid);

CREATE POLICY tenant_isolation ON triples
    USING (tenant_id = current_setting('app.current_tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id')::uuid);

CREATE POLICY tenant_isolation ON query_log
    USING (tenant_id = current_setting('app.current_tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id')::uuid);

CREATE POLICY tenant_isolation ON audit_log
    USING (tenant_id = current_setting('app.current_tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id')::uuid);
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
    |  EntityExpansionRewriter (expand with KG entity synonyms/relations)
    |  -> produces []string of query variants (capped at MAX_QUERY_VARIANTS=5)
    |
    v
2. RETRIEVE (parallel per query variant, ~150ms)
    |  Vector search (Qdrant/pgvector) ------+
    |  BM25 search (tsvector/ES) ------------+-- RRF merge (k=60)
    |  Graph context (Neo4j, optional) ------+
    |  Over-retrieve 2-3x top_k candidates for reranking
    |  RRF weights: dense=0.7, sparse=0.3 (configurable via RRF_DENSE_WEIGHT)
    |  ACL + freshness filters applied
    |  Adjacent chunk expansion (fetch chunks ±1 index from top results)
    |  -> merged, deduplicated results with expanded context
    |
    v
3. RERANK (~250ms)
    |  Cross-encoder sidecar (bge-reranker-v2-m3, self-hosted)
    |  -> top-K reranked results with relevance scores
    |
    v
4. CONFIDENCE CHECK (~1ms)
    |  If top reranked score < MIN_RELEVANCE_THRESHOLD (default 0.25):
    |    -> return low-confidence response:
    |       "I don't have enough relevant information to answer this question."
    |    -> include best-effort sources + flag: low_confidence=true
    |  Else: proceed to context assembly
    |
    v
5. CONTEXT ASSEMBLY (~10ms)
    |  Select top-K chunks within token budget
    |  Deduplicate overlapping passages
    |  Order chunks by document position (chunk_index) for coherence
    |  Build prompt: system + context + query
    |
    v
6. GENERATE (~1300ms)
    |  vLLM (streaming or batch, OpenAI-compatible API)
    |  Date/freshness conflict detection: if sources span >1yr,
    |    add advisory note to response metadata
    |  -> answer + source references + usage stats + timings + advisories
```

### Graph-Augmented Retrieval

When `use_graph=true` in the query request:

1. Extract key entities from the query (simple NER or LLM-based)
2. Call `GraphStore.QueryNeighborhood(entity, depth=1)` for each entity
3. Convert triples to synthetic chunks: `"{subject} {predicate} {object}"`
4. Include in RRF fusion with a configurable base score (default 0.5)
5. Graph-sourced results tagged with `retrieval_method: "graph"`

This enables multi-hop reasoning (e.g., "Who funded the company that acquired X?") that pure vector/BM25 search cannot answer.

### Entity Expansion Rewriting

The 5th rewrite strategy, `entity_expand`, leverages the KG to expand queries:

1. Extract entities from the query
2. For each entity, query KG for related entities (synonyms, parent concepts, co-occurring entities)
3. Generate expanded queries incorporating related entities
4. Example: "BRCA1 mutations in cancer" -> also searches "BRCA1 DNA repair breast cancer TP53" based on KG relationships

Requires `GRAPH_ENABLED=true`. Falls back to passthrough if KG is unavailable.

```go
// internal/rewrite/entity_expand.go
type EntityExpansionRewriter struct {
    graph     graph.GraphStore
    llm       llm.Provider    // optional: LLM to compose expanded query naturally
    maxExpand int             // max entities to expand (default 3)
}

func (r *EntityExpansionRewriter) Rewrite(ctx context.Context, query string) ([]string, error) {
    // 1. Extract entities from query (regex or LLM)
    // 2. For each entity, GraphStore.QueryNeighborhood(entity, depth=1)
    // 3. Collect related entity names
    // 4. Build expanded query: original + related entities
    // 5. Return [original_query, expanded_query]
}
```

### Adjacent Chunk Retrieval

After initial retrieval and RRF merge, the engine expands context by fetching chunks adjacent to high-scoring results. This recovers context that was split across chunk boundaries during ingestion.

```go
// internal/search/adjacent.go
type AdjacentExpander struct {
    chunkStore storage.ChunkStore
    window     int // chunks to fetch on each side (default 1)
}

func (e *AdjacentExpander) Expand(ctx context.Context, results []SearchResult) ([]SearchResult, error) {
    // 1. For each result, query chunks with same doc_id and chunk_index ± window
    // 2. Deduplicate against existing results
    // 3. Adjacent chunks inherit parent's score * ADJACENT_SCORE_DECAY (default 0.8)
    // 4. Tag with retrieval_method: "adjacent"
}
```

| Parameter | Default | Env Var | Rationale |
|-----------|---------|---------|-----------|
| Window size | 1 | `ADJACENT_CHUNK_WINDOW` | Fetch 1 chunk before and after each result. Set to 0 to disable. |
| Score decay | 0.8 | `ADJACENT_SCORE_DECAY` | Adjacent chunks scored at 80% of the parent chunk's score to preserve ranking signal. |
| Max expansion | 2x original count | — | Caps total results to prevent over-expansion for queries with many hits. |

This leverages the `chunk_index` field already stored in the `chunks` table (migration 004) and in Qdrant payloads.

### Hybrid Search Weight Defaults

RRF fusion combines dense (vector) and sparse (BM25) retrieval results. The weighting controls the relative influence of each signal.

| Parameter | Default | Env Var | Rationale |
|-----------|---------|---------|-----------|
| RRF k constant | 60 | `RRF_K` | Standard constant from the original RRF paper. Higher values compress rank differences. |
| Dense weight | 0.7 | `RRF_DENSE_WEIGHT` | Semantic search contributes 70% of the fused score. Empirically strong for general-purpose corpora. |
| Sparse weight | 0.3 | `RRF_SPARSE_WEIGHT` | BM25 contributes 30%, primarily catching keyword matches that dense retrieval misses. |
| Graph weight | 0.5 | `RRF_GRAPH_WEIGHT` | Graph-sourced synthetic chunks scored at 50% when included. |
| Over-retrieval factor | 3x | `RETRIEVE_OVER_FACTOR` | Retrieve 3x top_k candidates before reranking, to give the reranker a broader candidate pool. |

Weights are configurable per request via the API (e.g., `"rrf_weights": {"dense": 0.5, "sparse": 0.5}`) to allow per-query tuning. Defaults can also be set per collection.

### Confidence Threshold

A minimum relevance score check after reranking prevents the system from generating answers when retrieval quality is too low, reducing hallucination risk.

| Parameter | Default | Env Var | Rationale |
|-----------|---------|---------|-----------|
| Min relevance threshold | 0.25 | `MIN_RELEVANCE_THRESHOLD` | Top reranked result must exceed this score. Below this, the system returns a low-confidence advisory instead of generating. |
| Low-confidence behavior | Advisory response | `LOW_CONFIDENCE_BEHAVIOR` | Options: `advisory` (return "I don't have enough information" + best sources), `proceed` (generate anyway with warning flag), `error` (return 422). |

The threshold applies to `/v1/query` (generation endpoint). `/v1/retrieve` always returns results regardless of score, since the caller is expected to handle relevance filtering.

```go
// internal/generation/confidence.go
type ConfidenceCheck struct {
    minThreshold float64
    behavior     string // "advisory", "proceed", "error"
}

func (c *ConfidenceCheck) Evaluate(topScore float64) *LowConfidenceResult {
    if topScore >= c.minThreshold {
        return nil // proceed normally
    }
    // Return result based on configured behavior
}
```

### Date/Freshness Conflict Detection

When retrieved chunks span significantly different time periods, the system flags potential conflicts in the response metadata. This prevents users from receiving outdated information mixed with current data without awareness.

Detection logic (applied during context assembly):
1. Extract date metadata from each chunk's `metadata` JSONB field (`source_date`, `created_at`)
2. If the date range across retrieved chunks exceeds `FRESHNESS_CONFLICT_THRESHOLD` (default: 365 days), set `advisories.freshness_conflict = true`
3. Include in response: `"advisory": "Sources span multiple time periods (2022–2025). More recent sources may supersede earlier ones."`
4. Optionally sort chunks with `freshness_boost` to prioritize recent sources (configurable via `FRESHNESS_BOOST_ENABLED`, default: false)

This is advisory-only — the system does not suppress older chunks, since historical context can be valuable. The flag lets downstream consumers (UI, agents) decide how to present the information.

### Graceful Degradation

| Failure | Behavior |
|---------|----------|
| Qdrant/pgvector down | Fall back to BM25-only retrieval; log warning |
| Neo4j down | Skip graph retrieval; `use_graph` silently disabled |
| Cross-encoder sidecar down | Return RRF-merged results without reranking; confidence check uses RRF scores |
| FAISS sidecar down | Legacy endpoints return 503; v1 endpoints unaffected |
| Embedding service down | Return error (no fallback; embeddings are required for vector search) |
| vLLM down | `/v1/retrieve` still works; `/v1/query` returns error |
| OIDC provider down | Fall back to API key auth if `AUTH_MODE=oidc+apikey` |
| All retrieval scores below threshold | Return low-confidence advisory with best-effort sources (no hallucinated answer) |

---

## Caching Strategy

### Embedding Cache

Redis-backed, tenant-prefixed. Avoids redundant embedding inference for repeated text.

```
Key:    tenant:{tid}:emb:{sha256(text)}
Value:  []float32 (gob-encoded)
TTL:    24h (configurable)
```

### Query Response Cache

ACL-aware caching that prevents cross-tenant cache pollution:

```
Key:    tenant:{tid}:query:{sha256(normalized_query + acl_hash)}
Value:  serialized QueryResponse
TTL:    5min (short; balances freshness and cost)
```

**Cache bypass rules:**
- Bypass when `rewrite_strategies` includes any non-passthrough strategy (rewritten queries vary per invocation)
- Bypass when `use_graph=true` (graph context may change as new documents are ingested)
- Bypass when `Cache-Control: no-cache` header is present
- Invalidate on document ingest/delete for the affected collection

```go
// internal/storage/redis/cache.go
func (c *QueryCache) Key(tenantID, query string, aclHash string) string {
    normalized := strings.ToLower(strings.TrimSpace(query))
    h := sha256.Sum256([]byte(normalized + "|" + aclHash))
    return fmt.Sprintf("tenant:%s:query:%x", tenantID, h)
}

func (c *QueryCache) ShouldBypass(req *QueryRequest) bool {
    if req.UseGraph { return true }
    for _, s := range req.RewriteStrategies {
        if s != "passthrough" { return true }
    }
    return false
}
```

---

## Knowledge Graph Extraction — Detailed Design

### Model Selection

| Concern | Choice | Rationale |
|---------|--------|-----------|
| Extraction model | Llama-3.1-8B-Instruct via vLLM | Shared vLLM infrastructure; reliable JSON mode; swap via `KG_EXTRACTOR` config |
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

- Average chunk: ~300 tokens -> GPU-amortized cost (shared vLLM instance)
- 5M chunks full extraction: bounded by GPU throughput, not per-token billing. At ~50 chunks/sec on A100, full extraction takes ~28 hours.
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

### Go Client

```go
// internal/rerank/crossencoder.go
type CrossEncoderReranker struct {
    baseURL    string
    httpClient *http.Client
}

func (r *CrossEncoderReranker) Rerank(ctx context.Context, query string, docs []RerankDocument, topN int) ([]RerankResult, error)
```

### Reranker Selection

The `RERANKER_TYPE` env var controls which implementation is used:

| Value | Implementation | Notes |
|-------|---------------|-------|
| `crossencoder` | Local cross-encoder sidecar | Default; self-hosted; requires sidecar running |
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

### Future: Qdrant migration (Phase 18)

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

### v1 API (tenant-scoped, OIDC/API key auth)

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

### Admin (requires `admin` scope)

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
    RewriteStrategies []string          `json:"rewrite_strategies,omitempty"` // ["passthrough"], ["hyde"], ["multi_query"], ["entity_expand"]
    Filters           map[string]any    `json:"filters,omitempty"`
    UseGraph          bool              `json:"use_graph,omitempty"`          // default true
    Stream            bool              `json:"stream,omitempty"`
    CollectionID      string            `json:"collection_id,omitempty"`
    UseReranker       bool              `json:"use_reranker,omitempty"`       // default true
    RRFWeights        *RRFWeights       `json:"rrf_weights,omitempty"`        // override default dense/sparse weights
}

type RRFWeights struct {
    Dense  float64 `json:"dense"`  // default 0.7
    Sparse float64 `json:"sparse"` // default 0.3
}

// Response
type QueryResponse struct {
    Answer           string        `json:"answer"`
    Sources          []Source      `json:"sources"`
    RewrittenQueries []string      `json:"rewritten_queries"`
    Usage            UsageStats    `json:"usage,omitempty"`
    Timing           TimingStats   `json:"timing"`
    LowConfidence    bool          `json:"low_confidence,omitempty"`    // true when top score < MIN_RELEVANCE_THRESHOLD
    Advisories       []Advisory    `json:"advisories,omitempty"`       // warnings about freshness conflicts, low confidence, etc.
}

type Advisory struct {
    Type    string `json:"type"`    // "freshness_conflict", "low_confidence"
    Message string `json:"message"` // Human-readable description
}

type Source struct {
    DocumentID   string         `json:"document_id"`
    ChunkID      string         `json:"chunk_id"`
    Content      string         `json:"content"`
    Score        float64        `json:"score"`
    Method       string         `json:"retrieval_method"` // "vector", "bm25", "graph", "hybrid", "adjacent"
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
Go module, Makefile, Docker Compose (all profiles), Apptainer scripts (start/stop/build-sif, systemd units), config struct with env vars and sections (server, db, redis, objectstore, auth, faiss, rewrite, rerank, kg, limits), error types, slog logging, health check endpoint.

**Files**: `go.mod`, `cmd/api/main.go`, `cmd/worker/main.go`, `internal/config/config.go`, `internal/platform/{errors,pagination,validation}.go`, `internal/observability/logging.go`, `deploy/docker-compose.yml`, `deploy/docker-compose.minimal.yml`, `deploy/Dockerfile`, `deploy/apptainer/{start,stop,build-sif}.sh`, `deploy/apptainer/ragstack.env.example`, `deploy/apptainer/systemd/*.service`, `Makefile`, `.env.example`, `.gitignore`, `.golangci.yml`

### Phase 2: Database & Storage Layer
SQL migrations (001-010), pgx connection pool with tenant-aware connections (`SET LOCAL app.current_tenant_id`), CRUD for tenants, API keys, collections, documents, chunks, triples, audit log. BM25 search via `ts_rank_cd` + `plainto_tsquery` (when `BM25_ENGINE=postgres`). RLS policies. TextIndex interface with Postgres tsvector and Elasticsearch implementations, selected by `BM25_ENGINE` env var.

**Files**: `migrations/001-010_*.sql`, `internal/storage/postgres/{db,tenant,apikey,collection,document,chunk,triple,querylog}.go`

### Phase 3: Object Storage & Auth
S3/MinIO client (aws-sdk-go-v2). Unified auth resolver supporting both OIDC and API key. OIDC token validation with JWKS caching. Tenant context helpers. Auth middleware. Audit logger.

**Files**: `internal/storage/objectstore/{store,s3}.go`, `internal/auth/{auth,oidc,apikey,audit}.go`, `internal/tenant/{context,middleware}.go`

### Phase 4: Legacy FAISS Support
Python sidecar for FAISS index loading and search. Go client to call sidecar. Supports both distllm and TF-IDF index types. MongoDB registry reader. Result merger. Legacy API handlers (`/health`, `/databases`, `/query/{db}`).

**Files**: `cmd/faiss-sidecar/{main.py,requirements.txt,Dockerfile}`, `internal/legacy/{faiss,distllm,tfidf,registry,merger}.go`, `internal/storage/mongodb/registry.go`, `internal/api/handler_legacy.go`, `internal/embedding/remote.go`

**Verification**: Legacy endpoints return identical responses to existing rag_api for the same indices.

### Phase 5: Document Processing
Loaders for text, markdown, PDF, HTML, DOCX. Loader registry (keyed by MIME type). Fixed-size chunker with token-based overlap using tiktoken-go. Metadata extraction. Document checksum for idempotent re-ingestion.

#### Chunker Configuration

| Parameter | Default | Env Var | Rationale |
|-----------|---------|---------|-----------|
| Chunk size | 300 tokens | `CHUNK_SIZE_TOKENS` | Balances semantic coherence with retrieval precision. 200-500 is the effective range; 300 is a safe starting point per empirical results. |
| Overlap | 75 tokens (~25%) | `CHUNK_OVERLAP_TOKENS` | Prevents loss of context at chunk boundaries. 20-25% overlap is the consensus recommendation. |
| Min chunk size | 50 tokens | `CHUNK_MIN_TOKENS` | Prevents near-empty trailing chunks that add noise to retrieval results. |
| Section-awareness | true | `CHUNK_SECTION_AWARE` | Respects heading boundaries (markdown `#`, HTML `<h1>`–`<h6>`) to keep section context intact. Splits at section breaks when possible, falling back to token-based splitting within sections. |

These defaults are tunable per collection via the collection settings API. Start with defaults; adjust based on evaluation metrics (nDCG, hit rate) for your specific document corpus.

**Files**: `internal/document/{model,loader,loader_text,loader_markdown,loader_pdf,loader_html,loader_docx,chunker,metadata}.go`

### Phase 6: Embedding Service
Self-hosted embedding provider (bge-base-en-v1.5) with batching via HTTP sidecar or vLLM `/v1/embeddings` endpoint. Redis-backed embedding cache (hash text -> cached vector, tenant-prefixed keys). Remote embedder for legacy distllm query embedding.

**Files**: `internal/embedding/{provider,local,remote,cache}.go`, `internal/storage/redis/{client,cache}.go`

### Phase 7: Vector Store
Qdrant gRPC client wrapper (collection-per-tenant, HNSW config m=16 ef_construct=100, cosine distance, batch upsert, filtered search). pgvector implementation (same interface, `tenant_id` column, HNSW index). Selected by `VECTOR_STORE` env var.

**Files**: `internal/vectorstore/{store,qdrant,pgvector}.go`

### Phase 8: Knowledge Graph
Neo4j Go driver integration. GraphStore interface + Neo4j implementation. LLM-based entity/relation extraction via local LLM (vLLM, shared endpoint). Triples stored in both Neo4j (primary) and Postgres (fallback). Predicate normalization with alias map.

**Files**: `internal/graph/{store,neo4j,extractor,llm_extractor,model}.go`

### Phase 9: Ingestion Pipeline
River job queue. Two job types:
- **ProcessDocumentJob**: fetch from S3 -> detect MIME -> load -> chunk -> embed batch -> store chunks in Postgres -> store vectors in Qdrant/pgvector -> update document status
- **ExtractTriplesJob**: for each chunk batch -> call KGExtractor -> store triples in Neo4j + Postgres

Pipeline.Ingest: validate -> check checksum for duplicates -> upload to S3 -> create document record -> enqueue ProcessDocumentJob (which enqueues ExtractTriplesJob on completion if `GRAPH_ENABLED=true`).

**Files**: `internal/ingestion/{pipeline,jobs,worker}.go`

### Phase 10: Query Rewriting
QueryRewriter interface + 5 implementations:
- **PassthroughRewriter** — Returns original query unchanged (default)
- **MultiQueryRewriter** — LLM generates N paraphrases, all used for retrieval
- **HyDERewriter** — LLM generates hypothetical answer, embed it as the query
- **StepBackRewriter** — LLM generalizes query for broader context
- **EntityExpansionRewriter** — Expand query with KG entity synonyms/relations

Total query variants capped at `MAX_QUERY_VARIANTS=5` to bound latency.

**Files**: `internal/rewrite/{rewriter,passthrough,multi_query,hyde,step_back,entity_expand}.go`

### Phase 11: Retrieval Engine
Hybrid search: for each query variant -> embed -> run vector (Qdrant/pgvector) + BM25 (Postgres tsvector or Elasticsearch, selected by `BM25_ENGINE`) + graph (Neo4j) in parallel via errgroup -> RRF merge (k=60, dense weight 0.7, sparse weight 0.3, configurable) -> over-retrieve 3x candidates -> ACL + freshness filters -> adjacent chunk expansion (±1 chunk_index) -> deduplicate across variants -> rerank with cross-encoder sidecar -> confidence threshold check. Graceful fallback if components fail.

**Files**: `internal/search/{engine,vector,bm25,bm25_postgres,bm25_elasticsearch,rrf,filters,adjacent,legacy}.go`, `internal/rerank/{reranker,crossencoder}.go`

### Phase 12: Generation Service
vLLM client with OpenAI-compatible streaming SSE parsing. Prompt templates (system + user with chunk context). RAG orchestrator: rewrite -> search -> rerank -> confidence check -> assemble context -> render prompt -> call LLM. Confidence threshold gate: if top reranked score < `MIN_RELEVANCE_THRESHOLD`, return advisory instead of generating. Date/freshness conflict detection: flag when sources span >1yr. Returns answer + source references + usage stats + timing breakdown + advisories. Streaming variant returns channel of events.

**Files**: `internal/llm/{provider,vllm,prompt}.go`, `internal/generation/{service,context,confidence,freshness}.go`

### Phase 13: Caching
ACL-aware query response cache with bypass rules for rewrite/KG queries. Cache invalidation on document ingest/delete. Embedding cache with tenant-prefixed keys.

**Files**: `internal/storage/redis/{cache,ratelimit}.go` (update cache.go with ACL-aware logic)

### Phase 14: REST API
Chi router with all route groups (legacy, v1, admin). Middleware stack (request ID, logging, recovery, CORS, auth, rate limiting, audit). All handlers wired. SSE streaming. Response helpers with typed error serialization.

**Files**: `internal/api/{router,middleware,handler_query,handler_retrieve,handler_document,handler_collection,handler_graph,handler_legacy,handler_tenant,request,response,sse}.go`

### Phase 15: Cross-Encoder Sidecar
Python sidecar for local cross-encoder reranking. FastAPI + sentence-transformers. Model: BAAI/bge-reranker-v2-m3. HTTP API with `/rerank` and `/health` endpoints.

**Files**: `cmd/crossencoder-sidecar/{main.py,requirements.txt,Dockerfile}`

### Phase 16: Observability
OTel SDK init with OTLP gRPC exporter. Prometheus counters/histograms (documents_ingested, query_duration, embedding_duration, chunks_stored, rewrite_duration, rerank_duration, graph_query_duration, kg_extraction_duration, faiss_query_duration, cache_hit_rate). slog with trace correlation. HTTP middleware for tracing. Grafana dashboard templates.

**Files**: `internal/observability/{otel,metrics,logging}.go`

### Phase 17: Testing
Unit tests (chunking, RRF, rewriters, loaders, graph extraction, merger, cache bypass logic, RLS). Integration tests with testcontainers-go (Postgres, Qdrant, Redis, MinIO, Neo4j, Elasticsearch when `BM25_ENGINE=elasticsearch`). Legacy compat tests against golden fixtures. API handler tests with httptest. Security tests (RLS leak, auth matrix). E2E: ingest -> query round-trip.

**Files**: `*_test.go`, `testdata/sample.{pdf,md,docx,html,txt}`, `testdata/legacy_fixtures/*.json`, `testdata/eval_baseline.json`

### Phase 18: CI/CD Pipeline
GitHub Actions workflows for continuous integration, release, and nightly evaluation. Helm chart for Kubernetes deployment.

**Files**: `.github/workflows/{ci,release,nightly}.yml`, `deploy/helm/ragstack/**`

### Phase 19: Migration Tooling (Future)
Tool to migrate legacy FAISS indices into Qdrant/pgvector collections, enabling retirement of Python sidecar.

---

## Maturity Levels — When to Stop

Not every deployment needs all 19 phases. This section maps progressive RAG maturity levels to implementation phases, so teams can stop at the complexity appropriate for their use case. Inspired by the "5 Levels of RAG" framework (Perrone, 2026).

### Level 1: Naive RAG (Phases 1–9)
**What you get**: Basic ingest + embed + vector retrieve pipeline. Single retrieval signal (dense vector search only). No rewriting, no reranking, no graph.

**Phases included**: Project scaffold (1), Config (2), Auth/Storage (3), Legacy FAISS (4), Document Processing (5), Embedding (6), Vector Store (7), Knowledge Graph store interface only (8), Ingestion Pipeline (9).

**Skip or stub**: Query rewriting (10), reranking in retrieval engine (11), generation service uses passthrough retrieval (12).

**Good enough for**:
- Internal knowledge bases with < 10K documents
- Prototypes and proofs of concept
- Personal note search / documentation lookup
- Homogeneous document corpora (single domain, consistent format)

**Key metric**: Hit rate @5 > 0.7 on your evaluation set. If you're above this, the added complexity of higher levels may not be justified.

### Level 2: Smart Chunking + Hybrid Search (Phases 1–11, partial)
**What you get**: Section-aware chunking (Phase 5 with `CHUNK_SECTION_AWARE=true`), hybrid dense + BM25 retrieval with RRF fusion, configurable chunk overlap. No reranking yet.

**Phases included**: Everything in Level 1, plus: Query rewriting with passthrough only (10), Retrieval Engine with RRF merge but reranker disabled (11, `RERANKER_MODE=none`).

**Additional config**:
- Enable BM25 via tsvector (default, already in schema migration 004) or Elasticsearch (`BM25_ENGINE=elasticsearch`)
- Set RRF weights: `RRF_DENSE_WEIGHT=0.7`, `RRF_SPARSE_WEIGHT=0.3`
- Chunk size tuned to corpus (start with 300 tokens, iterate with eval)

**Good enough for**:
- Internal wikis and documentation portals
- Technical knowledge bases with keyword-heavy queries
- Support ticket search / FAQ systems
- Corpora mixing structured and unstructured content

**Key metric**: nDCG@5 > 0.6 and keyword queries returning relevant results that pure vector search missed.

### Level 3: Reranking + Query Rewriting (Phases 1–13)
**What you get**: Cross-encoder reranking, HyDE / multi-query rewriting, over-retrieval with rerank-down, ACL-aware caching.

**Phases included**: Everything in Level 2, plus: Full query rewriting (10), full retrieval engine with reranking (11), generation service (12), caching (13).

**Additional components**:
- Cross-encoder sidecar (Phase 15 — deploy early if using Level 3)
- Over-retrieve 3x candidates, rerank to top_k
- Confidence threshold active (`MIN_RELEVANCE_THRESHOLD=0.25`)

**Good enough for**:
- Customer-facing search and Q&A products
- Multi-department enterprise search
- E-commerce product discovery with natural language queries
- Research assistants for domain-specific corpora

**Key metric**: nDCG@5 > 0.75 and measurable lift (> 0.05) from reranker. If reranker isn't lifting nDCG, you may not need it — stay at Level 2.

### Level 4: Knowledge Graph + Multi-Hop (Phases 1–16)
**What you get**: KG extraction, graph-augmented retrieval for multi-hop queries, entity expansion rewriting, full observability.

**Phases included**: Everything in Level 3, plus: Knowledge Graph extraction (8, fully activated), entity expansion rewriting (10), REST API (14), cross-encoder sidecar (15), observability (16).

**Additional components**:
- Neo4j deployed and `GRAPH_ENABLED=true`
- Entity expansion rewriter enabled
- Full OTel tracing across pipeline stages
- Adjacent chunk retrieval active

**Good enough for**:
- Research platforms requiring relational reasoning
- Biomedical / scientific literature search
- Legal document analysis with cross-reference tracking
- Enterprise systems where queries require connecting information across documents

**Key metric**: Multi-hop query success rate > 0.6 (queries requiring 2+ document connections). If your queries are single-hop, Level 3 is sufficient.

### Level 5: Production-Grade (All Phases)
**What you get**: Full system — testing, CI/CD, evaluation framework, migration tooling. Everything hardened for production.

**Phases included**: All phases 1–19, including: Testing (17), CI/CD (18), Migration tooling (19).

**Additional hardening**:
- Gold set evaluation with regression detection (nightly CI)
- Date/freshness conflict detection active
- Full graceful degradation tested
- RLS security test suite
- Contract tests against legacy API
- Helm chart for Kubernetes deployment

**Required for**:
- Customer-facing SaaS products with SLAs
- Regulatory/compliance environments (audit logging, data isolation)
- High-throughput deployments (> 10K queries/day)
- Systems where incorrect answers have material consequences (medical, legal, financial)

**Key metric**: All SLOs met (P99 < 2s, nDCG@5 > 0.75, generation quality > 4.0/5.0). Nightly eval suite passes without regression.

### Choosing Your Level

| Use Case | Recommended Level | Stop After Phase |
|----------|-------------------|------------------|
| Personal notes / dev docs search | Level 1 | Phase 9 |
| Internal wiki / team knowledge base | Level 2 | Phase 11 |
| Customer-facing Q&A / support bot | Level 3 | Phase 15 |
| Research platform / scientific search | Level 4 | Phase 16 |
| SaaS product with SLAs / compliance | Level 5 | Phase 19 |

**How to decide**: Run the evaluation framework (even a minimal gold set of 20-50 queries) at each level. If the next level's additions don't measurably improve your key metrics, you're at the right stopping point. Complexity has a maintenance cost — only add it when the data justifies it.

**Upgrading later**: The phased architecture is designed for incremental adoption. Each level builds on the previous one without requiring rework. A Level 2 deployment can upgrade to Level 3 by deploying the cross-encoder sidecar and enabling reranking — no data migration needed.

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

  security:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with: { go-version: '1.23' }
      - run: go test -tags=security ./internal/storage/postgres/...
      # RLS leak tests, auth matrix, tenant isolation verification

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

      # Build Apptainer .sif images from pushed Docker images
      - name: Build Apptainer images
        run: |
          apptainer build ragstack-api.sif docker://ghcr.io/${{ github.repository }}/api:${{ github.ref_name }}
          apptainer build ragstack-crossencoder.sif docker://ghcr.io/${{ github.repository }}/crossencoder:${{ github.ref_name }}
      - name: Upload .sif to release
        uses: softprops/action-gh-release@v1
        with:
          files: |
            ragstack-api.sif
            ragstack-crossencoder.sif

      # Deploy to Kubernetes (skip if DEPLOY_TARGET=apptainer)
      - if: env.DEPLOY_TARGET != 'apptainer'
        run: helm upgrade --install ragstack ./deploy/helm/ragstack
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

  # Optional: only needed when BM25_ENGINE=elasticsearch
  elasticsearch:
    image: elasticsearch:8.15.0
    environment:
      discovery.type: single-node
      xpack.security.enabled: "false"
      ES_JAVA_OPTS: "-Xms512m -Xmx512m"
    ports: ["9200:9200"]
    volumes: [es_data:/usr/share/elasticsearch/data]
    healthcheck:
      test: ["CMD-SHELL", "curl -f http://localhost:9200/_cluster/health || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 5
    profiles: ["elasticsearch"]

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
  es_data:
  mongo_data:
```

---

## Apptainer Deployment

For HPC and bare-metal production environments where only Apptainer is available. Uses the same Docker images as Docker Compose — Apptainer builds `.sif` files from them.

### Building .sif Images

```bash
#!/usr/bin/env bash
# deploy/apptainer/build-sif.sh — Build .sif images from Docker images
set -euo pipefail

SIF_DIR="${SIF_DIR:-./sif}"
REGISTRY="${REGISTRY:-ghcr.io/org/ragstack}"
TAG="${TAG:-latest}"

mkdir -p "$SIF_DIR"

# Infrastructure services (from Docker Hub)
apptainer build "$SIF_DIR/postgres.sif"       docker://postgres:16-alpine
apptainer build "$SIF_DIR/qdrant.sif"          docker://qdrant/qdrant:latest
apptainer build "$SIF_DIR/redis.sif"           docker://redis:7-alpine
apptainer build "$SIF_DIR/neo4j.sif"           docker://neo4j:5
apptainer build "$SIF_DIR/minio.sif"           docker://minio/minio:latest

# Application images (from project registry)
apptainer build "$SIF_DIR/ragstack-api.sif"    docker://${REGISTRY}/api:${TAG}
apptainer build "$SIF_DIR/ragstack-crossencoder.sif" docker://${REGISTRY}/crossencoder:${TAG}

# Optional
apptainer build "$SIF_DIR/mongodb.sif"         docker://mongo:7
apptainer build "$SIF_DIR/jaeger.sif"          docker://jaegertracing/all-in-one:latest
# Elasticsearch only if BM25_ENGINE=elasticsearch
# apptainer build "$SIF_DIR/elasticsearch.sif" docker://elasticsearch:8.15.0
```

### Single-Node Start Script

```bash
#!/usr/bin/env bash
# deploy/apptainer/start.sh — Start all RagStack services as Apptainer instances
set -euo pipefail

SIF_DIR="${SIF_DIR:-./sif}"
DATA_DIR="${DATA_DIR:-/data/ragstack}"
ENV_FILE="${ENV_FILE:-./ragstack.env}"

mkdir -p "$DATA_DIR"/{postgres,qdrant,redis,neo4j,minio}

# --- Infrastructure ---
apptainer instance start \
  --bind "$DATA_DIR/postgres":/var/lib/postgresql/data \
  --env POSTGRES_USER=ragstack \
  --env POSTGRES_PASSWORD=ragstack_dev \
  --env POSTGRES_DB=ragstack \
  --net --network-args "portmap=5432:5432/tcp" \
  "$SIF_DIR/postgres.sif" postgres

echo "Waiting for Postgres..."
until apptainer exec instance://postgres pg_isready -U ragstack 2>/dev/null; do sleep 1; done

apptainer instance start \
  --bind "$DATA_DIR/qdrant":/qdrant/storage \
  --net --network-args "portmap=6333:6333/tcp,portmap=6334:6334/tcp" \
  "$SIF_DIR/qdrant.sif" qdrant

apptainer instance start \
  --bind "$DATA_DIR/redis":/data \
  --net --network-args "portmap=6379:6379/tcp" \
  "$SIF_DIR/redis.sif" redis

# --- Optional services (uncomment as needed) ---
# apptainer instance start --bind "$DATA_DIR/neo4j":/data \
#   --env NEO4J_AUTH=neo4j/ragstack_dev \
#   "$SIF_DIR/neo4j.sif" neo4j

# --- GPU services ---
# apptainer instance start --nv \
#   --bind "$DATA_DIR/crossencoder-cache":/root/.cache \
#   --env MODEL_NAME=BAAI/bge-reranker-v2-m3 \
#   "$SIF_DIR/ragstack-crossencoder.sif" crossencoder

# --- Application ---
apptainer instance start \
  --env-file "$ENV_FILE" \
  "$SIF_DIR/ragstack-api.sif" api

apptainer instance start \
  --env-file "$ENV_FILE" \
  "$SIF_DIR/ragstack-api.sif" worker

echo "All instances started. Run 'apptainer instance list' to verify."
```

### Single-Node Stop Script

```bash
#!/usr/bin/env bash
# deploy/apptainer/stop.sh — Stop all RagStack Apptainer instances
set -euo pipefail

for inst in worker api crossencoder neo4j redis qdrant postgres; do
  apptainer instance stop "$inst" 2>/dev/null && echo "Stopped $inst" || true
done
```

### Systemd Unit Files (Production)

For long-running production deployments, use systemd for auto-restart, dependency ordering, and logging.

```ini
# deploy/apptainer/systemd/ragstack-postgres.service
[Unit]
Description=RagStack PostgreSQL (Apptainer)
After=network.target

[Service]
Type=simple
Environment=SIF_DIR=/opt/ragstack/sif
Environment=DATA_DIR=/data/ragstack
ExecStart=apptainer instance run \
  --bind ${DATA_DIR}/postgres:/var/lib/postgresql/data \
  --env POSTGRES_USER=ragstack \
  --env POSTGRES_PASSWORD=${POSTGRES_PASSWORD} \
  --env POSTGRES_DB=ragstack \
  ${SIF_DIR}/postgres.sif
Restart=on-failure
RestartSec=10s

[Install]
WantedBy=multi-user.target
```

```ini
# deploy/apptainer/systemd/ragstack-api.service
[Unit]
Description=RagStack API Server (Apptainer)
After=ragstack-postgres.service ragstack-redis.service ragstack-qdrant.service
Requires=ragstack-postgres.service ragstack-redis.service ragstack-qdrant.service

[Service]
Type=simple
Environment=SIF_DIR=/opt/ragstack/sif
EnvironmentFile=/etc/ragstack/ragstack.env
ExecStart=apptainer instance run \
  --env-file /etc/ragstack/ragstack.env \
  ${SIF_DIR}/ragstack-api.sif
Restart=on-failure
RestartSec=10s

[Install]
WantedBy=multi-user.target
```

Additional unit files follow the same pattern for qdrant, redis, neo4j, worker, and crossencoder. The crossencoder unit adds `--nv` when GPU is available.

### Networking

- **Single node:** All services bind to `localhost` on their standard ports (same as Docker Compose). No overlay network — host networking by default.
- **Multi-node:** Set explicit host addresses in the env file:

```env
# Multi-node Apptainer configuration
POSTGRES_HOST=db-node.local
QDRANT_HOST=vector-node.local
REDIS_HOST=cache-node.local
VLLM_URL=http://gpu-node.local:8000
CROSSENCODER_URL=http://gpu-node.local:50052
```

No code changes required — the application resolves hosts from env vars.

### GPU Services

```bash
# vLLM on GPU node
apptainer instance start --nv \
  --bind /data/model-cache:/root/.cache/huggingface \
  --env MODEL=meta-llama/Llama-Scout-17B \
  vllm.sif vllm

# Cross-encoder on GPU (optional — CPU is viable)
apptainer instance start --nv \
  --env MODEL_NAME=BAAI/bge-reranker-v2-m3 \
  --env DEVICE=cuda \
  ragstack-crossencoder.sif crossencoder
```

### Data Persistence

Apptainer uses bind mounts instead of Docker volumes. All persistent data lives in host directories:

| Service | Host path | Container path |
|---------|-----------|----------------|
| PostgreSQL | `/data/ragstack/postgres` | `/var/lib/postgresql/data` |
| Qdrant | `/data/ragstack/qdrant` | `/qdrant/storage` |
| Redis | `/data/ragstack/redis` | `/data` |
| Neo4j | `/data/ragstack/neo4j` | `/data` |
| MinIO | `/data/ragstack/minio` | `/data` |
| Model cache | `/data/ragstack/models` | `/root/.cache/huggingface` |

---

## Key Dependencies (go.mod)

```
github.com/go-chi/chi/v5                    # HTTP router
github.com/jackc/pgx/v5                     # PostgreSQL driver (+ pgvector support)
github.com/riverqueue/river                  # Job queue
github.com/redis/go-redis/v9                 # Redis client
github.com/qdrant/go-client                  # Qdrant vector store
github.com/neo4j/neo4j-go-driver/v5         # Neo4j graph database
github.com/elastic/go-elasticsearch/v8       # Elasticsearch (optional BM25 backend)
github.com/aws/aws-sdk-go-v2                # S3/MinIO object storage
go.mongodb.org/mongo-driver                  # MongoDB (legacy config)
// No external API provider dependencies — all model inference is self-hosted via vLLM + sidecars
github.com/coreos/go-oidc/v3                # OIDC token validation
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

| Decision | Choice | Rationale | See |
|----------|--------|-----------|-----|
| Language | Go (not Python) | Runtime perf, concurrency, single binary; Python for ML sidecars | Decision 1 |
| Vector store | Qdrant default, pgvector option | Hard tenant isolation, dedicated resources; pgvector for simplicity | Decision 2 |
| BM25 | Postgres tsvector (default) / Elasticsearch (option) | tsvector for simplicity; Elasticsearch for richer DSL/scaling | Decision 2b |
| Graph DB | Neo4j (primary) + Postgres triples (fallback) | Cypher for complex graph queries; Postgres when Neo4j unavailable | — |
| KG extraction model | Local LLM via vLLM (Llama-3.1-8B) | Shared infrastructure with generation LLM; swappable via interface | Decision 3 |
| KG predicate vocab | Open (not closed) | Avoids domain-specific curation; normalization via alias map | — |
| Job queue | River (not Celery) | Go-native, Postgres-backed, no Redis dependency for jobs | — |
| Legacy FAISS | Python sidecar (not CGO) | Lower risk, exact compat, isolates complexity; migrate to Qdrant later | — |
| Reranker | Cross-encoder (self-hosted only) | Local bge-reranker-v2-m3; no external API dependency | Decision 3 |
| Cross-encoder model | BAAI/bge-reranker-v2-m3 | Multilingual, strong benchmarks, CPU-viable at batch=40 | — |
| Query rewriting | Interface-driven, per-request config | Users choose strategy per query; extensible; 5 strategies | — |
| Streaming | SSE (not WebSocket) | Simpler, widely supported, sufficient for LLM token streaming | — |
| Multi-tenancy | Baked in from day 1 | Avoids costly retrofit | — |
| Auth | OIDC + API key | Enterprise SSO + programmatic access | — |
| Data isolation | RLS policies + tenant columns | Defense-in-depth; compiler can't catch missing WHERE clauses | — |
| In-cluster security | mTLS via service mesh | Zero-trust networking; transparent to application | — |
| Legacy compat | Exact rag_api contract at root | Existing clients work without changes | — |
| Deployment | Docker Compose (dev) + Apptainer or Helm (prod) | Apptainer for HPC/bare-metal; Helm for cloud/K8s; same container images | Decision 4 |
| Caching | ACL-aware, rewrite/KG bypass | Prevents stale results and cross-tenant leakage | — |

---

## Evaluation Framework

Evaluation is continuous, not a one-time activity. The framework covers retrieval quality, generation quality, end-to-end system performance, and regression detection.

### Evaluation Datasets

#### Gold Set (required, maintained in-repo)

A curated set of query-document-answer triples used for automated regression testing:

```
testdata/
├── eval_baseline.json          # Current baseline scores
├── eval_gold_set.json          # Gold set: queries + expected docs + reference answers
└── eval_domain_sets/           # Optional domain-specific sets
    ├── biomedical.json
    └── general.json
```

**Gold set format:**
```json
{
  "version": "1.0",
  "queries": [
    {
      "id": "q001",
      "query": "What are the side effects of metformin?",
      "collection": "pharma_docs",
      "relevant_chunk_ids": ["chunk_42", "chunk_87", "chunk_103"],
      "reference_answer": "Common side effects include nausea, diarrhea...",
      "metadata": {
        "difficulty": "easy",
        "domain": "biomedical",
        "requires_multi_hop": false
      }
    }
  ]
}
```

**Gold set requirements:**
- Minimum 50 queries covering easy/medium/hard difficulty
- At least 10 queries requiring multi-hop reasoning (tests KG value)
- At least 10 queries with known lexical matches (tests BM25 value)
- Updated when new document types or domains are added

#### MCQA Benchmark (from distllm)

Import existing MCQA evaluation from distllm for scientific domain assessment:
- SciQ, PubMedQA, LitQA benchmarks
- Chunk-level provenance tracking (which chunks were used to answer)
- Existing baseline scores from distllm for regression comparison

### Retrieval Metrics

Measured on the gold set, computed by the nightly eval pipeline:

| Metric | What it measures | Target | Computed how |
|--------|-----------------|--------|--------------|
| nDCG@10 | Ranking quality of top-10 results | > 0.65 | Normalized discounted cumulative gain; gold set provides relevance grades |
| Hit@5 | Did at least one relevant doc appear in top-5? | > 0.85 | Binary per query, averaged |
| MRR | Position of first relevant result | > 0.70 | 1/rank of first relevant result, averaged |
| Recall@20 | Fraction of all relevant docs found in top-20 | > 0.80 | Relevant found / total relevant |
| Precision@5 | Fraction of top-5 that are relevant | > 0.60 | Relevant in top-5 / 5 |

**Measured across configurations to quantify feature value:**

```
Config A: passthrough rewrite, vector+BM25, no reranker, no graph  (baseline)
Config B: passthrough rewrite, vector+BM25, reranker, no graph     (reranker value)
Config C: hyde rewrite, vector+BM25, reranker, no graph            (rewrite value)
Config D: passthrough rewrite, vector+BM25, reranker, graph        (graph value)
Config E: hyde rewrite, vector+BM25, reranker, graph               (full pipeline)
```

This A/B matrix quantifies the marginal value of each component. If a component doesn't improve metrics, it can be disabled to reduce latency and complexity.

### Generation Metrics

| Metric | What it measures | How | Target |
|--------|-----------------|-----|--------|
| Faithfulness | Does the answer only use information from retrieved context? | LLM-as-judge: "Does this answer contain claims not supported by the context?" | > 0.90 |
| Answer relevance | Does the answer address the question? | LLM-as-judge: "How well does this answer address the query?" (1-5 scale) | > 4.0 |
| Citation accuracy | Are source references correct? | Automated: verify cited chunk IDs contain supporting text | > 0.85 |
| Hallucination rate | Fraction of answers with unsupported claims | Inverse of faithfulness | < 0.10 |
| Answer completeness | Does the answer cover all relevant aspects? | LLM-as-judge against reference answer | > 0.75 |

**LLM-as-judge implementation:**
```go
// internal/evaluation/judge.go
type Judge struct {
    llm llm.Provider
}

func (j *Judge) ScoreFaithfulness(ctx context.Context, answer, context string) (float64, error) {
    prompt := fmt.Sprintf(`Given the context and answer below, score faithfulness from 0.0 to 1.0.
A score of 1.0 means every claim in the answer is supported by the context.
A score of 0.0 means the answer is entirely fabricated.

Context: %s
Answer: %s

Return ONLY a JSON object: {"score": 0.X, "reason": "..."}`, context, answer)
    // Call LLM, parse score
}
```

### End-to-End System Metrics

| Metric | Target | Measured by |
|--------|--------|-------------|
| Query P99 latency | < 2s | Prometheus histogram |
| Retrieval P99 latency | < 500ms | Prometheus histogram |
| Ingestion throughput | >= 50 docs/min | Prometheus counter + timer |
| Cache hit rate | > 30% (steady state) | Prometheus counter |
| Error rate | < 1% | Prometheus counter |
| FAISS parity | 100% response match | Contract tests |

### Regression Detection

The nightly eval pipeline (`/.github/workflows/nightly.yml`) compares current scores against `testdata/eval_baseline.json`:

```json
{
  "version": "1.0",
  "timestamp": "2026-03-01T03:00:00Z",
  "config": "full_pipeline",
  "retrieval": {
    "ndcg_at_10": 0.72,
    "hit_at_5": 0.88,
    "mrr": 0.75,
    "recall_at_20": 0.85,
    "precision_at_5": 0.64
  },
  "generation": {
    "faithfulness": 0.93,
    "answer_relevance": 4.2,
    "citation_accuracy": 0.87,
    "hallucination_rate": 0.07
  },
  "latency": {
    "query_p99_ms": 1850,
    "retrieval_p99_ms": 420
  }
}
```

**Regression thresholds:**

| Metric | Allowed drop | Action on breach |
|--------|-------------|------------------|
| nDCG@10 | -0.03 | Fail nightly, alert team |
| Hit@5 | -0.05 | Fail nightly, alert team |
| Faithfulness | -0.05 | Fail nightly, block release |
| Hallucination rate | +0.05 | Fail nightly, block release |
| Query P99 | +200ms | Warning; block if > 2.5s |

**Baseline update process:**
1. Run eval suite on current main
2. If scores improved, update `eval_baseline.json` and commit
3. PR review required for baseline changes (prevents sneaky regressions)

### Component-Level Evaluation

#### Rewrite Strategy Evaluation

Each rewrite strategy is evaluated independently to measure its contribution:

| Strategy | What to measure | Good indicator |
|----------|----------------|----------------|
| Passthrough | Baseline recall | — |
| HyDE | Recall improvement over passthrough | > 5% nDCG@10 lift |
| Multi-query | Recall improvement; check for result diversity | > 3% recall@20 lift |
| Step-back | Improvement on broad/conceptual queries | Better hit@5 on "hard" queries |
| Entity expansion | Improvement on entity-rich queries | Better hit@5 on KG-testable queries |

Log rewrite hit rate per strategy in production metrics. If a strategy consistently fails to improve results, it should be disabled to save latency.

#### KG Value Assessment

Measure the marginal value of the knowledge graph:

| Test | Method | Expected outcome |
|------|--------|------------------|
| Multi-hop queries | Gold set subset requiring 2+ hops | Hit@5 > 0.60 with graph; < 0.30 without |
| Entity disambiguation | Queries with ambiguous entity names | Precision improvement with graph context |
| Sparse document coverage | Queries about rare topics with few chunks | Graph provides additional context paths |

If KG does not measurably improve retrieval on any test category, the operational cost of Neo4j may not be justified. The evaluation framework provides data to make this decision.

#### Reranker Value Assessment

| Test | Method | Expected outcome |
|------|--------|------------------|
| nDCG lift | Compare nDCG@5 with and without reranker | > 0.05 improvement |
| Latency cost | Measure P99 increase from reranking | < 300ms |
| Cross-encoder vs no reranker | Measure nDCG lift from reranking | > 0.05 improvement justifies sidecar |

### Evaluation Pipeline Implementation

```go
// internal/evaluation/suite.go (build tag: eval)
type EvalSuite struct {
    goldSet   GoldSet
    baseline  Baseline
    pipeline  *generation.Service
    judge     *Judge
}

func (s *EvalSuite) Run(ctx context.Context) (*EvalReport, error) {
    // 1. Load gold set
    // 2. Run each query through pipeline with multiple configs
    // 3. Compute retrieval metrics (nDCG, hit, MRR, recall, precision)
    // 4. Compute generation metrics via LLM-as-judge
    // 5. Compare against baseline
    // 6. Return report with pass/fail per metric
}
```

Build tag ensures evaluation code is not compiled into production binary:
```go
//go:build eval
```

Run manually:
```bash
go test -tags=eval -timeout=30m -v ./internal/evaluation/...
```

---

## Risks and Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Latency creep from rewrite + KG | P99 > 2s SLO breach | Medium | Cap query variants at 5; KG timeout at 150ms; fallback to hybrid-only |
| FAISS/embedding dimension mismatch | Silent wrong results | Low | Header check on index load; fail fast; health endpoint surfaces mismatches |
| GPU cost for self-hosted models | Budget overrun | Medium | Start with smaller models (7-8B) on A10G; scale to A100 + Ray for larger models only when quality demands it. Monitor GPU utilization; right-size instances. |
| Data drift (stale embeddings) | Degraded retrieval quality | Medium | Document checksum + versioning; cache invalidation on new versions |
| Cross-tenant data leakage | Security breach | Low | RLS policies + Qdrant collection isolation + integration tests for tenant isolation |
| Neo4j operational complexity | Increased ops burden | Medium | Neo4j is optional (`GRAPH_ENABLED=false`); Postgres triples as fallback |
| Python sidecar reliability | Legacy endpoint outages | Low | Health checks, auto-restart, graceful degradation (v1 endpoints unaffected) |
| vLLM/Ray infrastructure failure | Query endpoint down | Low | `/v1/retrieve` still works (no LLM needed); cached responses served; Ray auto-restarts failed workers; alert on error rate |
| KG extraction quality | Low-quality triples degrade retrieval | Medium | Confidence scoring; threshold filter (default 0.5); monitor entity coverage |
| RLS misconfiguration | Data leakage | Low | Dedicated security test suite in CI; tenant isolation integration tests |

---

## Observability

### Traces
OTel traces across rewrite -> retrieve -> rerank -> generate. Each span includes:
- Index used (qdrant, pgvector, faiss)
- Rewrite strategies applied
- Number of KG triples retrieved
- Reranker type and input/output sizes
- Token counts (prompt + completion)

### Metrics (Prometheus)

| Metric | Type | Labels |
|--------|------|--------|
| `ragstack_query_duration_seconds` | Histogram | `stage={rewrite,retrieve,rerank,generate,total}` |
| `ragstack_documents_ingested_total` | Counter | `status={success,failure}` |
| `ragstack_chunks_stored_total` | Counter | — |
| `ragstack_embedding_duration_seconds` | Histogram | `provider={local}` |
| `ragstack_rerank_duration_seconds` | Histogram | `type={crossencoder}` |
| `ragstack_graph_query_duration_seconds` | Histogram | — |
| `ragstack_kg_extraction_duration_seconds` | Histogram | — |
| `ragstack_faiss_query_duration_seconds` | Histogram | `index_type={distllm,tfidf}` |
| `ragstack_cache_operations_total` | Counter | `result={hit,miss,bypass}` |
| `ragstack_rewrite_hit_rate` | Gauge | `strategy={hyde,multi_query,step_back,entity_expand}` |
| `ragstack_auth_requests_total` | Counter | `method={oidc,apikey}, result={success,failure}` |

### Alerts

| Alert | Condition | Severity |
|-------|-----------|----------|
| QueryLatencyHigh | P99 > 2s for 5min | Critical |
| IngestionBacklog | River queue depth > 1000 for 10min | Warning |
| SidecarUnhealthy | FAISS or cross-encoder health check failing | Warning |
| CacheHitRateLow | Cache hit rate < 10% for 1hr | Info |
| TenantIsolationFailure | Security test suite failure | Critical |

---

## Verification Checklist (per release)

1. `make docker-up` — start all services (Postgres, Qdrant, Redis, MinIO, Neo4j, MongoDB, Jaeger)
2. `make migrate-up` — run SQL migrations (001-010)
3. `make build` — compile api + worker binaries
4. Create tenant + API key via admin endpoint
5. Verify audit log entry for tenant creation
6. Create a collection, upload a sample document
7. Poll document status until "ready"
8. Verify KG triples extracted: `GET /v1/graph/entities`
9. `POST /v1/query` with `rewrite_strategies: ["passthrough"]` — basic RAG, verify P99 < 2s
10. `POST /v1/query` with `rewrite_strategies: ["hyde"]` — HyDE rewriting
11. `POST /v1/query` with `rewrite_strategies: ["entity_expand"]` — entity expansion
12. `POST /v1/query` with `use_graph: true` — graph-augmented retrieval
13. `POST /v1/query/stream` — verify SSE streaming
14. `POST /v1/retrieve` — retrieve-only (no generation), verify P99 < 500ms
15. Start FAISS sidecar with sample indices
16. `GET /health` — legacy health check (status, mongodb_connected, embedding_service_available)
17. `GET /databases` — legacy database listing from MongoDB ragList
18. `POST /query/{db}` — legacy query, verify merged results match existing rag_api output
19. Verify graceful degradation: stop Neo4j -> queries still work without graph; stop Qdrant -> BM25 fallback
20. Verify cache bypass: query with `rewrite_strategies: ["hyde"]` does not serve cached response
21. Verify RLS: query with tenant A's key cannot access tenant B's data
22. Verify OIDC auth: valid token resolves tenant; expired token returns 401
23. `make test` — all unit + integration tests pass
24. `make lint` — golangci-lint passes
25. Run contract tests against legacy golden fixtures
26. Run security test suite (RLS leak, auth matrix, tenant isolation)
27. Check Jaeger for end-to-end traces across rewrite -> retrieve -> rerank -> generate
28. Check Prometheus `/metrics` for all registered counters/histograms
29. Verify Grafana dashboards render with live data
