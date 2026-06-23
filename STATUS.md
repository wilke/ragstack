# RAGStack — Status

Persistent status across sessions and machines. Read this first to pick up where the project left off.

**Last updated:** 2026-06-03
**Current tag:** [`v0.2.0`](https://github.com/wilke/ragstack/releases/tag/v0.2.0) at `4d28ac5`
**Branch:** `main` (synced with `origin`)
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

## Active TODOs

### Near-term — pick up here in the next session

- [ ] Wire `QdrantVectorStore` into `python/ragstack/pipeline/ingestion.py` (factory + config wiring)
- [ ] Wire it into `python/ragstack/api/main.py` so the REST `/v1/ingest` and `/v1/query` endpoints actually use Qdrant
- [ ] Add a conformance test that proves the Qdrant-backed flow returns valid responses against `contracts/schemas/query_response.json`
- [ ] Replace the in-memory `InMemoryVectorStore` default once the API factory is in place

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

- `.env.example` still has `NEO4J_PASSWORD=neo4j` — invalid for Neo4j 5. The apptainer `up.sh` defaults to `ragstack` instead. Docker-compose users will hit this until `.env.example` is fixed.
- `vm.max_map_count` requires sudo on each new host. Not automatable in user-space.
- Embedding sidecar deps include CUDA libraries even on CPU-only hosts (sentence-transformers pulls torch + cuda). ~5 GB on disk; first-run install is slow.
- SSH push to GitHub requires `ssh-add` after agent restarts. HTTPS push via `gh auth setup-git` is the workaround used here.
