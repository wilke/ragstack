"""Unit tests for ShardedIngestor (per-item isolation over a manifest)."""
import pytest

from ragstack.ingestion.backends import LocalAsyncIORunner
from ragstack.ingestion.manifest import Manifest, WorkItem
from ragstack.ingestion.sharded import ShardedIngestor


class _FakePipeline:
    """Records ingested sources; raises for any source in ``poison``."""

    def __init__(self, poison: set[str] | None = None) -> None:
        self.ingested: list[str] = []
        self._poison = poison or set()

    async def ingest(self, source: str) -> list[str]:
        if source in self._poison:
            raise ValueError("bad doc")
        self.ingested.append(source)
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
