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
``--neo4j-uri`` — REQUIRED unless ``NEO4J_URI`` is explicitly configured (and
non-empty); never the code default ``bolt://localhost:7687``, which is the
PRODUCTION graph on the deployment host (#454) — and the ``NEO4J_USER`` /
``NEO4J_PASSWORD`` settings from the task's environment — credentials are
never workflow inputs. ``memory`` is the process-local dev/test store; with it
the collection name may be given directly (``--stamp-collection``) since there
is nothing durable to protect.

Exit codes: 0 loaded; 3 the leg was refused (``ArchiveCorrupt:``, permanent);
4 the budget (``graph_cap_exceeded: live=L incoming=I cap=C would_fit=W``);
2 no graph store URI (neo4j backend), or the registry could not resolve the
collection; 1 a store failure mid-load.

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
from ragstack.provenance import add_provenance_arguments, provenance_from_args

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
                   help="bolt URI of the graph to WRITE to. NO default (#454): required "
                        "with the neo4j backend unless NEO4J_URI is explicitly configured "
                        "(and non-empty) — the code default bolt://localhost:7687 is the "
                        "PRODUCTION graph on the deployment host. User/password come from "
                        "the NEO4J_USER / NEO4J_PASSWORD settings, never the command line")
    p.add_argument("--stamp-collection", default="",
                   help="memory backend only: the collection name to stamp (no registry)")
    p.add_argument("--batch", type=int, default=LOAD_BATCH,
                   help=f"triples per add_triples call (default {LOAD_BATCH})")
    p.add_argument("--out", default="graph-load-summary.json", help="summary path")
    ingest_target.add_arguments(p)
    add_provenance_arguments(p)
    return p.parse_args(argv)


def _backend(args, settings) -> str:
    return args.graph_backend or (settings.graph_backend if settings.graph_backend in
                                  ("neo4j", "memory") else "neo4j")


def _require_neo4j_uri(args, settings) -> None:
    """Decide the graph URI before anything is read or written (#454): the flag,
    else ``NEO4J_URI`` ONLY IF explicitly configured — never the code default,
    which is localhost and therefore the PRODUCTION graph on the deployment
    host. An empty value is not a decision. Exit 2 otherwise."""
    if _backend(args, settings) != "neo4j":
        return
    uri = (args.neo4j_uri or "").strip()
    if not uri and ingest_target.configured_explicitly(settings, "neo4j_uri"):
        uri = (getattr(settings, "neo4j_uri", "") or "").strip()
    if not uri:
        print("error: --neo4j-uri is required: NEO4J_URI is not configured (or is "
              "empty), so pass the graph to write to — there is no default, because "
              "the conventional bolt://localhost:7687 is the PRODUCTION graph on the "
              "deployment host (#454).", file=sys.stderr, flush=True)
        raise SystemExit(2)
    args.neo4j_uri = uri


def _build_store(args, settings=None):
    if settings is None:
        from ragstack.config import settings

    backend = _backend(args, settings)
    if backend == "memory":
        from ragstack.stores.memory import InMemoryGraphStore

        return InMemoryGraphStore()
    from ragstack.stores.neo4j import Neo4jGraphStore  # lazy: the driver is optional

    if not (args.neo4j_uri or "").strip():  # main() decided it; never fall back
        raise SystemExit("load-graph: no --neo4j-uri (refusing localhost, #454)")
    return Neo4jGraphStore(
        uri=args.neo4j_uri, user=settings.neo4j_user,
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
                       "collection": collection,
                       "provenance": provenance_from_args(args)},
                      fh, indent=2, sort_keys=True)
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
        # ADR-0010 decision 8, #821: which workflow and tools image loaded it.
        json.dump({**summary.as_dict(), "status": "completed",
                   "provenance": provenance_from_args(args)}, fh, indent=2, sort_keys=True)
    print(f"loaded {summary.n_triples} triple(s) into {collection!r} "
          f"(live before: {summary.live_before}, cap: {summary.cap}) → {args.out}", flush=True)
    return 0


def main(argv=None, *, settings=None) -> int:
    args = parse_args(argv)
    if args.batch < 1:
        raise SystemExit("--batch must be >= 1")
    if settings is None:
        from ragstack.config import settings
    _require_neo4j_uri(args, settings)
    manifest = _verify(args)
    if manifest is None:
        return REFUSED_EXIT_CODE
    collection = _resolve_collection(args)
    return asyncio.run(amain(args, manifest, collection))


if __name__ == "__main__":
    sys.exit(main())
