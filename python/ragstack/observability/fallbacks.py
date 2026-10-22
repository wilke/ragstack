"""Fallback accounting — the process-level counter and the rate-limited log line.

#748 in one sentence: *the reranker answered 500 for two hours, the API fell
back to the fused order 231 times on one tenant, and nothing outside the log
file knew.* #751 PR-3 puts that fact in four places (principle 4 — one fact,
projected everywhere): the caller's response (``degraded``), deep health
(``observability.dependencies``), a counter (this module; PR-5 exports it as
``ragstack_fallback_total{component}``) and the per-request summary line
(``degraded=rerank`` via ``stages.note``).

Two things live here, both deliberately dependency-free (no Prometheus client
until PR-5 decides how to export):

* :func:`record_fallback` / :func:`fallback_total` — a plain dict counter keyed
  by component. Monotonic for the life of the process; a reader that wants a
  rate subtracts two samples.
* :class:`RateLimitedLog` — one line per ``interval`` per key, with the number
  of suppressed repeats carried on the next line that does go out. The incident
  wrote the same warning (with a traceback) 231 times in 90 minutes; that is
  noise that hides the next, different, warning. The FIRST occurrence is always
  logged, with its traceback; the repeats are counted.

Components are the contract's enum (``contracts/schemas/degraded_entry.json``);
:data:`FALLBACK_COMPONENTS` mirrors it so a typo here is a KeyError in a test
rather than a new label in a metric.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from time import monotonic

#: The components a request can degrade on — the ``component`` enum of
#: ``DegradedEntry``. ``graph`` and ``context`` are reserved: no fallback exists
#: for them today and none is invented here.
FALLBACK_COMPONENTS: tuple[str, ...] = ("rerank", "generation", "rewrite", "graph", "context")

#: The metric name PR-5 will export this counter under. Named here so the two
#: PRs agree on it without either importing the other.
FALLBACK_METRIC = "ragstack_fallback_total"

#: Upper bound on a ``DegradedEntry.reason`` — mirrors ``maxLength`` in the
#: contract, so a long exception message can never make a response invalid.
REASON_MAX_CHARS = 200

_counts: dict[str, int] = dict.fromkeys(FALLBACK_COMPONENTS, 0)
_counts_lock = threading.Lock()


def record_fallback(component: str) -> int:
    """Count one fallback on ``component``; returns the new total.

    ``component`` must be one of :data:`FALLBACK_COMPONENTS` — an unknown label
    is a programming error, not a new series.
    """
    if component not in _counts:
        raise KeyError(f"unknown fallback component {component!r}")
    with _counts_lock:
        _counts[component] += 1
        return _counts[component]


def fallback_total() -> dict[str, int]:
    """A snapshot ``{component: count}`` over every component (zeros included,
    so an exporter emits a complete series from the first scrape)."""
    with _counts_lock:
        return dict(_counts)


def reset_fallbacks() -> None:
    """Zero the counters. For tests; a running process never resets."""
    with _counts_lock:
        for key in _counts:
            _counts[key] = 0


_USERINFO_RE = re.compile(r"(\w+://)[^/\s@]+@")


def degraded_reason(exc: BaseException) -> str:
    """The ``reason`` string for a :class:`DegradedEntry`: ``ExcClass: first
    line of the message``, bounded to :data:`REASON_MAX_CHARS`, with any
    ``scheme://user:pass@`` userinfo in a URL replaced by ``scheme://***@``.

    First line only: an httpx error's message can carry several lines, and a
    traceback never belongs on the wire. The class name is always present, so
    a blank message still yields something greppable (``ConnectError:``).
    """
    text = str(exc).strip()
    first = text.splitlines()[0].strip() if text else ""
    first = _USERINFO_RE.sub(r"\1***@", first)
    reason = f"{type(exc).__name__}: {first}" if first else f"{type(exc).__name__}:"
    if len(reason) > REASON_MAX_CHARS:
        reason = reason[: REASON_MAX_CHARS - 1] + "…"
    return reason


class RateLimitedLog:
    """Emit a warning at most once per ``interval`` seconds per key.

    The first call for a key logs immediately (with ``exc_info`` if asked —
    the traceback is the diagnosis and the first occurrence is when it is
    read). Calls inside the window are counted, not logged. The next call
    outside the window logs again and appends ``(N similar warnings suppressed
    in the last Xs)``. The suppressed count is therefore never lost, only
    delayed — and ``suppressed(key)`` exposes it for a test or an exporter.

    Thread-safe by a single lock; the request path runs on one event loop, but
    the ingest pipeline does not.
    """

    def __init__(self, interval_s: float = 60.0, clock: Callable[[], float] = monotonic) -> None:
        self._interval = interval_s
        self._clock = clock
        self._lock = threading.Lock()
        # key -> (last emitted at, suppressed since then)
        self._state: dict[str, tuple[float, int]] = {}

    @property
    def interval(self) -> float:
        return self._interval

    def suppressed(self, key: str) -> int:
        """Warnings for ``key`` counted but not logged since the last line."""
        with self._lock:
            return self._state.get(key, (0.0, 0))[1]

    def warning(
        self,
        logger: logging.Logger,
        key: str,
        msg: str,
        *args: object,
        exc_info: bool = False,
    ) -> bool:
        """Log ``msg % args`` at WARNING unless a line for ``key`` went out
        within the window. Returns True when a line was emitted."""
        now = self._clock()
        with self._lock:
            last, count = self._state.get(key, (None, 0))
            if last is not None and now - last < self._interval:
                self._state[key] = (last, count + 1)
                return False
            self._state[key] = (now, 0)
        if count:
            msg = f"{msg} ({count} similar warnings suppressed in the last {self._interval:.0f}s)"
        logger.warning(msg, *args, exc_info=exc_info)
        return True

    def reset(self) -> None:
        """Forget every window. For tests."""
        with self._lock:
            self._state.clear()


#: The process-wide limiter the request path uses. One object so two call
#: sites that share a key share a window.
rate_limited = RateLimitedLog()
