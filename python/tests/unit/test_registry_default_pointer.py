"""`default` is a pointer, not a registry row (#276) — the registry container and
every durable collection-store backend.

* :class:`CollectionRegistry` never holds an entry under the reserved id and
  resolves that name (like ``None``) to the pointer target with ONE dict lookup —
  no store is consulted.
* A legacy ``default`` row left behind in a durable registry by the version that
  synthesised one is IGNORED on read (with one log line — the startup line) and
  REMOVED on the store's next write. Every backend: json file, inline JSON,
  sqlite, and postgres (opt-in via ``pg_test_dsn``).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from types import SimpleNamespace

import pytest

from ragstack.api.collections import CollectionEntry, CollectionRegistry
from ragstack.collection_store import (
    RESERVED_COLLECTION_ID,
    CollectionSpec,
    CreateOutcome,
    InMemoryCollectionStore,
    JsonFileCollectionStore,
    PostgresCollectionStore,
    SqliteCollectionStore,
    append_spec_to_file,
    remove_spec_from_file,
)
from ragstack.collection_store import _legacy_row_warned as _warned

# The container tests are sync; only the store-migration tests below are async
# (each marked individually) — a module-level asyncio mark would warn on the
# sync ones.
_async = pytest.mark.asyncio


def _entry(cid: str, shared: bool = False) -> CollectionEntry:
    return CollectionEntry(
        id=cid, label=cid, collection=cid, model="m", dim=4, chunk_method="fixed",
        chunk_size=None, chunk_overlap=None, chunk_params={}, is_shared_surface=shared,
        retriever=None, vector_store=None, text_index=None,
    )


def _spec(cid: str) -> CollectionSpec:
    return CollectionSpec(
        id=cid, collection=f"phys_{cid}", embedding_model="m", embedding_model_dim=4,
        chunk_method="fixed", chunk_size=200,
    )


# --------------------------------------------------------------------------- #
# the container
# --------------------------------------------------------------------------- #


def test_the_registry_refuses_an_entry_under_the_reserved_id():
    with pytest.raises(ValueError, match="pointer name"):
        CollectionRegistry([_entry("default", True)], default_id="default")
    reg = CollectionRegistry([_entry("phys", True)], default_id="phys")
    with pytest.raises(ValueError, match="pointer name"):
        reg.add(_entry("default"))
    assert not reg.has("default")


def test_the_reserved_name_resolves_through_to_the_pointer_target():
    reg = CollectionRegistry([_entry("phys", True), _entry("lib")], default_id="lib")
    assert reg.resolve(None) is reg.resolve("lib")
    assert reg.resolve("default") is reg.resolve("lib")
    assert reg.canonical(None) == reg.canonical("default") == "lib"
    assert reg.canonical("phys") == "phys"
    assert reg.canonical("ghost") == "ghost"  # unknown ids are the router's 404
    with pytest.raises(KeyError):
        reg.resolve("ghost")


def test_an_allowlist_naming_the_pointer_is_expanded_to_the_real_id():
    reg = CollectionRegistry([_entry("phys", True), _entry("lib")], default_id="lib")
    assert reg.permitted(None) is None
    assert reg.permitted({"default", "x"}) == {"lib", "x"}
    assert reg.permitted({"phys"}) == {"phys"}


def test_resolution_of_an_omitted_collection_touches_no_store():
    """The pointer is a dict lookup on the registry. A collection STORE (the
    durable registry) is never asked — the registry was built from it once."""

    class _CountingStore:
        calls = 0

        def __getattr__(self, name):
            def _record(*a, **k):
                _CountingStore.calls += 1
                raise AssertionError(f"store.{name} called during resolve")
            return _record

    store = _CountingStore()
    reg = CollectionRegistry([_entry("phys", True), _entry("lib")], default_id="lib")
    for _ in range(100):
        reg.resolve(None)
        reg.resolve("default")
        reg.canonical(None)
    assert _CountingStore.calls == 0
    assert store is not None  # (held, never consulted)


# --------------------------------------------------------------------------- #
# migration: the legacy row in a durable registry
# --------------------------------------------------------------------------- #


def _settings(tmp_path, **over):
    base = {
        "collections_file": str(tmp_path / "collections.json"),
        "collections_json": "",
    }
    base.update(over)
    return SimpleNamespace(**base)


def _legacy_row() -> dict:
    return json.loads(_spec("default").model_dump_json())


@pytest.fixture(autouse=True)
def _fresh_warn_state():
    _warned.clear()
    yield
    _warned.clear()


@_async
async def test_json_file_ignores_the_legacy_row_and_removes_it_on_the_next_write(tmp_path, caplog):
    path = tmp_path / "collections.json"
    path.write_text(json.dumps([_legacy_row(), _spec("lib").model_dump()]))
    store = JsonFileCollectionStore(_settings(tmp_path))

    with caplog.at_level(logging.WARNING, logger="ragstack.collection_store"):
        assert [s.id for s in await store.list_specs()] == ["lib"]
        assert [r.spec.id for r in await store.list_records()] == ["lib"]
        assert await store.get("default") is None
    lines = [r.message for r in caplog.records if "legacy 'default'" in r.message]
    assert len(lines) == 1, "the startup line is logged ONCE per store, not per read"

    # Untouched on disk until a write...
    assert [d["id"] for d in json.loads(path.read_text())] == ["default", "lib"]
    # ...and gone after the next one.
    assert await store.put(_spec("other")) is True
    assert [d["id"] for d in json.loads(path.read_text())] == ["lib", "other"]


@_async
async def test_json_file_create_and_delete_also_sweep_the_legacy_row(tmp_path):
    path = tmp_path / "collections.json"
    path.write_text(json.dumps([_legacy_row(), _spec("lib").model_dump()]))
    store = JsonFileCollectionStore(_settings(tmp_path))

    # The relic holds no MAX_COLLECTIONS slot: limit=1 with one real row is at
    # cap, limit=2 admits exactly one more.
    assert await store.create(_spec("new"), limit=1) is CreateOutcome.AT_CAP
    assert await store.create(_spec("new"), limit=2) is CreateOutcome.CREATED
    assert [d["id"] for d in json.loads(path.read_text())] == ["lib", "new"]

    path.write_text(json.dumps([_legacy_row(), _spec("lib").model_dump()]))
    # Removing an id that is not there still sweeps the relic — and honestly
    # reports that the CALLER's id was absent.
    assert remove_spec_from_file(str(path), "ghost") is False
    assert [d["id"] for d in json.loads(path.read_text())] == ["lib"]
    # Deleting the pointer name itself is never a delete of anything.
    path.write_text(json.dumps([_legacy_row(), _spec("lib").model_dump()]))
    assert await store.delete("default") is False
    assert [d["id"] for d in json.loads(path.read_text())] == ["lib"]
    assert await store.delete("lib") is True
    assert json.loads(path.read_text()) == []


@_async
async def test_append_helper_sweeps_the_legacy_row(tmp_path):
    path = tmp_path / "collections.json"
    path.write_text(json.dumps([_legacy_row()]))
    assert append_spec_to_file(str(path), _spec("lib")) is True
    assert [d["id"] for d in json.loads(path.read_text())] == ["lib"]


@_async
async def test_inline_json_registry_ignores_the_legacy_row(tmp_path, caplog):
    raw = json.dumps([_legacy_row(), _spec("lib").model_dump()])
    store = JsonFileCollectionStore(_settings(tmp_path, collections_file="", collections_json=raw))
    with caplog.at_level(logging.WARNING, logger="ragstack.collection_store"):
        assert [s.id for s in await store.list_specs()] == ["lib"]
    assert any("legacy 'default'" in r.message for r in caplog.records)


@_async
async def test_memory_store_never_holds_the_reserved_row():
    store = InMemoryCollectionStore([_spec("default"), _spec("lib")])
    assert [s.id for s in await store.list_specs()] == ["lib"]


@_async
async def test_sqlite_ignores_the_legacy_row_and_removes_it_on_the_next_write(tmp_path, caplog):
    db = str(tmp_path / "collections.db")
    store = SqliteCollectionStore(db)
    # Plant the relic the way the old build would have left it: a real row.
    await store.put(_spec("lib"))
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO collections (id, collection) VALUES (?, ?)",
            (RESERVED_COLLECTION_ID, "phys_default"),
        )

    def _ids_on_disk() -> list[str]:
        with sqlite3.connect(db) as conn:
            return [r[0] for r in conn.execute("SELECT id FROM collections ORDER BY id")]

    assert _ids_on_disk() == ["default", "lib"]
    with caplog.at_level(logging.WARNING, logger="ragstack.collection_store"):
        assert [s.id for s in await store.list_specs()] == ["lib"]
        assert [r.spec.id for r in await store.list_records()] == ["lib"]
        assert await store.get("default") is None
    assert sum("legacy 'default'" in r.message for r in caplog.records) == 1
    assert _ids_on_disk() == ["default", "lib"]  # read paths never write

    # The relic holds no cap slot, and the create sweeps it.
    assert await store.create(_spec("new"), limit=1) is CreateOutcome.AT_CAP
    assert await store.create(_spec("new"), limit=2) is CreateOutcome.CREATED
    assert _ids_on_disk() == ["lib", "new"]

    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO collections (id, collection) VALUES (?, ?)",
            (RESERVED_COLLECTION_ID, "phys_default"),
        )
    assert await store.delete("default") is False
    assert _ids_on_disk() == ["lib", "new"]
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO collections (id, collection) VALUES (?, ?)",
            (RESERVED_COLLECTION_ID, "phys_default"),
        )
    assert await store.put(_spec("lib")) is True
    assert _ids_on_disk() == ["lib", "new"]


@_async
async def test_postgres_ignores_the_legacy_row_and_removes_it_on_the_next_write(pg_test_dsn, caplog):
    store = PostgresCollectionStore(pg_test_dsn)
    try:
        await store.put(_spec("lib"))
        pool = await store._pool_()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO collections (id, collection) VALUES ($1, $2)",
                RESERVED_COLLECTION_ID, "phys_default",
            )

            async def _ids_on_disk() -> list[str]:
                rows = await conn.fetch("SELECT id FROM collections ORDER BY id")
                return [r[0] for r in rows]

            assert await _ids_on_disk() == ["default", "lib"]
            with caplog.at_level(logging.WARNING, logger="ragstack.collection_store"):
                assert [s.id for s in await store.list_specs()] == ["lib"]
                assert await store.get("default") is None
            assert sum("legacy 'default'" in r.message for r in caplog.records) == 1
            assert await _ids_on_disk() == ["default", "lib"]

            assert await store.create(_spec("new"), limit=1) is CreateOutcome.AT_CAP
            assert await store.create(_spec("new"), limit=2) is CreateOutcome.CREATED
            assert await _ids_on_disk() == ["lib", "new"]

            await conn.execute(
                "INSERT INTO collections (id, collection) VALUES ($1, $2)",
                RESERVED_COLLECTION_ID, "phys_default",
            )
            assert await store.delete("default") is False
            assert await _ids_on_disk() == ["lib", "new"]
    finally:
        await store.close()
