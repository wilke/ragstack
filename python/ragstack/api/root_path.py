"""Serving the API under a reverse proxy's path prefix.

The gateway publishes each deployment at ``/ragstack/<tenant>/api/...`` and
STRIPS that prefix, so the app only ever sees ``/docs`` and ``/openapi.json``.
Knowing nothing about the prefix, FastAPI rendered the Swagger page with a
ROOT-absolute ``url: '/openapi.json'`` — which through the gateway is the
gateway's own root, and 404s ("Failed to load API definition").

ASGI already has the field for this: ``scope["root_path"]`` is the prefix the
app is mounted under, and FastAPI builds the docs' ``openapi_url``, the redoc
page's ``spec-url`` and the schema's ``servers`` entry from it
(``applications.FastAPI.setup``). The prefix belongs to the DEPLOYMENT, not to
the app build, so it arrives per request as ``X-Forwarded-Prefix`` from the
proxy; ``ROOT_PATH`` pins it for a proxy that cannot be changed. Both default to
absent, which is what keeps direct-port access (``http://localhost:PORT/docs``
— how these get debugged) byte-identical to before.

The HEADER can never change routing, and that is enforced rather than assumed.
``root_path`` is not inert: Starlette's ``get_route_path`` strips it from
``scope["path"]`` whenever the path starts with it, so on the direct port a
caller sending ``X-Forwarded-Prefix: /health`` would turn its own ``GET /health``
into a 404. (Never a bypass — dependencies travel with the route, and a header
only affects the request carrying it — but it is not "untouched" either.) Since
the real proxied case is a prefix the gateway has ALREADY stripped, a
header-derived prefix that the path starts with cannot be genuine, and is
dropped; for the header, then, this really does only affect the URLs the app
EMITS. ``ROOT_PATH`` is exempt: pinning is an operator's own statement about
where the app is mounted, and the mounted-but-not-stripped proxy is a supported
ASGI arrangement where that stripping is the correct behaviour. Which is why a
``ROOT_PATH`` that collides with a real route (``/v1``) 404s the API — see
``config.Settings.root_path``.
"""
from __future__ import annotations

import logging
import re

from starlette.datastructures import MutableHeaders
from starlette.routing import get_route_path
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ragstack.config import settings

log = logging.getLogger(__name__)

# One leading slash, then a conservative path charset. Deliberately excludes
# ':', '?', '#', '\\', '%' and whitespace — see normalize_prefix. ``\Z`` and not
# ``$``: ``$`` also matches before a trailing newline, which would admit a
# prefix carrying one.
_PREFIX_RE = re.compile(r"^/[A-Za-z0-9._~/-]*\Z")
_MAX_PREFIX_LEN = 256

_HEADER_NAME = "X-Forwarded-Prefix"
_HEADER = _HEADER_NAME.lower().encode("latin-1")

# The paths whose BODY depends on X-Forwarded-Prefix (FastAPI's defaults, as set
# in api/main.py): the docs pages embed the schema URL, and the schema embeds the
# `servers` entry. Nothing caches in front of the API today, but a response that
# varies by a request header and does not say so is a cache poisoning waiting to
# be introduced.
_VARY_PATHS = frozenset({"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"})


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

    Traversal is rejected per SEGMENT, not as a substring: ``".." in prefix``
    would both miss ``/a/./b`` and reject a legitimate ``/v1..2``.
    """
    prefix = value.strip().rstrip("/")
    if not prefix or len(prefix) > _MAX_PREFIX_LEN:
        return ""
    if not _PREFIX_RE.match(prefix):
        return ""
    # prefix starts with "/", so split()[0] is the empty string before it; an
    # empty segment after that is a "//", which is the protocol-relative case.
    if any(seg in ("", ".", "..") for seg in prefix.split("/")[1:]):
        return ""
    return prefix


def validate_setting(value: str) -> str | None:
    """Resolve ``ROOT_PATH`` to a pin: ``None`` = unset, ``""`` = "no proxy".

    Three outcomes, because two of them look alike and must not behave alike:
    unset means the header decides, a valid value PINS the prefix, and an
    invalid value means no prefix at all. That last one is the point: falling
    back to the header would hand a caller the very thing the operator tried to
    fix, so a broken setting fails closed and loudly instead.

    Called once (at import), not per request — validating a constant on every
    request is what let its result look request-dependent in the first place.
    """
    if not value.strip():
        return None
    prefix = normalize_prefix(value)
    if not prefix:
        log.warning(
            "ROOT_PATH=%r is not a usable path prefix and is being ignored; "
            "the API will emit root-relative docs/schema URLs and "
            "X-Forwarded-Prefix will NOT be consulted. Expected one leading "
            "slash and a path-only charset, e.g. /ragstack/asm/api",
            value,
        )
        return ""
    return prefix


# Resolved ONCE, at import: ROOT_PATH is deployment configuration, so its
# validity cannot depend on who is calling. Tests override this module attribute
# (there is no other way for a setting read at import to change).
_CONFIGURED = validate_setting(settings.root_path)


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


def _would_reroute(path: str, prefix: str) -> bool:
    """Would ``get_route_path`` strip ``prefix`` off ``path``?

    Mirrors ``starlette.routing.get_route_path`` exactly: it strips only on a
    whole-segment match, so ``/v1x`` under a ``/v1`` prefix routes unchanged.
    """
    return path == prefix or path.startswith(prefix + "/")


class RootPathMiddleware:
    """Put the proxy's path prefix into ``scope["root_path"]``.

    Pure ASGI rather than ``BaseHTTPMiddleware``: the value has to be in the
    scope before routing, and there is no request or response body to touch.

    ``ROOT_PATH`` wins over the header — an explicit deployment setting is
    authoritative over anything a caller can send — and a ROOT_PATH that was
    rejected as invalid still wins, as "no proxy" (see ``validate_setting``).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            prefix = _CONFIGURED
            if prefix is None:
                prefix = _forwarded_prefix(scope)
                # A prefix the path still carries cannot have come from the
                # gateway, which strips it — and honouring it would re-route the
                # caller's own request (a self-inflicted 404). Drop it, so the
                # header is what this module claims it is: emitted URLs only.
                if prefix and _would_reroute(scope.get("path", ""), prefix):
                    prefix = ""
            if prefix:
                # Mutate rather than copy: this is the ASGI convention for scope
                # (FastAPI itself assigns root_path in its own __call__), and a
                # copy would hide everything downstream writes into the scope
                # from anything outside this middleware that reads it back.
                scope["root_path"] = prefix
            # get_route_path, not scope["path"]: under a pinned ROOT_PATH the
            # docs live at <prefix>/docs, and it is the ROUTE that decides
            # whether the body embeds the prefix.
            if scope["type"] == "http" and get_route_path(scope) in _VARY_PATHS:
                send = _vary_on_prefix(send)
        await self.app(scope, receive, send)


def _vary_on_prefix(send: Send) -> Send:
    """Announce that this response body depends on ``X-Forwarded-Prefix``."""

    async def _send(message: Message) -> None:
        if message["type"] == "http.response.start":
            MutableHeaders(scope=message).append("vary", _HEADER_NAME)
        await send(message)

    return _send
