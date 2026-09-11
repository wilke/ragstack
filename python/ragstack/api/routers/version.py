"""``GET /v1/version`` — which code this process runs (any credential).

Included under the ``resolve_tenant`` group in ``api/main.py``: a credential is
required when keys are configured (a 401 without one, like every ``/v1`` data
route), but no role is — a git tag and a start time are not backend detail, so
this deliberately does NOT join the admin-only ``/v1/health/deep`` group. The
control plane (ADR-0007) reads it with a tenant's own key to show
"configured vs running" on the fleet view; a viewer of that dashboard sees the
same fields, so the endpoint must not leak more than they do.

Python-only in v1 (ADR-0006 decision 4): the Go scaffold does not implement it,
and ``conformance/test_version.py`` skips on ``RAGSTACK_IMPL=go``.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ragstack.version import version_info

router = APIRouter()


class VersionResponse(BaseModel):
    """Mirror of ``contracts/schemas/version_response.json``."""

    version: str
    git_tag: str | None
    git_sha: str | None
    started_at: str
    python: str
    impl: str


@router.get("/version", response_model=VersionResponse)
def version() -> VersionResponse:
    """Package version, git identity, start time and runtime of this process.

    Deliberately a plain ``def``. :func:`ragstack.version.version_info` may run
    ``git`` — bounded, but up to a few seconds on a cold cache — and an ``async
    def`` would run that *on the event loop*, stalling every other request on
    the process behind a subprocess on the first call after each restart.
    FastAPI runs a sync route in its threadpool; the lifespan warms the same
    cache there, so in practice this path is already hot.
    """
    return VersionResponse(**version_info())
