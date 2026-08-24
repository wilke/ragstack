"""Perf budget for GET /v1/ingest/{job_id} after tenant-scoping it (#130, #355).

The scoping check (JobStore.get -> _apply_tenant_scope) adds one string
comparison against an already-fetched row, so p95 should be indistinguishable
from a plain point read. Measured at the store level, matching this repo's
perf-test altitude (test_smoke.py times the retriever, not an HTTP round trip;
tests/perf/ has no access to tests/api/conftest's ASGI client fixtures
anyway) — InMemoryJobStore.get(), the same store the router calls through
``get_job_store`` in the tests/api conftest.
"""
import pytest

from ragstack.jobstore import InMemoryJobStore
from tests.perf._budget import assert_budget_async


@pytest.mark.perf
@pytest.mark.asyncio
async def test_inmemory_jobstore_get_tenant_scoped_p95_budget():
    store = InMemoryJobStore()
    job = await store.create(source="/perf/doc.pdf", tenant_id="acme")

    async def _get_once() -> None:
        await store.get(job.job_id, tenant_id="acme", is_admin=False)

    await assert_budget_async(
        "ingest_status_get_tenant_scoped",
        _get_once,
        budget_s=0.005,
        n=20,
    )
