#!/usr/bin/env python
"""Backfill the per-document symbol glossary (#754 fix A) onto an EXISTING
Elasticsearch index — DRY RUN BY DEFAULT.

Reads the index (PIT + ``search_after``, sorted by ``doc_id`` so one document is
held at a time), reassembles each document from its chunks by ``start_char``,
builds its glossary exactly as ingest would (rules, preferring an offline LLM
glossary from ``--llm-cache`` when it holds the document's text), renders each
chunk's ``symbol_glossary`` field, and — only with ``--apply`` — writes it with
``_bulk`` partial ``update`` ops (``symbol_glossary`` +
``metadata.symbol_glossary_version``). Nothing else changes: ``content``,
offsets and every other metadata key are untouched, and **no vector is
touched** — Qdrant is never opened, nothing is re-embedded.

Safety:

* dry run unless ``--apply``; a dry run issues no write of any kind and prints
  what it would change;
* ``--apply`` also needs ``--confirm-index`` repeating the exact index name;
* there is no default ES URL and no default index;
* resumable: a chunk whose stored marker and field already equal what would be
  written is skipped, so a re-run after an interruption only does the rest;
* ``--apply`` without ``--llm-cache`` is refused while the index holds
  ``llm/…``-stamped chunks, unless ``--replace-llm``: without the cache every
  such chunk is re-planned from the rules, and its LLM glossary would be
  OVERWRITTEN. Check ``docs_llm`` / ``chunks_llm_replaced`` in the dry run;
* ``--refresh-off`` parks ``refresh_interval`` for the run and restores it after
  (``ElasticsearchTextIndex.bulk_load_refresh`` / ``restore_refresh``).

Sizing (plan §6): openchia, 16.5k chunks / 289 docs, is seconds. The 47.6M-chunk
open-access index is ~95 GB scrolled and 47.6M partial updates (each a Lucene
delete + reinsert): hours, ≥2× the index size in transient disk, and segment
churn on the order of a rewrite. Do not run it there without a plan — the owner
intends to re-ingest open-access under a new chunking policy instead.

Usage::

    python scripts/backfill_symbol_glossary.py --es-url http://localhost:24043 \\
        --index ragstack_lib_openchia_..._7033902e [--llm-cache glossary.jsonl]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from typing import Any

from ragstack.ingestion.reassembly import iter_documents_sorted
from ragstack.ingestion.symbol_glossary import (
    VERSION_KEY,
    SymbolGlossaryAttacher,
    SymbolGlossaryCache,
    config_from_json,
)
from ragstack.stores.elasticsearch import _MAPPINGS as _STORE_MAPPINGS

# The field's mapping is the store's own, so a backfilled index and a freshly
# created one cannot disagree (e.g. on ``norms``).
_MAPPING = {
    "properties": {
        "symbol_glossary": dict(_STORE_MAPPINGS["properties"]["symbol_glossary"]),
        "metadata": {"properties": {VERSION_KEY: {"type": "keyword", "ignore_above": 8191}}},
    }
}


def check_index_name(name: str) -> str | None:
    """Why ``name`` is not ONE concrete index name, or ``None`` if it is.
    Wildcards, lists, ``_all`` and exclusions would turn one backfill into a
    write across many indices."""
    if not name or not name.strip():
        return "empty index name"
    if any(c in name for c in "*?,"):
        return f"{name!r} is a pattern or a list, not one index"
    if name in ("_all", "*") or name.startswith(("-", "_")):
        return f"{name!r} is not a concrete index name"
    return None


async def resolve_exactly(es: Any, name: str) -> None:
    """Refuse unless the cluster resolves ``name`` to exactly that one index —
    an alias (which may point at several, or at another name) is refused too."""
    got = await es.indices.get(index=name)
    keys = set(getattr(got, "body", got).keys())
    if keys != {name}:
        raise SystemExit(f"refusing: {name!r} resolves to {sorted(keys)}, not exactly itself")


def plan_updates(
    attacher: SymbolGlossaryAttacher, text: str, rows: list[dict[str, Any]]
) -> tuple[list[tuple[str, str, str]], Counter[str]]:
    """``[(es _id, rendered field, version)]`` for the chunks that need a write,
    and counts. Pure — the testable core."""
    counts: Counter[str] = Counter()
    glossary = attacher.glossary_for(text)
    out: list[tuple[str, str, str]] = []
    for row in rows:
        counts["chunks"] += 1
        rendered = glossary.for_chunk(
            str(row.get("content") or ""), attacher.config, start_char=int(row.get("start_char") or 0)
        )
        if rendered:
            counts["chunks_with_glossary"] += 1
        stored_version = (row.get("metadata") or {}).get(VERSION_KEY)
        stored_field = row.get("symbol_glossary") or ""
        if stored_version == glossary.version and stored_field == rendered:
            counts["chunks_current"] += 1
            continue
        if str(stored_version or "").startswith("llm/") and not glossary.version.startswith("llm/"):
            # an LLM glossary in the index that this run would replace by rules
            counts["chunks_llm_replaced"] += 1
        out.append((str(row["_id"]), rendered, glossary.version))
    counts[f"docs_{'llm' if glossary.version.startswith('llm/') else 'rules'}"] += 1
    return out, counts


LLM_GUARD_MESSAGE = (
    "refusing --apply: {n} chunk(s) in {index!r} carry an LLM glossary (metadata.{key} = llm/…), "
    "and no --llm-cache was given. Without the cache those documents are re-planned from the "
    "rules and their LLM glossaries are OVERWRITTEN with rules output. Pass the cache the index "
    "was backfilled with (--llm-cache <file>), or --replace-llm to replace them on purpose. "
    "A dry run reports docs_llm and chunks_llm_replaced."
)


async def count_llm_stamped(es: Any, index: str) -> int:
    """How many chunks in ``index`` are stamped from an LLM glossary. Read-only."""
    resp = await es.count(index=index, query={"prefix": {f"metadata.{VERSION_KEY}": "llm/"}})
    return int(getattr(resp, "body", resp).get("count", 0))


async def run(args: argparse.Namespace, es: Any = None, store: Any = None) -> dict[str, Any]:
    owned = es is None
    if es is None:
        from elasticsearch import AsyncElasticsearch

        es = AsyncElasticsearch(hosts=args.es_url, request_timeout=120,
                                api_key=os.environ.get("ELASTICSEARCH_API_KEY") or None)
    cache = SymbolGlossaryCache(args.llm_cache) if args.llm_cache else None
    attacher = SymbolGlossaryAttacher(config_from_json(args.config), cache)
    totals: Counter[str] = Counter()
    prior_refresh: str | None = None
    pending: list[dict[str, Any]] = []

    async def flush() -> None:
        if not pending:
            return
        resp = await es.bulk(operations=list(pending), refresh=False)
        if resp.get("errors"):
            first = next((next(iter(i.values())) for i in resp.get("items", [])
                          if next(iter(i.values())).get("error")), {})
            raise RuntimeError(f"bulk update failed: {first.get('_id')}: {first.get('error')}")
        totals["chunks_written"] += len(pending) // 2
        pending.clear()

    try:
        await resolve_exactly(es, args.index)
        if args.apply and not args.llm_cache and not getattr(args, "replace_llm", False):
            # Without a cache every llm/-stamped chunk would be re-planned
            # (rules/… != llm/…), so "the index has any" == "this run would
            # overwrite some". Checked before the first write.
            n = await count_llm_stamped(es, args.index)
            if n:
                raise SystemExit(LLM_GUARD_MESSAGE.format(n=n, index=args.index, key=VERSION_KEY))
        if args.apply:
            await es.indices.put_mapping(index=args.index, body=_MAPPING)
            if args.refresh_off and store is not None:
                prior_refresh = await store.bulk_load_refresh(True)
        async for doc, rows in iter_documents_sorted(es, args.index):
            totals["docs"] += 1
            totals["reassembly_gaps"] += doc.gaps
            totals["reassembly_length_mismatches"] += doc.length_mismatches
            updates, counts = plan_updates(attacher, doc.text, rows)
            totals.update(counts)
            totals["chunks_to_update"] += len(updates)
            if not args.apply:
                continue
            for es_id, rendered, version in updates:
                pending.append({"update": {"_index": args.index, "_id": es_id}})
                pending.append({"doc": {"symbol_glossary": rendered or None,
                                        "metadata": {VERSION_KEY: version}}})
                if len(pending) >= 2 * args.batch_size:
                    await flush()
        if args.apply:
            await flush()
    finally:
        if args.apply and args.refresh_off and store is not None:
            await store.restore_refresh(prior_refresh)
        if store is not None:
            await store.close()
        if owned:
            await es.close()
    result = {"index": args.index, "dry_run": not args.apply, **dict(sorted(totals.items()))}
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--es-url", required=True, help="no default, deliberately")
    p.add_argument("--index", required=True, help="the one index to backfill")
    p.add_argument("--llm-cache", default="", help="offline LLM glossary cache, read-only")
    p.add_argument("--config", default="", help="GlossaryConfig overrides as JSON")
    p.add_argument("--batch-size", type=int, default=500)
    p.add_argument("--apply", action="store_true", help="WRITE. Without it this is a dry run.")
    p.add_argument("--confirm-index", default="", help="must repeat --index when --apply")
    p.add_argument("--replace-llm", action="store_true",
                   help="with --apply and no --llm-cache: overwrite llm/-stamped glossaries with rules")
    p.add_argument("--refresh-off", action="store_true",
                   help="park refresh_interval during --apply and restore it after")
    args = p.parse_args(argv)
    bad = check_index_name(args.index)
    if bad:
        p.error(bad)
    if args.apply and args.confirm_index != args.index:
        p.error("--apply writes to the index: repeat its exact name with --confirm-index")
    store = None
    if args.apply and args.refresh_off:
        from ragstack.stores.elasticsearch import ElasticsearchTextIndex

        store = ElasticsearchTextIndex(url=args.es_url, index=args.index)
    result = asyncio.run(run(args, store=store))
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
