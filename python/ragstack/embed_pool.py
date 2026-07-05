"""Multi-endpoint embedder pool.

Fans embedding requests across several backend endpoints (e.g. vLLM replicas on
the H200s) with least-loaded selection, a global concurrency cap (backpressure),
health tracking, and failover. ``PooledEmbedder`` satisfies the Embedder
protocol, so it drops in behind ``BatchingEmbedder`` exactly like a single
embedder; with one endpoint configured the plain single-endpoint embedder is
used instead.
"""
from __future__ import annotations

import asyncio
import logging
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
    """Route ``embed`` across endpoints with backpressure, failover, and health.

    - **Backpressure:** a global semaphore caps total in-flight requests, so a
      large ingest can't open unbounded concurrent calls across the fleet.
    - **Least-loaded:** each request goes to the healthy endpoint with the fewest
      in-flight requests.
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
        metrics_interval: float = 5.0,
        max_waiting: int = 512,
    ) -> None:
        if not endpoints:
            raise ValueError("PooledEmbedder requires at least one endpoint")
        self._eps = endpoints
        self._http = http
        self._sem = asyncio.Semaphore(max(1, max_concurrency))
        self._health_interval = health_interval
        # Server-queue-aware routing: poll each endpoint's vllm:num_requests_waiting
        # every `metrics_interval`s and prefer endpoints with fewer queued requests,
        # skipping any above `max_waiting`. This is globally aware (the server queue),
        # unlike least-local-load which is blind to other processes and clusters all
        # of them onto one endpoint (the 16k-on-one-endpoint failure mode).
        self._metrics_interval = metrics_interval
        self._max_waiting = max_waiting
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
                ep = self._select(tried)
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

    def _select(self, exclude: set[int]) -> Endpoint | None:
        healthy = [e for e in self._eps if e.healthy and id(e) not in exclude]
        # Fall back to not-yet-tried unhealthy endpoints as a last resort — a stale
        # health flag shouldn't strand a request if an endpoint is actually up.
        pool = healthy or [e for e in self._eps if id(e) not in exclude]
        if not pool:
            return None
        # Prefer endpoints under the waiting ceiling (server queue not backed up); if
        # every candidate is swamped, fall through to all of them rather than
        # stranding the request.
        under = [e for e in pool if e.waiting <= self._max_waiting]
        chosen = under or pool
        if len(chosen) == 1:
            return chosen[0]
        # WEIGHTED-RANDOM by inverse load, NOT deterministic argmin. Many independent
        # embed processes each poll /metrics on their own ~5s cycle, so a strict
        # "pick the least-queued" makes them all choose the SAME momentary minimum in
        # lockstep and flood it (the 148k-on-one-endpoint herd). Weighting by
        # 1/(waiting+active+1) still strongly prefers empty endpoints but spreads the
        # herd across them proportionally, so no single endpoint gets swamped.
        weights = [1.0 / (e.waiting + e.active + 1) for e in chosen]
        return random.choices(chosen, weights=weights, k=1)[0]

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
    max_waiting: int = 512,
) -> PooledEmbedder:
    """Build a PooledEmbedder over ``base_urls`` using the same per-endpoint
    embedder as the single-endpoint path (``make_embedder``).

    ``health_path`` is the probe path appended to each base URL. The default
    ``/health`` suits the sidecar and vLLM's OpenAI server; point it elsewhere
    for backends that expose readiness under a different path (a backend with no
    health route would otherwise read as permanently unhealthy).

    ``metrics_path`` (default vLLM's ``/metrics``) exposes ``num_requests_waiting``
    for server-queue-aware routing; a backend without it just routes by local load.
    ``max_waiting`` is the per-endpoint queue ceiling above which an endpoint is
    skipped (unless all are over it)."""
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
        endpoints, http=http, max_concurrency=max_concurrency, max_waiting=max_waiting
    )
