"""One verdict per dependency, computed once and projected everywhere.

#751 principle 4: a dead dependency must appear in the caller's response, in
``/v1/health/deep``, in ``fleet status`` and in a metric — and those must be
four projections of **one fact**, never four implementations that can
disagree. This module holds that fact for the dependencies a request can route
around (reranker, embedder, llm, ingest engine, DOI resolver):

* the **request path writes** here — every real rerank call records ``ok`` or
  ``down`` with the exception it saw (``routers/query.py::_maybe_rerank``), so
  while traffic flows the verdict is the truth of the last real call and no
  probe runs at all;
* **deep health reads** here — ``routers/health_deep.py`` serves the cached
  verdict and only runs its own bounded probe (sidecar ``/ready``, ``/v1/models``)
  when the cache is stale. Two pollers inside the window share one probe
  (per-name lock), so polling can never become load (principle 2);
* PR-5 will **export** the same snapshot as ``ragstack_dependency_up``.

What this is **not**: a circuit breaker. The request path still tries the
sidecar on every request whatever the cached verdict says — the owner has not
asked for admission control, and a breaker that trips on a 10 s-old fact would
turn one bad probe into a minute of silent fallbacks. Read it; do not route on
it.

States are the contract's ``state`` enum (``deep_health_response.json``):
``ok`` — the probe or the last real call succeeded; ``degraded`` — it answered,
but not well (loading, malformed); ``down`` — no usable answer; ``disabled`` —
not configured, nothing to probe. ``disabled`` is never cached: it is a fact
about settings, re-derived on every read.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic, perf_counter

STATE_OK = "ok"
STATE_DEGRADED = "degraded"
STATE_DOWN = "down"
STATE_DISABLED = "disabled"
STATES = (STATE_OK, STATE_DEGRADED, STATE_DOWN, STATE_DISABLED)

#: How long a verdict is served without re-probing. Ten seconds: the sidecars'
#: own ``/ready`` cache (#760) is 10 s, so a shorter window here would only
#: re-read the same answer.
DEFAULT_TTL_S = 10.0

#: Hard bound on one probe. Three seconds: a dependency that cannot say "yes"
#: in 3 s is not healthy for a request that would wait on it, and the deep
#: health endpoint runs several probes.
DEFAULT_TIMEOUT_S = 3.0

#: A probe returns ``(state, detail)``; raising is ``down`` with the exception.
ProbeOp = Callable[[], Awaitable[tuple[str, str | None]]]


@dataclass(frozen=True)
class Verdict:
    """What is known about one dependency, and when it was learned."""

    name: str
    state: str
    detail: str | None = None
    latency_ms: float | None = None
    #: ``monotonic()`` at the time the verdict was recorded.
    checked_at: float = 0.0
    #: ``"request"`` when the request path recorded it, ``"probe"`` when a
    #: health probe did. On the wire as part of ``detail``, so an operator can
    #: tell "the last real rerank failed" from "the probe failed".
    source: str = "probe"

    @property
    def ok(self) -> bool:
        """The compatibility boolean: true for ``ok`` and ``disabled``."""
        return self.state in (STATE_OK, STATE_DISABLED)


class DependencyHealth:
    """Per-process verdict cache. One instance (:data:`dependency_health`)
    serves the request path, deep health and (PR-5) the exporter."""

    def __init__(
        self,
        ttl_s: float = DEFAULT_TTL_S,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._ttl = ttl_s
        self._timeout = timeout_s
        self._clock = clock
        self._verdicts: dict[str, Verdict] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.probes_run = 0

    @property
    def ttl(self) -> float:
        return self._ttl

    @property
    def timeout(self) -> float:
        return self._timeout

    # -- reading ---------------------------------------------------------- #

    def get(self, name: str) -> Verdict | None:
        """The last verdict for ``name``, however old; ``None`` if never set."""
        return self._verdicts.get(name)

    def fresh(self, name: str) -> Verdict | None:
        """The verdict for ``name`` if it is inside the TTL, else ``None``."""
        v = self._verdicts.get(name)
        if v is None or self._clock() - v.checked_at >= self._ttl:
            return None
        return v

    def snapshot(self) -> dict[str, Verdict]:
        return dict(self._verdicts)

    # -- writing ---------------------------------------------------------- #

    def record(
        self,
        name: str,
        state: str,
        detail: str | None = None,
        *,
        latency_ms: float | None = None,
        source: str = "request",
    ) -> Verdict:
        """Record what a real call just learned. The request path's entry
        point: newer evidence replaces older, whichever side produced it."""
        if state not in STATES:
            raise ValueError(f"unknown dependency state {state!r}")
        v = Verdict(
            name=name, state=state, detail=detail, latency_ms=latency_ms,
            checked_at=self._clock(), source=source,
        )
        self._verdicts[name] = v
        return v

    async def probe(self, name: str, op: ProbeOp, *, timeout: float | None = None) -> Verdict:
        """The verdict for ``name``: the cached one while fresh, otherwise one
        run of ``op`` under a hard timeout. Concurrent callers share the run.

        ``op`` returns ``(state, detail)``; an exception is ``down`` with the
        exception class and first line as detail; a timeout is ``down`` with
        ``TimeoutError``. The probe is timed and the latency recorded.
        """
        cached = self.fresh(name)
        if cached is not None:
            return cached
        lock = self._locks.setdefault(name, asyncio.Lock())
        async with lock:
            cached = self.fresh(name)
            if cached is not None:  # a concurrent caller probed while we waited
                return cached
            self.probes_run += 1
            bound = self._timeout if timeout is None else timeout
            start = perf_counter()
            try:
                state, detail = await asyncio.wait_for(op(), timeout=bound)
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                state, detail = STATE_DOWN, f"TimeoutError: no answer within {bound:g}s"
            except Exception as e:
                first = (str(e).strip().splitlines() or [""])[0]
                state, detail = STATE_DOWN, f"{type(e).__name__}: {first}".rstrip(": ")
            latency_ms = round((perf_counter() - start) * 1000, 2)
            return self.record(name, state, detail, latency_ms=latency_ms, source="probe")

    def reset(self) -> None:
        """Forget everything. For tests."""
        self._verdicts.clear()
        self._locks.clear()
        self.probes_run = 0


#: The process-wide cache. Everything reads and writes this one object.
dependency_health = DependencyHealth()
