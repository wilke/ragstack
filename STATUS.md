# RAGStack — Status

Persistent status across sessions and machines. Read this first to pick up where the project left off.

**Last updated:** 2026-06-24
**Current tag:** [`v0.5.0`](https://github.com/wilke/ragstack/releases/tag/v0.5.0) at `284a344`
**Branch:** `main` (synced with `origin`). M1 ingest hardening (PR #4), M2 scalable ingestion (PR #5), and the multi-endpoint embedder pool (PR #8) merged; see the M1/M2/pool sections below.
**Deployed location (test+prod):** `/rag/` on host `coconut`. See [Production layout](#production-layout-rag) below.

## Where this fits

| Doc | Role |
|---|---|
| [SPEC.md](SPEC.md) | Architectural north star — data models, milestones, planned endpoints. Authoritative for *intent*. |
| [STATUS.md](STATUS.md) | **This file.** Current state, open TODOs, checkpoints, how to pick up. |
| [MEMORY.md](MEMORY.md) | Project rules, conventions, failures-and-fixes. Read before coding. |
| [CLAUDE.md](CLAUDE.md) | Operating instructions for Claude Code in this repo (commands, layout, working notes). |
| [scratchpad.md](scratchpad.md) | Append-only per-session notes — what changed, decisions, rationale. |

## What works today (end-to-end)

- **Monorepo scaffold**: Python (FastAPI, port `8000`) + Go (Chi, port `8080`) implementations of a shared OpenAPI 3.1 contract under `contracts/`.
- **Conformance suite**: HTTP black-box tests in `conformance/` runnable against either implementation via `RAGSTACK_BASE_URL`/`RAGSTACK_IMPL`.
- **Two deployment paths**:
  - Docker Compose (`make up-python` / `make up-go` / `make infra-up`).
  - **Apptainer rootless** (`make infra-up-apptainer`, `make sidecars-up-apptainer`) — preferred on hosts without Docker. Every writable container path is bind-mounted to a persistent host dir under `apptainer/data/`.
- **Infra stack**: Qdrant, Elasticsearch, Neo4j, Postgres, Redis — verified persistence across down/up cycles.
- **Embedding sidecar**: BAAI/bge-base-en-v1.5 (768-d, CPU) on `:50053`.
- **Qdrant integration**: `python/ragstack/stores/qdrant.py` implements the `VectorStore` protocol; CLI tools `python/scripts/{ingest_chunks,search}.py` provide round-trip ingest + semantic search with payload filtering.
- **Embedder abstraction**: `python/ragstack/embedders.py` supports both the local sidecar and any OpenAI-compatible endpoint (e.g. vLLM `--runner pooling`), selectable via `--embedding-api {sidecar,openai}`.
- **Multi-endpoint fan-out** (PR #8): `python/ragstack/embed_pool.py` load-balances embedding across several endpoints (e.g. vLLM replicas on the H200s) with least-loaded routing, a global concurrency cap, failover, and lazy health re-probing. Enabled via `EMBEDDING_ENDPOINTS`; both CLIs accept multiple `--embedding-url`. See the pool section below.
- **Functional REST API** (post-`9114fd1`): `/v1/ingest` runs the real load→chunk→embed→upsert pipeline against Qdrant; `/v1/retrieve` and `/v1/query` embed the query and return scored hits; `DELETE /v1/documents/{id}` removes a doc from Qdrant. `answer` from `/v1/query` is still a placeholder (LLM not yet wired). Validated: ingested a markdown file, retrieved with score 0.66, deleted, points went to 0.

## M1 ingest hardening (merged in `v0.4.0`, PR #4)

Shortest-path hardening of the PDF→chunk→embed→store→retrieve loop, from the multi-team plan in [`docs/m1-scalable-pdf-ingest-plan.md`](docs/m1-scalable-pdf-ingest-plan.md). 7 feature commits + 5 post-review fixes, 76 tests (73 pass, 3 skip — the skips are PDF tests needing the `pdf` extra) + a live integration pass against real Qdrant + the BGE sidecar. Merged to `main` via merge commit `03d549e`. What landed:

1. **Deterministic IDs** (`9bd3376`) — `loaders.py` doc IDs and `chunkers.py` chunk IDs are now `uuid5`-derived (was random `uuid4` at *both* layers), so re-ingesting a document overwrites in place instead of **silently duplicating the corpus** in Qdrant.
2. **PDF + loader registry + LFI confinement** (`16de832`) — `PdfLoader` (PyMuPDF, lazy `pdf` extra), `LoaderRegistry` dispatch by extension, and `INGEST_ROOT` confinement closing the arbitrary-file-read where `request.source` flowed into `open()`.
3. **Async ingest + JobStore** (`c73da0c`) — `/v1/ingest` returns `accepted` + a real `job_id` and runs in the background; `GET /v1/ingest/{job_id}` reports real status (accepted→running→completed/failed) from `jobstore.py` (in-memory or durable stdlib-sqlite).
4. **Bounded + poison-isolated embed** (`e14fe3b`) — `BatchingEmbedder` bounds request size by item/token budget and bisects a failing batch to quarantine a poison input (re-raising infra errors); `OpenAIEmbedder` now sorts response data by returned index.
5. **Dim reconciliation + (model,dim) collection scoping** (`5f95898`) — collections are auto-named `f(model,dim)`; `ensure_collection` hard-fails on a vector-size mismatch instead of writing mixed vectors. **The core protection for the "test different embedding models" workflow.**
6. **Loud non-durable fallback** (`9a78a5a`) — `require_durable_backends` makes a missing/unreachable Qdrant a fatal startup error instead of a silent degrade to in-memory.
7. **Security gate** (`27b810b`) — API-key auth on `/v1` (constant-time, `/health` open), CORS credentials no longer combined with the wildcard origin, and a production startup gate requiring `api_keys` + `ingest_root`.

**Live smoke test (coconut, prod-like config):** auth 401/200, async ingest→poll→completed, real BGE upsert (0→1 points), retrieval score 0.728, **re-ingest kept point count at 1 (idempotency proven)**, and `/etc/passwd` ingest rejected by `INGEST_ROOT`.

**Post-review hardening** (after the PR review + Copilot pass):

8. **SqliteJobStore connection leak** (`aa862c8`) — every op used `with conn:` (a transaction manager that commits but never closes), leaking a connection/fd per call on the durable path; now wrapped in `closing(...)`.
9. **Replace-on-reingest** (`3dbf9af`) — deterministic IDs only made a *byte-identical* re-ingest idempotent; an *edited* document chunks at shifted offsets (new chunk IDs) and the old chunks lingered as orphans. `pipeline.ingest` now deletes each doc's prior chunks (vector + text + graph) before upserting, after a successful embed so a transient failure can't destroy good data first.
10. **Reap interrupted jobs** (`3c0a96e`) — ingest runs as in-process background tasks, so a restart left durable jobs stuck `running` forever. `JobStore.fail_interrupted()` runs at startup and marks every non-terminal job `failed`/`interrupted`.
11. **Defensive dim check** (`d2ed334`, Copilot note) — `_existing_vector_size` walks the Qdrant config via `getattr` so an unexpected shape skips the optional check instead of raising `AttributeError` and hard-failing startup.
12. **Empty re-ingest no longer wipes data** (`b3b614e`, Copilot note) — the replace step in #9 deleted a document's prior chunks unconditionally; a re-ingest yielding no embeddable chunks (empty doc or all-quarantined) destroyed the prior version and upserted nothing. `pipeline.ingest` now raises `EmptyIngestError` before the delete phase, so the prior corpus survives and the job records `failed`.

Still open in M1: tenant isolation (server-side `tenant_id`). Conformance HTTP tests against the live flow remain a near-term TODO. **Residual on #9:** a crash *between* the deletes and the upsert leaves that one document empty until the next re-ingest — atomic replace needs Qdrant delete+upsert in one batch or the M2 job-resumability work; tracked for M2. **Caveat on #10:** `fail_interrupted()` reaps *all* non-terminal jobs at startup; under the durable sqlite store with **multiple uvicorn workers** a (re)starting worker would mark another worker's legitimately-running jobs failed. Fine for the current single-process model; needs a worker/lease guard before multi-worker.

## M2 scalable ingestion (merged in `v0.5.0`, PR #5)

Resumable 1→500k ingestion on a durable checkpoint, per the plan in [`docs/m1-scalable-pdf-ingest-plan.md`](docs/m1-scalable-pdf-ingest-plan.md) (M2 section). Merged to `main` via merge commit `284a344`. What landed:

1. **Sharded-ingestion seam** (`458a428`) — `manifest.py` (`build_manifest` expands a file or directory into `WorkItem`s whose `item_id` == the loader's document id), `backends.py` (`IngestBackend` protocol + `LocalAsyncIORunner`: bounded asyncio concurrency, no broker; Parsl/GoWe/k8s slot in later), `sharded.py` (`ShardedIngestor` runs a manifest through the pipeline with per-item failure isolation).
2. **Per-item state + resumability** (`25c8cee`) — `JobStore` gains `add_items`/`mark_item`/`completed_item_ids`/`item_counts` (InMemory + Sqlite). `ShardedIngestor` with a job_store skips already-completed items and checkpoints each as it lands. **The resume mechanism works at the ingestor level but is not yet reachable through the API** (every `POST` mints a new job_id; no resume trigger) — see [#6](https://github.com/wilke/ragstack/issues/6).
3. **PostgresJobStore** (`9d001ea`) — multi-process checkpoint of record via asyncpg (lazy pool/schema); new `postgres` extra; selected by `JOB_STORE_BACKEND=postgres` + `postgres_dsn`. Verified live against Postgres 16.
4. **Batch/directory endpoint** (`075a8fb`) — `/v1/ingest` accepts a directory (recursive, `.pdf/.txt/.md`); `GET` reports per-document `items` counts (contract: optional `items` added to `IngestResponse`). A single file is a 1-item manifest, so one path serves both scales.

**Live smoke (coconut, Postgres job store):** directory of 3 docs → job `completed`, `items={total:3,completed:3}`, Qdrant 0→3 points, 3 `job_items` rows in Postgres, re-ingest kept 3 points (idempotent).

**Post-review fixes** (PR review + Copilot pass):

5. **Manifest root re-confinement** (`1d2d338`) — `build_manifest` confined only the top-level source; `rglob` follows symlinks, so a link inside the root escaping it got enumerated. Each file is now re-confined; escaping symlinks are skipped.
6. **Postgres-safe startup + clean shutdown** (`16f1067`) — `fail_interrupted()` is unscoped (marks *all* non-terminal jobs failed), so it's skipped for the multi-process `postgres` backend (memory/sqlite still reap); job store is closed on shutdown so the asyncpg pool doesn't leak.
7. **Status/back-compat correctness** (`c775e96`) — don't overwrite `chunk_ids` on a resume-skip; `_final_status()` treats leftover `pending` (with nothing completed) as `failed` so a wholesale-failed shard isn't reported `completed`; corrected the endpoint's over-claimed resume docstring.
8. **Quality cleanup** (`f0e0694`, `/simplify`) — deduped the sqlite/postgres stores' shared logic (`_prepare_job_update`, `_fold_status_counts`, `_JOB_UPDATE_COLUMNS`); moved `close()` and the Postgres `fail_interrupted` no-op onto the `JobStore` protocol/store (lifespan no longer branches on backend name); collapsed `_final_status` and `build_manifest`. No behavior change.

Still open in M2:
- **API-level resume wiring** — [#6](https://github.com/wilke/ragstack/issues/6): the resume mechanism exists but no endpoint/startup path triggers it, so a crashed batch re-embeds everything on re-submit.
- **Per-owner lease for `fail_interrupted` under Postgres** — [#7](https://github.com/wilke/ragstack/issues/7): the startup sweep is unsafe across workers and is currently disabled for Postgres, so crashed Postgres jobs aren't reaped until a lease/heartbeat scopes ownership.
- Off-request *resumable* manifest build for very large submits (today the build is off-request but in-memory).
- Multi-endpoint embedder pool — **landed in PR #8** (see the section below). Per-tenant concurrency quota still deferred.

## Multi-endpoint embedder pool (merged, PR #8)

The last item on the M2 work-list. `python/ragstack/embed_pool.py` — `PooledEmbedder` satisfies the `Embedder` protocol and drops in behind `BatchingEmbedder` exactly like a single embedder. Merged to `main` via merge commit `a4432ac`. What landed:

1. **Routing + backpressure + failover + health** — least-loaded selection across endpoints; a global semaphore caps total in-flight requests; a 5xx / network / **retriable-4xx (429·408·425)** failure fails over to another endpoint (5xx/network demote the endpoint, a busy 429 does not), while every other 4xx propagates unchanged so `BatchingEmbedder` still quarantines genuine bad input; endpoints are re-probed lazily every `health_interval` so a recovered one rejoins the rotation.
2. **Wiring + config** — `deps._build_embedder` picks the pool when `embedding_endpoints` has >1 URL, else the single `embedding_sidecar_url`; both wrapped in `BatchingEmbedder`. New config: `embedding_endpoints` (accepts comma-separated **or** JSON-array env input via `Annotated[..., NoDecode]`), `embedding_max_concurrency`, `embedding_health_path`. CLIs `ingest_chunks.py`/`search.py` take multiple `--embedding-url` (`nargs="+"`) so bulk ingestion fans out.

12 pool unit tests (routing, failover/demotion, all-fail→RuntimeError, 4xx-propagates, retriable-4xx-fails-over, 5xx-fails-over, backpressure cap, least-loaded distribution, end-to-end health recovery, interval gating, health probing); full suite **109 pass / 4 skip**; repo `ruff check .` clean.

**Post-review fixes** (`/review` + Copilot): retriable-4xx now fails over instead of being mis-quarantined; the health refresh moved *outside* the backpressure semaphore so a slow probe can't hold a permit; `e.response is not None` guard before reading `status_code`; configurable `embedding_health_path` for OpenAI/vLLM backends without `/health` under the embeddings base; test `AsyncClient`-leak fixture; also cleared 5 pre-existing repo-wide ruff errors.

**Still deferred:** per-tenant concurrency quota — needs a server-side `tenant_id`, which arrives with the open tenant-isolation work. The global cap lands here.

## Active TODOs

### Near-term — pick up here in the next session

- [x] ~~Wire `QdrantVectorStore` into `IngestionPipeline` + `api/main.py`~~ — done in `9114fd1`. `python/ragstack/api/deps.py` provides the lifespan + factory; routers depend on `get_pipeline`/`get_vector_store`/`get_embedder`. Qdrant is the default backend.
- [ ] Add conformance tests that exercise the live Qdrant-backed flow against the JSON schemas (`/v1/ingest`, `/v1/retrieve`, `/v1/query`, `DELETE /v1/documents/{id}`). The schemas pass for our shapes (manually verified), but no automated coverage yet.
- [ ] Wire an LLM into `/v1/query` so `answer` stops being a placeholder. Easiest path is another OpenAI-compatible URL (vLLM serving Llama 3.x), reusing the embedder-style abstraction.
- [ ] Implement `GET /v1/documents` — needs a metadata store (Postgres) since the vector store only knows about chunks. Currently stub returns `[]`.

### Medium-term

- [ ] Cross-encoder reranker apptainer sidecar — mirror the embedding sidecar pattern in `apptainer/sidecars-up.sh`
- [ ] Elasticsearch `TextIndex` adapter (`python/ragstack/stores/elasticsearch.py`) — paralleling the Qdrant one
- [ ] Apptainer wrapper for the Python API itself (currently only available via `deploy/docker-compose.python.yml`)
- [ ] Bring vLLM serving SFR-Embedding-Mistral up on a GPU and run the embed-vs-BGE benchmark against representative chunks

### Long-term (per [SPEC.md](SPEC.md) milestones)

- [ ] **M4 — Graph**: KG extractor, Neo4j adapter (`GraphStore` protocol), graph-augmented retrieval
- [ ] **M5 — Intelligence**: Query rewriters (HyDE, multi-query, step-back, entity expansion), cross-encoder reranking in the pipeline, hybrid retrieval with RRF
- [ ] **M6 — API & Auth**: API-key auth, rate limiting, streaming responses
- [ ] **M7 — Observability**: Prometheus metrics, OpenTelemetry tracing, Grafana dashboards
- [ ] **M8 — Production**: Helm chart, horizontal scaling, load testing, runbook

## Checkpoints (tagged)

| Tag | Commit | Date | What landed |
|---|---|---|---|
| [`v0.1.0`](https://github.com/wilke/ragstack/releases/tag/v0.1.0) | `71ac896` | 2026-05-11 | CLAUDE.md + Apptainer Docker-free infra stack with persistent host binds |
| [`v0.2.0`](https://github.com/wilke/ragstack/releases/tag/v0.2.0) | `4d28ac5` | 2026-06-03 | Qdrant adapter + ingest/search CLIs + embedder abstraction (sidecar/openai) + embedding sidecar wrapper |
| [`v0.3.0`](https://github.com/wilke/ragstack/releases/tag/v0.3.0) | `435b81c` | 2026-06-24 | Functional REST API — Qdrant wired into the FastAPI app (`/v1/ingest`, `/v1/retrieve`, `/v1/query`, `DELETE`) |
| [`v0.4.0`](https://github.com/wilke/ragstack/releases/tag/v0.4.0) | `03d549e` | 2026-06-24 | M1 ingest hardening (PR #4) — deterministic IDs, PDF + LFI confinement, async ingest/JobStore, bounded+poison-isolated embed, (model,dim) collection scoping, durable-backend gate, API-key auth |
| [`v0.5.0`](https://github.com/wilke/ragstack/releases/tag/v0.5.0) | `284a344` | 2026-06-24 | M2 scalable ingestion (PR #5) — sharded-ingestion seam (manifest + IngestBackend + runner), per-item resumable checkpoint, PostgresJobStore, batch/directory ingest with per-item counts |

## Production layout (`/rag/`)

This host (`coconut`) runs the canonical deployed stack out of `/rag/`. Dev work still happens in a regular checkout (e.g. `~/Development/ragstack`); `/rag/` is the operating environment.

```
/rag/
├── repos/ragstack/      # git checkout — code is single-source-of-truth here
├── apptainer/images/    # SIFs (qdrant.sif, elasticsearch.sif, neo4j.sif, postgres.sif, redis.sif, python.sif)
├── data/                # all service persistence (qdrant/, elasticsearch/, neo4j/, postgres/, redis/, embedding/)
├── documents/           # input corpus (PDFs, derived chunks JSON)
├── config/rag.env       # env file: RAG_DATA, RAG_IMAGES, RAG_REPO, RAG_ENV, NEO4J_PASSWORD
├── envs/ragstack/       # shared conda env (path-based, multi-user) — Python 3.12 + ragstack[vector]
├── backups/             # DB snapshots (manual today; cron-driven later)
└── bin/
    ├── rag              # operator wrapper — sources rag.env, forwards to make
    └── activate         # sourceable — sets env vars + activates conda env
```

**The apptainer scripts in `repos/ragstack/apptainer/` honour `RAG_DATA` and `RAG_IMAGES` from the environment**, defaulting to in-repo paths when unset. The wrapper at `/rag/bin/rag` exports them by sourcing `config/rag.env`, so `apptainer instance` paths land under `/rag/`.

### Daily use

```bash
# admin/maintainer shell: activate everything in one shot
. /rag/bin/activate
# now: python, pip, ragstack package, env vars, conda env all set
cd $RAG_REPO/python
python scripts/ingest_chunks.py /rag/documents/chunks.json --collection my_corpus

# operator: start/stop services from any cwd
/rag/bin/rag infra-up-apptainer
/rag/bin/rag sidecars-up-apptainer
/rag/bin/rag infra-down-apptainer && /rag/bin/rag sidecars-down-apptainer
```

### Source-of-truth rules

- **Code**: `/rag/repos/ragstack/` is a normal git checkout; pull from `origin` to update. Hot-fix locally + push if needed.
- **Service data**: `/rag/data/<service>/` — owned by apptainer instances, do not edit while services are running.
- **Documents to ingest**: drop them in `/rag/documents/` so they aren't tied to a user's `$HOME`.
- **Secrets / config overrides**: `/rag/config/` — never commit anything from here into the repo.

## How to pick up (new session, possibly new machine)

1. **Clone and inspect**
   ```bash
   git clone https://github.com/wilke/ragstack.git
   cd ragstack
   git checkout v0.2.0           # or main for tip
   ```
2. **Read in this order**: `CLAUDE.md` → `MEMORY.md` → this file → recent `scratchpad.md` entries.
3. **Set up the Python env**
   ```bash
   conda create -n ragstack python=3.12 -y
   conda activate ragstack
   cd python && pip install -e ".[vector]"
   ```
4. **Bring infra + embedding sidecar up** (Apptainer path; no Docker required)
   ```bash
   sudo sysctl -w vm.max_map_count=262144     # one-time, for Elasticsearch
   make infra-pull-apptainer                  # ~1 GB images, one-time
   make sidecars-pull-apptainer               # python base SIF, one-time
   make infra-up-apptainer
   make sidecars-up-apptainer                 # ~5 GB deps install on first run
   ```
5. **Smoke-test the Qdrant pipeline**
   ```bash
   cd python
   python scripts/ingest_chunks.py scripts/example_chunks.json --collection demo
   python scripts/search.py "what is HNSW" --collection demo
   ```
6. **For the next chunk of work**, see "Near-term TODOs" above and the most recent `scratchpad.md` session entry.

## Known issues / friction

- **Collection naming changed** (`v0.4.0`): the API now scopes Qdrant collections to `(model, dim)` (e.g. `ragstack_baai_bge_base_en_v1_5_768_<hash>`), so data in the old literal `ragstack` collection is invisible to the API. Re-ingest, or pin `QDRANT_COLLECTION`. The CLI tools (`scripts/`) still use the literal `--collection` name.
- **Shared conda env (`ragstack`) — runtime extras present, lint tooling not**: `pytest`/`pytest-asyncio`/`pytest-cov` are installed so `make test-python` runs, and the `pdf` (PyMuPDF 1.27) + `postgres` (asyncpg 0.31) runtime extras are present — so PDF ingest and the Postgres job store both work, and the PDF loader tests pass (only the live Postgres integration test still skips, needing a reachable `TEST_PG_DSN`). Deliberately *not* run as `pip install -e ".[all,dev]"` — that would re-resolve pinned runtime deps (qdrant-client/fastapi) in an env that also backs the deployed stack. **`ruff` is now installed in this env** (PR #8 session), so `ruff check .` runs and is clean repo-wide; **`mypy` is still missing**, so the full `make lint-python` (which chains `ruff && mypy`) can't complete. A dedicated dev venv is the clean long-term home for the type-check tooling.
- `.env.example` still has `NEO4J_PASSWORD=neo4j` — invalid for Neo4j 5. The apptainer `up.sh` defaults to `ragstack` instead. Docker-compose users will hit this until `.env.example` is fixed.
- `vm.max_map_count` requires sudo on each new host. Not automatable in user-space.
- Embedding sidecar deps include CUDA libraries even on CPU-only hosts (sentence-transformers pulls torch + cuda). ~5 GB on disk; first-run install is slow.
- SSH push to GitHub requires `ssh-add` after agent restarts. HTTPS push via `gh auth setup-git` is the workaround used here.
