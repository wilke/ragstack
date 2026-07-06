"""List the collections the query API can serve (tenant-scoped read).

Principal-gated (any authenticated caller), like ``/stats/stores`` — the Explore
UI needs it to populate the collection picker, so it must NOT be admin-only. Each
entry's ``count`` is filtered to the caller's readable tenants (own + public),
never a global store total.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ragstack.api.collections import CollectionRegistry
from ragstack.api.deps import get_collections, get_model_registry, probe_tenant_count
from ragstack.api.model_registry import HOT_SWAPPABLE, ModelRegistry
from ragstack.api.security import Principal, resolve_principal
from ragstack.config import settings
from ragstack.provenance import read_manifest
from ragstack.tenancy import readable_tenants

log = logging.getLogger(__name__)

router = APIRouter()


class Provenance(BaseModel):
    """Verified build lineage from the collection's manifest (null when no
    manifest exists — e.g. manifests disabled or an out-of-band collection)."""

    chunk_method: str | None = None
    chunk_size: int | None = None
    chunk_overlap: int | None = None
    chunk_params: dict[str, Any] = {}
    spec_hash: str = ""
    corpus: str = ""
    chunk_count: int | None = None
    ingested_at: str = ""
    source: str = ""  # "ingest" (verified) | "config" (materialized from registry)


class CollectionInfo(BaseModel):
    id: str
    label: str
    model: str
    dim: int
    chunk_method: str | None = None  # from the registry label (may be operator-asserted)
    chunk_size: int | None = None
    default: bool
    count: int | None = None  # tenant-filtered; null when unavailable
    provenance: Provenance | None = None  # verified lineage from the manifest


class CollectionsResponse(BaseModel):
    collections: list[CollectionInfo]
    default: str


@router.get("/collections", response_model=CollectionsResponse)
async def list_collections(
    principal: Principal = Depends(resolve_principal),
    registry: CollectionRegistry = Depends(get_collections),
) -> CollectionsResponse:
    """Registry collections with tenant-scoped counts and chunk-strategy labels."""
    tenants = readable_tenants(principal.tenant)
    entries = list(registry.entries())
    # The per-collection counts are independent Qdrant round-trips — gather them
    # concurrently so latency is one round-trip, not N (the ops dashboard polls
    # this, and Explore/Compare call it on load). probe_tenant_count never raises.
    counts = await asyncio.gather(
        *(probe_tenant_count(e.vector_store, tenants) for e in entries)
    )
    infos: list[CollectionInfo] = []
    for e, count in zip(entries, counts, strict=True):
        m = read_manifest(settings.collection_manifest_dir, e.collection)
        prov = (
            Provenance(
                chunk_method=m.chunk_method or None,
                chunk_size=m.chunk_size,
                chunk_overlap=m.chunk_overlap,
                chunk_params=m.chunk_params,
                spec_hash=m.spec_hash,
                corpus=m.corpus,
                chunk_count=m.chunk_count,
                ingested_at=m.ingested_at,
                source=m.source,
            )
            if m is not None
            else None
        )
        infos.append(CollectionInfo(
            id=e.id,
            label=e.label,
            model=e.model,
            dim=e.dim,
            chunk_method=e.chunk_method or None,
            chunk_size=e.chunk_size,
            default=e.is_default,
            count=count,
            provenance=prov,
        ))
    return CollectionsResponse(collections=infos, default=registry.default_id)


class AvailableModel(BaseModel):
    id: str
    task: str  # llm | reranker
    label: str
    model: str
    provider: str


class AvailableModelsResponse(BaseModel):
    models: list[AvailableModel]


@router.get("/models/available", response_model=AvailableModelsResponse)
async def list_available_models(
    models: ModelRegistry = Depends(get_model_registry),
) -> AvailableModelsResponse:
    """Registered models assignable to a hot-swappable task (llm / reranker), for
    the Compare per-lane model pickers. Authenticated callers only (the router is
    mounted with ``resolve_principal``) — but base_urls are NOT exposed
    (registration is admin-only + SSRF-checked; callers only need to name a
    curated model)."""
    out = [
        AvailableModel(
            id=e.id, task=e.task, label=e.model or e.id, model=e.model, provider=e.provider
        )
        for e in models.entries()
        if e.task in HOT_SWAPPABLE
    ]
    return AvailableModelsResponse(models=out)
