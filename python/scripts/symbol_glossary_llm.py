#!/usr/bin/env python
"""Offline, cached, document-level LLM symbol glossary (#754 fix A).

Reads documents, asks an OpenAI-compatible LLM for each document's symbol
glossary, validates every proposed entry against the document's own text (the
symbol must occur in it; every word of the definition must occur in it), and
appends the result to a content-addressed cache that ingest and the backfill
read. **Writes only the cache file.** Never touches a store, never runs inside
ingest or a worker.

Documents come from either

* ``--jsonl FILE`` — the same pre-extracted JSONL the ingest tools load
  (``JsonlLoader``), so the cache key matches what ingest computes from
  ``Document.content``; or
* ``--es-url URL --index NAME`` — an existing index, READ-ONLY (PIT +
  ``search_after``), each document reassembled from its chunks by
  ``start_char`` (what the backfill reads, so its key matches the backfill's).

The cache key is ``sha256(document text)`` and the record's version is
``llm/1:<model>:<prompt hash>``; a document already cached at this version is
skipped, so a re-run is free and resumable.

The LLM endpoint and model default to ``LLM_ENDPOINT`` / ``LLM_MODEL`` from the
environment (e.g. ``set -a; . /rag/data/tenants/dev/config/tenant.env``). A key,
when the endpoint needs one, is read from ``LLM_API_KEY`` (or
``OPENAI_API_KEY``) and is never printed or written anywhere.

Usage::

    python scripts/symbol_glossary_llm.py --es-url http://localhost:24043 \\
        --index ragstack_lib_openchia_..._7033902e --cache glossary_llm.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx

from ragstack.ingestion.loaders import JsonlLoader
from ragstack.ingestion.symbol_glossary import (
    SymbolGlossaryCache,
    config_from_json,
    content_key,
)
from ragstack.ingestion.symbol_glossary_llm import extract_with_llm, llm_version
from ragstack.llm import OpenAILLM

log = logging.getLogger("symbol_glossary_llm")


async def _documents(args: argparse.Namespace) -> AsyncIterator[tuple[str, str]]:
    """``(doc_id, text)`` from the chosen source."""
    if args.jsonl:
        for doc in JsonlLoader().load(args.jsonl):
            yield doc.id, doc.content
        return
    from elasticsearch import AsyncElasticsearch

    from ragstack.ingestion.reassembly import documents_from_index

    es = AsyncElasticsearch(hosts=args.es_url, api_key=os.environ.get("ELASTICSEARCH_API_KEY") or None,
                            request_timeout=120)
    try:
        async for doc, _rows in documents_from_index(es, args.index):
            if doc.gaps or doc.length_mismatches:
                log.warning("%s: reassembled with %d gap(s), %d length mismatch(es)",
                            doc.doc_id, doc.gaps, doc.length_mismatches)
            yield doc.doc_id, doc.text
    finally:
        await es.close()


async def run(args: argparse.Namespace) -> dict[str, int]:
    endpoint = args.llm_endpoint or os.environ.get("LLM_ENDPOINT", "")
    model = args.llm_model or os.environ.get("LLM_MODEL", "")
    if not endpoint or not model:
        raise SystemExit("set --llm-endpoint/--llm-model or LLM_ENDPOINT/LLM_MODEL")
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or None
    version = llm_version(model)
    config = config_from_json(args.config)
    cache = SymbolGlossaryCache(args.cache, writable=True)
    counts = {"documents": 0, "cached": 0, "extracted": 0, "failed": 0, "entries": 0,
              "proposed": 0, "rejected": 0, "truncated": 0, "cut_off": 0}
    sem = asyncio.Semaphore(max(1, args.concurrency))
    only = set(args.doc_id or [])
    log.info("LLM glossary %s at %s -> %s", version, endpoint, args.cache)

    async with httpx.AsyncClient(timeout=args.timeout) as http:
        llm = OpenAILLM(endpoint, model, http, api_key=api_key)

        async def one(doc_id: str, text: str) -> None:
            key = content_key(text)
            async with sem:
                t0 = time.monotonic()
                for attempt in range(3):
                    try:
                        glossary, stats = await extract_with_llm(
                            llm, text, config=config, window_chars=args.window_chars,
                            max_windows=args.max_windows, max_tokens=args.max_tokens,
                        )
                        break
                    except Exception as e:  # noqa: BLE001 — one document never sinks the run
                        log.warning("%s: attempt %d failed: %s", doc_id, attempt + 1, type(e).__name__)
                        await asyncio.sleep(2 * (attempt + 1))
                else:
                    counts["failed"] += 1
                    return
            cache.put(key, glossary)
            counts["extracted"] += 1
            counts["entries"] += len(glossary.entries)
            counts["proposed"] += stats.proposed
            counts["rejected"] += stats.rejected
            counts["truncated"] += int(stats.truncated)
            counts["cut_off"] += stats.cut_off
            log.info("%s: %d entries (%d proposed, %d rejected, %d window(s)%s) in %.1fs",
                     doc_id, len(glossary.entries), stats.proposed, stats.rejected,
                     stats.windows, ", TRUNCATED" if stats.truncated else "",
                     time.monotonic() - t0)

        tasks: list[asyncio.Task[None]] = []
        async for doc_id, text in _documents(args):
            if only and doc_id not in only:
                continue
            counts["documents"] += 1
            if not text.strip():
                continue
            hit = cache.get(content_key(text), version)
            if hit is not None and (hit[1] or not args.retry_empty):
                counts["cached"] += 1
                continue
            if args.limit and len(tasks) >= args.limit:
                continue
            tasks.append(asyncio.create_task(one(doc_id, text)))
        await asyncio.gather(*tasks)
    cache.close()
    return counts


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--jsonl", help="pre-extracted documents (JsonlLoader format)")
    src.add_argument("--index", help="read documents from this ES index (read-only)")
    p.add_argument("--es-url", default="", help="Elasticsearch URL (required with --index; no default)")
    p.add_argument("--cache", required=True, type=Path, help="glossary cache JSONL (appended)")
    p.add_argument("--llm-endpoint", default="", help="default: $LLM_ENDPOINT")
    p.add_argument("--llm-model", default="", help="default: $LLM_MODEL")
    p.add_argument("--config", default="", help="GlossaryConfig overrides as JSON")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--window-chars", type=int, default=80_000)
    p.add_argument("--max-windows", type=int, default=4)
    p.add_argument("--max-tokens", type=int, default=2_048)
    p.add_argument("--timeout", type=float, default=300.0)
    p.add_argument("--limit", type=int, default=0, help="extract at most N uncached documents")
    p.add_argument("--doc-id", action="append", help="only these doc ids (repeatable)")
    p.add_argument("--retry-empty", action="store_true",
                   help="re-extract documents cached with an EMPTY glossary (the last record wins)")
    args = p.parse_args(argv)
    if args.index and not args.es_url:
        p.error("--index needs --es-url (there is deliberately no default URL)")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    counts = asyncio.run(run(args))
    print(json.dumps(counts, sort_keys=True))
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
