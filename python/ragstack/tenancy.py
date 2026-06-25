"""Tenancy primitives.

Every stored chunk carries a ``tenant_id``. A caller may *read* its own tenant
plus the shared ``public`` corpus (e.g. public-good document sets), but may only
*write* and *delete* within its own tenant. The tenant is always derived
server-side from the API key — never trusted from the request body.
"""
from __future__ import annotations

# The shared, world-readable tenant (public-good corpora live here).
PUBLIC_TENANT = "public"
# Tenant for callers when auth is disabled (dev/tests) or a key has no mapping.
DEFAULT_TENANT = "default"


def readable_tenants(tenant: str) -> list[str]:
    """Tenants a caller may read: its own plus the public corpus."""
    if tenant == PUBLIC_TENANT:
        return [PUBLIC_TENANT]
    return [tenant, PUBLIC_TENANT]
