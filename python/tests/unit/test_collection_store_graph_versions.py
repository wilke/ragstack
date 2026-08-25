"""``CollectionRecord.graph_archived_versions`` + ``append_graph_version``
(#350): the additive lifecycle field recording which archive versions carry
their graph leg — the flag eviction's graph drop is gated on (#380). All four
backends (Postgres via ``pg_test_dsn``): idempotent append, independent of
``versions``, preserved across a spec upsert, and migrated onto a pre-#350
sqlite table in place.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from ragstack.collection_store import (
    LIFECYCLE_FIELDS,
    CollectionSpec,
    InMemoryCollectionStore,
    JsonFileCollectionStore,
    PostgresCollectionStore,
    SqliteCollectionStore,
    lifecycle_of,
)


def _spec(cid: str) -> CollectionSpec:
    return CollectionSpec(id=cid, collection=f"{cid}_phys", embedding_model="m",
                          embedding_model_dim=4)


async def _assert_graph_versions(store) -> None:
    await store.put(_spec("a"))
    await store.put(_spec("b"))
    rec = await store.get("a")
    assert rec.graph_archived_versions == [] and rec.versions == []
    assert await store.append_version("a", 1) == [1]
    assert await store.append_version("a", 2) == [1, 2]
    assert await store.append_graph_version("a", 2) == [2]
    assert await store.append_graph_version("a", 2) == [2]  # idempotent
    assert await store.append_graph_version("a", 1) == [2, 1]  # insertion order kept
    assert await store.append_graph_version("nope", 1) == []
    rec = await store.get("a")
    assert rec.graph_archived_versions == [2, 1] and rec.versions == [1, 2]
    assert (await store.get("b")).graph_archived_versions == []
    # A spec upsert never resets the lifecycle, this field included.
    await store.put(_spec("a").model_copy(update={"label": "renamed"}))
    rec = await store.get("a")
    assert rec.spec.label == "renamed" and rec.graph_archived_versions == [2, 1]
    assert lifecycle_of(rec)["graph_archived_versions"] == [2, 1]
    assert [r.graph_archived_versions for r in await store.list_records()
            if r.spec.id == "a"] == [[2, 1]]
    with pytest.raises(ValueError):
        await store.append_graph_version("a", -1)


def test_field_is_a_lifecycle_field() -> None:
    assert "graph_archived_versions" in LIFECYCLE_FIELDS


@pytest.mark.asyncio
async def test_memory() -> None:
    await _assert_graph_versions(InMemoryCollectionStore())


@pytest.mark.asyncio
async def test_sqlite(tmp_path: Path) -> None:
    await _assert_graph_versions(SqliteCollectionStore(str(tmp_path / "c.db")))


@pytest.mark.asyncio
async def test_json_file_sidecar(tmp_path: Path) -> None:
    path = tmp_path / "collections.json"
    path.write_text("[]")
    store = JsonFileCollectionStore(SimpleNamespace(collections_file=str(path),
                                                    collections_json=""))
    await _assert_graph_versions(store)
    sidecar = json.loads((tmp_path / "collections.json.lifecycle.json").read_text())
    assert sidecar["a"]["graph_archived_versions"] == [2, 1]
    assert json.loads(path.read_text())[0]["id"] == "a"  # the registry file format is untouched
    assert "graph_archived_versions" not in json.loads(path.read_text())[0]


@pytest.mark.asyncio
async def test_postgres(pg_test_dsn: str) -> None:
    store = PostgresCollectionStore(pg_test_dsn)
    try:
        await _assert_graph_versions(store)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_sqlite_pre_existing_table_gets_the_column(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(
            "CREATE TABLE collections (id TEXT PRIMARY KEY, label TEXT NOT NULL DEFAULT '',"
            " collection TEXT NOT NULL, embedding_model_dim INTEGER NOT NULL DEFAULT 0,"
            " versions TEXT NOT NULL DEFAULT '[3]')"
        )
        conn.execute("INSERT INTO collections (id, collection) VALUES ('old', 'old_phys')")
    store = SqliteCollectionStore(str(path))
    with closing(sqlite3.connect(path)) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(collections)")}
    assert "graph_archived_versions" in cols
    rec = await store.get("old")
    assert rec.versions == [3] and rec.graph_archived_versions == []
    assert await store.append_graph_version("old", 3) == [3]
    assert (await store.get("old")).graph_archived_versions == [3]
