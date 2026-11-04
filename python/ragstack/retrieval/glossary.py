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
tenant-scoped request per collection (``get_symbol_glossaries``). The offline
trial that measured the gain did exactly this (every pool chunk's glossary
fetched from ES; docs/plans/symbol-glossary.md#dev-trial-2026-10-22).

**It must not make a vector search depend on Elasticsearch.** The lookup is
bounded by its own short timeout (``RERANK_GLOSSARY_TIMEOUT``, default 2 s;
``<= 0`` turns the lookup off), a collection with no text index is skipped, a
missing ES index is "no glossaries", and any failure degrades to "no glossary"
— the rerank still runs and the request never fails. The failure warning is
rate-limited per collection (:data:`WARN_INTERVAL_S`) and every failure is
counted in :data:`failure_counts`, so an ES outage costs one log line a minute
per collection, not one per query.

Two entry points:

* :func:`hydrate_symbol_glossary` — the API's: a retriever (single- or
  multi-collection) and its scoped filters;
* :func:`hydrate_chunks` — the library one, for callers that hold a text index
  and plain chunks (the eval scripts' ``--rerank-symbol-glossary``); it needs
  no router, request context or settings.

The API entry point has a second caller: ``/v1/retrieve`` with
``include_symbol_glossary: true`` (``routers/query.py::_glossary_for_sources``)
runs it once more over the FINAL, top_k-cut sources so each can carry its
glossary in the response — same batching, scope, timeout and degrade rules.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from typing import Any

from ragstack.models import Chunk, ScoredChunk
from ragstack.tenancy import tenant_of

log = logging.getLogger(__name__)

#: Graph pseudo-chunk id prefix (``retrieval.retriever.graph_context``): never
#: an indexed chunk, so never looked up.
_GRAPH_PREFIX = "graph-"

#: The default bound on one lookup, in seconds — ``Settings.rerank_glossary_timeout``.
DEFAULT_TIMEOUT_S = 2.0

#: At most one "hydration failed" warning per collection per this many seconds.
WARN_INTERVAL_S = 60.0

#: Failures per collection label since process start — the counter a metrics
#: exporter would read; there is no metrics hook in this codebase yet.
failure_counts: Counter[str] = Counter()
_last_warned: dict[str, float] = {}
_suppressed: Counter[str] = Counter()


def _note_failure(label: str, e: BaseException) -> None:
    failure_counts[label] += 1
    now = time.monotonic()
    last = _last_warned.get(label)
    if last is not None and now - last < WARN_INTERVAL_S:
        _suppressed[label] += 1
        return
    _last_warned[label] = now
    suppressed = _suppressed.pop(label, 0)
    log.warning(
        "symbol glossary hydration failed for collection %s (%s: %s); continuing "
        "with those chunks without their glossary [%d failure(s) total, %d not "
        "logged in the last %gs]",
        label, type(e).__name__, e, failure_counts[label], suppressed, WARN_INTERVAL_S,
    )


def _reset_failure_log() -> None:
    """Test hook: forget the rate-limit state and counts."""
    failure_counts.clear()
    _last_warned.clear()
    _suppressed.clear()


def _needs_glossary(chunk: Chunk) -> bool:
    return not chunk.symbol_glossary and not chunk.id.startswith(_GRAPH_PREFIX)


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


async def _lookup(
    text_index: Any,
    chunks: list[Chunk],
    tenants: list[str],
    timeout: float | None,
    label: str,
) -> dict[int, str]:
    """One batched lookup for every chunk in ``chunks`` that needs a glossary;
    returns index-into-``chunks`` → glossary for those that have one.
    Never raises (cancellation aside)."""
    lookup = getattr(text_index, "get_symbol_glossaries", None)
    if lookup is None:
        return {}  # no text index (or one that cannot answer): nothing to hydrate from
    if timeout is not None and timeout <= 0:
        return {}  # RERANK_GLOSSARY_TIMEOUT <= 0: lookup off
    positions = [i for i, c in enumerate(chunks) if _needs_glossary(c)]
    if not positions:
        return {}
    if not tenants:
        log.debug("rerank glossary hydration skipped for %s: no tenant scope", label)
        return {}
    ids = list(dict.fromkeys(chunks[i].id for i in positions))
    try:
        # The store bounds its own request (no retries); wait_for is the
        # backstop for any text index, so the lookup can never add more than
        # ``timeout`` to the query.
        call = lookup(ids, tenants, timeout=timeout)
        found = await (asyncio.wait_for(call, timeout) if timeout is not None else call)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — degrade, never fail the request
        _note_failure(label, e)
        return {}
    out: dict[int, str] = {}
    for i in positions:
        chunk = chunks[i]
        glossary = found.get((tenant_of(chunk), chunk.id))
        if glossary:
            out[i] = glossary
    return out


def _with_glossary(chunk: Chunk, glossary: str) -> Chunk:
    return chunk.model_copy(update={"symbol_glossary": glossary})


async def hydrate_chunks(
    text_index: Any,
    chunks: list[Chunk],
    tenants: list[str],
    *,
    timeout: float | None = DEFAULT_TIMEOUT_S,
    label: str = "-",
) -> list[Chunk]:
    """``chunks`` with ``symbol_glossary`` filled in from ``text_index`` for
    every chunk that has none, in ONE batched lookup scoped to ``tenants``
    (the caller's readable tenants, e.g. ``tenancy.readable_tenants(t)``).

    The library entry point: no router, request context or settings needed.
    Order is kept; a hydrated chunk is a copy with only ``symbol_glossary``
    set. ``text_index`` may be ``None`` (no-op). Never raises."""
    found = await _lookup(text_index, chunks, tenants, timeout, label)
    if not found:
        return chunks
    return [_with_glossary(c, found[i]) if i in found else c for i, c in enumerate(chunks)]


async def hydrate_symbol_glossary(
    retriever: Any,
    scored: list[ScoredChunk],
    filters: dict[str, Any] | None,
    tenant_id: str | None,
    *,
    timeout: float | None = DEFAULT_TIMEOUT_S,
) -> list[ScoredChunk]:
    """``scored`` with ``symbol_glossary`` filled in, from the candidate's own
    collection's text index, for every candidate that has none.

    * Single collection (a retriever with a ``text_index``): one lookup with
      ``filters`` — the request's scoped filter dict, whose ``tenant_id`` is
      the caller's readable tenants (own + public + share widening).
    * Multi-collection (a retriever with ``legs``): candidates are grouped by
      their ``collection`` stamp, and each group is looked up in that leg's
      text index with that leg's own scope (``CollectionLeg.filters``) — one
      request per collection, run concurrently, so ``timeout`` bounds the whole
      call, not each leg in turn. A leg without a text index is skipped.
    * Anything else (a test stub, no text index): unchanged.

    A hydrated candidate is a ``model_copy`` of its chunk with only
    ``symbol_glossary`` set — ``content``, metadata (including the
    multi-collection stamp) and the score are untouched, so the response's
    sources are the same. Candidates are returned in the same order."""
    if not any(_needs_glossary(s.chunk) for s in scored):
        return scored

    filled: dict[int, str] = {}
    legs = getattr(retriever, "legs", None)
    if legs is not None:
        by_id = {leg.id: leg for leg in legs}
        groups: dict[str, list[int]] = {}
        for i, s in enumerate(scored):
            if s.collection is not None and s.collection in by_id:
                groups.setdefault(s.collection, []).append(i)
        jobs = []
        for cid, idx in groups.items():
            text_index = getattr(by_id[cid].retriever, "text_index", None)
            if text_index is None:
                continue
            jobs.append((idx, _lookup(
                text_index,
                [scored[i].chunk for i in idx],
                _scope_of(by_id[cid].filters(filters, tenant_id)),
                timeout,
                cid,
            )))
        results = await asyncio.gather(*(job for _, job in jobs))
        for (idx, _), found in zip(jobs, results, strict=True):
            for j, glossary in found.items():
                filled[idx[j]] = glossary
    else:
        filled = await _lookup(
            getattr(retriever, "text_index", None),
            [s.chunk for s in scored],
            _scope_of(filters),
            timeout,
            getattr(retriever, "collection", None) or "-",
        )
    if not filled:
        return scored
    return [
        s.model_copy(update={"chunk": _with_glossary(s.chunk, filled[i])}) if i in filled else s
        for i, s in enumerate(scored)
    ]
