#!/usr/bin/env python
"""``load-graph`` step (#350): load an archived graph leg into the graph store,
scoped by ``(tenant, collection)``, within the collection's triple budget.

Input: the extract step's delta directory (``manifest.json`` +
``triples.jsonl.gz``; a full version directory works too). The manifest and
the triples file's sha256 are verified first; the registry entry named by
``--collection-id`` supplies the PHYSICAL collection name every triple is
stamped with (never the command line — the worker must see the same registry
the API does, as for ``load_embeddings.py``); then ONE live count of that
collection's triples decides the budget: ``live + incoming > --max-triples``
refuses the whole load with exit 4 and nothing written. Otherwise the triples
are upserted in batches (idempotent — both stores MERGE on the triple's key).

Graph store: ``--graph-backend neo4j`` (the default) connects with
``--neo4j-uri`` (default: the ``NEO4J_URI`` setting) and the ``NEO4J_USER`` /
``NEO4J_PASSWORD`` settings from the task's environment — credentials are
never workflow inputs. ``memory`` is the process-local dev/test store; with it
the collection name may be given directly (``--stamp-collection``) since there
is nothing durable to protect.

Exit codes: 0 loaded; 3 the leg was refused (``ArchiveCorrupt:``, permanent);
4 the budget (``graph_cap_exceeded: live=L incoming=I cap=C would_fit=W``);
2 the registry could not resolve the collection; 1 a store failure mid-load.

Usage::

    python scripts/load_graph.py --version-dir 3 --collection-id lib \
        --max-triples 200000 --neo4j-uri bolt://neo4j:7687 --out graph-load-summary.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from ragstack.graph.archive_load import LOAD_BATCH, load_triples
from ragstack.graph.budget import GRAPH_CAP_REFUSED_EXIT_CODE, GraphCapExceeded
from ragstack.ingestion.archive import ArchiveCorrupt, ArchiveError, verify_triples
from ragstack.ops import ingest_target

REFUSED_EXIT_CODE = 3


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version-dir", required=True, metavar="DIR",
                   help="the version (or delta) directory holding manifest.json + triples.jsonl.gz")
    p.add_argument("--max-triples", type=int, default=0,
                   help="the collection's triple cap (0 = unlimited): refuse the whole load "
                        "when live + incoming would exceed it")
    p.add_argument("--graph-backend", choices=["neo4j", "memory"], default=None,
                   help="graph store (default: the GRAPH_BACKEND setting, else neo4j)")
    p.add_argument("--neo4j-uri", default="",
                   help="bolt URI (default: the NEO4J_URI setting). User/password come from "
                        "the NEO4J_USER / NEO4J_PASSWORD settings, never the command line")
    p.add_argument("--stamp-collection", default="",
                   help="memory backend only: the collection name to stamp (no registry)")
    p.add_argument("--batch", type=int, default=LOAD_BATCH,
                   help=f"triples per add_triples call (default {LOAD_BATCH})")
    p.add_argument("--out", default="graph-load-summary.json", help="summary path")
    ingest_target.add_arguments(p)
    return p.parse_args(argv)


def _build_store(args):
    from ragstack.config import settings

    backend = args.graph_backend or (settings.graph_backend if settings.graph_backend in
                                     ("neo4j", "memory") else "neo4j")
    if backend == "memory":
        from ragstack.stores.memory import InMemoryGraphStore

        return InMemoryGraphStore()
    from ragstack.stores.neo4j import Neo4jGraphStore  # lazy: the driver is optional

    return Neo4jGraphStore(
        uri=args.neo4j_uri or settings.neo4j_uri, user=settings.neo4j_user,
        password=settings.neo4j_password, database=settings.neo4j_database or None,
    )


def _verify(args) -> dict | None:
    """Verify the leg + its identity BEFORE resolving anything; ``None`` (after
    the refusal line on stderr) means exit 3."""
    try:
        manifest = verify_triples(args.version_dir)
    except ArchiveCorrupt as e:
        print(str(e) if str(e).startswith("ArchiveCorrupt") else f"ArchiveCorrupt: {e}",
              file=sys.stderr, flush=True)
        return None
    except ArchiveError as e:
        print(f"ArchiveCorrupt: {args.version_dir}: {e}", file=sys.stderr, flush=True)
        return None
    if args.collection_id and str(manifest.get("collection_id") or "") != args.collection_id:
        print(f"SpecMismatch: {args.version_dir}: manifest collection_id "
              f"{manifest.get('collection_id')!r} != {args.collection_id!r}",
              file=sys.stderr, flush=True)
        return None
    return manifest


def _resolve_collection(args) -> str:
    """The physical collection name to stamp — from the registry entry (the
    loader's rule), or, for the memory backend only, ``--stamp-collection``.
    Synchronous, called BEFORE the event loop starts: the registry reader runs
    its own loop (as ``load_embeddings.py`` resolves in ``main()``)."""
    if args.graph_backend == "memory" and args.stamp_collection:
        return args.stamp_collection
    return ingest_target.resolve_or_exit(args).collection  # exit 2 with a readable message


async def amain(args, manifest: dict, collection: str) -> int:
    store = _build_store(args)
    try:
        if hasattr(store, "ensure_schema"):
            await store.ensure_schema()
        summary = await load_triples(
            args.version_dir, store, collection=collection,
            cap=args.max_triples if args.max_triples > 0 else None, batch_size=args.batch,
            manifest=manifest, log=lambda msg: print(msg, flush=True),
        )
    except GraphCapExceeded as e:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({"mode": "graph", "status": "refused", "graph_cap": e.detail(),
                       "collection": collection}, fh, indent=2, sort_keys=True)
        print(str(e), file=sys.stderr, flush=True)
        print(f"refused: nothing loaded → {args.out}", flush=True)
        return GRAPH_CAP_REFUSED_EXIT_CODE
    except ArchiveError as e:
        print(f"load-graph: {e}", file=sys.stderr, flush=True)
        return 1
    finally:
        if hasattr(store, "close"):
            await store.close()
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({**summary.as_dict(), "status": "completed"}, fh, indent=2, sort_keys=True)
    print(f"loaded {summary.n_triples} triple(s) into {collection!r} "
          f"(live before: {summary.live_before}, cap: {summary.cap}) → {args.out}", flush=True)
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.batch < 1:
        raise SystemExit("--batch must be >= 1")
    manifest = _verify(args)
    if manifest is None:
        return REFUSED_EXIT_CODE
    collection = _resolve_collection(args)
    return asyncio.run(amain(args, manifest, collection))


if __name__ == "__main__":
    sys.exit(main())
