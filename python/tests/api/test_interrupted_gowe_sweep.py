"""#632: a GoWe ingest whose process was killed outright flags its collection.

SIGKILL / OOM runs no handler, so a submitted GoWe run keeps writing to the
stores while nothing flags the row; the restart's interruption sweep then fails
the job and the row is evictable. The sweep now reads the in-flight INGEST
jobs' registry ids first (GoWe deployments only; graph-extraction jobs write no
chunks) and flags them once the registry is up. A store that no-ops the sweep
(Postgres) interrupted nothing, so nothing is flagged there.
"""
from __future__ import annotations

from types import SimpleNamespace

from ragstack.api import deps
from ragstack.collection_store import CollectionSpec, InMemoryCollectionStore
from ragstack.jobstore import COMPLETED, KIND_GRAPH, RUNNING, InMemoryJobStore

DEAD = "http://127.0.0.1:1"


def _spec(cid: str) -> CollectionSpec:
    return CollectionSpec(id=cid, label=cid, owner="t", collection=f"{cid}_phys",
                          embedding_model="m", embedding_model_dim=4,
                          chunk_method="fixed_token", chunk_size=256, chunk_overlap=32)


async def _jobs() -> InMemoryJobStore:
    js = InMemoryJobStore()
    running = await js.create("upload", tenant_id="t", collection_id="ingesting")
    await js.update(running.job_id, status=RUNNING)
    await js.create("upload", tenant_id="t", collection_id="accepted")  # accepted
    graph = await js.create("graph", tenant_id="t", collection_id="graphing", kind=KIND_GRAPH)
    await js.update(graph.job_id, status=RUNNING)
    done = await js.create("upload", tenant_id="t", collection_id="done")
    await js.update(done.job_id, status=COMPLETED)
    await js.create("upload", tenant_id="t")  # no registry id
    return js


async def test_the_in_flight_ingests_of_a_gowe_deployment_are_named() -> None:
    ids = await deps.interrupted_gowe_ingest_collections(
        await _jobs(), SimpleNamespace(ingest_backend="gowe"))
    assert ids == {"ingesting", "accepted"}


async def test_a_local_deployment_names_none() -> None:
    """The local path writes no archive at all (versions empty, never evictable)."""
    ids = await deps.interrupted_gowe_ingest_collections(
        await _jobs(), SimpleNamespace(ingest_backend="local"))
    assert ids == set()


async def test_flagging_sets_the_rows_and_survives_a_failing_store() -> None:
    store = InMemoryCollectionStore([_spec("a"), _spec("b")])
    await deps.flag_interrupted_collections(store, {"a", "unknown"})
    assert (await store.get("a")).archive_pending is True
    assert (await store.get("b")).archive_pending is False

    class Broken:
        async def set_archive_pending(self, cid, pending):
            raise RuntimeError("registry down")

    await deps.flag_interrupted_collections(Broken(), {"a"})  # logged, not raised


def _in_memory_settings(monkeypatch) -> None:
    s = deps.settings
    monkeypatch.setattr(s, "vector_backend", "memory")
    monkeypatch.setattr(s, "text_backend", "memory")
    monkeypatch.setattr(s, "graph_backend", "memory")
    monkeypatch.setattr(s, "rerank_enabled", False)
    monkeypatch.setattr(s, "require_durable_backends", False)
    monkeypatch.setattr(s, "qdrant_url", DEAD)
    monkeypatch.setattr(s, "elasticsearch_url", DEAD)
    monkeypatch.setattr(s, "ingest_backend", "gowe")


async def _boot(monkeypatch, job_store) -> InMemoryCollectionStore:
    from fastapi import FastAPI

    from ragstack.ingestion.backends import LocalAsyncIORunner

    store = InMemoryCollectionStore([_spec("ingesting"), _spec("graphing"), _spec("done")])
    monkeypatch.setattr(deps, "make_job_store", lambda *a, **k: job_store)
    monkeypatch.setattr(deps, "make_collection_store", lambda *a, **k: store)
    # The GoWe backend itself needs an engine; the sweep only reads the setting.
    monkeypatch.setattr(deps, "make_ingest_backend",
                        lambda *a, **k: LocalAsyncIORunner(max_concurrency=1))
    # ADR-0010's boot check refuses a gowe tenant with no image dirs; not under test.
    monkeypatch.setattr(deps, "_verify_tool_images_at_boot", lambda: None)
    try:
        async with deps.lifespan(FastAPI()):
            pass
    except Exception:  # noqa: BLE001 — later startup steps may need infra
        pass
    return store


async def test_startup_flags_the_collection_of_an_interrupted_gowe_ingest(monkeypatch) -> None:
    _in_memory_settings(monkeypatch)
    js = await _jobs()
    store = await _boot(monkeypatch, js)
    assert (await store.get("ingesting")).archive_pending is True
    assert (await store.get("graphing")).archive_pending is False
    assert (await store.get("done")).archive_pending is False
    # ...and the sweep still failed the jobs.
    assert await js.active_collection_ids() == set()


async def test_a_store_that_sweeps_nothing_flags_nothing(monkeypatch) -> None:
    """Postgres no-ops fail_interrupted (#7): its in-flight rows may be live
    jobs of sibling workers, not interrupted ones."""
    _in_memory_settings(monkeypatch)
    js = await _jobs()

    async def no_sweep() -> int:
        return 0

    monkeypatch.setattr(js, "fail_interrupted", no_sweep)
    store = await _boot(monkeypatch, js)
    assert (await store.get("ingesting")).archive_pending is False

