"""FastAPI application — entry point."""
from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ragstack.api.deps import lifespan
from ragstack.api.root_path import RootPathMiddleware
from ragstack.api.routers import (
    admin,
    admin_collections,
    admin_users,
    collections,
    documents,
    graph,
    groups,
    health,
    health_deep,
    jobs,
    models,
    models_registry,
    query,
    service_accounts,
    stats,
)
from ragstack.api.security import (
    ROLE_ADMIN,
    require_role,
    resolve_principal,
    resolve_tenant,
)
from ragstack.api.upload_guard import UploadContentLengthMiddleware
from ragstack.config import settings
from ragstack.observability import RequestContextMiddleware, configure_logging
from ragstack.observability.middleware import stamp_request_id
from ragstack.stores.errors import StoreUnavailable

# Before the app is built, and before any module-level log call: until #427 the
# API installed no root handler and set no level, so every log.info() under
# ragstack.* was discarded and every warning printed through logging.lastResort
# with no timestamp, level or logger name. This also makes LOG_LEVEL — which
# GET /v1/config has always echoed — actually do something.
configure_logging()

log = logging.getLogger(__name__)

app = FastAPI(
    title="RAGStack API",
    description="Production-grade Retrieval-Augmented Generation platform.",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)



@app.exception_handler(StoreUnavailable)
async def _store_unavailable(_: Request, exc: StoreUnavailable) -> JSONResponse:
    """A backing store didn't answer (timeout, unreachable, 5xx). That is the
    deployment's problem, not the caller's request — 503 with the reason and a
    Retry-After, never a bare 500 (which reads as a bug and hides the cause)."""
    log.warning("%s unavailable: %s", exc.store, exc)
    return JSONResponse(
        status_code=503,
        content={"detail": f"{exc.store} unavailable: {exc}"},
        headers={"Retry-After": "5"},
    )


@app.exception_handler(Exception)
async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
    """The bare-500 path — and the ONLY place ``X-Request-Id`` can be stamped
    on it.

    Starlette's ``ServerErrorMiddleware`` sits above every middleware
    ``add_middleware`` can install, and it renders its 500 with the *original*
    ``send``. So ``RequestContextMiddleware``'s ``send`` wrapper never sees that
    response and cannot stamp it. Registering this handler moves the response
    generation *inside* ``ServerErrorMiddleware`` — where the contextvar is
    still set — so the header can be attached here by hand.

    The body stays exactly what Starlette's default 500 carries — this exists to
    stamp the header and log the id, not to expose internals, and putting the id
    in the body is a schema change this work item deliberately does not make
    (that is #427 W6's ``error.json``). Note ``ServerErrorMiddleware`` re-raises
    after sending, so the traceback still reaches the server log and a test that
    wants to observe this response needs
    ``ASGITransport(..., raise_app_exceptions=False)``.
    """
    headers: dict[str, str] = {}
    stamp_request_id(headers)
    log.exception("unhandled %s", type(exc).__name__)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"},
        headers=headers,
    )


# Upload ingress guard (#202): refuse POST /v1/ingest/upload from its headers
# alone — Content-Length over max_upload_bytes_per_request (+ framing) → 413,
# no Content-Length → 411 — before FastAPI's multipart parser can drain the
# body into the spool. Added FIRST so it sits INSIDE CORSMiddleware (its
# refusals still carry the CORS headers a browser upload needs) and inside
# RootPathMiddleware (the prefix is in the scope when it matches the route).
# See api/upload_guard.py for what it can and cannot stop.
app.add_middleware(UploadContentLengthMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    # Credentials cannot be combined with a wildcard origin (browsers reject it
    # and it's unsafe); only allow credentials when origins are explicitly set.
    allow_credentials="*" not in settings.allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    # A cross-origin browser client can only READ a response header that is
    # explicitly exposed. X-Request-Id is the whole point of #427 W1 — the id a
    # user reads off the UI and an operator greps for — so without this the
    # frontend cannot see it. Retry-After is listed for the same reason: it has
    # been documented on the 429/503 responses all along and has been
    # unreadable cross-origin the entire time. That is a latent bug this fixes
    # in passing.
    expose_headers=["X-Request-Id", "Retry-After"],
)

# Mounted-under-a-prefix support. The gateway serves this app at
# /ragstack/<tenant>/api/ and strips the prefix, so /docs used to emit a
# root-absolute `url: '/openapi.json'` and 404 there. This restores the prefix
# into scope["root_path"], which is what FastAPI builds the docs/redoc URLs and
# the schema's `servers` entry from; absent a prefix nothing changes, so
# direct-port /docs still works. See api/root_path.py.
#
# Must sit OUTSIDE CORSMiddleware and the upload guard — the value has to be in
# the scope before routing (add_middleware inserts at the front of the stack).
# It is no longer the outermost, RequestContextMiddleware below is; that is
# harmless because root_path.py only ever sets scope["root_path"] and never
# scope["path"], so nothing the request-context middleware reads is affected.
app.add_middleware(RootPathMiddleware)

# Request correlation (#427). Added LAST so it is the OUTERMOST middleware this
# application installs, which is load-bearing rather than tidy: the upload guard
# hand-builds its 411/413 response and returns without calling the app at all,
# so only a send-wrapper outside it can stamp those with X-Request-Id.
# tests/api/test_request_id_upload_guard.py pins this ordering and fails if the
# add_middleware calls are reordered.
#
# It still cannot be outermost in absolute terms — Starlette's
# ServerErrorMiddleware is above everything here and renders its 500 with the
# original send. The _unhandled handler above covers that path; see
# observability/middleware.py for the probe that established it.
app.add_middleware(RequestContextMiddleware)

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
# Groups: RAGStack-native named groups of users, share targets (issue #245).
# Any authenticated caller creates/lists; owner-or-admin manages; membership is
# unioned into read authorization through the ONE seam (grants_for_subject).
app.include_router(
    groups.router, prefix="/v1", tags=["Query"], dependencies=[Depends(resolve_principal)]
)
# Admin surface: gated at the router level by the ``admin`` role (require_role also
# performs auth), so every route under it is admin-only by construction. /v1/health/deep
# joins this group so its backend-detail responses are admin-only by the same gate.
_admin = [Depends(require_role(ROLE_ADMIN))]
app.include_router(admin.router, prefix="/v1", tags=["Admin"], dependencies=_admin)
app.include_router(health_deep.router, prefix="/v1", tags=["Health"], dependencies=_admin)
app.include_router(models.router, prefix="/v1", tags=["Stats"], dependencies=_admin)
app.include_router(jobs.router, prefix="/v1", tags=["Stats"], dependencies=_admin)
app.include_router(
    models_registry.router, prefix="/v1/admin", tags=["Admin"], dependencies=_admin
)
# Service accounts (issue #258): register/list/disable machine identities. Admin
# only by the same include-time gate. Manages the account RECORD, never the key —
# API_KEYS is env, so rotation stays an operator edit plus a restart; disabling is
# what makes a leaked key stoppable without one.
app.include_router(
    service_accounts.router, prefix="/v1/admin", tags=["Admin"], dependencies=_admin
)
# Bearer admin grants: PATCH /v1/admin/users/{subject}/role. The only in-API way
# a federated identity becomes admin — the bearer path never inherits
# DEFAULT_ROLE and no token can elevate itself. Admin-gated at include time like
# the rest of /v1/admin; the ADMIN_SUBJECTS env allowlist is the other (and
# outage-proof) admin source, and it is not writable from here.
app.include_router(
    admin_users.router, prefix="/v1/admin", tags=["Admin"], dependencies=_admin
)
# Eviction (#359): POST /v1/admin/collections/evict — the operator's handle on
# the active-collection bound; the create path runs the same policy at the
# bound. Admin-gated at include time like the rest of /v1/admin.
app.include_router(
    admin_collections.router, prefix="/v1/admin", tags=["Admin"], dependencies=_admin
)
