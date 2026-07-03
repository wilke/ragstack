#!/usr/bin/env python
"""Atomic per-shard ingest tool for the CWL/GoWe bulk-ingest workflow (ADR-0001
step 2). Ingests ONE shard (a JSONL file of document records) → emits a
``receipt.json`` (chunk ids + per-doc catalog). The workflow scatters this over
the shards; ``merge_receipts.py`` gathers the receipts into a run summary.

**Stateless + idempotent by design.** It reuses ``IngestionPipeline.ingest``
(which owns chunk → embed → quarantine → delete-prior → upsert → neighbor-link)
and carries **no** checkpoint / resume / in-process concurrency — the workflow
engine owns scatter/retry/resume, so this tool just does one shard and reports.
Re-running a shard overwrites in place (deterministic uuid5 ids + upsert), so a
GoWe retry is safe. This deliberately sheds the bespoke machinery in
``ingest_jsonl.py`` (#71) rather than re-implementing the pipeline (#25).

Like the eval scatter step, a real run needs the live embedding fleet + Qdrant/ES;
it is not a CI step. The ``run_shard`` core is unit-tested offline with in-memory
stores + a fake embedder.

Usage::

    python scripts/ingest_shard.py shard.s0.jsonl --tenant public \
        --collection ragstack_sfr_tok256 --es-index ragstack_sfr_tok256 \
        --embedding-api openai --embedding-model Salesforce/SFR-Embedding-Mistral \
        --embedding-url http://localhost:9001 --chunk-method fixed_token \
        --chunk-size 256 --chunk-overlap 32 --out receipt.json
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

import httpx

from ragstack.embed_pool import make_pooled_embedder
from ragstack.embedders import make_embedder
from ragstack.ingestion.chunkers import make_chunker
from ragstack.ingestion.loaders import JsonlLoader
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.ingestion.receipts import COMPLETED
from ragstack.ingestion.shard import run_shard
from ragstack.stores.elasticsearch import ElasticsearchTextIndex
from ragstack.stores.memory import InMemoryTextIndex, InMemoryVectorStore
from ragstack.stores.qdrant import QdrantVectorStore, collection_name


def _build_embedder(args, http: httpx.AsyncClient):
    common = {"api": args.embedding_api, "http": http, "model": args.embedding_model,
              "api_key": args.embedding_api_key or os.getenv("OPENAI_API_KEY")}
    if len(args.embedding_url) > 1:
        return make_pooled_embedder(base_urls=args.embedding_url,
                                    max_concurrency=args.embedding_max_concurrency, **common)
    return make_embedder(base_url=args.embedding_url[0], **common)


async def _build_pipeline(args, http: httpx.AsyncClient) -> IngestionPipeline:
    embedder = _build_embedder(args, http)
    if args.vector_backend == "memory":
        vstore = InMemoryVectorStore()
    else:
        dim = len((await embedder.embed(["dimension probe"]))[0])
        coll = args.collection or collection_name("ragstack", args.embedding_model, dim)
        vstore = QdrantVectorStore(url=args.qdrant_url, collection=coll,
                                   vector_size=dim, timeout=args.qdrant_timeout)
        await vstore.ensure_collection()
    if args.text_backend == "elasticsearch":
        tindex = ElasticsearchTextIndex(url=args.es_url, index=args.es_index or args.collection)
        await tindex.ensure_index()
    else:
        tindex = InMemoryTextIndex()
    chunker = make_chunker(method=args.chunk_method, chunk_size=args.chunk_size,
                           chunk_overlap=args.chunk_overlap)
    return IngestionPipeline(loader=JsonlLoader(), chunker=chunker, embedder=embedder,
                             vector_store=vstore, text_index=tindex)


async def amain(args) -> int:
    timeout = httpx.Timeout(300.0, connect=30.0)
    limits = httpx.Limits(max_connections=64, max_keepalive_connections=32)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as http:
        pipeline = await _build_pipeline(args, http)
        shard_id = args.shard_id or os.path.basename(args.shard)
        receipt = await run_shard(pipeline, args.shard, args.tenant, shard_id)
    receipt.write(args.out)
    print(f"[{shard_id}] status={receipt.status} docs={receipt.n_docs} "
          f"chunks={receipt.n_chunks} → {args.out}"
          + (f"  ERROR: {receipt.error}" if receipt.error else ""), flush=True)
    return 0 if receipt.status == COMPLETED else 1


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("shard", help="one JSONL shard file to ingest")
    p.add_argument("--out", default="receipt.json", help="output receipt path")
    p.add_argument("--shard-id", default=None, help="receipt shard id (default: basename)")
    p.add_argument("--tenant", default="public")
    p.add_argument("--chunk-method", default="fixed_token")
    p.add_argument("--chunk-size", type=int, default=256)
    p.add_argument("--chunk-overlap", type=int, default=32)
    p.add_argument("--vector-backend", choices=["qdrant", "memory"], default="qdrant")
    p.add_argument("--collection", default=None)
    p.add_argument("--qdrant-url", default="http://localhost:6333")
    p.add_argument("--qdrant-timeout", type=int, default=120)
    p.add_argument("--text-backend", choices=["elasticsearch", "memory"], default="elasticsearch")
    p.add_argument("--es-url", default="http://localhost:9200")
    p.add_argument("--es-index", default=None)
    p.add_argument("--embedding-api", choices=["sidecar", "openai"], default="openai")
    p.add_argument("--embedding-url", nargs="+", default=["http://localhost:9001"])
    p.add_argument("--embedding-model", default="Salesforce/SFR-Embedding-Mistral")
    p.add_argument("--embedding-api-key", default=None)
    p.add_argument("--embedding-max-concurrency", type=int, default=8)
    return p.parse_args(argv)


def main(argv=None) -> int:
    return asyncio.run(amain(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
