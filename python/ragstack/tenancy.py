"""Tenancy primitives.

Every stored chunk carries a ``tenant_id``. A caller may *read* its own tenant
plus the shared ``public`` corpus (e.g. public-good document sets), but may only
*write* and *delete* within its own tenant. The tenant is always derived
server-side from the API key — never trusted from the request body.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ragstack.models import Chunk

# The shared, world-readable tenant (public-good corpora live here).
PUBLIC_TENANT = "public"
# Tenant for callers when auth is disabled (dev/tests) or a key has no mapping.
DEFAULT_TENANT = "default"


def readable_tenants(tenant: str) -> list[str]:
    """Tenants a caller may read: its own plus the public corpus."""
    if tenant == PUBLIC_TENANT:
        return [PUBLIC_TENANT]
    return [tenant, PUBLIC_TENANT]


def tenant_of(chunk: Chunk) -> str:
    """The tenant that owns a chunk: its stamped ``tenant_id`` (in metadata), or
    the default when unstamped. Single source for the fallback across stores."""
    return str(chunk.metadata.get("tenant_id", DEFAULT_TENANT))


def scope_filters(filters: dict[str, Any], tenant: str) -> dict[str, Any]:
    """Scope a filter dict to the tenants a caller may read (own + public).
    ``tenant_id`` is set last so a client (or a ``--filter``) can't widen it."""
    return {**filters, "tenant_id": readable_tenants(tenant)}
