# Scratchpad — keen-newton worktree

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
