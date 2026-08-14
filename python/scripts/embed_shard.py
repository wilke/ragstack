#!/usr/bin/env python
"""Per-shard **embed** tool for the decoupled bulk pipeline (ADR-0001 offline
plane, #141). Embeds ONE shard (a JSONL file of document records) → a JSONL
**embedding file** (vectors + metadata + deterministic ids). The workflow
scatters this over the shards; the separate load stage
(``scripts/load_embeddings.py``) reads the embedding files and upserts to
Qdrant/ES with backpressure. Splitting embed from load keeps the GPU fleet from
ever being blocked by a busy/capped Qdrant — the point of #141.

**No store contact.** This tool reuses ``IngestionPipeline.embed_source`` (load →
chunk → embed → link), which never touches the vector/text stores, so it needs
**only the embedding fleet + tokenizer** — no Qdrant, no Elasticsearch. The
pipeline's ``vector_store``/``text_index`` are in-memory placeholders that are
never called. Stateless + idempotent: the engine owns scatter/retry/resume, and a
re-run overwrites the embedding file in place.

Usage::

    python scripts/embed_shard.py shard.s0.jsonl --tenant public \
        --embedding-api openai --embedding-model Salesforce/SFR-Embedding-Mistral \
        --embedding-url http://localhost:9001 --chunk-method fixed_token \
        --chunk-size 256 --chunk-overlap 32 --out shard.s0.emb.jsonl \
        --receipt receipt.json
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
from ragstack.ingestion.embed_shard import run_embed_shard
from ragstack.ingestion.loaders import JsonlLoader
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.ingestion.receipts import COMPLETED
from ragstack.stores.memory import InMemoryTextIndex, InMemoryVectorStore


def _build_chunker(args):
    if args.chunk_method.startswith("semantic"):
        raise SystemExit(
            f"--chunk-method {args.chunk_method} is not yet wired in embed_shard "
            "(it needs the breakpoint embed bridge); use fixed/fixed_token/sentence/"
            "words here."
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


def _build_pipeline(args, http: httpx.AsyncClient) -> IngestionPipeline:
    chunker = _build_chunker(args)  # fail fast on a bad chunk config, before any I/O
    # None -> scale with the fleet: 8 in-flight PER endpoint. A fixed total
    # silently caps a wider fleet at a narrower fleet's throughput.
    concurrency = args.embedding_max_concurrency or 8 * max(len(args.embedding_url), 1)
    embedder = make_embedder_auto(
        api=args.embedding_api, http=http, base_urls=args.embedding_url,
        model=args.embedding_model,
        api_key=args.embedding_api_key or os.getenv("OPENAI_API_KEY"),
        max_concurrency=concurrency,
    )
    # The loader is REAL (embed_source loads through it); only the stores are
    # placeholders — embed_source never touches them.
    loader = JsonlLoader(passthrough_keys=_passthrough_keys(args))
    return IngestionPipeline(loader=loader, chunker=chunker, embedder=embedder,
                             vector_store=InMemoryVectorStore(), text_index=InMemoryTextIndex(),
                             boilerplate_filter=filter_from_mode(
                                 args.boilerplate, args.boilerplate_config))


def _passthrough_keys(args) -> list[str]:
    """The record-metadata keys to carry through the loader verbatim.

    The fixed EnrichedDoc schema has no slot for corpus-specific keys (JATS/PMC:
    ``content_type``, ``pmcid``, ``licence``, ``section_title``, …), and the
    loader drops what it has no slot for. The allow-list is a property of the
    CORPUS, so it arrives as a flag per run rather than server config. getattr
    default: hand-built Namespaces (tests) predate the flag."""
    raw = getattr(args, "metadata_passthrough", "") or ""
    return [k.strip() for k in raw.split(",") if k.strip()]


def _derive_group_size(args) -> int:
    """Docs per embed() call, sized so the POOL has something to distribute (#334).

    One group is one ``embed()`` call, and the pool spreads a call across at most
    ``ceil(chunks / request_batch)`` endpoints — so the group, not the permit
    count, is the fan-out ceiling. The old fixed 64 docs yielded ~1.5
    sub-requests on the production corpus (~3 chunks/doc, 128-chunk batches) and
    a six-GPU fleet measurably ran on ~1.3 GPUs.

    128 docs per endpoint ≈ 384 chunks ≈ 3 sub-requests per endpoint at 3
    chunks/doc — fleet fully engaged with in-flight depth to spare, while a
    single-endpoint run keeps a memory profile close to the old behaviour.
    Corpora with fewer chunks/doc under-fan rather than over-allocate, which is
    the safe direction; --embed-group-size overrides either way.
    """
    return 128 * max(1, len(args.embedding_url))


async def amain(args) -> int:
    timeout = httpx.Timeout(300.0, connect=30.0)
    limits = httpx.Limits(max_connections=64, max_keepalive_connections=32)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as http:
        pipeline = _build_pipeline(args, http)
        shard_id = args.shard_id or os.path.basename(args.shard)
        group = args.embed_group_size or _derive_group_size(args)
        receipt = await run_embed_shard(pipeline, args.shard, args.tenant, shard_id,
                                        args.out, group_size=group)
    receipt.write(args.receipt)
    print(f"[{shard_id}] status={receipt.status} docs={receipt.n_docs} "
          f"chunks={receipt.n_chunks} → {args.out}"
          + (f"  ERROR: {receipt.error}" if receipt.error else ""), flush=True)
    return 0 if receipt.status == COMPLETED else 1


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("shard", help="one JSONL shard file to embed")
    p.add_argument("--out", default="embeddings.jsonl", help="output embedding file path")
    p.add_argument("--receipt", default="receipt.json", help="output receipt path")
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
    p.add_argument("--embedding-api", choices=["sidecar", "openai"], default="openai")
    p.add_argument("--embedding-url", nargs="+", default=["http://localhost:9001"])
    p.add_argument("--embedding-model", default="Salesforce/SFR-Embedding-Mistral")
    p.add_argument("--embedding-api-key", default=None)
    p.add_argument("--embedding-max-concurrency", type=int, default=None,
                   help="TOTAL in-flight embedding requests across all endpoints. "
                        "Default: 8 PER endpoint (8 x len(--embedding-url)). The old "
                        "fixed default of 8 total meant adding endpoints changed "
                        "nothing — measured on the OA pilot: 6 endpoints ran at the "
                        "2-endpoint rate (~55 chunks/s) because 8 in-flight spread "
                        "1.3 deep per endpoint.")
    p.add_argument("--embed-group-size", type=int, default=0,
                   help="documents per embed() call (0 = derive: 128 x number of "
                        "endpoints). This bounds BOTH peak memory and the pool's "
                        "fan-out: a group's chunks split into ceil(chunks/128) "
                        "sub-requests, and only that many endpoints can be busy at "
                        "once. The old fixed 64 ran a 6-GPU fleet on ~1.3 GPUs")
    p.add_argument("--metadata-passthrough", default="",
                   help="comma-separated record-metadata keys to carry onto chunks "
                        "verbatim (enriched fields always win on collision). For a "
                        "JATS/PMC shard: content_type,pmcid,pmid,journal,publisher,"
                        "licence,section_title,sha256,source_url,graphic")
    return p.parse_args(argv)


def main(argv=None) -> int:
    return asyncio.run(amain(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
