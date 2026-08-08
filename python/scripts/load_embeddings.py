#!/usr/bin/env python
"""**Load** stage for the decoupled bulk pipeline (ADR-0001 offline plane, #141).

Reads one or more JSONL **embedding files** (produced by ``embed_shard.py``) and
upserts them into Qdrant/ES by reusing ``IngestionPipeline.index_chunks`` — the
same delete-prior → upsert → index logic as the coupled pipeline, no fork. The
embed work is already done, so this stage needs **only** the stores (no embedding
fleet). The collection's vector dim is taken from the embedding file **header**
(no embedder probe), and mismatched-dim files are rejected before any write.

**Backpressure follow-up:** #141's must-have — throttling upserts on Qdrant's
live health — will land as a ``BackpressuredVectorStore`` decorator wrapping the
pipeline's ``vector_store``; ``index_chunks`` is unchanged when it arrives, so is
this tool. Until then it loads at full rate (safe on an uncapped Qdrant).

Idempotent: deterministic ids + upsert-only + per-doc delete-prior, so re-running
(engine retry / resume) overwrites in place.

Usage::

    python scripts/load_embeddings.py shard.s0.emb.jsonl shard.s1.emb.jsonl \
        --collection ragstack_sfr_tok256 --es-index ragstack_sfr_tok256 \
        --qdrant-url http://localhost:6333 --es-url http://localhost:9200 \
        --out load-summary.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from ragstack.ingestion.chunkers import RecursiveCharacterChunker
from ragstack.ingestion.embedding_file import read_header
from ragstack.ingestion.load_embeddings import run_load_file
from ragstack.ingestion.loaders import JsonlLoader
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.ingestion.receipts import merge_summary
from ragstack.ops import ingest_target
from ragstack.stores.backpressure import BackpressuredVectorStore
from ragstack.stores.elasticsearch import ElasticsearchTextIndex
from ragstack.stores.memory import InMemoryTextIndex, InMemoryVectorStore
from ragstack.stores.qdrant import QdrantVectorStore


# Placeholder embedder/chunker: the load stage never embeds or chunks. A stub
# with no dependencies keeps the pipeline constructor happy without importing the
# embedding stack.
class _NoEmbed:
    async def embed(self, texts):  # pragma: no cover - never called
        raise RuntimeError("load stage does not embed")


async def _build_pipeline(args, target=None) -> IngestionPipeline:
    if (args.vector_backend == "qdrant") != (args.text_backend == "elasticsearch"):
        raise SystemExit(
            "vector-backend and text-backend must be consistent (both durable or "
            f"both in-memory); got vector={args.vector_backend} text={args.text_backend}"
        )
    if args.backpressure and args.backpressure_poll <= 0:
        # A zero/negative poll would busy-loop get_collection at full request rate
        # while the collection is not green — throttle, don't hammer.
        raise SystemExit("--backpressure-poll must be > 0")
    if args.vector_backend == "memory":
        vstore = InMemoryVectorStore()
        tindex = InMemoryTextIndex()
    else:
        if target is None:  # pragma: no cover — main() resolves before calling
            raise SystemExit("internal: no ingest target resolved")
        # Dim comes from the embedding files themselves (their header), and every
        # file must agree — a wrong-dim file (e.g. 768-d BGE into a 4096-d SFR
        # collection) is caught here, before the collection is created/written.
        dims = set()
        for f in args.embeddings:
            d = read_header(f).get("dim")
            if not d:
                raise SystemExit(f"{f}: embedding file header missing/zero 'dim'")
            dims.add(int(d))
        if len(dims) > 1:
            raise SystemExit(f"embedding files disagree on dim: {sorted(dims)}")
        dim = next(iter(dims))
        # The files' dim must also match the registry entry, or this load builds a
        # store whose vectors are not the ones the entry promises (#263/ADR-0002).
        target.check_build(dim=dim)
        # Both names come from the registry entry, never from the command line.
        # A contradicting --es-index was refused during resolution.
        es_index = target.es_index
        vstore = QdrantVectorStore(url=target.qdrant_url, collection=target.collection,
                                   vector_size=dim, timeout=args.qdrant_timeout,
                                   upsert_batch_size=args.upsert_batch_size,
                                   upsert_concurrency=args.upsert_concurrency)
        await vstore.ensure_collection()
        if args.backpressure:
            # #141: hold each upsert until the collection is green, so a bulk load
            # never piles unindexed vectors onto a Qdrant that is optimizing (the
            # VMA-exhaustion driver). Transparent to index_chunks/the pipeline.
            vstore = BackpressuredVectorStore(
                vstore, poll_interval=args.backpressure_poll,
                max_wait=args.backpressure_max_wait,
            )
        tindex = ElasticsearchTextIndex(url=args.es_url, index=es_index)
        await tindex.ensure_index()
    return IngestionPipeline(loader=JsonlLoader(), chunker=RecursiveCharacterChunker(),
                             embedder=_NoEmbed(), vector_store=vstore, text_index=tindex)


async def amain(args, target=None) -> int:
    pipeline = await _build_pipeline(args, target)
    receipts = []
    for path in args.embeddings:
        r = await run_load_file(pipeline, path, file_id=path, tenant=args.tenant)
        receipts.append(r)
        print(f"[{path}] status={r.status} chunks={r.n_chunks}"
              + (f"  ERROR: {r.error}" if r.error else ""), flush=True)
    summary = merge_summary(receipts)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, sort_keys=True)
    print(f"loaded {summary['n_chunks']} chunk(s) from {summary['n_shards']} file(s); "
          f"failed={summary['n_shards_failed']} → {args.out}", flush=True)
    # Arm ADR-0002's build-spec guard for this store. Without a manifest,
    # check_ingest_build_spec early-returns forever and a later API ingest with a
    # different chunker interleaves silently — the failure #263 is about.
    manifest_dir = args.manifest_dir or os.getenv("COLLECTION_MANIFEST_DIR", "")
    if target is not None and manifest_dir and not summary["n_shards_failed"]:
        spec_hash = target.write_manifest(
            manifest_dir,
            corpus=f"{summary['n_shards']} embedding file(s)",
            chunk_count=summary["n_chunks"],
        )
        print(f"wrote provenance manifest ({spec_hash}) to {manifest_dir}", flush=True)
    if args.fail_on_error and summary["n_shards_failed"]:
        return 1
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("embeddings", nargs="+", help="one or more JSONL embedding files")
    p.add_argument("--out", default="load-summary.json", help="output summary path")
    p.add_argument("--tenant", default=None,
                   help="override tenant (default: each file's header tenant)")
    p.add_argument("--fail-on-error", action="store_true",
                   help="exit non-zero if any file failed to load")
    p.add_argument("--backpressure", action="store_true",
                   help="hold each upsert until the Qdrant collection is green (#141). "
                        "OFF by default: the capped-Qdrant A/B benchmark showed it adds "
                        "latency without preventing drops below crash-scale (millions of "
                        "vectors + deferred indexing). Enable for a very large corpus on a "
                        "capped Qdrant")
    p.add_argument("--backpressure-poll", type=float, default=2.0,
                   help="seconds between health polls while holding (default 2.0)")
    p.add_argument("--backpressure-max-wait", type=float, default=None,
                   help="give up (error) if not green after this many seconds; "
                        "default None = wait indefinitely")
    p.add_argument("--vector-backend", choices=["qdrant", "memory"], default="qdrant")
    p.add_argument("--collection", default=None,
                   help="DEPRECATED — the PHYSICAL store name. Accepted only when "
                        "a registry entry already claims it; prefer --collection-id")
    p.add_argument("--qdrant-url", default="http://localhost:6333")
    p.add_argument("--qdrant-timeout", type=int, default=120)
    p.add_argument("--upsert-batch-size", type=int, default=256,
                   help="points per Qdrant upsert request (bounds payload size)")
    p.add_argument("--upsert-concurrency", type=int, default=4,
                   help="concurrent upsert batches (pipelines the load; default 4). "
                        "1 = serial, safest under a capped/optimizing collection")
    p.add_argument("--text-backend", choices=["elasticsearch", "memory"], default="elasticsearch")
    p.add_argument("--es-url", default="http://localhost:9200")
    p.add_argument("--es-index", default=None)
    p.add_argument("--manifest-dir", default="",
                   help="write a provenance manifest here (defaults to "
                        "$COLLECTION_MANIFEST_DIR). Arms ADR-0002's build-spec "
                        "guard for this store; skipped if neither is set")
    ingest_target.add_arguments(p)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    # Resolve the registry entry before any store is created or written (#263).
    # The in-memory backend is the dev/test path and owns no physical store, so
    # it has nothing to register.
    target = (
        ingest_target.resolve_or_exit(args)
        if args.vector_backend == "qdrant"
        else None
    )
    return asyncio.run(amain(args, target))


if __name__ == "__main__":
    sys.exit(main())
