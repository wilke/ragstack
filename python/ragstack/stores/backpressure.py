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

Not a full flow-controller: this is *admission* backpressure (hold the next
upsert until healthy), not an in-flight window. That is sufficient for the
serial per-file load stage; a concurrent loader that wants N-in-flight would add
a semaphore on top.
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
    """

    def __init__(
        self,
        inner: Any,
        *,
        poll_interval: float = 2.0,
        max_wait: float | None = None,
        ready_status: str = "green",
        health: HealthFn | None = None,
    ) -> None:
        self._inner = inner
        self._poll_interval = poll_interval
        self._max_wait = max_wait
        self._ready_status = ready_status
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
            if status == self._ready_status:
                if held:
                    log.info("qdrant %s (segments=%s) — resuming upserts",
                             status, getattr(h, "segments_count", "?"))
                return
            if self._max_wait is not None and waited >= self._max_wait:
                raise BackpressureTimeout(
                    f"collection not {self._ready_status!r} after {waited:.0f}s "
                    f"(status={status!r})"
                )
            held = True
            log.info("qdrant status=%s (segments=%s) — holding upsert, re-poll in %.1fs",
                     status, getattr(h, "segments_count", "?"), self._poll_interval)
            await asyncio.sleep(self._poll_interval)
            waited += self._poll_interval

    async def upsert(self, chunks: list[Chunk]) -> None:
        await self._await_ready()
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

    def __getattr__(self, name: str) -> Any:
        # Delegate non-protocol attributes (ensure_collection, healthcheck,
        # collection_health, _client, …) to the wrapped store. Guard underscore
        # names so a lookup before __init__ finishes (or of a private attr) raises
        # AttributeError instead of recursing on self._inner.
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._inner, name)
