"""FastAPI application — entry point."""
from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ragstack.api.deps import lifespan
from ragstack.api.root_path import RootPathMiddleware
from ragstack.api.routers import (
    admin,
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

# Mounted-under-a-prefix support. The gateway serves this app at
# /ragstack/<tenant>/api/ and strips the prefix, so /docs used to emit a
# root-absolute `url: '/openapi.json'` and 404 there. This restores the prefix
# into scope["root_path"], which is what FastAPI builds the docs/redoc URLs and
# the schema's `servers` entry from; absent a prefix nothing changes, so
# direct-port /docs still works. See api/root_path.py.
#
# Added LAST so it is the OUTERMOST middleware — the value must be in the scope
# before routing (add_middleware inserts at the front of the stack).
app.add_middleware(RootPathMiddleware)

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
