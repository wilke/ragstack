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

.. rubric:: The summary line (#427 W3)

One line per request, emitted from the ``finally`` so it arrives on **failure**
as well as on success — that is the acceptance criterion of #427 in a sentence,
and before W3 a successful request logged nothing at all. It answers, in one
greppable row: which request (``rid``), whose (``tenant``/``role``), which leg
(the ``*_ms`` stage fields), how much of the bound it consumed (``wall_ms``), and
how many other requests the process was serving (``inflight``).

Level by outcome, and the split is deliberate:

=======================  =======  =======================================
outcome                  level    status
=======================  =======  =======================================
``ok`` (< 500)           INFO     as observed
``server_error``         WARNING  as observed
``unhandled``            WARNING  500 (stamped by the branch below)
``client_disconnected``  INFO     as observed; ``None`` renders as ``-``
=======================  =======  =======================================

So ``LOG_LEVEL=WARNING`` — settable at runtime now via
``PUT /v1/admin/log-level``, without a restart — drops every success and keeps
every failure. ``client_disconnected`` is INFO on purpose: a user closing a tab
is not a server fault and must never page anyone or enter W4's error rate, but a
disconnect during a 30-second query is evidence in exactly the scenario this
issue exists for, so it is not DEBUG either.

``status`` renders as ``-`` when none was ever observed rather than being
replaced by a plausible number: an invented status on the least-explained
failures is the failure mode this line exists to end.

Two honest limits on the numbers, stated here because this is where they are
read rather than only in ``stages.py`` where they are computed:

* **``self_ms`` is an UPPER BOUND on Python-layer time**, not a measurement of
  it. It subtracts each external stage's mean, does not subtract a stage name it
  does not recognise, and does not see the dependency layer's own round trips
  (``security.provider.authenticate`` on the bearer path). Read it as "no more
  than this", and see ``stages.py`` before quoting it at ADR-0006's Go trigger.
* **A 5xx that has already started, followed by a client disconnect, logs
  ``client_disconnected`` at INFO** — the CancelledError branch only claims the
  outcome while it is still ``ok``, but the ``>= 500`` promotion runs after it
  and does not override what that branch set. The error is then below
  ``LOG_LEVEL=WARNING``. Rare (the client has to go away between the response
  start and the end of the body) and deliberate — attributing a disconnect to the
  server would be the worse error, and the store-failure line for such a request
  is a WARNING on its own — but it is a real hole in "WARNING keeps every
  failure", so it is written down rather than left to be found.

.. rubric:: The latency histogram (#427 W4)

The same ``finally`` also feeds ``observability.histogram``, which keeps a
bucketed distribution so that *"is the bound creeping?"* is answerable from a
log rather than from a new instrumentation project. Two asymmetries with the
summary line, both in ``_record_latency`` and both deliberate: the histogram
records only an **allowlist of parameter-free routes** (the raw path this
middleware sees would otherwise mint a series per job id), and it does not
record ``client_disconnected`` at all.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from time import perf_counter
from typing import TYPE_CHECKING

from starlette.datastructures import MutableHeaders

from ragstack.observability.context import (
    MISSING,
    RequestContext,
    current_context,
    set_context,
)
from ragstack.observability.histogram import latency_histogram, route_key
from ragstack.observability.stages import StageTimings

if TYPE_CHECKING:  # pragma: no cover
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger(__name__)

#: Requests currently inside this middleware, process-wide.
#:
#: The incident that opened #427 could not answer "was the box busy when that
#: search blew through its 30-second bound?", and this is the field that does.
#: Process-global rather than per-tenant on purpose: ``tenant_max_concurrency``
#: defaults to ``0`` and ``TenantQuota.slot`` then keeps no counter at all, so a
#: per-tenant number would render ``-`` on every deployment we have, including
#: all four production tenants.
#:
#: A plain ``int`` with no lock: every request runs on one event loop and there
#: is no await between the read and the write, so the increment is atomic with
#: respect to anything that could observe it.
_inflight = 0

#: Outcomes that mean the SERVER failed, and so are logged at WARNING to survive
#: ``LOG_LEVEL=WARNING``. ``client_disconnected`` is pointedly absent — see the
#: level table in the module docstring.
_FAULT_OUTCOMES = frozenset({"server_error", "unhandled"})

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


def _ms(seconds: float) -> str:
    """Milliseconds to one decimal, as a string.

    Formatted here rather than handed to the formatter as a float because
    ``str(0.0412...)`` renders seventeen significant figures of noise on a line
    a human is meant to scan, and because the stage fields are ``sum/count``
    strings anyway — one shape for every duration on the line.
    """
    return f"{seconds * 1000:.1f}"


def _log_summary(
    ctx: RequestContext,
    status: int | None,
    outcome: str,
    wall: float,
    concurrent: int,
) -> None:
    """Emit the one line per request. See the module docstring for the level
    table and for what each field is for.

    ``rid``, ``tenant``, ``role`` and ``route`` are deliberately **not** in
    ``extra``: :class:`~ragstack.observability.context.RequestContextFilter`
    puts them on every record in the process, and duplicating them here would
    collide with the formatter's own reserved-name handling.
    """
    fields: dict[str, str | int] = {
        "status": status if status is not None else MISSING,
        "outcome": outcome,
        "wall_ms": _ms(wall),
        "inflight": concurrent,
    }
    stages = ctx.stages
    if stages is not None:
        fields["self_ms"] = _ms(stages.self_seconds(wall))
    if ctx.collection:
        fields["coll"] = ctx.collection
    if ctx.collections:
        fields["colls"] = ctx.collections
    if ctx.qsha:
        fields["qsha"] = ctx.qsha
    if stages is not None:
        fields.update(stages.fields())

    level = logging.WARNING if outcome in _FAULT_OUTCOMES else logging.INFO
    log.log(level, "request complete", extra=fields)


def _record_latency(
    hist_route: str | None,
    ctx: RequestContext,
    outcome: str,
    wall: float,
) -> None:
    """Feed the in-process histogram (#427 W4). Skipped for most requests.

    Two exclusions, both deliberate:

    * ``hist_route is None`` — every route outside ``histogram.ALLOWED_ROUTES``.
      This middleware knows only the raw path, so a histogram keyed on it would
      mint one series per ``GET /v1/ingest/<job_id>``. The summary line above
      still covers every route; only the *distribution* is restricted.
    * ``client_disconnected`` — the wall time of a request whose client walked
      away measures how long the client stayed, not how long the server took.
      Recording it would both poison the latency distribution and put a closed
      tab into the error rate, which the level table above promises it never
      does.

    Wrapped in ``except Exception`` because this is the last statement of a
    ``finally``: an exception raised here would REPLACE the exception the
    ``unhandled`` branch is in the middle of propagating, turning a diagnosable
    500 into a mystery inside the observability code. The recording itself is
    integer and float arithmetic with no failure mode; the guard is for the
    version of this function somebody writes later.
    """
    if hist_route is None or outcome == "client_disconnected":
        return
    try:
        latency_histogram().record(
            hist_route,
            ctx.collection,
            wall,
            ctx.stages.totals() if ctx.stages is not None else None,
            is_error=outcome in _FAULT_OUTCOMES,
        )
    except Exception:  # noqa: BLE001 — see the docstring; never mask the request's own error
        log.debug("latency histogram recording failed", exc_info=True)


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

        global _inflight

        request_id = new_request_id()
        # A FRESH object per request, accumulator included. A shared or
        # module-scope one would leak request N's timings onto N+1 and every
        # number after that would be quietly wrong; see context.py mechanic 3
        # and stages.py mechanic 3.
        ctx = RequestContext(
            request_id=request_id,
            upstream_request_id=_upstream_id(scope),
            route=_route_label(scope),
            stages=StageTimings(),
        )
        set_context(ctx)

        # Resolved ONCE, at entry, and `None` for all but two routes — see
        # `histogram.route_key`. Doing it here rather than in the `finally`
        # keeps the allowlist check off the path of the exception handling, and
        # means every other route pays two string compares for the whole
        # feature.
        hist_route = route_key(scope.get("method", ""), scope.get("path", ""))

        status: int | None = None
        outcome = "ok"
        _inflight += 1
        started = perf_counter()

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
            # and invent a server error out of a user closing a tab. W1 wrote
            # this branch while the line was still DEBUG, precisely so that W3's
            # promotion would not turn every closed tab into a 500 in the log
            # and, via W4's rollup, into the error rate. The line is INFO now
            # (see the level table above) and a disconnect pages nobody.
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
            wall = perf_counter() - started
            if status is not None and status >= 500 and outcome == "ok":
                outcome = "server_error"
            # Sampled BEFORE the decrement, so the value counts this request
            # too: "N requests were in flight as this one finished", which is
            # what an operator reading a slow line wants to know. Decremented
            # after, so a raise on the way out cannot leak a permanent +1.
            concurrent = _inflight
            _inflight -= 1
            _log_summary(ctx, status, outcome, wall, concurrent)
            _record_latency(hist_route, ctx, outcome, wall)


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
