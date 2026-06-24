"""Application configuration loaded from environment variables."""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


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
    allowed_origins: list[str] = Field(default_factory=lambda: ["*"])

    # Retrieval defaults
    top_k: int = 5
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    # Observability
    log_level: str = "INFO"
    otel_exporter_otlp_endpoint: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
