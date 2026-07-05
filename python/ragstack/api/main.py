"""FastAPI application — entry point."""
from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ragstack.api.deps import lifespan
from ragstack.api.routers import (
    admin,
    collections,
    documents,
    graph,
    health,
    health_deep,
    jobs,
    models,
    query,
    stats,
)
from ragstack.api.security import (
    ROLE_ADMIN,
    require_role,
    resolve_principal,
    resolve_tenant,
)
from ragstack.config import settings

app = FastAPI(
    title="RAGStack API",
    description="Production-grade Retrieval-Augmented Generation platform.",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    # Credentials cannot be combined with a wildcard origin (browsers reject it
    # and it's unsafe); only allow credentials when origins are explicitly set.
    allow_credentials="*" not in settings.allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health stays open for liveness probes; the data/v1 surface requires an API key
# when keys are configured (always, in production). resolve_tenant both enforces
# auth here and provides the tenant to handlers (cached per request).
_secured = [Depends(resolve_tenant)]
app.include_router(health.router, tags=["Health"])
app.include_router(query.router, prefix="/v1", tags=["Query"], dependencies=_secured)
app.include_router(documents.router, prefix="/v1", tags=["Documents"], dependencies=_secured)
app.include_router(graph.router, prefix="/v1/graph", tags=["Graph"], dependencies=_secured)
# Stats/aggregation reads: any authenticated caller, tenant-scoped in the handlers.
# Enforce auth with resolve_principal (not resolve_tenant) so it matches the handler
# dependency and FastAPI caches it — the API key is verified once, not twice.
app.include_router(
    stats.router, prefix="/v1", tags=["Stats"], dependencies=[Depends(resolve_principal)]
)
app.include_router(
    collections.router, prefix="/v1", tags=["Query"], dependencies=[Depends(resolve_principal)]
)
# Admin surface: gated at the router level by the ``admin`` role (require_role also
# performs auth), so every route under it is admin-only by construction. /v1/health/deep
# joins this group so its backend-detail responses are admin-only by the same gate.
_admin = [Depends(require_role(ROLE_ADMIN))]
app.include_router(admin.router, prefix="/v1", tags=["Admin"], dependencies=_admin)
app.include_router(health_deep.router, prefix="/v1", tags=["Health"], dependencies=_admin)
app.include_router(models.router, prefix="/v1", tags=["Stats"], dependencies=_admin)
app.include_router(jobs.router, prefix="/v1", tags=["Stats"], dependencies=_admin)
