#!/usr/bin/env python
"""Search a Qdrant collection populated by ingest_chunks.py.

Embeds the query via the configured embedding backend, then asks Qdrant
for the top-K most similar chunks. Optionally filters by payload fields.

Two embedding backends are supported via ``--embedding-api`` (must match
whatever was used at ingest time):

- ``sidecar`` (default) — RAGStack embedding sidecar at ``<url>/embed``.
- ``openai`` — any OpenAI-compatible endpoint at ``<url>/v1/embeddings``,
  e.g. vLLM. Requires ``--embedding-model``.

Usage:

    cd python
    python scripts/search.py "what is HNSW?"
    python scripts/search.py "deployment options" --top-k 3
    python scripts/search.py "rerank" --filter tags=architecture
    python scripts/search.py "qdrant" --json > hits.json

    # against vLLM:
    python scripts/search.py "what is HNSW" \\
        --embedding-api openai \\
        --embedding-url http://localhost:9998 \\
        --embedding-model Salesforce/SFR-Embedding-Mistral
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import httpx

from ragstack.embedders import make_embedder
from ragstack.stores.qdrant import QdrantVectorStore


def parse_filters(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for f in items:
        if "=" not in f:
            raise SystemExit(f"--filter expects key=value, got {f!r}")
        k, v = f.split("=", 1)
        out[k] = v
    return out


async def run(args: argparse.Namespace) -> None:
    filters = parse_filters(args.filter) or None

    async with httpx.AsyncClient() as http:
        embedder = make_embedder(
            api=args.embedding_api,
            http=http,
            base_url=args.embedding_url,
            model=args.embedding_model,
            api_key=os.getenv("OPENAI_API_KEY"),
        )
        qvec = (await embedder.embed([args.query]))[0]

    store = QdrantVectorStore(url=args.qdrant_url, collection=args.collection)
    results = await store.search(qvec, top_k=args.top_k, filters=filters)

    if args.json:
        print(json.dumps(
            [
                {
                    "score": r.score,
                    "chunk_id": r.chunk.id,
                    "doc_id": r.chunk.doc_id,
                    "content": r.chunk.content,
                    "metadata": r.chunk.metadata,
                }
                for r in results
            ],
            indent=2,
        ))
        return

    if not results:
        print("no matches", file=sys.stderr)
        return

    for i, r in enumerate(results, 1):
        snippet = " ".join(r.chunk.content.split())
        if len(snippet) > 240:
            snippet = snippet[:237] + "..."
        print(f"#{i}  score={r.score:.4f}  doc={r.chunk.doc_id}  id={r.chunk.id}")
        print(f"     {snippet}")
        if r.chunk.metadata:
            md = ", ".join(f"{k}={v}" for k, v in r.chunk.metadata.items())
            print(f"     [{md}]")
        print()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("query", help="natural-language search query")
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
        default="http://localhost:50053",
        help="base URL of the embedding service (default: %(default)s)",
    )
    p.add_argument(
        "--embedding-model",
        default=None,
        help="model name (required for --embedding-api openai)",
    )
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument(
        "--filter",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="restrict to chunks whose payload has KEY=VALUE (repeatable)",
    )
    p.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = p.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
