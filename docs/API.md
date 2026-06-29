# RAGStack API Reference

HTTP API for the RAGStack Retrieval-Augmented Generation platform. The surface is
defined contract-first in [`contracts/openapi.yaml`](../contracts/openapi.yaml)
(OpenAPI 3.1) with JSON Schemas under `contracts/schemas/`; that contract is
authoritative and both implementations conform to it.

- **Python** (FastAPI) — default port **8000**
- **Go** (Chi) — default port **8080**

All examples below use `http://localhost:8000`. Interactive docs are served by the
Python app at `/docs` (Swagger UI) and `/redoc`.

---

## Authentication & tenancy

Auth is an API key passed in the **`X-API-Key`** header. The key maps to a
**tenant** server-side (via `API_KEY_TENANTS`); the tenant is **never** taken from
the request body, so a client cannot widen its own scope.

- **Reads** (`/v1/query`, `/v1/retrieve`, `/v1/documents`, graph) return the
  caller's own tenant **plus** the shared world-readable **`public`** tenant.
- **Writes/deletes** (`/v1/ingest`, `DELETE /v1/documents/...`) affect only the
  caller's own tenant.
- A key absent from the map resolves to the `default` tenant. If no API keys are
  configured at all (dev mode), requests are unauthenticated and use `default`.
- A request with an unknown key returns **401**.

```bash
curl -s http://localhost:8000/v1/query \
  -H 'X-API-Key: <your-key>' -H 'Content-Type: application/json' \
  -d '{"query": "..."}'
```

`/health` is open (no key required). All `/v1/*` routes require the header when
keys are configured.

---

## Endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/health` | Liveness check |
| POST | `/v1/query` | Full RAG: rewrite → retrieve → rerank → generate |
| POST | `/v1/retrieve` | Retrieve chunks only (no answer) |
| POST | `/v1/ingest` | Ingest a file/directory (async job) |
| GET | `/v1/ingest/{job_id}` | Poll ingest job status |
| GET | `/v1/documents` | List indexed documents |
| DELETE | `/v1/documents/{doc_id}` | Delete a document + its chunks |
| GET | `/v1/graph/entities` | List knowledge-graph entities |
| GET | `/v1/graph/neighbors/{entity}` | Entity neighborhood triples |

### GET /health

```bash
curl -s http://localhost:8000/health
# {"status": "ok"}
```

### POST /v1/query

Full pipeline: optionally expand the query (rewrite strategies), hybrid-retrieve
per variant, RRF-fuse, optionally cross-encoder rerank, then generate a grounded
answer. When no LLM is configured the `answer` is a retrieval-only placeholder
(sources are still returned). Generation/rewrite/rerank failures **degrade
gracefully** (HTTP 200 with sources) rather than erroring.

**Request** (`QueryRequest`)

| Field | Type | Default | Notes |
|---|---|---|---|
| `query` | string | — | **required** |
| `top_k` | int | 5 | results to return |
| `rewrite_strategies` | string[] | `["passthrough"]` | also `multiquery`, `hyde` (LLM-backed; ignored if no LLM) |
| `filters` | object | `{}` | metadata equality filters (ANDed); see [Metadata & filtering](#metadata--filtering) |
| `use_graph` | bool | true | include the knowledge-graph retrieval leg |
| `stream` | bool | false | reserved |

**Response** (`QueryResponse`): `{ answer, sources[], rewritten_queries[] }`

```bash
curl -s http://localhost:8000/v1/query \
  -H 'X-API-Key: kp' -H 'Content-Type: application/json' \
  -d '{"query": "how do viruses evade innate immunity?", "top_k": 5}'
```

### POST /v1/retrieve

Same retrieval (hybrid + optional rerank) but no answer generation.

**Request** (`RetrieveRequest`): `query` (required), `top_k` (5), `filters` (`{}`),
`use_graph` (true). **Response** (`RetrieveResponse`): `{ sources[] }`.

```bash
curl -s http://localhost:8000/v1/retrieve \
  -H 'X-API-Key: kp' -H 'Content-Type: application/json' \
  -d '{"query": "mechanisms of antibiotic resistance", "top_k": 10,
       "filters": {"doc_type": "article"}}'
```

### POST /v1/ingest

Accepts a file or directory `source` (resolved within `INGEST_ROOT`) and processes
it in the **background**, returning immediately with a `job_id`. A directory is
ingested recursively (`.pdf`/`.txt`/`.md`/`.jsonl`), one document per item.
Re-ingesting the same source **replaces** that document's chunks (deterministic
document id) rather than duplicating; a re-ingest that yields no embeddable chunks
fails the job and leaves the prior version intact.

> For multi-hundred-MB JSONL corpus dumps, use the operator tool
> `python/scripts/ingest_jsonl.py` instead — it streams, fans out across embedding
> endpoints, and bypasses the per-file size guard. See [Bulk ingestion](#bulk-ingestion).

**Request** (`IngestRequest`): `source` (required), `metadata` (`{}`).
**Response** (`IngestResponse`): `{ job_id, status, chunk_ids[], items? }`.

```bash
curl -s http://localhost:8000/v1/ingest \
  -H 'X-API-Key: kp' -H 'Content-Type: application/json' \
  -d '{"source": "papers/2024_review.pdf"}'
# {"job_id": "...", "status": "accepted"}
```

### GET /v1/ingest/{job_id}

Polls status: `accepted` → `running` → `completed` | `failed` (unknown id →
`unknown`, HTTP 200). Batch/directory jobs include `items`:
`{ total, completed, failed, pending }`.

```bash
curl -s http://localhost:8000/v1/ingest/<job_id> -H 'X-API-Key: kp'
```

### GET /v1/documents · DELETE /v1/documents/{doc_id}

List documents for the caller's readable tenants (`DocumentInfo[]`:
`{ doc_id, source, metadata }`), or delete one document and all its chunks
(scoped to the caller's tenant; **204** on success).

### GET /v1/graph/entities · GET /v1/graph/neighbors/{entity}

List KG entities (`EntityInfo[]`: `{ name, triple_count }`) or fetch an entity's
neighborhood triples (`TripleResponse[]`: `{ subject, predicate, object }`,
optional `?depth=` query param, default 1). Requires the graph backend (Neo4j) to
be configured; otherwise these return empty.

---

## Data models

**Source** (returned by query/retrieve):

| Field | Type | Notes |
|---|---|---|
| `doc_id` | string | parent document id |
| `chunk_id` | string | chunk id |
| `content` | string | chunk text |
| `score` | number | RRF fused score, or the cross-encoder score when reranking is on |
| `metadata` | object | arbitrary per-chunk metadata (see below) |

---

## Retrieval pipeline

A `/v1/query` (or `/v1/retrieve`) request flows through:

1. **Query expansion** — for each requested rewrite strategy (`passthrough`,
   `multiquery`, `hyde`); LLM-backed strategies are skipped if no LLM is wired.
2. **Hybrid retrieval per variant** — dense vector search (Qdrant) **+** BM25
   (Elasticsearch) **+** optional knowledge-graph leg, each tenant-scoped.
3. **RRF fusion** — Reciprocal Rank Fusion combines the ranked lists.
4. **Cross-encoder rerank** *(optional, server-config)* — when enabled, the fused
   top-`rerank_candidates` pool is rescored by the crossencoder sidecar and cut to
   `top_k`. A rerank failure falls back to the fused order. With rerank off,
   ordering and depth are unchanged.
5. **Answer generation** *(`/v1/query` only)* — an LLM grounds an answer on the
   sources; absent/failed LLM yields a retrieval-only placeholder.

The embedding model used at **query** time must match the model the corpus was
**ingested** with (vectors are model-specific); the Qdrant collection is named
`f(model, dim)` to keep models physically isolated.

---

## Metadata & filtering

`filters` is an object of equality constraints ANDed together and applied to chunk
metadata (Qdrant payload / ES keyword fields), on top of the automatic tenant
scoping. Example: `{"doc_type": "article", "year": 2021}`.

Metadata carried on each chunk depends on the loader. The bulk scholarly-corpus
loader (`ingest_jsonl.py`) stamps:

| Key | Example | Notes |
|---|---|---|
| `doc_type` | `article` | `article` / `supplement` / `front-matter` / `short` |
| `doi` | `10.1128/jvi.02415-06` | recovered from filename/text when absent |
| `doi_source` | `filename` | `metadata` / `filename` / `text` |
| `title` | `...` | when present in source metadata |
| `authors` | `["A. Smith", ...]` | list |
| `year` | `2021` | best-effort |
| `n_citations` | `42` | count (full citation list is kept in the doc-level catalog, not per chunk) |
| `tenant_id` | `public` | set server-side |

---

## Configuration (server)

Key environment variables (see `python/ragstack/config.py` for the full set):

| Var | Purpose |
|---|---|
| `API_KEYS`, `API_KEY_TENANTS` | auth keys and key→tenant map (JSON) |
| `EMBEDDING_API` | `sidecar` \| `openai` |
| `EMBEDDING_SIDECAR_URL` / `EMBEDDING_ENDPOINTS` | embedding endpoint(s); multiple → load-balanced pool |
| `EMBEDDING_MODEL`, `EMBEDDING_MODEL_DIM` | must match the ingested corpus |
| `VECTOR_BACKEND` | `qdrant` \| `memory` |
| `TEXT_BACKEND`, `ELASTICSEARCH_INDEX` | `elasticsearch` \| `memory` for BM25 |
| `RERANK_ENABLED`, `RERANK_CANDIDATES`, `CROSSENCODER_SIDECAR_URL` | cross-encoder rerank stage |
| `LLM_ENDPOINT`, `LLM_MODEL` | OpenAI-compatible chat endpoint for generation (empty → retrieval-only) |
| `INGEST_ROOT`, `MAX_DOCUMENT_BYTES` | ingest path confinement + size guard |
| `REQUIRE_DURABLE_BACKENDS` | production marker — fail fast on missing/unreachable durable backend instead of degrading to in-memory |
| `TENANT_MAX_CONCURRENCY` | per-tenant admission cap on the shared embedding fleet |

---

## Bulk ingestion

For large pre-extracted JSONL corpora (`{text, path, metadata}` per line), the
operator tool streams, enriches scholarly metadata, fans out embedding across
endpoints, and is resumable:

```bash
python scripts/ingest_jsonl.py corpus.jsonl --tenant public \
  --embedding-api openai \
  --embedding-url http://gpu0:9001 http://gpu1:9002 \
  --embedding-model <model> \
  --text-backend elasticsearch --es-index <idx> \
  --concurrency 16 --catalog-out corpus.catalog.jsonl
```

`--catalog-out` writes the full per-document metadata catalog (including the
extracted citation list); `--no-index` produces the catalog without embedding.

---

## Errors

| Status | When |
|---|---|
| `200` | success — **including** graceful degradation (LLM/rewrite/rerank failure returns sources with a note) |
| `204` | document deleted |
| `401` | unknown/invalid API key |
| `422` | request body fails validation |

Error responses never leak filesystem paths or upstream exception text.
