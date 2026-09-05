# RAGStack

A production-grade, full-stack **Retrieval-Augmented Generation** platform —
served by two implementations of one contract-first HTTP API.

| Component | Technology |
|---|---|
| Vector search | Qdrant (dense embeddings) |
| Text indexing | Elasticsearch (BM25 / full-text) |
| Metadata search | Structured payload filters |
| Knowledge graphs | Neo4j + entity extraction |
| Query rewriting | HyDE, multi-query, step-back, entity expansion |
| Scorer / reranker | Cross-encoder + Reciprocal Rank Fusion |
| REST API | FastAPI (Python), with auth, tenancy and rate limiting |

## Quick Start

Everything runs through the root `Makefile`, which `cd`s into the right
subdirectory for you. **Run these from the repository root** — there is no
top-level `pyproject.toml`, `tests/` or `docker-compose.yml`; the Python package
lives under `python/`, and the compose files under
`deploy/`.

```bash
make install-python    # pip install -e ".[all,dev]"  inside python/
make run-python        # uvicorn ragstack.api.main:app --reload --port 8000
```

That is enough for an in-memory dev server. For the real stores and the model
sidecars, in Docker:

```bash
cp .env.example .env   # then edit it
make up-python         # infra + sidecars + the API
make down              # stop everything
```

On a host without Docker, use `make infra-up-apptainer` /
`make sidecars-up-apptainer` instead — see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

`make help` lists every target.

Interactive API docs are served by the running server at `/docs` (Swagger) and
`/redoc` — `http://localhost:8000/docs` for the server `make run-python` just
started. The API listens on **8000** by convention.

## Documentation

Start here, in this order:

- **[docs/USER-GUIDE.md](docs/USER-GUIDE.md)** — for people *using* a deployment:
  sign in, make a collection, ingest, query, read the config.
- **[docs/COOKBOOK.md](docs/COOKBOOK.md)** — the same ground as questions, for
  users, integrators and operators.
- **[docs/API.md](docs/API.md)** — the HTTP reference: every endpoint, the
  ownership and sharing model, error semantics, configuration.
- **[CLAUDE.md](CLAUDE.md)** — the map for someone picking up the repo cold, and
  the pointer into [STATUS.md](STATUS.md), [MEMORY.md](MEMORY.md) and
  [SPEC.md](SPEC.md).
- **[docs/](docs/)** — deployment, architecture, ADRs, runbooks, glossary.

## Claude Code plugin

This repository ships a **Claude Code plugin** so you can query a RAGStack
knowledge base directly from Claude Code. The repo root *is* the plugin root, so
the bundled MCP server binary (`go/bin/ragstack-mcp`) is resolved automatically
via `${CLAUDE_PLUGIN_ROOT}`.

**Components**

| Path | What it provides |
|---|---|
| `.claude-plugin/plugin.json` | Plugin manifest (`name: ragstack`) |
| `.mcp.json` | Bundles the `ragstack` stdio MCP server (tools: `list_collections`, `search`, `answer`) |
| `commands/ragstack-query.md` | `/ragstack-query <question>` — grounded, cited answer |
| `skills/ragstack/SKILL.md` | Teaches Claude when to search vs. answer, and grounding rules |

**Use it for a session**

```bash
claude --plugin-dir /path/to/ragstack
```

Then ask a question about your indexed documents, or run
`/ragstack-query <question>`. To enable it persistently, add
`"ragstack": true` under `enabledPlugins` in `~/.claude/settings.json`.

**Configuration** (set in `.mcp.json`, overridable via the environment):

| Env var | Default | Purpose |
|---|---|---|
| `RAGSTACK_BASE_URL` | `http://localhost:8030` | RAGStack API base URL |
| `RAGSTACK_COLLECTION` | `demo_g1_sfr_tok512` | Default collection id |
| `RAGSTACK_API_KEY` | *(unset)* | Optional; sent as the `X-API-Key` header |

The API at `RAGSTACK_BASE_URL` must be running (see **Quick Start** above), or
the plugin's tools will fail to connect. Build the server binary with
`make` / your Go toolchain if `go/bin/ragstack-mcp` is missing.

## Project Layout

```
contracts/     # openapi.yaml + JSON schemas + fixtures — SOURCE OF TRUTH for the API
python/        # Python/FastAPI implementation (:8000) — package, tests, scripts
go/            # unfinished scaffold — not deployed, not part of the API
conformance/   # black-box pytest suite, run over HTTP against either one
sidecars/      # embedding / crossencoder / faiss model services
frontend/      # React + Vite SPA (explorer, compare, evidence, ops)
deploy/        # layered docker-compose files (infra, sidecars, per-impl API)
apptainer/     # the Docker-free deployment path (preferred on hosts without Docker)
cwl/           # CWL workflows for the bulk ingest / eval pipelines
docs/          # guides, ADRs, runbooks, and the static-site builder
```

Changing the API means: edit `contracts/` first, implement in **both** `python/`
then prove it with `make test-conformance-python`.

## Development

```bash
make test-python           # pytest, inside python/
make lint-python           # ruff check . && mypy ragstack/
make test-all              # the unit suites

make up-python             # conformance needs a server ALREADY running
make test-conformance      # both implementations, over HTTP
```

`make perf-python` runs the perf-budget tests, which `make test-python`
deliberately excludes.

## License

MIT
