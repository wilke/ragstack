# Plan: Robust, scalable PDF ingest + retrieve for ragstack

## Context

ragstack (v0.3.0, `/rag/repos/ragstack`) has a contract-first RAG API with Qdrant wired into a **fully synchronous** `/v1/ingest` and a working `/v1/retrieve`. The goal is the shortest robust path to a **scalable ingest+retrieve API/workflow** that (a) supports **PDF import → chunk → embed → store → retrieve-by-question** today, (b) lets the team **test different models** for embedding and (later) knowledge-graph feature extraction, and (c) scales ingest **1 → 500,000 documents**.

User decisions (binding): **single host now (coconut, 8×H200), cluster later** behind interfaces; **retrieve loop first**, KG seam now / build later; **one OpenAI-compatible serving abstraction** with persistent vLLM as default and on-demand as the same interface + launcher.

This plan is the synthesis of a multi-team exercise (primary + contra architectures, reviewed by systems-eng, software-eng, cybersecurity, and red-team agents). Its load-bearing code claims were verified against the live tree (see Verification). The headline finding: **chunk IDs are random (`chunkers.py:25` `uuid.uuid4()`), so every re-ingest silently duplicates the corpus in Qdrant** — this must be fixed before any scaling work, because all resumability/idempotency depends on it.

## Recommended approach

**Hybrid: contra-plan's shard/manifest seam, delivered on the primary plan's thin-router critical path.**

- Keep the existing `/v1/ingest` request contract; insert orchestration *behind* it.
- Adopt `IngestBackend.run_shards(manifest, shard_fn)` as the distribution seam — bounded shards fix unbounded batching by construction and map 1:1 onto Parsl/GoWe/k8s scatter later. Build only the single-host `LocalAsyncIORunner` (bounded `asyncio.Semaphore`, **no broker** — Redis/Celery are not on the critical path) now.
- One OpenAI-compatible `ModelEndpoint` abstraction (adopt ExaForge `EndpointPool` + `InferenceClient`) serves embedding, reranker, and future KG-LLM. Persistent vLLM is default; on-demand is the same interface + an `EndpointProvider.acquire/release` launcher — consumers never branch.

**GoWe: No for now, optional later, never the live control plane.** Verified blockers (`SetMaxOpenConns(1)` single writer, eager unbounded scatter in one tick, `O(N)` poll scan) make it unfit at 500k as written; fixing it is out-of-repo Go work. Keep it reachable behind `IngestBackend.run_shards` for HPC full-corpus reprocessing only, fed a sharded outer scatter (1 task = 1 shard ≈ 500 tasks, not 500k).

### Persistent services vs workflow steps
- **Long-lived services:** vLLM (embedding, default; reranker at M4; KG-LLM at M5), Qdrant, Postgres (job/shard status, M2), Neo4j (M5 only). Redis/Celery only if cross-process fan-out is later needed.
- **Workflow steps (bounded shards):** loader-dispatch → chunk → embed (token-batched, poison-isolated) → vector upsert → text index → (M5) KG extract. **Retrieve stays a synchronous low-latency handler** (`query.py:_retrieve` already works).

## Milestones

### M1 — PDF→chunk→embed→Qdrant→retrieve on coconut, restart-safe, secured (Effort: M/L)
A `.pdf` POSTed to `/v1/ingest` is chunked, embedded against persistent vLLM `/v1/embeddings`, upserted to Qdrant, retrievable by question — **idempotent on re-run**, truthful job status, behind auth. Includes the 5 non-negotiable correctness/security fixes below.

### M2 — Shard/manifest batch + resumable 1→500k (Effort: L)
Add `IngestBackend.run_shards` + `LocalAsyncIORunner`; `Manifest`/`ShardResults` model (carry chunk addressing sufficient for a later KG-only re-run). Promote `JobStore` to Postgres as the **single checkpoint of record** (not a file-based completed-set on the 500k write path). Make manifest-build for huge submits **off-request, bounded, resumable**; 1-doc fast path still writes a real job row. Multi-endpoint pool across H200s with `least_loaded` + per-tenant concurrency quota.

### M3 — Capacity, supervision, observability (Effort: M)
One-page capacity model (vLLM replica layout across 8×H200; **500k bottleneck is likely Qdrant upsert + PDF parsing, not GPU embedding**). Process supervision (stores → vLLM gated on `/health` → API gated on collection-ready). Static `CUDA_VISIBLE_DEVICES` partition between persistent serving and on-demand launchers. Surface shard progress on `GET /v1/ingest/{job_id}`; wire the declared-but-unused OTEL endpoint.

### M4 — Serving non-exclusivity + model-experiment harness (Effort: M)
`EndpointProvider.acquire/release` with `PersistentProvider` (default) + `LauncherProvider` (on-demand vLLM cold-start into the same pool; borrow ExaForge `aegis_bridge.py` / distllm `vllm_backend.py:33` under Parsl). `models.yaml` registry + `bench` command (same immutable manifest → each provider → throughput + retrieval quality). Add `OpenAIScorer` beside `CrossEncoderScorer` (`scoring/scorers.py:42`). Wire `/v1/query` generation via lifted distllm `generators/__init__.py` (STRATEGIES) + `rag/response_synthesizer.py` (`RagGenerator`).

### M5 — KG / semantic feature extraction (Effort: L)
LLM-backed `KGExtractor` (`protocols.py:76`, interface-only today; `graph/__init__.py` empty) calling the **same `ModelEndpoint`** (depend on the protocol, not concrete `OpenAIEmbedder`). Neo4j `GraphStore` driver (config-only today, `config.py:36-38`). Runs as its **own shard pass over stored chunks** — re-run KG without re-embedding. Wire `/entities` + `/neighbors`.

### M6 — Optional cluster/HPC dispatch (Effort: M; + out-of-repo Go epic if GoWe)
`ParslRunner` (Polaris/Aurora) and/or `GoWeRunner` (CWL scatter, 1 task=1 shard) behind `IngestBackend`. GoWe requires a Postgres `Store` impl first — explicit prerequisite, never a drop-in.

## Non-negotiable fixes (land in M1 — every reviewer flagged ≥1 as high/critical)

1. **Deterministic chunk IDs (CRITICAL).** `chunkers.py:25` → derive `uuid5(NAMESPACE_URL, f"{doc_id}:{start_char}:{end_char}")` (or content hash). Without this, re-ingest duplicates the corpus. Add a double-ingest conformance test asserting unchanged Qdrant point count.
2. **Bounded, poison-isolated embed (HIGH).** `pipeline.py:49-50` sends all chunks in one call; `OpenAIEmbedder.raise_for_status()` fails the whole batch on one bad input. Add token-budget sub-batching (borrow embedding_app `lib/embedding_utils.py:183`) + bisect-on-failure + dead-letter quarantine.
3. **Loud backend fallback (HIGH).** `deps.py:39-44` only `log.warning`s on Qdrant `ImportError`; `:59` hardcodes `InMemoryTextIndex`. Add `require_durable_backends=true` → startup hard-fail in non-dev; readiness gate before API accepts ingest.
4. **Dim reconciliation (HIGH).** `qdrant.py:44-48` returns early without checking `vector_size`. Auto-name collections by `(model, dim)`; hard-fail on startup if an existing collection's size ≠ `embedding_model_dim`. (This is the core model-testing flow — it bites immediately.)
5. **Security gate before network exposure (CRITICAL).** No auth (`api_keys` declared, wired to nothing); `request.source` → unconfined `Path(...).read_text()` = unauthenticated LFI of `.env`/secrets; CORS `['*']`+credentials; no tenant isolation; raw exception leakage. Wire `settings.api_keys` (APIKeyHeader, fail-closed), confine `source` to `INGEST_ROOT` (or switch to upload-body), sandbox/cap PDF parsing (JS off, no remote-URI fetch → SSRF), stamp+enforce `tenant_id` server-side, scrub `documents.py:48` exception detail.

## Critical files to modify (M1)

- `python/ragstack/ingestion/loaders.py` — **add** `PdfLoader` (pymupdf, JS disabled) + `LoaderRegistry` dispatch by `Path.suffix`; fail-soft per doc; confine to `INGEST_ROOT`.
- `python/ragstack/ingestion/chunkers.py` — deterministic IDs (`:25`).
- `python/ragstack/api/deps.py` — registry instead of hardwired `TextFileLoader` (`:74`); loud hard-fail for Qdrant fallback (`:39-44`) + `InMemoryTextIndex` (`:59`) under `require_durable_backends`; startup readiness + dim reconciliation.
- `python/ragstack/stores/qdrant.py` — collection name = `f(model,dim)`; reject write on size mismatch (`ensure_collection`, `:44`).
- `python/ragstack/embedders.py` — token-budget sub-batching + per-input bisect/dead-letter; route through new `ModelEndpoint`.
- `python/ragstack/api/routers/documents.py` — keep router signature; back `job_id` + `GET /v1/ingest/{job_id}` with a real `JobStore` (SQLite-status stopgap OK); scrub exception detail (`:48`).
- `python/ragstack/api/main.py` — wire `settings.api_keys` (APIKeyHeader, fail-closed in non-dev); tighten CORS.
- `python/tests/api/test_endpoints.py` — fix the actual stale test `test_ingest_endpoint_returns_accepted` (**there is no `test_ingest.py`**); add double-ingest idempotency test.
- `pyproject.toml` — add pymupdf + vector extras.

## Reuse (don't rebuild)

- **Adopt code:** ExaForge `src/exaforge/endpoints.py` (`EndpointPool`) + `src/exaforge/client.py` (`InferenceClient`) → the `ModelEndpoint` transport (health/LB/semaphore/retries/errors-as-data).
- **Borrow design:** embedding_app `lib/embedding_utils.py:183` (token-budget batching); ExaForge `checkpoint.py` two-phase scan/`read_by_ids` *pattern* (use Postgres on the 500k path, not its in-memory `set[str]`/whole-file rewrite); distllm `generators/__init__.py` STRATEGIES + `rag/response_synthesizer.py` (M4 generation), `vllm_backend.py:33` + `aegis_bridge.py` (M4 on-demand).

## Model serving (how to test a model)

One `ModelEndpoint` = `base_url` + model + api_key in a resilient pool; all roles consume it (`/v1/embeddings`, `/v1/score`, `/v1/chat/completions`). **Persistent (default):** point the pool at `vllm serve <model> --runner pooling` URLs across the H200s. **On-demand (same interface):** `LauncherProvider` cold-starts vLLM, registers `host:port` into the same pool, runs the batch, tears down — non-exclusive with persistent. **To test an embedding model:** add to `models.yaml`, set `(base_url, model, dim)`; collection auto-named `f(model,dim)` (physical A/B isolation + dim-mismatch hard-fail); re-run the same immutable manifest via `bench`. No code change. **KG-LLM:** same mechanism, role=`kg`; depends on the endpoint protocol so a provider-native client (distllm STRATEGIES: OpenAI/Anthropic/Gemini/HF) can slot in.

## Verification

- **Spot-checked claims (done, plan mode):** `chunkers.py:25`=uuid4 ✓; `qdrant.py:48` early return + `:125` uuid5 ✓; `deps.py:39-44/59` silent fallback ✓.
- **M1 end-to-end:** start Qdrant + a vLLM embedding endpoint; `POST /v1/ingest` a real PDF → assert 200 + chunk_ids; `POST /v1/retrieve` a question → assert relevant `sources`. Run `make test-conformance-python` against the live flow (after fixing the stale assertion).
- **Idempotency:** ingest the same PDF twice → assert Qdrant point count is identical (the deterministic-ID regression test).
- **Dim safety:** start with `EMBEDDING_MODEL_DIM` ≠ existing collection size → assert startup hard-fails (no silent mixed-vector writes).
- **Security gate:** unauthenticated request → 401; `request.source` outside `INGEST_ROOT` → rejected; CORS preflight from a disallowed origin → blocked.
- **Scale smoke (M2):** ingest a few-thousand-doc batch, kill mid-run, restart → assert it resumes from the Postgres checkpoint with no duplicates.

## Note on process (answering the meta-question)

The multi-agent treatment was warranted *here* because the decision is architecturally load-bearing and rested on unfamiliar-codebase claims that needed disconfirmation — the adversarial pass caught the random-chunk-ID corruption that **both** plans would otherwise have shipped. It is not a default: for known-shape work, a single "ultraplan" with one mandatory rule — **every file:line / quantitative claim verified against code before acceptance** — captures most of the value at a fraction of the cost.
