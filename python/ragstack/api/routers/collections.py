"""List the collections the query API can serve (tenant-scoped read).

Principal-gated (any authenticated caller), like ``/stats/stores`` — the Explore
UI needs it to populate the collection picker, so it must NOT be admin-only. Each
entry's ``count`` is filtered to the caller's readable tenants (own + public),
never a global store total.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ragstack.api.collections import CollectionRegistry
from ragstack.api.deps import get_collections
from ragstack.api.security import Principal, resolve_principal
from ragstack.tenancy import readable_tenants

log = logging.getLogger(__name__)

router = APIRouter()


class CollectionInfo(BaseModel):
    id: str
    label: str
    model: str
    dim: int
    chunk_method: str | None = None
    chunk_size: int | None = None
    default: bool
    count: int | None = None  # tenant-filtered; null when unavailable


class CollectionsResponse(BaseModel):
    collections: list[CollectionInfo]
    default: str


async def _count(vs: Any, tenants: list[str]) -> int | None:
    if vs is None or not hasattr(vs, "count_tenants"):
        return None
    try:
        return int(await vs.count_tenants(tenants))
    except Exception:
        log.warning("collections: count probe failed", exc_info=True)
        return None


@router.get("/collections", response_model=CollectionsResponse)
async def list_collections(
    principal: Principal = Depends(resolve_principal),
    registry: CollectionRegistry = Depends(get_collections),
) -> CollectionsResponse:
    """Registry collections with tenant-scoped counts and chunk-strategy labels."""
    tenants = readable_tenants(principal.tenant)
    infos = [
        CollectionInfo(
            id=e.id,
            label=e.label,
            model=e.model,
            dim=e.dim,
            chunk_method=e.chunk_method or None,
            chunk_size=e.chunk_size,
            default=e.is_default,
            count=await _count(e.vector_store, tenants),
        )
        for e in registry.entries()
    ]
    return CollectionsResponse(collections=infos, default=registry.default_id)
