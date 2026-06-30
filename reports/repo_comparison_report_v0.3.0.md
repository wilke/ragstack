# RAG Repo Comparison v0.3.0: ragstack vs siblings

> **Supersedes the v0.2.0-baselined report.** The structural change driving this re-baseline: **Qdrant is now WIRED as the default backend** and `POST /v1/ingest` + `POST /v1/retrieve` + `DELETE /v1/documents/{id}` run **end-to-end against live Qdrant**. The `/v1/query` answer is **still the literal `[LLM not yet wired]` placeholder** — generation is now the single missing link in an otherwise-working retrieve path.

---

## What changed since the v0.2.0 baseline

Read this first. Only the **ragstack side** moved; every sibling fact is unchanged.

**Conclusions that MOVED (all driven by Qdrant going from CLI-only/InMemory-default → wired default):**

- **Vector search is now a ragstack strength, not a gap.** Overlaps flipped:
  - vs **rag_api**: "Vector similarity search wired end-to-end" complementary → **ragstack-better**.
  - vs **embedding_app**: "Vector indexing" complementary → **ragstack-favored** (Qdrant beats row-position Faiss).
  - vs **GoWe**: the "multi-backend execution abstraction: Qdrant reachable only via CLI" missing-claim is now **FALSE** and dropped.
- **The Faiss/durable-vector-store rationale weakened.** `distllm` FaissIndexV2 and `embedding_app`'s Faiss recipe **downgrade** from "fills the real vector gap" → "optional alternative backend, only if the planned Faiss sidecar is built." `embedding_app`'s Arrow on-disk store de-urgentized (Qdrant payloads now carry chunk text+metadata durably).
- **Graceful degradation reframed:** InMemory stores are now an **ImportError fallback**, not the API default.
- **Token-budget embedding batching ESCALATED** from latent risk → **active operational fix**: `POST /v1/ingest` now runs `embedder.embed → Qdrant upsert` live and synchronously, so an unbounded single `/v1/embeddings` call (ragstack's `OpenAIEmbedder.embed`) will actually fail on a large document in production.
- **Checkpoint/resume, memory-bounded streaming, and fail-soft skip-audit SHARPENED** (ExaForge, distllm, embedding_app): ingest now does real embedding + Qdrant writes per request with no resume, no memory bound, and `GET /v1/ingest/{job_id}` still returns `'unknown'`.
- **The conformance suite is now STALE** vs the wired flow: `test_ingest.py` asserts `status=='accepted'` (code returns `'completed'`) and posts `/tmp/test.txt` expecting 200 (live pipeline 404s). This slightly tempers ragstack's engineering-rigor edge (GoWe's verified CONFORMANCE.md now clearly leads on that one axis) and is a near-term TODO.
- **Durable document/job registry gap got MORE acute, not less:** real chunks now sit in live Qdrant, yet `GET /v1/documents` returns `[]` and there's no Postgres registry to map chunks → source documents/jobs.

**Conclusions that did NOT move:**

- **LLM generation is still the #1 gap** (distllm, ExaForge fill it best). No LLM client module exists anywhere; rewriters reference a non-existent `llm_client.complete()`.
- Semantic chunking still missing (only `RecursiveCharacterChunker` wired).
- Hybrid retrieval / RRF fusion / cross-encoder rerank still **coded-but-unwired** (`retrieval/retriever.py`, `scoring/scorers.py`).
- Sparse/lexical retrieval still a Jaccard placeholder (`InMemoryTextIndex`); ES unbuilt.
- No auth, no eval harness, no async/distributed ingestion, no multi-corpus registry.

---

## TL;DR

1. **ragstack closed its vector gap.** Ingest → chunk → embed → Qdrant-upsert → retrieve runs end-to-end against live Qdrant; vector search now beats or matches every sibling. The siblings no longer fill a "real vector store" gap.
2. **Generation is the only thing between ragstack and a full RAG loop.** Lift **distllm's `LLMGenerator` protocol + a LangChain/vLLM backend + `RagGenerator`** (or **ExaForge's `client.py`/`endpoints.py`**) onto the already-working `Source[]` retrieval. Highest value, medium effort.
3. **Two gaps got teeth because ingest is now a live write path:** (a) **token-budget batching** behind the Embedder protocol (embedding_app/distllm) — a present correctness/scale fix for `OpenAIEmbedder`'s unbounded call; (b) **checkpoint/resume + memory-bounded streaming + typed skip-audit** (ExaForge `CheckpointManager`/`ItemSkipped`) to make synchronous ingest resumable and bounded.
4. **The persistence gap is now the most actionable near-term TODO:** real chunks exist in Qdrant but `GET /v1/documents` and `GET /v1/ingest/{job_id}` are stubs. **GoWe's pure-Go SQLite Store + migrations** (Go side) and the **Postgres registry** ragstack already configured are the path; rag_api's data-driven corpus registry is the multi-corpus extension.
5. **Conformance is stale and must be fixed.** It asserts `'accepted'`/expects 200 where the wired code returns `'completed'`/404 and never exercises the live Qdrant path. Cheap, high-leverage, and it's the one axis where a sibling (GoWe) now clearly leads.

---

## Per-repo verdict

### embedding_app — BV-BRC embedding / RagDB module
**What it is:** Untested batch-CLI research tool for embedding corpora; has a known `'embedding'` vs `'embeddings'` schema-key bug and a stubbed folder-mode TF-IDF path.

- **Useful (adopt working code):** `get_embeddings_from_endpoint` (`lib/embedding_utils.py:183`) — **token-budget batching loop + re-sort by returned `index` field**; near drop-in behind ragstack's Embedder protocol, and the index-sort guard prevents silent embedding/text misalignment (**low**). `semantic_chunk_text` (`lib/semantic_chunking.py:127`) — self-contained numpy+NLTK semantic chunker with injected `embed_fn`, MIT-derived (**medium**). `chunk_text` registry with `-1` no-chunk sentinel + uniform generator signature (**low**).
- **Useful (borrow design):** fail-soft `(results, errors)` ingestion with per-chunk doc_id/chunk_index/status (`embed_document:286`) — now directly applicable to a meaningful `GET /v1/ingest/{job_id}` (**medium**); JSONL field-alternatives validation (**low**).
- **Missing-it-fills:** token-budget batching (now a live gap); semantic chunking; TF-IDF persisted sparse baseline (partial — TF-IDF ≠ BM25); per-chunk error accounting for the job registry.
- **Overlap:** Vector indexing now **ragstack-favored** (Qdrant > row-position Faiss). Chunking and sparse retrieval favor the **sibling**. Ingestion orchestration and engineering rigor favor **ragstack**.
- **Verdict:** **Parts donor, not a component.** Lift the batching primitive and `semantic_chunk_text`; borrow the fail-soft pattern. Do NOT adopt its assembly (its own main path doesn't even use the batched client). The Faiss/durable-store rationale weakened now that Qdrant is live.

### distllm — ramanathanlab/distllm (PyPI v1.0.2, MIT)
**What it is:** Mature distributed embedding + RAG research stack with working LLM generation, FAISS, and an eval harness. Complementary to ragstack.

- **Useful (adopt working code):** `LLMGenerator` protocol + **vLLM / LangChain(OpenAI+Anthropic+Gemini) / HuggingFace** backends (`distllm/generate/generators/`) — **the highest-value lift in this whole report** (**medium**). `RagGenerator` synthesizer binding Retriever+LLMGenerator with a `retriever=None` no-RAG baseline (`rag/response_synthesizer.py`) — ragstack already has the retriever half (**medium**). `STRATEGIES` name→(Config,Impl) registry + discriminated-union config + `get_xxx()` factory (`embed/encoders/__init__.py`) (**low**). `BaseConfig.from_yaml/write_yaml` for reproducible runs (`utils.py`) (**low**). `registry.py` singleton model registry (**low**). `semantic_chunk.py` boundary-aware chunker (**medium**). Token-budget batching (**low**).
- **Useful (borrow design):** `FaissIndexV2` binary-quantize + oversample-rescore recipe (`rag/search.py`) — **downgraded** to "only if the planned Faiss sidecar is built" (**medium**); `EvaluationTask`/`EvalSuiteConfig` with abstention-aware precision as an eval-harness template (**medium**); file-granular skip-not-fail sharding from `distributed_embedding.py` (pattern, not the Parsl code — ragstack targets Celery) (**high**).
- **Missing-it-fills:** working LLM generation (#1); multiple LLM backends; distributed/async ingestion; RAG eval harness; YAML-reproducible runs.
- **Overlap:** Hybrid retrieval/fusion/rerank favors **ragstack** (coded, even if unwired — distllm has none). Pluggable-backend factory favors the **sibling**. Vector search and API surface now **complementary**. Engineering rigor favors **ragstack** (despite stale conformance).
- **Verdict:** **Best single source for the generation gap.** Integrate `LLMGenerator` + a backend + `RagGenerator` onto the live `Source[]` path; reconcile prompt/stream shape with ragstack's OpenAPI contract, do NOT adopt distllm's API surface. Ignore protein/ESM encoders and the Parsl/HPC layer.

### ExaForge — offline batch HPC inference (v0.1.0)
**What it is:** Domain-orthogonal sibling: offline embarrassingly-parallel inference vs ragstack's online serving. ~183 tests (respx-mocked), strict mypy, but no CI/contract, single language.

- **Useful (adopt working code):** `EndpointPool` + `InferenceClient` (`endpoints.py` + `client.py`) — **health-aware async pool over OpenAI-compatible backends** with global semaphore, round_robin/least_loaded, exponential-backoff retries, and errors-as-data `ChatResponse`; sits nearly drop-in behind ragstack's Embedder seam AND the not-yet-existent LLM client (**low**). `_extract_json` (`tasks/qa_generation.py`) — tolerant fenced/preambled JSON extraction, copy verbatim (**low**). `lustre.py atomic_write` (mkstemp+fsync+rename) (**low**).
- **Useful (borrow design):** `CheckpointManager` + two-phase `JsonlReader` (scan IDs → filter_pending → read_by_ids) + streaming batch `Orchestrator` — blueprint to make the wired one-shot `IngestionPipeline` resumable and memory-bounded (**medium**); `ItemSkipped` typed reject channel → per-reason skip files while marking done (**low**); discriminated-union-config-as-registry (`config.py`) (**medium**).
- **Missing-it-fills:** working LLM client; multi-endpoint health/LB/global throttle; checkpoint/resume; memory-bounded streaming; retry-with-backoff on inference; atomic/parallel-FS I/O; synthetic eval-set generation; typed skip/audit channel.
- **Overlap:** OpenAI-compatible client favors the **sibling** (ragstack is embedding-only, no retries/health/LB). Pipeline orchestration **complementary** (online retrieval vs offline batch). Test discipline favors **ragstack** (cross-language conformance + contract, though stale).
- **Verdict:** **Domain-orthogonal but the best donor for ragstack's resilience seams.** Lift `endpoints.py`+`client.py` for the structured multi-endpoint retrying LLM/embedding client; copy `_extract_json` and `atomic_write` verbatim; adopt the checkpoint + streaming-batch + `ItemSkipped` pattern. Two of these got STRONGER at v0.3.0 because ingest now writes to live Qdrant. Files: `/rag/repos/ExaForge/src/exaforge/{client,endpoints,checkpoint,orchestrator,lustre,config}.py`, `readers/jsonl.py`, `tasks/qa_generation.py`.

### rag_api — early internal retrieval prototype
**What it is:** ~1,200 LOC, no tests, no CI, wide-open CORS, committed plaintext API key, unbounded cache. Well behind ragstack's discipline — but it actually serves lexical + hybrid + multi-corpus paths end-to-end.

- **Useful (adopt working code):** **hand-rolled TF-IDF query encoding** from Arrow-stored vocab/idf, regex tokenizer (`\b\w\w+\b`), L2-normalize, faiss IndexFlatIP (`rag_service.py` `_load_tfidf_data`/`_encode_tfidf_query`) — a real lexical path ragstack could lift behind its `TextIndex` protocol without standing up Elasticsearch (**medium**, plus an offline artifact-build step not in the repo). Eager index preload returning `{loaded, skipped, failed}` (**low**). Composite cache key over artifact paths (**low**). Return query embedding in the response (**low**).
- **Useful (borrow design):** data-driven multi-corpus registry (Mongo `ragList`, one-name-to-many-configs fan-out + global re-rank) → back with ragstack's planned Postgres (**medium**); tri-state health aggregation (healthy/degraded/unhealthy) (**low**).
- **Missing-it-fills:** working sparse/lexical retrieval in the serving path; data-driven multi-corpus registry with runtime fan-out; heterogeneous dense+sparse merge that actually executes; document-LEVEL registry (downgraded — Qdrant payloads now cover row→text mapping).
- **Overlap:** Vector search now **ragstack-better** (flipped from complementary — Qdrant is the wired default). Embedding abstraction favors **ragstack**. Hybrid/multi-source **complementary** (ragstack coded-but-unwired; rag_api naive-but-running — caveat: no global top_k truncation → up to N_configs × top_k docs). Contracts/deployment/testing all favor **ragstack**.
- **Verdict:** **Concept donor for the lexical + multi-corpus gaps only.** Now sharper: ragstack writes to `InMemoryTextIndex` on every ingest but never reads it back for scoring — a real lexical index would have an immediate write path to plug into. Port the TF-IDF encoder behind the `TextIndex` protocol; borrow registry + eager-preload. Do NOT run it as a component.

### GoWe — CWL v1.2 workflow engine (Go)
**What it is:** Not a RAG component — zero functional overlap with retrieval/generation. Purely architectural donor, strong exactly where ragstack is weakest (orchestration, persistence, auth, Go maturity).

- **Useful (adopt working code):** pure-Go SQLite `Store` + idempotent additive migrations + single-writer WAL (`/rag/repos/GoWe/internal/store/`) — a zero-infra Go-side **document/job registry** mapping chunks-in-Qdrant back to documents/jobs, later swappable to Postgres behind the same interface (**medium**). Three-level declarative state machine with generic `canTransition[S comparable]` (`pkg/model/state.go`) — models ingest states queued→chunking→embedding→upserting→completed/failed (**low**). SSE-over-polling stream template (`internal/server/handler_sse.go`) (**low**). Executor registry behind a 4-method interface (`internal/executor/`) to generalize `deps.py`'s if/else backend selection (**low**).
- **Useful (borrow design):** capability/affinity-scored atomic `CheckoutTask` (`internal/store/sqlite.go:1783`, prestage=require/cache=prefer) for GPU-vs-CPU embedder routing — now concrete since embedding executes per ingest (**medium**); standard REST envelope `{status,request_id,timestamp,data,pagination,error}` (`internal/server/response.go`) — a CONTRACT change → flows contract→both impls→conformance (**medium**).
- **Missing-it-fills:** durable job/metadata persistence that works; async/distributed orchestration with retry/self-healing; broker-free pull worker model; multi-provider auth + per-task token delegation + rate limiting; multi-scheme file/artifact staging (S3/HTTP corpus ingestion — ragstack only reads local text/markdown).
- **Overlap:** Conformance-driven development moved **tie → sibling** (GoWe's verified 378/378 CONFORMANCE.md leads vs ragstack's stale suite). Go implementation maturity favors the **sibling** (ragstack Go is route stubs). Persistence **complementary** (ragstack vector persistence now real; metadata absent). HTTP layer, deployment, domain all **complementary**.
- **Verdict:** **Borrow concepts, never depend.** Case reinforced at v0.3.0: real documents now exist in Qdrant with no registry to list them, and real embedding+upsert runs synchronously with no retry/resume. Highest-value/lowest-effort: Executor registry pattern, state machine + SSE templates, and the SQLite Store for the Go-side registry. Caveat: GoWe persistence is single-node SQLite (single writer, max_open_conns=1) — fine as a pattern, not production-scale architecture.

---

## Cross-cutting themes

- **Generation is the universal #1 gap.** Three siblings have working LLM clients (distllm `LLMGenerator`, ExaForge `InferenceClient`); ragstack has a placeholder string and rewriters pointing at a nonexistent `llm_client`. This is the one change that converts the now-working retrieve path into a real RAG loop.
- **"Live ingest" turned latent risks into present bugs.** Unbounded embedding calls, no resume, no memory bound, no retry, no skip-audit were "future concerns" at v0.2.0. They now bite a real synchronous Qdrant write path. embedding_app, distllm, and ExaForge each solve these (batching, checkpointing, streaming, `ItemSkipped`).
- **Persistence is bifurcated:** vectors are now durable (Qdrant), but document/job *metadata* is entirely absent. Every sibling that tracks state (GoWe SQLite, rag_api Mongo registry, distllm per-file shard manifest, embedding_app `(results,errors)`) offers a piece of the Postgres registry ragstack configured-but-never-used.
- **Coded-but-unwired is ragstack's signature pattern.** `HybridRetriever`, `RRFScorer`, `CrossEncoderScorer`, `MultiQuery/HyDERewriter`, `InMemoryGraphStore` exist behind protocols but never run. rag_api and distllm show some equivalents (lexical fan-out, dense retrieval) actually executing — useful as wiring references.
- **ragstack still leads on contract rigor — with one new asterisk.** OpenAPI 3.1 + strict schemas + cross-language conformance beat every sibling, but the conformance suite is now stale against its own wired flow, and GoWe's verified CONFORMANCE.md is a model to match.
- **Config selection wants to go declarative.** distllm's `STRATEGIES` registry, ExaForge's discriminated-union-as-registry, and GoWe's Executor registry converge on replacing `deps.py`'s imperative `if vector_backend=='qdrant'` branching now that backend choice is a real runtime decision.

---

## Prioritized recommendations (re-ranked for v0.3.0)

| # | Idea | Adopt vs Borrow | Source repo / file | Fills which v0.3.0 gap | Effort | Priority |
|---|------|-----------------|--------------------|------------------------|--------|----------|
| 1 | `LLMGenerator` protocol + LangChain/vLLM backend + `RagGenerator` onto live `Source[]` | Adopt code | distllm `generate/generators/`, `rag/response_synthesizer.py` | `/v1/query` `[LLM not yet wired]` placeholder — the only missing RAG-loop link | Medium | **P0** |
| 2 | Fix the stale conformance suite to exercise the live Qdrant flow | Internal | conformance/`test_ingest.py` (asserts `'accepted'`/expects 200 vs `'completed'`/404) | No automated coverage of the wired ingest/retrieve path; only axis a sibling (GoWe) now leads | Low | **P0** |
| 3 | Token-budget embedding batching + re-sort by `index` behind Embedder protocol | Adopt code | embedding_app `embedding_utils.py:183`; distllm encoder stack | `OpenAIEmbedder.embed` unbounded single call now on live ingest→Qdrant path | Low | **P0** |
| 4 | Durable document/job registry (`GET /v1/documents`, `GET /v1/ingest/{job_id}`) | Adopt code (Go) / build (Py) | GoWe `internal/store/` SQLite + migrations; ragstack's configured Postgres | Real chunks in Qdrant but list-documents/ingest-status are stubs; Postgres unused | Medium | **P1** |
| 5 | Structured multi-endpoint, retrying LLM/embedding client (health, LB, backoff, errors-as-data) | Adopt code | ExaForge `client.py` + `endpoints.py` | Single-endpoint embedder, no retry/health/LB on live path; future LLM inherits gap | Low | **P1** |
| 6 | Checkpoint/resume + memory-bounded streaming batch + `ItemSkipped` audit | Borrow design | ExaForge `checkpoint.py`, `orchestrator.py`, `readers/jsonl.py`; distllm sharding; embedding_app `(results,errors)` | Synchronous ingest re-embeds/re-upserts whole corpus, holds it resident, can't skip bad docs | Medium | **P1** |
| 7 | Real lexical/sparse `TextIndex` (TF-IDF encoder) to replace Jaccard placeholder | Adopt code | rag_api `rag_service.py`; embedding_app `tfidf_embed.py` | `InMemoryTextIndex` written-on-ingest but never read; ES unbuilt; partial BM25 stand-in | Medium | **P1** |
| 8 | Wire the existing `HybridRetriever` + `RRFScorer` into the query path | Internal | ragstack `retrieval/retriever.py`, `scoring/scorers.py` (already coded) | Coded-but-unwired hybrid retrieval; rag_api/distllm show fan-out executing | Medium | **P1** |
| 9 | Semantic (embedding-similarity) chunker behind the Chunker protocol | Adopt code | distllm `embed/embedders/semantic_chunk.py`; embedding_app `semantic_chunking.py:127` | Only `RecursiveCharacterChunker` wired; chunk quality now affects live retrieval | Medium | **P2** |
| 10 | Declarative `STRATEGIES`/discriminated-union backend registry to replace `deps.py` if/else | Borrow design | distllm `embed/encoders/__init__.py`; ExaForge `config.py`; GoWe Executor registry | Imperative backend selection now a real runtime config decision | Low–Med | **P2** |
| 11 | `BaseConfig` YAML reproducibility (`from_yaml`/`write_yaml`, resolved-config write-back) | Adopt code | distllm `utils.py` | No per-run pipeline spec / reproducibility artifact | Low | **P2** |
| 12 | RAG eval / QA-benchmark harness (now a real retrieve path to score) | Borrow design | distllm `rag/tasks/` + `evaluate.py`; ExaForge `qa_generation.py` | Zero retrieval/answer-quality evaluation | Medium | **P2** |
| 13 | Data-driven multi-corpus registry + runtime fan-out + global re-rank | Borrow design | rag_api `database_manager.py`, `rag_service.search()` | Single fixed Qdrant collection; no multi-tenant/multi-corpus serving | Medium | **P2** |
| 14 | Singleton model registry for sidecar/worker reuse (LRU-1 + CUDA-clear) | Adopt code | distllm `registry.py` | Planned cross-encoder/embedder workers re-instantiate heavy models | Low | **P2** |
| 15 | Copy-verbatim primitives: tolerant JSON extraction; atomic write | Adopt code | ExaForge `_extract_json`, `lustre.atomic_write` | No tolerant model-output parsing; no atomic-persistence helper for checkpoints/metadata | Low | **P2** |
| 16 | Multi-scheme corpus staging (S3/HTTP) + auth/rate-limiting + REST envelope | Borrow design | GoWe `pkg/staging/`, `auth.go`, `response.go` | Loaders only read local text/markdown; no auth on a path that now writes/deletes; bare responses, no request tracing | Med–High | **P2** |

**Bottom line:** ragstack's vector gap is closed; the work re-centers on (P0) **generation**, **conformance-for-the-wired-flow**, and **embedding batching**, then (P1) the **document/job registry**, a **resilient inference client**, **resumable/bounded ingestion**, and **wiring the lexical + hybrid machinery that already exists**. Adopt working code from distllm (generation) and ExaForge (inference client, checkpointing); borrow design from GoWe (registry, orchestration, auth) and rag_api (lexical, multi-corpus).