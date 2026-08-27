"""Regression tests for the #149 backend fixups:
- every JobStore impl implements the full protocol (PostgresJobStore was missing
  list_jobs → GET /v1/jobs 500 on the postgres backend);
- _run_ingest records a provenance manifest only when the run landed data (an
  all-failed run must not stamp a 'verified' manifest with a null count)."""
from types import SimpleNamespace

import pytest

from ragstack.jobstore import InMemoryJobStore, PostgresJobStore, SqliteJobStore

_PROTOCOL_METHODS = (
    "create", "get", "update", "list_jobs", "item_counts",
    "add_items", "mark_item", "completed_item_ids", "fail_interrupted",
)


@pytest.mark.parametrize("cls", [InMemoryJobStore, SqliteJobStore, PostgresJobStore])
def test_jobstore_implements_full_protocol(cls):
    missing = [m for m in _PROTOCOL_METHODS if not callable(getattr(cls, m, None))]
    assert not missing, f"{cls.__name__} missing {missing}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "counts, expect_write",
    [
        ({"completed": 2, "failed": 0, "pending": 0}, True),   # landed data
        ({"completed": 0, "failed": 2, "pending": 0}, False),  # all-failed → skip
    ],
)
async def test_run_ingest_writes_manifest_only_on_success(monkeypatch, counts, expect_write):
    """The APP-LEVEL manifest (``write_ingest_manifest``) is the shared
    surface's, so the target passed here carries ``is_shared_surface=True``.

    #422 made ``target`` required and keyed this branch on that flag instead of
    on ``target is None``; before, the same coverage came from omitting the
    argument entirely. See the companion test below for the other arm."""
    import ragstack.api.deps as deps
    from ragstack.api.routers import documents as docs
    from ragstack.ingestion.manifest import ItemResult, Manifest, WorkItem
    from ragstack.jobstore import COMPLETED, FAILED

    writes: list = []
    monkeypatch.setattr(docs, "build_manifest",
                        lambda *a, **k: Manifest(items=[WorkItem(item_id="d", source="s")]))
    monkeypatch.setattr(deps, "write_ingest_manifest", lambda **k: writes.append(k))

    landed = counts.get("completed", 0) > 0
    status = COMPLETED if landed else FAILED

    class _JobStore:
        async def update(self, *a, **k):
            pass

        async def item_counts(self, job_id):
            return counts

    class _Ingestor:
        async def ingest_manifest(self, manifest, job_id=None, tenant_id=None):
            return [ItemResult(item_id="d", source="s", status=status,
                               chunk_ids=["a"] if landed else [])]

    surface = SimpleNamespace(id="ragstack", is_shared_surface=True)
    await docs._run_ingest(_JobStore(), _Ingestor(), "", "job1", "src", "public", surface)
    assert bool(writes) == expect_write


@pytest.mark.asyncio
async def test_run_ingest_writes_the_per_collection_manifest_off_the_surface():
    """The other arm of the same branch, which #422 turned from
    ``target is not None`` into ``not target.is_shared_surface``: a real
    collection gets ``write_ingest_manifest_for`` (bound to the entry) and NOT
    the app-level ``write_ingest_manifest``.

    Without this, keying the branch on the wrong flag — or dropping the
    distinction now that the surface entry is passed through rather than being
    ``None`` — would start writing per-collection manifests for the
    settings-derived corpus, silently."""
    import ragstack.api.deps as deps
    from ragstack.api.routers import documents as docs
    from ragstack.ingestion.manifest import ItemResult, Manifest, WorkItem
    from ragstack.jobstore import COMPLETED

    app_level: list = []
    per_collection: list = []
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(docs, "build_manifest",
                   lambda *a, **k: Manifest(items=[WorkItem(item_id="d", source="s")]))
        mp.setattr(deps, "write_ingest_manifest", lambda **k: app_level.append(k))
        mp.setattr(deps, "write_ingest_manifest_for",
                   lambda entry, **k: per_collection.append((entry, k)))

        class _JobStore:
            async def update(self, *a, **k):
                pass

            async def item_counts(self, job_id):
                return {"completed": 1, "failed": 0, "pending": 0}

        class _Ingestor:
            async def ingest_manifest(self, manifest, job_id=None, tenant_id=None):
                return [ItemResult(item_id="d", source="s", status=COMPLETED,
                                   chunk_ids=["a"])]

        owned = SimpleNamespace(id="lib", is_shared_surface=False)
        await docs._run_ingest(_JobStore(), _Ingestor(), "", "job1", "src", "t", owned)

    assert app_level == [], "the app-level manifest is the shared surface's only"
    assert [e.id for e, _ in per_collection] == ["lib"]
