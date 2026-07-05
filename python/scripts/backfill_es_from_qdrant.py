"""Backfill the Elasticsearch (BM25) text index from chunk text already in Qdrant.

BM25 needs the chunk *text* + metadata, not the embedding vectors — so if a corpus
was ingested vector-only (e.g. a quick demo, or a run with ``--text-backend none``),
this reconstructs Chunk objects from the Qdrant payloads and indexes them via the
real ``ElasticsearchTextIndex`` (correct schema, no re-embedding). Idempotent.

Example:
    python scripts/backfill_es_from_qdrant.py \\
        --collection ragstack_salesforce_sfr_embedding_mistral_4096_928f8ebe

The ES index name defaults to the collection name, matching the serving API's
``_es_index_name()`` when ``qdrant_collection_explicit`` is pinned.
"""
from __future__ import annotations

import argparse
import asyncio

from qdrant_client import QdrantClient

from ragstack.models import Chunk
from ragstack.stores.elasticsearch import ElasticsearchTextIndex

# Qdrant payload keys that map onto Chunk fields; everything else is metadata.
_RESERVED = {"chunk_id", "doc_id", "content", "start_char", "end_char"}


def _int(value: object) -> int:
    try:
        return int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _chunk(payload: dict) -> Chunk:
    meta = {k: v for k, v in payload.items() if k not in _RESERVED}
    return Chunk(
        id=str(payload["chunk_id"]),
        doc_id=str(payload["doc_id"]),
        content=payload.get("content", "") or "",
        start_char=_int(payload.get("start_char")),
        end_char=_int(payload.get("end_char")),
        metadata=meta,
    )


async def run(args: argparse.Namespace) -> None:
    qc = QdrantClient(url=args.qdrant_url, timeout=args.qdrant_timeout)
    es = ElasticsearchTextIndex(
        url=args.es_url, index=args.es_index or args.collection, api_key=args.es_api_key or None
    )
    await es.ensure_index()

    total, offset, batch = 0, None, []
    while True:
        points, offset = qc.scroll(
            collection_name=args.collection,
            with_payload=True,
            with_vectors=False,
            limit=args.batch_size,
            offset=offset,
        )
        batch.extend(_chunk(p.payload) for p in points)
        if len(batch) >= args.batch_size:
            await es.index(batch)
            total += len(batch)
            print(f"  indexed {total}...", flush=True)
            batch = []
        if offset is None:
            break
    if batch:
        await es.index(batch)
        total += len(batch)
    await es.close()
    print(f"done: indexed {total} chunks into ES index '{args.es_index or args.collection}'")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--collection", required=True, help="Qdrant collection to read from")
    p.add_argument("--qdrant-url", default="http://localhost:6333")
    p.add_argument("--qdrant-timeout", type=float, default=120.0)
    p.add_argument("--es-url", default="http://localhost:9200")
    p.add_argument("--es-index", default="", help="ES index (default: same as --collection)")
    p.add_argument("--es-api-key", default="")
    p.add_argument("--batch-size", type=int, default=1000)
    asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    main()
