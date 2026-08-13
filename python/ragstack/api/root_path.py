"""Serving the API under a reverse proxy's path prefix.

The gateway publishes each deployment at ``/ragstack/<tenant>/api/...`` and
STRIPS that prefix, so the app only ever sees ``/docs`` and ``/openapi.json``.
Knowing nothing about the prefix, FastAPI rendered the Swagger page with a
ROOT-absolute ``url: '/openapi.json'`` — which through the gateway is the
gateway's own root, and 404s ("Failed to load API definition").

ASGI already has the field for this: ``scope["root_path"]`` is the prefix the
app is mounted under, and FastAPI builds the docs' ``openapi_url``, the redoc
URL and the schema's ``servers`` entry from it (``applications.FastAPI.setup``).
The prefix belongs to the DEPLOYMENT, not to the app build, so it arrives per
request as ``X-Forwarded-Prefix`` from the proxy; ``ROOT_PATH`` pins it for a
proxy that cannot be changed. Both default to absent, which is what keeps
direct-port access (``http://localhost:PORT/docs`` — how these get debugged)
byte-identical to before.

Routing is untouched by design: the gateway strips the prefix, so
``scope["path"]`` never starts with it and Starlette's ``get_route_path``
returns the path unchanged. This only affects the URLs the app EMITS.
"""
from __future__ import annotations

import re

from starlette.types import ASGIApp, Receive, Scope, Send

from ragstack.config import settings

# One leading slash, then a conservative path charset. Deliberately excludes
# ':', '?', '#', '\\', '%' and whitespace — see normalize_prefix.
_PREFIX_RE = re.compile(r"^/[A-Za-z0-9._~/-]*$")
_MAX_PREFIX_LEN = 256

_HEADER = b"x-forwarded-prefix"


def normalize_prefix(value: str) -> str:
    """A usable ``root_path`` from a proxy-supplied prefix, or ``""`` for none.

    VALIDATED, not trusted: the header is caller-controlled (nginx sets it, but
    nothing stops a client from sending its own) and it lands in the Swagger
    page's ``openapi_url``. So a single leading slash and a path-only charset
    are required, which rejects ``//evil.example`` — a protocol-relative URL
    that would point the docs page's fetch at another origin — along with any
    scheme, query, fragment, traversal or CRLF. A rejected value is treated as
    absent, so the failure mode is the pre-existing root-relative behaviour
    rather than a bad URL.
    """
    prefix = value.strip().rstrip("/")
    if not prefix or len(prefix) > _MAX_PREFIX_LEN:
        return ""
    if "//" in prefix or ".." in prefix or not _PREFIX_RE.match(prefix):
        return ""
    return prefix


def _forwarded_prefix(scope: Scope) -> str:
    """The validated ``X-Forwarded-Prefix`` of this request, or ``""``.

    Last header wins, matching how a duplicated forwarding header is resolved
    elsewhere in the ASGI ecosystem — a proxy that sets its own value after an
    untrusted client's must be the one that counts.
    """
    found = ""
    for name, value in scope.get("headers", ()):
        if name.lower() == _HEADER:
            found = value.decode("latin-1")
    return normalize_prefix(found)


class RootPathMiddleware:
    """Put the proxy's path prefix into ``scope["root_path"]``.

    Pure ASGI rather than ``BaseHTTPMiddleware``: the value has to be in the
    scope before routing, and there is no request or response body to touch.

    ``ROOT_PATH`` wins over the header — an explicit deployment setting is
    authoritative over anything a caller can send.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            prefix = normalize_prefix(settings.root_path) or _forwarded_prefix(scope)
            if prefix:
                # Mutate rather than copy: this is the ASGI convention for scope
                # (FastAPI itself assigns root_path in its own __call__), and a
                # copy would hide everything downstream writes into the scope
                # from anything outside this middleware that reads it back.
                scope["root_path"] = prefix
        await self.app(scope, receive, send)
