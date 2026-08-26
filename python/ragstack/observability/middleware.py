"""``RequestContextMiddleware`` — generate the request id, install the context,
stamp ``X-Request-Id`` on the response.

Pure ASGI, not ``BaseHTTPMiddleware``. Both existing middlewares here are pure
ASGI (``api/upload_guard.py``, ``api/root_path.py``), and ``BaseHTTPMiddleware``
wraps every request in an anyio task group — measurable overhead on the hot path
for no benefit, since this touches the scope and the response *start* message
and never the body.

.. rubric:: Ordering

Installed **last** in ``api/main.py``, which makes it the outermost middleware
this application controls (``add_middleware`` inserts at the front of the stack).
Outermost matters concretely: ``upload_guard`` hand-builds its 411/413 response
and returns without ever calling the app, so only a ``send``-wrapper *outside* it
can stamp those. ``tests/api/test_request_id_upload_guard.py`` pins that ordering
— it fails if someone reorders the ``add_middleware`` calls.

.. rubric:: The one gap, and it is not fixable by ordering

Starlette's ``ServerErrorMiddleware`` sits **above** anything ``add_middleware``
can install, and when an unhandled exception escapes it generates its 500 with
the *original* ``send`` — bypassing the wrapper below it entirely. Probed:

    GET /ok       -> 200, x-request-id present
    GET /guarded  -> 413, x-request-id present   (upload-guard shape)
    GET /boom     -> 500, x-request-id ABSENT    (unhandled exception)
                     [middleware finally] observed status=None

Two consequences, both handled:

* the ``finally`` would see ``status=None`` — no status on the *least* explained
  failures, which is the acceptance criterion failing on its hardest case. So
  there is an ``except BaseException`` branch that records
  ``status=500, outcome=unhandled`` before re-raising. An exception escaping user
  middleware is a 500 by construction.
* the header would be absent on that path. ``api/main.py`` registers an
  application-level ``Exception`` handler which stamps it from the contextvar.
  That handler runs *inside* ``ServerErrorMiddleware``, so it sets the header on
  its own response and the ``send`` wrapper here correctly never sees it.

Note the middleware does **not** ``reset()`` the contextvar in its ``finally``:
that ``Exception`` handler runs after this ``finally``, in the same task context,
and a reset would blank the id it needs. Per-request isolation comes from
installing a *fresh* context object at entry.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from typing import TYPE_CHECKING

from starlette.datastructures import MutableHeaders

from ragstack.observability.context import RequestContext, current_context, set_context

if TYPE_CHECKING:  # pragma: no cover
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger(__name__)

#: The header we read (case-insensitively) and the one we write. Same name; the
#: values are never the same value — see ``_upstream_id``.
_HEADER = b"x-request-id"
_HEADER_NAME = "X-Request-Id"

#: An inbound id is accepted for RECORDING only if it looks like an id. The
#: charset cap is the log-injection guard: a newline in the header would
#: otherwise let a caller forge whole log lines, and a 4 KB header would let
#: them flood the file. Length is bounded at 64 to match the documented schema.
#:
#: Matched with ``fullmatch`` and with no ``$``, deliberately. ``$`` matches
#: BEFORE a trailing newline, so ``re.match(r"^…$", "abc\n")`` succeeds — which
#: would have let exactly the character this guard exists to exclude through,
#: and made the 64-character cap a 65-character one. Not exploitable today
#: (``_quote`` escapes it on the way out and h11 rejects a bare LF in a header),
#: but a guard that admits the thing its own docstring forbids is worse than no
#: guard, because it is trusted.
_UPSTREAM_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")


def new_request_id() -> str:
    """A fresh id: 16 lowercase hex characters (64 bits of ``uuid4``).

    Short enough that a user can read it off a screenshot and an operator can
    retype it; wide enough that a collision inside one log-retention window is
    not a practical concern. Measured at ~2 µs.
    """
    return uuid.uuid4().hex[:16]


def _upstream_id(scope: Scope) -> str:
    """A caller-supplied ``X-Request-ID``, if it is safe to record.

    **Never returned as the request id.** We always generate our own, so the id
    on our log line and in our response is unique and ours by construction; a
    caller cannot forge one, repeat one, or make two concurrent requests
    indistinguishable in the log — which is precisely the property this whole
    mechanism exists to provide. The inbound value is kept as a separate field
    purely so a gateway that sets one can be correlated with our line.
    """
    for name, value in scope.get("headers", ()):
        if name.lower() == _HEADER:
            try:
                candidate = value.decode("latin-1")
            except UnicodeDecodeError:  # pragma: no cover - latin-1 decodes any byte
                return ""
            return candidate if _UPSTREAM_RE.fullmatch(candidate) else ""
    return ""


def _route_label(scope: Scope) -> str:
    """``"GET /v1/query"`` — the raw path, not the matched route template.

    The route template is only known after routing, which is below this
    middleware; the raw path is what an operator greps for anyway.
    """
    method = scope.get("method", "-")
    path = scope.get("path", "-")
    return f"{method} {path}"


class RequestContextMiddleware:
    """Install a per-request :class:`RequestContext` and stamp the response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Pass lifespan (and websocket) through untouched — a lifespan scope has
        # no headers and no response to stamp, and swallowing it breaks startup.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = new_request_id()
        # A FRESH object per request. A shared or module-scope one would leak
        # request N's data onto N+1; see context.py mechanic 3.
        ctx = RequestContext(
            request_id=request_id,
            upstream_request_id=_upstream_id(scope),
            route=_route_label(scope),
        )
        set_context(ctx)

        status: int | None = None
        outcome = "ok"

        async def _send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)[_HEADER_NAME] = request_id
            await send(message)

        try:
            await self.app(scope, receive, _send)
        except asyncio.CancelledError:
            # A cancellation is almost always the CLIENT going away mid-request,
            # not a server fault. Recorded separately BEFORE the generic branch
            # below, which would otherwise stamp it status=500/outcome=unhandled
            # and invent a server error out of a user closing a tab. Invisible
            # today (this line is DEBUG), but W3 promotes it to INFO/WARNING —
            # at which point a disconnect would start paging someone.
            #
            # `status` is left as observed: if the response had already started
            # the client did get that status, and if it had not, None is the
            # honest answer rather than a number we made up.
            if outcome == "ok":
                outcome = "client_disconnected"
            raise
        except BaseException:
            # ServerErrorMiddleware is above us and will render the 500 with the
            # ORIGINAL send, so `status` would otherwise stay None here — no
            # status on exactly the failures nobody can explain. An exception
            # escaping user middleware IS a 500.
            status = 500
            outcome = "unhandled"
            raise
        finally:
            # Deliberately no _ctx.reset(): main.py's Exception handler runs
            # after this and reads the context to stamp the header.
            if status is not None and status >= 500 and outcome == "ok":
                outcome = "server_error"
            log.debug(
                "request complete",
                extra={"status": status if status is not None else "-", "outcome": outcome},
            )


def stamp_request_id(headers: MutableHeaders | dict[str, str]) -> str:
    """Write the current request id into ``headers`` and return it.

    For response paths that are generated *above* this middleware and so never
    pass through its ``send`` wrapper — today that is exactly ``main.py``'s
    ``Exception`` handler. Returns ``""`` and writes nothing outside a request.
    """
    ctx = current_context()
    if ctx is None or not ctx.request_id:
        return ""
    headers[_HEADER_NAME] = ctx.request_id
    return ctx.request_id
