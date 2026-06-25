"""Unit tests for ShardedIngestor (per-item isolation over a manifest)."""
import pytest

from ragstack.ingestion.backends import LocalAsyncIORunner
from ragstack.ingestion.manifest import Manifest, WorkItem
from ragstack.ingestion.sharded import ShardedIngestor
from ragstack.jobstore import InMemoryJobStore


class _FakePipeline:
    """Records (source, tenant) ingested; raises for any source in ``poison``."""

    def __init__(self, poison: set[str] | None = None) -> None:
        self.ingested: list[str] = []
        self.tenants: list[str] = []
        self._poison = poison or set()

    async def ingest(self, source: str, tenant_id: str = "default") -> list[str]:
        if source in self._poison:
            raise ValueError("bad doc")
        self.ingested.append(source)
        self.tenants.append(tenant_id)
        return [f"{source}#0", f"{source}#1"]


def _manifest(n: int) -> Manifest:
    return Manifest(items=[WorkItem(item_id=str(i), source=f"/s/{i}") for i in range(n)])


@pytest.mark.asyncio
async def test_all_items_completed():
    pipe = _FakePipeline()
    ingestor = ShardedIngestor(pipe, LocalAsyncIORunner(max_concurrency=3), shard_size=2)
    results = await ingestor.ingest_manifest(_manifest(5))
    assert len(results) == 5
    assert all(r.status == "completed" for r in results)
    assert all(len(r.chunk_ids) == 2 for r in results)
    assert len(pipe.ingested) == 5


@pytest.mark.asyncio
async def test_per_item_failure_is_isolated():
    pipe = _FakePipeline(poison={"/s/2"})
    ingestor = ShardedIngestor(pipe, LocalAsyncIORunner(), shard_size=10)
    results = await ingestor.ingest_manifest(_manifest(4))
    by_id = {r.item_id: r for r in results}
    assert by_id["2"].status == "failed"
    assert by_id["2"].error == "ValueError"
    # The other three still succeeded.
    assert sum(r.status == "completed" for r in results) == 3


@pytest.mark.asyncio
async def test_resumable_run_skips_completed_items():
    store = InMemoryJobStore()
    job = await store.create(source="/dir")

    # First run: item /s/2 fails, the other three complete and are checkpointed.
    pipe = _FakePipeline(poison={"/s/2"})
    ingestor = ShardedIngestor(
        pipe, LocalAsyncIORunner(), shard_size=10, job_store=store
    )
    manifest = _manifest(4)
    r1 = await ingestor.ingest_manifest(manifest, job_id=job.job_id)
    assert {x.item_id: x.status for x in r1}["2"] == "failed"
    assert (await store.item_counts(job.job_id)) == {
        "pending": 0,
        "completed": 3,
        "failed": 1,
    }

    # Second run (poison fixed): only the previously non-completed item reruns.
    pipe2 = _FakePipeline()
    ingestor2 = ShardedIngestor(
        pipe2, LocalAsyncIORunner(), shard_size=10, job_store=store
    )
    r2 = await ingestor2.ingest_manifest(manifest, job_id=job.job_id)
    assert [x.item_id for x in r2] == ["2"]
    assert pipe2.ingested == ["/s/2"]
    assert (await store.completed_item_ids(job.job_id)) == {"0", "1", "2", "3"}
