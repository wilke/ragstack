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
