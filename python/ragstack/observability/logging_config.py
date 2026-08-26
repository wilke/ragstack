"""Configure the root logger — the thing nothing in this repo did.

Before this module, the API installed **no** root handler and set **no** level.
Consequences, both verified rather than assumed:

* every ``log.info()`` under ``ragstack.*`` was discarded (the root logger sits
  at ``WARNING`` by default and had no handler to emit through anyway);
* ``log.warning()`` and above fell through to ``logging.lastResort``, a bare
  ``StreamHandler(stderr)`` with **no formatter** — so those lines carried no
  timestamp, no level and no logger name;
* ``LOG_LEVEL`` — defined in config, echoed by ``GET /v1/config``, written by
  tenant provisioning — configured nothing at all.

Format: **logfmt by default**, ``LOG_FORMAT=json`` for the alternative. logfmt
because the only consumer today is a person on the host running ``tail`` and
``grep``; there is no log shipper and no aggregator deployed anywhere, so JSON's
payoff is deferred while its cost (unreadable when tailed) is immediate. The
JSON branch is built and tested **now**, not left aspirational, so the day a
shipper appears the switch is a config change rather than a project.

.. rubric:: The level is parsed defensively, and this is not decorative

``logging.Logger.setLevel`` raises ``ValueError: Unknown level: 'info'`` on a
lowercase name. The deployed tenants ``dev`` and ``demo`` both have
``LOG_LEVEL=info``, written by ``apptainer/new-tenant.sh``, and ``.env.example``
documents ``debug | info | warn | error`` — all lowercase, and ``warn`` is not
even a stdlib level name (``WARN`` is). Reading ``settings.log_level`` straight
into ``setLevel`` would therefore have **crashed those two APIs at startup** —
the exact opposite of this module's headline fix. So: upper-case it, check it
against a known set, and on anything unrecognised **fall back to INFO and warn**.
A bad log level must never be fatal.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from collections.abc import Sequence
from typing import Any

from ragstack.observability.context import CONTEXT_FIELDS, MISSING, RequestContextFilter

#: Level names accepted in ``LOG_LEVEL``, after upper-casing. ``WARN`` is the
#: stdlib alias for ``WARNING`` and is included because ``.env.example`` has
#: documented ``warn`` since before this module existed.
LOG_LEVEL_NAMES = frozenset(
    {"CRITICAL", "FATAL", "ERROR", "WARNING", "WARN", "INFO", "DEBUG", "NOTSET"}
)

#: Fallback dampen set, used only when ``settings.log_dampen_loggers`` is
#: unavailable. The real default lives in ``config.py`` so an operator can change
#: it with ``LOG_DAMPEN_LOGGERS`` and no code change.
DEFAULT_DAMPEN_LOGGERS = ("httpx", "httpcore", "elastic_transport", "urllib3")

#: Marks the handler this module installs so a second call replaces it rather
#: than stacking a duplicate (which would double every line).
_HANDLER_NAME = "ragstack.observability"

#: ``LogRecord`` attributes present on every record regardless of context. Used
#: by the JSON formatter to decide what is a caller-supplied ``extra``.
_RESERVED = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime", "taskName", *CONTEXT_FIELDS}


def resolve_log_level(raw: str | None) -> tuple[int, str | None]:
    """``(numeric level, warning to emit or None)`` for a configured value.

    Never raises. An unknown value yields ``INFO`` plus a warning naming what was
    rejected, because a typo in ``LOG_LEVEL`` must degrade the logs, not take
    the API down.
    """
    name = (raw or "").strip().upper()
    if not name:
        return logging.INFO, None
    if name not in LOG_LEVEL_NAMES:
        return (
            logging.INFO,
            f"LOG_LEVEL={raw!r} is not a known level "
            f"({', '.join(sorted(LOG_LEVEL_NAMES))}); falling back to INFO",
        )
    level = logging.getLevelName(name)
    if not isinstance(level, int):  # pragma: no cover - unreachable via the set above
        return logging.INFO, f"LOG_LEVEL={raw!r} did not resolve to a level; using INFO"
    return level, None


def _utc(secs: float | None = None) -> time.struct_time:
    """UTC time tuple, for ``logging.Formatter.converter``.

    Wrapped rather than assigning ``time.gmtime`` straight onto the class: the
    stdlib gets away with ``converter = time.localtime`` because that is a C
    builtin and so does not bind as a method, and mypy reads ``gmtime``'s
    zero-argument overload and rejects the assignment. ``staticmethod`` of a
    plain function is the honest form of the same thing.
    """
    return time.gmtime(secs)


def _quote(value: str) -> str:
    """logfmt value quoting: bare when it is a simple token, quoted otherwise.

    Anything with whitespace, a quote, or a control character is JSON-quoted,
    which also escapes the newline that would otherwise let a message forge a
    second log line.
    """
    if value == "":
        return '""'
    if any(c.isspace() or c in '"\\=' or ord(c) < 0x20 for c in value):
        return json.dumps(value, ensure_ascii=False)
    return value


class LogfmtFormatter(logging.Formatter):
    """``TS LEVEL logger rid=… tenant=… msg="…"``.

    The message is rendered through the standard ``%``-style machinery first, so
    the ~219 existing ``log.warning("x %s", y)`` call sites keep working exactly
    as written and simply gain the context columns.
    """

    # UTC, and the `converter` is not optional. logging.Formatter defaults to
    # time.localtime, so a `Z` suffix on a local-time stamp is a silent lie —
    # here it was five hours off the gateway's own `Date:` header, on the one
    # field an operator uses to line two logs up. See test_timestamps_are_utc.
    converter = staticmethod(_utc)
    default_time_format = "%Y-%m-%dT%H:%M:%S"
    default_msec_format = "%s.%03dZ"

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        parts = [
            self.formatTime(record),
            record.levelname,
            record.name,
        ]
        for name in CONTEXT_FIELDS:
            value = getattr(record, name, MISSING)
            if value and value != MISSING:
                parts.append(f"{name}={_quote(str(value))}")
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                parts.append(f"{key}={_quote(str(value))}")
        parts.append(f"msg={_quote(message)}")
        line = " ".join(parts)
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        if record.stack_info:
            line += "\n" + self.formatStack(record.stack_info)
        return line


class JsonFormatter(logging.Formatter):
    """One JSON object per line — the ``LOG_FORMAT=json`` branch.

    Built and tested now rather than later so that flipping the setting is a
    config change and not a project. Same fields as the logfmt branch, same
    ``%``-style message rendering — and the same UTC ``converter``, for the same
    reason.
    """

    converter = staticmethod(_utc)
    default_time_format = "%Y-%m-%dT%H:%M:%S"
    default_msec_format = "%s.%03dZ"

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for name in CONTEXT_FIELDS:
            value = getattr(record, name, MISSING)
            if value and value != MISSING:
                payload[name] = value
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value if isinstance(value, (str, int, float, bool)) else str(value)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def make_formatter(log_format: str | None) -> logging.Formatter:
    """The formatter for ``LOG_FORMAT``. Unknown values fall back to logfmt —
    same "never fatal over a config typo" rule as the level."""
    return JsonFormatter() if (log_format or "").strip().lower() == "json" else LogfmtFormatter()


def apply_dampening(level: int, loggers: Sequence[str]) -> None:
    """Pin ``loggers`` to WARNING while ``level`` is INFO or higher; release them
    at DEBUG. Safe to call repeatedly, and safe to call with a *different* set
    than last time — the release branch uses ``NOTSET``, which restores
    inheritance rather than guessing a previous value.

    .. rubric:: Why damp at all

    Every logger in the default set is ``NOTSET``, so it inherits the root
    level. Before #427 root sat at WARNING with **no handler**, which made these
    libraries silent *by accident*. Raising root to INFO — the whole point of
    this module — un-mutes all of them at once, and they sit on a path this API
    takes several times per request.

    A single ``/v1/query`` makes **5 outbound HTTP calls minimum** (query
    embedding, Qdrant, Elasticsearch, cross-encoder rerank, the LLM), 6 with
    query rewriting, and up to **~14** on the multi-collection path. That is one
    ``httpx`` INFO line each. So the single summary line #427 exists to produce
    would arrive at a signal-to-noise ratio of **1:5, worst case 1:14** — and
    #427 exists precisely because the logs were unusable. Trading one kind of
    unusable for another is not a fix.

    This also **falsifies the plan's "net line count is unchanged" claim**, which
    accounted only for uvicorn's access log and not for the libraries that root's
    new level un-mutes.

    .. rubric:: What damping costs, and the one thing W3 must preserve

    The honest argument against: because :class:`RequestContextFilter` is on the
    root *handler*, third-party lines **do** carry the ``rid``. They are
    correlatable, not pure noise. But at INFO ``httpx`` contributes only method,
    URL and status, and a failure already produces a better line from our own
    store-failure path.

    Its one genuinely unique contribution is **which endpoint served the call**.
    The embedding fleet is six vLLM endpoints, and "was it always the same slow
    one?" is a real question that damping would otherwise make unanswerable.
    **W3 must put the resolved endpoint on the relevant stage tag** so that
    information survives this.

    .. rubric:: DEBUG is not a credential-exposure vector — verified, not assumed

    Measured against a real socket with ``httpx`` 0.28.1 / ``httpcore`` 1.0.9,
    sending both an ``Authorization: Bearer …`` and an ``X-API-Key``: a full
    round trip at DEBUG logs ``send_request_headers.started request=<Request
    [b'GET']>`` — the **repr**, which omits headers entirely. Neither credential
    appeared, and neither did the header *names*.

    One caveat, stated because it is real: **response** headers ARE logged at
    DEBUG (``receive_response_headers.complete return_value=(…, [(b'Server',
    …)])``). And this is a dependency-version-sensitive observation, not a
    guarantee — it is a property of these libraries' current logging, so re-check
    it rather than trusting this paragraph after a major upgrade.
    """
    for name in loggers:
        logging.getLogger(name).setLevel(
            logging.WARNING if level > logging.DEBUG else logging.NOTSET
        )


def apply_log_level(
    level: str | None = None,
    *,
    dampen: Sequence[str] | None = None,
) -> tuple[int, str | None]:
    """Apply a log level (and the dampen set that goes with it). Re-appliable.

    Split out of :func:`configure_logging` deliberately: everything here is
    "state that can change while the process runs", while installing a handler
    and choosing a formatter is start-up shape. A future admin endpoint that
    changes the level without a restart calls **this**, not
    ``configure_logging`` — it does not touch handlers, so it cannot double a
    line or lose the context filter, and calling it twice with different values
    is well defined (see :func:`apply_dampening` on why the release branch uses
    ``NOTSET``).

    That endpoint is **not** part of #427 W1 — it is a new route and a contract
    change, and it comes after. This function only makes it cheap.

    Returns ``(numeric level, warning to emit or None)``; the caller decides
    where the warning goes, because during start-up there may be no handler yet.
    """
    from ragstack.config import settings

    if level is None:
        level = settings.log_level
    if dampen is None:
        # `is None`, not a truthiness test: an EMPTY list is a valid, meaningful
        # value — "damp nothing" — and falling back to the default there would
        # make LOG_DAMPEN_LOGGERS= silently do the opposite of what it says.
        # Only a genuinely absent setting falls back.
        configured = getattr(settings, "log_dampen_loggers", None)
        dampen = DEFAULT_DAMPEN_LOGGERS if configured is None else configured

    numeric, warning = resolve_log_level(level)
    logging.getLogger().setLevel(numeric)
    apply_dampening(numeric, dampen)
    return numeric, warning


def configure_logging(
    *,
    level: str | None = None,
    log_format: str | None = None,
    quiet_uvicorn_access: bool | None = None,
    dampen: Sequence[str] | None = None,
) -> logging.Handler:
    """Install the root stderr handler and honour ``LOG_LEVEL``. Idempotent.

    Arguments default to the corresponding settings; they exist so tests can
    drive this without mutating global settings.

    Idempotent by construction: the handler is named, and a second call replaces
    the one it installed rather than stacking a duplicate. That matters because
    this is called at import time of ``api.main`` and again by anything that
    reconfigures — a stacked handler doubles every line.

    The level and dampen set are applied through :func:`apply_log_level`, which
    is separately callable so that changing the level later does not mean
    rebuilding the handler.

    Returns the installed handler.
    """
    from ragstack.config import settings

    if log_format is None:
        log_format = getattr(settings, "log_format", "logfmt")
    if quiet_uvicorn_access is None:
        quiet_uvicorn_access = bool(getattr(settings, "access_log_replaced", False))

    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, "name", None) == _HANDLER_NAME:
            root.removeHandler(existing)

    handler = logging.StreamHandler(sys.stderr)
    handler.name = _HANDLER_NAME
    handler.setFormatter(make_formatter(log_format))
    # On the HANDLER, not on a logger: a logger-level filter is not consulted
    # for records propagating up from child loggers, so most of ragstack.* would
    # silently miss the context.
    handler.addFilter(RequestContextFilter())
    root.addHandler(handler)

    # AFTER the handler is installed, so the bad-LOG_LEVEL warning below has
    # somewhere to go — the absence of a handler is the very bug this fixes.
    _, warning = apply_log_level(level, dampen=dampen)

    # uvicorn configures its own loggers with propagate=False and its own
    # handlers, so root's handler never sees their records. Attach the filter to
    # theirs too — otherwise a uvicorn error line is the one line in the file
    # with no context columns.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        for h in logging.getLogger(name).handlers:
            if not any(isinstance(f, RequestContextFilter) for f in h.filters):
                h.addFilter(RequestContextFilter())

    if quiet_uvicorn_access:
        # W3's summary line is a strict superset of uvicorn's access line
        # (method, path, status, plus id, tenant, timings), so this keeps the
        # net line count unchanged rather than doubling it. Default OFF in W1,
        # where that superset line does not exist yet.
        logging.getLogger("uvicorn.access").disabled = True

    if warning:
        # Emitted AFTER the handler is installed, or the warning about the
        # broken LOG_LEVEL would itself be swallowed by the very absence of a
        # handler this function exists to fix.
        logging.getLogger(__name__).warning("%s", warning)

    return handler
