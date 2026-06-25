#!/usr/bin/env python
"""Ingest a JSON file of documents+chunks into Qdrant.

Reads a JSON file describing one or more documents, each with a list of
pre-extracted chunks. Sends chunk text to an embedding endpoint in batches,
upserts the resulting (vector, payload) points into Qdrant, and creates
the collection if missing.

Two embedding backends are supported via ``--embedding-api``:

- ``sidecar`` (default) — the RAGStack embedding sidecar at
  ``<url>/embed`` with ``{"texts": [...]}``.
- ``openai`` — any OpenAI-compatible endpoint at ``<url>/v1/embeddings``,
  including vLLM's pooling runner. Requires ``--embedding-model``.

Input format (JSON array, or a single object):

    [
      {
        "doc_id": "policy_2024_q1",          // required
        "source": "policies/2024_q1.pdf",    // optional, copied into payload
        "metadata": {                        // optional, merged into every
          "title": "Q1 Policy",              //   chunk's payload (this is
          "author": "Legal",                 //   the "same for every chunk"
          "tags": ["policy", "2024"]         //   doc-level metadata)
        },
        "chunks": [
          {
            "text": "...",                   // required
            "chunk_index": 0,                // optional
            "page": 1,                       // optional, any extra fields
            "start_char": 0,                 //   land in the chunk payload
            "end_char": 500                  //   alongside doc metadata
          },
          ...
        ]
      }
    ]

Per-chunk fields override doc-level fields if names collide. Chunk IDs
default to "<doc_id>:<chunk_index>"; pass an explicit ``id`` to override.

Usage:

    cd python
    pip install -e ".[vector]"
    python scripts/ingest_chunks.py path/to/chunks.json

    # against vLLM serving SFR-Embedding-Mistral on :9998:
    python scripts/ingest_chunks.py path/to/chunks.json \\
        --embedding-api openai \\
        --embedding-url http://localhost:9998 \\
        --embedding-model Salesforce/SFR-Embedding-Mistral

    # fan out the bulk ingest across several replicas (load-balanced + failover):
    python scripts/ingest_chunks.py path/to/chunks.json \\
        --embedding-api openai \\
        --embedding-url http://gpu0:9998 http://gpu1:9998 http://gpu2:9998 \\
        --embedding-model Salesforce/SFR-Embedding-Mistral
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

from ragstack.embed_pool import make_pooled_embedder
from ragstack.embedders import make_embedder
from ragstack.models import Chunk
from ragstack.stores.qdrant import QdrantVectorStore


def flatten(docs: list[dict[str, Any]]) -> list[Chunk]:
    """Expand the doc-level JSON into a flat list of Chunks, copying
    doc-level metadata + source onto every chunk's payload."""
    out: list[Chunk] = []
    for d in docs:
        doc_id = d["doc_id"]
        source = d.get("source", "")
        doc_md = dict(d.get("metadata", {}))
        if source:
            doc_md.setdefault("source", source)
        for i, raw in enumerate(d.get("chunks", [])):
            if "text" not in raw:
                raise ValueError(f"chunk in doc {doc_id!r} missing 'text'")
            text = raw["text"]
            idx = raw.get("chunk_index", i)
            chunk_id = raw.get("id") or f"{doc_id}:{idx}"
            md = {**doc_md, **{k: v for k, v in raw.items() if k != "text"}}
            out.append(
                Chunk(
                    id=chunk_id,
                    doc_id=doc_id,
                    content=text,
                    start_char=int(raw.get("start_char", 0)),
                    end_char=int(raw.get("end_char", 0)),
                    metadata=md,
                )
            )
    return out


async def run(args: argparse.Namespace) -> None:
    raw = json.loads(args.input.read_text())
    docs = raw if isinstance(raw, list) else [raw]
    chunks = flatten(docs)
    if not chunks:
        print("no chunks to ingest", file=sys.stderr)
        return
    print(
        f"loaded {len(chunks)} chunks from {len(docs)} doc(s)", file=sys.stderr
    )

    async with httpx.AsyncClient() as http:
        urls = args.embedding_url
        common = {
            "api": args.embedding_api,
            "http": http,
            "model": args.embedding_model,
            "api_key": os.getenv("OPENAI_API_KEY"),
        }
        if len(urls) > 1:
            embedder = make_pooled_embedder(
                base_urls=urls,
                max_concurrency=args.embedding_max_concurrency,
                **common,
            )
            print(
                f"embedding fan-out across {len(urls)} endpoints", file=sys.stderr
            )
        else:
            embedder = make_embedder(base_url=urls[0], **common)

        # Embed first batch so we can size the collection to the model's dim.
        head, tail = chunks[: args.batch_size], chunks[args.batch_size:]
        head_vecs = await embedder.embed([c.content for c in head])
        dim = len(head_vecs[0])

        store = QdrantVectorStore(
            url=args.qdrant_url, collection=args.collection, vector_size=dim
        )
        await store.ensure_collection()
        print(
            f"collection {args.collection!r} ready (vector_size={dim}, api={args.embedding_api})",
            file=sys.stderr,
        )

        for c, v in zip(head, head_vecs, strict=True):
            c.embedding = v
        await store.upsert(head)
        done = len(head)
        print(f"  upserted {done}/{len(chunks)}", file=sys.stderr)

        for i in range(0, len(tail), args.batch_size):
            batch = tail[i : i + args.batch_size]
            vecs = await embedder.embed([c.content for c in batch])
            for c, v in zip(batch, vecs, strict=True):
                c.embedding = v
            await store.upsert(batch)
            done += len(batch)
            print(f"  upserted {done}/{len(chunks)}", file=sys.stderr)

    print("done", file=sys.stderr)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("input", type=Path, help="JSON file with documents+chunks")
    p.add_argument("--qdrant-url", default="http://localhost:6333")
    p.add_argument("--collection", default="ragstack")
    p.add_argument(
        "--embedding-api",
        choices=["sidecar", "openai"],
        default="sidecar",
        help="embedding backend protocol (default: sidecar)",
    )
    p.add_argument(
        "--embedding-url",
        nargs="+",
        default=["http://localhost:50053"],
        help=(
            "base URL(s) of the embedding service (default: %(default)s); "
            "pass multiple to load-balance across them with failover"
        ),
    )
    p.add_argument(
        "--embedding-model",
        default=None,
        help="model name (required for --embedding-api openai)",
    )
    p.add_argument(
        "--embedding-max-concurrency",
        type=int,
        default=8,
        help="max in-flight embedding requests when fanning out across URLs",
    )
    p.add_argument("--batch-size", type=int, default=64)
    args = p.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
