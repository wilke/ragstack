# RAG Plan G3 — Reconciled with KG & Query Rewriting (FAISS + rag_api compatibility)

## Purpose
Blend the self-hosted, lean plan (plan-g1) and the integration plan (plan-g2) with the branch spec that adds knowledge-graph and query-rewriting strengths, while keeping backward compatibility with existing FAISS indices and the rag_api HTTP surface.

## Summary of Key Decisions
- Keep self-hosted stack; primary metadata store is Postgres. Hybrid retrieval stays (dense + BM25).
- Add **Knowledge Graph** (Neo4j primary; NetworkX fallback) built during ingestion; expose graph-assisted retrieval paths.
- Add **Query Rewriting** strategies (HyDE, multi-query, step-back, entity expansion) as configurable pre-retrieval stage.
- Maintain **FAISS legacy indices** (/data/*.index) via read-only registry; fuse with new stores.
- Mirror **rag_api endpoints/behavior** via compatibility layer; also expose extended graph/rewrite toggles where safe.
- Latency target remains 1–2s P99; reranker optional but recommended.

## Architecture (Reconciled)
- **API Layer (FastAPI)**: exposes rag_api-compatible endpoints plus graph and rewrite controls; auth via OIDC/API key; rate limiting; streaming optional.
- **Pipeline Orchestrator**: query_rewriter -> retriever -> scorer/reranker -> context_builder -> LLM. Feature flags to enable/disable rewrite/KG.
- **Stores**:
  - Vector: Postgres+pgvector (primary for <5M) with IVF/HNSW; optional Qdrant plug-in if scale/latency demands; legacy FAISS indices via registry (read-only) with score fusion.
  - Text/BM25: Postgres pg_trgm; optional Elasticsearch/OpenSearch if branch stack is already provisioned.
  - Metadata: Postgres with RLS/ACL.
  - Knowledge Graph: Neo4j (prod) / NetworkX (dev) fed from ingestion entity/relation extraction.
  - Objects: MinIO/S3-compatible for raw docs.
- **Models**:
  - Embeddings: self-hosted BGE/Nomic (GPU) aligned to FAISS dim; detect mismatch on load.
  - Reranker: cross-encoder (bge-reranker-large or equivalent).
  - LLM: self-hosted Llama/Mistral via vLLM.
- **Compatibility**:
  - rag_api: match endpoints, status codes, schemas; generate OpenAPI for contract tests.
  - FAISS: registry mounts /data/*.index, tags (`legacy_a`, `legacy_b`), merges scores via RRF with ACL-aware metadata bridge.

## Ingestion Updates
- Pipelines write chunk + embedding to vector store, text index, and metadata; parallel KG extraction (NER + relation extraction) storing triples in Neo4j.
- Chunking unchanged (200–400 tokens, semantic boundaries, hashes); add entity annotations for KG.
- Event bus (Kafka/Redpanda) carries doc.ingested, doc.chunked, doc.embedded, graph.built, index.updated.

## Query Flow
1) **Rewrite (configurable)**: apply HyDE, multi-query, step-back, entity expansion (uses KG) -> set of queries.
2) **Retrieve**: per query, hit FAISS registry (legacy), pgvector (or Qdrant), and BM25; apply filters (ACL, freshness); fuse results (RRF) to top-K.
3) **Rerank**: cross-encoder from top-K to top-N (e.g., 40 -> 5).
4) **Context build**: dedupe/trim to token budget; assemble prompt.
5) **Generate**: LLM inference; return answer + citations + trace IDs.

## APIs
- **rag_api parity**: keep existing paths/verbs/payloads.
- **Extended** (new):
  - `POST /v1/query` supports flags `rewrite=true/false`, `use_graph=true/false`, `strategies=[...]`.
  - `GET /v1/graph/entities`, `/v1/graph/neighbors/{entity}` (aligned to branch spec).
  - Admin/debug: `GET /indexes`, `POST /indexes/reload`.

## Testing & Validation
- **Contract tests**: rag_api golden fixtures; ensure backward-compatible responses.
- **FAISS checks**: on startup verify dim/metric/quantizer; fail fast on mismatch; sample-query parity vs FAISS native search.
- **Rewrite/KG efficacy**: A/B on recall (nDCG@10, hit@5) with and without rewrite/KG; ensure latency within 1–2s.
- **KG correctness**: path/neighborhood queries vs known triples.
- **Security**: RBAC/RLS, ACL filtering across FAISS/pgvector/Qdrant.
- **Resilience**: fall back to BM25-only if embeddings/KG unavailable; FAISS unavailable -> continue with pgvector/BM25.

## Migration / Coexistence
- Phase 1: rag_api compatibility on current stores + FAISS registry.
- Phase 2: Enable query rewriting + reranker (flagged); monitor latency.
- Phase 3: Enable KG ingestion/query for tenants that opt in; gradual rollout.
- Optional: introduce Qdrant/Elasticsearch if scale or branch infra is present; keep pgvector path for small tenants.

## Observability
- OTel traces across rewrite→retrieve→rerank→LLM; include index used (faiss|pgvector|qdrant), rewrite strategies, KG hits.
- Metrics: rewrite hit rate, KG latency, FAISS query latency, fuse distribution, reranker time, LLM time, overall P99.

## Assumptions
- Index size still <5M chunks but may grow; FAISS indices remain read-only until backfill.
- GPUs available for embeddings/LLM; CPUs acceptable for reranker at this scale.
- Neo4j available for prod; NetworkX acceptable for dev/test.
- OIDC/API-key auth and mTLS in cluster; secrets via Vault/SealedSecrets.
