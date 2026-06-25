"""Application configuration loaded from environment variables."""
from __future__ import annotations

import json
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode


class Settings(BaseSettings):
    # LLM / Embeddings
    openai_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    llm_model: str = "gpt-4o-mini"

    # Vector store
    vector_backend: str = "qdrant"          # qdrant | memory
    # When true (production), refuse to start on a non-durable / unreachable
    # backend instead of silently degrading to in-memory and losing data.
    require_durable_backends: bool = False
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""
    qdrant_collection: str = "ragstack"

    # Embedding backend (used at both ingest and query time)
    embedding_api: str = "sidecar"          # sidecar | openai
    embedding_sidecar_url: str = "http://localhost:50053"
    # Optional fan-out: multiple embedding endpoints (e.g. vLLM replicas across
    # the H200s). When set, requests are load-balanced across them with failover;
    # empty falls back to the single embedding_sidecar_url. max_concurrency bounds
    # total in-flight embedding requests (backpressure).
    # Accepts either a comma-separated string or a JSON array, e.g.
    #   EMBEDDING_ENDPOINTS=http://h1:8000,http://h2:8000
    #   EMBEDDING_ENDPOINTS=["http://h1:8000","http://h2:8000"]
    # NoDecode: skip pydantic-settings' default JSON decode so the validator below
    # receives the raw env string and can accept comma-separated input too.
    embedding_endpoints: Annotated[list[str], NoDecode] = Field(default_factory=list)
    embedding_max_concurrency: int = 8
    # Probe path appended to each fan-out endpoint for health checks. The default
    # suits the sidecar and vLLM's OpenAI server; override for backends that
    # expose readiness elsewhere (a backend with no /health would otherwise read
    # as permanently unhealthy and degrade pool routing).
    embedding_health_path: str = "/health"

    @field_validator("embedding_endpoints", mode="before")
    @classmethod
    def _split_embedding_endpoints(cls, value: object) -> object:
        # pydantic-settings parses list[str] env vars as JSON, so a bare
        # comma-separated operator input would otherwise raise. Accept both forms.
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return []
            if value.startswith("["):
                return json.loads(value)
            return [item.strip() for item in value.split(",") if item.strip()]
        return value
    # embedding_model_dim is the vector dimension; defaults to BGE-base.
    # When embedding_api == "openai", set embedding_model to the OpenAI/vLLM model name.
    embedding_model_dim: int = 768

    # Chunker defaults
    chunk_size: int = 512
    chunk_overlap: int = 64

    # Embedding request batching (bounds request size so large documents don't
    # overflow the backend's max batch / context window).
    embedding_max_batch_items: int = 64
    embedding_max_batch_tokens: int = 8192
    embedding_chars_per_token: int = 4

    # Ingestion safety. When ingest_root is set, ingest sources must resolve
    # within it (defeats path-traversal / arbitrary-file-read). max_document_bytes
    # caps input size as a DoS guard; 0 disables the cap.
    ingest_root: str = ""
    max_document_bytes: int = 50_000_000

    # Sharded (batch/directory) ingestion: how many documents process at once
    # and how many per shard. Bounds in-flight work for large directories.
    ingest_concurrency: int = 4
    ingest_shard_size: int = 64

    # Ingestion job tracking. "memory" is process-local (lost on restart);
    # "sqlite" is durable single-process; "postgres" is the multi-process
    # checkpoint of record for the 500k path (uses postgres_dsn).
    job_store_backend: str = "memory"       # memory | sqlite | postgres
    job_store_path: str = "ragstack_jobs.db"

    # Elasticsearch (text index)
    elasticsearch_url: str = "http://localhost:9200"
    elasticsearch_index: str = "ragstack"

    # Neo4j (knowledge graph)
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "neo4j"

    # PostgreSQL (metadata / job queue)
    postgres_dsn: str = "postgresql+asyncpg://ragstack:ragstack@localhost/ragstack"

    # Redis (cache / rate limiting)
    redis_url: str = "redis://localhost:6379"

    # API
    api_keys: list[str] = Field(default_factory=list)
    # Maps an API key to its tenant_id (data isolation). Keys absent from the map
    # resolve to the "default" tenant. A key mapped to "public" writes into the
    # shared public corpus everyone can read.
    api_key_tenants: dict[str, str] = Field(default_factory=dict)
    allowed_origins: list[str] = Field(default_factory=lambda: ["*"])

    # Retrieval defaults
    top_k: int = 5
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    # Observability
    log_level: str = "INFO"
    otel_exporter_otlp_endpoint: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
