# RagStack Plan G4 — Implementation-Ready, Self-Hosted, Compatibility-Preserving

## Goals and Non-Functional Targets
- Serve <5M chunks with P99 query latency 1–2s; keep throughput goal explicit in perf tests.
- Self-hosted, Kubernetes-first; default to open-source components; secrets via Vault/SealedSecrets.
- Backward compatibility with rag_api HTTP contract and existing FAISS indices (read-only to start).
- Forward features: hybrid retrieval, reranking, query rewriting, optional knowledge graph enrichment.
- Security: OIDC/API key, RLS/ACL on metadata, mTLS in cluster, audit logs; no PHI/PCI assumed.

## Scope
Deliver a Go-based service (Chi router) that:
1) Exposes rag_api-compatible endpoints and extended v1 endpoints.
2) Mounts legacy FAISS indices from /data/*.index via a registry and merges with new retrieval.
3) Implements rewrite → retrieve → rerank → generate pipeline with optional KG context.
4) Runs on k8s with Helm chart, uses Postgres+pgvector (primary), Redis cache, MinIO, optional Neo4j.

## Architecture Overview
- API: Go + Chi; SSE for streaming; OIDC/API key auth; rate limiting via Redis.
- Orchestrator: pipeline service calling rewrite → retrieve (pgvector + BM25 + FAISS) → rerank → context → LLM.
- Stores: Postgres 16 (metadata, BM25 via pg_trgm, pgvector), Redis (cache/rate limit), MinIO (objects), Neo4j optional (KG), FAISS registry (read-only sidecar or CGO chosen per env).
- Models: self-hosted embeddings (bge-base/large) via HTTP sidecar; reranker bge-reranker-large; LLM via vLLM (Llama/Mistral) with streaming.
- Message bus optional (Kafka/Redpanda) for ingestion events; keep in-process queue (River) as default.

## Project Structure (files to create/update)
- cmd/api/main.go — HTTP server bootstrap, wiring, SSE.
- cmd/worker/main.go — ingestion worker (River) plus KG extractor jobs.
- cmd/faiss-sidecar/{main.py,requirements.txt,Dockerfile} — legacy FAISS service (kept read-only).
- internal/config/config.go — k8s-friendly env config; sections: server, db, redis, objectstore, auth, faiss, rewrite, rerank, kg, limits.
- internal/tenant/{context.go,middleware.go} — tenant propagation.
- internal/auth/{auth.go,oidc.go,apikey.go} — authn+authz.
- internal/document/{loader_*.go,chunker.go,metadata.go} — loaders (text/md/pdf/html/docx), 200–400 token chunks with overlap; checksum + ACL tags.
- internal/embedding/{provider.go,openai.go,local.go,cache.go} — HTTP embedding (local/vLLM) + cache.
- internal/vectorstore/{store.go,pgvector.go} — pgvector wrapper; HNSW/IVF creation.
- internal/search/{engine.go,vector.go,bm25.go,faiss.go,rrf.go,filters.go} — hybrid search + RRF + ACL filters.
- internal/legacy/{registry.go,faiss_client.go,merger.go} — registry for /data/*.index, health/warmup, score fusion.
- internal/rewrite/{rewriter.go,passthrough.go,hyde.go,multiquery.go,stepback.go,entity_expand.go} — strategies flagged per request.
- internal/rerank/{reranker.go,crossencoder.go} — local cross-encoder; API fallback optional.
- internal/graph/{store.go,neo4j.go,extractor.go,llm_extractor.go,model.go} — optional KG path.
- internal/generation/{service.go,context.go} — context builder with token budget; LLM client.
- internal/api/{router.go,middleware.go,handler_legacy.go,handler_query.go,handler_document.go,handler_collection.go,handler_graph.go,request.go,response.go,sse.go}.
- internal/storage/postgres/{db.go,tenant.go,apikey.go,collection.go,document.go,chunk.go,triple.go,querylog.go} — includes pg_trgm search and pgvector operations.
- internal/storage/redis/{client.go,cache.go,ratelimit.go}.
- migrations/00{1..9}_*.sql — see Schema section.
- deploy/{Dockerfile,docker-compose.yml,helm/Chart.yaml,values.yaml} — dev + k8s.
- testdata/ — sample docs, FAISS fixtures.

## Schema (Postgres)
1. tenants, api_keys (hashed), collections (unique per tenant), documents (status, acl, checksum, version), chunks (content, embedding_id, tsvector, pgvector column), query_log.
2. legacy_indices table (name, program, path, dim, metric, active, priority) to mirror FAISS registry metadata.
3. triples (tenant_id, doc_id, subject, predicate, object, metadata) for KG fallback; nullable if Neo4j primary.
Indexes lead with tenant_id; pg_trgm on chunks.content; pgvector index on chunks.embedding.

## Ingestion Pipeline
- Steps: upload → detect MIME → load → chunk → embed batch → store chunks (Postgres) + vectors (pgvector) + objects (MinIO) → optional KG extract → mark ready.
- Events: emit doc.ingested, doc.chunked, doc.embedded, graph.built (Kafka optional; log if bus absent).
- Idempotency: checksum/hash per document version; skip duplicate; invalidate caches.

## Query Pipeline
1) Rewrite (flags): passthrough default; HyDE; multi-query(N); step-back; entity expansion (uses KG entities). Limit total variants to keep latency.
2) Retrieve: per variant run pgvector search + BM25 (pg_trgm) + FAISS registry (read-only); apply ACL/freshness filters; fuse via RRF.
3) Rerank: cross-encoder on top-K (e.g., 40 → 5); fallback to fused scores on failure.
4) Context: dedupe by doc/chunk id; enforce token budget; include metadata for citations.
5) Generate: LLM via vLLM; streaming SSE supported; return answer, sources, timings, trace id.

## Compatibility (rag_api + FAISS)
- Legacy routes exposed under root: /health, /databases, /databases/{db}, /query/{db}; payloads match rag_api (tests with golden fixtures).
- FAISS registry: load /data/*.index, read header to assert dim/metric, tag names (legacy_a, legacy_b). Health + warmup endpoint. Read-only; writes flow to new store.
- Result merger preserves legacy ordering semantics; score_threshold applied per index before fusion.

## Knowledge Graph (optional per tenant)
- Ingestion: NER + relation extraction (LLM prompt) to triples; store in Neo4j and Postgres fallback.
- Query: when use_graph=true, fetch neighborhood(depth=1) for key entities; convert triples to synthetic chunks and include in RRF with base score.
- Feature flag per tenant/env; latency budget <150ms target for KG step.

## Query Rewriting
- Strategies configurable per request and via server defaults. Guardrails: cap total variants, track added latency; log rewrite hit-rate.

## Reranking
- Local cross-encoder (bge-reranker-large) over HTTP sidecar; configurable top_k input and output size. API reranker optional fallback.

## Caching
- Embedding cache (Redis, tenant-prefixed). Response cache keyed by normalized query + ACL hash with short TTL; bypass when rewrite/KG enabled to avoid stale contexts.

## Security
- Auth: OIDC bearer and API key; scopes per key. Rate limit per tenant and per IP.
- Data: RLS on Postgres tables; object keys prefixed by tenant. mTLS inside cluster; audit log query + admin actions.

## Observability
- OTel traces across rewrite→retrieve→rerank→LLM; include index used and strategies.
- Metrics: latency per stage, rewrite hit rate, KG latency, FAISS query latency/QPS, cache hit rate, reranker time, LLM time, overall P99. Dashboards in Grafana; alerts on SLO breach.

## Testing and Validation
- Contract tests: rag_api golden fixtures (200/4xx). Compatibility parity for FAISS indices.
- Retrieval tests: FAISS dimension/metric mismatch detection; corrupted index load failure; ACL filter correctness.
- Rewrite/KG A/B: nDCG@10, hit@5 with/without flags; ensure added latency stays within budget.
- Load/perf: k6 or vegeta to validate P99 <= 2s at target QPS with FAISS + pgvector; chaos to drop reranker/KG and verify graceful degrade.
- Security: RLS/ACL leak tests; authn/authz matrix; mTLS enforced.

## Rollout Phases
1) Core & Legacy: deploy API + ingestion + pgvector/BM25 + FAISS registry; rag_api parity tests pass; reranker off.
2) Reranker + Rewrite: enable reranker, then rewrite strategies under feature flags; monitor latency.
3) Knowledge Graph: enable ingestion extraction and query flag per tenant; monitor recall and latency.
4) Hardening: perf tuning, cache tuning, alerting, backup/restore drills, DR failover test.

## Risks and Mitigations
- Latency creep from rewrite/KG: cap variants, set KG timeout, fallback to hybrid only.
- FAISS/embedding mismatch: enforce header check and fail fast; surface health.
- Cost of GPUs for LLM/embeddings: support CPU reranker and allow external API model config.
- Data drift: document checksum + versioning; invalidate caches on new versions.

## Verification Checklist (per release)
- make docker-up (dev) / Helm install (staging) succeeds; migrations applied.
- rag_api contract suite green; FAISS health OK; dim/metric validated.
- P99 <= 2s at target QPS with rewrite off; with rewrite/KG on, P99 within agreed budget.
- Security tests pass (authn/authz, RLS); audit log captures query + admin actions.
- Observability dashboards show rewrite/KG/rerank timings; alerts armed.
