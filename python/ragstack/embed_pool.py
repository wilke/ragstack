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
import time

import httpx

from ragstack.embedders import make_embedder

log = logging.getLogger(__name__)


class Endpoint:
    """One backend endpoint: its embedder, health URL, and live load."""

    __slots__ = ("embedder", "health_url", "healthy", "active")

    def __init__(self, embedder, health_url: str) -> None:
        self.embedder = embedder
        self.health_url = health_url
        self.healthy = True  # optimistic; demoted on failure, restored by health checks
        self.active = 0


class PooledEmbedder:
    """Route ``embed`` across endpoints with backpressure, failover, and health.

    - **Backpressure:** a global semaphore caps total in-flight requests, so a
      large ingest can't open unbounded concurrent calls across the fleet.
    - **Least-loaded:** each request goes to the healthy endpoint with the fewest
      in-flight requests.
    - **Failover:** a 5xx / network failure demotes the endpoint and retries on
      another. A 4xx is a bad-input error (same on every endpoint), so it
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
    ) -> None:
        if not endpoints:
            raise ValueError("PooledEmbedder requires at least one endpoint")
        self._eps = endpoints
        self._http = http
        self._sem = asyncio.Semaphore(max(1, max_concurrency))
        self._health_interval = health_interval
        # Start the clock now so the first probe waits a full interval rather than
        # firing on the first request.
        self._last_health = time.monotonic()
        self._health_lock = asyncio.Lock()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with self._sem:
            await self._maybe_refresh_health()
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
                    if (
                        isinstance(e, httpx.HTTPStatusError)
                        and 400 <= e.response.status_code < 500
                    ):
                        # Bad input, not an endpoint fault — let the caller handle it.
                        raise
                    ep.healthy = False
                    last_exc = e
                    tried.add(id(ep))
                    log.warning(
                        "embedding endpoint %s failed, failing over: %s",
                        ep.health_url,
                        e,
                    )
                finally:
                    ep.active -= 1
            raise RuntimeError("all embedding endpoints failed") from last_exc

    def _select(self, exclude: set[int]) -> Endpoint | None:
        healthy = [e for e in self._eps if e.healthy and id(e) not in exclude]
        # Fall back to not-yet-tried unhealthy endpoints as a last resort — a stale
        # health flag shouldn't strand a request if an endpoint is actually up.
        pool = healthy or [e for e in self._eps if id(e) not in exclude]
        if not pool:
            return None
        return min(pool, key=lambda e: e.active)

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
) -> PooledEmbedder:
    """Build a PooledEmbedder over ``base_urls`` using the same per-endpoint
    embedder as the single-endpoint path (``make_embedder``)."""
    endpoints = [
        Endpoint(
            make_embedder(api=api, http=http, base_url=url, model=model, api_key=api_key),
            health_url=f"{url.rstrip('/')}/health",
        )
        for url in base_urls
    ]
    return PooledEmbedder(endpoints, http=http, max_concurrency=max_concurrency)
