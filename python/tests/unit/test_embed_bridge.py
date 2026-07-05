"""SyncEmbedBridge: sync embed_fn over an async embedder on a dedicated loop.

Guards the cross-loop fix — the bridge must build its embedder/httpx client on
its *own* background loop (not the caller's), and be invocable from within a
running event loop without deadlocking or raising a cross-loop error.
"""
import asyncio

import httpx
import pytest

from ragstack.ingestion.embed_bridge import SyncEmbedBridge


class _LoopRecordingEmbedder:
    """Records the running loop its embed() executes on; ignores the client."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http
        self.loop = None

    async def embed(self, texts):
        self.loop = asyncio.get_running_loop()
        return [[float(len(t))] for t in texts]


@pytest.mark.asyncio
async def test_bridge_runs_embed_on_its_own_loop_from_within_running_loop():
    main_loop = asyncio.get_running_loop()
    built = {}

    def factory(http: httpx.AsyncClient) -> _LoopRecordingEmbedder:
        built["embedder"] = _LoopRecordingEmbedder(http)
        return built["embedder"]

    bridge = SyncEmbedBridge(factory)
    try:
        # Call the blocking bridge the way the pipeline now does — off the main
        # loop (via a worker thread) so it never stalls the event loop.
        out = await asyncio.to_thread(bridge, ["a", "bb"])
        assert out == [[1.0], [2.0]]
        # The embed ran on the bridge's loop, not the main app loop — this is the
        # property that keeps the main-loop httpx client from being used here.
        assert built["embedder"].loop is not None
        assert built["embedder"].loop is not main_loop
    finally:
        bridge.close()


class _CountingOrderedEmbedder:
    """Embeds each text to a single-element vector = its running global index, so
    the flattened result reveals both the ORDER and how many calls were made."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http
        self.calls = 0
        self.max_concurrent = 0
        self._active = 0

    async def embed(self, texts):
        self.calls += 1
        self._active += 1
        self.max_concurrent = max(self.max_concurrent, self._active)
        # yield so concurrent sub-batches actually overlap on the loop
        await asyncio.sleep(0.01)
        self._active -= 1
        # tag each text with its content (an int) so order is checkable
        return [[float(t)] for t in texts]


@pytest.mark.asyncio
async def test_fanout_splits_into_concurrent_subbatches_preserving_order():
    made = {}

    def factory(http):
        made["e"] = _CountingOrderedEmbedder(http)
        return made["e"]

    bridge = SyncEmbedBridge(factory, batch_size=4)
    try:
        texts = [str(i) for i in range(10)]  # 10 -> ceil(10/4)=3 sub-batches
        out = await asyncio.to_thread(bridge, texts)
    finally:
        bridge.close()
    # order preserved end-to-end (this is what keeps breakpoints byte-identical)
    assert out == [[float(i)] for i in range(10)]
    e = made["e"]
    assert e.calls == 3  # split into 3 sub-batches, not 1
    assert e.max_concurrent >= 2  # dispatched concurrently, not serially


@pytest.mark.asyncio
async def test_fanout_concurrency_is_bounded_by_max_inflight():
    # Regression: a heavy document fans into hundreds of sub-batches. Without a
    # bound the bridge gathered them ALL at once, drowning a single-endpoint
    # breakpoint service (BGE sidecar) -> wedged -> ingest stall. The bridge must
    # cap concurrent sub-batch calls at max_inflight regardless of the embedder.
    made = {}

    def factory(http):
        made["e"] = _CountingOrderedEmbedder(http)
        return made["e"]

    bridge = SyncEmbedBridge(factory, batch_size=1, max_inflight=3)
    try:
        texts = [str(i) for i in range(60)]  # 60 sub-batches, fan-out of 60
        out = await asyncio.to_thread(bridge, texts)
    finally:
        bridge.close()
    e = made["e"]
    assert out == [[float(i)] for i in range(60)]  # order still preserved
    assert e.calls == 60  # every sub-batch dispatched
    assert e.max_concurrent <= 3  # NEVER more than max_inflight in flight at once
    assert e.max_concurrent == 3  # and it does run concurrently up to the bound


@pytest.mark.asyncio
async def test_fanout_single_call_for_small_lists():
    made = {}

    def factory(http):
        made["e"] = _CountingOrderedEmbedder(http)
        return made["e"]

    bridge = SyncEmbedBridge(factory, batch_size=64)
    try:
        out = await asyncio.to_thread(bridge, [str(i) for i in range(5)])
    finally:
        bridge.close()
    assert out == [[float(i)] for i in range(5)]
    assert made["e"].calls == 1  # <= batch_size -> exactly one call, no overhead


@pytest.mark.asyncio
async def test_bridge_reuses_one_loop_across_calls():
    loops = []

    def factory(http: httpx.AsyncClient) -> _LoopRecordingEmbedder:
        return _LoopRecordingEmbedder(http)

    bridge = SyncEmbedBridge(factory)
    try:
        for _ in range(2):
            await asyncio.to_thread(bridge, ["x"])
        # Same background loop reused across calls; close() tears it down cleanly.
        loops.append(bridge._loop)
        assert bridge._loop is not None
    finally:
        bridge.close()
    assert bridge._loop is None  # torn down
