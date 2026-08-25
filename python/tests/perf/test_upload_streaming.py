"""Perf budgets for the #202 upload hardening (#355).

1. A 50 MB ``POST /v1/ingest/upload`` through the endpoint, on the gowe branch,
   into a fake Workspace + Shock that CONSUME the stream chunk by chunk: peak
   Python-heap growth (``tracemalloc``, like #372's test) < 10 MB. What this
   measures is the PYTHON HEAP on the handler → Workspace hop — not disk and
   not ingress: the multipart parser has already received the whole body and
   spooled the part to a temp file (past 1 MiB) before the handler runs, and
   the Content-Length guard (``api/upload_guard.py``) is the only pre-body
   check. The upload is fed to httpx as a file object so the client streams it
   in 64 KB pieces; the endpoint hands the spooled ``UploadFile`` to the REAL
   ``WorkspaceClient.upload_source`` (wrapped in the per-request byte meter)
   which forwards it in ``STREAM_CHUNK`` pieces. A whole-file ``bytes`` anywhere
   on that hop would show as a 50 MB peak. The ``ru_maxrss`` delta is printed
   for reference only (process-lifetime high-water mark, so an earlier test can
   mask it).

2. ``InMemoryJobStore.count_active`` — the per-upload admission query
   (``single_inflight_ingest``) — p95 < 1 ms over a store holding 2 000 jobs.

The app/client/gowe fixtures are the ones ``tests/api`` uses (imported here so
pytest registers them for this module — ``tests/perf`` does not see
``tests/api/conftest.py`` on its own).
"""
from __future__ import annotations

import io
import json
import resource
import tracemalloc

import httpx
import pytest

from ragstack.api.main import app
from ragstack.jobstore import COMPLETED, RUNNING, InMemoryJobStore
from ragstack.workspace import WorkspaceClient
from tests.api import conftest as _api_conftest
from tests.api import test_ingest_gowe_path as _gowe_path
from tests.perf._budget import assert_budget_async

# Bound by assignment so pytest registers the api fixtures (the ASGI client and
# its autouse isolation) and the gowe fixture for this module.
client = _api_conftest.client
_acl_store = _api_conftest._acl_store
_clear_auth_caches = _api_conftest._clear_auth_caches
_enable_ingest = _api_conftest._enable_ingest
_isolate_qdrant = _api_conftest._isolate_qdrant
gowe = _gowe_path.gowe
AUTH = _gowe_path.AUTH
TOKEN = _gowe_path.TOKEN
FakeWorkspace = _gowe_path.FakeWorkspace

SIZE = 50 * 1024 * 1024
WS_URL = "http://workspace.test/services/Workspace"


class StreamingShock(httpx.MockTransport):
    """Fake Workspace RPC + Shock that consumes an upload chunk by chunk (stock
    ``MockTransport`` would ``aread()`` — and so buffer — the body first)."""

    def __init__(self) -> None:
        super().__init__(self._handle)
        self.received = 0
        self.node = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "shock.test":
            assert request.headers["Authorization"] == f"OAuth {TOKEN}"
            async for chunk in request.stream:  # type: ignore[union-attr]
                self.received += len(chunk)
            return httpx.Response(200, json={"status": 200, "data": {"id": "n"}, "error": None})
        await request.aread()
        return self._handle(request)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == TOKEN
        body = json.loads(request.content)
        assert body["method"] == "Workspace.create"
        path = body["params"][0]["objects"][0][0]
        parent, name = path.rsplit("/", 1)
        self.node += 1
        meta = [name, "unspecified", parent + "/", "2026-08-24T00:00:00Z", "id", "perf", 0, {}, {},
                "o", "n", f"http://shock.test/node/{self.node}"]
        return httpx.Response(200, json={"version": "1.1", "result": [[meta]]})


class StreamingWorkspace(FakeWorkspace):
    """The gowe fixture's fake Workspace (folder / receipts), but with
    ``upload_source`` delegated to the REAL client over the streaming Shock."""

    def __init__(self, engine, real: WorkspaceClient) -> None:
        super().__init__(engine)
        self._real = real

    async def upload_source(self, token, folder, filename, stream, *, max_bytes, size=None):
        uri = await self._real.upload_source(
            token, folder, filename, stream, max_bytes=max_bytes, size=size
        )
        self.uploads.append({"token": token, "folder": folder, "filename": filename,
                             "size": size, "bytes": size})
        return uri


@pytest.mark.perf
@pytest.mark.asyncio
async def test_upload_50mb_through_the_endpoint_does_not_buffer(client, gowe, monkeypatch):
    from ragstack.config import settings

    monkeypatch.setattr(settings, "max_document_bytes", SIZE)
    shock = StreamingShock()
    async with httpx.AsyncClient(transport=shock) as http:
        app.state.workspace = StreamingWorkspace(gowe["engine"], WorkspaceClient(WS_URL, http))
        payload = b"%PDF" + b"\xab" * (SIZE - 4)  # allocated BEFORE the measurement
        rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        tracemalloc.start()
        try:
            r = await client.post(
                "/v1/ingest/upload",
                files=[("files", ("big.pdf", io.BytesIO(payload), "application/pdf"))],
                data={"collection": "lib1"},
                headers=AUTH,
            )
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    assert r.status_code == 202, r.text
    peak_mb = peak / (1024 * 1024)
    rss_delta_mb = (rss_after - rss_before) / 1024  # ru_maxrss is KiB on Linux
    print(f"PERF upload_endpoint_50mb_memory: tracemalloc_peak={peak_mb:.1f}MB "
          f"ru_maxrss_delta={rss_delta_mb:.1f}MB (reference only) shock_received={shock.received}")
    # The whole file reached Shock (plus the multipart framing), and nothing
    # on the way held it whole.
    assert SIZE <= shock.received < SIZE + 4096
    assert peak_mb < 10, f"upload buffered the file: peak {peak_mb:.1f} MB"


@pytest.mark.perf
@pytest.mark.asyncio
async def test_inmemory_count_active_p95_budget():
    store = InMemoryJobStore()
    tenants = [f"tenant-{i}" for i in range(20)]
    for i in range(2000):
        job = await store.create(source=f"/doc{i}", tenant_id=tenants[i % len(tenants)])
        if i % 3 == 0:
            await store.update(job.job_id, status=COMPLETED)
        elif i % 3 == 1:
            await store.update(job.job_id, status=RUNNING)
    assert await store.count_active("tenant-3") > 0

    async def _count_once() -> None:
        await store.count_active("tenant-3")

    await assert_budget_async("inmemory_jobstore_count_active", _count_once,
                              budget_s=0.001, n=500)
