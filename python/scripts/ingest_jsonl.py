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
from ragstack.ingestion.enrich import EMPTY, enrich, index_metadata, resolve_profile
from ragstack.ingestion.loaders import deterministic_doc_id
from ragstack.models import Chunk, Document
from ragstack.stores.elasticsearch import ElasticsearchTextIndex
from ragstack.stores.qdrant import QdrantVectorStore, collection_name


def _doc_id_key(record: dict[str, Any], text: str) -> str:
    rec_path = record.get("path", "") or ""
    return str(Path(rec_path).resolve()) if rec_path else text


def _read_checkpoint(path: Path) -> dict[str, Any]:
    """Persisted resume state: ``{"line": int, "doc_types": list[str] | None}``.

    Missing/corrupt reads as a zero checkpoint (fresh start). The legacy
    bare-integer format is still accepted (line only, no filter recorded).
    """
    try:
        raw = path.read_text().strip()
    except FileNotFoundError:
        return {"line": 0, "doc_types": None}
    try:
        data = json.loads(raw)
        return {"line": int(data.get("line", 0)), "doc_types": data.get("doc_types")}
    except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
        try:
            return {"line": int(raw), "doc_types": None}  # legacy bare-int checkpoint
        except ValueError:
            return {"line": 0, "doc_types": None}


def _write_checkpoint(path: Path, line_no: int, doc_types: list[str] | None) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    # Persist the active doc-type filter alongside the line so a resume with a
    # *different* filter is rejected rather than silently skipping lines the new
    # filter would now keep.
    tmp.write_text(json.dumps({"line": line_no, "doc_types": doc_types}))
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


def _open_checkpoint_paths(args: argparse.Namespace, current_doc_types: list[str] | None):
    """Resolve the checkpoint path and resume start-line.

    Index and catalog-only passes keep *separate* default checkpoints: a cheap
    ``--no-index`` catalog run must not advance the checkpoint the expensive
    indexing run resumes from (which would make indexing skip every document)."""
    src = args.input
    if args.checkpoint:
        ckpt_path = args.checkpoint
    else:
        ckpt_path = src.with_suffix(src.suffix + (".catalog.ckpt" if args.no_index else ".ckpt"))
    ckpt = _read_checkpoint(ckpt_path) if args.resume else {"line": 0, "doc_types": None}
    start_line = ckpt["line"]
    if start_line and ckpt["doc_types"] != current_doc_types:
        # Resume keys only on line number, so a looser filter would silently skip
        # every line the stricter run had filtered out. Fail closed instead.
        raise SystemExit(
            f"checkpoint {ckpt_path.name} was written with --doc-types "
            f"{ckpt['doc_types']}, but this run uses {current_doc_types}. Resuming "
            "would silently skip lines the new filter would keep. Re-run with the "
            "same --doc-types, a fresh --checkpoint, or without --resume."
        )
    if start_line:
        print(f"resuming after line {start_line} (from {ckpt_path.name})", file=sys.stderr)
    return ckpt_path, start_line


def _open_catalog(args: argparse.Namespace, start_line: int) -> TextIO | None:
    if not args.catalog_out:
        return None
    # Append on resume so a resumed run doesn't truncate the catalog written so far.
    mode = "a" if (args.resume and start_line) else "w"
    return open(args.catalog_out, mode, encoding="utf-8")


def _kept(enriched, keep_types) -> bool:
    return enriched.doc_type != EMPTY and (keep_types is None or enriched.doc_type in keep_types)


async def _run_catalog_only(
    args, ckpt_path, start_line, catalog, stats, keep_types, current_doc_types, profile
) -> None:
    """Metadata-only pass: enrich + write the catalog, no chunking/embedding."""
    with args.input.open(encoding="utf-8") as fh:
        pending = 0
        catalog_pending: list[str] = []  # rows not yet covered by the checkpoint

        def checkpoint(up_to_line: int) -> None:
            # Flush buffered catalog rows BEFORE advancing the checkpoint so the
            # catalog never gets ahead of the resume point; a crash-resume would
            # otherwise re-append them (deterministic per doc, but duplicated).
            if catalog is not None and catalog_pending:
                catalog.write("".join(catalog_pending))
                catalog.flush()
                catalog_pending.clear()
            _write_checkpoint(ckpt_path, up_to_line, current_doc_types)

        for line_no, record in _iter_records(fh):
            if line_no <= start_line:
                continue
            # Advance over every processed line, including skipped ones, so a
            # resume doesn't re-scan a trailing run of filtered records forever.
            pending = line_no
            stats["seen"] += 1
            enriched = enrich(record, profile=profile)
            if not _kept(enriched, keep_types):
                stats["skipped"] += 1
                continue
            if catalog is not None:
                catalog_pending.append(
                    json.dumps(enriched.model_dump(), ensure_ascii=False) + "\n"
                )
            stats["docs"] += 1
            pending = line_no
            if stats["docs"] % 2000 == 0:
                checkpoint(pending)
            if args.limit and stats["docs"] >= args.limit:
                break
        checkpoint(pending)


async def run(args: argparse.Namespace) -> None:
    keep_types = set(args.doc_types) if args.doc_types else None
    # Publisher profile (DOI prefix / filename rule / front-matter set) for
    # enrichment; unknown names degrade to the ASM default in resolve_profile.
    profile = resolve_profile(args.publisher_profile)
    # Canonical (sorted) form of the active filter, persisted in the checkpoint so
    # a resume under a different filter is detected.
    current_doc_types = sorted(keep_types) if keep_types else None
    chunker = RecursiveCharacterChunker(
        chunk_size=args.chunk_size, chunk_overlap=args.chunk_overlap
    )
    ckpt_path, start_line = _open_checkpoint_paths(args, current_doc_types)
    catalog = _open_catalog(args, start_line)
    stats = {"seen": 0, "skipped": 0, "docs": 0, "chunks": 0}

    if args.no_index:
        await _run_catalog_only(
            args, ckpt_path, start_line, catalog, stats, keep_types, current_doc_types, profile
        )
        if catalog is not None:
            catalog.close()
            print(f"catalog written to {args.catalog_out}", file=sys.stderr)
        print(f"done: {stats['docs']} docs cataloged, {stats['skipped']} skipped "
              f"(saw {stats['seen']} records)", file=sys.stderr)
        return

    async with httpx.AsyncClient() as http:
        urls = args.embedding_url
        common = {
            "api": args.embedding_api, "http": http,
            "model": args.embedding_model, "api_key": os.getenv("OPENAI_API_KEY"),
        }
        if len(urls) > 1:
            embedder = make_pooled_embedder(
                base_urls=urls, max_concurrency=args.embedding_max_concurrency, **common
            )
            print(f"embedding fan-out across {len(urls)} endpoints", file=sys.stderr)
        else:
            embedder = make_embedder(base_url=urls[0], **common)

        # Size the collection to the model's vector dim via a one-text probe, so
        # the store exists before the concurrent workers start.
        dim = len((await embedder.embed(["dimension probe"]))[0])
        coll = args.collection or collection_name("ragstack", args.embedding_model, dim)
        store = QdrantVectorStore(url=args.qdrant_url, collection=coll, vector_size=dim)
        await store.ensure_collection()
        print(f"qdrant collection {coll!r} ready (dim={dim})", file=sys.stderr)
        text_index = None
        if args.text_backend == "elasticsearch":
            text_index = ElasticsearchTextIndex(url=args.es_url, index=args.es_index)
            await text_index.ensure_index()
            print(f"elasticsearch index {args.es_index!r} ready", file=sys.stderr)

        # Concurrent pipeline: a producer streams fixed-size chunk batches onto a
        # bounded queue (constant memory on huge inputs); `concurrency` workers
        # embed+upsert in parallel, fanning out across the embedder pool. Each
        # batch carries a monotonic seq + the last record line it covers; the
        # checkpoint only advances over the contiguous *completed* prefix, so a
        # crash never records a line whose batch (or an earlier one) hadn't
        # finished — resume re-does from the first unfinished batch (idempotent,
        # deterministic chunk IDs overwrite in place).
        concurrency = max(1, args.concurrency)
        queue: asyncio.Queue = asyncio.Queue(maxsize=concurrency * 2)
        # seq -> (last record line, buffered catalog rows) for completed batches.
        completed: dict[int, tuple[int, list[str]]] = {}
        failed: list[int] = []  # seqs whose batch errored (checkpoint stalls at the gap)
        next_seq = 0
        lock = asyncio.Lock()

        async def worker() -> None:
            nonlocal next_seq
            while True:
                item = await queue.get()
                try:
                    if item is None:
                        return
                    seq, end_line, chunks, cat_rows = item
                    try:
                        vecs = await embedder.embed([c.content for c in chunks])
                        for c, v in zip(chunks, vecs, strict=True):
                            c.embedding = v
                        # Replace-on-reingest: delete each document's prior chunks
                        # before writing its new ones, so an *edited* doc (shifted
                        # offsets → new chunk ids) doesn't leave orphans (mirrors
                        # IngestionPipeline.ingest). After a successful embed so a
                        # transient embed failure can't destroy good data first. The
                        # producer only flushes on a document boundary, so a doc's
                        # chunks live entirely in one batch / one worker — deleting
                        # this batch's distinct doc ids removes each exactly once
                        # with no cross-worker race.
                        for doc_id in dict.fromkeys(c.doc_id for c in chunks):
                            await store.delete(doc_id, tenant_id=args.tenant)
                            if text_index is not None:
                                await text_index.delete(doc_id, tenant_id=args.tenant)
                        await store.upsert(chunks)
                        if text_index is not None:
                            await text_index.index(chunks)
                    except Exception as e:
                        # Don't kill the worker (or deadlock the producer): leave
                        # this seq out of `completed` so the checkpoint stalls at
                        # the gap and --resume reprocesses from here. Record it so
                        # the run reports failure (non-zero exit) instead of "done".
                        failed.append(seq)
                        print(f"  batch seq={seq} failed: {type(e).__name__}: {e}; "
                              f"will reprocess on --resume", file=sys.stderr)
                        continue
                    async with lock:
                        completed[seq] = (end_line, cat_rows)
                        stats["chunks"] += len(chunks)
                        advanced = None
                        while next_seq in completed:
                            end_line_n, rows_n = completed.pop(next_seq)
                            # Write this batch's catalog rows in seq order, in
                            # lockstep with the checkpoint, so the catalog never
                            # gets ahead of the resume point. Rows for batches that
                            # finished out of order wait in `completed` until the
                            # contiguous prefix reaches them.
                            if catalog is not None and rows_n:
                                catalog.write("".join(rows_n))
                            advanced = end_line_n
                            next_seq += 1
                        if advanced is not None:
                            if catalog is not None:
                                catalog.flush()
                            _write_checkpoint(ckpt_path, advanced, current_doc_types)
                            print(f"  indexed {stats['chunks']} chunks / {stats['docs']} docs "
                                  f"(line {advanced})", file=sys.stderr)
                finally:
                    queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(concurrency)]

        seq = 0
        buf: list[Chunk] = []
        buf_catalog: list[str] = []  # catalog rows for the docs in `buf`
        buf_end_line = 0
        last_line = 0  # highest processed line (kept or skipped), for the final checkpoint
        with args.input.open(encoding="utf-8") as fh:
            for line_no, record in _iter_records(fh):
                if line_no <= start_line:
                    continue
                last_line = line_no
                stats["seen"] += 1
                enriched = enrich(record, profile=profile)
                if not _kept(enriched, keep_types):
                    stats["skipped"] += 1
                    continue
                # Buffer the catalog row with its batch; the worker writes it in
                # lockstep with the checkpoint when this batch's seq is folded.
                if catalog is not None:
                    buf_catalog.append(
                        json.dumps(enriched.model_dump(), ensure_ascii=False) + "\n"
                    )
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
                buf.extend(chunks)
                buf_end_line = line_no
                stats["docs"] += 1
                if len(buf) >= args.batch_size:
                    await queue.put((seq, buf_end_line, buf, buf_catalog))
                    seq += 1
                    buf = []
                    buf_catalog = []
                if args.limit and stats["docs"] >= args.limit:
                    break
        if buf:
            await queue.put((seq, buf_end_line, buf, buf_catalog))
            seq += 1
        for _ in workers:  # one sentinel per worker
            await queue.put(None)
        await asyncio.gather(*workers)

        # All batches done with no gap (next_seq caught up to the batch count):
        # advance the checkpoint over any trailing skipped lines after the last
        # kept doc, so a resume of a finished run doesn't re-scan them. A gap
        # (failed batch) leaves next_seq < seq, so the checkpoint correctly stalls.
        if next_seq == seq:
            _write_checkpoint(ckpt_path, last_line, current_doc_types)

        if text_index is not None and hasattr(text_index, "close"):
            await text_index.close()

    if catalog is not None:
        catalog.close()
        print(f"catalog written to {args.catalog_out}", file=sys.stderr)
    if failed:
        # Don't report success when batches errored: the checkpoint stalled at the
        # first gap, so the run is partial. Exit non-zero so the operator notices.
        print(f"FAILED: {len(failed)} batch(es) errored (seq {sorted(failed)}); "
              f"checkpoint stalled at the first gap — re-run with --resume to retry.",
              file=sys.stderr)
        raise SystemExit(1)
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
    p.add_argument("--publisher-profile", default="asm",
                   help="enrichment profile (DOI prefix / filename rule / front-matter set); "
                        "unknown names fall back to the default. See enrich.PROFILES")
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
    p.add_argument("--concurrency", type=int, default=1,
                   help="in-flight batches embedded+upserted in parallel; set >1 to fan out "
                        "across multiple --embedding-url endpoints (default: 1 = serial)")
    # resume
    p.add_argument("--resume", action="store_true", help="skip lines up to the checkpoint")
    p.add_argument("--checkpoint", type=Path, default=None,
                   help="checkpoint file (default: <input>.ckpt)")
    args = p.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
