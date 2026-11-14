"""#632: a GoWe ingest whose process was killed outright flags its collection.

SIGKILL / OOM runs no handler, so a submitted GoWe run keeps writing to the
stores while nothing flags the row; the restart's interruption sweep then fails
the job and the row is evictable. Startup now builds the registry FIRST, reads
the in-flight INGEST jobs' registry ids (GoWe deployments only; graph-extraction
jobs write no chunks), writes their flags, and only then sweeps — and skips the
sweep for this boot if any flag write failed, so the next boot finds the jobs
again. A crash later in the same boot cannot lose the flags. A job store that
does not sweep (Postgres, ``sweeps_interrupted = False``) is not read at all.
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
    assert await deps.flag_interrupted_collections(store, {"a", "unknown"}) is True
    assert (await store.get("a")).archive_pending is True
    assert (await store.get("b")).archive_pending is False

    class Broken:
        async def set_archive_pending(self, cid, pending):
            raise RuntimeError("registry down")

    assert await deps.flag_interrupted_collections(Broken(), {"a"}) is False  # not raised


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


def _store() -> InMemoryCollectionStore:
    return InMemoryCollectionStore([_spec("ingesting"), _spec("graphing"), _spec("done")])


def _local_backend(*a, **k):
    from ragstack.ingestion.backends import LocalAsyncIORunner

    return LocalAsyncIORunner(max_concurrency=1)


async def _boot(monkeypatch, job_store, store=None, ingest_backend=_local_backend):
    from fastapi import FastAPI

    store = store if store is not None else _store()
    monkeypatch.setattr(deps, "make_job_store", lambda *a, **k: job_store)
    monkeypatch.setattr(deps, "make_collection_store", lambda *a, **k: store)
    # The GoWe backend itself needs an engine; the sweep only reads the setting.
    monkeypatch.setattr(deps, "make_ingest_backend", ingest_backend)
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


async def test_a_store_that_does_not_sweep_is_not_read_and_flags_nothing(monkeypatch) -> None:
    """Postgres no-ops fail_interrupted (#7): its in-flight rows may be live
    jobs of sibling workers, and reading them would open the pool at boot —
    a slow or unavailable Postgres must not hang or fail startup over it."""
    _in_memory_settings(monkeypatch)
    js = await _jobs()

    async def unreachable(*a, **k):
        raise AssertionError("a non-sweeping job store must not be read at boot")

    monkeypatch.setattr(js, "sweeps_interrupted", False, raising=False)
    monkeypatch.setattr(js, "active_collection_ids", unreachable)
    monkeypatch.setattr(js, "active_for_collection", unreachable)
    assert await deps.interrupted_gowe_ingest_collections(
        js, SimpleNamespace(ingest_backend="gowe")) == set()
    store = await _boot(monkeypatch, js)
    assert (await store.get("ingesting")).archive_pending is False


def test_only_the_postgres_job_store_declares_it_does_not_sweep() -> None:
    from ragstack.jobstore import PostgresJobStore, SqliteJobStore

    assert InMemoryJobStore.sweeps_interrupted is True
    assert SqliteJobStore.sweeps_interrupted is True
    assert PostgresJobStore.sweeps_interrupted is False


async def test_a_crash_after_the_sweep_still_leaves_the_flag(monkeypatch) -> None:
    """Boot 1 fails the in-flight jobs, then a later startup step raises. The
    flags were written before the sweep, so boot 2 — which finds nothing in
    flight any more — still sees the collection unevictable."""
    _in_memory_settings(monkeypatch)
    js, store = await _jobs(), _store()

    def boom(*a, **k):
        raise RuntimeError("ingest backend misconfigured")

    await _boot(monkeypatch, js, store, ingest_backend=boom)
    assert await js.active_collection_ids() == set()  # boot 1 swept the jobs
    await _boot(monkeypatch, js, store)
    assert (await store.get("ingesting")).archive_pending is True



async def test_a_failed_flag_write_keeps_the_jobs_running_for_the_next_boot(monkeypatch) -> None:
    """A flag write that fails at boot (e.g. sqlite "database is locked") must
    not be followed by the sweep, or the flag is lost for good."""
    _in_memory_settings(monkeypatch)
    js, store = await _jobs(), _store()
    real = store.set_archive_pending

    async def locked(cid, pending):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(store, "set_archive_pending", locked)
    await _boot(monkeypatch, js, store)
    assert "ingesting" in await js.active_collection_ids()  # not swept
    assert (await store.get("ingesting")).archive_pending is False

    monkeypatch.setattr(store, "set_archive_pending", real)
    await _boot(monkeypatch, js, store)
    assert (await store.get("ingesting")).archive_pending is True
    assert await js.active_collection_ids() == set()  # swept on the good boot
