"""Tenancy primitives.

Every stored chunk carries a ``tenant_id``. A caller may *read* its own tenant
plus the shared ``public`` corpus (e.g. public-good document sets), but may only
*write* and *delete* within its own tenant. The tenant is always derived
server-side from the API key — never trusted from the request body.

Conceptually (ADR-0003 decision 1), the per-chunk ``tenant_id`` payload key is
an **owner_id**: it records who *wrote* the chunk. It is provenance plus
defence in depth — since #243 access is asserted at the *collection*
(``resolve_access``), not by this filter. "Tenant" as a *deployment* concept
now means a whole Qdrant instance (ADR-0005). The rename is a code-level
alias ONLY (per the #246 migration checklist, not the ADR): the physical key
name is historical and stays ``tenant_id`` forever, because renaming storage
would rewrite every point (it is indexed in Qdrant and baked into ES ids and
point ids). See :data:`OWNER_FIELD`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ragstack.models import Chunk

# The shared, world-readable tenant (public-good corpora live here).
PUBLIC_TENANT = "public"
# Tenant for callers when auth is disabled (dev/tests) or a key has no mapping.
DEFAULT_TENANT = "default"
# Historical payload key stamped on every chunk; records who wrote it (owner
# provenance, ADR-0003 §1). Never rename the stored key (#246: code-level
# alias only) — it is indexed in Qdrant and baked into ES ids and point ids.
# Single conceptual home for the stores' tenant-field
# comments: _TENANT_FIELD in stores/qdrant.py, the
# ``metadata.tenant_id`` mapping in stores/elasticsearch.py, _matches in
# stores/memory.py.
OWNER_FIELD = "tenant_id"


def readable_tenants(tenant: str, extra: list[str] | None = None) -> list[str]:
    """Tenants a caller may read: its own plus the public corpus, plus any
    ``extra`` writer-tenants the caller has been authorized to read for a
    *specific* collection (e.g. the owner's tenant of a collection shared with the
    caller — the owner's ``tenant_id`` is what stamps that collection's chunks).
    Order-preserving and de-duplicated."""
    base = [PUBLIC_TENANT] if tenant == PUBLIC_TENANT else [tenant, PUBLIC_TENANT]
    if extra:
        for t in extra:
            if t and t not in base:
                base.append(t)
    return base


def allowed_collection_ids(
    tenant: str, mapping: dict[str, list[str]]
) -> set[str] | None:
    """Collection ids a tenant is confined to, or ``None`` when unrestricted.

    ``None`` (unrestricted) when the mapping is empty (feature off) or the tenant
    isn't listed (operators/admins stay unrestricted). A listed tenant is confined
    to its set — the isolation boundary that lets one multi-collection API serve
    several orgs. Applies to reads (query/retrieve/chunks/list) and ingest targets;
    it does NOT replace the per-chunk ``tenant_id`` filter, which still scopes rows
    within a collection."""
    if not mapping:
        return None
    listed = mapping.get(tenant)
    return set(listed) if listed is not None else None


def tenant_of(chunk: Chunk) -> str:
    """The tenant that owns a chunk: its stamped ``tenant_id`` (in metadata), or
    the default when unstamped. Single source for the fallback across stores.
    The stamped ``tenant_id`` is conceptually the chunk's *owner_id* — see the
    module docstring and :data:`OWNER_FIELD` (ADR-0003 decision 1)."""
    return str(chunk.metadata.get("tenant_id", DEFAULT_TENANT))


def scope_filters(
    filters: dict[str, Any], tenant: str, extra: list[str] | None = None
) -> dict[str, Any]:
    """Scope a filter dict to the tenants a caller may read (own + public, plus any
    ``extra`` collection-scoped tenants — see :func:`readable_tenants`).
    ``tenant_id`` is set last so a client (or a ``--filter``) can't widen it."""
    return {**filters, "tenant_id": readable_tenants(tenant, extra)}
