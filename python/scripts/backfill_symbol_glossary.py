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
* an LLM glossary in the index is never replaced by rules output unless
  ``--replace-llm``. ``--apply`` is refused, before any write, when the run
  would replace one (``chunks_llm_replaced`` > 0): with no ``--llm-cache`` at
  all (checked with one count query), or with a cache that misses documents
  (a full read-only planning pass runs first whenever the index holds
  ``llm/…`` chunks). ``--llm-cache`` must name an existing, non-empty file, so
  a typo cannot open as an empty cache. Check ``docs_llm`` /
  ``chunks_llm_replaced`` in the dry run;
* ``--refresh-off`` parks ``refresh_interval`` for the run and restores it after
  (``ElasticsearchTextIndex.bulk_load_refresh`` / ``restore_refresh``).

**Re-attach from a glossary file** (``--glossary-file``, #847 G1): instead of
building glossaries, render each chunk from a ``glossary.jsonl``
(:mod:`ragstack.ingestion.glossary_file`) with the file's own config, stamped
``<glossary_version>@file/1`` — an Elasticsearch-only update, no re-embed. A
read-only pass over the whole index runs first, in a dry run too: every
document is reassembled from its chunks and its ``content_key`` checked against
the row's ``text_sha256``; a mismatch, a document with no row, or (unless
``--glossary-allow-unverifiable``) a document whose chunks do not reassemble its
full text refuses the run before any write, the mapping included. The same pass
counts the LLM glossaries the file would replace by rules, and ``--apply`` is
refused on any unless ``--replace-llm``. It does not combine with
``--llm-cache`` or ``--config``.

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

from ragstack.ingestion.glossary_file import (
    GlossaryFile,
    Resolution,
    read_glossary_file,
    resolve_documents,
)
from ragstack.ingestion.reassembly import Reassembled, iter_documents_sorted
from ragstack.ingestion.symbol_glossary import (
    VERSION_KEY,
    Glossary,
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
    attacher: SymbolGlossaryAttacher, text: str, rows: list[dict[str, Any]],
    glossary: Glossary | None = None,
) -> tuple[list[tuple[str, str, str]], Counter[str]]:
    """``[(es _id, rendered field, version)]`` for the chunks that need a write,
    and counts. Pure — the testable core. ``glossary`` (#847 G1, from a
    glossary file, already verified and stamped) replaces building one."""
    counts: Counter[str] = Counter()
    if glossary is None:
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


REPLACE_GUARD_MESSAGE = (
    "refusing --apply: this run would replace the LLM glossary of {n} chunk(s) in {index!r} "
    "with rules output (chunks_llm_replaced), because --llm-cache {cache!r} has no record for "
    "their documents' exact text (a partial or a different corpus's cache). Nothing was "
    "written. Pass the cache the index was backfilled with, or --replace-llm to replace them "
    "on purpose."
)

FILE_REPLACE_GUARD_MESSAGE = (
    "refusing --apply: this run would replace the LLM glossary of {n} chunk(s) in {index!r} "
    "with the rules-only glossaries --glossary-file {file!r} holds for their documents. "
    "Nothing was written. Pass --replace-llm to replace them on purpose."
)

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


def _file_glossary(gfile: GlossaryFile, doc: Reassembled, allow_unverifiable: bool) -> Glossary:
    """The stamped glossary for one reassembled document, or SystemExit."""
    res = resolve_documents(gfile, [doc], allow_unverifiable=allow_unverifiable)
    if res.problems:
        raise SystemExit(res.refusal(repr(str(gfile.path))))
    return res.glossaries[doc.doc_id]


async def _verify_glossary_file(
    es: Any, args: argparse.Namespace, gfile: GlossaryFile, attacher: SymbolGlossaryAttacher
) -> tuple[int, int]:
    """#847 G1: a full read-only pass over the index BEFORE any write (the
    mapping included): every document reassembled from its chunks and checked
    against the file — a ``text_sha256`` mismatch, a document with no row, or
    (without the override) an unverifiable reassembly refuses the run. Returns
    how many llm/-stamped chunks the file's glossaries would replace by rules,
    and how many documents are rendered unverified (the override)."""
    res = Resolution()
    replaced = 0
    async for doc, rows in iter_documents_sorted(es, args.index):
        one = resolve_documents(gfile, [doc], allow_unverifiable=args.glossary_allow_unverifiable)
        res.problems += one.problems
        res.unverified += one.unverified
        if not one.problems:
            replaced += plan_updates(attacher, doc.text, rows,
                                     one.glossaries[doc.doc_id])[1]["chunks_llm_replaced"]
    if res.problems:
        raise SystemExit(res.refusal(repr(args.glossary_file)))
    return replaced, len(res.unverified)


async def run(args: argparse.Namespace, es: Any = None, store: Any = None) -> dict[str, Any]:
    owned = es is None
    if es is None:
        from elasticsearch import AsyncElasticsearch

        es = AsyncElasticsearch(hosts=args.es_url, request_timeout=120,
                                api_key=os.environ.get("ELASTICSEARCH_API_KEY") or None)
    glossary_file = getattr(args, "glossary_file", None)
    gfile = read_glossary_file(glossary_file) if glossary_file else None
    cache = SymbolGlossaryCache(args.llm_cache) if args.llm_cache else None
    # With a glossary file the file's own config renders every chunk.
    attacher = SymbolGlossaryAttacher(
        gfile.config if gfile is not None else config_from_json(args.config), cache)
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
        if gfile is not None:
            # Dry run and --apply alike: a file that does not match the index
            # is refused before anything else happens.
            replaced, unverified = await _verify_glossary_file(es, args, gfile, attacher)
            if unverified:
                totals["docs_glossary_unverified"] = unverified
            if replaced and args.apply and not getattr(args, "replace_llm", False):
                raise SystemExit(FILE_REPLACE_GUARD_MESSAGE.format(
                    n=replaced, index=args.index, file=glossary_file))
        elif args.apply and not args.llm_cache and not getattr(args, "replace_llm", False):
            # Without a cache every llm/-stamped chunk would be re-planned
            # (rules/… != llm/…), so "the index has any" == "this run would
            # overwrite some". Checked before the first write.
            n = await count_llm_stamped(es, args.index)
            if n:
                raise SystemExit(LLM_GUARD_MESSAGE.format(n=n, index=args.index, key=VERSION_KEY))
        elif args.apply and not getattr(args, "replace_llm", False):
            # A cache was given, but it may miss documents (partial, or another
            # corpus's): plan everything read-only first, write nothing if any
            # LLM glossary would be replaced. Skipped when the index has no
            # llm/-stamped chunk, since then nothing can be replaced.
            if await count_llm_stamped(es, args.index):
                replaced = 0
                async for doc, rows in iter_documents_sorted(es, args.index):
                    replaced += plan_updates(attacher, doc.text, rows)[1]["chunks_llm_replaced"]
                if replaced:
                    raise SystemExit(REPLACE_GUARD_MESSAGE.format(
                        n=replaced, index=args.index, cache=args.llm_cache))
        if args.apply:
            await es.indices.put_mapping(index=args.index, body=_MAPPING)
            if args.refresh_off and store is not None:
                prior_refresh = await store.bulk_load_refresh(True)
        async for doc, rows in iter_documents_sorted(es, args.index):
            totals["docs"] += 1
            totals["reassembly_gaps"] += doc.gaps
            totals["reassembly_length_mismatches"] += doc.length_mismatches
            file_glossary = (_file_glossary(gfile, doc, args.glossary_allow_unverifiable)
                             if gfile is not None else None)
            updates, counts = plan_updates(attacher, doc.text, rows, file_glossary)
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


def existing_cache_file(path: str) -> str:
    """argparse type for ``--llm-cache``: an existing, non-empty file. A typo
    would otherwise open as an EMPTY cache, every LLM-stamped document would
    fall back to rules, and the run would replace their glossaries."""
    if not os.path.isfile(path):
        raise argparse.ArgumentTypeError(f"--llm-cache {path!r}: no such file")
    if os.path.getsize(path) == 0:
        raise argparse.ArgumentTypeError(f"--llm-cache {path!r}: the file is empty")
    return path


def existing_glossary_file(path: str) -> str:
    """argparse type for ``--glossary-file``: an existing, non-empty file."""
    if not os.path.isfile(path):
        raise argparse.ArgumentTypeError(f"--glossary-file {path!r}: no such file")
    if os.path.getsize(path) == 0:
        raise argparse.ArgumentTypeError(f"--glossary-file {path!r}: the file is empty")
    return path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--es-url", required=True, help="no default, deliberately")
    p.add_argument("--index", required=True, help="the one index to backfill")
    # default=None, not "": argparse runs ``type`` on a string default, so ""
    # would reject every run that omits the flag.
    p.add_argument("--llm-cache", default=None, type=existing_cache_file,
                   help="offline LLM glossary cache, read-only; must exist and be non-empty")
    p.add_argument("--glossary-file", default=None, type=existing_glossary_file,
                   help="#847: re-attach from this glossary.jsonl (scripts/glossary_file.py) "
                        "instead of building glossaries: each document is reassembled from "
                        "its chunks and its text_sha256 checked against the file in a "
                        "read-only pass first; a mismatch or a document with no row refuses "
                        "the run before any write. Renders with the file's own config")
    p.add_argument("--glossary-allow-unverifiable", action="store_true",
                   help="with --glossary-file: render documents whose chunks do not "
                        "reassemble their full text (a gap or a length mismatch)")
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
    if args.glossary_file and (args.llm_cache or args.config):
        p.error("--glossary-file renders from the file and its own config: it does not "
                "combine with --llm-cache or --config")
    if args.glossary_allow_unverifiable and not args.glossary_file:
        p.error("--glossary-allow-unverifiable needs --glossary-file")
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
