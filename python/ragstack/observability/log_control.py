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

.. rubric:: The root level does not reach ``uvicorn.*``

Uvicorn's loggers set ``propagate=False`` and carry their own handlers, so they
consult their own level and never root's. Verified both directions: setting the
root level to CRITICAL leaves uvicorn's access line printing, and setting it to
DEBUG does not enable uvicorn debug. Naming ``uvicorn``/``uvicorn.error``/
``uvicorn.access`` in ``loggers`` *does* reach them, and is the only thing that
does — worth knowing, because an operator reading ``effective_level: DEBUG``
would reasonably expect otherwise.

The side benefit is worth stating: a complete "denial of observability" is
therefore **not** reachable through this endpoint. Whatever level is set here,
the audit line below survives it.

One qualification since #427 W3, because the earlier wording named uvicorn's
access log as the other survivor and that is no longer the whole story:
``access_log_replaced`` now defaults to TRUE, so ``configure_logging`` sets
``uvicorn.access.disabled`` and that log is not printing in the first place. Its
replacement — the per-request summary line from
``observability.middleware`` — is an ordinary ``ragstack`` logger and **is**
governed by the root level set here. Setting WARNING therefore drops the success
lines, which is the intent (every failure line is WARNING); setting CRITICAL
drops them too. The audit line, not the access log, is the floor.

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

.. rubric:: TTL / auto-revert, and why it exists

Measured while reviewing the endpoint: httpcore emits roughly **15 log lines per
outbound HTTP call** at DEBUG (its connect/send/receive trace), and DEBUG
deliberately *releases* the dampen set. A single ``/v1/query`` makes 5 outbound
calls minimum and up to ~14 on the multi-collection path, so it goes from about
**3 lines to 75–210**. Without a TTL, DEBUG left on survives until the process
restarts — weeks, on a tenant.

The failure mode is not an attacker. The endpoint is admin-gated and audited. It
is an admin who turned DEBUG on to investigate something, was interrupted, and
never came back. :data:`MAX_TTL_SECONDS` is 24h for that reason: a TTL is for a
session an operator is *present* for, and a day bounds the blast radius of a
forgotten DEBUG. Anything longer wants ``LOG_LEVEL`` plus a restart, which is
reviewable and survives the process.

**Supersede, stated once so it cannot be got wrong.** Every :func:`set_level`
cancels any pending revert *before* it applies, and arms a new one only if the
new call carries a TTL. Two overlapping TTLs therefore cannot exist, and the
first timer can never fire later and clobber the second change. The corollary is
that a follow-up PUT which omits ``ttl_seconds`` **disarms** the expiry the
earlier one armed — deliberate (the expiry belongs to the change that armed it,
and "omitted means no expiry" is the documented default we were told not to
change), and visible both in the response and on the audit line, which carries
the ``ttl_seconds``/``expires_at`` columns on *every* change including the ones
that clear them. :func:`reset` cancels a pending revert too.

**Belt and braces: cancel AND a staleness epoch.** Every arm/cancel bumps
:attr:`_State.epoch` and the timer closes over the value it was armed with, so a
timer that fires anyway — because ``cancel()`` lost a race, or because a future
caller forgot to cancel — finds a stale epoch and does nothing. The explicit
``cancel()`` is what frees the handle; the epoch is what makes "never clobbers a
newer change" a property of the code rather than of the scheduler's goodwill.

**A timer handle, not a Task** (:class:`Timebase`). ``loop.call_later`` returns a
``TimerHandle``: it cannot keep the loop alive, it is discarded silently when the
loop closes (no *"Task was destroyed but it is pending"* at shutdown), and it is
cancellable synchronously — which matters because everything in this module is
sync under a :class:`threading.Lock`. A ``Task`` would need awaiting and
cancelling from the lifespan's ``finally`` to shut down cleanly; this needs
nothing, and :func:`cancel_pending_revert` is wired into that ``finally`` anyway
because the house pattern is to stop what you started
(``AccessTracker.start``/``stop``) and because it makes shutdown assertable.

``call_later`` needs a running loop *on this thread*, which is exactly the
condition for it to be correct (an asyncio loop's timers are not thread-safe).
The router is ``async def``, so a PUT always satisfies it. A TTL requested with
no running loop is **refused** rather than silently dropped or handed to a
``threading.Timer``: arming nothing while reporting success is precisely the
failure this feature exists to prevent, and a second concurrency mechanism is a
second thing to get wrong.

The clock and the scheduler both live behind :class:`Timebase` so tests can
substitute a controllable one — a countdown that has to be *observed* decreasing
cannot be tested by sleeping.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from ragstack.observability.logging_config import (
    LOG_LEVEL_NAMES,
    apply_log_level,
    configured_dampen_loggers,
    resolve_log_level,
)

#: This module's own logger — for the one thing worth saying that is not an audit
#: line (a cancel that failed against a dead loop, at DEBUG).
log = logging.getLogger(__name__)

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

#: Shortest accepted TTL. Zero would mean "revert immediately", which is a
#: request to do nothing dressed up as a request to do something; a caller who
#: means that has ``DELETE``.
MIN_TTL_SECONDS = 1

#: Longest accepted TTL: 24 hours. The bound is a judgement, so here is the
#: judgement. A TTL is a safety net for a debugging session an operator is
#: PRESENT for, and the thing it is netting is expensive — at DEBUG one
#: ``/v1/query`` writes 75–210 log lines instead of 3. A day is longer than any
#: real session and short enough that a forgotten DEBUG costs one day rather than
#: the weeks a tenant process actually runs for. A change that genuinely needs to
#: outlive a day is a ``LOG_LEVEL`` edit plus a restart: reviewable, visible in
#: the environment, and it survives the process — which this never does.
MAX_TTL_SECONDS = 86_400

#: Rejected as a level everywhere in this module. On the ROOT logger, NOTSET
#: does not mean "inherit" — there is nothing to inherit from — it means "no
#: threshold", i.e. every DEBUG line from every library in the process. Nobody
#: typing it means that. Per-logger it is redundant: `loggers` has replace
#: semantics, so "stop overriding this one" is expressed by omitting it.
_NOTSET = "NOTSET"

#: Names refused as per-logger override targets because they do not name a
#: per-logger thing.
#:
#: ``logging.getLogger("root")`` returns **the actual root logger** — CPython
#: short-circuits on ``name == root.name`` before consulting the manager — so an
#: override of ``"root"`` would move the root level while :data:`_state.level`
#: stayed ``None`` and the response kept reporting ``runtime_override: false``.
#: It is also invisible to the existence rule the moment any dependency creates
#: a ``root.<something>`` logger, since that registers a ``PlaceHolder`` under
#: the key ``"root"``.
#:
#: Not reachable today (nothing in the tree creates a ``root.*`` logger, and the
#: live server refuses the name for want of that placeholder) and never an audit
#: hole — the change is still audited, and ``level_before``/``level_after`` read
#: ``root.level`` directly, so they would have told the truth either way. Refused
#: explicitly anyway: it costs one comparison, and "reports the wrong thing about
#: itself" is not a property to leave resting on a dependency's logger names. Use
#: the ``level`` field, which is what actually owns the root level.
_RESERVED_NAMES = frozenset({"root"})


class LogControlError(ValueError):
    """A request this module refuses. The router turns it into a 422.

    Carries the message the caller sees; every one of them names both what was
    wrong and what to do instead, because the caller is an operator mid-incident.
    """


class Cancellable(Protocol):
    """Whatever :meth:`Timebase.call_later` hands back. ``TimerHandle`` satisfies it."""

    def cancel(self) -> None: ...  # pragma: no cover - structural type


class Timebase:
    """The clock and the timer this module reads time and schedules through.

    One seam, two reasons.

    *Testability.* A TTL whose countdown must be **observed** decreasing, and a
    supersede rule whose whole content is "the stale timer must not fire",
    cannot be tested by sleeping — a sleeping test is slow, flaky, and proves
    only that the happy path happened to win a race. Tests swap in a fake that
    advances the clock and fires due timers on command, which turns "the first
    timer must not clobber the second change" into an assertion instead of a
    hope: fire the stale timer *deliberately* and show nothing moved.

    *Honesty about clocks.* The countdown is computed on :func:`time.monotonic`,
    which no NTP step or suspended VM can move. ``expires_at`` is wall-clock
    because a human reads it; it is explicitly documented as the derived,
    weaker of the two.
    """

    def monotonic(self) -> float:
        """The clock the deadline is measured on. Never steps."""
        return time.monotonic()

    def utcnow(self) -> datetime:
        """Wall clock, for the human-readable stamps only."""
        return datetime.now(UTC)

    def check_schedulable(self) -> None:
        """Raise :class:`LogControlError` if a timer cannot be armed right now.

        Called **before** anything is applied, which is what keeps a TTL request
        atomic with the rest of the body: if the expiry cannot be armed, the
        level must not change either. Refusing beats the alternatives — silently
        applying a change with no expiry is exactly the forgotten-DEBUG failure
        this feature exists to prevent, and falling back to a
        ``threading.Timer`` would add a second concurrency mechanism to reason
        about for a case that cannot arise in the server (the router is
        ``async def``, so a PUT always runs on the loop thread).
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError as exc:
            raise LogControlError(
                "ttl_seconds: no asyncio event loop is running on this thread, so "
                "an auto-revert cannot be armed and nothing was applied. This "
                "cannot happen on the API (the handler is async); it means the "
                "control module was driven directly from synchronous code."
            ) from exc

    def call_later(self, delay: float, callback: Callable[[], None]) -> Cancellable:
        """Arm ``callback`` for ``delay`` seconds from now on the running loop.

        A ``TimerHandle``, deliberately, not a ``Task`` — see the module
        docstring: it cannot keep the loop alive, it dies quietly with the loop,
        and it cancels synchronously.
        """
        return asyncio.get_running_loop().call_later(delay, callback)


#: Swapped by tests (``monkeypatch.setattr(log_control, "_timebase", fake)``).
#: Module-level rather than injected per call so the production path carries no
#: parameter that exists only for tests.
_timebase: Timebase = Timebase()


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
    #: The TTL armed on the change now in force, or ``None``. Reported as sent.
    ttl_seconds: int | None = None
    #: Monotonic deadline of the pending auto-revert, or ``None``. The authority
    #: for the countdown: no wall-clock step can move it.
    deadline: float | None = None
    #: Wall-clock rendering of that deadline, for humans. ``""`` when unarmed.
    expires_at: str = ""
    #: The pending timer, kept only so it can be cancelled.
    handle: Cancellable | None = None
    #: Bumped on every arm and every cancel. A timer closes over the value it was
    #: armed with and does nothing if it no longer matches, so a stale timer can
    #: never clobber a newer change even if its ``cancel()`` did not take.
    epoch: int = 0


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


def _canonical_ttl(raw: object) -> int:
    """Validate one ``ttl_seconds`` and return it, or raise.

    Bounds are checked HERE rather than with pydantic ``ge=``/``le=`` on the
    request model, for two reasons. It keeps the refusal in the atomic
    pre-validation block with every other semantic check, so an out-of-range TTL
    leaves the effective level exactly as it was. And it keeps the 422 body the
    single-sentence ``{"detail": ...}`` shape the endpoint's own refusals use,
    naming the bound and what to do instead, rather than pydantic's list of error
    objects. The type check stays with pydantic, which already answers 422 for a
    fractional or non-numeric value.
    """
    # bool is an int in Python; `ttl_seconds: true` is not a duration. Pydantic
    # rejects it before we get here — this is the direct-call path's guard.
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise LogControlError(
            f"ttl_seconds: {raw!r} is not an integer number of seconds "
            f"({MIN_TTL_SECONDS}..{MAX_TTL_SECONDS})."
        )
    if not MIN_TTL_SECONDS <= raw <= MAX_TTL_SECONDS:
        raise LogControlError(
            f"ttl_seconds: {raw} is out of range — {MIN_TTL_SECONDS} to "
            f"{MAX_TTL_SECONDS} (24h). A TTL is a safety net for a debugging "
            "session someone is present for; to hold a level for longer, set "
            "LOG_LEVEL and restart, which is reviewable and survives the process."
        )
    return raw


def _cancel_revert_locked() -> None:
    """Disarm any pending auto-revert. Caller holds :data:`_lock`. Never raises.

    Bumps :attr:`_State.epoch` first, so a timer already in flight (or one whose
    ``cancel`` does not take) is stale the instant this returns — the cancel
    frees the handle, the epoch is what guarantees the behaviour.

    ``cancel()`` is swallowed on failure because the one caller that can hit a
    dead loop is the shutdown hook, and shutdown must not raise.
    """
    handle, _state.handle = _state.handle, None
    _state.epoch += 1
    _state.ttl_seconds = None
    _state.deadline = None
    _state.expires_at = ""
    if handle is not None:
        try:
            handle.cancel()
        except Exception:  # noqa: BLE001 — the loop may already be gone
            log.debug("log_control: cancelling the pending revert failed", exc_info=True)


def _arm_revert_locked(ttl: int) -> None:
    """Arm the auto-revert ``ttl`` seconds out. Caller holds :data:`_lock` and has
    already cancelled whatever was pending (and called
    :meth:`Timebase.check_schedulable`)."""
    _state.epoch += 1
    epoch = _state.epoch
    _state.ttl_seconds = ttl
    _state.deadline = _timebase.monotonic() + ttl
    _state.expires_at = _stamp(_timebase.utcnow() + timedelta(seconds=ttl))
    _state.handle = _timebase.call_later(ttl, lambda: _on_expiry(epoch))


def _on_expiry(epoch: int) -> None:
    """Revert to the configured defaults because a TTL ran out.

    Runs on the event loop. Does exactly what ``DELETE`` does — the end state a
    restart would produce — and is audited as ``expired`` rather than ``reset``
    so a level change nobody typed is distinguishable from one an operator asked
    for. ``changed_by`` is cleared for the same reason: a non-empty
    ``changed_at`` with an empty ``changed_by`` is the signature of the process
    changing its own level. The principal that *armed* it is on the audit line,
    which is the durable record.

    The epoch check is the supersede guarantee. A timer armed by an earlier call
    is stale the moment a later call arms or cancels, and a stale timer that
    fires anyway must not undo the newer change.
    """
    with _lock:
        if epoch != _state.epoch:
            return
        before = _describe_locked()
        _cancel_revert_locked()
        _state.level = None
        _state.overrides = {}
        _reapply()
        _state.changed_at = _now()
        _state.changed_by = ""
        after = _describe_locked()
    _audit(
        "expired",
        before,
        after,
        # Who to ask about a change nobody typed: the principal that armed it.
        str(before.get("changed_by") or ""),
        ttl_seconds=before.get("ttl_seconds"),
        expires_at=str(before.get("expires_at") or ""),
        message="log level auto-reverted: the TTL on the last change expired",
    )


def cancel_pending_revert() -> None:
    """Drop any pending auto-revert without reverting. For the app's shutdown.

    Wired into ``deps.lifespan``'s ``finally``. Strictly it is not needed — a
    ``TimerHandle`` cannot keep the loop alive and is discarded when the loop
    closes — but the house pattern is that whatever starts background work stops
    it (``AccessTracker.start``/``stop``), and a shutdown hook is the difference
    between "shutdown is fine" being asserted and being assumed.

    Not audited: the process is going away, and a restart reverts the level
    anyway. An audit line claiming a change nobody will observe would be noise.
    """
    with _lock:
        _cancel_revert_locked()


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
        if name in _RESERVED_NAMES:
            raise LogControlError(
                f"loggers: {name!r} does not name a per-logger target — "
                "logging.getLogger('root') IS the root logger. Use the `level` "
                "field, which owns the root level and reports it."
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

    # Rounded UP, so a 600s TTL reads 600 the instant it is armed rather than
    # 599; clamped at 0 rather than going negative, because a deadline that has
    # passed while the callback waits its turn on a busy loop is a moment, not a
    # state to report as -1.
    remaining: int | None = None
    if _state.deadline is not None:
        remaining = max(0, math.ceil(_state.deadline - _timebase.monotonic()))

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
        # The pending auto-revert. An operator has to be able to see that the
        # level will change under them; nothing else here would say so.
        "auto_revert_pending": _state.deadline is not None,
        "ttl_seconds": _state.ttl_seconds,
        "expires_at": _state.expires_at,
        "expires_in_seconds": remaining,
        "max_ttl_seconds": MAX_TTL_SECONDS,
    }


def describe() -> dict[str, object]:
    """The current effective log state — what ``GET`` returns. Never mutates."""
    with _lock:
        return _describe_locked()


def _audit(
    action: str,
    before: dict[str, object],
    after: dict[str, object],
    by: str,
    *,
    ttl_seconds: object = None,
    expires_at: str = "",
    message: str = "log level changed via API",
) -> None:
    """Record a change at WARNING, on the pinned audit logger.

    ``extra=`` rather than interpolation so both formatters render the fields as
    greppable ``key=value`` columns (logfmt) or JSON keys, and so the before/after
    survive a format switch.

    ``audit=`` is the field that says *what kind* of change this was, and the
    values are load-bearing: ``set``, ``reset`` (an operator asked for the
    defaults) and ``expired`` (a TTL ran out and the process reverted itself).
    Somebody will one day have to explain a level change nobody typed, and
    ``audit=expired`` is the whole of that explanation.

    ``ttl_seconds``/``expires_at`` appear on **every** line, ``-`` when there is
    no expiry — which is what makes a superseding PUT that silently disarms an
    earlier TTL visible in the log rather than only in a response nobody kept.
    """
    _audit_logger().warning(
        message,
        extra={
            "audit": action,
            "principal": by or "-",
            "level_before": before["effective_level"],
            "level_after": after["effective_level"],
            "overrides_before": _render_overrides(before),
            "overrides_after": _render_overrides(after),
            "ttl_seconds": ttl_seconds if ttl_seconds is not None else "-",
            "expires_at": expires_at or "-",
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
    ttl_seconds: object = None,
    principal: str = "",
) -> dict[str, object]:
    """Apply a runtime log level and/or override set. Returns the new state.

    ``level=None`` leaves the root level alone; ``loggers=None`` leaves the
    override set alone; a call that passes neither is refused, because a request
    that asks for nothing is far more likely a typo than an intent.

    ``loggers`` **replaces** the whole override set when present — ``{}`` clears
    it. Raises :class:`LogControlError` (→ 422) without applying anything if any
    part of the request is invalid.

    ``ttl_seconds`` auto-reverts this change to the **configured defaults** after
    that many seconds — the end state ``reset`` produces, not a restoration of
    what was in force before. ``None`` means no expiry, which is what this
    function has always done and is the unchanged default.

    **Every call supersedes the last, expiry included.** A pending revert is
    cancelled before anything is applied, and a new one is armed only if this
    call carries a TTL. So two TTLs can never overlap, an earlier timer can never
    fire later and clobber this change — and, the corollary worth saying out
    loud, a follow-up call that omits ``ttl_seconds`` **disarms** the expiry the
    earlier one armed. The response says so immediately
    (``auto_revert_pending``) and so does the audit line.
    """
    if level is None and loggers is None:
        if ttl_seconds is not None:
            raise LogControlError(
                "ttl_seconds modifies a change; it is not one. Send it alongside "
                "`level` and/or `loggers` — to put an expiry on the override "
                "already in force, re-send that override with `ttl_seconds`."
            )
        raise LogControlError(
            "nothing to change: send `level`, `loggers`, or both (DELETE resets "
            "to the configured defaults)."
        )
    # Validate EVERYTHING before taking the lock or touching a logger — the TTL
    # included, and including whether a timer CAN be armed. An expiry that
    # silently failed to arm would leave exactly the forgotten-DEBUG state this
    # feature exists to prevent, so it must fail the whole request instead.
    new_level = _canonical(level, what="level") if level is not None else None
    new_overrides = _validate_overrides(loggers) if loggers is not None else None
    new_ttl = _canonical_ttl(ttl_seconds) if ttl_seconds is not None else None
    if new_ttl is not None:
        _timebase.check_schedulable()

    with _lock:
        before = _describe_locked()
        # First: this change supersedes whatever the last one armed.
        _cancel_revert_locked()
        if new_level is not None:
            _state.level = new_level
        if new_overrides is not None:
            _state.overrides = new_overrides
        _reapply()
        _state.changed_at = _now()
        _state.changed_by = principal
        if new_ttl is not None:
            _arm_revert_locked(new_ttl)
        after = _describe_locked()
    _audit(
        "set",
        before,
        after,
        principal,
        ttl_seconds=after["ttl_seconds"],
        expires_at=str(after["expires_at"]),
    )
    return after


def reset(*, principal: str = "") -> dict[str, object]:
    """Drop every runtime override and re-apply the configured defaults.

    The state a restart would produce, without the restart — and without the
    caller needing to know what ``LOG_LEVEL`` or ``LOG_DAMPEN_LOGGERS`` say.
    Idempotent, and audited like a change, because it *is* one.

    **Cancels any pending auto-revert**, since it produces that revert's end
    state right now: leaving the timer armed would put a second, pointless
    "change" in the audit trail some minutes later. Audited as ``reset``, which
    is what tells an operator's reset apart from the ``expired`` line a TTL
    writes.
    """
    with _lock:
        before = _describe_locked()
        _cancel_revert_locked()
        _state.level = None
        _state.overrides = {}
        _reapply()
        _state.changed_at = _now()
        _state.changed_by = principal
        after = _describe_locked()
    _audit("reset", before, after, principal)
    return after


def _stamp(when: datetime) -> str:
    """ISO-8601 UTC, seconds resolution, ``Z``-suffixed — the house stamp format."""
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _now() -> str:
    """Now, stamped. Reads the clock through :data:`_timebase` so a test that
    controls time controls ``changed_at`` and ``expires_at`` together."""
    return _stamp(_timebase.utcnow())


def _reset_for_tests() -> None:
    """Forget all runtime state and re-apply configuration. **Tests only.**

    The state here is process-global, so a test that raises the level would
    otherwise leak into every test that runs after it in the same process — a
    failure that shows up far from its cause. Exported (underscored) so test
    fixtures have one honest way to say "as if this process had just started",
    rather than each reaching into ``_state``.

    Cancelling the pending revert is not optional here: a timer armed in one test
    and left armed would fire during some later test and move the root level out
    from under it, which is the same leak one file further along.
    """
    with _lock:
        _cancel_revert_locked()
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
    "MAX_TTL_SECONDS",
    "MIN_TTL_SECONDS",
    "LogControlError",
    "Timebase",
    "cancel_pending_revert",
    "describe",
    "reset",
    "set_level",
]
