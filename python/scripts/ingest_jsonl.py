#!/usr/bin/env python
"""Bulk-ingest a pre-extracted JSONL corpus into Qdrant (+ optional Elasticsearch).

This is the operator tool for the large extraction dumps (hundreds of MB) that
are too big for the per-file size guard on the API ingest path. It *streams* the
file (constant memory), recovers scholarly metadata per record
(:mod:`ragstack.ingestion.enrich` — DOI / title / authors / year / doc_type /
citations), chunks the body text, embeds in batches, and upserts the points.

Each input line is one document::

    {"text": "...", "path": "/.../jvi.02415-06.pdf", "metadata": {title, authors, ...}}

Two outputs, independently selectable:

- **Index** (default): chunk + embed + upsert into Qdrant, and into Elasticsearch
  when ``--text-backend elasticsearch``. Each chunk carries the index-safe
  metadata subset (DOI/title/authors/year/doc_type/n_citations) for filtering.
- **Catalog** (``--catalog-out FILE``): one JSON line per ingested document with
  the *full* enriched metadata, including the extracted citation list — the
  document-level metadata collection. Combine with ``--no-index`` to collect
  metadata without paying for embeddings.

Resumable: documents are flushed at document boundaries and the last fully
indexed line number is checkpointed to ``<input>.ckpt`` (override with
``--checkpoint``); re-running skips everything up to the checkpoint. Chunk IDs
are deterministic, so a resumed run overwrites in place rather than duplicating.

Usage::

    cd python
    . /rag/bin/activate
    # full corpus into the smoke collection, tenant 'public', against the sidecar:
    python scripts/ingest_jsonl.py /rag/inputs/<file>.jsonl \\
        --tenant public \\
        --embedding-model BAAI/bge-base-en-v1.5 \\
        --catalog-out /rag/inputs/<file>.catalog.jsonl

    # metadata-only pass (no embeddings), e.g. to QA the DOI/title recovery:
    python scripts/ingest_jsonl.py /rag/inputs/<file>.jsonl --no-index \\
        --catalog-out /rag/inputs/<file>.catalog.jsonl

    # also populate the BM25 index, and cap the run for a smoke:
    python scripts/ingest_jsonl.py /rag/inputs/<file>.jsonl --tenant public \\
        --embedding-model BAAI/bge-base-en-v1.5 \\
        --text-backend elasticsearch --es-index ragstack_smoke \\
        --doc-types article --limit 200
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, TextIO

import httpx

from ragstack.embed_pool import make_pooled_embedder
from ragstack.embedders import make_embedder
from ragstack.ingestion.chunkers import RecursiveCharacterChunker
from ragstack.ingestion.enrich import EMPTY, enrich, index_metadata
from ragstack.ingestion.loaders import deterministic_doc_id
from ragstack.models import Chunk, Document
from ragstack.stores.elasticsearch import ElasticsearchTextIndex
from ragstack.stores.qdrant import QdrantVectorStore, collection_name


def _doc_id_key(record: dict[str, Any], text: str) -> str:
    rec_path = record.get("path", "") or ""
    return str(Path(rec_path).resolve()) if rec_path else text


def _read_checkpoint(path: Path) -> int:
    try:
        return int(path.read_text().strip())
    except (FileNotFoundError, ValueError):
        return 0


def _write_checkpoint(path: Path, line_no: int) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(str(line_no))
    tmp.replace(path)  # atomic so a crash mid-write can't corrupt the checkpoint


def _iter_records(fh: TextIO):
    """Yield ``(line_no, record)`` for each valid JSON line; skip blanks/garbage."""
    for line_no, raw in enumerate(fh, start=1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            yield line_no, json.loads(raw)
        except json.JSONDecodeError:
            print(f"  warn: skipping malformed line {line_no}", file=sys.stderr)


async def run(args: argparse.Namespace) -> None:
    src = args.input
    keep_types = set(args.doc_types) if args.doc_types else None
    chunker = RecursiveCharacterChunker(
        chunk_size=args.chunk_size, chunk_overlap=args.chunk_overlap
    )
    # Index and catalog-only passes keep *separate* default checkpoints: a cheap
    # --no-index catalog run must not advance the checkpoint the expensive
    # indexing run resumes from (which would make indexing skip every document).
    if args.checkpoint:
        ckpt_path = args.checkpoint
    else:
        ckpt_path = src.with_suffix(src.suffix + (".catalog.ckpt" if args.no_index else ".ckpt"))
    start_line = _read_checkpoint(ckpt_path) if args.resume else 0
    if start_line:
        print(f"resuming after line {start_line} (from {ckpt_path.name})", file=sys.stderr)

    # Append on resume so a resumed run doesn't truncate the catalog written so far.
    catalog_mode = "a" if (args.catalog_out and args.resume and start_line) else "w"
    catalog: TextIO | None = (
        open(args.catalog_out, catalog_mode, encoding="utf-8") if args.catalog_out else None
    )

    async with httpx.AsyncClient() as http:
        embedder = store = text_index = None
        if not args.no_index:
            urls = args.embedding_url
            common = {
                "api": args.embedding_api,
                "http": http,
                "model": args.embedding_model,
                "api_key": os.getenv("OPENAI_API_KEY"),
            }
            if len(urls) > 1:
                embedder = make_pooled_embedder(
                    base_urls=urls, max_concurrency=args.embedding_max_concurrency, **common
                )
                print(f"embedding fan-out across {len(urls)} endpoints", file=sys.stderr)
            else:
                embedder = make_embedder(base_url=urls[0], **common)

        stats = {"seen": 0, "skipped": 0, "docs": 0, "chunks": 0}
        buffer: list[Chunk] = []
        pending_line = 0  # highest line whose document is fully built into `buffer`

        async def flush(up_to_line: int) -> None:
            """Embed + upsert the buffered chunks, then checkpoint ``up_to_line``.

            Always advances the checkpoint to ``up_to_line`` — by construction
            that is the last line whose document is wholly contained in the
            buffer being flushed, so everything up to it is durably indexed.
            """
            nonlocal buffer
            if store is not None and buffer:
                vecs = await embedder.embed([c.content for c in buffer])
                for c, v in zip(buffer, vecs, strict=True):
                    c.embedding = v
                await store.upsert(buffer)
                if text_index is not None:
                    await text_index.index(buffer)
                stats["chunks"] += len(buffer)
                buffer = []
                print(f"  indexed {stats['chunks']} chunks / {stats['docs']} docs "
                      f"(line {up_to_line})", file=sys.stderr)
            _write_checkpoint(ckpt_path, up_to_line)

        with src.open(encoding="utf-8") as fh:
            for line_no, record in _iter_records(fh):
                if line_no <= start_line:
                    continue
                stats["seen"] += 1
                enriched = enrich(record)
                if enriched.doc_type == EMPTY or (
                    keep_types is not None and enriched.doc_type not in keep_types
                ):
                    stats["skipped"] += 1
                    pending_line = line_no
                    continue

                if catalog is not None:
                    catalog.write(json.dumps(enriched.model_dump(), ensure_ascii=False) + "\n")

                stats["docs"] += 1
                pending_line = line_no

                if args.no_index:
                    # Catalog-only pass: no chunking/embedding. Checkpoint
                    # periodically so a long metadata run is resumable too.
                    if stats["docs"] % 2000 == 0:
                        _write_checkpoint(ckpt_path, pending_line)
                    if args.limit and stats["docs"] >= args.limit:
                        break
                    continue

                text = record.get("text", "") or ""
                doc = Document(
                    id=deterministic_doc_id(_doc_id_key(record, text)),
                    content=text,
                    metadata=index_metadata(enriched),
                    source=record.get("path", "") or "",
                )
                chunks = chunker.chunk(doc)
                for c in chunks:
                    c.metadata["tenant_id"] = args.tenant
                buffer.extend(chunks)

                # First flush sizes the collection to the model's vector dim.
                if store is None and not args.no_index and len(buffer) >= args.batch_size:
                    sample = await embedder.embed([buffer[0].content])
                    dim = len(sample[0])
                    coll = args.collection or collection_name(
                        "ragstack", args.embedding_model, dim
                    )
                    store = QdrantVectorStore(url=args.qdrant_url, collection=coll, vector_size=dim)
                    await store.ensure_collection()
                    print(f"qdrant collection {coll!r} ready (dim={dim})", file=sys.stderr)
                    if args.text_backend == "elasticsearch":
                        text_index = ElasticsearchTextIndex(url=args.es_url, index=args.es_index)
                        await text_index.ensure_index()
                        print(f"elasticsearch index {args.es_index!r} ready", file=sys.stderr)

                if store is not None and len(buffer) >= args.batch_size:
                    await flush(pending_line)
                if args.limit and stats["docs"] >= args.limit:
                    break

            # Tail: a corpus smaller than one batch never triggered store creation.
            if buffer and store is None and not args.no_index:
                sample = await embedder.embed([buffer[0].content])
                dim = len(sample[0])
                coll = args.collection or collection_name("ragstack", args.embedding_model, dim)
                store = QdrantVectorStore(url=args.qdrant_url, collection=coll, vector_size=dim)
                await store.ensure_collection()
                if args.text_backend == "elasticsearch":
                    text_index = ElasticsearchTextIndex(url=args.es_url, index=args.es_index)
                    await text_index.ensure_index()
            await flush(pending_line)

            # Close the ES client's connection pool — it owns an aiohttp session
            # independent of `http`, which otherwise warns "Unclosed connector".
            if text_index is not None and hasattr(text_index, "close"):
                await text_index.close()

    if catalog is not None:
        catalog.close()
        print(f"catalog written to {args.catalog_out}", file=sys.stderr)
    print(f"done: {stats['docs']} docs indexed, {stats['skipped']} skipped, "
          f"{stats['chunks']} chunks (saw {stats['seen']} records)", file=sys.stderr)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("input", type=Path, help="JSONL file (one {text,path,metadata} per line)")
    p.add_argument("--tenant", default="public",
                   help="tenant_id stamped on every chunk (default: public = world-readable)")
    p.add_argument("--doc-types", nargs="+", default=None,
                   help="only ingest these doc_type classes (default: all non-empty). "
                        "e.g. --doc-types article supplement")
    p.add_argument("--limit", type=int, default=0, help="stop after N ingested docs (0 = all)")
    # outputs
    p.add_argument("--catalog-out", type=Path, default=None,
                   help="write full per-doc enriched metadata (incl. citations) here")
    p.add_argument("--no-index", action="store_true",
                   help="skip embedding/upsert; only build the catalog")
    # vector store
    p.add_argument("--qdrant-url", default="http://localhost:6333")
    p.add_argument("--collection", default=None,
                   help="Qdrant collection (default: auto-named from model+dim)")
    # text index (BM25)
    p.add_argument("--text-backend", choices=["none", "elasticsearch"], default="none")
    p.add_argument("--es-url", default="http://localhost:9200")
    p.add_argument("--es-index", default="ragstack")
    # embedding
    p.add_argument("--embedding-api", choices=["sidecar", "openai"], default="sidecar")
    p.add_argument("--embedding-url", nargs="+", default=["http://localhost:50053"],
                   help="embedding service URL(s); pass several to load-balance with failover")
    p.add_argument("--embedding-model", default=None,
                   help="model name (required for --embedding-api openai; used to name the collection)")
    p.add_argument("--embedding-max-concurrency", type=int, default=8)
    # chunking
    p.add_argument("--chunk-size", type=int, default=512)
    p.add_argument("--chunk-overlap", type=int, default=64)
    p.add_argument("--batch-size", type=int, default=128, help="chunks embedded/upserted per batch")
    # resume
    p.add_argument("--resume", action="store_true", help="skip lines up to the checkpoint")
    p.add_argument("--checkpoint", type=Path, default=None,
                   help="checkpoint file (default: <input>.ckpt)")
    args = p.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
