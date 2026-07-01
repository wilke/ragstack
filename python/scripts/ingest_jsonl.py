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
import statistics
import sys
import time
from pathlib import Path
from typing import Any, TextIO

import httpx

from ragstack.embed_pool import make_pooled_embedder
from ragstack.embedders import make_embedder
from ragstack.ingestion.chunkers import link_neighbors_by_document, make_chunker
from ragstack.ingestion.embed_bridge import SyncEmbedBridge
from ragstack.ingestion.enrich import EMPTY, enrich, index_metadata, resolve_profile
from ragstack.ingestion.loaders import deterministic_doc_id
from ragstack.ingestion.tokenization import make_token_counter, resolve_max_tokens
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


def _build_embedder(args: argparse.Namespace, http: httpx.AsyncClient):
    """The embedder for ``args`` against ``http``: a pooled fan-out when multiple
    --embedding-url are given, else a single-endpoint embedder. Shared by the main
    ingest path and the semantic chunker's SyncEmbedBridge factory so backend
    selection (api / model / api_key / pooling) lives in exactly one place."""
    common = {
        "api": args.embedding_api, "http": http,
        "model": args.embedding_model,
        "api_key": args.embedding_api_key or os.getenv("OPENAI_API_KEY"),
    }
    if len(args.embedding_url) > 1:
        return make_pooled_embedder(
            base_urls=args.embedding_url,
            max_concurrency=args.embedding_max_concurrency, **common,
        )
    return make_embedder(base_url=args.embedding_url[0], **common)


async def _embed_drop_bad(embedder: Any, chunks: list[Chunk]) -> list[Chunk]:
    """Embed each chunk's content, returning the chunks that embedded (with
    ``.embedding`` set).

    Backstop: when the embedder exposes ``embed_isolated`` (the single-endpoint
    ``BatchingEmbedder``), a 4xx / over-context-window chunk is bisected out and
    **dropped** with a warning rather than failing the whole batch — so one
    oversized chunk (e.g. an estimate-counter undercount) can't abort a long
    ingest. Infra failures (5xx / network) still raise and leave the batch for
    ``--resume``. The pooled fan-out has no ``embed_isolated`` and keeps the prior
    all-or-nothing behaviour."""
    texts = [c.content for c in chunks]
    if hasattr(embedder, "embed_isolated"):
        vecs, quarantined = await embedder.embed_isolated(texts)
        kept: list[Chunk] = []
        for c, v in zip(chunks, vecs, strict=True):
            if v is None:
                continue
            c.embedding = v
            kept.append(c)
        if quarantined:
            print(
                f"  warn: dropped {quarantined} unembeddable chunk(s) "
                "(over context window / bad input); continuing",
                file=sys.stderr,
            )
        return kept
    vecs = await embedder.embed(texts)
    for c, v in zip(chunks, vecs, strict=True):
        c.embedding = v
    return chunks


class DocMetricsWriter:
    """Streaming writer for the per-document metrics JSONL (``--doc-metrics-out``).

    One row per document::

        {doc_id, source_file, n_chunks, tokens_min, tokens_median, tokens_max,
         chunk_chars_median, skipped, error}

    Token stats are computed from the final (stored) chunks' text via the shared
    :class:`TokenCounter`; char stats from ``len(chunk.content)``. A skipped or
    fully-unembeddable document has ``n_chunks == 0`` and null token/char stats.

    Rows are appended as they finalize (skipped docs at the producer; indexed docs
    right after ``link_neighbors``). Writes go through one handle; the ingest is a
    single-threaded asyncio loop, but the caller still serialises worker writes
    under the shared lock so a row is never interleaved with another.
    """

    def __init__(self, path: Path, token_counter: Any, *, append: bool) -> None:
        self._fh = open(path, "a" if append else "w", encoding="utf-8")
        self._token_counter = token_counter

    def emit(
        self,
        doc_id: str,
        source_file: str,
        chunks: list[Chunk],
        *,
        skipped: bool = False,
        error: str | None = None,
    ) -> None:
        if chunks:
            toks = [self._token_counter.count(c.content) for c in chunks]
            chars = [len(c.content) for c in chunks]
            tokens_min: int | None = min(toks)
            tokens_median: float | None = statistics.median(toks)
            tokens_max: int | None = max(toks)
            chunk_chars_median: float | None = statistics.median(chars)
        else:
            tokens_min = tokens_median = tokens_max = chunk_chars_median = None
        row = {
            "doc_id": doc_id,
            "source_file": source_file,
            "n_chunks": len(chunks),
            "tokens_min": tokens_min,
            "tokens_median": tokens_median,
            "tokens_max": tokens_max,
            "chunk_chars_median": chunk_chars_median,
            "skipped": skipped,
            "error": error,
        }
        self._fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


def _write_run_metrics(
    path: Path,
    *,
    input_file: str,
    stats: dict[str, int],
    failed_seqs: list[int],
    wall_s: float,
) -> None:
    """Append one per-FILE summary row to ``--run-metrics-out`` at end-of-run::

        {file, docs_seen, docs_indexed, docs_skipped, chunks, failed_batches,
         failed_batch_seqs, wall_s, chunks_per_s}

    ``failed_*`` mirror the ingester's own failed-batch bookkeeping (the seqs whose
    batch errored and left the checkpoint stalled). ``docs_indexed`` counts docs
    that produced chunks and were handed to a batch (``stats['docs']``); note a
    doc in a failed batch still counts here — the per-doc metrics carry the
    per-document error detail.

    ALWAYS appends (unlike the per-doc writer): each row is one file's summary, so
    ingesting several files into one --run-metrics-out — as separate invocations —
    must accumulate rows, not truncate the prior file's row.
    """
    wall = round(wall_s, 3)
    chunks = stats["chunks"]
    row = {
        "file": input_file,
        "docs_seen": stats["seen"],
        "docs_indexed": stats["docs"],
        "docs_skipped": stats["skipped"],
        "chunks": chunks,
        "failed_batches": len(failed_seqs),
        "failed_batch_seqs": sorted(failed_seqs),
        "wall_s": wall,
        "chunks_per_s": round(chunks / wall, 2) if wall > 0 else None,
    }
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


async def run(args: argparse.Namespace) -> None:
    keep_types = set(args.doc_types) if args.doc_types else None
    # Publisher profile (DOI prefix / filename rule / front-matter set) for
    # enrichment; unknown names degrade to the ASM default in resolve_profile.
    profile = resolve_profile(args.publisher_profile)
    # Canonical (sorted) form of the active filter, persisted in the checkpoint so
    # a resume under a different filter is detected.
    current_doc_types = sorted(keep_types) if keep_types else None
    # Chunker selected by --chunk-method. The semantic chunker needs to embed
    # sentence buffers; it runs synchronously inside the (async) ingest, so hand it
    # a SyncEmbedBridge that builds its own embedder against the same --embedding-*
    # backend on a background loop. Built only for semantic; closed at run() exit.
    embed_bridge: SyncEmbedBridge | None = None
    if args.chunk_method == "semantic":
        embed_bridge = SyncEmbedBridge(lambda http: _build_embedder(args, http))
    # Token budget: size/cap chunks so none exceeds the embedder's context window.
    # The counter is the embedding model's tokenizer by default (--chunk-token-counter
    # hf); the budget is auto-detected from the endpoint's max_model_len unless
    # --chunk-max-tokens overrides it. Cheap/lazy, so build it for every method.
    embed_base_url = args.embedding_url[0] if args.embedding_url else None
    embed_api_key = args.embedding_api_key or os.getenv("OPENAI_API_KEY")
    # The 'hf' and 'endpoint' backends need a model name (the embedding model's
    # tokenizer). When none is configured (e.g. the BGE sidecar path doesn't pass
    # --embedding-model), fall back to the zero-dep estimator so sizing still works.
    token_backend = args.chunk_token_counter
    if args.chunk_method == "fixed_token":
        # The sliding token window needs the HF fast tokenizer's offset mapping
        # (only HFTokenCounter exposes it); an estimate/endpoint counter would make
        # the chunker degrade to a single whole-doc chunk. Force 'hf' + require a
        # model so --chunk-size/--chunk-overlap are honoured as real token windows.
        if not args.embedding_model:
            raise SystemExit(
                "--chunk-method fixed_token requires --embedding-model (its sliding "
                "token window is built from that model's HF tokenizer)."
            )
        if token_backend != "hf":
            print(
                f"[ingest] --chunk-method fixed_token needs the HF tokenizer; "
                f"overriding --chunk-token-counter {token_backend!r} -> 'hf'.",
                file=sys.stderr,
            )
            token_backend = "hf"
    if token_backend in ("hf", "endpoint") and not args.embedding_model:
        print(
            f"[ingest] --chunk-token-counter {token_backend} needs --embedding-model; "
            "falling back to 'estimate'.",
            file=sys.stderr,
        )
        token_backend = "estimate"
    token_counter = make_token_counter(
        token_backend,
        model=args.embedding_model,
        base_url=embed_base_url,
        api_key=embed_api_key,
    )
    max_tokens = resolve_max_tokens(
        args.chunk_max_tokens, base_url=embed_base_url, api_key=embed_api_key
    )
    chunker = make_chunker(
        args.chunk_method,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        embed_fn=embed_bridge,
        buffer_size=args.chunk_buffer_size,
        breakpoint_percentile_threshold=args.chunk_breakpoint_percentile,
        min_chunk_length=args.chunk_min_length,
        max_tokens=max_tokens,
        token_counter=token_counter,
    )
    ckpt_path, start_line = _open_checkpoint_paths(args, current_doc_types)
    catalog = _open_catalog(args, start_line)
    stats = {"seen": 0, "skipped": 0, "docs": 0, "chunks": 0}
    # Per-document metrics (optional). Append on resume so a resumed run adds to
    # the rows already written rather than truncating them.
    doc_metrics: DocMetricsWriter | None = None
    if args.doc_metrics_out:
        doc_metrics = DocMetricsWriter(
            args.doc_metrics_out, token_counter, append=bool(args.resume and start_line)
        )
    run_started = time.monotonic()

    if args.no_index:
        await _run_catalog_only(
            args, ckpt_path, start_line, catalog, stats, keep_types, current_doc_types, profile
        )
        if catalog is not None:
            catalog.close()
            print(f"catalog written to {args.catalog_out}", file=sys.stderr)
        print(f"done: {stats['docs']} docs cataloged, {stats['skipped']} skipped "
              f"(saw {stats['seen']} records)", file=sys.stderr)
        if embed_bridge is not None:
            embed_bridge.close()
        if doc_metrics is not None:
            doc_metrics.close()  # catalog-only pass emits no per-doc chunk metrics
        if args.run_metrics_out:
            _write_run_metrics(
                args.run_metrics_out, input_file=str(args.input), stats=stats,
                failed_seqs=[], wall_s=time.monotonic() - run_started,
            )
        return

    async with httpx.AsyncClient() as http:
        embedder = _build_embedder(args, http)
        if len(args.embedding_url) > 1:
            print(f"embedding fan-out across {len(args.embedding_url)} endpoints",
                  file=sys.stderr)

        # Size the collection to the model's vector dim via a one-text probe, so
        # the store exists before the concurrent workers start.
        dim = len((await embedder.embed(["dimension probe"]))[0])
        coll = args.collection or collection_name("ragstack", args.embedding_model, dim)
        store = QdrantVectorStore(
            url=args.qdrant_url, collection=coll, vector_size=dim, timeout=args.qdrant_timeout
        )
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
        # Bound concurrent prune-deletes separately from the embed fan-out so a wide
        # --concurrency doesn't issue a burst of heavy deletes at the store.
        delete_sem = asyncio.Semaphore(max(1, args.delete_concurrency))

        async def worker() -> None:
            nonlocal next_seq
            while True:
                item = await queue.get()
                try:
                    if item is None:
                        return
                    seq, end_line, chunks, cat_rows, doc_info = item
                    try:
                        # Embed; an over-context/bad chunk is dropped (warned) rather
                        # than failing the whole batch (see _embed_drop_bad). A fully
                        # quarantined batch yields []; nothing to store but the seq
                        # still completes so the checkpoint advances (no stall).
                        kept = await _embed_drop_bad(embedder, chunks)
                        # Stamp prev/next/chunk_index per document on the SURVIVING
                        # chunks (after the drop above) so neighbor links never
                        # dangle to a quarantined chunk, and a mixed-doc batch never
                        # cross-links one doc's tail to the next doc's head.
                        link_neighbors_by_document(kept)
                        if doc_metrics is not None:
                            # One per-doc row right after link_neighbors, over the
                            # SURVIVING chunks grouped by doc_id. A doc all of whose
                            # chunks were quarantined leaves no survivors -> emit a
                            # zero-chunk row with an error so it's not silently lost.
                            surviving: dict[str, list[Chunk]] = {}
                            for c in kept:
                                surviving.setdefault(c.doc_id, []).append(c)
                            async with lock:
                                for d_id, src in doc_info.items():
                                    doc_chunks = surviving.get(d_id, [])
                                    err = (
                                        None if doc_chunks
                                        else "all chunks unembeddable (quarantined)"
                                    )
                                    doc_metrics.emit(
                                        d_id, src, doc_chunks, skipped=False, error=err
                                    )
                        # Upsert FIRST. Deterministic chunk ids overwrite an
                        # unchanged doc's points in place, so plain upsert is correct
                        # for re-ingest and — critically — a failure here never
                        # deletes anything. (A prior delete-before-upsert ordering lost
                        # data when a filtered delete on a large collection timed out
                        # mid-batch: the delete landed, the upsert didn't.)
                        if kept:
                            await store.upsert(kept)
                            if text_index is not None:
                                await text_index.index(kept)
                        # Only an EDITED doc (shifted offsets → new chunk ids) leaves
                        # orphan points; prune them only when asked (--replace), and
                        # only AFTER the successful upsert above, by id (cost O(stale),
                        # not a collection-wide filtered delete). Bounded concurrency
                        # so a wide embed fan-out can't issue many heavy deletes at once.
                        if args.replace and kept:
                            by_doc: dict[str, set[str]] = {}
                            for c in kept:
                                by_doc.setdefault(c.doc_id, set()).add(c.id)
                            for doc_id, keep in by_doc.items():
                                async with delete_sem:
                                    await store.delete_except(doc_id, keep, tenant_id=args.tenant)
                                    if text_index is not None:
                                        await text_index.delete_except(
                                            doc_id, keep, tenant_id=args.tenant
                                        )
                    except Exception as e:
                        # Don't kill the worker (or deadlock the producer): leave
                        # this seq out of `completed` so the checkpoint stalls at
                        # the gap and --resume reprocesses from here. Record it so
                        # the run reports failure (non-zero exit) instead of "done".
                        failed.append(seq)
                        print(f"  batch seq={seq} failed: {type(e).__name__}: {e}; "
                              f"will reprocess on --resume", file=sys.stderr)
                        if doc_metrics is not None:
                            # Record every doc in the failed batch with the error;
                            # a later --resume re-runs the batch and appends fresh
                            # rows, so the failure is visible without losing the docs.
                            err = f"batch seq={seq} failed: {type(e).__name__}: {e}"
                            async with lock:
                                for d_id, src in doc_info.items():
                                    doc_metrics.emit(d_id, src, [], skipped=False, error=err)
                        continue
                    async with lock:
                        completed[seq] = (end_line, cat_rows)
                        stats["chunks"] += len(kept)
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
        buf_doc_info: dict[str, str] = {}  # doc_id -> source_file for docs in `buf`
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
                    if doc_metrics is not None:
                        # Skipped docs (filtered doc_type / empty) never reach a
                        # batch; record them here as zero-chunk skipped rows.
                        d_src = record.get("path", "") or ""
                        d_id = deterministic_doc_id(
                            _doc_id_key(record, record.get("text", "") or "")
                        )
                        doc_metrics.emit(d_id, d_src, [], skipped=True, error=None)
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
                buf_doc_info[doc.id] = doc.source
                buf_end_line = line_no
                stats["docs"] += 1
                if len(buf) >= args.batch_size:
                    await queue.put((seq, buf_end_line, buf, buf_catalog, buf_doc_info))
                    seq += 1
                    buf = []
                    buf_catalog = []
                    buf_doc_info = {}
                if args.limit and stats["docs"] >= args.limit:
                    break
        if buf:
            await queue.put((seq, buf_end_line, buf, buf_catalog, buf_doc_info))
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
    if embed_bridge is not None:
        embed_bridge.close()
    # Emit the per-file run summary + close the per-doc writer BEFORE the failure
    # exit below, so a partial (failed-batch) run still leaves its metrics behind.
    if doc_metrics is not None:
        doc_metrics.close()
    if args.run_metrics_out:
        _write_run_metrics(
            args.run_metrics_out, input_file=str(args.input), stats=stats,
            failed_seqs=failed, wall_s=time.monotonic() - run_started,
        )
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
    p.add_argument("--doc-metrics-out", type=Path, default=None,
                   help="write ONE JSONL row per DOCUMENT here: "
                        "{doc_id, source_file, n_chunks, tokens_min, tokens_median, "
                        "tokens_max, chunk_chars_median, skipped, error}. Token stats "
                        "come from the same TokenCounter used for token-cap sizing. "
                        "Appends on --resume. No behaviour change when unset.")
    p.add_argument("--run-metrics-out", type=Path, default=None,
                   help="append ONE per-FILE summary row at end-of-run here: "
                        "{file, docs_seen, docs_indexed, docs_skipped, chunks, "
                        "failed_batches, failed_batch_seqs, wall_s, chunks_per_s}. "
                        "No behaviour change when unset.")
    p.add_argument("--no-index", action="store_true",
                   help="skip embedding/upsert; only build the catalog")
    # vector store
    p.add_argument("--qdrant-url", default="http://localhost:6333")
    p.add_argument("--qdrant-timeout", type=int, default=120,
                   help="per-request Qdrant timeout in seconds (default: %(default)s)")
    p.add_argument("--collection", default=None,
                   help="Qdrant collection (default: auto-named from model+dim)")
    p.add_argument("--replace", action="store_true",
                   help="prune orphan chunks of EDITED docs after upsert (upsert-then-prune, by id). "
                        "Default off = upsert-only, which is correct for unchanged re-ingest and never "
                        "deletes data on failure.")
    p.add_argument("--delete-concurrency", type=int, default=4,
                   help="max concurrent prune-deletes under --replace (default: %(default)s)")
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
    p.add_argument("--embedding-api-key", default=None,
                   help="Bearer token sent as 'Authorization: Bearer <key>' to every "
                        "--embedding-url (keyless endpoints ignore it, so one key is safe "
                        "for a mixed pool). Falls back to $OPENAI_API_KEY.")
    # chunking
    p.add_argument("--chunk-method",
                   choices=["fixed", "fixed_token", "sentence", "words", "semantic"],
                   default="fixed",
                   help="chunking strategy (default: fixed). 'fixed_token' is a "
                        "sliding TOKEN window: --chunk-size/--chunk-overlap are "
                        "interpreted as TOKENS (of the --embedding-model tokenizer), "
                        "not chars. semantic embeds sentence buffers via the "
                        "configured --embedding-* backend.")
    p.add_argument("--chunk-size", type=int, default=512,
                   help="chunk size (chars for fixed/sentence/words; TOKENS for "
                        "fixed_token)")
    p.add_argument("--chunk-overlap", type=int, default=64,
                   help="chunk overlap (chars for fixed/sentence/words; TOKENS for "
                        "fixed_token)")
    # Semantic-only tunables (ignored by other methods).
    p.add_argument("--chunk-buffer-size", type=int, default=3,
                   help="semantic: sentences of context on each side of a buffer")
    p.add_argument("--chunk-breakpoint-percentile", type=float, default=80.0,
                   help="semantic: distance percentile above which a chunk boundary is placed")
    p.add_argument("--chunk-min-length", type=int, default=500,
                   help="semantic: merge chunks shorter than this many chars into a neighbor")
    # Token-based sizing: keep every chunk within the embedder's context window.
    p.add_argument("--chunk-max-tokens", type=int, default=None,
                   help="the embedding model's context window in tokens. The chunker "
                        "keeps a small reserve below it (for BOS/EOS specials) and caps "
                        "every chunk to that budget. Default None = auto-detect the "
                        "window from the endpoint's max_model_len (falls back to 4096 if "
                        "it can't be probed). A given value is treated as the window, so "
                        "the reserve is subtracted from it too.")
    p.add_argument("--chunk-token-counter", choices=["hf", "endpoint", "estimate"],
                   default="hf",
                   help="how to count tokens: 'hf' loads the embedding model's "
                        "AutoTokenizer (exact, default), 'endpoint' POSTs /tokenize to the "
                        "embedding URL, 'estimate' uses a chars/token heuristic (zero deps). "
                        "'hf'/'endpoint' need --embedding-model; without it sizing falls "
                        "back to 'estimate'.")
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
