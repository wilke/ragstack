"""Perf budget for ``WorkspaceClient.upload_source`` (#356): streaming a 50 MB
in-memory file through a fake Workspace + Shock must cost <= 2x the raw read
time and must not buffer the file.

Memory is measured with ``tracemalloc`` (peak Python-heap allocation while the
upload runs), not ``resource.getrusage``: ``ru_maxrss`` is a process-lifetime
high-water mark that an earlier test can have pushed above any level this test
could detect, whereas the tracemalloc peak is reset per measurement and catches
exactly the failure mode the issue names — a whole-file ``bytes`` object (50 MB)
materialised anywhere on the Python side. The ``ru_maxrss`` delta is printed
for reference only.

The fake is a subclass of ``httpx.MockTransport`` that *iterates* the request
body instead of buffering it: stock ``MockTransport`` calls ``request.aread()``
before invoking the handler, which would itself allocate the 50 MB and mask the
thing under test. The handler still sees the same ``httpx.Request`` (headers,
URL, JSON-RPC body for the small Workspace calls).
"""
from __future__ import annotations

import io
import json
import resource
import time
import tracemalloc

import httpx
import pytest

from ragstack.workspace import STREAM_CHUNK, WorkspaceClient
from tests.perf._budget import assert_budget_async

SIZE = 50 * 1024 * 1024
TOKEN = "un=perf@patricbrc.org|tokenid=t|expiry=9999999999|sig=x"
WS_URL = "http://workspace.test/services/Workspace"
FOLDER = "/perf@patricbrc.org/home/.ragstack/collections/c1/sources"


class StreamingFake(httpx.MockTransport):
    """Fake Workspace RPC + Shock that consumes an upload chunk by chunk."""

    def __init__(self) -> None:
        super().__init__(self._handle)
        self.received = 0
        self.node = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "shock.test":
            async for chunk in request.stream:  # type: ignore[union-attr]
                self.received += len(chunk)
            return httpx.Response(200, json={"status": 200, "data": {"id": "n"}, "error": None})
        await request.aread()
        return self._handle(request)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == TOKEN
        body = json.loads(request.content)
        assert body["method"] == "Workspace.create" and body["params"][0]["createUploadNodes"] == 1
        path = body["params"][0]["objects"][0][0]
        parent, name = path.rsplit("/", 1)
        self.node += 1
        meta = [name, "unspecified", parent + "/", "2026-08-24T00:00:00Z", "id", "perf", 0, {}, {},
                "o", "n", f"http://shock.test/node/{self.node}"]
        return httpx.Response(200, json={"version": "1.1", "result": [[meta]]})


def _raw_read(buf: io.BytesIO) -> None:
    buf.seek(0)
    while buf.read(STREAM_CHUNK):
        pass


@pytest.mark.perf
@pytest.mark.asyncio
async def test_upload_50mb_within_2x_raw_read_time():
    payload = io.BytesIO(b"\xab" * SIZE)
    n = 20

    raw: list[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        _raw_read(payload)
        raw.append(time.perf_counter() - t0)
    raw.sort()
    raw_p95 = raw[min(n - 1, round(0.95 * (n - 1)))]
    print(f"PERF workspace_upload_50mb_raw_read: p95={raw_p95:.4f}s n={n}")

    fake = StreamingFake()
    async with httpx.AsyncClient(transport=fake) as http:
        client = WorkspaceClient(WS_URL, http)
        i = 0

        async def _upload_once() -> None:
            nonlocal i
            i += 1
            payload.seek(0)
            await client.upload_source(TOKEN, FOLDER, f"f{i}.bin", payload, max_bytes=SIZE)

        await assert_budget_async("workspace_upload_50mb", _upload_once,
                                  budget_s=2 * raw_p95, n=n)
    # The multipart framing adds a few hundred bytes per upload on top of the payload.
    assert n * SIZE <= fake.received < n * (SIZE + 4096)


@pytest.mark.perf
@pytest.mark.asyncio
async def test_upload_50mb_does_not_buffer_the_file():
    payload = io.BytesIO(b"\xcd" * SIZE)
    fake = StreamingFake()
    async with httpx.AsyncClient(transport=fake) as http:
        client = WorkspaceClient(WS_URL, http)
        rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        tracemalloc.start()
        try:
            await client.upload_source(TOKEN, FOLDER, "f.bin", payload, max_bytes=SIZE)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_mb = peak / (1024 * 1024)
    rss_delta_mb = (rss_after - rss_before) / 1024  # ru_maxrss is KiB on Linux
    print(f"PERF workspace_upload_50mb_memory: tracemalloc_peak={peak_mb:.1f}MB "
          f"ru_maxrss_delta={rss_delta_mb:.1f}MB (reference only)")
    assert SIZE <= fake.received < SIZE + 4096
    assert peak_mb < 20, f"upload buffered the file: peak {peak_mb:.1f} MB"
