# Scratchpad — keen-newton worktree

## Session 2026-07-01 — semantic ingest: fan-out review + #66/#65 producer & checkpoint fixes

Reviewed and merged **PR #67** (semantic breakpoint-embed fan-out across the pool: 2.06×, all 8 GPUs
vs 1-pinned), then planned+implemented its two filed follow-ups. Planning ran a multi-agent workflow
(understand → design panel per issue → adversarial critic); the critic caught real defects that shaped
the final designs. Both shipped as stacked PRs off `main`.

- **#66 → PR #68** (`perf/ingest-pipeline-producer`): one line — `chunks = await asyncio.to_thread(chunker.chunk, doc)`
  in `ingest_jsonl.py`'s producer. `chunker.chunk()` ran synchronously on the main event loop and the
  semantic path further blocked on the embed bridge's `fut.result()`, starving the embed+upsert workers
  (bursty single-GPU). Awaiting off-thread lets workers drain during the split. **Single in-flight by
  construction** (producer awaits each before the next line) → exactly one caller in `SyncEmbedBridge`,
  so no bridge hardening needed and determinism/#65-frontier untouched. Phase 2 (`--chunk-concurrency`)
  deferred → [#71](https://github.com/wilke/ragstack/issues/71).
- **#65 → PR #70** (`fix/ingest-checkpoint-interval-set`, merged via #70 after the stacked #69 was
  auto-closed when its base #68 merged): the checkpoint advanced only over the contiguous completed-seq
  prefix, so a slow/failed early batch pinned the frontier at the head while later batches upserted out
  of order — every restart re-embedded the lot. Fix: persist **`done_ranges`** (coalesced `[lo,hi]` line
  intervals of above-gap completions); resume skips a line if `<= frontier OR in done_ranges`.

**Non-obvious decisions / critic catches:**
- **The naive skip predicate loses catalog + doc-metrics.** Catalog rows for above-gap batches are
  buffered in `completed[seq]` and only flushed when the frontier folds; a full skip on resume drops
  them permanently. Fix: scope the `done_ranges` skip to **chunk+embed+upsert only** — a resume-skipped
  doc still buffers its catalog row (folds in lockstep) and emits a `"resumed (already indexed)"`
  doc-metrics row. Catalog-only batches (buf empty, buf_catalog non-empty) required a flush-on-catalog-size
  guard + an `if buf or buf_catalog:` EOF flush.
- **Edited-input / `--replace` regression:** `done_ranges` assumes the input is byte-stable across
  restarts (same as the line frontier already does). Gated the skip OFF under `--replace` (which must
  reprocess to prune orphans) and documented the immutability assumption in the module header.
- **No-data-loss invariant preserved:** a *failed* seq is never unioned into `done_ranges`, so its lines
  are in neither set and always re-fed. `done_ranges` is pure optimization metadata — sanitizes to `[]`
  on corruption, degrading to redundant work, never loss. Legacy bare-int + `{line,doc_types}` load with
  `done_ranges=[]`. Also moved `failed.append(seq)` under the lock.
- **Prod tie-in:** the lambda `next-batch` (tok256/tok512) build was hitting exactly this #65 churn under
  lambda-endpoint flapping (checkpoint pinned at line 51, doc-metrics ≈ 2× the file). #70 stops the
  whole-file re-churn, but a *flapping* endpoint still exits non-zero — the convergence lever is the
  deferred `--batch-retries` in [#71](https://github.com/wilke/ragstack/issues/71). Prod must redeploy
  on `main` and restart lambda **without `--replace`**.
- **Env gotcha (again):** tests need the `ragstack` conda env (Python 3.12); the bare `pytest`/`make
  test-python` on PATH is miniconda 3.8 and fails at collection on `dict[str, Any]` annotations.

Closed **#65** and **#66** (linking #70/#68); filed **#71** for the deferred hardening. #67 merged earlier.

## Session 2026-06-29 — review→fix→merge cycle: hybrid (v0.9.0) + M5 intelligence (v0.10.0)

A long review-driven session: opened at `v0.8.0` with PRs #14/#15 pending; closed at `v0.10.0` with
M5's core complete. Pattern throughout: `/review` (multi-agent finders + adversarial verify) → fix
blockers on the branch → check Copilot's comments → merge → reconcile downstream. Every merge was
preceded by a conflict probe (detached test-merge); `/rag/envs/ragstack` is the env that has the deps
(the base `python` env does not).

**Shipped two releases.**
- **v0.9.0** (`2947414`) — Elasticsearch BM25 + hybrid retrieval. #14 jsonschema floor (`>=4.22` to match
  conformance), #15 `ElasticsearchTextIndex` + hybrid wiring, #16 hardening. Review/Copilot fixes:
  `bulk()` surfaces partial-failure errors; full metadata round-trips through ES (filters target
  `metadata.<key>` for parity with the vector store); `ensure_index` is idempotent (create-and-catch,
  not check-then-create — closed a TOCTOU); `_build_query` fails closed without a `tenant_id` filter.
- **v0.10.0** (`3679436`) — M5 intelligence + scholarly ingestion. #17 query rewriting (multiquery/hyde
  → concurrent retrieve via `asyncio.gather` → RRF), #18 mypy baseline clean, #19/#22 JSONL ingestion +
  enrichment, #20/#23 cross-encoder reranking, #21 parallel bulk ingester.

**Design decisions / non-obvious fixes:**
- **mypy baseline (11 errors) cleared via 4 parallel subagents** (one per file group). Split into a portable
  PR #18 (qdrant/loaders/backends — no in-flight work) merged to `main`, while deps.py (TypedDict for the
  embedder kwargs) + scorers.py (`CrossEncoder` type under `TYPE_CHECKING`) rode with the reranker branch.
  `backends.py`: widened `isinstance(res, Exception)` → `BaseException` (gather can return a non-Exception
  BaseException that would crash `.extend`).
- **ingest_jsonl orphan-delete under concurrency** (#21): the producer only flushes a batch on a *document
  boundary*, so a doc's chunks live entirely in one batch/one worker → deleting the batch's distinct doc
  ids before upsert is race-free without a lock, and preserves "delete after a successful embed".
- **Resume-filter footgun** (#19): the checkpoint persists the active `--doc-types`; a resume under a
  different filter fails closed (was silently skipping lines the looser filter would keep).
- **Catalog lockstep** (#22, reconciled onto #21): catalog rows are buffered per batch and written by the
  worker in seq order in lockstep with the checkpoint, so the catalog never outruns the resume point and
  nothing past a failed-batch gap is written.
- **Reranker model mismatch** (#20): the sidecar picks its own model from `MODEL_NAME`; apptainer and
  config defaulted to MiniLM while docker-compose/sidecar defaulted to bge-reranker-v2-m3. Aligned all
  paths + added a startup `/health` model check. Also: validate sidecar `indices` (range/uniqueness →
  degrade, not silently dup/drop); `top_k` added to the `Scorer` protocol so implementers are
  interchangeable; `top_k>=1` request validation.
- **PR #21 was cut from the pre-fix commit** and reverted #19's orphan/resume fixes — rebased onto `main`
  and re-applied them inside the new worker/checkpoint structure (this is why conflict-probing every merge
  mattered).

**Follow-ups deferred → tracked as issues #25–#28:** consolidate `ingest_jsonl.py` onto `IngestionPipeline`
(three hand-rolled copies of the loop now); make enrichment publisher-config-driven (ASM-specific constants);
per-request rerank opt-out; shared sidecar-client base (SidecarReranker/SidecarEmbedder dup HTTP boilerplate).

**Also:** #24 API reference (`docs/API.md`) reviewed + merged (flagged `GET /v1/documents` as a stub
returning `[]`); STATUS.md + README cross-link it. STATUS bumped with an M5 section + checkpoint rows.

---

## Session 2026-06-24 — M2 scalable ingestion (branch `feat/m2-shard-manifest`)

Built the resumable 1→500k ingestion backbone on top of v0.4.0. 4 commits, 94 unit/API tests +
a live batch smoke (real Qdrant + BGE sidecar + Postgres job store).

**Commits:** `458a428` sharding seam (manifest + IngestBackend + LocalAsyncIORunner + ShardedIngestor) ·
`25c8cee` per-item job state + resumable run (skip completed, checkpoint each) · `9d001ea` PostgresJobStore
(asyncpg, lazy pool) · `075a8fb` batch/directory `/v1/ingest` + `items` counts (contract change).

**Design decisions:**
- One code path for 1 doc and 500k: a single file is a 1-item manifest. The `IngestBackend` seam is
  where "single host now, cluster later" lives — `LocalAsyncIORunner` now, Parsl/GoWe/k8s later, same protocol.
- `WorkItem.item_id == loader's deterministic doc id`, so manifest ids, checkpoint ids, and stored
  document ids all coincide (resume + future KG re-run address the same id).
- Resumability is per job_id: `add_items` is idempotent (preserves prior progress), completed items are
  skipped, each outcome checkpointed as it lands. Postgres is the multi-process checkpoint of record
  (sqlite's single writer is the reason to move off it for 500k).
- Job status: `failed` only if the run errors or *every* item fails; partial failures stay `completed`
  with `items.failed > 0`. Single-doc `chunk_ids` kept for back-compat.
- Postgres integration test + the live smoke never call `fail_interrupted()` against the shared DB
  (it reaps ALL non-terminal jobs) and clean up their own rows.

**Live smoke:** dir of 3 docs → completed, items total/completed=3, Qdrant 0→3, 3 Postgres job_items,
re-ingest stayed at 3 (idempotent). Teardown clean.

**Still open in M2:** multi-endpoint EndpointPool (per-tenant quota); resumable (not just off-request)
manifest build for huge submits.

## Session 2026-06-24 — M1 ingest hardening (branch `feat/m1-deterministic-ids`)

Implemented the shortest-path M1 from the multi-team plan (`docs/m1-scalable-pdf-ingest-plan.md`):
robust, scalable PDF→chunk→embed→store→retrieve. 7 commits, 32→69 unit tests, plus a live
integration pass on coconut (real Qdrant + BGE sidecar).

**Commits (oldest→newest):** `9bd3376` deterministic IDs · `16de832` PdfLoader+LoaderRegistry+INGEST_ROOT ·
`c73da0c` async ingest + JobStore · `e14fe3b` bounded/poison-isolated embed · `5f95898` dim
reconciliation + (model,dim) collection scoping · `9a78a5a` loud non-durable fallback ·
`27b810b` API-key auth + CORS fix + prod gate.

**Key decisions / rationale:**
- The duplicate-corpus bug was **two layers**: random `uuid4` in both `loaders.py` (doc id) and
  `chunkers.py` (chunk id). Fixing only the chunker is insufficient — doc id must be deterministic too.
  Chosen keys: resolved path (TextFileLoader), content (StringLoader), `f"{doc.id}:{start}:{end}"` (chunk).
- Async ingest reconciled the *stale* `status=="accepted"` tests by making the behavior real (background
  task) rather than editing assertions. Fixed 3 pre-existing red API tests by adding `tests/api/conftest.py`
  that wires `app.state` with in-memory doubles (lifespan doesn't run under httpx ASGITransport).
- Poison isolation distinguishes 4xx (bad input → bisect & quarantine) from 5xx/network (infra → re-raise),
  so a backend outage never silently drops a corpus.
- Collections scoped to `(model,dim)` for A/B model isolation; `ensure_collection` hard-fails on size
  mismatch. `require_durable_backends` gates the vector store strictly; text index only warns (no ES yet).
- `B008` added to ruff ignore (FastAPI `Depends()`-in-default idiom, pre-existing across routers).

**Live smoke (prod-like config):** auth 401/200 · ingest→poll→completed · 0→1 Qdrant points · retrieve
score 0.728 · **re-ingest kept count at 1 (idempotency proven)** · `/etc/passwd` rejected by INGEST_ROOT.

**Still open in M1:** tenant isolation (`tenant_id`); conformance HTTP tests for the live flow. Next milestone
is M2 (shard/manifest + resumable 1→500k) per the plan.

## Current Session (2026-03-01)

### Completed Work

#### 1-5. Prior Work (plan-c5.md edits)
- Gap analysis, self-hosted model migration, Python/Go duality docs, Elasticsearch BM25, Apptainer deployment

#### 6. Monorepo Refactoring (this session)
Restructured repo for Go + Python parallel development:

**Files moved:**
- `ragstack/` → `python/ragstack/`, `tests/` → `python/tests/`, `pyproject.toml` → `python/pyproject.toml`, `docker/` → `python/docker/`
- Deleted root `docker-compose.yml` (replaced by `deploy/`)

**New directories created:**
- `contracts/` — OpenAPI 3.1 spec, 11 JSON schemas, test fixtures
- `conformance/` — 12 files: HTTP black-box tests (pytest+httpx), schema validation, helpers
- `sidecars/` — 3 Python microservices (crossencoder, embedding, faiss)
- `go/` — Phase 1 scaffold: Chi router, 5 handler files, config, 8 Go tests passing
- `deploy/` — Split Docker Compose: infra, go, python, sidecars
- Root `Makefile`, `.env.example`, updated `.gitignore`

**Verification:**
- Go: `go build` succeeds, 8/8 tests pass
- Python: files moved correctly (git tracks as renames)
- Conformance tests designed to run via RAGSTACK_BASE_URL

### Key Decisions
- Monorepo: python/, go/, sidecars/, contracts/, conformance/, deploy/ as peers
- Go: chi/v5 router, slog logging, google/uuid
- Conformance: HTTP-only black-box, no code imports
- JSON schemas: additionalProperties: false

### Files Modified
- `docs/plan-c5.md` — extensive edits (prior sessions)
- All files in monorepo restructuring (see above)

### Potential Next Steps
- Run conformance tests against both implementations
- Update plan-c5.md project structure to reflect monorepo
- Begin Phase 2 (Qdrant + embedding integration)

---

## Session 2026-06-03 — CLAUDE.md, Apptainer infra, Qdrant adapter

### Completed Work

#### 1. CLAUDE.md + Apptainer infra stack (tag `v0.1.0`, commit `71ac896`)
- `CLAUDE.md` — polyglot monorepo overview, contracts/conformance flow, port split, worktree convention
- `apptainer/{pull,up,down}.sh` — Docker-free infra stack mirroring `deploy/docker-compose.infra.yml`
- All five services bind explicit host dirs under `apptainer/data/<svc>/` for **every** writable path
  (data, logs, configs, sockets, snapshots) — no `--writable-tmpfs`
- ES + Neo4j config dirs seeded from image on first run (they write `elasticsearch.yml` /
  `neo4j.conf` at startup)
- Verified persistence: wrote markers in each service, down→up cycle, read them back identically
- `make infra-{pull,up,down}-apptainer` targets

#### 2. Qdrant adapter + ingest/search CLI (uncommitted on `main`, planned `v0.2.0`)
- `python/ragstack/stores/qdrant.py` — `QdrantVectorStore` implements VectorStore protocol;
  UUID5-hashed point IDs so re-ingest overwrites; original chunk ID preserved in payload
- `python/scripts/ingest_chunks.py` — flatten doc-level metadata onto every chunk, batch
  embed → upsert. Auto-detects vector dim from first embed result, sizes collection
- `python/scripts/search.py` — embed query → `query_points` → text or `--json` output, `--filter k=v` repeatable
- `python/scripts/example_chunks.json` — 2 docs, 5 chunks, demonstrates doc-level metadata pattern

#### 3. Embedder abstraction with vLLM support
- `python/ragstack/embedders.py` — `SidecarEmbedder` (`POST /embed`) + `OpenAIEmbedder`
  (`POST /v1/embeddings`) + `make_embedder()` factory
- Scripts gain `--embedding-api {sidecar,openai}`, `--embedding-model`, pick up `OPENAI_API_KEY` env
- vLLM (`vllm serve <model> --runner pooling`) is just another `--embedding-api openai` endpoint

#### 4. Apptainer embedding sidecar wrapper
- `apptainer/sidecars-{pull,up,down}.sh` — one shared `python.sif` + per-sidecar host-bound `deps/` and `cache/`
- `pip install --target` into `apptainer/data/embedding/deps/` on first `up.sh` (5.1 GB: torch + cuda libs + sentence-transformers)
- HF cache bound at `apptainer/data/embedding/cache/` so BGE download persists
- Run via `python -m uvicorn` (PYTHONPATH=/deps), not the console script (relocated-install shebang issues)
- `make sidecars-{pull,up,down}-apptainer` targets

#### 5. End-to-end validation
- Conda env `ragstack` (Python 3.12.13), `pip install -e ".[vector]"`
- Ingested example_chunks.json: 5 chunks, BGE 768-d, collection `ragstack_demo`
- Search "what is HNSW" → top hit is the Qdrant chunk (score 0.53); filter `tags=deployment` works; "reranking pipeline" → the RRF chunk (0.63)

### Key Decisions
- **Persistence model**: explicit per-path host bind mounts > `--writable-tmpfs` overlay.
  More verbose but state is observable on host and easy to back up
- **Vector ID strategy**: UUID5(chunk_id) — Qdrant requires UUID/int, this is deterministic
- **Embedder protocol**: same async signature for sidecar and OpenAI flavors, switched by CLI flag.
  Default stays `sidecar` so existing callers don't break
- **Apptainer rootless tradeoffs**: no `--cwd` flag (wrapped qdrant CMD in `sh -c 'cd /qdrant && exec'`),
  `--env` shell-sources (ES dotted keys go via `-E key=value` CLI args instead), no PID-1 tini
  (skip it, accept the warning)
- **NEO4J_PASSWORD**: default changed to `ragstack` in apptainer `up.sh`. Neo4j 5 rejects literal `neo4j`.
  `.env.example` still says `neo4j` — also broken for the docker-compose path but out of scope

### Files Modified
- New: `CLAUDE.md`, `apptainer/{pull,up,down,sidecars-pull,sidecars-up,sidecars-down}.sh`,
  `python/ragstack/embedders.py`, `python/ragstack/stores/qdrant.py`,
  `python/scripts/{ingest_chunks.py,search.py,example_chunks.json}`
- Modified: `Makefile` (6 new apptainer targets), `python/ragstack/stores/__init__.py`
  (export QdrantVectorStore behind try/import), `.gitignore` (apptainer artifacts, `*.rdb`)

### Potential Next Steps
- Wire `QdrantVectorStore` into `IngestionPipeline` and `api/main.py` factory
- Cross-encoder reranker apptainer sidecar (parallel to embedding)
- Elasticsearch BM25 store adapter + `TextIndex` impl
- Add a `--embedding-api` autodetect (HEAD probe?) so users don't have to remember the flag
- Bring vLLM up properly (SFR-Embedding-Mistral on H200) and benchmark vs BGE for the workload
