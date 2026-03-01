# RAGStack

A **production-grade, full-stack Retrieval-Augmented Generation (RAG)** platform.

## Key Capabilities

| Component | Technology |
|---|---|
| Vector search | Qdrant (dense embeddings) |
| Text indexing | Elasticsearch (BM25 / full-text) |
| Metadata search | Structured payload filters |
| Knowledge graphs | Neo4j + entity extraction |
| Query rewriting | HyDE, multi-query, step-back, entity expansion |
| Scorer / reranker | Cross-encoder + Reciprocal Rank Fusion |
| REST API | FastAPI with auth, rate-limiting, streaming |

## Quick Start

```bash
# 1. Copy environment variables
cp .env.example .env
# Edit .env with your API keys

# 2. Start infrastructure
docker compose up -d

# 3. Install Python dependencies
pip install -e ".[dev]"

# 4. Run the API server
uvicorn ragstack.api.main:app --reload
```

API docs are available at `http://localhost:8000/docs`.

## Documentation

- **[SPEC.md](SPEC.md)** — Full architecture specification, data models, milestones
- **[docs/](docs/)** — Additional guides (deployment, configuration, contributing)

## Project Layout

```
ragstack/
├── api/           # FastAPI application & routers
├── ingestion/     # Document loaders, chunkers, embedders
├── retrieval/     # Hybrid retriever (vector + BM25 + graph)
├── rewriting/     # Query rewriting strategies
├── scoring/       # Reranker / scorer
├── graph/         # Knowledge-graph extraction & queries
├── stores/        # Store adapters (Qdrant, Elasticsearch, Neo4j, Postgres)
└── pipeline/      # End-to-end orchestration
tests/             # Unit, integration, API, and E2E tests
docker/            # Dockerfiles
docs/              # Additional documentation
```

## Development

```bash
# Lint
ruff check . && mypy ragstack/

# Test
pytest tests/ -v
```

## License

MIT
