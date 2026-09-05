# Integrate distllm/mcqa learnings, mirror rag_api surface, and ingest two existing FAISS indices

## Summary
- Re-implement the cucinellclark/rag_api HTTP contract on our stack while preserving behavior.
- Audit both external repos (distllm/mcqa, rag_api) to extract reusable ideas and strengths.
- Mount two existing FAISS indices from /data/*.index into the new retrieval layer (read-only to start) and harmonize metadata with our catalog.
- Deliver compatibility tests that prove API parity and correct FAISS-backed results.

## Planned Work
- Repo due-diligence
  - Clone ramanathanlab/distllm and review distllm/mcqa: catalog its MCQA pipeline, evaluation harness, and any reusable utilities (e.g., dataset loaders, scoring, distributed inference patterns). Decide whether to import its eval flow for our regression suite.
  - Clone cucinellclark/rag_api: extract OpenAPI/routers, request/response schemas, persistence choices (likely FAISS/pgvector), and middleware (auth, logging). Capture strengths (e.g., lean FastAPI design, simple FAISS wiring) and gaps to address (observability, auth, caching).
- API compatibility layer
  - Generate/Open OpenAPI spec from rag_api; freeze as contract for parity tests.
  - Implement controllers in our service to match endpoints, status codes, and payload shapes; add feature flags if behavior diverges.
  - Provide translation if our internal models differ (e.g., map our metadata fields to rag_api response schema).
- FAISS index integration
  - Inspect both indices: dimension, metric (L2/IP), trained quantizer type, and ID mapping (IndexIDMap or flat). Validate they align with the believed embedding model (Mistral-7B-Instruct-v0.3 or detect from index header).
  - Build an index registry that loads indices from /data/*.index, tags them (e.g., legacy_a, legacy_b), and exposes them to the retriever; keep read-only at first.
  - Create metadata bridge: for each FAISS vector ID, map to document/page metadata from our catalog; if absent, derive minimal stubs and plan a backfill job.
  - Add health checks and warmup for FAISS shards; ensure HNSW/IVF prefetch if applicable.
- Retriever/RAG path
  - Implement hybrid retrieval pipeline that can route to legacy FAISS indices or new store via config; fuse scores and enforce ACL filtering at the metadata layer.
  - Ensure reranker and LLM layers remain unchanged; guarantee latency target (1–2s P99) with FAISS access.
- Evaluation & strengths capture
  - Run distllm/mcqa eval flow on a representative MCQA set using our pipeline to benchmark recall/accuracy; store results to compare against rag_api baseline.
  - Document “keep” items from each repo (e.g., mcqa scoring scripts, rag_api endpoint design) and “drop/replace” decisions.
- Testing & verification
  - Contract tests against captured rag_api OpenAPI for all endpoints (200/4xx cases).
  - Retrieval correctness: compare top-k results from legacy FAISS vs our pipeline given identical embeddings; ensure deterministic ordering where expected.
  - Index introspection tests: fail fast if dimension/metric mismatch or if index files are missing/corrupt.
  - Load tests to confirm P99 <= 2s with FAISS indices loaded; alerting hooks wired.
- Deployment considerations
  - Mount /data/*.index into the retriever pods; document required disk/IO.
  - Blue/green rollout with config flag to switch traffic from legacy FAISS only -> hybrid -> new store.
  - Observability: expose FAISS query latency/QPS, cache hit rates, and index health metrics; log query + collection + shard.

## Public APIs / Interfaces
- Mirror rag_api endpoints exactly (paths, verbs, JSON schemas). Publish generated OpenAPI and version it.
- Internal index-registry API: GET /indexes (debug), POST /indexes/reload (admin, optional).

## Test Cases & Scenarios
- API parity: golden request/response fixtures from rag_api for each route.
- FAISS dimension mismatch detection; corrupted index load failure.
- Query with filters/metadata and verify legacy index respects ACL/filtering via metadata bridge.
- Throughput/latency under concurrent queries while loading both indices.
- Eval: MCQA accuracy/regression using distllm/mcqa harness.

## Assumptions / Defaults
- FAISS files live at /data/*.index and are readable in the runtime containers.
- Embeddings used to build the indices correspond to Mistral-7B-Instruct-v0.3 (to be verified via FAISS header); if mismatch, we will detect and reconcile.
- We will not mutate the legacy indices initially; writes go to the new pipeline, with a later reindex/backfill plan.
- We re-implement the rag_api surface, not deploy it verbatim, but will keep any reusable utilities if lightweight.
