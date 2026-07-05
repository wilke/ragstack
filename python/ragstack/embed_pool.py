"""Multi-endpoint embedder pool.

Fans embedding requests across several backend endpoints (e.g. vLLM replicas on
the H200s) with **admission-control routing** — round-robin with skip-if-high
and backpressure driven by each endpoint's server-side queue depth — plus a
global concurrency cap, health tracking, and failover. ``PooledEmbedder``
satisfies the Embedder protocol, so it drops in behind ``BatchingEmbedder``
exactly like a single embedder; with one endpoint configured the plain
single-endpoint embedder is used instead.

**Why admission control (and not least-loaded / weighted-random).** ~16
INDEPENDENT OS processes each build their own pool over the SAME small set of
throughput-limited vLLM endpoints (~1 in-flight request per endpoint, large
64x4080-token batches). Any routing that ranks by a ~seconds-stale global
minimum makes all 16 processes pick the SAME endpoint in lockstep and flood it
to 100k+ queued while the rest sit idle (the "herd"). The fix is per-process,
coordinated only through the shared SERVER signal ``vllm:num_requests_waiting``:

1. **Round-robin** with a per-process START OFFSET (from ``os.getpid()``), so N
   processes don't all begin at endpoint 0.
2. **Skip-if-high:** scan in round-robin order and skip any endpoint whose
   *load* (server ``waiting`` + this process's local in-flight ``active``, so a
   burst doesn't pile onto an endpoint it just fed before the next poll) exceeds
   ``max_waiting``; take the first acceptable one.
3. **Soft backpressure, then degrade — never livelock.** If EVERY endpoint is
   over the ceiling, take ONE short *jittered* breather and re-poll to let a
   queue drain and de-sync the herd. If they're still all over, DEGRADE to the
   least-loaded endpoint and submit anyway. A hard "wait until one is under the
   ceiling" is WRONG here: on a FLOPS-bound fleet the queues are permanently
   non-empty, so that condition may never occur — every worker would block, the
   whole fleet would stall, and ``--batch-retries`` would resubmit in lockstep
   (a thundering retry herd). That exact failure stalled 15/16 shards last run.
4. **Genuine-unavailability signal:** :class:`EmbedStalled` is raised only when
   NO endpoint is healthy (fleet down), not when they're merely busy — so
   ``--batch-retries`` backs off on a real outage while a saturated-but-up fleet
   keeps draining at its least-loaded endpoint.

Backends without /metrics keep ``waiting == 0`` forever, so skip-high never
fires and this degrades to plain round-robin (+ local ``active``).
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import time

import httpx

from ragstack.embedders import make_embedder

log = logging.getLogger(__name__)

# 4xx codes that mean "retry on another endpoint", not "bad input": a busy /
# rate-limited or momentarily-unavailable endpoint. Every other 4xx is the
# input's fault (it fails the same way on every endpoint) and must propagate so
# BatchingEmbedder can quarantine it instead of pointlessly failing over.
_RETRIABLE_STATUS = frozenset({408, 425, 429})


class EmbedStalled(RuntimeError):
    """No endpoint is healthy AND none can be admitted — the fleet is *down*, not
    merely busy.

    Raised only for genuine unavailability: a saturated-but-up fleet is NOT a
    stall (the pool degrades to its least-loaded endpoint and keeps draining).
    A subclass of ``RuntimeError`` so existing ``except RuntimeError`` callers
    (and ``retry.is_transient_error``, which matches the "temporarily
    unavailable" message) treat it as transient/retriable: a whole-fleet outage
    is not the input's fault, so ``--batch-retries`` should back off and re-feed
    rather than quarantine."""


class Endpoint:
    """One backend endpoint: its embedder, health/metrics URLs, and live load."""

    __slots__ = ("embedder", "health_url", "metrics_url", "healthy", "active", "waiting")

    def __init__(self, embedder, health_url: str, metrics_url: str | None = None) -> None:
        self.embedder = embedder
        self.health_url = health_url
        self.metrics_url = metrics_url
        self.healthy = True  # optimistic; demoted on failure, restored by health checks
        self.active = 0  # local in-flight requests from this process
        self.waiting = 0  # server-side queue depth (vllm:num_requests_waiting); 0 = unknown


def _parse_waiting(metrics_text: str) -> int:
    """Extract ``vllm:num_requests_waiting`` from Prometheus /metrics text.

    Returns 0 when the metric is absent (non-vLLM backend, e.g. the BGE sidecar),
    so routing transparently falls back to least-local-load for those."""
    for line in metrics_text.splitlines():
        if line.startswith("vllm:num_requests_waiting{"):
            try:
                return int(float(line.rsplit(" ", 1)[1]))
            except (ValueError, IndexError):
                return 0
    return 0


class PooledEmbedder:
    """Route ``embed`` across endpoints with admission control, failover, health.

    - **Admission-control routing:** round-robin (per-process random offset) that
      skips any endpoint whose *load* — server queue ``vllm:num_requests_waiting``
      plus this process's local in-flight ``active`` — is over ``max_waiting``.
      When EVERY endpoint is over, it takes one short jittered breather + re-poll
      and then DEGRADES to the least-loaded endpoint rather than blocking (a hard
      wait livelocks a permanently-queued fleet). See the module docstring.
    - **Backpressure:** a global semaphore also caps total in-flight requests, so
      a large ingest can't open unbounded concurrent calls across the fleet.
    - **Failover:** a 5xx / network failure demotes the endpoint and retries on
      another. A retriable 4xx (429/408/425 — busy or rate-limited) also fails
      over but does *not* demote, so a momentarily busy replica isn't sidelined.
      Every other 4xx is a bad-input error (same on every endpoint), so it
      propagates unchanged — letting ``BatchingEmbedder`` quarantine the input
      instead of pointlessly failing over.
    - **Health:** endpoints are re-probed lazily at most every ``health_interval``
      seconds, so a recovered endpoint rejoins the rotation.
    """

    def __init__(
        self,
        endpoints: list[Endpoint],
        *,
        http: httpx.AsyncClient,
        max_concurrency: int = 8,
        health_interval: float = 30.0,
        metrics_interval: float = 1.0,
        max_waiting: int = 64,
    ) -> None:
        if not endpoints:
            raise ValueError("PooledEmbedder requires at least one endpoint")
        self._eps = endpoints
        self._http = http
        self._sem = asyncio.Semaphore(max(1, max_concurrency))
        self._health_interval = health_interval
        # Admission control polls each endpoint's vllm:num_requests_waiting every
        # `metrics_interval`s (short — ~1s — so decisions ride a fresh-ish signal but
        # never block the hot path on a GET) and skips any endpoint whose load
        # (server `waiting` + local `active`) is over `max_waiting`. The ceiling is
        # a *soft* preference: skip a hot endpoint while any peer has slack, but
        # under universal load degrade to least-loaded rather than refuse. It sits
        # ABOVE natural steady-state queue depth (with ~16 procs over 8 endpoints a
        # busy endpoint legitimately parks tens of requests) so it flags the lopsided
        # 100k-herd outlier, not normal operation — 16 was below steady state and
        # fired constantly, which is what drove the stall collapse.
        self._metrics_interval = metrics_interval
        self._max_waiting = max_waiting
        # Per-PROCESS RNG seeded by pid: decorrelates the ~16 independent ingest
        # processes even when their pids are near-consecutive (pid % n clusters;
        # Random(pid).randrange hashes evenly). Drives both the round-robin START
        # offset (so they don't all begin at endpoint 0) and the breather jitter (so
        # a shared saturation moment doesn't re-sync their re-polls).
        self._rng = random.Random(os.getpid())
        self._rr = self._rng.randrange(len(endpoints))
        # Start the clock now so the first probe waits a full interval rather than
        # firing on the first request.
        self._last_health = time.monotonic()
        self._health_lock = asyncio.Lock()
        self._last_metrics = time.monotonic()
        self._metrics_lock = asyncio.Lock()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # Refresh health + server-queue metrics *outside* the semaphore: a slow probe
        # round must not hold a backpressure permit hostage while it waits on GETs.
        await self._maybe_refresh_health()
        await self._maybe_refresh_metrics()
        async with self._sem:
            tried: set[int] = set()
            last_exc: Exception | None = None
            for _ in range(len(self._eps)):
                # Admission control: round-robin to the first endpoint under the
                # load ceiling; if all are over, one jittered breather then degrade
                # to least-loaded (never blocks a busy-but-up fleet). Raises
                # EmbedStalled only when NO endpoint is healthy; returns None only
                # when every endpoint has already been tried this call (failover
                # exhausted) so the loop falls through to the real error below.
                ep = await self._admit(tried)
                if ep is None:
                    break
                ep.active += 1
                try:
                    return await ep.embedder.embed(texts)
                except (httpx.HTTPError, OSError) as e:
                    status = (
                        e.response.status_code
                        if isinstance(e, httpx.HTTPStatusError) and e.response is not None
                        else None
                    )
                    if (
                        status is not None
                        and 400 <= status < 500
                        and status not in _RETRIABLE_STATUS
                    ):
                        # Bad input, not an endpoint fault — let the caller handle it.
                        raise
                    # Transient: a busy/rate-limited endpoint (retriable 4xx) or a
                    # 5xx / network fault. Demote only on a real fault — a 429 means
                    # "busy", not "down", so don't sideline a healthy replica for a
                    # full health_interval.
                    if status is None or status >= 500:
                        ep.healthy = False
                    last_exc = e
                    tried.add(id(ep))
                    log.warning(
                        "embedding endpoint %s failed (%s), failing over: %s",
                        ep.health_url,
                        status or "network",
                        e,
                    )
                finally:
                    ep.active -= 1
            raise RuntimeError("all embedding endpoints failed") from last_exc

    async def embed_isolated(
        self, texts: list[str]
    ) -> tuple[list[list[float] | None], int]:
        """Like :meth:`embed` but isolates poison inputs across the fan-out.

        Mirrors ``BatchingEmbedder.embed_isolated`` for the pooled path so the
        ingest backstop (``scripts/ingest_jsonl.py`` ``_embed_drop_bad``, which
        prefers ``embed_isolated`` when present) covers multi-endpoint fleets too.

        Returns ``(vectors, quarantined)`` where ``vectors`` is aligned to
        ``texts`` with ``None`` for each quarantined (genuinely-bad, 4xx) input.
        A bad input propagates out of :meth:`embed` as an ``HTTPStatusError`` (the
        pool routes non-retriable 4xx straight through instead of failing over), so
        we bisect the offending sub-batch to quarantine it. Infrastructure
        failures — 5xx / network / all-endpoints-down — surface from :meth:`embed`
        as a ``RuntimeError`` (or a retriable-status error) and PROPAGATE
        unchanged, so ``--resume`` / ``--batch-retries`` re-feed the batch with no
        data loss. Order-preserving.
        """
        out: list[list[float] | None] = [None] * len(texts)
        quarantined = await self._embed_isolated_range(
            texts, list(range(len(texts))), out
        )
        return out, quarantined

    async def _embed_isolated_range(
        self, texts: list[str], indices: list[int], out: list[list[float] | None]
    ) -> int:
        try:
            vecs = await self.embed([texts[i] for i in indices])
        except httpx.HTTPStatusError as e:
            status = e.response.status_code if e.response is not None else None
            # Only a genuine bad-input 4xx reaches here (the pool fails over on
            # retriable 4xx / 5xx and raises RuntimeError when exhausted, which we
            # deliberately do NOT catch). Bisect to quarantine the culprit.
            if status is None or not 400 <= status < 500:
                raise
            if len(indices) == 1:
                log.warning(
                    "quarantining unembeddable input #%d (HTTP %d)", indices[0], status
                )
                return 1
            mid = len(indices) // 2
            left = await self._embed_isolated_range(texts, indices[:mid], out)
            right = await self._embed_isolated_range(texts, indices[mid:], out)
            return left + right
        for i, v in zip(indices, vecs, strict=True):
            out[i] = v
        return 0

    @staticmethod
    def _load(ep: Endpoint) -> int:
        """Effective queue depth this process should attribute to ``ep``: the
        server-reported ``waiting`` plus our own in-flight ``active``.

        Counting ``active`` closes the burst blind spot — the semantic breakpoint
        pass fans one doc into many concurrent ``embed`` calls that all read the
        SAME ~1s-stale ``waiting`` snapshot before vLLM has moved any into its
        queue. Adding ``active`` makes the 2nd..Nth picks in a burst see the load
        the 1st just placed, so they spread instead of dogpiling one endpoint."""
        return ep.waiting + ep.active

    def _candidates(self, exclude: set[int]) -> list[Endpoint]:
        """Endpoints still in play this call: healthy-and-not-excluded, or — as a
        last resort when none look healthy — any not-excluded (a stale health flag
        shouldn't strand a request if the endpoint is actually up)."""
        healthy = [e for e in self._eps if e.healthy and id(e) not in exclude]
        return healthy or [e for e in self._eps if id(e) not in exclude]

    async def _admit(self, exclude: set[int]) -> Endpoint | None:
        """Round-robin to an admissible endpoint; degrade to least-loaded under
        universal load; never hard-block a busy fleet.

        Order of preference:
        1. First endpoint in round-robin order whose load is <= ``max_waiting``.
        2. If all are over: ONE short jittered breather + re-poll (lets a queue
           drain and de-syncs the herd), then retry (1).
        3. Still all over, but some are healthy → DEGRADE to the least-loaded
           healthy endpoint and submit anyway (keep the pipe full — refusing
           forever is the livelock that collapsed the last run).
        4. No candidates left (failover exhausted this call) → ``None`` so
           :meth:`embed` raises its real underlying error.
        5. No *healthy* endpoint at all (fleet down) → :class:`EmbedStalled`."""
        if not self._candidates(exclude):
            return None  # failover exhausted — let embed() raise the real error
        ep = self._select(exclude)
        if ep is not None:
            return ep
        # Every candidate is over the ceiling. Take one bounded, jittered breather
        # and re-poll — NOT a wait-until-under-ceiling loop, which would livelock on
        # a permanently-queued fleet.
        await asyncio.sleep(self._metrics_interval * (0.5 + self._rng.random()))
        await self._refresh_waiting()
        if not self._candidates(exclude):
            return None
        ep = self._select(exclude)
        if ep is not None:
            return ep
        # Still universally over the ceiling. If nothing is healthy, the fleet is
        # down — surface a retriable stall. Otherwise degrade to least-loaded.
        healthy = [e for e in self._eps if e.healthy and id(e) not in exclude]
        if not healthy:
            raise EmbedStalled(
                "embedding fleet unavailable: no healthy endpoint and all over "
                f"max_waiting={self._max_waiting} (temporarily unavailable)"
            )
        log.warning(
            "embedding fleet busy: all %d endpoints over max_waiting=%d; "
            "degrading to least-loaded",
            len(healthy),
            self._max_waiting,
        )
        ep = min(healthy, key=self._load)
        self._rr = (self._eps.index(ep) + 1) % len(self._eps)
        return ep

    def _select(self, exclude: set[int]) -> Endpoint | None:
        """First endpoint (round-robin order) whose load is under the ceiling, or
        ``None``.

        ``None`` means "nothing under the ceiling right now" — either failover has
        excluded everything, or every remaining endpoint is over ``max_waiting``.
        :meth:`_admit` disambiguates (breather → degrade → stall); ``_select``
        itself is pure (no waiting) so distribution tests can call it directly."""
        pool = self._candidates(exclude)
        if not pool:
            return None
        # Round-robin scan from the shared cursor, skipping any endpoint whose load
        # (server queue + local in-flight) is over the ceiling. Advancing `self._rr`
        # PAST the chosen endpoint (not just to it) means the NEXT call starts one
        # further along, so a single process spreads its own requests evenly instead
        # of re-picking the same head.
        n = len(self._eps)
        for step in range(n):
            ep = self._eps[(self._rr + step) % n]
            if ep in pool and self._load(ep) <= self._max_waiting:
                self._rr = (self._eps.index(ep) + 1) % n
                return ep
        # Every candidate is over the ceiling — signal "swamped". _admit takes a
        # breather then degrades; distribution/skip tests read None as "all swamped".
        return None

    async def _maybe_refresh_metrics(self) -> None:
        if time.monotonic() - self._last_metrics < self._metrics_interval:
            return
        async with self._metrics_lock:
            if time.monotonic() - self._last_metrics < self._metrics_interval:
                return
            await self._refresh_waiting()
            self._last_metrics = time.monotonic()

    async def _refresh_waiting(self) -> None:
        """Poll each endpoint's /metrics for its server-side queue depth."""

        async def probe(ep: Endpoint) -> None:
            if not ep.metrics_url:
                return
            try:
                r = await self._http.get(ep.metrics_url, timeout=4.0)
                if r.status_code == 200:
                    ep.waiting = _parse_waiting(r.text)
            except (httpx.HTTPError, OSError):
                pass  # keep the last reading; a transient metrics blip shouldn't swing routing

        await asyncio.gather(*(probe(e) for e in self._eps))

    async def _maybe_refresh_health(self) -> None:
        if time.monotonic() - self._last_health < self._health_interval:
            return
        async with self._health_lock:
            if time.monotonic() - self._last_health < self._health_interval:
                return
            await self.check_health()
            self._last_health = time.monotonic()

    async def check_health(self) -> None:
        """Probe every endpoint's health URL and update its healthy flag."""

        async def probe(ep: Endpoint) -> None:
            try:
                r = await self._http.get(ep.health_url, timeout=5.0)
                ep.healthy = r.status_code == 200
            except (httpx.HTTPError, OSError):
                ep.healthy = False

        await asyncio.gather(*(probe(e) for e in self._eps))


def make_pooled_embedder(
    api: str,
    http: httpx.AsyncClient,
    base_urls: list[str],
    model: str | None = None,
    api_key: str | None = None,
    max_concurrency: int = 8,
    health_path: str = "/health",
    metrics_path: str = "/metrics",
    max_waiting: int = 64,
    metrics_interval: float = 1.0,
) -> PooledEmbedder:
    """Build a PooledEmbedder over ``base_urls`` using the same per-endpoint
    embedder as the single-endpoint path (``make_embedder``).

    ``health_path`` is the probe path appended to each base URL. The default
    ``/health`` suits the sidecar and vLLM's OpenAI server; point it elsewhere
    for backends that expose readiness under a different path (a backend with no
    health route would otherwise read as permanently unhealthy).

    ``metrics_path`` (default vLLM's ``/metrics``) exposes ``num_requests_waiting``
    for admission-control routing; a backend without it stays at waiting=0 and just
    round-robins. ``max_waiting`` is the per-endpoint load ceiling (server queue +
    local in-flight) above which an endpoint is skipped; when EVERY endpoint is
    over it, ``embed`` takes one jittered breather and then degrades to the
    least-loaded endpoint (raising :class:`EmbedStalled` only if nothing is
    healthy)."""
    suffix = health_path.lstrip("/")
    msuffix = metrics_path.lstrip("/")
    endpoints = [
        Endpoint(
            make_embedder(api=api, http=http, base_url=url, model=model, api_key=api_key),
            health_url=f"{url.rstrip('/')}/{suffix}",
            metrics_url=f"{url.rstrip('/')}/{msuffix}",
        )
        for url in base_urls
    ]
    return PooledEmbedder(
        endpoints,
        http=http,
        max_concurrency=max_concurrency,
        max_waiting=max_waiting,
        metrics_interval=metrics_interval,
    )
