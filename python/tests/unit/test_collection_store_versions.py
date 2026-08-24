"""``CollectionStore.next_version`` (#203/#353): the registry hands out the
archive version numbers (``versions/<n>/``) — 1, 2, 3 … per collection, one
atomic increment per call, never reset by a spec upsert, ``KeyError`` for an
unregistered id. The JSON-file registry cannot persist a counter and says so
(``NotImplementedError``). Postgres is opt-in via ``pg_test_dsn``.
"""
from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from ragstack.collection_store import (
    CollectionSpec,
    InMemoryCollectionStore,
    JsonFileCollectionStore,
    PostgresCollectionStore,
    SqliteCollectionStore,
)


def _spec(cid: str) -> CollectionSpec:
    return CollectionSpec(id=cid, collection=f"{cid}_phys", embedding_model="m",
                          embedding_model_dim=4)


async def _assert_versions(store) -> None:
    await store.put(_spec("a"))
    await store.put(_spec("b"))
    assert [await store.next_version("a") for _ in range(3)] == [1, 2, 3]
    assert await store.next_version("b") == 1  # independent per collection
    # Re-upserting the spec (put) must not reset the counter.
    await store.put(_spec("a").model_copy(update={"label": "renamed"}))
    assert await store.next_version("a") == 4
    with pytest.raises(KeyError):
        await store.next_version("nope")
    # Concurrent reservations never collide.
    got = await asyncio.gather(*(store.next_version("b") for _ in range(8)))
    assert sorted(got) == list(range(2, 10))
    assert await store.delete("a")
    with pytest.raises(KeyError):
        await store.next_version("a")


@pytest.mark.asyncio
async def test_memory_next_version() -> None:
    await _assert_versions(InMemoryCollectionStore())


@pytest.mark.asyncio
async def test_sqlite_next_version(tmp_path: Path) -> None:
    await _assert_versions(SqliteCollectionStore(str(tmp_path / "c.db")))


@pytest.mark.asyncio
async def test_postgres_next_version(pg_test_dsn: str) -> None:
    store = PostgresCollectionStore(pg_test_dsn)
    try:
        await _assert_versions(store)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_sqlite_pre_existing_table_gets_the_counter_column(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(
            "CREATE TABLE collections (id TEXT PRIMARY KEY, label TEXT NOT NULL DEFAULT '',"
            " collection TEXT NOT NULL, embedding_model_dim INTEGER NOT NULL DEFAULT 0)"
        )
        conn.execute("INSERT INTO collections (id, collection) VALUES ('old', 'old_phys')")
    store = SqliteCollectionStore(str(path))
    with closing(sqlite3.connect(path)) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(collections)")}
    assert "archive_version" in cols
    assert await store.next_version("old") == 1
    assert await store.next_version("old") == 2


@pytest.mark.asyncio
async def test_json_store_refuses_to_track_versions(tmp_path: Path) -> None:
    f = tmp_path / "collections.json"
    f.write_text("[]", encoding="utf-8")
    store = JsonFileCollectionStore(SimpleNamespace(collections_file=str(f), collections_json=""))
    assert await store.put(_spec("a"))
    with pytest.raises(NotImplementedError, match="sqlite or postgres"):
        await store.next_version("a")
