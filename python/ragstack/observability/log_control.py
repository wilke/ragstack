"""Change the log level of the running process, without restarting it (#427).

The owner's requirement, verbatim: *"make it set-able on demand via api call so
we don't have to reload the service."* This module is the mechanism;
``api/routers/admin_log_level.py`` is the (admin-gated) surface.

W1 split :func:`~ragstack.observability.logging_config.apply_log_level` out of
``configure_logging`` precisely so this could exist: it changes the level and
the dampen set and **does not touch handlers**, so calling it again cannot
double a line or drop the context filter. Everything here is built on that seam.

.. rubric:: Process-local, and it resets on restart

There is no persistence and there is deliberately none. A debugging session left
at DEBUG must not silently become a tenant's permanent configuration; the way to
make a level stick is ``LOG_LEVEL`` plus a restart, which is reviewable and
survives the process. The cost is that the state is invisible from outside the
process, which is why the response reports ``pid``.

Every production launch today is a **single** uvicorn process — no ``--workers``
anywhere — so one call reaches the one process serving every request. If
``--workers N`` were ever added, each worker would hold its own copy of this
state and one call would change exactly one worker, chosen by whichever accepted
the connection. That is not a hypothetical to guard against here; it is a
constraint to write down, and it is written down in the contract too.

.. rubric:: Why per-logger overrides are bounded three ways

``logging.getLogger(name)`` **creates** a logger object and puts it in a
process-global dict that is never garbage collected. An endpoint that accepts
arbitrary names is therefore an unbounded-growth path, admin gate or no admin
gate — and "an admin would never" is not a memory bound. So:

1. **charset and length** — ``^[A-Za-z0-9_.-]{1,128}$``, which also closes the
   log-injection route a newline in a logger name would open (the name is
   rendered on every line that logger emits);
2. **the name must already exist** in ``logging.Logger.manager.loggerDict``.
   This is the one that actually bounds growth: 1 and 3 bound a single request,
   but removing an override does **not** remove the logger, so a caller looping
   "add 32, reset, add 32 different" would still grow the dict without limit.
   Requiring existence makes the growth exactly **zero** — this endpoint never
   calls ``getLogger`` on a name that was not already there. It is not
   restrictive in practice: the house style is a module-level
   ``logging.getLogger(__name__)``, so every ``ragstack.*`` logger exists once
   its module is imported, and every dampened logger exists because
   ``apply_dampening`` touched it at start-up. The rejection message says so and
   suggests an ancestor, which always exists once any child does.
3. **a cap** on how many can be in force at once (:data:`MAX_LOGGER_OVERRIDES`),
   so one call cannot make the response unboundedly large either.

.. rubric:: Ordering: dampening overwrites overrides, so overrides go last

``apply_dampening`` sets the level of every name in the dampen set — to WARNING
above DEBUG, to NOTSET at DEBUG. It does that unconditionally, so an override of
``httpx`` would be silently erased by the next root-level change if the two were
applied in the wrong order, and a caller who set ``httpx=DEBUG`` at root INFO
would find it back at WARNING for no visible reason. :func:`_reapply` therefore
always runs in this order, on **every** change:

1. every logger this module has ever touched → ``NOTSET`` (so an override that
   was just dropped stops applying, rather than lingering at its old value);
2. ``apply_log_level`` → root level + the dampen set;
3. the current overrides on top.

.. rubric:: The audit line must survive the change it is auditing

An operator who finds DEBUG on in production needs to know who turned it on.
That record is only useful if it cannot be erased by the same call — and a plain
``log.warning()`` after setting the root level to ERROR would be dropped by the
level it just set. So the audit logger has **its own level pinned to WARNING**:
``getEffectiveLevel`` stops at a logger's own level and never consults root, so
the line emits whatever root is doing. ``ragstack.audit`` is also refused as an
override target, which closes the only remaining way to silence it from here.

**Never log a credential.** The audit line carries ``principal.tenant`` and
``principal.role`` and nothing else from the caller — never ``principal.token``,
never an API key. See the package docstring.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ragstack.observability.logging_config import (
    LOG_LEVEL_NAMES,
    apply_log_level,
    configured_dampen_loggers,
    resolve_log_level,
)

#: Logger for the audit trail. Pinned to WARNING by :func:`_audit_logger` so a
#: change that raises the threshold cannot hide itself.
AUDIT_LOGGER = "ragstack.audit"

#: Names accepted for a logger override. Dots for the hierarchy, dashes and
#: underscores because third-party packages use both. No whitespace and no
#: control characters — the name is printed on every line the logger emits.
LOGGER_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")

#: How many per-logger overrides may be in force at once. 32 is far more than
#: any real debugging session needs (the usual answer is one) and small enough
#: that the response stays readable.
MAX_LOGGER_OVERRIDES = 32

#: Rejected as a level everywhere in this module. On the ROOT logger, NOTSET
#: does not mean "inherit" — there is nothing to inherit from — it means "no
#: threshold", i.e. every DEBUG line from every library in the process. Nobody
#: typing it means that. Per-logger it is redundant: `loggers` has replace
#: semantics, so "stop overriding this one" is expressed by omitting it.
_NOTSET = "NOTSET"


class LogControlError(ValueError):
    """A request this module refuses. The router turns it into a 422.

    Carries the message the caller sees; every one of them names both what was
    wrong and what to do instead, because the caller is an operator mid-incident.
    """


@dataclass
class _State:
    """Runtime log state for this process. Guarded by :data:`_lock`."""

    #: The root level a PUT set, or ``None`` when configuration is in force.
    level: str | None = None
    #: name -> canonical level name, the overrides currently applied.
    overrides: dict[str, str] = field(default_factory=dict)
    #: Every name this module has ever set a level on. Reset to NOTSET before
    #: each re-apply so a dropped override stops applying. Bounded by the
    #: existence rule: it can only ever contain loggers that already existed.
    touched: set[str] = field(default_factory=set)
    changed_at: str = ""
    changed_by: str = ""


_lock = threading.Lock()
_state = _State()


def _audit_logger() -> logging.Logger:
    """The audit logger, with its own level pinned to WARNING.

    Pinned on every access rather than once at import: this module can be
    imported before ``configure_logging`` runs, and a test (or a future
    ``dictConfig``) could reset the level underneath it. Re-asserting it is one
    attribute write and removes a class of "the audit line vanished" bug.
    """
    log = logging.getLogger(AUDIT_LOGGER)
    if log.level != logging.WARNING:
        log.setLevel(logging.WARNING)
    return log


def _canonical(raw: str, *, what: str) -> str:
    """Validate one level name and return it upper-cased. Mirrors W1's parsing.

    Same ``.upper()`` and same accepted set as
    :func:`~ragstack.observability.logging_config.resolve_log_level`, including
    the documented ``warn`` — but the outcome on an unknown value is the
    opposite, and deliberately so. At start-up an unrecognised ``LOG_LEVEL``
    falls back to INFO with a warning, because a typo in the environment must
    never keep the API from booting. Here the caller is asking for something
    specific, right now, and silently doing something else would be worse than
    refusing: they would go on to read logs that are not the logs they asked for.
    """
    name = (raw or "").strip().upper()
    if not name:
        raise LogControlError(f"{what}: a level is required")
    if name == _NOTSET:
        raise LogControlError(
            f"{what}: NOTSET is not accepted. On the root logger it means 'no "
            "threshold' rather than 'inherit'; to stop overriding a logger, omit "
            "it from `loggers` (the map replaces the whole override set)."
        )
    if name not in LOG_LEVEL_NAMES:
        raise LogControlError(
            f"{what}: {raw!r} is not a known level. Accepted (case-insensitive): "
            f"{', '.join(sorted(LOG_LEVEL_NAMES - {_NOTSET}))}."
        )
    return name


def _logger_exists(name: str) -> bool:
    """Whether ``name`` is already in the process's logger registry.

    A ``PlaceHolder`` counts: it means some descendant exists, so the ancestor is
    a real node in the hierarchy and setting a level on it is meaningful. That is
    what makes ``ragstack`` or ``ragstack.stores`` usable as a broad override
    even though nothing calls ``getLogger`` on them directly.
    """
    return name in logging.Logger.manager.loggerDict


def _validate_overrides(loggers: Mapping[str, str]) -> dict[str, str]:
    """Validate a whole override map, or raise. Applies nothing.

    Every check for every entry happens here, before :func:`_reapply` touches a
    single logger, which is what makes a rejected request leave the effective
    level *exactly* as it was rather than half-applied.
    """
    if len(loggers) > MAX_LOGGER_OVERRIDES:
        raise LogControlError(
            f"loggers: {len(loggers)} overrides requested, at most "
            f"{MAX_LOGGER_OVERRIDES} may be in force at once."
        )
    validated: dict[str, str] = {}
    for name, level in loggers.items():
        if not LOGGER_NAME_RE.match(name):
            raise LogControlError(
                f"loggers: {name!r} is not a valid logger name — letters, digits, "
                "'.', '_' and '-' only, 1 to 128 characters."
            )
        if name == AUDIT_LOGGER:
            raise LogControlError(
                f"loggers: {AUDIT_LOGGER!r} cannot be overridden — it carries the "
                "audit trail for this endpoint."
            )
        if not _logger_exists(name):
            raise LogControlError(
                f"loggers: no logger named {name!r} exists in this process. A "
                "logger is created when its module is first imported, and this "
                "endpoint will not create one (that would leak a logger object "
                "per call). Try an ancestor that does exist, e.g. 'ragstack' or "
                "'httpx'."
            )
        validated[name] = _canonical(level, what=f"loggers[{name!r}]")
    return validated


def _reapply() -> None:
    """Put :data:`_state` into effect. Caller holds :data:`_lock`.

    Order is load-bearing — see the module docstring. Reset every touched logger
    first so a dropped override stops applying, then the root level and the
    dampen set, then the surviving overrides on top of both.
    """
    for name in _state.touched:
        logging.getLogger(name).setLevel(logging.NOTSET)
    apply_log_level(_state.level)
    for name, level in _state.overrides.items():
        logging.getLogger(name).setLevel(level)
    _state.touched = set(_state.overrides)


def _describe_locked() -> dict[str, object]:
    """Build the response payload. Caller holds :data:`_lock`."""
    from ragstack.config import settings

    root = logging.getLogger()
    configured_raw = str(getattr(settings, "log_level", ""))
    configured_numeric, _ = resolve_log_level(configured_raw)
    dampen = list(configured_dampen_loggers())

    loggers: list[dict[str, str]] = []
    for name in sorted(set(_state.overrides) | set(dampen)):
        loggers.append(
            {
                "name": name,
                "level": logging.getLevelName(logging.getLogger(name).level),
                "source": "override" if name in _state.overrides else "dampen",
            }
        )

    return {
        "pid": os.getpid(),
        "configured_level": configured_raw,
        "configured_level_resolved": logging.getLevelName(configured_numeric),
        "effective_level": logging.getLevelName(root.level),
        "runtime_override": _state.level is not None,
        "changed_at": _state.changed_at,
        "changed_by": _state.changed_by,
        # The POLICY, not a per-logger fact: damping is on above DEBUG and
        # released at DEBUG. An explicit override wins for the logger it names,
        # and `loggers` below reports each one's actual level, so the two fields
        # together say both what the rule is and what it produced.
        "dampening_active": bool(dampen) and root.level > logging.DEBUG,
        "dampen_loggers": dampen,
        "loggers": loggers,
        "logger_override_count": len(_state.overrides),
        "max_logger_overrides": MAX_LOGGER_OVERRIDES,
    }


def describe() -> dict[str, object]:
    """The current effective log state — what ``GET`` returns. Never mutates."""
    with _lock:
        return _describe_locked()


def _audit(action: str, before: dict[str, object], after: dict[str, object], by: str) -> None:
    """Record a change at WARNING, on the pinned audit logger.

    ``extra=`` rather than interpolation so both formatters render the fields as
    greppable ``key=value`` columns (logfmt) or JSON keys, and so the before/after
    survive a format switch.
    """
    _audit_logger().warning(
        "log level changed via API",
        extra={
            "audit": action,
            "principal": by or "-",
            "level_before": before["effective_level"],
            "level_after": after["effective_level"],
            "overrides_before": _render_overrides(before),
            "overrides_after": _render_overrides(after),
        },
    )


def _render_overrides(payload: Mapping[str, object]) -> str:
    """``name=LEVEL,name=LEVEL`` for the audit line, or ``-`` when there are none."""
    entries = payload.get("loggers")
    if not isinstance(entries, list):  # pragma: no cover - defensive
        return "-"
    return (
        ",".join(
            f"{e['name']}={e['level']}"
            for e in entries
            if isinstance(e, dict) and e.get("source") == "override"
        )
        or "-"
    )


def set_level(
    *,
    level: str | None = None,
    loggers: Mapping[str, str] | None = None,
    principal: str = "",
) -> dict[str, object]:
    """Apply a runtime log level and/or override set. Returns the new state.

    ``level=None`` leaves the root level alone; ``loggers=None`` leaves the
    override set alone; a call that passes neither is refused, because a request
    that asks for nothing is far more likely a typo than an intent.

    ``loggers`` **replaces** the whole override set when present — ``{}`` clears
    it. Raises :class:`LogControlError` (→ 422) without applying anything if any
    part of the request is invalid.
    """
    if level is None and loggers is None:
        raise LogControlError(
            "nothing to change: send `level`, `loggers`, or both (DELETE resets "
            "to the configured defaults)."
        )
    # Validate EVERYTHING before taking the lock or touching a logger.
    new_level = _canonical(level, what="level") if level is not None else None
    new_overrides = _validate_overrides(loggers) if loggers is not None else None

    with _lock:
        before = _describe_locked()
        if new_level is not None:
            _state.level = new_level
        if new_overrides is not None:
            _state.overrides = new_overrides
        _reapply()
        _state.changed_at = _now()
        _state.changed_by = principal
        after = _describe_locked()
    _audit("set", before, after, principal)
    return after


def reset(*, principal: str = "") -> dict[str, object]:
    """Drop every runtime override and re-apply the configured defaults.

    The state a restart would produce, without the restart — and without the
    caller needing to know what ``LOG_LEVEL`` or ``LOG_DAMPEN_LOGGERS`` say.
    Idempotent, and audited like a change, because it *is* one.
    """
    with _lock:
        before = _describe_locked()
        _state.level = None
        _state.overrides = {}
        _reapply()
        _state.changed_at = _now()
        _state.changed_by = principal
        after = _describe_locked()
    _audit("reset", before, after, principal)
    return after


def _now() -> str:
    """ISO-8601 UTC, seconds resolution, ``Z``-suffixed — the house stamp format."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _reset_for_tests() -> None:
    """Forget all runtime state and re-apply configuration. **Tests only.**

    The state here is process-global, so a test that raises the level would
    otherwise leak into every test that runs after it in the same process — a
    failure that shows up far from its cause. Exported (underscored) so test
    fixtures have one honest way to say "as if this process had just started",
    rather than each reaching into ``_state``.
    """
    with _lock:
        _state.level = None
        _state.overrides = {}
        _reapply()
        _state.touched = set()
        _state.changed_at = ""
        _state.changed_by = ""


__all__ = [
    "AUDIT_LOGGER",
    "LOGGER_NAME_RE",
    "MAX_LOGGER_OVERRIDES",
    "LogControlError",
    "describe",
    "reset",
    "set_level",
]
