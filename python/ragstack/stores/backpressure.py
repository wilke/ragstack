"""Qdrant upsert backpressure (ADR-0001 offline plane, #141).

A ``VectorStore`` decorator that gates ``upsert`` on the backing collection's
live health, so a bulk load never overwhelms a Qdrant that is optimizing —
the must-have of #141. It wraps any store exposing an async ``collection_health``
(``QdrantVectorStore``; see :class:`~ragstack.stores.qdrant.CollectionHealth`) and
delegates every other method unchanged.

Why a decorator: the load pipeline calls ``vector_store.upsert`` (via
``IngestionPipeline.index_chunks``); wrapping the store means backpressure is
applied transparently, with **no change** to the pipeline or the load stage —
they don't know it's there. And because the gate lives at the store boundary, it
composes with the deterministic-id / upsert-only idempotency: a batch held back
and retried later overwrites in place.

Mechanism: before each upsert, poll ``collection_health``; proceed only while
``status == "green"`` (idle/indexed). ``yellow`` (optimizing) / ``grey``
(pending) / ``red`` (error) hold the upsert and re-poll after ``poll_interval``
until green — bounding the accumulated-unindexed-vectors burst that drives the
VMA-exhaustion crash. Only ``upsert`` is gated; reads, deletes, and counts pass
through (a delete-prior is a cheap point op that doesn't trigger an index build).

In-flight window: the default is pure *admission* backpressure (hold the next
upsert until healthy), which is all the serial per-file load stage needs. Pass
``max_in_flight=N`` to also bound concurrent ``inner.upsert`` calls with a
semaphore — the "keep up to N batches in flight" half of #141 — so a future
concurrent loader can overlap uploads without a thundering herd (all tasks seeing
green at once and firing together). Left ``None`` (unbounded), it's inert, so the
current serial loader is unaffected.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from ragstack.models import Chunk

log = logging.getLogger(__name__)

HealthFn = Callable[[], Awaitable[Any]]


class BackpressureTimeout(RuntimeError):
    """The collection did not return to the ready status within ``max_wait``."""


class BackpressuredVectorStore:
    """Wrap a ``VectorStore`` and hold ``upsert`` until the collection is ready.

    ``inner`` must satisfy the ``VectorStore`` protocol. Health comes from
    ``inner.collection_health`` unless an explicit ``health`` callable is passed
    (used in tests). If neither is available (e.g. an in-memory store with no
    health signal), backpressure is a no-op and every call passes straight
    through — so wrapping such a store is harmless.

    The collection is "ready" when ``status == ready_status`` (default ``green``)
    **and** the optimizer reports healthy (``optimizer_ok``). ``max_in_flight``,
    if set, bounds concurrent ``inner.upsert`` calls (see the module docstring).
    """

    def __init__(
        self,
        inner: Any,
        *,
        poll_interval: float = 2.0,
        max_wait: float | None = None,
        ready_status: str = "green",
        max_in_flight: int | None = None,
        health: HealthFn | None = None,
    ) -> None:
        self._inner = inner
        self._poll_interval = poll_interval
        self._max_wait = max_wait
        self._ready_status = ready_status
        self._sem = asyncio.Semaphore(max_in_flight) if max_in_flight else None
        self._health: HealthFn | None = (
            health if health is not None else getattr(inner, "collection_health", None)
        )

    async def _await_ready(self) -> None:
        if self._health is None:
            return  # no health signal → no throttle (e.g. in-memory store)
        waited = 0.0
        held = False
        while True:
            h = await self._health()
            status = getattr(h, "status", self._ready_status)
            # Hold on an optimizer error too, not only on a non-green status: the
            # field exists precisely to catch a collection that reports ready while
            # its optimizer has failed. Absent (health object without the field) is
            # treated as healthy.
            optimizer_ok = getattr(h, "optimizer_ok", True)
            if status == self._ready_status and optimizer_ok:
                if held:
                    log.info("qdrant ready (status=%s, segments=%s) — resuming upserts",
                             status, getattr(h, "segments_count", "?"))
                return
            held = True
            # Compute the next sleep so total wait never exceeds max_wait, and the
            # timeout is reported accurately (the old code slept first and could
            # overshoot the budget by a whole poll_interval).
            if self._max_wait is None:
                delay = self._poll_interval
            else:
                remaining = self._max_wait - waited
                if remaining <= 0:
                    raise BackpressureTimeout(
                        f"collection not ready (status={status!r}, "
                        f"optimizer_ok={optimizer_ok}) after {waited:.1f}s"
                    )
                # A non-positive poll_interval would otherwise never advance
                # ``waited`` toward the deadline; fall back to the remaining budget.
                delay = min(self._poll_interval, remaining) if self._poll_interval > 0 \
                    else remaining
            log.info("qdrant status=%s optimizer_ok=%s (segments=%s) — holding upsert %.2fs",
                     status, optimizer_ok, getattr(h, "segments_count", "?"), delay)
            await asyncio.sleep(delay)
            waited += delay

    async def upsert(self, chunks: list[Chunk]) -> None:
        await self._await_ready()
        if self._sem is None:
            await self._inner.upsert(chunks)
        else:
            # Bound concurrent DB writes to max_in_flight (inert for a serial caller).
            async with self._sem:
                await self._inner.upsert(chunks)

    # --- the rest of the VectorStore protocol: pass straight through --------- #
    async def search(self, *args: Any, **kwargs: Any) -> Any:
        return await self._inner.search(*args, **kwargs)

    async def delete(self, *args: Any, **kwargs: Any) -> None:
        await self._inner.delete(*args, **kwargs)

    async def delete_except(self, *args: Any, **kwargs: Any) -> None:
        await self._inner.delete_except(*args, **kwargs)

    async def count_tenants(self, *args: Any, **kwargs: Any) -> int:
        return await self._inner.count_tenants(*args, **kwargs)

    async def get_chunks(self, *args: Any, **kwargs: Any) -> Any:
        return await self._inner.get_chunks(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        # Delegate non-protocol attributes (ensure_collection, healthcheck,
        # collection_health, _client, …) to the wrapped store. Guard underscore
        # names so a lookup before __init__ finishes (or of a private attr) raises
        # AttributeError instead of recursing on self._inner.
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._inner, name)
