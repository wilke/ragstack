# Production-Ready RAG Stack (Self-Hosted, Docs, <5M chunks, 1-2s P99)

## Summary
- Build a self-hosted RAG platform for internal document search/QA with <5M chunks, balanced 1-2s P99 latency, standard enterprise data (no PHI/PCI).
- Use Kubernetes for all services; favor open-source, self-managed components with clear SLOs, observability, and security controls.

## Core Components & Decisions
- Ingestion pipeline: Doc watchers + API upload into object storage (MinIO/S3-compatible). Normalize (PDF/Office/HTML), OCR if needed (tesseract/rapidocr), sanitize PII-lite, deduplicate, and write events to Kafka/Redpanda. Background workers (Celery/Arq) handle chunking and embedding.
- Chunking & metadata: Recursive splitting with semantic boundaries (titles/sections), 200-400 tokens target, retain source, page, heading, ACL tags, freshness timestamp, hash for drift.
- Embeddings service: Self-hosted embedding model (e.g., bge-large or Nomic-embed) served via vLLM/TGI with GPU; batch plus cache; expose gRPC/HTTP.
- Vector store: Postgres + pgvector (fits <5M chunks) with IVF/HNSW indexes, table-partitioned by collection/tenant; RLS for ACL; WAL archiving plus PITR.
- Metadata store: Same Postgres schema; store doc catalog, versions, ACLs, pipelines, eval results.
- Retriever: Hybrid (dense plus BM25 via pg_trgm) with top-k=40 then metadata filters (ACL, freshness), score fusion (RRF).
- Reranker: Cross-encoder (e.g., bge-reranker-large or Cohere Rerank self-hosted if licensed) to re-rank top 40 to 5.
- LLM inference: Self-hosted instruct model (e.g., Llama 3 or Mistral-family) on GPUs via vLLM; enable speculative sampling and max tokens caps; model registry with blue/green rollout.
- Orchestration/API gateway: Single service exposing /query, /ingest, /collections (REST+OpenAPI); internal DAG orchestrator (Prefect/Temporal) for pipelines; handles authn (OIDC) and authz (RBAC/ABAC on metadata).
- Prompting & guardrails: Prompt templates versioned; context window budget; input/output validation (Pydantic/JSON Schema), PII-lite mask, allow/deny domains, safety classifier; citation return of source ids.
- Caching: Two-tier - embedding cache (Redis), response cache (Redis/KeyDB) keyed by normalized query plus user ACL hash; TTL tuned to freshness.
- Observability: OpenTelemetry tracing across pipeline; structured logs to Loki; metrics to Prometheus/Grafana (latency, recall proxy, embedding QPS, queue lag, vector store hit ratio); alerts on SLOs.
- Evaluation: Nightly offline eval set (gold questions) plus synthetic generation; metrics: nDCG@k, hit@k, faithfulness, latency. Canary shadow traffic for regressions; store scores in Postgres.
- CI/CD: Git-based pipelines - unit/integration tests, contract tests on /query, smoke deploy to staging, data migrations, auto OpenAPI publishing, Helm chart for k8s deploy.
- Security/Governance: SSO via OIDC, RLS in Postgres, encryption in transit (mTLS) and at rest, secrets in Vault/SealedSecrets, audit logs for queries/ingestions, least-privilege IAM for object store.
- Resilience/DR: Multi-AZ Postgres, Kafka replication, vLLM stateless with HPA, object storage replication; nightly backups plus PITR drills; graceful degrade (fallback to BM25-only if embeddings down).

## Public APIs / Interfaces
- POST /ingest: metadata plus upload URL; returns doc_id/version.
- POST /ingest/{doc_id}/commit: finalize; triggers chunk plus embed job.
- GET /collections: list with ACL.
- POST /query: {query, top_k, filters, user_context}; returns answers, citations, scores, latency breakdown.
- Events on Kafka: doc.ingested, doc.chunked, doc.embedded, index.updated, eval.result.
- Embedding service gRPC: Embed(batch<Text>) -> embeddings.

## Test Cases & Scenarios
- Retrieval quality: nDCG@10, hit@5 on gold set; regression gate on PRs.
- Latency/SLO: load test to P99 <= 2s at expected QPS; chaos: drop embedding/reranker nodes -> fallback path.
- Security: RBAC/RLS enforcement, multi-tenant data leak tests, mTLS enforcement.
- Ingestion: idempotent re-upload same doc; malformed file; large file (500MB) backpressure; OCR path.
- Caching: correctness under cache hit/miss; invalidation on doc updates.
- DR: restore from backup; vector/index rebuild; failover of Postgres primary.
- Observability: trace completeness; alert firing on SLO breach.

## Assumptions & Defaults
- Language: Python services; gRPC/HTTP with FastAPI; Celery workers; Helm deploy on k8s.
- Hardware: GPU nodes for LLM/embeddings; CPU for reranker acceptable at this scale.
- Storage: MinIO for objects; Postgres 14+ with pgvector and pg_trgm.
- Models: Latest permissive-license open-source models; weights hosted locally.
- Scale: <5M chunks; balanced 1-2s P99 latency; standard enterprise compliance (no PHI/PCI).
