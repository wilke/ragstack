"""FastAPI application — entry point."""
from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ragstack.api.deps import lifespan
from ragstack.api.routers import documents, graph, health, query
from ragstack.api.security import resolve_tenant
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
