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
import sys

from ragstack.ingestion.chunkers import RecursiveCharacterChunker
from ragstack.ingestion.embedding_file import read_header
from ragstack.ingestion.load_embeddings import run_load_file
from ragstack.ingestion.loaders import JsonlLoader
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.ingestion.receipts import merge_summary
from ragstack.stores.elasticsearch import ElasticsearchTextIndex
from ragstack.stores.memory import InMemoryTextIndex, InMemoryVectorStore
from ragstack.stores.qdrant import QdrantVectorStore


# Placeholder embedder/chunker: the load stage never embeds or chunks. A stub
# with no dependencies keeps the pipeline constructor happy without importing the
# embedding stack.
class _NoEmbed:
    async def embed(self, texts):  # pragma: no cover - never called
        raise RuntimeError("load stage does not embed")


async def _build_pipeline(args) -> IngestionPipeline:
    if (args.vector_backend == "qdrant") != (args.text_backend == "elasticsearch"):
        raise SystemExit(
            "vector-backend and text-backend must be consistent (both durable or "
            f"both in-memory); got vector={args.vector_backend} text={args.text_backend}"
        )
    if args.vector_backend == "memory":
        vstore = InMemoryVectorStore()
        tindex = InMemoryTextIndex()
    else:
        if not args.collection:
            raise SystemExit("--collection is required for --vector-backend qdrant")
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
        es_index = args.es_index or args.collection
        vstore = QdrantVectorStore(url=args.qdrant_url, collection=args.collection,
                                   vector_size=dim, timeout=args.qdrant_timeout)
        await vstore.ensure_collection()
        tindex = ElasticsearchTextIndex(url=args.es_url, index=es_index)
        await tindex.ensure_index()
    return IngestionPipeline(loader=JsonlLoader(), chunker=RecursiveCharacterChunker(),
                             embedder=_NoEmbed(), vector_store=vstore, text_index=tindex)


async def amain(args) -> int:
    pipeline = await _build_pipeline(args)
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
    p.add_argument("--vector-backend", choices=["qdrant", "memory"], default="qdrant")
    p.add_argument("--collection", default=None)
    p.add_argument("--qdrant-url", default="http://localhost:6333")
    p.add_argument("--qdrant-timeout", type=int, default=120)
    p.add_argument("--text-backend", choices=["elasticsearch", "memory"], default="elasticsearch")
    p.add_argument("--es-url", default="http://localhost:9200")
    p.add_argument("--es-index", default=None)
    return p.parse_args(argv)


def main(argv=None) -> int:
    return asyncio.run(amain(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
