# RAGStack — Production-Grade Full-Stack RAG System Specification

## 1. Goals

Build a **production-ready Retrieval-Augmented Generation (RAG)** platform that combines:

| Capability | Purpose |
|---|---|
| Vector database | Dense semantic similarity search |
| Text indexing (BM25/full-text) | Sparse keyword retrieval |
| Metadata search | Structured filtering by document attributes |
| Knowledge graphs | Entity relationships and multi-hop reasoning |
| Query rewriting | Improve recall by reformulating user queries |
| Result scoring / reranking | Surface the most relevant context to the LLM |
| REST API | Production-ready HTTP interface for consumers |

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        API Layer                            │
│            FastAPI  ·  Auth  ·  Rate-limit  ·  CORS        │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│                     Pipeline Orchestrator                    │
│  query_rewriter → retriever → scorer → context_builder      │
└──┬──────────────┬──────────────────────────┬────────────────┘
   │              │                          │
   ▼              ▼                          ▼
Vector DB    Text Index              Knowledge Graph
(Qdrant /    (Elasticsearch /        (Neo4j /
 pgvector)    OpenSearch / BM25)      NetworkX)
   │              │
   └──────┬───────┘
          ▼
    Metadata Store
    (PostgreSQL / metadata filters)
          │
          ▼
    Reranker / Scorer
    (cross-encoder / Cohere rerank)
          │
          ▼
     LLM (OpenAI / local)
```

---

## 3. Components

### 3.1 Ingestion Pipeline

Responsibilities:
- Load documents from files, URLs, databases, or blob stores.
- Chunk documents into passages with configurable overlap.
- Generate dense embeddings (OpenAI `text-embedding-3-small`, BGE, etc.).
- Extract entities and build knowledge-graph triples.
- Index passages into: vector store, text index, and metadata store simultaneously.

Key interfaces:
```python
class DocumentLoader(Protocol):
    def load(self, source: str) -> list[Document]: ...

class Chunker(Protocol):
    def chunk(self, doc: Document) -> list[Chunk]: ...

class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...

class KGExtractor(Protocol):
    async def extract(self, chunks: list[Chunk]) -> list[Triple]: ...
```

### 3.2 Vector Store

- **Primary**: Qdrant (self-hosted Docker or Qdrant Cloud).
- **Fallback / local dev**: pgvector (PostgreSQL extension).
- Collections are namespaced per tenant/project.
- Payload (metadata) is stored alongside vectors for hybrid queries.

### 3.3 Text Indexing

- **Primary**: Elasticsearch 8.x / OpenSearch.
- Supports BM25 keyword search with field boosting.
- Synced with the vector store via the ingestion pipeline (same chunk IDs).

### 3.4 Metadata Search

- Structured filtering on fields such as `source`, `author`, `date`, `tags`, `language`, `doc_type`.
- Implemented as payload filters in Qdrant **and** as Elasticsearch query filters.
- Metadata schema is validated with Pydantic models on ingestion.

### 3.5 Knowledge Graph

- **Primary**: Neo4j (self-hosted or AuraDB).
- **Fallback / lightweight**: NetworkX (in-memory, for testing/dev).
- Entity extraction via spaCy NER + LLM-based relation extraction.
- Graph queries complement vector search for multi-hop reasoning (e.g., "Who funded the company that acquired X?").

Key operations:
- `graph.add_triples(triples: list[Triple])`
- `graph.query_neighborhood(entity: str, depth: int) -> list[Triple]`
- `graph.find_paths(start: str, end: str) -> list[Path]`

### 3.6 Query Rewriting

Strategies applied in order, configurable per request:

| Strategy | Description |
|---|---|
| `HyDERewriter` | Generate a hypothetical answer, use it as the query embedding |
| `MultiQueryRewriter` | Expand original query into N paraphrases, retrieve for each |
| `StepBackRewriter` | Generalize the query to retrieve broader context |
| `EntityExpander` | Replace abbreviations/pronouns with full entity names from KG |

All rewriters share:
```python
class QueryRewriter(Protocol):
    async def rewrite(self, query: str, context: RewriteContext) -> list[str]: ...
```

### 3.7 Scorer / Reranker

Two-stage scoring:

1. **Retrieval score** — cosine similarity (vector) + BM25 score (text), combined by Reciprocal Rank Fusion (RRF).
2. **Reranking score** — cross-encoder model (e.g., `cross-encoder/ms-marco-MiniLM-L-6-v2`) or Cohere Rerank API.

```python
class Scorer(Protocol):
    async def score(
        self, query: str, candidates: list[Chunk]
    ) -> list[ScoredChunk]: ...
```

### 3.8 Context Builder

- Selects top-K scored chunks (configurable, default 5).
- Deduplicates overlapping passages.
- Assembles a prompt with system instructions, retrieved context, and the user query.
- Tracks token budget to avoid exceeding the LLM's context window.

### 3.9 REST API

Built with **FastAPI** + **Uvicorn**.

#### Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/ingest` | Ingest one or more documents |
| `GET` | `/v1/ingest/{job_id}` | Poll ingestion job status |
| `POST` | `/v1/query` | RAG query (retrieve + generate) |
| `POST` | `/v1/retrieve` | Retrieve only (no generation) |
| `GET` | `/v1/documents` | List indexed documents |
| `DELETE` | `/v1/documents/{doc_id}` | Delete a document and its chunks |
| `GET` | `/v1/graph/entities` | List KG entities |
| `GET` | `/v1/graph/neighbors/{entity}` | Get entity neighbors |
| `GET` | `/v1/health` | Health check |

#### Authentication
- API-key header (`X-API-Key`) validated against a secrets store.
- Optional JWT bearer token for user-scoped requests.

#### Request / Response models (examples)

```python
class QueryRequest(BaseModel):
    query: str
    top_k: int = 5
    rewrite_strategies: list[str] = ["hyde"]
    filters: dict[str, Any] = {}
    use_graph: bool = True
    stream: bool = False

class QueryResponse(BaseModel):
    answer: str
    sources: list[Source]
    rewritten_queries: list[str]
    scores: list[float]
```

---

## 4. Data Models

```python
class Document(BaseModel):
    id: str
    content: str
    metadata: dict[str, Any]
    source: str

class Chunk(BaseModel):
    id: str
    doc_id: str
    content: str
    embedding: list[float] | None
    metadata: dict[str, Any]
    start_char: int
    end_char: int

class Triple(BaseModel):
    subject: str
    predicate: str
    object: str
    doc_id: str

class ScoredChunk(BaseModel):
    chunk: Chunk
    score: float
    retrieval_method: str   # "vector" | "bm25" | "graph" | "hybrid"
```

---

## 5. Infrastructure & Deployment

### Services (Docker Compose / Kubernetes)

| Service | Image | Purpose |
|---|---|---|
| `api` | Python 3.12 + FastAPI | Application server |
| `qdrant` | `qdrant/qdrant:latest` | Vector store |
| `elasticsearch` | `elasticsearch:8` | Text search |
| `neo4j` | `neo4j:5` | Knowledge graph |
| `postgres` | `postgres:16` | Metadata / job queue |
| `redis` | `redis:7` | Cache / rate limiting |
| `worker` | Python 3.12 + Celery | Async ingestion jobs |

### Environment Variables

```
OPENAI_API_KEY=
QDRANT_URL=http://qdrant:6333
QDRANT_API_KEY=
ELASTICSEARCH_URL=http://elasticsearch:9200
NEO4J_URI=bolt://neo4j:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=
POSTGRES_DSN=postgresql+asyncpg://user:pass@postgres/ragstack
REDIS_URL=redis://redis:6379
API_KEYS=key1,key2
EMBEDDING_MODEL=text-embedding-3-small
RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
LOG_LEVEL=INFO
```

### Observability

- **Structured logging**: JSON logs via `structlog`.
- **Metrics**: Prometheus metrics exposed at `/metrics`.
- **Tracing**: OpenTelemetry spans for each pipeline stage.
- **Dashboards**: Grafana for latency, throughput, error rate per endpoint.

---

## 6. Technology Stack

| Layer | Choice | Rationale |
|---|---|---|
| Language | Python 3.12 | Ecosystem maturity for ML/NLP |
| API framework | FastAPI | Async, auto-docs, Pydantic |
| Vector store | Qdrant | Native hybrid search, easy Docker |
| Text search | Elasticsearch 8 | Industry standard, rich query DSL |
| Graph DB | Neo4j 5 | Mature Cypher query language |
| ORM / SQL | SQLAlchemy 2 (async) | Async support, typed |
| Embeddings | OpenAI / sentence-transformers | Flexible, swappable |
| Reranker | cross-encoder (HuggingFace) | Local, no extra API cost |
| Task queue | Celery + Redis | Reliable async ingestion |
| Containerization | Docker Compose → Helm | Dev → prod parity |
| Testing | pytest + pytest-asyncio | Standard Python |
| Linting | ruff + mypy | Fast, strict typing |

---

## 7. Testing Strategy

| Layer | Tool | Coverage target |
|---|---|---|
| Unit | pytest | All pipeline components isolated with mocks |
| Integration | pytest + test containers | Each store (Qdrant, ES, Neo4j) via Docker |
| API | pytest + httpx async client | All endpoints, auth, error cases |
| E2E | pytest | Full ingest → query round trip |
| Load | Locust | Query throughput ≥ 100 RPS at p99 < 500 ms |

---

## 8. Milestones

| Milestone | Deliverable |
|---|---|
| M1 — Foundation | Project scaffold, Docker Compose, CI pipeline, health endpoint |
| M2 — Ingestion | Document loader, chunker, embedder, vector + text index |
| M3 — Retrieval | Hybrid retriever (vector + BM25), metadata filters |
| M4 — Graph | KG extractor, Neo4j integration, graph-augmented retrieval |
| M5 — Intelligence | Query rewriting (HyDE, multi-query), cross-encoder reranking |
| M6 — API & Auth | Full REST API, API-key auth, rate limiting, streaming |
| M7 — Observability | Prometheus metrics, OTel tracing, Grafana dashboards |
| M8 — Production | Helm chart, horizontal scaling, load testing, runbook |
