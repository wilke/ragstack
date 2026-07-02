"""Admin surface for the dashboard (role: ``admin``).

Every route here is gated by ``require_role(ROLE_ADMIN)`` at include time
(``api/main.py``), so authorization is enforced server-side — a UI route-gate is
never the only check.

``GET /config`` exposes the *effective operational* configuration for the
sysadmin config viewer. It is built from an explicit **allowlist** of
non-sensitive fields (the :class:`ConfigResponse` model fields): secrets
(``*_api_key``, ``*_password``, ``postgres_dsn``, ``api_keys``, the
``api_key_tenants``/``api_key_roles`` maps) are never read, so they can never
leak even as new settings are added.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter
from pydantic import BaseModel

from ragstack.config import settings

router = APIRouter()

# Endpoint URLs are operational config, but a URL can carry inline credentials
# (e.g. ``bolt://neo4j:secret@host:7687``). Redact userinfo from these before
# returning so the allowlist can't leak a password via the connection string.
_URL_FIELDS = frozenset(
    {"qdrant_url", "elasticsearch_url", "neo4j_uri", "crossencoder_sidecar_url"}
)


def _redact_url(value: str) -> str:
    """Strip any ``user:pass@`` userinfo from a URL, keeping scheme/host/port/path."""
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    if not (parts.username or parts.password):
        return value
    host = parts.hostname or ""
    netloc = host if parts.port is None else f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


class ConfigResponse(BaseModel):
    """Allowlisted, non-sensitive operational config. Adding a field here opts it
    into the response; nothing else is ever serialized."""

    # backends
    vector_backend: str
    text_backend: str
    graph_backend: str
    job_store_backend: str
    require_durable_backends: bool
    # vector / text / graph endpoints (URLs only — credentials are separate fields
    # and deliberately excluded)
    qdrant_url: str
    qdrant_collection: str
    qdrant_collection_explicit: str
    elasticsearch_url: str
    elasticsearch_index: str
    neo4j_uri: str
    # embedding
    embedding_api: str
    embedding_model: str
    embedding_model_dim: int
    embedding_endpoints: list[str]
    embedding_max_concurrency: int
    # chunking
    chunk_method: str
    chunk_size: int
    chunk_overlap: int
    chunk_max_tokens: int | None
    chunk_token_counter: str
    # retrieval / rerank / graph extraction
    top_k: int
    rerank_enabled: bool
    rerank_candidates: int
    reranker_model: str
    crossencoder_sidecar_url: str
    kg_extraction_enabled: bool
    # ingest / quotas
    ingest_concurrency: int
    ingest_shard_size: int
    tenant_max_concurrency: int
    max_document_bytes: int
    # observability
    log_level: str


@router.get("/config", response_model=ConfigResponse)
async def get_config() -> ConfigResponse:
    """Effective operational config (admin). Allowlisted — no secrets, and any
    inline credentials in endpoint URLs are redacted."""
    values: dict[str, Any] = {}
    for name in ConfigResponse.model_fields:
        val = getattr(settings, name)
        if name in _URL_FIELDS and isinstance(val, str):
            val = _redact_url(val)
        elif name == "embedding_endpoints" and isinstance(val, list):
            val = [_redact_url(u) if isinstance(u, str) else u for u in val]
        values[name] = val
    return ConfigResponse(**values)
