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
from collections.abc import Sequence

from ragstack.protocols import Embedder


class SyncEmbedBridge:
    """Wrap an async :class:`Embedder` as a sync ``embed_fn`` for chunkers.

    Lazily starts one background event-loop thread on first use and reuses it.
    Call :meth:`close` at shutdown to stop the loop and join the thread.
    """

    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
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

    def __call__(self, texts: Sequence[str]) -> list[list[float]]:
        loop = self._ensure_loop()
        fut = asyncio.run_coroutine_threadsafe(
            self._embedder.embed(list(texts)), loop
        )
        return fut.result()

    def close(self) -> None:
        with self._lock:
            loop, thread = self._loop, self._thread
            self._loop = self._thread = None
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=5.0)
        if loop is not None:
            loop.close()
