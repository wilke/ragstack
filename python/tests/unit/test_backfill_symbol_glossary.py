"""scripts/backfill_symbol_glossary.py (#754): dry run writes nothing; --apply
writes partial updates only; a re-run is resumable; --apply needs the index
named twice. A fake client — no Elasticsearch."""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import Any

import pytest

from ragstack.ingestion.symbol_glossary import VERSION_KEY

_spec = importlib.util.spec_from_file_location(
    "backfill_symbol_glossary",
    Path(__file__).resolve().parents[2] / "scripts" / "backfill_symbol_glossary.py",
)
assert _spec and _spec.loader
bf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bf)

TEXT = "where Qb is the breathing flow rate. " + "pad " * 30 + "Table: Qb = 0.5 m3/h."


def _rows(doc_id: str, text: str, size: int = 60, overlap: int = 10) -> list[dict[str, Any]]:
    rows, start, i = [], 0, 0
    while start < len(text):
        end = min(len(text), start + size)
        rows.append({"_id": f"t:{doc_id}-{i}", "doc_id": doc_id, "chunk_id": f"{doc_id}-{i}",
                     "content": text[start:end], "start_char": start, "end_char": end,
                     "metadata": {"tenant_id": "t"}})
        if end == len(text):
            break
        start, i = end - overlap, i + 1
    return rows


class FakeES:
    """Serves rows sorted by doc_id through the PIT/search API; records writes."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.bulks: list[list[dict[str, Any]]] = []
        self.mappings: list[Any] = []
        self.indices = self

    async def put_mapping(self, index: str, body: Any) -> None:
        self.mappings.append(body)

    async def get(self, index: str) -> dict[str, Any]:
        return getattr(self, "resolves", {index: {}})

    async def open_point_in_time(self, index: str, keep_alive: str) -> dict[str, str]:
        return {"id": "pit"}

    async def close_point_in_time(self, body: Any) -> None:
        return None

    async def search(self, body: dict[str, Any]) -> dict[str, Any]:
        assert body["sort"][0] == {"doc_id": "asc"}
        ordered = sorted(self.rows, key=lambda r: (r["doc_id"], r["_id"]))
        start = int(body["search_after"][0]) if "search_after" in body else 0
        page = ordered[start : start + body["size"]]
        return {"pit_id": "pit", "hits": {"hits": [
            {"_id": r["_id"], "_source": {k: v for k, v in r.items() if k != "_id"},
             "sort": [start + j + 1]} for j, r in enumerate(page)]}}

    async def bulk(self, operations: list[dict[str, Any]], refresh: bool) -> dict[str, Any]:
        self.bulks.append(operations)
        for action, doc in zip(operations[::2], operations[1::2], strict=True):
            row = next(r for r in self.rows if r["_id"] == action["update"]["_id"])
            row["symbol_glossary"] = doc["doc"]["symbol_glossary"]
            row["metadata"].update(doc["doc"]["metadata"])
        return {"errors": False, "items": []}


def _args(apply: bool = False) -> Any:
    return bf.main.__globals__["argparse"].Namespace(
        es_url="http://127.0.0.1:1", index="i", llm_cache="", config="", batch_size=2,
        apply=apply, confirm_index="i" if apply else "", refresh_off=False)


def test_dry_run_writes_nothing() -> None:
    es = FakeES(_rows("d1", TEXT) + _rows("d2", "plain words only " * 10))
    out = asyncio.run(bf.run(_args(), es=es))
    assert es.bulks == [] and es.mappings == []
    assert out["dry_run"] is True and out["docs"] == 2
    assert out["chunks_to_update"] == out["chunks"] and out["chunks_with_glossary"] >= 1


def test_apply_writes_partial_updates_then_is_resumable() -> None:
    es = FakeES(_rows("d1", TEXT))
    out = asyncio.run(bf.run(_args(apply=True), es=es))
    assert es.mappings and out["chunks_written"] == out["chunks"]
    for ops in es.bulks:
        for action, doc in zip(ops[::2], ops[1::2], strict=True):
            assert set(action) == {"update"} and set(doc["doc"]) == {"symbol_glossary", "metadata"}
    last = es.rows[-1]
    assert last["symbol_glossary"] == "Qb — breathing flow rate"
    assert last["metadata"][VERSION_KEY] == "rules/2" and "content" in last
    again = asyncio.run(bf.run(_args(apply=True), es=es))
    assert again["chunks_to_update"] == 0 and again["chunks_current"] == again["chunks"]


def test_apply_requires_confirm_index() -> None:
    with pytest.raises(SystemExit):
        bf.main(["--es-url", "http://127.0.0.1:1", "--index", "i", "--apply"])
    with pytest.raises(SystemExit):
        bf.main(["--es-url", "http://127.0.0.1:1", "--index", "i", "--apply", "--confirm-index", "j"])


def test_reassembly_streams_documents_and_reports_gaps() -> None:
    rows = _rows("d1", TEXT)
    del rows[1]  # a hole
    es = FakeES(rows)
    out = asyncio.run(bf.run(_args(), es=es))
    assert out["docs"] == 1 and out["reassembly_gaps"] == 1


@pytest.mark.parametrize("name", ["*", "ragstack_*", "a,b", "_all", "-a", "_x", "a?", ""])
def test_rejects_patterns_lists_and_non_concrete_names(name: str) -> None:
    assert bf.check_index_name(name) is not None
    with pytest.raises(SystemExit):
        bf.main(["--es-url", "http://127.0.0.1:1", "--index", name])


def test_accepts_one_concrete_name() -> None:
    assert bf.check_index_name("ragstack_lib_openchia_x_7033902e") is None


def test_refuses_an_alias_or_multi_resolution() -> None:
    es = FakeES(_rows("d1", TEXT))
    es.resolves = {"real_index_1": {}, "real_index_2": {}}  # type: ignore[attr-defined]
    with pytest.raises(SystemExit):
        asyncio.run(bf.run(_args(), es=es))
    es.resolves = {"other": {}}  # type: ignore[attr-defined]
    with pytest.raises(SystemExit):
        asyncio.run(bf.run(_args(apply=True), es=es))
    assert es.bulks == [] and es.mappings == []
