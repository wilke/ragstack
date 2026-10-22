"""Give rerank candidates their symbol glossary before the cross-encoder reads it.

Owner decision 2026-10-22 (#754): the reranker scores ``content + "\\n" +
symbol_glossary`` (``scoring.scorers.rerank_passage``,
``RERANK_INCLUDE_SYMBOL_GLOSSARY``, default on). But only one retrieval leg
carries the glossary:

* the BM25 leg — ``ElasticsearchTextIndex.search`` reads
  ``_source.symbol_glossary`` into the ``Chunk``;
* the vector leg does NOT — the Qdrant payload has no glossary
  (``QdrantVectorStore.upsert`` never writes it, ``_chunk_from_payload``
  cannot read it), so a dense hit is a ``Chunk`` with ``symbol_glossary=""``;
* graph pseudo-chunks (``graph-…`` ids) are triples, not indexed chunks, and
  have none.

Which object survives fusion is "last list wins" (``RRFScorer.fuse`` keeps
``chunks[key] = scored.chunk`` for each list in order). Inside one
``HybridRetriever.retrieve`` the lists are ``[vector, text, graph]``, so a chunk
both legs found keeps the BM25 copy and its glossary. Two cases lose it:

* a candidate only the vector leg found (or a ``retrieval_mode="vector"``
  request) — never had it;
* HyDE / multi-query: the router fuses the per-variant lists again, and the
  LAST variant's copy wins; if that variant found the chunk by vector only, the
  glossary an earlier variant's BM25 hit carried is dropped.

So before a rerank that reads the glossary, every candidate still without one
is looked up in its OWN collection's text index, in one batched,
tenant-scoped request per collection (:meth:`get_symbol_glossaries`). The
offline trial that measured the gain did exactly this (every pool chunk's
glossary fetched from ES). A failure degrades to "no glossary" with a warning —
the rerank still runs, and the request never fails because of it.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ragstack.models import ScoredChunk
from ragstack.tenancy import tenant_of

log = logging.getLogger(__name__)

#: Graph pseudo-chunk id prefix (``retrieval.retriever.graph_context``): never
#: an indexed chunk, so never looked up.
_GRAPH_PREFIX = "graph-"


def _needs_glossary(s: ScoredChunk) -> bool:
    return not s.chunk.symbol_glossary and not s.chunk.id.startswith(_GRAPH_PREFIX)


def _scope_of(filters: dict[str, Any] | None) -> list[str]:
    """The readable tenants in a scoped filter dict (``tenancy.scope_filters``
    puts them under ``tenant_id``). Empty when the dict is unscoped — and then
    nothing is read: the lookup is never unscoped."""
    raw = (filters or {}).get("tenant_id")
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        return [str(t) for t in raw]
    return [str(raw)]


async def _fill_from(
    text_index: Any,
    scored: list[ScoredChunk],
    positions: list[int],
    filters: dict[str, Any] | None,
    collection: str | None,
) -> dict[int, str]:
    """One batched lookup for ``positions`` of ``scored`` in ``text_index``;
    returns position → glossary for those that have one. Never raises."""
    lookup = getattr(text_index, "get_symbol_glossaries", None)
    if lookup is None or not positions:
        return {}
    tenants = _scope_of(filters)
    if not tenants:
        log.warning(
            "rerank glossary hydration skipped for collection %s: no tenant scope",
            collection or "-",
        )
        return {}
    ids = list(dict.fromkeys(scored[i].chunk.id for i in positions))
    try:
        found = await lookup(ids, tenants)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — degrade, never fail the request
        log.warning(
            "rerank glossary hydration failed for collection %s (%s: %s); "
            "reranking those candidates without their glossary",
            collection or "-", type(e).__name__, e,
        )
        return {}
    out: dict[int, str] = {}
    for i in positions:
        chunk = scored[i].chunk
        glossary = found.get((tenant_of(chunk), chunk.id))
        if glossary:
            out[i] = glossary
    return out


async def hydrate_symbol_glossary(
    retriever: Any,
    scored: list[ScoredChunk],
    filters: dict[str, Any] | None,
    tenant_id: str | None,
) -> list[ScoredChunk]:
    """``scored`` with ``symbol_glossary`` filled in, from the candidate's own
    collection's text index, for every candidate that has none.

    * Single collection (a retriever with a ``text_index``): one lookup with
      ``filters`` — the request's scoped filter dict, whose ``tenant_id`` is
      the caller's readable tenants (own + public + share widening).
    * Multi-collection (a retriever with ``legs``): candidates are grouped by
      their ``collection`` stamp, and each group is looked up in that leg's
      text index with that leg's own scope (``CollectionLeg.filters``) — one
      request per collection, run concurrently.
    * Anything else (a test stub, a text index without the lookup): unchanged.

    A hydrated candidate is a ``model_copy`` of its chunk with only
    ``symbol_glossary`` set — ``content``, metadata (including the
    multi-collection stamp) and the score are untouched, so the response's
    sources are the same. Candidates are returned in the same order."""
    positions = [i for i, s in enumerate(scored) if _needs_glossary(s)]
    if not positions:
        return scored

    legs = getattr(retriever, "legs", None)
    if legs is not None:
        by_id = {leg.id: leg for leg in legs}
        groups: dict[str, list[int]] = {}
        for i in positions:
            cid = scored[i].collection
            if cid is not None and cid in by_id:
                groups.setdefault(cid, []).append(i)
        results = await asyncio.gather(
            *(
                _fill_from(
                    getattr(by_id[cid].retriever, "text_index", None),
                    scored,
                    idx,
                    by_id[cid].filters(filters, tenant_id),
                    cid,
                )
                for cid, idx in groups.items()
            )
        )
        filled: dict[int, str] = {}
        for r in results:
            filled.update(r)
    else:
        filled = await _fill_from(
            getattr(retriever, "text_index", None),
            scored,
            positions,
            filters,
            getattr(retriever, "collection", None),
        )
    if not filled:
        return scored
    return [
        s.model_copy(
            update={"chunk": s.chunk.model_copy(update={"symbol_glossary": filled[i]})}
        )
        if i in filled
        else s
        for i, s in enumerate(scored)
    ]
