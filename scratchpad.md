# Scratchpad — keen-newton worktree

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
