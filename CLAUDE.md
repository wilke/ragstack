# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Documentation map

Read in this order when picking up the repo cold:

| Doc | When to read |
|---|---|
| [STATUS.md](STATUS.md) | **First.** Current capabilities, active TODOs by horizon, tagged checkpoints, onboarding steps. |
| [MEMORY.md](MEMORY.md) | **Before coding.** Project conventions, hardware constraints, and the list of debugged failures (apptainer rootless quirks, Neo4j 5 password rule, qdrant-client API rename, etc.). |
| [SPEC.md](SPEC.md) | Architectural north star — data models, milestones, planned endpoints. Authoritative for design *intent*. |
| [scratchpad.md](scratchpad.md) | Per-session change log — most recent entry has the latest decisions and rationale. |

When in doubt about *intended* design vs. current code, SPEC wins for intent and code wins for current reality.

## Repository shape

RAGStack is a **polyglot monorepo** with two parallel implementations of the same RAG API plus a shared contract and conformance suite. The peer top-level dirs are:

- `python/` — Python/FastAPI implementation (default port `8000`)
- `go/` — Go/Chi implementation (default port `8080`)
- `contracts/` — `openapi.yaml` (OpenAPI 3.1) + JSON schemas in `contracts/schemas/` (`additionalProperties: false`) + request/response fixtures. **Source of truth** for the API surface.
- `conformance/` — pytest+httpx black-box tests run over HTTP against a running server. No imports from `python/` or `go/` — selected by `RAGSTACK_BASE_URL` and `RAGSTACK_IMPL` env vars.
- `sidecars/` — three Python microservices (`embedding/`, `crossencoder/`, `faiss/`) that both implementations call as model backends.
- `deploy/` — Docker Compose split into layered files: `docker-compose.infra.yml` (Qdrant/ES/Neo4j/Postgres/Redis), `docker-compose.sidecars.yml`, and `docker-compose.{python,go}.yml` for the API. They are **stacked with multiple `-f` flags**, not used standalone.

When changing the API: update `contracts/openapi.yaml` + the relevant schema in `contracts/schemas/` first, then implement in both `python/` and `go/`, then verify with conformance against each.

## Common commands

All targets live in the root `Makefile` and `cd` into the right subdir for you. Prefer them over running tools directly so working-directory assumptions stay correct.

```bash
make install-python              # pip install -e ".[all,dev]" inside python/
make test-python                 # python/ pytest unit + API tests
make perf-python                 # perf budget tests (excluded from test-python)
make lint-python                 # ruff check . && mypy ragstack/  (inside python/)
make run-python                  # uvicorn ragstack.api.main:app --reload --port 8000

make build-go                    # go build -o bin/api ./cmd/api  (inside go/)
make test-go                     # go test ./... -v
make lint-go                     # golangci-lint run ./...
make run-go                      # build + run binary

make infra-up / make infra-down  # bring up/down the shared infra stack (Docker)
make up-python / make up-go      # infra + sidecars + chosen API, all in Docker
make down                        # stop everything (combines all compose files)

make infra-{pull,up,down}-apptainer       # Docker-free infra via Apptainer (preferred on no-Docker hosts)
make sidecars-{pull,up,down}-apptainer    # Embedding sidecar via Apptainer

make test-conformance-python     # RAGSTACK_BASE_URL=http://localhost:8000 RAGSTACK_IMPL=python pytest conformance/
make test-conformance-go         # RAGSTACK_BASE_URL=http://localhost:8080 RAGSTACK_IMPL=go pytest conformance/
make test-conformance            # both, sequentially
```

Conformance tests assume a server is **already running** at the URL — they don't start one. Bring it up with `make up-python` / `make up-go` (or `run-python` / `run-go` for non-Docker dev) first.

To run a single test: `cd python && pytest tests/path/to/test_x.py::test_name -v` (or the equivalent `go test -run` in `go/`). To run a single conformance test, prefix with the same `RAGSTACK_BASE_URL`/`RAGSTACK_IMPL` env vars the Make target uses.

## Implementation layout

**Python (`python/ragstack/`)** — protocol-driven; `protocols.py` defines the interfaces (`DocumentLoader`, `Chunker`, `Embedder`, `Scorer`, …) that concrete classes in `ingestion/`, `retrieval/`, `rewriting/`, `scoring/`, `graph/`, `stores/` satisfy. `pipeline/` orchestrates them; `api/` is the FastAPI surface.

**Go (`go/internal/`)** — `api/` holds the Chi router and per-resource handler files (`handler_query.go`, etc.); `config/` reads env; `platform/` has shared errors; `observability/` for logging. Note this is **Phase 1 scaffold** — many handlers may return stubs while Python is the more complete implementation.

**Sidecars** are independent FastAPI apps; the API talks to them over HTTP at `EMBEDDING_SIDECAR_URL`, `CROSSENCODER_SIDECAR_URL`, `FAISS_SIDECAR_URL` (see `.env.example`).

## Python utilities (post-v0.2.0)

- `python/ragstack/stores/qdrant.py` — `QdrantVectorStore` implementing the `VectorStore` protocol. Use `query_points()` (the `search()` method was removed in qdrant-client ≥ 1.10).
- `python/ragstack/embedders.py` — `SidecarEmbedder` (for the `:50053` BGE service) and `OpenAIEmbedder` (for any `/v1/embeddings` endpoint including vLLM). Pick via `make_embedder(api="sidecar"|"openai", ...)`.
- `python/scripts/ingest_chunks.py` and `search.py` — CLI tools that exercise the above end-to-end against a JSON file of pre-extracted chunks (see `example_chunks.json` for the doc-level-metadata shape). Both expose `--embedding-api {sidecar,openai}` and `--embedding-model` so vLLM is one flag away.

These aren't wired into the API yet — `api/main.py` still uses `InMemoryVectorStore`. Wiring is the top near-term TODO in [STATUS.md](STATUS.md).

## Apptainer deployment

The `apptainer/` scripts are the **preferred** path on hosts without Docker (and on the dev host `coconut`). Each container's writable directories are bind-mounted to host paths under `apptainer/data/<service>/<purpose>/` — every writable path is enumerated explicitly so state persists across restarts and is observable from the host. **Do not use `--writable-tmpfs` or opaque overlays** when adding a new service; enumerate the writable paths instead. See [MEMORY.md](MEMORY.md) for the catalog of apptainer rootless quirks (no `--cwd` flag, `--env` shell-sourcing breaks dotted keys, image FS read-only by default, etc.).

The scripts honour `RAG_DATA` and `RAG_IMAGES` env vars (defaulting to `$HERE/data` and `$HERE/images` in-repo). The deployed `/rag/` layout sets those to `/rag/data` and `/rag/apptainer/images` via `/rag/config/rag.env` — so a checkout under `/rag/repos/ragstack/` and a checkout under `~/Development/ragstack/` can coexist without stepping on each other's state. See [STATUS.md § Production layout](STATUS.md#production-layout-rag) for the full layout.

## Working notes

- Worktrees and subagents: place git worktrees and subagent isolation directories under `~/Development/worktrees/` (not next to this repo, not in `/tmp`).
- Port convention: Python = 8000, Go = 8080. Don't swap them — the conformance Make targets and `.env.example` (`PORT=8080`) hardcode this split.
- Both implementations must conform to the same JSON schemas; if a field name differs between them, the schema/OpenAPI is authoritative and the diverging side is the bug.
- **Before adding a gotcha workaround**, check [MEMORY.md](MEMORY.md) — it may already be documented. After spending >15 min debugging a non-obvious failure, add an entry there.
- **Pushing**: this repo's `origin` is HTTPS (set via `gh auth setup-git`) — `git push` works without an SSH key in the agent. If you switch hosts and SSH is needed, run `ssh-add ~/.ssh/<key>` first.
