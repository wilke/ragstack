"""Rebuild document text from an index's chunks — read-only (#754).

The symbol glossary is a *document*-level table, but an already-built index holds
only chunks. Every chunk carries ``start_char`` / ``end_char`` into its
document's text, and the chunkers cover the text with overlapping windows, so
pasting each chunk's new tail in ``start_char`` order recovers the document
(measured on dev openchia: 289 documents, 0 gaps).

Used by the offline LLM glossary step (``scripts/symbol_glossary_llm.py``), the
backfill (``scripts/backfill_symbol_glossary.py``) and the #754 scratch
experiment. :func:`scroll_index` issues ONLY ``open_point_in_time`` /
``search`` / ``close_point_in_time`` — it has no write path to misuse.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Reassembled:
    doc_id: str
    text: str
    #: Places where the next chunk started past the end of the text so far —
    #: filled with spaces so offsets stay true. 0 on every document measured.
    gaps: int
    #: Chunks whose ``len(content)`` disagrees with ``end_char - start_char``;
    #: their offsets cannot be trusted, so the text is approximate.
    length_mismatches: int


def reassemble(doc_id: str, chunks: Iterable[Mapping[str, Any]]) -> Reassembled:
    """Rebuild one document from its chunk rows (``content``, ``start_char``,
    ``end_char``). Offsets in the result equal the chunks' offsets, so a chunk's
    text is ``result.text[start_char:end_char]`` when nothing mismatched."""
    rows = sorted(chunks, key=lambda c: (int(c.get("start_char") or 0), int(c.get("end_char") or 0)))
    parts: list[str] = []
    pos = 0
    gaps = mismatches = 0
    for c in rows:
        start = int(c.get("start_char") or 0)
        end = int(c.get("end_char") or 0)
        text = str(c.get("content") or "")
        if len(text) != end - start:
            mismatches += 1
            end = start + len(text)
        if start > pos:
            gaps += 1
            parts.append(" " * (start - pos))
            pos = start
        if end > pos:
            parts.append(text[pos - start :])
            pos = end
    return Reassembled(doc_id, "".join(parts), gaps, mismatches)


async def scroll_index(
    es: Any,
    index: str,
    *,
    source: list[str] | None = None,
    query: dict[str, Any] | None = None,
    page_size: int = 1000,
    keep_alive: str = "5m",
    sort: list[dict[str, Any]] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield every hit of ``index`` (``{"_id", "_source"}``) via PIT +
    ``search_after`` — consistent under concurrent writes, no scroll context
    left behind. ``es`` is an ``AsyncElasticsearch``; only read APIs are called.
    ``sort`` precedes the ``_shard_doc`` tiebreaker (e.g. ``[{"doc_id": "asc"}]``
    to stream one document's chunks contiguously)."""
    pit = (await es.open_point_in_time(index=index, keep_alive=keep_alive))["id"]
    try:
        after: list[Any] | None = None
        while True:
            body: dict[str, Any] = {
                "size": page_size,
                "pit": {"id": pit, "keep_alive": keep_alive},
                "sort": [*(sort or []), {"_shard_doc": "asc"}],
                "query": query or {"match_all": {}},
            }
            if source is not None:
                body["_source"] = source
            if after is not None:
                body["search_after"] = after
            resp = await es.search(body=body)
            pit = resp.get("pit_id", pit)
            hits = resp["hits"]["hits"]
            if not hits:
                return
            for h in hits:
                yield h
            after = hits[-1]["sort"]
    finally:
        try:
            await es.close_point_in_time(body={"id": pit})
        except Exception:  # noqa: BLE001 — a PIT expires on its own
            pass


async def documents_from_index(
    es: Any, index: str, *, query: dict[str, Any] | None = None
) -> AsyncIterator[tuple[Reassembled, list[dict[str, Any]]]]:
    """Group an index's chunks by ``doc_id`` and yield each document reassembled,
    with its chunk rows (``_id`` + ``_source``). Holds the whole index's chunk
    rows in memory (fine for a collection the size of openchia, 33 MB; the
    backfill notes the open-access sizing)."""
    by_doc: dict[str, list[dict[str, Any]]] = {}
    async for hit in scroll_index(es, index, query=query):
        row = dict(hit["_source"])
        row["_id"] = hit["_id"]
        by_doc.setdefault(str(row.get("doc_id", "")), []).append(row)
    for doc_id in sorted(by_doc):
        rows = by_doc[doc_id]
        yield reassemble(doc_id, rows), rows


async def iter_documents_sorted(
    es: Any, index: str, *, query: dict[str, Any] | None = None, page_size: int = 1000
) -> AsyncIterator[tuple[Reassembled, list[dict[str, Any]]]]:
    """Like :func:`documents_from_index`, but STREAMING: hits are sorted by the
    ``doc_id`` keyword, so one document's chunks arrive contiguously and only
    one document is held at a time — the shape a backfill over a 47.6M-chunk
    index needs."""
    current: str | None = None
    rows: list[dict[str, Any]] = []
    async for hit in scroll_index(es, index, query=query, page_size=page_size,
                                  sort=[{"doc_id": "asc"}]):
        row = dict(hit["_source"])
        row["_id"] = hit["_id"]
        doc_id = str(row.get("doc_id", ""))
        if current is not None and doc_id != current:
            yield reassemble(current, rows), rows
            rows = []
        current = doc_id
        rows.append(row)
    if current is not None and rows:
        yield reassemble(current, rows), rows
