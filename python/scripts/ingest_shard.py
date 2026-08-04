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

from ragstack.embed_pool import make_embedder_auto
from ragstack.ingestion.boilerplate import filter_from_mode
from ragstack.ingestion.chunker_config import build_chunker
from ragstack.ingestion.loaders import JsonlLoader
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.ingestion.receipts import COMPLETED
from ragstack.ingestion.shard import run_shard
from ragstack.stores.elasticsearch import ElasticsearchTextIndex
from ragstack.stores.memory import InMemoryTextIndex, InMemoryVectorStore
from ragstack.stores.qdrant import QdrantVectorStore


def _build_embedder(args, http: httpx.AsyncClient):
    return make_embedder_auto(
        api=args.embedding_api, http=http, base_urls=args.embedding_url,
        model=args.embedding_model,
        api_key=args.embedding_api_key or os.getenv("OPENAI_API_KEY"),
        max_concurrency=args.embedding_max_concurrency,
    )


def _build_chunker(args):
    """Chunker via the shared factory (fixed_token token-window included).

    Semantic methods need the breakpoint embed-bridge (not wired here), so reject
    them with a clear message rather than the raw make_chunker error — the bulk
    corpus uses fixed_token; semantic bulk stays on ingest_jsonl.py for now.
    """
    if args.chunk_method.startswith("semantic"):
        raise SystemExit(
            f"--chunk-method {args.chunk_method} is not yet wired in ingest_shard "
            "(it needs the breakpoint embed bridge); use fixed/fixed_token/sentence/"
            "words here, or ingest_jsonl.py for semantic."
        )
    chunker, _counter, _max_tokens = build_chunker(
        args.chunk_method,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        model=args.embedding_model,
        token_backend=args.chunk_token_counter,
        max_tokens=args.chunk_max_tokens,
        base_url=args.embedding_url[0] if args.embedding_url else None,
        api_key=args.embedding_api_key or os.getenv("OPENAI_API_KEY"),
    )
    return chunker


async def _build_pipeline(args, http: httpx.AsyncClient) -> IngestionPipeline:
    # Guard the half-ingest footgun: a qdrant vector store with an in-memory text
    # index (or vice versa) would silently write only one leg. Require both real
    # or both in-memory.
    if (args.vector_backend == "qdrant") != (args.text_backend == "elasticsearch"):
        raise SystemExit(
            "vector-backend and text-backend must be consistent (both durable or "
            f"both in-memory); got vector={args.vector_backend} text={args.text_backend}"
        )
    chunker = _build_chunker(args)  # fail fast on a bad chunk config, before any I/O
    embedder = _build_embedder(args, http)
    if args.vector_backend == "memory":
        vstore = InMemoryVectorStore()
        tindex = InMemoryTextIndex()
    else:
        if not args.collection:
            # Auto-name from (model, dim) only when explicitly allowed; otherwise a
            # typo'd/empty collection silently writes to an auto-named store.
            raise SystemExit("--collection is required for --vector-backend qdrant")
        es_index = args.es_index or args.collection
        dim = len((await embedder.embed(["dimension probe"]))[0])
        vstore = QdrantVectorStore(url=args.qdrant_url, collection=args.collection,
                                   vector_size=dim, timeout=args.qdrant_timeout)
        await vstore.ensure_collection()
        tindex = ElasticsearchTextIndex(url=args.es_url, index=es_index)
        await tindex.ensure_index()
    return IngestionPipeline(loader=JsonlLoader(), chunker=chunker, embedder=embedder,
                             vector_store=vstore, text_index=tindex,
                             boilerplate_filter=filter_from_mode(
                                 args.boilerplate, args.boilerplate_config))


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
    # Same three modes as ingest_jsonl.py; "flag" only stamps metadata, so the
    # offline plane matches the online API's default instead of silently
    # producing chunks the API path would have tagged.
    p.add_argument("--boilerplate", choices=["off", "flag", "drop"], default="flag",
                   help="chunk-level boilerplate handling (see ingest_jsonl.py)")
    p.add_argument("--boilerplate-config", default="",
                   help="JSON object overriding BoilerplateConfig thresholds")
    p.add_argument("--chunk-method", default="fixed_token")
    p.add_argument("--chunk-size", type=int, default=256)
    p.add_argument("--chunk-overlap", type=int, default=32)
    p.add_argument("--chunk-token-counter", choices=["hf", "endpoint", "estimate"],
                   default="hf", help="token counter backend (fixed_token forces hf)")
    p.add_argument("--chunk-max-tokens", type=int, default=None,
                   help="per-chunk token budget (model window); default auto-detect")
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
