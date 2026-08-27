"""Bucketed latency histograms held in this process, and the periodic rollup
line that makes p95 per collection greppable (#427 W4).

The acceptance bullet this closes, verbatim: *p95 query latency per collection is
answerable **without adding instrumentation first**.* W3 gave every request a
summary line, which answers "how long did **this** request take". It does not
answer the question the incident actually raised — *is the bound creeping?* The
request that opened #427 exceeded 30 s and nobody could say whether that was
normal-and-worsening or a one-off, because nothing kept a distribution. This
module keeps one.

.. rubric:: Buckets, and why these buckets

Boundaries (seconds)::

    0.005 0.01 0.025 0.05 0.1 0.25 0.5 1 2.5 5 10 20 30 60 120 +Inf

The tail is chosen around the thing being watched rather than copied from a
default: **20 / 30 / 60 straddles both the old 30 s ``QDRANT_TIMEOUT`` and the
interim 60 s** the two big tenants now run. So mass migrating out of the 2.5–5 s
buckets and into 10–20 and 20–30 *is* the creeping-bound signal, visible as a
shifting p95 several windows before the first user sees a 503.

.. rubric:: Percentiles from buckets are APPROXIMATE, and the line says so

A bucketed histogram knows how many observations fell in ``(2.5 s, 5 s]``; it
does not know where in that range they fell. So the honest percentile is the
bucket's **upper bound**, not an interpolation inside it — and it is rendered
under a field name that says which: ``p95_ms_le=5000.0`` reads "p95 is at most
5000 ms", never ``p95=4213.7ms``, which would be four digits of precision this
instrument does not have. The top bucket has no upper bound and renders ``inf``.

For "is p95 creeping toward the timeout" that is the right instrument, and it is
not worth trading for a reservoir: bucket-to-bucket movement is exactly the
resolution the question needs, at fixed memory and no sampling decisions.

.. rubric:: Cardinality — the one thing that would make this a leak

The series key is ``(route, collection, stage)`` and **route is allowlisted, not
free**. This matters more than it looks: ``RequestContextMiddleware`` runs above
routing and therefore knows only the *raw path*, never the matched route
template (see ``middleware._route_label``). Keying a histogram on the raw path
would mint a new series for every ``GET /v1/ingest/<job_id>`` — one per job —
and burn the whole series budget on ids nobody will ever aggregate over. So only
:data:`ALLOWED_ROUTES`, which are parameter-free by construction and are item E's
target anyway, are recorded. The **summary line still covers every route**; only
the histogram is restricted.

The remaining unbounded axis is ``collection``, which is user-created. That is
what :data:`MAX_SERIES` and :attr:`LatencyHistogram.overflow` are for: at the cap
a new series is *not* created and a counter increments, so the map stops growing
and the rollup line says out loud that it is no longer complete. Silent
truncation would be worse than the leak.

**What 512 series buys, stated in collections rather than in series**, because
the multiplier is easy to miss: a ``(route, collection)`` pair costs
``1 + <stages that ran>`` series, and a fully-wired query runs about ten — so
roughly **11 series per pair, i.e. ~46 collections** across both routes before
the cap bites. Production tenants hold one or two collections each, so this is
ample; it is written here so nobody reads "512" as "512 collections". Memory at
the cap is ``512 × ~200 B ≈ 100 KB``.

One accepted wart of the suffix match: a 404 probe at ``POST /junk/v1/query``
records into the ``(POST /v1/query, -)`` row. Cardinality-safe by construction —
it cannot mint a series — but it is mild distribution pollution on the unscoped
row, and it is named rather than left to be discovered.

.. rubric:: What a "stage" observation is, under fan-out

The middleware's ``finally`` has aggregates, not individual calls:
``StageTimings.totals()`` gives ``(summed seconds, count)`` per stage for the
whole request. What goes into the histogram is that **per-request sum**. So a
stage series is a distribution of *stage-seconds per request*, not of per-call
latency — on a five-collection query that fanned out, one observation of
``vector`` is the total vector work that request did. That is the operator-
relevant number ("how much vector time does a query cost me"), and it avoids
inventing per-observation data this layer does not have. ``wall`` is recorded as
its own synthetic stage and is the headline distribution.

.. rubric:: Per-process, and it says so

Every production launch today is a single bare uvicorn — no ``--workers``
anywhere (verified across ``apptainer/new-tenant.sh`` and the ``Makefile``). So
one process sees every request and this view is complete. The line reports
``pid`` anyway: if workers were ever added, each would keep its own histogram
and *the aggregate of the rollup lines across processes* is the ground truth,
never any one process's view.

``since_s`` is elapsed seconds since this histogram started counting, and the
counters are cumulative over exactly that window. Two rollups are therefore
diffable — subtract the ``n`` values, and ``since_s`` tells you over what span.

.. rubric:: What is deliberately NOT recorded

``client_disconnected``. Its ``wall`` is however long the client stayed, not how
long the server took, so it is not an observation of server latency; and W3's
middleware docstring already commits in writing that a closed tab "must never
page anyone or enter W4's error rate". Both halves are honoured here by not
recording the request at all.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
from bisect import bisect_left
from collections.abc import Awaitable, Callable, Mapping
from time import monotonic

from ragstack.observability.context import MISSING

log = logging.getLogger(__name__)

#: Upper bounds, in seconds, of every bucket but the last. The last bucket is
#: implicit and unbounded (``+Inf``), so a series holds ``len(BOUNDS) + 1``
#: counters. See the module docstring for why the tail is 20/30/60/120.
BOUNDS: tuple[float, ...] = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0,
)

#: Hard cap on distinct ``(route, collection, stage)`` series. Reaching it
#: increments :attr:`LatencyHistogram.overflow` instead of growing the map — see
#: the cardinality note in the module docstring.
MAX_SERIES = 512

#: The synthetic stage name under which whole-request wall time is recorded.
#: Not a member of ``stages.STAGE_NAMES``: no ``stage()`` block produces it, the
#: middleware does.
WALL_STAGE = "wall"

#: The only paths whose latency enters the histogram, as ``(method, path)``.
#: Parameter-free by construction — that is the entire selection criterion, and
#: the reason is in the module docstring's cardinality note. Adding a route here
#: is a deliberate act: it must have no path parameters, or the cap becomes a
#: countdown.
ALLOWED_ROUTES: tuple[tuple[str, str], ...] = (
    ("POST", "/v1/query"),
    ("POST", "/v1/retrieve"),
)


def route_key(method: str, path: str) -> str | None:
    """The canonical histogram route label for a request, or ``None`` to skip.

    ``None`` for everything outside :data:`ALLOWED_ROUTES`, which is most of the
    API and all of its parameterised paths.

    The match is on the path's **suffix**, not on equality, and the label
    returned is the canonical one. Today the gateway strips its
    ``/ragstack/<tenant>/api`` prefix before the request reaches us (see
    ``api/root_path.py``), so ``scope["path"]`` is already ``/v1/query`` and
    equality would do. But ``ROOT_PATH`` also supports the mounted-but-not-
    stripped arrangement, where the prefix *does* survive into the scope — and
    this middleware runs above routing, so it would see it. Under equality the
    histogram would then be silently empty on exactly the deployments the
    incident happened on, which is the failure mode that ends with someone
    concluding the feature does not work. Matching the suffix and storing the
    canonical label costs one ``endswith`` and keeps the route axis two wide
    either way.
    """
    if not path:
        return None
    trimmed = path.rstrip("/") or "/"
    for allowed_method, suffix in ALLOWED_ROUTES:
        if method != allowed_method:
            continue
        if trimmed == suffix or trimmed.endswith("/" + suffix.lstrip("/")):
            return f"{allowed_method} {suffix}"
    return None


def _bucket(seconds: float) -> int:
    """Index of the bucket ``seconds`` falls in, under ``(lower, upper]``.

    ``bisect_left`` and not ``bisect_right``: a value **exactly on a boundary**
    must land in the bucket that boundary *bounds*, not in the one above it, so
    that ``5.0`` is reported as "at most 5000 ms" rather than "at most 10000 ms".
    Intervals are therefore half-open on the left and closed on the right —
    ``(2.5, 5.0]`` — which is also how the rendered ``p95_ms_le`` reads.

    A non-finite or negative value cannot be a duration; it is clamped into the
    first bucket rather than raising, because this runs in a ``finally``.
    """
    if not math.isfinite(seconds) or seconds <= 0.0:
        return 0
    return bisect_left(BOUNDS, seconds)


class Series:
    """One ``(route, collection, stage)`` distribution: buckets, sum, count, max.

    ~200 bytes. Not thread-safe and does not need to be — every writer is on the
    one event loop, with no await between the read and the write.
    """

    __slots__ = ("counts", "total", "count", "maximum", "errors")

    def __init__(self) -> None:
        self.counts = [0] * (len(BOUNDS) + 1)
        self.total = 0.0
        self.count = 0
        self.maximum = 0.0
        #: Requests in this series that ended in a SERVER fault. Only ever
        #: incremented on the :data:`WALL_STAGE` series, because "was it an
        #: error" is a property of the request, not of one of its legs.
        self.errors = 0

    def observe(self, seconds: float) -> None:
        self.counts[_bucket(seconds)] += 1
        self.total += seconds
        self.count += 1
        if seconds > self.maximum:
            self.maximum = seconds

    def percentile(self, q: float) -> float:
        """The **upper bound of the bucket** the ``q``-quantile falls in.

        Returns ``math.inf`` when it falls in the unbounded top bucket, and
        ``0.0`` when there is nothing to report. Never interpolates inside a
        bucket: this instrument does not know where in ``(2.5 s, 5 s]`` an
        observation sat, and inventing a number there is the false precision the
        module docstring refuses.
        """
        if self.count == 0:
            return 0.0
        rank = max(1, math.ceil(q * self.count))
        seen = 0
        for index, hits in enumerate(self.counts):
            seen += hits
            if seen >= rank:
                return BOUNDS[index] if index < len(BOUNDS) else math.inf
        return math.inf  # pragma: no cover - the loop always reaches `rank`


def render_bound(seconds: float) -> str:
    """A bucket-bound percentile as it appears on the line.

    Milliseconds to one decimal, or the literal ``inf`` for the unbounded top
    bucket — because ">120 s, we don't know how much more" is the true answer
    there and a rendered ``120000.0`` would understate it.
    """
    if math.isinf(seconds):
        return "inf"
    return f"{seconds * 1000:.1f}"


class LatencyHistogram:
    """Every series this process has recorded, since it started.

    One instance lives at module scope (:func:`histogram`); the class is
    separable so tests can drive an isolated one without touching global state.
    """

    __slots__ = ("_series", "_overflow", "_started")

    def __init__(self) -> None:
        self._series: dict[tuple[str, str, str], Series] = {}
        self._overflow = 0
        self._started = monotonic()

    # -- writing ---------------------------------------------------------- #

    def _series_for(self, key: tuple[str, str, str]) -> Series | None:
        found = self._series.get(key)
        if found is not None:
            return found
        if len(self._series) >= MAX_SERIES:
            # Do NOT create it. The map stops here and the counter says so; a
            # user-created collection id is an unbounded axis and this is the
            # only thing between it and a process that grows for a week.
            self._overflow += 1
            return None
        created = Series()
        self._series[key] = created
        return created

    def record(
        self,
        route: str,
        collection: str,
        wall_seconds: float,
        stage_totals: Mapping[str, tuple[float, int]] | None = None,
        *,
        is_error: bool = False,
    ) -> None:
        """Record one request. Called from the middleware's ``finally``.

        ``stage_totals`` is ``StageTimings.totals()`` — ``name -> (summed
        seconds, observations)``; the summed seconds is what is observed (see
        the fan-out note in the module docstring). The observation *count* is
        deliberately dropped here: this series counts **requests**, so that
        ``n`` on the rollup line means the same thing on every row.
        """
        coll = collection or MISSING
        wall = self._series_for((route, coll, WALL_STAGE))
        if wall is not None:
            wall.observe(wall_seconds)
            if is_error:
                wall.errors += 1
        for name, (seconds, _count) in (stage_totals or {}).items():
            series = self._series_for((route, coll, name))
            if series is not None:
                series.observe(seconds)

    # -- reading ---------------------------------------------------------- #

    @property
    def overflow(self) -> int:
        """Observations dropped because :data:`MAX_SERIES` was reached."""
        return self._overflow

    @property
    def size(self) -> int:
        """Distinct series held. Never exceeds :data:`MAX_SERIES`."""
        return len(self._series)

    def since_seconds(self) -> float:
        """Seconds this histogram has been counting for."""
        return monotonic() - self._started

    def get(self, route: str, collection: str, stage: str) -> Series | None:
        return self._series.get((route, collection or MISSING, stage))

    def groups(self) -> list[tuple[str, str, dict[str, Series]]]:
        """``[(route, collection, {stage: Series}), …]``, one entry per line the
        rollup will emit, in a stable sorted order."""
        grouped: dict[tuple[str, str], dict[str, Series]] = {}
        for (route, coll, stage), series in self._series.items():
            grouped.setdefault((route, coll), {})[stage] = series
        return [(route, coll, stages) for (route, coll), stages in sorted(grouped.items())]

    def reset(self) -> None:
        """Drop everything and restart the window. Tests, and nothing else."""
        self._series.clear()
        self._overflow = 0
        self._started = monotonic()


_histogram = LatencyHistogram()


def latency_histogram() -> LatencyHistogram:
    """The process-wide histogram.

    Named ``latency_histogram`` and not ``histogram`` on purpose: this package's
    ``__init__`` re-exports it, and a function called ``histogram`` there would
    SHADOW the ``ragstack.observability.histogram`` submodule — so
    ``from ragstack.observability import histogram`` would silently hand back a
    function. That is a five-minute debugging session for everyone who ever
    writes it, and the collision is avoidable by not creating it.
    """
    return _histogram


def reset_for_tests() -> None:
    """Reset the process-wide histogram. Test-support only."""
    _histogram.reset()


# --------------------------------------------------------------------------- #
# The rollup line
# --------------------------------------------------------------------------- #


def emit_rollup(hist: LatencyHistogram | None = None) -> int:
    """Log one INFO line per ``(route, collection)``. Returns lines emitted.

    Emitted at INFO, which means a runtime ``LOG_LEVEL=WARNING`` (settable
    without a restart via ``PUT /v1/admin/log-level``) silences it. That is the
    right trade — an operator who asked for quiet gets quiet, and every failure
    line survives — but it is a real consequence and the runbook says so.

    Nothing is emitted when nothing has been recorded: a process serving no
    queries should not write a line every five minutes saying so.

    The route field is ``for_route``, **not** ``route``, and that is not a
    stylistic choice. ``route`` is one of ``context.CONTEXT_FIELDS``, which
    ``RequestContextFilter`` **overwrites** on every record whenever a request
    context is current. Today this only ever runs on a background task where
    there is none, so ``route`` would in fact survive — but W5's deferred
    ``GET /v1/admin/stats/latency`` would call this from inside a request, and
    every row would then be silently relabelled with the *admin* endpoint's
    route while still reading like the truth. A field the logging filter cannot
    reach costs one character and removes the trap. ``since_s`` carries
    millisecond precision for the same "make it diffable" reason the field
    exists at all: two rollups must be distinguishable by their spans.
    """
    hist = hist or _histogram
    since = hist.since_seconds()
    pid = os.getpid()
    overflow = hist.overflow
    size = hist.size
    emitted = 0

    for route, coll, stages in hist.groups():
        wall = stages.get(WALL_STAGE)
        if wall is None or wall.count == 0:  # pragma: no cover - wall is always written
            continue
        fields: dict[str, object] = {
            "for_route": route,
            "coll": coll,
            "n": wall.count,
            "errors": wall.errors,
            "p50_ms_le": render_bound(wall.percentile(0.50)),
            "p95_ms_le": render_bound(wall.percentile(0.95)),
            "max_ms": f"{wall.maximum * 1000:.1f}",
            "since_s": f"{since:.3f}",
            "pid": pid,
            "series": size,
        }
        for name, series in sorted(stages.items()):
            if name == WALL_STAGE or series.count == 0:
                continue
            fields[f"{name}_p95_ms_le"] = render_bound(series.percentile(0.95))
        if overflow:
            # On every line, not once: a truncated histogram must not be
            # something you only learn by reading the first row.
            fields["series_overflow"] = overflow
        log.info("latency rollup", extra=fields)
        emitted += 1
    return emitted


class RollupTask:
    """The periodic emitter. One instance, owned by ``api/deps.lifespan``.

    Shaped after ``collection_store.AccessTracker``: a task held on the object,
    ``start()`` idempotent and needing a running loop, ``stop()`` cancelling and
    awaiting it. Copied rather than invented because that shape is already
    proven against this application's shutdown.
    """

    __slots__ = ("_interval", "_sleep", "_emit", "_task")

    def __init__(
        self,
        interval: float,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        emit: Callable[[], int] = emit_rollup,
    ) -> None:
        self._interval = float(interval)
        # Injectable so a test can drive iterations deterministically instead of
        # waiting for wall time. A test that sleeps for a meaningful duration is
        # a test that gets marked flaky and then deleted.
        self._sleep = sleep
        self._emit = emit
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> bool:
        """Start the loop. Returns ``False`` when it is disabled or already up.

        ``interval <= 0`` **disables** the rollup — no task is created at all,
        which is what ``LATENCY_ROLLUP_SECONDS=0`` has to mean if it is to be an
        off switch rather than a busy loop.
        """
        if self._interval <= 0:
            return False
        if self.running:
            return False
        self._task = asyncio.get_running_loop().create_task(self._loop())
        return True

    async def _loop(self) -> None:
        while True:
            await self._sleep(self._interval)
            try:
                self._emit()
            except Exception:  # noqa: BLE001 — a broken rollup must not kill the loop
                log.warning("latency rollup failed", exc_info=True)

    async def stop(self) -> None:
        """Cancel the loop and wait for it to finish. Safe to call twice, safe
        to call when it never started, and never raises.

        A pending task must neither keep the process alive at shutdown nor
        explode there — so it is cancelled (not merely dropped, which would
        leave "Task was destroyed but it is pending!" on the way out) and the
        cancellation is awaited, which returns immediately.
        """
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 — shutdown must not raise
            log.warning("latency rollup task shutdown failed", exc_info=True)


_rollup: RollupTask | None = None


def start_rollup(interval: float | None = None) -> bool:
    """Start the process-wide rollup task. Returns whether one is now running.

    ``interval`` defaults to ``LATENCY_ROLLUP_SECONDS``. Never raises: an
    observability feature must not be able to fail a start-up.
    """
    global _rollup
    if interval is None:
        from ragstack.config import settings

        interval = float(getattr(settings, "latency_rollup_seconds", 300.0))
    if _rollup is not None and _rollup.running:
        return True
    _rollup = RollupTask(interval)
    try:
        return _rollup.start()
    except RuntimeError:  # pragma: no cover - lifespan always has a running loop
        log.warning("latency rollup not started: no running event loop")
        return False


async def stop_rollup() -> None:
    """Stop the process-wide rollup task, if any. Never raises."""
    global _rollup
    task, _rollup = _rollup, None
    if task is not None:
        await task.stop()


def rollup_running() -> bool:
    """Whether a process-wide rollup task is currently running."""
    return _rollup is not None and _rollup.running

