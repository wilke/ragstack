"""Synchronous bridge from the async ``Embedder`` protocol to a sync embed fn.

The chunkers run **synchronously** (``Chunker.chunk(doc)``) inside the
already-async ingestion pipeline, but the configured embedder is async
(``Embedder.embed`` is a coroutine). The :class:`SemanticChunker` needs to embed
sentence buffers from inside that sync call, so we can't ``await`` and can't use
``asyncio.run`` (a loop is already running on the calling thread).

This bridge owns a dedicated background event loop on its own thread and submits
the embed coroutine to it with ``run_coroutine_threadsafe``, blocking the caller
until it returns. That is safe to call from within the main event loop because
the coroutine runs on a *different* loop/thread — no re-entrancy.
"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Sequence

import httpx

from ragstack.protocols import Embedder

# Builds the embedder over a client created on the bridge's own loop.
EmbedderFactory = Callable[[httpx.AsyncClient], Embedder]


class SyncEmbedBridge:
    """Wrap an async :class:`Embedder` as a sync ``embed_fn`` for chunkers.

    Lazily starts one background event-loop thread on first use and reuses it.
    Call :meth:`close` at shutdown to stop the loop and join the thread.

    The embedder and its ``httpx`` client are built *lazily on the bridge's own
    loop* from ``embedder_factory`` — not handed in pre-built. An httpx client
    binds to the loop it is first used on, so reusing the app's main-loop client
    here would raise a cross-loop ``RuntimeError`` when the embed coroutine runs
    on this background loop; building a dedicated client on this loop avoids that.
    """

    def __init__(self, embedder_factory: EmbedderFactory) -> None:
        self._factory = embedder_factory
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client: httpx.AsyncClient | None = None
        self._embedder: Embedder | None = None
        self._lock = threading.Lock()

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is not None:
                return self._loop
            loop = asyncio.new_event_loop()

            def _run() -> None:
                asyncio.set_event_loop(loop)
                loop.run_forever()

            thread = threading.Thread(
                target=_run, name="semantic-chunk-embed", daemon=True
            )
            thread.start()
            self._loop = loop
            self._thread = thread
            return loop

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        # Build the client + embedder lazily ON THIS (bridge) loop the first time,
        # so the httpx client binds here rather than to the app's main loop.
        if self._embedder is None:
            self._client = httpx.AsyncClient(timeout=120.0)
            self._embedder = self._factory(self._client)
        return await self._embedder.embed(texts)

    def __call__(self, texts: Sequence[str]) -> list[list[float]]:
        loop = self._ensure_loop()
        fut = asyncio.run_coroutine_threadsafe(self._embed(list(texts)), loop)
        return fut.result()

    def close(self) -> None:
        with self._lock:
            loop, thread = self._loop, self._thread
            self._loop = self._thread = None
        if loop is not None:
            # Close the httpx client on its own loop before stopping it.
            async def _shutdown() -> None:
                if self._client is not None:
                    await self._client.aclose()

            try:
                asyncio.run_coroutine_threadsafe(_shutdown(), loop).result(timeout=5.0)
            except Exception:
                pass
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=5.0)
        if loop is not None:
            loop.close()
