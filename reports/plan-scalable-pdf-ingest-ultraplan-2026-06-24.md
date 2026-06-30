# Design: Robust, scalable PDF ingest + retrieve

> Status: **approved design / roadmap** (refined remotely via Ultraplan). M1 is specified to
> implementation depth; M2–M6 to design-seam depth. All `file:line` claims were verified against the
> tree at the time of writing — re-verify before implementing a milestone, as line numbers drift.
>
> NOTE (local addendum, coconut): the cloud checkout did **not** contain the sibling repos, so it
> calls ExaForge/distllm/embedding_app/GoWe "design references only." On coconut they DO exist under
> `/rag/repos/` — so here we can vendor/copy the actual source (e.g. ExaForge `endpoints.py`/`client.py`)
> rather than reimplement. Still don't add a hard `from exaforge import ...` dependency; copy into ragstack.

## Context

RAGStack has a contract-first RAG API with Qdrant wired into a **fully synchronous** `/v1/ingest`
and a working `/v1/retrieve`. This is the shortest robust path to a **scalable ingest+retrieve**
workflow that (a) supports **PDF import → chunk → embed → store → retrieve-by-question** today,
(b) lets the team **swap embedding (and later KG) models** for experiments, and (c) scales ingest
**1 → 500,000 documents**.

Binding decisions: single host now (coconut, 8×H200), cluster later behind interfaces; retrieve loop
first, KG seam now / build later; one OpenAI-compatible serving abstraction with persistent vLLM as
default and on-demand as the same interface + launcher.

## The headline correctness bug (verified — TWO layers, deeper than first reported)

Re-ingesting the same document **silently duplicates the corpus in Qdrant**. It is a two-layer
ID-randomness problem, not a single line:

- `python/ragstack/stores/qdrant.py:123-125` already maps chunk IDs to **deterministic** UUID5 point
  IDs (`uuid5(NAMESPACE_URL, chunk_id)`) — the storage layer is correct *given stable inputs* and
  overwrites in place.
- But `python/ragstack/ingestion/loaders.py:18` assigns a **random `uuid4()` document ID on every
  load**, and `python/ragstack/ingestion/chunkers.py:25` assigns a **random `uuid4()` chunk ID**
  derived from nothing stable. So each re-ingest → new chunk IDs → new point IDs → duplicate points.

**Both layers must be made deterministic.** Fixing only `chunkers.py` is insufficient, because a
deterministic chunk ID built from `doc.id` is still random when `doc.id` is random per-load. All
resumability/idempotency in M2 depends on this; it lands first in M1.

## Recommended architecture

Keep the existing `/v1/ingest` request contract; insert orchestration *behind* it. Adopt a single
distribution seam — `IngestBackend.run_shards(manifest, shard_fn)` — where bounded shards fix
unbounded batching by construction and map 1:1 onto cluster scatter later. Build only the
single-host runner now (bounded `asyncio.Semaphore`, **no broker** — Redis/Celery off the critical
path). One OpenAI-compatible `ModelEndpoint` abstraction serves embedding, reranker, and future
KG-LLM; persistent vLLM is default, on-demand is the same interface behind an
`EndpointProvider.acquire/release` launcher so consumers never branch.

- **Long-lived services:** vLLM (embedding default; reranker M4; KG-LLM M5), Qdrant, Postgres
  (job/shard status, M2), Neo4j (M5). Redis/Celery only if cross-process fan-out is later needed.
- **Workflow steps (bounded shards):** loader-dispatch → chunk → embed (token-batched,
  poison-isolated) → vector upsert → text index → (M5) KG extract.
- **Retrieve stays a synchronous low-latency handler** — `api/routers/query.py` `_retrieve`
  (lines 41-59) already works against Qdrant.

## M1 — async PDF→chunk→embed→Qdrant→retrieve, restart-safe, secured

A `.pdf` POSTed to `/v1/ingest` returns `status="accepted"` + a real `job_id` **immediately**, runs
in the background (chunk → embed against persistent vLLM `/v1/embeddings` → Qdrant upsert), is
retrievable by question, **idempotent on re-run**, with truthful poll status and auth.
`GET /v1/ingest/{job_id}` reports real progress (pending/running/completed/failed) from a `JobStore`
(SQLite stopgap acceptable; Postgres in M2).

### Deterministic IDs (the load-bearing fix)

- `loaders.py` — `doc.id = uuid5(NAMESPACE_URL, normalized_source)` (or content hash for
  body-uploads). Apply to **both** `TextFileLoader` and `StringLoader`.
- `chunkers.py:25` — `chunk.id = uuid5(NAMESPACE_URL, f"{doc.id}:{start}:{end}")`.
- Add a double-ingest test asserting Qdrant point count is unchanged after re-ingesting the same PDF.

### M1 file-by-file

- `python/ragstack/ingestion/loaders.py` — deterministic `doc.id`; **add** `PdfLoader` (pymupdf,
  JavaScript disabled, no remote-resource fetch) + `LoaderRegistry` dispatching on `Path.suffix`;
  fail-soft per document; confine `source` to an `INGEST_ROOT` (defeats the current unauthenticated
  LFI — `request.source` flows straight into `Path(source).read_text()`).
- `python/ragstack/ingestion/chunkers.py` — deterministic `chunk.id` (`:25`).
- `python/ragstack/embedders.py` — token-budget sub-batching + per-input bisect-on-failure +
  dead-letter quarantine (today `OpenAIEmbedder.embed` at `:54-66` sends the whole list in one POST
  and `raise_for_status` at `:64` fails the entire batch on one bad input). Keep
  `make_embedder(api=...)`; route transport through the future `ModelEndpoint`.
- `python/ragstack/ingestion/pipeline.py` — `ingest` (`:41-63`) currently embeds all chunks in one
  `embedder.embed(texts)` call (`:49-50`); switch to the batched/poison-isolated embedder; keep the
  KG seam (`:59-61`) untouched.
- `python/ragstack/stores/qdrant.py` — collection name = `f(model, dim)`; in `ensure_collection`
  (`:44-54`, currently early-returns at `:47-48` with **no size check**), if the collection exists
  with a different `vector_size`, **hard-fail at startup** instead of silently writing mixed vectors.
- `python/ragstack/api/deps.py` — replace hardwired `TextFileLoader` (`:74`) with `LoaderRegistry`;
  under a new `require_durable_backends` setting, **hard-fail** instead of `log.warning` on the
  Qdrant `ImportError` fallback (`:39-44`) and the hardcoded `InMemoryTextIndex` (`:59`); add a
  readiness gate so the API rejects ingest until the collection is ready (today `ensure_collection`
  failure is swallowed at `:70-71`).
- `python/ragstack/api/routers/documents.py` — make `POST /ingest` (`:33-54`) create a `JobStore`
  row (`status="accepted"`), schedule the pipeline as a background task, return immediately; back
  `GET /ingest/{job_id}` (`:57-65`, today hardcodes `status="unknown"`) with real status; scrub the
  leaked exception detail at `:48-49` (`f"ingest failed: {e}"`).
- `python/ragstack/api/main.py` + `config.py` — wire `settings.api_keys` (`config.py:47`, declared,
  used nowhere) via an `APIKeyHeader` dependency, fail-closed outside dev; tighten CORS — default
  `allowed_origins=["*"]` (`config.py:48`) + `allow_credentials=True` (`main.py:24`) is the misconfig
  to fix; stamp and enforce a server-side `tenant_id`.
- `python/ragstack/jobstore.py` (**new**) — minimal `JobStore` protocol + SQLite impl
  (job_id, status, counts, error). Promoted to Postgres in M2.
- `python/pyproject.toml` — add `pymupdf`; ensure the `qdrant` extra (already at `:23`,
  `qdrant-client>=1.9`) is in the default install path for non-dev.
- **Contracts first (CLAUDE.md rule):** `contracts/openapi.yaml` `/v1/ingest` already describes
  "Ingestion accepted" (`:76`) and `status` is a free-form string in
  `contracts/schemas/ingest_response.json` — so `"accepted"` is schema-valid with no schema change.
  Document the status vocabulary (`accepted|running|completed|failed`) in the OpenAPI description.
- **Tests:** the stale `status == "accepted"` assertion exists in **both**
  `python/tests/api/test_endpoints.py:42-51` *and* `conformance/test_ingest.py:12-18` — with async
  ingest `"accepted"` becomes correct, so **reconcile by giving the endpoint async semantics rather
  than editing the assertion away.** Add: double-ingest idempotency test (point-count stable),
  dim-mismatch startup-failure test, and auth/LFI/CORS negative tests.

### M1 non-negotiable fixes (each reviewer-flagged; all verified)

1. **Deterministic IDs (CRITICAL)** — `loaders.py` + `chunkers.py:25`.
2. **Bounded, poison-isolated embed (HIGH)** — `embedders.py` + `pipeline.py:49-50`.
3. **Loud backend fallback (HIGH)** — `deps.py:39-44,59` → hard-fail under `require_durable_backends`.
4. **Dim reconciliation (HIGH)** — `qdrant.py:44-54`; collection `f(model,dim)` + startup hard-fail.
   Core model-testing flow; bites the moment a second embedding model is tried.
5. **Security gate before exposure (CRITICAL)** — auth (`api_keys`), confine `source` to `INGEST_ROOT`
   (LFI), CORS, tenant stamping, exception scrub (`documents.py:48`), PDF sandbox (JS off, no remote
   fetch → SSRF).

## Roadmap (design seams — later PRs)

- **M2 — shard/manifest + resumable 1→500k.** `IngestBackend.run_shards` + `LocalAsyncIORunner`;
  `Manifest`/`ShardResults` carrying chunk addressing sufficient for a later KG-only re-run. Promote
  `JobStore` to Postgres as the single checkpoint of record (not a file-based completed-set on the
  500k write path). Off-request, bounded, resumable manifest build for huge submits; 1-doc fast path
  still writes a real job row. Multi-endpoint pool across H200s, `least_loaded` + per-tenant quota.
- **M3 — capacity, supervision, observability.** Capacity model (vLLM replica layout across 8×H200;
  500k bottleneck is likely Qdrant upsert + PDF parsing, not GPU embedding). Process supervision
  (stores → vLLM gated on `/health` → API gated on collection-ready). Static `CUDA_VISIBLE_DEVICES`
  split between persistent serving and on-demand launchers. Surface shard progress on
  `GET /v1/ingest/{job_id}`; wire the declared-but-unused `otel_exporter_otlp_endpoint` (`config.py:56`).
- **M4 — serving non-exclusivity + model-experiment harness.** `EndpointProvider.acquire/release`
  with `PersistentProvider` (default) + `LauncherProvider` (on-demand vLLM cold-start into the same
  pool). `models.yaml` registry + a `bench` command (same immutable manifest → each provider →
  throughput + retrieval quality). Add `OpenAIScorer` beside `CrossEncoderScorer`
  (`scoring/scorers.py:42`). Wire `/v1/query` generation (today placeholder, `routers/query.py:88-93`).
- **M5 — KG / semantic feature extraction.** LLM-backed `KGExtractor` (`protocols.py:75-79`,
  interface-only; `graph/__init__.py` empty) calling the **same `ModelEndpoint`** (depend on the
  protocol, not concrete `OpenAIEmbedder`). Neo4j `GraphStore` driver (config-only today,
  `config.py:36-38`). Runs as its **own shard pass over stored chunks** — re-run KG without
  re-embedding. Wire `/v1/graph/entities` + `/neighbors` (today return `[]`, `routers/graph.py:21-30`).
- **M6 — optional cluster/HPC dispatch.** `ParslRunner` and/or `GoWeRunner` (CWL scatter, 1 task =
  1 shard ≈ 500 tasks, not 500k) behind `IngestBackend`. GoWe is **never the live control plane** and
  needs a Postgres `Store` impl first (verified blockers: single-writer `SetMaxOpenConns(1)`, eager
  unbounded scatter, O(N) poll) — explicit prerequisite, not a drop-in.

## How to test a new model (the experiment flow)

One `ModelEndpoint` = `base_url` + model + api_key in a resilient pool; every role consumes it
(`/v1/embeddings`, `/v1/score`, `/v1/chat/completions`). **Persistent (default):** point the pool at
`vllm serve <model> --runner pooling` URLs. **On-demand (same interface):** `LauncherProvider`
cold-starts vLLM, registers `host:port`, runs the batch, tears down. **To test an embedding model:**
add to `models.yaml`, set `(base_url, model, dim)`; the collection auto-names `f(model,dim)` (physical
A/B isolation + dim-mismatch hard-fail) and `bench` re-runs the same immutable manifest. No code
change. **KG-LLM:** same mechanism, role=`kg`, depending on the endpoint protocol.

## Verification

Verified during planning (read against the live tree):

- `chunkers.py:25` = `uuid.uuid4()`; `loaders.py:18` Document id = `uuid.uuid4()` (the deeper bug)
- `qdrant.py:123-125` deterministic `_point_id` via uuid5; `:44-48` `ensure_collection` early-return,
  no size check
- `deps.py:39-44` silent Qdrant→InMemory fallback, `:59` hardcoded `InMemoryTextIndex`,
  `:70-71` swallowed `ensure_collection` error
- `pipeline.py:49-50` single-call embed; `embedders.py:64` batch-fatal `raise_for_status`
- `documents.py:48-49` leaked exception, `:51-52` `uuid4` job_id + `status="completed"`,
  `:65` hardcoded `"unknown"`
- `config.py:47` `api_keys` declared/unused, `:48` `allowed_origins=["*"]` + `main.py:24`
  `allow_credentials=True`; `:56` OTEL endpoint declared/unused
- `protocols.py:75-79` `KGExtractor` interface-only; `graph/__init__.py` empty;
  `routers/graph.py` returns `[]`
- `conformance/test_ingest.py` *exists* and its `status == "accepted"` assertion (`:17`) matches the
  chosen async semantics; the twin at `test_endpoints.py:50` does too

End-to-end checks when M1 is implemented:

- Start Qdrant + a vLLM embedding endpoint; `POST /v1/ingest` a real PDF → assert 200 +
  `status="accepted"` + `job_id`; poll `GET /v1/ingest/{job_id}` → `completed`; `POST /v1/retrieve` a
  question → relevant `sources`. Run `make test-conformance-python`.
- Idempotency: ingest the same PDF twice → Qdrant point count identical.
- Dim safety: start with `EMBEDDING_MODEL_DIM` ≠ existing collection size → startup hard-fails.
- Security: unauthenticated request → 401; `source` outside `INGEST_ROOT` → rejected;
  disallowed-origin CORS preflight → blocked.
