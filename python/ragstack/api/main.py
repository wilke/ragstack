"""FastAPI application — entry point."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ragstack.api.routers import documents, graph, health, query
from ragstack.config import settings

app = FastAPI(
    title="RAGStack API",
    description="Production-grade Retrieval-Augmented Generation platform.",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["Health"])
app.include_router(query.router, prefix="/v1", tags=["Query"])
app.include_router(documents.router, prefix="/v1", tags=["Documents"])
app.include_router(graph.router, prefix="/v1/graph", tags=["Graph"])
