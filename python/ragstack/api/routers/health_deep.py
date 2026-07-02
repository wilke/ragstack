"""Deep dependency health probe (role: ``admin``).

Included under the ``require_role(ROLE_ADMIN)`` group in ``api/main.py``, so the
whole surface is admin-only *by construction* — a wiring slip can't expose it.
That gate is load-bearing: each check's ``detail`` carries backend
identity/error text (hostnames, driver versions, exception messages), which must
never reach a non-admin. There is deliberately no non-admin "shallow-but-detailed"
variant; ``GET /health`` stays the coarse public liveness probe.

Each dependency is probed with a cheap liveness op wrapped in try/except and
timed; ``status`` is ``degraded`` if any check fails.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ragstack.api.deps import (
    get_graph_store,
    get_job_store,
    get_text_index,
    get_vector_store,
)
from ragstack.config import settings

router = APIRouter()


class DeepCheck(BaseModel):
    name: str
    ok: bool
    detail: str | None = None
    latency_ms: float | None = None


class DeepHealthResponse(BaseModel):
    status: str  # "ok" | "degraded"
    checks: list[DeepCheck]


async def _probe(name: str, backend: str, op: Callable[[], Awaitable[Any]]) -> DeepCheck:
    """Run a liveness ``op`` and time it. Admin-only endpoint, so ``detail`` may
    carry backend identity/error text."""
    start = perf_counter()
    try:
        await op()
        ok, detail = True, backend
    except Exception as e:  # surface the backend error (admin-only)
        ok, detail = False, f"{backend}: {type(e).__name__}: {e}"
    latency_ms = round((perf_counter() - start) * 1000, 2)
    return DeepCheck(name=name, ok=ok, detail=detail, latency_ms=latency_ms)


@router.get("/health/deep", response_model=DeepHealthResponse)
async def health_deep(
    vector_store: Any = Depends(get_vector_store),
    text_index: Any = Depends(get_text_index),
    graph_store: Any = Depends(get_graph_store),
    job_store: Any = Depends(get_job_store),
) -> DeepHealthResponse:
    """Per-dependency liveness with latency + backend detail (admin only)."""

    async def _vector() -> None:
        # Read-only connectivity check. NOT ensure_collection(), which would
        # *create* the collection — a probe must never provision infra. The
        # in-memory store has no healthcheck → trivially live.
        if hasattr(vector_store, "healthcheck"):
            await vector_store.healthcheck()

    async def _text() -> None:
        # Read-only — NOT ensure_index() (which would create the index).
        if hasattr(text_index, "healthcheck"):
            await text_index.healthcheck()

    async def _graph() -> None:
        # None = graph disabled (optional component) → healthy no-op. Otherwise a
        # bounded read confirms connectivity.
        if graph_store is not None:
            await graph_store.list_entities(tenant_id=None, limit=1)

    async def _jobs() -> None:
        # A miss round-trips to the store (real query for sqlite/postgres) without
        # mutating anything.
        await job_store.get("__healthcheck__")

    checks = [
        await _probe("vector", settings.vector_backend, _vector),
        await _probe("text", settings.text_backend, _text),
        await _probe("graph", settings.graph_backend, _graph),
        await _probe("jobstore", settings.job_store_backend, _jobs),
    ]
    status = "ok" if all(c.ok for c in checks) else "degraded"
    return DeepHealthResponse(status=status, checks=checks)
