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

Fan-out: the semantic chunker hands a whole document's sentence buffers to this
bridge in ONE call. A bare ``embedder.embed(buffers)`` is a *single* coroutine —
and against a :class:`~ragstack.embed_pool.PooledEmbedder` (least-loaded routing
across N vLLM replicas) a single call lands on exactly ONE endpoint, so one doc's
breakpoint embed uses one GPU while the other N-1 sit idle. To saturate the fleet
we split the buffers into fixed-size sub-batches and dispatch them *concurrently*
on this loop, bounded by an explicit ``max_inflight`` semaphore; the pool then
spreads them least-loaded across every endpoint. Results are re-concatenated IN
INPUT ORDER, so the embeddings — and therefore the cosine distances, breakpoints,
and deterministic chunk ids — are byte-identical to the single-call path. With one
endpoint configured the vectors are identical too; only the number of HTTP
requests changes.

**Why the bridge bounds the fan-out itself** (``max_inflight``) rather than relying
on the downstream embedder's ``max_concurrency``: a heavy document can have
thousands of sentence buffers, so ``ceil(len/batch_size)`` sub-batch calls can be
in the hundreds. A ``PooledEmbedder`` has its own semaphore, but the *single-
endpoint* breakpoint path (e.g. ``--breakpoint-embedding-url`` → a BGE sidecar)
does NOT — so an unbounded ``gather`` fires all hundreds at once and drowns that
service (accept-queue overflow → wedged → no request completes → the whole ingest
stalls). The bridge-level semaphore caps in-flight sub-batches regardless of which
embedder receives them, which is the reliable throttle.
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

    def __init__(
        self,
        embedder_factory: EmbedderFactory,
        *,
        batch_size: int = 64,
        max_inflight: int = 8,
    ) -> None:
        self._factory = embedder_factory
        # Sub-batch size for the concurrent fan-out: a document's buffers are
        # embedded as ceil(len/batch_size) concurrent calls, which the pooled
        # embedder spreads least-loaded across all endpoints. Kept in step with
        # the ingest --batch-size (one buffer ~= one sentence-window, so 64 is a
        # sensible request granularity). <=0 disables splitting (one call).
        self._batch_size = batch_size
        # Hard cap on concurrent sub-batch embed calls. The semaphore is created
        # once per BRIDGE, so this is a bridge-wide ceiling across every
        # concurrently-chunked document (with --chunk-concurrency > 1, four docs
        # share this 8, they do not get 8 each) — which is what protects a
        # single-endpoint breakpoint service from a fan-out storm.
        #
        # NOTE: there is no CLI flag for this. --embedding-max-concurrency and
        # --breakpoint-embedding-max-concurrency (both default 8) are tunable, so
        # raising either to saturate a GPU fleet is silently re-capped here.
        self._max_inflight = max(1, max_inflight)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client: httpx.AsyncClient | None = None
        self._embedder: Embedder | None = None
        # Bounds the fan-out; created lazily ON the bridge loop (asyncio primitives
        # must bind to the loop that awaits them) alongside the embedder.
        self._sem: asyncio.Semaphore | None = None
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
            self._sem = asyncio.Semaphore(self._max_inflight)
        # Small buffer lists (or splitting disabled) stay a single call — identical
        # to the pre-fan-out behaviour, no extra request overhead for tiny docs.
        n = len(texts)
        if self._batch_size <= 0 or n <= self._batch_size:
            return await self._embedder.embed(texts)
        # Split into fixed-size sub-batches and embed them concurrently but BOUNDED
        # by `max_inflight` (bridge-wide, shared by all concurrent documents), so a
        # heavy doc's hundreds of sub-batches don't all fire at once and drown a
        # single-endpoint breakpoint service. gather preserves argument order, so
        # the flattened result is identical to a single embed(texts).
        bs = self._batch_size
        embedder = self._embedder
        sem = self._sem
        assert sem is not None  # created above with the embedder

        async def _one(sub: list[str]) -> list[list[float]]:
            async with sem:
                return await embedder.embed(sub)

        results = await asyncio.gather(
            *(_one(texts[i : i + bs]) for i in range(0, n, bs))
        )
        out: list[list[float]] = []
        for r in results:
            out.extend(r)
        return out

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
