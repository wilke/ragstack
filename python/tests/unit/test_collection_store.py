"""The durable collection registry (``ragstack.collection_store``).

A collection's identity is its build spec, so the store that maps
``id -> {index, model, dim, chunker}`` has to (a) round-trip that spec faithfully
through every backend, (b) not lose an entry when two writers race, and (c) leave
the shipped ``collections_file`` format byte-compatible so a deployment can
upgrade without touching its config.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from ragstack.collection_store import (
    CollectionSpec,
    InMemoryCollectionStore,
    JsonFileCollectionStore,
    PostgresCollectionStore,
    SqliteCollectionStore,
    make_collection_store,
    seed_from_json,
)

pytestmark = pytest.mark.asyncio


def _spec(cid: str = "acme", **over) -> CollectionSpec:
    base: dict = {
        "id": cid,
        "label": "ACME · SFR / semantic",
        "collection": f"ragstack_lib_{cid}",
        "text_index": f"ragstack_lib_{cid}",
        "embedding_api": "openai",
        "embedding_model": "Salesforce/SFR-Embedding-Mistral",
        "embedding_model_dim": 4096,
        "embedding_endpoints": ["http://localhost:9001", "http://localhost:9002"],
        "embedding_sidecar_url": "",
        "chunk_method": "semantic",
        "chunk_size": None,
        "chunk_overlap": None,
        "chunk_params": {"buffer_size": 5, "breakpoint_percentile_threshold": 92.5},
    }
    base.update(over)
    return CollectionSpec(**base)


def _settings(tmp_path, **over):
    base: dict = {
        "collections_file": str(tmp_path / "collections.json"),
        "collections_json": "",
        "collection_store_backend": "json",
        "collection_store_path": str(tmp_path / "collections.db"),
        "collection_store_dsn": "",
        "postgres_dsn": "",
    }
    base.update(over)
    return SimpleNamespace(**base)


def _stores(tmp_path):
    """Every locally-runnable backend. Postgres needs a server, so it is covered
    separately (schema parity below + an opt-in round-trip)."""
    return {
        "memory": InMemoryCollectionStore(),
        "json": JsonFileCollectionStore(_settings(tmp_path)),
        "sqlite": SqliteCollectionStore(str(tmp_path / "rt.db")),
    }


# --------------------------------------------------------------------------- #
# round-trip
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("backend", ["memory", "json", "sqlite"])
async def test_round_trip_preserves_the_whole_build_spec(tmp_path, backend):
    """Every identity-bearing field survives storage. A backend that quietly
    dropped ``chunk_params`` or the endpoint list would hand the next startup a
    *different* collection than the one that was created."""
    store = _stores(tmp_path)[backend]
    spec = _spec()
    assert await store.put(spec) is True

    assert await store.list_specs() == [spec]
    rec = await store.get("acme")
    assert rec is not None
    assert rec.spec == spec
    # spec_hash is denormalized onto the record — it is the ingest guard's key.
    assert rec.spec_hash == spec.spec_hash() and len(rec.spec_hash) == 8


@pytest.mark.parametrize("backend", ["memory", "json", "sqlite"])
async def test_put_is_an_upsert_and_delete_removes(tmp_path, backend):
    store = _stores(tmp_path)[backend]
    await store.put(_spec("a"))
    await store.put(_spec("b"))
    await store.put(_spec("a", label="renamed"))
    specs = await store.list_specs()
    assert sorted(s.id for s in specs) == ["a", "b"]
    assert [s.label for s in specs if s.id == "a"] == ["renamed"]

    assert await store.delete("a") is True
    assert await store.delete("a") is False
    assert [s.id for s in await store.list_specs()] == ["b"]


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_created_at_survives_an_update(tmp_path, backend):
    """An upsert re-stamps ``updated_at`` but must not re-mint ``created_at`` —
    the registration time is part of the record, not of the last edit."""
    store = _stores(tmp_path)[backend]
    await store.put(_spec("a"))
    first = await store.get("a")
    await store.put(_spec("a", label="v2"))
    second = await store.get("a")
    assert first is not None and second is not None
    assert second.created_at == first.created_at
    assert second.updated_at >= first.updated_at


async def test_sqlite_store_survives_reopen(tmp_path):
    """Durability, the actual point: a new process (here, a new store object over
    the same file) sees what the previous one wrote."""
    path = str(tmp_path / "durable.db")
    await SqliteCollectionStore(path).put(_spec("keepme"))
    assert [s.id for s in await SqliteCollectionStore(path).list_specs()] == ["keepme"]


async def test_sqlite_tolerates_a_table_created_by_an_older_build(tmp_path):
    """§8.1's additive-only migration: ``CREATE TABLE IF NOT EXISTS`` cannot alter,
    so a table missing later columns must be widened by ``ensure_columns`` rather
    than crash on the first SELECT."""
    import sqlite3

    path = str(tmp_path / "old.db")
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE collections (id TEXT PRIMARY KEY, collection TEXT NOT NULL)")
        conn.execute("INSERT INTO collections (id, collection) VALUES ('legacy', 'phys')")
    store = SqliteCollectionStore(path)  # runs ensure_columns
    specs = await store.list_specs()
    assert [s.id for s in specs] == ["legacy"]
    assert specs[0].collection == "phys"
    await store.put(_spec("new"))
    assert sorted(s.id for s in await store.list_specs()) == ["legacy", "new"]


async def test_postgres_store_shares_the_sqlite_schema():
    """Both SQL backends must render the same table from the same DDL string — the
    jobstore.py discipline. TEXT/INTEGER only: no JSONB (sqlite gives it NUMERIC
    affinity), no TIMESTAMPTZ (ISO-8601 text)."""
    from ragstack import collection_store as cs

    ddl = cs._COLLECTIONS_DDL
    assert "JSONB" not in ddl.upper() and "TIMESTAMP" not in ddl.upper()
    assert set(cs._COLLECTIONS_COLUMNS) | {"id"} == set(cs._COLUMNS)
    # Every additively-added column is nullable or defaulted (sqlite's ALTER TABLE
    # ADD COLUMN forbids NOT NULL without a default).
    for name, frag in cs._COLLECTIONS_COLUMNS.items():
        assert "NOT NULL" not in frag or "DEFAULT" in frag, name
    assert isinstance(PostgresCollectionStore("postgresql://x/y"), object)


@pytest.mark.skipif(
    not os.environ.get("RAGSTACK_TEST_POSTGRES_DSN"),
    reason="set RAGSTACK_TEST_POSTGRES_DSN to exercise the postgres backend",
)
async def test_postgres_round_trip():
    store = PostgresCollectionStore(os.environ["RAGSTACK_TEST_POSTGRES_DSN"])
    try:
        await store.put(_spec("pgspec"))
        rec = await store.get("pgspec")
        assert rec is not None and rec.spec == _spec("pgspec")
        assert await store.delete("pgspec") is True
    finally:
        await store.close()


# --------------------------------------------------------------------------- #
# concurrency — the live defect this work exists to close
# --------------------------------------------------------------------------- #


# A writer process, as a script: separate interpreters are the point — an
# in-process lock proves nothing about a sibling uvicorn, and forking out of a
# multi-threaded pytest is its own hazard.
_WRITER = """
import sys
from ragstack.collection_store import CollectionSpec, append_spec_to_file
path, prefix, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
for i in range(n):
    append_spec_to_file(path, CollectionSpec(
        id=f"{prefix}_{i}", collection="phys", embedding_model_dim=8,
        chunk_method="fixed", chunk_size=200))
"""


async def test_json_concurrent_writers_across_processes_lose_nothing(tmp_path):
    """Two prod API instances share one ``collections_file``. Unlocked, the
    read-modify-write (read N, append, replace) drops an entry whenever two
    writers interleave — measured at 14 of 48 surviving. With the flock every id
    must survive."""
    path = str(tmp_path / "shared.collections.json")
    script = tmp_path / "writer.py"
    script.write_text(_WRITER)
    workers, per_worker = 6, 8
    # Point the children at THIS checkout, not whatever `ragstack` the
    # interpreter happens to have installed (worktrees share one env).
    env = dict(os.environ, PYTHONPATH=str(pathlib.Path(__file__).resolve().parents[2]))
    procs = [
        subprocess.Popen(
            [sys.executable, str(script), path, f"c{w}", str(per_worker)], env=env
        )
        for w in range(workers)
    ]
    assert [p.wait(timeout=120) for p in procs] == [0] * workers

    stored = {d["id"] for d in json.loads(open(path, encoding="utf-8").read())}
    expected = {f"c{w}_{i}" for w in range(workers) for i in range(per_worker)}
    assert stored == expected, f"lost {sorted(expected - stored)}"
    # ...and no per-writer temp file leaked next to it.
    assert not [p for p in os.listdir(tmp_path) if p.endswith(".tmp")]


async def test_sqlite_concurrent_writers_lose_nothing(tmp_path):
    """The DB path's answer to the same race: the upsert is one statement, so
    serialization is the database's job rather than a filesystem lock's."""
    store = SqliteCollectionStore(str(tmp_path / "race.db"))
    ids = [f"c{i}" for i in range(40)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda cid: store._put_sync(_spec(cid)), ids))
    assert sorted(s.id for s in await store.list_specs()) == sorted(ids)


# --------------------------------------------------------------------------- #
# the deployed JSON format must not move
# --------------------------------------------------------------------------- #


async def test_json_file_format_is_unchanged(tmp_path):
    """Byte-compatibility with what is deployed: a list of plain spec dicts,
    ``indent=2``, no store bookkeeping (no spec_hash / created_at) leaking into
    the file. An older build must still be able to read a file this one wrote."""
    settings = _settings(tmp_path)
    store = JsonFileCollectionStore(settings)
    await store.put(_spec("acme"))
    raw = open(settings.collections_file, encoding="utf-8").read()
    data = json.loads(raw)
    assert isinstance(data, list) and len(data) == 1
    assert set(data[0]) == set(CollectionSpec.model_fields)
    assert raw == json.dumps(data, indent=2)


async def test_json_file_preserves_unknown_keys_on_rewrite(tmp_path):
    """Prod's registry carries hand-authored annotations (``_alias_note``). A
    write-through append must not silently strip a neighbouring entry's keys."""
    settings = _settings(tmp_path)
    hand = _spec("hand").model_dump() | {"_alias_note": "prefer the alias"}
    with open(settings.collections_file, "w", encoding="utf-8") as f:
        json.dump([hand], f, indent=2)
    await JsonFileCollectionStore(settings).put(_spec("added"))
    data = json.loads(open(settings.collections_file, encoding="utf-8").read())
    assert [d["id"] for d in data] == ["hand", "added"]
    assert data[0]["_alias_note"] == "prefer the alias"


async def test_json_store_without_a_file_cannot_persist(tmp_path):
    """No ``collections_file`` → in-memory only, reported as ``False`` so the
    create path can warn "lost on restart". Unchanged behaviour."""
    store = JsonFileCollectionStore(_settings(tmp_path, collections_file=""))
    assert await store.put(_spec()) is False
    assert await store.list_specs() == []


async def test_json_store_reads_inline_collections_json(tmp_path):
    settings = _settings(tmp_path, collections_file="",
                         collections_json=json.dumps([_spec("inline").model_dump()]))
    store = JsonFileCollectionStore(settings)
    assert [s.id for s in await store.list_specs()] == ["inline"]
    assert await store.put(_spec("x")) is False  # inline config is not writable


async def test_duplicate_ids_in_the_file_are_rejected(tmp_path):
    settings = _settings(tmp_path)
    rows = [_spec("dup").model_dump(), _spec("dup", label="other").model_dump()]
    with open(settings.collections_file, "w", encoding="utf-8") as f:
        json.dump(rows, f)
    with pytest.raises(RuntimeError, match="duplicate collection ids"):
        await JsonFileCollectionStore(settings).list_specs()


# --------------------------------------------------------------------------- #
# backend selection + the migration story
# --------------------------------------------------------------------------- #


async def test_backend_selection(tmp_path):
    assert isinstance(make_collection_store(_settings(tmp_path)), JsonFileCollectionStore)
    assert isinstance(
        make_collection_store(_settings(tmp_path, collection_store_backend="memory")),
        InMemoryCollectionStore,
    )
    assert isinstance(
        make_collection_store(_settings(tmp_path, collection_store_backend="sqlite")),
        SqliteCollectionStore,
    )
    assert isinstance(
        make_collection_store(_settings(tmp_path, collection_store_backend="postgres",
                                        collection_store_dsn="postgresql://x/y")),
        PostgresCollectionStore,
    )
    # An unknown backend falls back to the shipped one rather than failing boot.
    assert isinstance(
        make_collection_store(_settings(tmp_path, collection_store_backend="wat")),
        JsonFileCollectionStore,
    )


async def test_sqlite_backend_is_seeded_once_from_the_collections_file(tmp_path):
    """The whole upgrade path: point an existing deployment at sqlite, restart,
    and its registry is imported. Idempotent, and it must never resurrect a
    collection that was deleted after the seed."""
    settings = _settings(tmp_path, collection_store_backend="sqlite")
    with open(settings.collections_file, "w", encoding="utf-8") as f:
        json.dump([_spec("a").model_dump(), _spec("b").model_dump()], f, indent=2)

    store = make_collection_store(settings)
    assert await seed_from_json(store, settings) == 2
    assert sorted(s.id for s in await store.list_specs()) == ["a", "b"]

    # Second boot: already populated, so no re-import...
    assert await seed_from_json(store, settings) == 0
    # ...even after a delete, which must stay deleted.
    await store.delete("a")
    assert await seed_from_json(store, settings) == 0
    assert [s.id for s in await store.list_specs()] == ["b"]
    # The JSON file is left untouched, so rolling the backend back is a no-op.
    assert [d["id"] for d in json.loads(open(settings.collections_file).read())] == ["a", "b"]


async def test_seeding_never_touches_the_json_or_memory_backends(tmp_path):
    settings = _settings(tmp_path)
    with open(settings.collections_file, "w", encoding="utf-8") as f:
        json.dump([_spec("a").model_dump()], f)
    assert await seed_from_json(JsonFileCollectionStore(settings), settings) == 0
    assert await seed_from_json(InMemoryCollectionStore(), settings) == 0
